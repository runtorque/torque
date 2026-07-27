"""SQLite persistence for durable, one-shot operator reminders."""
from __future__ import annotations

import time


REMINDER_COLUMNS = (
    "id", "requester_id", "requester_agent_id", "thread_id", "target_agent_id",
    "group_name", "message", "created_at", "due_at", "terminal_at", "status",
    "cancelled_at", "delivered_at", "request_idempotency_key", "dedupe_key",
    "outbox_state", "attempt_count", "last_attempt_at", "last_error", "updated_at",
)


def _decode_reminder(row):
    if not row:
        return None
    item = dict(zip(REMINDER_COLUMNS, row))
    for key in ("created_at", "due_at", "terminal_at", "cancelled_at", "delivered_at",
                "last_attempt_at", "updated_at"):
        try:
            item[key] = float(item.get(key) or 0)
        except (TypeError, ValueError):
            item[key] = 0.0
    try:
        item["attempt_count"] = int(item.get("attempt_count") or 0)
    except (TypeError, ValueError):
        item["attempt_count"] = 0
    return item


class ReminderPersistenceMixin:
    def save_reminder(self, reminder: dict) -> dict:
        payload = dict(reminder or {})
        values = tuple(payload.get(column, "") for column in REMINDER_COLUMNS)
        columns = ", ".join(REMINDER_COLUMNS)
        updates = ", ".join(
            f"{key}=excluded.{key}" for key in REMINDER_COLUMNS
            if key not in {"id", "created_at"}
        )
        self._conn.execute(
            f"INSERT INTO reminders ({columns}) VALUES ({','.join('?' for _ in values)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", values)
        self._conn.commit()
        return self.load_reminder(payload.get("id", ""))

    def load_reminder(self, reminder_id: str):
        row = self._conn.execute(
            f"SELECT {', '.join(REMINDER_COLUMNS)} FROM reminders WHERE id=?",
            (str(reminder_id or "").strip(),)).fetchone()
        return _decode_reminder(row)

    def load_reminder_by_request_key(self, key: str):
        key = str(key or "").strip()
        if not key:
            return None
        row = self._conn.execute(
            f"SELECT {', '.join(REMINDER_COLUMNS)} FROM reminders "
            "WHERE request_idempotency_key=?", (key,)).fetchone()
        return _decode_reminder(row)

    def list_reminders(self, *, requester_id: str = "", status: str = "", limit: int = 100):
        clauses, params = [], []
        if requester_id:
            clauses.append("requester_id=?"); params.append(str(requester_id))
        if status:
            clauses.append("status=?"); params.append(str(status))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT {', '.join(REMINDER_COLUMNS)} FROM reminders{where} "
            "ORDER BY due_at ASC, id ASC LIMIT ?", (*params, max(1, min(int(limit), 1000))),
        ).fetchall()
        return [item for item in (_decode_reminder(row) for row in rows) if item]

    def iter_reminders(self, *, status: str = "", page_size: int = 500):
        """Keyset traversal means a busy requester cannot starve later rows."""
        after_due, after_id = -1.0, ""
        while True:
            clauses = ["(due_at>? OR (due_at=? AND id>?))"]
            params = [after_due, after_due, after_id]
            if status:
                clauses.insert(0, "status=?"); params.insert(0, str(status))
            rows = self._conn.execute(
                f"SELECT {', '.join(REMINDER_COLUMNS)} FROM reminders WHERE "
                + " AND ".join(clauses)
                + " ORDER BY due_at ASC, id ASC LIMIT ?",
                (*params, max(1, min(int(page_size), 1000))),
            ).fetchall()
            items = [item for item in (_decode_reminder(row) for row in rows) if item]
            if not items:
                return
            yield from items
            if len(items) < max(1, min(int(page_size), 1000)):
                return
            after_due, after_id = items[-1]["due_at"], items[-1]["id"]

    def claim_reminder_delivery(self, reminder_id: str, *, attempted_at: float):
        """Atomic final gate: cancel wins only before this update commits."""
        cursor = self._conn.execute(
            "UPDATE reminders SET status='delivering', outbox_state='claimed', "
            "attempt_count=attempt_count+1, last_attempt_at=?, updated_at=? "
            "WHERE id=? AND status='pending' AND due_at<=?",
            (attempted_at, attempted_at, str(reminder_id or "").strip(), attempted_at),
        )
        self._conn.commit()
        return bool(cursor.rowcount)

    def complete_reminder_delivery(self, reminder_id: str, *, delivered_at: float):
        cursor = self._conn.execute(
            "UPDATE reminders SET status='delivered', outbox_state='sent', delivered_at=?, "
            "terminal_at=?, updated_at=?, last_error='' WHERE id=? AND status='delivering'",
            (delivered_at, delivered_at, delivered_at, str(reminder_id or "").strip()),
        )
        self._conn.commit()
        return bool(cursor.rowcount)

    def reset_claimed_reminder_deliveries(self, *, now: float | None = None) -> int:
        ts = float(now if now is not None else time.time())
        cursor = self._conn.execute(
            "UPDATE reminders SET status='pending', outbox_state='pending', updated_at=? "
            "WHERE status='delivering'", (ts,))
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def cancel_reminder(self, reminder_id: str, *, requester_id: str, cancelled_at: float):
        cursor = self._conn.execute(
            "UPDATE reminders SET status='cancelled', outbox_state='cancelled', cancelled_at=?, "
            "terminal_at=?, updated_at=? WHERE id=? AND requester_id=? AND status='pending'",
            (cancelled_at, cancelled_at, cancelled_at, str(reminder_id or "").strip(),
             str(requester_id or "").strip()),
        )
        self._conn.commit()
        return bool(cursor.rowcount)

    def cancel_all_reminders(self, *, requester_id: str, cancelled_at: float) -> int:
        cursor = self._conn.execute(
            "UPDATE reminders SET status='cancelled', outbox_state='cancelled', cancelled_at=?, "
            "terminal_at=?, updated_at=? WHERE requester_id=? AND status='pending'",
            (cancelled_at, cancelled_at, cancelled_at, str(requester_id or "").strip()),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def prune_reminders(self, *, before: float) -> int:
        cursor = self._conn.execute(
            "DELETE FROM reminders WHERE status IN ('delivered','cancelled') "
            "AND terminal_at>0 AND terminal_at<?", (float(before),))
        self._conn.commit()
        return int(cursor.rowcount or 0)
