import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
