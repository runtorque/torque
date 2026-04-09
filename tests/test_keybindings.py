from enum import Enum, auto
import importlib
import sys
import types
import unittest


def _install_iterm2_stub():
    stored = {
        "bindings": [],
        "rpcs": {},
        "alerts": [],
        "alert_result": 1000,
    }

    iterm2 = types.ModuleType("iterm2")
    binding = types.ModuleType("iterm2.binding")
    keyboard = types.ModuleType("iterm2.keyboard")

    class Modifier(Enum):
        COMMAND = auto()
        OPTION = auto()
        SHIFT = auto()
        CONTROL = auto()
        FUNCTION = auto()

    class Keycode(Enum):
        UP_ARROW = auto()
        DOWN_ARROW = auto()
        LEFT_ARROW = auto()
        RIGHT_ARROW = auto()
        ANSI_A = auto()
        ANSI_B = auto()
        ANSI_C = auto()
        ANSI_T = auto()

    class BindingAction(Enum):
        INVOKE_SCRIPT_FUNCTION = auto()

    class KeyBinding:
        def __init__(self, character, modifiers, keycode, action, param, version=None, label=None):
            self.character = character
            self.modifiers = modifiers
            self.keycode = keycode
            self.action = action
            self.param = param
            self.version = version
            self.label = label
            self.key = (
                self.character,
                tuple(sorted(mod.name for mod in self.modifiers)),
                self.keycode.name if self.keycode else "",
            )

    async def async_get_global_key_bindings(_connection):
        return list(stored["bindings"])

    async def async_set_global_key_bindings(_connection, bindings):
        stored["bindings"] = list(bindings)

    class Alert:
        def __init__(self, title, subtitle, window_id=None):
            self.title = title
            self.subtitle = subtitle
            self.window_id = window_id
            self.buttons = []

        def add_button(self, title):
            self.buttons.append(title)

        async def async_run(self, _connection):
            stored["alerts"].append({
                "title": self.title,
                "subtitle": self.subtitle,
                "window_id": self.window_id,
                "buttons": list(self.buttons),
            })
            return stored["alert_result"]

    def RPC(fn):
        async def async_register(_connection, timeout=None):
            stored["rpcs"][fn.__name__] = fn
        fn.async_register = async_register
        return fn

    binding.KeyBinding = KeyBinding
    binding.BindingAction = BindingAction
    binding.async_get_global_key_bindings = async_get_global_key_bindings
    binding.async_set_global_key_bindings = async_set_global_key_bindings
    keyboard.Modifier = Modifier
    keyboard.Keycode = Keycode
    iterm2.binding = binding
    iterm2.keyboard = keyboard
    iterm2.Alert = Alert
    iterm2.RPC = RPC
    iterm2.Connection = object

    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard
    return stored, binding, keyboard


class KeybindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stored, self.binding_mod, self.keyboard_mod = _install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.keybindings_mod = importlib.import_module("loom.keybindings")
        self.keybindings_mod = importlib.reload(self.keybindings_mod)

    async def test_resolve_binding_specs_restores_function_modifier_for_arrows(self):
        specs = self.keybindings_mod._resolve_binding_specs(
            {
                "focus_next": {
                    "character": 0xF701,
                    "modifiers": ["command", "option"],
                    "keycode": "DOWN_ARROW",
                }
            }
        )

        focus_next = specs[0]
        modifier_names = {modifier.name for modifier in focus_next[1]}

        self.assertEqual(focus_next[2].name, "DOWN_ARROW")
        self.assertIn("FUNCTION", modifier_names)

    async def test_get_ordered_cells_and_current_agent_follow_parent_relationships(self):
        state = self.state_mod.MatrixState()
        state.groups = {"g": ["agent-1", "agent-2"]}
        state._children = {"agent-1": ["term-1"], "agent-2": []}

        first = self.state_mod.AgentCell(
            id="agent-1",
            name="One",
            group="g",
            slug="one",
            cell_type="agent",
            session_id="session-1",
            window_id="window-a",
        )
        terminal = self.state_mod.AgentCell(
            id="term-1",
            name="Logs",
            group="g",
            slug="one:logs",
            cell_type="terminal",
            session_id="session-term",
            window_id="window-a",
            parent_id="agent-1",
        )
        second = self.state_mod.AgentCell(
            id="agent-2",
            name="Two",
            group="g",
            slug="two",
            cell_type="agent",
            session_id="session-2",
            window_id="window-b",
        )
        state.agents = {first.id: first, terminal.id: terminal, second.id: second}
        state.active_session_id = terminal.session_id

        ordered = self.keybindings_mod.get_ordered_cells(state, "window-a")

        self.assertEqual([cell.id for cell in ordered], ["agent-1", "term-1"])
        current_agent, is_on_terminal = self.keybindings_mod._find_current_agent(state)
        self.assertEqual(current_agent.id, "agent-1")
        self.assertTrue(is_on_terminal)

    async def test_install_and_remove_preserve_displaced_bindings(self):
        displaced = self.binding_mod.KeyBinding(
            character=0xF701,
            modifiers=[
                self.keyboard_mod.Modifier.COMMAND,
                self.keyboard_mod.Modifier.OPTION,
                self.keyboard_mod.Modifier.FUNCTION,
            ],
            keycode=self.keyboard_mod.Keycode.DOWN_ARROW,
            action=self.binding_mod.BindingAction.INVOKE_SCRIPT_FUNCTION,
            param="user_defined()",
        )
        unrelated = self.binding_mod.KeyBinding(
            character=ord("X"),
            modifiers=[self.keyboard_mod.Modifier.SHIFT],
            keycode=self.keyboard_mod.Keycode.ANSI_A,
            action=self.binding_mod.BindingAction.INVOKE_SCRIPT_FUNCTION,
            param="other()",
        )
        self.stored["bindings"] = [displaced, unrelated]

        saved = await self.keybindings_mod.install(object())

        self.assertEqual(saved, [displaced])
        installed = self.stored["bindings"]
        self.assertIn(unrelated, installed)
        self.assertTrue(any(
            binding.param == "loom_focus_next()"
            for binding in installed
        ))

        await self.keybindings_mod.remove(object(), displaced=saved)

        self.assertEqual(self.stored["bindings"], [unrelated, displaced])

    async def test_build_close_confirmation_message_describes_terminals_and_worktree_loss(self):
        state = self.state_mod.MatrixState()
        state._children = {"agent-1": ["term-1"]}

        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/wt",
            worktree_checkpoints=2,
            worktree_dirty=True,
        )
        terminal = self.state_mod.AgentCell(
            id="term-1",
            name="Logs",
            group="g",
            slug="worker:logs",
            cell_type="terminal",
            parent_id="agent-1",
        )
        state.agents = {cell.id: cell, terminal.id: terminal}

        msg = self.keybindings_mod.build_close_cell_confirmation_message(
            state, cell)

        self.assertEqual(
            msg,
            'Remove "Worker" and its 1 terminal(s)? '
            'Its worktree has unmerged commits and has uncommitted changes. '
            'All changes will be lost.',
        )

    async def test_build_close_confirmation_message_describes_shared_worktree(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/shared",
        )
        shared = self.state_mod.AgentCell(
            id="agent-2",
            name="Peer",
            group="g",
            slug="peer",
            cell_type="agent",
            worktree_path="/tmp/shared",
        )
        state.agents = {cell.id: cell, shared.id: shared}

        msg = self.keybindings_mod.build_close_cell_confirmation_message(
            state, cell)

        self.assertEqual(
            msg,
            'Remove "Worker"? Its worktree is shared with "Peer" and will be kept.',
        )

    async def test_action_shortcuts_call_server_handlers_without_ws_clients(self):
        state = self.state_mod.MatrixState()
        state.groups = {"g": ["agent-1"]}
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Agent 1",
            group="g",
            slug="agent-1",
            cell_type="agent",
            session_id="session-1",
            window_id="window-a",
        )
        state.agents = {agent.id: agent}
        state.active_session_id = agent.session_id
        state.current_window_id = agent.window_id

        class Bridge:
            async def focus_session(self, _session_id):
                return None

        calls = []

        async def add_agent_handler(*, group, name=""):
            calls.append(("add_agent", group, name))

        async def add_terminal_handler(*, group, parent_id, name=""):
            calls.append(("add_terminal", group, parent_id, name))

        async def close_cell_handler(cell):
            calls.append(("close_cell", cell.id))

        await self.keybindings_mod.setup(
            object(),
            state,
            Bridge(),
            add_agent_handler=add_agent_handler,
            add_terminal_handler=add_terminal_handler,
            close_cell_handler=close_cell_handler,
        )

        await self.stored["rpcs"]["loom_add_agent"]()
        await self.stored["rpcs"]["loom_add_terminal"]()
        await self.stored["rpcs"]["loom_close_cell"]()

        self.assertEqual(
            calls,
            [
                ("add_agent", "g", "Agent 2"),
                ("add_terminal", "g", "agent-1", "Terminal 1"),
                ("close_cell", "agent-1"),
            ],
        )
