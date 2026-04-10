import asyncio
import importlib
import time
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.events_mod = importlib.import_module("loom.events")
        self.events_mod = importlib.reload(self.events_mod)
        self.base_mod = importlib.import_module("loom.adapters.base")
        self.base_mod = importlib.reload(self.base_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        return state

    async def test_session_start_persists_session_id_and_calls_ready_callback(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
        )
        state.agents[cell.id] = cell

        event_log = self.events_mod.EventLog()
        bus = self.events_mod.EventBus(state, event_log)
        seen = []
        bus.on_session_start = lambda agent: seen.append(agent.id)

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="session_start",
                data={"session_id": "agent-session-1"},
            )
        )

        self.assertEqual(cell.agent_session_id, "agent-session-1")
        self.assertEqual(seen, ["agent-1"])
        self.assertEqual(event_log.get(cell.id)[0].event_type, "session_start")

    async def test_activity_change_clears_blocked_label_on_current_task(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            current_task_id="task-1",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Investigate regression",
            group="g",
            agent_id=cell.id,
            lane="In Progress",
            labels=["loom:blocked", "keep"],
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="activity_change",
                data={"activity": "thinking", "detail": "Reading logs"},
            )
        )

        self.assertEqual(cell.activity, "thinking")
        self.assertEqual(cell.activity_detail, "Reading logs")
        self.assertEqual(state.board_tasks[task.id].labels, ["keep"])

    async def test_health_check_flags_stuck_agent_and_unlinks_post_derive_task(self):
        state = self._make_state()
        parent = self.state_mod.BoardTask(
            id="task-parent",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
        )
        child = self.state_mod.BoardTask(
            id="task-child",
            task="Derived task",
            group="g",
            lane="To Do",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            labels=["loom:derived"],
            agent_id="agent-2",
        )
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
            current_task_id=parent.id,
            idle_timeout=5,
            last_event_at=time.time() - 301,
        )
        state.agents[cell.id] = cell
        state.board_tasks[parent.id] = parent
        state.board_tasks[child.id] = child

        alerts = []

        class Notifier:
            def on_health_alert(self, cell_id, message):
                alerts.append((cell_id, message))

        sleep_calls = {"count": 0}
        orig_sleep = self.events_mod.asyncio.sleep

        async def fake_sleep(_delay):
            sleep_calls["count"] += 1
            if sleep_calls["count"] > 1:
                raise asyncio.CancelledError()

        self.events_mod.asyncio.sleep = fake_sleep
        try:
            with self.assertRaises(asyncio.CancelledError):
                await self.events_mod.health_check(
                    state,
                    self.events_mod.EventLog(),
                    self.events_mod.EventBus(state, self.events_mod.EventLog()),
                    notifier=Notifier(),
                )
        finally:
            self.events_mod.asyncio.sleep = orig_sleep

        self.assertEqual(cell.current_task_id, "")
        self.assertTrue(cell.needs_attention)
        self.assertIn("No activity for 5 minutes", cell.error_message)
        self.assertEqual(alerts, [("agent-1", "No activity for 5 minutes")])

    async def test_idle_activity_change_does_not_unlink_plain_backlog_child(self):
        state = self._make_state()
        parent = self.state_mod.BoardTask(
            id="task-parent",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
        )
        child = self.state_mod.BoardTask(
            id="task-child",
            task="Draft follow-up",
            group="g",
            lane="Backlog",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            labels=["loom:derived"],
        )
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            current_task_id=parent.id,
            activity="thinking",
        )
        state.agents[cell.id] = cell
        state.board_tasks[parent.id] = parent
        state.board_tasks[child.id] = child

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="activity_change",
                data={"activity": "", "detail": ""},
            )
        )

        self.assertEqual(cell.current_task_id, parent.id)
