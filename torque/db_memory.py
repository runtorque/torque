"""Shared-memory persistence helpers for TorqueDB."""

import time


class MemoryPersistenceMixin:
    """Shared memory CRUD and row-mapping helpers mixed into ``TorqueDB``."""

    def save_memory_entry(self, entry: dict):
        """Insert or replace a shared memory entry."""
        self._conn.execute(
            """
            INSERT INTO memory_entries
                (id, project_key, group_name, scope_kind, scope_ref,
                 entry_type, title, content, pinned, task_id,
                 source_kind, source_id, source_name, retention_kind,
                 expires_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                project_key=excluded.project_key,
                group_name=excluded.group_name,
                scope_kind=excluded.scope_kind,
                scope_ref=excluded.scope_ref,
                entry_type=excluded.entry_type,
                title=excluded.title,
                content=excluded.content,
                pinned=excluded.pinned,
                task_id=excluded.task_id,
                source_kind=excluded.source_kind,
                source_id=excluded.source_id,
                source_name=excluded.source_name,
                retention_kind=excluded.retention_kind,
                expires_at=excluded.expires_at,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at
            """,
            (
                entry["id"],
                entry.get("project_key", ""),
                entry.get("group_name", ""),
                entry["scope_kind"],
                entry.get("scope_ref", ""),
                entry["entry_type"],
                entry.get("title", ""),
                entry.get("content", ""),
                1 if entry.get("pinned") else 0,
                entry.get("task_id", ""),
                entry.get("source_kind", ""),
                entry.get("source_id", ""),
                entry.get("source_name", ""),
                entry.get("retention_kind", "ttl"),
                None if entry.get("expires_at") in ("", 0) else entry.get("expires_at"),
                float(entry.get("created_at", 0)),
                float(entry.get("updated_at", 0)),
            ),
        )
        self._conn.commit()

    def save_memory_link(self, link: dict):
        """Insert or keep an existing memory-entry link."""
        self._conn.execute(
            """
            INSERT INTO memory_links
                (entry_id, target_kind, target_ref, created_at)
            VALUES (?,?,?,?)
            ON CONFLICT(entry_id, target_kind, target_ref) DO NOTHING
            """,
            (
                link["entry_id"],
                link["target_kind"],
                link.get("target_ref", ""),
                float(link.get("created_at", 0)),
            ),
        )
        self._conn.commit()

    def set_memory_entry_pinned(self, entry_id: str, pinned: bool,
                                updated_at: float):
        """Update the pinned state of a memory entry."""
        self._conn.execute(
            "UPDATE memory_entries SET pinned=?, updated_at=? WHERE id=?",
            (1 if pinned else 0, float(updated_at), entry_id),
        )
        self._conn.commit()

    def purge_expired_memory_entries(self, now: float | None = None) -> int:
        """Delete every memory entry whose stored expiry has elapsed."""
        if now is None:
            now = time.time()
        cur = self._conn.execute(
            "DELETE FROM memory_entries "
            "WHERE expires_at IS NOT NULL AND expires_at<=?",
            (float(now),),
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

    def load_memory_entry(self, entry_id: str, *,
                          now: float | None = None) -> dict | None:
        """Load a memory entry by ID."""
        self.purge_expired_memory_entries(now=now)
        row = self._conn.execute(
            "SELECT id, project_key, group_name, scope_kind, scope_ref, "
            "entry_type, title, content, pinned, task_id, source_kind, "
            "source_id, source_name, retention_kind, expires_at, "
            "created_at, updated_at "
            "FROM memory_entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not row:
            return None
        return {
            **self._row_to_memory_entry(row),
            "links": self.load_memory_links(entry_id=entry_id),
        }

    def load_memory_links(self, *, entry_id: str = "",
                          target_kind: str = "", target_ref: str = "",
                          limit: int = 100, offset: int = 0) -> list[dict]:
        """List memory-entry links by entry or target."""
        sql = (
            "SELECT entry_id, target_kind, target_ref, created_at "
            "FROM memory_links WHERE 1=1"
        )
        params = []
        if entry_id:
            sql += " AND entry_id=?"
            params.append(entry_id)
        if target_kind:
            sql += " AND target_kind=?"
            params.append(target_kind)
        if target_ref:
            sql += " AND target_ref=?"
            params.append(target_ref)
        sql += (
            " ORDER BY created_at ASC, entry_id ASC, target_kind ASC, target_ref ASC "
            "LIMIT ? OFFSET ?"
        )
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_memory_link(row) for row in rows]

    def _load_memory_links_map(self, entry_ids: list[str]) -> dict[str, list[dict]]:
        """Return {entry_id: [link, ...]} for the provided entry IDs."""
        if not entry_ids:
            return {}
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self._conn.execute(
            "SELECT entry_id, target_kind, target_ref, created_at "
            f"FROM memory_links WHERE entry_id IN ({placeholders}) "
            "ORDER BY created_at ASC, entry_id ASC, target_kind ASC, target_ref ASC",
            entry_ids,
        ).fetchall()
        links_by_entry: dict[str, list[dict]] = {entry_id: [] for entry_id in entry_ids}
        for row in rows:
            link = self._row_to_memory_link(row)
            links_by_entry.setdefault(link["entry_id"], []).append(link)
        return links_by_entry

    @staticmethod
    def _row_to_memory_link(row) -> dict:
        return {
            "entry_id": row[0],
            "target_kind": row[1],
            "target_ref": row[2],
            "created_at": row[3],
        }

    @staticmethod
    def _row_to_memory_entry(row) -> dict:
        return {
            "id": row[0],
            "project_key": row[1],
            "group_name": row[2],
            "scope_kind": row[3],
            "scope_ref": row[4],
            "entry_type": row[5],
            "title": row[6],
            "content": row[7],
            "pinned": bool(row[8]),
            "task_id": row[9],
            "source_kind": row[10],
            "source_id": row[11],
            "source_name": row[12],
            # Keep the deprecated field available to current panel consumers.
            "retention_kind": row[13] or "ttl",
            "expires_at": row[14],
            "created_at": row[15],
            "updated_at": row[16],
        }

    def load_memory_entries(self, *, group_name: str = "",
                            project_key: str = "", scope_kind: str = "",
                            scope_ref: str = "", entry_type: str = "",
                            task_id: str = "", pinned_only: bool = False,
                            search: str = "", linked_target_kind: str = "",
                            linked_target_ref: str = "", limit: int = 20,
                            offset: int = 0, now: float | None = None
                            ) -> list[dict]:
        """List memory entries with deterministic filtering and ordering."""
        self.purge_expired_memory_entries(now=now)
        sql = (
            "SELECT id, project_key, group_name, scope_kind, scope_ref, "
            "entry_type, title, content, pinned, task_id, source_kind, "
            "source_id, source_name, retention_kind, expires_at, "
            "created_at, updated_at "
            "FROM memory_entries WHERE 1=1"
        )
        params = []
        if group_name:
            sql += " AND group_name=?"
            params.append(group_name)
        if project_key:
            sql += " AND project_key=?"
            params.append(project_key)
        if scope_kind:
            sql += " AND scope_kind=?"
            params.append(scope_kind)
        if scope_ref:
            sql += " AND scope_ref=?"
            params.append(scope_ref)
        if entry_type:
            sql += " AND entry_type=?"
            params.append(entry_type)
        if task_id:
            sql += " AND task_id=?"
            params.append(task_id)
        if pinned_only:
            sql += " AND pinned=1"
        if search:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])
        if linked_target_kind or linked_target_ref:
            sql += (
                " AND EXISTS ("
                "SELECT 1 FROM memory_links ml "
                "WHERE ml.entry_id=memory_entries.id"
            )
            if linked_target_kind:
                sql += " AND ml.target_kind=?"
                params.append(linked_target_kind)
            if linked_target_ref:
                sql += " AND ml.target_ref=?"
                params.append(linked_target_ref)
            sql += ")"
        sql += (
            " ORDER BY pinned DESC, created_at DESC, id DESC "
            "LIMIT ? OFFSET ?"
        )
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = self._conn.execute(sql, params).fetchall()
        entries = [self._row_to_memory_entry(row) for row in rows]
        links_by_entry = self._load_memory_links_map(
            [entry["id"] for entry in entries]
        )
        for entry in entries:
            entry["links"] = links_by_entry.get(entry["id"], [])
        return entries
