import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub(include_json_helpers=True)

from loom.db import LoomDB
from loom.doctor import build_doctor_report, format_mcp_health_report
from loom.mcp_retry import (
    IDEMPOTENCY_ARG,
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


if __name__ == "__main__":
    unittest.main()
