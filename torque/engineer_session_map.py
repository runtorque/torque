"""Deterministic Session Map read model for Engineer recovery."""

from __future__ import annotations

from datetime import datetime, timezone

from .mcp_engineer_tools.shared import worktree_boundary_overview
from .state import ARCHIVED_LANE, board_task_is_closed
from .task_health import HEALTH_SEVERITY
from .engineer_hints import compute_engineer_hints
from .worktree_streams import (
    compute_worktree_streams,
    stream_identity_for_agent,
)

_ITEM_LIMIT = 10
_JOURNAL_SCAN_LIMIT = 40
_QUEUE_STATE_ORDER = {
    "ready_to_resume": 0,
    "paused_by_validation": 1,
    "paused_by_blocker": 2,
    "paused_by_review": 3,
    "held": 4,
    "queued": 5,
}
_JOURNAL_TYPES = {"decision", "plan", "checkpoint"}


def build_engineer_session_map(state, group: str, *, engineer_cell=None,
                             item_limit: int = _ITEM_LIMIT,
                             journal_scan_limit: int = _JOURNAL_SCAN_LIMIT) -> dict:
    """Return a deterministic structured orchestration snapshot for ``group``."""
    group = str(group or "").strip()
    if not group:
        return {}
    engineer_cell = _resolve_engineer_cell(state, group, engineer_cell)

    tasks = [
        task for task in state.board_tasks.values()
        if getattr(task, "group", "") == group
    ]
    visible_tasks = [task for task in tasks if getattr(task, "lane", "") != ARCHIVED_LANE]
    archived_tasks = [task for task in tasks if getattr(task, "lane", "") == ARCHIVED_LANE]

    streams = [
        _scrub_stream(state, engineer_cell, stream)
        for stream in compute_worktree_streams(
            state,
            group=group,
            visibility_limit=max(5, int(item_limit)),
            include_orphaned=False,
        )
        if str(stream.get("state", "") or "").strip() != "merged"
    ]
    streams.sort(key=_stream_sort_key)

    asks = _build_pending_asks(visible_tasks, limit=item_limit)
    human_gates = _build_human_gates(state, streams, limit=item_limit)
    task_health = _build_task_health(state, visible_tasks, limit=item_limit)
    verification = _build_verification(visible_tasks, limit=item_limit)
    branch_boundaries = _build_branch_boundaries(
        state,
        streams,
        engineer_cell=engineer_cell,
        limit=item_limit,
    )
    agents = _build_agents(
        state,
        group,
        engineer_cell=engineer_cell,
        limit=item_limit,
    )
    queued_follow_up = _build_queued_follow_up(
        state,
        group,
        streams,
        engineer_cell=engineer_cell,
        limit=item_limit,
    )
    journal = _build_journal(
        state,
        group,
        limit=item_limit,
        journal_scan_limit=journal_scan_limit,
    )
    hints = _build_hints(
        state,
        group,
        engineer_cell=engineer_cell,
        limit=item_limit,
    )
    dispatch_shapes = _build_dispatch_shapes(
        state,
        group,
        engineer_cell=engineer_cell,
    )

    lane_counts = {
        lane_name: 0
        for lane_name in getattr(state, "board_lanes", [])
        if lane_name != ARCHIVED_LANE
    }
    for task in visible_tasks:
        lane = getattr(task, "lane", "") or ""
        if lane in lane_counts:
            lane_counts[lane] += 1

    return {
        "group": group,
        "overview": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tasks_total": len(visible_tasks),
            "archived_total": len(archived_tasks),
            "lanes": lane_counts,
            "active_stream_count": len(streams),
            "active_agent_count": agents["active_count"],
            "pending_ask_count": asks["count"],
            "human_gate_count": human_gates["count"],
            "unhealthy_task_count": sum(
                count for state_name, count in task_health["counts"].items()
                if state_name != "healthy"
            ),
            "queued_follow_up_count": queued_follow_up["count"],
            "journal_entry_count": journal["count"],
        },
        "streams": {
            "count": len(streams),
            "by_state": _stream_state_counts(streams),
            "items": streams[:item_limit],
            "truncated": len(streams) > item_limit,
        },
        "asks": asks,
        "human_gates": human_gates,
        "task_health": task_health,
        "verification": verification,
        "branch_boundaries": branch_boundaries,
        "agents": agents,
        "queued_follow_up": queued_follow_up,
        "journal": journal,
        "dispatch_shapes": dispatch_shapes,
        "hints": hints,
    }


def _build_pending_asks(tasks, *, limit: int) -> dict:
    items = []
    for task in tasks:
        labels = set(getattr(task, "labels", []) or [])
        if "torque:human" not in labels or board_task_is_closed(task):
            continue
        items.append({
            "id": getattr(task, "id", "") or "",
            "title": getattr(task, "task", "") or "",
            "parent_task_id": getattr(task, "parent_task_id", "") or "",
            "lane": getattr(task, "lane", "") or "",
            "created_at": getattr(task, "created_at", "") or "",
        })
    items.sort(key=lambda item: (item["created_at"], item["title"].lower(), item["id"]))
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _build_human_gates(state, streams: list[dict], *, limit: int) -> dict:
    items = []
    for stream in streams:
        queue_gate = stream.get("queue_gate", {}) or {}
        validation_state = str(stream.get("validation_state", "") or "").strip()
        gate_type = str(queue_gate.get("gate_type", "") or "").strip()
        if gate_type != "human_validation" and validation_state != "pending_human_validation":
            continue
        blocking_task_id = str(queue_gate.get("blocking_task_id", "") or "").strip()
        blocking_task = state.board_tasks.get(blocking_task_id) if blocking_task_id else None
        items.append({
            "stream_id": str(stream.get("stream_id", "") or "").strip(),
            "stream_title": _stream_title(stream),
            "branch": str(stream.get("branch", "") or "").strip(),
            "gate_reason": str(
                queue_gate.get("reason", "")
                or stream.get("gate_reason", "")
                or ""
            ).strip(),
            "recommended_next_action": str(
                stream.get("recommended_next_action", "") or ""
            ).strip(),
            "blocking_task_id": blocking_task_id,
            "blocking_task_title": getattr(blocking_task, "task", "") if blocking_task else "",
        })
    items.sort(key=lambda item: (item["branch"], item["stream_title"].lower(), item["stream_id"]))
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _build_task_health(state, tasks, *, limit: int) -> dict:
    counts = {
        "healthy": 0,
        "blocked": 0,
        "stale-in-progress": 0,
        "idle-risk": 0,
        "stalled": 0,
        "thrashing": 0,
    }
    items = []
    for task in tasks:
        labels = set(getattr(task, "labels", []) or [])
        if "torque:human" in labels:
            continue
        health_state = str(getattr(task, "health_state", "healthy") or "healthy")
        if health_state not in counts:
            counts[health_state] = 0
        counts[health_state] += 1
        if board_task_is_closed(task) or health_state == "healthy":
            continue
        details = task.health_details if isinstance(task.health_details, dict) else {}
        source_task_id = str(details.get("source_task_id", "") or "").strip()
        source_task = state.board_tasks.get(source_task_id) if source_task_id else None
        items.append({
            "id": getattr(task, "id", "") or "",
            "title": getattr(task, "task", "") or "",
            "health_state": health_state,
            "health_since": getattr(task, "health_since", "") or "",
            "via": getattr(source_task, "task", "") if source_task and source_task.id != task.id else "",
        })
    items.sort(
        key=lambda item: (
            -HEALTH_SEVERITY.get(item["health_state"], 0),
            item["health_since"],
            item["title"].lower(),
            item["id"],
        )
    )
    return {
        "counts": counts,
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _build_verification(tasks, *, limit: int) -> dict:
    counts = {
        "pending": 0,
        "attempted": 0,
        "passed": 0,
        "failed": 0,
    }
    items = []
    for task in tasks:
        if _verification_checkpoint_hidden_by_boundary(task):
            continue
        verification_state = str(getattr(task, "verification_state", "") or "").strip()
        if verification_state not in counts:
            continue
        counts[verification_state] += 1
        if verification_state not in {"pending", "failed"}:
            continue
        summary = getattr(task, "verification_summary", {}) or {}
        if not isinstance(summary, dict):
            summary = {}
        detail = (
            summary.get("human_validation_pending", "")
            or ("Live smoke pending" if summary.get("live_smoke_pending") else "")
            or (
                "Deploy not attempted"
                if summary.get("deploy_attempted") is False else ""
            )
            or summary.get("test_outcome", "")
            or getattr(task, "verification_notes", "")
            or summary.get("tests_run", "")
            or ""
        )
        items.append({
            "id": getattr(task, "id", "") or "",
            "title": getattr(task, "task", "") or "",
            "verification_state": verification_state,
            "verification_mode": getattr(task, "verification_mode", "") or "",
            "detail": str(detail).strip(),
        })
    items.sort(
        key=lambda item: (
            0 if item["verification_state"] == "failed" else 1,
            item["title"].lower(),
            item["id"],
        )
    )
    return {
        "counts": counts,
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _verification_checkpoint_hidden_by_boundary(task) -> bool:
    """Lazy Session Map filter; keep the persisted checkpoint for forensics."""
    boundary = getattr(task, "worktree_boundary", {}) or {}
    if not isinstance(boundary, dict):
        return False
    return str(boundary.get("status", "") or "").strip() == "merged"


def _build_branch_boundaries(state, streams: list[dict], *, engineer_cell, limit: int) -> dict:
    items = []
    seen = set()
    for stream in streams:
        repo_root = str(stream.get("repo_root", "") or "").strip()
        branch = str(stream.get("branch", "") or "").strip()
        if not repo_root or not branch:
            continue
        key = repo_root + "::" + branch
        if key in seen:
            continue
        seen.add(key)
        overview = worktree_boundary_overview(
            state,
            repo_root=repo_root,
            branch=branch,
        )
        if not overview:
            continue
        overview = dict(overview)
        overview["stream_id"] = str(stream.get("stream_id", "") or "").strip()
        overview["stream_title"] = _stream_title(stream)
        overview["foreground_task_id"] = str(
            stream.get("foreground_task_id", "") or ""
        ).strip()
        overview["foreground_task_title"] = str(
            stream.get("foreground_task_title", "") or ""
        ).strip()
        boundary_task = state.board_tasks.get(
            str(overview.get("latest_boundary_task_id", "") or "").strip()
        )
        boundary_data = (
            getattr(boundary_task, "worktree_boundary", {})
            if boundary_task is not None
            else {}
        )
        if not isinstance(boundary_data, dict):
            boundary_data = {}
        agent_id = (
            str(stream.get("agent_id", "") or "").strip()
            or str(boundary_data.get("recorded_by_agent_id", "") or "").strip()
        )
        agent = state.agents.get(agent_id) if agent_id else None
        if agent_id and _agent_visible(state, engineer_cell, agent_id):
            overview["agent_id"] = agent_id
            overview["agent_name"] = str(
                stream.get("agent_name", "")
                or stream.get("agent_slug", "")
                or getattr(agent, "name", "")
                or getattr(agent, "slug", "")
                or ""
            ).strip()
        elif agent_id:
            overview["agent_id"] = ""
            overview["agent_name"] = ""
            if _hidden_agent_marker(state, engineer_cell):
                overview["agent_hidden"] = True
        items.append(overview)
    items.sort(
        key=lambda item: (
            0 if item.get("partial_review_safe") else 1,
            item.get("branch", ""),
            item.get("latest_boundary_recorded_at", ""),
            item.get("stream_id", ""),
        )
    )
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _build_agents(state, group: str, *, engineer_cell, limit: int) -> dict:
    counts = {
        "idle": 0,
        "running": 0,
        "error": 0,
        "stopped": 0,
    }
    items = []
    total = 0
    needs_attention = 0
    for cell in state.iter_active_agents():
        if getattr(cell, "cell_type", "") not in {"agent", "terminal"}:
            continue
        if getattr(cell, "group", "") != group:
            continue
        if not _agent_visible(state, engineer_cell, getattr(cell, "id", "")):
            continue
        total += 1
        if getattr(cell, "needs_attention", False):
            needs_attention += 1
        status = str(getattr(cell, "status", "") or "stopped")
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
        if status == "stopped":
            continue
        current_task = state.agent_current_task(cell.id)
        identity = stream_identity_for_agent(cell)
        items.append({
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "status": status,
            "kind": (
                "terminal"
                if getattr(cell, "cell_type", "") == "terminal"
                else getattr(cell, "kind", "") or "worker"
            ),
            "type": getattr(cell, "agent_type", "") or "",
            "needs_attention": bool(getattr(cell, "needs_attention", False)),
            "current_task_id": getattr(current_task, "id", "") if current_task else "",
            "current_task": getattr(current_task, "task", "") if current_task else "",
            "activity": getattr(cell, "activity", "") or "",
            "activity_detail": getattr(cell, "activity_detail", "") or "",
            "branch": identity[1] if identity else "",
            "repo_root": identity[0] if identity else "",
        })
    items.sort(
        key=lambda item: (
            0 if item["status"] == "running" else 1,
            0 if item["needs_attention"] else 1,
            item["name"].lower(),
            item["id"],
        )
    )
    return {
        "total": total,
        "active_count": len(items),
        "needs_attention": needs_attention,
        "by_status": counts,
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _build_queued_follow_up(state, group: str, streams: list[dict], *,
                            engineer_cell, limit: int) -> dict:
    items = []
    for stream in streams:
        for queue_item in stream.get("queue_items", []) or []:
            items.append({
                "source": "stream_queue",
                "task_id": str((queue_item or {}).get("task_id", "") or "").strip(),
                "task_title": str((queue_item or {}).get("task_title", "") or "").strip(),
                "lane": str((queue_item or {}).get("lane", "") or "").strip(),
                "queue_state": str((queue_item or {}).get("queue_state", "") or "").strip(),
                "deps_met": bool((queue_item or {}).get("deps_met", False)),
                "held": bool((queue_item or {}).get("held", False)),
                "stream_id": str(stream.get("stream_id", "") or "").strip(),
                "stream_title": _stream_title(stream),
                "branch": str(stream.get("branch", "") or "").strip(),
                "gate_reason": str(
                    ((stream.get("queue_gate", {}) or {}).get("reason", ""))
                    or stream.get("gate_reason", "")
                    or ""
                ).strip(),
            })

    for entry in state.auto_dispatch_queues.get(group, []) or []:
        task = state.board_tasks.get(entry.task_id)
        target_agent_id = str(getattr(entry, "target_agent_id", "") or "").strip()
        target_agent = state.agents.get(target_agent_id) if target_agent_id else None
        item = {
            "source": "dispatch_queue",
            "task_id": str(getattr(entry, "task_id", "") or "").strip(),
            "task_title": getattr(task, "task", "") if task else str(getattr(entry, "task_id", "") or "").strip(),
            "lane": getattr(task, "lane", "") if task else "",
            "deps_met": state.board_deps_met(task) if task else True,
            "agent_group": str(getattr(entry, "agent_group", "") or "").strip(),
            "max_concurrent": int(getattr(entry, "max_concurrent", 1) or 1),
            "enqueued_at": str(getattr(entry, "enqueued_at", "") or "").strip(),
        }
        if target_agent_id and _agent_visible(state, engineer_cell, target_agent_id):
            item["target_agent_id"] = target_agent_id
            item["target_agent_name"] = (
                getattr(target_agent, "name", "")
                or getattr(target_agent, "slug", "")
                or target_agent_id
            ) if target_agent else target_agent_id
        elif target_agent_id:
            item["target_agent_id"] = ""
            item["target_agent_name"] = ""
            if _hidden_agent_marker(state, engineer_cell):
                item["target_agent_hidden"] = True
        items.append(item)

    items.sort(key=_queued_follow_up_sort_key)
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _build_journal(state, group: str, *, limit: int, journal_scan_limit: int) -> dict:
    entries = state.journal_read(group, max(limit, journal_scan_limit))
    filtered = [
        {
            "id": entry.get("id", 0),
            "type": str(entry.get("type", "") or "").strip(),
            "timestamp": entry.get("timestamp", 0),
            "entry": str(entry.get("entry", "") or ""),
        }
        for entry in entries
        if str(entry.get("type", "") or "").strip() in _JOURNAL_TYPES
    ]
    type_counts = {entry_type: 0 for entry_type in sorted(_JOURNAL_TYPES)}
    for entry in filtered:
        type_counts[entry["type"]] = type_counts.get(entry["type"], 0) + 1
    return {
        "count": len(filtered),
        "counts": type_counts,
        "items": filtered[:limit],
        "truncated": len(filtered) > limit,
    }


def _build_hints(state, group: str, *, engineer_cell, limit: int) -> dict:
    hints = compute_engineer_hints(
        state,
        group,
        engineer_id=getattr(engineer_cell, "id", "") or "",
    )
    return {
        "count": len(hints),
        "items": hints[:limit],
        "truncated": len(hints) > limit,
    }


def _build_dispatch_shapes(state, group: str, *, engineer_cell) -> dict:
    engineer_id = str(getattr(engineer_cell, "id", "") or "").strip()
    if not engineer_id:
        return {}
    summarizer = getattr(state, "engineer_dispatch_shape_summary", None)
    if not callable(summarizer):
        return {}
    return summarizer(engineer_id, group=group, window=20)


def _stream_state_counts(streams: list[dict]) -> dict[str, int]:
    counts = {}
    for stream in streams:
        state_name = str(stream.get("state", "") or "").strip()
        if not state_name:
            continue
        counts[state_name] = counts.get(state_name, 0) + 1
    return dict(sorted(counts.items()))


def _scrub_stream(state, engineer_cell, stream: dict) -> dict:
    payload = dict(stream or {})
    agent_id = str(payload.get("agent_id", "") or "").strip()
    if not agent_id:
        return payload
    if _agent_visible(state, engineer_cell, agent_id):
        return payload
    payload["agent_id"] = ""
    payload["agent_name"] = ""
    payload["agent_slug"] = ""
    if _hidden_agent_marker(state, engineer_cell):
        payload["agent_hidden"] = True
    return payload


def _agent_visible(state, engineer_cell, agent_id: str) -> bool:
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return False
    if not engineer_cell:
        return False
    return state.agent_is_visible_to_engineer(getattr(engineer_cell, "id", ""), agent_id)


def _resolve_engineer_cell(state, group: str, engineer_cell):
    if (
            engineer_cell
            and getattr(engineer_cell, "cell_type", "") == "agent"
            and str(getattr(engineer_cell, "kind", "") or "").strip() == "engineer"
            and str(getattr(engineer_cell, "group", "") or "").strip() == group
    ):
        return engineer_cell
    getter = getattr(state, "get_engineer_for_group", None)
    if callable(getter):
        candidate = getter(group)
    else:
        engineer_id = str(
            getattr(state.get_group_settings(group), "engineer_agent_id", "")
            or ""
        ).strip()
        candidate = state.agents.get(engineer_id)
    if (
            candidate
            and getattr(candidate, "cell_type", "") == "agent"
            and str(getattr(candidate, "kind", "") or "").strip() == "engineer"
            and str(getattr(candidate, "group", "") or "").strip() == group
    ):
        return candidate
    return None


def _hidden_agent_marker(state, engineer_cell) -> bool:
    return bool(
        engineer_cell
        and state.engineer_restricts_to_created_agents(getattr(engineer_cell, "group", ""))
    )


def _stream_title(stream: dict) -> str:
    for key in (
        "foreground_task_title",
        "latest_boundary_task_title",
        "branch",
        "stream_id",
    ):
        value = str(stream.get(key, "") or "").strip()
        if value:
            return value
    return "Stream"


def _stream_sort_key(stream: dict):
    return (
        0 if str(stream.get("state", "") or "").strip() == "awaiting_human_validation" else 1,
        str(stream.get("branch", "") or "").strip(),
        _stream_title(stream).lower(),
        str(stream.get("stream_id", "") or "").strip(),
    )


def _queued_follow_up_sort_key(item: dict):
    if item.get("source") == "stream_queue":
        return (
            0,
            _QUEUE_STATE_ORDER.get(str(item.get("queue_state", "") or "").strip(), 99),
            str(item.get("branch", "") or "").strip(),
            str(item.get("task_title", "") or "").strip().lower(),
            str(item.get("task_id", "") or "").strip(),
        )
    return (
        1,
        str(item.get("enqueued_at", "") or "").strip(),
        str(item.get("task_title", "") or "").strip().lower(),
        str(item.get("task_id", "") or "").strip(),
    )
