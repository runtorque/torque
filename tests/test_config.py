import importlib
import logging
import tempfile
import unittest
from pathlib import Path


class ConfigLoggingTests(unittest.TestCase):
    def test_init_paths_retargets_managed_log_handler(self):
        config = importlib.import_module("loom.config")
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
                first_file = first / "loom.log"
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
                second_file = second / "loom.log"
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
