import importlib
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.direct_message_mirrors import NON_USER_ASK_LABEL


class FakeActionManager:
    def __init__(self, actions=None):
        self.actions = actions or {}

    def load_action(self, name, base_dir=""):
        return dict(self.actions.get(name, {}))

    def get_transitions(self, name, base_dir=""):
        return list(self.actions.get(name, {}).get("transitions", []))

    def get_auto_close_on_done(self, name, base_dir=""):
        return bool(self.actions.get(name, {}).get("auto_close_on_done", False))

    def list_actions(self, base_dir=""):
        return []


class FakeWorktreeManager:
    async def diff_summary(self, _cell, *, non_test_only=False):
        return {"files": 0, "insertions": 0, "deletions": 0}

    async def checkpoint(self, _cell, *, message=""):
        return ""


class ServerAiReportMandatoryReviewGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, *, action_mgr=None):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )

        class DummyBridge:
            async def list_profiles(self):
                return []

            async def get_launch_context(self):
                return types.SimpleNamespace(
                    current_path="",
                    current_profile="",
                )

            async def update_session(self, *_args, **_kwargs):
                return None

        async def noop_async(*_args, **_kwargs):
            return None

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_broadcast_toast": noop_async,
            "_checkpoint_message": lambda _cell: "checkpoint",
            "_checkpoint_on_report": noop_async,
            "_cleanup_after_merge": noop_async,
            "_close_agent_session_only": noop_async,
            "_panel_event": lambda *args, **kwargs: None,
            "_record_task_boundary": noop_async,
            "_resolve_base_dir": noop_async,
            "_runtime_payload": lambda: {},
            "action_mgr": action_mgr or self._default_action_mgr(),
            "bridge": DummyBridge(),
            "db": None,
            "handle_command": None,
            "panel_log": types.SimpleNamespace(
                replace_last=lambda *args, **kwargs: {}
            ),
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
            "worktree_mgr": FakeWorktreeManager(),
        })
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            self.server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    def _default_action_mgr(self):
        return FakeActionManager({
            "feature/implement": {
                "implementation_depth": True,
                "review_required_above_loc": 100,
                "transitions": [{
                    "action": "feature/review",
                    "when": "implementation is complete and ready for review",
                    "status": "On Review",
                }],
            },
            "feature/review": {
                "auto_close_on_done": True,
                "transitions": [{
                    "action": "feature/fix-review",
                    "when": "issues were found",
                }],
            },
            "oneshot/fix": {
                "implementation_depth": True,
                "review_required_above_loc": 150,
                "transitions": [{
                    "action": "feature/review",
                    "when": "fix diff exceeded review gate threshold",
                    "status": "On Review",
                }],
            },
            "custom/no-review": {
                "implementation_depth": True,
                "review_required_above_loc": 100,
                "transitions": [],
            },
        })

    def _state_cell_task(self, *, kind="worker", action_name="feature/implement"):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id=f"{kind}-1",
            name=kind.title(),
            group="g",
            cell_type="agent",
            kind=kind,
            directory="/repo",
        )
        state.agents[cell.id] = cell
        task = state.board_add_task(
            "Implement mandatory gate",
            "g",
            lane="In Progress",
            id="task-1",
            action_name=action_name,
            agent_id=cell.id,
        )
        cell.current_task_id = task.id
        return state, cell, task

    def _add_review(self, state, task, *, lane="Done", message="Verdict: Ship", status=""):
        review = state.board_add_task(
            "Review mandatory gate",
            "g",
            lane=lane,
            id="task-review",
            action_name="feature/review",
            parent_task_id=task.id,
            pipeline_root_id=task.pipeline_root_id or task.id,
            pipeline_depth=task.pipeline_depth + 1,
            status=status,
            messages=[{
                "action": "done",
                "message": message,
                "agent_name": "Reviewer",
            }] if message else [],
        )
        return review

    async def _ai_done(self, state, cell, *, action_mgr=None):
        handle_command = self._extract_handle_command(
            state,
            action_mgr=action_mgr,
        )
        return await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "Implementation complete",
        })

    async def _ai_ask(self, state, cell, question):
        handle_command = self._extract_handle_command(state)
        return await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "ask",
            "message": question,
        })

    async def test_ai_ask_marks_user_attention_for_architect_owned_ask(self):
        state, cell, task = self._state_cell_task(kind="architect")

        result = await self._ai_ask(state, cell, "Should we ship?")

        self.assertEqual(result["type"], "ok")
        ask = state.board_tasks[result["task_id"]]
        self.assertIn("torque:human", ask.labels)
        self.assertNotIn(NON_USER_ASK_LABEL, ask.labels)
        self.assertTrue(cell.needs_attention)
        self.assertEqual(task.status, "Awaiting Input")

    async def test_ai_ask_suppresses_user_attention_for_engineer_owned_worker(self):
        state, worker, task = self._state_cell_task(kind="worker")
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"].append(engineer.id)
        worker.owner_engineer_id = engineer.id

        result = await self._ai_ask(state, worker, "Can I use the fallback?")

        self.assertEqual(result["type"], "ok")
        ask = state.board_tasks[result["task_id"]]
        self.assertIn("torque:human", ask.labels)
        self.assertIn(NON_USER_ASK_LABEL, ask.labels)
        self.assertFalse(worker.needs_attention)
        self.assertEqual(task.status, "Awaiting Input")

    async def test_worker_feature_implement_without_ship_review_is_rejected(self):
        state, cell, task = self._state_cell_task()

        result = await self._ai_done(state, cell)

        self.assertEqual(result["type"], "error")
        self.assertIn(
            "This is a mandatory-review task (action=feature/implement).",
            result["message"],
        )
        self.assertIn(
            'torque_derive(description="Review Implement mandatory gate", action="feature/review")',
            result["message"],
        )
        self.assertEqual(task.lane, "In Progress")

    async def test_worker_feature_implement_with_ship_review_succeeds(self):
        state, cell, task = self._state_cell_task()
        self._add_review(state, task, message="## Verdict\n**Ship** — ready")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")
        self.assertEqual(cell.current_task_id, "")

    async def test_worker_feature_implement_with_in_progress_review_is_rejected(self):
        state, cell, task = self._state_cell_task()
        self._add_review(state, task, lane="In Progress", message="Verdict: Ship")

        result = await self._ai_done(state, cell)

        self.assertEqual(result["type"], "error")
        self.assertIn(
            'torque_derive(description="Review Implement mandatory gate", action="feature/review")',
            result["message"],
        )
        self.assertEqual(task.lane, "In Progress")

    async def test_worker_oneshot_fix_done_succeeds(self):
        state, cell, task = self._state_cell_task(action_name="oneshot/fix")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_worker_action_without_feature_review_transition_succeeds(self):
        state, cell, task = self._state_cell_task(action_name="custom/no-review")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_engineer_feature_implement_done_succeeds_without_review(self):
        state, cell, task = self._state_cell_task(kind="engineer")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_done_review_with_ship_status_satisfies_gate(self):
        state, cell, task = self._state_cell_task()
        self._add_review(state, task, message="", status="Ship")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")
