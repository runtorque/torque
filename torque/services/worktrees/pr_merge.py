"""Worktree orchestration: pr merge."""

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
    _record_pr_metadata_on_task_boundary,
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

from .finalize import (
    _confirm_pr_merged_and_base_at_merge,
    _fallback_successful_worktree_merge_result,
    _finalize_successful_driverless_worktree_merge,
    _finalize_successful_worktree_merge,
    _post_success_guard_warning,
    _recover_authoritative_post_success_from_boundary,
    _resolve_already_merged_sha,
)
from .gates import (
    _attach_stale_base,
    _configured_worktree_submodules_for_cell,
    _untracked_overwrite_message,
)
from .preflight import (
    _append_post_success_warning,
    _attach_auto_force_push_metadata,
    _capture_worktree_merge_preserve_diff,
    _capture_worktree_merge_resume_targets,
    _post_success_result_error,
    _preflight_merge_attribution,
    _preflight_worktree_merge_gates,
    _worktree_merge_requested_cleanup,
)
from .runtime import (
    _origin_verification_evidence,
    _record_merge_completion_evidence,
)
from .submodules import (
    _combine_nested_submodule_results,
    _ee_pr_flow_submodules,
    _legacy_nested_submodules,
    _record_nested_submodule_metadata_on_latest_boundary,
)
from .targets import (
    _append_github_closing_refs_to_pr_body,
    _github_pr_closing_refs_enabled,
    _linked_github_issues_for_pr,
    _log_pr_task_ref_rewrite,
    _worktree_merge_error,
)


async def _cleanup_verified_merged_pr_head_branch(
    *,
    worktree_mgr: WorktreeManager,
    worktree_path: str,
    remote: str,
    branch: str,
    base_branch: str,
    pr_metadata: dict | None,
    origin_verification: dict | None,
) -> dict:
    """Best-effort remote cleanup, gated by authoritative merge evidence.

    This deliberately runs only after the PR state and origin/base SHA have
    both been independently verified. Cleanup failures are evidence, not
    merge failures: the merge already landed and must remain successful.
    """
    cleanup = {
        "attempted": False,
        "remote": str(remote or "").strip(),
        "branch": str(branch or "").strip(),
        # Keep the established branch-cleanup evidence names so local and
        # remote cleanup results can be read with the same vocabulary.
        "branch_deleted": False,
        "branch_delete_failed": False,
        "branch_delete_returncode": None,
        "branch_delete_stderr": "",
    }
    pr_state = str((pr_metadata or {}).get("state") or "").upper()
    origin_verified = bool((origin_verification or {}).get("verified"))
    if not origin_verified:
        cleanup.update(
            status="not_attempted",
            reason="origin_merge_not_verified",
        )
        return cleanup
    if pr_state != "MERGED":
        cleanup.update(status="not_attempted", reason="pr_not_verified_merged")
        return cleanup
    if not cleanup["remote"] or not cleanup["branch"]:
        cleanup.update(status="not_attempted", reason="remote_or_head_branch_missing")
        return cleanup
    # A PR head must never be allowed to name its own base branch.
    if cleanup["branch"] == str(base_branch or "").strip():
        cleanup.update(status="not_attempted", reason="head_branch_is_base_branch")
        return cleanup

    delete_helper = getattr(worktree_mgr, "github_delete_remote_branch", None)
    if not callable(delete_helper):
        cleanup.update(status="not_attempted", reason="remote_cleanup_unavailable")
        return cleanup

    cleanup["attempted"] = True
    try:
        delete_result = await delete_helper(
            worktree_path,
            cleanup["remote"],
            cleanup["branch"],
        )
    except Exception as exc:  # pragma: no cover - defensive integration path
        log.warning(
            "Remote PR head cleanup failed for %s/%s: %s",
            cleanup["remote"], cleanup["branch"], exc,
        )
        cleanup.update(
            status="refused",
            branch_delete_failed=True,
            branch_delete_stderr=str(exc),
            branch_delete_returncode=-1,
        )
        return cleanup

    if not isinstance(delete_result, dict):
        cleanup.update(
            status="refused",
            branch_delete_failed=True,
            branch_delete_stderr="remote branch delete returned invalid result",
            branch_delete_returncode=-1,
        )
        return cleanup

    cleanup["branch_delete_returncode"] = delete_result.get(
        "branch_delete_returncode"
    )
    cleanup["branch_delete_stderr"] = str(
        delete_result.get("branch_delete_stderr") or ""
    )
    if delete_result.get("ok"):
        cleanup["branch_deleted"] = bool(delete_result.get("deleted"))
        cleanup["branch_delete_failed"] = False
        cleanup["status"] = (
            "deleted" if cleanup["branch_deleted"] else "already_absent"
        )
        return cleanup

    cleanup.update(
        status="refused",
        branch_delete_failed=True,
        branch_delete_stderr=(
            cleanup["branch_delete_stderr"]
            or str(delete_result.get("error") or "remote branch delete failed")
        ),
    )
    return cleanup


def _remote_branch_cleanup_error(cleanup: dict | None) -> str:
    """Return caller-visible cleanup text for a failed remote delete.

    A landed merge must remain successful when this best-effort operation
    fails, but leaving that failure only in ``remote_branch_cleanup`` makes
    the main post-merge cleanup summary misleadingly report ``errors=[]``.
    """
    cleanup = cleanup or {}
    if not cleanup.get("branch_delete_failed"):
        return ""
    remote = str(cleanup.get("remote") or "remote").strip()
    branch = str(cleanup.get("branch") or "branch").strip()
    detail = str(
        cleanup.get("branch_delete_stderr")
        or cleanup.get("error")
        or "remote branch delete failed"
    ).strip()
    return f"Remote branch cleanup failed for '{remote}/{branch}': {detail}"


async def _run_pr_worktree_merge(
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
    progress=None,
) -> dict:
    async def _progress(phase: str, message: str, **extra) -> None:
        if not progress:
            return
        try:
            await progress(phase, message, **extra)
        except Exception:
            log.exception(
                "Failed to emit worktree merge progress for %s phase=%s",
                aid,
                phase,
            )

    await _progress("preflight", "Checking merge readiness\u2026")
    if not (cell and cell.worktree_path and cell.worktree_branch):
        failure = {
            "phase": "target_resolution",
            "error": "Agent has no worktree.",
        }
        recovered = await _recover_authoritative_post_success_from_boundary(
            state=state,
            worktree_mgr=worktree_mgr,
            aid=aid,
            failure=failure,
            cell=cell,
            worktree_path=str(getattr(cell, "worktree_path", "") or ""),
            repo_root=str(
                getattr(cell, "worktree_repo_root", "")
                or getattr(cell, "git_root", "")
                or ""
            ),
            branch=str(getattr(cell, "worktree_branch", "") or ""),
            base_branch=str(getattr(cell, "worktree_base_branch", "") or ""),
        )
        if recovered:
            return recovered
        return _worktree_merge_error(aid, "Agent has no worktree.")

    attribution_preflight = await _preflight_merge_attribution(
        state=state,
        cell=cell,
        aid=aid,
        data=data,
        latest_boundary_state_for_cell=latest_boundary_state_for_cell,
    )
    if not attribution_preflight.get("ok"):
        result = attribution_preflight.get("result") or _worktree_merge_error(
            aid,
            "Merge attribution preflight failed.",
        )
        result["mode"] = "pull_request"
        return result
    frozen_attribution = attribution_preflight["attribution"]
    frozen_boundary_state = attribution_preflight["boundary_state"]

    wt = cell.worktree_path
    repo_root = cell.worktree_repo_root or cell.git_root or ""
    branch = cell.worktree_branch or ""
    base_branch = cell.worktree_base_branch or "main"

    preflight = await worktree_mgr.github_preflight(wt)
    if not preflight.get("ok"):
        recovered = await _recover_authoritative_post_success_from_boundary(
            state=state,
            worktree_mgr=worktree_mgr,
            aid=aid,
            failure={
                **preflight,
                "phase": preflight.get("phase", "github_preflight"),
            },
            cell=cell,
            worktree_path=wt,
            repo_root=repo_root or wt,
            branch=branch,
            base_branch=base_branch,
            stale_base=None,
        )
        if recovered:
            return recovered
        return _worktree_merge_error(
            aid,
            preflight.get("error", "GitHub PR preflight failed."),
            mode="pull_request",
            phase=preflight.get("phase", "github_preflight"),
        )

    remote_info = await worktree_mgr.github_select_remote(wt)
    if not remote_info.get("ok"):
        recovered = await _recover_authoritative_post_success_from_boundary(
            state=state,
            worktree_mgr=worktree_mgr,
            aid=aid,
            failure={
                **remote_info,
                "phase": remote_info.get("phase", "github_remote"),
            },
            cell=cell,
            worktree_path=wt,
            repo_root=repo_root or wt,
            branch=branch,
            base_branch=base_branch,
            stale_base=None,
        )
        if recovered:
            return recovered
        return _worktree_merge_error(
            aid,
            remote_info.get("error", "GitHub remote selection failed."),
            mode="pull_request",
            phase=remote_info.get("phase", "github_remote"),
        )
    remote = remote_info.get("remote", "origin") or "origin"

    base_sync = await worktree_mgr.github_sync_remote_base(
        wt,
        repo_root or wt,
        remote,
        base_branch,
    )
    if not base_sync.get("ok"):
        recovered = await _recover_authoritative_post_success_from_boundary(
            state=state,
            worktree_mgr=worktree_mgr,
            aid=aid,
            failure={
                **base_sync,
                "phase": base_sync.get("phase", "remote_base_sync"),
            },
            cell=cell,
            worktree_path=wt,
            repo_root=repo_root or wt,
            remote=remote,
            branch=branch,
            base_branch=base_branch,
            stale_base=None,
        )
        if recovered:
            recovered["remote_base_sync"] = base_sync
            return recovered
        return _worktree_merge_error(
            aid,
            base_sync.get("error", "Remote base sync failed."),
            mode="pull_request",
            phase=base_sync.get("phase", "remote_base_sync"),
            remote_base_sync=base_sync,
        )

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
        frozen_attribution=frozen_attribution,
        frozen_boundary_state=frozen_boundary_state,
    )
    if not gates.get("ok"):
        result = gates.get("result") or _worktree_merge_error(
            aid,
            "Merge preflight failed.",
        )
        failure = dict(result) if isinstance(result, dict) else {
            "error": "Merge preflight failed.",
        }
        failure.setdefault("phase", "merge_preflight")
        recovered = await _recover_authoritative_post_success_from_boundary(
            state=state,
            worktree_mgr=worktree_mgr,
            aid=aid,
            failure=failure,
            cell=cell,
            worktree_path=wt,
            repo_root=repo_root or wt,
            remote=remote,
            branch=branch,
            base_branch=base_branch,
            stale_base=gates.get("stale_base"),
        )
        if recovered:
            if gates.get("workflow_breach"):
                recovered["workflow_breach"] = gates["workflow_breach"]
            return recovered
        if isinstance(result, dict):
            result["mode"] = "pull_request"
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
        return result

    merged_task_ids = frozen_attribution.target_task_ids

    squash = True
    msg = str(data.get("message", "") or "").strip()
    if not msg:
        msg = await _generate_merge_message(
            cell,
            worktree_mgr,
            squash,
            state=state,
        )
    fallback_title = f"Merge {cell.name or branch or 'worktree'} worktree"
    derived_title, derived_body = _split_merge_message_for_pr(
        msg,
        fallback_title=fallback_title,
    )
    pr_title = str(data.get("pr_title", "") or "").strip()
    pr_body = str(data.get("pr_body", "") or "").strip()
    title = pr_title or derived_title
    body = pr_body or derived_body

    group_settings = state.get_group_settings(getattr(cell, "group", "") or "")
    github_group_settings = getattr(
        group_settings,
        "board_sync_github",
        {},
    ) or {}
    if not isinstance(github_group_settings, dict):
        github_group_settings = {}
    base_repo = (
        str(preflight.get("name_with_owner") or "").strip()
        or str(github_group_settings.get("github_repo", "")).strip()
    )
    rewrite = _rewrite_pr_torque_task_refs_metadata(
        title,
        body,
        state=state,
        base_repo=base_repo,
    )
    title = rewrite["title"]
    body = rewrite["body"]
    _log_pr_task_ref_rewrite("worktree_merge", rewrite)

    close_issues_via_pr = _github_pr_closing_refs_enabled(group_settings)
    linked_issues: list[dict] = []
    if close_issues_via_pr:
        linked_issues = _linked_github_issues_for_pr(
            state,
            cell,
            base_repo=base_repo,
        )
        body = await _append_github_closing_refs_to_pr_body(
            body=body,
            linked_issues=linked_issues,
            group_settings=group_settings,
        )

    worktree_submodules = _configured_worktree_submodules_for_cell(state, cell)
    nested_merge_result = None
    nested_pr_submodules = _ee_pr_flow_submodules(worktree_submodules)
    legacy_submodules = _legacy_nested_submodules(
        worktree_submodules,
        nested_pr_submodules,
    )
    if nested_pr_submodules:
        merge_nested = getattr(
            worktree_mgr,
            "merge_nested_submodules_via_pr_for_merge",
            None,
        )
        if not callable(merge_nested):
            result = _worktree_merge_error(
                aid,
                "Nested submodule PR integration is unavailable.",
                mode="pull_request",
                phase="nested_submodule_pr_merge",
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
        nested_merge_result = await merge_nested(
            cell,
            nested_pr_submodules,
            title=title,
            body=body,
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
                "mode": "pull_request",
                "pending": True,
                "merged": False,
                "nested_submodules": nested_merge_result,
                "message": (
                    "Nested submodule PR is pending; parent pull request has "
                    "not been created or merged."
                ),
            }
            first_url = ""
            for item in nested_merge_result.get("submodules", []) or []:
                pr = item.get("pr") if isinstance(item, dict) else {}
                if isinstance(pr, dict) and pr.get("url"):
                    first_url = str(pr.get("url") or "")
                    break
            if first_url:
                result["url"] = first_url
                result["pr_url"] = first_url
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
                mode="pull_request",
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

    if legacy_submodules:
        merge_nested = getattr(
            worktree_mgr,
            "_merge_nested_submodules_for_merge",
            None,
        )
        if not callable(merge_nested):
            result = _worktree_merge_error(
                aid,
                "Nested submodule merge integration is unavailable.",
                mode="pull_request",
                phase="nested_submodule_merge",
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
        legacy_nested_result = await merge_nested(
            cell,
            legacy_submodules,
            message=msg,
        )
        if not legacy_nested_result.get("ok"):
            result = _worktree_merge_error(
                aid,
                legacy_nested_result.get(
                    "error",
                    "Nested submodule merge failed.",
                ),
                mode="pull_request",
                phase="nested_submodule_merge",
                nested_submodules=legacy_nested_result,
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
        nested_merge_result = _combine_nested_submodule_results(
            nested_merge_result,
            legacy_nested_result,
        )

    # The nested flow can add a final superproject gitlink commit. Re-run the
    # cheap superproject conflict/overwrite guards against that final branch tip
    # before publishing the parent PR branch.
    if nested_merge_result:
        post_nested_check = await worktree_mgr.check_merge_conflicts(cell)
        if not post_nested_check.get("clean"):
            result = _worktree_merge_error(
                aid,
                post_nested_check.get(
                    "error",
                    "Conflicts detected after nested submodule merge.",
                ),
                mode="pull_request",
                phase="nested_submodule_postcheck",
                nested_submodules=nested_merge_result,
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result
        overwrite_paths = await worktree_mgr.merge_untracked_overwrite_paths(
            repo_root or wt,
            base_branch,
            post_nested_check.get("tree_sha", ""),
        )
        if overwrite_paths:
            result = _worktree_merge_error(
                aid,
                _untracked_overwrite_message(
                    overwrite_paths,
                    operation="merge",
                    location="the checked-out base repo",
                ),
                mode="pull_request",
                phase="nested_submodule_postcheck",
                overwrite_paths=overwrite_paths,
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

    await _progress("push_branch", "Pushing branch to origin\u2026")
    pushed = await worktree_mgr.github_push_branch(wt, remote, branch)
    if not pushed.get("ok"):
        force_retry = getattr(
            worktree_mgr,
            "github_force_push_branch_with_lease_if_safe",
            None,
        )
        if callable(force_retry):
            retried = await force_retry(
                wt,
                remote,
                branch,
                base_branch=base_branch,
                push_error=pushed,
            )
            if retried.get("ok") or retried.get("safety_gate_passed"):
                pushed = retried
            elif retried.get("non_fast_forward"):
                pushed["non_fast_forward"] = True
                if isinstance(retried.get("auto_force_safety"), dict):
                    pushed["auto_force_safety"] = retried["auto_force_safety"]
                # The safe-force retry was declined by its safety gate. Carry
                # the refusal reason forward so the merge error explains WHY
                # the auto force-with-lease was skipped, not just the original
                # non-fast-forward rejection.
                refusal = str(retried.get("error") or "").strip()
                if refusal:
                    pushed["auto_force_refusal"] = refusal
        if not pushed.get("ok"):
            error_message = pushed.get(
                "error", "Failed to push worktree branch.")
            refusal = str(pushed.get("auto_force_refusal") or "").strip()
            if refusal:
                error_message = (
                    f"{error_message}\n"
                    f"Auto force-with-lease refused: {refusal}"
                )
            result = _worktree_merge_error(
                aid,
                error_message,
                mode="pull_request",
                phase=pushed.get("phase", "push_branch"),
            )
            if refusal:
                result["auto_force_refusal"] = refusal
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result

    push_metadata_result = pushed

    await _progress("pr_create", "Creating pull request\u2026")
    pr_result = await worktree_mgr.github_create_or_reuse_pr(
        wt,
        branch,
        base_branch,
        title=title,
        body=body,
    )
    if not pr_result.get("ok"):
        result = _worktree_merge_error(
            aid,
            pr_result.get("error", "Failed to create pull request."),
            mode="pull_request",
            phase=pr_result.get("phase", "pr_create"),
        )
        _attach_auto_force_push_metadata(result, push_metadata_result)
        if gates.get("workflow_breach"):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    already_merged_pr = bool(pr_result.get("already_merged"))
    if (
            close_issues_via_pr
            and linked_issues
            and pr_result.get("existing")
            and not already_merged_pr):
        existing_body = str(pr_result.get("body") or "")
        updated_existing_body = await _append_github_closing_refs_to_pr_body(
            body=existing_body,
            linked_issues=linked_issues,
            group_settings=group_settings,
        )
        if updated_existing_body != existing_body:
            edit_selector = (
                pr_result.get("number")
                or pr_result.get("url")
                or branch
            )
            edit_result = await worktree_mgr.github_pr_edit_body(
                wt,
                edit_selector,
                updated_existing_body,
            )
            if not edit_result.get("ok"):
                result = _worktree_merge_error(
                    aid,
                    edit_result.get(
                        "error",
                        "Failed to update pull request body.",
                    ),
                    mode="pull_request",
                    phase=edit_result.get("phase", "pr_edit_body"),
                    url=pr_result.get("url", ""),
                    pr_url=pr_result.get("url", ""),
                )
                _attach_auto_force_push_metadata(result, push_metadata_result)
                if gates.get("workflow_breach"):
                    result["workflow_breach"] = gates["workflow_breach"]
                return result
            pr_result.update({
                key: value
                for key, value in edit_result.items()
                if key not in {"phase"}
            })
            pr_result["body"] = updated_existing_body
            pr_result["body_updated"] = True

    pr_metadata = _pr_result_metadata(
        pr_result=pr_result,
        remote=remote,
        base_branch=base_branch,
        branch=branch,
        status="merged" if already_merged_pr else "created",
    )
    for merged_task_id in merged_task_ids:
        _record_pr_metadata_on_task_boundary(
            state,
            cell,
            merged_task_id,
            pr_metadata,
            requested_cleanup=requested_cleanup,
        )

    if already_merged_pr:
        merge_sha = await _resolve_already_merged_sha(
            worktree_mgr=worktree_mgr,
            cell=cell,
            repo_root=repo_root or wt,
            worktree_path=wt,
            base_branch=base_branch,
            pr_result=pr_result,
        )
        merge_result = {
            "ok": True,
            "phase": "pr_merge",
            "url": pr_result.get("url", ""),
            "number": pr_result.get("number"),
            "head_sha": pr_result.get("head_sha", ""),
            "merge_commit_sha": merge_sha,
            "merge_state": pr_result.get("merge_state", ""),
            "pending": False,
            "already_merged": True,
            "pr_status": pr_result,
        }
    else:
        squash_body = _append_pr_url_to_squash_body(
            body,
            str(pr_result.get("url") or pr_metadata.get("url") or ""),
        )

        head_sha = str(pr_result.get("head_sha") or "").strip()
        if not head_sha:
            current_head = getattr(worktree_mgr, "current_head", None)
            if callable(current_head):
                head_sha = await current_head(cell) or ""
        await _progress("pr_merge", "Merging pull request\u2026")
        merge_result = await worktree_mgr.github_request_squash_merge(
            wt,
            pr_result.get("number") or pr_result.get("url", ""),
            head_sha,
            subject=title,
            body=squash_body,
        )
        if (
            not merge_result.get("ok")
            and not bool(data.get("disable_auto_merge"))
            and _pr_merge_failure_allows_auto(merge_result)
        ):
            merge_result = await worktree_mgr.github_request_squash_merge(
                wt,
                pr_result.get("number") or pr_result.get("url", ""),
                head_sha,
                subject=title,
                body=squash_body,
                auto=True,
                url=pr_result.get("url", ""),
            )

    authoritative_guard = None
    authoritative_guard_failure = None
    if not merge_result.get("ok") and not merge_result.get("pending"):
        confirmation = await _confirm_pr_merged_and_base_at_merge(
            worktree_mgr=worktree_mgr,
            worktree_path=wt,
            repo_root=repo_root or wt,
            remote=remote,
            base_branch=base_branch,
            pr_result=pr_result,
            merge_result=merge_result,
        )
        if confirmation.get("ok"):
            authoritative_guard = confirmation
            authoritative_guard_failure = dict(merge_result)
            merge_result = dict(merge_result)
            merge_result.update({
                "ok": True,
                "pending": False,
                "merge_commit_sha": confirmation.get("merge_commit_sha", ""),
                "pr_status": confirmation.get("pr_status") or {},
            })
            merge_result.pop("error", None)
            merge_result["authoritative_post_success_guard"] = confirmation

    pending = bool(merge_result.get("pending"))
    pr_metadata = _pr_result_metadata(
        pr_result=pr_result,
        merge_result=merge_result,
        remote=remote,
        base_branch=base_branch,
        branch=branch,
        pending=pending,
        status=(
            "merged"
            if merge_result.get("ok") and not pending
            else "pending" if pending else "merge_failed"
        ),
    )
    for merged_task_id in merged_task_ids:
        _record_pr_metadata_on_task_boundary(
            state,
            cell,
            merged_task_id,
            pr_metadata,
            requested_cleanup=requested_cleanup,
        )

    if pending:
        result = {
            "type": "worktree_merge",
            "id": aid,
            "ok": True,
            "mode": "pull_request",
            "pending": True,
            "merged": False,
            "url": pr_metadata.get("url", ""),
            "pr_url": pr_metadata.get("url", ""),
            "pr": pr_metadata,
            "message": "Pull request is open with auto-merge pending.",
        }
        if nested_merge_result:
            result["nested_submodules"] = nested_merge_result
        _attach_auto_force_push_metadata(result, push_metadata_result)
        _attach_stale_base(result, gates.get("stale_base"))
        if gates.get("workflow_breach"):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    if not merge_result.get("ok"):
        result = _worktree_merge_error(
            aid,
            merge_result.get("error", "Pull request merge failed."),
            mode="pull_request",
            phase=merge_result.get("phase", "pr_merge"),
            url=pr_metadata.get("url", ""),
            pr_url=pr_metadata.get("url", ""),
            pr=pr_metadata,
        )
        _attach_auto_force_push_metadata(result, push_metadata_result)
        _attach_stale_base(result, gates.get("stale_base"))
        if gates.get("workflow_breach"):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    merge_sha = str(merge_result.get("merge_commit_sha") or "").strip()
    if not merge_sha:
        merge_sha = str(pr_metadata.get("merge_commit_sha") or "").strip()
    if not merge_sha and merge_result.get("already_merged"):
        merge_sha = str(pr_result.get("head_sha") or "already-merged").strip()
    if not merge_sha:
        result = _worktree_merge_error(
            aid,
            "Pull request merged but GitHub did not report a merge commit SHA.",
            mode="pull_request",
            phase="pr_merge",
            url=pr_metadata.get("url", ""),
            pr_url=pr_metadata.get("url", ""),
            pr=pr_metadata,
        )
        _attach_auto_force_push_metadata(result, push_metadata_result)
        if gates.get("workflow_breach"):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    post_merge_sync = (
        authoritative_guard.get("base_sync")
        if isinstance(authoritative_guard, dict)
        and isinstance(authoritative_guard.get("base_sync"), dict)
        else None
    )
    if post_merge_sync is None:
        post_merge_sync = await worktree_mgr.github_sync_remote_base(
            wt,
            repo_root or wt,
            remote,
            base_branch,
        )
    post_merge_sync_warning = ""
    if not post_merge_sync.get("ok"):
        confirmation = await _confirm_pr_merged_and_base_at_merge(
            worktree_mgr=worktree_mgr,
            worktree_path=wt,
            repo_root=repo_root or wt,
            remote=remote,
            base_branch=base_branch,
            pr_result=pr_result,
            merge_result=merge_result,
            post_merge_sync=post_merge_sync,
            force_status_query=True,
            skip_base_sync=True,
        )
        if confirmation.get("ok"):
            authoritative_guard = confirmation
        post_merge_sync_warning = (
            "Pull request is merged, but post-merge local base sync failed: "
            + _post_success_result_error(
                post_merge_sync,
                "local base sync failed",
            )
        )
        log.warning(post_merge_sync_warning)

    origin_verification = _origin_verification_evidence(
        merge_sha=merge_sha,
        remote=remote,
        base_branch=base_branch,
        post_merge_sync=post_merge_sync,
        authoritative_guard=authoritative_guard,
    )
    # A successful remote delete needs the worktree as its Git cwd. Run it
    # after authoritative merge verification but before finalization, which
    # may remove that worktree. This is independent of a later shared-context
    # removal skip.
    remote_branch_cleanup = await _cleanup_verified_merged_pr_head_branch(
        worktree_mgr=worktree_mgr,
        worktree_path=wt,
        remote=remote,
        branch=branch,
        base_branch=base_branch,
        pr_metadata=pr_metadata,
        origin_verification=origin_verification,
    )

    await _progress("finalize", "Finalizing merge\u2026")
    try:
        if getattr(cell, "driverless", False):
            result = await _finalize_successful_driverless_worktree_merge(
                state=state,
                target=cell,
                aid=aid,
                data=data,
                merge_sha=merge_sha,
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
                merge_sha=merge_sha,
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
                cleanup_merge_evidence={
                    "merge_commit_sha": merge_sha,
                    "origin_verified": bool(origin_verification.get("verified")),
                },
            )
    except Exception as exc:
        confirmation = await _confirm_pr_merged_and_base_at_merge(
            worktree_mgr=worktree_mgr,
            worktree_path=wt,
            repo_root=repo_root or wt,
            remote=remote,
            base_branch=base_branch,
            pr_result=pr_result,
            merge_result=merge_result,
            post_merge_sync=post_merge_sync,
        )
        if not confirmation.get("ok"):
            raise
        log.exception(
            "Post-merge finalization failed after PR %s landed at %s",
            pr_metadata.get("number") or pr_metadata.get("url") or branch,
            merge_sha,
        )
        result = _fallback_successful_worktree_merge_result(
            cell=cell,
            aid=aid,
            merge_sha=merge_sha,
            stale_base=gates.get("stale_base"),
            cleanup_error=str(exc),
        )
        authoritative_guard = confirmation
    result.update({
        "mode": "pull_request",
        "pending": False,
        "merged": True,
        "url": pr_metadata.get("url", ""),
        "pr_url": pr_metadata.get("url", ""),
        "pr": pr_metadata,
    })
    if nested_merge_result:
        result["nested_submodules"] = nested_merge_result
    if already_merged_pr:
        result["already_merged"] = True
        pr_warning = str(pr_result.get("warning") or "").strip()
        if pr_warning:
            _append_post_success_warning(
                result,
                pr_warning,
                phase=pr_result.get("phase", "pr_create"),
                detail=pr_result,
            )
    if authoritative_guard:
        result["authoritative_post_success_guard"] = authoritative_guard
        if authoritative_guard_failure:
            _append_post_success_warning(
                result,
                _post_success_guard_warning(
                    authoritative_guard_failure,
                    merge_sha,
                    base_branch=base_branch,
                ),
                phase=authoritative_guard_failure.get(
                    "phase",
                    "post_success_check",
                ),
                detail={
                    "failure": authoritative_guard_failure,
                    "confirmation": authoritative_guard,
                },
            )
    if post_merge_sync_warning:
        result["remote_base_sync"] = post_merge_sync
        _append_post_success_warning(
            result,
            post_merge_sync_warning,
            phase=post_merge_sync.get("phase", "remote_base_sync"),
            detail=post_merge_sync,
        )
    if origin_verification:
        result["origin_verification"] = origin_verification
    result["remote_branch_cleanup"] = remote_branch_cleanup
    remote_cleanup_error = _remote_branch_cleanup_error(remote_branch_cleanup)
    if remote_cleanup_error:
        # Preserve merge success while surfacing this failure through the
        # cleanup result and normal post-success warning channel.
        cleanup = result.setdefault("cleanup", {})
        errors = cleanup.setdefault("errors", [])
        if remote_cleanup_error not in errors:
            errors.append(remote_cleanup_error)
        warning = (
            "Merge landed, but post-merge cleanup reported warnings: "
            + remote_cleanup_error
        )
        log.warning(warning)
        _append_post_success_warning(
            result,
            warning,
            phase="remote_branch_delete",
            detail=remote_branch_cleanup,
        )
    _attach_auto_force_push_metadata(result, push_metadata_result)
    if gates.get("workflow_breach"):
        result["workflow_breach"] = gates["workflow_breach"]
    _record_merge_completion_evidence(
        state,
        result=result,
        task_ids=merged_task_ids,
        cell=cell,
        repo_root=repo_root or wt,
        branch=branch,
        base_branch=base_branch,
        remote=remote,
        origin_verification=origin_verification,
        board_sync_manager=board_sync_manager,
    )
    return result
