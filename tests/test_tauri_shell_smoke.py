import os
import subprocess
import unittest
from pathlib import Path


class TauriShellSmokeTests(unittest.TestCase):
    def test_tauri_shell_smoke(self):
        if not os.environ.get("RUN_TAURI_SMOKE"):
            self.skipTest("set RUN_TAURI_SMOKE=1 to run native Tauri smoke")
        script = Path(__file__).with_name("tauri_shell_smoke.test.js")
        subprocess.run(["node", "--test", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()
