import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli_daemon", str(path))
    spec = importlib.util.spec_from_loader("torque_cli_daemon", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliDaemonTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_parser_accepts_daemon_status(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["daemon", "status"])
        self.assertEqual(args.command, "daemon")
        self.assertEqual(args.daemon_cmd, "status")
        self.assertIs(args.func, self.cli.cmd_daemon_status)

    def test_parser_accepts_daemon_status_port_after_child(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["daemon", "status", "--port", "18972"])
        self.assertEqual(args.command, "daemon")
        self.assertEqual(args.daemon_cmd, "status")
        self.assertEqual(args.daemon_port, 18972)

    def test_resolve_daemon_status_port_uses_env_before_desktop_default(self):
        args = SimpleNamespace(port=self.cli.DEFAULT_PORT, daemon_port=None)
        with mock.patch.object(self.cli, "_argv_has_flag", return_value=False), \
             mock.patch.dict(os.environ, {"TORQUE_PORT": "19012"}):
            self.assertEqual(self.cli._resolve_daemon_status_port(args), 19012)

    def test_cmd_daemon_status_formats_get_config_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "torque.pid"
            pid_file.write_text(str(os.getpid()))
            payload = {
                "ok": True,
                "data": {
                    "current_profile": "desktop-torque-356",
                    "runtime": {
                        "mode": "desktop",
                        "profile": "desktop-torque-356",
                        "data_dir": tmpdir,
                        "port": 18972,
                        "version": "9.9.9-test",
                        "started_at": 1000,
                    },
                },
            }

            with mock.patch.object(self.cli, "_daemon_port_is_open", return_value=True), \
                 mock.patch.object(self.cli, "api_call", return_value=payload) as api_call, \
                 mock.patch.object(self.cli, "_format_uptime", return_value="2h 14m"):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.cli.cmd_daemon_status(
                        SimpleNamespace(port=self.cli.DEFAULT_PORT, daemon_port=18972)
                    )

        api_call.assert_called_once_with("get_config", port=18972)
        text = out.getvalue()
        self.assertIn("Daemon: running", text)
        self.assertIn(f"PID:    {os.getpid()}", text)
        self.assertIn("Port:   18972", text)
        self.assertIn("Profile: desktop-torque-356", text)
        self.assertIn(f"Data:   {tmpdir}", text)
        self.assertIn("Uptime: 2h 14m", text)
        self.assertIn("Version: 9.9.9-test", text)
        self.assertIn(f"Logs:   {tmpdir}/torque.log", text)

    def test_cmd_daemon_status_not_running_exits_one_without_api_call(self):
        with mock.patch.object(self.cli, "_daemon_port_is_open", return_value=False), \
             mock.patch.object(self.cli, "api_call") as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as exc:
                    self.cli.cmd_daemon_status(
                        SimpleNamespace(port=self.cli.DEFAULT_PORT, daemon_port=18972)
                    )

        self.assertEqual(exc.exception.code, 1)
        self.assertEqual(out.getvalue().strip(), "Daemon: not running")
        api_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
