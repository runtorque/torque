import contextlib
import importlib.util
import io
import json
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli_board_sync", str(path))
    spec = importlib.util.spec_from_loader("torque_cli_board_sync", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliBoardSyncTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_parser_accepts_board_sync_commands(self):
        parser = self.cli.build_parser()

        test_args = parser.parse_args(["board", "sync", "test", "-g", "torque"])
        self.assertEqual(test_args.command, "board")
        self.assertEqual(test_args.board_cmd, "sync")
        self.assertEqual(test_args.board_sync_cmd, "test")
        self.assertEqual(test_args.group, "torque")

        push_task_args = parser.parse_args(["board", "sync", "push", "task-1"])
        self.assertEqual(push_task_args.board_sync_cmd, "push")
        self.assertEqual(push_task_args.task, "task-1")

        push_group_args = parser.parse_args([
            "board", "sync", "push", "--group", "torque",
        ])
        self.assertEqual(push_group_args.group, "torque")

        pull_args = parser.parse_args([
            "board", "sync", "pull", "--preview", "task-1",
        ])
        self.assertEqual(pull_args.board_sync_cmd, "pull")
        self.assertTrue(pull_args.preview)
        self.assertEqual(pull_args.task, "task-1")

        group_settings_args = parser.parse_args([
            "group", "settings", "backend", "-s",
            "board_sync_provider=github",
            "board_sync_enabled=true",
            "board_sync_github={}",
        ])
        self.assertEqual(group_settings_args.set, [
            "board_sync_provider=github",
            "board_sync_enabled=true",
            "board_sync_github={}",
        ])

        json_pull_args = parser.parse_args([
            "--json", "board", "sync", "pull", "--preview", "--group", "torque",
        ])
        self.assertTrue(json_pull_args.json)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([
                "board", "sync", "pull", "--preview", "--group", "torque",
                "--json",
            ])

    def test_board_sync_test_calls_preflight(self):
        calls = []

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "ok": True,
                    "type": "board_sync_preflight",
                    "group": "torque",
                    "provider": "github",
                    "phase": "preflight",
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(port=18932, group="torque", json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_sync_test(args)

        self.assertEqual(calls, [("board_sync_preflight", {"group": "torque"})])
        self.assertIn("preflight passed", out.getvalue())

    def test_board_sync_push_task_resolves_task_and_calls_sync_task(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "Ship CLI sync"}
            },
            "task_id_aliases": {},
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "ok": True,
                    "type": "board_sync_task",
                    "queued": True,
                    "task_id": "task-1",
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(port=18932, task="task-1", group="", json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_sync_push(args)

        self.assertEqual(calls, [("board_sync_task", {"task": "task-1"})])
        self.assertIn("Queued board sync for task-1", out.getvalue())

    def test_board_sync_push_group_calls_sync_group(self):
        calls = []

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "ok": True,
                    "type": "board_sync_group",
                    "group": "torque",
                    "queued_count": 2,
                    "skipped_count": 0,
                    "queued": ["task-1", "task-2"],
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(port=18932, task="", group="torque", json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_sync_push(args)

        self.assertEqual(calls, [("board_sync_group", {"group": "torque"})])
        self.assertIn("2 task(s) in torque", out.getvalue())

    def test_board_sync_pull_task_calls_preview(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "Pull remote"}
            },
            "task_id_aliases": {},
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "ok": True,
                    "type": "board_pull_preview",
                    "task_id": "task-1",
                    "provider": "github",
                    "changes": {
                        "task": {"local": "Old", "remote": "New"},
                    },
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            preview=True,
            task="task-1",
            group="",
            json=False,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_sync_pull(args)

        self.assertEqual(calls, [("board_pull_preview", {"task": "task-1"})])
        self.assertIn("1 change(s)", out.getvalue())

    def test_board_sync_pull_group_previews_each_group_task(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "groups": {"torque": [], "other": []},
            "group_slugs": {},
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "One", "group": "torque"},
                "task-2": {"id": "task-2", "task": "Two", "group": "torque"},
                "task-3": {"id": "task-3", "task": "Three", "group": "other"},
            },
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "ok": True,
                    "type": "board_pull_preview",
                    "task_id": kwargs["task"],
                    "provider": "github",
                    "changes": {},
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            preview=True,
            task="",
            group="torque",
            json=True,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_sync_pull(args)

        self.assertEqual(
            calls,
            [
                ("board_pull_preview", {"task": "task-1"}),
                ("board_pull_preview", {"task": "task-2"}),
            ],
        )
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["type"], "board_pull_preview_group")
        self.assertEqual(payload["count"], 2)

    def test_board_sync_rejects_ambiguous_target(self):
        args = SimpleNamespace(port=18932, task="task-1", group="torque", json=False)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.cli.cmd_board_sync_push(args)
        self.assertIn("Provide exactly one target", err.getvalue())

    def test_board_sync_help_and_docs_round_trip(self):
        parser = self.cli.build_parser()
        help_out = io.StringIO()
        with contextlib.redirect_stdout(help_out), self.assertRaises(SystemExit):
            parser.parse_args(["board", "sync", "--help"])
        help_text = help_out.getvalue()
        for token in ("test", "push", "pull"):
            self.assertIn(token, help_text)

        docs = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "reference"
            / "cli.md"
        ).read_text()
        for command in (
            "torque board sync test -g backend",
            "torque board sync push fix-login",
            "torque board sync pull --preview fix-login",
            "torque --json board sync pull --preview --group backend",
        ):
            self.assertIn(command, docs)
        self.assertIn("torque group settings backend -s \\", docs)
        self.assertNotIn(
            "torque board sync pull --preview --group backend --json",
            docs,
        )
