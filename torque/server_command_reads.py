"""Direct command read models, journals, and MCP observation queries."""

from __future__ import annotations

import time
import uuid

from .commands.ai_reports import (
    TORQUE_AI_MCP_REPORT_TOOL_NAMES as _TORQUE_AI_MCP_REPORT_TOOL_NAMES,
)
from .config import log
from .event_ingest_db import event_call_row_from_record, redact_event_for_mcp_call_log
from .events import build_event_ingest_envelope
from .server_agent_common import _resolve_agent_id
from .state import MatrixState


def _handle_task_detail_command(data: dict, state: MatrixState) -> dict:
    """Return one full BoardTask dict for compact snapshot lazy-loading."""
    task_id = str(data.get("id", "") or data.get("task_id", "") or "").strip()
    if not task_id:
        return {"type": "error", "message": "task id required"}
    task = state.get_task_detail(task_id)
    if not task:
        return {"type": "error", "message": "Task not found"}
    return {
        "type": "task_detail",
        "id": task["id"],
        "task": task,
    }
def _handle_agent_message_history_command(
        data: dict, state: MatrixState) -> dict:
    """Return per-agent user-message recall history, newest first."""
    agent_id = str(
        data.get("agent_id", "") or data.get("cell_id", "") or data.get("id", "")
        or ""
    ).strip()
    if not agent_id:
        return {"type": "error", "message": "agent_id required"}
    try:
        limit = min(int(data.get("limit", 100)), 1000)
    except (TypeError, ValueError):
        limit = 100
    return {
        "type": "agent_message_history",
        "agent_id": agent_id,
        "history": state.agent_message_history_read(agent_id, limit=limit),
    }



def _handle_decisions_snapshot_command(data: dict, state: MatrixState) -> dict:
    """Return deferred architect decisions for compact snapshot clients."""
    include_archived = bool(data.get("include_archived", False))
    decisions = {
        decision["id"]: decision
        for decision in state.load_all_decisions(
            include_archived=include_archived,
        )
    }
    return {
        "type": "decisions_snapshot",
        "include_archived": include_archived,
        "decisions": decisions,
    }


def _handle_pending_hires_snapshot_command(data: dict,
                                           state: MatrixState) -> dict:
    """Return deferred pending hires for compact snapshot clients."""
    status_filter = str(
        data.get("status_filter", data.get("status", "pending")) or ""
    ).strip()
    architect_id = str(data.get("architect_id", "") or "").strip()
    pending_hires = {
        pending_hire["id"]: pending_hire
        for pending_hire in state.load_pending_hires(
            status_filter=status_filter,
            architect_id=architect_id,
        )
    }
    return {
        "type": "pending_hires_snapshot",
        "pending_hires": pending_hires,
    }


def _handle_archived_tasks_command(data: dict, state: MatrixState) -> dict:
    """Return archived tasks on demand, excluded from compact initial state."""
    group = str(data.get("group", "") or "").strip()
    return {
        "type": "archived_tasks",
        "group": group,
        "board_tasks": state.get_archived_task_details(group=group),
    }


def _architect_ui_tool_is_read(name: str) -> bool:
    return str(name or "").strip() in {
        "architect_decision_list",
        "architect_task_list",
        "architect_peer_list",
        "architect_peer_inbox",
    }


async def _handle_engineer_journal_snapshot_command(
        data: dict,
        state: MatrixState) -> dict:
    """Return deferred Engineer journal/worklog/stream snapshots.

    Journal entries are author-keyed (`engineer_journal[cell_id]`) while the
    still group-wide worklog/stream slices remain keyed by group.
    """
    group = str(data.get("group", "") or "").strip()
    if not group:
        return {"type": "error", "message": "group required"}
    try:
        limit = int(data.get("limit", 50) or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        worklog_limit = int(data.get("worklog_limit", 200) or 200)
    except (TypeError, ValueError):
        worklog_limit = 200
    worklog_limit = max(1, min(worklog_limit, 500))

    streams = {
        "count": 0,
        "by_state": {},
        "items": [],
        "truncated": False,
    }
    if bool(data.get("include_streams", True)):
        try:
            from .worktree_streams import prefill_branch_exists_for_state
            await prefill_branch_exists_for_state(state)
            streams = state._engineer_stream_payload(group)
        except Exception:
            log.exception("Failed to load engineer streams for %s", group)

    engineer_id = str(
        data.get("engineer_id") or data.get("cell_id") or ""
    ).strip()
    if engineer_id:
        engineer_journal = {
            engineer_id: state.journal_read(
                group,
                limit=limit,
                author_cell_id=engineer_id,
            ),
        }
    else:
        engineer_journal = state.engineer_journal_snapshot_by_author(
            group=group,
            limit=limit,
        )

    return {
        "type": "engineer_journal_snapshot",
        "group": group,
        "engineer_journal": engineer_journal,
        "engineer_worklog": {
            group: [
                dict(entry)
                for entry in list(state.engineer_worklog.get(group, []))[:worklog_limit]
            ],
        },
        "engineer_streams": {
            group: streams,
        },
    }


def _handle_architect_journal_read_command(
        data: dict,
        state: MatrixState) -> dict:
    """Return recent architect journal entries for the standalone UI."""
    architect_id = str(data.get("architect_id", "") or "").strip()
    if not architect_id:
        return {"type": "error", "message": "architect_id required"}
    try:
        limit = int(data.get("limit", 200) or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 500))
    try:
        since = float(data.get("since", 0) or 0)
    except (TypeError, ValueError):
        since = 0.0
    entries = state.architect_journal_read(
        architect_id,
        since=since,
        limit=limit,
    )
    return {
        "type": "architect_journal_entries",
        "architect_id": architect_id,
        "limit": limit,
        "since": since,
        "entries": entries,
    }


def _event_ingest_config_payload(state: MatrixState) -> dict:
    gs = state.global_settings
    return {
        "max_rows": int(getattr(gs, "event_ingest_max_rows", 100_000) or 100_000),
        "max_age_days": int(getattr(gs, "event_ingest_max_days", 14) or 0),
        "args_capture": str(
            getattr(gs, "mcp_call_log_args_capture", "metadata") or "metadata"
        ),
        "full_capture_tools": list(
            getattr(gs, "mcp_call_log_full_capture_tools", []) or []
        ),
    }


async def _configure_event_ingest_client(event_ingest_client, state: MatrixState) -> None:
    if not event_ingest_client:
        return
    response = await event_ingest_client.configure(**_event_ingest_config_payload(state))
    if response.get("type") != "ok" or response.get("op") != "configure":
        raise RuntimeError(
            "event-ingest configure failed: "
            f"{response.get('message') or response!r}"
        )


def _mcp_observation_event_id(observation: dict) -> str:
    cell_id = str(observation.get("cell_id") or "").strip()
    tool_name = str(observation.get("tool_name") or "").strip()
    idempotency_key = str(observation.get("idempotency_key") or "").strip()
    session_id = str(observation.get("session_id") or "").strip()
    request_id = str(observation.get("request_id") or "").strip()
    if idempotency_key:
        return f"mcp-direct:{cell_id}:{tool_name}:{idempotency_key}"
    if session_id or request_id:
        return f"mcp-direct:{cell_id}:{tool_name}:{session_id}:{request_id}:{uuid.uuid4().hex}"
    return f"mcp-direct:{cell_id}:{tool_name}:{uuid.uuid4().hex}"


async def _record_mcp_call_observation(
    state: MatrixState,
    event_ingest_client,
    observation: dict,
    *,
    ensure_configured=None,
) -> None:
    """Persist and live-emit direct /mcp calls for Agent Events > MCP.

    Claude-style hook envelopes still flow through ``/events``. Codex and
    other clients that call Torque's MCP endpoint directly do not have that
    provider hook, so this records the same UI shape without changing tool
    authority or the redaction policy used by the existing MCP call log.
    """
    if not event_ingest_client or not isinstance(observation, dict):
        return
    tool_name = str(observation.get("tool_name") or "").strip()
    cell_id = str(observation.get("cell_id") or "").strip()
    if not tool_name.startswith("mcp__") or not cell_id:
        return
    cell = state.agents.get(cell_id)
    if str(getattr(cell, "agent_type", "") or "").strip() == "claude-code":
        # Claude Code already supplies PostToolUse envelopes to /events. The
        # direct /mcp observer exists for clients without that hook path
        # (notably Codex); recording Claude calls here would duplicate both
        # historical rows and live deltas from the established hook stream.
        return

    is_error = bool(observation.get("is_error"))
    result = observation.get("result")
    error_text = ""
    if is_error and isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0] if isinstance(content[0], dict) else {}
            error_text = str(first.get("text") or "")[:500]
    raw = {
        "hook_event_name": str(
            observation.get("hook_event_name") or "PostToolUse"
        ),
        "tool_name": tool_name,
        "session_id": str(observation.get("session_id") or ""),
        "duration_ms": observation.get("duration_ms"),
        "success": not is_error,
        "tool_input": observation.get("arguments", {}),
        "tool_response": result,
    }
    if error_text:
        raw["error"] = error_text
    envelope = build_event_ingest_envelope(
        raw,
        headers={"X-Torque-Cell-Id": cell_id},
    )
    event_id = _mcp_observation_event_id(observation)
    try:
        if ensure_configured:
            await ensure_configured()
        response = await event_ingest_client.append(
            envelope,
            idempotency_key=event_id,
        )
    except Exception as exc:
        log.exception("Failed to persist direct MCP call observation")
        db = getattr(state, "db", None)
        if db and hasattr(db, "record_mcp_health_event_safe"):
            db.record_mcp_health_event_safe(
                surface="mcp",
                event="drop",
                tool_name=tool_name,
                error=str(exc) or type(exc).__name__,
            )
        return
    if response.get("type") == "error":
        message = str(response.get("message") or "event ingest error")
        log.warning("Failed to persist direct MCP call observation: %s", message)
        db = getattr(state, "db", None)
        if db and hasattr(db, "record_mcp_health_event_safe"):
            db.record_mcp_health_event_safe(
                surface="mcp",
                event="drop",
                tool_name=tool_name,
                error=message,
            )
        return
    if response.get("duplicate"):
        return

    # Worker report tools already emit a coalesced live delta from ai_report.
    # Persist the direct MCP observation for history, but do not reintroduce
    # the second live MCP delta that TORQUE:236 removed.
    if tool_name in _TORQUE_AI_MCP_REPORT_TOOL_NAMES:
        return

    redacted_envelope = redact_event_for_mcp_call_log(
        envelope,
        args_capture=state.global_settings.mcp_call_log_args_capture,
        full_capture_tools=state.global_settings.mcp_call_log_full_capture_tools,
    )
    rows = _mcp_call_rows_for_ui(state, [{
        "cursor": int(response.get("cursor") or 0),
        "idempotency_key": event_id,
        "event": redacted_envelope,
        "appended_at": time.time(),
    }])
    if not rows:
        return
    state._emit(
        "mcp_call_append",
        group=rows[0].get("group", ""),
        call=rows[0],
    )
    await state.broadcast()


def _mcp_call_rows_for_ui(state: MatrixState, records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        row = event_call_row_from_record(record)
        cell = state.agents.get(row.get("cell_id", ""))
        if cell:
            row["agent_name"] = cell.name
            row["agent_slug"] = cell.slug
            row["agent_kind"] = getattr(cell, "kind", "")
            row["group"] = getattr(cell, "group", "")
        rows.append(row)
    return rows


def _engineer_mcp_visible_cell_ids(state: MatrixState, engineer_id: str) -> set[str]:
    engineer_id = str(engineer_id or "").strip()
    engineer = state.get_active_agent(engineer_id)
    if not engineer:
        return set()
    group = str(getattr(engineer, "group", "") or "").strip()
    visible = {engineer_id}
    for cell in state.iter_active_agents():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if group and str(getattr(cell, "group", "") or "").strip() != group:
            continue
        owner = str(getattr(cell, "owner_engineer_id", "") or "").strip()
        creator = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
        if owner == engineer_id or creator == engineer_id:
            visible.add(cell.id)
    return visible


def _architect_mcp_visible_cell_ids(state: MatrixState, architect_id: str) -> set[str]:
    architect_id = str(architect_id or "").strip()
    architect = state.agents.get(architect_id)
    if not architect:
        return set()
    group = str(getattr(architect, "group", "") or "").strip()
    return {
        cell.id for cell in state.iter_active_agents()
        if getattr(cell, "cell_type", "") == "agent"
        and str(getattr(cell, "group", "") or "").strip() == group
    }


def _parse_mcp_call_query_params(data: dict) -> dict:
    try:
        limit = int(data.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))
    def _maybe_float(name):
        value = data.get(name)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    tool_pattern = (
        data.get("tool_name_pattern")
        or data.get("tool_filter")
        or "mcp__torque__%"
    )
    hook = str(data.get("hook_event_name") or data.get("hook") or "").strip()
    return {
        "tool_name_pattern": str(tool_pattern or "").strip(),
        "hook_event_name": hook,
        "since": _maybe_float("since"),
        "until": _maybe_float("until"),
        "limit": limit,
    }


async def _handle_mcp_calls_command(
    data: dict,
    state: MatrixState,
    event_ingest_client,
    *,
    scope: str = "trusted",
) -> dict:
    params = _parse_mcp_call_query_params(data)
    agent_ident = str(
        data.get("agent_id")
        or data.get("cell_id")
        or data.get("agent")
        or ""
    ).strip()
    requested_cell_id = _resolve_agent_id(state, agent_ident) if agent_ident else ""
    allowed_cell_ids: set[str] | None = None
    caller_id = str(data.get("caller_id") or data.get("_caller_id") or "").strip()
    if scope == "architect":
        allowed_cell_ids = _architect_mcp_visible_cell_ids(state, caller_id)
        if not allowed_cell_ids:
            return {"type": "error", "message": "architect not found"}
    elif scope == "engineer":
        allowed_cell_ids = _engineer_mcp_visible_cell_ids(state, caller_id)
        if not allowed_cell_ids:
            return {"type": "error", "message": "engineer not found"}

    query_cell_id = requested_cell_id
    query_cell_ids: list[str] | None = None
    if allowed_cell_ids is not None:
        if requested_cell_id:
            if requested_cell_id not in allowed_cell_ids:
                return {"type": "mcp_calls", "calls": [], "events": [], "limit": params["limit"]}
            query_cell_id = requested_cell_id
        else:
            query_cell_id = ""
            query_cell_ids = sorted(allowed_cell_ids)
    elif not query_cell_id and agent_ident:
        return {"type": "error", "message": f"Agent not found: {agent_ident}"}

    try:
        response = await event_ingest_client.query(
            cell_id=query_cell_id or None,
            cell_ids=query_cell_ids,
            **params,
        )
    except Exception as exc:
        log.exception("Failed to query event ingest MCP calls")
        return {"type": "error", "message": str(exc) or "event ingest unavailable"}
    if response.get("type") == "error":
        return {"type": "error", "message": response.get("message") or "query failed"}
    records = list(response.get("events") or [])
    rows = _mcp_call_rows_for_ui(state, records)
    return {
        "type": "mcp_calls",
        "cell_id": requested_cell_id or query_cell_id,
        "agent_id": requested_cell_id or query_cell_id,
        "scope": scope,
        "tool_name_pattern": params["tool_name_pattern"],
        "hook_event_name": params["hook_event_name"],
        "since": params["since"],
        "until": params["until"],
        "limit": params["limit"],
        "calls": rows,
        "events": rows,
        "settings": {
            "mcp_call_log_args_capture": state.global_settings.mcp_call_log_args_capture,
        },
    }
