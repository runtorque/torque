"""Agent removal, tombstone restoration, and purge commands."""

from __future__ import annotations

from dataclasses import asdict

from ..execution_scope import (
    engineer_architect_close_denied_message,
    is_architect_execution_target,
)
from ..state import MatrixState


AGENT_LIFECYCLE_COMMAND_NAMES = frozenset({
    "remove_agent",
    "restore_agent",
    "architect_engineer_restore",
    "purge_agent_now",
    "recently_deleted_agents",
})


async def _handle_remove_agent_command(
    data: dict,
    state: MatrixState,
    *,
    close_agent_session_only,
    cleanup_purged_agents,
    delete_architect_command=None,
    delete_engineer_command=None,
) -> dict:
    """Remove a cell using soft-delete for agents and hard-delete for terminals."""
    cell = state.agents.get(str(data.get("id", "") or "").strip())
    if not cell:
        return {"type": "ok", "removed": []}
    engineer_close_id = str(
        data.get("_engineer_close_id", "")
        or data.get("_engineer_dispatch_id", "")
        or data.get("_created_by_engineer_id", "")
        or data.get("owner_engineer_id", "")
        or ""
    ).strip()
    if engineer_close_id and is_architect_execution_target(cell):
        return {
            "type": "error",
            "message": engineer_architect_close_denied_message(cell),
            "agent_id": cell.id,
        }
    if str(getattr(cell, "cell_type", "") or "") == "terminal":
        removed = state.remove_agent(cell.id)
        await cleanup_purged_agents(removed)
        return {"type": "ok", "removed": [item.id for item in removed]}
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "architect":
        if delete_architect_command is None:
            raise RuntimeError("delete_architect_command is required")
        return await delete_architect_command(
            {"id": cell.id},
            state,
            close_agent_session_only=close_agent_session_only,
        )
    if kind == "engineer":
        if delete_engineer_command is None:
            raise RuntimeError("delete_engineer_command is required")
        return await delete_engineer_command(
            {"id": cell.id},
            state,
            close_agent_session_only=close_agent_session_only,
        )
    tombstoned = await close_agent_session_only(cell)
    return {"type": "ok", "tombstoned": [item.id for item in tombstoned]}


def _restore_or_purge_authority_error(
    state: MatrixState,
    cell,
    data: dict,
    *,
    resolve_architect_cell=None,
) -> dict | None:
    architect_id = str(data.get("architect_id", "") or "").strip()
    if not architect_id:
        return None
    architect = (
        resolve_architect_cell(state, architect_id=architect_id)
        if resolve_architect_cell
        else state.agents.get(architect_id)
    )
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    if str(getattr(cell, "kind", "") or "").strip() != "engineer":
        return {"type": "error", "message": "engineer not found in scope"}
    if str(getattr(cell, "hired_by_architect_id", "") or "").strip() != architect.id:
        return {"type": "error", "message": "engineer not found in scope"}
    return None


def _handle_restore_agent_command(
    data: dict,
    state: MatrixState,
    *,
    resolve_architect_cell=None,
) -> dict:
    agent_id = str(data.get("id", "") or data.get("engineer_id", "") or "").strip()
    cell = state.agents.get(agent_id)
    if not cell:
        return {"type": "error", "message": "Agent not found"}
    authority_error = _restore_or_purge_authority_error(
        state,
        cell,
        data,
        resolve_architect_cell=resolve_architect_cell,
    )
    if authority_error:
        return authority_error
    if not state.agent_is_tombstoned(cell):
        return {"type": "ok", "restored": [], "already_active": True}
    restored = state.restore_agent(cell.id)
    return {"type": "ok", "restored": [item.id for item in restored]}


async def _handle_purge_agent_now_command(
    data: dict,
    state: MatrixState,
    *,
    cleanup_purged_agents,
    resolve_architect_cell=None,
) -> dict:
    agent_id = str(data.get("id", "") or data.get("engineer_id", "") or "").strip()
    cell = state.agents.get(agent_id)
    if not cell:
        return {"type": "error", "message": "Agent not found"}
    authority_error = _restore_or_purge_authority_error(
        state,
        cell,
        data,
        resolve_architect_cell=resolve_architect_cell,
    )
    if authority_error:
        return authority_error
    if not state.agent_is_tombstoned(cell):
        return {
            "type": "error",
            "message": "Agent is not tombstoned; use Delete first",
        }
    removed = state.purge_agent_now(cell.id)
    await cleanup_purged_agents(removed)
    return {"type": "ok", "purged": [item.id for item in removed]}


def _handle_recently_deleted_agents_command(
    data: dict,
    state: MatrixState,
) -> dict:
    group = str(data.get("group", "") or "").strip()
    agents = []
    for cell in state.iter_agents(include_tombstoned=True):
        if not state.agent_is_tombstoned(cell):
            continue
        if group and str(getattr(cell, "group", "") or "").strip() != group:
            continue
        agents.append(asdict(cell))
    agents.sort(
        key=lambda item: (float(item.get("deleted_at") or 0), item["id"])
    )
    return {"type": "ok", "agents": agents}
