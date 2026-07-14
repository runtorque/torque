"""Durable panel-event and performance-rollup telemetry persistence."""

import sqlite3
import time

from torque import profiling


class TelemetryPersistenceMixin:
    """Persist operator panel events and bounded performance rollups."""

    @staticmethod
    def _panel_event_insert_values(evt: dict) -> tuple:
        return (
            evt["id"], evt["timestamp"], evt["kind"],
            evt.get("cell_id", ""), evt.get("agent_name", ""),
            evt.get("group", ""), evt.get("message", ""),
            evt.get("task_id", ""),
        )

    @staticmethod
    def _panel_event_update_values(evt: dict) -> tuple:
        return (
            evt["timestamp"], evt["kind"], evt.get("cell_id", ""),
            evt.get("agent_name", ""), evt.get("group", ""),
            evt.get("message", ""), evt.get("task_id", ""),
            evt["id"],
        )

    @staticmethod
    def _save_panel_events_batch_on_conn(
        conn: sqlite3.Connection,
        events: list[dict],
        updates: list[dict],
        max_size: int | None,
    ) -> int:
        inserted = 0
        updated = 0
        try:
            if events:
                conn.executemany(
                    "INSERT OR REPLACE INTO panel_events "
                    "(id, timestamp, kind, cell_id, agent_name, group_name, "
                    "message, task_id) VALUES (?,?,?,?,?,?,?,?)",
                    [TelemetryPersistenceMixin._panel_event_insert_values(evt) for evt in events],
                )
                inserted = len(events)
            if updates:
                conn.executemany(
                    "UPDATE panel_events SET timestamp=?, kind=?, cell_id=?, "
                    "agent_name=?, group_name=?, message=?, task_id=? WHERE id=?",
                    [TelemetryPersistenceMixin._panel_event_update_values(evt) for evt in updates],
                )
                updated = len(updates)
            if max_size is not None:
                conn.execute(
                    "DELETE FROM panel_events WHERE id NOT IN "
                    "(SELECT id FROM panel_events ORDER BY id DESC LIMIT ?)",
                    (max(0, int(max_size)),),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return inserted + updated

    def save_panel_event(self, evt: dict) -> int:
        """Insert a panel event and return the assigned row ID."""
        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_save_panel_event_ms"):
            self._conn.execute(
                "INSERT INTO panel_events "
                "(id, timestamp, kind, cell_id, agent_name, group_name, "
                "message, task_id) VALUES (?,?,?,?,?,?,?,?)",
                self._panel_event_insert_values(evt))
            self._conn.commit()
        return evt["id"]

    def save_panel_events_batch(
        self,
        events: list[dict],
        updates: list[dict] | None = None,
        max_size: int | None = None,
        *,
        separate_connection: bool = False,
    ) -> int:
        """Persist panel event inserts/updates in one transaction.

        ``separate_connection`` is used by the async panel-event flush path so
        SQLite I/O can run off the event loop without sharing the main
        connection across threads.
        """
        events = list(events or [])
        updates = list(updates or [])
        if not events and not updates and max_size is None:
            return 0
        def _operation():
            if separate_connection:
                conn = sqlite3.connect(str(self.db_path))
                try:
                    conn.execute("PRAGMA foreign_keys=ON")
                    return self._save_panel_events_batch_on_conn(
                        conn, events, updates, max_size)
                finally:
                    conn.close()
            return self._save_panel_events_batch_on_conn(
                self._conn, events, updates, max_size)

        return self._run_sqlite_write_with_lock_retry(
            _operation,
            surface="panel_events",
        )

    def update_panel_event(self, evt: dict):
        """Update an existing panel event row."""
        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_update_panel_event_ms"):
            self._conn.execute(
                "UPDATE panel_events SET timestamp=?, kind=?, cell_id=?, "
                "agent_name=?, group_name=?, message=?, task_id=? WHERE id=?",
                self._panel_event_update_values(evt))
            self._conn.commit()

    def trim_panel_events(self, max_size: int):
        """Delete oldest events beyond *max_size*."""
        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_trim_panel_events_ms"):
            self._conn.execute(
                "DELETE FROM panel_events WHERE id NOT IN "
                "(SELECT id FROM panel_events ORDER BY id DESC LIMIT ?)",
                (max_size,))
            self._conn.commit()

    def load_panel_events(self, limit: int = 50,
                          before_id: int = 0,
                          cell_id: str = "") -> list[dict]:
        """Load a page of panel events ordered by id DESC.

        Returns dicts with 'group' key (matching in-memory format).
        """
        clauses = []
        params = []
        if before_id:
            clauses.append("id < ?")
            params.append(before_id)
        if cell_id:
            clauses.append("cell_id = ?")
            params.append(cell_id)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        params.append(limit)
        rows = self._conn.execute(
            "SELECT id, timestamp, kind, cell_id, agent_name, "
            "group_name, message, task_id FROM panel_events "
            f"{where}ORDER BY id DESC LIMIT ?",
            tuple(params)).fetchall()
        events = []
        for r in rows:
            events.append({
                "id": r[0], "timestamp": r[1], "kind": r[2],
                "cell_id": r[3], "agent_name": r[4], "group": r[5],
                "message": r[6], "task_id": r[7],
            })
        # Return in ascending id order (oldest first)
        events.reverse()
        return events

    def load_panel_events_window(
        self,
        since: float,
        until: float,
        group: str = "",
        kinds: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[dict]:
        """Load durable panel events in a timestamp window, oldest first.

        This intentionally does not apply the recent-events ring-buffer cap:
        health/observability panels need bounded historical reads over the
        selected window rather than the latest N UI events.
        """
        clauses = ["timestamp >= ?", "timestamp <= ?"]
        params: list = [float(since or 0.0), float(until or 0.0)]
        group = str(group or "").strip()
        if group:
            clauses.append("group_name = ?")
            params.append(group)
        clean_kinds = [
            str(kind or "").strip()
            for kind in (kinds or [])
            if str(kind or "").strip()
        ]
        if clean_kinds:
            placeholders = ",".join(["?"] * len(clean_kinds))
            clauses.append(f"kind IN ({placeholders})")
            params.extend(clean_kinds)
        rows = self._conn.execute(
            "SELECT id, timestamp, kind, cell_id, agent_name, "
            "group_name, message, task_id FROM panel_events "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY timestamp ASC, id ASC",
            tuple(params),
        ).fetchall()
        return [
            {
                "id": r[0],
                "timestamp": r[1],
                "kind": r[2],
                "cell_id": r[3],
                "agent_name": r[4],
                "group": r[5],
                "message": r[6],
                "task_id": r[7],
            }
            for r in rows
        ]

    def save_metrics_perf_rollup(self, row: dict):
        """Upsert one bounded-retention perf rollup sample.

        Multiple aggregation ticks land in the same minute bucket.  Store rates
        and gauges as running averages while keeping percentile-like fields as
        bucket maxima so short spikes remain visible in history.
        """
        row = dict(row or {})
        bucket_start = int(row.get("bucket_start", 0) or 0)
        bucket_seconds = int(row.get("bucket_seconds", 60) or 60)
        if not bucket_start or bucket_seconds <= 0:
            return
        now = float(row.get("updated_at", time.time()) or time.time())
        sample_count = max(1, int(row.get("sample_count", 1) or 1))
        existing = self._conn.execute(
            "SELECT sample_count, event_loop_lag_p95_ms, ws_deltas_per_s, "
            "db_write_latency_p95_ms, rss_mb, cpu_pct, created_at "
            "FROM metrics_perf_rollups "
            "WHERE bucket_start=? AND bucket_seconds=?",
            (bucket_start, bucket_seconds),
        ).fetchone()
        if not existing:
            self._conn.execute(
                "INSERT OR REPLACE INTO metrics_perf_rollups "
                "(bucket_start, bucket_seconds, sample_count, "
                "event_loop_lag_p95_ms, ws_deltas_per_s, "
                "db_write_latency_p95_ms, rss_mb, cpu_pct, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    bucket_start,
                    bucket_seconds,
                    sample_count,
                    float(row.get("event_loop_lag_p95_ms", 0.0) or 0.0),
                    float(row.get("ws_deltas_per_s", 0.0) or 0.0),
                    float(row.get("db_write_latency_p95_ms", 0.0) or 0.0),
                    float(row.get("rss_mb", 0.0) or 0.0),
                    float(row.get("cpu_pct", 0.0) or 0.0),
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return
        old_count = max(0, int(existing[0] or 0))
        new_count = old_count + sample_count

        def avg(old_value, new_value):
            return (
                (float(old_value or 0.0) * old_count)
                + (float(new_value or 0.0) * sample_count)
            ) / max(1, new_count)

        self._conn.execute(
            "UPDATE metrics_perf_rollups SET "
            "sample_count=?, event_loop_lag_p95_ms=?, ws_deltas_per_s=?, "
            "db_write_latency_p95_ms=?, rss_mb=?, cpu_pct=?, updated_at=? "
            "WHERE bucket_start=? AND bucket_seconds=?",
            (
                new_count,
                max(
                    float(existing[1] or 0.0),
                    float(row.get("event_loop_lag_p95_ms", 0.0) or 0.0),
                ),
                avg(existing[2], row.get("ws_deltas_per_s", 0.0)),
                max(
                    float(existing[3] or 0.0),
                    float(row.get("db_write_latency_p95_ms", 0.0) or 0.0),
                ),
                avg(existing[4], row.get("rss_mb", 0.0)),
                avg(existing[5], row.get("cpu_pct", 0.0)),
                now,
                bucket_start,
                bucket_seconds,
            ),
        )
        self._conn.commit()

    def load_metrics_perf_rollups(
        self,
        since: float,
        until: float,
    ) -> list[dict]:
        """Load perf rollups whose bucket_start falls in [since, until)."""
        rows = self._conn.execute(
            "SELECT bucket_start, bucket_seconds, sample_count, "
            "event_loop_lag_p95_ms, ws_deltas_per_s, "
            "db_write_latency_p95_ms, rss_mb, cpu_pct, created_at, updated_at "
            "FROM metrics_perf_rollups "
            "WHERE bucket_start >= ? AND bucket_start < ? "
            "ORDER BY bucket_start ASC",
            (int(float(since or 0.0)), int(float(until or 0.0))),
        ).fetchall()
        cols = [
            "bucket_start",
            "bucket_seconds",
            "sample_count",
            "event_loop_lag_p95_ms",
            "ws_deltas_per_s",
            "db_write_latency_p95_ms",
            "rss_mb",
            "cpu_pct",
            "created_at",
            "updated_at",
        ]
        return [dict(zip(cols, row)) for row in rows]

    def trim_metrics_perf_rollups(self, cutoff: float):
        self._conn.execute(
            "DELETE FROM metrics_perf_rollups WHERE bucket_start < ?",
            (int(float(cutoff or 0.0)),),
        )
        self._conn.commit()

    def get_panel_event_max_id(self) -> int:
        """Return the highest panel_events id, or 0 if empty."""
        row = self._conn.execute(
            "SELECT MAX(id) FROM panel_events").fetchone()
        return row[0] if row and row[0] is not None else 0
