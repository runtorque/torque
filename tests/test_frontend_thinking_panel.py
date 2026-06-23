import subprocess
import unittest
from pathlib import Path


class FrontendThinkingPanelTests(unittest.TestCase):
    def test_frontend_thinking_panel_regressions(self):
        script = Path(__file__).with_name("frontend_thinking_panel.test.js")
        result = subprocess.run(
            ["node", "--test", str(script)],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                "frontend Thinking panel tests failed\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
