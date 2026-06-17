"""Deterministic task-health heuristics for Torque."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import shlex
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
LIVE_WORK_SIGNAL_SECS = IDLE_RISK_AFTER_SECS
STALLED_AFTER_SECS = 20 * 60
THRASH_WINDOW_SECS = 30 * 60
THRASH_MIN_MESSAGES = 6
THRASH_MIN_TRANSITIONS = 3
ARCHIVED_LANE = "Archived"

_BASELINE_PROCESS_NAMES = {
    "bash",
    "csh",
    "fish",
    "sh",
    "tcsh",
    "zsh",
}

_PROGRESS_ACTIONS = {"progress", "derive", "ask"}
_BLOCKED_ACTIONS = {"blocked", "error"}
IMPLEMENTED_NO_REVIEW_BOUNDARY_REASON = "implemented_no_review_boundary"
_REVIEW_ACTION_NAME = "feature/review"
_OPEN_BOUNDARY_STATUSES = {"open", "awaiting-review", "awaiting_review"}


@dataclass(frozen=True)
class TaskHealthSnapshot:
    state: str
    details: dict


@dataclass
class _HealthIndexes:
    """Precomputed full-board lookups used during task-health recompute.

    Full task-health passes used to call helper functions that scanned every
    active task for every active task.  Keep those helper APIs intact for small
    incremental contexts, but pass this context during full recomputes so the
    known hot paths become O(1)/edge-local lookups after one indexing pass.
    """

    tasks_by_id: dict[str, Any]
    children_by_id: dict[str, list[Any]]
    open_child_ids_by_parent: dict[str, set[str]]
    open_work_by_agent: dict[str, set[str]]
    open_worktree_boundaries_by_repo_branch: set[tuple[str, str]]
    open_review_stream_roots: set[str]
    open_review_parent_ids: set[str]
    open_review_ids: set[str]

    @classmethod
    def build(cls, tasks_by_id: dict[str, Any]) -> "_HealthIndexes":
        children_by_id: dict[str, list[Any]] = defaultdict(list)
        open_child_ids_by_parent: dict[str, set[str]] = defaultdict(set)
        open_work_by_agent: dict[str, set[str]] = defaultdict(set)
        open_worktree_boundaries_by_repo_branch: set[tuple[str, str]] = set()
        open_review_stream_roots: set[str] = set()
        open_review_parent_ids: set[str] = set()
        open_review_ids: set[str] = set()

        for task in tasks_by_id.values():
            tid = str(getattr(task, "id", "") or "").strip()
            parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
            lane = str(getattr(task, "lane", "") or "")
            if parent_id and parent_id in tasks_by_id:
                children_by_id[parent_id].append(task)
                if lane not in {"Done", ARCHIVED_LANE} and tid:
                    open_child_ids_by_parent[parent_id].add(tid)

            agent_id = str(getattr(task, "agent_id", "") or "").strip()
            if agent_id and tid and lane not in {"Done", "Backlog", ARCHIVED_LANE}:
                open_work_by_agent[agent_id].add(tid)

            boundary = getattr(task, "worktree_boundary", {}) or {}
            if isinstance(boundary, dict):
                status = str(boundary.get("status", "") or "").strip().lower()
                if status in _OPEN_BOUNDARY_STATUSES:
                    branch = str(boundary.get("branch", "") or "").strip()
                    repo_root = str(boundary.get("repo_root", "") or "").strip()
                    if branch and repo_root:
                        open_worktree_boundaries_by_repo_branch.add(
                            (repo_root, branch)
                        )

            action_name = str(getattr(task, "action_name", "") or "").strip().lower()
            if action_name == _REVIEW_ACTION_NAME and lane not in {"Done", ARCHIVED_LANE}:
                if tid:
                    open_review_ids.add(tid)
                root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
                if not root_id:
                    root_id = tid
                if root_id:
                    open_review_stream_roots.add(root_id)
                if parent_id:
                    open_review_parent_ids.add(parent_id)

        return cls(
            tasks_by_id=tasks_by_id,
            children_by_id=dict(children_by_id),
            open_child_ids_by_parent={
                key: set(value)
                for key, value in open_child_ids_by_parent.items()
            },
            open_work_by_agent={
                key: set(value)
                for key, value in open_work_by_agent.items()
            },
            open_worktree_boundaries_by_repo_branch=(
                open_worktree_boundaries_by_repo_branch
            ),
            open_review_stream_roots=open_review_stream_roots,
            open_review_parent_ids=open_review_parent_ids,
            open_review_ids=open_review_ids,
        )


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

    indexes = _HealthIndexes.build(active_tasks)

    locals_by_id = {
        tid: _compute_local_health(task, indexes, agents_by_id, now_ts)
        for tid, task in active_tasks.items()
    }

    effective: dict[str, TaskHealthSnapshot] = {}

    def _compute_effective(task: Any) -> TaskHealthSnapshot:
        if task.id in effective:
            return effective[task.id]
        snapshot = _roll_up_health(
            task,
            locals_by_id[task.id],
            [_compute_effective(child) for child in indexes.children_by_id.get(task.id, [])
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


def _compute_local_health(task: Any, tasks_by_id: dict[str, Any] | _HealthIndexes,
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

    if "torque:human" in labels:
        reasons.append("awaiting_human")
    if "torque:blocked" in labels:
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

    stale_reasons = _stale_in_progress_reasons(
        task,
        tasks_by_id,
        agents_by_id,
        now_ts,
    )
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
        if _has_live_work_signal(agent, now_ts):
            details["reasons"] = ["live_work_signal"]
            details["live_work_signal"] = True
            return TaskHealthSnapshot(state=HEALTH_HEALTHY, details=details)
        details["reasons"] = ["progress_silence_warning"]
        return TaskHealthSnapshot(state=HEALTH_IDLE_RISK, details=details)

    details["reasons"] = ["recent_activity"]
    return TaskHealthSnapshot(state=HEALTH_HEALTHY, details=details)


def next_task_health_deadline(task: Any, tasks_by_id: dict[str, Any] | _HealthIndexes,
                              agents_by_id: dict[str, Any],
                              now_ts: float) -> float | None:
    """Return the next timestamp at which this task can change by time alone.

    Structural changes (dependencies, labels, lanes, agents, worktree status)
    are handled by MatrixState dirty tracking.  This helper is intentionally
    conservative: it may schedule an extra due check, but should not schedule
    later than a known idle/stalled/thrash/live-signal transition.
    """
    if _task_counts_as_done(task) or getattr(task, "lane", "") == ARCHIVED_LANE:
        return None

    labels = set(getattr(task, "labels", []) or [])
    agent = agents_by_id.get(getattr(task, "agent_id", "") or "")
    if (
            "torque:human" in labels
            or "torque:blocked" in labels
            or _has_unmet_dependencies(task, tasks_by_id)
            or (agent and getattr(agent, "activity", "") == "waiting")):
        return None

    deadlines: list[float] = []
    thrash_deadline = _thrash_deadline(task, now_ts)
    if thrash_deadline:
        deadlines.append(thrash_deadline)

    live_deadline = _live_work_signal_deadline(agent, now_ts)
    if live_deadline:
        deadlines.append(live_deadline)

    if _is_monitored_task(task):
        last_activity_ts = _task_last_activity_ts(task, agent)
        if last_activity_ts is None:
            return min(deadlines) if deadlines else None
        idle_at = last_activity_ts + IDLE_RISK_AFTER_SECS
        stalled_at = last_activity_ts + STALLED_AFTER_SECS
        if now_ts < idle_at:
            deadlines.append(idle_at)
        if now_ts < stalled_at:
            deadlines.append(stalled_at)

    future_deadlines = [deadline for deadline in deadlines if deadline > now_ts]
    return min(future_deadlines) if future_deadlines else None


def _is_monitored_task(task: Any) -> bool:
    lane = getattr(task, "lane", "")
    return lane == "In Progress" or bool(getattr(task, "agent_id", ""))


def _has_unmet_dependencies(task: Any, tasks_by_id: dict[str, Any]) -> bool:
    tasks = (
        tasks_by_id.tasks_by_id
        if isinstance(tasks_by_id, _HealthIndexes)
        else tasks_by_id
    )
    for dep_id in getattr(task, "depends_on", []) or []:
        dep = tasks.get(dep_id)
        if dep and not _task_counts_as_done(dep):
            return True
    return False


def _stale_in_progress_reasons(task: Any, tasks_by_id: dict[str, Any],
                               agents_by_id: dict[str, Any],
                               now_ts: float) -> list[str]:
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
    if _has_live_work_signal(agent, now_ts):
        return []
    if _task_has_open_child(task, tasks_by_id):
        return []
    if _agent_has_other_open_work(agent_id, task.id, tasks_by_id):
        return []

    reasons = []
    branch_ahead = getattr(agent, "worktree_ahead", 0) > 0
    if branch_ahead and _has_no_open_review_boundary(task, tasks_by_id, agent):
        reasons.append(IMPLEMENTED_NO_REVIEW_BOUNDARY_REASON)
    if getattr(agent, "worktree_checkpoints", 0) > 0:
        reasons.append("checkpointed_worktree")
    if branch_ahead:
        reasons.append("branch_ahead_of_base")
    if (not getattr(agent, "worktree_dirty", False)
            and getattr(agent, "worktree_path", "")
            and getattr(agent, "worktree_checkpoints", 0) > 0):
        reasons.append("clean_checkpointed_branch")
    return reasons


def _has_no_open_review_boundary(task: Any, tasks_by_id: dict[str, Any],
                                 agent: Any | None) -> bool:
    """Return true when an ahead branch lacks review/boundary progress.

    This is intentionally read-only and advisory: an open worktree boundary or
    any still-open ``feature/review`` task in the same stream means review has
    at least been handed off, so the implemented-but-not-reviewed reason should
    not fire.
    """
    if _task_has_open_worktree_boundary(task):
        return False
    if _branch_has_open_worktree_boundary(task, tasks_by_id, agent):
        return False
    if _stream_has_open_review_task(task, tasks_by_id):
        return False
    return True


def _task_has_open_worktree_boundary(task: Any) -> bool:
    boundary = getattr(task, "worktree_boundary", {}) or {}
    if not isinstance(boundary, dict):
        return False
    status = str(boundary.get("status", "") or "").strip().lower()
    return status in _OPEN_BOUNDARY_STATUSES


def _branch_has_open_worktree_boundary(task: Any, tasks_by_id: dict[str, Any],
                                       agent: Any | None) -> bool:
    branch = str(getattr(agent, "worktree_branch", "") or "").strip()
    repo_root = str(
        getattr(agent, "worktree_repo_root", "")
        or getattr(agent, "git_root", "")
        or ""
    ).strip()
    if not branch or not repo_root:
        return False
    if isinstance(tasks_by_id, _HealthIndexes):
        return (repo_root, branch) in tasks_by_id.open_worktree_boundaries_by_repo_branch

    return _scan_branch_has_open_worktree_boundary(tasks_by_id, repo_root, branch)


def _scan_branch_has_open_worktree_boundary(
        tasks_by_id: dict[str, Any],
        repo_root: str,
        branch: str,
) -> bool:

    for other in tasks_by_id.values():
        boundary = getattr(other, "worktree_boundary", {}) or {}
        if not isinstance(boundary, dict):
            continue
        status = str(boundary.get("status", "") or "").strip().lower()
        if status not in _OPEN_BOUNDARY_STATUSES:
            continue
        if str(boundary.get("branch", "") or "").strip() != branch:
            continue
        if str(boundary.get("repo_root", "") or "").strip() != repo_root:
            continue
        return True
    return False


def _stream_has_open_review_task(task: Any,
                                 tasks_by_id: dict[str, Any]) -> bool:
    if isinstance(tasks_by_id, _HealthIndexes):
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        if not root_id:
            root_id = str(getattr(task, "id", "") or "").strip()
        if root_id and root_id in tasks_by_id.open_review_stream_roots:
            return True
        task_id = str(getattr(task, "id", "") or "").strip()
        if task_id and task_id in tasks_by_id.open_review_parent_ids:
            return True
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        return bool(parent_id and parent_id in tasks_by_id.open_review_ids)

    return _scan_stream_has_open_review_task(task, tasks_by_id)


def _scan_stream_has_open_review_task(task: Any,
                                      tasks_by_id: dict[str, Any]) -> bool:
    for other in tasks_by_id.values():
        action_name = str(getattr(other, "action_name", "") or "").strip().lower()
        if action_name != _REVIEW_ACTION_NAME:
            continue
        if getattr(other, "lane", "") in {"Done", ARCHIVED_LANE}:
            continue
        if _tasks_share_stream(task, other):
            return True
    return False


def _tasks_share_stream(left: Any, right: Any) -> bool:
    left_root = str(getattr(left, "pipeline_root_id", "") or "").strip()
    right_root = str(getattr(right, "pipeline_root_id", "") or "").strip()
    if not left_root:
        left_root = str(getattr(left, "id", "") or "").strip()
    if not right_root:
        right_root = str(getattr(right, "id", "") or "").strip()
    if left_root and right_root and left_root == right_root:
        return True

    left_id = str(getattr(left, "id", "") or "").strip()
    right_id = str(getattr(right, "id", "") or "").strip()
    return (
        bool(left_id and getattr(right, "parent_task_id", "") == left_id)
        or bool(right_id and getattr(left, "parent_task_id", "") == right_id)
    )


def _has_live_work_signal(agent: Any | None, now_ts: float) -> bool:
    """Return true for real work signals that should soften idle hints.

    Heartbeats are intentionally excluded: they are passive liveness pings and
    must not hide dead-on-arrival workers that only wake enough to heartbeat.
    """
    if not agent:
        return False
    if str(getattr(agent, "activity", "") or "").strip():
        return True
    if str(getattr(agent, "activity_detail", "") or "").strip():
        return True
    if _has_foreground_work_process(agent):
        return True
    if _ts_within(getattr(agent, "last_checkpoint_at", 0), now_ts,
                  LIVE_WORK_SIGNAL_SECS):
        return True
    return _ts_within(getattr(agent, "last_progress_at", 0), now_ts,
                      LIVE_WORK_SIGNAL_SECS)


def _live_work_signal_deadline(agent: Any | None, now_ts: float) -> float | None:
    if not agent:
        return None
    deadlines = []
    for attr in ("last_checkpoint_at", "last_progress_at"):
        try:
            ts = float(getattr(agent, attr, 0) or 0)
        except (TypeError, ValueError):
            continue
        if ts > 0 and ts <= now_ts and (now_ts - ts) <= LIVE_WORK_SIGNAL_SECS:
            deadlines.append(ts + LIVE_WORK_SIGNAL_SECS + 0.001)
    return min(deadlines) if deadlines else None


def _has_foreground_work_process(agent: Any) -> bool:
    current = _process_name(getattr(agent, "current_process", ""))
    if not current:
        return False
    command = _process_name(getattr(agent, "command", ""))
    if command and current == command:
        return False
    return current not in _BASELINE_PROCESS_NAMES


def _process_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    token = parts[0] if parts else text
    return os.path.basename(token).strip().lower()


def _ts_within(value: Any, now_ts: float, window_secs: float) -> bool:
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return False
    if ts <= 0:
        return False
    try:
        now = float(now_ts)
    except (TypeError, ValueError):
        return False
    return max(0.0, now - ts) <= window_secs


def _task_has_open_child(task: Any, tasks_by_id: dict[str, Any]) -> bool:
    if isinstance(tasks_by_id, _HealthIndexes):
        return bool(
            tasks_by_id.open_child_ids_by_parent.get(
                str(getattr(task, "id", "") or "").strip()
            )
        )
    return _scan_task_has_open_child(task, tasks_by_id)


def _scan_task_has_open_child(task: Any, tasks_by_id: dict[str, Any]) -> bool:
    for other in tasks_by_id.values():
        if getattr(other, "parent_task_id", "") != getattr(task, "id", ""):
            continue
        if getattr(other, "lane", "") not in {"Done", ARCHIVED_LANE}:
            return True
    return False


def _agent_has_other_open_work(agent_id: str, task_id: str,
                               tasks_by_id: dict[str, Any]) -> bool:
    if isinstance(tasks_by_id, _HealthIndexes):
        return any(
            other_id != task_id
            for other_id in tasks_by_id.open_work_by_agent.get(agent_id, set())
        )
    return _scan_agent_has_other_open_work(agent_id, task_id, tasks_by_id)


def _scan_agent_has_other_open_work(agent_id: str, task_id: str,
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


def _thrash_deadline(task: Any, now_ts: float) -> float | None:
    if not _is_thrashing(task, now_ts):
        return None
    timestamps = []
    for msg in getattr(task, "messages", []) or []:
        if not isinstance(msg, dict):
            continue
        ts = msg.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        if now_ts - float(ts) > THRASH_WINDOW_SECS:
            continue
        if not _action_category(msg.get("action", "")):
            continue
        timestamps.append(float(ts))
    if not timestamps:
        return None
    return min(timestamps) + THRASH_WINDOW_SECS + 0.001


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
