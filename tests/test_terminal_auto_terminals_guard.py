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
        COMMAND = "COMMAND"
        OPTION = "OPTION"
        SHIFT = "SHIFT"
        CONTROL = "CONTROL"
        FUNCTION = "FUNCTION"

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


class _DummyBridge:
    async def list_profiles(self):
        return []

    async def get_launch_context(self):
        return types.SimpleNamespace(current_path="", current_profile="")

    async def reorder_tabs(self):
        return None


class TerminalAutoTerminalsGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.server_agent_mod = importlib.import_module("torque.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, **closure_overrides):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )
        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_panel_event": lambda *args, **kwargs: None,
            "_resolve_base_dir": self._resolve_base_dir,
            "_resolve_agent_launch_config": lambda *args, **kwargs: {},
            "_resolve_engineer_launch_config": lambda *args, **kwargs: {},
            "_send_agent_prompt": self._noop_send_agent_prompt,
            "_suggest_template_agent_name": (
                lambda group, template, base_dir="": "Template Agent"
            ),
            "_runtime_payload": lambda *args, **kwargs: {},
            "action_mgr": types.SimpleNamespace(
                list_actions=lambda _base_dir: [],
            ),
            "bridge": _DummyBridge(),
            "db": None,
            "handle_command": None,
            "specialization_mgr": types.SimpleNamespace(
                render_engineer_preamble=lambda *_a, **_k: "",
            ),
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
        })
        closure_values.update(closure_overrides)
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

    async def _resolve_base_dir(self, group=""):
        return ""

    async def _noop_send_agent_prompt(self, *args, **kwargs):
        return None

    @staticmethod
    async def _fake_create_agent_with_config(state, group, name, launch_cfg,
                                             **_kwargs):
        return state.add_agent(
            name=name,
            group=group,
            terminal_backend="pty",
            profile=launch_cfg.get("profile", "Default"),
            command=launch_cfg.get("command", "codex"),
            directory=launch_cfg.get("directory", "/repo"),
            tab_color=launch_cfg.get("tab_color", ""),
            icon=launch_cfg.get("icon", ""),
        )

    def _state_with_hidden_auto_terminals(self, count=2):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", auto_terminals=count)
        return state

    async def test_add_agent_ignores_hidden_auto_terminals_without_explicit_companions(self):
        state = self._state_with_hidden_auto_terminals(count=2)
        child_terminal_calls = []

        async def create_child_terminals(*args, **kwargs):
            child_terminal_calls.append({"args": args, "kwargs": kwargs})
            return []

        launch_cfg = {
            "profile": "Default",
            "command": "codex",
            "directory": "/repo",
            "tab_color": "",
            "terminals": [],
        }
        handle_command = self._extract_handle_command(
            state,
            _create_agent_with_config=(
                lambda group, name, cfg, **kwargs:
                self._fake_create_agent_with_config(
                    state, group, name, cfg, **kwargs)
            ),
            _create_child_terminals=create_child_terminals,
            _resolve_agent_launch_config=lambda *args, **kwargs: launch_cfg,
        )

        await handle_command({
            "cmd": "add_agent",
            "group": "g",
            "name": "Worker",
        })

        self.assertEqual(child_terminal_calls, [])
        self.assertEqual(
            [cell.name for cell in state.agents.values()],
            ["Worker"],
        )
        self.assertFalse(
            any(cell.cell_type == "terminal" for cell in state.agents.values())
        )

    async def test_add_agent_preserves_explicit_companion_terminals(self):
        state = self._state_with_hidden_auto_terminals(count=2)
        child_terminal_calls = []

        async def create_child_terminals(group, parent_cell, *,
                                         terminals=None, count=0):
            child_terminal_calls.append({
                "group": group,
                "parent_id": parent_cell.id,
                "terminals": terminals,
                "count": count,
            })
            created = []
            for spec in terminals or []:
                terminal = state.add_terminal(
                    name=spec.get("name") or "Terminal",
                    group=group,
                    terminal_backend="pty",
                    command=spec.get("command", ""),
                    directory=spec.get("directory") or parent_cell.directory,
                    parent_id=parent_cell.id,
                )
                created.append(terminal)
            return created

        launch_cfg = {
            "profile": "Default",
            "command": "codex",
            "directory": "/repo",
            "tab_color": "",
            "terminals": [
                {
                    "name": "Logs",
                    "command": "tail -f app.log",
                },
            ],
        }
        handle_command = self._extract_handle_command(
            state,
            _create_agent_with_config=(
                lambda group, name, cfg, **kwargs:
                self._fake_create_agent_with_config(
                    state, group, name, cfg, **kwargs)
            ),
            _create_child_terminals=create_child_terminals,
            _resolve_agent_launch_config=lambda *args, **kwargs: launch_cfg,
        )

        await handle_command({
            "cmd": "add_agent",
            "group": "g",
            "name": "Worker",
        })

        self.assertEqual(len(child_terminal_calls), 1)
        self.assertEqual(child_terminal_calls[0]["terminals"],
                         launch_cfg["terminals"])
        self.assertEqual(child_terminal_calls[0]["count"], 0)
        terminals = [
            cell for cell in state.agents.values()
            if cell.cell_type == "terminal"
        ]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0].name, "Logs")
        self.assertEqual(terminals[0].command, "tail -f app.log")
        self.assertEqual(terminals[0].parent_id,
                         child_terminal_calls[0]["parent_id"])

    async def test_create_child_terminals_keeps_explicit_pty_runtime_path(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        parent = state.add_agent(
            name="Worker",
            group="g",
            terminal_backend="pty",
            profile="Default",
            command="codex",
            directory="/repo",
        )
        bridge = types.SimpleNamespace(create_session_calls=[])

        async def create_session(cell, **kwargs):
            bridge.create_session_calls.append({
                "cell": cell,
                "kwargs": kwargs,
            })

        bridge.create_session = create_session
        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=types.SimpleNamespace(),
        )

        created = await service.create_child_terminals(
            "g",
            parent,
            terminals=[
                {
                    "name": "Runner",
                    "command": "npm test",
                    "directory": "/repo/app",
                    "init_script": "echo ready",
                },
            ],
        )

        self.assertEqual(len(created), 1)
        terminal = created[0]
        self.assertEqual(terminal.cell_type, "terminal")
        self.assertEqual(terminal.terminal_backend, "pty")
        self.assertEqual(terminal.parent_id, parent.id)
        self.assertEqual(terminal.command, "npm test")
        self.assertEqual(terminal.directory, "/repo/app")
        self.assertEqual(len(bridge.create_session_calls), 1)
        self.assertIs(bridge.create_session_calls[0]["cell"], terminal)
        self.assertEqual(
            bridge.create_session_calls[0]["kwargs"]["init_script"],
            "echo ready",
        )
