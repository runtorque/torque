import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CommunityPackagingExclusionTests(unittest.TestCase):
    def test_standalone_community_artifact_excludes_ee_tree(self):
        if not shutil.which("make"):
            self.skipTest("make is not available")
        script = ROOT / "scripts" / "assert_community_package_excludes_ee.py"
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(
                "community packaging guard failed\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
        self.assertIn("excludes ee/", proc.stdout)


if __name__ == "__main__":
    unittest.main()
