import contextlib
import importlib.util
import io
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "loom"
    loader = SourceFileLoader("loom_cli", str(path))
    spec = importlib.util.spec_from_loader("loom_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliExternalTicketTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_ai_done_forwards_post_external_flag(self):
        calls = []
        self.cli._resolve_self_and_task = lambda _args: (
            {"id": "agent-1"},
            {"id": "task-1", "slug": "task-1"},
        )

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {"ok": True, "data": {}}

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            message="Shipped",
            post_external=True,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_ai_done(args)
        self.assertEqual(calls[0][0], "ai_report")
        self.assertTrue(calls[0][1]["push_external"])

    def test_board_import_calls_external_import_endpoint(self):
        calls = []
        self.cli.get_state_local = lambda _port: {"groups": {"g": []}}
        self.cli.detect_context = lambda _state: (None, None, "g")

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {"ok": True, "data": {"task_id": "task-1"}}

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            ref="github:openai/example#1",
            group="",
            lane="Backlog",
            provider="github",
            title="",
            description="",
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_import(args)
        self.assertEqual(calls[0][0], "external_import_task")
        self.assertEqual(calls[0][1]["provider"], "github")

    def test_board_comment_posts_comment_body(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "Investigate"}
            }
        }
        self.cli.resolve_task = lambda _state, _ident: {
            "id": "task-1", "task": "Investigate"
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {"ok": True, "data": {"message": "Posted"}}

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            identifier="task-1",
            comment="Looks good",
            message="",
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_board_comment(args)
        self.assertEqual(calls[0][0], "external_post_task_comment")
        self.assertEqual(calls[0][1]["comment"], "Looks good")
