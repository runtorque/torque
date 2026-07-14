"""SQLite persistence for Scratchpad notes and Mind Maps."""

from __future__ import annotations

from datetime import datetime, timezone

from ..task_ids import (
    format_mind_map_id,
    format_scratchpad_note_id,
    normalize_group_prefix,
)
from .common import (
    json_payload as _json_payload,
    json_payload_text as _json_payload_text,
    normalize_actor_kind as _normalize_actor_kind,
    slugify as _slugify,
    snapshot_db_payload as _snapshot_db_payload,
    unique_value as _unique_value,
)


THINKING_SCRATCHPAD_NOTE_COLUMNS = (
    "id",
    "slug",
    "group_name",
    "title",
    "body",
    "context_json",
    "links_json",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "archived_by_kind",
    "archived_by_id",
    "deleted_by_kind",
    "deleted_by_id",
    "created_at",
    "updated_at",
    "archived_at",
    "deleted_at",
)
THINKING_MIND_MAP_COLUMNS = (
    "id",
    "slug",
    "group_name",
    "title",
    "description",
    "metadata_json",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "archived_by_kind",
    "archived_by_id",
    "deleted_by_kind",
    "deleted_by_id",
    "created_at",
    "updated_at",
    "archived_at",
    "deleted_at",
)
THINKING_MIND_MAP_NODE_COLUMNS = (
    "id",
    "map_id",
    "label",
    "title",
    "notes",
    "node_type",
    "tags_json",
    "color",
    "x",
    "y",
    "position_json",
    "sort_order",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "deleted_by_kind",
    "deleted_by_id",
    "created_at",
    "updated_at",
    "deleted_at",
)
THINKING_MIND_MAP_LINK_COLUMNS = (
    "id",
    "map_id",
    "source_node_id",
    "target_node_id",
    "label",
    "link_type",
    "sort_order",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "deleted_by_kind",
    "deleted_by_id",
    "created_at",
    "updated_at",
    "deleted_at",
)

def _decode_scratchpad_note_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or THINKING_SCRATCHPAD_NOTE_COLUMNS, row))
    item["group"] = item.get("group_name", "")
    item["context"] = _json_payload(item.get("context_json", "{}"), {})
    item["links"] = _json_payload(item.get("links_json", "[]"), [])
    item["archived"] = bool(str(item.get("archived_at", "") or "").strip())
    item["deleted"] = bool(str(item.get("deleted_at", "") or "").strip())
    return item


def _decode_mind_map_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or THINKING_MIND_MAP_COLUMNS, row))
    item["group"] = item.get("group_name", "")
    item["metadata"] = _json_payload(item.get("metadata_json", "{}"), {})
    item["archived"] = bool(str(item.get("archived_at", "") or "").strip())
    item["deleted"] = bool(str(item.get("deleted_at", "") or "").strip())
    return item


def _decode_mind_map_node_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or THINKING_MIND_MAP_NODE_COLUMNS, row))
    item["tags"] = _json_payload(item.get("tags_json", "[]"), [])
    item["position"] = _json_payload(item.get("position_json", "{}"), {})
    try:
        item["x"] = float(item.get("x", 0) or 0)
    except (TypeError, ValueError):
        item["x"] = 0.0
    try:
        item["y"] = float(item.get("y", 0) or 0)
    except (TypeError, ValueError):
        item["y"] = 0.0
    try:
        item["sort_order"] = int(item.get("sort_order", 0) or 0)
    except (TypeError, ValueError):
        item["sort_order"] = 0
    item["deleted"] = bool(str(item.get("deleted_at", "") or "").strip())
    return item


def _decode_mind_map_link_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or THINKING_MIND_MAP_LINK_COLUMNS, row))
    try:
        item["sort_order"] = int(item.get("sort_order", 0) or 0)
    except (TypeError, ValueError):
        item["sort_order"] = 0
    item["deleted"] = bool(str(item.get("deleted_at", "") or "").strip())
    return item


class ThinkingPersistenceMixin:
    """TorqueDB API for Scratchpad and Mind Map persistence."""

    def next_scratchpad_note_id(self, group_name: str) -> str:
        """Allocate the next durable Thinking Scratchpad note ID for a group."""
        group_prefix = normalize_group_prefix(group_name)
        row = self._conn.execute(
            "SELECT next_note_number FROM scratchpad_note_id_counters "
            "WHERE group_prefix=?",
            (group_prefix,),
        ).fetchone()
        number = int(row[0] if row else 1)
        self._conn.execute(
            "INSERT OR REPLACE INTO scratchpad_note_id_counters "
            "(group_prefix, next_note_number) VALUES (?, ?)",
            (group_prefix, number + 1),
        )
        self._conn.commit()
        return format_scratchpad_note_id(group_prefix, number)

    def next_mind_map_id(self, group_name: str) -> str:
        """Allocate the next durable Thinking Mind Map ID for a group."""
        group_prefix = normalize_group_prefix(group_name)
        row = self._conn.execute(
            "SELECT next_map_number FROM mind_map_id_counters "
            "WHERE group_prefix=?",
            (group_prefix,),
        ).fetchone()
        number = int(row[0] if row else 1)
        self._conn.execute(
            "INSERT OR REPLACE INTO mind_map_id_counters "
            "(group_prefix, next_map_number) VALUES (?, ?)",
            (group_prefix, number + 1),
        )
        self._conn.commit()
        return format_mind_map_id(group_prefix, number)

    def _next_mind_map_item_id(self, map_id: str, item_kind: str) -> str:
        map_id = str(map_id or "").strip()
        if not map_id:
            raise ValueError("map_id is required")
        if item_kind not in {"node", "link"}:
            raise ValueError("item_kind must be node or link")
        row = self._conn.execute(
            "SELECT next_node_number, next_link_number "
            "FROM thinking_mind_map_item_counters WHERE map_id=?",
            (map_id,),
        ).fetchone()
        next_node = int(row[0] if row else 1)
        next_link = int(row[1] if row else 1)
        if item_kind == "node":
            number = next_node
            next_node += 1
            suffix = "N"
        else:
            number = next_link
            next_link += 1
            suffix = "L"
        self._conn.execute(
            "INSERT OR REPLACE INTO thinking_mind_map_item_counters "
            "(map_id, next_node_number, next_link_number) VALUES (?, ?, ?)",
            (map_id, next_node, next_link),
        )
        self._conn.commit()
        return f"{map_id}:{suffix}{number}"

    def _thinking_slug_for_group(self, table: str, group_name: str, title: str,
                                 existing_id: str = "",
                                 default: str = "thinking") -> str:
        if table not in {"thinking_scratchpad_notes", "thinking_mind_maps"}:
            raise ValueError("unsupported thinking slug table")
        base = _slugify(title or default)
        existing_id = str(existing_id or "").strip()
        rows = self._conn.execute(
            f"SELECT id, slug FROM {table} WHERE group_name=? AND deleted_at=''",
            (str(group_name or "").strip(),),
        ).fetchall()
        existing = {
            str(slug or "")
            for item_id, slug in rows
            if str(slug or "") and str(item_id or "") != existing_id
        }
        return _unique_value(base, existing)

    def _mind_map_sort_order(self, table: str, map_id: str) -> int:
        if table not in {"thinking_mind_map_nodes", "thinking_mind_map_links"}:
            raise ValueError("unsupported mind map item table")
        row = self._conn.execute(
            f"SELECT COALESCE(MAX(sort_order), 0) + 1 FROM {table} "
            "WHERE map_id=? AND deleted_at=''",
            (str(map_id or "").strip(),),
        ).fetchone()
        return int(row[0] if row else 1)

    def _mind_map_counts(self, map_id: str) -> dict:
        map_id = str(map_id or "").strip()
        node_row = self._conn.execute(
            "SELECT COUNT(*) FROM thinking_mind_map_nodes "
            "WHERE map_id=? AND deleted_at=''",
            (map_id,),
        ).fetchone()
        link_row = self._conn.execute(
            "SELECT COUNT(*) FROM thinking_mind_map_links "
            "WHERE map_id=? AND deleted_at=''",
            (map_id,),
        ).fetchone()
        return {
            "node_count": int(node_row[0] if node_row else 0),
            "link_count": int(link_row[0] if link_row else 0),
        }

    def load_scratchpad_note(self, note_id: str) -> dict | None:
        note_id = str(note_id or "").strip()
        if not note_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(THINKING_SCRATCHPAD_NOTE_COLUMNS)
            + " FROM thinking_scratchpad_notes WHERE id=?",
            (note_id,),
        )
        row = cursor.fetchone()
        return _decode_scratchpad_note_row(row, [d[0] for d in cursor.description])

    def list_scratchpad_notes(self, *, group: str = "",
                              include_archived: bool = False,
                              include_deleted: bool = False,
                              limit: int = 200) -> list[dict]:
        query = (
            "SELECT " + ", ".join(THINKING_SCRATCHPAD_NOTE_COLUMNS)
            + " FROM thinking_scratchpad_notes"
        )
        filters = []
        params = []
        group = str(group or "").strip()
        if group:
            filters.append("group_name=?")
            params.append(group)
        if not include_deleted:
            filters.append("deleted_at=''")
        if not include_archived:
            filters.append("archived_at=''")
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT ?"
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 200
        params.append(max(1, min(limit_value, 1000)))
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_scratchpad_note_row(row, cols) for row in cursor.fetchall()]

    def create_scratchpad_note(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        title = str(row.get("title", "") or "").strip()
        group = str(row.get("group", row.get("group_name", "")) or "").strip()
        if not title:
            raise ValueError("title is required")
        if not group:
            raise ValueError("group is required")
        note_id = str(row.get("id", "") or "").strip()
        if not note_id:
            note_id = self.next_scratchpad_note_id(group)
        if self.load_scratchpad_note(note_id):
            raise ValueError(f"scratchpad note already exists: {note_id}")
        now = datetime.now(timezone.utc).isoformat()
        created_by_kind = _normalize_actor_kind(row.get("created_by_kind", "user"))
        slug = str(row.get("slug", "") or "").strip()
        if not slug:
            slug = self._thinking_slug_for_group(
                "thinking_scratchpad_notes", group, title, note_id,
                default="note",
            )
        values = {
            "id": note_id,
            "slug": slug,
            "group_name": group,
            "title": title,
            "body": str(row.get("body", "") or ""),
            "context_json": _json_payload_text(
                row.get("context", row.get("context_json", {})), {}
            ),
            "links_json": _json_payload_text(
                row.get("links", row.get("links_json", [])), []
            ),
            "created_by_kind": created_by_kind,
            "created_by_id": str(row.get("created_by_id", "") or "").strip(),
            "updated_by_kind": _normalize_actor_kind(
                row.get("updated_by_kind", created_by_kind),
                default=created_by_kind,
            ),
            "updated_by_id": str(
                row.get("updated_by_id", row.get("created_by_id", "")) or ""
            ).strip(),
            "archived_by_kind": "",
            "archived_by_id": "",
            "deleted_by_kind": "",
            "deleted_by_id": "",
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "archived_at": str(row.get("archived_at", "") or ""),
            "deleted_at": str(row.get("deleted_at", "") or ""),
        }
        self._conn.execute(
            "INSERT INTO thinking_scratchpad_notes ("
            + ", ".join(THINKING_SCRATCHPAD_NOTE_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(THINKING_SCRATCHPAD_NOTE_COLUMNS))
            + ")",
            tuple(values[col] for col in THINKING_SCRATCHPAD_NOTE_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_scratchpad_note(note_id)
        if not saved:
            raise RuntimeError(f"failed to load saved scratchpad note {note_id}")
        return saved

    async def create_scratchpad_note_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "thinking", "create_scratchpad_note", _snapshot_db_payload(row_dict or {})
        )

    def update_scratchpad_note(self, note_id: str, patch: dict) -> dict | None:
        note_id = str(note_id or "").strip()
        existing = self.load_scratchpad_note(note_id)
        if not existing:
            return None
        patch = dict(patch or {})
        values = {}
        for key in ("title", "body", "slug", "updated_by_kind", "updated_by_id"):
            if key in patch:
                values[key] = patch[key]
        if "title" in values:
            values["title"] = str(values["title"] or "").strip()
            if not values["title"]:
                raise ValueError("title is required")
            if "slug" not in patch:
                values["slug"] = self._thinking_slug_for_group(
                    "thinking_scratchpad_notes",
                    existing["group_name"],
                    values["title"],
                    note_id,
                    default="note",
                )
        if "slug" in values:
            values["slug"] = str(values["slug"] or "").strip()
        if "body" in values:
            values["body"] = str(values["body"] or "")
        if "context" in patch or "context_json" in patch:
            values["context_json"] = _json_payload_text(
                patch.get("context", patch.get("context_json", {})), {}
            )
        if "links" in patch or "links_json" in patch:
            values["links_json"] = _json_payload_text(
                patch.get("links", patch.get("links_json", [])), []
            )
        if "updated_by_kind" in values:
            values["updated_by_kind"] = _normalize_actor_kind(values["updated_by_kind"])
        if "updated_by_id" in values:
            values["updated_by_id"] = str(values["updated_by_id"] or "").strip()
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE thinking_scratchpad_notes SET {assignments} WHERE id=?",
            tuple(values.values()) + (note_id,),
        )
        self._conn.commit()
        return self.load_scratchpad_note(note_id)

    async def update_scratchpad_note_async(self, note_id: str,
                                           patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "update_scratchpad_note", str(note_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def archive_scratchpad_note(self, note_id: str, *,
                                archived_by_kind: str = "user",
                                archived_by_id: str = "",
                                archived_at: str = "") -> dict | None:
        existing = self.load_scratchpad_note(note_id)
        if not existing:
            return None
        timestamp = str(archived_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(archived_by_kind)
        actor_id = str(archived_by_id or "").strip()
        self._conn.execute(
            "UPDATE thinking_scratchpad_notes SET archived_at=?, "
            "archived_by_kind=?, archived_by_id=?, updated_at=?, "
            "updated_by_kind=?, updated_by_id=? WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(note_id or "").strip()),
        )
        self._conn.commit()
        return self.load_scratchpad_note(note_id)

    async def archive_scratchpad_note_async(self, note_id: str, **kwargs
                                            ) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "archive_scratchpad_note", str(note_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def delete_scratchpad_note(self, note_id: str, *,
                               deleted_by_kind: str = "user",
                               deleted_by_id: str = "",
                               deleted_at: str = "") -> dict | None:
        existing = self.load_scratchpad_note(note_id)
        if not existing:
            return None
        timestamp = str(deleted_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(deleted_by_kind)
        actor_id = str(deleted_by_id or "").strip()
        self._conn.execute(
            "UPDATE thinking_scratchpad_notes SET deleted_at=?, "
            "deleted_by_kind=?, deleted_by_id=?, updated_at=?, "
            "updated_by_kind=?, updated_by_id=? WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(note_id or "").strip()),
        )
        self._conn.commit()
        return self.load_scratchpad_note(note_id)

    async def delete_scratchpad_note_async(self, note_id: str, **kwargs
                                           ) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "delete_scratchpad_note", str(note_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def load_mind_map(self, map_id: str, *, include_counts: bool = False
                      ) -> dict | None:
        map_id = str(map_id or "").strip()
        if not map_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(THINKING_MIND_MAP_COLUMNS)
            + " FROM thinking_mind_maps WHERE id=?",
            (map_id,),
        )
        row = cursor.fetchone()
        item = _decode_mind_map_row(row, [d[0] for d in cursor.description])
        if item and include_counts:
            item.update(self._mind_map_counts(map_id))
        return item

    def list_mind_maps(self, *, group: str = "",
                       include_archived: bool = False,
                       include_deleted: bool = False,
                       include_counts: bool = True,
                       limit: int = 200) -> list[dict]:
        query = (
            "SELECT " + ", ".join(THINKING_MIND_MAP_COLUMNS)
            + " FROM thinking_mind_maps"
        )
        filters = []
        params = []
        group = str(group or "").strip()
        if group:
            filters.append("group_name=?")
            params.append(group)
        if not include_deleted:
            filters.append("deleted_at=''")
        if not include_archived:
            filters.append("archived_at=''")
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT ?"
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 200
        params.append(max(1, min(limit_value, 1000)))
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        maps = [_decode_mind_map_row(row, cols) for row in cursor.fetchall()]
        if include_counts:
            for item in maps:
                item.update(self._mind_map_counts(item.get("id", "")))
        return maps

    def create_mind_map(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        title = str(row.get("title", "") or "").strip()
        group = str(row.get("group", row.get("group_name", "")) or "").strip()
        if not title:
            raise ValueError("title is required")
        if not group:
            raise ValueError("group is required")
        map_id = str(row.get("id", "") or "").strip()
        if not map_id:
            map_id = self.next_mind_map_id(group)
        if self.load_mind_map(map_id):
            raise ValueError(f"mind map already exists: {map_id}")
        now = datetime.now(timezone.utc).isoformat()
        created_by_kind = _normalize_actor_kind(row.get("created_by_kind", "user"))
        slug = str(row.get("slug", "") or "").strip()
        if not slug:
            slug = self._thinking_slug_for_group(
                "thinking_mind_maps", group, title, map_id,
                default="mind-map",
            )
        values = {
            "id": map_id,
            "slug": slug,
            "group_name": group,
            "title": title,
            "description": str(row.get("description", "") or ""),
            "metadata_json": _json_payload_text(
                row.get("metadata", row.get("metadata_json", {})), {}
            ),
            "created_by_kind": created_by_kind,
            "created_by_id": str(row.get("created_by_id", "") or "").strip(),
            "updated_by_kind": _normalize_actor_kind(
                row.get("updated_by_kind", created_by_kind),
                default=created_by_kind,
            ),
            "updated_by_id": str(
                row.get("updated_by_id", row.get("created_by_id", "")) or ""
            ).strip(),
            "archived_by_kind": "",
            "archived_by_id": "",
            "deleted_by_kind": "",
            "deleted_by_id": "",
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "archived_at": str(row.get("archived_at", "") or ""),
            "deleted_at": str(row.get("deleted_at", "") or ""),
        }
        self._conn.execute(
            "INSERT INTO thinking_mind_maps ("
            + ", ".join(THINKING_MIND_MAP_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(THINKING_MIND_MAP_COLUMNS))
            + ")",
            tuple(values[col] for col in THINKING_MIND_MAP_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_mind_map(map_id, include_counts=True)
        if not saved:
            raise RuntimeError(f"failed to load saved mind map {map_id}")
        return saved

    async def create_mind_map_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "thinking", "create_mind_map", _snapshot_db_payload(row_dict or {})
        )

    def update_mind_map(self, map_id: str, patch: dict) -> dict | None:
        map_id = str(map_id or "").strip()
        existing = self.load_mind_map(map_id)
        if not existing:
            return None
        patch = dict(patch or {})
        values = {}
        for key in (
            "title", "description", "slug", "updated_by_kind", "updated_by_id"
        ):
            if key in patch:
                values[key] = patch[key]
        if "title" in values:
            values["title"] = str(values["title"] or "").strip()
            if not values["title"]:
                raise ValueError("title is required")
            if "slug" not in patch:
                values["slug"] = self._thinking_slug_for_group(
                    "thinking_mind_maps",
                    existing["group_name"],
                    values["title"],
                    map_id,
                    default="mind-map",
                )
        if "slug" in values:
            values["slug"] = str(values["slug"] or "").strip()
        if "description" in values:
            values["description"] = str(values["description"] or "")
        if "metadata" in patch or "metadata_json" in patch:
            values["metadata_json"] = _json_payload_text(
                patch.get("metadata", patch.get("metadata_json", {})), {}
            )
        if "updated_by_kind" in values:
            values["updated_by_kind"] = _normalize_actor_kind(values["updated_by_kind"])
        if "updated_by_id" in values:
            values["updated_by_id"] = str(values["updated_by_id"] or "").strip()
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE thinking_mind_maps SET {assignments} WHERE id=?",
            tuple(values.values()) + (map_id,),
        )
        self._conn.commit()
        return self.load_mind_map(map_id, include_counts=True)

    async def update_mind_map_async(self, map_id: str,
                                    patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "update_mind_map", str(map_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def archive_mind_map(self, map_id: str, *,
                         archived_by_kind: str = "user",
                         archived_by_id: str = "",
                         archived_at: str = "") -> dict | None:
        existing = self.load_mind_map(map_id)
        if not existing:
            return None
        timestamp = str(archived_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(archived_by_kind)
        actor_id = str(archived_by_id or "").strip()
        self._conn.execute(
            "UPDATE thinking_mind_maps SET archived_at=?, archived_by_kind=?, "
            "archived_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(map_id or "").strip()),
        )
        self._conn.commit()
        return self.load_mind_map(map_id, include_counts=True)

    async def archive_mind_map_async(self, map_id: str, **kwargs
                                     ) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "archive_mind_map", str(map_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def delete_mind_map(self, map_id: str, *,
                        deleted_by_kind: str = "user",
                        deleted_by_id: str = "",
                        deleted_at: str = "") -> dict | None:
        existing = self.load_mind_map(map_id)
        if not existing:
            return None
        timestamp = str(deleted_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(deleted_by_kind)
        actor_id = str(deleted_by_id or "").strip()
        self._conn.execute(
            "UPDATE thinking_mind_maps SET deleted_at=?, deleted_by_kind=?, "
            "deleted_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(map_id or "").strip()),
        )
        self._conn.execute(
            "UPDATE thinking_mind_map_nodes SET deleted_at=?, deleted_by_kind=?, "
            "deleted_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE map_id=? AND deleted_at=''",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(map_id or "").strip()),
        )
        self._conn.execute(
            "UPDATE thinking_mind_map_links SET deleted_at=?, deleted_by_kind=?, "
            "deleted_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE map_id=? AND deleted_at=''",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(map_id or "").strip()),
        )
        self._conn.commit()
        return self.load_mind_map(map_id, include_counts=True)

    async def delete_mind_map_async(self, map_id: str, **kwargs
                                    ) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "delete_mind_map", str(map_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def load_mind_map_node(self, node_id: str) -> dict | None:
        node_id = str(node_id or "").strip()
        if not node_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(THINKING_MIND_MAP_NODE_COLUMNS)
            + " FROM thinking_mind_map_nodes WHERE id=?",
            (node_id,),
        )
        row = cursor.fetchone()
        return _decode_mind_map_node_row(row, [d[0] for d in cursor.description])

    def list_mind_map_nodes(self, map_id: str, *,
                            include_deleted: bool = False) -> list[dict]:
        query = (
            "SELECT " + ", ".join(THINKING_MIND_MAP_NODE_COLUMNS)
            + " FROM thinking_mind_map_nodes WHERE map_id=?"
        )
        params = [str(map_id or "").strip()]
        if not include_deleted:
            query += " AND deleted_at=''"
        query += " ORDER BY sort_order ASC, id ASC"
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_mind_map_node_row(row, cols) for row in cursor.fetchall()]

    def create_mind_map_node(self, map_id: str, row_dict: dict) -> dict:
        map_id = str(map_id or "").strip()
        mind_map = self.load_mind_map(map_id)
        if not mind_map or mind_map.get("deleted"):
            raise ValueError("mind map not found")
        if mind_map.get("archived"):
            raise ValueError("mind map is archived")
        row = dict(row_dict or {})
        label = str(row.get("label", row.get("title", "")) or "").strip()
        title = str(row.get("title", "") or "").strip()
        if not label and not title:
            raise ValueError("label is required")
        if not label:
            label = title
        node_id = str(row.get("id", "") or "").strip()
        if not node_id:
            node_id = self._next_mind_map_item_id(map_id, "node")
        if self.load_mind_map_node(node_id):
            raise ValueError(f"mind map node already exists: {node_id}")
        now = datetime.now(timezone.utc).isoformat()
        position = _json_payload(row.get("position", row.get("position_json", {})), {})
        x_value = (
            row.get("x")
            if "x" in row and row.get("x") is not None
            else position.get("x", 0)
        )
        y_value = (
            row.get("y")
            if "y" in row and row.get("y") is not None
            else position.get("y", 0)
        )
        try:
            x = float(x_value or 0)
        except (TypeError, ValueError):
            x = 0.0
        try:
            y = float(y_value or 0)
        except (TypeError, ValueError):
            y = 0.0
        position["x"] = x
        position["y"] = y
        created_by_kind = _normalize_actor_kind(row.get("created_by_kind", "user"))
        sort_order_raw = row.get("sort_order", None)
        if sort_order_raw is None:
            sort_order_raw = self._mind_map_sort_order(
                "thinking_mind_map_nodes", map_id
            )
        values = {
            "id": node_id,
            "map_id": map_id,
            "label": label,
            "title": title,
            "notes": str(row.get("notes", "") or ""),
            "node_type": str(row.get("node_type", row.get("type", "")) or "").strip(),
            "tags_json": _json_payload_text(
                row.get("tags", row.get("tags_json", [])), []
            ),
            "color": str(row.get("color", "") or "").strip(),
            "x": x,
            "y": y,
            "position_json": _json_payload_text(position, {}),
            "sort_order": int(sort_order_raw or 0),
            "created_by_kind": created_by_kind,
            "created_by_id": str(row.get("created_by_id", "") or "").strip(),
            "updated_by_kind": _normalize_actor_kind(
                row.get("updated_by_kind", created_by_kind),
                default=created_by_kind,
            ),
            "updated_by_id": str(
                row.get("updated_by_id", row.get("created_by_id", "")) or ""
            ).strip(),
            "deleted_by_kind": "",
            "deleted_by_id": "",
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "deleted_at": "",
        }
        self._conn.execute(
            "INSERT INTO thinking_mind_map_nodes ("
            + ", ".join(THINKING_MIND_MAP_NODE_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(THINKING_MIND_MAP_NODE_COLUMNS))
            + ")",
            tuple(values[col] for col in THINKING_MIND_MAP_NODE_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_mind_map_node(node_id)
        if not saved:
            raise RuntimeError(f"failed to load saved mind map node {node_id}")
        return saved

    async def create_mind_map_node_async(self, map_id: str,
                                         row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "thinking", "create_mind_map_node", str(map_id or ""),
            _snapshot_db_payload(row_dict or {}),
        )

    def update_mind_map_node(self, node_id: str, patch: dict) -> dict | None:
        node_id = str(node_id or "").strip()
        node = self.load_mind_map_node(node_id)
        if not node:
            return None
        patch = dict(patch or {})
        values = {}
        for key in (
            "label", "title", "notes", "node_type", "color", "x", "y",
            "sort_order", "updated_by_kind", "updated_by_id",
        ):
            if key in patch:
                values[key] = patch[key]
        if "type" in patch and "node_type" not in values:
            values["node_type"] = patch["type"]
        effective_label = str(values.get("label", node.get("label", "")) or "").strip()
        effective_title = str(values.get("title", node.get("title", "")) or "").strip()
        if ("label" in values or "title" in values) and not effective_label and not effective_title:
            raise ValueError("label is required")
        for key in ("label", "title", "node_type", "color", "updated_by_id"):
            if key in values:
                values[key] = str(values[key] or "").strip()
        if "notes" in values:
            values["notes"] = str(values["notes"] or "")
        if "x" in values:
            values["x"] = float(values["x"] or 0)
        if "y" in values:
            values["y"] = float(values["y"] or 0)
        if "sort_order" in values:
            values["sort_order"] = int(values["sort_order"] or 0)
        if "tags" in patch or "tags_json" in patch:
            values["tags_json"] = _json_payload_text(
                patch.get("tags", patch.get("tags_json", [])), []
            )
        if "position" in patch or "position_json" in patch or "x" in values or "y" in values:
            position = _json_payload(
                patch.get("position", patch.get("position_json", node.get("position", {}))),
                {},
            )
            if "x" not in values and "x" in position:
                values["x"] = float(position.get("x") or 0)
            if "y" not in values and "y" in position:
                values["y"] = float(position.get("y") or 0)
            effective_x = values.get("x", node.get("x", 0))
            effective_y = values.get("y", node.get("y", 0))
            position["x"] = effective_x
            position["y"] = effective_y
            values["position_json"] = _json_payload_text(position, {})
        if "updated_by_kind" in values:
            values["updated_by_kind"] = _normalize_actor_kind(values["updated_by_kind"])
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE thinking_mind_map_nodes SET {assignments} WHERE id=?",
            tuple(values.values()) + (node_id,),
        )
        self._conn.commit()
        return self.load_mind_map_node(node_id)

    async def update_mind_map_node_async(self, node_id: str,
                                         patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "update_mind_map_node", str(node_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def delete_mind_map_node(self, node_id: str, *,
                             deleted_by_kind: str = "user",
                             deleted_by_id: str = "",
                             deleted_at: str = "") -> dict | None:
        node = self.load_mind_map_node(node_id)
        if not node:
            return None
        timestamp = str(deleted_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(deleted_by_kind)
        actor_id = str(deleted_by_id or "").strip()
        self._conn.execute(
            "UPDATE thinking_mind_map_nodes SET deleted_at=?, "
            "deleted_by_kind=?, deleted_by_id=?, updated_at=?, "
            "updated_by_kind=?, updated_by_id=? WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(node_id or "").strip()),
        )
        self._conn.execute(
            "UPDATE thinking_mind_map_links SET deleted_at=?, "
            "deleted_by_kind=?, deleted_by_id=?, updated_at=?, "
            "updated_by_kind=?, updated_by_id=? "
            "WHERE map_id=? AND deleted_at='' "
            "AND (source_node_id=? OR target_node_id=?)",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             node["map_id"], str(node_id or "").strip(), str(node_id or "").strip()),
        )
        self._conn.commit()
        return self.load_mind_map_node(node_id)

    async def delete_mind_map_node_async(self, node_id: str, **kwargs
                                         ) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "delete_mind_map_node", str(node_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def reorder_mind_map_nodes(self, map_id: str, node_order: list,
                               *, updated_by_kind: str = "user",
                               updated_by_id: str = "") -> list[dict]:
        return self._reorder_mind_map_items(
            "thinking_mind_map_nodes",
            THINKING_MIND_MAP_NODE_COLUMNS,
            _decode_mind_map_node_row,
            map_id,
            node_order,
            updated_by_kind=updated_by_kind,
            updated_by_id=updated_by_id,
        )

    async def reorder_mind_map_nodes_async(self, map_id: str, node_order: list,
                                           **kwargs) -> list[dict]:
        return await self._enqueue_async_write(
            "thinking", "reorder_mind_map_nodes", str(map_id or ""),
            _snapshot_db_payload(node_order or []),
            **_snapshot_db_payload(kwargs or {}),
        )

    def load_mind_map_link(self, link_id: str) -> dict | None:
        link_id = str(link_id or "").strip()
        if not link_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(THINKING_MIND_MAP_LINK_COLUMNS)
            + " FROM thinking_mind_map_links WHERE id=?",
            (link_id,),
        )
        row = cursor.fetchone()
        return _decode_mind_map_link_row(row, [d[0] for d in cursor.description])

    def list_mind_map_links(self, map_id: str, *,
                            include_deleted: bool = False) -> list[dict]:
        query = (
            "SELECT " + ", ".join(THINKING_MIND_MAP_LINK_COLUMNS)
            + " FROM thinking_mind_map_links WHERE map_id=?"
        )
        params = [str(map_id or "").strip()]
        if not include_deleted:
            query += " AND deleted_at=''"
        query += " ORDER BY sort_order ASC, id ASC"
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_mind_map_link_row(row, cols) for row in cursor.fetchall()]

    def create_mind_map_link(self, map_id: str, row_dict: dict) -> dict:
        map_id = str(map_id or "").strip()
        mind_map = self.load_mind_map(map_id)
        if not mind_map or mind_map.get("deleted"):
            raise ValueError("mind map not found")
        if mind_map.get("archived"):
            raise ValueError("mind map is archived")
        row = dict(row_dict or {})
        source_id = str(
            row.get("source_node_id", row.get("source", "")) or ""
        ).strip()
        target_id = str(
            row.get("target_node_id", row.get("target", "")) or ""
        ).strip()
        if not source_id:
            raise ValueError("source_node_id is required")
        if not target_id:
            raise ValueError("target_node_id is required")
        if source_id == target_id:
            raise ValueError("source and target nodes must differ")
        source = self.load_mind_map_node(source_id)
        target = self.load_mind_map_node(target_id)
        if (
                not source or source.get("deleted")
                or str(source.get("map_id", "") or "") != map_id):
            raise ValueError("source node not found")
        if (
                not target or target.get("deleted")
                or str(target.get("map_id", "") or "") != map_id):
            raise ValueError("target node not found")
        link_id = str(row.get("id", "") or "").strip()
        if not link_id:
            link_id = self._next_mind_map_item_id(map_id, "link")
        if self.load_mind_map_link(link_id):
            raise ValueError(f"mind map link already exists: {link_id}")
        now = datetime.now(timezone.utc).isoformat()
        created_by_kind = _normalize_actor_kind(row.get("created_by_kind", "user"))
        sort_order_raw = row.get("sort_order", None)
        if sort_order_raw is None:
            sort_order_raw = self._mind_map_sort_order(
                "thinking_mind_map_links", map_id
            )
        values = {
            "id": link_id,
            "map_id": map_id,
            "source_node_id": source_id,
            "target_node_id": target_id,
            "label": str(row.get("label", "") or ""),
            "link_type": str(row.get("link_type", row.get("type", "")) or "").strip(),
            "sort_order": int(sort_order_raw or 0),
            "created_by_kind": created_by_kind,
            "created_by_id": str(row.get("created_by_id", "") or "").strip(),
            "updated_by_kind": _normalize_actor_kind(
                row.get("updated_by_kind", created_by_kind),
                default=created_by_kind,
            ),
            "updated_by_id": str(
                row.get("updated_by_id", row.get("created_by_id", "")) or ""
            ).strip(),
            "deleted_by_kind": "",
            "deleted_by_id": "",
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "deleted_at": "",
        }
        self._conn.execute(
            "INSERT INTO thinking_mind_map_links ("
            + ", ".join(THINKING_MIND_MAP_LINK_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(THINKING_MIND_MAP_LINK_COLUMNS))
            + ")",
            tuple(values[col] for col in THINKING_MIND_MAP_LINK_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_mind_map_link(link_id)
        if not saved:
            raise RuntimeError(f"failed to load saved mind map link {link_id}")
        return saved

    async def create_mind_map_link_async(self, map_id: str,
                                         row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "thinking", "create_mind_map_link", str(map_id or ""),
            _snapshot_db_payload(row_dict or {}),
        )

    def update_mind_map_link(self, link_id: str, patch: dict) -> dict | None:
        link_id = str(link_id or "").strip()
        link = self.load_mind_map_link(link_id)
        if not link:
            return None
        patch = dict(patch or {})
        values = {}
        for key in (
            "label", "link_type", "sort_order", "updated_by_kind",
            "updated_by_id",
        ):
            if key in patch:
                values[key] = patch[key]
        if "type" in patch and "link_type" not in values:
            values["link_type"] = patch["type"]
        if "source_node_id" in patch or "source" in patch:
            source_id = str(patch.get("source_node_id", patch.get("source", "")) or "").strip()
            source = self.load_mind_map_node(source_id)
            if (
                    not source or source.get("deleted")
                    or str(source.get("map_id", "") or "") != link["map_id"]):
                raise ValueError("source node not found")
            values["source_node_id"] = source_id
        if "target_node_id" in patch or "target" in patch:
            target_id = str(patch.get("target_node_id", patch.get("target", "")) or "").strip()
            target = self.load_mind_map_node(target_id)
            if (
                    not target or target.get("deleted")
                    or str(target.get("map_id", "") or "") != link["map_id"]):
                raise ValueError("target node not found")
            values["target_node_id"] = target_id
        source_id = str(values.get("source_node_id", link["source_node_id"]) or "")
        target_id = str(values.get("target_node_id", link["target_node_id"]) or "")
        if source_id == target_id:
            raise ValueError("source and target nodes must differ")
        for key in ("label", "link_type", "updated_by_id"):
            if key in values:
                values[key] = str(values[key] or "").strip() if key != "label" else str(values[key] or "")
        if "sort_order" in values:
            values["sort_order"] = int(values["sort_order"] or 0)
        if "updated_by_kind" in values:
            values["updated_by_kind"] = _normalize_actor_kind(values["updated_by_kind"])
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE thinking_mind_map_links SET {assignments} WHERE id=?",
            tuple(values.values()) + (link_id,),
        )
        self._conn.commit()
        return self.load_mind_map_link(link_id)

    async def update_mind_map_link_async(self, link_id: str,
                                         patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "update_mind_map_link", str(link_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def delete_mind_map_link(self, link_id: str, *,
                             deleted_by_kind: str = "user",
                             deleted_by_id: str = "",
                             deleted_at: str = "") -> dict | None:
        link = self.load_mind_map_link(link_id)
        if not link:
            return None
        timestamp = str(deleted_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(deleted_by_kind)
        actor_id = str(deleted_by_id or "").strip()
        self._conn.execute(
            "UPDATE thinking_mind_map_links SET deleted_at=?, "
            "deleted_by_kind=?, deleted_by_id=?, updated_at=?, "
            "updated_by_kind=?, updated_by_id=? WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             str(link_id or "").strip()),
        )
        self._conn.commit()
        return self.load_mind_map_link(link_id)

    async def delete_mind_map_link_async(self, link_id: str, **kwargs
                                         ) -> dict | None:
        return await self._enqueue_async_write(
            "thinking", "delete_mind_map_link", str(link_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def reorder_mind_map_links(self, map_id: str, link_order: list,
                               *, updated_by_kind: str = "user",
                               updated_by_id: str = "") -> list[dict]:
        return self._reorder_mind_map_items(
            "thinking_mind_map_links",
            THINKING_MIND_MAP_LINK_COLUMNS,
            _decode_mind_map_link_row,
            map_id,
            link_order,
            updated_by_kind=updated_by_kind,
            updated_by_id=updated_by_id,
        )

    async def reorder_mind_map_links_async(self, map_id: str, link_order: list,
                                           **kwargs) -> list[dict]:
        return await self._enqueue_async_write(
            "thinking", "reorder_mind_map_links", str(map_id or ""),
            _snapshot_db_payload(link_order or []),
            **_snapshot_db_payload(kwargs or {}),
        )

    def _reorder_mind_map_items(self, table: str, columns: tuple,
                                decoder, map_id: str, item_order: list,
                                *, updated_by_kind: str = "user",
                                updated_by_id: str = "") -> list[dict]:
        if table not in {"thinking_mind_map_nodes", "thinking_mind_map_links"}:
            raise ValueError("unsupported mind map item table")
        map_id = str(map_id or "").strip()
        if not self.load_mind_map(map_id):
            raise ValueError("mind map not found")
        if not isinstance(item_order, list):
            raise ValueError("item order must be a list")
        timestamp = datetime.now(timezone.utc).isoformat()
        actor_kind = _normalize_actor_kind(updated_by_kind)
        actor_id = str(updated_by_id or "").strip()
        changed_ids = []
        for index, item in enumerate(item_order, start=1):
            if isinstance(item, dict):
                item_id = str(item.get("id", "") or "").strip()
                order_value = item.get("sort_order", index)
            else:
                item_id = str(item or "").strip()
                order_value = index
            if not item_id:
                continue
            try:
                sort_order = int(order_value)
            except (TypeError, ValueError):
                sort_order = index
            cursor = self._conn.execute(
                f"UPDATE {table} SET sort_order=?, updated_at=?, "
                "updated_by_kind=?, updated_by_id=? "
                "WHERE id=? AND map_id=? AND deleted_at=''",
                (sort_order, timestamp, actor_kind, actor_id, item_id, map_id),
            )
            if cursor.rowcount:
                changed_ids.append(item_id)
        self._conn.commit()
        if not changed_ids:
            return []
        placeholders = ",".join(["?"] * len(changed_ids))
        cursor = self._conn.execute(
            "SELECT " + ", ".join(columns) + f" FROM {table} "
            f"WHERE id IN ({placeholders}) ORDER BY sort_order ASC, id ASC",
            tuple(changed_ids),
        )
        cols = [d[0] for d in cursor.description]
        return [decoder(row, cols) for row in cursor.fetchall()]

    def mind_map_payload(self, map_id: str, *,
                         include_archived: bool = False,
                         include_deleted: bool = False) -> dict | None:
        mind_map = self.load_mind_map(map_id, include_counts=True)
        if not mind_map:
            return None
        if mind_map.get("deleted") and not include_deleted:
            return None
        if mind_map.get("archived") and not include_archived:
            return None
        payload = dict(mind_map)
        payload["nodes"] = self.list_mind_map_nodes(
            map_id,
            include_deleted=include_deleted,
        )
        payload["links"] = self.list_mind_map_links(
            map_id,
            include_deleted=include_deleted,
        )
        payload["node_count"] = len([
            node for node in payload["nodes"] if not node.get("deleted")
        ])
        payload["link_count"] = len([
            link for link in payload["links"] if not link.get("deleted")
        ])
        return payload
