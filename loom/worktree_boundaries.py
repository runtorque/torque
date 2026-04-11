"""Helpers for task-scoped worktree merge boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable


def branch_key(repo_root: str, branch: str) -> str:
    return f"{repo_root.strip()}::{branch.strip()}"


def task_boundary(task) -> dict:
    boundary = getattr(task, "worktree_boundary", {}) or {}
    return boundary if isinstance(boundary, dict) else {}


def task_boundary_status(task) -> str:
    return str(task_boundary(task).get("status", "") or "")


def task_has_open_boundary(task) -> bool:
    return task_boundary_status(task) == "open"


def task_branch_key(task) -> str:
    boundary = task_boundary(task)
    repo_root = boundary.get("repo_root", "")
    branch = boundary.get("branch", "")
    if not repo_root or not branch:
        return ""
    return branch_key(repo_root, branch)


def boundary_sort_key(task) -> tuple[str, str]:
    boundary = task_boundary(task)
    return (
        str(boundary.get("recorded_at", "") or ""),
        str(getattr(task, "updated_at", "") or ""),
    )


def branch_boundary_tasks(tasks: Iterable, *,
                          repo_root: str,
                          branch: str,
                          statuses: set[str] | None = None) -> list:
    wanted = branch_key(repo_root, branch)
    matched = []
    for task in tasks:
        if task_branch_key(task) != wanted:
            continue
        if statuses:
            status = task_boundary(task).get("status", "") or ""
            if status not in statuses:
                continue
        matched.append(task)
    matched.sort(key=boundary_sort_key)
    return matched


def latest_boundary_task(tasks: Iterable, *,
                         repo_root: str,
                         branch: str,
                         statuses: set[str] | None = None):
    matched = branch_boundary_tasks(
        tasks, repo_root=repo_root, branch=branch, statuses=statuses
    )
    if not matched:
        return None
    return matched[-1]


def successor_tasks(tasks: Iterable, boundary_task_id: str) -> list:
    matched = [
        task for task in tasks
        if getattr(task, "resume_after_boundary_task_id", "") == boundary_task_id
    ]
    matched.sort(
        key=lambda task: (
            str(getattr(task, "created_at", "") or ""),
            str(getattr(task, "updated_at", "") or ""),
        )
    )
    return matched


def queued_successor_tasks(tasks: Iterable, boundary_task_id: str) -> list:
    return [
        task for task in successor_tasks(tasks, boundary_task_id)
        if getattr(task, "lane", "") in {"Backlog", "To Do"}
    ]


def started_successor_tasks(tasks: Iterable, boundary_task_id: str) -> list:
    return [
        task for task in successor_tasks(tasks, boundary_task_id)
        if getattr(task, "lane", "") not in {"Backlog", "To Do"}
    ]


def retarget_queued_successor_tasks(tasks: Iterable, *,
                                    agent_id: str,
                                    boundary_task_id: str,
                                    exclude_task_id: str = "") -> list:
    updated = []
    for task in tasks:
        if getattr(task, "id", "") == exclude_task_id:
            continue
        if getattr(task, "agent_id", "") != agent_id:
            continue
        if getattr(task, "lane", "") not in {"Backlog", "To Do"}:
            continue
        if getattr(task, "resume_after_boundary_task_id", "") == boundary_task_id:
            continue
        task.resume_after_boundary_task_id = boundary_task_id
        updated.append(task)
    return updated


def clear_stale_successor_references(tasks: Iterable) -> list:
    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in tasks
        if getattr(task, "id", "")
    }
    updated = []
    for task in tasks_by_id.values():
        boundary_task_id = (
            getattr(task, "resume_after_boundary_task_id", "") or ""
        )
        if not boundary_task_id:
            continue
        boundary_task = tasks_by_id.get(boundary_task_id)
        if boundary_task and task_has_open_boundary(boundary_task):
            continue
        task.resume_after_boundary_task_id = ""
        updated.append(task)
    return updated


def boundary_summary(task, *, queued_followers: list | None = None,
                     started_followers: list | None = None) -> dict:
    boundary = task_boundary(task)
    queued_followers = queued_followers or []
    started_followers = started_followers or []
    return {
        "task_id": getattr(task, "id", ""),
        "task_title": getattr(task, "task", ""),
        "task_slug": getattr(task, "slug", ""),
        "lane": getattr(task, "lane", ""),
        "boundary": dict(boundary),
        "queued_followers": [
            {
                "task_id": getattr(follower, "id", ""),
                "task_title": getattr(follower, "task", ""),
                "lane": getattr(follower, "lane", ""),
            }
            for follower in queued_followers
        ],
        "started_followers": [
            {
                "task_id": getattr(follower, "id", ""),
                "task_title": getattr(follower, "task", ""),
                "lane": getattr(follower, "lane", ""),
            }
            for follower in started_followers
        ],
    }


def mark_branch_boundaries_merged(tasks: Iterable, *,
                                  repo_root: str,
                                  branch: str,
                                  merge_sha: str,
                                  merged_at: str | None = None) -> list:
    """Mark merged branch-boundary tasks and apply the `merged` label.

    Scope is limited to boundary tasks for the merged branch whose boundary
    state was still active (`open`) or superseded by later work on that same
    branch. The pipeline root for any affected derived task is also updated so
    board labels and task-facing boundary state stay in sync. Queued or
    unrelated tasks are left untouched.
    """
    if not repo_root or not branch:
        return []

    tasks = list(tasks)
    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in tasks
        if getattr(task, "id", "")
    }
    merged_at = merged_at or datetime.now(timezone.utc).isoformat()
    updated = []
    updated_ids: set[str] = set()
    boundary_sources_by_root: dict[str, object] = {}

    def _append_merged_label(task) -> None:
        labels = list(getattr(task, "labels", []) or [])
        if "merged" not in labels:
            labels.append("merged")
            task.labels = labels

    def _mark_task_boundary_merged(task) -> None:
        boundary = dict(task_boundary(task))
        boundary["status"] = "merged"
        boundary["merged_at"] = merged_at
        boundary["merge_commit_sha"] = merge_sha
        boundary["superseded_by_task_id"] = ""
        boundary.pop("reason", None)
        task.worktree_boundary = boundary
        _append_merged_label(task)

    def _task_root_id(task) -> str:
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        if root_id:
            return root_id
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        if not parent_id:
            return str(getattr(task, "id", "") or "").strip()

        seen: set[str] = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = tasks_by_id.get(parent_id)
            if not parent:
                return parent_id
            next_parent_id = str(
                getattr(parent, "parent_task_id", "") or ""
            ).strip()
            if not next_parent_id:
                return str(getattr(parent, "id", "") or parent_id).strip()
            parent_id = next_parent_id
        return ""

    for task in branch_boundary_tasks(
            tasks,
            repo_root=repo_root,
            branch=branch,
            statuses={"open", "superseded"}):
        _mark_task_boundary_merged(task)
        updated.append(task)
        task_id = str(getattr(task, "id", "") or "").strip()
        if task_id:
            updated_ids.add(task_id)

        root_id = _task_root_id(task)
        if not root_id or root_id == task_id:
            continue
        current_source = boundary_sources_by_root.get(root_id)
        if current_source is None or boundary_sort_key(task) >= boundary_sort_key(
            current_source
        ):
            boundary_sources_by_root[root_id] = task

    for root_id, source_task in boundary_sources_by_root.items():
        if root_id in updated_ids:
            continue
        root_task = tasks_by_id.get(root_id)
        if not root_task:
            continue

        source_boundary = dict(task_boundary(source_task))
        if not source_boundary:
            continue

        boundary = dict(task_boundary(root_task))
        if not boundary:
            boundary = {
                "version": source_boundary.get("version", "1"),
                "repo_root": repo_root,
                "branch": branch,
                "base_branch": source_boundary.get("base_branch", "") or "",
                "commit_sha": source_boundary.get("commit_sha", "") or "",
                "kind": source_boundary.get("kind", "") or "",
                "status": "",
                "recorded_at": "",
                "recorded_by_agent_id": (
                    source_boundary.get("recorded_by_agent_id", "") or ""
                ),
                "message": "",
                "superseded_by_task_id": "",
                "merged_at": "",
                "merge_commit_sha": "",
            }
        else:
            if not boundary.get("version"):
                boundary["version"] = source_boundary.get("version", "1") or "1"
            boundary["repo_root"] = repo_root
            boundary["branch"] = branch
            if not boundary.get("base_branch"):
                boundary["base_branch"] = (
                    source_boundary.get("base_branch", "") or ""
                )
            if not boundary.get("commit_sha"):
                boundary["commit_sha"] = (
                    source_boundary.get("commit_sha", "") or ""
                )
            if not boundary.get("kind"):
                boundary["kind"] = source_boundary.get("kind", "") or ""
            if not boundary.get("recorded_by_agent_id"):
                boundary["recorded_by_agent_id"] = (
                    source_boundary.get("recorded_by_agent_id", "") or ""
                )
            boundary.setdefault("message", "")

        boundary["status"] = "merged"
        boundary["merged_at"] = merged_at
        boundary["merge_commit_sha"] = merge_sha
        boundary["superseded_by_task_id"] = ""
        boundary.pop("reason", None)
        root_task.worktree_boundary = boundary
        _append_merged_label(root_task)
        updated.append(root_task)
        updated_ids.add(root_id)

    return updated
