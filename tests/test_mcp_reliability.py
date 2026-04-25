import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub(include_json_helpers=True)

from loom.db import LoomDB
from loom.doctor import build_doctor_report, format_mcp_health_report
from loom.mcp_retry import (
    IDEMPOTENCY_ARG,
    api_request_hash,
    ensure_mcp_payload_idempotency,
    is_api_write_command,
    is_mcp_write_tool,
    replay_failed_writes,
    retry_async,
)
from loom.state import AgentCell, MatrixState


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
                    "task": "LOOM:217",
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


class MCPIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = LoomDB(Path(self.tmp.name) / "loom.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.agents["agent-1"] = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        self.mcp = importlib.import_module("loom.mcp")
        self.mcp = importlib.reload(self.mcp)
        self.state_mod = importlib.import_module("loom.state")

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
                "name": "loom_progress",
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

    async def test_reusing_idempotency_key_for_different_write_is_rejected(self):
        async def handle_command(_payload):
            return {"type": "ok"}

        base = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loom_progress",
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
        self.db = LoomDB(Path(self.tmp.name) / "loom.db")
        self.db.init()
        self.addCleanup(self.db.close)

    async def test_failed_write_replay_deletes_successful_queue_entry(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loom_progress",
                "arguments": {"message": "queued", IDEMPOTENCY_ARG: "idem-queued"},
            },
        }
        self.db.enqueue_failed_write(
            idempotency_key="idem-queued",
            endpoint="/mcp",
            surface="loom",
            tool_name="loom_progress",
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
        self.assertEqual(seen, ["loom_progress"])
        self.assertEqual(self.db.load_failed_writes(), [])
        health = self.db.load_mcp_health_summary(since=0)
        self.assertEqual(health["totals"].get("replay"), 1)

    async def test_api_failed_write_replay_dedupes_existing_idempotency_row(self):
        from loom.server import replay_api_failed_write_payload

        payload = {
            "cmd": "board_add_task",
            "title": "Queued once",
            "idempotency_key": "api-queued-idem",
        }
        cached_response = {
            "ok": True,
            "data": {"type": "board_task_added", "task_id": "LOOM:1"},
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
        from loom.server import (
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

    def test_architect_journal_replay_recovers_existing_jsonl_entry_without_duplicate(self):
        state = MatrixState(db=self.db)
        state_mod = importlib.import_module("loom.state")

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
            surface="loom",
            tool_name="loom_progress",
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
        self.assertIn("Loom MCP health", rendered)
        self.assertIn("retries=1", rendered)
        self.assertIn("drops=1", rendered)
        self.assertIn("architect_journal", rendered)


class CriticalWriteCaptureIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = LoomDB(Path(self.tmp.name) / "loom.db")
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
        from loom.server import _internal_failed_write_key

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
