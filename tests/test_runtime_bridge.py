import types
import unittest

from torque.runtime_bridge import AgentRuntimeBridge
from torque.runner_backends import CODEX_SDK_READONLY_BACKEND
from torque.state import AgentCell, MatrixState


class FakePty:
    def __init__(self):
        self.calls = []
        self.capabilities = types.SimpleNamespace(supports_embedded_terminal=True)
        self.on_session_terminated = None
        self.on_agent_session_end_detected = None
        self.on_terminal_disconnected = None
        self.on_terminal_output = None

    async def start(self): self.calls.append(("start",))
    async def reconnect_orphans(self): self.calls.append(("reconnect_orphans",))
    async def shutdown(self): self.calls.append(("shutdown",))
    async def create_session(self, cell, **kwargs): self.calls.append(("create_session", cell.id, kwargs))
    async def close_session(self, session_id): self.calls.append(("close_session", session_id))
    async def focus_session(self, session_id, *, client_id=""):
        self.calls.append(("focus_session", session_id, client_id)); return True
    async def update_session(self, cell, old_name=""): self.calls.append(("update_session", cell.id, old_name))
    async def send_text(self, session_id, text, **kwargs): self.calls.append(("send_text", session_id, text, kwargs))
    async def write_input(self, session_id, data): self.calls.append(("write_input", session_id, data))
    async def reorder_tabs(self): self.calls.append(("reorder_tabs",))
    async def list_profiles(self): self.calls.append(("list_profiles",)); return ["Default"]
    async def get_launch_context(self): self.calls.append(("get_launch_context",)); return types.SimpleNamespace(current_path="/repo")
    def prime_input_ready(self, session_id): self.calls.append(("prime_input_ready", session_id))
    def signal_input_ready(self, cell_id): self.calls.append(("signal_input_ready", cell_id))
    async def register_web_view_tool(self, **kwargs): self.calls.append(("register_web_view_tool", kwargs)); return False
    async def resize_session(self, session_id, cols, rows): self.calls.append(("resize_session", session_id, cols, rows))
    def get_terminal_buffer(self, session_id): self.calls.append(("get_terminal_buffer", session_id)); return "buf"


class FakeSdk:
    def __init__(self):
        self.sessions = {}
        self.calls = []
        self.event_sink = None
        self.terminal_output = None
    async def create_session(self, cell, **kwargs): self.calls.append(("create_session", cell.id, kwargs)); cell.session_id = "sdk-1"
    async def close_session(self, session_id): self.calls.append(("close_session", session_id))
    async def send_text(self, session_id, text): self.calls.append(("send_text", session_id, text))
    async def reconnect_orphans(self): self.calls.append(("reconnect_orphans",))
    async def close_all(self): pass
    def get_terminal_buffer(self, session_id): self.calls.append(("get_terminal_buffer", session_id)); return "sdkbuf"


class RuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    def _state(self):
        state = MatrixState()
        pty_cell = AgentCell(id="pty", name="PTY", group="g", session_id="pty-1")
        sdk_cell = AgentCell(id="sdk", name="SDK", group="g", session_id="sdk-1", agent_type="codex", runner_backend=CODEX_SDK_READONLY_BACKEND)
        state.agents = {"pty": pty_cell, "sdk": sdk_cell}
        return state, pty_cell, sdk_cell

    async def test_pty_pass_through_and_sdk_noops(self):
        state, pty_cell, sdk_cell = self._state()
        pty = FakePty()
        sdk = FakeSdk()
        bridge = AgentRuntimeBridge(state, pty, sdk_runner=sdk)
        await bridge.create_session(pty_cell, env_vars={"A": "B"})
        await bridge.send_text("pty-1", "hello", settled_submit=True)
        self.assertEqual(pty.calls[0][0], "create_session")
        self.assertEqual(pty.calls[1], ("send_text", "pty-1", "hello", {"settled_submit": True}))
        await bridge.create_session(sdk_cell, sdk_system_prompt="sys")
        await bridge.write_input("sdk-1", "ignored")
        self.assertEqual(sdk.calls[0][0], "create_session")
        self.assertFalse(any(call[0] == "write_input" and call[1] == "sdk-1" for call in pty.calls))
        self.assertEqual(bridge.get_terminal_buffer("sdk-1"), "sdkbuf")

    async def test_reconnect_runs_sdk_before_pty(self):
        state, _, _ = self._state()
        pty = FakePty()
        sdk = FakeSdk()
        bridge = AgentRuntimeBridge(state, pty, sdk_runner=sdk)
        await bridge.reconnect_orphans()
        self.assertEqual(sdk.calls[0], ("reconnect_orphans",))
        self.assertEqual(pty.calls[0], ("reconnect_orphans",))
