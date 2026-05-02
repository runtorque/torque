import json
from enum import Enum, auto
import importlib
import sys
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


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
        ANSI_E = auto()
        ANSI_P = auto()
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

    class Reference:
        def __init__(self, name):
            self.name = name

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
    iterm2.Reference = Reference
    iterm2.RPC = RPC
    iterm2.Connection = object

    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard
    return stored, binding, keyboard


class KeybindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.stored, self.binding_mod, self.keyboard_mod = _install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.keybindings_mod = importlib.import_module("torque.keybindings")
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

    async def test_engineer_hierarchy_drives_navigation_order(self):
        state = self.state_mod.MatrixState()
        state.groups = {"torque": ["eng-a", "worker-a", "eng-b", "worker-b", "user-1"]}
        state._children = {"eng-a": [], "worker-a": ["term-1"], "eng-b": [], "worker-b": [], "user-1": []}

        engineer_a = self.state_mod.AgentCell(
            id="eng-a",
            name="Alice",
            kind="engineer",
            group="torque",
            slug="alice",
            cell_type="agent",
            session_id="session-eng-a",
            window_id="window-a",
        )
        worker_a = self.state_mod.AgentCell(
            id="worker-a",
            name="Worker A",
            group="torque",
            slug="worker-a",
            cell_type="agent",
            session_id="session-worker-a",
            window_id="window-a",
            owner_engineer_id="eng-a",
        )
        engineer_b = self.state_mod.AgentCell(
            id="eng-b",
            name="Bob",
            kind="engineer",
            group="torque",
            slug="bob",
            cell_type="agent",
            session_id="session-eng-b",
            window_id="window-a",
        )
        worker_b = self.state_mod.AgentCell(
            id="worker-b",
            name="Worker B",
            group="torque",
            slug="worker-b",
            cell_type="agent",
            session_id="session-worker-b",
            window_id="window-a",
            owner_engineer_id="eng-b",
        )
        user_worker = self.state_mod.AgentCell(
            id="user-1",
            name="User Worker",
            group="torque",
            slug="user-worker",
            cell_type="agent",
            session_id="session-user-1",
            window_id="window-a",
        )
        terminal = self.state_mod.AgentCell(
            id="term-1",
            name="Logs",
            group="torque",
            slug="worker-a:logs",
            cell_type="terminal",
            session_id="session-term",
            window_id="window-a",
            parent_id="worker-a",
        )
        state.agents = {
            engineer_a.id: engineer_a,
            worker_a.id: worker_a,
            engineer_b.id: engineer_b,
            worker_b.id: worker_b,
            user_worker.id: user_worker,
            terminal.id: terminal,
        }

        ordered_agents = self.keybindings_mod.get_ordered_agents(state, "window-a")
        ordered_cells = self.keybindings_mod.get_ordered_cells(state, "window-a")

        self.assertEqual(
            [cell.id for cell in ordered_agents],
            ["eng-a", "worker-a", "eng-b", "worker-b", "user-1"],
        )
        self.assertEqual(
            [cell.id for cell in ordered_cells],
            ["eng-a", "worker-a", "term-1", "eng-b", "worker-b", "user-1"],
        )

    async def test_engineer_hierarchy_preserves_manual_group_order_within_buckets(self):
        state = self.state_mod.MatrixState()
        state.groups = {
            "torque": [
                "worker-a-2",
                "eng-b",
                "worker-b-2",
                "eng-a",
                "worker-b-1",
                "user-1",
                "worker-a-1",
            ]
        }
        state._children = {
            "eng-a": [],
            "eng-b": [],
            "worker-a-1": [],
            "worker-a-2": [],
            "worker-b-1": [],
            "worker-b-2": [],
            "user-1": [],
        }

        engineer_a = self.state_mod.AgentCell(
            id="eng-a",
            name="Alice",
            kind="engineer",
            group="torque",
            slug="alice",
            cell_type="agent",
            session_id="session-eng-a",
            window_id="window-a",
        )
        engineer_b = self.state_mod.AgentCell(
            id="eng-b",
            name="Bob",
            kind="engineer",
            group="torque",
            slug="bob",
            cell_type="agent",
            session_id="session-eng-b",
            window_id="window-a",
        )
        worker_a_1 = self.state_mod.AgentCell(
            id="worker-a-1",
            name="Worker A1",
            group="torque",
            slug="worker-a-1",
            cell_type="agent",
            session_id="session-worker-a-1",
            window_id="window-a",
            owner_engineer_id="eng-a",
        )
        worker_a_2 = self.state_mod.AgentCell(
            id="worker-a-2",
            name="Worker A2",
            group="torque",
            slug="worker-a-2",
            cell_type="agent",
            session_id="session-worker-a-2",
            window_id="window-a",
            owner_engineer_id="eng-a",
        )
        worker_b_1 = self.state_mod.AgentCell(
            id="worker-b-1",
            name="Worker B1",
            group="torque",
            slug="worker-b-1",
            cell_type="agent",
            session_id="session-worker-b-1",
            window_id="window-a",
            owner_engineer_id="eng-b",
        )
        worker_b_2 = self.state_mod.AgentCell(
            id="worker-b-2",
            name="Worker B2",
            group="torque",
            slug="worker-b-2",
            cell_type="agent",
            session_id="session-worker-b-2",
            window_id="window-a",
            owner_engineer_id="eng-b",
        )
        user_worker = self.state_mod.AgentCell(
            id="user-1",
            name="User Worker",
            group="torque",
            slug="user-worker",
            cell_type="agent",
            session_id="session-user-1",
            window_id="window-a",
        )
        state.agents = {
            engineer_a.id: engineer_a,
            engineer_b.id: engineer_b,
            worker_a_1.id: worker_a_1,
            worker_a_2.id: worker_a_2,
            worker_b_1.id: worker_b_1,
            worker_b_2.id: worker_b_2,
            user_worker.id: user_worker,
        }

        ordered_agents = self.keybindings_mod.get_ordered_agents(state, "window-a")

        self.assertEqual(
            [cell.id for cell in ordered_agents],
            [
                "eng-b",
                "worker-b-2",
                "worker-b-1",
                "eng-a",
                "worker-a-2",
                "worker-a-1",
                "user-1",
            ],
        )

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
            binding.param == "torque_focus_next()"
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
            kind="worker",
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
            'Delete "Worker" and its 1 terminal(s)? '
            'The worker and its terminal(s) will be scheduled for permanent '
            'deletion in 7 days — you can restore it from Recently deleted '
            'before then. Its worktree has unmerged commits and uncommitted '
            'changes. The worktree is preserved during the 7-day restore '
            'window; if not restored, all changes will be lost.',
        )

    async def test_build_close_confirmation_message_describes_shared_worktree(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            kind="worker",
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
            'Delete "Worker"? The worker will be scheduled for permanent '
            'deletion in 7 days — you can restore it from Recently deleted '
            'before then. '
            'Its worktree is shared with "Peer" and will be kept.',
        )

    async def test_build_close_confirmation_message_engineer_transfers_owned_work(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            group="g",
            slug="engineer",
            cell_type="agent",
            kind="engineer",
        )
        state.agents = {engineer.id: engineer}

        msg = self.keybindings_mod.build_close_cell_confirmation_message(
            state, engineer)

        self.assertIn('Delete "Engineer"?', msg)
        self.assertIn(
            "Owned workers and assigned tasks will be transferred to the user",
            msg,
        )
        self.assertIn("permanent deletion in 7 days", msg)
        self.assertIn("Recently deleted", msg)

    async def test_build_close_confirmation_message_architect_transfers_and_archives(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            slug="architect",
            cell_type="agent",
            kind="architect",
        )
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            group="g",
            slug="engineer",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        state.agents = {architect.id: architect, engineer.id: engineer}
        state.decisions = {
            "dec-1": {"id": "dec-1", "architect_id": "arch-1"},
        }

        msg = self.keybindings_mod.build_close_cell_confirmation_message(
            state, architect)

        self.assertIn('Delete "Architect"?', msg)
        self.assertIn("1 hired engineer will be transferred to the user", msg)
        self.assertIn("1 decision will be archived", msg)
        self.assertIn("permanent deletion in 7 days", msg)
        self.assertIn("Recently deleted", msg)

    async def test_build_close_confirmation_message_direct_terminal_is_immediate(self):
        state = self.state_mod.MatrixState()
        terminal = self.state_mod.AgentCell(
            id="term-1",
            name="Logs",
            group="g",
            slug="logs",
            cell_type="terminal",
        )
        state.agents = {terminal.id: terminal}

        msg = self.keybindings_mod.build_close_cell_confirmation_message(
            state, terminal)

        self.assertEqual(
            msg,
            'Delete "Logs"? This terminal will close.',
        )
        self.assertNotIn("Recently deleted", msg)

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

        async def add_agent_handler(*, group, name="", target_session_id=""):
            calls.append(("add_agent", group, name, target_session_id))

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

        await self.stored["rpcs"]["torque_add_agent"]()
        await self.stored["rpcs"]["torque_add_terminal"]()
        await self.stored["rpcs"]["torque_close_cell"]()

        self.assertEqual(
            calls,
            [
                ("add_agent", "g", "Agent 2", ""),
                ("add_terminal", "g", "agent-1", "Terminal 1"),
                ("close_cell", "agent-1"),
            ],
        )

    async def test_action_shortcuts_use_invocation_session_not_stale_active_session(self):
        state = self.state_mod.MatrixState()
        state.groups = {"torque": ["torque-agent"], "other": ["other-agent"]}
        torque_agent = self.state_mod.AgentCell(
            id="torque-agent",
            name="Torque Agent",
            group="torque",
            slug="torque-agent",
            cell_type="agent",
            session_id="session-torque",
            window_id="window-torque",
        )
        other_agent = self.state_mod.AgentCell(
            id="other-agent",
            name="Other Agent",
            group="other",
            slug="other-agent",
            cell_type="agent",
            session_id="session-other",
            window_id="window-other",
        )
        state.agents = {torque_agent.id: torque_agent, other_agent.id: other_agent}
        state.active_session_id = other_agent.session_id
        state.current_window_id = other_agent.window_id

        class Bridge:
            async def focus_session(self, _session_id):
                return None

        calls = []

        async def add_agent_handler(*, group, name="", target_session_id=""):
            calls.append(("add_agent", group, name, target_session_id))

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

        await self.stored["rpcs"]["torque_add_agent"](session_id=torque_agent.session_id)
        await self.stored["rpcs"]["torque_add_terminal"](session_id=torque_agent.session_id)
        await self.stored["rpcs"]["torque_close_cell"](session_id=torque_agent.session_id)

        self.assertEqual(
            calls,
            [
                ("add_agent", "torque", "Agent 1", "session-torque"),
                ("add_terminal", "torque", "torque-agent", "Terminal 1"),
                ("close_cell", "torque-agent"),
            ],
        )

    async def test_add_engineer_shortcut_dispatches_frontend_action(self):
        state = self.state_mod.MatrixState()

        class Bridge:
            async def focus_session(self, _session_id):
                return None

        sent = []

        class FakeWs:
            async def send_str(self, payload):
                sent.append(json.loads(payload))

        state._ws_clients = {FakeWs()}

        await self.keybindings_mod.setup(object(), state, Bridge())

        await self.stored["rpcs"]["torque_add_engineer"]()

        defaults = self.keybindings_mod.get_default_bindings()
        self.assertIn("add_engineer", defaults)
        self.assertEqual(defaults["add_engineer"]["keycode"], "ANSI_E")
        self.assertEqual(defaults["add_engineer"]["label"], "Add engineer")
        self.assertEqual(
            sent,
            [{"type": "action", "action": "add_engineer"}],
        )

    async def test_add_architect_shortcut_dispatches_frontend_action(self):
        state = self.state_mod.MatrixState()
        state.groups = {"torque": ["agent-1"]}
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Agent 1",
            group="torque",
            slug="agent-1",
            cell_type="agent",
            session_id="session-1",
            window_id="window-a",
        )
        state.agents = {agent.id: agent}
        state.active_session_id = agent.session_id

        class Bridge:
            async def focus_session(self, _session_id):
                return None

        sent = []

        class FakeWs:
            async def send_str(self, payload):
                sent.append(json.loads(payload))

        state._ws_clients = {FakeWs()}

        await self.keybindings_mod.setup(object(), state, Bridge())

        await self.stored["rpcs"]["torque_add_architect"](
            session_id=agent.session_id,
        )

        defaults = self.keybindings_mod.get_default_bindings()
        self.assertIn("add_architect", defaults)
        self.assertEqual(defaults["add_architect"]["keycode"], "ANSI_P")
        self.assertEqual(defaults["add_architect"]["label"], "Add architect")
        self.assertEqual(
            sent,
            [{"type": "action", "action": "add_architect", "group": "torque"}],
        )

    async def test_navigation_rpcs_follow_engineer_hierarchy_order(self):
        state = self.state_mod.MatrixState()
        state.groups = {"torque": ["eng-a", "worker-a", "eng-b", "worker-b", "user-1"]}
        state._children = {"eng-a": [], "worker-a": ["term-1"], "eng-b": [], "worker-b": [], "user-1": []}

        engineer_a = self.state_mod.AgentCell(
            id="eng-a",
            name="Alice",
            kind="engineer",
            group="torque",
            slug="alice",
            cell_type="agent",
            session_id="session-eng-a",
            window_id="window-a",
        )
        worker_a = self.state_mod.AgentCell(
            id="worker-a",
            name="Worker A",
            group="torque",
            slug="worker-a",
            cell_type="agent",
            session_id="session-worker-a",
            window_id="window-a",
            owner_engineer_id="eng-a",
        )
        engineer_b = self.state_mod.AgentCell(
            id="eng-b",
            name="Bob",
            kind="engineer",
            group="torque",
            slug="bob",
            cell_type="agent",
            session_id="session-eng-b",
            window_id="window-a",
        )
        worker_b = self.state_mod.AgentCell(
            id="worker-b",
            name="Worker B",
            group="torque",
            slug="worker-b",
            cell_type="agent",
            session_id="session-worker-b",
            window_id="window-a",
            owner_engineer_id="eng-b",
        )
        user_worker = self.state_mod.AgentCell(
            id="user-1",
            name="User Worker",
            group="torque",
            slug="user-worker",
            cell_type="agent",
            session_id="session-user-1",
            window_id="window-a",
        )
        terminal = self.state_mod.AgentCell(
            id="term-1",
            name="Logs",
            group="torque",
            slug="worker-a:logs",
            cell_type="terminal",
            session_id="session-term",
            window_id="window-a",
            parent_id="worker-a",
        )
        state.agents = {
            engineer_a.id: engineer_a,
            worker_a.id: worker_a,
            engineer_b.id: engineer_b,
            worker_b.id: worker_b,
            user_worker.id: user_worker,
            terminal.id: terminal,
        }
        state.current_window_id = "window-a"

        focused = []

        class Bridge:
            async def focus_session(self, session_id):
                focused.append(session_id)

        await self.keybindings_mod.setup(object(), state, Bridge())

        state.active_session_id = worker_a.session_id
        await self.stored["rpcs"]["torque_focus_prev"]()
        self.assertEqual(focused.pop(), engineer_a.session_id)

        state.active_session_id = worker_a.session_id
        await self.stored["rpcs"]["torque_focus_next"]()
        self.assertEqual(focused.pop(), terminal.session_id)

        state.active_session_id = terminal.session_id
        await self.stored["rpcs"]["torque_prev_agent"]()
        self.assertEqual(focused.pop(), engineer_a.session_id)

        state.active_session_id = terminal.session_id
        await self.stored["rpcs"]["torque_next_agent"]()
        self.assertEqual(focused.pop(), engineer_b.session_id)
