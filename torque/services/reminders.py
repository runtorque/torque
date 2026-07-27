"""User-owned, durable, non-executable one-shot reminders."""
from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timezone

from ..db import canonical_user_agent_thread_id

REMINDER_MAX_ACTIVE = 100
REMINDER_RETENTION_SECONDS = 30 * 24 * 60 * 60


class ReminderService:
    def __init__(self, state):
        self._state = state

    @property
    def _db(self):
        return getattr(self._state, "db", None)

    def _api(self, *names):
        return bool(self._db) and all(callable(getattr(self._db, name, None)) for name in names)

    @staticmethod
    def requester_id(_target) -> str:
        # User DMs are owned by the local operator, not by the recipient
        # agent. Keep that identity explicit even though there is one local UI
        # user today, so target-agent changes can never cross ownership.
        return "user"

    def create(self, *, target, delay_seconds: int, message: str,
               request_idempotency_key: str = "", now: float | None = None):
        if not self._api("save_reminder", "load_reminder", "list_reminders", "claim_reminder_delivery"):
            raise ValueError("Reminder store is unavailable")
        now = float(now if now is not None else time.time())
        requester = self.requester_id(target)
        key = str(request_idempotency_key or "").strip()
        existing = self._db.load_reminder_by_request_key(key) if key else None
        if existing:
            if (existing["requester_id"] != requester or existing["target_agent_id"] != target.id
                    or existing["message"] != message or existing["due_at"] != now + int(delay_seconds)):
                # Exact due clock naturally differs on a retry; ownership and
                # authored text are the non-negotiable idempotency contract.
                if (existing["requester_id"] != requester or existing["target_agent_id"] != target.id
                        or existing["message"] != message):
                    raise ValueError("idempotency key was reused for a different user_agent_message")
            return self._recover(existing, now=now)
        active = self._db.list_reminders(requester_id=requester, status="pending", limit=REMINDER_MAX_ACTIVE + 1)
        if len(active) >= REMINDER_MAX_ACTIVE:
            raise ValueError("At most 100 active reminders are allowed")
        reminder_id = "rem-" + uuid.uuid4().hex[:12]
        payload = {
            "id": reminder_id, "requester_id": requester, "requester_agent_id": target.id,
            "thread_id": canonical_user_agent_thread_id(target.id), "target_agent_id": target.id,
            "group_name": str(getattr(target, "group", "") or "").strip(), "message": message,
            "created_at": now, "due_at": now + int(delay_seconds), "terminal_at": 0,
            "status": "pending", "cancelled_at": 0, "delivered_at": 0,
            "request_idempotency_key": key, "dedupe_key": "reminder:" + reminder_id,
            "outbox_state": "pending", "attempt_count": 0, "last_attempt_at": 0,
            "last_error": "", "updated_at": now,
        }
        try:
            return self._db.save_reminder(payload)
        except sqlite3.IntegrityError:
            existing = self._db.load_reminder_by_request_key(key) if key else None
            if not existing:
                raise
            if (existing["requester_id"] != requester or existing["target_agent_id"] != target.id
                    or existing["message"] != message):
                raise ValueError("idempotency key was reused for a different user_agent_message")
            return self._recover(existing, now=now)

    def _recover(self, reminder, *, now: float):
        if reminder.get("status") in {"pending", "delivering"} and reminder.get("due_at", 0) <= now:
            self.reconcile(now=now)
        return self._db.load_reminder(reminder["id"]) or reminder

    def list_active(self, target, *, now=None):
        self.prune(now=now)
        if not self._api("list_reminders"):
            return []
        return self._db.list_reminders(requester_id=self.requester_id(target), status="pending", limit=REMINDER_MAX_ACTIVE)

    def cancel(self, target, value: str, *, now=None) -> int:
        if not self._api("cancel_reminder", "cancel_all_reminders"):
            return 0
        now = float(now if now is not None else time.time())
        if value == "all":
            return self._db.cancel_all_reminders(requester_id=self.requester_id(target), cancelled_at=now)
        return int(self._db.cancel_reminder(value, requester_id=self.requester_id(target), cancelled_at=now))

    def prune(self, *, now=None):
        if not self._api("prune_reminders"):
            return 0
        now = float(now if now is not None else time.time())
        return self._db.prune_reminders(before=now - REMINDER_RETENTION_SECONDS)

    def reconcile(self, *, now=None):
        if not self._api("iter_reminders", "reset_claimed_reminder_deliveries", "claim_reminder_delivery"):
            return
        now = float(now if now is not None else time.time())
        # A process loss after the final claim is safe: the row ID is stable
        # and save_direct_message is idempotent, so replay cannot duplicate it.
        self._db.reset_claimed_reminder_deliveries(now=now)
        for reminder in self._db.iter_reminders(status="pending"):
            # Recheck persisted UTC due_at immediately before the atomic claim.
            if float(reminder.get("due_at") or 0) <= now:
                self.deliver(reminder, now=now)
        self.prune(now=now)

    def deliver(self, reminder, *, now=None):
        if not reminder or reminder.get("status") != "pending":
            return False
        if not self._api("claim_reminder_delivery", "complete_reminder_delivery"):
            return False
        now = float(now if now is not None else time.time())
        if not self._db.claim_reminder_delivery(reminder["id"], attempted_at=now):
            return False
        try:
            target = self._state.get_active_agent(reminder.get("target_agent_id", ""))
            if target and getattr(target, "cell_type", "") == "agent":
                row_id = reminder["id"] + ":due"
                row = self._state.save_direct_message({
                    "id": row_id, "thread_id": reminder["thread_id"], "reply_to_id": "",
                    "idempotency_key": reminder["dedupe_key"] + ":thread",
                    "group_name": reminder["group_name"], "sender_id": "torque-server",
                    "sender_kind": "system", "sender_name": "Torque reminder",
                    "recipient_id": target.id, "recipient_kind": getattr(target, "kind", "worker"),
                    "recipient_name": getattr(target, "name", ""), "message": reminder["message"],
                    "message_type": "reminder", "created_at": now, "ack_required": False,
                    "blocking": False, "context_snapshot": {"reminder_id": reminder["id"], "server_owned": True},
                    "delivery_state": "delivered", "delivery_reason": "", "delivered_at": now,
                })
                append = getattr(self._state, "append_direct_message_to_caches", None)
                if callable(append):
                    append(row)
            else:
                # Never expose a deleted target or scoped group in fallback.
                existing = self._db.load_operator_notice_for_dedupe(reminder["dedupe_key"])
                if not existing:
                    self._state.publish_operator_notice(
                        notice_type="notification", severity="info", category="reminder",
                        title="Reminder due", message=reminder["message"], source="reminder",
                        action_kind="open_inbox", action_payload={}, dedupe_key=reminder["dedupe_key"],
                        broadcast=True,
                    )
            self._db.complete_reminder_delivery(reminder["id"], delivered_at=now)
            # Notifications deliberately happen only after durable delivery.
            notifier = getattr(self._state, "notification_manager", None)
            callback = getattr(notifier, "on_reminder", None)
            if callable(callback):
                try:
                    callback(reminder)
                except Exception:
                    pass
            return True
        except Exception as exc:
            # Preserve the pending durable outbox for bounded future reconcile.
            self._db.reset_claimed_reminder_deliveries(now=now)
            try:
                self._db._conn.execute("UPDATE reminders SET last_error=?, updated_at=? WHERE id=?", (str(exc)[:240], now, reminder["id"]))
                self._db._conn.commit()
            except Exception:
                pass
            return False

    @staticmethod
    def due_label(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
