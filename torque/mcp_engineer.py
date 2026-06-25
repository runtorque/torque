"""Engineer MCP tool entrypoint helpers.

This module exposes the ``engineer_*`` tool namespace and provides
session-binding helpers for transports that bind an engineer session via
``TORQUE_ENGINEER_ID``. In v1, Torque keeps a local-trust model; env/header
spoofing protections are out of scope.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

from .mcp_stdio_proxy import serve_http_proxy
from .mcp_tools_shared import authorize_caller, dispatch_scoped_tool
from .mcp_tool_search import make_tool_search_spec, tool_search_response
from .help_docs import help_tool_specs
from .mcp_engineer_tools.tool_specs import (
    ENGINEER_TOOLS as ENGINEER_ORCHESTRATION_TOOLS,
)


_ENV_VAR = "TORQUE_ENGINEER_ID"

ENGINEER_DEFERRED_TOOL_NAMES = {
    "engineer_specializations_list",
    "engineer_specialization_show",
    "engineer_specialization_save",
    "engineer_specialization_delete",
    "engineer_launch_settings",
    "engineer_task_reassign",
    "engineer_mcp_calls",
}

ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES = {
    "engineer_message_architect",
    "engineer_reply",
    "engineer_peer_list",
    "engineer_peer_notify",
    "engineer_peer_reply",
    "engineer_peer_inbox",
    "engineer_peer_inspect",
    "engineer_behavior_overlay_propose",
    "engineer_behavior_overlay_request_rollback",
}


def engineer_has_hiring_architect(cell) -> bool:
    return bool(str(getattr(cell, "hired_by_architect_id", "") or "").strip())


def engineer_can_override_worker_provider(cell, state=None) -> bool:
    if state is None or cell is None:
        return True
    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return True
    settings = state.get_engineer_settings(group)
    return bool(
        getattr(settings, "engineer_can_override_worker_provider", True)
    )


def _without_worker_provider_override_params(tools: list[dict]) -> list[dict]:
    filtered = []
    for tool in tools:
        spec = deepcopy(tool)
        name = str(spec.get("name", "") or "").strip()
        properties = (
            spec.get("inputSchema", {})
            .get("properties", {})
        )
        if name == "engineer_task_dispatch":
            properties.pop("agent_type", None)
            properties.pop("provider", None)
        elif name == "engineer_batch_dispatch":
            properties.pop("provider", None)
        filtered.append(spec)
    return filtered


def engineer_tools_for_cell(cell, state=None) -> list[dict]:
    if engineer_has_hiring_architect(cell):
        tools = list(ENGINEER_TOOLS)
    else:
        tools = [
            tool for tool in ENGINEER_TOOLS
            if str(tool.get("name", "") or "").strip()
            not in ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES
        ]
    if not engineer_can_override_worker_provider(cell, state):
        return _without_worker_provider_override_params(tools)
    return tools


ENGINEER_TOOLS = [deepcopy(tool) for tool in ENGINEER_ORCHESTRATION_TOOLS]
for _tool in ENGINEER_TOOLS:
    if str(_tool.get("name", "") or "").strip() in ENGINEER_DEFERRED_TOOL_NAMES:
        _tool["deferred"] = True
ENGINEER_TOOLS.extend([
    {
        "name": "engineer_area_list",
        "description": "Read-only list of Planning Areas in this engineer's group. Decision links are counted but decision details are hidden.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean"},
                "include_links": {"type": "boolean"},
                "include_notes": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "engineer_area_show",
        "description": "Read-only show for one same-group Planning Area with links filtered to engineer-visible tasks and decision counts only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "area_id": {"type": "string"},
                "note_limit": {"type": "integer"},
            },
            "required": ["area"],
        },
    },
    {
        "name": "engineer_initiative_list",
        "description": "Read-only list of first-class product Initiatives in this engineer's group. Board remains execution source of truth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean"},
                "include_links": {"type": "boolean"},
            },
        },
    },
    {
        "name": "engineer_initiative_show",
        "description": "Read-only show for one same-group Initiative with typed links and Board-derived linked task summary filtered to engineer-visible tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "initiative": {"type": "string"},
                "initiative_id": {"type": "string"},
            },
            "required": ["initiative"],
        },
    },
    *help_tool_specs("engineer_"),
    make_tool_search_spec("engineer_tool_search", "engineer"),
    {
        "name": "engineer_behavior_overlay_read",
        "description": (
            "Read this engineer's active Dynamic Behavior overlay. "
            "Use scope_kind=role for the inherited group Engineer-role "
            "overlay or effective for role+agent visibility."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role", "effective"],
                },
            },
        },
    },
    {
        "name": "engineer_behavior_overlay_versions",
        "description": "List this engineer's Dynamic Behavior overlay versions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                },
                "limit": {"type": "integer", "description": "Maximum versions."},
            },
        },
    },
    {
        "name": "engineer_behavior_overlay_diff",
        "description": "Diff this engineer's overlay versions or a proposal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                },
                "proposal_id": {"type": "string"},
                "from_version_id": {"type": "string"},
                "to_version_id": {"type": "string"},
            },
        },
    },
    {
        "name": "engineer_behavior_overlay_propose",
        "description": (
            "Propose a change to this engineer's own Dynamic Behavior "
            "overlay for architect governance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Proposed overlay text."},
                "rationale": {"type": "string", "description": "Why this improves behavior."},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                    "description": "Role writes are rejected in v1.",
                },
            },
            "required": ["text", "rationale"],
        },
    },
    {
        "name": "engineer_behavior_overlay_request_rollback",
        "description": "Request rollback of this engineer's overlay to an earlier version.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "version_id": {"type": "string"},
                "rationale": {"type": "string"},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                    "description": "Role rollbacks are rejected in v1.",
                },
            },
            "required": ["version_id", "rationale"],
        },
    },
    {
        "name": "engineer_mcp_calls",
        "deferred": True,
        "description": (
            "Return recent MCP call history for this engineer and owned "
            "workers. Defaults to mcp__torque__ tools; optionally filter by "
            "agent, tool pattern, hook event name, time, and limit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional self/owned worker id, slug, or name.",
                },
                "cell_id": {
                    "type": "string",
                    "description": "Optional exact cell id; alias for agent_id.",
                },
                "tool_name_pattern": {
                    "type": "string",
                    "description": "Optional exact tool name or SQL LIKE/glob pattern.",
                },
                "tool_filter": {
                    "type": "string",
                    "description": "Alias for tool_name_pattern.",
                },
                "hook_event_name": {
                    "type": "string",
                    "description": "Optional hook filter such as PostToolUse.",
                },
                "since": {
                    "type": "number",
                    "description": "Optional unix timestamp lower bound.",
                },
                "until": {
                    "type": "number",
                    "description": "Optional unix timestamp upper bound.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum calls to return (default 50, max 500).",
                },
            },
        },
    },
    {
        "name": "engineer_task_reassign",
        "deferred": True,
        "description": (
            "Reassign a task you currently own or originally created to "
            "another engineer in your group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or alias.",
                },
                "new_engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name to assign.",
                },
            },
            "required": ["task", "new_engineer_id"],
        },
    },
    {
        "name": "engineer_message_architect",
        "description": (
            "Send a direct message to the architect that hired this engineer. "
            "Use this for non-trivial product or scope decisions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "architect_id": {
                    "type": "string",
                    "description": "Architect id/slug/name.",
                },
                "message": {
                    "type": "string",
                    "description": "Message content.",
                },
                "ack_required": {
                    "type": "boolean",
                    "description": (
                        "Set true only when the architect must answer a "
                        "question or make a decision; default false for "
                        "status-only updates."
                    ),
                    "default": False,
                },
            },
            "required": ["architect_id", "message"],
        },
    },
    {
        "name": "engineer_reply",
        "description": "Reply to an existing architect↔engineer message thread.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Existing message id from the conversation log.",
                },
                "message": {
                    "type": "string",
                    "description": "Reply content.",
                },
                "ack_required": {
                    "type": "boolean",
                    "description": (
                        "Set true only when this reply needs an architect "
                        "answer or decision; default false for status-only "
                        "updates."
                    ),
                    "default": False,
                },
            },
            "required": ["message_id", "message"],
        },
    },
])
_ENGINEER_TOOL_NAMES = {
    str(tool.get("name", "") or "").strip()
    for tool in ENGINEER_TOOLS
}


def _stringify_startup_error(error_text: str) -> str:
    text = str(error_text or "").strip()
    if not text:
        return "unknown engineer session binding error"
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            return text
        message = str(payload.get("message", "") or "").strip()
        if message:
            return message
    return text


def bound_engineer_id_from_env() -> str:
    return str(os.environ.get(_ENV_VAR, "") or "").strip()


def validate_engineer_binding(state=None) -> tuple[str, str]:
    engineer_id = bound_engineer_id_from_env()
    if not engineer_id:
        return "", f"{_ENV_VAR} is required"
    if state is None:
        return engineer_id, ""
    _cell, _group, _effective_kind, error_text, is_error = authorize_caller(
        state,
        caller_kind="engineer",
        caller_id=engineer_id,
    )
    if is_error:
        return "", _stringify_startup_error(error_text)
    return engineer_id, ""


def exit_if_invalid_engineer_binding(state=None) -> str:
    engineer_id, error_text = validate_engineer_binding(state)
    if error_text:
        print(error_text)
        raise SystemExit(2)
    return engineer_id


async def _dispatch_engineer_tool(name, args, handle_command, state,
                                  caller_id: str = "",
                                  idempotency_key: str = ""):
    caller_id = str(caller_id or "").strip() or bound_engineer_id_from_env()
    if str(name or "").strip() not in _ENGINEER_TOOL_NAMES:
        return f"Unknown engineer tool: {name}", True
    if str(name or "").strip() in ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES:
        _cell, _group, _kind, error_text, is_error = authorize_caller(
            state,
            caller_kind="engineer",
            caller_id=caller_id,
        )
        if is_error:
            return error_text, True
        if not engineer_has_hiring_architect(_cell):
            return f"Unknown engineer tool: {name}", True
    if str(name or "").strip() == "engineer_tool_search":
        _cell, _group, _kind, error_text, is_error = authorize_caller(
            state,
            caller_kind="engineer",
            caller_id=caller_id,
        )
        if is_error:
            return error_text, True
        return tool_search_response(
            engineer_tools_for_cell(_cell, state),
            args,
        ), False
    return await dispatch_scoped_tool(
        name,
        args,
        handle_command,
        state,
        tool_prefix="engineer_",
        caller_kind="engineer",
        caller_id=caller_id,
        idempotency_key=idempotency_key,
    )


if __name__ == "__main__":
    sys.exit(serve_http_proxy(exit_if_invalid_engineer_binding()))
