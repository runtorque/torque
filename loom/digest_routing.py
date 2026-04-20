"""Digest recipient resolution for engineer/architect fan-out."""

from __future__ import annotations

from .state import AgentDigestSettings, WEAVER_MANDATORY_EVENTS

ARCHITECT_COARSE_EVENT_KINDS = frozenset({
    "task_completed",
    "agent_blocked",
    "agent_error",
    "ask_created",
    "task_derived",
    "pipeline_complete",
    "engineer_hired",
    "pending_hire_created",
    "pending_hire_approved",
    "pending_hire_rejected",
    "pending_hire_resolved",
})


def _cell_kind(cell) -> str:
    return str(getattr(cell, "kind", "") or "").strip()


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_weaver_id", "") or "").strip()


def candidate_digest_recipients(state, event: dict) -> list[str]:
    """Return the unfiltered recipient chain for a panel event."""
    source = state.agents.get(str(event.get("cell_id", "") or "").strip())
    if not source:
        return []

    kind = _cell_kind(source)
    recipients: list[str] = []

    if kind == "worker":
        owner_id = _effective_owner_engineer_id(source)
        owner = state.agents.get(owner_id) if owner_id else None
        if owner and _cell_kind(owner) == "engineer":
            recipients.append(owner.id)
            architect_id = str(
                getattr(owner, "hired_by_architect_id", "") or ""
            ).strip()
            architect = state.agents.get(architect_id) if architect_id else None
            if architect and _cell_kind(architect) == "architect":
                recipients.append(architect.id)
    elif kind == "engineer":
        recipients.append(source.id)
        architect_id = str(
            getattr(source, "hired_by_architect_id", "") or ""
        ).strip()
        architect = state.agents.get(architect_id) if architect_id else None
        if architect and _cell_kind(architect) == "architect":
            recipients.append(architect.id)
    elif kind == "architect":
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for recipient_id in recipients:
        recipient_id = str(recipient_id or "").strip()
        if not recipient_id or recipient_id in seen:
            continue
        ordered.append(recipient_id)
        seen.add(recipient_id)
    return ordered


def _default_digest_settings(state, recipient_id: str) -> AgentDigestSettings:
    cell = state.agents.get(str(recipient_id or "").strip())
    return AgentDigestSettings(
        agent_id=str(recipient_id or "").strip(),
        architect_digest=bool(cell and _cell_kind(cell) == "architect"),
    )


def _digest_settings_for(state, recipient_id: str) -> AgentDigestSettings:
    getter = getattr(state, "get_agent_digest_settings", None)
    if callable(getter):
        settings = getter(recipient_id)
        if settings:
            return settings
    settings = getattr(state, "agent_digest_settings", {}).get(recipient_id)
    if settings:
        return settings
    return _default_digest_settings(state, recipient_id)


def recipient_wants_digest_event(state, recipient_id: str, event: dict, *,
                                 ignore_pause: bool = False) -> bool:
    recipient = state.agents.get(str(recipient_id or "").strip())
    if not recipient:
        return False
    settings = _digest_settings_for(state, recipient_id)
    if not ignore_pause and getattr(settings, "paused", False):
        return False
    kind = str(event.get("kind", "") or "").strip()
    if kind not in WEAVER_MANDATORY_EVENTS:
        enabled = list(getattr(settings, "enabled_events", []) or [])
        if kind not in enabled:
            return False
    if _cell_kind(recipient) == "architect" and (
            kind not in ARCHITECT_COARSE_EVENT_KINDS):
        return False
    return True


def resolve_digest_recipients(state, event: dict) -> list[str]:
    """Resolve filtered digest recipients for a panel event."""
    recipients: list[str] = []
    for recipient_id in candidate_digest_recipients(state, event):
        if recipient_wants_digest_event(state, recipient_id, event):
            recipients.append(recipient_id)
    return recipients
