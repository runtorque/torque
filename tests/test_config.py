import importlib
import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock


class ConfigLoggingTests(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(os.environ, {
            "TORQUE_DATA_DIR": "",
            "TORQUE_PROFILE": "",
            "TORQUE_STANDALONE": "",
        }, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _reload_default_config(self):
        config = importlib.import_module("torque.config")
        return importlib.reload(config)

    def test_init_paths_retargets_managed_log_handler(self):
        config = importlib.import_module("torque.config")
        config = importlib.reload(config)

        original_handlers = list(config.log.handlers)
        for handler in list(original_handlers):
            config.log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        try:
            with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
                first = Path(td1) / "runtime-a"
                second = Path(td2) / "runtime-b"

                os.environ["TORQUE_DATA_DIR"] = str(first)
                config.init_paths(first)
                first_file = first / "torque.log"
                self.assertTrue(first_file.exists())
                self.assertEqual(
                    [getattr(h, "baseFilename", "") for h in config.log.handlers],
                    [str(first_file.resolve())],
                )

                config.log.info("first target")
                for handler in config.log.handlers:
                    handler.flush()
                self.assertIn("first target", first_file.read_text())

                os.environ["TORQUE_DATA_DIR"] = str(second)
                config.init_paths(second)
                second_file = second / "torque.log"
                self.assertTrue(second_file.exists())
                self.assertEqual(
                    [getattr(h, "baseFilename", "") for h in config.log.handlers],
                    [str(second_file.resolve())],
                )

                config.log.info("second target")
                for handler in config.log.handlers:
                    handler.flush()
                self.assertIn("second target", second_file.read_text())
        finally:
            for handler in list(config.log.handlers):
                config.log.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
            for handler in original_handlers:
                config.log.addHandler(handler)
            config.log.setLevel(logging.DEBUG)
            config.log.propagate = False

    def test_init_paths_uses_rotating_log_handler_and_rolls_over(self):
        config = importlib.import_module("torque.config")
        config = importlib.reload(config)

        original_handlers = list(config.log.handlers)
        old_max_bytes = config.LOG_ROTATION_MAX_BYTES
        old_backup_count = config.LOG_ROTATION_BACKUP_COUNT
        for handler in list(original_handlers):
            config.log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        try:
            config.LOG_ROTATION_MAX_BYTES = 180
            config.LOG_ROTATION_BACKUP_COUNT = 2
            with tempfile.TemporaryDirectory() as td:
                runtime = Path(td) / "runtime"
                os.environ["TORQUE_DATA_DIR"] = str(runtime)
                config.init_paths(runtime)
                handlers = [
                    handler for handler in config.log.handlers
                    if getattr(handler, "_torque_managed", False)
                ]
                self.assertEqual(len(handlers), 1)
                handler = handlers[0]
                self.assertIsInstance(handler, RotatingFileHandler)
                self.assertEqual(handler.maxBytes, 180)
                self.assertEqual(handler.backupCount, 2)

                for idx in range(20):
                    config.log.info("rotation-test-%02d %s", idx, "x" * 80)
                for handler in config.log.handlers:
                    handler.flush()

                self.assertTrue((runtime / "torque.log").exists())
                self.assertTrue((runtime / "torque.log.1").exists())
        finally:
            config.LOG_ROTATION_MAX_BYTES = old_max_bytes
            config.LOG_ROTATION_BACKUP_COUNT = old_backup_count
            for handler in list(config.log.handlers):
                config.log.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
            for handler in original_handlers:
                config.log.addHandler(handler)
            config.log.setLevel(logging.DEBUG)
            config.log.propagate = False

    def test_init_paths_removes_stale_pre_kinds_backup(self):
        config = importlib.import_module("torque.config")
        config = importlib.reload(config)

        original_handlers = list(config.log.handlers)
        for handler in list(original_handlers):
            config.log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        try:
            with tempfile.TemporaryDirectory() as td:
                runtime = Path(td) / "runtime"
                runtime.mkdir()
                backup_path = runtime / "torque.db.pre-kinds.bak"
                backup_path.write_bytes(b"stale backup")

                os.environ["TORQUE_DATA_DIR"] = str(runtime)
                config.init_paths(runtime)

                self.assertFalse(backup_path.exists())
                self.assertFalse(config._cleanup_stale_pre_kinds_backup(runtime))
        finally:
            for handler in list(config.log.handlers):
                config.log.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
            for handler in original_handlers:
                config.log.addHandler(handler)
            config.log.setLevel(logging.DEBUG)
            config.log.propagate = False

    def test_init_paths_falls_back_when_rotating_handler_fails(self):
        config = importlib.import_module("torque.config")
        config = importlib.reload(config)

        original_handlers = list(config.log.handlers)
        for handler in list(original_handlers):
            config.log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        try:
            with tempfile.TemporaryDirectory() as td, mock.patch(
                "torque.config.RotatingFileHandler",
                side_effect=OSError("rotation unavailable"),
            ):
                runtime = Path(td) / "runtime"
                os.environ["TORQUE_DATA_DIR"] = str(runtime)
                config.init_paths(runtime)

                managed = [
                    handler for handler in config.log.handlers
                    if getattr(handler, "_torque_managed", False)
                ]
                self.assertEqual(len(managed), 1)
                self.assertIsInstance(managed[0], logging.FileHandler)
                self.assertIn(
                    "rotation unavailable",
                    getattr(managed[0], "_torque_rotation_error", ""),
                )
                config.log.info("fallback still writes")
                managed[0].flush()
                self.assertIn(
                    "fallback still writes",
                    (runtime / "torque.log").read_text(encoding="utf-8"),
                )
        finally:
            for handler in list(config.log.handlers):
                config.log.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
            for handler in original_handlers:
                config.log.addHandler(handler)
            config.log.setLevel(logging.DEBUG)
            config.log.propagate = False

    def test_init_paths_uses_shared_default_profile_by_default(self):
        with tempfile.TemporaryDirectory() as home_dir:
            env = {
                "HOME": home_dir,
                "TORQUE_STANDALONE": "1",
                "TORQUE_PROFILE": "",
                "TORQUE_DATA_DIR": "",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                config = importlib.import_module("torque.config")
                config = importlib.reload(config)

                script_dir = Path(home_dir) / "installed-runtime"
                config.init_paths(script_dir)

                expected = Path(home_dir) / ".torque" / "profiles" / "default"
                self.assertEqual(config.DATA_DIR, expected)
                self.assertEqual(config.DB_FILE, expected / "torque.db")
                self.assertEqual(config.STATE_FILE, expected / "state.json")
                self.assertEqual(config.LOG_FILE, expected / "torque.log")
                self.assertEqual(config.WEBVIEW_FILE, script_dir / "webview.html")
                self.assertEqual(
                    config.ATTACHMENTS_DIR,
                    expected / "attachments",
                )
            self._reload_default_config()

    def test_init_paths_default_profile_does_not_mutate_old_desktop_or_standalone_profiles(self):
        with tempfile.TemporaryDirectory() as home_dir:
            home_path = Path(home_dir)
            desktop_marker = home_path / ".torque" / "profiles" / "desktop" / "marker.txt"
            standalone_marker = home_path / ".torque" / "profiles" / "standalone" / "marker.txt"
            desktop_marker.parent.mkdir(parents=True)
            standalone_marker.parent.mkdir(parents=True)
            desktop_marker.write_text("desktop", encoding="utf-8")
            standalone_marker.write_text("standalone", encoding="utf-8")
            env = {
                "HOME": home_dir,
                "TORQUE_STANDALONE": "1",
                "TORQUE_PROFILE": "",
                "TORQUE_DATA_DIR": "",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                config = importlib.import_module("torque.config")
                config = importlib.reload(config)

                script_dir = Path(home_dir) / "installed-runtime"
                config.init_paths(script_dir)

                self.assertEqual(
                    config.DATA_DIR,
                    home_path / ".torque" / "profiles" / "default",
                )
                self.assertEqual(desktop_marker.read_text(encoding="utf-8"), "desktop")
                self.assertEqual(standalone_marker.read_text(encoding="utf-8"), "standalone")
            self._reload_default_config()

    def test_init_paths_prefers_explicit_data_dir_over_profile(self):
        with tempfile.TemporaryDirectory() as home_dir:
            explicit_dir = Path(home_dir) / "custom-runtime"
            env = {
                "HOME": home_dir,
                "TORQUE_STANDALONE": "1",
                "TORQUE_PROFILE": "qa-profile",
                "TORQUE_DATA_DIR": str(explicit_dir),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                config = importlib.import_module("torque.config")
                config = importlib.reload(config)

                script_dir = Path(home_dir) / "installed-runtime"
                config.init_paths(script_dir)

                self.assertEqual(config.DATA_DIR, explicit_dir)
                self.assertEqual(config.DB_FILE, explicit_dir / "torque.db")
                self.assertEqual(config.LOG_FILE, explicit_dir / "torque.log")
                self.assertEqual(
                    config.ATTACHMENTS_DIR,
                    explicit_dir / "attachments",
                )
            self._reload_default_config()

    def test_init_paths_uses_default_profile_when_runtime_mode_is_unset(self):
        with tempfile.TemporaryDirectory() as home_dir:
            env = {
                "HOME": home_dir,
                "TORQUE_STANDALONE": "",
                "TORQUE_PROFILE": "",
                "TORQUE_DATA_DIR": "",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                config = importlib.import_module("torque.config")
                config = importlib.reload(config)

                script_dir = Path(home_dir) / "installed-runtime"
                config.init_paths(script_dir)

                expected = Path(home_dir) / ".torque" / "profiles" / "default"
                self.assertEqual(config.DATA_DIR, expected)
                self.assertEqual(config.DB_FILE, expected / "torque.db")
                self.assertEqual(
                    config.ATTACHMENTS_DIR,
                    expected / "attachments",
                )
            self._reload_default_config()
