"""SQLite persistence for Planning Areas, links, and notes."""

from __future__ import annotations

from datetime import datetime, timezone

from ..task_ids import format_area_id, normalize_group_prefix
from .common import (
    normalize_actor_kind as _normalize_actor_kind,
    slugify as _slugify,
    snapshot_db_payload as _snapshot_db_payload,
    unique_value as _unique_value,
)


AREA_LIFECYCLES = {
    "planned",
    "experimental",
    "active_investment",
    "stable",
    "maintenance",
    "deprecated",
    "retired",
}
AREA_LINK_TYPES = {"initiative", "decision", "task", "area"}
AREA_AREA_RELATIONS = {"related", "depends_on", "supports"}
AREA_NOTE_TYPES = {"caveat", "tech_debt", "open_question", "follow_up", "invariant"}
AREA_COLUMNS = (
    "id",
    "slug",
    "group_name",
    "title",
    "area_type",
    "lifecycle",
    "summary",
    "user_purpose",
    "system_purpose",
    "in_scope",
    "out_of_scope",
    "owner_kind",
    "owner_id",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "archived_by_kind",
    "archived_by_id",
    "created_at",
    "updated_at",
    "archived_at",
)
AREA_LINK_COLUMNS = (
    "id",
    "area_id",
    "link_type",
    "target_id",
    "relation",
    "created_by_kind",
    "created_by_id",
    "created_at",
)
AREA_NOTE_COLUMNS = (
    "id",
    "area_id",
    "note_type",
    "title",
    "body",
    "target_type",
    "target_id",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "archived_by_kind",
    "archived_by_id",
    "created_at",
    "updated_at",
    "archived_at",
)


def _normalize_area_lifecycle(value: str) -> str:
    lifecycle = str(value or "").strip().lower() or "planned"
    if lifecycle not in AREA_LIFECYCLES:
        raise ValueError(
            "lifecycle must be one of: " + ", ".join(sorted(AREA_LIFECYCLES))
        )
    return lifecycle


def _normalize_area_link_type(value: str) -> str:
    link_type = str(value or "").strip().lower()
    if link_type not in AREA_LINK_TYPES:
        raise ValueError(
            "link_type must be one of: " + ", ".join(sorted(AREA_LINK_TYPES))
        )
    return link_type


def _normalize_area_relation(value: str, *, link_type: str) -> str:
    relation = str(value or "").strip().lower()
    if link_type != "area":
        return ""
    relation = relation or "related"
    if relation not in AREA_AREA_RELATIONS:
        raise ValueError(
            "relation must be one of: " + ", ".join(sorted(AREA_AREA_RELATIONS))
        )
    return relation


def _normalize_area_note_type(value: str) -> str:
    note_type = str(value or "").strip().lower()
    if note_type not in AREA_NOTE_TYPES:
        raise ValueError(
            "note_type must be one of: " + ", ".join(sorted(AREA_NOTE_TYPES))
        )
    return note_type


def _decode_area_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or AREA_COLUMNS, row))
    item["group"] = item.get("group_name", "")
    item["archived"] = bool(str(item.get("archived_at", "") or "").strip())
    return item


def _decode_area_link_row(row, cols=None) -> dict | None:
    if not row:
        return None
    return dict(zip(cols or AREA_LINK_COLUMNS, row))


def _decode_area_note_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or AREA_NOTE_COLUMNS, row))
    item["archived"] = bool(str(item.get("archived_at", "") or "").strip())
    return item


class AreaPersistenceMixin:
    """TorqueDB API for Planning Area persistence."""

    def next_area_id(self, group_name: str) -> str:
        """Allocate the next durable Planning Area ID for a group."""
        group_prefix = normalize_group_prefix(group_name)
        row = self._conn.execute(
            "SELECT next_area_number FROM area_id_counters WHERE group_prefix=?",
            (group_prefix,),
        ).fetchone()
        number = int(row[0] if row else 1)
        self._conn.execute(
            "INSERT OR REPLACE INTO area_id_counters "
            "(group_prefix, next_area_number) VALUES (?, ?)",
            (group_prefix, number + 1),
        )
        self._conn.commit()
        return format_area_id(group_prefix, number)

    def _area_slug_for_group(self, group_name: str, title: str,
                             existing_id: str = "") -> str:
        base = _slugify(title or "area")
        existing_id = str(existing_id or "").strip()
        rows = self._conn.execute(
            "SELECT id, slug FROM planning_areas WHERE group_name=?",
            (str(group_name or "").strip(),),
        ).fetchall()
        existing = {
            str(slug or "")
            for area_id, slug in rows
            if str(slug or "") and str(area_id or "") != existing_id
        }
        return _unique_value(base, existing)

    def load_area(self, area_id: str) -> dict | None:
        area_id = str(area_id or "").strip()
        if not area_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(AREA_COLUMNS)
            + " FROM planning_areas WHERE id=?",
            (area_id,),
        )
        row = cursor.fetchone()
        return _decode_area_row(row, [d[0] for d in cursor.description])

    def list_areas(self, *, group: str = "",
                   include_archived: bool = False,
                   limit: int = 100) -> list[dict]:
        query = "SELECT " + ", ".join(AREA_COLUMNS) + " FROM planning_areas"
        filters = []
        params = []
        group = str(group or "").strip()
        if group:
            filters.append("group_name=?")
            params.append(group)
        if not include_archived:
            filters.append("archived_at=''")
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC"
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 100
        limit_value = max(1, min(limit_value, 500))
        query += " LIMIT ?"
        params.append(limit_value)
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_area_row(row, cols) for row in cursor.fetchall()]

    def create_area(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        title = str(row.get("title", "") or "").strip()
        group = str(row.get("group", row.get("group_name", "")) or "").strip()
        if not title:
            raise ValueError("title is required")
        if not group:
            raise ValueError("group is required")
        area_id = str(row.get("id", "") or "").strip()
        if not area_id:
            area_id = self.next_area_id(group)
        if self.load_area(area_id):
            raise ValueError(f"area already exists: {area_id}")
        now = datetime.now(timezone.utc).isoformat()
        lifecycle = _normalize_area_lifecycle(row.get("lifecycle", "planned"))
        owner_kind = _normalize_actor_kind(row.get("owner_kind", "user"))
        created_by_kind = _normalize_actor_kind(
            row.get("created_by_kind", owner_kind)
        )
        slug = str(row.get("slug", "") or "").strip()
        if not slug:
            slug = self._area_slug_for_group(group, title, area_id)
        values = {
            "id": area_id,
            "slug": slug,
            "group_name": group,
            "title": title,
            "area_type": str(row.get("area_type", "") or ""),
            "lifecycle": lifecycle,
            "summary": str(row.get("summary", "") or ""),
            "user_purpose": str(row.get("user_purpose", "") or ""),
            "system_purpose": str(row.get("system_purpose", "") or ""),
            "in_scope": str(row.get("in_scope", "") or ""),
            "out_of_scope": str(row.get("out_of_scope", "") or ""),
            "owner_kind": owner_kind,
            "owner_id": str(row.get("owner_id", "") or "").strip(),
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
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "archived_at": str(row.get("archived_at", "") or ""),
        }
        self._conn.execute(
            "INSERT INTO planning_areas ("
            + ", ".join(AREA_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(AREA_COLUMNS))
            + ")",
            tuple(values[col] for col in AREA_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_area(area_id)
        if not saved:
            raise RuntimeError(f"failed to load saved area {area_id}")
        return saved

    def update_area(self, area_id: str, patch: dict) -> dict | None:
        area_id = str(area_id or "").strip()
        existing = self.load_area(area_id)
        if not existing:
            return None
        patch = dict(patch or {})
        mutable = {
            "title", "area_type", "lifecycle", "summary", "user_purpose",
            "system_purpose", "in_scope", "out_of_scope", "owner_kind",
            "owner_id", "updated_by_kind", "updated_by_id",
        }
        values = {key: patch[key] for key in mutable if key in patch}
        if "title" in values:
            values["title"] = str(values["title"] or "").strip()
            if not values["title"]:
                raise ValueError("title is required")
            if "slug" not in patch:
                values["slug"] = self._area_slug_for_group(
                    existing["group_name"], values["title"], area_id,
                )
        if "slug" in patch:
            values["slug"] = str(patch.get("slug", "") or "").strip()
        if "lifecycle" in values:
            values["lifecycle"] = _normalize_area_lifecycle(values["lifecycle"])
        for key in ("owner_kind", "updated_by_kind"):
            if key in values:
                values[key] = _normalize_actor_kind(values[key])
        for key in (
            "area_type", "summary", "user_purpose", "system_purpose",
            "in_scope", "out_of_scope", "owner_id", "updated_by_id",
        ):
            if key in values:
                values[key] = str(values[key] or "")
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE planning_areas SET {assignments} WHERE id=?",
            tuple(values.values()) + (area_id,),
        )
        self._conn.commit()
        return self.load_area(area_id)

    async def create_area_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "planning_areas", "create_area", _snapshot_db_payload(row_dict or {})
        )

    async def update_area_async(self, area_id: str,
                                patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "planning_areas", "update_area", str(area_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def archive_area(self, area_id: str, *, archived_by_kind: str = "user",
                     archived_by_id: str = "", archived_at: str = "") -> dict | None:
        area_id = str(area_id or "").strip()
        existing = self.load_area(area_id)
        if not existing:
            return None
        timestamp = str(archived_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(archived_by_kind)
        actor_id = str(archived_by_id or "").strip()
        self._conn.execute(
            "UPDATE planning_areas SET archived_at=?, archived_by_kind=?, "
            "archived_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             area_id),
        )
        self._conn.commit()
        return self.load_area(area_id)

    async def archive_area_async(self, area_id: str, **kwargs) -> dict | None:
        return await self._enqueue_async_write(
            "planning_areas", "archive_area", str(area_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def list_area_links(self, area_id: str, link_type: str = "") -> list[dict]:
        area_id = str(area_id or "").strip()
        if not area_id:
            return []
        query = (
            "SELECT " + ", ".join(AREA_LINK_COLUMNS)
            + " FROM planning_area_links WHERE area_id=?"
        )
        params = [area_id]
        link_type = str(link_type or "").strip().lower()
        if link_type:
            query += " AND link_type=?"
            params.append(link_type)
        query += " ORDER BY id ASC"
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_area_link_row(row, cols) for row in cursor.fetchall()]

    def save_area_link(self, area_id: str, link_type: str, target_id: str, *,
                       relation: str = "", created_by_kind: str = "",
                       created_by_id: str = "") -> dict:
        area_id = str(area_id or "").strip()
        link_type = _normalize_area_link_type(link_type)
        target_id = str(target_id or "").strip()
        relation = _normalize_area_relation(relation, link_type=link_type)
        if not self.load_area(area_id):
            raise ValueError("area not found")
        if not target_id:
            raise ValueError("target_id is required")
        if link_type == "area" and target_id == area_id:
            raise ValueError("area cannot link to itself")
        created_at = datetime.now(timezone.utc).isoformat()
        actor_kind = (
            _normalize_actor_kind(created_by_kind, default="user")
            if created_by_kind else ""
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO planning_area_links "
            "(area_id, link_type, target_id, relation, created_by_kind, "
            "created_by_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (area_id, link_type, target_id, relation, actor_kind,
             str(created_by_id or "").strip(), created_at),
        )
        self._conn.commit()
        cursor = self._conn.execute(
            "SELECT " + ", ".join(AREA_LINK_COLUMNS)
            + " FROM planning_area_links WHERE area_id=? AND link_type=? "
            "AND target_id=? AND relation=?",
            (area_id, link_type, target_id, relation),
        )
        row = cursor.fetchone()
        link = _decode_area_link_row(row, [d[0] for d in cursor.description])
        if not link:
            raise RuntimeError("failed to save area link")
        return link

    async def save_area_link_async(self, area_id: str, link_type: str,
                                   target_id: str, **kwargs) -> dict:
        return await self._enqueue_async_write(
            "planning_areas", "save_area_link", str(area_id or ""),
            str(link_type or ""), str(target_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def delete_area_link(self, area_id: str, link_type: str,
                         target_id: str, relation: str = "") -> bool:
        link_type = _normalize_area_link_type(link_type)
        relation = _normalize_area_relation(relation, link_type=link_type)
        cursor = self._conn.execute(
            "DELETE FROM planning_area_links WHERE area_id=? "
            "AND link_type=? AND target_id=? AND relation=?",
            (str(area_id or "").strip(), link_type,
             str(target_id or "").strip(), relation),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def delete_area_link_async(self, area_id: str, link_type: str,
                                     target_id: str, relation: str = "") -> bool:
        return await self._enqueue_async_write(
            "planning_areas", "delete_area_link", str(area_id or ""),
            str(link_type or ""), str(target_id or ""), str(relation or ""),
        )

    def load_area_note(self, note_id: int | str) -> dict | None:
        try:
            note_id_value = int(note_id)
        except (TypeError, ValueError):
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(AREA_NOTE_COLUMNS)
            + " FROM planning_area_notes WHERE id=?",
            (note_id_value,),
        )
        row = cursor.fetchone()
        return _decode_area_note_row(row, [d[0] for d in cursor.description])

    def list_area_notes(self, area_id: str, *, include_archived: bool = False,
                        limit: int = 50) -> list[dict]:
        area_id = str(area_id or "").strip()
        if not area_id:
            return []
        query = (
            "SELECT " + ", ".join(AREA_NOTE_COLUMNS)
            + " FROM planning_area_notes WHERE area_id=?"
        )
        params = [area_id]
        if not include_archived:
            query += " AND archived_at=''"
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 50
        params.append(max(1, min(limit_value, 200)))
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_area_note_row(row, cols) for row in cursor.fetchall()]

    def create_area_note(self, area_id: str, row_dict: dict) -> dict:
        area_id = str(area_id or "").strip()
        if not self.load_area(area_id):
            raise ValueError("area not found")
        row = dict(row_dict or {})
        note_type = _normalize_area_note_type(
            row.get("note_type", row.get("type", ""))
        )
        title = str(row.get("title", "") or "").strip()
        body = str(row.get("body", "") or "")
        if not title:
            raise ValueError("title is required")
        now = datetime.now(timezone.utc).isoformat()
        created_by_kind = (
            _normalize_actor_kind(row.get("created_by_kind", ""))
            if row.get("created_by_kind", "") else ""
        )
        values = {
            "area_id": area_id,
            "note_type": note_type,
            "title": title,
            "body": body,
            "target_type": str(row.get("target_type", "") or "").strip().lower(),
            "target_id": str(row.get("target_id", "") or "").strip(),
            "created_by_kind": created_by_kind,
            "created_by_id": str(row.get("created_by_id", "") or "").strip(),
            "updated_by_kind": _normalize_actor_kind(
                row.get("updated_by_kind", created_by_kind),
                default=created_by_kind or "user",
            ) if (row.get("updated_by_kind", "") or created_by_kind) else "",
            "updated_by_id": str(
                row.get("updated_by_id", row.get("created_by_id", "")) or ""
            ).strip(),
            "archived_by_kind": "",
            "archived_by_id": "",
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "archived_at": "",
        }
        cols = [col for col in AREA_NOTE_COLUMNS if col != "id"]
        cur = self._conn.execute(
            "INSERT INTO planning_area_notes (" + ", ".join(cols) + ") VALUES ("
            + ",".join(["?"] * len(cols)) + ")",
            tuple(values[col] for col in cols),
        )
        self._conn.commit()
        saved = self.load_area_note(cur.lastrowid)
        if not saved:
            raise RuntimeError("failed to save area note")
        return saved

    async def create_area_note_async(self, area_id: str, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "planning_areas", "create_area_note", str(area_id or ""),
            _snapshot_db_payload(row_dict or {}),
        )

    def update_area_note(self, note_id: int | str, patch: dict) -> dict | None:
        note = self.load_area_note(note_id)
        if not note:
            return None
        patch = dict(patch or {})
        mutable = {
            "note_type", "title", "body", "target_type", "target_id",
            "updated_by_kind", "updated_by_id",
        }
        values = {key: patch[key] for key in mutable if key in patch}
        if "note_type" in values:
            values["note_type"] = _normalize_area_note_type(values["note_type"])
        if "title" in values:
            values["title"] = str(values["title"] or "").strip()
            if not values["title"]:
                raise ValueError("title is required")
        for key in ("body", "target_type", "target_id", "updated_by_id"):
            if key in values:
                values[key] = str(values[key] or "").strip() if key != "body" else str(values[key] or "")
        if "updated_by_kind" in values:
            values["updated_by_kind"] = _normalize_actor_kind(values["updated_by_kind"])
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE planning_area_notes SET {assignments} WHERE id=?",
            tuple(values.values()) + (int(note["id"]),),
        )
        self._conn.commit()
        return self.load_area_note(note["id"])

    async def update_area_note_async(self, note_id: int | str,
                                     patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "planning_areas", "update_area_note", note_id,
            _snapshot_db_payload(patch or {}),
        )

    def archive_area_note(self, note_id: int | str, *, archived_by_kind: str = "user",
                          archived_by_id: str = "", archived_at: str = ""
                          ) -> dict | None:
        note = self.load_area_note(note_id)
        if not note:
            return None
        timestamp = str(archived_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(archived_by_kind)
        actor_id = str(archived_by_id or "").strip()
        self._conn.execute(
            "UPDATE planning_area_notes SET archived_at=?, archived_by_kind=?, "
            "archived_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             int(note["id"])),
        )
        self._conn.commit()
        return self.load_area_note(note["id"])

    async def archive_area_note_async(self, note_id: int | str, **kwargs
                                      ) -> dict | None:
        return await self._enqueue_async_write(
            "planning_areas", "archive_area_note", note_id,
            **_snapshot_db_payload(kwargs or {}),
        )
