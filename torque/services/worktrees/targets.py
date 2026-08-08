"""Worktree orchestration: targets."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ...artifacts import normalize_artifacts
from ...board_sync import get_provider as get_board_sync_provider
from ...commands.board import _resolve_task_id
from ...config import log
from ...server_artifacts import store_preserved_merge_diff
from ...server_dispatch import (
    _capture_auto_resume_targets,
    _maybe_auto_resume_targets,
    _pump_auto_dispatch_queue,
)
from ...server_worktrees import (
    WorktreeCommandTarget,
    _append_pr_url_to_squash_body,
    _collect_linked_github_issues,
    _generate_merge_message,
    _pr_merge_failure_allows_auto,
    _pr_result_metadata,
    _record_pr_metadata_on_latest_boundary,
    _rewrite_pr_torque_task_refs_metadata,
    _split_merge_message_for_pr,
    _worktree_merge_diff_snapshot,
)
from ...state import (
    ARCHIVED_LANE,
    BoardTask,
    MatrixState,
    merge_cleanup_flags,
    normalize_engineer_merge_mode,
    task_counts_as_done,
    task_is_closed,
)
from ...worktree import (
    ExistingWorktreeTarget,
    WorktreeManager,
    build_diff_scope_context,
    format_stale_base_warning,
    stale_base_post_rebase_evidence_template,
)
from ...worktree_boundaries import (
    boundary_pr_metadata,
    branch_boundary_tasks,
    latest_boundary_base_branch,
    latest_boundary_task,
    task_boundary,
)
from ...worktree_streams import compute_worktree_stream

from .runtime import (
    _worktree_entry_matches_agent,
    _worktree_path_contains,
)

def _worktree_merge_preserve_diff_enabled(
    state: MatrixState,
    cell,
    data: dict,
) -> bool:
    if "preserve_merge_diff" in data:
        return bool(data.get("preserve_merge_diff"))
    if not cell:
        return False
    return bool(
        state.get_group_settings(cell.group).worktree_merge_preserve_diff
    )


def _worktree_merge_auto_done_candidate(
    state: MatrixState,
    cell,
) -> tuple[BoardTask | None, str]:
    """Return the sole active linked task eligible for merge auto-Done."""
    if not state or not cell:
        return None, "missing state or agent"

    linked_tasks = [
        task
        for task in state.board_tasks.values()
        if task.agent_id == cell.id and not task_is_closed(task)
    ]
    if len(linked_tasks) != 1:
        return None, f"{len(linked_tasks)} open linked tasks"

    task = linked_tasks[0]
    if task.lane in {"Backlog", "To Do"}:
        return None, f"sole linked task {task.id} is still queued in {task.lane}"
    return task, ""


def _maybe_auto_move_merged_task_to_done(
    state: MatrixState,
    cell,
    *,
    enabled: bool,
) -> dict:
    """Move a sole active linked task to Done after a successful merge."""
    decision = {"moved": False, "task_id": "", "reason": ""}

    if not enabled:
        decision["reason"] = "disabled by caller"
    else:
        task, reason = _worktree_merge_auto_done_candidate(state, cell)
        if not task:
            decision["reason"] = reason or "no eligible linked task"
        elif state.task_has_unresolved_descendants(task.id):
            decision["reason"] = (
                f"task {task.id} still has unresolved descendants"
            )
        elif task_counts_as_done(task):
            decision["reason"] = f"task {task.id} already counts as done"
        else:
            gate_result = state.board_move_task(task.id, "Done")
            if not task_counts_as_done(task):
                missing = (gate_result or {}).get("missing_gates", []) if isinstance(gate_result, dict) else []
                decision["reason"] = "finalization gates block Done" + (
                    ": " + ", ".join(missing[:3]) if missing else ""
                )
            else:
                if task.status:
                    task.status = ""
                    task.updated_at = datetime.now(timezone.utc).isoformat()
                    state.emit_task_upsert(task)
                    state._db_save_task(task)
                state.history_complete_task(cell.id, task.id, "done")
                decision.update({
                    "moved": True,
                    "task_id": task.id,
                    "reason": "moved sole linked task to Done",
                })

    if decision["moved"]:
        log.info(
            "Merge auto-Done moved task %s for '%s'",
            decision["task_id"],
            getattr(cell, "name", ""),
        )
    else:
        log.info(
            "Merge auto-Done skipped for '%s': %s",
            getattr(cell, "name", ""),
            decision["reason"] or "no-op",
        )
    return decision


def _worktree_merge_error(aid: str, message: str, **extra) -> dict:
    result = {
        "type": "worktree_merge",
        "id": aid,
        "ok": False,
        "error": message,
    }
    result.update(extra)
    return result


def _target_from_cell(cell) -> WorktreeCommandTarget | None:
    if not cell:
        return None
    return WorktreeCommandTarget(
        id=str(getattr(cell, "id", "") or ""),
        name=str(getattr(cell, "name", "") or getattr(cell, "id", "") or ""),
        group=str(getattr(cell, "group", "") or ""),
        worktree_path=str(getattr(cell, "worktree_path", "") or ""),
        worktree_branch=str(getattr(cell, "worktree_branch", "") or ""),
        worktree_repo_root=str(
            getattr(cell, "worktree_repo_root", "")
            or getattr(cell, "git_root", "")
            or ""
        ),
        worktree_base_branch=str(getattr(cell, "worktree_base_branch", "") or ""),
        git_root=str(getattr(cell, "git_root", "") or getattr(cell, "worktree_repo_root", "") or ""),
        slug=str(getattr(cell, "slug", "") or ""),
        worktree_merge_squash=bool(getattr(cell, "worktree_merge_squash", True)),
        worktree_checkpoints=int(getattr(cell, "worktree_checkpoints", 0) or 0),
        worktree_dirty=bool(getattr(cell, "worktree_dirty", False)),
        worktree_diff=dict(getattr(cell, "worktree_diff", {}) or {}),
        worktree_changed_files=list(getattr(cell, "worktree_changed_files", []) or []),
        worktree_ahead=int(getattr(cell, "worktree_ahead", 0) or 0),
        worktree_behind=int(getattr(cell, "worktree_behind", 0) or 0),
        worktree_merged=bool(getattr(cell, "worktree_merged", False)),
        current_task_id=str(getattr(cell, "current_task_id", "") or ""),
        source_agent_id=str(getattr(cell, "id", "") or ""),
        driverless=False,
        cell=cell,
    )


def _target_from_existing_worktree(
        target: ExistingWorktreeTarget, *, group: str = "") -> WorktreeCommandTarget:
    return WorktreeCommandTarget(
        id=f"driverless:{target.branch}",
        name=f"driverless:{target.branch}",
        group=group,
        worktree_path=target.worktree_path,
        worktree_branch=target.branch,
        worktree_repo_root=target.repo_root,
        worktree_base_branch=target.base_branch,
        git_root=target.git_root or target.repo_root,
        slug=f"driverless-{target.branch.replace('/', '-')}",
        worktree_merge_squash=True,
        worktree_dirty=target.is_dirty,
        worktree_diff={},
        worktree_changed_files=[],
        source_agent_id="",
        driverless=True,
        cell=None,
    )


def _target_has_driverless_payload(data: dict) -> bool:
    return bool(str(data.get("worktree_path", "") or "").strip()) \
        or bool(str(data.get("branch", "") or data.get("worktree_branch", "") or "").strip())


def _target_branch_from_payload(data: dict) -> str:
    return str(data.get("branch", "") or data.get("worktree_branch", "") or "").strip()


async def _reconcile_worktree_branch(state: MatrixState, worktree_mgr,
                                     cell) -> bool:
    """Sync a cell's cached worktree branch with its live HEAD, then persist.

    Called at the head of merge/rebase/diff command handling so a worker
    reused across tasks (now checked out on a fresh branch) is resolved to
    its current branch instead of the stale original. Returns True when the
    cached field changed.
    """
    if not cell or not getattr(cell, "worktree_path", ""):
        return False
    reconcile = getattr(worktree_mgr, "reconcile_worktree_branch", None)
    if not callable(reconcile):
        return False
    try:
        changed = await reconcile(cell)
    except Exception:
        log.exception(
            "Failed to reconcile worktree branch for '%s'",
            getattr(cell, "name", "") or getattr(cell, "id", ""),
        )
        return False
    if changed:
        state._emit_agent(cell)
        state._db_save_agent(cell)
    return changed


def _active_agent_owning_worktree_target_for_state(
        state: MatrixState,
        repo_root: str,
        path: str,
        branch: str = ""):
    repo_root = str(repo_root or "").strip()
    path = str(path or "").strip()
    branch = str(branch or "").strip()
    if not state:
        return None
    for agent in state.iter_active_agents():
        if state.agent_is_tombstoned(agent):
            continue
        status = str(getattr(agent, "status", "") or "").strip().lower()
        active = status not in {"", "stopped", "error"} or bool(
            getattr(agent, "session_id", None) and status != "stopped"
        )
        if not active:
            continue
        if _worktree_entry_matches_agent(repo_root, path, agent):
            return agent
        if branch and branch == str(getattr(agent, "worktree_branch", "") or "").strip():
            return agent
    return None


async def _resolve_worktree_command_target_value(
        *,
        state: MatrixState,
        worktree_mgr: WorktreeManager,
        data: dict,
        require_base: bool = False,
        reject_active_owner: bool = False,
        group: str = ""):
    """Return (target, cell, error_result) for live-agent or path+branch mode."""
    data = data or {}
    aid = str(data.get("id", "") or "").strip()
    has_path_target = _target_has_driverless_payload(data)
    if aid and has_path_target:
        return None, None, {
            "type": "error",
            "message": "Specify either id or worktree_path+branch, not both.",
        }
    if aid:
        cell = state.agents.get(aid)
        if not cell:
            return None, None, {
                "type": "error",
                "message": f"Agent/worktree not found: {aid}",
            }
        if state.agent_is_tombstoned(cell):
            return None, None, {
                "type": "error",
                "message": f"Agent/worktree is tombstoned: {aid}",
            }
        await _reconcile_worktree_branch(state, worktree_mgr, cell)
        target = _target_from_cell(cell)
        return target, cell, None
    if not has_path_target:
        return None, None, {
            "type": "error",
            "message": "Agent id or worktree_path+branch is required.",
        }
    worktree_path = str(data.get("worktree_path", "") or "").strip()
    branch = _target_branch_from_payload(data)
    if not worktree_path or not branch:
        return None, None, {
            "type": "error",
            "message": "worktree_path and branch are required.",
        }
    repo_root = str(data.get("repo_root", "") or "").strip()
    base_branch = str(data.get("base_branch", "") or "").strip()
    if not base_branch and repo_root:
        base_branch = latest_boundary_base_branch(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=branch,
            statuses={"open", "merged", "superseded"},
        )
    if not base_branch:
        # Validate once to resolve repo root, then consult boundary/group defaults.
        try:
            provisional = await worktree_mgr.validate_existing_worktree(
                worktree_path,
                repo_root=repo_root,
                branch=branch,
            )
            repo_root = provisional.repo_root
        except ValueError as exc:
            return None, None, {"type": "error", "message": str(exc)}
        base_branch = latest_boundary_base_branch(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=branch,
            statuses={"open", "merged", "superseded"},
        )
    if not base_branch:
        if group:
            base_branch = str(
                getattr(state.get_group_settings(group), "worktree_base_branch", "")
                or ""
            ).strip()
        if not base_branch:
            base_branch = "main"
    if require_base and not base_branch:
        return None, None, {"type": "error", "message": "base_branch is required."}
    try:
        existing = await worktree_mgr.validate_existing_worktree(
            worktree_path,
            repo_root=repo_root,
            branch=branch,
            base_branch=base_branch,
            worktree_submodules=(
                list(getattr(state.get_group_settings(group), "worktree_submodules", []) or [])
                if group else None
            ),
        )
    except ValueError as exc:
        return None, None, {"type": "error", "message": str(exc)}
    caller_kind = str(data.get("caller_kind", "") or "").strip()
    caller_id = str(data.get("caller_id", "") or "").strip()
    if caller_kind == "engineer" and caller_id:
        base_dir = ".torque/worktrees"
        try:
            base_dir = str(
                getattr(state.get_group_settings(group), "worktree_base_dir", "")
                or ".torque/worktrees"
            ).strip() or ".torque/worktrees"
        except Exception:
            base_dir = ".torque/worktrees"
        allowed_root = base_dir if os.path.isabs(base_dir) else os.path.join(
            existing.repo_root,
            base_dir,
        )
        if not _worktree_path_contains(allowed_root, existing.worktree_path):
            return None, None, {
                "type": "error",
                "message": "driverless worktree path is outside the configured Torque worktree directory",
            }
        latest = latest_boundary_task(
            state.board_tasks.values(),
            repo_root=existing.repo_root,
            branch=existing.branch,
            statuses={"open", "merged", "superseded"},
        )
        if latest:
            assigned = str(getattr(latest, "assigned_engineer_id", "") or "").strip()
            if assigned and assigned != caller_id:
                return None, None, {
                    "type": "error",
                    "message": "branch boundary is outside engineer scope",
                }
        else:
            caller = state.agents.get(caller_id)
            slug = str(getattr(caller, "slug", "") or "").strip()
            if not (
                    (slug and existing.branch.startswith(f"torque/{slug}/"))
                    or existing.branch.startswith("torque/user/")
            ):
                return None, None, {
                    "type": "error",
                    "message": "no visible boundary or owned branch prefix for driverless target",
                }
    owner = _active_agent_owning_worktree_target_for_state(
        state,
        existing.repo_root,
        existing.worktree_path,
        existing.branch,
    )
    if reject_active_owner and owner:
        return None, None, {
            "type": "error",
            "message": f"Worktree is already owned by active agent {owner.name or owner.id}.",
            "owner_agent_id": owner.id,
        }
    return _target_from_existing_worktree(existing, group=group), None, None


def _engineer_merge_mode_for_cell(state: MatrixState, cell) -> str:
    if not cell:
        return "pr"
    return normalize_engineer_merge_mode(
        getattr(
            state.get_group_settings(getattr(cell, "group", "")),
            "engineer_merge_mode",
            "pr",
        )
    )


def _github_pr_closing_refs_enabled(group_settings) -> bool:
    """Return whether GitHub linked issues should close through PR bodies."""
    provider = str(
        getattr(group_settings, "board_sync_provider", "none") or "none"
    ).strip().lower()
    if provider != "github":
        return False
    github_settings = getattr(group_settings, "board_sync_github", {}) or {}
    if not isinstance(github_settings, dict):
        return True
    return bool(github_settings.get("github_close_issues_via_pr", True))


def _add_task_with_pipeline_relatives(
    *,
    state: MatrixState,
    task_ids: set[str],
    task_id: str,
) -> None:
    """Add a task plus parent/root relatives to ``task_ids``."""
    task_id = str(task_id or "").strip()
    if not task_id or task_id not in state.board_tasks:
        return

    current_id = task_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        current = state.board_tasks.get(current_id)
        if not current:
            break
        task_ids.add(current_id)
        parent_id = str(getattr(current, "parent_task_id", "") or "").strip()
        if parent_id:
            current_id = parent_id
            continue
        break

    current = state.board_tasks.get(task_id)
    root_id = str(getattr(current, "pipeline_root_id", "") or "").strip() \
        if current else ""
    if root_id and root_id in state.board_tasks:
        task_ids.add(root_id)


def _active_pr_closing_ref_tasks(state: MatrixState, cell) -> list[BoardTask]:
    """Return branch/product/boundary tasks whose GitHub issues this PR closes."""
    if not state or not cell:
        return []

    task_ids: set[str] = set()
    current_id = str(getattr(cell, "current_task_id", "") or "").strip()
    if current_id:
        _add_task_with_pipeline_relatives(
            state=state,
            task_ids=task_ids,
            task_id=current_id,
        )

    current = state.agent_current_task(getattr(cell, "id", ""))
    if current:
        _add_task_with_pipeline_relatives(
            state=state,
            task_ids=task_ids,
            task_id=getattr(current, "id", ""),
        )

    for task in state.board_tasks.values():
        if getattr(task, "agent_id", "") != getattr(cell, "id", ""):
            continue
        if getattr(task, "lane", "") in {"Backlog", "To Do", ARCHIVED_LANE}:
            continue
        _add_task_with_pipeline_relatives(
            state=state,
            task_ids=task_ids,
            task_id=getattr(task, "id", ""),
        )

    repo_root = str(
        getattr(cell, "worktree_repo_root", "")
        or getattr(cell, "git_root", "")
        or ""
    ).strip()
    branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    if repo_root and branch:
        for task in branch_boundary_tasks(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=branch,
            statuses={"open"},
        ):
            _add_task_with_pipeline_relatives(
                state=state,
                task_ids=task_ids,
                task_id=getattr(task, "id", ""),
            )

        try:
            stream = compute_worktree_stream(
                state,
                repo_root=repo_root,
                branch=branch,
                group=getattr(cell, "group", "") or "",
                stream_agent_ids={getattr(cell, "id", "")},
                branch_exists_cache={
                    (os.path.realpath(os.path.expanduser(repo_root)), branch):
                    True
                },
            ) or {}
        except Exception:
            log.exception(
                "Failed to compute worktree stream for PR closing refs "
                "on branch '%s'",
                branch,
            )
            stream = {}
        queued_ids = {
            str(task_id or "").strip()
            for task_id in stream.get("queued_task_ids", []) or []
            if str(task_id or "").strip()
        }
        stream_ids = set()
        for key in (
            "product_task_ids",
            "started_task_ids",
            "workflow_task_ids",
        ):
            stream_ids.update(
                str(task_id or "").strip()
                for task_id in stream.get(key, []) or []
                if str(task_id or "").strip()
            )
        for key in ("foreground_task_id", "latest_boundary_task_id"):
            task_id = str(stream.get(key, "") or "").strip()
            if task_id:
                stream_ids.add(task_id)
        for task_id in stream_ids - queued_ids:
            _add_task_with_pipeline_relatives(
                state=state,
                task_ids=task_ids,
                task_id=task_id,
            )

    ordered = []
    for task in state.board_tasks.values():
        if getattr(task, "id", "") in task_ids:
            ordered.append(task)
    return ordered


def _linked_github_issues_for_pr(
    state: MatrixState,
    cell,
    *,
    base_repo: str = "",
) -> list[dict]:
    """Collect de-duplicated linked GitHub issues for a PR merge."""
    tasks = _active_pr_closing_ref_tasks(state, cell)
    return _collect_linked_github_issues(tasks, base_repo=base_repo)


async def _append_github_closing_refs_to_pr_body(
    *,
    body: str,
    linked_issues: list[dict],
    group_settings,
) -> str:
    """Append provider-rendered GitHub closing refs to a PR body."""
    if not linked_issues:
        return body or ""
    provider = get_board_sync_provider("github")
    return await provider.append_closing_refs(
        body or "",
        linked_issues,
        group_settings,
    )


def _log_pr_task_ref_rewrite(context: str, diagnostics: dict | None) -> None:
    """Log PR task-ref rewrite diagnostics without leaking PR body text."""
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    replaced = diagnostics.get("replaced", []) or []
    unresolved = diagnostics.get("unresolved", []) or []
    replaced_refs = sorted({
        (
            f"{str(item.get('raw_task_id') or item.get('task_id') or '').strip()}"
            f"->{str(item.get('ref') or '').strip()}"
        )
        for item in replaced
        if isinstance(item, dict)
        and str(item.get("raw_task_id") or item.get("task_id") or "").strip()
        and str(item.get("ref") or "").strip()
    })
    unresolved_ids = sorted({
        str(item.get("task_id") or "").strip()
        for item in unresolved
        if isinstance(item, dict) and str(item.get("task_id") or "").strip()
    })
    if replaced_refs:
        log.info(
            "Rewrote Torque task refs in %s PR metadata: %s",
            context,
            ", ".join(replaced_refs),
        )
    if unresolved_ids:
        log.info(
            "Left unresolved Torque task refs unchanged in %s PR metadata: %s",
            context,
            ", ".join(unresolved_ids),
        )
