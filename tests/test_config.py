import importlib
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ConfigLoggingTests(unittest.TestCase):
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

    def test_init_paths_uses_standalone_profile_by_default(self):
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

                expected = Path(home_dir) / ".torque" / "profiles" / "standalone"
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

    def test_init_paths_keeps_legacy_iterm_runtime_when_unset(self):
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

                self.assertEqual(config.DATA_DIR, script_dir)
                self.assertEqual(config.DB_FILE, script_dir / "torque.db")
                self.assertEqual(
                    config.ATTACHMENTS_DIR,
                    Path(home_dir) / ".torque" / "attachments",
                )
            self._reload_default_config()
