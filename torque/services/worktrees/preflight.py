"""Worktree orchestration: preflight."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime, timezone

from ...artifacts import normalize_artifacts
from ...backend_invariants import (
    check_backend_modularity_crossings,
    format_backend_modularity_crossings,
)
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

from .gates import (
    _attach_stale_base,
    _boundary_gate_message,
    _boundary_mismatch_force_enabled,
    _configured_worktree_submodules_for_cell,
    _emit_boundary_mismatch_override_workflow_breach,
    _emit_stale_base_override_workflow_breach,
    _is_reconcilable_nested_gitlink_conflict,
    _sibling_branch_divergence_gate_for_merge,
    _stale_base_force_enabled,
    _stale_base_merge_result,
    _untracked_overwrite_message,
)
from .submodules import (
    _latest_open_boundary_task_for_cell,
)
from .targets import (
    _worktree_merge_error,
    _worktree_merge_preserve_diff_enabled,
)

async def _preflight_worktree_merge_gates(
    *,
    state: MatrixState,
    cell,
    worktree_mgr: WorktreeManager,
    aid: str,
    data: dict,
    latest_boundary_state_for_cell,
    boundary_reason_message,
    panel_event=None,
    publish_nested_submodule_branches: bool = False,
) -> dict:
    """Run shared local merge gates before either direct or PR merge paths."""
    if not (cell and cell.worktree_path and cell.worktree_branch):
        return {
            "ok": False,
            "result": _worktree_merge_error(aid, "Agent has no worktree."),
        }

    worktree_submodules = _configured_worktree_submodules_for_cell(state, cell)
    boundary_state = await latest_boundary_state_for_cell(cell)
    dirty = (
        await worktree_mgr.has_uncommitted_changes(
            cell,
            worktree_submodules=worktree_submodules,
        )
        if worktree_submodules
        else await worktree_mgr.has_uncommitted_changes(cell)
    )
    if dirty:
        return {
            "ok": False,
            "boundary_state": boundary_state,
            "result": _worktree_merge_error(
                aid,
                "Commit or checkpoint changes before merging.",
            ),
        }

    boundary_override_event = None
    if boundary_state.get("latest") and not boundary_state.get("clean"):
        boundary_reason = boundary_state.get("reason", "")
        boundary_latest = boundary_state.get("latest")
        boundary_message = await _boundary_gate_message(
            worktree_mgr,
            cell,
            boundary_reason,
            boundary_latest,
            boundary_reason_message,
        )
        if (
                boundary_reason == "branch_tip_moved"
                and _boundary_mismatch_force_enabled(data)):
            boundary_override_event = (
                _emit_boundary_mismatch_override_workflow_breach(
                    state,
                    panel_event,
                    cell,
                    boundary_latest,
                    data,
                )
            )
        else:
            return {
                "ok": False,
                "boundary_state": boundary_state,
                "result": _worktree_merge_error(aid, boundary_message),
            }

    sibling_gate = await _sibling_branch_divergence_gate_for_merge(
        state,
        cell,
        worktree_mgr,
        aid,
        data,
    )
    if sibling_gate:
        return {
            "ok": False,
            "boundary_state": boundary_state,
            "result": sibling_gate,
        }

    stale_base = (
        await worktree_mgr.stale_base_info(
            cell,
            worktree_submodules=worktree_submodules,
        )
        if worktree_submodules
        else await worktree_mgr.stale_base_info(cell)
    )
    if stale_base.get("stale") and not _stale_base_force_enabled(data):
        return {
            "ok": False,
            "boundary_state": boundary_state,
            "stale_base": stale_base,
            "result": _stale_base_merge_result(aid, stale_base),
        }

    stale_base_override_event = None
    if stale_base.get("stale") and _stale_base_force_enabled(data):
        stale_base_override_event = (
            _emit_stale_base_override_workflow_breach(
                state,
                panel_event,
                cell,
                stale_base,
            )
        )

    repo_root = str(
        cell.worktree_repo_root or cell.git_root or cell.worktree_path or ""
    ).strip()
    base_ref = str(cell.worktree_base_branch or "").strip()
    candidate_ref = str(cell.worktree_branch or "").strip()
    try:
        # The invariant checker validates the repository and revisions itself.
        # Do not turn a missing or stale configured root into an exclusion: an
        # unverifiable merge must fail closed before any merge-side effects.
        backend_modularity = await asyncio.to_thread(
            check_backend_modularity_crossings,
            repo_root,
            base_ref,
            candidate_ref,
        )
    except Exception as exc:
        result = _worktree_merge_error(
            aid,
            f"Backend modularity preflight could not run: {exc}",
            phase="backend_modularity",
        )
        _attach_stale_base(result, stale_base)
        return {
            "ok": False,
            "boundary_state": boundary_state,
            "stale_base": stale_base,
            "backend_modularity": {
                "ok": False,
                "phase": "backend_modularity",
                "error": str(exc),
            },
            "workflow_breach": (
                boundary_override_event or stale_base_override_event
            ),
            "result": result,
        }
    if not backend_modularity.get("ok"):
        result = _worktree_merge_error(
            aid,
            format_backend_modularity_crossings(backend_modularity),
            phase="backend_modularity",
            backend_modularity=backend_modularity,
        )
        _attach_stale_base(result, stale_base)
        return {
            "ok": False,
            "boundary_state": boundary_state,
            "stale_base": stale_base,
            "backend_modularity": backend_modularity,
            "workflow_breach": (
                boundary_override_event or stale_base_override_event
            ),
            "result": result,
        }

    # Always run the initial superproject merge/overwrite guard before any
    # nested submodule publish/merge side effects.  Do not pass
    # worktree_submodules here: that variant runs nested publish preflight
    # first and can reject the PR path before it gets a chance to publish the
    # worker's nested branch.  The only conflict we may defer is the configured
    # nested gitlink conflict that the later nested reconciliation is designed
    # to resolve.
    precheck = await worktree_mgr.check_merge_conflicts(cell)
    nested_gitlink_reconciliation_required = False
    if not precheck.get("clean"):
        nested_gitlink_reconciliation_required = bool(
            worktree_submodules
            and _is_reconcilable_nested_gitlink_conflict(
                precheck,
                worktree_submodules,
            )
        )
        if nested_gitlink_reconciliation_required:
            precheck["nested_submodule_reconciliation_required"] = True
        else:
            result = _worktree_merge_error(
                aid,
                precheck.get("error", "Conflicts detected"),
            )
            _attach_stale_base(result, stale_base)
            return {
                "ok": False,
                "boundary_state": boundary_state,
                "stale_base": stale_base,
                "precheck": precheck,
                "workflow_breach": (
                    boundary_override_event or stale_base_override_event
                ),
                "result": result,
            }

    if precheck.get("clean") and precheck.get("tree_sha"):
        overwrite_paths = (
            await worktree_mgr.merge_untracked_overwrite_paths(
                cell.worktree_repo_root or cell.git_root or "",
                cell.worktree_base_branch or "",
                precheck.get("tree_sha", ""),
            )
        )
        if overwrite_paths:
            result = _worktree_merge_error(
                aid,
                _untracked_overwrite_message(
                    overwrite_paths,
                    operation="merge",
                    location="the checked-out base repo",
                ),
                overwrite_paths=overwrite_paths,
            )
            _attach_stale_base(result, stale_base)
            return {
                "ok": False,
                "boundary_state": boundary_state,
                "stale_base": stale_base,
                "precheck": precheck,
                "workflow_breach": (
                    boundary_override_event or stale_base_override_event
                ),
                "result": result,
            }

    if worktree_submodules and publish_nested_submodule_branches:
        publish_nested = getattr(
            worktree_mgr,
            "publish_nested_submodule_branches_for_merge",
            None,
        )
        if not callable(publish_nested):
            result = _worktree_merge_error(
                aid,
                "Nested submodule branch publishing is unavailable.",
                phase="nested_submodule_publish",
            )
            _attach_stale_base(result, stale_base)
            return {
                "ok": False,
                "boundary_state": boundary_state,
                "stale_base": stale_base,
                "workflow_breach": (
                    boundary_override_event or stale_base_override_event
                ),
                "result": result,
            }
        published = await publish_nested(cell, worktree_submodules)
        if not published.get("ok"):
            result = _worktree_merge_error(
                aid,
                published.get(
                    "error",
                    "Nested submodule branch publish failed.",
                ),
                phase=published.get("phase", "nested_submodule_publish"),
                nested_submodules=published,
            )
            _attach_stale_base(result, stale_base)
            return {
                "ok": False,
                "boundary_state": boundary_state,
                "stale_base": stale_base,
                "workflow_breach": (
                    boundary_override_event or stale_base_override_event
                ),
                "result": result,
            }

    return {
        "ok": True,
        "boundary_state": boundary_state,
        "stale_base": stale_base,
        "precheck": precheck,
        "backend_modularity": backend_modularity,
        "workflow_breach": (
            boundary_override_event or stale_base_override_event
        ),
    }


async def _capture_worktree_merge_preserve_diff(
    *,
    state: MatrixState,
    cell,
    worktree_mgr: WorktreeManager,
    data: dict,
) -> tuple[bool, object | None, dict | None]:
    preserve_merge_diff = _worktree_merge_preserve_diff_enabled(
        state,
        cell,
        data,
    )
    boundary_task_for_diff = None
    merge_diff_snapshot = None
    if preserve_merge_diff:
        boundary_task_for_diff = _latest_open_boundary_task_for_cell(
            state,
            cell,
        )
        if boundary_task_for_diff:
            merge_diff_snapshot = await _worktree_merge_diff_snapshot(
                cell,
                worktree_mgr,
            )
    return preserve_merge_diff, boundary_task_for_diff, merge_diff_snapshot


def _capture_worktree_merge_resume_targets(
    state: MatrixState,
    cell,
) -> list[dict]:
    pre_merge_queued_followups = [
        t for t in state.board_tasks.values()
        if t.agent_id == cell.id
        and t.lane in {"Backlog", "To Do"}
    ]
    merge_resume_targets = []
    for followup in pre_merge_queued_followups:
        merge_resume_targets.extend(
            _capture_auto_resume_targets(
                state,
                task=followup,
                group=cell.group,
            )
        )
    return merge_resume_targets


def _worktree_merge_requested_cleanup(
    state: MatrixState,
    cell,
    data: dict,
    *,
    preserve_merge_diff: bool,
) -> dict:
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
    return {
        "close_agent_on_merge": close_flag,
        "remove_worktree_on_merge": remove_flag,
        "auto_move_to_done": bool(data.get("auto_move_to_done", True)),
        "preserve_merge_diff": bool(preserve_merge_diff),
    }


def _auto_force_push_metadata(push_result: dict | None) -> dict:
    push_result = push_result or {}
    if not push_result.get("auto_force_push"):
        return {}
    metadata = {
        "auto_force_push": True,
        "force_with_lease": bool(push_result.get("force_with_lease")),
        "reason": str(push_result.get("auto_force_reason") or "").strip(),
        "remote": str(push_result.get("remote") or "").strip(),
        "branch": str(push_result.get("branch") or "").strip(),
        "base_branch": str(push_result.get("base_branch") or "").strip(),
        "remote_sha": str(push_result.get("remote_sha") or "").strip(),
        "local_sha": str(push_result.get("local_sha") or "").strip(),
        "base_sha": str(push_result.get("base_sha") or "").strip(),
        "force_lease_ref": str(push_result.get("force_lease_ref") or "").strip(),
        "force_lease_sha": str(push_result.get("force_lease_sha") or "").strip(),
    }
    return {key: value for key, value in metadata.items() if value}


def _attach_auto_force_push_metadata(result: dict,
                                     push_result: dict | None) -> dict:
    metadata = _auto_force_push_metadata(push_result)
    if metadata:
        result["auto_force_push"] = True
        result["push"] = metadata
    return result


def _append_post_success_warning(result: dict, message: str, *,
                                 phase: str = "",
                                 detail: dict | None = None) -> dict:
    """Attach a non-fatal warning for work done after merge success."""
    if not isinstance(result, dict):
        return result
    message = str(message or "").strip()
    if not message:
        return result
    existing = str(result.get("warning") or "").strip()
    result["warning"] = f"{existing}\n{message}" if existing else message
    entry = {"message": message}
    if phase:
        entry["phase"] = phase
    if isinstance(detail, dict):
        entry["detail"] = detail
    result.setdefault("post_success_warnings", []).append(entry)
    return result


def _post_success_result_error(result: dict | None, fallback: str) -> str:
    result = result or {}
    return str(
        result.get("error")
        or result.get("message")
        or result.get("stderr")
        or result.get("stdout")
        or fallback
    ).strip()


def _post_success_cleanup_warning(cleanup: dict | None) -> str:
    cleanup = cleanup or {}
    errors = [
        str(item or "").strip()
        for item in (cleanup.get("errors") or [])
        if str(item or "").strip()
    ]
    if not errors:
        return ""
    return (
        "Merge landed, but post-merge cleanup reported warnings: "
        + "; ".join(errors)
    )


def _sha_equal(left: str, right: str) -> bool:
    return bool(left and right and str(left).strip() == str(right).strip())


def _pr_status_indicates_merged(status: dict | None) -> bool:
    if not isinstance(status, dict):
        return False
    state_text = str(status.get("state") or "").strip().upper()
    return (
        state_text == "MERGED"
        or bool(str(status.get("merged_at") or "").strip())
        or bool(str(status.get("mergedAt") or "").strip())
        or bool(str(status.get("merge_commit_sha") or "").strip())
    )


def _merge_commit_sha_from_status(status: dict | None) -> str:
    if not isinstance(status, dict):
        return ""
    merge_sha = str(status.get("merge_commit_sha") or "").strip()
    if merge_sha:
        return merge_sha
    merge_commit = status.get("mergeCommit")
    if isinstance(merge_commit, dict):
        return str(
            merge_commit.get("oid")
            or merge_commit.get("sha")
            or merge_commit.get("id")
            or ""
        ).strip()
    if merge_commit:
        return str(merge_commit).strip()
    return ""


def _merge_commit_sha_from_sources(*sources: dict | None) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("merge_commit_sha", "sha"):
            sha = str(source.get(key) or "").strip()
            if sha:
                return sha
        sha = _merge_commit_sha_from_status(source.get("pr_status"))
        if sha:
            return sha
    return ""


def _candidate_pr_statuses(*sources: dict | None) -> list[dict]:
    statuses: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        nested = source.get("pr_status")
        if isinstance(nested, dict):
            statuses.append(nested)
        if source.get("state") or source.get("merged_at") \
                or source.get("mergedAt") or source.get("merge_commit_sha"):
            statuses.append(source)
    return statuses


def _pr_selector_from_sources(*sources: dict | None) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get("number") or source.get("url")
        if value not in {None, ""}:
            return str(value).strip()
        nested = source.get("pr_status")
        if isinstance(nested, dict):
            value = nested.get("number") or nested.get("url")
            if value not in {None, ""}:
                return str(value).strip()
    return ""


def _base_match_from_result(result: dict | None, merge_sha: str) -> dict | None:
    if not isinstance(result, dict):
        return None
    for key in ("base_sha", "remote_sha"):
        sha = str(result.get(key) or "").strip()
        if _sha_equal(sha, merge_sha):
            return {
                "source": "remote_base_sync",
                "key": key,
                "sha": sha,
                "result": result,
            }
    return None
