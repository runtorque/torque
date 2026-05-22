"""SQLite persistence layer for Torque state.

Used by:
- The daemon (write path): save persistent fields on mutation
- The CLI (read path): direct SQLite reads for status/task queries

Uses synchronous sqlite3 — the daemon is single-threaded asyncio and
current save() already does sync file I/O.  Single-row upserts are faster
than json.dumps() + write_text().
"""

import asyncio
import copy
import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from torque import __version__
from torque.config import ATTACHMENTS_DIR
from torque import profiling
from torque.db_board import (
    BoardPersistenceMixin,
    decode_auto_dispatch_queue_rows,
    decode_board_task_row,
    insert_board_task,
)
from torque.db_memory import MemoryPersistenceMixin
from torque.db_schema import initialize_database
from torque.task_ids import (
    format_derived_task_id,
    format_root_task_id,
    is_canonical_task_id,
    normalize_group_prefix,
    parse_task_id,
)

log = logging.getLogger("torque")


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


def _sqlite_retry_backoff(attempt: int) -> float:
    backoffs = (0.01, 0.025, 0.05)
    return backoffs[min(max(0, attempt - 1), len(backoffs) - 1)]


def _group_settings_field_names() -> set[str] | None:
    """Return the current GroupSettings dataclass field names when available."""
    state_mod = sys.modules.get("torque.state")
    group_settings_cls = getattr(state_mod, "GroupSettings", None)
    if group_settings_cls is None:
        try:
            from torque.state import GroupSettings as group_settings_cls
        except Exception:
            return None
    return set(getattr(group_settings_cls, "__dataclass_fields__", {}) or {})

_KINDS_SCHEMA_MIGRATION_VERSION = 1
_KINDS_BACKFILL_MIGRATION_VERSION = 2
_KINDS_CLEANUP_MIGRATION_VERSION = 3
_KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION = 4
_KINDS_SCHEMA_MIGRATION_VERSION_KEY = "schema_kinds_migration_version"
_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY = "schema_kinds_migration_migrated_at"
_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY = "schema_kinds_task_assignment_fixup_applied"
_KINDS_SCHEMA_BACKUP_NAME = "torque.db.pre-kinds.bak"
_KINDS_ENGINEER_OVERRIDE_ENV = "TORQUE_MIGRATE_ENGINEER_ID"
_KINDS_ENGINEER_GROUP = "torque"
_KINDS_ENGINEER_NAME = "Engineer"
_AGENT_ACTIVITY_TS_MIGRATION_VERSION = 1
_AGENT_ACTIVITY_TS_MIGRATION_VERSION_KEY = (
    "schema_agent_activity_timestamps_version"
)

_AGENT_PERSISTED_COLS = [
    "id", "name", "slug", "group_name", "cell_type", "terminal_backend",
    "session_id", "profile",
    "command", "directory", "tab_color", "icon", "window_id",
    "parent_id", "status", "worktree_path", "worktree_branch",
    "worktree_repo_root", "worktree_base_dir", "worktree_base_branch",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "agent_type",
    "agent_session_id",
    "last_progress_at", "last_heartbeat_at", "last_activity_at",
    "session_resume", "idle_timeout",
    "tasks_dispatched",
    "queue_empty_emitted",
    "kind", "role", "owner_engineer_id", "hired_by_architect_id",
    "dismissed_at", "deleted_at", "permanent_delete_after", "persistent",
    "engineer_specializations",
]

_AGENT_INSERT_SQL = """
    INSERT OR REPLACE INTO agents
        ({columns})
    VALUES ({placeholders})
"""

# GroupSettings fields that store dicts/lists — persisted as JSON text.
_GS_JSON_FIELDS = {
    "env_vars", "agent_env_vars", "terminal_env_vars",
    "board_default_labels", "worktree_symlinks",
    "board_sync_github",
    "architect_review_gate_thresholds",
    "architect_enabled_events",
    "default_engineer_specializations",
}

# GroupSettings fields that are booleans — stored as INTEGER 0/1.
_GS_BOOL_FIELDS = {
    "collapsed_default", "filter_by_window", "git_worktree",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "worktree_merge_preserve_diff",
    "agent_session_resume",
    "board_sync_enabled",
    "notifications", "notify_on_finish", "notify_on_error",
    "notify_on_attention", "terminal_always_custom_dialog",
    "terminal_close_on_disconnect", "architect_suppress_empty_digests",
}

_AGENT_PEER_MESSAGE_COLUMNS = [
    "id",
    "thread_id",
    "reply_to_id",
    "group_name",
    "sender_id",
    "sender_kind",
    "sender_name",
    "recipient_id",
    "recipient_kind",
    "recipient_name",
    "message",
    "message_type",
    "created_at",
    "ack_required",
    "blocking",
    "source_task_id",
    "context_task_ids",
    "context_engineer_ids",
    "context_decision_ids",
    "context_summary",
    "context_snapshot",
    "delivery_state",
    "delivery_reason",
    "delivered_at",
    "read_at",
    "archived_at",
]
_AGENT_PEER_MESSAGE_JSON_LIST_FIELDS = (
    "context_task_ids",
    "context_engineer_ids",
    "context_decision_ids",
)
_AGENT_PEER_MESSAGE_DELIVERY_STATES = {"buffered", "delivered", "failed"}
_AGENT_PEER_MESSAGE_NON_USER_WHERE = (
    "(sender_kind!='user' AND recipient_kind!='user' "
    "AND message_type='message' AND blocking=0)"
)
_AGENT_DIRECT_MESSAGE_WHERE = (
    "(sender_kind='user' OR recipient_kind='user' "
    "OR message_type!='message' OR blocking!=0)"
)
_AGENT_PEER_MESSAGE_USER_WHERE = _AGENT_DIRECT_MESSAGE_WHERE


def _json_text_list(value) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value or "[]")
        except (json.JSONDecodeError, TypeError):
            decoded = []
    else:
        decoded = value
    if not isinstance(decoded, list):
        return []
    out: list[str] = []
    for item in decoded:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _json_text_dict(value) -> dict:
    if isinstance(value, str):
        try:
            decoded = json.loads(value or "{}")
        except (json.JSONDecodeError, TypeError):
            decoded = {}
    else:
        decoded = value
    return decoded if isinstance(decoded, dict) else {}


def _peer_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default or 0.0)


def _peer_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def canonical_user_agent_thread_id(agent_id: str, *, user_id: str = "user") -> str:
    """Return the canonical V1 thread id for one user↔agent lane.

    The V1 direct-message panel renders one conversation per viewed agent.
    Rows involving the synthetic user participant are therefore grouped under
    this stable thread id.  Agent↔agent peer messages keep their existing
    caller-supplied/default message-id threads.
    """
    aid = str(agent_id or "").strip()
    uid = str(user_id or "user").strip() or "user"
    if not aid:
        return ""
    return f"user-agent:{uid}:{aid}"


def _direct_message_thread_id_for_participants(source: dict) -> str:
    sender_kind = str(source.get("sender_kind", "") or "").strip()
    recipient_kind = str(source.get("recipient_kind", "") or "").strip()
    if sender_kind == "user" and recipient_kind != "user":
        return canonical_user_agent_thread_id(
            source.get("recipient_id", ""),
            user_id=str(source.get("sender_id", "") or "user"),
        )
    if recipient_kind == "user" and sender_kind != "user":
        return canonical_user_agent_thread_id(
            source.get("sender_id", ""),
            user_id=str(source.get("recipient_id", "") or "user"),
        )
    return ""


def _normalize_agent_peer_message_record(record: dict) -> dict:
    source = dict(record or {})
    message_id = str(source.get("id", "") or "").strip()
    if not message_id:
        raise ValueError("id is required")
    message = str(source.get("message", "") or "")
    if not message:
        raise ValueError("message is required")
    sender_id = str(source.get("sender_id", "") or "").strip()
    recipient_id = str(source.get("recipient_id", "") or "").strip()
    if not sender_id:
        raise ValueError("sender_id is required")
    if not recipient_id:
        raise ValueError("recipient_id is required")
    created_at = _peer_float(
        source.get("created_at", source.get("timestamp", time.time())),
        time.time(),
    )
    delivery_state = str(
        source.get("delivery_state", "buffered") or "buffered"
    ).strip()
    if delivery_state not in _AGENT_PEER_MESSAGE_DELIVERY_STATES:
        delivery_state = "buffered"
    direct_thread_id = _direct_message_thread_id_for_participants(source)
    thread_id = direct_thread_id or str(source.get("thread_id", "") or "").strip()
    if not thread_id:
        thread_id = message_id
    group_name = str(
        source.get("group_name", source.get("group", "")) or ""
    ).strip()
    context_snapshot = _json_text_dict(source.get("context_snapshot", {}))
    return {
        "id": message_id,
        "thread_id": thread_id,
        "reply_to_id": str(source.get("reply_to_id", "") or "").strip(),
        "group_name": group_name,
        "sender_id": sender_id,
        "sender_kind": str(
            source.get("sender_kind", "architect") or "architect"
        ).strip(),
        "sender_name": str(source.get("sender_name", "") or ""),
        "recipient_id": recipient_id,
        "recipient_kind": str(
            source.get("recipient_kind", "architect") or "architect"
        ).strip(),
        "recipient_name": str(source.get("recipient_name", "") or ""),
        "message": message,
        "message_type": str(
            source.get("message_type", "message") or "message"
        ).strip() or "message",
        "created_at": created_at,
        "ack_required": _peer_bool(source.get("ack_required", False)),
        "blocking": _peer_bool(source.get("blocking", False)),
        "source_task_id": str(source.get("source_task_id", "") or "").strip(),
        "context_task_ids": _json_text_list(
            source.get("context_task_ids", [])
        ),
        "context_engineer_ids": _json_text_list(
            source.get("context_engineer_ids", [])
        ),
        "context_decision_ids": _json_text_list(
            source.get("context_decision_ids", [])
        ),
        "context_summary": str(source.get("context_summary", "") or ""),
        "context_snapshot": context_snapshot,
        "delivery_state": delivery_state,
        "delivery_reason": str(source.get("delivery_reason", "") or ""),
        "delivered_at": _peer_float(source.get("delivered_at", 0), 0),
        "read_at": _peer_float(source.get("read_at", 0), 0),
        "archived_at": _peer_float(source.get("archived_at", 0), 0),
    }


def _agent_peer_message_insert_values(record: dict) -> tuple:
    normalized = _normalize_agent_peer_message_record(record)
    values = []
    for column in _AGENT_PEER_MESSAGE_COLUMNS:
        value = normalized[column]
        if column in _AGENT_PEER_MESSAGE_JSON_LIST_FIELDS:
            value = json.dumps(value, separators=(",", ":"))
        elif column == "context_snapshot":
            value = json.dumps(value, separators=(",", ":"), sort_keys=True)
        elif column in {"ack_required", "blocking"}:
            value = int(bool(value))
        values.append(value)
    return tuple(values)


def _decode_agent_peer_message_row(row, cols=None) -> dict:
    cols = cols or _AGENT_PEER_MESSAGE_COLUMNS
    decoded = dict(zip(cols, row))
    for field in _AGENT_PEER_MESSAGE_JSON_LIST_FIELDS:
        decoded[field] = _json_text_list(decoded.get(field, "[]"))
    decoded["context_snapshot"] = _json_text_dict(
        decoded.get("context_snapshot", "{}")
    )
    decoded["ack_required"] = _peer_bool(decoded.get("ack_required", 0))
    decoded["blocking"] = _peer_bool(decoded.get("blocking", 0))
    decoded["created_at"] = _peer_float(decoded.get("created_at", 0), 0)
    decoded["delivered_at"] = _peer_float(decoded.get("delivered_at", 0), 0)
    decoded["read_at"] = _peer_float(decoded.get("read_at", 0), 0)
    decoded["archived_at"] = _peer_float(decoded.get("archived_at", 0), 0)
    decoded["timestamp"] = decoded["created_at"]
    return decoded


def _serialize_agent_cell(cell):
    d = asdict(cell) if not isinstance(cell, dict) else dict(cell)
    group_name = d.pop("group", d.pop("group_name", ""))
    role = d.get("role", "") or d.get("template", "")
    owner_engineer_id = (
        d.get("owner_engineer_id", "")
        or d.get("created_by_engineer_id", "")
    )
    return (
        d.get("id", ""),
        d.get("name", ""),
        d.get("slug", ""),
        group_name,
        d.get("cell_type", "agent"),
        d.get("terminal_backend", "iterm2"),
        d.get("session_id"),
        d.get("profile", "Default"),
        d.get("command", ""),
        d.get("directory", ""),
        d.get("tab_color", ""),
        d.get("icon", ""),
        d.get("window_id", ""),
        d.get("parent_id", ""),
        d.get("status", "stopped"),
        d.get("worktree_path", ""),
        d.get("worktree_branch", ""),
        d.get("worktree_repo_root", ""),
        d.get("worktree_base_dir", ".torque/worktrees"),
        d.get("worktree_base_branch", ""),
        int(d.get("worktree_auto_checkpoint", False)),
        int(d.get("checkpoint_on_progress", False)),
        int(d.get("worktree_merge_squash", True)),
        d.get("agent_type", ""),
        d.get("agent_session_id", ""),
        float(d.get("last_progress_at", 0) or 0),
        float(d.get("last_heartbeat_at", 0) or 0),
        max(
            float(d.get("last_activity_at", 0) or 0),
            float(d.get("last_progress_at", 0) or 0),
            float(d.get("last_heartbeat_at", 0) or 0),
        ),
        int(d.get("session_resume", True)),
        d.get("idle_timeout", 0),
        d.get("tasks_dispatched", 0),
        int(d.get("queue_empty_emitted", True)),
        d.get("kind", ""),
        role,
        owner_engineer_id,
        d.get("hired_by_architect_id", ""),
        int(d.get("dismissed_at", 0) or 0),
        float(d.get("deleted_at", 0) or 0),
        float(d.get("permanent_delete_after", 0) or 0),
        int(d.get("persistent", 0) or 0),
        json.dumps([
            str(n or "").strip()
            for n in (d.get("engineer_specializations") or [])
            if str(n or "").strip()
        ]),
    )


def _slugify(value: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "unnamed"


def _unique_value(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _decision_json_list(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            value = []
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _decision_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default or 0)


def _mcp_dispatch_response_group(response: dict) -> str:
    """Best-effort group inference for persisted Engineer dispatch responses."""
    if not isinstance(response, dict):
        return ""
    for key in ("group", "group_name", "engineer_group"):
        value = str(response.get(key, "") or "").strip()
        if value:
            return value
    results = response.get("results")
    if not isinstance(results, list):
        return ""
    groups = {
        str(item.get("engineer_group", "") or item.get("group", "") or "").strip()
        for item in results
        if isinstance(item, dict)
        and str(item.get("engineer_group", "") or item.get("group", "") or "").strip()
    }
    return next(iter(groups)) if len(groups) == 1 else ""


def _decode_mcp_response_payload(response: dict) -> dict:
    """Return the tool JSON inside an MCP result wrapper when present."""
    if not isinstance(response, dict):
        return {}
    content = response.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                decoded = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(decoded, dict):
                return decoded
    return response


def _decode_decision_row(row, cols) -> dict:
    decision = dict(zip(cols, row))
    decision["supersedes"] = (
        str(decision.get("supersedes", "") or "").strip() or None
    )
    decision["linked_task_ids"] = _decision_json_list(
        decision.get("linked_task_ids", "[]")
    )
    decision["linked_engineer_ids"] = _decision_json_list(
        decision.get("linked_engineer_ids", "[]")
    )
    decision["archived"] = bool(decision.get("archived", 0))
    decision["created_at"] = _decision_int(decision.get("created_at", 0))
    decision["updated_at"] = _decision_int(decision.get("updated_at", 0))
    return decision


def _decode_pending_hire_row(row, cols) -> dict:
    pending_hire = dict(zip(cols, row))
    pending_hire["created_at"] = _decision_int(
        pending_hire.get("created_at", 0)
    )
    pending_hire["resolved_at"] = _decision_int(
        pending_hire.get("resolved_at", 0)
    )
    return pending_hire


def _digest_event_json(event: dict) -> str:
    """Encode a digest event without EngineerEventBuffer private metadata."""
    payload = {
        str(key): value
        for key, value in dict(event or {}).items()
        if not str(key).startswith("_digest_")
    }
    return json.dumps(payload, separators=(",", ":"))


def _decode_digest_event_json(raw: str) -> dict:
    try:
        event = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        event = {}
    return event if isinstance(event, dict) else {}


def _snapshot_db_payload(value):
    """Copy a payload before it enters an async write queue.

    Hot-path state objects keep mutating after ``_db_save_*`` returns.  The
    queue must persist the value as it looked at enqueue time, not whatever
    the dataclass happens to contain when the background drainer reaches it.
    """
    if hasattr(value, "__dataclass_fields__"):
        return copy.deepcopy(value)
    if isinstance(value, (dict, list, tuple, set)):
        return copy.deepcopy(value)
    return value


class _QueuedAsyncDBWriter:
    """Per-resource FIFO writer that runs synchronous TorqueDB methods off-loop."""

    def __init__(self, owner: "TorqueDB", loop: asyncio.AbstractEventLoop):
        self._owner = owner
        self.loop = loop
        self._queues: dict[str, asyncio.Queue] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._worker_dbs: dict[str, "TorqueDB"] = {}
        self._worker_lock = threading.Lock()
        self.closed = False

    def enqueue_nowait(
        self,
        bucket: str,
        method_name: str,
        args: tuple,
        kwargs: dict,
    ) -> None:
        if self.closed:
            raise RuntimeError("TorqueDB async writer is closed")
        queue = self._queue_for(bucket)
        queue.put_nowait((method_name, args, kwargs, None))
        self._ensure_drainer(bucket)

    async def enqueue(
        self,
        bucket: str,
        method_name: str,
        args: tuple,
        kwargs: dict,
    ):
        if self.closed:
            raise RuntimeError("TorqueDB async writer is closed")
        future = self.loop.create_future()
        queue = self._queue_for(bucket)
        queue.put_nowait((method_name, args, kwargs, future))
        self._ensure_drainer(bucket)
        return await future

    async def flush(self) -> None:
        """Wait until all currently queued/running writes complete."""
        while True:
            queues = list(self._queues.values())
            if queues:
                await asyncio.gather(*(queue.join() for queue in queues))
            pending = [
                task
                for task in self._tasks.values()
                if task is not None and not task.done()
            ]
            if not any(not queue.empty() for queue in self._queues.values()):
                # A drainer may still be alive waiting for the next job; that
                # is fine.  ``Queue.join`` already waited for in-flight work.
                return
            if not pending:
                return

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.flush()
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._queues.clear()
        await asyncio.to_thread(self._close_worker_dbs)

    def _queue_for(self, bucket: str) -> asyncio.Queue:
        bucket = str(bucket or "misc")
        queue = self._queues.get(bucket)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[bucket] = queue
        return queue

    def _ensure_drainer(self, bucket: str) -> None:
        task = self._tasks.get(bucket)
        if task is not None and not task.done():
            return
        self._tasks[bucket] = self.loop.create_task(self._drain(bucket))

    async def _drain(self, bucket: str) -> None:
        queue = self._queue_for(bucket)
        try:
            while True:
                method_name, args, kwargs, future = await queue.get()
                try:
                    result = await asyncio.to_thread(
                        self._run_sync_write,
                        bucket,
                        method_name,
                        args,
                        kwargs,
                    )
                except Exception as exc:
                    if future is not None and not future.done():
                        future.set_exception(exc)
                    log.exception(
                        "Async SQLite write failed (%s.%s)",
                        bucket,
                        method_name,
                    )
                else:
                    if future is not None and not future.done():
                        future.set_result(result)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise

    def _run_sync_write(
        self,
        bucket: str,
        method_name: str,
        args: tuple,
        kwargs: dict,
    ):
        db = self._worker_db_for(bucket)
        method = getattr(db, method_name)
        return method(*args, **kwargs)

    def _worker_db_for(self, bucket: str) -> "TorqueDB":
        bucket = str(bucket or "misc")
        with self._worker_lock:
            db = self._worker_dbs.get(bucket)
            if db is not None:
                return db
            db = TorqueDB(self._owner.db_path)
            db._conn = sqlite3.connect(
                str(self._owner.db_path),
                check_same_thread=False,
            )
            db._conn.execute("PRAGMA foreign_keys=ON")
            db._conn.execute("PRAGMA busy_timeout=5000")
            self._worker_dbs[bucket] = db
            return db

    def _close_worker_dbs(self) -> None:
        with self._worker_lock:
            worker_dbs = list(self._worker_dbs.values())
            self._worker_dbs.clear()
        for db in worker_dbs:
            with contextlib.suppress(Exception):
                db.close()



class TorqueDB(BoardPersistenceMixin, MemoryPersistenceMixin):
    """Facade over Torque's SQLite persistence helpers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._async_writer: Optional["_QueuedAsyncDBWriter"] = None
        self.async_writes_enabled: bool = False

    def enable_async_writes(self, enabled: bool = True) -> None:
        """Toggle fire-and-forget async persistence for ``*_deferred`` calls."""
        self.async_writes_enabled = bool(enabled)

    def _insert_agent_row(self, executor, cell) -> None:
        values = _serialize_agent_cell(cell)
        executor.execute(
            _AGENT_INSERT_SQL.format(
                columns=", ".join(_AGENT_PERSISTED_COLS),
                placeholders=",".join(["?"] * len(values)),
            ),
            values,
        )

    def _insert_board_task_row(self, executor, task) -> None:
        insert_board_task(executor, task)

    def init(self):
        """Open connection, enable WAL, create tables if needed."""
        self._maybe_backup_pre_kinds_db()
        self._conn = sqlite3.connect(str(self.db_path))
        initialize_database(self._conn, self.backfill_agent_history)
        self._ensure_agent_peer_messages_schema()
        self._ensure_board_task_engineer_provenance_column()
        self._ensure_agent_queue_empty_emitted_column()
        self._migrate_agent_activity_timestamps_if_needed()
        self._refuse_unmigrated_legacy_rows_if_needed()
        self._migrate_kinds_schema_if_needed()
        self._ensure_agent_dismissed_at_column()
        self._ensure_agent_tombstone_columns()
        self._ensure_agent_engineer_specializations_column()
        self._ensure_board_task_suggested_specialization_column()
        self._backfill_kinds_if_needed()
        self._fixup_kinds_task_assignments_if_needed()
        self._cleanup_kinds_legacy_columns_if_needed()
        self._backfill_empty_worker_kinds_if_needed()
        self._migrate_agent_digest_settings_from_legacy_engineer_settings()
        self.migrate_task_ids_if_needed()

    def _ensure_board_task_engineer_provenance_column(self):
        try:
            self._conn.execute(
                "SELECT created_by_engineer_id FROM board_tasks LIMIT 0"
            )
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN created_by_engineer_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()

    def _ensure_agent_dismissed_at_column(self):
        try:
            self._conn.execute("SELECT dismissed_at FROM agents LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN dismissed_at "
                "INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()

    def _ensure_agent_tombstone_columns(self):
        for col in ("deleted_at", "permanent_delete_after"):
            try:
                self._conn.execute(f"SELECT {col} FROM agents LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE agents ADD COLUMN {col} "
                    "REAL NOT NULL DEFAULT 0"
                )
                self._conn.commit()

    def _ensure_agent_engineer_specializations_column(self):
        try:
            self._conn.execute(
                "SELECT engineer_specializations FROM agents LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN engineer_specializations "
                "TEXT NOT NULL DEFAULT '[]'"
            )
            self._conn.commit()

    def _ensure_agent_peer_messages_schema(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_peer_messages (
                id                   TEXT PRIMARY KEY,
                thread_id            TEXT NOT NULL,
                reply_to_id          TEXT NOT NULL DEFAULT '',
                group_name           TEXT NOT NULL DEFAULT '',
                sender_id            TEXT NOT NULL,
                sender_kind          TEXT NOT NULL,
                sender_name          TEXT NOT NULL DEFAULT '',
                recipient_id         TEXT NOT NULL,
                recipient_kind       TEXT NOT NULL,
                recipient_name       TEXT NOT NULL DEFAULT '',
                message              TEXT NOT NULL,
                message_type         TEXT NOT NULL DEFAULT 'message',
                created_at           REAL NOT NULL,
                ack_required         INTEGER NOT NULL DEFAULT 0,
                blocking             INTEGER NOT NULL DEFAULT 0,
                source_task_id       TEXT NOT NULL DEFAULT '',
                context_task_ids     TEXT NOT NULL DEFAULT '[]',
                context_engineer_ids TEXT NOT NULL DEFAULT '[]',
                context_decision_ids TEXT NOT NULL DEFAULT '[]',
                context_summary      TEXT NOT NULL DEFAULT '',
                context_snapshot     TEXT NOT NULL DEFAULT '{}',
                delivery_state       TEXT NOT NULL DEFAULT 'buffered',
                delivery_reason      TEXT NOT NULL DEFAULT '',
                delivered_at         REAL NOT NULL DEFAULT 0,
                read_at              REAL NOT NULL DEFAULT 0,
                archived_at          REAL NOT NULL DEFAULT 0
            )
            """
        )
        existing = {
            row[1]
            for row in self._conn.execute(
                "PRAGMA table_info(agent_peer_messages)"
            ).fetchall()
        }
        column_defs = {
            "thread_id": "TEXT NOT NULL DEFAULT ''",
            "reply_to_id": "TEXT NOT NULL DEFAULT ''",
            "group_name": "TEXT NOT NULL DEFAULT ''",
            "sender_id": "TEXT NOT NULL DEFAULT ''",
            "sender_kind": "TEXT NOT NULL DEFAULT 'architect'",
            "sender_name": "TEXT NOT NULL DEFAULT ''",
            "recipient_id": "TEXT NOT NULL DEFAULT ''",
            "recipient_kind": "TEXT NOT NULL DEFAULT 'architect'",
            "recipient_name": "TEXT NOT NULL DEFAULT ''",
            "message": "TEXT NOT NULL DEFAULT ''",
            "message_type": "TEXT NOT NULL DEFAULT 'message'",
            "created_at": "REAL NOT NULL DEFAULT 0",
            "ack_required": "INTEGER NOT NULL DEFAULT 0",
            "blocking": "INTEGER NOT NULL DEFAULT 0",
            "source_task_id": "TEXT NOT NULL DEFAULT ''",
            "context_task_ids": "TEXT NOT NULL DEFAULT '[]'",
            "context_engineer_ids": "TEXT NOT NULL DEFAULT '[]'",
            "context_decision_ids": "TEXT NOT NULL DEFAULT '[]'",
            "context_summary": "TEXT NOT NULL DEFAULT ''",
            "context_snapshot": "TEXT NOT NULL DEFAULT '{}'",
            "delivery_state": "TEXT NOT NULL DEFAULT 'buffered'",
            "delivery_reason": "TEXT NOT NULL DEFAULT ''",
            "delivered_at": "REAL NOT NULL DEFAULT 0",
            "read_at": "REAL NOT NULL DEFAULT 0",
            "archived_at": "REAL NOT NULL DEFAULT 0",
        }
        for column in _AGENT_PEER_MESSAGE_COLUMNS:
            if column == "id" or column in existing:
                continue
            self._conn.execute(
                "ALTER TABLE agent_peer_messages ADD COLUMN "
                f"{column} {column_defs[column]}"
            )
        for sql in (
            "CREATE INDEX IF NOT EXISTS "
            "idx_agent_peer_messages_recipient_recent "
            "ON agent_peer_messages(recipient_id, created_at DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS "
            "idx_agent_peer_messages_sender_recent "
            "ON agent_peer_messages(sender_id, created_at DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_thread "
            "ON agent_peer_messages(thread_id, created_at ASC, id ASC)",
            "CREATE INDEX IF NOT EXISTS "
            "idx_agent_peer_messages_group_recent "
            "ON agent_peer_messages(group_name, created_at DESC, id DESC)",
        ):
            self._conn.execute(sql)
        self._conn.commit()

    def _ensure_board_task_suggested_specialization_column(self):
        try:
            self._conn.execute(
                "SELECT suggested_specialization FROM board_tasks LIMIT 0"
            )
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN suggested_specialization "
                "TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- MCP reliability ----------------------------------------------------

    def load_mcp_idempotency(self, idempotency_key: str) -> dict | None:
        """Load a stored MCP idempotency response by key."""
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        row = self._conn.execute(
            "SELECT idempotency_key, surface, tool_name, request_hash, "
            "response_json, created_at, updated_at "
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
            "created_at": row[5],
            "updated_at": row[6],
        }

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
        existing = self.load_mcp_idempotency(key)
        created_at = float((existing or {}).get("created_at", 0) or now)
        self._conn.execute(
            "INSERT OR REPLACE INTO mcp_idempotency "
            "(idempotency_key, surface, tool_name, request_hash, "
            "response_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                key,
                str(surface or ""),
                str(tool_name or ""),
                str(request_hash or ""),
                json.dumps(response or {}, separators=(",", ":")),
                created_at,
                now,
            ),
        )
        self._conn.commit()

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

    def _legacy_engineer_rows_exist(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM engineer_settings LIMIT 1"
        ).fetchone()
        return bool(row)

    def _migrate_agent_digest_settings_from_legacy_engineer_settings(self):
        """Backfill per-agent digest settings from legacy per-group rows."""
        if not self._legacy_engineer_rows_exist():
            return
        try:
            legacy_rows = self.load_all_engineer_settings()
        except sqlite3.OperationalError:
            return
        if not legacy_rows:
            return
        for group_name, settings in legacy_rows.items():
            row = self._conn.execute(
                "SELECT engineer_agent_id FROM group_settings "
                "WHERE group_name=?",
                (group_name,),
            ).fetchone()
            engineer_id = str((row[0] if row else "") or "").strip()
            if not engineer_id:
                log.warning(
                    "Skipping legacy engineer_settings migration for '%s': no designated engineer",
                    group_name,
                )
                continue
            agent_row = self._conn.execute(
                "SELECT kind FROM agents WHERE id=?",
                (engineer_id,),
            ).fetchone()
            if not agent_row:
                log.warning(
                    "Skipping legacy engineer_settings migration for '%s': agent '%s' missing",
                    group_name,
                    engineer_id,
                )
                continue
            if str(agent_row[0] or "").strip() != "engineer":
                log.warning(
                    "Skipping legacy engineer_settings migration for '%s': agent '%s' is not an engineer",
                    group_name,
                    engineer_id,
                )
                continue
            if self.load_agent_digest_settings(engineer_id):
                continue
            self.save_agent_digest_settings(
                engineer_id,
                {
                    "agent_id": engineer_id,
                    "paused": bool(settings.get("paused", False)),
                    "push_interval": settings.get("push_interval", 60),
                    "max_interval": settings.get("max_interval", 300),
                    "heartbeat_interval": settings.get(
                        "heartbeat_interval",
                        settings.get("max_interval", 300),
                    ),
                    "digest_verbosity": settings.get(
                        "digest_verbosity",
                        "balanced",
                    ),
                    "enabled_events": list(
                        settings.get("enabled_events", []) or []
                    ),
                    "architect_digest": False,
                    "wake_on_digest": False,
                },
            )

    def load_task_id_aliases(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT legacy_id, task_id FROM task_id_aliases"
        ).fetchall()
        return {
            str(legacy_id or ""): str(task_id or "")
            for legacy_id, task_id in rows
            if legacy_id and task_id
        }

    def save_task_id_alias(self, legacy_id: str, task_id: str):
        legacy = str(legacy_id or "").strip()
        task = str(task_id or "").strip()
        if not legacy or not task or legacy == task:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO task_id_aliases (legacy_id, task_id) "
            "VALUES (?, ?)",
            (legacy, task),
        )
        self._conn.commit()

    def load_task_id_counters(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT group_prefix, next_root_number FROM task_id_counters"
        ).fetchall()
        return {
            str(prefix or ""): int(next_root or 1)
            for prefix, next_root in rows
            if prefix
        }

    def save_task_id_counter(self, group_prefix: str, next_root_number: int):
        prefix = normalize_group_prefix(group_prefix)
        self._conn.execute(
            "INSERT OR REPLACE INTO task_id_counters "
            "(group_prefix, next_root_number) VALUES (?, ?)",
            (prefix, max(1, int(next_root_number or 1))),
        )
        self._conn.commit()

    def load_pipeline_task_counters(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT root_task_id, next_child_number FROM pipeline_task_counters"
        ).fetchall()
        return {
            str(root_task_id or ""): int(next_child or 1)
            for root_task_id, next_child in rows
            if root_task_id
        }

    def save_pipeline_task_counter(self, root_task_id: str, next_child_number: int):
        root_id = str(root_task_id or "").strip()
        if not root_id:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_task_counters "
            "(root_task_id, next_child_number) VALUES (?, ?)",
            (root_id, max(1, int(next_child_number or 1))),
        )
        self._conn.commit()

    def save_agent(self, cell):
        """Upsert a single agent/terminal cell (persisted fields only)."""
        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_save_agent_ms"):
            self._insert_agent_row(self._conn, cell)
            self._conn.commit()

    def save_agent_deferred(self, cell) -> None:
        """Persist an agent off-loop when called from asyncio code.

        Synchronous callers keep the old immediate-write behavior.  Async
        callers enqueue a point-in-time snapshot and return immediately; use
        ``flush_async_writes`` during graceful shutdown/tests to wait for the
        background drainer.
        """
        self.defer_write(
            "agents",
            "save_agent",
            cell,
            snapshot_args=True,
        )

    async def save_agent_async(self, cell):
        """Queue and await an agent save without blocking the event loop."""
        return await self._enqueue_async_write(
            "agents",
            "save_agent",
            _snapshot_db_payload(cell),
        )

    def save_board_task(self, task):
        """Upsert a board task."""
        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_save_board_task_ms"):
            self._insert_board_task_row(self._conn, task)
            self._conn.commit()

    def save_board_tasks(self, tasks):
        """Upsert multiple board tasks in one SQLite transaction."""
        task_rows = list(tasks or [])
        if not task_rows:
            return

        def _operation():
            try:
                self._conn.execute("BEGIN")
                for task in task_rows:
                    self._insert_board_task_row(self._conn, task)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_save_board_tasks_ms"):
            self._run_sqlite_write_with_lock_retry(
                _operation,
                surface="board_tasks",
            )

    def save_board_task_deferred(self, task) -> None:
        """Persist a board task off-loop when called from asyncio code."""
        self.defer_write(
            "board_tasks",
            "save_board_task",
            task,
            snapshot_args=True,
        )

    async def save_board_task_async(self, task):
        """Queue and await a board-task save without blocking the event loop."""
        return await self._enqueue_async_write(
            "board_tasks",
            "save_board_task",
            _snapshot_db_payload(task),
        )

    def save_groups_and_members(
        self,
        groups: dict,
        slugs: dict | None = None,
    ) -> None:
        """Persist groups plus each group's ordered membership list."""
        self.save_groups(groups, slugs)
        for group_name, members in (groups or {}).items():
            self.save_group_members(group_name, members)

    def save_groups_and_members_deferred(
        self,
        groups: dict,
        slugs: dict | None = None,
    ) -> None:
        """Persist groups/members off-loop when called from asyncio code."""
        self.defer_write(
            "groups",
            "save_groups_and_members",
            groups,
            slugs or {},
            snapshot_args=True,
        )

    def defer_write(
        self,
        bucket: str,
        method_name: str,
        *args,
        snapshot_args: bool = True,
        **kwargs,
    ):
        """Run a sync write immediately or enqueue it for async contexts.

        This is the fire-and-forget half of the async write facade.  It keeps
        non-async tests/tools fully synchronous while moving daemon hot-path
        writes to per-resource FIFO drainers when an event loop is running.
        """
        if not self.async_writes_enabled or str(self.db_path) == ":memory:":
            return getattr(self, method_name)(*args, **kwargs)
        try:
            writer = self._get_async_writer()
        except RuntimeError:
            return getattr(self, method_name)(*args, **kwargs)
        call_args = args
        call_kwargs = kwargs
        if snapshot_args:
            call_args = tuple(_snapshot_db_payload(arg) for arg in args)
            call_kwargs = {
                key: _snapshot_db_payload(value)
                for key, value in kwargs.items()
            }
        writer.enqueue_nowait(
            str(bucket or "misc"),
            method_name,
            call_args,
            call_kwargs,
        )

    async def _enqueue_async_write(
        self,
        bucket: str,
        method_name: str,
        *args,
        **kwargs,
    ):
        if str(self.db_path) == ":memory:":
            return getattr(self, method_name)(*args, **kwargs)
        writer = self._get_async_writer()
        return await writer.enqueue(
            str(bucket or "misc"),
            method_name,
            tuple(args),
            dict(kwargs),
        )

    def _get_async_writer(self) -> "_QueuedAsyncDBWriter":
        loop = asyncio.get_running_loop()
        writer = self._async_writer
        if writer is not None and writer.loop is not loop:
            raise RuntimeError(
                "TorqueDB async writer is bound to a different event loop"
            )
        if writer is None or writer.closed:
            writer = _QueuedAsyncDBWriter(self, loop)
            self._async_writer = writer
        return writer

    async def flush_async_writes(self) -> None:
        """Wait for queued async SQLite writes to drain."""
        writer = self._async_writer
        if writer is not None:
            await writer.flush()

    async def close_async_writes(self) -> None:
        """Drain and stop async SQLite write workers."""
        writer = self._async_writer
        if writer is not None:
            await writer.aclose()
            self._async_writer = None

    def _delete_agent_sync(self, agent_id: str):
        self._conn.execute(
            "DELETE FROM agent_message_history WHERE agent_id=?",
            (agent_id,))
        self._conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        self._conn.execute(
            "DELETE FROM group_members WHERE agent_id=?", (agent_id,))
        self._conn.commit()

    def delete_agent(self, agent_id: str):
        return self.defer_write(
            "agents",
            "_delete_agent_sync",
            agent_id,
            snapshot_args=False,
        )

    def save_group(self, name: str, position: int):
        self._conn.execute(
            "INSERT OR REPLACE INTO groups (name, position) VALUES (?,?)",
            (name, position))
        self._conn.commit()

    def _delete_group_sync(self, name: str):
        self._conn.execute("DELETE FROM groups WHERE name=?", (name,))
        self._conn.execute(
            "DELETE FROM group_members WHERE group_name=?", (name,))
        self._conn.execute(
            "DELETE FROM group_settings WHERE group_name=?", (name,))
        self._conn.execute(
            "DELETE FROM engineer_task_log WHERE group_name=?", (name,))
        self._conn.commit()

    def delete_group(self, name: str):
        result = self.defer_write(
            "groups",
            "_delete_group_sync",
            name,
            snapshot_args=False,
        )
        self.defer_write(
            "group_settings",
            "_delete_group_settings_sync",
            name,
            snapshot_args=False,
        )
        return result

    def save_groups(self, groups: dict, slugs: dict = None):
        """Bulk-save all groups with positions and slugs."""
        slugs = slugs or {}
        self._conn.execute("DELETE FROM groups")
        for pos, name in enumerate(groups):
            self._conn.execute(
                "INSERT INTO groups (name, slug, position) VALUES (?,?,?)",
                (name, slugs.get(name, ""), pos))
        self._conn.commit()

    def save_group_members(self, group_name: str, agent_ids: list):
        """Replace the membership list for a group."""
        self._conn.execute(
            "DELETE FROM group_members WHERE group_name=?", (group_name,))
        for pos, aid in enumerate(agent_ids):
            self._conn.execute(
                "INSERT INTO group_members (group_name, agent_id, position) "
                "VALUES (?,?,?)", (group_name, aid, pos))
        self._conn.commit()

    def save_group_settings(self, group_name: str, gs):
        """Upsert group settings."""
        d = asdict(gs)
        # Convert dicts to JSON text, bools to int
        for k in _GS_JSON_FIELDS:
            if k in d:
                d[k] = json.dumps(d[k])
        for k in _GS_BOOL_FIELDS:
            if k in d:
                d[k] = int(d[k])

        cols = ["group_name"] + list(d.keys())
        placeholders = ",".join(["?"] * len(cols))
        col_str = ",".join(cols)
        self._conn.execute(
            f"INSERT OR REPLACE INTO group_settings ({col_str}) "
            f"VALUES ({placeholders})",
            [group_name] + list(d.values()))
        self._conn.commit()

    def _delete_group_settings_sync(self, group_name: str):
        self._conn.execute(
            "DELETE FROM group_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def delete_group_settings(self, group_name: str):
        return self.defer_write(
            "group_settings",
            "_delete_group_settings_sync",
            group_name,
            snapshot_args=False,
        )

    def save_schedule(self, sched):
        """Upsert a schedule."""
        d = asdict(sched)
        labels = json.dumps(d.pop("labels", []))
        action_vars = json.dumps(d.pop("action_vars", {}))
        group_name = d.pop("group", "")
        self._conn.execute("""
            INSERT OR REPLACE INTO schedules
                (id, name, slug, task_template, description, group_name,
                 action_name, action_vars, agent_template, labels,
                 cron_expr, scheduled_at, timezone, enabled,
                 last_run_at, next_run_at, run_count, last_task_id,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d["id"], d["name"], d["slug"],
            d.get("task_template", ""), d.get("description", ""),
            group_name,
            d.get("action_name", ""), action_vars,
            d.get("agent_template", ""), labels,
            d.get("cron_expr", ""), d.get("scheduled_at", ""),
            d.get("timezone", ""), 1 if d.get("enabled", True) else 0,
            d.get("last_run_at", ""), d.get("next_run_at", ""),
            d.get("run_count", 0), d.get("last_task_id", ""),
            d["created_at"], d["updated_at"],
        ))
        self._conn.commit()

    def _delete_schedule_sync(self, sid: str):
        self._conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
        self._conn.commit()

    def delete_schedule(self, sid: str):
        return self.defer_write(
            "schedules",
            "_delete_schedule_sync",
            sid,
            snapshot_args=False,
        )

    def save_ui_state(self, key: str, value):
        self._conn.execute(
            "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?,?)",
            (key, str(value)))
        self._conn.commit()

    def load_ui_state_value(self, key: str) -> str | None:
        """Return one ui_state value, or None when the key is unset."""
        row = self._conn.execute(
            "SELECT value FROM ui_state WHERE key=?",
            (key,),
        ).fetchone()
        return row[0] if row else None

    def save_global_settings(self, gs):
        """Persist global settings as key-value pairs."""
        d = asdict(gs)
        xterm_scrollback = int(d.get("xterm_scrollback", 2000) or 2000)
        self._conn.execute("DELETE FROM global_settings")
        for key, value in d.items():
            self._conn.execute(
                "INSERT INTO global_settings "
                "(key, value, xterm_scrollback) "
                "VALUES (?,?,?)",
                (
                    key,
                    json.dumps(value),
                    xterm_scrollback,
                ))
        self._conn.commit()

    def save_auto_dispatch_queue(self, group_name: str, entries: list):
        """Replace one group's auto-dispatch queue."""
        self._conn.execute(
            "DELETE FROM auto_dispatch_queue WHERE group_name=?",
            (group_name,),
        )
        for pos, entry in enumerate(entries):
            item = entry if isinstance(entry, dict) else asdict(entry)
            self._conn.execute(
                "INSERT INTO auto_dispatch_queue "
                "(group_name, position, task_id, agent_group, "
                "max_concurrent, target_agent_id, engineer_owner_id, "
                "provider, enqueued_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    group_name,
                    pos,
                    item.get("task_id", ""),
                    item.get("agent_group", ""),
                    int(item.get("max_concurrent", 1) or 1),
                    item.get("target_agent_id", ""),
                    item.get("engineer_owner_id", ""),
                    item.get("provider", ""),
                    item.get("enqueued_at", ""),
                ),
            )
        self._conn.commit()

    def _delete_auto_dispatch_queue_sync(self, group_name: str):
        self._conn.execute(
            "DELETE FROM auto_dispatch_queue WHERE group_name=?",
            (group_name,),
        )
        self._conn.commit()

    def delete_auto_dispatch_queue(self, group_name: str):
        return self.defer_write(
            "auto_dispatch_queue",
            "_delete_auto_dispatch_queue_sync",
            group_name,
            snapshot_args=False,
        )

    def _rewrite_attachment_refs(self, task_dict: dict,
                                 id_map: dict[str, str]) -> dict:
        old_id = str(task_dict.get("id", "") or "")
        new_id = id_map.get(old_id, old_id)
        old_dir = ATTACHMENTS_DIR / old_id
        new_dir = ATTACHMENTS_DIR / new_id

        attachments = []
        for att in list(task_dict.get("attachments", []) or []):
            item = dict(att or {})
            path = str(item.get("path", "") or "")
            if path and old_id and old_id != new_id:
                try:
                    item["path"] = str(new_dir / Path(path).name)
                except Exception:
                    item["path"] = path.replace(str(old_dir), str(new_dir))
            attachments.append(item)
        task_dict["attachments"] = attachments

        artifacts = []
        for artifact in list(task_dict.get("artifacts", []) or []):
            item = dict(artifact or {})
            path = str(item.get("path", "") or "")
            if path and old_id and old_id != new_id:
                try:
                    item["path"] = str(new_dir / Path(path).name)
                except Exception:
                    item["path"] = path.replace(str(old_dir), str(new_dir))
            storage = item.get("storage")
            if isinstance(storage, dict):
                storage = dict(storage)
                storage_path = str(storage.get("path", "") or "")
                if storage_path and old_id and old_id != new_id:
                    try:
                        storage["path"] = str(new_dir / Path(storage_path).name)
                    except Exception:
                        storage["path"] = storage_path.replace(
                            str(old_dir), str(new_dir)
                        )
                item["storage"] = storage
            provenance = item.get("provenance")
            if isinstance(provenance, dict):
                provenance = dict(provenance)
                provenance["task_id"] = new_id
                item["provenance"] = provenance
            artifacts.append(item)
        task_dict["artifacts"] = artifacts
        return task_dict

    def _rewrite_playbook_task_refs(self, payload, id_map: dict[str, str]):
        if isinstance(payload, list):
            return [self._rewrite_playbook_task_refs(item, id_map) for item in payload]
        if not isinstance(payload, dict):
            return payload
        out = {}
        for key, value in payload.items():
            if key in {"root_task_id", "task_id", "blocked_by_task_id"}:
                mapped = id_map.get(str(value or ""), str(value or ""))
                out[key] = mapped
            else:
                out[key] = self._rewrite_playbook_task_refs(value, id_map)
        return out

    def _reseed_task_id_counters(self):
        task_rows = self._conn.execute(
            "SELECT id FROM board_tasks"
        ).fetchall()
        group_next: dict[str, int] = {}
        pipeline_next: dict[str, int] = {}
        for (task_id,) in task_rows:
            parsed = parse_task_id(task_id)
            if not parsed:
                continue
            prefix = parsed["prefix"]
            group_next[prefix] = max(
                group_next.get(prefix, 1),
                parsed["root_number"] + 1,
            )
            if parsed["child_number"] is not None:
                root_id = format_root_task_id(prefix, parsed["root_number"])
                pipeline_next[root_id] = max(
                    pipeline_next.get(root_id, 1),
                    parsed["child_number"] + 1,
                )
        self._conn.execute("DELETE FROM task_id_counters")
        self._conn.execute("DELETE FROM pipeline_task_counters")
        for prefix, next_root in group_next.items():
            self._conn.execute(
                "INSERT INTO task_id_counters (group_prefix, next_root_number) "
                "VALUES (?, ?)",
                (prefix, next_root),
            )
        for root_id, next_child in pipeline_next.items():
            self._conn.execute(
                "INSERT INTO pipeline_task_counters "
                "(root_task_id, next_child_number) VALUES (?, ?)",
                (root_id, next_child),
            )

    def migrate_task_ids_if_needed(self):
        """Rewrite legacy task IDs into canonical group-scoped IDs."""
        rows = self._conn.execute(
            "SELECT * FROM board_tasks"
        ).fetchall()
        if not rows:
            return
        cols = [d[0] for d in self._conn.execute("SELECT * FROM board_tasks").description]
        tasks = [decode_board_task_row(row, cols) for row in rows]
        if all(is_canonical_task_id(task.get("id", "")) for task in tasks):
            self._reseed_task_id_counters()
            self._conn.commit()
            return

        by_id = {str(task["id"]): task for task in tasks}
        root_next: dict[str, int] = {}
        for task in tasks:
            parsed = parse_task_id(task.get("id", ""))
            if parsed and not task.get("parent_task_id"):
                root_next[parsed["prefix"]] = max(
                    root_next.get(parsed["prefix"], 1),
                    parsed["root_number"] + 1,
                )

        def sort_key(task):
            return (
                str(task.get("created_at", "") or ""),
                str(task.get("updated_at", "") or ""),
                str(task.get("id", "") or ""),
            )

        id_map: dict[str, str] = {}
        root_number_by_root_id: dict[str, int] = {}
        root_prefix_by_root_id: dict[str, str] = {}
        root_id_by_old_root: dict[str, str] = {}

        roots = [task for task in tasks if not task.get("parent_task_id")]
        roots.sort(key=sort_key)
        for task in roots:
            old_id = str(task["id"])
            parsed = parse_task_id(old_id)
            prefix = normalize_group_prefix(task.get("group", ""))
            if parsed and parsed["child_number"] is None:
                new_id = old_id
                root_num = parsed["root_number"]
                prefix = parsed["prefix"]
            else:
                root_num = root_next.get(prefix, 1)
                new_id = format_root_task_id(prefix, root_num)
                root_next[prefix] = root_num + 1
            id_map[old_id] = new_id
            root_number_by_root_id[old_id] = root_num
            root_prefix_by_root_id[old_id] = prefix
            root_id_by_old_root[old_id] = new_id

        child_next: dict[str, int] = {}
        existing_children = [
            task for task in tasks
            if parse_task_id(task.get("id", ""))
            and task.get("parent_task_id")
        ]
        for task in existing_children:
            parsed = parse_task_id(task.get("id", ""))
            if not parsed or parsed["child_number"] is None:
                continue
            pipeline_root_id = str(task.get("pipeline_root_id", "") or "")
            root_old_id = pipeline_root_id or ""
            root_num = parsed["root_number"]
            if root_old_id:
                root_number_by_root_id.setdefault(root_old_id, root_num)
                root_id_by_old_root.setdefault(
                    root_old_id,
                    format_root_task_id(parsed["prefix"], root_num),
                )
                child_next[root_old_id] = max(
                    child_next.get(root_old_id, 1),
                    parsed["child_number"] + 1,
                )

        children_by_root: dict[str, list[dict]] = {}
        for task in tasks:
            if not task.get("parent_task_id"):
                continue
            old_id = str(task["id"])
            if parse_task_id(old_id):
                id_map.setdefault(old_id, old_id)
                continue
            root_old_id = str(task.get("pipeline_root_id", "") or "")
            if not root_old_id:
                continue
            children_by_root.setdefault(root_old_id, []).append(task)

        for root_old_id, children in children_by_root.items():
            children.sort(key=sort_key)
            root_num = root_number_by_root_id.get(root_old_id)
            if root_num is None:
                root_new = id_map.get(root_old_id, root_old_id)
                root_parsed = parse_task_id(root_new)
                if not root_parsed:
                    continue
                root_num = root_parsed["root_number"]
            next_child = child_next.get(root_old_id, 1)
            for task in children:
                old_id = str(task["id"])
                prefix = normalize_group_prefix(task.get("group", ""))
                new_id = format_derived_task_id(prefix, root_num, next_child)
                next_child += 1
                id_map[old_id] = new_id
            child_next[root_old_id] = next_child

        updated_tasks = []
        for task in tasks:
            old_id = str(task["id"])
            new_id = id_map.get(old_id, old_id)
            task["id"] = new_id
            if task.get("parent_task_id"):
                task["parent_task_id"] = id_map.get(
                    str(task.get("parent_task_id", "") or ""),
                    str(task.get("parent_task_id", "") or ""),
                )
            if task.get("pipeline_root_id"):
                task["pipeline_root_id"] = id_map.get(
                    str(task.get("pipeline_root_id", "") or ""),
                    str(task.get("pipeline_root_id", "") or ""),
                )
            task["depends_on"] = [
                id_map.get(str(dep_id or ""), str(dep_id or ""))
                for dep_id in list(task.get("depends_on", []) or [])
                if dep_id
            ]
            if task.get("resume_after_boundary_task_id"):
                ref = str(task.get("resume_after_boundary_task_id", "") or "")
                task["resume_after_boundary_task_id"] = id_map.get(ref, ref)
            boundary = task.get("worktree_boundary")
            if isinstance(boundary, dict):
                boundary = dict(boundary)
                superseded = str(boundary.get("superseded_by_task_id", "") or "")
                if superseded:
                    boundary["superseded_by_task_id"] = id_map.get(
                        superseded, superseded
                    )
                task["worktree_boundary"] = boundary
            updated_tasks.append(self._rewrite_attachment_refs(task, id_map))

        self._conn.execute("DELETE FROM board_tasks")
        for task in updated_tasks:
            self._insert_board_task_row(self._conn, task)

        def _rewrite_single_ref(table: str, column: str):
            table_rows = self._conn.execute(
                f"SELECT rowid, {column} FROM {table}"
            ).fetchall()
            for rowid, value in table_rows:
                raw = str(value or "")
                mapped = id_map.get(raw, raw)
                if mapped != raw:
                    self._conn.execute(
                        f"UPDATE {table} SET {column}=? WHERE rowid=?",
                        (mapped, rowid),
                    )

        for table, column in (
            ("auto_dispatch_queue", "task_id"),
            ("panel_events", "task_id"),
            ("schedules", "last_task_id"),
            ("agent_tasks", "task_id"),
            ("agent_messages", "task_id"),
            ("engineer_task_log", "task_id"),
            ("memory_entries", "task_id"),
        ):
            _rewrite_single_ref(table, column)

        try:
            peer_rows = self._conn.execute(
                "SELECT id, context_task_ids FROM agent_peer_messages"
            ).fetchall()
        except sqlite3.OperationalError:
            peer_rows = []
        for message_id, task_ids_json in peer_rows:
            try:
                task_ids = json.loads(task_ids_json or "[]")
            except (json.JSONDecodeError, TypeError):
                task_ids = []
            if not isinstance(task_ids, list):
                continue
            rewritten = [
                id_map.get(str(task_id or ""), str(task_id or ""))
                for task_id in task_ids
                if str(task_id or "")
            ]
            if rewritten != task_ids:
                self._conn.execute(
                    "UPDATE agent_peer_messages SET context_task_ids=? "
                    "WHERE id=?",
                    (json.dumps(rewritten, separators=(",", ":")), message_id),
                )

        memory_rows = self._conn.execute(
            "SELECT rowid, scope_kind, scope_ref FROM memory_entries"
        ).fetchall()
        for rowid, scope_kind, scope_ref in memory_rows:
            kind = str(scope_kind or "")
            ref = str(scope_ref or "")
            mapped = ref
            if kind in {"task", "pipeline"}:
                mapped = id_map.get(ref, ref)
            if mapped != ref:
                self._conn.execute(
                    "UPDATE memory_entries SET scope_ref=? WHERE rowid=?",
                    (mapped, rowid),
                )

        link_rows = self._conn.execute(
            "SELECT rowid, target_kind, target_ref FROM memory_links"
        ).fetchall()
        for rowid, target_kind, target_ref in link_rows:
            kind = str(target_kind or "")
            ref = str(target_ref or "")
            mapped = ref
            if kind in {"task", "pipeline"}:
                mapped = id_map.get(ref, ref)
            if mapped != ref:
                self._conn.execute(
                    "UPDATE memory_links SET target_ref=? WHERE rowid=?",
                    (mapped, rowid),
                )

        for table in ("playbook_candidates", "playbooks"):
            rows = self._conn.execute(
                f"SELECT id, evidence FROM {table}"
            ).fetchall()
            for row_id, evidence_json in rows:
                try:
                    evidence = json.loads(evidence_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    evidence = {}
                rewritten = self._rewrite_playbook_task_refs(evidence, id_map)
                if rewritten != evidence:
                    self._conn.execute(
                        f"UPDATE {table} SET evidence=? WHERE id=?",
                        (json.dumps(rewritten), row_id),
                    )
            if table == "playbook_candidates":
                rows = self._conn.execute(
                    "SELECT id, supporting_runs, counterexamples "
                    "FROM playbook_candidates"
                ).fetchall()
                for row_id, supporting_json, counter_json in rows:
                    try:
                        supporting = json.loads(supporting_json or "[]")
                    except (json.JSONDecodeError, TypeError):
                        supporting = []
                    try:
                        counter = json.loads(counter_json or "[]")
                    except (json.JSONDecodeError, TypeError):
                        counter = []
                    new_supporting = self._rewrite_playbook_task_refs(
                        supporting, id_map
                    )
                    new_counter = self._rewrite_playbook_task_refs(
                        counter, id_map
                    )
                    if new_supporting != supporting or new_counter != counter:
                        self._conn.execute(
                            "UPDATE playbook_candidates "
                            "SET supporting_runs=?, counterexamples=? "
                            "WHERE id=?",
                            (
                                json.dumps(new_supporting),
                                json.dumps(new_counter),
                                row_id,
                            ),
                        )

        for old_id, new_id in id_map.items():
            if old_id == new_id:
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO task_id_aliases (legacy_id, task_id) "
                "VALUES (?, ?)",
                (old_id, new_id),
            )
            old_dir = ATTACHMENTS_DIR / old_id
            new_dir = ATTACHMENTS_DIR / new_id
            if old_dir.exists() and old_dir != new_dir:
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                if new_dir.exists():
                    for child in old_dir.iterdir():
                        target = new_dir / child.name
                        if target.exists():
                            target.unlink()
                        shutil.move(str(child), str(target))
                    shutil.rmtree(old_dir, ignore_errors=True)
                else:
                    shutil.move(str(old_dir), str(new_dir))

        self._reseed_task_id_counters()
        self._conn.commit()

    # -- Panel events -------------------------------------------------------

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
                    [TorqueDB._panel_event_insert_values(evt) for evt in events],
                )
                inserted = len(events)
            if updates:
                conn.executemany(
                    "UPDATE panel_events SET timestamp=?, kind=?, cell_id=?, "
                    "agent_name=?, group_name=?, message=?, task_id=? WHERE id=?",
                    [TorqueDB._panel_event_update_values(evt) for evt in updates],
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

    def get_panel_event_max_id(self) -> int:
        """Return the highest panel_events id, or 0 if empty."""
        row = self._conn.execute(
            "SELECT MAX(id) FROM panel_events").fetchone()
        return row[0] if row and row[0] is not None else 0

    # -- Digest delivery persistence ----------------------------------------

    def save_digest_queued_event(
        self,
        recipient_id: str,
        event: dict,
        enqueued_at: float,
    ) -> int:
        """Persist one queued digest event and return its queue row ID."""
        def _operation():
            try:
                cur = self._conn.execute(
                    "INSERT INTO digest_queued_events "
                    "(recipient_id, event_json, enqueued_at) VALUES (?,?,?)",
                    (
                        str(recipient_id or ""),
                        _digest_event_json(event),
                        float(enqueued_at or 0),
                    ),
                )
                self._conn.commit()
                return int(cur.lastrowid or 0)
            except Exception:
                self._conn.rollback()
                raise

        return self._run_sqlite_write_with_lock_retry(
            _operation,
            surface="digest",
        )

    def load_digest_queued_events(self) -> dict[str, list[dict]]:
        """Load queued digest events grouped by recipient in delivery order."""
        rows = self._conn.execute(
            "SELECT id, recipient_id, event_json, enqueued_at "
            "FROM digest_queued_events "
            "ORDER BY recipient_id, id"
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for row_id, recipient_id, event_json, enqueued_at in rows:
            recipient_id = str(recipient_id or "")
            if not recipient_id:
                continue
            event = _decode_digest_event_json(event_json)
            event["_digest_queue_id"] = int(row_id or 0)
            event["_digest_enqueued_at"] = float(enqueued_at or 0)
            result.setdefault(recipient_id, []).append(event)
        return result

    def load_digest_sent_events(
        self,
        *,
        limit_per_recipient: int = 200,
    ) -> dict[str, list[dict]]:
        """Load recent sent digest events grouped by recipient.

        Each recipient is capped in memory to the most recent
        ``limit_per_recipient`` rows, returned oldest-first for UI display.
        """
        cap = max(0, int(limit_per_recipient or 0))
        rows = self._conn.execute(
            "SELECT recipient_id, event_json, enqueued_at, delivered_at "
            "FROM digest_sent_events "
            "ORDER BY recipient_id, delivered_at DESC, id DESC"
        ).fetchall()
        newest_first: dict[str, list[dict]] = {}
        for recipient_id, event_json, enqueued_at, delivered_at in rows:
            recipient_id = str(recipient_id or "")
            if not recipient_id:
                continue
            bucket = newest_first.setdefault(recipient_id, [])
            if cap and len(bucket) >= cap:
                continue
            event = _decode_digest_event_json(event_json)
            event["delivered_at"] = float(
                event.get("delivered_at") or delivered_at or 0
            )
            bucket.append(event)

        result: dict[str, list[dict]] = {}
        for recipient_id, events in newest_first.items():
            result[recipient_id] = list(reversed(events))
        return result

    def complete_digest_delivery(
        self,
        recipient_id: str,
        sent_events: list[tuple[dict, float, float]],
        queue_ids: list[int],
        *,
        sent_cap: int = 200,
    ):
        """Move delivered events from the queued table to sent history.

        ``sent_events`` contains ``(event, enqueued_at, delivered_at)`` tuples.
        Queue deletion, sent inserts, and sent-history pruning are committed as
        one transaction so a mid-flush crash cannot leave a partial move.
        """
        recipient_id = str(recipient_id or "")
        if not recipient_id:
            return
        clean_queue_ids = []
        for row_id in queue_ids or []:
            try:
                row_id = int(row_id or 0)
            except (TypeError, ValueError):
                row_id = 0
            if row_id > 0:
                clean_queue_ids.append(row_id)
        sent_rows = [
            (
                recipient_id,
                _digest_event_json(event),
                float(enqueued_at or 0),
                float(delivered_at or 0),
            )
            for event, enqueued_at, delivered_at in (sent_events or [])
        ]

        def _operation():
            try:
                self._conn.execute("BEGIN")
                if sent_rows:
                    self._conn.executemany(
                        "INSERT INTO digest_sent_events "
                        "(recipient_id, event_json, enqueued_at, delivered_at) "
                        "VALUES (?,?,?,?)",
                        sent_rows,
                    )
                for i in range(0, len(clean_queue_ids), 500):
                    chunk = clean_queue_ids[i:i + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    self._conn.execute(
                        "DELETE FROM digest_queued_events "
                        f"WHERE recipient_id=? AND id IN ({placeholders})",
                        (recipient_id, *chunk),
                    )
                self._prune_digest_sent_events_uncommitted(
                    recipient_id,
                    keep=sent_cap,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        self._run_sqlite_write_with_lock_retry(_operation, surface="digest")

    def prune_digest_sent_events(self, recipient_id: str, *, keep: int = 200):
        """Keep only the newest sent digest events for one recipient."""
        with self._conn:
            self._prune_digest_sent_events_uncommitted(
                str(recipient_id or ""),
                keep=keep,
            )

    def _prune_digest_sent_events_uncommitted(
        self,
        recipient_id: str,
        *,
        keep: int,
    ):
        if not recipient_id:
            return
        keep = max(0, int(keep or 0))
        row = self._conn.execute(
            "SELECT COUNT(*) FROM digest_sent_events WHERE recipient_id=?",
            (recipient_id,),
        ).fetchone()
        count = int((row[0] if row else 0) or 0)
        excess = count - keep
        if excess <= 0:
            return
        self._conn.execute(
            "DELETE FROM digest_sent_events WHERE id IN ("
            "SELECT id FROM digest_sent_events "
            "WHERE recipient_id=? "
            "ORDER BY delivered_at ASC, id ASC "
            "LIMIT ?"
            ")",
            (recipient_id, excess),
        )

    # -- Engineer settings & journal -------------------------------------------

    def save_engineer_settings(self, group_name: str, settings: dict):
        """Upsert engineer settings for a group."""
        enabled_events = json.dumps(
            settings.get("enabled_events",
                         ["agent_started", "task_dispatched",
                          "task_derived", "task_health_alert"]))
        self._conn.execute("""
            INSERT OR REPLACE INTO engineer_settings
                (group_name, push_interval, max_interval, heartbeat_interval,
                 default_worker_concurrency, autonomy_mode,
                 wave_size_preference, same_agent_follow_up_preference,
                 digest_verbosity, escalation_style,
                 paused,
                 custom_instructions, restrict_to_created_agents,
                 pending_question, pending_note,
                 pending_note_kind, pending_note_set_at,
                 pending_note_actor_id, enabled_events,
                 engineer_provider, engineer_boot_command,
                 engineer_model, engineer_reasoning_effort,
                 engineer_directory, engineer_profile,
                 engineer_shell, engineer_tab_color,
                 pending_question_set_at,
                 pending_question_actor_id,
                 engineer_can_override_worker_provider)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            group_name,
            settings.get("push_interval", 60),
            settings.get("max_interval", 300),
            settings.get("heartbeat_interval",
                         settings.get("max_interval", 300)),
            settings.get("default_worker_concurrency", 2),
            settings.get("autonomy_mode", "dispatch_when_clear"),
            settings.get("wave_size_preference", "small"),
            settings.get("same_agent_follow_up_preference", "balanced"),
            settings.get("digest_verbosity", "balanced"),
            settings.get("escalation_style", "note_then_ask"),
            1 if settings.get("paused", False) else 0,
            settings.get("custom_instructions", ""),
            1 if settings.get("restrict_to_created_agents", False) else 0,
            settings.get("pending_question", ""),
            settings.get("pending_note", ""),
            settings.get("pending_note_kind", ""),
            float(settings.get("pending_note_set_at", 0) or 0),
            settings.get("pending_note_actor_id", ""),
            enabled_events,
            settings.get("engineer_provider", ""),
            settings.get("engineer_boot_command", ""),
            settings.get("engineer_model", ""),
            settings.get("engineer_reasoning_effort", ""),
            settings.get("engineer_directory", ""),
            settings.get("engineer_profile", ""),
            settings.get("engineer_shell", ""),
            settings.get("engineer_tab_color", ""),
            float(settings.get("pending_question_set_at", 0) or 0),
            settings.get("pending_question_actor_id", ""),
            1 if settings.get(
                "engineer_can_override_worker_provider", True) else 0,
        ))
        self._conn.commit()

    async def save_engineer_settings_async(self, group_name: str, settings: dict):
        """Queue and await a engineer-settings save without blocking the event loop."""
        return await self._enqueue_async_write(
            "engineer_settings",
            "save_engineer_settings",
            group_name,
            _snapshot_db_payload(settings or {}),
        )

    def save_agent_digest_settings(self, agent_id: str, settings: dict):
        """Upsert per-agent digest settings."""
        enabled_events = json.dumps(
            settings.get(
                "enabled_events",
                [
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ],
            )
        )
        self._conn.execute(
            """
            INSERT OR REPLACE INTO agent_digest_settings
                (agent_id, paused, push_interval, max_interval,
                 heartbeat_interval, digest_verbosity, enabled_events,
                 architect_digest, wake_on_digest, suppress_empty)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                agent_id,
                1 if settings.get("paused", False) else 0,
                settings.get("push_interval", 60),
                settings.get("max_interval", 300),
                settings.get(
                    "heartbeat_interval",
                    settings.get("max_interval", 300),
                ),
                settings.get("digest_verbosity", "balanced"),
                enabled_events,
                1 if settings.get("architect_digest", False) else 0,
                1 if settings.get("wake_on_digest", False) else 0,
                1 if settings.get("suppress_empty", False) else 0,
            ),
        )
        self._conn.commit()

    def load_engineer_settings(self, group_name: str) -> dict | None:
        """Load engineer settings for a group. Returns None if not set."""
        row = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, "
            "default_worker_concurrency, autonomy_mode, "
            "wave_size_preference, same_agent_follow_up_preference, "
            "digest_verbosity, escalation_style, paused, "
            "custom_instructions, restrict_to_created_agents, "
            "pending_question, pending_note, pending_note_kind, "
            "pending_note_set_at, pending_note_actor_id, enabled_events, "
            "engineer_provider, engineer_boot_command, "
            "engineer_model, engineer_reasoning_effort, "
            "engineer_directory, engineer_profile, "
            "engineer_shell, engineer_tab_color, "
            "pending_question_set_at, "
            "pending_question_actor_id, "
            "engineer_can_override_worker_provider "
            "FROM engineer_settings "
            "WHERE group_name=?", (group_name,)).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[18])
        except (json.JSONDecodeError, TypeError):
            enabled = [
                "agent_started",
                "task_dispatched",
                "task_derived",
                "task_health_alert",
            ]
        heartbeat_interval = row[3]
        if heartbeat_interval is None or (
                heartbeat_interval == 300 and row[2] != 300):
            heartbeat_interval = row[2]
        return {
            "group": row[0],
            "push_interval": row[1],
            "max_interval": row[2],
            "heartbeat_interval": heartbeat_interval,
            "default_worker_concurrency": row[4],
            "autonomy_mode": row[5],
            "wave_size_preference": row[6] if len(row) > 6 else "small",
            "same_agent_follow_up_preference": (
                row[7] if len(row) > 7 else "balanced"
            ),
            "digest_verbosity": row[8] if len(row) > 8 else "balanced",
            "escalation_style": row[9] if len(row) > 9 else "note_then_ask",
            "paused": bool(row[10]),
            "custom_instructions": row[11],
            "restrict_to_created_agents": bool(row[12]),
            "pending_question": row[13],
            "pending_note": row[14],
            "pending_note_kind": row[15],
            "pending_note_set_at": row[16] if len(row) > 16 else 0.0,
            "pending_note_actor_id": row[17] if len(row) > 17 else "",
            "enabled_events": enabled,
            "engineer_provider": row[19] if len(row) > 19 else "",
            "engineer_boot_command": row[20] if len(row) > 20 else "",
            "engineer_model": row[21] if len(row) > 21 else "",
            "engineer_reasoning_effort": row[22] if len(row) > 22 else "",
            "engineer_directory": row[23] if len(row) > 23 else "",
            "engineer_profile": row[24] if len(row) > 24 else "",
            "engineer_shell": row[25] if len(row) > 25 else "",
            "engineer_tab_color": row[26] if len(row) > 26 else "",
            "pending_question_set_at": row[27] if len(row) > 27 else 0.0,
            "pending_question_actor_id": row[28] if len(row) > 28 else "",
            "engineer_can_override_worker_provider": (
                bool(row[29]) if len(row) > 29 else True
            ),
        }

    def load_agent_digest_settings(self, agent_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT agent_id, paused, push_interval, max_interval, "
            "heartbeat_interval, digest_verbosity, enabled_events, "
            "architect_digest, wake_on_digest "
            "FROM agent_digest_settings WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[6])
        except (json.JSONDecodeError, TypeError):
            enabled = [
                "agent_started",
                "task_dispatched",
                "task_derived",
                "task_health_alert",
            ]
        heartbeat_interval = row[4]
        if heartbeat_interval is None or (
                heartbeat_interval == 300 and row[3] != 300):
            heartbeat_interval = row[3]
        return {
            "agent_id": row[0],
            "paused": bool(row[1]),
            "push_interval": row[2],
            "max_interval": row[3],
            "heartbeat_interval": heartbeat_interval,
            "digest_verbosity": row[5] if len(row) > 5 else "balanced",
            "enabled_events": enabled,
            "architect_digest": bool(row[7]) if len(row) > 7 else False,
            "wake_on_digest": bool(row[8]) if len(row) > 8 else False,
        }

    def delete_engineer_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM engineer_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def agent_exists(self, agent_id: str) -> bool:
        """Return whether an agent/cell row exists in SQLite."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM agents WHERE id=? LIMIT 1",
            (agent_id,),
        ).fetchone()
        return bool(row)

    def delete_agent_digest_settings(self, agent_id: str):
        self._conn.execute(
            "DELETE FROM agent_digest_settings WHERE agent_id=?",
            (agent_id,),
        )
        self._conn.commit()

    def load_all_engineer_settings(self) -> dict[str, dict]:
        """Load engineer settings for all groups. Returns {group: settings}."""
        rows = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, "
            "default_worker_concurrency, autonomy_mode, "
            "wave_size_preference, same_agent_follow_up_preference, "
            "digest_verbosity, escalation_style, paused, "
            "custom_instructions, restrict_to_created_agents, "
            "pending_question, pending_note, pending_note_kind, "
            "pending_note_set_at, pending_note_actor_id, enabled_events, "
            "engineer_provider, engineer_boot_command, "
            "engineer_model, engineer_reasoning_effort, "
            "engineer_directory, engineer_profile, "
            "engineer_shell, engineer_tab_color, "
            "pending_question_set_at, "
            "pending_question_actor_id, "
            "engineer_can_override_worker_provider "
            "FROM engineer_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[18])
            except (json.JSONDecodeError, TypeError):
                enabled = [
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ]
            heartbeat_interval = row[3]
            if heartbeat_interval is None or (
                    heartbeat_interval == 300 and row[2] != 300):
                heartbeat_interval = row[2]
            result[row[0]] = {
                "group": row[0],
                "push_interval": row[1],
                "max_interval": row[2],
                "heartbeat_interval": heartbeat_interval,
                "default_worker_concurrency": row[4],
                "autonomy_mode": row[5],
                "wave_size_preference": row[6] if len(row) > 6 else "small",
                "same_agent_follow_up_preference": (
                    row[7] if len(row) > 7 else "balanced"
                ),
                "digest_verbosity": row[8] if len(row) > 8 else "balanced",
                "escalation_style": row[9] if len(row) > 9 else "note_then_ask",
                "paused": bool(row[10]),
                "custom_instructions": row[11],
                "restrict_to_created_agents": bool(row[12]),
                "pending_question": row[13],
                "pending_note": row[14],
                "pending_note_kind": row[15],
                "pending_note_set_at": row[16] if len(row) > 16 else 0.0,
                "pending_note_actor_id": row[17] if len(row) > 17 else "",
                "enabled_events": enabled,
                "engineer_provider": row[19] if len(row) > 19 else "",
                "engineer_boot_command": row[20] if len(row) > 20 else "",
                "engineer_model": row[21] if len(row) > 21 else "",
                "engineer_reasoning_effort": row[22] if len(row) > 22 else "",
                "engineer_directory": row[23] if len(row) > 23 else "",
                "engineer_profile": row[24] if len(row) > 24 else "",
                "engineer_shell": row[25] if len(row) > 25 else "",
                "engineer_tab_color": row[26] if len(row) > 26 else "",
                "pending_question_set_at": row[27] if len(row) > 27 else 0.0,
                "pending_question_actor_id": row[28] if len(row) > 28 else "",
                "engineer_can_override_worker_provider": (
                    bool(row[29]) if len(row) > 29 else True
                ),
            }
        return result

    def load_all_agent_digest_settings(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT agent_id, paused, push_interval, max_interval, "
            "heartbeat_interval, digest_verbosity, enabled_events, "
            "architect_digest, wake_on_digest, suppress_empty "
            "FROM agent_digest_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[6])
            except (json.JSONDecodeError, TypeError):
                enabled = [
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ]
            heartbeat_interval = row[4]
            if heartbeat_interval is None or (
                    heartbeat_interval == 300 and row[3] != 300):
                heartbeat_interval = row[3]
            result[row[0]] = {
                "agent_id": row[0],
                "paused": bool(row[1]),
                "push_interval": row[2],
                "max_interval": row[3],
                "heartbeat_interval": heartbeat_interval,
                "digest_verbosity": row[5] if len(row) > 5 else "balanced",
                "enabled_events": enabled,
                "architect_digest": bool(row[7]) if len(row) > 7 else False,
                "wake_on_digest": bool(row[8]) if len(row) > 8 else False,
                "suppress_empty": bool(row[9]) if len(row) > 9 else False,
            }
        return result

    def save_journal_entry(self, group_name: str, timestamp: float,
                           entry_type: str, entry: str,
                           author_cell_id: str = "",
                           source_key: str = "",
                           return_inserted: bool = False):
        """Insert a engineer journal entry. Returns the new row ID."""
        author_cell_id = str(author_cell_id or "").strip()
        source_key = str(source_key or "").strip()
        inserted = True
        c = self._conn.execute(
            "INSERT OR IGNORE INTO engineer_journal "
            "(group_name, timestamp, entry_type, entry, author_cell_id, "
            "source_key) VALUES (?,?,?,?,?,?)",
            (group_name, timestamp, entry_type, entry, author_cell_id,
             source_key))
        if source_key and c.rowcount == 0:
            inserted = False
            row = self._conn.execute(
                "SELECT id FROM engineer_journal WHERE group_name=? "
                "AND author_cell_id=? AND source_key=?",
                (group_name, author_cell_id, source_key),
            ).fetchone()
            entry_id = int(row[0]) if row else 0
        else:
            entry_id = int(c.lastrowid or 0)
        self._conn.commit()
        if return_inserted:
            return entry_id, inserted
        return entry_id

    def load_journal_entries(self, group_name: str, limit: int = 20,
                             entry_type: str = "",
                             author_cell_id: str = "") -> list[dict]:
        """Load recent journal entries for a group, newest first."""
        filters = ["group_name=?"]
        params = [group_name]
        if entry_type:
            filters.append("entry_type=?")
            params.append(entry_type)
        author_cell_id = str(author_cell_id or "").strip()
        if author_cell_id:
            filters.append("author_cell_id=?")
            params.append(author_cell_id)
        params.append(limit)
        rows = self._conn.execute(
            "SELECT id, group_name, timestamp, entry_type, entry, "
            "author_cell_id FROM engineer_journal WHERE "
            + " AND ".join(filters)
            + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [{"id": r[0], "group": r[1], "timestamp": r[2],
                 "type": r[3], "entry": r[4],
                 "author_cell_id": r[5]} for r in rows]

    def load_decision(self, decision_id: str) -> dict | None:
        """Load one persisted architect decision by id."""
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return None
        cursor = self._conn.execute(
            "SELECT id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at "
            "FROM decisions WHERE id=?",
            (decision_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return _decode_decision_row(row, cols)

    def save_decision(self, row_dict: dict) -> dict:
        """Upsert one architect decision and return the normalized row."""
        row = dict(row_dict or {})
        decision_id = str(row.get("id", "") or "").strip()
        if not decision_id:
            raise ValueError("decision id is required")

        existing = self.load_decision(decision_id) or {}
        now_ts = int(datetime.now(timezone.utc).timestamp())
        architect_id = str(
            row.get("architect_id", existing.get("architect_id", "")) or ""
        ).strip()
        if not architect_id:
            raise ValueError("architect_id is required")

        created_at = _decision_int(
            row.get("created_at", existing.get("created_at", now_ts)),
            now_ts,
        )
        updated_at = _decision_int(
            row.get("updated_at", now_ts),
            now_ts,
        )
        supersedes = row.get("supersedes", existing.get("supersedes", None))
        supersedes = str(supersedes or "").strip() or None
        linked_task_ids = _decision_json_list(
            row.get(
                "linked_task_ids",
                existing.get("linked_task_ids", []),
            )
        )
        linked_engineer_ids = _decision_json_list(
            row.get(
                "linked_engineer_ids",
                existing.get("linked_engineer_ids", []),
            )
        )
        archived = bool(row.get("archived", existing.get("archived", False)))

        self._conn.execute(
            "INSERT OR REPLACE INTO decisions "
            "(id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                architect_id,
                str(row.get("title", existing.get("title", "")) or ""),
                str(row.get("rationale", existing.get("rationale", "")) or ""),
                str(row.get("status", existing.get("status", "proposed")) or "proposed"),
                supersedes,
                json.dumps(linked_task_ids),
                json.dumps(linked_engineer_ids),
                1 if archived else 0,
                created_at,
                updated_at,
            ),
        )
        self._conn.commit()
        saved = self.load_decision(decision_id)
        if not saved:
            raise RuntimeError(f"failed to load saved decision {decision_id}")
        return saved

    async def save_decision_async(self, row_dict: dict) -> dict:
        """Queue and await a decision save without blocking the event loop."""
        return await self._enqueue_async_write(
            "decisions",
            "save_decision",
            _snapshot_db_payload(row_dict or {}),
        )

    def load_decisions_for_architect(self, architect_id: str, *,
                                     include_archived: bool = False) -> list[dict]:
        """Load persisted decisions for one architect, newest first."""
        architect_id = str(architect_id or "").strip()
        if not architect_id:
            return []
        query = (
            "SELECT id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at "
            "FROM decisions WHERE architect_id=?"
        )
        params = [architect_id]
        if not include_archived:
            query += " AND archived=0"
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC"
        cursor = self._conn.execute(query, tuple(params))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_decision_row(row, cols) for row in rows]

    def load_all_decisions(self, *, include_archived: bool = False) -> list[dict]:
        """Load all persisted architect decisions, newest first."""
        query = (
            "SELECT id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at "
            "FROM decisions"
        )
        if not include_archived:
            query += " WHERE archived=0"
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC"
        cursor = self._conn.execute(query)
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_decision_row(row, cols) for row in rows]

    def delete_decision(self, decision_id: str) -> dict | None:
        """Soft-delete one decision by marking it archived."""
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return None
        existing = self.load_decision(decision_id)
        if not existing:
            return None
        self._conn.execute(
            "UPDATE decisions SET archived=1, updated_at=? WHERE id=?",
            (int(datetime.now(timezone.utc).timestamp()), decision_id),
        )
        self._conn.commit()
        return self.load_decision(decision_id)

    def hard_delete_decision(self, decision_id: str) -> None:
        """Permanently delete one decision row."""
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return
        self._conn.execute("DELETE FROM decisions WHERE id=?", (decision_id,))
        self._conn.commit()

    def load_pending_hire(self, hire_id: str) -> dict | None:
        """Load one persisted pending hire by id."""
        hire_id = str(hire_id or "").strip()
        if not hire_id:
            return None
        cursor = self._conn.execute(
            "SELECT id, architect_id, requested_name, requested_command, "
            "requested_provider, requested_directory, status, resolution_note, "
            "created_at, resolved_at, created_engineer_id "
            "FROM pending_hires WHERE id=?",
            (hire_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return _decode_pending_hire_row(row, cols)

    def save_pending_hire(self, row_dict: dict) -> dict:
        """Upsert one pending-hire row and return the normalized row."""
        row = dict(row_dict or {})
        hire_id = str(row.get("id", "") or "").strip()
        if not hire_id:
            raise ValueError("pending hire id is required")

        existing = self.load_pending_hire(hire_id) or {}
        now_ts = int(datetime.now(timezone.utc).timestamp())
        architect_id = str(
            row.get("architect_id", existing.get("architect_id", "")) or ""
        ).strip()
        if not architect_id:
            raise ValueError("architect_id is required")
        requested_name = str(
            row.get("requested_name", existing.get("requested_name", "")) or ""
        ).strip()
        if not requested_name:
            raise ValueError("requested_name is required")

        status = str(
            row.get("status", existing.get("status", "pending")) or "pending"
        ).strip() or "pending"
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError(
                "status must be one of: pending, approved, rejected"
            )
        created_at = _decision_int(
            row.get("created_at", existing.get("created_at", now_ts)),
            now_ts,
        )
        prior_status = str(existing.get("status", "") or "").strip()
        if status == "pending":
            resolved_at = 0
        elif "resolved_at" in row:
            resolved_at = _decision_int(row.get("resolved_at", now_ts), now_ts)
        elif prior_status == status and existing.get("resolved_at", 0):
            resolved_at = _decision_int(existing.get("resolved_at", 0), now_ts)
        else:
            resolved_at = now_ts

        self._conn.execute(
            "INSERT OR REPLACE INTO pending_hires "
            "(id, architect_id, requested_name, requested_command, "
            "requested_provider, requested_directory, status, resolution_note, "
            "created_at, resolved_at, created_engineer_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                hire_id,
                architect_id,
                requested_name,
                str(
                    row.get(
                        "requested_command",
                        existing.get("requested_command", ""),
                    ) or ""
                ),
                str(
                    row.get(
                        "requested_provider",
                        existing.get("requested_provider", ""),
                    ) or ""
                ),
                str(
                    row.get(
                        "requested_directory",
                        existing.get("requested_directory", ""),
                    ) or ""
                ),
                status,
                str(
                    row.get(
                        "resolution_note",
                        existing.get("resolution_note", ""),
                    ) or ""
                ),
                created_at,
                resolved_at,
                str(
                    row.get(
                        "created_engineer_id",
                        existing.get("created_engineer_id", ""),
                    ) or ""
                ),
            ),
        )
        self._conn.commit()
        saved = self.load_pending_hire(hire_id)
        if not saved:
            raise RuntimeError(f"failed to load saved pending hire {hire_id}")
        return saved

    async def save_pending_hire_async(self, row_dict: dict) -> dict:
        """Queue and await a pending-hire save without blocking the event loop."""
        return await self._enqueue_async_write(
            "pending_hires",
            "save_pending_hire",
            _snapshot_db_payload(row_dict or {}),
        )

    def load_pending_hires(self, *, status_filter: str = "",
                           architect_id: str = "") -> list[dict]:
        """Load persisted pending-hire rows, newest first."""
        query = (
            "SELECT id, architect_id, requested_name, requested_command, "
            "requested_provider, requested_directory, status, resolution_note, "
            "created_at, resolved_at, created_engineer_id "
            "FROM pending_hires WHERE 1=1"
        )
        params: list = []
        architect_id = str(architect_id or "").strip()
        if architect_id:
            query += " AND architect_id=?"
            params.append(architect_id)
        status_filter = str(status_filter or "").strip()
        if status_filter:
            query += " AND status=?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC, id DESC"
        cursor = self._conn.execute(query, tuple(params))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_pending_hire_row(row, cols) for row in rows]

    def delete_pending_hire(self, hire_id: str) -> None:
        """Permanently delete one pending-hire row."""
        hire_id = str(hire_id or "").strip()
        if not hire_id:
            return
        self._conn.execute("DELETE FROM pending_hires WHERE id=?", (hire_id,))
        self._conn.commit()

    def save_engineer_task_log_entry(self, record: dict) -> int:
        """Insert a persisted Engineer dispatch/worklog row."""
        c = self._conn.execute(
            "INSERT INTO engineer_task_log "
            "(group_name, task_id, task_title, agent_id, agent_name, "
            "agent_slug, agent_owned, started_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                record["group"],
                record["task_id"],
                record.get("task_title", ""),
                record["agent_id"],
                record.get("agent_name", ""),
                record.get("agent_slug", ""),
                1 if record.get("agent_owned") else 0,
                record["started_at"],
            ),
        )
        self._conn.commit()
        return c.lastrowid

    def load_engineer_task_log(self, group_name: str, limit: int = 100) -> list[dict]:
        """Load recent persisted Engineer dispatch rows for a group."""
        rows = self._conn.execute(
            "SELECT id, group_name, task_id, task_title, agent_id, agent_name, "
            "agent_slug, agent_owned, started_at FROM engineer_task_log "
            "WHERE group_name=? ORDER BY started_at DESC, id DESC LIMIT ?",
            (group_name, limit),
        ).fetchall()
        return [
            {
                "id": row[0],
                "group": row[1],
                "task_id": row[2],
                "task_title": row[3],
                "agent_id": row[4],
                "agent_name": row[5],
                "agent_slug": row[6],
                "agent_owned": bool(row[7]),
                "started_at": row[8],
            }
            for row in rows
        ]

    def trim_engineer_task_log(self, group_name: str, limit: int = 200):
        """Trim persisted Engineer worklog rows for a group to ``limit``."""
        self._conn.execute(
            "DELETE FROM engineer_task_log WHERE group_name=? AND id NOT IN ("
            "SELECT id FROM engineer_task_log WHERE group_name=? "
            "ORDER BY started_at DESC, id DESC LIMIT ?"
            ")",
            (group_name, group_name, limit),
        )
        self._conn.commit()

    def rename_engineer_task_log_group(self, old_name: str, new_name: str):
        """Move persisted Engineer worklog rows to a renamed group."""
        self._conn.execute(
            "UPDATE engineer_task_log SET group_name=? WHERE group_name=?",
            (new_name, old_name),
        )
        self._conn.commit()

    def save_agent_history(self, record: dict):
        """Insert or replace an agent history record."""
        self._conn.execute("""
            INSERT OR REPLACE INTO agent_history
                (id, name, slug, "group", agent_type, template,
                 created_at, removed_at, worktree_branch,
                 total_tokens_in, total_tokens_out, total_tasks, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            record["id"], record["name"], record.get("slug", ""),
            record.get("group", ""), record.get("agent_type", ""),
            record.get("template", ""), record["created_at"],
            record.get("removed_at"), record.get("worktree_branch", ""),
            record.get("total_tokens_in", 0),
            record.get("total_tokens_out", 0),
            record.get("total_tasks", 0),
            record.get("status", "active"),
        ))
        self._conn.commit()

    def update_agent_history(self, agent_id: str, **fields):
        """Update specific fields on an agent history record."""
        if not fields:
            return
        allowed = {"name", "slug", "group", "agent_type", "template",
                   "removed_at", "worktree_branch", "total_tokens_in",
                   "total_tokens_out", "total_tasks", "status"}
        parts = []
        vals = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            col = f'"{k}"' if k == "group" else k
            parts.append(f"{col}=?")
            vals.append(v)
        if not parts:
            return
        vals.append(agent_id)
        self._conn.execute(
            f"UPDATE agent_history SET {','.join(parts)} WHERE id=?",
            vals)
        self._conn.commit()

    def save_agent_task(self, record: dict):
        """Insert an agent-task association."""
        self._conn.execute(
            "INSERT INTO agent_tasks "
            "(agent_id, task_id, task_title, started_at, completed_at, "
            "outcome) VALUES (?,?,?,?,?,?)",
            (record["agent_id"], record["task_id"],
             record["task_title"], record["started_at"],
             record.get("completed_at"), record.get("outcome", "")))
        self._conn.commit()

    def update_agent_task(self, agent_id: str, task_id: str, **fields):
        """Update an agent-task record (completed_at, outcome)."""
        parts = []
        vals = []
        for k in ("completed_at", "outcome"):
            if k in fields:
                parts.append(f"{k}=?")
                vals.append(fields[k])
        if not parts:
            return
        vals.extend([agent_id, task_id])
        self._conn.execute(
            f"UPDATE agent_tasks SET {','.join(parts)} "
            f"WHERE agent_id=? AND task_id=?", vals)
        self._conn.commit()

    def save_agent_message(self, record: dict):
        """Insert an agent message record."""
        self._conn.execute(
            "INSERT INTO agent_messages "
            "(agent_id, task_id, timestamp, action, message) "
            "VALUES (?,?,?,?,?)",
            (record["agent_id"], record.get("task_id", ""),
             record["timestamp"], record["action"],
             record.get("message", "")))
        self._conn.commit()

    def save_agent_peer_message(self, record: dict) -> dict:
        """Persist one canonical peer message.

        The message ID is the idempotency boundary. Re-saving the same
        deterministic ID is a no-op and returns the first stored row rather
        than overwriting delivery/thread metadata.
        """
        normalized = _normalize_agent_peer_message_record(record)
        values = _agent_peer_message_insert_values(normalized)
        placeholders = ",".join(["?"] * len(_AGENT_PEER_MESSAGE_COLUMNS))
        columns = ", ".join(_AGENT_PEER_MESSAGE_COLUMNS)
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_peer_messages "
            f"({columns}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return self.load_agent_peer_message(normalized["id"]) or {}

    def save_peer_message(self, record: dict) -> dict:
        """Compatibility alias for durable peer-message persistence."""
        return self.save_agent_peer_message(record)

    def save_direct_message(self, record: dict) -> dict:
        """Persist one direct/conversation message.

        V1 stores direct messages in the existing agent_peer_messages table.
        This named helper is the preferred API for user↔agent rows; legacy
        peer helpers remain wrappers over the same physical store.
        """
        return self.save_agent_peer_message(record)

    def load_agent_peer_message(self, message_id: str) -> dict | None:
        """Load one canonical peer message by ID."""
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        row = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE id=?",
            (message_id,),
        ).fetchone()
        if not row:
            return None
        return _decode_agent_peer_message_row(row)

    def load_peer_message(self, message_id: str) -> dict | None:
        """Compatibility alias for loading one peer message."""
        return self.load_agent_peer_message(message_id)

    def load_direct_message(self, message_id: str) -> dict | None:
        """Load one direct/conversation message by ID."""
        return self.load_agent_peer_message(message_id)

    def load_agent_peer_messages_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent peer messages involving one agent, newest first."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = [
            "(sender_id=? OR recipient_id=?)",
            _AGENT_PEER_MESSAGE_NON_USER_WHERE,
        ]
        params: list = [agent_id, agent_id]
        peer_id = str(peer_id or "").strip()
        if peer_id:
            where.append(
                "((sender_id=? AND recipient_id=?) OR "
                "(sender_id=? AND recipient_id=?))"
            )
            params.extend([agent_id, peer_id, peer_id, agent_id])
        thread_id = str(thread_id or "").strip()
        if thread_id:
            where.append("thread_id=?")
            params.append(thread_id)
        since_value = _peer_float(since, 0)
        if since_value > 0:
            where.append("created_at>?")
            params.append(since_value)
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_peer_messages_for_architect(
        self,
        architect_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent persisted peer messages for one Architect."""
        return self.load_agent_peer_messages_for_agent(
            architect_id,
            limit=limit,
            since=since,
            peer_id=peer_id,
            thread_id=thread_id,
            include_archived=include_archived,
        )

    def load_direct_messages_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        peer_kind: str = "user",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent direct messages involving one agent, newest first.

        By default this returns the V1 user↔agent lane: rows involving the
        given agent whose opposite participant has kind ``user``.  ``peer_id``
        can narrow this to a specific synthetic user id, while ``thread_id``
        keeps future multi-thread callers possible without changing storage.
        """
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = [
            "(sender_id=? OR recipient_id=?)",
            _AGENT_PEER_MESSAGE_USER_WHERE,
        ]
        params: list = [agent_id, agent_id]
        peer_id = str(peer_id or "").strip()
        peer_kind = str(peer_kind or "").strip()
        if peer_id:
            where.append(
                "((sender_id=? AND recipient_id=?) OR "
                "(sender_id=? AND recipient_id=?))"
            )
            params.extend([agent_id, peer_id, peer_id, agent_id])
        elif peer_kind and peer_kind != "user":
            where.append(
                "((sender_id=? AND recipient_kind=?) OR "
                "(recipient_id=? AND sender_kind=?))"
            )
            params.extend([agent_id, peer_kind, agent_id, peer_kind])
        thread_id = str(thread_id or "").strip()
        if thread_id:
            where.append("thread_id=?")
            params.append(thread_id)
        since_value = _peer_float(since, 0)
        if since_value > 0:
            where.append("created_at>?")
            params.append(since_value)
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_agent_peer_messages_for_thread(
        self,
        thread_id: str,
        *,
        agent_id: str = "",
        limit: int = 1000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load a peer-message thread oldest first."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []
        limit = max(1, min(int(limit or 1000), 5000))
        where = ["thread_id=?", _AGENT_PEER_MESSAGE_NON_USER_WHERE]
        params: list = [thread_id]
        agent_id = str(agent_id or "").strip()
        if agent_id:
            where.append("(sender_id=? OR recipient_id=?)")
            params.extend([agent_id, agent_id])
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_direct_messages_for_thread(
        self,
        thread_id: str,
        *,
        agent_id: str = "",
        limit: int = 1000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load one direct-message thread oldest first."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []
        limit = max(1, min(int(limit or 1000), 5000))
        where = ["thread_id=?", _AGENT_PEER_MESSAGE_USER_WHERE]
        params: list = [thread_id]
        agent_id = str(agent_id or "").strip()
        if agent_id:
            where.append("(sender_id=? OR recipient_id=?)")
            params.extend([agent_id, agent_id])
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_buffered_agent_peer_messages(
        self,
        recipient_id: str,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """Load undelivered peer messages for replay, oldest first."""
        recipient_id = str(recipient_id or "").strip()
        if not recipient_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages "
            "WHERE recipient_id=? AND delivery_state='buffered' "
            f"AND {_AGENT_PEER_MESSAGE_NON_USER_WHERE} "
            "AND archived_at=0 "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (recipient_id, limit),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_buffered_direct_messages(
        self,
        recipient_id: str,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """Load buffered direct messages for one recipient, oldest first."""
        recipient_id = str(recipient_id or "").strip()
        if not recipient_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages "
            "WHERE recipient_id=? AND delivery_state='buffered' "
            f"AND {_AGENT_PEER_MESSAGE_USER_WHERE} "
            "AND archived_at=0 "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (recipient_id, limit),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_recent_agent_peer_messages_for_group(
        self,
        group_name: str,
        *,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent peer messages in a group, newest first."""
        group_name = str(group_name or "").strip()
        if not group_name:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = ["group_name=?", _AGENT_PEER_MESSAGE_NON_USER_WHERE]
        params: list = [group_name]
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def update_agent_peer_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Persist delivery state for one peer message."""
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        state = str(delivery_state or "").strip()
        if state not in _AGENT_PEER_MESSAGE_DELIVERY_STATES:
            raise ValueError("delivery_state must be delivered, buffered, or failed")
        if delivered_at is None:
            delivered_value = time.time() if state == "delivered" else 0.0
        else:
            delivered_value = _peer_float(delivered_at, 0)
        self._conn.execute(
            "UPDATE agent_peer_messages SET delivery_state=?, "
            "delivery_reason=?, delivered_at=? WHERE id=?",
            (state, str(reason or ""), delivered_value, message_id),
        )
        self._conn.commit()
        return self.load_agent_peer_message(message_id)

    def mark_peer_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Convenience wrapper for delivered/failed peer-message states."""
        return self.update_agent_peer_message_delivery(
            message_id,
            "delivered" if delivered else "failed",
            reason="" if delivered else reason,
            delivered_at=delivered_at,
        )

    def update_direct_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Persist transport delivery state for one direct message."""
        return self.update_agent_peer_message_delivery(
            message_id,
            delivery_state,
            reason=reason,
            delivered_at=delivered_at,
        )

    def mark_direct_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Convenience wrapper for delivered/failed direct-message states."""
        return self.update_direct_message_delivery(
            message_id,
            "delivered" if delivered else "failed",
            reason="" if delivered else reason,
            delivered_at=delivered_at,
        )

    def mark_direct_message_read(
        self,
        message_id: str,
        *,
        read_at: float | None = None,
        reader_id: str = "",
    ) -> dict | None:
        """Persist UI read state for one direct message.

        This intentionally does not alter delivery_state; transport delivery
        and UI read/unread are separate concepts.
        """
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        reader = str(reader_id or "").strip()
        existing = self.load_agent_peer_message(message_id)
        if not existing:
            return None
        if reader and reader != str(existing.get("recipient_id", "") or ""):
            return existing
        read_value = _peer_float(read_at, 0) if read_at is not None else time.time()
        self._conn.execute(
            "UPDATE agent_peer_messages SET read_at=? WHERE id=?",
            (read_value, message_id),
        )
        self._conn.commit()
        return self.load_agent_peer_message(message_id)

    def save_agent_message_history(self, record: dict) -> dict:
        """Insert a persisted user-message recall entry for one agent."""
        agent_id = str(record.get("agent_id", "") or "").strip()
        message = str(record.get("message", "") or "")
        sent_at = float(record.get("sent_at", time.time()) or time.time())
        cur = self._conn.execute(
            "INSERT INTO agent_message_history "
            "(agent_id, message, sent_at) VALUES (?,?,?)",
            (agent_id, message, sent_at),
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "agent_id": agent_id,
            "message": message,
            "sent_at": sent_at,
        }

    def load_agent_message_history(self, agent_id: str,
                                   limit: int = 100) -> list[dict]:
        """Load user-message recall entries for an agent, newest first."""
        agent_id = str(agent_id or "").strip()
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._conn.execute(
            "SELECT id, agent_id, message, sent_at "
            "FROM agent_message_history WHERE agent_id=? "
            "ORDER BY sent_at DESC, id DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        cols = ["id", "agent_id", "message", "sent_at"]
        return [dict(zip(cols, row)) for row in rows]

    def load_agent_history(self, status_filter: str = "",
                           limit: int = 50, offset: int = 0
                           ) -> list[dict]:
        """Load agent history records, active first."""
        sql = (
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status FROM agent_history")
        params: list = []
        if status_filter:
            sql += " WHERE status=?"
            params.append(status_filter)
        sql += (" ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END,"
                " created_at DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_history_detail(self, agent_id: str
                                  ) -> Optional[dict]:
        """Load a single agent history record."""
        row = self._conn.execute(
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status "
            "FROM agent_history WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return dict(zip(cols, row))

    def load_agent_tasks(self, agent_id: str) -> list[dict]:
        """Load task associations for an agent."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, task_title, started_at, "
            "completed_at, outcome FROM agent_tasks "
            "WHERE agent_id=? ORDER BY started_at DESC",
            (agent_id,)).fetchall()
        cols = ["id", "agent_id", "task_id", "task_title",
                "started_at", "completed_at", "outcome"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_messages(self, agent_id: str,
                            limit: int = 100) -> list[dict]:
        """Load messages for an agent, newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, timestamp, action, message "
            "FROM agent_messages WHERE agent_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (agent_id, limit)).fetchall()
        cols = ["id", "agent_id", "task_id", "timestamp",
                "action", "message"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_messages_by_task(self, task_id: str,
                                    limit: int = 100) -> list[dict]:
        """Load messages for a task, newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, timestamp, action, message "
            "FROM agent_messages WHERE task_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (task_id, limit)).fetchall()
        cols = ["id", "agent_id", "task_id", "timestamp",
                "action", "message"]
        return [dict(zip(cols, r)) for r in rows]

    def load_all_agent_tasks(self) -> list[dict]:
        """Load all agent-task associations ordered by start time."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, task_title, started_at, "
            "completed_at, outcome FROM agent_tasks "
            "ORDER BY started_at ASC, id ASC").fetchall()
        cols = ["id", "agent_id", "task_id", "task_title",
                "started_at", "completed_at", "outcome"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_tasks_window(
        self,
        since: float,
        until: float,
        group: str = "",
    ) -> list[dict]:
        """Load agent-task intervals overlapping a metrics window.

        Rows are left-joined to current board task metadata so the caller can
        scope and label utilization without adding new sampling tables.  A
        missing ``completed_at`` is treated as still running at ``until`` for
        overlap selection; the raw ``completed_at`` value is returned.
        """
        since = float(since or 0.0)
        until = float(until or 0.0)
        clauses = [
            "agent_tasks.started_at <= ?",
            "COALESCE(agent_tasks.completed_at, ?) >= ?",
        ]
        params: list = [until, until, since]
        group = str(group or "").strip()
        if group:
            clauses.append("board_tasks.group_name = ?")
            params.append(group)
        rows = self._conn.execute(
            "SELECT agent_tasks.id, agent_tasks.agent_id, "
            "agent_tasks.task_id, agent_tasks.task_title, "
            "agent_tasks.started_at, agent_tasks.completed_at, "
            "agent_tasks.outcome, "
            "board_tasks.group_name, board_tasks.action_name, "
            "board_tasks.assigned_engineer_id, board_tasks.created_at, "
            "board_tasks.updated_at, board_tasks.lane, "
            "board_tasks.worktree_boundary "
            "FROM agent_tasks "
            "LEFT JOIN board_tasks ON board_tasks.id = agent_tasks.task_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY agent_tasks.started_at ASC, agent_tasks.id ASC",
            tuple(params),
        ).fetchall()
        cols = [
            "id",
            "agent_id",
            "task_id",
            "task_title",
            "started_at",
            "completed_at",
            "outcome",
            "group",
            "action_name",
            "assigned_engineer_id",
            "created_at",
            "updated_at",
            "lane",
            "worktree_boundary",
        ]
        out = []
        for row in rows:
            item = dict(zip(cols, row))
            try:
                item["worktree_boundary"] = json.loads(
                    item.get("worktree_boundary", "{}") or "{}"
                )
            except (json.JSONDecodeError, TypeError):
                item["worktree_boundary"] = {}
            out.append(item)
        return out

    def load_mcp_dispatch_calls_window(
        self,
        since: float,
        until: float,
        group: str = "",
    ) -> list[dict]:
        """Load cached Engineer dispatch-tool responses in a time window.

        ``mcp_idempotency`` only stores calls that carried an idempotency key,
        so consumers must treat this as coverage-limited.  The optional
        ``group`` argument is best-effort: when a response exposes a group, it
        is filtered; unscoped responses are returned with ``group=''`` so the
        caller can surface partial coverage instead of fabricating precision.
        """
        since = float(since or 0.0)
        until = float(until or 0.0)
        rows = self._conn.execute(
            "SELECT idempotency_key, surface, tool_name, request_hash, "
            "response_json, created_at, updated_at "
            "FROM mcp_idempotency "
            "WHERE surface = 'engineer' "
            "AND tool_name IN (?, ?) "
            "AND updated_at >= ? AND updated_at <= ? "
            "ORDER BY updated_at ASC, created_at ASC",
            (
                "engineer_task_dispatch",
                "engineer_batch_dispatch",
                since,
                until,
            ),
        ).fetchall()
        requested_group = str(group or "").strip()
        out = []
        for row in rows:
            try:
                raw_response = json.loads(row[4] or "{}")
            except (json.JSONDecodeError, TypeError):
                raw_response = {}
            if not isinstance(raw_response, dict):
                raw_response = {}
            response = _decode_mcp_response_payload(raw_response)
            response_group = _mcp_dispatch_response_group(response)
            if requested_group and response_group \
                    and response_group != requested_group:
                continue
            out.append({
                "idempotency_key": row[0],
                "surface": row[1],
                "tool_name": row[2],
                "request_hash": row[3],
                "response": response,
                "raw_response": raw_response,
                "response_json": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "group": response_group,
                "unscoped": not bool(response_group),
            })
        return out

    def load_all_agent_history_records(self) -> list[dict]:
        """Load all persisted agent history records."""
        rows = self._conn.execute(
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status "
            "FROM agent_history ORDER BY created_at ASC").fetchall()
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return [dict(zip(cols, r)) for r in rows]

    # -- Playbook candidates -----------------------------------------------

    def replace_playbook_candidates(self, candidates: list[dict],
                                    group_name: str = ""):
        """Replace persisted draft playbook candidates."""
        if group_name:
            self._conn.execute(
                "DELETE FROM playbook_candidates WHERE group_name=?",
                (group_name,))
        else:
            self._conn.execute("DELETE FROM playbook_candidates")

        for candidate in candidates:
            self._conn.execute("""
                INSERT OR REPLACE INTO playbook_candidates
                    (id, group_name, family_key, status, created_at,
                     updated_at, name, root_action, labels,
                     normalized_task_family, entry_action, agent_template,
                     workflow, workflow_shape, dispatch_sequence,
                     action_combination, constraints, evidence,
                     supporting_runs, counterexamples)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                candidate["id"],
                candidate.get("group", ""),
                candidate.get("family_key", ""),
                candidate.get("status", "draft"),
                candidate.get("created_at", 0.0),
                candidate.get("updated_at", candidate.get("created_at", 0.0)),
                candidate.get("name", ""),
                candidate.get("root_action", ""),
                json.dumps(candidate.get("labels", [])),
                candidate.get("normalized_task_family", ""),
                candidate.get("entry_action", ""),
                candidate.get("agent_template", ""),
                json.dumps(candidate.get("workflow", [])),
                json.dumps(candidate.get("workflow_shape",
                                         candidate.get("workflow", []))),
                json.dumps(candidate.get("dispatch_sequence", [])),
                json.dumps(candidate.get("action_combination", [])),
                json.dumps(candidate.get("constraints", {})),
                json.dumps(candidate.get("evidence", {})),
                json.dumps(candidate.get("supporting_runs", [])),
                json.dumps(candidate.get("counterexamples", [])),
            ))
        self._conn.commit()

    def load_playbook_candidates(self, group_name: str = "",
                                 limit: int = 50) -> list[dict]:
        """Load persisted draft playbook candidates."""
        params: list = [limit]
        sql = (
            "SELECT id, group_name, family_key, status, created_at, "
            "updated_at, name, root_action, labels, "
            "normalized_task_family, entry_action, agent_template, "
            "workflow, workflow_shape, dispatch_sequence, "
            "action_combination, constraints, evidence, "
            "supporting_runs, counterexamples "
            "FROM playbook_candidates"
        )
        if group_name:
            sql += " WHERE group_name=?"
            params = [group_name, limit]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "group", "family_key", "status", "created_at",
                "updated_at", "name", "root_action", "labels",
                "normalized_task_family", "entry_action", "agent_template",
                "workflow", "workflow_shape", "dispatch_sequence",
                "action_combination", "constraints", "evidence",
                "supporting_runs", "counterexamples"]
        decoded = []
        for row in rows:
            item = dict(zip(cols, row))
            for key, default in (
                ("labels", []),
                ("workflow", []),
                ("workflow_shape", []),
                ("dispatch_sequence", []),
                ("action_combination", []),
                ("constraints", {}),
                ("evidence", {}),
                ("supporting_runs", []),
                ("counterexamples", []),
            ):
                try:
                    item[key] = json.loads(item.get(key, json.dumps(default)))
                except (json.JSONDecodeError, TypeError):
                    item[key] = default.copy() if isinstance(default, dict) else list(default)
            decoded.append(item)
        return decoded

    def load_playbook_candidate(self, candidate_id: str) -> Optional[dict]:
        """Load one persisted draft playbook candidate by ID."""
        rows = self.load_playbook_candidates(limit=1000)
        for row in rows:
            if row["id"] == candidate_id:
                return row
        return None

    def save_playbook(self, playbook: dict):
        """Insert or replace a generated or published playbook record."""
        self._conn.execute("""
            INSERT OR REPLACE INTO playbooks
                (id, group_name, source_candidate_id, status, generated,
                 review_required, created_at, updated_at, published_at,
                 discarded_at, name, description, match_data, entry_action,
                 agent_template, workflow, constraints, evidence,
                 publication_preview)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            playbook["id"],
            playbook.get("group", ""),
            playbook.get("source_candidate_id", ""),
            playbook.get("status", "draft"),
            1 if playbook.get("generated", True) else 0,
            1 if playbook.get("review_required", True) else 0,
            playbook.get("created_at", 0.0),
            playbook.get("updated_at", playbook.get("created_at", 0.0)),
            playbook.get("published_at"),
            playbook.get("discarded_at"),
            playbook.get("name", ""),
            playbook.get("description", ""),
            json.dumps(playbook.get("match", {})),
            playbook.get("entry_action", ""),
            playbook.get("agent_template", ""),
            json.dumps(playbook.get("workflow", [])),
            json.dumps(playbook.get("constraints", {})),
            json.dumps(playbook.get("evidence", {})),
            json.dumps(playbook.get("publication_preview", {})),
        ))
        self._conn.commit()

    def load_playbooks(self, group_name: str = "", status_filter: str = "",
                       limit: int = 50) -> list[dict]:
        """Load persisted playbook drafts or published recipes."""
        sql = (
            "SELECT id, group_name, source_candidate_id, status, generated, "
            "review_required, created_at, updated_at, published_at, "
            "discarded_at, name, description, match_data, entry_action, "
            "agent_template, workflow, constraints, evidence, "
            "publication_preview FROM playbooks"
        )
        clauses = []
        params: list = []
        if group_name:
            clauses.append("group_name=?")
            params.append(group_name)
        if status_filter:
            clauses.append("status=?")
            params.append(status_filter)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "group", "source_candidate_id", "status", "generated",
                "review_required", "created_at", "updated_at",
                "published_at", "discarded_at", "name", "description",
                "match", "entry_action", "agent_template", "workflow",
                "constraints", "evidence", "publication_preview"]
        decoded = []
        for row in rows:
            item = dict(zip(cols, row))
            item["generated"] = bool(item.get("generated", 1))
            item["review_required"] = bool(item.get("review_required", 1))
            for key, default in (
                ("match", {}),
                ("workflow", []),
                ("constraints", {}),
                ("evidence", {}),
                ("publication_preview", {}),
            ):
                try:
                    item[key] = json.loads(item.get(key, json.dumps(default)))
                except (json.JSONDecodeError, TypeError):
                    item[key] = default.copy() if isinstance(default, dict) else list(default)
            decoded.append(item)
        return decoded

    def load_playbook(self, playbook_id: str) -> Optional[dict]:
        """Load one persisted playbook draft or published recipe."""
        rows = self.load_playbooks(limit=1000)
        for row in rows:
            if row["id"] == playbook_id:
                return row
        return None

    def backfill_agent_history(self):
        """Create history records for existing agents that lack them."""
        import time
        if (
            self._column_exists("agents", "role")
            and self._column_exists("agents", "template")
        ):
            template_expr = (
                "CASE WHEN TRIM(COALESCE(role, '')) != '' "
                "THEN role ELSE template END"
            )
        elif self._column_exists("agents", "role"):
            template_expr = "role"
        elif self._column_exists("agents", "template"):
            template_expr = "template"
        else:
            template_expr = "''"
        rows = self._conn.execute(
            f"SELECT id, name, slug, group_name, agent_type, {template_expr}, "
            "worktree_branch, tasks_dispatched FROM agents "
            "WHERE cell_type='agent' AND id NOT IN "
            "(SELECT id FROM agent_history)").fetchall()
        for r in rows:
            self._conn.execute("""
                INSERT OR IGNORE INTO agent_history
                    (id, name, slug, "group", agent_type, template,
                     created_at, worktree_branch, total_tasks, status)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (r[0], r[1], r[2], r[3], r[4], r[5],
                  time.time(), r[6], r[7], "active"))
        self._conn.commit()

    # -- Bulk save (transitional) -------------------------------------------

    def save_all(self, state_dict: dict):
        """Bulk-write entire state to DB (used by migrate_from_json)."""
        c = self._conn.cursor()
        try:
            # Agents
            c.execute("DELETE FROM agents")
            for aid, a in state_dict.get("agents", {}).items():
                item = dict(a) if isinstance(a, dict) else asdict(a)
                item.setdefault("id", aid)
                self._insert_agent_row(c, item)

            # Groups + members
            c.execute("DELETE FROM groups")
            c.execute("DELETE FROM group_members")
            group_slugs = state_dict.get("group_slugs", {})
            for pos, (gname, members) in enumerate(
                    state_dict.get("groups", {}).items()):
                c.execute(
                    "INSERT INTO groups (name, slug, position) VALUES (?,?,?)",
                    (gname, group_slugs.get(gname, ""), pos))
                for mpos, aid in enumerate(members):
                    c.execute(
                        "INSERT INTO group_members "
                        "(group_name, agent_id, position) VALUES (?,?,?)",
                        (gname, aid, mpos))

            # Group settings
            c.execute("DELETE FROM group_settings")
            gs_fields = _group_settings_field_names()
            for gname, gs in state_dict.get("group_settings", {}).items():
                d = dict(gs) if isinstance(gs, dict) else asdict(gs)
                if gs_fields is not None:
                    d = {k: v for k, v in d.items() if k in gs_fields}
                for k in _GS_JSON_FIELDS:
                    if k in d:
                        d[k] = json.dumps(d[k])
                for k in _GS_BOOL_FIELDS:
                    if k in d:
                        d[k] = int(d[k])
                cols = ["group_name"] + list(d.keys())
                placeholders = ",".join(["?"] * len(cols))
                col_str = ",".join(cols)
                c.execute(
                    f"INSERT INTO group_settings ({col_str}) "
                    f"VALUES ({placeholders})",
                    [gname] + list(d.values()))

            # Board lanes
            c.execute("DELETE FROM board_lanes")
            for pos, lane in enumerate(
                    state_dict.get("board_lanes", [])):
                c.execute(
                    "INSERT INTO board_lanes (name, position) VALUES (?,?)",
                    (lane, pos))

            # Board tasks
            c.execute("DELETE FROM board_tasks")
            for tid, t in state_dict.get("board_tasks", {}).items():
                item = dict(t) if isinstance(t, dict) else asdict(t)
                item.setdefault("id", tid)
                self._insert_board_task_row(c, item)

            # Auto-dispatch queues
            c.execute("DELETE FROM auto_dispatch_queue")
            for gname, entries in state_dict.get(
                    "auto_dispatch_queues", {}).items():
                for pos, entry in enumerate(entries):
                    item = dict(entry)
                    c.execute("""
                        INSERT INTO auto_dispatch_queue
                            (group_name, position, task_id, agent_group,
                             max_concurrent, target_agent_id,
                             engineer_owner_id, provider, enqueued_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        gname,
                        pos,
                        item.get("task_id", ""),
                        item.get("agent_group", ""),
                        int(item.get("max_concurrent", 1) or 1),
                        item.get("target_agent_id", ""),
                        item.get("engineer_owner_id", ""),
                        item.get("provider", ""),
                        item.get("enqueued_at", ""),
                    ))

            # UI state
            c.execute("DELETE FROM ui_state")
            for key in (
                "panel_active",
                "board_panel_height",
                "active_group",
                "selected_principal_id",
                "selected_agent_id",
                "standalone_panel_layout",
                "detached_panels",
                "window_bounds",
                "workspace_sidebar_width",
                "engineer_panel_split_fraction",
                "context_panel_split_ratio",
                "supervisor_panel_state",
            ):
                val = state_dict.get(key)
                if val is not None:
                    if key in {
                        "standalone_panel_layout",
                        "detached_panels",
                        "window_bounds",
                        "supervisor_panel_state",
                    }:
                        val = json.dumps(val)
                    c.execute(
                        "INSERT INTO ui_state (key, value) VALUES (?,?)",
                        (key, str(val)))
            if state_dict.get("events_dismissed_attention") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "events_dismissed_attention",
                        json.dumps(state_dict.get("events_dismissed_attention")
                                   or {}),
                    ),
                )
            if state_dict.get("board_filters_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_filters_by_group",
                        json.dumps(state_dict.get("board_filters_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_selected_lanes_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_selected_lanes_by_group",
                        json.dumps(state_dict.get(
                            "board_selected_lanes_by_group") or {}),
                    ),
                )
            if state_dict.get("board_hidden_wide_lanes_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_hidden_wide_lanes_by_group",
                        json.dumps(state_dict.get(
                            "board_hidden_wide_lanes_by_group") or {}),
                    ),
                )
            if state_dict.get("board_saved_views_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_saved_views_by_group",
                        json.dumps(state_dict.get("board_saved_views_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_lane_sorts_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_lane_sorts_by_group",
                        json.dumps(state_dict.get("board_lane_sorts_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_card_density_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_card_density_by_group",
                        json.dumps(state_dict.get("board_card_density_by_group")
                                   or {}),
                    ),
                )

            self._conn.commit()
            self._reseed_task_id_counters()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- Read methods (daemon startup + CLI) --------------------------------

    def load_all(self) -> dict:
        """Load full state from SQLite. Returns dict matching state.json
        structure for easy consumption by MatrixState.load()."""
        c = self._conn.cursor()

        # Agents
        agents = {}
        for row in c.execute(
                "SELECT * FROM agents").fetchall():
            cols = [d[0] for d in c.description]
            d = dict(zip(cols, row))
            # Map group_name back to 'group' for AgentCell
            d["group"] = d.pop("group_name")
            d.setdefault("template", str(d.get("role", "") or ""))
            d.setdefault(
                "created_by_engineer_id",
                str(d.get("owner_engineer_id", "") or ""),
            )
            d["worktree_auto_checkpoint"] = bool(
                d.get("worktree_auto_checkpoint", 0))
            d["checkpoint_on_progress"] = bool(
                d.get("checkpoint_on_progress", 0))
            d["worktree_merge_squash"] = bool(
                d.get("worktree_merge_squash", 1))
            d["persistent"] = bool(d.get("persistent", 0))
            d["session_resume"] = bool(d.get("session_resume", 1))
            d["queue_empty_emitted"] = bool(
                d.get("queue_empty_emitted", 1)
            )
            d["last_progress_at"] = float(d.get("last_progress_at", 0) or 0)
            d["last_heartbeat_at"] = float(d.get("last_heartbeat_at", 0) or 0)
            d["last_activity_at"] = max(
                float(d.get("last_activity_at", 0) or 0),
                d["last_progress_at"],
                d["last_heartbeat_at"],
            )
            d["deleted_at"] = float(d.get("deleted_at", 0) or 0)
            d["permanent_delete_after"] = float(
                d.get("permanent_delete_after", 0) or 0
            )
            raw_specs = d.get("engineer_specializations", "")
            if isinstance(raw_specs, str):
                try:
                    decoded = json.loads(raw_specs or "[]")
                except (json.JSONDecodeError, TypeError):
                    decoded = []
            else:
                decoded = raw_specs or []
            if not isinstance(decoded, list):
                decoded = []
            d["engineer_specializations"] = [
                str(item or "").strip()
                for item in decoded
                if str(item or "").strip()
            ]
            agents[d["id"]] = d

        # Groups (ordered)
        groups = {}
        group_slugs = {}
        for row in c.execute(
                "SELECT name, slug FROM groups ORDER BY position"):
            groups[row[0]] = []
            if row[1]:
                group_slugs[row[0]] = row[1]

        # Group members
        for row in c.execute(
                "SELECT group_name, agent_id FROM group_members "
                "ORDER BY group_name, position"):
            gname, aid = row
            if gname in groups:
                groups[gname].append(aid)

        # Group settings
        group_settings = {}
        rows = c.execute("SELECT * FROM group_settings").fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            gs_fields = _group_settings_field_names()
            for row in rows:
                d = dict(zip(cols, row))
                gname = d.pop("group_name")
                if gs_fields is not None:
                    d = {k: v for k, v in d.items() if k in gs_fields}
                # Decode JSON fields
                for k in _GS_JSON_FIELDS:
                    if k in d and isinstance(d[k], str):
                        try:
                            d[k] = json.loads(d[k])
                        except (json.JSONDecodeError, TypeError):
                            d[k] = [] if k in {"board_default_labels",
                                                "worktree_symlinks",
                                                "default_engineer_specializations",
                                                "architect_enabled_events"} else {}
                    if k == "board_sync_github" and k in d \
                            and not isinstance(d[k], dict):
                        d[k] = {}
                # Decode booleans
                for k in _GS_BOOL_FIELDS:
                    if k in d:
                        d[k] = bool(d[k])
                group_settings[gname] = d

        # Board lanes
        board_lanes = [row[0] for row in c.execute(
            "SELECT name FROM board_lanes ORDER BY position")]

        # Board tasks
        board_tasks = {}
        rows = c.execute("SELECT * FROM board_tasks").fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            for row in rows:
                d = decode_board_task_row(row, cols)
                d.setdefault(
                    "engineer_owner_id",
                    str(d.get("assigned_engineer_id", "") or ""),
                )
                board_tasks[d["id"]] = d

        # UI state
        ui = {}
        for row in c.execute("SELECT key, value FROM ui_state"):
            ui[row[0]] = row[1]
        try:
            board_filters_by_group = json.loads(
                ui.get("board_filters_by_group", "{}") or "{}"
            )
            if not isinstance(board_filters_by_group, dict):
                board_filters_by_group = {}
        except Exception:
            board_filters_by_group = {}
        try:
            board_selected_lanes_by_group = json.loads(
                ui.get("board_selected_lanes_by_group", "{}") or "{}"
            )
            if not isinstance(board_selected_lanes_by_group, dict):
                board_selected_lanes_by_group = {}
        except Exception:
            board_selected_lanes_by_group = {}
        try:
            board_hidden_wide_lanes_by_group = json.loads(
                ui.get("board_hidden_wide_lanes_by_group", "{}") or "{}"
            )
            if not isinstance(board_hidden_wide_lanes_by_group, dict):
                board_hidden_wide_lanes_by_group = {}
        except Exception:
            board_hidden_wide_lanes_by_group = {}
        try:
            board_saved_views_by_group = json.loads(
                ui.get("board_saved_views_by_group", "{}") or "{}"
            )
            if not isinstance(board_saved_views_by_group, dict):
                board_saved_views_by_group = {}
        except Exception:
            board_saved_views_by_group = {}
        try:
            board_lane_sorts_by_group = json.loads(
                ui.get("board_lane_sorts_by_group", "{}") or "{}"
            )
            if not isinstance(board_lane_sorts_by_group, dict):
                board_lane_sorts_by_group = {}
        except Exception:
            board_lane_sorts_by_group = {}
        try:
            board_card_density_by_group = json.loads(
                ui.get("board_card_density_by_group", "{}") or "{}"
            )
            if not isinstance(board_card_density_by_group, dict):
                board_card_density_by_group = {}
        except Exception:
            board_card_density_by_group = {}
        try:
            detached_panels = json.loads(
                ui.get("detached_panels", "{}") or "{}"
            )
            if not isinstance(detached_panels, dict):
                detached_panels = {}
        except Exception:
            detached_panels = {}
        try:
            window_bounds = json.loads(
                ui.get("window_bounds", "{}") or "{}"
            )
            if not isinstance(window_bounds, dict):
                window_bounds = {}
        except Exception:
            window_bounds = {}
        try:
            workspace_sidebar_width = int(
                ui.get("workspace_sidebar_width", "0") or "0"
            )
        except (TypeError, ValueError):
            workspace_sidebar_width = 0
        try:
            context_panel_split_ratio = float(
                ui.get("context_panel_split_ratio", "0.38") or "0.38"
            )
        except (TypeError, ValueError):
            context_panel_split_ratio = 0.38
        try:
            engineer_panel_split_fraction = float(
                ui.get("engineer_panel_split_fraction", "0.30") or "0.30"
            )
        except (TypeError, ValueError):
            engineer_panel_split_fraction = 0.30
        try:
            supervisor_panel_state = json.loads(
                ui.get("supervisor_panel_state", "{}") or "{}"
            )
            if not isinstance(supervisor_panel_state, dict):
                supervisor_panel_state = {}
        except Exception:
            supervisor_panel_state = {}

        # Global settings
        global_settings = {}
        for row in c.execute("SELECT key, value FROM global_settings"):
            try:
                global_settings[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                global_settings[row[0]] = row[1]

        # Schedules
        schedules = {}
        try:
            rows = c.execute("SELECT * FROM schedules").fetchall()
            if rows:
                cols = [d[0] for d in c.description]
                for row in rows:
                    d = dict(zip(cols, row))
                    d["group"] = d.pop("group_name", "")
                    d["enabled"] = bool(d.get("enabled", 1))
                    try:
                        d["labels"] = json.loads(d.get("labels", "[]"))
                    except (json.JSONDecodeError, TypeError):
                        d["labels"] = []
                    try:
                        d["action_vars"] = json.loads(
                            d.get("action_vars", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        d["action_vars"] = {}
                    schedules[d["id"]] = d
        except Exception:
            pass  # table may not exist yet on first load

        auto_dispatch_queues = {}
        try:
            rows = c.execute(
                "SELECT group_name, position, task_id, agent_group, "
                "max_concurrent, target_agent_id, engineer_owner_id, "
                "provider, enqueued_at "
                "FROM auto_dispatch_queue ORDER BY group_name, position"
            ).fetchall()
            auto_dispatch_queues = decode_auto_dispatch_queue_rows(rows)
        except Exception:
            auto_dispatch_queues = {}

        task_id_aliases = self.load_task_id_aliases()
        task_id_counters = self.load_task_id_counters()
        pipeline_task_counters = self.load_pipeline_task_counters()

        return {
            "agents": agents,
            "groups": groups,
            "group_slugs": group_slugs,
            "group_settings": group_settings,
            "board_lanes": board_lanes,
            "board_tasks": board_tasks,
            "schedules": schedules,
            "auto_dispatch_queues": auto_dispatch_queues,
            "panel_active": ui.get("panel_active", "")
                or ("board" if ui.get("board_panel_open", "False") == "True"
                    else ""),
            "board_panel_height": int(ui.get("board_panel_height", "0")),
            "active_group": str(ui.get("active_group", "") or ""),
            "selected_principal_id": str(
                ui.get("selected_principal_id", "") or ""
            ),
            "selected_agent_id": str(
                ui.get("selected_agent_id", "") or ""
            ),
            "standalone_panel_layout": (
                json.loads(ui.get("standalone_panel_layout", "{}"))
                if ui.get("standalone_panel_layout") else {}
            ),
            "detached_panels": detached_panels,
            "window_bounds": window_bounds,
            "workspace_sidebar_width": workspace_sidebar_width,
            "engineer_panel_split_fraction": engineer_panel_split_fraction,
            "context_panel_split_ratio": context_panel_split_ratio,
            "supervisor_panel_state": supervisor_panel_state,
            "events_dismissed_attention": (
                json.loads(ui.get("events_dismissed_attention", "{}"))
                if ui.get("events_dismissed_attention") else {}
            ),
            "board_filters_by_group": board_filters_by_group,
            "board_selected_lanes_by_group": board_selected_lanes_by_group,
            "board_hidden_wide_lanes_by_group": board_hidden_wide_lanes_by_group,
            "board_saved_views_by_group": board_saved_views_by_group,
            "board_lane_sorts_by_group": board_lane_sorts_by_group,
            "board_card_density_by_group": board_card_density_by_group,
            "global_settings": global_settings,
            "task_id_aliases": task_id_aliases,
            "task_id_counters": task_id_counters,
            "pipeline_task_counters": pipeline_task_counters,
        }

    def has_data(self) -> bool:
        """Check if the DB has any persisted state."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM agents").fetchone()
        groups = self._conn.execute(
            "SELECT COUNT(*) FROM groups").fetchone()
        return (row and row[0] > 0) or (groups and groups[0] > 0)

    def _kinds_backup_path(self) -> Path:
        return self.db_path.with_name(_KINDS_SCHEMA_BACKUP_NAME)

    def _read_meta_value(self, key: str, *, db_path: Optional[Path] = None) -> Optional[str]:
        path = Path(db_path or self.db_path)
        if not path.exists():
            return None
        conn = None
        try:
            conn = sqlite3.connect(str(path))
            table = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='meta'"
            ).fetchone()
            if not table:
                return None
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?",
                (key,),
            ).fetchone()
            return None if not row else str(row[0])
        except sqlite3.OperationalError:
            return None
        finally:
            if conn is not None:
                conn.close()

    def _current_kinds_migration_version(self) -> int:
        raw = self._read_meta_value(_KINDS_SCHEMA_MIGRATION_VERSION_KEY)
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _current_agent_activity_ts_migration_version(self) -> int:
        raw = self._read_meta_value(_AGENT_ACTIVITY_TS_MIGRATION_VERSION_KEY)
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _ensure_agent_activity_timestamp_columns(self) -> None:
        for col in (
            "last_progress_at",
            "last_heartbeat_at",
            "last_activity_at",
        ):
            try:
                self._conn.execute(f"SELECT {col} FROM agents LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE agents ADD COLUMN {col} "
                    "REAL NOT NULL DEFAULT 0"
                )
                self._conn.commit()

    def _ensure_agent_queue_empty_emitted_column(self) -> None:
        try:
            self._conn.execute("SELECT queue_empty_emitted FROM agents LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN queue_empty_emitted "
                "INTEGER NOT NULL DEFAULT 1"
            )
            self._conn.commit()

    def _migrate_agent_activity_timestamps_if_needed(self) -> None:
        """Backfill split progress/heartbeat clocks from legacy activity."""
        self._ensure_agent_activity_timestamp_columns()
        if (
            self._current_agent_activity_ts_migration_version()
            >= _AGENT_ACTIVITY_TS_MIGRATION_VERSION
        ):
            return

        try:
            self._conn.execute("BEGIN")
            # Existing rows only had one mixed activity clock. Preserve that
            # value by seeding both new clocks once, then keep the compatibility
            # alias equal to max(progress, heartbeat).
            self._conn.execute(
                "UPDATE agents "
                "SET last_progress_at = CASE "
                "WHEN COALESCE(last_progress_at, 0) = 0 "
                "THEN COALESCE(last_activity_at, 0) ELSE last_progress_at END, "
                "last_heartbeat_at = CASE "
                "WHEN COALESCE(last_heartbeat_at, 0) = 0 "
                "THEN COALESCE(last_activity_at, 0) ELSE last_heartbeat_at END"
            )
            self._conn.execute(
                "UPDATE agents SET last_activity_at = "
                "MAX(COALESCE(last_activity_at, 0), "
                "COALESCE(last_progress_at, 0), "
                "COALESCE(last_heartbeat_at, 0))"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _AGENT_ACTIVITY_TS_MIGRATION_VERSION_KEY,
                    str(_AGENT_ACTIVITY_TS_MIGRATION_VERSION),
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        log.info(
            "migration: agent activity timestamps split applied (version=%d)",
            _AGENT_ACTIVITY_TS_MIGRATION_VERSION,
        )

    def _maybe_backup_pre_kinds_db(self):
        if not self.db_path.exists():
            return
        if self._kinds_backup_path().exists():
            return
        if self._current_kinds_migration_version() >= _KINDS_SCHEMA_MIGRATION_VERSION:
            return

        backup_path = self._kinds_backup_path()
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        source = target = None
        try:
            source = sqlite3.connect(str(self.db_path))
            target = sqlite3.connect(str(backup_path))
            source.backup(target)
        except Exception:
            if backup_path.exists():
                backup_path.unlink()
            raise
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

        log.info("migration: created pre-kinds backup at %s", backup_path)

    def _migrate_kinds_schema_if_needed(self):
        if self._current_kinds_migration_version() >= _KINDS_SCHEMA_MIGRATION_VERSION:
            return

        for col, col_type, default in [
            ("kind", "TEXT", "''"),
            ("role", "TEXT", "''"),
            ("owner_engineer_id", "TEXT", "''"),
            ("hired_by_architect_id", "TEXT", "''"),
            ("dismissed_at", "INTEGER", "0"),
            ("persistent", "INTEGER", "0"),
        ]:
            try:
                self._conn.execute(f"SELECT {col} FROM agents LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE agents ADD COLUMN {col} "
                    f"{col_type} NOT NULL DEFAULT {default}"
                )
                self._conn.commit()

        for col, col_type, default in [
            ("assigned_engineer_id", "TEXT", "''"),
            ("created_by_architect_id", "TEXT", "''"),
            ("created_by_engineer_id", "TEXT", "''"),
            ("suggested_action", "TEXT", "''"),
        ]:
            try:
                self._conn.execute(f"SELECT {col} FROM board_tasks LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE board_tasks ADD COLUMN {col} "
                    f"{col_type} NOT NULL DEFAULT {default}"
                )
                self._conn.commit()

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                architect_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'proposed',
                supersedes TEXT DEFAULT NULL,
                linked_task_ids TEXT NOT NULL DEFAULT '[]',
                linked_engineer_ids TEXT NOT NULL DEFAULT '[]',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decisions_architect "
            "ON decisions(architect_id)"
        )

        migrated_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (
                _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                str(_KINDS_SCHEMA_MIGRATION_VERSION),
            ),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY, migrated_at),
        )
        self._conn.commit()
        log.info(
            "migration: kinds schema applied (version=1, backup=%s)",
            self._kinds_backup_path(),
        )

    def _find_engineer_candidate_ids(self) -> list[str]:
        template_sql = self._optional_column_sql("agents", "template")
        created_by_sql = self._optional_column_sql(
            "agents", "created_by_engineer_id"
        )
        rows = self._conn.execute(
            f"SELECT id, name, {template_sql}, {created_by_sql} "
            "FROM agents WHERE cell_type='agent' AND group_name=?",
            (_KINDS_ENGINEER_GROUP,),
        ).fetchall()
        referenced_ids = {
            str(created_by_engineer_id or "").strip()
            for _agent_id, _name, _template, created_by_engineer_id in rows
            if str(created_by_engineer_id or "").strip()
        }
        candidates = set()
        for agent_id, name, template, created_by_engineer_id in rows:
            agent_id = str(agent_id or "").strip()
            if not agent_id:
                continue
            if re.search(r"engineer", str(name or ""), re.IGNORECASE):
                candidates.add(agent_id)
                continue
            if str(template or "").strip().lower() == "engineer":
                candidates.add(agent_id)
                continue
            if not str(created_by_engineer_id or "").strip() and agent_id in referenced_ids:
                candidates.add(agent_id)
        return sorted(candidates)

    def _configured_engineer_candidate_id(self) -> str:
        row = self._conn.execute(
            "SELECT engineer_agent_id FROM group_settings WHERE group_name=?",
            (_KINDS_ENGINEER_GROUP,),
        ).fetchone()
        configured_id = str(row[0] or "").strip() if row else ""
        if not configured_id:
            return ""
        exists = self._conn.execute(
            "SELECT 1 FROM agents "
            "WHERE id=? AND cell_type='agent' AND group_name=? "
            "LIMIT 1",
            (configured_id, _KINDS_ENGINEER_GROUP),
        ).fetchone()
        return configured_id if exists else ""

    def _resolve_engineer_backfill_target(self) -> tuple[Optional[str], bool]:
        configured_id = self._configured_engineer_candidate_id()
        if configured_id:
            return configured_id, True

        candidate_ids = self._find_engineer_candidate_ids()
        if not candidate_ids:
            log.info("migration: no Engineer found, skipping engineer backfill")
            return None, True
        if len(candidate_ids) == 1:
            return candidate_ids[0], True

        override = str(os.getenv(_KINDS_ENGINEER_OVERRIDE_ENV, "") or "").strip()
        if override and override in candidate_ids:
            return override, True

        if override:
            log.error(
                "migration: multiple Engineer candidates found %s; %s=%r did not match",
                candidate_ids,
                _KINDS_ENGINEER_OVERRIDE_ENV,
                override,
            )
        else:
            log.error(
                "migration: multiple Engineer candidates found %s; set %s to continue",
                candidate_ids,
                _KINDS_ENGINEER_OVERRIDE_ENV,
            )
        return None, False

    def _resolve_backfilled_engineer_identity(self, engineer_id: str) -> tuple[str, str]:
        rows = self._conn.execute(
            "SELECT id, name, slug FROM agents"
        ).fetchall()
        existing_names = {
            str(name or "").strip()
            for agent_id, name, _slug in rows
            if str(agent_id or "") != str(engineer_id or "")
            and str(name or "").strip()
        }
        target_name = _unique_value(_KINDS_ENGINEER_NAME, existing_names)
        existing_slugs = {
            str(slug or "").strip()
            for agent_id, _name, slug in rows
            if str(agent_id or "") != str(engineer_id or "")
            and str(slug or "").strip()
        }
        target_slug = _unique_value(_slugify(target_name), existing_slugs)
        return target_name, target_slug

    def _column_exists(self, table: str, column: str) -> bool:
        try:
            self._conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
            return True
        except sqlite3.OperationalError:
            return False

    def _optional_column_sql(self, table: str, column: str) -> str:
        if self._column_exists(table, column):
            return _quote_ident(column)
        return f"'' AS {_quote_ident(column)}"

    def _refuse_unmigrated_legacy_rows_if_needed(self) -> None:
        version = self._current_kinds_migration_version()
        if version >= _KINDS_SCHEMA_MIGRATION_VERSION:
            return
        if self._count_unmigrated_legacy_rows() <= 0:
            return
        current_major = int(str(__version__ or "2.0.0").split(".", 1)[0] or 2)
        prior_major = max(1, current_major - 1)
        message = (
            "ERROR: this version requires a prior kinds-refactor migration.\n"
            f"Install Torque {prior_major}.x first, boot once so the kinds-refactor migration runs, then upgrade to Torque {__version__}.\n"
            "Current DB has unmigrated rows with legacy columns populated."
        )
        print(message, file=sys.stderr)
        log.error(message)
        raise SystemExit(1)

    def _count_unmigrated_legacy_rows(self) -> int:
        count = 0
        if self._column_exists("agents", "template"):
            if not self._column_exists("agents", "kind"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents WHERE TRIM(COALESCE(template, '')) != ''"
                ).fetchone()
            elif not self._column_exists("agents", "role"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(template, '')) != '' AND TRIM(COALESCE(kind, '')) = ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(template, '')) != '' "
                    "AND (TRIM(COALESCE(kind, '')) = '' OR TRIM(COALESCE(role, '')) = '')"
                ).fetchone()
            count += int(row[0] or 0)

        if self._column_exists("agents", "created_by_engineer_id"):
            if not self._column_exists("agents", "kind"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_engineer_id, '')) != ''"
                ).fetchone()
            elif not self._column_exists("agents", "owner_engineer_id"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_engineer_id, '')) != '' "
                    "AND TRIM(COALESCE(kind, '')) = ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_engineer_id, '')) != '' "
                    "AND (TRIM(COALESCE(kind, '')) = '' "
                    "OR TRIM(COALESCE(owner_engineer_id, '')) = '')"
                ).fetchone()
            count += int(row[0] or 0)

        if self._column_exists("board_tasks", "engineer_owner_id"):
            if not self._column_exists("board_tasks", "assigned_engineer_id"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM board_tasks "
                    "WHERE TRIM(COALESCE(engineer_owner_id, '')) != ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM board_tasks "
                    "WHERE TRIM(COALESCE(engineer_owner_id, '')) != '' "
                    "AND TRIM(COALESCE(assigned_engineer_id, '')) = ''"
                ).fetchone()
            count += int(row[0] or 0)

        return count

    def _legacy_cleanup_row_counts(self) -> tuple[int, int]:
        mirrored_rows: set[tuple[str, str]] = set()
        drift_rows: set[tuple[str, str]] = set()

        if self._column_exists("agents", "template") and self._column_exists("agents", "role"):
            for agent_id, template, role in self._conn.execute(
                "SELECT id, template, role FROM agents"
            ).fetchall():
                template = str(template or "").strip()
                role = str(role or "").strip()
                if not template:
                    continue
                key = ("agents", str(agent_id or ""))
                if template == role:
                    mirrored_rows.add(key)
                else:
                    drift_rows.add(key)

        if (
            self._column_exists("agents", "created_by_engineer_id")
            and self._column_exists("agents", "owner_engineer_id")
        ):
            for agent_id, legacy_owner, owner_engineer_id in self._conn.execute(
                "SELECT id, created_by_engineer_id, owner_engineer_id FROM agents"
            ).fetchall():
                legacy_owner = str(legacy_owner or "").strip()
                owner_engineer_id = str(owner_engineer_id or "").strip()
                if not legacy_owner:
                    continue
                key = ("agents", str(agent_id or ""))
                if legacy_owner == owner_engineer_id:
                    mirrored_rows.add(key)
                else:
                    drift_rows.add(key)

        if (
            self._column_exists("board_tasks", "engineer_owner_id")
            and self._column_exists("board_tasks", "assigned_engineer_id")
        ):
            for task_id, legacy_owner, assigned_engineer_id in self._conn.execute(
                "SELECT id, engineer_owner_id, assigned_engineer_id FROM board_tasks"
            ).fetchall():
                legacy_owner = str(legacy_owner or "").strip()
                assigned_engineer_id = str(assigned_engineer_id or "").strip()
                if not legacy_owner:
                    continue
                key = ("board_tasks", str(task_id or ""))
                if legacy_owner == assigned_engineer_id:
                    mirrored_rows.add(key)
                else:
                    drift_rows.add(key)

        return len(mirrored_rows), len(drift_rows)

    def _captured_table_supporting_sql(self, table: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name=? AND type IN ('index', 'trigger') "
            "AND sql IS NOT NULL ORDER BY type, name",
            (table,),
        ).fetchall()
        return [str(sql) for _type, _name, sql in rows if sql]

    def _rebuild_table_without_columns(
        self,
        table: str,
        *,
        new_table: str,
        drop_columns: set[str],
    ) -> None:
        columns = self._conn.execute(
            f"PRAGMA table_info({_quote_ident(table)})"
        ).fetchall()
        kept_columns = [row for row in columns if str(row[1]) not in drop_columns]
        if len(kept_columns) == len(columns):
            return

        defs: list[str] = []
        pk_columns: list[tuple[int, str]] = []
        for _cid, name, col_type, notnull, default, pk in kept_columns:
            parts = [_quote_ident(name)]
            if col_type:
                parts.append(str(col_type))
            if notnull:
                parts.append("NOT NULL")
            if default is not None:
                parts.append(f"DEFAULT {default}")
            defs.append(" ".join(parts))
            if pk:
                pk_columns.append((int(pk), str(name)))
        if pk_columns:
            ordered = ", ".join(
                _quote_ident(name) for _pk, name in sorted(pk_columns)
            )
            defs.append(f"PRIMARY KEY ({ordered})")

        create_sql = (
            f"CREATE TABLE {_quote_ident(new_table)} (\n    "
            + ",\n    ".join(defs)
            + "\n)"
        )
        supporting_sql = self._captured_table_supporting_sql(table)
        copy_columns = ", ".join(_quote_ident(row[1]) for row in kept_columns)

        self._conn.execute(create_sql)
        self._conn.execute(
            f"INSERT INTO {_quote_ident(new_table)} ({copy_columns}) "
            f"SELECT {copy_columns} FROM {_quote_ident(table)}"
        )
        self._conn.execute(f"DROP TABLE {_quote_ident(table)}")
        self._conn.execute(
            f"ALTER TABLE {_quote_ident(new_table)} RENAME TO {_quote_ident(table)}"
        )
        for sql in supporting_sql:
            self._conn.execute(sql)

    def _cleanup_kinds_legacy_columns_if_needed(self) -> None:
        version = self._current_kinds_migration_version()
        if version >= _KINDS_CLEANUP_MIGRATION_VERSION:
            return

        agents_has_legacy = any(
            self._column_exists("agents", col)
            for col in ("template", "created_by_engineer_id")
        )
        tasks_has_legacy = self._column_exists("board_tasks", "engineer_owner_id")
        if version < _KINDS_BACKFILL_MIGRATION_VERSION and (
            agents_has_legacy or tasks_has_legacy
        ):
            return
        if not agents_has_legacy and not tasks_has_legacy:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_CLEANUP_MIGRATION_VERSION),
                ),
            )
            self._conn.commit()
            return

        mirrored_rows, drift_rows = self._legacy_cleanup_row_counts()
        if mirrored_rows > 0 and drift_rows == 0:
            log.warning(
                "migration cleanup: dropping legacy columns with %d mirrored rows",
                mirrored_rows,
            )

        try:
            self._conn.execute("BEGIN")
            self._rebuild_table_without_columns(
                "agents",
                new_table="agents_new",
                drop_columns={"template", "created_by_engineer_id"},
            )
            self._rebuild_table_without_columns(
                "board_tasks",
                new_table="board_tasks_new",
                drop_columns={"engineer_owner_id"},
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_CLEANUP_MIGRATION_VERSION),
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


    def _backfill_empty_worker_kinds_if_needed(self) -> None:
        version = self._current_kinds_migration_version()
        if version < _KINDS_CLEANUP_MIGRATION_VERSION:
            return
        if version >= _KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION:
            return

        updated = 0
        try:
            self._conn.execute("BEGIN")
            cursor = self._conn.execute(
                "UPDATE agents SET kind='worker' "
                "WHERE cell_type='agent' "
                "AND TRIM(COALESCE(kind, '')) = '' "
                "AND ("
                "TRIM(COALESCE(owner_engineer_id, '')) != '' "
                "OR COALESCE(persistent, 0) = 0"
                ")"
            )
            updated = max(0, int(cursor.rowcount or 0))
            migrated_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION),
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY, migrated_at),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        log.info(
            "migration: empty worker kind backfill applied (updated=%d)",
            updated,
        )

    def _load_group_engineer_map(self) -> dict[str, str]:
        valid_agent_ids_by_group: dict[str, set[str]] = {}
        for agent_id, group_name in self._conn.execute(
            "SELECT id, group_name FROM agents WHERE cell_type='agent'"
        ).fetchall():
            agent_id = str(agent_id or "").strip()
            group_name = str(group_name or "").strip()
            if not agent_id or not group_name:
                continue
            valid_agent_ids_by_group.setdefault(group_name, set()).add(agent_id)

        rows = self._conn.execute(
            "SELECT group_name, engineer_agent_id FROM group_settings"
        ).fetchall()
        group_engineer_map: dict[str, str] = {}
        for group_name, engineer_agent_id in rows:
            group_name = str(group_name or "").strip()
            if not group_name:
                continue
            engineer_agent_id = str(engineer_agent_id or "").strip()
            if (
                engineer_agent_id
                and engineer_agent_id
                in valid_agent_ids_by_group.get(group_name, set())
            ):
                group_engineer_map[group_name] = engineer_agent_id
                continue
            if engineer_agent_id:
                log.warning(
                    "migration: ignoring stale engineer_agent_id=%r for group=%r",
                    engineer_agent_id,
                    group_name,
                )
            group_engineer_map[group_name] = ""
        return group_engineer_map

    def _backfill_task_assignments_from_group_settings(
        self,
        group_engineer_map: dict[str, str],
        *,
        only_unassigned: bool,
        reset_stage1_fields: bool,
    ) -> int:
        updated = 0
        if only_unassigned:
            rows = self._conn.execute(
                "SELECT id, group_name FROM board_tasks WHERE assigned_engineer_id=''"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, group_name FROM board_tasks"
            ).fetchall()

        for task_id, group_name in rows:
            task_id = str(task_id or "").strip()
            if not task_id:
                continue
            assigned_engineer_id = group_engineer_map.get(
                str(group_name or "").strip(),
                "",
            )
            if only_unassigned:
                if not assigned_engineer_id:
                    continue
                self._conn.execute(
                    "UPDATE board_tasks SET assigned_engineer_id=? WHERE id=?",
                    (assigned_engineer_id, task_id),
                )
                updated += 1
                continue

            if reset_stage1_fields:
                self._conn.execute(
                    "UPDATE board_tasks "
                    "SET assigned_engineer_id=?, "
                    "created_by_architect_id='', "
                    "suggested_action='' "
                    "WHERE id=?",
                    (assigned_engineer_id, task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE board_tasks SET assigned_engineer_id=? WHERE id=?",
                    (assigned_engineer_id, task_id),
                )
        return updated

    def _backfill_kinds_if_needed(self):
        version = self._current_kinds_migration_version()
        if version < _KINDS_SCHEMA_MIGRATION_VERSION:
            return
        if version >= _KINDS_BACKFILL_MIGRATION_VERSION:
            return

        engineer_id, proceed = self._resolve_engineer_backfill_target()
        if not proceed:
            return

        worker_count = 0
        task_count = 0
        try:
            self._conn.execute("BEGIN")

            engineer_name = engineer_slug = ""
            if engineer_id:
                engineer_name, engineer_slug = self._resolve_backfilled_engineer_identity(
                    engineer_id
                )

            template_sql = self._optional_column_sql("agents", "template")
            created_by_sql = self._optional_column_sql(
                "agents", "created_by_engineer_id"
            )
            rows = self._conn.execute(
                f"SELECT id, cell_type, {template_sql}, {created_by_sql} "
                "FROM agents"
            ).fetchall()
            for agent_id, cell_type, template, created_by_engineer_id in rows:
                agent_id = str(agent_id or "").strip()
                cell_type = str(cell_type or "").strip()
                if not agent_id:
                    continue
                if cell_type == "terminal":
                    self._conn.execute(
                        "UPDATE agents SET kind='terminal' WHERE id=?",
                        (agent_id,),
                    )
                    continue
                if cell_type != "agent":
                    continue
                if engineer_id and agent_id == engineer_id:
                    self._conn.execute(
                        "UPDATE agents "
                        "SET name=?, slug=?, kind='engineer', role='', "
                        "owner_engineer_id='', hired_by_architect_id='', persistent=1 "
                        "WHERE id=?",
                        (engineer_name, engineer_slug, agent_id),
                    )
                    continue
                self._conn.execute(
                    "UPDATE agents "
                    "SET kind='worker', role=?, owner_engineer_id=?, "
                    "hired_by_architect_id='', persistent=0 "
                    "WHERE id=?",
                    (
                        str(template or ""),
                        str(created_by_engineer_id or ""),
                        agent_id,
                    ),
                )
                worker_count += 1

            task_count = int(
                self._conn.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0]
            )
            group_engineer_map = self._load_group_engineer_map()
            configured_torque_engineer_id = group_engineer_map.get(_KINDS_ENGINEER_GROUP, "")
            self._backfill_task_assignments_from_group_settings(
                group_engineer_map,
                only_unassigned=False,
                reset_stage1_fields=True,
            )

            migrated_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_BACKFILL_MIGRATION_VERSION),
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY, migrated_at),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY, "1"),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        log.info(
            "migration: kinds backfill applied (engineer=%s, workers=%d, tasks=%d)",
            engineer_id,
            worker_count,
            task_count,
        )

    def _fixup_kinds_task_assignments_if_needed(self):
        version = self._current_kinds_migration_version()
        if version != _KINDS_BACKFILL_MIGRATION_VERSION:
            return

        fixup_applied = str(
            self._read_meta_value(_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY) or ""
        ).strip()
        if fixup_applied == "1":
            return

        updated = 0
        try:
            self._conn.execute("BEGIN")
            group_engineer_map = self._load_group_engineer_map()
            updated = self._backfill_task_assignments_from_group_settings(
                group_engineer_map,
                only_unassigned=True,
                reset_stage1_fields=False,
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY, "1"),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        log.info("migration: kinds task assignment fixup applied (updated=%d)", updated)

    # -- Migration ----------------------------------------------------------

    def migrate_from_json(self, json_path: Path):
        """Import state from a state.json file into SQLite."""
        if not json_path.exists():
            return False
        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cannot read %s for migration: %s", json_path, exc)
            return False

        log.info("Migrating state from %s to SQLite", json_path)
        self.save_all(data)

        # Rename to .bak
        bak = json_path.with_suffix(".json.bak")
        try:
            json_path.rename(bak)
            log.info("Renamed %s → %s", json_path.name, bak.name)
        except OSError as exc:
            log.warning("Could not rename %s: %s", json_path, exc)

        return True
