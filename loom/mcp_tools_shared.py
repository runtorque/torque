"""Shared MCP tool implementation for engineer-scoped orchestration tools.

This module contains the shared read/write tool logic used by the
``engineer_*`` and ``architect_*`` namespaces. Scoping is caller-driven
via ``caller_kind`` + ``caller_id``.

Security note: v1 keeps Loom's local-trust model. Environment/header
spoofing protections are out of scope for this stage. The server HTTP
command surface is user-operated and trusted; cross-kind communication
graph enforcement lives in these MCP tool surfaces.
"""

import copy
import json
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone

from .config import log
from .mcp_weaver_tools.shared import (
    active_worker_ids as _active_worker_ids,
    blocked_dependency_titles as _blocked_dependency_titles,
    format_worktree_conflicts as _format_worktree_conflicts,
    resolve_agent as _resolve_agent,
    resolve_task as _resolve_task,
    run_worktree_merge_check as _run_worktree_merge_check,
    run_worktree_merge_check_with_options as _run_worktree_merge_check_with_options,
    worktree_boundary_overview as _worktree_boundary_overview,
    is_busy_agent as _is_busy_agent,
)
from .server_artifacts import serialize_task_for_mcp
from .state import (
    ARCHIVED_LANE,
    board_task_is_closed,
    get_weaver_notification_preset,
    normalize_default_worker_concurrency,
    normalize_weaver_digest_verbosity,
)
from .task_health import HEALTH_SEVERITY
from .weaver_hints import compute_weaver_hints
from .weaver_session_map import build_weaver_session_map
from .worktree_streams import compute_worktree_streams, member_task_ids_for_stream

_STREAM_STATES = (
    "implementing",
    "reviewing",
    "fixing_blockers",
    "awaiting_human_validation",
    "ready_to_merge",
    "merged",
)
_DECISION_STATUSES = {"proposed", "accepted", "revised", "rejected"}
_JOURNAL_ENTRY_TYPES = {"decision", "observation", "checkpoint", "plan"}
_HEALTH_SUMMARY_SILENT_AFTER_SECS = 5 * 60
_HEALTH_SUMMARY_LIMIT = 120
_ARCHITECT_BOARD_SUMMARY_TASK_LIMIT = 20
_ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT = 120
_ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT = 10_000

# ---------------------------------------------------------------------------
# Shared scoping helpers
# ---------------------------------------------------------------------------


def engineer_not_found_error(caller_id: str) -> str:
    return json.dumps({
        "type": "error",
        "message": f"no engineer with id={caller_id} exists",
    })


def normalize_tool_name(name: str, tool_prefix: str) -> str:
    prefix = str(tool_prefix or "").strip()
    if prefix and str(name or "").startswith(prefix):
        return str(name)[len(prefix):]
    return str(name or "")


def tool_name_with_prefix(tool_prefix: str, suffix: str) -> str:
    return f"{str(tool_prefix or '').rstrip('_')}_{suffix}"


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_weaver_id", "") or "").strip()


def _effective_assigned_engineer_id(task) -> str:
    return str(getattr(task, "assigned_engineer_id", "") or "").strip()


def _task_created_by_classifier(task) -> str:
    # Synthesized to avoid redundant storage; parent/pipeline refs are the derive/ask "system" heuristic.
    architect_id = str(
        getattr(task, "created_by_architect_id", "") or ""
    ).strip()
    if architect_id:
        return f"architect:{architect_id}"
    engineer_id = str(
        getattr(task, "created_by_engineer_id", "") or ""
    ).strip()
    if engineer_id:
        return f"engineer:{engineer_id}"
    if (
        str(getattr(task, "parent_task_id", "") or "").strip()
        or str(getattr(task, "pipeline_root_id", "") or "").strip()
    ):
        return "system"
    return "user"


def _summary_task_title(task) -> str:
    """Return a bounded first-line title for compact board summaries."""
    title = str(getattr(task, "task", "") or "").strip()
    if not title:
        return ""
    first_line = title.splitlines()[0].strip()
    if len(first_line) <= _ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT:
        return first_line
    return first_line[:_ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT - 1].rstrip() + "…"


def _architect_board_summary_task_item(task, *, created_by: str) -> dict:
    return {
        "id": task.id,
        "slug": task.slug,
        "title": _summary_task_title(task),
        "lane": task.lane,
        "labels": task.labels or [],
        "status": task.status,
        "assigned_engineer_id": _effective_assigned_engineer_id(task),
        "created_by": created_by,
        "health_state": getattr(task, "health_state", "healthy") or "healthy",
        "updated_at": getattr(task, "updated_at", "") or "",
    }


def _compact_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _architect_board_summary_json(summary: dict, task_items: list[dict]) -> str:
    """Attach a bounded task excerpt and keep architect summaries under budget."""
    count = len(task_items)
    limit = min(count, _ARCHITECT_BOARD_SUMMARY_TASK_LIMIT)
    while True:
        summary["tasks"] = {
            "count": count,
            "items": task_items[:limit],
            "truncated": count > limit,
        }
        text = _compact_json(summary)
        if len(text) <= _ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT or limit <= 0:
            return text
        limit -= 1


def _is_engineer_like_cell(state, cell) -> bool:
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return False
    return str(getattr(cell, "kind", "") or "").strip() == "engineer"


def _is_architect_cell(cell) -> bool:
    return bool(
        cell
        and getattr(cell, "cell_type", "") == "agent"
        and str(getattr(cell, "kind", "") or "").strip() == "architect"
    )


def _agent_dismissed_at(cell) -> int:
    try:
        return int(getattr(cell, "dismissed_at", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _engineer_dismissed_error(engineer_id: str) -> str:
    return json.dumps({
        "type": "error",
        "reason": "engineer_dismissed",
        "message": f"engineer {engineer_id} is dismissed",
        "engineer_id": str(engineer_id or "").strip(),
    })


def _caller_group(state, caller_id: str) -> str:
    caller = state.agents.get(str(caller_id or "").strip())
    return str(getattr(caller, "group", "") or "").strip() if caller else ""


def _dedupe_strings(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _architect_visible_engineers(state, caller_id: str) -> dict[str, tuple[object, str]]:
    caller_group = _caller_group(state, caller_id)
    if not caller_group:
        return {}
    visible = {}
    for cell in state.agents.values():
        if not _is_engineer_like_cell(state, cell):
            continue
        if str(getattr(cell, "group", "") or "").strip() != caller_group:
            continue
        hired_by = str(getattr(cell, "hired_by_architect_id", "") or "").strip()
        if hired_by == str(caller_id or "").strip():
            visible[cell.id] = (cell, "hired")
        elif not hired_by:
            visible[cell.id] = (cell, "visible")
    return visible


def _architect_hired_engineer_ids(state, caller_id: str) -> set[str]:
    return {
        engineer_id
        for engineer_id, (_cell, relation) in _architect_visible_engineers(
            state, caller_id
        ).items()
        if relation == "hired"
    }


def _resolve_architect_engineer(state, caller_id: str,
                                engineer_ident: str) -> tuple[str | None, str]:
    engineer_id = _resolve_agent(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    visible = _architect_visible_engineers(state, caller_id)
    if engineer_id not in visible:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _resolve_architect_hired_engineer(state, caller_id: str,
                                      engineer_ident: str) -> tuple[str | None, str]:
    engineer_id = _resolve_agent(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    if engineer_id not in _architect_hired_engineer_ids(state, caller_id):
        return None, "engineer not found in scope"
    return engineer_id, ""


def _normalize_decision_links(state, caller_id: str, *,
                              task_ids=None,
                              engineer_ids=None) -> tuple[list[str], list[str], str]:
    visible_tasks = _filter_tasks_for_caller(state, "architect", caller_id)
    normalized_task_ids = []
    for task_ident in _dedupe_strings(task_ids):
        task_id = _resolve_task(state, task_ident)
        if not task_id or task_id not in visible_tasks:
            return [], [], f"Task not found: {task_ident}"
        normalized_task_ids.append(task_id)

    normalized_engineer_ids = []
    for engineer_ident in _dedupe_strings(engineer_ids):
        engineer_id, error_text = _resolve_architect_engineer(
            state, caller_id, engineer_ident
        )
        if not engineer_id:
            return [], [], error_text
        normalized_engineer_ids.append(engineer_id)

    return normalized_task_ids, normalized_engineer_ids, ""


def _load_architect_decision(state, caller_id: str,
                             decision_id: str) -> tuple[dict | None, str]:
    decision_id = str(decision_id or "").strip()
    if not decision_id:
        return None, "decision id is required"
    decision = state.load_decision(decision_id)
    if not decision or str(decision.get("architect_id", "") or "").strip() != str(
        caller_id or ""
    ).strip():
        return None, "Decision not found"
    return decision, ""


def _load_architect_pending_hire(state, caller_id: str,
                                 hire_id: str) -> tuple[dict | None, str]:
    hire_id = str(hire_id or "").strip()
    if not hire_id:
        return None, "hire_id is required"
    pending_hire = state.load_pending_hire(hire_id)
    if not pending_hire or str(
            pending_hire.get("architect_id", "") or "").strip() != str(
                caller_id or "").strip():
        return None, "Pending hire not found"
    return pending_hire, ""


def _resolve_architect_for_engineer(state, caller_id: str,
                                    architect_ident: str) -> tuple[object | None, str]:
    architect_id = _resolve_agent(state, architect_ident)
    if not architect_id:
        return None, f"Architect not found: {architect_ident}"
    architect = state.agents.get(architect_id)
    if not _is_architect_cell(architect):
        return None, f"Architect not found: {architect_ident}"
    caller = state.agents.get(str(caller_id or "").strip())
    if not caller:
        return None, f"Engineer not found: {caller_id}"
    hired_by = str(getattr(caller, "hired_by_architect_id", "") or "").strip()
    if not hired_by:
        return None, "engineer is not hired by an architect"
    if architect.id != hired_by:
        return None, "architect not found in scope"
    return architect, ""


def _append_cross_kind_message(cell, entry: dict) -> None:
    if not cell:
        return
    cell.mcp_messages.insert(0, dict(entry))
    if len(cell.mcp_messages) > 20:
        cell.mcp_messages[:] = cell.mcp_messages[:20]


def mark_cross_kind_message_delivery(cell, message_id: str, *,
                                     delivered: bool,
                                     reason: str = "") -> bool:
    """Update the recipient-side cross-kind inbox delivery marker."""
    if not cell:
        return False
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    updated = False
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if str((entry or {}).get("id", "") or "").strip() != message_id:
            continue
        entry["delivered"] = bool(delivered)
        entry["buffered"] = not bool(delivered)
        if reason:
            entry["delivery_reason"] = str(reason or "").strip()
        elif "delivery_reason" in entry:
            entry.pop("delivery_reason", None)
        updated = True
        break
    return updated


def _load_message_entry(cell, message_id: str) -> tuple[dict | None, str]:
    message_id = str(message_id or "").strip()
    if not message_id:
        return None, "message_id is required"
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if str((entry or {}).get("id", "") or "").strip() == message_id:
            return dict(entry), ""
    return None, "Message not found"


def _deliver_architect_engineer_message(state, sender, recipient, *,
                                        action: str, message: str,
                                        reply_to_id: str = "",
                                        thread_id: str = "") -> dict:
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    timestamp = time.time()
    message_id = "msg-" + uuid.uuid4().hex[:12]
    conversation_id = str(thread_id or "").strip() or message_id
    reply_to = str(reply_to_id or "").strip()
    shared = {
        "id": message_id,
        "thread_id": conversation_id,
        "reply_to_id": reply_to,
        "action": action,
        "message": message_text,
        "timestamp": timestamp,
        "sender_id": sender.id,
        "sender_kind": str(getattr(sender, "kind", "") or "").strip(),
    }
    sender_entry = dict(shared)
    sender_entry.update({
        "peer_id": recipient.id,
        "peer_kind": str(getattr(recipient, "kind", "") or "").strip(),
        "direction": "sent",
    })
    recipient_entry = dict(shared)
    recipient_entry.update({
        "peer_id": sender.id,
        "peer_kind": str(getattr(sender, "kind", "") or "").strip(),
        "direction": "received",
    })
    log.debug(
        "architect-engineer message action=%s sender=%s recipient=%s thread=%s",
        action,
        sender.id,
        recipient.id,
        conversation_id,
    )
    _append_cross_kind_message(sender, sender_entry)
    _append_cross_kind_message(recipient, recipient_entry)
    state.history_record_message(sender.id, action, message_text)
    state.history_record_message(recipient.id, action, message_text)
    if str(getattr(recipient, "kind", "") or "").strip() == "engineer":
        recipient.pending_weaver_message = True
    if str(getattr(sender, "kind", "") or "").strip() == "engineer":
        sender.pending_weaver_message = False
    state._emit_agent(sender)
    state._emit_agent(recipient)
    return shared


async def _inject_mcp_message(handle_command, sender, recipient,
                              delivered: dict, message: str) -> None:
    """Ask the server to type the message into the recipient's terminal."""
    if not recipient or not handle_command:
        return
    try:
        result = await handle_command({
            "cmd": "inject_mcp_message",
            "agent_id": getattr(recipient, "id", ""),
            "message": message,
            "sender_name": str(getattr(sender, "name", "") or "").strip(),
            "sender_kind": str(getattr(sender, "kind", "") or "").strip(),
            "message_id": str(delivered.get("id", "") or ""),
        })
        was_delivered = bool(result and result.get("delivered"))
        reason = str((result or {}).get("reason", "") or "")
        mark_cross_kind_message_delivery(
            recipient,
            str(delivered.get("id", "") or ""),
            delivered=was_delivered,
            reason=reason,
        )
    except Exception:
        mark_cross_kind_message_delivery(
            recipient,
            str(delivered.get("id", "") or ""),
            delivered=False,
            reason="inject_failed",
        )
        log.exception(
            "Failed to inject MCP message into agent %s",
            getattr(recipient, "id", ""),
        )


def _visible_agent_ids_for_caller(state, caller_kind: str,
                                  caller_id: str) -> set[str]:
    if caller_kind == "architect":
        caller_id = str(caller_id or "").strip()
        visible = {caller_id} if caller_id in state.agents else set()
        visible.update(_architect_hired_engineer_ids(state, caller_id))
        return visible
    if caller_kind != "engineer":
        return set()
    visible = set()
    caller_id = str(caller_id or "").strip()
    for cell in state.agents.values():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "id", "") or "").strip() == caller_id:
            visible.add(cell.id)
            continue
        if _effective_owner_engineer_id(cell) == caller_id:
            visible.add(cell.id)
    return visible


def _filter_agents_for_caller(state, caller_kind: str,
                              caller_id: str) -> dict[str, object]:
    visible_agent_ids = _visible_agent_ids_for_caller(
        state, caller_kind, caller_id
    )
    filtered = {}
    for cell in state.agents.values():
        if getattr(cell, "cell_type", "") == "agent":
            if cell.id in visible_agent_ids:
                filtered[cell.id] = cell
            continue
        parent_id = str(getattr(cell, "parent_id", "") or "").strip()
        if (
            _effective_owner_engineer_id(cell) == str(caller_id or "").strip()
            or parent_id in visible_agent_ids
        ):
            filtered[cell.id] = cell
    return filtered


def _filter_tasks_for_caller(state, caller_kind: str,
                             caller_id: str) -> dict[str, object]:
    if caller_kind == "architect":
        caller_id = str(caller_id or "").strip()
        caller_group = _caller_group(state, caller_id)
        if not caller_group:
            return {}
        filtered = {}
        for task in state.board_tasks.values():
            if str(getattr(task, "group", "") or "").strip() != caller_group:
                continue
            filtered[task.id] = task
        return filtered
    if caller_kind != "engineer":
        return {}
    caller_id = str(caller_id or "").strip()
    caller = state.agents.get(caller_id)
    caller_group = str(getattr(caller, "group", "") or "").strip() if caller else ""
    if not caller_group:
        return {}
    filtered = {}
    for task in state.board_tasks.values():
        if str(getattr(task, "group", "") or "").strip() != caller_group:
            continue
        assigned_engineer_id = _effective_assigned_engineer_id(task)
        if not assigned_engineer_id or assigned_engineer_id == caller_id:
            filtered[task.id] = task
    return filtered


def _state_agent_visible_to_caller(state, caller_kind: str, caller_id: str,
                                   agent_id: str) -> bool:
    return str(agent_id or "").strip() in _visible_agent_ids_for_caller(
        state, caller_kind, caller_id
    )


def _resolve_visible_agent(state, caller_kind: str, caller_id: str,
                           agent_ident: str) -> tuple[str | None, str]:
    agent_id = _resolve_agent(state, agent_ident)
    if not agent_id:
        return None, f"Agent not found: {agent_ident}"
    if not _state_agent_visible_to_caller(state, caller_kind, caller_id, agent_id):
        if caller_kind == "engineer":
            return None, "agent not found in scope"
        return None, f"Agent not found: {agent_ident}"
    return agent_id, ""


def authorize_caller(state, *, caller_kind: str, caller_id: str):
    caller_id = str(caller_id or "").strip()
    label = {
        "engineer": "engineer",
        "architect": "architect",
    }.get(caller_kind, caller_kind)
    if caller_kind not in {"engineer", "architect"}:
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": f"unsupported caller kind: {caller_kind}",
        }), True
    if not caller_id:
        missing_message = (
            "LOOM_ENGINEER_ID is required"
            if caller_kind == "engineer"
            else "LOOM_ARCHITECT_ID is required"
            if caller_kind == "architect"
            else "caller id is required"
        )
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": missing_message,
        }), True
    cell = state.agents.get(caller_id)
    if caller_kind == "architect":
        valid = _is_architect_cell(cell)
    else:
        valid = _is_engineer_like_cell(state, cell)
    if not valid:
        error_text = (
            json.dumps({
                "type": "error",
                "message": f"no architect with id={caller_id} exists",
            })
            if caller_kind == "architect"
            else
            engineer_not_found_error(caller_id)
        )
        return None, "", caller_kind, error_text, True
    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": f"{label} {caller_id} is not assigned to a group",
        }), True
    return cell, group, caller_kind, "", False


def build_scoped_state_view(state, *, caller_kind: str, caller_id: str,
                            caller_cell, caller_group: str):
    if caller_kind == "architect":
        visible_agents = {
            cell_id: cell
            for cell_id, cell in state.agents.items()
            if str(getattr(cell, "group", "") or "").strip() == caller_group
        }
    else:
        visible_agents = _filter_agents_for_caller(state, caller_kind, caller_id)
    visible_tasks = _filter_tasks_for_caller(state, caller_kind, caller_id)
    visible_agent_ids = {
        cell_id for cell_id, cell in visible_agents.items()
        if getattr(cell, "cell_type", "") == "agent"
    }
    scoped_agents = dict(visible_agents)

    view_state = copy.copy(state)
    view_state.agents = scoped_agents
    view_state.board_tasks = dict(visible_tasks)
    scoped_agent_ids = {
        cell_id for cell_id, cell in scoped_agents.items()
        if getattr(cell, "cell_type", "") == "agent"
    }
    view_state.groups = {
        group_name: [
            agent_id for agent_id in agent_ids
            if agent_id in scoped_agent_ids
        ]
        for group_name, agent_ids in state.groups.items()
    }
    if caller_group and caller_group not in view_state.groups:
        view_state.groups[caller_group] = []
        view_state._children = {
            parent_id: [
                child_id for child_id in child_ids
                if child_id in view_state.agents
            ]
            for parent_id, child_ids in getattr(state, "_children", {}).items()
        if parent_id in scoped_agent_ids
    }
    view_state.group_settings = dict(getattr(state, "group_settings", {}))
    if caller_group:
        group_settings = state.get_group_settings(caller_group)
        view_state.group_settings[caller_group] = replace(
            group_settings,
            weaver_agent_id=str(getattr(caller_cell, "id", "") or ""),
        )
    view_state.agent_is_visible_to_weaver = (
        lambda _weaver_id, agent_id: str(agent_id or "").strip()
        in visible_agent_ids
    )
    view_state.weaver_restricts_to_created_agents = lambda _group: False
    return view_state


def _agent_visible_to_weaver(state, weaver_cell, agent_id: str) -> bool:
    if not weaver_cell:
        return False
    return state.agent_is_visible_to_weaver(weaver_cell.id, agent_id)


def _task_agent_payload_for_weaver(state, weaver_cell, agent_id: str) -> dict:
    """Return safe agent details for task views without leaking hidden agents."""
    if not agent_id:
        return {}
    agent = state.agents.get(agent_id)
    if not agent or agent.cell_type != "agent":
        if weaver_cell and state.weaver_restricts_to_created_agents(
                weaver_cell.group):
            return {"agent_hidden": True}
        return {}
    if _agent_visible_to_weaver(state, weaver_cell, agent_id):
        return {
            "agent_name": agent.slug or agent.name,
            "agent_status": agent.status,
        }
    if state.weaver_restricts_to_created_agents(weaver_cell.group):
        return {"agent_hidden": True}
    return {}


def _stream_payload_for_weaver(state, weaver_cell, stream: dict) -> dict:
    """Return a stream payload with hidden agent identity scrubbed."""
    payload = dict(stream or {})
    agent_id = str(payload.get("agent_id", "") or "").strip()
    if not agent_id:
        return payload
    if _agent_visible_to_weaver(state, weaver_cell, agent_id):
        return payload
    payload["agent_id"] = ""
    payload["agent_name"] = ""
    payload["agent_slug"] = ""
    if state.weaver_restricts_to_created_agents(weaver_cell.group):
        payload["agent_hidden"] = True
    return payload


def _stream_state_counts(streams: list[dict]) -> dict[str, int]:
    counts = {name: 0 for name in _STREAM_STATES}
    for stream in streams:
        state_name = str(stream.get("state", "") or "").strip()
        if not state_name:
            continue
        if state_name not in counts:
            counts[state_name] = 0
        counts[state_name] += 1
    return counts


def _parse_health_timestamp(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _safe_int(value, default: int = 0) -> int:
    try:
        if value not in (None, ""):
            return int(value)
    except (TypeError, ValueError):
        pass
    return default


def _format_health_duration(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    if total < 60:
        return f"{total} sec"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours < 24:
        if rem_minutes:
            return f"{hours} hr {rem_minutes} min"
        return f"{hours} hr"
    days = hours // 24
    rem_hours = hours % 24
    if rem_hours:
        return f"{days} day {rem_hours} hr"
    return f"{days} day"


def _format_health_clock(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")


def _clip_health_fragment(value: str, *, limit: int = 24) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _limit_health_summary(parts: list[str], fallback_parts: list[str]) -> str:
    summary = "; ".join(part for part in parts if part).strip()
    if summary and not summary.endswith("."):
        summary += "."
    if len(summary) <= _HEALTH_SUMMARY_LIMIT:
        return summary
    summary = "; ".join(part for part in fallback_parts if part).strip()
    if summary and not summary.endswith("."):
        summary += "."
    if len(summary) <= _HEALTH_SUMMARY_LIMIT:
        return summary
    return summary[: max(0, _HEALTH_SUMMARY_LIMIT - 1)].rstrip() + "…"


def _fresh_health_details(details, *, now_ts: float) -> tuple[dict, int | None,
                                                             float | None]:
    fresh = dict(details or {}) if isinstance(details, dict) else {}
    last_activity_ts = _parse_health_timestamp(fresh.get("last_activity_at"))
    silence_secs = None
    if last_activity_ts is not None:
        silence_secs = max(0, int(now_ts - last_activity_ts))
        if "silence_secs" in fresh:
            fresh["silence_secs"] = silence_secs
    return fresh, silence_secs, last_activity_ts


def _source_task_for_health(state, task, details: dict):
    source_task_id = str((details or {}).get("source_task_id", "") or "").strip()
    if source_task_id and source_task_id in state.board_tasks:
        return state.board_tasks[source_task_id]
    return task


def _source_agent_for_health(state, task, details: dict):
    source_task = _source_task_for_health(state, task, details)
    agent_id = str(getattr(source_task, "agent_id", "") or "").strip()
    if not agent_id:
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
    if not agent_id:
        return None
    return state.agents.get(agent_id)


def _health_summary(health_state: str, *, details: dict, agent=None,
                    now_ts: float, silence_secs: int | None = None,
                    last_activity_ts: float | None = None) -> str:
    state_name = str(health_state or "healthy").strip() or "healthy"
    if last_activity_ts is None:
        last_activity_ts = _parse_health_timestamp(
            (details or {}).get("last_activity_at")
        )
    if last_activity_ts is None and agent and getattr(agent, "last_event_at", 0):
        last_activity_ts = float(getattr(agent, "last_event_at", 0) or 0)
    if silence_secs is None and last_activity_ts is not None:
        silence_secs = max(0, int(now_ts - last_activity_ts))

    status = str(getattr(agent, "status", "") or "unknown").strip()
    tokens_in = _safe_int(getattr(agent, "session_tokens_in", 0) if agent else 0)
    tokens_out = _safe_int(getattr(agent, "session_tokens_out", 0) if agent else 0)
    tokens_part = f"tokens={tokens_in}/{tokens_out}"
    activity_detail = _clip_health_fragment(
        getattr(agent, "activity_detail", "") if agent else ""
    )
    activity_part = f"activity={activity_detail}" if activity_detail else ""
    clock = _format_health_clock(last_activity_ts)

    should_signal_silence = (
        silence_secs is not None
        and (
            silence_secs >= _HEALTH_SUMMARY_SILENT_AFTER_SECS
            or state_name in {"idle-risk", "stalled", "stale-in-progress"}
        )
    )
    if should_signal_silence:
        duration = _format_health_duration(silence_secs)
        parts = [
            f"Silent {duration}",
            f"status={status}",
            activity_part,
            tokens_part,
            f"last {clock}" if clock else "",
        ]
        fallback = [
            f"Silent {duration}",
            f"status={status}",
            tokens_part,
            f"last {clock}" if clock else "",
        ]
        return _limit_health_summary(parts, fallback)

    if state_name != "healthy":
        label = state_name.replace("-", " ").title()
        parts = [
            label,
            f"status={status}",
            activity_part,
            tokens_part,
            f"last {clock}" if clock else "",
        ]
        fallback = [label, f"status={status}", tokens_part]
        return _limit_health_summary(parts, fallback)

    if last_activity_ts is not None and silence_secs is not None:
        parts = [
            "Healthy",
            f"status={status}",
            f"last activity {_format_health_duration(silence_secs)} ago",
        ]
    else:
        parts = ["Healthy", f"status={status}"]
    return _limit_health_summary(parts, parts)


def _task_health_payload_for_response(state, task, *,
                                      now_ts: float | None = None) -> dict:
    """Return task health fields with freshness calculated at read time."""
    if now_ts is None:
        now_ts = time.time()
    health_state = str(getattr(task, "health_state", "") or "healthy")
    details, silence_secs, last_activity_ts = _fresh_health_details(
        getattr(task, "health_details", {}) or {},
        now_ts=now_ts,
    )
    agent = _source_agent_for_health(state, task, details)
    return {
        "health_state": health_state,
        "health_details": details,
        "health_summary": _health_summary(
            health_state,
            details=details,
            agent=agent,
            now_ts=now_ts,
            silence_secs=silence_secs,
            last_activity_ts=last_activity_ts,
        ),
    }


def _agent_health_payload_for_response(state, cell, *, current_task=None,
                                       now_ts: float | None = None) -> dict:
    """Return top-level health fields for agent detail responses."""
    if now_ts is None:
        now_ts = time.time()
    if current_task is not None:
        return _task_health_payload_for_response(
            state,
            current_task,
            now_ts=now_ts,
        )
    return {
        "health_state": "healthy",
        "health_summary": _health_summary(
            "healthy",
            details={},
            agent=cell,
            now_ts=now_ts,
        ),
    }


def _weaver_streams(state, weaver_cell, group: str, *,
                    include_merged: bool = True,
                    include_orphaned: bool = False,
                    visibility_limit: int = 10,
                    state_filter: str = "",
                    branch_filter: str = "",
                    repo_root_filter: str = "") -> list[dict]:
    streams = [
        _stream_payload_for_weaver(state, weaver_cell, stream)
        for stream in compute_worktree_streams(
            state,
            group=group,
            visibility_limit=visibility_limit,
            include_orphaned=include_orphaned,
        )
    ]
    if not include_merged:
        streams = [
            stream for stream in streams
            if stream.get("state", "") != "merged"
        ]
    if state_filter:
        streams = [
            stream for stream in streams
            if stream.get("state", "") == state_filter
        ]
    if branch_filter:
        streams = [
            stream for stream in streams
            if stream.get("branch", "") == branch_filter
        ]
    if repo_root_filter:
        streams = [
            stream for stream in streams
            if stream.get("repo_root", "") == repo_root_filter
        ]
    return streams


def _resolve_stream_payload(streams: list[dict], *, stream_ident: str = "",
                            repo_root: str = "", branch: str = "",
                            task_id: str = "") -> tuple[dict | None, str]:
    if task_id:
        matches = [
            stream for stream in streams
            if task_id in member_task_ids_for_stream(stream)
        ]
        if len(matches) == 1:
            return matches[0], ""
        if not matches:
            return None, f"Stream not found for task: {task_id}"
        return None, (
            "Multiple streams reference that task; provide stream id or "
            "repo_root + branch"
        )

    stream_ident = str(stream_ident or "").strip()
    repo_root = str(repo_root or "").strip()
    branch = str(branch or "").strip()
    if not branch and stream_ident:
        if stream_ident.startswith("stream:"):
            matches = [
                stream for stream in streams
                if stream.get("stream_id", "") == stream_ident
            ]
            if len(matches) == 1:
                return matches[0], ""
            return None, f"Stream not found: {stream_ident}"
        if "::" in stream_ident:
            repo_root, branch = stream_ident.split("::", 1)
        else:
            branch = stream_ident

    if not branch:
        return None, "Provide stream, branch, or task"

    matches = [
        stream for stream in streams
        if stream.get("branch", "") == branch
        and (not repo_root or stream.get("repo_root", "") == repo_root)
    ]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        if repo_root:
            return None, f"Stream not found for {repo_root}::{branch}"
        return None, f"Stream not found for branch: {branch}"
    return None, (
        "Multiple streams match that branch; provide repo_root or stream id"
    )


async def dispatch_scoped_tool(name, args, handle_command, state, *,
                               tool_prefix: str, caller_kind: str,
                               caller_id: str):
    """Execute a scoped orchestration tool call and return (text, is_error)."""

    _weaver_cell, _weaver_group, caller_kind, auth_error, auth_structured = authorize_caller(
        state, caller_kind=caller_kind, caller_id=caller_id
    )
    if auth_error:
        return auth_error, auth_structured

    view_state = build_scoped_state_view(
        state, caller_kind=caller_kind, caller_id=caller_id,
        caller_cell=_weaver_cell, caller_group=_weaver_group,
    )
    real_state = state
    state = view_state
    tool_name = normalize_tool_name(name, tool_prefix)

    # -- Read tools ---------------------------------------------------------

    if tool_name == "board_summary":
        summary_streams = _weaver_streams(
            state,
            _weaver_cell,
            _weaver_group,
            include_merged=False,
            visibility_limit=5,
        )
        tasks = [
            t for t in state.board_tasks.values()
            if t.group == _weaver_group
        ]
        archived_tasks = [t for t in tasks if t.lane == ARCHIVED_LANE]
        visible_tasks = [t for t in tasks if t.lane != ARCHIVED_LANE]
        if tool_prefix == "engineer_" and caller_kind == "engineer":
            visible_tasks = [
                t for t in visible_tasks
                if _effective_assigned_engineer_id(t) == str(caller_id or "").strip()
            ]
            archived_tasks = [
                t for t in archived_tasks
                if _effective_assigned_engineer_id(t) == str(caller_id or "").strip()
            ]
        summary_state = copy.copy(state)
        summary_state.board_tasks = {task.id: task for task in visible_tasks}

        lane_counts = {
            lane_name: 0 for lane_name in state.board_lanes
            if lane_name != ARCHIVED_LANE
        }
        extra_lanes = {}
        label_counts = {"ready": 0, "deferred": 0}
        health_counts = {
            "healthy": 0,
            "blocked": 0,
            "stale-in-progress": 0,
            "idle-risk": 0,
            "stalled": 0,
            "thrashing": 0,
        }
        verification_counts = {
            "pending": 0,
            "attempted": 0,
            "passed": 0,
            "failed": 0,
        }
        unhealthy = []
        pending_asks = []
        verification_items = []
        include_created_by = caller_kind == "architect"
        architect_task_items = []
        for task in visible_tasks:
            created_by = _task_created_by_classifier(task) if include_created_by else ""
            if include_created_by:
                architect_task_items.append(
                    _architect_board_summary_task_item(
                        task,
                        created_by=created_by,
                    )
                )
            if task.lane in lane_counts:
                lane_counts[task.lane] += 1
            else:
                extra_lanes[task.lane] = extra_lanes.get(task.lane, 0) + 1

            labels = set(task.labels or [])
            for label_name in label_counts:
                if label_name in labels:
                    label_counts[label_name] += 1

            health_state = getattr(task, "health_state", "healthy") or "healthy"
            if health_state not in health_counts:
                health_counts[health_state] = 0
            health_counts[health_state] += 1
            if not board_task_is_closed(task) and health_state != "healthy":
                item = {
                    "id": task.id,
                    "title": task.task,
                    "health_state": health_state,
                    "health_since": getattr(task, "health_since", ""),
                }
                if include_created_by:
                    item["created_by"] = created_by
                unhealthy.append(item)

            verification_state = getattr(task, "verification_state", "") or ""
            if not board_task_is_closed(task) and verification_state in verification_counts:
                verification_counts[verification_state] += 1
                if verification_state in {"pending", "failed"}:
                    item = {
                        "id": task.id,
                        "title": task.task,
                        "verification_state": verification_state,
                        "verification_mode": getattr(
                            task, "verification_mode", ""
                        ) or "",
                        "verification_notes": getattr(
                            task, "verification_notes", ""
                        ) or "",
                    }
                    if include_created_by:
                        item["created_by"] = created_by
                    verification_items.append(item)

            if "loom:human" in labels and not board_task_is_closed(task):
                item = {
                    "id": task.id,
                    "title": task.task,
                    "parent_task_id": task.parent_task_id,
                }
                if include_created_by:
                    item["created_by"] = created_by
                pending_asks.append(item)

        ordered_lanes = dict(lane_counts)
        for lane_name in sorted(extra_lanes):
            ordered_lanes[lane_name] = extra_lanes[lane_name]

        gs = state.get_group_settings(_weaver_group)
        weaver_id = gs.weaver_agent_id or (
            _weaver_cell.id if _weaver_cell and _weaver_cell.group == _weaver_group
            else ""
        )
        agent_status_counts = {
            "idle": 0,
            "running": 0,
            "error": 0,
            "stopped": 0,
        }
        active_agents = []
        total_agents = 0
        needs_attention = 0
        boundary_items = []
        seen_branch_keys = set()

        agents = [
            c for c in state.agents.values()
            if c.cell_type == "agent"
            and c.group == _weaver_group
            and c.id != weaver_id
            and _agent_visible_to_weaver(state, _weaver_cell, c.id)
        ]
        agents.sort(key=lambda c: ((c.slug or c.name or c.id).lower(), c.id))

        for cell in agents:
            total_agents += 1
            if cell.needs_attention:
                needs_attention += 1
            status = cell.status or "stopped"
            agent_status_counts[status] = agent_status_counts.get(status, 0) + 1
            current_task = state.agent_current_task(cell.id)
            repo_root = cell.worktree_repo_root or cell.git_root or ""
            branch = cell.worktree_branch or ""
            boundary_key = repo_root + "::" + branch if repo_root and branch else ""
            if boundary_key and boundary_key not in seen_branch_keys:
                overview = _worktree_boundary_overview(
                    state,
                    repo_root=repo_root,
                    branch=branch,
                )
                if overview:
                    overview["agent_id"] = cell.id
                    overview["agent_name"] = cell.name
                    overview["agent_slug"] = cell.slug
                    overview["current_task_id"] = current_task.id if current_task else ""
                    overview["current_task"] = current_task.task if current_task else ""
                    boundary_items.append(overview)
                    seen_branch_keys.add(boundary_key)
            if status == "stopped":
                continue
            active_agents.append({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "type": cell.agent_type,
                "status": status,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "needs_attention": cell.needs_attention,
            })

        pending_asks.sort(key=lambda item: (item["title"].lower(), item["id"]))
        unhealthy.sort(
            key=lambda item: (
                -HEALTH_SEVERITY.get(item["health_state"], 0),
                item["health_since"] or "",
                item["title"].lower(),
            ),
        )
        verification_items.sort(
            key=lambda item: (
                0 if item["verification_state"] == "failed" else 1,
                item["title"].lower(),
            ),
        )
        boundary_items.sort(
            key=lambda item: (
                0 if item["partial_review_safe"] else 1,
                item.get("branch", ""),
                item.get("latest_boundary_recorded_at", ""),
            ),
        )
        if include_created_by:
            lane_order = {lane: idx for idx, lane in enumerate(state.board_lanes)}
            architect_task_items.sort(
                key=lambda item: (
                    lane_order.get(item["lane"], len(lane_order)),
                    getattr(state.board_tasks.get(item["id"]), "position", 0),
                    item["title"].lower(),
                    item["id"],
                )
            )

        summary = {
            "group": _weaver_group,
            "tasks_total": len(visible_tasks),
            "archived_total": len(archived_tasks),
            "lanes": ordered_lanes,
            "labels": label_counts,
            "task_health": {
                "counts": health_counts,
                "unhealthy": unhealthy[:10],
                "truncated": len(unhealthy) > 10,
            },
            "hints": {
                "count": 0,
                "items": [],
                "truncated": False,
            },
            "asks": {
                "count": len(pending_asks),
                "items": pending_asks[:10],
                "truncated": len(pending_asks) > 10,
            },
            "verification": {
                "counts": verification_counts,
                "items": verification_items[:10],
                "truncated": len(verification_items) > 10,
            },
            "agents": {
                "total": total_agents,
                "active_count": len(active_agents),
                "needs_attention": needs_attention,
                "by_status": agent_status_counts,
                "active": active_agents[:10],
                "truncated": len(active_agents) > 10,
            },
            "streams": {
                "count": len(summary_streams),
                "by_state": _stream_state_counts(summary_streams),
                "items": summary_streams[:10],
                "truncated": len(summary_streams) > 10,
            },
            "branch_boundaries": {
                "count": len(boundary_items),
                "items": boundary_items[:10],
                "truncated": len(boundary_items) > 10,
            },
        }
        hints = compute_weaver_hints(
            summary_state,
            _weaver_group,
            weaver_id=_weaver_cell.id if _weaver_cell else "",
        )
        summary["hints"] = {
            "count": len(hints),
            "items": hints[:10],
            "truncated": len(hints) > 10,
        }
        if include_created_by:
            return _architect_board_summary_json(
                summary,
                architect_task_items,
            ), False
        return _compact_json(summary), False

    if tool_name == "session_map":
        return json.dumps(
            build_weaver_session_map(
                state,
                _weaver_group,
                weaver_cell=_weaver_cell,
            )
        ), False

    if tool_name == "streams_list":
        streams = _weaver_streams(
            state,
            _weaver_cell,
            _weaver_group,
            state_filter=str(args.get("state", "") or "").strip(),
            branch_filter=str(args.get("branch", "") or "").strip(),
            repo_root_filter=str(args.get("repo_root", "") or "").strip(),
            include_orphaned=bool(args.get("include_orphaned", False)),
        )
        return json.dumps({
            "group": _weaver_group,
            "count": len(streams),
            "streams": streams,
        }), False

    if tool_name == "stream_show":
        task_ident = str(args.get("task", "") or "").strip()
        task_id = ""
        if task_ident:
            task_id = _resolve_task(state, task_ident)
            if not task_id:
                return "Task not found", True
            task = state.board_tasks.get(task_id)
            if not task or task.group != _weaver_group:
                return "Task not found", True
        streams = _weaver_streams(
            state,
            _weaver_cell,
            _weaver_group,
            include_orphaned=True,
        )
        stream, error_text = _resolve_stream_payload(
            streams,
            stream_ident=str(args.get("stream", "") or "").strip(),
            repo_root=str(args.get("repo_root", "") or "").strip(),
            branch=str(args.get("branch", "") or "").strip(),
            task_id=task_id,
        )
        if error_text:
            return error_text, True
        return json.dumps(stream), False

    if tool_name == "board_list":
        lane_filter = args.get("lane", "")
        label_filter = args.get("label", "")
        health_filter = args.get("health", "")
        search = args.get("search", "").lower()

        lanes = {}
        for t in state.board_tasks.values():
            # Always scope to weaver's group
            if t.group != _weaver_group:
                continue
            if t.lane == ARCHIVED_LANE and lane_filter != ARCHIVED_LANE:
                continue
            if lane_filter and t.lane != lane_filter:
                continue
            if label_filter and label_filter not in (t.labels or []):
                continue
            health_state = getattr(t, "health_state", "healthy") or "healthy"
            if health_filter and health_state != health_filter:
                continue
            if search and search not in t.task.lower() \
                    and search not in (t.description or "").lower():
                continue
            lane_tasks = lanes.setdefault(t.lane, [])
            agent_name = ""
            agent_hidden = False
            if t.agent_id:
                agent_payload = _task_agent_payload_for_weaver(
                    state, _weaver_cell, t.agent_id
                )
                agent_name = agent_payload.get("agent_name", "")
                agent_hidden = bool(agent_payload.get("agent_hidden"))
            item = {
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "group": t.group,
                "labels": t.labels or [],
                "action": t.action_name,
                "agent": agent_name,
                "status": t.status,
                "health_state": health_state,
                "verification_state": getattr(
                    t, "verification_state", ""
                ) or "",
                "verification_mode": getattr(
                    t, "verification_mode", ""
                ) or "",
                "provider": t.provider,
                "external_id": t.external_id,
                "external_url": t.external_url,
                "health_since": getattr(t, "health_since", ""),
                "parent_task_id": t.parent_task_id,
            }
            if caller_kind == "architect":
                item["created_by"] = _task_created_by_classifier(t)
            if agent_hidden:
                item["agent_hidden"] = True
            lane_tasks.append(item)

        # Order lanes by board_lanes order
        ordered = {}
        for lane_name in state.board_lanes:
            if lane_name == ARCHIVED_LANE and lane_filter != ARCHIVED_LANE:
                continue
            if lane_name in lanes:
                ordered[lane_name] = lanes[lane_name]
        # Include any lanes not in board_lanes (shouldn't happen, but safe)
        for lane_name, tasks in lanes.items():
            if lane_name not in ordered:
                ordered[lane_name] = tasks

        return json.dumps({"lanes": ordered}), False

    if tool_name == "task_show":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task or task.group != _weaver_group:
            return "Task not found", True
        d = serialize_task_for_mcp(task, tasks_by_id=state.board_tasks)
        d.update(_task_health_payload_for_response(state, task))
        d["title"] = task.task
        d["action"] = task.action_name
        if caller_kind == "architect":
            d["created_by"] = _task_created_by_classifier(task)
        if task.agent_id and not _agent_visible_to_weaver(
                state, _weaver_cell, task.agent_id):
            d["agent_id"] = ""
            if state.weaver_restricts_to_created_agents(_weaver_group):
                d["agent_hidden"] = True
        # Include recent messages (last 10 only)
        if task.messages:
            d["messages"] = task.messages[-10:]
        # Enrich with agent info
        if task.agent_id:
            d.update(
                _task_agent_payload_for_weaver(
                    state, _weaver_cell, task.agent_id
                )
            )
        # Auto-include pipeline chain for pipeline tasks
        if task.pipeline_root_id or task.parent_task_id:
            chain = state.board_get_chain(tid)
            d["pipeline_chain"] = []
            for ct in chain:
                if ct.group != _weaver_group:
                    continue
                agent_slug = ""
                agent_hidden = False
                if ct.agent_id:
                    agent_payload = _task_agent_payload_for_weaver(
                        state, _weaver_cell, ct.agent_id
                    )
                    agent_slug = agent_payload.get("agent_name", "")
                    agent_hidden = bool(agent_payload.get("agent_hidden"))
                item = {
                    "id": ct.id,
                    "title": ct.task,
                    "lane": ct.lane,
                    "status": ct.status,
                    "health_state": getattr(ct, "health_state", "healthy"),
                    "verification_state": getattr(
                        ct, "verification_state", ""
                    ) or "",
                    "depth": ct.pipeline_depth,
                    "agent": agent_slug,
                }
                if caller_kind == "architect":
                    item["created_by"] = _task_created_by_classifier(ct)
                if agent_hidden:
                    item["agent_hidden"] = True
                d["pipeline_chain"].append(item)
        return json.dumps(d), False

    if tool_name == "engineer_list" and caller_kind == "architect":
        visible_task_ids = set(
            _filter_tasks_for_caller(real_state, caller_kind, caller_id)
        )
        engineers = []
        for cell, relation in _architect_visible_engineers(
            real_state, caller_id
        ).values():
            current_task = real_state.agent_current_task(cell.id)
            if current_task and current_task.id not in visible_task_ids:
                current_task = None
            engineers.append({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "status": cell.status,
                "dismissed_at": _agent_dismissed_at(cell),
                "group": cell.group,
                "relation": relation,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "activity": cell.activity,
                "activity_detail": cell.activity_detail,
            })
        engineers.sort(
            key=lambda item: (
                0 if item["relation"] == "hired" else 1,
                item["slug"] or item["name"] or item["id"],
                item["id"],
            )
        )
        return json.dumps({"engineers": engineers}), False

    if tool_name == "pending_hire_status" and caller_kind == "architect":
        pending_hire, hire_error = _load_architect_pending_hire(
            real_state, caller_id, args.get("hire_id", "")
        )
        if not pending_hire:
            return hire_error, True
        return json.dumps(pending_hire), False

    if tool_name == "pending_hire_list" and caller_kind == "architect":
        status_filter = str(args.get("status_filter", "") or "").strip()
        if status_filter and status_filter not in {
                "pending", "approved", "rejected"}:
            return (
                "status_filter must be one of: pending, approved, rejected",
                True,
            )
        return json.dumps({
            "pending_hires": real_state.load_pending_hires(
                status_filter=status_filter,
                architect_id=str(caller_id or "").strip(),
            )
        }), False

    if tool_name == "agents_list":
        agents = []
        for c in state.agents.values():
            if c.cell_type != "agent":
                continue
            if c.group != _weaver_group:
                continue
            if not _agent_visible_to_weaver(state, _weaver_cell, c.id):
                continue
            current_task = state.agent_current_task(c.id)
            agents.append({
                "id": c.id,
                "name": c.name,
                "slug": c.slug,
                "status": c.status,
                "group": c.group,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "activity": c.activity,
                "activity_detail": c.activity_detail,
            })
        return json.dumps({"agents": agents}), False

    if tool_name == "agent_show":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents[agent_id]

        d = {
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "agent_type": cell.agent_type,
            "status": cell.status,
            "group": cell.group,
            "directory": cell.directory,
            "git_root": cell.git_root,
            "activity": cell.activity,
            "activity_detail": cell.activity_detail,
            "error_message": cell.error_message,
            "needs_attention": cell.needs_attention,
            "tasks_dispatched": cell.tasks_dispatched,
            "session": {
                "session_id": cell.agent_session_id,
                "tokens_in": cell.session_tokens_in,
                "tokens_out": cell.session_tokens_out,
            },
        }
        current_task = state.agent_current_task(agent_id)
        if current_task and current_task.group != _weaver_group:
            current_task = None
        d.update(
            _agent_health_payload_for_response(
                state,
                cell,
                current_task=current_task,
            )
        )

        # Worktree state
        if cell.worktree_path:
            repo_root = cell.worktree_repo_root or cell.git_root or ""
            d["worktree"] = {
                "path": cell.worktree_path,
                "branch": cell.worktree_branch,
                "base_branch": cell.worktree_base_branch,
                "dirty": cell.worktree_dirty,
                "diff": cell.worktree_diff or {},
                "checkpoints": cell.worktree_checkpoints,
                "ahead": cell.worktree_ahead,
                "behind": cell.worktree_behind,
                "merged": cell.worktree_merged,
            }
            boundary_tasks = []
            for t in state.board_tasks.values():
                if t.group != _weaver_group:
                    continue
                boundary = getattr(t, "worktree_boundary", {}) or {}
                if not isinstance(boundary, dict):
                    continue
                if boundary.get("repo_root", "") != repo_root:
                    continue
                if boundary.get("branch", "") != cell.worktree_branch:
                    continue
                boundary_tasks.append({
                    "task_id": t.id,
                    "task_title": t.task,
                    "lane": t.lane,
                    "boundary": boundary,
                    "resume_after_boundary_task_id": (
                        getattr(t, "resume_after_boundary_task_id", "") or ""
                    ),
                })
            boundary_tasks.sort(
                key=lambda item: (
                    item["boundary"].get("recorded_at", ""),
                    item["task_id"],
                )
            )
            if boundary_tasks:
                d["worktree"]["task_boundaries"] = boundary_tasks
            overview = _worktree_boundary_overview(
                state,
                repo_root=repo_root,
                branch=cell.worktree_branch or "",
            )
            if overview:
                overview["current_task_id"] = (
                    current_task.id if current_task else ""
                )
                overview["current_task"] = (
                    current_task.task if current_task else ""
                )
                d["worktree"]["boundary_overview"] = overview

        # Child terminals
        children_ids = state._children.get(agent_id, [])
        if children_ids:
            terminals = []
            for cid in children_ids:
                tc = state.agents.get(cid)
                if tc:
                    terminals.append({
                        "name": tc.name,
                        "slug": tc.slug,
                        "status": tc.status,
                        "current_process": tc.current_process,
                        "current_path": tc.current_path,
                    })
            d["terminals"] = terminals

        # Task history — all tasks ever assigned to this agent
        tasks = []
        for t in state.board_tasks.values():
            if t.group != _weaver_group:
                continue
            if t.agent_id != agent_id:
                continue
            task_info = {
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "lane": t.lane,
                "status": t.status,
                "labels": t.labels or [],
                "action": t.action_name,
                "resume_after_boundary_task_id": (
                    t.resume_after_boundary_task_id or ""
                ),
            }
            if t.worktree_boundary:
                task_info["worktree_boundary"] = t.worktree_boundary
            if t.messages:
                task_info["messages"] = t.messages
            tasks.append(task_info)
        if tasks:
            d["tasks"] = tasks

        # Current task (may differ from tasks list if unlinked)
        if current_task and current_task.group == _weaver_group:
            d["current_task_id"] = current_task.id

        return json.dumps(d), False

    if tool_name == "actions_list":
        result = await handle_command({
            "cmd": "list_actions",
            "group": args.get("group", "") or _weaver_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "action_show":
        result = await handle_command({
            "cmd": "get_action",
            "name": args.get("name", ""),
            "group": args.get("group", "") or _weaver_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    # -- Write tools --------------------------------------------------------

    if tool_name == "engineer_hire" and caller_kind == "architect":
        name = str(args.get("name", "") or "").strip()
        if not name:
            return "name is required", True
        result = await handle_command({
            "cmd": "architect_engineer_hire",
            "architect_id": str(caller_id or "").strip(),
            "name": name,
            "command": str(args.get("command", "") or "").strip(),
            "provider": str(args.get("provider", "") or "").strip(),
            "directory": str(args.get("directory", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "engineer_dismiss" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_dismiss",
            "architect_id": str(caller_id or "").strip(),
            "engineer_id": engineer_id,
            "reason": str(args.get("reason", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "engineer_rehire" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_rehire",
            "architect_id": str(caller_id or "").strip(),
            "engineer_id": engineer_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_create":
        if caller_kind == "architect":
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            requested_group = str(args.get("group", "") or "").strip()
            if not requested_group:
                return "group is required", True
            if requested_group != _weaver_group:
                return "group must match the architect's group", True
            assigned_engineer_ident = str(
                args.get("assigned_engineer_id", "") or ""
            ).strip()
            if not assigned_engineer_ident:
                return "assigned_engineer_id is required", True
            assigned_engineer_id, engineer_error = _resolve_architect_hired_engineer(
                real_state, caller_id, assigned_engineer_ident
            )
            if not assigned_engineer_id:
                return engineer_error, True
            assigned_engineer = real_state.agents.get(assigned_engineer_id)
            if _agent_dismissed_at(assigned_engineer):
                return _engineer_dismissed_error(assigned_engineer_id), True
            action_vars = args.get("action_vars", {})
            if action_vars is None:
                action_vars = {}
            if not isinstance(action_vars, dict):
                return "action_vars must be an object", True

            create_result = await handle_command({
                "cmd": "board_add_task",
                "task": title,
                "description": args.get("description", ""),
                "group": _weaver_group,
                "lane": args.get("lane", ""),
                "labels": args.get("labels", []),
                "assigned_engineer_id": assigned_engineer_id,
            })
            if create_result and create_result.get("type") == "error":
                return create_result.get("message", "Unknown error"), True

            task_id = str((create_result or {}).get("task_id", "") or "").strip()
            if task_id:
                update_result = await handle_command({
                    "cmd": "board_update_task",
                    "id": task_id,
                    "assigned_engineer_id": assigned_engineer_id,
                    "created_by_architect_id": str(caller_id or "").strip(),
                    "suggested_action": str(
                        args.get("suggested_action", "") or ""
                    ).strip(),
                    "action_name": "",
                    "action_vars": action_vars,
                })
                if update_result and update_result.get("type") == "error":
                    return update_result.get("message", "Unknown error"), True
            return (
                json.dumps(create_result)
                if create_result else '{"type":"ok"}'
            ), False

        payload = {
            "cmd": "board_add_task",
            "task": args.get("title", ""),
            "description": args.get("description", ""),
            "group": _weaver_group,
            "lane": args.get("lane", ""),
            "action_name": args.get("action", ""),
            "action_vars": args.get("action_vars", {}),
            "labels": args.get("labels", []),
            "verification_mode": args.get("verification_mode", ""),
            "verification_state": args.get("verification_state", ""),
            "verification_notes": args.get("verification_notes", ""),
            "verification_summary": args.get("verification_summary", {}),
            "assigned_engineer_id": str(caller_id or "").strip(),
            "created_by_engineer_id": str(caller_id or "").strip(),
        }
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_reassign" and caller_kind == "architect":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        if str(getattr(task, "created_by_architect_id", "") or "").strip() != str(
            caller_id or ""
        ).strip():
            return "Task was not created by this architect", True
        engineer_ident = str(args.get("new_engineer_id", "") or "").strip()
        if not engineer_ident:
            return "new_engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        engineer = real_state.agents.get(engineer_id)
        if _agent_dismissed_at(engineer):
            return _engineer_dismissed_error(engineer_id), True
        result = await handle_command({
            "cmd": "board_update_task",
            "id": tid,
            "assigned_engineer_id": engineer_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({
            "type": "ok",
            "task_id": tid,
            "assigned_engineer_id": engineer_id,
        }), False

    if tool_name == "task_edit":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        payload = {"cmd": "board_update_task", "id": tid}
        if "title" in args:
            payload["task"] = args["title"]
        if "description" in args:
            payload["description"] = args["description"]
        if "labels" in args:
            payload["labels"] = args["labels"]
        if "action" in args:
            payload["action_name"] = args["action"]
        if "action_vars" in args:
            payload["action_vars"] = args["action_vars"]
        for key in (
            "verification_mode",
            "verification_state",
            "verification_notes",
            "verification_summary",
        ):
            if key in args:
                payload[key] = args[key]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_upload_artifact":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        payload = {"cmd": "task_upload_artifact", "task_id": tid}
        if caller_id:
            payload["cell_id"] = caller_id
        for key in (
            "local_path",
            "filename",
            "content_base64",
            "content_text",
            "artifact_type",
            "title",
            "mime_type",
            "summary",
            "prompt_mode",
        ):
            if key in args:
                payload[key] = args[key]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_verify":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        actor_name = getattr(_weaver_cell, "name", "") or caller_kind
        payload = {
            "cmd": "board_verify_task",
            "id": tid,
            "actor_name": actor_name,
        }
        if "mode" in args:
            payload["verification_mode"] = args["mode"]
        if "state" in args:
            payload["verification_state"] = args["state"]
        if "notes" in args:
            payload["verification_notes"] = args["notes"]
        if "tests_run" in args:
            payload["tests_run"] = args["tests_run"]
        if "human_validation_pending" in args:
            payload["human_validation_pending"] = args["human_validation_pending"]
        if "deploy_needed" in args:
            payload["deploy_needed"] = args["deploy_needed"]
        if "attempted" in args:
            payload["deploy_attempted"] = args["attempted"]
        if "smoke" in args:
            if args["smoke"] == "clear":
                payload["manual_smoke_done"] = False
            else:
                payload["smoke_status"] = args["smoke"]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_move":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        result = await handle_command({
            "cmd": "board_move_task",
            "id": tid,
            "lane": args.get("lane", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_dispatch":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        payload = {
            "cmd": "dispatch_task",
            "id": tid,
            "_weaver_dispatch_group": _weaver_group,
            "_weaver_dispatch_id": _weaver_cell.id,
        }
        agent_ident = args.get("agent", "")
        if agent_ident:
            agent_id, agent_error = _resolve_visible_agent(
                real_state, caller_kind, caller_id, agent_ident
            )
            if not agent_id:
                return agent_error, True
            payload["agent_id"] = agent_id
        else:
            payload["create_agent"] = True
            payload["_created_by_weaver_id"] = _weaver_cell.id
            payload["owner_engineer_id"] = str(caller_id or "").strip()
            if _weaver_cell:
                if _weaver_cell.session_id:
                    payload["target_session_id"] = _weaver_cell.session_id
                if _weaver_cell.window_id:
                    payload["target_window_id"] = _weaver_cell.window_id
            agent_type = args.get("agent_type", "")
            if agent_type:
                payload["agent_type"] = agent_type
            command = args.get("command", "")
            if command:
                payload["command"] = command
            model = args.get("model", "")
            if model:
                payload["model"] = model
            reasoning_effort = args.get("reasoning_effort", "")
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
        agent_name = args.get("name", "")
        if agent_name:
            payload["name"] = agent_name
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "batch_dispatch":
        raw_tasks = args.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return "tasks must be a non-empty array", True
        raw_max_concurrent = args.get("max_concurrent", None)
        if raw_max_concurrent is None:
            max_concurrent = normalize_default_worker_concurrency(
                state.get_weaver_settings(
                    _weaver_group).default_worker_concurrency
            )
        elif not isinstance(raw_max_concurrent, int) \
                or raw_max_concurrent < 1:
            return "max_concurrent must be an integer >= 1", True
        else:
            max_concurrent = raw_max_concurrent

        dispatch_lane = (
            state.get_group_settings(_weaver_group).dispatch_lane
            or "In Progress"
        )
        active_agents = _active_worker_ids(state, _weaver_group)
        active_before = len(active_agents)
        group_agents = {}
        seen_task_ids = set()
        results = []

        def _fail(entry_idx: int, task_ident: str, reason: str,
                  message: str, task_id: str = "") -> None:
            item = {
                "index": entry_idx,
                "task": task_ident,
                "status": "failed",
                "reason": reason,
                "message": message,
            }
            if task_id:
                item["task_id"] = task_id
            results.append(item)

        for idx, raw_entry in enumerate(raw_tasks):
            if not isinstance(raw_entry, dict):
                _fail(idx, "", "invalid_entry",
                      "Each tasks entry must be an object.")
                continue

            task_ident = raw_entry.get("task", "")
            if not isinstance(task_ident, str) or not task_ident.strip():
                _fail(idx, "", "invalid_entry",
                      "Each tasks entry must include a task string.")
                continue
            agent_group = raw_entry.get("agent_group", "")
            if agent_group and not isinstance(agent_group, str):
                _fail(idx, task_ident, "invalid_entry",
                      "agent_group must be a string when provided.")
                continue

            tid = _resolve_task(state, task_ident)
            if not tid:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if tid in seen_task_ids:
                _fail(idx, task_ident, "duplicate_task",
                      "Task appears more than once in this batch.", tid)
                continue
            seen_task_ids.add(tid)
            if state.auto_dispatch_queue_contains(tid):
                _fail(idx, task_ident, "already_queued",
                      "Task is already queued for auto-dispatch.", tid)
                continue

            task = state.board_tasks.get(tid)
            if not task:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if task.group != _weaver_group:
                _fail(idx, task_ident, "wrong_group",
                      "Task is outside the weaver's group.", tid)
                continue
            if task.agent_id:
                _fail(idx, task_ident, "already_assigned",
                      "Task is already linked to an agent.", tid)
                continue
            if task.lane == ARCHIVED_LANE:
                _fail(idx, task_ident, "already_archived",
                      "Task is archived.", tid)
                continue
            if task.lane == "Done":
                _fail(idx, task_ident, "already_done",
                      "Task is already Done.", tid)
                continue
            if task.lane in ("In Progress", dispatch_lane):
                _fail(idx, task_ident, "already_in_progress",
                      "Task is already in the dispatch lane.", tid)
                continue
            if not state.board_deps_met(task):
                unmet = _blocked_dependency_titles(state, task)
                _fail(
                    idx,
                    task_ident,
                    "blocked_by_dependencies",
                    "Blocked by dependencies: " + ", ".join(unmet),
                    tid,
                )
                continue

            target_agent_id = ""
            mode = "new_agent"
            if agent_group:
                mode = "agent_group"
                target_agent_id = group_agents.get(agent_group, "")

            needs_capacity = not target_agent_id
            if target_agent_id:
                needs_capacity = not _is_busy_agent(state, target_agent_id)
            if needs_capacity and len(active_agents) >= max_concurrent:
                queue_entry = state.auto_dispatch_queue_add(
                    _weaver_group,
                    tid,
                    agent_group=agent_group,
                    max_concurrent=max_concurrent,
                    target_agent_id=target_agent_id,
                    weaver_owner_id=_weaver_cell.id,
                )
                queue = state.auto_dispatch_queues.get(_weaver_group, [])
                item = {
                    "index": idx,
                    "task": task_ident,
                    "task_id": tid,
                    "status": "deferred",
                    "reason": "max_concurrent_reached",
                    "message": (
                        "Dispatch would exceed max_concurrent for the group."
                    ),
                    "queue_position": len(queue),
                    "queued_at": (
                        queue_entry.enqueued_at if queue_entry else ""
                    ),
                }
                if agent_group:
                    item["agent_group"] = agent_group
                results.append(item)
                continue

            payload = {
                "cmd": "dispatch_task",
                "id": tid,
                "_weaver_dispatch_group": _weaver_group,
                "_weaver_dispatch_id": _weaver_cell.id,
            }
            if target_agent_id:
                payload["agent_id"] = target_agent_id
            else:
                payload["create_agent"] = True
                payload["_created_by_weaver_id"] = _weaver_cell.id
                payload["owner_engineer_id"] = str(caller_id or "").strip()

            result = await handle_command(payload)
            task_after = state.board_tasks.get(tid)
            resolved_agent_id = task_after.agent_id if task_after else ""
            if resolved_agent_id and real_state.agents.get(resolved_agent_id):
                state.agents[resolved_agent_id] = real_state.agents[
                    resolved_agent_id
                ]
            if resolved_agent_id and (
                _is_busy_agent(state, resolved_agent_id)
                or resolved_agent_id in active_agents
            ):
                active_agents.add(resolved_agent_id)

            if result and result.get("type") == "error":
                _fail(
                    idx,
                    task_ident,
                    "dispatch_error",
                    result.get("message", "Unknown error"),
                    tid,
                )
                continue
            if result and result.get("type") == "dispatch_action_missing":
                _fail(
                    idx,
                    task_ident,
                    "dispatch_action_missing",
                    f"Action not found: {result.get('action_name', '')}",
                    tid,
                )
                continue

            if agent_group and not target_agent_id and resolved_agent_id:
                group_agents[agent_group] = resolved_agent_id

            item = {
                "index": idx,
                "task": task_ident,
                "task_id": tid,
                "status": "queued"
                if result and result.get("type") == "queued"
                else "dispatched",
                "mode": mode,
            }
            if agent_group:
                item["agent_group"] = agent_group
            if resolved_agent_id:
                item["agent_id"] = resolved_agent_id
                agent = real_state.agents.get(resolved_agent_id) or state.agents.get(
                    resolved_agent_id
                )
                if agent:
                    item["agent_name"] = agent.slug or agent.name
            results.append(item)

        payload = {
            "type": "ok",
            "max_concurrent": max_concurrent,
            "active_before": active_before,
            "active_after": len(active_agents),
            "results": results,
        }
        return json.dumps(payload), False

    if tool_name == "task_resolve":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        result = await handle_command({
            "cmd": "resolve_ask",
            "id": tid,
            "answer": args.get("answer", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    # -- Event tools --------------------------------------------------------

    if tool_name == "events":
        # Pull events from panel_log with optional filters
        since_id = args.get("since_id", 0)
        limit = args.get("limit", 50)
        type_filter = set(args.get("types", []))

        if state.panel_log:
            events = state.panel_log.get_page(limit, since_id)
        else:
            events = []

        # Scope to weaver's group
        events = [e for e in events
                  if e.get("group", "") == _weaver_group]
        if type_filter:
            events = [e for e in events if e.get("kind") in type_filter]

        cursor = events[-1]["id"] if events else since_id
        return json.dumps({"events": events, "cursor": cursor},
                          ), False

    if tool_name == "launch_settings":
        fields = {}
        mapping = {
            "provider": "weaver_provider",
            "command": "weaver_boot_command",
            "model": "weaver_model",
            "reasoning_effort": "weaver_reasoning_effort",
        }
        for src, dest in mapping.items():
            if src in args:
                fields[dest] = str(args.get(src, "") or "").strip()
        result = await handle_command({
            "cmd": "weaver_update_settings",
            "group": _weaver_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_weaver_settings(_weaver_group))}), False

    if tool_name == "notifications":
        ws = state.get_weaver_settings(_weaver_group)
        fields = {}
        preset_name = str(args.get("preset", "") or "").strip().lower()
        if preset_name:
            try:
                fields.update(get_weaver_notification_preset(preset_name))
            except ValueError:
                return (
                    "Unknown notification preset. Use quiet, normal, or noisy.",
                    True,
                )
        if "digest_verbosity" in args:
            fields["digest_verbosity"] = normalize_weaver_digest_verbosity(
                args["digest_verbosity"]
            )
        if "push_interval" in args:
            fields["push_interval"] = max(10, args["push_interval"])
        if "max_interval" in args:
            fields["max_interval"] = max(30, args["max_interval"])
        if "heartbeat_interval" in args:
            heartbeat_interval = args["heartbeat_interval"]
            fields["heartbeat_interval"] = 0 if heartbeat_interval <= 0 \
                else max(30, heartbeat_interval)
        if preset_name or "enable" in args or "disable" in args:
            current = set(fields.get("enabled_events", ws.enabled_events))
            for e in args.get("enable", []):
                current.add(e)
            for e in args.get("disable", []):
                current.discard(e)
            fields["enabled_events"] = sorted(current)

        result = await handle_command({
            "cmd": "weaver_update_settings",
            "group": _weaver_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_weaver_settings(_weaver_group))}), False

    if tool_name == "resume":
        result = await handle_command({
            "cmd": "weaver_resume",
            "group": _weaver_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return "Event delivery resumed.", False

    # -- Context tools ------------------------------------------------------

    if tool_name == "decision_create" and caller_kind == "architect":
        title = str(args.get("title", "") or "").strip()
        rationale = str(args.get("rationale", "") or "").strip()
        if not title:
            return "title is required", True
        if not rationale:
            return "rationale is required", True
        status = str(args.get("status", "") or "proposed").strip() or "proposed"
        if status not in _DECISION_STATUSES:
            return (
                "status must be one of: proposed, accepted, revised, rejected",
                True,
            )
        supersedes = str(args.get("supersedes", "") or "").strip() or None
        if supersedes:
            prior_decision, decision_error = _load_architect_decision(
                real_state, caller_id, supersedes
            )
            if not prior_decision:
                return decision_error, True
        linked_task_ids, linked_engineer_ids, link_error = _normalize_decision_links(
            real_state,
            caller_id,
            task_ids=args.get("linked_task_ids", []),
            engineer_ids=args.get("linked_engineer_ids", []),
        )
        if link_error:
            return link_error, True
        decision = real_state.save_decision({
            "id": "decision-" + uuid.uuid4().hex[:12],
            "architect_id": str(caller_id or "").strip(),
            "title": title,
            "rationale": rationale,
            "status": status,
            "supersedes": supersedes,
            "linked_task_ids": linked_task_ids,
            "linked_engineer_ids": linked_engineer_ids,
            "archived": False,
        })
        if not decision:
            return "Failed to save decision", True
        return json.dumps({
            "id": decision["id"],
            "created_at": decision["created_at"],
        }), False

    if tool_name == "decision_update" and caller_kind == "architect":
        decision, decision_error = _load_architect_decision(
            real_state, caller_id, args.get("id", "")
        )
        if not decision:
            return decision_error, True
        patch = {"id": decision["id"]}
        if "title" in args:
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            patch["title"] = title
        if "rationale" in args:
            rationale = str(args.get("rationale", "") or "").strip()
            if not rationale:
                return "rationale is required", True
            patch["rationale"] = rationale
        if "status" in args:
            status = str(args.get("status", "") or "").strip()
            if status not in _DECISION_STATUSES:
                return (
                    "status must be one of: proposed, accepted, revised, rejected",
                    True,
                )
            patch["status"] = status
        if "supersedes" in args:
            supersedes = str(args.get("supersedes", "") or "").strip() or None
            if supersedes:
                prior_decision, supersedes_error = _load_architect_decision(
                    real_state, caller_id, supersedes
                )
                if not prior_decision:
                    return supersedes_error, True
            patch["supersedes"] = supersedes
        if "linked_task_ids" in args or "linked_engineer_ids" in args:
            linked_task_ids, linked_engineer_ids, link_error = _normalize_decision_links(
                real_state,
                caller_id,
                task_ids=args.get(
                    "linked_task_ids",
                    decision.get("linked_task_ids", []),
                ),
                engineer_ids=args.get(
                    "linked_engineer_ids",
                    decision.get("linked_engineer_ids", []),
                ),
            )
            if link_error:
                return link_error, True
            if "linked_task_ids" in args:
                patch["linked_task_ids"] = linked_task_ids
            if "linked_engineer_ids" in args:
                patch["linked_engineer_ids"] = linked_engineer_ids
        if "archived" in args:
            patch["archived"] = bool(args.get("archived"))

        updated = real_state.save_decision(patch)
        if not updated:
            return "Failed to save decision", True
        return json.dumps(updated), False

    if tool_name == "decision_link" and caller_kind == "architect":
        decision, decision_error = _load_architect_decision(
            real_state, caller_id, args.get("id", "")
        )
        if not decision:
            return decision_error, True
        task_id = str(args.get("task_id", "") or "").strip()
        engineer_id = str(args.get("engineer_id", "") or "").strip()
        if bool(task_id) == bool(engineer_id):
            return "Provide exactly one of task_id or engineer_id", True

        patch = {"id": decision["id"]}
        if task_id:
            linked_task_ids, _linked_engineer_ids, link_error = _normalize_decision_links(
                real_state,
                caller_id,
                task_ids=list(decision.get("linked_task_ids", [])) + [task_id],
                engineer_ids=[],
            )
            if link_error:
                return link_error, True
            patch["linked_task_ids"] = linked_task_ids
        else:
            _linked_task_ids, linked_engineer_ids, link_error = _normalize_decision_links(
                real_state,
                caller_id,
                task_ids=[],
                engineer_ids=list(decision.get("linked_engineer_ids", [])) + [engineer_id],
            )
            if link_error:
                return link_error, True
            patch["linked_engineer_ids"] = linked_engineer_ids

        updated = real_state.save_decision(patch)
        if not updated:
            return "Failed to save decision", True
        return json.dumps(updated), False

    if tool_name == "decision_list" and caller_kind == "architect":
        status_filter = str(args.get("status_filter", "") or "").strip()
        if status_filter and status_filter not in _DECISION_STATUSES:
            return (
                "status_filter must be one of: proposed, accepted, revised, rejected",
                True,
            )
        decisions = real_state.load_decisions_for_architect(
            caller_id,
            include_archived=bool(args.get("include_archived", False)),
        )
        if status_filter:
            decisions = [
                decision for decision in decisions
                if str(decision.get("status", "") or "").strip() == status_filter
            ]
        return json.dumps({"decisions": decisions}), False

    if tool_name == "journal":
        if caller_kind == "architect":
            entry_type = str(args.get("type", "") or "").strip()
            if entry_type not in _JOURNAL_ENTRY_TYPES:
                return (
                    "type must be one of: decision, observation, checkpoint, plan",
                    True,
                )
            entry = str(args.get("entry", "") or "")
            if not entry:
                return "entry is required", True
            return json.dumps(real_state.architect_journal_append(
                caller_id,
                entry_type,
                entry,
            )), False
        result = await handle_command({
            "cmd": "weaver_journal_append",
            "group": _weaver_group,
            "entry_type": args.get("type", "observation"),
            "entry": args.get("entry", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "journal_read":
        if caller_kind == "architect":
            return json.dumps({
                "type": "journal",
                "entries": real_state.architect_journal_read(
                    caller_id,
                    since=args.get("since", 0),
                    limit=args.get("limit", 20),
                ),
            }), False
        result = await handle_command({
            "cmd": "weaver_journal_read",
            "group": _weaver_group,
            "tail": args.get("tail", 20),
            "entry_type": args.get("type", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    # -- Interaction tools --------------------------------------------------

    if tool_name == "engineer_message" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        engineer = real_state.agents.get(engineer_id)
        architect = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        delivered = _deliver_architect_engineer_message(
            real_state,
            architect,
            engineer,
            action="architect_message",
            message=message,
        )
        await _inject_mcp_message(
            handle_command, architect, engineer, delivered, message
        )
        return json.dumps({
            "type": "ok",
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
            "engineer_id": engineer.id,
        }), False

    if tool_name == "message_architect" and caller_kind == "engineer":
        architect_ident = str(args.get("architect_id", "") or "").strip()
        if not architect_ident:
            return "architect_id is required", True
        architect, architect_error = _resolve_architect_for_engineer(
            real_state, caller_id, architect_ident
        )
        if not architect:
            return architect_error, True
        engineer = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        delivered = _deliver_architect_engineer_message(
            real_state,
            engineer,
            architect,
            action="engineer_message_architect",
            message=message,
        )
        await _inject_mcp_message(
            handle_command, engineer, architect, delivered, message
        )
        return json.dumps({
            "type": "ok",
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
            "architect_id": architect.id,
        }), False

    if tool_name == "reply" and caller_kind in {"architect", "engineer"}:
        caller = real_state.agents.get(str(caller_id or "").strip())
        entry, message_error = _load_message_entry(
            caller, args.get("message_id", "")
        )
        if not entry:
            return message_error, True
        peer_id = str(entry.get("peer_id", "") or "").strip()
        peer = real_state.agents.get(peer_id)
        if caller_kind == "architect":
            engineer_id, engineer_error = _resolve_architect_hired_engineer(
                real_state, caller_id, peer_id
            )
            if not engineer_id:
                return engineer_error, True
            peer = real_state.agents.get(engineer_id)
            action = "architect_reply"
        else:
            architect, architect_error = _resolve_architect_for_engineer(
                real_state, caller_id, peer_id
            )
            if not architect:
                return architect_error, True
            peer = architect
            action = "engineer_reply"
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        delivered = _deliver_architect_engineer_message(
            real_state,
            caller,
            peer,
            action=action,
            message=message,
            reply_to_id=str(entry.get("id", "") or "").strip(),
            thread_id=str(entry.get("thread_id", "") or "").strip(),
        )
        await _inject_mcp_message(
            handle_command, caller, peer, delivered, message
        )
        return json.dumps({
            "type": "ok",
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
        }), False

    if tool_name == "agent_message":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True

        result = await handle_command({
            "cmd": "weaver_message",
            "agent_id": agent_id,
            "message": args.get("message", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "ask":
        question = args.get("question", "").strip()
        if not question:
            return "Question is required", True

        result = await handle_command({
            "cmd": "weaver_ask",
            "group": _weaver_group,
            "question": question,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Question posted to the Engineers panel. Event pushes have "
            "been paused. The human will see your question and reply "
            "via the panel or directly in this terminal.\n\n"
            f"After the human responds, call "
            f"{tool_name_with_prefix(tool_prefix, 'resume')} to unpause "
            "event delivery."
        ), False

    if tool_name == "note":
        message = args.get("message", "").strip()
        if not message:
            return "Message is required", True
        kind = args.get("kind", "note")
        if kind not in {"note", "question"}:
            return "kind must be 'note' or 'question'", True

        result = await handle_command({
            "cmd": "weaver_note",
            "group": _weaver_group,
            "message": message,
            "kind": kind,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Note posted to the Engineers panel without pausing event "
            "delivery. It will remain visible until dismissed."
        ), False

    if tool_name == "agent_close":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        result = await handle_command({
            "cmd": "remove_agent",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Agent closed"}), False

    if tool_name == "agent_relaunch":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell:
            return f"Agent not found: {agent_ident}", True
        if cell.status != "stopped":
            return f"Agent is not stopped (status: {cell.status})", True
        result = await handle_command({
            "cmd": "relaunch_agent",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": f"Agent {cell.slug} relaunched"}), False

    # -- Worktree tools -----------------------------------------------------

    if tool_name == "merge":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True

        # First check for conflicts / merge boundary eligibility
        force_stale_base = bool(args.get("force_stale_base"))
        result, error_text, blocked = await _run_worktree_merge_check_with_options(
            handle_command,
            agent_id,
            allow_stale_base=force_stale_base,
        )
        if blocked:
            return error_text, True
        if result and not result.get("clean", True):
            conflict_list = _format_worktree_conflicts(
                result.get("conflicts", [])
            )
            return (
                f"Merge has conflicts:\n{conflict_list}\n\n"
                f"Run {tool_name_with_prefix(tool_prefix, 'rebase')} on "
                f"{cell.slug or cell.id} to replay "
                f"{cell.worktree_branch} onto {cell.worktree_base_branch}, "
                f"then retry {tool_name_with_prefix(tool_prefix, 'merge')}. "
                "Ask the human only if the rebase still fails."
            ), True

        # Proceed with merge
        payload = {"cmd": "worktree_merge", "id": agent_id}
        msg = args.get("message", "")
        if msg:
            payload["message"] = msg
        if "close_agent_on_merge" in args:
            payload["close_agent_on_merge"] = bool(
                args.get("close_agent_on_merge")
            )
        if "remove_worktree_on_merge" in args:
            payload["remove_worktree_on_merge"] = bool(
                args.get("remove_worktree_on_merge")
            )
        if force_stale_base:
            payload["force_stale_base"] = True
        result = await handle_command(payload)
        if result and result.get("ok") is False:
            error = result.get("error", "Merge failed")
            if "conflict" in error.lower():
                fresh_check, _, _ = await _run_worktree_merge_check(
                    handle_command, agent_id
                )
                conflict_text = ""
                if fresh_check and not fresh_check.get("clean", True):
                    conflict_text = (
                        "\n\nConflicts:\n"
                        + _format_worktree_conflicts(
                            fresh_check.get("conflicts", [])
                        )
                    )
                return (
                    f"Merge failed: {error}{conflict_text}\n\n"
                    f"Run {tool_name_with_prefix(tool_prefix, 'rebase')} on "
                    f"{cell.slug or cell.id} and retry "
                    f"{tool_name_with_prefix(tool_prefix, 'merge')}. "
                    "Ask the human only if the rebase still fails."
                ), True
            return error, True
        cleanup = result.get("cleanup", {}) if result else {}
        cleanup_errors = cleanup.get("errors", [])
        if cleanup_errors:
            return (
                f"Merged {cell.worktree_branch} into "
                f"{cell.worktree_base_branch}, but cleanup failed:\n"
                + "\n".join(f"  - {err}" for err in cleanup_errors)
            ), True
        return json.dumps({
            "type": "ok",
            "message": f"Merged {cell.worktree_branch} into "
                       f"{cell.worktree_base_branch}",
            "sha": result.get("sha", ""),
            "cleanup": cleanup,
        }), False

    if tool_name == "rebase":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True

        check, error_text, blocked = (
            await _run_worktree_merge_check_with_options(
                handle_command,
                agent_id,
                allow_dirty=True,
                allow_stale_base=True,
            )
        )
        if blocked:
            return error_text, True

        conflicts_before = check.get("conflicts", [])
        result = await handle_command({
            "cmd": "worktree_rebase",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if result and result.get("ok") is False:
            conflicts = result.get("conflicts", []) or conflicts_before
            if conflicts:
                return (
                    f"{result.get('error', 'Rebase failed')}\n\n"
                    "Conflicts:\n"
                    f"{_format_worktree_conflicts(conflicts)}\n\n"
                    "The rebase was aborted and the worktree should be "
                    "clean. Ask the human if you want a manual conflict-"
                    "resolution plan."
                ), True
            return result.get("error", "Rebase failed"), True

        postcheck, error_text, blocked = await _run_worktree_merge_check(
            handle_command, agent_id
        )
        if blocked:
            return error_text, True

        return json.dumps({
            "type": "ok",
            "message": f"Rebased {cell.worktree_branch} onto "
                       f"{cell.worktree_base_branch}",
            "merge_ready": postcheck.get("clean", False),
            "default_message": postcheck.get("default_message", ""),
            "conflicts_before": conflicts_before,
            "conflicts_after": postcheck.get("conflicts", []),
            "boundary": postcheck.get("boundary"),
            "clean_boundary": postcheck.get("clean_boundary"),
        }), False

    if tool_name == "create_pr":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True

        payload = {"cmd": "worktree_create_pr", "id": agent_id}
        title = args.get("title", "")
        if title:
            payload["title"] = title
        body = args.get("body", "")
        if body:
            payload["body"] = body
        result = await handle_command(payload)
        if result and result.get("error"):
            return result["error"], True
        return json.dumps({
            "type": "ok",
            "url": result.get("url", ""),
            "message": result.get("message", "PR created"),
        }), False

    if tool_name == "diff":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        if not cell.worktree_base_branch:
            return "Agent has no base branch configured", True

        result = await handle_command({
            "cmd": "worktree_diff",
            "id": agent_id,
            "stat_only": args.get("stat_only", False),
            "summary_only": args.get("summary_only", False),
            "paths": args.get("paths", []),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if args.get("summary_only", False):
            warning = str(result.get("stale_base_warning", "") or "").strip()
            if warning:
                return f"{warning}\n\n{json.dumps(result)}", False
            return json.dumps(result), False
        return result.get("diff", "No changes"), False

    if tool_name == "worktree_remove":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        result = await handle_command({
            "cmd": "worktree_remove",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Worktree removed"}), False

    if tool_name == "worktree_checkpoint":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        result = await handle_command({
            "cmd": "worktree_checkpoint",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Checkpoint created"}), False

    return f"Unknown {tool_prefix.rstrip('_')} tool: {name}", True
