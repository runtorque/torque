import subprocess
import unittest
from pathlib import Path


class FrontendRenderSurfaceFocusTests(unittest.TestCase):
    def test_restore_surface_state_focus_uses_prevent_scroll(self):
        root = Path(__file__).resolve().parents[1]
        script = Path(__file__).with_name("frontend_render_surface_focus.test.js")
        proc = subprocess.run(
            ["node", "--test", str(script)],
            cwd=root,
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            self.fail(
                "frontend render surface-focus tests failed\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
