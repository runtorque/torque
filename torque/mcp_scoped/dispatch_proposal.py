"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403

_ARCHITECT_PROPOSAL_TOOL_REGISTRY = AsyncHandlerRegistry()
_ARCHITECT_PROPOSAL_TOOL_REGISTRY.register_prefix(
    "thinking_",
    _architect_thinking_tool,
    label="thinking_workspace",
)
_ARCHITECT_PROPOSAL_TOOL_REGISTRY.register_prefix(
    "idea_brief_",
    _architect_idea_brief_tool,
    label="idea_briefs",
)

async def dispatch_proposal(ctx: ScopedDispatchContext):
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

    # -- Proposal-oriented tools ------------------------------------------

    if caller_kind == "architect":
        proposal_result = await _ARCHITECT_PROPOSAL_TOOL_REGISTRY.dispatch(
            tool_name,
            tool_name,
            real_state,
            caller_id,
            _engineer_group,
            args,
        )
        if proposal_result.handled:
            return proposal_result.value

    if caller_kind == "architect" and tool_name == "proposal_board_summary":
        return _proposal_board_summary_json(real_state, caller_id, args)

    if caller_kind == "architect" and tool_name == "task_proposal_list":
        return _task_proposal_list_json(real_state, caller_id, args)

    if caller_kind == "architect" and tool_name == "task_proposal_show":
        return _task_proposal_show_json(real_state, caller_id, args)

    if caller_kind == "architect" and tool_name == "proposal_area_list":
        return _product_area_read_json(real_state, caller_id, args, show=False)

    if caller_kind == "architect" and tool_name == "proposal_area_show":
        return _product_area_read_json(real_state, caller_id, args, show=True)

    if caller_kind == "architect" and tool_name == "proposal_initiative_list":
        return _product_initiative_read_json(real_state, caller_id, args, show=False)

    if caller_kind == "architect" and tool_name == "proposal_initiative_show":
        return _product_initiative_read_json(real_state, caller_id, args, show=True)

    if caller_kind == "architect" and tool_name == "decision_proposal_list":
        decisions = _product_decisions_for_architect(
            real_state,
            caller_id,
            include_archived=bool(args.get("include_archived", False)),
        )
        return _compact_json({"type": "decision_proposal_list", "decisions": decisions}), False

    if caller_kind == "architect" and tool_name == "proposal_peer_list":
        include_dismissed, bool_error = _optional_bool_arg(args, "include_dismissed", False)
        if bool_error:
            return bool_error, True
        peers = []
        for cell in real_state.iter_agents(include_tombstoned=False):
            if str(getattr(cell, "id", "") or "").strip() == str(caller_id or "").strip():
                continue
            if (
                    getattr(cell, "cell_type", "") != "agent"
                    or str(getattr(cell, "kind", "") or "").strip() != "architect"
                    or str(getattr(cell, "group", "") or "").strip() != _engineer_group):
                continue
            if _agent_dismissed_at(cell) and not include_dismissed:
                continue
            proposal_peer_scope = _proposal_peer_scope_reason(real_state, caller_id, cell)
            if not proposal_peer_scope:
                continue
            item = _architect_peer_item(real_state, cell)
            item["proposal_peer_eligible"] = True
            item["proposal_peer_scope"] = proposal_peer_scope
            peers.append(item)
        peers.sort(key=lambda item: (str(item.get("name", "") or "").lower(), str(item.get("id", "") or "")))
        return _compact_json({"type": "proposal_peers", "architect_id": caller_id, "architects": peers}), False

    if caller_kind == "architect" and tool_name == "proposal_peer_inbox":
        return _proposal_peer_inbox_json(real_state, caller_id, args)

    if caller_kind == "architect" and tool_name == "task_propose":
        for forbidden in sorted(_PRODUCT_TASK_PROPOSAL_FORBIDDEN_ARGS):
            if forbidden not in args:
                continue
            value = args.get(forbidden)
            if value in (None, "", False, [], {}):
                continue
            return (
                f"{forbidden} is not accepted by architect_task_propose; "
                "product task proposals are always queued, unassigned, and non-dispatched",
                True,
            )
        title = str(args.get("title", "") or "").strip()
        if not title:
            return "title is required", True
        requested_group = str(args.get("group", "") or "").strip()
        if requested_group and requested_group != _engineer_group:
            return "group must match the architect's group", True
        lane = str(args.get("lane", "") or "").strip()
        if lane:
            if lane not in real_state.board_lanes:
                return "lane must already exist", True
            if lane in _PRODUCT_TASK_UNSAFE_LANES:
                return (
                    "product task proposals must stay in a planning-safe lane; "
                    "To Do, In Progress, Done, and Archived are rejected",
                    True,
                )
        else:
            if "Backlog" in real_state.board_lanes:
                lane = "Backlog"
            else:
                lane = next((candidate for candidate in real_state.board_lanes if candidate not in _PRODUCT_TASK_UNSAFE_LANES), "")
            if not lane:
                return "No planning-safe lane is available for product task proposals", True
        labels = _dedupe_strings(args.get("labels", []))
        for default_label in _PRODUCT_TASK_DEFAULT_LABELS:
            if default_label not in labels:
                labels.append(default_label)
        task = real_state.board_add_task(
            title,
            _engineer_group,
            lane=lane,
            description=str(args.get("description", "") or ""),
            labels=labels,
            assigned_engineer_id="",
            assigned_architect_id="",
            agent_id="",
            dispatch_state="queued",
            scheduled_at="",
            action_name="",
            action_vars={},
            created_by_architect_id=str(caller_id or "").strip(),
            suggested_action=str(args.get("suggested_action", "") or "").strip(),
            suggested_specialization=str(args.get("suggested_specialization", "") or "").strip(),
        )
        if not task:
            return "Failed to create product task proposal", True
        persisted = real_state.board_tasks.get(task.id)
        if (
                not persisted
                or str(getattr(persisted, "assigned_engineer_id", "") or "")
                or str(getattr(persisted, "assigned_architect_id", "") or "")
                or str(getattr(persisted, "agent_id", "") or "")
                or str(getattr(persisted, "dispatch_state", "") or "queued") != "queued"
                or str(getattr(persisted, "scheduled_at", "") or "")
                or str(getattr(persisted, "action_name", "") or "")):
            real_state.board_remove_task(task.id)
            return "product task proposal invariant failed; task was not persisted queued/unassigned", True
        response = serialize_task_for_mcp(persisted, tasks_by_id=real_state.board_tasks)
        response["type"] = "product_task_proposal_created"
        response["title"] = persisted.task
        response["labels"] = list(persisted.labels or [])
        response["caveat"] = (
            "Created as a normal queued Board task with product labels. "
            "The product proposal wrapper did not add board-sync authority; existing Board "
            "sync settings may still process normal Board tasks."
        )
        return _compact_json(response), False

    if caller_kind == "architect" and tool_name == "decision_propose":
        title = str(args.get("title", "") or "").strip()
        rationale = str(args.get("rationale", "") or "").strip()
        if not title:
            return "title is required", True
        if not rationale:
            return "rationale is required", True
        status = str(args.get("status", "") or "proposed").strip() or "proposed"
        if status != "proposed":
            return "Decision proposals must remain proposed", True
        if _dedupe_strings(args.get("linked_engineer_ids", [])):
            return "Decision proposals cannot link engineers", True
        supersedes = str(args.get("supersedes", "") or "").strip() or None
        if supersedes:
            prior_decision, decision_error = _load_product_decision(real_state, caller_id, supersedes)
            if not prior_decision:
                return decision_error, True
        linked_task_ids = []
        for task_ident in _dedupe_strings(args.get("linked_task_ids", [])):
            task_id = _resolve_task(real_state, task_ident)
            task = real_state.board_tasks.get(task_id or "")
            if not task_id or not _product_task_visible_for_architect(real_state, caller_id, task):
                return f"Task not found: {task_ident}", True
            linked_task_ids.append(task_id)
        decision = await real_state.save_decision_async({
            "id": "decision-" + uuid.uuid4().hex[:12],
            "architect_id": str(caller_id or "").strip(),
            "title": title,
            "rationale": rationale,
            "status": "proposed",
            "supersedes": supersedes,
            "linked_task_ids": linked_task_ids,
            "linked_engineer_ids": [],
            "archived": False,
            "metadata": _product_decision_metadata(caller_id),
        })
        if not decision:
            return "Failed to save decision", True
        return _compact_json({"type": "decision_proposed", "decision": decision}), False

    if caller_kind == "architect" and tool_name == "decision_proposal_update":
        decision, decision_error = _load_product_decision(real_state, caller_id, args.get("id", ""))
        if not decision:
            return decision_error, True
        patch = {"id": decision["id"], "architect_id": str(caller_id or "").strip(), "metadata": decision.get("metadata") or _product_decision_metadata(caller_id)}
        if "title" in args:
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            patch["title"] = title
        if "rationale" in args:
            rationale = str(args.get("rationale", "") or "").strip()
            if not rationale:
                return "rationale is required", True
            patch["rationale"] = rationale
        if "status" in args:
            status = str(args.get("status", "") or "").strip()
            if status != "proposed":
                return "Decision proposals must remain proposed", True
            patch["status"] = "proposed"
        if "linked_engineer_ids" in args and _dedupe_strings(args.get("linked_engineer_ids", [])):
            return "Decision proposals cannot link engineers", True
        if "supersedes" in args:
            supersedes = str(args.get("supersedes", "") or "").strip() or None
            if supersedes:
                prior_decision, supersedes_error = _load_product_decision(real_state, caller_id, supersedes)
                if not prior_decision:
                    return supersedes_error, True
            patch["supersedes"] = supersedes
        if "linked_task_ids" in args:
            linked_task_ids = []
            for task_ident in _dedupe_strings(args.get("linked_task_ids", [])):
                task_id = _resolve_task(real_state, task_ident)
                task = real_state.board_tasks.get(task_id or "")
                if not task_id or not _product_task_visible_for_architect(real_state, caller_id, task):
                    return f"Task not found: {task_ident}", True
                linked_task_ids.append(task_id)
            patch["linked_task_ids"] = linked_task_ids
            patch["linked_engineer_ids"] = []
        if "archived" in args:
            patch["archived"] = bool(args.get("archived"))
        updated = await real_state.save_decision_async(patch)
        if not updated:
            return "Failed to save decision", True
        return _compact_json({"type": "decision_proposal_updated", "decision": updated}), False

    if caller_kind == "architect" and tool_name == "decision_proposal_link":
        if str(args.get("engineer_id", "") or "").strip():
            return "Decision proposals cannot link engineers", True
        decision, decision_error = _load_product_decision(real_state, caller_id, args.get("id", ""))
        if not decision:
            return decision_error, True
        task_ident = str(args.get("task_id", "") or "").strip()
        if not task_ident:
            return "task_id is required", True
        task_id = _resolve_task(real_state, task_ident)
        task = real_state.board_tasks.get(task_id or "")
        if not task_id or not _product_task_visible_for_architect(real_state, caller_id, task):
            return f"Task not found: {task_ident}", True
        linked_task_ids = _dedupe_strings(list(decision.get("linked_task_ids", []) or []) + [task_id])
        updated = await real_state.save_decision_async({
            "id": decision["id"],
            "architect_id": str(caller_id or "").strip(),
            "linked_task_ids": linked_task_ids,
            "linked_engineer_ids": [],
            "metadata": decision.get("metadata") or _product_decision_metadata(caller_id),
        })
        if not updated:
            return "Failed to save decision", True
        return _compact_json({"type": "decision_proposal_linked", "decision": updated}), False

    if caller_kind == "architect" and tool_name == "proposal_peer_message":
        recipient, recipient_error = _resolve_product_architect_peer(real_state, caller_id, str(args.get("architect_id", "") or "").strip())
        if not recipient:
            return recipient_error, True
        architect = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        if ack_required and not _caller_authority_allows_capability(real_state, caller_id, "message.ack_request"):
            return "ack_required=true requires message.ack_request", True
        context, context_error = _normalize_proposal_context(real_state, caller_id, _engineer_group, args)
        if context_error:
            return context_error, True
        anchor_error = _require_proposal_peer_anchor(
            context,
            tool_name="architect_proposal_peer_message",
        )
        if anchor_error:
            return anchor_error, True
        ack_anchor_error = _require_product_ack_anchor(ack_required, context)
        if ack_anchor_error:
            return ack_anchor_error, True
        context = _add_proposal_peer_marker(context, source="architect_proposal_peer_message", caller_id=caller_id)
        length_error = _validate_architect_peer_message_length(message, context.get("context_summary", ""))
        if length_error:
            return length_error, True
        try:
            saved, created = _save_architect_peer_message(real_state, architect, recipient, action="architect_proposal_peer_message", message=message, ack_required=ack_required, context=context, idempotency_key=idempotency_key)
        except ValueError as exc:
            return str(exc), True
        if created:
            delivery = await _inject_architect_peer_message(handle_command, real_state, architect, recipient, saved, message)
            current = real_state.db.load_agent_peer_message(saved["id"])
            if current:
                saved = current
        else:
            delivery = {"state": str(saved.get("delivery_state", "buffered") or "buffered"), "reason": str(saved.get("delivery_reason", "") or "")}
        return _compact_json({"type": "ok", "message_id": saved["id"], "thread_id": saved["thread_id"], "recipient_architect_id": recipient.id, "ack_required": bool(saved.get("ack_required", False)), "delivery": delivery, "proposal_peer_marker": _PROPOSAL_PEER_MARKER}), False

    if caller_kind == "architect" and tool_name == "proposal_peer_reply":
        message_id = str(args.get("message_id", "") or "").strip()
        if not message_id:
            return "message_id is required", True
        db = getattr(real_state, "db", None)
        row = db.load_agent_peer_message(message_id) if db else None
        if not row or not _proposal_peer_row_visible(real_state, caller_id, row):
            return "thread not found in product-peer scope", True
        caller = real_state.agents.get(str(caller_id or "").strip())
        participants = {str(row.get("sender_id", "") or "").strip(), str(row.get("recipient_id", "") or "").strip()}
        peer_id = next((pid for pid in participants if pid != str(caller_id or "").strip()), "")
        peer, peer_error = _resolve_product_architect_peer(real_state, caller_id, peer_id)
        if not peer:
            return peer_error, True
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        if ack_required and not _caller_authority_allows_capability(real_state, caller_id, "message.ack_request"):
            return "ack_required=true requires message.ack_request", True
        if _dedupe_strings(args.get("context_engineer_ids", [])):
            context, context_error = _normalize_proposal_context(
                real_state,
                caller_id,
                _engineer_group,
                args,
            )
            if context_error:
                return context_error, True
            return "context_engineer_ids are not supported for proposal tools", True
        has_explicit_context = any(key in args for key in ("context_task_ids", "context_decision_ids", "context_area_ids", "context_initiative_ids", "context_idea_brief_ids", "context_summary"))
        if has_explicit_context:
            context, context_error = _normalize_proposal_context(real_state, caller_id, _engineer_group, args)
            if context_error:
                return context_error, True
        else:
            context = _row_proposal_context(row)
        anchor_error = _require_proposal_peer_anchor(
            context,
            tool_name="architect_proposal_peer_reply",
        )
        if anchor_error:
            return anchor_error, True
        ack_anchor_error = _require_product_ack_anchor(ack_required, context)
        if ack_anchor_error:
            return ack_anchor_error, True
        context = _add_proposal_peer_marker(context, source="architect_proposal_peer_reply", caller_id=caller_id)
        length_error = _validate_architect_peer_message_length(message, context.get("context_summary", ""))
        if length_error:
            return length_error, True
        try:
            saved, created = _save_architect_peer_message(real_state, caller, peer, action="architect_proposal_peer_reply", message=message, reply_to_id=message_id, thread_id=str(row.get("thread_id", "") or "").strip(), ack_required=ack_required, context=context, idempotency_key=idempotency_key)
        except ValueError as exc:
            return str(exc), True
        if created:
            await _inject_architect_peer_message(handle_command, real_state, caller, peer, saved, message)
        return _compact_json({"type": "ok", "message_id": saved["id"], "thread_id": saved["thread_id"], "proposal_peer_marker": _PROPOSAL_PEER_MARKER}), False

    if caller_kind == "architect" and tool_name == "proposal_message_user":
        sender = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        context, context_error = _normalize_proposal_context(real_state, caller_id, _engineer_group, args)
        if context_error:
            return context_error, True
        length_error = _validate_architect_peer_message_length(message, context.get("context_summary", ""))
        if length_error:
            return length_error, True
        try:
            saved, created = save_agent_user_direct_message_from_mcp(real_state, sender, message=message, thread_id=str(args.get("thread_id", "") or "").strip(), reply_to_id=str(args.get("reply_to_id", "") or "").strip(), context=context, idempotency_key=idempotency_key, notify=True)
        except ValueError as exc:
            return str(exc), True
        payload = _direct_user_message_response(saved, deduped=not created)
        payload["proposal_context_marker"] = _PROPOSAL_CONTEXT_MARKER
        return json.dumps(payload), False

    if caller_kind == "architect" and tool_name == "proposal_ask_user":
        question = str(args.get("question", "") or "").strip()
        if not question:
            return "Question is required", True
        context, context_error = _normalize_proposal_context(real_state, caller_id, _engineer_group, args)
        if context_error:
            return context_error, True
        architect = real_state.agents.get(str(caller_id or "").strip())
        description = str(args.get("description", "") or "")
        if context.get("context_summary"):
            description = f"{description}\n\nProduct context: {context.get('context_summary')}".strip()
        lane = "Backlog" if "Backlog" in real_state.board_lanes else (real_state.board_lanes[0] if real_state.board_lanes else "Backlog")
        task = real_state.board_add_task(
            question,
            _engineer_group,
            lane=lane,
            description=description,
            labels=["torque:human", "architect-ask", "product-ask", "proposal-only"],
            created_by_architect_id=str(caller_id or "").strip(),
            reply_agent_id=str(caller_id or "").strip(),
            assigned_engineer_id="",
            assigned_architect_id="",
            agent_id="",
            action_name="",
            status="Awaiting Input",
            dispatch_state="queued",
        )
        if not task:
            return "Failed to create product ask task", True
        save_direct_ask_mirror(real_state, architect, question, source_task_id=task.id)
        return _compact_json({"type": "ok", "task_id": task.id, "status": "Awaiting Input", "labels": list(task.labels or []), "proposal_context_marker": _PROPOSAL_CONTEXT_MARKER}), False

    if caller_kind == "architect" and tool_name == "proposal_journal":
        entry_type = str(args.get("type", "") or "").strip()
        if entry_type not in _PROPOSAL_JOURNAL_ENTRY_TYPES:
            return "type must be one of: observation, checkpoint, plan", True
        entry = str(args.get("entry", "") or "")
        if not entry:
            return "entry is required", True
        if not idempotency_key:
            return json.dumps(real_state.architect_journal_append(caller_id, entry_type, entry)), False
        result = await handle_command({"cmd": "architect_journal_append", "architect_id": caller_id, "entry_type": entry_type, "entry": entry})
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if caller_kind == "architect" and tool_name == "proposal_journal_read":
        entries = real_state.architect_journal_read(caller_id, since=args.get("since", 0), limit=args.get("limit", 20))
        entries = [entry for entry in entries if str((entry or {}).get("type", "") or "").strip() in _PROPOSAL_JOURNAL_ENTRY_TYPES]
        return json.dumps({"type": "proposal_journal", "entries": entries}), False

    return UNHANDLED
