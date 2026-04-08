"""Minimal MCP (Model Context Protocol) server over streamable HTTP.

Implements just enough of the MCP spec (JSON-RPC 2.0 over POST) to expose
loom ai commands as tools.  No external dependencies — uses aiohttp
routes on the existing server.

Agent identity comes from the ``X-Loom-Cell-Id`` header.  Claude Code
populates it from ``${LOOM_CELL_ID}`` in ``.mcp.json``; Codex uses
``env_http_headers`` in ``.codex/config.toml``.
"""

import json
import logging

from aiohttp import web

from .mcp_weaver import WEAVER_TOOLS, _dispatch_weaver_tool

log = logging.getLogger("loom")

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "loom", "version": "1.0.0"}
INSTRUCTIONS = (
    "Loom manages AI agent sessions and tasks in iTerm2. "
    "Use these tools to report progress, complete tasks, derive "
    "subtasks, and coordinate with other agents in the pipeline."
)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "loom_context",
        "description": (
            "Get current agent identity, status, and linked tasks. "
            "Returns the agent's name, group, directory, worktree info, "
            "and any board tasks currently assigned to this agent. Use "
            "this to understand your current assignment before starting work."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "loom_done",
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
        "name": "loom_blocked",
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
        "name": "loom_error",
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
        "name": "loom_progress",
        "description": (
            "Report current progress on the task. "
            "Updates the agent's activity detail shown in the Loom UI."
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
        "name": "loom_verify",
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
            },
        },
    },
    {
        "name": "loom_ready",
        "description": (
            "Signal that this agent is done and ready for the next task. "
            "Moves the task to Done, unlinks the agent, and cascades "
            "completion up the parent chain if all siblings are done."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "loom_name",
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
        "name": "loom_derive",
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
        "name": "loom_ask",
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
        "name": "loom_reply",
        "description": (
            "Reply to a message from the weaver (orchestrator agent). "
            "The reply is delivered to the weaver in its next event "
            "digest. Only works when you have a pending weaver message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Your reply to the weaver.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "loom_memory_publish",
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
        "name": "loom_memory_list",
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
        "name": "loom_memory_read",
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
        "name": "loom_memory_pin",
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
        "name": "loom_memory_link",
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
        "name": "loom_memory_unpin",
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

# Combined tool list (agent + weaver tools)
ALL_TOOLS = TOOLS + WEAVER_TOOLS
_ALL_TOOL_MAP = {t["name"]: t for t in ALL_TOOLS}


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

async def _dispatch_tool(name, args, cell_id, handle_command, state):
    """Execute a tool call and return (content_text, is_error)."""

    if name == "loom_context":
        cell = state.agents.get(cell_id)
        if not cell:
            return f"Agent {cell_id} not found", True
        from dataclasses import asdict
        tasks = {tid: asdict(t) for tid, t in state.board_tasks.items()
                 if t.agent_id == cell_id}
        return json.dumps({"agent": asdict(cell), "tasks": tasks},
                          indent=2), False

    if name in {
        "loom_memory_publish",
        "loom_memory_list",
        "loom_memory_read",
        "loom_memory_pin",
        "loom_memory_link",
        "loom_memory_unpin",
    }:
        payload = {"cell_id": cell_id}
        if name == "loom_memory_publish":
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
        elif name == "loom_memory_list":
            payload["cmd"] = "memory_list"
            for key in (
                "scope_kind", "scope_ref", "entry_type",
                "pinned_only", "search",
                "linked_target_kind", "linked_target_ref",
                "limit", "offset",
            ):
                if key in args:
                    payload[key] = args[key]
        elif name == "loom_memory_read":
            payload["cmd"] = "memory_read"
            payload["entry_id"] = args.get("entry_id", "")
        elif name == "loom_memory_pin":
            payload["cmd"] = "memory_pin"
            payload["entry_id"] = args.get("entry_id", "")
        elif name == "loom_memory_link":
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
        "loom_done":     "done",
        "loom_blocked":  "blocked",
        "loom_error":    "error",
        "loom_progress": "progress",
        "loom_verify":   "verify",
        "loom_ready":    "ready",
        "loom_name":     "name",
        "loom_derive":   "derive",
        "loom_ask":      "ask",
        "loom_reply":    "reply",
    }
    action = action_map.get(name)
    if not action:
        return f"Unknown tool: {name}", True

    # Build the ai_report command payload
    payload = {"cmd": "ai_report", "cell_id": cell_id, "action": action}

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

    result = await handle_command(payload)
    if result and result.get("type") == "error":
        return result.get("message", "Unknown error"), True

    return json.dumps(result) if result else '{"type":"ok"}', False


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _jsonrpc_ok(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# aiohttp handler
# ---------------------------------------------------------------------------

def create_mcp_handler(handle_command, state):
    """Return an aiohttp POST handler for the /mcp endpoint."""

    async def handle_mcp(request):
        # Read cell_id from header
        cell_id = request.headers.get("X-Loom-Cell-Id", "")

        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                _jsonrpc_error(None, -32700, "Parse error"), status=200)

        # JSON-RPC notifications (no "id") — just acknowledge
        if "id" not in body:
            method = body.get("method", "")
            log.info("MCP notification: %s", method)
            return web.Response(status=202)

        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        log.info("MCP %s id=%s cell=%s", method, req_id, cell_id[:8] if cell_id else "?")

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
            return web.json_response(_jsonrpc_ok(req_id, result))

        if method == "ping":
            return web.json_response(_jsonrpc_ok(req_id, {}))

        if method == "tools/list":
            return web.json_response(
                _jsonrpc_ok(req_id, {"tools": ALL_TOOLS}))

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name not in _ALL_TOOL_MAP:
                return web.json_response(
                    _jsonrpc_error(req_id, -32602,
                                   f"Unknown tool: {tool_name}"))

            # Weaver tools don't require X-Loom-Cell-Id but use it
            # to resolve the caller's group in multi-group setups
            if tool_name.startswith("weaver_"):
                text, is_error = await _dispatch_weaver_tool(
                    tool_name, arguments, handle_command, state,
                    cell_id=cell_id)
            else:
                if not cell_id:
                    return web.json_response(
                        _jsonrpc_ok(req_id, {
                            "content": [{
                                "type": "text",
                                "text":
                                    "X-Loom-Cell-Id header is required"
                                    " — this tool only works inside a"
                                    " Loom-managed agent session",
                            }],
                            "isError": True,
                        }))
                text, is_error = await _dispatch_tool(
                    tool_name, arguments, cell_id,
                    handle_command, state)

            return web.json_response(
                _jsonrpc_ok(req_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                }))

        return web.json_response(
            _jsonrpc_error(req_id, -32601, f"Method not found: {method}"))

    return handle_mcp
