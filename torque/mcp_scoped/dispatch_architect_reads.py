"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403

async def dispatch_architect_reads(ctx: ScopedDispatchContext):
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

    # -- Read tools ---------------------------------------------------------

    if tool_name == "attention_digest" and caller_kind == "architect":
        return _architect_attention_digest_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "group_health_brief" and caller_kind == "architect":
        return _compact_json(build_group_health_brief(
            state,
            caller_id,
            _engineer_group,
            args,
        )), False

    if tool_name == "wave_summary" and caller_kind == "architect":
        return _architect_wave_summary_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "completion_audit" and caller_kind == "architect":
        return _architect_completion_audit_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "events_recent" and caller_kind == "architect":
        return _architect_events_recent_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "engineer_feedback_status" and caller_kind == "architect":
        return _architect_engineer_feedback_status_json(
            real_state,
            caller_id,
            args,
        )

    if tool_name == "peer_list" and caller_kind == "architect":
        return _architect_peer_list_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "peer_list" and caller_kind == "engineer":
        return _engineer_peer_list_json(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )

    if tool_name == "peer_inbox" and caller_kind == "architect":
        return _architect_peer_inbox_json(real_state, caller_id, args)

    if tool_name == "peer_inbox" and caller_kind == "engineer":
        return _engineer_peer_inbox_json(real_state, caller_id, args)

    if tool_name == "peer_inspect" and caller_kind == "engineer":
        return _engineer_peer_inspect_json(real_state, caller_id, args)

    if tool_name == "engineer_peer_threads" and caller_kind == "architect":
        return _architect_engineer_peer_threads_json(real_state, caller_id, args)

    if tool_name == "engineer_peer_inspect" and caller_kind == "architect":
        return _architect_engineer_peer_inspect_json(real_state, caller_id, args)

    if tool_name == "semantic_recall":
        return await _semantic_recall_json(
            real_state,
            caller_kind,
            caller_id,
            args,
        )

    if tool_name == "boot_summary":
        from torque.ai_summaries import cached_boot_summary_payload

        return json.dumps(
            cached_boot_summary_payload(real_state, caller_kind, caller_id)
        ), False

    if tool_name == "initiative_list":
        return _initiative_read_json(
            real_state,
            caller_kind,
            caller_id,
            args,
            show=False,
        )

    if tool_name == "initiative_show":
        return _initiative_read_json(
            real_state,
            caller_kind,
            caller_id,
            args,
            show=True,
        )

    if tool_name == "area_list":
        return _area_read_json(
            real_state,
            caller_kind,
            caller_id,
            args,
            show=False,
        )

    if tool_name == "area_show":
        return _area_read_json(
            real_state,
            caller_kind,
            caller_id,
            args,
            show=True,
        )

    if tool_name == "hint_snooze" and caller_kind == "engineer":
        fingerprint = str(args.get("fingerprint", "") or "").strip()
        if not fingerprint:
            return "fingerprint is required", True
        clear = bool(args.get("clear", False))
        raw_hours = args.get("hours", 168)
        try:
            hours = float(raw_hours)
        except (TypeError, ValueError):
            return "hours must be a number", True
        settings = real_state.get_group_settings(_engineer_group)
        snoozes = dict(getattr(settings, "engineer_hint_snoozes", {}) or {})
        if clear or hours <= 0:
            snoozes.pop(fingerprint, None)
            expires_at = 0.0
            state_text = "cleared"
        else:
            hours = min(max(hours, 1.0), 24.0 * 365.0)
            expires_at = time.time() + hours * 3600.0
            snoozes[fingerprint] = expires_at
            state_text = "snoozed"
        real_state.update_group_settings(
            _engineer_group,
            engineer_hint_snoozes=snoozes,
        )
        return json.dumps({
            "type": "ok",
            "group": _engineer_group,
            "fingerprint": fingerprint,
            "state": state_text,
            "snoozed_until": expires_at,
        }), False

    if tool_name == "mcp_calls":
        target_agent = str(args.get("agent_id", "") or args.get("cell_id", "") or "").strip()
        if target_agent:
            if caller_kind == "architect":
                resolved_agent_id = _resolve_agent(real_state, target_agent)
                target_cell = real_state.agents.get(resolved_agent_id or "")
                if (
                    not resolved_agent_id
                    or not target_cell
                    or str(getattr(target_cell, "group", "") or "").strip() != _engineer_group
                ):
                    resolved_agent_id, resolve_error = None, f"Agent not found: {target_agent}"
                else:
                    resolve_error = ""
            else:
                resolved_agent_id, resolve_error = _resolve_visible_agent(
                    real_state,
                    caller_kind,
                    caller_id,
                    target_agent,
                )
            if not resolved_agent_id:
                return resolve_error, True
            target_agent = resolved_agent_id
        cmd_name = (
            "architect_mcp_calls"
            if caller_kind == "architect"
            else "engineer_mcp_calls"
        )
        payload = {
            "cmd": cmd_name,
            "caller_id": caller_id,
            "agent_id": target_agent,
            "cell_id": target_agent,
            "tool_name_pattern": (
                args.get("tool_name_pattern")
                or args.get("tool_filter")
                or "mcp__torque__%"
            ),
            "hook_event_name": args.get("hook_event_name", ""),
            "since": args.get("since", None),
            "until": args.get("until", None),
            "limit": args.get("limit", 50),
        }
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result or {"type": "mcp_calls", "calls": []}), False

    if tool_name == "engineer_pending_question" and caller_kind == "architect":
        return _architect_engineer_pending_question_json(
            real_state,
            caller_id,
            args,
        )

    if tool_name == "engineer_answer" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        answer = str(args.get("answer", "") or "").strip()
        if not answer:
            return "answer is required", True
        engineer, group, _question, pending_error = (
            _resolve_architect_pending_question_engineer(
                real_state, caller_id, engineer_ident
            )
        )
        if pending_error:
            return pending_error, True
        result = await handle_command({
            "cmd": "engineer_reply",
            "group": group,
            "answer": answer,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({
            "type": "ok",
            "engineer_id": engineer.id,
            "group": group,
        }), False

    if tool_name == "deploy_state" and caller_kind == "architect":
        return _compact_json(
            architect_deploy_state_payload(real_state, _engineer_group)
        ), False

    if tool_name == "get_architect_settings" and caller_kind == "architect":
        return json.dumps({
            "type": "architect_settings",
            "group": _engineer_group,
            "settings": asdict(
                real_state.get_architect_settings(_engineer_group)
            ),
        }), False

    if tool_name == "digest_filter" and caller_kind == "architect":
        settings = real_state.get_agent_digest_settings(caller_id)
        if "set" in args:
            current = normalize_architect_enabled_events(args.get("set", []))
        else:
            current = normalize_architect_enabled_events(
                getattr(settings, "enabled_events", []) or []
            )
        current_set = set(current)
        for event_kind in normalize_architect_enabled_events(
                args.get("enable", []) or []):
            current_set.add(event_kind)
        for event_kind in normalize_architect_enabled_events(
                args.get("disable", []) or []):
            current_set.discard(event_kind)
        enabled_events = sorted(current_set)
        real_state.update_agent_digest_settings(
            caller_id,
            architect_digest=True,
            enabled_events=enabled_events,
        )
        updated = real_state.get_agent_digest_settings(caller_id)
        return json.dumps({
            "type": "ok",
            "agent_id": caller_id,
            "group": _engineer_group,
            "enabled_events": list(getattr(updated, "enabled_events", []) or []),
            "mandatory_events": sorted(ARCHITECT_MANDATORY_EVENTS),
            "settings": asdict(updated),
        }), False

    if tool_name == "task_chain" and caller_kind == "architect":
        return _architect_task_chain_json(
            state,
            caller_id,
            args.get("task", ""),
        )

    if tool_name == "board_summary":
        summary_streams = _engineer_streams(
            state,
            _engineer_cell,
            _engineer_group,
            include_merged=False,
            visibility_limit=5,
        )
        tasks = [
            t for t in state.board_tasks.values()
            if t.group == _engineer_group
        ]
        archived_tasks = [t for t in tasks if t.lane == ARCHIVED_LANE]
        visible_tasks = [t for t in tasks if t.lane != ARCHIVED_LANE]
        if tool_prefix == "engineer_" and caller_kind == "engineer":
            visible_tasks = [
                t for t in visible_tasks
                if _effective_assigned_engineer_id(t) == str(caller_id or "").strip()
            ]
            archived_tasks = [
                t for t in archived_tasks
                if _effective_assigned_engineer_id(t) == str(caller_id or "").strip()
            ]
        pending_message_followups = [
            t for t in visible_tasks
            if task_is_engineer_message_followup(t)
            and not board_task_is_closed(t)
        ]
        actionable_visible_tasks = [
            t for t in visible_tasks
            if not task_is_engineer_message_followup(t)
        ]
        summary_state = copy.copy(state)
        summary_state.board_tasks = {
            task.id: task for task in actionable_visible_tasks
        }

        lane_counts = {
            lane_name: 0 for lane_name in state.board_lanes
            if lane_name != ARCHIVED_LANE
        }
        extra_lanes = {}
        label_counts = {"ready": 0, "deferred": 0}
        health_counts = {
            "healthy": 0,
            "blocked": 0,
            "stale-in-progress": 0,
            "idle-risk": 0,
            "stalled": 0,
            "thrashing": 0,
        }
        verification_counts = {
            "pending": 0,
            "attempted": 0,
            "passed": 0,
            "failed": 0,
        }
        review_counts = {
            "ship": 0,
            "block": 0,
            "needs_followup": 0,
            "unknown": 0,
        }
        unhealthy = []
        pending_asks = []
        verification_items = []
        review_items = []
        include_created_by = caller_kind == "architect"
        architect_task_items = []
        specialization_filter = set()
        specialization_filter_engineer_id = ""
        if include_created_by:
            spec_ident = str(
                args.get("specialization_engineer_id", "") or ""
            ).strip()
            if spec_ident:
                resolved_engineer_id, _engineer_error = _resolve_visible_agent(
                    real_state, caller_kind, caller_id, spec_ident
                )
                if resolved_engineer_id:
                    specialization_filter_engineer_id = resolved_engineer_id
                    spec_cell = real_state.agents.get(resolved_engineer_id)
                    specialization_filter = {
                        str(s or "").strip()
                        for s in (
                            getattr(
                                spec_cell, "engineer_specializations", []
                            ) or []
                        )
                        if str(s or "").strip()
                    }
        for task in actionable_visible_tasks:
            created_by = _task_created_by_classifier(task) if include_created_by else ""
            if include_created_by:
                task_spec = str(
                    getattr(task, "suggested_specialization", "") or ""
                ).strip()
                if (
                    not specialization_filter_engineer_id
                    or (task_spec and task_spec in specialization_filter)
                ):
                    architect_task_items.append(
                        _architect_board_summary_task_item(
                            task,
                            created_by=created_by,
                        )
                    )
            if task.lane in lane_counts:
                lane_counts[task.lane] += 1
            else:
                extra_lanes[task.lane] = extra_lanes.get(task.lane, 0) + 1

            labels = set(task.labels or [])
            for label_name in label_counts:
                if label_name in labels:
                    label_counts[label_name] += 1

            health_state = getattr(task, "health_state", "healthy") or "healthy"
            if health_state not in health_counts:
                health_counts[health_state] = 0
            health_counts[health_state] += 1
            if not board_task_is_closed(task) and health_state != "healthy":
                item = {
                    "id": task.id,
                    "title": task.task,
                    "health_state": health_state,
                    "health_since": getattr(task, "health_since", ""),
                }
                if include_created_by:
                    item["created_by"] = created_by
                unhealthy.append(item)

            verification_state = getattr(task, "verification_state", "") or ""
            if not board_task_is_closed(task) and verification_state in verification_counts:
                verification_counts[verification_state] += 1
                if verification_state in {"pending", "failed"}:
                    verification_summary = getattr(
                        task, "verification_summary", {}
                    ) or {}
                    if not isinstance(verification_summary, dict):
                        verification_summary = {}
                    detail = (
                        verification_summary.get("human_validation_pending", "")
                        or (
                            "Live smoke pending"
                            if verification_summary.get("live_smoke_pending")
                            else ""
                        )
                        or (
                            "Deploy not attempted"
                            if verification_summary.get("deploy_attempted") is False
                            else ""
                        )
                        or verification_summary.get("test_outcome", "")
                        or getattr(task, "verification_notes", "")
                        or verification_summary.get("tests_run", "")
                        or ""
                    )
                    item = {
                        "id": task.id,
                        "title": task.task,
                        "verification_state": verification_state,
                        "verification_mode": getattr(
                            task, "verification_mode", ""
                        ) or "",
                        "verification_notes": getattr(
                            task, "verification_notes", ""
                        ) or "",
                        "detail": str(detail).strip(),
                    }
                    if include_created_by:
                        item["created_by"] = created_by
                    verification_items.append(item)

            if "torque:human" in labels and not board_task_is_closed(task):
                item = {
                    "id": task.id,
                    "title": task.task,
                    "parent_task_id": task.parent_task_id,
                }
                if include_created_by:
                    item["created_by"] = created_by
                pending_asks.append(item)

            review = _task_review_inline_state(task)
            if review:
                verdict = review.get("verdict", "unknown") or "unknown"
                if verdict not in review_counts:
                    review_counts[verdict] = 0
                review_counts[verdict] += 1
                item = {
                    "id": task.id,
                    "title": task.task,
                    **review,
                }
                if include_created_by:
                    item["created_by"] = created_by
                review_items.append(item)

        ordered_lanes = dict(lane_counts)
        for lane_name in sorted(extra_lanes):
            ordered_lanes[lane_name] = extra_lanes[lane_name]

        gs = state.get_group_settings(_engineer_group)
        engineer_id = gs.engineer_agent_id or (
            _engineer_cell.id if _engineer_cell and _engineer_cell.group == _engineer_group
            else ""
        )
        agent_status_counts = {
            "idle": 0,
            "running": 0,
            "error": 0,
            "stopped": 0,
        }
        active_agents = []
        total_agents = 0
        needs_attention = 0
        boundary_items = []
        seen_branch_keys = set()

        agents = [
            c for c in state.iter_active_agents()
            if c.cell_type == "agent"
            and c.group == _engineer_group
            and c.id != engineer_id
            and _agent_visible_to_engineer(state, _engineer_cell, c.id)
        ]
        agents.sort(key=lambda c: ((c.slug or c.name or c.id).lower(), c.id))

        for cell in agents:
            total_agents += 1
            if cell.needs_attention:
                needs_attention += 1
            status = cell.status or "stopped"
            agent_status_counts[status] = agent_status_counts.get(status, 0) + 1
            current_task = state.agent_current_task(cell.id)
            repo_root = cell.worktree_repo_root or cell.git_root or ""
            branch = cell.worktree_branch or ""
            boundary_key = repo_root + "::" + branch if repo_root and branch else ""
            if boundary_key and boundary_key not in seen_branch_keys:
                overview = _worktree_boundary_overview(
                    state,
                    repo_root=repo_root,
                    branch=branch,
                )
                if overview:
                    overview["agent_id"] = cell.id
                    overview["agent_name"] = cell.name
                    overview["agent_slug"] = cell.slug
                    overview["current_task_id"] = current_task.id if current_task else ""
                    overview["current_task"] = current_task.task if current_task else ""
                    boundary_items.append(overview)
                    seen_branch_keys.add(boundary_key)
            if status == "stopped":
                continue
            active_agents.append({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "type": cell.agent_type,
                "status": status,
                "current_task_id": current_task.id if current_task else "",
                "current_task": current_task.task if current_task else "",
                "needs_attention": cell.needs_attention,
            })

        pending_asks.sort(key=lambda item: (item["title"].lower(), item["id"]))
        unhealthy.sort(
            key=lambda item: (
                -HEALTH_SEVERITY.get(item["health_state"], 0),
                item["health_since"] or "",
                item["title"].lower(),
            ),
        )
        verification_items.sort(
            key=lambda item: (
                0 if item["verification_state"] == "failed" else 1,
                item["title"].lower(),
            ),
        )
        review_items.sort(
            key=lambda item: (
                item.get("recorded_at", ""),
                item["title"].lower(),
            ),
            reverse=True,
        )
        boundary_items.sort(
            key=lambda item: (
                0 if item["partial_review_safe"] else 1,
                item.get("branch", ""),
                item.get("latest_boundary_recorded_at", ""),
            ),
        )
        if include_created_by:
            lane_order = {lane: idx for idx, lane in enumerate(state.board_lanes)}
            architect_task_items.sort(
                key=lambda item: (
                    lane_order.get(item["lane"], len(lane_order)),
                    getattr(state.board_tasks.get(item["id"]), "position", 0),
                    item["title"].lower(),
                    item["id"],
                )
            )

        summary = {
            "group": _engineer_group,
            "tasks_total": len(actionable_visible_tasks),
            "archived_total": len(archived_tasks),
            "pending_message_followups": len(pending_message_followups),
            "lanes": ordered_lanes,
            "labels": label_counts,
            "task_health": {
                "counts": health_counts,
                "unhealthy": unhealthy[:10],
                "truncated": len(unhealthy) > 10,
            },
            "hints": {
                "count": 0,
                "items": [],
                "truncated": False,
            },
            "asks": {
                "count": len(pending_asks),
                "items": pending_asks[:10],
                "truncated": len(pending_asks) > 10,
            },
            "verification": {
                "counts": verification_counts,
                "items": verification_items[:10],
                "truncated": len(verification_items) > 10,
            },
            "reviews": {
                "counts": review_counts,
                "items": review_items[:10],
                "truncated": len(review_items) > 10,
            },
            "agents": {
                "total": total_agents,
                "active_count": len(active_agents),
                "needs_attention": needs_attention,
                "by_status": agent_status_counts,
                "active": active_agents[:10],
                "truncated": len(active_agents) > 10,
            },
            "streams": {
                "count": len(summary_streams),
                "by_state": _stream_state_counts(summary_streams),
                "items": summary_streams[:10],
                "truncated": len(summary_streams) > 10,
            },
            "branch_boundaries": {
                "count": len(boundary_items),
                "items": boundary_items[:10],
                "truncated": len(boundary_items) > 10,
            },
        }
        board_sync_summary = _board_sync_summary_payload(actionable_visible_tasks)
        if board_sync_summary:
            summary["board_sync"] = board_sync_summary
        if include_created_by:
            summary["peer_messages"] = _architect_peer_message_summary(
                real_state,
                caller_id,
            )
        hints = compute_engineer_hints(
            summary_state,
            _engineer_group,
            engineer_id=_engineer_cell.id if _engineer_cell else "",
        )
        summary["hints"] = {
            "count": len(hints),
            "items": hints[:10],
            "truncated": len(hints) > 10,
        }
        if caller_kind == "engineer":
            summary["dispatch_shapes"] = _engineer_dispatch_shape_summary(
                real_state,
                _engineer_cell.id if _engineer_cell else "",
                group=_engineer_group,
                window=20,
            )
        if include_created_by:
            return _architect_board_summary_json(
                summary,
                architect_task_items,
            ), False
        return _compact_json(summary), False

    return UNHANDLED
