import os
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class RuntimePayloadExtensionTests(unittest.TestCase):
    def test_runtime_payload_includes_daemon_metadata(self):
        install_aiohttp_stub()
        from torque import server

        class FakeState:
            def get_default_command(self):
                return "codex"

        bridge = SimpleNamespace(
            capabilities=SimpleNamespace(supports_embedded_terminal=True)
        )

        payload = server._runtime_payload(bridge=bridge, state=FakeState())

        version_file = Path(__file__).resolve().parents[1] / "VERSION"
        expected_version = version_file.read_text(encoding="utf-8").strip()

        self.assertEqual(payload["version"], expected_version)
        self.assertIsInstance(payload["version"], str)
        self.assertEqual(payload["pid"], os.getpid())
        self.assertIsInstance(payload["pid"], int)
        self.assertIsInstance(payload["started_at"], float)
        self.assertGreater(payload["started_at"], 0)
        self.assertLessEqual(payload["started_at"], time.time() + 1)
        self.assertEqual(payload["log_path"], str(server.DATA_DIR / "torque.log"))
        self.assertIsInstance(payload["log_path"], str)
        self.assertEqual(
            payload["supervisor_log_path"],
            str(server.DATA_DIR / "pty_supervisor.log"),
        )
        self.assertIn("supervisor", payload)
        self.assertEqual(payload["supervisor"]["state"], "unavailable")

    def test_log_tail_parser_paginates_recent_entries(self):
        install_aiohttp_stub()
        from torque import server

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "torque.log"
            log_path.write_text(
                "12:00:00 INFO    booted\n"
                "12:00:01 WARNING slow path\n"
                "12:00:02 ERROR   failed thing\n",
                encoding="utf-8",
            )

            payload = server._tail_log_entries(log_path, since=0, limit=2, tail_bytes=4096)

        self.assertEqual([line["level"] for line in payload["lines"]], ["WARNING", "ERROR"])
        self.assertEqual(payload["lines"][-1]["message"], "failed thing")
        self.assertGreater(payload["cursor"], 0)
        raw = (
            "12:00:00 INFO    booted\n"
            "12:00:01 WARNING slow path\n"
            "12:00:02 ERROR   failed thing\n"
        )
        self.assertEqual(payload["size"], len(raw.encode("utf-8")))

    def test_log_target_paths_select_daemon_or_supervisor(self):
        install_aiohttp_stub()
        from torque import server

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with mock.patch.object(server, "DATA_DIR", data_dir):
                target, path = server._log_path_for_target("daemon")
                self.assertEqual(target, "daemon")
                self.assertEqual(path, data_dir / "torque.log")
                target, path = server._log_path_for_target("supervisor")
                self.assertEqual(target, "supervisor")
                self.assertEqual(path, data_dir / "pty_supervisor.log")
                target, path = server._log_path_for_target("unknown")
                self.assertEqual(target, "daemon")
                self.assertEqual(path, data_dir / "torque.log")


if __name__ == "__main__":
    unittest.main()
