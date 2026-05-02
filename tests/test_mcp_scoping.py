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
