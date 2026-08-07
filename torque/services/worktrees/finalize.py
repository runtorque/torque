"""Worktree orchestration: finalize."""

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

from .evidence import _persist_preserved_merge_diff_warning_only
from .gates import _attach_stale_base, _configured_worktree_submodules_for_cell
from .preflight import (
    _append_post_success_warning,
    _base_match_from_result,
    _candidate_pr_statuses,
    _capture_worktree_merge_preserve_diff,
    _capture_worktree_merge_resume_targets,
    _merge_commit_sha_from_sources,
    _merge_commit_sha_from_status,
    _post_success_cleanup_warning,
    _post_success_result_error,
    _pr_selector_from_sources,
    _pr_status_indicates_merged,
    _preflight_worktree_merge_gates,
    _sha_equal,
    _worktree_merge_requested_cleanup,
)
from .runtime import (
    _cleanup_shipped_reviewers_for_merged_cell,
    _origin_verification_evidence,
    _record_merge_completion_evidence,
)
from .submodules import (
    _ee_pr_flow_submodules,
    _legacy_nested_submodules,
    _record_nested_submodule_metadata_on_latest_boundary,
)
from .targets import (
    _maybe_auto_move_merged_task_to_done,
    _worktree_merge_error,
)

async def _confirm_pr_merged_and_base_at_merge(
    *,
    worktree_mgr: WorktreeManager,
    worktree_path: str,
    repo_root: str,
    remote: str,
    base_branch: str,
    pr_result: dict | None = None,
    merge_result: dict | None = None,
    post_merge_sync: dict | None = None,
    force_status_query: bool = False,
    skip_base_sync: bool = False,
) -> dict:
    """Authoritative guard: PR is merged AND origin/base is the merge SHA."""
    pr_result = pr_result or {}
    merge_result = merge_result or {}
    merge_sha = _merge_commit_sha_from_sources(
        merge_result,
        pr_result,
        post_merge_sync,
    )
    status = None
    for candidate in _candidate_pr_statuses(merge_result, pr_result):
        if _pr_status_indicates_merged(candidate):
            status = candidate
            if not merge_sha:
                merge_sha = _merge_commit_sha_from_status(candidate)
            break

    selector = _pr_selector_from_sources(merge_result, pr_result)
    status_helper = getattr(worktree_mgr, "github_pr_status", None)
    if (
        force_status_query or not status or not merge_sha
    ) and callable(status_helper) and selector:
        try:
            queried = await status_helper(worktree_path, selector)
        except Exception as exc:  # pragma: no cover - defensive logging path
            log.warning(
                "Post-success PR status confirmation failed for %s: %s",
                selector,
                exc,
            )
        else:
            if queried.get("ok") and _pr_status_indicates_merged(queried):
                status = queried
                queried_sha = _merge_commit_sha_from_status(queried)
                if queried_sha:
                    merge_sha = queried_sha

    if not status or not _pr_status_indicates_merged(status) or not merge_sha:
        return {"ok": False, "reason": "pr_not_confirmed_merged"}

    match = _base_match_from_result(post_merge_sync, merge_sha)
    if match:
        return {
            "ok": True,
            "merge_commit_sha": merge_sha,
            "pr_status": status,
            "base_match": match,
        }

    sync_helper = getattr(worktree_mgr, "github_sync_remote_base", None)
    sync_result = None
    if (
        not skip_base_sync
        and callable(sync_helper)
        and repo_root
        and remote
        and base_branch
    ):
        try:
            sync_result = await sync_helper(
                worktree_path,
                repo_root or worktree_path,
                remote,
                base_branch,
            )
        except Exception as exc:  # pragma: no cover - defensive logging path
            sync_result = {
                "ok": False,
                "phase": "remote_base_sync",
                "error": str(exc),
            }
        match = _base_match_from_result(sync_result, merge_sha)
        if match:
            return {
                "ok": True,
                "merge_commit_sha": merge_sha,
                "pr_status": status,
                "base_match": match,
                "base_sync": sync_result,
            }

    remote_sha_helper = getattr(worktree_mgr, "github_remote_branch_sha", None)
    if callable(remote_sha_helper) and repo_root and remote and base_branch:
        try:
            remote_sha_result = await remote_sha_helper(
                repo_root or worktree_path,
                remote,
                base_branch,
            )
        except Exception as exc:  # pragma: no cover - defensive logging path
            log.warning(
                "Post-success remote base confirmation failed for %s/%s: %s",
                remote,
                base_branch,
                exc,
            )
            remote_sha_result = None
        if isinstance(remote_sha_result, dict):
            remote_sha = str(remote_sha_result.get("sha") or "").strip()
        else:
            remote_sha = str(remote_sha_result or "").strip()
            remote_sha_result = {"sha": remote_sha} if remote_sha else None
        if _sha_equal(remote_sha, merge_sha):
            return {
                "ok": True,
                "merge_commit_sha": merge_sha,
                "pr_status": status,
                "base_match": {
                    "source": "remote_ground_truth",
                    "remote": remote,
                    "base_branch": base_branch,
                    "sha": remote_sha,
                    "result": remote_sha_result,
                },
                "base_sync": sync_result,
            }

    rev_parse = getattr(worktree_mgr, "rev_parse", None)
    if callable(rev_parse) and base_branch:
        seen_dirs = set()
        for directory in (repo_root, worktree_path):
            directory = str(directory or "").strip()
            if not directory or directory in seen_dirs:
                continue
            seen_dirs.add(directory)
            try:
                base_sha = await rev_parse(directory, base_branch)
            except Exception as exc:  # pragma: no cover - defensive logging path
                log.warning(
                    "Post-success base ref confirmation failed for %s in %s: %s",
                    base_branch,
                    directory,
                    exc,
                )
                continue
            if _sha_equal(str(base_sha or "").strip(), merge_sha):
                return {
                    "ok": True,
                    "merge_commit_sha": merge_sha,
                    "pr_status": status,
                    "base_match": {
                        "source": "rev_parse",
                        "directory": directory,
                        "ref": base_branch,
                        "sha": str(base_sha or "").strip(),
                    },
                    "base_sync": sync_result,
                }

    return {
        "ok": False,
        "reason": "base_not_at_merge_commit",
        "merge_commit_sha": merge_sha,
        "pr_status": status,
        "base_sync": sync_result,
    }


def _post_success_guard_warning(
    failure: dict | None,
    merge_sha: str,
    *,
    base_branch: str = "",
) -> str:
    failure = failure or {}
    phase = str(failure.get("phase") or "post_success_check").strip()
    error = _post_success_result_error(failure, "post-success check failed")
    base_label = str(base_branch or "base").strip() or "base"
    return (
        "Merge landed (PR is MERGED and "
        f"{base_label} is at merge commit {merge_sha}); "
        f"ignoring post-success {phase} failure: {error}"
    )


def _fallback_successful_worktree_merge_result(
    *,
    cell,
    aid: str,
    merge_sha: str,
    stale_base: dict | None,
    cleanup_error: str = "",
) -> dict:
    cleanup = {
        "close_agent": False,
        "remove_worktree": False,
        "agent_closed": False,
        "worktree_removed": False,
        "errors": [cleanup_error] if cleanup_error else [],
    }
    result = {
        "type": "worktree_merge",
        "id": aid,
        "ok": True,
        "sha": merge_sha,
        "branch": str(getattr(cell, "worktree_branch", "") or ""),
        "base_branch": str(getattr(cell, "worktree_base_branch", "") or ""),
        "agent_name": str(getattr(cell, "name", "") or ""),
        "cleanup": cleanup,
    }
    _attach_stale_base(result, stale_base)
    if cleanup_error:
        _append_post_success_warning(
            result,
            "Merge landed, but post-merge finalization reported warnings: "
            + cleanup_error,
            phase="post_merge_finalize",
            detail={"error": cleanup_error},
        )
    return result


def _latest_merged_pr_boundary_for_post_success(
    state: MatrixState,
    *,
    aid: str = "",
    repo_root: str = "",
    branch: str = "",
) -> tuple[object | None, dict, dict]:
    """Find a merged PR boundary only when it is still the latest branch work."""
    aid = str(aid or "").strip()
    repo_root = str(repo_root or "").strip()
    branch = str(branch or "").strip()
    candidates: list[tuple[tuple[str, str], object, dict, dict]] = []
    for task in state.board_tasks.values():
        boundary = task_boundary(task)
        boundary_repo_root = str(boundary.get("repo_root") or "").strip()
        boundary_branch = str(boundary.get("branch") or "").strip()
        if repo_root and boundary_repo_root != repo_root:
            continue
        if branch and boundary_branch != branch:
            continue
        recorded_by = str(
            boundary.get("recorded_by_agent_id") or ""
        ).strip()
        task_agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if aid and aid not in {recorded_by, task_agent_id}:
            continue
        pr = boundary_pr_metadata(boundary)
        sort_key = (
            str(boundary.get("recorded_at") or ""),
            str(getattr(task, "updated_at", "") or ""),
        )
        candidates.append((sort_key, task, boundary, pr))
    if not candidates:
        return None, {}, {}
    candidates.sort(key=lambda item: item[0])
    _sort_key, task, boundary, pr = candidates[-1]
    if str(boundary.get("status") or "").strip() != "merged":
        return None, {}, {}
    merge_sha = str(
        boundary.get("merge_commit_sha")
        or pr.get("merge_commit_sha")
        or ""
    ).strip()
    if not merge_sha or not pr:
        return None, {}, {}
    return task, boundary, pr


async def _recover_authoritative_post_success_from_boundary(
    *,
    state: MatrixState,
    worktree_mgr: WorktreeManager,
    aid: str,
    failure: dict,
    cell=None,
    worktree_path: str = "",
    repo_root: str = "",
    branch: str = "",
    base_branch: str = "",
    remote: str = "",
    stale_base: dict | None = None,
) -> dict | None:
    """Return success when a later phase fails after a confirmed PR merge."""
    task, boundary, pr = _latest_merged_pr_boundary_for_post_success(
        state,
        aid=aid,
        repo_root=repo_root,
        branch=branch,
    )
    if not boundary:
        return None

    merge_sha = str(
        boundary.get("merge_commit_sha")
        or pr.get("merge_commit_sha")
        or ""
    ).strip()
    boundary_repo_root = str(boundary.get("repo_root") or "").strip()
    boundary_branch = str(boundary.get("branch") or "").strip()
    boundary_base = str(
        base_branch
        or boundary.get("base_branch")
        or pr.get("base_branch")
        or ""
    ).strip()
    boundary_remote = str(remote or pr.get("remote") or "origin").strip()
    confirm_path = str(
        worktree_path
        or getattr(cell, "worktree_path", "")
        or boundary_repo_root
        or repo_root
        or ""
    ).strip()
    confirm_repo_root = str(
        repo_root
        or getattr(cell, "worktree_repo_root", "")
        or getattr(cell, "git_root", "")
        or boundary_repo_root
        or confirm_path
        or ""
    ).strip()
    if not (
        merge_sha
        and confirm_path
        and confirm_repo_root
        and boundary_remote
        and boundary_base
    ):
        return None

    pr_result = dict(pr)
    pr_result.setdefault("state", "MERGED")
    pr_result.setdefault("merge_commit_sha", merge_sha)
    merge_result = {
        "ok": True,
        "phase": "post_success_boundary",
        "merge_commit_sha": merge_sha,
        "url": pr_result.get("url", ""),
        "number": pr_result.get("number"),
        "pr_status": pr_result,
    }
    confirmation = await _confirm_pr_merged_and_base_at_merge(
        worktree_mgr=worktree_mgr,
        worktree_path=confirm_path,
        repo_root=confirm_repo_root,
        remote=boundary_remote,
        base_branch=boundary_base,
        pr_result=pr_result,
        merge_result=merge_result,
        force_status_query=True,
    )
    if not confirmation.get("ok"):
        return None

    cleanup_requested = dict(pr.get("requested_cleanup") or {})
    close_requested = bool(cleanup_requested.get("close_agent_on_merge"))
    remove_requested = bool(cleanup_requested.get("remove_worktree_on_merge"))
    agent_name = str(
        getattr(cell, "name", "")
        or getattr(task, "task", "")
        or aid
        or ""
    ).strip()
    result = {
        "type": "worktree_merge",
        "id": aid,
        "ok": True,
        "sha": merge_sha,
        "branch": boundary_branch or branch,
        "base_branch": boundary_base,
        "agent_name": agent_name,
        "cleanup": {
            "close_agent": close_requested,
            "remove_worktree": remove_requested,
            "agent_closed": close_requested,
            "worktree_removed": remove_requested,
            "errors": [],
            "recovered_after_success": True,
        },
        "mode": "pull_request",
        "pending": False,
        "merged": True,
        "url": pr.get("url", ""),
        "pr_url": pr.get("url", ""),
        "pr": pr,
        "authoritative_post_success_guard": confirmation,
    }
    origin_verification = _origin_verification_evidence(
        merge_sha=merge_sha,
        remote=boundary_remote,
        base_branch=boundary_base,
        authoritative_guard=confirmation,
    )
    if origin_verification:
        result["origin_verification"] = origin_verification
    _attach_stale_base(result, stale_base)
    phase = str(failure.get("phase") or "post_success_check").strip()
    warning = _post_success_guard_warning(
        failure,
        merge_sha,
        base_branch=boundary_base,
    )
    log.warning(warning)
    _append_post_success_warning(
        result,
        warning,
        phase=phase,
        detail={
            "failure": failure,
            "confirmation": confirmation,
            "boundary_task_id": str(getattr(task, "id", "") or ""),
        },
    )
    _record_merge_completion_evidence(
        state,
        result=result,
        task_ids=[str(getattr(task, "id", "") or "")],
        cell=cell,
        repo_root=boundary_repo_root or confirm_repo_root,
        branch=boundary_branch or branch,
        base_branch=boundary_base,
        remote=boundary_remote,
        origin_verification=origin_verification,
    )
    return result


async def _resolve_already_merged_sha(
    *,
    worktree_mgr: WorktreeManager,
    cell,
    repo_root: str,
    worktree_path: str,
    base_branch: str,
    pr_result: dict | None = None,
) -> str:
    """Best-effort landed SHA for an already-merged/no-op PR result."""
    pr_result = pr_result or {}
    for key in ("merge_commit_sha", "base_sha"):
        sha = str(pr_result.get(key) or "").strip()
        if sha:
            return sha
    rev_parse = getattr(worktree_mgr, "rev_parse", None)
    if callable(rev_parse):
        for directory in (repo_root, worktree_path):
            directory = str(directory or "").strip()
            if not directory:
                continue
            sha = await rev_parse(directory, base_branch)
            if sha:
                return str(sha).strip()
    current_head = getattr(worktree_mgr, "current_head", None)
    if callable(current_head):
        sha = await current_head(cell)
        if sha:
            return str(sha).strip()
    return str(pr_result.get("head_sha") or "").strip()


async def _finalize_successful_worktree_merge(
    *,
    state: MatrixState,
    cell,
    aid: str,
    data: dict,
    merge_sha: str,
    merged_task_ids: tuple[str, ...],
    stale_base: dict | None,
    preserve_merge_diff: bool,
    boundary_task_for_diff,
    merge_diff_snapshot: dict | None,
    merge_resume_targets: list[dict] | None,
    mark_branch_boundaries_merged,
    cleanup_after_merge,
    broadcast_toast,
    bridge,
    worktree_mgr: WorktreeManager,
    handle_command,
    panel_event,
    board_sync_manager=None,
) -> dict:
    """Apply all local side effects after a direct or PR merge succeeds."""
    merge_branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    merge_base_branch = str(
        getattr(cell, "worktree_base_branch", "") or ""
    ).strip()
    merge_agent_name = str(getattr(cell, "name", "") or "").strip()
    mark_branch_boundaries_merged(cell, merge_sha, merged_task_ids)
    state.cleanup_stale_boundary_successors()
    preserve_diff_warning = ""
    if preserve_merge_diff:
        preserve_diff_warning = (
            _persist_preserved_merge_diff_warning_only(
                state,
                cell,
                boundary_task_for_diff,
                merge_diff_snapshot,
                merge_commit_sha=merge_sha,
            )
        )
    cell.worktree_checkpoints = 0
    cell.worktree_merged = True
    cell.worktree_changed_files = []
    state.history_update_agent(cell, status="merged")
    state._emit_agent(cell)
    await broadcast_toast(
        f'"{cell.name}" merged to {cell.worktree_base_branch}',
        "success",
    )
    if preserve_diff_warning:
        await broadcast_toast(preserve_diff_warning, "warning")

    reviewer_cleanup = (
        await _cleanup_shipped_reviewers_for_merged_cell(
            state,
            cell,
            cleanup_after_merge,
        )
    )
    legacy_close_flag = bool(data.get("close_on_merge"))
    explicit_close = "close_agent_on_merge" in data
    explicit_remove = "remove_worktree_on_merge" in data
    if explicit_close or explicit_remove:
        close_flag = bool(data.get("close_agent_on_merge"))
        remove_flag = bool(data.get("remove_worktree_on_merge"))
    elif legacy_close_flag:
        close_flag = True
        remove_flag = True
    else:
        close_flag, remove_flag = merge_cleanup_flags(
            state.get_group_settings(cell.group).worktree_merge_cleanup
        )
    queued_followups = [
        t for t in state.board_tasks.values()
        if t.agent_id == cell.id
        and t.lane in {"Backlog", "To Do"}
    ]
    cleanup_overridden = False
    orig_close_flag = close_flag
    orig_remove_flag = remove_flag
    if queued_followups:
        # ADDITIVE-ONLY surfacing: the override decision itself is unchanged
        # (queued follow-ups always preserve agent + worktree). We only log
        # and record it so a caller that passed close/remove flags can detect
        # that they were not honored.
        if orig_close_flag or orig_remove_flag:
            cleanup_overridden = True
            log.warning(
                "merge cleanup flags overridden due to queued follow-ups "
                "for agent=%s tasks=%d (close=%s→False, remove=%s→False)",
                cell.slug or cell.id,
                len(queued_followups),
                orig_close_flag,
                orig_remove_flag,
            )
        close_flag = False
        remove_flag = False
    auto_done_decision = _maybe_auto_move_merged_task_to_done(
        state,
        cell,
        enabled=bool(data.get("auto_move_to_done", True)),
        cleanup_requested=bool(close_flag or remove_flag),
    )
    if board_sync_manager and auto_done_decision.get("moved"):
        board_sync_manager.enqueue_task(
            auto_done_decision.get("task_id", ""),
            reason="pr_merge_finalized",
        )
    # Unlink completed/archive-closed tasks from this agent so they don't
    # re-appear in future merge messages. Tasks stay on the board as history.
    for t in list(state.board_tasks.values()):
        if t.agent_id == cell.id and task_is_closed(t):
            t.agent_id = ""
            state._emit("task_upsert", **asdict(t))
            state._db_save_task(t)

    clear_flag = bool(data.get("clear_context"))
    if queued_followups or close_flag or remove_flag:
        clear_flag = False
    cleanup = {
        "close_agent": close_flag,
        "remove_worktree": remove_flag,
        "agent_closed": False,
        "worktree_removed": False,
        "errors": [],
    }
    if cleanup_overridden:
        cleanup["cleanup_overridden"] = True
        cleanup["override_reason"] = "queued_followups"
        cleanup["queued_followup_count"] = len(queued_followups)
    if clear_flag and not close_flag and not remove_flag and cell.session_id:
        await bridge.send_text(cell.session_id, "/clear\r")
        cell.tasks_dispatched = 0
        state._emit_agent(cell)
        state._db_save_agent(cell)
        log.info("Cleared context for '%s' after merge", cell.name)
    reset_failed_with_followups = False
    if close_flag or remove_flag:
        cleanup = await cleanup_after_merge(
            cell,
            close_agent=close_flag,
            remove_worktree=remove_flag,
        )
    elif cell.worktree_path:
        # Reset worktree branch to base tip so new work starts fresh (avoids
        # re-merging already-merged commits).
        valid = await worktree_mgr.validate(cell)
        if valid:
            worktree_submodules = _configured_worktree_submodules_for_cell(
                state,
                cell,
            )
            ok = (
                await worktree_mgr.reset_to_base(
                    cell,
                    worktree_submodules=worktree_submodules,
                )
                if worktree_submodules
                else await worktree_mgr.reset_to_base(cell)
            )
            if ok:
                cell.worktree_checkpoints = await worktree_mgr.count_commits(cell)
                cell.worktree_dirty = False
                cell.worktree_diff = {}
                if queued_followups:
                    cell.worktree_merged = False
                state._emit_agent(cell)
            else:
                log.warning("Post-merge reset failed for '%s'", cell.name)
                if queued_followups:
                    # DEFENSIVE: a failed reset leaves the worktree dirty.
                    # Skip this cycle's auto-resume + pump-drain so the next
                    # follow-up doesn't land on the dirty tree; the next pump
                    # cycle picks it up once the dirty state is resolved.
                    reset_failed_with_followups = True

    if reviewer_cleanup.get("agents"):
        cleanup["reviewer_cleanup"] = reviewer_cleanup
        cleanup["errors"].extend(reviewer_cleanup.get("errors", []))

    result = {
        "type": "worktree_merge",
        "id": aid,
        "ok": True,
        "sha": merge_sha,
        "branch": merge_branch,
        "base_branch": merge_base_branch,
        "agent_name": merge_agent_name,
        "cleanup": cleanup,
    }
    _attach_stale_base(result, stale_base)
    cleanup_warning = _post_success_cleanup_warning(cleanup)
    if cleanup_warning:
        log.warning(cleanup_warning)
        _append_post_success_warning(
            result,
            cleanup_warning,
            phase="post_merge_cleanup",
            detail=cleanup,
        )
    if queued_followups and reset_failed_with_followups:
        log.warning(
            "Skipping post-merge follow-up dispatch for '%s': worktree reset "
            "to base failed, so queued follow-ups would land on a dirty "
            "worktree; deferring to the next pump cycle.",
            cell.name,
        )
    elif queued_followups:
        await _maybe_auto_resume_targets(
            state,
            handle_command,
            panel_event,
            targets=merge_resume_targets or [],
            group=cell.group,
        )
        await _pump_auto_dispatch_queue(
            state,
            handle_command,
            panel_event,
            group=cell.group,
        )
    return result


async def _finalize_successful_driverless_worktree_merge(
    *,
    state: MatrixState,
    target: WorktreeCommandTarget,
    aid: str,
    data: dict,
    merge_sha: str,
    merged_task_ids: tuple[str, ...],
    stale_base: dict | None,
    preserve_merge_diff: bool,
    boundary_task_for_diff,
    merge_diff_snapshot: dict | None,
    mark_branch_boundaries_merged,
    worktree_mgr: WorktreeManager,
) -> dict:
    """Apply branch/boundary side effects after a driverless merge succeeds."""
    mark_branch_boundaries_merged(target, merge_sha, merged_task_ids)
    state.cleanup_stale_boundary_successors()
    preserve_diff_warning = ""
    if preserve_merge_diff:
        preserve_diff_warning = _persist_preserved_merge_diff_warning_only(
            state,
            target,
            boundary_task_for_diff,
            merge_diff_snapshot,
            merge_commit_sha=merge_sha,
        )
    cleanup = {
        "close_agent": False,
        "remove_worktree": False,
        "agent_closed": False,
        "worktree_removed": False,
        "errors": [],
        "driverless": True,
    }
    requested_cleanup = _worktree_merge_requested_cleanup(
        state,
        target,
        data,
        preserve_merge_diff=preserve_merge_diff,
    )
    if requested_cleanup.get("close_agent_on_merge"):
        cleanup["errors"].append(
            "close_agent_on_merge is not supported for driverless merges"
        )
    if requested_cleanup.get("remove_worktree_on_merge"):
        submodules = _configured_worktree_submodules_for_cell(state, target)
        existing = ExistingWorktreeTarget(
            repo_root=target.worktree_repo_root,
            worktree_path=target.worktree_path,
            branch=target.worktree_branch,
            head_sha=await worktree_mgr.rev_parse(target.worktree_path, "HEAD") or "",
            base_branch=target.worktree_base_branch,
            git_root=target.git_root or target.worktree_repo_root,
            is_dirty=False,
            listed_worktree_entry={},
        )
        remove_result = await worktree_mgr.safe_remove_existing_worktree(
            existing,
            delete_branch=True,
            worktree_submodules=submodules,
        )
        cleanup["remove_worktree"] = True
        cleanup["worktree_removed"] = bool(remove_result.get("worktree_removed"))
        cleanup["worktree_remove"] = remove_result
        if not remove_result.get("ok"):
            cleanup["errors"].append(
                remove_result.get("message") or "Safe worktree removal failed"
            )
    result = {
        "type": "worktree_merge",
        "id": aid,
        "ok": True,
        "sha": merge_sha,
        "branch": target.worktree_branch,
        "base_branch": target.worktree_base_branch,
        "agent_name": target.name,
        "driverless": True,
        "cleanup": cleanup,
    }
    if preserve_diff_warning:
        result["warning"] = preserve_diff_warning
    _attach_stale_base(result, stale_base)
    cleanup_warning = _post_success_cleanup_warning(cleanup)
    if cleanup_warning:
        log.warning(cleanup_warning)
        _append_post_success_warning(
            result,
            cleanup_warning,
            phase="post_merge_cleanup",
            detail=cleanup,
        )
    return result


async def _run_direct_worktree_merge(
    *,
    state: MatrixState,
    cell,
    aid: str,
    data: dict,
    worktree_mgr: WorktreeManager,
    latest_boundary_state_for_cell,
    boundary_reason_message,
    mark_branch_boundaries_merged,
    cleanup_after_merge,
    broadcast_toast,
    bridge,
    handle_command,
    panel_event,
    board_sync_manager=None,
) -> dict:
    gates = await _preflight_worktree_merge_gates(
        state=state,
        cell=cell,
        worktree_mgr=worktree_mgr,
        aid=aid,
        data=data,
        latest_boundary_state_for_cell=latest_boundary_state_for_cell,
        boundary_reason_message=boundary_reason_message,
        panel_event=panel_event,
        publish_nested_submodule_branches=False,
    )
    if not gates.get("ok"):
        result = gates.get("result") or _worktree_merge_error(
            aid,
            "Merge preflight failed.",
        )
        if gates.get("workflow_breach") and isinstance(result, dict):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    attribution = gates["attribution"]
    merged_task_ids = attribution.target_task_ids

    squash = cell.worktree_merge_squash
    msg = str(data.get("message", "") or "").strip()
    if not msg:
        msg = await _generate_merge_message(
            cell,
            worktree_mgr,
            squash,
            state=state,
        )
    fallback_title = f"Merge {cell.name or cell.worktree_branch or 'worktree'} worktree"
    derived_title, derived_body = _split_merge_message_for_pr(
        msg,
        fallback_title=fallback_title,
    )
    nested_merge_result = None
    worktree_submodules = _configured_worktree_submodules_for_cell(state, cell)
    nested_pr_submodules = _ee_pr_flow_submodules(worktree_submodules)
    legacy_submodules = _legacy_nested_submodules(
        worktree_submodules,
        nested_pr_submodules,
    )
    if nested_pr_submodules:
        merge_nested_pr = getattr(
            worktree_mgr,
            "merge_nested_submodules_via_pr_for_merge",
            None,
        )
        if not callable(merge_nested_pr):
            result = _worktree_merge_error(
                aid,
                "Nested submodule PR integration is unavailable.",
                phase="nested_submodule_pr_merge",
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
        nested_merge_result = await merge_nested_pr(
            cell,
            nested_pr_submodules,
            title=str(data.get("pr_title", "") or "").strip() or derived_title,
            body=str(data.get("pr_body", "") or "").strip() or derived_body,
            merge=True,
        )
        _record_nested_submodule_metadata_on_latest_boundary(
            state,
            cell,
            nested_merge_result,
        )
        if nested_merge_result.get("pending"):
            result = {
                "type": "worktree_merge",
                "id": aid,
                "ok": True,
                "mode": "direct",
                "pending": True,
                "merged": False,
                "nested_submodules": nested_merge_result,
                "message": (
                    "Nested submodule PR is pending; parent direct merge has "
                    "not run."
                ),
            }
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
        if not nested_merge_result.get("ok"):
            result = _worktree_merge_error(
                aid,
                nested_merge_result.get(
                    "error",
                    "Nested submodule PR merge failed.",
                ),
                phase=nested_merge_result.get(
                    "phase",
                    "nested_submodule_pr_merge",
                ),
                nested_submodules=nested_merge_result,
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
    preserve_merge_diff, boundary_task_for_diff, merge_diff_snapshot = (
        await _capture_worktree_merge_preserve_diff(
            state=state,
            cell=cell,
            worktree_mgr=worktree_mgr,
            data=data,
        )
    )
    merge_resume_targets = _capture_worktree_merge_resume_targets(state, cell)
    requested_cleanup = _worktree_merge_requested_cleanup(
        state,
        cell,
        data,
        preserve_merge_diff=preserve_merge_diff,
    )
    merge_result = (
        await worktree_mgr.server_merge(
            cell,
            msg,
            squash=squash,
            worktree_submodules=legacy_submodules,
        )
        if legacy_submodules
        else await worktree_mgr.server_merge(cell, msg, squash=squash)
    )
    if merge_result.get("ok"):
        if getattr(cell, "driverless", False):
            result = await _finalize_successful_driverless_worktree_merge(
                state=state,
                target=cell,
                aid=aid,
                data=data,
                merge_sha=merge_result["sha"],
                merged_task_ids=merged_task_ids,
                stale_base=gates.get("stale_base"),
                preserve_merge_diff=preserve_merge_diff,
                boundary_task_for_diff=boundary_task_for_diff,
                merge_diff_snapshot=merge_diff_snapshot,
                mark_branch_boundaries_merged=mark_branch_boundaries_merged,
                worktree_mgr=worktree_mgr,
            )
        else:
            result = await _finalize_successful_worktree_merge(
                state=state,
                cell=getattr(cell, "cell", None) or cell,
                aid=aid,
                data=data,
                merge_sha=merge_result["sha"],
                merged_task_ids=merged_task_ids,
                stale_base=gates.get("stale_base"),
                preserve_merge_diff=preserve_merge_diff,
                boundary_task_for_diff=boundary_task_for_diff,
                merge_diff_snapshot=merge_diff_snapshot,
                merge_resume_targets=merge_resume_targets,
                mark_branch_boundaries_merged=mark_branch_boundaries_merged,
                cleanup_after_merge=cleanup_after_merge,
                broadcast_toast=broadcast_toast,
                bridge=bridge,
                worktree_mgr=worktree_mgr,
                handle_command=handle_command,
                panel_event=panel_event,
                board_sync_manager=board_sync_manager,
            )
    else:
        result = _worktree_merge_error(
            aid,
            merge_result.get("error", "Merge failed"),
        )
        _attach_stale_base(result, gates.get("stale_base"))
    if gates.get("workflow_breach") and isinstance(result, dict):
        result["workflow_breach"] = gates["workflow_breach"]
    if nested_merge_result and isinstance(result, dict):
        result["nested_submodules"] = nested_merge_result
    if isinstance(result, dict) and result.get("ok") and result.get("sha"):
        _record_merge_completion_evidence(
            state,
            result=result,
            task_ids=merged_task_ids,
            cell=getattr(cell, "cell", None) or cell,
            repo_root=str(getattr(cell, "worktree_repo_root", "")
                          or getattr(cell, "git_root", "") or ""),
            branch=str(getattr(cell, "worktree_branch", "") or ""),
            base_branch=str(getattr(cell, "worktree_base_branch", "") or ""),
            board_sync_manager=board_sync_manager,
        )
    return result
