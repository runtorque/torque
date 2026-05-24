import subprocess
import unittest
from pathlib import Path

try:
    from ee_gate import ee_skip_reason, ee_tests_enabled
except ModuleNotFoundError:  # Support `python -m unittest tests.<module>`.
    from tests.ee_gate import ee_skip_reason, ee_tests_enabled

_EE_REQUIRED_PATHS = ["ee/frontend/remote/js"]
_EE_TESTS_ENABLED = ee_tests_enabled(_EE_REQUIRED_PATHS)
_EE_SKIP_REASON = ee_skip_reason(_EE_REQUIRED_PATHS)


@unittest.skipUnless(_EE_TESTS_ENABLED, _EE_SKIP_REASON)
class FrontendRemoteTests(unittest.TestCase):
    """Run the remote web UI (ee/frontend/remote) Node regression suite.

    Each frontend_remote_*.test.js exercises one module of the remote channel
    client (envelope, config, store, dedupe, reconnect/re-auth state machine,
    render). The bundle is enterprise-only and lives under ee/frontend/remote/.
    """

    def test_remote_frontend_node_suite(self):
        root = Path(__file__).resolve().parents[1]
        scripts = [
            str(Path(__file__).with_name(name))
            for name in (
                "frontend_remote_envelope.test.js",
                "frontend_remote_config.test.js",
                "frontend_remote_store.test.js",
                "frontend_remote_dedupe.test.js",
                "frontend_remote_state_machine.test.js",
                "frontend_remote_render.test.js",
                "frontend_remote_e2e.test.js",
            )
        ]
        proc = subprocess.run(
            ["node", "--test", *scripts],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.fail(
                "remote frontend Node tests failed\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
