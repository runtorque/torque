"""PtySupervisorClient tests against an in-process supervisor.

These tests stand in for the daemon-restart survival scenario: a
client connects to a live supervisor, creates a session, then
drops the connection. A fresh client connects to the same
supervisor and resumes — the session is still live, output streams
resume, write/close still work.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from torque.pty_supervisor import PtySupervisor
from torque.pty_supervisor_client import (
    PtySupervisorClient,
    SupervisorProtocolError,
    SupervisorUnavailable,
)


class _ServerHarness:
    def __init__(self):
        self.tmp: tempfile.TemporaryDirectory | None = None
        self.socket_path: Path | None = None
        self.supervisor: PtySupervisor | None = None
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.tmp.name) / "sup.sock"
        self.supervisor = PtySupervisor()
        self.server = await asyncio.start_unix_server(
            self.supervisor.handle_client, path=str(self.socket_path))

    async def stop(self) -> None:
        if self.supervisor:
            await self.supervisor.shutdown()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.tmp:
            self.tmp.cleanup()


class PtySupervisorClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = _ServerHarness()
        await self.h.start()

    async def asyncTearDown(self):
        await self.h.stop()

    async def test_connect_returns_pong_with_correct_version(self):
        client = PtySupervisorClient(self.h.socket_path)
        pong = await client.connect()
        self.assertEqual(pong.get("type"), "pong")
        self.assertGreaterEqual(pong.get("version"), 1)
        await client.aclose()

    async def test_create_write_list_close_cycle(self):
        client = PtySupervisorClient(self.h.socket_path)
        await client.connect()
        try:
            ok = await client.create(
                session_id="c1",
                cell_id="cell-c1",
                shell_argv=["/bin/sh", "-c", "sleep 3"],
                env={},
                cwd="",
                cols=80,
                rows=24,
            )
            self.assertEqual(ok.get("type"), "ok")
            self.assertEqual(ok.get("op"), "create")

            sessions = await client.list_sessions()
            ids = [s["session_id"] for s in sessions]
            self.assertIn("c1", ids)

            write_ok = await client.write_input("c1", b"ignored\n")
            self.assertEqual(write_ok.get("type"), "ok")

            resize_ok = await client.resize("c1", 100, 40)
            self.assertEqual(resize_ok.get("type"), "ok")

            close_ok = await client.close_session("c1")
            self.assertEqual(close_ok.get("type"), "ok")
        finally:
            await client.aclose()

    async def test_subscribe_streams_output_to_callback(self):
        client = PtySupervisorClient(self.h.socket_path)
        await client.connect()
        try:
            got = asyncio.Event()
            received: list[dict] = []

            async def on_output(msg):
                received.append(msg)
                # Look for our sentinel in either snapshot or output.
                import base64
                data = base64.b64decode(msg.get("data") or "")
                if b"hello-cli" in data:
                    got.set()

            await client.create(
                session_id="c2",
                cell_id="cell-c2",
                shell_argv=[
                    "/bin/sh", "-c", "printf 'hello-cli\\n'; sleep 2"],
                env={},
                cwd="",
                cols=80,
                rows=24,
            )
            sub_ok = await client.subscribe(
                "c2", on_output=on_output, on_exit=None)
            self.assertEqual(sub_ok.get("type"), "ok")
            await asyncio.wait_for(got.wait(), timeout=3.0)
            # At least one stream frame (snapshot or output) arrived.
            self.assertTrue(received)
            await client.close_session("c2")
        finally:
            await client.aclose()

    async def test_reconnect_resubscribes_and_resumes_session(self):
        """Drop the client; reconnect; session still live + output streams.

        This is the daemon-restart survival scenario. The supervisor
        is unchanged across the drop; the client has to re-subscribe
        after losing its connection.
        """
        client = PtySupervisorClient(
            self.h.socket_path, reconnect_delay=0.05)
        await client.connect()
        try:
            # Session emits output periodically for ~5s.
            await client.create(
                session_id="c3",
                cell_id="cell-c3",
                shell_argv=[
                    "/bin/sh", "-c",
                    "i=0; while [ $i -lt 50 ]; do "
                    "printf 'tick-%d\\n' $i; i=$((i+1)); sleep 0.1; "
                    "done",
                ],
                env={},
                cwd="",
                cols=80,
                rows=24,
            )
            received_before: list[bytes] = []
            received_after: list[bytes] = []
            first_phase = asyncio.Event()
            second_phase = asyncio.Event()

            phase = {"value": "before"}

            async def on_output(msg):
                import base64
                data = base64.b64decode(msg.get("data") or "")
                if phase["value"] == "before":
                    received_before.append(data)
                    if b"tick-" in data:
                        first_phase.set()
                else:
                    received_after.append(data)
                    if b"tick-" in data:
                        second_phase.set()

            await client.subscribe("c3", on_output=on_output)
            await asyncio.wait_for(first_phase.wait(), timeout=3.0)

            # Simulate daemon restart by forcibly dropping the socket.
            # The supervisor stays up; the client's reconnect_loop will
            # re-open and resubscribe automatically.
            phase["value"] = "after"
            # Close the writer to force EOF on the reader side.
            assert client._writer is not None
            client._writer.close()

            # Auto-reconnect + resubscribe should start streaming again.
            await asyncio.wait_for(second_phase.wait(), timeout=4.0)
            self.assertTrue(received_before)
            self.assertTrue(received_after)

            # Confirm via list that the session is still the same pid.
            sessions = await client.list_sessions()
            matches = [s for s in sessions if s["session_id"] == "c3"]
            self.assertEqual(len(matches), 1)
            self.assertTrue(matches[0]["alive"])

            await client.close_session("c3")
        finally:
            await client.aclose()

    async def test_reconnect_invokes_on_reconnect_callback(self):
        client = PtySupervisorClient(
            self.h.socket_path, reconnect_delay=0.05)
        seen: list[dict] = []

        async def on_reconnect(info):
            seen.append(info)

        client.on_reconnect = on_reconnect
        await client.connect()
        try:
            assert client._writer is not None
            client._writer.close()
            # Give reconnect loop time to fire.
            deadline = asyncio.get_running_loop().time() + 2.0
            while (asyncio.get_running_loop().time() < deadline
                   and not seen):
                await asyncio.sleep(0.05)
            self.assertTrue(seen, "on_reconnect was never called")
            info = seen[0]
            self.assertIn("previous_pid", info)
            self.assertIn("supervisor_pid", info)
            self.assertIn("fresh", info)
            # Same supervisor -> not fresh.
            self.assertFalse(info["fresh"])
        finally:
            await client.aclose()

    async def test_call_after_close_raises_unavailable(self):
        client = PtySupervisorClient(self.h.socket_path)
        await client.connect()
        await client.aclose()
        with self.assertRaises(SupervisorUnavailable):
            await client.call("ping")

    async def test_unknown_session_surfaces_as_error_frame(self):
        client = PtySupervisorClient(self.h.socket_path)
        await client.connect()
        try:
            result = await client.write_input("does-not-exist", b"")
            self.assertEqual(result.get("type"), "error")
            self.assertEqual(result.get("code"), "unknown_session")
        finally:
            await client.aclose()


class VersionMismatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_mismatch_raises(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            sock_path = Path(tmp.name) / "fake.sock"

            async def fake_handler(reader, writer):
                from torque.pty_supervisor import read_frame, write_frame
                msg = await read_frame(reader)
                if msg and msg.get("op") == "ping":
                    await write_frame(writer, {
                        "type": "pong",
                        "version": 999,
                        "pid": 0,
                    })
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

            server = await asyncio.start_unix_server(
                fake_handler, path=str(sock_path))
            try:
                client = PtySupervisorClient(sock_path)
                with self.assertRaises(SupervisorProtocolError):
                    await client.connect()
                await client.aclose()
            finally:
                server.close()
                await server.wait_closed()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
