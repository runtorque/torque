"""Helpers for task-scoped worktree merge boundaries."""

from __future__ import annotations

from typing import Iterable


def branch_key(repo_root: str, branch: str) -> str:
    return f"{repo_root.strip()}::{branch.strip()}"


def task_boundary(task) -> dict:
    boundary = getattr(task, "worktree_boundary", {}) or {}
    return boundary if isinstance(boundary, dict) else {}


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
