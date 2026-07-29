"""Minimal MCP (Model Context Protocol) server over streamable HTTP.

Implements just enough of the MCP spec (JSON-RPC 2.0 over POST) to expose
torque ai commands as tools.  No external dependencies — uses aiohttp
routes on the existing server.

Agent identity comes from the ``X-Torque-Cell-Id`` header.  Claude Code
populates it from ``${TORQUE_CELL_ID}`` in ``.mcp.json``; Codex uses
``env_http_headers`` in Torque-owned generated per-agent config.
"""

import asyncio
import json
import logging
import time
import uuid

from aiohttp import web

from . import __version__
from .deploy_state import agent_session_runtime_provenance_payload
from .capability_catalog import (
    CAPABILITY_CATALOG,
)
from .agent_classes import (
    apply_frozen_platform_group_board_authority,
    has_frozen_platform_group_board_authority,
    has_frozen_platform_task_authority_mode,
)
from .mcp_authority import (
    EffectiveAuthority,
    AuthorityValidationError,
    authority_definition_map,
    authority_definitions_from_tool_specs,
    audit_tool_authority_coverage,
    effective_authority_from_snapshot,
)
from .mcp_architect import (
    ARCHITECT_TOOLS,
    _dispatch_architect_tool,
)
from .mcp_engineer import (
    ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES,
    ENGINEER_TOOLS,
    _dispatch_engineer_tool,
    engineer_tools_for_cell,
)
from .mcp_public_call_authorization import (
    PublicCallAuthorizationDependencies,
    _PUBLIC_TOOL_CALL_AUTHORIZED,
    _PUBLIC_TOOL_CALL_TARGET_SCOPE_DENIED,
    _PUBLIC_TOOL_CALL_UNKNOWN,
    _classify_public_tool_call as _classify_public_tool_call_with_dependencies,
    _resolve_public_tool_call as _resolve_public_tool_call_with_dependencies,
    _resolve_scoped_resource,
    _scoped_resource_relationship,
    public_call_refusal_message,
)
from .mcp_tool_search import deferred_tool_specs, public_tool_spec
from .mcp_tool_search import tool_search_response
from .mcp_canonical import (
    authority_is_proposal_only,
    canonical_callable_handler_registry,
    canonical_registry_missing_handlers,
    canonical_tool_name,
    canonicalize_tool_specs,
    modernize_tool_authority,
)
from .help_docs import dispatch_help_tool, help_tool_specs
from .mcp_tools_shared import (
    _direct_user_message_response,
    save_agent_user_direct_message_from_mcp,
)
from .mcp_scoped.action_catalog import (
    ACTION_CATALOG_TOOL,
    SHARED_ACTION_CATALOG_TOOL_NAMES,
    dispatch_action_catalog_tool,
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
_CONTEXT_DEFAULT_TASK_LIMIT = 3
_CONTEXT_DESCRIPTION_PREVIEW_LIMIT = 600
from .mcp_session_wake import (
    _SESSION_WAKE_DEDUPE_SECS,
    _SESSION_WAKE_SEEN,
    _SESSION_WAKE_TASKS,
    _claim_session_wake,
    _emit_session_wake_entry,
    _extract_entry_timestamp,
    _format_session_wake_age,
    _format_session_wake_timestamp,
    _latest_architect_checkpoint_timestamp,
    _latest_engineer_checkpoint_timestamp,
    _log_session_wake_failure,
    _queue_session_wake_entry,
    _session_wake_checkpoint_fragment,
    _session_wake_deduped,
    reset_session_wake_state,
)

# Preserve the legacy facade symbols while session-wake bookkeeping lives in
# its focused background-journaling module.
reset_session_wake_state()

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "torque_context", "authority": {"requirements": [{"capability": "self.read","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Get current agent identity, status, and linked tasks. "
            "Returns the agent's name, group, directory, worktree info, "
            "its session/daemon code-revision provenance, and bounded summaries "
            "of board tasks currently assigned to this agent (with the total "
            "available reported). Set detail=true for complete legacy records. Use "
            "this to understand your current assignment before starting work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "detail": {
                    "type": "boolean",
                    "description": (
                        "Return the complete legacy agent and task records. "
                        "The default is a bounded orientation summary."
                    ),
                },
            },
        },
    },
    {
        "name": "torque_area_list", "authority": {"requirements": [{"capability": "planning.area.read","minimum_scope": "self","result_kind": "area","result_paths": ["areas"]}]},
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
        "name": "torque_area_show", "authority": {"requirements": [{"capability": "planning.area.read","minimum_scope": "self","target_argument": "area","target_kind": "area"},{"capability": "planning.area.read","minimum_scope": "self","target_argument": "area_id","target_kind": "area"}]},
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
    *help_tool_specs("torque_"),
    ACTION_CATALOG_TOOL,
    {
        "name": "torque_task_upload_artifact", "authority": {"requirements": [{"capability": "task.artifact.write","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_done", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Record completion of the current worker's task. Normally moves "
            "the task to Done and triggers cascade completion; if derived "
            "follow-up work remains open, the task stays In Progress as "
            "Worker Complete — Follow-up Open until the existing cascade "
            "resolves the chain."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Optional completion summary.",
                },
                "terminal_declaration": {
                    "type": "string",
                    "description": (
                        "Required when this task has an available derive "
                        "transition. State that no further work is needed and "
                        "that you will not derive after this; boilerplate is "
                        "acceptable."
                    ),
                },
                "deviation_statement": {
                    "type": "string",
                    "description": (
                        "Optional worker attestation of a deliberate deviation "
                        "from the task's acceptance criteria. Provide together "
                        "with deviation_reason; a lone field is recorded as an "
                        "incomplete disclosure attempt."
                    ),
                },
                "deviation_reason": {
                    "type": "string",
                    "description": (
                        "Why the stated acceptance-criteria deviation was "
                        "deliberate. Provide together with deviation_statement; "
                        "a lone field is recorded as an incomplete disclosure "
                        "attempt."
                    ),
                },
            },
        },
    },
    {
        "name": "torque_blocked", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_error", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_progress", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_verify", "authority": {"requirements": [{"capability": "task.verify","minimum_scope": "self","handler_scoped": True}]},
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
                "finalization_review": {
                    "type": "object",
                    "description": "Typed finalization-review evidence for this declared review task; prose is not accepted.",
                    "properties": {
                        "gate_id": {"type": "string"},
                        "verdict": {"type": "string", "enum": ["ship", "block", "needs_followup", "unknown"]},
                        "has_blocking_issues": {"type": "boolean"},
                        "required_follow_up_resolved": {"type": "boolean"},
                        "boundary": {"type": "string"},
                    },
                    "required": ["gate_id", "verdict", "has_blocking_issues", "required_follow_up_resolved", "boundary"],
                    "additionalProperties": False,
                },
            },
        },
    },
    {
        "name": "torque_ready", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Signal that this agent is done and ready for the next task. "
            "Normally moves the task to Done, unlinks the agent, and "
            "cascades completion up the parent chain. If derived follow-up "
            "work remains open, it records the worker completion while the "
            "task stays In Progress as Worker Complete — Follow-up Open."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "terminal_declaration": {
                    "type": "string",
                    "description": (
                        "Required when this task has an available derive "
                        "transition. State that no further work is needed and "
                        "that you will not derive after this; boilerplate is "
                        "acceptable."
                    ),
                },
                "deviation_statement": {
                    "type": "string",
                    "description": (
                        "Optional worker attestation of a deliberate deviation "
                        "from acceptance criteria. Provide with deviation_reason; "
                        "a lone field is recorded as an incomplete disclosure "
                        "attempt."
                    ),
                },
                "deviation_reason": {
                    "type": "string",
                    "description": (
                        "Why the stated acceptance-criteria deviation was "
                        "deliberate. Provide with deviation_statement; a lone "
                        "field is recorded as an incomplete disclosure attempt."
                    ),
                },
            },
        },
    },
    {
        "name": "torque_name", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_derive", "authority": {"requirements": [{"capability": "task.report","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_ask", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_message_user", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
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
                        "Message ID from a `## Message from the User` block "
                        "when replying. Omit for a proactive message. Torque "
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
        "name": "torque_get_user_message_loop", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Read the active user-scheduled /loop targeting this agent. This "
            "never reveals loops targeting another agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "torque_stop_user_message_loop", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
        "description": (
            "Stop the active user-scheduled /loop for this agent. This only "
            "affects the caller's own direct-message loop and adds a visible "
            "audit message for the user/operator."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional reason why the loop is no longer actionable.",
                },
            },
        },
    },
    {
        "name": "torque_reply", "authority": {"requirements": [{"capability": "message.user","minimum_scope": "self","handler_scoped": True}]},
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
        "name": "torque_memory_publish", "authority": {"requirements": [{"capability": "memory.write","minimum_scope": "group","handler_scoped": True}]},
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
        "name": "torque_memory_list", "authority": {"requirements": [{"capability": "memory.read","minimum_scope": "group","handler_scoped": True}]},
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
        "name": "torque_memory_read", "authority": {"requirements": [{"capability": "memory.read","minimum_scope": "group","handler_scoped": True}]},
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
        "name": "torque_memory_pin", "authority": {"requirements": [{"capability": "memory.admin","minimum_scope": "group","handler_scoped": True}]},
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
        "name": "torque_memory_link", "authority": {"requirements": [{"capability": "memory.admin","minimum_scope": "group","handler_scoped": True}]},
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
        "name": "torque_memory_unpin", "authority": {"requirements": [{"capability": "memory.admin","minimum_scope": "group","handler_scoped": True}]},
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

# Internal handlers retain historical names, but their authority descriptors
# use the canonical relationship vocabulary.
TOOLS = [
    modernize_tool_authority(tool, caller_kind="worker")
    for tool in TOOLS
]
ENGINEER_TOOLS = [
    modernize_tool_authority(tool, caller_kind="engineer")
    for tool in ENGINEER_TOOLS
]
ARCHITECT_TOOLS = [
    modernize_tool_authority(tool, caller_kind="architect")
    for tool in ARCHITECT_TOOLS
]

# Combined tool list
ALL_TOOLS = TOOLS + ARCHITECT_TOOLS + ENGINEER_TOOLS
_ALL_TOOL_MAP = {t["name"]: t for t in ALL_TOOLS}

# Public MCP names are canonical while dispatch remains intentionally routed
# through the mature role-scoped handlers.  This registry is the explicit
# bridge between those two contracts; it is also used by tools/call instead of
# reconstructing a separate candidate list from the advertised surface.
CANONICAL_CALLABLE_HANDLER_REGISTRY = canonical_callable_handler_registry(
    ALL_TOOLS
)


def _assert_canonical_callable_registry() -> None:
    """Fail closed if an Architect public schema has no registered handler."""

    architect_surface = canonicalize_tool_specs(
        ARCHITECT_TOOLS,
        caller_kind="architect",
    )
    missing = canonical_registry_missing_handlers(
        architect_surface,
        CANONICAL_CALLABLE_HANDLER_REGISTRY,
    )
    if missing:
        raise RuntimeError(
            "Architect canonical MCP tools lack registered handlers: "
            + ", ".join(missing)
        )


_assert_canonical_callable_registry()

WORKER_TOOL_AUTHORITY_DEFINITIONS = authority_definitions_from_tool_specs(
    TOOLS,
    base_kinds={"worker", "engineer", "architect"},
    capabilities=CAPABILITY_CATALOG,
)
ENGINEER_TOOL_AUTHORITY_DEFINITIONS = authority_definitions_from_tool_specs(
    ENGINEER_TOOLS,
    base_kinds={"engineer"},
    capabilities=CAPABILITY_CATALOG,
)
ARCHITECT_TOOL_AUTHORITY_DEFINITIONS = authority_definitions_from_tool_specs(
    ARCHITECT_TOOLS,
    base_kinds={"architect"},
    capabilities=CAPABILITY_CATALOG,
)
MCP_TOOL_AUTHORITY_DEFINITIONS = authority_definition_map((
    *WORKER_TOOL_AUTHORITY_DEFINITIONS,
    *ENGINEER_TOOL_AUTHORITY_DEFINITIONS,
    *ARCHITECT_TOOL_AUTHORITY_DEFINITIONS,
))
MCP_CANONICAL_AUTHORITY_VARIANTS = {}
for _kind, _definitions in (
    ("worker", WORKER_TOOL_AUTHORITY_DEFINITIONS),
    ("engineer", (*WORKER_TOOL_AUTHORITY_DEFINITIONS, *ENGINEER_TOOL_AUTHORITY_DEFINITIONS)),
    ("architect", (*WORKER_TOOL_AUTHORITY_DEFINITIONS, *ARCHITECT_TOOL_AUTHORITY_DEFINITIONS)),
):
    _by_name = {}
    for _definition in _definitions:
        _by_name.setdefault(
            canonical_tool_name(_definition.name),
            [],
        ).append(_definition)
    MCP_CANONICAL_AUTHORITY_VARIANTS[_kind] = {
        _name: tuple(_variants)
        for _name, _variants in _by_name.items()
    }

# Compatibility/read-model projection. The source of truth is each tool's
# first-class authority descriptor above, not this derived index.
_MCP_TOOL_CAPABILITIES = {
    name: frozenset(
        requirement.capability for requirement in definition.requirements
    )
    for name, definition in MCP_TOOL_AUTHORITY_DEFINITIONS.items()
}

# Authority metadata must cover the exact registered Torque MCP surface. This
# remains deliberately fail-closed: a newly registered tool without colocated
# metadata aborts module initialization.
MCP_AUTHORITY_COVERAGE = audit_tool_authority_coverage(
    ALL_TOOLS,
    _MCP_TOOL_CAPABILITIES,
    known_capabilities=CAPABILITY_CATALOG,
)
MCP_AUTHORITY_COVERAGE.require_valid()

MCP_AUTHORITY_SURFACE_COVERAGE = {}
for _surface_name, _surface_tools, _surface_definitions in (
    ("worker", TOOLS, WORKER_TOOL_AUTHORITY_DEFINITIONS),
    ("engineer", ENGINEER_TOOLS, ENGINEER_TOOL_AUTHORITY_DEFINITIONS),
    ("architect", ARCHITECT_TOOLS, ARCHITECT_TOOL_AUTHORITY_DEFINITIONS),
):
    _requirements = {
        definition.name: frozenset(
            requirement.capability for requirement in definition.requirements
        )
        for definition in _surface_definitions
    }
    _coverage = audit_tool_authority_coverage(
        _surface_tools,
        _requirements,
        known_capabilities=CAPABILITY_CATALOG,
    )
    _coverage.require_valid()
    MCP_AUTHORITY_SURFACE_COVERAGE[_surface_name] = _coverage


def mcp_tool_capability_requirements(
    tool_name: str,
    caller_kind: str = "",
) -> frozenset[str] | None:
    """Return the capability requirements declared by an MCP surface."""

    name = str(tool_name or "").strip()
    direct = _MCP_TOOL_CAPABILITIES.get(name)
    if direct is not None:
        return direct
    kind = str(caller_kind or "").strip()
    variants = MCP_CANONICAL_AUTHORITY_VARIANTS.get(kind, {}).get(name, ())
    if not variants:
        variants = tuple(
            variant
            for by_name in MCP_CANONICAL_AUTHORITY_VARIANTS.values()
            for variant in by_name.get(name, ())
        )
    if not variants:
        return None
    return frozenset(
        requirement.capability
        for variant in variants
        for requirement in variant.requirements
    )


def mcp_tool_allowed_by_authority(
    tool_name: str,
    authority: EffectiveAuthority,
) -> bool:
    """Project a tool from canonical frozen Agent Class authority."""

    tool_name = str(tool_name or "").strip()
    direct = MCP_TOOL_AUTHORITY_DEFINITIONS.get(tool_name)
    variants = (
        (direct,)
        if direct
        else MCP_CANONICAL_AUTHORITY_VARIANTS.get(
            str(authority.base_kind or "").strip(),
            {},
        ).get(tool_name, ())
    )
    if not variants:
        return False
    for tool_authority in variants:
        allowed = True
        for requirement in tool_authority.requirements:
            definition = CAPABILITY_CATALOG.get(requirement.capability)
            if not definition:
                allowed = False
                break
            if not definition.scoped:
                if not authority.has(requirement.capability):
                    allowed = False
                    break
                continue
            if not authority.allows(
                requirement.capability,
                scope=requirement.minimum_scope,
            ):
                allowed = False
                break
        if allowed:
            return True
    return False


def _effective_class_authority_for_cell(cell) -> EffectiveAuthority | None:
    if not cell:
        return None
    class_snapshot = getattr(cell, "effective_agent_class_snapshot", {})
    if not isinstance(class_snapshot, dict):
        return None
    authority_snapshot = class_snapshot.get("effective_authority")
    if not authority_snapshot:
        return None
    try:
        authority = effective_authority_from_snapshot(
            authority_snapshot,
            capabilities=CAPABILITY_CATALOG,
        )
        return apply_frozen_platform_group_board_authority(cell, authority)
    except AuthorityValidationError:
        # Frozen authority corruption fails closed rather than broadening the
        # caller's projected authority.
        return EffectiveAuthority(
            base_kind=str(getattr(cell, "kind", "") or ""),
            mode="allow",
            capabilities={},
        )


def _authority_project_tools(
    tools: list[dict],
    authority: EffectiveAuthority | None = None,
) -> list[dict]:
    if authority is None:
        return list(tools)
    return [
        tool for tool in tools
        if mcp_tool_allowed_by_authority(
            str(tool.get("name", "") or ""),
            authority,
        )
    ]


def _raw_tools_for_caller(
    state,
    cell_id: str,
    *,
    include_tombstoned: bool = False,
) -> tuple[list[dict], object, str]:
    """Return authority-projected internal operations for one caller."""

    cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
    if cell and state.agent_is_tombstoned(cell) and not include_tombstoned:
        return [], cell, ""
    authority = _effective_class_authority_for_cell(cell)
    caller_kind = str(getattr(cell, "kind", "") or "").strip() if cell else ""
    base_tools = TOOLS
    if caller_kind in {"engineer", "architect"}:
        # Persistent orchestrators share context, help, and memory primitives
        # with workers, but should not inherit worker lifecycle/reporting
        # operations such as done, derive, ready, rename, or worker reply.
        shared_names = {
            "torque_context",
            "torque_help_list",
            "torque_help_show",
            "torque_help_search",
            "torque_help_query",
            *SHARED_ACTION_CATALOG_TOOL_NAMES,
            "torque_memory_publish",
            "torque_memory_list",
            "torque_memory_read",
            "torque_memory_pin",
            "torque_memory_unpin",
            "torque_memory_link",
            # A persistent agent may inspect or stop only its own user loop.
            # Authority projection below keeps this out of classes without
            # message.user, while making it available to eligible Architects.
            "torque_get_user_message_loop",
            "torque_stop_user_message_loop",
        }
        base_tools = [
            tool for tool in TOOLS
            if str(tool.get("name", "") or "").strip() in shared_names
        ]
    tools = list(_authority_project_tools(base_tools, authority))
    # Product Manager is Architect-derived, but its explicit task_* grant
    # includes the four current-task reporters and derive.  These retain their
    # normal current-task semantics (and can therefore be inert when no task is
    # assigned); they are deliberately not inherited by other Architects.
    if caller_kind == "architect" and (
            has_frozen_platform_task_authority_mode(cell, "creator-proposal-only")
            or has_frozen_platform_group_board_authority(cell)
    ):
        product_manager_current_task_tools = {
            "torque_done",
            "torque_blocked",
            "torque_error",
            "torque_progress",
            "torque_derive",
        }
        tools.extend(_authority_project_tools(
            [
                tool for tool in TOOLS
                if str(tool.get("name", "") or "").strip()
                in product_manager_current_task_tools
            ],
            authority,
        ))
    if caller_kind == "engineer":
        tools.extend(_authority_project_tools(
            engineer_tools_for_cell(cell, state),
            authority,
        ))
    elif caller_kind == "architect":
        tools.extend(_authority_project_tools(
            ARCHITECT_TOOLS,
            authority,
        ))
    return tools, cell, caller_kind


def _canonical_tools_for_caller(
    state,
    cell_id: str,
    *,
    include_tombstoned: bool = False,
) -> list[dict]:
    tools, _cell, caller_kind = _raw_tools_for_caller(
        state,
        cell_id,
        include_tombstoned=include_tombstoned,
    )
    if not tools:
        return []
    canonical = canonicalize_tool_specs(
        tools,
        caller_kind=caller_kind or "worker",
        proposal_only=authority_is_proposal_only(
            _effective_class_authority_for_cell(_cell)
        ),
    )
    missing = canonical_registry_missing_handlers(
        canonical,
        CANONICAL_CALLABLE_HANDLER_REGISTRY,
    )
    if missing:
        raise RuntimeError(
            "Advertised canonical MCP tools lack registered handlers: "
            + ", ".join(missing)
        )
    return canonical


def _visible_tools(state, cell_id: str):
    """Return the complete canonical MCP catalog visible to the caller.

    MCP clients can defer tool schemas only after the server advertises those
    tools through ``tools/list``.  Omitting Torque's internally deferred tools
    made their schemas searchable as text while leaving the client without a
    registered callable handler.  Keep eager/deferred classification internal
    and let each provider's native tool-search layer decide which advertised
    schemas enter the model context.
    """

    return [
        public_tool_spec(tool)
        for tool in _canonical_tools_for_caller(state, cell_id)
    ]


def _undeclared_public_argument_names(
    state,
    cell_id: str,
    requested_tool_name: str,
    arguments: dict,
) -> list[str]:
    """Return public-schema keys supplied to an exact public tool name.

    MCP clients are not uniformly strict about a tool's input schema.  Torque
    therefore accepts extra keys for forward compatibility, but must not let
    that tolerance masquerade as proof that a key is part of the contract.
    Hidden legacy aliases intentionally retain their old argument shapes, so
    this check applies only to the exact canonical name advertised to the
    caller.
    """

    if not isinstance(arguments, dict):
        return []
    requested = str(requested_tool_name or "").strip()
    if not requested:
        return []
    for tool in _canonical_tools_for_caller(
        state,
        cell_id,
        include_tombstoned=True,
    ):
        if requested != str(tool.get("name", "") or "").strip():
            continue
        properties = (
            tool.get("inputSchema", {})
            .get("properties", {})
        )
        if not isinstance(properties, dict):
            return []
        return sorted(
            str(key)
            for key in arguments
            if str(key) not in properties
        )
    return []


def _undeclared_public_argument_notice(names: list[str]) -> str:
    """Format a caller-facing receipt for tolerated non-schema arguments."""

    label = "parameter" if len(names) == 1 else "parameters"
    return (
        f"Undeclared {label} received: {', '.join(names)}. "
        "They are not part of this public tool schema."
    )


def _append_undeclared_public_argument_notice(result: dict, names: list[str]) -> dict:
    """Expose tolerated non-schema arguments without rejecting the call."""

    if not names or not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    notice = _undeclared_public_argument_notice(names)
    if any(
        isinstance(block, dict) and block.get("text") == notice
        for block in content
    ):
        return result
    result = dict(result)
    result["content"] = [
        *content,
        {"type": "text", "text": notice},
    ]
    return result


def _deferred_tools_for_caller(state, cell_id: str):
    """Return deferred MCP tool schemas available to the caller."""
    return deferred_tool_specs(_canonical_tools_for_caller(state, cell_id))


_PUBLIC_CALL_AUTHORIZATION_DEPENDENCIES = PublicCallAuthorizationDependencies(
    raw_tools_for_caller=_raw_tools_for_caller,
    effective_class_authority_for_cell=_effective_class_authority_for_cell,
    visible_tools=_visible_tools,
    all_tool_map=_ALL_TOOL_MAP,
    canonical_callable_handler_registry=CANONICAL_CALLABLE_HANDLER_REGISTRY,
    engineer_architect_chain_tool_names=ENGINEER_ARCHITECT_CHAIN_TOOL_NAMES,
    tool_authority_definitions=MCP_TOOL_AUTHORITY_DEFINITIONS,
    tool_allowed_by_authority=mcp_tool_allowed_by_authority,
)


def _resolve_public_tool_call(
    state,
    cell_id: str,
    name: str,
    arguments: dict,
) -> tuple[str, dict]:
    """Preserve the legacy facade for public-call resolution."""
    return _resolve_public_tool_call_with_dependencies(
        state,
        cell_id,
        name,
        arguments,
        _PUBLIC_CALL_AUTHORIZATION_DEPENDENCIES,
    )


def _classify_public_tool_call(
    state,
    cell_id: str,
    requested_tool_name: str,
    arguments: dict,
) -> tuple[str, str, dict, object, str]:
    """Preserve the legacy facade for public-call classification."""
    return _classify_public_tool_call_with_dependencies(
        state,
        cell_id,
        requested_tool_name,
        arguments,
        _PUBLIC_CALL_AUTHORIZATION_DEPENDENCIES,
    )



def _result_collection_at_path(payload, path: str):
    """Return ``(parent, key, collection)`` for a dotted result path."""

    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        return None
    parent = payload
    for part in parts[:-1]:
        if not isinstance(parent, dict) or part not in parent:
            return None
        parent = parent[part]
    key = parts[-1]
    if not isinstance(parent, dict) or not isinstance(parent.get(key), list):
        return None
    return parent, key, parent[key]


def _scoped_result_resource(state, caller_cell, result_kind: str, item):
    """Resolve a declared result item to the resource used for ACL scope."""

    if result_kind in {"event", "semantic_recall"} and isinstance(item, dict):
        return item
    if isinstance(item, dict):
        if result_kind == "agent":
            reference = (
                item.get("agent_id")
                or item.get("cell_id")
                or item.get("id")
            )
        else:
            reference = item.get("id") or item.get(f"{result_kind}_id")
    else:
        reference = item
    reference = str(reference or "").strip()
    if not reference:
        return None
    return _resolve_scoped_resource(
        state,
        caller_cell,
        result_kind,
        reference,
    )


def _apply_tool_result_scope_filters(
    state,
    tool_name: str,
    result: dict,
    caller_cell,
) -> dict:
    """Filter declared result collections through frozen class authority.

    Result filtering is descriptor-driven and fail-closed. It is used only for
    collection paths explicitly declared by a tool; aggregate tools without a
    safely filterable result contract continue to require their broader
    ``minimum_scope``.
    """

    authority = _effective_class_authority_for_cell(caller_cell)
    tool_authority = MCP_TOOL_AUTHORITY_DEFINITIONS.get(tool_name)
    if not tool_authority or result.get("isError"):
        return result
    filters = [
        requirement
        for requirement in tool_authority.requirements
        if requirement.result_paths
    ]
    if not filters:
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    try:
        block = next(
            item for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
        payload = json.loads(str(block.get("text", "") or ""))
        for requirement in filters:
            for path in requirement.result_paths:
                located = _result_collection_at_path(payload, path)
                if located is None:
                    raise ValueError(f"missing declared result path {path}")
                parent, key, collection = located
                filtered = []
                for item in collection:
                    allowed = authority is None
                    if authority is not None:
                        resource = _scoped_result_resource(
                            state,
                            caller_cell,
                            requirement.result_kind,
                            item,
                        )
                        if resource is not None:
                            target_scope = _scoped_resource_relationship(
                                state,
                                caller_cell,
                                requirement.result_kind,
                                resource,
                            )
                            allowed = authority.allows(
                                requirement.capability,
                                scope=target_scope,
                            )
                    if allowed:
                        if isinstance(item, dict):
                            item = {
                                key: value
                                for key, value in item.items()
                                if not str(key).startswith("_acl_")
                            }
                        filtered.append(item)
                parent[key] = filtered
                # Collection summaries must not retain pre-filter counts.
                if len(filtered) != len(collection):
                    for count_key in ("count", "total", "tasks_total"):
                        if count_key in parent:
                            parent[count_key] = len(filtered)
        block["text"] = json.dumps(payload, separators=(",", ":"))
        return result
    except (StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return {
            "content": [{
                "type": "text",
                "text": "Tool result could not be safely scope-filtered",
            }],
            "isError": True,
        }


def _apply_inline_field_authority(result: dict, caller_cell) -> dict:
    """Remove capability-gated inline fields from otherwise visible records."""

    authority = _effective_class_authority_for_cell(caller_cell)
    if (
        authority is None
        or authority.has("task.board_sync.read")
        or result.get("isError")
    ):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result

    def strip_board_sync(value):
        if isinstance(value, dict):
            value.pop("board_sync", None)
            for child in value.values():
                strip_board_sync(child)
        elif isinstance(value, list):
            for child in value:
                strip_board_sync(child)

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            payload = json.loads(str(block.get("text", "") or ""))
        except (TypeError, json.JSONDecodeError):
            continue
        strip_board_sync(payload)
        block["text"] = json.dumps(payload, separators=(",", ":"))
    return result


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
        tasks = [
            (tid, task) for tid, task in state.board_tasks.items()
            if task.agent_id == cell_id
        ]
        runtime_provenance = agent_session_runtime_provenance_payload(state, cell)
        if bool(args.get("detail", False)):
            return json.dumps({
                "detail": "full",
                "agent": asdict(cell),
                "runtime_provenance": runtime_provenance,
                "tasks": {
                    tid: serialize_task_for_mcp(task, tasks_by_id=state.board_tasks)
                    for tid, task in tasks
                },
            }, indent=2), False

        def preview(value, limit=_CONTEXT_DESCRIPTION_PREVIEW_LIMIT):
            text = str(value or "")
            return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

        def artifact_refs(items):
            refs = []
            for artifact in list(items or [])[:5]:
                if not isinstance(artifact, dict):
                    continue
                refs.append({
                    key: preview(artifact.get(key, ""), limit=160)
                    for key in (
                        "type", "title", "filename", "url", "path",
                        "source_task_id", "source_task_label",
                    )
                    if artifact.get(key, "") not in (None, "")
                })
            return refs

        def task_summary(task):
            description = str(getattr(task, "description", "") or "")
            title = str(getattr(task, "task", "") or "")
            labels = list(getattr(task, "labels", []) or [])
            serialized = serialize_task_for_mcp(
                task,
                tasks_by_id=state.board_tasks,
            )
            task_artifacts = list(serialized.get("task_artifacts", []) or [])
            upstream_artifacts = list(serialized.get("upstream_artifacts", []) or [])
            return {
                "id": str(getattr(task, "id", "") or ""),
                "slug": str(getattr(task, "slug", "") or ""),
                "title": preview(title),
                "title_length": len(title),
                "title_truncated": len(title) > _CONTEXT_DESCRIPTION_PREVIEW_LIMIT,
                "lane": str(getattr(task, "lane", "") or ""),
                "status": str(getattr(task, "status", "") or ""),
                "dispatch_state": str(getattr(task, "dispatch_state", "") or ""),
                "health_state": str(getattr(task, "health_state", "") or ""),
                "labels": [preview(label, limit=120) for label in labels[:20]],
                "labels_total": len(labels),
                "labels_capped": len(labels) > 20,
                "parent_task_id": str(getattr(task, "parent_task_id", "") or ""),
                "pipeline_depth": int(getattr(task, "pipeline_depth", 0) or 0),
                "description_preview": preview(description),
                "description_length": len(description),
                "description_truncated": len(description) > _CONTEXT_DESCRIPTION_PREVIEW_LIMIT,
                "artifact_count": len(getattr(task, "attachments", []) or [])
                    + len(getattr(task, "artifacts", []) or []),
                # These are references rather than task prose, and retaining
                # them keeps the orientation call useful for artifact handoff.
                "task_artifacts": artifact_refs(task_artifacts),
                "task_artifacts_total": len(task_artifacts),
                "task_artifacts_capped": len(task_artifacts) > 5,
                "upstream_artifacts": artifact_refs(upstream_artifacts),
                "upstream_artifacts_total": len(upstream_artifacts),
                "upstream_artifacts_capped": len(upstream_artifacts) > 5,
            }

        selected = tasks[:_CONTEXT_DEFAULT_TASK_LIMIT]
        agent = {}
        for key in (
                "id", "name", "slug", "group", "kind", "cell_type", "status",
                "directory", "current_task_id", "worktree_path", "worktree_branch",
                "worktree_base_branch", "worktree_dirty", "worktree_diff",
        ):
            value = getattr(cell, key, "")
            agent[key] = preview(value) if isinstance(value, str) else value
        return json.dumps({
            "type": "torque_context",
            "detail": "summary",
            "detail_available": True,
            "agent": agent,
            "runtime_provenance": runtime_provenance,
            "tasks": {tid: task_summary(task) for tid, task in selected},
            "tasks_total": len(tasks),
            "tasks_returned": len(selected),
            "tasks_capped": len(selected) < len(tasks),
        }, indent=2), False

    action_catalog_result = await dispatch_action_catalog_tool(
        name, args, cell_id, handle_command, state,
    )
    if action_catalog_result is not None:
        return action_catalog_result

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

    if name in {
        "torque_help_list",
        "torque_help_show",
        "torque_help_search",
        "torque_help_query",
    }:
        return dispatch_help_tool(name, args, prefix="torque_")

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

    if name == "torque_get_user_message_loop":
        cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
        if not cell:
            return f"Agent {cell_id} not found", True
        if state.agent_is_tombstoned(cell):
            return f"Agent {cell_id} is tombstoned", True
        # The query deliberately has no target argument: an agent can learn
        # only whether a loop targets itself, never whether another agent is
        # user-scheduled.
        loop = state.active_agent_message_loop_for_agent(cell.id)
        return json.dumps({
            "type": "agent_message_loop",
            "active": bool(loop),
            "loop": {
                "id": loop.id,
                "status": loop.status,
                "interval_seconds": loop.interval_seconds,
                "message": loop.message,
                "run_count": loop.run_count,
                "last_run_at": loop.last_run_at,
                "next_run_at": loop.next_run_at,
            } if loop else None,
        }), False

    if name == "torque_stop_user_message_loop":
        cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
        if not cell:
            return f"Agent {cell_id} not found", True
        if state.agent_is_tombstoned(cell):
            return f"Agent {cell_id} is tombstoned", True
        loop = state.active_agent_message_loop_for_agent(cell.id)
        if not loop:
            return "No active user-scheduled /loop exists for this agent", True
        reason = str(args.get("reason", "") or "").strip()
        stopped = state.agent_message_loop_stop(
            loop.id,
            status="stopped",
            stopped_by=cell.id,
            reason=reason or "Stopped by receiving agent",
        )
        now = time.time()
        audit = {
            "id": "msg-" + uuid.uuid4().hex[:12],
            "thread_id": f"user-agent:user:{cell.id}",
            "reply_to_id": "",
            "idempotency_key": "",
            "group_name": str(getattr(cell, "group", "") or "").strip(),
            "sender_id": "system",
            "sender_kind": "system",
            "sender_name": "System",
            "recipient_id": cell.id,
            "recipient_kind": str(getattr(cell, "kind", "") or "worker").strip() or "worker",
            "recipient_name": str(getattr(cell, "name", "") or "").strip(),
            "message": (
                "Receiving agent stopped /loop."
                + (f" Reason: {reason}" if reason else "")
            ),
            "message_type": "system",
            "created_at": now,
            "context_snapshot": {
                "loop_id": loop.id,
                "loop_status": "stopped",
            },
            "delivery_state": "delivered",
            "delivered_at": now,
        }
        saved_audit = state.save_direct_message(audit)
        await state.broadcast()
        return json.dumps({
            "type": "agent_message_loop_stopped",
            "loop": {
                "id": stopped.id,
                "agent_id": stopped.agent_id,
                "status": stopped.status,
                "stopped_by": stopped.stopped_by,
                "stop_reason": stopped.stop_reason,
                "run_count": stopped.run_count,
            } if stopped else {},
            "audit_message_id": str((saved_audit or {}).get("id", "") or ""),
        }), False

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
        for key in (
                "terminal_declaration",
                "deviation_statement",
                "deviation_reason",
        ):
            if args.get(key):
                payload[key] = args[key]
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
        if "finalization_review" in args:
            payload["finalization_review"] = args.get("finalization_review")
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
        for key in (
                "terminal_declaration",
                "deviation_statement",
                "deviation_reason",
        ):
            if args.get(key):
                payload[key] = args[key]
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


def _jsonrpc_error_with_undeclared_public_argument_notice(
    id,
    code,
    message: str,
    names: list[str],
):
    """Keep JSON-RPC early errors truthful about tolerated public keys."""

    if names:
        message = f"{message} — {_undeclared_public_argument_notice(names)}"
    return _jsonrpc_error(id, code, message)


# ---------------------------------------------------------------------------
# aiohttp handler / replayable dispatch
# ---------------------------------------------------------------------------

def _idempotency_conflict_result(
    req_id,
    tool_name: str,
    undeclared_public_arguments: list[str] | None = None,
):
    return _jsonrpc_error_with_undeclared_public_argument_notice(
        req_id,
        -32602,
        (
            "Idempotency key was reused for a different MCP write"
            f" ({tool_name})"
        ),
        undeclared_public_arguments or [],
    )


def _qualified_mcp_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if not name:
        return ""
    if name.startswith("mcp__"):
        return name
    return "mcp__torque__" + name


async def _notify_mcp_call_observer(observer, observation: dict) -> None:
    """Best-effort MCP call telemetry hook for the Agent Events MCP panel."""
    if not observer:
        return
    try:
        maybe_awaitable = observer(observation)
        if hasattr(maybe_awaitable, "__await__"):
            await maybe_awaitable
    except Exception:
        log.exception(
            "Failed to record MCP call observation for %s",
            observation.get("tool_name", ""),
        )


async def dispatch_mcp_rpc_body(
    body: dict,
    *,
    cell_id: str,
    handle_command,
    state,
    idempotency_header: str = "",
    mcp_session_id: str = "",
    mcp_call_observer=None,
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
        requested_tool_name = (
            params.get("name", "") if isinstance(params, dict) else ""
        )
        raw_arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        arguments = strip_mcp_idempotency_args(raw_arguments)
        public_arguments = dict(arguments)
        # Compute from the advertised schema before classification so an
        # authorization/tombstone/idempotency early return cannot silently
        # erase evidence that a public argument name was undeclared.
        undeclared_public_arguments = _undeclared_public_argument_names(
            state,
            cell_id,
            requested_tool_name,
            public_arguments,
        )
        (
            call_classification,
            tool_name,
            arguments,
            caller_cell,
            caller_kind,
        ) = _classify_public_tool_call(
            state,
            cell_id,
            requested_tool_name,
            public_arguments,
        )
        if caller_cell and state.agent_is_tombstoned(caller_cell):
            return (
                _jsonrpc_error_with_undeclared_public_argument_notice(
                    req_id,
                    -32602,
                    f"Agent {cell_id} is tombstoned",
                    undeclared_public_arguments,
                ),
                200,
            )
        session_wake_pending = (
            caller_kind in {"architect", "engineer"}
            and _claim_session_wake(cell_id, mcp_session_id)
        )
        if call_classification != _PUBLIC_TOOL_CALL_AUTHORIZED:
            if session_wake_pending:
                _queue_session_wake_entry(
                    state,
                    cell_id=cell_id,
                    caller_kind=caller_kind,
                    first_tool_call_ts=first_tool_call_ts,
                )
            if call_classification != _PUBLIC_TOOL_CALL_UNKNOWN:
                is_creator_proposal_mode = has_frozen_platform_task_authority_mode(
                    caller_cell, "creator-proposal-only")
                is_group_board_authority_mode = (
                    has_frozen_platform_group_board_authority(caller_cell))
                if (
                        call_classification
                        == _PUBLIC_TOOL_CALL_TARGET_SCOPE_DENIED
                        and (is_creator_proposal_mode
                             or is_group_board_authority_mode)
                ):
                    scope = (
                        "group" if is_group_board_authority_mode
                        else "creator/self"
                    )
                    return (
                        _jsonrpc_error_with_undeclared_public_argument_notice(
                            req_id,
                            -32003,
                            "Authorization denied: target is outside Product "
                            f"Manager {scope} scope",
                            undeclared_public_arguments,
                        ),
                        200,
                    )
                message = public_call_refusal_message(
                    call_classification,
                    requested_tool_name,
                )
            else:
                message = f"Unknown tool: {requested_tool_name}"
            return _jsonrpc_error_with_undeclared_public_argument_notice(
                req_id,
                -32602,
                message,
                undeclared_public_arguments,
            ), 200
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
                        return _idempotency_conflict_result(
                            req_id,
                            tool_name,
                            undeclared_public_arguments,
                        ), 200
                    try:
                        cached = json.loads(existing.get("response_json", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        cached = {}
                    # Older persisted idempotency rows may predate the
                    # receipt feature. A replay remains acceptance-identical
                    # but must not recreate the silent-parameter trap.
                    cached = _append_undeclared_public_argument_notice(
                        cached,
                        undeclared_public_arguments,
                    )
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
                _jsonrpc_error_with_undeclared_public_argument_notice(
                    req_id,
                    -32602,
                    f"Unknown tool: {tool_name}",
                    undeclared_public_arguments,
                ),
                200,
            )

        # Default: cache successful responses (and most errors) on the
        # idempotency key. Set to False by a dispatcher when the response
        # is a recoverable refusal (e.g. deliverable_missing) that the
        # worker can flip to passing by retrying after side-effects.
        cacheable = True
        dispatch_started_at = time.time()
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
            elif tool_name == "engineer_tool_search":
                result = {
                    "content": [{
                        "type": "text",
                        "text": tool_search_response(
                            _canonical_tools_for_caller(state, cell_id),
                            arguments,
                        ),
                    }],
                    "isError": False,
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
            elif tool_name == "architect_tool_search":
                result = {
                    "content": [{
                        "type": "text",
                        "text": tool_search_response(
                            _canonical_tools_for_caller(state, cell_id),
                            arguments,
                        ),
                    }],
                    "isError": False,
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

        result = _apply_tool_result_scope_filters(
            state,
            tool_name,
            result,
            caller_cell,
        )
        result = _apply_inline_field_authority(result, caller_cell)
        result = _append_undeclared_public_argument_notice(
            result,
            undeclared_public_arguments,
        )

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
        duration_ms = int(max(0.0, (time.time() - dispatch_started_at) * 1000))
        await _notify_mcp_call_observer(
            mcp_call_observer,
            {
                "cell_id": str(cell_id or ""),
                "tool_name": _qualified_mcp_tool_name(tool_name),
                "raw_tool_name": str(tool_name or ""),
                "hook_event_name": "PostToolUse",
                "session_id": str(mcp_session_id or ""),
                "request_id": req_id,
                "idempotency_key": idempotency_key,
                "arguments": arguments,
                "result": result,
                "is_error": bool(result.get("isError")) if isinstance(result, dict) else False,
                "duration_ms": duration_ms,
            },
        )
        return _jsonrpc_ok(req_id, result), 200

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}"), 200


def create_mcp_handler(handle_command, state, *, mcp_call_observer=None):
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
            mcp_call_observer=mcp_call_observer,
        )
        if status == 202:
            return web.Response(status=202)
        return web.json_response(payload, status=status)

    return handle_mcp
