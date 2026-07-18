"""Durable event-ingest daemon/client/drainer tests."""

import asyncio
import contextlib
import json
import logging
import os
import signal
import sqlite3
import tempfile
import time
import unittest
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

from torque import event_ingest_daemon, profiling
from torque.event_ingest_db import EventIngestStore, event_call_row_from_record
from torque.event_ingest_daemon import (
    EventIngestDaemon,
    PROTOCOL_VERSION,
    read_frame,
    write_frame,
)
from torque.event_ingest_client import (
    EventIngestClient,
    EventIngestProtocolError,
    EventIngestUnavailable,
)
from torque.events import (
    EventBus,
    EventIngestDrainer,
    EventLog,
    PanelEventLog,
    build_event_ingest_envelope,
)
try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.state import AgentCell, MatrixState


async def _open_client(path: Path):
    return await asyncio.open_unix_connection(str(path))


class _Harness:
    def __init__(self, *, db_name: str = "event_ingest.db", max_rows: int = 100):
        self.tmp: tempfile.TemporaryDirectory | None = None
        self.socket_path: Path | None = None
        self.db_path: Path | None = None
        self.store: EventIngestStore | None = None
        self.daemon: EventIngestDaemon | None = None
        self.server: asyncio.AbstractServer | None = None
        self.db_name = db_name
        self.max_rows = max_rows

    async def start(self) -> None:
        if self.tmp is None:
            self.tmp = tempfile.TemporaryDirectory()
            self.db_path = Path(self.tmp.name) / self.db_name
        self.socket_path = Path(self.tmp.name) / "event_ingest.sock"
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self.store = EventIngestStore(
            self.db_path, max_rows=self.max_rows).init()
        self.daemon = EventIngestDaemon(self.store)
        self.server = await asyncio.start_unix_server(
            self.daemon.handle_client, path=str(self.socket_path))

    async def stop_server(self, *, close_store: bool = True) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.daemon and close_store:
            await self.daemon.shutdown()
        self.daemon = None
        if close_store:
            self.store = None
        if self.socket_path:
            with contextlib.suppress(FileNotFoundError):
                self.socket_path.unlink()

    async def stop(self) -> None:
        await self.stop_server(close_store=True)
        if self.tmp:
            self.tmp.cleanup()
            self.tmp = None


class EventIngestStoreTests(unittest.TestCase):
    def _envelope(self, cell_id, tool_name, hook, *, tool_input=None,
                  tool_output=None, tool_response=None,
                  session_id="sess", received_at=0):
        raw = {
            "hook_event_name": hook,
            "tool_name": tool_name,
            "session_id": session_id,
        }
        if tool_input is not None:
            raw["tool_input"] = tool_input
        if tool_output is not None:
            raw["tool_output"] = tool_output
        if tool_response is not None:
            raw["tool_response"] = tool_response
        return build_event_ingest_envelope(
            raw,
            headers={"X-Torque-Cell-Id": cell_id},
            received_at=received_at,
        )

    def test_append_drain_ack_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventIngestStore(Path(tmp) / "ingest.db", max_rows=2).init()
            try:
                first = store.append({"n": 1}, "k1")
                duplicate = store.append({"n": 1}, "k1")
                second = store.append({"n": 2}, "k2")

                self.assertEqual(first["cursor"], duplicate["cursor"])
                self.assertTrue(duplicate["duplicate"])
                self.assertFalse(second["duplicate"])

                drained = store.drain(since=0, limit=10)
                self.assertEqual([r["event"]["n"] for r in drained["events"]], [1, 2])

                ack = store.ack(up_to=second["cursor"])
                self.assertEqual(ack["ack_cursor"], second["cursor"])
                self.assertEqual(store.drain(since=0, limit=10)["events"], [])
            finally:
                store.close()

    def test_append_many_preserves_order_and_deduplicates_in_one_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventIngestStore(Path(tmp) / "ingest.db").init()
            try:
                first = store.append({"n": 1}, "k1")
                batched = store.append_many([
                    {"event": {"n": 2}, "idempotency_key": "k2"},
                    {"event": {"n": 1}, "idempotency_key": "k1"},
                    {"event": {"n": 3}, "idempotency_key": "k3"},
                ])
                self.assertEqual(
                    [result["duplicate"] for result in batched],
                    [False, True, False],
                )
                self.assertEqual(batched[1]["cursor"], first["cursor"])
                self.assertEqual(
                    [row["event"]["n"] for row in store.drain(limit=10)["events"]],
                    [1, 2, 3],
                )
            finally:
                store.close()

    def test_unacked_events_survive_store_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ingest.db"
            store = EventIngestStore(db_path).init()
            first = store.append({"survives": True}, "crash-key")
            store.close()

            reopened = EventIngestStore(db_path).init()
            try:
                drained = reopened.drain(since=0, limit=10)
                self.assertEqual(len(drained["events"]), 1)
                self.assertEqual(drained["events"][0]["cursor"], first["cursor"])
                self.assertEqual(drained["events"][0]["event"], {"survives": True})
            finally:
                reopened.close()

    def test_query_filters_and_orders_mcp_call_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventIngestStore(
                Path(tmp) / "ingest.db",
                args_capture="full",
            ).init()
            try:
                store.append(
                    self._envelope(
                        "cell-a",
                        "mcp__torque__torque_progress",
                        "PreToolUse",
                        received_at=10,
                    ),
                    "q1",
                    now=10,
                )
                store.append(
                    self._envelope(
                        "cell-a",
                        "mcp__torque__torque_progress",
                        "PostToolUse",
                        tool_input={"message": "done"},
                        received_at=20,
                    ),
                    "q2",
                    now=20,
                )
                store.append(
                    self._envelope(
                        "cell-b",
                        "mcp__torque__torque_done",
                        "PostToolUse",
                        received_at=30,
                    ),
                    "q3",
                    now=30,
                )
                store.append(
                    self._envelope("cell-a", "Bash", "PostToolUse", received_at=40),
                    "q4",
                    now=40,
                )

                self.assertEqual(
                    [r["cell_id"] for r in store.query(cell_id="cell-a", limit=10)],
                    ["cell-a", "cell-a", "cell-a"],
                )
                self.assertEqual(
                    [r["tool_name"] for r in store.query(
                        tool_name_pattern="mcp__torque__torque_progress",
                        limit=10,
                    )],
                    ["mcp__torque__torque_progress", "mcp__torque__torque_progress"],
                )
                self.assertEqual(
                    [r["tool_name"] for r in store.query(
                        tool_name_pattern="mcp__torque__%",
                        limit=10,
                    )],
                    [
                        "mcp__torque__torque_done",
                        "mcp__torque__torque_progress",
                        "mcp__torque__torque_progress",
                    ],
                )
                self.assertEqual(
                    [r["idempotency_key"] for r in store.query(
                        cell_id="cell-a",
                        hook_event_name="PostToolUse",
                        since=15,
                        until=35,
                        tool_name_pattern="mcp__torque__%",
                        limit=5,
                    )],
                    ["q2"],
                )
                self.assertEqual(
                    [r["idempotency_key"] for r in store.query(limit=2)],
                    ["q4", "q3"],
                )
            finally:
                store.close()

    def test_migration_backfills_normalized_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ingest.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO metadata(key, value) VALUES('ack_cursor', '0')"
                )
                conn.execute(
                    "CREATE TABLE events ("
                    "cursor INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "idempotency_key TEXT NOT NULL UNIQUE, "
                    "event_json TEXT NOT NULL, "
                    "appended_at REAL NOT NULL)"
                )
                event = self._envelope(
                    "legacy-cell",
                    "mcp__torque__torque_context",
                    "PostToolUse",
                    received_at=100,
                )
                conn.execute(
                    "INSERT INTO events(idempotency_key, event_json, appended_at) "
                    "VALUES (?, ?, ?)",
                    ("legacy-key", json.dumps(event), 100.0),
                )
                conn.commit()
            finally:
                conn.close()

            store = EventIngestStore(db_path).init()
            try:
                rows = store.query(cell_id="legacy-cell", limit=10)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["tool_name"], "mcp__torque__torque_context")
            finally:
                store.close()

    def test_redaction_modes_and_tool_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = self._envelope(
                "cell",
                "mcp__torque__secret",
                "PostToolUse",
                tool_input={"token": "secret", "task_id": "TORQUE:1"},
                tool_output={"result": "secret"},
            )
            off = EventIngestStore(
                Path(tmp) / "off.db",
                args_capture="off",
            ).init()
            meta = EventIngestStore(
                Path(tmp) / "meta.db",
                args_capture="metadata",
            ).init()
            full = EventIngestStore(
                Path(tmp) / "full.db",
                args_capture="full",
            ).init()
            allow = EventIngestStore(
                Path(tmp) / "allow.db",
                args_capture="metadata",
                full_capture_tools=["mcp__torque__secret"],
            ).init()
            try:
                off.append(event, "off")
                off_raw = off.query(limit=1)[0]["event"]["raw"]
                self.assertNotIn("tool_input", off_raw)
                self.assertNotIn("tool_output", off_raw)

                meta.append(event, "meta")
                meta_raw = meta.query(limit=1)[0]["event"]["raw"]
                self.assertEqual(meta_raw["tool_input"]["arg_keys"], ["task_id", "token"])
                self.assertTrue(meta_raw["tool_input"]["redacted"])
                self.assertNotIn("secret", json.dumps(meta_raw["tool_input"]))
                self.assertTrue(meta_raw["tool_output"]["redacted"])

                full.append(event, "full")
                self.assertEqual(
                    full.query(limit=1)[0]["event"]["raw"]["tool_input"]["token"],
                    "secret",
                )

                allow.append(event, "allow")
                self.assertEqual(
                    allow.query(limit=1)[0]["event"]["raw"]["tool_input"]["token"],
                    "secret",
                )
            finally:
                off.close()
                meta.close()
                full.close()
                allow.close()

    def test_call_row_maps_codex_tool_response_result(self):
        event = self._envelope(
            "cell",
            "mcp__torque__torque_context",
            "PostToolUse",
            tool_response={"content": [{"type": "text", "text": "ok"}]},
            received_at=123,
        )

        row = event_call_row_from_record({
            "cursor": 7,
            "idempotency_key": "codex-response",
            "event": event,
            "appended_at": 456,
        })

        self.assertEqual(row["cell_id"], "cell")
        self.assertEqual(row["tool_name"], "mcp__torque__torque_context")
        self.assertEqual(row["hook_event_name"], "PostToolUse")
        self.assertEqual(
            row["result"],
            {"content": [{"type": "text", "text": "ok"}]},
        )
        self.assertFalse(row["result_redacted"])

    def test_tool_response_redacts_with_output_capture_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = self._envelope(
                "cell",
                "mcp__torque__secret",
                "PostToolUse",
                tool_response={"result": "secret"},
            )
            off = EventIngestStore(
                Path(tmp) / "off.db",
                args_capture="off",
            ).init()
            meta = EventIngestStore(
                Path(tmp) / "meta.db",
                args_capture="metadata",
            ).init()
            try:
                off.append(event, "off")
                off_row = event_call_row_from_record(off.query(limit=1)[0])
                self.assertIsNone(off_row["result"])
                self.assertTrue(off_row["result_redacted"])
                self.assertNotIn("tool_response", off_row["raw"])

                meta.append(event, "meta")
                meta_row = event_call_row_from_record(meta.query(limit=1)[0])
                self.assertTrue(meta_row["result_redacted"])
                self.assertTrue(meta_row["result"]["redacted"])
                self.assertNotIn("secret", json.dumps(meta_row["result"]))
            finally:
                off.close()
                meta.close()

    def test_retention_trims_by_row_count_and_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventIngestStore(
                Path(tmp) / "ingest.db",
                max_rows=3,
                max_age_days=14,
            ).init()
            try:
                for idx in range(5):
                    store.append({"n": idx}, f"row-{idx}", now=100 + idx)
                with mock.patch("torque.event_ingest_db.time.time", return_value=105):
                    ack = store.ack(up_to=5)
                self.assertEqual(ack["trimmed"], 2)
                self.assertEqual([r["event"]["n"] for r in store.query(limit=10)], [4, 3, 2])
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as tmp:
            now = 100 + (15 * 86400)
            store = EventIngestStore(
                Path(tmp) / "ingest.db",
                max_rows=10,
                max_age_days=14,
            ).init()
            try:
                store.append({"n": "old"}, "old", now=100)
                store.append({"n": "new"}, "new", now=now)
                with mock.patch(
                    "torque.event_ingest_db.time.time",
                    return_value=now,
                ):
                    ack = store.ack(up_to=2)
                self.assertEqual(ack["trimmed"], 1)
                self.assertEqual([r["event"]["n"] for r in store.query(limit=10)], ["new"])
            finally:
                store.close()


class EventIngestClientCounterTests(unittest.IsolatedAsyncioTestCase):
    async def test_ack_records_trimmed_rows_counter(self):
        old_profile_enabled = os.environ.get("TORQUE_PROFILE_ENABLED")
        os.environ["TORQUE_PROFILE_ENABLED"] = "1"
        profiling.reset()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = EventIngestStore(Path(tmp) / "ingest.db", max_rows=2).init()
                try:
                    store.append({"n": 1}, "trim-k1")
                    store.append({"n": 2}, "trim-k2")
                    store.append({"n": 3}, "trim-k3")
                    trim_response = store.ack(up_to=3)
                finally:
                    store.close()

                client = EventIngestClient(Path(tmp) / "unused.sock")

                async def fake_call(op, **_payload):
                    self.assertEqual(op, "ack")
                    return trim_response

                client._call_with_reconnect_retry = fake_call
                ack = await client.ack(up_to=3)

            self.assertEqual(ack["trimmed"], 1)
            counters = profiling.recorder().snapshot()["counters"]
            self.assertEqual(counters.get("events_ingest_ring_trimmed"), 1)
        finally:
            if old_profile_enabled is None:
                os.environ.pop("TORQUE_PROFILE_ENABLED", None)
            else:
                os.environ["TORQUE_PROFILE_ENABLED"] = old_profile_enabled
            profiling.reset()

    async def test_configure_rejects_unknown_op_error_response(self):
        client = EventIngestClient(Path("unused.sock"))

        async def fake_call(op, **payload):
            self.assertEqual(op, "configure")
            self.assertEqual(payload["args_capture"], "metadata")
            return {
                "type": "error",
                "code": "protocol_error",
                "message": "unknown op: 'configure'",
            }

        client._call_with_reconnect_retry = fake_call
        with self.assertRaises(EventIngestProtocolError):
            await client.configure(args_capture="metadata")


class EventIngestThreadExhaustionTests(unittest.IsolatedAsyncioTestCase):
    """Connect-time thread/process exhaustion degrades, never aborts startup."""

    def _client(self, exc: Exception) -> EventIngestClient:
        # ``boom`` stands in for the executor allocating a worker thread/lock
        # inside ``asyncio.to_thread`` and failing under resource exhaustion;
        # data_dir is never touched because it raises immediately.
        def boom(_data_dir):
            raise exc

        return EventIngestClient(
            data_dir=Path("/nonexistent-torque-ingest"),
            reconnect_delay=0.01,
            ensure_running_func=boom,
        )

    async def test_connect_maps_thread_limit_to_unavailable(self):
        client = self._client(RuntimeError("can't start new thread"))
        with self.assertLogs("torque.event_ingest_client", level="ERROR") as logs:
            with self.assertRaises(EventIngestUnavailable):
                await client.connect()
        joined = "\n".join(logs.output)
        self.assertIn("system thread/process limit reached", joined)
        self.assertIn("ulimit -u", joined)
        # The original bare RuntimeError must not leak as the raised type.
        await client.aclose()

    async def test_call_with_reconnect_retry_degrades_on_thread_limit(self):
        client = self._client(RuntimeError("can't start new thread"))
        # append() -> _call_with_reconnect_retry only catches
        # EventIngestUnavailable; the mapping keeps runtime appends degrading
        # instead of surfacing a bare RuntimeError to callers.
        with self.assertRaises(EventIngestUnavailable):
            await client.append({"payload": 1}, idempotency_key="k")
        await client.aclose()

    async def test_non_resource_runtimeerror_still_degrades(self):
        client = self._client(RuntimeError("ensure_running blew up"))
        with self.assertLogs(
            "torque.event_ingest_client", level="WARNING"
        ) as logs:
            with self.assertRaises(EventIngestUnavailable):
                await client.connect()
        self.assertIn("ensure-running failed", "\n".join(logs.output))
        await client.aclose()

    async def test_protocol_error_subclass_propagates_untouched(self):
        client = self._client(EventIngestProtocolError("nope"))
        with self.assertRaises(EventIngestProtocolError):
            await client.connect()
        await client.aclose()


class EventIngestProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = _Harness()
        await self.h.start()

    async def asyncTearDown(self):
        await self.h.stop()

    async def test_protocol_version_bumped_for_query_and_configure_ops(self):
        self.assertGreaterEqual(PROTOCOL_VERSION, 3)

    async def test_ping_append_drain_ack_status(self):
        reader, writer = await _open_client(self.h.socket_path)
        try:
            await write_frame(writer, {"op": "ping"})
            pong = await read_frame(reader)
            self.assertEqual(pong.get("type"), "pong")
            self.assertEqual(pong.get("version"), PROTOCOL_VERSION)
            self.assertEqual(pong.get("pid"), os.getpid())

            await write_frame(writer, {
                "op": "append",
                "event": {"payload": "one"},
                "idempotency_key": "wire-1",
            })
            appended = await read_frame(reader)
            self.assertEqual(appended.get("type"), "ok")
            self.assertEqual(appended.get("op"), "append")

            await write_frame(writer, {
                "op": "append_many",
                "items": [
                    {"event": {"payload": "two"}, "idempotency_key": "wire-2"},
                    {"event": {"payload": "three"}, "idempotency_key": "wire-3"},
                ],
            })
            appended_many = await read_frame(reader)
            self.assertEqual(appended_many.get("type"), "ok")
            self.assertEqual(appended_many.get("op"), "append_many")
            self.assertEqual(len(appended_many.get("results", [])), 2)

            await write_frame(writer, {"op": "drain", "since": 0, "limit": 10})
            drained = await read_frame(reader)
            self.assertEqual(drained.get("type"), "drain")
            self.assertEqual(drained["events"][0]["event"], {"payload": "one"})

            await write_frame(writer, {
                "op": "ack",
                "up_to": drained["events"][-1]["cursor"],
            })
            acked = await read_frame(reader)
            self.assertEqual(acked.get("type"), "ok")
            self.assertEqual(acked.get("op"), "ack")

            await write_frame(writer, {"op": "status"})
            status = await read_frame(reader)
            self.assertEqual(status.get("type"), "status")
            self.assertEqual(status.get("pending_rows"), 0)
        finally:
            writer.close()
            await writer.wait_closed()


class EventIngestLifecycleTests(unittest.TestCase):
    def test_configure_logging_uses_rotating_handler_and_rolls_over(self):
        logger = event_ingest_daemon.log
        old_level = logger.level
        old_propagate = logger.propagate
        old_max_bytes = event_ingest_daemon.LOG_ROTATION_MAX_BYTES
        old_backup_count = event_ingest_daemon.LOG_ROTATION_BACKUP_COUNT

        try:
            event_ingest_daemon.LOG_ROTATION_MAX_BYTES = 180
            event_ingest_daemon.LOG_ROTATION_BACKUP_COUNT = 2
            with tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / event_ingest_daemon.DEFAULT_LOG_FILE_NAME
                event_ingest_daemon._configure_logging(log_path)

                handlers = [
                    handler for handler in logger.handlers
                    if getattr(handler, "_torque_event_ingest_managed", False)
                ]
                self.assertEqual(len(handlers), 1)
                handler = handlers[0]
                self.assertIsInstance(handler, RotatingFileHandler)
                self.assertEqual(handler.maxBytes, 180)
                self.assertEqual(handler.backupCount, 2)

                for idx in range(20):
                    event_ingest_daemon.log.info(
                        "event-ingest-rotation-test-%02d %s", idx, "x" * 80
                    )
                handler.flush()

                self.assertTrue(log_path.exists())
                self.assertTrue(log_path.with_name("event_ingest.log.1").exists())
        finally:
            event_ingest_daemon.LOG_ROTATION_MAX_BYTES = old_max_bytes
            event_ingest_daemon.LOG_ROTATION_BACKUP_COUNT = old_backup_count
            for handler in list(logger.handlers):
                if getattr(handler, "_torque_event_ingest_managed", False):
                    logger.removeHandler(handler)
                    with contextlib.suppress(Exception):
                        handler.close()
            logger.setLevel(old_level)
            logger.propagate = old_propagate

    def test_ensure_running_replaces_protocol_mismatch_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            old_pid = 12345
            new_pid = 23456
            ping_responses = [
                {"type": "pong", "version": PROTOCOL_VERSION - 1, "pid": old_pid},
                {"type": "pong", "version": PROTOCOL_VERSION, "pid": new_pid},
            ]

            def fake_ping(_socket_path):
                if ping_responses:
                    return ping_responses.pop(0)
                return {"type": "pong", "version": PROTOCOL_VERSION, "pid": new_pid}

            with mock.patch(
                "torque.event_ingest_daemon._ping_socket",
                side_effect=fake_ping,
            ), mock.patch(
                "torque.event_ingest_daemon._pid_alive",
                return_value=True,
            ), mock.patch(
                "torque.event_ingest_daemon._terminate_pid",
            ) as terminate_pid, mock.patch(
                "torque.event_ingest_daemon._read_pid_file",
                return_value=None,
            ), mock.patch(
                "torque.event_ingest_daemon.spawn_detached",
                return_value=new_pid,
            ) as spawn_detached:
                sock_path = event_ingest_daemon.ensure_running(data_dir, timeout=1.0)

            self.assertEqual(sock_path, data_dir / event_ingest_daemon.DEFAULT_SOCKET_NAME)
            terminate_pid.assert_called_once_with(old_pid)
            spawn_detached.assert_called_once()

    def test_detached_daemon_sigterm_exits_and_unlinks_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            pid_path = data_dir / event_ingest_daemon.DEFAULT_PID_FILE_NAME
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                sock_path = event_ingest_daemon.ensure_running(
                    data_dir,
                    timeout=5.0,
                    max_rows=10,
                )
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    with contextlib.suppress(ChildProcessError, OSError):
                        os.waitpid(pid, os.WNOHANG)
                    if not event_ingest_daemon._pid_alive(pid):
                        break
                    time.sleep(0.05)
                self.assertFalse(
                    event_ingest_daemon._pid_alive(pid),
                    "event-ingest daemon stayed alive after SIGTERM",
                )
                self.assertFalse(sock_path.exists())
                self.assertFalse(pid_path.exists())
            finally:
                if event_ingest_daemon._pid_alive(pid):
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(pid, signal.SIGKILL)


class EventIngestClientAndDrainerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = _Harness()
        await self.h.start()

    async def asyncTearDown(self):
        await self.h.stop()

    def _state_with_cell(self):
        state = MatrixState()
        state.groups["g"] = []
        cell = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            agent_type="claude-code",
        )
        state.agents[cell.id] = cell
        return state, cell

    async def test_client_idempotent_append(self):
        client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        await client.connect()
        try:
            first = await client.append({"payload": 1}, idempotency_key="same")
            second = await client.append({"payload": 1}, idempotency_key="same")
            self.assertEqual(first.get("cursor"), second.get("cursor"))
            self.assertTrue(second.get("duplicate"))

            status = await client.status()
            self.assertEqual(status.get("total_rows"), 1)
        finally:
            await client.aclose()

    async def test_concurrent_client_appends_share_one_durable_batch(self):
        client = EventIngestClient(
            self.h.socket_path,
            reconnect_delay=0.05,
            append_batch_window=0.02,
        )
        await client.connect()
        try:
            with mock.patch.object(
                self.h.store,
                "append_many",
                wraps=self.h.store.append_many,
            ) as append_many:
                results = await asyncio.gather(*[
                    client.append({"payload": index}, idempotency_key=f"batch-{index}")
                    for index in range(12)
                ])
            self.assertEqual(append_many.call_count, 1)
            self.assertEqual(len({result["cursor"] for result in results}), 12)
            self.assertTrue(all(result["type"] == "ok" for result in results))
        finally:
            await client.aclose()

    async def test_immediate_append_during_pending_background_reconnect(self):
        original_append = self.h.store.append_many

        def slow_append(*args, **kwargs):
            time.sleep(0.25)
            return original_append(*args, **kwargs)

        self.h.store.append_many = slow_append
        client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        await client.connect()
        try:
            assert client._writer is not None
            client._writer.close()
            await client._writer.wait_closed()

            deadline = asyncio.get_running_loop().time() + 1.0
            while (
                asyncio.get_running_loop().time() < deadline
                and (
                    client._reconnect_task is None
                    or client._reconnect_task.done()
                )
            ):
                await asyncio.sleep(0.01)
            self.assertIsNotNone(client._reconnect_task)
            self.assertFalse(client._reconnect_task.done())

            appended = await asyncio.wait_for(
                client.append({"slow": True}, idempotency_key="slow-after-drop"),
                timeout=2.0,
            )
            self.assertEqual(appended.get("type"), "ok")
            self.assertEqual((await client.status()).get("total_rows"), 1)
        finally:
            await client.aclose()

    async def test_drainer_applies_events_and_marks_drained(self):
        state, cell = self._state_with_cell()
        bus = EventBus(state, EventLog())
        bus.start()
        client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        await client.connect()
        try:
            envelope = build_event_ingest_envelope(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hello"},
                },
                headers={"X-Torque-Cell-Id": cell.id},
            )
            await client.append(envelope, idempotency_key="drain-1")

            drainer = EventIngestDrainer(client, bus, state, batch_size=10)
            processed = await drainer.drain_once()
            self.assertEqual(processed, 1)
            self.assertEqual(cell.activity, "tool_call")
            self.assertIn("Running:", cell.activity_detail)

            status = await client.status()
            self.assertEqual(status.get("pending_rows"), 0)
            self.assertGreaterEqual(status.get("ack_cursor"), 1)
        finally:
            await client.aclose()

    async def test_main_daemon_restart_drains_unacked_rows(self):
        state, cell = self._state_with_cell()
        first_client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        await first_client.connect()
        await first_client.append(
            build_event_ingest_envelope(
                {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
                headers={"X-Torque-Cell-Id": cell.id},
            ),
            idempotency_key="restart-1",
        )
        await first_client.aclose()

        # New main-daemon image: fresh client/EventBus, same ingest daemon/db.
        restarted_client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        await restarted_client.connect()
        try:
            bus = EventBus(state, EventLog())
            bus.start()
            drainer = EventIngestDrainer(restarted_client, bus, state)
            processed = await drainer.drain_once()
            self.assertEqual(processed, 1)
            self.assertEqual(cell.activity, "")
            self.assertEqual((await restarted_client.status()).get("pending_rows"), 0)
        finally:
            await restarted_client.aclose()

    async def test_restart_backlog_session_end_invokes_callbacks_and_hooks(self):
        state, cell = self._state_with_cell()
        cell.agent_type = "codex"
        cell.status = "running"
        cell.agent_session_id = "codex-session-current"
        panel_seen: list[dict] = []
        panel_log = PanelEventLog()
        panel_log.on_event = lambda evt: panel_seen.append(evt)

        client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        await client.connect()
        try:
            await client.append(
                build_event_ingest_envelope(
                    {
                        "hook_event_name": "Stop",
                        "session_id": "codex-session-current",
                        "last_assistant_message": "finished from backlog",
                    },
                    headers={"X-Torque-Cell-Id": cell.id},
                ),
                idempotency_key="startup-session-end",
            )

            bus = EventBus(state, EventLog(), panel_log=panel_log)
            bus.start()
            session_end_seen: list[str] = []
            engineer_seen: list[str] = []

            async def on_session_end(ended_cell):
                session_end_seen.append(ended_cell.id)

            class FakeEngineerBuffer:
                def on_agent_activity_change(self, changed_cell):
                    engineer_seen.append(changed_cell.id)

            bus.on_session_end = on_session_end
            bus._engineer_buffer = FakeEngineerBuffer()

            drainer = EventIngestDrainer(client, bus, state, batch_size=10)
            processed = await drainer.drain_once()
            self.assertEqual(processed, 1)
            self.assertEqual(session_end_seen, [cell.id])
            self.assertEqual(engineer_seen, [cell.id])
            self.assertEqual([evt["kind"] for evt in panel_seen], ["agent_finished"])
            self.assertEqual(cell.status, "idle")
            self.assertEqual(cell.last_summary, "finished from backlog")
        finally:
            await client.aclose()

    async def test_reconnect_callback_reports_fresh_and_append_recovers(self):
        client = EventIngestClient(self.h.socket_path, reconnect_delay=0.05)
        seen: list[dict] = []

        async def on_reconnect(info):
            seen.append(info)

        client.on_reconnect = on_reconnect
        await client.connect()
        try:
            client._last_daemon_pid = -1  # In-process replacement sentinel.
            assert client._writer is not None
            client._writer.close()

            deadline = asyncio.get_running_loop().time() + 2.0
            while asyncio.get_running_loop().time() < deadline and not seen:
                await asyncio.sleep(0.05)
            self.assertTrue(seen, "on_reconnect was never called")
            self.assertTrue(seen[0]["fresh"])

            appended = await client.append({"after": "reconnect"}, idempotency_key="after")
            self.assertEqual(appended.get("type"), "ok")
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
