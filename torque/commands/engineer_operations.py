"""Engineer communication, journal, and delivery-control commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import log

ENGINEER_OPERATION_COMMAND_NAMES = frozenset({
    "architect_journal_append",
    "digest_pause",
    "digest_resume",
    "engineer_ask",
    "engineer_dismiss_note",
    "engineer_flush_now",
    "engineer_journal_append",
    "engineer_journal_delete",
    "engineer_journal_read",
    "engineer_message",
    "engineer_note",
    "engineer_pause",
    "engineer_reply",
    "engineer_resume",
    "engineer_session_map_read",
    "engineer_update_settings",
    "inject_mcp_message",
})


@dataclass(slots=True)
class EngineerOperationRuntime:
    agent_dismissed_at: Any
    architect_dismissed_error: Any
    deliver_engineer_reply_and_resume: Any
    format_injected_mcp_message_prompt: Any
    handle_digest_pause_resume_command: Any
    handle_engineer_dismiss_note_command: Any
    handle_engineer_flush_now_command: Any
    panel_event: Any
    pending_question_reply_target: Any
    resolve_agent_id: Any
    send_agent_prompt: Any
    send_engineer_message_to_agent: Any
    agent_identity_anchor: Any
    bridge: Any
    build_engineer_session_map: Any
    critical_idempotency_key: Any
    critical_request_hash: Any
    db: Any
    direct_ask_mirror_source_key: Any
    engineer_buffer: Any
    save_direct_ask_mirror: Any
    state: Any


async def handle_engineer_operation_command(
    data: dict,
    runtime: EngineerOperationRuntime,
) -> dict | None:
    _agent_dismissed_at = runtime.agent_dismissed_at
    _architect_dismissed_error = runtime.architect_dismissed_error
    _deliver_engineer_reply_and_resume = runtime.deliver_engineer_reply_and_resume
    _format_injected_mcp_message_prompt = runtime.format_injected_mcp_message_prompt
    _handle_digest_pause_resume_command = runtime.handle_digest_pause_resume_command
    _handle_engineer_dismiss_note_command = runtime.handle_engineer_dismiss_note_command
    _handle_engineer_flush_now_command = runtime.handle_engineer_flush_now_command
    _panel_event = runtime.panel_event
    _pending_question_reply_target = runtime.pending_question_reply_target
    _resolve_agent_id = runtime.resolve_agent_id
    _send_agent_prompt = runtime.send_agent_prompt
    _send_engineer_message_to_agent = runtime.send_engineer_message_to_agent
    agent_identity_anchor = runtime.agent_identity_anchor
    bridge = runtime.bridge
    build_engineer_session_map = runtime.build_engineer_session_map
    critical_idempotency_key = runtime.critical_idempotency_key
    critical_request_hash = runtime.critical_request_hash
    db = runtime.db
    direct_ask_mirror_source_key = runtime.direct_ask_mirror_source_key
    engineer_buffer = runtime.engineer_buffer
    save_direct_ask_mirror = runtime.save_direct_ask_mirror
    state = runtime.state
    cmd = str(data.get("cmd", "") or "").strip()
    result = None

    if cmd == "engineer_message":
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
                  "engineer_reasoning_effort", "engineer_fast_mode",
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
            if not reply_target:
                result = {"type": "error",
                          "message": f"{target_label} not found"}
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

    return result
