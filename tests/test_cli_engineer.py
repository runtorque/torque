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
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli_engineer", str(path))
    spec = importlib.util.spec_from_loader("torque_cli_engineer", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliEngineerTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_parser_accepts_task_create_engineer_flag(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["task", "create", "Ship it", "--engineer", "alice"])
        self.assertEqual(args.command, "task")
        self.assertEqual(args.task_cmd, "create")
        self.assertEqual(args.engineer, "alice")

    def test_parser_accepts_task_edit_engineer_flag(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["task", "edit", "task-1", "--engineer", "alice"])
        self.assertEqual(args.command, "task")
        self.assertEqual(args.task_cmd, "edit")
        self.assertEqual(args.engineer, "alice")

    def test_parser_accepts_engineer_list_json_flag(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["engineer", "list", "--json"])
        self.assertEqual(args.command, "engineer")
        self.assertEqual(args.engineer_cmd, "list")
        self.assertTrue(args.json)

    def test_parser_accepts_engineer_dismiss_and_rehire(self):
        parser = self.cli.build_parser()
        dismiss_args = parser.parse_args(
            ["engineer", "dismiss", "alice", "--reason", "pause"]
        )
        self.assertEqual(dismiss_args.command, "engineer")
        self.assertEqual(dismiss_args.engineer_cmd, "dismiss")
        self.assertEqual(dismiss_args.identifier, "alice")
        self.assertEqual(dismiss_args.reason, "pause")

        rehire_args = parser.parse_args(["engineer", "rehire", "alice"])
        self.assertEqual(rehire_args.command, "engineer")
        self.assertEqual(rehire_args.engineer_cmd, "rehire")
        self.assertEqual(rehire_args.identifier, "alice")

    def test_parser_accepts_architect_settings(self):
        parser = self.cli.build_parser()
        args = parser.parse_args([
            "architect",
            "settings",
            "--group",
            "torque",
            "--json",
        ])
        self.assertEqual(args.command, "architect")
        self.assertEqual(args.architect_cmd, "settings")
        self.assertEqual(args.group, "torque")
        self.assertTrue(args.json)

    def test_parser_accepts_mcp_log(self):
        parser = self.cli.build_parser()
        args = parser.parse_args([
            "mcp-log",
            "worker-a",
            "--limit",
            "5",
            "--tool-filter",
            "mcp__torque__%",
            "--json",
        ])
        self.assertEqual(args.command, "mcp-log")
        self.assertEqual(args.agent, "worker-a")
        self.assertEqual(args.limit, 5)
        self.assertTrue(args.json)
        all_hooks = parser.parse_args(["mcp-log", "worker-a", "--hook", ""])
        self.assertEqual(all_hooks.hook, "")

    def test_cmd_mcp_log_resolves_agent_and_calls_daemon(self):
        state = {
            "agents": {
                "worker-a": {
                    "id": "worker-a",
                    "name": "Worker A",
                    "slug": "worker-a",
                    "group": "torque",
                    "kind": "worker",
                    "cell_type": "agent",
                }
            }
        }
        args = SimpleNamespace(
            port=18932,
            agent="worker-a",
            limit=3,
            tool_filter="mcp__torque__%",
            hook="PostToolUse",
            since_seconds=0,
            json=False,
        )
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(
                 self.cli,
                 "api_call",
                 return_value={
                     "ok": True,
                     "data": {
                         "calls": [{
                             "tool_name": "mcp__torque__torque_progress",
                             "hook_event_name": "PostToolUse",
                             "appended_at": 1712345678,
                             "success": True,
                             "args": {"redacted": True, "arg_keys": ["message"]},
                         }]
                     },
                 },
             ) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_mcp_log(args)

        api_call.assert_called_once_with(
            "mcp_calls",
            port=18932,
            cell_id="worker-a",
            tool_name_pattern="mcp__torque__%",
            hook_event_name="PostToolUse",
            since=None,
            limit=3,
        )
        self.assertIn("mcp__torque__torque_progress", out.getvalue())
        self.assertIn("args:redacted", out.getvalue())

    def test_cmd_engineer_dismiss_and_rehire_resolve_engineer_and_call_daemon(self):
        state = {
            "agents": {
                "eng-alice": {
                    "id": "eng-alice",
                    "name": "Alice",
                    "slug": "alice",
                    "group": "torque",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            }
        }
        dismiss_args = SimpleNamespace(
            port=18932,
            identifier="alice",
            reason="pause",
        )
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(
                 self.cli,
                 "api_call",
                 return_value={"ok": True, "data": {"closed_sessions": 2}},
             ) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_engineer_dismiss(dismiss_args)

        api_call.assert_called_once_with(
            "engineer_dismiss",
            port=18932,
            engineer_id="eng-alice",
            reason="pause",
        )
        self.assertIn('Dismissed "Alice"', out.getvalue())

        rehire_args = SimpleNamespace(port=18932, identifier="Alice")
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(
                 self.cli,
                 "api_call",
                 return_value={"ok": True, "data": {"replayed_messages": 1}},
             ) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_engineer_rehire(rehire_args)

        api_call.assert_called_once_with(
            "engineer_rehire",
            port=18932,
            engineer_id="eng-alice",
        )
        self.assertIn('Rehired "Alice"', out.getvalue())

    def test_cmd_architect_settings_rejects_updates(self):
        state = {"groups": {"torque": []}, "group_slugs": {"torque": "torque"}}
        args = SimpleNamespace(
            port=18932,
            group="torque",
            set=[
                "architect_autonomy_mode=ask_always",
                "architect_digest_verbosity=verbose",
            ],
            json=False,
        )
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(
                 self.cli,
                 "api_call",
                 return_value={"ok": True, "data": {}},
             ) as api_call:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                self.cli.cmd_architect_settings(args)

        api_call.assert_not_called()
        self.assertIn(
            "Updating architect settings from the CLI has been removed",
            err.getvalue(),
        )

    def test_cmd_task_create_sets_assigned_engineer_id_from_slug(self):
        state = {
            "agents": {
                "eng-alice": {
                    "id": "eng-alice",
                    "name": "Alice",
                    "slug": "alice",
                    "group": "torque",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            }
        }
        args = SimpleNamespace(
            port=18932,
            description="Ship it",
            group="torque",
            engineer="alice",
            context=None,
            labels=None,
            action=None,
            var=None,
            at=None,
            depends_on=None,
        )
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(self.cli, "api_call", return_value={"ok": True, "data": {}}) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_task_create(args)

        api_call.assert_called_once_with(
            "board_add_task",
            port=18932,
            task="Ship it",
            group="torque",
            lane="Backlog",
            assigned_engineer_id="eng-alice",
        )
        self.assertIn("Task added to Backlog", out.getvalue())

    def test_cmd_task_create_rejects_unknown_engineer_slug(self):
        state = {"agents": {}}
        args = SimpleNamespace(
            port=18932,
            description="Ship it",
            group="torque",
            engineer="alice",
            context=None,
            labels=None,
            action=None,
            var=None,
            at=None,
            depends_on=None,
        )
        err = io.StringIO()
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as cm:
            self.cli.cmd_task_create(args)

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("no engineer with slug 'alice'", err.getvalue())

    def test_cmd_task_create_rejects_cross_group_engineer_slug(self):
        state = {
            "agents": {
                "eng-bob": {
                    "id": "eng-bob",
                    "name": "Bob",
                    "slug": "bob",
                    "group": "group-b",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            }
        }
        args = SimpleNamespace(
            port=18932,
            description="Ship it",
            group="group-a",
            engineer="bob",
            context=None,
            labels=None,
            action=None,
            var=None,
            at=None,
            depends_on=None,
        )
        err = io.StringIO()
        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(self.cli, "api_call") as api_call, \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as cm:
            self.cli.cmd_task_create(args)

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("engineer 'bob' belongs to group 'group-b', not 'group-a'", err.getvalue())
        api_call.assert_not_called()

    def test_cmd_task_edit_sets_assigned_engineer_id_from_slug(self):
        state = {
            "agents": {
                "eng-alice": {
                    "id": "eng-alice",
                    "name": "Alice",
                    "slug": "alice",
                    "group": "torque",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            },
            "board_tasks": {
                "task-1": {
                    "id": "task-1",
                    "task": "Ship it",
                    "slug": "ship-it",
                    "group": "torque",
                    "verification_summary": {},
                }
            },
        }
        args = SimpleNamespace(
            port=18932,
            identifier="task-1",
            task=None,
            action=None,
            var=None,
            labels=None,
            group=None,
            engineer="alice",
            at=None,
            depends_on=None,
            verify_mode=None,
            verify_state=None,
            verify_note=None,
            verify_tests=None,
            verify_smoke_done=False,
            verify_deploy_needed=False,
            verify_deploy_attempted=False,
            verify_human=None,
        )
        with mock.patch.object(self.cli, "get_state_local", return_value=state), \
             mock.patch.object(self.cli, "api_call", return_value={"ok": True, "data": {}}) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_task_edit(args)

        api_call.assert_called_once_with(
            "board_update_task",
            port=18932,
            id="task-1",
            assigned_engineer_id="eng-alice",
        )
        self.assertIn("Updated task ship-it", out.getvalue())

    def test_cmd_task_edit_rejects_cross_group_engineer_slug_using_updated_group(self):
        state = {
            "agents": {
                "eng-alice": {
                    "id": "eng-alice",
                    "name": "Alice",
                    "slug": "alice",
                    "group": "group-a",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            },
            "board_tasks": {
                "task-1": {
                    "id": "task-1",
                    "task": "Ship it",
                    "slug": "ship-it",
                    "group": "group-a",
                    "verification_summary": {},
                }
            },
        }
        args = SimpleNamespace(
            port=18932,
            identifier="task-1",
            task=None,
            action=None,
            var=None,
            labels=None,
            group="group-b",
            engineer="alice",
            at=None,
            depends_on=None,
            verify_mode=None,
            verify_state=None,
            verify_note=None,
            verify_tests=None,
            verify_smoke_done=False,
            verify_deploy_needed=False,
            verify_deploy_attempted=False,
            verify_human=None,
        )
        err = io.StringIO()
        with mock.patch.object(self.cli, "get_state_local", return_value=state), \
             mock.patch.object(self.cli, "api_call") as api_call, \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as cm:
            self.cli.cmd_task_edit(args)

        self.assertEqual(cm.exception.code, 1)
        self.assertIn(
            "engineer 'alice' belongs to group 'group-a', not 'group-b'",
            err.getvalue(),
        )
        api_call.assert_not_called()

    def test_cmd_task_edit_rejects_cross_group_move_for_existing_assignment(self):
        state = {
            "agents": {
                "eng-alice": {
                    "id": "eng-alice",
                    "name": "Alice",
                    "slug": "alice",
                    "group": "group-a",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            },
            "board_tasks": {
                "task-1": {
                    "id": "task-1",
                    "task": "Ship it",
                    "slug": "ship-it",
                    "group": "group-a",
                    "assigned_engineer_id": "eng-alice",
                    "verification_summary": {},
                }
            },
        }
        args = SimpleNamespace(
            port=18932,
            identifier="task-1",
            task=None,
            action=None,
            var=None,
            labels=None,
            group="group-b",
            engineer=None,
            at=None,
            depends_on=None,
            verify_mode=None,
            verify_state=None,
            verify_note=None,
            verify_tests=None,
            verify_smoke_done=False,
            verify_deploy_needed=False,
            verify_deploy_attempted=False,
            verify_human=None,
        )
        err = io.StringIO()
        with mock.patch.object(self.cli, "get_state_local", return_value=state), \
             mock.patch.object(self.cli, "api_call") as api_call, \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as cm:
            self.cli.cmd_task_edit(args)

        self.assertEqual(cm.exception.code, 1)
        self.assertIn(
            "cannot move assigned task to group 'group-b' while engineer 'alice' belongs to group 'group-a'; clear or reassign --engineer",
            err.getvalue(),
        )
        api_call.assert_not_called()

    def test_cmd_engineer_list_outputs_json_with_worker_and_task_counts(self):
        state = {
            "agents": {
                "eng-alice": {
                    "id": "eng-alice",
                    "name": "Alice",
                    "slug": "alice",
                    "kind": "engineer",
                    "cell_type": "agent",
                    "status": "running",
                },
                "worker-1": {
                    "id": "worker-1",
                    "name": "Worker 1",
                    "slug": "worker-1",
                    "kind": "worker",
                    "cell_type": "agent",
                    "owner_engineer_id": "eng-alice",
                },
                "eng-bob": {
                    "id": "eng-bob",
                    "name": "Bob",
                    "slug": "bob",
                    "kind": "engineer",
                    "cell_type": "agent",
                    "status": "stopped",
                },
            },
            "board_tasks": {
                "task-1": {
                    "id": "task-1",
                    "task": "Owned",
                    "assigned_engineer_id": "eng-alice",
                }
            },
        }
        with mock.patch.object(self.cli, "get_state_local", return_value=state):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_engineer_list(SimpleNamespace(port=18932, json=True))

        payload = json.loads(out.getvalue())
        self.assertEqual(len(payload["engineers"]), 2)
        self.assertEqual(payload["engineers"][0]["id"], "eng-alice")
        self.assertEqual(payload["engineers"][0]["worker_count"], 1)
        self.assertEqual(payload["engineers"][0]["task_count"], 1)
        self.assertEqual(payload["engineers"][1]["id"], "eng-bob")
        self.assertEqual(payload["engineers"][1]["worker_count"], 0)
        self.assertEqual(payload["engineers"][1]["task_count"], 0)


if __name__ == "__main__":
    unittest.main()
