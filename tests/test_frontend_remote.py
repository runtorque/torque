import subprocess
import unittest
from pathlib import Path


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
