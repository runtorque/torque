"""In-process tests for the PTY supervisor.

These tests spin up a ``PtySupervisor`` backed by ``asyncio.start_unix_server``
on a temp path. Each test acts as the daemon-side client — opening a unix
socket connection and exchanging framed JSON directly. No subprocess /
``spawn_detached`` invocation: those paths are covered separately.
"""

import asyncio
import base64
import contextlib
import os
import tempfile
import unittest
from pathlib import Path

from torque import pty_supervisor
from torque.pty_supervisor import PtySupervisor, read_frame, write_frame


async def _open_client(path: Path):
    return await asyncio.open_unix_connection(str(path))


class _Harness:
    """Starts an in-process supervisor server on a temp socket."""

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


class SupervisorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = _Harness()
        await self.h.start()

    async def asyncTearDown(self):
        await self.h.stop()

    async def test_ping_returns_pong_with_version(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {"op": "ping"})
            msg = await read_frame(reader)
            self.assertEqual(msg.get("type"), "pong")
            self.assertEqual(
                msg.get("version"), pty_supervisor.PROTOCOL_VERSION)
            self.assertEqual(msg.get("pid"), os.getpid())
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_unknown_op_yields_protocol_error(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {"op": "not_a_real_op"})
            msg = await read_frame(reader)
            self.assertEqual(msg.get("type"), "error")
            self.assertEqual(msg.get("code"), "protocol_error")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_create_list_and_close(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {
                "op": "create",
                "session_id": "s1",
                "cell_id": "c1",
                "shell_argv": ["/bin/sh", "-c", "sleep 5"],
                "env": {},
                "cwd": "",
                "cols": 80,
                "rows": 24,
            })
            ok = await read_frame(reader)
            self.assertEqual(ok.get("type"), "ok")
            self.assertEqual(ok.get("op"), "create")
            self.assertEqual(ok.get("session_id"), "s1")
            self.assertIsInstance(ok.get("pid"), int)

            await write_frame(writer, {"op": "list"})
            lst = await read_frame(reader)
            self.assertEqual(lst.get("type"), "list")
            ids = [s["session_id"] for s in lst["sessions"]]
            self.assertIn("s1", ids)

            await write_frame(writer, {"op": "close", "session_id": "s1"})
            closed = await read_frame(reader)
            self.assertEqual(closed.get("type"), "ok")
            self.assertEqual(closed.get("op"), "close")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_subscribe_streams_output(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {
                "op": "create",
                "session_id": "s2",
                "cell_id": "c2",
                "shell_argv": [
                    "/bin/sh", "-c", "printf 'hello-sup\\n'; sleep 3"],
                "env": {},
                "cwd": "",
                "cols": 80,
                "rows": 24,
            })
            await read_frame(reader)  # create ok

            await write_frame(writer, {
                "op": "subscribe",
                "session_id": "s2",
            })
            sub_ok = await read_frame(reader)
            self.assertEqual(sub_ok.get("type"), "ok")
            self.assertEqual(sub_ok.get("op"), "subscribe")
            # Snapshot + subsequent output arrive as data frames.
            seen = False
            deadline = asyncio.get_running_loop().time() + 4.0
            while asyncio.get_running_loop().time() < deadline and not seen:
                msg = await asyncio.wait_for(read_frame(reader), timeout=2.0)
                if not msg:
                    break
                if msg.get("type") in ("snapshot", "output"):
                    data = base64.b64decode(msg.get("data") or "")
                    if b"hello-sup" in data:
                        seen = True
                        break
                elif msg.get("type") == "exit":
                    break
            self.assertTrue(seen, "did not receive hello-sup output")

            await write_frame(writer, {"op": "close", "session_id": "s2"})
            await read_frame(reader)  # close ok (may race with exit/output)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_write_then_read_echo(self):
        """`cat` round-trip — confirms stdin write reaches the subprocess."""
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {
                "op": "create",
                "session_id": "s3",
                "cell_id": "c3",
                "shell_argv": ["/bin/sh", "-c", "cat"],
                "env": {},
                "cwd": "",
                "cols": 80,
                "rows": 24,
            })
            await read_frame(reader)  # create ok

            await write_frame(writer, {
                "op": "subscribe",
                "session_id": "s3",
            })
            await read_frame(reader)  # subscribe ok
            await read_frame(reader)  # snapshot

            await write_frame(writer, {
                "op": "write",
                "session_id": "s3",
                "data": base64.b64encode(b"echo-me\n").decode("ascii"),
            })
            # Interleave: write ok + echoed output. Drain until we see the echo.
            got_echo = False
            deadline = asyncio.get_running_loop().time() + 3.0
            while asyncio.get_running_loop().time() < deadline and not got_echo:
                msg = await asyncio.wait_for(read_frame(reader), timeout=2.0)
                if not msg:
                    break
                if msg.get("type") == "output":
                    data = base64.b64decode(msg.get("data") or "")
                    if b"echo-me" in data:
                        got_echo = True
                        break
            self.assertTrue(got_echo)

            await write_frame(writer, {"op": "close", "session_id": "s3"})
            with contextlib.suppress(Exception):
                await asyncio.wait_for(read_frame(reader), timeout=2.0)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_session_survives_client_disconnect_and_is_resumable(self):
        """Drop and re-establish a client; the session keeps running."""
        reader, writer = await _open_client(self.h.socket_path)
        await write_frame(writer, {
            "op": "create",
            "session_id": "s4",
            "cell_id": "c4",
            "shell_argv": ["/bin/sh", "-c", "sleep 4; printf done\\n"],
            "env": {},
            "cwd": "",
            "cols": 80,
            "rows": 24,
        })
        await read_frame(reader)  # ok
        pid_before = self.h.supervisor.sessions["s4"].pid
        writer.close()
        await writer.wait_closed()

        # Second "daemon image" connection.
        reader2, writer2 = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer2, {"op": "list"})
            lst = await read_frame(reader2)
            matches = [s for s in lst["sessions"] if s["session_id"] == "s4"]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["pid"], pid_before)
            self.assertTrue(matches[0]["alive"])

            await write_frame(writer2, {
                "op": "close", "session_id": "s4"})
            await read_frame(reader2)
        finally:
            writer2.close()
            await writer2.wait_closed()

    async def test_unknown_session_returns_error(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {
                "op": "write",
                "session_id": "nope",
                "data": "",
            })
            msg = await read_frame(reader)
            self.assertEqual(msg.get("type"), "error")
            self.assertEqual(msg.get("code"), "unknown_session")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_duplicate_create_rejected(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            create_msg = {
                "op": "create",
                "session_id": "dup",
                "cell_id": "c",
                "shell_argv": ["/bin/sh", "-c", "sleep 2"],
                "env": {},
                "cwd": "",
                "cols": 80,
                "rows": 24,
            }
            await write_frame(writer, create_msg)
            first = await read_frame(reader)
            self.assertEqual(first.get("type"), "ok")

            await write_frame(writer, create_msg)
            # Subsequent frames may include interleaved output from s1;
            # walk until we see the expected error.
            saw_error = False
            deadline = asyncio.get_running_loop().time() + 2.0
            while (asyncio.get_running_loop().time() < deadline
                   and not saw_error):
                m = await asyncio.wait_for(read_frame(reader), timeout=1.0)
                if m is None:
                    break
                if (m.get("type") == "error"
                        and m.get("code") == "session_exists"):
                    saw_error = True
            self.assertTrue(saw_error)

            await write_frame(writer, {"op": "close", "session_id": "dup"})
        finally:
            writer.close()
            await writer.wait_closed()


class EnsureRunningTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_socket_returns_none_when_path_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing.sock"
            pong = pty_supervisor._ping_socket(missing, timeout=0.1)
            self.assertIsNone(pong)

    async def test_ping_socket_returns_pong_against_live_server(self):
        h = _Harness()
        await h.start()
        try:
            # _ping_socket is blocking — run in a thread so it doesn't
            # starve the event loop.
            pong = await asyncio.to_thread(
                pty_supervisor._ping_socket, h.socket_path, 1.0)
            self.assertIsNotNone(pong)
            self.assertEqual(pong.get("type"), "pong")
            self.assertEqual(
                pong.get("version"), pty_supervisor.PROTOCOL_VERSION)
        finally:
            await h.stop()

    async def test_cleanup_stale_removes_socket_and_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            sock = data_dir / pty_supervisor.DEFAULT_SOCKET_NAME
            pid = data_dir / pty_supervisor.DEFAULT_PID_FILE_NAME
            sock.write_bytes(b"stale")
            pid.write_text("12345")
            pty_supervisor._cleanup_stale({"socket": sock, "pid": pid})
            self.assertFalse(sock.exists())
            self.assertFalse(pid.exists())

    async def test_pid_alive_for_current_process(self):
        self.assertTrue(pty_supervisor._pid_alive(os.getpid()))

    async def test_pid_alive_false_for_nonexistent(self):
        # A pid we can be reasonably sure doesn't exist.
        self.assertFalse(pty_supervisor._pid_alive(2_000_000_000))


if __name__ == "__main__":
    unittest.main()
