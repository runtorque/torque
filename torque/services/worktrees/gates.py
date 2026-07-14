"""Worktree orchestration: gates."""

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

from .runtime import _resolve_agent_id
from .submodules import _summarize_paths

def _untracked_overwrite_message(paths: list[str], *,
                                 operation: str,
                                 location: str) -> str:
    summary = _summarize_paths(paths)
    return (
        f"Untracked files in {location} would be overwritten by {operation}: "
        f"{summary}. Move or remove them before retrying."
    )


def _stale_base_warning(stale_base: dict | None, *,
                        rebase_command: str = "") -> str:
    stale_base = stale_base or {}
    if not stale_base.get("stale"):
        return ""
    if rebase_command:
        return format_stale_base_warning(
            stale_base,
            rebase_command=rebase_command,
        )
    return str(stale_base.get("warning", "") or "").strip() \
        or format_stale_base_warning(stale_base)


def _stale_base_rebase_command(aid: str) -> str:
    aid = str(aid or "").strip()
    return f"worktree_rebase id={aid}" if aid else "worktree_rebase"


def _stale_base_post_rebase_evidence_required(
    stale_base: dict | None,
    *,
    post_rebase_head_sha: str = "",
    base_head_sha: str = "",
    review_boundary_updated=None,
    review_boundary_task_id: str = "",
) -> dict:
    evidence = stale_base_post_rebase_evidence_template(stale_base)
    post_head = str(post_rebase_head_sha or "").strip()
    base_head = str(base_head_sha or "").strip()
    if post_head:
        evidence["post_rebase_head_sha"] = post_head
    if base_head:
        evidence["base_head_sha"] = base_head
    if review_boundary_updated is not None:
        evidence["review_boundary_updated"] = bool(review_boundary_updated)
    task_id = str(review_boundary_task_id or "").strip()
    if task_id:
        evidence["review_boundary_task_id"] = task_id
    evidence["rerun_tests"] = [
        "<exact command(s) rerun after rebase; do not write 'tests passed' without commands>"
    ]
    evidence["note"] = (
        "Include this shape in the next feature/review derive context or "
        "merge handoff after rerunning the relevant tests."
    )
    return evidence


def _stale_base_suggestion(aid: str, *, retry_action: str) -> str:
    command = _stale_base_rebase_command(aid)
    retry_action = str(retry_action or "retry").strip() or "retry"
    return f"Run `{command}` then {retry_action}."


def _attach_stale_base_guidance(result: dict, aid: str, *,
                                retry_action: str) -> dict:
    result["code"] = "stale_base"
    result["suggested_command"] = _stale_base_rebase_command(aid)
    result["suggestion"] = _stale_base_suggestion(
        aid,
        retry_action=retry_action,
    )
    stale_base = result.get("stale_base")
    if isinstance(stale_base, dict):
        result["post_rebase_evidence_required"] = (
            _stale_base_post_rebase_evidence_required(stale_base)
        )
    return result


def _attach_stale_base(result: dict, stale_base: dict | None) -> dict:
    warning = _stale_base_warning(stale_base)
    if warning:
        result["stale_base"] = stale_base
        result["stale_base_warning"] = warning
    return result


def _stale_base_check_merge_result(aid: str,
                                   stale_base: dict | None) -> dict:
    warning = _stale_base_warning(stale_base)
    return _attach_stale_base_guidance({
        "type": "worktree_check_merge",
        "id": aid,
        "clean": False,
        "dirty": False,
        "conflicts": [],
        "error": warning,
        "stale_base": stale_base,
        "stale_base_warning": warning,
    }, aid, retry_action="retry merge readiness check")


def _stale_base_merge_result(aid: str, stale_base: dict | None) -> dict:
    warning = _stale_base_warning(stale_base)
    force_hint = (
        "Pass force=true only if you intentionally accept this risk; "
        "otherwise rebase and re-run the diff first."
    )
    suggestion = _stale_base_suggestion(
        aid,
        retry_action="re-run diff/review and retry merge",
    )
    return _attach_stale_base_guidance({
        "type": "worktree_merge",
        "id": aid,
        "ok": False,
        "error": (
            f"{warning}\n\nSuggested command: "
            f"`{_stale_base_rebase_command(aid)}`.\n{force_hint}"
            if warning else f"{suggestion}\n{force_hint}"
        ),
        "stale_base": stale_base,
        "stale_base_warning": warning,
    }, aid, retry_action="re-run diff/review and retry merge")


def _stale_base_review_derive_result(aid: str,
                                     stale_base: dict | None) -> dict:
    warning = _stale_base_warning(
        stale_base,
        rebase_command=_stale_base_rebase_command(aid),
    )
    suggestion = _stale_base_suggestion(
        aid,
        retry_action="re-run diff and derive feature/review",
    )
    message = (
        "Cannot derive feature/review from a stale worktree base.\n\n"
        f"{warning}\n\n"
        f"Suggested command: `{_stale_base_rebase_command(aid)}`."
    ) if warning else (
        "Cannot derive feature/review from a stale worktree base.\n\n"
        f"{suggestion}"
    )
    return _attach_stale_base_guidance({
        "type": "error",
        "message": message,
        "stale_base": stale_base,
        "stale_base_warning": warning,
    }, aid, retry_action="re-run diff and derive feature/review")


def _stale_base_force_enabled(data: dict | None) -> bool:
    data = data or {}
    return bool(data.get("force") or data.get("force_stale_base"))


def _boundary_mismatch_force_enabled(data: dict | None) -> bool:
    data = data or {}
    return bool(data.get("force_boundary_mismatch"))


def _boundary_mismatch_check_allowed(data: dict | None) -> bool:
    data = data or {}
    return bool(
        data.get("allow_boundary_mismatch")
        or data.get("force_boundary_mismatch")
    )


def _short_boundary_sha(sha: str) -> str:
    sha = str(sha or "").strip()
    if not sha:
        return "unknown"
    return sha[:12]


def _boundary_recorded_sha(boundary: dict | None) -> str:
    boundary = boundary if isinstance(boundary, dict) else {}
    recorded = boundary.get("boundary")
    if isinstance(recorded, dict):
        sha = str(
            recorded.get("commit_sha")
            or recorded.get("head_sha")
            or ""
        ).strip()
        if sha:
            return sha
    return str(
        boundary.get("boundary_sha")
        or boundary.get("commit_sha")
        or ""
    ).strip()


def _boundary_tip_sha(boundary: dict | None) -> str:
    boundary = boundary if isinstance(boundary, dict) else {}
    return str(
        boundary.get("tip_sha")
        or boundary.get("head_sha")
        or boundary.get("current_head_sha")
        or ""
    ).strip()


def _normalize_boundary_tip_mismatch_info(
        info: dict | None,
        *,
        boundary_sha: str,
        tip_sha: str) -> dict:
    info = dict(info or {})
    if boundary_sha and not info.get("boundary_sha"):
        info["boundary_sha"] = boundary_sha
    if tip_sha and not info.get("tip_sha"):
        info["tip_sha"] = tip_sha
    classification = str(
        info.get("classification")
        or info.get("state")
        or ""
    ).strip().lower()
    if not classification:
        if info.get("ancestor") is True:
            classification = "ahead"
        elif info.get("ancestor") is False:
            classification = "diverged"
        else:
            classification = "unknown"
    if classification in {"ancestor", "advanced"}:
        classification = "ahead"
    if classification in {"not_ancestor", "rewritten", "rewrite"}:
        classification = "diverged"
    info["classification"] = classification
    if "commit_count" not in info:
        count = info.get("ahead_count", info.get("commits", 0))
        try:
            info["commit_count"] = int(count)
        except (TypeError, ValueError):
            info["commit_count"] = 0
    return info


async def _ensure_boundary_tip_mismatch_info(
        worktree_mgr,
        cell,
        boundary: dict | None) -> dict:
    if not isinstance(boundary, dict):
        return {}
    existing = boundary.get("boundary_tip_mismatch")
    boundary_sha = _boundary_recorded_sha(boundary)
    tip_sha = _boundary_tip_sha(boundary)
    if isinstance(existing, dict) and existing:
        info = _normalize_boundary_tip_mismatch_info(
            existing,
            boundary_sha=boundary_sha,
            tip_sha=tip_sha,
        )
        boundary["boundary_tip_mismatch"] = info
        return info
    if not boundary_sha or not tip_sha:
        return {}
    classifier = getattr(worktree_mgr, "boundary_tip_mismatch_info", None)
    if not callable(classifier):
        return {}
    try:
        info = await classifier(cell, boundary_sha, tip_sha)
    except Exception:
        log.exception(
            "Failed to classify boundary-tip mismatch for '%s'",
            getattr(cell, "name", "") or getattr(cell, "id", ""),
        )
        return {}
    info = _normalize_boundary_tip_mismatch_info(
        info,
        boundary_sha=boundary_sha,
        tip_sha=tip_sha,
    )
    boundary["boundary_tip_mismatch"] = info
    return info


def _boundary_tip_mismatch_message(boundary: dict | None) -> str:
    boundary = boundary if isinstance(boundary, dict) else {}
    info = boundary.get("boundary_tip_mismatch")
    if not isinstance(info, dict):
        info = {}
    boundary_sha = str(
        info.get("boundary_sha")
        or _boundary_recorded_sha(boundary)
        or ""
    ).strip()
    classification = str(info.get("classification") or "").strip().lower()
    if classification == "ahead":
        try:
            count = int(info.get("commit_count", 0))
        except (TypeError, ValueError):
            count = 0
        return (
            f"Branch advanced {count} commit(s) past the last reviewed "
            f"boundary {_short_boundary_sha(boundary_sha)} — re-review "
            "the new commits or record a reviewed boundary at the tip."
        )
    if classification == "diverged":
        return (
            "Branch diverged from the last recorded boundary "
            f"{_short_boundary_sha(boundary_sha)} (history rewritten) — "
            "re-review required."
        )
    return (
        "Latest task boundary no longer matches the branch tip. "
        "A newer commit or external rewrite moved the branch."
    )


async def _boundary_gate_message(
        worktree_mgr,
        cell,
        reason: str,
        boundary: dict | None,
        boundary_reason_message) -> str:
    if reason == "branch_tip_moved":
        await _ensure_boundary_tip_mismatch_info(worktree_mgr, cell, boundary)
    return boundary_reason_message(reason, boundary)


async def _maybe_reject_stale_base_review_derive(
    worktree_mgr,
    cell,
    action_name: str,
) -> dict | None:
    if str(action_name or "").strip().lower() != "feature/review":
        return None
    if not (cell and getattr(cell, "worktree_path", "")):
        return None
    stale_info = getattr(worktree_mgr, "stale_base_info", None)
    if not callable(stale_info):
        return None
    try:
        stale_base = await stale_info(cell)
    except Exception:
        log.exception(
            "stale-base preflight failed before review derive for '%s'",
            getattr(cell, "name", "") or getattr(cell, "id", ""),
        )
        return None
    if not (stale_base or {}).get("stale"):
        return None
    return _stale_base_review_derive_result(
        getattr(cell, "id", "") or "",
        stale_base,
    )


def _pipeline_root_id_for_task(task) -> str:
    if not task:
        return ""
    return str(getattr(task, "pipeline_root_id", "") or task.id).strip()


def _agent_pipeline_root_ids(state: MatrixState, agent_id: str) -> set[str]:
    agent_id = str(agent_id or "").strip()
    if not state or not agent_id:
        return set()
    root_ids = {
        _pipeline_root_id_for_task(task)
        for task in state.board_tasks.values()
        if str(getattr(task, "agent_id", "") or "").strip() == agent_id
    }
    root_ids.discard("")
    return root_ids


def _configured_worktree_submodules_for_cell(state: MatrixState, cell) -> list[str]:
    if not state or not cell:
        return []
    try:
        gs = state.get_group_settings(getattr(cell, "group", "") or "")
        return list(getattr(gs, "worktree_submodules", []) or [])
    except Exception:
        return []


def _repo_rel_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _is_reconcilable_nested_gitlink_conflict(check: dict,
                                             submodules: list[str]) -> bool:
    if not check or check.get("clean"):
        return False
    allowed = {_repo_rel_path(path) for path in (submodules or []) if path}
    if not allowed:
        return False
    conflicts = check.get("conflicts", []) or []
    if not conflicts:
        return False
    conflict_paths = [
        _repo_rel_path(item.get("path", ""))
        for item in conflicts
        if isinstance(item, dict) and item.get("path")
    ]
    return bool(conflict_paths) and all(path in allowed for path in conflict_paths)


def _review_cycle_merge_sibling_candidates(
    state: MatrixState,
    cell,
) -> list[dict]:
    """Return sibling review/implement branches in the same pipeline roots."""
    if not state or not cell:
        return []
    target_agent_id = str(getattr(cell, "id", "") or "").strip()
    target_branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    target_repo = str(getattr(cell, "worktree_repo_root", "") or "").strip()
    if not target_agent_id or not target_branch:
        return []
    root_ids = _agent_pipeline_root_ids(state, target_agent_id)
    if not root_ids:
        return []

    candidates_by_branch: dict[str, dict] = {}
    eligible_actions = {"feature/implement", "feature/review"}
    for task in state.board_tasks.values():
        if _pipeline_root_id_for_task(task) not in root_ids:
            continue
        action_name = str(
            getattr(task, "action_name", "") or ""
        ).strip().lower()
        if action_name not in eligible_actions:
            continue
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if not agent_id or agent_id == target_agent_id:
            continue
        agent = state.agents.get(agent_id)
        if not agent:
            continue
        branch = str(getattr(agent, "worktree_branch", "") or "").strip()
        if not branch or branch == target_branch:
            continue
        repo_root = str(
            getattr(agent, "worktree_repo_root", "") or ""
        ).strip()
        if target_repo and repo_root and repo_root != target_repo:
            continue
        if branch in candidates_by_branch:
            candidates_by_branch[branch]["task_ids"].append(task.id)
            continue
        candidates_by_branch[branch] = {
            "branch": branch,
            "agent_id": agent_id,
            "agent_name": str(getattr(agent, "name", "") or ""),
            "agent_slug": str(getattr(agent, "slug", "") or ""),
            "task_id": task.id,
            "task_ids": [task.id],
            "task": str(getattr(task, "task", "") or ""),
            "action_name": action_name,
        }
    return list(candidates_by_branch.values())


async def _git_stdout(directory: str, *args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", directory, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode, stdout.decode().strip()
    except Exception:
        log.debug("git command failed for %s: %s", directory, " ".join(args))
        return 1, ""


async def _review_cycle_sibling_branch_divergence(
    state: MatrixState,
    cell,
    worktree_mgr: WorktreeManager,
) -> list[dict]:
    """Find sibling pipeline branches with commits absent from ``cell``."""
    if not state or not cell or not worktree_mgr:
        return []
    target_branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    if not target_branch:
        return []
    repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
    if not repo_root:
        repo_root = await worktree_mgr.get_repo_root(
            getattr(cell, "worktree_path", "") or ""
        ) or ""
    if not repo_root:
        return []

    submodule_paths = _configured_worktree_submodules_for_cell(state, cell)
    target_submodules: dict[str, dict] = {}
    if submodule_paths and hasattr(worktree_mgr, "nested_submodule_head_states"):
        try:
            target_submodules = {
                item.get("path", ""): item
                for item in await worktree_mgr.nested_submodule_head_states(
                    cell,
                    submodule_paths,
                )
                if item.get("path") and item.get("branch")
            }
        except Exception:
            log.exception(
                "Failed to inspect nested submodule branch state for '%s'",
                getattr(cell, "name", ""),
            )

    diverged: list[dict] = []
    for sibling in _review_cycle_merge_sibling_candidates(state, cell):
        branch = sibling.get("branch", "")
        if not branch:
            continue
        code, count_text = await _git_stdout(
            repo_root, "rev-list", "--count", f"{target_branch}..{branch}"
        )
        if code != 0:
            continue
        try:
            ahead = int((count_text.splitlines() or ["0"])[0] or 0)
        except ValueError:
            ahead = 0
        if ahead > 0:
            code, head_sha = await _git_stdout(repo_root, "rev-parse", branch)
            if code != 0:
                head_sha = ""
            item = dict(sibling)
            item["ahead"] = ahead
            item["head_sha"] = head_sha
            diverged.append(item)
        # Also gate sibling review/implement branches on the nested submodule
        # branch pair. A review sibling may have its superproject branch already
        # diffed while still carrying unmerged fixes in the submodule branch.
        if not target_submodules:
            continue
        sibling_agent = state.agents.get(sibling.get("agent_id", ""))
        if not sibling_agent:
            continue
        try:
            sibling_submodules = {
                item.get("path", ""): item
                for item in await worktree_mgr.nested_submodule_head_states(
                    sibling_agent,
                    submodule_paths,
                )
                if item.get("path") and item.get("branch")
            }
        except Exception:
            log.exception(
                "Failed to inspect sibling nested submodule branch state for '%s'",
                sibling.get("agent_name", ""),
            )
            sibling_submodules = {}
        for path, target_sub in target_submodules.items():
            sibling_sub = sibling_submodules.get(path)
            if not sibling_sub:
                continue
            sub_repo = sibling_sub.get("repo_root", "") or target_sub.get("repo_root", "")
            target_sub_branch = target_sub.get("branch", "")
            sibling_sub_branch = sibling_sub.get("branch", "")
            if not sub_repo or not target_sub_branch or not sibling_sub_branch:
                continue
            if target_sub_branch == sibling_sub_branch:
                continue
            code, sub_count_text = await _git_stdout(
                sub_repo,
                "rev-list",
                "--count",
                f"{target_sub_branch}..{sibling_sub_branch}",
            )
            if code != 0:
                continue
            try:
                sub_ahead = int(
                    (sub_count_text.splitlines() or ["0"])[0] or 0
                )
            except ValueError:
                sub_ahead = 0
            if sub_ahead <= 0:
                continue
            code, sub_head_sha = await _git_stdout(
                sub_repo,
                "rev-parse",
                sibling_sub_branch,
            )
            if code != 0:
                sub_head_sha = ""
            sub_item = dict(sibling)
            sub_item.update({
                "branch": sibling_sub_branch,
                "superproject_branch": branch,
                "submodule_path": path,
                "submodule": path,
                "repo_root": sub_repo,
                "ahead": sub_ahead,
                "head_sha": sub_head_sha,
            })
            diverged.append(sub_item)
    diverged.sort(key=lambda item: (item.get("branch", ""), item.get("task_id", "")))
    return diverged


def _sibling_branch_divergence_merge_result(aid: str,
                                            siblings: list[dict]) -> dict:
    branch_names = [
        str(item.get("branch", "") or "").strip()
        for item in (siblings or [])
        if str(item.get("branch", "") or "").strip()
    ]
    if not branch_names:
        subject = "A sibling branch has"
    elif len(branch_names) == 1:
        subject = f"sibling branch {branch_names[0]} has"
    else:
        subject = "sibling branches " + ", ".join(branch_names) + " have"
    message = (
        f"{subject} unmerged commits that may contain the Ship-verdict fix "
        "— diff sibling branches before merging. Pass force=true only after "
        "intentionally accepting this divergence."
    )
    return {
        "type": "worktree_merge",
        "id": aid,
        "ok": False,
        "code": "sibling_branch_divergence",
        "error": message,
        "siblings": siblings or [],
    }


async def _sibling_branch_divergence_gate_for_merge(
    state: MatrixState,
    cell,
    worktree_mgr: WorktreeManager,
    aid: str,
    data: dict,
) -> dict | None:
    if data.get("force"):
        return None
    siblings = await _review_cycle_sibling_branch_divergence(
        state,
        cell,
        worktree_mgr,
    )
    if not siblings:
        return None
    return _sibling_branch_divergence_merge_result(aid, siblings)


_WORKFLOW_BREACH_SUBKINDS = frozenset({
    "boundary_mismatch_override",
    "escape_clause_skip",
    "force_direct_merge",
    "merge_mode_locked",
    "stale_base_catch",
    "stale_base_override",
    "manual",
})
_WORKFLOW_BREACH_SOURCES = frozenset({"auto", "operator"})


def _workflow_breach_worker_for_task(state: MatrixState, task=None,
                                     worker_id: str = ""):
    """Resolve the worker implicated by a workflow-breach event, if any."""
    wid = str(worker_id or "").strip()
    if wid:
        return state.agents.get(_resolve_agent_id(state, wid) or wid)
    for field_name in ("agent_id", "reply_agent_id"):
        cid = str(getattr(task, field_name, "") or "").strip()
        if cid:
            cell = state.agents.get(cid)
            if cell:
                return cell
    return None


def _workflow_breach_engineer_for(state: MatrixState, *,
                                  task=None, worker=None):
    """Resolve the engineer whose per-cell history should own the event."""
    candidate_ids = [
        str(getattr(task, "assigned_engineer_id", "") or "").strip(),
        str(getattr(task, "created_by_engineer_id", "") or "").strip(),
        str(getattr(worker, "owner_engineer_id", "") or "").strip(),
        str(getattr(worker, "created_by_engineer_id", "") or "").strip(),
    ]
    if worker and str(getattr(worker, "kind", "") or "").strip() == "engineer":
        candidate_ids.append(str(getattr(worker, "id", "") or "").strip())

    for candidate_id in candidate_ids:
        if not candidate_id:
            continue
        cell = state.agents.get(candidate_id)
        if str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return cell
    return None


def _workflow_breach_active_task_for_worker(state: MatrixState, worker):
    if not worker:
        return None
    current_id = str(getattr(worker, "current_task_id", "") or "").strip()
    if current_id:
        current = state.board_tasks.get(current_id)
        try:
            if state.task_occupies_execution_slot(
                    current, agent_id=getattr(worker, "id", "")):
                return current
        except Exception:
            if current and getattr(current, "agent_id", "") == getattr(worker, "id", ""):
                return current
    current = state.agent_current_task(getattr(worker, "id", ""))
    if current:
        return current
    linked = [
        t for t in state.board_tasks.values()
        if getattr(t, "agent_id", "") == getattr(worker, "id", "")
        and getattr(t, "lane", "") not in ("Done", "Backlog", ARCHIVED_LANE)
    ]
    if len(linked) == 1:
        return linked[0]
    return None


def _format_workflow_breach_message(*, subkind: str, source: str,
                                    task_id: str = "", worker_id: str = "",
                                    branch: str = "",
                                    context: str = "") -> str:
    detail_parts = [f"source={source}"]
    if worker_id:
        detail_parts.append(f"worker={worker_id}")
    if branch:
        detail_parts.append(f"branch={branch}")
    if task_id:
        detail_parts.append(f"task={task_id}")
    details = " ".join(detail_parts)
    text = str(context or "").strip() or "Workflow-discipline breach reported."
    if details:
        return f"{subkind}: {text} ({details})"
    return f"{subkind}: {text}"


def _scope_domain_for_cell(state: MatrixState, cell) -> dict | None:
    """Resolve a cell's declared diff scope for the out-of-scope diff flag.

    Observability only (TORQUE:604 A2): used to annotate the diff summary, not
    to gate anything. Returns ``None`` when the task or its domain is ambiguous.
    """
    if not cell:
        return None
    task = None
    task_id = str(getattr(cell, "current_task_id", "") or "").strip()
    if task_id:
        task = state.board_tasks.get(task_id)
    if task is None:
        try:
            task = state.agent_current_task(getattr(cell, "id", ""))
        except Exception:
            task = None
    if task is None:
        return None
    scope = build_diff_scope_context(
        specialization=getattr(task, "suggested_specialization", "") or "",
        labels=getattr(task, "labels", None),
        task=getattr(task, "task", "") or "",
        description=getattr(task, "description", "") or "",
        context=getattr(task, "context", "") or "",
        criteria=getattr(task, "criteria", "") or "",
    )
    if not scope.get("domain") and not scope.get("allowed_foreign_domains"):
        return None
    return scope


def _emit_workflow_breach_event(state: MatrixState, panel_event, *,
                                subkind: str, source: str,
                                task=None, worker=None,
                                worker_id: str = "",
                                branch: str = "",
                                context: str = "") -> dict:
    """Persist/surface a workflow_breach panel event and return its shape."""
    subkind = str(subkind or "").strip() or "manual"
    source = str(source or "").strip() or "auto"
    if subkind not in _WORKFLOW_BREACH_SUBKINDS:
        subkind = "manual"
    if source not in _WORKFLOW_BREACH_SOURCES:
        source = "auto"

    if worker is None:
        worker = _workflow_breach_worker_for_task(
            state, task, worker_id=worker_id)
    if not worker_id and worker:
        worker_id = str(getattr(worker, "id", "") or "").strip()
    if not branch and worker:
        branch = str(getattr(worker, "worktree_branch", "") or "").strip()

    engineer = _workflow_breach_engineer_for(
        state, task=task, worker=worker)
    target = engineer or worker
    task_id = str(getattr(task, "id", "") or "").strip()
    group = (
        str(getattr(target, "group", "") or "").strip()
        or str(getattr(worker, "group", "") or "").strip()
        or str(getattr(task, "group", "") or "").strip()
    )
    agent_name = (
        str(getattr(target, "name", "") or "").strip()
        or str(getattr(target, "slug", "") or "").strip()
        or str(getattr(target, "id", "") or "").strip()
        or "torque"
    )
    cell_id = str(getattr(target, "id", "") or "").strip()
    message = _format_workflow_breach_message(
        subkind=subkind,
        source=source,
        task_id=task_id,
        worker_id=worker_id,
        branch=branch,
        context=context,
    )
    event = {
        "kind": "workflow_breach",
        "subkind": subkind,
        "task_id": task_id,
        "worker_id": worker_id,
        "branch": branch,
        "context": str(context or "").strip(),
        "source": source,
        "message": message,
        "cell_id": cell_id,
        "agent_name": agent_name,
        "group": group,
    }
    if panel_event:
        panel_event(
            "workflow_breach",
            cell_id,
            agent_name,
            group,
            message,
            task_id=task_id,
        )
    return event


def _handle_workflow_breach_command(data: dict, state: MatrixState,
                                    panel_event) -> dict:
    subkind = str(data.get("subkind", "") or "manual").strip()
    if subkind not in _WORKFLOW_BREACH_SUBKINDS:
        return {
            "type": "error",
            "message": (
                "Unknown workflow breach subkind "
                f"'{subkind}'. Expected one of: "
                + ", ".join(sorted(_WORKFLOW_BREACH_SUBKINDS))
            ),
        }
    task_id = _resolve_task_id(
        state,
        data.get("task_id", "") or data.get("task", "") or data.get("id", ""),
    )
    task = state.board_tasks.get(task_id)
    if not task:
        return {"type": "error", "message": "Task not found"}
    context = str(data.get("context", "") or "").strip()
    if not context:
        return {"type": "error", "message": "Workflow breach context required"}
    worker = _workflow_breach_worker_for_task(
        state, task, worker_id=data.get("worker_id", ""))
    event = _emit_workflow_breach_event(
        state,
        panel_event,
        subkind=subkind,
        source=str(data.get("source", "") or "operator"),
        task=task,
        worker=worker,
        worker_id=data.get("worker_id", ""),
        branch=data.get("branch", ""),
        context=context,
    )
    return {"type": "workflow_breach", "event": event}


def _emit_stale_base_catch_workflow_breach(state: MatrixState, panel_event,
                                           cell, stale_base: dict | None):
    warning = _stale_base_warning(stale_base)
    if not warning:
        return None
    breach_task = _workflow_breach_active_task_for_worker(state, cell)
    return _emit_workflow_breach_event(
        state,
        panel_event,
        subkind="stale_base_catch",
        source="auto",
        task=breach_task,
        worker=cell,
        context=(
            "Stale-base warning was followed by "
            f"rebase: {warning}"
        ),
    )


def _emit_stale_base_override_workflow_breach(state: MatrixState, panel_event,
                                             cell,
                                             stale_base: dict | None):
    warning = _stale_base_warning(stale_base)
    if not warning:
        return None
    breach_task = _workflow_breach_active_task_for_worker(state, cell)
    return _emit_workflow_breach_event(
        state,
        panel_event,
        subkind="stale_base_override",
        source="operator",
        task=breach_task,
        worker=cell,
        context=(
            "Stale-base merge gate was bypassed with force=true: "
            f"{warning}"
        ),
    )


def _boundary_mismatch_override_actor(state: MatrixState, cell,
                                      data: dict | None) -> str:
    data = data or {}
    for key in (
            "actor_agent_id",
            "_engineer_dispatch_id",
            "actor_id",
            "actor",
            "actor_name",
    ):
        value = str(data.get(key, "") or "").strip()
        if value:
            return value
    for attr in (
            "owner_engineer_id",
            "created_by_engineer_id",
            "id",
    ):
        value = str(getattr(cell, attr, "") or "").strip()
        if value:
            return value
    return "operator"


def _boundary_mismatch_override_reason(data: dict | None) -> str:
    data = data or {}
    for key in (
            "boundary_mismatch_reason",
            "force_boundary_mismatch_reason",
            "override_reason",
    ):
        value = str(data.get(key, "") or "").strip()
        if value:
            return value
    return "operator verified the branch tip against the reviewed boundary"


def _emit_boundary_mismatch_override_workflow_breach(
        state: MatrixState,
        panel_event,
        cell,
        boundary: dict | None,
        data: dict | None):
    boundary = boundary if isinstance(boundary, dict) else {}
    info = _normalize_boundary_tip_mismatch_info(
        boundary.get("boundary_tip_mismatch")
        if isinstance(boundary.get("boundary_tip_mismatch"), dict)
        else {},
        boundary_sha=_boundary_recorded_sha(boundary),
        tip_sha=_boundary_tip_sha(boundary),
    )
    boundary_sha = str(info.get("boundary_sha") or "").strip()
    tip_sha = str(info.get("tip_sha") or "").strip()
    actor = _boundary_mismatch_override_actor(state, cell, data)
    reason = _boundary_mismatch_override_reason(data)
    classification = str(info.get("classification") or "unknown").strip()
    breach_task = _workflow_breach_active_task_for_worker(state, cell)
    event = _emit_workflow_breach_event(
        state,
        panel_event,
        subkind="boundary_mismatch_override",
        source="operator",
        task=breach_task,
        worker=cell,
        context=(
            "Boundary-tip merge gate was bypassed with "
            "force_boundary_mismatch=true: "
            f"actor={actor} reason={reason} "
            f"boundary_sha={boundary_sha} tip_sha={tip_sha} "
            f"classification={classification}"
        ),
    )
    event["actor_agent_id"] = actor
    event["reason"] = reason
    event["boundary_sha"] = boundary_sha
    event["tip_sha"] = tip_sha
    event["boundary_mismatch"] = info
    return event
