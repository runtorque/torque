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
            BoardTask(
                id="fix-after-single-review",
                task="Follow-up fix without re-review",
                group="g",
                action_name="feature/fix-review",
                pipeline_root_id="root-clean",
                parent_task_id="review-clean",
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
        self.assertEqual(review["roots_count"], 2)
        self.assertEqual(review["review_tasks"], 3)
        self.assertEqual(review["first_pass_clean_count"], 1)
        self.assertEqual(review["fix_rounds"], 2)
        self.assertEqual(sum(payload["series"]["reviews"]), 3)

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


if __name__ == "__main__":
    unittest.main()
