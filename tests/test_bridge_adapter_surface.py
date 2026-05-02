import importlib
import sys
import types
import unittest


def _install_test_stubs():
    iterm2 = types.ModuleType("iterm2")
    iterm2.Connection = object

    class PartialProfile:
        @staticmethod
        async def async_query(conn):
            return [
                types.SimpleNamespace(name="Ops"),
                types.SimpleNamespace(name="Default"),
                types.SimpleNamespace(name="Ops"),
                types.SimpleNamespace(name=""),
            ]

    class ToolModule:
        def __init__(self):
            self.calls = []

        async def async_register_web_view_tool(self, conn, **kwargs):
            self.calls.append(kwargs)

    iterm2.PartialProfile = PartialProfile
    iterm2.tool = ToolModule()
    sys.modules["iterm2"] = iterm2

    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class FakeSession:
    def __init__(self, session_id, variables=None):
        self.session_id = session_id
        self._variables = dict(variables or {})

    async def async_get_variable(self, name):
        return self._variables.get(name)


class FakeTab:
    def __init__(self, session):
        self.current_session = session


class FakeWindow:
    def __init__(self, window_id, session):
        self.window_id = window_id
        self.current_tab = FakeTab(session)


class FakeApp:
    def __init__(self, window):
        self.current_terminal_window = window
        self.current_window = window


class BridgeAdapterSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_test_stubs()
        self.bridge_mod = importlib.import_module("torque.bridge")
        self.bridge_mod = importlib.reload(self.bridge_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    async def test_list_profiles_returns_sorted_unique_names(self):
        bridge = self.bridge_mod.ITerm2Adapter(None, self.state_mod.MatrixState())
        profiles = await bridge.list_profiles()
        self.assertEqual(profiles, ["Default", "Ops"])

    async def test_get_launch_context_reads_current_session_metadata(self):
        session = FakeSession(
            "session-1",
            {"path": "/tmp/repo", "profileName": "Ops"},
        )
        window = FakeWindow("window-1", session)

        async def fake_get_app(conn):
            return FakeApp(window)

        self.bridge_mod.iterm2.async_get_app = fake_get_app
        state = self.state_mod.MatrixState()
        bridge = self.bridge_mod.ITerm2Adapter(None, state)

        ctx = await bridge.get_launch_context()

        self.assertEqual(ctx.current_path, "/tmp/repo")
        self.assertEqual(ctx.current_profile, "Ops")
        self.assertEqual(ctx.current_window_id, "window-1")
        self.assertEqual(ctx.active_session_id, "session-1")

    async def test_prime_input_ready_marks_session_as_ready(self):
        bridge = self.bridge_mod.ITerm2Adapter(None, self.state_mod.MatrixState())
        bridge.prime_input_ready("session-2")
        self.assertIn("session-2", bridge._input_ready_sessions)

    async def test_register_web_view_tool_delegates_to_iterm2(self):
        bridge = self.bridge_mod.ITerm2Adapter(None, self.state_mod.MatrixState())
        registered = await bridge.register_web_view_tool(
            display_name="Torque",
            identifier="com.torque.toolbelt",
            url="http://127.0.0.1:18932/",
        )

        self.assertTrue(registered)
        self.assertEqual(len(self.bridge_mod.iterm2.tool.calls), 1)
        self.assertEqual(
            self.bridge_mod.iterm2.tool.calls[0]["identifier"],
            "com.torque.toolbelt",
        )

    async def test_iterm2_capabilities_remain_classic_toolbelt_only(self):
        bridge = self.bridge_mod.ITerm2Adapter(None, self.state_mod.MatrixState())

        self.assertFalse(bridge.capabilities.supports_embedded_terminal)
        self.assertTrue(bridge.capabilities.supports_toolbelt_registration)
        self.assertTrue(bridge.capabilities.supports_focus_tracking)
