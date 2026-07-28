"""Narrow ownership predicates for Architect worktree operations.

An Architect's ``worktree.merge: children`` authority normally follows the
Architect -> hired Engineer relationship.  User-owned workers deliberately
have no Engineer owner, so their ``torque/user/...`` worktrees use their
same-group task stream as the equivalent bounded relationship.  Task
provenance remains sufficient, but is not required: one Architect may
dispatch a stream while another same-group Architect with existing merge
authority drains it.
"""

from __future__ import annotations


def architect_can_access_user_owned_worker_worktree(
    state,
    caller_cell,
    worker,
) -> bool:
    """Whether ``caller_cell`` may access a same-group user-worker stream.

    This does *not* make user-owned workers generally visible to Architects.
    It is limited to a worker with no Engineer ownership, a ``torque/user/``
    branch, and a same-group task linked to that worker.  A task chain created
    by the calling Architect remains an explicitly accepted path; a
    same-group Architect who already has the projected ``worktree.merge``
    capability may also drain a stream created by an Architect peer.  The
    merge/review/validation gates remain in the normal worktree command path.
    """

    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    if not caller_id or str(getattr(caller_cell, "kind", "") or "").strip() != "architect":
        return False
    if str(getattr(worker, "kind", "") or "").strip() != "worker":
        return False
    if str(getattr(worker, "group", "") or "").strip() != str(
        getattr(caller_cell, "group", "") or ""
    ).strip():
        return False
    if any(
        str(getattr(worker, field, "") or "").strip()
        for field in ("owner_engineer_id", "created_by_engineer_id")
    ):
        return False
    if not str(getattr(worker, "worktree_branch", "") or "").strip().startswith(
        "torque/user/"
    ):
        return False

    worker_id = str(getattr(worker, "id", "") or "").strip()
    if not worker_id:
        return False
    tasks = getattr(state, "board_tasks", {}) or {}
    linked_same_group_task = False
    for task in tasks.values():
        if worker_id not in {
            str(getattr(task, "agent_id", "") or "").strip(),
            str(getattr(task, "reply_agent_id", "") or "").strip(),
        }:
            continue
        if str(getattr(task, "group", "") or "").strip() != str(
            getattr(caller_cell, "group", "") or ""
        ).strip():
            continue
        linked_same_group_task = True
        if _task_chain_created_by_architect(tasks, task, caller_id):
            return True
    # Dispatch provenance and merge authority commonly land on different
    # same-group Architect seats.  The transport still requires the caller's
    # frozen ``worktree.merge: children`` capability before this predicate can
    # project the user-worker relationship, so this fallback adds no tool
    # projection and does not bypass merge gates.
    return linked_same_group_task


def _task_chain_created_by_architect(tasks, task, architect_id: str) -> bool:
    """Check a task and its parent/root chain without trusting cycles."""

    current = task
    seen: set[str] = set()
    while current is not None:
        current_id = str(getattr(current, "id", "") or "").strip()
        if current_id:
            if current_id in seen:
                return False
            seen.add(current_id)
        if str(getattr(current, "created_by_architect_id", "") or "").strip() == architect_id:
            return True
        parent_id = str(getattr(current, "parent_task_id", "") or "").strip()
        root_id = str(getattr(current, "pipeline_root_id", "") or "").strip()
        next_id = parent_id or (root_id if root_id != current_id else "")
        current = tasks.get(next_id) if next_id else None
    return False
