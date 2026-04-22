import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MakefileInstallTests(unittest.TestCase):
    def test_install_dry_run_keeps_orjson_dependency_command_valid(self):
        if not shutil.which("make"):
            self.skipTest("make is not available")

        proc = subprocess.run(
            [
                "make",
                "-n",
                "install",
                "ITERM2_PROJECT=/tmp/loom-iterm2-test",
                "SCRIPT_DIR=/tmp/loom-iterm2-test/loom",
                "PROJECT_PYTHON=/usr/bin/printf",
                "GLOBAL_ENV=",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn(
            "aiohttp jinja2 pyyaml orjson 2>/dev/null || true",
            proc.stdout,
        )
        self.assertNotIn("/dev/nulle", proc.stdout)
        self.assertNotIn("\\true", proc.stdout)


if __name__ == "__main__":
    unittest.main()
