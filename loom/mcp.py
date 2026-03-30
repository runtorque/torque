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
            "Pause for human input. Sets the parent task status to "
            "'Awaiting Input' and creates a task in Backlog with the "
            "'human' label. A human will resolve it with an answer "
            "that is sent back to this agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The question or decision needed from a human."
                    ),
                },
            },
            "required": ["question"],
        },
    },
]

_TOOL_MAP = {t["name"]: t for t in TOOLS}


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

    # Map tool name → ai_report action + argument extraction
    action_map = {
        "loom_done":     "done",
        "loom_blocked":  "blocked",
        "loom_error":    "error",
        "loom_progress": "progress",
        "loom_ready":    "ready",
        "loom_name":     "name",
        "loom_derive":   "derive",
        "loom_ask":      "ask",
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
                _jsonrpc_ok(req_id, {"tools": TOOLS}))

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name not in _TOOL_MAP:
                return web.json_response(
                    _jsonrpc_error(req_id, -32602,
                                   f"Unknown tool: {tool_name}"))

            if not cell_id:
                return web.json_response(
                    _jsonrpc_ok(req_id, {
                        "content": [{
                            "type": "text",
                            "text": "X-Loom-Cell-Id header is required — "
                                    "this tool only works inside a "
                                    "Loom-managed agent session",
                        }],
                        "isError": True,
                    }))

            text, is_error = await _dispatch_tool(
                tool_name, arguments, cell_id, handle_command, state)
            return web.json_response(
                _jsonrpc_ok(req_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                }))

        return web.json_response(
            _jsonrpc_error(req_id, -32601, f"Method not found: {method}"))

    return handle_mcp
