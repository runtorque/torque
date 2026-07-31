"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403

async def dispatch_inventory(ctx: ScopedDispatchContext):
    name = ctx.name
    args = ctx.args
    handle_command = ctx.handle_command
    state = ctx.state
    real_state = ctx.real_state
    tool_prefix = ctx.tool_prefix
    caller_kind = ctx.caller_kind
    caller_id = ctx.caller_id
    idempotency_key = ctx.idempotency_key
    _engineer_cell = ctx.caller_cell
    _engineer_group = ctx.caller_group
    tool_name = normalize_tool_name(name, tool_prefix)

    if tool_name == "session_map":
        return json.dumps(
            build_engineer_session_map(
                state,
                _engineer_group,
                engineer_cell=_engineer_cell,
            )
        ), False

    if tool_name == "streams_list":
        streams = _engineer_streams(
            state,
            _engineer_cell,
            _engineer_group,
            state_filter=str(args.get("state", "") or "").strip(),
            branch_filter=str(args.get("branch", "") or "").strip(),
            repo_root_filter=str(args.get("repo_root", "") or "").strip(),
            include_orphaned=bool(args.get("include_orphaned", False)),
        )
        return json.dumps({
            "group": _engineer_group,
            "count": len(streams),
            "streams": streams,
        }), False

    if tool_name == "stream_show":
        task_ident = str(args.get("task", "") or "").strip()
        task_id = ""
        if task_ident:
            task_id = _resolve_task(state, task_ident)
            if not task_id:
                return "Task not found", True
            task = state.board_tasks.get(task_id)
            if not task or task.group != _engineer_group:
                return "Task not found", True
        streams = _engineer_streams(
            state,
            _engineer_cell,
            _engineer_group,
            include_orphaned=True,
        )
        stream, error_text = _resolve_stream_payload(
            streams,
            stream_ident=str(args.get("stream", "") or "").strip(),
            repo_root=str(args.get("repo_root", "") or "").strip(),
            branch=str(args.get("branch", "") or "").strip(),
            task_id=task_id,
        )
        if error_text:
            return error_text, True
        return json.dumps(stream), False

    if tool_name == "board_list":
        lane_filter = args.get("lane", "")
        label_filter = args.get("label", "")
        health_filter = args.get("health", "")
        search = args.get("search", "").lower()

        lanes = {}
        for t in state.board_tasks.values():
            # Always scope to engineer's group
            if t.group != _engineer_group:
                continue
            if t.lane == ARCHIVED_LANE and lane_filter != ARCHIVED_LANE:
                continue
            if lane_filter and t.lane != lane_filter:
                continue
            if label_filter and label_filter not in (t.labels or []):
                continue
            health_state = getattr(t, "health_state", "healthy") or "healthy"
            if health_filter and health_state != health_filter:
                continue
            if search and search not in t.task.lower() \
                    and search not in (t.description or "").lower():
                continue
            lane_tasks = lanes.setdefault(t.lane, [])
            agent_name = ""
            agent_hidden = False
            if t.agent_id:
                agent_payload = _task_agent_payload_for_engineer(
                    state, _engineer_cell, t.agent_id
                )
                agent_name = agent_payload.get("agent_name", "")
                agent_hidden = bool(agent_payload.get("agent_hidden"))
            item = {
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "group": t.group,
                "labels": t.labels or [],
                "action": t.action_name,
                "agent": agent_name,
                "status": t.status,
                "dispatch_state": (
                    getattr(t, "dispatch_state", "queued") or "queued"
                ),
                "health_state": health_state,
                "verification_state": getattr(
                    t, "verification_state", ""
                ) or "",
                "verification_mode": getattr(
                    t, "verification_mode", ""
                ) or "",
                "provider": t.provider,
                "external_id": t.external_id,
                "external_url": t.external_url,
                "health_since": getattr(t, "health_since", ""),
                "parent_task_id": t.parent_task_id,
            }
            if caller_kind == "architect":
                item["created_by"] = _task_created_by_classifier(t)
            if agent_hidden:
                item["agent_hidden"] = True
            _attach_task_board_sync_inline_state(item, t)
            _attach_task_review_inline_state(item, t)
            lane_tasks.append(item)

        # Order lanes by board_lanes order
        ordered = {}
        for lane_name in state.board_lanes:
            if lane_name == ARCHIVED_LANE and lane_filter != ARCHIVED_LANE:
                continue
            if lane_name in lanes:
                ordered[lane_name] = lanes[lane_name]
        # Include any lanes not in board_lanes (shouldn't happen, but safe)
        for lane_name, tasks in lanes.items():
            if lane_name not in ordered:
                ordered[lane_name] = tasks

        return json.dumps({"lanes": ordered}), False

    if tool_name == "task_show":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task or task.group != _engineer_group:
            return "Task not found", True
        d = serialize_task_for_mcp(task, tasks_by_id=state.board_tasks)
        d.update(_task_health_payload_for_response(state, task))
        d["title"] = task.task
        d["action"] = task.action_name
        board_sync = _task_board_sync_inline_state(task)
        if board_sync:
            d["board_sync"] = board_sync
        _attach_task_review_inline_state(d, task)
        awareness_block = build_engineer_deliverable_awareness(task)
        if awareness_block:
            d["deliverable_awareness"] = awareness_block
        if caller_kind == "architect":
            d["created_by"] = _task_created_by_classifier(task)
        if task.agent_id and not _agent_visible_to_engineer(
                state, _engineer_cell, task.agent_id):
            d["agent_id"] = ""
            if state.engineer_restricts_to_created_agents(_engineer_group):
                d["agent_hidden"] = True
        # Include recent messages (last 10 only)
        if task.messages:
            d["messages"] = task.messages[-10:]
        # Enrich with agent info
        if task.agent_id:
            d.update(
                _task_agent_payload_for_engineer(
                    state, _engineer_cell, task.agent_id
                )
            )
        # Auto-include pipeline chain for pipeline tasks
        if task.pipeline_root_id or task.parent_task_id:
            chain = state.board_get_chain(tid)
            d["pipeline_chain"] = []
            for ct in chain:
                if ct.group != _engineer_group:
                    continue
                agent_slug = ""
                agent_hidden = False
                if ct.agent_id:
                    agent_payload = _task_agent_payload_for_engineer(
                        state, _engineer_cell, ct.agent_id
                    )
                    agent_slug = agent_payload.get("agent_name", "")
                    agent_hidden = bool(agent_payload.get("agent_hidden"))
                item = {
                    "id": ct.id,
                    "title": ct.task,
                    "lane": ct.lane,
                    "status": ct.status,
                    "dispatch_state": (
                        getattr(ct, "dispatch_state", "queued") or "queued"
                    ),
                    "health_state": getattr(ct, "health_state", "healthy"),
                    "verification_state": getattr(
                        ct, "verification_state", ""
                    ) or "",
                    "depth": ct.pipeline_depth,
                    "agent": agent_slug,
                }
                if caller_kind == "architect":
                    item["created_by"] = _task_created_by_classifier(ct)
                if agent_hidden:
                    item["agent_hidden"] = True
                _attach_task_board_sync_inline_state(item, ct)
                _attach_task_review_inline_state(item, ct)
                d["pipeline_chain"].append(item)
        return json.dumps(d), False

    if tool_name == "task_list" and caller_kind == "architect":
        label_filter, label_error = _normalize_architect_task_list_label_filter(
            args.get("label_filter", "")
        )
        if label_error:
            return label_error, True
        limit, limit_error = _normalize_architect_task_list_limit(
            args.get("limit", None)
        )
        if limit_error:
            return limit_error, True
        archived, archived_error = _optional_bool_arg(args, "archived", False)
        if archived_error:
            return archived_error, True
        include_engineer_messages, include_messages_error = _optional_bool_arg(
            args,
            "include_engineer_messages",
            False,
        )
        if include_messages_error:
            return include_messages_error, True

        lane_filter = str(args.get("lane_filter", "") or "").strip()
        assigned_engineer_filter = str(
            args.get("assigned_engineer_id_filter", "") or ""
        ).strip()
        creator_filter = str(args.get("creator_filter", "") or "").strip()
        creator_filter_error = _validate_architect_task_creator_filter(
            creator_filter,
        )
        if creator_filter_error:
            return creator_filter_error, True

        task_items = []
        for task in state.board_tasks.values():
            if str(getattr(task, "group", "") or "").strip() != _engineer_group:
                continue
            task_archived = bool(
                str(getattr(task, "archived_at", "") or "").strip()
            )
            if task_archived != archived:
                continue
            task_labels = set(getattr(task, "labels", []) or [])
            if (
                not include_engineer_messages
                and task_is_engineer_message_followup(task)
            ):
                continue
            if label_filter and not all(label in task_labels for label in label_filter):
                continue
            if lane_filter and str(getattr(task, "lane", "") or "") != lane_filter:
                continue
            if (
                assigned_engineer_filter
                and _effective_assigned_engineer_id(task) != assigned_engineer_filter
            ):
                continue
            creator_matches, creator_error = _architect_task_creator_filter_matches(
                task,
                creator_filter,
            )
            if creator_error:
                return creator_error, True
            if not creator_matches:
                continue
            task_items.append(_architect_board_summary_task_item(
                task,
                created_by=_task_created_by_classifier(task),
            ))

        task_items.sort(key=lambda item: _architect_task_list_sort_key(state, item))
        total = len(task_items)
        return _compact_json({
            "type": "task_list",
            "tasks": task_items[:limit],
            "total": total,
            "truncated": total > limit,
        }), False

    if tool_name == "engineer_list" and caller_kind == "architect":
        include_tombstoned = bool(args.get("include_tombstoned", False))
        visible_task_ids = set(
            _filter_tasks_for_caller(real_state, caller_kind, caller_id)
        )
        engineers = []
        for cell, relation in _architect_visible_engineers(
            real_state, caller_id, include_tombstoned=include_tombstoned
        ).values():
            is_busy = real_state.agent_is_busy(cell.id)
            current_task = real_state.agent_current_task(cell.id)
            if current_task and current_task.id not in visible_task_ids:
                current_task = None
            specializations = list(
                getattr(cell, "engineer_specializations", []) or []
            )
            engineers.append({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                # Session turn-taking and task occupancy are intentionally
                # separate signals.  In particular, an idle Engineer session
                # can still own staged or live work.
                "session_status": cell.status,
                "is_busy": is_busy,
                "dismissed_at": _agent_dismissed_at(cell),
                "deleted_at": float(getattr(cell, "deleted_at", 0) or 0),
                "permanent_delete_after": float(
                    getattr(cell, "permanent_delete_after", 0) or 0
                ),
                "group": cell.group,
                "relation": relation,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "activity": cell.activity,
                "activity_detail": cell.activity_detail,
                "specializations": specializations,
                # Keep the underlying empty list intact: it is not a slug.
                # The display field prevents an intentional generalist from
                # being mistaken for an unconfigured or unusable engineer.
                "specialization_display": (
                    ", ".join(specializations) if specializations
                    else "generalist"
                ),
            })
        engineers.sort(
            key=lambda item: (
                0 if item["relation"] == "hired" else 1,
                item["slug"] or item["name"] or item["id"],
                item["id"],
            )
        )
        return json.dumps({"engineers": engineers}), False

    if tool_name == "pending_hire_status" and caller_kind == "architect":
        pending_hire, hire_error = _load_architect_pending_hire(
            real_state, caller_id, args.get("hire_id", "")
        )
        if not pending_hire:
            return hire_error, True
        return json.dumps(pending_hire), False

    if tool_name == "pending_hire_list" and caller_kind == "architect":
        status_filter = str(args.get("status_filter", "") or "").strip()
        if status_filter and status_filter not in {
                "pending", "approved", "rejected"}:
            return (
                "status_filter must be one of: pending, approved, rejected",
                True,
            )
        return json.dumps({
            "pending_hires": real_state.load_pending_hires(
                status_filter=status_filter,
                architect_id=str(caller_id or "").strip(),
            )
        }), False

    if tool_name == "behavior_overlay_read":
        requested = str(args.get("scope_kind", "") or "agent").strip()
        if requested == "effective":
            if caller_kind == "engineer":
                target_id = str(caller_id or "").strip()
            elif caller_kind == "architect":
                target_id, target_error = _resolve_behavior_overlay_architect_target(
                    real_state,
                    caller_id,
                    args.get("agent_id", ""),
                )
                if not target_id:
                    return target_error, True
                if (
                        not _caller_has_behavior_overlay_admin(real_state, caller_id)
                        and target_id != str(caller_id or "").strip()):
                    return "behavior overlay access is limited to this architect's own effective overlay", True
            else:
                return "behavior overlay tools are not available to this caller", True
            target = real_state.agents.get(target_id)
            role_scope = BehaviorOverlayScope.role(
                str(getattr(target, "group", "") or ""),
                str(getattr(target, "kind", "") or ""),
            )
            agent_scope = BehaviorOverlayScope.agent(
                target_id,
                group=str(getattr(target, "group", "") or ""),
            )
            layers = []
            for scope in (role_scope, agent_scope):
                version = real_state.db.load_behavior_overlay_active_version(scope) if real_state.db else None
                active = real_state.db.load_behavior_overlay_active(scope) if real_state.db else {}
                layers.append({
                    **scope.as_row_fields(),
                    "active": active or {},
                    "version": version_summary(version),
                    "text": str((version or {}).get("text", "") or ""),
                })
            return json.dumps({"type": "behavior_overlay_effective", "layers": layers}), False
        scope, scope_error = _behavior_scope_from_mcp_args(
            real_state, caller_kind, caller_id, args, default="agent"
        )
        if not scope:
            return scope_error, True
        if (
                caller_kind == "architect"
                and not _restricted_behavior_overlay_scope_allowed(
                    real_state, caller_id, scope)):
            return "behavior overlay access is limited to this architect's own overlay and architect role overlay", True
        version = real_state.db.load_behavior_overlay_active_version(scope) if real_state.db else None
        active = real_state.db.load_behavior_overlay_active(scope) if real_state.db else {}
        return json.dumps({
            "type": "behavior_overlay",
            **scope.as_row_fields(),
            "active": active,
            "version": version_summary(version),
            "text": str((version or {}).get("text", "") or ""),
        }), False

    if tool_name == "behavior_overlay_versions":
        scope, scope_error = _behavior_scope_from_mcp_args(
            real_state, caller_kind, caller_id, args, default="agent"
        )
        if not scope:
            return scope_error, True
        if (
                caller_kind == "architect"
                and not _restricted_behavior_overlay_scope_allowed(
                    real_state, caller_id, scope)):
            return "behavior overlay access is limited to this architect's own overlay and architect role overlay", True
        return json.dumps({
            "type": "behavior_overlay_versions",
            **scope.as_row_fields(),
            "versions": [
                version_summary(row)
                for row in real_state.list_behavior_overlay_versions(
                    scope_kind=scope.scope_kind,
                    scope_group=scope.scope_group,
                    scope_key=scope.scope_key,
                    limit=int(args.get("limit", 50) or 50),
                )
            ],
        }), False

    if tool_name == "behavior_overlay_diff":
        proposal_id = str(args.get("proposal_id", "") or "").strip()
        try:
            if proposal_id:
                proposal = real_state.load_behavior_overlay_proposal(proposal_id)
                if caller_kind == "engineer":
                    requested = str(args.get("scope_kind", "") or "agent")
                    if requested == "role":
                        if (
                                not proposal
                                or proposal.get("scope_kind") != "role"
                                or proposal.get("scope_group") != _caller_group(real_state, caller_id)
                                or proposal.get("scope_key") != "engineer"):
                            return "behavior overlay proposal not found", True
                    elif not proposal or proposal.get("agent_id") != str(caller_id or "").strip():
                        return "behavior overlay proposal not found", True
                elif caller_kind == "architect":
                    if not _behavior_overlay_visible_to_architect(
                            real_state, caller_id, proposal):
                        return "behavior overlay proposal not found", True
                    if not _restricted_behavior_overlay_proposal_allowed(
                            real_state, caller_id, proposal):
                        return "behavior overlay access is limited to this architect's own overlay and architect role overlay", True
                payload = real_state.behavior_overlay_diff_payload(
                    proposal_id=proposal_id,
                )
            else:
                scope, scope_error = _behavior_scope_from_mcp_args(
                    real_state, caller_kind, caller_id, args, default="agent"
                )
                if not scope:
                    return scope_error, True
                if (
                        caller_kind == "architect"
                        and not _restricted_behavior_overlay_scope_allowed(
                            real_state, caller_id, scope)):
                    return "behavior overlay access is limited to this architect's own overlay and architect role overlay", True
                payload = real_state.behavior_overlay_diff_payload(
                    agent_id=scope.agent_id,
                    scope_kind=scope.scope_kind,
                    scope_group=scope.scope_group,
                    scope_key=scope.scope_key,
                    from_version_id=str(args.get("from_version_id", "") or ""),
                    to_version_id=str(args.get("to_version_id", "") or ""),
                )
            return json.dumps(payload), False
        except Exception as exc:
            return str(exc), True

    if tool_name == "behavior_overlay_proposal_list" and caller_kind == "architect":
        target_filter = ""
        scope_filter = None
        if str(args.get("scope_kind", "") or "") == "role":
            scope_filter, scope_error = _behavior_scope_from_mcp_args(
                real_state, caller_kind, caller_id, args, default="role"
            )
            if not scope_filter:
                return scope_error, True
        if str(args.get("agent_id", "") or "").strip():
            target_filter, target_error = _resolve_behavior_overlay_architect_target(
                real_state,
                caller_id,
                args.get("agent_id", ""),
            )
            if not target_filter:
                return target_error, True
            if (
                    not _caller_has_behavior_overlay_admin(real_state, caller_id)
                    and target_filter != str(caller_id or "").strip()):
                return "behavior overlay access is limited to this architect's own overlay and architect role overlay", True
        proposals = []
        for proposal in real_state.list_behavior_overlay_proposals(
                status_filter=str(args.get("status_filter", "") or ""),
                agent_id=target_filter,
                scope_kind=scope_filter.scope_kind if scope_filter else "",
                scope_group=scope_filter.scope_group if scope_filter else "",
                scope_key=scope_filter.scope_key if scope_filter else "",
                limit=int(args.get("limit", 100) or 100)):
            if _behavior_overlay_visible_to_architect(
                    real_state, caller_id, proposal) and (
                        _restricted_behavior_overlay_proposal_allowed(
                            real_state, caller_id, proposal)):
                proposals.append(proposal_summary(proposal))
        return json.dumps({
            "type": "behavior_overlay_proposals",
            "proposals": proposals,
        }), False

    if tool_name == "agents_list":
        agents = []
        for c in state.iter_active_agents():
            if c.cell_type != "agent":
                continue
            if c.group != _engineer_group:
                continue
            if not _agent_visible_to_engineer(state, _engineer_cell, c.id):
                continue
            current_task = state.agent_current_task(c.id)
            agents.append({
                "id": c.id,
                "name": c.name,
                "slug": c.slug,
                "status": c.status,
                "group": c.group,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "activity": c.activity,
                "activity_detail": c.activity_detail,
            })
        return json.dumps({"agents": agents}), False

    if tool_name == "agent_show":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents[agent_id]

        d = {
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "agent_type": cell.agent_type,
            "status": cell.status,
            "group": cell.group,
            "directory": cell.directory,
            "git_root": cell.git_root,
            "activity": cell.activity,
            "activity_detail": cell.activity_detail,
            "error_message": cell.error_message,
            "needs_attention": cell.needs_attention,
            "tasks_dispatched": cell.tasks_dispatched,
            "session": {
                "session_id": cell.agent_session_id,
                "tokens_in": cell.session_tokens_in,
                "tokens_out": cell.session_tokens_out,
            },
        }
        current_task = state.agent_current_task(agent_id)
        if current_task and current_task.group != _engineer_group:
            current_task = None
        d.update(
            _agent_health_payload_for_response(
                state,
                cell,
                current_task=current_task,
            )
        )

        # Worktree state
        if cell.worktree_path:
            repo_root = cell.worktree_repo_root or cell.git_root or ""
            d["worktree"] = {
                "path": cell.worktree_path,
                "branch": cell.worktree_branch,
                "base_branch": cell.worktree_base_branch,
                "dirty": cell.worktree_dirty,
                "diff": cell.worktree_diff or {},
                "checkpoints": cell.worktree_checkpoints,
                "ahead": cell.worktree_ahead,
                "behind": cell.worktree_behind,
                "merged": cell.worktree_merged,
            }
            boundary_tasks = []
            for t in state.board_tasks.values():
                if t.group != _engineer_group:
                    continue
                boundary = getattr(t, "worktree_boundary", {}) or {}
                if not isinstance(boundary, dict):
                    continue
                if boundary.get("repo_root", "") != repo_root:
                    continue
                if boundary.get("branch", "") != cell.worktree_branch:
                    continue
                boundary_tasks.append({
                    "task_id": t.id,
                    "task_title": t.task,
                    "lane": t.lane,
                    "boundary": boundary,
                    "resume_after_boundary_task_id": (
                        getattr(t, "resume_after_boundary_task_id", "") or ""
                    ),
                })
            boundary_tasks.sort(
                key=lambda item: (
                    item["boundary"].get("recorded_at", ""),
                    item["task_id"],
                )
            )
            if boundary_tasks:
                d["worktree"]["task_boundaries"] = boundary_tasks
            overview = _worktree_boundary_overview(
                state,
                repo_root=repo_root,
                branch=cell.worktree_branch or "",
            )
            if overview:
                overview["current_task_id"] = (
                    current_task.id if current_task else ""
                )
                overview["current_task"] = (
                    current_task.task if current_task else ""
                )
                d["worktree"]["boundary_overview"] = overview

        # Child terminals
        children_ids = state._children.get(agent_id, [])
        if children_ids:
            terminals = []
            for cid in children_ids:
                tc = state.agents.get(cid)
                if tc:
                    terminals.append({
                        "name": tc.name,
                        "slug": tc.slug,
                        "status": tc.status,
                        "current_process": tc.current_process,
                        "current_path": tc.current_path,
                    })
            d["terminals"] = terminals

        # Task history — all tasks ever assigned to this agent
        tasks = []
        for t in state.board_tasks.values():
            if t.group != _engineer_group:
                continue
            if t.agent_id != agent_id:
                continue
            task_info = {
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "lane": t.lane,
                "status": t.status,
                "labels": t.labels or [],
                "action": t.action_name,
                "resume_after_boundary_task_id": (
                    t.resume_after_boundary_task_id or ""
                ),
            }
            if t.worktree_boundary:
                task_info["worktree_boundary"] = t.worktree_boundary
            if t.messages:
                task_info["messages"] = t.messages
            tasks.append(task_info)
        if tasks:
            d["tasks"] = tasks

        # Current task (may differ from tasks list if unlinked)
        if current_task and current_task.group == _engineer_group:
            d["current_task_id"] = current_task.id

        return json.dumps(d), False

    if tool_name == "actions_list":
        requested_group = str(args.get("group", "") or "").strip()
        if requested_group and requested_group != _engineer_group:
            return "Action catalog is limited to the caller's group", True
        result = await handle_command({
            "cmd": "list_action_catalog",
            "group": _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "action_show":
        result = await handle_command({
            "cmd": "get_action",
            "name": args.get("name", ""),
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "specializations_list":
        result = await handle_command({
            "cmd": "list_specializations",
            "group": args.get("group", "") or _engineer_group,
            "scope": str(args.get("scope", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "specialization_show":
        result = await handle_command({
            "cmd": "get_specialization",
            "name": args.get("name", ""),
            "group": args.get("group", "") or _engineer_group,
            "scope": str(args.get("scope", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "specialization_save":
        name = str(args.get("name", "") or "").strip()
        if not name:
            return "name is required", True
        payload = args.get("data")
        if payload is None:
            payload = args.get("specialization", {})
        if not isinstance(payload, dict):
            return "data must be an object", True
        result = await handle_command({
            "cmd": "save_specialization",
            "name": name,
            "data": payload,
            "scope": str(args.get("scope", "project") or "project"),
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "specialization_delete":
        name = str(args.get("name", "") or "").strip()
        if not name:
            return "name is required", True
        result = await handle_command({
            "cmd": "delete_specialization",
            "name": name,
            "scope": str(args.get("scope", "") or "").strip(),
            "group": args.get("group", "") or _engineer_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    # -- Write tools --------------------------------------------------------

    if tool_name == "behavior_overlay_propose" and caller_kind == "engineer":
        if str(args.get("scope_kind", "") or "agent") == "role":
            return (
                "engineer role behavior overlay writes are not supported in v1; "
                "ask the architect to curate a user-approved role proposal",
                True,
            )
        if not str(getattr(_engineer_cell, "hired_by_architect_id", "") or "").strip():
            return "engineer has no hiring architect to govern behavior overlays", True
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "agent_id": str(caller_id or "").strip(),
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "engineer",
            "text": str(args.get("text", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_request_rollback" and caller_kind == "engineer":
        if str(args.get("scope_kind", "") or "agent") == "role":
            return (
                "engineer role behavior overlay rollback requests are not "
                "supported in v1; ask the architect to curate a user-approved "
                "role rollback",
                True,
            )
        if not str(getattr(_engineer_cell, "hired_by_architect_id", "") or "").strip():
            return "engineer has no hiring architect to govern behavior overlays", True
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "agent_id": str(caller_id or "").strip(),
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "engineer",
            "proposal_type": "rollback",
            "target_version_id": str(args.get("version_id", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_propose" and caller_kind == "architect":
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "agent_id": str(caller_id or "").strip(),
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "architect",
            "text": str(args.get("text", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_propose_for_engineer" and caller_kind == "architect":
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state,
            caller_id,
            str(args.get("engineer_id", "") or ""),
        )
        if not engineer_id:
            resolved = _resolve_agent_including_tombstoned(
                real_state,
                str(args.get("engineer_id", "") or ""),
            )
            resolved_cell = real_state.agents.get(resolved or "")
            if resolved_cell and str(getattr(resolved_cell, "kind", "") or "") == "worker":
                return "worker behavior overlays are not supported in v1", True
            return engineer_error, True
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "agent_id": engineer_id,
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "architect",
            "text": str(args.get("text", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
            "architect_approver_id": str(caller_id or "").strip(),
            "auto_apply_architect_direct": True,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_propose_for_role" and caller_kind == "architect":
        if _agent_dismissed_at(real_state.agents.get(str(caller_id or "").strip())):
            return _architect_dismissed_error(caller_id), True
        scope, scope_error = _behavior_role_scope_for_caller(
            real_state,
            caller_id,
            str(args.get("role_kind", "") or args.get("role", "") or ""),
        )
        if not scope:
            return scope_error, True
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "scope_kind": "role",
            "group": scope.scope_group,
            "role_kind": scope.scope_key,
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "architect",
            "text": str(args.get("text", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
            "architect_approver_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_approve" and caller_kind == "architect":
        proposal_id = str(args.get("proposal_id", "") or "").strip()
        proposal = real_state.load_behavior_overlay_proposal(proposal_id)
        if not _behavior_overlay_visible_to_architect(
                real_state, caller_id, proposal):
            return "behavior overlay proposal not found", True
        result = await handle_command({
            "cmd": "behavior_overlay_architect_approve",
            "proposal_id": proposal_id,
            "architect_id": str(caller_id or "").strip(),
            "expected_proposed_text_sha256": str(
                args.get("expected_proposed_text_sha256", "") or ""
            ),
            "note": str(args.get("note", "") or ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_reject" and caller_kind == "architect":
        proposal_id = str(args.get("proposal_id", "") or "").strip()
        proposal = real_state.load_behavior_overlay_proposal(proposal_id)
        if not _behavior_overlay_visible_to_architect(
                real_state, caller_id, proposal):
            return "behavior overlay proposal not found", True
        result = await handle_command({
            "cmd": "behavior_overlay_architect_reject",
            "proposal_id": proposal_id,
            "architect_id": str(caller_id or "").strip(),
            "actor_id": str(caller_id or "").strip(),
            "note": str(args.get("note", "") or ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_rollback" and caller_kind == "architect":
        target_id, target_error = _resolve_behavior_overlay_architect_target(
            real_state,
            caller_id,
            args.get("agent_id", ""),
        )
        if not target_id:
            return target_error, True
        if (
                not _caller_has_behavior_overlay_admin(real_state, caller_id)
                and target_id != str(caller_id or "").strip()):
            return "behavior overlay rollback is limited to this architect's own overlay", True
        target_cell = real_state.agents.get(target_id)
        is_engineer_target = (
            str(getattr(target_cell, "kind", "") or "").strip() == "engineer"
        )
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "agent_id": target_id,
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "architect",
            "proposal_type": "rollback",
            "target_version_id": str(args.get("version_id", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
            "architect_approver_id": str(caller_id or "").strip(),
            "auto_apply_architect_direct": bool(is_engineer_target),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "behavior_overlay_rollback_role" and caller_kind == "architect":
        if _agent_dismissed_at(real_state.agents.get(str(caller_id or "").strip())):
            return _architect_dismissed_error(caller_id), True
        scope, scope_error = _behavior_role_scope_for_caller(
            real_state,
            caller_id,
            str(args.get("role_kind", "") or args.get("role", "") or ""),
        )
        if not scope:
            return scope_error, True
        result = await handle_command({
            "cmd": "behavior_overlay_propose",
            "scope_kind": "role",
            "group": scope.scope_group,
            "role_kind": scope.scope_key,
            "proposed_by_agent_id": str(caller_id or "").strip(),
            "proposed_by_kind": "architect",
            "proposal_type": "rollback",
            "target_version_id": str(args.get("version_id", "") or ""),
            "rationale": str(args.get("rationale", "") or ""),
            "expected_base_version_id": str(
                args.get("expected_base_version_id", "") or ""
            ),
            "idempotency_key": str(args.get("idempotency_key", "") or ""),
            "architect_approver_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "engineer_hire" and caller_kind == "architect":
        name = str(args.get("name", "") or "").strip()
        if not name:
            return "name is required", True
        payload = {
            "cmd": "architect_engineer_hire",
            "architect_id": str(caller_id or "").strip(),
            "name": name,
            "command": str(args.get("command", "") or "").strip(),
            "provider": str(args.get("provider", "") or "").strip(),
            "directory": str(args.get("directory", "") or "").strip(),
        }
        if "specializations" in args:
            payload["specializations"] = args.get("specializations")
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "engineer_set_specializations" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_set_specializations",
            "architect_id": str(caller_id or "").strip(),
            "engineer_id": engineer_id,
            "specializations": args.get("specializations", []),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "engineer_dismiss" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_dismiss",
            "architect_id": str(caller_id or "").strip(),
            "engineer_id": engineer_id,
            "reason": str(args.get("reason", "") or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "engineer_rehire" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_rehire",
            "architect_id": str(caller_id or "").strip(),
            "engineer_id": engineer_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "engineer_restore" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state,
            caller_id,
            engineer_ident,
            include_tombstoned=True,
        )
        if not engineer_id:
            return engineer_error, True
        result = await handle_command({
            "cmd": "architect_engineer_restore",
            "architect_id": str(caller_id or "").strip(),
            "engineer_id": engineer_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    return UNHANDLED
