"""Durable operator Inbox semantics."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid

from ..config import log

NOTICE_TYPES = frozenset({"alert", "notification"})
NOTICE_SEVERITIES = frozenset({
    "info",
    "success",
    "warning",
    "error",
    "critical",
})
NOTICE_ACTION_KINDS = frozenset({
    "",
    "open_agent",
    "open_inbox",
    "open_panel",
    "open_settings",
    "open_task",
    "retry_board_sync",
})


def _notice_text(value, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _notice_type(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in NOTICE_TYPES else "notification"


def _notice_severity(value, notice_type: str) -> str:
    value = str(value or "").strip().lower()
    if value in NOTICE_SEVERITIES:
        return value
    return "error" if notice_type == "alert" else "info"


def _notice_action_kind(value) -> str:
    value = str(value or "").strip()
    return value if value in NOTICE_ACTION_KINDS else ""


def _notice_dedupe_key(
    *,
    notice_type: str,
    category: str,
    source: str,
    group_name: str,
    agent_id: str,
    task_id: str,
    title: str,
    message: str,
) -> str:
    payload = json.dumps(
        {
            "notice_type": notice_type,
            "category": category,
            "source": source,
            "group_name": group_name,
            "agent_id": agent_id,
            "task_id": task_id,
            "title": title,
            "message": message,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "auto:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class OperatorNoticeService:
    """Persist, deduplicate, and project operator-facing notices."""

    SNAPSHOT_LIMIT = 200

    def __init__(self, state):
        self._state = state
        self._snapshot: dict[str, dict] = {}
        self._summary = self._empty_summary()
        self._hydrate_snapshot()

    @property
    def _db(self):
        return getattr(self._state, "db", None)

    @staticmethod
    def _empty_summary() -> dict:
        return {
            "open_alerts": 0,
            "unread_alerts": 0,
            "unread_notifications": 0,
            "unread_total": 0,
            "active_total": 0,
        }

    def _hydrate_snapshot(self) -> None:
        db = self._db
        list_notices = getattr(db, "list_operator_notices", None)
        load_summary = getattr(db, "operator_notice_summary", None)
        if not callable(list_notices) or not callable(load_summary):
            return
        notices = list_notices(
            include_archived=True,
            limit=self.SNAPSHOT_LIMIT,
            offset=0,
        )
        self._snapshot = {
            notice["id"]: notice
            for notice in notices
            if notice and notice.get("id")
        }
        self._summary = dict(load_summary() or self._empty_summary())

    def _remember(self, notice: dict | None) -> None:
        if not notice or not notice.get("id"):
            return
        self._snapshot[notice["id"]] = notice
        ordered = sorted(
            self._snapshot.values(),
            key=lambda item: (
                float(item.get("last_occurred_at") or 0),
                float(item.get("created_at") or 0),
            ),
            reverse=True,
        )[:self.SNAPSHOT_LIMIT]
        self._snapshot = {item["id"]: item for item in ordered}

    def _refresh_summary(self) -> None:
        load_summary = getattr(self._db, "operator_notice_summary", None)
        if callable(load_summary):
            self._summary = dict(
                load_summary() or self._empty_summary()
            )

    def summary(self) -> dict:
        return dict(self._summary)

    def list(
        self,
        *,
        notice_type: str = "",
        include_archived: bool = True,
        limit: int = SNAPSHOT_LIMIT,
        offset: int = 0,
    ) -> list[dict]:
        if not self._db:
            return []
        normalized_type = str(notice_type or "").strip().lower()
        if normalized_type and normalized_type not in NOTICE_TYPES:
            normalized_type = ""
        return self._db.list_operator_notices(
            notice_type=normalized_type,
            include_archived=bool(include_archived),
            limit=limit,
            offset=offset,
        )

    def snapshot(self) -> dict[str, dict]:
        return {
            notice_id: dict(notice)
            for notice_id, notice in self._snapshot.items()
        }

    def _emit_notice(
        self,
        notice: dict | None,
        *,
        event: str = "update",
    ) -> None:
        if not notice:
            return
        self._state._emit(
            "operator_notice_upsert",
            notice=notice,
            event=str(event or "update"),
            summary=self.summary(),
        )

    def _emit_summary(self) -> None:
        self._state._emit(
            "operator_notice_summary",
            summary=self.summary(),
        )

    def _schedule_broadcast(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop.is_closed():
            return
        loop.create_task(self._state.broadcast())

    def publish(
        self,
        *,
        notice_type: str,
        title: str,
        message: str,
        severity: str = "",
        category: str = "general",
        source: str = "",
        group_name: str = "",
        agent_id: str = "",
        task_id: str = "",
        action_kind: str = "",
        action_payload: dict | None = None,
        dedupe_key: str = "",
        broadcast: bool = True,
    ) -> dict | None:
        if not self._db:
            return None
        notice_type = _notice_type(notice_type)
        severity = _notice_severity(severity, notice_type)
        title = _notice_text(title, limit=160)
        message = _notice_text(message, limit=4000)
        category = _notice_text(category, limit=80) or "general"
        source = _notice_text(source, limit=160)
        group_name = _notice_text(group_name, limit=160)
        agent_id = _notice_text(agent_id, limit=160)
        task_id = _notice_text(task_id, limit=160)
        action_kind = _notice_action_kind(action_kind)
        action_payload = (
            dict(action_payload)
            if isinstance(action_payload, dict)
            else {}
        )
        if not title:
            title = "Torque alert" if notice_type == "alert" else "Torque"
        if not message:
            message = title
        dedupe_key = _notice_text(dedupe_key, limit=240)
        if not dedupe_key:
            dedupe_key = _notice_dedupe_key(
                notice_type=notice_type,
                category=category,
                source=source,
                group_name=group_name,
                agent_id=agent_id,
                task_id=task_id,
                title=title,
                message=message,
            )

        now = time.time()
        existing = self._db.load_operator_notice_for_dedupe(dedupe_key)
        if existing:
            patch = {
                "notice_type": notice_type,
                "severity": severity,
                "category": category,
                "title": title,
                "message": message,
                "source": source,
                "group_name": group_name,
                "agent_id": agent_id,
                "task_id": task_id,
                "action_kind": action_kind,
                "action_payload": action_payload,
                "occurrence_count": int(
                    existing.get("occurrence_count") or 1
                ) + 1,
                "last_occurred_at": now,
                "read_at": 0,
                "dismissed_at": 0,
                "archived_at": 0,
                "updated_at": now,
            }
            if notice_type == "alert":
                patch["resolved_at"] = 0
            notice = self._db.update_operator_notice(
                existing["id"],
                patch,
            )
        else:
            notice_id = "notice-" + uuid.uuid4().hex
            notice = self._db.save_operator_notice({
                "id": notice_id,
                "notice_type": notice_type,
                "severity": severity,
                "category": category,
                "title": title,
                "message": message,
                "source": source,
                "group_name": group_name,
                "agent_id": agent_id,
                "task_id": task_id,
                "action_kind": action_kind,
                "action_payload": action_payload,
                "dedupe_key": dedupe_key,
                "occurrence_count": 1,
                "first_occurred_at": now,
                "last_occurred_at": now,
                "read_at": 0,
                "resolved_at": 0,
                "dismissed_at": 0,
                "archived_at": 0,
                "created_at": now,
                "updated_at": now,
            })
        self._remember(notice)
        self._refresh_summary()
        self._emit_notice(
            notice,
            event="recur" if existing else "publish",
        )
        if broadcast:
            self._schedule_broadcast()
        return notice

    def update_lifecycle(
        self,
        notice_id: str,
        action: str,
        *,
        broadcast: bool = True,
    ) -> dict | None:
        if not self._db:
            return None
        existing = self._db.load_operator_notice(notice_id)
        if not existing:
            return None
        now = time.time()
        patch: dict[str, object] = {"updated_at": now}
        if action == "read":
            patch["read_at"] = existing.get("read_at") or now
        elif action == "resolve":
            patch.update({
                "read_at": existing.get("read_at") or now,
                "resolved_at": now,
                "dismissed_at": 0,
            })
        elif action == "dismiss":
            patch.update({
                "read_at": existing.get("read_at") or now,
                "dismissed_at": now,
            })
        elif action == "archive":
            patch.update({
                "read_at": existing.get("read_at") or now,
                "archived_at": now,
            })
        elif action == "restore":
            patch["archived_at"] = 0
        else:
            raise ValueError(f"Unknown operator notice action: {action}")
        notice = self._db.update_operator_notice(notice_id, patch)
        self._remember(notice)
        self._refresh_summary()
        self._emit_notice(notice, event=action)
        if broadcast:
            self._schedule_broadcast()
        return notice

    def mark_all_read(
        self,
        *,
        notice_type: str = "",
        broadcast: bool = True,
    ) -> int:
        if not self._db:
            return 0
        normalized_type = str(notice_type or "").strip().lower()
        if normalized_type and normalized_type not in NOTICE_TYPES:
            normalized_type = ""
        count = self._db.mark_all_operator_notices_read(
            notice_type=normalized_type,
        )
        if count:
            read_at = time.time()
            for notice in self._snapshot.values():
                if notice.get("archived_at") or notice.get("read_at"):
                    continue
                if (
                    normalized_type
                    and notice.get("notice_type") != normalized_type
                ):
                    continue
                notice["read_at"] = read_at
                notice["updated_at"] = read_at
        self._refresh_summary()
        if count:
            self._state._emit(
                "operator_notices_read_all",
                notice_type=normalized_type,
                read_at=read_at,
                summary=self.summary(),
            )
            if broadcast:
                self._schedule_broadcast()
        else:
            self._emit_summary()
        return count

    def publish_best_effort(self, **kwargs) -> dict | None:
        try:
            return self.publish(**kwargs)
        except Exception:
            log.exception("Failed to publish operator notice")
            return None
