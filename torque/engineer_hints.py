"""Shared Engineer hint synthesis helpers."""

from __future__ import annotations

import time

from .state import (
    ARCHIVED_LANE,
    board_task_is_closed,
    normalize_worktree_merge_cleanup,
    task_is_engineer_message_followup,
)
from .task_health import (
    HEALTH_IDLE_RISK,
    HEALTH_STALLED,
    HEALTH_STALE_IN_PROGRESS,
)
from .worktree_streams import compute_worktree_streams, member_task_ids_for_stream

ENGINEER_HINT_RESEND_COOLDOWN_SECS = 30 * 60
ENGINEER_HINT_GROUP_CLEANUP_MIN = 2
ENGINEER_HINT_GROUP_READY_TO_MERGE_MIN = 2
ENGINEER_HINT_GROUP_IDLE_RISK_MIN = 2
ENGINEER_HINT_DISPATCH_SHAPE_WINDOW = 20
ENGINEER_HINT_DISPATCH_SHAPE_MIN_SAMPLE = 10
ENGINEER_HINT_DISPATCH_SHAPE_MIN_HINTABLE_SERIAL = 8
ENGINEER_HINT_DISPATCH_SHAPE_SERIAL_RATIO = 0.80
ENGINEER_HINT_DISPATCH_SHAPE_READY_TASK_MIN = 2

_MAX_PREVIEW_ITEMS = 3
_SEVERE_LONG_RUNNING_STATES = {
    HEALTH_STALLED,
    HEALTH_STALE_IN_PROGRESS,
}
_LONG_RUNNING_STATES = _SEVERE_LONG_RUNNING_STATES | {HEALTH_IDLE_RISK}


def compute_engineer_hints(state, group: str, *, engineer_id: str = "",
                         visibility_limit: int = 10) -> list[dict]:
    """Return current non-blocking actionable hints for one Engineer group."""
    group = str(group or "").strip()
    if not group:
        return []

    streams = compute_worktree_streams(
        state,
        group=group,
        visibility_limit=visibility_limit,
        include_orphaned=False,
    )
    task_to_stream_id = {}
    for stream in streams:
        stream_id = str(stream.get("stream_id", "") or "").strip()
        if not stream_id:
            continue
        for task_id in member_task_ids_for_stream(stream):
            if task_id:
                task_to_stream_id.setdefault(task_id, stream_id)

    hints = []
    hints.extend(_merged_cleanup_hints(state, group, engineer_id=engineer_id))
    hints.extend(
        _ready_to_merge_hints(
            state,
            group,
            streams=streams,
            engineer_id=engineer_id,
        )
    )
    hints.extend(
        _long_running_hints(
            state,
            group,
            engineer_id=engineer_id,
            task_to_stream_id=task_to_stream_id,
        )
    )
    hints.extend(_dispatch_shape_hints(state, group, engineer_id=engineer_id))
    hints.sort(
        key=lambda item: (
            -int(item.get("priority", 0) or 0),
            str(item.get("kind", "") or ""),
            str(item.get("fingerprint", "") or ""),
        )
    )
    return hints


def _dispatch_shape_hints(state, group: str, *, engineer_id: str) -> list[dict]:
    target_engineer_id = _dispatch_shape_engineer_id(
        state,
        group,
        engineer_id=engineer_id,
    )
    summarizer = getattr(state, "engineer_dispatch_shape_summary", None)
    if not target_engineer_id or not callable(summarizer):
        return []
    summary = summarizer(
        target_engineer_id,
        group=group,
        window=ENGINEER_HINT_DISPATCH_SHAPE_WINDOW,
    )
    total = _safe_int(summary.get("total"))
    if total < ENGINEER_HINT_DISPATCH_SHAPE_MIN_SAMPLE:
        return []
    hintable_serial = _safe_int(summary.get("hintable_serial"))
    if hintable_serial < ENGINEER_HINT_DISPATCH_SHAPE_MIN_HINTABLE_SERIAL:
        return []
    if total <= 0 or (
            hintable_serial / total
            < ENGINEER_HINT_DISPATCH_SHAPE_SERIAL_RATIO):
        return []

    ready_tasks = _dispatch_shape_ready_tasks(
        state,
        group,
        engineer_id=target_engineer_id,
    )
    if len(ready_tasks) < ENGINEER_HINT_DISPATCH_SHAPE_READY_TASK_MIN:
        return []

    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    serial_count = _safe_int(counts.get("serial"))
    batch_count = _safe_int(counts.get("batch"))
    warm_cluster_count = _safe_int(counts.get("warm_cluster"))
    window = _safe_int(
        summary.get("window"),
        default=ENGINEER_HINT_DISPATCH_SHAPE_WINDOW,
    ) or ENGINEER_HINT_DISPATCH_SHAPE_WINDOW
    fingerprint_target = target_engineer_id or group
    return [{
        "kind": "dispatch_shape_distribution",
        "priority": 25,
        "fingerprint": f"dispatch_shape:serial-heavy:{fingerprint_target}",
        "message": (
            f"Dispatch shape: last {window} direct dispatches were "
            f"{serial_count} serial / {batch_count} batch / "
            f"{warm_cluster_count} warm cluster. For the next independent "
            "wave, consider engineer_batch_dispatch."
        ),
        "agent_ids": [],
        "task_ids": [
            str(getattr(task, "id", "") or "").strip()
            for task in ready_tasks[:ENGINEER_HINT_DISPATCH_SHAPE_READY_TASK_MIN]
        ],
        "stream_ids": [],
    }]


def _dispatch_shape_engineer_id(state, group: str, *, engineer_id: str) -> str:
    engineer_id = str(engineer_id or "").strip()
    if engineer_id:
        return engineer_id
    getter = getattr(state, "get_engineer_for_group", None)
    engineer = getter(group) if callable(getter) else None
    return str(getattr(engineer, "id", "") or "").strip()


def _dispatch_shape_ready_tasks(state, group: str, *,
                                engineer_id: str) -> list:
    lane_order = {
        lane: idx
        for idx, lane in enumerate(getattr(state, "board_lanes", []) or [])
    }
    ready_tasks = []
    board_deps_met = getattr(state, "board_deps_met", None)
    for task in state.board_tasks.values():
        if str(getattr(task, "group", "") or "").strip() != group:
            continue
        lane = str(getattr(task, "lane", "") or "").strip()
        if lane in {"In Progress", "Done", ARCHIVED_LANE}:
            continue
        if board_task_is_closed(task):
            continue
        if str(getattr(task, "agent_id", "") or "").strip():
            continue
        assigned_engineer_id = str(
            getattr(task, "assigned_engineer_id", "") or ""
        ).strip()
        if engineer_id and assigned_engineer_id \
                and assigned_engineer_id != engineer_id:
            continue
        labels = set(getattr(task, "labels", []) or [])
        if "torque:human" in labels:
            continue
        if task_is_engineer_message_followup(task):
            continue
        if callable(board_deps_met) and not board_deps_met(task):
            continue
        ready_tasks.append(task)
    ready_tasks.sort(
        key=lambda task: (
            lane_order.get(str(getattr(task, "lane", "") or ""), len(lane_order)),
            int(getattr(task, "position", 0) or 0),
            _task_label(task).lower(),
            str(getattr(task, "id", "") or ""),
        )
    )
    return ready_tasks


def _merged_cleanup_hints(state, group: str, *, engineer_id: str) -> list[dict]:
    cleanup_policy = _group_worktree_merge_cleanup_policy(state, group)
    retained_by_policy = cleanup_policy == "keep"
    candidates = []
    for cell in state.iter_active_agents():
        if not _is_visible_actionable_agent(
                state, cell, group=group, engineer_id=engineer_id):
            continue
        if not getattr(cell, "worktree_path", ""):
            continue
        if not bool(getattr(cell, "worktree_merged", False)):
            continue
        if state.agent_is_busy(cell.id):
            continue
        if getattr(cell, "needs_attention", False):
            continue
        if str(getattr(cell, "status", "") or "").strip() == "error":
            continue
        candidates.append(cell)

    if len(candidates) < ENGINEER_HINT_GROUP_CLEANUP_MIN:
        return []

    candidates.sort(key=lambda cell: (_agent_label(cell).lower(), cell.id))
    names = [_agent_label(cell) for cell in candidates]
    agent_ids = [cell.id for cell in candidates]
    kind = (
        "merged_retained_by_policy"
        if retained_by_policy else "merged_cleanup"
    )
    fingerprint = kind + ":" + ",".join(agent_ids)
    if _hint_is_snoozed(state, group, fingerprint):
        return []
    if retained_by_policy:
        return [{
            "kind": kind,
            "priority": 15,
            "attention": False,
            "snoozeable": True,
            "policy": cleanup_policy,
            "fingerprint": fingerprint,
            "message": (
                f"{len(candidates)} idle merged agents are retained by the "
                f"group cleanup policy: {_preview_list(names)}"
            ),
            "agent_ids": agent_ids,
            "task_ids": [],
            "stream_ids": [],
        }]
    return [{
        "kind": kind,
        "priority": 90,
        "attention": True,
        "snoozeable": True,
        "policy": cleanup_policy,
        "fingerprint": fingerprint,
        "message": (
            f"{len(candidates)} idle agents have merged branches ready for "
            f"cleanup: {_preview_list(names)}"
        ),
        "agent_ids": agent_ids,
        "task_ids": [],
        "stream_ids": [],
    }]


def _group_worktree_merge_cleanup_policy(state, group: str) -> str:
    getter = getattr(state, "get_group_settings", None)
    settings = getter(group) if callable(getter) else None
    return normalize_worktree_merge_cleanup(
        getattr(settings, "worktree_merge_cleanup", "keep")
    )


def _hint_is_snoozed(state, group: str, fingerprint: str) -> bool:
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return False
    getter = getattr(state, "get_group_settings", None)
    settings = getter(group) if callable(getter) else None
    snoozes = getattr(settings, "engineer_hint_snoozes", {}) or {}
    if not isinstance(snoozes, dict):
        return False
    try:
        expires_at = float(snoozes.get(fingerprint, 0) or 0)
    except (TypeError, ValueError):
        return False
    return expires_at > time.time()


def _ready_to_merge_hints(state, group: str, *, streams: list[dict],
                          engineer_id: str) -> list[dict]:
    candidates = []
    for stream in streams:
        if str(stream.get("state", "") or "").strip() != "ready_to_merge":
            continue
        agent_id = str(stream.get("agent_id", "") or "").strip()
        if not agent_id:
            continue
        agent = state.agents.get(agent_id)
        if not _is_visible_actionable_agent(
                state, agent, group=group, engineer_id=engineer_id):
            continue
        if state.agent_is_busy(agent_id):
            continue
        candidates.append((stream, agent))

    if len(candidates) < ENGINEER_HINT_GROUP_READY_TO_MERGE_MIN:
        return []

    candidates.sort(
        key=lambda item: (
            str((item[0] or {}).get("branch", "") or ""),
            _agent_label(item[1]).lower(),
            str((item[0] or {}).get("stream_id", "") or ""),
        )
    )
    labels = [_stream_label(stream, agent) for stream, agent in candidates]
    agent_ids = [agent.id for _stream, agent in candidates]
    stream_ids = [
        str(stream.get("stream_id", "") or "").strip()
        for stream, _agent in candidates
        if str(stream.get("stream_id", "") or "").strip()
    ]
    return [{
        "kind": "ready_to_merge",
        "priority": 80,
        "fingerprint": "ready_to_merge:" + ",".join(stream_ids),
        "message": (
            f"{len(candidates)} idle streams are ready to merge: "
            f"{_preview_list(labels)}"
        ),
        "agent_ids": agent_ids,
        "task_ids": [],
        "stream_ids": stream_ids,
    }]


def _long_running_hints(state, group: str, *, engineer_id: str,
                        task_to_stream_id: dict[str, str]) -> list[dict]:
    selected = {}
    for task in state.board_tasks.values():
        if getattr(task, "group", "") != group or board_task_is_closed(task):
            continue
        health_state = str(getattr(task, "health_state", "") or "").strip()
        if health_state not in _LONG_RUNNING_STATES:
            continue
        details = task.health_details if isinstance(task.health_details, dict) else {}
        source_task_id = str(details.get("source_task_id", "") or task.id).strip()
        candidate = selected.get(source_task_id)
        if candidate is None or _prefer_long_running_candidate(task, candidate):
            selected[source_task_id] = task

    severe_hints = []
    idle_risk_candidates = []
    for task in selected.values():
        details = task.health_details if isinstance(task.health_details, dict) else {}
        source_task_id = str(details.get("source_task_id", "") or task.id).strip()
        source_task = state.board_tasks.get(source_task_id) or task
        if board_task_is_closed(source_task):
            source_task = task
        agent_id = (
            str(getattr(source_task, "agent_id", "") or "").strip()
            or str(getattr(task, "agent_id", "") or "").strip()
        )
        if not agent_id:
            continue
        agent = state.agents.get(agent_id)
        if not _is_visible_actionable_agent(
                state, agent, group=group, engineer_id=engineer_id):
            continue
        if state.agent_pending_engineer_reply_tasks(agent_id):
            continue

        health_state = str(getattr(task, "health_state", "") or "").strip()
        if health_state in _SEVERE_LONG_RUNNING_STATES:
            severe_hints.append(
                _severe_long_running_hint(
                    task,
                    source_task=source_task,
                    agent=agent,
                    stream_id=task_to_stream_id.get(source_task.id, ""),
                )
            )
        elif health_state == HEALTH_IDLE_RISK:
            idle_risk_candidates.append((task, source_task, agent))

    hints = [hint for hint in severe_hints if hint]
    if len(idle_risk_candidates) >= ENGINEER_HINT_GROUP_IDLE_RISK_MIN:
        idle_risk_candidates.sort(
            key=lambda item: (
                _agent_label(item[2]).lower(),
                _task_label(item[1]).lower(),
                getattr(item[1], "id", ""),
            )
        )
        preview = [
            f"{_agent_label(agent)}/{_task_label(source_task)}"
            for _task, source_task, agent in idle_risk_candidates
        ]
        task_ids = [source_task.id for _task, source_task, _agent in idle_risk_candidates]
        agent_ids = [agent.id for _task, _source_task, agent in idle_risk_candidates]
        stream_ids = [
            task_to_stream_id.get(source_task.id, "")
            for _task, source_task, _agent in idle_risk_candidates
            if task_to_stream_id.get(source_task.id, "")
        ]
        hints.append({
            "kind": "idle_risk_followup",
            "priority": 50,
            "fingerprint": "idle_risk:" + ",".join(sorted(task_ids)),
            "message": (
                f"{len(idle_risk_candidates)} active tasks are at idle risk "
                f"without Engineer follow-up: {_preview_list(preview)}"
            ),
            "agent_ids": agent_ids,
            "task_ids": task_ids,
            "stream_ids": stream_ids,
        })
    return hints


def _severe_long_running_hint(task, *, source_task, agent, stream_id: str) -> dict:
    health_state = str(getattr(task, "health_state", "") or "").strip()
    details = task.health_details if isinstance(task.health_details, dict) else {}
    silence_secs = details.get("silence_secs")
    duration = _format_duration(silence_secs) if isinstance(silence_secs, int) else ""
    agent_name = _agent_label(agent)
    task_name = _task_label(source_task)
    if health_state == HEALTH_STALLED:
        if duration:
            message = (
                f"{agent_name} has been stalled on {task_name} for {duration} "
                "without Engineer follow-up"
            )
        else:
            message = (
                f"{agent_name} has stalled on {task_name} without Engineer "
                "follow-up"
            )
        priority = 70
    else:
        stale_reason = _stale_reason(details)
        if stale_reason:
            message = (
                f"{agent_name} looks idle on {task_name} ({stale_reason}) "
                "without Engineer follow-up"
            )
        else:
            message = (
                f"{agent_name} looks idle on {task_name} without Engineer "
                "follow-up"
            )
        priority = 60
    return {
        "kind": "long_running_followup",
        "priority": priority,
        "fingerprint": (
            f"{health_state}:{getattr(source_task, 'id', '')}:{getattr(agent, 'id', '')}"
        ),
        "message": message,
        "agent_ids": [getattr(agent, "id", "")],
        "task_ids": [getattr(source_task, "id", "")],
        "stream_ids": [stream_id] if stream_id else [],
    }


def _prefer_long_running_candidate(candidate, incumbent) -> bool:
    candidate_details = (
        candidate.health_details if isinstance(candidate.health_details, dict)
        else {}
    )
    incumbent_details = (
        incumbent.health_details if isinstance(incumbent.health_details, dict)
        else {}
    )
    candidate_aggregate = bool(candidate_details.get("aggregate"))
    incumbent_aggregate = bool(incumbent_details.get("aggregate"))
    candidate_silence = int(candidate_details.get("silence_secs", 0) or 0)
    incumbent_silence = int(incumbent_details.get("silence_secs", 0) or 0)
    return (
        candidate_aggregate < incumbent_aggregate
        or (
            candidate_aggregate == incumbent_aggregate
            and candidate_silence > incumbent_silence
        )
        or (
            candidate_aggregate == incumbent_aggregate
            and candidate_silence == incumbent_silence
            and getattr(candidate, "id", "") < getattr(incumbent, "id", "")
        )
    )


def _is_visible_actionable_agent(state, cell, *, group: str, engineer_id: str) -> bool:
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return False
    if getattr(cell, "group", "") != group:
        return False
    if engineer_id and getattr(cell, "id", "") == engineer_id:
        return False
    if engineer_id and not state.agent_is_visible_to_engineer(engineer_id, cell.id):
        return False
    return True


def _agent_label(cell) -> str:
    return str(
        getattr(cell, "slug", "")
        or getattr(cell, "name", "")
        or getattr(cell, "id", "")
        or "agent"
    ).strip()


def _task_label(task, *, limit: int = 48) -> str:
    title = str(getattr(task, "task", "") or getattr(task, "id", "") or "task").strip()
    if len(title) <= limit:
        return title
    return title[:limit - 1].rstrip() + "…"


def _stream_label(stream: dict, agent, *, limit: int = 48) -> str:
    branch = str(stream.get("branch", "") or "").strip()
    label = branch or _agent_label(agent)
    if len(label) <= limit:
        return label
    return label[:limit - 1].rstrip() + "…"


def _preview_list(items: list[str]) -> str:
    preview = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not preview:
        return "none"
    shown = preview[:_MAX_PREVIEW_ITEMS]
    extra = len(preview) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text += f" (+{extra} more)"
    return text


def _stale_reason(details: dict) -> str:
    reasons = set(details.get("reasons", []) or [])
    parts = []
    if "clean_checkpointed_branch" in reasons:
        parts.append("clean checkpointed branch")
    elif "checkpointed_worktree" in reasons:
        parts.append("checkpointed worktree")
    if "branch_ahead_of_base" in reasons:
        parts.append("branch ahead of base")
    return ", ".join(parts)


def _format_duration(seconds: int) -> str:
    minutes = max(1, int(seconds) // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if rem_minutes:
        return f"{hours}h {rem_minutes}m"
    return f"{hours}h"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
