"""Worktree lifecycle and merge command orchestration.

The transport server composes runtime dependencies; this module owns command
branching and result semantics for the complete worktree command surface.
"""

from __future__ import annotations

import asyncio as _aio
import os
import time
from dataclasses import dataclass
from typing import Any

from ..config import log


WORKTREE_COMMAND_NAMES = frozenset({
    "worktree_create",
    "worktree_advance_boundary",
    "worktree_adopt",
    "worktree_remove",
    "worktree_list",
    "worktree_prune",
    "worktree_checkpoint",
    "worktree_history",
    "worktree_diff_full",
    "worktree_check_merge",
    "worktree_rebase",
    "worktree_rollback",
    "worktree_diff",
    "worktree_check_conflicts",
    "worktree_create_pr",
    "worktree_merge",
})


@dataclass(slots=True)
class WorktreeCommandRuntime:
    ExistingWorktreeTarget: Any
    apply_persistent_prompt: Any
    attach_stale_base: Any
    boundary_gate_message: Any
    boundary_mismatch_check_allowed: Any
    boundary_reason_message: Any
    broadcast_toast: Any
    build_cell_persistent_prompt: Any
    checkpoint_message: Any
    checkpoint_worktree_with_submodules: Any
    classify_repo_worktrees: Any
    cleanup_after_merge: Any
    configured_worktree_submodules_for_cell: Any
    emit_stale_base_catch_workflow_breach: Any
    emit_workflow_breach_event: Any
    engineer_merge_mode_for_cell: Any
    generate_merge_message: Any
    is_designated_engineer: Any
    is_reconcilable_nested_gitlink_conflict: Any
    latest_boundary_state_for_cell: Any
    launch_resolver_for_cell: Any
    log_pr_task_ref_rewrite: Any
    mark_branch_boundaries_merged: Any
    panel_event: Any
    persistent_prompt_filename: Any
    reconcile_worktree_branch: Any
    recover_authoritative_post_success_from_boundary: Any
    relaunch_agent_after_worktree_removal: Any
    resolve_agent_launch_config: Any
    resolve_architect_launch_config: Any
    resolve_base_dir: Any
    resolve_engineer_launch_config: Any
    resolve_worker_launch_config: Any
    resolve_worktree_command_target_value: Any
    rewrite_pr_torque_task_refs_metadata: Any
    run_direct_worktree_merge: Any
    run_pr_worktree_merge: Any
    safe_remove_worktree_result: Any
    save_task_record: Any
    scope_domain_for_cell: Any
    send_agent_prompt: Any
    shared_review_checkpoint_block_reason: Any
    stale_base_check_merge_result: Any
    stale_base_force_enabled: Any
    stale_base_post_rebase_evidence_required: Any
    stale_base_warning: Any
    target_branch_from_payload: Any
    target_has_driverless_payload: Any
    untracked_overwrite_message: Any
    workflow_breach_active_task_for_worker: Any
    worktree_full_diff: Any
    worktree_merge_error: Any
    worktree_merge_requested_cleanup: Any
    worktree_submodules_for_cell: Any
    advance_latest_boundary_after_mechanical_commit: Any
    board_sync_manager: Any
    bridge: Any
    get_adapter: Any
    handle_command: Any
    mcp_entrypoint_for_cell: Any
    refresh_latest_boundary_after_rebase: Any
    runtime_env_vars_for_cell: Any
    state: Any
    worktree_mgr: Any


async def handle_worktree_command(
    data: dict,
    runtime: WorktreeCommandRuntime,
) -> dict | None:
    ExistingWorktreeTarget = runtime.ExistingWorktreeTarget
    _apply_persistent_prompt = runtime.apply_persistent_prompt
    _attach_stale_base = runtime.attach_stale_base
    _boundary_gate_message = runtime.boundary_gate_message
    _boundary_mismatch_check_allowed = runtime.boundary_mismatch_check_allowed
    _boundary_reason_message = runtime.boundary_reason_message
    _broadcast_toast = runtime.broadcast_toast
    _build_cell_persistent_prompt = runtime.build_cell_persistent_prompt
    _checkpoint_message = runtime.checkpoint_message
    _checkpoint_worktree_with_submodules = runtime.checkpoint_worktree_with_submodules
    _classify_repo_worktrees = runtime.classify_repo_worktrees
    _cleanup_after_merge = runtime.cleanup_after_merge
    _configured_worktree_submodules_for_cell = runtime.configured_worktree_submodules_for_cell
    _emit_stale_base_catch_workflow_breach = runtime.emit_stale_base_catch_workflow_breach
    _emit_workflow_breach_event = runtime.emit_workflow_breach_event
    _engineer_merge_mode_for_cell = runtime.engineer_merge_mode_for_cell
    _generate_merge_message = runtime.generate_merge_message
    _is_designated_engineer = runtime.is_designated_engineer
    _is_reconcilable_nested_gitlink_conflict = runtime.is_reconcilable_nested_gitlink_conflict
    _latest_boundary_state_for_cell = runtime.latest_boundary_state_for_cell
    _launch_resolver_for_cell = runtime.launch_resolver_for_cell
    _log_pr_task_ref_rewrite = runtime.log_pr_task_ref_rewrite
    _mark_branch_boundaries_merged = runtime.mark_branch_boundaries_merged
    _panel_event = runtime.panel_event
    _persistent_prompt_filename = runtime.persistent_prompt_filename
    _reconcile_worktree_branch = runtime.reconcile_worktree_branch
    _recover_authoritative_post_success_from_boundary = runtime.recover_authoritative_post_success_from_boundary
    _relaunch_agent_after_worktree_removal = runtime.relaunch_agent_after_worktree_removal
    _resolve_agent_launch_config = runtime.resolve_agent_launch_config
    _resolve_architect_launch_config = runtime.resolve_architect_launch_config
    _resolve_base_dir = runtime.resolve_base_dir
    _resolve_engineer_launch_config = runtime.resolve_engineer_launch_config
    _resolve_worker_launch_config = runtime.resolve_worker_launch_config
    _resolve_worktree_command_target_value = runtime.resolve_worktree_command_target_value
    _rewrite_pr_torque_task_refs_metadata = runtime.rewrite_pr_torque_task_refs_metadata
    _run_direct_worktree_merge = runtime.run_direct_worktree_merge
    _run_pr_worktree_merge = runtime.run_pr_worktree_merge
    _safe_remove_worktree_result = runtime.safe_remove_worktree_result
    _save_task_record = runtime.save_task_record
    _scope_domain_for_cell = runtime.scope_domain_for_cell
    _send_agent_prompt = runtime.send_agent_prompt
    _shared_review_checkpoint_block_reason = runtime.shared_review_checkpoint_block_reason
    _stale_base_check_merge_result = runtime.stale_base_check_merge_result
    _stale_base_force_enabled = runtime.stale_base_force_enabled
    _stale_base_post_rebase_evidence_required = runtime.stale_base_post_rebase_evidence_required
    _stale_base_warning = runtime.stale_base_warning
    _target_branch_from_payload = runtime.target_branch_from_payload
    _target_has_driverless_payload = runtime.target_has_driverless_payload
    _untracked_overwrite_message = runtime.untracked_overwrite_message
    _workflow_breach_active_task_for_worker = runtime.workflow_breach_active_task_for_worker
    _worktree_full_diff = runtime.worktree_full_diff
    _worktree_merge_error = runtime.worktree_merge_error
    _worktree_merge_requested_cleanup = runtime.worktree_merge_requested_cleanup
    _worktree_submodules_for_cell = runtime.worktree_submodules_for_cell
    advance_latest_boundary_after_mechanical_commit = runtime.advance_latest_boundary_after_mechanical_commit
    board_sync_manager = runtime.board_sync_manager
    bridge = runtime.bridge
    get_adapter = runtime.get_adapter
    handle_command = runtime.handle_command
    mcp_entrypoint_for_cell = runtime.mcp_entrypoint_for_cell
    refresh_latest_boundary_after_rebase = runtime.refresh_latest_boundary_after_rebase
    runtime_env_vars_for_cell = runtime.runtime_env_vars_for_cell
    state = runtime.state
    worktree_mgr = runtime.worktree_mgr
    cmd = str(data.get("cmd", "") or "").strip()
    result = None

    if cmd == "worktree_create":
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
    elif cmd == "worktree_advance_boundary":
        target, live_cell, error_result = await _resolve_worktree_command_target_value(
            state=state,
            worktree_mgr=worktree_mgr,
            data=data,
            require_base=True,
            group=str(data.get("group", "") or ""),
        )
        if error_result:
            result = {
                "type": "worktree_advance_boundary",
                "ok": False,
                "error": error_result.get("message", "Target resolution failed"),
            }
        else:
            previous_head = str(data.get("expected_previous_head", "") or "").strip()
            expected_new = str(data.get("expected_new_head", "") or "").strip()
            if not expected_new:
                expected_new = await worktree_mgr.current_head(target) or ""
            note = str(data.get("verification_note", "") or "").strip()
            submodules = _configured_worktree_submodules_for_cell(state, target)
            machine = await worktree_mgr.verify_mechanical_gitlink_commit(
                target,
                previous_head=previous_head,
                new_head=expected_new,
                worktree_submodules=submodules,
            )
            updated_task, advance_result = advance_latest_boundary_after_mechanical_commit(
                state.board_tasks.values(),
                repo_root=target.worktree_repo_root,
                branch=target.worktree_branch,
                expected_previous_head=previous_head,
                new_head=expected_new,
                machine_verification=machine,
                actor_agent_id=str(data.get("actor_agent_id", "") or target.source_agent_id or ""),
                reason=str(data.get("reason", "") or "verified_mechanical_gitlink"),
                verification_note=note,
            )
            if updated_task:
                updated_task.messages.append({
                    "timestamp": time.time(),
                    "action": "worktree_boundary_advanced",
                    "message": "Advanced boundary to branch tip after verified mechanical gitlink-only commit.",
                    "agent_name": "torque",
                })
                _save_task_record(updated_task)
            result = {
                "type": "worktree_advance_boundary",
                **advance_result,
                "machine_verification": machine,
            }
            if not advance_result.get("ok"):
                result["error"] = advance_result.get("reason", "advance_refused")
    elif cmd == "worktree_adopt":
        adopt_agent_id = str(
            data.get("adopt_agent_id", "") or data.get("id", "") or ""
        ).strip()
        cell = state.agents.get(adopt_agent_id)
        if not cell:
            result = {"type": "error", "message": "Agent not found"}
        elif state.agent_is_tombstoned(cell):
            result = {"type": "error", "message": "Agent is tombstoned"}
        else:
            status = str(getattr(cell, "status", "") or "").strip().lower()
            if status not in {"", "stopped", "idle", "error"}:
                result = {
                    "type": "error",
                    "message": f"Agent is not adoptable while status={cell.status}",
                }
            else:
                target, _live, error_result = await _resolve_worktree_command_target_value(
                    state=state,
                    worktree_mgr=worktree_mgr,
                    data=data,
                    require_base=True,
                    reject_active_owner=True,
                    group=str(data.get("group", "") or cell.group or ""),
                )
                if error_result:
                    result = error_result
                else:
                    cell.worktree_path = target.worktree_path
                    cell.worktree_branch = target.worktree_branch
                    cell.worktree_repo_root = target.worktree_repo_root
                    cell.git_root = target.worktree_repo_root
                    cell.worktree_base_branch = target.worktree_base_branch
                    cell.directory = target.worktree_path
                    cell.worktree_dirty = bool(target.worktree_dirty)
                    cell.worktree_diff = {}
                    cell.worktree_changed_files = []
                    cell.worktree_checkpoints = await worktree_mgr.count_commits(cell)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)
                    state.history_update_agent(
                        cell,
                        worktree_branch=cell.worktree_branch,
                    )
                    result = {
                        "type": "worktree_adopt",
                        "ok": True,
                        "id": cell.id,
                        "agent_id": cell.id,
                        "worktree_path": cell.worktree_path,
                        "branch": cell.worktree_branch,
                        "base_branch": cell.worktree_base_branch,
                    }
                    if data.get("relaunch") and cell.cell_type == "agent":
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
    elif cmd == "worktree_remove":
        if _target_has_driverless_payload(data):
            target, _cell, error_result = await _resolve_worktree_command_target_value(
                state=state,
                worktree_mgr=worktree_mgr,
                data=data,
                require_base=True,
                reject_active_owner=True,
                group=str(data.get("group", "") or ""),
            )
            if error_result:
                result = error_result
            else:
                submodules = _configured_worktree_submodules_for_cell(
                    state,
                    target,
                )
                existing = ExistingWorktreeTarget(
                    repo_root=target.worktree_repo_root,
                    worktree_path=target.worktree_path,
                    branch=target.worktree_branch,
                    head_sha=await worktree_mgr.rev_parse(
                        target.worktree_path, "HEAD"
                    ) or "",
                    base_branch=target.worktree_base_branch,
                    git_root=target.git_root or target.worktree_repo_root,
                    is_dirty=False,
                    listed_worktree_entry={},
                )
                remove_result = await worktree_mgr.safe_remove_existing_worktree(
                    existing,
                    delete_branch=bool(data.get("delete_branch", True)),
                    worktree_submodules=submodules,
                )
                result = {
                    "type": "worktree_remove",
                    "id": target.id,
                    "driverless": True,
                    **remove_result,
                }
                if not remove_result.get("worktree_removed"):
                    result = {
                        "type": "error",
                        "message": (
                            remove_result.get("message")
                            or "Worktree removal failed"
                        ),
                        "id": target.id,
                        "worktree_remove": remove_result,
                    }
        else:
            cell = state.agents.get(data.get("id", ""))
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
        target, live_cell, error_result = await _resolve_worktree_command_target_value(
            state=state,
            worktree_mgr=worktree_mgr,
            data=data,
            require_base=True,
            reject_active_owner=_target_has_driverless_payload(data),
            group=str(data.get("group", "") or ""),
        )
        if error_result:
            result = {
                "type": "worktree_check_merge",
                "id": data.get("id", "") or _target_branch_from_payload(data),
                "error": error_result.get("message", "No worktree"),
            }
            return result
        cell = target
        aid = target.id
        if cell and cell.worktree_path \
                and cell.worktree_branch:
            boundary_state = await _latest_boundary_state_for_cell(
                cell
            )
            submodules = _worktree_submodules_for_cell(cell)
            boundary_blocks = bool(
                boundary_state.get("latest")
                and not boundary_state.get("clean")
            )
            boundary_allowed = False
            boundary_error = ""
            if boundary_blocks:
                boundary_reason = boundary_state.get("reason", "")
                boundary_error = await _boundary_gate_message(
                    worktree_mgr,
                    cell,
                    boundary_reason,
                    boundary_state.get("latest"),
                    _boundary_reason_message,
                    state=state,
                )
                boundary_allowed = bool(
                    boundary_reason == "branch_tip_moved"
                    and _boundary_mismatch_check_allowed(data)
                )
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
            elif boundary_blocks and not boundary_allowed:
                result = {
                    "type": "worktree_check_merge",
                    "id": aid,
                    "clean": False,
                    "dirty": False,
                    "conflicts": [],
                    "boundary": boundary_state.get("latest"),
                    "clean_boundary": None,
                    "error": boundary_error,
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
                if boundary_allowed:
                    check["boundary_mismatch_override"] = True
                _attach_stale_base(check, stale_base)
                if check.get("clean"):
                    squash = cell.worktree_merge_squash
                    check["default_message"] = \
                        await _generate_merge_message(
                            cell, worktree_mgr, squash,
                            state=state)
                    if getattr(cell, "driverless", False):
                        check["driverless"] = True
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
                            await refresh_latest_boundary_after_rebase(
                                state.board_tasks.values(),
                                repo_root=(
                                    cell.worktree_repo_root
                                    or cell.git_root
                                    or ""
                                ),
                                branch=cell.worktree_branch or "",
                                worktree_path=cell.worktree_path or "",
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
                    base_head_sha = str(
                        (stale_base_before_rebase or {}).get(
                            "base_head", ""
                        ) or ""
                    ).strip()
                    repo_root_for_evidence = (
                        cell.worktree_repo_root
                        or cell.git_root
                        or ""
                    )
                    rev_parse = getattr(worktree_mgr, "rev_parse", None)
                    if callable(rev_parse) and repo_root_for_evidence:
                        try:
                            base_head_sha = (
                                await rev_parse(
                                    repo_root_for_evidence,
                                    cell.worktree_base_branch or "",
                                )
                                or base_head_sha
                            )
                        except Exception:
                            log.debug(
                                "post-rebase base head evidence failed "
                                "for '%s'",
                                cell.name,
                                exc_info=True,
                            )
                    post_rebase_evidence = (
                        _stale_base_post_rebase_evidence_required(
                            stale_base_before_rebase,
                            post_rebase_head_sha=rebased_head_sha,
                            base_head_sha=base_head_sha,
                            review_boundary_updated=bool(
                                refreshed_boundary
                            ),
                            review_boundary_task_id=(
                                getattr(refreshed_boundary, "id", "")
                                if refreshed_boundary else ""
                            ),
                        )
                    )
                    result = {
                        "type": "worktree_rebase",
                        "id": aid,
                        "ok": True,
                        "post_rebase_head_sha": rebased_head_sha,
                        "base_head_sha": base_head_sha,
                        "review_boundary_updated": bool(
                            refreshed_boundary
                        ),
                        "post_rebase_evidence": post_rebase_evidence,
                    }
                    if refreshed_boundary:
                        result["review_boundary_task_id"] = (
                            getattr(refreshed_boundary, "id", "")
                            or ""
                        )
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
        target, live_cell, error_result = await _resolve_worktree_command_target_value(
            state=state,
            worktree_mgr=worktree_mgr,
            data=data,
            require_base=True,
            reject_active_owner=_target_has_driverless_payload(data),
            group=str(data.get("group", "") or ""),
        )
        cell = target
        if error_result or not cell or not cell.worktree_path:
            result = {
                "type": "worktree_pr",
                "error": (error_result or {}).get("message", "Agent has no worktree."),
            }
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
            group_settings = state.get_group_settings(
                getattr(cell, "group", "") or ""
            )
            github_group_settings = getattr(
                group_settings,
                "board_sync_github",
                {},
            ) or {}
            if not isinstance(github_group_settings, dict):
                github_group_settings = {}
            rewrite = _rewrite_pr_torque_task_refs_metadata(
                title,
                body,
                state=state,
                base_repo=str(
                    github_group_settings.get("github_repo", "")
                    or ""
                ).strip(),
            )
            title = rewrite["title"]
            body = rewrite["body"]
            _log_pr_task_ref_rewrite("worktree_create_pr", rewrite)
            submodules = _configured_worktree_submodules_for_cell(
                state,
                cell,
            )
            pr_result = (
                await worktree_mgr.create_pr(
                    cell,
                    title=title,
                    body=body,
                    worktree_submodules=submodules,
                )
                if submodules
                else await worktree_mgr.create_pr(
                    cell,
                    title=title,
                    body=body,
                )
            )
            if "error" in pr_result:
                result = {"type": "worktree_pr",
                          "error": pr_result["error"]}
            elif pr_result.get("pending_ee_pr"):
                result = {
                    "type": "worktree_pr",
                    "url": pr_result.get("url", ""),
                    "message": pr_result.get(
                        "message",
                        "Nested submodule PR created/reused; parent PR "
                        "will be created after it merges.",
                    ),
                    "pending": True,
                    "pending_ee_pr": True,
                    "nested_submodules": pr_result,
                }
                if getattr(cell, "driverless", False):
                    result["driverless"] = True
            else:
                msg = ("PR already exists"
                       if pr_result.get("existing")
                       else "PR created")
                result = {"type": "worktree_pr",
                          "url": pr_result["url"],
                          "message": msg}
                if getattr(cell, "driverless", False):
                    result["driverless"] = True
    elif cmd == "worktree_merge":
        target, live_cell, error_result = await _resolve_worktree_command_target_value(
            state=state,
            worktree_mgr=worktree_mgr,
            data=data,
            require_base=True,
            reject_active_owner=_target_has_driverless_payload(data),
            group=str(data.get("group", "") or ""),
        )
        cell = target
        aid = getattr(target, "id", "") if target else data.get("id", "")
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

        async def _emit_worktree_merge_progress(
                phase: str,
                message: str,
                **extra) -> None:
            state._emit(
                "worktree_merge_progress",
                id=aid,
                phase=phase,
                message=message,
                **extra,
            )
            await state.broadcast()

        if error_result:
            failure = {
                "phase": "target_resolution",
                "error": error_result.get("message", "No worktree"),
            }
            known_cell = state.agents.get(
                str(data.get("id", "") or "").strip()
            )
            result = await _recover_authoritative_post_success_from_boundary(
                state=state,
                worktree_mgr=worktree_mgr,
                aid=aid,
                failure=failure,
                cell=known_cell,
                worktree_path=str(
                    getattr(known_cell, "worktree_path", "") or ""
                ),
                repo_root=str(
                    getattr(known_cell, "worktree_repo_root", "")
                    or getattr(known_cell, "git_root", "")
                    or ""
                ),
                branch=str(
                    getattr(known_cell, "worktree_branch", "") or ""
                ),
                base_branch=str(
                    getattr(known_cell, "worktree_base_branch", "") or ""
                ),
            )
            if not result:
                result = _worktree_merge_error(
                    aid,
                    error_result.get("message", "No worktree"),
                    phase="target_resolution",
                )
        elif getattr(cell, "driverless", False) and _worktree_merge_requested_cleanup(
                state,
                cell,
                data,
                preserve_merge_diff=False,
        ).get("close_agent_on_merge"):
            result = _worktree_merge_error(
                aid,
                "close_agent_on_merge/auto_sweep cleanup is not supported in driverless mode",
                phase="driverless_cleanup",
                driverless=True,
            )
        elif merge_mode == "pr" and requested_force_direct:
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
                await _emit_worktree_merge_progress(
                    "direct_merge",
                    "Merging locally\u2026",
                )
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
                    progress=_emit_worktree_merge_progress,
                )

    return result
