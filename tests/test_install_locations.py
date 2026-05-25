import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from torque import install_locations


class InstallLocationTests(unittest.TestCase):
    def test_primary_paths_live_under_torque_home(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            self.assertEqual(
                install_locations.primary_app_dir(home_path),
                home_path / ".torque" / "app",
            )
            self.assertEqual(
                install_locations.primary_runtime_venv(home_path),
                home_path / ".torque" / "runtime" / "venv",
            )
            self.assertEqual(
                install_locations.primary_runtime_python(home_path),
                home_path / ".torque" / "runtime" / "venv" / "bin" / "python",
            )
            self.assertEqual(
                install_locations.profile_data_dir("Desk Top!", home_path),
                home_path / ".torque" / "profiles" / "desk-top",
            )

    def test_legacy_iterm2_python_candidates_prefer_project_env(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            project = (
                home_path
                / "Library/Application Support/iTerm2/Scripts/torque/iterm2env/versions/3.14.0/bin/python3"
            )
            fallback = (
                home_path
                / ".config/iterm2/AppSupport/iterm2env-test/versions/3.15.0/bin/python3"
            )
            project.parent.mkdir(parents=True)
            fallback.parent.mkdir(parents=True)
            project.write_text("#!/bin/sh\n", encoding="utf-8")
            fallback.write_text("#!/bin/sh\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"HOME": str(home_path)}, clear=False):
                self.assertEqual(
                    install_locations.legacy_iterm2_python_candidates(),
                    [project.resolve()],
                )


if __name__ == "__main__":
    unittest.main()
