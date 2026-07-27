"""Durable, one-shot task-completion watch persistence."""
from __future__ import annotations
import json
import time

TASK_WATCH_COLUMNS = (
    "id", "requester_agent_id", "thread_id", "group_name", "task_ids",
    "created_at", "expires_at", "status", "fired_at", "cancelled_at",
    "dedupe_key", "outbox_state", "outbox_attempted_at", "updated_at",
)

def _decode_task_watch(row):
    if not row:
        return None
    item = dict(zip(TASK_WATCH_COLUMNS, row))
    try:
        item["task_ids"] = json.loads(item["task_ids"] or "[]")
    except (TypeError, json.JSONDecodeError):
        item["task_ids"] = []
    for key in ("created_at", "expires_at", "fired_at", "cancelled_at", "outbox_attempted_at", "updated_at"):
        try: item[key] = float(item.get(key) or 0)
        except (TypeError, ValueError): item[key] = 0.0
    return item

class TaskWatchPersistenceMixin:
    def save_task_watch(self, watch: dict) -> dict:
        payload = dict(watch or {})
        payload["task_ids"] = json.dumps(list(payload.get("task_ids") or []), separators=(",", ":"))
        values = tuple(payload.get(column, "") for column in TASK_WATCH_COLUMNS)
        columns = ", ".join(TASK_WATCH_COLUMNS)
        updates = ", ".join(f"{key}=excluded.{key}" for key in TASK_WATCH_COLUMNS if key not in {"id", "created_at"})
        self._conn.execute(f"INSERT INTO task_watches ({columns}) VALUES ({','.join('?' for _ in values)}) ON CONFLICT(id) DO UPDATE SET {updates}", values)
        self._conn.commit()
        return self.load_task_watch(payload.get("id", ""))

    def load_task_watch(self, watch_id: str):
        row = self._conn.execute(f"SELECT {', '.join(TASK_WATCH_COLUMNS)} FROM task_watches WHERE id=?", (str(watch_id or "").strip(),)).fetchone()
        return _decode_task_watch(row)

    def list_task_watches(self, *, requester_agent_id: str = "", status: str = "", limit: int = 100):
        clauses, params = [], []
        if requester_agent_id:
            clauses.append("requester_agent_id=?"); params.append(str(requester_agent_id))
        if status:
            clauses.append("status=?"); params.append(str(status))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(f"SELECT {', '.join(TASK_WATCH_COLUMNS)} FROM task_watches{where} ORDER BY created_at ASC, id ASC LIMIT ?", (*params, max(1, min(int(limit), 1000)))).fetchall()
        return [item for item in (_decode_task_watch(row) for row in rows) if item]

    def update_task_watch(self, watch_id: str, patch: dict, *, only_status: str = ""):
        allowed = set(TASK_WATCH_COLUMNS) - {"id", "requester_agent_id", "created_at"}
        items = []
        for key, value in dict(patch or {}).items():
            if key not in allowed: continue
            if key == "task_ids": value = json.dumps(list(value or []), separators=(",", ":"))
            items.append((key, value))
        if not items: return self.load_task_watch(watch_id)
        if "updated_at" not in dict(items): items.append(("updated_at", time.time()))
        sql = "UPDATE task_watches SET " + ", ".join(f"{key}=?" for key, _ in items) + " WHERE id=?"
        params = [value for _, value in items] + [str(watch_id or "").strip()]
        if only_status:
            sql += " AND status=?"; params.append(only_status)
        self._conn.execute(sql, tuple(params)); self._conn.commit()
        return self.load_task_watch(watch_id)

    def claim_task_watch_fired(self, watch_id: str, *, fired_at: float) -> dict | None:
        """Atomically transition one active watch to its terminal fired state."""
        cursor = self._conn.execute(
            "UPDATE task_watches SET status='fired', fired_at=?, outbox_state='pending', updated_at=? "
            "WHERE id=? AND status='active'",
            (fired_at, fired_at, str(watch_id or '').strip()),
        )
        self._conn.commit()
        if not cursor.rowcount:
            return None
        return self.load_task_watch(watch_id)

    def claim_task_watch_outbox(self, watch_id: str, *, attempted_at: float) -> bool:
        """Claim a pending outbox row so concurrent event paths cannot deliver twice."""
        cursor = self._conn.execute(
            "UPDATE task_watches SET outbox_state='sending', outbox_attempted_at=?, updated_at=? "
            "WHERE id=? AND status='fired' AND outbox_state='pending'",
            (attempted_at, attempted_at, str(watch_id or '').strip()),
        )
        self._conn.commit()
        return bool(cursor.rowcount)

    def reset_sending_task_watch_outboxes(self) -> int:
        cursor = self._conn.execute(
            "UPDATE task_watches SET outbox_state='pending', updated_at=? "
            "WHERE status='fired' AND outbox_state='sending'", (time.time(),)
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def claim_task_watch_cancelled(
        self,
        watch_id: str,
        *,
        cancelled_at: float,
    ) -> dict | None:
        """Atomically cancel one active watch and report only a real claim."""
        cursor = self._conn.execute(
            "UPDATE task_watches SET status='cancelled', cancelled_at=?, "
            "outbox_state='cancelled', updated_at=? "
            "WHERE id=? AND status='active'",
            (cancelled_at, cancelled_at, str(watch_id or '').strip()),
        )
        self._conn.commit()
        if not cursor.rowcount:
            return None
        return self.load_task_watch(watch_id)

    def terminate_task_watches_for_requester(
        self,
        requester_agent_id: str,
        *,
        cancelled_at: float,
    ) -> int:
        """Prevent pending delivery after a requester loses its scope."""
        cursor = self._conn.execute(
            "UPDATE task_watches SET status='cancelled', cancelled_at=?, "
            "outbox_state='cancelled', updated_at=? "
            "WHERE requester_agent_id=? AND status IN ('active', 'fired')",
            (cancelled_at, cancelled_at, str(requester_agent_id or '').strip()),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def terminate_task_watches_for_group(
        self,
        group_name: str,
        *,
        cancelled_at: float,
    ) -> int:
        """Terminate watches when a group is removed or renamed."""
        cursor = self._conn.execute(
            "UPDATE task_watches SET status='cancelled', cancelled_at=?, "
            "outbox_state='cancelled', updated_at=? "
            "WHERE group_name=? AND status IN ('active', 'fired')",
            (cancelled_at, cancelled_at, str(group_name or '').strip()),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)
