import importlib
import sys
import types
import unittest
from enum import Enum


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


def _install_iterm2_stub():
    iterm2 = types.ModuleType("iterm2")

    class Connection:
        pass

    class Modifier(Enum):
        COMMAND = "command"
        OPTION = "option"
        SHIFT = "shift"
        CONTROL = "control"
        FUNCTION = "function"

    class Keycode(Enum):
        UP_ARROW = "UP_ARROW"
        DOWN_ARROW = "DOWN_ARROW"
        LEFT_ARROW = "LEFT_ARROW"
        RIGHT_ARROW = "RIGHT_ARROW"
        HOME = "HOME"
        END = "END"
        PAGE_UP = "PAGE_UP"
        PAGE_DOWN = "PAGE_DOWN"
        FORWARD_DELETE = "FORWARD_DELETE"
        ANSI_A = "ANSI_A"
        ANSI_B = "ANSI_B"
        ANSI_C = "ANSI_C"
        ANSI_T = "ANSI_T"

    binding = types.ModuleType("iterm2.binding")
    keyboard = types.ModuleType("iterm2.keyboard")
    tool = types.SimpleNamespace(async_register_web_view_tool=None)
    keyboard.Modifier = Modifier
    keyboard.Keycode = Keycode
    iterm2.Connection = Connection
    iterm2.tool = tool
    iterm2.binding = binding
    iterm2.keyboard = keyboard
    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard


class ServerDeriveLifecycleTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        _install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress"
        )
        return state

    def test_review_to_fix_flow_frees_parent_slot_and_promotes_started_fix(self):
        state = self._make_state()
        implementer = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-root",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            current_task_id="task-review",
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )

        self.assertFalse(state.task_occupies_execution_slot(root, agent_id=implementer.id))
        self.assertEqual(state.agent_current_task(implementer.id).id, fix.id)

        self.server_mod._promote_task_for_active_report(
            state, implementer, fix
        )

        self.assertEqual(state.board_tasks[fix.id].lane, "In Progress")
        self.assertEqual(implementer.current_task_id, fix.id)
        self.assertTrue(state.agent_is_busy(implementer.id))

    def test_fix_to_rereview_reuses_prior_reviewer_stage(self):
        state = self._make_state()
        implementer = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            session_id="impl-session",
            status="running",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            status="idle",
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )

        reused = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, fix, "feature/review"
        )
        fresh = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, root, "feature/review"
        )

        self.assertIs(reused, reviewer)
        self.assertIsNone(fresh)

    def test_open_descendants_block_manual_completion(self):
        state = self._make_state()
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )

        rejected = self.server_mod._reject_completion_with_open_descendants(
            state, root, "done"
        )
        self.assertEqual(rejected["type"], "error")

        state.board_move_task(fix.id, "Done")
        allowed = self.server_mod._reject_completion_with_open_descendants(
            state, root, "done"
        )

        self.assertIsNone(allowed)


class ServerReviewAgentReuseDeriveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        _install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    class _ActionManager:
        def get_transitions(self, action_name, _base_dir):
            if action_name == "feature/implement":
                return [{"action": "feature/review"}]
            if action_name == "feature/review":
                return [{"action": "feature/fix-review"}]
            return []

        def load_action(self, _action_name, _base_dir):
            return {}

        def list_actions(self, _base_dir):
            return []

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress"
        )
        return state

    def _make_agent(self, agent_id, *, status="idle", session_id=None,
                    current_task_id=""):
        return self.state_mod.AgentCell(
            id=agent_id,
            name=agent_id,
            slug=agent_id,
            group="g",
            cell_type="agent",
            status=status,
            session_id=(
                session_id if session_id is not None
                else f"{agent_id}-session"
            ),
            current_task_id=current_task_id,
            directory="/repo",
        )

    def _extract_handle_command(self, state, dispatch_handler):
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

        async def resolve_base_dir(_group=""):
            return "/repo"

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_ownership_engineer_id_for_dispatch_source":
                lambda _cell: "",
            "_panel_event": lambda *args, **kwargs: None,
            "_resolve_base_dir": resolve_base_dir,
            "_runtime_payload": lambda: {},
            "action_mgr": self._ActionManager(),
            "bridge": DummyBridge(),
            "handle_command": dispatch_handler,
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
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

    def _recording_dispatch(self, state):
        calls = []

        async def dispatch(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "dispatch_task")
            task = state.board_tasks[payload["id"]]
            if payload.get("agent_id"):
                task.agent_id = payload["agent_id"]
                task.lane = "In Progress"
                return {
                    "type": "ok",
                    "task_id": task.id,
                    "agent_id": payload["agent_id"],
                }
            self.assertTrue(payload.get("create_agent"))
            agent_id = f"created-{len(calls)}"
            agent = self._make_agent(agent_id)
            state.agents[agent_id] = agent
            state.groups["g"].append(agent_id)
            task.agent_id = agent_id
            task.lane = "In Progress"
            return {
                "type": "ok",
                "task_id": task.id,
                "agent_id": agent_id,
            }

        return calls, dispatch

    def _add_second_review_cycle_chain(self, state, *,
                                       reviewer_status="idle",
                                       reviewer_session="review-1-session"):
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="task-fix",
        )
        reviewer = self._make_agent(
            "review-1",
            status=reviewer_status,
            session_id=reviewer_session,
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        state.groups["g"].extend([implementer.id, reviewer.id])
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )
        return implementer, reviewer, root, fix

    async def test_feature_review_derive_reuses_live_prior_reviewer_in_chain(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(state)
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(result["agent_id"], reviewer.id)
        self.assertEqual(calls[0]["agent_id"], reviewer.id)
        self.assertNotIn("create_agent", calls[0])

    async def test_feature_review_derive_creates_agent_when_prior_reviewer_closed(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(
                state,
                reviewer_status="stopped",
                reviewer_session="",
            )
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
        })

        self.assertEqual(result["type"], "ok")
        self.assertNotEqual(result["agent_id"], reviewer.id)
        self.assertTrue(calls[0]["create_agent"])
        self.assertNotIn("agent_id", calls[0])

    async def test_explicit_target_agent_overrides_prior_reviewer_reuse(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(state)
        )
        explicit = self._make_agent("review-2")
        state.agents[explicit.id] = explicit
        state.groups["g"].append(explicit.id)
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
            "target_agent": explicit.id,
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(result["agent_id"], explicit.id)
        self.assertEqual(calls[0]["agent_id"], explicit.id)
        self.assertNotEqual(calls[0]["agent_id"], reviewer.id)

    async def test_first_feature_review_derive_creates_new_agent(self):
        state = self._make_state()
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="task-root",
        )
        state.agents[implementer.id] = implementer
        state.groups["g"].append(implementer.id)
        state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review implementation",
        })

        self.assertEqual(result["type"], "ok")
        self.assertNotEqual(result["agent_id"], implementer.id)
        self.assertTrue(calls[0]["create_agent"])
        self.assertNotIn("agent_id", calls[0])
