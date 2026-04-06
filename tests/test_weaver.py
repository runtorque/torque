import asyncio
import importlib
import sys
import time
import types
import unittest


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class FakeBridge:
    def __init__(self):
        self.sent = []

    async def send_text(self, session_id, text):
        self.sent.append(text)
        await asyncio.sleep(0)


class WeaverEventBufferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.weaver_mod = importlib.import_module("loom.weaver")
        self.weaver_mod = importlib.reload(self.weaver_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        group = "g"
        state.groups[group] = []
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            slug="weaver",
            group=group,
            cell_type="agent",
            session_id="session-1",
            status="running",
            activity="",
        )
        state.agents[weaver.id] = weaver
        state.group_settings[group] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.weaver_settings[group] = self.state_mod.WeaverSettings(group=group)
        return state, group, weaver

    async def test_simultaneous_flush_triggers_only_one_digest(self):
        state, group, weaver = self._make_state()
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._check_weaver_flush(weaver)

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("── Loom Digest (1 event)", bridge.sent[0])
        self.assertNotIn("Heartbeat", bridge.sent[0])

    async def test_overdue_idle_push_uses_digest_format(self):
        state, group, _ = self._make_state()
        active = self.state_mod.AgentCell(
            id="agent-2",
            name="Worker",
            slug="worker",
            group=group,
            cell_type="agent",
            activity="thinking",
        )
        state.agents[active.id] = active

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 301

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("── Loom Digest (0 events)", bridge.sent[0])
        self.assertIn("No new events since last digest.", bridge.sent[0])
        self.assertIn("Active: worker (thinking)", bridge.sent[0])
        self.assertNotIn("Heartbeat", bridge.sent[0])
