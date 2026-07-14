"""MCP reliability, idempotency, and command-capture persistence."""

import json
import logging
import sqlite3
import time

from torque.mcp_idempotency import (
    MCP_IDEMPOTENCY_FULL_TTL_SECONDS,
    MCP_IDEMPOTENCY_MAINTENANCE_INTERVAL_SECONDS,
    MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
    collect_mcp_idempotency_storage_stats,
    compacted_idempotency_response,
    json_response_bytes,
    maintain_mcp_idempotency_storage,
)
from torque.task_ids import normalize_group_prefix


log = logging.getLogger("torque")


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """Return whether *exc* represents SQLite lock contention."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def sqlite_retry_backoff(attempt: int) -> float:
    """Return the bounded retry delay for a 1-based write attempt."""
    backoffs = (0.01, 0.025, 0.05)
    return backoffs[min(max(0, attempt - 1), len(backoffs) - 1)]


# Keep the implementation names used by the extracted methods private while
# exporting descriptive helpers for compatibility from ``torque.db``.
_is_sqlite_lock_error = is_sqlite_lock_error
_sqlite_retry_backoff = sqlite_retry_backoff


class ReliabilityPersistenceMixin:
    """Persist reliability receipts, replay queues, and health events."""

    def load_mcp_idempotency(self, idempotency_key: str) -> dict | None:
        """Load a stored MCP idempotency response by key."""
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        row = self._conn.execute(
            "SELECT idempotency_key, surface, tool_name, request_hash, "
            "response_json, response_bytes, expires_at, compacted_at, "
            "created_at, updated_at "
            "FROM mcp_idempotency WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if not row:
            return None
        return {
            "idempotency_key": row[0],
            "surface": row[1],
            "tool_name": row[2],
            "request_hash": row[3],
            "response_json": row[4],
            "response_bytes": row[5],
            "expires_at": row[6],
            "compacted_at": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    def _maybe_maintain_mcp_idempotency(self, *, now: float | None = None) -> None:
        """Run bounded receipt compaction opportunistically after writes."""
        now_value = float(time.time() if now is None else now)
        if (
            now_value - self._last_mcp_idempotency_maintenance_at
            < MCP_IDEMPOTENCY_MAINTENANCE_INTERVAL_SECONDS
        ):
            return
        self._last_mcp_idempotency_maintenance_at = now_value
        try:
            summary = self.maintain_mcp_idempotency(now=now_value)
            if summary.get("compacted") or summary.get("deleted"):
                log.info(
                    "mcp idempotency maintenance compacted=%s deleted=%s",
                    summary.get("compacted", 0),
                    summary.get("deleted", 0),
                )
        except Exception:
            log.exception("MCP idempotency maintenance failed")

    def maintain_mcp_idempotency(self, **kwargs) -> dict:
        """Compact/delete idempotency receipts in bounded batches."""
        return maintain_mcp_idempotency_storage(self._conn, **kwargs)

    def mcp_idempotency_storage_stats(self) -> dict:
        """Return logical/table-size stats for doctor and metrics surfaces."""
        return collect_mcp_idempotency_storage_stats(self._conn)

    def save_mcp_idempotency(
        self,
        *,
        idempotency_key: str,
        surface: str,
        tool_name: str,
        request_hash: str,
        response: dict,
    ) -> None:
        """Persist one idempotent MCP write response."""
        key = str(idempotency_key or "").strip()
        if not key:
            return
        now = time.time()
        surface_value = str(surface or "")
        tool_value = str(tool_name or "")
        response_json = json.dumps(response or {}, separators=(",", ":"))
        original_response_bytes = json_response_bytes(response_json)
        compacted_at = 0.0
        if original_response_bytes > MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES:
            compacted_at = now
            response_json = json.dumps(
                compacted_idempotency_response(
                    surface=surface_value,
                    tool_name=tool_value,
                    reason="oversize",
                    original_response_bytes=original_response_bytes,
                    max_response_bytes=MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
                ),
                separators=(",", ":"),
            )
        response_bytes = json_response_bytes(response_json)
        existing = self.load_mcp_idempotency(key)
        created_at = float((existing or {}).get("created_at", 0) or now)
        expires_at = (
            now if compacted_at else now + MCP_IDEMPOTENCY_FULL_TTL_SECONDS
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO mcp_idempotency "
            "(idempotency_key, surface, tool_name, request_hash, "
            "response_json, response_bytes, expires_at, compacted_at, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                surface_value,
                tool_value,
                str(request_hash or ""),
                response_json,
                response_bytes,
                expires_at,
                compacted_at,
                created_at,
                now,
            ),
        )
        self._conn.commit()
        self._maybe_maintain_mcp_idempotency(now=now)

    def load_command_receipt(self, idempotency_key: str) -> dict | None:
        """Load one internal command receipt by idempotency key."""
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        row = self._conn.execute(
            "SELECT idempotency_key, surface, command_name, request_hash, "
            "response_json, created_at, updated_at "
            "FROM command_receipts WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if not row:
            return None
        try:
            response = json.loads(row[4] or "null")
        except (json.JSONDecodeError, TypeError):
            response = None
        return {
            "idempotency_key": row[0],
            "surface": row[1],
            "command_name": row[2],
            "request_hash": row[3],
            "response_json": row[4],
            "response": response,
            "created_at": row[5],
            "updated_at": row[6],
        }

    def save_command_receipt(
        self,
        *,
        idempotency_key: str,
        surface: str,
        command_name: str,
        request_hash: str,
        response,
    ) -> None:
        """Persist one internal command receipt."""
        key = str(idempotency_key or "").strip()
        if not key:
            return
        now = time.time()
        existing = self.load_command_receipt(key)
        created_at = float((existing or {}).get("created_at", 0) or now)
        self._conn.execute(
            "INSERT OR REPLACE INTO command_receipts "
            "(idempotency_key, surface, command_name, request_hash, "
            "response_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                key,
                str(surface or ""),
                str(command_name or ""),
                str(request_hash or ""),
                json.dumps(response, separators=(",", ":")),
                created_at,
                now,
            ),
        )
        self._conn.commit()

    def enqueue_failed_write(
        self,
        *,
        idempotency_key: str,
        endpoint: str,
        method: str = "POST",
        surface: str = "",
        tool_name: str = "",
        caller_id: str = "",
        payload: dict | None = None,
        attempts: int = 0,
        last_error: str = "",
    ) -> None:
        """Persist or update a failed idempotent write for boot-time replay."""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key is required")
        now = time.time()
        row = self._conn.execute(
            "SELECT created_at FROM failed_writes WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        created_at = float(row[0]) if row else now
        self._conn.execute(
            "INSERT OR REPLACE INTO failed_writes "
            "(idempotency_key, endpoint, method, surface, tool_name, "
            "caller_id, payload_json, created_at, updated_at, attempts, last_error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                str(endpoint or ""),
                str(method or "POST"),
                str(surface or ""),
                str(tool_name or ""),
                str(caller_id or ""),
                json.dumps(payload or {}, separators=(",", ":")),
                created_at,
                now,
                int(attempts or 0),
                str(last_error or ""),
            ),
        )
        self._conn.commit()

    def load_failed_writes(self, *, limit: int = 100) -> list[dict]:
        """Load queued failed writes oldest-first."""
        rows = self._conn.execute(
            "SELECT id, idempotency_key, endpoint, method, surface, tool_name, "
            "caller_id, payload_json, created_at, updated_at, attempts, last_error "
            "FROM failed_writes ORDER BY created_at ASC, id ASC LIMIT ?",
            (max(1, int(limit or 100)),),
        ).fetchall()
        writes = []
        for row in rows:
            try:
                payload = json.loads(row[7] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            writes.append({
                "id": row[0],
                "idempotency_key": row[1],
                "endpoint": row[2],
                "method": row[3],
                "surface": row[4],
                "tool_name": row[5],
                "caller_id": row[6],
                "payload": payload if isinstance(payload, dict) else {},
                "created_at": row[8],
                "updated_at": row[9],
                "attempts": row[10],
                "last_error": row[11],
            })
        return writes

    def delete_failed_write(self, failed_write_id) -> None:
        self._conn.execute(
            "DELETE FROM failed_writes WHERE id=?",
            (int(failed_write_id or 0),),
        )
        self._conn.commit()

    def delete_failed_write_by_key(self, idempotency_key: str) -> None:
        key = str(idempotency_key or "").strip()
        if not key:
            return
        self._conn.execute(
            "DELETE FROM failed_writes WHERE idempotency_key=?",
            (key,),
        )
        self._conn.commit()

    def mark_failed_write_attempt(self, failed_write_id, last_error: str) -> None:
        self._conn.execute(
            "UPDATE failed_writes SET attempts=attempts+1, updated_at=?, "
            "last_error=? WHERE id=?",
            (time.time(), str(last_error or ""), int(failed_write_id or 0)),
        )
        self._conn.commit()

    def persist_command_capture(
        self,
        *,
        agents: dict[str, object] | None = None,
        deleted_agents: set[str] | None = None,
        tasks: dict[str, object] | None = None,
        deleted_tasks: set[str] | None = None,
        task_id_counters: dict[str, int] | None = None,
        pipeline_task_counters: dict[str, int] | None = None,
        task_id_aliases: dict[str, str] | None = None,
        idempotency_key: str,
        surface: str,
        command_name: str,
        request_hash: str,
        response,
        delete_failed_write_key: str = "",
    ) -> None:
        """Persist one captured critical command snapshot atomically."""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key is required")
        now = time.time()
        cursor = self._conn.cursor()
        try:
            receipt_row = cursor.execute(
                "SELECT created_at FROM command_receipts WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            created_at = float(receipt_row[0]) if receipt_row else now

            for agent_id in sorted(deleted_agents or ()):
                cursor.execute(
                    "DELETE FROM agent_message_history WHERE agent_id=?",
                    (agent_id,),
                )
                cursor.execute("DELETE FROM agents WHERE id=?", (agent_id,))
                cursor.execute(
                    "DELETE FROM group_members WHERE agent_id=?",
                    (agent_id,),
                )
            for _agent_id, cell in sorted((agents or {}).items()):
                self._insert_agent_row(cursor, cell)

            for task_id in sorted(deleted_tasks or ()):
                cursor.execute("DELETE FROM board_tasks WHERE id=?", (task_id,))
            for _task_id, task in sorted((tasks or {}).items()):
                self._insert_board_task_row(cursor, task)

            for legacy_id, task_id in sorted((task_id_aliases or {}).items()):
                if not legacy_id or not task_id:
                    continue
                cursor.execute(
                    "INSERT OR REPLACE INTO task_id_aliases (legacy_id, task_id) "
                    "VALUES (?, ?)",
                    (legacy_id, task_id),
                )

            for group_prefix, next_root_number in sorted(
                (task_id_counters or {}).items()
            ):
                cursor.execute(
                    "INSERT OR REPLACE INTO task_id_counters "
                    "(group_prefix, next_root_number) VALUES (?, ?)",
                    (
                        normalize_group_prefix(group_prefix),
                        max(1, int(next_root_number or 1)),
                    ),
                )

            for root_task_id, next_child_number in sorted(
                (pipeline_task_counters or {}).items()
            ):
                if not root_task_id:
                    continue
                cursor.execute(
                    "INSERT OR REPLACE INTO pipeline_task_counters "
                    "(root_task_id, next_child_number) VALUES (?, ?)",
                    (root_task_id, max(1, int(next_child_number or 1))),
                )

            cursor.execute(
                "INSERT OR REPLACE INTO command_receipts "
                "(idempotency_key, surface, command_name, request_hash, "
                "response_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    key,
                    str(surface or ""),
                    str(command_name or ""),
                    str(request_hash or ""),
                    json.dumps(response, separators=(",", ":")),
                    created_at,
                    now,
                ),
            )
            delete_key = str(delete_failed_write_key or "").strip()
            if delete_key:
                cursor.execute(
                    "DELETE FROM failed_writes WHERE idempotency_key=?",
                    (delete_key,),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def record_mcp_health_event(
        self,
        *,
        surface: str,
        tool_name: str = "",
        event: str,
        error: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO mcp_health_events "
            "(timestamp, surface, tool_name, event, error) VALUES (?,?,?,?,?)",
            (
                time.time(),
                str(surface or ""),
                str(tool_name or ""),
                str(event or ""),
                str(error or "")[:500],
            ),
        )
        self._conn.commit()

    def record_mcp_health_event_safe(
        self,
        *,
        surface: str,
        tool_name: str = "",
        event: str,
        error: str = "",
    ) -> None:
        try:
            self.record_mcp_health_event(
                surface=surface,
                tool_name=tool_name,
                event=event,
                error=error,
            )
            return
        except Exception:
            pass
        # Async panel-event flushes run in worker threads where the main
        # sqlite connection is not usable. Fall back to a short-lived
        # connection so health counters are still captured.
        try:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    "INSERT INTO mcp_health_events "
                    "(timestamp, surface, tool_name, event, error) "
                    "VALUES (?,?,?,?,?)",
                    (
                        time.time(),
                        str(surface or ""),
                        str(tool_name or ""),
                        str(event or ""),
                        str(error or "")[:500],
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            log.debug("Failed to record MCP health event", exc_info=True)

    def record_perceived_empty_episode(
        self,
        *,
        timestamp: float,
        cell_id: str,
        group_name: str = "",
        agent_name: str = "",
        session_id: str = "",
        transcript_path: str = "",
        trigger_reason: str = "",
        confidence: str = "",
        threshold_n: int = 0,
        window_seconds: int = 0,
        tool_calls_json: str = "[]",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO perceived_empty_episodes "
            "(timestamp, cell_id, group_name, agent_name, session_id, "
            "transcript_path, trigger_reason, confidence, threshold_n, "
            "window_seconds, tool_calls_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                float(timestamp or time.time()),
                str(cell_id or ""),
                str(group_name or ""),
                str(agent_name or ""),
                str(session_id or ""),
                str(transcript_path or ""),
                str(trigger_reason or "")[:500],
                str(confidence or "")[:50],
                int(threshold_n or 0),
                int(window_seconds or 0),
                str(tool_calls_json or "[]"),
                time.time(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def record_perceived_empty_episode_safe(self, **kwargs) -> int:
        try:
            return self.record_perceived_empty_episode(**kwargs)
        except Exception:
            pass
        try:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.execute(
                    "INSERT INTO perceived_empty_episodes "
                    "(timestamp, cell_id, group_name, agent_name, session_id, "
                    "transcript_path, trigger_reason, confidence, threshold_n, "
                    "window_seconds, tool_calls_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        float(kwargs.get("timestamp") or time.time()),
                        str(kwargs.get("cell_id") or ""),
                        str(kwargs.get("group_name") or ""),
                        str(kwargs.get("agent_name") or ""),
                        str(kwargs.get("session_id") or ""),
                        str(kwargs.get("transcript_path") or ""),
                        str(kwargs.get("trigger_reason") or "")[:500],
                        str(kwargs.get("confidence") or "")[:50],
                        int(kwargs.get("threshold_n") or 0),
                        int(kwargs.get("window_seconds") or 0),
                        str(kwargs.get("tool_calls_json") or "[]"),
                        time.time(),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()
        except Exception:
            log.debug("Failed to record perceived-empty episode", exc_info=True)
            return 0

    def load_perceived_empty_episodes(
        self,
        *,
        cell_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        clauses = []
        params: list = []
        if str(cell_id or "").strip():
            clauses.append("cell_id = ?")
            params.append(str(cell_id or "").strip())
        try:
            row_limit = max(1, min(500, int(limit or 50)))
        except (TypeError, ValueError):
            row_limit = 50
        sql = (
            "SELECT id, timestamp, cell_id, group_name, agent_name, "
            "session_id, transcript_path, trigger_reason, confidence, "
            "threshold_n, window_seconds, tool_calls_json, created_at "
            "FROM perceived_empty_episodes"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params.append(row_limit)
        rows = self._conn.execute(sql, params).fetchall()
        episodes = []
        for row in rows:
            d = {
                "id": row[0],
                "timestamp": row[1],
                "cell_id": row[2],
                "group_name": row[3],
                "agent_name": row[4],
                "session_id": row[5],
                "transcript_path": row[6],
                "trigger_reason": row[7],
                "confidence": row[8],
                "threshold_n": row[9],
                "window_seconds": row[10],
                "tool_calls_json": row[11],
                "created_at": row[12],
            }
            try:
                d["tool_calls"] = json.loads(row[11] or "[]")
            except (json.JSONDecodeError, TypeError):
                d["tool_calls"] = []
            episodes.append(d)
        return episodes

    def _record_reliability_event_safe(
        self,
        *,
        surface: str,
        event: str,
        error: str = "",
    ) -> None:
        self.record_mcp_health_event_safe(
            surface=surface,
            event=event,
            error=error,
        )

    def _rollback_after_failed_write(self) -> None:
        try:
            self._conn.rollback()
        except Exception:
            log.debug("Failed to rollback SQLite write before retry", exc_info=True)

    def _run_sqlite_write_with_lock_retry(
        self,
        operation,
        *,
        surface: str,
        attempts: int = 4,
    ):
        attempts = max(1, int(attempts or 1))
        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except Exception as exc:
                if _is_sqlite_lock_error(exc):
                    # A lock can surface after SQLite has staged writes but
                    # before commit. Roll back before recording health or
                    # sleeping; otherwise the health-counter commit can commit
                    # the partially failed payload and retry duplicates it.
                    self._rollback_after_failed_write()
                    if attempt >= attempts:
                        self._record_reliability_event_safe(
                            surface=surface,
                            event="drop",
                            error=str(exc),
                        )
                        raise
                    self._record_reliability_event_safe(
                        surface=surface,
                        event="retry",
                        error=str(exc),
                    )
                    time.sleep(_sqlite_retry_backoff(attempt))
                    continue
                raise

    def load_mcp_health_summary(self, *, since: float = 0) -> dict:
        """Return counts of recent MCP retry/drop/dedupe/replay events."""
        try:
            since_value = float(since or 0)
        except (TypeError, ValueError):
            since_value = 0.0
        rows = self._conn.execute(
            "SELECT surface, tool_name, event, COUNT(*) "
            "FROM mcp_health_events WHERE timestamp >= ? "
            "GROUP BY surface, tool_name, event "
            "ORDER BY surface, tool_name, event",
            (since_value,),
        ).fetchall()
        totals: dict[str, int] = {}
        surfaces: dict[str, dict] = {}
        for surface, tool_name, event, count in rows:
            surface = str(surface or "mcp")
            tool_name = str(tool_name or "")
            event = str(event or "")
            count = int(count or 0)
            totals[event] = totals.get(event, 0) + count
            surface_entry = surfaces.setdefault(
                surface,
                {"events": {}, "tools": {}},
            )
            surface_entry["events"][event] = (
                surface_entry["events"].get(event, 0) + count
            )
            if tool_name:
                tool_entry = surface_entry["tools"].setdefault(tool_name, {})
                tool_entry[event] = tool_entry.get(event, 0) + count
        pending_failed_writes = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM failed_writes"
            ).fetchone()[0]
            or 0
        )
        return {
            "since": since_value,
            "totals": totals,
            "surfaces": surfaces,
            "pending_failed_writes": pending_failed_writes,
        }
