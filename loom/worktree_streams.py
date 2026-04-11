"""Computed worktree stream synthesis helpers.

Phase 1 keeps streams as a read-only computed model derived from existing
task, boundary, verification, and agent state. No persisted stream table is
introduced here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .state import board_task_is_closed
from .task_ids import parse_task_id
from .worktree_boundaries import (
    branch_boundary_tasks,
    branch_key,
    queued_successor_tasks,
    started_successor_tasks,
    task_boundary,
    task_branch_key,
)

_QUEUED_LANES = {"Backlog", "To Do"}
_VISIBILITY_LABELS = {"loom:weaver-message"}
_WORKFLOW_LABELS = {"loom:derived", "review-fix", "loom:human"}
_REVIEW_HINTS = ("review", "re-review")
_VALIDATION_HINTS = ("validate", "validation", "manual smoke", "smoke test")
_MERGE_CONFLICT_HINTS = ("merge conflict", "resolve conflict", "conflict")
_PRODUCT_ACTIONS = {
    "feature/implement",
    "oneshot/feature",
    "oneshot/fix",
    "oneshot/improvement",
}
_NON_PRODUCT_ACTION_HINTS = (
    "review",
    "validate",
    "validation",
    "merge",
    "conflict",
    "research",
)


def stream_id(repo_root: str, branch: str) -> str:
    """Return the canonical computed stream id for a branch/worktree slice."""
    return f"stream:{branch_key(repo_root, branch)}"


def stream_identity_for_agent(cell) -> tuple[str, str] | None:
    """Return ``(repo_root, branch)`` for an agent with an active worktree."""
    if not cell:
        return None
    repo_root = str(
        getattr(cell, "worktree_repo_root", "")
        or getattr(cell, "git_root", "")
        or ""
    ).strip()
    branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    if not repo_root or not branch:
        return None
    return repo_root, branch


def classify_stream_task(task, tasks_by_id: dict[str, Any] | None = None) -> str:
    """Classify a task as ``product``, ``workflow``, or ``visibility``."""
    if _is_visibility_task(task):
        return "visibility"
    if _is_workflow_task(task, tasks_by_id or {}):
        return "workflow"
    return "product"


def compute_worktree_streams(state, *, group: str = "",
                             visibility_limit: int = 10) -> list[dict]:
    """Return computed streams for ``state`` filtered to ``group`` when set."""
    tasks = [
        task for task in state.board_tasks.values()
        if not group or getattr(task, "group", "") == group
    ]
    agents = [
        cell for cell in state.agents.values()
        if getattr(cell, "cell_type", "") == "agent"
        and (not group or getattr(cell, "group", "") == group)
    ]
    if not tasks and not agents:
        return []

    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in tasks
        if getattr(task, "id", "")
    }
    children_by_parent: dict[str, set[str]] = defaultdict(set)
    boundary_task_to_stream_key: dict[str, str] = {}
    boundary_to_successors: dict[str, set[str]] = defaultdict(set)
    seeds_by_stream_key: dict[str, set[str]] = defaultdict(set)
    agent_ids_by_stream_key: dict[str, set[str]] = defaultdict(set)
    agent_stream_key_by_id: dict[str, str] = {}

    for task in tasks:
        task_id = getattr(task, "id", "") or ""
        parent_id = getattr(task, "parent_task_id", "") or ""
        if parent_id:
            children_by_parent[parent_id].add(task_id)

        key = task_branch_key(task)
        if key:
            boundary_task_to_stream_key[task_id] = key
            seeds_by_stream_key[key].add(task_id)

    for task in tasks:
        task_id = getattr(task, "id", "") or ""
        boundary_task_id = (
            getattr(task, "resume_after_boundary_task_id", "") or ""
        )
        if boundary_task_id:
            boundary_to_successors[boundary_task_id].add(task_id)
            stream_key = boundary_task_to_stream_key.get(boundary_task_id, "")
            if stream_key:
                seeds_by_stream_key[stream_key].add(task_id)

    for cell in agents:
        identity = stream_identity_for_agent(cell)
        if not identity:
            continue
        repo_root, branch = identity
        key = branch_key(repo_root, branch)
        agent_stream_key_by_id[cell.id] = key
        agent_ids_by_stream_key[key].add(cell.id)
        current_task_id = str(getattr(cell, "current_task_id", "") or "").strip()
        if current_task_id in tasks_by_id:
            seeds_by_stream_key[key].add(current_task_id)

    task_explicit_stream_keys = _build_task_explicit_stream_keys(
        tasks=tasks,
        boundary_task_to_stream_key=boundary_task_to_stream_key,
        agent_stream_key_by_id=agent_stream_key_by_id,
    )
    task_descendant_stream_keys = _build_task_descendant_stream_keys(
        tasks_by_id=tasks_by_id,
        children_by_parent=children_by_parent,
        task_explicit_stream_keys=task_explicit_stream_keys,
    )

    for task in tasks:
        reply_agent_id = str(getattr(task, "reply_agent_id", "") or "").strip()
        if reply_agent_id:
            reply_agent = state.agents.get(reply_agent_id)
            identity = stream_identity_for_agent(reply_agent)
            if identity:
                seeds_by_stream_key[branch_key(*identity)].add(task.id)
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if agent_id:
            owner = state.agents.get(agent_id)
            identity = stream_identity_for_agent(owner)
            if identity:
                seeds_by_stream_key[branch_key(*identity)].add(task.id)

    streams = []
    for key in sorted(seeds_by_stream_key):
        if not seeds_by_stream_key[key]:
            continue
        repo_root, branch = key.split("::", 1)
        member_ids = _expand_stream_task_ids(
            seed_ids=seeds_by_stream_key[key],
            target_stream_key=key,
            tasks_by_id=tasks_by_id,
            children_by_parent=children_by_parent,
            boundary_to_successors=boundary_to_successors,
            task_explicit_stream_keys=task_explicit_stream_keys,
            task_descendant_stream_keys=task_descendant_stream_keys,
        )
        if not member_ids:
            continue
        stream = compute_worktree_stream(
            state,
            repo_root=repo_root,
            branch=branch,
            group=group,
            visibility_limit=visibility_limit,
            task_ids=member_ids,
            stream_agent_ids=agent_ids_by_stream_key.get(key, set()),
        )
        if stream:
            streams.append(stream)

    streams.sort(
        key=lambda item: (
            -_parse_iso(item.get("last_activity_at", "")),
            item.get("branch", ""),
            item.get("stream_id", ""),
        )
    )
    return streams


def compute_worktree_stream(state, *, repo_root: str, branch: str,
                            group: str = "", visibility_limit: int = 10,
                            task_ids: set[str] | None = None,
                            stream_agent_ids: set[str] | None = None
                            ) -> dict | None:
    """Return one computed stream for ``repo_root`` + ``branch``."""
    if not repo_root or not branch:
        return None

    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in state.board_tasks.values()
        if getattr(task, "id", "")
        and (not group or getattr(task, "group", "") == group)
    }
    if task_ids is None:
        children_by_parent: dict[str, set[str]] = defaultdict(set)
        boundary_to_successors: dict[str, set[str]] = defaultdict(set)
        boundary_task_to_stream_key: dict[str, str] = {}
        boundary_ids: set[str] = set()
        agent_stream_key_by_id: dict[str, str] = {}
        task_ids = set()

        for task in tasks_by_id.values():
            task_id = getattr(task, "id", "") or ""
            parent_id = getattr(task, "parent_task_id", "") or ""
            if parent_id:
                children_by_parent[parent_id].add(task_id)
            task_key = task_branch_key(task)
            if task_key:
                boundary_task_to_stream_key[task_id] = task_key
            if task_key == branch_key(repo_root, branch):
                boundary_ids.add(task_id)
                task_ids.add(task_id)

        for task in tasks_by_id.values():
            boundary_task_id = (
                getattr(task, "resume_after_boundary_task_id", "") or ""
            )
            if boundary_task_id:
                boundary_to_successors[boundary_task_id].add(task.id)
                if boundary_task_id in boundary_ids:
                    task_ids.add(task.id)

        inferred_stream_agent_ids = set(stream_agent_ids or set())
        for cell in state.agents.values():
            if getattr(cell, "cell_type", "") != "agent":
                continue
            if group and getattr(cell, "group", "") != group:
                continue
            identity = stream_identity_for_agent(cell)
            if not identity:
                continue
            key = branch_key(*identity)
            agent_stream_key_by_id[cell.id] = key
            if identity != (repo_root, branch):
                continue
            inferred_stream_agent_ids.add(cell.id)
            current_task_id = str(getattr(cell, "current_task_id", "") or "").strip()
            if current_task_id in tasks_by_id:
                task_ids.add(current_task_id)

        for task in tasks_by_id.values():
            if getattr(task, "agent_id", "") in inferred_stream_agent_ids:
                task_ids.add(task.id)
            if getattr(task, "reply_agent_id", "") in inferred_stream_agent_ids:
                task_ids.add(task.id)

        task_explicit_stream_keys = _build_task_explicit_stream_keys(
            tasks=tasks_by_id.values(),
            boundary_task_to_stream_key=boundary_task_to_stream_key,
            agent_stream_key_by_id=agent_stream_key_by_id,
        )
        task_descendant_stream_keys = _build_task_descendant_stream_keys(
            tasks_by_id=tasks_by_id,
            children_by_parent=children_by_parent,
            task_explicit_stream_keys=task_explicit_stream_keys,
        )
        stream_agent_ids = inferred_stream_agent_ids
        task_ids = _expand_stream_task_ids(
            seed_ids=task_ids,
            target_stream_key=branch_key(repo_root, branch),
            tasks_by_id=tasks_by_id,
            children_by_parent=children_by_parent,
            boundary_to_successors=boundary_to_successors,
            task_explicit_stream_keys=task_explicit_stream_keys,
            task_descendant_stream_keys=task_descendant_stream_keys,
        )
    stream_tasks = [
        tasks_by_id[task_id]
        for task_id in task_ids
        if task_id in tasks_by_id
    ]
    if not stream_tasks:
        return None

    classifications = {
        task.id: classify_stream_task(task, tasks_by_id)
        for task in stream_tasks
    }
    boundary_tasks = branch_boundary_tasks(
        stream_tasks,
        repo_root=repo_root,
        branch=branch,
    )
    latest_boundary = boundary_tasks[-1] if boundary_tasks else None
    review_boundary_tasks = [
        task for task in boundary_tasks
        if _is_review_task(task)
    ]
    latest_review_boundary = (
        review_boundary_tasks[-1] if review_boundary_tasks else None
    )
    reference_boundary = latest_review_boundary or latest_boundary

    queued_followers = []
    started_followers = []
    if reference_boundary:
        queued_followers = queued_successor_tasks(stream_tasks, reference_boundary.id)
        started_followers = started_successor_tasks(stream_tasks, reference_boundary.id)

    product_tasks = _sort_tasks(
        task for task in stream_tasks
        if classifications.get(task.id) == "product"
    )
    workflow_tasks = _sort_tasks(
        task for task in stream_tasks
        if classifications.get(task.id) == "workflow"
    )
    open_non_visibility_tasks = _sort_open_tasks(
        task for task in stream_tasks
        if classifications.get(task.id) != "visibility"
        and not board_task_is_closed(task)
    )
    queued_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if getattr(task, "lane", "") in _QUEUED_LANES
    )
    started_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if getattr(task, "lane", "") not in _QUEUED_LANES
    )
    review_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if _is_review_task(task)
    )
    blocker_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if _is_blocker_task(task, tasks_by_id)
    )
    validation_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if _is_validation_task(task)
    )

    merged = _is_merged_stream(boundary_tasks, state, repo_root=repo_root, branch=branch)
    validation_state, validation_record = _compute_validation_state(stream_tasks)
    pending_validation = validation_state == "pending_human_validation"
    branch_has_advanced = bool(latest_review_boundary and started_followers)
    partial_review_safe = bool(latest_review_boundary and not started_followers)
    merge_conflict = any(_is_merge_conflict_task(task) for task in blocker_tasks)

    if merged:
        code_state = "merged"
    elif merge_conflict:
        code_state = "merge_conflict"
    elif blocker_tasks:
        code_state = "review_blocked"
    elif (
        latest_review_boundary
        and partial_review_safe
        and not review_tasks
        and not blocker_tasks
    ):
        code_state = "reviewed_clean"
    else:
        code_state = "implementing"

    open_product_tasks = [
        task for task in open_non_visibility_tasks
        if classifications.get(task.id) == "product"
    ]

    if merged:
        stream_state = "merged"
    elif merge_conflict or blocker_tasks:
        stream_state = "fixing_blockers"
    elif review_tasks:
        stream_state = "reviewing"
    elif pending_validation and code_state == "reviewed_clean":
        stream_state = "awaiting_human_validation"
    elif (
        code_state == "reviewed_clean"
        and not open_product_tasks
        and not queued_tasks
    ):
        stream_state = "ready_to_merge"
    else:
        stream_state = "implementing"

    if merged:
        merge_state = "merged"
    elif stream_state == "ready_to_merge":
        merge_state = "ready"
    else:
        merge_state = "not_ready"

    active_blocker_task = blocker_tasks[0] if blocker_tasks else None
    active_review_task = review_tasks[0] if review_tasks else None
    foreground_task = _select_foreground_task(
        state,
        branch=branch,
        repo_root=repo_root,
        stream_tasks=stream_tasks,
        started_tasks=started_tasks,
        blocker_task=active_blocker_task,
        review_task=active_review_task,
    )
    owner_agent = _select_owner_agent(
        state,
        stream_tasks=stream_tasks,
        repo_root=repo_root,
        branch=branch,
        stream_agent_ids=stream_agent_ids or set(),
        latest_boundary=latest_boundary,
        latest_review_boundary=latest_review_boundary,
        foreground_task=foreground_task,
    )
    visibility_items = _recent_visibility_items(
        stream_tasks,
        limit=visibility_limit,
    )

    gate_reason = _compute_gate_reason(
        stream_state=stream_state,
        blocker_task=active_blocker_task,
        review_task=active_review_task,
        validation_state=validation_state,
        validation_record=validation_record,
        merge_conflict=merge_conflict,
        validation_tasks=validation_tasks,
        branch_advanced=branch_has_advanced,
        latest_review_boundary=latest_review_boundary,
    )
    recommended_next_action = _recommended_next_action(
        stream_state=stream_state,
        validation_state=validation_state,
        validation_tasks=validation_tasks,
        merge_conflict=merge_conflict,
        has_open_product_tasks=bool(open_product_tasks or queued_tasks),
    )

    latest_reviewed_commit_sha = ""
    if latest_review_boundary:
        latest_reviewed_commit_sha = (
            task_boundary(latest_review_boundary).get("commit_sha", "") or ""
        )

    last_activity_at = _latest_activity_iso(
        stream_tasks,
        [
            state.agents.get(agent_id)
            for agent_id in {
                *(stream_agent_ids or set()),
                *{
                    getattr(task, "agent_id", "") or ""
                    for task in stream_tasks
                },
                *{
                    getattr(task, "reply_agent_id", "") or ""
                    for task in stream_tasks
                },
            }
            if agent_id and state.agents.get(agent_id)
        ],
    )

    group_value = _stream_group(stream_tasks, owner_agent)
    latest_boundary_info = task_boundary(latest_boundary) if latest_boundary else {}

    return {
        "stream_id": stream_id(repo_root, branch),
        "group": group_value,
        "repo_root": repo_root,
        "branch": branch,
        "agent_id": getattr(owner_agent, "id", "") or "",
        "agent_name": getattr(owner_agent, "name", "") or "",
        "agent_slug": getattr(owner_agent, "slug", "") or "",
        "foreground_task_id": getattr(foreground_task, "id", "") or "",
        "foreground_task_title": getattr(foreground_task, "task", "") or "",
        "product_task_ids": [task.id for task in product_tasks],
        "workflow_task_ids": [task.id for task in workflow_tasks],
        "queued_task_ids": [task.id for task in queued_tasks],
        "started_task_ids": [task.id for task in started_tasks],
        "recent_visibility_items": visibility_items,
        "latest_boundary_task_id": getattr(latest_boundary, "id", "") or "",
        "latest_boundary_task_title": getattr(latest_boundary, "task", "") or "",
        "latest_boundary_recorded_at": (
            latest_boundary_info.get("recorded_at", "") or ""
        ),
        "latest_boundary_status": latest_boundary_info.get("status", "") or "",
        "latest_reviewed_commit_sha": latest_reviewed_commit_sha,
        "active_review_task_id": getattr(active_review_task, "id", "") or "",
        "active_blocker_task_id": getattr(active_blocker_task, "id", "") or "",
        "state": stream_state,
        "code_state": code_state,
        "validation_state": validation_state,
        "merge_state": merge_state,
        "gate_reason": gate_reason,
        "recommended_next_action": recommended_next_action,
        "branch_advanced": branch_has_advanced,
        "partial_review_safe": partial_review_safe,
        "last_activity_at": last_activity_at,
    }


def _expand_stream_task_ids(*, seed_ids: set[str], target_stream_key: str,
                            tasks_by_id: dict[str, Any],
                            children_by_parent: dict[str, set[str]],
                            boundary_to_successors: dict[str, set[str]],
                            task_explicit_stream_keys: dict[str, set[str]],
                            task_descendant_stream_keys: dict[str, set[str]]
                            ) -> set[str]:
    queue = [task_id for task_id in seed_ids if task_id in tasks_by_id]
    seen: set[str] = set()
    while queue:
        task_id = queue.pop()
        if task_id in seen or task_id not in tasks_by_id:
            continue
        seen.add(task_id)
        task = tasks_by_id[task_id]

        for related_id in (
            getattr(task, "parent_task_id", "") or "",
            getattr(task, "pipeline_root_id", "") or "",
            getattr(task, "resume_after_boundary_task_id", "") or "",
        ):
            if (
                related_id
                and related_id in tasks_by_id
                and _task_matches_stream(
                    related_id,
                    target_stream_key=target_stream_key,
                    task_explicit_stream_keys=task_explicit_stream_keys,
                    task_descendant_stream_keys=task_descendant_stream_keys,
                )
            ):
                queue.append(related_id)

        for child_id in children_by_parent.get(task_id, ()):
            if _task_matches_stream(
                child_id,
                target_stream_key=target_stream_key,
                task_explicit_stream_keys=task_explicit_stream_keys,
                task_descendant_stream_keys=task_descendant_stream_keys,
            ):
                queue.append(child_id)
        for successor_id in boundary_to_successors.get(task_id, ()):
            if _task_matches_stream(
                successor_id,
                target_stream_key=target_stream_key,
                task_explicit_stream_keys=task_explicit_stream_keys,
                task_descendant_stream_keys=task_descendant_stream_keys,
            ):
                queue.append(successor_id)
    return seen


def _root_task_id(task) -> str:
    if not task:
        return ""
    return (
        str(getattr(task, "pipeline_root_id", "") or "").strip()
        or (
            str(getattr(task, "id", "") or "").strip()
            if not getattr(task, "parent_task_id", "")
            else ""
        )
    )


def _is_visibility_task(task) -> bool:
    labels = set(getattr(task, "labels", []) or [])
    if labels & _VISIBILITY_LABELS:
        return True
    if str(getattr(task, "reply_agent_id", "") or "").strip():
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "awaiting reply":
        return True
    for message in getattr(task, "messages", []) or []:
        if str(message.get("action", "") or "").strip() in {"weaver_message", "reply"}:
            return True
    return False


def _is_workflow_task(task, tasks_by_id: dict[str, Any]) -> bool:
    if _is_visibility_task(task):
        return False
    labels = set(getattr(task, "labels", []) or [])
    if labels & _WORKFLOW_LABELS:
        return True
    if getattr(task, "parent_task_id", "") or getattr(task, "pipeline_depth", 0):
        return True
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if action_name:
        if action_name in _PRODUCT_ACTIONS:
            return False
        if any(hint in action_name for hint in _NON_PRODUCT_ACTION_HINTS):
            return True
    text = _task_text(task)
    if any(hint in text for hint in _REVIEW_HINTS + _VALIDATION_HINTS):
        return True
    if _is_merge_conflict_task(task):
        return True
    parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
    parent = tasks_by_id.get(parent_id) if parent_id else None
    if parent and _is_review_task(parent):
        return True
    return False


def _is_review_task(task) -> bool:
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if "review" in action_name:
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "on review":
        return True
    text = _task_text(task)
    return any(hint in text for hint in _REVIEW_HINTS)


def _is_validation_task(task) -> bool:
    if _is_visibility_task(task):
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if "validate" in action_name or "validation" in action_name:
        return True
    text = _task_text(task)
    return any(hint in text for hint in _VALIDATION_HINTS)


def _is_merge_conflict_task(task) -> bool:
    if _is_visibility_task(task):
        return False
    text = _task_text(task)
    return any(hint in text for hint in _MERGE_CONFLICT_HINTS)


def _is_blocker_task(task, tasks_by_id: dict[str, Any]) -> bool:
    if _is_visibility_task(task) or _is_review_task(task) or _is_validation_task(task):
        return False
    if _is_merge_conflict_task(task):
        return True
    parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
    if parent_id and _has_review_ancestor(parent_id, tasks_by_id):
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "fixing":
        return True
    root_id = _root_task_id(task)
    root_task = tasks_by_id.get(root_id) if root_id else None
    if root_task:
        root_status = str(getattr(root_task, "status", "") or "").strip().lower()
        if root_status == "fixing":
            return True
    return False


def _has_review_ancestor(task_id: str, tasks_by_id: dict[str, Any]) -> bool:
    current_id = task_id
    while current_id:
        current = tasks_by_id.get(current_id)
        if not current:
            return False
        if _is_review_task(current):
            return True
        current_id = str(getattr(current, "parent_task_id", "") or "").strip()
    return False


def _select_foreground_task(state, *, branch: str, repo_root: str,
                            stream_tasks: list[Any], started_tasks: list[Any],
                            blocker_task=None, review_task=None):
    if blocker_task and getattr(blocker_task, "lane", "") not in _QUEUED_LANES:
        return blocker_task
    if review_task and getattr(review_task, "lane", "") not in _QUEUED_LANES:
        return review_task

    for cell in state.agents.values():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        identity = stream_identity_for_agent(cell)
        if identity != (repo_root, branch):
            continue
        current = state.agent_current_task(cell.id)
        if current and current in stream_tasks and not _is_visibility_task(current):
            return current

    return started_tasks[0] if started_tasks else None


def _select_owner_agent(state, *, stream_tasks: list[Any], repo_root: str,
                        branch: str, stream_agent_ids: set[str],
                        latest_boundary=None, latest_review_boundary=None,
                        foreground_task=None):
    candidate_ids = set(stream_agent_ids or set())
    for task in stream_tasks:
        for agent_id in (
            str(getattr(task, "agent_id", "") or "").strip(),
            str(getattr(task, "reply_agent_id", "") or "").strip(),
            str(task_boundary(task).get("recorded_by_agent_id", "") or "").strip(),
        ):
            if agent_id:
                candidate_ids.add(agent_id)

    candidates = [
        state.agents.get(agent_id)
        for agent_id in candidate_ids
        if agent_id and state.agents.get(agent_id)
        and getattr(state.agents.get(agent_id), "cell_type", "") == "agent"
    ]
    if not candidates:
        return None

    stream_task_ids = {task.id for task in stream_tasks}
    latest_mutable_boundary = _latest_mutable_boundary_task(
        stream_tasks,
        latest_boundary=latest_boundary,
        latest_review_boundary=latest_review_boundary,
    )
    preferred_current_owner_id = _preferred_owner_agent_id(
        stream_tasks,
        latest_mutable_boundary=latest_mutable_boundary,
        foreground_task=foreground_task,
    )
    mutable_task_ids = {
        task.id for task in stream_tasks
        if _is_mutable_task(task)
    }

    def _sort_key(cell):
        identity = stream_identity_for_agent(cell)
        current = state.agent_current_task(cell.id)
        current_id = getattr(current, "id", "") if current else ""
        current_mutable = bool(current_id and current_id in mutable_task_ids)
        linked_mutable = any(
            str(getattr(task, "agent_id", "") or "").strip() == cell.id
            and _is_mutable_task(task)
            for task in stream_tasks
        )
        boundary_owner = bool(
            latest_mutable_boundary
            and str(
                task_boundary(latest_mutable_boundary).get("recorded_by_agent_id", "")
                or ""
            ).strip() == cell.id
        )
        return (
            0 if cell.id == preferred_current_owner_id else 1,
            0 if boundary_owner else 1,
            0 if current_mutable else 1,
            0 if linked_mutable else 1,
            0 if identity == (repo_root, branch) else 1,
            0 if current_id in stream_task_ids else 1,
            0 if getattr(cell, "status", "") == "running" else 1,
            0 if state.agent_is_busy(cell.id) and current_mutable else 1,
            -float(getattr(cell, "last_event_at", 0.0) or 0.0),
            str(getattr(cell, "name", "") or "").lower(),
            cell.id,
        )

    candidates.sort(key=_sort_key)
    return candidates[0]


def _build_task_explicit_stream_keys(*, tasks, boundary_task_to_stream_key: dict[str, str],
                                     agent_stream_key_by_id: dict[str, str]
                                     ) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for task in tasks:
        task_id = getattr(task, "id", "") or ""
        if not task_id:
            continue
        keys = set()
        direct_key = task_branch_key(task)
        if direct_key:
            keys.add(direct_key)
        resume_after = str(getattr(task, "resume_after_boundary_task_id", "") or "").strip()
        if resume_after and resume_after in boundary_task_to_stream_key:
            keys.add(boundary_task_to_stream_key[resume_after])
        for agent_id in (
            str(getattr(task, "agent_id", "") or "").strip(),
            str(getattr(task, "reply_agent_id", "") or "").strip(),
        ):
            key = agent_stream_key_by_id.get(agent_id, "")
            if key:
                keys.add(key)
        out[task_id] = keys
    return out


def _build_task_descendant_stream_keys(*, tasks_by_id: dict[str, Any],
                                       children_by_parent: dict[str, set[str]],
                                       task_explicit_stream_keys: dict[str, set[str]]
                                       ) -> dict[str, set[str]]:
    cache: dict[str, set[str]] = {}

    def _collect(task_id: str) -> set[str]:
        if task_id in cache:
            return set(cache[task_id])
        keys = set(task_explicit_stream_keys.get(task_id, set()))
        for child_id in children_by_parent.get(task_id, ()):
            if child_id not in tasks_by_id:
                continue
            keys.update(_collect(child_id))
        cache[task_id] = set(keys)
        return set(keys)

    for task_id in tasks_by_id:
        _collect(task_id)
    return cache


def _task_matches_stream(task_id: str, *, target_stream_key: str,
                         task_explicit_stream_keys: dict[str, set[str]],
                         task_descendant_stream_keys: dict[str, set[str]]) -> bool:
    explicit_keys = task_explicit_stream_keys.get(task_id, set())
    if explicit_keys:
        return target_stream_key in explicit_keys
    descendant_keys = task_descendant_stream_keys.get(task_id, set())
    if descendant_keys:
        return target_stream_key in descendant_keys
    return True


def _latest_mutable_boundary_task(stream_tasks: list[Any], *,
                                  latest_boundary=None, latest_review_boundary=None):
    if latest_boundary and _is_mutable_task(latest_boundary):
        return latest_boundary
    if latest_review_boundary and latest_review_boundary in stream_tasks:
        prior = [
            task for task in stream_tasks
            if task_boundary(task)
            and _is_mutable_task(task)
            and _task_activity_ts(task) <= _task_activity_ts(latest_review_boundary)
        ]
        prior = _sort_tasks(prior)
        if prior:
            return prior[-1]
    boundaries = _sort_tasks(
        task for task in stream_tasks
        if task_boundary(task) and _is_mutable_task(task)
    )
    return boundaries[-1] if boundaries else None


def _preferred_owner_agent_id(stream_tasks: list[Any], *, latest_mutable_boundary=None,
                              foreground_task=None) -> str:
    if foreground_task and _is_mutable_task(foreground_task):
        agent_id = str(getattr(foreground_task, "agent_id", "") or "").strip()
        if agent_id:
            return agent_id
    if latest_mutable_boundary:
        boundary_agent_id = str(
            task_boundary(latest_mutable_boundary).get("recorded_by_agent_id", "") or ""
        ).strip()
        if boundary_agent_id:
            return boundary_agent_id
    mutable_tasks = _sort_open_tasks(
        task for task in stream_tasks
        if _is_mutable_task(task)
    )
    for task in mutable_tasks:
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if agent_id:
            return agent_id
    historical_tasks = _sort_tasks(
        task for task in stream_tasks
        if _is_mutable_task(task)
    )
    for task in reversed(historical_tasks):
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if agent_id:
            return agent_id
    return ""


def _is_mutable_task(task) -> bool:
    return (
        bool(task)
        and not _is_visibility_task(task)
        and not _is_review_task(task)
        and not _is_validation_task(task)
    )


def _compute_validation_state(tasks: list[Any]) -> tuple[str, dict]:
    latest = None
    latest_ts = -1.0

    for task in tasks:
        summary = getattr(task, "verification_summary", {}) or {}
        if not isinstance(summary, dict):
            summary = {}
        has_signal = bool(
            getattr(task, "verification_state", "")
            or getattr(task, "verification_notes", "")
            or summary
        )
        if not has_signal:
            continue
        ts = _parse_iso(getattr(task, "verification_updated_at", "") or "")
        if not ts:
            ts = _task_activity_ts(task)
        if ts >= latest_ts:
            latest_ts = ts
            latest = {
                "task_id": getattr(task, "id", "") or "",
                "verification_state": (
                    getattr(task, "verification_state", "") or ""
                ),
                "verification_notes": (
                    getattr(task, "verification_notes", "") or ""
                ),
                "summary": dict(summary),
            }

    if not latest:
        return "none", {}

    state = str(latest.get("verification_state", "") or "").strip()
    summary = latest.get("summary", {}) or {}
    notes = str(latest.get("verification_notes", "") or "").strip()
    pending_text = str(summary.get("human_validation_pending", "") or "").strip()
    combined = " ".join(part for part in (pending_text, notes) if part).lower()

    if "waiv" in combined:
        return "waived", latest
    if state == "failed":
        if pending_text or summary.get("manual_smoke_done"):
            return "pending_human_validation", latest
        return "automated_only", latest
    if state == "passed" or summary.get("manual_smoke_done"):
        return "validated", latest
    if state == "pending" or pending_text:
        return "pending_human_validation", latest
    if state == "attempted" or summary.get("tests_run") or summary.get("deploy_attempted"):
        return "automated_only", latest
    return "none", latest


def _compute_gate_reason(*, stream_state: str, blocker_task, review_task,
                         validation_state: str, validation_record: dict,
                         merge_conflict: bool, validation_tasks: list[Any],
                         branch_advanced: bool, latest_review_boundary) -> str:
    if stream_state == "merged":
        return ""
    if merge_conflict and blocker_task:
        return getattr(blocker_task, "task", "") or "Merge conflict must be resolved"
    if blocker_task:
        return getattr(blocker_task, "task", "") or "Review blockers must be fixed"
    if review_task:
        return getattr(review_task, "task", "") or "Review is in progress"
    if validation_state == "pending_human_validation":
        summary = (validation_record or {}).get("summary", {}) or {}
        pending_text = str(summary.get("human_validation_pending", "") or "").strip()
        if pending_text:
            return pending_text
        notes = str((validation_record or {}).get("verification_notes", "") or "").strip()
        if notes:
            return notes
        if validation_tasks:
            return getattr(validation_tasks[0], "task", "") or "Manual validation pending"
        return "Human or runtime validation is still pending"
    if branch_advanced and latest_review_boundary:
        return (
            "The branch has advanced beyond the latest reviewed boundary; "
            "the reviewed commit is no longer current."
        )
    return ""


def _recommended_next_action(*, stream_state: str, validation_state: str,
                             validation_tasks: list[Any], merge_conflict: bool,
                             has_open_product_tasks: bool) -> str:
    if stream_state == "merged":
        return "none"
    if merge_conflict:
        return "resolve_merge_conflict"
    if stream_state == "fixing_blockers":
        return "address_review_blockers"
    if stream_state == "reviewing":
        return "wait_for_review"
    if validation_state == "pending_human_validation":
        return "run_manual_validation" if validation_tasks else "merge_after_validation"
    if stream_state == "ready_to_merge":
        return "merge"
    if has_open_product_tasks:
        return "continue_implementation"
    return "continue_implementation"


def _recent_visibility_items(tasks: list[Any], *, limit: int = 10) -> list[dict]:
    items = []
    for task in tasks:
        if not _is_visibility_task(task):
            continue
        task_items = []
        for message in getattr(task, "messages", []) or []:
            action = str(message.get("action", "") or "").strip()
            if action not in {"weaver_message", "reply"}:
                continue
            kind = "agent_reply" if action == "reply" else "weaver_message"
            summary = _summarize_visibility_message(
                str(message.get("message", "") or "").strip()
            )
            task_items.append({
                "kind": kind,
                "task_id": getattr(task, "id", "") or "",
                "task_title": getattr(task, "task", "") or "",
                "summary": summary,
                "timestamp": _timestamp_to_iso(message.get("timestamp")),
                "_ts": _numeric_timestamp(message.get("timestamp")),
            })

        if not task_items:
            created = _parse_iso(getattr(task, "created_at", "") or "")
            task_items.append({
                "kind": "weaver_message",
                "task_id": getattr(task, "id", "") or "",
                "task_title": getattr(task, "task", "") or "",
                "summary": _summarize_visibility_message(
                    str(getattr(task, "description", "") or getattr(task, "task", "") or "")
                ),
                "timestamp": _timestamp_to_iso(created),
                "_ts": created,
            })

        items.extend(task_items)

    items.sort(
        key=lambda item: (
            -(item.get("_ts", 0.0) or 0.0),
            item.get("task_id", ""),
            item.get("kind", ""),
        )
    )
    trimmed = items[:max(0, int(limit))]
    for item in trimmed:
        item.pop("_ts", None)
    return trimmed


def _summarize_visibility_message(message: str, *, limit: int = 72) -> str:
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    summary = lines[0] if lines else str(message or "").strip()
    if not summary:
        return "Communication"
    if len(summary) <= limit:
        return summary
    return summary[:limit - 1].rstrip() + "…"


def _is_merged_stream(boundary_tasks: list[Any], state, *,
                      repo_root: str, branch: str) -> bool:
    if boundary_tasks:
        latest = task_boundary(boundary_tasks[-1])
        if latest.get("status", "") == "merged":
            return True
    for cell in state.agents.values():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if stream_identity_for_agent(cell) != (repo_root, branch):
            continue
        if bool(getattr(cell, "worktree_merged", False)):
            return True
    return False


def _stream_group(stream_tasks: list[Any], owner_agent) -> str:
    if owner_agent and getattr(owner_agent, "group", ""):
        return owner_agent.group
    counts: dict[str, int] = defaultdict(int)
    for task in stream_tasks:
        group = str(getattr(task, "group", "") or "").strip()
        if group:
            counts[group] += 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _sort_tasks(tasks) -> list[Any]:
    ordered = list(tasks)
    ordered.sort(
        key=lambda task: (
            _task_canonical_order(task),
            getattr(task, "created_at", "") or "",
            getattr(task, "id", "") or "",
        )
    )
    return ordered


def _sort_open_tasks(tasks) -> list[Any]:
    ordered = list(tasks)
    ordered.sort(
        key=lambda task: (
            _lane_priority(getattr(task, "lane", "")),
            -_task_activity_ts(task),
            _task_canonical_order(task),
            getattr(task, "id", "") or "",
        )
    )
    return ordered


def _task_canonical_order(task) -> tuple[int, int, str]:
    parsed = parse_task_id(getattr(task, "id", "") or "")
    if not parsed:
        return (10**9, 10**9, getattr(task, "id", "") or "")
    child = parsed.get("child_number")
    return (
        int(parsed.get("root_number", 10**9)),
        int(child if child is not None else 0),
        getattr(task, "id", "") or "",
    )


def _lane_priority(lane: str) -> int:
    if lane == "In Progress":
        return 0
    if lane == "To Do":
        return 1
    if lane == "Backlog":
        return 2
    return 3


def _task_text(task) -> str:
    return " ".join(
        part.strip().lower()
        for part in (
            str(getattr(task, "task", "") or ""),
            str(getattr(task, "description", "") or ""),
            str(getattr(task, "action_name", "") or ""),
            str(getattr(task, "status", "") or ""),
        )
        if part and part.strip()
    )


def _task_activity_ts(task) -> float:
    timestamps = [
        _parse_iso(getattr(task, "updated_at", "") or ""),
        _parse_iso(getattr(task, "created_at", "") or ""),
    ]
    for message in getattr(task, "messages", []) or []:
        timestamps.append(_numeric_timestamp(message.get("timestamp")))
    timestamps = [ts for ts in timestamps if ts]
    return max(timestamps) if timestamps else 0.0


def _latest_activity_iso(tasks: list[Any], agents: list[Any]) -> str:
    latest = 0.0
    for task in tasks:
        latest = max(latest, _task_activity_ts(task))
    for cell in agents:
        latest = max(latest, float(getattr(cell, "last_event_at", 0.0) or 0.0))
    return _timestamp_to_iso(latest)


def _parse_iso(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _numeric_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _parse_iso(value)
    return 0.0


def _timestamp_to_iso(value) -> str:
    ts = _numeric_timestamp(value)
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
