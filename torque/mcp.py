"""Minimal MCP (Model Context Protocol) server over streamable HTTP.

Implements just enough of the MCP spec (JSON-RPC 2.0 over POST) to expose
torque ai commands as tools.  No external dependencies — uses aiohttp
routes on the existing server.

Agent identity comes from the ``X-Torque-Cell-Id`` header.  Claude Code
populates it from ``${TORQUE_CELL_ID}`` in ``.mcp.json``; Codex uses
``env_http_headers`` in ``.codex/config.toml``.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from aiohttp import web

from . import __version__
from .mcp_architect import ARCHITECT_TOOLS, _dispatch_architect_tool
from .mcp_engineer import (
    ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES,
    ENGINEER_TOOLS,
    _dispatch_engineer_tool,
    engineer_tools_for_cell,
)
from .mcp_tool_search import deferred_tool_specs, eager_tool_specs
from .mcp_tools_shared import (
    _direct_user_message_response,
    save_agent_user_direct_message_from_mcp,
)
from .mcp_retry import (
    IDEMPOTENCY_HEADER,
    derive_idempotency_key,
    is_mcp_write_tool,
    mcp_idempotency_key_from_body,
    mcp_request_hash,
    mcp_tool_surface,
    record_mcp_health_event_safe,
    strip_mcp_idempotency_args,
)
from .server_artifacts import serialize_task_for_mcp

log = logging.getLogger("torque")

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "torque", "version": __version__}
INSTRUCTIONS = (
    "Torque manages AI agent sessions and tasks in local terminals. "
    "Use these tools to report progress, complete tasks, derive "
    "subtasks, and coordinate with other agents in the pipeline."
)
MCP_SESSION_HEADER = "X-Torque-MCP-Session-Id"
_SESSION_WAKE_DEDUPE_SECS = 60.0
_SESSION_WAKE_SEEN: set[tuple[str, str]] = set()
_SESSION_WAKE_TASKS: set[asyncio.Task] = set()


def _extract_entry_timestamp(entry: dict | None) -> float:
    try:
        return float(((entry or {}).get("timestamp", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _format_session_wake_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def _format_session_wake_age(delta_secs: float) -> str:
    total = max(0, int(delta_secs or 0))
    if total < 60:
        return f"{total}s ago"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 8:
        return f"{weeks}w ago"
    months = days // 30
    if months < 24:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _session_wake_checkpoint_fragment(checkpoint_ts: float,
                                      first_tool_call_ts: float) -> str:
    if checkpoint_ts <= 0:
        return "previous checkpoint none recorded"
    return (
        "previous checkpoint "
        f"{_format_session_wake_timestamp(checkpoint_ts)} "
        f"({_format_session_wake_age(first_tool_call_ts - checkpoint_ts)})"
    )


def _session_wake_deduped(last_entry_ts: float,
                          first_tool_call_ts: float) -> bool:
    if last_entry_ts <= 0 or first_tool_call_ts <= 0:
        return False
    return abs(last_entry_ts - first_tool_call_ts) <= _SESSION_WAKE_DEDUPE_SECS


def _latest_architect_checkpoint_timestamp(state, architect_id: str) -> float:
    for entry in state.architect_journal_read(architect_id, limit=1_000_000):
        if str((entry or {}).get("type", "") or "").strip() == "checkpoint":
            return _extract_entry_timestamp(entry)
    return 0.0


def _latest_engineer_checkpoint_timestamp(state, group: str,
                                          engineer_id: str) -> float:
    entries = state.journal_read(
        group,
        limit=1,
        entry_type="checkpoint",
        author_cell_id=engineer_id,
    )
    return _extract_entry_timestamp(entries[0] if entries else None)


async def _emit_session_wake_entry(state, *, cell_id: str, caller_kind: str,
                                   first_tool_call_ts: float) -> None:
    cell = state.agents.get(str(cell_id or "").strip())
    if not cell:
        return

    current_kind = str(getattr(cell, "kind", "") or "").strip()
    if current_kind != caller_kind or caller_kind not in {"architect", "engineer"}:
        return

    if caller_kind == "architect":
        refresh_peer_cache = getattr(state, "refresh_peer_message_cache_for_agent", None)
        if callable(refresh_peer_cache):
            refresh_peer_cache(cell.id, emit=True)
        latest_entries = state.architect_journal_read(cell.id, limit=1)
        last_entry_ts = _extract_entry_timestamp(
            latest_entries[0] if latest_entries else None
        )
        if _session_wake_deduped(last_entry_ts, first_tool_call_ts):
            return
        group = str(getattr(cell, "group", "") or "").strip() or "(none)"
        entry = (
            "Session wake — "
            f"{_session_wake_checkpoint_fragment(
                _latest_architect_checkpoint_timestamp(state, cell.id),
                first_tool_call_ts,
            )}, architect id {cell.id}, group {group}."
        )
        state.architect_journal_append(cell.id, "observation", entry)
        return

    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return
    latest_entries = state.journal_read(
        group,
        limit=1,
        author_cell_id=cell.id,
    )
    last_entry_ts = _extract_entry_timestamp(
        latest_entries[0] if latest_entries else None
    )
    if _session_wake_deduped(last_entry_ts, first_tool_call_ts):
        return
    name = str(getattr(cell, "name", "") or "").strip() or "(unnamed)"
    slug = str(getattr(cell, "slug", "") or "").strip() or "(none)"
    entry = (
        "Session wake — "
        f"{_session_wake_checkpoint_fragment(
            _latest_engineer_checkpoint_timestamp(state, group, cell.id),
            first_tool_call_ts,
        )}, engineer id {cell.id}, name {name}, slug {slug}, group {group}."
    )
    state.journal_append(group, "observation", entry, author_cell_id=cell.id)


def _log_session_wake_failure(task: asyncio.Task) -> None:
    _SESSION_WAKE_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.exception("Background session wake journaling failed", exc_info=exc)


def _queue_session_wake_entry(state, *, cell_id: str, caller_kind: str,
                              first_tool_call_ts: float) -> None:
    task_ref = asyncio.create_task(
        _emit_session_wake_entry(
            state,
            cell_id=cell_id,
            caller_kind=caller_kind,
            first_tool_call_ts=first_tool_call_ts,
        )
    )
    _SESSION_WAKE_TASKS.add(task_ref)
    task_ref.add_done_callback(_log_session_wake_failure)


def _claim_session_wake(cell_id: str, mcp_session_id: str) -> bool:
    cell_id = str(cell_id or "").strip()
    mcp_session_id = str(mcp_session_id or "").strip()
    if not cell_id or not mcp_session_id:
        return False
    key = (cell_id, mcp_session_id)
    if key in _SESSION_WAKE_SEEN:
        return False
    _SESSION_WAKE_SEEN.add(key)
    return True

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "torque_context",
        "description": (
            "Get current agent identity, status, and linked tasks. "
            "Returns the agent's name, group, directory, worktree info, "
            "and any board tasks currently assigned to this agent. Use "
            "this to understand your current assignment before starting work."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "torque_area_list",
        "description": "Read-only list of Planning Areas in this worker's group. Decision links are counted but hidden.",
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
        "name": "torque_area_show",
        "description": "Read-only show for one same-group Planning Area with worker-visible task links and decision counts only.",
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
        "name": "torque_task_upload_artifact",
        "description": (
            "Upload and attach an image or other artifact to the agent's "
            "current task. Provide a local_path or inline content, and Torque "
            "stores the file on the task and returns normalized artifact metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {
                    "type": "string",
                    "description": "Local filesystem path to upload from.",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Optional filename override. Required for inline uploads."
                    ),
                },
                "content_base64": {
                    "type": "string",
                    "description": (
                        "Base64-encoded file content for binary inline uploads."
                    ),
                },
                "content_text": {
                    "type": "string",
                    "description": (
                        "Plain-text inline content to write as a task artifact."
                    ),
                },
                "artifact_type": {
                    "type": "string",
                    "description": (
                        "Optional artifact type override such as image, diff, log, "
                        "test_report, generated_doc, or file_ref."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional display title for the artifact.",
                },
                "mime_type": {
                    "type": "string",
                    "description": "Optional MIME type override.",
                },
                "summary": {
                    "type": "string",
                    "description": "Optional human-readable summary.",
                },
                "prompt_mode": {
                    "type": "string",
                    "enum": ["auto", "none", "path", "summary", "inline"],
                    "description": "Optional prompt shaping mode.",
                },
            },
        },
    },
    {
        "name": "torque_done",
        "description": (
            "Mark the current task as complete and move it to Done. "
            "Triggers cascade completion — if all sibling tasks of "
            "the parent are also Done, the parent moves to Done too."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Optional completion summary.",
                },
            },
        },
    },
    {
        "name": "torque_blocked",
        "description": (
            "Signal that this agent is blocked and needs human attention. "
            "Sets needs_attention flag and adds a 'blocked' label."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the agent is blocked.",
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "torque_error",
        "description": (
            "Report an unrecoverable error on the current task. "
            "Sets error state and adds an 'error' label."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Description of what went wrong.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "torque_progress",
        "description": (
            "Report current progress on the task. "
            "Updates the agent's activity detail shown in the Torque UI."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Brief description of current activity.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "torque_verify",
        "description": (
            "Record manual verification metadata for the current task, "
            "such as deploy/restart checkpoint state, tests run, "
            "manual smoke completion, and remaining human validation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["pending", "attempted", "passed", "failed"],
                    "description": "Verification state summary.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["deploy", "restart"],
                    "description": "Whether verification is about deploy or restart.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-form verification notes.",
                },
                "tests_run": {
                    "type": "string",
                    "description": "Short summary of tests that were run.",
                },
                "manual_smoke_done": {
                    "type": "boolean",
                    "description": "Whether manual smoke testing was completed.",
                },
                "deploy_needed": {
                    "type": "boolean",
                    "description": "Whether a deploy is still required.",
                },
                "deploy_attempted": {
                    "type": "boolean",
                    "description": "Whether the deploy/restart was attempted.",
                },
                "human_validation_pending": {
                    "type": "string",
                    "description": "What still needs human validation.",
                },
                "test_outcome": {
                    "type": "string",
                    "enum": [
                        "passed",
                        "full_suite_passed",
                        "full_suite_attempted",
                        "unrelated_flake_accepted",
                        "narrower_suite_accepted",
                        "failed",
                    ],
                    "description": "Structured test outcome taxonomy.",
                },
                "full_suite_attempted": {
                    "type": "boolean",
                    "description": "Whether the full test suite was attempted.",
                },
                "unrelated_flake_accepted": {
                    "type": "boolean",
                    "description": "Whether an unrelated flaky failure is accepted with evidence.",
                },
                "isolated_rerun_evidence": {
                    "type": "string",
                    "description": "Focused or isolated rerun evidence supporting flake acceptance.",
                },
                "reviewer_acceptance": {
                    "type": "string",
                    "enum": ["accepted_flake_evidence", "accepted_narrower_suite"],
                    "description": "Reviewer acceptance of flake evidence or narrower-suite coverage.",
                },
                "live_smoke_pending": {
                    "type": "boolean",
                    "description": "Whether live smoke remains pending operator-side.",
                },
            },
        },
    },
    {
        "name": "torque_ready",
        "description": (
            "Signal that this agent is done and ready for the next task. "
            "Moves the task to Done, unlinks the agent, and cascades "
            "completion up the parent chain if all siblings are done."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "torque_name",
        "description": "Rename this agent to reflect the current task objective.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short descriptive name in kebab-case "
                        "(e.g. 'fix-auth-bug')."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "torque_derive",
        "description": (
            "Derive a subtask and dispatch it. The parent task stays "
            "In Progress with a status badge while the derived task "
            "is worked on. The action's transitions field controls "
            "which actions can be derived and where the task is routed "
            "(new agent, self, parent agent, or root agent)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short title for the derived task.",
                },
                "context": {
                    "type": "string",
                    "description": "Longer description or context.",
                },
                "action": {
                    "type": "string",
                    "description": (
                        "Action name for the derived task "
                        "(e.g. 'pipeline/review')."
                    ),
                },
                "action_vars": {
                    "type": "object",
                    "description": "Action variables as key-value pairs.",
                    "additionalProperties": {"type": "string"},
                },
                "group": {
                    "type": "string",
                    "description": (
                        "Target group (defaults to current task's group)."
                    ),
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "torque_ask",
        "description": (
            "Pause for human input only when the current task cannot "
            "proceed safely without a blocking human decision or "
            "approval. Sets the parent task status to 'Awaiting "
            "Input' and creates a task in Backlog with the 'human' "
            "label. Do not use this for status updates, soft "
            "suggestions, or optional follow-up ideas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Short title for the ask task."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Longer description with full context "
                        "(question becomes the title)."
                    ),
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "torque_message_user",
        "description": (
            "Send a non-blocking durable direct message to the user-facing "
            "conversation panel. Use this to answer a `## Message from the "
            "User` injection or to send user-visible context without "
            "creating a Backlog ask task. For blocking decisions or "
            "approvals, use torque_ask instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message content for the user.",
                },
                "reply_to_id": {
                    "type": "string",
                    "description": (
                        "Optional message id this is replying to. Torque "
                        "derives the user lane from the calling agent."
                    ),
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Optional retry key; omit unless explicitly retrying "
                        "the same message."
                    ),
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "torque_reply",
        "description": (
            "Reply to a message from the engineer (orchestrator agent). "
            "The reply is delivered to the engineer in its next event "
            "digest. Only works when you have a pending engineer message. "
            "When multiple Engineer follow-up tasks are open, include the "
            "task id to choose which message you are answering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Optional task ID for the specific Engineer "
                        "follow-up you are answering."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Your reply to the engineer.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "torque_memory_publish",
        "description": (
            "Publish an explicit shared memory entry for the current "
            "task, pipeline, group, or project. Durable decisions/warnings "
            "are preserved automatically; transient notes can decay over time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_type": {
                    "type": "string",
                    "enum": ["finding", "decision", "warning", "handoff", "note"],
                    "description": "Type of memory entry to publish.",
                },
                "content": {
                    "type": "string",
                    "description": "The shared memory content.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional short title.",
                },
                "scope_kind": {
                    "type": "string",
                    "enum": ["task", "pipeline", "group", "project"],
                    "description": (
                        "Optional scope. Defaults to task when the agent "
                        "has an active task, otherwise group."
                    ),
                },
                "scope_ref": {
                    "type": "string",
                    "description": "Optional explicit scope reference.",
                },
                "pinned": {
                    "type": "boolean",
                    "description": "Whether to pin this entry.",
                },
                "retention_kind": {
                    "type": "string",
                    "enum": ["durable", "transient"],
                    "description": (
                        "Optional retention override. Defaults from entry "
                        "type; pinned entries always become durable."
                    ),
                },
            },
            "required": ["entry_type", "content"],
        },
    },
    {
        "name": "torque_memory_list",
        "description": (
            "List shared memory entries with deterministic filtering by "
            "scope, type, pin, and simple text search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope_kind": {
                    "type": "string",
                    "enum": ["task", "pipeline", "group", "project"],
                    "description": "Optional scope filter.",
                },
                "scope_ref": {
                    "type": "string",
                    "description": "Optional explicit scope reference.",
                },
                "entry_type": {
                    "type": "string",
                    "enum": ["finding", "decision", "warning", "handoff", "note"],
                    "description": "Optional entry type filter.",
                },
                "pinned_only": {
                    "type": "boolean",
                    "description": "Only return pinned entries.",
                },
                "search": {
                    "type": "string",
                    "description": "Simple text search over title and content.",
                },
                "linked_target_kind": {
                    "type": "string",
                    "enum": ["task", "agent", "pipeline"],
                    "description": "Optional linked-target filter.",
                },
                "linked_target_ref": {
                    "type": "string",
                    "description": "Optional explicit linked target reference.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum entries to return (default: 20).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Offset for pagination (default: 0).",
                },
            },
        },
    },
    {
        "name": "torque_memory_read",
        "description": "Read one shared memory entry, including its links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Memory entry ID.",
                },
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "torque_memory_pin",
        "description": "Pin a shared memory entry so it stays high-signal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Memory entry ID.",
                },
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "torque_memory_link",
        "description": (
            "Link a memory entry to a task, agent, or pipeline so it is "
            "discoverable outside its primary scope."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Memory entry ID.",
                },
                "target_kind": {
                    "type": "string",
                    "enum": ["task", "agent", "pipeline"],
                    "description": "What to link the entry to.",
                },
                "target_ref": {
                    "type": "string",
                    "description": "Optional explicit target ID/slug.",
                },
            },
            "required": ["entry_id", "target_kind"],
        },
    },
    {
        "name": "torque_memory_unpin",
        "description": "Remove the pin from a shared memory entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Memory entry ID.",
                },
            },
            "required": ["entry_id"],
        },
    },
]

_TOOL_MAP = {t["name"]: t for t in TOOLS}

# Combined tool list
ALL_TOOLS = TOOLS + ARCHITECT_TOOLS + ENGINEER_TOOLS
_ALL_TOOL_MAP = {t["name"]: t for t in ALL_TOOLS}


def _visible_tools(state, cell_id: str):
    """Return the MCP tool list visible to the caller."""
    tools = list(TOOLS)
    cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
    if cell and state.agent_is_tombstoned(cell):
        return []
    caller_kind = str(getattr(cell, "kind", "") or "").strip() if cell else ""
    if caller_kind == "engineer":
        tools.extend(eager_tool_specs(engineer_tools_for_cell(cell, state)))
    elif caller_kind == "architect":
        tools.extend(eager_tool_specs(ARCHITECT_TOOLS))
    return tools


def _deferred_tools_for_caller(state, cell_id: str):
    """Return deferred MCP tool schemas available to the caller."""
    cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
    if cell and state.agent_is_tombstoned(cell):
        return []
    caller_kind = str(getattr(cell, "kind", "") or "").strip() if cell else ""
    if caller_kind == "engineer":
        return deferred_tool_specs(engineer_tools_for_cell(cell, state))
    if caller_kind == "architect":
        return deferred_tool_specs(ARCHITECT_TOOLS)
    return []


def _tool_hidden_for_caller(tool_name: str, caller_kind: str, caller_cell) -> bool:
    """Return True when a known tool is intentionally scoped out."""
    name = str(tool_name or "").strip()
    if caller_kind == "engineer" and name in ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES:
        return not bool(
            str(getattr(caller_cell, "hired_by_architect_id", "") or "").strip()
        )
    return False


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

async def _dispatch_tool(name, args, cell_id, handle_command, state, *,
                         idempotency_key: str = ""):
    """Execute a tool call and return (content_text, is_error)."""

    if name == "torque_context":
        cell = state.agents.get(cell_id)
        if not cell:
            return f"Agent {cell_id} not found", True
        if state.agent_is_tombstoned(cell):
            return f"Agent {cell_id} is tombstoned", True
        from dataclasses import asdict
        tasks = {tid: serialize_task_for_mcp(t, tasks_by_id=state.board_tasks)
                 for tid, t in state.board_tasks.items()
                 if t.agent_id == cell_id}
        return json.dumps({"agent": asdict(cell), "tasks": tasks},
                          indent=2), False

    if name in {"torque_area_list", "torque_area_show"}:
        cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
        if not cell:
            return f"Agent {cell_id} not found", True
        if state.agent_is_tombstoned(cell):
            return f"Agent {cell_id} is tombstoned", True
        group = str(getattr(cell, "group", "") or "").strip()
        if not group:
            return "agent is not assigned to a group", True
        visible_task_ids = {
            task_id for task_id, task in state.board_tasks.items()
            if str(getattr(task, "agent_id", "") or "").strip() == str(cell_id or "").strip()
        }
        if name == "torque_area_show":
            ident = str(args.get("area", "") or args.get("area_id", "") or args.get("id", "") or "").strip()
            if not ident:
                return "area is required", True
            area_id = state.resolve_area_id(ident, group=group)
            area = state.load_area(area_id)
            if not area or str(area.get("group_name", "") or "").strip() != group:
                return "Area not found", True
            try:
                note_limit = min(max(int(args.get("note_limit", 50) or 50), 1), 100)
            except (TypeError, ValueError):
                note_limit = 50
            payload = state.area_payload(
                area_id,
                visible_task_ids=visible_task_ids,
                visible_decision_ids=set(),
                decision_details=False,
                note_limit=note_limit,
            )
            if not payload:
                return "Area not found", True
            payload["type"] = "area"
            return json.dumps(payload, separators=(",", ":")), False
        try:
            limit = min(max(int(args.get("limit", 50) or 50), 1), 100)
        except (TypeError, ValueError):
            limit = 50
        areas = []
        for item in state.list_areas(
                group=group,
                include_archived=bool(args.get("include_archived", False)),
                limit=limit):
            payload = state.area_payload(
                item["id"],
                visible_task_ids=visible_task_ids,
                visible_decision_ids=set(),
                include_links=bool(args.get("include_links", False)),
                include_notes=bool(args.get("include_notes", False)),
                decision_details=False,
                note_limit=10,
            ) or item
            areas.append(payload)
        return json.dumps({"type": "area_list", "group": group, "areas": areas}, separators=(",", ":")), False

    if name == "torque_task_upload_artifact":
        payload = {"cmd": "task_upload_artifact", "cell_id": cell_id}
        for key in (
            "local_path",
            "filename",
            "content_base64",
            "content_text",
            "artifact_type",
            "title",
            "mime_type",
            "summary",
            "prompt_mode",
        ):
            if key in args:
                payload[key] = args[key]
        if idempotency_key:
            payload["idempotency_key"] = derive_idempotency_key(
                idempotency_key,
                payload,
            )
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "torque_message_user":
        cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
        if not cell:
            return f"Agent {cell_id} not found", True
        if state.agent_is_tombstoned(cell):
            return f"Agent {cell_id} is tombstoned", True
        message = str(args.get("message", "") or "").strip()
        if not message:
            return "message is required", True
        try:
            saved, created = save_agent_user_direct_message_from_mcp(
                state,
                cell,
                message=message,
                thread_id=str(args.get("thread_id", "") or "").strip(),
                reply_to_id=str(args.get("reply_to_id", "") or "").strip(),
                idempotency_key=idempotency_key,
                notify=True,
            )
        except ValueError as exc:
            return str(exc), True
        return json.dumps(
            _direct_user_message_response(saved, deduped=not created)
        ), False

    if name in {
        "torque_memory_publish",
        "torque_memory_list",
        "torque_memory_read",
        "torque_memory_pin",
        "torque_memory_link",
        "torque_memory_unpin",
    }:
        payload = {"cell_id": cell_id}
        if name == "torque_memory_publish":
            payload["cmd"] = "memory_publish"
            payload["entry_type"] = args.get("entry_type", "")
            payload["content"] = args.get("content", "")
            if args.get("title"):
                payload["title"] = args["title"]
            if args.get("scope_kind"):
                payload["scope_kind"] = args["scope_kind"]
            if args.get("scope_ref"):
                payload["scope_ref"] = args["scope_ref"]
            if "pinned" in args:
                payload["pinned"] = bool(args["pinned"])
            if args.get("retention_kind"):
                payload["retention_kind"] = args["retention_kind"]
        elif name == "torque_memory_list":
            payload["cmd"] = "memory_list"
            for key in (
                "scope_kind", "scope_ref", "entry_type",
                "pinned_only", "search",
                "linked_target_kind", "linked_target_ref",
                "limit", "offset",
            ):
                if key in args:
                    payload[key] = args[key]
        elif name == "torque_memory_read":
            payload["cmd"] = "memory_read"
            payload["entry_id"] = args.get("entry_id", "")
        elif name == "torque_memory_pin":
            payload["cmd"] = "memory_pin"
            payload["entry_id"] = args.get("entry_id", "")
        elif name == "torque_memory_link":
            payload["cmd"] = "memory_link"
            payload["entry_id"] = args.get("entry_id", "")
            payload["target_kind"] = args.get("target_kind", "")
            if args.get("target_ref"):
                payload["target_ref"] = args["target_ref"]
        else:
            payload["cmd"] = "memory_unpin"
            payload["entry_id"] = args.get("entry_id", "")

        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    # Map tool name → ai_report action + argument extraction
    action_map = {
        "torque_done":     "done",
        "torque_blocked":  "blocked",
        "torque_error":    "error",
        "torque_progress": "progress",
        "torque_verify":   "verify",
        "torque_ready":    "ready",
        "torque_name":     "name",
        "torque_derive":   "derive",
        "torque_ask":      "ask",
        "torque_reply":    "reply",
    }
    action = action_map.get(name)
    if not action:
        return f"Unknown tool: {name}", True

    # Build the ai_report command payload
    payload = {"cmd": "ai_report", "cell_id": cell_id, "action": action}
    if idempotency_key:
        payload["idempotency_key"] = derive_idempotency_key(
            idempotency_key,
            payload,
        )

    if action == "done":
        if args.get("message"):
            payload["message"] = args["message"]
    elif action == "blocked":
        payload["message"] = args.get("reason", "")
    elif action == "error":
        payload["message"] = args.get("message", "")
    elif action == "progress":
        payload["message"] = args.get("message", "")
    elif action == "verify":
        if "mode" in args:
            payload["verification_mode"] = args.get("mode", "")
        if "state" in args:
            payload["verification_state"] = args.get("state", "")
        if "notes" in args:
            payload["verification_notes"] = args.get("notes", "")
        for key in (
            "tests_run",
            "manual_smoke_done",
            "deploy_needed",
            "deploy_attempted",
            "human_validation_pending",
            "test_outcome",
            "full_suite_attempted",
            "unrelated_flake_accepted",
            "isolated_rerun_evidence",
            "reviewer_acceptance",
            "live_smoke_pending",
        ):
            if key in args:
                payload[key] = args[key]
    elif action == "ready":
        pass
    elif action == "name":
        payload["message"] = args.get("name", "")
    elif action == "derive":
        payload["message"] = args.get("description", "")
        if args.get("context"):
            payload["description"] = args["context"]
        if args.get("action"):
            payload["action_name"] = args["action"]
        if args.get("action_vars"):
            payload["action_vars"] = args["action_vars"]
        if args.get("group"):
            payload["group"] = args["group"]
    elif action == "ask":
        payload["message"] = args.get("question", "")
        if args.get("description"):
            payload["description"] = args["description"]
    elif action == "reply":
        payload["message"] = args.get("message", "")
        if args.get("task"):
            payload["task_id"] = args["task"]

    result = await handle_command(payload)
    if result and result.get("type") == "error":
        return result.get("message", "Unknown error"), True
    if result and result.get("type") == "deliverable_missing":
        # Hard gate: surface to the worker as a tool failure so the
        # MCP client treats the response as an error and the worker
        # can react (upload artifact, then retry). Return a 3-tuple so
        # the MCP cache layer in dispatch_mcp_rpc_body knows NOT to
        # save this refusal as a successful idempotency response —
        # otherwise a same-key retry after the artifact is uploaded
        # would replay the cached refusal instead of re-running the
        # gate. Same vector as the /api/cmd and replay paths.
        return (
            result.get(
                "message",
                "Deliverable artifact required before completion.",
            ),
            True,
            False,  # no_cache
        )
    if result and result.get("type") == "review_required":
        # Mandatory-review hard gate (TORQUE:256). Same recoverable-
        # refusal shape as deliverable_missing — surface to the worker
        # as an MCP tool error and signal no_cache so a retry after the
        # worker derives the review re-runs the gate.
        return (
            result.get(
                "message",
                "Review required by action contract before completion.",
            ),
            True,
            False,  # no_cache
        )

    return json.dumps(result) if result else '{"type":"ok"}', False


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _jsonrpc_ok(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# aiohttp handler / replayable dispatch
# ---------------------------------------------------------------------------

def _idempotency_conflict_result(req_id, tool_name: str):
    return _jsonrpc_error(
        req_id,
        -32602,
        (
            "Idempotency key was reused for a different MCP write"
            f" ({tool_name})"
        ),
    )


async def dispatch_mcp_rpc_body(
    body: dict,
    *,
    cell_id: str,
    handle_command,
    state,
    idempotency_header: str = "",
    mcp_session_id: str = "",
) -> tuple[dict, int]:
    """Dispatch one parsed MCP JSON-RPC body and return (payload, HTTP status).

    This function is shared by the live /mcp endpoint and failed-write replay
    on daemon boot.  Tool response shapes are unchanged; idempotency is
    server-side and only uses hidden keys generated by Torque-owned clients.
    """

    # JSON-RPC notifications (no "id") — just acknowledge
    if "id" not in body:
        method = body.get("method", "")
        log.info("MCP notification: %s", method)
        return {}, 202

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    log.info(
        "MCP %s id=%s cell=%s",
        method,
        req_id,
        cell_id[:8] if cell_id else "?",
    )

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }
        return _jsonrpc_ok(req_id, result), 200

    if method == "ping":
        return _jsonrpc_ok(req_id, {}), 200

    if method == "tools/list":
        return _jsonrpc_ok(req_id, {"tools": _visible_tools(state, cell_id)}), 200

    if method == "tools/call":
        first_tool_call_ts = time.time()
        tool_name = params.get("name", "") if isinstance(params, dict) else ""
        raw_arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        arguments = strip_mcp_idempotency_args(raw_arguments)
        caller_cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
        caller_kind = str(
            getattr(caller_cell, "kind", "") or ""
        ).strip() if caller_cell else ""
        if caller_cell and state.agent_is_tombstoned(caller_cell):
            return (
                _jsonrpc_error(req_id, -32602, f"Agent {cell_id} is tombstoned"),
                200,
            )
        session_wake_pending = (
            caller_kind in {"architect", "engineer"}
            and _claim_session_wake(cell_id, mcp_session_id)
        )
        if _tool_hidden_for_caller(tool_name, caller_kind, caller_cell):
            if session_wake_pending:
                _queue_session_wake_entry(
                    state,
                    cell_id=cell_id,
                    caller_kind=caller_kind,
                    first_tool_call_ts=first_tool_call_ts,
                )
            return (
                _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}"),
                200,
            )
        write_tool = is_mcp_write_tool(tool_name)
        idempotency_key = ""
        request_hash = ""
        db = getattr(state, "db", None)
        if write_tool:
            idempotency_key = mcp_idempotency_key_from_body(
                body,
                header_key=idempotency_header,
            )
            if idempotency_key and db:
                request_hash = mcp_request_hash(body)
                existing = db.load_mcp_idempotency(idempotency_key)
                if existing:
                    if (
                        str(existing.get("request_hash", "") or "")
                        and str(existing.get("request_hash", "") or "") != request_hash
                    ):
                        record_mcp_health_event_safe(
                            db,
                            surface=mcp_tool_surface(tool_name),
                            tool_name=tool_name,
                            event="idempotency_conflict",
                        )
                        if session_wake_pending:
                            _queue_session_wake_entry(
                                state,
                                cell_id=cell_id,
                                caller_kind=caller_kind,
                                first_tool_call_ts=first_tool_call_ts,
                            )
                        return _idempotency_conflict_result(req_id, tool_name), 200
                    try:
                        cached = json.loads(existing.get("response_json", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        cached = {}
                    record_mcp_health_event_safe(
                        db,
                        surface=mcp_tool_surface(tool_name),
                        tool_name=tool_name,
                        event="dedupe",
                    )
                    if session_wake_pending:
                        _queue_session_wake_entry(
                            state,
                            cell_id=cell_id,
                            caller_kind=caller_kind,
                            first_tool_call_ts=first_tool_call_ts,
                        )
                    return _jsonrpc_ok(req_id, cached), 200

        if tool_name not in _ALL_TOOL_MAP:
            if session_wake_pending:
                _queue_session_wake_entry(
                    state,
                    cell_id=cell_id,
                    caller_kind=caller_kind,
                    first_tool_call_ts=first_tool_call_ts,
                )
            return (
                _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}"),
                200,
            )

        # Default: cache successful responses (and most errors) on the
        # idempotency key. Set to False by a dispatcher when the response
        # is a recoverable refusal (e.g. deliverable_missing) that the
        # worker can flip to passing by retrying after side-effects.
        cacheable = True
        if tool_name.startswith("engineer_"):
            if not cell_id:
                result = {
                    "content": [{
                        "type": "text",
                        "text": (
                            "X-Torque-Cell-Id header is required"
                            " — engineer tools only work inside a"
                            " Torque-managed engineer session"
                        ),
                    }],
                    "isError": True,
                }
            elif caller_kind != "engineer":
                result = {
                    "content": [{
                        "type": "text",
                        "text": (
                            "engineer tools are only available inside a "
                            "Torque-managed engineer session"
                        ),
                    }],
                    "isError": True,
                }
            else:
                _ret = await _dispatch_engineer_tool(
                    tool_name, arguments, handle_command, state,
                    caller_id=cell_id,
                    idempotency_key=idempotency_key,
                )
                text = _ret[0]
                is_error = _ret[1]
                if len(_ret) > 2 and _ret[2] is False:
                    cacheable = False
                result = {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                }
        elif tool_name.startswith("architect_"):
            if not cell_id:
                result = {
                    "content": [{
                        "type": "text",
                        "text": (
                            "X-Torque-Cell-Id header is required"
                            " — architect tools only work inside a"
                            " Torque-managed architect session"
                        ),
                    }],
                    "isError": True,
                }
            elif caller_kind != "architect":
                result = {
                    "content": [{
                        "type": "text",
                        "text": (
                            "architect tools are only available inside a "
                            "Torque-managed architect session"
                        ),
                    }],
                    "isError": True,
                }
            else:
                _ret = await _dispatch_architect_tool(
                    tool_name, arguments, handle_command, state,
                    caller_id=cell_id,
                    idempotency_key=idempotency_key,
                )
                text = _ret[0]
                is_error = _ret[1]
                if len(_ret) > 2 and _ret[2] is False:
                    cacheable = False
                result = {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                }
        else:
            if not cell_id:
                result = {
                    "content": [{
                        "type": "text",
                        "text":
                            "X-Torque-Cell-Id header is required"
                            " — this tool only works inside a"
                            " Torque-managed agent session",
                    }],
                    "isError": True,
                }
            else:
                _ret = await _dispatch_tool(
                    tool_name, arguments, cell_id,
                    handle_command, state,
                    idempotency_key=idempotency_key,
                )
                # _dispatch_tool may return a 2-tuple (text, is_error) or
                # a 3-tuple (text, is_error, cacheable). The deliverable
                # gate returns the 3-tuple with cacheable=False so this
                # refusal is NOT saved as an idempotency response —
                # otherwise a same-key retry after the worker uploads
                # the artifact would replay the cached refusal instead
                # of re-running the gate.
                text = _ret[0]
                is_error = _ret[1]
                if len(_ret) > 2 and _ret[2] is False:
                    cacheable = False
                result = {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                }

        if write_tool and idempotency_key and db and cacheable:
            try:
                db.save_mcp_idempotency(
                    idempotency_key=idempotency_key,
                    surface=mcp_tool_surface(tool_name),
                    tool_name=tool_name,
                    request_hash=request_hash or mcp_request_hash(body),
                    response=result,
                )
            except Exception:
                log.exception(
                    "Failed to persist MCP idempotency key for %s",
                    tool_name,
                )

        if session_wake_pending:
            _queue_session_wake_entry(
                state,
                cell_id=cell_id,
                caller_kind=caller_kind,
                first_tool_call_ts=first_tool_call_ts,
            )
        return _jsonrpc_ok(req_id, result), 200

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}"), 200


def create_mcp_handler(handle_command, state):
    """Return an aiohttp POST handler for the /mcp endpoint."""

    async def handle_mcp(request):
        # Read cell_id from header
        cell_id = request.headers.get("X-Torque-Cell-Id", "")

        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                _jsonrpc_error(None, -32700, "Parse error"), status=200)

        payload, status = await dispatch_mcp_rpc_body(
            body,
            cell_id=cell_id,
            handle_command=handle_command,
            state=state,
            idempotency_header=request.headers.get(IDEMPOTENCY_HEADER, ""),
            mcp_session_id=request.headers.get(MCP_SESSION_HEADER, ""),
        )
        if status == 202:
            return web.Response(status=202)
        return web.json_response(payload, status=status)

    return handle_mcp
