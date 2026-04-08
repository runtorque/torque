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
import shutil
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Optional

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

_AGENT_PERSISTED_COLS = [
    "id", "name", "slug", "group_name", "cell_type", "session_id", "profile",
    "command", "directory", "tab_color", "icon", "template", "window_id",
    "parent_id", "status", "worktree_path", "worktree_branch",
    "worktree_repo_root", "worktree_base_dir", "worktree_base_branch",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "agent_type",
    "agent_session_id", "session_resume", "idle_timeout",
    "tasks_dispatched",
]

# GroupSettings fields that store dicts — persisted as JSON text.
_GS_JSON_FIELDS = {"env_vars", "agent_env_vars", "terminal_env_vars",
                    "board_default_labels", "worktree_symlinks"}

# GroupSettings fields that are booleans — stored as INTEGER 0/1.
_GS_BOOL_FIELDS = {
    "collapsed_default", "filter_by_window", "git_worktree",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash",
    "agent_session_resume", "agent_always_custom_dialog",
    "dispatch_auto_terminals",
    "notifications", "notify_on_finish", "notify_on_error",
    "notify_on_attention", "terminal_always_custom_dialog",
    "terminal_close_on_disconnect",
}




class LoomDB(BoardPersistenceMixin, MemoryPersistenceMixin):
    """Facade over Loom's SQLite persistence helpers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def init(self):
        """Open connection, enable WAL, create tables if needed."""
        self._conn = sqlite3.connect(str(self.db_path))
        initialize_database(self._conn, self.backfill_agent_history)
        self.migrate_task_ids_if_needed()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

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
        self._conn.execute("""
            INSERT OR REPLACE INTO agents
                (id, name, slug, group_name, cell_type, session_id, profile,
                 command, directory, tab_color, icon, template, window_id,
                 parent_id, status, worktree_path, worktree_branch,
                 worktree_repo_root, worktree_base_dir, worktree_base_branch,
                 worktree_auto_checkpoint, checkpoint_on_progress,
                 worktree_merge_squash,
                 agent_type, agent_session_id, session_resume, idle_timeout,
                 tasks_dispatched)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cell.id, cell.name, cell.slug, cell.group, cell.cell_type,
            cell.session_id, cell.profile, cell.command, cell.directory,
            cell.tab_color, cell.icon, cell.template, cell.window_id,
            cell.parent_id, cell.status, cell.worktree_path,
            cell.worktree_branch, cell.worktree_repo_root,
            cell.worktree_base_dir, cell.worktree_base_branch,
            int(cell.worktree_auto_checkpoint),
            int(cell.checkpoint_on_progress),
            int(cell.worktree_merge_squash), cell.agent_type,
            cell.agent_session_id, int(cell.session_resume),
            cell.idle_timeout,
            cell.tasks_dispatched,
        ))
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
        self._conn.execute("DELETE FROM global_settings")
        for key, value in d.items():
            self._conn.execute(
                "INSERT INTO global_settings (key, value) VALUES (?,?)",
                (key, json.dumps(value)))
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
                "max_concurrent, target_agent_id, enqueued_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    group_name,
                    pos,
                    item.get("task_id", ""),
                    item.get("agent_group", ""),
                    int(item.get("max_concurrent", 1) or 1),
                    item.get("target_agent_id", ""),
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
            insert_board_task(self._conn, task)

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
                          before_id: int = 0) -> list[dict]:
        """Load a page of panel events ordered by id DESC.

        Returns dicts with 'group' key (matching in-memory format).
        """
        if before_id:
            rows = self._conn.execute(
                "SELECT id, timestamp, kind, cell_id, agent_name, "
                "group_name, message, task_id FROM panel_events "
                "WHERE id < ? ORDER BY id DESC LIMIT ?",
                (before_id, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, timestamp, kind, cell_id, agent_name, "
                "group_name, message, task_id FROM panel_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
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

    # -- Weaver settings & journal -------------------------------------------

    def save_weaver_settings(self, group_name: str, settings: dict):
        """Upsert weaver settings for a group."""
        enabled_events = json.dumps(
            settings.get("enabled_events",
                         ["agent_started", "task_dispatched", "task_derived"]))
        self._conn.execute("""
            INSERT OR REPLACE INTO weaver_settings
                (group_name, push_interval, max_interval, heartbeat_interval,
                 paused,
                 custom_instructions, pending_question, pending_note,
                 pending_note_kind, enabled_events,
                 weaver_provider, weaver_boot_command)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            group_name,
            settings.get("push_interval", 60),
            settings.get("max_interval", 300),
            settings.get("heartbeat_interval",
                         settings.get("max_interval", 300)),
            1 if settings.get("paused", False) else 0,
            settings.get("custom_instructions", ""),
            settings.get("pending_question", ""),
            settings.get("pending_note", ""),
            settings.get("pending_note_kind", ""),
            enabled_events,
            settings.get("weaver_provider", ""),
            settings.get("weaver_boot_command", ""),
        ))
        self._conn.commit()

    def load_weaver_settings(self, group_name: str) -> dict | None:
        """Load weaver settings for a group. Returns None if not set."""
        row = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, paused, "
            "custom_instructions, pending_question, pending_note, pending_note_kind, enabled_events, "
            "weaver_provider, weaver_boot_command "
            "FROM weaver_settings "
            "WHERE group_name=?", (group_name,)).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[9])
        except (json.JSONDecodeError, TypeError):
            enabled = ["agent_started", "task_dispatched", "task_derived"]
        heartbeat_interval = row[3]
        if heartbeat_interval is None or (
                heartbeat_interval == 300 and row[2] != 300):
            heartbeat_interval = row[2]
        return {
            "group": row[0],
            "push_interval": row[1],
            "max_interval": row[2],
            "heartbeat_interval": heartbeat_interval,
            "paused": bool(row[4]),
            "custom_instructions": row[5],
            "pending_question": row[6],
            "pending_note": row[7],
            "pending_note_kind": row[8],
            "enabled_events": enabled,
            "weaver_provider": row[10] if len(row) > 10 else "",
            "weaver_boot_command": row[11] if len(row) > 11 else "",
        }

    def delete_weaver_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM weaver_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def load_all_weaver_settings(self) -> dict[str, dict]:
        """Load weaver settings for all groups. Returns {group: settings}."""
        rows = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, paused, "
            "custom_instructions, pending_question, pending_note, pending_note_kind, enabled_events, "
            "weaver_provider, weaver_boot_command "
            "FROM weaver_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[9])
            except (json.JSONDecodeError, TypeError):
                enabled = ["agent_started", "task_dispatched", "task_derived"]
            heartbeat_interval = row[3]
            if heartbeat_interval is None or (
                    heartbeat_interval == 300 and row[2] != 300):
                heartbeat_interval = row[2]
            result[row[0]] = {
                "group": row[0],
                "push_interval": row[1],
                "max_interval": row[2],
                "heartbeat_interval": heartbeat_interval,
                "paused": bool(row[4]),
                "custom_instructions": row[5],
                "pending_question": row[6],
                "pending_note": row[7],
                "pending_note_kind": row[8],
                "enabled_events": enabled,
                "weaver_provider": row[10] if len(row) > 10 else "",
                "weaver_boot_command": row[11] if len(row) > 11 else "",
            }
        return result

    def save_journal_entry(self, group_name: str, timestamp: float,
                           entry_type: str, entry: str) -> int:
        """Insert a weaver journal entry. Returns the new row ID."""
        c = self._conn.execute(
            "INSERT INTO weaver_journal "
            "(group_name, timestamp, entry_type, entry) "
            "VALUES (?,?,?,?)",
            (group_name, timestamp, entry_type, entry))
        self._conn.commit()
        return c.lastrowid

    def load_journal_entries(self, group_name: str, limit: int = 20,
                             entry_type: str = "") -> list[dict]:
        """Load recent journal entries for a group, newest first."""
        if entry_type:
            rows = self._conn.execute(
                "SELECT id, group_name, timestamp, entry_type, entry "
                "FROM weaver_journal WHERE group_name=? AND entry_type=? "
                "ORDER BY id DESC LIMIT ?",
                (group_name, entry_type, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, group_name, timestamp, entry_type, entry "
                "FROM weaver_journal WHERE group_name=? "
                "ORDER BY id DESC LIMIT ?",
                (group_name, limit)).fetchall()
        return [{"id": r[0], "group": r[1], "timestamp": r[2],
                 "type": r[3], "entry": r[4]} for r in rows]

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
        rows = self._conn.execute(
            "SELECT id, name, slug, group_name, agent_type, template, "
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
                c.execute("""
                    INSERT INTO agents
                        (id, name, slug, group_name, cell_type, session_id,
                         profile, command, directory, tab_color, icon,
                         template, window_id, parent_id, status,
                         worktree_path, worktree_branch, worktree_repo_root,
                         worktree_base_dir, worktree_base_branch,
                         worktree_auto_checkpoint, checkpoint_on_progress,
                         worktree_merge_squash,
                         agent_type, agent_session_id, session_resume,
                         idle_timeout, tasks_dispatched)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    a.get("id", aid),
                    a.get("name", ""),
                    a.get("slug", ""),
                    a.get("group", ""),
                    a.get("cell_type", "agent"),
                    a.get("session_id"),
                    a.get("profile", "Default"),
                    a.get("command", ""),
                    a.get("directory", ""),
                    a.get("tab_color", ""),
                    a.get("icon", ""),
                    a.get("template", ""),
                    a.get("window_id", ""),
                    a.get("parent_id", ""),
                    a.get("status", "stopped"),
                    a.get("worktree_path", ""),
                    a.get("worktree_branch", ""),
                    a.get("worktree_repo_root", ""),
                    a.get("worktree_base_dir", ".loom/worktrees"),
                    a.get("worktree_base_branch", ""),
                    int(a.get("worktree_auto_checkpoint", False)),
                    int(a.get("checkpoint_on_progress", False)),
                    int(a.get("worktree_merge_squash", True)),
                    a.get("agent_type", ""),
                    a.get("agent_session_id", ""),
                    int(a.get("session_resume", True)),
                    a.get("idle_timeout", 5),
                    a.get("tasks_dispatched", 0),
                ))

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
                d = dict(t) if isinstance(t, dict) else asdict(t)
                labels = json.dumps(d.pop("labels", []))
                action_vars = json.dumps(d.pop("action_vars", {}))
                messages = json.dumps(d.pop("messages", []))
                depends_on = json.dumps(d.pop("depends_on", []))
                attachments = json.dumps(d.pop("attachments", []))
                health_details = json.dumps(d.pop("health_details", {}))
                artifacts = json.dumps(d.pop("artifacts", []))
                verification_summary = json.dumps(
                    d.pop("verification_summary", {})
                )
                group_name = d.pop("group", "")
                values = (
                    d.get("id", tid), d.get("task", ""),
                    d.get("description", ""), d.get("slug", ""),
                    group_name, d.get("action_name", ""),
                    d.get("agent_template", ""), action_vars,
                    d.get("instructions", ""),
                    d.get("context", ""), d.get("criteria", ""),
                    d.get("lane", "Backlog"), d.get("position", 0),
                    d.get("agent_id", ""), labels,
                    d.get("created_at", ""), d.get("updated_at", ""),
                    d.get("lane_entered_at", ""),
                    d.get("provider", ""), d.get("external_id", ""),
                    d.get("external_url", ""),
                    d.get("parent_task_id", ""), d.get("pipeline_depth", 0),
                    d.get("pipeline_root_id", ""), d.get("status", ""),
                    d.get("scheduled_at", ""), messages, depends_on,
                    attachments, d.get("health_state", "healthy"),
                    d.get("health_since", ""), health_details,
                    artifacts,
                    d.get("verification_mode", ""),
                    d.get("verification_state", ""),
                    d.get("verification_notes", ""),
                    d.get("verification_updated_at", ""),
                    d.get("verification_updated_by", ""),
                    verification_summary,
                    d.get("archived_at", ""),
                    d.get("archived_from_lane", ""),
                )
                c.execute("""
                    INSERT INTO board_tasks
                        (id, task, description, slug, group_name,
                         action_name, agent_template,
                         action_vars, instructions, context,
                         criteria, lane, position, agent_id, labels,
                         created_at, updated_at, lane_entered_at, provider, external_id,
                         external_url, parent_task_id, pipeline_depth,
                         pipeline_root_id, status, scheduled_at, messages,
                         depends_on, attachments, health_state, health_since,
                         health_details, artifacts, verification_mode,
                         verification_state, verification_notes,
                         verification_updated_at, verification_updated_by,
                         verification_summary, archived_at,
                         archived_from_lane)
                    VALUES ({placeholders})
                """.format(placeholders=",".join(["?"] * len(values))), values)

            # Auto-dispatch queues
            c.execute("DELETE FROM auto_dispatch_queue")
            for gname, entries in state_dict.get(
                    "auto_dispatch_queues", {}).items():
                for pos, entry in enumerate(entries):
                    item = dict(entry)
                    c.execute("""
                        INSERT INTO auto_dispatch_queue
                            (group_name, position, task_id, agent_group,
                             max_concurrent, target_agent_id, enqueued_at)
                        VALUES (?,?,?,?,?,?,?)
                    """, (
                        gname,
                        pos,
                        item.get("task_id", ""),
                        item.get("agent_group", ""),
                        int(item.get("max_concurrent", 1) or 1),
                        item.get("target_agent_id", ""),
                        item.get("enqueued_at", ""),
                    ))

            # UI state
            c.execute("DELETE FROM ui_state")
            for key in ("panel_active", "board_panel_height"):
                val = state_dict.get(key)
                if val is not None:
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
            d["worktree_auto_checkpoint"] = bool(
                d.get("worktree_auto_checkpoint", 0))
            d["checkpoint_on_progress"] = bool(
                d.get("checkpoint_on_progress", 0))
            d["worktree_merge_squash"] = bool(
                d.get("worktree_merge_squash", 1))
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
                "max_concurrent, target_agent_id, enqueued_at "
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
