import importlib
import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MCPToolsSharedArchitectTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.shared_mod = importlib.import_module("torque.mcp_tools_shared")
        self.shared_mod = importlib.reload(self.shared_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        state.groups["torque"] = []
        return state

    def _add_agent(self, state, agent_id, name, *, kind, hired_by_architect_id="", owner_engineer_id=""):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="torque",
            cell_type="agent",
            kind=kind,
            hired_by_architect_id=hired_by_architect_id,
            owner_engineer_id=owner_engineer_id,
            created_by_engineer_id=owner_engineer_id,
            status="running",
        )
        state.agents[cell.id] = cell
        state.groups["torque"].append(cell.id)
        return cell

    def _add_task(self, state, task_id, title, **kwargs):
        task = self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group="torque",
            lane="Backlog",
            **kwargs,
        )
        state.board_tasks[task.id] = task
        return task

    def test_filter_agents_for_architect_sees_self_and_hired_engineers_only(self):
        state = self._make_state()
        architect = self._add_agent(state, "arch-1", "Architect", kind="architect")
        hired = self._add_agent(
            state,
            "eng-hired",
            "Hired Engineer",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        self._add_agent(state, "eng-user", "User Engineer", kind="engineer")
        self._add_agent(
            state,
            "worker-1",
            "Worker One",
            kind="worker",
            owner_engineer_id=hired.id,
        )

        visible = self.shared_mod._filter_agents_for_caller(
            state,
            "architect",
            architect.id,
        )

        self.assertEqual(set(visible), {architect.id, hired.id})

    def test_filter_tasks_for_architect_sees_all_group_tasks(self):
        state = self._make_state()
        architect = self._add_agent(state, "arch-1", "Architect", kind="architect")
        hired = self._add_agent(
            state,
            "eng-hired",
            "Hired Engineer",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        user_engineer = self._add_agent(state, "eng-user", "User Engineer", kind="engineer")

        created = self._add_task(
            state,
            "task-created",
            "Created by architect",
            assigned_engineer_id=user_engineer.id,
            created_by_architect_id=architect.id,
        )
        hired_task = self._add_task(
            state,
            "task-hired",
            "Assigned to hired engineer",
            assigned_engineer_id=hired.id,
        )
        self._add_task(
            state,
            "task-user",
            "User task",
            assigned_engineer_id=user_engineer.id,
        )
        state.groups["other"] = []
        other_group = self.state_mod.BoardTask(
            id="task-other-group",
            task="Other group task",
            group="other",
            lane="Backlog",
        )
        state.board_tasks[other_group.id] = other_group

        visible = self.shared_mod._filter_tasks_for_caller(
            state,
            "architect",
            architect.id,
        )

        self.assertEqual(set(visible), {created.id, hired_task.id, "task-user"})

    def test_authorize_caller_accepts_architect(self):
        state = self._make_state()
        architect = self._add_agent(state, "arch-1", "Architect", kind="architect")

        cell, group, effective_kind, error_text, is_error = self.shared_mod.authorize_caller(
            state,
            caller_kind="architect",
            caller_id=architect.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(cell.id, architect.id)
        self.assertEqual(group, "torque")
        self.assertEqual(effective_kind, "architect")
        self.assertEqual(error_text, "")

    def test_architect_settings_tool_reads_existing_settings(self):
        state = self._make_state()
        architect = self._add_agent(
            state,
            "arch-1",
            "Architect",
            kind="architect",
        )
        state.update_architect_settings(
            "torque",
            architect_provider="codex",
            architect_autonomy_mode="dispatch_freely",
        )

        async def handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = asyncio.run(self.shared_mod.dispatch_scoped_tool(
            "architect_get_architect_settings",
            {},
            handle_command,
            state,
            tool_prefix="architect_",
            caller_kind="architect",
            caller_id=architect.id,
        ))

        self.assertFalse(is_error)
        self.assertEqual(
            json.loads(text)["settings"]["architect_provider"],
            "codex",
        )

    def test_removed_architect_settings_update_tool_is_unknown(self):
        state = self._make_state()
        architect = self._add_agent(
            state,
            "arch-1",
            "Architect",
            kind="architect",
        )

        async def handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = asyncio.run(self.shared_mod.dispatch_scoped_tool(
            "architect_update_architect_settings",
            {"architect_autonomy_mode": "ask_always"},
            handle_command,
            state,
            tool_prefix="architect_",
            caller_kind="architect",
            caller_id=architect.id,
        ))

        self.assertTrue(is_error)
        self.assertEqual(
            text,
            "Unknown architect tool: architect_update_architect_settings",
        )

    def test_architect_board_summary_json_trims_tasks_to_response_budget(self):
        summary = {
            "group": "torque",
            "streams": {
                "count": 1,
                "items": [{"summary": "stream-context " * 500}],
                "truncated": False,
            },
        }
        items = [
            {
                "id": f"task-{idx}",
                "slug": f"task-{idx}",
                "title": "Task " + ("x" * 110),
                "lane": "Backlog",
                "labels": [],
                "status": "",
                "assigned_engineer_id": "eng-1",
                "created_by": "architect:arch-1",
                "health_state": "healthy",
                "updated_at": "2026-04-21T00:00:00+00:00",
            }
            for idx in range(self.shared_mod._ARCHITECT_BOARD_SUMMARY_TASK_LIMIT)
        ]

        text = self.shared_mod._architect_board_summary_json(summary, items)

        self.assertLessEqual(
            len(text),
            self.shared_mod._ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT,
        )
        payload = json.loads(text)
        self.assertEqual(payload["tasks"]["count"], len(items))
        self.assertTrue(payload["tasks"]["truncated"])
        self.assertLess(
            len(payload["tasks"]["items"]),
            self.shared_mod._ARCHITECT_BOARD_SUMMARY_TASK_LIMIT,
        )

    def test_architect_deploy_state_counts_commits_and_extracts_task_ids(self):
        deploy_mod = importlib.import_module("torque.deploy_state")
        deploy_mod = importlib.reload(deploy_mod)
        state = self._make_state()
        state.boot_timestamp = 100.0
        state.boot_repo_root = "/repo"
        state.boot_head_commit = "boot-sha"
        state.boot_mainline_branch = "main"
        calls = []

        def fake_git(repo_root, *args):
            calls.append((repo_root, args))
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "main"
            if args == ("rev-parse", "HEAD"):
                return "current-sha"
            if args == ("rev-list", "--count", "boot-sha..main"):
                return "3"
            if args == ("log", "--format=%B", "--reverse", "boot-sha..main"):
                return (
                    "Merge TORQUE:101\n\n"
                    "Ship deploy state for TORQUE:102 and TORQUE:101\n"
                    "No task id here"
                )
            raise AssertionError(f"unexpected git call: {args}")

        with mock.patch.object(deploy_mod, "_run_git", side_effect=fake_git):
            payload = deploy_mod.architect_deploy_state_payload(
                state,
                "torque",
                now=160.0,
            )

        self.assertEqual(payload["boot_timestamp"], 100.0)
        self.assertEqual(payload["boot_head_commit"], "boot-sha")
        self.assertEqual(payload["current_head_commit"], "current-sha")
        self.assertEqual(payload["daemon_uptime_seconds"], 60)
        self.assertEqual(payload["pending_deploy"]["count"], 3)
        self.assertEqual(
            payload["pending_deploy"]["torque_task_ids"],
            ["TORQUE:101", "TORQUE:102"],
        )
        self.assertNotIn("error", payload)
        self.assertEqual(calls[0], ("/repo", ("rev-parse", "--abbrev-ref", "HEAD")))

    def test_architect_deploy_state_fails_gracefully_for_detached_head(self):
        deploy_mod = importlib.import_module("torque.deploy_state")
        deploy_mod = importlib.reload(deploy_mod)
        state = self._make_state()
        state.boot_timestamp = 100.0
        state.boot_repo_root = "/repo"
        state.boot_head_commit = "boot-sha"
        state.boot_mainline_branch = "main"

        def fake_git(_repo_root, *args):
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "HEAD"
            raise AssertionError(f"unexpected git call: {args}")

        with mock.patch.object(deploy_mod, "_run_git", side_effect=fake_git):
            payload = deploy_mod.architect_deploy_state_payload(
                state,
                "torque",
                now=160.0,
            )

        self.assertEqual(payload["pending_deploy"]["count"], -1)
        self.assertEqual(payload["pending_deploy"]["torque_task_ids"], [])
        self.assertIn("detached HEAD", payload["error"])

    def test_capture_deploy_boot_state_uses_installed_source_repo_metadata(self):
        deploy_mod = importlib.import_module("torque.deploy_state")
        deploy_mod = importlib.reload(deploy_mod)
        state = self._make_state()

        with tempfile.TemporaryDirectory() as tmp:
            script_dir = Path(tmp)
            (script_dir / ".torque_source_repo_root").write_text(
                "/repo\n",
                encoding="utf-8",
            )

            def fake_git(repo_root, *args):
                repo_root = str(repo_root)
                if repo_root == str(script_dir):
                    raise subprocess.CalledProcessError(
                        128,
                        ["git"],
                        stderr="fatal: not a git repository",
                    )
                if repo_root == "/repo" and args == (
                    "rev-parse",
                    "--show-toplevel",
                ):
                    return "/repo"
                if repo_root == "/repo" and args == ("rev-parse", "HEAD"):
                    return "boot-sha"
                if repo_root == "/repo" and args == (
                    "rev-parse",
                    "--abbrev-ref",
                    "HEAD",
                ):
                    return "main"
                raise AssertionError(f"unexpected git call: {repo_root} {args}")

            with mock.patch.object(deploy_mod, "_run_git", side_effect=fake_git):
                deploy_mod.capture_deploy_boot_state(state, script_dir)

        self.assertEqual(state.boot_repo_root, "/repo")
        self.assertEqual(state.boot_head_commit, "boot-sha")
        self.assertEqual(state.boot_mainline_branch, "main")
        self.assertEqual(state.boot_head_error, "")


class MCPToolsSharedMergePayloadTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.shared_mod = importlib.import_module("torque.mcp_tools_shared")
        self.shared_mod = importlib.reload(self.shared_mod)

    def _cell(self):
        return self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
        )

    def test_merge_payload_surfaces_cleanup_override_warning(self):
        # Part A (TORQUE:381 / :393): the silent cleanup override must reach the
        # MCP caller both as structured fields and as a human-readable WARNING.
        result = {
            "type": "worktree_merge",
            "ok": True,
            "sha": "abc123",
            "cleanup": {
                "close_agent": False,
                "remove_worktree": False,
                "agent_closed": False,
                "worktree_removed": False,
                "errors": [],
                "cleanup_overridden": True,
                "override_reason": "queued_followups",
                "queued_followup_count": 2,
            },
        }
        payload = self.shared_mod._worktree_merge_success_payload(
            result, self._cell()
        )
        # Structured fields ride along in cleanup.
        self.assertTrue(payload["cleanup"]["cleanup_overridden"])
        self.assertEqual(
            payload["cleanup"]["override_reason"], "queued_followups"
        )
        self.assertEqual(payload["cleanup"]["queued_followup_count"], 2)
        # Human-readable WARNING is surfaced in both warning + message.
        self.assertIn("WARNING", payload["warning"])
        self.assertIn("2 queued follow-up", payload["warning"])
        self.assertIn("WARNING", payload["message"])

    def test_merge_payload_no_warning_without_override(self):
        result = {
            "type": "worktree_merge",
            "ok": True,
            "sha": "abc123",
            "cleanup": {
                "close_agent": True,
                "remove_worktree": True,
                "agent_closed": True,
                "worktree_removed": True,
                "errors": [],
            },
        }
        payload = self.shared_mod._worktree_merge_success_payload(
            result, self._cell()
        )
        self.assertNotIn("cleanup_overridden", payload["cleanup"])
        self.assertNotIn("WARNING", payload.get("warning", ""))
        self.assertNotIn("WARNING", payload["message"])


if __name__ == "__main__":
    unittest.main()
