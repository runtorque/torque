import contextlib
import importlib.util
import io
import signal
import os
import tempfile
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
        self.cli._ensure_desktop_runtime_ready = lambda python_path: None
        self._orig_popen = self.cli.subprocess.Popen
        self.addCleanup(
            setattr,
            self.cli.subprocess,
            "Popen",
            self._orig_popen,
        )

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

    def test_resolve_desktop_profile_ignores_general_loom_profile(self):
        with mock.patch.dict(os.environ, {
            "LOOM_PROFILE": "toolbelt-profile",
            "LOOM_DESKTOP_PROFILE": "",
        }, clear=False):
            self.assertEqual(
                self.cli._resolve_desktop_profile(""),
                self.cli.DESKTOP_DEFAULT_PROFILE,
            )

    def test_resolve_desktop_port_ignores_general_loom_port(self):
        args = SimpleNamespace(port=self.cli.DEFAULT_PORT)
        self.cli._argv_has_flag = lambda flag: False
        with mock.patch.dict(os.environ, {
            "LOOM_PORT": "18932",
            "LOOM_DESKTOP_PORT": "",
        }, clear=False):
            self.assertEqual(
                self.cli._resolve_desktop_port(args),
                self.cli.DESKTOP_DEFAULT_PORT,
            )

    def test_build_parser_accepts_desktop_port_after_subcommand(self):
        parser = self.cli.build_parser()

        args = parser.parse_args(["desktop", "--port", "19046"])

        self.assertEqual(args.command, "desktop")
        self.assertEqual(args.desktop_port, 19046)
        self.assertIs(args.func, self.cli.cmd_desktop)

    def test_build_parser_accepts_top_level_port_before_desktop_subcommand(self):
        parser = self.cli.build_parser()

        args = parser.parse_args(["--port", "19046", "desktop"])

        self.assertEqual(args.command, "desktop")
        self.assertEqual(args.port, 19046)
        self.assertIsNone(args.desktop_port)
        self.assertIs(args.func, self.cli.cmd_desktop)

    def test_resolve_desktop_port_accepts_top_level_before_subcommand_form(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["--port", "19046", "desktop"])
        self.cli._argv_has_flag = lambda flag: flag == "--port"

        self.assertEqual(self.cli._resolve_desktop_port(args), 19046)

    def test_desktop_runtime_guardrail_requires_pywebview_in_selected_python(self):
        cli = _load_cli_module()
        cli._python_has_module = lambda python_path, module_name: False
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            fake_python = Path(td) / "python3"
            fake_python.write_text("#!/bin/sh\n")

            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as ctx:
                    cli._ensure_desktop_runtime_ready(fake_python)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("make desktop-deps", stderr.getvalue())
        self.assertIn("--python", stderr.getvalue())

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
