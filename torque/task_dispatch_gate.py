"""Shared predicate and response for edits protected by active dispatches."""

from __future__ import annotations


def task_has_active_dispatch(state, task) -> bool:
    """Whether *task* still has a running execution stream.

    ``dispatch_state`` records that work was handed off, but it deliberately
    survives a worker session ending for audit/history. It is therefore not
    enough on its own to decide whether an amendment could invalidate work in
    flight. Only a task-specific worker is authoritative. An assigned
    engineer can outlive many task executions, so its runtime status is not
    evidence that this particular task still has work in flight.
    """
    if str(getattr(task, "dispatch_state", "queued") or "queued").strip().lower() != "live":
        return False

    agent_id = str(getattr(task, "agent_id", "") or "").strip()
    if not agent_id:
        return False
    stream = state.agents.get(agent_id)
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
            "Task has active work in its assigned worker. Stop or complete "
            "that worker before editing."
        ),
    }
