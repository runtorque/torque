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

    def test_parser_accepts_doctor_mcp_health_flag(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["doctor", "--mcp-health"])
        self.assertEqual(args.command, "doctor")
        self.assertTrue(args.mcp_health)

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
        report = {"schema_version": 2, "result": "pass", "checks": []}
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=True))

        self.assertEqual(json.loads(out.getvalue()), report)

    def test_cmd_doctor_prints_mcp_health_only(self):
        report = {
            "result": "fail",
            "mcp_health": {
                "recent_window_seconds": 3600,
                "pending_failed_writes": 1,
                "totals": {"retry": 2, "drop": 1},
                "surfaces": {"loom": {"events": {"retry": 2}, "tools": {}}},
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(
                    SimpleNamespace(port=18932, json=False, mcp_health=True)
                )

        text = out.getvalue()
        self.assertIn("Loom MCP health", text)
        self.assertIn("pending_failed_writes: 1", text)
        self.assertIn("retries=2", text)

    def test_cmd_doctor_exits_nonzero_on_fail(self):
        report = {"result": "fail", "checks": [], "failed_checks": ["migration_version"]}
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Result: FAIL", out.getvalue())

    def test_cmd_doctor_prints_architect_and_pending_hire_sections(self):
        report = {
            "result": "pass",
            "warnings": [],
            "checks": [],
            "architects": {
                "total": 0,
                "architects": [],
            },
            "pending_hires": {
                "pending": 0,
                "approved_recent": 0,
                "rejected_recent": 0,
                "stale_pending": 0,
                "stale_pending_hires": [],
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        text = out.getvalue()
        self.assertIn("[architects]", text)
        self.assertIn("[pending_hires]", text)
        self.assertIn("pending:                  0", text)
        self.assertIn("stale_pending (>24h):     0", text)

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

    def test_cmd_doctor_keeps_zero_exit_for_stale_pending_hire_warning(self):
        report = {
            "result": "pass",
            "warnings": [
                {
                    "name": "stale_pending_hire",
                    "status": "warn",
                    "details": {
                        "id": "hire-1",
                        "architect_id": "arch-1",
                        "architect_name": "productmind",
                        "age_hours": 26,
                    },
                }
            ],
            "checks": [],
            "pending_hires": {
                "pending": 1,
                "approved_recent": 0,
                "rejected_recent": 0,
                "stale_pending": 1,
                "stale_pending_hires": [
                    {
                        "id": "hire-1",
                        "architect_id": "arch-1",
                        "architect_name": "productmind",
                        "age_hours": 26,
                    }
                ],
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        text = out.getvalue()
        self.assertIn("Result: PASS (with warnings)", text)
        self.assertIn(
            "pending hire hire-1 from productmind has been waiting 26 hours; approve or reject",
            text,
        )

    def test_cmd_doctor_keeps_zero_exit_for_ignored_legacy_role_warning(self):
        report = {
            "result": "pass",
            "warnings": [
                {
                    "name": "legacy_template_files_ignored",
                    "status": "warn",
                    "details": {
                        "count": 1,
                        "files": ["~/.loom/agents/shared.yaml"],
                        "hint": (
                            "legacy template files in agents/ are ignored; "
                            "move them into roles/"
                        ),
                    },
                }
            ],
            "checks": [],
            "roles": {
                "roles_dir": "/tmp/.loom/roles",
                "roles_file_count": 1,
                "roles_with_preamble": 1,
                "roles_with_priorities": 0,
            },
            "stage_6_cleanup": {
                "legacy_template_files_ignored": 1,
                "legacy_columns_present": False,
                "weaver_tool_aliases_present": False,
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        text = out.getvalue()
        self.assertIn("Result: PASS (with warnings)", text)
        self.assertIn(
            "legacy template files in agents/ are ignored; move them into roles/: ~/.loom/agents/shared.yaml",
            text,
        )

    def test_cmd_doctor_prints_no_engineers_warning(self):
        report = {
            "result": "pass",
            "warnings": [
                {
                    "name": "no_engineers",
                    "status": "warn",
                    "details": {
                        "count": 0,
                        "hint": (
                            "no engineer exists; create one from the Agent panel "
                            "before using engineer MCP tools"
                        ),
                    },
                }
            ],
            "checks": [],
            "engineers": {
                "total": 0,
                "engineers": [],
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        text = out.getvalue()
        self.assertIn("Result: PASS (with warnings)", text)
        self.assertIn(
            "no engineer exists; create one from the Agent panel before using engineer MCP tools",
            text,
        )

    def test_cmd_doctor_no_longer_prints_default_engineer_routing(self):
        report = {
            "result": "pass",
            "warnings": [],
            "checks": [],
            "engineers": {
                "total": 2,
                "engineers": [
                    {
                        "id": "eng-alice",
                        "name": "Alice",
                        "slug": "alice",
                        "persistent": 1,
                        "worker_count": 0,
                        "task_count": 0,
                    },
                    {
                        "id": "eng-bob",
                        "name": "Bob",
                        "slug": "bob",
                        "persistent": 1,
                        "worker_count": 0,
                        "task_count": 0,
                    },
                ],
            },
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        text = out.getvalue()
        self.assertNotIn("default (weaver_* routing)", text)

    def test_cmd_doctor_exits_nonzero_for_invalid_architect_state(self):
        report = {
            "result": "fail",
            "warnings": [],
            "failed_checks": ["invalid_architect_hired_by_architect"],
            "checks": [
                {
                    "name": "invalid_architect_hired_by_architect",
                    "status": "fail",
                    "details": {
                        "count": 1,
                        "invalid": [
                            {
                                "id": "arch-1",
                                "name": "productmind",
                                "hired_by_architect_id": "arch-parent",
                            }
                        ],
                    },
                }
            ],
        }
        with mock.patch.object(self.cli, "get_doctor_local", return_value=report):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                self.cli.cmd_doctor(SimpleNamespace(port=18932, json=False))

        self.assertEqual(cm.exception.code, 1)
        self.assertIn(
            "architect productmind has hired_by_architect_id, invalid state",
            out.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
