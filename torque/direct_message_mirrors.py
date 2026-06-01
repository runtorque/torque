"""Canonical blocking-ask recipient resolution + direct-message mirrors.

This module is the SINGLE canonical home for "who is this agent's blocking-ask
recipient / is the ask user-destined" (``resolve_ask_recipient``).  Every
surface that needs owner-aware ask routing MUST share this one resolver rather
than re-deriving ownership in parallel:

1. ``torque_ask`` blocking-HITL routing (server.py ``ask`` action): uses
   ``ask_recipient_is_user`` for ``needs_attention`` and
   ``ask_task_labels_for_owner_recipient`` for the ``torque:non-user-ask``
   label.
2. The direct-message mirror rows below the terminal: ``save_direct_ask_mirror``
   stamps the resolved ``recipient_*`` fields onto the persisted row.
3. The EE connector egress (``ee/python`` / ``_wire_kind_for_direct_message_row``):
   it consumes the resolver's OUTPUT as stamped on the row
   (``recipient_kind``/``sender_kind``) to decide whether an ask is user-destined
   and may egress to the ``{kind:"remote-client", id:"user"}`` lane.  It does NOT
   re-derive ownership.
4. P6's remote ask surface consumes the same stamped rows, so it answers exactly
   the asks that are genuinely user-destined -- identical to local behavior.

The ask row itself remains a display mirror.  Ask-reply rows are also the
durable transport record for answer delivery: if the asking agent has no live
session, the row stays buffered and is replayed when the agent wakes.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .config import log
from .db import canonical_user_agent_thread_id


NON_USER_ASK_LABEL = "torque:non-user-ask"


@dataclass(frozen=True)
class DirectMessageParticipant:
    id: str
    kind: str
    name: str


def user_direct_message_participant() -> DirectMessageParticipant:
    return DirectMessageParticipant(id="user", kind="user", name="User")


def participant_is_user(kind, ident) -> bool:
    """Return whether a ``(kind, id)`` pair denotes the canonical user.

    Shared so every surface (ask routing, mirrors, and the connector's
    row-level gate) agrees on what "user-destined" means.
    """
    return (
        str(kind or "").strip() == "user"
        and str(ident or "").strip() == "user"
    )


def agent_direct_message_kind(cell) -> str:
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind in {"architect", "engineer", "worker"}:
        return kind
    return "worker"


def agent_direct_message_participant(cell) -> DirectMessageParticipant:
    if not cell:
        return user_direct_message_participant()
    return DirectMessageParticipant(
        id=str(getattr(cell, "id", "") or "").strip(),
        kind=agent_direct_message_kind(cell),
        name=str(getattr(cell, "name", "") or "").strip(),
    )


def resolve_ask_recipient(state, asking_agent) -> DirectMessageParticipant:
    """Canonical owner-aware resolver: who answers ``asking_agent``'s ask.

    This is THE single resolver shared by ``torque_ask`` routing, the
    direct-message mirror, and (via the stamped row fields) the connector
    egress.  Owner-chain routing falls through to the user only when the
    immediate owner is the user:

    - worker -> owning engineer (``owner_engineer_id``), otherwise user
    - engineer -> hiring architect (``hired_by_architect_id``), otherwise user
    - architect -> user (always)
    """
    if not asking_agent:
        return user_direct_message_participant()
    kind = agent_direct_message_kind(asking_agent)
    if kind == "worker":
        owner_id = str(getattr(asking_agent, "owner_engineer_id", "") or "").strip()
        owner = getattr(state, "agents", {}).get(owner_id) if owner_id else None
        if (
                owner
                and str(getattr(owner, "cell_type", "") or "") == "agent"
                and str(getattr(owner, "kind", "") or "").strip() == "engineer"):
            return agent_direct_message_participant(owner)
        return user_direct_message_participant()
    if kind == "engineer":
        architect_id = str(
            getattr(asking_agent, "hired_by_architect_id", "") or ""
        ).strip()
        architect = (
            getattr(state, "agents", {}).get(architect_id)
            if architect_id else None
        )
        if (
                architect
                and str(getattr(architect, "cell_type", "") or "") == "agent"
                and str(getattr(architect, "kind", "") or "").strip()
                == "architect"):
            return agent_direct_message_participant(architect)
        return user_direct_message_participant()
    return user_direct_message_participant()


# Back-compat alias: ``resolve_ask_recipient`` is the canonical name.
resolve_ask_owner_recipient = resolve_ask_recipient


def ask_recipient_is_user(state, asking_agent) -> bool:
    """Return whether a blocking ask is ultimately addressed to the user.

    Canonical user-destined determination, derived from
    ``resolve_ask_recipient``.  This is the same definition the connector
    egress applies to the resolver-stamped row fields.
    """
    recipient = resolve_ask_recipient(state, asking_agent)
    return participant_is_user(recipient.kind, recipient.id)


# Back-compat alias retained for existing callsites.
ask_owner_recipient_is_user = ask_recipient_is_user


def ask_task_labels_for_owner_recipient(
        state,
        asking_agent,
        labels: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Return ask-task labels annotated with backend-authoritative routing.

    ``torque:human`` remains the compatibility marker for blocking ask tasks
    and resolution tooling.  ``torque:non-user-ask`` marks asks whose
    owner-aware recipient is an agent rather than the user, allowing
    user-facing attention surfaces to suppress their yellow nudge without
    re-deriving ownership in the frontend.
    """
    result = list(labels or [])
    if not ask_recipient_is_user(state, asking_agent):
        if NON_USER_ASK_LABEL not in result:
            result.append(NON_USER_ASK_LABEL)
    return result


def direct_ask_mirror_source_key(
        *,
        source_task_id: str = "",
        group: str = "",
        agent_id: str = "",
        timestamp: float = 0,
        question: str = "") -> str:
    task_id = str(source_task_id or "").strip()
    if task_id:
        return f"task:{task_id}"
    return "\0".join((
        "engineer_ask",
        str(group or "").strip(),
        str(agent_id or "").strip(),
        str(float(timestamp or 0)),
        str(question or "").strip(),
    ))


def _direct_ask_message_id(source_key: str) -> str:
    key = str(source_key or "").strip()
    if not key:
        key = str(time.time())
    digest = hashlib.sha256(("direct-ask\0" + key).encode("utf-8")).hexdigest()
    return "msg-" + digest[:12]


def _direct_ask_reply_message_id(ask_message_id: str) -> str:
    digest = hashlib.sha256(
        ("direct-ask-reply\0" + str(ask_message_id or "").strip()).encode("utf-8")
    ).hexdigest()
    return "msg-" + digest[:12]


def _append_existing_direct_message(state, row: dict | None) -> dict | None:
    if not row:
        return None
    append_direct = getattr(state, "append_direct_message_to_caches", None)
    if callable(append_direct):
        append_direct(row)
    return row


def _load_direct_message(state, message_id: str) -> dict | None:
    db = getattr(state, "db", None)
    loader = getattr(db, "load_direct_message", None) if db else None
    if not callable(loader):
        return None
    return loader(str(message_id or "").strip())


def save_direct_ask_mirror(
        state,
        asking_agent,
        question: str,
        *,
        source_task_id: str = "",
        source_key: str = "",
        created_at: float | None = None) -> dict | None:
    """Persist or return a display-only ``message_type='ask'`` mirror row."""
    if not state or not getattr(state, "db", None) or not asking_agent:
        return None
    question_text = str(question or "").strip()
    if not question_text:
        return None
    sender = agent_direct_message_participant(asking_agent)
    if not sender.id:
        return None
    key = str(source_key or "").strip() or direct_ask_mirror_source_key(
        source_task_id=source_task_id,
        group=str(getattr(asking_agent, "group", "") or "").strip(),
        agent_id=sender.id,
        question=question_text,
    )
    message_id = _direct_ask_message_id(key)
    existing = _load_direct_message(state, message_id)
    if existing:
        return _append_existing_direct_message(state, existing)

    recipient = resolve_ask_recipient(state, asking_agent)
    now = float(created_at if created_at is not None else time.time())
    row = {
        "id": message_id,
        "thread_id": canonical_user_agent_thread_id(sender.id),
        "reply_to_id": "",
        "group_name": str(getattr(asking_agent, "group", "") or "").strip(),
        "sender_id": sender.id,
        "sender_kind": sender.kind,
        "sender_name": sender.name,
        "recipient_id": recipient.id,
        "recipient_kind": recipient.kind,
        "recipient_name": recipient.name,
        "message": question_text,
        "message_type": "ask",
        "created_at": now,
        "ack_required": True,
        "blocking": True,
        "source_task_id": str(source_task_id or "").strip(),
        "delivery_state": "delivered",
        "delivery_reason": "",
        "delivered_at": now,
        "read_at": 0,
    }
    try:
        return state.save_direct_message(row)
    except Exception:
        log.exception("Failed to mirror blocking ask into direct messages")
        return None


def save_direct_ask_reply_mirror(
        state,
        asking_agent,
        answer: str,
        *,
        question: str = "",
        source_task_id: str = "",
        source_key: str = "",
        created_at: float | None = None,
        delivery_state: str = "delivered",
        delivery_reason: str = "") -> dict | None:
    """Persist or return a display-only ``message_type='ask_reply'`` row."""
    if not state or not getattr(state, "db", None) or not asking_agent:
        return None
    answer_text = str(answer or "").strip()
    if not answer_text:
        return None
    ask_row = save_direct_ask_mirror(
        state,
        asking_agent,
        question,
        source_task_id=source_task_id,
        source_key=source_key,
    )
    if not ask_row:
        return None
    reply_id = _direct_ask_reply_message_id(str(ask_row.get("id", "") or ""))
    existing = _load_direct_message(state, reply_id)
    if existing:
        return _append_existing_direct_message(state, existing)

    sender = DirectMessageParticipant(
        id=str(ask_row.get("recipient_id", "") or "").strip(),
        kind=str(ask_row.get("recipient_kind", "") or "").strip(),
        name=str(ask_row.get("recipient_name", "") or "").strip(),
    )
    recipient = DirectMessageParticipant(
        id=str(ask_row.get("sender_id", "") or "").strip(),
        kind=str(ask_row.get("sender_kind", "") or "").strip(),
        name=str(ask_row.get("sender_name", "") or "").strip(),
    )
    now = float(created_at if created_at is not None else time.time())
    delivery_state = str(delivery_state or "delivered").strip() or "delivered"
    if delivery_state not in {"buffered", "delivered", "failed"}:
        delivery_state = "delivered"
    row = {
        "id": reply_id,
        "thread_id": str(ask_row.get("thread_id", "") or ""),
        "reply_to_id": str(ask_row.get("id", "") or ""),
        "group_name": str(ask_row.get("group_name", "") or ""),
        "sender_id": sender.id,
        "sender_kind": sender.kind,
        "sender_name": sender.name,
        "recipient_id": recipient.id,
        "recipient_kind": recipient.kind,
        "recipient_name": recipient.name,
        "message": answer_text,
        "message_type": "ask_reply",
        "created_at": now,
        "ack_required": False,
        "blocking": False,
        "source_task_id": str(ask_row.get("source_task_id", "") or ""),
        "delivery_state": delivery_state,
        "delivery_reason": str(delivery_reason or ""),
        "delivered_at": now if delivery_state == "delivered" else 0,
        "read_at": 0,
    }
    try:
        return state.save_direct_message(row)
    except Exception:
        log.exception("Failed to mirror blocking ask reply into direct messages")
        return None
