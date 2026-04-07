import importlib
import sys
import types
import unittest
from enum import Enum

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


def install_iterm2_stub():
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

    tool = types.SimpleNamespace(async_register_web_view_tool=None)
    binding = types.ModuleType("iterm2.binding")
    keyboard = types.ModuleType("iterm2.keyboard")
    keyboard.Modifier = Modifier
    keyboard.Keycode = Keycode
    iterm2.Connection = Connection
    iterm2.tool = tool
    iterm2.binding = binding
    iterm2.keyboard = keyboard
    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard
    return iterm2


class ServerSelfDispatchTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)

    def test_self_dispatch_prompt_is_minimal_hint(self):
        self.assertEqual(
            self.server_mod._build_self_dispatch_prompt(),
            "Proceed with the derived task you just created.",
        )

    def test_self_dispatch_bypasses_busy_agent_queue(self):
        active = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
        )

        self.assertTrue(
            self.server_mod._should_queue_existing_agent_dispatch(
                active,
                target_task_id="task-2",
                self_dispatch=False,
            )
        )
        self.assertFalse(
            self.server_mod._should_queue_existing_agent_dispatch(
                active,
                target_task_id="task-2",
                self_dispatch=True,
            )
        )
