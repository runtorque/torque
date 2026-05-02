from datetime import datetime, timezone
import importlib
import unittest
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class TaskHealthTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.task_health_mod = importlib.import_module("torque.task_health")
        self.task_health_mod = importlib.reload(self.task_health_mod)

    def test_explicit_human_and_dependency_signals_are_blocked(self):
        dep = self.state_mod.BoardTask(
            id="dep-1",
            task="Dependency",
            group="g",
            lane="To Do",
        )
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Needs review",
            group="g",
            lane="In Progress",
            labels=["torque:human"],
            depends_on=["dep-1"],
            updated_at=_iso(1_000),
        )

        snapshots = self.task_health_mod.compute_task_health(
            {"dep-1": dep, "task-1": parent},
            {},
            now_ts=1_600,
        )

        self.assertEqual(snapshots["task-1"].state, "blocked")
        self.assertEqual(
            snapshots["task-1"].details["reasons"],
            ["awaiting_human", "dependency_blocked"],
        )

    def test_idle_risk_and_stalled_are_time_based_and_deterministic(self):
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Investigate drift",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(10_000),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
            )
        }

        idle_snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            agents,
            now_ts=10_000 + (11 * 60),
        )
        stalled_snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            agents,
            now_ts=10_000 + (21 * 60),
        )

        self.assertEqual(idle_snapshots["task-1"].state, "idle-risk")
        self.assertEqual(stalled_snapshots["task-1"].state, "stalled")

    def test_heartbeats_do_not_mask_progress_silence(self):
        base = 50_000
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Review stall",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(base),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                last_progress_at=base,
                last_heartbeat_at=base + (10 * 60),
            )
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            agents,
            now_ts=base + (11 * 60),
        )

        self.assertEqual(snapshots["task-1"].state, "idle-risk")
        self.assertEqual(
            snapshots["task-1"].details["reasons"],
            ["progress_silence_warning"],
        )
        self.assertEqual(snapshots["task-1"].details["silence_secs"], 11 * 60)

    def test_zero_progress_heartbeat_only_agent_uses_task_clock_for_health(self):
        base = 70_000
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Never emitted progress",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(base),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                last_progress_at=0,
                last_heartbeat_at=base + (10 * 60),
            )
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            agents,
            now_ts=base + (11 * 60),
        )

        self.assertEqual(snapshots["task-1"].state, "idle-risk")
        self.assertEqual(
            snapshots["task-1"].details["reasons"],
            ["progress_silence_warning"],
        )
        self.assertEqual(snapshots["task-1"].details["silence_secs"], 11 * 60)

    def test_token_zero_after_dispatch_is_caught_by_existing_silence_rule(self):
        base = 60_000
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Worker never starts",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(base),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                status="running",
                last_progress_at=base,
                last_heartbeat_at=base + 30,
                session_tokens_in=0,
                session_tokens_out=0,
            )
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            agents,
            now_ts=base + (11 * 60),
        )

        self.assertEqual(snapshots["task-1"].state, "idle-risk")
        self.assertEqual(
            snapshots["task-1"].details["reasons"],
            ["progress_silence_warning"],
        )

    def test_thrashing_detects_recent_message_churn_without_completion(self):
        base = 20_000
        messages = []
        for idx, action in enumerate(
            ["progress", "blocked", "progress", "error", "progress", "blocked"]
        ):
            messages.append({
                "timestamp": base + (idx * 60),
                "action": action,
                "message": action,
            })
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Flaky pipeline",
            group="g",
            lane="In Progress",
            messages=messages,
            updated_at=_iso(base + 5),
        )

        snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            {},
            now_ts=base + (10 * 60),
        )

        self.assertEqual(snapshots["task-1"].state, "thrashing")
        self.assertEqual(
            snapshots["task-1"].details["reasons"],
            ["message_churn"],
        )

    def test_idle_agent_with_checkpointed_work_is_flagged_stale_in_progress(self):
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Wrap up release notes",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(25_000),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                status="running",
                worktree_path="/repo/.torque/worktrees/agent-1",
                worktree_checkpoints=2,
                worktree_dirty=False,
            )
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"task-1": task},
            agents,
            now_ts=25_120,
        )

        self.assertEqual(snapshots["task-1"].state, "stale-in-progress")
        self.assertIn(
            "checkpointed_worktree",
            snapshots["task-1"].details["reasons"],
        )

    def test_open_derived_handoff_is_not_flagged_stale_in_progress(self):
        parent = self.state_mod.BoardTask(
            id="task-parent",
            task="Implement feature",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(26_000),
        )
        child = self.state_mod.BoardTask(
            id="task-review",
            task="Review feature",
            group="g",
            lane="In Progress",
            parent_task_id="task-parent",
            agent_id="agent-2",
            updated_at=_iso(26_000),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Implementer",
                group="g",
                cell_type="agent",
                status="running",
                worktree_path="/repo/.torque/worktrees/agent-1",
                worktree_checkpoints=1,
                worktree_dirty=False,
            ),
            "agent-2": self.state_mod.AgentCell(
                id="agent-2",
                name="Reviewer",
                group="g",
                cell_type="agent",
                status="running",
                activity="thinking",
            ),
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"task-parent": parent, "task-review": child},
            agents,
            now_ts=26_060,
        )

        self.assertEqual(snapshots["task-parent"].state, "healthy")

    def test_queued_same_agent_follow_up_is_not_flagged_stale_in_progress(self):
        current = self.state_mod.BoardTask(
            id="task-1",
            task="Current task",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            updated_at=_iso(27_000),
        )
        queued = self.state_mod.BoardTask(
            id="task-2",
            task="Queued follow-up",
            group="g",
            lane="To Do",
            agent_id="agent-1",
            updated_at=_iso(27_100),
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                status="running",
                worktree_path="/repo/.torque/worktrees/agent-1",
                worktree_checkpoints=1,
                worktree_dirty=False,
            )
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"task-1": current, "task-2": queued},
            agents,
            now_ts=27_120,
        )

        self.assertEqual(snapshots["task-1"].state, "healthy")

    def test_parent_rolls_up_worst_open_child_health(self):
        parent = self.state_mod.BoardTask(
            id="parent",
            task="Parent",
            group="g",
            lane="In Progress",
            updated_at=_iso(30_000 + (16 * 60)),
        )
        child = self.state_mod.BoardTask(
            id="child",
            task="Child",
            group="g",
            lane="In Progress",
            parent_task_id="parent",
            updated_at=_iso(30_000),
            agent_id="agent-1",
        )
        agents = {
            "agent-1": self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
            )
        }

        snapshots = self.task_health_mod.compute_task_health(
            {"parent": parent, "child": child},
            agents,
            now_ts=30_000 + (21 * 60),
        )

        self.assertEqual(snapshots["child"].state, "stalled")
        self.assertEqual(snapshots["parent"].state, "stalled")
        self.assertTrue(snapshots["parent"].details["aggregate"])
        self.assertEqual(
            snapshots["parent"].details["source_task_id"],
            "child",
        )


class MatrixStateTaskHealthTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_recompute_task_health_updates_since_only_on_transition(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        task = state.board_add_task(
            "Track silence",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id="agent-1",
        )
        self.assertIsNotNone(task)
        task.created_at = _iso(40_000)
        task.updated_at = _iso(40_000)
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )

        state.recompute_task_health(now_ts=40_000 + (11 * 60), persist=False)
        first_since = state.board_tasks["task-1"].health_since
        state.recompute_task_health(now_ts=40_000 + (12 * 60), persist=False)

        self.assertEqual(state.board_tasks["task-1"].health_state, "idle-risk")
        self.assertEqual(state.board_tasks["task-1"].health_since, first_since)

        state.recompute_task_health(now_ts=40_000 + (21 * 60), persist=False)

        self.assertEqual(state.board_tasks["task-1"].health_state, "stalled")
        self.assertNotEqual(state.board_tasks["task-1"].health_since, first_since)

    def test_recompute_task_health_quiet_state_is_noop(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.board_add_task("Quiet", "g", id="task-1")

        task_health_mod = importlib.import_module("torque.task_health")
        with mock.patch.object(
                task_health_mod,
                "compute_task_health",
                side_effect=AssertionError("full scan should be skipped")):
            self.assertEqual(state.recompute_task_health(persist=False), [])

    def test_incremental_recompute_single_task_op_skips_unrelated(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        parent = state.board_add_task("Parent", "g", id="parent")
        child = state.board_add_task(
            "Child",
            "g",
            id="child",
            parent_task_id=parent.id,
        )
        state.board_add_task("Unrelated", "g", id="unrelated")

        task_health_mod = importlib.import_module("torque.task_health")
        original = task_health_mod._compute_local_health
        calls = []

        def wrapped(task, *args, **kwargs):
            calls.append(task.id)
            return original(task, *args, **kwargs)

        with mock.patch.object(
                task_health_mod,
                "_compute_local_health",
                side_effect=wrapped):
            state.board_update_task(child.id, labels=["torque:blocked"])

        self.assertCountEqual(calls, ["child", "parent"])
        self.assertNotIn("unrelated", calls)

    def test_incremental_recompute_cascade_updates_ancestors(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        parent = state.board_add_task("Parent", "g", id="parent")
        child = state.board_add_task(
            "Child",
            "g",
            id="child",
            parent_task_id=parent.id,
        )

        state.board_update_task(child.id, labels=["torque:blocked"])

        self.assertEqual(state.board_tasks["child"].health_state, "blocked")
        self.assertEqual(state.board_tasks["parent"].health_state, "blocked")
        self.assertTrue(state.board_tasks["parent"].health_details["aggregate"])
        self.assertEqual(
            state.board_tasks["parent"].health_details["source_task_id"],
            "child",
        )

        state.board_update_task(child.id, labels=[])

        self.assertEqual(state.board_tasks["child"].health_state, "healthy")
        self.assertEqual(state.board_tasks["parent"].health_state, "healthy")

    def test_same_agent_open_task_add_invalidates_existing_task(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_checkpoints=1,
            worktree_dirty=False,
        )
        state.board_add_task(
            "Current",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id="agent-1",
        )

        state.board_add_task(
            "Queued follow-up",
            "g",
            lane="To Do",
            id="task-2",
            agent_id="agent-1",
        )

        task_health_mod = importlib.import_module("torque.task_health")
        full = task_health_mod.compute_task_health(
            state.board_tasks,
            state.agents,
        )["task-1"].state
        self.assertEqual(state.board_tasks["task-1"].health_state, "healthy")
        self.assertEqual(state.board_tasks["task-1"].health_state, full)
        self.assertFalse(state.has_pending_task_health_recompute())

    def test_same_agent_open_task_done_invalidates_existing_task(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_checkpoints=1,
            worktree_dirty=False,
        )
        state.board_add_task(
            "Current",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id="agent-1",
        )
        state.board_add_task(
            "Queued follow-up",
            "g",
            lane="To Do",
            id="task-2",
            agent_id="agent-1",
        )
        self.assertEqual(state.board_tasks["task-1"].health_state, "healthy")

        state.board_update_task("task-2", lane="Done")

        task_health_mod = importlib.import_module("torque.task_health")
        full = task_health_mod.compute_task_health(
            state.board_tasks,
            state.agents,
        )["task-1"].state
        self.assertEqual(
            state.board_tasks["task-1"].health_state,
            "stale-in-progress",
        )
        self.assertEqual(state.board_tasks["task-1"].health_state, full)
        self.assertFalse(state.has_pending_task_health_recompute())

    def test_agent_status_change_invalidates_assigned_task(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_checkpoints=1,
            worktree_dirty=False,
        )
        state.agents[agent.id] = agent
        state.board_add_task(
            "Current",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id=agent.id,
        )
        self.assertEqual(
            state.board_tasks["task-1"].health_state,
            "stale-in-progress",
        )

        agent.status = "stopped"
        state._emit_agent(agent)
        state.recompute_task_health()

        self.assertEqual(state.board_tasks["task-1"].health_state, "healthy")
        self.assertFalse(state.has_pending_task_health_recompute())

    def test_task_done_cascade_refreshes_ancestor_health_incrementally(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        parent = state.board_add_task(
            "Parent",
            "g",
            id="parent",
            lane="In Progress",
        )
        child = state.board_add_task(
            "Child",
            "g",
            id="child",
            lane="To Do",
            parent_task_id=parent.id,
            labels=["torque:blocked"],
        )
        self.assertEqual(state.board_tasks["parent"].health_state, "blocked")

        state.board_update_task(child.id, lane="Done")

        self.assertEqual(state.board_tasks["child"].health_state, "healthy")
        self.assertEqual(state.board_tasks["parent"].lane, "Done")
        self.assertEqual(state.board_tasks["parent"].health_state, "healthy")
        self.assertFalse(state.has_pending_task_health_recompute())

    def test_task_delete_cleans_secondary_indexes(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.board_add_task("Dependency", "g", id="dep")
        state.board_add_task("Parent", "g", id="parent")
        child = state.board_add_task(
            "Child",
            "g",
            id="child",
            parent_task_id="parent",
            agent_id="agent-1",
            depends_on=["dep"],
        )
        self.assertIn(child.id, state._tasks_by_group["g"])
        self.assertIn(child.id, state._tasks_by_parent["parent"])
        self.assertIn(child.id, state._tasks_by_agent["agent-1"])
        self.assertIn(child.id, state._task_dependents_by_dep["dep"])
        self.assertIn(child.id, state._task_index_refs)

        state.board_remove_task(child.id)

        self.assertNotIn(child.id, state._tasks_by_group.get("g", set()))
        self.assertNotIn(
            child.id,
            state._tasks_by_parent.get("parent", set()),
        )
        self.assertNotIn(
            child.id,
            state._tasks_by_agent.get("agent-1", set()),
        )
        self.assertNotIn(
            child.id,
            state._task_dependents_by_dep.get("dep", set()),
        )
        self.assertNotIn(child.id, state._task_index_refs)
