"""Weaver MCP tools — board-wide project management for orchestrator agents.

Weaver tools operate across the entire task board, not scoped to a single
agent's task.  They don't require ``X-Loom-Cell-Id`` (though they accept
it for audit logging).

Tools are served from the same ``/mcp`` endpoint as agent tools — the
``weaver_`` prefix provides namespace separation.
"""

import json
import logging
from dataclasses import asdict

log = logging.getLogger("loom")

# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolve_task(state, identifier: str) -> str | None:
    """Resolve a task by ID, slug, or ID prefix.  Returns task ID or None."""
    if not identifier:
        return None
    # Exact ID
    if identifier in state.board_tasks:
        return identifier
    # Slug match
    for t in state.board_tasks.values():
        if t.slug == identifier:
            return t.id
    # ID prefix
    for t in state.board_tasks.values():
        if t.id.startswith(identifier):
            return t.id
    return None


def _resolve_agent(state, identifier: str) -> str | None:
    """Resolve an agent by ID, slug, or name (case-insensitive).

    Only matches top-level agents (cell_type == 'agent'), not terminals.
    Returns agent ID or None.
    """
    if not identifier:
        return None
    # Exact ID
    if identifier in state.agents:
        c = state.agents[identifier]
        if c.cell_type == "agent":
            return c.id
    ident_lower = identifier.lower()
    # Slug match
    for c in state.agents.values():
        if c.cell_type != "agent":
            continue
        if c.slug == ident_lower:
            return c.id
    # Name match (case-insensitive)
    for c in state.agents.values():
        if c.cell_type != "agent":
            continue
        if c.name.lower() == ident_lower:
            return c.id
    # ID prefix
    for c in state.agents.values():
        if c.cell_type != "agent":
            continue
        if c.id.startswith(identifier):
            return c.id
    return None


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

WEAVER_TOOLS = [
    # -- Read tools ---------------------------------------------------------
    {
        "name": "weaver_board_list",
        "description": (
            "List all tasks on the board grouped by lane. "
            "Supports optional filters by lane, label, group, or "
            "text search. Returns a summary of each task including "
            "title, slug, lane, labels, action, and assigned agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lane": {
                    "type": "string",
                    "description": "Filter to a specific lane.",
                },
                "label": {
                    "type": "string",
                    "description": "Filter to tasks with this label.",
                },
                "group": {
                    "type": "string",
                    "description": "Filter to tasks in this group.",
                },
                "search": {
                    "type": "string",
                    "description": (
                        "Text search across task title and description."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_task_show",
        "description": (
            "Show full details for a task by slug or ID. "
            "Returns title, description, labels, action, action variables, "
            "pipeline info, assigned agent, and activity messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task slug or ID.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_task_chain",
        "description": (
            "Show the full derivation chain for a pipeline task. "
            "Returns all tasks in the chain from root to leaves "
            "with their status, lane, depth, and assigned agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task slug or ID (any task in the chain).",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_lanes_list",
        "description": "List all available lanes on the board in order.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weaver_actions_list",
        "description": (
            "List available actions (project and user scope) with "
            "name, description, variables, and scope. Use this to "
            "see what actions can be attached to tasks for dispatch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped actions."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_action_show",
        "description": (
            "Show full details of an action including its YAML contents, "
            "prompt template, transitions, and discovered variables."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Action name (e.g. 'feature/implement')."
                    ),
                },
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped actions."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "weaver_agents_list",
        "description": (
            "List all active agents with their name, slug, status, "
            "group, current task, and activity detail."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weaver_pipelines_list",
        "description": (
            "Discover pipelines from action transitions. Returns "
            "connected components in the action transition graph "
            "with nodes, edges, and entry points."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped actions."
                    ),
                },
            },
        },
    },
    # -- Write tools --------------------------------------------------------
    {
        "name": "weaver_task_create",
        "description": (
            "Create a new task on the board. Specify a title and "
            "optionally attach an action, group, lane, and labels."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short task title.",
                },
                "description": {
                    "type": "string",
                    "description": "Longer description or context.",
                },
                "group": {
                    "type": "string",
                    "description": "Target group for the task.",
                },
                "lane": {
                    "type": "string",
                    "description": (
                        "Lane to place the task in (default: Backlog)."
                    ),
                },
                "action": {
                    "type": "string",
                    "description": (
                        "Action name to attach (e.g. 'feature/implement')."
                    ),
                },
                "action_vars": {
                    "type": "object",
                    "description": "Action variable values as key-value pairs.",
                    "additionalProperties": {"type": "string"},
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels to attach to the task.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "weaver_task_edit",
        "description": (
            "Edit fields on an existing task. Only the fields you "
            "provide will be updated — omitted fields are unchanged."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task slug or ID to edit.",
                },
                "title": {
                    "type": "string",
                    "description": "New task title.",
                },
                "description": {
                    "type": "string",
                    "description": "New description.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New label list (replaces existing).",
                },
                "action": {
                    "type": "string",
                    "description": "New action name.",
                },
                "action_vars": {
                    "type": "object",
                    "description": "New action variable values.",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_task_move",
        "description": "Move a task to a different lane on the board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task slug or ID.",
                },
                "lane": {
                    "type": "string",
                    "description": "Target lane name.",
                },
            },
            "required": ["task", "lane"],
        },
    },
    {
        "name": "weaver_task_dispatch",
        "description": (
            "Dispatch a task to an agent. Creates a new agent by "
            "default, or dispatches to an existing agent if specified. "
            "The task moves to In Progress and the agent receives "
            "the rendered prompt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task slug or ID to dispatch.",
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "Existing agent slug or ID to dispatch to. "
                        "If omitted, a new agent is created."
                    ),
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_task_resolve",
        "description": (
            "Resolve an ask task by providing an answer. The answer "
            "is sent to the parent task's agent and the ask task "
            "moves to Done."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Slug or ID of the ask task to resolve."
                    ),
                },
                "answer": {
                    "type": "string",
                    "description": "The answer to send to the agent.",
                },
            },
            "required": ["task", "answer"],
        },
    },
]

_WEAVER_TOOL_MAP = {t["name"]: t for t in WEAVER_TOOLS}


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

async def _dispatch_weaver_tool(name, args, handle_command, state):
    """Execute a weaver tool call and return (content_text, is_error)."""

    # -- Read tools ---------------------------------------------------------

    if name == "weaver_board_list":
        lane_filter = args.get("lane", "")
        label_filter = args.get("label", "")
        group_filter = args.get("group", "")
        search = args.get("search", "").lower()

        lanes = {}
        for t in state.board_tasks.values():
            if lane_filter and t.lane != lane_filter:
                continue
            if label_filter and label_filter not in (t.labels or []):
                continue
            if group_filter and t.group != group_filter:
                continue
            if search and search not in t.task.lower() \
                    and search not in (t.description or "").lower():
                continue
            lane_tasks = lanes.setdefault(t.lane, [])
            agent_name = ""
            if t.agent_id:
                agent = state.agents.get(t.agent_id)
                if agent:
                    agent_name = agent.slug or agent.name
            lane_tasks.append({
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "group": t.group,
                "labels": t.labels or [],
                "action": t.action_name,
                "agent": agent_name,
                "status": t.status,
                "parent_task_id": t.parent_task_id,
            })

        # Order lanes by board_lanes order
        ordered = {}
        for lane_name in state.board_lanes:
            if lane_name in lanes:
                ordered[lane_name] = lanes[lane_name]
        # Include any lanes not in board_lanes (shouldn't happen, but safe)
        for lane_name, tasks in lanes.items():
            if lane_name not in ordered:
                ordered[lane_name] = tasks

        return json.dumps({"lanes": ordered}, indent=2), False

    if name == "weaver_task_show":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        d = asdict(task)
        # Enrich with agent info
        if task.agent_id:
            agent = state.agents.get(task.agent_id)
            if agent:
                d["agent_name"] = agent.slug or agent.name
                d["agent_status"] = agent.status
        return json.dumps(d, indent=2), False

    if name == "weaver_task_chain":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        result = await handle_command({"cmd": "task_chain", "task_id": tid})
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result, indent=2), False

    if name == "weaver_lanes_list":
        return json.dumps({"lanes": list(state.board_lanes)}, indent=2), False

    if name == "weaver_actions_list":
        result = await handle_command({
            "cmd": "list_actions",
            "group": args.get("group", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result, indent=2), False

    if name == "weaver_action_show":
        result = await handle_command({
            "cmd": "get_action",
            "name": args.get("name", ""),
            "group": args.get("group", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result, indent=2), False

    if name == "weaver_agents_list":
        agents = []
        for c in state.agents.values():
            if c.cell_type != "agent":
                continue
            task_title = ""
            if c.current_task_id:
                t = state.board_tasks.get(c.current_task_id)
                if t:
                    task_title = t.task
            agents.append({
                "id": c.id,
                "name": c.name,
                "slug": c.slug,
                "status": c.status,
                "group": c.group,
                "current_task_id": c.current_task_id,
                "current_task": task_title,
                "activity": c.activity,
                "activity_detail": c.activity_detail,
            })
        return json.dumps({"agents": agents}, indent=2), False

    if name == "weaver_pipelines_list":
        result = await handle_command({
            "cmd": "discover_pipelines",
            "group": args.get("group", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result, indent=2), False

    # -- Write tools --------------------------------------------------------

    if name == "weaver_task_create":
        payload = {
            "cmd": "board_add_task",
            "task": args.get("title", ""),
            "description": args.get("description", ""),
            "group": args.get("group", ""),
            "lane": args.get("lane", ""),
            "action_name": args.get("action", ""),
            "action_vars": args.get("action_vars", {}),
            "labels": args.get("labels", []),
        }
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "weaver_task_edit":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        payload = {"cmd": "board_update_task", "id": tid}
        if "title" in args:
            payload["task"] = args["title"]
        if "description" in args:
            payload["description"] = args["description"]
        if "labels" in args:
            payload["labels"] = args["labels"]
        if "action" in args:
            payload["action_name"] = args["action"]
        if "action_vars" in args:
            payload["action_vars"] = args["action_vars"]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "weaver_task_move":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        result = await handle_command({
            "cmd": "board_move_task",
            "id": tid,
            "lane": args.get("lane", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "weaver_task_dispatch":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        payload = {"cmd": "dispatch_task", "id": tid}
        agent_ident = args.get("agent", "")
        if agent_ident:
            agent_id = _resolve_agent(state, agent_ident)
            if not agent_id:
                return f"Agent not found: {agent_ident}", True
            payload["agent_id"] = agent_id
        else:
            payload["create_agent"] = True
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "weaver_task_resolve":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        result = await handle_command({
            "cmd": "resolve_ask",
            "id": tid,
            "answer": args.get("answer", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    return f"Unknown weaver tool: {name}", True
