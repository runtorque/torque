"""Auto-dispatch persistence and canonical task-ID migration."""

import json
import shutil
import sqlite3
from dataclasses import asdict
from pathlib import Path

from torque.config import ATTACHMENTS_DIR
from torque.db_board import decode_board_task_row
from torque.task_ids import (
    format_derived_task_id,
    format_root_task_id,
    is_canonical_task_id,
    normalize_group_prefix,
    parse_task_id,
)


class TaskIdPersistenceMixin:
    """Persist auto-dispatch queues and migrate legacy task identifiers."""

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

    def migrate_task_ids_if_needed(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
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
            if manage_transaction:
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
        if manage_transaction:
            self._conn.commit()
