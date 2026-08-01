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
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from torque import __version__
from torque.behavior_overlay import (
    BehaviorOverlayScope,
    coerce_behavior_overlay_scope,
    behavior_overlay_scope_id,
    overlay_text_bytes,
    overlay_text_sha256,
)
from torque.config import ATTACHMENTS_DIR
from torque import profiling
from torque.db_board import (
    BoardPersistenceMixin,
    decode_auto_dispatch_queue_rows,
    decode_board_task_row,
    insert_board_task,
)
from torque.db_memory import MemoryPersistenceMixin
from torque.persistence.common import (
    GROUP_SETTINGS_BOOL_FIELDS as _GS_BOOL_FIELDS,
    GROUP_SETTINGS_JSON_FIELDS as _GS_JSON_FIELDS,
    group_settings_field_names as _group_settings_field_names,
    json_loads_default as _json_loads_default,
    json_payload as _json_payload,
    json_payload_text as _json_payload_text,
    normalize_actor_kind as _normalize_actor_kind,
    slugify as _slugify,
    snapshot_db_payload as _snapshot_db_payload,
    unique_value as _unique_value,
)
from torque.persistence.behavior_overlays import (
    BehaviorOverlayPersistenceMixin,
    _decode_behavior_overlay_activation_row,
    _decode_behavior_overlay_active_row,
    _decode_behavior_overlay_proposal_row,
    _decode_behavior_overlay_version_row,
)
from torque.persistence.agent_history import (
    AgentHistoryPersistenceMixin,
    _AGENT_DIRECT_MESSAGE_WHERE,
    _AGENT_PEER_CHAT_WHERE,
    _AGENT_PEER_MESSAGE_COLUMNS,
    _AGENT_PEER_MESSAGE_DELIVERY_STATES,
    _AGENT_PEER_MESSAGE_NON_USER_WHERE,
    _AGENT_PEER_MESSAGE_USER_WHERE,
    _ENGINEER_PEER_MESSAGE_WHERE,
    _agent_peer_message_insert_values,
    _decode_agent_peer_message_row,
    _decode_mcp_response_payload,
    _mcp_dispatch_response_group,
    _normalize_agent_peer_message_record,
    _peer_float,
    canonical_user_agent_thread_id,
)
from torque.persistence.architect_governance import (
    ArchitectGovernancePersistenceMixin,
    _decode_decision_row,
    _decode_pending_hire_row,
    _decision_int,
    _decision_json_dict,
    _decision_json_list,
)
from torque.persistence.areas import (
    AREA_AREA_RELATIONS,
    AREA_COLUMNS,
    AREA_LIFECYCLES,
    AREA_LINK_COLUMNS,
    AREA_LINK_TYPES,
    AREA_NOTE_COLUMNS,
    AREA_NOTE_TYPES,
    AreaPersistenceMixin,
    _decode_area_link_row,
    _decode_area_note_row,
    _decode_area_row,
    _normalize_area_lifecycle,
    _normalize_area_link_type,
    _normalize_area_note_type,
    _normalize_area_relation,
)
from torque.persistence.thinking import (
    THINKING_SCRATCHPAD_NOTE_COLUMNS,
    ThinkingPersistenceMixin,
    _decode_scratchpad_note_row,
)
from torque.persistence.idea_briefs import (
    IDEA_BRIEF_COLUMNS,
    IdeaBriefPersistenceMixin,
    _decode_idea_brief_row,
)
from torque.persistence.playbooks import PlaybookPersistenceMixin
from torque.persistence.migrations import (
    MigrationPersistenceMixin,
    _KINDS_BACKFILL_MIGRATION_VERSION,
    _KINDS_CLEANUP_MIGRATION_VERSION,
    _KINDS_ENGINEER_GROUP,
    _KINDS_ENGINEER_NAME,
    _KINDS_ENGINEER_OVERRIDE_ENV,
    _KINDS_SCHEMA_BACKUP_NAME,
    _KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY,
    _KINDS_SCHEMA_MIGRATION_VERSION,
    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
    _KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY,
    _KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION,
    _create_sqlite_backup,
    _quote_ident,
)
from torque.persistence.snapshots import SnapshotPersistenceMixin
from torque.persistence.digests import (
    DigestPersistenceMixin,
    _decode_digest_event_json,
    _digest_event_json,
)
from torque.persistence.telemetry import TelemetryPersistenceMixin
from torque.persistence.task_ids import TaskIdPersistenceMixin
from torque.persistence.ai import (
    AIPersistenceMixin,
    AI_INDEX_SOURCE_TYPES,
    AI_INDEX_STATE_ID,
    AI_SECRET_PROVIDERS,
    AI_SUMMARY_STATUSES,
    _ai_index_source_type,
    _ai_index_state_dict,
    _ai_job_dict,
    _ai_source_dict,
    _ai_source_state_for_hash,
    _ai_summary_dict,
    _ai_summary_status,
    _json_dumps_stable,
    _nonnegative_int,
    _normalize_ai_secret_provider,
)
from torque.persistence.reliability import (
    ReliabilityPersistenceMixin,
    is_sqlite_lock_error as _is_sqlite_lock_error,
    sqlite_retry_backoff as _sqlite_retry_backoff,
)
from torque.persistence.operator_notices import OperatorNoticePersistenceMixin
from torque.persistence.task_watches import TaskWatchPersistenceMixin
from torque.persistence.reminders import ReminderPersistenceMixin
from torque.persistence.initiatives import (
    INITIATIVE_COLUMNS,
    INITIATIVE_LINK_COLUMNS,
    INITIATIVE_LINK_TYPES,
    INITIATIVE_PLANNING_STATUSES,
    InitiativePersistenceMixin,
    _decode_initiative_link_row,
    _decode_initiative_row,
    _normalize_initiative_status,
)
from torque.db_schema import (
    AGENT_PEER_MESSAGE_COLUMNS,
    SCHEMA_VERSION,
    create_ai_embedding_vec_table,
    drop_ai_embedding_vec_table,
    finalize_database_migrations,
    initialize_database,
)
from torque.idea_briefs import (
    IDEA_BRIEF_BODY_FIELDS,
    IDEA_BRIEF_DEFAULT_STATUS,
    IDEA_BRIEF_TEXT_FIELDS,
    IDEA_BRIEF_PROPOSAL_SCOPE,
    idea_brief_contract_metadata,
    idea_brief_is_archived,
    normalize_idea_brief_status,
)
from torque.mcp_idempotency import (
    MCP_IDEMPOTENCY_FULL_TTL_SECONDS,
    MCP_IDEMPOTENCY_MAINTENANCE_INTERVAL_SECONDS,
    MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
    compacted_idempotency_response,
    collect_mcp_idempotency_storage_stats,
    json_response_bytes,
    maintain_mcp_idempotency_storage,
)
from torque.task_ids import (
    format_derived_task_id,
    format_area_id,
    format_idea_brief_id,
    format_initiative_id,
    format_root_task_id,
    format_scratchpad_note_id,
    is_canonical_task_id,
    normalize_group_prefix,
    parse_task_id,
)

log = logging.getLogger("torque")


























_AGENT_PERSISTED_COLS = [
    "id", "name", "slug", "group_name", "cell_type", "terminal_backend",
    "runner_backend",
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
    "agent_class_id", "agent_class_version", "agent_class_assigned_at",
    "agent_class_assigned_by", "effective_agent_class_id",
    "effective_agent_class_version", "effective_agent_class_snapshot",
    "effective_agent_class_applied_at",
]

_AGENT_INSERT_SQL = """
    INSERT OR REPLACE INTO agents
        ({columns})
    VALUES ({placeholders})
"""

# GroupSettings fields that store dicts/lists — persisted as JSON text.

# GroupSettings fields that are booleans — stored as INTEGER 0/1.

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
        d.get("terminal_backend", "pty"),
        d.get("runner_backend", "pty") or "pty",
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
        d.get("agent_class_id", ""),
        d.get("agent_class_version", ""),
        float(d.get("agent_class_assigned_at", 0) or 0),
        d.get("agent_class_assigned_by", ""),
        d.get("effective_agent_class_id", ""),
        d.get("effective_agent_class_version", ""),
        json.dumps(
            d.get("effective_agent_class_snapshot")
            if isinstance(d.get("effective_agent_class_snapshot"), dict)
            else {}
        ),
        float(d.get("effective_agent_class_applied_at", 0) or 0),
    )










class _QueuedAsyncDBWriter:
    """Global FIFO SQLite writer with coalesced agent-state transactions."""

    def __init__(self, owner: "TorqueDB", loop: asyncio.AbstractEventLoop):
        self._owner = owner
        self.loop = loop
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._worker_db: "TorqueDB" | None = None
        self._agent_batch_window = max(0.0, float(os.environ.get(
            "TORQUE_DB_AGENT_BATCH_WINDOW", "0.01"
        ) or 0.01))
        self._max_drain_batch = max(1, int(os.environ.get(
            "TORQUE_DB_WRITE_BATCH_SIZE", "512"
        ) or 512))
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
        self._queue.put_nowait((bucket, method_name, args, kwargs, None))
        self._ensure_drainer()

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
        self._queue.put_nowait((bucket, method_name, args, kwargs, future))
        self._ensure_drainer()
        return await future

    async def flush(self) -> None:
        """Wait until all currently queued/running writes complete."""
        await self._queue.join()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.flush()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        await asyncio.to_thread(self._close_worker_db)

    def _ensure_drainer(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = self.loop.create_task(
            self._drain(),
            name="torque-sqlite-writer",
        )

    @staticmethod
    def _is_coalescible_agent_save(item: tuple) -> bool:
        bucket, method_name, args, _kwargs, future = item
        if bucket != "agents" or method_name != "save_agent" or future is not None:
            return False
        if not args:
            return False
        cell = args[0]
        agent_id = (
            str(cell.get("id", "") or "")
            if isinstance(cell, dict)
            else str(getattr(cell, "id", "") or "")
        )
        return bool(agent_id)

    @staticmethod
    def _agent_id_from_item(item: tuple) -> str:
        cell = item[2][0]
        if isinstance(cell, dict):
            return str(cell.get("id", "") or "")
        return str(getattr(cell, "id", "") or "")

    async def _drain(self) -> None:
        try:
            while True:
                first = await self._queue.get()
                items = [first]
                if self._is_coalescible_agent_save(first):
                    if self._agent_batch_window:
                        await asyncio.sleep(self._agent_batch_window)
                    while len(items) < self._max_drain_batch:
                        try:
                            items.append(self._queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                await self._run_items(items)
        except asyncio.CancelledError:
            raise

    async def _run_items(self, items: list[tuple]) -> None:
        index = 0
        while index < len(items):
            item = items[index]
            if self._is_coalescible_agent_save(item):
                end = index + 1
                while (
                    end < len(items)
                    and self._is_coalescible_agent_save(items[end])
                ):
                    end += 1
                run = items[index:end]
                latest_by_agent: dict[str, tuple] = {}
                for queued in run:
                    latest_by_agent[self._agent_id_from_item(queued)] = queued
                try:
                    await asyncio.to_thread(
                        self._run_sync_agent_batch,
                        [queued[2][0] for queued in latest_by_agent.values()],
                    )
                    profiling.recorder().observe(
                        "sqlite_agent_batch_size", len(latest_by_agent)
                    )
                    profiling.recorder().incr(
                        "sqlite_agent_saves_coalesced",
                        len(run) - len(latest_by_agent),
                    )
                except Exception:
                    log.exception(
                        "Async SQLite agent batch failed (%d queued, %d rows)",
                        len(run),
                        len(latest_by_agent),
                    )
                finally:
                    for _queued in run:
                        self._queue.task_done()
                index = end
                continue

            bucket, method_name, args, kwargs, future = item
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
                self._queue.task_done()
            index += 1

    def _run_sync_write(
        self,
        bucket: str,
        method_name: str,
        args: tuple,
        kwargs: dict,
    ):
        db = self._worker_db_for()
        method = getattr(db, method_name)
        return method(*args, **kwargs)

    def _run_sync_agent_batch(self, cells: list) -> None:
        self._worker_db_for().save_agents(cells)

    def _worker_db_for(self) -> "TorqueDB":
        if self._worker_db is not None:
            return self._worker_db
        # Preserve the owner's concrete facade identity across importlib reloads
        # in long-running test/dev processes.  Looking up the module-global
        # TorqueDB here can otherwise instantiate a newer class than the one
        # whose methods the caller patched or configured.
        db = type(self._owner)(self._owner.db_path)
        db._conn = sqlite3.connect(
            str(self._owner.db_path),
            check_same_thread=False,
        )
        db._conn.execute("PRAGMA foreign_keys=ON")
        db._conn.execute("PRAGMA busy_timeout=5000")
        self._worker_db = db
        return db

    def _close_worker_db(self) -> None:
        db = self._worker_db
        self._worker_db = None
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()



class TorqueDB(
        MigrationPersistenceMixin,
        SnapshotPersistenceMixin,
        DigestPersistenceMixin,
        TelemetryPersistenceMixin,
        TaskIdPersistenceMixin,
        AIPersistenceMixin,
        ReliabilityPersistenceMixin,
        OperatorNoticePersistenceMixin,
        TaskWatchPersistenceMixin,
        ReminderPersistenceMixin,
        PlaybookPersistenceMixin,
        AgentHistoryPersistenceMixin,
        BehaviorOverlayPersistenceMixin,
        ArchitectGovernancePersistenceMixin,
        IdeaBriefPersistenceMixin,
        ThinkingPersistenceMixin,
        AreaPersistenceMixin,
        InitiativePersistenceMixin,
        BoardPersistenceMixin,
        MemoryPersistenceMixin,
):
    """Facade over Torque's SQLite persistence helpers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._async_writer: Optional["_QueuedAsyncDBWriter"] = None
        self.async_writes_enabled: bool = False
        self._last_mcp_idempotency_maintenance_at: float = 0.0

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

    def _post_init_migration_runners(self) -> dict[int, Callable[[], None]]:
        return {
            8: lambda: self._run_kinds_legacy_stages(
                manage_transaction=False
            ),
            9: lambda: (
                self._migrate_agent_digest_settings_from_legacy_engineer_settings(
                    manage_transaction=False
                )
            ),
            10: lambda: self.migrate_task_ids_if_needed(
                manage_transaction=False
            ),
            28: lambda: self._migrate_legacy_memory_entries_to_ttl_if_needed(
                manage_transaction=False
            ),
        }

    def init(self):
        """Open connection, enable WAL, create tables if needed."""
        self._maybe_backup_pending_schema_migration()
        self._maybe_backup_pre_kinds_db()
        self._conn = sqlite3.connect(str(self.db_path))
        initialize_database(self._conn, self.backfill_agent_history)
        self._refuse_unmigrated_legacy_rows_if_needed()
        finalize_database_migrations(
            self._conn,
            self.backfill_agent_history,
            post_init_runners=self._post_init_migration_runners(),
        )
        # The ledgered legacy-memory runner above must precede this sweep so
        # NULL legacy rows are available for its 60-day stamp.
        self.purge_expired_memory_entries()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- MCP reliability ----------------------------------------------------


    def _legacy_engineer_rows_exist(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM engineer_settings LIMIT 1"
        ).fetchone()
        return bool(row)

    def _migrate_agent_digest_settings_from_legacy_engineer_settings(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
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
                commit=manage_transaction,
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

    def save_agent_class_audit(self, record: dict) -> dict:
        """Persist one trusted Agent Class assignment/effective snapshot event."""
        source = dict(record or {})
        event_id = str(source.get("id", "") or uuid.uuid4().hex).strip()
        snapshot = source.get("snapshot_json", {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        row = {
            "id": event_id,
            "agent_id": str(source.get("agent_id", "") or ""),
            "agent_name": str(source.get("agent_name", "") or ""),
            "event": str(source.get("event", "") or ""),
            "actor_kind": str(source.get("actor_kind", "user") or "user"),
            "actor_id": str(source.get("actor_id", "") or ""),
            "actor_label": str(source.get("actor_label", "") or ""),
            "previous_class_id": str(source.get("previous_class_id", "") or ""),
            "previous_class_version": str(source.get("previous_class_version", "") or ""),
            "assigned_class_id": str(source.get("assigned_class_id", "") or ""),
            "assigned_class_version": str(source.get("assigned_class_version", "") or ""),
            "effective_class_id": str(source.get("effective_class_id", "") or ""),
            "effective_class_version": str(source.get("effective_class_version", "") or ""),
            "snapshot_hash": str(source.get("snapshot_hash", "") or snapshot.get("snapshot_hash", "") or ""),
            "snapshot_json": json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
            "message": str(source.get("message", "") or ""),
            "created_at": float(source.get("created_at", time.time()) or 0),
        }
        self._conn.execute(
            """
            INSERT OR REPLACE INTO agent_class_audit
            (id, agent_id, agent_name, event, actor_kind, actor_id, actor_label,
             previous_class_id, previous_class_version,
             assigned_class_id, assigned_class_version,
             effective_class_id, effective_class_version,
             snapshot_hash, snapshot_json, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(row[key] for key in [
                "id", "agent_id", "agent_name", "event", "actor_kind",
                "actor_id", "actor_label", "previous_class_id",
                "previous_class_version", "assigned_class_id",
                "assigned_class_version", "effective_class_id",
                "effective_class_version", "snapshot_hash", "snapshot_json",
                "message", "created_at",
            ]),
        )
        self._conn.commit()
        row["snapshot_json"] = snapshot
        return row

    def list_agent_class_audit(self, *, agent_id: str = "", limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), 500))
        params = []
        where = ""
        if str(agent_id or "").strip():
            where = "WHERE agent_id=?"
            params.append(str(agent_id or "").strip())
        rows = self._conn.execute(
            "SELECT id, agent_id, agent_name, event, actor_kind, actor_id, actor_label, "
            "previous_class_id, previous_class_version, assigned_class_id, "
            "assigned_class_version, effective_class_id, effective_class_version, "
            "snapshot_hash, snapshot_json, message, created_at "
            "FROM agent_class_audit "
            f"{where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        keys = [
            "id", "agent_id", "agent_name", "event", "actor_kind",
            "actor_id", "actor_label", "previous_class_id",
            "previous_class_version", "assigned_class_id",
            "assigned_class_version", "effective_class_id",
            "effective_class_version", "snapshot_hash", "snapshot_json",
            "message", "created_at",
        ]
        out = []
        for row in rows:
            item = dict(zip(keys, row))
            item["snapshot_json"] = _json_loads_default(item.get("snapshot_json"), {})
            item["created_at"] = float(item.get("created_at", 0) or 0)
            out.append(item)
        return out

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

    async def save_global_settings_durable(self, gs):
        """Persist global settings and propagate write failures to the caller."""
        return await self._enqueue_async_write(
            "global_settings",
            "save_global_settings",
            _snapshot_db_payload(gs),
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

    def save_agent_message_loop(self, loop):
        """Upsert one user→agent direct-message loop."""
        d = asdict(loop) if not isinstance(loop, dict) else dict(loop)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO agent_message_loops
                (id, agent_id, group_name, interval_seconds, message, status,
                 created_by, stopped_by, stop_reason, created_at, updated_at,
                 next_run_at, last_run_at, run_count, last_message_id,
                 deferred_at, deferred_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(d.get("id", "") or ""),
                str(d.get("agent_id", "") or ""),
                str(d.get("group_name", "") or ""),
                int(d.get("interval_seconds", 0) or 0),
                str(d.get("message", "") or ""),
                str(d.get("status", "active") or "active"),
                str(d.get("created_by", "user") or "user"),
                str(d.get("stopped_by", "") or ""),
                str(d.get("stop_reason", "") or ""),
                float(d.get("created_at", 0) or 0),
                float(d.get("updated_at", 0) or 0),
                float(d.get("next_run_at", 0) or 0),
                float(d.get("last_run_at", 0) or 0),
                int(d.get("run_count", 0) or 0),
                str(d.get("last_message_id", "") or ""),
                float(d.get("deferred_at", 0) or 0),
                str(d.get("deferred_reason", "") or ""),
            ),
        )
        self._conn.commit()

    def load_agent_message_loops(self) -> dict[str, dict]:
        """Load all persisted user→agent direct-message loops."""
        rows = self._conn.execute(
            "SELECT id, agent_id, group_name, interval_seconds, message, "
            "status, created_by, stopped_by, stop_reason, created_at, "
            "updated_at, next_run_at, last_run_at, run_count, last_message_id, "
            "deferred_at, deferred_reason FROM agent_message_loops"
        ).fetchall()
        loops: dict[str, dict] = {}
        for row in rows:
            item = {
                "id": str(row[0] or ""),
                "agent_id": str(row[1] or ""),
                "group_name": str(row[2] or ""),
                "interval_seconds": int(row[3] or 0),
                "message": str(row[4] or ""),
                "status": str(row[5] or "active"),
                "created_by": str(row[6] or "user"),
                "stopped_by": str(row[7] or ""),
                "stop_reason": str(row[8] or ""),
                "created_at": float(row[9] or 0),
                "updated_at": float(row[10] or 0),
                "next_run_at": float(row[11] or 0),
                "last_run_at": float(row[12] or 0),
                "run_count": int(row[13] or 0),
                "last_message_id": str(row[14] or ""),
                "deferred_at": float(row[15] or 0),
                "deferred_reason": str(row[16] or ""),
            }
            if item["id"]:
                loops[item["id"]] = item
        return loops

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
