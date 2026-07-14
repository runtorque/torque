"""SQLite persistence for Initiatives and their links."""

from __future__ import annotations

from datetime import datetime, timezone

from ..task_ids import format_initiative_id, normalize_group_prefix
from .common import (
    normalize_actor_kind as _normalize_actor_kind,
    slugify as _slugify,
    snapshot_db_payload as _snapshot_db_payload,
    unique_value as _unique_value,
)


INITIATIVE_PLANNING_STATUSES = {
    "triage",
    "now",
    "next",
    "later",
    "parked",
    "shipped",
}
INITIATIVE_LINK_TYPES = {"task", "decision"}
INITIATIVE_COLUMNS = (
    "id",
    "slug",
    "group_name",
    "title",
    "summary",
    "why",
    "in_scope",
    "out_of_scope",
    "done_definition",
    "planning_status",
    "priority",
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
INITIATIVE_LINK_COLUMNS = (
    "id",
    "initiative_id",
    "link_type",
    "target_id",
    "created_by_kind",
    "created_by_id",
    "created_at",
)


def _normalize_initiative_status(value: str) -> str:
    status = str(value or "").strip().lower() or "triage"
    if status not in INITIATIVE_PLANNING_STATUSES:
        raise ValueError(
            "planning_status must be one of: "
            + ", ".join(sorted(INITIATIVE_PLANNING_STATUSES))
        )
    return status


def _decode_initiative_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or INITIATIVE_COLUMNS, row))
    item["group"] = item.get("group_name", "")
    item["archived"] = bool(str(item.get("archived_at", "") or "").strip())
    return item


def _decode_initiative_link_row(row, cols=None) -> dict | None:
    if not row:
        return None
    return dict(zip(cols or INITIATIVE_LINK_COLUMNS, row))


class InitiativePersistenceMixin:
    """TorqueDB API for Initiative persistence."""

    def next_initiative_id(self, group_name: str) -> str:
        """Allocate the next durable Initiative ID for a group."""
        group_prefix = normalize_group_prefix(group_name)
        row = self._conn.execute(
            "SELECT next_initiative_number FROM initiative_id_counters "
            "WHERE group_prefix=?",
            (group_prefix,),
        ).fetchone()
        number = int(row[0] if row else 1)
        self._conn.execute(
            "INSERT OR REPLACE INTO initiative_id_counters "
            "(group_prefix, next_initiative_number) VALUES (?, ?)",
            (group_prefix, number + 1),
        )
        self._conn.commit()
        return format_initiative_id(group_prefix, number)

    def _initiative_slug_for_group(self, group_name: str, title: str,
                                   existing_id: str = "") -> str:
        base = _slugify(title or "initiative")
        existing_id = str(existing_id or "").strip()
        rows = self._conn.execute(
            "SELECT id, slug FROM initiatives WHERE group_name=?",
            (str(group_name or "").strip(),),
        ).fetchall()
        existing = {
            str(slug or "")
            for initiative_id, slug in rows
            if str(slug or "") and str(initiative_id or "") != existing_id
        }
        return _unique_value(base, existing)

    def load_initiative(self, initiative_id: str) -> dict | None:
        initiative_id = str(initiative_id or "").strip()
        if not initiative_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(INITIATIVE_COLUMNS)
            + " FROM initiatives WHERE id=?",
            (initiative_id,),
        )
        row = cursor.fetchone()
        return _decode_initiative_row(row, [d[0] for d in cursor.description])

    def list_initiatives(self, *, group: str = "",
                         include_archived: bool = False) -> list[dict]:
        query = "SELECT " + ", ".join(INITIATIVE_COLUMNS) + " FROM initiatives"
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
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_initiative_row(row, cols) for row in cursor.fetchall()]

    def create_initiative(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        title = str(row.get("title", "") or "").strip()
        group = str(row.get("group", row.get("group_name", "")) or "").strip()
        if not title:
            raise ValueError("title is required")
        if not group:
            raise ValueError("group is required")
        initiative_id = str(row.get("id", "") or "").strip()
        if not initiative_id:
            initiative_id = self.next_initiative_id(group)
        if self.load_initiative(initiative_id):
            raise ValueError(f"initiative already exists: {initiative_id}")
        now = datetime.now(timezone.utc).isoformat()
        planning_status = _normalize_initiative_status(
            row.get("planning_status", "triage")
        )
        owner_kind = _normalize_actor_kind(row.get("owner_kind", "user"))
        created_by_kind = _normalize_actor_kind(
            row.get("created_by_kind", owner_kind)
        )
        slug = str(row.get("slug", "") or "").strip()
        if not slug:
            slug = self._initiative_slug_for_group(group, title, initiative_id)
        values = {
            "id": initiative_id,
            "slug": slug,
            "group_name": group,
            "title": title,
            "summary": str(row.get("summary", "") or ""),
            "why": str(row.get("why", "") or ""),
            "in_scope": str(row.get("in_scope", "") or ""),
            "out_of_scope": str(row.get("out_of_scope", "") or ""),
            "done_definition": str(row.get("done_definition", "") or ""),
            "planning_status": planning_status,
            "priority": str(row.get("priority", "") or ""),
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
            "INSERT INTO initiatives ("
            + ", ".join(INITIATIVE_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(INITIATIVE_COLUMNS))
            + ")",
            tuple(values[col] for col in INITIATIVE_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_initiative(initiative_id)
        if not saved:
            raise RuntimeError(f"failed to load saved initiative {initiative_id}")
        return saved

    def update_initiative(self, initiative_id: str, patch: dict) -> dict | None:
        initiative_id = str(initiative_id or "").strip()
        existing = self.load_initiative(initiative_id)
        if not existing:
            return None
        patch = dict(patch or {})
        mutable = {
            "title", "summary", "why", "in_scope", "out_of_scope",
            "done_definition", "planning_status", "priority", "owner_kind",
            "owner_id", "updated_by_kind", "updated_by_id",
        }
        values = {key: patch[key] for key in mutable if key in patch}
        if "title" in values:
            values["title"] = str(values["title"] or "").strip()
            if not values["title"]:
                raise ValueError("title is required")
            if "slug" not in patch:
                values["slug"] = self._initiative_slug_for_group(
                    existing["group_name"], values["title"], initiative_id,
                )
        if "slug" in patch:
            values["slug"] = str(patch.get("slug", "") or "").strip()
        if "planning_status" in values:
            values["planning_status"] = _normalize_initiative_status(
                values["planning_status"]
            )
        for key in ("owner_kind", "updated_by_kind"):
            if key in values:
                values[key] = _normalize_actor_kind(values[key])
        for key in (
            "summary", "why", "in_scope", "out_of_scope", "done_definition",
            "priority", "owner_id", "updated_by_id",
        ):
            if key in values:
                values[key] = str(values[key] or "")
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE initiatives SET {assignments} WHERE id=?",
            tuple(values.values()) + (initiative_id,),
        )
        self._conn.commit()
        return self.load_initiative(initiative_id)

    async def create_initiative_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "initiatives", "create_initiative", _snapshot_db_payload(row_dict or {})
        )

    async def update_initiative_async(self, initiative_id: str,
                                      patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "initiatives", "update_initiative", str(initiative_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def archive_initiative(self, initiative_id: str, *,
                           archived_by_kind: str = "user",
                           archived_by_id: str = "",
                           archived_at: str = "") -> dict | None:
        initiative_id = str(initiative_id or "").strip()
        existing = self.load_initiative(initiative_id)
        if not existing:
            return None
        timestamp = str(archived_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(archived_by_kind)
        actor_id = str(archived_by_id or "").strip()
        self._conn.execute(
            "UPDATE initiatives SET archived_at=?, archived_by_kind=?, "
            "archived_by_id=?, updated_at=?, updated_by_kind=?, updated_by_id=? "
            "WHERE id=?",
            (timestamp, actor_kind, actor_id, timestamp, actor_kind, actor_id,
             initiative_id),
        )
        self._conn.commit()
        return self.load_initiative(initiative_id)

    async def archive_initiative_async(self, initiative_id: str, **kwargs
                                       ) -> dict | None:
        return await self._enqueue_async_write(
            "initiatives", "archive_initiative", str(initiative_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def list_initiative_links(self, initiative_id: str,
                              link_type: str = "") -> list[dict]:
        initiative_id = str(initiative_id or "").strip()
        if not initiative_id:
            return []
        query = (
            "SELECT " + ", ".join(INITIATIVE_LINK_COLUMNS)
            + " FROM initiative_links WHERE initiative_id=?"
        )
        params = [initiative_id]
        link_type = str(link_type or "").strip()
        if link_type:
            query += " AND link_type=?"
            params.append(link_type)
        query += " ORDER BY id ASC"
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_initiative_link_row(row, cols) for row in cursor.fetchall()]

    def save_initiative_link(self, initiative_id: str, link_type: str,
                             target_id: str, *, created_by_kind: str = "",
                             created_by_id: str = "") -> dict:
        initiative_id = str(initiative_id or "").strip()
        link_type = str(link_type or "").strip()
        target_id = str(target_id or "").strip()
        if not self.load_initiative(initiative_id):
            raise ValueError("initiative not found")
        if link_type not in INITIATIVE_LINK_TYPES:
            raise ValueError("link_type must be one of: task, decision")
        if not target_id:
            raise ValueError("target_id is required")
        created_at = datetime.now(timezone.utc).isoformat()
        actor_kind = (
            _normalize_actor_kind(created_by_kind, default="user")
            if created_by_kind else ""
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO initiative_links "
            "(initiative_id, link_type, target_id, created_by_kind, "
            "created_by_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (initiative_id, link_type, target_id, actor_kind,
             str(created_by_id or "").strip(), created_at),
        )
        self._conn.commit()
        cursor = self._conn.execute(
            "SELECT " + ", ".join(INITIATIVE_LINK_COLUMNS)
            + " FROM initiative_links WHERE initiative_id=? "
            "AND link_type=? AND target_id=?",
            (initiative_id, link_type, target_id),
        )
        row = cursor.fetchone()
        link = _decode_initiative_link_row(row, [d[0] for d in cursor.description])
        if not link:
            raise RuntimeError("failed to save initiative link")
        return link

    async def save_initiative_link_async(self, initiative_id: str,
                                         link_type: str, target_id: str,
                                         **kwargs) -> dict:
        return await self._enqueue_async_write(
            "initiatives", "save_initiative_link", str(initiative_id or ""),
            str(link_type or ""), str(target_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def delete_initiative_link(self, initiative_id: str, link_type: str,
                               target_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM initiative_links WHERE initiative_id=? "
            "AND link_type=? AND target_id=?",
            (str(initiative_id or "").strip(), str(link_type or "").strip(),
             str(target_id or "").strip()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def delete_initiative_link_async(self, initiative_id: str,
                                           link_type: str,
                                           target_id: str) -> bool:
        return await self._enqueue_async_write(
            "initiatives", "delete_initiative_link", str(initiative_id or ""),
            str(link_type or ""), str(target_id or ""),
        )
