"""Durable SQLite ring for agent-hook event ingest.

This store is owned by the event-ingest subprocess.  The main Torque daemon
only talks to it through ``event_ingest_daemon``'s unix-socket protocol.
"""

from __future__ import annotations

import json
import fnmatch
import re
import sqlite3
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_MAX_ROWS = 100_000
DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_ARGS_CAPTURE = "metadata"
VALID_ARGS_CAPTURE_MODES = {"off", "metadata", "full"}
_NORMALIZED_COLUMNS_BACKFILL_KEY = "normalized_columns_backfilled_v1"
_TOOL_RESULT_KEYS = ("tool_output", "tool_response")


def normalize_args_capture_mode(value: Any) -> str:
    mode = str(value or DEFAULT_ARGS_CAPTURE).strip().lower()
    if mode not in VALID_ARGS_CAPTURE_MODES:
        return DEFAULT_ARGS_CAPTURE
    return mode


def _json_byte_size(value: Any) -> int:
    try:
        return len(json.dumps(value, separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _redaction_summary(value: Any, *, include_arg_keys: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "redacted": True,
        "byte_size": _json_byte_size(value),
    }
    if include_arg_keys:
        if isinstance(value, dict):
            summary["arg_keys"] = sorted(str(key) for key in value.keys())
        else:
            summary["arg_keys"] = []
            if value is not None:
                summary["value_type"] = type(value).__name__
    return summary


def _tool_matches_patterns(tool_name: str, patterns: list[str] | tuple[str, ...]) -> bool:
    tool_name = str(tool_name or "")
    for raw_pattern in patterns or []:
        pattern = str(raw_pattern or "").strip()
        if not pattern:
            continue
        if fnmatch.fnmatchcase(tool_name, pattern):
            return True
        try:
            if re.fullmatch(pattern, tool_name) or re.search(pattern, tool_name):
                return True
        except re.error:
            pass
        if tool_name == pattern:
            return True
    return False


def redact_event_for_mcp_call_log(
    event: dict[str, Any],
    *,
    args_capture: str = DEFAULT_ARGS_CAPTURE,
    full_capture_tools: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return the event envelope after one-way ingest-time arg/output redaction."""
    if not isinstance(event, dict):
        return event
    mode = normalize_args_capture_mode(args_capture)
    redacted = deepcopy(event)
    raw = redacted.get("raw")
    if not isinstance(raw, dict):
        return redacted
    tool_name = str(raw.get("tool_name") or raw.get("name") or "")
    if mode == "metadata" and _tool_matches_patterns(
        tool_name,
        list(full_capture_tools or []),
    ):
        return redacted
    if mode == "full":
        return redacted
    if mode == "off":
        raw.pop("tool_input", None)
        for key in _TOOL_RESULT_KEYS:
            raw.pop(key, None)
        return redacted
    if "tool_input" in raw:
        raw["tool_input"] = _redaction_summary(raw.get("tool_input"), include_arg_keys=True)
    for key in _TOOL_RESULT_KEYS:
        if key in raw:
            raw[key] = _redaction_summary(raw.get(key))
    return redacted


def _first_present(source: dict[str, Any], *keys: str) -> tuple[str, Any]:
    for key in keys:
        if key in source:
            return key, source.get(key)
    return "", None


def event_call_row_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project one ingest DB row into the MCP-call UI/API shape."""
    event = dict(record.get("event") or {})
    headers = dict(event.get("headers") or {})
    raw = dict(event.get("raw") or {})
    cell_id = str(
        record.get("cell_id")
        or headers.get("X-Torque-Cell-Id")
        or ""
    )
    tool_name = str(record.get("tool_name") or raw.get("tool_name") or raw.get("name") or "")
    hook_event_name = str(
        record.get("hook_event_name")
        or raw.get("hook_event_name")
        or raw.get("type")
        or ""
    )
    session_id = str(record.get("session_id") or raw.get("session_id") or "")
    duration_ms = raw.get("duration_ms")
    try:
        duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    error = (
        raw.get("error")
        or raw.get("error_message")
        or raw.get("exception")
        or raw.get("stderr")
        or ""
    )
    success = raw.get("success")
    if success is None:
        success = hook_event_name not in {"PostToolUseFailure", "ToolUseFailure"}
        if error:
            success = False
    result_key, result = _first_present(raw, *_TOOL_RESULT_KEYS)
    return {
        "cursor": int(record.get("cursor") or 0),
        "idempotency_key": str(record.get("idempotency_key") or ""),
        "cell_id": cell_id,
        "tool_name": tool_name,
        "hook_event_name": hook_event_name,
        "session_id": session_id,
        "appended_at": float(record.get("appended_at") or 0.0),
        "received_at": float(event.get("received_at") or record.get("appended_at") or 0.0),
        "duration_ms": duration_ms,
        "success": bool(success),
        "error": str(error or ""),
        "args": raw.get("tool_input") if "tool_input" in raw else None,
        "args_redacted": "tool_input" not in raw or bool(
            isinstance(raw.get("tool_input"), dict)
            and raw.get("tool_input", {}).get("redacted")
        ),
        "result": result if result_key else None,
        "result_redacted": not result_key or bool(
            isinstance(result, dict)
            and result.get("redacted")
        ),
        "raw": raw,
    }


class EventIngestStore:
    """Small SQLite-backed append-only event ring.

    Rows are never considered drained by deletion alone.  ``ack_cursor`` is
    persisted in ``metadata`` and ``drain`` starts after ``max(since,
    ack_cursor)`` so retained acked rows can still provide a short
    idempotency window without replaying after daemon restart.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_age_days: int | float = DEFAULT_MAX_AGE_DAYS,
        args_capture: str = DEFAULT_ARGS_CAPTURE,
        full_capture_tools: list[str] | tuple[str, ...] | None = None,
    ):
        self.db_path = Path(db_path)
        self.max_rows = max(1, int(max_rows or DEFAULT_MAX_ROWS))
        self.max_age_days = max(
            0.0,
            float(DEFAULT_MAX_AGE_DAYS if max_age_days is None else max_age_days),
        )
        self.args_capture = normalize_args_capture_mode(args_capture)
        self.full_capture_tools = [
            str(item or "").strip()
            for item in list(full_capture_tools or [])
            if str(item or "").strip()
        ]
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def init(self) -> "EventIngestStore":
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # WAL + FULL gives a durable commit boundary while keeping readers and
        # writers independent enough for drain/status calls under firehose load.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "cursor INTEGER PRIMARY KEY AUTOINCREMENT, "
            "idempotency_key TEXT NOT NULL UNIQUE, "
            "event_json TEXT NOT NULL, "
            "appended_at REAL NOT NULL)"
        )
        self._ensure_normalized_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_ingest_cursor "
            "ON events(cursor)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_ingest_cell_appended "
            "ON events(cell_id, appended_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_ingest_tool_appended "
            "ON events(tool_name, appended_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_ingest_hook_appended "
            "ON events(hook_event_name, appended_at DESC)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO metadata(key, value) "
            "VALUES('ack_cursor', '0')"
        )
        self._backfill_normalized_columns(conn)
        self._conn = conn
        return self

    def _ensure_normalized_columns(self, conn: sqlite3.Connection) -> None:
        for column in ("cell_id", "tool_name", "hook_event_name", "session_id"):
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _backfill_normalized_columns(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (_NORMALIZED_COLUMNS_BACKFILL_KEY,),
        ).fetchone()
        if row and str(row["value"] or "") == "1":
            return
        conn.execute(
            "UPDATE events SET "
            "cell_id = COALESCE(cell_id, json_extract(event_json, '$.headers.\"X-Torque-Cell-Id\"')), "
            "tool_name = COALESCE(tool_name, json_extract(event_json, '$.raw.tool_name')), "
            "hook_event_name = COALESCE(hook_event_name, json_extract(event_json, '$.raw.hook_event_name')), "
            "session_id = COALESCE(session_id, json_extract(event_json, '$.raw.session_id')) "
            "WHERE cell_id IS NULL OR tool_name IS NULL "
            "OR hook_event_name IS NULL OR session_id IS NULL"
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, '1')",
            (_NORMALIZED_COLUMNS_BACKFILL_KEY,),
        )

    def configure(
        self,
        *,
        max_rows: int | None = None,
        max_age_days: int | float | None = None,
        args_capture: str | None = None,
        full_capture_tools: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if max_rows is not None:
                self.max_rows = max(1, int(max_rows or DEFAULT_MAX_ROWS))
            if max_age_days is not None:
                self.max_age_days = max(0.0, float(max_age_days))
            if args_capture is not None:
                self.args_capture = normalize_args_capture_mode(args_capture)
            if full_capture_tools is not None:
                self.full_capture_tools = [
                    str(item or "").strip()
                    for item in list(full_capture_tools or [])
                    if str(item or "").strip()
                ]
            return {
                "max_rows": self.max_rows,
                "max_age_days": self.max_age_days,
                "args_capture": self.args_capture,
                "full_capture_tools": list(self.full_capture_tools),
            }

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("EventIngestStore is not initialized")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def append(
        self,
        event: dict[str, Any],
        idempotency_key: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Append ``event`` unless ``idempotency_key`` already exists."""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key is required")
        if not isinstance(event, dict):
            raise ValueError("event envelope must be an object")
        event = redact_event_for_mcp_call_log(
            event,
            args_capture=self.args_capture,
            full_capture_tools=self.full_capture_tools,
        )
        raw = event.get("raw") if isinstance(event, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        headers = event.get("headers") if isinstance(event, dict) else {}
        if not isinstance(headers, dict):
            headers = {}
        cell_id = str(headers.get("X-Torque-Cell-Id") or "")
        tool_name = str(raw.get("tool_name") or raw.get("name") or "")
        hook_event_name = str(raw.get("hook_event_name") or raw.get("type") or "")
        session_id = str(raw.get("session_id") or "")
        event_json = json.dumps(event, separators=(",", ":"), sort_keys=True)
        appended_at = float(time.time() if now is None else now)
        with self._lock:
            conn = self.conn
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT cursor FROM events WHERE idempotency_key = ?",
                    (key,),
                ).fetchone()
                if row is not None:
                    conn.execute("COMMIT")
                    return {
                        "cursor": int(row["cursor"]),
                        "duplicate": True,
                    }
                cur = conn.execute(
                    "INSERT INTO events("
                    "idempotency_key, event_json, appended_at, "
                    "cell_id, tool_name, hook_event_name, session_id"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        event_json,
                        appended_at,
                        cell_id,
                        tool_name,
                        hook_event_name,
                        session_id,
                    ),
                )
                cursor = int(cur.lastrowid)
                conn.execute("COMMIT")
                return {"cursor": cursor, "duplicate": False}
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                row = conn.execute(
                    "SELECT cursor FROM events WHERE idempotency_key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    raise
                return {"cursor": int(row["cursor"]), "duplicate": True}
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def drain(self, *, since: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return rows after ``max(since, ack_cursor)`` in cursor order."""
        since_cursor = max(0, int(since or 0))
        row_limit = max(1, int(limit or 100))
        with self._lock:
            ack_cursor = self.ack_cursor()
            effective_since = max(since_cursor, ack_cursor)
            rows = self.conn.execute(
                "SELECT cursor, idempotency_key, event_json, appended_at "
                "FROM events WHERE cursor > ? ORDER BY cursor LIMIT ?",
                (effective_since, row_limit),
            ).fetchall()
            events = []
            for row in rows:
                events.append({
                    "cursor": int(row["cursor"]),
                    "idempotency_key": row["idempotency_key"],
                    "event": json.loads(row["event_json"]),
                    "appended_at": float(row["appended_at"]),
                })
            return {
                "events": events,
                "ack_cursor": ack_cursor,
                "high_watermark": self.high_watermark(),
            }

    def ack(self, *, up_to: int) -> dict[str, Any]:
        """Persist the drained cursor and trim old acked rows to max_rows."""
        target = max(0, int(up_to or 0))
        with self._lock:
            conn = self.conn
            current = self.ack_cursor()
            ack_cursor = max(current, target)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE metadata SET value = ? WHERE key = 'ack_cursor'",
                    (str(ack_cursor),),
                )
                trimmed = self._trim_locked(ack_cursor)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            status = self.status()
            status.update({"ack_cursor": ack_cursor, "trimmed": trimmed})
            return status

    def status(self) -> dict[str, Any]:
        with self._lock:
            ack_cursor = self.ack_cursor()
            high = self.high_watermark()
            total = int(self.conn.execute(
                "SELECT COUNT(*) AS c FROM events").fetchone()["c"])
            pending = int(self.conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE cursor > ?",
                (ack_cursor,),
            ).fetchone()["c"])
            return {
                "ack_cursor": ack_cursor,
                "high_watermark": high,
                "total_rows": total,
                "pending_rows": pending,
                "max_rows": self.max_rows,
                "max_age_days": self.max_age_days,
                "args_capture": self.args_capture,
                "full_capture_tools": list(self.full_capture_tools),
            }

    def query(
        self,
        *,
        cell_id: str | None = None,
        cell_ids: list[str] | tuple[str, ...] | None = None,
        tool_name_pattern: str | None = None,
        hook_event_name: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int | None = 50,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        single_cell = str(cell_id or "").strip()
        multi_cells = [
            str(item or "").strip()
            for item in list(cell_ids or [])
            if str(item or "").strip()
        ]
        if single_cell:
            clauses.append("cell_id = ?")
            params.append(single_cell)
        elif multi_cells:
            placeholders = ",".join("?" for _ in multi_cells)
            clauses.append(f"cell_id IN ({placeholders})")
            params.extend(multi_cells)
        pattern = str(tool_name_pattern or "").strip()
        if pattern:
            sql_pattern = pattern.replace("*", "%")
            if "%" in sql_pattern:
                clauses.append("tool_name LIKE ?")
                params.append(sql_pattern)
            else:
                clauses.append("tool_name = ?")
                params.append(sql_pattern)
        hook = str(hook_event_name or "").strip()
        if hook:
            clauses.append("hook_event_name = ?")
            params.append(hook)
        if since is not None:
            clauses.append("appended_at >= ?")
            params.append(float(since))
        if until is not None:
            clauses.append("appended_at <= ?")
            params.append(float(until))
        try:
            row_limit = int(limit if limit is not None else 50)
        except (TypeError, ValueError):
            row_limit = 50
        row_limit = max(1, min(row_limit, 500))

        sql = (
            "SELECT cursor, idempotency_key, event_json, appended_at, "
            "cell_id, tool_name, hook_event_name, session_id FROM events"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY appended_at DESC, cursor DESC LIMIT ?"
        params.append(row_limit)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                results.append({
                    "cursor": int(row["cursor"]),
                    "idempotency_key": row["idempotency_key"],
                    "event": json.loads(row["event_json"]),
                    "appended_at": float(row["appended_at"]),
                    "cell_id": row["cell_id"] or "",
                    "tool_name": row["tool_name"] or "",
                    "hook_event_name": row["hook_event_name"] or "",
                    "session_id": row["session_id"] or "",
                })
            return results

    def ack_cursor(self) -> int:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = 'ack_cursor'"
        ).fetchone()
        if row is None:
            return 0
        try:
            return max(0, int(row["value"] or 0))
        except (TypeError, ValueError):
            return 0

    def high_watermark(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(cursor), 0) AS c FROM events"
        ).fetchone()
        return int(row["c"] or 0)

    def _trim_locked(self, ack_cursor: int) -> int:
        trimmed = 0
        if self.max_age_days > 0:
            cutoff = time.time() - (self.max_age_days * 86400.0)
            cur = self.conn.execute(
                "DELETE FROM events WHERE appended_at < ? AND cursor <= ?",
                (cutoff, ack_cursor),
            )
            trimmed += int(cur.rowcount or 0)
        total = int(self.conn.execute(
            "SELECT COUNT(*) AS c FROM events").fetchone()["c"])
        overflow = max(0, total - self.max_rows)
        if overflow <= 0:
            return trimmed
        cur = self.conn.execute(
            "DELETE FROM events WHERE cursor IN ("
            "SELECT cursor FROM events WHERE cursor <= ? "
            "ORDER BY cursor LIMIT ?) ",
            (ack_cursor, overflow),
        )
        trimmed += int(cur.rowcount or 0)
        return trimmed
