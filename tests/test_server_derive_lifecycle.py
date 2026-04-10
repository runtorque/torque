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
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
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
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
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
