"""Background session-wake journaling for authenticated MCP callers.

This responsibility is separate from the streamable HTTP transport: it tracks a
first tool call for Architect and Engineer MCP sessions, records the recovery
checkpoint context without delaying that call, and owns its task lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger("torque")

_SESSION_WAKE_DEDUPE_SECS = 60.0
_SESSION_WAKE_SEEN: set[tuple[str, str]] = set()
_SESSION_WAKE_TASKS: set[asyncio.Task] = set()


def reset_session_wake_state() -> None:
    """Reset process-local session bookkeeping when the MCP facade reloads."""
    _SESSION_WAKE_SEEN.clear()
    _SESSION_WAKE_TASKS.clear()


def _extract_entry_timestamp(entry: dict | None) -> float:
    try:
        return float(((entry or {}).get("timestamp", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _format_session_wake_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def _format_session_wake_age(delta_secs: float) -> str:
    total = max(0, int(delta_secs or 0))
    if total < 60:
        return f"{total}s ago"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 8:
        return f"{weeks}w ago"
    months = days // 30
    if months < 24:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _session_wake_checkpoint_fragment(
    checkpoint_ts: float,
    first_tool_call_ts: float,
) -> str:
    if checkpoint_ts <= 0:
        return "previous checkpoint none recorded"
    return (
        "previous checkpoint "
        f"{_format_session_wake_timestamp(checkpoint_ts)} "
        f"({_format_session_wake_age(first_tool_call_ts - checkpoint_ts)})"
    )


def _session_wake_deduped(
    last_entry_ts: float,
    first_tool_call_ts: float,
) -> bool:
    if last_entry_ts <= 0 or first_tool_call_ts <= 0:
        return False
    return abs(last_entry_ts - first_tool_call_ts) <= _SESSION_WAKE_DEDUPE_SECS


def _latest_architect_checkpoint_timestamp(state, architect_id: str) -> float:
    for entry in state.architect_journal_read(architect_id, limit=1_000_000):
        if str((entry or {}).get("type", "") or "").strip() == "checkpoint":
            return _extract_entry_timestamp(entry)
    return 0.0


def _latest_engineer_checkpoint_timestamp(
    state,
    group: str,
    engineer_id: str,
) -> float:
    entries = state.journal_read(
        group,
        limit=1,
        entry_type="checkpoint",
        author_cell_id=engineer_id,
    )
    return _extract_entry_timestamp(entries[0] if entries else None)


async def _emit_session_wake_entry(
    state,
    *,
    cell_id: str,
    caller_kind: str,
    first_tool_call_ts: float,
) -> None:
    cell = state.agents.get(str(cell_id or "").strip())
    if not cell:
        return

    current_kind = str(getattr(cell, "kind", "") or "").strip()
    if current_kind != caller_kind or caller_kind not in {"architect", "engineer"}:
        return

    if caller_kind == "architect":
        refresh_peer_cache = getattr(
            state,
            "refresh_peer_message_cache_for_agent",
            None,
        )
        if callable(refresh_peer_cache):
            refresh_peer_cache(cell.id, emit=True)
        latest_entries = state.architect_journal_read(cell.id, limit=1)
        last_entry_ts = _extract_entry_timestamp(
            latest_entries[0] if latest_entries else None
        )
        if _session_wake_deduped(last_entry_ts, first_tool_call_ts):
            return
        group = str(getattr(cell, "group", "") or "").strip() or "(none)"
        entry = (
            "Session wake — "
            f"{_session_wake_checkpoint_fragment(
                _latest_architect_checkpoint_timestamp(state, cell.id),
                first_tool_call_ts,
            )}, architect id {cell.id}, group {group}."
        )
        state.architect_journal_append(cell.id, "observation", entry)
        return

    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return
    latest_entries = state.journal_read(
        group,
        limit=1,
        author_cell_id=cell.id,
    )
    last_entry_ts = _extract_entry_timestamp(
        latest_entries[0] if latest_entries else None
    )
    if _session_wake_deduped(last_entry_ts, first_tool_call_ts):
        return
    name = str(getattr(cell, "name", "") or "").strip() or "(unnamed)"
    slug = str(getattr(cell, "slug", "") or "").strip() or "(none)"
    entry = (
        "Session wake — "
        f"{_session_wake_checkpoint_fragment(
            _latest_engineer_checkpoint_timestamp(state, group, cell.id),
            first_tool_call_ts,
        )}, engineer id {cell.id}, name {name}, slug {slug}, group {group}."
    )
    state.journal_append(group, "observation", entry, author_cell_id=cell.id)


def _log_session_wake_failure(task: asyncio.Task) -> None:
    _SESSION_WAKE_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.exception("Background session wake journaling failed", exc_info=exc)


def _queue_session_wake_entry(
    state,
    *,
    cell_id: str,
    caller_kind: str,
    first_tool_call_ts: float,
) -> None:
    task_ref = asyncio.create_task(
        _emit_session_wake_entry(
            state,
            cell_id=cell_id,
            caller_kind=caller_kind,
            first_tool_call_ts=first_tool_call_ts,
        )
    )
    _SESSION_WAKE_TASKS.add(task_ref)
    task_ref.add_done_callback(_log_session_wake_failure)


def _claim_session_wake(cell_id: str, mcp_session_id: str) -> bool:
    cell_id = str(cell_id or "").strip()
    mcp_session_id = str(mcp_session_id or "").strip()
    if not cell_id or not mcp_session_id:
        return False
    key = (cell_id, mcp_session_id)
    if key in _SESSION_WAKE_SEEN:
        return False
    _SESSION_WAKE_SEEN.add(key)
    return True
