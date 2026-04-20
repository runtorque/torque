import contextlib
import importlib.util
import io
import json
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock



def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "loom"
    loader = SourceFileLoader("loom_cli_doctor", str(path))
    spec = importlib.util.spec_from_loader("loom_cli_doctor", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliDoctorTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_parser_accepts_doctor_json_flag(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["doctor", "--json"])
        self.assertEqual(args.command, "doctor")
        self.assertTrue(args.json)

    def test_get_doctor_local_prefers_daemon_when_port_is_open(self):
        report = {"result": "pass", "source": "api"}
        with mock.patch.object(self.cli.socket, "create_connection", return_value=mock.MagicMock()), \
             mock.patch.object(self.cli, "api_call", return_value={"ok": True, "data": report}) as api_call, \
             mock.patch.object(self.cli, "db_read_doctor_report") as db_read:
            result = self.cli.get_doctor_local(18932)

        self.assertEqual(result, report)
        api_call.assert_called_once_with("doctor", port=18932)
        db_read.assert_not_called()

    def test_get_doctor_local_falls_back_to_sqlite_when_daemon_is_unavailable(self):
        report = {"result": "pass", "source": "sqlite"}
        with mock.patch.object(self.cli.socket, "create_connection", side_effect=OSError), \
             mock.patch.object(self.cli, "_db_available", return_value=True), \
             mock.patch.object(self.cli, "db_read_doctor_report", return_value=report) as db_read, \
             mock.patch.object(self.cli, "api_call") as api_call:
            result = self.cli.get_doctor_local(18932)

        self.assertEqual(result, report)
        db_read.assert_called_once_with()
        api_call.assert_not_called()

    def test_cmd_doctor_prints_json_report(self):
        report = {"schema_version": 1, "result": "pass", "checks": []}
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=True))

        self.assertEqual(json.loads(out.getvalue()), report)

    def test_cmd_doctor_exits_nonzero_on_fail(self):
        report = {"result": "fail", "checks": [], "failed_checks": ["migration_version"]}
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Result: FAIL", out.getvalue())

    def test_cmd_doctor_keeps_zero_exit_for_pass_with_warnings(self):
        report = {
            "result": "pass",
            "warnings": [
                {
                    "name": "unassigned_tasks_when_engineer_present",
                    "status": "warn",
                    "details": {"count": 2, "engineer_count": 1},
                }
            ],
            "checks": [],
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        self.assertIn("Result: PASS (with warnings)", out.getvalue())

    def test_cmd_doctor_keeps_zero_exit_for_shadowed_legacy_role_warning(self):
        report = {
            "result": "pass",
            "warnings": [
                {
                    "name": "shadowed_legacy_templates",
                    "status": "warn",
                    "details": {
                        "count": 1,
                        "slugs": ["shared"],
                        "hint": (
                            "legacy template shadowed by new role; "
                            "consider migrating the legacy file"
                        ),
                    },
                }
            ],
            "checks": [],
            "roles": {
                "roles_dir": "/tmp/.loom/roles",
                "roles_file_count": 1,
                "legacy_templates_dir": "/tmp/.loom/agents",
                "legacy_templates_file_count": 1,
                "shadowed_legacy_templates": 1,
                "roles_with_preamble": 1,
                "roles_with_priorities": 0,
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        text = out.getvalue()
        self.assertIn("Result: PASS (with warnings)", text)
        self.assertIn(
            "legacy template shadowed by new role; consider migrating the legacy file: shared",
            text,
        )


if __name__ == "__main__":
    unittest.main()
