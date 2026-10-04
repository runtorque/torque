import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CommunityPackagingExclusionTests(unittest.TestCase):
    def test_install_copies_all_assets_when_default_shell_is_dash(self):
        make = shutil.which("make")
        dash = shutil.which("dash")
        if not make or not dash:
            self.skipTest("make and dash are required")
        with tempfile.TemporaryDirectory(prefix="torque-install-shell-") as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / "Makefile", root / "Makefile")
            (root / "default-shell.mk").write_text(f"SHELL := {dash}\n")
            files = [
                "torque.py", "torque_desktop.py", "webview.html",
                "torque/server.py", "torque/config with spaces.yaml",
                "static/js/app.js", "static/asset with spaces.txt",
                "ui/dist/index.html", "ui/dist/.vite/manifest.json",
                "ui/dist/assets/chunk with spaces.js",
            ]
            for name in files:
                source = root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"fixture: {name}\n")
            artifact = root / "installed app"
            proc = subprocess.run(
                [make, "-f", "default-shell.mk", "-f", "Makefile",
                 "-o", "ui-build", "install-standalone",
                 f"PRIMARY_APP_DIR={artifact}"],
                cwd=root, text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            for name in files:
                with self.subTest(name=name):
                    installed = artifact / name
                    self.assertTrue(installed.is_file(), proc.stdout + proc.stderr)
                    self.assertEqual(installed.read_bytes(), (root / name).read_bytes())

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
