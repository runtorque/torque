from datetime import datetime, timezone
import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class TaskHealthTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.task_health_mod = importlib.import_module("loom.task_health")
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
            labels=["loom:human"],
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
        self.state_mod = importlib.import_module("loom.state")
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
