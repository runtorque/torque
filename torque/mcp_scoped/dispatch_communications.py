"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403

async def dispatch_communications(ctx: ScopedDispatchContext):
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

    if tool_name == "task_read_grant" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "agent is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        task_ident = str(args.get("task", "") or "").strip()
        task_id = _resolve_task(state, task_ident)
        task = state.board_tasks.get(task_id)
        if not task:
            return "Task not found", True
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        architect = real_state.agents.get(str(caller_id or "").strip())
        engineer = real_state.agents.get(engineer_id)
        group = str(getattr(architect, "group", "") or "").strip()
        task_hash = _task_content_hash(task)
        grant_id = "msg-" + uuid.uuid4().hex[:12]
        context = {
            "context_task_ids": [task.id],
            "context_engineer_ids": [engineer_id],
            "context_snapshot": {
                "task_read_grant": {
                    "marker": TASK_READ_GRANT_MARKER,
                    "grant_id": grant_id,
                    "architect_id": str(caller_id or "").strip(),
                    "recipient_engineer_id": engineer_id,
                    "group": group,
                    "task_id": task.id,
                    "task_content_hash": task_hash,
                },
            },
        }
        delivered = _deliver_architect_engineer_message(
            real_state,
            architect,
            engineer,
            action="architect_task_read_grant",
            message=message,
            message_id=grant_id,
            context=context,
        )
        await _inject_mcp_message(
            handle_command,
            real_state,
            architect,
            engineer,
            delivered,
            (
                f"{message}\n\n"
                "Inspect the pinned task revision with "
                f'peer_context(message_id="{delivered["id"]}"). '
                "Reply with agent_reply and the returned task_content_hash."
            ),
        )
        return json.dumps({
            "type": "ok",
            "grant_id": delivered["id"],
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
            "engineer_id": engineer_id,
            "task_id": task.id,
            "task_content_hash": task_hash,
        }), False

    if tool_name == "message_user" and caller_kind in {"architect", "engineer"}:
        sender = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        context, context_error = _normalize_agent_user_message_context(
            real_state,
            caller_kind,
            caller_id,
            _engineer_group,
            args,
        )
        if context_error:
            return context_error, True
        length_error = _validate_architect_peer_message_length(
            message,
            context.get("context_summary", ""),
        )
        if length_error:
            return length_error, True
        try:
            saved, created = save_agent_user_direct_message_from_mcp(
                real_state,
                sender,
                message=message,
                thread_id=str(args.get("thread_id", "") or "").strip(),
                reply_to_id=str(args.get("reply_to_id", "") or "").strip(),
                context=context,
                idempotency_key=idempotency_key,
                notify=True,
            )
        except ValueError as exc:
            return str(exc), True
        return json.dumps(
            _direct_user_message_response(saved, deduped=not created)
        ), False

    if tool_name == "engineer_message" and caller_kind == "architect":
        response, message_error = await _send_architect_engineer_message(
            real_state,
            handle_command,
            caller_id,
            args,
        )
        if message_error:
            return message_error, True
        return json.dumps(response), False

    if tool_name == "engineer_feedback_request" and caller_kind == "architect":
        return await _architect_engineer_feedback_request_json(
            real_state,
            handle_command,
            caller_id,
            args,
        )

    if tool_name == "peer_message" and caller_kind == "architect":
        recipient, recipient_error = _resolve_architect_peer(
            real_state,
            caller_id,
            str(args.get("architect_id", "") or "").strip(),
        )
        if not recipient:
            return recipient_error, True
        architect = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        if ack_required and not _caller_authority_allows_capability(
            real_state, caller_id, "message.ack_request"
        ):
            return "ack_required=true requires message.ack_request", True
        context, context_error = _normalize_architect_peer_context(
            real_state,
            caller_id,
            _engineer_group,
            args,
        )
        if context_error:
            return context_error, True
        length_error = _validate_architect_peer_message_length(
            message,
            context.get("context_summary", ""),
        )
        if length_error:
            return length_error, True
        try:
            saved, created = _save_architect_peer_message(
                real_state,
                architect,
                recipient,
                action="architect_peer_message",
                message=message,
                ack_required=ack_required,
                context=context,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            return str(exc), True
        if created:
            delivery = await _inject_architect_peer_message(
                handle_command,
                real_state,
                architect,
                recipient,
                saved,
                message,
            )
            current = real_state.db.load_agent_peer_message(saved["id"])
            if current:
                saved = current
        else:
            delivery = {
                "state": str(saved.get("delivery_state", "buffered") or "buffered"),
                "reason": str(saved.get("delivery_reason", "") or ""),
            }
        return json.dumps({
            "type": "ok",
            "message_id": saved["id"],
            "thread_id": saved["thread_id"],
            "recipient_architect_id": recipient.id,
            "ack_required": bool(saved.get("ack_required", False)),
            "delivery": delivery,
        }), False

    if tool_name == "message_architect" and caller_kind == "engineer":
        architect_ident = str(args.get("architect_id", "") or "").strip()
        if not architect_ident:
            return "supervisor is required", True
        architect, architect_error = _resolve_architect_for_engineer(
            real_state, caller_id, architect_ident
        )
        if not architect:
            return architect_error, True
        engineer = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        if ack_required and not _caller_authority_allows_capability(
            real_state, caller_id, "message.ack_request"
        ):
            return "ack_required=true requires message.ack_request", True
        delivered = _deliver_architect_engineer_message(
            real_state,
            engineer,
            architect,
            action="engineer_message_architect",
            message=message,
            ack_required=ack_required,
        )
        await _inject_mcp_message(
            handle_command, real_state, engineer, architect, delivered, message
        )
        return json.dumps({
            "type": "ok",
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
            "architect_id": architect.id,
        }), False

    if tool_name == "peer_notify" and caller_kind == "engineer":
        recipient, recipient_error = _resolve_engineer_peer(
            real_state,
            caller_id,
            str(args.get("engineer_id", "") or "").strip(),
        )
        if not recipient:
            return recipient_error, True
        sender = real_state.agents.get(str(caller_id or "").strip())
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        if ack_required and not _caller_authority_allows_capability(
            real_state, caller_id, "message.ack_request"
        ):
            return "ack_required=true requires message.ack_request", True
        context, context_error = _normalize_engineer_peer_context(
            state,
            caller_id,
            _engineer_group,
            recipient,
            args,
        )
        if context_error:
            return context_error, True
        length_error = _validate_architect_peer_message_length(
            message,
            context.get("context_summary", ""),
        )
        if length_error:
            return length_error, True
        try:
            saved, created = _save_engineer_peer_message(
                real_state,
                sender,
                recipient,
                action="engineer_peer_notify",
                message=message,
                thread_id=str(args.get("thread_id", "") or "").strip(),
                ack_required=ack_required,
                context=context,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            return str(exc), True
        if created:
            delivery = await _inject_architect_peer_message(
                handle_command,
                real_state,
                sender,
                recipient,
                saved,
                message,
            )
            current = real_state.db.load_agent_peer_message(saved["id"])
            if current:
                saved = current
            _emit_engineer_peer_architect_event(
                real_state,
                sender,
                recipient,
                saved,
                opened=not bool(str(args.get("thread_id", "") or "").strip()),
            )
        else:
            delivery = {
                "state": str(saved.get("delivery_state", "buffered") or "buffered"),
                "reason": str(saved.get("delivery_reason", "") or ""),
            }
        return json.dumps({
            "type": "ok",
            "message_id": saved["id"],
            "thread_id": saved["thread_id"],
            "recipient_engineer_id": recipient.id,
            "ack_required": bool(saved.get("ack_required", False)),
            "delivery": delivery,
        }), False

    if tool_name == "peer_reply" and caller_kind == "engineer":
        message_id = str(args.get("message_id", "") or "").strip()
        if not message_id:
            return "message_id is required", True
        db = getattr(real_state, "db", None)
        row = db.load_agent_peer_message(message_id) if db else None
        if not row or not _is_engineer_peer_row(row):
            return "thread not found in scope", True
        caller_id_text = str(caller_id or "").strip()
        participants = {
            str(row.get("sender_id", "") or "").strip(),
            str(row.get("recipient_id", "") or "").strip(),
        }
        if caller_id_text not in participants:
            return "thread not found in scope", True
        peer_id = next((pid for pid in participants if pid != caller_id_text), "")
        peer, peer_error = _resolve_engineer_peer(
            real_state,
            caller_id,
            peer_id,
            include_dismissed=True,
        )
        if not peer:
            return peer_error, True
        sender = real_state.agents.get(caller_id_text)
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required, ack_error = _optional_bool_arg(args, "ack_required")
        if ack_error:
            return ack_error, True
        if ack_required and not _caller_authority_allows_capability(
            real_state, caller_id, "message.ack_request"
        ):
            return "ack_required=true requires message.ack_request", True
        length_error = _validate_architect_peer_message_length(message)
        if length_error:
            return length_error, True
        try:
            saved, created = _save_engineer_peer_message(
                real_state,
                sender,
                peer,
                action="engineer_peer_reply",
                message=message,
                reply_to_id=message_id,
                thread_id=str(row.get("thread_id", "") or "").strip(),
                ack_required=ack_required,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            return str(exc), True
        if created:
            delivery = await _inject_architect_peer_message(
                handle_command,
                real_state,
                sender,
                peer,
                saved,
                message,
            )
            current = real_state.db.load_agent_peer_message(saved["id"])
            if current:
                saved = current
            _emit_engineer_peer_architect_event(
                real_state,
                sender,
                peer,
                saved,
                opened=False,
            )
        else:
            delivery = {
                "state": str(saved.get("delivery_state", "buffered") or "buffered"),
                "reason": str(saved.get("delivery_reason", "") or ""),
            }
        return json.dumps({
            "type": "ok",
            "message_id": saved["id"],
            "thread_id": saved["thread_id"],
            "delivery": delivery,
        }), False

    if (
            caller_kind == "architect"
            and tool_name in {"engineer_reply", "peer_reply"}
    ) or (caller_kind == "engineer" and tool_name == "reply"):
        caller = real_state.agents.get(str(caller_id or "").strip())
        entry, message_error = _load_message_entry(
            caller, args.get("message_id", "")
        )
        if (
                not entry
                and caller_kind == "architect"
                and tool_name == "peer_reply"):
            message_id = str(args.get("message_id", "") or "").strip()
            db = getattr(real_state, "db", None)
            durable_row = db.load_agent_peer_message(message_id) if db else None
            caller_id_text = str(caller_id or "").strip()
            participants = {
                str((durable_row or {}).get("sender_id", "") or "").strip(),
                str((durable_row or {}).get("recipient_id", "") or "").strip(),
            }
            caller_group = str(getattr(caller, "group", "") or "").strip()
            if (
                    durable_row
                    and caller_id_text in participants
                    and len(participants) == 2
                    and {
                        str(durable_row.get("sender_kind", "") or "").strip(),
                        str(durable_row.get("recipient_kind", "") or "").strip(),
                    } == {"architect"}
                    and str(
                        durable_row.get("group_name", "") or ""
                    ).strip() == caller_group
                    and str(
                        durable_row.get("message_type", "message") or "message"
                    ).strip() == "message"
                    and not bool(durable_row.get("blocking", False))
                    and not float(durable_row.get("archived_at", 0) or 0)):
                entry = _agent_peer_message_row_to_entry(
                    durable_row,
                    caller_id_text,
                )
        if not entry and caller_kind == "engineer":
            db = getattr(real_state, "db", None)
            durable_row = (
                db.load_agent_peer_message(
                    str(args.get("message_id", "") or "").strip()
                )
                if db else None
            )
            if (
                _task_read_grant_from_row(durable_row or {})
                and str((durable_row or {}).get("recipient_id", "") or "").strip()
                == str(caller_id or "").strip()
            ):
                entry = {
                    "id": str(durable_row.get("id", "") or ""),
                    "thread_id": str(durable_row.get("thread_id", "") or ""),
                    "peer_id": str(durable_row.get("sender_id", "") or ""),
                    "peer_kind": str(durable_row.get("sender_kind", "") or ""),
                }
        if not entry:
            return message_error, True
        grant_row = None
        grant = {}
        if caller_kind == "engineer":
            db = getattr(real_state, "db", None)
            grant_row = (
                db.load_agent_peer_message(
                    str(entry.get("id", "") or "").strip()
                )
                if db else None
            )
            grant = _task_read_grant_from_row(grant_row or {})
        grant_hash = ""
        grant_context = None
        if grant:
            caller_id_text = str(caller_id or "").strip()
            architect_id = str(grant.get("architect_id", "") or "").strip()
            group = str(grant.get("group", "") or "").strip()
            task_id = str(grant.get("task_id", "") or "").strip()
            task = real_state.board_tasks.get(task_id)
            architect = real_state.agents.get(architect_id)
            caller_engineer = real_state.agents.get(caller_id_text)
            if (
                str(grant.get("grant_id", "") or "").strip()
                != str((grant_row or {}).get("id", "") or "").strip()
                or
                str((grant_row or {}).get("recipient_id", "") or "").strip()
                != caller_id_text
                or str(grant.get("recipient_engineer_id", "") or "").strip()
                != caller_id_text
                or str((grant_row or {}).get("sender_id", "") or "").strip()
                != architect_id
                or str((grant_row or {}).get("group_name", "") or "").strip()
                != group
                or not architect
                or str(getattr(architect, "kind", "") or "").strip()
                != "architect"
                or str(getattr(architect, "group", "") or "").strip() != group
                or not caller_engineer
                or str(getattr(caller_engineer, "group", "") or "").strip()
                != group
                or not task
                or str(getattr(task, "group", "") or "").strip() != group
            ):
                return "Message thread not found in scope", True
            pinned_hash = str(
                grant.get("task_content_hash", "") or ""
            ).strip()
            current_hash = _task_content_hash(task)
            supplied_hash = str(
                args.get("task_content_hash", "") or ""
            ).strip()
            if current_hash != pinned_hash:
                return json.dumps({
                    "authored_verdict": str(args.get("message", "") or ""),
                    "pinned_task_content_hash": pinned_hash,
                    "current_task_content_hash": current_hash,
                    "reason": "task body changed under reader",
                    "next_step": (
                        "request a fresh task_read_grant from an eligible "
                        "Architect"
                    ),
                }), True
            if not supplied_hash:
                return (
                    "task_content_hash is required for a task_read_grant verdict",
                    True,
                )
            if supplied_hash != pinned_hash:
                return "task_content_hash does not match the pinned grant", True
            grant_hash = pinned_hash
            grant_context = {
                "context_task_ids": [task_id],
                "context_engineer_ids": [caller_id_text],
                "context_snapshot": {
                    "task_read_grant": grant,
                    "task_content_hash": pinned_hash,
                },
            }
        peer_id = str(entry.get("peer_id", "") or "").strip()
        peer = real_state.agents.get(peer_id)
        if caller_kind == "architect":
            peer_kind = str(entry.get("peer_kind", "") or "").strip()
            if tool_name == "peer_reply" and peer_kind == "architect":
                peer, peer_error = _resolve_architect_peer(
                    real_state,
                    caller_id,
                    peer_id,
                )
                if not peer:
                    return peer_error, True
                action = "architect_peer_reply"
            elif tool_name == "engineer_reply" and peer_kind in {"", "engineer"}:
                engineer_id, engineer_error = _resolve_architect_hired_engineer(
                    real_state, caller_id, peer_id
                )
                if not engineer_id:
                    return engineer_error, True
                peer = real_state.agents.get(engineer_id)
                action = "architect_reply"
            else:
                return "Message thread not found in scope", True
        else:
            architect, architect_error = _resolve_architect_for_engineer(
                real_state, caller_id, peer_id
            )
            if not architect:
                return architect_error, True
            peer = architect
            action = "engineer_reply"
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        ack_required = False
        if caller_kind == "engineer" or (
                caller_kind == "architect" and action == "architect_peer_reply"):
            ack_required, ack_error = _optional_bool_arg(args, "ack_required")
            if ack_error:
                return ack_error, True
            if (
                ack_required
                and action == "architect_peer_reply"
                and not _caller_authority_allows_capability(
                    real_state, caller_id, "message.ack_request"
                )
            ):
                return "ack_required=true requires message.ack_request", True
        if caller_kind == "architect" and action == "architect_peer_reply":
            length_error = _validate_architect_peer_message_length(message)
            if length_error:
                return length_error, True
            try:
                saved, created = _save_architect_peer_message(
                    real_state,
                    caller,
                    peer,
                    action=action,
                    message=message,
                    reply_to_id=str(entry.get("id", "") or "").strip(),
                    thread_id=str(entry.get("thread_id", "") or "").strip(),
                    ack_required=ack_required,
                    idempotency_key=idempotency_key,
                )
            except ValueError as exc:
                return str(exc), True
            if created:
                await _inject_architect_peer_message(
                    handle_command,
                    real_state,
                    caller,
                    peer,
                    saved,
                    message,
                )
            return json.dumps({
                "type": "ok",
                "message_id": saved["id"],
                "thread_id": saved["thread_id"],
            }), False
        delivered = _deliver_architect_engineer_message(
            real_state,
            caller,
            peer,
            action=action,
            message=message,
            reply_to_id=str(entry.get("id", "") or "").strip(),
            thread_id=str(entry.get("thread_id", "") or "").strip(),
            ack_required=ack_required,
            context=grant_context,
        )
        await _inject_mcp_message(
            handle_command, real_state, caller, peer, delivered, message
        )
        response = {
            "type": "ok",
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
        }
        if grant_hash:
            response["task_content_hash"] = grant_hash
        return json.dumps(response), False

    if tool_name == "agent_message":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        reply_required, reply_error = _optional_bool_arg(
            args,
            "reply_required",
            True,
        )
        if reply_error:
            return reply_error, True

        result = await handle_command({
            "cmd": "engineer_message",
            "agent_id": agent_id,
            "message": args.get("message", ""),
            "reply_required": reply_required,
            "sender_agent_id": (
                str(getattr(_engineer_cell, "id", "") or "").strip()
                or str(caller_id or "").strip()
            ),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "ask" and caller_kind == "architect":
        question = str(args.get("question", "") or "").strip()
        if not question:
            return "Question is required", True
        architect = real_state.agents.get(str(caller_id or "").strip())

        create_result = await handle_command({
            "cmd": "board_add_task",
            "task": question,
            "description": str(args.get("description", "") or ""),
            "group": _engineer_group,
            "lane": "Backlog",
            "labels": ["torque:human", "architect-ask"],
        })
        if create_result and create_result.get("type") == "error":
            return create_result.get("message", "Unknown error"), True

        task_id = str((create_result or {}).get("task_id", "") or "").strip()
        if not task_id:
            return "Failed to create architect ask task", True

        update_result = await handle_command({
            "cmd": "board_update_task",
            "id": task_id,
            "created_by_architect_id": str(caller_id or "").strip(),
            "reply_agent_id": str(caller_id or "").strip(),
            "assigned_engineer_id": "",
            "agent_id": "",
            "action_name": "",
            "status": "Awaiting Input",
        })
        if update_result and update_result.get("type") == "error":
            return update_result.get("message", "Unknown error"), True
        save_direct_ask_mirror(
            real_state,
            architect,
            question,
            source_task_id=task_id,
        )

        return json.dumps({
            "type": "ok",
            "task_id": task_id,
            "status": "Awaiting Input",
            "labels": ["torque:human", "architect-ask"],
        }), False

    if tool_name == "ask":
        question = args.get("question", "").strip()
        if not question:
            return "Question is required", True

        result = await handle_command({
            "cmd": "engineer_ask",
            "group": _engineer_group,
            "question": question,
            "engineer_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Question posted to the Agent panel. Event pushes have "
            "been paused. The human will see your question and reply "
            "via the panel or directly in this terminal.\n\n"
            f"After the human responds, call "
            f"{tool_name_with_prefix(tool_prefix, 'resume')} to unpause "
            "event delivery."
        ), False

    if tool_name == "note":
        message = args.get("message", "").strip()
        if not message:
            return "Message is required", True
        kind = args.get("kind", "note")
        if kind not in {"note", "question"}:
            return "kind must be 'note' or 'question'", True

        result = await handle_command({
            "cmd": "engineer_note",
            "group": _engineer_group,
            "message": message,
            "kind": kind,
            "engineer_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Note posted to the Agent panel without pausing event "
            "delivery. It will remain visible until dismissed."
        ), False

    if tool_name == "agent_close":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        target_cell = real_state.agents.get(agent_id) or state.agents.get(agent_id)
        if caller_kind == "engineer" and is_architect_execution_target(target_cell):
            return engineer_architect_close_denied_message(target_cell), True
        result = await handle_command({
            "cmd": "remove_agent",
            "id": agent_id,
            "_engineer_close_id": (
                str(caller_id or "").strip()
                if caller_kind == "engineer" else ""
            ),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Agent closed"}), False

    if tool_name == "agent_relaunch":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell:
            return f"Agent not found: {agent_ident}", True
        if cell.status != "stopped":
            return f"Agent is not stopped (status: {cell.status})", True
        result = await handle_command({
            "cmd": "relaunch_agent",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": f"Agent {cell.slug} relaunched"}), False

    # -- Worktree tools -----------------------------------------------------

    return UNHANDLED
