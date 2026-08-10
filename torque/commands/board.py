"""Core board command helpers shared by user and Architect paths."""

from __future__ import annotations

from ..state import MatrixState


BOARD_ARCHIVE_COMMAND_NAMES = frozenset({
    "board_archive_task",
    "board_archive_tasks",
    "board_unarchive_task",
})


def _resolve_task_id(state, identifier: str) -> str:
    """Resolve a task by canonical ID, legacy alias, or ID prefix."""
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    resolver = getattr(state, "resolve_board_task_id", None)
    if callable(resolver):
        resolved = resolver(ident)
        return resolved or (
            "" if ident in getattr(state, "task_id_aliases", {}) else ident
        )
    aliased = state.resolve_task_alias(ident)
    if aliased != ident:
        return aliased if aliased in state.board_tasks else ""
    if ident in state.board_tasks:
        return ident
    prefix_matches = [
        task.id
        for task in state.board_tasks.values()
        if task.id.startswith(ident)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return ident


def _handle_board_archive_command(
    state: MatrixState,
    data: dict,
) -> dict | None:
    """Archive one board task."""
    task_id = _resolve_task_id(state, data.get("id", ""))
    if task_id not in state.board_tasks:
        return {"type": "error", "message": "Task not found"}
    state.board_archive_task(task_id, position=data.get("position"))
    return None


def _handle_board_archive_tasks_command(
    state: MatrixState,
    data: dict,
) -> dict:
    """Archive multiple board tasks in one atomic batch."""
    raw_ids = data.get("ids", data.get("task_ids", []))
    if not isinstance(raw_ids, list):
        return {"type": "error", "message": "ids must be an array"}
    try:
        archived_ids = state.board_archive_tasks(raw_ids)
    except Exception as exc:
        return {"type": "error", "message": str(exc)}

    count = len(archived_ids)
    message = (
        "No tasks archived"
        if count == 0
        else f"Archived {count} completed task{'' if count == 1 else 's'}"
    )
    return {"type": "toast", "level": "success", "message": message}


def _handle_board_unarchive_command(
    state: MatrixState,
    data: dict,
) -> dict:
    """Unarchive one board task."""
    task_id = _resolve_task_id(state, data.get("id", ""))
    if task_id not in state.board_tasks:
        return {"type": "error", "message": "Task not found"}
    if state.board_tasks[task_id].lane != "Archived":
        return {"type": "error", "message": "Task is not archived"}
    mutation = state.board_unarchive_task(
        task_id,
        lane=data.get("lane", ""),
        position=data.get("position"),
        allow_done_advisory=True,
        acknowledge_unmerged=data.get("acknowledge_unmerged") is True,
        clear_status=data.get("clear_status") is True,
    )
    if isinstance(mutation, dict):
        if mutation.get("type") in {
                "error", "task_move_acknowledgement_required"}:
            return mutation
    task = state.board_tasks.get(task_id)
    if (
            isinstance(mutation, dict)
            and not mutation.get("eligible", True)
            and (not task or task.lane == "Archived")):
        return {
            "type": "finalization_blocked",
            "task_id": task_id,
            "finalization": mutation,
            "missing_gates": mutation.get("missing_gates", []),
        }
    if not task or task.lane == "Archived":
        return {"type": "error", "message": "Task was not restored"}
    result = {
        "type": "task_unarchived",
        "task_id": task_id,
        "new_lane": task.lane,
    }
    if isinstance(mutation, dict):
        result["advisory"] = mutation
    return result
