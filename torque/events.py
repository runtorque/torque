"""EventBus, EventLog, PanelEventLog, and health monitoring."""

import asyncio
import os
import time
from datetime import datetime
from collections import deque
from dataclasses import asdict

from .adapters import get_adapter
from .adapters.base import AgentEvent, EVENT_TYPES
from .config import log
from .context_window import normalize_context_window_usage
from .task_health import HEALTH_SEVERITY
from . import profiling


PERSISTENT_CELL_EVENT_KINDS = {"architect", "engineer"}
WORKER_BOOT_DOA_EVENT = "worker_boot_doa"
WORKER_BOOT_DOA_DEFAULT_SECONDS = 90.0
WORKER_BOOT_DOA_ENV = "TORQUE_WORKER_BOOT_DOA_SECONDS"
WORKER_BOOT_DOA_IGNORED_AGENT_EVENTS = {
    "session_start",
    "heartbeat",
    "context_update",
}
WORKER_BOOT_DOA_IGNORED_PANEL_EVENTS = {
    "agent_started",
    "task_dispatched",
    "task_queued",
    WORKER_BOOT_DOA_EVENT,
}
PROGRESS_EVENT_TYPES = {
    "tool_start",
    "tool_end",
    "message",
    "error",
    "waiting",
    "progress",
    "cost_update",
}
ENGINEER_QUEUE_EMPTY_DEBOUNCE_SECS = 120
ENGINEER_QUEUE_ACTIVE_TASK_LANES = {"To Do", "In Progress"}


def _parse_iso_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _task_progress_anchor_ts(task) -> float:
    if not task:
        return 0.0
    timestamps = [
        _parse_iso_ts(getattr(task, "updated_at", "") or ""),
        _parse_iso_ts(getattr(task, "created_at", "") or ""),
    ]
    for msg in getattr(task, "messages", []) or []:
        ts = msg.get("timestamp") if isinstance(msg, dict) else None
        if isinstance(ts, (int, float)):
            timestamps.append(float(ts))
    timestamps = [ts for ts in timestamps if ts]
    return max(timestamps) if timestamps else 0.0


def _agent_progress_ts(state, cell) -> float:
    progress_at = float(getattr(cell, "last_progress_at", 0.0) or 0.0)
    if progress_at:
        return progress_at
    task_id = str(getattr(cell, "current_task_id", "") or "").strip()
    task = state.board_tasks.get(task_id) if task_id else None
    return _task_progress_anchor_ts(task)


def _cell_kind(cell) -> str:
    return str(getattr(cell, "kind", "") or "").strip()


def _apply_context_window(cell, data: dict | None, timestamp: float | None,
                          *, clear_on_empty: bool = False) -> bool:
    context_window = normalize_context_window_usage(
        (data or {}).get("context_window"),
        now=timestamp,
    )
    if context_window:
        if getattr(cell, "context_window", {}) == context_window:
            return False
        cell.context_window = context_window
        return True
    if clear_on_empty and getattr(cell, "context_window", {}):
        cell.context_window = {}
        return True
    else:
        return False


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_engineer_id", "") or "").strip()


def worker_boot_doa_timeout_seconds() -> float:
    """Return the boot-DOA watchdog delay, or 0 when disabled."""
    raw = os.environ.get(WORKER_BOOT_DOA_ENV, "")
    if not raw:
        return WORKER_BOOT_DOA_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning(
            "Invalid %s=%r; using %.0fs",
            WORKER_BOOT_DOA_ENV,
            raw,
            WORKER_BOOT_DOA_DEFAULT_SECONDS,
        )
        return WORKER_BOOT_DOA_DEFAULT_SECONDS
    return max(0.0, value)


def worker_has_post_boot_activity(event_log: "EventLog", panel_log, *,
                                  cell_id: str, started_at: float) -> bool:
    """Return True if a worker posted any meaningful signal after boot.

    Dispatch bookkeeping (``task_dispatched``) and the boot signal itself do
    not count: the failure mode is a worker whose TUI started but never
    produced hook/MCP output after Torque attempted to send the prompt.
    """
    for event in event_log.get(cell_id, since=started_at):
        if event.event_type not in WORKER_BOOT_DOA_IGNORED_AGENT_EVENTS:
            return True

    if not panel_log:
        return False
    try:
        recent = panel_log.get_recent(getattr(panel_log, "_max_size", 500))
    except Exception:
        log.exception("Failed to inspect panel events for boot-DOA activity")
        return False
    for evt in recent:
        if str(evt.get("cell_id", "") or "") != cell_id:
            continue
        try:
            ts = float(evt.get("timestamp", 0) or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= started_at:
            continue
        kind = str(evt.get("kind", "") or "")
        if kind not in WORKER_BOOT_DOA_IGNORED_PANEL_EVENTS:
            return True
    return False


def _task_is_open_for_boot_doa(task) -> bool:
    if not task:
        return False
    lane = str(getattr(task, "lane", "") or "").strip()
    if lane in {"Done", "Archived"}:
        return False
    status = str(getattr(task, "status", "") or "").strip().lower()
    return status not in {"done", "closed", "archived"}


def emit_worker_boot_doa_if_inactive(state, event_log: "EventLog", panel_log,
                                     cell, *, started_at: float,
                                     timeout_seconds: float | None = None,
                                     now: float | None = None) -> bool:
    """Emit a safe escalation for booted workers that posted no activity.

    Recovery tradeoff: Torque deliberately does **not** retry the dispatch
    prompt or auto-close/relaunch here. A duplicate prompt can corrupt a slow
    but healthy worker's turn, and auto-redispatch has the same false-positive
    risk. The safe default is to surface ``worker_boot_doa`` to the owning
    engineer so they can inspect and relaunch/re-dispatch deliberately.
    """
    if not cell or _cell_kind(cell) != "worker":
        return False
    if str(getattr(cell, "status", "") or "") != "running":
        return False

    timeout = (worker_boot_doa_timeout_seconds() if timeout_seconds is None
               else float(timeout_seconds or 0.0))
    if timeout <= 0:
        return False
    current = time.time() if now is None else float(now)
    if current - float(started_at or 0.0) < timeout:
        return False

    task_id = str(getattr(cell, "current_task_id", "") or "").strip()
    task = state.board_tasks.get(task_id) if task_id else None
    if not _task_is_open_for_boot_doa(task):
        return False
    if str(getattr(task, "agent_id", "") or "").strip() not in {"", cell.id}:
        return False
    if worker_has_post_boot_activity(
            event_log,
            panel_log,
            cell_id=cell.id,
            started_at=float(started_at or 0.0),
    ):
        return False

    if not panel_log:
        log.warning(
            "worker boot DOA detected but no panel log is available: "
            "cell=%s task=%s",
            cell.id,
            task_id,
        )
        return False

    message = (
        f"Worker boot DOA: {cell.name} started {int(timeout)}s ago "
        "but posted no activity after boot. Inspect the worker and "
        "re-dispatch or relaunch if needed."
    )
    log.warning(
        "worker boot DOA detected: cell=%s task=%s owner=%s timeout=%.1fs",
        cell.id,
        task_id,
        _effective_owner_engineer_id(cell) or "<none>",
        timeout,
    )
    pe = panel_log.append(
        kind=WORKER_BOOT_DOA_EVENT,
        cell_id=cell.id,
        agent_name=cell.name,
        group=cell.group,
        message=message,
        task_id=task_id,
    )
    state._emit("event_append", **pe)
    cell.needs_attention = True
    cell.error_message = message
    cell.last_event_text = WORKER_BOOT_DOA_EVENT
    state._emit_agent(cell)
    state._db_save_agent(cell)
    return True


def engineer_has_queue_work(state, engineer) -> bool:
    """Return whether an engineer still owns active tasks or live workers."""
    engineer_id = str(getattr(engineer, "id", "") or "").strip()
    if not engineer_id:
        return False

    for task in getattr(state, "board_tasks", {}).values():
        assigned_id = str(
            getattr(task, "assigned_engineer_id", "") or ""
        ).strip()
        lane = str(getattr(task, "lane", "") or "").strip()
        if (
                assigned_id == engineer_id
                and lane in ENGINEER_QUEUE_ACTIVE_TASK_LANES
        ):
            return True

    for cell in getattr(state, "agents", {}).values():
        if _cell_kind(cell) != "worker":
            continue
        if str(getattr(cell, "status", "") or "").strip() == "stopped":
            continue
        if _effective_owner_engineer_id(cell) == engineer_id:
            return True

    return False


def _engineer_queue_idle_ready(engineer, now: float) -> bool:
    activity = str(getattr(engineer, "activity", "") or "").strip()
    if activity not in {"", "waiting"}:
        return False
    last_progress_at = float(getattr(engineer, "last_progress_at", 0.0) or 0.0)
    if last_progress_at <= 0:
        return False
    return now - last_progress_at >= ENGINEER_QUEUE_EMPTY_DEBOUNCE_SECS


def _engineer_queue_empty_fire_ready(engineer, *, now: float,
                                     observed_empty_at: float) -> bool:
    """Return whether idle+empty have remained stable for the debounce."""
    if not _engineer_queue_idle_ready(engineer, now):
        return False
    last_progress_at = float(getattr(engineer, "last_progress_at", 0.0) or 0.0)
    stable_since = max(float(observed_empty_at or 0.0), last_progress_at)
    return now - stable_since >= ENGINEER_QUEUE_EMPTY_DEBOUNCE_SECS


def check_engineer_queue_empty(state, event_bus: "EventBus",
                               now: float | None = None) -> bool:
    """Emit one engineer_queue_empty event per work→empty transition."""
    now = time.time() if now is None else float(now)
    empty_since = getattr(state, "_engineer_queue_empty_since", None)
    if empty_since is None:
        empty_since = {}
        setattr(state, "_engineer_queue_empty_since", empty_since)

    changed = False
    for engineer in list(getattr(state, "agents", {}).values()):
        if _cell_kind(engineer) != "engineer":
            continue
        engineer_id = str(getattr(engineer, "id", "") or "").strip()
        if not engineer_id:
            continue

        if engineer_has_queue_work(state, engineer):
            empty_since.pop(engineer_id, None)
            if bool(getattr(engineer, "queue_empty_emitted", True)):
                engineer.queue_empty_emitted = False
                state._db_save_agent(engineer)
                state._emit_agent(engineer)
                changed = True
            continue

        activity = str(getattr(engineer, "activity", "") or "").strip()
        last_progress_at = float(getattr(engineer, "last_progress_at", 0.0) or 0.0)
        if activity not in {"", "waiting"} or last_progress_at <= 0:
            empty_since.pop(engineer_id, None)
            continue

        observed_at = empty_since.setdefault(engineer_id, now)
        if not _engineer_queue_empty_fire_ready(
                engineer,
                now=now,
                observed_empty_at=observed_at,
        ):
            continue
        if bool(getattr(engineer, "queue_empty_emitted", True)):
            continue

        panel_log = getattr(state, "panel_log", None) or getattr(
            event_bus, "_panel_log", None)
        if not panel_log:
            continue
        pe = panel_log.append(
            kind="engineer_queue_empty",
            cell_id=engineer.id,
            agent_name=engineer.name,
            group=engineer.group,
            message="",
        )
        state._emit("event_append", **pe)
        engineer.queue_empty_emitted = True
        engineer.last_event_text = "queue_empty"
        try:
            engineer.mark_heartbeat(now)
        except Exception:
            log.exception("failed to mark queue-empty heartbeat for %s", engineer.id)
        state._db_save_agent(engineer)
        state._emit_agent(engineer)
        changed = True

    return changed


class PanelEventLog:
    """Global ring buffer of high-level events for the Events panel.

    Unlike EventLog (per-cell, low-level), this stores aggregate events
    visible across the UI: task dispatched, completed, ask created, etc.
    The in-memory deque is still updated synchronously so recent-event
    queries and WebSocket broadcasts see new events immediately.

    When a *db* is provided, SQLite persistence is batched: appends/updates
    are queued and flushed in one transaction after ``flush_interval`` seconds
    or once ``flush_max_events`` inserts have accumulated.  Each batch commits
    independently; a hard process crash can lose events that are still only in
    memory (normally bounded by the small flush window/threshold), while
    graceful daemon shutdown must call ``aclose()`` to flush pending events.
    WebSocket broadcasts are intentionally fire-and-forget/best-effort;
    recovery is via this durable panel_events log and state snapshots, not a
    WS ACK protocol.
    """

    def __init__(self, max_size: int = 500, db=None,
                 flush_max_events: int = 50,
                 flush_interval: float = 0.1):
        self._max_size = max_size
        self._db = db
        self._events: deque[dict] = deque(maxlen=max_size)
        self.on_event = None  # callback(event_dict) — called on append/replace
        self._flush_max_events = max(1, int(flush_max_events or 1))
        self._flush_interval = max(0.0, float(flush_interval or 0.0))
        self._pending_events: list[dict] = []
        self._pending_event_ids: set[int] = set()
        self._pending_updates: dict[int, dict] = {}
        self._flush_handle: asyncio.TimerHandle | None = None
        self._flush_task: asyncio.Task | None = None
        self._last_flush_failed = False
        self._closed = False
        # Seed id counter and hydrate deque from DB after restart
        if db:
            self._id_counter = db.get_panel_event_max_id()
            for evt in db.load_panel_events(limit=max_size):
                self._events.append(evt)
        else:
            self._id_counter = 0

    def append(self, kind: str, cell_id: str, agent_name: str,
               group: str, message: str, task_id: str = "") -> dict:
        self._id_counter += 1
        evt = {
            "id": self._id_counter,
            "timestamp": time.time(),
            "kind": kind,
            "cell_id": cell_id,
            "agent_name": agent_name,
            "group": group,
            "message": message,
            "task_id": task_id,
        }
        self._events.append(evt)
        if self._db:
            self._queue_insert(evt)
        if self.on_event:
            try:
                self.on_event(evt)
            except Exception:
                log.exception("PanelEventLog.on_event callback failed")
        return evt

    def replace_last(self, kind: str, cell_id: str, **kwargs) -> dict:
        """Replace the most recent event of *kind* for *cell_id*.

        If no matching event exists, appends a new one.
        Returns the updated/created event dict.
        """
        for evt in reversed(self._events):
            if evt["kind"] == kind and evt["cell_id"] == cell_id:
                evt["timestamp"] = time.time()
                for k, v in kwargs.items():
                    if k in evt:
                        evt[k] = v
                if self._db:
                    self._queue_update(evt)
                return evt
        return self.append(kind=kind, cell_id=cell_id, **kwargs)

    def _has_pending_db_work(self) -> bool:
        return bool(self._pending_events or self._pending_updates)

    def _queue_insert(self, evt: dict) -> None:
        self._pending_events.append(evt)
        self._pending_event_ids.add(int(evt["id"]))
        delay = 0.0 if len(self._pending_events) >= self._flush_max_events \
            else self._flush_interval
        self._schedule_flush(delay)

    def _queue_update(self, evt: dict) -> None:
        evt_id = int(evt["id"])
        if evt_id in self._pending_event_ids:
            # The insert has not been flushed yet; it will persist the latest
            # in-memory contents when the batch is copied for SQLite.
            return
        self._pending_updates[evt_id] = evt
        self._schedule_flush(self._flush_interval)

    def _schedule_flush(self, delay: float | None = None) -> None:
        if not self._db or self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Non-async contexts (mostly tests/tools) keep the old durability
            # semantics instead of leaving unflushed work with no running loop.
            self.flush_pending_sync()
            return

        delay = self._flush_interval if delay is None else max(0.0, delay)
        if delay <= 0:
            if self._flush_handle:
                self._flush_handle.cancel()
                self._flush_handle = None
            loop.call_soon(self._ensure_flush_task)
            return
        if self._flush_handle is None:
            self._flush_handle = loop.call_later(delay, self._ensure_flush_task)

    def _ensure_flush_task(self) -> None:
        self._flush_handle = None
        if not self._db or self._closed:
            return
        if self._flush_task and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._flush_pending_async())

    def _pop_pending_batch(self) -> tuple[list[dict], list[dict]]:
        events = list(self._pending_events)
        event_ids = {int(evt["id"]) for evt in events}
        updates = [
            evt
            for evt_id, evt in self._pending_updates.items()
            if int(evt_id) not in event_ids
        ]
        self._pending_events.clear()
        self._pending_event_ids.clear()
        self._pending_updates.clear()
        return events, updates

    def _requeue_pending_batch(self, events: list[dict],
                               updates: list[dict]) -> None:
        if events:
            self._pending_events = list(events) + self._pending_events
            self._pending_event_ids.update(int(evt["id"]) for evt in events)
        for evt in updates:
            self._pending_updates[int(evt["id"])] = evt

    def _persist_batch_sync(self, events: list[dict],
                            updates: list[dict],
                            *,
                            separate_connection: bool) -> int:
        if not self._db or (not events and not updates):
            return 0
        return self._db.save_panel_events_batch(
            events,
            updates=updates,
            max_size=self._max_size,
            separate_connection=separate_connection,
        )

    async def _flush_pending_async(self) -> None:
        self._last_flush_failed = False
        try:
            while self._has_pending_db_work():
                events, updates = self._pop_pending_batch()
                if not events and not updates:
                    break
                event_rows = [dict(evt) for evt in events]
                update_rows = [dict(evt) for evt in updates]
                try:
                    await asyncio.to_thread(
                        self._persist_batch_sync,
                        event_rows,
                        update_rows,
                        separate_connection=True,
                    )
                except Exception as exc:
                    self._last_flush_failed = True
                    self._requeue_pending_batch(events, updates)
                    if hasattr(self._db, "record_mcp_health_event_safe"):
                        self._db.record_mcp_health_event_safe(
                            surface="panel_events",
                            event="drop",
                            error=str(exc) or type(exc).__name__,
                        )
                    log.exception(
                        "Failed to persist %d panel events and %d updates",
                        len(events), len(updates))
                    break
                if (self._pending_events
                        and len(self._pending_events) >= self._flush_max_events):
                    continue
                break
        finally:
            self._flush_task = None
            if self._has_pending_db_work() and not self._closed:
                delay = 0.0 if (
                    len(self._pending_events) >= self._flush_max_events
                ) else self._flush_interval
                self._schedule_flush(delay)

    def flush_pending_sync(self) -> int:
        """Synchronously flush pending panel-event DB work.

        This is used only when no event loop is running.  The daemon's normal
        path uses the async/off-loop flusher so appends stay in-memory-only.
        """
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        events, updates = self._pop_pending_batch()
        try:
            return self._persist_batch_sync(
                events,
                updates,
                separate_connection=False,
            )
        except Exception as exc:
            self._requeue_pending_batch(events, updates)
            if hasattr(self._db, "record_mcp_health_event_safe"):
                self._db.record_mcp_health_event_safe(
                    surface="panel_events",
                    event="drop",
                    error=str(exc) or type(exc).__name__,
                )
            log.exception(
                "Failed to synchronously persist %d panel events and %d updates",
                len(events), len(updates))
            return 0

    async def flush(self) -> None:
        """Flush all queued SQLite work for tests and graceful shutdown."""
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        while True:
            task = self._flush_task
            if task and not task.done():
                await task
                if self._last_flush_failed:
                    return
                continue
            if not self._has_pending_db_work():
                return
            self._ensure_flush_task()
            task = self._flush_task
            if not task:
                return
            await task
            if self._last_flush_failed:
                return

    async def aclose(self) -> None:
        """Cancel timers and flush pending SQLite writes before shutdown."""
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        await self.flush()
        self._closed = True
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None

    def get_recent(self, n: int = 100) -> list[dict]:
        if n >= len(self._events):
            return list(self._events)
        return list(self._events)[-n:]

    def get_page(self, limit: int = 50, before_id: int = 0,
                 cell_id: str = "") -> list[dict]:
        """Return a page of older events from DB (for scroll pagination)."""
        if self._db:
            return self._db.load_panel_events(limit, before_id, cell_id=cell_id)
        # Fallback: paginate from in-memory buffer
        events = list(self._events)
        if before_id:
            events = [e for e in events if e["id"] < before_id]
        if cell_id:
            events = [e for e in events if e.get("cell_id", "") == cell_id]
        return events[-limit:]


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


def _agent_event_message(event: AgentEvent) -> str:
    data = event.data or {}
    et = event.event_type
    if et == "session_start":
        return "Session started"
    if et == "session_end":
        return data.get("summary", "") or data.get("reason", "") or "Session ended"
    if et == "tool_start":
        return data.get("detail", "") or (
            f"Using {data.get('tool')}" if data.get("tool") else "Tool started"
        )
    if et == "tool_end":
        tool = data.get("tool", "")
        suffix = "finished" if data.get("success", True) else "failed"
        return f"{tool} {suffix}".strip() or f"Tool {suffix}"
    if et == "activity_change":
        return data.get("detail", "") or data.get("activity", "") or "Activity changed"
    if et == "error":
        return data.get("error", "") or "Error"
    if et == "waiting":
        return data.get("reason", "") or "Waiting for permission"
    if et == "progress":
        return data.get("detail", "") or "Progress update"
    if et == "cost_update":
        return "Token usage updated"
    if et == "context_update":
        return "Context usage updated"
    return et.replace("_", " ")


def _serialize_agent_event(event: AgentEvent, cell, index: int) -> dict:
    return {
        "id": f"live:{event.timestamp:.6f}:{index}:{event.event_type}",
        "timestamp": event.timestamp,
        "kind": event.event_type,
        "cell_id": event.cell_id,
        "agent_name": getattr(cell, "name", ""),
        "group": getattr(cell, "group", ""),
        "message": _agent_event_message(event),
        "task_id": getattr(cell, "current_task_id", ""),
        "source": "event_log",
    }


def get_cell_event_stream(cell, event_log: EventLog,
                          panel_log: PanelEventLog | None = None,
                          db=None, limit: int = 200) -> list[dict]:
    """Return per-cell events in timestamp order for the focused Events tab."""
    cell_id = getattr(cell, "id", "")
    live_events = [
        _serialize_agent_event(event, cell, index)
        for index, event in enumerate(event_log.get(cell_id))
        # context_update is high-frequency context-meter telemetry; surfacing it
        # in the human-facing event feed is the same noise we suppress on the
        # card status line. The grid context meter renders the data directly.
        if event.event_type != "context_update"
    ]
    events = live_events
    if getattr(cell, "kind", "") in PERSISTENT_CELL_EVENT_KINDS:
        # Two-source layering: EventLog is live/low-level, panel_events is persisted/high-level for architect/engineer cells only.
        if db is not None:
            persisted_events = db.load_panel_events(limit=limit, cell_id=cell_id)
        elif panel_log is not None:
            persisted_events = panel_log.get_page(limit=limit, cell_id=cell_id)
        else:
            persisted_events = []
        events = [dict(evt, source=evt.get("source", "panel_events"))
                  for evt in persisted_events] + live_events
    events.sort(key=lambda evt: (
        float(evt.get("timestamp", 0) or 0),
        str(evt.get("source", "")),
        str(evt.get("id", "")),
    ))
    if limit and len(events) > limit:
        events = events[-limit:]
    return events


class EventBus:
    """Central event processing hub.

    Receives AgentEvents, updates AgentCell fields, appends to EventLog,
    and schedules throttled WebSocket broadcasts (at most once per second).
    """

    def __init__(self, state, event_log: EventLog, notifier=None,
                 panel_log: PanelEventLog | None = None):
        # state is a MatrixState — imported at runtime to avoid circular deps
        self._state = state
        self._log = event_log
        self._notifier = notifier
        self._panel_log = panel_log
        self._engineer_buffer = None  # EngineerEventBuffer, set from server.py
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._boot_doa_timers: dict[str, asyncio.TimerHandle] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.on_session_start = None  # callback(cell) — agent TUI ready
        self.on_session_end = None  # async callback(cell) — agent finished turn

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

        prev_activity = cell.activity
        prev_status = cell.status
        prev_clocks = (
            cell.last_progress_at,
            cell.last_heartbeat_at,
            cell.last_activity_at,
            cell.last_event_at,
        )
        self._apply(event, cell)
        clocks_changed = prev_clocks != (
            cell.last_progress_at,
            cell.last_heartbeat_at,
            cell.last_activity_at,
            cell.last_event_at,
        )
        if cell.status != prev_status or clocks_changed:
            self._state._db_save_agent(cell)
        self._log.append(event)
        if event.event_type == "session_start":
            self._schedule_worker_boot_doa_check(cell, event.timestamp)
        log.info("Event: cell='%s' type=%s activity='%s' detail='%s'",
                 cell.name, event.event_type, cell.activity,
                 cell.activity_detail[:50] if cell.activity_detail else "")
        if self._notifier:
            self._notifier.on_event(event)
        # Notify engineer buffer of activity change (for idle-gated delivery)
        if self._engineer_buffer:
            self._engineer_buffer.on_agent_activity_change(cell)
        # Auto-unlink agent from parent task when idle after derive
        if prev_activity and not cell.activity and cell.current_task_id:
            self._maybe_unlink_post_derive(cell)
        # Notify on agent turn completion (for auto-checkpoint, etc.)
        if event.event_type == "session_end" and self.on_session_end:
            try:
                await self.on_session_end(cell)
            except Exception:
                log.exception("on_session_end callback failed for '%s'",
                              cell.name)
        self._schedule_broadcast()

    def _schedule_worker_boot_doa_check(self, cell, started_at: float):
        """Schedule the safe boot-DOA escalation check for worker sessions."""
        if _cell_kind(cell) != "worker":
            return
        timeout = worker_boot_doa_timeout_seconds()
        if timeout <= 0:
            return
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        existing = self._boot_doa_timers.pop(cell.id, None)
        if existing:
            existing.cancel()
        self._boot_doa_timers[cell.id] = loop.call_later(
            timeout,
            self._run_worker_boot_doa_check,
            cell.id,
            float(started_at or 0.0),
            timeout,
        )

    def _run_worker_boot_doa_check(self, cell_id: str, started_at: float,
                                   timeout: float):
        self._boot_doa_timers.pop(cell_id, None)
        cell = self._state.agents.get(cell_id)
        if not cell:
            return
        changed = emit_worker_boot_doa_if_inactive(
            self._state,
            self._log,
            self._panel_log,
            cell,
            started_at=started_at,
            timeout_seconds=timeout,
        )
        if changed and self._loop and not self._loop.is_closed():
            self._loop.create_task(self._do_broadcast())

    def _apply(self, event: AgentEvent, cell):
        """Update AgentCell fields based on event type."""
        et = event.event_type
        d = event.data
        if et in PROGRESS_EVENT_TYPES:
            cell.mark_progress(event.timestamp)
        else:
            cell.mark_heartbeat(event.timestamp)
        # Always refresh the ephemeral context_window meter (this mutates
        # cell.context_window in place); the WS delta still flows via the
        # unconditional _emit_agent(cell) at the end of this method, so the
        # grid context meter keeps updating. We intentionally do NOT surface a
        # human-facing status line for context_update — it is high-frequency
        # telemetry and would spam the agent card's activity line.
        _apply_context_window(
            cell,
            d,
            event.timestamp,
            clear_on_empty=et == "session_start",
        )

        if et == "session_start":
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            # Persist the agent's own session ID for resume support
            agent_sid = d.get("session_id", "")
            if agent_sid and agent_sid != cell.agent_session_id:
                cell.agent_session_id = agent_sid
                self._state._db_save_agent(cell)
            # Signal the bridge that this agent's TUI is ready for input
            if self.on_session_start:
                self.on_session_start(cell)

        elif et == "session_end":
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            if cell.status != "stopped":
                cell.status = "idle"
            cell.last_event_text = "Session ended"
            summary = d.get("summary", "")
            if summary:
                cell.last_summary = summary

        elif et == "activity_change":
            next_activity = d.get("activity", "")
            next_detail = d.get("detail", "")
            # Claude Code can emit trailing "thinking" activity changes
            # (for example from SubagentStop) after a turn has already
            # ended. Do not revive an idle agent on that passive tail event.
            if (
                cell.status != "running"
                and not cell.activity
                and next_activity in ("", "thinking")
                and not next_detail
            ):
                return
            cell.activity = next_activity
            cell.activity_detail = next_detail
            cell.error_message = ""
            cell.needs_attention = False
            if cell.activity_detail:
                cell.last_event_text = cell.activity_detail
            # Auto-remove blocked label when agent resumes activity
            if cell.current_task_id and cell.activity not in ("", "waiting"):
                task = self._state.board_tasks.get(cell.current_task_id)
                if task and "torque:blocked" in task.labels:
                    task.labels.remove("torque:blocked")
                    self._state._db_save_task(task)
                    self._state._emit("task_upsert", **asdict(task))

        elif et == "tool_start":
            cell.activity = "tool_call"
            cell.activity_detail = d.get("detail", d.get("tool", ""))
            cell.error_message = ""
            cell.needs_attention = False
            if cell.activity_detail:
                cell.last_event_text = cell.activity_detail

        elif et == "tool_end":
            # Don't overwrite state if the tool itself set needs_attention
            # (e.g. torque ai blocked/error runs inside this tool call)
            if not cell.needs_attention and cell.status == "running":
                cell.activity = "thinking"
                cell.activity_detail = ""

        elif et == "message":
            if cell.status == "running" or cell.activity:
                cell.activity = "thinking"
                cell.activity_detail = ""
                cell.needs_attention = False

        elif et == "error":
            cell.error_message = d.get("error", "Unknown error")
            cell.needs_attention = True
            cell.last_event_text = cell.error_message

        elif et == "waiting":
            cell.activity = "waiting"
            cell.activity_detail = d.get("reason", "")
            cell.needs_attention = True
            if cell.activity_detail:
                cell.last_event_text = cell.activity_detail

        elif et == "progress":
            cell.activity_detail = d.get("detail", "")
            if cell.activity_detail:
                cell.last_event_text = cell.activity_detail

        elif et == "cost_update":
            cell.session_tokens_in += d.get("input_tokens", 0)
            cell.session_tokens_out += d.get("output_tokens", 0)

        elif et == "context_update":
            # Context-window meter already refreshed above. Deliberately no
            # last_event_text here: surfacing it spammed the agent card status
            # line on every high-frequency context_update.
            pass

        elif et == "heartbeat":
            detail = d.get("detail", "")
            if detail:
                cell.last_event_text = detail

        elif et == "derive":
            cell.activity = "thinking"
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            cell.last_event_text = (
                d.get("summary", "") or d.get("task", "") or "Derived follow-up task"
            )

        elif et == "done":
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            if cell.status != "stopped":
                cell.status = "idle"
            summary = d.get("summary", "") or d.get("detail", "")
            if summary:
                cell.last_summary = summary
                cell.last_event_text = summary
            else:
                cell.last_event_text = "Task done"

        # Emit panel event for key types
        if self._panel_log and et in (
                "session_start", "session_end", "error", "waiting",
                "derive", "done"):
            kind_map = {
                "session_start": "agent_started",
                "session_end": "agent_finished",
                "error": "agent_error",
                "waiting": "agent_waiting",
                "derive": "task_derived",
                "done": "task_done",
            }
            msg = ""
            if et == "error":
                msg = d.get("error", "Unknown error")
            elif et == "session_end":
                msg = d.get("summary", "") or "Session ended"
            elif et == "waiting":
                msg = d.get("reason", "") or "Waiting for permission"
            elif et == "derive":
                msg = d.get("summary", "") or d.get("task", "") or "Derived task"
            elif et == "done":
                msg = d.get("summary", "") or d.get("detail", "") or "Done"
            pe = self._panel_log.append(
                kind=kind_map[et],
                cell_id=cell.id,
                agent_name=cell.name,
                group=cell.group,
                message=msg,
            )
            self._state._emit("event_append", **pe)

        # Emit delta for all event types
        self._state._emit_agent(cell)

    def _maybe_unlink_post_derive(self, cell):
        """Unlink an idle agent once its task has truly handed work off.

        After an agent derives a subtask and then goes idle, it still holds
        current_task_id for the parent task.  This blocks future dispatches.
        Unlinking here frees the agent without triggering auto-dispatch, but
        only once the descendant branch is actually queued/started/awaiting
        human input. Failed dispatches should keep the current task linked.
        """
        task = self._state.board_tasks.get(cell.current_task_id)
        if not task:
            return
        if self._state.task_has_live_handoff_descendants(task.id):
            log.info("Auto-unlinking idle agent '%s' from post-derive task "
                     "'%s'", cell.name, task.task[:60])
            cell.current_task_id = ""
            self._state._emit_agent(cell)
            self._state._db_save_agent(cell)

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
        # Coalesce health recompute with the throttled broadcast, but do not
        # scan the board if the last 1s of deltas cannot affect task health.
        if self._state.has_pending_task_health_recompute():
            try:
                self._state.recompute_task_health()
            except Exception:
                log.exception("recompute_task_health failed")
        asyncio.create_task(self._do_broadcast())

    async def _do_broadcast(self):
        try:
            await self._state.broadcast()
        except Exception:
            log.exception("EventBus broadcast failed")

    def cleanup_cell(self, cell_id: str):
        """Clean up when a cell is removed."""
        self._log.clear(cell_id)
        handle = self._boot_doa_timers.pop(cell_id, None)
        if handle:
            handle.cancel()


# -- Durable event-ingest drainer -----------------------------------------


def build_event_ingest_envelope(
    raw: dict,
    *,
    headers: dict | None = None,
    received_at: float | None = None,
) -> dict:
    """Build the durable envelope stored by the event-ingest sidecar."""
    return {
        "schema": 1,
        "received_at": float(time.time() if received_at is None else received_at),
        "headers": dict(headers or {}),
        "raw": dict(raw or {}),
    }


def _parse_profile_synthetic_event(raw: dict, cell) -> AgentEvent | None:
    """Parse harness-only events without requiring a provider hook shape."""
    if not profiling.is_enabled():
        return None
    if raw.get("source") != "torque-profile-harness":
        return None
    event_type = str(raw.get("event_type", "")).strip()
    if event_type not in EVENT_TYPES:
        return None
    data = dict(raw.get("data") or {})
    for key in (
            "activity", "detail", "summary", "task", "tool", "reason",
            "error", "session_id", "model", "input_tokens",
            "output_tokens"):
        if key in raw and key not in data:
            data[key] = raw[key]
    data.setdefault("event_id", raw.get("event_id", ""))
    try:
        timestamp = float(raw.get("timestamp") or time.time())
    except (TypeError, ValueError):
        timestamp = time.time()
    return AgentEvent(
        cell_id=cell.id,
        timestamp=timestamp,
        event_type=event_type,
        data=data,
    )


def agent_event_from_ingest_envelope(state, envelope: dict) -> AgentEvent | None:
    """Resolve a durable ingest envelope into an ``AgentEvent``.

    This intentionally mirrors the old ``/events`` request path: header
    cell-id match first, cwd fallback second, then provider adapter parsing.
    Unknown or unparsed events return ``None`` and are still safe for the
    drainer to ack because they would have been discarded before Phase 2.
    """
    raw = dict((envelope or {}).get("raw") or {})
    headers = dict((envelope or {}).get("headers") or {})

    cell_id = headers.get("X-Torque-Cell-Id", "")
    cell = state.agents.get(cell_id) if cell_id else None

    if not cell:
        cwd = raw.get("cwd", "")
        if cwd:
            import os
            for candidate in state.iter_active_agents():
                if candidate.session_id and candidate.directory and \
                        os.path.realpath(candidate.directory) == os.path.realpath(cwd):
                    cell = candidate
                    break

    if not cell:
        log.debug("Event from unknown cell (id=%s, cwd=%s), discarding",
                  cell_id, raw.get("cwd", ""))
        profiling.recorder().incr("events_dropped_unknown_cell")
        return None

    event = _parse_profile_synthetic_event(raw, cell)
    if event is None:
        adapter = get_adapter(cell.agent_type)
        event = adapter.parse_event(raw, cell)
    if event:
        profiling.recorder().incr("events_accepted")
        return event

    profiling.recorder().incr("events_dropped_unparsed")
    return None


class EventIngestDrainer:
    """Drain durable event-ingest rows into ``EventBus.emit`` asynchronously."""

    def __init__(
        self,
        client,
        event_bus: EventBus,
        state,
        *,
        batch_size: int = 100,
        idle_interval: float = 0.05,
        error_interval: float = 1.0,
    ):
        self.client = client
        self.event_bus = event_bus
        self.state = state
        self.batch_size = max(1, int(batch_size or 100))
        self.idle_interval = max(0.01, float(idle_interval or 0.05))
        self.error_interval = max(0.05, float(error_interval or 1.0))
        self.cursor = 0
        self._task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._closed = False
            self._task = asyncio.create_task(self.run_forever())
        return self._task

    async def stop(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def run_forever(self) -> None:
        while not self._closed:
            try:
                processed = await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Event-ingest drainer iteration failed")
                await asyncio.sleep(self.error_interval)
                continue
            if processed < self.batch_size:
                await asyncio.sleep(self.idle_interval)

    async def drain_once(self) -> int:
        response = await self.client.drain(
            since=self.cursor,
            limit=self.batch_size,
        )
        if response.get("type") == "error":
            raise RuntimeError(response.get("message") or response.get("code"))

        ack_cursor = int(response.get("ack_cursor") or 0)
        if ack_cursor > self.cursor:
            self.cursor = ack_cursor

        rows = list(response.get("events") or [])
        if not rows:
            return 0

        processed = 0
        last_cursor = self.cursor
        for row in rows:
            cursor = int(row.get("cursor") or 0)
            envelope = dict(row.get("event") or {})
            event = agent_event_from_ingest_envelope(self.state, envelope)
            if event is not None:
                with profiling.timer("event_bus_emit_ms"):
                    await self.event_bus.emit(event)
            last_cursor = max(last_cursor, cursor)
            processed += 1

        if last_cursor > self.cursor:
            ack = await self.client.ack(up_to=last_cursor)
            if ack.get("type") == "error":
                raise RuntimeError(ack.get("message") or ack.get("code"))
            self.cursor = max(self.cursor, int(ack.get("ack_cursor") or last_cursor))
        return processed


async def health_check(state, event_log: EventLog, event_bus: EventBus,
                       notifier=None):
    """Periodic health check — runs every 30 seconds.

    Flags agents that appear stuck or have repeated errors.
    """
    while True:
        await asyncio.sleep(30)
        now = time.time()
        changed = False
        old_task_health = {
            tid: {
                "state": getattr(task, "health_state", "healthy") or "healthy",
                "source_task_id": (
                    task.health_details.get("source_task_id", "")
                    if isinstance(task.health_details, dict) else ""
                ),
            }
            for tid, task in state.board_tasks.items()
        }

        for cell in state.cells_with_awareness():
            if cell.status != "running":
                continue
            last_progress_at = _agent_progress_ts(state, cell)
            if last_progress_at == 0.0:
                continue  # no progress signal or task anchor yet

            # Fallback: unlink idle agent from post-derive parent task
            # (catches cases where the activity_change event was missed)
            if not cell.activity and cell.current_task_id:
                task = state.board_tasks.get(cell.current_task_id)
                if task and state.task_has_live_handoff_descendants(task.id):
                    log.info("Health check: auto-unlinking idle agent '%s' "
                             "from post-derive task '%s'",
                             cell.name, task.task[:60])
                    cell.current_task_id = ""
                    state._emit_agent(cell)
                    state._db_save_agent(cell)
                    changed = True

            timeout_min = cell.idle_timeout
            if timeout_min <= 0:
                continue  # idle timeout disabled for this group

            silence = now - last_progress_at
            timeout_sec = timeout_min * 60

            # No events for timeout while running and not waiting
            if silence > timeout_sec and cell.activity not in ("waiting",):
                if not cell.needs_attention:
                    cell.needs_attention = True
                    cell.error_message = (
                        f"No activity for {timeout_min} minute"
                        f"{'s' if timeout_min != 1 else ''}")
                    state._emit_agent(cell)
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
                    state._emit_agent(cell)
                    changed = True
                    if notifier:
                        notifier.on_health_alert(cell.id, msg)

        changed_task_ids = state.recompute_task_health(now_ts=now)
        if changed_task_ids:
            changed = True
            _emit_task_health_alerts(
                state,
                event_bus,
                changed_task_ids,
                old_task_health,
                notifier,
            )

        if check_engineer_queue_empty(state, event_bus, now=now):
            changed = True

        if changed:
            await state.broadcast()


def _emit_task_health_alerts(state, event_bus: EventBus,
                             changed_task_ids: list[str],
                             old_task_health: dict[str, dict],
                             notifier=None):
    panel_log = getattr(state, "panel_log", None) or getattr(
        event_bus, "_panel_log", None)
    if not panel_log:
        return

    pending_by_source: dict[str, object] = {}
    for tid in changed_task_ids:
        task = state.board_tasks.get(tid)
        if not task or task.lane in {"Done", "Archived"}:
            continue
        new_state = getattr(task, "health_state", "healthy") or "healthy"
        if new_state not in {
                "idle-risk", "stalled", "thrashing", "stale-in-progress"}:
            continue
        previous = old_task_health.get(tid, {})
        old_state = previous.get("state", "healthy") or "healthy"
        if new_state == old_state:
            continue
        if HEALTH_SEVERITY.get(new_state, 0) < HEALTH_SEVERITY.get(old_state, 0):
            continue
        details = task.health_details if isinstance(task.health_details, dict) else {}
        source_task_id = details.get("source_task_id", "") or task.id
        incumbent = pending_by_source.get(source_task_id)
        if incumbent is None:
            pending_by_source[source_task_id] = task
            continue
        incumbent_details = (
            incumbent.health_details if isinstance(incumbent.health_details, dict)
            else {}
        )
        incumbent_aggregate = bool(incumbent_details.get("aggregate"))
        current_aggregate = bool(details.get("aggregate"))
        incumbent_severity = HEALTH_SEVERITY.get(
            getattr(incumbent, "health_state", "healthy") or "healthy", 0)
        current_severity = HEALTH_SEVERITY.get(new_state, 0)
        if current_severity > incumbent_severity:
            pending_by_source[source_task_id] = task
        elif current_severity == incumbent_severity \
                and incumbent_aggregate and not current_aggregate:
            pending_by_source[source_task_id] = task

    for task in pending_by_source.values():
        evt = panel_log.append(
            kind="task_health_alert",
            cell_id="",
            agent_name=task.task[:80],
            group=task.group,
            message=_task_health_alert_message(task),
            task_id=task.id,
        )
        state._emit("event_append", **evt)
        if notifier:
            notifier.on_task_health_alert(task.id, task.health_state)


def _task_health_alert_message(task) -> str:
    details = task.health_details if isinstance(task.health_details, dict) else {}
    state_name = getattr(task, "health_state", "healthy") or "healthy"
    if state_name == "idle-risk":
        prefix = "idle risk"
    elif state_name == "stale-in-progress":
        prefix = "stale in progress"
    else:
        prefix = state_name

    parts = [prefix]
    silence_secs = details.get("silence_secs")
    if isinstance(silence_secs, int) and silence_secs > 0:
        parts.append(f"no progress for {_format_duration(silence_secs)}")
    elif "message_churn" in (details.get("reasons") or []):
        parts.append("recent progress/block churn")

    if details.get("aggregate") and details.get("source_task_title"):
        parts.append(f"via {details['source_task_title'][:40]}")

    return ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]


def _format_duration(seconds: int) -> str:
    minutes = max(1, seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if rem_minutes:
        return f"{hours}h {rem_minutes}m"
    return f"{hours}h"
