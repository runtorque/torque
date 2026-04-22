"""EventBus, EventLog, PanelEventLog, and health monitoring."""

import asyncio
import time
from datetime import datetime
from collections import deque
from dataclasses import asdict

from .adapters.base import AgentEvent
from .config import log
from .task_health import HEALTH_SEVERITY


PERSISTENT_CELL_EVENT_KINDS = {"architect", "engineer"}
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


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_weaver_id", "") or "").strip()


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
        state._db_save_agent(engineer)
        state._emit_agent(engineer)
        changed = True

    return changed


class PanelEventLog:
    """Global ring buffer of high-level events for the Events panel.

    Unlike EventLog (per-cell, low-level), this stores aggregate events
    visible across the UI: task dispatched, completed, ask created, etc.
    When a *db* is provided, events are persisted to SQLite and the
    in-memory deque acts as a write-through cache for the most recent
    ``_max_size`` entries.
    """

    def __init__(self, max_size: int = 500, db=None):
        self._max_size = max_size
        self._db = db
        self._events: deque[dict] = deque(maxlen=max_size)
        self.on_event = None  # callback(event_dict) — called on append/replace
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
            try:
                self._db.save_panel_event(evt)
                self._db.trim_panel_events(self._max_size)
            except Exception:
                log.exception("Failed to persist panel event %s", evt["id"])
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
                    try:
                        self._db.update_panel_event(evt)
                    except Exception:
                        log.exception("Failed to update panel event %s",
                                      evt["id"])
                return evt
        return self.append(kind=kind, cell_id=cell_id, **kwargs)

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
        self._weaver_buffer = None  # WeaverEventBuffer, set from server.py
        self._timers: dict[str, asyncio.TimerHandle] = {}
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
        log.info("Event: cell='%s' type=%s activity='%s' detail='%s'",
                 cell.name, event.event_type, cell.activity,
                 cell.activity_detail[:50] if cell.activity_detail else "")
        if self._notifier:
            self._notifier.on_event(event)
        # Notify weaver buffer of activity change (for idle-gated delivery)
        if self._weaver_buffer:
            self._weaver_buffer.on_agent_activity_change(cell)
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

    def _apply(self, event: AgentEvent, cell):
        """Update AgentCell fields based on event type."""
        et = event.event_type
        d = event.data
        if et in PROGRESS_EVENT_TYPES:
            cell.mark_progress(event.timestamp)
        else:
            cell.mark_heartbeat(event.timestamp)

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
                if task and "loom:blocked" in task.labels:
                    task.labels.remove("loom:blocked")
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
            # (e.g. loom ai blocked/error runs inside this tool call)
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

        # Emit panel event for key types
        if self._panel_log and et in (
                "session_start", "session_end", "error", "waiting"):
            kind_map = {
                "session_start": "agent_started",
                "session_end": "agent_finished",
                "error": "agent_error",
                "waiting": "agent_waiting",
            }
            msg = ""
            if et == "error":
                msg = d.get("error", "Unknown error")
            elif et == "session_end":
                msg = d.get("summary", "") or "Session ended"
            elif et == "waiting":
                msg = d.get("reason", "") or "Waiting for permission"
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
