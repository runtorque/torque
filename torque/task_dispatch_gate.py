"""Shared predicate and response for edits protected by active dispatches."""

from __future__ import annotations


def task_has_active_dispatch(state, task) -> bool:
    """Whether *task* still has a running execution stream.

    ``dispatch_state`` records that work was handed off, but it deliberately
    survives a worker session ending for audit/history. It is therefore not
    enough on its own to decide whether an amendment could invalidate work in
    flight. A task-specific worker is authoritative when attached; otherwise
    architect-to-engineer dispatches use the assigned engineer stream.
    """
    if str(getattr(task, "dispatch_state", "queued") or "queued").strip().lower() != "live":
        return False

    agent_id = str(getattr(task, "agent_id", "") or "").strip()
    if agent_id:
        stream = state.agents.get(agent_id)
    else:
        stream = state.agents.get(
            str(getattr(task, "assigned_engineer_id", "") or "").strip()
        )
    return bool(stream and str(getattr(stream, "status", "") or "").strip().lower() == "running")


def active_dispatch_edit_error(task) -> dict:
    """Build the common refusal for an amendment with work still in flight."""
    return {
        "type": "error",
        "reason": "task_dispatched",
        "dispatch_state": str(
            getattr(task, "dispatch_state", "queued") or "queued"
        ).strip().lower(),
        "message": (
            "Task has active dispatched work. Stop the active execution "
            "stream before editing it."
        ),
    }
