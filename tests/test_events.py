import asyncio
import importlib
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.events_mod = importlib.import_module("torque.events")
        self.events_mod = importlib.reload(self.events_mod)
        self.base_mod = importlib.import_module("torque.adapters.base")
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
            current_task_id="TORQUE:1",
        )

    def _make_temp_db(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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


    async def test_panel_event_log_retries_sqlite_lock_without_duplicates(self):
        db = self._make_temp_db()
        panel_log = self.events_mod.PanelEventLog(
            max_size=10,
            db=db,
            flush_max_events=1,
            flush_interval=60.0,
        )
        from torque.db import TorqueDB

        original = TorqueDB._save_panel_events_batch_on_conn
        calls = {"count": 0}

        def flaky(conn, events, updates, max_size):
            calls["count"] += 1
            if calls["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return original(conn, events, updates, max_size)

        try:
            with mock.patch.object(
                TorqueDB, "_save_panel_events_batch_on_conn", side_effect=flaky
            ):
                panel_log.append(
                    kind="task_dispatched",
                    cell_id="agent-1",
                    agent_name="Agent",
                    group="g",
                    message="retry-once",
                )
                await asyncio.wait_for(panel_log.flush(), timeout=2.0)

            persisted = db.load_panel_events(limit=10)
            self.assertEqual([evt["message"] for evt in persisted], ["retry-once"])
            self.assertEqual(len(persisted), 1)
            health = db.load_mcp_health_summary(since=0)
            self.assertEqual(
                health["surfaces"]["panel_events"]["events"].get("retry"),
                1,
            )
        finally:
            await panel_log.aclose()

    def test_panel_event_log_forced_crash_mid_batch_loses_pending_queue(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "torque.db"
        repo_root = Path(__file__).resolve().parents[1]
        code = f"""
import asyncio
import os
import sqlite3
from pathlib import Path

from torque.db import TorqueDB
from torque.events import PanelEventLog

async def main():
    db = TorqueDB(Path({str(db_path)!r}))
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

        from torque.db import TorqueDB

        db = TorqueDB(db_path)
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
                        "task_id": "TORQUE:7",
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
                        "task_id": "TORQUE:1",
                    },
                    {
                        "id": 2,
                        "timestamp": 30.0,
                        "kind": "task_completed",
                        "cell_id": cell_id,
                        "agent_name": "Engineer",
                        "group": "g",
                        "message": "Persisted middle",
                        "task_id": "TORQUE:2",
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

    async def test_non_session_start_event_can_persist_provider_session_id(self):
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

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=125.0,
                event_type="context_update",
                data={"session_id": "agent-session-from-live-hook"},
            )
        )

        self.assertEqual(cell.agent_session_id, "agent-session-from-live-hook")
        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(
            upserts[0]["agent_session_id"],
            "agent-session-from-live-hook",
        )

    async def test_codex_permission_request_marks_agent_waiting_for_approval(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
        )
        state.agents[cell.id] = cell

        codex_mod = importlib.import_module("torque.adapters.codex")
        codex_mod = importlib.reload(codex_mod)
        event = codex_mod.CodexAdapter().parse_event(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "apply_patch",
                "tool_input": {"patch": "*** Begin Patch\n*** End Patch\n"},
            },
            cell,
        )
        self.assertIsNotNone(event)

        panel_log = self.events_mod.PanelEventLog()
        bus = self.events_mod.EventBus(
            state,
            self.events_mod.EventLog(),
            panel_log=panel_log,
        )
        await bus.emit(event)

        self.assertEqual(cell.activity, "waiting")
        self.assertTrue(cell.needs_attention)
        self.assertEqual(
            cell.activity_detail,
            "Waiting for approval: Applying patch",
        )
        self.assertEqual(cell.last_event_text, cell.activity_detail)
        self.assertEqual(panel_log.get_recent()[-1]["kind"], "agent_waiting")

    async def test_context_window_updates_do_not_mutate_session_tokens(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            session_tokens_in=7,
            session_tokens_out=11,
        )
        state.agents[cell.id] = cell

        event_log = self.events_mod.EventLog()
        bus = self.events_mod.EventBus(state, event_log)

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=123.0,
                event_type="session_start",
                data={
                    "session_id": "provider-session-1",
                    "context_window": {
                        "source": "codex_transcript",
                        "model": "gpt-5.4",
                        "session_id": "provider-session-1",
                        "used_tokens": "50",
                        "limit_tokens": "200",
                    },
                },
            )
        )
        self.assertEqual(cell.context_window["used_tokens"], 50)
        self.assertEqual(cell.context_window["limit_tokens"], 200)
        self.assertEqual(cell.context_window["used_pct"], 25.0)
        self.assertEqual(cell.context_window["updated_at"], 123.0)

        # Prove context_update does NOT clobber the human-facing status line:
        # seed a meaningful prior status and confirm it survives the update.
        cell.last_event_text = "Using Edit"

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=124.0,
                event_type="context_update",
                data={
                    "context_window": {
                        "source": "codex_transcript",
                        "model": "gpt-5.4",
                        "session_id": "provider-session-1",
                        "used_tokens": 147732,
                        "limit_tokens": 258400,
                        "input_tokens": 147293,
                        "output_tokens": 439,
                        "cached_input_tokens": 139648,
                        "reasoning_output_tokens": 16,
                        "session_total_tokens": 1799981,
                    },
                },
            )
        )

        # The ephemeral context_window meter still updates (the WS delta path is
        # unaffected) ...
        self.assertEqual(cell.context_window["used_tokens"], 147732)
        self.assertEqual(cell.context_window["limit_tokens"], 258400)
        self.assertAlmostEqual(cell.context_window["used_pct"], 57.17, places=2)
        self.assertEqual(cell.context_window["input_tokens"], 147293)
        self.assertEqual(cell.context_window["output_tokens"], 439)
        self.assertEqual(cell.context_window["cached_input_tokens"], 139648)
        self.assertEqual(cell.context_window["reasoning_output_tokens"], 16)
        self.assertEqual(cell.context_window["session_total_tokens"], 1799981)
        # ... but context_update no longer surfaces the noisy "Context usage
        # updated" status line on the agent card: the prior status is preserved.
        self.assertEqual(cell.last_event_text, "Using Edit")
        self.assertNotEqual(cell.last_event_text, "Context usage updated")
        self.assertEqual(cell.session_tokens_in, 7)
        self.assertEqual(cell.session_tokens_out, 11)
        self.assertEqual(
            [event.event_type for event in event_log.get(cell.id)],
            ["session_start", "context_update"],
        )

        # The human-facing per-cell event feed excludes context_update telemetry
        # (the grid context meter renders the data directly).
        stream = self.events_mod.get_cell_event_stream(cell, event_log)
        self.assertNotIn(
            "context_update", [evt["kind"] for evt in stream]
        )
        self.assertNotIn(
            "Context usage updated", [evt["message"] for evt in stream]
        )

    async def test_provider_usage_context_updates_dedupe_and_coalesce(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="claude-code",
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=200.0,
                event_type="context_update",
                data={
                    "provider_usage": {
                        "five_hour": {
                            "available": True,
                            "used_percentage": 12.2,
                            "resets_at": "2026-05-26T05:00:00Z",
                        },
                        "seven_day": {
                            "available": False,
                            "used_percentage": None,
                            "resets_at": None,
                        },
                    },
                },
            )
        )

        self.assertEqual(cell.provider_usage["five_hour"]["used_percentage"], 12)
        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(
            upserts[0]["provider_usage"]["five_hour"]["used_percentage"],
            12,
        )

        # Same integer percentage + same reset time is not a meaningful update,
        # so no second delta is queued despite heartbeat clock churn.
        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=201.0,
                event_type="context_update",
                data={
                    "provider_usage": {
                        "five_hour": {
                            "available": True,
                            "used_percentage": 12.4,
                            "resets_at": "2026-05-26T05:00:00Z",
                        },
                        "seven_day": {
                            "available": False,
                            "used_percentage": None,
                            "resets_at": None,
                        },
                    },
                },
            )
        )
        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(cell.provider_usage["five_hour"]["used_percentage"], 12)

        # A meaningful integer-percent change replaces the pending telemetry
        # upsert instead of appending another one before the throttled broadcast.
        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=202.0,
                event_type="context_update",
                data={
                    "provider_usage": {
                        "five_hour": {
                            "available": True,
                            "used_percentage": 12.6,
                            "resets_at": "2026-05-26T05:00:00Z",
                        },
                        "seven_day": {
                            "available": False,
                            "used_percentage": None,
                            "resets_at": None,
                        },
                    },
                },
            )
        )

        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(
            upserts[0]["provider_usage"]["five_hour"]["used_percentage"],
            13,
        )
        self.assertEqual(cell.provider_usage["five_hour"]["used_percentage"], 13)

    async def test_session_start_without_context_clears_stale_context_window(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            agent_session_id="provider-session-1",
            context_window={
                "source": "codex_transcript",
                "model": "gpt-5.4",
                "session_id": "provider-session-1",
                "used_tokens": 90,
                "limit_tokens": 100,
                "used_pct": 90.0,
            },
        )
        state.agents[cell.id] = cell

        event_log = self.events_mod.EventLog()
        bus = self.events_mod.EventBus(state, event_log)

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=126.0,
                event_type="session_start",
                data={"session_id": "provider-session-2"},
            )
        )

        self.assertEqual(cell.agent_session_id, "provider-session-2")
        self.assertEqual(cell.context_window, {})
        self.assertEqual(event_log.get(cell.id)[0].event_type, "session_start")

    async def test_session_start_with_malformed_context_clears_stale_context_window(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            context_window={
                "source": "codex_transcript",
                "session_id": "provider-session-1",
                "used_tokens": 90,
                "limit_tokens": 100,
                "used_pct": 90.0,
            },
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=127.0,
                event_type="session_start",
                data={
                    "session_id": "provider-session-2",
                    "context_window": {
                        "source": "codex_transcript",
                        "session_id": "provider-session-2",
                        "used_tokens": 50,
                        "limit_tokens": 0,
                    },
                },
            )
        )

        self.assertEqual(cell.context_window, {})

    async def test_session_end_applies_context_window_update(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
        )
        state.agents[cell.id] = cell

        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())
        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=125.0,
                event_type="session_end",
                data={
                    "summary": "done",
                    "context_window": {
                        "source": "codex_transcript",
                        "used_tokens": 9,
                        "limit_tokens": 10,
                    },
                },
            )
        )

        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.last_summary, "done")
        self.assertEqual(cell.context_window["used_pct"], 90.0)

    def test_worker_boot_doa_escalates_when_worker_posts_no_activity(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
            current_task_id="task-1",
            owner_engineer_id="eng-1",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
            lane="In Progress",
            agent_id=cell.id,
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task
        event_log = self.events_mod.EventLog()
        panel_log = self.events_mod.PanelEventLog()
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=100.0,
                event_type="session_start",
                data={},
            )
        )
        panel_log.append(
            kind="agent_started",
            cell_id=cell.id,
            agent_name=cell.name,
            group=cell.group,
            message="",
        )
        panel_log.append(
            kind="task_dispatched",
            cell_id=cell.id,
            agent_name=cell.name,
            group=cell.group,
            message=task.task,
            task_id=task.id,
        )

        changed = self.events_mod.emit_worker_boot_doa_if_inactive(
            state,
            event_log,
            panel_log,
            cell,
            started_at=100.0,
            timeout_seconds=60.0,
            now=161.0,
        )

        self.assertTrue(changed)
        self.assertTrue(cell.needs_attention)
        self.assertIn("Worker boot DOA", cell.error_message)
        events = panel_log.get_recent()
        self.assertEqual(events[-1]["kind"], "worker_boot_doa")
        self.assertEqual(events[-1]["cell_id"], cell.id)
        self.assertEqual(events[-1]["task_id"], task.id)

    def test_worker_boot_doa_ignores_context_update_telemetry(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
            current_task_id="task-1",
            owner_engineer_id="eng-1",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
            lane="In Progress",
            agent_id=cell.id,
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task
        event_log = self.events_mod.EventLog()
        panel_log = self.events_mod.PanelEventLog()
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=100.0,
                event_type="session_start",
                data={},
            )
        )
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=120.0,
                event_type="context_update",
                data={
                    "context_window": {
                        "source": "claude_statusline",
                        "used_tokens": 50,
                        "limit_tokens": 200,
                    },
                },
            )
        )

        changed = self.events_mod.emit_worker_boot_doa_if_inactive(
            state,
            event_log,
            panel_log,
            cell,
            started_at=100.0,
            timeout_seconds=60.0,
            now=161.0,
        )

        self.assertTrue(changed)
        self.assertTrue(cell.needs_attention)
        self.assertEqual(panel_log.get_recent()[-1]["kind"], "worker_boot_doa")

    def test_worker_boot_doa_suppressed_after_post_boot_activity(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
            current_task_id="task-1",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
            lane="In Progress",
            agent_id=cell.id,
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task
        event_log = self.events_mod.EventLog()
        panel_log = self.events_mod.PanelEventLog()
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=100.0,
                event_type="session_start",
                data={},
            )
        )
        event_log.append(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=120.0,
                event_type="tool_start",
                data={"detail": "Running tests"},
            )
        )

        changed = self.events_mod.emit_worker_boot_doa_if_inactive(
            state,
            event_log,
            panel_log,
            cell,
            started_at=100.0,
            timeout_seconds=60.0,
            now=161.0,
        )

        self.assertFalse(changed)
        self.assertFalse(cell.needs_attention)
        self.assertEqual(panel_log.get_recent(), [])

    def _boot_doa_worker_state(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
            current_task_id="task-1",
            owner_engineer_id="eng-1",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
            lane="In Progress",
            agent_id=cell.id,
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task
        return state, cell, task

    async def test_worker_boot_doa_timer_escalates_on_scheduled_path(self):
        """End-to-end: the EventBus timer scheduled on session_start fires the
        escalation when the worker posts no activity within the window."""
        state, cell, task = self._boot_doa_worker_state()
        panel_log = self.events_mod.PanelEventLog()
        bus = self.events_mod.EventBus(
            state, self.events_mod.EventLog(), panel_log=panel_log)
        bus.start()

        with mock.patch.dict(
                os.environ, {self.events_mod.WORKER_BOOT_DOA_ENV: "0.05"}):
            await bus.emit(self.base_mod.AgentEvent(
                cell_id=cell.id, timestamp=0.0,
                event_type="session_start", data={}))
            # Timer is armed but escalation has not fired yet.
            self.assertIn(cell.id, bus._boot_doa_timers)
            self.assertFalse(cell.needs_attention)
            # Advance past the watchdog window with no worker activity.
            await asyncio.sleep(0.15)

        self.assertTrue(cell.needs_attention)
        self.assertIn("Worker boot DOA", cell.error_message)
        events = panel_log.get_recent()
        self.assertEqual(events[-1]["kind"], "worker_boot_doa")
        self.assertEqual(events[-1]["cell_id"], cell.id)
        self.assertEqual(events[-1]["task_id"], task.id)
        self.assertNotIn(cell.id, bus._boot_doa_timers)

    async def test_worker_boot_doa_timer_suppressed_by_post_boot_activity(self):
        """Negative path: activity within the window cancels escalation even
        though the scheduled timer still runs."""
        state, cell, task = self._boot_doa_worker_state()
        panel_log = self.events_mod.PanelEventLog()
        bus = self.events_mod.EventBus(
            state, self.events_mod.EventLog(), panel_log=panel_log)
        bus.start()

        with mock.patch.dict(
                os.environ, {self.events_mod.WORKER_BOOT_DOA_ENV: "0.05"}):
            await bus.emit(self.base_mod.AgentEvent(
                cell_id=cell.id, timestamp=0.0,
                event_type="session_start", data={}))
            # Worker posts real activity before the window elapses.
            await bus.emit(self.base_mod.AgentEvent(
                cell_id=cell.id, timestamp=1.0,
                event_type="tool_start", data={"detail": "Running tests"}))
            await asyncio.sleep(0.15)

        self.assertFalse(cell.needs_attention)
        self.assertNotIn(
            "worker_boot_doa",
            {evt["kind"] for evt in panel_log.get_recent()})
        self.assertNotIn(cell.id, bus._boot_doa_timers)

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
            session_id="pty-session-1",
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
        self.assertEqual(cell.session_id, "pty-session-1")
        self.assertEqual(saved, [("agent-1", "idle")])
        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(upserts[-1]["status"], "idle")
        self.assertEqual(upserts[-1]["last_event_text"], "Session ended")

    async def test_codex_session_end_for_current_provider_session_marks_idle(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Codex worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
            activity="thinking",
            session_id="pty-session-1",
            agent_session_id="codex-session-current",
        )
        state.agents[cell.id] = cell
        saved = []
        state._db_save_agent = lambda agent: saved.append((agent.id, agent.status))
        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(self.base_mod.AgentEvent(
            cell_id=cell.id,
            timestamp=time.time(),
            event_type="session_end",
            data={
                "session_id": "codex-session-current",
                "summary": "Finished",
            },
        ))

        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.last_summary, "Finished")
        self.assertEqual(saved, [(cell.id, "idle")])
        self.assertEqual(
            [op for op in state._delta_ops if op["op"] == "agent_upsert"][-1]["status"],
            "idle",
        )

    async def test_session_end_ignores_stale_or_non_live_cell(self):
        cases = (
            ("stale provider session", {"agent_session_id": "current"},
             {"session_id": "old"}),
            ("stopped", {"status": "stopped"}, {}),
            ("terminal error", {"status": "error"}, {}),
            ("dismissed", {"dismissed_at": 1}, {}),
            ("tombstoned", {"deleted_at": 1.0}, {}),
        )
        for label, overrides, data in cases:
            with self.subTest(label=label):
                state = self._make_state()
                cell_kwargs = {
                    "id": "agent-1",
                    "name": "Codex worker",
                    "group": "g",
                    "cell_type": "agent",
                    "agent_type": "codex",
                    "status": "running",
                    "activity": "thinking",
                    "session_id": "pty-session-1",
                }
                cell_kwargs.update(overrides)
                cell = self.state_mod.AgentCell(**cell_kwargs)
                state.agents[cell.id] = cell
                saved = []
                state._db_save_agent = lambda agent: saved.append(agent.id)
                bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

                await bus.emit(self.base_mod.AgentEvent(
                    cell_id=cell.id,
                    timestamp=time.time(),
                    event_type="session_end",
                    data=data,
                ))

                self.assertEqual(cell.status, overrides.get("status", "running"))
                self.assertEqual(cell.activity, "thinking")
                self.assertEqual(saved, [])
                self.assertEqual(state._delta_ops, [])

    async def test_codex_replacement_rejects_old_stop_before_session_start(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Codex replacement",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
            activity="thinking",
            session_id="replacement-pty-session",
            # Restart/relaunch intentionally clears this before the new
            # provider SessionStart event is drained.
            agent_session_id="",
        )
        state.agents[cell.id] = cell
        saved = []
        callbacks = []
        state._db_save_agent = lambda agent: saved.append(agent.id)
        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        async def on_session_end(ended_cell):
            callbacks.append(ended_cell.id)

        bus.on_session_end = on_session_end
        await bus.emit(self.base_mod.AgentEvent(
            cell_id=cell.id,
            timestamp=time.time(),
            event_type="session_end",
            data={"session_id": "old-codex-session"},
        ))

        self.assertEqual(cell.status, "running")
        self.assertEqual(cell.activity, "thinking")
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(saved, [])
        self.assertEqual(callbacks, [])
        self.assertEqual(state._delta_ops, [])

    async def test_session_end_broadcasts_idle_before_completion_callback(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
            activity="thinking",
            activity_detail="Running tests",
            session_id="pty-session-1",
        )
        state.agents[cell.id] = cell

        events = []

        async def fake_broadcast():
            events.append((
                "broadcast",
                [
                    {
                        "op": op.get("op"),
                        "id": op.get("id"),
                        "status": op.get("status"),
                        "last_event_text": op.get("last_event_text"),
                    }
                    for op in state._delta_ops
                ],
            ))
            state._delta_ops.clear()

        async def on_session_end(ended_cell):
            events.append(("callback", ended_cell.status, len(state._delta_ops)))

        state.broadcast = fake_broadcast
        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())
        bus.start()
        bus.on_session_end = on_session_end

        def cleanup_timers():
            for handle in list(bus._timers.values()):
                handle.cancel()
            bus._timers.clear()

        self.addCleanup(cleanup_timers)

        await bus.emit(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=time.time(),
                event_type="session_end",
                data={"summary": "All done"},
            )
        )

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][0], "broadcast")
        upserts = [
            op for op in events[0][1]
            if op["op"] == "agent_upsert" and op["id"] == cell.id
        ]
        self.assertTrue(upserts)
        self.assertEqual(upserts[-1]["status"], "idle")
        self.assertEqual(upserts[-1]["last_event_text"], "Session ended")
        self.assertEqual(events[1], ("callback", "idle", 0))

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
            labels=["torque:blocked", "keep"],
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
            labels=["torque:derived"],
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
            labels=["torque:derived"],
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

    async def test_codex_context_update_accepts_provider_usage_without_sdk_source(self):
        state = self._make_state()
        cell = self._make_cell("codex")
        cell.agent_type = "codex"
        state.agents[cell.id] = cell
        bus = self.events_mod.EventBus(state, self.events_mod.EventLog())

        await bus.emit(self.base_mod.AgentEvent(
            cell_id=cell.id,
            timestamp=time.time(),
            event_type="context_update",
            data={
                "provider_usage": {"five_hour": {"available": True, "used_percentage": 99, "resets_at": "2026-01-01T00:00:00Z"}},
                "context_window": {"used_tokens": 10, "limit_tokens": 100},
            },
        ))

        self.assertIsNotNone(cell.provider_usage)
        self.assertEqual(cell.provider_usage["five_hour"]["used_percentage"], 99)
        self.assertEqual(cell.context_window["used_tokens"], 10)
