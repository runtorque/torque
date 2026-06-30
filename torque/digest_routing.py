"""Digest recipient resolution for engineer/architect fan-out."""

from __future__ import annotations

from .engineer_ask_events import (
    ENGINEER_ASK_EVENT_KINDS,
    ENGINEER_ASK_RESOLVED,
    ENGINEER_AWAITING_HUMAN_INPUT,
)
from .state import (
    ARCHITECT_MANDATORY_EVENTS,
    AgentDigestSettings,
    ENGINEER_MANDATORY_EVENTS,
)

ARCHITECT_COARSE_EVENTS = frozenset({
    # Plan vocabulary.
    "task_done",
    "task_blocked",
    "task_error",
    "task_ask",
    "task_derive",
    # Current panel-event vocabulary.
    "task_completed",
    "agent_blocked",
    "agent_error",
    "ask_created",
    "task_derived",
    # Cross-engineer lifecycle/pipeline signals.
    "pipeline_complete",
    "engineer_hired",
    "engineer_fired",
    "engineer_dismissed",
    "engineer_rehired",
    "workflow_breach",
    "engineer_queue_empty",
    "perceived_empty_episode",
    "engineer_peer_thread_opened",
    "engineer_peer_thread_active",
    ENGINEER_AWAITING_HUMAN_INPUT,
    ENGINEER_ASK_RESOLVED,
})

# Back-compat for earlier Phase 1 tests/imports.
ARCHITECT_COARSE_EVENT_KINDS = ARCHITECT_COARSE_EVENTS

_NO_ROUTING_SOURCE = object()


def _cell_kind(cell) -> str:
    return str(getattr(cell, "kind", "") or "").strip()


def _is_tombstoned(state, cell) -> bool:
    checker = getattr(state, "agent_is_tombstoned", None)
    if callable(checker):
        return bool(checker(cell))
    try:
        return float(getattr(cell, "deleted_at", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _event_kind(event: dict) -> str:
    return str((event or {}).get("kind", "") or "").strip()


def _is_architect_coarse_event(event: dict) -> bool:
    return _event_kind(event) in ARCHITECT_COARSE_EVENTS


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_engineer_id", "") or "").strip()


def _event_task_chain(state, event: dict) -> list:
    task_id = str((event or {}).get("task_id", "") or "").strip()
    task = getattr(state, "board_tasks", {}).get(task_id) if task_id else None
    chain = []
    seen_task_ids: set[str] = set()
    while task:
        current_id = str(getattr(task, "id", "") or "").strip()
        if current_id:
            if current_id in seen_task_ids:
                break
            seen_task_ids.add(current_id)
        chain.append(task)
        next_id = str(getattr(task, "parent_task_id", "") or "").strip()
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        if not next_id and root_id and root_id != current_id:
            next_id = root_id
        task = getattr(state, "board_tasks", {}).get(next_id) if next_id else None
    return chain


def _task_routing_source(state, event: dict):
    task_chain = _event_task_chain(state, event)
    if not task_chain:
        return None

    for current in task_chain:
        assigned_engineer_id = str(
            getattr(current, "assigned_engineer_id", "") or ""
        ).strip()
        if assigned_engineer_id:
            engineer = state.agents.get(assigned_engineer_id)
            if engineer and not _is_tombstoned(state, engineer):
                return engineer
            return _NO_ROUTING_SOURCE

    task = task_chain[0]
    for field_name in ("agent_id", "reply_agent_id"):
        cell_id = str(getattr(task, field_name, "") or "").strip()
        if not cell_id:
            continue
        source = state.agents.get(cell_id)
        if source and not _is_tombstoned(state, source):
            return source
    return None


def _task_completion_creator_architect_id(state, event: dict) -> str:
    """Return the task creator architect for the built-in completion route."""
    if _event_kind(event) != "task_completed":
        return ""
    task_chain = _event_task_chain(state, event)
    if not task_chain:
        return ""

    event_group = str((event or {}).get("group", "") or "").strip()
    source_task_group = str(getattr(task_chain[0], "group", "") or "").strip()
    if event_group and source_task_group and event_group != source_task_group:
        return ""

    for task in task_chain:
        architect_id = str(
            getattr(task, "created_by_architect_id", "") or ""
        ).strip()
        if not architect_id:
            continue
        architect = state.agents.get(architect_id)
        if not architect or _is_tombstoned(state, architect):
            return ""
        if _cell_kind(architect) != "architect":
            return ""
        architect_group = str(getattr(architect, "group", "") or "").strip()
        task_group = str(getattr(task, "group", "") or "").strip()
        if event_group and architect_group and architect_group != event_group:
            return ""
        if task_group and architect_group and architect_group != task_group:
            return ""
        return architect.id
    return ""


def _is_task_completion_creator_subscription(
        state, recipient_id: str, event: dict) -> bool:
    recipient_id = str(recipient_id or "").strip()
    return bool(
        recipient_id
        and _task_completion_creator_architect_id(state, event) == recipient_id
    )


def _event_routing_source(state, event: dict):
    source = _task_routing_source(state, event)
    if source is _NO_ROUTING_SOURCE:
        return None
    if source:
        return source

    cell_id = str(event.get("cell_id", "") or "").strip()
    if cell_id:
        source = state.agents.get(cell_id)
        if source and not _is_tombstoned(state, source):
            return source

    group = str(event.get("group", "") or "").strip()
    if not group:
        return None
    return getattr(state, "get_engineer_for_group", lambda _group: None)(group)


def candidate_digest_recipients(state, event: dict) -> list[str]:
    """Return possible recipients before pause/enabled-events filters."""
    source = _event_routing_source(state, event)
    recipients: list[str] = []

    if source and not _is_tombstoned(state, source):
        kind = _cell_kind(source)
        architect_eligible = _is_architect_coarse_event(event)

        if kind == "worker":
            owner_id = _effective_owner_engineer_id(source)
            owner = state.agents.get(owner_id) if owner_id else None
            if (
                    owner
                    and not _is_tombstoned(state, owner)
                    and _cell_kind(owner) == "engineer"
            ):
                recipients.append(owner.id)
                if architect_eligible:
                    architect_id = str(
                        getattr(owner, "hired_by_architect_id", "") or ""
                    ).strip()
                    architect = (
                        state.agents.get(architect_id)
                        if architect_id
                        else None
                    )
                    if (
                            architect
                            and not _is_tombstoned(state, architect)
                            and _cell_kind(architect) == "architect"
                    ):
                        recipients.append(architect.id)
        elif kind == "engineer":
            architect_id = str(
                getattr(source, "hired_by_architect_id", "") or ""
            ).strip()
            architect = state.agents.get(architect_id) if architect_id else None
            if _event_kind(event) in ENGINEER_ASK_EVENT_KINDS:
                if (
                        architect
                        and not _is_tombstoned(state, architect)
                        and _cell_kind(architect) == "architect"
                ):
                    recipients.append(architect.id)
            else:
                recipients.append(source.id)
                if architect_eligible:
                    if (
                            architect
                            and not _is_tombstoned(state, architect)
                            and _cell_kind(architect) == "architect"
                    ):
                        recipients.append(architect.id)
        elif (
                kind != "architect"
                and str(getattr(source, "id", "") or "").strip()
        ):
            recipients.append(source.id)

    creator_architect_id = _task_completion_creator_architect_id(state, event)
    if creator_architect_id:
        recipients.append(creator_architect_id)

    seen: set[str] = set()
    ordered: list[str] = []
    for recipient_id in recipients:
        recipient_id = str(recipient_id or "").strip()
        if not recipient_id or recipient_id in seen:
            continue
        recipient = state.agents.get(recipient_id)
        if _is_tombstoned(state, recipient):
            continue
        ordered.append(recipient_id)
        seen.add(recipient_id)
    return ordered


def _default_digest_settings(state, recipient_id: str) -> AgentDigestSettings:
    cell = state.agents.get(str(recipient_id or "").strip())
    is_architect = bool(cell and _cell_kind(cell) == "architect")
    kwargs = {}
    if is_architect:
        kwargs["enabled_events"] = []
    return AgentDigestSettings(
        agent_id=str(recipient_id or "").strip(),
        push_interval=300 if is_architect else 60,
        architect_digest=is_architect,
        wake_on_digest=False,
        **kwargs,
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
    if _cell_kind(recipient) == "architect":
        if kind not in ARCHITECT_COARSE_EVENTS:
            return False
        if _is_task_completion_creator_subscription(state, recipient_id, event):
            return True
        if kind in ARCHITECT_MANDATORY_EVENTS:
            return True
        enabled = list(getattr(settings, "enabled_events", []) or [])
        return kind in enabled
    if kind not in ENGINEER_MANDATORY_EVENTS:
        enabled = list(getattr(settings, "enabled_events", []) or [])
        if kind not in enabled:
            return False
    return True


def resolve_digest_recipients(state, event: dict) -> list[str]:
    """Resolve filtered digest recipients for a panel event."""
    recipients: list[str] = []
    for recipient_id in candidate_digest_recipients(state, event):
        if recipient_wants_digest_event(state, recipient_id, event):
            recipients.append(recipient_id)
    return recipients
