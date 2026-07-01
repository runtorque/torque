import importlib
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MCPScopingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("torque.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        state.groups["torque"] = []
        return state

    def _add_engineer(self, state, agent_id, name):
        engineer = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group="torque",
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
        )
        state.agents[agent_id] = engineer
        state.groups["torque"].append(agent_id)
        return engineer

    def _add_architect(self, state, agent_id, name):
        architect = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group="torque",
            cell_type="agent",
            kind="architect",
            status="idle",
            persistent=True,
        )
        state.agents[agent_id] = architect
        state.groups["torque"].append(agent_id)
        return architect

    def _add_worker(self, state, agent_id, name, owner_id):
        worker = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group="torque",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=owner_id,
            created_by_engineer_id=owner_id,
            status="idle",
        )
        state.agents[agent_id] = worker
        state.groups["torque"].append(agent_id)
        return worker

    def _add_task(self, state, task_id, title, *, group="torque",
                  assigned_engineer_id="", **kwargs):
        task = self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group=group,
            lane=kwargs.pop("lane", "Backlog"),
            assigned_engineer_id=assigned_engineer_id,
            **kwargs,
        )
        state.board_tasks[task.id] = task
        return task

    def _iso(self, ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    def _attach_stale_health_fixture(self, state, *, base_ts=1_700_000_000):
        engineer = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", engineer.id)
        worker.status = "idle"
        worker.last_event_at = base_ts
        worker.session_tokens_in = 0
        worker.session_tokens_out = 0
        task = self._add_task(
            state,
            "task-stale",
            "Silent task",
            assigned_engineer_id=engineer.id,
        )
        task.lane = "In Progress"
        task.agent_id = worker.id
        worker.current_task_id = task.id
        task.health_state = "idle-risk"
        task.health_details = {
            "aggregate": False,
            "source_task_id": task.id,
            "last_activity_at": self._iso(base_ts),
            "reasons": ["progress_silence_warning"],
            "agent_last_event_at": self._iso(base_ts),
            "silence_secs": 42,
        }
        return engineer, worker, task, base_ts

    async def test_engineer_agents_list_scopes_to_single_engineer(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        alice_worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        self._add_worker(state, "worker-orphan", "Orphan Worker", "")

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(
            [item["id"] for item in data["agents"]],
            [alice.id, alice_worker.id],
        )

    async def test_engineer_mcp_calls_dispatches_scoped_query(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        bob = self._add_engineer(state, "eng-bob", "Bob")
        bob_worker = self._add_worker(state, "worker-b", "Bob Worker", bob.id)
        calls = []

        async def fake_handle_command(payload):
            calls.append(payload)
            self.assertEqual(payload["cmd"], "engineer_mcp_calls")
            self.assertEqual(payload["caller_id"], alice.id)
            return {"type": "mcp_calls", "calls": [{"cell_id": worker.id}]}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_mcp_calls",
            {
                "agent_id": worker.id,
                "tool_name_pattern": "mcp__torque__%",
                "since": 123,
                "limit": 7,
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["calls"], [{"cell_id": worker.id}])
        self.assertEqual(calls[-1]["agent_id"], worker.id)
        self.assertEqual(calls[-1]["tool_name_pattern"], "mcp__torque__%")
        self.assertEqual(calls[-1]["since"], 123)
        self.assertEqual(calls[-1]["limit"], 7)

        hidden_text, hidden_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_mcp_calls",
            {"agent_id": bob_worker.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )
        self.assertTrue(hidden_error)
        self.assertEqual(hidden_text, "agent not found in scope")

    async def test_engineer_agents_list_scopes_between_two_engineers(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        alice_worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        bob_worker = self._add_worker(state, "worker-b", "Bob Worker", bob.id)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        alice_text, alice_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )
        bob_text, bob_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=bob.id,
        )

        self.assertFalse(alice_error, alice_text)
        self.assertFalse(bob_error, bob_text)
        self.assertEqual(
            {item["id"] for item in json.loads(alice_text)["agents"]},
            {alice.id, alice_worker.id},
        )
        self.assertEqual(
            {item["id"] for item in json.loads(bob_text)["agents"]},
            {bob.id, bob_worker.id},
        )

    async def test_engineer_diff_summary_prefixes_stale_base_warning(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        warning = (
            "⚠ STALE BASE: torque/alice/worker-a forks from 11111111 "
            "(Old base)."
        )
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "worktree_diff")
            return {
                "type": "ok",
                "stale_base_warning": warning,
                "summary": {
                    "agent_name": worker.name,
                    "branch": worker.worktree_branch,
                    "base_branch": "main",
                    "stats": {"files": 1, "insertions": 2, "deletions": 0},
                    "files": [],
                },
            }

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_diff",
            {"agent": worker.id, "summary_only": True},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertTrue(text.startswith("⚠ STALE BASE"), text)
        self.assertIn('"summary"', text)
        self.assertEqual(calls[0]["summary_only"], True)

    async def test_engineer_worktree_checkpoint_allows_reviewer_shared_snapshot(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        implementer = self._add_worker(
            state,
            "worker-impl",
            "Implementer",
            alice.id,
        )
        reviewer = self._add_worker(
            state,
            "worker-review",
            "Reviewer",
            alice.id,
        )
        for worker in (implementer, reviewer):
            worker.worktree_path = "/tmp/shared-review-worktree"
            worker.worktree_branch = "torque/shared-review"
            worker.worktree_base_branch = "main"
            worker.worktree_repo_root = "/tmp/repo"
        root = self._add_task(
            state,
            "TORQUE:1",
            "Implement feature",
            assigned_engineer_id=alice.id,
        )
        root.lane = "In Progress"
        root.agent_id = implementer.id
        root.action_name = "feature/implement"
        review = self._add_task(
            state,
            "TORQUE:1:review",
            "Review feature",
            assigned_engineer_id=alice.id,
        )
        review.lane = "In Progress"
        review.parent_task_id = root.id
        review.pipeline_root_id = root.id
        review.pipeline_depth = 1
        review.agent_id = reviewer.id
        review.action_name = "feature/review"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "worktree_checkpoint")
            # A reviewer shares the implementer's worktree, so the checkpoint
            # command intentionally snapshots the selected reviewer's shared
            # branch context instead of rejecting reviewer targets.
            self.assertEqual(payload["id"], reviewer.id)
            return {"type": "ok"}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_worktree_checkpoint",
            {"agent": reviewer.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["type"], "ok")
        self.assertEqual(calls, [{"cmd": "worktree_checkpoint",
                                  "id": reviewer.id}])

    async def test_engineer_worktree_remove_reports_verified_failure(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "worktree_remove")
            return {
                "type": "error",
                "message": (
                    "Worktree removal did not take; path or git worktree "
                    "entry is still present"
                ),
                "worktree_remove": {
                    "type": "worktree_remove",
                    "id": worker.id,
                    "ok": False,
                    "worktree_removed": False,
                    "message": (
                        "Worktree removal did not take; path or git "
                        "worktree entry is still present"
                    ),
                    "mismatches": ["reported_removed_but_present"],
                    "post_state": {
                        "path_exists": True,
                        "worktree_listed": True,
                    },
                },
            }

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_worktree_remove",
            {"agent": worker.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "error")
        self.assertIn("did not take", payload["message"])
        self.assertEqual(
            payload["worktree_remove"]["mismatches"],
            ["reported_removed_but_present"],
        )
        self.assertEqual(
            payload["worktree_remove"]["post_state"],
            {"path_exists": True, "worktree_listed": True},
        )
        self.assertEqual(calls, [{"cmd": "worktree_remove", "id": worker.id}])

    async def test_engineer_merge_refuses_stale_base_unless_forced(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        warning = (
            "⚠ STALE BASE: torque/alice/worker-a forks from 11111111 "
            "(Old base)."
        )
        calls = []

        async def blocked_handle_command(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "worktree_check_merge")
            return {
                "type": "worktree_check_merge",
                "id": worker.id,
                "clean": False,
                "conflicts": [],
                "error": warning,
                "stale_base": {"stale": True},
                "stale_base_warning": warning,
            }

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id},
            blocked_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertIn("STALE BASE", text)
        self.assertEqual([call["cmd"] for call in calls], ["worktree_check_merge"])

        forced_calls = []

        async def forced_handle_command(payload):
            forced_calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                self.assertTrue(payload.get("allow_stale_base"))
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                    "stale_base": {"stale": True},
                    "stale_base_warning": warning,
                }
            if payload["cmd"] == "worktree_merge":
                self.assertTrue(payload.get("force_stale_base"))
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        forced_text, forced_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id, "force_stale_base": True},
            forced_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(forced_error, forced_text)
        self.assertEqual(json.loads(forced_text)["sha"], "abc123")
        self.assertEqual(
            [call["cmd"] for call in forced_calls],
            ["worktree_check_merge", "worktree_merge"],
        )

        force_alias_calls = []

        async def force_alias_handle_command(payload):
            force_alias_calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                self.assertTrue(payload.get("allow_stale_base"))
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                    "stale_base": {"stale": True},
                    "stale_base_warning": warning,
                }
            if payload["cmd"] == "worktree_merge":
                self.assertTrue(payload.get("force"))
                self.assertTrue(payload.get("force_stale_base"))
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "sha": "def456",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        alias_text, alias_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id, "force": True},
            force_alias_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(alias_error, alias_text)
        self.assertEqual(json.loads(alias_text)["sha"], "def456")
        self.assertEqual(
            [call["cmd"] for call in force_alias_calls],
            ["worktree_check_merge", "worktree_merge"],
        )

    async def test_engineer_merge_driverless_boundary_error_not_phantom_conflict(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        calls = []
        boundary_error = (
            "Branch advanced 2 commit(s) past the last reviewed boundary "
            "abcdef123456 — re-review the new commits or record a reviewed "
            "boundary at the tip."
        )

        async def handle_command(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "worktree_check_merge")
            return {
                "type": "worktree_check_merge",
                "id": "driverless:torque/alice/worker-a",
                "clean": False,
                "dirty": False,
                "conflicts": [],
                "error": boundary_error,
            }

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {
                "worktree_path": "/tmp/worker-a",
                "branch": "torque/alice/worker-a",
                "base_branch": "main",
            },
            handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Branch advanced 2 commit(s)", text)
        self.assertNotIn("Merge has conflicts", text)
        self.assertNotIn("no file details", text.lower())
        self.assertEqual([call["cmd"] for call in calls], ["worktree_check_merge"])

    async def test_engineer_merge_passes_boundary_mismatch_override_and_actor(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                self.assertTrue(payload.get("allow_boundary_mismatch"))
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                    "boundary_mismatch_override": True,
                }
            if payload["cmd"] == "worktree_merge":
                self.assertTrue(payload.get("force_boundary_mismatch"))
                self.assertEqual(
                    payload.get("boundary_mismatch_reason"),
                    "reviewer verified ground truth",
                )
                self.assertEqual(payload.get("actor_agent_id"), alice.id)
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {
                "agent": worker.id,
                "force_boundary_mismatch": True,
                "boundary_mismatch_reason": "reviewer verified ground truth",
            },
            handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["sha"], "abc123")
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )

    async def test_engineer_merge_returns_pr_pending_success_shape(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                self.assertNotIn("force_direct", payload)
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "mode": "pull_request",
                    "pending": True,
                    "merged": False,
                    "pr_url": "https://github.com/acme/repo/pull/7",
                    "url": "https://github.com/acme/repo/pull/7",
                    "pr": {
                        "url": "https://github.com/acme/repo/pull/7",
                        "number": 7,
                        "status": "pending",
                    },
                    "auto_force_push": True,
                    "push": {
                        "auto_force_push": True,
                        "force_with_lease": True,
                        "reason": "remote_merged_to_base",
                    },
                    "message": "Pull request is open with auto-merge pending.",
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "ok")
        self.assertEqual(payload["mode"], "pull_request")
        self.assertEqual(payload["pr_url"], "https://github.com/acme/repo/pull/7")
        self.assertTrue(payload["pending"])
        self.assertFalse(payload["merged"])
        self.assertEqual(payload["sha"], "")
        self.assertEqual(payload["pr"]["number"], 7)
        self.assertTrue(payload["auto_force_push"])
        self.assertEqual(payload["push"]["reason"], "remote_merged_to_base")
        self.assertTrue(payload["push"]["force_with_lease"])
        self.assertIn("merge_report_snippet", payload)
        self.assertIn("https://github.com/acme/repo/pull/7",
                      payload["merge_report_snippet"])
        self.assertIn("torque/alice/worker-a", payload["merge_report_snippet"])
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )

    async def test_engineer_merge_cleanup_removed_agent_still_returns_success(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                self.assertTrue(payload.get("close_agent_on_merge"))
                self.assertTrue(payload.get("remove_worktree_on_merge"))
                # Simulate post-merge cleanup racing the response path: the
                # successful merge already happened, then cleanup tombstoned
                # the agent and removed its worktree metadata.
                state.remove_agent(worker.id)
                worker.worktree_path = ""
                worker.worktree_branch = ""
                worker.worktree_base_branch = ""
                worker.worktree_repo_root = ""
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "mode": "pull_request",
                    "pending": False,
                    "merged": True,
                    "sha": "squash789",
                    "cleanup": {
                        "close_agent": True,
                        "remove_worktree": True,
                        "agent_closed": True,
                        "worktree_removed": True,
                        "errors": [],
                    },
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {
                "agent": worker.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "ok")
        self.assertEqual(payload["sha"], "squash789")
        self.assertEqual(
            payload["message"],
            "Squash-merged torque/alice/worker-a into main",
        )
        self.assertTrue(payload["cleanup"]["agent_closed"])
        self.assertTrue(payload["cleanup"]["worktree_removed"])
        self.assertNotIn("No worktree or base branch", text)
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )

    async def test_engineer_merge_tombstoned_post_success_retry_uses_guard_not_blind_rebase(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        worker.worktree_repo_root = "/repo"
        task = state.board_add_task(
            "Merged worker",
            "torque",
            lane="Done",
            id="TORQUE:777",
            agent_id=worker.id,
        )
        task.worktree_boundary = {
            "version": "1",
            "repo_root": worker.worktree_repo_root,
            "branch": worker.worktree_branch,
            "base_branch": worker.worktree_base_branch,
            "commit_sha": "head123",
            "kind": "marker",
            "status": "merged",
            "recorded_at": "2026-05-29T18:00:00+00:00",
            "recorded_by_agent_id": worker.id,
            "message": "",
            "superseded_by_task_id": "",
            "merged_at": "2026-05-29T18:05:00+00:00",
            "merge_commit_sha": "squash789",
            "pr": {
                "provider": "github",
                "remote": "origin",
                "base_branch": worker.worktree_base_branch,
                "head_branch": worker.worktree_branch,
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "state": "merged",
                "merge_commit_sha": "squash789",
                "requested_cleanup": {
                    "close_agent_on_merge": True,
                    "remove_worktree_on_merge": True,
                },
            },
        }
        state.remove_agent(worker.id)
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "mode": "pull_request",
                    "pending": False,
                    "merged": True,
                    "sha": "squash789",
                    "branch": "torque/alice/worker-a",
                    "base_branch": "main",
                    "pr_url": "https://github.com/acme/repo/pull/7",
                    "warning": (
                        "Merge landed (PR is MERGED and main is at merge "
                        "commit squash789); ignoring post-success "
                        "target_resolution failure: Agent/worktree is tombstoned"
                    ),
                    "cleanup": {
                        "close_agent": True,
                        "remove_worktree": True,
                        "agent_closed": True,
                        "worktree_removed": True,
                        "errors": [],
                    },
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {
                "agent": worker.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "ok")
        self.assertEqual(payload["sha"], "squash789")
        self.assertTrue(payload["merged"])
        self.assertIn("ignoring post-success target_resolution", payload["warning"])
        self.assertEqual([call["cmd"] for call in calls], ["worktree_merge"])
        self.assertNotIn("rebase", text.lower())

    async def test_engineer_merge_landed_pr_cleanup_errors_are_warnings(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "mode": "pull_request",
                    "pending": False,
                    "merged": True,
                    "sha": "squash789",
                    "cleanup": {
                        "close_agent": True,
                        "remove_worktree": True,
                        "agent_closed": True,
                        "worktree_removed": True,
                        "errors": [
                            "github_preflight: Not a GitHub repository: "
                            "No such file or directory: .torque/worktrees/worker-a",
                        ],
                    },
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {
                "agent": worker.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "ok")
        self.assertEqual(payload["sha"], "squash789")
        self.assertTrue(payload["merged"])
        self.assertIn("post-merge cleanup reported warnings", payload["warning"])
        self.assertIn("github_preflight", payload["warning"])
        self.assertEqual(
            payload["cleanup"]["errors"],
            [
                "github_preflight: Not a GitHub repository: "
                "No such file or directory: .torque/worktrees/worker-a",
            ],
        )
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )

    async def test_engineer_merge_passes_pr_title_body_to_worktree_merge(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                self.assertEqual(
                    payload["pr_title"],
                    "Document engineer-authored PR metadata",
                )
                self.assertEqual(
                    payload["pr_body"],
                    "Covers TORQUE:497 and records test coverage.",
                )
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {
                "agent": worker.id,
                "pr_title": "Document engineer-authored PR metadata",
                "pr_body": "Covers TORQUE:497 and records test coverage.",
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["sha"], "abc123")
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )

    async def test_engineer_merge_pr_error_includes_phase_and_url(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"

        async def fake_handle_command(payload):
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": False,
                    "mode": "pull_request",
                    "phase": "pr_merge",
                    "pr_url": "https://github.com/acme/repo/pull/7",
                    "error": "GitHub reported the PR is not mergeable.",
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertIn("not mergeable", text)
        self.assertIn("phase=pr_merge", text)
        self.assertIn("pr_url=https://github.com/acme/repo/pull/7", text)
        self.assertIn("retryable=false", text)

    async def test_engineer_rebase_allows_stale_base_precheck_to_clear_gate(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        worker.worktree_path = "/tmp/worker-a"
        worker.worktree_branch = "torque/alice/worker-a"
        worker.worktree_base_branch = "main"
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                if len([c for c in calls if c["cmd"] == "worktree_check_merge"]) == 1:
                    self.assertTrue(payload.get("allow_stale_base"))
                    return {
                        "type": "worktree_check_merge",
                        "id": worker.id,
                        "clean": True,
                        "conflicts": [],
                        "stale_base": {"stale": True},
                        "stale_base_warning": "⚠ STALE BASE",
                    }
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                    "default_message": "Merge after rebase",
                }
            if payload["cmd"] == "worktree_rebase":
                return {"type": "worktree_rebase", "id": worker.id, "ok": True}
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_rebase",
            {"agent": worker.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertTrue(payload["merge_ready"])
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_rebase", "worktree_check_merge"],
        )

    async def test_engineer_task_create_stamps_assigned_engineer(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "board_task_added", "task_id": "TORQUE:1"}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_create",
            {
                "title": "Investigate regression",
                "description": "repro and fix",
                "assigned_engineer_id": "eng-bob",
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(calls[0]["assigned_engineer_id"], alice.id)
        self.assertEqual(calls[0]["created_by_engineer_id"], alice.id)
        self.assertEqual(json.loads(text)["task_id"], "TORQUE:1")

    async def test_engineer_task_create_forces_caller_group(self):
        state = self._make_state()
        state.groups["other"] = []
        alice = self._add_engineer(state, "eng-alice", "Alice")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "board_task_added", "task_id": "TORQUE:2"}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_create",
            {
                "title": "Cross-group attempt",
                "group": "other",
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(calls[0]["group"], "torque")
        self.assertEqual(calls[0]["assigned_engineer_id"], alice.id)
        self.assertEqual(calls[0]["created_by_engineer_id"], alice.id)

    async def test_engineer_task_edit_rejects_unassigned_task_in_other_group(self):
        state = self._make_state()
        state.groups["other"] = []
        alice = self._add_engineer(state, "eng-alice", "Alice")
        self._add_task(state, "task-b", "Other group task", group="other")

        async def fake_handle_command(_payload):
            self.fail("cross-group task should be rejected before mutation")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_edit",
            {"task": "task-b", "title": "Hacked"},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task not found")

    async def test_engineer_task_mark_covered_records_and_moves_owned_task(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        covered = self._add_task(
            state,
            "TORQUE:833",
            "Covered triage card",
            lane="To Do",
            status="Needs triage",
            assigned_engineer_id=alice.id,
        )
        covering = self._add_task(
            state,
            "TORQUE:855",
            "Implementation task",
            assigned_engineer_id=alice.id,
        )
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] != "board_mark_task_covered":
                self.fail(f"Unexpected command: {payload}")
            try:
                return state.board_mark_task_covered(
                    payload["id"],
                    covering_task_id=payload.get("covering_task_id", ""),
                    pr_url=payload.get("pr_url", ""),
                    sha=payload.get("sha", ""),
                    tests_run=payload.get("tests_run", ""),
                    evidence=payload.get("evidence", ""),
                    notes=payload.get("notes", ""),
                    actor_name=payload.get("actor_name", ""),
                    actor_id=payload.get("actor_id", ""),
                    actor_kind=payload.get("actor_kind", ""),
                    move_to_done=payload.get("move_to_done", False),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_mark_covered",
            {
                "task": covered.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/999",
                "sha": "abc1234",
                "tests_run": "pytest tests/test_mcp_scoping.py -k covered",
                "move_to_done": True,
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "task_marked_covered")
        self.assertEqual(calls[-1]["actor_id"], alice.id)
        self.assertEqual(calls[-1]["actor_kind"], "engineer")
        refreshed = state.board_tasks[covered.id]
        self.assertEqual(refreshed.lane, "Done")
        self.assertEqual(refreshed.status, "")
        self.assertEqual(refreshed.completion_evidence["covered_by"]["task_id"], covering.id)
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by")

    async def test_engineer_task_mark_covered_rejects_unassigned_task(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        task = self._add_task(state, "TORQUE:834", "Unassigned triage card")

        async def fake_handle_command(_payload):
            self.fail("unassigned coverage close should be rejected before mutation")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_mark_covered",
            {"task": task.id, "pr_url": "https://github.com/runtorque/torque/pull/1"},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "task not found in scope")
        self.assertEqual(state.board_tasks[task.id].lane, "Backlog")
        self.assertEqual(state.board_tasks[task.id].completion_evidence, {})

    async def test_engineer_task_reassign_transfers_task_between_board_summaries(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        task = self._add_task(
            state,
            "task-owned",
            "Owned task",
            assigned_engineer_id=alice.id,
            created_by_engineer_id=alice.id,
        )

        async def fake_handle_command(_payload):
            self.fail("reassign should update state directly")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_reassign",
            {"task": task.id, "new_engineer_id": bob.slug},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(
            json.loads(text),
            {
                "type": "ok",
                "task_id": task.id,
                "from": alice.id,
                "to": bob.id,
            },
        )
        self.assertEqual(state.board_tasks[task.id].assigned_engineer_id, bob.id)

        alice_summary_text, alice_summary_error = (
            await self.mcp_engineer_mod._dispatch_engineer_tool(
                "engineer_board_summary",
                {},
                fake_handle_command,
                state,
                caller_id=alice.id,
            )
        )
        bob_summary_text, bob_summary_error = (
            await self.mcp_engineer_mod._dispatch_engineer_tool(
                "engineer_board_summary",
                {},
                fake_handle_command,
                state,
                caller_id=bob.id,
            )
        )

        self.assertFalse(alice_summary_error, alice_summary_text)
        self.assertFalse(bob_summary_error, bob_summary_text)
        self.assertEqual(json.loads(alice_summary_text)["tasks_total"], 0)
        self.assertEqual(json.loads(bob_summary_text)["tasks_total"], 1)

    async def test_engineer_task_reassign_allows_creator_to_assign_unowned_task(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        task = self._add_task(
            state,
            "task-unassigned",
            "Unassigned task",
            created_by_engineer_id=alice.id,
        )

        async def fake_handle_command(_payload):
            self.fail("reassign should update state directly")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_reassign",
            {"task": task.id, "new_engineer_id": bob.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(
            json.loads(text),
            {
                "type": "ok",
                "task_id": task.id,
                "from": "",
                "to": bob.id,
            },
        )
        self.assertEqual(state.board_tasks[task.id].assigned_engineer_id, bob.id)

    async def test_engineer_task_reassign_rejects_non_owner(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        charlie = self._add_engineer(state, "eng-charlie", "Charlie")
        task = self._add_task(
            state,
            "task-owned",
            "Owned task",
            assigned_engineer_id=alice.id,
            created_by_engineer_id=bob.id,
        )

        async def fake_handle_command(_payload):
            self.fail("reassign should be rejected before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_reassign",
            {"task": task.id, "new_engineer_id": charlie.id},
            fake_handle_command,
            state,
            caller_id=charlie.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(
            text,
            "Task can only be reassigned by the assigned engineer or creator",
        )
        self.assertEqual(state.board_tasks[task.id].assigned_engineer_id, alice.id)

    async def test_engineer_agent_message_rejects_out_of_scope_target(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        self._add_worker(state, "worker-b", "Bob Worker", bob.id)

        async def fake_handle_command(_payload):
            self.fail("out-of-scope target should be rejected before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agent_message",
            {"agent": "worker-b", "message": "hello"},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "agent not found in scope")

    async def test_engineer_task_dispatch_rejects_architect_target(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        architect = self._add_architect(state, "arch-catalyst", "Catalyst")
        architect.effective_agent_class_snapshot = {
            "id": "creative-architect",
            "base_kind": "architect",
        }
        task = self._add_task(
            state,
            "task-review",
            "Review through invalid architect target",
            assigned_engineer_id=alice.id,
        )

        async def fake_handle_command(_payload):
            self.fail("architect-targeted task dispatch should be denied before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_dispatch",
            {"task": task.id, "agent": architect.slug},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertIn(
            "Engineer-originated executable task routing to Architect targets/classes is denied",
            text,
        )
        self.assertEqual(state.board_tasks[task.id].agent_id, "")
        self.assertEqual(state.board_tasks[task.id].lane, "Backlog")

    async def test_engineer_task_dispatch_rejects_architect_class_target(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        target = self._add_worker(state, "worker-class", "Class Target", alice.id)
        target.effective_agent_class_snapshot = {
            "id": "creative-architect",
            "base_kind": "architect",
        }
        task = self._add_task(
            state,
            "task-class",
            "Invalid architect-class target",
            assigned_engineer_id=alice.id,
        )

        async def fake_handle_command(_payload):
            self.fail("architect-class task dispatch should be denied before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_dispatch",
            {"task": task.id, "agent": target.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Architect targets/classes is denied", text)
        self.assertEqual(state.board_tasks[task.id].agent_id, "")

    async def test_engineer_agent_message_still_allows_visible_architect(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        architect = self._add_architect(state, "arch-catalyst", "Catalyst")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {
                "type": "queued",
                "agent_id": payload["agent_id"],
                "message": payload["message"],
            }

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agent_message",
            {"agent": architect.slug, "message": "status only"},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(calls[0]["cmd"], "engineer_message")
        self.assertEqual(calls[0]["agent_id"], architect.id)
        self.assertEqual(json.loads(text)["agent_id"], architect.id)

    async def test_engineer_board_summary_excludes_unassigned_tasks(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        self._add_task(
            state,
            "task-owned",
            "Owned task",
            assigned_engineer_id=alice.id,
        )
        self._add_task(state, "task-unassigned", "Unassigned task")

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_board_summary",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["tasks_total"], 1)
        self.assertEqual(data["lanes"]["Backlog"], 1)

    async def test_engineer_board_summary_excludes_engineer_message_followups(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        self._add_task(
            state,
            "task-owned",
            "Owned task",
            assigned_engineer_id=alice.id,
        )
        self._add_task(
            state,
            "task-reply",
            "Engineer: Need status",
            assigned_engineer_id=alice.id,
            labels=["torque:engineer-message"],
            status="Awaiting Reply",
        )

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_board_summary",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["tasks_total"], 1)
        self.assertEqual(data["lanes"]["Backlog"], 1)
        self.assertEqual(data["pending_message_followups"], 1)

    async def test_engineer_board_reads_include_board_sync_state(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        task = self._add_task(
            state,
            "task-synced",
            "Synced task",
            assigned_engineer_id=alice.id,
            board_sync={
                "provider": "github",
                "sync_state": "error",
                "last_error": "project item missing",
                "github": {"project_item_id": "hidden-from-compact-state"},
            },
        )

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        summary_text, summary_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_board_summary",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )
        self.assertFalse(summary_error, summary_text)
        summary = json.loads(summary_text)
        self.assertEqual(summary["board_sync"]["count"], 1)
        self.assertEqual(summary["board_sync"]["error_count"], 1)
        self.assertEqual(
            summary["board_sync"]["items"][0],
            {
                "id": task.id,
                "title": "Synced task",
                "provider": "github",
                "sync_state": "error",
                "last_error": "project item missing",
            },
        )

        show_text, show_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_show",
            {"task": task.id},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )
        self.assertFalse(show_error, show_text)
        self.assertEqual(
            json.loads(show_text)["board_sync"],
            {
                "provider": "github",
                "sync_state": "error",
                "last_error": "project item missing",
            },
        )

        list_text, list_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_board_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )
        self.assertFalse(list_error, list_text)
        listed = json.loads(list_text)["lanes"]["Backlog"][0]
        self.assertEqual(listed["board_sync"]["provider"], "github")
        self.assertNotIn("github", listed["board_sync"])

    async def test_engineer_task_show_refreshes_silence_secs_at_read_time(self):
        state = self._make_state()
        engineer, _worker, task, base_ts = self._attach_stale_health_fixture(state)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        with mock.patch("torque.mcp_tools_shared.time.time", return_value=base_ts + 600):
            text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                "engineer_task_show",
                {"task": task.id},
                fake_handle_command,
                state,
                caller_id=engineer.id,
            )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["health_details"]["silence_secs"], 600)
        self.assertEqual(data["health_state"], "idle-risk")
        self.assertIn("Silent 10 min", data["health_summary"])
        self.assertIn("status=idle", data["health_summary"])
        self.assertLessEqual(len(data["health_summary"]), 120)

        with mock.patch("torque.mcp_tools_shared.time.time", return_value=base_ts + 900):
            text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                "engineer_task_show",
                {"task": task.id},
                fake_handle_command,
                state,
                caller_id=engineer.id,
            )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["health_details"]["silence_secs"], 900)

    async def test_engineer_agent_show_promotes_current_task_health(self):
        state = self._make_state()
        engineer, worker, _task, base_ts = self._attach_stale_health_fixture(state)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        with mock.patch("torque.mcp_tools_shared.time.time", return_value=base_ts + 900):
            text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                "engineer_agent_show",
                {"agent": worker.id},
                fake_handle_command,
                state,
                caller_id=engineer.id,
            )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["health_state"], "idle-risk")
        self.assertEqual(data["health_details"]["silence_secs"], 900)
        self.assertIn("Silent 15 min", data["health_summary"])
        self.assertIn("status=idle", data["health_summary"])
        self.assertLessEqual(len(data["health_summary"]), 120)

    async def test_engineer_task_show_healthy_summary_is_not_alarming(self):
        state = self._make_state()
        engineer = self._add_engineer(state, "eng-alice", "Alice")
        worker = self._add_worker(state, "worker-a", "Alice Worker", engineer.id)
        base_ts = 1_700_000_000
        worker.status = "running"
        worker.last_event_at = base_ts
        task = self._add_task(
            state,
            "task-healthy",
            "Recent task",
            assigned_engineer_id=engineer.id,
        )
        task.lane = "In Progress"
        task.agent_id = worker.id
        task.health_state = "healthy"
        task.health_details = {
            "aggregate": False,
            "source_task_id": task.id,
            "last_activity_at": self._iso(base_ts),
            "reasons": ["recent_activity"],
            "agent_last_event_at": self._iso(base_ts),
            "silence_secs": 3,
        }

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        with mock.patch("torque.mcp_tools_shared.time.time", return_value=base_ts + 60):
            text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                "engineer_task_show",
                {"task": task.id},
                fake_handle_command,
                state,
                caller_id=engineer.id,
            )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["health_state"], "healthy")
        self.assertEqual(data["health_details"]["silence_secs"], 60)
        self.assertNotIn("Silent", data["health_summary"])

    async def test_engineer_read_overviews_still_parse_with_health_payloads(self):
        state = self._make_state()
        engineer, _worker, _task, _base_ts = self._attach_stale_health_fixture(state)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        for tool_name in (
            "engineer_board_summary",
            "engineer_session_map",
            "engineer_streams_list",
        ):
            with self.subTest(tool_name=tool_name):
                text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                    tool_name,
                    {},
                    fake_handle_command,
                    state,
                    caller_id=engineer.id,
                )

                self.assertFalse(is_error, text)
                json.loads(text)

    async def test_deleted_engineer_session_returns_structured_error(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        state.groups["torque"].remove(alice.id)
        del state.agents[alice.id]

        async def fake_handle_command(_payload):
            self.fail("deleted engineer should fail before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(
            json.loads(text),
            {"type": "error", "message": f"no engineer with id={alice.id} exists"},
        )


class EngineerBindingValidationTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("torque.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["torque"] = []
        return state

    def test_validate_engineer_binding_requires_env_var(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            engineer_id, error = self.mcp_engineer_mod.validate_engineer_binding()

        self.assertEqual(engineer_id, "")
        self.assertEqual(error, "TORQUE_ENGINEER_ID is required")

    def test_validate_engineer_binding_rejects_missing_engineer(self):
        state = self._make_state()
        with mock.patch.dict("os.environ", {"TORQUE_ENGINEER_ID": "eng-missing"}, clear=True):
            engineer_id, error = self.mcp_engineer_mod.validate_engineer_binding(state)

        self.assertEqual(engineer_id, "")
        self.assertEqual(error, "no engineer with id=eng-missing exists")


class ServerPrClosingRefsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    class _FakePrWorktreeManager:
        def __init__(self, *, existing=False, existing_body=""):
            self.existing = existing
            self.existing_body = existing_body
            self.create_calls = []
            self.edit_calls = []
            self.merge_calls = []

        async def github_preflight(self, worktree_path):
            return {
                "ok": True,
                "phase": "github_preflight",
                "name_with_owner": "acme/repo",
                "url": "https://github.com/acme/repo",
            }

        async def github_select_remote(self, worktree_path):
            return {"ok": True, "phase": "github_remote", "remote": "origin"}

        async def github_sync_remote_base(
            self,
            worktree_path,
            repo_root,
            remote,
            base_branch,
        ):
            return {"ok": True, "phase": "remote_base_sync", "synced": True}

        async def has_uncommitted_changes(self, _cell):
            return False

        async def stale_base_info(self, _cell):
            return {"stale": False}

        async def check_merge_conflicts(self, _cell):
            return {"clean": True, "tree_sha": "tree-sha"}

        async def merge_untracked_overwrite_paths(
            self,
            _repo_root,
            _base_branch,
            _tree_sha,
        ):
            return []

        async def list_checkpoints(self, _cell):
            return [{"message": "Implement linked GitHub issue", "body": ""}]

        async def github_push_branch(self, worktree_path, remote, branch):
            return {"ok": True, "phase": "push_branch"}

        async def github_create_or_reuse_pr(
            self,
            worktree_path,
            branch,
            base_branch,
            title="",
            body="",
        ):
            self.create_calls.append({
                "worktree_path": worktree_path,
                "branch": branch,
                "base_branch": base_branch,
                "title": title,
                "body": body,
            })
            return {
                "ok": True,
                "phase": "pr_create",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "body": self.existing_body if self.existing else body,
                "head_sha": "head123",
                "state": "OPEN",
                "merge_state": "CLEAN",
                "existing": self.existing,
            }

        async def github_pr_edit_body(self, worktree_path, selector, body):
            self.edit_calls.append({
                "worktree_path": worktree_path,
                "selector": selector,
                "body": body,
            })
            self.existing_body = body
            return {
                "ok": True,
                "phase": "pr_edit_body",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "body": body,
                "head_sha": "head123",
                "state": "OPEN",
            }

        async def github_request_squash_merge(
            self,
            worktree_path,
            pr_number,
            head_sha,
            subject="",
            body="",
            auto=False,
            url="",
        ):
            self.merge_calls.append({
                "worktree_path": worktree_path,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "subject": subject,
                "body": body,
                "auto": auto,
                "url": url,
            })
            return {
                "ok": True,
                "phase": "pr_merge",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "head_sha": "head123",
                "merge_state": "BLOCKED",
                "pending": True,
                "pr_status": {
                    "ok": True,
                    "state": "OPEN",
                    "merge_state": "BLOCKED",
                },
            }

    def _make_state(self, *, close_issues=True, external_id="acme/repo#12"):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings(
            "g",
            engineer_merge_mode="pr",
            board_sync_provider="github",
            board_sync_github={
                "github_close_issues_via_pr": close_issues,
            },
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/tmp/worker",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
            worktree_repo_root="/repo",
            current_task_id="TORQUE:1:1",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        product = state.board_add_task(
            "Linked product task",
            "g",
            lane="Done",
            id="TORQUE:1",
            provider="github",
            external_id=external_id,
            external_url=(
                "https://github.com/"
                f"{external_id.split('#', 1)[0]}/issues/"
                f"{external_id.split('#', 1)[1]}"
            ),
        )
        review = state.board_add_task(
            "Review linked product task",
            "g",
            lane="In Progress",
            id="TORQUE:1:1",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            agent_id=worker.id,
            action_name="feature/review",
        )
        review.worktree_boundary = {
            "version": "1",
            "branch": worker.worktree_branch,
            "repo_root": worker.worktree_repo_root,
            "base_branch": worker.worktree_base_branch,
            "commit_sha": "head123",
            "kind": "marker",
            "status": "open",
            "recorded_at": "2026-05-19T18:00:00+00:00",
            "recorded_by_agent_id": worker.id,
        }
        return state, worker, product, review

    async def _run_pr_merge(
            self,
            state,
            worker,
            worktree_mgr,
            data=None,
            progress=None):
        async def latest_boundary_state(_cell):
            return {
                "latest": {"task_id": "TORQUE:1:1"},
                "clean": {"task_id": "TORQUE:1:1"},
                "reason": "",
            }

        async def noop_async(*_args, **_kwargs):
            return None

        return await self.server_mod._run_pr_worktree_merge(
            state=state,
            cell=worker,
            aid=worker.id,
            data=data or {},
            worktree_mgr=worktree_mgr,
            latest_boundary_state_for_cell=latest_boundary_state,
            boundary_reason_message=lambda reason, _boundary=None: reason,
            mark_branch_boundaries_merged=lambda *_args, **_kwargs: None,
            cleanup_after_merge=noop_async,
            broadcast_toast=noop_async,
            bridge=None,
            handle_command=noop_async,
            panel_event=None,
            progress=progress,
        )

    async def test_generated_pr_body_includes_same_repo_closing_ref(self):
        state, worker, _product, _review = self._make_state()
        mgr = self._FakePrWorktreeManager()

        result = await self._run_pr_merge(state, worker, mgr)

        self.assertTrue(result["ok"])
        create_body = mgr.create_calls[0]["body"]
        self.assertIn("Linked Torque issues:", create_body)
        self.assertIn("- Closes #12", create_body)
        self.assertNotIn("acme/repo#12", create_body)
        self.assertIn("- Closes #12", mgr.merge_calls[0]["body"])
        self.assertIn("PR: https://github.com/acme/repo/pull/7",
                      mgr.merge_calls[0]["body"])

    async def test_pr_merge_emits_create_and_merge_progress_phases(self):
        state, worker, _product, _review = self._make_state()
        mgr = self._FakePrWorktreeManager()
        phases = []

        async def progress(phase, message, **_extra):
            phases.append((phase, message))

        result = await self._run_pr_merge(
            state,
            worker,
            mgr,
            progress=progress,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [phase for phase, _message in phases],
            ["preflight", "push_branch", "pr_create", "pr_merge"],
        )
        self.assertIn("Creating pull request", phases[2][1])
        self.assertIn("Merging pull request", phases[3][1])

    async def test_user_supplied_pr_body_gets_closing_refs_appended(self):
        state, worker, _product, _review = self._make_state()
        mgr = self._FakePrWorktreeManager()

        await self._run_pr_merge(
            state,
            worker,
            mgr,
            {"pr_body": "Operator-authored body."},
        )

        create_body = mgr.create_calls[0]["body"]
        self.assertTrue(create_body.startswith("Operator-authored body."))
        self.assertIn("- Closes #12", create_body)

    async def test_reused_pr_body_is_edited_to_add_missing_closing_refs(self):
        state, worker, _product, _review = self._make_state()
        mgr = self._FakePrWorktreeManager(
            existing=True,
            existing_body="Existing PR body.",
        )

        await self._run_pr_merge(state, worker, mgr)

        self.assertEqual(len(mgr.edit_calls), 1)
        self.assertEqual(mgr.edit_calls[0]["selector"], 7)
        self.assertIn("Existing PR body.", mgr.edit_calls[0]["body"])
        self.assertIn("- Closes #12", mgr.edit_calls[0]["body"])

    async def test_duplicate_closing_refs_are_not_appended_on_rerun(self):
        state, worker, _product, _review = self._make_state()
        mgr = self._FakePrWorktreeManager(
            existing=True,
            existing_body="Existing PR body.\n\nFixes #12",
        )

        await self._run_pr_merge(
            state,
            worker,
            mgr,
            {"pr_body": "Operator body.\n\nFixes #12"},
        )

        create_body = mgr.create_calls[0]["body"]
        self.assertEqual(create_body.lower().count("#12"), 1)
        self.assertEqual(mgr.edit_calls, [])

    async def test_disabled_setting_skips_closing_ref_injection(self):
        state, worker, _product, _review = self._make_state(close_issues=False)
        mgr = self._FakePrWorktreeManager(
            existing=True,
            existing_body="Existing PR body.",
        )
        original = self.server_mod.get_board_sync_provider
        self.server_mod.get_board_sync_provider = mock.Mock(
            side_effect=AssertionError("provider should not be called")
        )
        try:
            await self._run_pr_merge(state, worker, mgr)
        finally:
            self.server_mod.get_board_sync_provider = original

        self.assertNotIn("Closes", mgr.create_calls[0]["body"])
        self.assertEqual(mgr.edit_calls, [])

    async def test_cross_repo_refs_render_with_owner_repo_prefix(self):
        state, worker, _product, _review = self._make_state(
            external_id="other/project#34",
        )
        mgr = self._FakePrWorktreeManager()

        await self._run_pr_merge(state, worker, mgr)

        create_body = mgr.create_calls[0]["body"]
        self.assertIn("- Closes other/project#34", create_body)
        self.assertNotIn("- Closes #34", create_body)
