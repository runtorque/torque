"""Profile-scoped main Torque daemon ownership regression tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from torque.daemon_owner import (
    OWNER_FD_ENV,
    OWNER_FILE_NAME,
    DaemonAlreadyOwnedError,
    ProcessInspection,
    ProfileDaemonOwner,
    UnsafeDaemonOwnershipError,
    inspect_process,
)
from torque.events import AgentEvent, EventIngestDrainer


class ProfileDaemonOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "profile"
        self.source_path = Path(__file__)

    def _acquire(self, *, data_dir=None, profile="qa", port=18932, **kwargs):
        return ProfileDaemonOwner.acquire(
            data_dir=data_dir or self.data_dir,
            profile=profile,
            port=port,
            source_path=self.source_path,
            **kwargs,
        )

    def _write_previous(self, *, pid: int, identity: dict | None = None) -> dict:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        owner = {
            "schema_version": 1,
            "daemon_id": "previous-daemon",
            "pid": pid,
            "port": 18932,
            "profile": "qa",
            "data_dir": str(self.data_dir.resolve()),
            "acquired_at": 1000,
            "executable": "/usr/bin/python3",
            "source_path": "/opt/torque/torque.py",
            "process_identity": identity
            or {
                "kind": "test",
                "start_token": "boot-a:100",
                "executable": "/usr/bin/python3",
                "command": "python3 torque.py",
            },
        }
        (self.data_dir / OWNER_FILE_NAME).write_text(
            json.dumps(owner), encoding="utf-8"
        )
        return owner

    def test_owner_metadata_is_actionable_and_orderly_release_is_reusable(self):
        owner = self._acquire(port=19001)
        metadata = json.loads(
            (self.data_dir / OWNER_FILE_NAME).read_text(encoding="utf-8")
        )

        self.assertEqual(metadata["pid"], os.getpid())
        self.assertEqual(metadata["port"], 19001)
        self.assertEqual(metadata["profile"], "qa")
        self.assertEqual(metadata["data_dir"], str(self.data_dir.resolve()))
        self.assertTrue(metadata["daemon_id"])
        self.assertTrue(metadata["process_identity"]["start_token"])
        self.assertTrue(metadata["executable"])
        self.assertEqual(metadata["source_path"], str(self.source_path.resolve()))

        owner.release()
        self.assertEqual(
            (self.data_dir / OWNER_FILE_NAME).read_text(encoding="utf-8"), ""
        )
        replacement = self._acquire(port=19002)
        self.addCleanup(replacement.release)
        self.assertNotEqual(replacement.daemon_id, metadata["daemon_id"])

    def test_same_profile_different_port_is_rejected_with_owner_and_advice(self):
        owner = self._acquire(port=18932)
        self.addCleanup(owner.release)

        with self.assertRaises(DaemonAlreadyOwnedError) as raised:
            self._acquire(port=18933)

        message = str(raised.exception)
        self.assertIn(f"pid={os.getpid()}", message)
        self.assertIn("port=18932", message)
        self.assertIn(owner.daemon_id, message)
        self.assertIn("Attach to the existing Torque daemon", message)
        self.assertIn("distinct TORQUE_PROFILE/TORQUE_DATA_DIR", message)

    def test_separate_backend_process_owns_profile_across_ports_and_crash_recovers(self):
        child_code = """
import json
import sys
from torque.daemon_owner import ProfileDaemonOwner

owner = ProfileDaemonOwner.acquire(
    data_dir=sys.argv[1],
    profile="qa",
    port=18932,
    source_path=sys.executable,
)
print(json.dumps(owner.metadata), flush=True)
sys.stdin.read(1)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.data_dir)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        def cleanup_child():
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(cleanup_child)
        owner_line = child.stdout.readline()
        if not owner_line:
            self.fail(f"owner subprocess failed: {child.stderr.read()}")
        child_metadata = json.loads(owner_line)

        with self.assertRaises(DaemonAlreadyOwnedError) as raised:
            self._acquire(port=18933)
        self.assertIn(f"pid={child.pid}", str(raised.exception))
        self.assertIn("port=18932", str(raised.exception))
        self.assertEqual(child_metadata["pid"], child.pid)

        # Abrupt exit leaves metadata but releases the kernel lock. Recovery is
        # allowed only after the recorded PID is observably dead.
        child.kill()
        child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
        recovered = self._acquire(port=18933)
        self.addCleanup(recovered.release)
        self.assertEqual(recovered.metadata["port"], 18933)
        self.assertNotEqual(
            recovered.daemon_id, child_metadata["daemon_id"]
        )

    def test_distinct_resolved_data_dirs_can_run_concurrently(self):
        first = self._acquire(
            data_dir=Path(self.tmp.name) / "profile-a",
            profile="a",
            port=18932,
        )
        second = self._acquire(
            data_dir=Path(self.tmp.name) / "profile-b",
            profile="b",
            port=18933,
        )
        self.addCleanup(first.release)
        self.addCleanup(second.release)

        self.assertNotEqual(first.path, second.path)
        self.assertNotEqual(first.daemon_id, second.daemon_id)

    def test_verified_dead_owner_is_recovered(self):
        dead_pid = 999_999_999
        previous = self._write_previous(pid=dead_pid)

        def inspect(pid):
            if pid == dead_pid:
                return ProcessInspection("dead", {}, "test process is dead")
            return inspect_process(pid)

        owner = self._acquire(port=18944, process_inspector=inspect)
        self.addCleanup(owner.release)

        self.assertNotEqual(owner.daemon_id, previous["daemon_id"])
        self.assertEqual(owner.metadata["port"], 18944)

    def test_in_place_exec_adopts_the_same_kernel_lock(self):
        original = self._acquire(port=18932)
        original_id = original.daemon_id
        original.prepare_exec_handoff()
        self.assertEqual(
            os.environ[OWNER_FD_ENV], str(original._handle.fileno())
        )

        # Simulate the old Python image disappearing while a duplicate of its
        # same locked open-file description survives exec.
        inherited_fd = os.dup(original._handle.fileno())
        os.set_inheritable(inherited_fd, True)
        os.environ[OWNER_FD_ENV] = str(inherited_fd)
        original._handle.close()
        original._handle = None
        original._released = True

        replacement = self._acquire(port=18932)
        self.addCleanup(replacement.release)
        self.assertNotEqual(replacement.daemon_id, original_id)
        self.assertNotIn(OWNER_FD_ENV, os.environ)
        self.assertFalse(os.get_inheritable(replacement._handle.fileno()))

        with self.assertRaises(DaemonAlreadyOwnedError):
            self._acquire(port=18933)

    def test_unlocked_metadata_for_live_matching_owner_is_not_reclaimed(self):
        previous_pid = 42420
        previous = self._write_previous(pid=previous_pid)

        def inspect(pid):
            self.assertEqual(pid, previous_pid)
            return ProcessInspection(
                "live", dict(previous["process_identity"]), ""
            )

        with self.assertRaises(UnsafeDaemonOwnershipError) as raised:
            self._acquire(process_inspector=inspect)

        self.assertIn("not demonstrably stale", str(raised.exception))
        self.assertIn("matches the recorded owner", str(raised.exception))

    def test_pid_reuse_identity_mismatch_is_not_reclaimed(self):
        previous_pid = 42421
        self._write_previous(pid=previous_pid)

        def inspect(pid):
            self.assertEqual(pid, previous_pid)
            return ProcessInspection(
                "live",
                {
                    "kind": "test",
                    "start_token": "boot-b:900",
                    "executable": "/usr/bin/other",
                    "command": "unrelated process",
                },
                "",
            )

        with self.assertRaises(UnsafeDaemonOwnershipError) as raised:
            self._acquire(process_inspector=inspect)

        message = str(raised.exception)
        self.assertIn("possible PID reuse", message)
        self.assertIn("Refusing automatic reclamation", message)

    def test_ambiguous_liveness_is_not_treated_as_dead(self):
        previous_pid = 42422
        self._write_previous(pid=previous_pid)

        with self.assertRaises(UnsafeDaemonOwnershipError) as raised:
            self._acquire(
                process_inspector=lambda _pid: ProcessInspection(
                    "unknown", {}, "permission denied"
                )
            )

        self.assertIn("permission denied", str(raised.exception))

    def test_corrupt_nonempty_owner_metadata_requires_manual_recovery(self):
        self.data_dir.mkdir(parents=True)
        (self.data_dir / OWNER_FILE_NAME).write_text("{not-json", encoding="utf-8")

        with self.assertRaises(UnsafeDaemonOwnershipError) as raised:
            self._acquire()

        self.assertIn("cannot be verified safely", str(raised.exception))
        self.assertIn("removing the owner file manually", str(raised.exception))


class ServerOwnershipBoundaryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        from torque import server

        cls.server = server

    async def test_server_refuses_second_owner_before_backend_db_or_drainer_setup(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            owner = ProfileDaemonOwner.acquire(
                data_dir=tmp,
                profile="incident",
                port=18932,
                source_path=__file__,
            )
            self.addCleanup(owner.release)
            db_factory = mock.Mock()

            with mock.patch.object(server, "DATA_DIR", Path(tmp)), mock.patch.object(
                server, "WS_PORT", 18933
            ), mock.patch.dict(
                os.environ, {"TORQUE_PROFILE": "incident"}
            ), mock.patch.object(
                server, "TorqueDB", db_factory
            ):
                with self.assertRaises(DaemonAlreadyOwnedError) as raised:
                    await server.main()

            self.assertIn("port=18932", str(raised.exception))
            db_factory.assert_not_called()

    async def test_exact_incident_topology_cannot_steal_and_ack_stop(self):
        """A second unknown-cell consumer never reaches the global ack cursor."""

        with tempfile.TemporaryDirectory() as tmp:
            owner = ProfileDaemonOwner.acquire(
                data_dir=tmp,
                profile="default",
                port=18932,
                source_path=__file__,
            )
            self.addCleanup(owner.release)

            stop_event = AgentEvent(
                cell_id="c3",
                timestamp=123.0,
                event_type="session_end",
                data={"reason": "Stop", "session_id": "exact-session"},
            )

            class IngestClient:
                def __init__(self):
                    self.drain_calls = 0
                    self.acks = []

                async def drain(self, **_kwargs):
                    self.drain_calls += 1
                    return {
                        "type": "drain_result",
                        "ack_cursor": 950,
                        "events": [{"cursor": 951, "event": {"raw": {}}}],
                    }

                async def ack(self, *, up_to):
                    self.acks.append(up_to)
                    return {"type": "ack_result", "ack_cursor": up_to}

            owner_client = IngestClient()
            would_be_thief_client = IngestClient()
            bus = SimpleNamespace(emit=mock.AsyncMock())
            owner_state = SimpleNamespace(agents={"c3": object()})

            # Port 18933 models the source daemon from the incident. It has no
            # c3 state and would globally ack cursor 951 if it could start.
            with self.assertRaises(DaemonAlreadyOwnedError):
                ProfileDaemonOwner.acquire(
                    data_dir=tmp,
                    profile="default",
                    port=18933,
                    source_path=__file__,
                )
            self.assertEqual(would_be_thief_client.drain_calls, 0)
            self.assertEqual(would_be_thief_client.acks, [])

            drainer = EventIngestDrainer(
                owner_client,
                bus,
                owner_state,
                daemon_identity=owner.label,
            )
            with mock.patch(
                "torque.events.agent_event_from_ingest_envelope",
                return_value=stop_event,
            ):
                processed = await drainer.drain_once()

            self.assertEqual(processed, 1)
            bus.emit.assert_awaited_once_with(stop_event)
            self.assertEqual(owner_client.acks, [951])


class DaemonIdentityDiagnosticTests(unittest.TestCase):
    def test_unknown_cell_diagnostic_names_the_consuming_daemon(self):
        from torque.events import agent_event_from_ingest_envelope

        state = SimpleNamespace(
            agents={},
            iter_active_agents=lambda: iter(()),
        )
        with self.assertLogs("torque", level="DEBUG") as captured:
            result = agent_event_from_ingest_envelope(
                state,
                {
                    "headers": {"X-Torque-Cell-Id": "c3"},
                    "raw": {"cwd": "/tmp/c3"},
                },
                daemon_identity="daemon-a pid=11 port=18932",
            )

        self.assertIsNone(result)
        self.assertIn(
            "daemon=daemon-a pid=11 port=18932",
            "\n".join(captured.output),
        )


if __name__ == "__main__":
    unittest.main()
