"""SQLite persistence for Scratchpad notes."""

from __future__ import annotations

from datetime import datetime, timezone

from ..task_ids import format_scratchpad_note_id, normalize_group_prefix
from .common import (
    json_payload as _json_payload,
    json_payload_text as _json_payload_text,
    normalize_actor_kind as _normalize_actor_kind,
    slugify as _slugify,
    snapshot_db_payload as _snapshot_db_payload,
    unique_value as _unique_value,
)


THINKING_SCRATCHPAD_NOTE_COLUMNS = (
    "id", "slug", "group_name", "title", "body", "context_json", "links_json",
    "created_by_kind", "created_by_id", "updated_by_kind", "updated_by_id",
    "archived_by_kind", "archived_by_id", "deleted_by_kind", "deleted_by_id",
    "created_at", "updated_at", "archived_at", "deleted_at",
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


class ThinkingPersistenceMixin:
    """TorqueDB API for Scratchpad persistence."""

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

    def _thinking_slug_for_group(self, table: str, group_name: str, title: str,
                                 existing_id: str = "",
                                 default: str = "thinking") -> str:
        if table != "thinking_scratchpad_notes":
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
