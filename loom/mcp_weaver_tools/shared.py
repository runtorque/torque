"""Shared helpers for Weaver MCP tools."""

from __future__ import annotations

from ..state import board_task_counts_as_done
from ..worktree_boundaries import (
    latest_boundary_task,
    queued_successor_tasks,
    started_successor_tasks,
    task_boundary,
)


def resolve_task(state, identifier: str) -> str | None:
    """Resolve a task by ID, legacy alias, or ID prefix."""
    if not identifier:
        return None
    if identifier in state.board_tasks:
        return identifier
    aliased = state.resolve_task_alias(identifier)
    if aliased in state.board_tasks:
        return aliased
    matches = [task.id for task in state.board_tasks.values()
               if task.id.startswith(identifier)]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_agent(state, identifier: str) -> str | None:
    """Resolve an agent by ID, slug, or name (case-insensitive)."""
    if not identifier:
        return None
    if identifier in state.agents:
        cell = state.agents[identifier]
        if cell.cell_type == "agent":
            return cell.id
    ident_lower = identifier.lower()
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.slug == ident_lower:
            return cell.id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.name.lower() == ident_lower:
            return cell.id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.id.startswith(identifier):
            return cell.id
    return None


def is_busy_agent(state, agent_id: str) -> bool:
    """Return True when the agent has a live current task."""
    cell = state.agents.get(agent_id)
    if not cell or cell.cell_type != "agent":
        return False
    return state.agent_is_busy(agent_id)


def active_worker_ids(state, group: str) -> set[str]:
    """Count active non-weaver agents for a group."""
    active: set[str] = set()
    weaver_id = state.get_group_settings(group).weaver_agent_id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.group != group:
            continue
        if cell.id == weaver_id:
            continue
        if is_busy_agent(state, cell.id):
            active.add(cell.id)
    return active


def blocked_dependency_titles(state, task) -> list[str]:
    return [
        state.board_tasks[dependency_id].task[:40]
        for dependency_id in task.depends_on
        if dependency_id in state.board_tasks
        and not board_task_counts_as_done(state.board_tasks[dependency_id])
    ]


def format_worktree_conflicts(conflicts) -> str:
    lines = []
    for conflict in conflicts or []:
        if isinstance(conflict, dict):
            path = str(conflict.get("path", "")).strip()
            reason = str(conflict.get("reason", "")).strip()
            detail = str(conflict.get("detail", "")).strip()
            if detail and detail not in reason:
                reason = f"{reason} — {detail}" if reason else detail
            if path and reason:
                lines.append(f"  - {path} ({reason})")
            elif path:
                lines.append(f"  - {path}")
            elif reason:
                lines.append(f"  - {reason}")
            else:
                lines.append("  - (unknown conflict)")
            continue
        if conflict:
            lines.append(f"  - {conflict}")
    if not lines:
        lines.append("  - (no file details reported)")
    return "\n".join(lines)


async def run_worktree_merge_check(handle_command, agent_id: str):
    return await run_worktree_merge_check_with_options(
        handle_command,
        agent_id,
        allow_dirty=False,
    )


async def run_worktree_merge_check_with_options(handle_command, agent_id: str, *,
                                                allow_dirty: bool = False,
                                                allow_stale_base: bool = False):
    payload = {
        "cmd": "worktree_check_merge",
        "id": agent_id,
    }
    if allow_stale_base:
        payload["allow_stale_base"] = True
    result = await handle_command(payload)
    if result and result.get("type") == "error":
        return result, result.get("message", "Unknown error"), True
    result = result or {}
    if result.get("dirty") and not allow_dirty:
        return (
            result,
            "Worktree has uncommitted changes. Create a checkpoint or "
            "commit them before retrying.",
            True,
        )
    if result.get("stale_base") and not allow_stale_base:
        return (
            result,
            result.get("stale_base_warning")
            or result.get("error")
            or "Stale base — rebase before merge.",
            True,
        )
    if not result.get("clean", True) and result.get("error"):
        return result, result.get("error", "Merge blocked"), True
    return result, "", False


def worktree_boundary_overview(state, *, repo_root: str, branch: str):
    if not repo_root or not branch:
        return None
    latest = latest_boundary_task(
        state.board_tasks.values(),
        repo_root=repo_root,
        branch=branch,
        statuses={"open"},
    )
    if not latest:
        return None
    queued = queued_successor_tasks(state.board_tasks.values(), latest.id)
    started = started_successor_tasks(state.board_tasks.values(), latest.id)
    boundary = task_boundary(latest)
    return {
        "repo_root": repo_root,
        "branch": branch,
        "latest_boundary_task_id": latest.id,
        "latest_boundary_task": latest.task,
        "latest_boundary_status": boundary.get("status", "") or "",
        "latest_boundary_recorded_at": boundary.get("recorded_at", "") or "",
        "latest_boundary_commit_sha": boundary.get("commit_sha", "") or "",
        "queued_followups": [
            {"id": task.id, "title": task.task, "lane": task.lane}
            for task in queued
        ],
        "started_followups": [
            {"id": task.id, "title": task.task, "lane": task.lane}
            for task in started
        ],
        "queued_count": len(queued),
        "started_count": len(started),
        "branch_advanced": bool(started),
        "partial_review_safe": not started,
    }
