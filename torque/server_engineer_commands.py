"""Handle Engineer journal, digest, plus flush commands."""

from __future__ import annotations

import hashlib
import time

from .server_agent_common import _resolve_agent_id
from .state import MatrixState


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
