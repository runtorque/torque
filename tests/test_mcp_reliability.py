import asyncio
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub(include_json_helpers=True)

from torque.db import TorqueDB
from torque.doctor import (
    build_doctor_report,
    format_doctor_report,
    format_mcp_health_report,
)
from torque.mcp_idempotency import (
    MCP_IDEMPOTENCY_FULL_TTL_SECONDS,
    MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
)
from torque.mcp_retry import (
    IDEMPOTENCY_ARG,
    api_request_hash,
    ensure_mcp_payload_idempotency,
    is_api_write_command,
    is_mcp_write_tool,
    is_mcp_pr_phase_retryable,
    replay_failed_writes,
    retry_async,
)
from torque.state import AgentCell, MatrixState


class MCPRetryHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_async_survives_three_transient_timeouts_then_succeeds(self):
        attempts = []
        retries = []
        sleeps = []

        async def operation(attempt):
            attempts.append(attempt)
            if attempt < 4:
                raise asyncio.TimeoutError("fake timeout")
            return {"ok": True}

        async def sleep(delay):
            sleeps.append(delay)

        async def on_retry(attempt, exc):
            retries.append((attempt, type(exc).__name__))

        result = await retry_async(
            operation,
            attempts=4,
            sleep=sleep,
            on_retry=on_retry,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(attempts, [1, 2, 3, 4])
        self.assertEqual(retries, [(1, "TimeoutError"), (2, "TimeoutError"), (3, "TimeoutError")])
        self.assertEqual(sleeps, [0.5, 1.5, 3.0])

    def test_pr_phase_retry_classification(self):
        transient_error = "network timeout while contacting GitHub"
        expected = {
            "github_preflight": False,
            "github_remote": True,
            "remote_base_sync": True,
            "push_branch": True,
            "pr_create": True,
            "pr_merge": True,
            "nested_submodule_pr_preflight": False,
            "nested_submodule_pr_push": True,
            "nested_submodule_pr_create": True,
            "nested_submodule_pr_merge": True,
            "nested_submodule_pr_sync": True,
            "nested_submodule_gitlink_bump": False,
        }
        for phase, retryable in expected.items():
            with self.subTest(phase=phase):
                self.assertEqual(
                    is_mcp_pr_phase_retryable(phase, transient_error),
                    retryable,
                )

        self.assertFalse(
            is_mcp_pr_phase_retryable(
                "pr_merge",
                "GitHub reported a merge conflict.",
            )
        )
        self.assertFalse(
            is_mcp_pr_phase_retryable(
                "remote_base_sync",
                "Local main cannot be fast-forwarded; resolve divergence.",
            )
        )

    def test_architect_task_update_is_scoped_write_tool(self):
        self.assertTrue(is_mcp_write_tool("architect_task_update"))
        self.assertFalse(is_mcp_write_tool("architect_task_list"))
        self.assertFalse(is_api_write_command("architect_task_list"))

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "architect_task_update",
                "arguments": {
                    "task": "TORQUE:217",
                    "title": "Updated title",
                },
            },
        }

        updated, idempotency_key, tool_name = ensure_mcp_payload_idempotency(
            payload,
        )

        self.assertEqual(tool_name, "architect_task_update")
        self.assertTrue(idempotency_key)
        self.assertEqual(
            updated["params"]["arguments"][IDEMPOTENCY_ARG],
            idempotency_key,
        )

    def test_ai_settings_api_command_read_write_classification(self):
        self.assertFalse(is_api_write_command("get_ai_settings"))
        self.assertTrue(is_api_write_command("update_ai_settings"))

    def test_all_registered_write_tools_classify_independent_of_lazy_partition(self):
        mcp_mod = importlib.reload(importlib.import_module("torque.mcp"))
        write_tools = {
            "torque_task_upload_artifact",
            "torque_done",
            "torque_blocked",
            "torque_error",
            "torque_progress",
            "torque_verify",
            "torque_ready",
            "torque_name",
            "torque_derive",
            "torque_ask",
            "torque_message_user",
            "torque_reply",
            "torque_memory_publish",
            "torque_memory_pin",
            "torque_memory_link",
            "torque_memory_unpin",
            "engineer_launch_settings",
            "engineer_notifications",
            "engineer_resume",
            "engineer_task_create",
            "engineer_task_edit",
            "engineer_task_upload_artifact",
            "engineer_task_mark_covered",
            "engineer_task_verify",
            "engineer_task_move",
            "engineer_task_dispatch",
            "engineer_batch_dispatch",
            "engineer_task_resolve",
            "engineer_hint_snooze",
            "engineer_journal",
            "engineer_agent_message",
            "engineer_ask",
            "engineer_message_user",
            "engineer_note",
            "engineer_agent_close",
            "engineer_agent_relaunch",
            "engineer_merge",
            "engineer_rebase",
            "engineer_create_pr",
            "engineer_worktree_remove",
            "engineer_worktree_checkpoint",
            "engineer_specialization_save",
            "engineer_specialization_delete",
            "engineer_task_reassign",
            "engineer_message_architect",
            "engineer_reply",
            "architect_engineer_hire",
            "architect_engineer_set_specializations",
            "architect_engineer_dismiss",
            "architect_engineer_rehire",
            "architect_task_create",
            "architect_task_pickup",
            "architect_task_update",
            "architect_task_reassign",
            "architect_task_move",
            "architect_task_mark_covered",
            "architect_ask",
            "architect_message_user",
            "architect_engineer_message",
            "architect_peer_message",
            "architect_reply",
            "architect_decision_create",
            "architect_decision_update",
            "architect_decision_link",
            "architect_area_create",
            "architect_area_update",
            "architect_area_archive",
            "architect_area_link_task",
            "architect_area_unlink_task",
            "architect_area_link_decision",
            "architect_area_unlink_decision",
            "architect_area_link_initiative",
            "architect_area_unlink_initiative",
            "architect_area_link_area",
            "architect_area_unlink_area",
            "architect_area_note_create",
            "architect_area_note_update",
            "architect_area_note_archive",
            "architect_initiative_create",
            "architect_initiative_update",
            "architect_initiative_archive",
            "architect_initiative_link_task",
            "architect_initiative_unlink_task",
            "architect_initiative_link_decision",
            "architect_initiative_unlink_decision",
            "architect_journal",
        }
        registered_names = {tool["name"] for tool in mcp_mod.ALL_TOOLS}

        self.assertTrue(write_tools <= registered_names)
        for tool_name in write_tools:
            self.assertTrue(is_mcp_write_tool(tool_name), tool_name)
        self.assertFalse(is_mcp_write_tool("architect_initiative_list"))
        self.assertFalse(is_mcp_write_tool("architect_initiative_show"))
        self.assertFalse(is_mcp_write_tool("engineer_initiative_list"))
        self.assertFalse(is_mcp_write_tool("engineer_initiative_show"))
        self.assertFalse(is_mcp_write_tool("architect_area_list"))
        self.assertFalse(is_mcp_write_tool("architect_area_show"))
        self.assertFalse(is_mcp_write_tool("engineer_area_list"))
        self.assertFalse(is_mcp_write_tool("engineer_area_show"))
        self.assertFalse(is_mcp_write_tool("torque_area_list"))
        self.assertFalse(is_mcp_write_tool("torque_area_show"))

        lazy_names = {
            tool["name"]
            for tool in mcp_mod.ENGINEER_TOOLS + mcp_mod.ARCHITECT_TOOLS
            if tool.get("deferred")
        }
        lazy_write_tools = write_tools & lazy_names
        self.assertEqual(
            lazy_write_tools,
            {
                "engineer_launch_settings",
                "engineer_task_reassign",
                "engineer_specialization_save",
                "engineer_specialization_delete",
                "architect_engineer_dismiss",
                "architect_engineer_rehire",
            },
        )
        for tool_name in lazy_write_tools:
            self.assertTrue(is_mcp_write_tool(tool_name), tool_name)


class MCPIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.agents["agent-1"] = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        self.mcp = importlib.import_module("torque.mcp")
        self.mcp = importlib.reload(self.mcp)
        self.state_mod = importlib.import_module("torque.state")

    async def test_duplicate_mcp_write_with_same_key_does_not_run_twice(self):
        calls = []

        async def handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok", "count": len(calls)}

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "torque_progress",
                "arguments": {
                    "message": "still running",
                    IDEMPOTENCY_ARG: "idem-1",
                },
            },
        }

        first, status = await self.mcp.dispatch_mcp_rpc_body(
            body,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(first["result"]["isError"])
        self.assertEqual(json.loads(first["result"]["content"][0]["text"])["count"], 1)

        retry_body = dict(body)
        retry_body["id"] = 2
        second, status = await self.mcp.dispatch_mcp_rpc_body(
            retry_body,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )

        self.assertEqual(status, 200)
        self.assertEqual(second["id"], 2)
        self.assertEqual(json.loads(second["result"]["content"][0]["text"])["count"], 1)
        self.assertEqual(len(calls), 1)
        health = self.db.load_mcp_health_summary(since=0)
        self.assertEqual(health["totals"].get("dedupe"), 1)

    async def test_torque_message_user_retry_does_not_duplicate_row(self):
        async def handle_command(_payload):
            return {"type": "ok"}

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "torque_message_user",
                "arguments": {
                    "message": "This should be saved once.",
                    IDEMPOTENCY_ARG: "direct-user-idem-1",
                },
            },
        }

        first, status = await self.mcp.dispatch_mcp_rpc_body(
            body,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(first["result"]["isError"])
        first_payload = json.loads(first["result"]["content"][0]["text"])

        retry = dict(body)
        retry["id"] = 2
        second, status = await self.mcp.dispatch_mcp_rpc_body(
            retry,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(second["result"]["isError"])
        second_payload = json.loads(second["result"]["content"][0]["text"])
        self.assertEqual(second_payload["message_id"], first_payload["message_id"])
        self.assertEqual(
            self.db._conn.execute(
                "SELECT COUNT(*) FROM agent_peer_messages "
                "WHERE sender_kind='worker' AND recipient_kind='user'"
            ).fetchone()[0],
            1,
        )

    async def test_engineer_merge_pr_phase_errors_cache_only_non_retryable(self):
        engineer = AgentCell(
            id="eng-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        worker = AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
            created_by_engineer_id=engineer.id,
            worktree_path="/tmp/worker",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
        )
        self.state.groups["g"] = [engineer.id, worker.id]
        self.state.agents[engineer.id] = engineer
        self.state.agents[worker.id] = worker

        phases = {
            "github_preflight": False,
            "github_remote": True,
            "remote_base_sync": True,
            "push_branch": True,
            "pr_create": True,
            "pr_merge": True,
            "nested_submodule_pr_preflight": False,
            "nested_submodule_pr_create": True,
            "nested_submodule_pr_merge": True,
            "nested_submodule_gitlink_bump": False,
        }
        for phase, retryable in phases.items():
            calls = []

            async def handle_command(payload, *, phase=phase):
                calls.append(dict(payload))
                if payload["cmd"] == "worktree_check_merge":
                    return {
                        "type": "worktree_check_merge",
                        "id": worker.id,
                        "clean": True,
                        "conflicts": [],
                    }
                if payload["cmd"] == "worktree_merge":
                    return {
                        "type": "worktree_merge",
                        "id": worker.id,
                        "ok": False,
                        "mode": "pull_request",
                        "phase": phase,
                        "pr_url": "https://github.com/acme/repo/pull/7",
                        "error": f"network timeout during {phase}",
                    }
                raise AssertionError(f"unexpected payload: {payload}")

            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "engineer_merge",
                    "arguments": {
                        "agent": worker.id,
                        IDEMPOTENCY_ARG: f"pr-phase-{phase}",
                    },
                },
            }
            first, status = await self.mcp.dispatch_mcp_rpc_body(
                body,
                cell_id=engineer.id,
                handle_command=handle_command,
                state=self.state,
            )
            self.assertEqual(status, 200)
            self.assertTrue(first["result"]["isError"])
            first_text = first["result"]["content"][0]["text"]
            self.assertIn(f"phase={phase}", first_text)
            self.assertIn(f"retryable={'true' if retryable else 'false'}", first_text)

            retry_body = json.loads(json.dumps(body))
            retry_body["id"] = 2
            second, status = await self.mcp.dispatch_mcp_rpc_body(
                retry_body,
                cell_id=engineer.id,
                handle_command=handle_command,
                state=self.state,
            )
            self.assertEqual(status, 200)
            self.assertTrue(second["result"]["isError"])
            merge_calls = [
                call for call in calls
                if call.get("cmd") == "worktree_merge"
            ]
            if retryable:
                self.assertEqual(len(merge_calls), 2, phase)
                self.assertIsNone(
                    self.db.load_mcp_idempotency(f"pr-phase-{phase}"),
                    phase,
                )
            else:
                self.assertEqual(len(merge_calls), 1, phase)
                self.assertIsNotNone(
                    self.db.load_mcp_idempotency(f"pr-phase-{phase}"),
                    phase,
                )

    async def test_architect_peer_message_retry_does_not_duplicate_row(self):
        arch_a = AgentCell(
            id="arch-a",
            name="Architect A",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        arch_b = AgentCell(
            id="arch-b",
            name="Architect B",
            group="g",
            cell_type="agent",
            kind="architect",
            session_id="session-b",
        )
        self.state.groups["g"] = [arch_a.id, arch_b.id]
        self.state.agents[arch_a.id] = arch_a
        self.state.agents[arch_b.id] = arch_b
        self.db.save_groups_and_members(self.state.groups, {})
        self.db.save_agent(arch_a)
        self.db.save_agent(arch_b)
        calls = []

        async def handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok", "delivered": True}

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "architect_peer_message",
                "arguments": {
                    "architect_id": arch_b.id,
                    "message": "Please review this boundary.",
                    "ack_required": True,
                    IDEMPOTENCY_ARG: "peer-idem-1",
                },
            },
        }

        first, status = await self.mcp.dispatch_mcp_rpc_body(
            body,
            cell_id=arch_a.id,
            handle_command=handle_command,
            state=self.state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(first["result"]["isError"])
        first_payload = json.loads(first["result"]["content"][0]["text"])

        retry = dict(body)
        retry["id"] = 2
        second, status = await self.mcp.dispatch_mcp_rpc_body(
            retry,
            cell_id=arch_a.id,
            handle_command=handle_command,
            state=self.state,
        )

        self.assertEqual(status, 200)
        second_payload = json.loads(second["result"]["content"][0]["text"])
        self.assertEqual(second_payload["message_id"], first_payload["message_id"])
        self.assertEqual(
            self.db._conn.execute(
                "SELECT COUNT(*) FROM agent_peer_messages"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["inject_mcp_message"],
        )

    async def test_reusing_idempotency_key_for_different_write_is_rejected(self):
        async def handle_command(_payload):
            return {"type": "ok"}

        base = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "torque_progress",
                "arguments": {
                    "message": "first",
                    IDEMPOTENCY_ARG: "idem-conflict",
                },
            },
        }
        await self.mcp.dispatch_mcp_rpc_body(
            base,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        changed = json.loads(json.dumps(base))
        changed["id"] = 2
        changed["params"]["arguments"]["message"] = "different"
        response, _status = await self.mcp.dispatch_mcp_rpc_body(
            changed,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertIn("error", response)
        self.assertIn("Idempotency key", response["error"]["message"])

    async def test_oversize_idempotency_receipt_replays_safe_compacted_error(self):
        calls = []

        async def handle_command(_payload):
            calls.append(1)
            return {"type": "ok", "blob": "x" * (MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES + 1024)}

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "torque_progress",
                "arguments": {
                    "message": "large",
                    IDEMPOTENCY_ARG: "idem-large",
                },
            },
        }
        first, status = await self.mcp.dispatch_mcp_rpc_body(
            body,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(first["result"]["isError"])

        stored = self.db.load_mcp_idempotency("idem-large")
        self.assertIsNotNone(stored)
        self.assertGreater(float(stored.get("compacted_at", 0) or 0), 0)
        self.assertLess(
            len((stored.get("response_json") or "").encode("utf-8")),
            MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
        )

        retry = json.loads(json.dumps(body))
        retry["id"] = 2
        second, status = await self.mcp.dispatch_mcp_rpc_body(
            retry,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertEqual(status, 200)
        self.assertTrue(second["result"]["isError"])
        self.assertIn("Exact replay is unavailable", second["result"]["content"][0]["text"])
        self.assertEqual(len(calls), 1)

    async def test_compacted_idempotency_receipt_still_rejects_conflicting_hash(self):
        async def handle_command(_payload):
            return {"type": "ok", "blob": "x" * (MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES + 1024)}

        base = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "torque_progress",
                "arguments": {
                    "message": "first",
                    IDEMPOTENCY_ARG: "idem-large-conflict",
                },
            },
        }
        await self.mcp.dispatch_mcp_rpc_body(
            base,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertGreater(
            float(self.db.load_mcp_idempotency("idem-large-conflict").get("compacted_at", 0) or 0),
            0,
        )
        changed = json.loads(json.dumps(base))
        changed["id"] = 2
        changed["params"]["arguments"]["message"] = "different"
        response, _status = await self.mcp.dispatch_mcp_rpc_body(
            changed,
            cell_id="agent-1",
            handle_command=handle_command,
            state=self.state,
        )
        self.assertIn("error", response)
        self.assertIn("Idempotency key", response["error"]["message"])

    def test_expired_idempotency_receipts_compact_in_bounded_batches(self):
        for idx in range(3):
            self.db.save_mcp_idempotency(
                idempotency_key=f"idem-old-{idx}",
                surface="torque",
                tool_name="torque_progress",
                request_hash=f"hash-{idx}",
                response={"content": [{"type": "text", "text": str(idx)}], "isError": False},
            )
        old_updated = 1000.0
        self.db._conn.execute(
            "UPDATE mcp_idempotency SET updated_at=?, expires_at=?",
            (old_updated, old_updated + MCP_IDEMPOTENCY_FULL_TTL_SECONDS),
        )
        self.db._conn.commit()

        summary = self.db.maintain_mcp_idempotency(
            now=old_updated + MCP_IDEMPOTENCY_FULL_TTL_SECONDS + 10,
            batch_size=2,
        )
        self.assertEqual(summary["compacted"], 2)
        self.assertEqual(
            self.db._conn.execute(
                "SELECT COUNT(*) FROM mcp_idempotency WHERE compacted_at > 0"
            ).fetchone()[0],
            2,
        )
        kept = self.db.load_mcp_idempotency("idem-old-0")
        self.assertEqual(kept["request_hash"], "hash-0")
        cached = json.loads(kept["response_json"])
        self.assertTrue(cached["isError"])
        self.assertIn("idempotency_receipt", cached)

    def test_mcp_idempotency_schema_defaults_and_storage_stats(self):
        columns = {
            row[1]: row for row in self.db._conn.execute("PRAGMA table_info(mcp_idempotency)")
        }
        for column in ("response_bytes", "expires_at", "compacted_at"):
            self.assertIn(column, columns)

        self.db.save_mcp_idempotency(
            idempotency_key="idem-schema",
            surface="torque",
            tool_name="torque_progress",
            request_hash="hash",
            response={"content": [{"type": "text", "text": "ok"}], "isError": False},
        )
        stored = self.db.load_mcp_idempotency("idem-schema")
        self.assertGreater(int(stored["response_bytes"]), 0)
        self.assertGreater(float(stored["expires_at"]), float(stored["updated_at"]))
        self.assertEqual(float(stored["compacted_at"]), 0.0)
        stats = self.db.mcp_idempotency_storage_stats()
        self.assertEqual(stats["row_count"], 1)
        self.assertGreater(stats["response_bytes"], 0)

    def test_legacy_mcp_idempotency_schema_migrates_before_retention_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.executescript(
                    """
                    CREATE TABLE mcp_idempotency (
                        idempotency_key TEXT PRIMARY KEY,
                        surface         TEXT NOT NULL DEFAULT '',
                        tool_name       TEXT NOT NULL DEFAULT '',
                        request_hash    TEXT NOT NULL DEFAULT '',
                        response_json   TEXT NOT NULL DEFAULT '{}',
                        created_at      REAL NOT NULL,
                        updated_at      REAL NOT NULL
                    );
                    CREATE INDEX idx_mcp_idempotency_tool
                        ON mcp_idempotency(surface, tool_name, updated_at DESC);
                    """
                )
                conn.commit()
            finally:
                conn.close()

            migrated = TorqueDB(db_path)
            try:
                migrated.init()
                columns = {
                    row[1]
                    for row in migrated._conn.execute(
                        "PRAGMA table_info(mcp_idempotency)"
                    )
                }
                for column in ("response_bytes", "expires_at", "compacted_at"):
                    self.assertIn(column, columns)
                indexes = {
                    row[1]
                    for row in migrated._conn.execute(
                        "PRAGMA index_list(mcp_idempotency)"
                    )
                }
                self.assertIn("idx_mcp_idempotency_retention", indexes)
            finally:
                migrated.close()

    async def test_architect_journal_retry_after_outer_receipt_loss_reuses_internal_record(self):
        architect = AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        self.state.agents[architect.id] = architect

        async def handle_command(payload):
            if payload.get("cmd") != "architect_journal_append":
                raise AssertionError(f"unexpected payload: {payload}")
            return self.state.architect_journal_append(
                payload.get("architect_id", ""),
                payload.get("entry_type", ""),
                payload.get("entry", ""),
                idempotency_key=payload.get("idempotency_key", ""),
                request_hash=api_request_hash(payload),
            )

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "architect_journal",
                "arguments": {
                    "type": "plan",
                    "entry": "Durably checkpoint this architect note.",
                    IDEMPOTENCY_ARG: "journal-outer-idem",
                },
            },
        }

        with mock.patch.object(self.state_mod, "DATA_DIR", Path(self.tmp.name)):
            first, status = await self.mcp.dispatch_mcp_rpc_body(
                body,
                cell_id=architect.id,
                handle_command=handle_command,
                state=self.state,
            )
            self.assertEqual(status, 200)
            first_record = json.loads(first["result"]["content"][0]["text"])
            self.assertEqual(first_record["type"], "plan")

            self.db._conn.execute(
                "DELETE FROM mcp_idempotency WHERE idempotency_key=?",
                ("journal-outer-idem",),
            )
            self.db._conn.commit()

            retry_body = json.loads(json.dumps(body))
            retry_body["id"] = 2
            second, status = await self.mcp.dispatch_mcp_rpc_body(
                retry_body,
                cell_id=architect.id,
                handle_command=handle_command,
                state=self.state,
            )

            self.assertEqual(status, 200)
            second_record = json.loads(second["result"]["content"][0]["text"])
            self.assertEqual(second_record["id"], first_record["id"])
            entries = self.state.architect_journal_read(architect.id, limit=10)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["entry"], first_record["entry"])


class MCPFailedWriteReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)

    async def test_failed_write_replay_deletes_successful_queue_entry(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "torque_progress",
                "arguments": {"message": "queued", IDEMPOTENCY_ARG: "idem-queued"},
            },
        }
        self.db.enqueue_failed_write(
            idempotency_key="idem-queued",
            endpoint="/mcp",
            surface="torque",
            tool_name="torque_progress",
            caller_id="agent-1",
            payload=payload,
            attempts=4,
            last_error="fake timeout",
        )
        seen = []

        async def sender(write):
            seen.append(write["payload"]["params"]["name"])
            return {"ok": True}

        summary = await replay_failed_writes(self.db, sender)

        self.assertEqual(summary, {"attempted": 1, "replayed": 1, "failed": 0})
        self.assertEqual(seen, ["torque_progress"])
        self.assertEqual(self.db.load_failed_writes(), [])
        health = self.db.load_mcp_health_summary(since=0)
        self.assertEqual(health["totals"].get("replay"), 1)

    async def test_api_failed_write_replay_dedupes_existing_idempotency_row(self):
        from torque.server import replay_api_failed_write_payload

        payload = {
            "cmd": "board_add_task",
            "title": "Queued once",
            "idempotency_key": "api-queued-idem",
        }
        cached_response = {
            "ok": True,
            "data": {"type": "board_task_added", "task_id": "TORQUE:1"},
        }
        self.db.save_mcp_idempotency(
            idempotency_key="api-queued-idem",
            surface="api",
            tool_name="board_add_task",
            request_hash=api_request_hash(payload),
            response=cached_response,
        )
        self.db.enqueue_failed_write(
            idempotency_key="api-queued-idem",
            endpoint="/api/cmd",
            surface="api",
            tool_name="board_add_task",
            payload=payload,
            attempts=4,
            last_error="fake timeout after commit",
        )
        calls = []
        results = []

        async def handle_command(unexpected_payload):
            calls.append(dict(unexpected_payload))
            raise AssertionError("queued API replay should have been deduped")

        async def sender(write):
            result = await replay_api_failed_write_payload(
                self.db,
                write["payload"],
                handle_command,
            )
            results.append(result)
            return result

        summary = await replay_failed_writes(self.db, sender)

        self.assertEqual(summary, {"attempted": 1, "replayed": 1, "failed": 0})
        self.assertEqual(results, [cached_response])
        self.assertEqual(calls, [])
        self.assertEqual(self.db.load_failed_writes(), [])
        health = self.db.load_mcp_health_summary(since=0)
        self.assertEqual(health["totals"].get("dedupe"), 1)

    async def test_internal_failed_write_replay_dedupes_existing_command_receipt(self):
        from torque.server import (
            _internal_failed_write_key,
            replay_internal_failed_write_payload,
        )

        payload = {
            "cmd": "ai_report",
            "cell_id": "agent-1",
            "action": "done",
            "message": "All set.",
            "idempotency_key": "internal-ai-idem",
        }
        cached_response = {"type": "ok", "message": "All set."}
        self.db.save_command_receipt(
            idempotency_key="internal-ai-idem",
            surface="internal",
            command_name="ai_report:done",
            request_hash=api_request_hash(payload),
            response=cached_response,
        )
        self.db.enqueue_failed_write(
            idempotency_key=_internal_failed_write_key("internal-ai-idem"),
            endpoint="/internal/cmd",
            surface="internal",
            tool_name="ai_report:done",
            caller_id="agent-1",
            payload=payload,
            attempts=2,
            last_error="daemon exited before reply",
        )
        calls = []
        results = []

        async def handle_command(unexpected_payload):
            calls.append(dict(unexpected_payload))
            raise AssertionError("internal replay should have been deduped")

        async def sender(write):
            result = await replay_internal_failed_write_payload(
                self.db,
                write["payload"],
                handle_command,
            )
            results.append(result)
            return result

        summary = await replay_failed_writes(self.db, sender)

        self.assertEqual(summary, {"attempted": 1, "replayed": 1, "failed": 0})
        self.assertEqual(results, [cached_response])
        self.assertEqual(calls, [])
        self.assertEqual(self.db.load_failed_writes(), [])

    async def test_api_replay_does_not_cache_deliverable_missing_refusal(self):
        """A queued /api/cmd `ai_report` write that replays while the
        deliverable is still missing must NOT be persisted as a successful
        idempotency response. Same masking-failure mode as the round-1
        bug, just on the durable failed-write replay path.

        After the fix, the replay returns an error envelope and the
        idempotency table stays empty so that a same-key retry — after
        the worker uploads an artifact — re-runs the gate cleanly
        instead of replaying the cached refusal.
        """
        from torque.server import replay_api_failed_write_payload

        payload = {
            "cmd": "ai_report",
            "cell_id": "agent-1",
            "action": "done",
            "task_id": "TASK:1",
            "message": "Done!",
            "idempotency_key": "api-deliv-idem",
        }

        async def handle_command(_payload):
            return {
                "type": "deliverable_missing",
                "message": (
                    "Cannot mark task done: deliverable required "
                    "(type=report) but no matching artifact attached. "
                    "Call `torque_task_upload_artifact(...)` first, "
                    "then retry torque_done."
                ),
            }

        result = await replay_api_failed_write_payload(
            self.db,
            payload,
            handle_command,
        )

        # Replay surfaces the refusal as a structured error envelope —
        # NOT as a successful {ok:true, data:{type:"deliverable_missing"}}.
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("type"), "deliverable_missing")
        self.assertIn("deliverable required", result.get("error", ""))

        # And critically: NO idempotency row was saved, so a same-key
        # retry after the worker uploads will re-run the gate.
        cached = self.db.load_mcp_idempotency("api-deliv-idem")
        self.assertIsNone(cached)

    async def test_internal_replay_does_not_cache_deliverable_missing_refusal(self):
        """The internal-command replay path (used for ai_report critical
        writes) must not cache a deliverable_missing refusal as a command
        receipt either. The normal `handle_command` codepath skips the
        receipt write for this result type; the replay wrapper surfaces
        it as a semantic failure envelope so the caller does not treat
        the refusal as completion.
        """
        from torque.server import replay_internal_failed_write_payload

        payload = {
            "cmd": "ai_report",
            "cell_id": "agent-1",
            "action": "ready",
            "task_id": "TASK:1",
            "idempotency_key": "internal-deliv-idem",
        }

        async def handle_command(_payload):
            return {
                "type": "deliverable_missing",
                "message": (
                    "Cannot mark task ready: deliverable required "
                    "(type=report) but no matching artifact attached."
                ),
            }

        result = await replay_internal_failed_write_payload(
            self.db,
            payload,
            handle_command,
        )

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("type"), "deliverable_missing")
        self.assertIn("deliverable required", result.get("error", ""))

        # The replay wrapper should not have written a command receipt
        # for the refusal — confirm the receipt table stays empty so a
        # retry after artifact upload re-runs the gate.
        receipt = self.db.load_command_receipt("internal-deliv-idem")
        self.assertIsNone(receipt)

    def test_architect_journal_replay_recovers_existing_jsonl_entry_without_duplicate(self):
        state = MatrixState(db=self.db)
        state_mod = importlib.import_module("torque.state")

        with mock.patch.object(state_mod, "DATA_DIR", Path(self.tmp.name)):
            first = state.architect_journal_append(
                "arch-1",
                "checkpoint",
                "Remember this exact checkpoint.",
                idempotency_key="journal-recover-idem",
                request_hash="journal-recover-hash",
            )
            self.db._conn.execute(
                "DELETE FROM command_receipts WHERE idempotency_key=?",
                ("journal-recover-idem",),
            )
            self.db._conn.commit()

            replayed = state.architect_journal_append(
                "arch-1",
                "checkpoint",
                "Remember this exact checkpoint.",
                idempotency_key="journal-recover-idem",
                request_hash="journal-recover-hash",
            )

            self.assertEqual(replayed["id"], first["id"])
            entries = state.architect_journal_read("arch-1", limit=10)
            self.assertEqual(len(entries), 1)
            receipt = self.db.load_command_receipt("journal-recover-idem")
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["response"]["id"], first["id"])

    def test_doctor_mcp_health_reports_recent_retry_drop_counts(self):
        self.db.record_mcp_health_event(
            surface="torque",
            tool_name="torque_progress",
            event="retry",
            error="timeout",
        )
        self.db.record_mcp_health_event(
            surface="architect",
            tool_name="architect_journal",
            event="drop",
            error="EADDRNOTAVAIL",
        )
        self.db.enqueue_failed_write(
            idempotency_key="idem-pending",
            endpoint="/mcp",
            surface="architect",
            tool_name="architect_journal",
            caller_id="arch-1",
            payload={"method": "tools/call"},
            attempts=4,
            last_error="EADDRNOTAVAIL",
        )

        report = build_doctor_report(self.db._conn, self.db.db_path)
        health = report["mcp_health"]
        rendered = format_mcp_health_report(report)

        self.assertEqual(health["totals"].get("retry"), 1)
        self.assertEqual(health["totals"].get("drop"), 1)
        self.assertEqual(health["pending_failed_writes"], 1)
        self.assertIn("Torque MCP health", rendered)
        self.assertIn("retries=1", rendered)
        self.assertIn("drops=1", rendered)
        self.assertIn("architect_journal", rendered)

    def test_doctor_warns_on_mcp_idempotency_storage_bloat_without_dbstat(self):
        self.db.save_mcp_idempotency(
            idempotency_key="idem-doctor-large",
            surface="torque",
            tool_name="torque_progress",
            request_hash="hash",
            response={"content": [{"type": "text", "text": "x" * (2 * 1024 * 1024)}], "isError": False},
        )
        # Simulate an old/large DB row that predates write-time compaction so
        # doctor can exercise max-receipt warning paths.
        self.db._conn.execute(
            "UPDATE mcp_idempotency SET response_json=?, response_bytes=? WHERE idempotency_key=?",
            ("x" * (2 * 1024 * 1024), 2 * 1024 * 1024, "idem-doctor-large"),
        )
        self.db._conn.commit()

        class NoDbstatProxy:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, params=()):
                if "dbstat" in str(sql).lower():
                    raise __import__("sqlite3").OperationalError("no such table: dbstat")
                return self._conn.execute(sql, params)

        report = build_doctor_report(NoDbstatProxy(self.db._conn), self.db.db_path)
        storage = report["mcp_idempotency_storage"]
        rendered = format_doctor_report(report)

        self.assertFalse(storage["dbstat_available"])
        self.assertIn("max_response_bytes", storage["warnings"])
        self.assertIn("mcp_idempotency_storage_bloat", [w["name"] for w in report["warnings"]])
        self.assertIn("dbstat unavailable", rendered)


class CriticalWriteCaptureIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.add_group("g")
        self.state.agents["agent-1"] = AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
        )
        self.state.agents["agent-2"] = AgentCell(
            id="agent-2",
            name="Beta",
            group="g",
            cell_type="agent",
        )

    async def test_overlapping_critical_receipts_are_isolated_per_task(self):
        from torque.server import _internal_failed_write_key

        started = asyncio.Event()
        release = asyncio.Event()

        async def critical_write(agent_id: str, *, idem_key: str, new_name: str,
                                 hold_open: bool = False):
            payload = {
                "cmd": "ai_report",
                "cell_id": agent_id,
                "action": "name",
                "message": new_name,
                "idempotency_key": idem_key,
            }
            failed_write_key = _internal_failed_write_key(idem_key)
            self.db.enqueue_failed_write(
                idempotency_key=failed_write_key,
                endpoint="/internal/cmd",
                surface="internal",
                tool_name="ai_report:name",
                caller_id=agent_id,
                payload=payload,
                attempts=0,
                last_error="pending",
            )
            capture_active = False
            try:
                self.state.begin_critical_write_capture(
                    command_name="ai_report:name",
                    idempotency_key=idem_key,
                    request_hash=api_request_hash(payload),
                )
                capture_active = True
                cell = self.state.agents[agent_id]
                cell.name = new_name
                self.state._db_save_agent(cell)
                if hold_open:
                    started.set()
                    await release.wait()
                self.state.finalize_critical_write_capture(
                    {"type": "ok", "agent_id": agent_id, "name": new_name},
                    delete_failed_write_key=failed_write_key,
                    surface="internal",
                )
                capture_active = False
            finally:
                if capture_active:
                    self.state.clear_critical_write_capture()

        async def first_write():
            await critical_write(
                "agent-1",
                idem_key="idem-alpha",
                new_name="Alpha renamed",
                hold_open=True,
            )

        async def second_write():
            await started.wait()
            try:
                await critical_write(
                    "agent-2",
                    idem_key="idem-beta",
                    new_name="Beta renamed",
                )
            finally:
                release.set()

        await asyncio.gather(
            asyncio.create_task(first_write()),
            asyncio.create_task(second_write()),
        )

        receipt_alpha = self.db.load_command_receipt("idem-alpha")
        receipt_beta = self.db.load_command_receipt("idem-beta")
        self.assertEqual(receipt_alpha["response"]["name"], "Alpha renamed")
        self.assertEqual(receipt_beta["response"]["name"], "Beta renamed")
        self.assertEqual(self.db.load_failed_writes(), [])
        self.assertEqual(
            self.db._conn.execute(
                "SELECT name FROM agents WHERE id=?",
                ("agent-1",),
            ).fetchone()[0],
            "Alpha renamed",
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT name FROM agents WHERE id=?",
                ("agent-2",),
            ).fetchone()[0],
            "Beta renamed",
        )

    async def test_unrelated_task_write_does_not_fall_into_other_task_capture(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_capture_open():
            capture_active = False
            try:
                self.state.begin_critical_write_capture(
                    command_name="ai_report:done",
                    idempotency_key="idem-held-open",
                    request_hash="request-held-open",
                )
                capture_active = True
                started.set()
                await release.wait()
            finally:
                if capture_active:
                    self.state.clear_critical_write_capture()

        async def unrelated_task_mutation():
            await started.wait()
            created = self.state.board_add_task("Outside capture", "g")
            await self.state.flush_db_writes()
            release.set()
            return created

        hold_task = asyncio.create_task(hold_capture_open())
        mutate_task = asyncio.create_task(unrelated_task_mutation())
        created = await mutate_task
        await hold_task

        self.assertIsNotNone(created)
        persisted = self.db._conn.execute(
            "SELECT id FROM board_tasks WHERE id=?",
            (created.id,),
        ).fetchone()
        self.assertIsNotNone(persisted)
        self.assertIsNone(self.db.load_command_receipt("idem-held-open"))


if __name__ == "__main__":
    unittest.main()
