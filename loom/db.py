"""SQLite persistence layer for Loom state.

Used by:
- The daemon (write path): save persistent fields on mutation
- The CLI (read path): direct SQLite reads for status/task queries

Uses synchronous sqlite3 — the daemon is single-threaded asyncio and
current save() already does sync file I/O.  Single-row upserts are faster
than json.dumps() + write_text().
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loom import __version__
from loom.config import ATTACHMENTS_DIR
from loom.db_board import (
    BoardPersistenceMixin,
    decode_auto_dispatch_queue_rows,
    decode_board_task_row,
    insert_board_task,
)
from loom.db_memory import MemoryPersistenceMixin
from loom.db_schema import initialize_database
from loom.task_ids import (
    format_derived_task_id,
    format_root_task_id,
    is_canonical_task_id,
    normalize_group_prefix,
    parse_task_id,
)

log = logging.getLogger("loom")

_KINDS_SCHEMA_MIGRATION_VERSION = 1
_KINDS_BACKFILL_MIGRATION_VERSION = 2
_KINDS_CLEANUP_MIGRATION_VERSION = 3
_KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION = 4
_KINDS_SCHEMA_MIGRATION_VERSION_KEY = "schema_kinds_migration_version"
_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY = "schema_kinds_migration_migrated_at"
_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY = "schema_kinds_task_assignment_fixup_applied"
_KINDS_SCHEMA_BACKUP_NAME = "loom.db.pre-kinds.bak"
_KINDS_WEAVER_OVERRIDE_ENV = "LOOM_MIGRATE_WEAVER_ID"
_KINDS_WEAVER_GROUP = "loom"
_KINDS_WEAVER_NAME = "Weaver"

_AGENT_PERSISTED_COLS = [
    "id", "name", "slug", "group_name", "cell_type", "terminal_backend",
    "session_id", "profile",
    "command", "directory", "tab_color", "icon", "window_id",
    "parent_id", "status", "worktree_path", "worktree_branch",
    "worktree_repo_root", "worktree_base_dir", "worktree_base_branch",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "agent_type",
    "agent_session_id", "session_resume", "idle_timeout",
    "tasks_dispatched",
    "kind", "role", "owner_engineer_id", "hired_by_architect_id",
    "dismissed_at", "persistent",
]

_AGENT_INSERT_SQL = """
    INSERT OR REPLACE INTO agents
        ({columns})
    VALUES ({placeholders})
"""

# GroupSettings fields that store dicts — persisted as JSON text.
_GS_JSON_FIELDS = {"env_vars", "agent_env_vars", "terminal_env_vars",
                    "board_default_labels", "worktree_symlinks"}

# GroupSettings fields that are booleans — stored as INTEGER 0/1.
_GS_BOOL_FIELDS = {
    "collapsed_default", "filter_by_window", "git_worktree",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "worktree_merge_preserve_diff",
    "agent_session_resume", "agent_always_custom_dialog",
    "dispatch_auto_terminals",
    "notifications", "notify_on_finish", "notify_on_error",
    "notify_on_attention", "terminal_always_custom_dialog",
    "terminal_close_on_disconnect",
}

def _serialize_agent_cell(cell):
    d = asdict(cell) if not isinstance(cell, dict) else dict(cell)
    group_name = d.pop("group", d.pop("group_name", ""))
    role = d.get("role", "") or d.get("template", "")
    owner_engineer_id = (
        d.get("owner_engineer_id", "")
        or d.get("created_by_weaver_id", "")
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
        d.get("worktree_base_dir", ".loom/worktrees"),
        d.get("worktree_base_branch", ""),
        int(d.get("worktree_auto_checkpoint", False)),
        int(d.get("checkpoint_on_progress", False)),
        int(d.get("worktree_merge_squash", True)),
        d.get("agent_type", ""),
        d.get("agent_session_id", ""),
        int(d.get("session_resume", True)),
        d.get("idle_timeout", 0),
        d.get("tasks_dispatched", 0),
        d.get("kind", ""),
        role,
        owner_engineer_id,
        d.get("hired_by_architect_id", ""),
        int(d.get("dismissed_at", 0) or 0),
        int(d.get("persistent", 0) or 0),
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
    """Encode a digest event without WeaverEventBuffer private metadata."""
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




class LoomDB(BoardPersistenceMixin, MemoryPersistenceMixin):
    """Facade over Loom's SQLite persistence helpers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

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
        self._ensure_board_task_engineer_provenance_column()
        self._refuse_unmigrated_legacy_rows_if_needed()
        self._migrate_kinds_schema_if_needed()
        self._ensure_agent_dismissed_at_column()
        self._backfill_kinds_if_needed()
        self._fixup_kinds_task_assignments_if_needed()
        self._cleanup_kinds_legacy_columns_if_needed()
        self._backfill_empty_worker_kinds_if_needed()
        self._migrate_agent_digest_settings_from_legacy_weaver_settings()
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

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _legacy_weaver_rows_exist(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM weaver_settings LIMIT 1"
        ).fetchone()
        return bool(row)

    def _migrate_agent_digest_settings_from_legacy_weaver_settings(self):
        """Backfill per-agent digest settings from legacy per-group rows."""
        if not self._legacy_weaver_rows_exist():
            return
        try:
            legacy_rows = self.load_all_weaver_settings()
        except sqlite3.OperationalError:
            return
        if not legacy_rows:
            return
        for group_name, settings in legacy_rows.items():
            row = self._conn.execute(
                "SELECT weaver_agent_id FROM group_settings "
                "WHERE group_name=?",
                (group_name,),
            ).fetchone()
            engineer_id = str((row[0] if row else "") or "").strip()
            if not engineer_id:
                log.warning(
                    "Skipping legacy weaver_settings migration for '%s': no designated engineer",
                    group_name,
                )
                continue
            agent_row = self._conn.execute(
                "SELECT kind FROM agents WHERE id=?",
                (engineer_id,),
            ).fetchone()
            if not agent_row:
                log.warning(
                    "Skipping legacy weaver_settings migration for '%s': agent '%s' missing",
                    group_name,
                    engineer_id,
                )
                continue
            if str(agent_row[0] or "").strip() != "engineer":
                log.warning(
                    "Skipping legacy weaver_settings migration for '%s': agent '%s' is not an engineer",
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
        self._insert_agent_row(self._conn, cell)
        self._conn.commit()

    def save_board_task(self, task):
        """Upsert a board task."""
        self._insert_board_task_row(self._conn, task)
        self._conn.commit()

    def delete_agent(self, agent_id: str):
        self._conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        self._conn.execute(
            "DELETE FROM group_members WHERE agent_id=?", (agent_id,))
        self._conn.commit()

    def save_group(self, name: str, position: int):
        self._conn.execute(
            "INSERT OR REPLACE INTO groups (name, position) VALUES (?,?)",
            (name, position))
        self._conn.commit()

    def delete_group(self, name: str):
        self._conn.execute("DELETE FROM groups WHERE name=?", (name,))
        self._conn.execute(
            "DELETE FROM group_members WHERE group_name=?", (name,))
        self._conn.execute(
            "DELETE FROM group_settings WHERE group_name=?", (name,))
        self._conn.execute(
            "DELETE FROM weaver_task_log WHERE group_name=?", (name,))
        self._conn.commit()

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

    def delete_group_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM group_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

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

    def delete_schedule(self, sid: str):
        self._conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
        self._conn.commit()

    def save_ui_state(self, key: str, value):
        self._conn.execute(
            "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?,?)",
            (key, str(value)))
        self._conn.commit()

    def save_global_settings(self, gs):
        """Persist global settings as key-value pairs."""
        d = asdict(gs)
        xterm_scrollback = int(d.get("xterm_scrollback", 2000) or 2000)
        self._conn.execute("DELETE FROM global_settings")
        for key, value in d.items():
            self._conn.execute(
                "INSERT INTO global_settings "
                "(key, value, xterm_scrollback) VALUES (?,?,?)",
                (key, json.dumps(value), xterm_scrollback))
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
                "max_concurrent, target_agent_id, weaver_owner_id, enqueued_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    group_name,
                    pos,
                    item.get("task_id", ""),
                    item.get("agent_group", ""),
                    int(item.get("max_concurrent", 1) or 1),
                    item.get("target_agent_id", ""),
                    item.get("weaver_owner_id", ""),
                    item.get("enqueued_at", ""),
                ),
            )
        self._conn.commit()

    def delete_auto_dispatch_queue(self, group_name: str):
        self._conn.execute(
            "DELETE FROM auto_dispatch_queue WHERE group_name=?",
            (group_name,),
        )
        self._conn.commit()

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
            ("weaver_task_log", "task_id"),
            ("memory_entries", "task_id"),
        ):
            _rewrite_single_ref(table, column)

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

    def save_panel_event(self, evt: dict) -> int:
        """Insert a panel event and return the assigned row ID."""
        self._conn.execute(
            "INSERT INTO panel_events "
            "(id, timestamp, kind, cell_id, agent_name, group_name, "
            "message, task_id) VALUES (?,?,?,?,?,?,?,?)",
            (evt["id"], evt["timestamp"], evt["kind"],
             evt.get("cell_id", ""), evt.get("agent_name", ""),
             evt.get("group", ""), evt.get("message", ""),
             evt.get("task_id", "")))
        self._conn.commit()
        return evt["id"]

    def update_panel_event(self, evt: dict):
        """Update an existing panel event row."""
        self._conn.execute(
            "UPDATE panel_events SET timestamp=?, kind=?, cell_id=?, "
            "agent_name=?, group_name=?, message=?, task_id=? WHERE id=?",
            (evt["timestamp"], evt["kind"], evt.get("cell_id", ""),
             evt.get("agent_name", ""), evt.get("group", ""),
             evt.get("message", ""), evt.get("task_id", ""),
             evt["id"]))
        self._conn.commit()

    def trim_panel_events(self, max_size: int):
        """Delete oldest events beyond *max_size*."""
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

        with self._conn:
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

    # -- Weaver settings & journal -------------------------------------------

    def save_weaver_settings(self, group_name: str, settings: dict):
        """Upsert weaver settings for a group."""
        enabled_events = json.dumps(
            settings.get("enabled_events",
                         ["agent_started", "task_dispatched",
                          "task_derived", "task_health_alert"]))
        self._conn.execute("""
            INSERT OR REPLACE INTO weaver_settings
                (group_name, push_interval, max_interval, heartbeat_interval,
                 default_worker_concurrency, autonomy_mode,
                 wave_size_preference, same_agent_follow_up_preference,
                 digest_verbosity, escalation_style,
                 paused,
                 custom_instructions, restrict_to_created_agents,
                 pending_question, pending_note,
                 pending_note_kind, enabled_events,
                 weaver_provider, weaver_boot_command,
                 weaver_model, weaver_reasoning_effort,
                 weaver_directory, weaver_profile,
                 weaver_shell, weaver_tab_color)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            enabled_events,
            settings.get("weaver_provider", ""),
            settings.get("weaver_boot_command", ""),
            settings.get("weaver_model", ""),
            settings.get("weaver_reasoning_effort", ""),
            settings.get("weaver_directory", ""),
            settings.get("weaver_profile", ""),
            settings.get("weaver_shell", ""),
            settings.get("weaver_tab_color", ""),
        ))
        self._conn.commit()

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
                 architect_digest, wake_on_digest)
            VALUES (?,?,?,?,?,?,?,?,?)
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
            ),
        )
        self._conn.commit()

    def load_weaver_settings(self, group_name: str) -> dict | None:
        """Load weaver settings for a group. Returns None if not set."""
        row = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, "
            "default_worker_concurrency, autonomy_mode, "
            "wave_size_preference, same_agent_follow_up_preference, "
            "digest_verbosity, escalation_style, paused, "
            "custom_instructions, restrict_to_created_agents, "
            "pending_question, pending_note, pending_note_kind, enabled_events, "
            "weaver_provider, weaver_boot_command, "
            "weaver_model, weaver_reasoning_effort, "
            "weaver_directory, weaver_profile, "
            "weaver_shell, weaver_tab_color "
            "FROM weaver_settings "
            "WHERE group_name=?", (group_name,)).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[16])
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
            "enabled_events": enabled,
            "weaver_provider": row[17] if len(row) > 17 else "",
            "weaver_boot_command": row[18] if len(row) > 18 else "",
            "weaver_model": row[19] if len(row) > 19 else "",
            "weaver_reasoning_effort": row[20] if len(row) > 20 else "",
            "weaver_directory": row[21] if len(row) > 21 else "",
            "weaver_profile": row[22] if len(row) > 22 else "",
            "weaver_shell": row[23] if len(row) > 23 else "",
            "weaver_tab_color": row[24] if len(row) > 24 else "",
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

    def delete_weaver_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM weaver_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def delete_agent_digest_settings(self, agent_id: str):
        self._conn.execute(
            "DELETE FROM agent_digest_settings WHERE agent_id=?",
            (agent_id,),
        )
        self._conn.commit()

    def load_all_weaver_settings(self) -> dict[str, dict]:
        """Load weaver settings for all groups. Returns {group: settings}."""
        rows = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, "
            "default_worker_concurrency, autonomy_mode, "
            "wave_size_preference, same_agent_follow_up_preference, "
            "digest_verbosity, escalation_style, paused, "
            "custom_instructions, restrict_to_created_agents, "
            "pending_question, pending_note, pending_note_kind, enabled_events, "
            "weaver_provider, weaver_boot_command, "
            "weaver_model, weaver_reasoning_effort, "
            "weaver_directory, weaver_profile, "
            "weaver_shell, weaver_tab_color "
            "FROM weaver_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[16])
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
                "enabled_events": enabled,
                "weaver_provider": row[17] if len(row) > 17 else "",
                "weaver_boot_command": row[18] if len(row) > 18 else "",
                "weaver_model": row[19] if len(row) > 19 else "",
                "weaver_reasoning_effort": row[20] if len(row) > 20 else "",
                "weaver_directory": row[21] if len(row) > 21 else "",
                "weaver_profile": row[22] if len(row) > 22 else "",
                "weaver_shell": row[23] if len(row) > 23 else "",
                "weaver_tab_color": row[24] if len(row) > 24 else "",
            }
        return result

    def load_all_agent_digest_settings(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT agent_id, paused, push_interval, max_interval, "
            "heartbeat_interval, digest_verbosity, enabled_events, "
            "architect_digest, wake_on_digest "
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
            }
        return result

    def save_journal_entry(self, group_name: str, timestamp: float,
                           entry_type: str, entry: str,
                           author_cell_id: str = "") -> int:
        """Insert a weaver journal entry. Returns the new row ID."""
        author_cell_id = str(author_cell_id or "").strip()
        c = self._conn.execute(
            "INSERT INTO weaver_journal "
            "(group_name, timestamp, entry_type, entry, author_cell_id) "
            "VALUES (?,?,?,?,?)",
            (group_name, timestamp, entry_type, entry, author_cell_id))
        self._conn.commit()
        return c.lastrowid

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
            "author_cell_id FROM weaver_journal WHERE "
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

    def save_weaver_task_log_entry(self, record: dict) -> int:
        """Insert a persisted Weaver dispatch/worklog row."""
        c = self._conn.execute(
            "INSERT INTO weaver_task_log "
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

    def load_weaver_task_log(self, group_name: str, limit: int = 100) -> list[dict]:
        """Load recent persisted Weaver dispatch rows for a group."""
        rows = self._conn.execute(
            "SELECT id, group_name, task_id, task_title, agent_id, agent_name, "
            "agent_slug, agent_owned, started_at FROM weaver_task_log "
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

    def trim_weaver_task_log(self, group_name: str, limit: int = 200):
        """Trim persisted Weaver worklog rows for a group to ``limit``."""
        self._conn.execute(
            "DELETE FROM weaver_task_log WHERE group_name=? AND id NOT IN ("
            "SELECT id FROM weaver_task_log WHERE group_name=? "
            "ORDER BY started_at DESC, id DESC LIMIT ?"
            ")",
            (group_name, group_name, limit),
        )
        self._conn.commit()

    def rename_weaver_task_log_group(self, old_name: str, new_name: str):
        """Move persisted Weaver worklog rows to a renamed group."""
        self._conn.execute(
            "UPDATE weaver_task_log SET group_name=? WHERE group_name=?",
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
            for gname, gs in state_dict.get("group_settings", {}).items():
                d = dict(gs) if isinstance(gs, dict) else asdict(gs)
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
                             weaver_owner_id, enqueued_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (
                        gname,
                        pos,
                        item.get("task_id", ""),
                        item.get("agent_group", ""),
                        int(item.get("max_concurrent", 1) or 1),
                        item.get("target_agent_id", ""),
                        item.get("weaver_owner_id", ""),
                        item.get("enqueued_at", ""),
                    ))

            # UI state
            c.execute("DELETE FROM ui_state")
            for key in (
                "panel_active",
                "board_panel_height",
                "standalone_panel_layout",
            ):
                val = state_dict.get(key)
                if val is not None:
                    if key == "standalone_panel_layout":
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
                "created_by_weaver_id",
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
            for row in rows:
                d = dict(zip(cols, row))
                gname = d.pop("group_name")
                # Decode JSON fields
                for k in _GS_JSON_FIELDS:
                    if k in d and isinstance(d[k], str):
                        try:
                            d[k] = json.loads(d[k])
                        except (json.JSONDecodeError, TypeError):
                            d[k] = [] if k in {"board_default_labels",
                                                "worktree_symlinks"} else {}
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
                    "weaver_owner_id",
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
                "max_concurrent, target_agent_id, weaver_owner_id, enqueued_at "
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
            "standalone_panel_layout": (
                json.loads(ui.get("standalone_panel_layout", "{}"))
                if ui.get("standalone_panel_layout") else {}
            ),
            "events_dismissed_attention": (
                json.loads(ui.get("events_dismissed_attention", "{}"))
                if ui.get("events_dismissed_attention") else {}
            ),
            "board_filters_by_group": board_filters_by_group,
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

    def _find_weaver_candidate_ids(self) -> list[str]:
        template_sql = self._optional_column_sql("agents", "template")
        created_by_sql = self._optional_column_sql(
            "agents", "created_by_weaver_id"
        )
        rows = self._conn.execute(
            f"SELECT id, name, {template_sql}, {created_by_sql} "
            "FROM agents WHERE cell_type='agent' AND group_name=?",
            (_KINDS_WEAVER_GROUP,),
        ).fetchall()
        referenced_ids = {
            str(created_by_weaver_id or "").strip()
            for _agent_id, _name, _template, created_by_weaver_id in rows
            if str(created_by_weaver_id or "").strip()
        }
        candidates = set()
        for agent_id, name, template, created_by_weaver_id in rows:
            agent_id = str(agent_id or "").strip()
            if not agent_id:
                continue
            if re.search(r"weaver", str(name or ""), re.IGNORECASE):
                candidates.add(agent_id)
                continue
            if str(template or "").strip().lower() == "weaver":
                candidates.add(agent_id)
                continue
            if not str(created_by_weaver_id or "").strip() and agent_id in referenced_ids:
                candidates.add(agent_id)
        return sorted(candidates)

    def _configured_weaver_candidate_id(self) -> str:
        row = self._conn.execute(
            "SELECT weaver_agent_id FROM group_settings WHERE group_name=?",
            (_KINDS_WEAVER_GROUP,),
        ).fetchone()
        configured_id = str(row[0] or "").strip() if row else ""
        if not configured_id:
            return ""
        exists = self._conn.execute(
            "SELECT 1 FROM agents "
            "WHERE id=? AND cell_type='agent' AND group_name=? "
            "LIMIT 1",
            (configured_id, _KINDS_WEAVER_GROUP),
        ).fetchone()
        return configured_id if exists else ""

    def _resolve_weaver_backfill_target(self) -> tuple[Optional[str], bool]:
        configured_id = self._configured_weaver_candidate_id()
        if configured_id:
            return configured_id, True

        candidate_ids = self._find_weaver_candidate_ids()
        if not candidate_ids:
            log.info("migration: no Weaver found, skipping engineer backfill")
            return None, True
        if len(candidate_ids) == 1:
            return candidate_ids[0], True

        override = str(os.getenv(_KINDS_WEAVER_OVERRIDE_ENV, "") or "").strip()
        if override and override in candidate_ids:
            return override, True

        if override:
            log.error(
                "migration: multiple Weaver candidates found %s; %s=%r did not match",
                candidate_ids,
                _KINDS_WEAVER_OVERRIDE_ENV,
                override,
            )
        else:
            log.error(
                "migration: multiple Weaver candidates found %s; set %s to continue",
                candidate_ids,
                _KINDS_WEAVER_OVERRIDE_ENV,
            )
        return None, False

    def _resolve_backfilled_weaver_identity(self, engineer_id: str) -> tuple[str, str]:
        rows = self._conn.execute(
            "SELECT id, name, slug FROM agents"
        ).fetchall()
        existing_names = {
            str(name or "").strip()
            for agent_id, name, _slug in rows
            if str(agent_id or "") != str(engineer_id or "")
            and str(name or "").strip()
        }
        target_name = _unique_value(_KINDS_WEAVER_NAME, existing_names)
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
            f"Install Loom {prior_major}.x first, boot once so the kinds-refactor migration runs, then upgrade to Loom {__version__}.\n"
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

        if self._column_exists("agents", "created_by_weaver_id"):
            if not self._column_exists("agents", "kind"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_weaver_id, '')) != ''"
                ).fetchone()
            elif not self._column_exists("agents", "owner_engineer_id"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_weaver_id, '')) != '' "
                    "AND TRIM(COALESCE(kind, '')) = ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_weaver_id, '')) != '' "
                    "AND (TRIM(COALESCE(kind, '')) = '' "
                    "OR TRIM(COALESCE(owner_engineer_id, '')) = '')"
                ).fetchone()
            count += int(row[0] or 0)

        if self._column_exists("board_tasks", "weaver_owner_id"):
            if not self._column_exists("board_tasks", "assigned_engineer_id"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM board_tasks "
                    "WHERE TRIM(COALESCE(weaver_owner_id, '')) != ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM board_tasks "
                    "WHERE TRIM(COALESCE(weaver_owner_id, '')) != '' "
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
            self._column_exists("agents", "created_by_weaver_id")
            and self._column_exists("agents", "owner_engineer_id")
        ):
            for agent_id, legacy_owner, owner_engineer_id in self._conn.execute(
                "SELECT id, created_by_weaver_id, owner_engineer_id FROM agents"
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
            self._column_exists("board_tasks", "weaver_owner_id")
            and self._column_exists("board_tasks", "assigned_engineer_id")
        ):
            for task_id, legacy_owner, assigned_engineer_id in self._conn.execute(
                "SELECT id, weaver_owner_id, assigned_engineer_id FROM board_tasks"
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
            for col in ("template", "created_by_weaver_id")
        )
        tasks_has_legacy = self._column_exists("board_tasks", "weaver_owner_id")
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
                drop_columns={"template", "created_by_weaver_id"},
            )
            self._rebuild_table_without_columns(
                "board_tasks",
                new_table="board_tasks_new",
                drop_columns={"weaver_owner_id"},
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

    def _load_group_weaver_map(self) -> dict[str, str]:
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
            "SELECT group_name, weaver_agent_id FROM group_settings"
        ).fetchall()
        group_weaver_map: dict[str, str] = {}
        for group_name, weaver_agent_id in rows:
            group_name = str(group_name or "").strip()
            if not group_name:
                continue
            weaver_agent_id = str(weaver_agent_id or "").strip()
            if (
                weaver_agent_id
                and weaver_agent_id
                in valid_agent_ids_by_group.get(group_name, set())
            ):
                group_weaver_map[group_name] = weaver_agent_id
                continue
            if weaver_agent_id:
                log.warning(
                    "migration: ignoring stale weaver_agent_id=%r for group=%r",
                    weaver_agent_id,
                    group_name,
                )
            group_weaver_map[group_name] = ""
        return group_weaver_map

    def _backfill_task_assignments_from_group_settings(
        self,
        group_weaver_map: dict[str, str],
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
            assigned_engineer_id = group_weaver_map.get(
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

        engineer_id, proceed = self._resolve_weaver_backfill_target()
        if not proceed:
            return

        worker_count = 0
        task_count = 0
        try:
            self._conn.execute("BEGIN")

            engineer_name = engineer_slug = ""
            if engineer_id:
                engineer_name, engineer_slug = self._resolve_backfilled_weaver_identity(
                    engineer_id
                )

            template_sql = self._optional_column_sql("agents", "template")
            created_by_sql = self._optional_column_sql(
                "agents", "created_by_weaver_id"
            )
            rows = self._conn.execute(
                f"SELECT id, cell_type, {template_sql}, {created_by_sql} "
                "FROM agents"
            ).fetchall()
            for agent_id, cell_type, template, created_by_weaver_id in rows:
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
                        str(created_by_weaver_id or ""),
                        agent_id,
                    ),
                )
                worker_count += 1

            task_count = int(
                self._conn.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0]
            )
            group_weaver_map = self._load_group_weaver_map()
            configured_loom_weaver_id = group_weaver_map.get(_KINDS_WEAVER_GROUP, "")
            self._backfill_task_assignments_from_group_settings(
                group_weaver_map,
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
            group_weaver_map = self._load_group_weaver_map()
            updated = self._backfill_task_assignments_from_group_settings(
                group_weaver_map,
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
