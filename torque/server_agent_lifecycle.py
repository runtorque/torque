"""Dismiss, rehire, delete, relaunch, and restart agent lifecycle commands."""

from __future__ import annotations

import json
import os
import time

from .adapters import get_adapter
from .config import log
from .server_agent import (
    _new_agent_prompt_sequence,
    _startup_prompt_for_new_agent,
    mcp_entrypoint_for_cell,
    resolve_default_boot_nudge,
    runtime_env_vars_for_cell,
)
from .server_agent_common import _should_show_guidance_hint
from .server_agent_operations import (
    GUIDANCE_HINT_IDENTITY_LAUNCH,
    _agent_dismissed_at,
    _architect_dismissed_error,
    _close_cell_session_preserving_state,
    _dismissal_close_cells,
    _engineer_name_exists,
    _launch_resolver_for_cell,
    _relaunch_command_base,
    _resolve_architect_cell,
    _resolve_engineer_cell,
    _validate_architect_lifecycle_authority,
    _validate_engineer_lifecycle_authority,
)
from .server_command_reads import _architect_ui_tool_is_read
from .server_communication import _replay_buffered_cross_kind_messages
from .server_dispatch import _find_active_worktree_owner
from .state import MatrixState


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
    overlay_cleanup = {
        "cancelled_proposals": state.cancel_behavior_overlay_proposals_for_agent(
            engineer.id,
            reason="engineer dismissed",
            actor_kind="system",
        )
    }

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
        "behavior_overlay_cleanup": overlay_cleanup,
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

    overlay_cleanup = state.cleanup_behavior_overlay_for_agent_delete(
        engineer.id,
        reason="engineer deleted",
    )

    tombstoned = await close_agent_session_only(engineer)
    del tombstoned
    result = {
        "transferred_agents": transferred_agents,
        "transferred_tasks": transferred_tasks,
    }
    if (
            overlay_cleanup.get("cancelled_proposals")
            or overlay_cleanup.get("active_cleared")):
        result["behavior_overlay_cleanup"] = overlay_cleanup
    return result


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

    hired_engineer_ids = []
    transferred_engineers = 0
    for cell in list(state.iter_active_agents()):
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            continue
        if str(getattr(cell, "hired_by_architect_id", "") or "").strip() != architect.id:
            continue
        hired_engineer_ids.append(cell.id)
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

    overlay_cleanup = state.cleanup_behavior_overlay_for_architect_delete(
        architect.id,
        hired_engineer_ids=hired_engineer_ids,
        reason="architect deleted",
    )

    tombstoned = await close_agent_session_only(architect)
    del tombstoned
    result = {
        "transferred_engineers": transferred_engineers,
        "archived_decisions": archived_decisions,
    }
    if (
            overlay_cleanup.get("cancelled_proposals")
            or overlay_cleanup.get("active_cleared")):
        result["behavior_overlay_cleanup"] = overlay_cleanup
    return result







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
    cell.fast_mode = str(launch_cfg.get("fast_mode", "inherit") or "inherit")
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
    state.apply_effective_agent_class_for_launch(
        cell,
        base_dir=cell.directory or base_dir,
    )
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
        if cell.agent_type and prev_directory and prev_directory != cell.directory:
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
        sdk_system_prompt=launch_cfg.get("sdk_system_prompt", ""),
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
        send_agent_prompt,
        clear_digest_backlog_for_restart=None) -> dict | None:
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
    if clear_digest_backlog_for_restart:
        try:
            cleared = clear_digest_backlog_for_restart(cell.id)
            if cleared:
                log.info(
                    "Cleared %d queued digest event(s) before restarting '%s'",
                    cleared,
                    cell.name,
                )
        except Exception:
            log.exception(
                "Failed to clear queued digest backlog for '%s' during restart",
                cell.name,
            )

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
    cell.fast_mode = str(launch_cfg.get("fast_mode", "inherit") or "inherit")
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
    state.apply_effective_agent_class_for_launch(
        cell,
        base_dir=cell.directory or base_dir,
    )
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

    persistent_prompt_text = build_cell_persistent_prompt(cell, launch_cfg)
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
        sdk_system_prompt=launch_cfg.get("sdk_system_prompt", ""),
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
