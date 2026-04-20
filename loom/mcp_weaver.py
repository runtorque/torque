"""Legacy ``weaver_*`` MCP tool aliases.

The legacy weaver namespace remains available for a one-release
compatibility window. Headerless external calls still resolve the
default engineer at call time. Calls bound to an existing legacy
Weaver session stay pinned to that specific Weaver/group instead of
silently hopping to the global default engineer.
"""

from __future__ import annotations

from .mcp_tools_shared import (
    NO_ENGINEER_ALIAS_ERROR,
    dispatch_scoped_tool,
    resolve_default_engineer,
)
from .mcp_weaver_tools.tool_specs import WEAVER_TOOLS


def _resolve_weaver_alias_caller(state, cell_id: str = "") -> tuple[str, str]:
    cell_id = str(cell_id or "").strip()
    if cell_id:
        cell = state.agents.get(cell_id)
        if (
            cell
            and getattr(cell, "cell_type", "") == "agent"
            and str(getattr(cell, "kind", "") or "").strip() != "engineer"
            and state.get_group_settings(getattr(cell, "group", "")).weaver_agent_id
            == cell.id
        ):
            return "legacy_weaver", cell.id

    engineer_id = resolve_default_engineer(state) or ""
    if engineer_id:
        return "engineer", engineer_id
    return "", ""


def _authorize_weaver_cell(state, cell_id: str = ""):
    """Resolve the effective caller for a legacy weaver alias request."""
    caller_kind, caller_id = _resolve_weaver_alias_caller(state, cell_id)
    if not caller_id:
        return None, "", NO_ENGINEER_ALIAS_ERROR
    cell = state.agents.get(caller_id)
    if not cell:
        return None, "", NO_ENGINEER_ALIAS_ERROR
    return cell, str(getattr(cell, "group", "") or ""), ""


async def _dispatch_weaver_tool(name, args, handle_command, state,
                                cell_id: str = ""):
    caller_kind, caller_id = _resolve_weaver_alias_caller(state, cell_id)
    if not caller_id:
        return NO_ENGINEER_ALIAS_ERROR, True
    return await dispatch_scoped_tool(
        name,
        args,
        handle_command,
        state,
        tool_prefix="weaver_",
        caller_kind=caller_kind,
        caller_id=caller_id,
    )
