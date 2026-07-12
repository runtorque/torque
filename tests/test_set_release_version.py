import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReleaseVersionStampTests(unittest.TestCase):
    def test_release_stamper_updates_readme_with_runtime_sources(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for relative in (
                "VERSION",
                "README.md",
                "torque/__init__.py",
                "src-tauri/Cargo.toml",
                "src-tauri/tauri.conf.json",
                "src-tauri/Cargo.lock",
            ):
                source = root / relative
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            stamp = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "set_release_version.py"),
                    "9.8.7",
                    "--repo-root",
                    str(target),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(stamp.returncode, 0, stamp.stderr)

            check = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "set_release_version.py"),
                    "9.8.7",
                    "--repo-root",
                    str(target),
                    "--check",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn(
                "shields.io/badge/version-9.8.7-green.svg",
                (target / "README.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
