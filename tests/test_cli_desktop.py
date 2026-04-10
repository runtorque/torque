import contextlib
import importlib.util
import io
import signal
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "loom"
    loader = SourceFileLoader("loom_cli_desktop", str(path))
    spec = importlib.util.spec_from_loader("loom_cli_desktop", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliDesktopTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()
        self.entrypoint = Path(__file__).resolve().parents[1] / "loom_desktop.py"

    def test_cmd_desktop_spawn_uses_desktop_defaults(self):
        calls = []
        self.cli._resolve_desktop_entrypoint = lambda: self.entrypoint
        self.cli._resolve_desktop_python = lambda explicit="": Path("/python/bin/python3")
        self.cli._argv_has_flag = lambda flag: False

        class FakeProc:
            def __init__(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        self.cli.subprocess.Popen = lambda cmd, env=None: (
            calls.append((cmd, env)) or FakeProc()
        )

        args = SimpleNamespace(
            port=self.cli.DEFAULT_PORT,
            profile="",
            data_dir="",
            python="",
            attach=False,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_desktop(args)

        self.assertEqual(
            calls[0][0],
            ["/python/bin/python3", str(self.entrypoint)],
        )
        self.assertEqual(calls[0][1]["LOOM_PORT"], "18933")
        self.assertEqual(calls[0][1]["LOOM_PROFILE"], "desktop")
        self.assertEqual(calls[0][1]["LOOM_DESKTOP_MODE"], "spawn")
        self.assertEqual(calls[0][1]["LOOM_DESKTOP_ATTACH"], "0")
        self.assertIn("/.loom/profiles/desktop", calls[0][1]["LOOM_DATA_DIR"])
        self.assertIn("Loom desktop spawn", out.getvalue())

    def test_cmd_desktop_attach_honors_explicit_target(self):
        calls = []
        self.cli._resolve_desktop_entrypoint = lambda: self.entrypoint
        self.cli._resolve_desktop_python = lambda explicit="": Path(explicit or "/python/bin/python3")
        self.cli._argv_has_flag = lambda flag: flag == "--port"

        class FakeProc:
            def __init__(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        self.cli.subprocess.Popen = lambda cmd, env=None: (
            calls.append((cmd, env)) or FakeProc()
        )

        args = SimpleNamespace(
            port=19001,
            profile="qa-desktop",
            data_dir="/tmp/loom-qa",
            python="/custom/python",
            attach=True,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_desktop(args)

        self.assertEqual(calls[0][1]["LOOM_PORT"], "19001")
        self.assertEqual(calls[0][1]["LOOM_PROFILE"], "qa-desktop")
        self.assertEqual(calls[0][1]["LOOM_DATA_DIR"], "/tmp/loom-qa")
        self.assertEqual(calls[0][0][0], "/custom/python")
        self.assertEqual(calls[0][1]["LOOM_DESKTOP_MODE"], "attach")
        self.assertEqual(calls[0][1]["LOOM_DESKTOP_ATTACH"], "1")
        self.assertIn("Loom desktop attach", out.getvalue())

    def test_cmd_desktop_propagates_nonzero_exit_status(self):
        self.cli._resolve_desktop_entrypoint = lambda: self.entrypoint
        self.cli._resolve_desktop_python = lambda explicit="": Path("/python/bin/python3")
        self.cli._argv_has_flag = lambda flag: False

        class FakeProc:
            def wait(self, timeout=None):
                return 7

        self.cli.subprocess.Popen = lambda cmd, env=None: FakeProc()

        args = SimpleNamespace(
            port=self.cli.DEFAULT_PORT,
            profile="",
            data_dir="",
            python="",
            attach=False,
        )

        with self.assertRaises(SystemExit) as ctx:
            self.cli.cmd_desktop(args)

        self.assertEqual(ctx.exception.code, 7)

    def test_resolve_desktop_python_prefers_iterm2_candidate_over_current_python(self):
        self.cli._candidate_iterm2_pythons = lambda: [
            Path("/global/python3"),
            Path("/project/python3"),
        ]

        resolved = self.cli._resolve_desktop_python("")

        self.assertEqual(resolved, Path("/project/python3"))

    def test_cmd_desktop_handles_keyboard_interrupt_cleanly(self):
        self.cli._resolve_desktop_entrypoint = lambda: self.entrypoint
        self.cli._resolve_desktop_python = lambda explicit="": Path("/python/bin/python3")
        self.cli._argv_has_flag = lambda flag: False

        class FakeProc:
            def __init__(self):
                self.signals = []
                self.wait_calls = 0

            def wait(self, timeout=None):
                self.wait_calls += 1
                if self.wait_calls == 1 and timeout is None:
                    raise KeyboardInterrupt
                return 130

            def send_signal(self, sig):
                self.signals.append(sig)

            def terminate(self):
                self.signals.append("terminate")

        fake_proc = FakeProc()
        self.cli.subprocess.Popen = lambda cmd, env=None: fake_proc

        args = SimpleNamespace(
            port=self.cli.DEFAULT_PORT,
            profile="",
            data_dir="",
            python="",
            attach=False,
        )

        with self.assertRaises(SystemExit) as ctx:
            self.cli.cmd_desktop(args)

        self.assertEqual(ctx.exception.code, 130)
        self.assertEqual(fake_proc.signals, [signal.SIGINT])
