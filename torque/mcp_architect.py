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
from .mcp_engineer_tools.tool_specs import (
    ENGINEER_TOOLS as _ENGINEER_TOOL_SPECS,
)
from .help_docs import help_tool_specs

_ENV_VAR = "TORQUE_ARCHITECT_ID"

ARCHITECT_DEFERRED_TOOL_NAMES = {
    "architect_get_architect_settings",
    "architect_engineer_dismiss",
    "architect_engineer_rehire",
    "architect_engineer_restore",
    "architect_mcp_calls",
}

_ARCHITECT_WORKTREE_SOURCE_NAMES = {
    "engineer_merge",
    "engineer_rebase",
    "engineer_create_pr",
    "engineer_diff",
}

_ARCHITECT_TASK_EXECUTION_SOURCE_NAMES = {
    "engineer_task_upload_artifact",
    "engineer_task_verify",
}

_ARCHITECT_WORKTREE_DESCRIPTIONS = {
    "architect_merge": (
        "Merge a hired Engineer's, or a same-group task-linked "
        "user-owned Worker's, reviewed worktree stream through Torque's "
        "configured PR or direct-merge workflow. Safety gates remain active; "
        "use worktree_rebase if Torque reports conflicts or a stale base."
    ),
    "architect_rebase": (
        "Rebase a hired Engineer's worktree stream onto its base branch, then "
        "return its updated merge readiness."
    ),
    "architect_create_pr": (
        "Push a hired Engineer's worktree branch and create a GitHub pull "
        "request through Torque."
    ),
    "architect_diff": (
        "Inspect the changed files or diff for a hired Engineer's worktree."
    ),
}


def _architect_worktree_tool_specs() -> list[dict]:
    """Adapt the audited worktree handlers to the Architect namespace.

    Architect worktree operations intentionally require an agent target.
    Driverless path/branch targeting is an Engineer-only escape hatch because
    it cannot express either the Architect-to-hired-Engineer relationship or
    the same-group task-stream relationship for a user-owned Worker, both of
    which are enforced by the frozen Agent Class authority gate.
    """

    tools = []
    for source in _ENGINEER_TOOL_SPECS:
        source_name = str(source.get("name", "") or "").strip()
        if source_name not in _ARCHITECT_WORKTREE_SOURCE_NAMES:
            continue
        copied = deepcopy(source)
        copied["name"] = source_name.replace("engineer_", "architect_", 1)
        copied["description"] = _ARCHITECT_WORKTREE_DESCRIPTIONS[
            copied["name"]
        ]
        schema = copied.get("inputSchema", {})
        properties = schema.get("properties", {})
        for key in ("worktree_path", "branch", "repo_root", "base_branch"):
            properties.pop(key, None)
        if "agent" in properties:
            properties["agent"]["description"] = (
                "Hired Engineer, or task-linked user-owned Worker, ID or name "
                "with a worktree."
            )
        authority = copied.get("authority", {})
        for requirement in authority.get("requirements", []):
            if requirement.get("capability") == "worktree.merge":
                # The public call gate derives the narrowly task-linked
                # Architect -> user-worker relationship before dispatch.
                requirement["handler_scoped"] = True
        boundary_override = properties.get("force_boundary_mismatch", {})
        if boundary_override:
            boundary_override["description"] = str(
                boundary_override.get("description", "") or ""
            ).replace("an Engineer verifies", "the Architect verifies")
        required = list(schema.get("required", []) or [])
        if "agent" not in required:
            required.append("agent")
        schema["required"] = required
        tools.append(copied)
    return tools


def _architect_task_execution_tool_specs() -> list[dict]:
    """Adapt task execution handlers that are authorized for Architects."""

    tools = []
    for source in _ENGINEER_TOOL_SPECS:
        source_name = str(source.get("name", "") or "").strip()
        if source_name not in _ARCHITECT_TASK_EXECUTION_SOURCE_NAMES:
            continue
        copied = deepcopy(source)
        copied["name"] = source_name.replace("engineer_", "architect_", 1)
        copied["description"] = (
            "Record verification evidence for a visible Architect-owned task. "
            "This updates task evidence only; it does not bypass review, "
            "merge, deployment, or worktree-boundary gates."
        )
        tools.append(copied)
    return tools


_ARCHITECT_TOOL_SPECS = [
    make_tool_search_spec("architect_tool_search", "architect"),
    {
        "name": "architect_attention_digest", "authority": {"requirements": [{"capability": "board.read","minimum_scope": "group","handler_scoped": True}]},
        "description": (
            "Return a compact bounded digest of actionable orchestration "
            "states needing this Architect's attention: blocking asks, "
            "hired-engineer human questions, ack-required peer messages, "
            "ready-to-merge streams, blocker/stale-base loops, unhealthy "
            "active work, pending hires, and a separate parked/deferred count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit_per_section": {
                    "type": "integer",
                    "description": "Maximum items per section (default 5, max 20).",
                },
            },
        },
    },
    {
        "name": "architect_group_health_brief", "authority": {"requirements": [{"capability": "board.read","minimum_scope": "group","handler_scoped": True},{"capability": "decision.read","minimum_scope": "self","handler_scoped": True},{"capability": "event.read","minimum_scope": "group","handler_scoped": True},{"capability": "planning.area.read","minimum_scope": "group","handler_scoped": True},{"capability": "planning.initiative.read","minimum_scope": "group","handler_scoped": True},{"capability": "task.read","minimum_scope": "group","handler_scoped": True},{"capability": "telemetry.read","minimum_scope": "group","handler_scoped": True}]},
        "description": (
            "Return a read-only onboarding and health brief for "
            "this Architect's group. The payload separates observed facts, "
            "inferred risks, suggested next steps, and responsible-actor "
            "recommendations; it never routes, dispatches, messages, creates "
            "tasks, accepts decisions, merges, deploys, or mutates state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["onboarding", "operating", "anomalies"],
                    "description": "Brief focus. Defaults to operating.",
                },
                "limit_per_section": {
                    "type": "integer",
                    "description": "Maximum items per section (default 5, max 20).",
                },
                "stale_after_hours": {
                    "type": "number",
                    "description": "Age threshold for stale task/review/handoff heuristics (default 24).",
                },
                "silent_after_hours": {
                    "type": "number",
                    "description": "Progress silence threshold for active agents/workstreams (default 2).",
                },
            },
        },
    },
    {
        "name": "architect_board_summary", "authority": {"requirements": [{"capability": "board.read","minimum_scope": "group","handler_scoped": True}]},
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
                        "engineer's specializations; an Engineer with no "
                        "specializations is a generalist and matches every slug."
                    ),
                },
            },
        },
    },
    {
        "name": "architect_wave_summary", "authority": {"requirements": [{"capability": "decision.read","minimum_scope": "self","handler_scoped": True},{"capability": "task.read","minimum_scope": "group","target_argument": "task_ids","target_kind": "task"}]},
        "description": (
            "Generate a compact bounded wave-summary drafting aid from either "
            "one same-group decision id or an explicit task-id list. The "
            "summary expands visible linked task chains, groups completed work "
            "by category/labels, reports recorded PR/squash/review/origin/test "
            "evidence with unknown markers for missing data, separates active "
            "gates from parked/deferred exclusions, and includes deploy/live "
            "smoke caveats."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "decision_id": {
                    "type": "string",
                    "description": (
                        "Same-group decision id whose linked tasks define "
                        "the wave. Provide exactly one of decision_id or "
                        "task_ids."
                    ),
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit task ids/slugs defining the wave. Provide "
                        "exactly one of decision_id or task_ids."
                    ),
                },
                "limit_per_section": {
                    "type": "integer",
                    "description": "Maximum items per bounded section (default 8, max 20).",
                },
            },
        },
    },
    {
        "name": "architect_completion_audit", "authority": {"requirements": [{"capability": "decision.read","minimum_scope": "self","handler_scoped": True},{"capability": "task.read","minimum_scope": "group","target_argument": "task_ids","target_kind": "task"}]},
        "description": (
            "Run a compact conservative completion audit before marking a "
            "decision/task wave complete. Given one same-group decision id "
            "or an explicit task-id list, it expands visible task chains, "
            "separates active gates from parked/deferred exclusions, reports "
            "open branch boundaries, blocking asks, pending engineer/peer "
            "obligations, pending hires, verification/deploy/live-smoke "
            "caveats, and recommends complete, not_complete, or "
            "complete_with_caveats."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "decision_id": {
                    "type": "string",
                    "description": (
                        "Same-group decision id whose linked tasks define "
                        "the audit scope. Provide exactly one of decision_id "
                        "or task_ids."
                    ),
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit task ids/slugs defining the audit scope. "
                        "Provide exactly one of decision_id or task_ids."
                    ),
                },
                "limit_per_section": {
                    "type": "integer",
                    "description": "Maximum items per bounded section (default 8, max 20).",
                },
            },
        },
    },
    {
        "name": "architect_boot_summary", "authority": {"requirements": [{"capability": "event.read","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Return this Architect's cached AI boot-recovery summary. "
            "Read-only: never performs a live provider call. If the status is "
            "empty, stale, refreshing, or error, fall back to "
            "architect_journal_read and architect_decision_list."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_semantic_recall", "authority": {"requirements": [{"capability": "semantic_recall.read","minimum_scope": "self","result_kind": "semantic_recall","result_paths": ["results"]}]},
        "description": (
            "Search the local AI semantic index for snippets visible to this "
            "Architect. Results are over-fetched then filtered through the "
            "same Architect decision, journal, task, hired-engineer journal, "
            "and engineer-peer-thread inspect visibility rules before any "
            "text is returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language recall query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum visible snippets to return (default 5, max 20).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "architect_events_recent", "authority": {"requirements": [{"capability": "event.read","minimum_scope": "self","result_kind": "event","result_paths": ["events"]},{"capability": "event.read","minimum_scope": "self","target_argument": "engineer_id","target_kind": "agent"}]},
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
        "name": "architect_mcp_calls", "authority": {"requirements": [{"capability": "telemetry.read","minimum_scope": "self","result_kind": "agent","result_paths": ["calls"]},{"capability": "telemetry.read","minimum_scope": "self","target_argument": "agent_id","target_kind": "agent"},{"capability": "telemetry.read","minimum_scope": "self","target_argument": "cell_id","target_kind": "agent"}]},
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
    *help_tool_specs("architect_"),
    {
        "name": "architect_deploy_state", "authority": {"requirements": [{"capability": "deploy.read"}]},
        "description": (
            "Return read-only daemon boot git state and pending mainline "
            "commit count since boot for deploy observability."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_get_architect_settings", "authority": {"requirements": [{"capability": "settings.admin"}]},
        "deferred": True,
        "description": "Read this group's persisted Architect settings.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "architect_digest_filter", "authority": {"requirements": [{"capability": "event.manage","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Read or update this architect's per-architect digest event "
            "filter. The mandatory floor (ask_created, "
            "engineer_awaiting_human_input, agent_error, agent_blocked, "
            "task_blocked) is always delivered and cannot be disabled."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "set": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Replace optional enabled event kinds. Omit to keep "
                        "the current set."
                    ),
                },
                "enable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional event kinds to add.",
                },
                "disable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional event kinds to remove.",
                },
            },
        },
    },
    {
        "name": "architect_task_show", "authority": {"requirements": [{"capability": "task.read","minimum_scope": "self","target_argument": "task","target_kind": "task"}]},
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
        "name": "architect_task_list", "authority": {"requirements": [{"capability": "task.read","minimum_scope": "self","result_kind": "task","result_paths": ["tasks"]}]},
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
        "name": "architect_task_chain", "authority": {"requirements": [{"capability": "task.read","minimum_scope": "group","target_argument": "task","target_kind": "task"}]},
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
        "name": "architect_task_pickup", "authority": {"requirements": [{"capability": "task.update","minimum_scope": "self","target_argument": "task","target_kind": "task","handler_scoped": True}]},
        "description": (
            "Claim a routed product-proposal product task in this architect's group "
            "without creating a covering duplicate. Requires durable inbound "
            "product-peer route evidence from the product proposal creator, "
            "sets assigned_architect_id on the original task, and records "
            "pickup audit evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "product-proposal product task ID or alias to claim.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional pickup reason/context recorded in audit evidence.",
                },
                "source": {
                    "type": "string",
                    "description": "Optional human-readable source route/request note.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "architect_task_create", "authority": {"requirements": [{"capability": "task.create","minimum_scope": "self","target_argument": "assigned_engineer_id","target_kind": "agent"}]},
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
                "required_review_gates": {
                    "type": "array",
                    "description": (
                        "Optional durable legacy review-cardinality declaration. "
                        "Each named gate adds one distinct independent Ship "
                        "review required before the root can close."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "role": {"type": "string"},
                        },
                        "required": ["id"],
                    },
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
                "dispatch": {
                    "type": "boolean",
                    "description": (
                        "When true, atomically sends a dispatch message to "
                        "the assigned engineer and marks the task live."
                    ),
                },
                "dispatch_message": {
                    "type": "string",
                    "description": (
                        "Optional message to send when dispatching. A non-empty "
                        "value implies `dispatch: true`; otherwise a concise "
                        "default mentioning the created task id is used."
                    ),
                },
            },
            "required": ["title", "group", "assigned_engineer_id"],
        },
    },
    {
        "name": "architect_task_update", "authority": {"requirements": [{"capability": "task.update","minimum_scope": "self","target_argument": "task","target_kind": "task"}]},
        "description": (
            "Update title, description, labels, suggested action, and/or action binding for a "
            "task in this architect's group within its frozen authority. "
            "The Product Manager's trusted full-board authority may update any "
            "same-group task; other Architect classes retain their ownership "
            "limits. Omitted fields are left unchanged; labels use "
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
                "suggested_action": {
                    "type": "string",
                    "description": (
                        "Replacement non-binding action suggestion. Must exist "
                        "in ActionManager.list_actions() for this group. Provide "
                        "an empty string to clear the suggestion."
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
                "required_review_gates": {
                    "type": "array",
                    "description": (
                        "Replacement durable review-cardinality declaration. "
                        "A dispatched task must first be stopped and re-laned "
                        "before this declaration can be amended."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "role": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "architect_task_block_reply", "authority": {"requirements": [{"capability": "task.update","minimum_scope": "self","target_argument": "task","target_kind": "task"}]},
        "description": (
            "Answer the latest blocked worker question for this task. Torque persists "
            "the correlated reply before waking the same worker/session and returns "
            "an explicit unrecoverable status if that context cannot be resumed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Blocked task ID or alias."},
                "answer": {"type": "string", "description": "Ruling for the worker to continue with."},
            },
            "required": ["task", "answer"],
        },
    },
    {
        "name": "architect_task_reassign", "authority": {"requirements": [{"capability": "task.reassign","minimum_scope": "self","target_argument": "task","target_kind": "task"},{"capability": "task.reassign","minimum_scope": "self","target_argument": "new_engineer_id","target_kind": "agent","handler_scoped": True}]},
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
        "name": "architect_task_dispatch", "authority": {"requirements": [{"capability": "task.dispatch","minimum_scope": "self","target_argument": "task","target_kind": "task"}]},
        "description": (
            "Dispatch a task created by this architect to a new Worker. "
            "Product Manager authority is restricted to its own product proposals; "
            "all normal dispatch safety gates remain active."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID or alias."},
                "name": {"type": "string", "description": "Optional worker name."},
                "command": {"type": "string", "description": "Optional worker command override."},
                "model": {"type": "string", "description": "Optional worker model override."},
                "reasoning_effort": {"type": "string", "description": "Optional reasoning-effort override."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "architect_task_move", "authority": {"requirements": [{"capability": "task.move","minimum_scope": "self","target_argument": "task","target_kind": "task"}]},
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
        "name": "architect_task_mark_covered", "authority": {"requirements": [{"capability": "task.mark_covered","minimum_scope": "self","target_argument": "task","target_kind": "task","handler_scoped": True},{"capability": "task.mark_covered","minimum_scope": "self","target_argument": "covering_task","target_kind": "task"},{"capability": "task.mark_covered","minimum_scope": "self","target_argument": "covering_task_id","target_kind": "task"}]},
        "description": (
            "Mark a user-created task or a task created by this architect as "
            "covered by another visible task or PR. Also allows a product-proposal "
            "product root created by another architect only when it has "
            "explicit route/coverage evidence (for example a caller-created "
            "covers:<task> covering task or inbound product-peer route). "
            "When accepting a product proposal into a covering task, use "
            "this to link and advance the proposal root with an audit trail rather "
            "than leaving a duplicate. Records durable completion evidence "
            "and an activity message; set move_to_done=true to close the "
            "covered card."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Covered task ID or alias."},
                "covering_task": {
                    "type": "string",
                    "description": "Optional visible task ID/alias whose work covers this card.",
                },
                "covering_task_id": {
                    "type": "string",
                    "description": "Alias for covering_task.",
                },
                "pr_url": {
                    "type": "string",
                    "description": "Optional PR URL that covers the card.",
                },
                "sha": {
                    "type": "string",
                    "description": "Optional commit or merge SHA evidence.",
                },
                "tests_run": {
                    "type": "string",
                    "description": "Optional tests/checks evidence.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Optional concise evidence summary.",
                },
                "notes": {"type": "string", "description": "Optional notes."},
                "move_to_done": {
                    "type": "boolean",
                    "description": "Move the covered card to Done after recording evidence.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "architect_proposal_root_backlog_hygiene", "authority": {"requirements": [{"capability": "task.mark_covered","minimum_scope": "self","target_argument": "task_ids","target_kind": "task"}]},
        "description": (
            "Inventory already-covered product proposal roots in this "
            "Architect's group and optionally finalize only eligible routed "
            "roots whose durable covered_by evidence points at this "
            "Architect's covering task. Dry-run by default; set apply=true "
            "to move eligible roots to Done while preserving completion "
            "evidence and appending an audit message. Ineligible roots remain "
            "in Backlog with reasons."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "apply": {
                    "type": "boolean",
                    "description": (
                        "Finalize eligible roots. Defaults to false for "
                        "read-only inventory."
                    ),
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional task IDs/aliases to restrict the inventory "
                        "or apply set."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "When apply=true, finalize at most this many eligible "
                        "roots. 0 means no limit."
                    ),
                },
            },
        },
    },
    {
        "name": "architect_task_coverage_reconcile", "authority": {"requirements": [{"capability": "task.mark_covered", "minimum_scope": "self", "target_argument": "task_ids", "target_kind": "task", "handler_scoped": True}]},
        "description": (
            "NOT YET AVAILABLE: recognized strict task-coverage reconciliation "
            "operation. It activates only after TORQUE:1228 is merged and "
            "this caller session is relaunched, because tool projection and "
            "authority are frozen for the session. This provisional route "
            "performs no board or state mutation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "minItems": 1, "maxItems": 25,
                             "items": {"type": "string"},
                             "description": "Explicit bounded task IDs or aliases."},
                "apply": {"type": "boolean", "description": "Reserved for TORQUE:1228; currently unavailable."},
                "dry_run_token": {"type": "string", "description": "Reserved for TORQUE:1228; currently unavailable."},
            },
            "required": ["task_ids"],
        },
    },
    {
        "name": "architect_ask", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "architect_message_user", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Send a non-blocking durable direct message to the user-facing "
            "conversation panel. Use this for status/context or to reply to "
            "a `## Message from the User` injection. Use architect_ask "
            "instead when the work should block on a user decision."
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
                        "Message ID from a `## Message from the User` block "
                        "when replying. Omit only for a proactive message. "
                        "Torque never guesses a reply target from historical "
                        "messages and derives the user lane from the caller."
                    ),
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
        "name": "architect_engineer_list", "authority": {"requirements": [{"capability": "engineer.roster.read","minimum_scope": "children","result_kind": "agent","result_paths": ["engineers"]}]},
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
        "name": "architect_engineer_hire", "authority": {"requirements": [{"capability": "engineer.hire","minimum_scope": "children","handler_scoped": True}]},
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
                "specializations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional complete ordered project specialization "
                        "list for the new engineer. The first entry is "
                        "primary; [] means generalist."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "architect_engineer_set_specializations", "authority": {"requirements": [{"capability": "engineer.manage","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
        "description": (
            "Full-replace the ordered project specialization list for an "
            "engineer hired by this architect. The first entry is primary; "
            "[] clears the list/generalist. No fresh user approval is needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Hired engineer id/slug/name.",
                },
                "specializations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Complete replacement ordered list of project "
                        "specialization slugs."
                    ),
                },
            },
            "required": ["engineer_id", "specializations"],
        },
    },
    {
        "name": "architect_engineer_dismiss", "authority": {"requirements": [{"capability": "engineer.manage","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
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
        "name": "architect_engineer_rehire", "authority": {"requirements": [{"capability": "engineer.manage","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
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
        "name": "architect_engineer_restore", "authority": {"requirements": [{"capability": "engineer.manage","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
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
        "name": "architect_pending_hire_status", "authority": {"requirements": [{"capability": "engineer.hire","minimum_scope": "children","handler_scoped": True}]},
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
        "name": "architect_pending_hire_list", "authority": {"requirements": [{"capability": "engineer.hire","minimum_scope": "children","handler_scoped": True}]},
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
        "name": "architect_behavior_overlay_read", "authority": {"requirements": [{"capability": "behavior_overlay.read","minimum_scope": "self","target_argument": "agent_id","target_kind": "agent"}]},
        "description": (
            "Read this architect's active Dynamic Behavior overlay, or a "
            "hired engineer's overlay when agent_id is provided."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role", "effective"],
                },
                "role_kind": {
                    "type": "string",
                    "enum": ["architect", "engineer", "worker"],
                },
            },
        },
    },
    {
        "name": "architect_behavior_overlay_versions", "authority": {"requirements": [{"capability": "behavior_overlay.read","minimum_scope": "self","target_argument": "agent_id","target_kind": "agent"}]},
        "description": "List overlay versions for self or a hired engineer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                },
                "role_kind": {
                    "type": "string",
                    "enum": ["architect", "engineer", "worker"],
                },
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "architect_behavior_overlay_diff", "authority": {"requirements": [{"capability": "behavior_overlay.read","minimum_scope": "self","target_argument": "agent_id","target_kind": "agent"}]},
        "description": "Diff overlay versions or a visible proposal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                },
                "role_kind": {
                    "type": "string",
                    "enum": ["architect", "engineer", "worker"],
                },
                "proposal_id": {"type": "string"},
                "from_version_id": {"type": "string"},
                "to_version_id": {"type": "string"},
            },
        },
    },
    {
        "name": "architect_behavior_overlay_proposal_list", "authority": {"requirements": [{"capability": "behavior_overlay.read","minimum_scope": "self","target_argument": "agent_id","target_kind": "agent"}]},
        "description": "List behavior overlay proposals visible to this architect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["proposed", "approved", "rejected", "applied"],
                },
                "agent_id": {"type": "string"},
                "scope_kind": {
                    "type": "string",
                    "enum": ["agent", "role"],
                },
                "role_kind": {
                    "type": "string",
                    "enum": ["architect", "engineer", "worker"],
                },
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "architect_behavior_overlay_propose", "authority": {"requirements": [{"capability": "behavior_overlay.propose","minimum_scope": "self","handler_scoped": True}]},
        "description": "Propose a change to this architect's own overlay; routes to user approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "rationale": {"type": "string"},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["text", "rationale"],
        },
    },
    {
        "name": "architect_behavior_overlay_propose_for_engineer", "authority": {"requirements": [{"capability": "behavior_overlay.admin","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
        "description": "Author a Dynamic Behavior overlay change for a hired engineer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {"type": "string"},
                "text": {"type": "string"},
                "rationale": {"type": "string"},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["engineer_id", "text", "rationale"],
        },
    },
    {
        "name": "architect_behavior_overlay_propose_for_role", "authority": {"requirements": [{"capability": "behavior_overlay.admin","minimum_scope": "group","handler_scoped": True}]},
        "description": (
            "Propose a group-scoped role Dynamic Behavior overlay. "
            "Role overlays are architect-authored and always user-approved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "role_kind": {
                    "type": "string",
                    "enum": ["architect", "engineer", "worker"],
                },
                "text": {"type": "string"},
                "rationale": {"type": "string"},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["role_kind", "text", "rationale"],
        },
    },
    {
        "name": "architect_behavior_overlay_approve", "authority": {"requirements": [{"capability": "behavior_overlay.admin","minimum_scope": "group","handler_scoped": True}]},
        "description": "Approve a hired engineer behavior overlay proposal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "expected_proposed_text_sha256": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "architect_behavior_overlay_reject", "authority": {"requirements": [{"capability": "behavior_overlay.admin","minimum_scope": "group","handler_scoped": True}]},
        "description": "Reject a visible behavior overlay proposal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "architect_behavior_overlay_rollback", "authority": {"requirements": [{"capability": "behavior_overlay.propose","minimum_scope": "self","target_argument": "agent_id","target_kind": "agent"}]},
        "description": "Request rollback for this architect or a hired engineer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "version_id": {"type": "string"},
                "rationale": {"type": "string"},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["version_id", "rationale"],
        },
    },
    {
        "name": "architect_behavior_overlay_rollback_role", "authority": {"requirements": [{"capability": "behavior_overlay.admin","minimum_scope": "group","handler_scoped": True}]},
        "description": "Propose rollback for a group-scoped role overlay; routes to user approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role_kind": {
                    "type": "string",
                    "enum": ["architect", "engineer", "worker"],
                },
                "version_id": {"type": "string"},
                "rationale": {"type": "string"},
                "expected_base_version_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["role_kind", "version_id", "rationale"],
        },
    },
    {
        "name": "architect_engineer_message", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
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
                "task": {
                    "type": "string",
                    "description": (
                        "Optional task id/slug to dispatch. Only this explicit "
                        "argument marks a task live; when omitted, the message "
                        "marks no task live even if its text mentions task IDs "
                        "or slugs."
                    ),
                },
            },
            "required": ["engineer_id", "message"],
        },
    },
    {
        "name": "architect_engineer_feedback_request", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","handler_scoped": True}]},
        "description": (
            "Fan out one structured retrospective feedback request to all "
            "Engineers hired by this Architect. Does not create tasks and "
            "does not target visible-only/non-hired Engineers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Optional custom prompt. Defaults to a compact "
                        "post-wave retrospective request."
                    ),
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional feedback categories. Defaults cover what "
                        "worked, blockers, next-wave changes, and risks/follow-ups."
                    ),
                },
                "request_id": {
                    "type": "string",
                    "description": (
                        "Optional stable id for tracking/idempotent operator "
                        "coordination. Auto-generated when omitted."
                    ),
                },
            },
        },
    },
    {
        "name": "architect_engineer_feedback_status", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","handler_scoped": True}]},
        "description": (
            "Return bounded response tracking for a retrospective feedback "
            "request: requested Engineers, replied Engineers, pending Engineers, "
            "and the relevant message/thread ids. Replies are detected as "
            "Engineer→Architect messages in each request thread."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": (
                        "Feedback request id. When omitted, returns the most "
                        "recent feedback request from this Architect."
                    ),
                },
            },
        },
    },
    {
        "name": "architect_peer_list", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","handler_scoped": True}]},
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
        "name": "architect_peer_message", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","target_argument": "architect_id","target_kind": "agent"}]},
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
        "name": "architect_peer_inbox", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","target_argument": "peer_architect_id","target_kind": "agent"}]},
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
                    "description": "Maximum threads to return (default 6, max 100).",
                },
                "detail": {
                    "type": "boolean",
                    "description": "Include complete message bodies and context snapshots; default is a bounded thread summary.",
                },
            },
        },
    },
    {
        "name": "architect_engineer_peer_threads", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
        "description": (
            "List Engineer↔Engineer notify-and-inspect threads where both "
            "participants are Engineers hired by this Architect. This is an "
            "on-demand inspect surface and is not gated by digest notification settings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Optional hired Engineer id/slug/name filter.",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Optional Engineer peer thread id filter.",
                },
                "active_since": {
                    "type": "number",
                    "description": "Optional lower-bound last-activity timestamp.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum threads to return (default 20, max 100).",
                },
            },
        },
    },
    {
        "name": "architect_engineer_peer_inspect", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","handler_scoped": True}]},
        "description": (
            "Inspect a full Engineer↔Engineer notify-and-inspect thread and its "
            "read-only referenced task/stream context. Requires both Engineer "
            "participants to be hired by this Architect."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "message_id": {"type": "string"},
                "include_live": {
                    "type": "boolean",
                    "description": "Include revalidated live task context when available.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum messages to return (default 100, max 1000).",
                },
            },
        },
    },
    {
        "name": "architect_engineer_journal_read", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
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
        "name": "architect_engineer_pending_question", "authority": {"requirements": [{"capability": "message.worker","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
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
        "name": "architect_engineer_answer", "authority": {"requirements": [{"capability": "message.worker","minimum_scope": "children","target_argument": "engineer_id","target_kind": "agent"}]},
        "description": (
            "Answer a hired engineer's pending blocking question (the "
            "owner-routed ask surfaced by architect_engineer_pending_question). "
            "Delivers the answer to the engineer and resumes its event "
            "delivery — the architect-side counterpart to the way an engineer "
            "resolves a worker's ask via engineer_task_resolve."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Hired engineer id/slug/name with a pending question.",
                },
                "answer": {
                    "type": "string",
                    "description": "Answer text delivered to the engineer.",
                },
            },
            "required": ["engineer_id", "answer"],
        },
    },
    {
        "name": "architect_engineer_reply", "authority": {"requirements": [{"capability": "message.engineer","minimum_scope": "children","target_argument": "message_id","target_kind": "message_peer"}]},
        "description": (
            "Reply to an existing Architect↔Engineer message thread."
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
        "name": "architect_peer_reply", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","target_argument": "message_id","target_kind": "message_peer"}]},
        "description": (
            "Reply to an existing same-group Architect↔Architect message "
            "thread."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Existing peer message id.",
                },
                "message": {
                    "type": "string",
                    "description": "Reply content.",
                },
                "ack_required": {
                    "type": "boolean",
                    "description": "Whether this follow-up requires a reply.",
                },
            },
            "required": ["message_id", "message"],
        },
    },
    {
        "name": "architect_area_list", "authority": {"requirements": [{"capability": "planning.area.read","minimum_scope": "self","result_kind": "area","result_paths": ["areas"]}]},
        "description": "List Planning Areas in this architect's group with compact optional link/note summaries.",
        "inputSchema": {"type": "object", "properties": {"include_archived": {"type": "boolean"}, "include_links": {"type": "boolean"}, "include_notes": {"type": "boolean"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "architect_area_show", "authority": {"requirements": [{"capability": "planning.area.read","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.read","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]},
        "description": "Show one same-group Planning Area with links and typed notes. Decision links include only decisions visible to this architect.",
        "inputSchema": {"type": "object", "properties": {"area": {"type": "string", "description": "Area id (for example TORQUE-A:1) or slug."}, "area_id": {"type": "string"}, "note_limit": {"type": "integer"}}, "required": ["area"]},
    },
    {
        "name": "architect_area_create", "authority": {"requirements": [{"capability": "planning.area.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create an architect-owned Planning Area in this architect's group.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "area_type": {"type": "string"}, "lifecycle": {"type": "string", "enum": ["planned", "experimental", "active_investment", "stable", "maintenance", "deprecated", "retired"]}, "summary": {"type": "string"}, "user_purpose": {"type": "string"}, "system_purpose": {"type": "string"}, "in_scope": {"type": "string"}, "out_of_scope": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "architect_area_update", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]},
        "description": "Update an Area owned or created by this architect. Owner transfer is not allowed via MCP.",
        "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "title": {"type": "string"}, "area_type": {"type": "string"}, "lifecycle": {"type": "string", "enum": ["planned", "experimental", "active_investment", "stable", "maintenance", "deprecated", "retired"]}, "summary": {"type": "string"}, "user_purpose": {"type": "string"}, "system_purpose": {"type": "string"}, "in_scope": {"type": "string"}, "out_of_scope": {"type": "string"}}, "required": ["area"]},
    },
    {"name": "architect_area_archive", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Archive an Area owned or created by this architect.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}}, "required": ["area"]}},
    {"name": "architect_area_link_task", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Link a visible same-group Board task to an Area without mutating the task.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "task": {"type": "string"}, "task_id": {"type": "string"}}, "required": ["area", "task"]}},
    {"name": "architect_area_unlink_task", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Remove an Area↔task link row only.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "task": {"type": "string"}, "task_id": {"type": "string"}}, "required": ["area", "task"]}},
    {"name": "architect_area_link_decision", "authority": {"requirements":[{"capability": "decision.link","minimum_scope": "self","target_argument": "decision","target_kind": "decision"},{"capability": "decision.link","minimum_scope": "self","target_argument": "decision_id","target_kind": "decision"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Link one caller-owned architect decision to an Area without mutating the decision.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "decision": {"type": "string"}, "decision_id": {"type": "string"}}, "required": ["area", "decision"]}},
    {"name": "architect_area_unlink_decision", "authority": {"requirements":[{"capability": "decision.link","minimum_scope": "self","target_argument": "decision","target_kind": "decision"},{"capability": "decision.link","minimum_scope": "self","target_argument": "decision_id","target_kind": "decision"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Remove an Area↔decision link row only.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "decision": {"type": "string"}, "decision_id": {"type": "string"}}, "required": ["area", "decision"]}},
    {"name": "architect_area_link_initiative", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Link a same-group Initiative to an Area without mutating the Initiative.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "initiative": {"type": "string"}, "initiative_id": {"type": "string"}}, "required": ["area", "initiative"]}},
    {"name": "architect_area_unlink_initiative", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Remove an Area↔Initiative link row only.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "initiative": {"type": "string"}, "initiative_id": {"type": "string"}}, "required": ["area", "initiative"]}},
    {"name": "architect_area_link_area", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Link one same-group Area to another with a pure label (related, depends_on, or supports). No graph semantics are inferred.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "target_area": {"type": "string"}, "target_area_id": {"type": "string"}, "relation": {"type": "string", "enum": ["related", "depends_on", "supports"]}}, "required": ["area", "target_area"]}},
    {"name": "architect_area_unlink_area", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Remove one Area↔Area labeled link row only.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "target_area": {"type": "string"}, "target_area_id": {"type": "string"}, "relation": {"type": "string", "enum": ["related", "depends_on", "supports"]}}, "required": ["area", "target_area"]}},
    {"name": "architect_area_note_create", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Create a flat typed Area note (caveat, tech_debt, open_question, follow_up, invariant).", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "note_type": {"type": "string", "enum": ["caveat", "tech_debt", "open_question", "follow_up", "invariant"]}, "title": {"type": "string"}, "body": {"type": "string"}, "target_type": {"type": "string", "enum": ["task", "decision", "initiative", "area"]}, "target_id": {"type": "string"}}, "required": ["area", "note_type", "title"]}},
    {"name": "architect_area_note_update", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Update a flat typed Area note owned by an Area this architect can write.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "note": {"type": "string"}, "note_id": {"type": "string"}, "note_type": {"type": "string", "enum": ["caveat", "tech_debt", "open_question", "follow_up", "invariant"]}, "title": {"type": "string"}, "body": {"type": "string"}, "target_type": {"type": "string", "enum": ["task", "decision", "initiative", "area"]}, "target_id": {"type": "string"}}, "required": ["area", "note"]}},
    {"name": "architect_area_note_archive", "authority": {"requirements":[{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.write","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]}, "description": "Archive a flat typed Area note.", "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "note": {"type": "string"}, "note_id": {"type": "string"}}, "required": ["area", "note"]}},
    {
        "name": "architect_initiative_list", "authority": {"requirements": [{"capability": "planning.initiative.read","minimum_scope": "self","result_kind": "initiative","result_paths": ["initiatives"]}]},
        "description": "List first-class product Initiatives in this architect's group. Read-only; Board remains execution source of truth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean"},
                "include_links": {"type": "boolean", "description": "Include bounded linked task/decision summaries."},
            },
        },
    },
    {
        "name": "architect_initiative_show", "authority": {"requirements": [{"capability": "planning.initiative.read","minimum_scope": "self","target_argument": "initiative","target_kind": "initiative"},{"capability": "planning.initiative.read","minimum_scope": "self","target_argument": "initiative_id","target_kind": "initiative"}]},
        "description": "Show one same-group Initiative with typed links and Board-derived linked task summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "initiative": {"type": "string", "description": "Initiative id (for example TORQUE-I:1) or slug."},
                "initiative_id": {"type": "string", "description": "Alias for initiative."},
            },
        },
    },
    {
        "name": "architect_initiative_create", "authority": {"requirements": [{"capability": "planning.initiative.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create an architect-owned Initiative in this architect's group.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "why": {"type": "string"},
                "in_scope": {"type": "string"},
                "out_of_scope": {"type": "string"},
                "done_definition": {"type": "string"},
                "planning_status": {"type": "string", "enum": ["triage", "now", "next", "later", "parked", "shipped"]},
                "priority": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "architect_initiative_update", "authority": {"requirements": [{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative","target_kind": "initiative"},{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative_id","target_kind": "initiative"}]},
        "description": "Update an Initiative owned or created by this architect. User-created initiatives not owned/created by this architect are not writable via MCP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "initiative": {"type": "string"},
                "initiative_id": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "why": {"type": "string"},
                "in_scope": {"type": "string"},
                "out_of_scope": {"type": "string"},
                "done_definition": {"type": "string"},
                "planning_status": {"type": "string", "enum": ["triage", "now", "next", "later", "parked", "shipped"]},
                "priority": {"type": "string"},
            },
            "required": ["initiative"],
        },
    },
    {
        "name": "architect_initiative_archive", "authority": {"requirements": [{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative","target_kind": "initiative"},{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative_id","target_kind": "initiative"}]},
        "description": "Archive an Initiative owned or created by this architect.",
        "inputSchema": {"type": "object", "properties": {"initiative": {"type": "string"}, "initiative_id": {"type": "string"}}, "required": ["initiative"]},
    },
    {
        "name": "architect_initiative_link_task", "authority": {"requirements": [{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative","target_kind": "initiative"},{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative_id","target_kind": "initiative"}]},
        "description": "Link a visible same-group Board task to an Initiative through a typed link row; does not mutate the task.",
        "inputSchema": {"type": "object", "properties": {"initiative": {"type": "string"}, "initiative_id": {"type": "string"}, "task": {"type": "string"}, "task_id": {"type": "string"}}, "required": ["initiative", "task"]},
    },
    {
        "name": "architect_initiative_unlink_task", "authority": {"requirements": [{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative","target_kind": "initiative"},{"capability": "planning.initiative.write","minimum_scope": "self","target_argument": "initiative_id","target_kind": "initiative"}]},
        "description": "Remove an Initiative↔task typed link row only; does not mutate the task.",
        "inputSchema": {"type": "object", "properties": {"initiative": {"type": "string"}, "initiative_id": {"type": "string"}, "task": {"type": "string"}, "task_id": {"type": "string"}}, "required": ["initiative", "task"]},
    },
    {
        "name": "architect_initiative_link_decision", "authority": {"requirements":[{"capability":"decision.link","minimum_scope":"self","target_argument":"decision","target_kind":"decision"},{"capability":"decision.link","minimum_scope":"self","target_argument":"decision_id","target_kind":"decision"},{"capability":"planning.initiative.write","minimum_scope":"self","target_argument":"initiative","target_kind":"initiative"},{"capability":"planning.initiative.write","minimum_scope":"self","target_argument":"initiative_id","target_kind":"initiative"}]},
        "description": "Link one caller-owned architect decision to an Initiative through a typed link row; does not mutate the decision.",
        "inputSchema": {"type": "object", "properties": {"initiative": {"type": "string"}, "initiative_id": {"type": "string"}, "decision": {"type": "string"}, "decision_id": {"type": "string"}}, "required": ["initiative", "decision"]},
    },
    {
        "name": "architect_initiative_unlink_decision", "authority": {"requirements":[{"capability":"decision.link","minimum_scope":"self","target_argument":"decision","target_kind":"decision"},{"capability":"decision.link","minimum_scope":"self","target_argument":"decision_id","target_kind":"decision"},{"capability":"planning.initiative.write","minimum_scope":"self","target_argument":"initiative","target_kind":"initiative"},{"capability":"planning.initiative.write","minimum_scope":"self","target_argument":"initiative_id","target_kind":"initiative"}]},
        "description": "Remove an Initiative↔decision typed link row only; does not mutate the decision.",
        "inputSchema": {"type": "object", "properties": {"initiative": {"type": "string"}, "initiative_id": {"type": "string"}, "decision": {"type": "string"}, "decision_id": {"type": "string"}}, "required": ["initiative", "decision"]},
    },
    {
        "name": "architect_decision_create", "authority": {"requirements": [{"capability": "decision.create","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "architect_decision_update", "authority": {"requirements":[{"capability":"decision.update","minimum_scope":"self","target_argument":"id","target_kind":"decision"}]},
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
        "name": "architect_decision_link", "authority": {"requirements": [{"capability": "decision.link","minimum_scope": "self","target_argument": "engineer_id","target_kind": "agent"},{"capability": "decision.link","minimum_scope": "self","target_argument": "task_id","target_kind": "task"}]},
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
        "name": "architect_decision_list", "authority": {"requirements":[{"capability":"decision.read","minimum_scope":"self","result_kind":"decision","result_paths":["decisions"]}]},
        "description": "List persisted decisions authored by any Architect in this group. Defaults to six bounded summaries and reports the total available.",
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
                "limit": {
                    "type": "integer",
                    "description": "Maximum decisions to return (default: 6).",
                },
                "detail": {
                    "type": "boolean",
                    "description": "Include full rationale and metadata for each returned decision.",
                },
            },
        },
    },
    {
        "name": "architect_journal", "authority": {"requirements": [{"capability": "journal.private","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "architect_journal_read", "authority": {"requirements": [{"capability": "journal.private","minimum_scope": "self","handler_scoped": True}]},
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
                    "description": "Maximum entries to return (default: 6).",
                },
                "detail": {
                    "type": "boolean",
                    "description": "Include complete journal entries; default previews are bounded.",
                },
            },
        },
    },
]


_ARCHITECT_PRODUCT_TOOL_SPECS = [
    {
        "name": "architect_proposal_board_summary", "authority": {"requirements": [{"capability": "board.read","minimum_scope": "group","handler_scoped": True}]},
        "description": "Board summary over tasks visible to the caller's frozen Product Manager authority; never dispatches.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "architect_task_proposal_list", "authority": {"requirements": [{"capability": "task.read","minimum_scope": "self","result_kind": "task","result_paths": ["tasks"]}]},
        "description": "List same-group task summaries; labels are an optional filter.",
        "inputSchema": {"type": "object", "properties": {"label_filter": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}, "lane_filter": {"type": "string"}, "include_archived": {"type": "boolean"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "architect_task_proposal_show", "authority": {"requirements": [{"capability": "task.read","minimum_scope": "self","target_argument": "task","target_kind": "task"}]},
        "description": "Show one same-group task; cross-group tasks remain unavailable.",
        "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
    },
    {
        "name": "architect_task_propose", "authority": {"requirements": [{"capability": "task.propose","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create an unassigned queued product task proposal with product-proposal/proposal-only labels and non-binding suggested_action/suggested_specialization hints only.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "group": {"type": "string"}, "description": {"type": "string"}, "lane": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}, "suggested_action": {"type": "string"}, "suggested_specialization": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "architect_proposal_area_list", "authority": {"requirements": [{"capability": "planning.area.read","minimum_scope": "self","result_kind": "area","result_paths": ["areas"]}]},
        "description": "Product-safe wrapper for same-group Area reads.",
        "inputSchema": {"type": "object", "properties": {"include_archived": {"type": "boolean"}, "include_links": {"type": "boolean"}, "include_notes": {"type": "boolean"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "architect_proposal_area_show", "authority": {"requirements": [{"capability": "planning.area.read","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.read","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]},
        "description": "Product-safe wrapper for one same-group Area read.",
        "inputSchema": {"type": "object", "properties": {"area": {"type": "string"}, "area_id": {"type": "string"}, "note_limit": {"type": "integer"}}},
    },
    {
        "name": "architect_proposal_initiative_list", "authority": {"requirements": [{"capability": "planning.initiative.read","minimum_scope": "self","result_kind": "initiative","result_paths": ["initiatives"]}]},
        "description": "Product-safe wrapper for same-group Initiative reads.",
        "inputSchema": {"type": "object", "properties": {"include_archived": {"type": "boolean"}, "include_links": {"type": "boolean"}}},
    },
    {
        "name": "architect_proposal_initiative_show", "authority": {"requirements": [{"capability": "planning.initiative.read","minimum_scope": "self","target_argument": "initiative","target_kind": "initiative"},{"capability": "planning.initiative.read","minimum_scope": "self","target_argument": "initiative_id","target_kind": "initiative"}]},
        "description": "Product-safe wrapper for one same-group Initiative read.",
        "inputSchema": {"type": "object", "properties": {"initiative": {"type": "string"}, "initiative_id": {"type": "string"}}},
    },
    {
        "name": "architect_decision_proposal_list", "authority": {"requirements":[{"capability":"decision.read","minimum_scope":"self","result_kind":"decision","result_paths":["decisions"]}]},
        "description": "List same-group decisions; proposal-only mutation tools remain caller-owned. Defaults to six bounded summaries and reports the total available.",
        "inputSchema": {"type": "object", "properties": {"include_archived": {"type": "boolean"}, "limit": {"type": "integer", "description": "Maximum decisions to return (default: 6)."}, "detail": {"type": "boolean", "description": "Include complete decision records for returned entries."}}},
    },
    {
        "name": "architect_decision_propose", "authority": {"requirements": [{"capability": "decision.propose","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a caller-owned proposed product decision. accepted/revised/rejected and engineer links are rejected.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "rationale": {"type": "string"}, "status": {"type": "string", "enum": ["proposed"]}, "supersedes": {"type": "string"}, "linked_task_ids": {"type": "array", "items": {"type": "string"}}, "linked_engineer_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "rationale"]},
    },
    {
        "name": "architect_decision_proposal_update", "authority": {"requirements":[{"capability":"decision.propose","minimum_scope":"self","target_argument":"id","target_kind":"decision"}]},
        "description": "Update a caller-owned proposed product decision only. Status must remain proposed and engineer links are rejected.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "rationale": {"type": "string"}, "status": {"type": "string", "enum": ["proposed"]}, "supersedes": {"type": "string"}, "linked_task_ids": {"type": "array", "items": {"type": "string"}}, "linked_engineer_ids": {"type": "array", "items": {"type": "string"}}, "archived": {"type": "boolean"}}, "required": ["id"]},
    },
    {
        "name": "architect_decision_proposal_link", "authority": {"requirements":[{"capability":"decision.propose","minimum_scope":"self","target_argument":"id","target_kind":"decision"},{"capability":"decision.link","minimum_scope":"self","target_argument":"engineer_id","target_kind":"agent"},{"capability":"decision.link","minimum_scope":"self","target_argument":"task_id","target_kind":"task"}]},
        "description": "Append a product-proposal task link to a caller-owned proposed product decision; engineer links are not supported.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "task_id": {"type": "string"}, "engineer_id": {"type": "string"}}, "required": ["id", "task_id"]},
    },
    {
        "name": "architect_idea_brief_list", "authority": {"requirements": [{"capability": "idea_brief.read","minimum_scope": "self","result_kind": "idea_brief","result_paths": ["idea_briefs"]}]},
        "description": "List same-group Idea Brief proposal artifacts visible to this product/ideation wrapper; includes caller_owned for safe update decisions.",
        "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}, "status": {"type": "string", "enum": ["draft", "proposed", "parked", "archived"]}, "include_archived": {"type": "boolean"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "architect_idea_brief_show", "authority": {"requirements": [{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "idea_brief","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "brief_id","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "id","target_kind": "idea_brief"}]},
        "description": "Show one same-group Idea Brief by id or slug without granting execution authority.",
        "inputSchema": {"type": "object", "properties": {"idea_brief": {"type": "string"}, "brief_id": {"type": "string"}, "id": {"type": "string"}, "group": {"type": "string"}, "include_archived": {"type": "boolean"}}},
    },
    {
        "name": "architect_idea_brief_create", "authority": {"requirements": [{"capability": "idea_brief.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a caller-owned Idea Brief draft linked to allowed Thinking artifacts; proposal-only and never dispatches work.",
        "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}, "title": {"type": "string"}, "problem_opportunity": {"type": "string"}, "why_it_matters": {"type": "string"}, "proposed_shape": {"type": "string"}, "smallest_useful_version": {"type": "string"}, "risks_tradeoffs": {"type": "string"}, "open_questions": {"type": "string"}, "thinking_links": {"type": "array", "items": {"type": "object"}}, "source_context": {"type": "object"}}, "required": ["problem_opportunity"]},
    },
    {
        "name": "architect_idea_brief_update", "authority": {"requirements": [{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "idea_brief","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "brief_id","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "id","target_kind": "idea_brief"},{"capability": "idea_brief.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Update a caller-owned Idea Brief only; status may remain draft/proposed/parked but archive uses the explicit archive tool.",
        "inputSchema": {"type": "object", "properties": {"idea_brief": {"type": "string"}, "brief_id": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}, "problem_opportunity": {"type": "string"}, "why_it_matters": {"type": "string"}, "proposed_shape": {"type": "string"}, "smallest_useful_version": {"type": "string"}, "risks_tradeoffs": {"type": "string"}, "open_questions": {"type": "string"}, "status": {"type": "string", "enum": ["draft", "parked"]}, "thinking_links": {"type": "array", "items": {"type": "object"}}, "source_context": {"type": "object"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_idea_brief_refine", "authority": {"requirements": [{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "idea_brief","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "brief_id","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "id","target_kind": "idea_brief"},{"capability": "idea_brief.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Refine a caller-owned Idea Brief with field patches and an optional refinement note; no execution side effects.",
        "inputSchema": {"type": "object", "properties": {"idea_brief": {"type": "string"}, "brief_id": {"type": "string"}, "id": {"type": "string"}, "refinement_note": {"type": "string"}, "title": {"type": "string"}, "problem_opportunity": {"type": "string"}, "why_it_matters": {"type": "string"}, "proposed_shape": {"type": "string"}, "smallest_useful_version": {"type": "string"}, "risks_tradeoffs": {"type": "string"}, "open_questions": {"type": "string"}, "thinking_links": {"type": "array", "items": {"type": "object"}}, "source_context": {"type": "object"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_idea_brief_park", "authority": {"requirements": [{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "idea_brief","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "brief_id","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "id","target_kind": "idea_brief"},{"capability": "idea_brief.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Park a caller-owned Idea Brief for later; keeps it durable and does not create tasks or decisions.",
        "inputSchema": {"type": "object", "properties": {"idea_brief": {"type": "string"}, "brief_id": {"type": "string"}, "id": {"type": "string"}, "reason": {"type": "string"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_idea_brief_archive", "authority": {"requirements": [{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "idea_brief","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "brief_id","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "id","target_kind": "idea_brief"},{"capability": "idea_brief.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Archive a caller-owned Idea Brief; this is a terminal visibility change, not an execution action.",
        "inputSchema": {"type": "object", "properties": {"idea_brief": {"type": "string"}, "brief_id": {"type": "string"}, "id": {"type": "string"}, "reason": {"type": "string"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_idea_brief_propose", "authority": {"requirements": [{"capability": "idea_brief.propose","minimum_scope": "self","handler_scoped": True},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "idea_brief","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "brief_id","target_kind": "idea_brief"},{"capability": "idea_brief.read","minimum_scope": "self","target_argument": "id","target_kind": "idea_brief"},{"capability": "idea_brief.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Explicitly mark a caller-owned Idea Brief proposed for product-safe review. It creates no task, assignment, dispatch, accepted decision, merge, or deploy action.",
        "inputSchema": {"type": "object", "properties": {"idea_brief": {"type": "string"}, "brief_id": {"type": "string"}, "id": {"type": "string"}, "note": {"type": "string"}, "proposal_note": {"type": "string"}, "review_target": {"type": "string"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_proposal_peer_list", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","handler_scoped": True}]},
        "description": "List selected same-group Architect/product-profile peers eligible for product-peer messages.",
        "inputSchema": {"type": "object", "properties": {"include_dismissed": {"type": "boolean"}}},
    },
    {
        "name": "architect_proposal_peer_message", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","target_argument": "architect_id","target_kind": "agent"}]},
        "description": "Send a product-peer marked message to an eligible same-group Architect/product-profile peer. Product-scope anchors are optional; use them when the message genuinely concerns a record.",
        "inputSchema": {"type": "object", "properties": {"architect_id": {"type": "string"}, "message": {"type": "string"}, "ack_required": {"type": "boolean"}, "context_task_ids": {"type": "array", "items": {"type": "string"}}, "context_decision_ids": {"type": "array", "items": {"type": "string"}}, "context_area_ids": {"type": "array", "items": {"type": "string"}}, "context_initiative_ids": {"type": "array", "items": {"type": "string"}}, "context_idea_brief_ids": {"type": "array", "items": {"type": "string"}}, "context_summary": {"type": "string"}}, "required": ["architect_id", "message"]},
    },
    {
        "name": "architect_proposal_peer_inbox", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","target_argument": "peer_architect_id","target_kind": "agent"}]},
        "description": "Read only product-peer marked Architect↔Architect threads involving this caller; raw peer and Architect↔Engineer rows are hidden.",
        "inputSchema": {"type": "object", "properties": {"peer_architect_id": {"type": "string"}, "thread_id": {"type": "string"}, "requires_reply": {"type": "boolean"}, "since": {"type": "number"}, "limit": {"type": "integer", "description": "Maximum threads to return (default: 6)."}, "detail": {"type": "boolean", "description": "Include complete thread messages and context."}}},
    },
    {
        "name": "architect_proposal_peer_reply", "authority": {"requirements": [{"capability": "message.architect_peer","minimum_scope": "group","target_argument": "message_id","target_kind": "message_peer"}]},
        "description": "Reply inside a product-peer marked thread only. Product-scope anchors are optional; use them when the reply genuinely concerns a record.",
        "inputSchema": {"type": "object", "properties": {"message_id": {"type": "string"}, "message": {"type": "string"}, "ack_required": {"type": "boolean"}, "context_task_ids": {"type": "array", "items": {"type": "string"}}, "context_decision_ids": {"type": "array", "items": {"type": "string"}}, "context_area_ids": {"type": "array", "items": {"type": "string"}}, "context_initiative_ids": {"type": "array", "items": {"type": "string"}}, "context_idea_brief_ids": {"type": "array", "items": {"type": "string"}}, "context_summary": {"type": "string"}}, "required": ["message_id", "message"]},
    },
    {
        "name": "architect_proposal_message_user", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
        "description": "Send a product-scoped direct user message after validating product-scoped context attachments. Pass reply_to_id from the injected user-message block when replying; omit it for a proactive message.",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "reply_to_id": {"type": "string", "description": "Message ID from the injected user-message block when replying; omit for a proactive message."}, "thread_id": {"type": "string"}, "context_task_ids": {"type": "array", "items": {"type": "string"}}, "context_decision_ids": {"type": "array", "items": {"type": "string"}}, "context_area_ids": {"type": "array", "items": {"type": "string"}}, "context_initiative_ids": {"type": "array", "items": {"type": "string"}}, "context_idea_brief_ids": {"type": "array", "items": {"type": "string"}}, "context_summary": {"type": "string"}}, "required": ["message"]},
    },
    {
        "name": "architect_proposal_ask_user", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a blocking product-scoped user ask after validating product-scoped context attachments.",
        "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "description": {"type": "string"}, "context_task_ids": {"type": "array", "items": {"type": "string"}}, "context_decision_ids": {"type": "array", "items": {"type": "string"}}, "context_area_ids": {"type": "array", "items": {"type": "string"}}, "context_initiative_ids": {"type": "array", "items": {"type": "string"}}, "context_idea_brief_ids": {"type": "array", "items": {"type": "string"}}, "context_summary": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "architect_proposal_journal", "authority": {"requirements": [{"capability": "journal.private","minimum_scope": "self","handler_scoped": True}]},
        "description": "Append a private product-planning recovery journal entry. decision journal entries are intentionally unsupported in Wave 4B.",
        "inputSchema": {"type": "object", "properties": {"type": {"type": "string", "enum": ["observation", "checkpoint", "plan"]}, "entry": {"type": "string"}}, "required": ["type", "entry"]},
    },
    {
        "name": "architect_proposal_journal_read", "authority": {"requirements": [{"capability": "journal.private","minimum_scope": "self","handler_scoped": True}]},
        "description": "Read recent private product-planning recovery journal entries, excluding decision-type journal rows.",
        "inputSchema": {"type": "object", "properties": {"since": {"type": "number"}, "limit": {"type": "integer", "description": "Maximum entries to return (default: 6)."}, "detail": {"type": "boolean", "description": "Include complete journal entry bodies."}}},
    },
]


_ARCHITECT_THINKING_TOOL_SPECS = [
    {
        "name": "architect_thinking_scratchpad_list", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","result_kind": "scratchpad_note","result_paths": ["notes"]}]},
        "description": "List same-group Scratchpad notes visible to this Architect; includes caller_owned for safe update decisions.",
        "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}, "include_archived": {"type": "boolean"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "architect_thinking_scratchpad_show", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "note","target_kind": "scratchpad_note"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "note_id","target_kind": "scratchpad_note"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "id","target_kind": "scratchpad_note"}]},
        "description": "Show one same-group Scratchpad note by id or slug.",
        "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}, "note_id": {"type": "string"}, "id": {"type": "string"}, "group": {"type": "string"}, "include_archived": {"type": "boolean"}}},
    },
    {
        "name": "architect_thinking_scratchpad_create", "authority": {"requirements": [{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a caller-owned Scratchpad note in the Architect's group; never writes outside the caller group.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "context": {"type": "object"}, "links": {"type": "array", "items": {"type": "object"}}, "group": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "architect_thinking_scratchpad_update", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "note","target_kind": "scratchpad_note"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "note_id","target_kind": "scratchpad_note"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "id","target_kind": "scratchpad_note"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Update a caller-owned Scratchpad note only; same-group notes owned by others are read-only.",
        "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}, "note_id": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "context": {"type": "object"}, "links": {"type": "array", "items": {"type": "object"}}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_thinking_mind_map_list", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","result_kind": "mind_map","result_paths": ["mind_maps"]}]},
        "description": "List same-group Mind Maps visible to this Architect; includes caller_owned for safe update decisions.",
        "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}, "include_archived": {"type": "boolean"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "architect_thinking_mind_map_show", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"}]},
        "description": "Show one same-group Mind Map with nodes and links.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "id": {"type": "string"}, "group": {"type": "string"}, "include_archived": {"type": "boolean"}}},
    },
    {
        "name": "architect_thinking_mind_map_create", "authority": {"requirements": [{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a caller-owned Mind Map in the Architect's group.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "metadata": {"type": "object"}, "group": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "architect_thinking_mind_map_update", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Update a caller-owned Mind Map only.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "metadata": {"type": "object"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_thinking_mind_map_node_create", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a node in a caller-owned Mind Map.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "label": {"type": "string"}, "title": {"type": "string"}, "notes": {"type": "string"}, "node_type": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "color": {"type": "string"}, "position": {"type": "object"}, "x": {"type": "number"}, "y": {"type": "number"}, "sort_order": {"type": "integer"}, "group": {"type": "string"}}, "required": ["label"]},
    },
    {
        "name": "architect_thinking_mind_map_node_update", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Update a node in a caller-owned Mind Map only.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "node": {"type": "string"}, "node_id": {"type": "string"}, "id": {"type": "string"}, "label": {"type": "string"}, "title": {"type": "string"}, "notes": {"type": "string"}, "node_type": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "color": {"type": "string"}, "position": {"type": "object"}, "x": {"type": "number"}, "y": {"type": "number"}, "sort_order": {"type": "integer"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_thinking_mind_map_node_position", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Move a node in a caller-owned Mind Map only.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "node": {"type": "string"}, "node_id": {"type": "string"}, "id": {"type": "string"}, "position": {"type": "object"}, "x": {"type": "number"}, "y": {"type": "number"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_thinking_mind_map_link_create", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Create a link between nodes in a caller-owned Mind Map.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "source_node_id": {"type": "string"}, "source": {"type": "string"}, "target_node_id": {"type": "string"}, "target": {"type": "string"}, "label": {"type": "string"}, "link_type": {"type": "string"}, "sort_order": {"type": "integer"}, "group": {"type": "string"}}},
    },
    {
        "name": "architect_thinking_mind_map_link_update", "authority": {"requirements": [{"capability": "thinking.read","minimum_scope": "self","target_argument": "mind_map","target_kind": "mind_map"},{"capability": "thinking.read","minimum_scope": "self","target_argument": "map_id","target_kind": "mind_map"},{"capability": "thinking.write","minimum_scope": "self","handler_scoped": True}]},
        "description": "Update a link in a caller-owned Mind Map only.",
        "inputSchema": {"type": "object", "properties": {"mind_map": {"type": "string"}, "map_id": {"type": "string"}, "link": {"type": "string"}, "link_id": {"type": "string"}, "id": {"type": "string"}, "label": {"type": "string"}, "link_type": {"type": "string"}, "source_node_id": {"type": "string"}, "source": {"type": "string"}, "target_node_id": {"type": "string"}, "target": {"type": "string"}, "sort_order": {"type": "integer"}, "group": {"type": "string"}}},
    },
]

_ARCHITECT_TOOL_SPECS.extend([
    {
        "name": "architect_decision_get",
        "authority": {
            "requirements": [{
                "capability": "decision.read",
                "minimum_scope": "self",
                "target_argument": "id",
                "target_kind": "decision",
            }],
        },
        "description": "Read one decision authored by an Architect in this group.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "architect_decision_review",
        "authority": {
            "requirements": [{
                "capability": "decision.accept",
                "minimum_scope": "self",
                "target_argument": "id",
                "target_kind": "decision",
            }],
        },
        "description": "Accept, reject, or mark a caller-owned proposed decision as revised.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "decision": {
                    "type": "string",
                    "enum": ["accepted", "rejected", "revised"],
                },
                "rationale": {"type": "string"},
            },
            "required": ["id", "decision"],
        },
    },
])

_ARCHITECT_THINKING_TOOL_SPECS.extend([
    {
        "name": "architect_thinking_archive",
        "authority": {
            "requirements": [{
                "capability": "thinking.write",
                "minimum_scope": "self",
                "handler_scoped": True,
            }],
        },
        "description": "Archive a caller-owned Thinking artifact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_type": {
                    "type": "string",
                    "enum": ["scratchpad", "mind_map"],
                },
                "artifact": {"type": "string"},
            },
            "required": ["artifact_type", "artifact"],
        },
    },
    {
        "name": "architect_thinking_mind_map_node_delete",
        "authority": {
            "requirements": [{
                "capability": "thinking.write",
                "minimum_scope": "self",
                "handler_scoped": True,
            }],
        },
        "description": "Delete a node from a caller-owned Mind Map.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mind_map": {"type": "string"},
                "map_id": {"type": "string"},
                "node": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "architect_thinking_mind_map_link_delete",
        "authority": {
            "requirements": [{
                "capability": "thinking.write",
                "minimum_scope": "self",
                "handler_scoped": True,
            }],
        },
        "description": "Delete a link from a caller-owned Mind Map.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mind_map": {"type": "string"},
                "map_id": {"type": "string"},
                "link": {"type": "string"},
                "link_id": {"type": "string"},
            },
            "required": ["link"],
        },
    },
])


def _copy_tool_spec(tool: dict) -> dict:
    copied = deepcopy(tool)
    if str(copied.get("name", "") or "").strip() in ARCHITECT_DEFERRED_TOOL_NAMES:
        copied["deferred"] = True
    return copied


ARCHITECT_TOOLS = [
    _copy_tool_spec(tool)
    for tool in (
        _ARCHITECT_TOOL_SPECS
        + _ARCHITECT_PRODUCT_TOOL_SPECS
        + _ARCHITECT_THINKING_TOOL_SPECS
        + _architect_task_execution_tool_specs()
        + _architect_worktree_tool_specs()
    )
]

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


def _bound_architect_id_candidates_from_env() -> list[str]:
    """Return candidate Architect ids in safest binding order.

    ``TORQUE_CELL_ID`` is the per-PTY identity installed by Torque's terminal
    runtime.  ``TORQUE_ARCHITECT_ID`` may also be present for compatibility,
    but older shared Codex MCP config files could persist a stale Architect id.
    Prefer the current cell id when it validates as an Architect, then fall
    back to the explicit kind-specific variable for non-Torque/manual launch
    compatibility.
    """
    candidates = []
    for key in ("TORQUE_CELL_ID", _ENV_VAR):
        value = str(os.environ.get(key, "") or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


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


def _validate_architect_record(architect_id: str, record: dict | None) -> str:
    if not record:
        return f"no architect with id={architect_id} exists"
    if str(record.get("cell_type", "") or "").strip() != "agent":
        return f"cell {architect_id} is not an agent"
    kind = str(record.get("kind", "") or "").strip()
    if kind != "architect":
        return (
            f"agent {architect_id} exists but kind={kind or '<empty>'}; "
            "expected architect"
        )
    group = str(record.get("group", "") or "").strip()
    if not group:
        return f"architect {architect_id} is not assigned to a group"
    return ""


def validate_architect_binding(state=None) -> tuple[str, str]:
    candidates = _bound_architect_id_candidates_from_env()
    if not candidates:
        return "", f"{_ENV_VAR} is required"

    errors = []
    if state is None:
        for architect_id in candidates:
            try:
                record = _load_architect_row_from_db(architect_id)
            except Exception as exc:
                return "", f"failed to load Torque state for architect binding: {exc}"
            error = _validate_architect_record(architect_id, record)
            if not error:
                return architect_id, ""
            errors.append(error)
        return "", errors[0]

    for architect_id in candidates:
        cell = state.agents.get(architect_id)
        record = None
        if cell is not None:
            record = {
                "cell_type": getattr(cell, "cell_type", ""),
                "kind": getattr(cell, "kind", ""),
                "group": getattr(cell, "group", ""),
            }
        error = _validate_architect_record(architect_id, record)
        if not error:
            return architect_id, ""
        errors.append(error)
    return "", errors[0]


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
