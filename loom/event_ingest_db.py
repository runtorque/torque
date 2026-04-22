"""Durable SQLite ring for agent-hook event ingest.

This store is owned by the event-ingest subprocess.  The main Loom daemon
only talks to it through ``event_ingest_daemon``'s unix-socket protocol.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


DEFAULT_MAX_ROWS = 50_000


class EventIngestStore:
    """Small SQLite-backed append-only event ring.

    Rows are never considered drained by deletion alone.  ``ack_cursor`` is
    persisted in ``metadata`` and ``drain`` starts after ``max(since,
    ack_cursor)`` so retained acked rows can still provide a short
    idempotency window without replaying after daemon restart.
    """

    def __init__(self, db_path: Path, *, max_rows: int = DEFAULT_MAX_ROWS):
        self.db_path = Path(db_path)
        self.max_rows = max(1, int(max_rows or DEFAULT_MAX_ROWS))
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_ingest_cursor "
            "ON events(cursor)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO metadata(key, value) "
            "VALUES('ack_cursor', '0')"
        )
        self._conn = conn
        return self

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
                    "INSERT INTO events(idempotency_key, event_json, appended_at) "
                    "VALUES (?, ?, ?)",
                    (key, event_json, appended_at),
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
            }

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
        total = int(self.conn.execute(
            "SELECT COUNT(*) AS c FROM events").fetchone()["c"])
        overflow = max(0, total - self.max_rows)
        if overflow <= 0:
            return 0
        cur = self.conn.execute(
            "DELETE FROM events WHERE cursor IN ("
            "SELECT cursor FROM events WHERE cursor <= ? "
            "ORDER BY cursor LIMIT ?) ",
            (ack_cursor, overflow),
        )
        return int(cur.rowcount or 0)
