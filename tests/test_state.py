import importlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class HotJsonSerializationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _state_payload_with_task_message(self, message: str) -> dict:
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Nested message payload",
            group="g",
            lane="Backlog",
            messages=[{
                "timestamp": 1,
                "action": "progress",
                "message": message,
                "agent_name": "worker",
            }],
        )
        state.board_tasks[task.id] = task
        return {"type": "state", "seq": state._seq, **state.to_dict()}

    def test_hot_json_should_offload_uses_size_threshold_for_state_payloads(self):
        small_delta = {
            "type": "delta",
            "seq": 1,
            "ops": [{"op": "task_upsert", "id": "task-1"}],
        }
        large_delta = {
            "type": "delta",
            "seq": 2,
            "ops": [{
                "op": "task_upsert",
                "value": "x" * self.state_mod.HOT_JSON_OFFLOAD_BYTES,
            }],
        }
        small_state = {"type": "state", "seq": 1, "groups": {}}
        large_state = {
            "type": "state",
            "seq": 1,
            "payload": "x" * self.state_mod.HOT_JSON_OFFLOAD_BYTES,
        }
        small_wrapped_state = {
            "ok": True,
            "data": {"type": "state", "seq": 1, "groups": {}},
        }

        self.assertFalse(self.state_mod.hot_json_should_offload(small_delta))
        self.assertTrue(self.state_mod.hot_json_should_offload(large_delta))
        self.assertFalse(self.state_mod.hot_json_should_offload(small_state))
        self.assertTrue(self.state_mod.hot_json_should_offload(large_state))
        self.assertFalse(
            self.state_mod.hot_json_should_offload(small_wrapped_state)
        )

    def test_hot_json_should_offload_realistic_nested_state_payloads(self):
        small_state = self._state_payload_with_task_message("small")
        large_state = self._state_payload_with_task_message(
            "x" * (self.state_mod.HOT_JSON_OFFLOAD_BYTES * 2)
        )
        wrapped_large_state = {"ok": True, "data": large_state}

        self.assertLess(
            len(self.state_mod.hot_json_dumps_bytes(small_state)),
            self.state_mod.HOT_JSON_OFFLOAD_BYTES,
        )
        self.assertFalse(self.state_mod.hot_json_should_offload(small_state))
        self.assertGreaterEqual(
            len(self.state_mod.hot_json_dumps_bytes(large_state)),
            self.state_mod.HOT_JSON_OFFLOAD_BYTES,
        )
        self.assertTrue(self.state_mod.hot_json_should_offload(large_state))
        self.assertTrue(
            self.state_mod.hot_json_should_offload(wrapped_large_state)
        )

    async def test_hot_json_dumps_async_inlines_small_and_offloads_large_payloads(self):
        calls = []
        original_to_thread = self.state_mod.asyncio.to_thread

        async def recording_to_thread(func, /, *args, **kwargs):
            calls.append((func, args, kwargs))
            return func(*args, **kwargs)

        self.state_mod.asyncio.to_thread = recording_to_thread
        try:
            small_state = {"type": "state", "seq": 1, "groups": {}}
            small_wrapped_state = {
                "ok": True,
                "data": {"type": "state", "seq": 1, "groups": {}},
            }
            large_state = {
                "type": "state",
                "seq": 1,
                "payload": "x" * self.state_mod.HOT_JSON_OFFLOAD_BYTES,
            }

            self.assertEqual(
                json.loads(await self.state_mod.hot_json_dumps_async(small_state)),
                small_state,
            )
            self.assertEqual(calls, [])

            self.assertEqual(
                json.loads(
                    await self.state_mod.hot_json_dumps_async(small_wrapped_state)
                ),
                small_wrapped_state,
            )
            self.assertEqual(calls, [])

            self.assertEqual(
                json.loads(await self.state_mod.hot_json_dumps_async(large_state)),
                large_state,
            )
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0][0], self.state_mod.hot_json_dumps_bytes)
        finally:
            self.state_mod.asyncio.to_thread = original_to_thread

    async def test_hot_json_dumps_async_offloads_large_nested_state_payloads(self):
        calls = []
        original_to_thread = self.state_mod.asyncio.to_thread

        async def recording_to_thread(func, /, *args, **kwargs):
            calls.append((func, args, kwargs))
            return func(*args, **kwargs)

        self.state_mod.asyncio.to_thread = recording_to_thread
        try:
            large_state = self._state_payload_with_task_message(
                "x" * (self.state_mod.HOT_JSON_OFFLOAD_BYTES * 2)
            )
            wrapped_large_state = {"ok": True, "data": large_state}

            self.assertEqual(
                json.loads(await self.state_mod.hot_json_dumps_async(large_state)),
                large_state,
            )
            self.assertEqual(len(calls), 1)

            self.assertEqual(
                json.loads(
                    await self.state_mod.hot_json_dumps_async(wrapped_large_state)
                ),
                wrapped_large_state,
            )
            self.assertEqual(len(calls), 2)
            self.assertTrue(
                all(call[0] is self.state_mod.hot_json_dumps_bytes for call in calls)
            )
        finally:
            self.state_mod.asyncio.to_thread = original_to_thread

    def test_hot_json_bytes_and_string_outputs_parse_equally(self):
        payload = {
            "task": "Check JSON 🚀",
            "path": Path("/tmp/loom"),
            "when": datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        }

        raw_bytes = self.state_mod.hot_json_dumps_bytes(payload)
        raw_str = self.state_mod.hot_json_dumps(payload)

        self.assertIsInstance(raw_bytes, bytes)
        self.assertIsInstance(raw_str, str)
        self.assertEqual(raw_bytes.decode("utf-8"), raw_str)
        self.assertEqual(json.loads(raw_bytes), json.loads(raw_str))


class MatrixStateCleanupTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_update_global_settings_validates_xterm_scrollback(self):
        state = self.state_mod.MatrixState()
        state.update_global_settings(xterm_scrollback=4096)
        self.assertEqual(state.global_settings.xterm_scrollback, 4096)

        with self.assertRaises(ValueError):
            state.update_global_settings(xterm_scrollback=99)

        self.assertEqual(state.global_settings.xterm_scrollback, 4096)

    def test_snapshot_msg_hot_json_round_trips(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = ["agent-1"]
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            activity_detail="Serializing café payload",
        )
        state.board_tasks["task-1"] = self.state_mod.BoardTask(
            id="task-1",
            task="Check JSON 🚀",
            group="g",
            lane="Backlog",
        )

        raw = state.snapshot_msg()
        decoded = json.loads(raw)

        self.assertEqual(decoded, {
            "type": "state",
            "seq": state._seq,
            **state.to_dict(),
        })

        if self.state_mod.orjson is not None:
            self.assertNotIn(": ", raw)

    def test_compact_snapshot_uses_task_summaries_and_excludes_archived_tasks(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.board_tasks["task-1"] = self.state_mod.BoardTask(
            id="task-1",
            task="Heavy task",
            slug="heavy-task",
            group="g",
            lane="In Progress",
            position=3,
            action_name="feature/implement",
            labels=["performance"],
            agent_id="agent-1",
            assigned_engineer_id="eng-1",
            parent_task_id="parent-1",
            pipeline_depth=1,
            status="On Review",
            created_at="2026-04-21T00:00:00+00:00",
            updated_at="2026-04-22T00:00:00+00:00",
            scheduled_at="2026-04-23T00:00:00+00:00",
            depends_on=["task-0"],
            provider="github",
            external_id="123",
            external_url="https://example.test/tasks/123",
            health_state="attention",
            health_since="2026-04-22T12:00:00+00:00",
            health_details={"reason": "large"},
            verification_mode="deploy",
            verification_state="pending",
            verification_notes="needs smoke",
            verification_summary={"tests_run": "targeted"},
            lane_entered_at="2026-04-22T00:00:00+00:00",
            worktree_boundary={"base": "main"},
            resume_after_boundary_task_id="task-boundary",
            description="long description",
            context="legacy context",
            criteria="legacy criteria",
            instructions="legacy instructions",
            messages=[{"action": "progress", "message": "full progress body"}],
            attachments=[{"filename": "image.png"}],
            artifacts=[{"kind": "log"}],
            action_vars={"name": "value"},
        )
        state.board_tasks["task-archived"] = self.state_mod.BoardTask(
            id="task-archived",
            task="Archived task",
            group="g",
            lane=self.state_mod.ARCHIVED_LANE,
            archived_at="2026-04-22T00:00:00+00:00",
            archived_from_lane="Done",
        )

        full = state.to_dict()
        compact = state.to_dict_compact()
        task = compact["board_tasks"]["task-1"]

        self.assertIn("task-archived", full["board_tasks"])
        self.assertIn("messages", full["board_tasks"]["task-1"])
        self.assertIn("description", full["board_tasks"]["task-1"])
        self.assertIn("health_details", full["board_tasks"]["task-1"])
        self.assertIn("verification_summary", full["board_tasks"]["task-1"])
        self.assertIn("worktree_boundary", full["board_tasks"]["task-1"])
        self.assertIn("decisions", full)
        self.assertIn("pending_hires", full)
        self.assertEqual(
            compact["snapshot_protocol"],
            self.state_mod.COMPACT_SNAPSHOT_PROTOCOL,
        )
        self.assertEqual(
            set(task),
            set(self.state_mod.COMPACT_BOARD_TASK_FIELDS),
        )
        self.assertEqual(task["task"], "Heavy task")
        self.assertEqual(task["labels"], ["performance"])
        self.assertEqual(task["created_at"], "2026-04-21T00:00:00+00:00")
        self.assertEqual(task["updated_at"], "2026-04-22T00:00:00+00:00")
        self.assertEqual(task["scheduled_at"], "2026-04-23T00:00:00+00:00")
        self.assertEqual(task["depends_on"], ["task-0"])
        self.assertEqual(task["provider"], "github")
        self.assertEqual(task["external_id"], "123")
        self.assertEqual(
            task["external_url"], "https://example.test/tasks/123")
        self.assertEqual(task["health_since"], "2026-04-22T12:00:00+00:00")
        self.assertEqual(task["health_details"], {"reason": "large"})
        self.assertEqual(task["verification_notes"], "needs smoke")
        self.assertEqual(
            task["verification_summary"], {"tests_run": "targeted"})
        self.assertEqual(
            task["messages"],
            [{"count": 1, "action": "progress", "message": "progress"}],
        )
        self.assertEqual(task["worktree_boundary"], {"base": "main"})
        self.assertEqual(
            task["resume_after_boundary_task_id"], "task-boundary")
        self.assertNotIn("description", task)
        self.assertNotIn("context", task)
        self.assertNotIn("criteria", task)
        self.assertNotIn("instructions", task)
        self.assertNotIn("attachments", task)
        self.assertNotIn("artifacts", task)
        self.assertNotIn("action_vars", task)
        self.assertNotIn("task-archived", compact["board_tasks"])
        self.assertNotIn("decisions", compact)
        self.assertNotIn("pending_hires", compact)
        self.assertNotIn("engineer_journal", compact)
        self.assertNotIn("engineer_worklog", compact)
        self.assertNotIn("engineer_streams", compact)

    def test_compact_snapshot_preserves_most_of_full_size_reduction(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        long_text = "detail " * 2000
        action_blob = "vars " * 1000
        message_blob = "message " * 1500
        for i in range(8):
            state.board_tasks[f"task-{i}"] = self.state_mod.BoardTask(
                id=f"task-{i}",
                task=f"Task {i}",
                slug=f"task-{i}",
                group="g",
                lane="In Progress",
                position=i,
                action_name="feature/implement",
                labels=["performance", "compact"],
                agent_id=f"agent-{i}",
                assigned_engineer_id=f"eng-{i}",
                parent_task_id="",
                pipeline_depth=0,
                status="On Review",
                created_at="2026-04-21T00:00:00+00:00",
                updated_at="2026-04-22T00:00:00+00:00",
                scheduled_at="2026-04-23T00:00:00+00:00",
                depends_on=["root"],
                provider="github",
                external_id=f"{i}",
                external_url=f"https://example.test/tasks/{i}",
                health_state="attention",
                health_since="2026-04-22T12:00:00+00:00",
                health_details={"reason": "recent_activity", "silence_secs": 12},
                verification_mode="deploy",
                verification_state="pending",
                verification_notes="needs smoke",
                verification_summary={"tests_run": "targeted"},
                lane_entered_at="2026-04-22T00:00:00+00:00",
                worktree_boundary={"repo_root": "/tmp/repo", "branch": "main"},
                resume_after_boundary_task_id="boundary-1",
                description=long_text,
                instructions=long_text,
                context=long_text,
                criteria=long_text,
                action_vars={"payload": action_blob},
                messages=[
                    {
                        "timestamp": 1_700_000_000 + i,
                        "action": "progress",
                        "message": message_blob,
                        "agent_name": "worker",
                    }
                    for _ in range(6)
                ],
                attachments=[{"filename": "image.png", "mime_type": "image/png"}],
                artifacts=[{"kind": "log", "summary": long_text[:200]}],
            )

        full_bytes = len(self.state_mod.hot_json_dumps_bytes(state.to_dict()))
        compact_bytes = len(
            self.state_mod.hot_json_dumps_bytes(state.to_dict_compact()))
        reduction = 1 - (compact_bytes / full_bytes)

        # LOOM:154 phase-1 measured ~98% reduction. Preserve at least 95% of
        # that win after eagerly restoring board-semantic metadata.
        self.assertGreaterEqual(reduction, 0.931)

    def test_compact_snapshot_does_not_run_deferred_db_loaders(self):
        state = self.state_mod.MatrixState()

        class ExplodingDB:
            def load_all_decisions(self, **_kwargs):
                raise AssertionError("compact snapshot must defer decisions")

            def load_pending_hires(self, **_kwargs):
                raise AssertionError("compact snapshot must defer pending hires")

        state.db = ExplodingDB()

        compact = state.to_dict_compact()

        self.assertEqual(compact["board_tasks"], {})
        self.assertNotIn("decisions", compact)
        self.assertNotIn("pending_hires", compact)

    def test_task_detail_returns_full_shape_and_resolves_aliases(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.board_tasks["task-1"] = self.state_mod.BoardTask(
            id="task-1",
            task="Full task",
            group="g",
            lane="To Do",
            description="full description",
            messages=[{"action": "progress", "message": "hello"}],
            artifacts=[{"kind": "diff"}],
            worktree_boundary={"base": "main"},
        )
        state.task_id_aliases["legacy-1"] = "task-1"

        detail = state.get_task_detail("legacy-1")

        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], "task-1")
        self.assertEqual(detail["description"], "full description")
        self.assertEqual(detail["messages"][0]["message"], "hello")
        self.assertEqual(detail["artifacts"], [{"kind": "diff"}])
        self.assertEqual(detail["worktree_boundary"], {"base": "main"})

    def test_hot_json_default_handles_guarded_types(self):
        raw = self.state_mod.hot_json_dumps({
            "path": Path("/tmp/loom"),
            "when": datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        })

        self.assertEqual(json.loads(raw), {
            "path": "/tmp/loom",
            "when": "2026-04-22T12:00:00+00:00",
        })

    def test_remove_agent_expires_orphaned_asks_and_clears_engineer_question(self):
        state = self.state_mod.MatrixState()
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id=agent.id,
            status="Awaiting Input",
        )
        ask = self.state_mod.BoardTask(
            id="ask-1",
            task="Review plan",
            group="g",
            lane="Backlog",
            labels=["loom:human", "loom:derived"],
            parent_task_id=parent.id,
        )

        state.agents[agent.id] = agent
        state.groups["g"] = [agent.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=agent.id
        )
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(
            group="g",
            pending_question="Need review",
            pending_note="FYI: tests are green",
            pending_note_kind="note",
            paused=True,
        )
        state.board_tasks[parent.id] = parent
        state.board_tasks[ask.id] = ask

        state.remove_agent(agent.id)

        self.assertEqual(state.group_settings["g"].engineer_agent_id, "")
        self.assertEqual(state.engineer_settings["g"].pending_question, "")
        self.assertEqual(state.engineer_settings["g"].pending_note, "")
        self.assertEqual(state.engineer_settings["g"].pending_note_kind, "")
        self.assertFalse(state.engineer_settings["g"].paused)
        self.assertEqual(state.board_tasks[parent.id].agent_id, "")
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[ask.id].lane, "Done")
        self.assertTrue(
            any(
                msg.get("action") == "system"
                and "source agent is no longer available" in msg.get("message", "")
                for msg in state.board_tasks[ask.id].messages
            )
        )

    def test_cleanup_orphaned_attention_expires_persisted_stale_state(self):
        state = self.state_mod.MatrixState()
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id="missing-agent",
            status="Awaiting Input",
        )
        ask = self.state_mod.BoardTask(
            id="ask-1",
            task="Review plan",
            group="g",
            lane="Backlog",
            labels=["loom:human", "loom:derived"],
            parent_task_id=parent.id,
        )

        state.groups["g"] = []
        state.board_tasks[parent.id] = parent
        state.board_tasks[ask.id] = ask
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(
            group="g",
            pending_question="Old question",
            pending_note="Soft question",
            pending_note_kind="question",
            paused=True,
        )

        cleaned = state.cleanup_orphaned_attention(emit=False)

        self.assertEqual(cleaned, {"asks": 1, "engineer_questions": 1})
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[ask.id].lane, "Done")
        self.assertEqual(state.engineer_settings["g"].pending_question, "")
        self.assertEqual(state.engineer_settings["g"].pending_note, "")
        self.assertEqual(state.engineer_settings["g"].pending_note_kind, "")
        self.assertFalse(state.engineer_settings["g"].paused)
        self.assertEqual(state._delta_ops, [])

    def test_cleanup_orphaned_attention_keeps_persisted_engineer_during_boot(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        db.save_group("g", 0)
        db.save_agent(engineer)
        db.save_group_settings(
            "g",
            self.state_mod.GroupSettings(engineer_agent_id=engineer.id),
        )

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(
            group="g",
            pending_question="Need approval",
            pending_question_actor_id=engineer.id,
            pending_note="FYI",
            pending_note_kind="note",
            paused=True,
        )

        cleaned = state.cleanup_orphaned_attention(emit=False)

        self.assertEqual(cleaned, {"asks": 0, "engineer_questions": 0})
        ws = state.engineer_settings["g"]
        self.assertEqual(ws.pending_question, "Need approval")
        self.assertEqual(ws.pending_question_actor_id, engineer.id)
        self.assertEqual(ws.pending_note, "FYI")
        self.assertEqual(ws.pending_note_kind, "note")
        self.assertTrue(ws.paused)

    def test_cleanup_orphaned_attention_false_fallback_clears_persisted_agent_row(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id=engineer.id,
            status="Awaiting Input",
        )
        ask = self.state_mod.BoardTask(
            id="ask-1",
            task="Review plan",
            group="g",
            lane="Backlog",
            labels=["loom:human", "loom:derived"],
            parent_task_id=parent.id,
        )
        db.save_group("g", 0)
        db.save_agent(engineer)
        self.assertTrue(db.agent_exists(engineer.id))

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(
            group="g",
            pending_question="Need approval",
            pending_question_set_at=123.0,
            pending_question_actor_id=engineer.id,
            pending_note="FYI",
            pending_note_kind="note",
            paused=True,
        )
        state.board_tasks[parent.id] = parent
        state.board_tasks[ask.id] = ask

        cleaned = state.cleanup_orphaned_attention(
            emit=False,
            allow_persisted_agent_fallback=False,
        )

        self.assertTrue(db.agent_exists(engineer.id))
        self.assertEqual(cleaned, {"asks": 1, "engineer_questions": 1})
        ws = state.engineer_settings["g"]
        self.assertEqual(ws.pending_question, "")
        self.assertEqual(ws.pending_question_set_at, 0.0)
        self.assertEqual(ws.pending_question_actor_id, "")
        self.assertEqual(ws.pending_note, "")
        self.assertEqual(ws.pending_note_kind, "")
        self.assertFalse(ws.paused)
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[ask.id].lane, "Done")

    def test_load_preserves_pending_question_for_persisted_engineer(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        db.save_groups_and_members({"g": [engineer.id]}, {"g": "g"})
        db.save_agent(engineer)
        db.save_group_settings(
            "g",
            self.state_mod.GroupSettings(engineer_agent_id=engineer.id),
        )
        db.save_engineer_settings(
            "g",
            {
                "group": "g",
                "pending_question": "Need approval",
                "pending_question_actor_id": engineer.id,
                "pending_note": "FYI",
                "pending_note_kind": "note",
                "paused": True,
            },
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        ws = state.engineer_settings["g"]
        self.assertEqual(ws.pending_question, "Need approval")
        self.assertEqual(ws.pending_question_actor_id, engineer.id)
        self.assertEqual(ws.pending_note, "FYI")
        self.assertEqual(ws.pending_note_kind, "note")
        self.assertTrue(ws.paused)

    def test_cleanup_stale_boundary_successors_clears_merged_refs(self):
        state = self.state_mod.MatrixState()
        boundary = self.state_mod.BoardTask(
            id="task-1",
            task="Boundary task",
            group="g",
            lane="Done",
            worktree_boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "merged",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        queued = self.state_mod.BoardTask(
            id="task-2",
            task="Queued follow-up",
            group="g",
            lane="To Do",
            resume_after_boundary_task_id=boundary.id,
        )

        state.board_tasks[boundary.id] = boundary
        state.board_tasks[queued.id] = queued

        cleaned = state.cleanup_stale_boundary_successors()

        self.assertEqual(cleaned, 1)
        self.assertEqual(
            state.board_tasks[queued.id].resume_after_boundary_task_id, ""
        )

    def test_load_clears_stale_boundary_successors_and_keeps_open_ones(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-open",
                task="Open boundary",
                group="g",
                lane="Done",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "loom/worker",
                    "status": "open",
                    "recorded_at": "2026-04-07T10:00:00+00:00",
                },
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-merged",
                task="Merged boundary",
                group="g",
                lane="Done",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "loom/worker",
                    "status": "merged",
                    "recorded_at": "2026-04-07T11:00:00+00:00",
                },
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-valid",
                task="Valid queued follow-up",
                group="g",
                lane="To Do",
                resume_after_boundary_task_id="task-open",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-stale",
                task="Stale queued follow-up",
                group="g",
                lane="To Do",
                resume_after_boundary_task_id="task-merged",
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(
            state.board_tasks["task-valid"].resume_after_boundary_task_id,
            "task-open",
        )
        self.assertEqual(
            state.board_tasks["task-stale"].resume_after_boundary_task_id,
            "",
        )

    def test_load_migrates_legacy_archived_label_to_archived_lane(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Old archived task",
                group="g",
                lane="Done",
                labels=["loom:archived", "bug"],
                updated_at="2026-04-07T10:00:00+00:00",
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        task = state.board_tasks["task-1"]
        self.assertEqual(task.lane, "Archived")
        self.assertEqual(task.archived_from_lane, "Done")
        self.assertEqual(task.archived_at, "2026-04-07T10:00:00+00:00")
        self.assertEqual(task.labels, ["bug"])

    def test_load_migrates_legacy_non_done_archives_without_done_semantics(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="dep-1",
                task="Legacy archived child",
                group="g",
                lane="In Progress",
                labels=["loom:archived"],
                updated_at="2026-04-07T10:00:00+00:00",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Blocked follow-up",
                group="g",
                lane="Backlog",
                depends_on=["dep-1"],
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        dep = state.board_tasks["dep-1"]
        self.assertEqual(dep.lane, "Archived")
        self.assertEqual(dep.archived_from_lane, "In Progress")
        self.assertEqual(dep.archived_at, "2026-04-07T10:00:00+00:00")
        self.assertFalse(state.board_deps_met(state.board_tasks["task-1"]))

    def test_load_restores_auto_dispatch_queue_and_busy_agents(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["agent-1"]}, {"g": "g"})
        db.save_group_members("g", ["agent-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                created_by_engineer_id="engineer-1",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-active",
                task="Active work",
                group="g",
                lane="In Progress",
                agent_id="agent-1",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-queued",
                task="Queued follow-up",
                group="g",
                lane="Backlog",
            )
        )
        db.save_auto_dispatch_queue("g", [
            {
                "task_id": "task-queued",
                "agent_group": "followup",
                "max_concurrent": 1,
                "target_agent_id": "agent-1",
                "engineer_owner_id": "engineer-1",
                "provider": "codex",
                "enqueued_at": "2026-04-07T10:00:00+00:00",
            }
        ])

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertTrue(state.agent_is_busy("agent-1"))
        self.assertEqual(
            state.agent_current_task("agent-1").id,
            "task-active",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].task_id,
            "task-queued",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].target_agent_id,
            "agent-1",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].engineer_owner_id,
            "engineer-1",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].provider,
            "codex",
        )

    def test_load_restores_kinds_fields_on_agents_and_tasks(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["agent-1"]}, {"g": "g"})
        db.save_group_members("g", ["agent-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="agent-1",
                name="Architect",
                group="g",
                cell_type="agent",
                created_by_engineer_id="engineer-1",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Plan work",
                group="g",
                lane="Backlog",
            )
        )
        db._conn.execute(
            "UPDATE agents SET kind=?, role=?, owner_engineer_id=?, "
            "hired_by_architect_id=?, persistent=? WHERE id=?",
            ("architect", "lead", "engineer-1", "architect-root", 1, "agent-1"),
        )
        db._conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id=?, "
            "created_by_architect_id=?, suggested_action=? WHERE id=?",
            ("engineer-1", "architect-root", "feature/review", "task-1"),
        )
        db._conn.commit()

        state = self.state_mod.MatrixState(db=db)
        state.load()

        agent = state.agents["agent-1"]
        self.assertEqual(agent.kind, "architect")
        self.assertEqual(agent.role, "lead")
        self.assertEqual(agent.owner_engineer_id, "engineer-1")
        self.assertEqual(agent.hired_by_architect_id, "architect-root")
        self.assertTrue(agent.persistent)

        task = state.board_tasks["task-1"]
        self.assertEqual(task.assigned_engineer_id, "engineer-1")
        self.assertEqual(task.created_by_architect_id, "architect-root")
        self.assertEqual(task.suggested_action, "feature/review")
        self.assertEqual(
            state.agents["agent-1"].created_by_engineer_id,
            "engineer-1",
        )

    def test_load_backfills_architect_queue_empty_digest_default(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["arch-1"]}, {"g": "g"})
        db.save_group_members("g", ["arch-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
                persistent=True,
            )
        )
        db.save_agent_digest_settings(
            "arch-1",
            {
                "agent_id": "arch-1",
                "enabled_events": ["task_completed"],
                "architect_digest": True,
            },
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        settings = state.get_agent_digest_settings("arch-1")
        self.assertEqual(
            settings.enabled_events,
            [
                "task_completed",
                "engineer_queue_empty",
                "engineer_awaiting_human_input",
                "engineer_ask_resolved",
            ],
        )
        persisted = db.load_agent_digest_settings("arch-1")
        self.assertIn("engineer_queue_empty", persisted["enabled_events"])
        self.assertIn("engineer_awaiting_human_input", persisted["enabled_events"])
        self.assertIn("engineer_ask_resolved", persisted["enabled_events"])

    def test_architect_settings_round_trip_through_group_settings(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_group_settings(
            "g",
            self.state_mod.GroupSettings(
                architect_boot_command="codex --architect",
                architect_provider="codex",
                architect_model="gpt-5.1-architect",
                architect_reasoning_effort="high",
                architect_custom_instructions="Own scope.",
                architect_autonomy_mode="ask_always",
                architect_paused=True,
                architect_digest_verbosity="verbose",
                architect_journal_checkpoint_frequency="every_20_minutes",
                architect_review_gate_thresholds={
                    "ship_direct_max": 25,
                    "review_default_above": 90,
                    "self_review_bypass_allowed": True,
                },
            ),
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        settings = state.get_architect_settings("g")
        self.assertEqual(settings.architect_boot_command, "codex --architect")
        self.assertEqual(settings.architect_provider, "codex")
        self.assertEqual(settings.architect_model, "gpt-5.1-architect")
        self.assertEqual(settings.architect_reasoning_effort, "high")
        self.assertEqual(settings.architect_custom_instructions, "Own scope.")
        self.assertEqual(settings.architect_autonomy_mode, "ask_always")
        self.assertTrue(settings.architect_paused)
        self.assertEqual(settings.architect_digest_verbosity, "verbose")
        self.assertEqual(
            settings.architect_journal_checkpoint_frequency,
            "every_20_minutes",
        )
        self.assertEqual(
            settings.architect_review_gate_thresholds,
            {
                "ship_direct_max": 25,
                "review_default_above": 90,
                "self_review_bypass_allowed": True,
            },
        )

    def test_architect_digest_settings_have_user_pain_aware_defaults(self):
        """Architect ArchitectSettings ship with empty-window digest suppression."""
        settings = self.state_mod.ArchitectSettings(group="g")
        self.assertEqual(settings.architect_push_interval, 300)
        self.assertEqual(settings.architect_max_interval, 600)
        self.assertEqual(settings.architect_heartbeat_interval, 0)
        self.assertTrue(settings.architect_suppress_empty_digests)
        self.assertIn("task_done", settings.architect_enabled_events)

    def test_architect_digest_knobs_round_trip_through_group_settings(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_group_settings(
            "g",
            self.state_mod.GroupSettings(
                architect_push_interval=120,
                architect_max_interval=240,
                architect_heartbeat_interval=600,
                architect_suppress_empty_digests=False,
                architect_enabled_events=["task_done", "task_blocked"],
            ),
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        settings = state.get_architect_settings("g")
        self.assertEqual(settings.architect_push_interval, 120)
        self.assertEqual(settings.architect_max_interval, 240)
        self.assertEqual(settings.architect_heartbeat_interval, 600)
        self.assertFalse(settings.architect_suppress_empty_digests)
        self.assertEqual(
            settings.architect_enabled_events,
            ["task_done", "task_blocked"],
        )

    def test_default_agent_digest_settings_inherits_architect_knobs(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.update_architect_settings(
            "g",
            architect_push_interval=180,
            architect_max_interval=360,
            architect_heartbeat_interval=900,
            architect_suppress_empty_digests=False,
            architect_enabled_events=["task_done"],
        )
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"].append(architect.id)

        defaults = state._default_agent_digest_settings(architect.id, architect)
        self.assertEqual(defaults.push_interval, 180)
        self.assertEqual(defaults.max_interval, 360)
        self.assertEqual(defaults.heartbeat_interval, 900)
        self.assertFalse(defaults.suppress_empty)
        self.assertEqual(defaults.enabled_events, ["task_done"])
        self.assertTrue(defaults.architect_digest)

    def test_default_agent_digest_settings_falls_back_to_default_events(self):
        """Empty architect_enabled_events means 'use default catalog'."""
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.update_architect_settings("g", architect_enabled_events=[])
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"].append(architect.id)

        defaults = state._default_agent_digest_settings(architect.id, architect)
        self.assertIn("task_done", defaults.enabled_events)
        self.assertIn("task_completed", defaults.enabled_events)

    def test_load_backfills_suppress_empty_for_legacy_architect_rows(self):
        """Pre-existing architect digest rows should pick up suppress_empty."""
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["arch-1"]}, {"g": "g"})
        db.save_group_members("g", ["arch-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
                persistent=True,
            )
        )
        # Simulate an architect digest row that predates the new column —
        # heartbeat=300, suppress_empty=False (column default).
        db.save_agent_digest_settings(
            "arch-1",
            {
                "agent_id": "arch-1",
                "heartbeat_interval": 300,
                "architect_digest": True,
                "suppress_empty": False,
            },
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        backfilled = state.get_agent_digest_settings("arch-1")
        self.assertTrue(backfilled.suppress_empty)
        # Persisted, not just in-memory.
        persisted = db.load_all_agent_digest_settings()["arch-1"]
        self.assertTrue(persisted["suppress_empty"])
        # Marker was written so we don't fight a user who later turns it off.
        self.assertEqual(
            db.load_ui_state_value(
                "architect_digest_suppress_empty_backfilled"
            ),
            "1",
        )

    def test_backfill_runs_only_once_respects_user_override(self):
        """Once the marker is set, a user-set False is preserved across reloads."""
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["arch-1"]}, {"g": "g"})
        db.save_group_members("g", ["arch-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
                persistent=True,
            )
        )
        db.save_agent_digest_settings(
            "arch-1",
            {
                "agent_id": "arch-1",
                "architect_digest": True,
                "suppress_empty": False,
            },
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()
        # First load backfills.
        self.assertTrue(
            state.get_agent_digest_settings("arch-1").suppress_empty
        )

        # User explicitly turns it back off.
        state.update_agent_digest_settings("arch-1", suppress_empty=False)
        self.assertFalse(
            state.get_agent_digest_settings("arch-1").suppress_empty
        )

        # Reload — the marker means we do NOT re-flip the user's choice.
        state2 = self.state_mod.MatrixState(db=db)
        state2.load()
        self.assertFalse(
            state2.get_agent_digest_settings("arch-1").suppress_empty
        )

    def test_sync_architect_digest_settings_propagates_new_knobs(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"].append(architect.id)
        # Materialize a per-agent digest row.
        state.update_agent_digest_settings(architect.id)

        state.update_architect_settings(
            "g",
            architect_push_interval=240,
            architect_heartbeat_interval=1200,
            architect_suppress_empty_digests=False,
        )

        per_agent = state.get_agent_digest_settings(architect.id)
        self.assertEqual(per_agent.push_interval, 240)
        self.assertEqual(per_agent.heartbeat_interval, 1200)
        self.assertFalse(per_agent.suppress_empty)

    def test_agent_visibility_to_engineer_enforces_kind_scope(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        peer_engineer = self.state_mod.AgentCell(
            id="engineer-2",
            name="Peer engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        owned = self.state_mod.AgentCell(
            id="agent-owned",
            name="Owned worker",
            group="g",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
            created_by_engineer_id=engineer.id,
        )
        terminal = self.state_mod.AgentCell(
            id="terminal-owned",
            name="Owned terminal",
            group="g",
            cell_type="terminal",
            kind="terminal",
            parent_id=owned.id,
        )
        other_owned = self.state_mod.AgentCell(
            id="agent-other-owned",
            name="Other engineer worker",
            group="g",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=peer_engineer.id,
        )
        other_group = self.state_mod.AgentCell(
            id="agent-other",
            name="Other group worker",
            group="other",
            cell_type="agent",
            kind="worker",
            created_by_engineer_id=engineer.id,
        )
        state.agents = {
            engineer.id: engineer,
            architect.id: architect,
            peer_engineer.id: peer_engineer,
            owned.id: owned,
            terminal.id: terminal,
            other_owned.id: other_owned,
            other_group.id: other_group,
        }
        state.groups["g"] = [
            engineer.id,
            architect.id,
            peer_engineer.id,
            owned.id,
            terminal.id,
            other_owned.id,
        ]
        state.groups["other"] = [other_group.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )

        self.assertTrue(state.agent_is_visible_to_engineer(engineer.id, engineer.id))
        self.assertTrue(state.agent_is_visible_to_engineer(engineer.id, architect.id))
        self.assertTrue(state.agent_is_visible_to_engineer(engineer.id, owned.id))
        self.assertTrue(state.agent_is_visible_to_engineer(engineer.id, terminal.id))
        self.assertFalse(
            state.agent_is_visible_to_engineer(engineer.id, peer_engineer.id)
        )
        self.assertFalse(
            state.agent_is_visible_to_engineer(engineer.id, other_owned.id)
        )
        self.assertFalse(
            state.agent_is_visible_to_engineer(engineer.id, other_group.id)
        )

    def test_board_remove_task_clears_boundary_successor_links(self):
        state = self.state_mod.MatrixState()
        boundary = self.state_mod.BoardTask(
            id="task-1",
            task="Boundary task",
            group="g",
            lane="Done",
            worktree_boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        queued = self.state_mod.BoardTask(
            id="task-2",
            task="Queued follow-up",
            group="g",
            lane="To Do",
            resume_after_boundary_task_id=boundary.id,
        )

        state.board_tasks[boundary.id] = boundary
        state.board_tasks[queued.id] = queued

        state.board_remove_task(boundary.id)

        self.assertEqual(
            state.board_tasks[queued.id].resume_after_boundary_task_id, ""
        )

    def test_engineer_resume_semantics_preserve_non_blocking_notes(self):
        state = self.state_mod.MatrixState()
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(
            group="g",
            pending_question="Need review",
            pending_question_actor_id="eng-1",
            pending_note="FYI: branch is ready",
            pending_note_kind="note",
            paused=True,
        )

        state.update_engineer_settings("g", paused=False, pending_question="")

        ws = state.engineer_settings["g"]
        self.assertEqual(ws.pending_question, "")
        self.assertEqual(ws.pending_question_actor_id, "")
        self.assertFalse(ws.paused)
        self.assertEqual(ws.pending_note, "FYI: branch is ready")
        self.assertEqual(ws.pending_note_kind, "note")

    def test_engineer_and_group_setting_updates_normalize_new_policy_fields(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []

        state.update_engineer_settings(
            "g",
            autonomy_mode="not-a-real-mode",
            default_worker_concurrency=0,
            wave_size_preference="gigantic",
            same_agent_follow_up_preference="always",
            digest_verbosity="wall-of-text",
            escalation_style="shrug",
            restrict_to_created_agents=1,
        )
        state.update_group_settings(
            "g",
            worktree_merge_cleanup="???",
            worktree_merge_preserve_diff=True,
        )

        ws = state.engineer_settings["g"]
        gs = state.group_settings["g"]
        self.assertEqual(ws.autonomy_mode, "dispatch_when_clear")
        self.assertEqual(ws.default_worker_concurrency, 1)
        self.assertEqual(ws.wave_size_preference, "small")
        self.assertEqual(ws.same_agent_follow_up_preference, "balanced")
        self.assertEqual(ws.digest_verbosity, "balanced")
        self.assertEqual(ws.escalation_style, "note_then_ask")
        self.assertTrue(ws.restrict_to_created_agents)
        self.assertEqual(gs.worktree_merge_cleanup, "keep")
        self.assertTrue(gs.worktree_merge_preserve_diff)

    def test_history_record_dispatch_persists_engineer_worklog_and_survives_reload(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            created_by_engineer_id=engineer.id,
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [engineer.id, worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(group="g")
        state._db_save_groups()
        state._db_save_group_settings("g")
        state._db_save_agent(engineer)
        state._db_save_agent(worker)
        state.history_record_agent(engineer)
        state.history_record_agent(worker)

        task = state.board_add_task(
            "Ship Worklog tab",
            "g",
            lane="In Progress",
            id="LOOM:1",
            agent_id=worker.id,
        )

        state.history_record_dispatch(
            worker,
            task,
            engineer_group="g",
            engineer_id=engineer.id,
        )

        self.assertEqual(state.engineer_worklog["g"][0]["task_id"], task.id)
        self.assertTrue(state.engineer_worklog["g"][0]["agent_owned"])

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        self.assertEqual(reloaded.engineer_worklog["g"][0]["task_id"], task.id)
        self.assertEqual(
            reloaded.to_dict()["engineer_worklog"]["g"][0]["agent_name"],
            "Worker",
        )


class MatrixStatePauseSuppressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self, *, paused=True):
        state = self.state_mod.MatrixState()
        state.groups["g"] = ["eng-1", "worker-1"]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id="eng-1"
        )
        state.agents["eng-1"] = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents["worker-1"] = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            owner_engineer_id="eng-1",
        )
        state.update_agent_digest_settings("eng-1", paused=paused)
        state._delta_ops.clear()
        return state

    async def test_paused_digest_queues_subagent_messages_until_resume(self):
        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(json.loads(msg))

        state = self._make_state(paused=True)
        ws = FakeWS()
        state._ws_clients.add(ws)

        state._emit(
            "event_append",
            id=1,
            timestamp=1,
            kind="agent_progress",
            group="g",
            cell_id="worker-1",
            agent_name="Worker",
            message="first",
            task_id="",
        )
        state._emit(
            "mcp_call_append",
            group="g",
            call={
                "cell_id": "worker-1",
                "tool_name": "mcp__loom__loom_progress",
                "hook_event_name": "PostToolUse",
                "appended_at": 2,
            },
        )

        self.assertEqual(state._delta_ops, [])
        self.assertEqual(len(state._paused_subagent_delta_ops), 2)
        await state.broadcast()
        self.assertEqual(ws.messages, [])

        state.update_agent_digest_settings("eng-1", paused=False)
        await state.broadcast()

        self.assertEqual(len(ws.messages), 1)
        ops = ws.messages[0]["ops"]
        self.assertEqual(
            [op["op"] for op in ops],
            ["agent_digest_update", "event_append", "mcp_call_append"],
        )
        self.assertEqual(ops[1]["id"], 1)
        self.assertEqual(ops[2]["call"]["cell_id"], "worker-1")
        self.assertEqual(state._paused_subagent_delta_ops, [])

        await state.broadcast()
        self.assertEqual(len(ws.messages), 1)

    async def test_unpaused_digest_broadcasts_subagent_messages_normally(self):
        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(json.loads(msg))

        state = self._make_state(paused=False)
        ws = FakeWS()
        state._ws_clients.add(ws)

        state._emit(
            "event_append",
            id=1,
            timestamp=1,
            kind="agent_progress",
            group="g",
            cell_id="worker-1",
            agent_name="Worker",
            message="first",
            task_id="",
        )
        await state.broadcast()

        self.assertEqual(len(ws.messages), 1)
        self.assertEqual(
            [op["op"] for op in ws.messages[0]["ops"]],
            ["event_append"],
        )

    def test_paused_subagent_queue_cap_drops_oldest(self):
        state = self._make_state(paused=True)
        limit = self.state_mod.PAUSED_SUBAGENT_DELTA_QUEUE_LIMIT

        with self.assertLogs("loom", level="WARNING") as logs:
            for idx in range(limit + 5):
                state._emit(
                    "event_append",
                    id=idx,
                    timestamp=idx,
                    kind="agent_progress",
                    group="g",
                    cell_id="worker-1",
                    agent_name="Worker",
                    message=str(idx),
                    task_id="",
                )

        self.assertEqual(len(state._paused_subagent_delta_ops), limit)
        self.assertEqual(state._paused_subagent_delta_ops[0]["id"], 5)
        self.assertEqual(state._paused_subagent_delta_ops[-1]["id"], limit + 4)
        self.assertTrue(
            any("Paused subagent message queue exceeded" in line for line in logs.output)
        )

class MatrixStateBoardWorkflowTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def test_resolve_board_task_id_prefers_alias_over_archived_literal(self):
        state = self._make_state()
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Archived task",
            group="g",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Live task",
            group="g",
            lane="Backlog",
        )
        state.board_tasks[archived.id] = archived
        state.board_tasks[live.id] = live
        state.task_id_aliases[archived.id] = live.id

        self.assertEqual(state.resolve_task_alias("LOOM:51"), live.id)
        self.assertEqual(state.resolve_board_task_id("LOOM:51"), live.id)
        self.assertEqual(state.resolve_board_task_id("LOOM:5"), live.id)

    def test_board_add_task_archived_literal_collision_creates_persisted_alias(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["Loom"] = []
        state._db_save_groups()
        state.task_id_counters["LOOM"] = 51
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Archived original",
            group="Loom",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        state.board_tasks[archived.id] = archived
        state._db_save_task(archived)

        task = state.board_add_task("New live task", "Loom")

        self.assertIsNotNone(task)
        self.assertNotEqual(task.id, "LOOM:51")
        self.assertEqual(len(task.id), 8)
        self.assertEqual(state.task_id_aliases["LOOM:51"], task.id)
        self.assertTrue(db.board_task_exists(task.id))
        self.assertTrue(db.board_task_exists("LOOM:51"))
        self.assertEqual(state.resolve_board_task_id("LOOM:51"), task.id)

    def test_board_update_task_alias_persists_missing_canonical_and_full_delta(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Highlight Events panel header",
            group="g",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Keep track of which agent moved a task",
            group="g",
            lane="Backlog",
        )
        db.save_board_task(archived)
        db.save_task_id_alias("LOOM:51", live.id)
        state.board_tasks[archived.id] = archived
        state.board_tasks[live.id] = live
        state.task_id_aliases["LOOM:51"] = live.id

        state.board_update_task(
            "LOOM:51",
            description="Architect-written description",
            action_name="feature/implement",
            assigned_engineer_id="eng-1",
        )

        updated = state.board_tasks[live.id]
        self.assertEqual(updated.description, "Architect-written description")
        self.assertEqual(updated.action_name, "feature/implement")
        self.assertEqual(updated.assigned_engineer_id, "eng-1")
        self.assertEqual(state.board_tasks["LOOM:51"].task, archived.task)
        self.assertTrue(db.board_task_exists(live.id))
        row = db._conn.execute(
            "SELECT description, action_name, assigned_engineer_id "
            "FROM board_tasks WHERE id=?",
            (live.id,),
        ).fetchone()
        self.assertEqual(
            row,
            ("Architect-written description", "feature/implement", "eng-1"),
        )
        archived_row = db._conn.execute(
            "SELECT task, description FROM board_tasks WHERE id='LOOM:51'"
        ).fetchone()
        self.assertEqual(archived_row, (archived.task, ""))
        task_ops = [
            op for op in state._delta_ops
            if op.get("op") == "task_upsert" and op.get("id") == live.id
        ]
        self.assertTrue(task_ops)
        full_delta = task_ops[0]
        self.assertEqual(full_delta["description"], "Architect-written description")
        self.assertEqual(full_delta["action_name"], "feature/implement")
        self.assertEqual(full_delta["assigned_engineer_id"], "eng-1")
        self.assertIn("created_at", full_delta)
        self.assertIn("health_details", full_delta)

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()
        self.assertEqual(reloaded.resolve_board_task_id("LOOM:51"), live.id)
        self.assertEqual(
            reloaded.board_tasks[live.id].description,
            "Architect-written description",
        )

    def test_board_add_task_allocates_group_scoped_root_ids(self):
        state = self.state_mod.MatrixState()
        state.groups["Loom Team"] = []
        state.groups["Ops"] = []

        first = state.board_add_task("First task", "Loom Team")
        second = state.board_add_task("Second task", "Loom Team")
        third = state.board_add_task("Ops task", "Ops")

        self.assertEqual(first.id, "LOOM_TEAM:1")
        self.assertEqual(second.id, "LOOM_TEAM:2")
        self.assertEqual(third.id, "OPS:1")

    def test_board_add_task_transliterates_accented_group_names(self):
        state = self.state_mod.MatrixState()
        state.groups["Atlas Público"] = []

        task = state.board_add_task("Localization", "Atlas Público")

        self.assertEqual(task.id, "ATLAS_PUBLICO:1")

    def test_board_add_task_allocates_pipeline_scoped_child_ids_across_groups(self):
        state = self.state_mod.MatrixState()
        state.groups["Loom"] = []
        state.groups["Review Team"] = []

        root = state.board_add_task("Root", "Loom")
        child = state.board_add_task(
            "Implement",
            "Loom",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )
        cross_group = state.board_add_task(
            "Review",
            "Review Team",
            parent_task_id=child.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )

        self.assertEqual(root.id, "LOOM:1")
        self.assertEqual(child.id, "LOOM:1:1")
        self.assertEqual(cross_group.id, "REVIEW_TEAM:1:2")

    def test_started_descendant_handoff_frees_parent_execution_slot(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id="agent-1",
        )
        child = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-child",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
            agent_id="agent-2",
        )
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
        )

        self.assertIsNotNone(parent)
        self.assertIsNotNone(child)
        self.assertTrue(state.task_has_live_handoff_descendants(parent.id))
        self.assertIsNone(state.agent_current_task("agent-1"))
        self.assertFalse(state.agent_is_busy("agent-1"))

    def test_plain_backlog_child_does_not_free_parent_execution_slot(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id="agent-1",
        )
        child = state.board_add_task(
            "Follow-up draft",
            "g",
            lane="Backlog",
            id="task-child",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
        )
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
        )

        self.assertIsNotNone(parent)
        self.assertIsNotNone(child)
        self.assertFalse(state.task_has_live_handoff_descendants(parent.id))
        self.assertEqual(state.agent_current_task("agent-1").id, parent.id)
        self.assertTrue(state.agent_is_busy("agent-1"))

    def test_agent_pending_engineer_reply_tasks_only_include_open_tasks_for_worker(self):
        state = self._make_state()
        state.board_add_task(
            "Answered thread",
            "g",
            lane="Done",
            id="task-done",
            reply_agent_id="agent-1",
            labels=["loom:engineer-message"],
        )
        pending_old = state.board_add_task(
            "Older thread",
            "g",
            lane="Backlog",
            id="task-old",
            reply_agent_id="agent-1",
            labels=["loom:engineer-message"],
        )
        pending_new = state.board_add_task(
            "Newer thread",
            "g",
            lane="Backlog",
            id="task-new",
            reply_agent_id="agent-1",
            labels=["loom:engineer-message"],
        )
        state.board_add_task(
            "Other worker thread",
            "g",
            lane="Backlog",
            id="task-other",
            reply_agent_id="agent-2",
            labels=["loom:engineer-message"],
        )

        pending = state.agent_pending_engineer_reply_tasks("agent-1")

        self.assertEqual([task.id for task in pending], [pending_old.id, pending_new.id])

    def test_load_restores_pending_engineer_message_from_open_followup_tasks(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["agent-1"]}, {"g": "g"})
        db.save_group_members("g", ["agent-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-reply",
                task="Engineer: Check status",
                group="g",
                lane="Backlog",
                reply_agent_id="agent-1",
                labels=["loom:engineer-message"],
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertTrue(state.agents["agent-1"].pending_engineer_message)
        self.assertEqual(
            [task.id for task in state.agent_pending_engineer_reply_tasks("agent-1")],
            ["task-reply"],
        )

    def test_queued_follow_up_becomes_current_task_over_suspended_parent(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id="agent-1",
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
            agent_id="agent-2",
        )
        follow_up = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="task-fix",
            parent_task_id="task-review",
            pipeline_root_id="task-parent",
            pipeline_depth=2,
            agent_id="agent-1",
        )
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
        )

        self.assertIsNotNone(parent)
        self.assertIsNotNone(review)
        self.assertIsNotNone(follow_up)
        self.assertEqual(state.agent_current_task("agent-1").id, follow_up.id)
        self.assertTrue(state.agent_is_busy("agent-1"))

    def test_done_child_cascades_single_level_parent_to_done(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            status="On review",
            health_state="stalled",
            health_details={"reasons": ["no_progress_timeout"]},
        )
        child = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )

        state._delta_ops.clear()
        state.board_move_task(child.id, "Done")

        self.assertEqual(state.board_tasks[child.id].lane, "Done")
        self.assertEqual(state.board_tasks[parent.id].lane, "Done")
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[parent.id].health_state, "healthy")
        upsert_ids = [
            op.get("id") for op in state._delta_ops
            if op.get("op") == "task_upsert"
        ]
        self.assertIn(child.id, upsert_ids)
        self.assertIn(parent.id, upsert_ids)

    def test_done_cascades_multi_pass_review_chain_after_ship_verdict(self):
        state = self._make_state()
        root = state.board_add_task(
            "Implement cascading completion",
            "g",
            lane="In Progress",
            id="LOOM:77",
            status="On review",
            health_state="idle-risk",
            health_details={"reasons": ["progress_silence_warning"]},
        )
        review_one = state.board_add_task(
            "Review pass 1",
            "g",
            lane="In Progress",
            id="LOOM:77:1",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            status="Fixing",
            health_state="stalled",
            health_details={"reasons": ["no_progress_timeout"]},
        )
        fix_one = state.board_add_task(
            "Fix review pass 1",
            "g",
            lane="In Progress",
            id="LOOM:77:2",
            parent_task_id=review_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )
        review_two = state.board_add_task(
            "Review pass 2",
            "g",
            lane="In Progress",
            id="LOOM:77:3",
            parent_task_id=fix_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=3,
        )
        fix_two = state.board_add_task(
            "Fix review pass 2",
            "g",
            lane="In Progress",
            id="LOOM:77:4",
            parent_task_id=review_two.id,
            pipeline_root_id=root.id,
            pipeline_depth=4,
        )
        final_review = state.board_add_task(
            "Final review pass",
            "g",
            lane="In Progress",
            id="LOOM:77:5",
            parent_task_id=fix_two.id,
            pipeline_root_id=root.id,
            pipeline_depth=5,
            messages=[{
                "timestamp": 1.0,
                "action": "done",
                "message": "Verdict: Ship — no blocking issues",
                "agent_name": "Reviewer",
            }],
        )

        state._delta_ops.clear()
        state.board_move_task(final_review.id, "Done")

        chain = state.board_get_chain(root.id)
        self.assertEqual(
            [(task.id, task.lane) for task in chain],
            [
                ("LOOM:77", "Done"),
                ("LOOM:77:1", "Done"),
                ("LOOM:77:2", "Done"),
                ("LOOM:77:3", "Done"),
                ("LOOM:77:4", "Done"),
                ("LOOM:77:5", "Done"),
            ],
        )
        self.assertEqual(state.board_tasks[root.id].status, "")
        self.assertEqual(state.board_tasks[review_one.id].status, "")
        self.assertEqual(state.board_tasks[root.id].health_state, "healthy")
        self.assertEqual(
            state.board_tasks[review_one.id].health_state,
            "healthy",
        )
        upsert_ids = [
            op.get("id") for op in state._delta_ops
            if op.get("op") == "task_upsert"
        ]
        for task in chain:
            self.assertIn(task.id, upsert_ids)

    def test_done_cascade_waits_for_open_sibling(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
        )
        first_child = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        second_child = state.board_add_task(
            "Follow-up fix",
            "g",
            lane="To Do",
            id="task-fix",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )

        state.board_move_task(first_child.id, "Done")

        self.assertEqual(state.board_tasks[first_child.id].lane, "Done")
        self.assertEqual(state.board_tasks[second_child.id].lane, "To Do")
        self.assertEqual(state.board_tasks[parent.id].lane, "In Progress")

    def test_done_cascade_ignores_engineer_follow_up_replies(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            status="Reviewing",
        )
        follow_up = state.board_add_task(
            "Engineer: Need status",
            "g",
            lane="Backlog",
            id="task-reply",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            labels=["loom:derived", "loom:engineer-message"],
        )

        state.board_move_task(follow_up.id, "Done")

        self.assertEqual(state.board_tasks[follow_up.id].lane, "Done")
        self.assertEqual(state.board_tasks[parent.id].lane, "In Progress")
        self.assertEqual(state.board_tasks[parent.id].status, "Reviewing")

    def test_engineer_message_followup_predicate_uses_system_label(self):
        task = self.state_mod.BoardTask(
            id="task-reply",
            task="Engineer: Need status",
            group="g",
            lane="Backlog",
            labels=["loom:derived", "loom:engineer-message"],
        )
        normal = self.state_mod.BoardTask(
            id="task-normal",
            task="Implement feature",
            group="g",
            lane="Backlog",
            labels=["loom:derived"],
        )

        self.assertTrue(
            self.state_mod.task_is_engineer_message_followup(task)
        )
        self.assertFalse(
            self.state_mod.task_is_engineer_message_followup(normal)
        )
        self.assertFalse(
            self.state_mod.task_is_engineer_message_followup(None)
        )

    def test_done_cascade_does_not_complete_engineer_follow_up_parent(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Engineer: Need status",
            "g",
            lane="In Progress",
            id="task-reply",
            labels=["loom:derived", "loom:engineer-message"],
        )
        child = state.board_add_task(
            "Investigate reply",
            "g",
            lane="In Progress",
            id="task-reply-child",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            labels=["loom:derived"],
        )

        state.board_move_task(child.id, "Done")

        self.assertEqual(state.board_tasks[child.id].lane, "Done")
        self.assertEqual(state.board_tasks[parent.id].lane, "In Progress")

    def test_board_update_task_done_lane_uses_same_cascade(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
        )
        child = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )

        state.board_update_task(child.id, lane="Done")

        self.assertEqual(state.board_tasks[child.id].lane, "Done")
        self.assertEqual(state.board_tasks[parent.id].lane, "Done")

    def test_add_group_rejects_prefix_collisions(self):
        state = self.state_mod.MatrixState()

        state.add_group("Foo Bar")
        state.add_group("Foo-Bar")

        self.assertIn("Foo Bar", state.groups)
        self.assertNotIn("Foo-Bar", state.groups)

    def test_board_task_lifecycle_covers_creation_editing_and_cleanup(self):
        state = self._make_state()
        dep = state.board_add_task(
            "Dependency task",
            "g",
            lane="To Do",
            id="dep-1",
            labels=["ready"],
        )
        task = state.board_add_task(
            "Ship feature",
            "g",
            description="Initial description",
            id="task-1",
            labels=["loom:blocked", "keep"],
            depends_on=["dep-1", "missing-task"],
            verification_mode="deploy",
            verification_state="pending",
            verification_notes="Need manual smoke on staging",
            verification_summary={
                "tests_run": "python3 -m unittest",
                "deploy_needed": True,
            },
            artifacts=[{
                "type": "log",
                "title": "build.log",
                "path": "/tmp/build.log",
                "summary": "Compile failed in auth module",
                "prompt": {"mode": "summary"},
            }],
        )

        self.assertIsNotNone(dep)
        self.assertIsNotNone(task)
        self.assertEqual(task.lane, "Backlog")
        self.assertEqual(task.position, 0)
        self.assertEqual(task.lane_entered_at, task.created_at)
        self.assertEqual(task.depends_on, ["dep-1"])
        self.assertEqual(task.artifacts[0]["type"], "log")
        self.assertEqual(task.artifacts[0]["prompt"]["mode"], "summary")
        self.assertEqual(task.verification_mode, "deploy")
        self.assertEqual(task.verification_state, "pending")
        self.assertEqual(
            task.verification_summary["tests_run"],
            "python3 -m unittest",
        )

        original_slug = task.slug
        state.board_update_task(
            task.id,
            task="Ship feature safely",
            description="Updated description",
            labels=["loom:error", "keep"],
            scheduled_at="2026-04-07T10:00:00+00:00",
            verification_state="failed",
            verification_notes="Smoke failed on login redirect",
            verification_summary={
                "tests_run": "python3 -m unittest tests.test_auth",
                "manual_smoke_done": True,
                "deploy_needed": True,
                "deploy_attempted": True,
                "human_validation_pending": "Confirm prod login redirect",
            },
            artifacts=[{
                "type": "snippet",
                "title": "failing example",
                "content": "assert actual == expected",
                "prompt": {"mode": "auto"},
            }],
        )

        updated = state.board_tasks[task.id]
        self.assertEqual(updated.description, "Updated description")
        self.assertEqual(updated.labels, ["loom:error", "keep"])
        self.assertEqual(updated.scheduled_at, "2026-04-07T10:00:00+00:00")
        self.assertEqual(updated.artifacts[0]["type"], "snippet")
        self.assertEqual(updated.artifacts[0]["storage"]["kind"], "inline")
        self.assertEqual(updated.verification_state, "failed")
        self.assertTrue(updated.verification_summary["manual_smoke_done"])
        self.assertEqual(
            updated.verification_summary["human_validation_pending"],
            "Confirm prod login redirect",
        )
        self.assertNotEqual(updated.slug, original_slug)
        self.assertEqual(updated.lane_entered_at, task.created_at)

        updated.lane_entered_at = "2026-04-06T00:30:00+00:00"
        state.board_move_task(task.id, "Done")

        finished = state.board_tasks[task.id]
        self.assertEqual(finished.lane, "Done")
        self.assertEqual(finished.labels, ["keep"])
        self.assertNotEqual(
            finished.lane_entered_at,
            "2026-04-06T00:30:00+00:00",
        )

        state.board_remove_task(dep.id)

        self.assertEqual(state.board_tasks[task.id].depends_on, [])

    def test_dependency_updates_strip_invalid_entries_and_reject_cycles(self):
        state = self._make_state()
        task_a = state.board_add_task("Task A", "g", id="task-a")
        task_b = state.board_add_task("Task B", "g", id="task-b")
        task_c = state.board_add_task("Task C", "g", id="task-c")

        self.assertIsNotNone(task_a)
        self.assertIsNotNone(task_b)
        self.assertIsNotNone(task_c)

        state.board_update_task(
            task_a.id,
            depends_on=[task_a.id, "missing-task", task_b.id],
        )
        self.assertEqual(state.board_tasks[task_a.id].depends_on, [task_b.id])

        state.board_update_task(task_b.id, depends_on=[task_c.id])
        self.assertFalse(state.board_deps_met(state.board_tasks[task_a.id]))
        self.assertEqual(
            [t.id for t in state.board_get_dependents(task_b.id)],
            [task_a.id],
        )

        state.board_update_task(task_c.id, depends_on=[task_a.id])

        self.assertEqual(state.board_tasks[task_c.id].depends_on, [])

        state.board_move_task(task_b.id, "Done")
        self.assertTrue(state.board_deps_met(state.board_tasks[task_a.id]))

    def test_archive_and_restore_preserve_source_lane_and_done_dependencies(self):
        state = self._make_state()
        dep = state.board_add_task("Dependency", "g", id="dep-1")
        task = state.board_add_task(
            "Follow-up",
            "g",
            id="task-1",
            depends_on=["dep-1"],
        )

        self.assertIsNotNone(dep)
        self.assertIsNotNone(task)

        state.board_move_task(dep.id, "Done")
        self.assertTrue(state.board_deps_met(state.board_tasks[task.id]))

        state.board_archive_task(dep.id)

        archived = state.board_tasks[dep.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.archived_from_lane, "Done")
        self.assertTrue(archived.archived_at)
        self.assertTrue(state.board_deps_met(state.board_tasks[task.id]))

        state.board_unarchive_task(dep.id)

        restored = state.board_tasks[dep.id]
        self.assertEqual(restored.lane, "Done")
        self.assertEqual(restored.archived_at, "")
        self.assertEqual(restored.archived_from_lane, "")
        self.assertTrue(state.board_deps_met(state.board_tasks[task.id]))

    def test_archiving_active_task_unlinks_agent_and_clears_busy_state(self):
        state = self._make_state()
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            current_task_id="task-1",
        )
        state.agents[agent.id] = agent
        state.groups["g"] = [agent.id]
        task = state.board_add_task(
            "In-flight work",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id=agent.id,
        )

        self.assertIsNotNone(task)
        self.assertTrue(state.agent_is_busy(agent.id))

        state.board_archive_task(task.id)

        archived = state.board_tasks[task.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.archived_from_lane, "In Progress")
        self.assertEqual(archived.agent_id, "")
        self.assertFalse(state.agent_is_busy(agent.id))
        self.assertIsNone(state.agent_current_task(agent.id))

    def test_board_update_task_routes_archived_lane_through_archive_semantics(self):
        state = self._make_state()
        task = state.board_add_task("Ship release", "g", lane="Done", id="task-1")

        self.assertIsNotNone(task)

        state.board_update_task(task.id, lane="Archived", description="Keep for reference")

        archived = state.board_tasks[task.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.archived_from_lane, "Done")
        self.assertEqual(archived.description, "Keep for reference")

    def test_board_move_task_clears_status_for_archived_noop_and_persists(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        state._db_save_groups()
        task = state.board_add_task(
            "Archived task",
            "g",
            lane="Backlog",
            id="task-1",
            status="Fixing",
        )

        self.assertIsNotNone(task)

        state.board_archive_task(task.id)
        state._delta_ops.clear()
        state.board_move_task(task.id, "Archived", clear_status=True)

        archived = state.board_tasks[task.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.status, "")
        row = db._conn.execute(
            "SELECT status FROM board_tasks WHERE id=?",
            (task.id,),
        ).fetchone()
        self.assertEqual(row, ("",))
        task_upserts = [
            op for op in state._delta_ops
            if op.get("op") == "task_upsert" and op.get("id") == task.id
        ]
        self.assertTrue(task_upserts)
        self.assertEqual(task_upserts[-1]["lane"], "Archived")
        self.assertEqual(task_upserts[-1]["status"], "")

    def test_lane_transition_timestamp_tracks_update_and_remove_lane_moves(self):
        state = self._make_state()
        state.board_add_lane("Review")
        task = state.board_add_task(
            "Review the patch",
            "g",
            lane="Backlog",
            id="task-review",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.lane_entered_at, task.created_at)

        original_lane_entered_at = task.lane_entered_at
        state.board_update_task(task.id, lane="Review")

        moved = state.board_tasks[task.id]
        self.assertEqual(moved.lane, "Review")
        self.assertNotEqual(moved.lane_entered_at, original_lane_entered_at)

        before_remove = moved.lane_entered_at
        state.board_remove_lane("Review", move_tasks_to="To Do")

        moved_again = state.board_tasks[task.id]
        self.assertEqual(moved_again.lane, "To Do")
        self.assertNotEqual(moved_again.lane_entered_at, before_remove)

    def test_schedule_crud_tracks_due_items_and_slug_updates(self):
        state = self._make_state()
        due = state.schedule_add(
            "Morning sync",
            "g",
            cron_expr="0 8 * * *",
            next_run_at="2026-04-06T08:00:00+00:00",
            labels=["ops"],
        )
        later = state.schedule_add(
            "Weekly review",
            "g",
            scheduled_at="2026-04-08T08:00:00+00:00",
            next_run_at="2026-04-08T08:00:00+00:00",
        )

        self.assertIsNotNone(due)
        self.assertIsNotNone(later)
        self.assertEqual(
            [s.id for s in state.schedule_get_due("2026-04-06T08:00:00+00:00")],
            [due.id],
        )

        state.schedule_update(due.id, name="Morning standup", enabled=False)

        updated = state.schedules[due.id]
        self.assertEqual(updated.slug, "morning-standup")
        self.assertFalse(updated.enabled)
        self.assertEqual(
            state.schedule_get_due("2026-04-09T08:00:00+00:00"),
            [later],
        )

    def test_default_engineer_specializations_persist_via_update(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")

        state.update_group_settings(
            "g",
            default_engineer_specializations=["ui-frontend", "react"],
        )

        gs = state.group_settings["g"]
        self.assertEqual(
            gs.default_engineer_specializations, ["ui-frontend", "react"])

    def test_default_engineer_specializations_can_be_cleared(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings(
            "g",
            default_engineer_specializations=["ui-frontend"],
        )

        state.update_group_settings("g", default_engineer_specializations=[])

        gs = state.group_settings["g"]
        self.assertEqual(gs.default_engineer_specializations, [])


class MatrixStateEngineerStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state_with_open_stream(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.engineer_settings["g"] = self.state_mod.EngineerSettings(group="g")

        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker",
            git_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        product = self.state_mod.BoardTask(
            id="LOOM:1",
            task="Add Events tab",
            group="g",
            lane="Done",
            action_name="feature/implement",
            agent_id=worker.id,
            created_at="2026-04-07T10:00:00+00:00",
            updated_at="2026-04-07T10:30:00+00:00",
            lane_entered_at="2026-04-07T10:00:00+00:00",
        )
        review = self.state_mod.BoardTask(
            id="LOOM:1:1",
            task="Review Events implementation",
            group="g",
            lane="Done",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            agent_id=worker.id,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            lane_entered_at="2026-04-07T11:00:00+00:00",
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        return state, product

    def test_to_dict_includes_engineer_streams_snapshot(self):
        state, _product = self._make_state_with_open_stream()

        payload = state.to_dict()

        self.assertIn("engineer_streams", payload)
        self.assertIn("g", payload["engineer_streams"])
        summary = payload["engineer_streams"]["g"]
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["items"][0]["branch"], "loom/worker")
        self.assertEqual(summary["items"][0]["state"], "ready_to_merge")

    async def test_snapshot_msg_async_round_trips_state_payload(self):
        state, _product = self._make_state_with_open_stream()

        raw = await state.snapshot_msg_async()

        self.assertEqual(json.loads(raw), {
            "type": "state",
            "seq": state._seq,
            **state.to_dict(),
        })

    async def test_broadcast_appends_engineer_stream_deltas_for_task_changes(self):
        state, product = self._make_state_with_open_stream()

        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(json.loads(msg))

        ws = FakeWS()
        state._ws_clients.add(ws)
        state._emit("task_upsert", **self.state_mod.asdict(product))

        # Primary broadcast: UI-facing ops land immediately; the expensive
        # engineer-stream recompute is deferred to a background worker so
        # mutations don't wait on `git for-each-ref`.
        await state.broadcast()

        self.assertEqual(len(ws.messages), 1, ws.messages)
        primary_ops = ws.messages[0]["ops"]
        self.assertEqual(
            [op["op"] for op in primary_ops if op["op"] == "task_upsert"],
            ["task_upsert"],
        )
        self.assertFalse(
            any(op["op"] == "engineer_streams" for op in primary_ops),
            "engineer_streams should not block the primary delta frame",
        )

        # Drain the deferred recompute and its follow-up broadcast.
        self.assertIsNotNone(state._engineer_recompute_task)
        await state._engineer_recompute_task

        self.assertEqual(len(ws.messages), 2, ws.messages)
        followup_ops = ws.messages[1]["ops"]
        stream_ops = [
            op for op in followup_ops if op["op"] == "engineer_streams"
        ]
        self.assertEqual(len(stream_ops), 1)
        self.assertEqual(stream_ops[0]["group"], "g")
        self.assertEqual(stream_ops[0]["streams"]["count"], 1)
        self.assertEqual(
            stream_ops[0]["streams"]["items"][0]["branch"],
            "loom/worker",
        )


class AgentCellActivityClockTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_heartbeat_updates_only_heartbeat_and_activity_alias(self):
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            last_progress_at=100.0,
            last_heartbeat_at=100.0,
        )

        changed = cell.mark_heartbeat(200.0)

        self.assertTrue(changed)
        self.assertEqual(cell.last_progress_at, 100.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)
        self.assertEqual(cell.last_event_at, 200.0)

    def test_progress_updates_progress_heartbeat_and_activity_alias(self):
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            last_progress_at=100.0,
            last_heartbeat_at=150.0,
        )

        changed = cell.mark_progress(200.0)

        self.assertTrue(changed)
        self.assertEqual(cell.last_progress_at, 200.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)
        self.assertEqual(cell.last_event_at, 200.0)

    def test_heartbeat_only_split_clock_survives_construction_and_reload(self):
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            last_progress_at=0.0,
            last_heartbeat_at=200.0,
            last_activity_at=200.0,
        )

        self.assertEqual(cell.last_progress_at, 0.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)

        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": [cell.id]}, {"g": "g"})
        db.save_group_members("g", [cell.id])
        db.save_agent(cell)

        state = self.state_mod.MatrixState(db=db)
        state.load()

        loaded = state.agents[cell.id]
        self.assertEqual(loaded.last_progress_at, 0.0)
        self.assertEqual(loaded.last_heartbeat_at, 200.0)
        self.assertEqual(loaded.last_activity_at, 200.0)

    def test_legacy_mixed_clock_still_backfills_split_fields(self):
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            last_event_at=123.0,
        )

        self.assertEqual(cell.last_progress_at, 123.0)
        self.assertEqual(cell.last_heartbeat_at, 123.0)
        self.assertEqual(cell.last_activity_at, 123.0)


class SelectedPrincipalIdTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_default_is_empty_string(self):
        state = self.state_mod.MatrixState()
        self.assertEqual(state.selected_principal_id, "")

    def test_to_dict_includes_selected_principal_id(self):
        state = self.state_mod.MatrixState()
        state.selected_principal_id = "architect-a"
        d = state.to_dict()
        self.assertEqual(d["selected_principal_id"], "architect-a")

    def test_to_dict_compact_includes_selected_principal_id(self):
        state = self.state_mod.MatrixState()
        state.selected_principal_id = "architect-b"
        d = state.to_dict_compact()
        self.assertEqual(d["selected_principal_id"], "architect-b")

    def test_persists_and_restores_selected_principal_id(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        # A group is required for MatrixState.load() to exit its fast-path
        # early return for empty databases.
        db.save_groups({"g": []}, {"g": "g"})
        db.save_ui_state("selected_principal_id", "architect-42")

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.selected_principal_id, "architect-42")

    def test_defaults_to_empty_when_ui_state_missing(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.selected_principal_id, "")
