import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli", str(path))
    spec = importlib.util.spec_from_loader("torque_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliExternalTicketTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_resolve_repo_root_returns_common_root_for_linked_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            subprocess.run(
                ["git", "-C", str(repo_root), "init", "-b", "main"],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "user.name", "Torque Test"],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "user.email", "torque@example.com"],
                capture_output=True,
                text=True,
                check=True,
            )
            (repo_root / "README.md").write_text("hello\n")
            subprocess.run(
                ["git", "-C", str(repo_root), "add", "README.md"],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "commit", "-m", "Initial commit"],
                capture_output=True,
                text=True,
                check=True,
            )

            worktree_path = repo_root / ".torque" / "worktrees" / "worker"
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git", "-C", str(repo_root),
                    "worktree", "add", "-b", "torque/worker",
                    str(worktree_path), "main",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertEqual(
                self.cli.resolve_repo_root(str(worktree_path)),
                str(repo_root.resolve()),
            )

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

    def test_ai_done_forwards_force_skip_review_audit_reason(self):
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
            message="Mechanical refactor",
            post_external=False,
            force_skip_review=True,
            review_skip_reason="generated rename only",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.cli.cmd_ai_done(args)

        self.assertEqual(calls[0][0], "ai_report")
        self.assertTrue(calls[0][1]["force_skip_review"])
        self.assertEqual(
            calls[0][1]["review_skip_reason"],
            "generated rename only",
        )

    def test_ai_derive_prints_prior_reviewer_reuse_disclosure(self):
        self.cli._resolve_self_and_task = lambda _args: (
            {"id": "agent-1"},
            {"id": "task-1", "slug": "task-1"},
        )
        self.cli.api_call = lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "task_id": "task-review-2",
                "agent_id": "reviewer-1",
                "reviewer_reuse": {
                    "kind": "prior_reviewer_reuse",
                    "reviewer_id": "reviewer-1",
                    "prior_review_task_ids": [
                        "task-review-1",
                        "task-review-1b",
                    ],
                },
            },
        }
        args = SimpleNamespace(
            port=18932,
            description="Review the fix",
            context="",
            action="feature/review",
            var=[],
            group="",
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_ai_derive(args)

        rendered = out.getvalue()
        self.assertIn("Reviewer reuse: reviewer-1", rendered)
        self.assertIn("task-review-1, task-review-1b", rendered)
        self.assertIn("not a fresh reviewer", rendered)

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

    def test_task_verify_calls_board_verify_task(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "Deploy billing changes"}
            }
        }
        self.cli.resolve_task = lambda _state, _ident: {
            "id": "task-1", "task": "Deploy billing changes"
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "message": "Verification updated: state=failed",
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            identifier="task-1",
            mode="deploy",
            state=None,
            attempted=True,
            clear_attempted=False,
            smoke="failed",
            note="Smoke failed on login redirect",
            clear_note=False,
            tests="python3 -m unittest",
            clear_tests=False,
            deploy_needed=False,
            clear_deploy_needed=False,
            human="Confirm fixed build",
            clear_human=False,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_task_verify(args)

        self.assertEqual(calls[0][0], "board_verify_task")
        self.assertEqual(
            calls[0][1],
            {
                "id": "task-1",
                "actor_name": "torque-cli",
                "verification_mode": "deploy",
                "verification_notes": "Smoke failed on login redirect",
                "tests_run": "python3 -m unittest",
                "human_validation_pending": "Confirm fixed build",
                "deploy_attempted": True,
                "smoke_status": "failed",
            },
        )

    def test_task_verify_can_clear_stale_checkpoint_fields(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "Deploy billing changes"}
            }
        }
        self.cli.resolve_task = lambda _state, _ident: {
            "id": "task-1", "task": "Deploy billing changes"
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "message": "Verification updated: state=passed",
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            identifier="task-1",
            mode="clear",
            state="clear",
            attempted=False,
            clear_attempted=True,
            smoke="clear",
            note=None,
            clear_note=True,
            tests=None,
            clear_tests=True,
            deploy_needed=False,
            clear_deploy_needed=True,
            human=None,
            clear_human=True,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_task_verify(args)

        self.assertEqual(calls[0][0], "board_verify_task")
        self.assertEqual(
            calls[0][1],
            {
                "id": "task-1",
                "actor_name": "torque-cli",
                "verification_mode": "",
                "verification_state": "",
                "verification_notes": "",
                "tests_run": "",
                "human_validation_pending": "",
                "deploy_needed": False,
                "deploy_attempted": False,
                "manual_smoke_done": False,
            },
        )

    def test_workflow_breach_calls_manual_endpoint(self):
        calls = []
        self.cli.get_state_local = lambda _port: {
            "board_tasks": {
                "task-1": {"id": "task-1", "task": "Investigate residue"}
            }
        }
        self.cli.resolve_task = lambda _state, _ident: {
            "id": "task-1", "task": "Investigate residue"
        }

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "event": {
                        "subkind": "manual",
                        "task_id": "task-1",
                    },
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            subkind="manual",
            task="task-1",
            context="Reviewer worktree residue caught by Courier",
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_workflow_breach(args)

        self.assertEqual(
            calls[0],
            (
                "workflow_breach",
                {
                    "subkind": "manual",
                    "task_id": "task-1",
                    "context": "Reviewer worktree residue caught by Courier",
                    "source": "operator",
                },
            ),
        )
        self.assertIn("Workflow breach recorded: manual (task-1)", out.getvalue())

    def test_memory_show_calls_memory_read_endpoint(self):
        calls = []

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "entry": {
                        "id": "mem-1",
                        "entry_type": "decision",
                        "scope_kind": "task",
                        "scope_ref": "task-1",
                        "title": "Storage choice",
                        "content": "Use SQLite.",
                        "pinned": True,
                        "links": [
                            {
                                "entry_id": "mem-1",
                                "target_kind": "agent",
                                "target_ref": "agent-1",
                            }
                        ],
                    }
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            entry_id="mem-1",
            json=False,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_memory_show(args)

        self.assertEqual(calls[0], ("memory_read", {"entry_id": "mem-1"}))
        self.assertIn("Storage choice", out.getvalue())
        self.assertIn("agent:agent-1", out.getvalue())

    def test_memory_link_uses_current_cell_context(self):
        calls = []

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {"ok": True, "data": {"entry": {"id": "mem-1"}}}

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            entry_id="mem-1",
            kind="task",
            ref="task-1",
        )
        out = io.StringIO()
        prev = os.environ.get("TORQUE_CELL_ID")
        os.environ["TORQUE_CELL_ID"] = "agent-1"
        try:
            with contextlib.redirect_stdout(out):
                self.cli.cmd_memory_link(args)
        finally:
            if prev is None:
                os.environ.pop("TORQUE_CELL_ID", None)
            else:
                os.environ["TORQUE_CELL_ID"] = prev

        self.assertEqual(
            calls[0],
            (
                "memory_link",
                {
                    "entry_id": "mem-1",
                    "target_kind": "task",
                    "target_ref": "task-1",
                    "cell_id": "agent-1",
                },
            ),
        )

    def test_worktree_list_calls_api_with_resolved_repo_root(self):
        calls = []
        self.cli.resolve_repo_root = lambda ident="": "/repo"

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "repo_root": "/repo",
                    "items": [{
                        "branch": "torque/worker",
                        "path": "/repo/.torque/worktrees/worker",
                        "base_branch": "main",
                        "merged": True,
                        "dirty": False,
                        "admin_stale": False,
                        "prunable": True,
                        "prune_reason": "merged_clean",
                        "owner_agent_name": "",
                    }],
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(port=18932, repo="", json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_worktree_list(args)

        self.assertEqual(
            calls[0],
            ("worktree_list", {"repo_root": "/repo"}),
        )
        self.assertIn("torque/worker", out.getvalue())
        self.assertIn("prunable", out.getvalue())

    def test_worktree_prune_calls_api_with_resolved_repo_root(self):
        calls = []
        self.cli.resolve_repo_root = lambda ident="": "/repo"

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, kwargs))
            return {
                "ok": True,
                "data": {
                    "repo_root": "/repo",
                    "removed": [{
                        "branch": "torque/worker",
                        "path": "/repo/.torque/worktrees/worker",
                        "prune_reason": "merged_clean",
                    }],
                    "skipped": [],
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(port=18932, repo="", json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_worktree_prune(args)

        self.assertEqual(
            calls[0],
            ("worktree_prune", {"repo_root": "/repo"}),
        )
        self.assertIn("Removed:", out.getvalue())
        self.assertIn("torque/worker", out.getvalue())
