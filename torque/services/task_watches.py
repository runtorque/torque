"""Event-driven, requester-scoped all-tasks-Done watches.

The state layer calls this service only after task mutations or startup hydration;
it never owns a timer or polling loop.  A fired row is durable before its outbox
is delivered, and stable notice/message ids make reconciliation idempotent.
"""
from __future__ import annotations
import time
import uuid
from datetime import datetime, timezone
from ..db import canonical_user_agent_thread_id

WATCH_MAX_TASKS = 20
WATCH_MAX_ACTIVE = 100
WATCH_EXPIRY_SECONDS = 30 * 24 * 60 * 60

class TaskWatchService:
    def __init__(self, state): self._state = state
    @property
    def _db(self): return getattr(self._state, "db", None)

    def _visible(self, watch, task) -> bool:
        requester = self._state.get_active_agent(watch.get("requester_agent_id", ""))
        return bool(requester and getattr(requester, "cell_type", "") == "agent" and
                    getattr(requester, "group", "") == watch.get("group_name", "") and task and
                    getattr(task, "group", "") == watch.get("group_name", ""))

    def _cancel(self, watch, *, now=None):
        now = float(now or time.time())
        return self._db.update_task_watch(watch["id"], {"status": "cancelled", "cancelled_at": now, "outbox_state": "cancelled", "updated_at": now}, only_status="active")

    def create(self, *, target, task_ids: list[str], now: float | None = None):
        if not self._db: raise ValueError("Task watch store is unavailable")
        now = float(now if now is not None else time.time())
        active = self._db.list_task_watches(requester_agent_id=target.id, status="active", limit=WATCH_MAX_ACTIVE + 1)
        if len(active) >= WATCH_MAX_ACTIVE: raise ValueError("At most 100 active watches are allowed for this agent")
        watch_id = "watch-" + uuid.uuid4().hex[:12]
        watch = self._db.save_task_watch({
            "id": watch_id, "requester_agent_id": target.id,
            "thread_id": canonical_user_agent_thread_id(target.id), "group_name": target.group,
            "task_ids": list(task_ids), "created_at": now, "expires_at": now + WATCH_EXPIRY_SECONDS,
            "status": "active", "fired_at": 0, "cancelled_at": 0,
            "dedupe_key": "task-watch:" + watch_id, "outbox_state": "pending",
            "outbox_attempted_at": 0, "updated_at": now,
        })
        self.evaluate_watch(watch, now=now)
        return self._db.load_task_watch(watch_id)

    def list_active(self, target, *, now=None):
        self.prune(now=now)
        return self._db.list_task_watches(requester_agent_id=target.id, status="active", limit=WATCH_MAX_ACTIVE)

    def cancel(self, target, value: str, *, now=None) -> int:
        now = float(now if now is not None else time.time()); value = str(value or "").strip()
        if value == "all": candidates = self._db.list_task_watches(requester_agent_id=target.id, status="active", limit=WATCH_MAX_ACTIVE)
        else:
            item = self._db.load_task_watch(value)
            candidates = [item] if item and item.get("requester_agent_id") == target.id and item.get("status") == "active" else []
        for watch in candidates: self._cancel(watch, now=now)
        return len(candidates)

    def prune(self, *, now=None):
        if not self._db: return 0
        now = float(now if now is not None else time.time()); count=0
        for watch in self._db.list_task_watches(status="active", limit=10000):
            requester = self._state.get_active_agent(watch.get("requester_agent_id", ""))
            tasks = [self._state.board_tasks.get(task_id) for task_id in watch.get("task_ids", [])]
            if (watch.get("expires_at", 0) <= now or not requester
                    or getattr(requester, "group", "") != watch.get("group_name", "")
                    or any(not self._visible(watch, task) for task in tasks)):
                self._cancel(watch, now=now); count += 1
        return count

    def evaluate_for_task(self, task_id: str, *, now=None):
        if not self._db: return
        now = float(now if now is not None else time.time())
        for watch in self._db.list_task_watches(status="active", limit=10000):
            if task_id in watch.get("task_ids", []): self.evaluate_watch(watch, now=now)

    def evaluate_watch(self, watch, *, now=None):
        if not self._db or not watch or watch.get("status") != "active": return
        now = float(now if now is not None else time.time())
        if watch.get("expires_at", 0) <= now:
            self._cancel(watch, now=now); return
        tasks = [self._state.board_tasks.get(task_id) for task_id in watch.get("task_ids", [])]
        from ..state import task_counts_as_done
        if (not tasks or any(not self._visible(watch, task) for task in tasks)
                or any(getattr(task, "lane", "") == "Archived" and not task_counts_as_done(task) for task in tasks)):
            self._cancel(watch, now=now); return
        if not all(task_counts_as_done(task) for task in tasks): return
        fired = self._db.claim_task_watch_fired(watch["id"], fired_at=now)
        if fired:
            self.deliver_outbox(fired, now=now)

    def reconcile(self, *, now=None):
        if not self._db: return
        self.prune(now=now)
        # A daemon crash can leave a claimed outbox mid-delivery.  The notice
        # and thread ids are stable, so returning it to pending is safe.
        self._db.reset_sending_task_watch_outboxes()
        for watch in self._db.list_task_watches(status="active", limit=10000):
            self.evaluate_watch(watch, now=now)
        for watch in self._db.list_task_watches(status="fired", limit=10000):
            if watch.get("outbox_state") != "sent": self.deliver_outbox(watch, now=now)

    def deliver_outbox(self, watch, *, now=None):
        if not self._db or not watch or watch.get("status") != "fired": return
        now = float(now if now is not None else time.time())
        if not self._db.claim_task_watch_outbox(watch["id"], attempted_at=now):
            return
        try:
            task_ids = list(watch.get("task_ids") or [])
            title = "Watched tasks completed"
            message = "All watched tasks are Done: " + ", ".join(task_ids)
            # OperatorNoticeService intentionally treats a repeated publish as
            # a recurrence.  A task watch outbox must instead *ensure* its one
            # occurrence before retrying later effects (the durable thread row).
            existing_notice = self._db.load_operator_notice_for_dedupe(
                watch["dedupe_key"]
            )
            if not existing_notice:
                self._state.publish_operator_notice(
                    notice_type="notification", severity="success",
                    category="task_watch", title=title, message=message,
                    group_name=watch["group_name"],
                    task_id=task_ids[0] if task_ids else "",
                    action_kind="open_task",
                    action_payload={"task_id": task_ids[0] if task_ids else ""},
                    dedupe_key=watch["dedupe_key"], broadcast=True,
                )
            target = self._state.get_active_agent(watch["requester_agent_id"])
            if target:
                row = self._state.save_direct_message({
                    "id": watch["id"] + ":complete", "thread_id": watch["thread_id"], "reply_to_id": "",
                    "idempotency_key": watch["dedupe_key"] + ":thread", "group_name": watch["group_name"],
                    "sender_id": "system", "sender_kind": "system", "sender_name": "System",
                    "recipient_id": target.id, "recipient_kind": getattr(target, "kind", "worker"), "recipient_name": getattr(target, "name", ""),
                    "message": message, "message_type": "system", "created_at": now, "ack_required": False, "blocking": False,
                    "context_snapshot": {"task_watch_id": watch["id"], "command_response": "watch_complete"},
                    "delivery_state": "delivered", "delivery_reason": "", "delivered_at": now,
                })
                append = getattr(self._state, "append_direct_message_to_caches", None)
                if callable(append): append(row)
            self._db.update_task_watch(watch["id"], {"outbox_state": "sent", "outbox_attempted_at": now, "updated_at": now}, only_status="fired")
        except Exception:
            # Fired is intentionally not rolled back. Startup/event reconciliation retries a stable outbox.
            self._db.update_task_watch(watch["id"], {"outbox_state": "pending", "outbox_attempted_at": now, "updated_at": now}, only_status="fired")
