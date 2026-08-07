"""Cross-kind, peer, feedback, and user-direct message delivery helpers."""

import hashlib
import json
import re
import time
import uuid

from torque.config import log
from torque.commands.task_dispatch import (
    ACTION_BINDING_REQUIRED_FOR_DISPATCH,
    task_has_action_binding,
)
from torque.persistence.agent_history import canonical_user_agent_thread_id
from torque.identity import prepend_agent_identity_anchor
from torque.mcp_engineer_tools.shared import resolve_task as _resolve_task
from torque.mcp_scoped.architect_reports import _compact_json
from torque.mcp_scoped.common import (
    _agent_dismissed_at,
    _agent_is_tombstoned,
    _architect_visible_engineers,
    _effective_assigned_engineer_id,
    _is_architect_cell,
    _resolve_architect_hired_engineer,
    _task_created_by_classifier,
)
from torque.mcp_scoped.peer_context import (
    _engineer_peer_hiring_architect_id,
)
from torque.mcp_scoped.peer_inbox import (
    _engineer_peer_existing_message_matches_pair,
    _engineer_peer_thread_belongs_to_pair,
    _peer_message_id_from_idempotency_key,
    _validate_architect_peer_message_length,
)
from torque.mcp_scoped.proposals import (
    _architect_task_owned_by_caller,
    _engineer_created_task_handoff_refusal,
    _routed_product_proposal_root_pickup_authorization,
)
from torque.server_prompts import build_engineer_deliverable_awareness
from torque.state import board_task_is_closed

_ARCHITECT_FEEDBACK_DEFAULT_CATEGORIES = (
    "What worked well?",
    "What slowed you down?",
    "What should we change next wave?",
    "Risks or follow-ups the architect should track.",
)

_ARCHITECT_FEEDBACK_DEFAULT_PROMPT = (
    "Please reply in this thread with concise retrospective feedback for "
    "the latest wave."
)

_ARCHITECT_FEEDBACK_REQUEST_ID_RE = re.compile(
    r"\bfeedback(?:_request)?_id\s*[:=]\s*([A-Za-z0-9_.:-]{1,80})\b",
    re.IGNORECASE,
)

_ARCHITECT_FEEDBACK_REQUEST_ID_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

_ARCHITECT_FEEDBACK_STATUS_LOAD_LIMIT = 1000

_TASK_ID_REFERENCE_RE = re.compile(
    r"\b[A-Z][A-Z0-9_]*:[1-9][0-9]*(?::[1-9][0-9]*)?\b"
)

_ARCHITECT_EXPLICIT_DISPATCH_ADVISORY = (
    "This message referenced one eligible staged task but did not dispatch it. "
    "Pass task=<task id or slug> explicitly to dispatch."
)

_TASK_SLUG_REFERENCE_BOUNDARY_RE = r"[A-Za-z0-9_-]"

def _append_cross_kind_message(cell, entry: dict) -> None:
    if not cell:
        return
    message_id = str((entry or {}).get("id", "") or "").strip()
    if message_id:
        cell.mcp_messages[:] = [
            dict(item)
            for item in (cell.mcp_messages or [])
            if str((item or {}).get("id", "") or "").strip() != message_id
        ]
    cell.mcp_messages.insert(0, dict(entry))
    if len(cell.mcp_messages) > 20:
        cell.mcp_messages[:] = cell.mcp_messages[:20]


def mark_cross_kind_message_delivery(cell, message_id: str, *,
                                     delivered: bool,
                                     reason: str = "") -> bool:
    """Update the recipient-side cross-kind inbox delivery marker."""
    if not cell:
        return False
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    updated = False
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if str((entry or {}).get("id", "") or "").strip() != message_id:
            continue
        entry["delivered"] = bool(delivered)
        entry["buffered"] = not bool(delivered)
        if reason:
            entry["delivery_reason"] = str(reason or "").strip()
        elif "delivery_reason" in entry:
            entry.pop("delivery_reason", None)
        updated = True
        break
    return updated


def _load_message_entry(cell, message_id: str) -> tuple[dict | None, str]:
    message_id = str(message_id or "").strip()
    if not message_id:
        return None, "message_id is required"
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if str((entry or {}).get("id", "") or "").strip() == message_id:
            return dict(entry), ""
    return None, "Message not found"


def _sanitize_mcp_worker_provider_override(
    state,
    group: str,
    engineer_id: str,
    provider: str,
) -> str:
    provider = str(provider or "").strip()
    if not provider:
        return ""
    settings = state.get_engineer_settings(group)
    if getattr(settings, "engineer_can_override_worker_provider", True):
        return provider
    log.warning(
        "Engineer %s attempted worker provider override '%s' in group %s "
        "while provider overrides are disabled; falling back to group default",
        engineer_id,
        provider,
        group,
    )
    return ""


def _mcp_worker_provider_override_arg(args: dict) -> tuple[str, str]:
    """Return requested provider override and an error message if ambiguous."""
    provider = str(args.get("provider", "") or "").strip()
    agent_type = str(args.get("agent_type", "") or "").strip()
    if provider and agent_type and provider != agent_type:
        return "", (
            "provider and agent_type overrides disagree; use one provider "
            "value for the new worker"
        )
    return provider or agent_type, ""


def _resolve_exact_task_reference(state, task_ident: str) -> str:
    """Resolve a message token as an exact task ID/alias, never as a prefix."""
    ident = str(task_ident or "").strip()
    if not ident:
        return ""
    resolver = getattr(state, "resolve_board_task_id", None)
    if callable(resolver):
        return str(resolver(ident, allow_prefix=False) or "").strip()
    alias_resolver = getattr(state, "resolve_task_alias", None)
    if callable(alias_resolver):
        aliased = str(alias_resolver(ident) or "").strip()
        if aliased != ident:
            return aliased if aliased in getattr(state, "board_tasks", {}) else ""
    return ident if ident in getattr(state, "board_tasks", {}) else ""


def _message_mentions_task_slug(message_text: str, slug: str) -> bool:
    slug = str(slug or "").strip()
    if not slug:
        return False
    pattern = (
        rf"(?<!{_TASK_SLUG_REFERENCE_BOUNDARY_RE})"
        rf"{re.escape(slug)}"
        rf"(?!{_TASK_SLUG_REFERENCE_BOUNDARY_RE})"
    )
    return bool(re.search(pattern, str(message_text or ""), re.IGNORECASE))


def _deliverable_awareness_for_referenced_tasks(state, message_text: str) -> str:
    """Return awareness blocks for any deliverable tasks referenced in text.

    Scans ``message_text`` for canonical task IDs (e.g. ``TORQUE:241``),
    resolves them via the board (including legacy alias lookups) and
    concatenates an awareness block for each task that carries a
    deliverable contract. Returns ``""`` when nothing matches.
    """
    if not message_text:
        return ""
    seen: set[str] = set()
    blocks: list[str] = []
    aliases = getattr(state, "task_id_aliases", {}) or {}
    for raw in _TASK_ID_REFERENCE_RE.findall(message_text):
        tid = str(aliases.get(raw, raw) or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        task = state.board_tasks.get(tid)
        if not task:
            continue
        block = build_engineer_deliverable_awareness(task)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _deliver_architect_engineer_message(state, sender, recipient, *,
                                        action: str, message: str,
                                        message_id: str = "",
                                        reply_to_id: str = "",
                                        thread_id: str = "",
                                        ack_required: bool = False,
                                        context: dict | None = None) -> dict:
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    timestamp = time.time()
    message_id = (
        str(message_id or "").strip()
        or "msg-" + uuid.uuid4().hex[:12]
    )
    conversation_id = str(thread_id or "").strip() or message_id
    reply_to = str(reply_to_id or "").strip()
    sender_kind = str(getattr(sender, "kind", "") or "").strip()
    recipient_kind = str(getattr(recipient, "kind", "") or "").strip()
    sender_name = str(getattr(sender, "name", "") or "").strip()
    recipient_name = str(getattr(recipient, "name", "") or "").strip()
    group_name = str(getattr(sender, "group", "") or "").strip() or str(
        getattr(recipient, "group", "") or ""
    ).strip()
    context = dict(context or {})

    saved = None
    if getattr(state, "db", None):
        row = {
            "id": message_id,
            "thread_id": conversation_id,
            "reply_to_id": reply_to,
            "group_name": group_name,
            "sender_id": sender.id,
            "sender_kind": sender_kind,
            "sender_name": sender_name,
            "recipient_id": recipient.id,
            "recipient_kind": recipient_kind,
            "recipient_name": recipient_name,
            "message": message_text,
            "message_type": "message",
            "created_at": timestamp,
            "ack_required": bool(ack_required),
            "blocking": False,
            "context_task_ids": list(context.get("context_task_ids", []) or []),
            "context_engineer_ids": list(
                context.get("context_engineer_ids", []) or []
            ),
            "context_decision_ids": list(
                context.get("context_decision_ids", []) or []
            ),
            "context_summary": str(context.get("context_summary", "") or ""),
            "context_snapshot": dict(context.get("context_snapshot", {}) or {}),
            "delivery_state": "buffered",
            "delivery_reason": "",
            "delivered_at": 0,
        }
        save_peer = getattr(state, "save_peer_message", None)
        if callable(save_peer):
            saved = save_peer(row, cache_participants=False)
        else:
            saved = state.db.save_agent_peer_message(row)
    if saved:
        message_id = str(saved.get("id", message_id) or message_id)
        conversation_id = str(
            saved.get("thread_id", conversation_id) or conversation_id
        )
        timestamp = float(saved.get("created_at", timestamp) or timestamp)

    shared = {
        "id": message_id,
        "thread_id": conversation_id,
        "reply_to_id": reply_to,
        "action": action,
        "message": message_text,
        "timestamp": timestamp,
        "group": group_name,
        "sender_id": sender.id,
        "sender_kind": sender_kind,
        "sender_name": sender_name,
        "recipient_id": recipient.id,
        "recipient_kind": recipient_kind,
        "recipient_name": recipient_name,
        "delivery_state": "buffered",
        "delivered": False,
        "buffered": True,
        "context_summary": str(context.get("context_summary", "") or ""),
        "context_task_ids": list(context.get("context_task_ids", []) or []),
        "context_engineer_ids": list(
            context.get("context_engineer_ids", []) or []
        ),
        "context_decision_ids": list(
            context.get("context_decision_ids", []) or []
        ),
    }
    if sender_kind == "engineer" and recipient_kind == "architect":
        shared["ack_required"] = bool(ack_required)
    sender_entry = dict(shared)
    sender_entry.update({
        "peer_id": recipient.id,
        "peer_kind": recipient_kind,
        "direction": "sent",
    })
    recipient_entry = dict(shared)
    if sender_kind == "architect" and recipient_kind == "engineer":
        body = message_text
        awareness = _deliverable_awareness_for_referenced_tasks(
            state, message_text
        )
        if awareness:
            body = f"{message_text}\n\n{awareness}"
        recipient_entry["message"] = prepend_agent_identity_anchor(
            body,
            recipient,
        )
    recipient_entry.update({
        "peer_id": sender.id,
        "peer_kind": sender_kind,
        "direction": "received",
    })
    log.debug(
        "architect-engineer message action=%s sender=%s recipient=%s thread=%s",
        action,
        sender.id,
        recipient.id,
        conversation_id,
    )
    _append_cross_kind_message(sender, sender_entry)
    _append_cross_kind_message(recipient, recipient_entry)
    state.history_record_message(
        sender.id,
        action,
        message_text,
        mark_progress=False,
    )
    state.history_record_message(
        recipient.id,
        action,
        message_text,
        mark_progress=False,
    )
    if recipient_kind == "engineer":
        recipient.pending_engineer_message = True
    if sender_kind == "engineer":
        sender.pending_engineer_message = False
    state._emit_agent(sender)
    state._emit_agent(recipient)
    return shared


def _architect_dispatch_message_for_task(task, message: str) -> str:
    message_text = str(message or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    task_title = str(getattr(task, "task", "") or "").strip()
    header = f"Task {task_id}: {task_title}".strip()
    if message_text:
        if task_id and task_id not in message_text:
            return f"{header}\n\n{message_text}"
        return message_text
    parts = [f"Please pick up {header}.".strip()]
    description = str(getattr(task, "description", "") or "").strip()
    if description:
        parts.append(description)
    return "\n\n".join(part for part in parts if part)


def _resolve_architect_dispatch_task(state, caller_id: str, engineer_id: str,
                                     group: str, task_ident: str
                                     ) -> tuple[object | None, str]:
    task_id = _resolve_task(state, task_ident)
    if not task_id:
        return None, "Task not found"
    task = state.board_tasks.get(task_id)
    if not task or str(getattr(task, "group", "") or "").strip() != group:
        return None, "Task not found"
    caller_id_str = str(caller_id or "").strip()
    creator_class = _task_created_by_classifier(task)
    if creator_class != "user" and not _architect_task_owned_by_caller(
        task,
        caller_id_str,
    ):
        handoff_refusal = _engineer_created_task_handoff_refusal(
            task, caller_id_str,
        )
        if handoff_refusal:
            return None, handoff_refusal
        pickup_authorization, _pickup_error = (
            _routed_product_proposal_root_pickup_authorization(
                state, caller_id_str, task,
            )
        )
        if pickup_authorization:
            return None, (
                f"Task {task_id} was not created by this architect. Run "
                f'task_claim(task="{task_id}") before dispatch.'
            )
        return None, (
            "Task was not created by this architect. No routed pickup is "
            "available to this architect."
        )
    if _effective_assigned_engineer_id(task) != str(engineer_id or "").strip():
        return None, "Task is not assigned to this engineer"
    if board_task_is_closed(task):
        return None, "Task is already closed"
    return task, ""


def _architect_message_needs_explicit_dispatch_advisory(
        state,
        caller_id: str,
        engineer_id: str,
        group: str,
        message: str) -> str:
    """Return an explicit no-dispatch advisory for one referenced staged task.

    This intentionally preserves the former matcher only as a non-mutating
    migration affordance.  It must not select a task for dispatch or expose
    its identity in the response.
    """
    message_text = str(message or "")
    if not message_text:
        return ""
    exact_task_refs = {
        task_id
        for raw in _TASK_ID_REFERENCE_RE.findall(message_text)
        for task_id in [_resolve_exact_task_reference(state, raw)]
        if task_id
    }
    matches: set[str] = set()
    provenance_declines: set[str] = set()
    for task in state.board_tasks.values():
        task_id = str(getattr(task, "id", "") or "").strip()
        if not task_id:
            continue
        if (
            str(getattr(task, "dispatch_state", "") or "queued").strip().lower()
            != "queued"
        ):
            continue
        if task_id not in exact_task_refs:
            slug = str(getattr(task, "slug", "") or "").strip()
            if not slug or not _message_mentions_task_slug(message_text, slug):
                continue
        valid_task, _error = _resolve_architect_dispatch_task(
            state,
            caller_id,
            engineer_id,
            group,
            task_id,
        )
        if valid_task:
            matches.add(valid_task.id)
        elif _error and "engineer provenance" in _error:
            provenance_declines.add(_error)
    if len(matches) == 1:
        return _ARCHITECT_EXPLICIT_DISPATCH_ADVISORY
    if len(provenance_declines) == 1:
        return (
            "No task was dispatched: " + provenance_declines.pop()
            + " Pass task=<task id or slug> only after task_claim succeeds."
        )
    return ""


async def _send_architect_engineer_message(real_state, handle_command,
                                           caller_id: str, args: dict, *,
                                           dispatch_task_id: str = ""):
    engineer_ident = str(args.get("engineer_id", "") or "").strip()
    if not engineer_ident:
        # ``agent`` is the canonical public argument.  ``engineer_id`` is
        # only the translated handler/persistence field and must not leak
        # into caller guidance.
        return None, "agent is required"
    engineer_id, engineer_error = _resolve_architect_hired_engineer(
        real_state, caller_id, engineer_ident
    )
    if not engineer_id:
        return None, engineer_error
    engineer = real_state.agents.get(engineer_id)
    architect = real_state.agents.get(str(caller_id or "").strip())
    message = str(args.get("message", "") or "").strip()
    architect_group = str(getattr(architect, "group", "") or "")
    # Dispatch is an explicit operation.  Message text often references a
    # staged task as context, so it must never select or launch a task.
    # ``dispatch_task_id`` is supplied by task_create(dispatch=true); direct
    # messages opt in through their explicit ``task`` argument.
    task_ident = str(dispatch_task_id or args.get("task", "") or "").strip()
    dispatch_task = None
    if task_ident:
        dispatch_task, task_error = _resolve_architect_dispatch_task(
            real_state,
            caller_id,
            engineer_id,
            architect_group,
            task_ident,
        )
        if not dispatch_task:
            return None, task_error
        if not task_has_action_binding(dispatch_task):
            return None, ACTION_BINDING_REQUIRED_FOR_DISPATCH
    if dispatch_task:
        message = _architect_dispatch_message_for_task(dispatch_task, message)
    if not message:
        return None, "message is required"
    delivered = _deliver_architect_engineer_message(
        real_state,
        architect,
        engineer,
        action="architect_message",
        message=message,
    )
    await _inject_mcp_message(
        handle_command, real_state, architect, engineer, delivered, message
    )
    response = {
        "type": "ok",
        "message_id": delivered["id"],
        "thread_id": delivered["thread_id"],
        "engineer_id": engineer.id,
    }
    if dispatch_task:
        real_state.board_update_task(dispatch_task.id, dispatch_state="live")
        response["task_id"] = dispatch_task.id
        response["dispatch_state"] = "live"
    else:
        dispatch_advisory = _architect_message_needs_explicit_dispatch_advisory(
            real_state,
            caller_id,
            engineer_id,
            architect_group,
            message,
        )
        if dispatch_advisory:
            response["dispatch_advisory"] = dispatch_advisory
    return response, ""


def _normalize_feedback_request_id(value: str) -> tuple[str, str]:
    request_id = str(value or "").strip()
    if not request_id:
        return "feedback-" + uuid.uuid4().hex[:12], ""
    if not _ARCHITECT_FEEDBACK_REQUEST_ID_VALUE_RE.match(request_id):
        return "", (
            "request_id must be 1-80 characters and contain only letters, "
            "numbers, dot, underscore, colon, or dash"
        )
    return request_id, ""


def _normalize_feedback_categories(raw_categories) -> tuple[list[str], str]:
    if raw_categories in (None, ""):
        return list(_ARCHITECT_FEEDBACK_DEFAULT_CATEGORIES), ""
    if not isinstance(raw_categories, list):
        return [], "categories must be an array of strings"
    categories = []
    seen = set()
    for item in raw_categories:
        text = " ".join(str(item or "").split())
        if not text or text in seen:
            continue
        categories.append(text)
        seen.add(text)
    if not categories:
        return [], "categories must include at least one non-empty string"
    if len(categories) > 12:
        return [], "categories must include at most 12 items"
    for category in categories:
        if len(category) > 240:
            return [], "each category must be at most 240 characters"
    return categories, ""


def _feedback_request_id_from_row(row: dict) -> str:
    for source in (
            str((row or {}).get("context_summary", "") or ""),
            str((row or {}).get("message", "") or "")):
        match = _ARCHITECT_FEEDBACK_REQUEST_ID_RE.search(source)
        if match:
            return match.group(1)
    return ""


def _format_engineer_feedback_message(
        request_id: str,
        prompt: str,
        categories: list[str]) -> str:
    parts = [
        "Retrospective feedback request",
        f"feedback_request_id: {request_id}",
        "",
        prompt,
        "",
        "Please reply in this thread with bullets for:",
    ]
    parts.extend(f"- {category}" for category in categories)
    parts.extend([
        "",
        "Keep it compact; no task is being created for this request.",
    ])
    return "\n".join(parts)


def _architect_feedback_hired_engineers(state, caller_id: str) -> list:
    hired = []
    for cell, relation in _architect_visible_engineers(
            state, caller_id, include_tombstoned=False).values():
        if relation != "hired":
            continue
        if _agent_is_tombstoned(state, cell):
            continue
        hired.append(cell)
    hired.sort(
        key=lambda cell: (
            str(getattr(cell, "slug", "") or getattr(cell, "name", "") or "").lower(),
            str(getattr(cell, "id", "") or ""),
        )
    )
    return hired


async def _architect_engineer_feedback_request_json(
        real_state,
        handle_command,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    architect = real_state.agents.get(str(caller_id or "").strip())
    if not architect:
        return "architect not found", True
    raw_request_id = str(args.get("request_id", "") or "").strip()
    request_id, request_id_error = _normalize_feedback_request_id(
        raw_request_id
    )
    if request_id_error:
        return request_id_error, True
    categories, category_error = _normalize_feedback_categories(
        args.get("categories", None)
    )
    if category_error:
        return category_error, True
    prompt = " ".join(str(args.get("prompt", "") or "").split())
    if not prompt:
        prompt = _ARCHITECT_FEEDBACK_DEFAULT_PROMPT
    if len(prompt) > 4000:
        return "prompt must be at most 4000 characters", True

    if raw_request_id and _feedback_request_candidate_rows(
            real_state,
            caller_id,
            request_id=request_id):
        status_text, _status_error = _architect_engineer_feedback_status_json(
            real_state,
            caller_id,
            {"request_id": request_id},
        )
        payload = json.loads(status_text)
        payload["type"] = "engineer_feedback_request"
        payload["deduped"] = True
        return _compact_json(payload), False

    engineers = _architect_feedback_hired_engineers(real_state, caller_id)
    if not engineers:
        return _compact_json({
            "type": "engineer_feedback_request",
            "request_id": request_id,
            "requested_count": 0,
            "requested": [],
            "message": "no hired engineers in scope",
            "categories": categories,
        }), False

    message = _format_engineer_feedback_message(
        request_id,
        prompt,
        categories,
    )
    length_error = _validate_architect_peer_message_length(message)
    if length_error:
        return length_error, True
    requested = []
    for engineer in engineers:
        context = {
            "context_engineer_ids": [engineer.id],
            "context_summary": f"feedback_request_id={request_id}",
            "context_snapshot": {
                "feedback_request": {
                    "request_id": request_id,
                    "categories": list(categories),
                },
            },
        }
        delivered = _deliver_architect_engineer_message(
            real_state,
            architect,
            engineer,
            action="architect_message",
            message=message,
            context=context,
        )
        await _inject_mcp_message(
            handle_command,
            real_state,
            architect,
            engineer,
            delivered,
            message,
        )
        requested.append({
            "engineer_id": engineer.id,
            "engineer_name": str(getattr(engineer, "name", "") or engineer.id),
            "engineer_slug": str(getattr(engineer, "slug", "") or ""),
            "message_id": delivered["id"],
            "thread_id": delivered["thread_id"],
        })
    return _compact_json({
        "type": "engineer_feedback_request",
        "request_id": request_id,
        "prompt": prompt,
        "categories": categories,
        "requested_count": len(requested),
        "requested": requested,
        "tracking": {
            "status_tool": "architect_engineer_feedback_status",
            "reply_detection": "engineer->architect messages in each request thread",
        },
    }), False


def _feedback_request_candidate_rows(
        state,
        caller_id: str,
        *,
        request_id: str = "") -> list[dict]:
    db = getattr(state, "db", None)
    if not db:
        return []
    rows = db.load_agent_peer_messages_for_agent(
        caller_id,
        limit=_ARCHITECT_FEEDBACK_STATUS_LOAD_LIMIT,
    )
    candidates = []
    for row in rows:
        if (
                str(row.get("sender_id", "") or "").strip() != caller_id
                or str(row.get("sender_kind", "") or "").strip() != "architect"
                or str(row.get("recipient_kind", "") or "").strip() != "engineer"):
            continue
        row_request_id = _feedback_request_id_from_row(row)
        if not row_request_id:
            continue
        if request_id and row_request_id != request_id:
            continue
        candidate = dict(row)
        candidate["feedback_request_id"] = row_request_id
        candidates.append(candidate)
    return candidates


def _feedback_status_item_for_request(
        state,
        caller_id: str,
        request_row: dict) -> dict:
    engineer_id = str(request_row.get("recipient_id", "") or "").strip()
    engineer = state.agents.get(engineer_id)
    requested_at = float(request_row.get("created_at", 0) or 0)
    thread_id = str(request_row.get("thread_id", "") or "").strip()
    reply_rows = []
    db = getattr(state, "db", None)
    if db and thread_id:
        for row in db.load_agent_peer_messages_for_thread(thread_id, limit=1000):
            if (
                    str(row.get("sender_id", "") or "").strip() == engineer_id
                    and str(row.get("recipient_id", "") or "").strip() == caller_id
                    and float(row.get("created_at", 0) or 0) > requested_at):
                reply_rows.append(row)
    reply_rows.sort(
        key=lambda row: (
            float(row.get("created_at", 0) or 0),
            str(row.get("id", "") or ""),
        )
    )
    item = {
        "engineer_id": engineer_id,
        "engineer_name": (
            str(getattr(engineer, "name", "") or "").strip()
            or str(request_row.get("recipient_name", "") or "").strip()
            or engineer_id
        ),
        "engineer_slug": str(getattr(engineer, "slug", "") or ""),
        "status": "replied" if reply_rows else "pending",
        "request_message_id": str(request_row.get("id", "") or ""),
        "thread_id": thread_id,
        "requested_at": requested_at,
        "reply_count": len(reply_rows),
    }
    if reply_rows:
        first = reply_rows[0]
        latest = reply_rows[-1]
        item.update({
            "reply_message_id": str(first.get("id", "") or ""),
            "reply_at": float(first.get("created_at", 0) or 0),
            "latest_reply_message_id": str(latest.get("id", "") or ""),
            "latest_reply_at": float(latest.get("created_at", 0) or 0),
        })
    return item


def _architect_engineer_feedback_status_json(
        real_state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    request_id = str(args.get("request_id", "") or "").strip()
    if request_id and not _ARCHITECT_FEEDBACK_REQUEST_ID_VALUE_RE.match(request_id):
        return (
            "request_id must be 1-80 characters and contain only letters, "
            "numbers, dot, underscore, colon, or dash"
        ), True
    candidates = _feedback_request_candidate_rows(
        real_state,
        caller_id,
        request_id=request_id,
    )
    if not candidates:
        return _compact_json({
            "type": "engineer_feedback_status",
            "request_id": request_id,
            "requested_count": 0,
            "replied_count": 0,
            "pending_count": 0,
            "requested": [],
            "replied": [],
            "pending": [],
            "message": "feedback request not found",
        }), False
    if not request_id:
        request_id = max(
            candidates,
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            ),
        )["feedback_request_id"]
        candidates = [
            row for row in candidates
            if row.get("feedback_request_id") == request_id
        ]

    by_engineer: dict[str, dict] = {}
    for row in candidates:
        engineer_id = str(row.get("recipient_id", "") or "").strip()
        current = by_engineer.get(engineer_id)
        if not current or (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
        ) > (
                float(current.get("created_at", 0) or 0),
                str(current.get("id", "") or ""),
        ):
            by_engineer[engineer_id] = row

    requested = [
        _feedback_status_item_for_request(real_state, caller_id, row)
        for row in by_engineer.values()
    ]
    requested.sort(
        key=lambda item: (
            0 if item["status"] == "pending" else 1,
            item["engineer_slug"] or item["engineer_name"] or item["engineer_id"],
            item["engineer_id"],
        )
    )
    replied = [item for item in requested if item["status"] == "replied"]
    pending = [item for item in requested if item["status"] == "pending"]
    return _compact_json({
        "type": "engineer_feedback_status",
        "request_id": request_id,
        "requested_count": len(requested),
        "replied_count": len(replied),
        "pending_count": len(pending),
        "requested": requested,
        "replied": replied,
        "pending": pending,
        "reply_detection": "engineer->architect messages in each request thread",
    }), False


def _load_existing_peer_message_for_idempotency(state, message_id: str) -> dict | None:
    message_id = str(message_id or "").strip()
    if not message_id or not getattr(state, "db", None):
        return None
    return state.db.load_agent_peer_message(message_id)


def _agent_user_direct_message_id_from_idempotency_key(
        idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(
        ("agent-user-message\0" + key).encode("utf-8")
    ).hexdigest()
    return "msg-" + digest[:12]


def _direct_message_agent_kind(cell) -> str:
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind in {"architect", "engineer", "worker"}:
        return kind
    return "worker"


def _load_existing_agent_user_direct_message_for_idempotency(
        state,
        message_id: str) -> dict | None:
    message_id = str(message_id or "").strip()
    db = getattr(state, "db", None)
    if not message_id or not db:
        return None
    loader = getattr(db, "load_direct_message", None)
    if callable(loader):
        return loader(message_id)
    return db.load_agent_peer_message(message_id)


def _agent_user_direct_message_conflicts_with_existing(
        existing: dict,
        sender,
        *,
        message: str,
        reply_to_id: str,
        reply_to_was_explicit: bool = True) -> bool:
    if not existing or not sender:
        return False
    if str(existing.get("sender_id", "") or "").strip() != str(
            getattr(sender, "id", "") or "").strip():
        return True
    if str(existing.get("sender_kind", "") or "").strip() != (
            _direct_message_agent_kind(sender)):
        return True
    if str(existing.get("recipient_kind", "") or "").strip() != "user":
        return True
    if str(existing.get("recipient_id", "") or "").strip() != "user":
        return True
    if str(existing.get("message", "") or "") != str(message or ""):
        return True
    # When the original call omitted reply_to_id and Torque inferred one,
    # idempotent retries must return the existing row instead of treating the
    # now-populated stored reply target as a conflicting argument. Explicit
    # reply_to_id reuse remains strict.
    if reply_to_was_explicit and str(
            existing.get("reply_to_id", "") or "").strip() != str(
                reply_to_id or "").strip():
        return True
    return False


def _requested_user_agent_thread_mismatch(
        requested_thread_id: str,
        agent_id: str) -> tuple[bool, str]:
    """Return whether an explicit canonical user↔agent thread is spoofed.

    V1 direct-message storage always normalizes user lanes to
    ``user-agent:<user-id>:<agent-id>`` based on the persisted sender/recipient.
    Non-canonical legacy/client thread ids are still ignored by the DB layer,
    but an explicit canonical id for a different agent is almost certainly a
    stale or spoofed caller binding and must not be silently re-attributed.
    """
    requested = str(requested_thread_id or "").strip()
    aid = str(agent_id or "").strip()
    prefix = "user-agent:"
    if not requested.startswith(prefix):
        return False, ""
    parts = requested.split(":", 2)
    if len(parts) != 3:
        return False, ""
    user_id = parts[1].strip() or "user"
    requested_agent_id = parts[2].strip()
    expected = canonical_user_agent_thread_id(aid, user_id=user_id)
    if aid and requested_agent_id and requested != expected:
        return True, expected
    return False, ""


def _agent_user_direct_message_reply_thread_id(
        state,
        reply_to_id: str,
        sender_id: str) -> str:
    reply_to_id = str(reply_to_id or "").strip()
    sender_id = str(sender_id or "").strip()
    if not reply_to_id or not sender_id:
        return ""
    db = getattr(state, "db", None)
    loader = getattr(db, "load_direct_message", None) if db else None
    parent = loader(reply_to_id) if callable(loader) else None
    if not parent:
        return ""
    kinds = {
        str(parent.get("sender_kind", "") or "").strip(),
        str(parent.get("recipient_kind", "") or "").strip(),
    }
    if "user" not in kinds:
        return ""
    if sender_id not in {
        str(parent.get("sender_id", "") or "").strip(),
        str(parent.get("recipient_id", "") or "").strip(),
    }:
        return ""
    return str(parent.get("thread_id", "") or "").strip()


def _validate_agent_user_direct_message_reply_to_id(
        state,
        reply_to_id: str,
        sender_id: str) -> tuple[dict | None, str]:
    """Return the parent direct-message row or an error for unsafe replies."""
    reply_to_id = str(reply_to_id or "").strip()
    sender_id = str(sender_id or "").strip()
    if not reply_to_id:
        return None, ""
    db = getattr(state, "db", None)
    loader = getattr(db, "load_direct_message", None) if db else None
    parent = loader(reply_to_id) if callable(loader) else None
    if not parent:
        return None, f"reply_to_id not found: {reply_to_id}"
    kinds = {
        str(parent.get("sender_kind", "") or "").strip(),
        str(parent.get("recipient_kind", "") or "").strip(),
    }
    if "user" not in kinds:
        return None, (
            "reply_to_id must reference a direct user-message row, not an "
            "Architect/Engineer peer thread"
        )
    participant_ids = {
        str(parent.get("sender_id", "") or "").strip(),
        str(parent.get("recipient_id", "") or "").strip(),
    }
    if sender_id not in participant_ids:
        return None, (
            "reply_to_id does not belong to this agent's user lane; pass a "
            "message id from the current architect↔user conversation"
        )
    return parent, ""


def _direct_user_message_response(row: dict, *,
                                  deduped: bool = False) -> dict:
    row = row or {}
    delivery_state = str(row.get("delivery_state", "") or "").strip() \
        or "delivered"
    return {
        "type": "ok",
        "message_id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "agent_id": str(row.get("sender_id", "") or "").strip(),
        "sender_id": str(row.get("sender_id", "") or "").strip(),
        "sender_kind": str(row.get("sender_kind", "") or "").strip(),
        "recipient_id": str(row.get("recipient_id", "") or "").strip(),
        "recipient_kind": str(row.get("recipient_kind", "") or "").strip(),
        "message_type": str(row.get("message_type", "message") or "message"),
        "blocking": bool(row.get("blocking", False)),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivery": {
            "state": delivery_state,
            "reason": str(row.get("delivery_reason", "") or ""),
        },
        "delivered": delivery_state == "delivered",
        "read_at": float(row.get("read_at", 0) or 0),
        "deduped": bool(deduped),
    }


def _notify_agent_user_direct_message(state, row: dict) -> None:
    """Best-effort notification hook for agent→user direct messages.

    Slice 3 owns the MCP write path; Slice 5 wires a concrete
    NotificationManager method.  Keep this hook optional so the durable
    message write never depends on notification delivery.
    """
    manager = getattr(state, "notification_manager", None)
    callback = getattr(manager, "on_direct_user_message", None)
    if not callable(callback):
        callback = getattr(state, "on_direct_user_message", None)
    if not callable(callback):
        return
    try:
        callback(row)
    except Exception:
        log.exception("Failed to notify for direct user message")


def save_agent_user_direct_message_from_mcp(
        state,
        sender,
        *,
        message: str,
        thread_id: str = "",
        reply_to_id: str = "",
        context: dict | None = None,
        idempotency_key: str = "",
        notify: bool = True) -> tuple[dict, bool]:
    """Persist one agent→user direct message from an MCP tool call.

    Returns ``(row, created)``.  Idempotency-derived duplicates return the
    existing row and do not re-notify.
    """
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    if not sender or getattr(sender, "cell_type", "") != "agent":
        raise ValueError("agent not found")
    if not getattr(state, "db", None):
        raise ValueError("Direct message store is unavailable")

    reply_to = str(reply_to_id or "").strip()
    reply_to_was_explicit = bool(reply_to)
    message_id = _agent_user_direct_message_id_from_idempotency_key(
        idempotency_key
    )
    created = False
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
        created = True
    existing = _load_existing_agent_user_direct_message_for_idempotency(
        state,
        message_id,
    )
    if existing:
        if _agent_user_direct_message_conflicts_with_existing(
                existing,
                sender,
                message=message_text,
                reply_to_id=reply_to,
                reply_to_was_explicit=reply_to_was_explicit):
            raise ValueError(
                "idempotency key was reused for a different message_user call"
            )
        append_direct = getattr(state, "append_direct_message_to_caches", None)
        if callable(append_direct):
            append_direct(existing)
        return existing, False
    if not created:
        created = True

    context = dict(context or {})
    sender_id = str(getattr(sender, "id", "") or "").strip()
    if reply_to:
        _parent, reply_error = _validate_agent_user_direct_message_reply_to_id(
            state,
            reply_to,
            sender_id,
        )
        if reply_error:
            raise ValueError(reply_error)
    requested_thread_id = str(thread_id or "").strip()
    mismatched, expected_thread_id = _requested_user_agent_thread_mismatch(
        requested_thread_id,
        sender_id,
    )
    if mismatched:
        raise ValueError(
            "thread_id is for a different user-agent lane; "
            f"expected {expected_thread_id}"
        )
    if not requested_thread_id:
        requested_thread_id = _agent_user_direct_message_reply_thread_id(
            state,
            reply_to,
            sender_id,
        )
    now = time.time()
    row = {
        "id": message_id,
        "thread_id": requested_thread_id,
        "reply_to_id": reply_to,
        "group_name": str(getattr(sender, "group", "") or "").strip(),
        "sender_id": sender.id,
        "sender_kind": _direct_message_agent_kind(sender),
        "sender_name": str(getattr(sender, "name", "") or "").strip(),
        "recipient_id": "user",
        "recipient_kind": "user",
        "recipient_name": "User",
        "message": message_text,
        "message_type": "message",
        "created_at": now,
        "ack_required": False,
        "blocking": False,
        "source_task_id": "",
        "context_task_ids": list(context.get("context_task_ids", []) or []),
        "context_engineer_ids": list(context.get("context_engineer_ids", []) or []),
        "context_decision_ids": list(context.get("context_decision_ids", []) or []),
        "context_summary": str(context.get("context_summary", "") or ""),
        "context_snapshot": dict(context.get("context_snapshot", {}) or {}),
        "delivery_state": "delivered",
        "delivery_reason": "",
        "delivered_at": now,
        "read_at": 0,
    }
    saved = state.save_direct_message(row) if getattr(state, "db", None) else None
    if not saved:
        raise ValueError("failed to save direct message")
    if notify:
        _notify_agent_user_direct_message(state, saved)
    return saved, created


def _save_architect_peer_message(state, sender, recipient, *,
                                 action: str,
                                 message: str,
                                 reply_to_id: str = "",
                                 thread_id: str = "",
                                 ack_required: bool = False,
                                 context: dict | None = None,
                                 idempotency_key: str = "") -> tuple[dict, bool]:
    """Persist a canonical Architect peer message and project UI caches.

    Returns ``(row, created)``.  When an idempotency-derived message id already
    exists the stored row is returned and no audit side effects are repeated.
    """
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    message_id = _peer_message_id_from_idempotency_key(idempotency_key)
    created = False
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
        created = True
    existing = _load_existing_peer_message_for_idempotency(state, message_id)
    if existing:
        state.append_peer_message_to_caches(existing)
        return existing, False
    if not created:
        created = True
    conversation_id = str(thread_id or "").strip() or message_id
    context = dict(context or {})
    row = {
        "id": message_id,
        "thread_id": conversation_id,
        "reply_to_id": str(reply_to_id or "").strip(),
        "group_name": str(getattr(sender, "group", "") or "").strip(),
        "sender_id": sender.id,
        "sender_kind": "architect",
        "recipient_id": recipient.id,
        "recipient_kind": "architect",
        "message": message_text,
        "created_at": time.time(),
        "ack_required": bool(ack_required),
        "context_task_ids": list(context.get("context_task_ids", []) or []),
        "context_engineer_ids": list(context.get("context_engineer_ids", []) or []),
        "context_decision_ids": list(context.get("context_decision_ids", []) or []),
        "context_summary": str(context.get("context_summary", "") or ""),
        "context_snapshot": dict(context.get("context_snapshot", {}) or {}),
        "delivery_state": "buffered",
        "delivery_reason": "",
    }
    saved = state.save_peer_message(row) if getattr(state, "db", None) else None
    if not saved:
        raise ValueError("failed to save peer message")
    state.history_record_message(
        sender.id,
        action,
        message_text,
        mark_progress=False,
    )
    state.history_record_message(
        recipient.id,
        action,
        message_text,
        mark_progress=False,
    )
    return saved, created


def _save_engineer_peer_message(state, sender, recipient, *,
                                action: str,
                                message: str,
                                reply_to_id: str = "",
                                thread_id: str = "",
                                ack_required: bool = False,
                                context: dict | None = None,
                                idempotency_key: str = "") -> tuple[dict, bool]:
    """Persist a canonical Engineer↔Engineer peer notification/reply."""
    message_text = str(message or "").strip()
    if not message_text:
        raise ValueError("message is required")
    message_id = _peer_message_id_from_idempotency_key(idempotency_key)
    created = False
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
        created = True
    requested_thread_id = str(thread_id or "").strip()
    existing = _load_existing_peer_message_for_idempotency(state, message_id)
    if existing:
        if not _engineer_peer_existing_message_matches_pair(
                existing,
                sender.id,
                recipient.id,
                requested_thread_id):
            raise ValueError("idempotency key conflicts with existing peer message")
        state.append_peer_message_to_caches(existing)
        return existing, False
    if not created:
        created = True
    if requested_thread_id:
        ok, error = _engineer_peer_thread_belongs_to_pair(
            state,
            requested_thread_id,
            sender.id,
            recipient.id,
        )
        if not ok:
            raise ValueError(error)
    conversation_id = requested_thread_id or message_id
    context = dict(context or {})
    context_task_ids = list(context.get("context_task_ids", []) or [])
    row = {
        "id": message_id,
        "thread_id": conversation_id,
        "reply_to_id": str(reply_to_id or "").strip(),
        "group_name": str(getattr(sender, "group", "") or "").strip(),
        "sender_id": sender.id,
        "sender_kind": "engineer",
        "sender_name": str(getattr(sender, "name", "") or "").strip(),
        "recipient_id": recipient.id,
        "recipient_kind": "engineer",
        "recipient_name": str(getattr(recipient, "name", "") or "").strip(),
        "message": message_text,
        "created_at": time.time(),
        "ack_required": bool(ack_required),
        "source_task_id": context_task_ids[0] if context_task_ids else "",
        "context_task_ids": context_task_ids,
        "context_engineer_ids": list(context.get("context_engineer_ids", []) or []),
        "context_decision_ids": list(context.get("context_decision_ids", []) or []),
        "context_summary": str(context.get("context_summary", "") or ""),
        "context_snapshot": dict(context.get("context_snapshot", {}) or {}),
        "delivery_state": "buffered",
        "delivery_reason": "",
    }
    saved = state.save_peer_message(row) if getattr(state, "db", None) else None
    if not saved:
        raise ValueError("failed to save peer message")
    state.history_record_message(
        sender.id,
        action,
        message_text,
        mark_progress=False,
    )
    state.history_record_message(
        recipient.id,
        action,
        message_text,
        mark_progress=False,
    )
    return saved, created


def _engineer_peer_digest_message(sender, recipient, row: dict, *,
                                  opened: bool) -> str:
    sender_name = str(getattr(sender, "name", "") or getattr(sender, "id", "") or "")
    recipient_name = str(
        getattr(recipient, "name", "") or getattr(recipient, "id", "") or ""
    )
    label = "peer thread opened" if opened else "peer thread active"
    parts = [f"{sender_name} ↔ {recipient_name}: {label}"]
    context_bits = []
    for task_id in list((row or {}).get("context_task_ids", []) or [])[:2]:
        text = str(task_id or "").strip()
        if text:
            context_bits.append(text)
    snapshot = dict((row or {}).get("context_snapshot", {}) or {})
    for stream in list(snapshot.get("streams", []) or [])[:2]:
        if not isinstance(stream, dict):
            continue
        branch = str(stream.get("branch", "") or "").strip()
        stream_id = str(stream.get("stream_id", "") or "").strip()
        if branch:
            context_bits.append(f"stream {branch}")
        elif stream_id:
            context_bits.append(stream_id)
    if context_bits:
        parts.append(" — " + " / ".join(context_bits[:3]))
    thread_id = str((row or {}).get("thread_id", "") or "").strip()
    if thread_id:
        parts.append(f" (thread {thread_id})")
    message = "".join(parts)
    return message[:240].rstrip()


def _emit_engineer_peer_architect_event(state, sender, recipient, row: dict, *,
                                        opened: bool) -> bool:
    architect_id = _engineer_peer_hiring_architect_id(sender)
    if not architect_id or architect_id != _engineer_peer_hiring_architect_id(recipient):
        return False
    architect = state.agents.get(architect_id)
    if not _is_architect_cell(architect, state) or _agent_is_tombstoned(state, architect):
        return False
    kind = "engineer_peer_thread_opened" if opened else "engineer_peer_thread_active"
    now = time.time()
    thread_id = str((row or {}).get("thread_id", "") or "").strip()
    if not opened:
        settings = state.get_agent_digest_settings(architect_id)
        quiet_floor = max(
            int(getattr(settings, "push_interval", 0) or 0),
            int(getattr(settings, "max_interval", 0) or 0),
            300,
        )
        cache = getattr(state, "_engineer_peer_thread_active_notified_at", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(state, "_engineer_peer_thread_active_notified_at", cache)
        key = (architect_id, thread_id)
        if now - float(cache.get(key, 0) or 0) < quiet_floor:
            return False
        cache[key] = now
    panel_log = getattr(state, "panel_log", None)
    append = getattr(panel_log, "append", None)
    if not callable(append):
        return False
    event = append(
        kind=kind,
        cell_id=str(getattr(sender, "id", "") or "").strip(),
        agent_name=str(getattr(sender, "name", "") or "").strip(),
        group=str(getattr(sender, "group", "") or "").strip(),
        message=_engineer_peer_digest_message(sender, recipient, row, opened=opened),
        task_id=str((row or {}).get("source_task_id", "") or "").strip(),
    )
    emitter = getattr(state, "_emit", None)
    if callable(emitter):
        emitter("event_append", **event)
    return True


async def _inject_architect_peer_message(handle_command, state, sender,
                                         recipient, row: dict,
                                         message: str) -> dict:
    """Inject a durable peer message and persist delivery state."""
    message_id = str((row or {}).get("id", "") or "").strip()
    if _agent_dismissed_at(recipient):
        updated = state.update_peer_message_delivery(
            message_id,
            "buffered",
            reason="recipient_dismissed",
        )
        return {
            "state": str((updated or row).get("delivery_state", "buffered") or "buffered"),
            "reason": str((updated or row).get("delivery_reason", "recipient_dismissed") or ""),
        }
    if not recipient or not handle_command:
        updated = state.update_peer_message_delivery(
            message_id,
            "buffered",
            reason="no_session",
        )
        return {
            "state": str((updated or row).get("delivery_state", "buffered") or "buffered"),
            "reason": str((updated or row).get("delivery_reason", "no_session") or ""),
        }
    try:
        result = await handle_command({
            "cmd": "inject_mcp_message",
            "agent_id": getattr(recipient, "id", ""),
            "message": message,
            "sender_name": str(getattr(sender, "name", "") or "").strip(),
            "sender_kind": str(getattr(sender, "kind", "") or "").strip(),
            "message_id": message_id,
            "ack_required": bool((row or {}).get("ack_required", False)),
        })
    except Exception:
        log.exception(
            "Failed to inject peer message into %s",
            getattr(recipient, "id", ""),
        )
        updated = state.update_peer_message_delivery(
            message_id,
            "failed",
            reason="inject_failed",
        )
        return {
            "state": str((updated or row).get("delivery_state", "failed") or "failed"),
            "reason": str((updated or row).get("delivery_reason", "inject_failed") or ""),
        }

    if isinstance(result, dict) and result.get("type") == "error":
        updated = state.update_peer_message_delivery(
            message_id,
            "failed",
            reason=str(result.get("message", "") or "inject_failed"),
        )
    elif bool(result and result.get("delivered")):
        updated = state.update_peer_message_delivery(message_id, "delivered")
    else:
        updated = state.update_peer_message_delivery(
            message_id,
            "buffered",
            reason=str((result or {}).get("reason", "") or "no_session"),
        )
    current = updated or row
    return {
        "state": str(current.get("delivery_state", "buffered") or "buffered"),
        "reason": str(current.get("delivery_reason", "") or ""),
    }


def _update_cross_kind_peer_delivery(state, message_id: str, *,
                                     delivered: bool,
                                     reason: str = "",
                                     failed: bool = False) -> None:
    updater = getattr(state, "update_peer_message_delivery", None)
    if not callable(updater):
        return
    message_id = str(message_id or "").strip()
    if not message_id:
        return
    if delivered:
        updater(message_id, "delivered", cache_participants=False)
    else:
        updater(
            message_id,
            "failed" if failed else "buffered",
            reason=str(reason or ""),
            cache_participants=False,
        )


async def _inject_mcp_message(handle_command, state, sender, recipient,
                              delivered: dict, message: str) -> None:
    """Ask the server to type the message into the recipient's terminal."""
    if not recipient or not handle_command:
        return
    message_id = str(delivered.get("id", "") or "")
    try:
        payload = {
            "cmd": "inject_mcp_message",
            "agent_id": getattr(recipient, "id", ""),
            "message": message,
            "sender_name": str(getattr(sender, "name", "") or "").strip(),
            "sender_kind": str(getattr(sender, "kind", "") or "").strip(),
            "message_id": message_id,
        }
        if "ack_required" in delivered:
            payload["ack_required"] = bool(delivered.get("ack_required", False))
        result = await handle_command(payload)
        was_delivered = bool(result and result.get("delivered"))
        reason = str((result or {}).get("reason", "") or "")
        mark_cross_kind_message_delivery(
            recipient,
            message_id,
            delivered=was_delivered,
            reason=reason,
        )
        _update_cross_kind_peer_delivery(
            state,
            message_id,
            delivered=was_delivered,
            reason=reason or "no_session",
        )
    except Exception:
        mark_cross_kind_message_delivery(
            recipient,
            message_id,
            delivered=False,
            reason="inject_failed",
        )
        _update_cross_kind_peer_delivery(
            state,
            message_id,
            delivered=False,
            reason="inject_failed",
            failed=True,
        )
        log.exception(
            "Failed to inject MCP message into agent %s",
            getattr(recipient, "id", ""),
        )
