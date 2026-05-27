import importlib
import json
import sqlite3
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.db import TorqueDB

install_aiohttp_stub()
from torque.metrics import FRONTEND_RENDER_STALENESS_SECONDS, MetricsCollector
from torque.state import AgentCell, BoardTask, EngineerSettings, MatrixState


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class SystemHealthMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)

    def _insert_event(self, event_id, ts, kind, group="g", task_id=""):
        self.db._conn.execute(
            "INSERT INTO panel_events "
            "(id, timestamp, kind, cell_id, agent_name, group_name, message, task_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (event_id, ts, kind, "cell", "agent", group, kind, task_id),
        )
        self.db._conn.commit()

    def _state(self):
        state = MatrixState(self.db)
        state.groups["g"] = []
        return state

    def test_load_panel_events_window_filters_without_recent_cap(self):
        for idx in range(1, 8):
            self._insert_event(idx, float(idx), "task_dispatched", group="g")
        self._insert_event(8, 4.5, "worker_boot_doa", group="g")
        self._insert_event(9, 5.5, "task_dispatched", group="other")

        rows = self.db.load_panel_events_window(
            2.0,
            6.0,
            group="g",
            kinds=["task_dispatched"],
        )

        self.assertEqual([row["id"] for row in rows], [2, 3, 4, 5, 6])
        self.assertTrue(all(row["group"] == "g" for row in rows))

    def test_dispatch_metrics_exclude_task_auto_dispatched(self):
        now = 100_000.0
        self._insert_event(1, now - 100, "task_dispatched", group="g")
        self._insert_event(2, now - 90, "task_auto_dispatched", group="g")
        self._insert_event(3, now - 80, "task_queued", group="g")
        state = self._state()

        payload = state.system_health_metrics(window="24h", group="g", now=now)

        self.assertEqual(payload["summary"]["dispatch"]["count"], 1)
        self.assertEqual(payload["summary"]["dispatch"]["autoresume_count"], 1)
        self.assertEqual(payload["summary"]["dispatch"]["queued_count"], 1)
        self.assertEqual(sum(payload["series"]["dispatches"]), 1)

    def test_worker_boot_doa_rate_uses_dispatch_denominator(self):
        now = 100_000.0
        self._insert_event(1, now - 300, "task_dispatched", group="g")
        self._insert_event(2, now - 200, "task_dispatched", group="g")
        self._insert_event(3, now - 100, "worker_boot_doa", group="g")
        state = self._state()

        payload = state.system_health_metrics(window="24h", group="g", now=now)

        doa = payload["summary"]["worker_boot_doa"]
        self.assertEqual(doa["count"], 1)
        self.assertEqual(doa["denominator"], 2)
        self.assertEqual(doa["rate"], 0.5)

    def test_review_cycle_aggregation_identifies_clean_and_multi_round(self):
        now = 200_000.0
        state = MatrixState()
        state.groups["g"] = []
        tasks = [
            BoardTask(id="root-clean", task="Root", group="g", created_at=_iso(now - 5000)),
            BoardTask(
                id="review-clean",
                task="Review",
                group="g",
                action_name="feature/review",
                pipeline_root_id="root-clean",
                parent_task_id="root-clean",
                created_at=_iso(now - 4000),
            ),
            BoardTask(id="root-followup", task="Root with follow-up", group="g", created_at=_iso(now - 5000)),
            BoardTask(
                id="review-before-fix",
                task="Review before fix",
                group="g",
                action_name="feature/review",
                pipeline_root_id="root-followup",
                parent_task_id="root-followup",
                created_at=_iso(now - 3900),
            ),
            BoardTask(
                id="fix-after-single-review",
                task="Follow-up fix without re-review",
                group="g",
                action_name="feature/fix-review",
                pipeline_root_id="root-followup",
                parent_task_id="review-before-fix",
                created_at=_iso(now - 3500),
            ),
            BoardTask(id="root-fix", task="Root 2", group="g", created_at=_iso(now - 5000)),
            BoardTask(
                id="review-one",
                task="Review 1",
                group="g",
                action_name="feature/review",
                pipeline_root_id="root-fix",
                parent_task_id="root-fix",
                created_at=_iso(now - 3000),
            ),
            BoardTask(
                id="fix-one",
                task="Fix",
                group="g",
                action_name="feature/fix-review",
                pipeline_root_id="root-fix",
                parent_task_id="review-one",
                created_at=_iso(now - 2000),
            ),
            BoardTask(
                id="review-two",
                task="Review 2",
                group="g",
                action_name="feature/review",
                pipeline_root_id="root-fix",
                parent_task_id="fix-one",
                created_at=_iso(now - 1000),
            ),
        ]
        state.board_tasks = {task.id: task for task in tasks}

        payload = state.system_health_metrics(window="24h", group="g", now=now)

        review = payload["summary"]["review_cycles"]
        self.assertEqual(review["roots_count"], 3)
        self.assertEqual(review["review_tasks"], 4)
        self.assertEqual(review["first_pass_clean_count"], 1)
        self.assertEqual(review["first_pass_clean_pct"], 1 / 3)
        self.assertEqual(review["fix_rounds"], 2)
        by_root = {
            item["pipeline_root_id"]: item
            for item in payload["distributions"]["review_cycles"]
        }
        self.assertTrue(by_root["root-clean"]["first_pass_clean"])
        self.assertFalse(by_root["root-followup"]["first_pass_clean"])
        self.assertFalse(by_root["root-fix"]["first_pass_clean"])
        self.assertEqual(sum(payload["series"]["reviews"]), 4)

    def test_merge_aggregation_uses_worktree_boundary_and_ignores_bad_times(self):
        now = 1_000_000.0
        state = MatrixState()
        state.groups["g"] = []
        state.board_tasks = {
            "merged": BoardTask(
                id="merged",
                task="Merged",
                group="g",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "feature",
                    "status": "merged",
                    "recorded_at": _iso(now - 7200),
                    "merged_at": _iso(now - 3600),
                },
            ),
            "bad": BoardTask(
                id="bad",
                task="Bad",
                group="g",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "bad",
                    "status": "merged",
                    "recorded_at": "not-a-time",
                    "merged_at": "not-a-time",
                },
            ),
            "open": BoardTask(
                id="open",
                task="Open",
                group="g",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "open",
                    "status": "open",
                    "recorded_at": _iso(now - (8 * 86400)),
                },
            ),
        }

        payload = state.system_health_metrics(window="7d", group="g", now=now)

        merge = payload["summary"]["merge"]
        self.assertEqual(merge["merged_count"], 1)
        self.assertEqual(merge["median_boundary_to_merge_seconds"], 3600)
        self.assertEqual(merge["open_count"], 1)
        self.assertEqual(merge["stale_open_count"], 1)

    def test_task_age_uses_lane_entered_at_with_created_fallback(self):
        now = 1_000_000.0
        state = MatrixState()
        state.groups["g"] = []
        state.board_tasks = {
            "todo": BoardTask(
                id="todo",
                task="Todo",
                group="g",
                lane="To Do",
                created_at=_iso(now - 100),
                lane_entered_at=_iso(now - 7200),
            ),
            "backlog": BoardTask(
                id="backlog",
                task="Backlog",
                group="g",
                lane="Backlog",
                created_at=_iso(now - 5 * 3600),
            ),
        }

        payload = state.system_health_metrics(window="24h", group="g", now=now)

        ages = payload["distributions"]["task_age_by_lane"]
        self.assertEqual(ages["To Do"]["p50_seconds"], 7200)
        self.assertEqual(ages["Backlog"]["buckets"]["4-24h"], 1)

    def test_utilization_clips_agent_task_intervals_to_window(self):
        now = 500_000.0
        since = now - 24 * 3600
        task = BoardTask(id="task-1", task="Task", group="g")
        self.db.save_board_task(task)
        self.db.save_agent_history({
            "id": "worker-1",
            "name": "Worker 1",
            "group": "g",
            "agent_type": "codex",
            "template": "",
            "created_at": since - 3600,
        })
        self.db.save_agent_history({
            "id": "worker-2",
            "name": "Worker 2",
            "group": "g",
            "agent_type": "codex",
            "template": "",
            "created_at": now - 3600,
        })
        self.db.save_agent_task({
            "agent_id": "worker-1",
            "task_id": task.id,
            "task_title": task.task,
            "started_at": since - 1800,
            "completed_at": since + 1800,
        })
        self.db.save_agent_task({
            "agent_id": "worker-2",
            "task_id": task.id,
            "task_title": task.task,
            "started_at": now - 1800,
            "completed_at": None,
        })
        state = self._state()
        state.board_tasks[task.id] = task
        state.agents["eng"] = AgentCell(
            id="eng",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.engineer_settings["g"] = EngineerSettings(
            group="g",
            default_worker_concurrency=2,
        )

        payload = state.system_health_metrics(window="24h", group="g", now=now)

        util = payload["summary"]["utilization"]
        self.assertEqual(util["busy_seconds"], 3600)
        self.assertEqual(util["capacity_seconds"], 24 * 2 * 3600)
        self.assertAlmostEqual(util["percent"], (3600 / (24 * 2 * 3600)) * 100)
        self.assertEqual(payload["series"]["busy_seconds"][0], 1800)
        self.assertEqual(payload["series"]["busy_seconds"][-1], 1800)

    def test_dispatch_shape_reads_mcp_idempotency_with_coverage(self):
        now = 800_000.0
        task = BoardTask(id="task-1", task="Task", group="g")
        self.db.save_board_task(task)
        self._insert_event(1, now - 100, "task_dispatched", group="g", task_id=task.id)
        self.db._conn.execute(
            "INSERT INTO mcp_idempotency "
            "(idempotency_key, surface, tool_name, request_hash, response_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "key-1",
                "engineer",
                "engineer_batch_dispatch",
                "hash",
                json.dumps({
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "type": "ok",
                            "results": [
                                {"task_id": task.id, "status": "dispatched"},
                                {"task_id": "missing", "status": "deferred"},
                            ],
                        }),
                    }],
                    "isError": False,
                }),
                now - 100,
                now - 100,
            ),
        )
        self.db._conn.commit()
        state = self._state()
        state.board_tasks[task.id] = task

        payload = state.system_health_metrics(window="24h", group="g", now=now)

        shape = payload["summary"]["dispatch_shape"]
        self.assertEqual(shape["batch_tool_calls"], 1)
        self.assertEqual(shape["batch_entries"], 2)
        self.assertEqual(shape["statuses"], {"dispatched": 1, "deferred": 1})
        self.assertTrue(payload["coverage"]["dispatch_shape"]["partial"])

    def test_metrics_collector_aggregates_tick_with_overhead(self):
        collector = MetricsCollector(enabled=True)
        collector.record_event_loop_lag(2.0)
        collector.record_event_loop_lag(10.0)
        collector.record_ws_delta(op_count=4, payload_bytes=200, subscribers=3)
        collector.record_db_write(latency_ms=7.5)

        tick = collector.aggregate_tick(
            live={"agents": 2, "ptys": 1, "prompt_queue_depth": 1},
            now=1_000.0,
            interval_seconds=2.0,
        )

        self.assertEqual(tick["type"], "metrics_tick")
        self.assertEqual(tick["schema_version"], 1)
        self.assertTrue(tick["enabled"])
        self.assertEqual(tick["interval_ms"], 2000)
        self.assertEqual(tick["perf"]["ws"]["deltas_per_s"], 2.0)
        self.assertEqual(tick["perf"]["ws"]["bytes_per_s"], 100.0)
        self.assertEqual(tick["perf"]["ws"]["subscribers"], 3)
        self.assertEqual(tick["perf"]["db"]["writes_per_s"], 0.5)
        self.assertEqual(tick["perf"]["db"]["write_latency_p95_ms"], 7.5)
        self.assertEqual(tick["perf"]["live"]["agents"], 2)
        self.assertLess(tick["meter_overhead"]["agg_tick_ms"], 50.0)
        self.assertLess(tick["meter_overhead"]["collect_overhead_pct"], 1.0)

    def test_frontend_render_absent_and_stale_emit_null(self):
        collector = MetricsCollector(enabled=True)

        never_reported = collector.aggregate_tick(
            live={},
            now=1_000.0,
            interval_seconds=2.0,
        )
        self.assertIsNone(never_reported["perf"]["frontend"])

        collector.record_frontend_render({
            "count": 4,
            "duration_ms": 12.0,
            "render_per_s": 2.0,
        })
        reported_at = collector._frontend_render_samples[-1]["timestamp"]
        fresh = collector.aggregate_tick(
            live={},
            now=reported_at + 1.0,
            interval_seconds=2.0,
        )
        self.assertEqual(fresh["perf"]["frontend"]["render_per_s"], 2.0)
        self.assertEqual(fresh["perf"]["frontend"]["render_ms_p95"], 12.0)

        stale = collector.aggregate_tick(
            live={},
            now=reported_at + FRONTEND_RENDER_STALENESS_SECONDS + 0.1,
            interval_seconds=2.0,
        )
        self.assertIsNone(stale["perf"]["frontend"])

    def test_metrics_enabled_toggle_disables_inline_collection(self):
        state = MatrixState()
        state.update_global_settings(metrics_enabled=False)

        state._emit(
            "event_append",
            kind="task_dispatched",
            timestamp=123.0,
        )

        self.assertFalse(state.metrics_collector.enabled)
        self.assertEqual(state.metrics_collector._inline_calls, 0)

    def test_metrics_perf_rollups_upsert_and_trim(self):
        row = {
            "bucket_start": 120,
            "bucket_seconds": 60,
            "sample_count": 1,
            "event_loop_lag_p95_ms": 10.0,
            "ws_deltas_per_s": 2.0,
            "db_write_latency_p95_ms": 3.0,
            "rss_mb": 100.0,
            "cpu_pct": 5.0,
            "updated_at": 125.0,
        }
        self.db.save_metrics_perf_rollup(row)
        row.update({
            "event_loop_lag_p95_ms": 20.0,
            "ws_deltas_per_s": 4.0,
            "db_write_latency_p95_ms": 1.0,
            "rss_mb": 200.0,
            "cpu_pct": 7.0,
            "updated_at": 130.0,
        })
        self.db.save_metrics_perf_rollup(row)

        rows = self.db.load_metrics_perf_rollups(0, 1000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sample_count"], 2)
        self.assertEqual(rows[0]["event_loop_lag_p95_ms"], 20.0)
        self.assertEqual(rows[0]["ws_deltas_per_s"], 3.0)
        self.assertEqual(rows[0]["db_write_latency_p95_ms"], 3.0)
        self.assertEqual(rows[0]["rss_mb"], 150.0)

        self.db.trim_metrics_perf_rollups(121)
        self.assertEqual(self.db.load_metrics_perf_rollups(0, 1000), [])

    def test_metrics_history_contract_serves_perf_and_workflow(self):
        now = 3600 * 100 + 10
        until = 3600 * 101
        event_ts = until - 1800
        self._insert_event(1, event_ts, "task_dispatched", group="g")
        self.db.save_metrics_perf_rollup({
            "bucket_start": int((until - 120) // 60) * 60,
            "bucket_seconds": 60,
            "sample_count": 1,
            "event_loop_lag_p95_ms": 11.0,
            "ws_deltas_per_s": 4.0,
            "db_write_latency_p95_ms": 2.0,
            "rss_mb": 123.0,
            "cpu_pct": 6.0,
            "updated_at": until - 60,
        })
        state = self._state()
        state.active_group = "g"

        payload = state.metrics_history(window="24h", now=now)

        self.assertEqual(payload["type"], "metrics_history")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["group"], "g")
        self.assertEqual(payload["scope"], "group")
        self.assertEqual(payload["bucket_seconds"], 3600)
        self.assertEqual(len(payload["buckets"]), 24)
        self.assertEqual(len(payload["perf"]["ws_deltas_per_s"]), 24)
        self.assertEqual(sum(payload["workflow"]["dispatch"]["series"]), 1)
        self.assertIn("dispatch_shape", payload["coverage"])
        self.assertEqual(
            payload["perf"]["retention"]["rollup_resolution_seconds"],
            60,
        )


class SystemHealthServerCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, panel_log=None):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )
        closure_values = {name: None for name in handle_code.co_freevars}
        closure_values.update({
            "db": None,
            "state": state,
            "panel_log": panel_log,
            "bridge": types.SimpleNamespace(),
            "handle_command": None,
        })
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

    async def test_broadcast_metrics_disabled_skips_payload_byte_count(self):
        state = self.state_mod.MatrixState()
        state.global_settings.metrics_enabled = False
        state.metrics_collector.set_enabled(False)
        encode_calls = {"count": 0}

        class CountingStr(str):
            def encode(self, *args, **kwargs):
                encode_calls["count"] += 1
                return super().encode(*args, **kwargs)

        async def fake_dumps(*_args, **_kwargs):
            return CountingStr('{"type":"delta","ops":[]}')

        original_dumps = self.state_mod.hot_json_dumps_async
        original_profiling_enabled = self.state_mod.profiling.is_enabled
        self.state_mod.hot_json_dumps_async = fake_dumps
        self.state_mod.profiling.is_enabled = lambda: False
        try:
            state._emit("ui_update", key="metrics_disabled_guard", value=True)
            await state.broadcast()
        finally:
            self.state_mod.hot_json_dumps_async = original_dumps
            self.state_mod.profiling.is_enabled = original_profiling_enabled

        self.assertEqual(encode_calls["count"], 0)

    async def test_server_command_returns_metrics_and_validates_window(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []

        class PanelLog:
            def __init__(self):
                self.flushed = False

            async def flush(self):
                self.flushed = True

        panel_log = PanelLog()
        handle_command = self._extract_handle_command(state, panel_log=panel_log)

        ok = await handle_command({
            "cmd": "get_system_health_metrics",
            "window": "24h",
            "group": "g",
        })
        bad = await handle_command({
            "cmd": "get_system_health_metrics",
            "window": "bad",
            "group": "g",
        })

        self.assertEqual(ok["type"], "system_health_metrics")
        self.assertEqual(ok["group"], "g")
        self.assertTrue(panel_log.flushed)
        self.assertEqual(bad["type"], "error")
        self.assertIn("window must be one of", bad["message"])

    async def test_server_command_returns_metrics_history_contract(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.active_group = "g"
        handle_command = self._extract_handle_command(state, panel_log=None)

        ok = await handle_command({
            "cmd": "get_metrics_history",
            "window": "24h",
        })
        bad = await handle_command({
            "cmd": "get_metrics_history",
            "window": "bad",
        })

        self.assertEqual(ok["type"], "metrics_history")
        self.assertEqual(ok["schema_version"], 1)
        self.assertEqual(ok["group"], "g")
        self.assertIn("perf", ok)
        self.assertIn("workflow", ok)
        self.assertEqual(bad["type"], "error")
        self.assertIn("window must be one of", bad["message"])


if __name__ == "__main__":
    unittest.main()
