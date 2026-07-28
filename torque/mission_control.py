"""Pure read-only Mission Control summary derivation.

Wave 1 intentionally derives a console-shaped read model from existing
in-memory primitives only.  It does not persist state, mutate Board tasks,
modify agents, or introduce a Mission-Control-specific state machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Iterable

from .state import ARCHIVED_LANE, board_task_is_closed
from .task_health import (
    HEALTH_BLOCKED,
    HEALTH_IDLE_RISK,
    HEALTH_STALE_IN_PROGRESS,
    HEALTH_STALLED,
    HEALTH_THRASHING,
    IMPLEMENTED_NO_REVIEW_BOUNDARY_REASON,
)
from .worktree_streams import compute_worktree_streams

VERSION = 1
DEFAULT_LIMIT_PER_SECTION = 20
DEFAULT_RECENT_COMPLETED_SECONDS = 7 * 24 * 60 * 60
_FIXED_SECTION_KEYS = (
    "needs_operator_now",
    "at_risk_watchlist",
    "in_flight",
    "recently_completed",
)
_HEALTH_SEVERITY = {
    HEALTH_BLOCKED: 90,
    HEALTH_STALE_IN_PROGRESS: 80,
    HEALTH_STALLED: 75,
    HEALTH_THRASHING: 65,
    HEALTH_IDLE_RISK: 50,
}
_OPEN_REVIEW_BOUNDARY_STATUSES = {"open", "awaiting-review", "awaiting_review"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_limit(value: Any) -> int:
    return max(1, min(_safe_int(value, DEFAULT_LIMIT_PER_SECTION), 100))


def _now_iso(now_ts: float) -> str:
    return datetime.fromtimestamp(float(now_ts), tz=timezone.utc).isoformat()


def _parse_ts(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _task_updated_ts(task: Any) -> float:
    return max(
        _parse_ts(getattr(task, "updated_at", "")),
        _parse_ts(getattr(task, "lane_entered_at", "")),
        _parse_ts(getattr(task, "created_at", "")),
    )


def _stream_updated_ts(stream: dict) -> float:
    return _parse_ts((stream or {}).get("last_activity_at"))


def _state_tasks(state: Any, group: str) -> list[Any]:
    if group and hasattr(state, "tasks_in_group"):
        return list(state.tasks_in_group(group))
    return list(getattr(state, "board_tasks", {}).values())


def _state_agents(state: Any, group: str) -> list[Any]:
    if hasattr(state, "iter_active_agents"):
        agents = list(state.iter_active_agents())
    else:
        agents = list(getattr(state, "agents", {}).values())
    if group:
        agents = [agent for agent in agents if str(getattr(agent, "group", "") or "") == group]
    return agents


def _agent_ref(state: Any, agent_id: str) -> dict:
    agent = getattr(state, "agents", {}).get(str(agent_id or "").strip())
    if not agent:
        return {}
    return {
        "agent_id": getattr(agent, "id", "") or "",
        "agent_name": getattr(agent, "name", "") or getattr(agent, "id", "") or "",
        "kind": getattr(agent, "kind", "") or "",
        "owner_engineer_id": getattr(agent, "owner_engineer_id", "") or "",
    }


def _task_owner(state: Any, task: Any) -> dict:
    owner = _agent_ref(state, getattr(task, "agent_id", "") or getattr(task, "reply_agent_id", "") or "")
    assigned = str(getattr(task, "assigned_engineer_id", "") or "").strip()
    if assigned:
        owner["assigned_engineer_id"] = assigned
    assigned_architect = str(getattr(task, "assigned_architect_id", "") or "").strip()
    if assigned_architect:
        owner["assigned_architect_id"] = assigned_architect
    created_by_engineer = str(getattr(task, "created_by_engineer_id", "") or "").strip()
    if created_by_engineer:
        owner["created_by_engineer_id"] = created_by_engineer
    created_by_architect = str(getattr(task, "created_by_architect_id", "") or "").strip()
    if created_by_architect:
        owner["created_by_architect_id"] = created_by_architect
    return owner


def _compact_source_ref(kind: str, source_id: str) -> dict:
    return {"kind": kind, "id": source_id}


def _task_deep_link(task: Any) -> dict:
    return {
        "surface": "board_task",
        "kind": "inspect",
        "task_id": getattr(task, "id", "") or "",
    }


def _agent_deep_link(agent: Any) -> dict:
    return {
        "surface": "agent",
        "kind": "inspect",
        "agent_id": getattr(agent, "id", "") or "",
    }


def _stream_deep_link(stream: dict) -> dict:
    return {
        "surface": "stream",
        "kind": "inspect",
        "stream_id": str((stream or {}).get("stream_id", "") or ""),
    }


def _task_card(
    state: Any,
    task: Any,
    *,
    card_id: str,
    kind: str,
    gate: str,
    reason: str,
    recommended_next_action: str,
    evidence_chips: Iterable[str] = (),
    caveat_chips: Iterable[str] = (),
    severity: str = "medium",
    priority: int = 50,
) -> dict:
    task_id = getattr(task, "id", "") or ""
    return {
        "id": card_id,
        "ref": {"kind": "task", "id": task_id},
        "kind": kind,
        "title": getattr(task, "task", "") or task_id,
        "group": getattr(task, "group", "") or "",
        "owner": _task_owner(state, task),
        "task_ids": [task_id] if task_id else [],
        "primary_task_id": task_id,
        "gate": gate,
        "reason": reason,
        "recommended_next_action": recommended_next_action,
        "evidence_chips": [chip for chip in evidence_chips if chip],
        "caveat_chips": [chip for chip in caveat_chips if chip],
        "severity": severity,
        "priority": int(priority),
        "timestamps": {
            "created_at": getattr(task, "created_at", "") or "",
            "updated_at": getattr(task, "updated_at", "") or "",
            "lane_entered_at": getattr(task, "lane_entered_at", "") or "",
        },
        "deep_links": [_task_deep_link(task)],
        "source_refs": [_compact_source_ref("task", task_id)] if task_id else [],
    }


def _stream_card(
    stream: dict,
    *,
    gate: str,
    reason: str,
    recommended_next_action: str,
    evidence_chips: Iterable[str] = (),
    caveat_chips: Iterable[str] = (),
    severity: str = "medium",
    priority: int = 50,
) -> dict:
    stream_id = str((stream or {}).get("stream_id", "") or "")
    task_ids = [
        str(task_id or "")
        for task_id in (stream.get("product_task_ids", []) or [])
        if str(task_id or "")
    ]
    if not task_ids:
        task_ids = [
            str(task_id or "")
            for task_id in (stream.get("workflow_task_ids", []) or [])
            if str(task_id or "")
        ]
    primary_task_id = str(stream.get("foreground_task_id") or (task_ids[0] if task_ids else ""))
    title = str(stream.get("foreground_task_title") or stream.get("branch") or stream_id)
    return {
        "id": f"mc:stream:{stream_id}:{gate}",
        "ref": {"kind": "stream", "id": stream_id},
        "kind": "stream",
        "title": title,
        "group": str(stream.get("group", "") or ""),
        "owner": {
            key: stream.get(key, "")
            for key in ("agent_id", "agent_name", "agent_slug")
            if stream.get(key)
        },
        "task_ids": task_ids,
        "primary_task_id": primary_task_id,
        "stream_id": stream_id,
        "gate": gate,
        "reason": reason,
        "recommended_next_action": recommended_next_action,
        "evidence_chips": [chip for chip in evidence_chips if chip],
        "caveat_chips": [chip for chip in caveat_chips if chip],
        "severity": severity,
        "priority": int(priority),
        "timestamps": {
            "last_activity_at": str(stream.get("last_activity_at", "") or ""),
            "latest_boundary_recorded_at": str(stream.get("latest_boundary_recorded_at", "") or ""),
        },
        "deep_links": [_stream_deep_link(stream)],
        "source_refs": [
            _compact_source_ref("stream", stream_id),
            *[_compact_source_ref("task", task_id) for task_id in task_ids],
        ],
    }


def _deploy_card(group: str, deploy_state: dict, *, now_ts: float) -> dict | None:
    pending = deploy_state.get("pending_deploy") if isinstance(deploy_state, dict) else {}
    if not isinstance(pending, dict):
        return None
    count = _safe_int(pending.get("count"), 0)
    if count <= 0:
        return None
    task_ids = [str(task_id or "") for task_id in (pending.get("torque_task_ids") or []) if str(task_id or "")]
    return {
        "id": f"mc:deploy:{group or 'all'}:pending",
        "ref": {"kind": "deploy_state", "id": group or "all"},
        "kind": "deploy_state",
        "title": "Deploy or relaunch pending",
        "group": group,
        "owner": {},
        "task_ids": task_ids,
        "primary_task_id": task_ids[0] if task_ids else "",
        "gate": "deploy_or_relaunch_pending",
        "reason": f"{count} commit(s) are present after the daemon boot commit.",
        "recommended_next_action": "record_deploy_or_relaunch",
        "evidence_chips": [f"pending_commits:{count}"],
        "caveat_chips": ["read_only_git_derived"],
        "severity": "high",
        "priority": 93,
        "timestamps": {
            "generated_at": _now_iso(now_ts),
            "boot_timestamp": deploy_state.get("boot_timestamp", 0),
        },
        "deep_links": [{"surface": "deploy_state", "kind": "inspect", "group": group}],
        "source_refs": [_compact_source_ref("deploy_state", group or "all")],
    }


def _engineer_question_cards(state: Any, group: str) -> list[dict]:
    groups = [group] if group else sorted(set(
        list(getattr(state, "groups", {}).keys())
        + list(getattr(state, "engineer_settings", {}).keys())
    ))
    cards = []
    for group_name in groups:
        if not group_name:
            continue
        try:
            settings = state.get_engineer_settings(group_name)
        except Exception:
            settings = getattr(state, "engineer_settings", {}).get(group_name)
        if not settings:
            continue
        question = str(getattr(settings, "pending_question", "") or "").strip()
        if not question:
            continue
        actor_id = str(
            getattr(settings, "pending_question_actor_id", "") or ""
        ).strip()
        agent = getattr(state, "agents", {}).get(actor_id)
        title = getattr(agent, "name", "") or actor_id or f"{group_name} engineer"
        set_at = _safe_float(getattr(settings, "pending_question_set_at", 0.0), 0.0)
        cards.append({
            "id": f"mc:engineer:{actor_id or group_name}:question",
            "ref": {"kind": "engineer_question", "id": actor_id or group_name},
            "kind": "engineer_question",
            "title": title,
            "group": group_name,
            "owner": _agent_ref(state, actor_id) if actor_id else {},
            "task_ids": [],
            "primary_task_id": "",
            "gate": "engineer_question",
            "reason": question,
            "recommended_next_action": "answer_engineer_question",
            "evidence_chips": ["engineer_pending_question"],
            "caveat_chips": [],
            "severity": "high",
            "priority": 94,
            "timestamps": {
                "set_at": set_at,
            },
            "deep_links": (
                [_agent_deep_link(agent)] if agent else [{
                    "surface": "engineer_settings",
                    "kind": "inspect",
                    "group": group_name,
                }]
            ),
            "source_refs": [
                _compact_source_ref(
                    "engineer_settings",
                    f"{group_name}:{actor_id}" if actor_id else group_name,
                )
            ],
        })
    return cards


def _verification_cards(state: Any, task: Any) -> list[dict]:
    v_state = str(getattr(task, "verification_state", "") or "").strip()
    summary = getattr(task, "verification_summary", {}) or {}
    if not isinstance(summary, dict):
        summary = {}
    notes = str(getattr(task, "verification_notes", "") or "").strip()
    if v_state == "failed":
        return [_task_card(
            state,
            task,
            card_id=f"mc:task:{task.id}:failed_verification",
            kind="verification",
            gate="failed_verification",
            reason=notes or "Verification failed and needs investigation.",
            recommended_next_action="investigate_failed_verification",
            evidence_chips=["verification:failed"],
            severity="critical",
            priority=96,
        )]
    live_smoke_pending = bool(summary.get("live_smoke_pending"))
    human_validation = str(summary.get("human_validation_pending", "") or "").strip()
    deploy_needed = bool(summary.get("deploy_needed"))
    if live_smoke_pending or human_validation:
        action = "perform_live_smoke" if live_smoke_pending else "record_or_complete_verification"
        return [_task_card(
            state,
            task,
            card_id=f"mc:task:{task.id}:manual_validation",
            kind="verification",
            gate="manual_validation_pending",
            reason=human_validation or notes or "Manual/live-smoke validation is pending.",
            recommended_next_action=action,
            evidence_chips=[
                f"verification:{v_state}" if v_state else "verification:pending",
                "live_smoke_pending" if live_smoke_pending else "",
            ],
            caveat_chips=["operator_validation_required"],
            severity="high",
            priority=88,
        )]
    if v_state in {"pending", "attempted"} or deploy_needed:
        return [_task_card(
            state,
            task,
            card_id=f"mc:task:{task.id}:verification_pending",
            kind="verification",
            gate="verification_pending",
            reason=notes or "Verification needs to be recorded or completed.",
            recommended_next_action="record_or_complete_verification",
            evidence_chips=[f"verification:{v_state}" if v_state else "deploy_needed"],
            severity="medium",
            priority=70,
        )]
    return []


def _task_needs_review(task: Any) -> bool:
    if str(getattr(task, "action_name", "") or "").strip().lower() == "feature/review":
        return True
    details = getattr(task, "health_details", {}) or {}
    if isinstance(details, dict):
        reasons = [str(reason or "") for reason in (details.get("reasons") or [])]
        if IMPLEMENTED_NO_REVIEW_BOUNDARY_REASON in reasons:
            return True
    boundary = getattr(task, "worktree_boundary", {}) or {}
    if isinstance(boundary, dict):
        status = str(boundary.get("status", "") or "").strip().lower()
        if status in _OPEN_REVIEW_BOUNDARY_STATUSES:
            return True
    return False


def _board_sync_error_card(state: Any, task: Any) -> dict | None:
    board_sync = getattr(task, "board_sync", {}) or {}
    if not isinstance(board_sync, dict):
        return None
    if str(board_sync.get("sync_state", "") or "").strip() != "error":
        return None
    return _task_card(
        state,
        task,
        card_id=f"mc:task:{task.id}:sync_error",
        kind="board_sync",
        gate="board_sync_error",
        reason=str(board_sync.get("last_error", "") or "Board sync is in error state."),
        recommended_next_action="inspect_sync_error",
        evidence_chips=["sync_state:error"],
        severity="medium",
        priority=63,
    )


def _health_watch_card(state: Any, task: Any) -> dict | None:
    health = str(getattr(task, "health_state", "") or "healthy").strip()
    if health in {"", "healthy"}:
        return None
    priority = _HEALTH_SEVERITY.get(health, 45)
    return _task_card(
        state,
        task,
        card_id=f"mc:task:{task.id}:health:{health}",
        kind="task_health",
        gate="task_health_risk",
        reason=f"Task health is {health}.",
        recommended_next_action="watch_idle_risk",
        evidence_chips=[f"health:{health}"],
        severity="high" if priority >= 75 else "medium",
        priority=priority,
    )


def _sort_cards(cards: Iterable[dict]) -> list[dict]:
    return sorted(
        cards,
        key=lambda card: (
            -int(card.get("priority", 0) or 0),
            -max(_parse_ts(value) for value in (card.get("timestamps") or {}).values()) if card.get("timestamps") else 0,
            str(card.get("id", "")),
        ),
    )


def _limit_section(cards: list[dict], limit: int) -> dict:
    return {
        "count": len(cards),
        "items": cards[:limit],
        "truncated": len(cards) > limit,
    }


def _build_scope(group: str) -> dict:
    return {
        "kind": "group" if group else "all_groups",
        "group": group,
        "visibility": "explicit_group_board_visibility",
        "peer_ack_details": "omitted_without_caller_scoped_context",
        "pending_hires": "omitted_without_architect_caller_context",
    }


def _source_ok(count: int) -> dict:
    return {"state": "ok", "count": int(count)}


def _source_error(message: str) -> dict:
    return {"state": "error", "error": str(message or "unknown error")}


def build_mission_control_summary(
    state: Any,
    *,
    group: str = "",
    limit_per_section: int = DEFAULT_LIMIT_PER_SECTION,
    now_ts: float | None = None,
    include_recent_completed: bool = True,
    deploy_state: dict | None = None,
    recent_completed_seconds: int = DEFAULT_RECENT_COMPLETED_SECONDS,
    streams: list[dict] | None = None,
    source_errors: dict | None = None,
) -> dict:
    """Build the Wave 1 Mission Control summary without side effects."""
    group = str(group or "")
    limit = _clamp_limit(limit_per_section)
    if now_ts is None:
        now_ts = time.time()
    recent_window = max(0, _safe_int(recent_completed_seconds, DEFAULT_RECENT_COMPLETED_SECONDS))
    tasks = _state_tasks(state, group)
    agents = _state_agents(state, group)
    source_freshness = {
        "tasks": _source_ok(len(tasks)),
        "agents": _source_ok(len(agents)),
    }
    if source_errors:
        for key, message in source_errors.items():
            if message:
                source_freshness[str(key)] = _source_error(str(message))

    if streams is None:
        try:
            streams = compute_worktree_streams(
                state,
                group=group,
                visibility_limit=limit,
                include_orphaned=False,
            )
            source_freshness.setdefault("streams", _source_ok(len(streams)))
        except Exception as exc:  # isolate derived stream failures
            streams = []
            source_freshness["streams"] = _source_error(str(exc) or exc.__class__.__name__)
    else:
        source_freshness.setdefault("streams", _source_ok(len(streams)))

    if deploy_state is not None:
        if isinstance(deploy_state, dict) and deploy_state.get("error"):
            source_freshness["deploy_state"] = _source_error(str(deploy_state.get("error") or ""))
        else:
            source_freshness["deploy_state"] = {"state": "ok"}
    else:
        source_freshness["deploy_state"] = {"state": "not_requested"}

    open_tasks = [
        task for task in tasks
        if getattr(task, "lane", "") != ARCHIVED_LANE
        and not board_task_is_closed(task)
    ]
    closed_tasks = [
        task for task in tasks
        if getattr(task, "lane", "") == "Done"
    ]

    needs_operator_now: list[dict] = []
    at_risk_watchlist: list[dict] = []
    in_flight: list[dict] = []
    recently_completed: list[dict] = []

    deploy_card = _deploy_card(group, deploy_state or {}, now_ts=float(now_ts)) if deploy_state else None
    if deploy_card:
        needs_operator_now.append(deploy_card)
    needs_operator_now.extend(_engineer_question_cards(state, group))

    seen_task_ids: set[str] = set()
    for task in open_tasks:
        labels = set(getattr(task, "labels", []) or [])
        if "torque:human" in labels:
            needs_operator_now.append(_task_card(
                state,
                task,
                card_id=f"mc:task:{task.id}:ask",
                kind="ask",
                gate="human_ask",
                reason="A worker/engineer ask is awaiting operator input.",
                recommended_next_action="answer_ask",
                evidence_chips=["label:torque:human"],
                severity="high",
                priority=95,
            ))
            seen_task_ids.add(task.id)
        for card in _verification_cards(state, task):
            needs_operator_now.append(card)
            seen_task_ids.update(card.get("task_ids", []))
        if _task_needs_review(task):
            needs_operator_now.append(_task_card(
                state,
                task,
                card_id=f"mc:task:{task.id}:review_needed",
                kind="review",
                gate="review_needed",
                reason="An explicit review task or review boundary is open.",
                recommended_next_action="run_review",
                evidence_chips=["review_boundary_or_task"],
                severity="high",
                priority=84,
            ))
            seen_task_ids.add(task.id)
        sync_card = _board_sync_error_card(state, task)
        if sync_card:
            at_risk_watchlist.append(sync_card)
            seen_task_ids.add(task.id)
        health_card = _health_watch_card(state, task)
        if health_card:
            at_risk_watchlist.append(health_card)
            seen_task_ids.add(task.id)

    for stream in streams or []:
        state_name = str((stream or {}).get("state", "") or "").strip()
        gate_reason = str((stream or {}).get("gate_reason", "") or "").strip()
        if state_name == "ready_to_merge":
            needs_operator_now.append(_stream_card(
                stream,
                gate="ready_to_merge",
                reason=gate_reason or "Stream is ready to merge.",
                recommended_next_action="merge_ready_stream",
                evidence_chips=["stream:ready_to_merge"],
                severity="high",
                priority=92,
            ))
        elif state_name == "merge_readiness_unknown":
            at_risk_watchlist.append(_stream_card(
                stream,
                gate="merge_readiness_unknown",
                reason=gate_reason or (
                    "Current-base merge readiness could not be determined."
                ),
                recommended_next_action="check_merge_readiness",
                evidence_chips=["stream:merge_readiness_unknown"],
                severity="high",
                priority=90,
            ))
        elif state_name == "awaiting_human_validation":
            needs_operator_now.append(_stream_card(
                stream,
                gate="manual_validation_pending",
                reason=gate_reason or "Stream is awaiting human/live-smoke validation.",
                recommended_next_action="perform_live_smoke",
                evidence_chips=["stream:awaiting_human_validation"],
                caveat_chips=["operator_validation_required"],
                severity="high",
                priority=86,
            ))
        elif state_name == "merge_conflict":
            at_risk_watchlist.append(_stream_card(
                stream,
                gate="merge_conflict",
                reason=gate_reason or "Stream has a merge conflict.",
                recommended_next_action="resolve_merge_conflict",
                evidence_chips=["stream:merge_conflict"],
                severity="high",
                priority=82,
            ))
        elif state_name == "fixing_blockers":
            stale = ((stream or {}).get("merge_readiness", {}) or {}).get(
                "stale_base", {}
            )
            if isinstance(stale, dict) and stale.get("merge_conflict"):
                at_risk_watchlist.append(_stream_card(
                    stream,
                    gate="stale_base_conflict",
                    reason=gate_reason or (
                        "The base branch conflicts with this stream."
                    ),
                    recommended_next_action="rebase_stale_base",
                    evidence_chips=["stream:stale_base_conflict"],
                    severity="high",
                    priority=84,
                ))
                continue
            at_risk_watchlist.append(_stream_card(
                stream,
                gate="review_blockers",
                reason=gate_reason or "Review blockers are being fixed.",
                recommended_next_action="fix_review_blockers",
                evidence_chips=["stream:fixing_blockers"],
                severity="high",
                priority=78,
            ))
        else:
            stale = ((stream or {}).get("merge_readiness", {}) or {}).get("stale_base", {})
            if bool((stream or {}).get("branch_advanced")) or (isinstance(stale, dict) and stale.get("stale")):
                at_risk_watchlist.append(_stream_card(
                    stream,
                    gate="stale_base",
                    reason=gate_reason or "Stream needs a stale-base/review-boundary check.",
                    recommended_next_action="rebase_stale_base",
                    evidence_chips=["stream:stale_base"],
                    severity="medium",
                    priority=68,
                ))

    for agent in agents:
        if bool(getattr(agent, "worktree_merged", False)) and str(getattr(agent, "worktree_path", "") or "").strip():
            agent_id = getattr(agent, "id", "") or ""
            at_risk_watchlist.append({
                "id": f"mc:agent:{agent_id}:retained_merged_worktree",
                "ref": {"kind": "agent", "id": agent_id},
                "kind": "worktree_cleanup",
                "title": getattr(agent, "name", "") or agent_id,
                "group": getattr(agent, "group", "") or "",
                "owner": _agent_ref(state, agent_id),
                "task_ids": [getattr(agent, "current_task_id", "") or ""] if getattr(agent, "current_task_id", "") else [],
                "primary_task_id": getattr(agent, "current_task_id", "") or "",
                "gate": "retained_merged_worktree",
                "reason": "Merged worktree is retained and may be eligible for operator cleanup inspection.",
                "recommended_next_action": "clean_retained_merged_worktree",
                "evidence_chips": ["worktree_merged:true"],
                "caveat_chips": ["read_only_recommendation", "no_cleanup_button"],
                "severity": "low",
                "priority": 35,
                "timestamps": {
                    "last_activity_at": getattr(agent, "last_activity_at", 0.0) or 0.0,
                    "last_progress_at": getattr(agent, "last_progress_at", 0.0) or 0.0,
                },
                "deep_links": [_agent_deep_link(agent)],
                "source_refs": [_compact_source_ref("agent", agent_id)],
            })

    for task in open_tasks:
        if task.id in seen_task_ids:
            continue
        lane = str(getattr(task, "lane", "") or "")
        if lane not in {"In Progress", "To Do"}:
            continue
        health = str(getattr(task, "health_state", "") or "healthy").strip()
        if health not in {"", "healthy"}:
            continue
        in_flight.append(_task_card(
            state,
            task,
            card_id=f"mc:task:{task.id}:in_flight",
            kind="task",
            gate="active_work",
            reason="Active work appears healthy.",
            recommended_next_action="continue_implementation",
            evidence_chips=[f"lane:{lane}", "health:healthy"],
            severity="low",
            priority=25,
        ))

    if include_recent_completed and recent_window > 0:
        cutoff = float(now_ts) - recent_window
        for task in closed_tasks:
            completed_ts = max(_parse_ts(getattr(task, "lane_entered_at", "")), _task_updated_ts(task))
            if completed_ts < cutoff:
                continue
            recently_completed.append(_task_card(
                state,
                task,
                card_id=f"mc:task:{task.id}:completed",
                kind="task_completion",
                gate="completed",
                reason="Task completed recently.",
                recommended_next_action="no_action",
                evidence_chips=["lane:Done"],
                severity="none",
                priority=10,
            ))

    sorted_sections = {
        "needs_operator_now": _sort_cards(needs_operator_now),
        "at_risk_watchlist": _sort_cards(at_risk_watchlist),
        "in_flight": _sort_cards(in_flight),
        "recently_completed": _sort_cards(recently_completed),
    }
    sections = {
        key: _limit_section(sorted_sections[key], limit)
        for key in _FIXED_SECTION_KEYS
    }
    return {
        "type": "mission_control_summary",
        "version": VERSION,
        "generated_at": _now_iso(float(now_ts)),
        "group": group,
        "scope": _build_scope(group),
        "limits": {
            "limit_per_section": limit,
            "include_recent_completed": bool(include_recent_completed),
            "recent_completed_seconds": recent_window,
        },
        "counts": {
            "total_cards": sum(section["count"] for section in sections.values()),
            **{key: sections[key]["count"] for key in _FIXED_SECTION_KEYS},
        },
        "sections": sections,
        "source_freshness": source_freshness,
    }
