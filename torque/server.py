"""aiohttp server, WebSocket command handler, and runtime entry point."""

import asyncio
import contextlib
import hashlib
import json
import mimetypes
import os
import re
import shlex
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
from . import cloud_hooks
from . import config as torque_config
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
from .db import TorqueDB, canonical_user_agent_thread_id
from .deploy_state import architect_deploy_state_payload, capture_deploy_boot_state
from .remote_ingress import ingest_remote_user_agent_message
from .direct_message_mirrors import (
    ask_recipient_is_user,
    ask_task_labels_for_owner_recipient,
    direct_ask_mirror_source_key,
    save_direct_ask_mirror,
    save_direct_ask_reply_mirror,
)
from .doctor import build_doctor_report
from dataclasses import asdict
from .state import (
    ARCHIVED_LANE,
    ArchitectSettings,
    BoardTask,
    EngineerSettings,
    COMPACT_SNAPSHOT_PROTOCOL,
    MatrixState,
    hot_json_dumps_async,
    hot_json_dumps_bytes_async,
    merge_cleanup_flags,
    normalize_architect_review_gate_thresholds,
    normalize_default_worker_concurrency,
    normalize_engineer_merge_mode,
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
from .event_ingest_db import event_call_row_from_record, redact_event_for_mcp_call_log
from .adapters import get_adapter, get_providers
from .adapters.base import AgentEvent
from .notifications import NotificationManager
from .worktree import (
    WorktreeManager,
    classify_task_scope_domain,
    format_stale_base_warning,
)
from .worktree_boundaries import (
    boundary_submodule_branches,
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
from .actions import (
    ActionManager,
    DEFAULT_REVIEW_REQUIRED_ABOVE_LOC,
    TORQUE_CONTEXT_STUB,
)
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
from .server_board_sync import BoardSyncManager
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
from .specializations import SpecializationManager
from .external_tickets import (
    ExternalTicketError,
    build_completion_comment,
    import_ticket as import_external_ticket,
    normalize_link as normalize_external_link,
    open_ticket_url,
    post_ticket_comment,
    push_ticket_status,
)
from .board_sync import get_provider as get_board_sync_provider
from .mcp import create_mcp_handler, dispatch_mcp_rpc_body
from .mcp_retry import api_request_hash, is_api_write_command, replay_failed_writes
from .identity import (
    agent_identity_anchor,
    agent_kind_for_identity,
    prepend_agent_identity_anchor,
)

GUIDANCE_HINT_USER_DIRECT_REPLY = "user_message.reply_hint"
GUIDANCE_HINT_IDENTITY_DISPATCH = "agent_identity_anchor.dispatch"
GUIDANCE_HINT_IDENTITY_LAUNCH = "agent_identity_anchor.launch"

from .server_actions import _action_to_yaml
from .server_agent import (
    AgentLaunchService,
    _append_task_artifacts,
    _build_self_dispatch_prompt,
    _copy_worktree_context,
    _new_agent_prompt_sequence,
    _startup_prompt_for_new_agent,
    mcp_entrypoint_for_cell,
    resolve_default_boot_nudge,
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
from .server_supervisor import (
    build_supervisor_sessions_payload,
    build_supervisor_terminate_payload,
)
from .server_worktrees import (
    _append_pr_url_to_squash_body,
    _collect_linked_github_issues,
    _generate_merge_message,
    _pr_merge_failure_allows_auto,
    _pr_result_metadata,
    _record_pr_metadata_on_latest_boundary,
    _split_merge_message_for_pr,
    _worktree_diff_updater,
    _worktree_full_diff,
    _worktree_merge_diff_snapshot,
)
from .worktree_streams import compute_worktree_stream
from .server_prompts import (
    build_dispatch_postscript,
    build_torque_system_prompt,
    compute_commit_hint,
    deliverable_word,
)
from .engineer_session_map import build_engineer_session_map


def _read_torque_version() -> str:
    candidates = [
        torque_config.SCRIPT_DIR / "VERSION",
        torque_config.SCRIPT_DIR.parent / "VERSION",
        Path(__file__).resolve().parents[1] / "VERSION",
    ]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "unknown"


_STARTED_AT: float = time.time()
_TORQUE_VERSION: str = _read_torque_version()
_LOG_LINE_RE = re.compile(
    r"^(?P<clock>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<message>.*)$"
)
_LOG_TAIL_BYTES = 256 * 1024
_LOG_MAX_LINES = 500


def _runtime_payload(*, bridge=None, state=None) -> dict:
    runtime_mode = (
        "desktop"
        if os.environ.get("TORQUE_DESKTOP_MODE", "").strip()
        else "standalone"
    )
    capabilities = getattr(bridge, "capabilities", None)
    embedded_terminal = bool(
        getattr(capabilities, "supports_embedded_terminal", False)
    )
    if state is not None and hasattr(state, "get_default_command"):
        default_command = state.get_default_command()
    else:
        default_command = torque_config.DEFAULT_COMMAND
    return {
        "mode": runtime_mode,
        "standalone": STANDALONE,
        "embedded_terminal": embedded_terminal,
        "layout": "ide" if embedded_terminal else "classic",
        "terminal_backend": "pty",
        "home_directory": str(Path.home()),
        "profile": os.environ.get("TORQUE_PROFILE", "").strip(),
        "data_dir": str(DATA_DIR),
        "port": WS_PORT,
        "default_command": default_command,
        "version": _TORQUE_VERSION,
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "log_path": str(DATA_DIR / "torque.log"),
    }


def _parse_log_line(line: str, *, today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    text = line.rstrip("\n")
    match = _LOG_LINE_RE.match(text)
    if not match:
        return {
            "ts": 0.0,
            "level": "",
            "logger": "torque",
            "message": text,
            "raw": text,
        }
    hour, minute, second = [
        int(part) for part in match.group("clock").split(":")
    ]
    stamped = today.replace(
        hour=hour,
        minute=minute,
        second=second,
        microsecond=0,
    )
    return {
        "ts": stamped.timestamp(),
        "level": match.group("level").strip(),
        "logger": "torque",
        "message": match.group("message"),
        "raw": text,
    }


def _tail_log_entries(
    log_path: Path,
    *,
    since: float = 0.0,
    limit: int = _LOG_MAX_LINES,
    tail_bytes: int = _LOG_TAIL_BYTES,
) -> dict:
    limit = max(1, min(int(limit or _LOG_MAX_LINES), 2000))
    try:
        stat = log_path.stat()
    except FileNotFoundError:
        return {
            "lines": [],
            "cursor": time.time(),
            "size": 0,
            "inode": "",
            "path": str(log_path),
        }
    start = max(0, stat.st_size - max(4096, int(tail_bytes or _LOG_TAIL_BYTES)))
    with log_path.open("rb") as handle:
        handle.seek(start)
        if start:
            handle.readline()  # discard a partial first line
        raw = handle.read()
    today = datetime.now(timezone.utc)
    entries = []
    for raw_line in raw.decode("utf-8", "replace").splitlines():
        entry = _parse_log_line(raw_line, today=today)
        if since and entry["ts"] and entry["ts"] <= since:
            continue
        entries.append(entry)
    if len(entries) > limit:
        entries = entries[-limit:]
    cursor = max([entry.get("ts", 0.0) for entry in entries] + [since, time.time()])
    inode = str(getattr(stat, "st_ino", "") or "")
    return {
        "lines": entries,
        "cursor": cursor,
        "size": stat.st_size,
        "inode": inode,
        "path": str(log_path),
    }


def _resolve_pending_engineer_specializations(
        data: dict, state, group: str, is_engineer: bool) -> list:
    """Resolve the specializations list applied to a new engineer.

    Honors an explicit ``specializations`` field in ``data`` (including an
    explicit empty list, which means "no specs"). When the field is absent,
    falls back to the group-level default
    (``GroupSettings.default_engineer_specializations``).

    Returns ``[]`` for non-engineer agents.
    """
    if not is_engineer:
        return []
    if "specializations" in data:
        return [
            str(item or "").strip()
            for item in (data.get("specializations") or [])
            if str(item or "").strip()
        ]
    gs_default = state.get_group_settings(group)
    return [
        str(item or "").strip()
        for item in (
            getattr(gs_default, "default_engineer_specializations", None) or []
        )
        if str(item or "").strip()
    ]


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


def _promote_suggested_action(state, task):
    """Promote ``suggested_action`` -> ``action_name`` when empty.

    Architects set ``suggested_action`` as a non-binding hint. Workers
    only inherit the action's deliverable contract / template / transitions
    once ``action_name`` is recorded on the task. If the dispatch flow
    skips this promotion, contract enforcement is silently bypassed
    (TORQUE:262). Returns the (possibly refreshed) task.
    """
    if not task:
        return task
    if str(getattr(task, "action_name", "") or "").strip():
        return task
    suggested = str(getattr(task, "suggested_action", "") or "").strip()
    if not suggested:
        return task
    state.board_update_task(task.id, action_name=suggested)
    return state.board_tasks.get(task.id) or task


def _resolve_agent_id(state, identifier: str) -> str:
    """Resolve an agent by exact ID, slug, name, or ID prefix."""
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    if ident in state.agents:
        cell = state.agents[ident]
        if cell.cell_type == "agent" and not state.agent_is_tombstoned(cell):
            return cell.id
    ident_lower = ident.lower()
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if cell.slug == ident_lower:
            return cell.id
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if cell.name.lower() == ident_lower:
            return cell.id
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if cell.id.startswith(ident):
            return cell.id
    return ""


def _relay_agent_roster(state: MatrixState) -> list[dict]:
    """Return the group-scoped, non-tombstoned agent roster for relay snapshots."""
    group = str(getattr(state, "active_group", "") or "").strip()
    if not group:
        groups = [
            str(name or "").strip()
            for name in getattr(state, "groups", {}).keys()
            if str(name or "").strip()
        ]
        if len(groups) == 1:
            group = groups[0]
    if not group:
        return []
    roster: list[dict] = []
    for cell in getattr(state, "agents", {}).values():
        if state.agent_is_tombstoned(cell):
            continue
        if str(getattr(cell, "group", "") or "") != group:
            continue
        roster.append({
            "id": getattr(cell, "id", ""),
            "name": getattr(cell, "name", ""),
            "kind": getattr(cell, "kind", ""),
        })
    return roster


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
    cleanup_requested: bool,
) -> dict:
    """Move a sole active linked task to Done after a successful merge."""
    decision = {"moved": False, "task_id": "", "reason": ""}

    if not enabled:
        decision["reason"] = "disabled by caller"
    elif not cleanup_requested:
        decision["reason"] = "merge cleanup not requested"
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
            state.board_move_task(task.id, "Done")
            if task.status:
                task.status = ""
                task.updated_at = datetime.now(timezone.utc).isoformat()
                state._emit("task_upsert", **asdict(task))
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
    try:
        changed = await worktree_mgr.reconcile_worktree_branch(cell)
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

    if boundary_state.get("latest") and not boundary_state.get("clean"):
        return {
            "ok": False,
            "boundary_state": boundary_state,
            "result": _worktree_merge_error(
                aid,
                boundary_reason_message(
                    boundary_state.get("reason", ""),
                    boundary_state.get("latest"),
                ),
            ),
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
                "workflow_breach": stale_base_override_event,
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
                "workflow_breach": stale_base_override_event,
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
                "workflow_breach": stale_base_override_event,
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
                "workflow_breach": stale_base_override_event,
                "result": result,
            }

    return {
        "ok": True,
        "boundary_state": boundary_state,
        "stale_base": stale_base,
        "precheck": precheck,
        "workflow_breach": stale_base_override_event,
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


async def _finalize_successful_worktree_merge(
    *,
    state: MatrixState,
    cell,
    aid: str,
    data: dict,
    merge_sha: str,
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
    mark_branch_boundaries_merged(cell, merge_sha)
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
    )
    if not gates.get("ok"):
        result = gates.get("result") or _worktree_merge_error(
            aid,
            "Merge preflight failed.",
        )
        if gates.get("workflow_breach") and isinstance(result, dict):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    squash = cell.worktree_merge_squash
    msg = str(data.get("message", "") or "").strip()
    if not msg:
        msg = await _generate_merge_message(
            cell,
            worktree_mgr,
            squash,
            state=state,
        )
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
    worktree_submodules = _configured_worktree_submodules_for_cell(state, cell)
    merge_result = (
        await worktree_mgr.server_merge(
            cell,
            msg,
            squash=squash,
            worktree_submodules=worktree_submodules,
        )
        if worktree_submodules
        else await worktree_mgr.server_merge(cell, msg, squash=squash)
    )
    if merge_result.get("ok"):
        result = await _finalize_successful_worktree_merge(
            state=state,
            cell=cell,
            aid=aid,
            data=data,
            merge_sha=merge_result["sha"],
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
    return result


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
) -> dict:
    if not (cell and cell.worktree_path and cell.worktree_branch):
        return _worktree_merge_error(aid, "Agent has no worktree.")

    wt = cell.worktree_path
    repo_root = cell.worktree_repo_root or cell.git_root or ""
    branch = cell.worktree_branch or ""
    base_branch = cell.worktree_base_branch or "main"

    preflight = await worktree_mgr.github_preflight(wt)
    if not preflight.get("ok"):
        return _worktree_merge_error(
            aid,
            preflight.get("error", "GitHub PR preflight failed."),
            mode="pull_request",
            phase=preflight.get("phase", "github_preflight"),
        )

    remote_info = await worktree_mgr.github_select_remote(wt)
    if not remote_info.get("ok"):
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
        publish_nested_submodule_branches=True,
    )
    if not gates.get("ok"):
        result = gates.get("result") or _worktree_merge_error(
            aid,
            "Merge preflight failed.",
        )
        if isinstance(result, dict):
            result["mode"] = "pull_request"
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
        return result

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
    close_issues_via_pr = _github_pr_closing_refs_enabled(group_settings)
    linked_issues: list[dict] = []
    if close_issues_via_pr:
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
    if worktree_submodules:
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
        nested_merge_result = await merge_nested(
            cell,
            worktree_submodules,
            message=msg,
        )
        if not nested_merge_result.get("ok"):
            result = _worktree_merge_error(
                aid,
                nested_merge_result.get(
                    "error",
                    "Nested submodule merge failed.",
                ),
                mode="pull_request",
                phase="nested_submodule_merge",
                nested_submodules=nested_merge_result,
            )
            _attach_stale_base(result, gates.get("stale_base"))
            if gates.get("workflow_breach"):
                result["workflow_breach"] = gates["workflow_breach"]
            return result

        # The nested merge can add a final superproject gitlink commit. Re-run
        # the cheap superproject conflict/overwrite guards against that final
        # branch tip before publishing the PR branch.
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

    if close_issues_via_pr and linked_issues and pr_result.get("existing"):
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
        status="created",
    )
    _record_pr_metadata_on_latest_boundary(
        state,
        cell,
        pr_metadata,
        requested_cleanup=requested_cleanup,
    )
    squash_body = _append_pr_url_to_squash_body(
        body,
        str(pr_result.get("url") or pr_metadata.get("url") or ""),
    )

    head_sha = str(pr_result.get("head_sha") or "").strip()
    if not head_sha:
        current_head = getattr(worktree_mgr, "current_head", None)
        if callable(current_head):
            head_sha = await current_head(cell) or ""
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
    _record_pr_metadata_on_latest_boundary(
        state,
        cell,
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

    post_merge_sync = await worktree_mgr.github_sync_remote_base(
        wt,
        repo_root or wt,
        remote,
        base_branch,
    )
    if not post_merge_sync.get("ok"):
        result = _worktree_merge_error(
            aid,
            post_merge_sync.get(
                "error",
                "Pull request merged but local base sync failed.",
            ),
            mode="pull_request",
            phase=post_merge_sync.get("phase", "remote_base_sync"),
            url=pr_metadata.get("url", ""),
            pr_url=pr_metadata.get("url", ""),
            pr=pr_metadata,
            sha=merge_sha,
            remote_base_sync=post_merge_sync,
        )
        _attach_auto_force_push_metadata(result, push_metadata_result)
        if gates.get("workflow_breach"):
            result["workflow_breach"] = gates["workflow_breach"]
        return result

    result = await _finalize_successful_worktree_merge(
        state=state,
        cell=cell,
        aid=aid,
        data=data,
        merge_sha=merge_sha,
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
    _attach_auto_force_push_metadata(result, push_metadata_result)
    if gates.get("workflow_breach"):
        result["workflow_breach"] = gates["workflow_breach"]
    return result


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


def _scope_domain_for_cell(state: MatrixState, cell) -> str | None:
    """Resolve a cell's declared scope domain for the out-of-scope diff flag.

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
    return classify_task_scope_domain(
        specialization=getattr(task, "suggested_specialization", "") or "",
        labels=getattr(task, "labels", None),
        description=getattr(task, "description", "") or "",
    )


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


def _ai_derive_parent_task(state: MatrixState, task):
    """Return the structural parent for a newly derived ``torque ai`` task.

    Review tasks hand fixes back to their implementation parent.  Making the
    fix task a child of the implementer task keeps dispatch worktree
    inheritance on the implementer's branch instead of walking through the
    reviewer task/agent.
    """
    if not state or not task:
        return None
    if _is_feature_review_task(task):
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        parent = state.board_tasks.get(parent_id) if parent_id else None
        if parent:
            return parent
    return task


def _resolve_inherited_worktree_source(
    state: MatrixState,
    task,
    inherit_from: str = "",
):
    """Resolve the agent whose worktree should seed a new task dispatch."""
    if not state or not task:
        return None
    inherit_from = str(inherit_from or "").strip()
    if inherit_from:
        src = state.agents.get(inherit_from)
        if src and getattr(src, "worktree_path", ""):
            return src
        return None

    # HITL dispatch: walk parent chain to find the worktree before launching
    # the session, so derived reviewers/fixes do not briefly create and run
    # inside a throwaway branch.
    parent_task_id = str(getattr(task, "parent_task_id", "") or "").strip()
    seen = set()
    while parent_task_id and parent_task_id not in seen:
        seen.add(parent_task_id)
        parent_task = state.board_tasks.get(parent_task_id)
        if not parent_task:
            break
        parent_agent_id = str(
            getattr(parent_task, "agent_id", "") or ""
        ).strip()
        if parent_agent_id:
            parent_agent = state.agents.get(parent_agent_id)
            if parent_agent and getattr(parent_agent, "worktree_path", ""):
                return parent_agent
        parent_task_id = str(
            getattr(parent_task, "parent_task_id", "") or ""
        ).strip()
    return None


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


def _prior_live_reviewer_agent_for_chain(state: MatrixState, task):
    """Find the most recent live feature/review agent in ``task``'s chain."""
    if not state or not task:
        return None
    for prior in reversed(state.board_get_chain(task.id)):
        if prior.id == task.id:
            continue
        action_name = str(
            getattr(prior, "action_name", "") or ""
        ).strip().lower()
        if action_name != _REVIEW_GATE_ACTION:
            continue
        agent_id = str(getattr(prior, "agent_id", "") or "").strip()
        if not agent_id:
            continue
        agent = state.agents.get(agent_id)
        if _agent_can_receive_dispatch(agent):
            return agent
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

    TORQUE:88 intentionally launches feature/review workers in the
    implementer's worktree. During that review window, the implementer is a
    suspended ancestor in the task graph, so Torque-originated checkpoint writes
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
    status_verdict = _review_verdict_from_message(
        getattr(task, "status", "") or ""
    )
    if status_verdict:
        return status_verdict == "ship"
    return False


_REVIEW_GATE_ACTION = "feature/review"


def _coerce_action_bool(value) -> bool:
    """Return a conservative boolean for action YAML metadata."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _action_is_implementation_depth(act: dict | None) -> bool:
    """Return whether an action represents code-mutating implementation work."""
    if not isinstance(act, dict):
        return False
    if "implementation_depth" in act:
        return _coerce_action_bool(act.get("implementation_depth"))
    # Backward compatibility: pre-field actions that already opted into the
    # LOC review gate should keep their existing behavior until edited.
    return "review_required_above_loc" in act


def _review_gate_threshold_from_action(act: dict | None) -> int | None:
    """Return an implementation action's review-required LOC threshold."""
    if not _action_is_implementation_depth(act):
        return None
    if "review_required_above_loc" not in act:
        return DEFAULT_REVIEW_REQUIRED_ABOVE_LOC
    try:
        threshold = int(act.get("review_required_above_loc"))
    except (TypeError, ValueError):
        return None
    return threshold if threshold >= 0 else None


def _explicit_review_gate_threshold_from_action(act: dict | None) -> int | None:
    """Return only the explicitly configured action-level LOC threshold."""
    if not _action_is_implementation_depth(act):
        return None
    if "review_required_above_loc" not in act:
        return None
    try:
        threshold = int(act.get("review_required_above_loc"))
    except (TypeError, ValueError):
        return None
    return threshold if threshold >= 0 else None


def _review_gate_policy_from_loc_gate(loc_gate, *, source: str,
                                      action: str = "") -> dict | None:
    """Normalize a transition-local LOC gate block into a policy dict."""
    if not isinstance(loc_gate, dict):
        return None
    thresholds = normalize_architect_review_gate_thresholds(loc_gate)
    ship_direct_max = int(thresholds.get("ship_direct_max", 0) or 0)
    review_default_above = int(
        thresholds.get("review_default_above", DEFAULT_REVIEW_REQUIRED_ABOVE_LOC)
        or 0
    )
    return {
        "source": source,
        "action": action,
        "threshold": max(ship_direct_max, review_default_above),
        "ship_direct_max": ship_direct_max,
        "review_default_above": review_default_above,
        "self_review_bypass_allowed": bool(
            thresholds.get("self_review_bypass_allowed", False)
        ),
        "controls_self_review_bypass": True,
    }


def _review_gate_transition_policy(act: dict | None) -> dict | None:
    """Return the first feature/review transition-local LOC gate policy."""
    if not isinstance(act, dict):
        return None
    transitions = act.get("transitions") or []
    if not isinstance(transitions, list):
        return None
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        action = str(transition.get("action", "") or "").strip()
        if action.lower() != _REVIEW_GATE_ACTION:
            continue
        if "loc_gate" not in transition:
            continue
        policy = _review_gate_policy_from_loc_gate(
            transition.get("loc_gate"),
            source="transition",
            action=action,
        )
        if policy:
            return policy
    return None


def _review_gate_policy_from_action_threshold(
        threshold: int | None) -> dict | None:
    if threshold is None:
        return None
    return {
        "source": "action",
        "threshold": threshold,
        "controls_self_review_bypass": False,
    }


def _review_gate_task_chain(state: MatrixState, task) -> list:
    """Return the root→leaf-ish chain available for review-gate scoping."""
    if not state or not task:
        return []
    task_id = str(getattr(task, "id", "") or "").strip()
    if not task_id:
        return [task]
    try:
        chain = list(state.board_get_chain(task_id) or [])
    except Exception:
        chain = []
    if task not in chain:
        chain.append(task)
    return chain


def _review_gate_architect_id(state: MatrixState, task, cell=None) -> str:
    """Return the architect whose settings should shape this review gate."""
    for chain_task in _review_gate_task_chain(state, task):
        architect_id = str(
            getattr(chain_task, "created_by_architect_id", "") or ""
        ).strip()
        if architect_id:
            return architect_id

    for chain_task in _review_gate_task_chain(state, task):
        engineer_id = str(
            getattr(chain_task, "assigned_engineer_id", "") or ""
        ).strip()
        engineer = state.agents.get(engineer_id) if engineer_id else None
        architect_id = str(
            getattr(engineer, "hired_by_architect_id", "") or ""
        ).strip()
        if architect_id:
            return architect_id

    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip() or str(
        getattr(cell, "created_by_engineer_id", "") or ""
    ).strip()
    owner = state.agents.get(owner_id) if owner_id else None
    return str(getattr(owner, "hired_by_architect_id", "") or "").strip()


def _review_gate_architect_policy(state: MatrixState, task, cell=None) -> dict | None:
    """Return architect-configured review-gate thresholds for scoped work."""
    architect_id = _review_gate_architect_id(state, task, cell)
    if not architect_id:
        return None
    architect = state.agents.get(architect_id)
    group = str(
        getattr(architect, "group", "") or getattr(task, "group", "") or ""
    ).strip()
    if not group or not hasattr(state, "get_architect_settings"):
        return None
    settings = state.get_architect_settings(group)
    thresholds = normalize_architect_review_gate_thresholds(
        getattr(settings, "architect_review_gate_thresholds", {})
    )
    ship_direct_max = int(thresholds.get("ship_direct_max", 0) or 0)
    review_default_above = int(
        thresholds.get("review_default_above", DEFAULT_REVIEW_REQUIRED_ABOVE_LOC)
        or 0
    )
    return {
        "source": "architect",
        "architect_id": architect_id,
        "threshold": max(ship_direct_max, review_default_above),
        "ship_direct_max": ship_direct_max,
        "review_default_above": review_default_above,
        "self_review_bypass_allowed": bool(
            thresholds.get("self_review_bypass_allowed", False)
        ),
        "controls_self_review_bypass": True,
    }


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


def _feature_review_transition_is_mandatory(transition) -> bool:
    if not isinstance(transition, dict):
        return str(transition or "").strip().lower() == _REVIEW_GATE_ACTION
    action = str(transition.get("action", "") or "").strip().lower()
    if action != _REVIEW_GATE_ACTION:
        return False
    # oneshot/* actions use a feature/review transition for the optional
    # diff-size review gate.  That is not the mandatory feature pipeline
    # closeout contract this guard enforces.
    when = str(transition.get("when", "") or "").strip().lower()
    if "review gate threshold" in when or "diff exceeded" in when:
        return False
    return True


def _action_requires_mandatory_feature_review(
        action_mgr: ActionManager | None,
        action_name: str,
        base_dir: str = "") -> bool:
    action_name = str(action_name or "").strip()
    if not action_name:
        return False
    if action_name.lower() == "feature/implement":
        return True
    if not action_mgr:
        return False
    try:
        transitions = action_mgr.get_transitions(action_name, base_dir) or []
    except Exception:
        return False
    return any(
        _feature_review_transition_is_mandatory(transition)
        for transition in transitions
    )


def _task_is_pipeline_descendant(
        state: MatrixState,
        task,
        candidate) -> bool:
    if not state or not task or not candidate:
        return False
    task_id = str(getattr(task, "id", "") or "").strip()
    candidate_id = str(getattr(candidate, "id", "") or "").strip()
    if not task_id or not candidate_id or candidate_id == task_id:
        return False

    task_root_id = str(
        getattr(task, "pipeline_root_id", "") or task_id
    ).strip()
    candidate_root_id = str(
        getattr(candidate, "pipeline_root_id", "") or candidate_id
    ).strip()
    if task_root_id and candidate_root_id and task_root_id != candidate_root_id:
        return False

    if not str(getattr(task, "parent_task_id", "") or "").strip():
        return candidate_root_id == task_id

    seen = set()
    parent_id = str(getattr(candidate, "parent_task_id", "") or "").strip()
    while parent_id and parent_id not in seen:
        if parent_id == task_id:
            return True
        seen.add(parent_id)
        parent = state.board_tasks.get(parent_id)
        if not parent:
            break
        parent_id = str(getattr(parent, "parent_task_id", "") or "").strip()
    return False


def _task_has_shipped_review_descendant(state: MatrixState, task) -> bool:
    """Return whether ``task`` has a closed descendant Ship review."""
    if not state or not task:
        return False
    for chain_task in state.board_get_chain(task.id):
        if not _task_is_pipeline_descendant(state, task, chain_task):
            continue
        if not task_counts_as_done(chain_task):
            continue
        if not _is_feature_review_task(chain_task):
            continue
        if _review_task_has_ship_verdict(chain_task):
            return True
    return False


def _mandatory_review_done_error(task, action_name: str) -> str:
    title = str(getattr(task, "task", "") or "").strip()
    if not title:
        title = str(getattr(task, "id", "") or "task").strip()
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    action_name = str(action_name or "").strip() or "unknown"
    return (
        "This is a mandatory-review task "
        f"(action={action_name}). Direct `torque_done(...)` is blocked — "
        "derive `feature/review` first, then the reviewer's Ship verdict "
        "triggers cascade-done. Use:\n\n"
        f"  torque_derive(description=\"Review {title}\", action=\"feature/review\")"
    )


def _task_has_matching_deliverable_artifact(task) -> bool:
    """Return True if ``task`` already has a matching artifact attached.

    The match rule is intentionally lenient: when ``deliverable_type`` is
    empty or ``other``, ANY artifact/attachment satisfies the gate;
    otherwise the artifact's ``type`` (or ``artifact_type``) must equal
    ``deliverable_type``.
    """
    if not task:
        return False
    expected = str(getattr(task, "deliverable_type", "") or "").strip().lower()
    accept_any = expected in ("", "other")
    candidates = []
    candidates.extend(getattr(task, "artifacts", None) or [])
    candidates.extend(getattr(task, "attachments", None) or [])
    for entry in candidates:
        if not isinstance(entry, dict):
            return True if accept_any else False
        atype = str(
            entry.get("type", "")
            or entry.get("artifact_type", "")
            or ""
        ).strip().lower()
        if accept_any:
            return True
        if atype == expected:
            return True
    return False


def _reject_missing_deliverable(task, action_label: str) -> dict | None:
    """Reject ``torque_done`` / ``torque_ready`` when the deliverable is missing.

    ``action_label`` is the verb shown in the error message (``done`` or
    ``ready``).
    """
    if not task or not getattr(task, "deliverable_required", False):
        return None
    if _task_has_matching_deliverable_artifact(task):
        return None
    type_label = (
        str(getattr(task, "deliverable_type", "") or "").strip()
        or "any"
    )
    title_default = (
        str(getattr(task, "deliverable_artifact_title", "") or "").strip()
        or str(getattr(task, "task", "") or "").strip()
        or "deliverable"
    )
    artifact_type = (
        str(getattr(task, "deliverable_type", "") or "").strip()
        or "generated_doc"
    )
    word = deliverable_word(getattr(task, "deliverable_type", ""))
    return {
        "type": "deliverable_missing",
        "message": (
            f"Cannot mark task {action_label}: deliverable required "
            f"(type={type_label}) but no matching artifact attached. "
            f"Call `torque_task_upload_artifact(content_text=\"<your full "
            f"{word}>\", artifact_type=\"{artifact_type}\", "
            f"title=\"{title_default}\")` first, then retry "
            f"torque_{action_label}."
        ),
    }


def _reject_pending_review(task, action_label: str) -> dict | None:
    """Reject ``torque_done`` / ``torque_ready`` when a structural review is
    required and no reviewer-issued bypass is set (TORQUE:256).

    Workers cannot self-grant the bypass: ``pre_approved_by`` is set only
    when a reviewer derives a fix transition that declares
    ``pre_approved: true``. Workers must derive the required transition
    (e.g. ``feature/review``) and let cascade-done close the parent task.

    ``action_label`` is the verb shown in the error message (``done`` or
    ``ready``).
    """
    if not task or not getattr(task, "requires_review", False):
        return None
    if str(getattr(task, "pre_approved_by", "") or "").strip():
        return None
    title = str(getattr(task, "task", "") or "").strip()
    if not title:
        title = str(getattr(task, "id", "") or "task").strip()
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    return {
        "type": "review_required",
        "message": (
            f"Cannot mark task {action_label}: review required by action "
            "contract. This task carries requires_review=true (declared by "
            "its action's transitions[required: true]). Derive the review "
            f"transition before calling torque_{action_label}; the reviewer's "
            "Ship verdict will cascade-close the parent. If this is a fix "
            "to a previously-reviewed change, the reviewer must derive via "
            "a `pre_approved: true` transition to grant a structural "
            "bypass — workers cannot self-grant it.\n\n"
            f"  torque_derive(description=\"Review {title}\", "
            "action=\"feature/review\")"
        ),
    }


def _reject_mandatory_review_done_without_ship(
        state: MatrixState,
        action_mgr: ActionManager | None,
        cell,
        task,
        *,
        base_dir: str = "") -> dict | None:
    """Reject worker direct-done on mandatory review-pipeline tasks."""
    if not state or not cell or not task:
        return None
    if agent_kind_for_identity(cell) != "worker":
        return None
    # Reviewer-issued pre-approval bypass (TORQUE:256) — when the parent
    # review derived this task via a ``pre_approved: true`` transition,
    # the structural flag overrides this heuristic gate too. Workers
    # cannot self-grant the bypass; ``pre_approved_by`` is set only by
    # ``ai_report.derive`` when the chosen transition carries the flag.
    if str(getattr(task, "pre_approved_by", "") or "").strip():
        return None

    action_name = str(getattr(task, "action_name", "") or "").strip()
    if not _action_requires_mandatory_feature_review(
            action_mgr, action_name, base_dir):
        return None
    if _task_has_shipped_review_descendant(state, task):
        return None
    return {
        "type": "error",
        "message": _mandatory_review_done_error(task, action_name),
    }


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
    # Reviewer-issued pre-approval bypass (TORQUE:256) — a derived fix
    # task carrying ``pre_approved_by`` skips the LOC gate too, since the
    # reviewer already determined the change ships without re-review.
    if str(getattr(task, "pre_approved_by", "") or "").strip():
        return None

    act = action_mgr.load_action(task.action_name, base_dir)
    if not _action_is_implementation_depth(act):
        return None
    transition_policy = _review_gate_transition_policy(act)
    action_policy = _review_gate_policy_from_action_threshold(
        _explicit_review_gate_threshold_from_action(act)
    )
    architect_policy = _review_gate_architect_policy(state, task, cell)
    gate_policy = transition_policy or action_policy
    if (
            gate_policy
            and gate_policy.get("source") == "action"
            and architect_policy):
        # Action-level review_required_above_loc is a legacy threshold-only
        # setting.  Preserve existing architect control over whether workers
        # may self-review-bypass while still letting the action threshold win.
        gate_policy = dict(gate_policy)
        gate_policy["controls_self_review_bypass"] = True
        gate_policy["self_review_bypass_allowed"] = bool(
            architect_policy.get("self_review_bypass_allowed", False)
        )
        gate_policy["bypass_source"] = "architect"
        gate_policy["bypass_architect_id"] = architect_policy.get(
            "architect_id", "")
    if not gate_policy:
        gate_policy = architect_policy
    if not gate_policy:
        gate_policy = _review_gate_policy_from_action_threshold(
            _review_gate_threshold_from_action(act)
        )
        if gate_policy:
            gate_policy["source"] = "default"
    if not gate_policy:
        return None
    threshold = int(gate_policy.get("threshold", 0) or 0)

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

    if (
            force_skip_review
            and gate_policy.get("controls_self_review_bypass")
            and not gate_policy.get("self_review_bypass_allowed")):
        force_skip_review = False
        bypass_source = (
            gate_policy.get("bypass_source")
            or gate_policy.get("source", "review-gate")
        )
        skip_reason = (
            "self-review bypass disabled by "
            f"{bypass_source} settings"
        )

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
    if gate_policy.get("source") == "transition":
        context += (
            "- Transition LOC gate: "
            f"action={gate_policy.get('action') or _REVIEW_GATE_ACTION}, "
            f"ship_direct_max={gate_policy['ship_direct_max']}, "
            f"review_default_above={gate_policy['review_default_above']}, "
            "self_review_bypass_allowed="
            f"{gate_policy['self_review_bypass_allowed']}\n"
        )
        if skip_reason:
            context += f"- Skip request ignored: {skip_reason}\n"
    elif gate_policy.get("source") == "architect":
        context += (
            "- Architect review policy: "
            f"architect={gate_policy['architect_id']}, "
            f"ship_direct_max={gate_policy['ship_direct_max']}, "
            f"review_default_above={gate_policy['review_default_above']}, "
            "self_review_bypass_allowed="
            f"{gate_policy['self_review_bypass_allowed']}\n"
        )
        if skip_reason:
            context += f"- Skip request ignored: {skip_reason}\n"
    elif skip_reason:
        context += f"- Skip request ignored: {skip_reason}\n"
    derive_result = await handle_command({
        "cmd": "ai_report",
        "cell_id": cell.id,
        "action": "derive",
        "task_id": task.id,
        "action_name": _REVIEW_GATE_ACTION,
        "message": title,
        "description": context,
        "_review_gate": True,
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
            "before calling `torque_done(...)` again."
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
            if _agent_has_pending_engineer_followups(state, agent_id):
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


_WORKTREE_REMOVAL_FRESH_AGENT_SECONDS = 5 * 60


def _timestamp_to_unix(value) -> float:
    if isinstance(value, (int, float)):
        return float(value or 0.0)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _worktree_path_contains(path: str, candidate: str) -> bool:
    path = str(path or "").strip()
    candidate = str(candidate or "").strip()
    if not path or not candidate:
        return False
    try:
        root = os.path.realpath(os.path.expanduser(path))
        child = os.path.realpath(os.path.expanduser(candidate))
        return os.path.commonpath([root, child]) == root
    except Exception:
        return path == candidate or candidate.startswith(path.rstrip("/") + "/")


def _worktree_entry_matches_agent(repo_root: str, path: str, agent) -> bool:
    """Best-effort match from a git worktree entry to a live Torque agent.

    The primary key is ``agent.worktree_path``, but cleanup safety must still
    recognize an active worker whose tracking was partially cleared by a prior
    failed cleanup. In that case the terminal's current/directory/git_root and
    Torque's default worktree path basename (<agent id>) still identify the
    attached worktree.
    """
    if not agent or getattr(agent, "cell_type", "") != "agent":
        return False
    path = str(path or "").strip()
    if not path:
        return False

    agent_repo = str(
        getattr(agent, "worktree_repo_root", "")
        or getattr(agent, "git_root", "")
        or ""
    ).strip()
    if agent_repo == repo_root:
        if _worktree_path_contains(path, getattr(agent, "worktree_path", "")):
            return True
    else:
        # Linked worktrees report their own path as git_root from inside the
        # worker terminal, not the main repo root.
        git_root = str(getattr(agent, "git_root", "") or "").strip()
        if git_root and _worktree_path_contains(path, git_root):
            return True

    for attr in ("worktree_path", "directory", "current_path"):
        value = str(getattr(agent, attr, "") or "").strip()
        if value and _worktree_path_contains(path, value):
            return True

    try:
        basename = os.path.basename(os.path.realpath(path))
    except Exception:
        basename = os.path.basename(path)
    agent_id = str(getattr(agent, "id", "") or "").strip()
    return bool(agent_id and basename == agent_id)


def _fresh_assigned_task_for_agent(
        state: MatrixState,
        agent_id: str,
        *,
        now: float,
        threshold: float = _WORKTREE_REMOVAL_FRESH_AGENT_SECONDS):
    if not agent_id:
        return None
    newest = None
    newest_ts = 0.0
    for task in state.board_tasks.values():
        if getattr(task, "agent_id", "") != agent_id:
            continue
        if task_is_closed(task):
            continue
        ts = (
            _timestamp_to_unix(getattr(task, "lane_entered_at", ""))
            or _timestamp_to_unix(getattr(task, "updated_at", ""))
            or _timestamp_to_unix(getattr(task, "created_at", ""))
        )
        if ts and now - ts <= threshold and ts >= newest_ts:
            newest = task
            newest_ts = ts
    return newest


def _worktree_removal_refusal_reason(
        state: MatrixState,
        cell,
        *,
        now: float | None = None,
        threshold: float = _WORKTREE_REMOVAL_FRESH_AGENT_SECONDS) -> str:
    """Return a hard-refusal reason for active/fresh worktree removal."""
    if not state or not cell or not getattr(cell, "worktree_path", ""):
        return ""
    if state.agent_is_tombstoned(cell):
        return ""

    status = str(getattr(cell, "status", "") or "").strip().lower()
    non_stopped = status not in {"", "stopped", "error"}
    now = float(now if now is not None else time.time())
    name = str(getattr(cell, "name", "") or getattr(cell, "id", "") or "agent")

    if getattr(cell, "session_id", None) and status != "stopped":
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (attached session)"
        )
    if status == "running":
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (running)"
        )
    if non_stopped and _agent_has_open_assigned_tasks(state, cell.id):
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (open assigned task)"
        )

    latest_activity = max(
        _timestamp_to_unix(getattr(cell, "last_progress_at", 0.0)),
        _timestamp_to_unix(getattr(cell, "last_heartbeat_at", 0.0)),
        _timestamp_to_unix(getattr(cell, "last_activity_at", 0.0)),
        _timestamp_to_unix(getattr(cell, "last_event_at", 0.0)),
    )
    if non_stopped and latest_activity and now - latest_activity <= threshold:
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (recent activity)"
        )

    fresh_task = _fresh_assigned_task_for_agent(
        state,
        cell.id,
        now=now,
        threshold=threshold,
    )
    if non_stopped and fresh_task:
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (recently dispatched task {fresh_task.id})"
        )
    return ""


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


def _agent_has_pending_engineer_followups(state: MatrixState,
                                        agent_id: str) -> bool:
    """Return whether the agent still owes the designated engineer a visible reply."""
    if not agent_id:
        return False
    if state.agent_pending_engineer_reply_tasks(agent_id):
        return True
    cell = state.agents.get(agent_id)
    return bool(cell and cell.pending_engineer_message)


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
        if _agent_has_pending_engineer_followups(state, agent_id):
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


def _engineer_display_name(state: MatrixState, group: str) -> str:
    engineer_id = state.get_group_settings(group).engineer_agent_id or ""
    engineer = state.agents.get(engineer_id) if engineer_id else None
    name = (engineer.name if engineer else "").strip()
    return name or "Engineer"


def _summarize_engineer_message(message: str, *, limit: int = 72) -> str:
    lines = [
        line.strip() for line in str(message or "").splitlines()
        if line.strip()
    ]
    summary = lines[0] if lines else str(message or "").strip()
    if not summary:
        return "Engineer follow-up"
    if len(summary) <= limit:
        return summary
    return summary[:limit - 1].rstrip() + "…"


def _engineer_followup_task_title(message: str) -> str:
    return f"Engineer: {_summarize_engineer_message(message)}"


def _format_mcp_message_prompt(message: str, *,
                               sender_name: str = "Engineer",
                               sender_kind: str = "engineer",
                               task_id: str = "",
                               reply_required: bool = True) -> str:
    # System-origin payloads (e.g. Torque digests) bring their own header
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
    if task_id and reply_required:
        prompt += (
            f'Reply with: torque_reply(task="{task_id}", '
            'message="your response")\n'
        )
    prompt += "---\n"
    return prompt


def _format_engineer_message_prompt(message: str, task_id: str,
                                    *,
                                    reply_required: bool = True) -> str:
    return _format_mcp_message_prompt(
        message,
        sender_name="Engineer",
        sender_kind="engineer",
        task_id=task_id,
        reply_required=reply_required,
    )


async def inject_mcp_message(state: MatrixState, bridge, target, message: str, *,
                             sender_name: str = "Torque",
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
        f"mcp__torque__{recipient_kind_key}_reply"
        if recipient_kind_key in {"architect", "engineer"}
        else "mcp__torque__torque_reply"
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
        if ack_required:
            hint_prefix = "Ack required. Reply with:"
        elif sender_kind_key == "architect" and recipient_kind_key == "architect":
            hint_prefix = "Optional reply:"
        else:
            hint_prefix = "Reply with:"
        blocks.append(
            f'{hint_prefix} {reply_tool}(message_id="{message_id}", '
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


def _peer_message_row_replay_entry(row: dict, target_id: str) -> dict | None:
    """Project a buffered canonical peer message into replay prompt fields."""
    target_id = str(target_id or "").strip()
    if (
        not row
        or not target_id
        or str(row.get("recipient_id", "") or "").strip() != target_id
    ):
        return None
    sender_id = str(row.get("sender_id", "") or "").strip()
    recipient_kind = str(row.get("recipient_kind", "") or "").strip() or "architect"
    sender_kind = str(row.get("sender_kind", "") or "").strip() or "architect"
    delivery_state = str(row.get("delivery_state", "") or "").strip() or "buffered"
    return {
        "id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "action": (
            "architect_peer_reply"
            if str(row.get("reply_to_id", "") or "").strip()
            else "architect_peer_message"
        ),
        "message": str(row.get("message", "") or ""),
        "timestamp": float(row.get("created_at", row.get("timestamp", 0)) or 0),
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "recipient_id": target_id,
        "recipient_kind": recipient_kind,
        "peer_id": sender_id,
        "peer_kind": sender_kind,
        "direction": "received",
        "ack_required": bool(row.get("ack_required", False)),
        "delivered": delivery_state == "delivered",
        "buffered": delivery_state == "buffered",
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
    }


def _is_canonical_peer_replay_entry(entry: dict) -> bool:
    return str((entry or {}).get("action", "") or "").strip() in {
        "architect_peer_message",
        "architect_peer_reply",
    }


def _user_direct_message_id_from_idempotency_key(idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(
        ("user-agent-message\0" + key).encode("utf-8")
    ).hexdigest()
    return "msg-" + digest[:12]


def _user_agent_message_idempotency_key(data: dict) -> str:
    """Return the browser/API idempotency key for user→agent sends."""
    data = data or {}
    for key in (
        "idempotency_key",
        "idempotencyKey",
        "client_message_id",
        "clientMessageId",
    ):
        value = str(data.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _user_direct_message_reply_tool(recipient_kind: str) -> str:
    kind = str(recipient_kind or "").strip()
    if kind == "architect":
        return "architect_message_user"
    if kind == "engineer":
        return "engineer_message_user"
    return "torque_message_user"


def _should_show_guidance_hint(state: MatrixState | None,
                               cell,
                               hint_type: str) -> bool:
    """Delegate recurring soft-hint cadence to state when available."""
    checker = getattr(state, "should_show_guidance_hint", None)
    if not callable(checker):
        return True
    try:
        return bool(checker(hint_type, cell))
    except Exception:
        log.exception(
            "Failed to evaluate guidance hint cadence for hint=%s cell=%s",
            hint_type,
            getattr(cell, "id", ""),
        )
        return True


def _format_user_direct_message_prompt(
        row: dict,
        recipient_kind: str,
        *,
        include_free_text_reply_hint: bool = True) -> str:
    """Format a durable user→agent message as an injected agent prompt."""
    row = row or {}
    thread_id = str(row.get("thread_id", "") or "").strip()
    message = str(row.get("message", "") or "").strip("\n")
    tool_name = _user_direct_message_reply_tool(recipient_kind)
    thread_arg = json.dumps(thread_id)
    parts = [
        "## Message from the User",
        "",
    ]
    if message:
        parts.extend([message, ""])
    parts.extend([
        "Reply to this user-facing thread with:",
        f"  mcp__torque__{tool_name}(thread_id={thread_arg}, message=\"...\")",
        "",
    ])
    if include_free_text_reply_hint:
        parts.append(
            "Do not rely on free-text terminal output for the user-facing reply."
        )
    parts.append("---")
    return "\n".join(parts) + "\n"


def _direct_message_delivery_response(row: dict | None, *,
                                      deduped: bool = False) -> dict:
    row = row or {}
    delivery_state = str(row.get("delivery_state", "") or "").strip() \
        or "buffered"
    return {
        "type": "ok",
        "message_id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "agent_id": str(row.get("recipient_id", "") or "").strip()
        if str(row.get("sender_kind", "") or "").strip() == "user"
        else str(row.get("sender_id", "") or "").strip(),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered": delivery_state == "delivered",
        "buffered": delivery_state == "buffered",
        "deduped": bool(deduped),
    }


def _user_agent_message_conflicts_with_existing(existing: dict,
                                                target,
                                                *,
                                                message: str,
                                                reply_to_id: str) -> bool:
    if not existing or not target:
        return False
    if str(existing.get("sender_kind", "") or "").strip() != "user":
        return True
    if str(existing.get("recipient_id", "") or "").strip() != str(
            getattr(target, "id", "") or "").strip():
        return True
    if str(existing.get("message", "") or "") != str(message or ""):
        return True
    if str(existing.get("reply_to_id", "") or "").strip() != str(
            reply_to_id or "").strip():
        return True
    return False


async def _queue_user_direct_message_to_agent(
        state: MatrixState,
        target,
        row: dict,
        send_prompt,
        *,
        emit: bool = True) -> dict | None:
    """Queue a persisted user→agent direct message into a live session."""
    message_id = str((row or {}).get("id", "") or "").strip()
    if not message_id:
        return row
    if not target or _agent_dismissed_at(target):
        return state.update_direct_message_delivery(
            message_id,
            "buffered",
            reason="agent_dismissed",
            emit=emit,
        ) or row
    if not getattr(target, "session_id", ""):
        return state.update_direct_message_delivery(
            message_id,
            "buffered",
            reason="no_session",
            emit=emit,
        ) or row
    if not send_prompt:
        return state.update_direct_message_delivery(
            message_id,
            "buffered",
            reason="send_prompt_unavailable",
            emit=emit,
        ) or row
    prompt = _format_user_direct_message_prompt(
        row,
        str(getattr(target, "kind", "") or "worker").strip() or "worker",
        include_free_text_reply_hint=_should_show_guidance_hint(
            state,
            target,
            GUIDANCE_HINT_USER_DIRECT_REPLY,
        ),
    )
    try:
        queued = await _queue_cell_prompt_send(
            target,
            prompt,
            send_prompt,
            prime_input_ready=True,
            settled_submit=True,
            wait_for_delivery=True,
        )
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__ or "delivery_failed"
        log.exception(
            "Failed to deliver direct user message %s to %s",
            message_id,
            getattr(target, "id", ""),
        )
        return state.update_direct_message_delivery(
            message_id,
            "buffered",
            reason=reason,
            emit=emit,
        ) or row
    if not queued:
        return state.update_direct_message_delivery(
            message_id,
            "buffered",
            reason="no_session",
            emit=emit,
        ) or row
    return state.update_direct_message_delivery(
        message_id,
        "delivered",
        emit=emit,
    ) or row


async def _replay_buffered_cross_kind_messages(
        state: MatrixState,
        bridge,
        target,
        *,
        send_prompt=None) -> int:
    """Replay buffered peer/direct inbox entries after a session wakes."""
    if not target or not getattr(target, "session_id", ""):
        return 0
    replayed = 0
    replay_candidates: dict[str, dict] = {}
    for entry in list(getattr(target, "mcp_messages", []) or []):
        if "user" in {
            str((entry or {}).get("sender_kind", "") or "").strip(),
            str((entry or {}).get("recipient_kind", "") or "").strip(),
        }:
            continue
        if str((entry or {}).get("direction", "") or "") != "received":
            continue
        if entry.get("delivered") is not False:
            continue
        message_id = str(entry.get("id", "") or "").strip()
        if message_id:
            replay_candidates[message_id] = dict(entry)

    db = getattr(state, "db", None)
    if db and hasattr(db, "load_buffered_agent_peer_messages"):
        for row in db.load_buffered_agent_peer_messages(target.id, limit=1000):
            entry = _peer_message_row_replay_entry(row, target.id)
            if not entry or not entry.get("id"):
                continue
            replay_candidates[entry["id"]] = entry
            append_to_caches = getattr(state, "append_peer_message_to_caches", None)
            if callable(append_to_caches):
                append_to_caches(row, emit=False)

    direct_rows: list[dict] = []
    if db and hasattr(db, "load_buffered_direct_messages"):
        direct_rows = list(db.load_buffered_direct_messages(
            target.id,
            limit=1000,
        ))

    entries = sorted(
        replay_candidates.values(),
        key=lambda item: (
            float((item or {}).get("timestamp", 0) or 0),
            str((item or {}).get("id", "") or ""),
        ),
    )
    for entry in entries:
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
            if _is_canonical_peer_replay_entry(entry):
                state.update_peer_message_delivery(
                    message_id,
                    "buffered",
                    reason="replay_failed",
                    emit=False,
                )
            else:
                _mark_cross_kind_message_delivery(
                    target,
                    message_id,
                    delivered=False,
                    reason="replay_failed",
                )
            continue
        if _is_canonical_peer_replay_entry(entry):
            state.update_peer_message_delivery(
                message_id,
                "delivered",
                emit=False,
            )
        else:
            _mark_cross_kind_message_delivery(target, message_id, delivered=True)
        replayed += 1

    direct_rows.sort(
        key=lambda item: (
            float((item or {}).get("created_at", item.get("timestamp", 0)) or 0),
            str((item or {}).get("id", "") or ""),
        )
    )
    for row in direct_rows:
        message_id = str((row or {}).get("id", "") or "").strip()
        message_text = str((row or {}).get("message", "") or "")
        if not message_id or not message_text:
            continue
        append_direct = getattr(state, "append_direct_message_to_caches", None)
        if callable(append_direct):
            append_direct(row, emit=False)
        updated = await _queue_user_direct_message_to_agent(
            state,
            target,
            row,
            send_prompt,
            emit=True,
        )
        if (
            str((updated or {}).get("delivery_state", "") or "").strip()
            == "delivered"
        ):
            replayed += 1
    if replayed:
        state._emit_agent(target)
    return replayed


def _make_agent_session_start_handler(
        state: MatrixState,
        bridge,
        send_prompt_getter,
        *,
        schedule_task=None):
    """Build the EventBus session_start hook.

    The hook is intentionally small: mark the terminal input-ready, then
    asynchronously replay any buffered direct/peer inbox messages for live,
    non-dismissed agent sessions.
    """
    if schedule_task is None:
        schedule_task = asyncio.create_task

    def _on_agent_session_start(cell):
        """Signal terminal readiness and recover buffered direct/peer messages."""
        bridge.signal_input_ready(cell.id)
        if (
            str(getattr(cell, "cell_type", "") or "") != "agent"
            or _agent_dismissed_at(cell)
        ):
            return

        async def _recover_buffered_messages():
            try:
                await _replay_buffered_cross_kind_messages(
                    state,
                    bridge,
                    cell,
                    send_prompt=send_prompt_getter(),
                )
                await state.broadcast()
            except Exception:
                log.exception(
                    "Failed to recover buffered messages for %s",
                    getattr(cell, "id", ""),
                )

        schedule_task(_recover_buffered_messages())

    return _on_agent_session_start


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


def _engineer_inline_thread_parent(state: MatrixState,
                                   target) -> Optional[BoardTask]:
    if not target:
        return None
    return state.agent_current_task(target.id)


def _append_engineer_inline_thread_message(state: MatrixState,
                                           target,
                                           parent_task_id: str,
                                           message: str,
                                           *,
                                           sender_agent_id: str = "",
                                           reply_required: bool = False
                                           ) -> Optional[BoardTask]:
    parent = state.board_tasks.get(parent_task_id)
    if not parent:
        return None
    sender_agent_id = str(sender_agent_id or "").strip()
    group_settings = state.get_group_settings(parent.group or target.group)
    if not sender_agent_id and group_settings:
        sender_agent_id = group_settings.engineer_agent_id or ""
    entry = {
        "timestamp": time.time(),
        "sender_agent_id": sender_agent_id,
        "recipient_agent_id": target.id,
        "content": message,
        "reply_required": bool(reply_required),
    }
    thread = list(getattr(parent, "messages_thread", []) or [])
    thread.append(entry)
    state.board_update_task(parent.id, messages_thread=thread)
    return state.board_tasks.get(parent.id)


def _create_engineer_followup_task(state: MatrixState, target, message: str,
                                  *,
                                  reply_required: bool = True
                                 ) -> Optional[BoardTask]:
    if not reply_required:
        return None
    if not target or not target.group:
        return None
    active_task = state.agent_current_task(target.id)
    labels = ["torque:engineer-message"]
    kwargs = {
        "description": message,
        "status": "Awaiting Reply",
        "labels": labels,
        "reply_agent_id": target.id,
        "board_sync": {
            "version": 1,
            "auto_track": False,
            "auto_sync_excluded": True,
            "auto_sync_excluded_reason": "engineer_message",
        },
    }
    task_group = target.group
    if active_task:
        labels.insert(0, "torque:derived")
        task_group = active_task.group or target.group
        kwargs.update({
            "parent_task_id": active_task.id,
            "pipeline_depth": active_task.pipeline_depth + 1,
            "pipeline_root_id": active_task.pipeline_root_id or active_task.id,
        })
    return state.board_add_task(
        task=_engineer_followup_task_title(message),
        group=task_group,
        lane="Backlog",
        **kwargs,
    )


def _resolve_pending_engineer_reply_task(state: MatrixState, cell, *,
                                       task_id: str = ""
                                       ) -> tuple[Optional[BoardTask],
                                                  list[BoardTask], str]:
    pending = state.agent_pending_engineer_reply_tasks(cell.id) if cell else []
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
        return None, pending, "No pending engineer message to reply to"
    ids = ", ".join(task.id for task in pending[:5])
    if len(pending) > 5:
        ids += ", …"
    return None, pending, (
        "Multiple pending engineer messages; reply with task=<id>. "
        f"Open reply tasks: {ids}"
    )


async def _send_engineer_message_to_agent(state: MatrixState, bridge, target,
                                        message: str, panel_event,
                                        *,
                                        sender_agent_id: str = "",
                                        reply_required: bool = True) -> dict:
    if not target or not target.session_id:
        return {"type": "error", "message": "Agent is not running"}
    reply_required = bool(reply_required)
    follow_up = None
    inline_parent = None
    if reply_required:
        follow_up = _create_engineer_followup_task(state, target, message)
        if not follow_up:
            return {
                "type": "error",
                "message": "Failed to create Engineer follow-up task",
            }
        prompt = _format_engineer_message_prompt(message, follow_up.id)
    else:
        inline_parent = _engineer_inline_thread_parent(state, target)
        if not inline_parent:
            return {
                "type": "error",
                "message": (
                    "reply_required=false requires an active parent task "
                    "for inline-thread persistence"
                ),
            }
        prompt = _format_engineer_message_prompt(
            message,
            "",
            reply_required=False,
        )
    optimistic_baseline = state.snapshot_agent_optimistic_state(target)
    optimistic_at = time.time()
    optimistic_marked = state.mark_agent_optimistic_running(
        target,
        optimistic_at,
        emit=True,
        persist=False,
    )
    if optimistic_marked:
        await state.broadcast()
    try:
        if hasattr(bridge, "prime_input_ready"):
            bridge.prime_input_ready(target.session_id)
        await bridge.send_text(target.session_id, prompt)
    except Exception as exc:
        log.exception("Failed to send Engineer message to agent %s", target.id)
        if (
            optimistic_marked
            and getattr(target, "status", "") == "running"
            and not getattr(target, "activity", "")
            and float(getattr(target, "last_progress_at", 0) or 0) <= optimistic_at
        ):
            if state.restore_agent_optimistic_state(
                    target,
                    optimistic_baseline,
                    emit=True,
                    persist=False):
                await state.broadcast()
        if follow_up:
            state.board_remove_task(follow_up.id)
        return {
            "type": "error",
            "message": f"Failed to send message: {exc}",
        }

    if not reply_required:
        updated_parent = _append_engineer_inline_thread_message(
            state,
            target,
            inline_parent.id,
            message,
            sender_agent_id=sender_agent_id,
            reply_required=False,
        )
        if not updated_parent:
            return {
                "type": "error",
                "message": "Failed to append inline Engineer message",
            }
        state.history_record_message(
            target.id,
            "engineer_message",
            message,
            task_id=updated_parent.id,
        )
        return {
            "type": "ok",
            "reply_required": False,
            "task_id": "",
            "thread_task_id": updated_parent.id,
        }

    follow_up.messages.append({
        "timestamp": time.time(),
        "action": "engineer_message",
        "message": message,
        "agent_name": _engineer_display_name(state, target.group),
    })
    state.board_update_task(
        follow_up.id,
        messages=list(follow_up.messages),
    )
    group_settings = state.get_group_settings(target.group)
    effective_engineer_id = str(sender_agent_id or "").strip()
    if not effective_engineer_id and group_settings:
        effective_engineer_id = group_settings.engineer_agent_id or ""
    state.history_record_dispatch(
        target,
        follow_up,
        engineer_group=target.group,
        engineer_id=effective_engineer_id,
    )
    state.history_record_message(
        target.id,
        "engineer_message",
        message,
        task_id=follow_up.id,
    )
    target.pending_engineer_message = True
    state._emit_agent(target)
    panel_event(
        "engineer_message",
        target.id,
        target.name,
        target.group,
        message[:200],
        task_id=follow_up.id,
    )
    return {"type": "ok", "reply_required": True, "task_id": follow_up.id}


def _handle_engineer_reply(state: MatrixState, cell, *, message: str,
                         task_id: str = "", panel_event=None) -> dict:
    if not message:
        return {"type": "error", "message": "Reply message is required"}
    reply_task, pending, error = _resolve_pending_engineer_reply_task(
        state,
        cell,
        task_id=task_id,
    )
    if error:
        if not pending:
            cell.pending_engineer_message = False
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
    cell.pending_engineer_message = bool(
        state.agent_pending_engineer_reply_tasks(cell.id)
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


def _is_architect_ask_task(task) -> bool:
    labels = set(getattr(task, "labels", []) or [])
    return "architect-ask" in labels and "torque:human" in labels


def _architect_ask_reply_prompt(task, answer: str) -> str:
    question = str(getattr(task, "task", "") or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    blocks = ["## User reply to architect ask"]
    if task_id:
        blocks.append(f"Task: {task_id}")
    if question:
        blocks.append(f"Question:\n{question}")
    blocks.append(f"Answer:\n{str(answer or '').strip()}")
    return "\n" + "\n\n".join(blocks) + "\n---\n"


async def _resolve_architect_ask_task(
        state: MatrixState,
        bridge,
        task,
        answer: str,
        panel_event=None) -> dict:
    """Resolve an architect→user ask and enqueue the reply for the architect."""
    answer = str(answer or "").strip()
    if not task:
        return {"type": "error", "message": "Task not found"}
    if not _is_architect_ask_task(task):
        return {"type": "error", "message": "Not an architect ask task"}
    if not answer:
        return {"type": "error", "message": "Answer is required"}

    architect_id = str(
        getattr(task, "reply_agent_id", "") or
        getattr(task, "created_by_architect_id", "") or ""
    ).strip()
    architect = state.agents.get(architect_id) if architect_id else None
    if (
        not architect
        or str(getattr(architect, "kind", "") or "").strip() != "architect"
    ):
        return {
            "type": "error",
            "message": "Architect ask has no linked architect",
        }

    question = str(getattr(task, "task", "") or "").strip()
    message_text = (
        f'Answer to your question "{question}":\n{answer}'
        if question else answer
    )
    entry = {
        "id": "msg-" + uuid.uuid4().hex[:12],
        "thread_id": str(getattr(task, "id", "") or ""),
        "reply_to_id": "",
        "action": "architect_ask_reply",
        "message": message_text,
        "timestamp": time.time(),
        "sender_id": "user",
        "sender_kind": "human",
        "peer_id": "user",
        "peer_kind": "human",
        "peer_name": "User",
        "direction": "received",
        "task_id": str(getattr(task, "id", "") or ""),
        "question": question,
        "answer": answer,
        "delivered": False,
        "buffered": True,
    }

    try:
        if getattr(architect, "session_id", "") and bridge:
            if hasattr(bridge, "prime_input_ready"):
                bridge.prime_input_ready(architect.session_id)
            await bridge.send_text(
                architect.session_id,
                _architect_ask_reply_prompt(task, answer),
            )
            entry["delivered"] = True
            entry["buffered"] = False
            architect.status = "running"
    except Exception:
        log.exception(
            "Failed to inject architect ask reply %s to architect %s",
            getattr(task, "id", ""),
            architect.id,
        )
        entry["delivery_reason"] = "inject_failed"

    architect.mcp_messages.insert(0, entry)
    if len(architect.mcp_messages) > 20:
        architect.mcp_messages[:] = architect.mcp_messages[:20]
    state._emit_agent(architect)
    state._db_save_agent(architect)
    state.history_record_message(
        architect.id,
        "architect_ask_reply",
        message_text,
        task_id=str(getattr(task, "id", "") or ""),
    )

    messages = list(getattr(task, "messages", []) or [])
    messages.append({
        "timestamp": time.time(),
        "action": "architect_ask_reply",
        "message": answer,
        "agent_name": "User",
    })
    if not task_is_closed(task):
        state.board_move_task(task.id, "Done")
    state.board_update_task(task.id, status="", messages=messages)
    save_direct_ask_reply_mirror(
        state,
        architect,
        answer,
        question=question,
        source_task_id=str(getattr(task, "id", "") or ""),
    )

    if panel_event:
        panel_event(
            "ask_resolved",
            architect.id,
            architect.name,
            task.group,
            "Resolved: " + (question[:120] if question else task.id),
            task_id=task.id,
        )
    return {
        "type": "ok",
        "task_id": task.id,
        "architect_id": architect.id,
        "message_id": entry["id"],
    }


def _handle_engineer_flush_now_command(engineer_buffer, data: dict) -> dict:
    recipient_or_group = data.get("agent_id", "") or data.get("group", "")
    ok, message = engineer_buffer.request_manual_flush(recipient_or_group)
    if ok:
        return {"type": "ok"}
    return {"type": "error", "message": message or "Unable to send queued events"}


def _engineer_journal_source_key(prefix: str, *parts) -> str:
    """Return a stable source key for idempotent system journal inserts."""
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part or "").encode("utf-8", "replace"))
        h.update(b"\0")
    return f"{prefix}:{h.hexdigest()[:32]}"


def _append_engineer_journal_entry(
    state: MatrixState,
    group: str,
    entry_type: str,
    entry: str,
    *,
    author_cell_id: str = "",
    timestamp: float | None = None,
    source_key: str = "",
) -> dict | None:
    """Append a per-engineer journal entry with shared attribution semantics."""
    group = str(group or "").strip()
    entry = str(entry or "").strip()
    if not group or not entry:
        return None
    return state.journal_append(
        group,
        str(entry_type or "").strip() or "observation",
        entry,
        author_cell_id=str(author_cell_id or "").strip(),
        timestamp=timestamp,
        source_key=str(source_key or "").strip(),
    )


async def _handle_engineer_dismiss_note_command(
    data: dict,
    state: MatrixState,
    panel_event,
) -> dict:
    """Clear the live engineer note after archiving it to panel events."""
    group = data.get("group", "")
    ws = state.get_engineer_settings(group)
    pending_note = str(getattr(ws, "pending_note", "") or "")
    note_kind = str(getattr(ws, "pending_note_kind", "") or "note").strip()
    if note_kind not in {"note", "question"}:
        note_kind = "note"
    engineer = state.get_engineer_for_group(group)
    author_cell_id = (
        str(getattr(ws, "pending_note_actor_id", "") or "").strip()
        or str(getattr(engineer, "id", "") or "").strip()
    )
    try:
        note_timestamp = float(getattr(ws, "pending_note_set_at", 0) or 0)
    except (TypeError, ValueError):
        note_timestamp = 0.0
    if not note_timestamp:
        note_timestamp = time.time()

    if pending_note:
        _append_engineer_journal_entry(
            state,
            group,
            "note_dismissed",
            pending_note,
            author_cell_id=author_cell_id,
            timestamp=note_timestamp,
            source_key=_engineer_journal_source_key(
                "note_dismissed",
                group,
                author_cell_id,
                note_timestamp,
                note_kind,
                pending_note,
            ),
        )

    if pending_note and panel_event:
        event_kind = (
            "engineer_question_dismissed"
            if note_kind == "question"
            else "engineer_note_dismissed"
        )
        panel_event(
            event_kind,
            str(getattr(engineer, "id", "") or ""),
            str(getattr(engineer, "name", "") or "Engineer"),
            group,
            pending_note,
        )

    await state.update_engineer_settings_async(
        group,
        pending_note="",
        pending_note_kind="",
        pending_note_set_at=0.0,
        pending_note_actor_id="")
    return {"type": "ok"}


def _handle_digest_pause_resume_command(
    state: MatrixState,
    engineer_buffer,
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
        engineer_buffer.on_delivery_paused(agent_id)
    else:
        engineer_buffer.on_delivery_resumed(agent_id)
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


def _truthy_compact_value(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {
        "1",
        "true",
        "yes",
        "compact",
        COMPACT_SNAPSHOT_PROTOCOL,
    }


def _payload_wants_compact_snapshot(payload: dict | None) -> bool:
    payload = payload or {}
    return (
        str(payload.get("protocol_version", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or str(payload.get("snapshot_protocol", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or _truthy_compact_value(payload.get("compact"))
    )


def _request_wants_compact_snapshot(request) -> bool:
    query = getattr(request, "query", {}) or {}
    return (
        str(query.get("protocol_version", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or str(query.get("snapshot_protocol", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or _truthy_compact_value(query.get("compact"))
    )


# ``deploy`` is intentionally listed here even though v1 does not implement a
# handler: the worker-context guard must still preemptively reject an in-daemon
# deploy attempt until a future deploy API exists.
_API_DAEMON_LIFECYCLE_COMMANDS = {"restart", "stop", "deploy"}
_DAEMON_STOP_RESULT_TYPE = "daemon_stop"
_DAEMON_STOP_TRIGGER_DELAY_SECONDS = 0.05


class _DaemonStopState:
    """Small shared state for graceful daemon-stop request draining."""

    def __init__(self) -> None:
        self.requested = False

    def request(self) -> bool:
        first_request = not self.requested
        self.requested = True
        return first_request

    def should_reject_api_request(self, cmd: str) -> bool:
        return self.requested and str(cmd or "").strip().lower() != "stop"


def _daemon_stop_result(*, already_requested: bool = False) -> dict:
    return {
        "type": _DAEMON_STOP_RESULT_TYPE,
        "message": (
            "Torque daemon stop already requested"
            if already_requested else
            "Torque daemon stopping"
        ),
    }


def _is_daemon_stop_result(result: dict | None) -> bool:
    return isinstance(result, dict) and result.get("type") == _DAEMON_STOP_RESULT_TYPE


def _daemon_stop_rejection_payload() -> dict:
    return {
        "ok": False,
        "error": "Torque daemon is stopping",
        "type": _DAEMON_STOP_RESULT_TYPE,
    }


async def _handle_daemon_stop_command(
    *,
    daemon_stop_state: _DaemonStopState,
    schedule_daemon_stop,
    state,
) -> dict:
    already_requested = not daemon_stop_state.request()
    if already_requested:
        log.info("Stop requested while daemon stop already pending")
    else:
        log.info("Stop requested — draining requests and shutting down")
        # Persist all agents (status etc.) before stop, mirroring restart.
        # Helper daemons are intentionally left running for PID-file adoption
        # by the next daemon; this matches current restart semantics and is
        # audited by TORQUE:358.
        for cell in list(state.agents.values()):
            try:
                state._db_save_agent(cell)
            except Exception:
                log.exception(
                    "Failed to persist agent '%s' before daemon stop",
                    getattr(cell, "id", ""),
                )

    # Schedule even after best-effort cleanup failures, and also on repeated
    # stop requests so a prior failed/cleared stop task cannot strand the
    # daemon in a requested-but-not-stopping state.
    schedule_daemon_stop()
    return _daemon_stop_result(already_requested=already_requested)


async def _shutdown_daemon_runtime(
    *,
    terminal_clients,
    ui_ws_clients,
    panel_log,
    event_ingest_drainer,
    event_ingest_client,
    cloud_connector_runtime=None,
    bridge,
    runner,
    state,
    db,
) -> None:
    """Run the daemon shutdown drain sequence in one shared place."""
    for ws_clients in terminal_clients.values():
        for ws_client in list(ws_clients):
            try:
                await ws_client.close()
            except Exception:
                pass
    terminal_clients.clear()
    for ws_client in list(ui_ws_clients):
        try:
            await ws_client.close()
        except Exception:
            pass
    ui_ws_clients.clear()
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
        await cloud_hooks.stop_cloud_connector(cloud_connector_runtime)
    except Exception:
        log.exception("Cloud connector shutdown drain failed")
    try:
        await bridge.shutdown()
    except Exception:
        log.exception("Terminal adapter shutdown failed")
    try:
        await runner.cleanup()
    except Exception:
        log.exception("HTTP runner cleanup failed")
    try:
        await state.flush_db_writes()
        await db.close_async_writes()
    except Exception:
        log.exception("Async SQLite write queue shutdown failed")
    try:
        db.close()
    except Exception:
        log.exception("SQLite database close failed")



def _api_worker_context_guard(data: dict | None, headers=None,
                              remote: str = "") -> dict | None:
    data = data or {}
    cmd = str(data.get("cmd", "") or "").strip().lower()
    force = data.get("force")
    if (
            cmd not in _API_DAEMON_LIFECYCLE_COMMANDS
            or force is True
            or str(force or "").strip().lower() in {"1", "true", "yes", "on"}):
        return None
    headers = headers or {}
    cell_id = next((str(value).strip() for value in (
        headers.get("TORQUE_CELL_ID"),
        headers.get("X-Torque-Cell-Id"),
        data.get("TORQUE_CELL_ID"),
        data.get("torque_cell_id"),
        data.get("cell_id"),
    ) if str(value or "").strip()), "")
    if not cell_id:
        return None
    message = (
        f"Refusing HTTP /api/cmd {cmd} from Torque worker context "
        f"(TORQUE_CELL_ID={cell_id}). Restarting/stopping/deploying Torque "
        "from inside a live worker can corrupt dispatch state. If this is "
        "intentional, retry with force=true."
    )
    log.warning(
        "Rejected worker HTTP /api/cmd lifecycle request: cmd=%s "
        "cell_id=%s source=%s",
        cmd,
        cell_id,
        str(remote or "unknown").strip() or "unknown",
    )
    return {"message": message, "status": 403}


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


async def _handle_send_text_command(data, state: MatrixState, send_prompt) -> bool:
    cell = state.agents.get(data.get("id"))
    return await _queue_cell_prompt_send(
        cell,
        data.get("text", ""),
        send_prompt,
    )


async def _handle_send_user_message_command(data, state: MatrixState,
                                            bridge) -> bool:
    cell_id = str(data.get("cell_id") or data.get("id") or "").strip()
    text = str(data.get("text") or "")
    if not cell_id or not text.strip():
        return False
    cell = state.agents.get(cell_id)
    if not cell or not getattr(cell, "session_id", ""):
        return False
    optimistic_baseline = state.snapshot_agent_optimistic_state(cell)
    optimistic_at = time.time()
    optimistic_marked = state.mark_agent_optimistic_running(
        cell,
        optimistic_at,
        emit=True,
        persist=False,
    )
    if optimistic_marked:
        await state.broadcast()
    try:
        await bridge.send_text(cell.session_id, text)
    except Exception:
        if (
            optimistic_marked
            and getattr(cell, "status", "") == "running"
            and not getattr(cell, "activity", "")
            and float(getattr(cell, "last_progress_at", 0) or 0) <= optimistic_at
        ):
            if state.restore_agent_optimistic_state(
                    cell,
                    optimistic_baseline,
                    emit=True,
                    persist=False):
                await state.broadcast()
        raise
    state.record_message_history(cell.id, text)
    return True


async def _handle_user_agent_message_command(data, state: MatrixState,
                                             send_prompt) -> dict:
    """Persist and non-interruptively deliver a user→agent direct message."""
    target_ident = str(
        data.get("agent_id")
        or data.get("cell_id")
        or data.get("target_agent_id")
        or ""
    ).strip()
    target_id = _resolve_agent_id(state, target_ident)
    target = state.get_active_agent(target_id) if target_id else None
    if not target or getattr(target, "cell_type", "") != "agent":
        return {
            "type": "error",
            "message": f"Agent not found: {target_ident}",
        }
    message_text = str(data.get("message") or data.get("text") or "")
    if not message_text.strip():
        return {"type": "error", "message": "Message is required"}
    if not getattr(state, "db", None):
        return {
            "type": "error",
            "message": "Direct message store is unavailable",
        }

    idempotency_key = _user_agent_message_idempotency_key(data)
    message_id = _user_direct_message_id_from_idempotency_key(idempotency_key)
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
    reply_to_id = str(data.get("reply_to_id", "") or "").strip()
    requested_thread_id = str(data.get("thread_id", "") or "").strip()
    thread_id = requested_thread_id or canonical_user_agent_thread_id(target.id)
    recipient_kind = str(getattr(target, "kind", "") or "").strip() or "worker"
    recipient_name = str(getattr(target, "name", "") or "").strip()

    existing = (
        state.db.load_direct_message(message_id)
        if idempotency_key and getattr(state, "db", None)
        else None
    )
    if existing:
        if _user_agent_message_conflicts_with_existing(
                existing,
                target,
                message=message_text,
                reply_to_id=reply_to_id):
            return {
                "type": "error",
                "message": (
                    "idempotency key was reused for a different "
                    "user_agent_message"
                ),
            }
        append_direct = getattr(state, "append_direct_message_to_caches", None)
        if callable(append_direct):
            append_direct(existing)
        return _direct_message_delivery_response(existing, deduped=True)

    row = {
        "id": message_id,
        "thread_id": thread_id,
        "reply_to_id": reply_to_id,
        "idempotency_key": idempotency_key,
        "group_name": str(getattr(target, "group", "") or "").strip(),
        "sender_id": "user",
        "sender_kind": "user",
        "sender_name": "User",
        "recipient_id": target.id,
        "recipient_kind": recipient_kind,
        "recipient_name": recipient_name,
        "message": message_text,
        "message_type": "message",
        "created_at": time.time(),
        "blocking": False,
        "delivery_state": "buffered",
        "delivery_reason": "",
    }
    saved = state.save_direct_message(row)
    if not saved:
        return {
            "type": "error",
            "message": "Failed to save direct message",
        }
    delivered = await _queue_user_direct_message_to_agent(
        state,
        target,
        saved,
        send_prompt,
        emit=True,
    )
    return _direct_message_delivery_response(delivered or saved)


async def _deliver_engineer_reply_and_resume(state: MatrixState, engineer, *,
                                           group: str,
                                           answer: str,
                                           send_prompt,
                                           engineer_buffer) -> dict:
    ws = state.get_engineer_settings(group)
    question = str(getattr(ws, "pending_question", "") or "").strip()
    author_cell_id = (
        str(getattr(ws, "pending_question_actor_id", "") or "").strip()
        or str(getattr(engineer, "id", "") or "").strip()
    )
    try:
        question_timestamp = float(
            getattr(ws, "pending_question_set_at", 0) or 0
        )
    except (TypeError, ValueError):
        question_timestamp = 0.0
    formatted = (
        "\n"
        "## Human Reply\n"
        f"{answer}\n"
        "---\n"
    )
    await _queue_cell_prompt_send(
        engineer,
        formatted,
        send_prompt,
        prime_input_ready=True,
        wait_for_delivery=True,
    )
    await state.update_engineer_settings_async(
        group,
        pending_question="",
        paused=False,
        _pending_question_actor_id=getattr(engineer, "id", "") or "",
    )
    engineer_buffer.on_delivery_resumed(group)
    if question:
        _append_engineer_journal_entry(
            state,
            group,
            "qa",
            f"Question:\n{question}\n\nAnswer:\n{str(answer or '').strip()}",
            author_cell_id=author_cell_id,
            source_key=_engineer_journal_source_key(
                "qa",
                group,
                author_cell_id,
                question_timestamp,
                question,
            ),
        )
        asking_agent = state.agents.get(author_cell_id) or engineer
        source_key = direct_ask_mirror_source_key(
            group=group,
            agent_id=author_cell_id or str(getattr(engineer, "id", "") or ""),
            timestamp=question_timestamp,
            question=question,
        )
        save_direct_ask_reply_mirror(
            state,
            asking_agent,
            answer,
            question=question,
            source_key=source_key,
            created_at=time.time(),
        )
    state.journal_append(
        group,
        "observation",
        f"Human replied: {answer}",
    )
    return {"type": "ok"}


def _pending_question_reply_target(state: MatrixState, group: str):
    """Return the agent that should receive a human reply for pending_question.

    Actor-scoped engineer asks should reply to the engineer that asked the
    question. Legacy rows without an actor fall back to the group engineer.
    """
    ws = state.get_engineer_settings(group)
    actor_id = str(
        getattr(ws, "pending_question_actor_id", "") or ""
    ).strip()
    if actor_id:
        return state.agents.get(actor_id), "Engineer"
    return state.get_engineer_for_group(group), "Engineer"


def _sanitize_engineer_worker_provider_override(
    state: MatrixState,
    group: str,
    data: dict,
    requested_provider: str,
) -> str:
    """Return allowed worker provider override or '' to use group defaults."""
    requested_provider = str(requested_provider or "").strip()
    if not requested_provider:
        return ""
    engineer_id = str(data.get("_engineer_dispatch_id", "") or "").strip()
    if not engineer_id:
        return requested_provider
    settings = state.get_engineer_settings(group)
    if getattr(settings, "engineer_can_override_worker_provider", True):
        return requested_provider
    log.warning(
        "Engineer %s attempted worker provider override '%s' in group %s "
        "while provider overrides are disabled; falling back to group default",
        engineer_id,
        requested_provider,
        group,
    )
    return ""


def _worker_provider_override_from_dispatch(data: dict) -> str:
    """Return requested worker provider override from new/legacy API names."""
    provider = str(data.get("provider", "") or "").strip()
    agent_type = str(data.get("agent_type", "") or "").strip()
    if provider and agent_type and provider != agent_type:
        raise ValueError(
            "provider and agent_type overrides disagree; use one provider "
            "value for the new worker"
        )
    return provider or agent_type


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


def _handle_board_archive_tasks_command(state: MatrixState, data: dict) -> dict:
    """Archive multiple board tasks in one atomic batch."""
    raw_ids = data.get("ids", data.get("task_ids", []))
    if not isinstance(raw_ids, list):
        return {"type": "error", "message": "ids must be an array"}
    try:
        archived_ids = state.board_archive_tasks(raw_ids)
    except Exception as exc:
        return {"type": "error", "message": str(exc)}

    count = len(archived_ids)
    if count == 0:
        message = "No tasks archived"
    else:
        message = (
            f"Archived {count} completed task"
            f"{'' if count == 1 else 's'}"
        )
    return {"type": "toast", "level": "success", "message": message}


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


def _handle_doctor_command(db: TorqueDB) -> dict:
    return build_doctor_report(db._conn, db.db_path, runtime_python=sys.executable)


_INTERNAL_FAILED_WRITE_PREFIX = "internal:"
_NO_COMMAND_RECEIPT = object()
_CRITICAL_BOARD_COMMANDS = {
    "architect_task_update",
    "board_add_task",
    "board_update_task",
    "board_move_task",
    "board_archive_task",
    "board_archive_tasks",
    "board_unarchive_task",
    "board_verify_task",
    "board_remove_task",
}
_CRITICAL_AI_REPORT_ACTIONS = {
    "done",
    "blocked",
    "error",
    "ask",
    "derive",
    "ready",
    "verify",
    "name",
    "reply",
}


def _internal_failed_write_key(idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    return f"{_INTERNAL_FAILED_WRITE_PREFIX}{key}" if key else ""


def _critical_command_name(data: dict) -> str:
    cmd = str((data or {}).get("cmd", "") or "").strip()
    if cmd == "ai_report":
        action = str((data or {}).get("action", "") or "").strip()
        if action in _CRITICAL_AI_REPORT_ACTIONS:
            return f"ai_report:{action}"
        return ""
    if cmd in _CRITICAL_BOARD_COMMANDS:
        return cmd
    if cmd == "architect_journal_append":
        return cmd
    return ""


def _critical_command_needs_capture(data: dict) -> bool:
    cmd = str((data or {}).get("cmd", "") or "").strip()
    return cmd == "ai_report" or cmd in _CRITICAL_BOARD_COMMANDS


def _critical_command_caller_id(data: dict) -> str:
    for key in ("cell_id", "architect_id", "agent_id", "id"):
        value = str((data or {}).get(key, "") or "").strip()
        if value:
            return value
    return ""


def _critical_command_conflict_result(command_name: str) -> dict:
    return {
        "type": "error",
        "message": (
            "idempotency key was reused for a different internal command "
            f"({command_name or 'unknown'})"
        ),
    }


def _load_internal_command_receipt(
    db: TorqueDB | None,
    payload: dict,
) -> tuple[object, str, str]:
    key = str((payload or {}).get("idempotency_key", "") or "").strip()
    command_name = _critical_command_name(payload)
    if not db or not key or not command_name:
        return _NO_COMMAND_RECEIPT, "", ""
    request_hash = api_request_hash(payload)
    existing = db.load_command_receipt(key)
    if not existing:
        return _NO_COMMAND_RECEIPT, key, request_hash
    existing_hash = str(existing.get("request_hash", "") or "").strip()
    if existing_hash and existing_hash != request_hash:
        return _critical_command_conflict_result(command_name), key, request_hash
    return existing.get("response"), key, request_hash


async def replay_internal_failed_write_payload(
    db: TorqueDB,
    payload: dict,
    handle_command,
):
    """Replay one queued critical internal command using command receipts."""
    cached, _key, _request_hash = _load_internal_command_receipt(db, payload)
    if cached is not _NO_COMMAND_RECEIPT:
        return cached
    result = await handle_command(payload)
    # Mirror handle_command's deliverable_missing semantics: surface as a
    # semantic failure so the replay caller doesn't treat the refusal as
    # success. The receipt-save path inside handle_command already
    # avoids persisting a command receipt for this result type, so a
    # subsequent retry after the worker uploads an artifact will re-run
    # the gate cleanly.
    if isinstance(result, dict) and result.get("type") == "deliverable_missing":
        return {
            "ok": False,
            "type": "deliverable_missing",
            "error": result.get(
                "message",
                "Deliverable artifact required before completion.",
            ),
        }
    if isinstance(result, dict) and result.get("type") == "review_required":
        # Mirror deliverable_missing replay semantics for the
        # mandatory-review gate (TORQUE:256). Don't cache the refusal.
        return {
            "ok": False,
            "type": "review_required",
            "error": result.get(
                "message",
                "Review required by action contract before completion.",
            ),
        }
    return result


async def replay_api_failed_write_payload(
    db: TorqueDB,
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
    # Mirror the direct /api/cmd hard-gate semantics: a deliverable_missing
    # refusal is a semantic failure, not a successful write. Surface it as
    # an error envelope and DO NOT save it as an idempotency response —
    # otherwise a later same-key retry (after the artifact is uploaded)
    # would be deduped to the cached refusal instead of re-running the
    # gate. Same shape as handle_api_cmd's deliverable_missing branch.
    if result and result.get("type") == "deliverable_missing":
        return {
            "ok": False,
            "type": "deliverable_missing",
            "error": result.get(
                "message",
                "Deliverable artifact required before completion.",
            ),
        }
    if result and result.get("type") == "review_required":
        # Mirror deliverable_missing semantics: review_required is a
        # recoverable refusal — don't cache it as an idempotency response,
        # so a same-key retry after the worker derives the review re-runs
        # the gate (TORQUE:256).
        return {
            "ok": False,
            "type": "review_required",
            "error": result.get(
                "message",
                "Review required by action contract before completion.",
            ),
        }
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
    return bool(str(getattr(cell, "created_by_engineer_id", "") or "").strip())


def _agent_role_slug(cell) -> str:
    return str(
        getattr(cell, "role", "")
        or getattr(cell, "template", "")
        or ""
    ).strip()


def _agent_owner_engineer_name(state: MatrixState, cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if not owner_id:
        owner_id = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
    if not owner_id:
        return ""
    owner = state.agents.get(owner_id)
    return owner.name if owner else ""


def _owner_is_user_from_ids(*, owner_engineer_id: str = "",
                            created_by_engineer_id: str = "",
                            hired_by_architect_id: str = "") -> bool:
    """Return True when an agent's owner is the user.

    Owner-is-user is the absence of any non-user ownership stamp: no
    owning/creating engineer and no hiring architect. Expressed via the
    ownership ids (per the kinds-refactor invariants) rather than by
    hardcoding agent kinds, so it holds uniformly for user-owned
    architects, engineers, and workers.
    """
    return not (
        str(owner_engineer_id or "").strip()
        or str(created_by_engineer_id or "").strip()
        or str(hired_by_architect_id or "").strip()
    )


def _agent_owner_is_user(cell) -> bool:
    """Return True when ``cell``'s owner is the user (not engineer/architect)."""
    if cell is None:
        return False
    return _owner_is_user_from_ids(
        owner_engineer_id=getattr(cell, "owner_engineer_id", ""),
        created_by_engineer_id=getattr(cell, "created_by_engineer_id", ""),
        hired_by_architect_id=getattr(cell, "hired_by_architect_id", ""),
    )


def _normalize_prompt_block(text: str) -> str:
    return str(text or "").strip("\n")


def _assemble_worker_prompt(*, role_mgr, cell, base_dir: str = "",
                            prompt_body: str = "", postscript: str = "",
                            disable_role_preamble: bool = False,
                            include_identity_anchor: bool = True) -> str:
    """Assemble the final worker prompt with optional role preamble.

    The final shape is:
    {identity anchor block}

    {role preamble block}

    {task/action prompt block}

    {torque postscript}

    Empty blocks are omitted. Exactly one blank line is inserted between
    included blocks, and the final prompt always ends with a trailing newline.
    """
    blocks = []

    if include_identity_anchor:
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


def _build_torque_context(state: MatrixState, cell, task) -> dict:
    """Build the ``torque`` namespace dict for Jinja2 template rendering."""
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


def _launch_resolver_for_cell(
        cell, *,
        resolve_agent_launch_config,
        resolve_engineer_launch_config=None,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        is_designated_engineer=None):
    """Pick the kind-specific launch resolver for an existing cell."""
    if getattr(cell, "cell_type", "") != "agent":
        return resolve_agent_launch_config
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "architect":
        if resolve_architect_launch_config:
            return resolve_architect_launch_config
        if resolve_engineer_launch_config:
            return resolve_engineer_launch_config
    if kind == "engineer" and resolve_engineer_launch_config:
        return resolve_engineer_launch_config
    if kind == "worker" and resolve_worker_launch_config:
        return resolve_worker_launch_config
    if is_designated_engineer and is_designated_engineer(cell) \
            and resolve_engineer_launch_config:
        return resolve_engineer_launch_config
    return resolve_agent_launch_config


async def _relaunch_agent_after_worktree_removal(
        cell, *,
        bridge,
        state,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_engineer_launch_config=None,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        is_designated_engineer=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        send_agent_prompt=None):
    """Reset an agent session after its worktree is removed.

    This sibling restart path always opens a fresh provider conversation
    (``agent_session_id`` is cleared above), so when ``send_agent_prompt``
    is supplied the role's startup + initial prompts are re-delivered via
    ``_new_agent_prompt_sequence``. Mirrors the ``:259`` fix in
    ``_handle_relaunch_agent_command``: codex agents get their persistent
    prompt seated as the first chat turn, claude-code agents get any role
    ``initial_prompt`` (kickoff text) without duplicating the file-injected
    system prompt.
    """
    if cell.cell_type != "agent":
        return
    if cell.session_id:
        await bridge.close_session(cell.session_id)
    cell.status = "stopped"
    cell.session_id = None
    cell.agent_session_id = ""
    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    resolver = _launch_resolver_for_cell(
        cell,
        resolve_agent_launch_config=resolve_agent_launch_config,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        resolve_architect_launch_config=resolve_architect_launch_config,
        resolve_worker_launch_config=resolve_worker_launch_config,
        is_designated_engineer=is_designated_engineer,
    )
    launch_cfg = resolver(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    persistent_prompt_text = build_cell_persistent_prompt(cell, launch_cfg)
    apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
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

    # Fresh-session kickoff: agent_session_id was cleared above to force a
    # fresh provider conversation, so the same kickoff conditions as
    # ``_handle_relaunch_agent_command`` always fire here when
    # ``send_agent_prompt`` is supplied. Without this, any role
    # ``initial_prompt`` is silently dropped on worktree-removal relaunch
    # and codex agents lose their persistent system prompt entirely.
    if (
            send_agent_prompt
            and cell.session_id
            and (not cell.agent_session_id or not cell.session_resume)
    ):
        startup_prompt = _startup_prompt_for_new_agent(
            agent_type=launch_cfg.get("agent_type", ""),
            persistent_prompt_text=persistent_prompt_text,
        )
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg, startup_prompt=startup_prompt, cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)


def _resolve_engineer_group(state: MatrixState) -> str:
    """Return the reserved engineer group, preferring the designated engineer."""
    for group_name, group_settings in state.group_settings.items():
        engineer_id = str(getattr(group_settings, "engineer_agent_id", "") or "")
        cell = state.get_active_agent(engineer_id)
        if cell and cell.cell_type == "agent" \
                and str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return str(cell.group or group_name or "torque")
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return str(cell.group or "torque")
    return "torque"


def _resolve_engineer_cell(state: MatrixState, *, engineer_id: str = "",
                           engineer_slug: str = "",
                           include_tombstoned: bool = False):
    """Resolve an engineer agent by exact id or slug."""
    engineer_id = str(engineer_id or "").strip()
    engineer_slug = str(engineer_slug or "").strip().lower()
    for cell in state.iter_agents(include_tombstoned=include_tombstoned):
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


def _relaunch_command_base(command: str, prompt_filename: str) -> str:
    """Return a persisted relaunch command without Torque-managed prompt flags."""
    command = str(command or "").strip()
    prompt_filename = str(prompt_filename or "").strip()
    if not command or not prompt_filename:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    cleaned = []
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        if (
                part == "--append-system-prompt-file"
                and idx + 1 < len(parts)
                and prompt_filename in parts[idx + 1]):
            idx += 2
            continue
        cleaned.append(part)
        idx += 1
    if len(cleaned) == len(parts):
        return command
    return shlex.join(cleaned)


def _engineer_dismissed_error(engineer_id: str) -> dict:
    return {
        "type": "error",
        "reason": "engineer_dismissed",
        "message": f"engineer {engineer_id} is dismissed",
        "engineer_id": str(engineer_id or "").strip(),
    }


def _engineer_tombstoned_error(engineer_id: str) -> dict:
    return {
        "type": "error",
        "reason": "engineer_tombstoned",
        "message": f"engineer {engineer_id} is tombstoned",
        "engineer_id": str(engineer_id or "").strip(),
    }


def _architect_dismissed_error(architect_id: str) -> dict:
    return {
        "type": "error",
        "reason": "architect_dismissed",
        "message": f"architect {architect_id} is dismissed",
        "architect_id": str(architect_id or "").strip(),
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


def _validate_architect_lifecycle_authority(
        state: MatrixState,
        architect,
        *,
        caller_kind: str = "") -> dict | None:
    """Return an error if a non-user tries to manage architect lifecycle."""
    del state, architect
    caller_kind = str(caller_kind or "").strip()
    if caller_kind and caller_kind != "user":
        return {
            "type": "error",
            "message": "architect lifecycle is user-only",
        }
    return None


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_engineer_id", "") or "").strip()


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
    for cell in list(state.iter_active_agents()):
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
                            architect_slug: str = "",
                            include_tombstoned: bool = False):
    """Resolve an architect agent by exact id or slug."""
    architect_id = str(architect_id or "").strip()
    architect_slug = str(architect_slug or "").strip().lower()
    for cell in state.iter_agents(include_tombstoned=include_tombstoned):
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
    for cell in state.iter_active_agents():
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
    architect_settings = None
    if state is not None and group:
        try:
            group_settings = state.get_group_settings(group)
            architect_settings = state.get_architect_settings(group)
        except Exception:
            group_settings = None
            architect_settings = None

    architect_body = build_architect_system_prompt(
        group or "default",
        architect_settings=architect_settings,
        action_system_prompt=action_system_prompt,
        group_settings=group_settings,
    ).rstrip()

    torque_preamble = build_torque_system_prompt(
        include_shared_memory=False,
    ).rstrip()
    return torque_preamble + "\n\n" + architect_body + "\n"


def _snapshot_dataclass_like(obj) -> dict:
    if obj is None:
        return {}
    try:
        return asdict(obj)
    except TypeError:
        return {
            key: getattr(obj, key)
            for key in dir(obj)
            if not key.startswith("_")
            and not callable(getattr(obj, key, None))
        }


def _preview_group_settings_for_prompt(
        state: MatrixState, group: str, payload: dict | None = None):
    """Return a group-settings snapshot with unsaved form values overlaid."""
    values = _snapshot_dataclass_like(state.get_group_settings(group))
    for key, value in dict(payload or {}).items():
        if key in values:
            values[key] = value
    return SimpleNamespace(**values)


def _preview_engineer_settings_for_prompt(
        state: MatrixState, group: str, payload: dict | None = None
        ) -> EngineerSettings:
    """Return EngineerSettings with unsaved form values overlaid.

    The preview path intentionally does not mutate MatrixState; it mirrors the
    prompt builder's inputs so the settings modal can ask for a one-off render
    while the user is still editing the form.
    """
    values = _snapshot_dataclass_like(state.get_engineer_settings(group))
    values["group"] = group
    valid = set(EngineerSettings.__dataclass_fields__)
    for key, value in dict(payload or {}).items():
        if key in valid and key != "group":
            values[key] = value
    values["group"] = group
    return EngineerSettings(**{
        key: values.get(key)
        for key in EngineerSettings.__dataclass_fields__
    })


def _preview_architect_settings_for_prompt(
        state: MatrixState, group: str, payload: dict | None = None
        ) -> ArchitectSettings:
    """Return ArchitectSettings with unsaved form values overlaid."""
    incoming = dict(payload or {})
    if (
            "custom_instructions" in incoming
            and "architect_custom_instructions" not in incoming):
        incoming["architect_custom_instructions"] = incoming.pop(
            "custom_instructions"
        )
    values = _snapshot_dataclass_like(state.get_architect_settings(group))
    values["group"] = group
    valid = set(ArchitectSettings.__dataclass_fields__)
    for key, value in incoming.items():
        if key in valid and key != "group":
            values[key] = value
    values["group"] = group
    return ArchitectSettings(**{
        key: values.get(key)
        for key in ArchitectSettings.__dataclass_fields__
    })


def _build_group_system_prompt_preview(
        state: MatrixState, group: str, kind: str, *,
        settings_payload: dict | None = None,
        group_settings_payload: dict | None = None,
        action_system_prompt: str = "",
        specializations_preamble: str = "") -> str:
    """Build the settings-modal system-prompt preview for a group role.

    Mirrors the current boot prompt paths instead of reimplementing prompt
    assembly in JavaScript:

    - Engineer: ``build_engineer_system_prompt(...)`` as used for designated
      engineer launch/relaunch.
    - Architect: Torque's persistent agent preamble plus
      ``build_architect_system_prompt(...)`` as used by
      ``_architect_persistent_prompt_text``.
    """
    normalized_kind = str(kind or "").strip().lower()
    group_name = str(group or "").strip() or "default"
    group_settings = _preview_group_settings_for_prompt(
        state, group_name, group_settings_payload)

    if normalized_kind == "engineer":
        from .engineer import build_engineer_system_prompt

        engineer_settings = _preview_engineer_settings_for_prompt(
            state, group_name, settings_payload)
        return build_engineer_system_prompt(
            group_name,
            engineer_settings,
            action_system_prompt,
            group_settings=group_settings,
            specializations_preamble=specializations_preamble,
        ).rstrip() + "\n"

    if normalized_kind == "architect":
        from .architect import build_architect_system_prompt

        architect_settings = _preview_architect_settings_for_prompt(
            state, group_name, settings_payload)
        architect_body = build_architect_system_prompt(
            group_name,
            architect_settings=architect_settings,
            action_system_prompt=action_system_prompt,
            group_settings=group_settings,
        ).rstrip()
        torque_preamble = build_torque_system_prompt(
            include_shared_memory=False,
        ).rstrip()
        return torque_preamble + "\n\n" + architect_body + "\n"

    raise ValueError("kind must be 'engineer' or 'architect'")


def _agent_overrides_from_role_settings(kind: str, settings) -> dict:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "engineer":
        mapping = {
            "engineer_provider": "provider",
            "engineer_boot_command": "command",
            "engineer_model": "model",
            "engineer_reasoning_effort": "reasoning_effort",
            "engineer_directory": "directory",
            "engineer_profile": "profile",
            "engineer_shell": "shell",
            "engineer_tab_color": "tab_color",
        }
    else:
        mapping = {
            "architect_provider": "provider",
            "architect_boot_command": "command",
            "architect_model": "model",
            "architect_reasoning_effort": "reasoning_effort",
            "architect_directory": "directory",
            "architect_profile": "profile",
            "architect_shell": "shell",
            "architect_tab_color": "tab_color",
        }
    out = {}
    for source, target in mapping.items():
        value = getattr(settings, source, "")
        if isinstance(value, str):
            value = value.strip()
        if value:
            out[target] = value
    return out


async def _handle_add_engineer_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_engineer_launch_config,
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
    launch_cfg = resolve_engineer_launch_config(
        group,
        base_dir=base_dir,
        explicit_template="",
        overrides=overrides,
    )

    from .engineer import build_engineer_system_prompt

    persistent_prompt_text = build_engineer_system_prompt(
        group,
        state.get_engineer_settings(group),
        launch_cfg.get("system_prompt", ""),
        group_settings=state.get_group_settings(group),
        owner_is_user=not str(
            data.get("hired_by_architect_id", "") or "").strip(),
    )
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template="",
        persistent_prompt_text=persistent_prompt_text,
        created_by_engineer_id="",
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
                cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
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
        resolve_engineer_launch_config,
        create_agent_with_config,
        send_agent_prompt,
        resolve_architect_launch_config=None) -> dict:
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
    launch_resolver = resolve_architect_launch_config or resolve_engineer_launch_config
    launch_cfg = launch_resolver(
        group,
        base_dir=base_dir,
        explicit_template="",
        overrides=overrides,
    )
    if torque_config.ARCHITECT_USES_WORKTREE:
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
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template="",
        persistent_prompt_text=persistent_prompt_text,
        created_by_engineer_id="",
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
                cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
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
        resolve_worker_launch_config=None,
        create_agent_with_config,
        send_agent_prompt) -> dict:
    """Create and launch a user-owned detached worker agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Worker name is required"}

    supplied_owner = str(data.get("owner_engineer_id", "") or "").strip()
    supplied_legacy_owner = str(
        data.get("created_by_engineer_id", "")
        or data.get("_created_by_engineer_id", "")
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
            "created_by_engineer_id",
            "_created_by_engineer_id",
            "hired_by_architect_id",
    ):
        overrides.pop(key, None)
    launch_resolver = resolve_worker_launch_config or resolve_agent_launch_config
    launch_cfg = launch_resolver(
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
        created_by_engineer_id="",
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
    if _agent_dismissed_at(architect):
        return _architect_dismissed_error(architect.id)
    if not architect.group:
        return {"type": "error", "message": "Architect is not assigned to a group"}

    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Engineer name is required"}

    pending_hire = await state.save_pending_hire_async({
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
        resolve_engineer_launch_config,
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
    if _agent_dismissed_at(architect):
        return _architect_dismissed_error(architect.id)
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
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        create_agent_with_config=create_agent_with_config,
        send_agent_prompt=send_agent_prompt,
    )
    if created.get("type") == "error":
        return created

    saved = await state.save_pending_hire_async({
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

    saved = await state.save_pending_hire_async({
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


def _handle_task_detail_command(data: dict, state: MatrixState) -> dict:
    """Return one full BoardTask dict for compact snapshot lazy-loading."""
    task_id = str(data.get("id", "") or data.get("task_id", "") or "").strip()
    if not task_id:
        return {"type": "error", "message": "task id required"}
    task = state.get_task_detail(task_id)
    if not task:
        return {"type": "error", "message": "Task not found"}
    return {
        "type": "task_detail",
        "id": task["id"],
        "task": task,
    }


def _handle_agent_message_history_command(
        data: dict, state: MatrixState) -> dict:
    """Return per-agent user-message recall history, newest first."""
    agent_id = str(
        data.get("agent_id", "") or data.get("cell_id", "") or data.get("id", "")
        or ""
    ).strip()
    if not agent_id:
        return {"type": "error", "message": "agent_id required"}
    try:
        limit = min(int(data.get("limit", 100)), 1000)
    except (TypeError, ValueError):
        limit = 100
    return {
        "type": "agent_message_history",
        "agent_id": agent_id,
        "history": state.agent_message_history_read(agent_id, limit=limit),
    }


def _handle_decisions_snapshot_command(data: dict, state: MatrixState) -> dict:
    """Return deferred architect decisions for compact snapshot clients."""
    include_archived = bool(data.get("include_archived", False))
    decisions = {
        decision["id"]: decision
        for decision in state.load_all_decisions(
            include_archived=include_archived,
        )
    }
    return {
        "type": "decisions_snapshot",
        "decisions": decisions,
    }


def _handle_pending_hires_snapshot_command(data: dict,
                                           state: MatrixState) -> dict:
    """Return deferred pending hires for compact snapshot clients."""
    status_filter = str(
        data.get("status_filter", data.get("status", "pending")) or ""
    ).strip()
    architect_id = str(data.get("architect_id", "") or "").strip()
    pending_hires = {
        pending_hire["id"]: pending_hire
        for pending_hire in state.load_pending_hires(
            status_filter=status_filter,
            architect_id=architect_id,
        )
    }
    return {
        "type": "pending_hires_snapshot",
        "pending_hires": pending_hires,
    }


def _handle_archived_tasks_command(data: dict, state: MatrixState) -> dict:
    """Return archived tasks on demand, excluded from compact initial state."""
    group = str(data.get("group", "") or "").strip()
    return {
        "type": "archived_tasks",
        "group": group,
        "board_tasks": state.get_archived_task_details(group=group),
    }


def _architect_ui_tool_is_read(name: str) -> bool:
    return str(name or "").strip() in {
        "architect_decision_list",
        "architect_task_list",
        "architect_peer_list",
        "architect_peer_inbox",
    }


async def _handle_engineer_journal_snapshot_command(
        data: dict,
        state: MatrixState) -> dict:
    """Return deferred Engineer journal/worklog/stream snapshots.

    Journal entries are author-keyed (`engineer_journal[cell_id]`) while the
    still group-wide worklog/stream slices remain keyed by group.
    """
    group = str(data.get("group", "") or "").strip()
    if not group:
        return {"type": "error", "message": "group required"}
    try:
        limit = int(data.get("limit", 50) or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        worklog_limit = int(data.get("worklog_limit", 200) or 200)
    except (TypeError, ValueError):
        worklog_limit = 200
    worklog_limit = max(1, min(worklog_limit, 500))

    streams = {
        "count": 0,
        "by_state": {},
        "items": [],
        "truncated": False,
    }
    if bool(data.get("include_streams", True)):
        try:
            from .worktree_streams import prefill_branch_exists_for_state
            await prefill_branch_exists_for_state(state)
            streams = state._engineer_stream_payload(group)
        except Exception:
            log.exception("Failed to load engineer streams for %s", group)

    engineer_id = str(
        data.get("engineer_id") or data.get("cell_id") or ""
    ).strip()
    if engineer_id:
        engineer_journal = {
            engineer_id: state.journal_read(
                group,
                limit=limit,
                author_cell_id=engineer_id,
            ),
        }
    else:
        engineer_journal = state.engineer_journal_snapshot_by_author(
            group=group,
            limit=limit,
        )

    return {
        "type": "engineer_journal_snapshot",
        "group": group,
        "engineer_journal": engineer_journal,
        "engineer_worklog": {
            group: [
                dict(entry)
                for entry in list(state.engineer_worklog.get(group, []))[:worklog_limit]
            ],
        },
        "engineer_streams": {
            group: streams,
        },
    }


def _handle_architect_journal_read_command(
        data: dict,
        state: MatrixState) -> dict:
    """Return recent architect journal entries for the standalone UI."""
    architect_id = str(data.get("architect_id", "") or "").strip()
    if not architect_id:
        return {"type": "error", "message": "architect_id required"}
    try:
        limit = int(data.get("limit", 200) or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 500))
    try:
        since = float(data.get("since", 0) or 0)
    except (TypeError, ValueError):
        since = 0.0
    entries = state.architect_journal_read(
        architect_id,
        since=since,
        limit=limit,
    )
    return {
        "type": "architect_journal_entries",
        "architect_id": architect_id,
        "limit": limit,
        "since": since,
        "entries": entries,
    }


def _event_ingest_config_payload(state: MatrixState) -> dict:
    gs = state.global_settings
    return {
        "max_rows": int(getattr(gs, "event_ingest_max_rows", 100_000) or 100_000),
        "max_age_days": int(getattr(gs, "event_ingest_max_days", 14) or 0),
        "args_capture": str(
            getattr(gs, "mcp_call_log_args_capture", "metadata") or "metadata"
        ),
        "full_capture_tools": list(
            getattr(gs, "mcp_call_log_full_capture_tools", []) or []
        ),
    }


async def _configure_event_ingest_client(event_ingest_client, state: MatrixState) -> None:
    if not event_ingest_client:
        return
    response = await event_ingest_client.configure(**_event_ingest_config_payload(state))
    if response.get("type") != "ok" or response.get("op") != "configure":
        raise RuntimeError(
            "event-ingest configure failed: "
            f"{response.get('message') or response!r}"
        )


# Worker actions that route through the `cmd=ai_report` server logic.
# A worker invokes one of these via the corresponding MCP tool
# (`mcp__torque__torque_<action>`). The MCP server in `torque/mcp.py` maps the
# tool to `cmd=ai_report` and calls `handle_command` directly (see the
# `action_map` there), so the same code path runs for every entry
# point. The set is also used to gate `_append_mcp`'s synthetic
# `mcp_call_append` delta below.
_TORQUE_AI_MCP_REPORT_ACTIONS = frozenset({
    "progress", "done", "blocked", "error",
    "ask", "derive", "ready", "verify", "name",
})
# Fully-qualified MCP tool names for the worker reporting surface,
# matching what Claude Code emits in its PostToolUse hook envelopes.
# Kept in sync with `_TORQUE_AI_MCP_REPORT_ACTIONS` so the `/events`
# capture clause can tell whether a given MCP tool's
# `mcp_call_append` was already emitted upstream by the ai_report
# handler (avoiding a duplicate live broadcast).
_TORQUE_AI_MCP_REPORT_TOOL_NAMES = frozenset(
    "mcp__torque__torque_" + action for action in _TORQUE_AI_MCP_REPORT_ACTIONS
)


def _mcp_call_rows_for_ui(state: MatrixState, records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        row = event_call_row_from_record(record)
        cell = state.agents.get(row.get("cell_id", ""))
        if cell:
            row["agent_name"] = cell.name
            row["agent_slug"] = cell.slug
            row["agent_kind"] = getattr(cell, "kind", "")
            row["group"] = getattr(cell, "group", "")
        rows.append(row)
    return rows


def _engineer_mcp_visible_cell_ids(state: MatrixState, engineer_id: str) -> set[str]:
    engineer_id = str(engineer_id or "").strip()
    engineer = state.get_active_agent(engineer_id)
    if not engineer:
        return set()
    group = str(getattr(engineer, "group", "") or "").strip()
    visible = {engineer_id}
    for cell in state.iter_active_agents():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if group and str(getattr(cell, "group", "") or "").strip() != group:
            continue
        owner = str(getattr(cell, "owner_engineer_id", "") or "").strip()
        creator = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
        if owner == engineer_id or creator == engineer_id:
            visible.add(cell.id)
    return visible


def _architect_mcp_visible_cell_ids(state: MatrixState, architect_id: str) -> set[str]:
    architect_id = str(architect_id or "").strip()
    architect = state.agents.get(architect_id)
    if not architect:
        return set()
    group = str(getattr(architect, "group", "") or "").strip()
    return {
        cell.id for cell in state.iter_active_agents()
        if getattr(cell, "cell_type", "") == "agent"
        and str(getattr(cell, "group", "") or "").strip() == group
    }


def _parse_mcp_call_query_params(data: dict) -> dict:
    try:
        limit = int(data.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))
    def _maybe_float(name):
        value = data.get(name)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    tool_pattern = (
        data.get("tool_name_pattern")
        or data.get("tool_filter")
        or "mcp__torque__%"
    )
    hook = str(data.get("hook_event_name") or data.get("hook") or "").strip()
    return {
        "tool_name_pattern": str(tool_pattern or "").strip(),
        "hook_event_name": hook,
        "since": _maybe_float("since"),
        "until": _maybe_float("until"),
        "limit": limit,
    }


async def _handle_mcp_calls_command(
    data: dict,
    state: MatrixState,
    event_ingest_client,
    *,
    scope: str = "trusted",
) -> dict:
    params = _parse_mcp_call_query_params(data)
    agent_ident = str(
        data.get("agent_id")
        or data.get("cell_id")
        or data.get("agent")
        or ""
    ).strip()
    requested_cell_id = _resolve_agent_id(state, agent_ident) if agent_ident else ""
    allowed_cell_ids: set[str] | None = None
    caller_id = str(data.get("caller_id") or data.get("_caller_id") or "").strip()
    if scope == "architect":
        allowed_cell_ids = _architect_mcp_visible_cell_ids(state, caller_id)
        if not allowed_cell_ids:
            return {"type": "error", "message": "architect not found"}
    elif scope == "engineer":
        allowed_cell_ids = _engineer_mcp_visible_cell_ids(state, caller_id)
        if not allowed_cell_ids:
            return {"type": "error", "message": "engineer not found"}

    query_cell_id = requested_cell_id
    query_cell_ids: list[str] | None = None
    if allowed_cell_ids is not None:
        if requested_cell_id:
            if requested_cell_id not in allowed_cell_ids:
                return {"type": "mcp_calls", "calls": [], "events": [], "limit": params["limit"]}
            query_cell_id = requested_cell_id
        else:
            query_cell_id = ""
            query_cell_ids = sorted(allowed_cell_ids)
    elif not query_cell_id and agent_ident:
        return {"type": "error", "message": f"Agent not found: {agent_ident}"}

    try:
        response = await event_ingest_client.query(
            cell_id=query_cell_id or None,
            cell_ids=query_cell_ids,
            **params,
        )
    except Exception as exc:
        log.exception("Failed to query event ingest MCP calls")
        return {"type": "error", "message": str(exc) or "event ingest unavailable"}
    if response.get("type") == "error":
        return {"type": "error", "message": response.get("message") or "query failed"}
    records = list(response.get("events") or [])
    rows = _mcp_call_rows_for_ui(state, records)
    return {
        "type": "mcp_calls",
        "cell_id": requested_cell_id or query_cell_id,
        "agent_id": requested_cell_id or query_cell_id,
        "scope": scope,
        "tool_name_pattern": params["tool_name_pattern"],
        "hook_event_name": params["hook_event_name"],
        "since": params["since"],
        "until": params["until"],
        "limit": params["limit"],
        "calls": rows,
        "events": rows,
        "settings": {
            "mcp_call_log_args_capture": state.global_settings.mcp_call_log_args_capture,
        },
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
        resolve_engineer_launch_config,
        resolve_architect_launch_config=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_engineer,
        send_agent_prompt=None,
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
            state, bridge, engineer, send_prompt=send_agent_prompt)
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
            resolve_engineer_launch_config=resolve_engineer_launch_config,
            apply_persistent_prompt=apply_persistent_prompt,
            build_cell_persistent_prompt=build_cell_persistent_prompt,
            persistent_prompt_filename=persistent_prompt_filename,
            is_designated_engineer=is_designated_engineer,
            send_agent_prompt=send_agent_prompt,
            preserve_cell_launch_config=True,
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
        state, bridge, engineer, send_prompt=send_agent_prompt)
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


async def _handle_architect_dismiss_command(
        data: dict,
        state: MatrixState, *,
        close_session,
        panel_event=None) -> dict:
    """Pause an architect by closing its session while preserving rows/history."""
    architect = _resolve_architect_cell(
        state,
        architect_id=data.get("architect_id", "") or data.get("id", ""),
        architect_slug=data.get("slug", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    authority_error = _validate_architect_lifecycle_authority(
        state,
        architect,
        caller_kind=data.get("caller_kind", "") or data.get("_caller_kind", ""),
    )
    if authority_error:
        return authority_error

    if _agent_dismissed_at(architect):
        return {
            "type": "ok",
            "architect_id": architect.id,
            "dismissed_at": _agent_dismissed_at(architect),
            "already_dismissed": True,
            "closed_sessions": 0,
        }

    dismissed_at = int(time.time())
    architect.dismissed_at = dismissed_at
    state._emit_agent(architect)
    state._db_save_agent(architect)

    errors: list[str] = []
    closed_sessions = 0
    if await _close_cell_session_preserving_state(
            state,
            architect,
            close_session,
            errors=errors):
        closed_sessions += 1

    reason = str(data.get("reason", "") or "").strip()
    state._emit(
        "architect_dismissed",
        architect_id=architect.id,
        group=architect.group,
        dismissed_at=dismissed_at,
    )
    if panel_event:
        panel_event(
            "architect_dismissed",
            architect.id,
            architect.name,
            architect.group,
            reason or "Architect dismissed",
        )
    result = {
        "type": "ok",
        "architect_id": architect.id,
        "dismissed_at": dismissed_at,
        "closed_sessions": closed_sessions,
    }
    if errors:
        result["close_errors"] = errors
    return result


async def _handle_architect_rehire_command(
        data: dict,
        state: MatrixState, *,
        bridge,
        worktree_mgr,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_engineer_launch_config,
        resolve_architect_launch_config=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_engineer,
        send_agent_prompt=None,
        panel_event=None) -> dict:
    """Resume a dismissed architect with the same id/slug and launch config."""
    architect = _resolve_architect_cell(
        state,
        architect_id=data.get("architect_id", "") or data.get("id", ""),
        architect_slug=data.get("slug", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    authority_error = _validate_architect_lifecycle_authority(
        state,
        architect,
        caller_kind=data.get("caller_kind", "") or data.get("_caller_kind", ""),
    )
    if authority_error:
        return authority_error

    dismissed_at = _agent_dismissed_at(architect)
    if not dismissed_at:
        return {
            "type": "ok",
            "architect_id": architect.id,
            "dismissed_at": 0,
            "already_hired": True,
            "replayed_messages": 0,
        }
    if architect.session_id:
        architect.dismissed_at = 0
        state._emit_agent(architect)
        state._db_save_agent(architect)
        replayed = await _replay_buffered_cross_kind_messages(
            state, bridge, architect, send_prompt=send_agent_prompt)
        state._emit(
            "architect_rehired",
            architect_id=architect.id,
            group=architect.group,
        )
        if panel_event:
            panel_event(
                "architect_rehired",
                architect.id,
                architect.name,
                architect.group,
                "Architect rehired",
            )
        return {
            "type": "ok",
            "architect_id": architect.id,
            "dismissed_at": 0,
            "already_running": True,
            "replayed_messages": replayed,
        }

    architect.status = "stopped"
    state._emit_agent(architect)
    state._db_save_agent(architect)

    async def _restore_dismissed_after_failed_rehire() -> None:
        if architect.session_id:
            try:
                await bridge.close_session(architect.session_id)
            except Exception:
                log.exception(
                    "Failed to close partial rehire session for '%s'",
                    architect.name,
                )
        architect.dismissed_at = dismissed_at
        architect.status = "stopped"
        architect.session_id = None
        state._emit_agent(architect)
        state._db_save_agent(architect)

    try:
        relaunch_result = await _handle_relaunch_agent_command(
            {"id": architect.id},
            state,
            bridge=bridge,
            worktree_mgr=worktree_mgr,
            resolve_base_dir=resolve_base_dir,
            resolve_agent_launch_config=resolve_agent_launch_config,
            resolve_engineer_launch_config=resolve_engineer_launch_config,
            resolve_architect_launch_config=resolve_architect_launch_config,
            apply_persistent_prompt=apply_persistent_prompt,
            build_cell_persistent_prompt=build_cell_persistent_prompt,
            persistent_prompt_filename=persistent_prompt_filename,
            is_designated_engineer=is_designated_engineer,
            send_agent_prompt=send_agent_prompt,
            preserve_cell_launch_config=True,
        )
    except Exception as exc:
        log.exception("Failed to rehire architect '%s'", architect.name)
        await _restore_dismissed_after_failed_rehire()
        return {"type": "error", "message": f"Failed to rehire architect: {exc}"}

    if relaunch_result and relaunch_result.get("type") == "error":
        await _restore_dismissed_after_failed_rehire()
        return relaunch_result
    if not architect.session_id:
        await _restore_dismissed_after_failed_rehire()
        return {
            "type": "error",
            "message": "Failed to rehire architect: no session was created",
        }

    architect.dismissed_at = 0
    state._emit_agent(architect)
    state._db_save_agent(architect)
    replayed = await _replay_buffered_cross_kind_messages(
        state, bridge, architect, send_prompt=send_agent_prompt)
    state._emit(
        "architect_rehired",
        architect_id=architect.id,
        group=architect.group,
    )
    if panel_event:
        panel_event(
            "architect_rehired",
            architect.id,
            architect.name,
            architect.group,
            "Architect rehired",
        )
    return {
        "type": "ok",
        "architect_id": architect.id,
        "dismissed_at": 0,
        "session_id": architect.session_id or "",
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
    for cell in list(state.iter_active_agents()):
        if cell.cell_type != "agent":
            continue
        owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
        creator_id = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
        if owner_id != engineer.id and creator_id != engineer.id:
            continue
        if owner_id == engineer.id:
            cell.owner_engineer_id = ""
        if creator_id == engineer.id:
            cell.created_by_engineer_id = ""
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

    tombstoned = await close_agent_session_only(engineer)
    del tombstoned
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
    for cell in list(state.iter_active_agents()):
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
        saved = await state.save_decision_async({
            "id": decision["id"],
            "archived": True,
        })
        if saved:
            archived_decisions += 1

    tombstoned = await close_agent_session_only(architect)
    del tombstoned
    return {
        "transferred_engineers": transferred_engineers,
        "archived_decisions": archived_decisions,
    }


async def _handle_remove_agent_command(
        data: dict,
        state: MatrixState, *,
        close_agent_session_only,
        cleanup_purged_agents) -> dict:
    """Remove a cell using soft-delete for agents and hard-delete for terminals."""
    cell = state.agents.get(str(data.get("id", "") or "").strip())
    if not cell:
        return {"type": "ok", "removed": []}
    if str(getattr(cell, "cell_type", "") or "") == "terminal":
        removed = state.remove_agent(cell.id)
        await cleanup_purged_agents(removed)
        return {"type": "ok", "removed": [c.id for c in removed]}
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "architect":
        return await _handle_delete_architect_command(
            {"id": cell.id},
            state,
            close_agent_session_only=close_agent_session_only,
        )
    if kind == "engineer":
        return await _handle_delete_engineer_command(
            {"id": cell.id},
            state,
            close_agent_session_only=close_agent_session_only,
        )
    tombstoned = await close_agent_session_only(cell)
    return {
        "type": "ok",
        "tombstoned": [c.id for c in tombstoned],
    }


def _restore_or_purge_authority_error(state: MatrixState, cell, data: dict) -> dict | None:
    """Return architect-scope authorization error for restore/purge commands."""
    architect_id = str(data.get("architect_id", "") or "").strip()
    if not architect_id:
        return None
    architect = _resolve_architect_cell(state, architect_id=architect_id)
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    if str(getattr(cell, "kind", "") or "").strip() != "engineer":
        return {"type": "error", "message": "engineer not found in scope"}
    if str(getattr(cell, "hired_by_architect_id", "") or "").strip() != architect.id:
        return {"type": "error", "message": "engineer not found in scope"}
    return None


def _handle_restore_agent_command(data: dict, state: MatrixState) -> dict:
    aid = str(data.get("id", "") or data.get("engineer_id", "") or "").strip()
    cell = state.agents.get(aid)
    if not cell:
        return {"type": "error", "message": "Agent not found"}
    authority_error = _restore_or_purge_authority_error(state, cell, data)
    if authority_error:
        return authority_error
    if not state.agent_is_tombstoned(cell):
        return {"type": "ok", "restored": [], "already_active": True}
    restored = state.restore_agent(cell.id)
    return {"type": "ok", "restored": [c.id for c in restored]}


async def _handle_purge_agent_now_command(
        data: dict,
        state: MatrixState, *,
        cleanup_purged_agents) -> dict:
    aid = str(data.get("id", "") or data.get("engineer_id", "") or "").strip()
    cell = state.agents.get(aid)
    if not cell:
        return {"type": "error", "message": "Agent not found"}
    authority_error = _restore_or_purge_authority_error(state, cell, data)
    if authority_error:
        return authority_error
    if not state.agent_is_tombstoned(cell):
        return {
            "type": "error",
            "message": "Agent is not tombstoned; use Delete first",
        }
    removed = state.purge_agent_now(cell.id)
    await cleanup_purged_agents(removed)
    return {"type": "ok", "purged": [c.id for c in removed]}


def _handle_recently_deleted_agents_command(
        data: dict,
        state: MatrixState) -> dict:
    group = str(data.get("group", "") or "").strip()
    agents = []
    for cell in state.iter_agents(include_tombstoned=True):
        if not state.agent_is_tombstoned(cell):
            continue
        if group and str(getattr(cell, "group", "") or "").strip() != group:
            continue
        agents.append(asdict(cell))
    agents.sort(key=lambda item: (float(item.get("deleted_at") or 0), item["id"]))
    return {"type": "ok", "agents": agents}


async def _dispatch_architect_ui_tool(name: str, args: dict,
                                      state: MatrixState, *,
                                      handle_command=None) -> dict:
    """Run an architect-scoped shared-core tool for the user-facing UI."""
    from .mcp_tools_shared import dispatch_scoped_tool

    caller_id = str(
        args.get("sender_architect_id", "")
        or args.get("caller_architect_id", "")
        or args.get("architect_id", "")
        or ""
    ).strip()
    if not caller_id:
        return {"type": "error", "message": "architect_id is required"}
    caller = state.agents.get(caller_id)
    if (
            _agent_dismissed_at(caller)
            and not _architect_ui_tool_is_read(name)):
        return _architect_dismissed_error(caller_id)

    async def _restricted_handle_command(_data: dict) -> dict:
        cmd = str((_data or {}).get("cmd", "") or "").strip()
        if handle_command and cmd in {
                "board_update_task",
                "inject_mcp_message",
                "list_actions",
        }:
            return await handle_command(dict(_data or {}))
        return {
            "type": "error",
            "message": "Architect UI command cannot route nested commands",
        }

    payload_text, is_error = await dispatch_scoped_tool(
        name,
        args,
        _restricted_handle_command,
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
        resolve_engineer_launch_config,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_engineer,
        send_agent_prompt=None,
        preserve_cell_launch_config: bool = False) -> dict | None:
    """Relaunch a stopped agent or terminal using current launch settings.

    When the new session is opened against a fresh provider conversation
    (no ``agent_session_id`` to resume into, or ``session_resume`` disabled),
    the role's startup + initial prompts are re-delivered via
    ``_new_agent_prompt_sequence`` so codex engineers/architects get their
    persistent prompt seated as the first turn and any role kickoff text
    fires. When both signals indicate a viable resume, the kickoff is
    skipped — the resumed conversation already carries that context.
    """
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
    resolver = _launch_resolver_for_cell(
        cell,
        resolve_agent_launch_config=resolve_agent_launch_config,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        resolve_architect_launch_config=resolve_architect_launch_config,
        resolve_worker_launch_config=resolve_worker_launch_config,
        is_designated_engineer=is_designated_engineer,
    )
    launch_cfg = resolver(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    if cell.cell_type == "agent" and preserve_cell_launch_config:
        # Rehire resumes the same durable person, so keep the provider and
        # command captured on the cell even if group launch settings have
        # since changed.  Plain relaunch is intentionally different: it
        # honors the current resolved launch settings and only falls back
        # to cell values for blank resolver fields below.
        if cell.command:
            launch_cfg["command"] = _relaunch_command_base(
                cell.command,
                persistent_prompt_filename(cell),
            )
        if cell.agent_type:
            launch_cfg["agent_type"] = cell.agent_type
    cell.session_resume = bool(
        launch_cfg.get("session_resume", cell.session_resume))
    cell.idle_timeout = int(
        launch_cfg.get("idle_timeout", cell.idle_timeout) or 0)
    if cell.cell_type == "agent":
        # Fall back to the cell's persisted values when the re-resolved
        # launch_cfg has empty entries.  The group-level engineer_settings
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
        or ".torque/worktrees")
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
                    base_dir=cell.worktree_base_dir or ".torque/worktrees",
                    base_branch=launch_cfg.get("worktree_base_branch", "") or "",
                    symlinks=launch_cfg.get("worktree_symlinks", []),
                    include_gitignored_symlinks=launch_cfg.get(
                        "worktree_symlink_gitignored_paths", False),
                    worktree_submodules=launch_cfg.get(
                        "worktree_submodules", []),
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
    persistent_prompt_text = build_cell_persistent_prompt(cell, launch_cfg)
    apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
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

    # Fresh-session kickoff: when the new session has no prior provider
    # conversation to resume into (no agent_session_id, or session_resume
    # disabled), re-deliver the startup + initial prompts. This restores
    # codex's persistent system prompt as the first turn (codex has no
    # file-injection equivalent of claude-code's --append-system-prompt-file)
    # and fires any role-defined initial_prompt for both providers. When
    # both signals indicate a viable resume, the kickoff is skipped to
    # avoid duplicating the system prompt onto the resumed conversation.
    if (
            send_agent_prompt
            and cell.cell_type == "agent"
            and cell.session_id
            and (not cell.agent_session_id or not cell.session_resume)
    ):
        startup_prompt = _startup_prompt_for_new_agent(
            agent_type=launch_cfg.get("agent_type", ""),
            persistent_prompt_text=persistent_prompt_text,
        )
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg, startup_prompt=startup_prompt, cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return None


async def _handle_restart_agent_command(
        data: dict,
        state: MatrixState, *,
        bridge,
        worktree_mgr,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_engineer_launch_config,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        persistent_prompt_filename,
        is_designated_engineer,
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
    if str(getattr(cell, "kind", "") or "").strip() == "architect":
        state.refresh_peer_message_cache_for_agent(cell.id, emit=False)

    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    resolver = _launch_resolver_for_cell(
        cell,
        resolve_agent_launch_config=resolve_agent_launch_config,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        resolve_architect_launch_config=resolve_architect_launch_config,
        resolve_worker_launch_config=resolve_worker_launch_config,
        is_designated_engineer=is_designated_engineer,
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
        or ".torque/worktrees")
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
    kind = str(getattr(cell, "kind", "") or "").strip()
    persistent_prompt_text = build_cell_persistent_prompt(cell, launch_cfg)
    if kind == "architect":
        persistent_prompt_text = _architect_persistent_prompt_text(
            group=cell.group,
            action_system_prompt=launch_cfg.get("system_prompt", ""),
            state=state,
        )
    apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
    state._emit_agent(cell)
    state._db_save_agent(cell)

    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
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
                cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return None


async def main(connection=None):
    log.info("Torque starting (port=%d)", WS_PORT)
    profiling.configure_asyncio(asyncio.get_running_loop())
    cloud_connector_runtime = None
    db = TorqueDB(DB_FILE)
    db.init()
    log.info("SQLite database opened at %s", DB_FILE)
    state = MatrixState(db=db)
    state.load()
    db.enable_async_writes(True)
    capture_deploy_boot_state(state, torque_config.SCRIPT_DIR)
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    event_log = EventLog()
    panel_log = PanelEventLog(
        max_size=state.global_settings.max_event_log, db=db)
    state.panel_log = panel_log
    notifier = NotificationManager(state)
    state.notification_manager = notifier
    notifier.start()
    event_bus = EventBus(state, event_log, notifier, panel_log=panel_log)
    event_bus.start()
    asyncio.create_task(health_check(state, event_log, event_bus, notifier))

    # Defence in depth against the proc-ceiling root cause: reap any orphaned
    # event-ingest / pty-supervisor sidecars left behind by killed daemons
    # whose temp data dirs have since been deleted. Spare our own DATA_DIR and
    # any sibling profile so a co-resident live daemon's sidecars survive.
    try:
        from . import sidecar_reaper

        spare_dirs = [DATA_DIR]
        profiles_root = Path.home() / ".torque" / "profiles"
        if profiles_root.exists():
            spare_dirs.extend(p for p in profiles_root.iterdir() if p.is_dir())
        reaped = await asyncio.to_thread(
            sidecar_reaper.reap_orphaned_sidecars, spare_data_dirs=spare_dirs
        )
        if reaped:
            log.info("Reaped %d orphaned sidecar(s) at startup", len(reaped))
    except Exception:
        log.exception("Orphaned-sidecar reap at startup failed (non-fatal)")

    from .event_ingest_client import EventIngestClient

    event_ingest_client = EventIngestClient(data_dir=DATA_DIR)
    event_ingest_configured = [False]
    try:
        await event_ingest_client.connect()
        await _configure_event_ingest_client(event_ingest_client, state)
        event_ingest_configured[0] = True
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
    log.info("Event bus, event-ingest client, health monitor, "
             "and notifications initialized")

    async def _ensure_event_ingest_configured():
        if event_ingest_configured[0]:
            return
        await _configure_event_ingest_client(event_ingest_client, state)
        event_ingest_configured[0] = True

    async def _on_event_ingest_reconnect(_info):
        event_ingest_configured[0] = False
        await _ensure_event_ingest_configured()

    event_ingest_client.on_reconnect = _on_event_ingest_reconnect

    supervisor_banner: dict | None = None
    from .local_pty import LocalPtyAdapter, SupervisedPtyAdapter

    if torque_config.PROFILE_SKIP_PTY:
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
                    "survive a Torque restart. See torque.log for details."
                ),
                "detail": str(exc),
            }
            bridge = LocalPtyAdapter(state)
    worktree_mgr = WorktreeManager()
    action_mgr = ActionManager()
    template_mgr = RoleManager()
    specialization_mgr = SpecializationManager()

    # Install the fail-closed worktree-isolation guard hook for every repo
    # root we already know about, so existing checkouts are protected without
    # waiting for the next worktree creation (TORQUE:580). Idempotent and
    # never clobbers a foreign pre-commit hook.
    try:
        from .worktree import ensure_worktree_isolation_guard
        _guarded_roots: set[str] = set()
        for _cell in list(state.agents.values()):
            _root = (getattr(_cell, "worktree_repo_root", "") or "").strip()
            if _root and _root not in _guarded_roots:
                _guarded_roots.add(_root)
                ensure_worktree_isolation_guard(_root)
    except Exception:
        log.debug("Could not install worktree-isolation guard at startup",
                  exc_info=True)
    agent_launch = AgentLaunchService(
        state=state,
        connection=connection,
        bridge=bridge,
        worktree_mgr=worktree_mgr,
        template_mgr=template_mgr,
    )

    def _resolve_engineer_specializations_preamble(cell) -> str:
        """Return the combined specialization preamble for an engineer cell."""
        if not cell:
            return ""
        names = list(
            getattr(cell, "engineer_specializations", []) or [])
        if not names:
            return ""
        base_dir = getattr(cell, "directory", "") or ""
        try:
            return specialization_mgr.render_engineer_preamble(
                names, base_dir=base_dir)
        except Exception:
            log.exception(
                "failed to render engineer specializations for %s",
                getattr(cell, "id", ""))
            return ""

    from .engineer import EngineerEventBuffer
    async def _inject_digest_message(target, message: str, **kwargs):
        await inject_mcp_message(state, bridge, target, message, **kwargs)

    engineer_buffer = EngineerEventBuffer(
        state,
        bridge,
        inject_message=_inject_digest_message,
    )
    engineer_buffer.start()
    event_bus._engineer_buffer = engineer_buffer
    panel_log.on_event = engineer_buffer.on_panel_event
    log.info("Engineer event buffer started")


    def _worktree_remove_skip_result(cell, reason: str, *,
                                     shared_with: list | None = None) -> dict:
        return {
            "ok": False,
            "worktree_removed": False,
            "branch_deleted": False,
            "skipped": True,
            "reason": reason,
            "message": reason,
            "agent_id": getattr(cell, "id", ""),
            "agent_name": getattr(cell, "name", ""),
            "path": getattr(cell, "worktree_path", ""),
            "branch": getattr(cell, "worktree_branch", ""),
            "shared_with": [
                {
                    "id": getattr(agent, "id", ""),
                    "name": getattr(agent, "name", ""),
                }
                for agent in (shared_with or [])
            ],
            "mismatches": [],
        }

    def _clear_worktree_tracking(cell) -> None:
        cell.worktree_path = ""
        cell.worktree_branch = ""
        cell.worktree_base_branch = ""
        cell.worktree_repo_root = ""
        cell.worktree_dirty = False
        cell.worktree_diff = {}
        cell.worktree_changed_files = []
        cell.worktree_checkpoints = 0
        cell.worktree_ahead = 0
        cell.worktree_behind = 0
        cell.worktree_merged = False

    def _worktree_submodules_for_cell(cell) -> list[str]:
        if not cell:
            return []
        try:
            gs = state.get_group_settings(getattr(cell, "group", "") or "")
            return list(getattr(gs, "worktree_submodules", []) or [])
        except Exception:
            return []

    async def _checkpoint_worktree_with_submodules(cell, message: str = ""):
        submodules = _worktree_submodules_for_cell(cell)
        if submodules:
            return await worktree_mgr.checkpoint(
                cell,
                message=message,
                worktree_submodules=submodules,
            )
        return await worktree_mgr.checkpoint(cell, message=message)

    async def _safe_remove_worktree_result(cell) -> dict:
        """Remove a worktree only when it is not active/shared, then verify."""
        if not cell or not cell.worktree_path:
            return {
                "ok": True,
                "worktree_removed": True,
                "branch_deleted": True,
                "skipped": True,
                "message": "No worktree path configured",
                "mismatches": [],
            }

        refusal = _worktree_removal_refusal_reason(state, cell)
        if refusal:
            log.info("Skipping worktree removal for '%s' — %s",
                     cell.name, refusal)
            return _worktree_remove_skip_result(cell, refusal)

        same_path = str(cell.worktree_path or "")
        other_users = [
            a for a in state.agents.values()
            if a.id != cell.id
            and not state.agent_is_tombstoned(a)
            and (
                _worktree_path_contains(same_path, getattr(a, "worktree_path", ""))
                or _worktree_path_contains(same_path, getattr(a, "directory", ""))
                or _worktree_path_contains(same_path, getattr(a, "current_path", ""))
                or _worktree_path_contains(same_path, getattr(a, "git_root", ""))
            )
        ]
        active_other_users = []
        for other in other_users:
            other_reason = _worktree_removal_refusal_reason(state, other)
            status = str(getattr(other, "status", "") or "").strip().lower()
            non_stopped = status not in {"", "stopped", "error"}
            latest_activity = max(
                _timestamp_to_unix(getattr(other, "last_progress_at", 0.0)),
                _timestamp_to_unix(getattr(other, "last_heartbeat_at", 0.0)),
                _timestamp_to_unix(getattr(other, "last_activity_at", 0.0)),
                _timestamp_to_unix(getattr(other, "last_event_at", 0.0)),
            )
            if other_reason or (
                getattr(other, "session_id", None) and status != "stopped"
            ) or status == "running" or (
                non_stopped and _agent_has_open_assigned_tasks(state, other.id)
            ) or (
                non_stopped
                and latest_activity
                and time.time() - latest_activity <= (
                    _WORKTREE_REMOVAL_FRESH_AGENT_SECONDS
                )
            ):
                active_other_users.append(other)
        if active_other_users:
            names = ", ".join(a.name for a in active_other_users)
            reason = (
                "skipped: worktree belongs to active/fresh agent "
                f"shared with {names}"
            )
            log.info("Skipping worktree removal for '%s' — %s",
                     cell.name, reason)
            return _worktree_remove_skip_result(
                cell,
                reason,
                shared_with=active_other_users,
            )

        if hasattr(worktree_mgr, "remove_result"):
            submodules = _worktree_submodules_for_cell(cell)
            if submodules:
                result = await worktree_mgr.remove_result(
                    cell,
                    worktree_submodules=submodules,
                )
            else:
                result = await worktree_mgr.remove_result(cell)
        else:
            ok = await worktree_mgr.remove(cell)
            result = {
                "ok": bool(ok),
                "worktree_removed": bool(ok),
                "branch_deleted": bool(ok),
                "skipped": False,
                "message": (
                    "Worktree removed" if ok
                    else "Worktree removal failed"
                ),
                "mismatches": [],
            }

        if result.get("worktree_removed"):
            # If inactive/tombstoned cells shared the same worktree metadata,
            # reconcile their Torque tracking with the verified git state too.
            for other in other_users:
                _clear_worktree_tracking(other)
                state._emit_agent(other)
                state._db_save_agent(other)
        return result

    async def _safe_remove_worktree(cell):
        result = await _safe_remove_worktree_result(cell)
        return bool(result.get("ok"))

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
                    remove_result = await _safe_remove_worktree_result(c)
                    if remove_result.get("worktree_removed"):
                        removed_worktree = True
                    if not remove_result.get("ok"):
                        cleanup["errors"].append(
                            remove_result.get("message")
                            or f"Failed to remove worktree for '{c.name}'."
                        )
                    for mismatch in remove_result.get("mismatches", []) or []:
                        cleanup["errors"].append(
                            f"Worktree removal mismatch for '{c.name}': "
                            f"{mismatch}"
                        )
                cleanup["worktree_removed"] = removed_worktree
            return cleanup

        repo_root = cell.worktree_repo_root
        remove_result = await _safe_remove_worktree_result(cell)
        ok = bool(remove_result.get("ok"))
        if remove_result.get("worktree_removed"):
            cleanup["worktree_removed"] = True
        if not ok:
            cleanup["errors"].append(
                remove_result.get("message")
                or f"Failed to remove worktree for '{cell.name}'."
            )
        for mismatch in remove_result.get("mismatches", []) or []:
            cleanup["errors"].append(
                f"Worktree removal mismatch for '{cell.name}': {mismatch}"
            )
        if remove_result.get("worktree_removed") and repo_root:
            cell.directory = repo_root
        if remove_result.get("worktree_removed") \
                and cell.cell_type == "agent" and cell.session_id:
            await _relaunch_agent_after_worktree_removal(
                cell,
                bridge=bridge,
                state=state,
                resolve_base_dir=_resolve_base_dir,
                resolve_agent_launch_config=_resolve_agent_launch_config,
                resolve_engineer_launch_config=_resolve_engineer_launch_config,
                resolve_architect_launch_config=_resolve_architect_launch_config,
                resolve_worker_launch_config=_resolve_worker_launch_config,
                is_designated_engineer=_is_designated_engineer,
                apply_persistent_prompt=_apply_persistent_prompt,
                build_cell_persistent_prompt=_build_cell_persistent_prompt,
                send_agent_prompt=_send_agent_prompt,
            )
        else:
            state._emit_agent(cell)
            state._db_save_agent(cell)
        return cleanup

    async def _close_agent_session_only(cell, *,
                                        errors: list | None = None) -> list:
        """Tombstone an agent and close live sessions without final cleanup."""
        if not cell:
            return []
        session_ids = {
            c.id: c.session_id
            for c in state._agent_cascade_cells(cell.id)
            if c.session_id
        }
        removed = state.remove_agent(cell.id)
        for c in removed:
            session_id = session_ids.get(c.id)
            if session_id:
                try:
                    await bridge.close_session(session_id)
                except Exception as exc:
                    if errors is not None:
                        errors.append(
                            f"Failed to close session for '{c.name}': {exc}"
                        )
                    log.exception("Failed to close session for '%s'", c.name)
        return removed

    async def _cleanup_purged_agents(removed: list, *,
                                     errors: list | None = None) -> None:
        """Run irreversible filesystem/runtime cleanup for hard-purged cells."""
        for c in removed:
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
            if c.worktree_path:
                ok = await _safe_remove_worktree(c)
                if not ok and errors is not None:
                    errors.append(f"Failed to remove worktree for '{c.name}'.")

    async def _tombstone_sweeper():
        """Periodically purge expired soft-deleted agents."""
        while True:
            await asyncio.sleep(300)
            try:
                removed = state.purge_tombstoned_agents()
                if removed:
                    await _cleanup_purged_agents(removed)
                    await state.broadcast()
                    log.info("Purged %d expired agent tombstone(s)", len(removed))
            except Exception:
                log.exception("Agent tombstone sweeper failed")

    def _checkpoint_message(cell) -> str:
        """Build a checkpoint commit message from the agent's last summary."""
        summary = cell.last_summary.strip()
        n = cell.worktree_checkpoints + 1
        subject = f"torque: checkpoint {n} — {cell.name}"
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
            sha = await _checkpoint_worktree_with_submodules(cell, msg)
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
        subject = f"torque: checkpoint {n} — {cell.name}"
        if message:
            msg = f"{subject}\n\n{message}"
        else:
            msg = subject
        sha = await _checkpoint_worktree_with_submodules(cell, msg)
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
                "Torque — supervisor restarted",
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

    async def _on_agent_session_end_detected(cell, data=None):
        """Convert bridge-detected turn completion into a normal AgentEvent."""
        if str(getattr(cell, "agent_type", "") or "") != "codex":
            return
        if str(getattr(cell, "status", "") or "") != "running":
            return
        payload = {
            "reason": "pty_idle_screen",
            "source": "codex_idle_screen_backstop",
        }
        payload.update(dict(data or {}))
        await event_bus.emit(AgentEvent(
            cell_id=cell.id,
            timestamp=time.time(),
            event_type="session_end",
            data=payload,
        ))

    # Signal bridge when agent TUI is ready (hook-based session_start)
    event_bus.on_session_start = _make_agent_session_start_handler(
        state,
        bridge,
        lambda: _send_agent_prompt,
    )
    # Handle agent turn completion (hook-based session_end)
    event_bus.on_session_end = _on_agent_session_end
    # Handle codex turn completion detected by the PTY idle-screen backstop.
    if hasattr(bridge, "on_agent_session_end_detected"):
        bridge.on_agent_session_end_detected = _on_agent_session_end_detected
    # Also checkpoint when the terminal session is actually closed (tab closed)
    bridge.on_session_terminated = _on_agent_session_end
    event_ingest_drainer.start()
    log.info("Durable event-ingest drainer started after EventBus callbacks")

    async def _state_payload(*, compact: bool = False) -> dict:
        # Prefill the per-repo branch cache before legacy state.to_dict()
        # runs — otherwise the sync engineer-stream snapshot inside it would
        # fork `git show-ref` per branch on the event loop, stalling the WS.
        if not compact:
            try:
                from .worktree_streams import prefill_branch_exists_for_state
                await prefill_branch_exists_for_state(state)
            except Exception:
                log.exception("Branch-exists prefill failed for state payload")
        state_payload = state.to_dict_compact() if compact else state.to_dict()
        return {
            "type": "state",
            "seq": state._seq,
            **state_payload,
            **engineer_buffer.export_state(),
            "providers": get_providers(),
            "runtime": _runtime_payload(bridge=bridge, state=state),
        }

    terminal_clients: dict[str, set[web.WebSocketResponse]] = {}
    daemon_stop_state = _DaemonStopState()
    daemon_stop_event = asyncio.Event()
    daemon_stop_task: asyncio.Task | None = None

    def _schedule_daemon_stop() -> None:
        nonlocal daemon_stop_task
        if daemon_stop_event.is_set():
            return
        if daemon_stop_task and not daemon_stop_task.done():
            return

        async def _trigger_stop_after_response_grace() -> None:
            await asyncio.sleep(_DAEMON_STOP_TRIGGER_DELAY_SECONDS)
            daemon_stop_event.set()

        daemon_stop_task = asyncio.create_task(_trigger_stop_after_response_grace())

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
    state.sync_ui_selection_to_session(
        state.active_session_id or "",
        emit=False,
    )
    log.info("Startup checkpoint: orphan reconnect complete")
    asyncio.create_task(_worktree_diff_updater(state, worktree_mgr))
    log.info("Startup checkpoint: worktree diff updater scheduled")
    asyncio.create_task(_tombstone_sweeper())
    log.info("Startup checkpoint: tombstone sweeper scheduled")

    async def _resolve_base_dir(group: str = "") -> str:
        return await agent_launch.resolve_base_dir(group)

    def _resolve_deliverable_for_create(
        action_name: str,
        base_dir: str,
        explicit: dict | None,
    ) -> dict:
        """Resolve a task's deliverable contract at create time.

        Explicit kwargs (from the MCP/HTTP caller) win over the action's
        ``deliverable`` block. Returns the normalized contract dict.
        """
        from .actions import normalize_deliverable
        contract = {"required": False, "type": "", "format": "",
                    "artifact_title": ""}
        if action_name:
            try:
                contract = action_mgr.get_deliverable(action_name, base_dir)
            except Exception:
                log.exception(
                    "Failed to load action deliverable for '%s'", action_name)
        if isinstance(explicit, dict) and explicit:
            override = normalize_deliverable(explicit)
            for key in ("required", "type", "format", "artifact_title"):
                ev = override.get(key)
                if key == "required":
                    if "required" in explicit:
                        contract["required"] = bool(ev)
                elif ev:
                    contract[key] = ev
        return contract

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

    def _resolve_engineer_launch_config(group: str, *,
                                      base_dir: str = "",
                                      explicit_template: str = "",
                                      overrides: dict | None = None) -> dict:
        return agent_launch.resolve_engineer_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=overrides,
        )

    def _resolve_worker_launch_config(group: str, *,
                                      base_dir: str = "",
                                      explicit_template: str = "",
                                      overrides: dict | None = None) -> dict:
        return agent_launch.resolve_worker_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=overrides,
        )

    def _resolve_architect_launch_config(group: str, *,
                                      base_dir: str = "",
                                      explicit_template: str = "",
                                      overrides: dict | None = None) -> dict:
        return agent_launch.resolve_architect_launch_config(
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
                                        created_by_engineer_id: str = "",
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
            created_by_engineer_id=created_by_engineer_id,
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

    async def _ingest_remote_user_agent_message(payload: dict) -> dict:
        return await ingest_remote_user_agent_message(
            payload,
            state=state,
            send_prompt=_send_agent_prompt,
            handler=_handle_user_agent_message_command,
        )

    def _recent_user_direct_messages(limit: int) -> list[dict]:
        """Bounded recent user↔agent rows for the remote snapshot-on-open.

        Returns newest-first canonical direct-message rows from the same
        agent_peer_messages source that feeds live egress; the connector
        applies the user-destined gate + payload shaping.  Never unbounded.
        """
        db = getattr(state, "db", None)
        loader = getattr(db, "load_recent_user_direct_messages", None) if db else None
        if not callable(loader):
            return []
        try:
            return loader(limit=max(1, int(limit or 1)))
        except Exception:
            log.exception("recent user direct-message snapshot load failed")
            return []

    # -- Cloud connector (relay) wiring ---------------------------------------
    # Mutable holder so a relay-settings save can stop + restart the connector
    # in place (apply-on-change) without a daemon restart.
    cloud_connector_runtime_holder: list = [None]

    def _build_cloud_connector_context() -> cloud_hooks.CloudConnectorContext:
        """Construct the connector context from Global Settings (settings-primary,
        env / ee_connector.json fallback for unset fields)."""
        resolved = cloud_hooks.resolve_relay_config(
            state.global_settings, data_dir=str(DATA_DIR)
        )
        # Publish resolved config + per-field provenance for the Settings UI.
        state.set_relay_config(resolved)
        config = dict(resolved.get("config", {}))
        config["module"] = torque_config.CLOUD_CONNECTOR_MODULE
        return cloud_hooks.CloudConnectorContext(
            state=state,
            remote_user_agent_message=_ingest_remote_user_agent_message,
            recent_direct_messages=_recent_user_direct_messages,
            agent_roster=lambda: _relay_agent_roster(state),
            register_direct_message_observer=(
                cloud_hooks.register_direct_message_observer
            ),
            report_connection_state=(
                lambda payload: state.set_relay_connection(payload)
            ),
            profile=str(os.environ.get("TORQUE_PROFILE", "") or ""),
            data_dir=str(DATA_DIR),
            config=config,
        )

    def _relay_settings_fingerprint() -> tuple:
        gs = state.global_settings
        return (
            bool(gs.relay_enabled),
            gs.relay_url,
            gs.relay_daemon_id,
            gs.relay_credential_id,
            gs.relay_private_key_path,
        )

    async def _restart_cloud_connector() -> None:
        """Apply-on-change: stop the running connector and start a fresh one with
        the current settings-derived config. Defensive / non-fatal — a relay
        misconfig must never crash the settings-save path. The :601
        relay_connection signal surfaces the resulting connect/disconnect/error.
        """
        try:
            await cloud_hooks.stop_cloud_connector(
                cloud_connector_runtime_holder[0]
            )
        except Exception:
            log.exception("Cloud connector stop during settings apply failed")
        runtime = None
        try:
            runtime = await cloud_hooks.start_cloud_connector(
                _build_cloud_connector_context()
            )
        except Exception:
            log.exception("Cloud connector restart during settings apply failed")
        cloud_connector_runtime_holder[0] = runtime
        # When the connector is now disabled, clear the relay signal back to
        # "disabled" (a disabled connector reports nothing on its own).
        if runtime is not None and not runtime.enabled:
            state.set_relay_connection(None)

    # -- Persistent system prompt ---------------------------------------------

    def _build_dispatch_persistent_prompt(system_prompt: str = "",
                                          owner_is_user: bool = False) -> str:
        parts = []
        if system_prompt:
            parts.append(system_prompt.rstrip())
        parts.append(
            build_torque_system_prompt(owner_is_user=owner_is_user).rstrip()
        )
        return "\n\n".join(parts) + "\n"

    def _build_cell_persistent_prompt(cell, launch_cfg: dict) -> str:
        if cell.cell_type != "agent" or not launch_cfg.get("agent_type"):
            return ""
        gs = state.get_group_settings(cell.group)
        if gs.engineer_agent_id == cell.id or cell.kind == "engineer":
            from .engineer import build_engineer_system_prompt
            ws = state.get_engineer_settings(cell.group)
            spec_preamble = _resolve_engineer_specializations_preamble(cell)
            return build_engineer_system_prompt(
                cell.group, ws, launch_cfg.get("system_prompt", ""),
                group_settings=gs,
                specializations_preamble=spec_preamble,
                owner_is_user=_agent_owner_is_user(cell))
        if cell.kind == "architect":
            return _architect_persistent_prompt_text(
                group=cell.group,
                action_system_prompt=launch_cfg.get("system_prompt", ""),
                state=state,
            )
        return _build_dispatch_persistent_prompt(
            launch_cfg.get("system_prompt", ""),
            owner_is_user=_agent_owner_is_user(cell))

    def _is_designated_engineer(cell) -> bool:
        if not cell or cell.cell_type != "agent":
            return False
        gs = state.get_group_settings(cell.group)
        return bool(gs and gs.engineer_agent_id == cell.id)

    def _ownership_engineer_id_for_dispatch_source(cell) -> str:
        """Return the immutable Engineer owner id to stamp on new agents."""
        if not cell or cell.cell_type != "agent":
            return ""
        owner_id = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
        if owner_id:
            return owner_id
        if _is_designated_engineer(cell):
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
            elif "torque:error" in (task.labels or []):
                outcome = "error"
            elif "torque:blocked" in (task.labels or []):
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

    def _live_history_status(cell, record: dict | None = None) -> str:
        """Return the history status implied by the current live cell."""
        existing = str((record or {}).get("status", "") or "").strip()
        if state.agent_is_tombstoned(cell):
            return "merged" if existing == "merged" else "removed"
        if bool(getattr(cell, "worktree_merged", False)):
            return "merged"
        status = str(getattr(cell, "status", "") or "").strip()
        kind = str(getattr(cell, "kind", "") or "").strip()
        if status and status != "stopped":
            return "active"
        if bool(getattr(cell, "persistent", False)) and kind in {"architect", "engineer"}:
            return "active"
        return existing or "active"

    def _enrich_history_record(record: dict) -> dict:
        """Overlay live metadata and task counts for active agents."""
        if not record:
            return record
        record = dict(record)
        cell = state.agents.get(record.get("id", ""))
        if cell and cell.cell_type == "agent":
            live_count = max(
                int(cell.tasks_dispatched or 0),
                len(_current_board_tasks_for_agent(cell.id)),
            )
            record.update({
                "name": cell.name or record.get("name", ""),
                "slug": cell.slug or record.get("slug", ""),
                "group": cell.group or record.get("group", ""),
                "agent_type": cell.agent_type or record.get("agent_type", ""),
                "template": cell.template or record.get("template", ""),
                "worktree_branch": (
                    cell.worktree_branch or record.get("worktree_branch", "")
                ),
                "kind": str(getattr(cell, "kind", "") or "").strip(),
                "status": _live_history_status(cell, record),
            })
            record["total_tasks"] = max(
                int(record.get("total_tasks") or 0), live_count)
        return record

    def _live_history_record(cell, base: dict | None = None) -> dict:
        """Synthesize/refresh a history row from a live agent cell."""
        base = dict(base or {})
        live_count = max(
            int(getattr(cell, "tasks_dispatched", 0) or 0),
            len(_current_board_tasks_for_agent(cell.id)),
        )
        return {
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "group": cell.group,
            "agent_type": cell.agent_type,
            "template": cell.template,
            "created_at": base.get("created_at")
                or getattr(cell, "last_activity_at", 0)
                or getattr(cell, "last_heartbeat_at", 0),
            "removed_at": base.get("removed_at"),
            "worktree_branch": cell.worktree_branch
                or base.get("worktree_branch", ""),
            "total_tokens_in": int(base.get("total_tokens_in") or 0),
            "total_tokens_out": int(base.get("total_tokens_out") or 0),
            "total_tasks": max(int(base.get("total_tasks") or 0), live_count),
            "status": _live_history_status(cell, base),
            "kind": str(getattr(cell, "kind", "") or "").strip(),
        }

    def _sort_history_records(records: list[dict]) -> list[dict]:
        return sorted(
            records,
            key=lambda r: (
                0 if r.get("status") == "active" else 1,
                -(float(r.get("created_at") or 0)),
            ),
        )

    def _history_records_with_live_agents(records: list[dict]) -> list[dict]:
        """Merge live agents so stale persisted status can't hide them."""
        by_id = {str(r.get("id", "") or ""): _enrich_history_record(r)
                 for r in records}
        for cell in state.agents.values():
            if getattr(cell, "cell_type", "") != "agent":
                continue
            base = by_id.get(cell.id) or db.load_agent_history_detail(cell.id)
            by_id[cell.id] = _live_history_record(cell, base)
        return list(by_id.values())

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
        for agent in state.iter_active_agents():
            if _worktree_entry_matches_agent(repo_root, path, agent):
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
            is_torque_branch = branch.startswith("torque/")
            owner = _worktree_owner_for_entry(repo_root, path)
            if not is_torque_branch and not owner:
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
        submodules = _worktree_submodules_for_cell(cell)
        current_submodules = []
        if submodules and hasattr(worktree_mgr, "nested_submodule_head_states"):
            try:
                current_submodules = await worktree_mgr.nested_submodule_head_states(
                    cell,
                    submodules,
                )
            except Exception:
                log.exception(
                    "Failed to verify nested submodule boundary for '%s'",
                    cell.name,
                )
                current_submodules = []
        if current_submodules:
            summary["submodules"] = current_submodules
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
            recorded_submodules = boundary_submodule_branches(boundary)
            reconcile_check = getattr(
                worktree_mgr,
                "gitlink_reconciliation_boundary_state",
                None,
            )
            if (
                submodules
                and recorded_submodules
                and callable(reconcile_check)
            ):
                try:
                    reconciliation = await reconcile_check(
                        cell,
                        boundary_commit_sha=commit_sha,
                        head_sha=head_sha,
                        recorded_submodules=recorded_submodules,
                        current_submodules=current_submodules,
                        worktree_submodules=submodules,
                    )
                except Exception:
                    log.exception(
                        "Failed to verify gitlink reconciliation boundary "
                        "for '%s'",
                        cell.name,
                    )
                    reconciliation = {}
                if reconciliation.get("ok"):
                    summary["clean_mergeable"] = True
                    summary["gitlink_reconciliation"] = reconciliation
                    return {"latest": summary, "clean": summary, "reason": ""}
            summary["reason"] = "branch_tip_moved"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        recorded_submodules = boundary_submodule_branches(boundary)
        if recorded_submodules:
            current_by_path = {
                item.get("path", ""): item for item in current_submodules
            }
            for recorded in recorded_submodules:
                current = current_by_path.get(recorded.get("path", ""))
                if not current:
                    summary["reason"] = "missing_submodule_head_sha"
                    summary["submodule_mismatch"] = recorded
                    return {
                        "latest": summary,
                        "clean": None,
                        "reason": summary["reason"],
                    }
                if current.get("commit_sha", "") != recorded.get("commit_sha", ""):
                    summary["reason"] = "submodule_branch_tip_moved"
                    summary["submodule_mismatch"] = {
                        "recorded": recorded,
                        "current": current,
                    }
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
        if reason == "submodule_branch_tip_moved":
            return (
                "Latest task boundary no longer matches the nested submodule "
                "branch tip. A newer submodule commit or external rewrite "
                "moved the branch pair."
            )
        if reason == "missing_submodule_head_sha":
            return (
                "Cannot verify the nested submodule branch tip for the latest "
                "task boundary."
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
        subject = f"torque: task boundary — {task.task[:72]}"
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
            boundary_sha = await _checkpoint_worktree_with_submodules(
                cell,
                _task_boundary_checkpoint_message(task, cell, message),
            ) or ""
            kind = "checkpoint"
            if not boundary_sha:
                reason = "checkpoint_failed"
        else:
            boundary_sha = await worktree_mgr.current_head(cell) or ""
            if not boundary_sha:
                reason = "missing_head_sha"

        recorded_at = datetime.now(timezone.utc).isoformat()
        submodule_states = []
        submodules = _worktree_submodules_for_cell(cell)
        if submodules and hasattr(worktree_mgr, "nested_submodule_head_states"):
            try:
                submodule_states = await worktree_mgr.nested_submodule_head_states(
                    cell,
                    submodules,
                )
            except Exception:
                log.exception(
                    "Failed to record nested submodule boundary for '%s'",
                    cell.name,
                )

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
        if submodule_states:
            task.worktree_boundary["submodules"] = submodule_states
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
        """Build the torque-ai instruction block appended to dispatch prompts.

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

        is_impl = bool(
            task.action_name
            and amgr.is_implementation_depth(task.action_name, base_dir)
        )
        commit_hint = compute_commit_hint(
            has_worktree_branch=bool(cell and cell.worktree_branch),
            is_implementation=is_impl,
            auto_checkpoint=bool(cell and cell.worktree_auto_checkpoint),
            checkpoint_on_progress=bool(
                cell and cell.checkpoint_on_progress),
        )

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
            deliverable_required=bool(
                getattr(task, "deliverable_required", False)),
            deliverable_type=str(
                getattr(task, "deliverable_type", "") or ""),
            deliverable_format=str(
                getattr(task, "deliverable_format", "") or ""),
            deliverable_artifact_title=str(
                getattr(task, "deliverable_artifact_title", "") or ""),
            task_title=str(getattr(task, "task", "") or ""),
            requires_review=bool(
                getattr(task, "requires_review", False)),
            pre_approved_by=str(
                getattr(task, "pre_approved_by", "") or ""),
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
        critical_command_name = _critical_command_name(data)
        critical_idempotency_key = str(
            (data or {}).get("idempotency_key", "") or ""
        ).strip()
        critical_request_hash = ""
        critical_failed_write_key = ""
        critical_capture_active = False
        if db and critical_command_name and critical_idempotency_key:
            await state.flush_db_writes()
            cached_result, critical_idempotency_key, critical_request_hash = (
                _load_internal_command_receipt(db, data)
            )
            if cached_result is not _NO_COMMAND_RECEIPT:
                return cached_result
            critical_failed_write_key = _internal_failed_write_key(
                critical_idempotency_key
            )
            db.enqueue_failed_write(
                idempotency_key=critical_failed_write_key,
                endpoint="/internal/cmd",
                method="POST",
                surface="internal",
                tool_name=critical_command_name,
                caller_id=_critical_command_caller_id(data),
                payload=dict(data or {}),
                attempts=0,
                last_error="pending",
            )
            if _critical_command_needs_capture(data):
                state.begin_critical_write_capture(
                    command_name=critical_command_name,
                    idempotency_key=critical_idempotency_key,
                    request_hash=critical_request_hash,
                )
                critical_capture_active = True

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
                "architect_settings": asdict(
                    state.get_architect_settings(group)
                ),
                "resolved_agent_defaults": resolved_defaults,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(current_path
                                                          or await _resolve_base_dir(group)),
                "playbooks": state.list_playbooks(group=group,
                                                   status="published",
                                                   limit=200),
                "runtime": _runtime_payload(bridge=bridge, state=state),
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
                "engineer_settings": asdict(state.get_engineer_settings(group)),
                "architect_settings": asdict(state.get_architect_settings(group)),
                "resolved_agent_defaults": template_mgr.resolve_agent_config(
                    "", gs, {}, base_dir=base_dir),
                "profiles": pnames,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(base_dir),
                "actions": action_mgr.list_actions(base_dir),
                "playbooks": state.list_playbooks(group=group,
                                                   status="published",
                                                   limit=200),
                "runtime": _runtime_payload(bridge=bridge, state=state),
            }

        if cmd == "get_architect_settings":
            group = data.get("group", "")
            return {
                "type": "architect_settings",
                "group": group,
                "settings": asdict(state.get_architect_settings(group)),
            }

        if cmd == "preview_system_prompt":
            kind = str(data.get("kind", "") or "").strip().lower()
            if kind not in {"engineer", "architect"}:
                return {
                    "type": "error",
                    "message": "kind must be 'engineer' or 'architect'",
                }
            group = str(data.get("group", "") or "").strip()
            settings_payload = dict(data.get("settings", {}) or {})
            group_settings_payload = dict(
                data.get("group_settings", {}) or {}
            )
            group_settings = _preview_group_settings_for_prompt(
                state, group, group_settings_payload)
            if kind == "engineer":
                role_settings = _preview_engineer_settings_for_prompt(
                    state, group, settings_payload)
            else:
                role_settings = _preview_architect_settings_for_prompt(
                    state, group, settings_payload)
            base_dir = await _resolve_base_dir(group)
            resolved = template_mgr.resolve_agent_config(
                "",
                group_settings,
                _agent_overrides_from_role_settings(kind, role_settings),
                base_dir=base_dir,
            )
            specializations_preamble = ""
            specialization_names = []
            if kind == "engineer":
                raw_specializations = getattr(
                    group_settings, "default_engineer_specializations", []
                )
                if isinstance(raw_specializations, list):
                    specialization_names = [
                        str(item or "").strip()
                        for item in raw_specializations
                        if str(item or "").strip()
                    ]
                if specialization_names:
                    try:
                        specializations_preamble = (
                            specialization_mgr.render_engineer_preamble(
                                specialization_names,
                                base_dir=base_dir,
                            )
                        )
                    except Exception:
                        log.exception(
                            "failed to render system prompt preview "
                            "specializations for group=%s", group)
            prompt = _build_group_system_prompt_preview(
                state,
                group,
                kind,
                settings_payload=settings_payload,
                group_settings_payload=group_settings_payload,
                action_system_prompt=resolved.get("system_prompt", ""),
                specializations_preamble=specializations_preamble,
            )
            return {
                "type": "system_prompt_preview",
                "request_id": str(data.get("request_id", "") or ""),
                "kind": kind,
                "group": group,
                "prompt": prompt,
                "metadata": {
                    "provider": resolved.get("provider", ""),
                    "template": resolved.get("template", ""),
                    "specializations": specialization_names,
                },
            }

        # get_global_settings: respond directly
        if cmd == "get_global_settings":
            return {
                "type": "global_settings",
                "settings": asdict(state.global_settings),
                "keybinding_defaults": {},
                # Resolved relay config + per-field provenance (settings / env /
                # ee_connector.json) for the Settings "Relay" section.
                "relay_config": cloud_hooks.resolve_relay_config(
                    state.global_settings, data_dir=str(DATA_DIR)
                ),
            }

        # test_relay_connection: daemon-side connectivity probe for the Settings
        # "Relay" section "Test connection" button. Bounded + defensive; rides the
        # connector's certifi context so a CA-missing failure is distinguished
        # from "unreachable". Returns a structured {status, message, detail}.
        if cmd == "test_relay_connection":
            result = await cloud_hooks.probe_relay_connection(
                state.global_settings, data_dir=str(DATA_DIR)
            )
            return {"type": "relay_test_result", **result}

        # generate_relay_device_link: b2 daemon-mediated mint of a single-use
        # relay device link (replaces the manual `wrangler d1` OTC seed). The mint
        # rides the daemon's authenticated relay WS; the relay derives the owner
        # from the authed attach and enforces replay/fencing/rate-limit.
        #
        # LOCAL-CONFIRMATION gesture is REQUIRED (security invariant): the caller
        # MUST pass confirm=true, so a remote reach to this command cannot
        # silently mint a bearer credential. On confirm, the raw code + establish
        # URL are returned to the LOCAL caller exactly ONCE and are NEVER
        # persisted or logged by the daemon. Frontend (QR/display-once) is owned
        # by a separate task and is intentionally out of scope here.
        if cmd == "generate_relay_device_link":
            if not bool(data.get("confirm")):
                return {
                    "type": "relay_device_link",
                    "ok": False,
                    "status": "confirmation_required",
                    "message": (
                        "Generating a device link mints a single-use, "
                        "short-lived credential. Confirm to proceed."
                    ),
                }
            result = await cloud_hooks.mint_relay_device_link(
                cloud_connector_runtime_holder[0],
                label=str(data.get("label", "") or ""),
            )
            return {"type": "relay_device_link", **result}

        # generate_daemon_credential: client half of the :676 in-app daemon
        # credential provisioning flow. The daemon generates the ES256 keypair
        # locally, posts the PUBLIC JWK plus the pasted one-time pairing token to
        # /v1/pair, and persists ONLY the resulting credential_id + private key
        # FILE PATH into Global Settings. The raw private key is never stored in
        # SQLite and never returned to the browser.
        if cmd == "generate_daemon_credential":
            result = await cloud_hooks.generate_daemon_credential(
                state.global_settings,
                pairing_token=str(data.get("pairing_token", "") or ""),
                data_dir=str(DATA_DIR),
            )
            if result.get("ok"):
                credential_id = str(result.get("credential_id", "") or "").strip()
                private_key_path = str(result.get("private_key_path", "") or "").strip()
                if credential_id and private_key_path:
                    old_relay = _relay_settings_fingerprint()
                    try:
                        await state.update_global_settings_durable(
                            relay_credential_id=credential_id,
                            relay_private_key_path=private_key_path,
                        )
                    except Exception:
                        log.exception(
                            "Relay accepted daemon credential but Torque could not save "
                            "it to Settings; credential_id=%s private_key_path=%s",
                            credential_id,
                            private_key_path,
                        )
                        result = {
                            "ok": False,
                            "error": "settings_write_failed",
                            "recoverable": True,
                            "credential_id": credential_id,
                            "private_key_path": private_key_path,
                            "message": (
                                "Relay accepted the credential but Torque couldn't save "
                                "it to Settings. Recover by setting Settings → Relay "
                                "credential ID and private key path to the values shown, "
                                "or ask the relay admin to revoke the credential, then retry."
                            ),
                        }
                        response = {"type": "daemon_credential", **result}
                        try:
                            response["relay_config"] = cloud_hooks.resolve_relay_config(
                                state.global_settings, data_dir=str(DATA_DIR)
                            )
                        except Exception:
                            log.exception(
                                "Failed to resolve relay config after daemon credential generation"
                            )
                        return response
                    if _relay_settings_fingerprint() != old_relay:
                        try:
                            await _restart_cloud_connector()
                        except Exception:
                            log.exception(
                                "Cloud connector apply-on-daemon-credential failed"
                            )
                else:
                    result = {
                        "ok": False,
                        "error": "invalid_response",
                        "message": (
                            "Relay pairing did not return a credential_id and "
                            "private key path to store."
                        ),
                    }
            response = {"type": "daemon_credential", **result}
            try:
                response["relay_config"] = cloud_hooks.resolve_relay_config(
                    state.global_settings, data_dir=str(DATA_DIR)
                )
            except Exception:
                log.exception("Failed to resolve relay config after daemon credential generation")
            return response

        if cmd == "doctor":
            return _handle_doctor_command(db)

        if cmd == "get_system_health_metrics":
            try:
                await state.flush_db_writes()
                if panel_log and hasattr(panel_log, "flush"):
                    await panel_log.flush()
                return state.system_health_metrics(
                    window=data.get("window", "24h"),
                    group=data.get("group", ""),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}

        if cmd == "get_deploy_state":
            group = str(data.get("group", "") or "")
            payload = architect_deploy_state_payload(state, group)
            payload["type"] = "deploy_state"
            payload["group"] = group
            payload.setdefault("error", "")
            pending = payload.get("pending_deploy")
            if not isinstance(pending, dict):
                payload["pending_deploy"] = {"count": 0, "torque_task_ids": []}
            else:
                pending.setdefault("count", 0)
                pending.setdefault("torque_task_ids", [])
            if "daemon_uptime_seconds" not in payload:
                payload["daemon_uptime_seconds"] = None
            return payload

        if cmd == "supervisor_sessions_list":
            return await build_supervisor_sessions_payload(
                bridge, state, _runtime_payload)

        if cmd == "supervisor_session_terminate":
            return await build_supervisor_terminate_payload(
                bridge, state, _runtime_payload,
                str(data.get("session_id") or ""),
            )

        # get_events: paginated event log query
        if cmd == "get_events":
            before_id = int(data.get("before_id", 0))
            limit = min(int(data.get("limit", 50)), 200)
            events = panel_log.get_page(limit=limit, before_id=before_id)
            return {"type": "events_page", "events": events}

        if cmd == "task_detail":
            return _handle_task_detail_command(data, state)

        if cmd == "get_agent_message_history":
            return _handle_agent_message_history_command(data, state)

        if cmd == "decisions_snapshot":
            return _handle_decisions_snapshot_command(data, state)

        if cmd == "pending_hires_snapshot":
            return _handle_pending_hires_snapshot_command(data, state)

        if cmd == "archived_tasks":
            return _handle_archived_tasks_command(data, state)

        if cmd == "engineer_journal_snapshot":
            return await _handle_engineer_journal_snapshot_command(data, state)

        if cmd == "architect_journal_read":
            return _handle_architect_journal_read_command(data, state)

        if cmd == "mcp_calls":
            return await _handle_mcp_calls_command(
                data,
                state,
                event_ingest_client,
                scope="trusted",
            )

        if cmd == "architect_mcp_calls":
            return await _handle_mcp_calls_command(
                data,
                state,
                event_ingest_client,
                scope="architect",
            )

        if cmd == "engineer_mcp_calls":
            return await _handle_mcp_calls_command(
                data,
                state,
                event_ingest_client,
                scope="engineer",
            )

        if cmd == "architect_task_list":
            return await _dispatch_architect_ui_tool(cmd, data, state)

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
                status_filter=status_filter,
                limit=min(max(limit + offset, limit), 200),
                offset=0)
            records = _history_records_with_live_agents(records)
            if status_filter:
                records = [
                    r for r in records if r.get("status") == status_filter
                ]
            records = _sort_history_records(records)[offset:offset + limit]
            return {"type": "agent_history_list",
                    "records": records}

        if cmd == "get_agent_history_detail":
            agent_id = data.get("agent_id", "")
            if not agent_id:
                return {"type": "error",
                        "message": "agent_id required"}
            record = db.load_agent_history_detail(agent_id)
            live_cell = state.agents.get(agent_id)
            if live_cell and live_cell.cell_type == "agent":
                record = _live_history_record(live_cell, record)
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

        if cmd == "list_specializations":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "") or ""
            items = specialization_mgr.list_specializations(
                base_dir=base_dir, scope=scope)
            return {
                "type": "specializations",
                "group": data.get("group", ""),
                "specializations": items,
            }

        if cmd == "get_specialization":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "") or ""
            name = str(data.get("name", "") or "").strip()
            if not name:
                return {"type": "error",
                        "message": "Specialization name required"}
            spec = specialization_mgr.get_specialization(
                name, base_dir=base_dir, scope=scope)
            if not spec:
                return {
                    "type": "error",
                    "message": f"Specialization \"{name}\" not found",
                }
            return {
                "type": "specialization_detail",
                "name": name,
                "specialization": spec,
            }

        if cmd == "save_specialization":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "project") or "project"
            name = str(data.get("name", "") or "").strip()
            if not name:
                return {"type": "error",
                        "message": "Specialization name required"}
            payload = data.get("data")
            if payload is None:
                payload = data.get("specialization", {})
            old_name = str(data.get("old_name", "") or "").strip()
            old_scope = str(data.get("old_scope", "") or "").strip()
            if old_name and (old_name != name or (
                    old_scope and old_scope != scope)):
                if old_scope:
                    specialization_mgr.delete_specialization(
                        old_name, scope=old_scope, base_dir=base_dir)
                else:
                    specialization_mgr.delete_specialization(
                        old_name, base_dir=base_dir)
                    specialization_mgr.delete_specialization(
                        old_name, scope="user", base_dir=base_dir)
            try:
                specialization_mgr.save_specialization(
                    name, payload or {}, scope=scope, base_dir=base_dir)
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}
            return {
                "type": "specializations",
                "group": data.get("group", ""),
                "specializations": specialization_mgr.list_specializations(
                    base_dir=base_dir),
                "saved": name,
            }

        if cmd == "delete_specialization":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "") or ""
            name = str(data.get("name", "") or "").strip()
            if not name:
                return {"type": "error",
                        "message": "Specialization name required"}
            deleted = specialization_mgr.delete_specialization(
                name, scope=scope, base_dir=base_dir)
            if not deleted:
                return {
                    "type": "error",
                    "message": f"Specialization \"{name}\" not found",
                }
            return {
                "type": "specializations",
                "group": data.get("group", ""),
                "specializations": specialization_mgr.list_specializations(
                    base_dir=base_dir),
                "deleted": name,
            }

        if cmd == "set_engineer_specializations":
            engineer_ident = str(data.get("engineer_id", "") or "").strip()
            if not engineer_ident:
                return {
                    "type": "error",
                    "message": "engineer_id is required",
                }
            agent_id = _resolve_agent_id(state, engineer_ident)
            cell = state.agents.get(agent_id) if agent_id else None
            if not cell or cell.kind != "engineer":
                return {
                    "type": "error",
                    "message": f"Engineer \"{engineer_ident}\" not found",
                }
            raw = data.get("specializations", [])
            if not isinstance(raw, list):
                return {
                    "type": "error",
                    "message": "specializations must be a list",
                }
            names = []
            seen = set()
            for item in raw:
                token = str(item or "").strip()
                if not token or token in seen:
                    continue
                names.append(token)
                seen.add(token)
            cell.engineer_specializations = names
            state._emit_agent(cell)
            state._db_save_agent(cell)
            return {
                "type": "engineer_specializations",
                "engineer_id": cell.id,
                "specializations": names,
            }

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
                gdir = os.path.expanduser("~/.torque/actions")
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
            # Reject 'torque' as a variable name (reserved namespace)
            avars = action_mgr.get_action_vars(prompt)
            for av in avars:
                if av.get("name") == "torque":
                    return {"type": "error",
                            "message": "'torque' is a reserved variable "
                                       "name"}
            scope = data.get("scope", "project")  # "project" or "user"
            base_dir = await _resolve_base_dir(data.get("group", ""))

            if scope == "user":
                tdir = os.path.expanduser("~/.torque/actions")
                os.makedirs(tdir, exist_ok=True)
            else:
                tdir = action_mgr.find_actions_dir(base_dir)
                if not tdir:
                    d = base_dir or os.getcwd()
                    tdir = os.path.join(d, ".torque", "actions")
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

            elif cmd == "update_architect_settings":
                group = data.get("group", "")
                settings = dict(data.get("settings", {}) or {})
                valid = set(ArchitectSettings.__dataclass_fields__)
                for key, value in data.items():
                    if key in valid and key != "group":
                        settings[key] = value
                state.update_architect_settings(group, **settings)
                result = {
                    "type": "ok",
                    "settings": asdict(state.get_architect_settings(group)),
                }

            elif cmd == "update_global_settings":
                settings = data.get("settings", {})
                old_relay = _relay_settings_fingerprint()
                state.update_global_settings(**settings)
                # Apply-on-change: restart the cloud connector when any relay
                # field changed so the new config takes effect without a daemon
                # restart. Defensive / non-fatal.
                if _relay_settings_fingerprint() != old_relay:
                    try:
                        await _restart_cloud_connector()
                    except Exception:
                        log.exception(
                            "Cloud connector apply-on-change failed")
                # Propagate max_event_log to panel log
                new_max = state.global_settings.max_event_log
                if panel_log._max_size != new_max:
                    panel_log._max_size = new_max
                    panel_log._events = deque(
                        panel_log._events, maxlen=new_max)
                    if panel_log._db:
                        panel_log._db.trim_panel_events(new_max)
                try:
                    await _configure_event_ingest_client(event_ingest_client, state)
                    event_ingest_configured[0] = True
                except Exception:
                    event_ingest_configured[0] = False
                    log.exception("Failed to reconfigure event ingest daemon")

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
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
                    create_agent_with_config=_create_agent_with_config,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "add_architect":
                result = await _handle_add_architect_command(
                    data,
                    state,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
                    resolve_architect_launch_config=_resolve_architect_launch_config,
                    create_agent_with_config=_create_agent_with_config,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "add_worker":
                result = await _handle_add_worker_command(
                    data,
                    state,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    resolve_worker_launch_config=_resolve_worker_launch_config,
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
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
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
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
                    resolve_architect_launch_config=_resolve_architect_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_engineer=_is_designated_engineer,
                    send_agent_prompt=_send_agent_prompt,
                    panel_event=_panel_event,
                )

            elif cmd == "architect_dismiss":
                result = await _handle_architect_dismiss_command(
                    data,
                    state,
                    close_session=bridge.close_session,
                    panel_event=_panel_event,
                )

            elif cmd == "architect_rehire":
                result = await _handle_architect_rehire_command(
                    data,
                    state,
                    bridge=bridge,
                    worktree_mgr=worktree_mgr,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
                    resolve_architect_launch_config=_resolve_architect_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_engineer=_is_designated_engineer,
                    send_agent_prompt=_send_agent_prompt,
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

            elif cmd in {"restore_agent", "architect_engineer_restore"}:
                result = _handle_restore_agent_command(data, state)

            elif cmd == "purge_agent_now":
                result = await _handle_purge_agent_now_command(
                    data,
                    state,
                    cleanup_purged_agents=_cleanup_purged_agents,
                )

            elif cmd == "recently_deleted_agents":
                result = _handle_recently_deleted_agents_command(data, state)

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
                    "architect_peer_inbox",
                    "architect_peer_list",
                    "architect_peer_message",
                    "architect_task_update",
            }:
                result = await _dispatch_architect_ui_tool(
                    cmd,
                    data,
                    state,
                    handle_command=handle_command,
                )

            elif cmd == "add_agent":
                group = data["group"]
                is_engineer = data.get("is_engineer", False)

                # Enforce one engineer per group
                if is_engineer:
                    gs_check = state.get_group_settings(group)
                    if gs_check.engineer_agent_id:
                        existing = state.agents.get(
                            gs_check.engineer_agent_id)
                        ename = existing.name if existing else "unknown"
                        result = {
                            "type": "error",
                            "message": (
                                f"Group '{group}' already has a "
                                f"engineer: {ename}")}
                        # Skip agent creation — jump to broadcast
                        is_engineer = False
                        data = {}  # prevent fallthrough

                if data:
                    base_dir = await _resolve_base_dir(group)
                    explicit_template = data.get("template", "").strip()
                    _overrides = dict(data)
                    resolver = (
                        _resolve_engineer_launch_config
                        if is_engineer else _resolve_agent_launch_config
                    )
                    launch_cfg = resolver(
                        group,
                        base_dir=base_dir,
                        explicit_template=explicit_template,
                        overrides=_overrides,
                    )

                    persistent_prompt_text = ""
                    pending_specializations = (
                        _resolve_pending_engineer_specializations(
                            data, state, group, is_engineer)
                    )
                    # Engineer: build persistent prompt and skip worktree
                    if is_engineer:
                        from .engineer import build_engineer_system_prompt
                        ws = state.get_engineer_settings(group)
                        action_sp = launch_cfg.get("system_prompt", "")
                        spec_preamble = ""
                        if pending_specializations:
                            try:
                                spec_preamble = (
                                    specialization_mgr.render_engineer_preamble(
                                        pending_specializations,
                                        base_dir=base_dir,
                                    )
                                )
                            except Exception:
                                log.exception(
                                    "failed to render specializations "
                                    "for new engineer in group=%s", group)
                        persistent_prompt_text = build_engineer_system_prompt(
                            group, ws, action_sp,
                            group_settings=state.get_group_settings(group),
                            specializations_preamble=spec_preamble,
                            owner_is_user=not str(
                                data.get("hired_by_architect_id", "")
                                or "").strip())
                        launch_cfg["worktree"] = False
                    startup_prompt = _startup_prompt_for_new_agent(
                        agent_type=launch_cfg.get("agent_type", ""),
                        persistent_prompt_text=persistent_prompt_text,
                    )

                    name = (data.get("name", "") or "").strip()
                    if not name:
                        if is_engineer:
                            name = "Engineer"
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
                        # Designate as engineer
                        if is_engineer:
                            if pending_specializations:
                                cell.engineer_specializations = list(
                                    pending_specializations)
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                            state.update_group_settings(
                                group, engineer_agent_id=cell.id)
                            # Reorder now that engineer_agent_id is set
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
                                        cell=cell,
                                        default_boot_nudge=
                                        resolve_default_boot_nudge(
                                            state, cell),
                                        include_identity_anchor=
                                        _should_show_guidance_hint(
                                            state,
                                            cell,
                                            GUIDANCE_HINT_IDENTITY_LAUNCH,
                                        )):
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
                    terminal_backend="pty",
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
                result = await _handle_remove_agent_command(
                    data,
                    state,
                    close_agent_session_only=_close_agent_session_only,
                    cleanup_purged_agents=_cleanup_purged_agents,
                )

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
                if cell and state.agent_is_tombstoned(cell):
                    result = {
                        "type": "error",
                        "message": "Agent is tombstoned and cannot be focused",
                    }
                elif cell:
                    selected_id = cell.parent_id if (
                        cell.cell_type == "terminal" and cell.parent_id
                    ) else cell.id
                    if selected_id and selected_id in state.agents:
                        state.selected_agent_id = selected_id
                        state._emit(
                            "ui_update",
                            key="selected_agent_id",
                            value=state.selected_agent_id,
                        )
                        state._db_save_ui(
                            "selected_agent_id",
                            state.selected_agent_id,
                        )
                    if cell.session_id:
                        await bridge.focus_session(cell.session_id)

            elif cmd == "send_text":
                await _handle_send_text_command(data, state, _send_agent_prompt)

            elif cmd == "send_user_message":
                await _handle_send_user_message_command(data, state, bridge)

            elif cmd == "user_agent_message":
                result = await _handle_user_agent_message_command(
                    data,
                    state,
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
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
                    resolve_architect_launch_config=_resolve_architect_launch_config,
                    resolve_worker_launch_config=_resolve_worker_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_engineer=_is_designated_engineer,
                    send_agent_prompt=_send_agent_prompt,
                )

            elif cmd == "restart_agent":
                result = await _handle_restart_agent_command(
                    data,
                    state,
                    bridge=bridge,
                    worktree_mgr=worktree_mgr,
                    resolve_base_dir=_resolve_base_dir,
                    resolve_agent_launch_config=_resolve_agent_launch_config,
                    resolve_engineer_launch_config=_resolve_engineer_launch_config,
                    resolve_architect_launch_config=_resolve_architect_launch_config,
                    resolve_worker_launch_config=_resolve_worker_launch_config,
                    apply_persistent_prompt=_apply_persistent_prompt,
                    build_cell_persistent_prompt=_build_cell_persistent_prompt,
                    persistent_prompt_filename=_persistent_prompt_filename,
                    is_designated_engineer=_is_designated_engineer,
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
                    if str(getattr(cell, "kind", "") or "").strip() == "architect":
                        state.refresh_peer_message_cache_for_agent(
                            cell.id,
                            emit=False,
                        )
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
                                or ".torque/worktrees",
                            base_branch=cell.worktree_base_branch
                                or gs.worktree_base_branch or "",
                            symlinks=gs.worktree_symlinks,
                            include_gitignored_symlinks=getattr(
                                gs,
                                "worktree_symlink_gitignored_paths",
                                False,
                            ),
                            worktree_submodules=getattr(
                                gs,
                                "worktree_submodules",
                                [],
                            ),
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
                                launch_resolver = _launch_resolver_for_cell(
                                    cell,
                                    resolve_agent_launch_config=
                                    _resolve_agent_launch_config,
                                    resolve_engineer_launch_config=
                                    _resolve_engineer_launch_config,
                                    resolve_architect_launch_config=
                                    _resolve_architect_launch_config,
                                    resolve_worker_launch_config=
                                    _resolve_worker_launch_config,
                                    is_designated_engineer=
                                    _is_designated_engineer,
                                )
                                launch_cfg = launch_resolver(
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
                    remove_result = await _safe_remove_worktree_result(cell)
                    if repo_root and remove_result.get("worktree_removed"):
                        cell.directory = repo_root
                    # Relaunch if requested by the UI
                    if (
                            remove_result.get("worktree_removed")
                            and data.get("relaunch")
                            and cell.cell_type == "agent"):
                        await _relaunch_agent_after_worktree_removal(
                            cell,
                            bridge=bridge,
                            state=state,
                            resolve_base_dir=_resolve_base_dir,
                            resolve_agent_launch_config=_resolve_agent_launch_config,
                            resolve_engineer_launch_config=_resolve_engineer_launch_config,
                            resolve_architect_launch_config=_resolve_architect_launch_config,
                            resolve_worker_launch_config=_resolve_worker_launch_config,
                            is_designated_engineer=_is_designated_engineer,
                            apply_persistent_prompt=_apply_persistent_prompt,
                            build_cell_persistent_prompt=_build_cell_persistent_prompt,
                            send_agent_prompt=_send_agent_prompt,
                        )
                    else:
                        state._emit_agent(cell)
                        state._db_save_agent(cell)
                    result = {
                        "type": "worktree_remove",
                        "id": cell.id,
                        **remove_result,
                    }
                    if not remove_result.get("worktree_removed"):
                        result = {
                            "type": "error",
                            "message": (
                                remove_result.get("message")
                                or "Worktree removal failed"
                            ),
                            "id": cell.id,
                            "worktree_remove": remove_result,
                        }
                elif cell:
                    result = {
                        "type": "error",
                        "message": "Agent has no worktree",
                        "id": cell.id,
                    }

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
                        if hasattr(worktree_mgr, "remove_path_result"):
                            remove_result = await worktree_mgr.remove_path_result(
                                repo_root,
                                item.get("path", ""),
                                branch=item.get("branch", ""),
                                name=item.get("branch", "") or item.get("path", ""),
                            )
                        else:
                            ok = await worktree_mgr.remove_path(
                                repo_root,
                                item.get("path", ""),
                                branch=item.get("branch", ""),
                                name=(
                                    item.get("branch", "")
                                    or item.get("path", "")
                                ),
                            )
                            remove_result = {
                                "ok": ok,
                                "worktree_removed": ok,
                                "branch_deleted": ok,
                                "mismatches": [],
                                "message": (
                                    "Worktree removed" if ok
                                    else "remove_failed"
                                ),
                            }
                        if remove_result.get("worktree_removed"):
                            entry = {
                                "path": item.get("path", ""),
                                "branch": item.get("branch", ""),
                                "prune_reason": item.get("prune_reason", ""),
                            }
                            if not remove_result.get("ok"):
                                entry["warning"] = remove_result.get(
                                    "message",
                                    "Worktree removed but cleanup was incomplete",
                                )
                            if remove_result.get("mismatches"):
                                entry["mismatches"] = remove_result.get(
                                    "mismatches"
                                )
                            removed.append(entry)
                        else:
                            skipped.append({
                                "path": item.get("path", ""),
                                "branch": item.get("branch", ""),
                                "prune_reason": (
                                    remove_result.get("message")
                                    or "remove_failed"
                                ),
                                "mismatches": remove_result.get(
                                    "mismatches", []
                                ),
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
                    await _checkpoint_worktree_with_submodules(cell, msg)
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
                if cell and cell.worktree_path \
                        and cell.worktree_branch:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    submodules = _worktree_submodules_for_cell(cell)
                    dirty = (
                        await worktree_mgr.has_uncommitted_changes(
                            cell,
                            worktree_submodules=submodules,
                        )
                        if submodules
                        else await worktree_mgr.has_uncommitted_changes(cell)
                    )
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
                        stale_base = (
                            await worktree_mgr.stale_base_info(
                                cell,
                                worktree_submodules=submodules,
                            )
                            if submodules
                            else await worktree_mgr.stale_base_info(cell)
                        )
                        if stale_base.get("stale") \
                                and not (
                                    data.get("allow_stale_base")
                                    or _stale_base_force_enabled(data)
                                ):
                            result = _stale_base_check_merge_result(
                                aid, stale_base
                            )
                            result["boundary"] = boundary_state.get("latest")
                            result["clean_boundary"] = boundary_state.get("clean")
                            return result
                        check = (
                            await worktree_mgr.check_merge_conflicts(
                                cell,
                                worktree_submodules=submodules,
                            )
                            if submodules
                            else await worktree_mgr.check_merge_conflicts(cell)
                        )
                        nested_merge_preflight = getattr(
                            worktree_mgr,
                            "nested_submodule_merge_preflight",
                            None,
                        )
                        if (
                            submodules
                            and _is_reconcilable_nested_gitlink_conflict(
                                check,
                                submodules,
                            )
                            and callable(nested_merge_preflight)
                        ):
                            nested_preflight = (
                                await nested_merge_preflight(
                                    cell,
                                    submodules,
                                )
                            )
                            if nested_preflight.get("ok"):
                                check = {
                                    "clean": True,
                                    "tree_sha": "",
                                    "conflicts": [],
                                    "nested_submodule_reconciliation_required": True,
                                    "nested_submodules": nested_preflight,
                                    "precheck": check,
                                }
                        if check.get("clean") and check.get("tree_sha"):
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
                if cell and cell.worktree_path:
                    submodules = _worktree_submodules_for_cell(cell)
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
                    elif (
                        await worktree_mgr.has_uncommitted_changes(
                            cell,
                            worktree_submodules=submodules,
                        )
                        if submodules
                        else await worktree_mgr.has_uncommitted_changes(cell)
                    ):
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
                                stale_base_before_rebase = (
                                    await stale_info(
                                        cell,
                                        worktree_submodules=submodules,
                                    )
                                    if submodules
                                    else await stale_info(cell)
                                )
                            except Exception:
                                log.exception(
                                    "stale-base preflight failed before rebase "
                                    "for '%s'",
                                    cell.name,
                                )
                        check = (
                            await worktree_mgr.check_merge_conflicts(
                                cell,
                                worktree_submodules=submodules,
                            )
                            if submodules
                            else await worktree_mgr.check_merge_conflicts(cell)
                        )
                        previous_head_sha = (
                            await worktree_mgr.current_head(cell) or ""
                        )
                        previous_submodules = (
                            await worktree_mgr.nested_submodule_head_states(
                                cell,
                                submodules,
                            )
                            if submodules
                            and hasattr(
                                worktree_mgr,
                                "nested_submodule_head_states",
                            )
                            else []
                        )
                        ok = (
                            await worktree_mgr.rebase_onto_base(
                                cell,
                                worktree_submodules=submodules,
                            )
                            if submodules
                            else await worktree_mgr.rebase_onto_base(cell)
                        )
                        if ok:
                            rebased_head_sha = (
                                await worktree_mgr.current_head(cell) or ""
                            )
                            rebased_submodules = (
                                await worktree_mgr.nested_submodule_head_states(
                                    cell,
                                    submodules,
                                )
                                if submodules
                                and hasattr(
                                    worktree_mgr,
                                    "nested_submodule_head_states",
                                )
                                else []
                            )
                            dirty_after_rebase = (
                                await worktree_mgr.has_uncommitted_changes(
                                    cell,
                                    worktree_submodules=submodules,
                                )
                                if submodules
                                else await worktree_mgr.has_uncommitted_changes(cell)
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
                                        previous_submodules=previous_submodules,
                                        rebased_submodules=rebased_submodules,
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
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
                    submodules = _worktree_submodules_for_cell(cell)
                    stale_base = (
                        await worktree_mgr.stale_base_info(
                            cell,
                            worktree_submodules=submodules,
                        )
                        if submodules
                        else await worktree_mgr.stale_base_info(cell)
                    )
                    if summary_only:
                        scope_domain = _scope_domain_for_cell(state, cell)
                        summary = await worktree_mgr.diff_files_summary(
                            cell,
                            paths=paths,
                            scope_domain=scope_domain,
                        )
                        out_of_scope = summary.get("out_of_scope") or {}
                        if out_of_scope.get("count"):
                            log.warning(
                                "Out-of-scope diff for '%s' (%s task): %s",
                                cell.name,
                                out_of_scope.get("domain", ""),
                                out_of_scope.get("digest_line", ""),
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
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
                        submodules = _worktree_submodules_for_cell(cell)
                        conflict_info = (
                            await worktree_mgr.check_merge_conflicts(
                                cell,
                                worktree_submodules=submodules,
                            )
                            if submodules
                            else await worktree_mgr.check_merge_conflicts(cell)
                        )
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
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
                await _reconcile_worktree_branch(state, worktree_mgr, cell)
                requested_force_direct = bool(data.get("force_direct"))
                merge_mode = _engineer_merge_mode_for_cell(state, cell)
                direct_merge_breach_event = None
                direct_merge_warning = (
                    "Direct local worktree merge was forced; the default "
                    "workflow is GitHub PR squash merge."
                )
                forced_by_direct_mode_warning = (
                    "Group setting engineer_merge_mode='direct' forced a "
                    "direct local worktree merge; the PR workflow was bypassed."
                )
                if merge_mode == "pr" and requested_force_direct:
                    message = (
                        "Group setting engineer_merge_mode='pr' forbids "
                        "force_direct=true. Adjust setting or omit force_direct."
                    )
                    workflow_breach = None
                    if cell:
                        workflow_breach = _emit_workflow_breach_event(
                            state,
                            _panel_event,
                            subkind="merge_mode_locked",
                            source="operator",
                            task=_workflow_breach_active_task_for_worker(
                                state,
                                cell,
                            ),
                            worker=cell,
                            context=message,
                        )
                    result = _worktree_merge_error(
                        aid,
                        message,
                        phase="merge_mode_locked",
                        code="force_direct_disallowed",
                    )
                    result["message"] = message
                    if workflow_breach:
                        result["workflow_breach"] = workflow_breach
                else:
                    force_direct = (
                        requested_force_direct
                        or merge_mode == "direct"
                    )
                    if (
                            merge_mode == "engineer-choice"
                            and requested_force_direct
                            and cell):
                        direct_merge_breach_event = (
                            _emit_workflow_breach_event(
                                state,
                                _panel_event,
                                subkind="force_direct_merge",
                                source="operator",
                                task=_workflow_breach_active_task_for_worker(
                                    state,
                                    cell,
                                ),
                                worker=cell,
                                context=direct_merge_warning,
                            )
                        )
                    elif (
                            merge_mode == "direct"
                            and not requested_force_direct
                            and cell):
                        direct_merge_breach_event = (
                            _emit_workflow_breach_event(
                                state,
                                _panel_event,
                                subkind="merge_mode_locked",
                                source="operator",
                                task=_workflow_breach_active_task_for_worker(
                                    state,
                                    cell,
                                ),
                                worker=cell,
                                context=forced_by_direct_mode_warning,
                            )
                        )

                    if force_direct:
                        result = await _run_direct_worktree_merge(
                            state=state,
                            cell=cell,
                            aid=aid,
                            data=data,
                            worktree_mgr=worktree_mgr,
                            latest_boundary_state_for_cell=(
                                _latest_boundary_state_for_cell
                            ),
                            boundary_reason_message=_boundary_reason_message,
                            mark_branch_boundaries_merged=(
                                _mark_branch_boundaries_merged
                            ),
                            cleanup_after_merge=_cleanup_after_merge,
                            broadcast_toast=_broadcast_toast,
                            bridge=bridge,
                            handle_command=handle_command,
                            panel_event=_panel_event,
                            board_sync_manager=board_sync_manager,
                        )
                        if isinstance(result, dict):
                            result["force_direct"] = True
                            if merge_mode == "direct":
                                result["engineer_merge_mode"] = "direct"
                                if not requested_force_direct:
                                    result["warning"] = (
                                        forced_by_direct_mode_warning
                                    )
                            else:
                                result["warning"] = direct_merge_warning
                            if direct_merge_breach_event:
                                result["workflow_breach"] = (
                                    direct_merge_breach_event
                                )
                    else:
                        result = await _run_pr_worktree_merge(
                            state=state,
                            cell=cell,
                            aid=aid,
                            data=data,
                            worktree_mgr=worktree_mgr,
                            latest_boundary_state_for_cell=(
                                _latest_boundary_state_for_cell
                            ),
                            boundary_reason_message=_boundary_reason_message,
                            mark_branch_boundaries_merged=(
                                _mark_branch_boundaries_merged
                            ),
                            cleanup_after_merge=_cleanup_after_merge,
                            broadcast_toast=_broadcast_toast,
                            bridge=bridge,
                            handle_command=handle_command,
                            panel_event=_panel_event,
                            board_sync_manager=board_sync_manager,
                        )

            # -- Board sync commands --
            elif cmd == "board_sync_preflight":
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    result = await board_sync_manager.preflight(
                        data.get("group", ""),
                        provider_name=data.get("provider", ""),
                        settings_overrides=(
                            data.get("settings")
                            or data.get("group_settings")
                            or {}
                        ),
                    )

            elif cmd == "board_sync_list_projects":
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    result = await board_sync_manager.list_projects(
                        data.get("group", ""),
                        owner=data.get("owner", ""),
                        provider_name=data.get("provider", ""),
                        settings_overrides=(
                            data.get("settings")
                            or data.get("group_settings")
                            or {}
                        ),
                    )

            elif cmd == "board_sync_task":
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    sync_result = board_sync_manager.enqueue_task(
                        data.get("task", data.get("id", "")),
                        reason="explicit",
                        explicit=True,
                        force=True,
                    )
                    result = {
                        "type": "board_sync_task",
                        **sync_result,
                    }

            elif cmd == "board_sync_group":
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    result = board_sync_manager.enqueue_group(
                        data.get("group", ""),
                        explicit=True,
                        force=bool(data.get("force", False)),
                        reason="group_sync",
                    )

            elif cmd == "board_pull_preview":
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    result = await board_sync_manager.pull_preview(
                        data.get("task", data.get("id", "")))

            elif cmd == "board_pull_apply":
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    result = await board_sync_manager.pull_apply(
                        data.get("task", data.get("id", "")),
                        data.get("fields", []),
                    )

            elif cmd in ("board_import_preview", "board_pull_import_preview"):
                if not board_sync_manager:
                    result = {"type": "error", "message": "Board sync manager unavailable"}
                else:
                    result = await board_sync_manager.import_preview(
                        data.get("group", ""))

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
                    suggested_specialization=data.get(
                        "suggested_specialization", ""),
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
                assigned_cell = state.agents.get(
                    str(add_kwargs.get("assigned_engineer_id", "") or "").strip()
                )
                if assigned_cell and state.agent_is_tombstoned(assigned_cell):
                    result = _engineer_tombstoned_error(assigned_cell.id)
                else:
                    # Resolve deliverable contract from action + explicit kwarg
                    deliverable_explicit = data.get("deliverable")
                    if (action_name or isinstance(deliverable_explicit, dict)
                            and deliverable_explicit):
                        deliverable_base_dir = await _resolve_base_dir(group)
                        deliverable_contract = _resolve_deliverable_for_create(
                            action_name,
                            deliverable_base_dir,
                            deliverable_explicit
                            if isinstance(deliverable_explicit, dict) else None,
                        )
                        add_kwargs["deliverable_required"] = bool(
                            deliverable_contract["required"])
                        add_kwargs["deliverable_type"] = deliverable_contract["type"]
                        add_kwargs["deliverable_format"] = (
                            deliverable_contract["format"])
                        add_kwargs["deliverable_artifact_title"] = (
                            deliverable_contract["artifact_title"])
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
                        if board_sync_manager:
                            board_sync_manager.enqueue_task(
                                bt.id,
                                reason="task_create",
                            )

            elif cmd == "board_archive_task":
                result = _handle_board_archive_command(state, data)
                if not (isinstance(result, dict)
                        and result.get("type") == "error"):
                    if board_sync_manager:
                        board_sync_manager.enqueue_task(
                            data.get("id", ""),
                            reason="task_archive",
                        )

            elif cmd == "board_archive_tasks":
                result = _handle_board_archive_tasks_command(state, data)
                if not (isinstance(result, dict)
                        and result.get("type") == "error"):
                    for _sync_tid in data.get("ids", data.get("task_ids", [])):
                        if board_sync_manager:
                            board_sync_manager.enqueue_task(
                                _sync_tid,
                                reason="task_archive",
                            )

            elif cmd == "board_unarchive_task":
                result = _handle_board_unarchive_command(state, data)
                if not (isinstance(result, dict)
                        and result.get("type") == "error"):
                    if board_sync_manager:
                        board_sync_manager.enqueue_task(
                            data.get("id", ""),
                            reason="task_unarchive",
                        )

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
                assigned_update = str(
                    fields.get("assigned_engineer_id", "") or ""
                ).strip()
                assigned_cell = (
                    state.agents.get(assigned_update)
                    if "assigned_engineer_id" in fields and assigned_update
                    else None
                )
                agent_update = str(fields.get("agent_id", "") or "").strip()
                agent_cell = (
                    state.agents.get(agent_update)
                    if "agent_id" in fields and agent_update else None
                )
                if assigned_cell and state.agent_is_tombstoned(assigned_cell):
                    result = _engineer_tombstoned_error(assigned_cell.id)
                elif agent_cell and state.agent_is_tombstoned(agent_cell):
                    result = {
                        "type": "error",
                        "message": "Agent is tombstoned",
                    }
                else:
                    state.board_update_task(tid, **fields)
                    if board_sync_manager:
                        board_sync_manager.enqueue_for_local_change(
                            tid,
                            reason="task_update",
                            fields=fields.keys(),
                        )
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
                        data.get("actor_name", "") or "torque"
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
                        if board_sync_manager:
                            board_sync_manager.enqueue_task(
                                bt.id,
                                reason="external_import",
                            )
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
                    update_fields = {
                        "provider": link["provider"],
                        "external_id": link["external_id"],
                        "external_url": link["external_url"],
                    }
                    board_sync = data.get("board_sync", None)
                    if isinstance(board_sync, dict):
                        update_fields["board_sync"] = board_sync
                    elif (
                            not link["provider"]
                            and not link["external_id"]
                            and not link["external_url"]
                    ):
                        update_fields["board_sync"] = {
                            "version": 1,
                            "enabled": False,
                        }
                    state.board_update_task(tid, **update_fields)
                    if board_sync_manager:
                        board_sync_manager.enqueue_task(
                            tid,
                            reason="external_link",
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
                            "agent_name": "torque",
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
                            "agent_name": "torque",
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
                if not (isinstance(result, dict)
                        and result.get("type") == "error"):
                    if board_sync_manager:
                        board_sync_manager.enqueue_task(
                            data.get("id", ""),
                            reason="task_archive",
                        )

            elif cmd == "board_archive_tasks":
                result = _handle_board_archive_tasks_command(state, data)
                if not (isinstance(result, dict)
                        and result.get("type") == "error"):
                    for _sync_tid in data.get("ids", data.get("task_ids", [])):
                        if board_sync_manager:
                            board_sync_manager.enqueue_task(
                                _sync_tid,
                                reason="task_archive",
                            )

            elif cmd == "board_unarchive_task":
                result = _handle_board_unarchive_command(state, data)
                if not (isinstance(result, dict)
                        and result.get("type") == "error"):
                    if board_sync_manager:
                        board_sync_manager.enqueue_task(
                            data.get("id", ""),
                            reason="task_unarchive",
                        )

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
                            "engineer"
                            if actor and actor.id == state.get_group_settings(
                                task.group
                            ).engineer_agent_id
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
                if not _mv_task:
                    result = {"type": "error", "message": "Task not found"}
                else:
                    _mv_resume_targets = _capture_auto_resume_targets(
                        state,
                        task=_mv_task,
                        group=_mv_task.group if _mv_task else "",
                    )
                    _mv_done_before = task_counts_as_done(_mv_task)
                    _mv_previous_lane = str(getattr(_mv_task, "lane", "") or "")
                    _mv_new = data.get("lane", "")
                    if not _mv_new:
                        result = {"type": "error", "message": "lane is required"}
                    elif _mv_new not in state.board_lanes:
                        result = {
                            "type": "error",
                            "message": f"Unknown lane: {_mv_new}",
                        }
                    else:
                        _mv_clear_status = data.get("clear_status", False)
                        if not isinstance(_mv_clear_status, bool):
                            _mv_clear_status = False
                        state.board_move_task(
                            _mv_id,
                            _mv_new,
                            data.get("position"),
                            clear_status=_mv_clear_status,
                        )
                        if board_sync_manager:
                            board_sync_manager.enqueue_task(
                                _mv_id,
                                reason="task_move",
                            )
                        _mv_task_after = state.board_tasks.get(_mv_id)
                        result = {
                            "type": "task_moved",
                            "task_id": _mv_id,
                            "previous_lane": _mv_previous_lane,
                            "new_lane": (
                                str(getattr(_mv_task_after, "lane", "") or "")
                                if _mv_task_after else _mv_new
                            ),
                            "status": (
                                str(getattr(_mv_task_after, "status", "") or "")
                                if _mv_task_after else ""
                            ),
                        }
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
                        task = _promote_suggested_action(state, task)
                        act_meta = action_mgr.load_action(
                            task.action_name, base_dir) \
                            if task.action_name else None
                        # Late-bind deliverable contract from the action if
                        # the task didn't already carry one (e.g. action_name
                        # was set after creation, or task pre-dates the
                        # deliverable feature).
                        if (task.action_name and not task.deliverable_required
                                and not task.deliverable_type):
                            try:
                                _act_deliv = action_mgr.get_deliverable(
                                    task.action_name, base_dir)
                            except Exception:
                                _act_deliv = None
                            if _act_deliv and _act_deliv.get("required"):
                                state.board_update_task(
                                    tid,
                                    deliverable_required=bool(
                                        _act_deliv["required"]),
                                    deliverable_type=_act_deliv["type"],
                                    deliverable_format=_act_deliv["format"],
                                    deliverable_artifact_title=
                                    _act_deliv["artifact_title"],
                                )
                                task = state.board_tasks.get(tid) or task
                        # Mandatory-review contract (TORQUE:256). When an
                        # action declares a ``required: true`` transition,
                        # stamp ``requires_review`` on the task so
                        # ``torque_done`` / ``torque_ready`` refuse until the
                        # transition is taken or a reviewer-issued
                        # ``pre_approved_by`` is set on the derived task.
                        if task.action_name and not task.requires_review:
                            try:
                                _has_required = action_mgr.has_required_transition(
                                    task.action_name, base_dir)
                            except Exception:
                                _has_required = False
                            if _has_required:
                                state.board_update_task(
                                    tid,
                                    requires_review=True,
                                )
                                task = state.board_tasks.get(tid) or task
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
                            or data.get("_created_by_engineer_id", "")
                            or ""
                        ).strip()
                        if agent_id:
                            target_cell = state.agents.get(agent_id)
                            if target_cell and state.agent_is_tombstoned(target_cell):
                                result = {
                                    "type": "error",
                                    "message": "Agent is tombstoned",
                                }
                            elif agent_id and not target_cell:
                                result = {"type": "error",
                                          "message": "Agent not found"}
                            elif (
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
                                and state.agent_is_tombstoned(owner_cell)
                            ):
                                result = _engineer_tombstoned_error(owner_cell.id)
                            elif (
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
                            if assigned_engineer and state.agent_is_tombstoned(
                                    assigned_engineer):
                                result = _engineer_tombstoned_error(
                                    assigned_engineer_id)
                            elif _agent_dismissed_at(assigned_engineer):
                                result = _engineer_dismissed_error(
                                    assigned_engineer_id)
                        if result:
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
                                            state.get_engineer_settings(
                                                task.group
                                            ).default_worker_concurrency
                                        )
                                    )
                                    state.auto_dispatch_queue_add(
                                        task.group,
                                        tid,
                                        target_agent_id=cell.id,
                                        max_concurrent=queue_cap,
                                        engineer_owner_id=str(
                                            data.get(
                                                "_engineer_dispatch_id", ""
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
                            from torque.state import _slugify
                            agent_name = data.get("name", "")
                            if not agent_name:
                                slug = _slugify(task.task)
                                agent_name = slug or "agent"
                            launch_overrides = {}
                            agent_type = _worker_provider_override_from_dispatch(
                                data
                            )
                            agent_type = _sanitize_engineer_worker_provider_override(
                                state,
                                group,
                                data,
                                agent_type,
                            )
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
                            launch_cfg = _resolve_worker_launch_config(
                                group,
                                base_dir=base_dir,
                                explicit_template=explicit_template,
                                overrides=launch_overrides,
                            )
                            inherit_from = data.get(
                                "inherit_worktree_from", "")
                            inherited_worktree_source = (
                                _resolve_inherited_worktree_source(
                                    state,
                                    task,
                                    inherit_from,
                                )
                            )
                            persistent_prompt_text = ""
                            startup_prompt = ""
                            if launch_cfg.get("agent_type"):
                                persistent_prompt_text = \
                                    _build_dispatch_persistent_prompt(
                                        launch_cfg.get("system_prompt", ""),
                                        owner_is_user=_owner_is_user_from_ids(
                                            created_by_engineer_id=data.get(
                                                "_created_by_engineer_id", ""),
                                            owner_engineer_id=data.get(
                                                "owner_engineer_id", ""),
                                            hired_by_architect_id=data.get(
                                                "hired_by_architect_id", ""),
                                        ))
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
                                created_by_engineer_id=data.get(
                                    "_created_by_engineer_id", ""),
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
                                final_prompt = _build_self_dispatch_prompt(
                                    shared_context_block,
                                )
                                if _should_show_guidance_hint(
                                        state,
                                        cell,
                                        GUIDANCE_HINT_IDENTITY_DISPATCH):
                                    final_prompt = prepend_agent_identity_anchor(
                                        final_prompt,
                                        cell,
                                    )
                            else:
                                # Build torque context for template rendering
                                torque_ctx = _build_torque_context(
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
                                        torque_context=torque_ctx)
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
                                        torque_ctx["task"]["upstream_artifacts"]
                                    )
                                    prompt = _append_task_artifacts(
                                        prompt,
                                        task.attachments,
                                        task.artifacts,
                                        upstream_artifacts,
                                    )
                                    is_clean = \
                                        torque_ctx["context"]["is_clean"]
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
                                        include_identity_anchor=
                                        _should_show_guidance_hint(
                                            state,
                                            cell,
                                            GUIDANCE_HINT_IDENTITY_DISPATCH,
                                        ),
                                    )

                            if not result and not final_prompt:
                                initial_prompt = launch_cfg.get("initial_prompt", "") or ""
                                if not startup_prompt and not initial_prompt.strip():
                                    log.warning(
                                        "dispatch_task: empty prompt sequence for cell=%s task=%s (startup=%d, initial=%d, final=%d)",
                                        cell.slug or cell.name or cell.id, task.id,
                                        len(startup_prompt or ""), len(initial_prompt), len(final_prompt or ""))
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
                                            cell=cell,
                                            task_id=task.id,
                                            include_identity_anchor=
                                            _should_show_guidance_hint(
                                                state,
                                                cell,
                                                GUIDANCE_HINT_IDENTITY_LAUNCH,
                                            ),
                                            include_final_identity_anchor=False):
                                    await _send_agent_prompt(
                                        cell,
                                        prompt_text,
                                        **send_kwargs)

                            state.history_record_dispatch(
                                cell,
                                task,
                                engineer_group=data.get(
                                    "_engineer_dispatch_group",
                                    "",
                                ),
                                engineer_id=data.get(
                                    "_engineer_dispatch_id",
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
                elif "torque:human" not in (task.labels or []):
                    result = {"type": "error",
                              "message": "Not an ask task"}
                elif not answer.strip():
                    result = {"type": "error",
                              "message": "Answer is required"}
                elif _is_architect_ask_task(task):
                    result = await _resolve_architect_ask_task(
                        state,
                        bridge,
                        task,
                        answer,
                        panel_event=_panel_event,
                    )
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

                            save_direct_ask_reply_mirror(
                                state,
                                agent,
                                answer,
                                question=str(getattr(task, "task", "") or ""),
                                source_task_id=str(getattr(task, "id", "") or ""),
                            )
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
                        created_by_engineer_id="",
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
                    torque_ctx = _build_torque_context(
                        state, preview_cell, preview_task_obj)
                    is_clean = torque_ctx["context"]["is_clean"]
                    shared_context_block = build_prompt_memory_block(
                        state.db,
                        cell=preview_cell,
                        task=preview_task_obj,
                    )
                else:
                    torque_ctx = {
                        **TORQUE_CONTEXT_STUB,
                        "task": {
                            **TORQUE_CONTEXT_STUB["task"],
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
                        torque_context=torque_ctx)
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

            elif cmd == "ui_select_group":
                raw_group = str(data.get("group", "") or "").strip()
                if raw_group and raw_group not in state.groups:
                    raw_group = ""
                state.active_group = raw_group
                state._emit(
                    "ui_update",
                    key="active_group",
                    value=state.active_group,
                )
                state._db_save_ui("active_group", state.active_group)

            elif cmd == "ui_select_principal":
                raw_principal = str(data.get("principal_id", "") or "").strip()
                # Empty string is "user" (the default principal).
                # Otherwise must be an existing architect agent.
                if raw_principal:
                    target = state.agents.get(raw_principal)
                    if not target or (target.kind or "") != "architect":
                        raw_principal = ""
                state.selected_principal_id = raw_principal
                state._emit(
                    "ui_update",
                    key="selected_principal_id",
                    value=state.selected_principal_id,
                )
                state._db_save_ui(
                    "selected_principal_id",
                    state.selected_principal_id,
                )

            elif cmd in {"select_agent", "ui_select_agent"}:
                raw_agent_id = str(data.get("id", "") or "").strip()
                if raw_agent_id:
                    target = state.agents.get(raw_agent_id)
                    if not target or state.agent_is_tombstoned(target):
                        raw_agent_id = ""
                    elif target.cell_type == "terminal" and target.parent_id:
                        raw_agent_id = target.parent_id
                state.selected_agent_id = raw_agent_id
                state._emit(
                    "ui_update",
                    key="selected_agent_id",
                    value=state.selected_agent_id,
                )
                state._db_save_ui(
                    "selected_agent_id",
                    state.selected_agent_id,
                )

            elif cmd == "ui_set_window_bounds":
                raw_window = str(data.get("window", "") or "").strip()
                bounds = data.get("bounds", {})
                if not raw_window or not isinstance(bounds, dict):
                    result = {
                        "type": "error",
                        "message": "Invalid window bounds state",
                    }
                else:
                    normalized = {}
                    for key in ("x", "y", "width", "height"):
                        value = bounds.get(key)
                        if value is None:
                            continue
                        try:
                            normalized[key] = float(value)
                        except (TypeError, ValueError):
                            continue
                    display_id = str(bounds.get("display_id", "") or "").strip()
                    if display_id:
                        normalized["display_id"] = display_id
                    if normalized:
                        next_bounds = dict(state.window_bounds or {})
                        next_bounds[raw_window] = normalized
                        state.window_bounds = next_bounds
                    else:
                        state.window_bounds = {
                            key: value
                            for key, value in (state.window_bounds or {}).items()
                            if key != raw_window
                        }
                    state._emit(
                        "ui_update",
                        key="window_bounds",
                        value=state.window_bounds,
                    )
                    state._db_save_ui(
                        "window_bounds",
                        json.dumps(state.window_bounds),
                    )

            elif cmd == "ui_set_workspace_sidebar_width":
                try:
                    width = int(data.get("width", 0) or 0)
                except (TypeError, ValueError):
                    width = 0
                state.workspace_sidebar_width = max(0, width)
                state._emit(
                    "ui_update",
                    key="workspace_sidebar_width",
                    value=state.workspace_sidebar_width,
                )
                state._db_save_ui(
                    "workspace_sidebar_width",
                    state.workspace_sidebar_width,
                )

            elif cmd == "ui_set_terminal_direct_messages_height":
                try:
                    height = int(data.get("height", 0) or 0)
                except (TypeError, ValueError):
                    height = 0
                state.terminal_direct_messages_height = max(0, height)
                state._emit(
                    "ui_update",
                    key="terminal_direct_messages_height",
                    value=state.terminal_direct_messages_height,
                )
                state._db_save_ui(
                    "terminal_direct_messages_height",
                    state.terminal_direct_messages_height,
                )

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

            elif cmd == "ui_set_detached_panels":
                detached_panels = data.get("detached_panels", {})
                if not isinstance(detached_panels, dict):
                    result = {
                        "type": "error",
                        "message": "Invalid detached panel state",
                    }
                else:
                    normalized = {}
                    for panel, raw in detached_panels.items():
                        panel = str(panel or "").strip()
                        if not panel or not isinstance(raw, dict):
                            continue
                        item = dict(raw)
                        bounds = item.get("bounds")
                        if bounds is not None and not isinstance(bounds, dict):
                            item.pop("bounds", None)
                        label = str(item.get("label", "") or "").strip()
                        if label:
                            item["label"] = label
                        normalized[panel] = item
                    state.detached_panels = normalized
                    state._emit(
                        "ui_update",
                        key="detached_panels",
                        value=state.detached_panels,
                    )
                    state._db_save_ui(
                        "detached_panels",
                        json.dumps(state.detached_panels),
                    )

            elif cmd == "ui_set_detached_panel_bounds":
                panel = str(data.get("panel", "") or "").strip()
                label = str(data.get("label", "") or "").strip()
                bounds = data.get("bounds", {})
                if not panel or not isinstance(bounds, dict):
                    result = {
                        "type": "error",
                        "message": "Invalid detached panel bounds state",
                    }
                else:
                    normalized_bounds = {}
                    for key in ("x", "y", "width", "height"):
                        value = bounds.get(key)
                        if value is None:
                            continue
                        try:
                            normalized_bounds[key] = float(value)
                        except (TypeError, ValueError):
                            continue
                    display_id = str(bounds.get("display_id", "") or "").strip()
                    if display_id:
                        normalized_bounds["display_id"] = display_id
                    next_panels = dict(state.detached_panels or {})
                    entry = dict(next_panels.get(panel) or {})
                    if label:
                        entry["label"] = label
                    if normalized_bounds:
                        entry["bounds"] = normalized_bounds
                    if entry:
                        next_panels[panel] = entry
                    state.detached_panels = next_panels
                    state._emit(
                        "ui_update",
                        key="detached_panels",
                        value=state.detached_panels,
                    )
                    state._db_save_ui(
                        "detached_panels",
                        json.dumps(state.detached_panels),
                    )

            elif cmd == "first_run_complete":
                sentinel = Path.home() / ".torque" / ".first_run_complete"
                sentinel.parent.mkdir(parents=True, exist_ok=True)
                sentinel.write_text(
                    datetime.now(timezone.utc).isoformat(),
                    encoding="utf-8",
                )
                result = {
                    "type": "ok",
                    "first_run_complete": True,
                    "sentinel": str(sentinel),
                }

            elif cmd == "ui_set_engineer_panel_split":
                try:
                    fraction = float(data.get("fraction", 0.30))
                except (TypeError, ValueError):
                    fraction = 0.30
                fraction = max(0.12, min(0.75, fraction))
                state.engineer_panel_split_fraction = fraction
                state._emit(
                    "ui_update",
                    key="engineer_panel_split_fraction",
                    value=state.engineer_panel_split_fraction,
                )
                state._db_save_ui(
                    "engineer_panel_split_fraction",
                    state.engineer_panel_split_fraction,
                )

            elif cmd == "ui_set_context_panel_split":
                try:
                    ratio = float(data.get("ratio", 0.38))
                except (TypeError, ValueError):
                    ratio = 0.38
                ratio = max(0.28, min(0.62, ratio))
                state.context_panel_split_ratio = ratio
                state._emit(
                    "ui_update",
                    key="context_panel_split_ratio",
                    value=state.context_panel_split_ratio,
                )
                state._db_save_ui(
                    "context_panel_split_ratio",
                    state.context_panel_split_ratio,
                )

            elif cmd == "ui_set_supervisor_panel_state":
                raw = data.get("state", {})
                if not isinstance(raw, dict):
                    result = {
                        "type": "error",
                        "message": "Invalid supervisor panel state",
                    }
                else:
                    sort_key = str(raw.get("sortKey", "") or "")
                    if sort_key not in {
                        "state", "owner", "session", "pid",
                        "command", "bytes", "tty", "path",
                    }:
                        sort_key = "owner"
                    sort_direction = str(
                        raw.get("sortDirection", "") or ""
                    )
                    if sort_direction not in {"asc", "desc"}:
                        sort_direction = "asc"
                    try:
                        scroll_pos = max(
                            0,
                            int(float(raw.get("scrollPos", 0) or 0)),
                        )
                    except (TypeError, ValueError):
                        scroll_pos = 0
                    state.supervisor_panel_state = {
                        "autoRefresh": bool(raw.get("autoRefresh", True)),
                        "sortKey": sort_key,
                        "sortDirection": sort_direction,
                        "selectedSessionId": str(
                            raw.get("selectedSessionId", "") or ""
                        ),
                        "expandedSessionId": str(
                            raw.get("expandedSessionId", "") or ""
                        ),
                        "scrollPos": scroll_pos,
                    }
                    state._emit(
                        "ui_update",
                        key="supervisor_panel_state",
                        value=state.supervisor_panel_state,
                    )
                    state._db_save_ui(
                        "supervisor_panel_state",
                        json.dumps(state.supervisor_panel_state),
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

            elif cmd == "board_set_selected_lanes":
                raw_lanes = data.get("selected_lanes_by_group", {})
                if isinstance(raw_lanes, dict):
                    state.board_selected_lanes_by_group = {
                        str(group or ""): str(lane or "")
                        for group, lane in raw_lanes.items()
                        if str(group or "") and str(lane or "")
                    }
                else:
                    state.board_selected_lanes_by_group = {}
                state._emit("ui_update", key="board_selected_lanes_by_group",
                            value=state.board_selected_lanes_by_group)
                state._db_save_ui(
                    "board_selected_lanes_by_group",
                    json.dumps(state.board_selected_lanes_by_group),
                )

            elif cmd == "board_set_hidden_wide_lanes":
                raw_lanes = data.get("hidden_wide_lanes_by_group", {})
                normalized = {}
                if isinstance(raw_lanes, dict):
                    for group, lanes in raw_lanes.items():
                        group = str(group or "")
                        if not group or not isinstance(lanes, dict):
                            continue
                        lane_state = {
                            str(lane or ""): True
                            for lane, hidden in lanes.items()
                            if str(lane or "") and bool(hidden)
                        }
                        normalized[group] = lane_state
                state.board_hidden_wide_lanes_by_group = normalized
                state._emit("ui_update",
                            key="board_hidden_wide_lanes_by_group",
                            value=state.board_hidden_wide_lanes_by_group)
                state._db_save_ui(
                    "board_hidden_wide_lanes_by_group",
                    json.dumps(state.board_hidden_wide_lanes_by_group),
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
                                nxt = cron_next(
                                    cron_expr,
                                    datetime.now(timezone.utc), tz=tz)
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
                                nxt = cron_next(
                                    new_cron,
                                    datetime.now(timezone.utc),
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
                        nxt = cron_next(sched.cron_expr,
                                        datetime.now(timezone.utc),
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
                    now = datetime.now(timezone.utc)
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
                        labels=list(sched.labels),
                        board_sync={
                            "version": 1,
                            "auto_track": False,
                            "auto_sync_excluded": True,
                            "auto_sync_excluded_reason": "schedule",
                        })
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
                        # Emit a live `mcp_call_append` delta for the
                        # `mcp__torque__torque_<act>` tool call on the SAME
                        # broadcast that carries this report's
                        # event_append + agent_upsert. Without this, the
                        # only `mcp_call_append` for worker reports
                        # would come from the `/events` PostToolUse hook
                        # — a separate `state.broadcast()` ~30-100ms
                        # later (Claude Code dispatches the hook after
                        # the MCP tool returns). That second broadcast
                        # misses rAF coalesce in the frontend and
                        # produces a second full DOM rebuild of the
                        # engineer panel per call (visible flicker,
                        # mid-type selection loss, scroll-anchor
                        # churn). The `/events` capture clause
                        # downstream suppresses its own emission for
                        # tool names in `_TORQUE_AI_MCP_REPORT_TOOL_NAMES`
                        # so we don't double-emit; persistence still
                        # writes, so the on-demand `cmd=mcp_calls`
                        # fetch keeps working and codex workers (no
                        # PostToolUse hooks) keep getting the live
                        # delta from this path.
                        if act in _TORQUE_AI_MCP_REPORT_ACTIONS:
                            try:
                                _now = time.time()
                                row = {
                                    "cursor": 0,
                                    "idempotency_key": "",
                                    "cell_id": c.id,
                                    "tool_name": "mcp__torque__torque_" + act,
                                    "hook_event_name": "PostToolUse",
                                    "session_id": getattr(
                                        c, "session_id", "") or "",
                                    "appended_at": _now,
                                    "received_at": _now,
                                    "duration_ms": None,
                                    "success": act != "error",
                                    "error": (msg if act == "error" else ""),
                                    "args": {"message": str(msg)} if msg else {},
                                    "args_redacted": False,
                                    "result": None,
                                    "result_redacted": True,
                                    "agent_name": c.name,
                                    "agent_slug": getattr(c, "slug", ""),
                                    "agent_kind": getattr(c, "kind", ""),
                                    "group": getattr(c, "group", ""),
                                }
                                state._emit(
                                    "mcp_call_append",
                                    group=row["group"],
                                    call=row,
                                )
                            except Exception:
                                log.exception(
                                    "Failed to emit synthetic "
                                    "mcp_call_append for ai_report"
                                )

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
                        deliverable_rejection = (
                            _reject_missing_deliverable(task, "done")
                        )
                        if deliverable_rejection:
                            result = deliverable_rejection
                    if (
                        not (result and result.get("type") in (
                            "error", "deliverable_missing"))
                        and action == "done"
                    ):
                        review_rejection = (
                            _reject_pending_review(task, "done")
                        )
                        if review_rejection:
                            result = review_rejection
                    if (
                        not (result and result.get("type") == "error")
                        and not (result
                                 and result.get("type")
                                 in ("deliverable_missing",
                                     "review_required"))
                        and action == "done"
                    ):
                        base_dir = cell.worktree_repo_root \
                            or cell.directory \
                            or await _resolve_base_dir(
                                task.group if task else cell.group)
                        mandatory_review_rejection = (
                            _reject_mandatory_review_done_without_ship(
                                state,
                                action_mgr,
                                cell,
                                task,
                                base_dir=base_dir,
                            )
                        )
                        if mandatory_review_rejection:
                            result = mandatory_review_rejection
                        else:
                            rejected = (
                                _reject_completion_with_open_descendants(
                                    state, task, "done")
                            )
                            if rejected:
                                result = rejected
                            elif not result:
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
                                            f"torque: checkpoint {n} — "
                                            f"{cell.name}"
                                        )
                                        if message:
                                            cp_msg = f"{cp_msg}\n\n{message}"
                                        elif cell.last_summary:
                                            cp_msg = (
                                                f"{cp_msg}\n\n"
                                                f"{cell.last_summary.strip()}"
                                            )
                                        sha = await _checkpoint_worktree_with_submodules(
                                            cell,
                                            cp_msg,
                                        )
                                        if sha:
                                            state._db_save_agent(cell)
                                    except Exception:
                                        log.exception(
                                            "review gate checkpoint failed for"
                                            " '%s'", cell.name)

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

                    if result and result.get("type") in (
                            "error", "deliverable_missing",
                            "review_required"):
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
                        # torque_done mid-turn. Running the same
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
                                    sha = await _checkpoint_worktree_with_submodules(
                                        cell,
                                        cp_msg,
                                    )
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
                                        posted, "torque")
                                    _save_task(task)
                                    result = {
                                        "type": "external_comment_posted",
                                        "task_id": task.id,
                                        "message": posted,
                                    }
                                except ExternalTicketError as exc:
                                    _append_task_msg(
                                        task, "external_error",
                                        str(exc), "torque")
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
                            _add_label(task, "torque:blocked")
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
                            _add_label(task, "torque:error")
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
                        deliverable_rejection = (
                            _reject_missing_deliverable(task, "ready")
                        )
                        review_rejection = (
                            None if deliverable_rejection
                            else _reject_pending_review(task, "ready")
                        )
                        rejected = (
                            deliverable_rejection
                            or review_rejection
                            or _reject_completion_with_open_descendants(
                                state, task, "ready")
                        )
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
                        target_agent = (
                            data.get("target_agent", "") or ""
                        ).strip()
                        if (
                            task
                            and not target_agent
                            and str(act_name or "").strip().lower()
                            == _REVIEW_GATE_ACTION
                        ):
                            prior_reviewer = \
                                _prior_live_reviewer_agent_for_chain(
                                    state,
                                    task,
                                )
                            if prior_reviewer:
                                target_agent = prior_reviewer.id
                        is_auto_review_gate = bool(
                            data.get("_review_gate")
                        ) and act_name == _REVIEW_GATE_ACTION

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
                                    and act_name not in valid_targets \
                                    and not is_auto_review_gate:
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
                                stale_base_rejection = None
                                if not (max_d and new_depth > max_d):
                                    stale_base_rejection = (
                                        await _maybe_reject_stale_base_review_derive(
                                            worktree_mgr,
                                            cell,
                                            act_name,
                                        )
                                    )
                                if max_d and new_depth > max_d:
                                    cell.needs_attention = True
                                    state._emit_agent(cell)
                                    if task:
                                        _add_label(task,
                                                   "torque:depth-limit")
                                        _save_task(task)
                                    result = {
                                        "type": "error",
                                        "message":
                                            f"Pipeline depth limit "
                                            f"({max_d}) reached"}
                                elif stale_base_rejection:
                                    result = stale_base_rejection
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
                                    if (not derive_status
                                            and is_auto_review_gate):
                                        derive_status = "On Review"
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
                                    derive_parent_task = \
                                        _ai_derive_parent_task(
                                            state,
                                            task,
                                        )
                                    derive_parent_task_id = (
                                        derive_parent_task.id
                                        if derive_parent_task else task.id
                                    )
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
                                    # Mandatory-review pre-approval bypass
                                    # (TORQUE:256). When a reviewer derives a
                                    # fix via a ``pre_approved: true``
                                    # transition, stamp the derived task with
                                    # the reviewer's task id so its
                                    # ``torque_done`` gate resolves clean.
                                    derive_pre_approved_by = ""
                                    if cur_transitions and act_name:
                                        for tr in cur_transitions:
                                            if isinstance(tr, dict) \
                                                    and tr.get("action") \
                                                    == act_name \
                                                    and tr.get("pre_approved"):
                                                derive_pre_approved_by = task.id
                                                break
                                    new_task = reusable_task
                                    if not new_task:
                                        new_task = state.board_add_task(
                                            task=message,
                                            group=grp,
                                            lane="Backlog",
                                            action_name=act_name,
                                            action_vars=act_vars,
                                            labels=["torque:derived"],
                                            parent_task_id=derive_parent_task_id,
                                            pipeline_depth=new_depth,
                                            pipeline_root_id=root_id,
                                            description=derive_desc,
                                            assigned_engineer_id=assigned_engineer_id,
                                            pre_approved_by=derive_pre_approved_by,
                                        )
                                    elif reused_existing_task:
                                        _refresh_reused_derived_task(
                                            new_task,
                                            message=message,
                                            description=derive_desc,
                                            action_vars=act_vars,
                                        )
                                        new_task.parent_task_id = (
                                            derive_parent_task_id
                                        )
                                        new_task.pipeline_root_id = root_id
                                        # Reuse path: rewrite
                                        # pre_approved_by from the
                                        # selected transition so a
                                        # blocking-fix re-derive onto a
                                        # task that previously carried
                                        # a pre-approval bypass clears
                                        # it (and vice-versa).
                                        new_task.pre_approved_by = (
                                            derive_pre_approved_by
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
                                                derive_parent_task_id) \
                                                if derive_parent_task_id \
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
                                                and cur_transitions \
                                                and not is_auto_review_gate:
                                            # No explicit target declared.
                                            # Reuse an ancestor thread only
                                            # when this derive is clearly
                                            # returning to a prior action
                                            # stage (for example fix ->
                                            # re-review). Otherwise keep the
                                            # normal fresh-agent behavior.
                                            reuse_self = False
                                            if not target_agent:
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
                                            if not (tgt and
                                                    tgt.worktree_path) \
                                                    and cell.worktree_path \
                                                    and derive_parent_task_id == task.id:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                            if cell.worktree_path:
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            elif cell.worktree_branch:
                                                dispatch_data[
                                                    "handoff_worktree_from"
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
                                            owner_engineer_id = \
                                                _ownership_engineer_id_for_dispatch_source(
                                                    cell
                                                )
                                            if owner_engineer_id:
                                                dispatch_data[
                                                    "_created_by_engineer_id"
                                                ] = owner_engineer_id
                                            # Worktree inheritance is resolved
                                            # by dispatch_task from the derived
                                            # task's structural parent.  Do not
                                            # force the caller's branch here:
                                            # review-derived fixes must skip
                                            # the reviewer and land on the
                                            # implementer's branch.
                                            if cell.worktree_path \
                                                    and derive_parent_task_id == task.id:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                            if cell.worktree_path:
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
                                        owner_engineer_id = (
                                            _ownership_engineer_id_for_dispatch_source(
                                                cell
                                            )
                                            or str(
                                                getattr(
                                                    task,
                                                    "assigned_engineer_id",
                                                    "",
                                                )
                                                or ""
                                            ).strip()
                                            or str(
                                                getattr(
                                                    new_task,
                                                    "assigned_engineer_id",
                                                    "",
                                                )
                                                or ""
                                            ).strip()
                                        )
                                        _record_derive_dispatch_shape_metric(
                                            state,
                                            engineer_id=owner_engineer_id,
                                            group=grp,
                                            result=result,
                                            new_task=new_task,
                                            derive_parent_task_id=(
                                                derive_parent_task_id
                                            ),
                                            action_name=act_name,
                                            target_id=target_id or "",
                                            target_agent=target_agent,
                                            reuse_self=bool(reuse_self),
                                            transition_target=tr_target,
                                            reused_existing_task=bool(
                                                reused_existing_task
                                            ),
                                        )
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
                            ask_targets_user = ask_recipient_is_user(
                                state, cell)
                            # Keep parent in In Progress with
                            # "Awaiting Input" status
                            cell.activity = ""
                            cell.activity_detail = ""
                            cell.needs_attention = ask_targets_user
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
                                labels=ask_task_labels_for_owner_recipient(
                                    state,
                                    cell,
                                    ["torque:human", "torque:derived"],
                                ),
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
                                save_direct_ask_mirror(
                                    state,
                                    cell,
                                    message,
                                    source_task_id=new_task.id,
                                )
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
                        result = _handle_engineer_reply(
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

            # -- Engineer commands ------------------------------------------

            elif cmd == "engineer_message":
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
                    reply_required = data.get("reply_required", True)
                    if isinstance(reply_required, str):
                        reply_required = (
                            reply_required.strip().lower()
                            not in {"false", "0", "no", "off", ""}
                        )
                    result = await _send_engineer_message_to_agent(
                        state,
                        bridge,
                        target,
                        msg_text,
                        _panel_event,
                        sender_agent_id=str(
                            data.get("sender_agent_id", "") or ""
                        ).strip(),
                        reply_required=bool(reply_required),
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

            elif cmd == "architect_journal_append":
                architect_id = str(
                    data.get("architect_id")
                    or data.get("cell_id")
                    or ""
                ).strip()
                entry_type = str(data.get("entry_type", "") or "").strip()
                entry_text = str(data.get("entry", "") or "")
                if not architect_id:
                    result = {
                        "type": "error",
                        "message": "architect_id is required",
                    }
                elif _agent_dismissed_at(state.agents.get(architect_id)):
                    result = _architect_dismissed_error(architect_id)
                elif entry_type not in (
                    "decision", "observation", "checkpoint", "plan"
                ):
                    result = {
                        "type": "error",
                        "message": (
                            "entry_type must be one of: decision, "
                            "observation, checkpoint, plan"
                        ),
                    }
                elif not entry_text:
                    result = {
                        "type": "error",
                        "message": "Entry text is required",
                    }
                else:
                    try:
                        result = state.architect_journal_append(
                            architect_id,
                            entry_type,
                            entry_text,
                            idempotency_key=str(
                                data.get("idempotency_key", "") or ""
                            ).strip(),
                            request_hash=(
                                critical_request_hash
                                if critical_idempotency_key else ""
                            ),
                        )
                    except ValueError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "engineer_journal_append":
                group = data.get("group", "")
                entry_type = data.get("entry_type", "")
                entry_text = data.get("entry", "")
                if entry_type not in (
                        "decision", "observation", "checkpoint", "plan",
                        "note_dismissed", "qa"):
                    result = {"type": "error",
                              "message":
                                  "entry_type must be one of: decision, "
                                  "observation, checkpoint, plan, "
                                  "note_dismissed, qa"}
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

            elif cmd == "engineer_journal_read":
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

            elif cmd == "engineer_session_map_read":
                group = str(data.get("group", "") or "").strip()
                if not group:
                    result = {
                        "type": "error",
                        "message": "Group is required",
                    }
                else:
                    engineer_id = str(
                        data.get("engineer_id")
                        or data.get("agent_id")
                        or state.get_group_settings(group).engineer_agent_id
                        or ""
                    ).strip()
                    engineer_cell = state.agents.get(engineer_id)
                    if (
                            not engineer_cell
                            or getattr(engineer_cell, "cell_type", "") != "agent"
                            or str(
                                getattr(engineer_cell, "kind", "") or ""
                            ).strip() != "engineer"
                            or str(
                                getattr(engineer_cell, "group", "") or ""
                            ).strip() != group
                    ):
                        engineer_cell = None
                    result = {
                        "type": "engineer_session_map",
                        "group": group,
                        "engineer_id": (
                            getattr(engineer_cell, "id", "")
                            if engineer_cell else ""
                        ),
                        "session_map": build_engineer_session_map(
                            state,
                            group,
                            engineer_cell=engineer_cell,
                        ),
                    }

            elif cmd == "engineer_journal_delete":
                group = data.get("group", "")
                entry_id = data.get("entry_id", 0)
                if entry_id and db:
                    author_cell_id = str(
                        data.get("author_cell_id", "") or ""
                    ).strip()
                    if not author_cell_id:
                        row = db._conn.execute(
                            "SELECT author_cell_id FROM engineer_journal "
                            "WHERE id=? AND group_name=?",
                            (entry_id, group),
                        ).fetchone()
                        if row:
                            author_cell_id = str(row[0] or "").strip()
                    db._conn.execute(
                        "DELETE FROM engineer_journal WHERE id=? "
                        "AND group_name=?", (entry_id, group))
                    db._conn.commit()
                    state._emit("journal_delete",
                                group=group, id=entry_id,
                                author_cell_id=author_cell_id)
                result = {"type": "ok"}

            elif cmd == "engineer_update_settings":
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
                          "engineer_can_override_worker_provider",
                          "paused", "engineer_provider",
                          "engineer_boot_command", "engineer_model",
                          "engineer_reasoning_effort",
                          "engineer_directory", "engineer_profile",
                          "engineer_shell", "engineer_tab_color"):
                    if k in data:
                        fields[k] = data[k]
                await state.update_engineer_settings_async(group, **fields)
                result = {"type": "ok"}

            elif cmd == "engineer_ask":
                group = data.get("group", "")
                question = data.get("question", "")
                if not question:
                    result = {"type": "error",
                              "message": "Question is required"}
                else:
                    engineer_id = str(
                        data.get("engineer_id", "")
                        or data.get("cell_id", "")
                        or ""
                    ).strip()
                    engineer = None
                    if not engineer_id:
                        engineer = state.get_engineer_for_group(group)
                        engineer_id = str(
                            getattr(engineer, "id", "") or ""
                        ).strip()
                    else:
                        engineer = state.agents.get(engineer_id)
                    await state.update_engineer_settings_async(
                        group,
                        pending_question=question,
                        paused=True,
                        _pending_question_actor_id=engineer_id)
                    ws = state.get_engineer_settings(group)
                    try:
                        question_ts = float(
                            getattr(ws, "pending_question_set_at", 0) or 0
                        )
                    except (TypeError, ValueError):
                        question_ts = 0.0
                    source_key = direct_ask_mirror_source_key(
                        group=group,
                        agent_id=engineer_id,
                        timestamp=question_ts,
                        question=question,
                    )
                    save_direct_ask_mirror(
                        state,
                        engineer,
                        question,
                        source_key=source_key,
                        created_at=question_ts or None,
                    )
                    log.info(
                        "engineer_ask persisted pending question for group=%s "
                        "pending_question_len=%d paused=True",
                        group,
                        len(str(question or "")),
                    )
                    engineer_buffer.on_delivery_paused(group)
                    # Also log to journal
                    state.journal_append(
                        group, "observation",
                        f"Asked human: {question}")
                    result = {"type": "ok"}

            elif cmd == "engineer_note":
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
                    engineer_id = str(
                        data.get("engineer_id", "")
                        or data.get("cell_id", "")
                        or ""
                    ).strip()
                    if not engineer_id:
                        engineer = state.get_engineer_for_group(group)
                        engineer_id = str(
                            getattr(engineer, "id", "") or ""
                        ).strip()
                    await state.update_engineer_settings_async(
                        group,
                        pending_note=message,
                        pending_note_kind=kind,
                        _pending_note_actor_id=engineer_id)
                    prefix = "Soft question" if kind == "question" else "Note"
                    state.journal_append(
                        group, "observation",
                        f"{prefix} for human: {message}")
                    result = {"type": "ok"}

            elif cmd == "engineer_dismiss_note":
                result = await _handle_engineer_dismiss_note_command(
                    data,
                    state,
                    _panel_event,
                )

            elif cmd == "engineer_reply":
                group = data.get("group", "")
                answer = data.get("answer", "")
                if not answer:
                    result = {"type": "error",
                              "message": "Answer is required"}
                else:
                    reply_target, target_label = _pending_question_reply_target(
                        state,
                        group,
                    )
                    if not reply_target or not reply_target.session_id:
                        result = {"type": "error",
                                  "message": f"{target_label} is not running"}
                    else:
                        result = await _deliver_engineer_reply_and_resume(
                            state,
                            reply_target,
                            group=group,
                            answer=answer,
                            send_prompt=_send_agent_prompt,
                            engineer_buffer=engineer_buffer,
                        )

            elif cmd == "engineer_pause":
                group = data.get("group", "")
                await state.update_engineer_settings_async(group, paused=True)
                engineer_buffer.on_delivery_paused(group)
                result = {"type": "ok"}

            elif cmd == "engineer_resume":
                group = data.get("group", "")
                engineer_id = str(
                    data.get("engineer_id", "")
                    or data.get("cell_id", "")
                    or ""
                ).strip()
                if not engineer_id:
                    engineer = state.get_engineer_for_group(group)
                    engineer_id = str(getattr(engineer, "id", "") or "").strip()
                await state.update_engineer_settings_async(
                    group,
                    paused=False,
                    pending_question="",
                    _pending_question_actor_id=engineer_id)
                engineer_buffer.on_delivery_resumed(group)
                result = {"type": "ok"}

            elif cmd == "digest_pause":
                result = _handle_digest_pause_resume_command(
                    state,
                    engineer_buffer,
                    data,
                    paused=True,
                )

            elif cmd == "digest_resume":
                result = _handle_digest_pause_resume_command(
                    state,
                    engineer_buffer,
                    data,
                    paused=False,
                )

            elif cmd == "engineer_flush_now":
                result = _handle_engineer_flush_now_command(
                    engineer_buffer, data)

            elif cmd == "stop":
                result = await _handle_daemon_stop_command(
                    daemon_stop_state=daemon_stop_state,
                    schedule_daemon_stop=_schedule_daemon_stop,
                    state=state,
                )

            elif cmd == "restart":
                log.info("Restart requested — cleaning up and re-executing")
                # Persist all agents (status etc.) before restart
                for cell in state.agents.values():
                    state._db_save_agent(cell)
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            result = {"type": "error", "message": str(exc)}

        if db and critical_command_name and critical_idempotency_key:
            try:
                # A deliverable_missing refusal is a recoverable hard-gate
                # failure: the worker can flip it to passing by uploading
                # an artifact and retrying. Persist NEITHER the command
                # receipt nor the captured state so a same-key retry
                # re-runs the gate cleanly. We still clean up the
                # failed-write queue entry — there is no value in
                # replaying the same refusal.
                is_deliverable_missing = (
                    isinstance(result, dict)
                    and result.get("type") == "deliverable_missing"
                )
                # Same recoverable-refusal semantics for the
                # mandatory-review gate (TORQUE:256): don't cache the
                # receipt, drop the queued failed-write so a retry after
                # the worker derives the review re-runs the gate cleanly.
                is_review_required = (
                    isinstance(result, dict)
                    and result.get("type") == "review_required"
                )
                if is_deliverable_missing or is_review_required:
                    db.delete_failed_write_by_key(critical_failed_write_key)
                elif critical_capture_active:
                    state.finalize_critical_write_capture(
                        result,
                        delete_failed_write_key=critical_failed_write_key,
                        surface="internal",
                    )
                else:
                    db.save_command_receipt(
                        idempotency_key=critical_idempotency_key,
                        surface="internal",
                        command_name=critical_command_name,
                        request_hash=critical_request_hash or api_request_hash(data),
                        response=result,
                    )
                    db.delete_failed_write_by_key(critical_failed_write_key)
            except Exception as exc:
                log.exception(
                    "Failed to persist internal command receipt for %s",
                    critical_command_name,
                )
                result = {"type": "error", "message": str(exc)}
            finally:
                if critical_capture_active:
                    state.clear_critical_write_capture()

        await state.broadcast()
        return result

    # -- Events endpoint (agent hooks) ----------------------------------------

    async def handle_events(request):
        """Receive events from agent hooks (Claude Code HTTP hooks, etc.)."""
        request_started = time.perf_counter()
        profiling.recorder().incr("events_endpoint_received")
        try:
            raw = await request.json()
        except Exception:
            profiling.recorder().incr("events_dropped_invalid_json")
            if hasattr(db, "record_mcp_health_event_safe"):
                db.record_mcp_health_event_safe(
                    surface="events",
                    event="drop",
                    error="invalid JSON",
                )
            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)
            return web.json_response({}, status=400)

        headers = {
            "X-Torque-Cell-Id": request.headers.get("X-Torque-Cell-Id", ""),
        }
        # TORQUE:238 cutover: workers now report exclusively via
        # `mcp__torque__torque_*` MCP tools (no Bash CLI rewrite bridge).
        # Claude Code already emits PostToolUse hooks with the real
        # mcp__ tool name, so persistence + on-demand fetch +
        # capture clause all see the right shape directly.
        envelope = build_event_ingest_envelope(raw, headers=headers)
        idempotency_key = None
        explicit_event_id = raw.get("event_id")
        if not explicit_event_id and isinstance(raw.get("data"), dict):
            explicit_event_id = raw["data"].get("event_id")
        if explicit_event_id:
            idempotency_key = (
                f"events:{headers['X-Torque-Cell-Id']}:{explicit_event_id}"
            )

        try:
            await _ensure_event_ingest_configured()
            response = await event_ingest_client.append(
                envelope,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            log.exception("Failed to durably enqueue agent event")
            profiling.recorder().incr("events_dropped_ingest_unavailable")
            if hasattr(db, "record_mcp_health_event_safe"):
                db.record_mcp_health_event_safe(
                    surface="events",
                    event="drop",
                    error=str(exc) or type(exc).__name__,
                )
            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)
            return web.json_response(
                {"ok": False, "error": str(exc) or "event ingest unavailable"},
                status=503,
            )
        if response.get("type") == "error":
            profiling.recorder().incr("events_dropped_ingest_error")
            if hasattr(db, "record_mcp_health_event_safe"):
                db.record_mcp_health_event_safe(
                    surface="events",
                    event="drop",
                    error=str(response.get("message", "ingest error")),
                )
            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)
            return web.json_response(
                {"ok": False, "error": response.get("message", "ingest error")},
                status=500,
            )
        if response.get("duplicate") and hasattr(db, "record_mcp_health_event_safe"):
            db.record_mcp_health_event_safe(
                surface="events",
                event="dedupe",
            )
        try:
            raw_tool = str(raw.get("tool_name") or raw.get("name") or "")
            raw_hook = str(raw.get("hook_event_name") or raw.get("type") or "")
            # TORQUE:238: when this PostToolUse came from a worker calling
            # one of the `mcp__torque__torque_*` reporting tools, the live
            # `mcp_call_append` delta was already emitted by the
            # ai_report handler on the same broadcast as the report's
            # event_append + agent_upsert (see `_append_mcp` wrapper).
            # Suppress the duplicate emission here so the frontend sees
            # ONE delta-bundle per call (rAF-coalesced), not two
            # ~30-100ms apart. The persistent ingest row is still
            # written above, so on-demand `cmd=mcp_calls` fetches still
            # resolve the row by cursor — same shape as engineer/
            # architect MCP calls, except those don't go through
            # ai_report so their mcp_call_append fires from this clause
            # alone.
            already_emitted_via_ai_report = (
                raw_tool in _TORQUE_AI_MCP_REPORT_TOOL_NAMES
            )
            if (
                raw_tool.startswith("mcp__")
                and raw_hook == "PostToolUse"
                and not bool(response.get("duplicate"))
                and not already_emitted_via_ai_report
            ):
                redacted_envelope = redact_event_for_mcp_call_log(
                    envelope,
                    args_capture=state.global_settings.mcp_call_log_args_capture,
                    full_capture_tools=(
                        state.global_settings.mcp_call_log_full_capture_tools
                    ),
                )
                record = {
                    "cursor": int(response.get("cursor") or 0),
                    "idempotency_key": idempotency_key or "",
                    "event": redacted_envelope,
                    "appended_at": time.time(),
                }
                rows = _mcp_call_rows_for_ui(state, [record])
                if rows:
                    state._emit(
                        "mcp_call_append",
                        group=rows[0].get("group", ""),
                        call=rows[0],
                    )
        except Exception:
            log.exception("Failed to emit MCP call live delta")
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

    board_sync_manager = BoardSyncManager(
        state,
        panel_event=_panel_event,
        toast=_broadcast_toast,
    )
    board_sync_manager.start()
    log.info("Board sync manager started")

    async def _replay_failed_write(write: dict):
        endpoint = str(write.get("endpoint", "") or "")
        payload = dict(write.get("payload", {}) or {})
        if endpoint == "/internal/cmd":
            return await replay_internal_failed_write_payload(
                db,
                payload,
                handle_command,
            )
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

    cloud_connector_runtime = await cloud_hooks.start_cloud_connector(
        _build_cloud_connector_context()
    )
    cloud_connector_runtime_holder[0] = cloud_connector_runtime
    if cloud_connector_runtime.enabled and not cloud_connector_runtime.started:
        log.warning(
            "Cloud connector not started (module=%s, error=%s)",
            cloud_connector_runtime.module_name,
            cloud_connector_runtime.error,
        )

    # -- HTTP / WS routes ---------------------------------------------------

    async def handle_index(_request):
        from .config import WEBVIEW_FILE  # re-read after init_paths
        return web.FileResponse(WEBVIEW_FILE)

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        compact_snapshot = _request_wants_compact_snapshot(request)

        def ws_state_payload():
            return _state_payload(compact=compact_snapshot)

        if not await _register_ready_ui_ws_client(
                state, ws, ws_state_payload):
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
                        data = json.loads(msg.data)
                        if _payload_wants_compact_snapshot(data):
                            compact_snapshot = True
                        if data.get("type") == "connect":
                            sent = await _register_ready_ui_ws_client(
                                state, ws, ws_state_payload)
                            if not sent:
                                break
                            continue
                        if data.get("cmd") == "resync":
                            sent = await _register_ready_ui_ws_client(
                                state, ws, ws_state_payload)
                            if not sent:
                                break
                            continue

                        result = await handle_command(data)
                        if result:
                            if result.get("type") == "state":
                                sent = await _register_ready_ui_ws_client(
                                    state, ws, ws_state_payload)
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
            # Initial sends use `_send_ui_ws_json` so client disconnects
            # mid-handshake (browser tab closed before WS upgrade settles
            # — common during reload) are swallowed cleanly instead of
            # raising `ClientConnectionResetError` to the request handler.
            if not bridge.capabilities.supports_embedded_terminal:
                if not await _send_ui_ws_json(ws, {
                    "type": "error",
                    "message": "Embedded terminals are unavailable in this runtime.",
                }):
                    return ws
            cell = state.agents.get(cell_id)
            if cell and cell.session_id:
                if not await _send_ui_ws_json(ws, {
                    "type": "snapshot",
                    "cell_id": cell_id,
                    "session_id": cell.session_id,
                    "data": bridge.get_terminal_buffer(cell.session_id),
                }):
                    return ws
            else:
                if not await _send_ui_ws_json(ws, {
                    "type": "snapshot",
                    "cell_id": cell_id,
                    "session_id": "",
                    "data": "",
                }):
                    return ws
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
        if daemon_stop_state.should_reject_api_request(cmd):
            return web.json_response(_daemon_stop_rejection_payload(), status=503)

        guard = _api_worker_context_guard(
            data,
            request.headers,
            getattr(request, "remote", "") or "",
        )
        if guard:
            return web.json_response(
                {"ok": False, "error": guard["message"],
                 "type": "worker_lifecycle_guard"},
                status=guard["status"])

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
        if result and result.get("type") == "deliverable_missing":
            # Hard-gate refusal: surface as a CLI/REST failure so the
            # documented `torque ai done`/`ready` paths see the same outcome
            # workers see via MCP. Skip idempotency caching so a retry
            # after the artifact is uploaded actually re-runs the gate.
            return web.json_response(
                {
                    "ok": False,
                    "error": result.get(
                        "message",
                        "Deliverable artifact required before completion.",
                    ),
                    "type": "deliverable_missing",
                },
                status=409,
            )
        if result and result.get("type") == "review_required":
            # Mandatory-review hard-gate refusal (TORQUE:256). Same shape as
            # deliverable_missing — non-2xx, no idempotency cache, so a
            # retry after the worker derives the review re-runs the gate.
            return web.json_response(
                {
                    "ok": False,
                    "error": result.get(
                        "message",
                        "Review required by action contract before completion.",
                    ),
                    "type": "review_required",
                },
                status=409,
            )

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
                    cell.command != "torque-profile-harness"
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
                command="torque-profile-harness",
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

    async def handle_logs(request):
        """GET /logs — cursor-tail Torque's profile log for the in-app viewer."""
        try:
            since = float(request.query.get("since", "0") or 0)
        except (TypeError, ValueError):
            since = 0.0
        try:
            limit = int(request.query.get("limit", _LOG_MAX_LINES) or _LOG_MAX_LINES)
        except (TypeError, ValueError):
            limit = _LOG_MAX_LINES
        payload = _tail_log_entries(DATA_DIR / "torque.log", since=since, limit=limit)
        payload["follow"] = request.query.get("follow", "0") in {"1", "true", "yes"}
        return web.json_response(payload)

    async def handle_ui_state(_request):
        """GET /api/ui_state — lightweight state needed before first paint.

        The Tauri shell uses this before showing the main window so native
        window geometry can be restored without waiting for the WebSocket
        snapshot.
        """
        return web.json_response({
            "window_bounds": state.window_bounds or {},
            "detached_panels": state.detached_panels or {},
            "active_group": state.active_group or "",
        })

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
    app_server.router.add_get("/logs", handle_logs)
    app_server.router.add_get("/api/ui_state", handle_ui_state)
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

        log.info("Open http://127.0.0.1:%d/ in a browser", WS_PORT)

        await daemon_stop_event.wait()
    finally:
        await board_sync_manager.stop()
        await _shutdown_daemon_runtime(
            terminal_clients=terminal_clients,
            ui_ws_clients=state._ws_clients,
            panel_log=panel_log,
            event_ingest_drainer=event_ingest_drainer,
            event_ingest_client=event_ingest_client,
            cloud_connector_runtime=cloud_connector_runtime_holder[0],
            bridge=bridge,
            runner=runner,
            state=state,
            db=db,
        )
