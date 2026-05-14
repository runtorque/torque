import importlib
import sys
import types
import unittest


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


class FakeLineInfo:
    overflow = 0
    first_visible_line_number = 0
    mutable_area_height = 24


class FakeScreenLine:
    def __init__(self, string):
        self.string = string


class FakeSession:
    def __init__(self, session_id, screen_snapshots=None):
        self.session_id = session_id
        self.sent = []
        self.screen_snapshots = list(screen_snapshots or [])
        self.screen_reads = 0

    async def async_set_profile_properties(self, change):
        pass

    async def async_send_text(self, text):
        self.sent.append(text)

    async def async_get_variable(self, name):
        return None

    async def async_get_line_info(self):
        return FakeLineInfo()

    async def async_get_contents(self, first, count):
        if not self.screen_snapshots:
            return []
        idx = min(self.screen_reads, len(self.screen_snapshots) - 1)
        self.screen_reads += 1
        return [FakeScreenLine(line) for line in self.screen_snapshots[idx]]


class FakeTab:
    def __init__(self, session):
        self.current_session = session
        self.sessions = [session]


class FakeWindow:
    def __init__(self, session):
        self.window_id = "window-1"
        self.tabs = [FakeTab(session)]


class FakeApp:
    def __init__(self, session):
        window = FakeWindow(session)
        self.current_window = window
        self.windows = [window]


class BridgeSendTextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_test_stubs()
        self.bridge_mod = importlib.import_module("torque.bridge")
        self.bridge_mod = importlib.reload(self.bridge_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    async def _make_bridge(self, session):
        async def fake_get_app(conn):
            return FakeApp(session)

        self.bridge_mod.iterm2.async_get_app = fake_get_app
        state = self.state_mod.MatrixState()
        bridge = self.bridge_mod.ITerm2Adapter(None, state)
        return bridge, state

    async def test_send_text_multiline_sends_body_then_submit(self):
        session = FakeSession("session-1")
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="agent",
            group="g",
            cell_type="agent",
            session_id="session-1",
        )
        state.agents[cell.id] = cell

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        orig_sleep = self.bridge_mod.asyncio.sleep
        self.bridge_mod.asyncio.sleep = fake_sleep
        try:
            await bridge.send_text("session-1", "line one\nline two\r")
        finally:
            self.bridge_mod.asyncio.sleep = orig_sleep

        self.assertEqual(session.sent, ["line one\nline two", "\r"])
        self.assertEqual(delays, [0.3])

    async def test_send_text_claude_multiline_uses_newline_shortcut_then_submit(self):
        session = FakeSession("session-1")
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="agent",
            group="g",
            cell_type="agent",
            session_id="session-1",
            agent_type="claude-code",
            command="claude",
        )
        state.agents[cell.id] = cell
        bridge._input_ready_sessions.add("session-1")

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        orig_sleep = self.bridge_mod.asyncio.sleep
        self.bridge_mod.asyncio.sleep = fake_sleep
        try:
            await bridge.send_text("session-1", "line one\nline two\r")
        finally:
            self.bridge_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            session.sent,
            ["line one", "\n", "line two", "\r"],
        )
        self.assertEqual(delays, [0.3])

    async def test_send_text_single_line_submits_without_settled_delay_by_default(self):
        session = FakeSession("session-1")
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="agent",
            group="g",
            cell_type="agent",
            session_id="session-1",
        )
        state.agents[cell.id] = cell

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        orig_sleep = self.bridge_mod.asyncio.sleep
        self.bridge_mod.asyncio.sleep = fake_sleep
        try:
            await bridge.send_text(
                "session-1",
                "Proceed with the derived task you just created.\r",
            )
        finally:
            self.bridge_mod.asyncio.sleep = orig_sleep

        self.assertEqual(session.sent, [
            "Proceed with the derived task you just created.",
            "\r",
        ])
        self.assertEqual(delays, [])

    async def test_settled_submit_single_line_delays_without_screen_wait(self):
        session = FakeSession(
            "session-derive",
            screen_snapshots=[["Loading Codex..."]],
        )
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-derive",
            name="codex",
            group="g",
            cell_type="agent",
            session_id="session-derive",
            agent_type="codex",
            command="codex",
        )
        state.agents[cell.id] = cell
        bridge.prime_input_ready("session-derive")

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        orig_sleep = self.bridge_mod.asyncio.sleep
        self.bridge_mod.asyncio.sleep = fake_sleep
        try:
            await bridge.send_text(
                "session-derive",
                "Proceed with the derived task you just created.\r",
                settled_submit=True,
            )
        finally:
            self.bridge_mod.asyncio.sleep = orig_sleep

        self.assertEqual(session.sent, [
            "Proceed with the derived task you just created.",
            "\r",
        ])
        self.assertEqual(delays, [0.3])
        self.assertEqual(session.screen_reads, 0)

    async def test_send_text_waits_for_codex_ready_once(self):
        ready_screen = [
            "OpenAI Codex",
            "model: gpt-5.4 high",
            "directory: ~/repo",
            "› Ready",
        ]
        session = FakeSession(
            "session-2",
            screen_snapshots=[
                ["Loading Codex..."],
                ready_screen,
                ready_screen,
            ],
        )
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-2",
            name="codex",
            group="g",
            cell_type="agent",
            session_id="session-2",
            agent_type="codex",
            command="codex",
        )
        state.agents[cell.id] = cell

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        orig_sleep = self.bridge_mod.asyncio.sleep
        self.bridge_mod.asyncio.sleep = fake_sleep
        try:
            await bridge.send_text("session-2", "line one\nline two\r")
            first_reads = session.screen_reads
            await bridge.send_text("session-2", "follow up\r")
        finally:
            self.bridge_mod.asyncio.sleep = orig_sleep

        self.assertEqual(session.sent, [
            "line one\nline two",
            "\r",
            "follow up",
            "\r",
        ])
        self.assertEqual(first_reads, 3)
        self.assertEqual(session.screen_reads, first_reads)
        # delays: 2 poll-intervals to reach stable_polls=2,
        # post_ready_delay (codex 2.5s), submit-key multiline delay (0.3).
        self.assertEqual(delays, [0.25, 0.25, 2.5, 0.3])

    async def test_send_text_logs_warning_when_session_not_found(self):
        # TORQUE:165 observability — a missing iTerm2 session on dispatch
        # used to silently drop the prompt, producing DOA workers after
        # daemon restarts with zero log signal. Surface it.
        session = FakeSession("present-session")
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-x",
            name="ghost",
            group="g",
            cell_type="agent",
            session_id="missing-session",
            agent_type="claude-code",
            command="claude",
        )
        state.agents[cell.id] = cell

        with self.assertLogs("torque", level="WARNING") as logs:
            await bridge.send_text("missing-session", "some prompt body\r")

        joined = "\n".join(logs.output)
        self.assertIn("send_text dropped", joined)
        self.assertIn("missing-session", joined)
        self.assertIn("claude-code", joined)
        self.assertEqual(session.sent, [])  # no writes to any session

    async def test_reconnect_orphans_primes_awareness_input_ready(self):
        session = FakeSession("session-relinked")
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-relinked",
            name="engineer",
            group="g",
            cell_type="agent",
            session_id="session-relinked",
            status="stopped",
            agent_type="claude-code",
            command="claude",
        )
        state.agents[cell.id] = cell
        bridge._input_ready_events[cell.id] = self.bridge_mod.asyncio.Event()
        bridge._start_prompt_monitor = lambda cell: None

        async def noop_resolve(cell):
            pass

        bridge._resolve_git_info = noop_resolve

        await bridge.reconnect_orphans()

        self.assertEqual(cell.status, "idle")
        self.assertIn("session-relinked", bridge._input_ready_sessions)
        self.assertNotIn(cell.id, bridge._input_ready_events)

        async def fail_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise AssertionError(
                f"relinked session should not wait for readiness ({timeout})")

        orig_wait_for = self.bridge_mod.asyncio.wait_for
        self.bridge_mod.asyncio.wait_for = fail_wait_for
        try:
            await bridge.send_text("session-relinked", "hello")
        finally:
            self.bridge_mod.asyncio.wait_for = orig_wait_for

        self.assertEqual(session.sent, ["hello", "\r"])

    async def test_send_text_claude_does_not_release_on_startup_banner(self):
        session = FakeSession(
            "session-3",
            screen_snapshots=[
                ["Claude Code", "claude.ai/code", "Starting..."],
                ["Claude Code", "claude.ai/code", "Starting..."],
            ],
        )
        bridge, state = await self._make_bridge(session)
        cell = self.state_mod.AgentCell(
            id="agent-3",
            name="engineer",
            group="g",
            cell_type="agent",
            session_id="session-3",
            agent_type="claude-code",
            command="claude",
        )
        state.agents[cell.id] = cell

        timeouts = []

        async def fake_wait_for(awaitable, timeout):
            timeouts.append(timeout)
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise self.bridge_mod.asyncio.TimeoutError

        orig_wait_for = self.bridge_mod.asyncio.wait_for
        self.bridge_mod.asyncio.wait_for = fake_wait_for
        try:
            await bridge.send_text("session-3", "hello engineer\r")
        finally:
            self.bridge_mod.asyncio.wait_for = orig_wait_for

        self.assertEqual(session.sent, ["hello engineer", "\r"])
        self.assertEqual(session.screen_reads, 0)
        self.assertEqual(timeouts, [30.0])
