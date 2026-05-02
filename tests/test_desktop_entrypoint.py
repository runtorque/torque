import importlib
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DesktopEntrypointTests(unittest.TestCase):
    def _entrypoint_path(self):
        return Path(__file__).resolve().parents[1] / "torque_desktop.py"

    def _reset_config(self):
        config = importlib.import_module("torque.config")
        importlib.reload(config)

    def test_entrypoint_defaults_do_not_reuse_toolbelt_runtime_env(self):
        with tempfile.TemporaryDirectory() as home_dir:
            env = {
                "HOME": home_dir,
                "TORQUE_PROFILE": "toolbelt-profile",
                "TORQUE_PORT": "18932",
                "TORQUE_DATA_DIR": "/tmp/toolbelt-runtime",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                runpy.run_path(str(self._entrypoint_path()), run_name="torque_desktop_test")

                expected_dir = str(Path(home_dir) / ".torque" / "profiles" / "desktop")
                self.assertEqual(os.environ["TORQUE_DESKTOP_PROFILE"], "desktop")
                self.assertEqual(os.environ["TORQUE_DESKTOP_PORT"], "18933")
                self.assertEqual(os.environ["TORQUE_DESKTOP_DATA_DIR"], expected_dir)
                self.assertEqual(os.environ["TORQUE_PROFILE"], "desktop")
                self.assertEqual(os.environ["TORQUE_PORT"], "18933")
                self.assertEqual(os.environ["TORQUE_DATA_DIR"], expected_dir)
                self.assertEqual(os.environ["TORQUE_DESKTOP_MODE"], "spawn")
                self.assertEqual(os.environ["TORQUE_STANDALONE"], "1")

        self._reset_config()

    def test_entrypoint_prefers_desktop_specific_env_over_general_torque_env(self):
        with tempfile.TemporaryDirectory() as home_dir:
            env = {
                "HOME": home_dir,
                "TORQUE_PROFILE": "toolbelt-profile",
                "TORQUE_PORT": "18932",
                "TORQUE_DATA_DIR": "/tmp/toolbelt-runtime",
                "TORQUE_DESKTOP_PROFILE": "desktop-qa",
                "TORQUE_DESKTOP_PORT": "19050",
                "TORQUE_DESKTOP_DATA_DIR": "/tmp/desktop-qa",
                "TORQUE_DESKTOP_MODE": "attach",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                runpy.run_path(str(self._entrypoint_path()), run_name="torque_desktop_test")

                self.assertEqual(os.environ["TORQUE_PROFILE"], "desktop-qa")
                self.assertEqual(os.environ["TORQUE_PORT"], "19050")
                self.assertEqual(os.environ["TORQUE_DATA_DIR"], "/tmp/desktop-qa")
                self.assertEqual(os.environ["TORQUE_DESKTOP_PROFILE"], "desktop-qa")
                self.assertEqual(os.environ["TORQUE_DESKTOP_PORT"], "19050")
                self.assertEqual(os.environ["TORQUE_DESKTOP_DATA_DIR"], "/tmp/desktop-qa")
                self.assertEqual(os.environ["TORQUE_DESKTOP_MODE"], "attach")

        self._reset_config()
