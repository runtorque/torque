"""Shared MCP tool implementation for engineer-scoped orchestration tools.

This module contains the shared read/write tool logic used by the new
``engineer_*`` namespace and by the legacy ``weaver_*`` compatibility
aliases. Scoping is caller-driven via ``caller_kind`` + ``caller_id``.

Security note: v1 keeps Loom's local-trust model. Environment/header
spoofing protections are out of scope for this stage.
"""

import copy
import json
from dataclasses import asdict, replace

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

# ---------------------------------------------------------------------------
# Shared scoping helpers
# ---------------------------------------------------------------------------

NO_ENGINEER_ALIAS_ERROR = (
    "no engineer exists; create one via the Engineers panel or the "
    "add_engineer server command"
)


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


def _is_engineer_like_cell(state, cell) -> bool:
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return False
    if str(getattr(cell, "kind", "") or "").strip() == "engineer":
        return True
    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return False
    return bool(state.get_group_settings(group).weaver_agent_id == cell.id)


def _visible_agent_ids_for_caller(state, caller_kind: str,
                                  caller_id: str) -> set[str]:
    if caller_kind == "legacy_weaver":
        weaver = state.agents.get(str(caller_id or "").strip())
        if not weaver or getattr(weaver, "cell_type", "") != "agent":
            return set()
        group = str(getattr(weaver, "group", "") or "").strip()
        if not group:
            return set()
        visible = set()
        restrict = bool(state.weaver_restricts_to_created_agents(group))
        for cell in state.agents.values():
            if getattr(cell, "cell_type", "") != "agent":
                continue
            if str(getattr(cell, "group", "") or "").strip() != group:
                continue
            if not restrict or str(
                getattr(cell, "created_by_weaver_id", "") or ""
            ).strip() == str(caller_id or "").strip():
                visible.add(cell.id)
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
    if caller_kind == "legacy_weaver":
        caller = state.agents.get(str(caller_id or "").strip())
        caller_group = str(getattr(caller, "group", "") or "").strip() if caller else ""
        if not caller_group:
            return {}
        return {
            task.id: task
            for task in state.board_tasks.values()
            if str(getattr(task, "group", "") or "").strip() == caller_group
        }
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


def resolve_default_engineer(state) -> str | None:
    engineers = [
        cell for cell in state.agents.values()
        if getattr(cell, "cell_type", "") == "agent"
        and str(getattr(cell, "kind", "") or "").strip() == "engineer"
    ]
    if not engineers:
        engineers = []
        seen = set()
        for group_name in list(getattr(state, "groups", {}).keys()):
            weaver_id = str(
                state.get_group_settings(group_name).weaver_agent_id or ""
            ).strip()
            if not weaver_id or weaver_id in seen:
                continue
            cell = state.agents.get(weaver_id)
            if not cell or getattr(cell, "cell_type", "") != "agent":
                continue
            engineers.append(cell)
            seen.add(weaver_id)
        if not engineers:
            return None
    if len(engineers) == 1:
        return engineers[0].id

    weaver_named = [
        cell for cell in engineers
        if str(getattr(cell, "name", "") or "") == "Weaver"
    ]
    if len(weaver_named) == 1:
        return weaver_named[0].id
    if weaver_named:
        engineers = weaver_named

    order_map = {
        cell.id: index for index, cell in enumerate(state.agents.values())
    }

    def sort_key(cell):
        created_at = getattr(cell, "created_at", "")
        if isinstance(created_at, (int, float)) and created_at:
            return (0, float(created_at), order_map.get(cell.id, 0), cell.id)
        if isinstance(created_at, str) and created_at:
            return (1, created_at, order_map.get(cell.id, 0), cell.id)
        created_ts = None
        db = getattr(state, "db", None)
        if db:
            try:
                record = db.load_agent_history_detail(cell.id) or {}
                created_ts = record.get("created_at")
            except Exception:
                created_ts = None
        if isinstance(created_ts, (int, float)) and created_ts:
            return (0, float(created_ts), order_map.get(cell.id, 0), cell.id)
        return (2, order_map.get(cell.id, 0), order_map.get(cell.id, 0), cell.id)

    engineers.sort(key=sort_key)
    return engineers[0].id if engineers else None


def authorize_caller(state, *, caller_kind: str, caller_id: str):
    caller_id = str(caller_id or "").strip()
    label = "engineer" if caller_kind == "engineer" else "weaver"
    if caller_kind not in {"engineer", "legacy_weaver"}:
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": f"unsupported caller kind: {caller_kind}",
        }), True
    if not caller_id:
        missing_message = (
            "LOOM_ENGINEER_ID is required"
            if caller_kind == "engineer"
            else "legacy weaver session id is required"
        )
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": missing_message,
        }), True
    cell = state.agents.get(caller_id)
    if not _is_engineer_like_cell(state, cell):
        error_text = (
            engineer_not_found_error(caller_id)
            if caller_kind == "engineer"
            else json.dumps({
                "type": "error",
                "message": f"no weaver with id={caller_id} exists",
            })
        )
        return None, "", caller_kind, error_text, True
    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": f"{label} {caller_id} is not assigned to a group",
        }), True
    effective_kind = (
        "engineer"
        if str(getattr(cell, "kind", "") or "").strip() == "engineer"
        else "legacy_weaver"
    )
    return cell, group, effective_kind, "", False


def build_scoped_state_view(state, *, caller_kind: str, caller_id: str,
                            caller_cell, caller_group: str):
    visible_agents = _filter_agents_for_caller(state, caller_kind, caller_id)
    visible_tasks = _filter_tasks_for_caller(state, caller_kind, caller_id)
    visible_agent_ids = {
        cell_id for cell_id, cell in visible_agents.items()
        if getattr(cell, "cell_type", "") == "agent"
    }
    if caller_kind == "legacy_weaver":
        scoped_agents = {}
        same_group_agent_ids = {
            cell.id for cell in state.agents.values()
            if getattr(cell, "cell_type", "") == "agent"
            and str(getattr(cell, "group", "") or "").strip() == caller_group
        }
        for cell in state.agents.values():
            if getattr(cell, "cell_type", "") == "agent":
                if str(getattr(cell, "group", "") or "").strip() == caller_group:
                    scoped_agents[cell.id] = cell
                continue
            parent_id = str(getattr(cell, "parent_id", "") or "").strip()
            if (
                str(getattr(cell, "group", "") or "").strip() == caller_group
                or parent_id in same_group_agent_ids
            ):
                scoped_agents[cell.id] = cell
    else:
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
    if caller_kind == "legacy_weaver":
        view_state.weaver_restricts_to_created_agents = (
            lambda group: state.weaver_restricts_to_created_agents(group)
        )
    else:
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
        for task in visible_tasks:
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
                unhealthy.append({
                    "id": task.id,
                    "title": task.task,
                    "health_state": health_state,
                    "health_since": getattr(task, "health_since", ""),
                })

            verification_state = getattr(task, "verification_state", "") or ""
            if not board_task_is_closed(task) and verification_state in verification_counts:
                verification_counts[verification_state] += 1
                if verification_state in {"pending", "failed"}:
                    verification_items.append({
                        "id": task.id,
                        "title": task.task,
                        "verification_state": verification_state,
                        "verification_mode": getattr(
                            task, "verification_mode", ""
                        ) or "",
                        "verification_notes": getattr(
                            task, "verification_notes", ""
                        ) or "",
                    })

            if "loom:human" in labels and not board_task_is_closed(task):
                pending_asks.append({
                    "id": task.id,
                    "title": task.task,
                    "parent_task_id": task.parent_task_id,
                })

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
            state,
            _weaver_group,
            weaver_id=_weaver_cell.id if _weaver_cell else "",
        )
        summary["hints"] = {
            "count": len(hints),
            "items": hints[:10],
            "truncated": len(hints) > 10,
        }
        return json.dumps(summary), False

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
        d["title"] = task.task
        d["action"] = task.action_name
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
                if agent_hidden:
                    item["agent_hidden"] = True
                d["pipeline_chain"].append(item)
        return json.dumps(d), False

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
                current_task = state.agent_current_task(agent_id)
                if current_task and current_task.group != _weaver_group:
                    current_task = None
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
        current_task = state.agent_current_task(agent_id)
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

    if tool_name == "task_create":
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
        }
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

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

    if tool_name == "journal":
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
            "Question posted to the Weaver panel. Event pushes have "
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
            "Note posted to the Weaver panel without pausing event "
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
        result, error_text, blocked = await _run_worktree_merge_check(
            handle_command, agent_id
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
