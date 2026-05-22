"""Shared MCP tool implementation for engineer-scoped orchestration tools.

This module contains the shared read/write tool logic used by the
``engineer_*`` and ``architect_*`` namespaces. Scoping is caller-driven
via ``caller_kind`` + ``caller_id``.

Security note: v1 keeps Torque's local-trust model. Environment/header
spoofing protections are out of scope for this stage. The server HTTP
command surface is user-operated and trusted; cross-kind communication
graph enforcement lives in these MCP tool surfaces.
"""

import copy
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone

from .config import log
from .deploy_state import architect_deploy_state_payload
from .digest_routing import resolve_digest_recipients
from .direct_message_mirrors import save_direct_ask_mirror
from .mcp_retry import (
    derive_idempotency_key,
    is_mcp_pr_phase,
    is_mcp_pr_phase_retryable,
)
from .mcp_engineer_tools.shared import (
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
from .server_prompts import build_engineer_deliverable_awareness
from .identity import prepend_agent_identity_anchor
from .state import (
    ARCHIVED_LANE,
    board_task_is_closed,
    get_engineer_notification_preset,
    normalize_default_worker_concurrency,
    normalize_engineer_digest_verbosity,
    task_counts_as_done,
    task_is_engineer_message_followup,
)
from .task_health import HEALTH_SEVERITY
from .engineer_hints import compute_engineer_hints
from .engineer_session_map import build_engineer_session_map
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
_JOURNAL_ENTRY_TYPE_NAMES = (
    "decision", "observation", "checkpoint", "plan",
    "note_dismissed", "qa",
)
_JOURNAL_ENTRY_TYPES = set(_JOURNAL_ENTRY_TYPE_NAMES)
_ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPE_NAMES = (
    "decision", "observation", "checkpoint", "plan",
)
_ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPES = set(
    _ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPE_NAMES
)
_HEALTH_SUMMARY_SILENT_AFTER_SECS = 5 * 60
_HEALTH_SUMMARY_LIMIT = 120
_ARCHITECT_BOARD_SUMMARY_TASK_LIMIT = 20
_ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT = 120
_ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT = 10_000
_ARCHITECT_EVENTS_RECENT_DEFAULT_LIMIT = 20
_ARCHITECT_EVENTS_RECENT_MAX_LIMIT = 100
_ARCHITECT_EVENTS_RECENT_LOAD_LIMIT = 500
_ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT = 120
_ARCHITECT_EVENTS_RECENT_RESPONSE_LIMIT = 10_000
_ARCHITECT_TASK_CHAIN_NODE_LIMIT = 50
_ARCHITECT_TASK_LIST_DEFAULT_LIMIT = 100
_ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT = 16 * 1024
_ARCHITECT_PEER_INBOX_DEFAULT_LIMIT = 20
_ARCHITECT_PEER_INBOX_MAX_LIMIT = 100
_ARCHITECT_PEER_SUMMARY_LOAD_LIMIT = 1000
_DISPATCH_SHAPE_VALID_BATCH_STATUSES = {
    "dispatched",
    "queued",
    "deferred",
    "cap_raised",
}
_TASK_DISPATCH_LAUNCH_OVERRIDE_ARGS = (
    "name",
    "agent_type",
    "command",
    "model",
    "reasoning_effort",
)

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


def _record_engineer_dispatch_shape(state, **kwargs):
    recorder = getattr(state, "record_engineer_dispatch_shape", None)
    if not callable(recorder):
        return None
    try:
        return recorder(**kwargs)
    except Exception:
        log.exception("Failed to record engineer dispatch shape metric")
        return None


def _engineer_dispatch_shape_summary(
        state,
        engineer_id: str,
        *,
        group: str = "",
        window: int = 20) -> dict:
    summarizer = getattr(state, "engineer_dispatch_shape_summary", None)
    if not callable(summarizer):
        return {}
    try:
        return summarizer(engineer_id, group=group, window=window)
    except Exception:
        log.exception("Failed to summarize engineer dispatch shape metric")
        return {}


def _has_task_dispatch_launch_overrides(args: dict) -> bool:
    return any(
        bool(str(args.get(key, "") or "").strip())
        for key in _TASK_DISPATCH_LAUNCH_OVERRIDE_ARGS
    )


def _batch_dispatch_shape(valid_entries: list[dict]) -> tuple[str, dict]:
    agent_group_counts: dict[str, int] = {}
    statuses: dict[str, int] = {}
    task_ids: list[str] = []
    for entry in valid_entries:
        status = str(entry.get("status", "") or "").strip()
        if status:
            statuses[status] = statuses.get(status, 0) + 1
        task_id = str(entry.get("task_id", "") or "").strip()
        if task_id:
            task_ids.append(task_id)
        agent_group = str(entry.get("agent_group", "") or "").strip()
        if agent_group:
            agent_group_counts[agent_group] = (
                agent_group_counts.get(agent_group, 0) + 1
            )
    clustered_entry_count = sum(
        count for count in agent_group_counts.values()
        if count > 1
    )
    valid_entry_count = len(valid_entries)
    shape = (
        "warm_cluster"
        if clustered_entry_count
        else "batch"
        if valid_entry_count > 1
        else "serial"
    )
    metadata = {
        "entry_count": valid_entry_count,
        "statuses": statuses,
        "agent_group_counts": agent_group_counts,
        "clustered_entry_count": clustered_entry_count,
        "independent_entry_count": valid_entry_count - clustered_entry_count,
    }
    return shape, {"task_ids": task_ids, "metadata": metadata}


def tool_name_with_prefix(tool_prefix: str, suffix: str) -> str:
    return f"{str(tool_prefix or '').rstrip('_')}_{suffix}"


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_engineer_id", "") or "").strip()


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


def _task_board_sync_inline_state(task) -> dict:
    """Return the compact board-sync state exposed by MCP read surfaces."""
    sync = getattr(task, "board_sync", {}) or {}
    if not isinstance(sync, dict) or not sync:
        return {}
    payload = {
        "provider": str(sync.get("provider", "") or ""),
        "sync_state": str(sync.get("sync_state", "") or ""),
        "last_error": str(sync.get("last_error", "") or ""),
    }
    return payload if any(payload.values()) else {}


def _attach_task_board_sync_inline_state(item: dict, task) -> dict:
    board_sync = _task_board_sync_inline_state(task)
    if board_sync:
        item["board_sync"] = board_sync
    return item


def _board_sync_summary_payload(tasks) -> dict:
    items = []
    for task in tasks:
        board_sync = _task_board_sync_inline_state(task)
        if not board_sync:
            continue
        items.append({
            "id": getattr(task, "id", "") or "",
            "title": _summary_task_title(task),
            **board_sync,
        })
    if not items:
        return {}
    error_count = sum(1 for item in items if item.get("sync_state") == "error")
    return {
        "count": len(items),
        "error_count": error_count,
        "items": items[:10],
        "truncated": len(items) > 10,
    }


def _architect_board_summary_task_item(task, *, created_by: str) -> dict:
    item = {
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
    suggested_specialization = str(
        getattr(task, "suggested_specialization", "") or ""
    ).strip()
    if suggested_specialization:
        item["suggested_specialization"] = suggested_specialization
    _attach_task_board_sync_inline_state(item, task)
    return item


def _normalize_architect_task_list_label_filter(value) -> tuple[list[str], str]:
    if value in (None, ""):
        return [], ""
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, list):
        raw_values = value
    else:
        return [], "label_filter must be a string or list of strings"

    labels = []
    seen = set()
    for item in raw_values:
        if not isinstance(item, str):
            return [], "label_filter entries must be strings"
        label = item.strip()
        if not label or label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return labels, ""


async def _validate_task_update_action_name(
        action_name: str,
        group: str,
        handle_command) -> str:
    """Return an error message if an architect action binding is invalid."""
    name = str(action_name or "").strip()
    if not name:
        return ""
    result = await handle_command({
        "cmd": "list_actions",
        "group": str(group or "").strip(),
    })
    if isinstance(result, dict) and result.get("type") == "error":
        return (
            "Unable to validate action_name via ActionManager.list_actions(): "
            f"{result.get('message', 'Unknown error')}"
        )
    actions = result.get("actions") if isinstance(result, dict) else None
    if not isinstance(actions, list):
        return (
            "Unable to validate action_name via ActionManager.list_actions(): "
            "list_actions returned an unexpected response"
        )
    action_names = {
        str(item.get("name", "") if isinstance(item, dict) else item).strip()
        for item in actions
        if str(item.get("name", "") if isinstance(item, dict) else item).strip()
    }
    if name not in action_names:
        return (
            f"Unknown action_name '{name}' "
            "(validated against ActionManager.list_actions() "
            f"for group '{str(group or '').strip()}')"
        )
    return ""


def _normalize_architect_task_list_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_TASK_LIST_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit must be an integer"
    if limit < 0:
        return 0, "limit must be at least 0"
    return limit, ""


def _architect_task_creator_filter_matches(task, creator_filter: str
                                           ) -> tuple[bool, str]:
    creator_filter = str(creator_filter or "").strip()
    if not creator_filter:
        return True, ""
    lower = creator_filter.lower()
    created_by = _task_created_by_classifier(task)
    if lower == "user":
        return created_by == "user", ""
    if lower == "architect":
        return created_by.startswith("architect:"), ""
    if lower == "system":
        return created_by == "system", ""
    if lower.startswith("engineer:"):
        engineer_id = creator_filter.split(":", 1)[1].strip()
        if not engineer_id:
            return False, "creator_filter engineer:<id> requires an id"
        created_by_engineer_id = str(
            getattr(task, "created_by_engineer_id", "") or ""
        ).strip()
        return created_by_engineer_id == engineer_id, ""
    return (
        False,
        "creator_filter must be one of: user, architect, engineer:<id>, system",
    )


def _validate_architect_task_creator_filter(creator_filter: str) -> str:
    if not str(creator_filter or "").strip():
        return ""
    _matches, error = _architect_task_creator_filter_matches(
        None,
        creator_filter,
    )
    return error


def _architect_task_list_sort_key(state, item: dict) -> tuple[int, int, str, str]:
    lane_order = {lane: idx for idx, lane in enumerate(state.board_lanes)}
    task = state.board_tasks.get(item.get("id", ""))
    return (
        lane_order.get(item.get("lane", ""), len(lane_order)),
        getattr(task, "position", 0) if task else 0,
        str(item.get("title", "") or "").lower(),
        str(item.get("id", "") or ""),
    )


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
    if _agent_is_tombstoned(state, cell):
        return False
    return str(getattr(cell, "kind", "") or "").strip() == "engineer"


def _is_architect_cell(cell, state=None) -> bool:
    return bool(
        cell
        and getattr(cell, "cell_type", "") == "agent"
        and not (state is not None and _agent_is_tombstoned(state, cell))
        and str(getattr(cell, "kind", "") or "").strip() == "architect"
    )


def _agent_is_tombstoned(state, cell) -> bool:
    checker = getattr(state, "agent_is_tombstoned", None)
    if callable(checker):
        return bool(checker(cell))
    try:
        return float(getattr(cell, "deleted_at", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _resolve_agent_including_tombstoned(state, identifier: str) -> str | None:
    ident = str(identifier or "").strip()
    if not ident:
        return None
    cell = state.agents.get(ident)
    if cell and getattr(cell, "cell_type", "") == "agent":
        return str(getattr(cell, "id", "") or "")
    ident_lower = ident.lower()
    iterator = getattr(state, "iter_agents", None)
    cells = (
        iterator(include_tombstoned=True)
        if callable(iterator) else state.agents.values()
    )
    for cell in cells:
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "slug", "") or "").strip().lower() == ident_lower:
            return str(getattr(cell, "id", "") or "")
    cells = (
        iterator(include_tombstoned=True)
        if callable(iterator) else state.agents.values()
    )
    for cell in cells:
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "name", "") or "").strip().lower() == ident_lower:
            return str(getattr(cell, "id", "") or "")
    cells = (
        iterator(include_tombstoned=True)
        if callable(iterator) else state.agents.values()
    )
    for cell in cells:
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "id", "") or "").startswith(ident):
            return str(getattr(cell, "id", "") or "")
    return None


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


def _architect_dismissed_error(architect_id: str) -> str:
    return json.dumps({
        "type": "error",
        "reason": "architect_dismissed",
        "message": f"architect {architect_id} is dismissed",
        "architect_id": str(architect_id or "").strip(),
    })


_ARCHITECT_READ_TOOL_NAMES = frozenset({
    "action_show",
    "actions_list",
    "agent_show",
    "agents_list",
    "board_list",
    "board_summary",
    "decision_list",
    "deploy_state",
    "diff",
    "engineer_journal_read",
    "engineer_list",
    "engineer_pending_question",
    "events",
    "events_recent",
    "get_architect_settings",
    "journal_read",
    "mcp_calls",
    "pending_hire_list",
    "pending_hire_status",
    "peer_inbox",
    "peer_list",
    "session_map",
    "specialization_show",
    "specializations_list",
    "stream_show",
    "streams_list",
    "task_chain",
    "task_list",
    "task_show",
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


def _architect_visible_engineers(
        state,
        caller_id: str,
        *,
        include_tombstoned: bool = False) -> dict[str, tuple[object, str]]:
    caller_group = _caller_group(state, caller_id)
    if not caller_group:
        return {}
    visible = {}
    for cell in state.iter_agents(include_tombstoned=include_tombstoned):
        if not cell or getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            continue
        if not include_tombstoned and _agent_is_tombstoned(state, cell):
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
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    engineer = state.agents.get(engineer_id)
    if _agent_is_tombstoned(state, engineer):
        return None, "engineer is tombstoned"
    visible = _architect_visible_engineers(state, caller_id)
    if engineer_id not in visible:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _resolve_architect_hired_engineer(state, caller_id: str,
                                      engineer_ident: str, *,
                                      include_tombstoned: bool = False
                                      ) -> tuple[str | None, str]:
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    engineer = state.agents.get(engineer_id)
    if _agent_is_tombstoned(state, engineer) and not include_tombstoned:
        return None, "engineer is tombstoned"
    hired_ids = {
        eid for eid, (_cell, relation) in _architect_visible_engineers(
            state, caller_id, include_tombstoned=include_tombstoned
        ).items()
        if relation == "hired"
    }
    if engineer_id not in hired_ids:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _resolve_group_engineer(state, caller_id: str,
                            engineer_ident: str) -> tuple[str | None, str]:
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    engineer = state.agents.get(engineer_id)
    if _agent_is_tombstoned(state, engineer):
        return None, "engineer is tombstoned"
    if not _is_engineer_like_cell(state, engineer):
        return None, "engineer not found in scope"
    caller_group = _caller_group(state, caller_id)
    engineer_group = str(getattr(engineer, "group", "") or "").strip()
    if not caller_group or engineer_group != caller_group:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _event_task_chain(state, event: dict) -> list:
    task_id = str((event or {}).get("task_id", "") or "").strip()
    task = getattr(state, "board_tasks", {}).get(task_id)
    chain = []
    seen: set[str] = set()
    while task:
        current_id = str(getattr(task, "id", "") or "").strip()
        if current_id:
            if current_id in seen:
                break
            seen.add(current_id)
        chain.append(task)
        next_id = str(getattr(task, "parent_task_id", "") or "").strip()
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        if not next_id and root_id and root_id != current_id:
            next_id = root_id
        task = getattr(state, "board_tasks", {}).get(next_id) if next_id else None
    return chain


def _event_agent_kind(cell) -> str:
    kind = str(getattr(cell, "kind", "") or "").strip() if cell else ""
    if kind:
        return kind
    if cell and str(getattr(cell, "cell_type", "") or "").strip() == "terminal":
        return "terminal"
    return ""


def _event_attribution(state, event: dict) -> dict:
    cell_id = str((event or {}).get("cell_id", "") or "").strip()
    cell = getattr(state, "agents", {}).get(cell_id)
    task_chain = _event_task_chain(state, event)
    task = task_chain[0] if task_chain else None

    assigned_engineer_id = ""
    created_by_architect_id = ""
    created_by_engineer_id = ""
    task_agent_ids: list[str] = []
    for chain_task in task_chain:
        if not assigned_engineer_id:
            assigned_engineer_id = _effective_assigned_engineer_id(chain_task)
        if not created_by_architect_id:
            created_by_architect_id = str(
                getattr(chain_task, "created_by_architect_id", "") or ""
            ).strip()
        if not created_by_engineer_id:
            created_by_engineer_id = str(
                getattr(chain_task, "created_by_engineer_id", "") or ""
            ).strip()
        for field_name in ("agent_id", "reply_agent_id"):
            task_agent_id = str(getattr(chain_task, field_name, "") or "").strip()
            if task_agent_id:
                task_agent_ids.append(task_agent_id)

    owner_engineer_id = ""
    if cell:
        owner_engineer_id = _effective_owner_engineer_id(cell)
    if not owner_engineer_id:
        for task_agent_id in task_agent_ids:
            task_agent = getattr(state, "agents", {}).get(task_agent_id)
            owner_engineer_id = _effective_owner_engineer_id(task_agent)
            if owner_engineer_id:
                break

    agent_name = str((event or {}).get("agent_name", "") or "")
    if not agent_name and cell:
        agent_name = str(getattr(cell, "name", "") or "")

    return {
        "cell": cell,
        "task": task,
        "task_chain": task_chain,
        "task_agent_ids": task_agent_ids,
        "cell_id": cell_id,
        "agent_name": agent_name,
        "agent_kind": _event_agent_kind(cell),
        "assigned_engineer_id": assigned_engineer_id,
        "created_by_architect_id": created_by_architect_id,
        "created_by_engineer_id": created_by_engineer_id,
        "owner_engineer_id": owner_engineer_id,
    }


def _event_involves_engineer(state, event: dict, engineer_id: str,
                             attribution: dict | None = None) -> bool:
    engineer_id = str(engineer_id or "").strip()
    if not engineer_id:
        return False
    attribution = attribution or _event_attribution(state, event)
    if engineer_id in {
        str(attribution.get("assigned_engineer_id", "") or "").strip(),
        str(attribution.get("owner_engineer_id", "") or "").strip(),
        str(attribution.get("created_by_engineer_id", "") or "").strip(),
        str(attribution.get("cell_id", "") or "").strip(),
    }:
        return True
    if engineer_id in {
        str(agent_id or "").strip()
        for agent_id in attribution.get("task_agent_ids", []) or []
    }:
        return True
    for agent_id in attribution.get("task_agent_ids", []) or []:
        task_agent = getattr(state, "agents", {}).get(str(agent_id or "").strip())
        if _effective_owner_engineer_id(task_agent) == engineer_id:
            return True
    return False


def _architect_event_visible(state, caller_id: str, caller_group: str,
                             event: dict, attribution: dict) -> bool:
    if str((event or {}).get("group", "") or "").strip() != caller_group:
        return False
    caller_id = str(caller_id or "").strip()
    if str(attribution.get("cell_id", "") or "").strip() == caller_id:
        return True
    if str(attribution.get("created_by_architect_id", "") or "").strip() == caller_id:
        return True
    hired_engineers = _architect_hired_engineer_ids(state, caller_id)
    if str(attribution.get("assigned_engineer_id", "") or "").strip() in hired_engineers:
        return True
    if str(attribution.get("owner_engineer_id", "") or "").strip() in hired_engineers:
        return True
    cell = attribution.get("cell")
    if (
        _is_engineer_like_cell(state, cell)
        and str(getattr(cell, "id", "") or "").strip() in hired_engineers
    ):
        return True
    return any(
        _event_involves_engineer(state, event, engineer_id, attribution)
        for engineer_id in hired_engineers
    )


def _task_chain_sort_key(task) -> tuple[int, str, str]:
    try:
        depth = int(getattr(task, "pipeline_depth", 0) or 0)
    except (TypeError, ValueError):
        depth = 0
    return (
        depth,
        str(getattr(task, "created_at", "") or ""),
        str(getattr(task, "id", "") or ""),
    )


def _task_chain_depth(task) -> int:
    try:
        return max(0, int(getattr(task, "pipeline_depth", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _resolve_task_chain_root(state, task):
    if not state or not task:
        return None
    root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
    if root_id:
        root = state.board_tasks.get(root_id)
        if root:
            return root
    current = task
    seen = {str(getattr(task, "id", "") or "").strip()}
    while current:
        parent_id = str(getattr(current, "parent_task_id", "") or "").strip()
        if not parent_id or parent_id in seen:
            break
        parent = state.board_tasks.get(parent_id)
        if not parent:
            break
        current = parent
        seen.add(parent_id)
    return current


def _architect_task_chain_root_visible(state, caller_id: str, root_task) -> bool:
    if not state or not root_task:
        return False
    caller_id = str(caller_id or "").strip()
    if not caller_id:
        return False
    if str(getattr(root_task, "created_by_architect_id", "") or "").strip() == caller_id:
        return True
    return _effective_assigned_engineer_id(root_task) in _architect_hired_engineer_ids(
        state,
        caller_id,
    )


def _collect_task_chain_tasks(state, root_task) -> dict[str, object]:
    if not state or not root_task:
        return {}
    root_id = str(getattr(root_task, "id", "") or "").strip()
    root_group = str(getattr(root_task, "group", "") or "").strip()
    tasks_by_id: dict[str, object] = {}

    def _include(candidate) -> None:
        if not candidate:
            return
        task_id = str(getattr(candidate, "id", "") or "").strip()
        if not task_id:
            return
        if root_group and str(getattr(candidate, "group", "") or "").strip() != root_group:
            return
        tasks_by_id[task_id] = candidate

    _include(root_task)
    for task in state.board_get_chain(root_id):
        _include(task)

    pending = [root_id]
    seen: set[str] = set()
    while pending:
        current_id = pending.pop(0)
        if current_id in seen:
            continue
        seen.add(current_id)
        for child in sorted(state.board_get_children(current_id), key=_task_chain_sort_key):
            child_id = str(getattr(child, "id", "") or "").strip()
            if not child_id:
                continue
            _include(child)
            if child_id not in seen:
                pending.append(child_id)
    return tasks_by_id


def _build_task_chain_tree(root_task, tasks_by_id: dict[str, object]
                           ) -> tuple[dict, dict, bool]:
    root_id = str(getattr(root_task, "id", "") or "").strip()
    children_by_parent: dict[str, list[str]] = {
        task_id: [] for task_id in tasks_by_id
    }
    orphan_ids: list[str] = []
    for task_id, task in tasks_by_id.items():
        if task_id == root_id:
            continue
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        if parent_id and parent_id in tasks_by_id and parent_id != task_id:
            children_by_parent.setdefault(parent_id, []).append(task_id)
        else:
            orphan_ids.append(task_id)
    for child_ids in children_by_parent.values():
        child_ids.sort(key=lambda tid: _task_chain_sort_key(tasks_by_id[tid]))
    if orphan_ids:
        root_children = children_by_parent.setdefault(root_id, [])
        root_children.extend(sorted(
            orphan_ids,
            key=lambda tid: _task_chain_sort_key(tasks_by_id[tid]),
        ))
        root_children[:] = list(dict.fromkeys(root_children))

    stats = {
        "total_nodes": 0,
        "done": 0,
        "in_progress": 0,
        "max_depth": 0,
    }
    truncated = False
    visited: set[str] = set()

    def _build(task_id: str, depth: int) -> dict | None:
        nonlocal truncated
        if truncated or task_id in visited:
            return None
        task = tasks_by_id.get(task_id)
        if not task:
            return None
        if stats["total_nodes"] >= _ARCHITECT_TASK_CHAIN_NODE_LIMIT:
            truncated = True
            return None
        visited.add(task_id)
        stats["total_nodes"] += 1
        if task_counts_as_done(task):
            stats["done"] += 1
        if str(getattr(task, "lane", "") or "").strip() == "In Progress":
            stats["in_progress"] += 1
        stats["max_depth"] = max(stats["max_depth"], depth, _task_chain_depth(task))
        children = []
        for child_id in children_by_parent.get(task_id, []):
            child = _build(child_id, depth + 1)
            if child is not None:
                children.append(child)
            if truncated:
                break
        return {
            "task_id": task.id,
            "title": getattr(task, "task", "") or "",
            "lane": getattr(task, "lane", "") or "",
            "status": getattr(task, "status", "") or "",
            "action_name": getattr(task, "action_name", "") or "",
            "agent_id": getattr(task, "agent_id", "") or "",
            "children": children,
        }

    tree = _build(root_id, 0) or {
        "task_id": root_id,
        "title": getattr(root_task, "task", "") or "",
        "lane": getattr(root_task, "lane", "") or "",
        "status": getattr(root_task, "status", "") or "",
        "action_name": getattr(root_task, "action_name", "") or "",
        "agent_id": getattr(root_task, "agent_id", "") or "",
        "children": [],
    }
    return tree, stats, truncated


def _architect_task_chain_json(state, caller_id: str, task_ident: str) -> tuple[str, bool]:
    task_id = _resolve_task(state, task_ident)
    if not task_id:
        return "Task not found", True
    task = state.board_tasks.get(task_id)
    if not task:
        return "Task not found", True
    root_task = _resolve_task_chain_root(state, task) or task
    if not _architect_task_chain_root_visible(state, caller_id, root_task):
        return "Task chain root not visible to this architect", True
    tasks_by_id = _collect_task_chain_tasks(state, root_task)
    tree, stats, truncated = _build_task_chain_tree(root_task, tasks_by_id)
    payload = {
        "root": {
            "task_id": root_task.id,
            "title": getattr(root_task, "task", "") or "",
            "lane": getattr(root_task, "lane", "") or "",
            "status": getattr(root_task, "status", "") or "",
            "assigned_engineer_id": _effective_assigned_engineer_id(root_task),
        },
        "focus_task_id": task_id,
        "tree": tree,
        "stats": stats,
    }
    if truncated:
        payload["truncated"] = True
    return _compact_json(payload), False


def _clip_event_message(value: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= _ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT:
        return text
    return text[:_ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT - 1].rstrip() + "…"


def _normalize_architect_events_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_EVENTS_RECENT_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit must be an integer"
    if limit < 1:
        return 0, "limit must be at least 1"
    return min(limit, _ARCHITECT_EVENTS_RECENT_MAX_LIMIT), ""


def _normalize_since(value) -> tuple[float, str]:
    if value in (None, ""):
        return 0.0, ""
    try:
        since = float(value)
    except (TypeError, ValueError):
        return 0.0, "since must be a number"
    return since, ""


def _load_recent_panel_events(state) -> list[dict]:
    db = getattr(state, "db", None)
    if db and hasattr(db, "load_panel_events"):
        return list(reversed(db.load_panel_events(
            limit=_ARCHITECT_EVENTS_RECENT_LOAD_LIMIT
        )))
    panel_log = getattr(state, "panel_log", None)
    if panel_log and hasattr(panel_log, "get_recent"):
        return list(reversed(panel_log.get_recent(
            _ARCHITECT_EVENTS_RECENT_LOAD_LIMIT
        )))
    return []


def _peer_row_created_at(row: dict) -> float:
    try:
        return float((row or {}).get("created_at", (row or {}).get("timestamp", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _load_recent_architect_peer_rows(
        state,
        architect_id: str,
        *,
        limit: int = _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
        since: float = 0.0) -> list[dict]:
    """Load recent canonical peer-message rows involving one Architect."""
    architect_id = str(architect_id or "").strip()
    if not architect_id:
        return []
    limit = max(1, min(int(limit or _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT), 1000))
    db = getattr(state, "db", None)
    if db and hasattr(db, "load_agent_peer_messages_for_agent"):
        return db.load_agent_peer_messages_for_agent(
            architect_id,
            limit=limit,
            since=since,
        )
    cell = getattr(state, "agents", {}).get(architect_id)
    rows = []
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if "user" in {
            str((entry or {}).get("sender_kind", "") or "").strip(),
            str((entry or {}).get("recipient_kind", "") or "").strip(),
        }:
            continue
        if str((entry or {}).get("action", "") or "").strip() not in {
            "architect_peer_message",
            "architect_peer_reply",
        }:
            continue
        timestamp = _peer_row_created_at(entry)
        if since and timestamp < since:
            continue
        row = dict(entry)
        row.setdefault("created_at", timestamp)
        row.setdefault("group_name", row.get("group", ""))
        rows.append(row)
    rows.sort(
        key=lambda row: (
            _peer_row_created_at(row),
            str((row or {}).get("id", "") or ""),
        ),
        reverse=True,
    )
    return rows[:limit]


def _peer_thread_pending_reply_info(messages: list[dict],
                                    caller_id: str) -> tuple[bool, float]:
    """Return whether a thread needs caller reply plus the pending ack time."""
    latest_outgoing = 0.0
    latest_incoming_ack = 0.0
    caller_id = str(caller_id or "").strip()
    for row in messages:
        ts = _peer_row_created_at(row)
        if str((row or {}).get("sender_id", "") or "").strip() == caller_id:
            latest_outgoing = max(latest_outgoing, ts)
        elif bool((row or {}).get("ack_required", False)):
            latest_incoming_ack = max(latest_incoming_ack, ts)
    return latest_incoming_ack > latest_outgoing, latest_incoming_ack


def _architect_peer_message_summary(state, architect_id: str) -> dict:
    rows = _load_recent_architect_peer_rows(
        state,
        architect_id,
        limit=_ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    )
    architect_id = str(architect_id or "").strip()
    grouped: dict[str, list[dict]] = {}
    sent_count = 0
    received_count = 0
    unread_count = 0
    latest_message_at = 0.0
    for row in rows:
        sender_id = str((row or {}).get("sender_id", "") or "").strip()
        recipient_id = str((row or {}).get("recipient_id", "") or "").strip()
        timestamp = _peer_row_created_at(row)
        latest_message_at = max(latest_message_at, timestamp)
        if sender_id == architect_id:
            sent_count += 1
        if recipient_id == architect_id:
            received_count += 1
            delivery_state = str(
                (row or {}).get("delivery_state", "") or "buffered"
            ).strip()
            if delivery_state != "delivered":
                unread_count += 1
        thread_id = str((row or {}).get("thread_id", "") or "").strip()
        if thread_id:
            grouped.setdefault(thread_id, []).append(row)

    requires_reply_count = 0
    pending_times = []
    for messages in grouped.values():
        messages.sort(
            key=lambda row: (
                _peer_row_created_at(row),
                str((row or {}).get("id", "") or ""),
            )
        )
        requires_reply, pending_at = _peer_thread_pending_reply_info(
            messages,
            architect_id,
        )
        if requires_reply:
            requires_reply_count += 1
            if pending_at:
                pending_times.append(pending_at)

    return {
        "recent_count": len(rows),
        "sent_count": sent_count,
        "received_count": received_count,
        "unread_count": unread_count,
        "ack_required_pending_count": requires_reply_count,
        "requires_reply_count": requires_reply_count,
        "oldest_unanswered_at": min(pending_times) if pending_times else 0.0,
        "latest_message_at": latest_message_at,
        "truncated": len(rows) >= _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    }


def _peer_row_context(row: dict) -> dict:
    summary = str((row or {}).get("context_summary", "") or "")
    if len(summary) > 240:
        summary = summary[:239].rstrip() + "…"
    return {
        "task_ids": list((row or {}).get("context_task_ids", []) or []),
        "engineer_ids": list((row or {}).get("context_engineer_ids", []) or []),
        "decision_ids": list((row or {}).get("context_decision_ids", []) or []),
        "summary": summary,
    }


def _peer_row_involves_engineer(state, row: dict, engineer_id: str) -> bool:
    engineer_id = str(engineer_id or "").strip()
    if not engineer_id:
        return False
    if engineer_id in {
        str(item or "").strip()
        for item in ((row or {}).get("context_engineer_ids", []) or [])
    }:
        return True
    for task_id in (row or {}).get("context_task_ids", []) or []:
        task = getattr(state, "board_tasks", {}).get(str(task_id or "").strip())
        if task and _effective_assigned_engineer_id(task) == engineer_id:
            return True
    return False


def _architect_peer_message_event(state, row: dict, architect_id: str,
                                  requires_reply: bool) -> dict:
    entry = _agent_peer_message_row_to_entry(row, architect_id)
    sender = getattr(state, "agents", {}).get(entry["sender_id"])
    peer = getattr(state, "agents", {}).get(entry["peer_id"])
    context = _peer_row_context(row)
    task_id = context["task_ids"][0] if context["task_ids"] else ""
    assigned_engineer_id = ""
    if task_id:
        task = getattr(state, "board_tasks", {}).get(task_id)
        assigned_engineer_id = _effective_assigned_engineer_id(task) if task else ""
    if not assigned_engineer_id and context["engineer_ids"]:
        assigned_engineer_id = context["engineer_ids"][0]
    return {
        "id": entry["id"],
        "kind": entry["action"],
        "timestamp": float(entry.get("timestamp", 0) or 0),
        "cell_id": entry["sender_id"],
        "agent_name": str(getattr(sender, "name", "") or "").strip()
        or entry["sender_id"],
        "agent_kind": entry["sender_kind"],
        "group": str((row or {}).get("group_name", (row or {}).get("group", "")) or ""),
        "task_id": task_id,
        "assigned_engineer_id": assigned_engineer_id,
        "created_by_architect_id": entry["sender_id"],
        "owner_engineer_id": "",
        "message": _clip_event_message(entry.get("message", "") or ""),
        "digest_recipients": [],
        "message_id": entry["id"],
        "thread_id": entry["thread_id"],
        "reply_to_id": entry["reply_to_id"],
        "direction": entry["direction"],
        "peer_architect_id": entry["peer_id"],
        "peer_name": str(getattr(peer, "name", "") or "").strip()
        or entry["peer_id"],
        "ack_required": bool(entry.get("ack_required", False)),
        "requires_reply": bool(requires_reply),
        "delivery_state": entry["delivery_state"],
        "context": context,
    }


def _recent_architect_peer_message_events(
        state,
        architect_id: str,
        architect_group: str,
        *,
        kind_filter: str = "",
        engineer_filter: str = "",
        since: float = 0.0) -> list[dict]:
    rows = _load_recent_architect_peer_rows(
        state,
        architect_id,
        limit=_ARCHITECT_EVENTS_RECENT_LOAD_LIMIT,
        since=since,
    )
    rows = [
        row for row in rows
        if str((row or {}).get("group_name", (row or {}).get("group", "")) or "").strip()
        == str(architect_group or "").strip()
    ]
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(
            str((row or {}).get("thread_id", "") or "").strip(),
            [],
        ).append(row)
    requires_by_thread: dict[str, bool] = {}
    for thread_id, messages in grouped.items():
        messages.sort(
            key=lambda row: (
                _peer_row_created_at(row),
                str((row or {}).get("id", "") or ""),
            )
        )
        requires_by_thread[thread_id] = _peer_thread_pending_reply_info(
            messages,
            architect_id,
        )[0]

    events = []
    for row in rows:
        action = (
            "architect_peer_reply"
            if str((row or {}).get("reply_to_id", "") or "").strip()
            else "architect_peer_message"
        )
        if kind_filter and kind_filter != action:
            continue
        if engineer_filter and not _peer_row_involves_engineer(
            state,
            row,
            engineer_filter,
        ):
            continue
        thread_id = str((row or {}).get("thread_id", "") or "").strip()
        events.append(_architect_peer_message_event(
            state,
            row,
            architect_id,
            requires_by_thread.get(thread_id, False),
        ))
    return events


def _architect_events_recent_json(state, architect_id: str, architect_group: str,
                                  args: dict) -> tuple[str, bool]:
    limit, limit_error = _normalize_architect_events_limit(args.get("limit"))
    if limit_error:
        return limit_error, True
    since, since_error = _normalize_since(args.get("since"))
    if since_error:
        return since_error, True
    kind_filter = str(args.get("kind_filter", "") or "").strip()
    engineer_filter = str(args.get("engineer_id", "") or "").strip()

    events = []
    for event in _load_recent_panel_events(state):
        if kind_filter and str(event.get("kind", "") or "").strip() != kind_filter:
            continue
        timestamp = float(event.get("timestamp", 0) or 0)
        if since and timestamp < since:
            continue
        attribution = _event_attribution(state, event)
        if not _architect_event_visible(
            state,
            architect_id,
            architect_group,
            event,
            attribution,
        ):
            continue
        if engineer_filter and not _event_involves_engineer(
            state, event, engineer_filter, attribution
        ):
            continue
        events.append({
            "id": str(event.get("id", "") or ""),
            "kind": str(event.get("kind", "") or ""),
            "timestamp": timestamp,
            "cell_id": attribution["cell_id"],
            "agent_name": attribution["agent_name"],
            "agent_kind": attribution["agent_kind"],
            "group": str(event.get("group", "") or ""),
            "task_id": str(event.get("task_id", "") or ""),
            "assigned_engineer_id": attribution["assigned_engineer_id"],
            "created_by_architect_id": attribution["created_by_architect_id"],
            "owner_engineer_id": attribution["owner_engineer_id"],
            "message": _clip_event_message(event.get("message", "") or ""),
            "digest_recipients": resolve_digest_recipients(state, event),
        })

    events.extend(_recent_architect_peer_message_events(
        state,
        architect_id,
        architect_group,
        kind_filter=kind_filter,
        engineer_filter=engineer_filter,
        since=since,
    ))
    events.sort(
        key=lambda event: (
            float((event or {}).get("timestamp", 0) or 0),
            str((event or {}).get("id", "") or ""),
        ),
        reverse=True,
    )
    truncated = len(events) > limit
    payload_events = events[:limit]
    while True:
        payload = {"events": payload_events, "truncated": truncated}
        text = _compact_json(payload)
        if (
            len(text) <= _ARCHITECT_EVENTS_RECENT_RESPONSE_LIMIT
            or not payload_events
        ):
            return text, False
        payload_events = payload_events[:-1]
        truncated = True


def _architect_engineer_pending_question_json(
        state, architect_id: str, args: dict) -> tuple[str, bool]:
    engineer_ident = str(args.get("engineer_id", "") or "").strip()
    if not engineer_ident:
        return "engineer_id is required", True
    engineer_id, engineer_error = _resolve_architect_hired_engineer(
        state, architect_id, engineer_ident
    )
    if not engineer_id:
        return engineer_error, True
    engineer = state.agents.get(engineer_id)
    engineer_group = str(getattr(engineer, "group", "") or "").strip()
    payload = {
        "type": "engineer_pending_question",
        "engineer_id": engineer_id,
        "question": "",
        "set_at": 0.0,
        "paused": False,
        "note": "",
    }
    if not engineer_group:
        payload["note"] = "Engineer has no group."
        return _compact_json(payload), False
    ws = state.get_engineer_settings(engineer_group)
    question = str(getattr(ws, "pending_question", "") or "")
    pending_owner_id = str(
        getattr(ws, "pending_question_actor_id", "") or ""
    ).strip()
    if not question or pending_owner_id != engineer_id:
        payload["note"] = "No pending question for engineer."
        return _compact_json(payload), False
    payload.update({
        "question": question,
        "set_at": float(getattr(ws, "pending_question_set_at", 0.0) or 0.0),
        "paused": bool(getattr(ws, "paused", False)),
        "note": "Question is awaiting human input.",
    })
    return _compact_json(payload), False


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
    if not _is_architect_cell(architect, state):
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


def _resolve_architect_peer(state, caller_id: str,
                            architect_ident: str) -> tuple[object | None, str]:
    """Resolve a same-group Architect peer for a send/reply mutation."""
    architect_ident = str(architect_ident or "").strip()
    if not architect_ident:
        return None, "architect_id is required"
    caller = state.agents.get(str(caller_id or "").strip())
    caller_group = str(getattr(caller, "group", "") or "").strip()
    architect_id = _resolve_agent_including_tombstoned(state, architect_ident)
    if not architect_id:
        return None, f"Architect not found: {architect_ident}"
    if architect_id == str(caller_id or "").strip():
        return None, "cannot message self"
    architect = state.agents.get(architect_id)
    if (
        not architect
        or getattr(architect, "cell_type", "") != "agent"
        or str(getattr(architect, "kind", "") or "").strip() != "architect"
    ):
        return None, f"Architect not found: {architect_ident}"
    if _agent_is_tombstoned(state, architect):
        return None, "architect is tombstoned"
    peer_group = str(getattr(architect, "group", "") or "").strip()
    if not caller_group or peer_group != caller_group:
        return None, "architect not found in scope"
    return architect, ""


def _resolve_architect_peer_filter(
        state,
        caller_id: str,
        architect_ident: str) -> tuple[str, str]:
    """Resolve an optional inbox peer filter without hiding old threads."""
    architect_ident = str(architect_ident or "").strip()
    if not architect_ident:
        return "", ""
    caller = state.agents.get(str(caller_id or "").strip())
    caller_group = str(getattr(caller, "group", "") or "").strip()
    architect_id = _resolve_agent_including_tombstoned(state, architect_ident)
    if not architect_id:
        return "", f"Architect not found: {architect_ident}"
    if architect_id == str(caller_id or "").strip():
        return "", "peer_architect_id cannot be self"
    architect = state.agents.get(architect_id)
    if (
        not architect
        or getattr(architect, "cell_type", "") != "agent"
        or str(getattr(architect, "kind", "") or "").strip() != "architect"
    ):
        return "", f"Architect not found: {architect_ident}"
    peer_group = str(getattr(architect, "group", "") or "").strip()
    if not caller_group or peer_group != caller_group:
        return "", "architect not found in scope"
    return architect_id, ""


def _architect_current_task_summary(state, cell) -> tuple[str, str]:
    current_task_id = str(getattr(cell, "current_task_id", "") or "").strip()
    task = state.board_tasks.get(current_task_id) if current_task_id else None
    if not task:
        for candidate in state.board_tasks.values():
            if str(getattr(candidate, "agent_id", "") or "").strip() == str(
                    getattr(cell, "id", "") or "").strip():
                task = candidate
                current_task_id = str(getattr(candidate, "id", "") or "")
                break
    return current_task_id, str(getattr(task, "task", "") or "") if task else ""


def _architect_peer_item(state, cell) -> dict:
    current_task_id, current_task = _architect_current_task_summary(state, cell)
    return {
        "id": str(getattr(cell, "id", "") or ""),
        "slug": str(getattr(cell, "slug", "") or ""),
        "name": str(getattr(cell, "name", "") or ""),
        "group": str(getattr(cell, "group", "") or ""),
        "status": str(getattr(cell, "status", "") or ""),
        "dismissed_at": _agent_dismissed_at(cell),
        "current_task_id": current_task_id,
        "current_task": current_task,
    }


def _architect_peer_list_json(
        state,
        caller_id: str,
        caller_group: str,
        args: dict) -> tuple[str, bool]:
    include_dismissed, bool_error = _optional_bool_arg(
        args,
        "include_dismissed",
        False,
    )
    if bool_error:
        return bool_error, True
    architects = []
    for cell in state.iter_agents(include_tombstoned=False):
        if str(getattr(cell, "id", "") or "").strip() == str(caller_id or "").strip():
            continue
        if (
            getattr(cell, "cell_type", "") != "agent"
            or str(getattr(cell, "kind", "") or "").strip() != "architect"
            or str(getattr(cell, "group", "") or "").strip() != caller_group
        ):
            continue
        if _agent_dismissed_at(cell) and not include_dismissed:
            continue
        architects.append(_architect_peer_item(state, cell))
    architects.sort(key=lambda item: (
        str(item.get("name", "") or "").lower(),
        str(item.get("id", "") or ""),
    ))
    return _compact_json({
        "type": "architect_peers",
        "architect_id": caller_id,
        "architects": architects,
    }), False


def _peer_context_task_snapshot(task) -> dict:
    return {
        "id": str(getattr(task, "id", "") or ""),
        "slug": str(getattr(task, "slug", "") or ""),
        "title": _summary_task_title(task),
        "lane": str(getattr(task, "lane", "") or ""),
        "status": str(getattr(task, "status", "") or ""),
        "labels": list(getattr(task, "labels", []) or []),
        "assigned_engineer_id": _effective_assigned_engineer_id(task),
        "created_by_architect_id": str(
            getattr(task, "created_by_architect_id", "") or ""
        ),
        "updated_at": str(getattr(task, "updated_at", "") or ""),
    }


def _peer_context_engineer_snapshot(state, engineer) -> dict:
    current_task_id, current_task = _architect_current_task_summary(state, engineer)
    active_assigned = 0
    open_assigned = 0
    for task in state.board_tasks.values():
        if _effective_assigned_engineer_id(task) != str(
                getattr(engineer, "id", "") or "").strip():
            continue
        if not board_task_is_closed(task):
            open_assigned += 1
        if str(getattr(task, "lane", "") or "") in {"To Do", "In Progress"}:
            active_assigned += 1
    return {
        "id": str(getattr(engineer, "id", "") or ""),
        "slug": str(getattr(engineer, "slug", "") or ""),
        "name": str(getattr(engineer, "name", "") or ""),
        "status": str(getattr(engineer, "status", "") or ""),
        "dismissed_at": _agent_dismissed_at(engineer),
        "hired_by_architect_id": str(
            getattr(engineer, "hired_by_architect_id", "") or ""
        ),
        "current_task_id": current_task_id,
        "current_task": current_task,
        "open_assigned_task_count": open_assigned,
        "active_assigned_task_count": active_assigned,
    }


def _decision_excerpt(text: str, limit: int = 240) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + "…"


def _peer_context_decision_snapshot(decision: dict) -> dict:
    return {
        "id": str(decision.get("id", "") or ""),
        "title": str(decision.get("title", "") or ""),
        "status": str(decision.get("status", "") or ""),
        "rationale_excerpt": _decision_excerpt(
            str(decision.get("rationale", "") or "")
        ),
        "linked_task_ids": list(decision.get("linked_task_ids", []) or []),
        "linked_engineer_ids": list(decision.get("linked_engineer_ids", []) or []),
        "updated_at": int(decision.get("updated_at", 0) or 0),
    }


def _normalize_architect_peer_context(
        state,
        caller_id: str,
        caller_group: str,
        args: dict) -> tuple[dict, str]:
    visible_tasks = _filter_tasks_for_caller(state, "architect", caller_id)
    task_ids = []
    task_snapshots = []
    for task_ident in _dedupe_strings(args.get("context_task_ids", [])):
        task_id = _resolve_task(state, task_ident)
        if not task_id or task_id not in visible_tasks:
            return {}, f"Task not found: {task_ident}"
        task = state.board_tasks.get(task_id)
        if not task or str(getattr(task, "group", "") or "").strip() != caller_group:
            return {}, f"Task not found: {task_ident}"
        task_ids.append(task_id)
        task_snapshots.append(_peer_context_task_snapshot(task))

    engineer_ids = []
    engineer_snapshots = []
    for engineer_ident in _dedupe_strings(args.get("context_engineer_ids", [])):
        engineer_id, engineer_error = _resolve_architect_engineer(
            state,
            caller_id,
            engineer_ident,
        )
        if not engineer_id:
            return {}, engineer_error
        engineer = state.agents.get(engineer_id)
        engineer_ids.append(engineer_id)
        engineer_snapshots.append(_peer_context_engineer_snapshot(state, engineer))

    decision_ids = []
    decision_snapshots = []
    for decision_ident in _dedupe_strings(args.get("context_decision_ids", [])):
        decision_id = str(decision_ident or "").strip()
        decision = state.load_decision(decision_id) if decision_id else None
        if (
            not decision
            or str(decision.get("architect_id", "") or "").strip()
            != str(caller_id or "").strip()
        ):
            return {}, f"Decision not found: {decision_ident}"
        decision_ids.append(decision_id)
        decision_snapshots.append(_peer_context_decision_snapshot(decision))

    return {
        "context_task_ids": task_ids,
        "context_engineer_ids": engineer_ids,
        "context_decision_ids": decision_ids,
        "context_summary": str(args.get("context_summary", "") or "").strip(),
        "context_snapshot": {
            "tasks": task_snapshots,
            "engineers": engineer_snapshots,
            "decisions": decision_snapshots,
        },
    }, ""


def _normalize_agent_user_message_context(
        state,
        caller_kind: str,
        caller_id: str,
        caller_group: str,
        args: dict) -> tuple[dict, str]:
    if caller_kind == "architect":
        return _normalize_architect_peer_context(
            state,
            caller_id,
            caller_group,
            args,
        )
    if caller_kind != "engineer":
        return {}, ""
    visible_tasks = _filter_tasks_for_caller(state, "engineer", caller_id)
    task_ids = []
    task_snapshots = []
    for task_ident in _dedupe_strings(args.get("context_task_ids", [])):
        task_id = _resolve_task(state, task_ident)
        if not task_id or task_id not in visible_tasks:
            return {}, f"Task not found: {task_ident}"
        task = state.board_tasks.get(task_id)
        if not task or str(getattr(task, "group", "") or "").strip() != caller_group:
            return {}, f"Task not found: {task_ident}"
        task_ids.append(task_id)
        task_snapshots.append(_peer_context_task_snapshot(task))
    return {
        "context_task_ids": task_ids,
        "context_engineer_ids": [],
        "context_decision_ids": [],
        "context_summary": str(args.get("context_summary", "") or "").strip(),
        "context_snapshot": {
            "tasks": task_snapshots,
            "engineers": [],
            "decisions": [],
        },
    }, ""


def _validate_architect_peer_message_length(
        message: str,
        context_summary: str = "") -> str:
    size = len(str(message or "").encode("utf-8")) + len(
        str(context_summary or "").encode("utf-8")
    )
    if size > _ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT:
        return (
            "message and context_summary must be at most "
            f"{_ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT} bytes combined"
        )
    return ""


def _peer_message_id_from_idempotency_key(idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(
        ("agent-peer-message\0" + key).encode("utf-8")
    ).hexdigest()
    return "msg-" + digest[:12]


def _agent_peer_message_row_to_entry(row: dict, agent_id: str) -> dict:
    sender_id = str(row.get("sender_id", "") or "").strip()
    recipient_id = str(row.get("recipient_id", "") or "").strip()
    sender_kind = str(row.get("sender_kind", "") or "").strip() or "architect"
    recipient_kind = str(row.get("recipient_kind", "") or "").strip() or "architect"
    direction = "sent" if str(agent_id or "").strip() == sender_id else "received"
    peer_id = recipient_id if direction == "sent" else sender_id
    peer_kind = recipient_kind if direction == "sent" else sender_kind
    created_at = float(row.get("created_at", row.get("timestamp", 0)) or 0)
    delivery_state = str(row.get("delivery_state", "") or "").strip() or "buffered"
    action = (
        "architect_peer_reply"
        if str(row.get("reply_to_id", "") or "").strip()
        else "architect_peer_message"
    )
    context = {
        "task_ids": list(row.get("context_task_ids", []) or []),
        "engineer_ids": list(row.get("context_engineer_ids", []) or []),
        "decision_ids": list(row.get("context_decision_ids", []) or []),
        "summary": str(row.get("context_summary", "") or ""),
        "snapshot": dict(row.get("context_snapshot", {}) or {}),
    }
    return {
        "id": str(row.get("id", "") or ""),
        "thread_id": str(row.get("thread_id", "") or ""),
        "reply_to_id": str(row.get("reply_to_id", "") or ""),
        "direction": direction,
        "action": action,
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "recipient_id": recipient_id,
        "recipient_kind": recipient_kind,
        "peer_id": peer_id,
        "peer_kind": peer_kind,
        "message": str(row.get("message", "") or ""),
        "timestamp": created_at,
        "ack_required": bool(row.get("ack_required", False)),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered_at": float(row.get("delivered_at", 0) or 0),
        "context": context,
        "context_task_ids": context["task_ids"],
        "context_engineer_ids": context["engineer_ids"],
        "context_decision_ids": context["decision_ids"],
        "context_summary": context["summary"],
        "context_snapshot": context["snapshot"],
    }


def _thread_requires_architect_reply(messages: list[dict],
                                     caller_id: str) -> bool:
    latest_outgoing = 0.0
    latest_incoming_ack = 0.0
    caller_id = str(caller_id or "").strip()
    for row in messages:
        ts = float(row.get("created_at", row.get("timestamp", 0)) or 0)
        if str(row.get("sender_id", "") or "").strip() == caller_id:
            latest_outgoing = max(latest_outgoing, ts)
        elif bool(row.get("ack_required", False)):
            latest_incoming_ack = max(latest_incoming_ack, ts)
    return latest_incoming_ack > latest_outgoing

def _architect_peer_inbox_json(
        state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    try:
        limit = int(args.get("limit", _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
                    or _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT
    limit = max(1, min(limit, _ARCHITECT_PEER_INBOX_MAX_LIMIT))
    try:
        since_value = float(args.get("since", 0) or 0)
    except (TypeError, ValueError):
        return "since must be a number", True
    requires_reply, requires_reply_error = _optional_bool_arg(
        args,
        "requires_reply",
        False,
    )
    if requires_reply_error:
        return requires_reply_error, True
    peer_id, peer_error = _resolve_architect_peer_filter(
        state,
        caller_id,
        str(args.get("peer_architect_id", "") or "").strip(),
    )
    if peer_error:
        return peer_error, True
    thread_id = str(args.get("thread_id", "") or "").strip()
    if not getattr(state, "db", None):
        return _compact_json({"type": "architect_peer_inbox", "threads": []}), False
    row_limit = min(max(limit * 20, limit), 1000)
    rows = state.db.load_agent_peer_messages_for_agent(
        caller_id,
        limit=row_limit,
        since=since_value,
        peer_id=peer_id,
        thread_id=thread_id,
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("thread_id", "") or ""), []).append(row)
    threads = []
    for group_thread_id, messages in grouped.items():
        messages.sort(
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            )
        )
        requires = _thread_requires_architect_reply(messages, caller_id)
        if requires_reply and not requires:
            continue
        last = messages[-1]
        entry_for_caller = _agent_peer_message_row_to_entry(last, caller_id)
        peer_architect_id = entry_for_caller["peer_id"]
        peer = state.agents.get(peer_architect_id)
        threads.append({
            "thread_id": group_thread_id,
            "peer_architect_id": peer_architect_id,
            "peer_name": str(getattr(peer, "name", "") or "").strip()
            or peer_architect_id,
            "last_message_at": float(last.get("created_at", 0) or 0),
            "requires_reply": requires,
            "messages": [
                _agent_peer_message_row_to_entry(row, caller_id)
                for row in messages
            ],
        })
    threads.sort(
        key=lambda item: (
            float(item.get("last_message_at", 0) or 0),
            str(item.get("thread_id", "") or ""),
        ),
        reverse=True,
    )
    return _compact_json({
        "type": "architect_peer_inbox",
        "threads": threads[:limit],
    }), False


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


def _optional_bool_arg(args: dict, key: str, default: bool = False
                       ) -> tuple[bool, str]:
    if key not in args or args.get(key) is None:
        return bool(default), ""
    value = args.get(key)
    if isinstance(value, bool):
        return value, ""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True, ""
        if lowered in {"false", "0", "no", "off", ""}:
            return False, ""
    return bool(default), f"{key} must be a boolean"


def _sanitize_mcp_worker_provider_override(
    state,
    group: str,
    engineer_id: str,
    provider: str,
) -> str:
    provider = str(provider or "").strip()
    if not provider:
        return ""
    settings = state.get_engineer_settings(group)
    if getattr(settings, "engineer_can_override_worker_provider", True):
        return provider
    log.warning(
        "Engineer %s attempted worker provider override '%s' in group %s "
        "while provider overrides are disabled; falling back to group default",
        engineer_id,
        provider,
        group,
    )
    return ""


_TASK_ID_REFERENCE_RE = re.compile(
    r"\b[A-Z][A-Z0-9_]*:[1-9][0-9]*(?::[1-9][0-9]*)?\b"
)


def _deliverable_awareness_for_referenced_tasks(state, message_text: str) -> str:
    """Return awareness blocks for any deliverable tasks referenced in text.

    Scans ``message_text`` for canonical task IDs (e.g. ``TORQUE:241``),
    resolves them via the board (including legacy alias lookups) and
    concatenates an awareness block for each task that carries a
    deliverable contract. Returns ``""`` when nothing matches.
    """
    if not message_text:
        return ""
    seen: set[str] = set()
    blocks: list[str] = []
    aliases = getattr(state, "task_id_aliases", {}) or {}
    for raw in _TASK_ID_REFERENCE_RE.findall(message_text):
        tid = str(aliases.get(raw, raw) or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        task = state.board_tasks.get(tid)
        if not task:
            continue
        block = build_engineer_deliverable_awareness(task)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _deliver_architect_engineer_message(state, sender, recipient, *,
                                        action: str, message: str,
                                        reply_to_id: str = "",
                                        thread_id: str = "",
                                        ack_required: bool = False) -> dict:
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
    if (
        str(getattr(sender, "kind", "") or "").strip() == "engineer"
        and str(getattr(recipient, "kind", "") or "").strip() == "architect"
    ):
        shared["ack_required"] = bool(ack_required)
    sender_entry = dict(shared)
    sender_entry.update({
        "peer_id": recipient.id,
        "peer_kind": str(getattr(recipient, "kind", "") or "").strip(),
        "direction": "sent",
    })
    recipient_entry = dict(shared)
    if (
        str(getattr(sender, "kind", "") or "").strip() == "architect"
        and str(getattr(recipient, "kind", "") or "").strip() == "engineer"
    ):
        body = message_text
        awareness = _deliverable_awareness_for_referenced_tasks(
            state, message_text
        )
        if awareness:
            body = f"{message_text}\n\n{awareness}"
        recipient_entry["message"] = prepend_agent_identity_anchor(
            body,
            recipient,
        )
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
    state.history_record_message(
        sender.id,
        action,
        message_text,
        mark_progress=False,
    )
    state.history_record_message(
        recipient.id,
        action,
        message_text,
        mark_progress=False,
    )
    if str(getattr(recipient, "kind", "") or "").strip() == "engineer":
        recipient.pending_engineer_message = True
    if str(getattr(sender, "kind", "") or "").strip() == "engineer":
        sender.pending_engineer_message = False
    state._emit_agent(sender)
    state._emit_agent(recipient)
    return shared


def _load_existing_peer_message_for_idempotency(state, message_id: str) -> dict | None:
    message_id = str(message_id or "").strip()
    if not message_id or not getattr(state, "db", None):
        return None
    return state.db.load_agent_peer_message(message_id)


def _agent_user_direct_message_id_from_idempotency_key(
        idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(
        ("agent-user-message\0" + key).encode("utf-8")
    ).hexdigest()
    return "msg-" + digest[:12]


def _direct_message_agent_kind(cell) -> str:
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind in {"architect", "engineer", "worker"}:
        return kind
    return "worker"


def _load_existing_agent_user_direct_message_for_idempotency(
        state,
        message_id: str) -> dict | None:
    message_id = str(message_id or "").strip()
    db = getattr(state, "db", None)
    if not message_id or not db:
        return None
    loader = getattr(db, "load_direct_message", None)
    if callable(loader):
        return loader(message_id)
    return db.load_agent_peer_message(message_id)


def _agent_user_direct_message_conflicts_with_existing(
        existing: dict,
        sender,
        *,
        message: str,
        reply_to_id: str) -> bool:
    if not existing or not sender:
        return False
    if str(existing.get("sender_id", "") or "").strip() != str(
            getattr(sender, "id", "") or "").strip():
        return True
    if str(existing.get("sender_kind", "") or "").strip() != (
            _direct_message_agent_kind(sender)):
        return True
    if str(existing.get("recipient_kind", "") or "").strip() != "user":
        return True
    if str(existing.get("recipient_id", "") or "").strip() != "user":
        return True
    if str(existing.get("message", "") or "") != str(message or ""):
        return True
    if str(existing.get("reply_to_id", "") or "").strip() != str(
            reply_to_id or "").strip():
        return True
    return False


def _agent_user_direct_message_reply_thread_id(
        state,
        reply_to_id: str,
        sender_id: str) -> str:
    reply_to_id = str(reply_to_id or "").strip()
    sender_id = str(sender_id or "").strip()
    if not reply_to_id or not sender_id:
        return ""
    db = getattr(state, "db", None)
    loader = getattr(db, "load_direct_message", None) if db else None
    parent = loader(reply_to_id) if callable(loader) else None
    if not parent:
        return ""
    kinds = {
        str(parent.get("sender_kind", "") or "").strip(),
        str(parent.get("recipient_kind", "") or "").strip(),
    }
    if "user" not in kinds:
        return ""
    if sender_id not in {
        str(parent.get("sender_id", "") or "").strip(),
        str(parent.get("recipient_id", "") or "").strip(),
    }:
        return ""
    return str(parent.get("thread_id", "") or "").strip()


def _direct_user_message_response(row: dict, *,
                                  deduped: bool = False) -> dict:
    row = row or {}
    delivery_state = str(row.get("delivery_state", "") or "").strip() \
        or "delivered"
    return {
        "type": "ok",
        "message_id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "agent_id": str(row.get("sender_id", "") or "").strip(),
        "sender_id": str(row.get("sender_id", "") or "").strip(),
        "sender_kind": str(row.get("sender_kind", "") or "").strip(),
        "recipient_id": str(row.get("recipient_id", "") or "").strip(),
        "recipient_kind": str(row.get("recipient_kind", "") or "").strip(),
        "message_type": str(row.get("message_type", "message") or "message"),
        "blocking": bool(row.get("blocking", False)),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivery": {
            "state": delivery_state,
            "reason": str(row.get("delivery_reason", "") or ""),
        },
        "delivered": delivery_state == "delivered",
        "read_at": float(row.get("read_at", 0) or 0),
        "deduped": bool(deduped),
    }


def _notify_agent_user_direct_message(state, row: dict) -> None:
    """Best-effort notification hook for agent→user direct messages.

    Slice 3 owns the MCP write path; Slice 5 wires a concrete
    NotificationManager method.  Keep this hook optional so the durable
    message write never depends on notification delivery.
    """
    manager = getattr(state, "notification_manager", None)
    callback = getattr(manager, "on_direct_user_message", None)
    if not callable(callback):
        callback = getattr(state, "on_direct_user_message", None)
    if not callable(callback):
        return
    try:
        callback(row)
    except Exception:
        log.exception("Failed to notify for direct user message")


def save_agent_user_direct_message_from_mcp(
        state,
        sender,
        *,
        message: str,
        thread_id: str = "",
        reply_to_id: str = "",
        context: dict | None = None,
        idempotency_key: str = "",
        notify: bool = True) -> tuple[dict, bool]:
    """Persist one agent→user direct message from an MCP tool call.

    Returns ``(row, created)``.  Idempotency-derived duplicates return the
    existing row and do not re-notify.
    """
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    if not sender or getattr(sender, "cell_type", "") != "agent":
        raise ValueError("agent not found")
    if not getattr(state, "db", None):
        raise ValueError("Direct message store is unavailable")

    reply_to = str(reply_to_id or "").strip()
    message_id = _agent_user_direct_message_id_from_idempotency_key(
        idempotency_key
    )
    created = False
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
        created = True
    existing = _load_existing_agent_user_direct_message_for_idempotency(
        state,
        message_id,
    )
    if existing:
        if _agent_user_direct_message_conflicts_with_existing(
                existing,
                sender,
                message=message_text,
                reply_to_id=reply_to):
            raise ValueError(
                "idempotency key was reused for a different message_user call"
            )
        append_direct = getattr(state, "append_direct_message_to_caches", None)
        if callable(append_direct):
            append_direct(existing)
        return existing, False
    if not created:
        created = True

    context = dict(context or {})
    requested_thread_id = str(thread_id or "").strip()
    if not requested_thread_id:
        requested_thread_id = _agent_user_direct_message_reply_thread_id(
            state,
            reply_to,
            str(getattr(sender, "id", "") or "").strip(),
        )
    now = time.time()
    row = {
        "id": message_id,
        "thread_id": requested_thread_id,
        "reply_to_id": reply_to,
        "group_name": str(getattr(sender, "group", "") or "").strip(),
        "sender_id": sender.id,
        "sender_kind": _direct_message_agent_kind(sender),
        "sender_name": str(getattr(sender, "name", "") or "").strip(),
        "recipient_id": "user",
        "recipient_kind": "user",
        "recipient_name": "User",
        "message": message_text,
        "message_type": "message",
        "created_at": now,
        "ack_required": False,
        "blocking": False,
        "source_task_id": "",
        "context_task_ids": list(context.get("context_task_ids", []) or []),
        "context_engineer_ids": list(context.get("context_engineer_ids", []) or []),
        "context_decision_ids": list(context.get("context_decision_ids", []) or []),
        "context_summary": str(context.get("context_summary", "") or ""),
        "context_snapshot": dict(context.get("context_snapshot", {}) or {}),
        "delivery_state": "delivered",
        "delivery_reason": "",
        "delivered_at": now,
        "read_at": 0,
    }
    saved = state.save_direct_message(row) if getattr(state, "db", None) else None
    if not saved:
        raise ValueError("failed to save direct message")
    if notify:
        _notify_agent_user_direct_message(state, saved)
    return saved, created


def _save_architect_peer_message(state, sender, recipient, *,
                                 action: str,
                                 message: str,
                                 reply_to_id: str = "",
                                 thread_id: str = "",
                                 ack_required: bool = False,
                                 context: dict | None = None,
                                 idempotency_key: str = "") -> tuple[dict, bool]:
    """Persist a canonical Architect peer message and project UI caches.

    Returns ``(row, created)``.  When an idempotency-derived message id already
    exists the stored row is returned and no audit side effects are repeated.
    """
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    message_id = _peer_message_id_from_idempotency_key(idempotency_key)
    created = False
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
        created = True
    existing = _load_existing_peer_message_for_idempotency(state, message_id)
    if existing:
        state.append_peer_message_to_caches(existing)
        return existing, False
    if not created:
        created = True
    conversation_id = str(thread_id or "").strip() or message_id
    context = dict(context or {})
    row = {
        "id": message_id,
        "thread_id": conversation_id,
        "reply_to_id": str(reply_to_id or "").strip(),
        "group_name": str(getattr(sender, "group", "") or "").strip(),
        "sender_id": sender.id,
        "sender_kind": "architect",
        "recipient_id": recipient.id,
        "recipient_kind": "architect",
        "message": message_text,
        "created_at": time.time(),
        "ack_required": bool(ack_required),
        "context_task_ids": list(context.get("context_task_ids", []) or []),
        "context_engineer_ids": list(context.get("context_engineer_ids", []) or []),
        "context_decision_ids": list(context.get("context_decision_ids", []) or []),
        "context_summary": str(context.get("context_summary", "") or ""),
        "context_snapshot": dict(context.get("context_snapshot", {}) or {}),
        "delivery_state": "buffered",
        "delivery_reason": "",
    }
    saved = state.save_peer_message(row) if getattr(state, "db", None) else None
    if not saved:
        raise ValueError("failed to save peer message")
    state.history_record_message(
        sender.id,
        action,
        message_text,
        mark_progress=False,
    )
    state.history_record_message(
        recipient.id,
        action,
        message_text,
        mark_progress=False,
    )
    return saved, created


async def _inject_architect_peer_message(handle_command, state, sender,
                                         recipient, row: dict,
                                         message: str) -> dict:
    """Inject an Architect peer message and persist delivery state."""
    message_id = str((row or {}).get("id", "") or "").strip()
    if _agent_dismissed_at(recipient):
        updated = state.update_peer_message_delivery(
            message_id,
            "buffered",
            reason="recipient_dismissed",
        )
        return {
            "state": str((updated or row).get("delivery_state", "buffered") or "buffered"),
            "reason": str((updated or row).get("delivery_reason", "recipient_dismissed") or ""),
        }
    if not recipient or not handle_command:
        updated = state.update_peer_message_delivery(
            message_id,
            "buffered",
            reason="no_session",
        )
        return {
            "state": str((updated or row).get("delivery_state", "buffered") or "buffered"),
            "reason": str((updated or row).get("delivery_reason", "no_session") or ""),
        }
    try:
        result = await handle_command({
            "cmd": "inject_mcp_message",
            "agent_id": getattr(recipient, "id", ""),
            "message": message,
            "sender_name": str(getattr(sender, "name", "") or "").strip(),
            "sender_kind": "architect",
            "message_id": message_id,
            "ack_required": bool((row or {}).get("ack_required", False)),
        })
    except Exception:
        log.exception(
            "Failed to inject Architect peer message into %s",
            getattr(recipient, "id", ""),
        )
        updated = state.update_peer_message_delivery(
            message_id,
            "failed",
            reason="inject_failed",
        )
        return {
            "state": str((updated or row).get("delivery_state", "failed") or "failed"),
            "reason": str((updated or row).get("delivery_reason", "inject_failed") or ""),
        }

    if isinstance(result, dict) and result.get("type") == "error":
        updated = state.update_peer_message_delivery(
            message_id,
            "failed",
            reason=str(result.get("message", "") or "inject_failed"),
        )
    elif bool(result and result.get("delivered")):
        updated = state.update_peer_message_delivery(message_id, "delivered")
    else:
        updated = state.update_peer_message_delivery(
            message_id,
            "buffered",
            reason=str((result or {}).get("reason", "") or "no_session"),
        )
    current = updated or row
    return {
        "state": str(current.get("delivery_state", "buffered") or "buffered"),
        "reason": str(current.get("delivery_reason", "") or ""),
    }


async def _inject_mcp_message(handle_command, sender, recipient,
                              delivered: dict, message: str) -> None:
    """Ask the server to type the message into the recipient's terminal."""
    if not recipient or not handle_command:
        return
    try:
        payload = {
            "cmd": "inject_mcp_message",
            "agent_id": getattr(recipient, "id", ""),
            "message": message,
            "sender_name": str(getattr(sender, "name", "") or "").strip(),
            "sender_kind": str(getattr(sender, "kind", "") or "").strip(),
            "message_id": str(delivered.get("id", "") or ""),
        }
        if "ack_required" in delivered:
            payload["ack_required"] = bool(delivered.get("ack_required", False))
        result = await handle_command(payload)
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
    for cell in state.iter_active_agents():
        if state.agent_is_visible_to_engineer(
                caller_id,
                str(getattr(cell, "id", "") or "").strip()):
            visible.add(cell.id)
    return visible


def _filter_agents_for_caller(state, caller_kind: str,
                              caller_id: str) -> dict[str, object]:
    visible_agent_ids = _visible_agent_ids_for_caller(
        state, caller_kind, caller_id
    )
    filtered = {}
    for cell in state.iter_active_agents():
        if cell.id in visible_agent_ids:
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
            "TORQUE_ENGINEER_ID is required"
            if caller_kind == "engineer"
            else "TORQUE_ARCHITECT_ID is required"
            if caller_kind == "architect"
            else "caller id is required"
        )
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": missing_message,
        }), True
    cell = state.agents.get(caller_id)
    if caller_kind == "architect":
        valid = _is_architect_cell(cell, state)
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
            and not _agent_is_tombstoned(state, cell)
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
            engineer_agent_id=str(getattr(caller_cell, "id", "") or ""),
        )
    view_state.agent_is_visible_to_engineer = (
        lambda _engineer_id, agent_id: str(agent_id or "").strip()
        in visible_agent_ids
    )
    view_state.engineer_restricts_to_created_agents = lambda _group: False
    if caller_kind == "engineer":
        real_journal_read = state.journal_read
        caller_author_id = str(caller_id or "").strip()

        def _engineer_scoped_journal_read(
            group,
            limit=20,
            entry_type="",
            author_cell_id="",
        ):
            return real_journal_read(
                group,
                limit,
                entry_type,
                author_cell_id=(
                    str(author_cell_id or "").strip()
                    or caller_author_id
                ),
            )

        view_state.journal_read = _engineer_scoped_journal_read
    return view_state


def _agent_visible_to_engineer(state, engineer_cell, agent_id: str) -> bool:
    if not engineer_cell:
        return False
    return state.agent_is_visible_to_engineer(engineer_cell.id, agent_id)


def _task_agent_payload_for_engineer(state, engineer_cell, agent_id: str) -> dict:
    """Return safe agent details for task views without leaking hidden agents."""
    if not agent_id:
        return {}
    agent = state.agents.get(agent_id)
    if not agent or agent.cell_type != "agent":
        if engineer_cell and state.engineer_restricts_to_created_agents(
                engineer_cell.group):
            return {"agent_hidden": True}
        return {}
    if _agent_visible_to_engineer(state, engineer_cell, agent_id):
        return {
            "agent_name": agent.slug or agent.name,
            "agent_status": agent.status,
        }
    if state.engineer_restricts_to_created_agents(engineer_cell.group):
        return {"agent_hidden": True}
    return {}


def _stream_payload_for_engineer(state, engineer_cell, stream: dict) -> dict:
    """Return a stream payload with hidden agent identity scrubbed."""
    payload = dict(stream or {})
    agent_id = str(payload.get("agent_id", "") or "").strip()
    if not agent_id:
        return payload
    if _agent_visible_to_engineer(state, engineer_cell, agent_id):
        return payload
    payload["agent_id"] = ""
    payload["agent_name"] = ""
    payload["agent_slug"] = ""
    if state.engineer_restricts_to_created_agents(engineer_cell.group):
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
    last_activity_ts = _parse_health_timestamp(
        fresh.get("last_progress_at") or fresh.get("last_activity_at")
    )
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
            (details or {}).get("last_progress_at")
            or (details or {}).get("last_activity_at")
        )
    if last_activity_ts is None and agent and getattr(agent, "last_progress_at", 0):
        last_activity_ts = float(getattr(agent, "last_progress_at", 0) or 0)
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


def _engineer_streams(state, engineer_cell, group: str, *,
                    include_merged: bool = True,
                    include_orphaned: bool = False,
                    visibility_limit: int = 10,
                    state_filter: str = "",
                    branch_filter: str = "",
                    repo_root_filter: str = "") -> list[dict]:
    streams = [
        _stream_payload_for_engineer(state, engineer_cell, stream)
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


def _worktree_result_pr_url(result: dict | None) -> str:
    result = result or {}
    pr = result.get("pr")
    if not isinstance(pr, dict):
        pr = {}
    return str(
        result.get("pr_url")
        or result.get("url")
        or pr.get("url")
        or ""
    ).strip()


def _worktree_result_phase(result: dict | None) -> str:
    result = result or {}
    phase = str(result.get("phase") or "").strip()
    if phase:
        return phase
    pr = result.get("pr")
    if isinstance(pr, dict):
        return str(pr.get("phase") or "").strip()
    return ""


def _worktree_result_error(result: dict | None, fallback: str = "") -> str:
    result = result or {}
    return str(
        result.get("error")
        or result.get("message")
        or fallback
        or "Merge failed"
    ).strip()


def _format_worktree_pr_error(result: dict | None,
                              fallback: str = "Merge failed"
                              ) -> tuple[str, bool | None]:
    """Return an MCP-facing error string plus optional cacheability.

    ``False`` cacheability marks transient PR transport/API phases as
    retryable through the MCP idempotency layer; ``None`` preserves the
    normal cache policy for deterministic errors.
    """
    result = result or {}
    error = _worktree_result_error(result, fallback)
    phase = _worktree_result_phase(result)
    pr_url = _worktree_result_pr_url(result)
    retryable = (
        is_mcp_pr_phase_retryable(phase, error)
        if is_mcp_pr_phase(phase)
        else False
    )
    context = []
    if phase:
        context.append(f"phase={phase}")
    if pr_url:
        context.append(f"pr_url={pr_url}")
    if phase and is_mcp_pr_phase(phase):
        context.append(f"retryable={'true' if retryable else 'false'}")
    if context:
        error = f"{error}\n\nPR context: " + ", ".join(context)
    return error, False if retryable else None


def _worktree_merge_success_payload(result: dict | None, cell) -> dict:
    result = result or {}
    cleanup = result.get("cleanup", {})
    if not isinstance(cleanup, dict):
        cleanup = {}
    mode = str(result.get("mode") or "direct").strip() or "direct"
    pending = bool(result.get("pending"))
    pr_url = _worktree_result_pr_url(result)
    payload = {
        "type": "ok",
        "message": str(result.get("message") or "").strip(),
        "mode": mode,
        "pr_url": pr_url,
        "pending": pending,
        "sha": str(result.get("sha") or "").strip(),
        "cleanup": cleanup,
    }
    if not payload["message"]:
        if mode == "pull_request":
            if pending:
                payload["message"] = (
                    "Pull request is open with auto-merge pending."
                )
            else:
                payload["message"] = (
                    f"Squash-merged {cell.worktree_branch} into "
                    f"{cell.worktree_base_branch}"
                )
        else:
            payload["message"] = (
                f"Merged {cell.worktree_branch} into "
                f"{cell.worktree_base_branch}"
            )
    if "merged" in result:
        payload["merged"] = bool(result.get("merged"))
    elif mode == "pull_request":
        payload["merged"] = bool(payload["sha"]) and not pending
    if "url" in result:
        payload["url"] = str(result.get("url") or "").strip()
    if isinstance(result.get("pr"), dict):
        payload["pr"] = result["pr"]
    if "force_direct" in result:
        payload["force_direct"] = bool(result.get("force_direct"))
    if result.get("warning"):
        payload["warning"] = str(result.get("warning") or "")
    if isinstance(result.get("workflow_breach"), dict):
        payload["workflow_breach"] = result["workflow_breach"]
    if isinstance(result.get("stale_base"), dict):
        payload["stale_base"] = result["stale_base"]
    if result.get("stale_base_warning"):
        payload["stale_base_warning"] = str(
            result.get("stale_base_warning") or ""
        )
    return payload


async def dispatch_scoped_tool(name, args, handle_command, state, *,
                               tool_prefix: str, caller_kind: str,
                               caller_id: str,
                               idempotency_key: str = ""):
    """Execute a scoped tool call.

    Returns ``(text, is_error)`` or ``(text, is_error, cacheable)`` when the
    MCP idempotency layer should not cache a recoverable refusal.
    """

    _engineer_cell, _engineer_group, caller_kind, auth_error, auth_structured = authorize_caller(
        state, caller_kind=caller_kind, caller_id=caller_id
    )
    if auth_error:
        return auth_error, auth_structured

    persist_missing = getattr(state, "persist_missing_aliased_tasks", None)
    if callable(persist_missing):
        persist_missing()

    view_state = build_scoped_state_view(
        state, caller_kind=caller_kind, caller_id=caller_id,
        caller_cell=_engineer_cell, caller_group=_engineer_group,
    )
    real_state = state
    state = view_state
    tool_name = normalize_tool_name(name, tool_prefix)
    _raw_handle_command = handle_command
    if (
            caller_kind == "architect"
            and _agent_dismissed_at(_engineer_cell)
            and tool_name not in _ARCHITECT_READ_TOOL_NAMES):
        return _architect_dismissed_error(caller_id), True

    async def handle_command(payload):
        command_payload = dict(payload or {})
        if idempotency_key and "idempotency_key" not in command_payload:
            command_payload["idempotency_key"] = derive_idempotency_key(
                idempotency_key,
                command_payload,
            )
        return await _raw_handle_command(command_payload)

    # -- Read tools ---------------------------------------------------------

    if tool_name == "events_recent" and caller_kind == "architect":
        return _architect_events_recent_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "peer_list" and caller_kind == "architect":
        return _architect_peer_list_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "peer_inbox" and caller_kind == "architect":
        return _architect_peer_inbox_json(real_state, caller_id, args)

    if tool_name == "mcp_calls":
        target_agent = str(args.get("agent_id", "") or args.get("cell_id", "") or "").strip()
        if target_agent:
            if caller_kind == "architect":
                resolved_agent_id = _resolve_agent(real_state, target_agent)
                target_cell = real_state.agents.get(resolved_agent_id or "")
                if (
                    not resolved_agent_id
                    or not target_cell
                    or str(getattr(target_cell, "group", "") or "").strip() != _engineer_group
                ):
                    resolved_agent_id, resolve_error = None, f"Agent not found: {target_agent}"
                else:
                    resolve_error = ""
            else:
                resolved_agent_id, resolve_error = _resolve_visible_agent(
                    real_state,
                    caller_kind,
                    caller_id,
                    target_agent,
                )
            if not resolved_agent_id:
                return resolve_error, True
            target_agent = resolved_agent_id
        cmd_name = (
            "architect_mcp_calls"
            if caller_kind == "architect"
            else "engineer_mcp_calls"
        )
        payload = {
            "cmd": cmd_name,
            "caller_id": caller_id,
            "agent_id": target_agent,
            "cell_id": target_agent,
            "tool_name_pattern": (
                args.get("tool_name_pattern")
                or args.get("tool_filter")
                or "mcp__torque__%"
            ),
            "hook_event_name": args.get("hook_event_name", ""),
            "since": args.get("since", None),
            "until": args.get("until", None),
            "limit": args.get("limit", 50),
        }
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result or {"type": "mcp_calls", "calls": []}), False

    if tool_name == "engineer_pending_question" and caller_kind == "architect":
        return _architect_engineer_pending_question_json(
            real_state,
            caller_id,
            args,
        )

    if tool_name == "deploy_state" and caller_kind == "architect":
        return _compact_json(
            architect_deploy_state_payload(real_state, _engineer_group)
        ), False

    if tool_name == "get_architect_settings" and caller_kind == "architect":
        return json.dumps({
            "type": "architect_settings",
            "group": _engineer_group,
            "settings": asdict(
                real_state.get_architect_settings(_engineer_group)
            ),
        }), False

    if tool_name == "task_chain" and caller_kind == "architect":
        return _architect_task_chain_json(
            state,
            caller_id,
            args.get("task", ""),
        )

    if tool_name == "board_summary":
        summary_streams = _engineer_streams(
            state,
            _engineer_cell,
            _engineer_group,
            include_merged=False,
            visibility_limit=5,
        )
        tasks = [
            t for t in state.board_tasks.values()
            if t.group == _engineer_group
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
        pending_message_followups = [
            t for t in visible_tasks
            if task_is_engineer_message_followup(t)
            and not board_task_is_closed(t)
        ]
        actionable_visible_tasks = [
            t for t in visible_tasks
            if not task_is_engineer_message_followup(t)
        ]
        summary_state = copy.copy(state)
        summary_state.board_tasks = {
            task.id: task for task in actionable_visible_tasks
        }

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
        specialization_filter = set()
        specialization_filter_engineer_id = ""
        if include_created_by:
            spec_ident = str(
                args.get("specialization_engineer_id", "") or ""
            ).strip()
            if spec_ident:
                resolved_engineer_id, _engineer_error = _resolve_visible_agent(
                    real_state, caller_kind, caller_id, spec_ident
                )
                if resolved_engineer_id:
                    specialization_filter_engineer_id = resolved_engineer_id
                    spec_cell = real_state.agents.get(resolved_engineer_id)
                    specialization_filter = {
                        str(s or "").strip()
                        for s in (
                            getattr(
                                spec_cell, "engineer_specializations", []
                            ) or []
                        )
                        if str(s or "").strip()
                    }
        for task in actionable_visible_tasks:
            created_by = _task_created_by_classifier(task) if include_created_by else ""
            if include_created_by:
                task_spec = str(
                    getattr(task, "suggested_specialization", "") or ""
                ).strip()
                if (
                    not specialization_filter_engineer_id
                    or (task_spec and task_spec in specialization_filter)
                ):
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

            if "torque:human" in labels and not board_task_is_closed(task):
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

        gs = state.get_group_settings(_engineer_group)
        engineer_id = gs.engineer_agent_id or (
            _engineer_cell.id if _engineer_cell and _engineer_cell.group == _engineer_group
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
            c for c in state.iter_active_agents()
            if c.cell_type == "agent"
            and c.group == _engineer_group
            and c.id != engineer_id
            and _agent_visible_to_engineer(state, _engineer_cell, c.id)
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
            "group": _engineer_group,
            "tasks_total": len(actionable_visible_tasks),
            "archived_total": len(archived_tasks),
            "pending_message_followups": len(pending_message_followups),
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
        board_sync_summary = _board_sync_summary_payload(actionable_visible_tasks)
        if board_sync_summary:
            summary["board_sync"] = board_sync_summary
        if include_created_by:
            summary["peer_messages"] = _architect_peer_message_summary(
                real_state,
                caller_id,
            )
        hints = compute_engineer_hints(
            summary_state,
            _engineer_group,
            engineer_id=_engineer_cell.id if _engineer_cell else "",
        )
        summary["hints"] = {
            "count": len(hints),
            "items": hints[:10],
            "truncated": len(hints) > 10,
        }
        if caller_kind == "engineer":
            summary["dispatch_shapes"] = _engineer_dispatch_shape_summary(
                real_state,
                _engineer_cell.id if _engineer_cell else "",
                group=_engineer_group,
                window=20,
            )
        if include_created_by:
            return _architect_board_summary_json(
                summary,
                architect_task_items,
            ), False
        return _compact_json(summary), False

    if tool_name == "session_map":
        return json.dumps(
            build_engineer_session_map(
                state,
                _engineer_group,
                engineer_cell=_engineer_cell,
            )
        ), False

    if tool_name == "streams_list":
        streams = _engineer_streams(
            state,
            _engineer_cell,
            _engineer_group,
            state_filter=str(args.get("state", "") or "").strip(),
            branch_filter=str(args.get("branch", "") or "").strip(),
            repo_root_filter=str(args.get("repo_root", "") or "").strip(),
            include_orphaned=bool(args.get("include_orphaned", False)),
        )
        return json.dumps({
            "group": _engineer_group,
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
            if not task or task.group != _engineer_group:
                return "Task not found", True
        streams = _engineer_streams(
            state,
            _engineer_cell,
            _engineer_group,
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
            # Always scope to engineer's group
            if t.group != _engineer_group:
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
                agent_payload = _task_agent_payload_for_engineer(
                    state, _engineer_cell, t.agent_id
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
            _attach_task_board_sync_inline_state(item, t)
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
        if not task or task.group != _engineer_group:
            return "Task not found", True
        d = serialize_task_for_mcp(task, tasks_by_id=state.board_tasks)
        d.update(_task_health_payload_for_response(state, task))
        d["title"] = task.task
        d["action"] = task.action_name
        board_sync = _task_board_sync_inline_state(task)
        if board_sync:
            d["board_sync"] = board_sync
        awareness_block = build_engineer_deliverable_awareness(task)
        if awareness_block:
            d["deliverable_awareness"] = awareness_block
        if caller_kind == "architect":
            d["created_by"] = _task_created_by_classifier(task)
        if task.agent_id and not _agent_visible_to_engineer(
                state, _engineer_cell, task.agent_id):
            d["agent_id"] = ""
            if state.engineer_restricts_to_created_agents(_engineer_group):
                d["agent_hidden"] = True
        # Include recent messages (last 10 only)
        if task.messages:
            d["messages"] = task.messages[-10:]
        # Enrich with agent info
        if task.agent_id:
            d.update(
                _task_agent_payload_for_engineer(
                    state, _engineer_cell, task.agent_id
                )
            )
        # Auto-include pipeline chain for pipeline tasks
        if task.pipeline_root_id or task.parent_task_id:
            chain = state.board_get_chain(tid)
            d["pipeline_chain"] = []
            for ct in chain:
                if ct.group != _engineer_group:
                    continue
                agent_slug = ""
                agent_hidden = False
                if ct.agent_id:
                    agent_payload = _task_agent_payload_for_engineer(
                        state, _engineer_cell, ct.agent_id
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
                _attach_task_board_sync_inline_state(item, ct)
                d["pipeline_chain"].append(item)
        return json.dumps(d), False

    if tool_name == "task_list" and caller_kind == "architect":
        label_filter, label_error = _normalize_architect_task_list_label_filter(
            args.get("label_filter", "")
        )
        if label_error:
            return label_error, True
        limit, limit_error = _normalize_architect_task_list_limit(
            args.get("limit", None)
        )
        if limit_error:
            return limit_error, True
        archived, archived_error = _optional_bool_arg(args, "archived", False)
        if archived_error:
            return archived_error, True
        include_engineer_messages, include_messages_error = _optional_bool_arg(
            args,
            "include_engineer_messages",
            False,
        )
        if include_messages_error:
            return include_messages_error, True

        lane_filter = str(args.get("lane_filter", "") or "").strip()
        assigned_engineer_filter = str(
            args.get("assigned_engineer_id_filter", "") or ""
        ).strip()
        creator_filter = str(args.get("creator_filter", "") or "").strip()
        creator_filter_error = _validate_architect_task_creator_filter(
            creator_filter,
        )
        if creator_filter_error:
            return creator_filter_error, True

        task_items = []
        for task in state.board_tasks.values():
            if str(getattr(task, "group", "") or "").strip() != _engineer_group:
                continue
            task_archived = bool(
                str(getattr(task, "archived_at", "") or "").strip()
            )
            if task_archived != archived:
                continue
            task_labels = set(getattr(task, "labels", []) or [])
            if (
                not include_engineer_messages
                and task_is_engineer_message_followup(task)
            ):
                continue
            if label_filter and not all(label in task_labels for label in label_filter):
                continue
            if lane_filter and str(getattr(task, "lane", "") or "") != lane_filter:
                continue
            if (
                assigned_engineer_filter
                and _effective_assigned_engineer_id(task) != assigned_engineer_filter
            ):
                continue
            creator_matches, creator_error = _architect_task_creator_filter_matches(
                task,
                creator_filter,
            )
            if creator_error:
                return creator_error, True
            if not creator_matches:
                continue
            task_items.append(_architect_board_summary_task_item(
                task,
                created_by=_task_created_by_classifier(task),
            ))

        task_items.sort(key=lambda item: _architect_task_list_sort_key(state, item))
        total = len(task_items)
        return _compact_json({
            "type": "task_list",
            "tasks": task_items[:limit],
            "total": total,
            "truncated": total > limit,
        }), False

    if tool_name == "engineer_list" and caller_kind == "architect":
        include_tombstoned = bool(args.get("include_tombstoned", False))
        visible_task_ids = set(
            _filter_tasks_for_caller(real_state, caller_kind, caller_id)
        )
        engineers = []
        for cell, relation in _architect_visible_engineers(
            real_state, caller_id, include_tombstoned=include_tombstoned
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
                "deleted_at": float(getattr(cell, "deleted_at", 0) or 0),
                "permanent_delete_after": float(
                    getattr(cell, "permanent_delete_after", 0) or 0
                ),
                "group": cell.group,
                "relation": relation,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "activity": cell.activity,
                "activity_detail": cell.activity_detail,
                "specializations": list(
                    getattr(cell, "engineer_specializations", []) or []
                ),
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
        for c in state.iter_active_agents():
            if c.cell_type != "agent":
                continue
            if c.group != _engineer_group:
                continue
            if not _agent_visible_to_engineer(state, _engineer_cell, c.id):
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
        if current_task and current_task.group != _engineer_group:
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
                if t.group != _engineer_group:
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
            if t.group != _engineer_group:
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
        if current_task and current_task.group == _engineer_group:
            d["current_task_id"] = current_task.id

        return json.dumps(d), False

    if tool_name == "actions_list":
        result = await handle_command({
            "cmd": "list_actions",
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "action_show":
        result = await handle_command({
            "cmd": "get_action",
            "name": args.get("name", ""),
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "specializations_list":
        result = await handle_command({
            "cmd": "list_specializations",
            "group": args.get("group", "") or _engineer_group,
            "scope": str(args.get("scope", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "specialization_show":
        result = await handle_command({
            "cmd": "get_specialization",
            "name": args.get("name", ""),
            "group": args.get("group", "") or _engineer_group,
            "scope": str(args.get("scope", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "specialization_save":
        name = str(args.get("name", "") or "").strip()
        if not name:
            return "name is required", True
        payload = args.get("data")
        if payload is None:
            payload = args.get("specialization", {})
        if not isinstance(payload, dict):
            return "data must be an object", True
        result = await handle_command({
            "cmd": "save_specialization",
            "name": name,
            "data": payload,
            "scope": str(args.get("scope", "project") or "project"),
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "specialization_delete":
        name = str(args.get("name", "") or "").strip()
        if not name:
            return "name is required", True
        result = await handle_command({
            "cmd": "delete_specialization",
            "name": name,
            "scope": str(args.get("scope", "") or "").strip(),
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

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

    if tool_name == "engineer_restore" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state,
            caller_id,
            engineer_ident,
            include_tombstoned=True,
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_restore",
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
            if requested_group != _engineer_group:
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

            suggested_specialization = str(
                args.get("suggested_specialization", "") or ""
            ).strip()

            architect_create_payload = {
                "cmd": "board_add_task",
                "task": title,
                "description": args.get("description", ""),
                "group": _engineer_group,
                "lane": args.get("lane", ""),
                "labels": args.get("labels", []),
                "assigned_engineer_id": assigned_engineer_id,
            }
            architect_deliverable = args.get("deliverable")
            if isinstance(architect_deliverable, dict):
                architect_create_payload["deliverable"] = architect_deliverable
            create_result = await handle_command(architect_create_payload)
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
                    "suggested_specialization": suggested_specialization,
                    "action_name": "",
                    "action_vars": action_vars,
                })
                if update_result and update_result.get("type") == "error":
                    return update_result.get("message", "Unknown error"), True

            response = dict(create_result) if create_result else {"type": "ok"}
            if suggested_specialization:
                engineer_specs = set(
                    getattr(assigned_engineer, "engineer_specializations", [])
                    or []
                )
                if suggested_specialization not in engineer_specs:
                    response["suggested_specialization_warning"] = (
                        f"assigned engineer does not carry specialization "
                        f"'{suggested_specialization}'"
                    )
            if task_id:
                created_task = real_state.board_tasks.get(task_id)
                awareness_block = build_engineer_deliverable_awareness(
                    created_task
                )
                if awareness_block:
                    response["deliverable_awareness"] = awareness_block
            return json.dumps(response), False

        payload = {
            "cmd": "board_add_task",
            "task": args.get("title", ""),
            "description": args.get("description", ""),
            "group": _engineer_group,
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
        engineer_deliverable = args.get("deliverable")
        if isinstance(engineer_deliverable, dict):
            payload["deliverable"] = engineer_deliverable
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_update" and caller_kind == "architect":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task or str(getattr(task, "group", "") or "").strip() != _engineer_group:
            return "Task not found", True
        caller_id_str = str(caller_id or "").strip()
        creator_class = _task_created_by_classifier(task)
        creator_architect_id = str(
            getattr(task, "created_by_architect_id", "") or ""
        ).strip()
        if creator_class != "user" and creator_architect_id != caller_id_str:
            return "Task was not created by this architect", True

        patch = {}
        updated_fields = []
        if "title" in args:
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            patch["task"] = title
            updated_fields.append("title")
        if "description" in args:
            description = str(args.get("description", "") or "")
            if not description.strip():
                return "description is required", True
            patch["description"] = description
            updated_fields.append("description")
        if "labels" in args:
            labels = args.get("labels")
            if not isinstance(labels, list):
                return "labels must be a list", True
            normalized_labels = []
            seen_labels = set()
            for item in labels:
                if not isinstance(item, str):
                    return "labels entries must be strings", True
                label = item.strip()
                if not label or label in seen_labels:
                    continue
                normalized_labels.append(label)
                seen_labels.add(label)
            patch["labels"] = normalized_labels
            updated_fields.append("labels")
        if "action_name" in args:
            action_name = str(args.get("action_name", "") or "").strip()
            action_error = await _validate_task_update_action_name(
                action_name,
                _engineer_group,
                handle_command,
            )
            if action_error:
                return action_error, True
            patch["action_name"] = action_name
            updated_fields.append("action_name")
        if "action_vars" in args:
            action_vars = args.get("action_vars")
            if action_vars is None:
                action_vars = {}
            if not isinstance(action_vars, dict):
                return "action_vars must be an object", True
            patch["action_vars"] = copy.deepcopy(action_vars)
            updated_fields.append("action_vars")
        if not updated_fields:
            return "At least one editable field is required", True

        update_result = await handle_command({
            "cmd": "board_update_task",
            "id": tid,
            **patch,
        })
        if update_result and update_result.get("type") == "error":
            return update_result.get("message", "Unknown error"), True
        return json.dumps({
            "type": "ok",
            "task_id": tid,
            "updated_fields": updated_fields,
        }), False

    if tool_name == "task_reassign":
        task_state = state
        if caller_kind == "engineer":
            task_state = real_state
        tid = _resolve_task(task_state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = task_state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        engineer_ident = str(args.get("new_engineer_id", "") or "").strip()
        if not engineer_ident:
            return "new_engineer_id is required", True
        caller_id_str = str(caller_id or "").strip()
        old_engineer_id = _effective_assigned_engineer_id(task)

        if caller_kind == "architect":
            if str(
                getattr(task, "created_by_architect_id", "") or ""
            ).strip() != caller_id_str:
                return "Task was not created by this architect", True
            engineer_id, engineer_error = _resolve_architect_hired_engineer(
                real_state, caller_id, engineer_ident
            )
        else:
            caller_group = _caller_group(real_state, caller_id_str)
            if str(getattr(task, "group", "") or "").strip() != caller_group:
                return "Task not found", True
            created_by_engineer_id = str(
                getattr(task, "created_by_engineer_id", "") or ""
            ).strip()
            if (
                old_engineer_id != caller_id_str
                and created_by_engineer_id != caller_id_str
            ):
                return (
                    "Task can only be reassigned by the assigned engineer "
                    "or creator"
                ), True
            engineer_id, engineer_error = _resolve_group_engineer(
                real_state, caller_id, engineer_ident
            )
        if not engineer_id:
            return engineer_error, True
        engineer = real_state.agents.get(engineer_id)
        if _agent_dismissed_at(engineer):
            return _engineer_dismissed_error(engineer_id), True
        real_state.board_update_task(tid, assigned_engineer_id=engineer_id)
        if caller_kind == "architect":
            return json.dumps({
                "type": "ok",
                "task_id": tid,
                "assigned_engineer_id": engineer_id,
            }), False
        return json.dumps({
            "type": "ok",
            "task_id": tid,
            "from": old_engineer_id,
            "to": engineer_id,
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
        actor_name = getattr(_engineer_cell, "name", "") or caller_kind
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
        task = state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        requested_new_lane = str(args.get("new_lane", "") or "").strip()
        requested_lane = str(args.get("lane", "") or "").strip()
        if (
            requested_new_lane
            and requested_lane
            and requested_new_lane != requested_lane
        ):
            return "lane and new_lane must match when both are provided", True
        target_lane = requested_new_lane or requested_lane
        if not target_lane:
            required_arg = "new_lane" if tool_prefix == "architect_" else "lane"
            return f"{required_arg} is required", True
        if target_lane not in real_state.board_lanes:
            return f"Unknown lane: {target_lane}", True
        clear_status, clear_status_error = _optional_bool_arg(
            args,
            "clear_status",
            False,
        )
        if clear_status_error:
            return clear_status_error, True
        previous_lane = str(getattr(task, "lane", "") or "")
        result = await handle_command({
            "cmd": "board_move_task",
            "id": tid,
            "lane": target_lane,
            "clear_status": clear_status,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        moved = real_state.board_tasks.get(tid)
        if not moved:
            return "Task not found", True
        return json.dumps({
            "type": "task_moved",
            "task_id": tid,
            "previous_lane": previous_lane,
            "new_lane": str(getattr(moved, "lane", "") or ""),
            "status": str(getattr(moved, "status", "") or ""),
        }), False

    if tool_name == "task_dispatch":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        payload = {
            "cmd": "dispatch_task",
            "id": tid,
            "_engineer_dispatch_group": _engineer_group,
            "_engineer_dispatch_id": _engineer_cell.id,
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
            payload["_created_by_engineer_id"] = _engineer_cell.id
            payload["owner_engineer_id"] = str(caller_id or "").strip()
            if _engineer_cell:
                if _engineer_cell.session_id:
                    payload["target_session_id"] = _engineer_cell.session_id
                if _engineer_cell.window_id:
                    payload["target_window_id"] = _engineer_cell.window_id
            agent_type = _sanitize_mcp_worker_provider_override(
                state,
                _engineer_group,
                _engineer_cell.id,
                args.get("agent_type", ""),
            )
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
        result_type = str((result or {}).get("type", "") or "ok")
        if (
                caller_kind == "engineer"
                and result_type != "dispatch_action_missing"):
            has_launch_overrides = _has_task_dispatch_launch_overrides(args)
            existing_agent = bool(agent_ident)
            shape = "warm_cluster" if existing_agent else "serial"
            task_ids = [
                str((result or {}).get("task_id", "") or tid).strip()
            ]
            _record_engineer_dispatch_shape(
                real_state,
                engineer_id=_engineer_cell.id,
                group=_engineer_group,
                source_tool="engineer_task_dispatch",
                shape=shape,
                task_ids=task_ids,
                task_count=1,
                outcome=result_type,
                hintable=(
                    shape == "serial"
                    and not existing_agent
                    and not has_launch_overrides
                ),
                metadata={
                    "existing_agent": existing_agent,
                    "has_launch_overrides": has_launch_overrides,
                    "target_agent_id": str(payload.get("agent_id", "") or ""),
                },
            )
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "batch_dispatch":
        raw_tasks = args.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return "tasks must be a non-empty array", True
        raw_max_concurrent = args.get("max_concurrent", None)
        if raw_max_concurrent is None:
            max_concurrent = normalize_default_worker_concurrency(
                state.get_engineer_settings(
                    _engineer_group).default_worker_concurrency
            )
        elif not isinstance(raw_max_concurrent, int) \
                or raw_max_concurrent < 1:
            return (
                "max_concurrent must be an integer >= 1; it is the "
                "engineer-group active worker cap for this batch, not an "
                "agent_group affinity cap."
            ), True
        else:
            max_concurrent = raw_max_concurrent

        provider = _sanitize_mcp_worker_provider_override(
            state,
            _engineer_group,
            _engineer_cell.id,
            args.get("provider", ""),
        )
        dispatch_lane = (
            state.get_group_settings(_engineer_group).dispatch_lane
            or "In Progress"
        )
        active_agents = _active_worker_ids(state, _engineer_group)
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
            queued_group, queued_idx, queued_entry = \
                state.auto_dispatch_queue_find(tid)
            if queued_entry:
                if queued_group != _engineer_group:
                    _fail(
                        idx,
                        task_ident,
                        "already_queued",
                        (
                            "Task is already queued for auto-dispatch in "
                            f"engineer group '{queued_group}'."
                        ),
                        tid,
                    )
                    continue
                prior_cap = int(queued_entry.max_concurrent or 1)
                refreshed_entry, cap_raised = (
                    state.auto_dispatch_queue_raise_max_concurrent(
                        _engineer_group, tid, max_concurrent
                    )
                )
                queue = state.auto_dispatch_queues.get(_engineer_group, [])
                queue_position = queued_idx + 1 if queued_idx >= 0 else len(queue)
                if cap_raised and refreshed_entry:
                    item = {
                        "index": idx,
                        "task": task_ident,
                        "task_id": tid,
                        "status": "cap_raised",
                        "reason": "already_queued_cap_raised",
                        "message": (
                            "Task was already deferred for engineer group "
                            f"'{_engineer_group}'; raised stored "
                            "max_concurrent (engineer-group active worker "
                            f"cap) from {prior_cap} to {max_concurrent}."
                        ),
                        "queue_position": queue_position,
                        "previous_max_concurrent": prior_cap,
                        "max_concurrent": max_concurrent,
                        "queued_at": refreshed_entry.enqueued_at,
                    }
                    if refreshed_entry.agent_group:
                        item["agent_group"] = refreshed_entry.agent_group
                    results.append(item)
                    continue
                item = {
                    "index": idx,
                    "task": task_ident,
                    "task_id": tid,
                    "status": "failed",
                    "reason": "already_queued",
                    "message": (
                        "Task is already queued for auto-dispatch in "
                        f"engineer group '{_engineer_group}' with "
                        f"max_concurrent={prior_cap}; requested "
                        f"max_concurrent={max_concurrent} does not raise "
                        "the engineer-group active worker cap, so the "
                        "queued entry was left unchanged."
                    ),
                    "queue_position": queue_position,
                    "current_max_concurrent": prior_cap,
                    "requested_max_concurrent": max_concurrent,
                }
                if queued_entry.agent_group:
                    item["agent_group"] = queued_entry.agent_group
                results.append(item)
                continue

            task = state.board_tasks.get(tid)
            if not task:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if task.group != _engineer_group:
                _fail(idx, task_ident, "wrong_group",
                      "Task is outside the engineer's group.", tid)
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
                    _engineer_group,
                    tid,
                    agent_group=agent_group,
                    max_concurrent=max_concurrent,
                    target_agent_id=target_agent_id,
                    engineer_owner_id=_engineer_cell.id,
                    provider=provider,
                )
                queue = state.auto_dispatch_queues.get(_engineer_group, [])
                item = {
                    "index": idx,
                    "task": task_ident,
                    "task_id": tid,
                    "status": "deferred",
                    "reason": "max_concurrent_reached",
                    "message": (
                        "Dispatch would exceed max_concurrent for engineer "
                        f"group '{_engineer_group}' "
                        f"({len(active_agents)}/{max_concurrent} active "
                        "worker slots in use). max_concurrent is the "
                        "engineer-group active worker cap for this batch; "
                        "agent_group only controls same-agent affinity."
                    ),
                    "engineer_group": _engineer_group,
                    "active_count": len(active_agents),
                    "cap": max_concurrent,
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
                "_engineer_dispatch_group": _engineer_group,
                "_engineer_dispatch_id": _engineer_cell.id,
            }
            if target_agent_id:
                payload["agent_id"] = target_agent_id
            else:
                payload["create_agent"] = True
                payload["_created_by_engineer_id"] = _engineer_cell.id
                payload["owner_engineer_id"] = str(caller_id or "").strip()
                if provider:
                    payload["agent_type"] = provider

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
        valid_entries = [
            item for item in results
            if str(item.get("status", "") or "")
            in _DISPATCH_SHAPE_VALID_BATCH_STATUSES
        ]
        if caller_kind == "engineer" and valid_entries:
            shape, shape_data = _batch_dispatch_shape(valid_entries)
            metadata = dict(shape_data.get("metadata", {}))
            metadata.update({
                "raw_entry_count": len(raw_tasks),
                "result_count": len(results),
                "max_concurrent": max_concurrent,
                "active_before": active_before,
                "active_after": len(active_agents),
                "provider": provider,
            })
            _record_engineer_dispatch_shape(
                real_state,
                engineer_id=_engineer_cell.id,
                group=_engineer_group,
                source_tool="engineer_batch_dispatch",
                shape=shape,
                task_ids=shape_data.get("task_ids", []),
                task_count=len(valid_entries),
                outcome="ok",
                hintable=False,
                metadata=metadata,
            )
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

        # Scope to engineer's group
        events = [e for e in events
                  if e.get("group", "") == _engineer_group]
        if type_filter:
            events = [e for e in events if e.get("kind") in type_filter]

        cursor = events[-1]["id"] if events else since_id
        return json.dumps({"events": events, "cursor": cursor},
                          ), False

    if tool_name == "launch_settings":
        fields = {}
        mapping = {
            "provider": "engineer_provider",
            "command": "engineer_boot_command",
            "model": "engineer_model",
            "reasoning_effort": "engineer_reasoning_effort",
        }
        for src, dest in mapping.items():
            if src in args:
                fields[dest] = str(args.get(src, "") or "").strip()
        result = await handle_command({
            "cmd": "engineer_update_settings",
            "group": _engineer_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_engineer_settings(_engineer_group))}), False

    if tool_name == "notifications":
        ws = state.get_engineer_settings(_engineer_group)
        fields = {}
        preset_name = str(args.get("preset", "") or "").strip().lower()
        if preset_name:
            try:
                fields.update(get_engineer_notification_preset(preset_name))
            except ValueError:
                return (
                    "Unknown notification preset. Use quiet, normal, or noisy.",
                    True,
                )
        if "digest_verbosity" in args:
            fields["digest_verbosity"] = normalize_engineer_digest_verbosity(
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
            "cmd": "engineer_update_settings",
            "group": _engineer_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_engineer_settings(_engineer_group))}), False

    if tool_name == "resume":
        result = await handle_command({
            "cmd": "engineer_resume",
            "group": _engineer_group,
            "engineer_id": str(caller_id or "").strip(),
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
        decision = await real_state.save_decision_async({
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

        updated = await real_state.save_decision_async(patch)
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

        updated = await real_state.save_decision_async(patch)
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
            if entry_type not in _ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPES:
                return (
                    "type must be one of: decision, observation, "
                    "checkpoint, plan",
                    True,
                )
            entry = str(args.get("entry", "") or "")
            if not entry:
                return "entry is required", True
            if not idempotency_key:
                return json.dumps(real_state.architect_journal_append(
                    caller_id,
                    entry_type,
                    entry,
                )), False
            result = await handle_command({
                "cmd": "architect_journal_append",
                "architect_id": caller_id,
                "entry_type": entry_type,
                "entry": entry,
            })
            if result and result.get("type") == "error":
                return result.get("message", "Unknown error"), True
            return json.dumps(result), False
        result = await handle_command({
            "cmd": "engineer_journal_append",
            "group": _engineer_group,
            "entry_type": args.get("type", "observation"),
            "entry": args.get("entry", ""),
            "author_cell_id": str(caller_id or "").strip(),
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
        scope = str(args.get("scope", "") or "").strip().lower()
        include_cross_author = bool(args.get("include_cross_author", False))
        if scope and scope not in {"self", "group"}:
            return "scope must be one of: self, group", True
        author_cell_id = ""
        if caller_kind == "engineer" and scope != "group" \
                and not include_cross_author:
            author_cell_id = str(caller_id or "").strip()
        entries = real_state.journal_read(
            _engineer_group,
            args.get("tail", 20),
            args.get("type", ""),
            author_cell_id=author_cell_id,
        )
        return json.dumps({"type": "journal", "entries": entries}), False

    if tool_name == "engineer_journal_read" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        type_filter = str(args.get("type_filter", "") or "").strip()
        if type_filter and type_filter not in _JOURNAL_ENTRY_TYPES:
            return (
                "type_filter must be one of: decision, observation, "
                "checkpoint, plan, note_dismissed, qa",
                True,
            )
        try:
            since_value = float(args.get("since", 0) or 0)
        except (TypeError, ValueError):
            since_value = 0.0
        try:
            limit_value = int(args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit_value = 20
        if limit_value <= 0:
            return json.dumps({"type": "journal", "entries": []}), False
        limit_value = min(limit_value, 100)
        engineer = real_state.agents.get(engineer_id)
        engineer_group = str(getattr(engineer, "group", "") or "").strip()
        if not engineer_group:
            return json.dumps({"type": "journal", "entries": []}), False
        entries = real_state.journal_read(
            engineer_group,
            limit_value,
            type_filter,
            author_cell_id=engineer_id,
        )
        if since_value:
            filtered = []
            for entry in entries:
                try:
                    timestamp = float((entry or {}).get("timestamp", 0) or 0)
                except (TypeError, ValueError):
                    timestamp = 0.0
                if timestamp > since_value:
                    filtered.append(entry)
            entries = filtered
        return json.dumps({"type": "journal", "entries": entries}), False

    # -- Interaction tools --------------------------------------------------

    if tool_name == "message_user" and caller_kind in {"architect", "engineer"}:
        sender = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        context, context_error = _normalize_agent_user_message_context(
            real_state,
            caller_kind,
            caller_id,
            _engineer_group,
            args,
        )
        if context_error:
            return context_error, True
        length_error = _validate_architect_peer_message_length(
            message,
            context.get("context_summary", ""),
        )
        if length_error:
            return length_error, True
        try:
            saved, created = save_agent_user_direct_message_from_mcp(
                real_state,
                sender,
                message=message,
                thread_id=str(args.get("thread_id", "") or "").strip(),
                reply_to_id=str(args.get("reply_to_id", "") or "").strip(),
                context=context,
                idempotency_key=idempotency_key,
                notify=True,
            )
        except ValueError as exc:
            return str(exc), True
        return json.dumps(
            _direct_user_message_response(saved, deduped=not created)
        ), False

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

    if tool_name == "peer_message" and caller_kind == "architect":
        recipient, recipient_error = _resolve_architect_peer(
            real_state,
            caller_id,
            str(args.get("architect_id", "") or "").strip(),
        )
        if not recipient:
            return recipient_error, True
        architect = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        context, context_error = _normalize_architect_peer_context(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )
        if context_error:
            return context_error, True
        length_error = _validate_architect_peer_message_length(
            message,
            context.get("context_summary", ""),
        )
        if length_error:
            return length_error, True
        try:
            saved, created = _save_architect_peer_message(
                real_state,
                architect,
                recipient,
                action="architect_peer_message",
                message=message,
                ack_required=ack_required,
                context=context,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            return str(exc), True
        if created:
            delivery = await _inject_architect_peer_message(
                handle_command,
                real_state,
                architect,
                recipient,
                saved,
                message,
            )
            current = real_state.db.load_agent_peer_message(saved["id"])
            if current:
                saved = current
        else:
            delivery = {
                "state": str(saved.get("delivery_state", "buffered") or "buffered"),
                "reason": str(saved.get("delivery_reason", "") or ""),
            }
        return json.dumps({
            "type": "ok",
            "message_id": saved["id"],
            "thread_id": saved["thread_id"],
            "recipient_architect_id": recipient.id,
            "ack_required": bool(saved.get("ack_required", False)),
            "delivery": delivery,
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
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        delivered = _deliver_architect_engineer_message(
            real_state,
            engineer,
            architect,
            action="engineer_message_architect",
            message=message,
            ack_required=ack_required,
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
            peer_kind = str(entry.get("peer_kind", "") or "").strip()
            if peer_kind == "architect":
                peer, peer_error = _resolve_architect_peer(
                    real_state,
                    caller_id,
                    peer_id,
                )
                if not peer:
                    return peer_error, True
                action = "architect_peer_reply"
            elif peer_kind in {"", "engineer"}:
                engineer_id, engineer_error = _resolve_architect_hired_engineer(
                    real_state, caller_id, peer_id
                )
                if not engineer_id:
                    return engineer_error, True
                peer = real_state.agents.get(engineer_id)
                action = "architect_reply"
            else:
                return "Message peer kind is not replyable", True
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
        ack_required = False
        if caller_kind == "engineer" or (
                caller_kind == "architect" and action == "architect_peer_reply"):
            ack_required, ack_error = _optional_bool_arg(args, "ack_required")
            if ack_error:
                return ack_error, True
        if caller_kind == "architect" and action == "architect_peer_reply":
            length_error = _validate_architect_peer_message_length(message)
            if length_error:
                return length_error, True
            try:
                saved, created = _save_architect_peer_message(
                    real_state,
                    caller,
                    peer,
                    action=action,
                    message=message,
                    reply_to_id=str(entry.get("id", "") or "").strip(),
                    thread_id=str(entry.get("thread_id", "") or "").strip(),
                    ack_required=ack_required,
                    idempotency_key=idempotency_key,
                )
            except ValueError as exc:
                return str(exc), True
            if created:
                await _inject_architect_peer_message(
                    handle_command,
                    real_state,
                    caller,
                    peer,
                    saved,
                    message,
                )
            return json.dumps({
                "type": "ok",
                "message_id": saved["id"],
                "thread_id": saved["thread_id"],
            }), False
        delivered = _deliver_architect_engineer_message(
            real_state,
            caller,
            peer,
            action=action,
            message=message,
            reply_to_id=str(entry.get("id", "") or "").strip(),
            thread_id=str(entry.get("thread_id", "") or "").strip(),
            ack_required=ack_required,
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
        reply_required, reply_error = _optional_bool_arg(
            args,
            "reply_required",
            True,
        )
        if reply_error:
            return reply_error, True

        result = await handle_command({
            "cmd": "engineer_message",
            "agent_id": agent_id,
            "message": args.get("message", ""),
            "reply_required": reply_required,
            "sender_agent_id": (
                str(getattr(_engineer_cell, "id", "") or "").strip()
                or str(caller_id or "").strip()
            ),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "ask" and caller_kind == "architect":
        question = str(args.get("question", "") or "").strip()
        if not question:
            return "Question is required", True
        architect = real_state.agents.get(str(caller_id or "").strip())

        create_result = await handle_command({
            "cmd": "board_add_task",
            "task": question,
            "description": str(args.get("description", "") or ""),
            "group": _engineer_group,
            "lane": "Backlog",
            "labels": ["torque:human", "architect-ask"],
        })
        if create_result and create_result.get("type") == "error":
            return create_result.get("message", "Unknown error"), True

        task_id = str((create_result or {}).get("task_id", "") or "").strip()
        if not task_id:
            return "Failed to create architect ask task", True

        update_result = await handle_command({
            "cmd": "board_update_task",
            "id": task_id,
            "created_by_architect_id": str(caller_id or "").strip(),
            "reply_agent_id": str(caller_id or "").strip(),
            "assigned_engineer_id": "",
            "agent_id": "",
            "action_name": "",
            "status": "Awaiting Input",
        })
        if update_result and update_result.get("type") == "error":
            return update_result.get("message", "Unknown error"), True
        save_direct_ask_mirror(
            real_state,
            architect,
            question,
            source_task_id=task_id,
        )

        return json.dumps({
            "type": "ok",
            "task_id": task_id,
            "status": "Awaiting Input",
            "labels": ["torque:human", "architect-ask"],
        }), False

    if tool_name == "ask":
        question = args.get("question", "").strip()
        if not question:
            return "Question is required", True

        result = await handle_command({
            "cmd": "engineer_ask",
            "group": _engineer_group,
            "question": question,
            "engineer_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Question posted to the Agent panel. Event pushes have "
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
            "cmd": "engineer_note",
            "group": _engineer_group,
            "message": message,
            "kind": kind,
            "engineer_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Note posted to the Agent panel without pausing event "
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
        force_sibling_divergence = bool(args.get("force"))
        force_stale_base = bool(
            args.get("force_stale_base") or args.get("force")
        )
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
        pr_title = str(args.get("pr_title") or "").strip()
        if pr_title:
            payload["pr_title"] = pr_title
        pr_body = str(args.get("pr_body") or "").strip()
        if pr_body:
            payload["pr_body"] = pr_body
        if "close_agent_on_merge" in args:
            payload["close_agent_on_merge"] = bool(
                args.get("close_agent_on_merge")
            )
        if "remove_worktree_on_merge" in args:
            payload["remove_worktree_on_merge"] = bool(
                args.get("remove_worktree_on_merge")
            )
        if "auto_move_to_done" in args:
            payload["auto_move_to_done"] = bool(
                args.get("auto_move_to_done")
            )
        if "force_direct" in args:
            payload["force_direct"] = bool(args.get("force_direct"))
        if force_stale_base:
            payload["force_stale_base"] = True
        if force_sibling_divergence:
            payload["force"] = True
        result = await handle_command(payload)
        if result and result.get("ok") is False:
            error, cacheable = _format_worktree_pr_error(result, "Merge failed")
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
            if cacheable is False:
                return error, True, False
            return error, True
        cleanup = result.get("cleanup", {}) if result else {}
        cleanup_errors = cleanup.get("errors", [])
        if cleanup_errors:
            return (
                f"Merged {cell.worktree_branch} into "
                f"{cell.worktree_base_branch}, but cleanup failed:\n"
                + "\n".join(f"  - {err}" for err in cleanup_errors)
            ), True
        return json.dumps(
            _worktree_merge_success_payload(result, cell)
        ), False

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
            error, cacheable = _format_worktree_pr_error(
                result,
                "Failed to create pull request",
            )
            if cacheable is False:
                return error, True, False
            return error, True
        pr_url = _worktree_result_pr_url(result)
        payload = {
            "type": "ok",
            "url": pr_url,
            "pr_url": pr_url,
            "message": (result or {}).get("message", "PR created"),
        }
        phase = _worktree_result_phase(result)
        if phase:
            payload["phase"] = phase
        if isinstance((result or {}).get("pr"), dict):
            payload["pr"] = result["pr"]
        return json.dumps(payload), False

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

        # Review workers may share their implementer's worktree; diff follows
        # the selected cell's shared branch context, not a reviewer-local fork.
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
