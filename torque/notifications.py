"""macOS notifications with batching."""

import asyncio
import subprocess

from .adapters.base import AgentEvent
from .config import log


class NotificationManager:
    """Batches and sends macOS notifications.

    Collects notification triggers over a 5-second window, then sends
    a single combined notification per group.
    """

    BATCH_SECONDS = 5.0

    def __init__(self, state):
        self._state = state
        self._pending: list[dict] = []  # {group, cell_name, kind, noun}
        self._timer: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        self._loop = asyncio.get_running_loop()

    def on_event(self, event: AgentEvent):
        """Check if an event should trigger a notification."""
        cell = self._state.agents.get(event.cell_id)
        if not cell:
            return

        gs = self._state.get_group_settings(cell.group)
        if not gs.notifications:
            return

        kind = None
        if event.event_type == "session_end" and gs.notify_on_finish:
            kind = "finished"
        elif event.event_type == "error" and gs.notify_on_error:
            kind = "error"
        elif event.event_type == "waiting" and gs.notify_on_attention:
            kind = "needs attention"

        if not kind:
            return

        self._pending.append({
            "group": cell.group,
            "cell_name": cell.name,
            "kind": kind,
            "noun": "agents",
        })
        self._schedule_flush()

    def on_health_alert(self, cell_id: str, message: str):
        """Called by health_check when an agent is flagged."""
        cell = self._state.agents.get(cell_id)
        if not cell:
            return

        gs = self._state.get_group_settings(cell.group)
        if not gs.notifications or not gs.notify_on_attention:
            return

        self._pending.append({
            "group": cell.group,
            "cell_name": cell.name,
            "kind": "needs attention",
            "noun": "agents",
        })
        self._schedule_flush()

    def on_system_alert(self, title: str, body: str):
        """Send a system-level notification immediately, bypassing the
        5-second batch window. Used for infrastructure events that the
        user should see right away (e.g. PTY supervisor restart).
        """
        if not self._loop:
            return
        asyncio.create_task(_send_notification(title, body))

    def on_direct_user_message(self, row: dict) -> bool:
        """Notify for agent→user direct messages.

        This is the concrete implementation behind the optional
        ``state.notification_manager.on_direct_user_message`` hook used by
        the MCP ``*_message_user`` tools.  It is intentionally best-effort:
        notification failures are logged from the background task and never
        fail durable message persistence.
        """
        row = row or {}
        if not _is_agent_to_user_direct_message(row):
            return False

        sender_id = str(row.get("sender_id", "") or "").strip()
        cell = self._state.agents.get(sender_id)
        group = (
            str(row.get("group_name", "") or "").strip()
            or str(getattr(cell, "group", "") or "").strip()
        )
        if not group:
            return False
        gs = self._state.get_group_settings(group)
        if not gs.notifications or not gs.notify_on_attention:
            return False

        agent_name = (
            str(row.get("sender_name", "") or "").strip()
            or str(getattr(cell, "name", "") or "").strip()
            or sender_id
            or "agent"
        )
        first_line = _first_line(row.get("message", ""))
        title = f"Torque message from {agent_name}"
        body = f"{agent_name}: {first_line}"
        return self._send_immediate_best_effort(
            title,
            body,
            error_message="Failed to send direct user message notification",
        )

    def on_task_health_alert(self, task_id: str, health_state: str):
        """Queue a task-health notification when a task becomes risky."""
        task = self._state.board_tasks.get(task_id)
        if not task:
            return

        gs = self._state.get_group_settings(task.group)
        if not gs.notifications or not gs.notify_on_attention:
            return

        kind = _health_notification_kind(health_state)
        if not kind:
            return

        self._pending.append({
            "group": task.group,
            "cell_name": _task_subject(task.task),
            "kind": kind,
            "noun": "tasks",
        })
        self._schedule_flush()

    def _schedule_flush(self):
        if not self._loop:
            return
        if self._timer is not None:
            return  # already scheduled
        self._timer = self._loop.call_later(
            self.BATCH_SECONDS, self._fire_flush)

    def _fire_flush(self):
        self._timer = None
        pending = self._pending[:]
        self._pending.clear()
        if pending:
            asyncio.create_task(self._flush(pending))

    def _send_immediate_best_effort(
            self,
            title: str,
            body: str,
            *,
            error_message: str) -> bool:
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return False
            self._loop = loop
        if loop.is_closed():
            return False

        async def _send():
            try:
                await _send_notification(title, body)
            except Exception:
                log.exception(error_message)

        loop.create_task(_send())
        return True

    async def _flush(self, items: list[dict]):
        """Combine and send notifications."""
        # Group by group name
        by_group: dict[str, list[dict]] = {}
        for item in items:
            by_group.setdefault(item["group"], []).append(item)

        for group, group_items in by_group.items():
            title = f"Torque — {group}"
            body = _build_body(group_items)
            await _send_notification(title, body)


def _build_body(items: list[dict]) -> str:
    """Build a notification body from batched items."""
    if len(items) == 1:
        item = items[0]
        return f"{item['cell_name']} {item['kind']}"

    # Summarize by kind
    by_kind: dict[tuple[str, str], int] = {}
    for item in items:
        key = (item["kind"], item.get("noun", "agents"))
        by_kind[key] = by_kind.get(key, 0) + 1

    parts = []
    for (kind, noun), count in by_kind.items():
        if count == 1:
            name = next(
                i["cell_name"] for i in items
                if i["kind"] == kind and i.get("noun", "agents") == noun
            )
            parts.append(f"{name} {kind}")
        else:
            parts.append(f"{count} {noun} {kind}")

    return ", ".join(parts)


def _health_notification_kind(health_state: str) -> str:
    if health_state == "idle-risk":
        return "at risk"
    if health_state in ("stalled", "thrashing"):
        return health_state
    return ""


def _is_agent_to_user_direct_message(row: dict) -> bool:
    sender_kind = str((row or {}).get("sender_kind", "") or "").strip()
    recipient_kind = str((row or {}).get("recipient_kind", "") or "").strip()
    recipient_id = str((row or {}).get("recipient_id", "") or "").strip()
    message_type = str((row or {}).get("message_type", "message") or "message")
    return (
        sender_kind in {"architect", "engineer", "worker"}
        and recipient_kind == "user"
        and recipient_id == "user"
        and message_type == "message"
    )


def _first_line(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    return text.splitlines()[0].strip()


def _task_subject(title: str) -> str:
    title = (title or "").strip()
    if len(title) > 40:
        title = title[:37].rstrip() + "..."
    return f'Task "{title or "untitled"}"'


async def _send_notification(title: str, body: str):
    """Send a macOS notification via osascript."""
    try:
        script = (
            f'display notification "{_escape(body)}" '
            f'with title "{_escape(title)}"'
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("osascript notification failed: %s",
                        stderr.decode().strip())
        else:
            log.debug("Notification sent: %s — %s", title, body)
    except Exception:
        log.exception("Failed to send notification")


def _escape(s: str) -> str:
    """Escape a string for AppleScript."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
