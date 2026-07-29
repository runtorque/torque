"""Read-only action-catalog MCP projection shared by every agent kind."""

from __future__ import annotations

import json


ACTION_CATALOG_TOOL = {
    "name": "torque_actions_list",
    "authority": {
        "requirements": [{
            "capability": "self.read",
            "minimum_scope": "self",
            "handler_scoped": True,
        }],
    },
    "description": (
        "List the live dispatch-effective action catalog for this group. "
        "Each action includes its description, declared transitions "
        "(always an explicit list), worktree directive (null means "
        "inherited), labels, normalized dispatch target, and winning "
        "project/user scope. "
        "Use this before selecting a task action or deriving follow-up work."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "group": {
                "type": "string",
                "description": (
                    "Optional group whose project action scope to resolve; "
                    "defaults to the caller's group."
                ),
            },
        },
    },
}


SHARED_ACTION_CATALOG_TOOL_NAMES = frozenset({"torque_actions_list"})


async def dispatch_action_catalog_tool(
    name: str,
    args: dict,
    cell_id: str,
    handle_command,
    state,
):
    """Dispatch the generic action catalog read or return ``None``.

    The response is limited to the caller's group before asking the command
    layer to resolve the project-plus-user catalog, so a generic ``self.read``
    grant cannot inspect a different group's project configuration.
    """
    if name != "torque_actions_list":
        return None
    cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
    if not cell:
        return f"Agent {cell_id} not found", True
    if state.agent_is_tombstoned(cell):
        return f"Agent {cell_id} is tombstoned", True
    caller_group = str(getattr(cell, "group", "") or "").strip()
    requested_group = str(args.get("group", "") or "").strip()
    if requested_group and requested_group != caller_group:
        return "Action catalog is limited to the caller's group", True
    result = await handle_command({
        "cmd": "list_action_catalog",
        "group": caller_group,
    })
    if result and result.get("type") == "error":
        return result.get("message", "Unknown error"), True
    return json.dumps(result or {
        "type": "action_catalog",
        "group": caller_group,
        "actions": [],
    }), False
