import asyncio
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def _make_cell(self, cell_id="agent-1", kind="worker"):
        return self.state_mod.AgentCell(
            id=cell_id,
            name="Agent",
            group="g",
            cell_type="agent",
            kind=kind,
            current_task_id="LOOM:1",
        )

    def _make_temp_db(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        return db

    async def test_panel_event_log_batches_sqlite_flush(self):
        db = self._make_temp_db()
        panel_log = self.events_mod.PanelEventLog(
            max_size=10,
            db=db,
            flush_max_events=3,
            flush_interval=60.0,
        )
        try:
            panel_log.append(
                kind="task_dispatched",
                cell_id="agent-1",
                agent_name="Agent",
                group="g",
                message="one",
            )
            panel_log.append(
                kind="task_completed",
                cell_id="agent-2",
                agent_name="Agent",
                group="g",
                message="two",
            )

            # Appends are in-memory-only until the batch threshold/window
            # flushes them to SQLite.
            self.assertEqual(db.load_panel_events(limit=10), [])

            panel_log.append(
                kind="task_derived",
                cell_id="agent-3",
                agent_name="Agent",
                group="g",
                message="three",
            )
            await asyncio.wait_for(panel_log.flush(), timeout=2.0)

            persisted = db.load_panel_events(limit=10)
            self.assertEqual(
                [evt["message"] for evt in persisted],
                ["one", "two", "three"],
            )
        finally:
            await panel_log.aclose()

    async def test_panel_event_log_timer_flush_persists_within_window(self):
        db = self._make_temp_db()
        panel_log = self.events_mod.PanelEventLog(
            max_size=10,
            db=db,
            flush_max_events=50,
            flush_interval=0.02,
        )
        try:
            panel_log.append(
                kind="task_dispatched",
                cell_id="agent-1",
                agent_name="Agent",
                group="g",
                message="timer",
            )

            deadline = time.monotonic() + 0.5
            persisted = []
            while time.monotonic() < deadline:
                persisted = db.load_panel_events(limit=10)
                if persisted:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual([evt["message"] for evt in persisted], ["timer"])
        finally:
            await panel_log.aclose()

    async def test_panel_event_log_shutdown_flush_survives_restart(self):
        db = self._make_temp_db()
        panel_log = self.events_mod.PanelEventLog(
            max_size=10,
            db=db,
            flush_max_events=50,
            flush_interval=60.0,
        )
        panel_log.append(
            kind="task_dispatched",
            cell_id="agent-1",
            agent_name="Agent",
            group="g",
            message="shutdown",
        )
        self.assertEqual(db.load_panel_events(limit=10), [])

        await panel_log.aclose()

        restarted_panel_log = self.events_mod.PanelEventLog(max_size=10, db=db)
        try:
            self.assertEqual(
                [evt["message"] for evt in restarted_panel_log.get_recent()],
                ["shutdown"],
            )
        finally:
            await restarted_panel_log.aclose()

    async def test_panel_event_log_batches_replace_last_updates(self):
        db = self._make_temp_db()
        panel_log = self.events_mod.PanelEventLog(
            max_size=10,
            db=db,
            flush_max_events=50,
            flush_interval=60.0,
        )
        try:
            panel_log.append(
                kind="agent_progress",
                cell_id="agent-1",
                agent_name="Agent",
                group="g",
                message="first",
            )
            panel_log.replace_last(
                "agent_progress",
                "agent-1",
                agent_name="Agent",
                group="g",
                message="latest before insert",
            )
            await panel_log.flush()
            self.assertEqual(
                [evt["message"] for evt in db.load_panel_events(limit=10)],
                ["latest before insert"],
            )

            panel_log.replace_last(
                "agent_progress",
                "agent-1",
                agent_name="Agent",
                group="g",
                message="latest after insert",
            )
            self.assertEqual(
                [evt["message"] for evt in db.load_panel_events(limit=10)],
                ["latest before insert"],
            )
            await panel_log.flush()
            self.assertEqual(
                [evt["message"] for evt in db.load_panel_events(limit=10)],
                ["latest after insert"],
            )
        finally:
            await panel_log.aclose()

    def test_panel_event_log_forced_crash_mid_batch_loses_pending_queue(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "loom.db"
        repo_root = Path(__file__).resolve().parents[1]
        code = f"""
import asyncio
import os
from pathlib import Path

from loom.db import LoomDB
from loom.events import PanelEventLog

async def main():
    db = LoomDB(Path({str(db_path)!r}))
    db.init()
    panel_log = PanelEventLog(
        max_size=10,
        db=db,
        flush_max_events=50,
        flush_interval=60.0,
    )
    panel_log.append(
        kind="task_dispatched",
        cell_id="agent-1",
        agent_name="Agent",
        group="g",
        message="crash-pending",
    )
    os._exit(0)

asyncio.run(main())
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        from loom.db import LoomDB

        db = LoomDB(db_path)
        db.init()
        self.addCleanup(db.close)
        self.assertEqual(db.load_panel_events(limit=10), [])

    def test_architect_cell_event_stream_survives_restart_via_panel_events(self):
        cell = self._make_cell("arch-1", "architect")
        event_log = self.events_mod.EventLog()

        class FakeDB:
            def load_panel_events(self, limit=50, before_id=0, cell_id=""):
                self.args = (limit, before_id, cell_id)
                return [
                    {
                        "id": 7,
                        "timestamp": 10.0,
                        "kind": "task_dispatched",
                        "cell_id": cell_id,
                        "agent_name": "Architect",
                        "group": "g",
                        "message": "Persisted dispatch",
                        "task_id": "LOOM:7",
                    }
                ]

        db = FakeDB()
        events = self.events_mod.get_cell_event_stream(
            cell, event_log, db=db, limit=20)

        self.assertEqual(db.args, (20, 0, "arch-1"))
        self.assertEqual([evt["message"] for evt in events],
                         ["Persisted dispatch"])
        self.assertEqual(events[0]["source"], "panel_events")

    def test_worker_cell_event_stream_keeps_event_log_only_path(self):
        cell = self._make_cell("worker-1", "worker")
        event_log = self.events_mod.EventLog()
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=20.0,
                event_type="tool_start",
                data={"detail": "Running tests"},
            )
        )

        class FailingDB:
            def load_panel_events(self, **_kwargs):
                raise AssertionError("workers must not load panel_events")

        events = self.events_mod.get_cell_event_stream(
            cell, event_log, db=FailingDB(), limit=20)

        self.assertEqual([evt["kind"] for evt in events], ["tool_start"])
        self.assertEqual([evt["message"] for evt in events], ["Running tests"])
        self.assertEqual(events[0]["source"], "event_log")

    def test_persistent_cell_event_stream_merges_sources_by_timestamp(self):
        cell = self._make_cell("eng-1", "engineer")
        event_log = self.events_mod.EventLog()
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=20.0,
                event_type="tool_start",
                data={"detail": "Live tool"},
            )
        )
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=40.0,
                event_type="progress",
                data={"detail": "Live progress"},
            )
        )

        class FakeDB:
            def load_panel_events(self, limit=50, before_id=0, cell_id=""):
                return [
                    {
                        "id": 1,
                        "timestamp": 10.0,
                        "kind": "task_dispatched",
                        "cell_id": cell_id,
                        "agent_name": "Engineer",
                        "group": "g",
                        "message": "Persisted older",
                        "task_id": "LOOM:1",
                    },
                    {
                        "id": 2,
                        "timestamp": 30.0,
                        "kind": "task_completed",
                        "cell_id": cell_id,
                        "agent_name": "Engineer",
                        "group": "g",
                        "message": "Persisted middle",
                        "task_id": "LOOM:2",
                    },
                ]

        events = self.events_mod.get_cell_event_stream(
            cell, event_log, db=FakeDB(), limit=20)

        self.assertEqual(
            [evt["message"] for evt in events],
            ["Persisted older", "Live tool", "Persisted middle", "Live progress"],
        )
        self.assertEqual(
            [evt["source"] for evt in events],
            ["panel_events", "event_log", "panel_events", "event_log"],
        )

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

    async def test_session_end_marks_agent_idle_and_persists_status(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="claude-code",
            status="running",
            activity="thinking",
            activity_detail="Running tests",
        )
        state.agents[cell.id] = cell

        saved = []
        state._db_save_agent = lambda agent: saved.append((agent.id, agent.status))

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="session_end",
                data={"summary": "All done"},
            )
        )

        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.activity, "")
        self.assertEqual(cell.activity_detail, "")
        self.assertEqual(cell.last_event_text, "Session ended")
        self.assertEqual(cell.last_summary, "All done")
        self.assertEqual(saved, [("agent-1", "idle")])

    async def test_session_end_ignores_trailing_passive_activity_change(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="claude-code",
            status="running",
            activity="tool_call",
            activity_detail="Using subagent",
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="session_end",
                data={"summary": "Done"},
            )
        )
        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="activity_change",
                data={"activity": "thinking", "detail": ""},
            )
        )

        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.activity, "")
        self.assertEqual(cell.activity_detail, "")
        self.assertEqual(cell.last_event_text, "Session ended")

    async def test_session_end_ignores_trailing_tool_end(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="claude-code",
            status="running",
            activity="tool_call",
            activity_detail="Running tests",
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="session_end",
                data={"summary": "Done"},
            )
        )
        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="tool_end",
                data={"tool": "Bash", "success": True},
            )
        )

        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.activity, "")
        self.assertEqual(cell.activity_detail, "")

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

    async def test_activity_change_heartbeat_does_not_advance_progress_clock(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            last_progress_at=100.0,
            last_heartbeat_at=100.0,
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=200.0,
                event_type="activity_change",
                data={"activity": "thinking", "detail": "Still alive"},
            )
        )

        self.assertEqual(cell.last_progress_at, 100.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)

    async def test_progress_event_advances_progress_and_heartbeat_clocks(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            last_progress_at=100.0,
            last_heartbeat_at=150.0,
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=200.0,
                event_type="progress",
                data={"detail": "Tests are running"},
            )
        )

        self.assertEqual(cell.last_progress_at, 200.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)

    async def test_health_check_does_not_treat_heartbeat_alias_as_progress(self):
        state = self._make_state()
        base = time.time() - 301
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Never emitted progress",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
            created_at=self.task_health_iso(base),
            updated_at=self.task_health_iso(base),
        )
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
            current_task_id=task.id,
            idle_timeout=5,
            last_progress_at=0,
            last_heartbeat_at=time.time() - 1,
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task

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

        self.assertTrue(cell.needs_attention)
        self.assertIn("No activity for 5 minutes", cell.error_message)
        self.assertEqual(alerts, [("agent-1", "No activity for 5 minutes")])

    @staticmethod
    def task_health_iso(ts):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

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

    def test_engineer_queue_empty_emits_once_per_cycle_and_survives_restart(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Courier",
            group="g",
            cell_type="agent",
            kind="engineer",
            status="running",
            queue_empty_emitted=True,
        )
        engineer.mark_progress(1_000.0)
        state.agents[engineer.id] = engineer
        panel_log = self.events_mod.PanelEventLog()
        state.panel_log = panel_log
        bus = self.events_mod.EventBus(
            state,
            self.events_mod.EventLog(),
            panel_log=panel_log,
        )

        active_task = state.board_add_task(
            "Build feature",
            "g",
            lane="In Progress",
            id="task-1",
            assigned_engineer_id=engineer.id,
        )
        self.assertIsNotNone(active_task)
        self.assertFalse(engineer.queue_empty_emitted)

        # Active assigned work suppresses the empty event even after the idle
        # clock is old enough.
        changed = self.events_mod.check_engineer_queue_empty(
            state,
            bus,
            now=1_240.0,
        )
        self.assertFalse(changed)
        self.assertEqual(panel_log.get_recent(), [])

        # Once the queue drains, both "idle" and "empty" must be observed for
        # the debounce window before a single event fires.
        state.board_move_task("task-1", "Done")
        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                state,
                bus,
                now=1_240.0,
            )
        )
        self.assertTrue(
            self.events_mod.check_engineer_queue_empty(
                state,
                bus,
                now=1_360.0,
            )
        )
        events = panel_log.get_recent()
        self.assertEqual([evt["kind"] for evt in events],
                         ["engineer_queue_empty"])
        self.assertEqual(events[0]["cell_id"], engineer.id)
        self.assertTrue(engineer.queue_empty_emitted)

        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                state,
                bus,
                now=1_520.0,
            )
        )
        self.assertEqual(
            [evt["kind"] for evt in panel_log.get_recent()],
            ["engineer_queue_empty"],
        )

        # Simulate daemon restart after the empty event was persisted: the
        # in-memory debounce map is gone, but the persisted gate prevents boot
        # from re-firing.
        restarted = self._make_state()
        restarted_engineer = self.state_mod.AgentCell(
            id=engineer.id,
            name=engineer.name,
            group="g",
            cell_type="agent",
            kind="engineer",
            status="running",
            last_progress_at=engineer.last_progress_at,
            queue_empty_emitted=True,
        )
        restarted.agents[restarted_engineer.id] = restarted_engineer
        restarted_panel_log = self.events_mod.PanelEventLog()
        restarted.panel_log = restarted_panel_log
        restarted_bus = self.events_mod.EventBus(
            restarted,
            self.events_mod.EventLog(),
            panel_log=restarted_panel_log,
        )

        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                restarted,
                restarted_bus,
                now=2_000.0,
            )
        )
        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                restarted,
                restarted_bus,
                now=2_120.0,
            )
        )
        self.assertEqual(restarted_panel_log.get_recent(), [])

        second_task = restarted.board_add_task(
            "Build second feature",
            "g",
            lane="To Do",
            id="task-2",
            assigned_engineer_id=restarted_engineer.id,
        )
        self.assertIsNotNone(second_task)
        self.assertFalse(restarted_engineer.queue_empty_emitted)
        restarted_engineer.mark_progress(2_000.0)
        restarted.board_move_task("task-2", "Done")

        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                restarted,
                restarted_bus,
                now=2_120.0,
            )
        )
        self.assertTrue(
            self.events_mod.check_engineer_queue_empty(
                restarted,
                restarted_bus,
                now=2_240.0,
            )
        )
        self.assertEqual(
            [evt["kind"] for evt in restarted_panel_log.get_recent()],
            ["engineer_queue_empty"],
        )

    def test_engineer_queue_empty_debounce_counts_from_drain_transition(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Courier",
            group="g",
            cell_type="agent",
            kind="engineer",
            status="running",
            queue_empty_emitted=False,
        )
        engineer.mark_progress(1_000.0)
        state.agents[engineer.id] = engineer
        panel_log = self.events_mod.PanelEventLog()
        state.panel_log = panel_log
        bus = self.events_mod.EventBus(
            state,
            self.events_mod.EventLog(),
            panel_log=panel_log,
        )

        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                state,
                bus,
                now=1_000.0,
            )
        )
        self.assertFalse(
            self.events_mod.check_engineer_queue_empty(
                state,
                bus,
                now=1_119.0,
            )
        )
        self.assertTrue(
            self.events_mod.check_engineer_queue_empty(
                state,
                bus,
                now=1_120.0,
            )
        )
        self.assertEqual(
            [evt["kind"] for evt in panel_log.get_recent()],
            ["engineer_queue_empty"],
        )

    def test_engineer_queue_empty_gate_clears_when_owned_worker_spawns(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Courier",
            group="g",
            cell_type="agent",
            kind="engineer",
            queue_empty_emitted=True,
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
            status="running",
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker

        state._emit_agent(worker)

        self.assertFalse(engineer.queue_empty_emitted)

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
