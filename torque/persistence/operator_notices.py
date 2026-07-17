"""Durable operator Inbox persistence."""

from __future__ import annotations

import json
import time


OPERATOR_NOTICE_COLUMNS = (
    "id",
    "notice_type",
    "severity",
    "category",
    "title",
    "message",
    "source",
    "group_name",
    "agent_id",
    "task_id",
    "action_kind",
    "action_payload",
    "dedupe_key",
    "occurrence_count",
    "first_occurred_at",
    "last_occurred_at",
    "read_at",
    "resolved_at",
    "dismissed_at",
    "archived_at",
    "created_at",
    "updated_at",
)

_OPERATOR_NOTICE_MUTABLE_COLUMNS = frozenset(
    set(OPERATOR_NOTICE_COLUMNS) - {"id", "created_at"}
)


def _decode_operator_notice(row) -> dict | None:
    if not row:
        return None
    notice = dict(zip(OPERATOR_NOTICE_COLUMNS, row))
    try:
        notice["action_payload"] = json.loads(
            notice.get("action_payload") or "{}"
        )
    except (TypeError, json.JSONDecodeError):
        notice["action_payload"] = {}
    notice["occurrence_count"] = max(
        1,
        int(notice.get("occurrence_count") or 1),
    )
    for field in (
        "first_occurred_at",
        "last_occurred_at",
        "read_at",
        "resolved_at",
        "dismissed_at",
        "archived_at",
        "created_at",
        "updated_at",
    ):
        try:
            notice[field] = float(notice.get(field) or 0)
        except (TypeError, ValueError):
            notice[field] = 0.0
    return notice


def _operator_notice_storage_values(notice: dict) -> tuple:
    payload = dict(notice or {})
    action_payload = payload.get("action_payload")
    if not isinstance(action_payload, dict):
        action_payload = {}
    payload["action_payload"] = json.dumps(
        action_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return tuple(payload.get(column, "") for column in OPERATOR_NOTICE_COLUMNS)


class OperatorNoticePersistenceMixin:
    """SQLite operations for alerts and notifications shown in the Inbox."""

    def save_operator_notice(self, notice: dict) -> dict:
        values = _operator_notice_storage_values(notice)
        columns = ", ".join(OPERATOR_NOTICE_COLUMNS)
        placeholders = ", ".join("?" for _ in OPERATOR_NOTICE_COLUMNS)
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in OPERATOR_NOTICE_COLUMNS
            if column not in {"id", "created_at"}
        )
        self._conn.execute(
            f"INSERT INTO operator_notices ({columns}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )
        self._conn.commit()
        return self.load_operator_notice(str(notice.get("id", "") or ""))

    def load_operator_notice(self, notice_id: str) -> dict | None:
        row = self._conn.execute(
            f"SELECT {', '.join(OPERATOR_NOTICE_COLUMNS)} "
            "FROM operator_notices WHERE id=?",
            (str(notice_id or "").strip(),),
        ).fetchone()
        return _decode_operator_notice(row)

    def load_operator_notice_for_dedupe(
        self,
        dedupe_key: str,
    ) -> dict | None:
        key = str(dedupe_key or "").strip()
        if not key:
            return None
        row = self._conn.execute(
            f"SELECT {', '.join(OPERATOR_NOTICE_COLUMNS)} "
            "FROM operator_notices WHERE dedupe_key=? "
            "ORDER BY last_occurred_at DESC, created_at DESC LIMIT 1",
            (key,),
        ).fetchone()
        return _decode_operator_notice(row)

    def list_operator_notices(
        self,
        *,
        notice_type: str = "",
        include_archived: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        clauses = []
        params: list[object] = []
        notice_type = str(notice_type or "").strip()
        if notice_type:
            clauses.append("notice_type=?")
            params.append(notice_type)
        if not include_archived:
            clauses.append("archived_at=0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            limit = max(1, min(1000, int(limit)))
        except (TypeError, ValueError):
            limit = 200
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        rows = self._conn.execute(
            f"SELECT {', '.join(OPERATOR_NOTICE_COLUMNS)} "
            f"FROM operator_notices {where} "
            "ORDER BY last_occurred_at DESC, created_at DESC "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [
            notice
            for notice in (_decode_operator_notice(row) for row in rows)
            if notice is not None
        ]

    def update_operator_notice(
        self,
        notice_id: str,
        patch: dict,
    ) -> dict | None:
        notice_id = str(notice_id or "").strip()
        if not notice_id:
            return None
        updates = []
        params: list[object] = []
        for column, value in dict(patch or {}).items():
            if column not in _OPERATOR_NOTICE_MUTABLE_COLUMNS:
                continue
            if column == "action_payload":
                value = json.dumps(
                    value if isinstance(value, dict) else {},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            updates.append(f"{column}=?")
            params.append(value)
        if not updates:
            return self.load_operator_notice(notice_id)
        if "updated_at" not in patch:
            updates.append("updated_at=?")
            params.append(time.time())
        params.append(notice_id)
        self._conn.execute(
            f"UPDATE operator_notices SET {', '.join(updates)} WHERE id=?",
            tuple(params),
        )
        self._conn.commit()
        return self.load_operator_notice(notice_id)

    def mark_all_operator_notices_read(
        self,
        *,
        notice_type: str = "",
        read_at: float | None = None,
    ) -> int:
        read_at = float(read_at or time.time())
        clauses = ["archived_at=0", "read_at=0"]
        params: list[object] = [read_at, read_at]
        notice_type = str(notice_type or "").strip()
        if notice_type:
            clauses.append("notice_type=?")
            params.append(notice_type)
        cursor = self._conn.execute(
            "UPDATE operator_notices SET read_at=?, updated_at=? "
            f"WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def operator_notice_summary(self) -> dict:
        row = self._conn.execute(
            "SELECT "
            "SUM(CASE WHEN notice_type='alert' AND archived_at=0 "
            "AND resolved_at=0 AND dismissed_at=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN notice_type='alert' AND archived_at=0 "
            "AND read_at=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN notice_type='notification' AND archived_at=0 "
            "AND read_at=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN archived_at=0 THEN 1 ELSE 0 END) "
            "FROM operator_notices"
        ).fetchone() or (0, 0, 0, 0)
        open_alerts = int(row[0] or 0)
        unread_alerts = int(row[1] or 0)
        unread_notifications = int(row[2] or 0)
        return {
            "open_alerts": open_alerts,
            "unread_alerts": unread_alerts,
            "unread_notifications": unread_notifications,
            "unread_total": unread_alerts + unread_notifications,
            "active_total": int(row[3] or 0),
        }
