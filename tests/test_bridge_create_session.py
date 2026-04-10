import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _install_test_stubs():
    iterm2 = types.ModuleType("iterm2")
    iterm2.Connection = object

    class Color:
        def __init__(self, r, g, b):
            self.r = r
            self.g = g
            self.b = b

    class LocalWriteOnlyProfile:
        def set_use_tab_color(self, value):
            pass

        def set_use_tab_color_light(self, value):
            pass

        def set_use_tab_color_dark(self, value):
            pass

        def set_tab_color(self, value):
            pass

        def set_tab_color_light(self, value):
            pass

        def set_tab_color_dark(self, value):
            pass

    iterm2.Color = Color
    iterm2.LocalWriteOnlyProfile = LocalWriteOnlyProfile
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
    def __init__(self, session_id):
        self.session_id = session_id
        self.sent = []

    async def async_set_profile_properties(self, change):
        pass

    async def async_send_text(self, text):
        self.sent.append(text)

    async def async_get_variable(self, name):
        return None


class FakePrevTab:
    def __init__(self):
        self.select_calls = 0

    async def async_select(self):
        self.select_calls += 1


class FakeTab:
    def __init__(self, session):
        self.current_session = session
        self.sessions = [session]
        self.title = ""

    async def async_set_title(self, title):
        self.title = title


class FakeWindow:
    def __init__(self, window_id, current_tab=None):
        self.window_id = window_id
        self.current_tab = current_tab or FakePrevTab()
        self.created_profiles = []
        self.tabs = []

    async def async_create_tab(self, profile=None):
        self.created_profiles.append(profile)
        tab = FakeTab(FakeSession(f"session-{self.window_id}"))
        self.tabs.append(tab)
        return tab


class FakeApp:
    def __init__(self, current_window, windows):
        self.current_window = current_window
        self.windows = windows


class CreateSessionWindowTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_test_stubs()
        self.bridge_mod = importlib.import_module("loom.bridge")
        self.state_mod = importlib.import_module("loom.state")

    async def _make_bridge(self, app):
        async def fake_get_app(conn):
            return app

        self.bridge_mod.iterm2.async_get_app = fake_get_app

        state = self.state_mod.MatrixState()
        state.groups = {"g": ["parent"]}
        bridge = self.bridge_mod.ITerm2Adapter(None, state)

        async def noop(*args, **kwargs):
            return None

        bridge._resolve_git_info = noop
        bridge._start_prompt_monitor = lambda cell: None
        bridge._start_terminal_monitors = lambda cell: None
        bridge.reorder_tabs = noop
        return bridge, state

    async def test_create_session_prefers_target_session_window(self):
        focused = FakeWindow("focused-window")
        source = FakeWindow("source-window")
        source.tabs.append(FakeTab(FakeSession("parent-session")))
        bridge, state = await self._make_bridge(
            FakeApp(focused, [focused, source])
        )

        child = self.state_mod.AgentCell(
            id="child",
            name="child",
            group="g",
            cell_type="agent",
            profile="Default",
            command="echo hi",
            directory="",
        )

        await bridge.create_session(child, target_session_id="parent-session")

        self.assertEqual(child.window_id, "source-window")
        self.assertEqual(source.created_profiles, ["Default"])
        self.assertEqual(focused.created_profiles, [])

    async def test_create_session_uses_parent_window_for_child_terminals(self):
        focused = FakeWindow("focused-window")
        source = FakeWindow("source-window")
        source.tabs.append(FakeTab(FakeSession("parent-session")))
        bridge, state = await self._make_bridge(
            FakeApp(focused, [focused, source])
        )

        parent = self.state_mod.AgentCell(
            id="parent",
            name="parent",
            group="g",
            cell_type="agent",
            profile="Default",
            command="echo parent",
            directory="",
        )
        parent.session_id = "parent-session"
        parent.window_id = "source-window"
        state.agents[parent.id] = parent

        child = self.state_mod.AgentCell(
            id="child",
            name="child",
            group="g",
            cell_type="terminal",
            profile="Default",
            command="",
            directory="",
            parent_id="parent",
        )

        await bridge.create_session(child)

        self.assertEqual(child.window_id, "source-window")
        self.assertEqual(source.created_profiles, ["Default"])
        self.assertEqual(focused.created_profiles, [])

    async def test_create_session_uses_launch_context_directory_for_blank_agent_dir(self):
        focused = FakeWindow("focused-window")
        bridge, _state = await self._make_bridge(FakeApp(focused, [focused]))
        terminal_adapter = importlib.import_module("loom.terminal_adapter")

        with tempfile.TemporaryDirectory() as tmpdir:
            async def fake_get_launch_context():
                return terminal_adapter.TerminalLaunchContext(
                    current_path=tmpdir,
                )

            bridge.get_launch_context = fake_get_launch_context

            child = self.state_mod.AgentCell(
                id="child",
                name="child",
                group="g",
                cell_type="agent",
                profile="Default",
                command="",
                directory="",
                agent_type="claude-code",
            )

            await bridge.create_session(child)

            session = focused.tabs[0].current_session
            self.assertEqual(child.directory, tmpdir)
            self.assertTrue(
                any(
                    sent.startswith("cd ")
                    and tmpdir in sent
                    for sent in session.sent
                )
            )
            self.assertTrue(
                (Path(tmpdir) / ".claude" / "settings.local.json").exists()
            )

    async def test_dispatch_created_tab_restores_currently_selected_tab(self):
        focused_tab = FakePrevTab()
        source_tab = FakePrevTab()
        focused = FakeWindow("focused-window", current_tab=focused_tab)
        source = FakeWindow("source-window", current_tab=source_tab)
        source.tabs.append(FakeTab(FakeSession("parent-session")))
        bridge, _state = await self._make_bridge(
            FakeApp(focused, [focused, source])
        )

        child = self.state_mod.AgentCell(
            id="child",
            name="child",
            group="g",
            cell_type="agent",
            profile="Default",
            command="echo hi",
            directory="",
        )

        await bridge.create_session(
            child,
            target_session_id="parent-session",
            restore_focus_to_prev_tab=True,
        )

        self.assertEqual(focused_tab.select_calls, 1)
        self.assertEqual(source_tab.select_calls, 0)

    async def test_create_session_leaves_focus_on_new_tab_when_enabled(self):
        focused_tab = FakePrevTab()
        focused = FakeWindow("focused-window", current_tab=focused_tab)
        bridge, _state = await self._make_bridge(FakeApp(focused, [focused]))

        child = self.state_mod.AgentCell(
            id="child",
            name="child",
            group="g",
            cell_type="agent",
            profile="Default",
            command="echo hi",
            directory="",
        )

        await bridge.create_session(child)

        self.assertEqual(focused_tab.select_calls, 0)

    async def test_create_session_preserves_existing_focus_new_tabs_setting(self):
        focused_tab = FakePrevTab()
        source_tab = FakePrevTab()
        focused = FakeWindow("focused-window", current_tab=focused_tab)
        source = FakeWindow("source-window", current_tab=source_tab)
        source.tabs.append(FakeTab(FakeSession("parent-session")))
        bridge, state = await self._make_bridge(
            FakeApp(focused, [focused, source])
        )
        state.global_settings.focus_new_tabs = False

        child = self.state_mod.AgentCell(
            id="child",
            name="child",
            group="g",
            cell_type="agent",
            profile="Default",
            command="echo hi",
            directory="",
        )

        await bridge.create_session(child, target_session_id="parent-session")

        self.assertEqual(source_tab.select_calls, 1)
        self.assertEqual(focused_tab.select_calls, 0)
