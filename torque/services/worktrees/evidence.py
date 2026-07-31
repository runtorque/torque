"""Worktree orchestration: evidence."""

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


def _persist_preserved_merge_diff_warning_only(
    state: MatrixState,
    cell,
    boundary_task_for_diff,
    merge_diff_snapshot: dict | None,
    *,
    merge_commit_sha: str,
) -> str:
    if not boundary_task_for_diff:
        return (
            "Merge succeeded, but Torque could not preserve the pre-merge "
            "diff because no open branch boundary task was available."
        )

    if merge_diff_snapshot and merge_diff_snapshot.get("error"):
        log.warning(
            "Preserve-merge-diff capture failed for '%s': %s",
            cell.name,
            merge_diff_snapshot.get("error", ""),
        )
        return (
            "Merge succeeded, but Torque could not preserve the pre-merge "
            "diff because capturing the patch failed."
        )

    patch_text = str((merge_diff_snapshot or {}).get("patch_text", "") or "")
    if not patch_text:
        return (
            "Merge succeeded, but Torque could not preserve the pre-merge "
            "diff because the patch was empty."
        )

    artifact = None
    previous_artifacts = normalize_artifacts(
        boundary_task_for_diff.artifacts or []
    )
    previous_updated_at = boundary_task_for_diff.updated_at
    try:
        artifact = store_preserved_merge_diff(
            task_id=boundary_task_for_diff.id,
            patch_text=patch_text,
            worktree_branch=cell.worktree_branch or "",
            base_branch=cell.worktree_base_branch or "",
            merge_commit_sha=merge_commit_sha,
            boundary_task_id=boundary_task_for_diff.id,
            boundary_recorded_at=(
                (boundary_task_for_diff.worktree_boundary or {}).get(
                    "recorded_at", ""
                )
            ),
            boundary_task_title=boundary_task_for_diff.task,
            diff_stats=(merge_diff_snapshot or {}).get("stats"),
            diff_files=(merge_diff_snapshot or {}).get("files"),
            agent_id=cell.id,
            agent_name=cell.slug or cell.name,
            existing_artifacts=previous_artifacts,
        )
        state.board_update_task(
            boundary_task_for_diff.id,
            artifacts=previous_artifacts + [artifact],
        )
    except Exception:
        if artifact:
            artifact_path = str(artifact.get("path", "") or "").strip()
            if artifact_path:
                try:
                    Path(artifact_path).unlink(missing_ok=True)
                except Exception:
                    log.warning(
                        "Failed to clean up preserved merge diff artifact "
                        "after rollback for '%s': %s",
                        cell.name,
                        artifact_path,
                    )
        boundary_task_for_diff.artifacts = previous_artifacts
        boundary_task_for_diff.updated_at = previous_updated_at
        try:
            state._emit("task_upsert", **asdict(boundary_task_for_diff))
        except Exception:
            log.exception(
                "Failed to re-emit boundary task after preserved merge diff "
                "rollback for '%s'",
                cell.name,
            )
        log.exception(
            "Failed to persist preserved merge diff for '%s'",
            cell.name,
        )
        return (
            "Merge succeeded, but Torque could not save the preserved diff "
            "artifact."
        )
    return ""


def _derive_handoff_accepted(dispatch_result) -> bool:
    return bool(dispatch_result) and dispatch_result.get("type") in {
        "ok",
        "queued",
    }


def _record_engineer_dispatch_shape_metric(state: MatrixState, **kwargs):
    recorder = getattr(state, "record_engineer_dispatch_shape", None)
    if not callable(recorder):
        return None
    try:
        return recorder(**kwargs)
    except Exception:
        log.exception("Failed to record engineer dispatch shape metric")
        return None


def _record_derive_dispatch_shape_metric(
        state: MatrixState,
        *,
        engineer_id: str,
        group: str,
        result: dict,
        new_task,
        derive_parent_task_id: str,
        action_name: str,
        target_id: str,
        target_agent: str,
        reuse_self: bool,
        transition_target: str,
        reused_existing_task: bool):
    result_type = str((result or {}).get("type", "") or "").strip()
    if result_type not in {"ok", "queued"}:
        return None
    engineer_id = str(engineer_id or "").strip()
    if not engineer_id or not new_task:
        return None
    target_agent_id = str(
        target_id or (result or {}).get("agent_id", "") or ""
    ).strip()
    warm_cluster = bool(
        reuse_self
        or target_id
        or target_agent
        or (reused_existing_task and getattr(new_task, "agent_id", ""))
    )
    return _record_engineer_dispatch_shape_metric(
        state,
        engineer_id=engineer_id,
        group=group,
        source_tool="torque_derive",
        shape="warm_cluster" if warm_cluster else "serial",
        task_ids=[new_task.id],
        task_count=1,
        outcome=result_type,
        hintable=False,
        metadata={
            "parent_task_id": derive_parent_task_id,
            "action_name": action_name,
            "target_agent_id": target_agent_id,
            "target_agent": target_agent,
            "reuse_self": bool(reuse_self),
            "transition_target": transition_target,
            "reused_existing_task": bool(reused_existing_task),
        },
    )
