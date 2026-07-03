"""Read-only Torque Steward operating brief helpers.

Wave B keeps Steward behavior conservative: these helpers build structured
summaries from already-visible in-memory state and never mutate Torque state,
dispatch work, send messages, create artifacts, or approve decisions.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .state import ARCHIVED_LANE, board_task_is_closed, task_counts_as_done

STEWARD_BRIEF_SCHEMA_VERSION = 1
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20
_DEFAULT_STALE_AFTER_HOURS = 24.0
_DEFAULT_SILENT_AFTER_HOURS = 2.0

_AUTHORITY_CANNOT = [
    "restart/compact/deploy/administer Torque",
    "schedule notifications or background monitoring",
    "create, move, assign, dispatch, or complete tasks",
    "hire, dismiss, message, or control Engineers/Workers",
    "merge/rebase/checkpoint worktrees or create PRs",
    "edit Agent Classes, Profiles, roles, or specializations",
    "accept decisions or replace Blueprint/Product Manager, Torqly, or Catalyst authority",
]

_HELP_REFS = [
    {
        "title": "Help docs contract",
        "source_path": "docs/reference/help.md",
        "use": "Use architect_help_query/show for deterministic Torque concept explanations.",
    },
    {
        "title": "Agent Classes / Torque Steward class",
        "source_path": "docs/reference/agent-classes.md#torque-steward-class",
        "use": "Explains the Steward class, draft/read-only status, and authority ceiling.",
    },
    {
        "title": "Agent operating rules",
        "source_path": "AGENTS.md",
        "use": "Defines worker/engineer reporting, review gates, and no deploy/stop in worker context.",
    },
]


def _limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_LIMIT
    if parsed < 1:
        return 1
    return min(parsed, _MAX_LIMIT)


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _iso_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _age_hours(ts: float, now_ts: float) -> float:
    if ts <= 0:
        return 0.0
    return max(0.0, (now_ts - ts) / 3600.0)


def _task_title(task: Any) -> str:
    return str(getattr(task, "task", "") or getattr(task, "title", "") or "").strip()


def _agent_label(agent: Any, fallback_id: str = "") -> str:
    if not agent:
        return fallback_id
    return str(
        getattr(agent, "slug", "")
        or getattr(agent, "name", "")
        or getattr(agent, "id", "")
        or fallback_id
    ).strip()


def _task_item(task: Any, *, now_ts: float = 0.0) -> dict[str, Any]:
    updated_ts = _iso_ts(getattr(task, "updated_at", ""))
    item = {
        "task_id": str(getattr(task, "id", "") or ""),
        "title": _task_title(task),
        "lane": str(getattr(task, "lane", "") or ""),
        "status": str(getattr(task, "status", "") or ""),
        "health_state": str(getattr(task, "health_state", "") or "healthy") or "healthy",
        "assigned_engineer_id": str(getattr(task, "assigned_engineer_id", "") or ""),
        "assigned_architect_id": str(getattr(task, "assigned_architect_id", "") or ""),
        "agent_id": str(getattr(task, "agent_id", "") or ""),
        "updated_at": str(getattr(task, "updated_at", "") or ""),
        "labels": list(getattr(task, "labels", []) or []),
    }
    if now_ts and updated_ts:
        item["age_hours"] = round(_age_hours(updated_ts, now_ts), 1)
    return {k: v for k, v in item.items() if v not in ("", None, [])}


def _responsible_for_task(state: Any, task: Any, category: str) -> str:
    labels = {str(label or "").strip().lower() for label in (getattr(task, "labels", []) or [])}
    specialization = str(getattr(task, "suggested_specialization", "") or "").strip()
    assigned = str(getattr(task, "assigned_engineer_id", "") or "").strip()
    agent_id = str(getattr(task, "agent_id", "") or "").strip()
    if category in {"blocked_asks", "missed_user_updates"} or "torque:human" in labels:
        return "user"
    if assigned:
        return _agent_label(getattr(state, "agents", {}).get(assigned), f"Engineer {assigned}")
    if agent_id:
        agent = getattr(state, "agents", {}).get(agent_id)
        owner = str(getattr(agent, "owner_engineer_id", "") or "").strip() if agent else ""
        if owner:
            return _agent_label(getattr(state, "agents", {}).get(owner), f"Engineer {owner}")
    if specialization in {"ui-ux", "frontend"} or "ui" in labels:
        return "Panelsmith"
    if specialization in {"release", "runtime-maintenance"} or labels & {"release", "deploy", "worktree", "merge"}:
        return "Forge"
    if specialization in {"prompts-config", "orchestration-core"}:
        return "Torqly"
    if labels & {"product", "product-proposal", "pm-created", "planning"}:
        return "Blueprint/Product Manager"
    if labels & {"creative", "ideation", "exploration"}:
        return "Catalyst"
    if labels & {"comms", "handoff", "notification"}:
        return "Courier"
    return "Torqly or the owning Architect"


def _actor_snapshot(state: Any, group: str, caller_id: str) -> dict[str, Any]:
    actors = []
    classes: dict[str, int] = {}
    for cell in getattr(state, "agents", {}).values():
        if str(getattr(cell, "cell_type", "") or "") != "agent":
            continue
        if str(getattr(cell, "group", "") or "").strip() != group:
            continue
        kind = str(getattr(cell, "kind", "") or "").strip() or "agent"
        class_id = str(getattr(cell, "effective_agent_class_id", "") or "").strip()
        class_label = ""
        snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
        if isinstance(snapshot, dict):
            class_label = str(
                snapshot.get("primary_identity_label")
                or snapshot.get("display_name")
                or ""
            ).strip()
        class_key = class_label or class_id or f"default {kind}"
        classes[class_key] = classes.get(class_key, 0) + 1
        if kind in {"architect", "engineer"} or cell.id == caller_id:
            current_task = state.agent_current_task(cell.id) if hasattr(state, "agent_current_task") else None
            actors.append({
                "id": cell.id,
                "name": getattr(cell, "name", "") or cell.id,
                "slug": getattr(cell, "slug", "") or "",
                "kind": kind,
                "status": getattr(cell, "status", "") or "",
                "class": class_key,
                "current_task_id": getattr(current_task, "id", "") if current_task else "",
                "current_task": _task_title(current_task) if current_task else "",
            })
    actors.sort(key=lambda item: (item.get("kind", ""), item.get("name", "").lower(), item.get("id", "")))
    return {
        "architects_and_engineers": actors,
        "class_counts": [
            {"class": key, "count": count}
            for key, count in sorted(classes.items(), key=lambda kv: kv[0].lower())
        ],
        "note": "Actor/class data is limited to agents visible in this same-group read model; it is not roster-management authority.",
    }


def _bounded(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    return {"count": len(items), "items": items[:limit], "truncated": len(items) > limit}


def _append_suggestion(suggestions: list[dict[str, Any]], *, category: str,
                       actor: str, recommendation: str, evidence: list[str],
                       risk: str = "medium", confidence: str = "medium") -> None:
    suggestions.append({
        "category": category,
        "responsible_actor": actor,
        "recommendation": recommendation,
        "evidence": evidence,
        "risk": risk,
        "confidence": confidence,
        "mutation_performed": False,
    })


def build_steward_operating_brief(state: Any, architect_id: str, group: str,
                                  args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a structured read-only Steward onboarding/operating brief."""

    args = dict(args or {})
    mode = str(args.get("mode", "operating") or "operating").strip().lower()
    if mode not in {"onboarding", "operating", "anomalies"}:
        mode = "operating"
    limit = _limit(args.get("limit_per_section"))
    stale_after_hours = _positive_float(args.get("stale_after_hours"), _DEFAULT_STALE_AFTER_HOURS)
    silent_after_hours = _positive_float(args.get("silent_after_hours"), _DEFAULT_SILENT_AFTER_HOURS)
    now_ts = float(args.get("now_ts") or 0) or time.time()

    tasks = [
        task for task in getattr(state, "board_tasks", {}).values()
        if str(getattr(task, "group", "") or "").strip() == group
        and str(getattr(task, "lane", "") or "") != ARCHIVED_LANE
    ]
    open_tasks = [task for task in tasks if not board_task_is_closed(task)]
    done_tasks = [task for task in tasks if task_counts_as_done(task)]

    lane_counts: dict[str, int] = {}
    health_counts: dict[str, int] = {}
    active_workstreams: list[dict[str, Any]] = []
    for task in tasks:
        lane = str(getattr(task, "lane", "") or "")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        health = str(getattr(task, "health_state", "") or "healthy") or "healthy"
        health_counts[health] = health_counts.get(health, 0) + 1
        if task in open_tasks and lane in {"In Progress", "To Do"}:
            active_workstreams.append(_task_item(task, now_ts=now_ts))
    active_workstreams.sort(key=lambda item: (item.get("lane", ""), item.get("title", ""), item.get("task_id", "")))

    blocked_asks = []
    stale_handoffs = []
    stale_reviews = []
    missed_user_updates = []
    unhealthy_tasks = []
    branch_merge_gates = []

    children_by_parent: dict[str, list[Any]] = {}
    for task in tasks:
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(task)

    for task in open_tasks:
        labels = {str(label or "").strip() for label in (getattr(task, "labels", []) or [])}
        lower_labels = {label.lower() for label in labels}
        health = str(getattr(task, "health_state", "") or "healthy") or "healthy"
        updated_age = _age_hours(_iso_ts(getattr(task, "updated_at", "")), now_ts)
        item = _task_item(task, now_ts=now_ts)
        is_review_like = (
            str(getattr(task, "action_name", "") or "").endswith("/review")
            or "review" in lower_labels
            or str(getattr(task, "requires_review", "") or "").lower() == "true"
        )
        if "torque:human" in lower_labels or "human" in lower_labels or "awaiting input" in str(getattr(task, "status", "") or "").lower():
            blocked_asks.append({
                **item,
                "responsible_actor": "user",
                "evidence": ["task is open and marked as a human/blocking ask"],
            })
        if health != "healthy":
            unhealthy_tasks.append({
                **item,
                "responsible_actor": _responsible_for_task(state, task, "unhealthy_tasks"),
                "evidence": [f"health_state={health}"],
            })
        if (
            updated_age >= stale_after_hours
            and str(getattr(task, "lane", "") or "") == "In Progress"
            and not is_review_like
        ):
            stale_handoffs.append({
                **item,
                "responsible_actor": _responsible_for_task(state, task, "stale_handoffs"),
                "evidence": [f"In Progress task has not updated for {updated_age:.1f}h"],
            })
        if is_review_like and updated_age >= stale_after_hours:
            stale_reviews.append({
                **item,
                "responsible_actor": _responsible_for_task(state, task, "stale_reviews"),
                "evidence": [f"review-looking task is open and stale for {updated_age:.1f}h"],
            })
        boundary = getattr(task, "worktree_boundary", {}) or {}
        if isinstance(boundary, dict) and boundary:
            status = str(boundary.get("status", "") or "").strip()
            if status not in {"merged", "superseded"}:
                branch_merge_gates.append({
                    **item,
                    "responsible_actor": _responsible_for_task(state, task, "branch_merge_gates"),
                    "evidence": ["visible worktree boundary is still open"],
                    "boundary": {
                        key: boundary.get(key)
                        for key in ("repo_root", "branch", "base_branch", "status", "recorded_at")
                        if boundary.get(key) not in ("", None)
                    },
                })
        completed_children = [child for child in children_by_parent.get(getattr(task, "id", ""), []) if task_counts_as_done(child)]
        if completed_children:
            stale_handoffs.append({
                **item,
                "responsible_actor": _responsible_for_task(state, task, "stale_handoffs"),
                "evidence": [
                    "parent/root task is still open after derived work completed",
                    "completed child task ids: " + ", ".join(getattr(child, "id", "") for child in completed_children[:3]),
                ],
            })

    for task in done_tasks:
        labels = {str(label or "").strip().lower() for label in (getattr(task, "labels", []) or [])}
        if labels & {"user-facing", "operator-smoke", "release", "deploy"}:
            missed_user_updates.append({
                **_task_item(task, now_ts=now_ts),
                "responsible_actor": "user" if "operator-smoke" in labels else _responsible_for_task(state, task, "missed_user_updates"),
                "evidence": ["completed task carries a user-facing/release label; verify the user-facing update or smoke handoff is visible"],
            })

    dangling_workers = []
    silent_agents = []
    for cell in getattr(state, "agents", {}).values():
        if str(getattr(cell, "cell_type", "") or "") != "agent":
            continue
        if str(getattr(cell, "group", "") or "").strip() != group:
            continue
        kind = str(getattr(cell, "kind", "") or "").strip()
        current_task = state.agent_current_task(cell.id) if hasattr(state, "agent_current_task") else None
        status = str(getattr(cell, "status", "") or "")
        if kind == "worker" and not current_task and int(getattr(cell, "tasks_dispatched", 0) or 0) == 0:
            dangling_workers.append({
                "agent_id": cell.id,
                "agent": _agent_label(cell, cell.id),
                "status": status,
                "responsible_actor": _agent_label(getattr(state, "agents", {}).get(str(getattr(cell, "owner_engineer_id", "") or "")), "owning Engineer or Torqly"),
                "evidence": ["worker has no visible current task and tasks_dispatched=0"],
            })
        last_progress = float(getattr(cell, "last_progress_at", 0.0) or 0.0)
        if current_task and status in {"running", "idle"} and last_progress > 0:
            silent_hours = _age_hours(last_progress, now_ts)
            if silent_hours >= silent_after_hours:
                silent_agents.append({
                    "agent_id": cell.id,
                    "agent": _agent_label(cell, cell.id),
                    "kind": kind,
                    "status": status,
                    "current_task_id": current_task.id,
                    "current_task": _task_title(current_task),
                    "silent_hours": round(silent_hours, 1),
                    "responsible_actor": _responsible_for_task(state, current_task, "silent_agents"),
                    "evidence": [f"agent has visible active task and no progress for {silent_hours:.1f}h"],
                })

    for section in (blocked_asks, stale_handoffs, stale_reviews, missed_user_updates, unhealthy_tasks, branch_merge_gates):
        section.sort(key=lambda item: (-float(item.get("age_hours", 0) or 0), item.get("title", ""), item.get("task_id", "")))
    dangling_workers.sort(key=lambda item: (item.get("agent", ""), item.get("agent_id", "")))
    silent_agents.sort(key=lambda item: (-float(item.get("silent_hours", 0) or 0), item.get("agent", "")))

    anomalies = {
        "blocked_asks": _bounded(blocked_asks, limit),
        "stale_handoffs": _bounded(stale_handoffs, limit),
        "stale_reviews": _bounded(stale_reviews, limit),
        "missed_user_updates": _bounded(missed_user_updates, limit),
        "dangling_unused_workers": _bounded(dangling_workers, limit),
        "silent_agents_workstreams": _bounded(silent_agents, limit),
        "unhealthy_tasks": _bounded(unhealthy_tasks, limit),
        "branch_boundary_merge_gates": _bounded(branch_merge_gates, limit),
    }

    observed_facts = [
        {"kind": "group", "text": f"Group {group!r} has {len(tasks)} visible non-archived tasks and {len(open_tasks)} open tasks."},
        {"kind": "lanes", "counts": dict(sorted(lane_counts.items()))},
        {"kind": "health", "counts": dict(sorted(health_counts.items()))},
        {"kind": "authority", "text": "Steward Wave B output is read-only: observations, inferred risks, and suggested next steps only."},
    ]

    risks = []
    suggestions: list[dict[str, Any]] = []
    risk_sources = [
        ("blocked_asks", blocked_asks, "Blocked ask may be waiting on user input."),
        ("stale_handoffs", stale_handoffs, "Completed/routed work may not have an active owner or parent follow-through."),
        ("stale_reviews", stale_reviews, "Review loop may be waiting longer than expected."),
        ("missed_user_updates", missed_user_updates, "Completed user-facing work may still need operator-facing status or smoke handoff."),
        ("dangling_unused_workers", dangling_workers, "Unused worker sessions may be noise or cleanup candidates."),
        ("silent_agents_workstreams", silent_agents, "Active workstream has gone quiet."),
        ("branch_boundary_merge_gates", branch_merge_gates, "Visible branch/worktree boundary may need authorized merge/rebase/review handling."),
    ]
    for category, items, text in risk_sources:
        if not items:
            continue
        risks.append({"category": category, "risk": text, "evidence_count": len(items)})
        first = items[0]
        _append_suggestion(
            suggestions,
            category=category,
            actor=str(first.get("responsible_actor") or "authorized owner"),
            recommendation=(
                "Review the cited evidence and take the smallest authorized next step; "
                "the Steward should not perform the action."
            ),
            evidence=list(first.get("evidence", []))[:3],
            risk="high" if category in {"blocked_asks", "branch_boundary_merge_gates"} else "medium",
        )

    return {
        "type": "steward_operating_brief",
        "schema_version": STEWARD_BRIEF_SCHEMA_VERSION,
        "mode": mode,
        "group": group,
        "generated_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "authority_contract": {
            "can": [
                "read visible same-group operating context through projected read tools",
                "explain Torque concepts using maintained Help docs",
                "summarize observed operating state",
                "infer risks with evidence and confidence",
                "suggest responsible actors and safe next steps",
            ],
            "cannot": list(_AUTHORITY_CANNOT),
            "mutation_performed": False,
            "structured_output": ["observed_facts", "inferred_risks", "suggested_next_steps"],
        },
        "onboarding": {
            "purpose": "Torque Steward is a conservative group operating entrypoint for onboarding, health summaries, anomaly detection, and responsible-actor suggestions.",
            "concepts": ["groups", "Architects/classes", "Engineers", "Workers", "board tasks", "decisions", "actions", "worktrees", "review gates"],
            "help_refs": list(_HELP_REFS),
            "visible_actors": _actor_snapshot(state, group, architect_id),
        },
        "operating_state": {
            "active_workstreams": _bounded(active_workstreams, limit),
            "current_asks_and_gates": {
                "blocked_asks": anomalies["blocked_asks"],
                "branch_boundary_merge_gates": anomalies["branch_boundary_merge_gates"],
                "stale_reviews": anomalies["stale_reviews"],
            },
            "important_decisions": {
                "note": "Use architect_decision_list for caller-visible decisions; this brief does not accept or mutate decisions.",
            },
        },
        "observed_facts": observed_facts,
        "anomalies": anomalies,
        "inferred_risks": risks,
        "suggested_next_steps": suggestions[:limit],
        "responsible_agent_suggestions": suggestions[:limit],
        "scoping": {
            "state_source": "already-projected same-group MCP state view",
            "reads_only": True,
            "stale_after_hours": stale_after_hours,
            "silent_after_hours": silent_after_hours,
            "limit_per_section": limit,
            "denied_actions": list(_AUTHORITY_CANNOT),
        },
    }
