"""Display-only direct-message mirror helpers for blocking asks.

These helpers keep blocking ask delivery/resolution semantics in their
existing code paths while projecting asks and ask replies into the durable
direct-message store for the below-terminal conversation panel.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .config import log
from .db import canonical_user_agent_thread_id


@dataclass(frozen=True)
class DirectMessageParticipant:
    id: str
    kind: str
    name: str


def user_direct_message_participant() -> DirectMessageParticipant:
    return DirectMessageParticipant(id="user", kind="user", name="User")


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


def resolve_ask_owner_recipient(state, asking_agent) -> DirectMessageParticipant:
    """Return the display recipient for an asking agent's blocking ask.

    V1 asks are still delivered/resolved by the existing user-facing paths.
    The mirror row is owner-aware so the conversation model is already shaped
    for owner-routed ask delivery:

    - worker → owning engineer (``owner_engineer_id``), otherwise user
    - engineer → hiring architect (``hired_by_architect_id``), otherwise user
    - architect → user
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


def direct_ask_mirror_source_key(*,
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


def save_direct_ask_mirror(state,
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

    recipient = resolve_ask_owner_recipient(state, asking_agent)
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


def save_direct_ask_reply_mirror(state,
                                 asking_agent,
                                 answer: str,
                                 *,
                                 question: str = "",
                                 source_task_id: str = "",
                                 source_key: str = "",
                                 created_at: float | None = None) -> dict | None:
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
        "delivery_state": "delivered",
        "delivery_reason": "",
        "delivered_at": now,
        "read_at": 0,
    }
    try:
        return state.save_direct_message(row)
    except Exception:
        log.exception("Failed to mirror blocking ask reply into direct messages")
        return None
