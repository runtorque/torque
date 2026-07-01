import importlib.util
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli_runtime_paths", str(path))
    spec = importlib.util.spec_from_loader("torque_cli_runtime_paths", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliRuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def _patch_home(self, home: Path, **extra):
        env = {
            "HOME": str(home),
            "TORQUE_DATA_DIR": "",
            "TORQUE_PROFILE": "",
            "TORQUE_STANDALONE": "",
            "TORQUE_PORT": "",
        }
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=False)

    def test_default_runtime_dir_is_shared_default_profile(self):
        with tempfile.TemporaryDirectory() as home, self._patch_home(Path(home)):
            self.cli._configure_runtime_paths(
                SimpleNamespace(command="status", port=self.cli.DESKTOP_DEFAULT_PORT)
            )

            self.assertEqual(
                self.cli.RUNTIME_DIR,
                Path(home) / ".torque" / "profiles" / "default",
            )
            self.assertEqual(
                self.cli.TORQUE_DB,
                str(Path(home) / ".torque" / "profiles" / "default" / "torque.db"),
            )

    def test_default_port_uses_shared_default_profile(self):
        with tempfile.TemporaryDirectory() as home, self._patch_home(Path(home)):
            self.cli._configure_runtime_paths(
                SimpleNamespace(command="status", port=self.cli.DEFAULT_PORT)
            )

            self.assertEqual(
                self.cli.RUNTIME_DIR,
                Path(home) / ".torque" / "profiles" / "default",
            )

    def test_default_resolution_leaves_existing_desktop_and_standalone_profiles_in_place(self):
        with tempfile.TemporaryDirectory() as home, self._patch_home(Path(home)):
            home_path = Path(home)
            desktop_marker = home_path / ".torque" / "profiles" / "desktop" / "marker.txt"
            standalone_marker = home_path / ".torque" / "profiles" / "standalone" / "marker.txt"
            desktop_marker.parent.mkdir(parents=True)
            standalone_marker.parent.mkdir(parents=True)
            desktop_marker.write_text("desktop", encoding="utf-8")
            standalone_marker.write_text("standalone", encoding="utf-8")

            self.cli._configure_runtime_paths(
                SimpleNamespace(command="status", port=self.cli.DEFAULT_PORT)
            )

            self.assertEqual(
                self.cli.RUNTIME_DIR,
                home_path / ".torque" / "profiles" / "default",
            )
            self.assertEqual(desktop_marker.read_text(encoding="utf-8"), "desktop")
            self.assertEqual(standalone_marker.read_text(encoding="utf-8"), "standalone")

    def test_profile_and_data_dir_override_port(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            with self._patch_home(home_path, TORQUE_PROFILE="QA Profile"):
                self.cli._configure_runtime_paths(
                    SimpleNamespace(command="status", port=self.cli.DEFAULT_PORT)
                )
                self.assertEqual(
                    self.cli.RUNTIME_DIR,
                    home_path / ".torque" / "profiles" / "qa-profile",
                )

            custom = home_path / "custom-data"
            with self._patch_home(
                home_path,
                TORQUE_PROFILE="QA Profile",
                TORQUE_DATA_DIR=str(custom),
            ):
                self.cli._configure_runtime_paths(
                    SimpleNamespace(command="status", port=self.cli.DEFAULT_PORT)
                )
                self.assertEqual(self.cli.RUNTIME_DIR, custom)

    def test_legacy_toolbelt_db_is_fallback_only_when_primary_missing(self):
        with tempfile.TemporaryDirectory() as home, self._patch_home(Path(home)):
            home_path = Path(home)
            legacy_db = (
                home_path
                / "Library/Application Support/iTerm2/Scripts/torque/torque/torque.db"
            )
            legacy_db.parent.mkdir(parents=True)
            legacy_db.write_text("legacy", encoding="utf-8")

            self.cli._configure_runtime_paths(
                SimpleNamespace(command="status", port=self.cli.DESKTOP_DEFAULT_PORT)
            )
            self.assertEqual(self.cli.RUNTIME_DIR, legacy_db.parent)

            primary_db = home_path / ".torque" / "profiles" / "default" / "torque.db"
            primary_db.parent.mkdir(parents=True)
            primary_db.write_text("primary", encoding="utf-8")
            self.cli._configure_runtime_paths(
                SimpleNamespace(command="status", port=self.cli.DESKTOP_DEFAULT_PORT)
            )
            self.assertEqual(self.cli.RUNTIME_DIR, primary_db.parent)

    def test_explicit_profile_disables_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            legacy_db = (
                home_path
                / "Library/Application Support/iTerm2/Scripts/torque/torque/torque.db"
            )
            legacy_db.parent.mkdir(parents=True)
            legacy_db.write_text("legacy", encoding="utf-8")

            with self._patch_home(home_path, TORQUE_PROFILE="desktop"):
                self.cli._configure_runtime_paths(
                    SimpleNamespace(command="status", port=self.cli.DESKTOP_DEFAULT_PORT)
                )

            self.assertEqual(
                self.cli.RUNTIME_DIR,
                home_path / ".torque" / "profiles" / "desktop",
            )

    def test_logs_use_log_artifact_for_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as home, self._patch_home(Path(home)):
            home_path = Path(home)
            legacy_log = (
                home_path
                / "Library/Application Support/iTerm2/Scripts/torque/torque/torque.log"
            )
            legacy_log.parent.mkdir(parents=True)
            legacy_log.write_text("legacy log", encoding="utf-8")

            self.cli._configure_runtime_paths(
                SimpleNamespace(command="logs", port=self.cli.DESKTOP_DEFAULT_PORT)
            )

            self.assertEqual(self.cli.RUNTIME_DIR, legacy_log.parent)
            self.assertEqual(self.cli.TORQUE_LOG, str(legacy_log))


if __name__ == "__main__":
    unittest.main()
