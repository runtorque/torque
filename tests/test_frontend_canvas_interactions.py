import subprocess
import unittest
from pathlib import Path


class FrontendCanvasInteractionsTests(unittest.TestCase):
    def test_frontend_canvas_interactions_regressions(self):
        root = Path(__file__).resolve().parents[1]
        script = Path(__file__).with_name("frontend_canvas_interactions.test.js")
        proc = subprocess.run(
            ["node", "--test", str(script)],
            cwd=root,
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            self.fail(
                "frontend canvas interactions tests failed\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
