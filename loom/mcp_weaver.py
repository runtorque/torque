"""Legacy ``weaver_*`` MCP tool aliases.

The legacy weaver namespace remains available for a one-release
compatibility window. Each alias resolves the default engineer at call
time and delegates to the shared engineer-scoped implementation.
"""

from __future__ import annotations

from .mcp_tools_shared import NO_ENGINEER_ALIAS_ERROR, dispatch_scoped_tool, resolve_default_engineer
from .mcp_weaver_tools.tool_specs import WEAVER_TOOLS


def _authorize_weaver_cell(state, cell_id: str = ""):
    """Legacy compatibility helper for resolving the default engineer."""
    del cell_id
    engineer_id = resolve_default_engineer(state)
    if not engineer_id:
        return None, "", NO_ENGINEER_ALIAS_ERROR
    cell = state.agents.get(engineer_id)
    if not cell:
        return None, "", NO_ENGINEER_ALIAS_ERROR
    return cell, str(getattr(cell, "group", "") or ""), ""


async def _dispatch_weaver_tool(name, args, handle_command, state,
                                cell_id: str = ""):
    del cell_id
    engineer_id = resolve_default_engineer(state)
    if not engineer_id:
        return NO_ENGINEER_ALIAS_ERROR, True
    return await dispatch_scoped_tool(
        name,
        args,
        handle_command,
        state,
        tool_prefix="weaver_",
        caller_kind="engineer",
        caller_id=engineer_id,
    )
