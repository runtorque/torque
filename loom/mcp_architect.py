"""Architect MCP tool entrypoint helpers.

This module exposes the ``architect_*`` tool namespace and provides
session-binding helpers for transports that bind an architect session via
``LOOM_ARCHITECT_ID``.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

from .config import DB_FILE
from .db import LoomDB
from .mcp_stdio_proxy import serve_http_proxy
from .mcp_tools_shared import dispatch_scoped_tool

_ENV_VAR = "LOOM_ARCHITECT_ID"


_ARCHITECT_TOOL_SPECS = [
    {
        "name": "architect_workspace_overview",
        "description": (
            "Return a compact, architect-scoped rollup of visible engineers, "
            "their current task queues/workers/worktree streams, this "
            "architect's open tasks, pending hires, unread messages, and "
            "minimal journal self-state."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_board_summary",
        "description": (
            "Return a compact board overview for this architect's group, "
            "including task creator attribution and a bounded lightweight "
            "task summary excerpt."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_events_recent",
        "description": (
            "Return recent architect-scoped coarse panel events with task, "
            "engineer, worker-owner, creator, and digest-recipient attribution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum events to return (default 20, max 100).",
                },
                "kind_filter": {
                    "type": "string",
                    "description": "Optional panel event kind filter.",
                },
                "since": {
                    "type": "number",
                    "description": "Optional unix timestamp lower bound.",
                },
                "engineer_id": {
                    "type": "string",
                    "description": "Optional engineer id involvement filter.",
                },
            },
        },
    },
    {
        "name": "architect_deploy_state",
        "description": (
            "Return read-only daemon boot git state and pending mainline "
            "commit count since boot for deploy observability."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_task_show",
        "description": (
            "Show full details for one task the architect can see."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias.",
                }
            },
            "required": ["task"],
        },
    },
    {
        "name": "architect_task_create",
        "description": (
            "Create a task for a specific engineer. The assigned_engineer_id is "
            "required, created_by_architect_id is stamped automatically, and "
            "suggested_action is stored separately from the engineer's eventual "
            "chosen action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title."},
                "group": {
                    "type": "string",
                    "description": "Target group. Must match the architect's group.",
                },
                "description": {
                    "type": "string",
                    "description": "Longer description or context.",
                },
                "suggested_action": {
                    "type": "string",
                    "description": "Optional non-binding action suggestion.",
                },
                "action_vars": {
                    "type": "object",
                    "description": "Optional structured action variables.",
                    "additionalProperties": {"type": "string"},
                },
                "assigned_engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name to assign.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional labels.",
                },
                "lane": {
                    "type": "string",
                    "description": "Optional lane override.",
                },
            },
            "required": ["title", "group", "assigned_engineer_id"],
        },
    },
    {
        "name": "architect_task_reassign",
        "description": (
            "Reassign a task created by this architect to another visible engineer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID or alias."},
                "new_engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name to assign.",
                },
            },
            "required": ["task", "new_engineer_id"],
        },
    },
    {
        "name": "architect_ask",
        "description": (
            "Ask the user a blocking product/scope question. Creates a "
            "visible Backlog task labeled as a human architect ask with "
            "status 'Awaiting Input'; the user's reply is delivered to "
            "the architect inbox."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question the architect is asking the user.",
                },
                "description": {
                    "type": "string",
                    "description": "Longer context, decision options, or constraints.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "architect_engineer_list",
        "description": (
            "List engineers visible to this architect, marking each as hired "
            "or visible and including dismissed_at for paused engineers."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_engineer_hire",
        "description": (
            "Queue a new engineer hire request for user approval. This returns "
            "immediately with status='pending'; poll with "
            "architect_pending_hire_status or architect_pending_hire_list for "
            "resolution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Requested engineer name."},
                "command": {
                    "type": "string",
                    "description": "Optional boot command override.",
                },
                "provider": {
                    "type": "string",
                    "description": "Optional provider override.",
                },
                "directory": {
                    "type": "string",
                    "description": "Optional working directory override.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "architect_engineer_dismiss",
        "description": (
            "Pause a hired engineer. The engineer and owned worker terminals "
            "are closed, but history and task assignments are preserved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for the dismissal.",
                },
            },
            "required": ["engineer_id"],
        },
    },
    {
        "name": "architect_engineer_rehire",
        "description": (
            "Resume a previously dismissed hired engineer using the same "
            "agent id, slug, history, and launch configuration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name.",
                },
            },
            "required": ["engineer_id"],
        },
    },
    {
        "name": "architect_pending_hire_status",
        "description": "Read one pending-hire request created by this architect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hire_id": {
                    "type": "string",
                    "description": "Pending-hire id.",
                }
            },
            "required": ["hire_id"],
        },
    },
    {
        "name": "architect_pending_hire_list",
        "description": "List pending-hire requests created by this architect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["pending", "approved", "rejected"],
                    "description": "Optional status filter.",
                }
            },
        },
    },
    {
        "name": "architect_engineer_message",
        "description": "Send a direct message from this architect to a hired engineer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name.",
                },
                "message": {
                    "type": "string",
                    "description": "Message content.",
                },
            },
            "required": ["engineer_id", "message"],
        },
    },
    {
        "name": "architect_engineer_journal_read",
        "description": (
            "Read recent journal entries from a hired engineer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Hired engineer id/slug/name.",
                },
                "since": {
                    "type": "number",
                    "description": "Optional lower-bound timestamp filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum entries to return (default: 20, max: 100).",
                },
                "type_filter": {
                    "type": "string",
                    "enum": ["decision", "observation", "checkpoint", "plan"],
                    "description": "Optional journal entry type filter.",
                },
            },
            "required": ["engineer_id"],
        },
    },
    {
        "name": "architect_reply",
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
            },
            "required": ["message_id", "message"],
        },
    },
    {
        "name": "architect_decision_create",
        "description": "Create a new architect decision log entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Decision title."},
                "rationale": {
                    "type": "string",
                    "description": "Decision rationale.",
                },
                "status": {
                    "type": "string",
                    "enum": ["proposed", "accepted", "revised", "rejected"],
                    "description": "Initial decision status.",
                },
                "supersedes": {
                    "type": "string",
                    "description": "Optional prior decision id.",
                },
                "linked_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional linked task ids.",
                },
                "linked_engineer_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional linked engineer ids.",
                },
            },
            "required": ["title", "rationale"],
        },
    },
    {
        "name": "architect_decision_update",
        "description": "Update an existing decision owned by this architect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Decision id."},
                "title": {"type": "string", "description": "Updated title."},
                "rationale": {
                    "type": "string",
                    "description": "Updated rationale.",
                },
                "status": {
                    "type": "string",
                    "enum": ["proposed", "accepted", "revised", "rejected"],
                    "description": "Updated status.",
                },
                "supersedes": {
                    "type": "string",
                    "description": "Optional prior decision id.",
                },
                "linked_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replacement linked task ids.",
                },
                "linked_engineer_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replacement linked engineer ids.",
                },
                "archived": {
                    "type": "boolean",
                    "description": "Archive or unarchive the decision.",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "architect_decision_link",
        "description": (
            "Append one linked task or engineer id to a decision. Provide exactly "
            "one of task_id or engineer_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Decision id."},
                "task_id": {
                    "type": "string",
                    "description": "Task id/alias to append.",
                },
                "engineer_id": {
                    "type": "string",
                    "description": "Engineer id/slug/name to append.",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "architect_decision_list",
        "description": "List this architect's persisted decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["proposed", "accepted", "revised", "rejected"],
                    "description": "Optional status filter.",
                },
                "include_archived": {
                    "type": "boolean",
                    "description": "Include archived decisions.",
                },
            },
        },
    },
    {
        "name": "architect_journal",
        "description": (
            "Append an entry to this architect's private JSONL journal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["decision", "observation", "checkpoint", "plan"],
                    "description": "Journal entry type.",
                },
                "entry": {
                    "type": "string",
                    "description": "Journal entry content.",
                },
            },
            "required": ["type", "entry"],
        },
    },
    {
        "name": "architect_journal_read",
        "description": "Read recent entries from this architect's journal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "number",
                    "description": "Optional lower-bound timestamp filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum entries to return (default: 20).",
                },
            },
        },
    },
]


def _copy_tool_spec(tool: dict) -> dict:
    return deepcopy(tool)


ARCHITECT_TOOLS = [_copy_tool_spec(tool) for tool in _ARCHITECT_TOOL_SPECS]
_ARCHITECT_TOOL_NAMES = {
    str(tool.get("name", "") or "").strip()
    for tool in ARCHITECT_TOOLS
}


def _stringify_startup_error(error_text: str) -> str:
    text = str(error_text or "").strip()
    if not text:
        return "unknown architect session binding error"
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            return text
        message = str(payload.get("message", "") or "").strip()
        if message:
            return message
    return text


def bound_architect_id_from_env() -> str:
    return str(os.environ.get(_ENV_VAR, "") or "").strip()


def _load_architect_row_from_db(architect_id: str) -> dict | None:
    architect_id = str(architect_id or "").strip()
    if not architect_id:
        return None
    db = LoomDB(DB_FILE)
    db.init()
    try:
        return (db.load_all().get("agents", {}) or {}).get(architect_id)
    finally:
        db.close()


def validate_architect_binding(state=None) -> tuple[str, str]:
    architect_id = bound_architect_id_from_env()
    if not architect_id:
        return "", f"{_ENV_VAR} is required"

    if state is None:
        try:
            record = _load_architect_row_from_db(architect_id)
        except Exception as exc:
            return "", f"failed to load Loom state for architect binding: {exc}"
        if not record:
            return "", f"no architect with id={architect_id} exists"
        if str(record.get("cell_type", "") or "").strip() != "agent":
            return "", f"cell {architect_id} is not an agent"
        kind = str(record.get("kind", "") or "").strip()
        if kind != "architect":
            return (
                "",
                f"agent {architect_id} exists but kind={kind or '<empty>'}; expected architect",
            )
        group = str(record.get("group", "") or "").strip()
        if not group:
            return "", f"architect {architect_id} is not assigned to a group"
        return architect_id, ""

    cell = state.agents.get(architect_id)
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return "", f"no architect with id={architect_id} exists"
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind != "architect":
        return (
            "",
            f"agent {architect_id} exists but kind={kind or '<empty>'}; expected architect",
        )
    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return "", f"architect {architect_id} is not assigned to a group"
    return architect_id, ""


def exit_if_invalid_architect_binding(state=None) -> str:
    architect_id, error_text = validate_architect_binding(state)
    if error_text:
        print(_stringify_startup_error(error_text))
        raise SystemExit(2)
    return architect_id


async def _dispatch_architect_tool(name, args, handle_command, state,
                                   caller_id: str = ""):
    caller_id = str(caller_id or "").strip() or bound_architect_id_from_env()
    if str(name or "").strip() not in _ARCHITECT_TOOL_NAMES:
        return f"Unknown architect tool: {name}", True
    return await dispatch_scoped_tool(
        name,
        args,
        handle_command,
        state,
        tool_prefix="architect_",
        caller_kind="architect",
        caller_id=caller_id,
    )


if __name__ == "__main__":
    sys.exit(serve_http_proxy(exit_if_invalid_architect_binding()))
