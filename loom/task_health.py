"""Deterministic task-health heuristics for Loom."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

HEALTH_HEALTHY = "healthy"
HEALTH_BLOCKED = "blocked"
HEALTH_STALE_IN_PROGRESS = "stale-in-progress"
HEALTH_IDLE_RISK = "idle-risk"
HEALTH_STALLED = "stalled"
HEALTH_THRASHING = "thrashing"

HEALTH_SEVERITY = {
    HEALTH_HEALTHY: 0,
    HEALTH_IDLE_RISK: 1,
    HEALTH_THRASHING: 2,
    HEALTH_STALLED: 3,
    HEALTH_STALE_IN_PROGRESS: 4,
    HEALTH_BLOCKED: 5,
}

IDLE_RISK_AFTER_SECS = 10 * 60
STALLED_AFTER_SECS = 20 * 60
THRASH_WINDOW_SECS = 30 * 60
THRASH_MIN_MESSAGES = 6
THRASH_MIN_TRANSITIONS = 3
ARCHIVED_LANE = "Archived"

_PROGRESS_ACTIONS = {"progress", "derive", "ask"}
_BLOCKED_ACTIONS = {"blocked", "error"}


@dataclass(frozen=True)
class TaskHealthSnapshot:
    state: str
    details: dict


def now_iso(now_ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(now_ts, tz=timezone.utc) if now_ts \
        else datetime.now(timezone.utc)
    return dt.isoformat()


def compute_task_health(tasks_by_id: dict[str, Any], agents_by_id: dict[str, Any],
                        now_ts: float | None = None) -> dict[str, TaskHealthSnapshot]:
    """Return effective task-health snapshots for every task.

    Archived tasks are out of view and are guaranteed-healthy by
    `_compute_local_health`, so we skip them entirely instead of
    allocating snapshots we'll never read.
    """
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()

    active_tasks = {
        tid: task for tid, task in tasks_by_id.items()
        if getattr(task, "lane", "") != ARCHIVED_LANE
    }

    children_by_id: dict[str, list[Any]] = defaultdict(list)
    for task in active_tasks.values():
        parent_id = getattr(task, "parent_task_id", "")
        if parent_id and parent_id in active_tasks:
            children_by_id[parent_id].append(task)

    locals_by_id = {
        tid: _compute_local_health(task, active_tasks, agents_by_id, now_ts)
        for tid, task in active_tasks.items()
    }

    effective: dict[str, TaskHealthSnapshot] = {}

    def _compute_effective(task: Any) -> TaskHealthSnapshot:
        if task.id in effective:
            return effective[task.id]
        snapshot = _roll_up_health(
            task,
            locals_by_id[task.id],
            [_compute_effective(child) for child in children_by_id.get(task.id, [])
             if getattr(child, "lane", "") not in {"Done", ARCHIVED_LANE}],
            active_tasks,
        )
        effective[task.id] = snapshot
        return snapshot

    for task in active_tasks.values():
        _compute_effective(task)
    return effective


def _roll_up_health(task: Any, local: TaskHealthSnapshot,
                    child_snapshots: list[TaskHealthSnapshot],
                    tasks_by_id: dict[str, Any]) -> TaskHealthSnapshot:
    if not child_snapshots:
        return local

    worst = local
    for snapshot in child_snapshots:
        if HEALTH_SEVERITY[snapshot.state] > HEALTH_SEVERITY[worst.state]:
            worst = snapshot

    if worst is local:
        return local

    source_task_id = worst.details.get("source_task_id", "")
    source_title = ""
    if source_task_id and source_task_id in tasks_by_id:
        source_title = getattr(tasks_by_id[source_task_id], "task", "")

    details = dict(worst.details)
    details.setdefault("reasons", [])
    details["aggregate"] = True
    details["source_task_id"] = source_task_id
    details["source_task_title"] = source_title
    details["local_state"] = local.state
    details["local_reasons"] = local.details.get("reasons", [])
    return TaskHealthSnapshot(state=worst.state, details=details)


def _compute_local_health(task: Any, tasks_by_id: dict[str, Any],
                          agents_by_id: dict[str, Any],
                          now_ts: float) -> TaskHealthSnapshot:
    if _task_counts_as_done(task) or getattr(task, "lane", "") == ARCHIVED_LANE:
        return TaskHealthSnapshot(
            state=HEALTH_HEALTHY,
            details={
                "reasons": ["task_done" if _task_counts_as_done(task) else "task_archived"],
                "aggregate": False,
                "source_task_id": task.id,
                "last_activity_at": _iso_or_empty(
                    _task_last_activity_ts(task, agents_by_id.get(getattr(task, "agent_id", ""), None))
                ),
            },
        )

    labels = set(getattr(task, "labels", []) or [])
    agent = agents_by_id.get(getattr(task, "agent_id", "") or "")
    reasons = []

    if "loom:human" in labels:
        reasons.append("awaiting_human")
    if "loom:blocked" in labels:
        reasons.append("explicit_blocked")
    if _has_unmet_dependencies(task, tasks_by_id):
        reasons.append("dependency_blocked")
    if agent and getattr(agent, "activity", "") == "waiting":
        reasons.append("agent_waiting")

    last_activity_ts = _task_last_activity_ts(task, agent)
    details = {
        "aggregate": False,
        "source_task_id": task.id,
        "last_activity_at": _iso_or_empty(last_activity_ts),
        "reasons": reasons[:],
    }
    if agent and getattr(agent, "last_progress_at", 0):
        details["agent_last_progress_at"] = _iso_or_empty(agent.last_progress_at)
        # Backwards-compatible name; health now keys this to progress only.
        details["agent_last_event_at"] = _iso_or_empty(agent.last_progress_at)
    if agent and getattr(agent, "last_heartbeat_at", 0):
        details["agent_last_heartbeat_at"] = _iso_or_empty(
            agent.last_heartbeat_at
        )

    if reasons:
        return TaskHealthSnapshot(state=HEALTH_BLOCKED, details=details)

    stale_reasons = _stale_in_progress_reasons(task, tasks_by_id, agents_by_id)
    if stale_reasons:
        details["reasons"] = stale_reasons
        details["agent_idle"] = True
        return TaskHealthSnapshot(
            state=HEALTH_STALE_IN_PROGRESS,
            details=details,
        )

    if _is_thrashing(task, now_ts):
        details["reasons"] = ["message_churn"]
        return TaskHealthSnapshot(state=HEALTH_THRASHING, details=details)

    if not _is_monitored_task(task):
        details["reasons"] = ["not_active"]
        return TaskHealthSnapshot(state=HEALTH_HEALTHY, details=details)

    if last_activity_ts is None:
        details["reasons"] = ["no_recent_signal"]
        return TaskHealthSnapshot(state=HEALTH_STALLED, details=details)

    silence_secs = max(0, int(now_ts - last_activity_ts))
    details["silence_secs"] = silence_secs
    if silence_secs >= STALLED_AFTER_SECS:
        details["reasons"] = ["no_progress_timeout"]
        return TaskHealthSnapshot(state=HEALTH_STALLED, details=details)
    if silence_secs >= IDLE_RISK_AFTER_SECS:
        details["reasons"] = ["progress_silence_warning"]
        return TaskHealthSnapshot(state=HEALTH_IDLE_RISK, details=details)

    details["reasons"] = ["recent_activity"]
    return TaskHealthSnapshot(state=HEALTH_HEALTHY, details=details)


def _is_monitored_task(task: Any) -> bool:
    lane = getattr(task, "lane", "")
    return lane == "In Progress" or bool(getattr(task, "agent_id", ""))


def _has_unmet_dependencies(task: Any, tasks_by_id: dict[str, Any]) -> bool:
    for dep_id in getattr(task, "depends_on", []) or []:
        dep = tasks_by_id.get(dep_id)
        if dep and not _task_counts_as_done(dep):
            return True
    return False


def _stale_in_progress_reasons(task: Any, tasks_by_id: dict[str, Any],
                               agents_by_id: dict[str, Any]) -> list[str]:
    if getattr(task, "lane", "") != "In Progress":
        return []
    agent_id = getattr(task, "agent_id", "") or ""
    if not agent_id:
        return []
    agent = agents_by_id.get(agent_id)
    if not agent or getattr(agent, "cell_type", "") != "agent":
        return []
    if getattr(agent, "status", "") != "running":
        return []
    if getattr(agent, "activity", ""):
        return []
    if _task_has_open_child(task, tasks_by_id):
        return []
    if _agent_has_other_open_work(agent_id, task.id, tasks_by_id):
        return []

    reasons = []
    if getattr(agent, "worktree_checkpoints", 0) > 0:
        reasons.append("checkpointed_worktree")
    if getattr(agent, "worktree_ahead", 0) > 0:
        reasons.append("branch_ahead_of_base")
    if (not getattr(agent, "worktree_dirty", False)
            and getattr(agent, "worktree_path", "")
            and getattr(agent, "worktree_checkpoints", 0) > 0):
        reasons.append("clean_checkpointed_branch")
    return reasons


def _task_has_open_child(task: Any, tasks_by_id: dict[str, Any]) -> bool:
    for other in tasks_by_id.values():
        if getattr(other, "parent_task_id", "") != getattr(task, "id", ""):
            continue
        if getattr(other, "lane", "") not in {"Done", ARCHIVED_LANE}:
            return True
    return False


def _agent_has_other_open_work(agent_id: str, task_id: str,
                               tasks_by_id: dict[str, Any]) -> bool:
    for other in tasks_by_id.values():
        if getattr(other, "id", "") == task_id:
            continue
        if getattr(other, "agent_id", "") != agent_id:
            continue
        if getattr(other, "lane", "") in {"Done", "Backlog", ARCHIVED_LANE}:
            continue
        return True
    return False


def _task_counts_as_done(task: Any) -> bool:
    lane = getattr(task, "lane", "")
    if lane == "Done":
        return True
    return lane == ARCHIVED_LANE and getattr(task, "archived_from_lane", "") == "Done"


def _task_last_activity_ts(task: Any, agent: Any | None) -> float | None:
    timestamps = [
        _parse_iso(getattr(task, "updated_at", "")),
        _parse_iso(getattr(task, "created_at", "")),
    ]
    for msg in getattr(task, "messages", []) or []:
        ts = msg.get("timestamp")
        if isinstance(ts, (int, float)):
            timestamps.append(float(ts))
    if agent and getattr(agent, "last_progress_at", 0):
        timestamps.append(float(agent.last_progress_at))
    timestamps = [ts for ts in timestamps if ts]
    return max(timestamps) if timestamps else None


def _is_thrashing(task: Any, now_ts: float) -> bool:
    recent = []
    for msg in getattr(task, "messages", []) or []:
        ts = msg.get("timestamp")
        action = msg.get("action", "")
        if not isinstance(ts, (int, float)):
            continue
        if now_ts - float(ts) > THRASH_WINDOW_SECS:
            continue
        if action in {"done", "ready"}:
            return False
        category = _action_category(action)
        if category:
            recent.append(category)

    if len(recent) < THRASH_MIN_MESSAGES:
        return False

    transitions = 0
    prev = recent[0]
    for category in recent[1:]:
        if category != prev:
            transitions += 1
            prev = category
    return transitions >= THRASH_MIN_TRANSITIONS


def _action_category(action: str) -> str:
    if action in _PROGRESS_ACTIONS:
        return "progress"
    if action in _BLOCKED_ACTIONS:
        return "blocked"
    return ""


def _parse_iso(ts: str) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def _iso_or_empty(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
