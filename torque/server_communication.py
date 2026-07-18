"""Durable user, Engineer, Architect, and cross-kind message orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict
from typing import Optional

from .commands.board import _resolve_task_id
from .config import log
from .db import canonical_user_agent_thread_id
from .direct_message_mirrors import save_direct_ask_reply_mirror
from .identity import agent_identity_anchor
from .server_agent_common import _resolve_agent_id, _should_show_guidance_hint
from .server_agent_operations import _agent_dismissed_at
from .server_artifacts import describe_task_artifact_for_digest
from .state import BoardTask, MatrixState, task_counts_as_done, task_is_closed


GUIDANCE_HINT_USER_DIRECT_REPLY = "user_message.reply_hint"
USER_AGENT_LOOP_MIN_INTERVAL_SECONDS = 60
USER_AGENT_LOOP_MAX_INTERVAL_SECONDS = 24 * 60 * 60
USER_AGENT_LOOP_MAX_MESSAGE_CHARS = 4000


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
            f'Reply with: agent_reply(task="{task_id}", '
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
    engineer_peer_message = (
        sender_kind_key == "engineer"
        and recipient_kind_key == "engineer"
    )
    if engineer_peer_message:
        reply_tool = "mcp__torque__peer_reply"
    elif (
        sender_kind_key == "architect"
        and recipient_kind_key == "architect"
    ):
        reply_tool = "mcp__torque__peer_reply"
    else:
        reply_tool = "mcp__torque__agent_reply"
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
    if engineer_peer_message:
        blocks.append(
            "Inspect referenced context with: "
            f'mcp__torque__peer_context(message_id="{message_id}")'
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
    has_reply = bool(str(row.get("reply_to_id", "") or "").strip())
    if sender_kind == "architect" and recipient_kind == "engineer":
        action = "architect_reply" if has_reply else "architect_message"
    elif sender_kind == "engineer" and recipient_kind == "architect":
        action = "engineer_reply" if has_reply else "engineer_message_architect"
    elif sender_kind == "engineer" and recipient_kind == "engineer":
        action = "engineer_peer_reply" if has_reply else "engineer_peer_notify"
    else:
        action = "architect_peer_reply" if has_reply else "architect_peer_message"
    return {
        "id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "action": action,
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
        "architect_message",
        "architect_reply",
        "engineer_message_architect",
        "engineer_reply",
        "engineer_peer_notify",
        "engineer_peer_reply",
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

def _parse_user_agent_loop_interval(raw: str) -> tuple[int, str]:
    """Parse a bounded /loop interval token into seconds."""
    token = str(raw or "").strip().lower()
    match = re.fullmatch(r"(\d+)\s*([smh])", token)
    if not match:
        return 0, "Interval must look like 1m, 10m, or 2h"
    amount = int(match.group(1))
    unit = match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
    seconds = amount * multiplier
    if seconds < USER_AGENT_LOOP_MIN_INTERVAL_SECONDS:
        return (
            0,
            (
                "Interval must be at least "
                f"{USER_AGENT_LOOP_MIN_INTERVAL_SECONDS // 60}m"
            ),
        )
    if seconds > USER_AGENT_LOOP_MAX_INTERVAL_SECONDS:
        return 0, "Interval must be 24h or less"
    return seconds, ""

def _format_user_agent_loop_interval(seconds: int) -> str:
    value = max(0, int(seconds or 0))
    if value and value % 3600 == 0:
        amount = value // 3600
        return f"{amount}h"
    if value and value % 60 == 0:
        amount = value // 60
        return f"{amount}m"
    return f"{value}s"

def _parse_user_agent_loop_command(message_text: str) -> dict:
    """Parse the tiny supported /loop grammar.

    Supported syntax:
      /loop every <interval> <message>
      /loop cancel
    """
    raw = str(message_text or "").strip()
    if raw == "/loop cancel":
        return {"action": "cancel"}
    match = re.fullmatch(r"/loop\s+every\s+(\S+)\s+([\s\S]+)", raw)
    if not match:
        return {
            "type": "error",
            "message": (
                "Usage: /loop every <interval> <message> "
                "(for example: /loop every 10m check status), "
                "or /loop cancel"
            ),
        }
    seconds, error = _parse_user_agent_loop_interval(match.group(1))
    if error:
        return {"type": "error", "message": error}
    message = str(match.group(2) or "").strip()
    if not message:
        return {"type": "error", "message": "Loop message is required"}
    if len(message) > USER_AGENT_LOOP_MAX_MESSAGE_CHARS:
        return {
            "type": "error",
            "message": (
                "Loop message is too long "
                f"(max {USER_AGENT_LOOP_MAX_MESSAGE_CHARS} characters)"
            ),
        }
    return {"action": "create", "interval_seconds": seconds, "message": message}

def _user_direct_message_reply_tool(recipient_kind: str) -> str:
    del recipient_kind
    return "user_message"

def _format_user_direct_message_prompt(
        row: dict,
        recipient_kind: str,
        *,
        include_free_text_reply_hint: bool = True,
        state: MatrixState | None = None) -> str:
    """Format a durable user→agent message as an injected agent prompt."""
    row = row or {}
    if str(row.get("message_type", "") or "").strip() == "ask_reply":
        return _format_ask_reply_direct_message_prompt(state, row)
    message_id = str(row.get("id", "") or "").strip()
    message = str(row.get("message", "") or "").strip("\n")
    message_type = str(row.get("message_type", "") or "").strip()
    context_snapshot = row.get("context_snapshot", {})
    if not isinstance(context_snapshot, dict):
        context_snapshot = {}
    if (
        message_type == "slash_command"
        and context_snapshot.get("slash_command") == "compact"
    ):
        return message
    tool_name = _user_direct_message_reply_tool(recipient_kind)
    reply_arg = json.dumps(message_id)
    parts = [
        "## Message from the User",
        "",
        f"Message ID: `{message_id}`",
        "",
    ]
    if message_type == "loop":
        parts.extend([
            "This message was sent by a user-scheduled /loop.",
            "If the loop is no longer actionable, stop it with:",
            "  mcp__torque__user_message_loop_stop(reason=\"...\")",
            "",
        ])
    if message:
        parts.extend([message, ""])
    parts.extend([
        "Reply to this user-facing conversation with:",
        f"  mcp__torque__{tool_name}(message=\"...\", reply_to_id={reply_arg})",
        "",
    ])
    if include_free_text_reply_hint:
        parts.append(
            "Do not rely on free-text terminal output for the user-facing reply."
        )
    parts.append("---")
    return "\n".join(parts) + "\n"

def _ask_reply_question_from_direct_row(
        state: MatrixState | None,
        row: dict) -> str:
    """Recover the original ask question for a durable ask-reply row."""
    row = row or {}
    reply_to_id = str(row.get("reply_to_id", "") or "").strip()
    db = getattr(state, "db", None) if state else None
    loader = getattr(db, "load_direct_message", None) if db else None
    if reply_to_id and callable(loader):
        try:
            ask_row = loader(reply_to_id)
        except Exception:
            log.exception("Failed to load direct ask row %s", reply_to_id)
            ask_row = None
        question = str((ask_row or {}).get("message", "") or "").strip()
        if question:
            return question
    source_task_id = str(row.get("source_task_id", "") or "").strip()
    task = (
        getattr(state, "board_tasks", {}).get(source_task_id)
        if state and source_task_id else None
    )
    return str(getattr(task, "task", "") or "").strip()

def _format_ask_reply_direct_message_prompt(
        state: MatrixState | None,
        row: dict) -> str:
    """Format a durable ask answer for delivery/replay to the asking agent."""
    answer = str((row or {}).get("message", "") or "").strip()
    question = _ask_reply_question_from_direct_row(state, row)
    parts = ["## Human Reply", ""]
    if question:
        parts.extend([
            "Answer to your question:",
            "",
            f"Question:\n{question}",
            "",
            f"Answer:\n{answer}",
        ])
    else:
        parts.append(answer)
    parts.extend(["", "---"])
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
    if target and state.agent_is_tombstoned(target):
        return state.update_direct_message_delivery(
            message_id,
            "buffered",
            reason="agent_tombstoned",
            emit=emit,
        ) or row
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
        state=state,
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

def _save_user_agent_system_audit_message(
        state: MatrixState,
        target,
        message: str,
        *,
        context_snapshot: dict | None = None,
        now: float | None = None) -> dict | None:
    """Persist a visible system/audit row in the user↔agent DM lane."""
    if not getattr(state, "db", None) or not target:
        return None
    ts = float(now if now is not None else time.time())
    row = {
        "id": "msg-" + uuid.uuid4().hex[:12],
        "thread_id": canonical_user_agent_thread_id(target.id),
        "reply_to_id": "",
        "idempotency_key": "",
        "group_name": str(getattr(target, "group", "") or "").strip(),
        "sender_id": "system",
        "sender_kind": "system",
        "sender_name": "System",
        "recipient_id": target.id,
        "recipient_kind": str(getattr(target, "kind", "") or "").strip()
            or "worker",
        "recipient_name": str(getattr(target, "name", "") or "").strip(),
        "message": str(message or "").strip(),
        "message_type": "system",
        "created_at": ts,
        "ack_required": False,
        "blocking": False,
        "context_snapshot": dict(context_snapshot or {}),
        "delivery_state": "delivered",
        "delivery_reason": "",
        "delivered_at": ts,
    }
    return state.save_direct_message(row)

def _save_user_agent_loop_audit_message(
        state: MatrixState,
        target,
        message: str,
        *,
        loop_id: str = "",
        status: str = "",
        now: float | None = None) -> dict | None:
    return _save_user_agent_system_audit_message(
        state,
        target,
        message,
        context_snapshot={
            "loop_id": str(loop_id or "").strip(),
            "loop_status": str(status or "").strip(),
        },
        now=now,
    )

def _user_agent_loop_response(loop, *, audit_row: dict | None = None) -> dict:
    payload = {
        "type": "agent_message_loop",
        "loop": asdict(loop) if loop else {},
    }
    if audit_row:
        payload["audit_message_id"] = str(audit_row.get("id", "") or "")
    return payload

def _handle_user_agent_loop_command(
        data: dict,
        state: MatrixState,
        target,
        message_text: str) -> dict:
    parsed = _parse_user_agent_loop_command(message_text)
    if parsed.get("type") == "error":
        return parsed
    action = parsed.get("action")
    if action == "cancel":
        loop = state.active_agent_message_loop_for_agent(target.id)
        if not loop:
            return {
                "type": "error",
                "message": "No active /loop exists for this agent",
            }
        stopped = state.agent_message_loop_stop(
            loop.id,
            status="cancelled",
            stopped_by="user",
            reason="Cancelled by user",
        )
        audit = _save_user_agent_loop_audit_message(
            state,
            target,
            "User cancelled /loop.",
            loop_id=loop.id,
            status="cancelled",
        )
        return _user_agent_loop_response(stopped, audit_row=audit)
    if action != "create":
        return {"type": "error", "message": "Unsupported /loop command"}
    try:
        loop = state.agent_message_loop_add(
            agent_id=target.id,
            group_name=str(getattr(target, "group", "") or "").strip(),
            interval_seconds=int(parsed["interval_seconds"]),
            message=str(parsed["message"]),
            created_by="user",
        )
    except ValueError as exc:
        return {"type": "error", "message": str(exc)}
    interval_label = _format_user_agent_loop_interval(loop.interval_seconds)
    audit = _save_user_agent_loop_audit_message(
        state,
        target,
        (
            f"User started /loop every {interval_label}: "
            f"{loop.message}"
        ),
        loop_id=loop.id,
        status="active",
    )
    return _user_agent_loop_response(loop, audit_row=audit)

def _restore_user_agent_restart_focus(state: MatrixState,
                                      target_id: str,
                                      selected_before: str) -> None:
    """Keep the selected DM target stable if restart internals disturb it."""
    target_id = str(target_id or "").strip()
    selected_before = str(selected_before or "").strip()
    if not target_id or selected_before != target_id:
        return
    if str(getattr(state, "selected_agent_id", "") or "").strip() == target_id:
        return
    state.selected_agent_id = target_id
    state._emit(
        "ui_update",
        key="selected_agent_id",
        value=state.selected_agent_id,
    )
    state._db_save_ui("selected_agent_id", state.selected_agent_id)

def _user_agent_restart_response(target,
                                 *,
                                 status: str,
                                 requested_row: dict | None = None,
                                 audit_row: dict | None = None,
                                 message: str = "") -> dict:
    payload = {
        "type": "agent_restart",
        "agent_id": str(getattr(target, "id", "") or "").strip(),
        "status": str(status or "").strip(),
    }
    if message:
        payload["message"] = str(message)
    if requested_row:
        payload["requested_message_id"] = str(
            requested_row.get("id", "") or "")
    if audit_row:
        payload["audit_message_id"] = str(audit_row.get("id", "") or "")
    return payload

async def _handle_user_agent_restart_command(
        data: dict,
        state: MatrixState,
        target,
        restart_agent) -> dict:
    """Handle the exact user-DM /restart slash command for one target."""
    del data  # Currently no command arguments are accepted for /restart.
    requested = _save_user_agent_system_audit_message(
        state,
        target,
        f"User requested /restart for {target.name or target.id}.",
        context_snapshot={
            "slash_command": "restart",
            "restart_status": "requested",
        },
    )
    if not callable(restart_agent):
        audit = _save_user_agent_system_audit_message(
            state,
            target,
            "User /restart failed: agent restart is unavailable.",
            context_snapshot={
                "slash_command": "restart",
                "restart_status": "failed",
                "restart_reason": "restart_unavailable",
            },
        )
        payload = _user_agent_restart_response(
            target,
            status="failed",
            requested_row=requested,
            audit_row=audit,
            message="Agent restart is unavailable",
        )
        payload["type"] = "error"
        return payload

    if _agent_dismissed_at(target):
        audit = _save_user_agent_system_audit_message(
            state,
            target,
            "User /restart failed: target agent is dismissed.",
            context_snapshot={
                "slash_command": "restart",
                "restart_status": "failed",
                "restart_reason": "agent_dismissed",
            },
        )
        payload = _user_agent_restart_response(
            target,
            status="failed",
            requested_row=requested,
            audit_row=audit,
            message="Target agent is dismissed",
        )
        payload["type"] = "error"
        return payload

    selected_before = str(getattr(state, "selected_agent_id", "") or "").strip()
    try:
        result = await restart_agent({
            "cmd": "restart_agent",
            "id": target.id,
        })
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__ or "restart_failed"
        log.exception(
            "User-DM /restart failed for '%s'",
            getattr(target, "id", ""),
        )
        audit = _save_user_agent_system_audit_message(
            state,
            target,
            f"User /restart failed: {reason}",
            context_snapshot={
                "slash_command": "restart",
                "restart_status": "failed",
                "restart_reason": reason,
            },
        )
        payload = _user_agent_restart_response(
            target,
            status="failed",
            requested_row=requested,
            audit_row=audit,
            message=reason,
        )
        payload["type"] = "error"
        return payload

    if isinstance(result, dict) and result.get("type") == "error":
        reason = str(result.get("message", "") or "Restart failed").strip()
        audit = _save_user_agent_system_audit_message(
            state,
            target,
            f"User /restart failed: {reason}",
            context_snapshot={
                "slash_command": "restart",
                "restart_status": "failed",
                "restart_reason": reason,
            },
        )
        payload = _user_agent_restart_response(
            target,
            status="failed",
            requested_row=requested,
            audit_row=audit,
            message=reason,
        )
        payload["type"] = "error"
        return payload

    _restore_user_agent_restart_focus(state, target.id, selected_before)
    audit = _save_user_agent_system_audit_message(
        state,
        target,
        f"User /restart succeeded for {target.name or target.id}.",
        context_snapshot={
            "slash_command": "restart",
            "restart_status": "succeeded",
        },
    )
    return _user_agent_restart_response(
        target,
        status="succeeded",
        requested_row=requested,
        audit_row=audit,
    )

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
                updated = state.update_peer_message_delivery(
                    message_id,
                    "buffered",
                    reason="replay_failed",
                    emit=False,
                )
                if not updated:
                    _mark_cross_kind_message_delivery(
                        target,
                        message_id,
                        delivered=False,
                        reason="replay_failed",
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
            updated = state.update_peer_message_delivery(
                message_id,
                "delivered",
                emit=False,
            )
            if not updated:
                _mark_cross_kind_message_delivery(
                    target,
                    message_id,
                    delivered=True,
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

def _ask_reply_target_for_task(state: MatrixState, task) -> tuple[object | None, str]:
    """Return the logical agent that should receive an ask answer."""
    target_id = str(getattr(task, "reply_agent_id", "") or "").strip()
    parent = None
    if not target_id:
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        parent = state.board_tasks.get(parent_id) if parent_id else None
        target_id = str(getattr(parent, "agent_id", "") or "").strip()
    target = state.agents.get(target_id) if target_id else None
    return target, target_id

async def _resolve_human_ask_task(
        state: MatrixState,
        task,
        answer: str,
        send_prompt,
        *,
        panel_event=None) -> dict:
    """Resolve a worker/engineer blocking ask with durable answer delivery."""
    answer = str(answer or "").strip()
    if not task:
        return {"type": "error", "message": "Task not found"}
    if "torque:human" not in (getattr(task, "labels", []) or []):
        return {"type": "error", "message": "Not an ask task"}
    if not answer:
        return {"type": "error", "message": "Answer is required"}
    parent = state.board_tasks.get(str(getattr(task, "parent_task_id", "") or ""))
    if not parent:
        return {"type": "error", "message": "Parent task not found"}

    agent, target_id = _ask_reply_target_for_task(state, task)
    if not agent or str(getattr(agent, "cell_type", "") or "") != "agent":
        return {
            "type": "error",
            "message": (
                "Ask target agent not found"
                + (f": {target_id}" if target_id else "")
            ),
        }

    question = str(getattr(task, "task", "") or "")
    reply_row = save_direct_ask_reply_mirror(
        state,
        agent,
        answer,
        question=question,
        source_task_id=str(getattr(task, "id", "") or ""),
        created_at=time.time(),
        delivery_state="buffered",
    )
    delivery = None
    if reply_row:
        delivery = await _queue_user_direct_message_to_agent(
            state,
            agent,
            reply_row,
            send_prompt,
            emit=True,
        )
    else:
        prompt = _format_ask_reply_direct_message_prompt(
            state,
            {
                "message": answer,
                "message_type": "ask_reply",
                "source_task_id": str(getattr(task, "id", "") or ""),
            },
        )
        queued = await _queue_cell_prompt_send(
            agent,
            prompt,
            send_prompt,
            prime_input_ready=True,
            settled_submit=True,
            wait_for_delivery=True,
        )
        if not queued:
            return {
                "type": "error",
                "message": (
                    "Ask answer delivery unavailable: target agent has no "
                    "session and the direct message store is unavailable"
                ),
            }

    if not task_is_closed(task):
        state.board_move_task(task.id, "Done")
    messages = list(getattr(task, "messages", []) or [])
    messages.append({
        "timestamp": time.time(),
        "action": "ask_reply",
        "message": answer,
        "agent_name": "Human",
    })
    state.board_update_task(task.id, status="", messages=messages)
    state._clear_parent_awaiting_input(parent, exclude_task_id=task.id)

    q = question
    if len(q) > 120:
        q = q[:120] + "…"
    if panel_event:
        panel_event(
            "ask_resolved",
            agent.id,
            agent.name,
            agent.group,
            "Resolved: " + (q or task.id),
            task_id=task.id,
        )
    current = delivery or reply_row or {}
    return {
        "type": "ok",
        "task_id": task.id,
        "agent_id": agent.id,
        "message_id": str(current.get("id", "") or ""),
        "delivery_state": str(
            current.get("delivery_state", "") or (
                "delivered" if not reply_row else "buffered"
            )
        ),
        "delivery_reason": str(current.get("delivery_reason", "") or ""),
    }

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
