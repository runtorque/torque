"""EventBus, EventLog, and health monitoring."""

import asyncio
import time
from collections import deque

from .adapters.base import AgentEvent
from .config import log


class EventLog:
    """Per-cell ring buffer of AgentEvents (in-memory only)."""

    def __init__(self, max_size: int = 200):
        self._max_size = max_size
        self._events: dict[str, deque[AgentEvent]] = {}

    def append(self, event: AgentEvent):
        buf = self._events.get(event.cell_id)
        if buf is None:
            buf = deque(maxlen=self._max_size)
            self._events[event.cell_id] = buf
        buf.append(event)

    def get(self, cell_id: str, since: float = 0.0) -> list[AgentEvent]:
        buf = self._events.get(cell_id)
        if not buf:
            return []
        if since:
            return [e for e in buf if e.timestamp > since]
        return list(buf)

    def clear(self, cell_id: str):
        self._events.pop(cell_id, None)


class EventBus:
    """Central event processing hub.

    Receives AgentEvents, updates AgentCell fields, appends to EventLog,
    and schedules throttled WebSocket broadcasts (at most once per second).
    """

    def __init__(self, state, event_log: EventLog, notifier=None):
        # state is a MatrixState — imported at runtime to avoid circular deps
        self._state = state
        self._log = event_log
        self._notifier = notifier
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        """Capture the running event loop. Call once during server startup."""
        self._loop = asyncio.get_running_loop()

    async def emit(self, event: AgentEvent):
        """Process an incoming event: update cell, log, schedule broadcast."""
        cell = self._state.agents.get(event.cell_id)
        if not cell:
            log.warning("Event for unknown cell %s (type=%s), discarding",
                        event.cell_id, event.event_type)
            return

        self._apply(event, cell)
        self._log.append(event)
        if self._notifier:
            self._notifier.on_event(event)
        self._schedule_broadcast()

    def _apply(self, event: AgentEvent, cell):
        """Update AgentCell fields based on event type."""
        cell.last_event_at = event.timestamp

        et = event.event_type
        d = event.data

        if et == "session_start":
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            # Persist the agent's own session ID for resume support
            agent_sid = d.get("session_id", "")
            if agent_sid:
                cell.agent_session_id = agent_sid

        elif et == "session_end":
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False

        elif et == "activity_change":
            cell.activity = d.get("activity", "")
            cell.activity_detail = d.get("detail", "")
            cell.error_message = ""
            cell.needs_attention = False

        elif et == "tool_start":
            cell.activity = "tool_call"
            cell.activity_detail = d.get("detail", d.get("tool", ""))
            cell.error_message = ""
            cell.needs_attention = False

        elif et == "tool_end":
            cell.activity = "thinking"
            cell.activity_detail = ""
            cell.needs_attention = False

        elif et == "message":
            cell.activity = "thinking"
            cell.activity_detail = ""
            cell.needs_attention = False

        elif et == "error":
            cell.error_message = d.get("error", "Unknown error")
            cell.needs_attention = True

        elif et == "waiting":
            cell.activity = "waiting"
            cell.activity_detail = d.get("reason", "")
            cell.needs_attention = True

        elif et == "progress":
            cell.activity_detail = d.get("detail", "")

        elif et == "cost_update":
            cell.session_tokens_in += d.get("input_tokens", 0)
            cell.session_tokens_out += d.get("output_tokens", 0)

    def _schedule_broadcast(self):
        """Schedule a throttled broadcast (at most once per second)."""
        if not self._loop:
            return
        # Global trailing-edge throttle: if a timer is already pending, skip
        if "_global" in self._timers:
            return
        handle = self._loop.call_later(1.0, self._fire_broadcast)
        self._timers["_global"] = handle

    def _fire_broadcast(self):
        """Timer callback — create a task for the async broadcast."""
        self._timers.pop("_global", None)
        asyncio.create_task(self._do_broadcast())

    async def _do_broadcast(self):
        try:
            await self._state.broadcast()
        except Exception:
            log.exception("EventBus broadcast failed")

    def cleanup_cell(self, cell_id: str):
        """Clean up when a cell is removed."""
        self._log.clear(cell_id)


async def health_check(state, event_log: EventLog, event_bus: EventBus,
                       notifier=None):
    """Periodic health check — runs every 30 seconds.

    Flags agents that appear stuck or have repeated errors.
    """
    while True:
        await asyncio.sleep(30)
        now = time.time()
        changed = False

        for cell in state.cells_with_awareness():
            if cell.status != "running":
                continue
            if cell.last_event_at == 0.0:
                continue  # never received an event

            gs = state.get_group_settings(cell.group)
            timeout_min = gs.agent_idle_timeout
            if timeout_min <= 0:
                continue  # idle timeout disabled for this group

            silence = now - cell.last_event_at
            timeout_sec = timeout_min * 60

            # No events for timeout while running and not waiting
            if silence > timeout_sec and cell.activity not in ("waiting",):
                if not cell.needs_attention:
                    cell.needs_attention = True
                    cell.error_message = (
                        f"No activity for {timeout_min} minute"
                        f"{'s' if timeout_min != 1 else ''}")
                    changed = True
                    if notifier:
                        notifier.on_health_alert(
                            cell.id, cell.error_message)

            # Repeated errors in last 5 minutes
            recent = event_log.get(cell.id, since=now - 300)
            recent_errors = [e for e in recent if e.event_type == "error"]
            if len(recent_errors) >= 3:
                msg = f"{len(recent_errors)} errors in last 5 minutes"
                if cell.error_message != msg:
                    cell.needs_attention = True
                    cell.error_message = msg
                    changed = True
                    if notifier:
                        notifier.on_health_alert(cell.id, msg)

        if changed:
            await state.broadcast()
