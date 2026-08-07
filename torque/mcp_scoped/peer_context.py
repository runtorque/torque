"""Architect/Engineer peer resolution and normalized context snapshots."""

import time

from torque.mcp_engineer_tools.shared import (
    resolve_agent as _resolve_agent,
    resolve_task as _resolve_task,
)
from torque.mcp_scoped.architect_reports import _compact_json
from torque.mcp_scoped.common import (
    _agent_dismissed_at,
    _agent_is_tombstoned,
    _dedupe_strings,
    _effective_assigned_engineer_id,
    _filter_tasks_for_caller,
    _is_architect_cell,
    _is_engineer_like_cell,
    _optional_bool_arg,
    _resolve_agent_including_tombstoned,
    _resolve_architect_engineer,
    _summary_task_title,
)
from torque.task_content import compute_task_content_hash

TASK_READ_GRANT_MARKER = "torque.task-read-grant.v1"


def _task_content_hash(task) -> str:
    """Return the authored-content identity, recomputed from live content."""
    return compute_task_content_hash(task)


def _task_read_grant_from_row(row: dict) -> dict:
    snapshot = dict((row or {}).get("context_snapshot", {}) or {})
    grant = snapshot.get("task_read_grant", {})
    if not isinstance(grant, dict):
        return {}
    grant = dict(grant)
    if grant.get("marker") != TASK_READ_GRANT_MARKER:
        return {}
    return grant
from torque.mcp_scoped.health import _engineer_streams, _resolve_stream_payload
from torque.state import board_task_is_closed
from torque.worktree_streams import member_task_ids_for_stream

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
        return None, "peer is required"
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


def _engineer_peer_hiring_architect_id(cell) -> str:
    return str(getattr(cell, "hired_by_architect_id", "") or "").strip()


def _engineer_peer_item(cell) -> dict:
    item = {
        "id": str(getattr(cell, "id", "") or ""),
        "slug": str(getattr(cell, "slug", "") or ""),
        "name": str(getattr(cell, "name", "") or ""),
        "group": str(getattr(cell, "group", "") or ""),
        "status": str(getattr(cell, "status", "") or ""),
        "hired_by_architect_id": _engineer_peer_hiring_architect_id(cell),
    }
    dismissed_at = _agent_dismissed_at(cell)
    if dismissed_at:
        item["dismissed_at"] = dismissed_at
    return item


def _engineer_peer_eligibility_error(
        state,
        caller_id: str,
        peer,
        *,
        include_dismissed: bool = False) -> str:
    caller = state.agents.get(str(caller_id or "").strip())
    if not caller or not _is_engineer_like_cell(state, caller):
        return "engineer not found in scope"
    if not peer or not _is_engineer_like_cell(state, peer):
        return "engineer not found in scope"
    if str(getattr(peer, "id", "") or "").strip() == str(caller_id or "").strip():
        return "cannot message self"
    if _agent_is_tombstoned(state, peer):
        return "engineer is tombstoned"
    if _agent_dismissed_at(peer) and not include_dismissed:
        return "engineer is dismissed"
    caller_group = str(getattr(caller, "group", "") or "").strip()
    peer_group = str(getattr(peer, "group", "") or "").strip()
    if not caller_group or peer_group != caller_group:
        return "engineer not found in scope"
    caller_architect_id = _engineer_peer_hiring_architect_id(caller)
    peer_architect_id = _engineer_peer_hiring_architect_id(peer)
    if not caller_architect_id or peer_architect_id != caller_architect_id:
        return "engineer not found in scope"
    architect = state.agents.get(caller_architect_id)
    if not _is_architect_cell(architect, state) or _agent_is_tombstoned(state, architect):
        return "engineer not found in scope"
    return ""


def _resolve_engineer_peer(
        state,
        caller_id: str,
        engineer_ident: str,
        *,
        include_dismissed: bool = False) -> tuple[object | None, str]:
    engineer_ident = str(engineer_ident or "").strip()
    if not engineer_ident:
        return None, "peer is required"
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    peer = state.agents.get(engineer_id)
    error = _engineer_peer_eligibility_error(
        state,
        caller_id,
        peer,
        include_dismissed=include_dismissed,
    )
    if error:
        return None, error
    return peer, ""


def _resolve_engineer_peer_filter(
        state,
        caller_id: str,
        engineer_ident: str) -> tuple[str, str]:
    engineer_ident = str(engineer_ident or "").strip()
    if not engineer_ident:
        return "", ""
    peer, error = _resolve_engineer_peer(
        state,
        caller_id,
        engineer_ident,
        include_dismissed=True,
    )
    if not peer:
        return "", error
    return str(getattr(peer, "id", "") or "").strip(), ""


def _engineer_peer_list_json(
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
    caller = state.agents.get(str(caller_id or "").strip())
    caller_architect_id = _engineer_peer_hiring_architect_id(caller)
    engineers = []
    if caller_architect_id and caller_group:
        for cell in state.iter_agents(include_tombstoned=False):
            if str(getattr(cell, "id", "") or "").strip() == str(caller_id or "").strip():
                continue
            if (
                    getattr(cell, "cell_type", "") != "agent"
                    or str(getattr(cell, "kind", "") or "").strip() != "engineer"
                    or str(getattr(cell, "group", "") or "").strip() != caller_group
                    or _engineer_peer_hiring_architect_id(cell) != caller_architect_id):
                continue
            if _agent_dismissed_at(cell) and not include_dismissed:
                continue
            engineers.append(_engineer_peer_item(cell))
    engineers.sort(key=lambda item: (
        str(item.get("name", "") or "").lower(),
        str(item.get("id", "") or ""),
    ))
    return _compact_json({
        "type": "engineer_peers",
        "engineer_id": caller_id,
        "engineers": engineers,
    }), False


def _engineer_peer_stream_ref_items(raw_refs) -> list[dict]:
    if isinstance(raw_refs, (str, dict)):
        raw_refs = [raw_refs]
    if not isinstance(raw_refs, list):
        return []
    refs: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in raw_refs:
        if isinstance(raw, dict):
            item = {
                "stream": str(raw.get("stream", "") or raw.get("stream_id", "") or "").strip(),
                "repo_root": str(raw.get("repo_root", "") or "").strip(),
                "branch": str(raw.get("branch", "") or "").strip(),
                "task": str(raw.get("task", "") or raw.get("task_id", "") or "").strip(),
            }
        else:
            item = {
                "stream": str(raw or "").strip(),
                "repo_root": "",
                "branch": "",
                "task": "",
            }
        key = (
            item["stream"],
            item["repo_root"],
            item["branch"],
            item["task"],
        )
        if not any(key) or key in seen:
            continue
        seen.add(key)
        refs.append(item)
    return refs


def _engineer_peer_stream_snapshot(stream: dict, ref: dict) -> dict:
    member_ids = []
    try:
        member_ids = list(member_task_ids_for_stream(stream))
    except Exception:
        member_ids = []
    return {
        "stream_ref": str(ref.get("stream") or ref.get("branch") or ref.get("task") or ""),
        "stream_id": str((stream or {}).get("stream_id", "") or ""),
        "repo_root": str((stream or {}).get("repo_root", "") or ""),
        "branch": str((stream or {}).get("branch", "") or ""),
        "state": str((stream or {}).get("state", "") or ""),
        "agent_id": str((stream or {}).get("agent_id", "") or ""),
        "agent_name": str((stream or {}).get("agent_name", "") or ""),
        "agent_slug": str((stream or {}).get("agent_slug", "") or ""),
        "task_ids": member_ids,
        "captured_at": time.time(),
    }


def _normalize_engineer_peer_context(
        state,
        caller_id: str,
        caller_group: str,
        peer,
        args: dict) -> tuple[dict, str]:
    task_ids = []
    task_snapshots = []
    visible_tasks = _filter_tasks_for_caller(state, "engineer", caller_id)
    for task_ident in _dedupe_strings(args.get("context_task_ids", [])):
        task_id = _resolve_task(state, task_ident)
        if not task_id or task_id not in visible_tasks:
            return {}, f"Task not found: {task_ident}"
        task = state.board_tasks.get(task_id)
        if not task or str(getattr(task, "group", "") or "").strip() != caller_group:
            return {}, f"Task not found: {task_ident}"
        task_ids.append(task_id)
        task_snapshots.append(_peer_context_task_snapshot(task))

    stream_refs = _engineer_peer_stream_ref_items(args.get("context_stream_refs", []))
    stream_snapshots = []
    if stream_refs:
        caller = state.agents.get(str(caller_id or "").strip())
        streams = _engineer_streams(
            state,
            caller,
            caller_group,
            include_merged=True,
            include_orphaned=True,
        )
        for ref in stream_refs:
            task_ref = str(ref.get("task", "") or "").strip()
            task_id = _resolve_task(state, task_ref) if task_ref else ""
            stream, stream_error = _resolve_stream_payload(
                streams,
                stream_ident=str(ref.get("stream", "") or ""),
                repo_root=str(ref.get("repo_root", "") or ""),
                branch=str(ref.get("branch", "") or ""),
                task_id=task_id,
            )
            if not stream:
                return {}, stream_error
            stream_snapshots.append(_engineer_peer_stream_snapshot(stream, ref))

    if not task_ids and not stream_snapshots:
        return {}, "context_task_ids or context_stream_refs is required"

    caller = state.agents.get(str(caller_id or "").strip())
    supervising_architect_id = _engineer_peer_hiring_architect_id(caller)
    context_summary = str(args.get("context_summary", "") or "").strip()
    return {
        "context_task_ids": task_ids,
        "context_engineer_ids": [
            str(getattr(caller, "id", "") or "").strip(),
            str(getattr(peer, "id", "") or "").strip(),
        ],
        "context_decision_ids": [],
        "context_summary": context_summary,
        "context_snapshot": {
            "tasks": task_snapshots,
            "streams": stream_snapshots,
            "inspect_grant": {
                "scope": "thread_context",
                "source_engineer_id": str(getattr(caller, "id", "") or "").strip(),
                "recipient_engineer_id": str(getattr(peer, "id", "") or "").strip(),
                "supervising_architect_id": supervising_architect_id,
            },
        },
    }, ""


def _peer_context_task_snapshot(task) -> dict:
    return {
        "id": str(getattr(task, "id", "") or ""),
        "slug": str(getattr(task, "slug", "") or ""),
        "title": _summary_task_title(task),
        "lane": str(getattr(task, "lane", "") or ""),
        "status": str(getattr(task, "status", "") or ""),
        "labels": list(getattr(task, "labels", []) or []),
        "assigned_engineer_id": _effective_assigned_engineer_id(task),
        "assigned_architect_id": str(getattr(task, "assigned_architect_id", "") or ""),
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
