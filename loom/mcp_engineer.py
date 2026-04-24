"""Engineer MCP tool entrypoint helpers.

This module exposes the ``engineer_*`` tool namespace and provides
session-binding helpers for transports that bind an engineer session via
``LOOM_ENGINEER_ID``. In v1, Loom keeps a local-trust model; env/header
spoofing protections are out of scope.
"""

from __future__ import annotations

import json
import os
import sys

from .mcp_stdio_proxy import serve_http_proxy
from .mcp_tools_shared import authorize_caller, dispatch_scoped_tool
from .mcp_engineer_tools.tool_specs import (
    ENGINEER_TOOLS as ENGINEER_ORCHESTRATION_TOOLS,
)


_ENV_VAR = "LOOM_ENGINEER_ID"

ENGINEER_TOOLS = list(ENGINEER_ORCHESTRATION_TOOLS)
ENGINEER_TOOLS.extend([
    {
        "name": "engineer_task_reassign",
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
