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
from copy import deepcopy

from .mcp_tools_shared import authorize_caller, dispatch_scoped_tool
from .mcp_weaver_tools.tool_specs import WEAVER_TOOLS


_ENV_VAR = "LOOM_ENGINEER_ID"


def _rename_tool_spec(tool: dict) -> dict:
    renamed = deepcopy(tool)
    renamed["name"] = str(renamed.get("name", "")).replace(
        "weaver_", "engineer_", 1
    )
    description = str(renamed.get("description", "") or "")
    description = description.replace("Weaver", "Engineer")
    description = description.replace("weaver", "engineer")
    renamed["description"] = description
    return renamed


ENGINEER_TOOLS = [_rename_tool_spec(tool) for tool in WEAVER_TOOLS]


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
                                  caller_id: str = ""):
    caller_id = str(caller_id or "").strip() or bound_engineer_id_from_env()
    return await dispatch_scoped_tool(
        name,
        args,
        handle_command,
        state,
        tool_prefix="engineer_",
        caller_kind="engineer",
        caller_id=caller_id,
    )


if __name__ == "__main__":
    exit_if_invalid_engineer_binding()
    sys.exit(0)
