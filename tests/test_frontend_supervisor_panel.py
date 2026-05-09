import subprocess
import unittest
from pathlib import Path


class FrontendSupervisorPanelTests(unittest.TestCase):
    def test_frontend_supervisor_panel(self):
        root = Path(__file__).resolve().parents[1]
        script = Path(__file__).with_name("frontend_supervisor_panel.test.js")
        proc = subprocess.run(
            ["node", "--test", str(script)],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.fail(
                "frontend supervisor panel tests failed\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
