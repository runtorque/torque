"""Worktree orchestration: submodules."""

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


def _latest_open_boundary_task_for_cell(state: MatrixState, cell):
    if not cell:
        return None
    repo_root = cell.worktree_repo_root or cell.git_root or ""
    if not repo_root or not cell.worktree_branch:
        return None
    return latest_boundary_task(
        state.board_tasks.values(),
        repo_root=repo_root,
        branch=cell.worktree_branch,
        statuses={"open"},
    )


def _record_nested_submodule_metadata_on_latest_boundary(
    state: MatrixState,
    cell,
    nested_result: dict | None,
) -> dict | None:
    """Attach nested submodule PR state to the latest open branch boundary."""
    if not state or not cell or not isinstance(nested_result, dict):
        return None
    latest = _latest_open_boundary_task_for_cell(state, cell)
    if not latest:
        return None
    boundary = dict(task_boundary(latest))
    submodules = []
    for item in nested_result.get("submodules", []) or []:
        if not isinstance(item, dict):
            continue
        pr = item.get("pr") if isinstance(item.get("pr"), dict) else {}
        merge = item.get("merge") if isinstance(item.get("merge"), dict) else {}
        submodules.append({
            key: value
            for key, value in {
                "path": item.get("path", ""),
                "branch": item.get("branch", ""),
                "base_branch": item.get("base_branch", ""),
                "remote": item.get("remote", ""),
                "old_gitlink_sha": item.get("old_gitlink_sha", ""),
                "reviewed_sha": item.get("reviewed_sha", "")
                                or item.get("head_sha", ""),
                "merged_sha": item.get("merged_sha", ""),
                "merged_main_sha": item.get("merged_main_sha", ""),
                "pending": bool(item.get("pending")),
                "skipped": bool(item.get("skipped")),
                "skip_reason": item.get("skip_reason", ""),
                "pr": {
                    key: value
                    for key, value in {
                        "url": pr.get("url", ""),
                        "number": pr.get("number"),
                        "head_sha": pr.get("head_sha", ""),
                        "state": pr.get("state", ""),
                        "merge_state": pr.get("merge_state", ""),
                        "merge_commit_sha": pr.get("merge_commit_sha", ""),
                        "existing": pr.get("existing"),
                    }.items()
                    if value not in ("", None, {})
                },
                "merge": {
                    key: value
                    for key, value in {
                        "phase": merge.get("phase", ""),
                        "pending": merge.get("pending"),
                        "merge_commit_sha": merge.get("merge_commit_sha", ""),
                        "merge_state": merge.get("merge_state", ""),
                    }.items()
                    if value not in ("", None, {})
                },
            }.items()
            if value not in ("", None, {}, [])
        })
    boundary["nested_submodules"] = {
        "phase": nested_result.get("phase", ""),
        "pending": bool(nested_result.get("pending")),
        "pending_submodule_pr": bool(nested_result.get("pending_submodule_pr")),
        "real_delta": bool(nested_result.get("real_delta")),
        "submodules": submodules,
    }
    gitlink_bump = nested_result.get("gitlink_bump")
    if isinstance(gitlink_bump, dict):
        boundary["nested_submodules"]["gitlink_bump"] = {
            key: value
            for key, value in gitlink_bump.items()
            if value not in ("", None, {}, [])
        }
    latest.worktree_boundary = boundary
    latest.updated_at = datetime.now(timezone.utc).isoformat()
    state._emit("task_upsert", **asdict(latest))
    state._db_save_task(latest)
    return boundary


def _ee_pr_flow_submodules(worktree_submodules) -> list[str]:
    """Return configured submodule paths handled by the ee PR-first flow."""
    paths = []
    seen = set()
    for raw in worktree_submodules or []:
        path = str(raw or "").strip().strip("/")
        if not path or path in seen:
            continue
        parts = [part for part in path.split("/") if part]
        if parts and parts[-1] == "ee":
            paths.append(path)
            seen.add(path)
    return paths


def _legacy_nested_submodules(worktree_submodules,
                              pr_flow_submodules: list[str]) -> list[str]:
    pr_set = set(pr_flow_submodules or [])
    return [
        str(path or "").strip().strip("/")
        for path in (worktree_submodules or [])
        if str(path or "").strip().strip("/")
        and str(path or "").strip().strip("/") not in pr_set
    ]


def _combine_nested_submodule_results(primary: dict | None,
                                      secondary: dict | None) -> dict | None:
    if not primary:
        return secondary
    if not secondary:
        return primary
    combined = dict(primary)
    combined["submodules"] = (
        list(primary.get("submodules", []) or [])
        + list(secondary.get("submodules", []) or [])
    )
    combined["legacy_nested_submodules"] = secondary
    if "gitlink_bump" not in combined and isinstance(
        secondary.get("gitlink_bump"), dict
    ):
        combined["gitlink_bump"] = secondary["gitlink_bump"]
    combined["real_delta"] = bool(primary.get("real_delta")) or bool(
        secondary.get("submodules")
    )
    return combined


def _summarize_paths(paths: list[str], limit: int = 3) -> str:
    trimmed = [str(path or "").strip() for path in paths if str(path or "").strip()]
    if not trimmed:
        return ""
    if len(trimmed) <= limit:
        return ", ".join(trimmed)
    remaining = len(trimmed) - limit
    return ", ".join(trimmed[:limit]) + f" (+{remaining} more)"
