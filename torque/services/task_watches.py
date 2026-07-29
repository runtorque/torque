"""Event-driven, requester-scoped all-tasks-Done watches.

The state layer calls this service only after task mutations or startup hydration;
it never owns a timer or polling loop.  A fired row is durable before its outbox
is delivered, and stable notice/message ids make reconciliation idempotent.
"""
from __future__ import annotations
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from ..db import canonical_user_agent_thread_id
from ..config import log

WATCH_MAX_TASKS = 20
WATCH_MAX_ACTIVE = 100
WATCH_EXPIRY_SECONDS = 30 * 24 * 60 * 60

class TaskWatchService:
    def __init__(self, state): self._state = state
    @property
    def _db(self): return getattr(self._state, "db", None)

    def _has_watch_api(self, *names: str) -> bool:
        """Keep board mutation compatibility with minimal test/legacy DBs."""
        db = self._db
        return bool(db) and all(callable(getattr(db, name, None)) for name in names)

    def _visible(self, watch, task) -> bool:
        requester = self._state.get_active_agent(watch.get("requester_agent_id", ""))
        return bool(requester and getattr(requester, "cell_type", "") == "agent" and
                    getattr(requester, "group", "") == watch.get("group_name", "") and task and
                    getattr(task, "group", "") == watch.get("group_name", ""))

    def _cancel(self, watch, *, now=None):
        if not self._has_watch_api("claim_task_watch_cancelled"):
            return None
        now = float(now or time.time())
        return self._db.claim_task_watch_cancelled(
            watch["id"],
            cancelled_at=now,
        )

    def _cancel_delivery(self, watch, *, now=None):
        """Terminate one fired outbox when its task scope vanishes."""
        if not self._has_watch_api("cancel_task_watch_delivery"):
            return None
        return self._db.cancel_task_watch_delivery(
            watch["id"], cancelled_at=float(now if now is not None else time.time())
        )

    def _terminate_unauthorized_delivery(self, watch, tasks, *, now=None):
        """Cancel just this watch unless the requester itself lost scope."""
        requester = self._state.get_active_agent(watch.get("requester_agent_id", ""))
        if (not requester
                or getattr(requester, "group", "") != watch.get("group_name", "")):
            return self.invalidate_requester(
                watch.get("requester_agent_id", ""), now=now
            )
        return self._cancel_delivery(watch, now=now)

    def invalidate_requester(self, requester_agent_id: str, *, now=None) -> int:
        """Atomically stop active/fired watches before requester teardown."""
        if not self._has_watch_api("terminate_task_watches_for_requester"):
            return 0
        return self._db.terminate_task_watches_for_requester(
            requester_agent_id,
            cancelled_at=float(now if now is not None else time.time()),
        )

    def invalidate_group(self, group_name: str, *, now=None) -> int:
        """Atomically stop watches before a group identity/scope disappears."""
        if not self._has_watch_api("terminate_task_watches_for_group"):
            return 0
        return self._db.terminate_task_watches_for_group(
            group_name,
            cancelled_at=float(now if now is not None else time.time()),
        )

    def _recover_request_watch(self, watch, *, now: float):
        """Resume a durable request after its command-result audit was lost."""
        if watch.get("status") == "active":
            self.evaluate_watch(watch, now=now)
        watch = self._db.load_task_watch(watch["id"]) or watch
        if watch.get("status") == "fired" and watch.get("outbox_state") != "sent":
            self.deliver_outbox(watch, now=now)
        return self._db.load_task_watch(watch["id"]) or watch

    def create(
        self, *, target, task_ids: list[str], now: float | None = None,
        request_idempotency_key: str = "",
    ):
        if not self._has_watch_api(
                "list_task_watches", "save_task_watch", "load_task_watch",
                "update_task_watch", "claim_task_watch_fired"):
            raise ValueError("Task watch store is unavailable")
        task_ids = [str(task_id or "").strip() for task_id in (task_ids or [])]
        if (not task_ids or len(task_ids) > WATCH_MAX_TASKS
                or any(not task_id for task_id in task_ids)
                or len(set(task_ids)) != len(task_ids)):
            raise ValueError("A task watch requires 1–20 unique task IDs")
        now = float(now if now is not None else time.time())
        request_key = str(request_idempotency_key or "").strip()
        if request_key and not self._has_watch_api("load_task_watch_by_request_key"):
            raise ValueError("Task watch store is unavailable")
        existing = self._db.load_task_watch_by_request_key(request_key) if request_key else None
        if existing:
            if (existing.get("requester_agent_id") != target.id
                    or existing.get("group_name") != target.group
                    or existing.get("task_ids") != list(task_ids)):
                raise ValueError("idempotency key was reused for a different user_agent_message")
            return self._recover_request_watch(existing, now=now)
        active = self._db.list_task_watches(requester_agent_id=target.id, status="active", limit=WATCH_MAX_ACTIVE + 1)
        if len(active) >= WATCH_MAX_ACTIVE: raise ValueError("At most 100 active watches are allowed for this agent")
        watch_id = "watch-" + uuid.uuid4().hex[:12]
        payload = {
            "id": watch_id, "requester_agent_id": target.id,
            "thread_id": canonical_user_agent_thread_id(target.id), "group_name": target.group,
            "task_ids": list(task_ids), "created_at": now, "expires_at": now + WATCH_EXPIRY_SECONDS,
            "status": "active", "fired_at": 0, "cancelled_at": 0,
            "dedupe_key": "task-watch:" + watch_id,
            "request_idempotency_key": request_key, "outbox_state": "pending",
            "outbox_attempted_at": 0, "updated_at": now,
        }
        try:
            watch = self._db.save_task_watch(payload)
        except sqlite3.IntegrityError:
            existing = self._db.load_task_watch_by_request_key(request_key) if request_key else None
            if not existing:
                raise
            if (existing.get("requester_agent_id") != target.id
                    or existing.get("group_name") != target.group
                    or existing.get("task_ids") != list(task_ids)):
                raise ValueError("idempotency key was reused for a different user_agent_message")
            return self._recover_request_watch(existing, now=now)
        self.evaluate_watch(watch, now=now)
        return self._db.load_task_watch(watch_id)

    def list_active(self, target, *, now=None):
        if not self._has_watch_api("list_task_watches"):
            return []
        self.prune(now=now)
        return self._db.list_task_watches(
            requester_agent_id=target.id,
            status="active",
            limit=WATCH_MAX_ACTIVE,
        )

    def cancel(self, target, value: str, *, now=None) -> int:
        if not self._has_watch_api(
                "list_task_watches", "load_task_watch",
                "claim_task_watch_cancelled"):
            return 0
        now = float(now if now is not None else time.time()); value = str(value or "").strip()
        if value == "all": candidates = self._db.list_task_watches(requester_agent_id=target.id, status="active", limit=WATCH_MAX_ACTIVE)
        else:
            item = self._db.load_task_watch(value)
            candidates = [item] if item and item.get("requester_agent_id") == target.id and item.get("status") == "active" else []
        cancelled = 0
        for watch in candidates:
            if self._cancel(watch, now=now):
                cancelled += 1
        return cancelled

    def prune(self, *, now=None):
        if not self._has_watch_api(
                "iter_task_watches", "claim_task_watch_cancelled"):
            return 0
        now = float(now if now is not None else time.time()); count=0
        for watch in self._db.iter_task_watches(status="active"):
            requester = self._state.get_active_agent(watch.get("requester_agent_id", ""))
            tasks = [self._state.board_tasks.get(task_id) for task_id in watch.get("task_ids", [])]
            if (watch.get("expires_at", 0) <= now or not requester
                    or getattr(requester, "group", "") != watch.get("group_name", "")
                    or any(not self._visible(watch, task) for task in tasks)):
                self._cancel(watch, now=now); count += 1
        return count

    def evaluate_for_task(self, task_id: str, *, now=None):
        if not self._has_watch_api("iter_task_watches"):
            return
        now = float(now if now is not None else time.time())
        for watch in self._db.iter_task_watches(status="active"):
            if task_id in watch.get("task_ids", []): self.evaluate_watch(watch, now=now)

    def evaluate_watch(self, watch, *, now=None):
        if (not self._has_watch_api(
                    "claim_task_watch_fired", "claim_task_watch_cancelled")
                or not watch or watch.get("status") != "active"):
            return
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
        if not self._has_watch_api(
                "iter_task_watches", "reset_sending_task_watch_outboxes"):
            return
        self.prune(now=now)
        # A daemon crash can leave a claimed outbox mid-delivery.  The notice
        # and thread ids are stable, so returning it to pending is safe.
        self._db.reset_sending_task_watch_outboxes()
        for watch in self._db.iter_task_watches(status="active"):
            self.evaluate_watch(watch, now=now)
        for watch in self._db.iter_task_watches(status="fired"):
            if watch.get("outbox_state") != "sent": self.deliver_outbox(watch, now=now)

    def deliver_outbox(self, watch, *, now=None):
        if (not self._has_watch_api(
                    "claim_task_watch_outbox", "claim_task_watch_notice_delivery",
                    "load_direct_message", "update_task_watch")
                or not watch or watch.get("status") != "fired"):
            return
        now = float(now if now is not None else time.time())
        tasks = [self._state.board_tasks.get(task_id)
                 for task_id in watch.get("task_ids", [])]
        if not tasks or any(not self._visible(watch, task) for task in tasks):
            self._terminate_unauthorized_delivery(watch, tasks, now=now)
            return
        if not self._db.claim_task_watch_outbox(watch["id"], attempted_at=now):
            return
        # Scope invalidation can race after the first outbox claim.  Recheck it
        # and atomically claim the final notification gate; an invalidator that
        # wins while this row is sending changes the state to cancelled first.
        tasks = [self._state.board_tasks.get(task_id)
                 for task_id in watch.get("task_ids", [])]
        if not tasks or any(not self._visible(watch, task) for task in tasks):
            self._terminate_unauthorized_delivery(watch, tasks, now=now)
            return
        if not self._db.claim_task_watch_notice_delivery(watch["id"], attempted_at=now):
            return
        try:
            # The final claim is deliberately followed by another authorization
            # check.  Lifecycle invalidation also claims ``notifying`` rows, so
            # an invalidator that wins at this boundary prevents the only
            # required side effect: the durable originating-thread row.
            current = self._db.load_task_watch(watch["id"])
            # Do not retain object references from before the final claim.  A
            # task can be removed from the board in that interval, while the
            # former dataclass instance would still appear group-visible.
            tasks = [self._state.board_tasks.get(task_id)
                     for task_id in watch.get("task_ids", [])]
            task_ids = list(watch.get("task_ids") or [])
            message = "All watched tasks are Done: " + ", ".join(task_ids)
            target = self._state.get_active_agent(watch["requester_agent_id"])
            if (not current or current.get("status") != "fired"
                    or current.get("outbox_state") != "notifying"):
                return
            requester_lost = (
                not target
                or getattr(target, "group", "") != watch.get("group_name", "")
            )
            if requester_lost:
                self.invalidate_requester(watch.get("requester_agent_id", ""), now=now)
                return
            if not tasks or any(not self._visible(watch, task) for task in tasks):
                self._cancel_delivery(watch, now=now)
                return

            # The thread row is the notification.  It is committed before any
            # optional desktop fanout, has a stable idempotency key, and is
            # explicitly checked on recovery so a crash cannot create a second
            # message or rewrite a non-watch row with a colliding id.
            row_id = watch["id"] + ":complete"
            row = self._db.load_direct_message(row_id)
            if row is not None:
                snapshot = row.get("context_snapshot", {}) or {}
                if (row.get("thread_id") != watch["thread_id"]
                        or row.get("recipient_id") != target.id
                        or snapshot.get("task_watch_id") != watch["id"]):
                    raise RuntimeError("task watch completion row collision")
            else:
                row = self._state.save_direct_message({
                    "id": watch["id"] + ":complete", "thread_id": watch["thread_id"], "reply_to_id": "",
                    "idempotency_key": watch["dedupe_key"] + ":thread", "group_name": watch["group_name"],
                    "sender_id": "torque-server", "sender_kind": "system", "sender_name": "Torque",
                    "recipient_id": target.id, "recipient_kind": getattr(target, "kind", "worker"), "recipient_name": getattr(target, "name", ""),
                    "message": message, "message_type": "system", "created_at": now, "ack_required": False, "blocking": False,
                    "context_snapshot": {"task_watch_id": watch["id"], "command_response": "watch_complete", "server_owned": True},
                    "delivery_state": "delivered", "delivery_reason": "", "delivered_at": now,
                })
                if not row:
                    raise RuntimeError("failed to save task watch completion row")
            terminal = self._db.update_task_watch(
                watch["id"],
                {"outbox_state": "sent", "outbox_attempted_at": now, "updated_at": now},
                only_status="fired",
            )
            if (not terminal or terminal.get("status") != "fired"
                    or terminal.get("outbox_state") != "sent"):
                return
        except Exception:
            # Fired is intentionally not rolled back. Startup/event reconciliation retries a stable outbox.
            log.exception("Task watch durable thread delivery failed: watch_id=%s", watch.get("id", ""))
            self._db.update_task_watch(watch["id"], {"outbox_state": "pending", "outbox_attempted_at": now, "updated_at": now}, only_status="fired")
            return

        # Desktop notification is deliberately after the durable thread row
        # and terminal outbox update.  It is optional and never retries or
        # rolls back durable delivery when an OS notification path fails.
        notifier = getattr(self._state, "notification_manager", None)
        callback = getattr(notifier, "on_task_watch", None)
        if callable(callback):
            try:
                callback(watch)
            except Exception:
                log.exception("Task watch desktop notification failed")
