"""Architect MCP tool entrypoint helpers.

This module exposes the ``architect_*`` tool namespace and provides
session-binding helpers for transports that bind an architect session via
``TORQUE_ARCHITECT_ID``.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

from .config import DB_FILE
from .db import TorqueDB
from .mcp_stdio_proxy import serve_http_proxy
from .mcp_tools_shared import authorize_caller, dispatch_scoped_tool
from .mcp_tool_search import make_tool_search_spec, tool_search_response

_ENV_VAR = "TORQUE_ARCHITECT_ID"

ARCHITECT_DEFERRED_TOOL_NAMES = {
    "architect_get_architect_settings",
    "architect_engineer_dismiss",
    "architect_engineer_rehire",
    "architect_engineer_restore",
    "architect_mcp_calls",
}


_ARCHITECT_TOOL_SPECS = [
    make_tool_search_spec("architect_tool_search", "architect"),
    {
        "name": "architect_board_summary",
        "description": (
            "Return a compact board overview for this architect's group, "
            "including task creator attribution and a bounded lightweight "
            "task summary excerpt, board_sync state when present, and "
            "peer-message attention counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "specialization_engineer_id": {
                    "type": "string",
                    "description": (
                        "Optional engineer id/slug/name. When set, the task "
                        "excerpt is filtered to tasks whose "
                        "suggested_specialization matches one of that "
                        "engineer's specializations."
                    ),
                },
            },
        },
    },
    {
        "name": "architect_events_recent",
        "description": (
            "Return recent architect-scoped coarse panel events with task, "
            "engineer, worker-owner, creator, digest-recipient, and "
            "peer-message attribution."
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
        "name": "architect_mcp_calls",
        "deferred": True,
        "description": (
            "Return recent MCP call history for this architect's group. "
            "Defaults to mcp__torque__ tools; optionally filter by agent, "
            "tool pattern, hook event name, time, and limit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent id/slug/name in this architect's group.",
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
        "name": "architect_deploy_state",
        "description": (
            "Return read-only daemon boot git state and pending mainline "
            "commit count since boot for deploy observability."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_get_architect_settings",
        "deferred": True,
        "description": "Read this group's persisted Architect settings.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_task_show",
        "description": (
            "Show full details for one task the architect can see, including "
            "board_sync state when present."
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
        "name": "architect_task_list",
        "description": (
            "List tasks in this architect's group with optional backlog "
            "filters for labels, lane, assigned engineer, creator, and "
            "archived state. Includes board_sync state when present. Label "
            "filters use AND semantics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "label_filter": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": (
                        "Optional label or labels. When multiple labels are "
                        "provided, tasks must have all labels."
                    ),
                },
                "lane_filter": {
                    "type": "string",
                    "description": "Optional exact board lane name.",
                },
                "assigned_engineer_id_filter": {
                    "type": "string",
                    "description": "Optional exact assigned engineer id.",
                },
                "creator_filter": {
                    "type": "string",
                    "description": (
                        "Optional creator filter: user, architect, "
                        "engineer:<id>, or system."
                    ),
                },
                "archived": {
                    "type": "boolean",
                    "description": (
                        "When false/default, exclude archived tasks. When "
                        "true, include archived tasks only."
                    ),
                },
                "include_engineer_messages": {
                    "type": "boolean",
                    "description": (
                        "Default false excludes torque:engineer-message "
                        "follow-up tasks from actionable task lists; set true "
                        "to audit those message follow-ups explicitly."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum tasks to return (default 100).",
                },
            },
        },
    },
    {
        "name": "architect_task_chain",
        "description": (
            "Show the full derived-task tree for one visible pipeline, rooted at "
            "the pipeline root and annotated with summary stats."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID, slug, or alias from the pipeline.",
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
                "suggested_specialization": {
                    "type": "string",
                    "description": (
                        "Optional non-binding routing hint: a specialization "
                        "slug (e.g. 'ui-ux'). When set, the response includes "
                        "a warning if the assigned engineer does not carry "
                        "that specialization."
                    ),
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
                "deliverable": {
                    "type": "object",
                    "description": (
                        "Optional deliverable contract. When `required: true` "
                        "is set, the worker must upload a matching artifact "
                        "via `torque_task_upload_artifact` before "
                        "`torque_done`/`torque_ready` will be accepted."
                    ),
                    "properties": {
                        "required": {"type": "boolean"},
                        "type": {"type": "string"},
                        "format": {"type": "string"},
                        "artifact_title": {"type": "string"},
                    },
                },
            },
            "required": ["title", "group", "assigned_engineer_id"],
        },
    },
    {
        "name": "architect_task_update",
        "description": (
            "Update title, description, labels, and/or action binding for a "
            "task in this architect's group, provided the task was created "
            "by this architect or by the user. Tasks created by other architects, "
            "engineers, or system-derived (parent/pipeline) tasks remain "
            "off-limits. Omitted fields are left unchanged; labels use "
            "replace semantics; action_vars also use replace semantics. "
            "Non-empty action_name values are validated against "
            "ActionManager.list_actions() for the architect's group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID or alias."},
                "title": {"type": "string", "description": "Replacement title."},
                "description": {
                    "type": "string",
                    "description": "Replacement description.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Replacement label list. Provide [] to clear labels."
                    ),
                },
                "action_name": {
                    "type": "string",
                    "description": (
                        "Replacement action name to bind to the task. Must "
                        "exist in ActionManager.list_actions() for this group. "
                        "Provide an empty string to clear the binding."
                    ),
                },
                "action_vars": {
                    "type": "object",
                    "description": (
                        "Replacement structured action variables. Provide {} "
                        "to clear action variables."
                    ),
                },
            },
            "required": ["task"],
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
        "name": "architect_task_move",
        "description": (
            "Move any visible task in this architect's group to an existing "
            "board lane, optionally clearing its status badge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID or alias."},
                "new_lane": {
                    "type": "string",
                    "description": "Target lane name. Must already exist.",
                },
                "clear_status": {
                    "type": "boolean",
                    "description": "Clear the task's status badge while moving it.",
                },
            },
            "required": ["task", "new_lane"],
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
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_tombstoned": {
                    "type": "boolean",
                    "description": "Include engineers in the 7-day restore window.",
                },
            },
        },
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
        "name": "architect_engineer_restore",
        "description": (
            "Restore a hired engineer that is in the 7-day recently-deleted "
            "window. Ownership transfers performed at delete time are not undone."
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
        "name": "architect_peer_list",
        "description": (
            "List same-group Architect peers that can receive direct "
            "Architect peer messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_dismissed": {
                    "type": "boolean",
                    "description": "Include dismissed same-group Architects.",
                },
            },
        },
    },
    {
        "name": "architect_peer_message",
        "description": (
            "Send a durable same-group direct message to another Architect."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "architect_id": {
                    "type": "string",
                    "description": "Recipient Architect id/slug/name.",
                },
                "message": {
                    "type": "string",
                    "description": "Message content (combined with context_summary max ~16 KiB).",
                },
                "ack_required": {
                    "type": "boolean",
                    "description": "Whether this message requires a reply.",
                },
                "context_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional same-group task ids/aliases to snapshot.",
                },
                "context_engineer_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional visible same-group Engineer ids/slugs/names to snapshot.",
                },
                "context_decision_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional caller-owned decision ids to snapshot.",
                },
                "context_summary": {
                    "type": "string",
                    "description": "Optional concise context summary.",
                },
            },
            "required": ["architect_id", "message"],
        },
    },
    {
        "name": "architect_peer_inbox",
        "description": (
            "Read durable same-group Architect peer message threads involving "
            "this Architect."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "peer_architect_id": {
                    "type": "string",
                    "description": "Optional peer Architect id/slug/name filter.",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Optional thread id filter.",
                },
                "requires_reply": {
                    "type": "boolean",
                    "description": "Only return threads with an unanswered incoming ack-required message.",
                },
                "since": {
                    "type": "number",
                    "description": "Optional unix timestamp lower bound.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum threads to return (default 20, max 100).",
                },
            },
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
                    "enum": [
                        "decision", "observation", "checkpoint", "plan",
                        "note_dismissed", "qa",
                    ],
                    "description": "Optional journal entry type filter.",
                },
            },
            "required": ["engineer_id"],
        },
    },
    {
        "name": "architect_engineer_pending_question",
        "description": (
            "Read the current blocking human-input question for a hired "
            "engineer, if one is pending."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Hired engineer id/slug/name.",
                },
            },
            "required": ["engineer_id"],
        },
    },
    {
        "name": "architect_reply",
        "description": (
            "Reply to an existing Architect↔Engineer or Architect↔Architect "
            "message thread."
        ),
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
                        "For Architect peer replies, whether this follow-up "
                        "requires a reply."
                    ),
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
    copied = deepcopy(tool)
    if str(copied.get("name", "") or "").strip() in ARCHITECT_DEFERRED_TOOL_NAMES:
        copied["deferred"] = True
    return copied


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
    db = TorqueDB(DB_FILE)
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
            return "", f"failed to load Torque state for architect binding: {exc}"
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
                                   caller_id: str = "",
                                   idempotency_key: str = ""):
    caller_id = str(caller_id or "").strip() or bound_architect_id_from_env()
    if str(name or "").strip() not in _ARCHITECT_TOOL_NAMES:
        return f"Unknown architect tool: {name}", True
    if str(name or "").strip() == "architect_tool_search":
        _cell, _group, _kind, error_text, is_error = authorize_caller(
            state,
            caller_kind="architect",
            caller_id=caller_id,
        )
        if is_error:
            return error_text, True
        return tool_search_response(ARCHITECT_TOOLS, args), False
    return await dispatch_scoped_tool(
        name,
        args,
        handle_command,
        state,
        tool_prefix="architect_",
        caller_kind="architect",
        caller_id=caller_id,
        idempotency_key=idempotency_key,
    )


if __name__ == "__main__":
    sys.exit(serve_http_proxy(exit_if_invalid_architect_binding()))
