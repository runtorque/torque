"""Task-to-agent dispatch command orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import log

AUTO_CLOSE_SPAWNED_LABEL = "torque:auto-close-spawned-agent"
GUIDANCE_HINT_IDENTITY_DISPATCH = "agent_identity_anchor.dispatch"
GUIDANCE_HINT_IDENTITY_LAUNCH = "agent_identity_anchor.launch"


@dataclass(slots=True)
class TaskDispatchRuntime:
    agent_can_receive_dispatch: Any
    agent_dismissed_at: Any
    append_task_artifacts: Any
    apply_agent_class_launch_selection: Any
    assemble_worker_prompt: Any
    behavior_overlay_prompt_block_for_cell: Any
    build_dispatch_persistent_prompt: Any
    build_postscript: Any
    build_self_dispatch_prompt: Any
    build_torque_context: Any
    copy_worktree_context: Any
    create_agent_with_config: Any
    create_child_terminals: Any
    engineer_dismissed_error: Any
    engineer_tombstoned_error: Any
    find_active_worktree_owner: Any
    new_agent_prompt_sequence: Any
    owner_is_user_from_ids: Any
    panel_event: Any
    promote_suggested_action: Any
    record_task_dispatch: Any
    resolve_base_dir: Any
    resolve_inherited_worktree_source: Any
    resolve_task_id: Any
    resolve_worker_launch_config: Any
    resolve_worktree_command_target_value: Any
    sanitize_engineer_worker_provider_override: Any
    send_agent_prompt: Any
    should_handoff_shared_worktree: Any
    should_queue_existing_agent_dispatch: Any
    should_show_guidance_hint: Any
    startup_prompt_for_new_agent: Any
    worker_provider_override_from_dispatch: Any
    action_mgr: Any
    build_prompt_memory_block: Any
    engineer_architect_task_routing_denied_message: Any
    is_architect_execution_target: Any
    normalize_default_worker_concurrency: Any
    prepend_agent_identity_anchor: Any
    state: Any
    task_counts_as_done: Any
    template_mgr: Any
    worktree_mgr: Any


async def handle_dispatch_task_command(
    data: dict,
    runtime: TaskDispatchRuntime,
) -> dict | None:
    _agent_can_receive_dispatch = runtime.agent_can_receive_dispatch
    _agent_dismissed_at = runtime.agent_dismissed_at
    _append_task_artifacts = runtime.append_task_artifacts
    _apply_agent_class_launch_selection = runtime.apply_agent_class_launch_selection
    _assemble_worker_prompt = runtime.assemble_worker_prompt
    _behavior_overlay_prompt_block_for_cell = runtime.behavior_overlay_prompt_block_for_cell
    _build_dispatch_persistent_prompt = runtime.build_dispatch_persistent_prompt
    _build_postscript = runtime.build_postscript
    _build_self_dispatch_prompt = runtime.build_self_dispatch_prompt
    _build_torque_context = runtime.build_torque_context
    _copy_worktree_context = runtime.copy_worktree_context
    _create_agent_with_config = runtime.create_agent_with_config
    _create_child_terminals = runtime.create_child_terminals
    _engineer_dismissed_error = runtime.engineer_dismissed_error
    _engineer_tombstoned_error = runtime.engineer_tombstoned_error
    _find_active_worktree_owner = runtime.find_active_worktree_owner
    _new_agent_prompt_sequence = runtime.new_agent_prompt_sequence
    _owner_is_user_from_ids = runtime.owner_is_user_from_ids
    _panel_event = runtime.panel_event
    _promote_suggested_action = runtime.promote_suggested_action
    _record_task_dispatch = runtime.record_task_dispatch
    _resolve_base_dir = runtime.resolve_base_dir
    _resolve_inherited_worktree_source = runtime.resolve_inherited_worktree_source
    _resolve_task_id = runtime.resolve_task_id
    _resolve_worker_launch_config = runtime.resolve_worker_launch_config
    _resolve_worktree_command_target_value = runtime.resolve_worktree_command_target_value
    _sanitize_engineer_worker_provider_override = runtime.sanitize_engineer_worker_provider_override
    _send_agent_prompt = runtime.send_agent_prompt
    _should_handoff_shared_worktree = runtime.should_handoff_shared_worktree
    _should_queue_existing_agent_dispatch = runtime.should_queue_existing_agent_dispatch
    _should_show_guidance_hint = runtime.should_show_guidance_hint
    _startup_prompt_for_new_agent = runtime.startup_prompt_for_new_agent
    _worker_provider_override_from_dispatch = runtime.worker_provider_override_from_dispatch
    action_mgr = runtime.action_mgr
    build_prompt_memory_block = runtime.build_prompt_memory_block
    engineer_architect_task_routing_denied_message = runtime.engineer_architect_task_routing_denied_message
    is_architect_execution_target = runtime.is_architect_execution_target
    normalize_default_worker_concurrency = runtime.normalize_default_worker_concurrency
    prepend_agent_identity_anchor = runtime.prepend_agent_identity_anchor
    state = runtime.state
    task_counts_as_done = runtime.task_counts_as_done
    template_mgr = runtime.template_mgr
    worktree_mgr = runtime.worktree_mgr
    result = None
    inherit_worktree_from = str(
        data.get("inherit_worktree_from", "") or ""
    ).strip()

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
                    and data.get("_engineer_dispatch_id")
                    and is_architect_execution_target(target_cell)
                ):
                    message = (
                        engineer_architect_task_routing_denied_message(
                            target_cell
                        )
                    )
                    result = {
                        "type": "error",
                        "message": message,
                        "task_id": tid,
                        "agent_id": agent_id,
                    }
                    _panel_event(
                        "task_dispatch_denied",
                        target_cell.id,
                        target_cell.name,
                        target_cell.group,
                        message,
                        task_id=tid,
                    )
                    log.warning(
                        "Denied engineer-originated task dispatch "
                        "to architect target: engineer=%s task=%s "
                        "target=%s",
                        data.get("_engineer_dispatch_id", ""),
                        tid,
                        agent_id,
                    )
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
                class_launch = _apply_agent_class_launch_selection(
                    data,
                    launch_cfg,
                    base_kind="worker",
                    base_dir=base_dir,
                )
                if not class_launch.get("ok"):
                    result = class_launch["error"]
                    cell = None
                adopt_path = str(data.get("adopt_worktree_path", "") or "").strip()
                if adopt_path and not result:
                    adopt_payload = {
                        "worktree_path": adopt_path,
                        "branch": str(data.get("adopt_branch", "") or "").strip(),
                        "repo_root": str(data.get("adopt_repo_root", "") or "").strip(),
                        "base_branch": str(data.get("adopt_base_branch", "") or "").strip(),
                        "group": group,
                    }
                    adopt_target, _adopt_cell, adopt_error = (
                        await _resolve_worktree_command_target_value(
                            state=state,
                            worktree_mgr=worktree_mgr,
                            data=adopt_payload,
                            require_base=True,
                            reject_active_owner=True,
                            group=group,
                        )
                    )
                    if adopt_error:
                        result = adopt_error
                        cell = None
                    else:
                        launch_cfg["adopted_worktree"] = {
                            "worktree_path": adopt_target.worktree_path,
                            "branch": adopt_target.worktree_branch,
                            "repo_root": adopt_target.worktree_repo_root,
                            "base_branch": adopt_target.worktree_base_branch,
                        }
                        launch_cfg["worktree"] = False
                        launch_cfg["directory"] = adopt_target.worktree_path
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
                if result:
                    cell = None
                else:
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
                    if (
                            data.get("create_agent")
                            and not launch_cfg.get("adopted_worktree")
                            and task.action_name):
                        try:
                            auto_close_spawn = action_mgr.get_auto_close_on_done(
                                task.action_name,
                                base_dir=base_dir,
                            )
                        except Exception:
                            auto_close_spawn = False
                        if auto_close_spawn:
                            labels = list(task.labels or [])
                            if AUTO_CLOSE_SPAWNED_LABEL not in labels:
                                labels.append(AUTO_CLOSE_SPAWNED_LABEL)
                                task.labels = labels
                                state._db_save_task(task)
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

            # Reused reviewers are existing agents and therefore do not pass
            # through the new-agent inheritance branch.  Apply an explicit
            # worktree handoff before prompting them, so their eventual task
            # boundary is recorded from the worktree they reviewed rather
            # than a predecessor worktree they happened to retain.
            if cell and not result and inherit_worktree_from:
                inherited_worktree_source = (
                    _resolve_inherited_worktree_source(
                        state,
                        task,
                        inherit_worktree_from,
                    )
                )
                if (
                        inherited_worktree_source
                        and inherited_worktree_source is not cell
                        and _copy_worktree_context(
                            cell,
                            inherited_worktree_source,
                        )
                ):
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

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
                            behavior_overlay_block=
                            _behavior_overlay_prompt_block_for_cell(
                                state,
                                cell=cell,
                                include_agent=False,
                                worker_dispatch=True,
                            ),
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

    return result
