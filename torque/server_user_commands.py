"""Handle local task-watch plus one-shot-reminder user commands."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone

from .server_communication import (
    USER_REMINDER_MAX_DELAY_SECONDS,
    USER_REMINDER_MAX_MESSAGE_CHARS,
    USER_REMINDER_MIN_DELAY_SECONDS,
    _save_user_agent_system_audit_message,
    _user_agent_message_idempotency_key,
    _user_direct_message_id_from_idempotency_key,
    _user_dm_display_text,
)
from .state import MatrixState, task_counts_as_done


def _watch_local_response(data: dict, state: MatrixState, target, command_id: str, content: str) -> dict:
    idempotency_key = _user_agent_message_idempotency_key(data)
    message_id = _user_direct_message_id_from_idempotency_key(idempotency_key) or "msg-" + uuid.uuid4().hex[:12]
    existing = state.db.load_direct_message(message_id) if idempotency_key else None
    if existing:
        snapshot = existing.get("context_snapshot", {}) or {}
        if (str(existing.get("recipient_id", "")) != target.id or snapshot.get("slash_command") != command_id):
            return {"type": "error", "message": "idempotency key was reused for a different user_agent_message"}
        append = getattr(state, "append_direct_message_to_caches", None)
        if callable(append): append(existing)
        return {"type": "ok", "message_id": message_id, "thread_id": existing.get("thread_id", ""), "agent_id": target.id, "delivered": True, "buffered": False, "deduped": True}
    saved = _save_user_agent_system_audit_message(state, target, content, message_id=message_id, idempotency_key=idempotency_key, context_snapshot={"slash_command": command_id, "command_response": command_id})
    if not saved: return {"type": "error", "message": "Failed to save command response"}
    return {"type": "ok", "message_id": saved["id"], "thread_id": saved["thread_id"], "agent_id": target.id, "delivered": True, "buffered": False, "deduped": False}

def _handle_task_watch_command(data: dict, state: MatrixState, target, message_text: str, command_id: str) -> dict:
    # Idempotency is checked before every mutation, including immediate fire.
    key = _user_agent_message_idempotency_key(data)
    mid = _user_direct_message_id_from_idempotency_key(key)
    if mid and state.db.load_direct_message(mid):
        # A durable /watch request may predate its audit row after a partial
        # failure.  Continue through canonical parsing so a reused key cannot
        # create (or mask) a conflicting watch request.
        load_request = getattr(state.db, "load_task_watch_by_request_key", None)
        if command_id != "watch" or not callable(load_request) or not load_request(key):
            return _watch_local_response(data, state, target, command_id, "")
    raw = str(message_text or "").strip()
    # Case variants/mixed forms are recognized but intentionally rejected locally.
    expected = "/" + command_id
    if not raw.startswith(expected) or (len(raw) > len(expected) and not raw[len(expected)].isspace()):
        return _watch_local_response(data, state, target, command_id, f"Usage: {expected}" + (" <task-id> [<task-id> ...]" if command_id == "watch" else (" <watch-id|all>" if command_id == "unwatch" else "")))
    rest = raw[len(expected):].strip()
    if command_id == "watches":
        if rest: return _watch_local_response(data, state, target, command_id, "Usage: /watches")
        watches = state.list_task_watches(target)
        lines = ["**Active task watches**"]
        for watch in watches[:100]:
            visible = [state.board_tasks.get(task_id) for task_id in watch.get("task_ids", [])]
            if any(not task or getattr(task, "group", "") != target.group for task in visible): continue
            done = sum(1 for task in visible if task_counts_as_done(task))
            titles = ", ".join(f"`{_user_dm_display_text(task.id, limit=48)}` {_user_dm_display_text(task.task, limit=72)}" for task in visible)
            expiry = datetime.fromtimestamp(watch["expires_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"- `{watch['id']}` · {done}/{len(visible)} Done · expires {expiry} · {titles}")
        if len(lines) == 1: lines.append("- No active task watches.")
        return _watch_local_response(data, state, target, command_id, "\n".join(lines))
    if command_id == "unwatch":
        if not rest or len(rest.split()) != 1 or (rest != "all" and not re.fullmatch(r"watch-[a-f0-9]{12}", rest)):
            return _watch_local_response(data, state, target, command_id, "Usage: /unwatch <watch-id|all>")
        count = state.cancel_task_watch(target, rest)
        if rest == "all":
            detail = (
                f"Cancelled {count} active task watch(es)."
                if count else "No active task watches."
            )
        else:
            detail = (
                f"Cancelled task watch `{rest}`."
                if count else "No matching active task watch."
            )
        return _watch_local_response(data, state, target, command_id, detail)
    refs = rest.split()
    if not refs or len(refs) > 20:
        return _watch_local_response(data, state, target, command_id, "Usage: /watch <task-id> [<task-id> ...] (1–20 unique task IDs)")
    canonical = []
    for ref in refs:
        resolved = state.resolve_board_task_id(ref)
        task = state.board_tasks.get(resolved) if resolved else None
        if (not task or getattr(task, "group", "") != target.group
                or (getattr(task, "lane", "") == "Archived" and not task_counts_as_done(task))):
            return _watch_local_response(data, state, target, command_id, "Task references must name visible tasks in this agent's group.")
        canonical.append(resolved)
    if len(set(canonical)) != len(canonical):
        return _watch_local_response(data, state, target, command_id, "Each watched task must be unique.")
    try:
        watch = state.create_task_watch(
            target=target, task_ids=canonical, request_idempotency_key=key,
        )
    except ValueError as exc:
        if str(exc) == "idempotency key was reused for a different user_agent_message":
            return {"type": "error", "message": str(exc)}
        return _watch_local_response(data, state, target, command_id, str(exc))
    return _watch_local_response(
        data, state, target, command_id,
        f"Watching {len(canonical)} task(s) until all are Done: `{watch['id']}`.",
    )


def _parse_reminder_delay(token: str) -> tuple[int, str]:
    """Parse the intentionally tiny one-shot reminder delay vocabulary."""
    match = re.fullmatch(r"(\d+)([mhd])", str(token or ""))
    if not match:
        return 0, "Reminder delay must look like 10m, 2h, or 1d."
    seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if seconds < USER_REMINDER_MIN_DELAY_SECONDS:
        return 0, "Reminder delay must be at least 1m."
    if seconds > USER_REMINDER_MAX_DELAY_SECONDS:
        return 0, "Reminder delay must be 30d or less."
    return seconds, ""


def _reminder_local_response(data: dict, state: MatrixState, target, command_id: str, content: str) -> dict:
    idempotency_key = _user_agent_message_idempotency_key(data)
    message_id = _user_direct_message_id_from_idempotency_key(idempotency_key) or "msg-" + uuid.uuid4().hex[:12]
    command_text = str(data.get("_reminder_command", "") or "")
    existing = state.db.load_direct_message(message_id) if idempotency_key else None
    if existing:
        snapshot = existing.get("context_snapshot", {}) or {}
        if (str(existing.get("recipient_id", "") or "") != target.id
                or snapshot.get("slash_command") != command_id
                or snapshot.get("reminder_command", "") != command_text):
            return {"type": "error", "message": "idempotency key was reused for a different user_agent_message"}
        append = getattr(state, "append_direct_message_to_caches", None)
        if callable(append): append(existing)
        return {"type": "ok", "message_id": message_id, "thread_id": existing.get("thread_id", ""), "agent_id": target.id, "delivered": True, "buffered": False, "deduped": True}
    saved = _save_user_agent_system_audit_message(
        state, target, content, message_id=message_id, idempotency_key=idempotency_key,
        context_snapshot={"slash_command": command_id, "command_response": command_id, "reminder_command": command_text},
    )
    if not saved: return {"type": "error", "message": "Failed to save command response"}
    return {"type": "ok", "message_id": saved["id"], "thread_id": saved["thread_id"], "agent_id": target.id, "delivered": True, "buffered": False, "deduped": False}


def _handle_reminder_command(data: dict, state: MatrixState, target, message_text: str, command_id: str) -> dict:
    """Execute local-only reminder grammar without ever queuing a provider prompt."""
    raw = str(message_text or "").strip()
    data = dict(data or {})
    data["_reminder_command"] = raw
    key = _user_agent_message_idempotency_key(data)
    mid = _user_direct_message_id_from_idempotency_key(key)
    if mid and state.db.load_direct_message(mid):
        # Command-result persistence is the terminal idempotent outcome for
        # list/cancel and for a successfully-audited create.  A create whose
        # audit crashed has no row and therefore resumes through its durable
        # request key below.
        return _reminder_local_response(data, state, target, command_id, "")
    expected = "/" + command_id
    if command_id == "reminders":
        if raw != "/reminders":
            return _reminder_local_response(data, state, target, command_id, "Usage: /reminders")
        reminders = state.list_reminders(target)
        lines = ["**Active reminders**"]
        now = time.time()
        for reminder in reminders[:100]:
            text = _user_dm_display_text(reminder.get("message", ""), limit=120)
            due = datetime.fromtimestamp(reminder["due_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            remaining = max(0, int(reminder["due_at"] - now))
            if remaining >= 86400: relative = f"in {remaining // 86400}d"
            elif remaining >= 3600: relative = f"in {remaining // 3600}h"
            else: relative = f"in {max(1, (remaining + 59) // 60)}m"
            visible = state.get_active_agent(reminder.get("target_agent_id", ""))
            origin = _user_dm_display_text(getattr(visible, "name", ""), limit=72) if visible else "original conversation unavailable"
            lines.append(f"- `{reminder['id']}` · {due} ({relative}) · {origin} · pending · {text}")
        if len(lines) == 1:
            lines.append("- No active reminders.")
        return _reminder_local_response(data, state, target, command_id, "\n".join(lines))
    # Case variants and malformed /remind forms were intentionally claimed by
    # the registry and are local guidance rather than provider instructions.
    if not raw.startswith("/remind") or (len(raw) > 7 and not raw[7].isspace()):
        return _reminder_local_response(data, state, target, command_id, "Usage: /remind in 10m <message>, /remind cancel <reminder-id>, or /remind cancel all")
    if raw.startswith("/remind cancel"):
        match = re.fullmatch(r"/remind cancel (rem-[a-f0-9]{12}|all)", raw)
        if not match:
            return _reminder_local_response(data, state, target, command_id, "Usage: /remind cancel <reminder-id|all>")
        value = match.group(1)
        count = state.cancel_reminder(target, value)
        if value == "all":
            text = f"Cancelled {count} active reminder(s)." if count else "No active reminders."
        else:
            text = f"Cancelled reminder `{value}`." if count else "No matching active reminder."
        return _reminder_local_response(data, state, target, command_id, text)
    match = re.fullmatch(r"/remind in (\d+[mhd]) ([\s\S]+)", raw)
    if not match:
        return _reminder_local_response(data, state, target, command_id, "Usage: /remind in 10m <message>")
    if match.group(2)[0].isspace():
        return _reminder_local_response(data, state, target, command_id, "Usage: /remind in 10m <message>")
    delay, error = _parse_reminder_delay(match.group(1))
    if error:
        return _reminder_local_response(data, state, target, command_id, error)
    text = str(match.group(2) or "").strip()
    if not text:
        return _reminder_local_response(data, state, target, command_id, "Reminder message is required.")
    if len(text) > USER_REMINDER_MAX_MESSAGE_CHARS:
        return _reminder_local_response(data, state, target, command_id, f"Reminder message is too long (max {USER_REMINDER_MAX_MESSAGE_CHARS} characters).")
    try:
        reminder = state.create_reminder(
            target=target, delay_seconds=delay, message=text,
            request_idempotency_key=_user_agent_message_idempotency_key(data),
        )
    except ValueError as exc:
        if str(exc) == "idempotency key was reused for a different user_agent_message":
            return {"type": "error", "message": str(exc)}
        return _reminder_local_response(data, state, target, command_id, str(exc))
    due = datetime.fromtimestamp(reminder["due_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _reminder_local_response(data, state, target, command_id, f"Reminder `{reminder['id']}` set for {due}.")
