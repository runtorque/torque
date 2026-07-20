"""Agent, role-lifecycle, session, and group-layout command orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .behavior_overlays import (
    BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES,
    _BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY,
)
from ..server_agent import resolve_default_boot_nudge


log = logging.getLogger(__name__)

GUIDANCE_HINT_IDENTITY_LAUNCH = "agent_identity_anchor.launch"

AGENT_OPERATION_COMMAND_NAMES = frozenset({
    "rename_group",
    "add_engineer", "add_architect", "add_worker",
    "agent_class_launch", "create_agent_from_class",
    "architect_engineer_hire", "architect_engineer_set_specializations",
    "pending_hire_approve", "pending_hire_reject", "pending_hire_list",
    "engineer_dismiss", "architect_engineer_dismiss",
    "engineer_rehire", "architect_engineer_rehire",
    "architect_dismiss", "architect_rehire",
    "delete_engineer", "delete_architect",
    "restore_agent", "architect_engineer_restore",
    "purge_agent_now", "recently_deleted_agents", "rename_engineer",
    "architect_decision_create", "architect_decision_update",
    "architect_decision_link", "architect_decision_list",
    "architect_peer_inbox", "architect_peer_list",
    "architect_peer_message", "architect_task_update",
    "add_agent", "add_terminal", "remove_agent", "update_agent",
    "focus_agent", "send_text", "send_user_message",
    "user_agent_message", "relaunch_agent", "restart_agent",
    "move_group", "move_agent", "reparent_terminal", "reorder_child",
    "clear_agent_context",
}) | BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES


@dataclass(slots=True)
class AgentOperationRuntime:
    apply_persistent_prompt: Any
    build_cell_persistent_prompt: Any
    cleanup_purged_agents: Any
    close_agent_session_only: Any
    create_agent_with_config: Any
    create_child_terminals: Any
    dispatch_architect_ui_tool: Any
    handle_add_architect_command: Any
    handle_add_engineer_command: Any
    handle_add_worker_command: Any
    handle_agent_class_launch_command: Any
    handle_architect_dismiss_command: Any
    handle_architect_engineer_hire_command: Any
    handle_architect_rehire_command: Any
    handle_delete_architect_command: Any
    handle_delete_engineer_command: Any
    handle_engineer_dismiss_command: Any
    handle_engineer_rehire_command: Any
    handle_pending_hire_approve_command: Any
    handle_pending_hire_list_command: Any
    handle_pending_hire_reject_command: Any
    handle_purge_agent_now_command: Any
    handle_recently_deleted_agents_command: Any
    handle_relaunch_agent_command: Any
    handle_remove_agent_command: Any
    handle_rename_engineer_command: Any
    handle_restart_agent_command: Any
    handle_restore_agent_command: Any
    handle_send_text_command: Any
    handle_send_user_message_command: Any
    handle_set_engineer_specializations_command: Any
    handle_user_agent_message_command: Any
    is_designated_engineer: Any
    new_agent_prompt_sequence: Any
    normalize_engineer_specialization_selection: Any
    normalize_ui_client_id: Any
    panel_event: Any
    persistent_prompt_filename: Any
    project_specialization_names: Any
    resolve_agent_launch_config: Any
    resolve_architect_cell: Any
    resolve_architect_launch_config: Any
    resolve_base_dir: Any
    resolve_engineer_launch_config: Any
    resolve_pending_engineer_specializations: Any
    resolve_worker_launch_config: Any
    restart_agent_session: Any
    send_agent_prompt: Any
    should_show_guidance_hint: Any
    startup_prompt_for_new_agent: Any
    suggest_template_agent_name: Any
    bridge: Any
    engineer_buffer: Any
    handle_command: Any
    specialization_mgr: Any
    state: Any
    worktree_mgr: Any


async def handle_agent_operation_command(
    data: dict, runtime: AgentOperationRuntime,
) -> dict | None:
    """Dispatch commands that own agent creation, lifecycle, and UI sessions."""
    cmd = str(data.get("cmd", "") or "").strip()
    result = None
    _apply_persistent_prompt = runtime.apply_persistent_prompt
    _build_cell_persistent_prompt = runtime.build_cell_persistent_prompt
    _cleanup_purged_agents = runtime.cleanup_purged_agents
    _close_agent_session_only = runtime.close_agent_session_only
    _create_agent_with_config = runtime.create_agent_with_config
    _create_child_terminals = runtime.create_child_terminals
    _dispatch_architect_ui_tool = runtime.dispatch_architect_ui_tool
    _handle_add_architect_command = runtime.handle_add_architect_command
    _handle_add_engineer_command = runtime.handle_add_engineer_command
    _handle_add_worker_command = runtime.handle_add_worker_command
    _handle_agent_class_launch_command = runtime.handle_agent_class_launch_command
    _handle_architect_dismiss_command = runtime.handle_architect_dismiss_command
    _handle_architect_engineer_hire_command = runtime.handle_architect_engineer_hire_command
    _handle_architect_rehire_command = runtime.handle_architect_rehire_command
    _handle_delete_architect_command = runtime.handle_delete_architect_command
    _handle_delete_engineer_command = runtime.handle_delete_engineer_command
    _handle_engineer_dismiss_command = runtime.handle_engineer_dismiss_command
    _handle_engineer_rehire_command = runtime.handle_engineer_rehire_command
    _handle_pending_hire_approve_command = runtime.handle_pending_hire_approve_command
    _handle_pending_hire_list_command = runtime.handle_pending_hire_list_command
    _handle_pending_hire_reject_command = runtime.handle_pending_hire_reject_command
    _handle_purge_agent_now_command = runtime.handle_purge_agent_now_command
    _handle_recently_deleted_agents_command = runtime.handle_recently_deleted_agents_command
    _handle_relaunch_agent_command = runtime.handle_relaunch_agent_command
    _handle_remove_agent_command = runtime.handle_remove_agent_command
    _handle_rename_engineer_command = runtime.handle_rename_engineer_command
    _handle_restart_agent_command = runtime.handle_restart_agent_command
    _handle_restore_agent_command = runtime.handle_restore_agent_command
    _handle_send_text_command = runtime.handle_send_text_command
    _handle_send_user_message_command = runtime.handle_send_user_message_command
    _handle_set_engineer_specializations_command = runtime.handle_set_engineer_specializations_command
    _handle_user_agent_message_command = runtime.handle_user_agent_message_command
    _is_designated_engineer = runtime.is_designated_engineer
    _new_agent_prompt_sequence = runtime.new_agent_prompt_sequence
    _normalize_engineer_specialization_selection = runtime.normalize_engineer_specialization_selection
    _normalize_ui_client_id = runtime.normalize_ui_client_id
    _panel_event = runtime.panel_event
    _persistent_prompt_filename = runtime.persistent_prompt_filename
    _project_specialization_names = runtime.project_specialization_names
    _resolve_agent_launch_config = runtime.resolve_agent_launch_config
    _resolve_architect_cell = runtime.resolve_architect_cell
    _resolve_architect_launch_config = runtime.resolve_architect_launch_config
    _resolve_base_dir = runtime.resolve_base_dir
    _resolve_engineer_launch_config = runtime.resolve_engineer_launch_config
    _resolve_pending_engineer_specializations = runtime.resolve_pending_engineer_specializations
    _resolve_worker_launch_config = runtime.resolve_worker_launch_config
    _restart_agent_session = runtime.restart_agent_session
    _send_agent_prompt = runtime.send_agent_prompt
    _should_show_guidance_hint = runtime.should_show_guidance_hint
    _startup_prompt_for_new_agent = runtime.startup_prompt_for_new_agent
    _suggest_template_agent_name = runtime.suggest_template_agent_name
    bridge = runtime.bridge
    engineer_buffer = runtime.engineer_buffer
    handle_command = runtime.handle_command
    specialization_mgr = runtime.specialization_mgr
    state = runtime.state
    worktree_mgr = runtime.worktree_mgr

    # Command branches are intentionally explicit: the manifest above is the
    # ownership boundary and prevents accidental prefix-based route capture.

    if cmd == "rename_group":
        state.rename_group(data["group"], data["new_name"])

    elif cmd == "add_engineer":
        result = await _handle_add_engineer_command(
            data,
            state,
            resolve_base_dir=_resolve_base_dir,
            resolve_engineer_launch_config=_resolve_engineer_launch_config,
            create_agent_with_config=_create_agent_with_config,
            specialization_mgr=specialization_mgr,
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

    elif cmd in {"agent_class_launch", "create_agent_from_class"}:
        result = await _handle_agent_class_launch_command(
            data,
            state,
            resolve_base_dir=_resolve_base_dir,
            resolve_agent_launch_config=_resolve_agent_launch_config,
            resolve_engineer_launch_config=_resolve_engineer_launch_config,
            resolve_architect_launch_config=_resolve_architect_launch_config,
            resolve_worker_launch_config=_resolve_worker_launch_config,
            create_agent_with_config=_create_agent_with_config,
            specialization_mgr=specialization_mgr,
            send_agent_prompt=_send_agent_prompt,
        )

    elif cmd == "architect_engineer_hire":
        result = await _handle_architect_engineer_hire_command(
            data,
            state,
            resolve_base_dir=_resolve_base_dir,
            specialization_mgr=specialization_mgr,
        )

    elif cmd == "architect_engineer_set_specializations":
        result = await _handle_set_engineer_specializations_command(
            data,
            state,
            resolve_base_dir=_resolve_base_dir,
            specialization_mgr=specialization_mgr,
            architect_id=str(data.get("architect_id", "") or ""),
        )

    elif cmd == "pending_hire_approve":
        result = await _handle_pending_hire_approve_command(
            data,
            state,
            resolve_base_dir=_resolve_base_dir,
            resolve_engineer_launch_config=_resolve_engineer_launch_config,
            create_agent_with_config=_create_agent_with_config,
            specialization_mgr=specialization_mgr,
            send_agent_prompt=_send_agent_prompt,
        )

    elif cmd == "pending_hire_reject":
        result = await _handle_pending_hire_reject_command(
            data,
            state,
        )

    elif cmd == "pending_hire_list":
        result = _handle_pending_hire_list_command(data, state)

    elif cmd in BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES:
        behavior_overlay_mutation_result = (
            await _BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.dispatch(
                cmd,
                data,
                state,
            )
        )
        result = behavior_overlay_mutation_result.value

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
        result = _handle_restore_agent_command(
            data, state,
            resolve_architect_cell=_resolve_architect_cell,
        )

    elif cmd == "purge_agent_now":
        result = await _handle_purge_agent_now_command(
            data,
            state,
            cleanup_purged_agents=_cleanup_purged_agents,
            resolve_architect_cell=_resolve_architect_cell,
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
        # ``add_agent`` is the legacy/general user creation route.  The
        # only ordinary agent kind is now ``worker`` (Architects and
        # Engineers have dedicated creation flows), so it must use the
        # worker resolver just like ``add_worker`` and task dispatch.  Using
        # the generic resolver here silently bypassed group worker defaults
        # such as ``worker_model``.

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
                if is_engineer else _resolve_worker_launch_config
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
                from ..engineer import build_engineer_system_prompt
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
                persistent_prompt_text=persistent_prompt_text,
                kind="engineer" if is_engineer else "worker")
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

                # Only explicit launch config terminals create
                # companion terminals. Legacy hidden
                # GroupSettings.auto_terminals is intentionally no
                # longer an implicit fallback for ordinary agent
                # creation.
                if launch_cfg.get("terminals"):
                    await _create_child_terminals(
                        group, cell,
                        terminals=launch_cfg["terminals"])
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
            delete_architect_command=_handle_delete_architect_command,
            delete_engineer_command=_handle_delete_engineer_command,
        )

    elif cmd == "update_agent":
        cell = state.agents.get(data["id"])
        if cell:
            old_name = cell.name
            new_name = data.get("name", cell.name)
            new_color = data.get("tab_color", cell.tab_color)
            update_fields = {
                "name": new_name,
                "tab_color": new_color,
            }
            if "icon" in data:
                update_fields["icon"] = data.get("icon", cell.icon)
            if "engineer_specializations" in data:
                if cell.kind != "engineer":
                    result = {
                        "type": "error",
                        "message": (
                            "engineer_specializations can only be "
                            "updated for engineers"
                        ),
                    }
                else:
                    try:
                        base_dir = await _resolve_base_dir(cell.group)
                        update_fields["engineer_specializations"] = (
                            _normalize_engineer_specialization_selection(
                                data.get("engineer_specializations"),
                                valid_names=_project_specialization_names(
                                    specialization_mgr, base_dir),
                            )
                        )
                    except ValueError as exc:
                        result = {
                            "type": "error",
                            "message": str(exc),
                        }
            if not result:
                # Edit popup save path: name + engineer specializations
                # are persisted in one update_agent round-trip.
                state.update_agent(data["id"], **update_fields)
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
            client_id = _normalize_ui_client_id(data.get("_client_id", ""))
            selected_id = cell.parent_id if (
                cell.cell_type == "terminal" and cell.parent_id
            ) else cell.id
            if selected_id and selected_id in state.agents and not client_id:
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
                await bridge.focus_session(
                    cell.session_id,
                    client_id=client_id,
                )

    elif cmd == "send_text":
        await _handle_send_text_command(data, state, _send_agent_prompt)

    elif cmd == "send_user_message":
        await _handle_send_user_message_command(data, state, bridge)

    elif cmd == "user_agent_message":
        result = await _handle_user_agent_message_command(
            data,
            state,
            _send_agent_prompt,
            restart_agent=_restart_agent_session,
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
            clear_digest_backlog_for_restart=(
                engineer_buffer.clear_digest_backlog_for_restart
            ),
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

    return result
