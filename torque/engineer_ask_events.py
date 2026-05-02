"""Panel-event helpers for engineer blocking human-input questions."""

from __future__ import annotations


ENGINEER_AWAITING_HUMAN_INPUT = "engineer_awaiting_human_input"
ENGINEER_ASK_RESOLVED = "engineer_ask_resolved"
ENGINEER_ASK_EVENT_KINDS = frozenset({
    ENGINEER_AWAITING_HUMAN_INPUT,
    ENGINEER_ASK_RESOLVED,
})

_QUESTION_PREVIEW_LIMIT = 200


def question_preview(question: str, limit: int = _QUESTION_PREVIEW_LIMIT) -> str:
    """Return a compact one-line question preview for event messages."""
    text = " ".join(str(question or "").split())
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)].rstrip() + "…"


def _resolve_engineer_cell(state, group: str, engineer_id: str = ""):
    engineer_id = str(engineer_id or "").strip()
    if engineer_id:
        cell = getattr(state, "agents", {}).get(engineer_id)
        if cell:
            return cell
    group = str(group or "").strip()
    if not group:
        return None
    getter = getattr(state, "get_engineer_for_group", None)
    if callable(getter):
        return getter(group)
    return None


def _append_panel_event(state, *, kind: str, cell_id: str, agent_name: str,
                        group: str, message: str) -> None:
    panel_log = getattr(state, "panel_log", None)
    if not panel_log or not hasattr(panel_log, "append"):
        return
    event = panel_log.append(
        kind=kind,
        cell_id=cell_id,
        agent_name=agent_name,
        group=group,
        message=message,
        task_id="",
    )
    emit = getattr(state, "_emit", None)
    if callable(emit):
        emit("event_append", **event)


def _engineer_event_identity(state, group: str, engineer_id: str = "") -> tuple[str, str, str]:
    cell = _resolve_engineer_cell(state, group, engineer_id)
    cell_id = (
        str(getattr(cell, "id", "") or "").strip()
        or str(engineer_id or "").strip()
    )
    agent_name = (
        str(getattr(cell, "name", "") or "").strip()
        or str(getattr(cell, "slug", "") or "").strip()
        or cell_id
        or "Engineer"
    )
    event_group = (
        str(getattr(cell, "group", "") or "").strip()
        or str(group or "").strip()
    )
    return cell_id, agent_name, event_group


def emit_engineer_awaiting_human_input_event(
        state, *, group: str, question: str, engineer_id: str = "") -> None:
    """Persist/surface an architect-digestable event for a blocking engineer ask."""
    cell_id, agent_name, event_group = _engineer_event_identity(
        state, group, engineer_id
    )
    if not event_group:
        return
    preview = question_preview(question)
    message = "Awaiting human input"
    if preview:
        message += f": {preview}"
    _append_panel_event(
        state,
        kind=ENGINEER_AWAITING_HUMAN_INPUT,
        cell_id=cell_id,
        agent_name=agent_name,
        group=event_group,
        message=message,
    )


def emit_engineer_ask_resolved_event(
        state, *, group: str, question: str = "", engineer_id: str = "") -> None:
    """Persist/surface an architect-digestable event when an engineer ask clears."""
    cell_id, agent_name, event_group = _engineer_event_identity(
        state, group, engineer_id
    )
    if not event_group:
        return
    preview = question_preview(question, limit=120)
    message = "Engineer ask resolved"
    if preview:
        message += f": {preview}"
    _append_panel_event(
        state,
        kind=ENGINEER_ASK_RESOLVED,
        cell_id=cell_id,
        agent_name=agent_name,
        group=event_group,
        message=message,
    )
