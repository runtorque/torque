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


def _is_busy_agent(state, agent_id: str) -> bool:
    """Return True when the agent has a live current task."""
    cell = state.agents.get(agent_id)
    if not cell or cell.cell_type != "agent":
        return False
    if cell.current_task_id:
        task = state.board_tasks.get(cell.current_task_id)
        if task and task.lane != "Done":
            return True
    return False


def _active_worker_ids(state, group: str) -> set[str]:
    """Count active non-weaver agents for a group."""
    active = set()
    weaver_id = state.get_group_settings(group).weaver_agent_id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.group != group:
            continue
        if cell.id == weaver_id:
            continue
        if _is_busy_agent(state, cell.id):
            active.add(cell.id)
    return active


def _blocked_dependency_titles(state, task) -> list[str]:
    return [
        state.board_tasks[d].task[:40]
        for d in task.depends_on
        if d in state.board_tasks
        and state.board_tasks[d].lane != "Done"
    ]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

WEAVER_TOOLS = [
    # -- Read tools ---------------------------------------------------------
    {
        "name": "weaver_board_summary",
        "description": (
            "Return a compact board overview for the weaver's group. "
            "Includes lane counts, active agent status, pending asks, "
            "and key label counts without embedding full task lists."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
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
            "pipeline info, assigned agent, and activity messages. "
            "For pipeline tasks, automatically includes the chain summary."
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
        "name": "weaver_agents_list",
        "description": (
            "List all active agents with their name, slug, status, "
            "group, current task, and activity detail."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weaver_agent_show",
        "description": (
            "Show detailed information about a specific agent. "
            "Returns agent metadata, worktree state (path, branch, "
            "diff stats, checkpoints), task history with messages, "
            "session info, and child terminals. Use for post-completion "
            "review before merging."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug, name, or ID.",
                },
            },
            "required": ["agent"],
        },
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
                    "description":
                        "Action variable values as key-value pairs.",
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
                "name": {
                    "type": "string",
                    "description": (
                        "Name for the new agent (e.g. 'worker'). "
                        "Only used when creating a new agent."
                    ),
                },
                "agent_type": {
                    "type": "string",
                    "description": (
                        "Agent backend for a new agent "
                        "(e.g. 'claude-code', 'codex')."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Boot command override for a new agent. "
                        "Only used when creating a new agent."
                    ),
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_batch_dispatch",
        "description": (
            "Dispatch a planned batch of tasks with an explicit "
            "concurrency cap. Tasks are processed in request order. "
            "Tasks that share an agent_group are routed to the same "
            "agent so later tasks can queue behind earlier ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": (
                        "Ordered task entries. Each item must include "
                        "a task slug or ID and may include an "
                        "agent_group string to keep related tasks on "
                        "the same agent."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "Task slug or ID.",
                            },
                            "agent_group": {
                                "type": "string",
                                "description": (
                                    "Optional grouping key. Entries "
                                    "with the same value share a "
                                    "single agent within this batch."
                                ),
                            },
                        },
                        "required": ["task"],
                    },
                },
                "max_concurrent": {
                    "type": "integer",
                    "description": (
                        "Maximum number of active worker agents "
                        "allowed in the group after this call."
                    ),
                },
            },
            "required": ["tasks", "max_concurrent"],
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
    # -- Event tools --------------------------------------------------------
    {
        "name": "weaver_events",
        "description": (
            "Poll for recent events. Use after context cleanup to "
            "catch up on what happened. Returns panel events filtered "
            "by group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since_id": {
                    "type": "integer",
                    "description": (
                        "Return events after this event ID (cursor). "
                        "Omit for latest events."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description":
                        "Max events to return (default: 50).",
                },
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter to specific event types. "
                        "Omit for all types."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_notifications",
        "description": (
            "Configure event push settings. Sets which optional events "
            "appear in digests and the push interval. Mandatory events "
            "(task_completed, agent_error, agent_reply, agent_blocked, "
            "ask_created) are always included."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "push_interval": {
                    "type": "integer",
                    "description": (
                        "Seconds between digest pushes "
                        "(min: 10, default: 60)."
                    ),
                },
                "max_interval": {
                    "type": "integer",
                    "description": (
                        "Max seconds between pushes including "
                        "idle digests (default: 300)."
                    ),
                },
                "enable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional event types to enable: "
                        "agent_started, task_dispatched, "
                        "task_derived, agent_progress."
                    ),
                },
                "disable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional event types to disable.",
                },
            },
        },
    },
    {
        "name": "weaver_resume",
        "description": (
            "Resume event delivery after a weaver_ask. Call this "
            "after the human has responded (via the panel or "
            "directly in your terminal) to unpause event pushes."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # -- Context tools ------------------------------------------------------
    {
        "name": "weaver_journal",
        "description": (
            "Append an entry to the weaver's persistent decision "
            "journal. Use this to record decisions, observations, and "
            "periodic checkpoints. The journal survives context "
            "cleanup — read it back with weaver_journal_read to "
            "resume orchestration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "decision", "observation",
                        "checkpoint", "plan",
                    ],
                    "description": (
                        "Entry type: decision (action taken + "
                        "rationale), observation (something noted), "
                        "checkpoint (board state summary for context "
                        "recovery), plan (intended next steps)."
                    ),
                },
                "entry": {
                    "type": "string",
                    "description": (
                        "Journal entry content. Be concise but "
                        "include rationale for decisions."
                    ),
                },
            },
            "required": ["type", "entry"],
        },
    },
    {
        "name": "weaver_journal_read",
        "description": (
            "Read recent journal entries. Use after context cleanup "
            "or startup to recover the weaver's decision history "
            "and resume orchestration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tail": {
                    "type": "integer",
                    "description": (
                        "Number of most recent entries to return "
                        "(default: 20)."
                    ),
                },
                "type": {
                    "type": "string",
                    "enum": [
                        "decision", "observation",
                        "checkpoint", "plan",
                    ],
                    "description":
                        "Filter to a specific entry type.",
                },
            },
        },
    },
    # -- Interaction tools --------------------------------------------------
    {
        "name": "weaver_agent_message",
        "description": (
            "Send a message to any agent's terminal. The agent can "
            "reply via loom_reply, which appears in the weaver's "
            "next event digest. Use for: redirecting agents, "
            "providing context, answering questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID.",
                },
                "message": {
                    "type": "string",
                    "description":
                        "Message to send to the agent.",
                },
            },
            "required": ["agent", "message"],
        },
    },
    {
        "name": "weaver_ask",
        "description": (
            "Ask the human a question. The question is displayed in "
            "the Weaver panel and event pushes are automatically "
            "paused until the human replies. Use this when you need "
            "human guidance — prioritization, design decisions, "
            "approval, or clarification. After the human responds "
            "(via the panel or directly in your terminal), call "
            "weaver_resume to unpause event delivery."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question for the human.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "weaver_agent_close",
        "description": (
            "Close an agent — ends its terminal session and removes "
            "it from the group. The agent's worktree (if any) is "
            "preserved on disk. Use after merging or when the agent "
            "is no longer needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID to close.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_agent_relaunch",
        "description": (
            "Relaunch a stopped agent — re-creates the terminal "
            "session. If the agent has a worktree, it is reused. "
            "If session_resume is enabled, the previous Claude Code "
            "session is resumed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID to relaunch.",
                },
            },
            "required": ["agent"],
        },
    },
    # -- Worktree tools -----------------------------------------------------
    {
        "name": "weaver_merge",
        "description": (
            "Merge an agent's worktree branch into the base branch "
            "(usually main). Uses server-side merge — no interactive "
            "resolution. If there are conflicts, the merge will fail "
            "and you should ask the human for permission to rebase "
            "and resolve conflicts before retrying."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID with a worktree.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Custom merge commit message. If omitted, "
                        "auto-generated from completed tasks."
                    ),
                },
                "close_agent_on_merge": {
                    "type": "boolean",
                    "description": (
                        "Close the agent after a successful merge."
                    ),
                },
                "remove_worktree_on_merge": {
                    "type": "boolean",
                    "description": (
                        "Remove the worktree after a successful merge."
                    ),
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_create_pr",
        "description": (
            "Create a GitHub pull request for an agent's worktree "
            "branch. Pushes the branch to origin and creates a PR "
            "via the GitHub CLI (gh). Returns the PR URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID with a worktree.",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "PR title. If omitted, uses the agent's "
                        "linked task title or agent name."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "PR description body (markdown).",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_diff",
        "description": (
            "Get the diff of an agent's worktree branch against "
            "its base branch. Returns the full diff output, "
            "optionally limited to specific files. Useful for "
            "reviewing changes before merge or PR."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID with a worktree.",
                },
                "stat_only": {
                    "type": "boolean",
                    "description": (
                        "If true, return only the diffstat summary "
                        "(files changed, insertions, deletions) "
                        "instead of the full diff. Default: false."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Limit diff to specific file paths. "
                        "If omitted, shows all changes."
                    ),
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_worktree_remove",
        "description": (
            "Remove an agent's worktree from disk. Use after merging "
            "to clean up. The agent's directory reverts to the "
            "original repo root."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID with a worktree.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_worktree_checkpoint",
        "description": (
            "Create a checkpoint commit on an agent's worktree. "
            "Commits all current changes with an auto-generated "
            "message. Useful for saving progress before risky "
            "operations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug or ID with a worktree.",
                },
            },
            "required": ["agent"],
        },
    },
]

_WEAVER_TOOL_MAP = {t["name"]: t for t in WEAVER_TOOLS}


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

async def _dispatch_weaver_tool(name, args, handle_command, state,
                                cell_id=""):
    """Execute a weaver tool call and return (content_text, is_error)."""

    # Resolve the weaver's group — all tools are scoped to this group.
    # Prefer the caller's group (from X-Loom-Cell-Id) for multi-group setups.
    _weaver_group = ""
    _weaver_cell = None
    if cell_id:
        _weaver_cell = state.agents.get(cell_id)
        if _weaver_cell:
            _weaver_group = _weaver_cell.group
    if not _weaver_group:
        for gn, gs in state.group_settings.items():
            if gs.weaver_agent_id:
                _weaver_group = gn
                if not _weaver_cell:
                    _weaver_cell = state.agents.get(gs.weaver_agent_id)
                break
    if not _weaver_group:
        return "No weaver configured for any group", True

    # -- Read tools ---------------------------------------------------------

    if name == "weaver_board_summary":
        tasks = [
            t for t in state.board_tasks.values()
            if t.group == _weaver_group
        ]

        lane_counts = {lane_name: 0 for lane_name in state.board_lanes}
        extra_lanes = {}
        label_counts = {"ready": 0, "deferred": 0}
        health_counts = {
            "healthy": 0,
            "blocked": 0,
            "idle-risk": 0,
            "stalled": 0,
            "thrashing": 0,
        }
        unhealthy = []
        pending_asks = []

        for task in tasks:
            if task.lane in lane_counts:
                lane_counts[task.lane] += 1
            else:
                extra_lanes[task.lane] = extra_lanes.get(task.lane, 0) + 1

            labels = set(task.labels or [])
            for label_name in label_counts:
                if label_name in labels:
                    label_counts[label_name] += 1

            health_state = getattr(task, "health_state", "healthy") or "healthy"
            if health_state not in health_counts:
                health_counts[health_state] = 0
            health_counts[health_state] += 1
            if task.lane != "Done" and health_state != "healthy":
                unhealthy.append({
                    "id": task.id,
                    "title": task.task,
                    "health_state": health_state,
                    "health_since": getattr(task, "health_since", ""),
                })

            if "loom:human" in labels and task.lane != "Done":
                pending_asks.append({
                    "id": task.id,
                    "title": task.task,
                    "parent_task_id": task.parent_task_id,
                })

        ordered_lanes = dict(lane_counts)
        for lane_name in sorted(extra_lanes):
            ordered_lanes[lane_name] = extra_lanes[lane_name]

        gs = state.get_group_settings(_weaver_group)
        weaver_id = gs.weaver_agent_id or (
            _weaver_cell.id if _weaver_cell and _weaver_cell.group == _weaver_group
            else ""
        )
        agent_status_counts = {
            "idle": 0,
            "running": 0,
            "error": 0,
            "stopped": 0,
        }
        active_agents = []
        total_agents = 0
        needs_attention = 0

        agents = [
            c for c in state.agents.values()
            if c.cell_type == "agent"
            and c.group == _weaver_group
            and c.id != weaver_id
        ]
        agents.sort(key=lambda c: ((c.slug or c.name or c.id).lower(), c.id))

        for cell in agents:
            total_agents += 1
            if cell.needs_attention:
                needs_attention += 1
            status = cell.status or "stopped"
            agent_status_counts[status] = agent_status_counts.get(status, 0) + 1
            if status == "stopped":
                continue
            task_title = ""
            if cell.current_task_id:
                task = state.board_tasks.get(cell.current_task_id)
                if task:
                    task_title = task.task
            active_agents.append({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "type": cell.agent_type,
                "status": status,
                "current_task_id": cell.current_task_id,
                "current_task": task_title,
                "needs_attention": cell.needs_attention,
            })

        pending_asks.sort(key=lambda item: (item["title"].lower(), item["id"]))
        unhealthy.sort(
            key=lambda item: (item["health_state"], item["health_since"], item["title"]),
            reverse=True,
        )

        summary = {
            "group": _weaver_group,
            "tasks_total": len(tasks),
            "lanes": ordered_lanes,
            "labels": label_counts,
            "task_health": {
                "counts": health_counts,
                "unhealthy": unhealthy[:10],
                "truncated": len(unhealthy) > 10,
            },
            "asks": {
                "count": len(pending_asks),
                "items": pending_asks[:10],
                "truncated": len(pending_asks) > 10,
            },
            "agents": {
                "total": total_agents,
                "active_count": len(active_agents),
                "needs_attention": needs_attention,
                "by_status": agent_status_counts,
                "active": active_agents[:10],
                "truncated": len(active_agents) > 10,
            },
        }
        return json.dumps(summary), False

    if name == "weaver_board_list":
        lane_filter = args.get("lane", "")
        label_filter = args.get("label", "")
        search = args.get("search", "").lower()

        lanes = {}
        for t in state.board_tasks.values():
            # Always scope to weaver's group
            if t.group != _weaver_group:
                continue
            if lane_filter and t.lane != lane_filter:
                continue
            if label_filter and label_filter not in (t.labels or []):
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
                "health_state": getattr(t, "health_state", "healthy"),
                "health_since": getattr(t, "health_since", ""),
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

        return json.dumps({"lanes": ordered}), False

    if name == "weaver_task_show":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        d = {
            "id": task.id,
            "slug": task.slug,
            "title": task.task,
            "description": task.description,
            "group": task.group,
            "lane": task.lane,
            "status": task.status,
            "labels": task.labels or [],
            "action": task.action_name,
            "action_vars": task.action_vars or {},
            "agent_id": task.agent_id,
            "parent_task_id": task.parent_task_id,
            "pipeline_depth": task.pipeline_depth,
            "depends_on": task.depends_on or [],
            "created_at": task.created_at,
            "health_state": getattr(task, "health_state", "healthy"),
            "health_since": getattr(task, "health_since", ""),
            "health_details": getattr(task, "health_details", {}) or {},
        }
        # Include recent messages (last 10 only)
        if task.messages:
            d["messages"] = task.messages[-10:]
        # Enrich with agent info
        if task.agent_id:
            agent = state.agents.get(task.agent_id)
            if agent:
                d["agent_name"] = agent.slug or agent.name
                d["agent_status"] = agent.status
        # Auto-include pipeline chain for pipeline tasks
        if task.pipeline_root_id or task.parent_task_id:
            chain = state.board_get_chain(tid)
            d["pipeline_chain"] = []
            for ct in chain:
                agent_slug = ""
                if ct.agent_id:
                    a = state.agents.get(ct.agent_id)
                    if a:
                        agent_slug = a.slug or a.name
                d["pipeline_chain"].append({
                    "id": ct.id,
                    "title": ct.task,
                    "lane": ct.lane,
                    "status": ct.status,
                    "health_state": getattr(ct, "health_state", "healthy"),
                    "depth": ct.pipeline_depth,
                    "agent": agent_slug,
                })
        return json.dumps(d), False

    if name == "weaver_agents_list":
        agents = []
        for c in state.agents.values():
            if c.cell_type != "agent":
                continue
            if c.group != _weaver_group:
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
        return json.dumps({"agents": agents}), False

    if name == "weaver_agent_show":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents[agent_id]

        d = {
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "agent_type": cell.agent_type,
            "status": cell.status,
            "group": cell.group,
            "directory": cell.directory,
            "git_root": cell.git_root,
            "activity": cell.activity,
            "activity_detail": cell.activity_detail,
            "error_message": cell.error_message,
            "needs_attention": cell.needs_attention,
            "tasks_dispatched": cell.tasks_dispatched,
            "session": {
                "session_id": cell.agent_session_id,
                "tokens_in": cell.session_tokens_in,
                "tokens_out": cell.session_tokens_out,
            },
        }

        # Worktree state
        if cell.worktree_path:
            d["worktree"] = {
                "path": cell.worktree_path,
                "branch": cell.worktree_branch,
                "base_branch": cell.worktree_base_branch,
                "dirty": cell.worktree_dirty,
                "diff": cell.worktree_diff or {},
                "checkpoints": cell.worktree_checkpoints,
                "ahead": cell.worktree_ahead,
                "behind": cell.worktree_behind,
                "merged": cell.worktree_merged,
            }

        # Child terminals
        children_ids = state._children.get(agent_id, [])
        if children_ids:
            terminals = []
            for cid in children_ids:
                tc = state.agents.get(cid)
                if tc:
                    terminals.append({
                        "name": tc.name,
                        "slug": tc.slug,
                        "status": tc.status,
                        "current_process": tc.current_process,
                        "current_path": tc.current_path,
                    })
            d["terminals"] = terminals

        # Task history — all tasks ever assigned to this agent
        tasks = []
        for t in state.board_tasks.values():
            if t.agent_id != agent_id:
                continue
            task_info = {
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "lane": t.lane,
                "status": t.status,
                "labels": t.labels or [],
                "action": t.action_name,
            }
            if t.messages:
                task_info["messages"] = t.messages
            tasks.append(task_info)
        if tasks:
            d["tasks"] = tasks

        # Current task (may differ from tasks list if unlinked)
        if cell.current_task_id:
            d["current_task_id"] = cell.current_task_id

        return json.dumps(d), False

    if name == "weaver_actions_list":
        result = await handle_command({
            "cmd": "list_actions",
            "group": args.get("group", "") or _weaver_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if name == "weaver_action_show":
        result = await handle_command({
            "cmd": "get_action",
            "name": args.get("name", ""),
            "group": args.get("group", "") or _weaver_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    # -- Write tools --------------------------------------------------------

    if name == "weaver_task_create":
        payload = {
            "cmd": "board_add_task",
            "task": args.get("title", ""),
            "description": args.get("description", ""),
            "group": args.get("group", "") or _weaver_group,
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
            if _weaver_cell:
                if _weaver_cell.session_id:
                    payload["target_session_id"] = _weaver_cell.session_id
                if _weaver_cell.window_id:
                    payload["target_window_id"] = _weaver_cell.window_id
            agent_type = args.get("agent_type", "")
            if agent_type:
                payload["agent_type"] = agent_type
            command = args.get("command", "")
            if command:
                payload["command"] = command
        agent_name = args.get("name", "")
        if agent_name:
            payload["name"] = agent_name
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "weaver_batch_dispatch":
        raw_tasks = args.get("tasks", [])
        max_concurrent = args.get("max_concurrent", 0)
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return "tasks must be a non-empty array", True
        if not isinstance(max_concurrent, int) or max_concurrent < 1:
            return "max_concurrent must be an integer >= 1", True

        dispatch_lane = (
            state.get_group_settings(_weaver_group).dispatch_lane
            or "In Progress"
        )
        active_agents = _active_worker_ids(state, _weaver_group)
        active_before = len(active_agents)
        group_agents = {}
        seen_task_ids = set()
        results = []

        def _fail(entry_idx: int, task_ident: str, reason: str,
                  message: str, task_id: str = "") -> None:
            item = {
                "index": entry_idx,
                "task": task_ident,
                "status": "failed",
                "reason": reason,
                "message": message,
            }
            if task_id:
                item["task_id"] = task_id
            results.append(item)

        for idx, raw_entry in enumerate(raw_tasks):
            if not isinstance(raw_entry, dict):
                _fail(idx, "", "invalid_entry",
                      "Each tasks entry must be an object.")
                continue

            task_ident = raw_entry.get("task", "")
            if not isinstance(task_ident, str) or not task_ident.strip():
                _fail(idx, "", "invalid_entry",
                      "Each tasks entry must include a task string.")
                continue
            agent_group = raw_entry.get("agent_group", "")
            if agent_group and not isinstance(agent_group, str):
                _fail(idx, task_ident, "invalid_entry",
                      "agent_group must be a string when provided.")
                continue

            tid = _resolve_task(state, task_ident)
            if not tid:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if tid in seen_task_ids:
                _fail(idx, task_ident, "duplicate_task",
                      "Task appears more than once in this batch.", tid)
                continue
            seen_task_ids.add(tid)

            task = state.board_tasks.get(tid)
            if not task:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if task.group != _weaver_group:
                _fail(idx, task_ident, "wrong_group",
                      "Task is outside the weaver's group.", tid)
                continue
            if task.agent_id:
                _fail(idx, task_ident, "already_assigned",
                      "Task is already linked to an agent.", tid)
                continue
            if task.lane == "Done":
                _fail(idx, task_ident, "already_done",
                      "Task is already Done.", tid)
                continue
            if task.lane in ("In Progress", dispatch_lane):
                _fail(idx, task_ident, "already_in_progress",
                      "Task is already in the dispatch lane.", tid)
                continue
            if not state.board_deps_met(task):
                unmet = _blocked_dependency_titles(state, task)
                _fail(
                    idx,
                    task_ident,
                    "blocked_by_dependencies",
                    "Blocked by dependencies: " + ", ".join(unmet),
                    tid,
                )
                continue

            target_agent_id = ""
            mode = "new_agent"
            if agent_group:
                mode = "agent_group"
                target_agent_id = group_agents.get(agent_group, "")

            needs_capacity = not target_agent_id
            if target_agent_id:
                needs_capacity = not _is_busy_agent(state, target_agent_id)
            if needs_capacity and len(active_agents) >= max_concurrent:
                item = {
                    "index": idx,
                    "task": task_ident,
                    "task_id": tid,
                    "status": "deferred",
                    "reason": "max_concurrent_reached",
                    "message": (
                        "Dispatch would exceed max_concurrent for the group."
                    ),
                }
                if agent_group:
                    item["agent_group"] = agent_group
                results.append(item)
                continue

            payload = {"cmd": "dispatch_task", "id": tid}
            if target_agent_id:
                payload["agent_id"] = target_agent_id
            else:
                payload["create_agent"] = True

            result = await handle_command(payload)
            task_after = state.board_tasks.get(tid)
            resolved_agent_id = task_after.agent_id if task_after else ""
            if resolved_agent_id and (
                _is_busy_agent(state, resolved_agent_id)
                or resolved_agent_id in active_agents
            ):
                active_agents.add(resolved_agent_id)

            if result and result.get("type") == "error":
                _fail(
                    idx,
                    task_ident,
                    "dispatch_error",
                    result.get("message", "Unknown error"),
                    tid,
                )
                continue
            if result and result.get("type") == "dispatch_action_missing":
                _fail(
                    idx,
                    task_ident,
                    "dispatch_action_missing",
                    f"Action not found: {result.get('action_name', '')}",
                    tid,
                )
                continue

            if agent_group and not target_agent_id and resolved_agent_id:
                group_agents[agent_group] = resolved_agent_id

            item = {
                "index": idx,
                "task": task_ident,
                "task_id": tid,
                "status": "queued"
                if result and result.get("type") == "queued"
                else "dispatched",
                "mode": mode,
            }
            if agent_group:
                item["agent_group"] = agent_group
            if resolved_agent_id:
                item["agent_id"] = resolved_agent_id
                agent = state.agents.get(resolved_agent_id)
                if agent:
                    item["agent_name"] = agent.slug or agent.name
            results.append(item)

        payload = {
            "type": "ok",
            "max_concurrent": max_concurrent,
            "active_before": active_before,
            "active_after": len(active_agents),
            "results": results,
        }
        return json.dumps(payload), False

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

    # -- Event tools --------------------------------------------------------

    if name == "weaver_events":
        # Pull events from panel_log with optional filters
        since_id = args.get("since_id", 0)
        limit = args.get("limit", 50)
        type_filter = set(args.get("types", []))

        if state.panel_log:
            events = state.panel_log.get_page(limit, since_id)
        else:
            events = []

        # Scope to weaver's group
        events = [e for e in events
                  if e.get("group", "") == _weaver_group]
        if type_filter:
            events = [e for e in events if e.get("kind") in type_filter]

        cursor = events[-1]["id"] if events else since_id
        return json.dumps({"events": events, "cursor": cursor},
                          ), False

    if name == "weaver_notifications":
        ws = state.get_weaver_settings(_weaver_group)
        fields = {}
        if "push_interval" in args:
            fields["push_interval"] = max(10, args["push_interval"])
        if "max_interval" in args:
            fields["max_interval"] = max(30, args["max_interval"])
        if "enable" in args or "disable" in args:
            current = set(ws.enabled_events)
            for e in args.get("enable", []):
                current.add(e)
            for e in args.get("disable", []):
                current.discard(e)
            fields["enabled_events"] = sorted(current)

        result = await handle_command({
            "cmd": "weaver_update_settings",
            "group": _weaver_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_weaver_settings(_weaver_group))}), False

    if name == "weaver_resume":
        result = await handle_command({
            "cmd": "weaver_resume",
            "group": _weaver_group,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return "Event delivery resumed.", False

    # -- Context tools ------------------------------------------------------

    if name == "weaver_journal":
        result = await handle_command({
            "cmd": "weaver_journal_append",
            "group": _weaver_group,
            "entry_type": args.get("type", "observation"),
            "entry": args.get("entry", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if name == "weaver_journal_read":
        result = await handle_command({
            "cmd": "weaver_journal_read",
            "group": _weaver_group,
            "tail": args.get("tail", 20),
            "entry_type": args.get("type", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    # -- Interaction tools --------------------------------------------------

    if name == "weaver_agent_message":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True

        result = await handle_command({
            "cmd": "weaver_message",
            "agent_id": agent_id,
            "message": args.get("message", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if name == "weaver_ask":
        question = args.get("question", "").strip()
        if not question:
            return "Question is required", True

        result = await handle_command({
            "cmd": "weaver_ask",
            "group": _weaver_group,
            "question": question,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return (
            "Question posted to the Weaver panel. Event pushes have "
            "been paused. The human will see your question and reply "
            "via the panel or directly in this terminal.\n\n"
            "After the human responds, call weaver_resume to unpause "
            "event delivery."
        ), False

    if name == "weaver_agent_close":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        result = await handle_command({
            "cmd": "remove_agent",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Agent closed"}), False

    if name == "weaver_agent_relaunch":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents.get(agent_id)
        if not cell:
            return f"Agent not found: {agent_ident}", True
        if cell.status != "stopped":
            return f"Agent is not stopped (status: {cell.status})", True
        result = await handle_command({
            "cmd": "relaunch_agent",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": f"Agent {cell.slug} relaunched"}), False

    # -- Worktree tools -----------------------------------------------------

    if name == "weaver_merge":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True

        # First check for conflicts
        result = await handle_command({
            "cmd": "worktree_check_conflicts",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if result and not result.get("clean", True):
            conflicts = result.get("conflicts", [])
            conflict_list = "\n".join(f"  - {c}" for c in conflicts)
            return (
                f"Merge has conflicts:\n{conflict_list}\n\n"
                "Ask the human for permission to rebase and resolve "
                "conflicts before retrying the merge. Use weaver_ask "
                "to request permission."
            ), True

        # Proceed with merge
        payload = {"cmd": "worktree_merge", "id": agent_id}
        msg = args.get("message", "")
        if msg:
            payload["message"] = msg
        if args.get("close_agent_on_merge"):
            payload["close_agent_on_merge"] = True
        if args.get("remove_worktree_on_merge"):
            payload["remove_worktree_on_merge"] = True
        result = await handle_command(payload)
        if result and result.get("ok") is False:
            error = result.get("error", "Merge failed")
            if "conflict" in error.lower():
                return (
                    f"Merge failed: {error}\n\n"
                    "Ask the human for permission to rebase and "
                    "resolve conflicts. Use weaver_ask."
                ), True
            return error, True
        cleanup = result.get("cleanup", {}) if result else {}
        cleanup_errors = cleanup.get("errors", [])
        if cleanup_errors:
            return (
                f"Merged {cell.worktree_branch} into "
                f"{cell.worktree_base_branch}, but cleanup failed:\n"
                + "\n".join(f"  - {err}" for err in cleanup_errors)
            ), True
        return json.dumps({
            "type": "ok",
            "message": f"Merged {cell.worktree_branch} into "
                       f"{cell.worktree_base_branch}",
            "sha": result.get("sha", ""),
            "cleanup": cleanup,
        }), False

    if name == "weaver_create_pr":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True

        payload = {"cmd": "worktree_create_pr", "id": agent_id}
        title = args.get("title", "")
        if title:
            payload["title"] = title
        body = args.get("body", "")
        if body:
            payload["body"] = body
        result = await handle_command(payload)
        if result and result.get("error"):
            return result["error"], True
        return json.dumps({
            "type": "ok",
            "url": result.get("url", ""),
            "message": result.get("message", "PR created"),
        }), False

    if name == "weaver_diff":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        if not cell.worktree_base_branch:
            return "Agent has no base branch configured", True

        result = await handle_command({
            "cmd": "worktree_diff",
            "id": agent_id,
            "stat_only": args.get("stat_only", False),
            "paths": args.get("paths", []),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return result.get("diff", "No changes"), False

    if name == "weaver_worktree_remove":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        result = await handle_command({
            "cmd": "worktree_remove",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Worktree removed"}), False

    if name == "weaver_worktree_checkpoint":
        agent_ident = args.get("agent", "")
        agent_id = _resolve_agent(state, agent_ident)
        if not agent_id:
            return f"Agent not found: {agent_ident}", True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        result = await handle_command({
            "cmd": "worktree_checkpoint",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Checkpoint created"}), False

    return f"Unknown weaver tool: {name}", True
