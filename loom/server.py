"""aiohttp server, WebSocket command handler, and runtime entry point."""

import asyncio
import contextlib
import json
import mimetypes
import os
import shutil
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import aiohttp
from aiohttp import web
from . import config as loom_config
from . import profiling
from .config import (
    WS_PORT,
    DB_FILE,
    WEBVIEW_FILE,
    STANDALONE,
    BIND_HOST,
    ATTACHMENTS_DIR,
    DATA_DIR,
    log,
)
from .db import LoomDB
from .deploy_state import capture_deploy_boot_state
from .doctor import build_doctor_report
from dataclasses import asdict
from .state import (
    ARCHIVED_LANE,
    BoardTask,
    MatrixState,
    hot_json_dumps_async,
    hot_json_dumps_bytes_async,
    merge_cleanup_flags,
    normalize_default_worker_concurrency,
    task_counts_as_done,
    task_is_closed,
)
from .events import (
    EventLog,
    EventBus,
    EventIngestDrainer,
    PanelEventLog,
    build_event_ingest_envelope,
    get_cell_event_stream,
    health_check,
)
from .adapters import get_adapter, get_providers
from .notifications import NotificationManager
from .worktree import WorktreeManager, format_stale_base_warning
from .worktree_boundaries import (
    boundary_summary,
    branch_boundary_tasks,
    latest_boundary_base_branch,
    latest_boundary_task,
    mark_branch_boundaries_merged,
    queued_successor_tasks,
    refresh_latest_boundary_after_rebase,
    retarget_queued_successor_tasks,
    started_successor_tasks,
    task_boundary,
)
from .actions import ActionManager, LOOM_CONTEXT_STUB
from .artifacts import (
    normalize_artifacts,
    task_artifacts,
)
from .attachment_uploads import (
    AttachmentUploadError,
    save_message_attachment_stream,
)
from .server_artifacts import (
    describe_task_artifact_for_digest,
    finalize_task_attachments,
    remove_task_owned_artifacts_by_filename,
    serialize_upstream_task_artifacts,
    serialize_task_artifact,
    store_preserved_merge_diff,
    store_task_upload,
)
from .task_ids import is_canonical_task_id, is_draft_task_token
from .memory import (
    build_memory_entry,
    build_memory_link,
    build_prompt_memory_block,
    detect_current_task,
    infer_project_key,
    load_visible_memory_entries,
    normalize_entry_type,
    normalize_link_target_kind,
    normalize_retention_kind,
)
from .roles import RoleManager
from .external_tickets import (
    ExternalTicketError,
    build_completion_comment,
    import_ticket as import_external_ticket,
    normalize_link as normalize_external_link,
    open_ticket_url,
    post_ticket_comment,
    push_ticket_status,
)
from .mcp import create_mcp_handler, dispatch_mcp_rpc_body
from .mcp_retry import api_request_hash, is_api_write_command, replay_failed_writes
from .identity import (
    agent_identity_anchor,
    agent_kind_for_identity,
    prepend_agent_identity_anchor,
)

from .server_actions import _action_to_yaml
from .server_agent import (
    AgentLaunchService,
    _append_task_artifacts,
    _build_self_dispatch_prompt,
    _copy_worktree_context,
    _new_agent_prompt_sequence,
    _startup_prompt_for_new_agent,
    mcp_entrypoint_for_cell,
    runtime_env_vars_for_cell,
)
from .server_dispatch import (
    _cells_share_worktree_context,
    _capture_auto_resume_targets,
    _find_active_worktree_owner,
    _maybe_auto_resume_targets,
    _pump_auto_dispatch_queue,
    _pump_auto_dispatch_queue_forever,
    _scheduler_loop,
    _should_handoff_shared_worktree,
    _should_queue_existing_agent_dispatch,
)
from .server_worktrees import (
    _generate_merge_message,
    _worktree_diff_updater,
    _worktree_full_diff,
    _worktree_merge_diff_snapshot,
)
from .server_prompts import (
    build_dispatch_postscript,
    build_loom_system_prompt,
)
from .weaver_session_map import build_weaver_session_map


def _should_install_keybindings() -> bool:
    """Keybindings/RPCs are only installed in iTerm2-hosted mode."""
    return not STANDALONE


def _resolve_task_id(state, identifier: str) -> str:
    """Resolve a task by canonical ID, legacy alias, or ID prefix.

    Legacy aliases take precedence over literal rows so archived rows whose IDs
    were later reused as aliases do not absorb writes meant for the live task.
    """
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    resolver = getattr(state, "resolve_board_task_id", None)
    if callable(resolver):
        resolved = resolver(ident)
        return resolved or ("" if ident in getattr(state, "task_id_aliases", {}) else ident)
    aliased = state.resolve_task_alias(ident)
    if aliased != ident:
        return aliased if aliased in state.board_tasks else ""
    if ident in state.board_tasks:
        return ident
    prefix_matches = [
        task.id for task in state.board_tasks.values()
        if task.id.startswith(ident)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return ident


def _resolve_agent_id(state, identifier: str) -> str:
    """Resolve an agent by exact ID, slug, name, or ID prefix."""
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    if ident in state.agents:
        cell = state.agents[ident]
        if cell.cell_type == "agent":
            return cell.id
    ident_lower = ident.lower()
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
        if cell.id.startswith(ident):
            return cell.id
    return ""


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


def _summarize_paths(paths: list[str], limit: int = 3) -> str:
    trimmed = [str(path or "").strip() for path in paths if str(path or "").strip()]
    if not trimmed:
        return ""
    if len(trimmed) <= limit:
        return ", ".join(trimmed)
    remaining = len(trimmed) - limit
    return ", ".join(trimmed[:limit]) + f" (+{remaining} more)"


def _untracked_overwrite_message(paths: list[str], *,
                                 operation: str,
                                 location: str) -> str:
    summary = _summarize_paths(paths)
    return (
        f"Untracked files in {location} would be overwritten by {operation}: "
        f"{summary}. Move or remove them before retrying."
    )


def _stale_base_warning(stale_base: dict | None) -> str:
    stale_base = stale_base or {}
    if not stale_base.get("stale"):
        return ""
    return str(stale_base.get("warning", "") or "").strip() \
        or format_stale_base_warning(stale_base)


def _attach_stale_base(result: dict, stale_base: dict | None) -> dict:
    warning = _stale_base_warning(stale_base)
    if warning:
        result["stale_base"] = stale_base
        result["stale_base_warning"] = warning
    return result


def _stale_base_check_merge_result(aid: str,
                                   stale_base: dict | None) -> dict:
    warning = _stale_base_warning(stale_base)
    return {
        "type": "worktree_check_merge",
        "id": aid,
        "clean": False,
        "dirty": False,
        "conflicts": [],
        "error": warning,
        "stale_base": stale_base,
        "stale_base_warning": warning,
    }


def _stale_base_merge_result(aid: str, stale_base: dict | None) -> dict:
    warning = _stale_base_warning(stale_base)
    force_hint = (
        "Pass force_stale_base=true only if you intentionally accept this "
        "risk; otherwise rebase and re-run the diff first."
    )
    return {
        "type": "worktree_merge",
        "id": aid,
        "ok": False,
        "error": f"{warning}\n\n{force_hint}" if warning else force_hint,
        "stale_base": stale_base,
        "stale_base_warning": warning,
    }


_WORKFLOW_BREACH_SUBKINDS = frozenset({
    "escape_clause_skip",
    "stale_base_catch",
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
        str(getattr(worker, "created_by_weaver_id", "") or "").strip(),
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
        or "loom"
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
            "Merge succeeded, but Loom could not preserve the pre-merge "
            "diff because no open branch boundary task was available."
        )

    if merge_diff_snapshot and merge_diff_snapshot.get("error"):
        log.warning(
            "Preserve-merge-diff capture failed for '%s': %s",
            cell.name,
            merge_diff_snapshot.get("error", ""),
        )
        return (
            "Merge succeeded, but Loom could not preserve the pre-merge "
            "diff because capturing the patch failed."
        )

    patch_text = str((merge_diff_snapshot or {}).get("patch_text", "") or "")
    if not patch_text:
        return (
            "Merge succeeded, but Loom could not preserve the pre-merge "
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
            "Merge succeeded, but Loom could not save the preserved diff "
            "artifact."
        )
    return ""


def _derive_handoff_accepted(dispatch_result) -> bool:
    return bool(dispatch_result) and dispatch_result.get("type") in {
        "ok",
        "queued",
    }


def _agent_can_receive_dispatch(cell) -> bool:
    return bool(
        cell
        and cell.cell_type == "agent"
        and cell.session_id
        and (cell.status or "") not in {"stopped", "error"}
    )


def _promote_task_for_active_report(state: MatrixState, cell, task) -> None:
    """Normalize a task into the dispatch lane once work has clearly started."""
    if not cell or not task:
        return
    if task.agent_id and task.agent_id != cell.id:
        return
    fields = {}
    if not task.agent_id:
        fields["agent_id"] = cell.id
    if task.lane in {"Backlog", "To Do"}:
        fields["lane"] = (
            state.get_group_settings(task.group).dispatch_lane
            or "In Progress"
        )
    if fields:
        state.board_update_task(task.id, **fields)
    if cell.current_task_id != task.id:
        cell.current_task_id = task.id
        state._emit_agent(cell)
        state._db_save_agent(cell)


def _reject_completion_with_open_descendants(state: MatrixState, task,
                                             action_name: str) -> dict | None:
    if not task:
        return None
    if not state.task_has_unresolved_descendants(task.id):
        return None
    return {
        "type": "error",
        "message":
            f"Cannot mark task {action_name}: "
            "derived follow-up work is still unresolved",
        "task_id": task.id,
    }


def _nearest_ancestor_agent_for_action_stage(state: MatrixState, task,
                                             action_name: str):
    """Find the closest ancestor already associated with ``action_name``."""
    if not task or not action_name:
        return None
    ancestor_id = task.parent_task_id
    while ancestor_id:
        ancestor = state.board_tasks.get(ancestor_id)
        if not ancestor:
            break
        if ancestor.action_name == action_name and ancestor.agent_id:
            agent = state.agents.get(ancestor.agent_id)
            if _agent_can_receive_dispatch(agent):
                return agent
        ancestor_id = ancestor.parent_task_id
    return None


def _looks_like_review_task(task) -> bool:
    if not task:
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if "review" in action_name:
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "on review":
        return True
    text = " ".join(
        part.strip().lower()
        for part in (
            str(getattr(task, "task", "") or ""),
            str(getattr(task, "description", "") or ""),
            action_name,
            status,
        )
        if part and part.strip()
    )
    return "review" in text or "re-review" in text


def _is_feature_review_task(task) -> bool:
    if not task:
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    return action_name == "feature/review"


def _task_ancestry_has_agent(state: MatrixState, task,
                             agent_id: str) -> bool:
    """Return whether ``task`` or any ancestor is assigned to ``agent_id``."""
    agent_id = str(agent_id or "").strip()
    if not state or not task or not agent_id:
        return False
    seen = set()
    cursor = task
    while cursor and getattr(cursor, "id", "") not in seen:
        seen.add(getattr(cursor, "id", ""))
        if str(getattr(cursor, "agent_id", "") or "").strip() == agent_id:
            return True
        parent_id = str(getattr(cursor, "parent_task_id", "") or "").strip()
        if not parent_id:
            break
        cursor = state.board_tasks.get(parent_id)
    return False


def _active_shared_worktree_review_for_cell(state: MatrixState, cell):
    """Return an active reviewer task that owns ``cell``'s shared worktree.

    LOOM:88 intentionally launches feature/review workers in the
    implementer's worktree. During that review window, the implementer is a
    suspended ancestor in the task graph, so Loom-originated checkpoint writes
    from that implementer must fail closed while the reviewer owns the mutable
    branch.
    """
    if (
        not state
        or not cell
        or not (cell.worktree_path or cell.worktree_branch)
    ):
        return None
    cell_id = str(getattr(cell, "id", "") or "").strip()
    if not cell_id:
        return None

    for task in state.board_tasks.values():
        if not _is_feature_review_task(task):
            continue
        if task_is_closed(task):
            continue
        reviewer_id = str(getattr(task, "agent_id", "") or "").strip()
        if not reviewer_id or reviewer_id == cell_id:
            continue
        # When a review derives blocker fixes back to the implementer, the
        # review task remains open/status=Fixing but no longer owns the
        # foreground mutable branch; the descendant fix task does.
        if not state.task_occupies_execution_slot(
                task,
                agent_id=reviewer_id):
            continue
        reviewer = state.agents.get(reviewer_id)
        if not _cells_share_worktree_context(cell, reviewer):
            continue
        if _task_ancestry_has_agent(state, task, cell_id):
            return task
    return None


def _shared_review_checkpoint_block_reason(state: MatrixState, cell) -> str:
    """Explain why ``cell`` cannot checkpoint during an active review."""
    review_task = _active_shared_worktree_review_for_cell(state, cell)
    if not review_task:
        return ""
    review_label = getattr(review_task, "id", "") or "active review"
    cell_label = getattr(cell, "name", "") or getattr(cell, "id", "")
    return (
        f"Cannot checkpoint '{cell_label}' while "
        f"feature/review task {review_label} is active on the shared "
        "worktree. Checkpoint the reviewer worker instead, or wait for "
        "the review to finish."
    )


def _normalized_review_verdict_line(line: str) -> str:
    text = str(line or "").strip()
    while text[:1] in {"#", ">", "-", "*"}:
        text = text[1:].strip()
    for token in ("**", "__", "`"):
        text = text.replace(token, "")
    text = text.strip()
    lower = text.lower()
    if lower.startswith("verdict"):
        rest = text[len("verdict"):].lstrip()
        if rest[:1] in {":", "-", "—", "–"}:
            text = rest[1:].strip()
        else:
            text = rest.strip()
    return text.strip()


def _review_verdict_from_message(message: str) -> str:
    """Return ``ship`` or a non-ship verdict parsed from a review message.

    This is intentionally a lightweight free-form parser: explicit verdict
    lines may have markdown/bullet prefixes and varied casing, but paraphrases
    such as "looks good" or "approved" fail closed so reviewer cleanup does
    not fire from an ambiguous message.
    """
    for line in reversed(str(message or "").splitlines()):
        text = _normalized_review_verdict_line(line)
        if not text:
            continue
        lower = text.lower().strip(" .")
        if lower.startswith("ship with fixes"):
            return "ship_with_fixes"
        if lower.startswith(("needs rework", "needs changes", "blocker")):
            return "needs_rework"
        if lower == "ship" or lower == "ship it":
            return "ship"
        if lower.startswith("ship") and len(lower) > 4:
            next_char = lower[4]
            if next_char in {" ", ":", "-", "—", "–", ",", ";"}:
                return "ship"
    return ""


def _review_task_has_ship_verdict(task) -> bool:
    if not task:
        return False
    for entry in reversed(getattr(task, "messages", []) or []):
        if str(entry.get("action", "") or "").lower() != "done":
            continue
        verdict = _review_verdict_from_message(entry.get("message", ""))
        if verdict:
            return verdict == "ship"
    return False


_REVIEW_GATE_ACTION = "feature/review"


def _review_gate_threshold_from_action(act: dict | None) -> int | None:
    """Return an action's review-required LOC threshold, if configured."""
    if not isinstance(act, dict) or "review_required_above_loc" not in act:
        return None
    try:
        threshold = int(act.get("review_required_above_loc"))
    except (TypeError, ValueError):
        return None
    return threshold if threshold >= 0 else None


def _has_review_gate_transition(transitions: list) -> bool:
    """Return whether an action can transition to the review gate action."""
    for transition in transitions or []:
        if not isinstance(transition, dict):
            continue
        action_name = str(transition.get("action", "") or "").strip()
        if action_name == _REVIEW_GATE_ACTION:
            return True
    return False


def _chain_has_shipped_review(state: MatrixState, task) -> bool:
    """Return whether this task chain already has a closed Ship review."""
    if not state or not task:
        return False
    for chain_task in state.board_get_chain(task.id):
        if not task_counts_as_done(chain_task):
            continue
        if not _is_feature_review_task(chain_task):
            continue
        if _review_task_has_ship_verdict(chain_task):
            return True
    return False


def _review_gate_diff_size(summary: dict) -> int:
    """Return insertions + deletions from a diff summary dict."""
    try:
        insertions = int((summary or {}).get("insertions", 0) or 0)
    except (TypeError, ValueError):
        insertions = 0
    try:
        deletions = int((summary or {}).get("deletions", 0) or 0)
    except (TypeError, ValueError):
        deletions = 0
    return max(0, insertions) + max(0, deletions)


def _review_gate_skip_audit_message(cell, task, *,
                                    diff_size: int,
                                    threshold: int,
                                    reason: str) -> str:
    worker_id = str(getattr(cell, "id", "") or "").strip()
    worker_name = str(getattr(cell, "name", "") or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    reason = str(reason or "").strip() or "force-skip-review"
    worker = worker_id
    if worker_name:
        worker = f"{worker_id} ({worker_name})" if worker_id else worker_name
    return (
        "Review gate skipped by worker "
        f"{worker} for task {task_id}: diff size {diff_size} LOC, "
        f"threshold {threshold}; reason: {reason}"
    )


async def _maybe_apply_review_required_gate(
        state: MatrixState,
        action_mgr: ActionManager,
        worktree_mgr: WorktreeManager,
        handle_command,
        panel_event,
        *,
        cell,
        task,
        base_dir: str = "",
        force_skip_review: bool = False,
        skip_reason: str = "",
        checkpoint_for_gate=None,
        append_task_msg=None,
        record_history_msg=None) -> dict | None:
    """Enforce action-level review-required-above-LOC metadata.

    Returns an error result when direct completion is refused, otherwise None
    so the caller can proceed with the normal closeout path.
    """
    if not state or not cell or not task or not task.action_name:
        return None

    act = action_mgr.load_action(task.action_name, base_dir)
    threshold = _review_gate_threshold_from_action(act)
    if threshold is None:
        return None

    transitions = action_mgr.get_transitions(task.action_name, base_dir)
    if not _has_review_gate_transition(transitions):
        return None

    if _chain_has_shipped_review(state, task):
        return None

    if checkpoint_for_gate:
        await checkpoint_for_gate()

    try:
        diff_summary = await worktree_mgr.diff_summary(
            cell,
            non_test_only=True,
        )
    except TypeError:
        # Test doubles or older integrations may not accept the keyword.
        diff_summary = await worktree_mgr.diff_summary(cell)
    diff_size = _review_gate_diff_size(diff_summary)
    if diff_size <= threshold:
        return None

    if force_skip_review:
        reason = str(skip_reason or "").strip() or "force-skip-review"
        audit = _review_gate_skip_audit_message(
            cell,
            task,
            diff_size=diff_size,
            threshold=threshold,
            reason=reason,
        )
        if append_task_msg:
            append_task_msg(task, "review_gate_skipped", audit, cell.name)
        elif task:
            task.messages.append({
                "timestamp": time.time(),
                "action": "review_gate_skipped",
                "message": audit,
                "agent_name": getattr(cell, "name", ""),
            })
        if record_history_msg:
            record_history_msg(
                cell,
                "review_gate_skipped",
                audit,
                task_override=task,
            )
        if panel_event:
            panel_event(
                "review_gate_skipped",
                cell.id,
                cell.name,
                cell.group,
                audit,
                task_id=task.id,
            )
            _emit_workflow_breach_event(
                state,
                panel_event,
                subkind="escape_clause_skip",
                source="auto",
                task=task,
                worker=cell,
                context=audit,
            )
        return None

    title = f"Review required — diff exceeded {threshold} LOC threshold"
    context = (
        f"Review required — diff exceeded {threshold} LOC threshold. "
        "Please review and return Ship / Ship with fixes / Revert.\n\n"
        "Gate details:\n"
        f"- Worker: {cell.id} ({cell.name})\n"
        f"- Task: {task.id}\n"
        f"- Diff: {diff_size} non-test LOC "
        f"({(diff_summary or {}).get('insertions', 0)} insertions + "
        f"{(diff_summary or {}).get('deletions', 0)} deletions across "
        f"{(diff_summary or {}).get('files', 0)} non-test files)\n"
        f"- Threshold: {threshold}\n"
    )
    derive_result = await handle_command({
        "cmd": "ai_report",
        "cell_id": cell.id,
        "action": "derive",
        "task_id": task.id,
        "action_name": _REVIEW_GATE_ACTION,
        "message": title,
        "description": context,
    })
    if derive_result and derive_result.get("type") == "error":
        return {
            "type": "error",
            "message": (
                "Cannot close directly — review gate required "
                f"(diff: {diff_size} LOC, threshold: {threshold}), but "
                "auto-deriving `feature/review` failed: "
                f"{derive_result.get('message', 'unknown error')}"
            ),
        }

    review_task_id = (derive_result or {}).get("task_id", "")
    breach_context = (
        "Review gate auto-derived "
        f"{review_task_id or _REVIEW_GATE_ACTION} after direct done attempt; "
        f"diff {diff_size} non-test LOC exceeded threshold {threshold}."
    )
    _emit_workflow_breach_event(
        state,
        panel_event,
        subkind="escape_clause_skip",
        source="auto",
        task=task,
        worker=cell,
        context=breach_context,
    )
    review_task_label = review_task_id or "the review task"
    return {
        "type": "error",
        "message": (
            "Cannot close directly — `feature/review` auto-derived at "
            f"{review_task_label} per action gate (diff: {diff_size} LOC, "
            f"threshold: {threshold}). Wait for reviewer's Ship verdict "
            "before calling `loom ai done` again."
        ),
        "task_id": review_task_id,
        "review_gate": {
            "diff_size": diff_size,
            "threshold": threshold,
            "review_task_id": review_task_id,
        },
    }


def _shipped_review_cleanup_candidates(state: MatrixState, merged_cell) -> list:
    """Return reviewer agents whose Ship verdict should be cleaned post-merge."""
    if not state or not merged_cell:
        return []
    root_ids = {
        str(getattr(task, "pipeline_root_id", "") or task.id).strip()
        for task in state.board_tasks.values()
        if getattr(task, "agent_id", "") == getattr(merged_cell, "id", "")
    }
    root_ids.discard("")
    if not root_ids:
        return []

    candidates = []
    seen_agent_ids = set()
    for root_id in sorted(root_ids):
        for task in state.board_get_chain(root_id):
            if not task_counts_as_done(task):
                continue
            if not _is_feature_review_task(task):
                continue
            if not _review_task_has_ship_verdict(task):
                continue
            agent_id = str(getattr(task, "agent_id", "") or "").strip()
            if (
                not agent_id
                or agent_id == getattr(merged_cell, "id", "")
                or agent_id in seen_agent_ids
            ):
                continue
            if _agent_has_open_assigned_tasks(state, agent_id):
                continue
            if _agent_has_targeted_auto_dispatch_work(state, agent_id):
                continue
            if _agent_has_pending_weaver_followups(state, agent_id):
                continue
            cell = state.agents.get(agent_id)
            if not cell or getattr(cell, "cell_type", "") != "agent":
                continue
            seen_agent_ids.add(agent_id)
            candidates.append(cell)
    return candidates


async def _cleanup_shipped_reviewers_for_merged_cell(
        state: MatrixState,
        merged_cell,
        cleanup_after_merge,
) -> dict:
    """Close/remove Ship reviewers after their parent branch has merged."""
    summary = {
        "close_agent": True,
        "remove_worktree": True,
        "agents": [],
        "agent_closed": 0,
        "worktree_removed": 0,
        "errors": [],
    }
    for reviewer in _shipped_review_cleanup_candidates(state, merged_cell):
        summary["agents"].append(reviewer.id)
        cleanup = await cleanup_after_merge(
            reviewer,
            close_agent=True,
            remove_worktree=True,
        )
        if cleanup.get("agent_closed"):
            summary["agent_closed"] += 1
        if cleanup.get("worktree_removed"):
            summary["worktree_removed"] += 1
        summary["errors"].extend(cleanup.get("errors", []) or [])
    return summary


def _is_generic_review_fix_task(task) -> bool:
    if not task:
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    return action_name == "feature/fix-review" or "review-fix" in labels


def _find_reusable_review_fix_task(state: MatrixState, task,
                                   action_name: str):
    """Return an unresolved generic review-fix task in the active review loop."""
    if str(action_name or "").strip().lower() != "feature/fix-review":
        return None
    review_task = task if _looks_like_review_task(task) else None
    ancestor_id = str(getattr(task, "parent_task_id", "") or "").strip() if task else ""
    while not review_task and ancestor_id:
        ancestor = state.board_tasks.get(ancestor_id)
        if not ancestor:
            break
        if _looks_like_review_task(ancestor):
            review_task = ancestor
            break
        ancestor_id = str(getattr(ancestor, "parent_task_id", "") or "").strip()
    if not review_task:
        return None

    candidates = [
        child for child in state.board_get_children(review_task.id)
        if not task_is_closed(child)
        and _is_generic_review_fix_task(child)
    ]
    candidates.sort(
        key=lambda current: (
            getattr(current, "lane", "") != "In Progress",
            getattr(current, "lane", "") != "To Do",
            getattr(current, "created_at", "") or "",
            getattr(current, "id", "") or "",
        )
    )
    return candidates[0] if candidates else None


def _merge_reused_task_description(existing: str, incoming: str) -> str:
    existing = str(existing or "").strip()
    incoming = str(incoming or "").strip()
    if not incoming:
        return existing
    if not existing or existing == incoming:
        return incoming
    if incoming in existing:
        return existing
    return existing + "\n\n" + incoming


def _refresh_reused_derived_task(task, *, message: str,
                                 description: str = "",
                                 action_vars: dict | None = None) -> None:
    """Update a reused derived task so follow-up prompts use fresh guidance."""
    if not task:
        return
    message = str(message or "").strip()
    if message:
        task.task = message
    task.description = _merge_reused_task_description(
        getattr(task, "description", "") or "",
        description,
    )
    if action_vars:
        merged_vars = dict(getattr(task, "action_vars", {}) or {})
        merged_vars.update(action_vars)
        task.action_vars = merged_vars


def _agent_has_open_assigned_tasks(state: MatrixState, agent_id: str) -> bool:
    """Return whether the agent still owns any unresolved board task."""
    if not agent_id:
        return False
    for current in state.board_tasks.values():
        if current.agent_id != agent_id:
            continue
        if task_is_closed(current):
            continue
        return True
    return False


def _agent_has_targeted_auto_dispatch_work(state: MatrixState,
                                           agent_id: str) -> bool:
    """Return whether a queued auto-dispatch entry is pinned to this agent."""
    if not agent_id:
        return False
    for entries in state.auto_dispatch_queues.values():
        for entry in entries:
            if entry.target_agent_id != agent_id:
                continue
            queued = state.board_tasks.get(entry.task_id)
            if queued and not task_is_closed(queued):
                return True
    return False


def _agent_has_pending_weaver_followups(state: MatrixState,
                                        agent_id: str) -> bool:
    """Return whether the agent still owes the designated engineer a visible reply."""
    if not agent_id:
        return False
    if state.agent_pending_weaver_reply_tasks(agent_id):
        return True
    cell = state.agents.get(agent_id)
    return bool(cell and cell.pending_weaver_message)


async def _maybe_auto_close_root_done_agents(
        state: MatrixState,
        task,
        *,
        action_mgr: ActionManager,
        resolve_base_dir,
        close_agent,
) -> list[str]:
    """Auto-close agents whose completed actions opt in after root completion."""
    if not task:
        return []
    root_id = str(getattr(task, "pipeline_root_id", "") or task.id).strip()
    root_task = state.board_tasks.get(root_id) if root_id else None
    if not root_task:
        root_task = task
    if not task_counts_as_done(root_task):
        return []
    if state.task_has_unresolved_descendants(root_task.id):
        return []

    base_dir_cache: dict[str, str] = {}
    auto_close_cache: dict[tuple[str, str], bool] = {}
    candidates = []
    seen_agent_ids = set()

    for chain_task in state.board_get_chain(root_task.id):
        if not task_counts_as_done(chain_task):
            continue
        agent_id = str(getattr(chain_task, "agent_id", "") or "").strip()
        action_name = str(getattr(chain_task, "action_name", "") or "").strip()
        if not agent_id or not action_name or agent_id in seen_agent_ids:
            continue

        group = str(getattr(chain_task, "group", "") or root_task.group or "").strip()
        if group not in base_dir_cache:
            base_dir_cache[group] = await resolve_base_dir(group)
        base_dir = base_dir_cache[group]
        cache_key = (base_dir, action_name)
        if cache_key not in auto_close_cache:
            auto_close_cache[cache_key] = action_mgr.get_auto_close_on_done(
                action_name,
                base_dir=base_dir,
            )
        if not auto_close_cache[cache_key]:
            continue
        if _agent_has_open_assigned_tasks(state, agent_id):
            continue
        if _agent_has_targeted_auto_dispatch_work(state, agent_id):
            continue
        if _agent_has_pending_weaver_followups(state, agent_id):
            continue

        cell = state.agents.get(agent_id)
        if not cell or cell.cell_type != "agent":
            continue
        seen_agent_ids.add(agent_id)
        candidates.append(cell)

    closed = []
    for candidate in candidates:
        await close_agent(candidate)
        closed.append(candidate.id)
    return closed


def _append_mcp_message(cell, action: str, message: str = ""):
    """Append an MCP message to the cell log."""
    if not cell:
        return
    cell.mcp_messages.insert(0, {
        "action": action,
        "message": message,
        "timestamp": time.time(),
    })
    if len(cell.mcp_messages) > 20:
        cell.mcp_messages[:] = cell.mcp_messages[:20]


def _weaver_display_name(state: MatrixState, group: str) -> str:
    weaver_id = state.get_group_settings(group).weaver_agent_id or ""
    weaver = state.agents.get(weaver_id) if weaver_id else None
    name = (weaver.name if weaver else "").strip()
    return name or "Weaver"


def _summarize_weaver_message(message: str, *, limit: int = 72) -> str:
    lines = [
        line.strip() for line in str(message or "").splitlines()
        if line.strip()
    ]
    summary = lines[0] if lines else str(message or "").strip()
    if not summary:
        return "Weaver follow-up"
    if len(summary) <= limit:
        return summary
    return summary[:limit - 1].rstrip() + "…"


def _weaver_followup_task_title(message: str) -> str:
    return f"Weaver: {_summarize_weaver_message(message)}"


def _format_mcp_message_prompt(message: str, *,
                               sender_name: str = "Weaver",
                               sender_kind: str = "weaver",
                               task_id: str = "") -> str:
    # System-origin payloads (e.g. Loom digests) bring their own header
    # and trailing separator; wrapping them would double-up the chrome.
    if sender_kind == "system":
        return "\n" + message + "\n"
    prompt = (
        "\n"
        f"## Message from {sender_name}\n"
        f"{message}\n\n"
    )
    if task_id:
        prompt += f"Task: {task_id}\n"
    if task_id:
        prompt += (
            f'Reply with: loom_reply(task="{task_id}", '
            'message="your response")\n'
        )
    prompt += "---\n"
    return prompt


def _format_weaver_message_prompt(message: str, task_id: str) -> str:
    return _format_mcp_message_prompt(
        message,
        sender_name="Weaver",
        sender_kind="weaver",
        task_id=task_id,
    )


async def inject_mcp_message(state: MatrixState, bridge, target, message: str, *,
                             sender_name: str = "Loom",
                             sender_kind: str = "system",
                             action: str = "system",
                             task_id: str = "") -> None:
    if not target or not target.session_id:
        raise ValueError("Target agent is not running")
    if hasattr(bridge, "prime_input_ready"):
        bridge.prime_input_ready(target.session_id)
    await bridge.send_text(
        target.session_id,
        _format_mcp_message_prompt(
            message,
            sender_name=sender_name,
            sender_kind=sender_kind,
            task_id=task_id,
        ),
    )
    _append_mcp_message(target, action, message)
    state._emit_agent(target)


def _format_injected_mcp_message_prompt(
    *,
    message: str,
    sender_name: str,
    sender_kind: str,
    recipient_kind: str,
    message_id: str,
    recipient_anchor: str = "",
    ack_required: bool = False,
) -> str:
    sender_kind_key = str(sender_kind or "").strip()
    recipient_kind_key = str(recipient_kind or "").strip()
    sender_label = sender_name or sender_kind_key or "peer"
    if sender_kind_key and sender_name:
        header = f"Message from {sender_name} ({sender_kind_key})"
    else:
        header = f"Message from {sender_label}"
    reply_tool = (
        f"mcp__loom__{recipient_kind_key}_reply"
        if recipient_kind_key in {"architect", "engineer"}
        else "mcp__loom__loom_reply"
    )
    blocks = []
    anchor = str(recipient_anchor or "").strip()
    if anchor:
        blocks.append(anchor)
    blocks.append(f"## {header}")
    body = str(message or "").strip("\n")
    if anchor and (body == anchor or body.startswith(anchor + "\n")):
        body = body[len(anchor):].lstrip("\n")
    if body:
        blocks.append(body)
    include_reply_hint = True
    if sender_kind_key == "engineer" and recipient_kind_key == "architect":
        include_reply_hint = bool(ack_required)
    if include_reply_hint:
        blocks.append(
            f'Reply with: {reply_tool}(message_id="{message_id}", '
            'message="your response")'
        )
    prefix = "" if anchor else "\n"
    return prefix + "\n\n".join(blocks) + "\n---\n"


def _mark_cross_kind_message_delivery(cell, message_id: str, *,
                                      delivered: bool,
                                      reason: str = "") -> None:
    message_id = str(message_id or "").strip()
    if not cell or not message_id:
        return
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if str((entry or {}).get("id", "") or "").strip() != message_id:
            continue
        entry["delivered"] = bool(delivered)
        entry["buffered"] = not bool(delivered)
        if reason:
            entry["delivery_reason"] = str(reason or "").strip()
        else:
            entry.pop("delivery_reason", None)
        return


async def _replay_buffered_cross_kind_messages(
        state: MatrixState,
        bridge,
        target) -> int:
    """Replay architect/engineer inbox entries that buffered while dismissed."""
    if not target or not getattr(target, "session_id", ""):
        return 0
    replayed = 0
    entries = list(getattr(target, "mcp_messages", []) or [])
    for entry in reversed(entries):
        if str((entry or {}).get("direction", "") or "") != "received":
            continue
        if entry.get("delivered") is not False:
            continue
        message_id = str(entry.get("id", "") or "").strip()
        message_text = str(entry.get("message", "") or "")
        if not message_id or not message_text:
            continue
        sender_id = str(entry.get("sender_id", "") or "").strip()
        sender = state.agents.get(sender_id)
        sender_name = (
            str(getattr(sender, "name", "") or "").strip()
            or str(entry.get("sender_kind", "") or "").strip()
            or "peer"
        )
        sender_kind = (
            str(getattr(sender, "kind", "") or "").strip()
            or str(entry.get("sender_kind", "") or "").strip()
        )
        recipient_anchor = ""
        if (
            str(getattr(target, "kind", "") or "").strip() == "engineer"
            and sender_kind == "architect"
        ):
            recipient_anchor = agent_identity_anchor(target)
        formatted = _format_injected_mcp_message_prompt(
            message=message_text,
            sender_name=sender_name,
            sender_kind=sender_kind,
            recipient_kind=str(getattr(target, "kind", "") or ""),
            message_id=message_id,
            recipient_anchor=recipient_anchor,
            ack_required=bool(entry.get("ack_required", False)),
        )
        try:
            if hasattr(bridge, "prime_input_ready"):
                bridge.prime_input_ready(target.session_id)
            await bridge.send_text(target.session_id, formatted)
        except Exception:
            log.exception(
                "Failed to replay buffered MCP message %s to %s",
                message_id,
                target.id,
            )
            _mark_cross_kind_message_delivery(
                target,
                message_id,
                delivered=False,
                reason="replay_failed",
            )
            continue
        _mark_cross_kind_message_delivery(target, message_id, delivered=True)
        replayed += 1
    if replayed:
        state._emit_agent(target)
    return replayed


def _inherit_assigned_engineer_for_derived_task(parent_task,
                                                derived_task=None) -> str:
    """Keep derived-task ownership bound to the parent's assigned engineer."""
    assigned_engineer_id = str(
        getattr(parent_task, "assigned_engineer_id", "") or ""
    ).strip()
    if derived_task is not None:
        derived_task.assigned_engineer_id = assigned_engineer_id
    return assigned_engineer_id


def _emit_task_artifact_uploaded_event(panel_event, task, actor, artifact) -> None:
    if not panel_event or not task or not artifact:
        return
    agent_name = ""
    cell_id = ""
    if actor:
        agent_name = str(getattr(actor, "name", "") or "").strip()
        cell_id = str(getattr(actor, "id", "") or "").strip()
    panel_event(
        "task_artifact_uploaded",
        cell_id,
        agent_name,
        task.group,
        describe_task_artifact_for_digest(
            artifact,
            task_id=task.id,
            task_label=task.task,
        ),
        task_id=task.id,
    )


def _create_weaver_followup_task(state: MatrixState, target, message: str
                                 ) -> Optional[BoardTask]:
    if not target or not target.group:
        return None
    active_task = state.agent_current_task(target.id)
    labels = ["loom:weaver-message"]
    kwargs = {
        "description": message,
        "status": "Awaiting Reply",
        "labels": labels,
        "reply_agent_id": target.id,
    }
    task_group = target.group
    if active_task:
        labels.insert(0, "loom:derived")
        task_group = active_task.group or target.group
        kwargs.update({
            "parent_task_id": active_task.id,
            "pipeline_depth": active_task.pipeline_depth + 1,
            "pipeline_root_id": active_task.pipeline_root_id or active_task.id,
        })
    return state.board_add_task(
        task=_weaver_followup_task_title(message),
        group=task_group,
        lane="Backlog",
        **kwargs,
    )


def _resolve_pending_weaver_reply_task(state: MatrixState, cell, *,
                                       task_id: str = ""
                                       ) -> tuple[Optional[BoardTask],
                                                  list[BoardTask], str]:
    pending = state.agent_pending_weaver_reply_tasks(cell.id) if cell else []
    if not cell:
        return None, pending, "Cell not found"
    explicit = _resolve_task_id(state, task_id) if task_id else ""
    if explicit:
        task = state.board_tasks.get(explicit)
        if not task:
            return None, pending, f"Task not found: {task_id}"
        if task.reply_agent_id != cell.id:
            return None, pending, (
                f"Task {task.id} is not awaiting a reply from this agent"
            )
        if task_is_closed(task):
            return None, pending, f"Task {task.id} is already closed"
        return task, pending, ""
    if len(pending) == 1:
        return pending[0], pending, ""
    if not pending:
        return None, pending, "No pending weaver message to reply to"
    ids = ", ".join(task.id for task in pending[:5])
    if len(pending) > 5:
        ids += ", …"
    return None, pending, (
        "Multiple pending weaver messages; reply with task=<id>. "
        f"Open reply tasks: {ids}"
    )


async def _send_weaver_message_to_agent(state: MatrixState, bridge, target,
                                        message: str, panel_event) -> dict:
    if not target or not target.session_id:
        return {"type": "error", "message": "Agent is not running"}
    follow_up = _create_weaver_followup_task(state, target, message)
    if not follow_up:
        return {
            "type": "error",
            "message": "Failed to create Weaver follow-up task",
        }
    try:
        if hasattr(bridge, "prime_input_ready"):
            bridge.prime_input_ready(target.session_id)
        await bridge.send_text(
            target.session_id,
            _format_weaver_message_prompt(message, follow_up.id),
        )
    except Exception as exc:
        log.exception("Failed to send Weaver message to agent %s", target.id)
        state.board_remove_task(follow_up.id)
        return {
            "type": "error",
            "message": f"Failed to send message: {exc}",
        }

    follow_up.messages.append({
        "timestamp": time.time(),
        "action": "weaver_message",
        "message": message,
        "agent_name": _weaver_display_name(state, target.group),
    })
    state.board_update_task(
        follow_up.id,
        messages=list(follow_up.messages),
    )
    group_settings = state.get_group_settings(target.group)
    state.history_record_dispatch(
        target,
        follow_up,
        weaver_group=target.group,
        weaver_id=group_settings.weaver_agent_id if group_settings else "",
    )
    state.history_record_message(
        target.id,
        "weaver_message",
        message,
        task_id=follow_up.id,
    )
    target.pending_weaver_message = True
    state._emit_agent(target)
    panel_event(
        "weaver_message",
        target.id,
        target.name,
        target.group,
        message[:200],
        task_id=follow_up.id,
    )
    return {"type": "ok", "task_id": follow_up.id}


def _handle_weaver_reply(state: MatrixState, cell, *, message: str,
                         task_id: str = "", panel_event=None) -> dict:
    if not message:
        return {"type": "error", "message": "Reply message is required"}
    reply_task, pending, error = _resolve_pending_weaver_reply_task(
        state,
        cell,
        task_id=task_id,
    )
    if error:
        if not pending:
            cell.pending_weaver_message = False
            state._emit_agent(cell)
        return {"type": "error", "message": error}

    _append_mcp_message(cell, "reply", message)
    reply_task.messages.append({
        "timestamp": time.time(),
        "action": "reply",
        "message": message,
        "agent_name": cell.name,
    })
    state.board_update_task(
        reply_task.id,
        messages=list(reply_task.messages),
        status="",
    )
    state.history_record_message(
        cell.id,
        "reply",
        message,
        task_id=reply_task.id,
    )
    state.history_complete_task(cell.id, reply_task.id, "answered")
    if not task_counts_as_done(reply_task):
        state.board_move_task(reply_task.id, "Done")
    cell.pending_weaver_message = bool(
        state.agent_pending_weaver_reply_tasks(cell.id)
    )
    state._emit_agent(cell)
    if panel_event:
        panel_event(
            "agent_reply",
            cell.id,
            cell.name,
            cell.group,
            message[:200],
            task_id=reply_task.id,
        )
    return {"type": "ok", "task_id": reply_task.id}


def _handle_weaver_flush_now_command(weaver_buffer, data: dict) -> dict:
    recipient_or_group = data.get("agent_id", "") or data.get("group", "")
    ok, message = weaver_buffer.request_manual_flush(recipient_or_group)
    if ok:
        return {"type": "ok"}
    return {"type": "error", "message": message or "Unable to send queued events"}


def _handle_digest_pause_resume_command(
    state: MatrixState,
    weaver_buffer,
    data: dict,
    *,
    paused: bool,
) -> dict:
    agent_ident = data.get("agent_id", "")
    agent_id = _resolve_agent_id(state, agent_ident)
    if not agent_id:
        return {
            "type": "error",
            "message": f"Agent not found: {agent_ident}",
        }
    state.update_agent_digest_settings(agent_id, paused=paused)
    if paused:
        weaver_buffer.on_delivery_paused(agent_id)
    else:
        weaver_buffer.on_delivery_resumed(agent_id)
    return {
        "type": "ok",
        "agent_id": agent_id,
        "paused": paused,
    }


def _is_closing_ui_ws_error(exc: Exception) -> bool:
    client_reset = getattr(
        getattr(aiohttp, "client_exceptions", None),
        "ClientConnectionResetError",
        None,
    )
    if client_reset and isinstance(exc, client_reset):
        return True
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return True
    text = str(exc or "").lower()
    return "closing transport" in text or "write eof" in text


async def _send_ui_ws_json(ws, payload: dict) -> bool:
    if not ws or getattr(ws, "closed", False):
        return False
    try:
        await ws.send_str(await hot_json_dumps_async(payload))
        return True
    except Exception as exc:
        if _is_closing_ui_ws_error(exc):
            return False
        raise


async def _hot_json_response(
    payload: dict, *, status: int = 200
) -> web.Response:
    body = await hot_json_dumps_bytes_async(payload)
    return web.Response(
        body=body, status=status, content_type="application/json")


async def _register_ready_ui_ws_client(state: MatrixState, ws,
                                       payload_factory) -> bool:
    connect_started = time.perf_counter()
    async with state._ws_clients_lock:
        state._ws_clients.discard(ws)
    while True:
        payload = payload_factory()
        if asyncio.iscoroutine(payload):
            payload = await payload
        if profiling.is_enabled() and payload.get("type") == "state":
            payload_bytes = len(json.dumps(payload).encode("utf-8"))
            profiling.recorder().observe("snapshot_json_bytes", payload_bytes)
        if not await _send_ui_ws_json(ws, payload):
            return False
        async with state._ws_clients_lock:
            if state._seq == int(payload.get("seq", 0) or 0):
                state._ws_clients.add(ws)
                profiling.recorder().incr("ws_connects")
                profiling.recorder().observe_ms(
                    "ws_connect_latency_ms",
                    time.perf_counter() - connect_started,
                )
                return True


async def _queue_cell_prompt_send(cell, prompt: str, send_prompt, *,
                                  prime_input_ready: bool = False,
                                  settled_submit: bool = False,
                                  wait_for_delivery: bool = False) -> bool:
    """Queue prompt delivery for a live cell without blocking fast controls."""
    if not cell or not getattr(cell, "session_id", ""):
        return False
    delivery = await send_prompt(
        cell,
        prompt,
        background=True,
        prime_input_ready=prime_input_ready,
        settled_submit=settled_submit,
    )
    if wait_for_delivery and delivery is not None:
        await delivery
    return True


async def _handle_send_user_message_command(data, state: MatrixState,
                                            bridge) -> bool:
    cell_id = str(data.get("cell_id") or data.get("id") or "").strip()
    text = str(data.get("text") or "")
    if not cell_id or not text.strip():
        return False
    cell = state.agents.get(cell_id)
    if not cell or not getattr(cell, "session_id", ""):
        return False
    cell.status = "running"
    state.mark_agent_progress(cell, emit=False)
    state._emit_agent(cell)
    await bridge.send_text(cell.session_id, text)
    return True


async def _deliver_weaver_reply_and_resume(state: MatrixState, weaver, *,
                                           group: str,
                                           answer: str,
                                           send_prompt,
                                           weaver_buffer) -> dict:
    formatted = (
        "\n"
        "## Human Reply\n"
        f"{answer}\n"
        "---\n"
    )
    await _queue_cell_prompt_send(
        weaver,
        formatted,
        send_prompt,
        prime_input_ready=True,
        wait_for_delivery=True,
    )
    state.update_weaver_settings(
        group,
        pending_question="",
        paused=False,
    )
    weaver_buffer.on_delivery_resumed(group)
    state.journal_append(
        group,
        "observation",
        f"Human replied: {answer}",
    )
    return {"type": "ok"}


def _resolve_ai_report_task(state: MatrixState, cell, *,
                            task_id: str = "") -> Optional[BoardTask]:
    """Resolve the task an agent report should apply to.

    Prefer an explicit task id. Otherwise ignore stale ``current_task_id``
    pointers that no longer occupy the agent's live execution slot, and fall
    back to the state-derived active task before using the older linked-task
    heuristic.
    """
    if not cell:
        return None
    if task_id:
        return state.board_tasks.get(_resolve_task_id(state, task_id))
    if cell.current_task_id:
        current = state.board_tasks.get(cell.current_task_id)
        if state.task_occupies_execution_slot(current, agent_id=cell.id):
            return current
    current = state.agent_current_task(cell.id)
    if current:
        return current
    linked = [
        t for t in state.board_tasks.values()
        if t.agent_id == cell.id
        and t.lane not in ("Done", "Backlog", ARCHIVED_LANE)
    ]
    if len(linked) == 1:
        return linked[0]
    return None


def _handle_board_archive_command(state: MatrixState, data: dict) -> dict | None:
    """Archive one board task.

    Batch UI actions expand descendants on the client before sending commands,
    so the state-layer archive API intentionally remains single-task here.
    """
    tid = _resolve_task_id(state, data.get("id", ""))
    if tid not in state.board_tasks:
        return {"type": "error", "message": "Task not found"}
    state.board_archive_task(
        tid,
        position=data.get("position"),
    )
    return None


def _handle_board_unarchive_command(state: MatrixState, data: dict) -> dict | None:
    """Unarchive one board task.

    Descendant restoration is expanded by the board client in the same way as
    archive operations.
    """
    tid = _resolve_task_id(state, data.get("id", ""))
    if tid not in state.board_tasks:
        return {"type": "error", "message": "Task not found"}
    state.board_unarchive_task(
        tid,
        lane=data.get("lane", ""),
        position=data.get("position"),
    )
    return None


def _handle_doctor_command(db: LoomDB) -> dict:
    return build_doctor_report(db._conn, db.db_path)


async def replay_api_failed_write_payload(
    db: LoomDB,
    payload: dict,
    handle_command,
):
    """Replay one queued /api/cmd write with live API idempotency semantics."""
    payload = dict(payload or {})
    cmd = str(payload.get("cmd", "") or "")
    idempotency_key = str(payload.get("idempotency_key", "") or "").strip()
    request_hash = ""
    if idempotency_key and is_api_write_command(cmd):
        request_hash = api_request_hash(payload)
        existing = db.load_mcp_idempotency(idempotency_key)
        if existing:
            existing_hash = str(existing.get("request_hash", "") or "")
            if existing_hash and existing_hash != request_hash:
                db.record_mcp_health_event(
                    surface="api",
                    tool_name=cmd,
                    event="idempotency_conflict",
                )
                return {
                    "ok": False,
                    "error": (
                        "idempotency key was reused for a different "
                        f"API command ({cmd})"
                    ),
                }
            try:
                cached = json.loads(existing.get("response_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                cached = {}
            db.record_mcp_health_event(
                surface="api",
                tool_name=cmd,
                event="dedupe",
            )
            return cached

    result = await handle_command(payload)
    if idempotency_key and is_api_write_command(cmd):
        db.save_mcp_idempotency(
            idempotency_key=idempotency_key,
            surface="api",
            tool_name=cmd,
            request_hash=request_hash or api_request_hash(payload),
            response={"ok": True, "data": result if result else {}},
        )
    return result


async def _handle_role_template_command(data: dict, role_mgr,
                                        resolve_base_dir) -> dict | None:
    cmd = data.get("cmd", "")
    response_type = ""
    item_key = ""
    item_name = ""

    if cmd == "list_roles":
        response_type = "roles"
        item_key = "roles"
    elif cmd == "list_templates":
        response_type = "templates"
        item_key = "templates"
    elif cmd == "save_role":
        response_type = "roles"
        item_key = "roles"
        item_name = "Role"
    elif cmd == "save_template":
        response_type = "templates"
        item_key = "templates"
        item_name = "Template"
    elif cmd == "delete_role":
        response_type = "roles"
        item_key = "roles"
        item_name = "Role"
    elif cmd == "delete_template":
        response_type = "templates"
        item_key = "templates"
        item_name = "Template"
    else:
        return None

    base_dir = await resolve_base_dir(data.get("group", ""))
    group = data.get("group", "")

    if cmd in {"list_roles", "list_templates"}:
        return {
            "type": response_type,
            "group": group,
            item_key: role_mgr.list_roles(base_dir),
        }

    name = data.get("name", "").strip()
    if not name:
        return {"type": "error", "message": f"{item_name} name required"}

    if cmd in {"save_role", "save_template"}:
        scope = data.get("scope", "project")
        old_name = data.get("old_name", "").strip()
        payload = data.get("data")
        if payload is None:
            payload = data.get("role")
        if payload is None:
            payload = data.get("template", {})
        if old_name and old_name != name:
            role_mgr.delete_template(old_name, base_dir=base_dir)
            role_mgr.delete_template(old_name, scope="user",
                                     base_dir=base_dir)
        role_mgr.save_role(
            name, payload, scope=scope, base_dir=base_dir)
        return {
            "type": response_type,
            "group": group,
            item_key: role_mgr.list_roles(base_dir),
            "saved": name,
        }

    delete_fn = role_mgr.delete_role if cmd == "delete_role" else (
        role_mgr.delete_template
    )
    deleted = delete_fn(
        name, scope=data.get("scope", ""), base_dir=base_dir)
    if not deleted:
        return {"type": "error", "message": f"{item_name} \"{name}\" not found"}
    return {
        "type": response_type,
        "group": group,
        item_key: role_mgr.list_roles(base_dir),
        "deleted": name,
    }


def _agent_kind_for_context(cell) -> str:
    return agent_kind_for_identity(cell)


def _agent_is_worker_for_role_preamble(cell) -> bool:
    if not cell or getattr(cell, "cell_type", "agent") != "agent":
        return False
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "worker":
        return True
    if kind in {"engineer", "architect", "terminal"}:
        return False
    return bool(str(getattr(cell, "created_by_weaver_id", "") or "").strip())


def _agent_role_slug(cell) -> str:
    return str(
        getattr(cell, "role", "")
        or getattr(cell, "template", "")
        or ""
    ).strip()


def _agent_owner_engineer_name(state: MatrixState, cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if not owner_id:
        owner_id = str(getattr(cell, "created_by_weaver_id", "") or "").strip()
    if not owner_id:
        return ""
    owner = state.agents.get(owner_id)
    return owner.name if owner else ""


def _normalize_prompt_block(text: str) -> str:
    return str(text or "").strip("\n")


def _assemble_worker_prompt(*, role_mgr, cell, base_dir: str = "",
                            prompt_body: str = "", postscript: str = "",
                            disable_role_preamble: bool = False) -> str:
    """Assemble the final worker prompt with optional role preamble.

    The final shape is:
    {identity anchor block}

    {role preamble block}

    {task/action prompt block}

    {loom postscript}

    Empty blocks are omitted. Exactly one blank line is inserted between
    included blocks, and the final prompt always ends with a trailing newline.
    """
    blocks = []

    identity_anchor = agent_identity_anchor(cell)
    if identity_anchor:
        blocks.append(identity_anchor)

    if role_mgr and cell and not disable_role_preamble \
            and _agent_is_worker_for_role_preamble(cell):
        role_slug = _agent_role_slug(cell)
        if role_slug:
            role = role_mgr.load_role(role_slug, base_dir=base_dir) or {}
            preamble = role_mgr.render_preamble(role)
            if preamble:
                blocks.append(_normalize_prompt_block(preamble))

    body_block = _normalize_prompt_block(prompt_body)
    if body_block:
        blocks.append(body_block)

    postscript_block = _normalize_prompt_block(postscript)
    if postscript_block:
        blocks.append(postscript_block)

    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def _build_loom_context(state: MatrixState, cell, task) -> dict:
    """Build the ``loom`` namespace dict for Jinja2 template rendering."""
    workerish = _agent_is_worker_for_role_preamble(cell)
    cell_id = getattr(cell, "id", "")
    tasks_dispatched = int(getattr(cell, "tasks_dispatched", 0) or 0)
    worktree_path = getattr(cell, "worktree_path", "") or ""
    worktree_branch = getattr(cell, "worktree_branch", "") or ""
    worktree_base_branch = getattr(cell, "worktree_base_branch", "") or ""
    worktree_dirty = bool(getattr(cell, "worktree_dirty", False))
    worktree_diff = getattr(cell, "worktree_diff", {}) or {}
    worktree_checkpoints = int(
        getattr(cell, "worktree_checkpoints", 0) or 0
    )
    agent_ctx = {
        "id": cell_id,
        "name": getattr(cell, "name", ""),
        "slug": getattr(cell, "slug", ""),
        "type": getattr(cell, "agent_type", ""),
        "group": getattr(cell, "group", ""),
        "directory": getattr(cell, "directory", ""),
        "kind": _agent_kind_for_context(cell),
        "role": _agent_role_slug(cell) if workerish else "",
        "owner_engineer": _agent_owner_engineer_name(
            state, cell) if workerish else "",
    }

    linked = sorted(
        (t for t in state.board_tasks.values()
         if t.agent_id == cell_id and t.id != getattr(task, "id", "")),
        key=lambda t: t.created_at,
    )
    context_ctx = {
        "is_clean": tasks_dispatched == 0,
        "tasks_dispatched": tasks_dispatched,
        "previous_tasks": [
            {"task": t.task, "lane": t.lane, "action": t.action_name}
            for t in linked
        ],
    }

    worktree_ctx = {
        "active": bool(worktree_path),
        "path": worktree_path,
        "branch": worktree_branch,
        "base_branch": worktree_base_branch,
        "dirty": worktree_dirty,
        "diff": worktree_diff,
        "checkpoints": worktree_checkpoints,
    }

    parent_agent_slug = ""
    parent_agent_name = ""
    parent_agent_id = ""
    parent_task_id = getattr(task, "parent_task_id", "")
    if parent_task_id:
        pt = state.board_tasks.get(parent_task_id)
        if pt and pt.agent_id:
            pa = state.agents.get(pt.agent_id)
            if pa:
                parent_agent_id = pa.id
                parent_agent_name = pa.name
                parent_agent_slug = pa.slug or pa.name

    attachments = list(getattr(task, "attachments", []) or [])
    artifacts = list(getattr(task, "artifacts", []) or [])
    task_ctx = {
        "id": getattr(task, "id", ""),
        "title": getattr(task, "task", ""),
        "slug": getattr(task, "slug", ""),
        "description": getattr(task, "description", ""),
        "depth": getattr(task, "pipeline_depth", 0),
        "is_derived": bool(parent_task_id),
        "parent_task_id": parent_task_id,
        "parent_agent_id": parent_agent_id,
        "parent_agent_name": parent_agent_name,
        "parent_agent_slug": parent_agent_slug,
        "labels": list(getattr(task, "labels", []) or []),
        "group": getattr(task, "group", ""),
        "status": getattr(task, "status", ""),
        "verification_mode": getattr(task, "verification_mode", ""),
        "verification_state": getattr(task, "verification_state", ""),
        "verification_notes": getattr(task, "verification_notes", ""),
        "verification_updated_at": getattr(
            task, "verification_updated_at", ""),
        "verification_updated_by": getattr(
            task, "verification_updated_by", ""),
        "verification_summary": getattr(task, "verification_summary", {}) or {},
        "worktree_boundary": getattr(task, "worktree_boundary", {}) or {},
        "resume_after_boundary_task_id": getattr(
            task, "resume_after_boundary_task_id", "") or "",
        "attachments": [
            {"path": a.get("path", ""), "filename": a.get("filename", "")}
            for a in attachments
            if isinstance(a, dict)
        ],
        "artifacts": task_artifacts(attachments, artifacts),
        "upstream_artifacts": serialize_upstream_task_artifacts(
            task,
            tasks_by_id=state.board_tasks,
        ),
    }

    terminals_ctx = []
    for cid in state._children.get(cell_id, []):
        ch = state.agents.get(cid)
        if ch:
            terminals_ctx.append({
                "name": getattr(ch, "name", ""),
                "slug": getattr(ch, "slug", ""),
                "current_path": getattr(ch, "current_path", ""),
                "current_process": getattr(ch, "current_process", ""),
                "current_branch": getattr(ch, "current_branch", ""),
            })

    return {
        "agent": agent_ctx,
        "context": context_ctx,
        "worktree": worktree_ctx,
        "task": task_ctx,
        "terminals": terminals_ctx,
    }


def _resolve_memory_cell_and_task(state, cell_id: str = "",
                                  task_id: str = ""):
    """Resolve best-effort agent/task context for memory commands."""
    resolved_task = None
    resolved_cell = None

    task_id = str(task_id or "").strip()
    if task_id:
        resolved_task = state.board_tasks.get(state.resolve_task_alias(task_id))

    cell_id = str(cell_id or "").strip()
    if cell_id:
        resolved_cell = state.agents.get(cell_id)
        if resolved_task is None:
            resolved_task = detect_current_task(
                state,
                cell_id,
                explicit_task_id=task_id,
            )

    if resolved_cell is None and resolved_task and getattr(
        resolved_task, "agent_id", ""
    ):
        resolved_cell = state.agents.get(resolved_task.agent_id)

    return resolved_cell, resolved_task


def _resolve_memory_scope_ref(scope_kind: str, scope_ref: str = "",
                              *, cell=None, task=None) -> str:
    """Resolve memory scope refs from explicit values or active context."""
    kind = str(scope_kind or "").strip()
    ref = str(scope_ref or "").strip()
    if ref or not kind:
        return ref

    if kind == "task":
        if task and getattr(task, "id", ""):
            return task.id
        raise ValueError("Task scope requires an active task or scope_ref")

    if kind == "pipeline":
        if task:
            return getattr(task, "pipeline_root_id", "") or task.id
        raise ValueError("Pipeline scope requires an active task or scope_ref")

    if kind == "group":
        group_name = ""
        if task:
            group_name = getattr(task, "group", "") or ""
        if not group_name and cell:
            group_name = getattr(cell, "group", "") or ""
        if group_name:
            return group_name
        raise ValueError("Group scope requires a group or scope_ref")

    if kind == "project":
        project_key = infer_project_key(cell=cell, task=task)
        if project_key:
            return project_key
        raise ValueError(
            "Project scope requires a repo/worktree context or explicit scope_ref"
        )

    return ref


def _resolve_memory_link_ref(target_kind: str, target_ref: str = "",
                             *, cell=None, task=None) -> str:
    """Resolve memory-link target refs from explicit values or active context."""
    kind = str(target_kind or "").strip()
    ref = str(target_ref or "").strip()
    if ref:
        return ref

    if kind == "task":
        if task and getattr(task, "id", ""):
            return task.id
        raise ValueError("Task link requires an active task or target_ref")

    if kind == "agent":
        if cell and getattr(cell, "id", ""):
            return cell.id
        raise ValueError("Agent link requires an active agent or target_ref")

    if kind == "pipeline":
        if task:
            return getattr(task, "pipeline_root_id", "") or task.id
        raise ValueError("Pipeline link requires an active task or target_ref")

    return ref


def _apply_verification_report(task, payload, actor_name, save_task,
                               *, root_task=None, timestamp=None):
    """Apply a verification checkpoint update to a task and optional root."""
    if not task:
        return "", None

    from datetime import datetime, timezone

    summary = dict(task.verification_summary or {})
    if "tests_run" in payload:
        tests_run = str(payload.get("tests_run", "") or "").strip()
        if tests_run:
            summary["tests_run"] = tests_run
        else:
            summary.pop("tests_run", None)
    if "manual_smoke_done" in payload:
        summary["manual_smoke_done"] = bool(
            payload.get("manual_smoke_done")
        )
    smoke_status = str(payload.get("smoke_status", "") or "").strip()
    if smoke_status in {"passed", "failed"}:
        summary["manual_smoke_done"] = True
    if "deploy_needed" in payload:
        summary["deploy_needed"] = bool(
            payload.get("deploy_needed")
        )
    if "deploy_attempted" in payload:
        summary["deploy_attempted"] = bool(
            payload.get("deploy_attempted")
        )
    if "human_validation_pending" in payload:
        human_pending = str(
            payload.get("human_validation_pending", "") or ""
        ).strip()
        if human_pending:
            summary["human_validation_pending"] = human_pending
        else:
            summary.pop("human_validation_pending", None)

    if "verification_mode" in payload:
        mode = str(payload.get("verification_mode", "") or "").strip()
        task.verification_mode = (
            mode if mode in {"", "deploy", "restart"} else ""
        )

    verification_state = None
    if "verification_state" in payload:
        verify_state = str(
            payload.get("verification_state", "") or ""
        ).strip()
        verification_state = (
            verify_state if verify_state in {
                "", "pending", "attempted", "passed", "failed"
            } else ""
        )
    elif smoke_status in {"passed", "failed"}:
        verification_state = smoke_status
    elif "deploy_attempted" in payload and payload.get("deploy_attempted"):
        verification_state = "attempted"
    if verification_state is not None:
        task.verification_state = verification_state
        if verification_state == "passed":
            summary.pop("human_validation_pending", None)
            summary.pop("deploy_needed", None)

    if "verification_notes" in payload:
        task.verification_notes = str(
            payload.get("verification_notes", "") or ""
        ).strip()

    task.verification_summary = summary
    task.verification_updated_at = (
        timestamp
        or datetime.now(timezone.utc).isoformat()
    )
    task.verification_updated_by = actor_name

    parts = []
    if task.verification_state:
        parts.append(f"state={task.verification_state}")
    if task.verification_mode:
        parts.append(f"mode={task.verification_mode}")
    if summary.get("tests_run"):
        parts.append(f"tests={summary['tests_run']}")
    if summary.get("manual_smoke_done"):
        parts.append("manual smoke done")
    if summary.get("deploy_needed"):
        parts.append("deploy needed")
    if summary.get("deploy_attempted"):
        parts.append("deploy attempted")
    if summary.get("human_validation_pending"):
        parts.append(
            "human validation="
            + summary["human_validation_pending"]
        )
    if task.verification_notes:
        parts.append(f"notes={task.verification_notes}")

    msg = "Verification updated"
    if parts:
        msg += ": " + "; ".join(parts)

    save_task(task)

    if root_task:
        root_task.verification_mode = task.verification_mode
        root_task.verification_state = task.verification_state
        root_task.verification_notes = task.verification_notes
        root_task.verification_updated_at = task.verification_updated_at
        root_task.verification_updated_by = task.verification_updated_by
        root_task.verification_summary = dict(summary)
        save_task(root_task)

    return msg, root_task


async def _relaunch_agent_after_worktree_removal(
        cell, *,
        bridge,
        state,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_weaver_launch_config=None,
        is_designated_weaver=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt):
    """Reset an agent session after its worktree is removed."""
    if cell.cell_type != "agent":
        return
    if cell.session_id:
        await bridge.close_session(cell.session_id)
    cell.status = "stopped"
    cell.session_id = None
    cell.agent_session_id = ""
    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    use_weaver_launch = (
        str(getattr(cell, "kind", "") or "").strip() in ("engineer", "architect")
        or bool(is_designated_weaver and is_designated_weaver(cell))
    )
    resolver = (
        resolve_weaver_launch_config
        if use_weaver_launch and resolve_weaver_launch_config
        else resolve_agent_launch_config
    )
    launch_cfg = resolver(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    apply_persistent_prompt(
        cell, launch_cfg,
        build_cell_persistent_prompt(cell, launch_cfg))
    state._emit_agent(cell)
    state._db_save_agent(cell)
    await bridge.create_session(
        cell,
        env_vars=runtime_env_vars_for_cell(cell, launch_cfg.get("env_vars")),
        env_file=launch_cfg.get("env_file", ""),
        shell=launch_cfg.get("shell", ""),
        system_prompt=launch_cfg.get("system_prompt", ""),
        mcp_entrypoint=mcp_entrypoint_for_cell(cell),
    )


def _resolve_engineer_group(state: MatrixState) -> str:
    """Return the reserved engineer group, preferring the designated engineer."""
    for group_name, group_settings in state.group_settings.items():
        engineer_id = str(getattr(group_settings, "weaver_agent_id", "") or "")
        cell = state.agents.get(engineer_id)
        if cell and cell.cell_type == "agent" \
                and str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return str(cell.group or group_name or "loom")
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return str(cell.group or "loom")
    return "loom"


def _resolve_engineer_cell(state: MatrixState, *, engineer_id: str = "",
                           engineer_slug: str = ""):
    """Resolve an engineer agent by exact id or slug."""
    engineer_id = str(engineer_id or "").strip()
    engineer_slug = str(engineer_slug or "").strip().lower()
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            continue
        if engineer_id and cell.id == engineer_id:
            return cell
        if engineer_slug and str(getattr(cell, "slug", "") or "").strip().lower() \
                == engineer_slug:
            return cell
    return None


def _agent_dismissed_at(cell) -> int:
    try:
        return int(getattr(cell, "dismissed_at", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _engineer_dismissed_error(engineer_id: str) -> dict:
    return {
        "type": "error",
        "reason": "engineer_dismissed",
        "message": f"engineer {engineer_id} is dismissed",
        "engineer_id": str(engineer_id or "").strip(),
    }


def _validate_engineer_lifecycle_authority(
        state: MatrixState,
        engineer,
        *,
        architect_id: str = "") -> dict | None:
    """Return an error if an architect-scoped lifecycle command is unauthorized."""
    architect_id = str(architect_id or "").strip()
    if not architect_id:
        return None
    architect = _resolve_architect_cell(state, architect_id=architect_id)
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    hired_by = str(getattr(engineer, "hired_by_architect_id", "") or "").strip()
    if hired_by != architect.id:
        return {"type": "error", "message": "engineer not found in scope"}
    return None


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_weaver_id", "") or "").strip()


def _dismissal_close_cells(state: MatrixState, engineer) -> list:
    """Return the engineer, owned workers, and child terminals to close."""
    roots = []
    seen: set[str] = set()

    def add_root(cell) -> None:
        if not cell or cell.id in seen:
            return
        seen.add(cell.id)
        roots.append(cell)

    add_root(engineer)
    for cell in list(state.agents.values()):
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "worker":
            continue
        if _effective_owner_engineer_id(cell) == engineer.id:
            add_root(cell)

    ordered = []
    ordered_seen: set[str] = set()

    def add_with_children(cell) -> None:
        if not cell or cell.id in ordered_seen:
            return
        ordered_seen.add(cell.id)
        ordered.append(cell)
        for child_id in list(getattr(state, "_children", {}).get(cell.id, [])):
            add_with_children(state.agents.get(child_id))

    for root in roots:
        add_with_children(root)
    return ordered


async def _close_cell_session_preserving_state(
        state: MatrixState,
        cell,
        close_session,
        *,
        errors: list[str] | None = None) -> bool:
    """Close a cell's terminal session while preserving its agent row/history."""
    if not cell:
        return False
    had_session = bool(getattr(cell, "session_id", "") or "")
    if had_session:
        try:
            await close_session(cell.session_id)
        except Exception as exc:
            if errors is not None:
                errors.append(f"Failed to close session for '{cell.name}': {exc}")
            log.exception("Failed to close session for '%s'", cell.name)
    cell.status = "stopped"
    cell.session_id = None
    cell.current_process = ""
    cell.current_path = ""
    cell.current_branch = ""
    cell.git_root = ""
    cell.activity = ""
    cell.activity_detail = ""
    cell.error_message = ""
    cell.needs_attention = False
    state._emit_agent(cell)
    state._db_save_agent(cell)
    return had_session


def _resolve_architect_cell(state: MatrixState, *, architect_id: str = "",
                            architect_slug: str = ""):
    """Resolve an architect agent by exact id or slug."""
    architect_id = str(architect_id or "").strip()
    architect_slug = str(architect_slug or "").strip().lower()
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "architect":
            continue
        if architect_id and cell.id == architect_id:
            return cell
        if architect_slug and str(getattr(cell, "slug", "") or "").strip().lower() \
                == architect_slug:
            return cell
    return None


def _engineer_name_exists(state: MatrixState, name: str, *,
                          exclude_id: str = "") -> bool:
    """Return True when another engineer already has ``name``."""
    return _agent_name_exists_for_kind(
        state,
        name,
        kind="engineer",
        exclude_id=exclude_id,
    )


def _architect_name_exists(state: MatrixState, name: str, *,
                           exclude_id: str = "") -> bool:
    """Return True when another architect already has ``name``."""
    return _agent_name_exists_for_kind(
        state,
        name,
        kind="architect",
        exclude_id=exclude_id,
    )


def _agent_name_exists_for_kind(state: MatrixState, name: str, *,
                                kind: str, exclude_id: str = "") -> bool:
    """Return True when another agent of ``kind`` already has ``name``."""
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    excluded = str(exclude_id or "").strip()
    expected_kind = str(kind or "").strip()
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != expected_kind:
            continue
        if excluded and cell.id == excluded:
            continue
        if str(cell.name or "").strip().lower() == normalized:
            return True
    return False


def _architect_persistent_prompt_text(group: str = "",
                                      action_system_prompt: str = "",
                                      state: MatrixState = None) -> str:
    """Build the persistent prompt for user-created architect agents."""
    from .architect import build_architect_system_prompt

    group_settings = None
    if state is not None and group:
        try:
            group_settings = state.get_group_settings(group)
        except Exception:
            group_settings = None

    architect_body = build_architect_system_prompt(
        group or "default",
        architect_settings=None,
        action_system_prompt=action_system_prompt,
        group_settings=group_settings,
    ).rstrip()

    return build_loom_system_prompt().rstrip() + "\n\n" + architect_body + "\n"


async def _handle_add_engineer_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_weaver_launch_config,
        create_agent_with_config,
        send_agent_prompt) -> dict:
    """Create and launch a persistent engineer agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Engineer name is required"}
    if _engineer_name_exists(state, name):
        return {
            "type": "error",
            "message": f"Engineer '{name}' already exists",
        }

    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = await resolve_base_dir(group)
    overrides = {
        key: str(data.get(key, "") or "").strip()
        for key in ("command", "provider", "directory")
        if str(data.get(key, "") or "").strip()
    }
    launch_cfg = resolve_weaver_launch_config(
        group,
        base_dir=base_dir,
        explicit_template="",
        overrides=overrides,
    )

    from .weaver import build_engineer_system_prompt

    persistent_prompt_text = build_engineer_system_prompt(
        group,
        state.get_weaver_settings(group),
        launch_cfg.get("system_prompt", ""),
        group_settings=state.get_group_settings(group),
    )
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
        is_weaver=True,
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template="",
        persistent_prompt_text=persistent_prompt_text,
        created_by_weaver_id="",
        owner_engineer_id="",
        kind="engineer",
        persistent=True,
        hired_by_architect_id=str(
            data.get("hired_by_architect_id", "") or ""
        ).strip(),
    )
    if not cell:
        return {"type": "error", "message": "Failed to create engineer"}

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt,
                cell=cell):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return {
        "id": cell.id,
        "slug": cell.slug,
        "name": cell.name,
        "kind": "engineer",
    }


async def _handle_add_architect_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_weaver_launch_config,
        create_agent_with_config,
        send_agent_prompt) -> dict:
    """Create and launch a persistent architect agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Architect name is required"}
    if _architect_name_exists(state, name):
        return {
            "type": "error",
            "message": f"Architect '{name}' already exists",
        }

    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = await resolve_base_dir(group)
    overrides = {
        key: str(data.get(key, "") or "").strip()
        for key in ("command", "provider", "directory")
        if str(data.get(key, "") or "").strip()
    }
    launch_cfg = resolve_weaver_launch_config(
        group,
        base_dir=base_dir,
        explicit_template="",
        overrides=overrides,
    )
    if loom_config.ARCHITECT_USES_WORKTREE:
        launch_cfg["worktree"] = bool(
            launch_cfg.get("worktree")
            or state.get_group_settings(group).git_worktree
        )
    else:
        launch_cfg["worktree"] = False

    persistent_prompt_text = _architect_persistent_prompt_text(
        group=group,
        action_system_prompt=launch_cfg.get("system_prompt", ""),
        state=state,
    )
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
        is_weaver=True,
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template="",
        persistent_prompt_text=persistent_prompt_text,
        created_by_weaver_id="",
        owner_engineer_id="",
        kind="architect",
        persistent=True,
        hired_by_architect_id="",
    )
    if not cell:
        return {"type": "error", "message": "Failed to create architect"}

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt,
                cell=cell):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return {
        "id": cell.id,
        "slug": cell.slug,
        "name": cell.name,
        "kind": "architect",
    }


async def _handle_add_worker_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_agent_launch_config,
        create_agent_with_config,
        send_agent_prompt) -> dict:
    """Create and launch a user-owned detached worker agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Worker name is required"}

    supplied_owner = str(data.get("owner_engineer_id", "") or "").strip()
    supplied_legacy_owner = str(
        data.get("created_by_weaver_id", "")
        or data.get("_created_by_weaver_id", "")
        or ""
    ).strip()
    if supplied_owner or supplied_legacy_owner:
        return {
            "type": "error",
            "message": (
                "Detached worker creation does not accept "
                "owner_engineer_id"
            ),
        }

    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = await resolve_base_dir(group)
    explicit_template = str(data.get("template", "") or "").strip()
    overrides = dict(data)
    for key in (
            "cmd",
            "name",
            "group",
            "kind",
            "owner_engineer_id",
            "created_by_weaver_id",
            "_created_by_weaver_id",
            "hired_by_architect_id",
    ):
        overrides.pop(key, None)
    launch_cfg = resolve_agent_launch_config(
        group,
        base_dir=base_dir,
        explicit_template=explicit_template,
        overrides=overrides,
    )
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text="",
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template=explicit_template,
        persistent_prompt_text="",
        created_by_weaver_id="",
        owner_engineer_id="",
        kind="worker",
        persistent=False,
        hired_by_architect_id="",
    )
    if not cell:
        return {"type": "error", "message": "Failed to create worker"}

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return {
        "id": cell.id,
        "slug": cell.slug,
        "name": cell.name,
        "kind": "worker",
    }


async def _handle_architect_engineer_hire_command(
        data: dict,
        state: MatrixState) -> dict:
    """Queue a user-approved pending hire for an architect."""
    architect = _resolve_architect_cell(
        state,
        architect_id=data.get("architect_id", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    if not architect.group:
        return {"type": "error", "message": "Architect is not assigned to a group"}

    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Engineer name is required"}

    pending_hire = state.save_pending_hire({
        "id": "hire-" + uuid.uuid4().hex[:12],
        "architect_id": architect.id,
        "requested_name": name,
        "requested_command": str(data.get("command", "") or "").strip(),
        "requested_provider": str(data.get("provider", "") or "").strip(),
        "requested_directory": str(data.get("directory", "") or "").strip(),
        "status": "pending",
        "resolution_note": "",
        "created_engineer_id": "",
    })
    if not pending_hire:
        return {"type": "error", "message": "Failed to create pending hire"}
    return {
        "hire_id": pending_hire["id"],
        "status": pending_hire["status"],
    }


async def _handle_pending_hire_approve_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_weaver_launch_config,
        create_agent_with_config,
        send_agent_prompt) -> dict:
    """Approve a pending architect hire and create the engineer."""
    pending_hire = state.load_pending_hire(data.get("id", ""))
    if not pending_hire:
        return {"type": "error", "message": "Pending hire not found"}

    if pending_hire["status"] == "approved":
        engineer = state.agents.get(pending_hire.get("created_engineer_id", ""))
        return {
            "engineer_id": str(pending_hire.get("created_engineer_id", "") or ""),
            "slug": str(getattr(engineer, "slug", "") or ""),
        }
    if pending_hire["status"] == "rejected":
        return {"type": "error", "message": "Pending hire has already been rejected"}

    architect = _resolve_architect_cell(
        state,
        architect_id=pending_hire.get("architect_id", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found for pending hire"}
    if not architect.group:
        return {"type": "error", "message": "Architect is not assigned to a group"}

    created = await _handle_add_engineer_command(
        {
            "name": pending_hire.get("requested_name", ""),
            "command": pending_hire.get("requested_command", ""),
            "provider": pending_hire.get("requested_provider", ""),
            "directory": pending_hire.get("requested_directory", ""),
            "group": architect.group,
            "hired_by_architect_id": architect.id,
        },
        state,
        resolve_base_dir=resolve_base_dir,
        resolve_weaver_launch_config=resolve_weaver_launch_config,
        create_agent_with_config=create_agent_with_config,
        send_agent_prompt=send_agent_prompt,
    )
    if created.get("type") == "error":
        return created

    saved = state.save_pending_hire({
        "id": pending_hire["id"],
        "status": "approved",
        "resolution_note": str(data.get("note", "") or "").strip(),
        "created_engineer_id": created["id"],
    })
    if not saved:
        return {"type": "error", "message": "Failed to resolve pending hire"}
    return {
        "engineer_id": created["id"],
        "slug": created["slug"],
    }


async def _handle_pending_hire_reject_command(
        data: dict,
        state: MatrixState) -> dict:
    """Reject a pending architect hire request."""
    pending_hire = state.load_pending_hire(data.get("id", ""))
    if not pending_hire:
        return {"type": "error", "message": "Pending hire not found"}
    if pending_hire["status"] == "approved":
        return {"type": "error", "message": "Pending hire has already been approved"}
    if pending_hire["status"] == "rejected":
        return {"ok": True}

    saved = state.save_pending_hire({
        "id": pending_hire["id"],
        "status": "rejected",
        "resolution_note": str(data.get("note", "") or "").strip(),
        "created_engineer_id": "",
    })
    if not saved:
        return {"type": "error", "message": "Failed to resolve pending hire"}
    return {"ok": True}


def _handle_pending_hire_list_command(data: dict, state: MatrixState) -> dict:
    """Return pending-hire rows for the UI or architect-scoped polling."""
    status_filter = str(data.get("status_filter", "") or "").strip()
    architect_id = str(data.get("architect_id", "") or "").strip()
    return {
        "pending_hires": state.load_pending_hires(
            status_filter=status_filter,
            architect_id=architect_id,
        )
    }


async def _handle_engineer_dismiss_command(
        data: dict,
        state: MatrixState, *,
        close_session,
        panel_event=None) -> dict:
    """Pause an engineer by closing sessions while preserving rows/history."""
    engineer = _resolve_engineer_cell(
        state,
        engineer_id=data.get("engineer_id", "") or data.get("id", ""),
        engineer_slug=data.get("slug", ""),
    )
    if not engineer:
        return {"type": "error", "message": "Engineer not found"}
    authority_error = _validate_engineer_lifecycle_authority(
        state,
        engineer,
        architect_id=data.get("architect_id", ""),
    )
    if authority_error:
        return authority_error

    if _agent_dismissed_at(engineer):
        return {
            "type": "ok",
            "engineer_id": engineer.id,
            "dismissed_at": _agent_dismissed_at(engineer),
            "already_dismissed": True,
            "closed_sessions": 0,
        }

    dismissed_at = int(time.time())
    engineer.dismissed_at = dismissed_at
    state._emit_agent(engineer)
    state._db_save_agent(engineer)

    errors: list[str] = []
    closed_sessions = 0
    cells_to_close = _dismissal_close_cells(state, engineer)
    # Dismiss is a hard pause: active tool calls may be interrupted and rely on normal session-resume recovery.
    for cell in cells_to_close:
        if await _close_cell_session_preserving_state(
                state,
                cell,
                close_session,
                errors=errors):
            closed_sessions += 1

    reason = str(data.get("reason", "") or "").strip()
    if panel_event:
        panel_event(
            "engineer_dismissed",
            engineer.id,
            engineer.name,
            engineer.group,
            reason or "Engineer dismissed",
        )
    result = {
        "type": "ok",
        "engineer_id": engineer.id,
        "dismissed_at": dismissed_at,
        "closed_sessions": closed_sessions,
        "closed_cells": [cell.id for cell in cells_to_close],
    }
    if errors:
        result["close_errors"] = errors
    return result


async def _handle_engineer_rehire_command(
        data: dict,
        state: MatrixState, *,
        bridge,
        worktree_mgr,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_weaver_launch_config,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_weaver,
        panel_event=None) -> dict:
    """Resume a dismissed engineer with the same id/slug and launch config."""
    engineer = _resolve_engineer_cell(
        state,
        engineer_id=data.get("engineer_id", "") or data.get("id", ""),
        engineer_slug=data.get("slug", ""),
    )
    if not engineer:
        return {"type": "error", "message": "Engineer not found"}
    authority_error = _validate_engineer_lifecycle_authority(
        state,
        engineer,
        architect_id=data.get("architect_id", ""),
    )
    if authority_error:
        return authority_error

    dismissed_at = _agent_dismissed_at(engineer)
    if not dismissed_at:
        return {
            "type": "ok",
            "engineer_id": engineer.id,
            "dismissed_at": 0,
            "already_hired": True,
            "replayed_messages": 0,
        }
    if engineer.session_id:
        engineer.dismissed_at = 0
        state._emit_agent(engineer)
        state._db_save_agent(engineer)
        replayed = await _replay_buffered_cross_kind_messages(
            state, bridge, engineer)
        if panel_event:
            panel_event(
                "engineer_rehired",
                engineer.id,
                engineer.name,
                engineer.group,
                "Engineer rehired",
            )
        return {
            "type": "ok",
            "engineer_id": engineer.id,
            "dismissed_at": 0,
            "already_running": True,
            "replayed_messages": replayed,
        }

    engineer.status = "stopped"
    state._emit_agent(engineer)
    state._db_save_agent(engineer)

    async def _restore_dismissed_after_failed_rehire() -> None:
        if engineer.session_id:
            try:
                await bridge.close_session(engineer.session_id)
            except Exception:
                log.exception(
                    "Failed to close partial rehire session for '%s'",
                    engineer.name,
                )
        engineer.dismissed_at = dismissed_at
        engineer.status = "stopped"
        engineer.session_id = None
        state._emit_agent(engineer)
        state._db_save_agent(engineer)

    try:
        relaunch_result = await _handle_relaunch_agent_command(
            {"id": engineer.id},
            state,
            bridge=bridge,
            worktree_mgr=worktree_mgr,
            resolve_base_dir=resolve_base_dir,
            resolve_agent_launch_config=resolve_agent_launch_config,
            resolve_weaver_launch_config=resolve_weaver_launch_config,
            apply_persistent_prompt=apply_persistent_prompt,
            build_cell_persistent_prompt=build_cell_persistent_prompt,
            persistent_prompt_filename=persistent_prompt_filename,
            is_designated_weaver=is_designated_weaver,
        )
    except Exception as exc:
        log.exception("Failed to rehire engineer '%s'", engineer.name)
        await _restore_dismissed_after_failed_rehire()
        return {"type": "error", "message": f"Failed to rehire engineer: {exc}"}

    if relaunch_result and relaunch_result.get("type") == "error":
        await _restore_dismissed_after_failed_rehire()
        return relaunch_result
    if not engineer.session_id:
        await _restore_dismissed_after_failed_rehire()
        return {
            "type": "error",
            "message": "Failed to rehire engineer: no session was created",
        }

    engineer.dismissed_at = 0
    state._emit_agent(engineer)
    state._db_save_agent(engineer)
    replayed = await _replay_buffered_cross_kind_messages(
        state, bridge, engineer)
    if panel_event:
        panel_event(
            "engineer_rehired",
            engineer.id,
            engineer.name,
            engineer.group,
            "Engineer rehired",
        )
    return {
        "type": "ok",
        "engineer_id": engineer.id,
        "dismissed_at": 0,
        "session_id": engineer.session_id or "",
        "replayed_messages": replayed,
    }


async def _handle_delete_engineer_command(
        data: dict,
        state: MatrixState, *,
        close_agent_session_only) -> dict:
    """Delete an engineer after transferring owned workers/tasks to user."""
    policy = str(data.get("transfer_policy", "user") or "user").strip()
    if policy not in {"user", "orphan"}:
        return {
            "type": "error",
            "message": "transfer_policy must be 'user' or 'orphan'",
        }
    del policy

    engineer = _resolve_engineer_cell(
        state,
        engineer_id=data.get("id", ""),
        engineer_slug=data.get("slug", ""),
    )
    if not engineer:
        return {"type": "error", "message": "Engineer not found"}

    transferred_agents = 0
    for cell in list(state.agents.values()):
        if cell.cell_type != "agent":
            continue
        owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
        creator_id = str(getattr(cell, "created_by_weaver_id", "") or "").strip()
        if owner_id != engineer.id and creator_id != engineer.id:
            continue
        if owner_id == engineer.id:
            cell.owner_engineer_id = ""
        if creator_id == engineer.id:
            cell.created_by_weaver_id = ""
        transferred_agents += 1
        state._emit_agent(cell)
        state._db_save_agent(cell)

    transferred_tasks = 0
    for task in list(state.board_tasks.values()):
        if str(getattr(task, "assigned_engineer_id", "") or "").strip() != engineer.id:
            continue
        if task.assigned_engineer_id != "":
            transferred_tasks += 1
        state.board_update_task(task.id, assigned_engineer_id="")

    await close_agent_session_only(engineer)
    return {
        "transferred_agents": transferred_agents,
        "transferred_tasks": transferred_tasks,
    }


async def _handle_rename_engineer_command(
        data: dict,
        state: MatrixState, *,
        update_session) -> dict:
    """Rename an engineer while preserving engineer-specific fields."""
    engineer = _resolve_engineer_cell(
        state,
        engineer_id=data.get("id", ""),
        engineer_slug=data.get("slug", ""),
    )
    if not engineer:
        return {"type": "error", "message": "Engineer not found"}

    new_name = str(data.get("new_name", "") or "").strip()
    if not new_name:
        return {"type": "error", "message": "new_name is required"}
    if _engineer_name_exists(state, new_name, exclude_id=engineer.id):
        return {
            "type": "error",
            "message": f"Engineer '{new_name}' already exists",
        }

    old_name = engineer.name
    state.update_agent(
        engineer.id,
        name=new_name,
        tab_color=engineer.tab_color,
        icon=engineer.icon,
    )
    if new_name != old_name:
        state.history_update_agent(engineer, name=engineer.name, slug=engineer.slug)
        if engineer.session_id:
            await update_session(engineer, old_name)
    return {
        "id": engineer.id,
        "slug": engineer.slug,
        "name": engineer.name,
        "kind": "engineer",
    }


async def _handle_delete_architect_command(
        data: dict,
        state: MatrixState, *,
        close_agent_session_only) -> dict:
    """Delete an architect after transferring hired engineers to the user."""
    architect = _resolve_architect_cell(
        state,
        architect_id=data.get("id", ""),
        architect_slug=data.get("slug", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found"}

    transferred_engineers = 0
    for cell in list(state.agents.values()):
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            continue
        if str(getattr(cell, "hired_by_architect_id", "") or "").strip() != architect.id:
            continue
        cell.hired_by_architect_id = ""
        transferred_engineers += 1
        state._emit_agent(cell)
        state._db_save_agent(cell)

    archived_decisions = 0
    for decision in state.load_decisions_for_architect(
            architect.id, include_archived=False):
        saved = state.save_decision({
            "id": decision["id"],
            "archived": True,
        })
        if saved:
            archived_decisions += 1

    await close_agent_session_only(architect)
    return {
        "transferred_engineers": transferred_engineers,
        "archived_decisions": archived_decisions,
    }


async def _dispatch_architect_ui_tool(name: str, args: dict,
                                      state: MatrixState) -> dict:
    """Run an architect-scoped shared-core tool for the user-facing UI."""
    from .mcp_tools_shared import dispatch_scoped_tool

    caller_id = str(args.get("architect_id", "") or "").strip()
    if not caller_id:
        return {"type": "error", "message": "architect_id is required"}

    async def _unexpected_handle_command(_data: dict) -> dict:
        return {
            "type": "error",
            "message": "Architect UI command cannot route nested commands",
        }

    payload_text, is_error = await dispatch_scoped_tool(
        name,
        args,
        _unexpected_handle_command,
        state,
        tool_prefix="architect_",
        caller_kind="architect",
        caller_id=caller_id,
    )
    if is_error:
        return {"type": "error", "message": payload_text}
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {"type": "ok", "message": payload_text}
    if isinstance(payload, dict):
        payload.setdefault("type", "ok")
        return payload
    return {"type": "ok", "data": payload}


async def _handle_relaunch_agent_command(
        data: dict,
        state: MatrixState, *,
        bridge,
        worktree_mgr,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_weaver_launch_config,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_weaver) -> dict | None:
    """Relaunch a stopped agent or terminal using current launch settings."""
    cell = state.agents.get(data.get("id", ""))
    if not cell:
        return {"type": "error", "message": "Agent not found"}
    if cell.status != "stopped":
        return None

    owner = _find_active_worktree_owner(state, cell)
    if owner:
        return {
            "type": "error",
            "message":
                f"Cannot relaunch '{cell.name}' while "
                f"'{owner.name}' is active on "
                f"{owner.worktree_branch or owner.worktree_path}",
        }

    gs = state.get_group_settings(cell.group)
    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    use_weaver_launch = (
        str(getattr(cell, "kind", "") or "").strip() in ("engineer", "architect")
        or is_designated_weaver(cell)
    )
    resolver = (
        resolve_weaver_launch_config if use_weaver_launch
        else resolve_agent_launch_config
    )
    launch_cfg = resolver(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    cell.session_resume = bool(
        launch_cfg.get("session_resume", cell.session_resume))
    cell.idle_timeout = int(
        launch_cfg.get("idle_timeout", cell.idle_timeout) or 0)
    if cell.cell_type == "agent":
        # Fall back to the cell's persisted values when the re-resolved
        # launch_cfg has empty entries.  The group-level weaver_settings
        # can't encode per-agent provider/command choices, so resolving
        # without overrides often returns a generic default that would
        # otherwise clobber an architect or engineer's actual config —
        # including agent_type, which drives MCP/hook installation.
        cell.command = launch_cfg.get("command") or cell.command
        cell.profile = launch_cfg.get("profile") or cell.profile
        cell.tab_color = launch_cfg.get("tab_color") or cell.tab_color
        cell.icon = launch_cfg.get("icon") or cell.icon
        cell.agent_type = launch_cfg.get("agent_type") or cell.agent_type
        if not cell.worktree_path:
            cell.directory = launch_cfg.get("directory") or cell.directory
    cell.worktree_base_dir = (
        launch_cfg.get("worktree_base_dir")
        or cell.worktree_base_dir
        or ".loom/worktrees")
    cell.worktree_auto_checkpoint = bool(
        launch_cfg.get(
            "worktree_auto_checkpoint",
            cell.worktree_auto_checkpoint))
    cell.checkpoint_on_progress = bool(
        launch_cfg.get(
            "checkpoint_on_progress",
            cell.checkpoint_on_progress))
    cell.worktree_merge_squash = bool(
        launch_cfg.get(
            "worktree_merge_squash",
            cell.worktree_merge_squash))
    state._emit_agent(cell)
    state._db_save_agent(cell)

    if cell.cell_type == "terminal":
        env = {**gs.env_vars, **gs.terminal_env_vars} or None
        env_file = gs.terminal_env_file or gs.env_file
        shell = gs.terminal_shell or gs.shell or ""
        init_script = gs.terminal_init_script
    else:
        env = runtime_env_vars_for_cell(cell, launch_cfg.get("env_vars"))
        env_file = launch_cfg.get("env_file", "")
        shell = launch_cfg.get("shell", "")
        init_script = ""
        prev_directory = cell.directory
        if cell.worktree_path:
            if await worktree_mgr.validate(cell):
                cell.directory = cell.worktree_path
                log.info("Reusing worktree for '%s': %s",
                         cell.name, cell.worktree_path)
            else:
                log.warning("Worktree invalid for '%s', clearing", cell.name)
                cell.worktree_path = ""
                cell.worktree_branch = ""
                cell.worktree_repo_root = ""
                cell.worktree_base_branch = ""
                state._emit_agent(cell)
                state._db_save_agent(cell)
        if not cell.worktree_path and launch_cfg.get("worktree") and cell.directory:
            repo_root = await worktree_mgr.get_repo_root(cell.directory)
            if repo_root:
                wt_path = await worktree_mgr.create(
                    cell,
                    repo_root,
                    base_dir=cell.worktree_base_dir or ".loom/worktrees",
                    base_branch=launch_cfg.get("worktree_base_branch", "") or "",
                    symlinks=launch_cfg.get("worktree_symlinks", []),
                    state=state,
                )
                if wt_path:
                    cell.directory = wt_path
                    state._emit_agent(cell)
                    state._db_save_agent(cell)
        if (cell.agent_type and prev_directory and prev_directory != cell.directory):
            get_adapter(cell.agent_type).uninstall_persistent_prompt(
                os.path.expanduser(prev_directory),
                persistent_prompt_filename(cell),
            )
    apply_persistent_prompt(
        cell, launch_cfg,
        build_cell_persistent_prompt(cell, launch_cfg))
    state._emit_agent(cell)
    state._db_save_agent(cell)
    await bridge.create_session(
        cell,
        env_vars=env,
        env_file=env_file,
        init_script=init_script,
        shell=shell,
        system_prompt=launch_cfg.get("system_prompt", ""),
        mcp_entrypoint=mcp_entrypoint_for_cell(cell),
    )
    return None


async def _handle_restart_agent_command(
        data: dict,
        state: MatrixState, *,
        bridge,
        worktree_mgr,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_weaver_launch_config,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_weaver,
        send_agent_prompt) -> dict | None:
    """Restart an agent from scratch using its original launch parameters.

    Unlike ``relaunch`` (which resumes the prior provider session via
    ``session_resume``), restart closes the current session, clears the
    resumed-session state, and re-delivers the full startup + initial
    prompt sequence, as if the agent had just been created.
    """
    cell = state.agents.get(data.get("id", ""))
    if not cell:
        return {"type": "error", "message": "Agent not found"}
    if cell.cell_type != "agent":
        return {"type": "error",
                "message": "Only agents can be restarted"}

    owner = _find_active_worktree_owner(state, cell)
    if owner:
        return {
            "type": "error",
            "message":
                f"Cannot restart '{cell.name}' while "
                f"'{owner.name}' is active on "
                f"{owner.worktree_branch or owner.worktree_path}",
        }

    # Close any live session before opening a fresh one.
    if cell.session_id:
        try:
            await bridge.close_session(cell.session_id)
        except Exception:
            log.exception("Failed to close session for '%s' during restart",
                          cell.name)
    cell.status = "stopped"
    cell.session_id = None
    # Start from scratch — drop any resumed provider session and any
    # running task context so the new session gets a fresh run.
    cell.agent_session_id = ""
    cell.tasks_dispatched = 0
    cell.current_task_id = ""
    cell.mcp_messages = []

    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    kind = str(getattr(cell, "kind", "") or "").strip()
    use_weaver_launch = (
        kind in ("engineer", "architect")
        or is_designated_weaver(cell)
    )
    resolver = (
        resolve_weaver_launch_config if use_weaver_launch
        else resolve_agent_launch_config
    )
    launch_cfg = resolver(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    cell.session_resume = bool(
        launch_cfg.get("session_resume", cell.session_resume))
    cell.idle_timeout = int(
        launch_cfg.get("idle_timeout", cell.idle_timeout) or 0)
    cell.command = launch_cfg.get("command") or cell.command
    cell.profile = launch_cfg.get("profile") or cell.profile
    cell.tab_color = launch_cfg.get("tab_color") or cell.tab_color
    cell.icon = launch_cfg.get("icon") or cell.icon
    cell.agent_type = launch_cfg.get("agent_type") or cell.agent_type
    if not cell.worktree_path:
        cell.directory = launch_cfg.get("directory") or cell.directory
    cell.worktree_base_dir = (
        launch_cfg.get("worktree_base_dir")
        or cell.worktree_base_dir
        or ".loom/worktrees")
    state._emit_agent(cell)
    state._db_save_agent(cell)

    prev_directory = cell.directory
    if cell.worktree_path:
        if await worktree_mgr.validate(cell):
            cell.directory = cell.worktree_path
        else:
            log.warning("Worktree invalid for '%s', clearing", cell.name)
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_base_branch = ""
            state._emit_agent(cell)
            state._db_save_agent(cell)
    if (cell.agent_type and prev_directory
            and prev_directory != cell.directory):
        get_adapter(cell.agent_type).uninstall_persistent_prompt(
            os.path.expanduser(prev_directory),
            persistent_prompt_filename(cell),
        )

    # Rebuild and re-apply the persistent prompt the same way creation does.
    persistent_prompt_text = build_cell_persistent_prompt(cell, launch_cfg)
    if kind == "architect":
        persistent_prompt_text = _architect_persistent_prompt_text(
            launch_cfg.get("system_prompt", ""))
    apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
    state._emit_agent(cell)
    state._db_save_agent(cell)

    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
        is_weaver=use_weaver_launch,
    )

    await bridge.create_session(
        cell,
        env_vars=runtime_env_vars_for_cell(cell, launch_cfg.get("env_vars")),
        env_file=launch_cfg.get("env_file", ""),
        shell=launch_cfg.get("shell", ""),
        system_prompt=launch_cfg.get("system_prompt", ""),
        mcp_entrypoint=mcp_entrypoint_for_cell(cell),
    )

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt,
                cell=cell):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return None


async def main(connection=None):
    log.info("Loom starting (port=%d)", WS_PORT)
    profiling.configure_asyncio(asyncio.get_running_loop())
    db = LoomDB(DB_FILE)
    db.init()
    log.info("SQLite database opened at %s", DB_FILE)
    state = MatrixState(db=db)
    state.load()
    capture_deploy_boot_state(state, loom_config.SCRIPT_DIR)
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    event_log = EventLog()
    panel_log = PanelEventLog(
        max_size=state.global_settings.max_event_log, db=db)
    state.panel_log = panel_log
    notifier = NotificationManager(state)
    notifier.start()
    event_bus = EventBus(state, event_log, notifier, panel_log=panel_log)
    event_bus.start()
    asyncio.create_task(health_check(state, event_log, event_bus, notifier))
    from .event_ingest_client import EventIngestClient

    event_ingest_client = EventIngestClient(data_dir=DATA_DIR)
    try:
        await event_ingest_client.connect()
        log.info("Event ingest daemon connected at %s",
                 event_ingest_client.socket_path)
    except Exception:
        # Keep startup alive; endpoint appends and the drainer both retry via
        # ensure_running on demand. If append still cannot persist an event,
        # /events returns 503 instead of pretending the event is safe.
        log.exception("Event ingest daemon unavailable at startup")
    event_ingest_drainer = EventIngestDrainer(
        event_ingest_client,
        event_bus,
        state,
    )
    event_ingest_drainer.start()
    log.info("Event bus, durable event-ingest drainer, health monitor, "
             "and notifications started")

    supervisor_banner: dict | None = None
    if STANDALONE:
        from .local_pty import LocalPtyAdapter, SupervisedPtyAdapter

        if loom_config.PROFILE_SKIP_PTY:
            bridge = LocalPtyAdapter(state)
            log.info("Profile mode — PTY supervisor skipped")
        else:
            from . import pty_supervisor

            bridge = None
            try:
                sock_path = pty_supervisor.ensure_running(DATA_DIR)
                bridge = SupervisedPtyAdapter(state, sock_path)
                log.info(
                    "Standalone mode — using PTY supervisor at %s", sock_path)
            except Exception as exc:
                log.exception(
                    "PTY supervisor unavailable — falling back to in-memory "
                    "(terminals will not survive daemon restart)")
                supervisor_banner = {
                    "kind": "supervisor_unavailable",
                    "message": (
                        "PTY supervisor unavailable — terminals will not "
                        "survive a Loom restart. See loom.log for details."
                    ),
                    "detail": str(exc),
                }
                bridge = LocalPtyAdapter(state)
    else:
        from .bridge import ITerm2Adapter

        bridge = ITerm2Adapter(connection, state)
    worktree_mgr = WorktreeManager()
    action_mgr = ActionManager()
    template_mgr = RoleManager()
    agent_launch = AgentLaunchService(
        state=state,
        connection=connection,
        bridge=bridge,
        worktree_mgr=worktree_mgr,
        template_mgr=template_mgr,
    )

    from .weaver import WeaverEventBuffer
    async def _inject_digest_message(target, message: str, **kwargs):
        await inject_mcp_message(state, bridge, target, message, **kwargs)

    weaver_buffer = WeaverEventBuffer(
        state,
        bridge,
        inject_message=_inject_digest_message,
    )
    weaver_buffer.start()
    event_bus._weaver_buffer = weaver_buffer
    panel_log.on_event = weaver_buffer.on_panel_event
    log.info("Weaver event buffer started")


    async def _safe_remove_worktree(cell):
        """Remove a worktree only if no other agent shares it."""
        if not cell.worktree_path:
            return True
        other_users = [a for a in state.agents.values()
                       if a.id != cell.id
                       and a.worktree_path == cell.worktree_path]
        if other_users:
            log.info("Skipping worktree removal for '%s' — shared with %s",
                     cell.name,
                     ", ".join(a.name for a in other_users))
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_base_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_checkpoints = 0
            return True
        else:
            return await worktree_mgr.remove(cell)

    async def _cleanup_after_merge(cell, *,
                                   close_agent: bool = False,
                                   remove_worktree: bool = False) -> dict:
        """Apply optional post-merge cleanup and return a status summary."""
        cleanup = {
            "close_agent": close_agent,
            "remove_worktree": remove_worktree,
            "agent_closed": False,
            "worktree_removed": False,
            "errors": [],
        }
        if not close_agent and not remove_worktree:
            return cleanup

        if close_agent:
            removed = await _close_agent_session_only(
                cell,
                errors=cleanup["errors"],
            )
            cleanup["agent_closed"] = True
            if remove_worktree:
                removed_worktree = False
                for c in removed:
                    if not c.worktree_path:
                        continue
                    ok = await _safe_remove_worktree(c)
                    if ok:
                        removed_worktree = True
                    else:
                        cleanup["errors"].append(
                            f"Failed to remove worktree for '{c.name}'."
                        )
                cleanup["worktree_removed"] = removed_worktree
            return cleanup

        repo_root = cell.worktree_repo_root
        ok = await _safe_remove_worktree(cell)
        if ok:
            cleanup["worktree_removed"] = True
        else:
            cleanup["errors"].append(
                f"Failed to remove worktree for '{cell.name}'."
            )
        if repo_root:
            cell.directory = repo_root
        if ok and cell.cell_type == "agent" and cell.session_id:
            await _relaunch_agent_after_worktree_removal(
                cell,
                bridge=bridge,
                state=state,
                resolve_base_dir=_resolve_base_dir,
                resolve_agent_launch_config=_resolve_agent_launch_config,
                resolve_weaver_launch_config=_resolve_weaver_launch_config,
                is_designated_weaver=_is_designated_weaver,
                apply_persistent_prompt=_apply_persistent_prompt,
                build_cell_persistent_prompt=_build_cell_persistent_prompt,
            )
        else:
            state._emit_agent(cell)
            state._db_save_agent(cell)
        return cleanup

    async def _close_agent_session_only(cell, *,
                                        errors: list | None = None) -> list:
        """Remove an agent session without removing its worktree."""
        if not cell:
            return []
        removed = state.remove_agent(cell.id)
        for c in removed:
            if c.cell_type == "agent":
                state.history_remove_agent(c)
            if c.session_id:
                try:
                    await bridge.close_session(c.session_id)
                except Exception as exc:
                    if errors is not None:
                        errors.append(
                            f"Failed to close session for '{c.name}': {exc}"
                        )
                    log.exception("Failed to close session for '%s'", c.name)
            if c.agent_type and c.directory:
                adapter = get_adapter(c.agent_type)
                try:
                    if hasattr(adapter, "uninstall_hooks"):
                        adapter.uninstall_hooks(
                            os.path.expanduser(c.directory))
                    if hasattr(adapter, "uninstall_mcp_config"):
                        adapter.uninstall_mcp_config(
                            os.path.expanduser(c.directory))
                    adapter.uninstall_persistent_prompt(
                        os.path.expanduser(c.directory),
                        _persistent_prompt_filename(c))
                except Exception:
                    log.exception(
                        "Failed agent cleanup while closing '%s'",
                        c.name,
                    )
            event_bus.cleanup_cell(c.id)
            worktree_mgr.forget_refresh_state(c.id)
        return removed

    def _checkpoint_message(cell) -> str:
        """Build a checkpoint commit message from the agent's last summary."""
        summary = cell.last_summary.strip()
        n = cell.worktree_checkpoints + 1
        subject = f"loom: checkpoint {n} — {cell.name}"
        if summary:
            return f"{subject}\n\n{summary}"
        return subject

    async def _on_agent_session_end(cell):
        """Handle agent turn completion: auto-checkpoint."""
        state.history_snapshot_tokens(cell)
        # Auto-checkpoint
        if cell.worktree_path and cell.cell_type == "agent":
            if not cell.worktree_auto_checkpoint:
                return
            block_reason = _shared_review_checkpoint_block_reason(
                state,
                cell,
            )
            if block_reason:
                log.info("Skipping session-end checkpoint: %s", block_reason)
                return
            msg = _checkpoint_message(cell)
            sha = await worktree_mgr.checkpoint(cell, message=msg)
            if sha:
                state._db_save_agent(cell)

    # Minimum seconds between progress-triggered checkpoints per agent.
    _CHECKPOINT_INTERVAL = 300  # 5 minutes

    async def _checkpoint_on_report(cell, message: str = ""):
        """Checkpoint worktree on ai progress/done if enabled and throttled."""
        if not cell.worktree_path or cell.cell_type != "agent":
            return
        if not cell.checkpoint_on_progress:
            return
        block_reason = _shared_review_checkpoint_block_reason(state, cell)
        if block_reason:
            log.info("Skipping progress checkpoint: %s", block_reason)
            return
        now = time.time()
        if (cell.last_checkpoint_at
                and now - cell.last_checkpoint_at < _CHECKPOINT_INTERVAL):
            return
        n = cell.worktree_checkpoints + 1
        subject = f"loom: checkpoint {n} — {cell.name}"
        if message:
            msg = f"{subject}\n\n{message}"
        else:
            msg = subject
        sha = await worktree_mgr.checkpoint(cell, message=msg)
        if sha:
            cell.last_checkpoint_at = now
            state._db_save_agent(cell)

    async def _broadcast_toast(message, level="info"):
        """Send a toast notification to all WS clients."""
        msg = json.dumps({"type": "toast", "message": message,
                          "level": level})
        dead = set()
        for ws_client in state._ws_clients:
            try:
                await ws_client.send_str(msg)
            except Exception:
                dead.add(ws_client)
        state._ws_clients -= dead

    # Persistent supervisor-health banner. Only populated in standalone
    # mode when the supervisor is unavailable / restarted. Latest state
    # is replayed to each newly connected WS client.
    supervisor_banner_state: dict = {"banner": supervisor_banner}

    async def _broadcast_system_banner(banner):
        supervisor_banner_state["banner"] = banner
        payload = json.dumps({"type": "system_banner", "banner": banner})
        dead = set()
        for ws_client in state._ws_clients:
            try:
                await ws_client.send_str(payload)
            except Exception:
                dead.add(ws_client)
        state._ws_clients -= dead

    async def _on_supervisor_event(kind, detail):
        """Translate SupervisedPtyAdapter events into user-visible
        banner + macOS notification.
        """
        if kind == "fresh_instance":
            banner = {
                "kind": "supervisor_restarted",
                "message": (
                    "PTY supervisor restarted — open terminals were "
                    "lost. Relaunch affected sessions from the UI."
                ),
            }
            await _broadcast_system_banner(banner)
            notifier.on_system_alert(
                "Loom — supervisor restarted",
                "Open terminals were lost. Relaunch them from the UI.")
        elif kind == "reconnected":
            # Routine reconnect to the same instance — clear banner.
            await _broadcast_system_banner(None)
        elif kind == "connect_failed":
            banner = {
                "kind": "supervisor_unavailable",
                "message": (
                    "Lost connection to the PTY supervisor — "
                    "terminal output may stall until it comes back."
                ),
            }
            await _broadcast_system_banner(banner)

    # Duck-type: only SupervisedPtyAdapter has this attribute.
    if hasattr(bridge, "on_supervisor_event"):
        bridge.on_supervisor_event = _on_supervisor_event

    # Signal bridge when agent TUI is ready (hook-based session_start)
    event_bus.on_session_start = lambda cell: bridge.signal_input_ready(cell.id)
    # Handle agent turn completion (hook-based session_end)
    event_bus.on_session_end = _on_agent_session_end
    # Also checkpoint when the terminal session is actually closed (tab closed)
    bridge.on_session_terminated = _on_agent_session_end

    def _runtime_payload() -> dict:
        return {
            "standalone": STANDALONE,
            "embedded_terminal": bridge.capabilities.supports_embedded_terminal,
            "layout": "ide" if bridge.capabilities.supports_embedded_terminal else "classic",
            "terminal_backend": "pty" if STANDALONE else "iterm2",
            "home_directory": str(Path.home()),
            "profile": os.environ.get("LOOM_PROFILE", "").strip(),
            "data_dir": str(DATA_DIR),
            "port": WS_PORT,
            "default_command": state.get_default_command(),
        }

    async def _state_payload() -> dict:
        # Prefill the per-repo branch cache before state.to_dict() runs —
        # otherwise the sync weaver-stream snapshot inside it would fork
        # `git show-ref` per branch on the event loop, stalling the WS.
        try:
            from .worktree_streams import prefill_branch_exists_for_state
            await prefill_branch_exists_for_state(state)
        except Exception:
            log.exception("Branch-exists prefill failed for state payload")
        return {
            "type": "state",
            "seq": state._seq,
            **state.to_dict(),
            **weaver_buffer.export_state(),
            "providers": get_providers(),
            "runtime": _runtime_payload(),
        }

    terminal_clients: dict[str, set[web.WebSocketResponse]] = {}

    async def _broadcast_terminal_output(cell_id: str, session_id: str, text: str):
        if not text:
            return
        payload = json.dumps({
            "type": "output",
            "cell_id": cell_id,
            "session_id": session_id,
            "data": text,
        })
        dead = set()
        for ws_client in terminal_clients.get(cell_id, set()):
            try:
                await ws_client.send_str(payload)
            except Exception:
                dead.add(ws_client)
        if dead:
            terminal_clients.get(cell_id, set()).difference_update(dead)

    bridge.on_terminal_output = _broadcast_terminal_output

    async def _on_terminal_disconnected(cell):
        """Auto-remove a terminal when its tab is closed (close_on_disconnect)."""
        log.info("Auto-removing terminal '%s' (close_on_disconnect)", cell.name)
        removed = state.remove_agent(cell.id)
        for c in removed:
            event_bus.cleanup_cell(c.id)
            worktree_mgr.forget_refresh_state(c.id)

    bridge.on_terminal_disconnected = _on_terminal_disconnected
    await bridge.start()
    log.info("Startup checkpoint: bridge started")
    await bridge.reconnect_orphans()
    log.info("Startup checkpoint: orphan reconnect complete")
    asyncio.create_task(_worktree_diff_updater(state, worktree_mgr))
    log.info("Startup checkpoint: worktree diff updater scheduled")

    keybindings = None
    _displaced = [[]]

    async def _resolve_base_dir(group: str = "") -> str:
        return await agent_launch.resolve_base_dir(group)

    def _resolve_provider_command(
        provider: str, boot_command: str, default_command: str,
    ) -> tuple[str, str]:
        return agent_launch.resolve_provider_command(
            provider, boot_command, default_command
        )

    def _suggest_template_agent_name(group: str, template_name: str,
                                     base_dir: str = "") -> str:
        return agent_launch.suggest_template_agent_name(
            group, template_name, base_dir
        )

    def _resolve_agent_launch_config(group: str, *,
                                     base_dir: str = "",
                                     explicit_template: str = "",
                                     overrides: dict | None = None) -> dict:
        return agent_launch.resolve_agent_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=overrides,
        )

    def _resolve_weaver_launch_config(group: str, *,
                                      base_dir: str = "",
                                      explicit_template: str = "",
                                      overrides: dict | None = None) -> dict:
        return agent_launch.resolve_weaver_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=overrides,
        )

    async def _create_child_terminals(group: str, parent_cell,
                                      terminals: list[dict] | None = None,
                                      count: int = 0):
        return await agent_launch.create_child_terminals(
            group, parent_cell, terminals=terminals, count=count
        )

    def _persistent_prompt_filename(cell) -> str:
        return agent_launch.persistent_prompt_filename(cell)

    def _apply_persistent_prompt(cell, launch_cfg: dict,
                                 prompt_text: str = "") -> None:
        agent_launch.apply_persistent_prompt(cell, launch_cfg, prompt_text)

    async def _create_agent_with_config(group: str, name: str,
                                        launch_cfg: dict, *,
                                        explicit_template: str = "",
                                        target_session_id: str = "",
                                        target_window_id: str = "",
                                        persistent_prompt_text: str = "",
                                        created_by_weaver_id: str = "",
                                        owner_engineer_id: str = "",
                                        kind: str = "",
                                        persistent: bool = False,
                                        hired_by_architect_id: str = "",
                                        inherited_worktree_from=None,
                                        restore_focus_to_prev_tab: bool = False):
        return await agent_launch.create_agent_with_config(
            group,
            name,
            launch_cfg,
            explicit_template=explicit_template,
            target_session_id=target_session_id,
            target_window_id=target_window_id,
            persistent_prompt_text=persistent_prompt_text,
            created_by_weaver_id=created_by_weaver_id,
            owner_engineer_id=owner_engineer_id,
            kind=kind,
            persistent=persistent,
            hired_by_architect_id=hired_by_architect_id,
            inherited_worktree_from=inherited_worktree_from,
            restore_focus_to_prev_tab=restore_focus_to_prev_tab,
        )

    async def _send_agent_prompt(cell, prompt: str, *,
                                 delay: float = 0,
                                 persist: bool = False,
                                 background: bool = False,
                                 prime_input_ready: bool = False,
                                 settled_submit: bool = False):
        return await agent_launch.send_agent_prompt(
            cell,
            prompt,
            delay=delay,
            persist=persist,
            background=background,
            prime_input_ready=prime_input_ready,
            settled_submit=settled_submit,
        )

    # -- Persistent system prompt ---------------------------------------------

    def _build_dispatch_persistent_prompt(system_prompt: str = "") -> str:
        parts = []
        if system_prompt:
            parts.append(system_prompt.rstrip())
        parts.append(build_loom_system_prompt().rstrip())
        return "\n\n".join(parts) + "\n"

    def _build_cell_persistent_prompt(cell, launch_cfg: dict) -> str:
        if cell.cell_type != "agent" or not launch_cfg.get("agent_type"):
            return ""
        gs = state.get_group_settings(cell.group)
        if gs.weaver_agent_id == cell.id:
            from .weaver import build_weaver_system_prompt
            ws = state.get_weaver_settings(cell.group)
            return build_weaver_system_prompt(
                cell.group, ws, launch_cfg.get("system_prompt", ""),
                group_settings=gs)
        if cell.kind == "engineer":
            from .weaver import build_engineer_system_prompt
            ws = state.get_weaver_settings(cell.group)
            return build_engineer_system_prompt(
                cell.group, ws, launch_cfg.get("system_prompt", ""),
                group_settings=gs)
        if cell.kind == "architect":
            return _architect_persistent_prompt_text(
                group=cell.group,
                action_system_prompt=launch_cfg.get("system_prompt", ""),
                state=state,
            )
        return _build_dispatch_persistent_prompt(
            launch_cfg.get("system_prompt", ""))

    def _is_designated_weaver(cell) -> bool:
        if not cell or cell.cell_type != "agent":
            return False
        gs = state.get_group_settings(cell.group)
        return bool(gs and gs.weaver_agent_id == cell.id)

    def _ownership_weaver_id_for_dispatch_source(cell) -> str:
        """Return the immutable Weaver owner id to stamp on new agents."""
        if not cell or cell.cell_type != "agent":
            return ""
        owner_id = str(getattr(cell, "created_by_weaver_id", "") or "").strip()
        if owner_id:
            return owner_id
        if _is_designated_weaver(cell):
            return cell.id
        return ""

    def _record_task_dispatch(cell, task, lane: str) -> None:
        """Link a task to an agent and persist dispatch history."""
        repo_root = cell.worktree_repo_root or cell.git_root or ""
        next_boundary_task_id = ""
        if cell.worktree_branch and repo_root:
            latest = latest_boundary_task(
                state.board_tasks.values(),
                repo_root=repo_root,
                branch=cell.worktree_branch,
                statuses={"open"},
            )
            if latest and latest.id != task.id:
                next_boundary_task_id = latest.id
        if task.resume_after_boundary_task_id != next_boundary_task_id:
            task.resume_after_boundary_task_id = next_boundary_task_id
        state.board_update_task(task.id, agent_id=cell.id, lane=lane)
        state.auto_dispatch_queue_remove_task(task.id)
        cell.current_task_id = task.id
        state.mark_agent_progress(cell, emit=False)

    def _iso_to_unix(ts: str) -> float | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            return None

    def _current_board_tasks_for_agent(agent_id: str) -> list[dict]:
        """Best-effort fallback for active agents missing persisted task rows."""
        tasks = []
        for task in state.board_tasks.values():
            if task.agent_id != agent_id:
                continue
            outcome = ""
            if task_counts_as_done(task):
                outcome = "done"
            elif task.lane == "Archived":
                outcome = "archived"
            elif "loom:error" in (task.labels or []):
                outcome = "error"
            elif "loom:blocked" in (task.labels or []):
                outcome = "blocked"
            tasks.append({
                "agent_id": agent_id,
                "task_id": task.id,
                "task_title": task.task,
                "started_at": (_iso_to_unix(task.updated_at)
                                or _iso_to_unix(task.created_at)),
                "completed_at": (_iso_to_unix(task.updated_at)
                                  if task_is_closed(task) else None),
                "outcome": outcome,
            })
        tasks.sort(key=lambda t: t.get("started_at") or 0, reverse=True)
        return tasks

    def _enrich_history_record(record: dict) -> dict:
        """Overlay live task counts for active agents."""
        if not record:
            return record
        cell = state.agents.get(record.get("id", ""))
        if cell and cell.cell_type == "agent":
            live_count = max(
                int(cell.tasks_dispatched or 0),
                len(_current_board_tasks_for_agent(cell.id)),
            )
            record["total_tasks"] = max(
                int(record.get("total_tasks") or 0), live_count)
        return record

    def _save_task_record(task) -> None:
        if not task:
            return
        task.updated_at = datetime.now(timezone.utc).isoformat()
        state._emit("task_upsert", **asdict(task))
        state._db_save_task(task)

    def _boundary_base_branch_for_worktree(repo_root: str, branch: str) -> str:
        if not repo_root or not branch:
            return ""
        return latest_boundary_base_branch(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=branch,
        )

    def _worktree_owner_for_entry(repo_root: str, path: str):
        repo_root = str(repo_root or "").strip()
        path = str(path or "").strip()
        if not repo_root or not path:
            return None
        for agent in state.agents.values():
            if agent.cell_type != "agent":
                continue
            if (agent.worktree_repo_root or agent.git_root or "") != repo_root:
                continue
            if (agent.worktree_path or "") == path:
                return agent
        return None

    async def _classify_repo_worktrees(repo_root: str) -> list[dict]:
        repo_root = str(repo_root or "").strip()
        if not repo_root:
            return []
        entries = await worktree_mgr.list_worktrees(repo_root)
        items: list[dict] = []
        for entry in entries:
            branch = str(entry.get("branch", "") or "").strip()
            path = str(entry.get("path", "") or "").strip()
            is_loom_branch = branch.startswith("loom/")
            owner = _worktree_owner_for_entry(repo_root, path)
            if not is_loom_branch and not owner:
                continue

            exists = bool(path) and os.path.isdir(path)
            admin_stale = bool(entry.get("prunable")) or not exists
            base_branch = (
                str(getattr(owner, "worktree_base_branch", "") or "").strip()
                if owner else ""
            )
            if not base_branch and branch:
                base_branch = _boundary_base_branch_for_worktree(
                    repo_root,
                    branch,
                )

            dirty = False
            merged = False
            if owner:
                dirty = bool(getattr(owner, "worktree_dirty", False))
                if base_branch:
                    merged = bool(getattr(owner, "worktree_merged", False))
            elif exists and base_branch and branch:
                probe = SimpleNamespace(
                    name=branch,
                    worktree_path=path,
                    worktree_repo_root=repo_root,
                    worktree_branch=branch,
                    worktree_base_branch=base_branch,
                )
                dirty = await worktree_mgr.has_uncommitted_changes(probe)
                merged = await worktree_mgr.is_branch_merged(
                    repo_root,
                    branch=branch,
                    base_branch=base_branch,
                )

            prunable = False
            prune_reason = ""
            if owner:
                prune_reason = "owned_by_agent"
            elif admin_stale:
                prunable = True
                prune_reason = "stale_admin"
            elif not base_branch:
                prune_reason = "unknown_base_branch"
            elif dirty:
                prune_reason = "dirty"
            elif not merged:
                prune_reason = "not_merged"
            else:
                prunable = True
                prune_reason = "merged_clean"

            items.append({
                "path": path,
                "branch": branch,
                "branch_ref": str(entry.get("branch_ref", "") or ""),
                "head_sha": str(entry.get("head_sha", "") or ""),
                "base_branch": base_branch,
                "exists": exists,
                "admin_stale": admin_stale,
                "dirty": dirty,
                "merged": merged,
                "prunable": prunable,
                "prune_reason": prune_reason,
                "owner_agent_id": getattr(owner, "id", "") if owner else "",
                "owner_agent_name": getattr(owner, "name", "") if owner else "",
            })
        items.sort(key=lambda item: (item["branch"], item["path"]))
        return items

    def _branch_boundary_tasks_for_cell(cell, statuses: set[str] | None = None
                                        ) -> list:
        repo_root = ""
        if cell:
            repo_root = cell.worktree_repo_root or cell.git_root or ""
        if not cell or not repo_root or not cell.worktree_branch:
            return []
        return branch_boundary_tasks(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=cell.worktree_branch,
            statuses=statuses,
        )

    async def _latest_boundary_state_for_cell(cell) -> dict:
        if not cell or not cell.worktree_path:
            return {"latest": None, "clean": None, "reason": ""}

        latest = latest_boundary_task(
            state.board_tasks.values(),
            repo_root=cell.worktree_repo_root or cell.git_root or "",
            branch=cell.worktree_branch,
            statuses={"open"},
        )
        if not latest:
            return {"latest": None, "clean": None, "reason": ""}

        queued = queued_successor_tasks(state.board_tasks.values(), latest.id)
        started = started_successor_tasks(state.board_tasks.values(), latest.id)
        summary = boundary_summary(
            latest,
            queued_followers=queued,
            started_followers=started,
        )
        summary["clean_mergeable"] = False

        boundary = task_boundary(latest)
        commit_sha = boundary.get("commit_sha", "")
        if not commit_sha:
            summary["reason"] = boundary.get("reason", "") or "missing_commit_sha"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }

        head_sha = await worktree_mgr.current_head(cell)
        summary["head_sha"] = head_sha or ""
        if started:
            summary["reason"] = "started_successor"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        if not head_sha:
            summary["reason"] = "missing_head_sha"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        if head_sha != commit_sha:
            summary["reason"] = "branch_tip_moved"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }

        summary["clean_mergeable"] = True
        return {"latest": summary, "clean": summary, "reason": ""}

    def _boundary_reason_message(reason: str, boundary: dict | None = None) -> str:
        if reason == "started_successor":
            if boundary and boundary.get("started_followers"):
                follower = boundary["started_followers"][0]
                return (
                    "Latest task boundary is no longer cleanly mergeable "
                    f"because follow-up task \"{follower.get('task_title', '')}\""
                    " has already started."
                )
            return (
                "Latest task boundary is no longer cleanly mergeable "
                "because a follow-up task has already started."
            )
        if reason == "branch_tip_moved":
            return (
                "Latest task boundary no longer matches the branch tip. "
                "A newer commit or external rewrite moved the branch."
            )
        if reason == "missing_head_sha":
            return "Cannot verify the current branch tip for the latest task boundary."
        if reason == "missing_commit_sha":
            return "Latest task boundary is missing its recorded commit SHA."
        if boundary:
            task_title = boundary.get("task_title", "")
            if task_title:
                return f"Latest task boundary for \"{task_title}\" is not mergeable."
        return "Latest task boundary is not mergeable."

    def _task_boundary_checkpoint_message(task, cell, message: str) -> str:
        subject = f"loom: task boundary — {task.task[:72]}"
        body_lines = [f"Task: {task.task}"]
        if cell.worktree_branch:
            body_lines.append(f"Branch: {cell.worktree_branch}")
        if message and message.strip() and message.strip() != "Done":
            body_lines.append("")
            body_lines.append(message.strip())
        return subject + "\n\n" + "\n".join(body_lines)

    async def _record_task_boundary(task, cell, message: str = "") -> dict | None:
        if not task or not cell or not cell.worktree_path:
            return None

        dirty = await worktree_mgr.has_uncommitted_changes(cell)
        boundary_sha = ""
        kind = "marker"
        reason = ""
        if dirty:
            boundary_sha = await worktree_mgr.checkpoint(
                cell,
                message=_task_boundary_checkpoint_message(task, cell, message),
            ) or ""
            kind = "checkpoint"
            if not boundary_sha:
                reason = "checkpoint_failed"
        else:
            boundary_sha = await worktree_mgr.current_head(cell) or ""
            if not boundary_sha:
                reason = "missing_head_sha"

        recorded_at = datetime.now(timezone.utc).isoformat()

        for older in _branch_boundary_tasks_for_cell(cell, statuses={"open"}):
            if older.id == task.id:
                continue
            older_boundary = dict(task_boundary(older))
            older_boundary["status"] = "superseded"
            older_boundary["superseded_by_task_id"] = task.id
            older_boundary.pop("reason", None)
            older.worktree_boundary = older_boundary
            _save_task_record(older)

        task.worktree_boundary = {
            "version": "1",
            "branch": cell.worktree_branch or "",
            "repo_root": cell.worktree_repo_root or cell.git_root or "",
            "base_branch": cell.worktree_base_branch or "",
            "commit_sha": boundary_sha,
            "kind": kind,
            "status": "open" if boundary_sha else "invalid",
            "recorded_at": recorded_at,
            "recorded_by_agent_id": cell.id,
            "message": message.strip(),
            "superseded_by_task_id": "",
            "merged_at": "",
            "merge_commit_sha": "",
            "reason": reason,
        }
        _save_task_record(task)

        for queued_task in retarget_queued_successor_tasks(
                state.board_tasks.values(),
                agent_id=cell.id,
                boundary_task_id=task.id,
                exclude_task_id=task.id):
            _save_task_record(queued_task)

        return dict(task.worktree_boundary)

    def _mark_branch_boundaries_merged(cell, merge_sha: str) -> None:
        if not cell:
            return
        repo_root = cell.worktree_repo_root or cell.git_root or ""
        for branch_task in mark_branch_boundaries_merged(
                state.board_tasks.values(),
                repo_root=repo_root,
                branch=cell.worktree_branch or "",
                merge_sha=merge_sha):
            _save_task_record(branch_task)

    # -- Postscript builder -------------------------------------------------

    def _build_postscript(task, amgr, base_dir="", is_clean=True,
                          cell=None):
        """Build the loom-ai instruction block appended to dispatch prompts.

        Only shows commands relevant to the action's transitions.
        ``done``, ``blocked``, and ``error`` are always included.
        ``derive`` only appears when the action declares transitions.
        ``ask`` only appears when an ``ask`` transition exists.

        When ``is_clean`` is False (agent already has context from prior
        tasks), emits an abbreviated version with derive/ask commands
        if the action has transitions.
        """
        # Resolve transitions for this action
        transitions = []
        if task.action_name:
            transitions = amgr.get_transitions(task.action_name,
                                               base_dir)

        commit_hint = ""
        if (cell and cell.worktree_branch
                and not cell.worktree_auto_checkpoint
                and not cell.checkpoint_on_progress):
            commit_hint = ("Before reporting done, commit all your "
                           "changes with a descriptive commit message.")

        # Pipeline context for derived tasks
        pipeline_context = ""
        if task.parent_task_id:
            max_d = state.global_settings.max_pipeline_depth or "∞"
            parent = state.board_tasks.get(task.parent_task_id)
            root = state.board_tasks.get(task.pipeline_root_id)
            ctx = (f"This task is part of a pipeline "
                   f"(depth {task.pipeline_depth}/{max_d}).")
            if parent:
                p_agent = ""
                if parent.agent_id:
                    a = state.agents.get(parent.agent_id)
                    if a:
                        p_agent = f", agent: {a.slug or a.name}"
                p_status = ""
                if parent.status:
                    p_status = f", status: {parent.status}"
                ctx += (f"\nParent task: \"{parent.task[:80]}\" "
                        f"({parent.lane}{p_status}{p_agent})")
            if root and root.id != (parent.id if parent else ""):
                ctx += f"\nRoot task: \"{root.task[:80]}\""
            pipeline_context = ctx

        return build_dispatch_postscript(
            transitions=transitions,
            is_clean=is_clean,
            commit_hint=commit_hint,
            pipeline_context=pipeline_context,
        )

    # -- Command handler ----------------------------------------------------

    async def handle_command(data: dict) -> dict | None:
        """Handle a command, return a direct-response dict or None.

        Direct-response commands (get_config, get_group_settings,
        worktree_history) return immediately without broadcasting.
        Mutation commands broadcast state to all WS clients and
        optionally return a result dict. This HTTP command surface is
        operated by the user and intentionally trusted; MCP tool
        surfaces enforce architect/engineer/worker communication scope.
        """
        cmd = data.get("cmd")
        log.info("CMD %s %s", cmd,
                 {k: v for k, v in data.items() if k != "cmd"})

        # get_config: respond directly, no state mutation
        if cmd == "get_config":
            profile_names = await bridge.list_profiles()
            ctx = await bridge.get_launch_context()
            current_path = ctx.current_path
            current_profile = ctx.current_profile

            group = data.get("group", "")
            group_cells = []
            for aid in state.groups.get(group, []):
                c = state.agents.get(aid)
                if c and c.session_id and c.current_path:
                    group_cells.append({
                        "id": c.id, "name": c.name,
                        "current_path": c.current_path,
                    })

            gs = state.get_group_settings(group)
            resolved_defaults = template_mgr.resolve_agent_config(
                "", gs, {}, base_dir=current_path or await _resolve_base_dir(group))
            return {
                "type": "config",
                "profiles": profile_names,
                "current_path": current_path,
                "current_profile": current_profile,
                "group_cells": group_cells,
                "group_settings": asdict(gs),
                "resolved_agent_defaults": resolved_defaults,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(current_path
                                                          or await _resolve_base_dir(group)),
                "playbooks": state.list_playbooks(group=group,
                                                   status="published",
                                                   limit=200),
                "runtime": _runtime_payload(),
            }

        # get_group_settings: respond directly, no state mutation
        if cmd == "get_group_settings":
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            pnames = await bridge.list_profiles()
            base_dir = await _resolve_base_dir(group)
            return {
                "type": "group_settings",
                "group": group,
                "settings": asdict(gs),
                "weaver_settings": asdict(state.get_weaver_settings(group)),
                "resolved_agent_defaults": template_mgr.resolve_agent_config(
                    "", gs, {}, base_dir=base_dir),
                "profiles": pnames,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(base_dir),
                "actions": action_mgr.list_actions(base_dir),
                "playbooks": state.list_playbooks(group=group,
                                                   status="published",
                                                   limit=200),
                "runtime": _runtime_payload(),
            }

        # get_global_settings: respond directly
        if cmd == "get_global_settings":
            return {
                "type": "global_settings",
                "settings": asdict(state.global_settings),
                "keybinding_defaults": keybindings.get_default_bindings(),
            }

        if cmd == "doctor":
            return _handle_doctor_command(db)

        # get_events: paginated event log query
        if cmd == "get_events":
            before_id = int(data.get("before_id", 0))
            limit = min(int(data.get("limit", 50)), 200)
            events = panel_log.get_page(limit=limit, before_id=before_id)
            return {"type": "events_page", "events": events}

        if cmd == "get_cell_events":
            cell_id = str(data.get("cell_id", "") or "")
            limit = min(int(data.get("limit", 200)), 200)
            cell = state.agents.get(cell_id)
            if not cell:
                return {"type": "error", "message": "Cell not found"}
            events = get_cell_event_stream(
                cell,
                event_log,
                panel_log=panel_log,
                db=db,
                limit=limit,
            )
            return {
                "type": "cell_events",
                "cell_id": cell_id,
                "events": events,
            }

        # Agent history queries
        if cmd == "get_agent_history":
            status_filter = data.get("status", "")
            limit = min(int(data.get("limit", 50)), 200)
            offset = int(data.get("offset", 0))
            records = db.load_agent_history(
                status_filter=status_filter, limit=limit, offset=offset)
            records = [_enrich_history_record(r) for r in records]
            return {"type": "agent_history_list",
                    "records": records}

        if cmd == "get_agent_history_detail":
            agent_id = data.get("agent_id", "")
            if not agent_id:
                return {"type": "error",
                        "message": "agent_id required"}
            record = db.load_agent_history_detail(agent_id)
            if not record:
                return {"type": "error",
                        "message": "Agent not found in history"}
            record = _enrich_history_record(record)
            tasks = db.load_agent_tasks(agent_id)
            if not tasks:
                tasks = _current_board_tasks_for_agent(agent_id)
            messages = db.load_agent_messages(
                agent_id,
                limit=int(data.get("message_limit", 100)))
            return {"type": "agent_history_detail",
                    "record": record,
                    "tasks": tasks,
                    "messages": messages}

        if cmd == "get_playbook_candidates":
            limit = min(int(data.get("limit", 50)), 200)
            return {
                "type": "playbook_candidates",
                "group": data.get("group", ""),
                "candidates": state.list_playbook_candidates(
                    group=data.get("group", ""), limit=limit),
            }

        if cmd == "extract_playbook_candidates":
            return {
                "type": "playbook_candidates",
                "group": data.get("group", ""),
                "candidates": state.extract_playbook_candidates(
                    group=data.get("group", "")),
            }

        if cmd == "get_playbooks":
            limit = min(int(data.get("limit", 50)), 200)
            return {
                "type": "playbooks",
                "group": data.get("group", ""),
                "status": data.get("status", ""),
                "playbooks": state.list_playbooks(
                    group=data.get("group", ""),
                    status=data.get("status", ""),
                    limit=limit,
                ),
            }

        if cmd == "get_playbook":
            playbook_id = data.get("id", "")
            if not playbook_id:
                return {"type": "error", "message": "id required"}
            playbook = state.get_playbook(playbook_id)
            if not playbook:
                return {"type": "error", "message": "Playbook not found"}
            return {"type": "playbook_detail", "playbook": playbook}

        if cmd == "generate_playbook_draft":
            candidate_id = data.get("candidate_id", "")
            if not candidate_id:
                return {"type": "error",
                        "message": "candidate_id required"}
            candidate = db.load_playbook_candidate(candidate_id)
            if not candidate:
                return {"type": "error",
                        "message": "Playbook candidate not found"}
            base_dir = await _resolve_base_dir(
                data.get("group", "") or candidate.get("group", ""))
            from .playbooks import build_playbook_draft

            draft = build_playbook_draft(
                candidate, action_mgr, template_mgr, base_dir=base_dir)
            existing = state.get_playbook(draft["id"])
            if existing and existing.get("status") == "published":
                draft["created_at"] = existing.get("created_at",
                                                    draft["created_at"])
                draft["published_at"] = existing.get("published_at")
                draft["status"] = existing.get("status", "published")
            state.save_playbook(draft)
            return {"type": "playbook_detail", "playbook": draft}

        if cmd == "publish_playbook_draft":
            playbook_id = data.get("id", "")
            if not playbook_id:
                return {"type": "error", "message": "id required"}
            playbook = state.get_playbook(playbook_id)
            if not playbook:
                return {"type": "error", "message": "Playbook not found"}
            if playbook.get("status") != "draft":
                return {"type": "error",
                        "message": "Only draft playbooks can be published"}
            preview = playbook.get("publication_preview", {})
            if not preview.get("ready_to_publish", False):
                return {"type": "error",
                        "message": "Draft is missing required action "
                                   "or template references"}
            from .playbooks import publish_playbook_record

            published = publish_playbook_record(playbook)
            state.save_playbook(published)
            return {"type": "playbook_detail", "playbook": published}

        if cmd == "discard_playbook_draft":
            playbook_id = data.get("id", "")
            if not playbook_id:
                return {"type": "error", "message": "id required"}
            playbook = state.get_playbook(playbook_id)
            if not playbook:
                return {"type": "error", "message": "Playbook not found"}
            if playbook.get("status") != "draft":
                return {"type": "error",
                        "message": "Only draft playbooks can be discarded"}
            from .playbooks import discard_playbook_record

            discarded = discard_playbook_record(playbook)
            state.save_playbook(discarded)
            return {"type": "playbook_detail", "playbook": discarded}

        # list_actions: respond directly
        if cmd == "list_actions":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions", "group": data.get("group", ""),
                    "actions": actions}

        role_template_result = await _handle_role_template_command(
            data, template_mgr, _resolve_base_dir)
        if role_template_result is not None:
            return role_template_result

        if cmd == "get_template":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "")
            tpl = template_mgr.load_template(
                data.get("name", ""), base_dir, scope=scope)
            if not tpl:
                return {"type": "error",
                        "message": f"Template \"{data['name']}\" not found"}
            return {"type": "template_detail", "name": data["name"],
                    "template": tpl}

        if cmd == "render_template":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            rendered = template_mgr.resolve_agent_config(
                data.get("name", ""), gs, data.get("overrides", {}),
                base_dir=base_dir)
            return {
                "type": "template_rendered",
                "name": data.get("name", ""),
                "config": rendered,
            }

        # get_action: respond directly
        if cmd == "get_action":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "")
            # Scope-aware loading: search only the target directory
            raw = None
            if scope == "user":
                gdir = os.path.expanduser("~/.loom/actions")
                for suffix in ("", ".yaml", ".yml"):
                    p = os.path.join(gdir, data["name"] + suffix)
                    if os.path.isfile(p):
                        with open(p) as f:
                            raw = f.read()
                        break
            if raw is None:
                raw = action_mgr._load_raw(data["name"], base_dir)
            if not raw:
                return {"type": "error",
                        "message": f"Action \"{data['name']}\" not found"}
            # Editor mode: parse raw YAML without Jinja2 rendering
            from .actions import parse_yaml
            try:
                act = parse_yaml(raw) or {}
            except Exception:
                act = {}
            avars = action_mgr.get_action_vars(raw)
            return {"type": "action_detail", "name": data["name"],
                    "action": act, "vars": avars}

        # render_action: render action prompt without creating an agent
        if cmd == "render_action":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            raw = action_mgr._load_raw(data["name"], base_dir)
            if not raw:
                return {"type": "error",
                        "message": f"Action \"{data['name']}\" not found"}
            variables = data.get("vars", {})
            rendered = action_mgr.render_action(raw, variables)
            return {"type": "action_rendered",
                    "name": data["name"],
                    "prompt": rendered.get("prompt", ""),
                    "group": rendered.get("group", ""),
                    "labels": rendered.get("labels", [])}

        # save_action: write action YAML to disk
        if cmd == "save_action":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Action name required"}
            act_data = data.get("action", {})
            # Validate {{ TASK }} in prompt
            prompt = act_data.get("prompt", "")
            if not action_mgr.validate_prompt(prompt):
                return {"type": "error",
                        "message": "Action prompt must contain {{ TASK }}"}
            # Reject 'loom' as a variable name (reserved namespace)
            avars = action_mgr.get_action_vars(prompt)
            for av in avars:
                if av.get("name") == "loom":
                    return {"type": "error",
                            "message": "'loom' is a reserved variable "
                                       "name"}
            scope = data.get("scope", "project")  # "project" or "user"
            base_dir = await _resolve_base_dir(data.get("group", ""))

            if scope == "user":
                tdir = os.path.expanduser("~/.loom/actions")
                os.makedirs(tdir, exist_ok=True)
            else:
                tdir = action_mgr.find_actions_dir(base_dir)
                if not tdir:
                    d = base_dir or os.getcwd()
                    tdir = os.path.join(d, ".loom", "actions")
                    os.makedirs(tdir, exist_ok=True)
            # Rename or scope change: delete old file from any location
            old_name = data.get("old_name", "")
            if old_name:
                for old_dir in action_mgr.find_actions_dirs(base_dir):
                    for suffix in (".yaml", ".yml"):
                        old_path = os.path.join(old_dir, old_name + suffix)
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                            break
            path = os.path.join(tdir, name + ".yaml")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            yaml_text = _action_to_yaml(name, act_data)
            with open(path, "w") as f:
                f.write(yaml_text)
            # Return updated list
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions",
                    "group": data.get("group", ""),
                    "actions": actions,
                    "saved": name}

        # delete_action: remove action file from disk
        if cmd == "delete_action":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Action name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            deleted = False
            for tdir in action_mgr.find_actions_dirs(base_dir):
                for suffix in (".yaml", ".yml"):
                    path = os.path.join(tdir, name + suffix)
                    if os.path.isfile(path):
                        os.remove(path)
                        deleted = True
                        break
                if deleted:
                    break
            if not deleted:
                return {"type": "error",
                        "message": f"Action \"{name}\" not found"}
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions",
                    "group": data.get("group", ""),
                    "actions": actions,
                    "deleted": name}

        # -- Mutation commands: broadcast state at the end --
        result = None
        try:
            if cmd == "refresh":
                pass

            elif cmd == "resync":
                # Client detected a sequence gap — send full snapshot
                return await _state_payload()

            elif cmd == "add_group":
                group_name = data["group"]
                state.add_group(group_name)
                default_directory = (data.get("default_directory") or "").strip()
                if default_directory and group_name in state.groups:
                    state.update_group_settings(
                        group_name, default_directory=default_directory
                    )

            elif cmd == "update_group_settings":
                settings = data.get("settings", {})
                state.update_group_settings(data["group"], **settings)

            elif cmd == "update_global_settings":
                settings = data.get("settings", {})
                old_kb = state.global_settings.keybindings.copy()
                state.update_global_settings(**settings)
                new_kb = state.global_settings.keybindings
                if new_kb != old_kb and _should_install_keybindings() and keybindings:
                    _displaced[0] = await keybindings.reinstall(
                        connection, _displaced[0],
                        overrides=new_kb or None)
                # Propagate max_event_log to panel log
                new_max = state.global_settings.max_event_log
                if panel_log._max_size != new_max:
                    panel_log._max_size = new_max
                    panel_log._events = deque(
                        panel_log._events, maxlen=new_max)
                    if panel_log._db:
                        panel_log._db.trim_panel_events(new_max)

            elif cmd == "suspend_keybindings" and _should_install_keybindings() and keybindings:
                await keybindings.remove(connection, _displaced[0])

            elif cmd == "resume_keybindings" and _should_install_keybindings() and keybindings:
                _kb_overrides = (state.global_settings.keybindings
                                 or None)
                _displaced[0] = await keybindings.reinstall(
                    connection, _displaced[0],
                    overrides=_kb_overrides)

            elif cmd == "remove_group":
                removed = state.remove_group(data["group"])
                for c in removed:
                    if c.session_id:
                        await bridge.close_session(c.session_id)
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                        if hasattr(adapter, "uninstall_mcp_config"):
                            adapter.uninstall_mcp_config(
                                os.path.expanduser(c.directory))
                        adapter.uninstall_persistent_prompt(
                            os.path.expanduser(c.directory),
                            _persistent_prompt_filename(c))
                    event_bus.cleanup_cell(c.id)
                    worktree_mgr.forget_refresh_state(c.id)
                    await _safe_remove_worktree(c)

            elif cmd == "rename_group":
                state.rename_group(data["group"], data["new_name"])

            elif cmd == "add_engineer":
                result = await _handle_add_engineer_command(
                    data,
                    state,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_weaver_launch_config=_resolve_weaver_launch_config,
                    create_agent_with_config=_create_agent_with_config,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "add_architect":
                result = await _handle_add_architect_command(
                    data,
                    state,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_weaver_launch_config=_resolve_weaver_launch_config,
                    create_agent_with_config=_create_agent_with_config,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "add_worker":
                result = await _handle_add_worker_command(
                    data,
                    state,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    create_agent_with_config=_create_agent_with_config,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "architect_engineer_hire":
                result = await _handle_architect_engineer_hire_command(
                    data,
                    state,
                )

            elif cmd == "pending_hire_approve":
                result = await _handle_pending_hire_approve_command(
                    data,
                    state,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_weaver_launch_config=_resolve_weaver_launch_config,
                    create_agent_with_config=_create_agent_with_config,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "pending_hire_reject":
                result = await _handle_pending_hire_reject_command(
                    data,
                    state,
                )

            elif cmd == "pending_hire_list":
                result = _handle_pending_hire_list_command(data, state)

            elif cmd in {"engineer_dismiss", "architect_engineer_dismiss"}:
                result = await _handle_engineer_dismiss_command(
                    data,
                    state,
                    close_session=bridge.close_session,
                    panel_event=_panel_event,
                )

            elif cmd in {"engineer_rehire", "architect_engineer_rehire"}:
                result = await _handle_engineer_rehire_command(
                    data,
                    state,
                    bridge=bridge,
                    worktree_mgr=worktree_mgr,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    resolve_weaver_launch_config=_resolve_weaver_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_weaver=_is_designated_weaver,
                    panel_event=_panel_event,
                )

            elif cmd == "delete_engineer":
                result = await _handle_delete_engineer_command(
                    data,
                    state,
                    close_agent_session_only=_close_agent_session_only,
                )

            elif cmd == "delete_architect":
                result = await _handle_delete_architect_command(
                    data,
                    state,
                    close_agent_session_only=_close_agent_session_only,
                )

            elif cmd == "rename_engineer":
                result = await _handle_rename_engineer_command(
                    data,
                    state,
                    update_session=bridge.update_session,
                )

            elif cmd in {
                    "architect_decision_create",
                    "architect_decision_update",
                    "architect_decision_link",
                    "architect_decision_list",
            }:
                result = await _dispatch_architect_ui_tool(
                    cmd,
                    data,
                    state,
                )

            elif cmd == "add_agent":
                group = data["group"]
                is_weaver = data.get("is_weaver", False)

                # Enforce one weaver per group
                if is_weaver:
                    gs_check = state.get_group_settings(group)
                    if gs_check.weaver_agent_id:
                        existing = state.agents.get(
                            gs_check.weaver_agent_id)
                        ename = existing.name if existing else "unknown"
                        result = {
                            "type": "error",
                            "message": (
                                f"Group '{group}' already has a "
                                f"weaver: {ename}")}
                        # Skip agent creation — jump to broadcast
                        is_weaver = False
                        data = {}  # prevent fallthrough

                if data:
                    base_dir = await _resolve_base_dir(group)
                    explicit_template = data.get("template", "").strip()
                    _overrides = dict(data)
                    resolver = (
                        _resolve_weaver_launch_config
                        if is_weaver else _resolve_agent_launch_config
                    )
                    launch_cfg = resolver(
                        group,
                        base_dir=base_dir,
                        explicit_template=explicit_template,
                        overrides=_overrides,
                    )

                    persistent_prompt_text = ""
                    # Weaver: build persistent prompt and skip worktree
                    if is_weaver:
                        from .weaver import build_weaver_system_prompt
                        ws = state.get_weaver_settings(group)
                        action_sp = launch_cfg.get("system_prompt", "")
                        persistent_prompt_text = build_weaver_system_prompt(
                            group, ws, action_sp,
                            group_settings=state.get_group_settings(group))
                        launch_cfg["worktree"] = False
                    startup_prompt = _startup_prompt_for_new_agent(
                        agent_type=launch_cfg.get("agent_type", ""),
                        persistent_prompt_text=persistent_prompt_text,
                        is_weaver=is_weaver,
                    )

                    name = (data.get("name", "") or "").strip()
                    if not name:
                        if is_weaver:
                            name = "Weaver"
                        elif explicit_template:
                            name = _suggest_template_agent_name(
                                group, explicit_template, base_dir)
                        else:
                            name = state.next_cell_name(group, "agent")
                    cell = await _create_agent_with_config(
                        group, name, launch_cfg,
                        explicit_template=explicit_template,
                        target_session_id=data.get(
                            "target_session_id", ""),
                        target_window_id=data.get(
                            "target_window_id", ""),
                        persistent_prompt_text=persistent_prompt_text)
                    if cell:
                        # Designate as weaver
                        if is_weaver:
                            state.update_group_settings(
                                group, weaver_agent_id=cell.id)
                            # Reorder now that weaver_agent_id is set
                            # (the reorder in create_session ran too early)
                            await bridge.reorder_tabs()

                        if launch_cfg.get("terminals"):
                            await _create_child_terminals(
                                group, cell,
                                terminals=launch_cfg["terminals"])
                        else:
                            gs = state.get_group_settings(group)
                            if gs.auto_terminals > 0:
                                await _create_child_terminals(
                                    group, cell,
                                    count=gs.auto_terminals)
                        if cell.session_id:
                            for prompt_text, send_kwargs in \
                                    _new_agent_prompt_sequence(
                                        launch_cfg,
                                        startup_prompt=startup_prompt,
                                        cell=cell):
                                await _send_agent_prompt(
                                    cell, prompt_text, **send_kwargs)

            elif cmd == "add_terminal":
                group = data.get("group", "")
                parent_id = data.get("parent_id", "")
                # Resolve group from parent if needed
                resolve_group = group
                if parent_id:
                    p = state.agents.get(parent_id)
                    if p:
                        resolve_group = p.group
                gs = state.get_group_settings(resolve_group or group)
                profile = data.get("profile") or gs.terminal_profile or gs.profile or "Default"
                parent_wt = p.worktree_path if parent_id and p and p.worktree_path else ""
                directory = data.get("directory") or parent_wt or gs.terminal_directory or gs.default_directory or ""
                _tc = gs.terminal_tab_color
                tab_color = data.get("tab_color") or (_tc if _tc != "none" else "") or gs.tab_color or ""
                shell = data.get("shell") or gs.terminal_shell or gs.shell or ""
                env = {**gs.env_vars, **gs.terminal_env_vars, **(data.get("env_vars") or {})} or None
                command = data.get("command") or gs.terminal_boot_command or ""
                cmd_args = data.get("command_args") or gs.terminal_command_args or ""
                if cmd_args and command:
                    command = (command + " " + cmd_args).strip()
                init_script = data.get("init_script") or gs.terminal_init_script or ""
                env_file = data.get("env_file") or gs.terminal_env_file or gs.env_file

                cell = state.add_terminal(
                    name=data["name"], group=group,
                    terminal_backend="pty" if bridge.capabilities.supports_embedded_terminal else "iterm2",
                    profile=profile, command=command,
                    directory=directory, tab_color=tab_color,
                    parent_id=parent_id,
                )
                if cell:
                    await bridge.create_session(
                        cell, env_vars=env,
                        env_file=env_file,
                        init_script=init_script,
                        shell=shell)

            elif cmd == "remove_agent":
                cell = state.agents.get(data["id"])
                removed = []
                if cell:
                    if str(getattr(cell, "kind", "") or "").strip() == "architect":
                        result = await _handle_delete_architect_command(
                            {"id": cell.id},
                            state,
                            close_agent_session_only=_close_agent_session_only,
                        )
                    else:
                        removed = await _close_agent_session_only(cell)
                for c in removed:
                    await _safe_remove_worktree(c)

            elif cmd == "update_agent":
                cell = state.agents.get(data["id"])
                if cell:
                    old_name = cell.name
                    new_name = data.get("name", cell.name)
                    new_color = data.get("tab_color", cell.tab_color)
                    new_icon = data.get("icon", cell.icon)
                    state.update_agent(data["id"], name=new_name,
                                       tab_color=new_color,
                                       icon=new_icon)
                    if new_name != old_name and cell.cell_type == "agent":
                        state.history_update_agent(
                            cell, name=new_name, slug=cell.slug)
                    if cell.session_id:
                        await bridge.update_session(cell, old_name)

            elif cmd == "focus_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.session_id:
                    await bridge.focus_session(cell.session_id)

            elif cmd == "send_text":
                cell = state.agents.get(data["id"])
                if cell and cell.session_id:
                    cell.status = "running"
                    state.mark_agent_progress(cell, emit=False)
                    state._emit_agent(cell)
                await _queue_cell_prompt_send(
                    cell,
                    data.get("text", ""),
                    _send_agent_prompt,
                )

            elif cmd == "send_user_message":
                await _handle_send_user_message_command(data, state, bridge)

            elif cmd == "broadcast_to_group":
                for aid in state.groups.get(data["group"], []):
                    cell = state.agents.get(aid)
                    if cell and cell.session_id:
                        cell.status = "running"
                        state.mark_agent_progress(cell, emit=False)
                        state._emit_agent(cell)
                    await _queue_cell_prompt_send(
                        cell,
                        data.get("text", ""),
                        _send_agent_prompt,
                    )
                    # Also send to child terminals
                    for child_id in state._children.get(aid, []):
                        child = state.agents.get(child_id)
                        if child and child.session_id:
                            child.status = "running"
                            state.mark_agent_progress(child, emit=False)
                            state._emit_agent(child)
                        await _queue_cell_prompt_send(
                            child,
                            data.get("text", ""),
                            _send_agent_prompt,
                        )

            elif cmd == "relaunch_agent":
                result = await _handle_relaunch_agent_command(
                    data,
                    state,
                    bridge=bridge,
                    worktree_mgr=worktree_mgr,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    resolve_weaver_launch_config=_resolve_weaver_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_weaver=_is_designated_weaver,
                )

            elif cmd == "restart_agent":
                result = await _handle_restart_agent_command(
                    data,
                    state,
                    bridge=bridge,
                    worktree_mgr=worktree_mgr,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    resolve_weaver_launch_config=_resolve_weaver_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_weaver=_is_designated_weaver,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "move_group":
                state.move_group(data["group"], data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "move_agent":
                state.move_agent(data["id"], data["target_group"],
                                 data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "reparent_terminal":
                state.reparent_terminal(data["id"],
                                        data.get("parent_id", ""))
                await bridge.reorder_tabs()

            elif cmd == "reorder_child":
                state.reorder_child(data["id"], data["parent_id"],
                                    data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "clear_agent_context":
                cell = state.agents.get(data.get("id", ""))
                if cell and cell.session_id and cell.cell_type == "agent":
                    if cell.agent_type in ("claude-code", "codex"):
                        await bridge.send_text(
                            cell.session_id, "/clear\r")
                    cell.tasks_dispatched = 0
                    cell.agent_session_id = ""
                    cell.current_task_id = ""
                    cell.mcp_messages = []
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_create":
                cell = state.agents.get(data["id"])
                if cell and not cell.worktree_path and cell.directory:
                    gs = state.get_group_settings(cell.group)
                    repo_root = await worktree_mgr.get_repo_root(
                        cell.directory)
                    if repo_root:
                        wt_path = await worktree_mgr.create(
                            cell, repo_root,
                            base_dir=cell.worktree_base_dir
                                or ".loom/worktrees",
                            base_branch=cell.worktree_base_branch
                                or gs.worktree_base_branch or "",
                            symlinks=gs.worktree_symlinks,
                            state=state,
                        )
                        if wt_path:
                            cell.directory = wt_path
                            state._emit_agent(cell)
                            state._db_save_agent(cell)
                            # Relaunch if requested by the UI
                            if data.get("relaunch"):
                                if cell.session_id:
                                    await bridge.close_session(
                                        cell.session_id)
                                cell.status = "stopped"
                                cell.session_id = None
                                # Clear session ID — the old session
                                # may not exist (no prompts sent yet)
                                cell.agent_session_id = ""
                                base_dir = cell.worktree_repo_root \
                                    or cell.directory \
                                    or await _resolve_base_dir(cell.group)
                                launch_cfg = _resolve_agent_launch_config(
                                    cell.group,
                                    base_dir=base_dir,
                                    explicit_template=cell.template,
                                    overrides={},
                                )
                                if cell.agent_type:
                                    get_adapter(cell.agent_type) \
                                        .uninstall_persistent_prompt(
                                            os.path.expanduser(repo_root),
                                            _persistent_prompt_filename(cell))
                                _apply_persistent_prompt(
                                    cell, launch_cfg,
                                    _build_cell_persistent_prompt(
                                        cell, launch_cfg))
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                                await bridge.create_session(
                                    cell,
                                    env_vars=runtime_env_vars_for_cell(
                                        cell, launch_cfg.get("env_vars")),
                                    env_file=launch_cfg.get("env_file", ""),
                                    shell=launch_cfg.get("shell", ""),
                                    system_prompt=launch_cfg.get(
                                        "system_prompt", ""),
                                    mcp_entrypoint=mcp_entrypoint_for_cell(
                                        cell),
                                    target_session_id=data.get(
                                        "target_session_id", ""),
                                    target_window_id=data.get(
                                        "target_window_id", ""))

            elif cmd == "worktree_remove":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    # Restore directory to original repo root
                    repo_root = cell.worktree_repo_root
                    await _safe_remove_worktree(cell)
                    if repo_root:
                        cell.directory = repo_root
                    # Relaunch if requested by the UI
                    if data.get("relaunch") and cell.cell_type == "agent":
                        await _relaunch_agent_after_worktree_removal(
                            cell,
                            bridge=bridge,
                            state=state,
                            resolve_base_dir=_resolve_base_dir,
                            resolve_agent_launch_config=_resolve_agent_launch_config,
                            resolve_weaver_launch_config=_resolve_weaver_launch_config,
                            is_designated_weaver=_is_designated_weaver,
                            apply_persistent_prompt=_apply_persistent_prompt,
                            build_cell_persistent_prompt=_build_cell_persistent_prompt,
                        )
                    else:
                        state._emit_agent(cell)
                        state._db_save_agent(cell)

            elif cmd == "worktree_list":
                requested_root = str(data.get("repo_root", "") or "").strip()
                repo_root = (
                    await worktree_mgr.get_repo_root(requested_root)
                    if requested_root else None
                ) or requested_root
                if not repo_root or not os.path.isdir(repo_root):
                    result = {
                        "type": "error",
                        "message": "Valid repo_root required for worktree list.",
                    }
                else:
                    items = await _classify_repo_worktrees(repo_root)
                    result = {
                        "type": "worktree_list",
                        "repo_root": repo_root,
                        "items": items,
                        "prunable_count": sum(
                            1 for item in items if item.get("prunable")
                        ),
                    }
                    return result

            elif cmd == "worktree_prune":
                requested_root = str(data.get("repo_root", "") or "").strip()
                repo_root = (
                    await worktree_mgr.get_repo_root(requested_root)
                    if requested_root else None
                ) or requested_root
                if not repo_root or not os.path.isdir(repo_root):
                    result = {
                        "type": "error",
                        "message": "Valid repo_root required for worktree prune.",
                    }
                else:
                    items = await _classify_repo_worktrees(repo_root)
                    candidates = [item for item in items if item.get("prunable")]
                    removed = []
                    skipped = []
                    admin_candidates = [
                        item for item in candidates if item.get("admin_stale")
                    ]
                    for item in candidates:
                        if item.get("admin_stale"):
                            continue
                        ok = await worktree_mgr.remove_path(
                            repo_root,
                            item.get("path", ""),
                            branch=item.get("branch", ""),
                            name=item.get("branch", "") or item.get("path", ""),
                        )
                        if ok:
                            removed.append({
                                "path": item.get("path", ""),
                                "branch": item.get("branch", ""),
                                "prune_reason": item.get("prune_reason", ""),
                            })
                        else:
                            skipped.append({
                                "path": item.get("path", ""),
                                "branch": item.get("branch", ""),
                                "prune_reason": "remove_failed",
                            })

                    prune_ran = False
                    if admin_candidates or removed:
                        prune_ran = await worktree_mgr.prune_admin(repo_root)

                    remaining = await _classify_repo_worktrees(repo_root)
                    remaining_keys = {
                        (item.get("path", ""), item.get("branch", ""))
                        for item in remaining
                    }
                    for item in admin_candidates:
                        key = (item.get("path", ""), item.get("branch", ""))
                        if key not in remaining_keys:
                            removed.append({
                                "path": item.get("path", ""),
                                "branch": item.get("branch", ""),
                                "prune_reason": item.get("prune_reason", ""),
                            })
                        else:
                            skipped.append({
                                "path": item.get("path", ""),
                                "branch": item.get("branch", ""),
                                "prune_reason": "stale_admin_not_pruned",
                            })

                    result = {
                        "type": "worktree_prune",
                        "repo_root": repo_root,
                        "removed": removed,
                        "skipped": skipped,
                        "remaining": remaining,
                        "prune_ran": prune_ran,
                    }
                    return result

            elif cmd == "worktree_checkpoint":
                cell = state.agents.get(data["id"])
                block_reason = _shared_review_checkpoint_block_reason(
                    state,
                    cell,
                )
                if block_reason:
                    result = {"type": "error", "message": block_reason}
                elif cell and cell.worktree_path:
                    msg = _checkpoint_message(cell)
                    await worktree_mgr.checkpoint(cell, message=msg)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_history":
                cell = state.agents.get(data.get("id", ""))
                commits = []
                if cell and cell.worktree_path:
                    commits = await worktree_mgr.list_checkpoints(cell)
                return {
                    "type": "worktree_history",
                    "id": data.get("id", ""),
                    "branch": cell.worktree_branch if cell else "",
                    "base_branch": cell.worktree_base_branch
                    if cell else "",
                    "commits": commits,
                }

            elif cmd == "worktree_diff_full":
                cell = state.agents.get(data.get("id", ""))
                result = await _worktree_full_diff(cell, worktree_mgr)
                if cell and cell.worktree_path:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    result["boundary"] = boundary_state.get("latest")
                    result["clean_boundary"] = boundary_state.get("clean")
                result["type"] = "worktree_diff_full"
                result["id"] = data.get("id", "")
                return result

            elif cmd == "worktree_check_merge":
                cell = state.agents.get(data.get("id", ""))
                aid = data.get("id", "")
                if cell and cell.worktree_path \
                        and cell.worktree_branch:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    dirty = await worktree_mgr.has_uncommitted_changes(
                        cell)
                    if dirty:
                        result = {
                            "type": "worktree_check_merge",
                            "id": aid, "clean": False,
                            "dirty": True, "conflicts": [],
                            "boundary": boundary_state.get("latest"),
                            "clean_boundary": boundary_state.get("clean"),
                        }
                    elif boundary_state.get("latest") \
                            and not boundary_state.get("clean"):
                        result = {
                            "type": "worktree_check_merge",
                            "id": aid,
                            "clean": False,
                            "dirty": False,
                            "conflicts": [],
                            "boundary": boundary_state.get("latest"),
                            "clean_boundary": None,
                            "error": _boundary_reason_message(
                                boundary_state.get("reason", ""),
                                boundary_state.get("latest"),
                            ),
                        }
                    else:
                        stale_base = await worktree_mgr.stale_base_info(cell)
                        if stale_base.get("stale") \
                                and not data.get("allow_stale_base"):
                            result = _stale_base_check_merge_result(
                                aid, stale_base
                            )
                            result["boundary"] = boundary_state.get("latest")
                            result["clean_boundary"] = boundary_state.get("clean")
                            return result
                        check = await \
                            worktree_mgr.check_merge_conflicts(cell)
                        if check.get("clean"):
                            overwrite_paths = (
                                await worktree_mgr.merge_untracked_overwrite_paths(
                                    cell.worktree_repo_root
                                    or cell.git_root
                                    or "",
                                    cell.worktree_base_branch or "",
                                    check.get("tree_sha", ""),
                                )
                            )
                            if overwrite_paths:
                                check["clean"] = False
                                check["tree_sha"] = ""
                                check["conflicts"] = []
                                check["error"] = _untracked_overwrite_message(
                                    overwrite_paths,
                                    operation="merge",
                                    location="the checked-out base repo",
                                )
                                check["overwrite_paths"] = overwrite_paths
                        check["type"] = "worktree_check_merge"
                        check["id"] = aid
                        check["boundary"] = boundary_state.get("latest")
                        check["clean_boundary"] = boundary_state.get("clean")
                        _attach_stale_base(check, stale_base)
                        if check.get("clean"):
                            squash = cell.worktree_merge_squash
                            check["default_message"] = \
                                await _generate_merge_message(
                                    cell, worktree_mgr, squash,
                                    state=state)
                        result = check
                else:
                    result = {
                        "type": "worktree_check_merge",
                        "id": data.get("id", ""),
                        "error": "No worktree",
                    }
                return result

            elif cmd == "worktree_rebase":
                cell = state.agents.get(data.get("id", ""))
                aid = data.get("id", "")
                if cell and cell.worktree_path:
                    overwrite_paths = (
                        await worktree_mgr.rebase_untracked_overwrite_paths(cell)
                    )
                    if overwrite_paths:
                        result = {
                            "type": "worktree_rebase",
                            "id": aid,
                            "ok": False,
                            "error": _untracked_overwrite_message(
                                overwrite_paths,
                                operation="rebase",
                                location="the worktree",
                            ),
                            "overwrite_paths": overwrite_paths,
                            "conflicts": [],
                        }
                    elif await worktree_mgr.has_uncommitted_changes(cell):
                        result = {
                            "type": "worktree_rebase",
                            "id": aid,
                            "ok": False,
                            "error": "Worktree has uncommitted changes. "
                                     "Create a checkpoint or commit them "
                                     "before rebasing.",
                            "conflicts": [],
                        }
                    else:
                        stale_base_before_rebase = {}
                        stale_info = getattr(
                            worktree_mgr, "stale_base_info", None)
                        if callable(stale_info):
                            try:
                                stale_base_before_rebase = await stale_info(cell)
                            except Exception:
                                log.exception(
                                    "stale-base preflight failed before rebase "
                                    "for '%s'",
                                    cell.name,
                                )
                        check = await worktree_mgr.check_merge_conflicts(cell)
                        previous_head_sha = (
                            await worktree_mgr.current_head(cell) or ""
                        )
                        ok = await worktree_mgr.rebase_onto_base(cell)
                        if ok:
                            rebased_head_sha = (
                                await worktree_mgr.current_head(cell) or ""
                            )
                            dirty_after_rebase = (
                                await worktree_mgr.has_uncommitted_changes(cell)
                            )
                            refreshed_boundary = None
                            if not dirty_after_rebase:
                                refreshed_boundary = (
                                    refresh_latest_boundary_after_rebase(
                                        state.board_tasks.values(),
                                        repo_root=(
                                            cell.worktree_repo_root
                                            or cell.git_root
                                            or ""
                                        ),
                                        branch=cell.worktree_branch or "",
                                        previous_head_sha=previous_head_sha,
                                        rebased_head_sha=rebased_head_sha,
                                    )
                                )
                            if refreshed_boundary:
                                _save_task_record(refreshed_boundary)
                            cell.worktree_checkpoints = \
                                await worktree_mgr.count_commits(cell)
                            cell.worktree_dirty = dirty_after_rebase
                            cell.worktree_diff = {}
                            cell.worktree_changed_files = []
                            state._emit_agent(cell)
                            result = {"type": "worktree_rebase",
                                      "id": aid, "ok": True}
                            breach_event = (
                                _emit_stale_base_catch_workflow_breach(
                                    state,
                                    _panel_event,
                                    cell,
                                    stale_base_before_rebase,
                                )
                            )
                            if breach_event:
                                result["workflow_breach"] = breach_event
                        else:
                            result = {
                                "type": "worktree_rebase",
                                "id": aid, "ok": False,
                                "error": "Rebase failed — conflicts "
                                         "require manual resolution",
                                "conflicts": check.get("conflicts", []),
                            }
                else:
                    result = {"type": "worktree_rebase",
                              "id": aid, "error": "No worktree"}

            elif cmd == "worktree_rollback":
                cell = state.agents.get(data.get("id", ""))
                sha = data.get("sha", "")
                if cell and cell.worktree_path and sha:
                    await worktree_mgr.rollback(cell, sha)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_diff":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "error",
                              "message": "Agent has no worktree."}
                elif not cell.worktree_base_branch:
                    result = {"type": "error",
                              "message": "No base branch configured."}
                else:
                    import asyncio as _aio
                    stat_only = data.get("stat_only", False)
                    summary_only = data.get("summary_only", False)
                    paths = data.get("paths", [])
                    stale_base = await worktree_mgr.stale_base_info(cell)
                    if summary_only:
                        summary = await worktree_mgr.diff_files_summary(
                            cell,
                            paths=paths,
                        )
                        result = {
                            "type": "ok",
                            "summary": {
                                "agent_name": cell.name,
                                "branch": cell.worktree_branch or "",
                                "base_branch": cell.worktree_base_branch,
                                "path_filters": paths,
                                **summary,
                            },
                        }
                        _attach_stale_base(result, stale_base)
                        if stale_base.get("stale"):
                            result["summary"]["stale_base"] = stale_base
                            result["summary"]["stale_base_warning"] = (
                                result.get("stale_base_warning", "")
                            )
                    else:
                        diff_args = [
                            "git", "-C", cell.worktree_path,
                            "diff",
                        ]
                        if stat_only:
                            diff_args.append("--stat")
                        diff_args.append(
                            f"{cell.worktree_base_branch}...HEAD")
                        if paths:
                            diff_args.append("--")
                            diff_args.extend(paths)
                        proc = await _aio.create_subprocess_exec(
                            *diff_args,
                            stdout=_aio.subprocess.PIPE,
                            stderr=_aio.subprocess.PIPE,
                        )
                        stdout, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            result = {"type": "error",
                                      "message": stderr.decode().strip()
                                      or "git diff failed"}
                        else:
                            diff_text = stdout.decode()
                            warning = _stale_base_warning(stale_base)
                            if warning:
                                diff_text = f"{warning}\n\n{diff_text}"
                            # Truncate if too large (100K chars)
                            if len(diff_text) > 100_000:
                                diff_text = (
                                    diff_text[:100_000]
                                    + "\n\n... truncated (too large) ..."
                                )
                            result = {"type": "ok",
                                      "diff": diff_text}
                            _attach_stale_base(result, stale_base)

            elif cmd == "worktree_check_conflicts":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "error",
                              "message": "Agent has no worktree."}
                else:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    if boundary_state.get("latest") \
                            and not boundary_state.get("clean"):
                        result = {
                            "type": "ok",
                            "clean": False,
                            "conflicts": [],
                            "error": _boundary_reason_message(
                                boundary_state.get("reason", ""),
                                boundary_state.get("latest"),
                            ),
                            "boundary": boundary_state.get("latest"),
                        }
                    else:
                        conflict_info = \
                            await worktree_mgr.check_merge_conflicts(cell)
                        result = {
                            "type": "ok",
                            "clean": conflict_info.get("clean", True),
                            "conflicts": conflict_info.get(
                                "conflicts", []),
                            "boundary": boundary_state.get("latest"),
                            "clean_boundary": boundary_state.get("clean"),
                        }

            elif cmd == "worktree_create_pr":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "worktree_pr",
                              "error": "Agent has no worktree."}
                else:
                    # Build PR title from linked task or agent name
                    title = data.get("title", "")
                    if not title:
                        for t in state.board_tasks.values():
                            if t.agent_id == cell.id:
                                title = t.task
                                break
                    if not title:
                        title = cell.name
                    body = data.get("body", "")
                    pr_result = await worktree_mgr.create_pr(
                        cell, title=title, body=body)
                    if "error" in pr_result:
                        result = {"type": "worktree_pr",
                                  "error": pr_result["error"]}
                    else:
                        msg = ("PR already exists"
                               if pr_result.get("existing")
                               else "PR created")
                        result = {"type": "worktree_pr",
                                  "url": pr_result["url"],
                                  "message": msg}

            elif cmd == "worktree_merge":
                cell = state.agents.get(data.get("id", ""))
                aid = data.get("id", "")
                if cell and cell.worktree_path and cell.worktree_branch:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    # Block merge if worktree has uncommitted changes
                    dirty = await worktree_mgr.has_uncommitted_changes(
                        cell)
                    if dirty:
                        result = {
                            "type": "worktree_merge", "id": aid,
                            "ok": False,
                            "error": "Commit or checkpoint changes "
                                     "before merging.",
                        }
                    elif boundary_state.get("latest") \
                            and not boundary_state.get("clean"):
                        result = {
                            "type": "worktree_merge",
                            "id": aid,
                            "ok": False,
                            "error": _boundary_reason_message(
                                boundary_state.get("reason", ""),
                                boundary_state.get("latest"),
                            ),
                        }
                    else:
                        stale_base = await worktree_mgr.stale_base_info(cell)
                        if stale_base.get("stale") \
                                and not data.get("force_stale_base"):
                            result = _stale_base_merge_result(
                                aid, stale_base
                            )
                            return result
                        precheck = await worktree_mgr.check_merge_conflicts(
                            cell
                        )
                        if not precheck.get("clean"):
                            result = {
                                "type": "worktree_merge",
                                "id": aid,
                                "ok": False,
                                "error": precheck.get(
                                    "error", "Conflicts detected"
                                ),
                            }
                            _attach_stale_base(result, stale_base)
                        else:
                            overwrite_paths = (
                                await worktree_mgr.merge_untracked_overwrite_paths(
                                    cell.worktree_repo_root
                                    or cell.git_root
                                    or "",
                                    cell.worktree_base_branch or "",
                                    precheck.get("tree_sha", ""),
                                )
                            )
                            if overwrite_paths:
                                result = {
                                    "type": "worktree_merge",
                                    "id": aid,
                                    "ok": False,
                                    "error": _untracked_overwrite_message(
                                        overwrite_paths,
                                        operation="merge",
                                        location="the checked-out base repo",
                                    ),
                                    "overwrite_paths": overwrite_paths,
                                }
                            else:
                                squash = cell.worktree_merge_squash
                                msg = data.get("message", "").strip()
                                if not msg:
                                    msg = await _generate_merge_message(
                                        cell, worktree_mgr, squash,
                                        state=state)
                                preserve_merge_diff = (
                                    _worktree_merge_preserve_diff_enabled(
                                        state,
                                        cell,
                                        data,
                                    )
                                )
                                boundary_task_for_diff = None
                                merge_diff_snapshot = None
                                if preserve_merge_diff:
                                    boundary_task_for_diff = (
                                        _latest_open_boundary_task_for_cell(
                                            state,
                                            cell,
                                        )
                                    )
                                    if boundary_task_for_diff:
                                        merge_diff_snapshot = (
                                            await _worktree_merge_diff_snapshot(
                                                cell,
                                                worktree_mgr,
                                            )
                                        )
                                merge_result = \
                                    await worktree_mgr.server_merge(
                                        cell, msg, squash=squash)
                                if merge_result["ok"]:
                                    _mark_branch_boundaries_merged(
                                        cell, merge_result["sha"]
                                    )
                                    state.cleanup_stale_boundary_successors()
                                    preserve_diff_warning = ""
                                    if preserve_merge_diff:
                                        preserve_diff_warning = (
                                            _persist_preserved_merge_diff_warning_only(
                                                state,
                                                cell,
                                                boundary_task_for_diff,
                                                merge_diff_snapshot,
                                                merge_commit_sha=merge_result["sha"],
                                            )
                                        )
                                    cell.worktree_checkpoints = 0
                                    cell.worktree_merged = True
                                    cell.worktree_changed_files = []
                                    state.history_update_agent(
                                        cell, status="merged")
                                    state._emit_agent(cell)
                                    await _broadcast_toast(
                                        f'"{cell.name}" merged to '
                                        f"{cell.worktree_base_branch}",
                                        "success")
                                    if preserve_diff_warning:
                                        await _broadcast_toast(
                                            preserve_diff_warning,
                                            "warning",
                                        )
                                    reviewer_cleanup = (
                                        await _cleanup_shipped_reviewers_for_merged_cell(
                                            state,
                                            cell,
                                            _cleanup_after_merge,
                                        )
                                    )
                                    # Unlink completed/archive-closed tasks from this agent so
                                    # they don't re-appear in future merge
                                    # messages.  Tasks stay on the board as a
                                    # historical record.
                                    for t in list(
                                            state.board_tasks.values()):
                                        if t.agent_id == cell.id \
                                                and task_is_closed(t):
                                            t.agent_id = ""
                                            state._emit(
                                                "task_upsert", **asdict(t))
                                            state._db_save_task(t)

                                    legacy_close_flag = bool(
                                        data.get("close_on_merge"))
                                    explicit_close = (
                                        "close_agent_on_merge" in data
                                    )
                                    explicit_remove = (
                                        "remove_worktree_on_merge" in data
                                    )
                                    if explicit_close or explicit_remove:
                                        close_flag = bool(
                                            data.get("close_agent_on_merge"))
                                        remove_flag = bool(
                                            data.get("remove_worktree_on_merge"))
                                    elif legacy_close_flag:
                                        close_flag = True
                                        remove_flag = True
                                    else:
                                        close_flag, remove_flag = (
                                            merge_cleanup_flags(
                                                state.get_group_settings(
                                                    cell.group
                                                ).worktree_merge_cleanup
                                            )
                                        )
                                    queued_followups = [
                                        t for t in state.board_tasks.values()
                                        if t.agent_id == cell.id
                                        and t.lane in {"Backlog", "To Do"}
                                    ]
                                    if queued_followups:
                                        close_flag = False
                                        remove_flag = False
                                    clear_flag = bool(
                                        data.get("clear_context"))
                                    if queued_followups or close_flag \
                                            or remove_flag:
                                        clear_flag = False
                                    cleanup = {
                                        "close_agent": close_flag,
                                        "remove_worktree": remove_flag,
                                        "agent_closed": False,
                                        "worktree_removed": False,
                                        "errors": [],
                                    }
                                    if clear_flag and not close_flag \
                                            and not remove_flag \
                                            and cell.session_id:
                                        await bridge.send_text(
                                            cell.session_id, "/clear\r")
                                        cell.tasks_dispatched = 0
                                        state._emit_agent(cell)
                                        state._db_save_agent(cell)
                                        log.info(
                                            "Cleared context for '%s' "
                                            "after merge", cell.name)
                                    if close_flag or remove_flag:
                                        cleanup = await _cleanup_after_merge(
                                            cell,
                                            close_agent=close_flag,
                                            remove_worktree=remove_flag,
                                        )
                                    elif cell.worktree_path:
                                        # Reset worktree branch to base tip
                                        # so new work starts fresh (avoids
                                        # re-merging already-merged commits)
                                        valid = await \
                                            worktree_mgr.validate(cell)
                                        if valid:
                                            ok = await worktree_mgr\
                                                .reset_to_base(cell)
                                            if ok:
                                                cell.worktree_checkpoints =\
                                                    await worktree_mgr\
                                                    .count_commits(cell)
                                                cell.worktree_dirty = False
                                                cell.worktree_diff = {}
                                                if queued_followups:
                                                    cell.worktree_merged = False
                                                state._emit_agent(cell)
                                            else:
                                                log.warning(
                                                    "Post-merge reset "
                                                    "failed for '%s'",
                                                    cell.name)
                                    if reviewer_cleanup.get("agents"):
                                        cleanup["reviewer_cleanup"] = (
                                            reviewer_cleanup
                                        )
                                        cleanup["errors"].extend(
                                            reviewer_cleanup.get(
                                                "errors", []
                                            )
                                        )
                                    result = {
                                        "type": "worktree_merge",
                                        "id": aid, "ok": True,
                                        "sha": merge_result["sha"],
                                        "cleanup": cleanup,
                                    }
                                    _attach_stale_base(result, stale_base)
                                else:
                                    result = {
                                        "type": "worktree_merge",
                                        "id": aid, "ok": False,
                                        "error": merge_result.get(
                                            "error", "Merge failed"),
                                    }
                                    _attach_stale_base(result, stale_base)
                else:
                    result = {
                        "type": "worktree_merge", "id": aid,
                        "ok": False,
                        "error": "Agent has no worktree.",
                    }

            # -- Board commands (Phase 5) --
            elif cmd == "board_add_task":
                # Apply per-group board defaults for fields not
                # explicitly provided by the client
                group = data.get("group", "")
                gs = state.get_group_settings(group)
                lane = data.get("lane", "") or gs.board_default_lane
                action_name = data.get("action_name", "") or \
                    gs.board_default_action
                labels = data.get("labels", [])
                if not labels and gs.board_default_labels:
                    labels = list(gs.board_default_labels)
                ext_link = normalize_external_link(
                    data.get("provider", ""),
                    data.get("external_id", ""),
                    data.get("external_url", ""),
                )
                add_kwargs = dict(
                    task=data.get("task", ""),
                    group=group,
                    lane=lane,
                    description=data.get("description", ""),
                    action_name=action_name,
                    action_vars=data.get("action_vars", {}),
                    agent_template=data.get("agent_template", ""),
                    agent_id=data.get("agent_id", ""),
                    labels=labels,
                    provider=ext_link["provider"],
                    external_id=ext_link["external_id"],
                    external_url=ext_link["external_url"],
                    depends_on=data.get("depends_on", []),
                    scheduled_at=data.get("scheduled_at", ""),
                    assigned_engineer_id=data.get("assigned_engineer_id", ""),
                    created_by_engineer_id=data.get("created_by_engineer_id", ""),
                    verification_mode=data.get("verification_mode", ""),
                    verification_state=data.get("verification_state", ""),
                    verification_notes=data.get("verification_notes", ""),
                    verification_updated_at=data.get(
                        "verification_updated_at", ""),
                    verification_updated_by=data.get(
                        "verification_updated_by", ""),
                    verification_summary=data.get(
                        "verification_summary", {}),
                )
                # Pass client-provided ID (for pre-uploaded attachments)
                draft_upload_id = ""
                incoming_id = str(data.get("id", "") or "").strip()
                if incoming_id:
                    if is_draft_task_token(incoming_id) or (
                        not is_canonical_task_id(incoming_id)
                    ):
                        draft_upload_id = incoming_id
                    else:
                        add_kwargs["id"] = incoming_id
                # Attachments from client (already uploaded to disk)
                if data.get("attachments"):
                    add_kwargs["attachments"] = data["attachments"]
                if data.get("artifacts"):
                    add_kwargs["artifacts"] = data["artifacts"]
                bt = state.board_add_task(**add_kwargs)
                if not bt:
                    result = {"type": "error",
                              "message": "Invalid lane, group, or empty task"}
                else:
                    if draft_upload_id:
                        attachments, artifacts = finalize_task_attachments(
                            bt.attachments,
                            bt.artifacts,
                            draft_task_id=draft_upload_id,
                            task_id=bt.id,
                        )
                        state.board_update_task(
                            bt.id,
                            attachments=attachments,
                            artifacts=artifacts,
                        )
                        bt = state.board_tasks.get(bt.id, bt)
                    result = {
                        "type": "external_imported" if bt.external_id
                        or bt.external_url else "board_task_added",
                        "task_id": bt.id,
                        "title": bt.task,
                    }

            elif cmd == "board_archive_task":
                result = _handle_board_archive_command(state, data)

            elif cmd == "board_unarchive_task":
                result = _handle_board_unarchive_command(state, data)

            elif cmd == "board_update_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                _update_task = state.board_tasks.get(tid)
                _update_resume_targets = _capture_auto_resume_targets(
                    state,
                    task=_update_task,
                    group=_update_task.group if _update_task else "",
                )
                fields = {k: v for k, v in data.items()
                          if k not in ("cmd", "id")}
                if {"provider", "external_id", "external_url"} & set(fields):
                    link = normalize_external_link(
                        fields.get("provider", ""),
                        fields.get("external_id", ""),
                        fields.get("external_url", ""),
                    )
                    fields["provider"] = link["provider"]
                    fields["external_id"] = link["external_id"]
                    fields["external_url"] = link["external_url"]
                state.board_update_task(tid, **fields)
                # Auto-dispatch if agent_id was set and agent is idle
                _new_aid = fields.get("agent_id", "")
                if _new_aid:
                    _tsk = state.board_tasks.get(tid)
                    _cell = state.agents.get(_new_aid)
                    if (_tsk and _cell
                            and _tsk.lane == "To Do"
                            and not state.agent_is_busy(_new_aid)
                            and _cell.cell_type == "agent"
                            and state.board_deps_met(_tsk)):
                        await handle_command({
                            "cmd": "dispatch_task",
                            "id": tid, "agent_id": _new_aid})
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=_update_resume_targets,
                    group=_update_task.group if _update_task else "",
                )

            elif cmd == "board_verify_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error", "message": "Task not found"}
                else:
                    resume_targets = _capture_auto_resume_targets(
                        state,
                        task=task,
                        group=task.group,
                    )
                    actor_name = str(
                        data.get("actor_name", "") or "loom"
                    ).strip()

                    def _save_verified_task(current_task):
                        current_task.updated_at = datetime.now(
                            timezone.utc
                        ).isoformat()
                        state._emit("task_upsert", **asdict(current_task))
                        state._db_save_task(current_task)

                    root_task = None
                    root_id = task.pipeline_root_id or ""
                    if root_id and root_id != task.id:
                        root_task = state.board_tasks.get(root_id)
                    payload = {}
                    for key in (
                        "verification_mode",
                        "verification_state",
                        "verification_notes",
                        "tests_run",
                        "manual_smoke_done",
                        "deploy_needed",
                        "deploy_attempted",
                        "human_validation_pending",
                        "smoke_status",
                    ):
                        if key in data:
                            payload[key] = data[key]
                    verify_msg, _updated_root = _apply_verification_report(
                        task,
                        payload,
                        actor_name,
                        _save_verified_task,
                        root_task=root_task,
                    )
                    _panel_event(
                        "task_verification_updated",
                        "",
                        actor_name,
                        task.group,
                        verify_msg,
                        task_id=task.id,
                    )
                    state.recompute_task_health()
                    result = {
                        "type": "verification_updated",
                        "task_id": task.id,
                        "message": verify_msg,
                    }
                    await _maybe_auto_resume_targets(
                        state,
                        handle_command,
                        _panel_event,
                        targets=resume_targets,
                        group=task.group,
                    )

            elif cmd == "workflow_breach":
                result = _handle_workflow_breach_command(
                    data,
                    state,
                    _panel_event,
                )

            elif cmd == "external_import_task":
                group = data.get("group", "")
                lane = data.get("lane", "") or "Backlog"
                labels = data.get("labels", [])
                try:
                    # `import_external_ticket` may shell out to the `gh`
                    # CLI for GitHub tickets (sync subprocess.run). Offload
                    # it to a thread so the event loop keeps serving the UI.
                    imported = await asyncio.to_thread(
                        import_external_ticket,
                        data.get("ref", ""),
                        provider=data.get("provider", ""),
                        title=data.get("title", ""),
                        description=data.get("description", ""),
                        external_id=data.get("external_id", ""),
                        external_url=data.get("external_url", ""),
                    )
                    bt = state.board_add_task(
                        task=imported.title,
                        group=group,
                        lane=lane,
                        description=imported.description,
                        labels=labels,
                        provider=imported.provider,
                        external_id=imported.external_id,
                        external_url=imported.external_url,
                    )
                    if not bt:
                        result = {"type": "error",
                                  "message": "Invalid group, lane, or task"}
                    else:
                        result = {
                            "type": "external_imported",
                            "task_id": bt.id,
                            "title": bt.task,
                            "provider": bt.provider,
                            "external_id": bt.external_id,
                            "external_url": bt.external_url,
                        }
                except ExternalTicketError as exc:
                    result = {"type": "error", "message": str(exc)}

            elif cmd == "external_link_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    link = normalize_external_link(
                        data.get("provider", ""),
                        data.get("external_id", ""),
                        data.get("external_url", ""),
                        ref=data.get("ref", ""),
                    )
                    state.board_update_task(
                        tid,
                        provider=link["provider"],
                        external_id=link["external_id"],
                        external_url=link["external_url"],
                    )
                    result = {
                        "type": "external_unlinked"
                        if not link["provider"]
                        and not link["external_id"]
                        and not link["external_url"]
                        else "external_linked",
                        "task_id": tid,
                        **link,
                    }

            elif cmd == "external_open_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    try:
                        url = open_ticket_url(
                            task.provider,
                            task.external_id,
                            task.external_url,
                        )
                        result = {
                            "type": "external_open",
                            "task_id": tid,
                            "url": url,
                        }
                    except ExternalTicketError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "external_push_task_status":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    try:
                        pushed = push_ticket_status(
                            task,
                            status=data.get("status", "") or task.status
                            or task.lane,
                            note=data.get("note", ""),
                        )
                        task.messages.append({
                            "timestamp": time.time(),
                            "action": "external_status",
                            "message": pushed,
                            "agent_name": "loom",
                        })
                        state.board_update_task(tid, messages=task.messages)
                        result = {
                            "type": "external_status_pushed",
                            "task_id": tid,
                            "message": pushed,
                        }
                    except ExternalTicketError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "external_post_task_comment":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    try:
                        comment = (data.get("comment", "") or "").strip()
                        if not comment:
                            comment = build_completion_comment(
                                task.task,
                                data.get("summary", ""),
                            )
                        posted = post_ticket_comment(task, comment=comment)
                        task.messages.append({
                            "timestamp": time.time(),
                            "action": "external_comment",
                            "message": posted,
                            "agent_name": "loom",
                        })
                        state.board_update_task(tid, messages=task.messages)
                        result = {
                            "type": "external_comment_posted",
                            "task_id": tid,
                            "message": posted,
                        }
                    except ExternalTicketError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "board_remove_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                state.board_remove_task(tid)
                # Clean up attachment files
                att_dir = ATTACHMENTS_DIR / tid
                if att_dir.is_dir():
                    shutil.rmtree(att_dir, ignore_errors=True)

            elif cmd == "board_archive_task":
                result = _handle_board_archive_command(state, data)

            elif cmd == "board_unarchive_task":
                result = _handle_board_unarchive_command(state, data)

            elif cmd == "remove_attachment":
                tid = _resolve_task_id(state, data.get("task_id", ""))
                fname = data.get("filename", "")
                task = state.board_tasks.get(tid)
                if fname:
                    fpath = ATTACHMENTS_DIR / tid / fname
                    if fpath.is_file():
                        fpath.unlink()
                if task and fname:
                    task.attachments = [
                        a for a in task.attachments
                        if a.get("filename") != fname]
                    task.artifacts = remove_task_owned_artifacts_by_filename(
                        task.artifacts,
                        fname,
                        task_id=tid,
                    )
                    state.board_update_task(
                        tid,
                        attachments=task.attachments,
                        artifacts=task.artifacts,
                    )

            elif cmd == "task_upload_artifact":
                tid = _resolve_task_id(state, data.get("task_id", ""))
                cell_id = data.get("cell_id", "")
                if not tid and cell_id:
                    current_task = state.agent_current_task(cell_id)
                    if current_task:
                        tid = current_task.id
                task = state.board_tasks.get(tid)
                if not task:
                    result = {
                        "type": "error",
                        "message": (
                            "Task not found"
                            if data.get("task_id")
                            else "No active task available for this agent"
                        ),
                    }
                else:
                    actor = state.agents.get(cell_id) if cell_id else None
                    provenance = {
                        "source": (
                            "weaver"
                            if actor and actor.id == state.get_group_settings(
                                task.group
                            ).weaver_agent_id
                            else "agent"
                        ),
                        "agent_id": actor.id if actor else "",
                        "agent_name": (actor.slug or actor.name) if actor else "",
                    }
                    try:
                        artifact = store_task_upload(
                            task_id=tid,
                            local_path=data.get("local_path", ""),
                            filename=data.get("filename", ""),
                            content_base64=data.get("content_base64", ""),
                            content_text=data.get("content_text", ""),
                            artifact_type=data.get("artifact_type", ""),
                            title=data.get("title", ""),
                            mime_type=data.get("mime_type", ""),
                            summary=data.get("summary", ""),
                            prompt_mode=data.get("prompt_mode", ""),
                            provenance=provenance,
                        )
                    except FileNotFoundError as exc:
                        result = {"type": "error", "message": str(exc)}
                    except ValueError as exc:
                        result = {"type": "error", "message": str(exc)}
                    else:
                        artifacts = normalize_artifacts(task.artifacts or [])
                        artifacts.append(artifact)
                        state.board_update_task(tid, artifacts=artifacts)
                        refreshed = state.board_tasks.get(tid)
                        serialized_artifact = serialize_task_artifact(
                            artifact,
                            task_id=tid,
                            task_label=(
                                refreshed.task if refreshed else task.task
                            ),
                        )
                        _emit_task_artifact_uploaded_event(
                            _panel_event,
                            refreshed or task,
                            actor,
                            serialized_artifact,
                        )
                        result = {
                            "type": "task_artifact_uploaded",
                            "task_id": tid,
                            "artifact": serialized_artifact,
                        }

            elif cmd == "board_move_task":
                _mv_id = _resolve_task_id(state, data.get("id", ""))
                _mv_task = state.board_tasks.get(_mv_id)
                _mv_resume_targets = _capture_auto_resume_targets(
                    state,
                    task=_mv_task,
                    group=_mv_task.group if _mv_task else "",
                )
                _mv_done_before = task_counts_as_done(_mv_task)
                _mv_new = data.get("lane", "")
                state.board_move_task(
                    _mv_id, _mv_new, data.get("position"))
                _mv_task_after = state.board_tasks.get(_mv_id)
                # Moving out of Done may re-block dependents
                if _mv_done_before and not task_counts_as_done(_mv_task_after):
                    for _dt in state.board_get_dependents(_mv_id):
                        if not task_is_closed(_dt):
                            _panel_event(
                                "task_blocked_by_dep", "",
                                "", _dt.group,
                                f"Task '{_dt.task[:60]}' is "
                                "blocked again (dependency "
                                "moved out of Done)",
                                task_id=_dt.id)
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=_mv_resume_targets,
                    group=_mv_task_after.group if _mv_task_after else "",
                )

            elif cmd == "board_reorder_task":
                state.board_reorder_task(
                    data.get("id", ""),
                    data.get("position", 0))

            elif cmd == "dispatch_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    group = task.group
                    if not group or group not in state.groups:
                        # Fall back to first group
                        group = next(iter(state.groups), "")
                    if not group:
                        result = {"type": "error",
                                  "message": "No group available"}
                    elif not state.board_deps_met(task):
                        unmet = [
                            state.board_tasks[d].task[:40]
                            for d in task.depends_on
                            if d in state.board_tasks
                            and not task_counts_as_done(
                                state.board_tasks[d]
                            )]
                        result = {
                            "type": "error",
                            "message":
                                "Blocked by dependencies: "
                                + ", ".join(unmet)}
                    else:
                        cell = None
                        base_dir = await _resolve_base_dir(group)
                        act_meta = action_mgr.load_action(
                            task.action_name, base_dir) \
                            if task.action_name else None
                        action_template = ""
                        if isinstance(act_meta, dict):
                            raw_agent = act_meta.get("agent", "")
                            if isinstance(raw_agent, str):
                                action_template = raw_agent
                        explicit_template = task.agent_template or action_template
                        agent_id = data.get("agent_id", "")
                        handoff_from = data.get(
                            "handoff_worktree_from", "")
                        dispatch_owner_id = str(
                            data.get("owner_engineer_id", "")
                            or data.get("_created_by_weaver_id", "")
                            or ""
                        ).strip()
                        if agent_id:
                            target_cell = state.agents.get(agent_id)
                            if (
                                target_cell
                                and str(getattr(target_cell, "kind", "") or "").strip()
                                == "engineer"
                                and _agent_dismissed_at(target_cell)
                            ):
                                result = _engineer_dismissed_error(target_cell.id)
                        elif dispatch_owner_id:
                            owner_cell = state.agents.get(dispatch_owner_id)
                            if (
                                owner_cell
                                and str(getattr(owner_cell, "kind", "") or "").strip()
                                == "engineer"
                                and _agent_dismissed_at(owner_cell)
                            ):
                                result = _engineer_dismissed_error(owner_cell.id)
                        else:
                            assigned_engineer_id = str(
                                getattr(task, "assigned_engineer_id", "") or ""
                            ).strip()
                            assigned_engineer = (
                                state.agents.get(assigned_engineer_id)
                                if assigned_engineer_id else None
                            )
                            if _agent_dismissed_at(assigned_engineer):
                                result = _engineer_dismissed_error(
                                    assigned_engineer_id)
                        if agent_id and agent_id not in state.agents:
                            result = {"type": "error",
                                      "message": "Agent not found"}
                        elif result:
                            pass
                        elif agent_id:
                            # Dispatch to existing agent
                            cell = state.agents.get(agent_id)
                            active = state.agent_current_task(cell.id)
                            if active and active.id != tid:
                                allow_self_dispatch = bool(
                                    data.get("_self_dispatch")
                                    and cell.id == agent_id
                                )
                                if _should_queue_existing_agent_dispatch(
                                        active,
                                        target_task_id=tid,
                                        self_dispatch=allow_self_dispatch):
                                    # Agent is busy — queue the task
                                    state.board_update_task(
                                        tid, agent_id=cell.id)
                                    state.board_move_task(tid, "To Do")
                                    queue_cap = (
                                        normalize_default_worker_concurrency(
                                            state.get_weaver_settings(
                                                task.group
                                            ).default_worker_concurrency
                                        )
                                    )
                                    state.auto_dispatch_queue_add(
                                        task.group,
                                        tid,
                                        target_agent_id=cell.id,
                                        max_concurrent=queue_cap,
                                        weaver_owner_id=str(
                                            data.get(
                                                "_weaver_dispatch_id", ""
                                            ) or ""
                                        ),
                                    )
                                    _panel_event(
                                        "task_queued", cell.id,
                                        cell.name, cell.group,
                                        task.task[:80],
                                        task_id=tid)
                                    result = {
                                        "type": "queued",
                                        "task_id": tid,
                                        "agent_id": cell.id}
                                    cell = None  # skip dispatch below
                            if cell and not result \
                                    and not _agent_can_receive_dispatch(cell):
                                result = {
                                    "type": "error",
                                    "message": "Agent is not available",
                                    "agent_id": cell.id,
                                }
                                cell = None
                        elif data.get("create_agent"):
                            # Create a new agent
                            from loom.state import _slugify
                            agent_name = data.get("name", "")
                            if not agent_name:
                                slug = _slugify(task.task)
                                agent_name = slug or "agent"
                            launch_overrides = {}
                            agent_type = (data.get("agent_type", "")
                                          or "").strip()
                            if agent_type:
                                launch_overrides["provider"] = agent_type
                            command_override = (data.get("command", "")
                                                or "").strip()
                            if command_override:
                                launch_overrides["command"] = (
                                    command_override)
                            model_override = (data.get("model", "")
                                              or "").strip()
                            if model_override:
                                launch_overrides["model"] = model_override
                            reasoning_override = (
                                data.get("reasoning_effort", "") or ""
                            ).strip()
                            if reasoning_override:
                                launch_overrides["reasoning_effort"] = (
                                    reasoning_override
                                )
                            launch_cfg = _resolve_agent_launch_config(
                                group,
                                base_dir=base_dir,
                                explicit_template=explicit_template,
                                overrides=launch_overrides,
                            )
                            inherited_worktree_source = None
                            inherit_from = data.get(
                                "inherit_worktree_from", "")
                            if inherit_from:
                                src = state.agents.get(inherit_from)
                                if src and src.worktree_path:
                                    inherited_worktree_source = src
                            elif task.parent_task_id:
                                # HITL dispatch: walk parent chain to find
                                # the worktree before launching the session,
                                # so derived reviewers do not briefly create
                                # and run inside a throwaway branch.
                                _ptid = task.parent_task_id
                                while _ptid:
                                    _pt = state.board_tasks.get(_ptid)
                                    if not _pt:
                                        break
                                    if _pt.agent_id:
                                        _pa = state.agents.get(_pt.agent_id)
                                        if _pa and _pa.worktree_path:
                                            inherited_worktree_source = _pa
                                            break
                                    _ptid = _pt.parent_task_id
                            persistent_prompt_text = ""
                            startup_prompt = ""
                            if launch_cfg.get("agent_type"):
                                persistent_prompt_text = \
                                    _build_dispatch_persistent_prompt(
                                        launch_cfg.get("system_prompt", ""))
                                startup_prompt = _startup_prompt_for_new_agent(
                                    agent_type=launch_cfg.get(
                                        "agent_type", ""),
                                    persistent_prompt_text=
                                    persistent_prompt_text,
                                )
                            cell = await _create_agent_with_config(
                                group, agent_name, launch_cfg,
                                explicit_template=explicit_template,
                                target_session_id=data.get(
                                    "target_session_id", ""),
                                target_window_id=data.get(
                                    "target_window_id", ""),
                                persistent_prompt_text=persistent_prompt_text,
                                created_by_weaver_id=data.get(
                                    "_created_by_weaver_id", ""),
                                owner_engineer_id=data.get(
                                    "owner_engineer_id", ""),
                                kind="worker",
                                inherited_worktree_from=inherited_worktree_source,
                                restore_focus_to_prev_tab=True,
                            )
                            if cell:
                                # Worktree inheritance (pipeline) is applied
                                # before session creation. Re-copy here in
                                # case the source changed while the agent
                                # session was launching.
                                if inherited_worktree_source:
                                    _copy_worktree_context(
                                        cell,
                                        inherited_worktree_source,
                                    )
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)

                                if launch_cfg.get("terminals"):
                                    await _create_child_terminals(
                                        group, cell,
                                        terminals=launch_cfg["terminals"])

                                # Auto-create child terminals (off by default for dispatch)
                                gs = state.get_group_settings(group)
                                if gs.dispatch_auto_terminals \
                                        and gs.auto_terminals > 0:
                                    await _create_child_terminals(
                                        group, cell, count=gs.auto_terminals)

                        if cell and not result \
                                and not _agent_can_receive_dispatch(cell):
                            result = {
                                "type": "error",
                                "message": "Agent is not available",
                                "agent_id": cell.id,
                            }
                            cell = None

                        if cell:
                            final_prompt = ""
                            shared_context_block = build_prompt_memory_block(
                                state.db,
                                cell=cell,
                                task=task,
                            )
                            if data.get("_self_dispatch"):
                                final_prompt = prepend_agent_identity_anchor(
                                    _build_self_dispatch_prompt(
                                        shared_context_block,
                                    ),
                                    cell,
                                )
                            else:
                                # Build loom context for template rendering
                                loom_ctx = _build_loom_context(
                                    state, cell, task)
                                # Compose prompt: action-aware
                                prompt = None
                                base_dir = ""
                                disable_role_preamble = False
                                if task.action_name \
                                        and not data.get("force_no_action"):
                                    base_dir = cell.worktree_repo_root \
                                        or cell.directory \
                                        or await _resolve_base_dir(group)
                                    tvars = {"TASK": task.task,
                                             **(task.action_vars or {})}
                                    rendered_action = action_mgr.render_action(
                                        task.action_name, tvars,
                                        base_dir=base_dir,
                                        loom_context=loom_ctx)
                                    if not rendered_action:
                                        # Action deleted — warn frontend
                                        result = {
                                            "type":
                                                "dispatch_action_missing",
                                            "task_id": tid,
                                            "action_name":
                                                task.action_name}
                                        prompt = None
                                    else:
                                        prompt = rendered_action.get(
                                            "prompt", "")
                                        disable_role_preamble = bool(
                                            rendered_action.get(
                                                "disable_role_preamble",
                                                False,
                                            )
                                        )
                                elif task.instructions or task.context \
                                        or task.criteria:
                                    # Legacy fallback for old tasks
                                    parts = []
                                    if task.task:
                                        parts.append(task.task)
                                    if task.instructions:
                                        parts.append(task.instructions)
                                    if task.context:
                                        parts.append(task.context)
                                    if task.criteria:
                                        parts.append(task.criteria)
                                    prompt = "\n\n".join(parts)
                                else:
                                    prompt = task.task

                                if prompt:
                                    upstream_artifacts = (
                                        loom_ctx["task"]["upstream_artifacts"]
                                    )
                                    prompt = _append_task_artifacts(
                                        prompt,
                                        task.attachments,
                                        task.artifacts,
                                        upstream_artifacts,
                                    )
                                    is_clean = \
                                        loom_ctx["context"]["is_clean"]
                                    prompt += shared_context_block
                                    postscript = _build_postscript(
                                        task, action_mgr,
                                        base_dir if task.action_name
                                        else "",
                                        is_clean=is_clean,
                                        cell=cell)
                                    final_prompt = _assemble_worker_prompt(
                                        role_mgr=template_mgr,
                                        cell=cell,
                                        base_dir=base_dir or (
                                            cell.worktree_repo_root
                                            or cell.directory
                                        ),
                                        prompt_body=prompt,
                                        postscript=postscript,
                                        disable_role_preamble=
                                        disable_role_preamble,
                                    )

                            if not result and not final_prompt:
                                result = {
                                    "type": "error",
                                    "message": "Dispatch prompt unavailable",
                                    "task_id": tid,
                                }

                        if cell and not result:
                            owner = _find_active_worktree_owner(state, cell)
                            if owner:
                                if _should_handoff_shared_worktree(
                                        owner,
                                        target_agent_id=cell.id,
                                        handoff_from=handoff_from):
                                    owner.current_task_id = ""
                                    owner.activity = ""
                                    owner.activity_detail = ""
                                    state._emit_agent(owner)
                                    state._db_save_agent(owner)
                                else:
                                    state.board_update_task(
                                        tid, agent_id=cell.id)
                                    state.board_move_task(tid, "To Do")
                                    _panel_event(
                                        "task_queued", cell.id,
                                        cell.name, cell.group,
                                        f"{task.task[:80]} "
                                        f"(waiting for {owner.name})",
                                        task_id=tid)
                                    result = {
                                        "type": "queued",
                                        "task_id": tid,
                                        "agent_id": cell.id,
                                        "reason":
                                            "shared_worktree_busy",
                                        "blocked_by_agent_id":
                                            owner.id,
                                        "blocked_by_task_id":
                                            (
                                                state.agent_current_task(
                                                    owner.id
                                                ).id
                                                if state.agent_current_task(
                                                    owner.id
                                                ) else ""
                                            ),
                                    }
                                    cell = None
                        if cell and not result:
                            dispatch_lane = \
                                state.get_group_settings(group) \
                                    .dispatch_lane or "In Progress"
                            _record_task_dispatch(
                                cell, task, dispatch_lane)

                            # Track dispatch count after prompt resolution
                            cell.tasks_dispatched += 1
                            state._emit_agent(cell)
                            state._db_save_agent(cell)

                            if agent_id:
                                delay = 3 if data.get(
                                    "_self_dispatch") else 0
                                if delay:
                                    # Self-dispatch: delay so
                                    # prompt arrives after current
                                    # agent turn finishes, then
                                    # re-prime the existing session
                                    # so the minimal follow-up is
                                    # submitted as a real prompt.
                                    await _send_agent_prompt(
                                        cell, final_prompt,
                                        delay=delay,
                                        background=True,
                                        prime_input_ready=True,
                                        settled_submit=True,
                                    )
                                else:
                                    # Existing agent — queue now
                                    await _send_agent_prompt(
                                        cell,
                                        final_prompt,
                                        background=True,
                                    )
                            elif data.get("create_agent"):
                                for prompt_text, send_kwargs in \
                                        _new_agent_prompt_sequence(
                                            launch_cfg,
                                            startup_prompt=
                                            startup_prompt,
                                            final_prompt=final_prompt,
                                            cell=cell):
                                    await _send_agent_prompt(
                                        cell,
                                        prompt_text,
                                        **send_kwargs)

                            state.history_record_dispatch(
                                cell,
                                task,
                                weaver_group=data.get(
                                    "_weaver_dispatch_group",
                                    "",
                                ),
                                weaver_id=data.get(
                                    "_weaver_dispatch_id",
                                    "",
                                ),
                            )
                            _panel_event(
                                "task_dispatched", cell.id,
                                cell.name, cell.group,
                                task.task[:80],
                                task_id=task.id)
                            result = {
                                "type": "ok",
                                "task_id": tid,
                                "agent_id": cell.id,
                            }

            elif cmd == "resolve_ask":
                # Resolve an ask task: send answer to parent's agent
                tid = data.get("id", "")
                answer = data.get("answer", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                elif "loom:human" not in (task.labels or []):
                    result = {"type": "error",
                              "message": "Not an ask task"}
                elif not answer.strip():
                    result = {"type": "error",
                              "message": "Answer is required"}
                elif not task.parent_task_id:
                    result = {"type": "error",
                              "message": "Ask task has no parent"}
                else:
                    parent = state.board_tasks.get(
                        task.parent_task_id)
                    if not parent:
                        result = {"type": "error",
                                  "message": "Parent task not found"}
                    elif not parent.agent_id:
                        result = {"type": "error",
                                  "message": "Parent task has no "
                                             "linked agent"}
                    else:
                        agent = state.agents.get(parent.agent_id)
                        if not agent or not agent.session_id:
                            result = {
                                "type": "error",
                                "message": "Parent agent not "
                                           "available"}
                        else:
                            # Send answer to the parent's agent
                            q = task.task
                            if len(q) > 120:
                                q = q[:120] + "…"
                            msg = (f"Answer to your question "
                                   f"\"{q}\":\n{answer}")
                            await bridge.send_text(
                                agent.session_id,
                                msg + "\r")
                            agent.status = "running"
                            state._emit_agent(agent)

                            # Move ask task to Done (no cascade)
                            from datetime import datetime, timezone
                            if not task_is_closed(task):
                                state.board_move_task(
                                    task.id, "Done")
                            task.status = ""
                            task.updated_at = datetime.now(
                                timezone.utc).isoformat()
                            state._emit("task_upsert",
                                        **asdict(task))
                            state._db_save_task(task)

                            # Clear parent "Awaiting Input" status
                            parent.status = ""
                            parent.updated_at = datetime.now(
                                timezone.utc).isoformat()
                            state._emit("task_upsert",
                                        **asdict(parent))
                            state._db_save_task(parent)

                            # Clear root task status too
                            root_id = parent.pipeline_root_id \
                                or parent.id
                            if root_id != parent.id:
                                root = state.board_tasks.get(
                                    root_id)
                                if root and root.status:
                                    root.status = ""
                                    root.updated_at = datetime.now(
                                        timezone.utc).isoformat()
                                    state._emit("task_upsert",
                                                **asdict(root))
                                    state._db_save_task(root)

                            _panel_event(
                                "ask_resolved", agent.id,
                                agent.name, agent.group,
                                "Resolved: " + q,
                                task_id=tid)
                            result = {"type": "ok",
                                      "task_id": tid}

            elif cmd == "preview_prompt":
                # Preview rendered prompt for a task or inline params
                tid = data.get("id", "")
                act_name = data.get("action_name", "")
                preview_role_slug = str(
                    data.get("agent_template", "") or ""
                ).strip()
                task_text = data.get("task", "")
                avars = data.get("action_vars", {})
                act_group = data.get("group", "")
                attachments = data.get("attachments", [])
                artifacts = normalize_artifacts(data.get("artifacts", []))

                if tid and not task_text:
                    t = state.board_tasks.get(tid)
                    if t:
                        task_text = t.task
                        act_name = act_name or t.action_name
                        preview_role_slug = (
                            preview_role_slug
                            or str(t.agent_template or "").strip()
                        )
                        avars = avars or t.action_vars or {}
                        act_group = act_group or t.group
                        attachments = t.attachments or []
                        artifacts = normalize_artifacts(t.artifacts or [])

                task_desc = data.get("description", "")
                if tid and not task_desc:
                    t = state.board_tasks.get(tid)
                    if t:
                        task_desc = t.description or ""

                preview_task = state.board_tasks.get(tid) if tid else None
                preview_cell = None
                preview_agent_id = str(data.get("agent_id", "") or "").strip()
                if preview_agent_id:
                    preview_cell = state.agents.get(preview_agent_id)
                elif preview_task and preview_task.agent_id:
                    preview_cell = state.agents.get(preview_task.agent_id)
                elif preview_role_slug:
                    preview_cell = SimpleNamespace(
                        id="",
                        name="",
                        slug="",
                        group=act_group,
                        cell_type="agent",
                        agent_type="",
                        directory="",
                        kind="worker",
                        role=preview_role_slug,
                        template=preview_role_slug,
                        owner_engineer_id="",
                        created_by_weaver_id="",
                        worktree_repo_root="",
                        git_root="",
                        worktree_branch="",
                        worktree_auto_checkpoint=False,
                        checkpoint_on_progress=False,
                    )

                preview_task_obj = preview_task or SimpleNamespace(
                    id=tid,
                    task=task_text,
                    slug="",
                    description=task_desc,
                    pipeline_depth=0,
                    parent_task_id=str(data.get("parent_task_id", "") or ""),
                    pipeline_root_id="",
                    labels=[],
                    group=act_group,
                    status="",
                    verification_mode="",
                    verification_state="",
                    verification_notes="",
                    verification_updated_at="",
                    verification_updated_by="",
                    verification_summary={},
                    worktree_boundary={},
                    resume_after_boundary_task_id="",
                    attachments=attachments or [],
                    artifacts=artifacts or [],
                    action_name=act_name,
                    agent_template=preview_role_slug,
                    created_at="",
                    updated_at="",
                    agent_id=preview_agent_id,
                )
                preview_upstream_artifacts = serialize_upstream_task_artifacts(
                    preview_task_obj,
                    tasks_by_id=state.board_tasks,
                )

                if preview_cell:
                    loom_ctx = _build_loom_context(
                        state, preview_cell, preview_task_obj)
                    is_clean = loom_ctx["context"]["is_clean"]
                    shared_context_block = build_prompt_memory_block(
                        state.db,
                        cell=preview_cell,
                        task=preview_task_obj,
                    )
                else:
                    loom_ctx = {
                        **LOOM_CONTEXT_STUB,
                        "task": {
                            **LOOM_CONTEXT_STUB["task"],
                            "title": task_text,
                            "description": task_desc,
                            "group": act_group,
                            "attachments": [
                                {"path": a.get("path", ""),
                                 "filename": a.get("filename", "")}
                                for a in (attachments or [])
                                if isinstance(a, dict)
                            ],
                            "artifacts": task_artifacts(
                                attachments or [],
                                artifacts or [],
                            ),
                            "upstream_artifacts": preview_upstream_artifacts,
                        },
                    }
                    is_clean = True
                    shared_context_block = ""

                base_dir = (
                    preview_cell.worktree_repo_root
                    or preview_cell.directory
                ) if preview_cell else ""
                if not base_dir:
                    base_dir = await _resolve_base_dir(act_group)

                prompt_text = task_text
                disable_role_preamble = False
                if act_name:
                    rendered_action = action_mgr.render_action(
                        act_name,
                        {"TASK": task_text, **avars},
                        base_dir=base_dir,
                        loom_context=loom_ctx)
                    if not rendered_action:
                        result = {"type": "prompt_preview",
                                  "prompt": task_text,
                                  "warning": f"Action "
                                             f"\"{act_name}\" not found"}
                    else:
                        prompt_text = rendered_action.get("prompt", "")
                        disable_role_preamble = bool(
                            rendered_action.get(
                                "disable_role_preamble", False)
                        )

                if result is None:
                    prompt_text = _append_task_artifacts(
                        prompt_text,
                        attachments,
                        artifacts,
                        preview_upstream_artifacts,
                    )
                    prompt_text += shared_context_block
                    postscript = _build_postscript(
                        preview_task_obj,
                        action_mgr,
                        base_dir if act_name else "",
                        is_clean=is_clean,
                        cell=preview_cell,
                    )
                    result = {
                        "type": "prompt_preview",
                        "prompt": _assemble_worker_prompt(
                            role_mgr=template_mgr,
                            cell=preview_cell,
                            base_dir=base_dir,
                            prompt_body=prompt_text,
                            postscript=postscript,
                            disable_role_preamble=disable_role_preamble,
                        ),
                    }

            elif cmd == "memory_list":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    cell, task = _resolve_memory_cell_and_task(
                        state,
                        data.get("cell_id", ""),
                        data.get("task_id", ""),
                    )
                    scope_kind = (data.get("scope_kind", "") or "").strip()
                    try:
                        scope_ref = _resolve_memory_scope_ref(
                            scope_kind,
                            (data.get("scope_ref", "") or "").strip(),
                            cell=cell,
                            task=task,
                        )
                    except ValueError as exc:
                        result = {
                            "type": "error",
                            "message": str(exc),
                        }
                        scope_ref = ""
                    group_name = (data.get("group_name", "") or "").strip()
                    project_key = (data.get("project_key", "") or "").strip()
                    linked_target_kind = (
                        data.get("linked_target_kind", "") or ""
                    ).strip()
                    linked_target_ref = (
                        data.get("linked_target_ref", "") or ""
                    ).strip()
                    if linked_target_kind:
                        try:
                            linked_target_kind = normalize_link_target_kind(
                                linked_target_kind
                            )
                        except ValueError as exc:
                            result = {
                                "type": "error",
                                "message": str(exc),
                            }
                            linked_target_kind = ""
                    if result is None and linked_target_kind:
                        try:
                            linked_target_ref = _resolve_memory_link_ref(
                                linked_target_kind,
                                linked_target_ref,
                                cell=cell,
                                task=task,
                            )
                        except ValueError as exc:
                            result = {
                                "type": "error",
                                "message": str(exc),
                            }
                    if result is None:
                        if not scope_kind and not scope_ref and not group_name:
                            if task:
                                scope_kind = "task"
                                scope_ref = task.id
                            elif cell and cell.group:
                                scope_kind = "group"
                                scope_ref = cell.group
                                group_name = cell.group
                        if not group_name:
                            if task and task.group:
                                group_name = task.group
                            elif scope_kind == "group" and scope_ref:
                                group_name = scope_ref
                            elif cell and cell.group and not project_key:
                                group_name = cell.group
                        search_text = (data.get("search", "") or "").strip()
                        entries = load_visible_memory_entries(
                            state.db,
                            group_name=group_name,
                            project_key=project_key,
                            scope_kind=scope_kind,
                            scope_ref=scope_ref,
                            entry_type=(data.get("entry_type", "") or "").strip(),
                            task_id=_resolve_task_id(
                                state,
                                (data.get("filter_task_id", "") or "").strip()
                            ),
                            pinned_only=bool(data.get("pinned_only", False)),
                            search=search_text,
                            linked_target_kind=linked_target_kind,
                            linked_target_ref=linked_target_ref,
                            limit=int(data.get("limit", 20) or 20),
                            offset=int(data.get("offset", 0) or 0),
                            compact=not bool(search_text),
                        )
                        result = {
                            "type": "memory_entries",
                            "entries": entries,
                            "scope_kind": scope_kind,
                            "scope_ref": scope_ref,
                            "group_name": group_name,
                            "project_key": project_key,
                            "linked_target_kind": linked_target_kind,
                            "linked_target_ref": linked_target_ref,
                        }

            elif cmd == "memory_read":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        result = {"type": "memory_entry", "entry": entry}

            elif cmd == "board_add_lane":
                name = data.get("name", "").strip()
                if not name:
                    result = {"type": "error",
                              "message": "Lane name cannot be empty"}
                else:
                    state.board_add_lane(name, data.get("position"))

            elif cmd == "board_rename_lane":
                state.board_rename_lane(
                    data.get("old_name", ""),
                    data.get("new_name", "").strip())

            elif cmd == "board_remove_lane":
                state.board_remove_lane(
                    data.get("name", ""),
                    data.get("move_tasks_to", ""))

            elif cmd == "board_reorder_lanes":
                state.board_reorder_lanes(data.get("lanes", []))

            elif cmd == "board_set_panel":
                if "active" in data:
                    state.panel_active = str(data["active"])
                    state._emit("ui_update", key="panel_active",
                                value=state.panel_active)
                    state._db_save_ui("panel_active",
                                      state.panel_active)
                elif "open" in data:
                    # Backward compat
                    state.panel_active = "board" if data["open"] \
                        else ""
                    state._emit("ui_update", key="panel_active",
                                value=state.panel_active)
                    state._db_save_ui("panel_active",
                                      state.panel_active)
                if "height" in data:
                    state.board_panel_height = int(data["height"])
                    state._emit("ui_update", key="board_panel_height",
                                value=state.board_panel_height)
                    state._db_save_ui("board_panel_height",
                                      state.board_panel_height)

            elif cmd == "standalone_set_panel_layout":
                layout = data.get("layout", {})
                if not isinstance(layout, dict):
                    result = {
                        "type": "error",
                        "message": "Invalid standalone panel layout",
                    }
                else:
                    state.standalone_panel_layout = layout
                    state._emit(
                        "ui_update",
                        key="standalone_panel_layout",
                        value=state.standalone_panel_layout,
                    )
                    state._db_save_ui(
                        "standalone_panel_layout",
                        json.dumps(state.standalone_panel_layout),
                    )

            elif cmd == "events_dismiss":
                item_id = str(data.get("id", "") or "").strip()
                if not item_id:
                    result = {"type": "error", "message": "Missing event id"}
                else:
                    try:
                        timestamp = float(data.get("timestamp", 0) or 0)
                    except (TypeError, ValueError):
                        timestamp = 0.0
                    if timestamp <= 0:
                        timestamp = time.time()
                    state.events_dismissed_attention[item_id] = timestamp
                    state._emit(
                        "ui_update",
                        key="events_dismissed_attention",
                        value=state.events_dismissed_attention,
                    )
                    state._db_save_ui(
                        "events_dismissed_attention",
                        json.dumps(state.events_dismissed_attention),
                    )

            elif cmd == "board_set_filters":
                raw_filters = data.get("filters_by_group", {})
                if isinstance(raw_filters, dict):
                    state.board_filters_by_group = raw_filters
                else:
                    state.board_filters_by_group = {}
                state._emit("ui_update", key="board_filters_by_group",
                            value=state.board_filters_by_group)
                state._db_save_ui(
                    "board_filters_by_group",
                    json.dumps(state.board_filters_by_group),
                )

            elif cmd == "board_set_saved_views":
                raw_views = data.get("saved_views_by_group", {})
                if isinstance(raw_views, dict):
                    state.board_saved_views_by_group = raw_views
                else:
                    state.board_saved_views_by_group = {}
                state._emit("ui_update", key="board_saved_views_by_group",
                            value=state.board_saved_views_by_group)
                state._db_save_ui(
                    "board_saved_views_by_group",
                    json.dumps(state.board_saved_views_by_group),
                )

            elif cmd == "board_set_lane_sorts":
                raw_sorts = data.get("lane_sorts_by_group", {})
                if isinstance(raw_sorts, dict):
                    state.board_lane_sorts_by_group = raw_sorts
                else:
                    state.board_lane_sorts_by_group = {}
                state._emit("ui_update", key="board_lane_sorts_by_group",
                            value=state.board_lane_sorts_by_group)
                state._db_save_ui(
                    "board_lane_sorts_by_group",
                    json.dumps(state.board_lane_sorts_by_group),
                )

            elif cmd == "board_set_card_density":
                raw_density = data.get("card_density_by_group", {})
                if isinstance(raw_density, dict):
                    state.board_card_density_by_group = raw_density
                else:
                    state.board_card_density_by_group = {}
                state._emit("ui_update", key="board_card_density_by_group",
                            value=state.board_card_density_by_group)
                state._db_save_ui(
                    "board_card_density_by_group",
                    json.dumps(state.board_card_density_by_group),
                )

            # -- Schedule commands ------------------------------------------

            elif cmd == "schedule_create":
                name = data.get("name", "").strip()
                group = data.get("group", "")
                if not name:
                    result = {"type": "error",
                              "message": "Schedule name is required"}
                elif not group or group not in state.groups:
                    result = {"type": "error",
                              "message": "Valid group is required"}
                else:
                    cron_expr = data.get("cron_expr", "")
                    scheduled_at = data.get("scheduled_at", "")
                    tz = data.get("timezone", "")
                    if not cron_expr and not scheduled_at:
                        result = {"type": "error",
                                  "message": "Either cron_expr or "
                                             "scheduled_at is required"}
                    else:
                        if cron_expr:
                            try:
                                from .cron import parse_cron, \
                                    next_run as cron_next
                                parse_cron(cron_expr)
                                from datetime import datetime, \
                                    timezone as dt_tz
                                nxt = cron_next(
                                    cron_expr,
                                    datetime.now(dt_tz.utc), tz=tz)
                                next_run_at = nxt.isoformat()
                            except ValueError as e:
                                result = {
                                    "type": "error",
                                    "message": f"Invalid cron: {e}"}
                                next_run_at = None
                        else:
                            next_run_at = scheduled_at

                        if next_run_at is not None:
                            kwargs = {
                                "task_template":
                                    data.get("task_template", ""),
                                "description":
                                    data.get("description", ""),
                                "action_name":
                                    data.get("action_name", ""),
                                "action_vars":
                                    data.get("action_vars", {}),
                                "agent_template":
                                    data.get("agent_template", ""),
                                "labels": data.get("labels", []),
                                "cron_expr": cron_expr,
                                "scheduled_at": scheduled_at,
                                "timezone": tz,
                                "next_run_at": next_run_at,
                                "enabled":
                                    data.get("enabled", True),
                            }
                            sched = state.schedule_add(
                                name, group, **kwargs)
                            if sched:
                                result = {"type": "ok",
                                          "schedule_id": sched.id}
                            else:
                                result = {"type": "error",
                                          "message":
                                              "Failed to create "
                                              "schedule"}

            elif cmd == "schedule_update":
                sid = data.get("id", "")
                sched = state.schedules.get(sid)
                if not sched:
                    result = {"type": "error",
                              "message": "Schedule not found"}
                else:
                    fields = {}
                    for k in ("name", "task_template", "description",
                              "group", "action_name", "action_vars",
                              "agent_template", "labels", "cron_expr",
                              "scheduled_at", "timezone", "enabled"):
                        if k in data:
                            fields[k] = data[k]
                    new_cron = fields.get("cron_expr", sched.cron_expr)
                    new_at = fields.get("scheduled_at",
                                        sched.scheduled_at)
                    new_tz = fields.get("timezone", sched.timezone)
                    if "cron_expr" in fields or "scheduled_at" in fields \
                            or "timezone" in fields:
                        if new_cron:
                            try:
                                from .cron import parse_cron, \
                                    next_run as cron_next
                                parse_cron(new_cron)
                                from datetime import datetime, \
                                    timezone as dt_tz
                                nxt = cron_next(
                                    new_cron,
                                    datetime.now(dt_tz.utc),
                                    tz=new_tz)
                                fields["next_run_at"] = nxt.isoformat()
                            except ValueError as e:
                                result = {
                                    "type": "error",
                                    "message":
                                        f"Invalid cron: {e}"}
                                fields = None
                        elif new_at:
                            fields["next_run_at"] = new_at
                    if fields is not None:
                        state.schedule_update(sid, **fields)

            elif cmd == "schedule_remove":
                sid = data.get("id", "")
                if sid in state.schedules:
                    state.schedule_remove(sid)
                else:
                    result = {"type": "error",
                              "message": "Schedule not found"}

            elif cmd == "schedule_enable":
                sid = data.get("id", "")
                sched = state.schedules.get(sid)
                if not sched:
                    result = {"type": "error",
                              "message": "Schedule not found"}
                else:
                    fields = {"enabled": True}
                    if sched.cron_expr:
                        from .cron import next_run as cron_next
                        from datetime import datetime, timezone as dt_tz
                        nxt = cron_next(sched.cron_expr,
                                        datetime.now(dt_tz.utc),
                                        tz=sched.timezone)
                        fields["next_run_at"] = nxt.isoformat()
                    elif sched.scheduled_at:
                        fields["next_run_at"] = sched.scheduled_at
                    state.schedule_update(sid, **fields)

            elif cmd == "schedule_disable":
                sid = data.get("id", "")
                if sid in state.schedules:
                    state.schedule_update(sid, enabled=False)
                else:
                    result = {"type": "error",
                              "message": "Schedule not found"}

            elif cmd == "schedule_list":
                result = {
                    "type": "schedule_list",
                    "schedules": [
                        asdict(s) for s in state.schedules.values()
                    ],
                }

            elif cmd == "schedule_run":
                sid = data.get("id", "")
                sched = state.schedules.get(sid)
                if not sched:
                    result = {"type": "error",
                              "message": "Schedule not found"}
                elif sched.group not in state.groups:
                    result = {"type": "error",
                              "message": "Schedule group not found"}
                else:
                    from datetime import datetime, timezone as dt_tz
                    now = datetime.now(dt_tz.utc)
                    title = sched.task_template or sched.name
                    title = (title
                             .replace("{date}",
                                      now.strftime("%Y-%m-%d"))
                             .replace("{time}",
                                      now.strftime("%H:%M"))
                             .replace("{datetime}",
                                      now.strftime("%Y-%m-%d %H:%M")))
                    task = state.board_add_task(
                        task=title, group=sched.group,
                        lane="Backlog",
                        description=sched.description,
                        action_name=sched.action_name,
                        action_vars=dict(sched.action_vars),
                        agent_template=sched.agent_template,
                        labels=list(sched.labels))
                    if task:
                        await handle_command({
                            "cmd": "dispatch_task",
                            "id": task.id,
                            "create_agent": True})
                        sched.last_run_at = now.isoformat()
                        sched.run_count += 1
                        sched.last_task_id = task.id
                        state._emit("schedule_upsert",
                                    **asdict(sched))
                        state._db_save_schedule(sched)
                        _panel_event("schedule_fired", "",
                                     sched.name, sched.group,
                                     title, task_id=task.id)
                        result = {"type": "ok",
                                  "task_id": task.id}

            elif cmd == "ai_report":
                cell_id = data.get("cell_id", "")
                action = data.get("action", "")
                message = data.get("message", "")
                task_id = data.get("task_id", "")

                cell = state.agents.get(cell_id)
                if not cell:
                    result = {"type": "error",
                              "message": f"Cell {cell_id} not found"}
                else:
                    task = _resolve_ai_report_task(
                        state,
                        cell,
                        task_id=task_id,
                    )
                    resume_targets = _capture_auto_resume_targets(
                        state,
                        task=task,
                        group=(task.group if task else cell.group) or "",
                    )

                    def _add_label(t, label):
                        if label not in t.labels:
                            t.labels.append(label)

                    def _save_task(t):
                        from datetime import datetime, timezone
                        t.updated_at = datetime.now(
                            timezone.utc).isoformat()
                        state._emit("task_upsert", **asdict(t))
                        state._db_save_task(t)

                    def _cascade_done(task_id):
                        """Complete done task ancestors via state-layer logic."""
                        state.board_cascade_done(task_id)

                    def _append_mcp(c, act, msg=""):
                        _append_mcp_message(c, act, msg)

                    def _append_task_msg(t, act, msg, agent_name):
                        """Append to the task's persisted activity log."""
                        if t:
                            t.messages.append({
                                "timestamp": time.time(),
                                "action": act,
                                "message": msg,
                                "agent_name": agent_name,
                            })

                    def _record_history_msg(c, act, msg="", task_override=None):
                        """Persist to agent_messages history table."""
                        state.history_record_message(
                            c.id, act, msg,
                            task_id=(
                                task_override.id if task_override
                                else (task.id if task else "")
                            ))

                    async def _drain_auto_dispatch_queue(group_name: str):
                        if not group_name:
                            return
                        await _pump_auto_dispatch_queue(
                            state,
                            handle_command,
                            _panel_event,
                            group=group_name,
                        )

                    if action in {
                        "progress", "done", "blocked", "error",
                        "ask", "derive", "ready", "verify",
                    }:
                        state.mark_agent_progress(cell)

                    if result and result.get("type") == "error":
                        pass  # auto-resolve failed; skip action

                    elif action in {"progress", "blocked", "error",
                                     "verify", "derive", "ask"}:
                        _promote_task_for_active_report(state, cell, task)

                    if (
                        not (result and result.get("type") == "error")
                        and action == "done"
                    ):
                        rejected = _reject_completion_with_open_descendants(
                            state, task, "done")
                        if rejected:
                            result = rejected
                        else:
                            async def _checkpoint_for_review_gate():
                                if not (
                                    cell.worktree_path
                                    and cell.cell_type == "agent"
                                    and cell.worktree_auto_checkpoint
                                ):
                                    return
                                try:
                                    n = cell.worktree_checkpoints + 1
                                    cp_msg = (
                                        f"loom: checkpoint {n} — {cell.name}"
                                    )
                                    if message:
                                        cp_msg = f"{cp_msg}\n\n{message}"
                                    elif cell.last_summary:
                                        cp_msg = (
                                            f"{cp_msg}\n\n"
                                            f"{cell.last_summary.strip()}"
                                        )
                                    sha = await worktree_mgr.checkpoint(
                                        cell,
                                        message=cp_msg,
                                    )
                                    if sha:
                                        state._db_save_agent(cell)
                                except Exception:
                                    log.exception(
                                        "review gate checkpoint failed for"
                                        " '%s'", cell.name)

                            base_dir = cell.worktree_repo_root \
                                or cell.directory \
                                or await _resolve_base_dir(
                                    task.group if task else cell.group)
                            gate_result = await _maybe_apply_review_required_gate(
                                state,
                                action_mgr,
                                worktree_mgr,
                                handle_command,
                                _panel_event,
                                cell=cell,
                                task=task,
                                base_dir=base_dir,
                                force_skip_review=bool(
                                    data.get("force_skip_review")),
                                skip_reason=data.get(
                                    "review_skip_reason", ""),
                                checkpoint_for_gate=
                                    _checkpoint_for_review_gate,
                                append_task_msg=_append_task_msg,
                                record_history_msg=_record_history_msg,
                            )
                            if gate_result:
                                result = gate_result

                    if result and result.get("type") == "error":
                        pass

                    elif action == "done":
                        cell.activity = ""
                        cell.activity_detail = ""
                        cell.needs_attention = False
                        cell.error_message = ""
                        if message:
                            cell.last_summary = message
                        cell.current_task_id = ""
                        _append_mcp(cell, "done", message or "Done")
                        _append_task_msg(task, "done",
                                         message or "Done", cell.name)
                        _record_history_msg(
                            cell, "done", message or "Done")
                        if task:
                            state.history_complete_task(
                                cell.id, task.id, "done")
                        if task:
                            await _record_task_boundary(
                                task, cell, message or "Done"
                            )
                        state._emit_agent(cell)
                        # Auto-checkpoint on done. The session_end hook
                        # callback (_on_agent_session_end, wired at
                        # server.py:1541) already checkpoints, but it
                        # only fires when Claude Code's
                        # Stop/SessionEnd/idle_prompt hook reaches us —
                        # racy and skipped when the agent calls
                        # loom_done mid-turn. Running the same
                        # checkpoint synchronously here ensures dirty
                        # work lands on the branch before the MCP
                        # reply returns, mirroring the pre-merge
                        # checkpoint in worktree_merge.
                        if (cell.worktree_path
                                and cell.cell_type == "agent"
                                and cell.worktree_auto_checkpoint):
                            block_reason = (
                                _shared_review_checkpoint_block_reason(
                                    state,
                                    cell,
                                )
                            )
                            if block_reason:
                                log.info(
                                    "Skipping done auto-checkpoint: %s",
                                    block_reason,
                                )
                            else:
                                try:
                                    cp_msg = _checkpoint_message(cell)
                                    sha = await worktree_mgr.checkpoint(
                                        cell, message=cp_msg)
                                    if sha:
                                        state._db_save_agent(cell)
                                except Exception:
                                    log.exception(
                                        "done auto-checkpoint failed for"
                                        " '%s'", cell.name)
                        if task and not task_counts_as_done(task):
                            state.board_move_task(task.id, "Done")
                        if task:
                            task.status = ""
                            _save_task(task)
                            if data.get("push_external") \
                                    and (task.provider or task.external_url):
                                try:
                                    posted = post_ticket_comment(
                                        task,
                                        comment=build_completion_comment(
                                            task.task, message),
                                    )
                                    _append_task_msg(
                                        task, "external_comment",
                                        posted, "loom")
                                    _save_task(task)
                                    result = {
                                        "type": "external_comment_posted",
                                        "task_id": task.id,
                                        "message": posted,
                                    }
                                except ExternalTicketError as exc:
                                    _append_task_msg(
                                        task, "external_error",
                                        str(exc), "loom")
                                    _save_task(task)
                                    result = {
                                        "type": "warning",
                                        "message": str(exc),
                                        "task_id": task.id,
                                    }
                            _cascade_done(task.id)
                            # Notify dependents that are now unblocked
                            for _dt in state.board_get_dependents(
                                    task.id):
                                if not task_is_closed(_dt) \
                                        and state.board_deps_met(_dt):
                                    _panel_event(
                                        "task_unblocked", "",
                                        "", _dt.group,
                                        f"Task '{_dt.task[:60]}'"
                                        " is now unblocked",
                                        task_id=_dt.id)
                        _panel_event(
                            "task_completed", cell.id,
                            cell.name, cell.group,
                            message or "Task completed",
                            task_id=task.id if task else "")
                        await _maybe_auto_resume_targets(
                            state,
                            handle_command,
                            _panel_event,
                            targets=resume_targets,
                            group=task.group if task else cell.group,
                        )
                        await _drain_auto_dispatch_queue(
                            task.group if task else cell.group
                        )
                        if task:
                            await _maybe_auto_close_root_done_agents(
                                state,
                                task,
                                action_mgr=action_mgr,
                                resolve_base_dir=_resolve_base_dir,
                                close_agent=_close_agent_session_only,
                            )

                    elif action == "blocked":
                        cell.needs_attention = True
                        cell.activity = "waiting"
                        cell.activity_detail = message
                        _append_mcp(cell, "blocked", message)
                        _append_task_msg(task, "blocked",
                                         message, cell.name)
                        _record_history_msg(cell, "blocked", message)
                        state._emit_agent(cell)
                        if task:
                            _add_label(task, "loom:blocked")
                            _save_task(task)
                        _panel_event(
                            "agent_blocked", cell.id,
                            cell.name, cell.group, message,
                            task_id=task.id if task else "")
                        state.recompute_task_health()

                    elif action == "error":
                        cell.error_message = message
                        cell.needs_attention = True
                        _append_mcp(cell, "error", message)
                        _append_task_msg(task, "error",
                                         message, cell.name)
                        _record_history_msg(cell, "error", message)
                        state._emit_agent(cell)
                        if task:
                            _add_label(task, "loom:error")
                            _save_task(task)
                        _panel_event(
                            "agent_error", cell.id,
                            cell.name, cell.group, message,
                            task_id=task.id if task else "")
                        state.recompute_task_health()

                    elif action == "progress":
                        cell.activity_detail = message
                        if cell.needs_attention:
                            cell.needs_attention = False
                        _append_mcp(cell, "progress", message)
                        _append_task_msg(task, "progress",
                                         message, cell.name)
                        _record_history_msg(cell, "progress", message)
                        state._emit_agent(cell)
                        if task:
                            _save_task(task)
                        # Auto-checkpoint on progress (throttled)
                        await _checkpoint_on_report(cell, message)
                        # Panel event — replace last progress
                        # for this agent to avoid flooding
                        pe = panel_log.replace_last(
                            "agent_progress", cell.id,
                            agent_name=cell.name,
                            group=cell.group,
                            message=message)
                        state._emit("event_append", **pe)
                        state.recompute_task_health()

                    elif action == "verify":
                        if not task:
                            result = {"type": "error",
                                      "message": "No linked task to verify"}
                        else:
                            payload = {}
                            for key in (
                                "verification_mode",
                                "verification_state",
                                "verification_notes",
                                "tests_run",
                                "manual_smoke_done",
                                "deploy_needed",
                                "deploy_attempted",
                                "human_validation_pending",
                            ):
                                if key in data:
                                    payload[key] = data[key]
                            root_task = None
                            root_id = task.pipeline_root_id or ""
                            if root_id and root_id != task.id:
                                root_task = state.board_tasks.get(root_id)
                            verify_msg, _root_task = _apply_verification_report(
                                task,
                                payload,
                                cell.name,
                                _save_task,
                                root_task=root_task,
                            )
                            _append_mcp(cell, "verify", verify_msg)
                            _append_task_msg(task, "verify",
                                             verify_msg, cell.name)
                            _record_history_msg(cell, "verify", verify_msg)
                            state._emit_agent(cell)
                            _panel_event(
                                "task_verification_updated",
                                cell.id, cell.name, cell.group,
                                verify_msg, task_id=task.id,
                            )
                            result = {
                                "type": "verification_updated",
                                "task_id": task.id,
                                "message": verify_msg,
                            }
                            await _maybe_auto_resume_targets(
                                state,
                                handle_command,
                                _panel_event,
                                targets=resume_targets,
                                group=task.group if task else cell.group,
                            )

                    elif action == "ready":
                        rejected = _reject_completion_with_open_descendants(
                            state, task, "ready")
                        if rejected:
                            result = rejected
                        else:
                            cell.activity = ""
                            cell.activity_detail = "ready"
                            cell.needs_attention = False
                            cell.error_message = ""
                            cell.current_task_id = ""
                            _append_mcp(cell, "ready", "Ready")
                            _append_task_msg(task, "ready",
                                             message or "Ready", cell.name)
                            _record_history_msg(
                                cell, "ready", message or "Ready")
                            if task:
                                state.history_complete_task(
                                    cell.id, task.id, "ready")
                                await _record_task_boundary(
                                    task, cell, message or "Ready"
                                )
                            state._emit_agent(cell)
                            if task:
                                if not task_counts_as_done(task):
                                    state.board_move_task(
                                        task.id, "Done")
                                task.agent_id = ""
                                task.status = ""
                                _save_task(task)
                                _cascade_done(task.id)
                                # Notify dependents now unblocked
                                for _dt in state.board_get_dependents(
                                        task.id):
                                    if not task_is_closed(_dt) \
                                            and state.board_deps_met(_dt):
                                        _panel_event(
                                            "task_unblocked", "",
                                            "", _dt.group,
                                            f"Task '{_dt.task[:60]}'"
                                            " is now unblocked",
                                            task_id=_dt.id)
                            _panel_event(
                                "task_completed", cell.id,
                                cell.name, cell.group,
                                "Ready (task completed)",
                                task_id=task.id if task else "")
                            await _maybe_auto_resume_targets(
                                state,
                                handle_command,
                                _panel_event,
                                targets=resume_targets,
                                group=task.group if task else cell.group,
                            )
                            await _drain_auto_dispatch_queue(
                                task.group if task else cell.group
                            )

                    elif action == "derive":
                        # Derive a new task and dispatch it
                        act_name = data.get("action_name", "")
                        act_vars = data.get("action_vars", {})
                        derive_group = data.get("group", "")
                        reuse_self = data.get("reuse_self", False)
                        target_agent = data.get("target_agent", "")

                        if not task:
                            result = {"type": "error",
                                      "message":
                                          "No linked task to derive from"}
                        elif not message:
                            result = {"type": "error",
                                      "message":
                                          "Derive requires a description"}
                        else:
                            # Validate transition
                            base_dir = cell.worktree_repo_root \
                                or cell.directory \
                                or await _resolve_base_dir(
                                    task.group)
                            cur_transitions = \
                                action_mgr.get_transitions(
                                    task.action_name, base_dir)
                            valid_targets = [
                                t["action"] for t in cur_transitions
                                if isinstance(t, dict)
                                and t.get("action")]
                            if cur_transitions and act_name \
                                    and act_name not in valid_targets:
                                result = {
                                    "type": "error",
                                    "message":
                                        f"Action '{task.action_name}'"
                                        f" cannot transition to "
                                        f"'{act_name}'. Valid: "
                                        f"{', '.join(valid_targets)}"}
                            else:
                                # Check depth limit
                                new_depth = task.pipeline_depth + 1
                                act_meta = \
                                    action_mgr.load_action(
                                        act_name, base_dir) \
                                    if act_name else None
                                max_d = (
                                    (act_meta or {}).get("max_depth")
                                    or state.global_settings
                                        .max_pipeline_depth
                                    or 0)
                                if max_d and new_depth > max_d:
                                    cell.needs_attention = True
                                    state._emit_agent(cell)
                                    if task:
                                        _add_label(task,
                                                   "loom:depth-limit")
                                        _save_task(task)
                                    result = {
                                        "type": "error",
                                        "message":
                                            f"Pipeline depth limit "
                                            f"({max_d}) reached"}
                                else:
                                    # Keep parent in In Progress;
                                    # update its status from transition
                                    cell.activity = ""
                                    cell.activity_detail = ""
                                    cell.needs_attention = False
                                    cell.error_message = ""
                                    state._emit_agent(cell)
                                    # Determine status from transition
                                    derive_status = ""
                                    if cur_transitions and act_name:
                                        for tr in cur_transitions:
                                            if isinstance(tr, dict) \
                                                    and tr.get("action") \
                                                    == act_name:
                                                derive_status = tr.get(
                                                    "status", "")
                                                break
                                    if not derive_status and act_name:
                                        derive_status = act_name
                                    # Update parent task status
                                    task.status = derive_status
                                    _save_task(task)
                                    # Propagate status to root
                                    root_id_s = \
                                        task.pipeline_root_id \
                                        or task.id
                                    if root_id_s != task.id:
                                        root_t = \
                                            state.board_tasks.get(
                                                root_id_s)
                                        if root_t:
                                            root_t.status = \
                                                derive_status
                                            _save_task(root_t)
                                    # Create derived task
                                    grp = derive_group \
                                        or task.group
                                    root_id = \
                                        task.pipeline_root_id \
                                        or task.id
                                    derive_desc = data.get(
                                        "description", "")
                                    assigned_engineer_id = (
                                        _inherit_assigned_engineer_for_derived_task(task)
                                    )
                                    reusable_task = _find_reusable_review_fix_task(
                                        state,
                                        task,
                                        act_name,
                                    )
                                    reused_existing_task = reusable_task is not None
                                    new_task = reusable_task
                                    if not new_task:
                                        new_task = state.board_add_task(
                                            task=message,
                                            group=grp,
                                            lane="Backlog",
                                            action_name=act_name,
                                            action_vars=act_vars,
                                            labels=["loom:derived"],
                                            parent_task_id=task.id,
                                            pipeline_depth=new_depth,
                                            pipeline_root_id=root_id,
                                            description=derive_desc,
                                            assigned_engineer_id=assigned_engineer_id,
                                        )
                                    elif reused_existing_task:
                                        _refresh_reused_derived_task(
                                            new_task,
                                            message=message,
                                            description=derive_desc,
                                            action_vars=act_vars,
                                        )
                                        _inherit_assigned_engineer_for_derived_task(
                                            task,
                                            new_task,
                                        )
                                        _save_task(new_task)
                                    if new_task:
                                        _inherit_assigned_engineer_for_derived_task(
                                            task,
                                            new_task,
                                        )
                                    if new_task:
                                        _append_mcp(
                                            cell, "derive",
                                            message[:80])
                                        _append_task_msg(
                                            task, "derive",
                                            message, cell.name)
                                        _record_history_msg(
                                            cell, "derive",
                                            message[:80])
                                        _save_task(task)
                                        state._emit_agent(cell)
                                        if not reused_existing_task:
                                            _panel_event(
                                                "task_derived",
                                                cell.id, cell.name,
                                                cell.group,
                                                message[:80],
                                                task_id=new_task.id)
                                        elif (
                                            new_task.agent_id
                                            and getattr(new_task, "lane", "") == "In Progress"
                                        ):
                                            result = {
                                                "type": "ok",
                                                "task_id": new_task.id,
                                                "agent_id": new_task.agent_id,
                                            }
                                        elif (
                                            new_task.agent_id
                                            and state.agent_is_busy(new_task.agent_id)
                                        ):
                                            result = {
                                                "type": "queued",
                                                "task_id": new_task.id,
                                                "agent_id": new_task.agent_id,
                                            }
                                        # Determine dispatch target
                                        # Enforce transition's declared
                                        # target — ignore --self/--agent
                                        # if the transition specifies a
                                        # different target
                                        tr_target = ""
                                        if cur_transitions and act_name:
                                            for tr in cur_transitions:
                                                if isinstance(tr, dict) \
                                                        and tr.get(
                                                            "action"
                                                        ) == act_name:
                                                    tr_target = tr.get(
                                                        "target", "")
                                                    break
                                        if tr_target == "self":
                                            reuse_self = True
                                            target_agent = ""
                                        elif tr_target == "parent":
                                            reuse_self = False
                                            pt = state.board_tasks.get(
                                                task.parent_task_id) \
                                                if task.parent_task_id \
                                                else None
                                            if pt and pt.agent_id:
                                                a = state.agents.get(
                                                    pt.agent_id)
                                                if a:
                                                    target_agent = \
                                                        a.slug or a.name
                                        elif tr_target == "root":
                                            reuse_self = False
                                            rid = task.pipeline_root_id \
                                                or task.id
                                            rt = state.board_tasks.get(
                                                rid)
                                            if rt and rt.agent_id:
                                                a = state.agents.get(
                                                    rt.agent_id)
                                                if a:
                                                    target_agent = \
                                                        a.slug or a.name
                                        elif tr_target == "" \
                                                and cur_transitions:
                                            # No explicit target declared.
                                            # Reuse an ancestor thread only
                                            # when this derive is clearly
                                            # returning to a prior action
                                            # stage (for example fix ->
                                            # re-review). Otherwise keep the
                                            # normal fresh-agent behavior.
                                            reuse_self = False
                                            ancestor_agent = \
                                                _nearest_ancestor_agent_for_action_stage(
                                                    state,
                                                    task,
                                                    act_name,
                                                )
                                            if ancestor_agent:
                                                target_agent = (
                                                    ancestor_agent.slug
                                                    or ancestor_agent.name
                                                )
                                            else:
                                                target_agent = ""
                                        target_id = None
                                        if reuse_self:
                                            target_id = cell.id
                                        elif target_agent:
                                            target_id = \
                                                _resolve_agent_id(
                                                    state,
                                                    target_agent)
                                            if not target_id:
                                                result = {
                                                    "type": "error",
                                                    "message":
                                                        "Agent not "
                                                        "found: "
                                                        + target_agent
                                                }
                                        elif reused_existing_task and new_task.agent_id:
                                            target_id = new_task.agent_id

                                        if result and \
                                                result.get("type") \
                                                == "error":
                                            pass  # skip dispatch
                                        elif result and reused_existing_task:
                                            pass
                                        elif target_id == cell.id:
                                            # Self-dispatch through the
                                            # normal delayed same-agent
                                            # prompt path.
                                            dispatch_data = {
                                                "cmd": "dispatch_task",
                                                "id": new_task.id,
                                                "agent_id": cell.id,
                                                "_self_dispatch": True,
                                            }
                                            await state.broadcast()
                                            dr = await handle_command(
                                                dispatch_data)
                                            if dr and dr.get("type") \
                                                    == "error":
                                                result = dr
                                            else:
                                                result = {
                                                    "type": "ok",
                                                    "task_id":
                                                        new_task.id,
                                                    "agent_id":
                                                        cell.id,
                                                }
                                        elif target_id:
                                            # Dispatch to different
                                            # existing agent
                                            tgt = state.agents.get(
                                                target_id)
                                            dispatch_data = {
                                                "cmd": "dispatch_task",
                                                "id": new_task.id,
                                                "agent_id": target_id,
                                            }
                                            # Inherit worktree from
                                            # target agent
                                            if tgt and \
                                                    tgt.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = target_id
                                            if cell.worktree_path:
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            elif cell.worktree_branch:
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            if not (tgt and
                                                    tgt.worktree_path) \
                                                    and cell.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                            await state.broadcast()
                                            dr = \
                                                await handle_command(
                                                    dispatch_data)
                                            if not _derive_handoff_accepted(dr):
                                                result = dr or {
                                                    "type": "error",
                                                    "message":
                                                        "Derived task dispatch failed",
                                                }
                                            else:
                                                if cell.current_task_id == task.id:
                                                    cell.current_task_id = ""
                                                state._emit_agent(cell)
                                                state._db_save_agent(cell)
                                                result = {
                                                    "type": (
                                                        (dr or {}).get("type")
                                                        or "ok"
                                                    ),
                                                    "task_id":
                                                        new_task.id,
                                                    "agent_id":
                                                        (
                                                            (dr or {}).get(
                                                                "agent_id"
                                                            )
                                                            or target_id
                                                        )}
                                        else:
                                            # Default: new agent
                                            dispatch_data = {
                                                "cmd":
                                                    "dispatch_task",
                                                "id": new_task.id,
                                                "create_agent": True,
                                            }
                                            owner_weaver_id = \
                                                _ownership_weaver_id_for_dispatch_source(
                                                    cell
                                                )
                                            if owner_weaver_id:
                                                dispatch_data[
                                                    "_created_by_weaver_id"
                                                ] = owner_weaver_id
                                            # Inherit worktree from
                                            # parent agent
                                            if cell.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            if cell.session_id:
                                                dispatch_data[
                                                    "target_session_id"
                                                ] = cell.session_id
                                            if cell.window_id:
                                                dispatch_data[
                                                    "target_window_id"
                                                ] = cell.window_id
                                            await state.broadcast()
                                            dr = \
                                                await handle_command(
                                                    dispatch_data)
                                            if not _derive_handoff_accepted(dr):
                                                result = dr or {
                                                    "type": "error",
                                                    "message":
                                                        "Derived task dispatch failed",
                                                }
                                            else:
                                                if cell.current_task_id == task.id:
                                                    cell.current_task_id = ""
                                                state._emit_agent(cell)
                                                state._db_save_agent(cell)
                                                agent_id_result = (
                                                    (dr or {}).get("agent_id")
                                                    or new_task.agent_id
                                                )
                                                result = {
                                                    "type": (
                                                        (dr or {}).get("type")
                                                        or "ok"
                                                    ),
                                                    "task_id":
                                                        new_task.id,
                                                    "agent_id":
                                                        agent_id_result}
                                    else:
                                        result = {
                                            "type": "error",
                                            "message":
                                                "Failed to create "
                                                "derived task"}

                    elif action == "ask":
                        # Create a derived task in Backlog for human
                        if not task:
                            result = {"type": "error",
                                      "message":
                                          "No linked task to derive from"}
                        elif not message:
                            result = {"type": "error",
                                      "message":
                                          "Ask requires a question"}
                        else:
                            # Keep parent in In Progress with
                            # "Awaiting Input" status
                            cell.activity = ""
                            cell.activity_detail = ""
                            cell.needs_attention = True
                            cell.error_message = ""
                            _append_mcp(cell, "ask", message)
                            _append_task_msg(task, "ask",
                                             message, cell.name)
                            _record_history_msg(cell, "ask", message)
                            state._emit_agent(cell)
                            task.status = "Awaiting Input"
                            _save_task(task)
                            # Propagate status to root
                            root_id_s = task.pipeline_root_id \
                                or task.id
                            if root_id_s != task.id:
                                root_t = state.board_tasks.get(
                                    root_id_s)
                                if root_t:
                                    root_t.status = "Awaiting Input"
                                    _save_task(root_t)
                            # Create HITL task in Backlog
                            grp = task.group
                            root_id = task.pipeline_root_id \
                                or task.id
                            ask_desc = data.get(
                                "description", "")
                            new_task = state.board_add_task(
                                task=message,
                                group=grp,
                                lane="Backlog",
                                labels=["loom:human", "loom:derived"],
                                parent_task_id=task.id,
                                pipeline_depth=
                                    task.pipeline_depth + 1,
                                pipeline_root_id=root_id,
                                description=ask_desc,
                            )
                            if new_task:
                                result = {
                                    "type": "ok",
                                    "task_id": new_task.id}
                                _panel_event(
                                    "ask_created", cell.id,
                                    cell.name, cell.group,
                                    message,
                                    task_id=new_task.id)
                            else:
                                result = {
                                    "type": "error",
                                    "message":
                                        "Failed to create ask task"}

                    elif action == "name":
                        if not message:
                            result = {"type": "error",
                                      "message":
                                          "Name is required"}
                        else:
                            old_name = cell.name
                            _append_mcp(cell, "name", message)
                            _append_task_msg(task, "name",
                                             message, cell.name)
                            _record_history_msg(
                                cell, "name", message)
                            if task:
                                _save_task(task)
                            state.update_agent(cell.id,
                                               name=message)
                            state.history_update_agent(
                                cell, name=message,
                                slug=cell.slug)
                            if cell.session_id:
                                await bridge.update_session(
                                    cell, old_name)
                            _panel_event(
                                "agent_renamed", cell.id,
                                cell.name, cell.group,
                                f"{old_name} \u2192 {cell.name}")
                            state.recompute_task_health()
                            result = {"type": "ok",
                                      "slug": cell.slug}

                    elif action == "reply":
                        result = _handle_weaver_reply(
                            state,
                            cell,
                            message=message,
                            task_id=task_id,
                            panel_event=_panel_event,
                        )

                    else:
                        result = {"type": "error",
                                  "message":
                                      f"Unknown ai action: {action}"}

            elif cmd == "memory_publish":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    cell, task = _resolve_memory_cell_and_task(
                        state,
                        data.get("cell_id", ""),
                        data.get("task_id", ""),
                    )
                    existing = None
                    entry_id = (data.get("entry_id", "") or "").strip()
                    if entry_id:
                        existing = state.db.load_memory_entry(entry_id)
                        if not existing:
                            result = {
                                "type": "error",
                                "message": "Memory entry not found",
                            }
                    try:
                        if result is not None:
                            raise RuntimeError("__skip_memory_publish__")
                        scope_kind = (data.get("scope_kind", "") or "").strip()
                        if not scope_kind and existing:
                            scope_kind = existing.get("scope_kind", "")
                        scope_ref = _resolve_memory_scope_ref(
                            scope_kind,
                            data.get("scope_ref", "")
                            if "scope_ref" in data
                            else (existing.get("scope_ref", "") if existing else ""),
                            cell=cell,
                            task=task,
                        )
                        entry_type = data.get("entry_type", "")
                        if not entry_type and existing:
                            entry_type = existing.get("entry_type", "")
                        title = data.get("title", "")
                        if "title" not in data and existing:
                            title = existing.get("title", "")
                        content = data.get("content", "")
                        if "content" not in data and existing:
                            content = existing.get("content", "")
                        pinned = (
                            bool(data.get("pinned", False))
                            if "pinned" in data
                            else bool(existing.get("pinned", False))
                            if existing
                            else False
                        )
                        retention_kind = (
                            (data.get("retention_kind", "") or "").strip()
                            if "retention_kind" in data
                            else (existing.get("retention_kind", "") if existing else "")
                        )
                        if retention_kind:
                            retention_kind = normalize_retention_kind(retention_kind)
                        source_kind = data.get("source_kind", "")
                        if not source_kind and existing:
                            source_kind = existing.get("source_kind", "agent")
                        pending_links = []
                        for raw_link in (data.get("link_targets", []) or []):
                            if not isinstance(raw_link, dict):
                                raise ValueError(
                                    "Each link target must be an object"
                                )
                            target_kind = normalize_link_target_kind(
                                raw_link.get("target_kind", "")
                            )
                            pending_links.append(
                                build_memory_link(
                                    state,
                                    entry_id=entry_id or "__pending__",
                                    target_kind=target_kind,
                                    target_ref=_resolve_memory_link_ref(
                                        target_kind,
                                        raw_link.get("target_ref", ""),
                                        cell=cell,
                                        task=task,
                                    ),
                                    cell=cell,
                                    task=task,
                                )
                            )
                        entry = build_memory_entry(
                            state,
                            cell=cell,
                            task=task,
                            entry_type=normalize_entry_type(entry_type),
                            title=title,
                            content=content,
                            scope_kind=scope_kind,
                            scope_ref=scope_ref,
                            pinned=pinned,
                            source_kind=source_kind or "agent",
                            retention_kind=retention_kind,
                        )
                    except RuntimeError as exc:
                        if str(exc) != "__skip_memory_publish__":
                            raise
                    except ValueError as exc:
                        result = {"type": "error", "message": str(exc)}
                    else:
                        if result is None:
                            if existing:
                                entry["id"] = existing["id"]
                                entry["created_at"] = existing.get(
                                    "created_at", entry["created_at"]
                                )
                                entry["source_kind"] = existing.get(
                                    "source_kind", entry["source_kind"]
                                )
                                entry["source_id"] = existing.get(
                                    "source_id", entry["source_id"]
                                )
                                entry["source_name"] = existing.get(
                                    "source_name", entry["source_name"]
                                )
                            state.db.save_memory_entry(entry)
                            for link in pending_links:
                                link["entry_id"] = entry["id"]
                                state.db.save_memory_link(link)
                            entry = state.db.load_memory_entry(entry["id"]) or entry
                            result = {"type": "memory_entry", "entry": entry}

            elif cmd == "memory_pin":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        now = time.time()
                        state.db.set_memory_entry_pinned(entry_id, True, now)
                        entry = state.db.load_memory_entry(entry_id) or entry
                        result = {"type": "memory_entry", "entry": entry}

            elif cmd == "memory_link":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        cell, task = _resolve_memory_cell_and_task(
                            state,
                            data.get("cell_id", ""),
                            data.get("task_id", ""),
                        )
                        try:
                            target_kind = normalize_link_target_kind(
                                data.get("target_kind", "")
                            )
                            link = build_memory_link(
                                state,
                                entry_id=entry_id,
                                target_kind=target_kind,
                                target_ref=_resolve_memory_link_ref(
                                    target_kind,
                                    data.get("target_ref", ""),
                                    cell=cell,
                                    task=task,
                                ),
                                cell=cell,
                                task=task,
                            )
                        except ValueError as exc:
                            result = {"type": "error", "message": str(exc)}
                        else:
                            state.db.save_memory_link(link)
                            entry = state.db.load_memory_entry(entry_id)
                            result = {"type": "memory_entry", "entry": entry}

            elif cmd == "memory_unpin":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        now = time.time()
                        state.db.set_memory_entry_pinned(entry_id, False, now)
                        entry = state.db.load_memory_entry(entry_id) or entry
                        result = {"type": "memory_entry", "entry": entry}

            elif cmd == "task_chain":
                tid = data.get("task_id", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    chain = state.board_get_chain(tid)
                    result = {
                        "type": "ok",
                        "root_id": task.pipeline_root_id or task.id,
                        "chain": [
                            {"id": t.id, "task": t.task,
                             "lane": t.lane,
                             "status": t.status,
                             "depth": t.pipeline_depth,
                             "agent_id": t.agent_id,
                             "action_name": t.action_name,
                             "parent_task_id": t.parent_task_id,
                             "labels": t.labels}
                            for t in chain
                        ],
                    }

            elif cmd == "discover_pipelines":
                base_dir = await _resolve_base_dir(
                    data.get("group", ""))
                pipelines = action_mgr.discover_pipelines(base_dir)
                result = {"type": "pipelines", "pipelines": pipelines}

            # -- Weaver commands ------------------------------------------

            elif cmd == "weaver_message":
                agent_ident = data.get("agent_id", "")
                msg_text = data.get("message", "")
                agent_id = _resolve_agent_id(state, agent_ident)
                if not agent_id:
                    result = {"type": "error",
                              "message": f"Agent not found: {agent_ident}"}
                elif not msg_text:
                    result = {"type": "error",
                              "message": "Message is required"}
                else:
                    target = state.agents.get(agent_id)
                    result = await _send_weaver_message_to_agent(
                        state,
                        bridge,
                        target,
                        msg_text,
                        _panel_event,
                    )

            elif cmd == "inject_mcp_message":
                target_ident = data.get("agent_id", "")
                target_id = _resolve_agent_id(state, target_ident)
                target = state.agents.get(target_id) if target_id else None
                if not target:
                    result = {"type": "error",
                              "message": f"Agent not found: {target_ident}"}
                elif not getattr(target, "session_id", ""):
                    result = {"type": "ok", "delivered": False,
                              "reason": "no_session"}
                else:
                    recipient_anchor = ""
                    if (
                        str(getattr(target, "kind", "") or "").strip()
                        == "engineer"
                        and str(data.get("sender_kind", "") or "").strip()
                        == "architect"
                    ):
                        recipient_anchor = agent_identity_anchor(target)
                    formatted = _format_injected_mcp_message_prompt(
                        message=str(data.get("message", "") or ""),
                        sender_name=str(data.get("sender_name", "") or ""),
                        sender_kind=str(data.get("sender_kind", "") or ""),
                        recipient_kind=str(
                            getattr(target, "kind", "") or ""
                        ),
                        message_id=str(data.get("message_id", "") or ""),
                        recipient_anchor=recipient_anchor,
                        ack_required=bool(data.get("ack_required", False)),
                    )
                    try:
                        if hasattr(bridge, "prime_input_ready"):
                            bridge.prime_input_ready(target.session_id)
                        await bridge.send_text(target.session_id, formatted)
                        result = {"type": "ok", "delivered": True}
                    except Exception as exc:
                        log.exception(
                            "Failed to inject MCP message to %s", target.id)
                        result = {"type": "error",
                                  "message": f"Failed to inject: {exc}"}

            elif cmd == "weaver_journal_append":
                group = data.get("group", "")
                entry_type = data.get("entry_type", "")
                entry_text = data.get("entry", "")
                if entry_type not in (
                        "decision", "observation", "checkpoint", "plan"):
                    result = {"type": "error",
                              "message":
                                  "entry_type must be one of: decision, "
                                  "observation, checkpoint, plan"}
                elif not entry_text:
                    result = {"type": "error",
                              "message": "Entry text is required"}
                else:
                    evt = state.journal_append(
                        group, entry_type, entry_text,
                        author_cell_id=str(
                            data.get("author_cell_id", "") or ""
                        ).strip())
                    result = {"type": "ok", "id": evt["id"]}

            elif cmd == "weaver_journal_read":
                group = data.get("group", "")
                tail = data.get("tail", 20)
                entry_type = data.get("entry_type", "")
                entries = state.journal_read(
                    group,
                    tail,
                    entry_type,
                    author_cell_id=str(
                        data.get("author_cell_id", "") or ""
                    ).strip(),
                )
                result = {"type": "journal", "entries": entries}

            elif cmd == "weaver_session_map_read":
                group = str(data.get("group", "") or "").strip()
                if not group:
                    result = {
                        "type": "error",
                        "message": "Group is required",
                    }
                else:
                    result = {
                        "type": "weaver_session_map",
                        "group": group,
                        "session_map": build_weaver_session_map(state, group),
                    }

            elif cmd == "weaver_journal_delete":
                group = data.get("group", "")
                entry_id = data.get("entry_id", 0)
                if entry_id and db:
                    db._conn.execute(
                        "DELETE FROM weaver_journal WHERE id=? "
                        "AND group_name=?", (entry_id, group))
                    db._conn.commit()
                    state._emit("journal_delete",
                                group=group, id=entry_id)
                result = {"type": "ok"}

            elif cmd == "weaver_update_settings":
                group = data.get("group", "")
                fields = {}
                for k in ("push_interval", "max_interval",
                          "heartbeat_interval",
                          "default_worker_concurrency",
                          "autonomy_mode",
                          "wave_size_preference",
                          "same_agent_follow_up_preference",
                          "digest_verbosity",
                          "escalation_style",
                          "pending_note", "pending_note_kind",
                          "custom_instructions", "enabled_events",
                          "paused", "weaver_provider",
                          "weaver_boot_command", "weaver_model",
                          "weaver_reasoning_effort",
                          "weaver_directory", "weaver_profile",
                          "weaver_shell", "weaver_tab_color"):
                    if k in data:
                        fields[k] = data[k]
                state.update_weaver_settings(group, **fields)
                result = {"type": "ok"}

            elif cmd == "weaver_ask":
                group = data.get("group", "")
                question = data.get("question", "")
                if not question:
                    result = {"type": "error",
                              "message": "Question is required"}
                else:
                    state.update_weaver_settings(
                        group,
                        pending_question=question,
                        paused=True)
                    weaver_buffer.on_delivery_paused(group)
                    # Also log to journal
                    state.journal_append(
                        group, "observation",
                        f"Asked human: {question}")
                    result = {"type": "ok"}

            elif cmd == "weaver_note":
                group = data.get("group", "")
                message = data.get("message", "")
                kind = data.get("kind", "note")
                if not message:
                    result = {"type": "error",
                              "message": "Message is required"}
                elif kind not in {"note", "question"}:
                    result = {"type": "error",
                              "message": "kind must be 'note' or 'question'"}
                else:
                    state.update_weaver_settings(
                        group,
                        pending_note=message,
                        pending_note_kind=kind)
                    prefix = "Soft question" if kind == "question" else "Note"
                    state.journal_append(
                        group, "observation",
                        f"{prefix} for human: {message}")
                    result = {"type": "ok"}

            elif cmd == "weaver_dismiss_note":
                group = data.get("group", "")
                state.update_weaver_settings(
                    group,
                    pending_note="",
                    pending_note_kind="")
                result = {"type": "ok"}

            elif cmd == "weaver_reply":
                group = data.get("group", "")
                answer = data.get("answer", "")
                if not answer:
                    result = {"type": "error",
                              "message": "Answer is required"}
                else:
                    weaver = state.get_weaver_for_group(group)
                    if not weaver or not weaver.session_id:
                        result = {"type": "error",
                                  "message": "Weaver is not running"}
                    else:
                        result = await _deliver_weaver_reply_and_resume(
                            state,
                            weaver,
                            group=group,
                            answer=answer,
                            send_prompt=_send_agent_prompt,
                            weaver_buffer=weaver_buffer,
                        )

            elif cmd == "weaver_pause":
                group = data.get("group", "")
                state.update_weaver_settings(group, paused=True)
                weaver_buffer.on_delivery_paused(group)
                result = {"type": "ok"}

            elif cmd == "weaver_resume":
                group = data.get("group", "")
                state.update_weaver_settings(
                    group, paused=False, pending_question="")
                weaver_buffer.on_delivery_resumed(group)
                result = {"type": "ok"}

            elif cmd == "digest_pause":
                result = _handle_digest_pause_resume_command(
                    state,
                    weaver_buffer,
                    data,
                    paused=True,
                )

            elif cmd == "digest_resume":
                result = _handle_digest_pause_resume_command(
                    state,
                    weaver_buffer,
                    data,
                    paused=False,
                )

            elif cmd == "weaver_flush_now":
                result = _handle_weaver_flush_now_command(
                    weaver_buffer, data)

            elif cmd == "restart":
                log.info("Restart requested — cleaning up and re-executing")
                if _should_install_keybindings() and keybindings:
                    await keybindings.remove(connection, _displaced[0])
                # Persist all agents (status etc.) before restart
                for cell in state.agents.values():
                    state._db_save_agent(cell)
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            result = {"type": "error", "message": str(exc)}

        await state.broadcast()
        return result

    async def _confirm_keybinding_close(cell) -> bool:
        if not keybindings:
            return False
        try:
            import iterm2
            message = keybindings.build_close_cell_confirmation_message(
                state, cell)
            alert = iterm2.Alert(
                "Close cell?",
                message,
                window_id=cell.window_id or state.current_window_id or None,
            )
            alert.add_button("Cancel")
            alert.add_button("Remove")
            return await alert.async_run(connection) == 1001
        except Exception:
            log.exception("Failed to confirm close-cell keybinding for '%s'",
                          cell.name)
            return False

    async def _run_keybinding_command(payload: dict,
                                      description: str) -> dict | None:
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            log.warning("Keybinding command '%s' failed: %s",
                        description, result.get("message", "unknown error"))
        return result

    async def _handle_keybinding_add_agent(*,
                                           group: str,
                                           name: str = "",
                                           target_session_id: str = "",
                                           target_window_id: str = ""):
        payload = {"cmd": "add_agent", "group": group}
        if name:
            payload["name"] = name
        if target_session_id:
            payload["target_session_id"] = target_session_id
        if target_window_id:
            payload["target_window_id"] = target_window_id
        await _run_keybinding_command(payload, "add_agent")

    async def _handle_keybinding_add_terminal(*,
                                              group: str,
                                              parent_id: str,
                                              name: str = ""):
        payload = {
            "cmd": "add_terminal",
            "group": group,
            "parent_id": parent_id,
        }
        if name:
            payload["name"] = name
        await _run_keybinding_command(payload, "add_terminal")

    async def _handle_keybinding_close_cell(cell):
        if await _confirm_keybinding_close(cell):
            await _run_keybinding_command(
                {"cmd": "remove_agent", "id": cell.id},
                "remove_agent",
            )

    # Register iTerm2 RPCs and global key bindings only when Loom is
    # running as an iTerm2-hosted script. External standalone mode still
    # controls sessions through the adapter, but it should not try to
    # register iTerm2-global shortcuts before the HTTP server binds.
    if _should_install_keybindings():
        _kb_overrides = state.global_settings.keybindings or None
        from . import keybindings

        displaced_bindings = await keybindings.setup(
            connection,
            state,
            bridge,
            overrides=_kb_overrides,
            add_agent_handler=_handle_keybinding_add_agent,
            add_terminal_handler=_handle_keybinding_add_terminal,
            close_cell_handler=_handle_keybinding_close_cell,
        )
        # Mutable container so nested closures can reassign on keybinding change
        _displaced = [displaced_bindings]
        log.info("Startup checkpoint: keybindings installed")
    else:
        log.info("Standalone mode — iTerm2 keybindings skipped")
    log.info("Startup checkpoint: post-keybinding setup")

    # -- Events endpoint (agent hooks) ----------------------------------------

    async def handle_events(request):
        """Receive events from agent hooks (Claude Code HTTP hooks, etc.)."""
        request_started = time.perf_counter()
        profiling.recorder().incr("events_endpoint_received")
        try:
            raw = await request.json()
        except Exception:
            profiling.recorder().incr("events_dropped_invalid_json")
            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)
            return web.json_response({}, status=400)

        headers = {
            "X-Loom-Cell-Id": request.headers.get("X-Loom-Cell-Id", ""),
        }
        envelope = build_event_ingest_envelope(raw, headers=headers)
        idempotency_key = None
        explicit_event_id = raw.get("event_id")
        if not explicit_event_id and isinstance(raw.get("data"), dict):
            explicit_event_id = raw["data"].get("event_id")
        if explicit_event_id:
            idempotency_key = (
                f"events:{headers['X-Loom-Cell-Id']}:{explicit_event_id}"
            )

        try:
            response = await event_ingest_client.append(
                envelope,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            log.exception("Failed to durably enqueue agent event")
            profiling.recorder().incr("events_dropped_ingest_unavailable")
            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)
            return web.json_response(
                {"ok": False, "error": str(exc) or "event ingest unavailable"},
                status=503,
            )
        if response.get("type") == "error":
            profiling.recorder().incr("events_dropped_ingest_error")
            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)
            return web.json_response(
                {"ok": False, "error": response.get("message", "ingest error")},
                status=500,
            )
        profiling.recorder().incr("events_enqueued")

        profiling.recorder().observe_ms(
            "event_endpoint_ms", time.perf_counter() - request_started)

        # Return 200 only after the ingest daemon has committed the event.
        return web.json_response({})

    # -- Panel event helper -------------------------------------------------

    def _panel_event(kind, cell_id, agent_name, group, message,
                     task_id=""):
        """Append a panel event and queue a delta broadcast."""
        pe = panel_log.append(
            kind=kind, cell_id=cell_id, agent_name=agent_name,
            group=group, message=message, task_id=task_id)
        state._emit("event_append", **pe)

    async def _replay_failed_write(write: dict):
        endpoint = str(write.get("endpoint", "") or "")
        payload = dict(write.get("payload", {}) or {})
        if endpoint == "/mcp":
            response, _status = await dispatch_mcp_rpc_body(
                payload,
                cell_id=str(write.get("caller_id", "") or ""),
                handle_command=handle_command,
                state=state,
            )
            if response.get("error"):
                log.info(
                    "Queued MCP write replay reached tool surface with error: %s",
                    response.get("error"),
                )
            return response
        if endpoint == "/api/cmd":
            return await replay_api_failed_write_payload(
                db,
                payload,
                handle_command,
            )
        raise ValueError(f"Unsupported failed-write endpoint: {endpoint}")

    replay_summary = await replay_failed_writes(db, _replay_failed_write)
    if replay_summary.get("attempted"):
        log.info("Failed-write replay summary: %s", replay_summary)

    # -- Scheduler ----------------------------------------------------------

    asyncio.create_task(
        _pump_auto_dispatch_queue_forever(
            state, handle_command, _panel_event
        )
    )
    asyncio.create_task(
        _scheduler_loop(state, handle_command, _panel_event))
    log.info("Task scheduler and auto-dispatch queue pump started")
    log.info("Startup checkpoint: scheduler tasks scheduled")

    # -- HTTP / WS routes ---------------------------------------------------

    async def handle_index(_request):
        from .config import WEBVIEW_FILE  # re-read after init_paths
        return web.FileResponse(WEBVIEW_FILE)

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if not await _register_ready_ui_ws_client(
                state, ws, _state_payload):
            return ws
        # Replay the current supervisor banner (if any) to the new client.
        banner = supervisor_banner_state.get("banner")
        if banner is not None:
            with contextlib.suppress(Exception):
                await ws.send_str(json.dumps({
                    "type": "system_banner",
                    "banner": banner,
                }))
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        result = await handle_command(
                            json.loads(msg.data))
                        if result:
                            if result.get("type") == "state":
                                sent = await _register_ready_ui_ws_client(
                                    state, ws, _state_payload)
                            else:
                                sent = await _send_ui_ws_json(ws, result)
                            if not sent:
                                break
                    except json.JSONDecodeError:
                        log.warning("Received malformed JSON from webview")
                    except Exception:
                        log.exception("Error handling WS command")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            state._ws_clients.discard(ws)
        return ws

    async def handle_terminal_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        cell_id = request.match_info.get("cell_id", "")
        terminal_clients.setdefault(cell_id, set()).add(ws)
        try:
            if not bridge.capabilities.supports_embedded_terminal:
                await ws.send_str(json.dumps({
                    "type": "error",
                    "message": "Embedded terminals are unavailable in this runtime.",
                }))
            cell = state.agents.get(cell_id)
            if cell and cell.session_id:
                await ws.send_str(json.dumps({
                    "type": "snapshot",
                    "cell_id": cell_id,
                    "session_id": cell.session_id,
                    "data": bridge.get_terminal_buffer(cell.session_id),
                }))
            else:
                await ws.send_str(json.dumps({
                    "type": "snapshot",
                    "cell_id": cell_id,
                    "session_id": "",
                    "data": "",
                }))
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                cell = state.agents.get(cell_id)
                if not cell or not cell.session_id:
                    continue
                if payload.get("type") == "input":
                    await bridge.write_input(cell.session_id, payload.get("data", ""))
                elif payload.get("type") == "resize":
                    await bridge.resize_session(
                        cell.session_id,
                        int(payload.get("cols", 0) or 0),
                        int(payload.get("rows", 0) or 0),
                    )
                elif payload.get("type") == "focus":
                    await bridge.focus_session(cell.session_id)
        finally:
            terminal_clients.get(cell_id, set()).discard(ws)
        return ws

    # -- REST API endpoint --------------------------------------------------

    async def handle_api_cmd(request):
        """REST endpoint for CLI and scripting access.

        Accepts the same {"cmd": ..., ...} payloads as the WS handler.
        Returns {"ok": true, "data": ...} on success or
        {"ok": false, "error": "..."} on failure.
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON"}, status=400)

        cmd = data.get("cmd")
        if not cmd:
            return web.json_response(
                {"ok": False, "error": "missing 'cmd'"}, status=400)

        idempotency_key = str(data.get("idempotency_key", "") or "").strip()
        request_hash = ""
        if idempotency_key and is_api_write_command(cmd):
            request_hash = api_request_hash(data)
            existing = db.load_mcp_idempotency(idempotency_key)
            if existing:
                if (
                    str(existing.get("request_hash", "") or "")
                    and str(existing.get("request_hash", "") or "") != request_hash
                ):
                    return web.json_response(
                        {
                            "ok": False,
                            "error": (
                                "idempotency key was reused for a different "
                                f"API command ({cmd})"
                            ),
                        },
                        status=409,
                    )
                try:
                    cached = json.loads(existing.get("response_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    cached = {}
                db.record_mcp_health_event(
                    surface="api",
                    tool_name=str(cmd or ""),
                    event="dedupe",
                )
                return web.json_response(cached)

        try:
            result = await handle_command(data)
        except Exception as exc:
            log.exception("API command '%s' failed", cmd)
            return web.json_response(
                {"ok": False, "error": str(exc)}, status=500)

        if result and result.get("type") == "error":
            return web.json_response(
                {"ok": False, "error": result.get("message", "")})

        payload = result if result else await _state_payload()
        response_payload = {"ok": True, "data": payload}
        if idempotency_key and is_api_write_command(cmd):
            db.save_mcp_idempotency(
                idempotency_key=idempotency_key,
                surface="api",
                tool_name=str(cmd or ""),
                request_hash=request_hash or api_request_hash(data),
                response=response_payload,
            )
        if isinstance(payload, dict) and payload.get("type") == "state":
            return await _hot_json_response(response_payload)
        return web.json_response(response_payload)

    # -- Profile harness endpoints -----------------------------------------

    def _remove_profile_synthetic_agents(group: str, prefix: str) -> int:
        removed = 0
        members = list(state.groups.get(group, []))
        for aid in members:
            cell = state.agents.get(aid)
            if not cell:
                continue
            if (
                    cell.command != "loom-profile-harness"
                    and not cell.name.startswith(prefix)
            ):
                continue
            for child_id in list(state._children.get(aid, [])):
                child = state.agents.pop(child_id, None)
                if child:
                    state._emit("agent_remove", id=child_id,
                                group=child.group,
                                cell_type=child.cell_type)
                    state._db_delete_agent(child_id)
            state._children.pop(aid, None)
            state.agents.pop(aid, None)
            if aid in state.groups.get(group, []):
                state.groups[group].remove(aid)
            state._emit("agent_remove", id=aid,
                        group=group,
                        cell_type=cell.cell_type)
            state._db_delete_agent(aid)
            removed += 1
        if removed:
            state._emit_group(group)
            state._db_save_groups()
        return removed

    async def handle_profile_get(request):
        if not profiling.is_enabled():
            raise web.HTTPNotFound()
        profiling.recorder().set_gauge("agent_count", len(state.agents))
        profiling.recorder().set_gauge("group_count", len(state.groups))
        profiling.recorder().set_gauge("board_task_count", len(state.board_tasks))
        data = profiling.recorder().snapshot()
        limit = int(request.query.get("cprofile_limit", "30") or 30)
        data["cprofile_top"] = profiling.cprofile_top(limit=limit)
        return web.json_response({"ok": True, "data": data})

    async def handle_profile_reset(request):
        if not profiling.is_enabled():
            raise web.HTTPNotFound()
        profiling.reset()
        return web.json_response({"ok": True})

    async def handle_profile_synthetic_agents(request):
        """Create N in-memory/persisted fake cells for the perf harness.

        This deliberately bypasses terminal/session creation.  It is only
        registered when profiling is enabled and is intended for ephemeral
        standalone harness data directories.
        """
        if not profiling.is_enabled():
            raise web.HTTPNotFound()
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON"}, status=400)
        group = str(data.get("group", "perf-harness") or "perf-harness")
        prefix = str(data.get("prefix", "perf-agent") or "perf-agent")
        count = max(0, int(data.get("count", 10) or 0))
        reset_existing = bool(data.get("reset", True))
        removed = 0
        if reset_existing:
            removed = _remove_profile_synthetic_agents(group, prefix)
        if group not in state.groups:
            state.add_group(group)

        directory = str(data.get("directory", "") or DATA_DIR)
        agent_type = str(data.get("agent_type", "claude-code")
                         or "claude-code")
        ids: list[str] = []
        for index in range(count):
            name = f"{prefix}-{index + 1:02d}"
            cell = state.add_agent(
                name=name,
                group=group,
                profile="Synthetic",
                command="loom-profile-harness",
                directory=directory,
            )
            if not cell:
                continue
            cell.terminal_backend = "synthetic"
            cell.session_id = f"synthetic-{uuid.uuid4().hex[:12]}"
            cell.agent_session_id = f"profile-{uuid.uuid4().hex[:12]}"
            cell.agent_type = agent_type
            cell.status = "running"
            cell.current_process = "synthetic-agent"
            cell.current_path = directory
            cell.kind = "worker"
            state._emit_agent(cell)
            state._db_save_agent(cell)
            ids.append(cell.id)

        await state.broadcast()
        profiling.recorder().set_gauge("synthetic_agent_count", len(ids))
        return web.json_response({
            "ok": True,
            "data": {
                "group": group,
                "agent_ids": ids,
                "removed": removed,
            },
        })

    # -- Attachment upload/serve endpoints -----------------------------------

    async def handle_upload(request):
        """POST /api/upload — multipart file upload for task images/artifacts."""
        reader = await request.multipart()
        task_id = ""
        saved = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "task_id":
                task_id = (await part.text()).strip()
            elif part.name == "file":
                if not task_id:
                    return web.json_response(
                        {"ok": False, "error": "task_id must come before file parts"},
                        status=400)
                fname = part.filename or "artifact.bin"
                # Sanitize filename — no spaces (paths with spaces
                # get lost during terminal paste to Claude Code)
                fname = fname.replace("/", "_").replace("\\", "_")
                fname = fname.replace(" ", "_")
                att_dir = ATTACHMENTS_DIR / task_id
                att_dir.mkdir(parents=True, exist_ok=True)
                # Deduplicate: if name exists, add suffix
                dest = att_dir / fname
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    i = 1
                    while dest.exists():
                        dest = att_dir / f"{stem}_{i}{suffix}"
                        fname = dest.name
                        i += 1
                fdata = await part.read()
                dest.write_bytes(fdata)
                mime = part.headers.get(
                    aiohttp.hdrs.CONTENT_TYPE,
                    mimetypes.guess_type(fname)[0] or "application/octet-stream")
                entry = {
                    "path": str(dest),
                    "filename": fname,
                    "mime_type": mime,
                    "size_bytes": len(fdata),
                }
                saved.append(entry)
        if not task_id:
            return web.json_response(
                {"ok": False, "error": "missing task_id"}, status=400)
        # Don't update the task here — the client sends attachments
        # on submit (so Cancel discards uploads properly).
        return web.json_response({"ok": True, "data": saved})

    async def handle_upload_cleanup(request):
        """POST /api/upload/cleanup — remove attachment dir for cancelled drafts."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON"}, status=400)
        task_id = data.get("task_id", "").strip()
        if not task_id:
            return web.json_response(
                {"ok": False, "error": "missing task_id"}, status=400)
        # Only clean up if no task with this ID exists
        if task_id not in state.board_tasks:
            att_dir = ATTACHMENTS_DIR / task_id
            if att_dir.is_dir():
                shutil.rmtree(att_dir, ignore_errors=True)
        return web.json_response({"ok": True})

    async def handle_attachment_upload(request):
        """POST /api/attachment/upload — image drops for agent message compose."""
        try:
            reader = await request.multipart()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid multipart upload"}, status=400)

        agent_id = ""
        saved = []
        try:
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "agent_id":
                    agent_id = (await part.text()).strip()
                elif part.name == "file":
                    if not agent_id:
                        raise AttachmentUploadError(
                            "agent_id must come before file parts", status=400)
                    mime = part.headers.get(
                        aiohttp.hdrs.CONTENT_TYPE,
                        "application/octet-stream")
                    entry = await save_message_attachment_stream(
                        agent_id=agent_id,
                        filename=part.filename or "screenshot",
                        mime_type=mime,
                        stream=part,
                        attachments_dir=ATTACHMENTS_DIR,
                    )
                    saved.append(entry)
        except AttachmentUploadError as exc:
            # Keep multi-file drops all-or-nothing from the endpoint's
            # perspective.  A later invalid/oversized part should not leave
            # earlier files from the same compose drop behind.
            for entry in saved:
                try:
                    Path(entry.get("path", "")).unlink()
                except Exception:
                    pass
            return web.json_response(
                {"ok": False, "error": str(exc)}, status=exc.status)
        except Exception as exc:
            for entry in saved:
                try:
                    Path(entry.get("path", "")).unlink()
                except Exception:
                    pass
            log.exception("Attachment upload failed")
            return web.json_response(
                {"ok": False, "error": str(exc) or "attachment upload failed"},
                status=500)

        if not agent_id:
            return web.json_response(
                {"ok": False, "error": "missing agent_id"}, status=400)
        if not saved:
            return web.json_response(
                {"ok": False, "error": "missing file"}, status=400)
        return web.json_response({"ok": True, "data": saved})

    async def handle_serve_attachment(request):
        """GET /attachments/{task_id}/{filename} — serve attachment file."""
        task_id = request.match_info["task_id"]
        filename = request.match_info["filename"]
        fpath = ATTACHMENTS_DIR / task_id / filename
        if not fpath.is_file():
            raise web.HTTPNotFound()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return web.FileResponse(fpath, headers={
            "Content-Type": mime,
            "Cache-Control": "max-age=3600",
        })

    # -- Orphan attachment cleanup on startup --------------------------------

    def _cleanup_orphan_attachments():
        if not ATTACHMENTS_DIR.is_dir():
            return
        task_ids = set(state.board_tasks.keys())
        for entry in ATTACHMENTS_DIR.iterdir():
            if entry.is_dir() and entry.name not in task_ids:
                shutil.rmtree(entry, ignore_errors=True)
                log.info("Cleaned up orphan attachments for %s", entry.name)

    _cleanup_orphan_attachments()

    # -- Start server -------------------------------------------------------

    app_server = web.Application()
    app_server.router.add_get("/", handle_index)
    app_server.router.add_get("/ws", handle_ws)
    app_server.router.add_get("/ws/terminal/{cell_id}", handle_terminal_ws)
    app_server.router.add_post("/events", handle_events)
    app_server.router.add_post("/api/cmd", handle_api_cmd)
    if profiling.is_enabled():
        app_server.router.add_get("/api/profile", handle_profile_get)
        app_server.router.add_post("/api/profile/reset", handle_profile_reset)
        app_server.router.add_post(
            "/api/profile/synthetic_agents",
            handle_profile_synthetic_agents,
        )
    app_server.router.add_post("/mcp", create_mcp_handler(handle_command, state))
    app_server.router.add_post("/api/upload", handle_upload)
    app_server.router.add_post("/api/upload/cleanup", handle_upload_cleanup)
    app_server.router.add_post("/api/attachment/upload", handle_attachment_upload)
    app_server.router.add_get(
        "/attachments/{task_id}/{filename}", handle_serve_attachment)
    from .config import SCRIPT_DIR
    app_server.router.add_static("/static", SCRIPT_DIR / "static")
    log.info("Startup checkpoint: routes registered")

    runner = web.AppRunner(app_server)
    log.info("Startup checkpoint: AppRunner created")
    try:
        await runner.setup()
        log.info("Startup checkpoint: runner setup complete")
        site = web.TCPSite(runner, BIND_HOST, WS_PORT, reuse_address=True)
        log.info("Startup checkpoint: TCPSite created")
        try:
            await site.start()
        except OSError as exc:
            log.error("Cannot bind port %d: %s — is another instance running?",
                      WS_PORT, exc)
            raise
        log.info("Startup checkpoint: site start complete")
        log.info("HTTP/WS server listening on %s:%d", BIND_HOST, WS_PORT)

        # -- Register toolbelt (skipped in standalone-only mode) ----------------

        if not STANDALONE:
            registered = await bridge.register_web_view_tool(
                display_name="Loom",
                identifier="com.loom.toolbelt",
                reveal_if_already_registered=True,
                url=f"http://127.0.0.1:{WS_PORT}/",
            )
            if registered:
                log.info("Toolbelt webview registered — Loom ready")
            else:
                log.warning("Toolbelt webview registration unavailable")
        else:
            log.info("Standalone mode — toolbelt registration skipped")
            log.info("Open http://127.0.0.1:%d/ in a browser", WS_PORT)

        await asyncio.Future()
    finally:
        for ws_clients in terminal_clients.values():
            for ws_client in list(ws_clients):
                try:
                    await ws_client.close()
                except Exception:
                    pass
        terminal_clients.clear()
        for ws_client in list(state._ws_clients):
            try:
                await ws_client.close()
            except Exception:
                pass
        state._ws_clients.clear()
        try:
            await panel_log.aclose()
        except Exception:
            log.exception("Panel event log shutdown flush failed")
        try:
            await event_ingest_drainer.stop()
        except Exception:
            log.exception("Event ingest drainer shutdown failed")
        try:
            await event_ingest_client.aclose()
        except Exception:
            log.exception("Event ingest client shutdown failed")
        try:
            await bridge.shutdown()
        except Exception:
            log.exception("Terminal adapter shutdown failed")
        try:
            await runner.cleanup()
        except Exception:
            log.exception("HTTP runner cleanup failed")
        try:
            db.close()
        except Exception:
            log.exception("SQLite database close failed")
