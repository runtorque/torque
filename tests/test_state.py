import asyncio
import importlib
import json
import sys
import tempfile
import threading
import time
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
        self.state_mod = importlib.import_module("torque.state")
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
            "path": Path("/tmp/torque"),
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_update_global_settings_validates_xterm_scrollback(self):
        state = self.state_mod.MatrixState()
        state.update_global_settings(xterm_scrollback=4096)
        self.assertEqual(state.global_settings.xterm_scrollback, 4096)

        with self.assertRaises(ValueError):
            state.update_global_settings(xterm_scrollback=99)

        self.assertEqual(state.global_settings.xterm_scrollback, 4096)

    def test_delta_observer_filters_unrelated_operations(self):
        state = self.state_mod.MatrixState()
        observed = []
        unregister = state.register_delta_observer(
            observed.append,
            ops={"task_upsert"},
        )

        state._emit("agent_upsert", id="agent-1", value={"status": "working"})
        state._emit("task_upsert", id="task-1", value={"task": "Work"})

        self.assertEqual(
            observed,
            [{"op": "task_upsert", "id": "task-1", "value": {"task": "Work"}}],
        )
        unregister()
        state._emit("task_upsert", id="task-2", value={"task": "More"})
        self.assertEqual(len(observed), 1)

    def test_unfiltered_delta_observer_keeps_legacy_behavior(self):
        state = self.state_mod.MatrixState()
        observed = []
        state.register_delta_observer(observed.append)

        state._emit("agent_remove", id="agent-1")

        self.assertEqual(observed, [{"op": "agent_remove", "id": "agent-1"}])

    def test_update_global_settings_normalizes_relay_fields(self):
        state = self.state_mod.MatrixState()
        state.update_global_settings(
            relay_enabled="true",
            relay_url="  wss://relay.example/ws  ",
            relay_daemon_id=" daemon-3 ",
            relay_credential_id="cred-3",
            relay_private_key_path="  /keys/relay.pem ",
        )
        gs = state.global_settings
        self.assertIs(gs.relay_enabled, True)
        self.assertEqual(gs.relay_url, "wss://relay.example/ws")
        self.assertEqual(gs.relay_daemon_id, "daemon-3")
        self.assertEqual(gs.relay_credential_id, "cred-3")
        self.assertEqual(gs.relay_private_key_path, "/keys/relay.pem")

        state.update_global_settings(relay_enabled="false")
        self.assertIs(state.global_settings.relay_enabled, False)

    def test_ai_global_settings_defaults_are_non_secret(self):
        state = self.state_mod.MatrixState()
        gs = state.global_settings

        self.assertFalse(gs.ai_enabled)
        self.assertEqual(gs.ai_generation_provider, "anthropic")
        self.assertEqual(gs.ai_anthropic_model, "")
        self.assertEqual(gs.ai_openai_compatible_base_url, "")
        self.assertEqual(gs.ai_openai_compatible_model, "")
        self.assertEqual(gs.ai_embedding_model, "BAAI/bge-m3")
        self.assertEqual(gs.ai_embedding_runtime, "sentence_transformers")
        self.assertEqual(
            gs.ai_index_corpus,
            {
                "architect_journals": True,
                "engineer_journals": True,
                "decisions": True,
                "tasks": True,
                "engineer_peer_threads": True,
            },
        )
        self.assertTrue(gs.ai_boot_summary_enabled)
        self.assertEqual(gs.ai_boot_summary_min_interval_seconds, 600)
        self.assertEqual(gs.ai_boot_summary_max_refreshes_per_hour, 20)

        state.update_global_settings(
            ai_anthropic_api_key="sk-should-not-stick",
            api_key="sk-should-not-stick",
        )
        serialized = json.dumps(state.to_dict(), sort_keys=True)
        compact = json.dumps(state.to_dict_compact(), sort_keys=True)
        for secretish in (
            "api_key",
            "anthropic_api_key",
            "openai_api_key",
            "sk-should-not-stick",
        ):
            self.assertNotIn(secretish, serialized)
            self.assertNotIn(secretish, compact)

    def test_update_global_settings_normalizes_ai_fields(self):
        state = self.state_mod.MatrixState()

        state.update_global_settings(
            ai_enabled="yes",
            ai_generation_provider=" openai_compatible ",
            ai_anthropic_model="  claude-test  ",
            ai_openai_compatible_base_url=" http://localhost:11434/v1 ",
            ai_openai_compatible_model=" local-model ",
            ai_embedding_model=" custom/model ",
            ai_embedding_runtime=" sentence_transformers ",
            ai_index_corpus={"tasks": "false", "decisions": True, "bogus": True},
            ai_boot_summary_enabled="0",
            ai_boot_summary_min_interval_seconds="30",
            ai_boot_summary_max_refreshes_per_hour="5",
        )

        gs = state.global_settings
        self.assertIs(gs.ai_enabled, True)
        self.assertEqual(gs.ai_generation_provider, "openai_compatible")
        self.assertEqual(gs.ai_anthropic_model, "claude-test")
        self.assertEqual(
            gs.ai_openai_compatible_base_url,
            "http://localhost:11434/v1",
        )
        self.assertEqual(gs.ai_openai_compatible_model, "local-model")
        self.assertEqual(gs.ai_embedding_model, "custom/model")
        self.assertEqual(gs.ai_embedding_runtime, "sentence_transformers")
        self.assertEqual(
            gs.ai_index_corpus,
            {
                "architect_journals": True,
                "engineer_journals": True,
                "decisions": True,
                "tasks": False,
                "engineer_peer_threads": True,
            },
        )
        self.assertIs(gs.ai_boot_summary_enabled, False)
        self.assertEqual(gs.ai_boot_summary_min_interval_seconds, 30)
        self.assertEqual(gs.ai_boot_summary_max_refreshes_per_hour, 5)

        with self.assertRaises(ValueError):
            state.update_global_settings(ai_generation_provider="bad-provider")
        with self.assertRaises(ValueError):
            state.update_global_settings(ai_embedding_runtime="torch")

    def test_update_global_settings_normalizes_status_bar_visibility(self):
        state = self.state_mod.MatrixState()
        self.assertEqual(
            state.global_settings.status_bar_visibility,
            {
                "daemon_status": False,
                "claude_usage": False,
                "codex_usage": False,
                "deploy": True,
                "health": False,
                "workload": False,
                "tasks": True,
                "attention": True,
            },
        )

        state.update_global_settings(
            status_bar_visibility={
                "daemon_status": "true",
                "tasks": "false",
                "attention": True,
                "unknown": True,
            }
        )

        self.assertEqual(
            state.global_settings.status_bar_visibility,
            {
                "daemon_status": True,
                "claude_usage": False,
                "codex_usage": False,
                "deploy": True,
                "health": False,
                "workload": False,
                "tasks": False,
                "attention": True,
            },
        )

        state = self.state_mod.MatrixState()
        emitted = []
        state._emit = lambda event, **payload: emitted.append((event, payload))
        from dataclasses import asdict
        submitted = asdict(state.global_settings)
        submitted["status_bar_visibility"] = {
            "tasks": False,
            "attention": True,
        }
        state.update_global_settings(**submitted)

        self.assertEqual(emitted[-1][0], "global_settings_update")
        self.assertEqual(
            emitted[-1][1]["changed_keys"],
            ["status_bar_visibility"],
        )

    def test_update_global_settings_normalizes_perceived_empty_knobs(self):
        state = self.state_mod.MatrixState()

        state.update_global_settings(
            perceived_empty_probe_threshold="1",
            perceived_empty_window_seconds="99999",
        )

        self.assertEqual(state.global_settings.perceived_empty_probe_threshold, 2)
        self.assertEqual(state.global_settings.perceived_empty_window_seconds, 3600)

    def test_flag_perceived_empty_episode_sets_attention_without_stopping(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        cell = state.add_agent(
            name="Worker",
            group="g",
        )
        cell.status = "running"
        cell.kind = "worker"

        flagged = state.flag_perceived_empty_episode(
            cell.id,
            detail="Perceived-empty tool-result episode detected",
        )

        self.assertTrue(flagged)
        self.assertTrue(cell.needs_attention)
        self.assertEqual(cell.status, "running")
        self.assertEqual(cell.last_event_text,
                         "Perceived-empty tool-result episode detected")

    def test_relay_fields_default_off_and_serialize(self):
        state = self.state_mod.MatrixState()
        gs = state.global_settings
        self.assertFalse(gs.relay_enabled)
        self.assertEqual(gs.relay_url, "")
        self.assertEqual(gs.relay_private_key_path, "")
        # asdict (the persistence + delta payload) carries the relay keys.
        from dataclasses import asdict
        d = asdict(gs)
        for key in ("relay_enabled", "relay_url", "relay_daemon_id",
                    "relay_credential_id", "relay_private_key_path"):
            self.assertIn(key, d)

    def test_update_agent_persists_ordered_engineer_specializations(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        engineer = state.add_agent(name="Engineer", group="g")
        engineer.kind = "engineer"
        worker = state.add_agent(name="Worker", group="g")
        worker.kind = "worker"

        state.update_agent(
            engineer.id,
            engineer_specializations=[
                "ui-ux",
                "",
                "runtime-pty",
                "ui-ux",
            ],
        )
        state.update_agent(worker.id, engineer_specializations=["ui-ux"])

        self.assertEqual(
            engineer.engineer_specializations,
            ["ui-ux", "runtime-pty"],
        )
        self.assertEqual(worker.engineer_specializations, [])

    def test_set_relay_config_dedupes_and_emits(self):
        state = self.state_mod.MatrixState()
        payload = {
            "config": {"enabled": True, "relay_url": "wss://r/ws"},
            "sources": {"relay_url": {"value": "wss://r/ws",
                                      "source": "settings"}},
        }
        self.assertTrue(state.set_relay_config(payload))
        self.assertEqual(state.relay_config, payload)
        # Idempotent: an identical payload emits no further delta.
        self.assertFalse(state.set_relay_config(dict(payload)))

    def test_snapshot_includes_relay_config(self):
        state = self.state_mod.MatrixState()
        state.set_relay_config({
            "config": {"enabled": False},
            "sources": {"relay_url": {"value": "", "source": ""}},
        })
        snap = state.to_dict()
        self.assertIn("relay_config", snap)
        self.assertEqual(snap["relay_config"]["config"], {"enabled": False})
        # Deep-copied: mutating the snapshot must not corrupt live state.
        snap["relay_config"]["config"]["enabled"] = True
        self.assertFalse(state.relay_config["config"]["enabled"])

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

    def test_worktree_boundary_normalization_preserves_pr_metadata(self):
        boundary = self.state_mod._normalize_worktree_boundary({
            "repo_root": "/repo",
            "branch": "torque/worker",
            "status": "open",
            "commit_sha": "reviewed-head",
            "pr": {
                "provider": "github",
                "remote": "origin",
                "base_branch": "main",
                "head_branch": "torque/worker",
                "head_sha": "reviewed-head",
                "url": "https://github.com/acme/repo/pull/123",
                "number": "123",
                "state": "auto_merge_enabled",
                "merge_state": "BLOCKED",
                "created_at": "2026-04-07T10:30:00+00:00",
                "updated_at": "2026-04-07T10:30:00+00:00",
                "requested_cleanup": {
                    "close_agent_on_merge": True,
                    "remove_worktree_on_merge": False,
                    "auto_move_to_done": True,
                    "preserve_merge_diff": False,
                },
            },
        })

        self.assertEqual(boundary["commit_sha"], "reviewed-head")
        self.assertEqual(boundary["pr"]["number"], 123)
        self.assertEqual(boundary["pr"]["state"], "auto_merge_enabled")
        self.assertEqual(boundary["pr"]["merge_state"], "BLOCKED")
        self.assertEqual(
            boundary["pr"]["requested_cleanup"],
            {
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": False,
                "auto_move_to_done": True,
                "preserve_merge_diff": False,
            },
        )

    def test_load_seeds_architect_peer_message_caches_from_db(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        arch_a = self.state_mod.AgentCell(
            id="arch-a",
            name="Architect A",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        arch_b = self.state_mod.AgentCell(
            id="arch-b",
            name="Architect B",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        db.save_groups_and_members({"g": [arch_a.id, arch_b.id]}, {"g": "g"})
        db.save_agent(arch_a)
        db.save_agent(arch_b)
        db.save_agent_peer_message({
            "id": "msg-peer-1",
            "thread_id": "msg-peer-1",
            "group_name": "g",
            "sender_id": arch_a.id,
            "sender_kind": "architect",
            "recipient_id": arch_b.id,
            "recipient_kind": "architect",
            "message": "Can you sanity-check this?",
            "created_at": 123.0,
            "ack_required": True,
            "context_task_ids": ["TORQUE:1"],
            "context_snapshot": {"tasks": [{"id": "TORQUE:1"}]},
        })
        db.save_direct_message({
            "id": "direct-user-arch-a",
            "thread_id": "caller-thread-is-normalized",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": arch_a.id,
            "recipient_kind": "architect",
            "message": "direct user note",
            "created_at": 124.0,
            "delivery_state": "buffered",
        })
        db.save_direct_message({
            "id": "direct-ask-arch-a",
            "thread_id": "user-agent:user:arch-a",
            "group_name": "g",
            "sender_id": arch_a.id,
            "sender_kind": "architect",
            "recipient_id": arch_b.id,
            "recipient_kind": "architect",
            "message": "display-only owner ask",
            "message_type": "ask",
            "created_at": 125.0,
            "blocking": True,
            "source_task_id": "ask-task-1",
            "delivery_state": "delivered",
        })

        state = self.state_mod.MatrixState(db=db)
        state.load()

        sender_entry = state.agents[arch_a.id].mcp_messages[0]
        recipient_entry = state.agents[arch_b.id].mcp_messages[0]
        self.assertEqual(sender_entry["id"], "msg-peer-1")
        self.assertEqual(sender_entry["action"], "architect_peer_message")
        self.assertEqual(sender_entry["direction"], "sent")
        self.assertEqual(sender_entry["peer_id"], arch_b.id)
        self.assertEqual(recipient_entry["direction"], "received")
        self.assertEqual(recipient_entry["peer_id"], arch_a.id)
        self.assertTrue(recipient_entry["ack_required"])
        self.assertEqual(recipient_entry["context_task_ids"], ["TORQUE:1"])
        self.assertEqual(
            recipient_entry["context"]["snapshot"]["tasks"][0]["id"],
            "TORQUE:1",
        )
        self.assertEqual(
            [entry["id"] for entry in state.agents[arch_a.id].mcp_messages],
            ["msg-peer-1"],
        )
        self.assertEqual(
            [entry["id"] for entry in state.direct_messages_by_agent[arch_a.id]],
            ["direct-user-arch-a"],
        )
        self.assertEqual(
            state.direct_messages_by_agent[arch_a.id][0]["thread_id"],
            "user-agent:user:arch-a",
        )
        self.assertEqual(state.direct_messages_by_agent.get(arch_b.id), [])
        self.assertEqual(
            [entry["id"] for entry in state.agents[arch_b.id].mcp_messages],
            ["msg-peer-1"],
        )

    def test_load_seeds_engineer_peer_message_caches_and_threads_from_db(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        eng_a = self.state_mod.AgentCell(
            id="eng-a",
            name="Engineer A",
            group="g",
            kind="engineer",
            cell_type="agent",
            hired_by_architect_id="arch-1",
        )
        eng_b = self.state_mod.AgentCell(
            id="eng-b",
            name="Engineer B",
            group="g",
            kind="engineer",
            cell_type="agent",
            hired_by_architect_id="arch-1",
        )
        db.save_groups_and_members({"g": [eng_a.id, eng_b.id]}, {"g": "g"})
        db.save_agent(eng_a)
        db.save_agent(eng_b)
        db.save_agent_peer_message({
            "id": "msg-eng-1",
            "thread_id": "thread-eng",
            "group_name": "g",
            "sender_id": eng_a.id,
            "sender_kind": "engineer",
            "recipient_id": eng_b.id,
            "recipient_kind": "engineer",
            "message": "Please inspect TORQUE:801",
            "created_at": 123.0,
            "ack_required": True,
            "context_task_ids": ["TORQUE:801"],
            "context_snapshot": {"tasks": [{"id": "TORQUE:801"}]},
        })
        db.save_agent_peer_message({
            "id": "msg-eng-2",
            "thread_id": "thread-eng",
            "reply_to_id": "msg-eng-1",
            "group_name": "g",
            "sender_id": eng_b.id,
            "sender_kind": "engineer",
            "recipient_id": eng_a.id,
            "recipient_kind": "engineer",
            "message": "Looking now",
            "created_at": 124.0,
        })

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(
            [entry["action"] for entry in state.agents[eng_a.id].mcp_messages],
            ["engineer_peer_reply", "engineer_peer_notify"],
        )
        self.assertEqual(
            state.agents[eng_b.id].mcp_messages[-1]["action"],
            "engineer_peer_notify",
        )
        pair_key = f"agent-pair:{eng_a.id}:{eng_b.id}"
        self.assertIn(pair_key, state.agent_peer_threads)
        thread = state.agent_peer_threads[pair_key]
        self.assertEqual(
            [message["action"] for message in thread["messages"]],
            ["engineer_peer_notify", "engineer_peer_reply"],
        )

    def test_save_peer_message_updates_caches_and_delivery_deltas(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.agents["arch-a"] = self.state_mod.AgentCell(
            id="arch-a",
            name="Architect A",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        state.agents["arch-b"] = self.state_mod.AgentCell(
            id="arch-b",
            name="Architect B",
            group="g",
            kind="architect",
            cell_type="agent",
        )

        saved = state.save_peer_message({
            "id": "msg-peer-2",
            "thread_id": "msg-peer-2",
            "group_name": "g",
            "sender_id": "arch-a",
            "recipient_id": "arch-b",
            "message": "FYI",
            "created_at": 200.0,
        })

        self.assertEqual(saved["id"], "msg-peer-2")
        self.assertEqual(state.agents["arch-a"].mcp_messages[0]["direction"], "sent")
        self.assertEqual(
            state.agents["arch-b"].mcp_messages[0]["direction"],
            "received",
        )
        self.assertEqual(
            [op["op"] for op in state._delta_ops],
            ["agent_upsert", "agent_upsert", "agent_peer_thread_upsert"],
        )
        pair_key = "agent-pair:arch-a:arch-b"
        self.assertEqual(state._delta_ops[-1]["thread_id"], pair_key)
        thread = state.agent_peer_threads[pair_key]
        self.assertEqual(thread["thread_id"], pair_key)
        self.assertEqual(thread["title"], "Architect A ↔ Architect B")
        self.assertEqual(thread["participant_ids"], ["arch-a", "arch-b"])
        self.assertEqual(thread["message_count"], 1)
        self.assertEqual(thread["last_message_id"], "msg-peer-2")
        self.assertEqual(thread["messages"][0]["thread_id"], "msg-peer-2")
        self.assertFalse(thread["truncated"])

        updated = state.update_peer_message_delivery(
            "msg-peer-2",
            "delivered",
            delivered_at=250.0,
        )

        self.assertEqual(updated["delivery_state"], "delivered")
        recipient_entry = state.agents["arch-b"].mcp_messages[0]
        self.assertTrue(recipient_entry["delivered"])
        self.assertFalse(recipient_entry["buffered"])
        self.assertEqual(recipient_entry["delivered_at"], 250.0)
        sender_entry = state.agents["arch-a"].mcp_messages[0]
        self.assertTrue(sender_entry["delivered"])
        self.assertFalse(sender_entry["buffered"])
        self.assertEqual(sender_entry["delivered_at"], 250.0)
        self.assertEqual(
            state.agent_peer_threads[pair_key]["pending_delivery_count"],
            0,
        )
        self.assertEqual(state._delta_ops[-1]["thread_id"], pair_key)
        self.assertEqual(
            [op["op"] for op in state._delta_ops],
            [
                "agent_upsert",
                "agent_upsert",
                "agent_peer_thread_upsert",
                "agent_upsert",
                "agent_upsert",
                "agent_peer_thread_upsert",
            ],
        )

    def test_agent_peer_threads_merge_pair_threads_and_emit_pair_key_deltas(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.agents["arch-a"] = self.state_mod.AgentCell(
            id="arch-a",
            name="Torqly",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        state.agents["eng-a"] = self.state_mod.AgentCell(
            id="eng-a",
            name="Courier",
            group="g",
            kind="engineer",
            cell_type="agent",
        )

        state.save_peer_message({
            "id": "pair-msg-1",
            "thread_id": "thread-arch-to-eng",
            "group_name": "g",
            "sender_id": "arch-a",
            "sender_kind": "architect",
            "recipient_id": "eng-a",
            "recipient_kind": "engineer",
            "message": "Need review.",
            "created_at": 10.0,
            "ack_required": True,
            "delivery_state": "delivered",
        })
        state.save_peer_message({
            "id": "pair-msg-2",
            "thread_id": "thread-eng-to-arch",
            "reply_to_id": "pair-msg-1",
            "group_name": "g",
            "sender_id": "eng-a",
            "sender_kind": "engineer",
            "recipient_id": "arch-a",
            "recipient_kind": "architect",
            "message": "Acknowledged.",
            "created_at": 20.0,
            "ack_required": True,
            "delivery_state": "buffered",
        })

        pair_key = "agent-pair:arch-a:eng-a"
        self.assertEqual(list(state.agent_peer_threads), [pair_key])
        peer_ops = [
            op for op in state._delta_ops
            if op["op"] == "agent_peer_thread_upsert"
        ]
        self.assertEqual([op["thread_id"] for op in peer_ops], [pair_key, pair_key])
        self.assertTrue(all(op["thread"]["thread_id"] == pair_key for op in peer_ops))

        thread = state.agent_peer_threads[pair_key]
        self.assertEqual(thread["title"], "Courier ↔ Torqly")
        self.assertEqual(thread["participant_ids"], ["arch-a", "eng-a"])
        self.assertEqual(thread["last_activity_at"], 20.0)
        self.assertEqual(thread["last_message_id"], "pair-msg-2")
        self.assertEqual(thread["message_count"], 2)
        self.assertEqual(thread["ack_required_count"], 2)
        self.assertEqual(thread["pending_delivery_count"], 1)
        self.assertFalse(thread["truncated"])
        self.assertEqual(
            [message["id"] for message in thread["messages"]],
            ["pair-msg-1", "pair-msg-2"],
        )
        self.assertEqual(
            [message["thread_id"] for message in thread["messages"]],
            ["thread-arch-to-eng", "thread-eng-to-arch"],
        )
        self.assertEqual(thread["messages"][1]["reply_to_id"], "pair-msg-1")

    def test_load_seeds_direct_message_caches_from_db(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            group="g",
            kind="worker",
            cell_type="agent",
        )
        arch = self.state_mod.AgentCell(
            id="arch-a",
            name="Architect A",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        db.save_groups_and_members({"g": [worker.id, arch.id]}, {"g": "g"})
        db.save_agent(worker)
        db.save_agent(arch)
        db.save_direct_message({
            "id": "direct-1",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": worker.id,
            "recipient_kind": "worker",
            "message": "first",
            "created_at": 1.0,
        })
        db.save_direct_message({
            "id": "direct-2",
            "group_name": "g",
            "sender_id": worker.id,
            "sender_kind": "worker",
            "recipient_id": "user",
            "recipient_kind": "user",
            "message": "second",
            "created_at": 2.0,
            "delivery_state": "delivered",
            "read_at": 3.0,
        })
        db.save_agent_peer_message({
            "id": "peer-only",
            "thread_id": "peer-only",
            "group_name": "g",
            "sender_id": arch.id,
            "sender_kind": "architect",
            "recipient_id": worker.id,
            "recipient_kind": "worker",
            "message": "not a user direct message",
            "created_at": 4.0,
        })

        state = self.state_mod.MatrixState(db=db)
        state.load()

        cached = state.direct_messages_by_agent[worker.id]
        self.assertEqual([entry["id"] for entry in cached], ["direct-1", "direct-2"])
        self.assertEqual(cached[0]["direction"], "received")
        self.assertTrue(cached[0]["unread"])
        self.assertEqual(cached[1]["direction"], "sent")
        self.assertFalse(cached[1]["unread"])
        self.assertNotIn("peer-only", [entry["id"] for entry in cached])
        self.assertEqual(
            state.to_dict()["direct_messages_by_agent"][worker.id][0]["id"],
            "direct-1",
        )
        self.assertEqual(
            state.to_dict_compact()["direct_messages_by_agent"][worker.id][1]["id"],
            "direct-2",
        )

    def test_load_seeds_agent_peer_threads_snapshot_sort_and_tail_cap(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        arch_a = self.state_mod.AgentCell(
            id="arch-a",
            name="Architect A",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        eng_a = self.state_mod.AgentCell(
            id="eng-a",
            name="Engineer One",
            group="g",
            kind="engineer",
            cell_type="agent",
        )
        arch_b = self.state_mod.AgentCell(
            id="arch-b",
            name="Architect B",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            group="g",
            kind="worker",
            cell_type="agent",
        )
        db.save_groups_and_members(
            {"g": [arch_a.id, eng_a.id, arch_b.id, worker.id]},
            {"g": "g"},
        )
        for cell in (arch_a, eng_a, arch_b, worker):
            db.save_agent(cell)
        for idx in range(1, 106):
            sender = arch_a if idx % 2 else eng_a
            recipient = eng_a if idx % 2 else arch_a
            db.save_agent_peer_message({
                "id": f"long-{idx:03d}",
                "thread_id": "thread-long",
                "group_name": "g",
                "sender_id": sender.id,
                "sender_kind": sender.kind,
                "sender_name": "stale sender name",
                "recipient_id": recipient.id,
                "recipient_kind": recipient.kind,
                "recipient_name": "stale recipient name",
                "message": f"message {idx}",
                "created_at": float(idx),
                "ack_required": idx == 105,
                "delivery_state": "buffered" if idx >= 104 else "delivered",
                "delivered_at": 0 if idx >= 104 else float(idx),
            })
        for idx in range(106, 108):
            sender = arch_a if idx % 2 else eng_a
            recipient = eng_a if idx % 2 else arch_a
            db.save_agent_peer_message({
                "id": f"long-alt-{idx:03d}",
                "thread_id": "thread-long-alt",
                "group_name": "g",
                "sender_id": sender.id,
                "sender_kind": sender.kind,
                "sender_name": "stale sender name",
                "recipient_id": recipient.id,
                "recipient_kind": recipient.kind,
                "recipient_name": "stale recipient name",
                "message": f"alternate thread message {idx}",
                "created_at": float(idx),
                "delivery_state": "delivered",
                "delivered_at": float(idx),
            })
        db.save_agent_peer_message({
            "id": "new-thread-message",
            "thread_id": "thread-new",
            "group_name": "g",
            "sender_id": arch_b.id,
            "sender_kind": "architect",
            "recipient_id": arch_a.id,
            "recipient_kind": "architect",
            "message": "newest thread",
            "created_at": 200.0,
            "delivery_state": "delivered",
        })
        db.save_direct_message({
            "id": "direct-user-arch",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": arch_a.id,
            "recipient_kind": "architect",
            "message": "not chat",
            "created_at": 201.0,
        })
        db.save_agent_peer_message({
            "id": "worker-peer",
            "thread_id": "thread-worker",
            "group_name": "g",
            "sender_id": arch_a.id,
            "sender_kind": "architect",
            "recipient_id": worker.id,
            "recipient_kind": "worker",
            "message": "worker traffic excluded",
            "created_at": 202.0,
        })

        state = self.state_mod.MatrixState(db=db)
        state.load()
        snapshot = state.to_dict()["agent_peer_threads"]
        compact = state.to_dict_compact()["agent_peer_threads"]

        long_pair_key = "agent-pair:arch-a:eng-a"
        arch_pair_key = "agent-pair:arch-a:arch-b"
        self.assertEqual(list(snapshot), [arch_pair_key, long_pair_key])
        self.assertEqual(list(compact), [arch_pair_key, long_pair_key])
        long_thread = snapshot[long_pair_key]
        self.assertEqual(long_thread["thread_id"], long_pair_key)
        self.assertEqual(long_thread["group"], "g")
        self.assertEqual(long_thread["title"], "Architect A ↔ Engineer One")
        self.assertEqual(long_thread["participant_ids"], ["arch-a", "eng-a"])
        self.assertEqual(
            [participant["name"] for participant in long_thread["participants"]],
            ["Architect A", "Engineer One"],
        )
        self.assertEqual(long_thread["message_count"], 107)
        self.assertEqual(long_thread["ack_required_count"], 1)
        self.assertEqual(long_thread["pending_delivery_count"], 2)
        self.assertEqual(long_thread["requires_reply_participant_ids"], ["eng-a"])
        self.assertTrue(long_thread["truncated"])
        self.assertEqual(len(long_thread["messages"]), 100)
        self.assertEqual(long_thread["messages"][0]["id"], "long-008")
        self.assertEqual(long_thread["messages"][-1]["id"], "long-alt-107")
        self.assertEqual(long_thread["messages"][-1]["thread_id"], "thread-long-alt")
        self.assertEqual(long_thread["last_message_id"], "long-alt-107")
        self.assertEqual(long_thread["last_message"]["action"], "architect_message")
        self.assertEqual(
            snapshot[arch_pair_key]["title"],
            "Architect A ↔ Architect B",
        )
        self.assertEqual(snapshot[arch_pair_key]["thread_id"], arch_pair_key)
        self.assertEqual(snapshot[arch_pair_key]["messages"][0]["thread_id"], "thread-new")
        state._delta_ops.clear()
        self.assertEqual(state.seed_agent_peer_threads(emit=True), 2)
        self.assertEqual(
            [op["thread_id"] for op in state._delta_ops],
            [arch_pair_key, long_pair_key],
        )
        self.assertTrue(all(
            op["thread"]["thread_id"] == op["thread_id"]
            for op in state._delta_ops
        ))
        self.assertNotIn("agent-pair:arch-a:worker-1", snapshot)
        self.assertNotIn("direct-user-arch", snapshot)

    def test_seed_agent_peer_threads_drops_oldest_pairs_beyond_row_cap(self):
        # Regression/stress check: the Chat panel seed loads at most
        # AGENT_PEER_THREAD_SEED_ROW_LIMIT rows, newest-first.  With more chat
        # rows than the cap spread across many distinct pairs, only the pairs
        # whose newest activity falls inside the newest-cap window survive a
        # seed/reload; the oldest pairs are dropped entirely.  This guards
        # against a regression that drops the row clamp (loading the whole
        # table) or flips the newest-first ordering (loading stale pairs while
        # starving recent ones).
        from torque.db import TorqueDB

        cap = self.state_mod.AGENT_PEER_THREAD_SEED_ROW_LIMIT
        msgs_per_pair = 4
        pair_count = (cap // msgs_per_pair) + 300  # rows comfortably over cap
        expected_kept = cap // msgs_per_pair

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        arch = self.state_mod.AgentCell(
            id="arch-0",
            name="Architect 0",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        engineers = [
            self.state_mod.AgentCell(
                id=f"eng-{idx}",
                name=f"Engineer {idx}",
                group="g",
                kind="engineer",
                cell_type="agent",
            )
            for idx in range(pair_count)
        ]
        members = [arch.id] + [eng.id for eng in engineers]
        db.save_groups_and_members({"g": members}, {"g": "g"})
        db.save_agent(arch)
        for eng in engineers:
            db.save_agent(eng)

        # Strictly increasing timestamps so pair ordering by created_at is
        # deterministic: higher pair index == more recent activity.
        ts = 0.0
        total_rows = 0
        for pair_idx, eng in enumerate(engineers):
            for m in range(msgs_per_pair):
                ts += 1.0
                if m % 2 == 0:
                    sender, recipient = arch, eng
                else:
                    sender, recipient = eng, arch
                db.save_agent_peer_message({
                    "id": f"m-{pair_idx:06d}-{m}",
                    "thread_id": f"thread-{pair_idx}",
                    "group_name": "g",
                    "sender_id": sender.id,
                    "sender_kind": sender.kind,
                    "recipient_id": recipient.id,
                    "recipient_kind": recipient.kind,
                    "message": f"pair {pair_idx} msg {m}",
                    "created_at": ts,
                    "delivery_state": "delivered",
                    "delivered_at": ts,
                })
                total_rows += 1
        self.assertGreater(total_rows, cap)

        # The loader itself must honor the cap rather than streaming the table.
        loaded = db.load_recent_agent_peer_chat_messages(limit=cap)
        self.assertEqual(len(loaded), cap)

        state = self.state_mod.MatrixState(db=db)
        seeded = state.seed_agent_peer_threads(emit=False)
        self.assertEqual(seeded, expected_kept)

        snapshot = state.agent_peer_threads_snapshot()
        self.assertEqual(len(snapshot), expected_kept)
        # No partial pair leaks in: every kept pair retains all its rows since
        # the cap lands on an exact pair boundary here.
        self.assertEqual(
            sum(t["message_count"] for t in snapshot.values()),
            cap,
        )

        # Newest pairs are kept; oldest pairs are dropped.
        newest_pair = f"agent-pair:arch-0:eng-{pair_count - 1}"
        oldest_pair = "agent-pair:arch-0:eng-0"
        self.assertIn(newest_pair, snapshot)
        self.assertNotIn(oldest_pair, snapshot)
        # The boundary pair (oldest one still inside the window) is kept,
        # the next-older one is not.
        first_kept_idx = pair_count - expected_kept
        self.assertIn(f"agent-pair:arch-0:eng-{first_kept_idx}", snapshot)
        self.assertNotIn(f"agent-pair:arch-0:eng-{first_kept_idx - 1}", snapshot)

        # Ordering is newest-activity-first.
        keys = list(snapshot)
        self.assertEqual(keys[0], newest_pair)
        activities = [snapshot[k]["last_activity_at"] for k in keys]
        self.assertEqual(activities, sorted(activities, reverse=True))

        # A reload reproduces the same bounded aggregate (idempotent seed).
        reseeded = state.seed_agent_peer_threads(emit=False)
        self.assertEqual(reseeded, expected_kept)
        self.assertEqual(state.agent_peer_threads_snapshot(), snapshot)

    def test_seed_agent_peer_threads_caps_single_oversized_thread(self):
        # Regression/stress check: a single pair with more rows than the seed
        # cap loads only the newest-cap rows for that pair.  message_count
        # reflects the loaded (capped) rows, the thread is flagged truncated,
        # and the display tail stays bounded to AGENT_PEER_THREAD_MESSAGE_LIMIT
        # with the genuinely newest message last.
        from torque.db import TorqueDB

        cap = self.state_mod.AGENT_PEER_THREAD_SEED_ROW_LIMIT
        message_limit = self.state_mod.AGENT_PEER_THREAD_MESSAGE_LIMIT
        total_rows = cap + 1500

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        arch = self.state_mod.AgentCell(
            id="arch-0",
            name="Architect 0",
            group="g",
            kind="architect",
            cell_type="agent",
        )
        eng = self.state_mod.AgentCell(
            id="eng-0",
            name="Engineer 0",
            group="g",
            kind="engineer",
            cell_type="agent",
        )
        db.save_groups_and_members({"g": [arch.id, eng.id]}, {"g": "g"})
        db.save_agent(arch)
        db.save_agent(eng)

        for i in range(1, total_rows + 1):
            if i % 2:
                sender, recipient = arch, eng
            else:
                sender, recipient = eng, arch
            db.save_agent_peer_message({
                "id": f"m-{i:06d}",
                "thread_id": "thread-big",
                "group_name": "g",
                "sender_id": sender.id,
                "sender_kind": sender.kind,
                "recipient_id": recipient.id,
                "recipient_kind": recipient.kind,
                "message": f"msg {i}",
                "created_at": float(i),
                "delivery_state": "delivered",
                "delivered_at": float(i),
            })

        state = self.state_mod.MatrixState(db=db)
        self.assertEqual(state.seed_agent_peer_threads(emit=False), 1)

        snapshot = state.agent_peer_threads_snapshot()
        pair_key = "agent-pair:arch-0:eng-0"
        thread = snapshot[pair_key]
        # Only the newest-cap rows are loaded for the pair.
        self.assertEqual(thread["message_count"], cap)
        self.assertTrue(thread["truncated"])
        # Display tail stays bounded and ends on the genuinely newest message.
        self.assertEqual(len(thread["messages"]), message_limit)
        self.assertEqual(thread["messages"][-1]["id"], f"m-{total_rows:06d}")
        self.assertEqual(thread["last_message_id"], f"m-{total_rows:06d}")
        self.assertEqual(thread["last_activity_at"], float(total_rows))

    def test_save_direct_message_updates_cache_and_read_deltas(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.agents["worker-1"] = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            group="g",
            kind="worker",
            cell_type="agent",
        )

        saved = state.save_direct_message({
            "id": "direct-state-1",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": "worker-1",
            "recipient_kind": "worker",
            "message": "Ping",
            "created_at": 100.0,
        })

        self.assertEqual(saved["id"], "direct-state-1")
        cached = state.direct_messages_by_agent["worker-1"][0]
        self.assertEqual(cached["direction"], "received")
        self.assertTrue(cached["unread"])
        self.assertEqual(
            [op["op"] for op in state._delta_ops],
            ["direct_message_upsert"],
        )

        updated = state.mark_direct_message_read(
            "direct-state-1",
            read_at=150.0,
            reader_id="worker-1",
        )

        self.assertEqual(updated["read_at"], 150.0)
        self.assertEqual(updated["delivery_state"], "buffered")
        cached = state.direct_messages_by_agent["worker-1"][0]
        self.assertEqual(cached["read_at"], 150.0)
        self.assertFalse(cached["unread"])
        self.assertEqual(
            [op["op"] for op in state._delta_ops],
            ["direct_message_upsert", "direct_message_read"],
        )

    def test_sync_ui_selection_to_session_selects_parent_agent_and_group(self):
        state = self.state_mod.MatrixState()
        state.groups = {"alpha": ["agent-1"], "beta": ["agent-2"]}
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Agent One",
            group="alpha",
            cell_type="agent",
        )
        state.agents["term-1"] = self.state_mod.AgentCell(
            id="term-1",
            name="Shell",
            group="alpha",
            cell_type="terminal",
            parent_id="agent-1",
            session_id="sess-term-1",
        )
        state.agents["agent-2"] = self.state_mod.AgentCell(
            id="agent-2",
            name="Agent Two",
            group="beta",
            cell_type="agent",
            session_id="sess-agent-2",
        )
        state._children = {"agent-1": ["term-1"], "agent-2": []}
        state.selected_agent_id = "agent-2"
        state.active_group = "beta"
        state._delta_ops.clear()

        selected = state.sync_ui_selection_to_session(
            "sess-term-1",
            persist=False,
        )

        self.assertEqual(selected, "agent-1")
        self.assertEqual(state.selected_agent_id, "agent-1")
        self.assertEqual(state.active_group, "alpha")
        self.assertEqual(
            state._delta_ops,
            [{
                "op": "ui_update",
                "key": "selected_agent_id",
                "value": "agent-1",
            }, {
                "op": "ui_update",
                "key": "active_group",
                "value": "alpha",
            }],
        )

    def test_compact_agent_projection_bounds_heavy_browser_fields(self):
        state = self.state_mod.MatrixState()
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            effective_agent_class_snapshot={
                "id": "qa-worker",
                "version": "7",
                "base_kind": "worker",
                "primary_identity_label": "QA Worker",
                "warnings": ["Check configuration"],
                "prompt": {"job": "x" * 20_000},
                "effective_authority": {"capabilities": {"secret": True}},
                "metadata": {
                    "archived": False,
                    "private_notes": "x" * 10_000,
                },
            },
            mcp_messages=[
                {"action": "progress", "message": str(index), "timestamp": index}
                for index in range(30)
            ],
            worktree_changed_files=[f"file-{index}.py" for index in range(150)],
        )
        state.agents[agent.id] = agent
        state.groups["g"] = [agent.id]

        full_agent = state.to_dict()["agents"][agent.id]
        compact_agent = state.to_dict_compact()["agents"][agent.id]
        state._delta_ops.clear()
        state._emit_agent(agent)
        delta_agent = state._delta_ops[-1]

        self.assertIn("prompt", full_agent["effective_agent_class_snapshot"])
        for projected in (compact_agent, delta_agent):
            class_snapshot = projected["effective_agent_class_snapshot"]
            self.assertEqual(class_snapshot["primary_identity_label"], "QA Worker")
            self.assertEqual(class_snapshot["warnings"], ["Check configuration"])
            self.assertNotIn("prompt", class_snapshot)
            self.assertNotIn("effective_authority", class_snapshot)
            self.assertEqual(class_snapshot["metadata"], {"archived": False})
            self.assertEqual(len(projected["mcp_messages"]), 20)
            self.assertEqual(projected["mcp_message_count"], 30)
            self.assertEqual(len(projected["worktree_changed_files"]), 100)
            self.assertEqual(projected["worktree_changed_files_count"], 150)

        self.assertLess(
            len(self.state_mod.hot_json_dumps_bytes(compact_agent)),
            len(self.state_mod.hot_json_dumps_bytes(full_agent)) // 3,
        )

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
            dispatch_state="live",
            depends_on=["task-0"],
            provider="github",
            external_id="123",
            external_url="https://example.test/tasks/123",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "sync_state": "queued",
                "last_error": "retry later",
                "last_synced_hash": "heavy-hash",
                "github": {"issue_number": 123, "project_item_id": "heavy"},
            },
            health_state="attention",
            health_since="2026-04-22T12:00:00+00:00",
            health_details={"reason": "large"},
            verification_mode="deploy",
            verification_state="pending",
            verification_notes="needs smoke",
            verification_summary={"tests_run": "targeted"},
            completion_evidence={
                "status": "evidence_attached",
                "sources": ["verification"],
            },
            lane_entered_at="2026-04-22T00:00:00+00:00",
            worktree_boundary={
                "repo_root": "/tmp/repo",
                "branch": "feature/x",
                "status": "open",
                "base": "main",
                "diff_stats": {"files": ["heavy"] * 50},
                "pr": {
                    "url": "https://example.test/pr/5",
                    "number": 5,
                    "state": "open",
                    "head_sha": "abc123",
                    "body": "heavy",
                },
            },
            resume_after_boundary_task_id="task-boundary",
            description="long description",
            context="legacy context",
            criteria="legacy criteria",
            instructions="legacy instructions",
            messages=[{"action": "progress", "message": "full progress body"}],
            messages_thread=[{
                "timestamp": 123,
                "sender_agent_id": "eng-1",
                "recipient_agent_id": "agent-1",
                "content": "full inline message body",
                "reply_required": True,
            }],
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
        self.assertEqual(task["dispatch_state"], "live")
        self.assertEqual(task["depends_on"], ["task-0"])
        self.assertEqual(task["provider"], "github")
        self.assertEqual(task["external_id"], "123")
        self.assertEqual(
            task["external_url"], "https://example.test/tasks/123")
        self.assertEqual(task["board_sync"], {
            "version": 1,
            "provider": "github",
            "enabled": True,
            "sync_state": "queued",
            "last_error": "retry later",
        })
        self.assertEqual(task["health_since"], "2026-04-22T12:00:00+00:00")
        self.assertEqual(task["health_details"], {"reason": "large"})
        self.assertEqual(
            task["messages"],
            [{"count": 1, "action": "progress", "message": "progress"}],
        )
        self.assertEqual(task["messages_thread_summary"], {
            "count": 1,
            "recipient_agent_ids": ["agent-1"],
            "sender_agent_ids": ["eng-1"],
            "reply_required": True,
            "last_timestamp": 123.0,
        })
        self.assertEqual(task["worktree_boundary"], {
            "repo_root": "/tmp/repo",
            "branch": "feature/x",
            "status": "open",
            "pr": {
                "url": "https://example.test/pr/5",
                "number": 5,
                "state": "open",
                "head_sha": "abc123",
            },
        })
        self.assertEqual(
            task["resume_after_boundary_task_id"], "task-boundary")
        self.assertNotIn("description", task)
        self.assertNotIn("context", task)
        self.assertNotIn("criteria", task)
        self.assertNotIn("instructions", task)
        self.assertNotIn("attachments", task)
        self.assertNotIn("artifacts", task)
        self.assertNotIn("action_vars", task)
        self.assertNotIn("messages_thread", task)
        self.assertNotIn("verification_notes", task)
        self.assertNotIn("verification_summary", task)
        self.assertNotIn("completion_evidence", task)
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

        # TORQUE:154 phase-1 measured ~98% reduction. Preserve at least 95% of
        # that win after eagerly restoring board-semantic metadata.
        self.assertGreaterEqual(reduction, 0.931)

    def test_task_dispatch_state_persists_and_emits_delta(self):
        db_mod = importlib.import_module("torque.db")
        db_mod = importlib.reload(db_mod)
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            try:
                state = self.state_mod.MatrixState(db=db)
                state.add_group("g")
                state._delta_ops.clear()

                task = state.board_add_task("Queued task", "g")
                self.assertEqual(task.dispatch_state, "queued")
                self.assertEqual(state._delta_ops[-1]["op"], "task_upsert")
                self.assertEqual(state._delta_ops[-1]["dispatch_state"], "queued")

                state._delta_ops.clear()
                state.board_update_task(task.id, dispatch_state="live")

                self.assertEqual(
                    state.board_tasks[task.id].dispatch_state,
                    "live",
                )
                self.assertEqual(state._delta_ops[-1]["op"], "task_upsert")
                self.assertEqual(state._delta_ops[-1]["dispatch_state"], "live")
                self.assertEqual(
                    db.load_all()["board_tasks"][task.id]["dispatch_state"],
                    "live",
                )
            finally:
                db.close()

    def test_dispatch_state_live_from_backlog_auto_advances_to_active_lane(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        task = state.board_add_task("Stage work", "g", lane="Backlog")
        self.assertIsNotNone(task)
        state._delta_ops.clear()

        state.board_update_task(task.id, dispatch_state="live")

        updated = state.board_tasks[task.id]
        self.assertEqual(updated.dispatch_state, "live")
        self.assertEqual(updated.lane, "To Do")
        self.assertEqual(state._delta_ops[-1]["op"], "task_upsert")
        self.assertEqual(state._delta_ops[-1]["dispatch_state"], "live")
        self.assertEqual(state._delta_ops[-1]["lane"], "To Do")

    def test_board_add_task_live_from_backlog_defaults_to_active_lane(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")

        task = state.board_add_task(
            "Create already live",
            "g",
            dispatch_state="live",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.dispatch_state, "live")
        self.assertEqual(task.lane, "To Do")
        self.assertEqual(state._delta_ops[-1]["lane"], "To Do")

    def test_dispatch_state_live_backlog_auto_advance_guardrails(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")

        queued = state.board_add_task("Queued backlog", "g", lane="Backlog")
        self.assertIsNotNone(queued)
        state.board_update_task(queued.id, description="Still queued")
        self.assertEqual(state.board_tasks[queued.id].dispatch_state, "queued")
        self.assertEqual(state.board_tasks[queued.id].lane, "Backlog")

        done = state.board_add_task("Done task", "g", lane="Done")
        self.assertIsNotNone(done)
        state.board_update_task(done.id, dispatch_state="live")
        self.assertEqual(state.board_tasks[done.id].dispatch_state, "live")
        self.assertEqual(state.board_tasks[done.id].lane, "Done")

        archived = state.board_add_task("Archived task", "g", lane="To Do")
        self.assertIsNotNone(archived)
        state.board_archive_task(archived.id)
        state.board_update_task(archived.id, dispatch_state="live")
        self.assertEqual(state.board_tasks[archived.id].dispatch_state, "live")
        self.assertEqual(state.board_tasks[archived.id].lane, "Archived")

        manual = state.board_add_task("Manual live backlog", "g", lane="Backlog")
        self.assertIsNotNone(manual)
        state.board_update_task(manual.id, dispatch_state="live")
        self.assertEqual(state.board_tasks[manual.id].lane, "To Do")
        state.board_move_task(manual.id, "Backlog")
        state.board_update_task(
            manual.id,
            dispatch_state="live",
            description="Operator intentionally moved this live task back.",
        )
        self.assertEqual(state.board_tasks[manual.id].dispatch_state, "live")
        self.assertEqual(state.board_tasks[manual.id].lane, "Backlog")

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
            "path": Path("/tmp/torque"),
            "when": datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        })

        self.assertEqual(json.loads(raw), {
            "path": "/tmp/torque",
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
            labels=["torque:human", "torque:derived"],
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

        self.assertEqual(state.group_settings["g"].engineer_agent_id, agent.id)
        self.assertTrue(state.agent_is_tombstoned(agent))
        self.assertGreater(agent.permanent_delete_after, agent.deleted_at)
        self.assertIsNone(state.get_engineer_for_group("g"))
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

    def test_remove_agent_tombstones_restores_and_purges(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        agent = state.add_agent(name="Worker", group="g")
        child = state.add_terminal(name="Shell", group="g", parent_id=agent.id)
        agent.session_id = "session-agent"
        child.session_id = "session-child"
        agent.current_task_id = "task-1"
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Do work",
            group="g",
            lane="In Progress",
            agent_id=agent.id,
        )
        state.board_tasks[task.id] = task

        tombstoned = state.remove_agent(agent.id)

        self.assertEqual([c.id for c in tombstoned], [agent.id, child.id])
        self.assertIn(agent.id, state.agents)
        self.assertIn(agent.id, state.groups["g"])
        self.assertTrue(state.agent_is_tombstoned(agent))
        self.assertTrue(state.agent_is_tombstoned(child))
        self.assertEqual(agent.session_id, None)
        self.assertEqual(child.session_id, None)
        self.assertEqual(agent.current_task_id, "")
        self.assertEqual(task.agent_id, "")

        restored = state.restore_agent(agent.id)
        self.assertEqual([c.id for c in restored], [agent.id, child.id])
        self.assertFalse(state.agent_is_tombstoned(agent))
        self.assertFalse(state.agent_is_tombstoned(child))

        state.remove_agent(agent.id)
        agent.permanent_delete_after = 1
        child.permanent_delete_after = 1
        purged = state.purge_tombstoned_agents(now=2)
        self.assertEqual([c.id for c in purged], [agent.id, child.id])
        self.assertNotIn(agent.id, state.agents)
        self.assertNotIn(child.id, state.agents)
        self.assertNotIn(agent.id, state.groups["g"])

    def test_remove_agent_tombstone_marks_history_removed_once(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        agent = state.add_agent(name="Worker", group="g")
        agent.session_tokens_in = 7
        agent.session_tokens_out = 11
        state.history_record_agent(agent)

        state.remove_agent(agent.id)

        rec = db.load_agent_history_detail(agent.id)
        self.assertEqual(rec["status"], "removed")
        self.assertTrue(rec["removed_at"])
        self.assertEqual(rec["total_tokens_in"], 7)
        self.assertEqual(rec["total_tokens_out"], 11)

        agent.permanent_delete_after = 1
        state.purge_tombstoned_agents(now=2)

        rec = db.load_agent_history_detail(agent.id)
        self.assertEqual(rec["status"], "removed")
        self.assertEqual(rec["total_tokens_in"], 7)
        self.assertEqual(rec["total_tokens_out"], 11)

    def test_remove_agent_tombstone_preserves_merged_history_status(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        agent = state.add_agent(name="Merged Worker", group="g")
        state.history_record_agent(agent)
        db.update_agent_history(agent.id, status="merged")

        state.remove_agent(agent.id)

        rec = db.load_agent_history_detail(agent.id)
        self.assertEqual(rec["status"], "merged")
        self.assertTrue(rec["removed_at"])

    def test_load_reconciles_tombstoned_agent_history_status(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            deleted_at=123,
            permanent_delete_after=456,
        )
        db.save_agent(agent)
        db.save_groups_and_members({"g": [agent.id]}, {"g": "g"})
        db.save_agent_history({
            "id": agent.id,
            "name": agent.name,
            "group": agent.group,
            "created_at": 1,
            "status": "active",
        })

        state = self.state_mod.MatrixState(db=db)
        state.load()

        rec = db.load_agent_history_detail(agent.id)
        self.assertEqual(rec["status"], "removed")
        self.assertTrue(rec["removed_at"])

    def test_remove_group_marks_agent_history_removed(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        agent = state.add_agent(name="Worker", group="g")
        state.history_record_agent(agent)

        state.remove_group("g")

        rec = db.load_agent_history_detail(agent.id)
        self.assertEqual(rec["status"], "removed")
        self.assertTrue(rec["removed_at"])

    def test_remove_direct_terminal_hard_deletes_without_tombstone(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        terminal = state.add_terminal(name="Shell", group="g")
        terminal.session_id = "session-terminal"
        state._delta_ops.clear()

        removed = state.remove_agent(terminal.id)

        self.assertEqual([c.id for c in removed], [terminal.id])
        self.assertNotIn(terminal.id, state.agents)
        self.assertNotIn(terminal.id, state.groups["g"])
        self.assertEqual(
            state._delta_ops,
            [{
                "op": "agent_remove",
                "id": terminal.id,
                "group": "g",
                "cell_type": "terminal",
            }, {
                "op": "group_update",
                "name": "g",
                "slug": "",
                "agents": [],
            }],
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
            labels=["torque:human", "torque:derived"],
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
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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

    def test_cleanup_orphaned_attention_false_fallback_keeps_actor_scoped_question(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
            labels=["torque:human", "torque:derived"],
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
        self.assertEqual(cleaned, {"asks": 1, "engineer_questions": 0})
        ws = state.engineer_settings["g"]
        self.assertEqual(ws.pending_question, "Need approval")
        self.assertEqual(ws.pending_question_actor_id, engineer.id)
        self.assertEqual(ws.pending_note, "")
        self.assertEqual(ws.pending_note_kind, "")
        self.assertTrue(ws.paused)
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[ask.id].lane, "Done")

    def test_cleanup_orphaned_attention_keeps_reply_agent_ask_when_source_unavailable(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id=worker.id,
            status="Awaiting Input",
        )
        ask = self.state_mod.BoardTask(
            id="ask-1",
            task="Review plan",
            group="g",
            lane="Backlog",
            labels=["torque:human", "torque:derived"],
            parent_task_id=parent.id,
            reply_agent_id=worker.id,
        )
        db.save_group("g", 0)
        db.save_agent(worker)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        state.board_tasks[parent.id] = parent
        state.board_tasks[ask.id] = ask

        cleaned = state.cleanup_orphaned_attention(
            emit=False,
            allow_persisted_agent_fallback=False,
        )

        self.assertEqual(cleaned, {"asks": 0, "engineer_questions": 0})
        self.assertEqual(state.board_tasks[parent.id].status, "Awaiting Input")
        self.assertEqual(state.board_tasks[ask.id].lane, "Backlog")

    def test_load_preserves_pending_question_for_persisted_engineer(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                "branch": "torque/worker",
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
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                    "branch": "torque/worker",
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
                    "branch": "torque/worker",
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
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Old archived task",
                group="g",
                lane="Done",
                labels=["torque:archived", "bug"],
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
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="dep-1",
                task="Legacy archived child",
                group="g",
                lane="In Progress",
                labels=["torque:archived"],
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

    def test_auto_dispatch_queue_raise_max_concurrent_only_raises(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.board_add_task("Queued", "g", id="task-queued")
        entry = state.auto_dispatch_queue_add(
            "g", "task-queued", max_concurrent=1
        )

        raised_entry, changed = state.auto_dispatch_queue_raise_max_concurrent(
            "g", "task-queued", 3
        )

        self.assertTrue(changed)
        self.assertIs(raised_entry, entry)
        self.assertEqual(entry.max_concurrent, 3)
        lower_entry, changed = state.auto_dispatch_queue_raise_max_concurrent(
            "g", "task-queued", 2
        )
        self.assertFalse(changed)
        self.assertIs(lower_entry, entry)
        self.assertEqual(entry.max_concurrent, 3)
        missing_entry, changed = state.auto_dispatch_queue_raise_max_concurrent(
            "other", "task-queued", 4
        )
        self.assertFalse(changed)
        self.assertIsNone(missing_entry)

    def test_load_restores_auto_dispatch_queue_and_busy_agents(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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

    def test_load_preserves_custom_architect_digest_filter_without_floor(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
        self.assertEqual(settings.enabled_events, ["task_completed"])
        persisted = db.load_agent_digest_settings("arch-1")
        self.assertEqual(persisted["enabled_events"], ["task_completed"])

    def test_architect_settings_round_trip_through_group_settings(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                architect_directory="/repo/.torque/architect",
                architect_profile="Ops",
                architect_shell="fish",
                architect_tab_color="none",
                architect_custom_instructions="Own scope.",
                architect_autonomy_mode="ask_always",
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
        self.assertEqual(settings.architect_directory, "/repo/.torque/architect")
        self.assertEqual(settings.architect_profile, "Ops")
        self.assertEqual(settings.architect_shell, "fish")
        self.assertEqual(settings.architect_tab_color, "none")
        self.assertEqual(settings.architect_custom_instructions, "Own scope.")
        self.assertEqual(settings.architect_autonomy_mode, "ask_always")
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
        """Architect settings default to quiet, empty-window digest suppression."""
        settings = self.state_mod.ArchitectSettings(group="g")
        self.assertEqual(settings.architect_push_interval, 300)
        self.assertEqual(settings.architect_max_interval, 600)
        self.assertEqual(settings.architect_heartbeat_interval, 0)
        self.assertTrue(settings.architect_suppress_empty_digests)
        self.assertEqual(settings.architect_enabled_events, [])

    def test_architect_digest_knobs_round_trip_through_group_settings(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                architect_enabled_events=["task_done", "pipeline_complete"],
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
            ["task_done", "pipeline_complete"],
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

    def test_default_agent_digest_settings_empty_events_stays_quiet(self):
        """Empty architect_enabled_events means only the server floor is active."""
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
        self.assertEqual(defaults.enabled_events, [])

    def test_load_backfills_suppress_empty_for_legacy_architect_rows(self):
        """Pre-existing architect digest rows should pick up suppress_empty."""
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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

    def test_load_backfills_legacy_broad_architect_filters_to_quiet(self):
        """Pre-existing broad default architect rows should become quiet."""
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                "enabled_events": list(
                    self.state_mod._ARCHITECT_DIGEST_LEGACY_DEFAULT_ENABLED_EVENTS
                ),
            },
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        settings = state.get_agent_digest_settings("arch-1")
        self.assertEqual(settings.enabled_events, [])
        self.assertEqual(
            db.load_ui_state_value("architect_digest_quiet_default_backfilled"),
            "1",
        )
        self.assertEqual(
            db.load_all_agent_digest_settings()["arch-1"]["enabled_events"],
            [],
        )

    def test_load_backfills_legacy_broad_group_architect_filters_to_quiet(self):
        """Legacy group-level architect defaults should not keep future architects noisy."""
        from torque.db import TorqueDB

        routing_mod = importlib.import_module("torque.digest_routing")
        routing_mod = importlib.reload(routing_mod)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["arch-1", "eng-1", "worker-1"]}, {"g": "g"})
        db.save_group_members("g", ["arch-1", "eng-1", "worker-1"])
        db.save_group_settings(
            "g",
            self.state_mod.GroupSettings(
                architect_enabled_events=list(
                    self.state_mod._ARCHITECT_DIGEST_LEGACY_DEFAULT_ENABLED_EVENTS
                ),
            ),
        )
        # Simulate a partial run of the earlier per-agent-only backfill. The
        # group-level backfill must still run so legacy group rows get quieted.
        db.save_ui_state("architect_digest_quiet_default_backfilled", "1")
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
        db.save_agent(
            self.state_mod.AgentCell(
                id="eng-1",
                name="Engineer",
                group="g",
                cell_type="agent",
                kind="engineer",
                hired_by_architect_id="arch-1",
                persistent=True,
            )
        )
        db.save_agent(
            self.state_mod.AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                cell_type="agent",
                kind="worker",
                owner_engineer_id="eng-1",
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(
            state.get_architect_settings("g").architect_enabled_events,
            [],
        )
        self.assertEqual(
            db.load_all()["group_settings"]["g"]["architect_enabled_events"],
            [],
        )
        self.assertEqual(
            db.load_ui_state_value(
                "architect_digest_group_quiet_default_backfilled"
            ),
            "1",
        )
        recipients = routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": "worker-1", "group": "g", "kind": "task_completed"},
        )
        self.assertEqual(recipients, ["eng-1"])

    def test_load_preserves_custom_group_architect_filter(self):
        """Custom group-level architect opt-ins are not mistaken for legacy defaults."""
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_group_settings(
            "g",
            self.state_mod.GroupSettings(
                architect_enabled_events=["task_done", "pipeline_complete"],
            ),
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(
            state.get_architect_settings("g").architect_enabled_events,
            ["task_done", "pipeline_complete"],
        )
        self.assertEqual(
            db.load_all()["group_settings"]["g"]["architect_enabled_events"],
            ["task_done", "pipeline_complete"],
        )

    def test_backfill_runs_only_once_respects_user_override(self):
        """Once the marker is set, a user-set False is preserved across reloads."""
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                "branch": "torque/worker",
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
            engineer_merge_mode="direct",
            guidance_hint_cadence=150,
            engineer_hint_snoozes={
                "merged_retained_by_policy:a,b": time.time() + 3600,
                "expired": time.time() - 1,
                "bad": "not-a-time",
            },
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
        self.assertEqual(gs.engineer_merge_mode, "direct")
        self.assertEqual(gs.guidance_hint_cadence, 100)
        self.assertEqual(
            set(gs.engineer_hint_snoozes),
            {"merged_retained_by_policy:a,b"},
        )

        state.update_group_settings("g", engineer_merge_mode="not-real")
        self.assertEqual(state.group_settings["g"].engineer_merge_mode, "pr")
        state.update_group_settings("g", guidance_hint_cadence="not-an-int")
        self.assertEqual(state.group_settings["g"].guidance_hint_cadence, 4)

        state.update_group_settings("g", worktree_merge_cleanup="auto_sweep")
        self.assertEqual(gs.worktree_merge_cleanup, "auto_sweep")
        self.assertEqual(
            self.state_mod.merge_cleanup_flags(gs.worktree_merge_cleanup),
            (True, True),
        )

    def test_group_settings_engineer_merge_mode_defaults_and_snapshots(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []

        self.assertEqual(
            self.state_mod.GroupSettings().engineer_merge_mode,
            "pr",
        )
        self.assertEqual(
            self.state_mod.GroupSettings().guidance_hint_cadence,
            4,
        )
        self.assertEqual(self.state_mod.GroupSettings().worker_provider, "")
        self.assertEqual(self.state_mod.GroupSettings().worker_model, "")
        state.update_group_settings(
            "g",
            engineer_merge_mode="engineer-choice",
            guidance_hint_cadence="0",
            worker_provider=" codex ",
            worker_model=" gpt-5.4 ",
            worker_reasoning_effort=" high ",
            worker_boot_command=" codex --worker ",
        )

        self.assertEqual(
            state.to_dict()["group_settings"]["g"]["engineer_merge_mode"],
            "engineer-choice",
        )
        self.assertEqual(
            state.to_dict()["group_settings"]["g"]["guidance_hint_cadence"],
            0,
        )
        self.assertEqual(
            state.to_dict()["group_settings"]["g"]["worker_provider"],
            "codex",
        )
        self.assertEqual(
            state.to_dict()["group_settings"]["g"]["worker_model"],
            "gpt-5.4",
        )
        self.assertEqual(
            state.to_dict()["group_settings"]["g"]["worker_reasoning_effort"],
            "high",
        )
        self.assertEqual(
            state.to_dict()["group_settings"]["g"]["worker_boot_command"],
            "codex --worker",
        )

    def test_guidance_hint_cadence_sequence_and_session_reset(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            guidance_hint_cadence=4,
        )
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            session_id="session-1",
        )

        self.assertEqual(
            [
                state.should_show_guidance_hint("soft.hint", worker)
                for _ in range(5)
            ],
            [True, False, False, True, False],
        )

        worker.session_id = "session-2"
        self.assertTrue(state.should_show_guidance_hint("soft.hint", worker))

        state.group_settings["g"].guidance_hint_cadence = 0
        worker.session_id = "session-3"
        self.assertEqual(
            [
                state.should_show_guidance_hint("soft.hint", worker)
                for _ in range(3)
            ],
            [True, True, True],
        )

    def test_history_record_dispatch_persists_engineer_worklog_and_survives_reload(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
            id="TORQUE:1",
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


class MatrixStateTaskBacklinkHydrationTests(unittest.TestCase):
    """Cold-load regression coverage for the derived agent→task backlink."""

    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _new_persisted_state(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        worker = self.state_mod.AgentCell(
            id="worker-synthetic",
            name="Synthetic Worker",
            group="g",
            kind="worker",
            cell_type="agent",
            current_task_id="stale-ephemeral-value",
        )
        state.agents[worker.id] = worker
        state.groups["g"] = [worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress",
        )
        state._db_save_groups()
        state._db_save_group_settings("g")
        state._db_save_agent(worker)
        return db, state, worker

    def test_cold_load_rehydrates_live_assigned_task_backlink(self):
        db, state, worker = self._new_persisted_state()
        live = state.board_add_task(
            "Synthetic live dispatch",
            "g",
            lane="In Progress",
            id="TASK-LIVE",
            action_name="feature/implement",
            agent_id=worker.id,
        )
        self.assertIsNotNone(live)

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        restored = reloaded.agents[worker.id]
        self.assertEqual(restored.status, "stopped")
        self.assertEqual(restored.current_task_id, live.id)

    def test_cold_load_selects_latest_live_assignment_with_id_tiebreak(self):
        db, state, worker = self._new_persisted_state()
        older = state.board_add_task(
            "Synthetic earlier live dispatch",
            "g",
            lane="In Progress",
            id="TASK-LIVE-EARLIER",
            agent_id=worker.id,
            dispatch_state="live",
        )
        later = state.board_add_task(
            "Synthetic later live dispatch",
            "g",
            lane="In Progress",
            id="TASK-LIVE-LATER",
            agent_id=worker.id,
            dispatch_state="live",
        )
        tied_lower = state.board_add_task(
            "Synthetic tied live dispatch A",
            "g",
            lane="In Progress",
            id="TASK-LIVE-TIE-A",
            agent_id=worker.id,
            dispatch_state="live",
        )
        tied_higher = state.board_add_task(
            "Synthetic tied live dispatch B",
            "g",
            lane="In Progress",
            id="TASK-LIVE-TIE-B",
            agent_id=worker.id,
            dispatch_state="live",
        )
        older.created_at = older.updated_at = "2026-01-01T00:00:00+00:00"
        later.created_at = later.updated_at = "2026-01-02T00:00:00+00:00"
        tied_lower.created_at = tied_lower.updated_at = "2026-01-03T00:00:00+00:00"
        tied_higher.created_at = tied_higher.updated_at = "2026-01-03T00:00:00+00:00"
        state._db_save_task(older)
        state._db_save_task(later)
        state._db_save_task(tied_lower)
        state._db_save_task(tied_higher)

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        # Later durable mutation wins; equal timestamps use the stable ID.
        self.assertEqual(
            reloaded.agents[worker.id].current_task_id,
            tied_higher.id,
        )

    def test_cold_load_does_not_restore_terminal_or_workerless_backlinks(self):
        db, state, worker = self._new_persisted_state()
        terminal = state.board_add_task(
            "Synthetic completed dispatch",
            "g",
            lane="Done",
            id="TASK-DONE",
            agent_id=worker.id,
        )
        child = state.board_add_task(
            "Synthetic workerless derived child",
            "g",
            lane="In Progress",
            id="TASK-CHILD",
            parent_task_id=terminal.id,
            pipeline_root_id=terminal.id,
            pipeline_depth=1,
            action_name="feature/review",
        )
        self.assertIsNotNone(terminal)
        self.assertIsNotNone(child)
        self.assertEqual(child.agent_id, "")

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        self.assertEqual(reloaded.agents[worker.id].current_task_id, "")
        self.assertEqual(reloaded.board_tasks[child.id].agent_id, "")



class MatrixStateDurableSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    async def test_durable_global_settings_failure_preserves_memory(self):
        attempted = []

        class BadDB:
            async def save_global_settings_durable(self, gs):
                attempted.append({
                    "relay_credential_id": gs.relay_credential_id,
                    "relay_private_key_path": gs.relay_private_key_path,
                })
                raise OSError("settings database is read-only")

        state = self.state_mod.MatrixState(db=BadDB())
        state.global_settings = self.state_mod.GlobalSettings(
            relay_credential_id="cred-old",
            relay_private_key_path="/keys/old.pem",
        )

        with self.assertRaises(OSError):
            await state.update_global_settings_durable(
                relay_credential_id=" cred-new ",
                relay_private_key_path=" /keys/new.pem ",
            )

        self.assertEqual(attempted, [{
            "relay_credential_id": "cred-new",
            "relay_private_key_path": "/keys/new.pem",
        }])
        self.assertEqual(state.global_settings.relay_credential_id, "cred-old")
        self.assertEqual(
            state.global_settings.relay_private_key_path,
            "/keys/old.pem",
        )

    async def test_durable_global_settings_success_applies_after_save(self):
        saved = []

        class GoodDB:
            async def save_global_settings_durable(self, gs):
                saved.append({
                    "relay_credential_id": gs.relay_credential_id,
                    "relay_private_key_path": gs.relay_private_key_path,
                })

        state = self.state_mod.MatrixState(db=GoodDB())
        await state.update_global_settings_durable(
            relay_credential_id="cred-new",
            relay_private_key_path="/keys/new.pem",
        )

        self.assertEqual(saved, [{
            "relay_credential_id": "cred-new",
            "relay_private_key_path": "/keys/new.pem",
        }])
        self.assertEqual(state.global_settings.relay_credential_id, "cred-new")
        self.assertEqual(
            state.global_settings.relay_private_key_path,
            "/keys/new.pem",
        )


class MatrixStatePauseBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self, *, engineer_digest_paused=True,
                    architect_digest_paused=False):
        state = self.state_mod.MatrixState()
        state.groups["g"] = ["arch-1", "eng-1", "worker-1"]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id="eng-1"
        )
        state.agents["arch-1"] = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
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
            hired_by_architect_id="arch-1",
        )
        state.update_agent_digest_settings(
            "eng-1",
            paused=engineer_digest_paused,
        )
        state.update_agent_digest_settings(
            "arch-1",
            paused=architect_digest_paused,
        )
        state._delta_ops.clear()
        return state

    async def test_paused_digest_does_not_suppress_subagent_broadcasts(self):
        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(json.loads(msg))

        state = self._make_state(
            engineer_digest_paused=True,
            architect_digest_paused=True,
        )
        ws = FakeWS()
        state._ws_clients.add(ws)

        state._emit_agent(state.agents["worker-1"])
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
                "tool_name": "mcp__torque__torque_progress",
                "hook_event_name": "PostToolUse",
                "appended_at": 2,
            },
        )
        state._emit(
            "task_upsert",
            id="TORQUE:1",
            group="g",
            task="Visible worker task",
            agent_id="worker-1",
        )
        state._emit(
            "engineer_worklog_append",
            group="g",
            entry={
                "agent_id": "worker-1",
                "task_id": "TORQUE:1",
                "event": "progress",
            },
        )
        state._emit(
            "digest_buffer_stats",
            agent_id="eng-1",
            group="g",
            buffered_events=1,
            queued_events=[],
            sent_events=[],
        )

        await state.broadcast()

        self.assertEqual(len(ws.messages), 1)
        ops = ws.messages[0]["ops"]
        self.assertEqual(
            [op["op"] for op in ops],
            [
                "agent_upsert",
                "event_append",
                "mcp_call_append",
                "task_upsert",
                "engineer_worklog_append",
                "digest_buffer_stats",
            ],
        )
        self.assertEqual(ops[0]["id"], "worker-1")
        self.assertEqual(ops[1]["cell_id"], "worker-1")
        self.assertEqual(ops[2]["call"]["cell_id"], "worker-1")
        self.assertEqual(ops[3]["agent_id"], "worker-1")
        self.assertEqual(ops[4]["entry"]["agent_id"], "worker-1")
        self.assertEqual(ops[5]["agent_id"], "eng-1")

    async def test_tombstone_and_restore_upserts_emit_normally(self):
        # Post-TORQUE:294, pause no longer suppresses broadcast at emit time —
        # tombstone/restore upserts simply reach _delta_ops like any other
        # agent_upsert. Lock that lifecycle behavior down here.
        state = self._make_state(engineer_digest_paused=True)
        worker = state.agents["worker-1"]

        state._delta_ops.clear()
        state.remove_agent(worker.id)

        self.assertEqual(len(state._delta_ops), 1)
        tombstone_op = state._delta_ops[0]
        self.assertEqual(tombstone_op["op"], "agent_upsert")
        self.assertEqual(tombstone_op["id"], worker.id)
        self.assertGreater(tombstone_op["deleted_at"], 0)

        state._delta_ops.clear()
        state.restore_agent(worker.id)

        self.assertEqual(len(state._delta_ops), 1)
        restore_op = state._delta_ops[0]
        self.assertEqual(restore_op["op"], "agent_upsert")
        self.assertEqual(restore_op["id"], worker.id)
        self.assertEqual(restore_op["deleted_at"], 0.0)
        self.assertEqual(restore_op["permanent_delete_after"], 0.0)

    async def test_unpaused_digest_broadcasts_subagent_messages_normally(self):
        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(json.loads(msg))

        state = self._make_state(engineer_digest_paused=False)
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

class MatrixStateBoardWorkflowTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def test_board_mark_task_covered_records_evidence_and_history(self):
        state = self._make_state()
        covered = state.board_add_task("Stale triage card", "g", id="TORQUE:833")
        covering = state.board_add_task(
            "Implementation covers stale card",
            "g",
            id="TORQUE:855",
        )

        result = state.board_mark_task_covered(
            covered.id,
            covering_task_id=covering.id,
            pr_url="https://github.com/runtorque/torque/pull/999",
            sha="abc1234",
            tests_run="pytest tests/test_state.py -k covered",
            notes="Implementation includes the triage fix.",
            actor_name="Panelsmith",
            actor_id="eng-1",
            actor_kind="engineer",
        )

        self.assertEqual(result["type"], "task_marked_covered")
        refreshed = state.board_tasks[covered.id]
        self.assertEqual(refreshed.lane, "Backlog")
        evidence = refreshed.completion_evidence
        self.assertEqual(evidence["status"], "evidence_attached")
        self.assertEqual(evidence["sources"], ["covered_by"])
        covered_by = evidence["covered_by"]
        self.assertEqual(covered_by["task_id"], covering.id)
        self.assertEqual(covered_by["task_title"], covering.task)
        self.assertEqual(covered_by["pr_url"], "https://github.com/runtorque/torque/pull/999")
        self.assertEqual(covered_by["sha"], "abc1234")
        self.assertEqual(covered_by["tests_run"], "pytest tests/test_state.py -k covered")
        self.assertFalse(covered_by["moved_to_done"])
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by")
        self.assertIn(covering.id, refreshed.messages[-1]["message"])
        self.assertIn("abc1234", refreshed.messages[-1]["message"])

    def test_board_mark_task_covered_can_move_to_done(self):
        state = self._make_state()
        covered = state.board_add_task(
            "Covered backlog card",
            "g",
            lane="To Do",
            id="TORQUE:834",
            status="Needs triage",
        )

        result = state.board_mark_task_covered(
            covered.id,
            pr_url="https://github.com/runtorque/torque/pull/1000",
            evidence="Resolved by linked PR.",
            actor_name="Panelsmith",
            move_to_done=True,
        )

        self.assertTrue(result["moved_to_done"])
        refreshed = state.board_tasks[covered.id]
        self.assertEqual(refreshed.lane, "Done")
        self.assertEqual(refreshed.status, "")
        self.assertTrue(refreshed.completion_evidence["covered_by"]["moved_to_done"])

    def test_board_pickup_architect_task_records_assignment_and_audit(self):
        state = self._make_state()
        task = state.board_add_task(
            "product-proposal root",
            "g",
            id="TORQUE:1130",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id="pm-1",
        )

        result = state.board_pickup_architect_task(
            task.id,
            architect_id="arch-1",
            actor_name="Torqly",
            reason="Accepted PM handoff as implementation root.",
            source="product-peer route msg-123",
            authorization={
                "scope": "routed_product_proposal_root_pickup",
                "source": "proposal_peer_route",
                "route_message_id": "msg-123",
            },
        )

        self.assertEqual(result["type"], "task_picked_up")
        refreshed = state.board_tasks[task.id]
        self.assertEqual("arch-1", refreshed.assigned_architect_id)
        evidence = refreshed.completion_evidence
        self.assertIn("architect_pickup", evidence["sources"])
        pickup = evidence["architect_pickup"]
        self.assertEqual("arch-1", pickup["picked_up_by_id"])
        self.assertEqual(
            "",
            pickup["previous_assignment"]["assigned_architect_id"],
        )
        self.assertEqual(
            "msg-123",
            pickup["authorization"]["route_message_id"],
        )
        self.assertEqual(refreshed.messages[-1]["action"], "architect_pickup")
        self.assertIn("Torqly", refreshed.messages[-1]["message"])

    def test_board_finalize_existing_task_coverage_preserves_original_evidence(self):
        state = self._make_state()
        covered = state.board_add_task(
            "Already covered backlog card",
            "g",
            id="TORQUE:1116",
            status="Covered elsewhere",
        )
        covering = state.board_add_task(
            "Covering implementation",
            "g",
            id="TORQUE:1119",
        )
        state.board_mark_task_covered(
            covered.id,
            covering_task_id=covering.id,
            pr_url="https://github.com/runtorque/torque/pull/942",
            sha="abc942",
            tests_run="make test",
            notes="Final shipped evidence.",
            actor_name="Torqly",
            actor_id="arch-1",
            actor_kind="architect",
            authorization={
                "scope": "routed_product_proposal_root",
                "source": "covering_task_label",
                "covered_task_id": covered.id,
                "covering_task_id": covering.id,
            },
            move_to_done=False,
        )
        original = dict(covered.completion_evidence["covered_by"])

        result = state.board_finalize_existing_task_coverage(
            covered.id,
            actor_name="Torque",
            actor_id="arch-1",
            actor_kind="system",
            reason="Backlog hygiene.",
        )

        self.assertEqual(result["type"], "task_coverage_finalized")
        refreshed = state.board_tasks[covered.id]
        self.assertEqual(refreshed.lane, "Done")
        self.assertEqual(refreshed.status, "")
        covered_by = refreshed.completion_evidence["covered_by"]
        for key in (
                "recorded_at", "recorded_by", "recorded_by_id",
                "recorded_by_kind", "task_id", "task_title", "pr_url", "sha",
                "tests_run", "notes", "authorization"):
            self.assertEqual(covered_by[key], original[key])
        self.assertFalse(original["moved_to_done"])
        self.assertTrue(covered_by["moved_to_done"])
        self.assertEqual(covered_by["finalized_by"], "Torque")
        self.assertEqual(covered_by["finalized_by_id"], "arch-1")
        self.assertEqual(covered_by["finalized_by_kind"], "system")
        self.assertEqual(refreshed.messages[-2]["action"], "covered_by")
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by_finalized")
        self.assertIn("Backlog hygiene", refreshed.messages[-1]["message"])

    def test_board_mark_task_covered_rejects_empty_or_self_coverage(self):
        state = self._make_state()
        task = state.board_add_task("Triage card", "g", id="TORQUE:794")

        with self.assertRaisesRegex(ValueError, "At least one"):
            state.board_mark_task_covered(task.id)
        with self.assertRaisesRegex(ValueError, "another task"):
            state.board_mark_task_covered(task.id, covering_task_id=task.id)

    def test_agent_message_history_persists_across_state_reload(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            session_id="session-1",
        )
        db.save_group("g", 0)
        db.save_agent(worker)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = [worker.id]
        state.agents[worker.id] = worker

        entry = state.record_message_history(worker.id, "hello across restart")

        self.assertIsNotNone(entry)
        self.assertEqual(
            state.to_dict()["agent_message_history"][worker.id][0]["message"],
            "hello across restart",
        )

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        self.assertEqual(
            reloaded.to_dict()["agent_message_history"][worker.id][0]["message"],
            "hello across restart",
        )

    def _add_engineer_followup(
            self, state, parent, task_id, *, lane="Backlog",
            depth=1, reply_agent_id="", status="Awaiting Reply",
            messages=None):
        return state.board_add_task(
            f"Engineer: {task_id}",
            "g",
            lane=lane,
            id=task_id,
            parent_task_id=parent.id,
            pipeline_root_id=parent.pipeline_root_id or parent.id,
            pipeline_depth=depth,
            reply_agent_id=reply_agent_id,
            status=status,
            labels=["torque:derived", "torque:engineer-message"],
            messages=list(messages or []),
        )

    def _assert_engineer_followup_expired(self, state, task_id):
        task = state.board_tasks[task_id]
        self.assertEqual(task.lane, "Done")
        self.assertEqual(task.status, "")
        self.assertEqual(
            [message.get("message") for message in task.messages],
            [self.state_mod._ENGINEER_MESSAGE_EXPIRY_NOTE],
        )

    def test_resolve_board_task_id_prefers_alias_over_archived_literal(self):
        state = self._make_state()
        archived = self.state_mod.BoardTask(
            id="TORQUE:51",
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

        self.assertEqual(state.resolve_task_alias("TORQUE:51"), live.id)
        self.assertEqual(state.resolve_board_task_id("TORQUE:51"), live.id)
        self.assertEqual(state.resolve_board_task_id("TORQUE:5"), live.id)

    def test_board_add_task_archived_literal_collision_creates_persisted_alias(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["Torque"] = []
        state._db_save_groups()
        state.task_id_counters["TORQUE"] = 51
        archived = self.state_mod.BoardTask(
            id="TORQUE:51",
            task="Archived original",
            group="Torque",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        state.board_tasks[archived.id] = archived
        state._db_save_task(archived)

        task = state.board_add_task("New live task", "Torque")

        self.assertIsNotNone(task)
        self.assertNotEqual(task.id, "TORQUE:51")
        self.assertEqual(len(task.id), 8)
        self.assertEqual(state.task_id_aliases["TORQUE:51"], task.id)
        self.assertTrue(db.board_task_exists(task.id))
        self.assertTrue(db.board_task_exists("TORQUE:51"))
        self.assertEqual(state.resolve_board_task_id("TORQUE:51"), task.id)

    def test_board_update_task_alias_persists_missing_canonical_and_full_delta(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        archived = self.state_mod.BoardTask(
            id="TORQUE:51",
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
        db.save_task_id_alias("TORQUE:51", live.id)
        state.board_tasks[archived.id] = archived
        state.board_tasks[live.id] = live
        state.task_id_aliases["TORQUE:51"] = live.id

        state.board_update_task(
            "TORQUE:51",
            description="Architect-written description",
            action_name="feature/implement",
            assigned_engineer_id="eng-1",
        )

        updated = state.board_tasks[live.id]
        self.assertEqual(updated.description, "Architect-written description")
        self.assertEqual(updated.action_name, "feature/implement")
        self.assertEqual(updated.assigned_engineer_id, "eng-1")
        self.assertEqual(state.board_tasks["TORQUE:51"].task, archived.task)
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
            "SELECT task, description FROM board_tasks WHERE id='TORQUE:51'"
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
        self.assertEqual(reloaded.resolve_board_task_id("TORQUE:51"), live.id)
        self.assertEqual(
            reloaded.board_tasks[live.id].description,
            "Architect-written description",
        )

    def test_board_add_task_allocates_group_scoped_root_ids(self):
        state = self.state_mod.MatrixState()
        state.groups["Torque Team"] = []
        state.groups["Ops"] = []

        first = state.board_add_task("First task", "Torque Team")
        second = state.board_add_task("Second task", "Torque Team")
        third = state.board_add_task("Ops task", "Ops")

        self.assertEqual(first.id, "TORQUE_TEAM:1")
        self.assertEqual(second.id, "TORQUE_TEAM:2")
        self.assertEqual(third.id, "OPS:1")

    def test_board_add_task_transliterates_accented_group_names(self):
        state = self.state_mod.MatrixState()
        state.groups["Atlas Público"] = []

        task = state.board_add_task("Localization", "Atlas Público")

        self.assertEqual(task.id, "ATLAS_PUBLICO:1")

    def test_board_add_task_allocates_pipeline_scoped_child_ids_across_groups(self):
        state = self.state_mod.MatrixState()
        state.groups["Torque"] = []
        state.groups["Review Team"] = []

        root = state.board_add_task("Root", "Torque")
        child = state.board_add_task(
            "Implement",
            "Torque",
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

        self.assertEqual(root.id, "TORQUE:1")
        self.assertEqual(child.id, "TORQUE:1:1")
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
            labels=["torque:engineer-message"],
        )
        pending_old = state.board_add_task(
            "Older thread",
            "g",
            lane="Backlog",
            id="task-old",
            reply_agent_id="agent-1",
            labels=["torque:engineer-message"],
        )
        pending_new = state.board_add_task(
            "Newer thread",
            "g",
            lane="Backlog",
            id="task-new",
            reply_agent_id="agent-1",
            labels=["torque:engineer-message"],
        )
        state.board_add_task(
            "Other worker thread",
            "g",
            lane="Backlog",
            id="task-other",
            reply_agent_id="agent-2",
            labels=["torque:engineer-message"],
        )

        pending = state.agent_pending_engineer_reply_tasks("agent-1")

        self.assertEqual([task.id for task in pending], [pending_old.id, pending_new.id])

    def test_load_restores_pending_engineer_message_from_open_followup_tasks(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                labels=["torque:engineer-message"],
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertTrue(state.agents["agent-1"].pending_engineer_message)
        self.assertEqual(
            [task.id for task in state.agent_pending_engineer_reply_tasks("agent-1")],
            ["task-reply"],
        )

    def test_load_retroactively_expires_historical_engineer_message_ghosts(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
                id="task-parent",
                task="Completed parent",
                group="g",
                lane="Done",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-reply",
                task="Engineer: Check status",
                group="g",
                lane="Backlog",
                parent_task_id="task-parent",
                pipeline_root_id="task-parent",
                pipeline_depth=1,
                reply_agent_id="agent-1",
                status="Awaiting Reply",
                labels=["torque:engineer-message"],
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        expired = state.board_tasks["task-reply"]
        self.assertEqual(expired.lane, "Done")
        self.assertEqual(expired.status, "")
        self.assertEqual(
            [message.get("message") for message in expired.messages],
            [self.state_mod._ENGINEER_MESSAGE_EXPIRY_NOTE],
        )
        self.assertFalse(state.agents["agent-1"].pending_engineer_message)
        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()
        self.assertEqual(reloaded.board_tasks["task-reply"].lane, "Done")
        self.assertEqual(
            len(reloaded.board_tasks["task-reply"].messages),
            1,
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

    def test_done_cascade_holds_mandatory_review_root_without_ship(self):
        """Regression for TORQUE:1353's legacy-cascade bypass."""
        state = self._make_state()
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-implement",
            action_name="feature/implement",
            requires_review=True,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            completion_evidence={"review": {"verdict": "unknown"}},
        )

        state.board_move_task(review.id, "Done")

        self.assertEqual(review.lane, "Done")
        self.assertEqual(root.lane, "In Progress")

    def test_done_cascade_allows_root_without_action_review_gate(self):
        state = self._make_state()
        root = state.board_add_task(
            "General follow-up",
            "g",
            lane="In Progress",
            id="task-root",
            # A stale/manual structural flag alone must not invent an action
            # contract for an unbound task.
            requires_review=True,
        )
        child = state.board_add_task(
            "Finish follow-up",
            "g",
            lane="In Progress",
            id="task-child",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )

        state.board_move_task(child.id, "Done")

        self.assertEqual(root.lane, "Done")

    def test_done_cascade_holds_root_while_second_review_is_open(self):
        """Model TORQUE:1215: a Ship cannot outrun an outstanding review."""
        state = self._make_state()
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-implement",
            action_name="feature/implement",
            requires_review=True,
        )
        first_review = state.board_add_task(
            "Review pass one",
            "g",
            lane="In Progress",
            id="task-review-one",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            # Deliberately rely on the canonical raw-message fallback rather
            # than completion_evidence, as the live record must be read.
            messages=[{"action": "done", "message": "Verdict: Ship"}],
        )
        second_review = state.board_add_task(
            "Review pass two",
            "g",
            lane="In Progress",
            id="task-review-two",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )

        state.board_move_task(first_review.id, "Done")

        self.assertEqual(first_review.lane, "Done")
        self.assertEqual(second_review.lane, "In Progress")
        self.assertEqual(root.lane, "In Progress")

    def test_done_cascade_allows_ship_without_implementer_done(self):
        """TORQUE:1358 shape: reviewer Ship, not parent self-done, closes it."""
        state = self._make_state()
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-implement",
            action_name="feature/implement",
            requires_review=True,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            messages=[{"action": "done", "message": "Verdict: Ship"}],
        )

        state.board_move_task(review.id, "Done")

        self.assertEqual(root.lane, "Done")

    def test_done_cascades_multi_pass_review_chain_after_ship_verdict(self):
        state = self._make_state()
        root = state.board_add_task(
            "Implement cascading completion",
            "g",
            lane="In Progress",
            id="TORQUE:77",
            status="On review",
            health_state="idle-risk",
            health_details={"reasons": ["progress_silence_warning"]},
        )
        review_one = state.board_add_task(
            "Review pass 1",
            "g",
            lane="In Progress",
            id="TORQUE:77:1",
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
            id="TORQUE:77:2",
            parent_task_id=review_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )
        review_two = state.board_add_task(
            "Review pass 2",
            "g",
            lane="In Progress",
            id="TORQUE:77:3",
            parent_task_id=fix_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=3,
        )
        fix_two = state.board_add_task(
            "Fix review pass 2",
            "g",
            lane="In Progress",
            id="TORQUE:77:4",
            parent_task_id=review_two.id,
            pipeline_root_id=root.id,
            pipeline_depth=4,
        )
        final_review = state.board_add_task(
            "Final review pass",
            "g",
            lane="In Progress",
            id="TORQUE:77:5",
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
                ("TORQUE:77", "Done"),
                ("TORQUE:77:1", "Done"),
                ("TORQUE:77:2", "Done"),
                ("TORQUE:77:3", "Done"),
                ("TORQUE:77:4", "Done"),
                ("TORQUE:77:5", "Done"),
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
            labels=["torque:derived", "torque:engineer-message"],
        )

        state.board_move_task(follow_up.id, "Done")

        self.assertEqual(state.board_tasks[follow_up.id].lane, "Done")
        self.assertEqual(state.board_tasks[parent.id].lane, "In Progress")
        self.assertEqual(state.board_tasks[parent.id].status, "Reviewing")

    def test_parent_done_expires_open_engineer_message_child(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
            pending_engineer_message=True,
        )
        state.agents[worker.id] = worker
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id=worker.id,
        )
        follow_up = self._add_engineer_followup(
            state, parent, "task-reply", reply_agent_id=worker.id
        )

        state.board_move_task(parent.id, "Done")

        self._assert_engineer_followup_expired(state, follow_up.id)
        self.assertFalse(state.agents[worker.id].pending_engineer_message)
        self.assertEqual(state.agent_pending_engineer_reply_tasks(worker.id), [])
        self.assertFalse(state.agent_is_busy(worker.id))

    def test_parent_done_does_not_double_close_answered_engineer_message_child(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
        )
        follow_up = self._add_engineer_followup(
            state,
            parent,
            "task-reply",
            lane="Done",
            status="",
            messages=[{
                "timestamp": 1,
                "action": "reply",
                "message": "Answered already",
                "agent_name": "Worker",
            }],
        )

        state.board_move_task(parent.id, "Done")

        answered = state.board_tasks[follow_up.id]
        self.assertEqual(answered.lane, "Done")
        self.assertEqual(len(answered.messages), 1)
        self.assertNotIn(
            self.state_mod._ENGINEER_MESSAGE_EXPIRY_NOTE,
            [message.get("message") for message in answered.messages],
        )

    def test_parent_done_expires_multi_level_engineer_message_descendants(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
        )
        normal_child = state.board_add_task(
            "Follow-up work",
            "g",
            lane="To Do",
            id="task-child",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        direct_follow_up = self._add_engineer_followup(
            state, parent, "task-direct-reply"
        )
        nested_follow_up = self._add_engineer_followup(
            state, normal_child, "task-nested-reply", depth=2
        )

        state.board_move_task(parent.id, "Done")

        self.assertEqual(state.board_tasks[normal_child.id].lane, "To Do")
        for task_id in (direct_follow_up.id, nested_follow_up.id):
            self._assert_engineer_followup_expired(state, task_id)

    def test_archive_from_done_expires_open_engineer_message_descendants(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Completed parent",
            "g",
            lane="Done",
            id="task-parent",
        )
        follow_up = self._add_engineer_followup(state, parent, "task-reply")

        state.board_archive_task(parent.id)

        self.assertEqual(state.board_tasks[parent.id].lane, "Archived")
        self.assertEqual(state.board_tasks[parent.id].archived_from_lane, "Done")
        self._assert_engineer_followup_expired(state, follow_up.id)

    def test_batch_archive_rolls_back_when_batch_persist_fails(self):
        state = self._make_state()
        first = state.board_add_task(
            "Completed one",
            "g",
            lane="Done",
            id="task-done-one",
        )
        second = state.board_add_task(
            "Completed two",
            "g",
            lane="Done",
            id="task-done-two",
        )
        state._delta_ops.clear()

        class FailingDB:
            def save_board_tasks(self, tasks):
                self.tasks = list(tasks)
                raise RuntimeError("synthetic batch failure")

        failing_db = FailingDB()
        state.db = failing_db

        with self.assertRaisesRegex(RuntimeError, "synthetic batch failure"):
            state.board_archive_tasks([first.id, second.id])

        self.assertEqual(state.board_tasks[first.id].lane, "Done")
        self.assertEqual(state.board_tasks[second.id].lane, "Done")
        self.assertEqual(state.board_tasks[first.id].archived_at, "")
        self.assertEqual(state.board_tasks[second.id].archived_at, "")
        self.assertEqual(state._delta_ops, [])
        self.assertEqual(
            sorted(task.id for task in failing_db.tasks),
            [first.id, second.id],
        )

    def test_retroactive_cleanup_expires_historical_engineer_message_ghosts(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            pending_engineer_message=True,
        )
        state.agents[worker.id] = worker
        done_one = state.board_add_task(
            "Done parent one",
            "g",
            lane="Done",
            id="task-done-one",
        )
        done_two = state.board_add_task(
            "Done parent two",
            "g",
            lane="Done",
            id="task-done-two",
        )
        archived = state.board_add_task(
            "Archived done parent",
            "g",
            lane="Archived",
            id="task-archived",
            archived_from_lane="Done",
            archived_at="2026-04-29T00:00:00+00:00",
        )
        open_parent = state.board_add_task(
            "Still open parent",
            "g",
            lane="In Progress",
            id="task-open",
        )
        ghost_ids = []
        for idx, parent in enumerate((done_one, done_two, archived), start=1):
            ghost = self._add_engineer_followup(
                state, parent, f"task-ghost-{idx}", reply_agent_id=worker.id
            )
            ghost_ids.append(ghost.id)
        live_follow_up = self._add_engineer_followup(
            state, open_parent, "task-live-reply", reply_agent_id=worker.id
        )

        expired = state.cleanup_resolved_engineer_message_followups()
        expired_again = state.cleanup_resolved_engineer_message_followups()

        self.assertEqual(expired, 3)
        self.assertEqual(expired_again, 0)
        for task_id in ghost_ids:
            self._assert_engineer_followup_expired(state, task_id)
        self.assertEqual(state.board_tasks[live_follow_up.id].lane, "Backlog")
        self.assertTrue(state.agents[worker.id].pending_engineer_message)
        self.assertEqual(
            [task.id for task in state.agent_pending_engineer_reply_tasks(worker.id)],
            [live_follow_up.id],
        )

    def test_engineer_message_followup_predicate_uses_system_label(self):
        task = self.state_mod.BoardTask(
            id="task-reply",
            task="Engineer: Need status",
            group="g",
            lane="Backlog",
            labels=["torque:derived", "torque:engineer-message"],
        )
        normal = self.state_mod.BoardTask(
            id="task-normal",
            task="Implement feature",
            group="g",
            lane="Backlog",
            labels=["torque:derived"],
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
            labels=["torque:derived", "torque:engineer-message"],
        )
        child = state.board_add_task(
            "Investigate reply",
            "g",
            lane="In Progress",
            id="task-reply-child",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            labels=["torque:derived"],
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
            labels=["torque:blocked", "keep"],
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
            labels=["torque:error", "keep"],
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
        self.assertEqual(updated.labels, ["torque:error", "keep"])
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

    def test_verification_summary_normalizes_taxonomy_fields(self):
        state = self._make_state()

        task = state.board_add_task(
            "Verify flaky run",
            "g",
            id="task-1",
            verification_state="attempted",
            verification_summary={
                "tests_run": "make test",
                "test_outcome": "unrelated_flake_accepted",
                "full_suite_attempted": 1,
                "unrelated_flake_accepted": True,
                "isolated_rerun_evidence": (
                    "python3 -m unittest tests.test_state passed"
                ),
                "reviewer_acceptance": "accepted_flake_evidence",
                "live_smoke_pending": False,
                "unknown_future_key": "ignored",
            },
        )

        summary = task.verification_summary
        self.assertEqual(summary["test_outcome"], "unrelated_flake_accepted")
        self.assertTrue(summary["full_suite_attempted"])
        self.assertTrue(summary["unrelated_flake_accepted"])
        self.assertFalse(summary["live_smoke_pending"])
        self.assertEqual(
            summary["isolated_rerun_evidence"],
            "python3 -m unittest tests.test_state passed",
        )
        self.assertEqual(
            summary["reviewer_acceptance"],
            "accepted_flake_evidence",
        )
        self.assertNotIn("unknown_future_key", summary)

        state.board_update_task(
            task.id,
            verification_summary={
                "test_outcome": "not-a-real-outcome",
                "reviewer_acceptance": "not-a-real-acceptance",
                "deploy_attempted": False,
            },
        )

        summary = state.board_tasks[task.id].verification_summary
        self.assertNotIn("test_outcome", summary)
        self.assertNotIn("reviewer_acceptance", summary)
        self.assertFalse(summary["deploy_attempted"])

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

    def test_archiving_task_resets_stale_health_state(self):
        state = self._make_state()
        stale_since = "2026-05-02T21:05:34.887564+00:00"
        task = state.board_add_task(
            "In-flight work",
            "g",
            lane="In Progress",
            id="task-1",
        )

        self.assertIsNotNone(task)
        task.health_state = "stalled"
        task.health_since = stale_since

        state.board_archive_task(task.id)

        archived = state.board_tasks[task.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.health_state, "healthy")
        self.assertNotEqual(archived.health_since, stale_since)
        self.assertGreaterEqual(
            datetime.fromisoformat(archived.health_since),
            datetime.fromisoformat(archived.archived_at),
        )

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
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.streams_mod = importlib.import_module("torque.worktree_streams")
        # This fixture uses a synthetic, non-repository path.  Reset the
        # process-global readiness cache so the snapshot test neither leaks
        # nor depends on a probe installed by another test.
        self.streams_mod.invalidate_branch_exists_cache("/repo")

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
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
            git_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        product = self.state_mod.BoardTask(
            id="TORQUE:1",
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
            id="TORQUE:1:1",
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
                "branch": "torque/worker",
                "base_branch": "main",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        # `ready_to_merge` deliberately requires a fresh current-base probe;
        # a historical clean review is not enough.  Model that probe directly
        # rather than making this snapshot fixture consult the host checkout.
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/worker", "main", {
                "state": "fresh",
                "stale": False,
                "source": "merge_readiness_check",
                "merge_clean": True,
            },
        )
        return state, product

    def test_to_dict_includes_engineer_streams_snapshot(self):
        state, _product = self._make_state_with_open_stream()

        payload = state.to_dict()

        self.assertIn("engineer_streams", payload)
        self.assertIn("g", payload["engineer_streams"])
        summary = payload["engineer_streams"]["g"]
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["items"][0]["branch"], "torque/worker")
        self.assertEqual(summary["items"][0]["state"], "ready_to_merge")
        self.assertEqual(
            summary["items"][0]["merge_readiness"]["stale_base"]["source"],
            "merge_readiness_check",
        )

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
            "torque/worker",
        )

    async def test_engineer_recompute_debounces_a_broadcast_burst(self):
        state, _product = self._make_state_with_open_stream()
        runtime_mod = importlib.import_module("torque.state_runtime")
        computed = []

        def compute(groups):
            computed.append(set(groups))
            return []

        state._compute_engineer_stream_payloads = compute
        original = runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS
        runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = 0.05
        try:
            for index in range(5):
                state._emit("task_upsert", id=f"burst-{index}", group="g")
                await state.broadcast()
            # Allow the worker to enter its debounce wait, then add another
            # primary-broadcast group inside the coalescing window.
            await asyncio.sleep(0.01)
            state._schedule_engineer_recompute({"other"})
            await state._engineer_recompute_task
        finally:
            runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = original

        self.assertEqual(computed, [{"g", "other"}])
        self.assertLess(len(computed), 5)

    async def test_engineer_recompute_drains_group_dirtied_during_threaded_compute(self):
        state, _product = self._make_state_with_open_stream()
        runtime_mod = importlib.import_module("torque.state_runtime")
        computed = []
        started = threading.Event()
        release = threading.Event()
        main_thread = threading.get_ident()

        def compute(groups):
            computed.append((set(groups), threading.get_ident()))
            if groups == {"g"}:
                started.set()
                release.wait(timeout=1)
            return []

        state._compute_engineer_stream_payloads = compute
        original = runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS
        runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = 0.01
        try:
            state._schedule_engineer_recompute({"g"})
            await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
            # This arrives while the first CPU pass is off-loop. It must be
            # consumed by the worker's next drain iteration, not discarded.
            state._schedule_engineer_recompute({"g"})
            release.set()
            await state._engineer_recompute_task
        finally:
            runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = original
            release.set()

        self.assertEqual([groups for groups, _thread in computed], [
            {"g"}, {"g"},
        ])
        self.assertTrue(all(thread != main_thread for _groups, thread in computed))

    async def test_engineer_recompute_reschedules_pending_work_on_cancellation(self):
        state, _product = self._make_state_with_open_stream()
        runtime_mod = importlib.import_module("torque.state_runtime")
        computed = []
        state._compute_engineer_stream_payloads = lambda groups: (
            computed.append(set(groups)) or []
        )
        original = runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS
        runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = 0.01
        try:
            state._schedule_engineer_recompute({"g"})
            cancelled_worker = state._engineer_recompute_task
            await asyncio.sleep(0)
            cancelled_worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled_worker
            replacement_worker = state._engineer_recompute_task
            self.assertIsNotNone(replacement_worker)
            self.assertIsNot(replacement_worker, cancelled_worker)
            await replacement_worker
        finally:
            runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = original

        self.assertEqual(computed, [{"g"}])

    async def test_engineer_recompute_retries_a_threaded_payload_failure(self):
        state, _product = self._make_state_with_open_stream()
        runtime_mod = importlib.import_module("torque.state_runtime")
        computed = []

        def compute(groups):
            computed.append(set(groups))
            if len(computed) == 1:
                raise RuntimeError("transient threaded read failure")
            return []

        state._compute_engineer_stream_payloads = compute
        original = runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS
        runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = 0.01
        try:
            state._schedule_engineer_recompute({"g"})
            await state._engineer_recompute_task
        finally:
            runtime_mod._ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = original

        self.assertEqual(computed, [{"g"}, {"g"}])


class AgentCellActivityClockTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
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

        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_default_is_empty_string(self):
        state = self.state_mod.MatrixState()
        self.assertEqual(state.selected_principal_id, "")

    def test_to_dict_includes_selected_principal_id(self):
        state = self.state_mod.MatrixState()
        state.selected_principal_id = "architect-a"
        d = state.to_dict()
        self.assertEqual(d["selected_principal_id"], "architect-a")

    def test_to_dict_includes_persisted_ui_state(self):
        state = self.state_mod.MatrixState()
        state.active_group = "beta"
        state.window_bounds = {"main": {"width": 1200, "height": 800}}
        state.workspace_sidebar_width = 720
        state.terminal_direct_messages_height = 224
        state.terminal_compose_height = 116
        state.context_panel_split_ratio = 0.44
        state.supervisor_panel_state = {"sortKey": "bytes"}
        state.board_selected_lanes_by_group = {"beta": "Done"}
        state.board_hidden_wide_lanes_by_group = {
            "beta": {"To Do": True}
        }

        d = state.to_dict()

        self.assertEqual(d["active_group"], "beta")
        self.assertEqual(d["window_bounds"], state.window_bounds)
        self.assertEqual(d["workspace_sidebar_width"], 720)
        self.assertEqual(d["terminal_direct_messages_height"], 224)
        self.assertEqual(d["terminal_compose_height"], 116)
        self.assertEqual(d["context_panel_split_ratio"], 0.44)
        self.assertEqual(d["supervisor_panel_state"], {"sortKey": "bytes"})
        self.assertEqual(
            d["board_selected_lanes_by_group"],
            {"beta": "Done"},
        )
        self.assertEqual(
            d["board_hidden_wide_lanes_by_group"],
            {"beta": {"To Do": True}},
        )

    def test_to_dict_includes_selected_agent_id(self):
        state = self.state_mod.MatrixState()
        state.selected_agent_id = "agent-a"
        d = state.to_dict()
        self.assertEqual(d["selected_agent_id"], "agent-a")

    def test_to_dict_compact_includes_selected_principal_id(self):
        state = self.state_mod.MatrixState()
        state.selected_principal_id = "architect-b"
        d = state.to_dict_compact()
        self.assertEqual(d["selected_principal_id"], "architect-b")

    def test_to_dict_compact_includes_selected_agent_id(self):
        state = self.state_mod.MatrixState()
        state.selected_agent_id = "agent-b"
        d = state.to_dict_compact()
        self.assertEqual(d["selected_agent_id"], "agent-b")

    def test_persists_and_restores_selected_principal_id(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        db.save_ui_state("selected_principal_id", "architect-42")

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.selected_principal_id, "architect-42")

    def test_persists_and_restores_selected_agent_id(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        db.save_ui_state("selected_agent_id", "agent-42")

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.selected_agent_id, "agent-42")

    def test_restores_standalone_panel_layout_without_agents_or_groups(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        layout = {
            "version": 1,
            "bottom": {
                "open": True,
                "size": 280,
                "tabs": ["board"],
                "active": "board",
            },
            "right": {
                "open": True,
                "size": 320,
                "tabs": ["actions", "templates", "history", "context"],
                "active": "history",
            },
            "floats": {},
            "last_active": "history",
        }
        db.save_ui_state("standalone_panel_layout", json.dumps(layout))

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.standalone_panel_layout, layout)

    def test_persists_and_restores_window_group_and_board_lane_state(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        db.save_groups({"g": []}, {"g": "g"})
        db.save_ui_state("active_group", "g")
        db.save_ui_state(
            "window_bounds",
            json.dumps({"main": {"width": 1200, "height": 800}}),
        )
        db.save_ui_state("workspace_sidebar_width", "700")
        db.save_ui_state("terminal_direct_messages_height", "224")
        db.save_ui_state("terminal_compose_height", "116")
        db.save_ui_state("context_panel_split_ratio", "0.42")
        db.save_ui_state(
            "supervisor_panel_state",
            json.dumps({"sortKey": "bytes", "sortDirection": "desc"}),
        )
        db.save_ui_state(
            "board_selected_lanes_by_group",
            json.dumps({"g": "Done"}),
        )
        db.save_ui_state(
            "board_hidden_wide_lanes_by_group",
            json.dumps({"g": {"To Do": True}}),
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.active_group, "g")
        self.assertEqual(
            state.window_bounds,
            {"main": {"width": 1200, "height": 800}},
        )
        self.assertEqual(state.workspace_sidebar_width, 700)
        self.assertEqual(state.terminal_direct_messages_height, 224)
        self.assertEqual(state.terminal_compose_height, 116)
        self.assertEqual(state.context_panel_split_ratio, 0.42)
        self.assertEqual(
            state.supervisor_panel_state,
            {"sortKey": "bytes", "sortDirection": "desc"},
        )
        self.assertEqual(state.board_selected_lanes_by_group, {"g": "Done"})
        self.assertEqual(
            state.board_hidden_wide_lanes_by_group,
            {"g": {"To Do": True}},
        )

    def test_defaults_to_empty_when_ui_state_missing(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(state.selected_principal_id, "")
        self.assertEqual(state.selected_agent_id, "")
        self.assertEqual(state.active_group, "")
        self.assertEqual(state.window_bounds, {})
        self.assertEqual(state.workspace_sidebar_width, 0)
        self.assertEqual(state.terminal_direct_messages_height, 0)
        self.assertEqual(state.terminal_compose_height, 0)
        self.assertEqual(state.supervisor_panel_state, {})
        self.assertEqual(state.board_selected_lanes_by_group, {})
        self.assertEqual(state.board_hidden_wide_lanes_by_group, {})


class DetachedPanelsStateTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_defaults_to_empty_dict(self):
        state = self.state_mod.MatrixState()
        self.assertEqual(state.detached_panels, {})
        self.assertEqual(state.to_dict()["detached_panels"], {})
        self.assertEqual(state.to_dict_compact()["detached_panels"], {})

    def test_persists_and_restores_detached_panels(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        db.save_groups({"g": []}, {"g": "g"})
        db.save_ui_state(
            "detached_panels",
            json.dumps({
                "engineer": {
                    "label": "panel-engineer-abc123",
                    "bounds": {"x": 10, "y": 20, "width": 900, "height": 640},
                }
            }),
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(
            state.detached_panels["engineer"]["label"],
            "panel-engineer-abc123",
        )


class EngineerDispatchShapeStateTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_records_newest_first_and_caps_per_engineer_without_snapshot(self):
        state = self.state_mod.MatrixState()

        for idx in range(105):
            state.record_engineer_dispatch_shape(
                "engineer-1",
                group="g",
                source_tool="engineer_task_dispatch",
                shape="serial",
                task_ids=[f"task-{idx}"],
                hintable=True,
            )
        state.record_engineer_dispatch_shape(
            "engineer-2",
            group="g",
            source_tool="engineer_task_dispatch",
            shape="batch",
            task_ids=["other-task"],
            task_count=1,
        )

        events = state.engineer_dispatch_shape_events(
            "engineer-1",
            limit=200,
        )
        self.assertEqual(len(events), 100)
        self.assertEqual(events[0]["task_ids"], ["task-104"])
        self.assertEqual(events[-1]["task_ids"], ["task-5"])
        self.assertEqual(
            state.engineer_dispatch_shape_events("engineer-2")[0]["shape"],
            "batch",
        )
        self.assertNotIn("engineer_dispatch_shapes", state.to_dict())
        self.assertNotIn("engineer_dispatch_shapes", state.to_dict_compact())

    def test_summary_counts_direct_dispatches_separately_from_derives(self):
        state = self.state_mod.MatrixState()
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="engineer_task_dispatch",
            shape="serial",
            task_ids=["serial-1"],
            hintable=True,
        )
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="engineer_task_dispatch",
            shape="serial",
            task_ids=["serial-override"],
            hintable=False,
            metadata={"has_launch_overrides": True},
        )
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="engineer_batch_dispatch",
            shape="batch",
            task_ids=["batch-1", "batch-2"],
            task_count=2,
        )
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="engineer_task_dispatch",
            shape="warm_cluster",
            task_ids=["warm-1"],
        )
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="torque_derive",
            shape="serial",
            task_ids=["derive-serial"],
        )
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="torque_derive",
            shape="warm_cluster",
            task_ids=["derive-warm"],
        )
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="other",
            source_tool="engineer_task_dispatch",
            shape="serial",
            task_ids=["other-group"],
            hintable=True,
        )

        summary = state.engineer_dispatch_shape_summary(
            "engineer-1",
            group="g",
            window=20,
        )

        self.assertEqual(summary["total"], 4)
        self.assertEqual(
            summary["counts"],
            {"serial": 2, "batch": 1, "warm_cluster": 1},
        )
        self.assertEqual(summary["hintable_serial"], 1)
        self.assertEqual(summary["derives_total"], 2)
        self.assertEqual(
            summary["derives_by_shape"],
            {"serial": 1, "batch": 0, "warm_cluster": 1},
        )
        direct_events = state.engineer_dispatch_shape_events(
            "engineer-1",
            group="g",
            limit=20,
            include_derives=False,
        )
        self.assertEqual(
            {event["source_tool"] for event in direct_events},
            {"engineer_task_dispatch", "engineer_batch_dispatch"},
        )

    def test_record_rejects_unknown_shape(self):
        state = self.state_mod.MatrixState()

        with self.assertRaises(ValueError):
            state.record_engineer_dispatch_shape(
                "engineer-1",
                source_tool="engineer_task_dispatch",
                shape="parallelish",
            )

    def test_dispatch_shape_events_are_not_persisted_on_reload(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.record_engineer_dispatch_shape(
            "engineer-1",
            group="g",
            source_tool="engineer_task_dispatch",
            shape="serial",
            task_ids=["task-1"],
            hintable=True,
        )

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        self.assertEqual(
            reloaded.engineer_dispatch_shape_events("engineer-1"),
            [],
        )


class RelayConnectionSignalTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _state(self):
        return self.state_mod.MatrixState()

    def test_default_is_disabled_and_in_snapshot(self):
        state = self._state()
        self.assertEqual(state.relay_connection["status"], "disabled")
        self.assertFalse(state.relay_connection["enabled"])
        full = state.to_dict()
        compact = state.to_dict_compact()
        self.assertEqual(full["relay_connection"]["status"], "disabled")
        self.assertEqual(compact["relay_connection"]["status"], "disabled")
        # Snapshot is a copy, not the live dict, so a later mutation can't alias.
        self.assertIsNot(full["relay_connection"], state.relay_connection)

    def test_status_change_emits_exactly_one_delta(self):
        state = self._state()
        emitted = state.set_relay_connection({
            "status": "connecting",
            "enabled": True,
            "relay_host": "relay.runtorque.com",
            "daemon_id": "daemon-1",
            "since": "2026-05-23T00:00:00.000Z",
        })
        self.assertTrue(emitted)
        ops = [op for op in state._delta_ops if op["op"] == "relay_connection"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["status"], "connecting")
        self.assertTrue(ops[0]["enabled"])
        self.assertEqual(ops[0]["relay_host"], "relay.runtorque.com")
        # Live snapshot now reflects the new state.
        self.assertEqual(state.to_dict()["relay_connection"]["status"], "connecting")

    def test_identical_payload_dedupes_to_no_delta(self):
        state = self._state()
        payload = {
            "status": "connected",
            "enabled": True,
            "relay_host": "relay.runtorque.com",
            "daemon_id": "daemon-1",
            "last_connected_at": "2026-05-23T00:00:00.000Z",
            "retry_count": 0,
            "since": "2026-05-23T00:00:00.000Z",
        }
        self.assertTrue(state.set_relay_connection(payload))
        state._delta_ops.clear()
        # Re-submitting the same payload must NOT emit a delta.
        self.assertFalse(state.set_relay_connection(dict(payload)))
        self.assertEqual(
            [op for op in state._delta_ops if op["op"] == "relay_connection"],
            [],
        )
        # A genuine change emits exactly one.
        changed = dict(payload, status="disconnected")
        self.assertTrue(state.set_relay_connection(changed))
        ops = [op for op in state._delta_ops if op["op"] == "relay_connection"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["status"], "disconnected")

    def test_partial_payload_merges_over_defaults(self):
        state = self._state()
        state.set_relay_connection({"status": "error", "last_error": "boom"})
        rc = state.relay_connection
        self.assertEqual(rc["status"], "error")
        self.assertEqual(rc["last_error"], "boom")
        # Unspecified keys fall back to defaults rather than disappearing.
        self.assertEqual(rc["retry_count"], 0)
        self.assertEqual(rc["relay_host"], "")
        self.assertIn("enabled", rc)


class TaskUpsertObserverTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_register_unregister_and_exception_isolated(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        seen = []

        def observer(payload):
            seen.append(payload["id"])

        def exploding(_payload):
            raise RuntimeError("observer boom")

        unregister = state.register_task_upsert_observer(observer)
        state.register_task_upsert_observer(exploding)

        task = state.board_add_task("Observed", "g", id="task-observed")

        self.assertIn(task.id, seen)
        count_before_unregister = len(seen)
        self.assertEqual(state._delta_ops[-1]["op"], "task_upsert")
        unregister()
        state.board_update_task(task.id, task="Renamed")

        self.assertEqual(len(seen), count_before_unregister)


class AgentGroupOrderPersistenceTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_move_agent_architect_order_survives_database_reload(self):
        from torque.db import TorqueDB

        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            db.init()
            try:
                state = self.state_mod.MatrixState(db=db)
                state.groups["g"] = []
                for aid in ("arch-a", "worker", "arch-b", "arch-c"):
                    cell = self.state_mod.AgentCell(
                        id=aid,
                        name=aid,
                        group="g",
                        kind="worker" if aid == "worker" else "architect",
                        cell_type="agent",
                    )
                    state.agents[aid] = cell
                    state.groups["g"].append(aid)
                    state._db_save_agent(cell)
                state._db_save_groups()

                state.move_agent("arch-c", "g", before="arch-a")
                self.assertEqual(
                    state.groups["g"],
                    ["arch-c", "arch-a", "worker", "arch-b"],
                )

                reloaded = self.state_mod.MatrixState(db=db)
                reloaded.load()
                self.assertEqual(
                    reloaded.groups["g"],
                    ["arch-c", "arch-a", "worker", "arch-b"],
                )
            finally:
                db.close()
