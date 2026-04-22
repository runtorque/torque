import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class FakeActionManager:
    def __init__(self, action=None, transitions=None):
        self.action = action or {}
        self.transitions = transitions or []

    def load_action(self, _name, _base_dir=""):
        return dict(self.action)

    def get_transitions(self, _name, _base_dir=""):
        return list(self.transitions)


class FakeWorktreeManager:
    def __init__(self, summary):
        self.summary = summary
        self.non_test_only = None

    async def diff_summary(self, _cell, *, non_test_only=False):
        self.non_test_only = non_test_only
        return dict(self.summary)


class ReviewGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.worktree_mod = importlib.import_module("loom.worktree")
        self.worktree_mod = importlib.reload(self.worktree_mod)

    def _state_cell_task(self, *, action_name="feature/implement"):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            worktree_path="/tmp/wt",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        task = state.board_add_task(
            "Implement",
            "g",
            lane="In Progress",
            id="task-1",
            action_name=action_name,
            agent_id=cell.id,
        )
        return state, cell, task

    async def test_gate_fires_above_threshold_and_auto_derives_review(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager(
            {"review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 2, "insertions": 80, "deletions": 30},
        )
        calls = []
        checkpoints = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        async def checkpoint_for_gate():
            checkpoints.append("checkpoint")

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
            checkpoint_for_gate=checkpoint_for_gate,
        )

        self.assertEqual(result["type"], "error")
        self.assertIn("auto-derived at task-review", result["message"])
        self.assertIn("diff: 110 LOC", result["message"])
        self.assertIn("threshold: 100", result["message"])
        self.assertEqual(calls[0]["action"], "derive")
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertEqual(calls[0]["task_id"], task.id)
        self.assertEqual(checkpoints, ["checkpoint"])
        self.assertTrue(worktree_mgr.non_test_only)

    async def test_gate_skips_below_threshold(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager(
            {"review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 99, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_gate_inactive_without_action_metadata(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager({}, [{"action": "feature/review"}])
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 500, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_gate_skips_when_no_review_transition_exists(self):
        state, cell, task = self._state_cell_task(action_name="oneshot/fix")
        action_mgr = FakeActionManager({"review_required_above_loc": 100}, [])
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 200, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_gate_skips_after_closed_ship_review_in_chain(self):
        state, cell, task = self._state_cell_task()
        review = state.board_add_task(
            "Review",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=task.id,
            pipeline_root_id=task.id,
            pipeline_depth=1,
        )
        review.messages.append({
            "action": "done",
            "message": "Verdict — Ship",
            "agent_name": "Reviewer",
        })
        action_mgr = FakeActionManager(
            {"review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 200, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_force_skip_review_bypasses_gate_and_records_audit(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager(
            {"review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 2, "insertions": 175, "deletions": 25},
        )
        calls = []
        panel_events = []
        history = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        def panel_event(*args, **kwargs):
            panel_events.append((args, kwargs))

        def append_task_msg(t, act, msg, agent_name):
            t.messages.append({
                "action": act,
                "message": msg,
                "agent_name": agent_name,
            })

        def record_history_msg(c, act, msg, task_override=None):
            history.append((c.id, act, msg, task_override.id))

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            panel_event,
            cell=cell,
            task=task,
            force_skip_review=True,
            skip_reason="mechanical rename",
            append_task_msg=append_task_msg,
            record_history_msg=record_history_msg,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertEqual(task.messages[-1]["action"], "review_gate_skipped")
        audit = task.messages[-1]["message"]
        self.assertIn("worker-1", audit)
        self.assertIn("task-1", audit)
        self.assertIn("diff size 200 LOC", audit)
        self.assertIn("threshold 100", audit)
        self.assertIn("mechanical rename", audit)
        self.assertEqual(panel_events[0][0][0], "review_gate_skipped")
        self.assertIn("diff size 200 LOC", panel_events[0][0][4])
        self.assertEqual(history[0][1], "review_gate_skipped")

    async def test_non_test_filter_excludes_test_files_from_diff_size(self):
        summary, paths = self.worktree_mod._numstat_summary(
            "120\t10\tloom/server.py\n"
            "999\t1\ttests/test_server.py\n"
            "50\t50\tstatic/app.test.js\n"
            "10\t10\tpkg/foo_test.go\n"
            "1\t2\tsrc/main.py\n",
            non_test_only=True,
        )

        self.assertEqual(paths, ["loom/server.py", "src/main.py"])
        self.assertEqual(summary["files"], 2)
        self.assertEqual(summary["insertions"], 121)
        self.assertEqual(summary["deletions"], 12)
