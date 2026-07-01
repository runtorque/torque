import builtins
import os
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


class TorqueEntrypointTests(unittest.TestCase):
    def _entrypoint_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "torque.py"

    def test_entrypoint_forces_standalone_without_iterm2(self):
        init_calls = []
        main_calls = []

        config_mod = types.ModuleType("torque.config")
        config_mod.STANDALONE = False

        def init_paths(script_dir):
            init_calls.append(script_dir)

        config_mod.init_paths = init_paths

        server_mod = types.ModuleType("torque.server")

        async def main(connection):
            main_calls.append(connection)

        server_mod.main = main

        original_import = builtins.__import__

        def fail_on_iterm2_import(name, *args, **kwargs):
            if name == "iterm2":
                raise AssertionError("torque.py must not import iterm2")
            return original_import(name, *args, **kwargs)

        with mock.patch.dict(
            sys.modules,
            {
                "torque.config": config_mod,
                "torque.server": server_mod,
            },
        ), mock.patch.dict(
            os.environ,
            {"TORQUE_STANDALONE": "0", "TORQUE_PROFILE": "", "TORQUE_DATA_DIR": ""},
            clear=False,
        ), mock.patch.object(
            builtins,
            "__import__",
            side_effect=fail_on_iterm2_import,
        ):
            runpy.run_path(str(self._entrypoint_path()), run_name="__main__")
            standalone_value = os.environ["TORQUE_STANDALONE"]
            profile_value = os.environ["TORQUE_PROFILE"]

        self.assertEqual(standalone_value, "1")
        self.assertEqual(profile_value, "default")
        self.assertEqual(init_calls, [self._entrypoint_path().parent])
        self.assertEqual(main_calls, [None])

    def test_entrypoint_does_not_invent_profile_when_data_dir_is_explicit(self):
        config_mod = types.ModuleType("torque.config")
        config_mod.init_paths = lambda _script_dir: None

        server_mod = types.ModuleType("torque.server")

        async def main(_connection):
            return None

        server_mod.main = main

        with mock.patch.dict(
            sys.modules,
            {
                "torque.config": config_mod,
                "torque.server": server_mod,
            },
        ), mock.patch.dict(
            os.environ,
            {
                "TORQUE_STANDALONE": "",
                "TORQUE_PROFILE": "",
                "TORQUE_DATA_DIR": "/tmp/torque-explicit",
            },
            clear=False,
        ):
            runpy.run_path(str(self._entrypoint_path()), run_name="__main__")
            self.assertEqual(os.environ["TORQUE_STANDALONE"], "1")
            self.assertEqual(os.environ["TORQUE_PROFILE"], "")


if __name__ == "__main__":
    unittest.main()
