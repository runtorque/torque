"""Canonical public MCP contract over Torque's role-scoped handlers.

The mature MCP handlers still use their historical ``torque_*``,
``engineer_*``, and ``architect_*`` operation names internally.  This module
defines the role-neutral public vocabulary, merges compatible schemas for a
caller, and resolves a canonical call back to the safest eligible internal
operation.

Legacy names remain an internal compatibility boundary only.  They are never
returned from ``tools/list`` or deferred tool search.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import re
from typing import Iterable, Mapping


ROLE_PREFIXES = ("torque_", "engineer_", "architect_")


_SUFFIX_TO_CANONICAL = {
    # Discovery and context.
    "context": "context",
    "tool_search": "tool_search",
    "help_list": "help_search",
    "help_search": "help_search",
    "help_query": "help_query",
    "help_show": "help_get",
    # Human and agent communication.
    "ask": "user_ask",
    "message_user": "user_message",
    "proposal_message_user": "user_message",
    "proposal_ask_user": "user_ask",
    "note": "user_note",
    "stop_user_message_loop": "user_message_loop_stop",
    "peer_list": "peer_list",
    "proposal_peer_list": "peer_list",
    "peer_notify": "peer_message",
    "peer_message": "peer_message",
    "proposal_peer_message": "peer_message",
    "peer_inbox": "peer_inbox",
    "proposal_peer_inbox": "peer_inbox",
    "peer_reply": "peer_reply",
    "proposal_peer_reply": "peer_reply",
    "peer_inspect": "peer_context",
    "agent_message": "agent_message",
    "engineer_message": "agent_message",
    "message_architect": "supervisor_message",
    "reply": "agent_reply",
    "engineer_reply": "agent_reply",
    "engineer_peer_threads": "agent_thread_list",
    "engineer_peer_inspect": "agent_thread_get",
    "engineer_journal_read": "agent_journal_list",
    "engineer_pending_question": "agent_ask_get",
    "engineer_answer": "agent_ask_answer",
    "task_resolve": "agent_ask_answer",
    "engineer_feedback_request": "feedback_request",
    "engineer_feedback_status": "feedback_status",
    # Board and task operations.
    "board_summary": "board_summary",
    "proposal_board_summary": "board_summary",
    "board_list": "task_list",
    "task_list": "task_list",
    "task_proposal_list": "task_list",
    "task_show": "task_get",
    "task_proposal_show": "task_get",
    "task_chain": "task_chain",
    "task_create": "task_create",
    "task_propose": "task_create",
    "task_edit": "task_update",
    "task_update": "task_update",
    "task_pickup": "task_claim",
    "task_move": "task_move",
    "task_reassign": "task_reassign",
    "task_dispatch": "task_dispatch",
    "batch_dispatch": "task_dispatch",
    "task_mark_covered": "task_mark_covered",
    "proposal_root_backlog_hygiene": "task_coverage_reconcile",
    "task_upload_artifact": "task_artifact_upload",
    "task_verify": "task_verify",
    "verify": "task_verify",
    "derive": "task_derive",
    "progress": "task_progress",
    "done": "task_complete",
    "blocked": "task_blocked",
    "error": "task_error",
    "ready": "agent_ready",
    # Agent and action inventory/control.
    "agents_list": "agent_list",
    "engineer_list": "agent_list",
    "agent_show": "agent_get",
    "name": "agent_rename",
    "agent_close": "agent_close",
    "agent_relaunch": "agent_relaunch",
    "launch_settings": "agent_launch_settings",
    "actions_list": "action_list",
    "action_show": "action_get",
    # Planning.
    "area_list": "area_list",
    "proposal_area_list": "area_list",
    "area_show": "area_get",
    "proposal_area_show": "area_get",
    "area_create": "area_create",
    "area_update": "area_update",
    "area_archive": "area_archive",
    "area_link_task": "area_link",
    "area_unlink_task": "area_link",
    "area_link_decision": "area_link",
    "area_unlink_decision": "area_link",
    "area_link_initiative": "area_link",
    "area_unlink_initiative": "area_link",
    "area_link_area": "area_link",
    "area_unlink_area": "area_link",
    "area_note_create": "area_note",
    "area_note_update": "area_note",
    "area_note_archive": "area_note",
    "initiative_list": "initiative_list",
    "proposal_initiative_list": "initiative_list",
    "initiative_show": "initiative_get",
    "proposal_initiative_show": "initiative_get",
    "initiative_create": "initiative_create",
    "initiative_update": "initiative_update",
    "initiative_archive": "initiative_archive",
    "initiative_link_task": "initiative_link",
    "initiative_unlink_task": "initiative_link",
    "initiative_link_decision": "initiative_link",
    "initiative_unlink_decision": "initiative_link",
    "decision_list": "decision_list",
    "decision_proposal_list": "decision_list",
    "decision_get": "decision_get",
    "decision_create": "decision_create",
    "decision_propose": "decision_create",
    "decision_update": "decision_update",
    "decision_proposal_update": "decision_update",
    "decision_link": "decision_link",
    "decision_proposal_link": "decision_link",
    "decision_review": "decision_review",
    # Idea briefs and thinking.
    "idea_brief_list": "idea_brief_list",
    "idea_brief_show": "idea_brief_get",
    "idea_brief_create": "idea_brief_create",
    "idea_brief_update": "idea_brief_update",
    "idea_brief_refine": "idea_brief_update",
    "idea_brief_park": "idea_brief_transition",
    "idea_brief_archive": "idea_brief_transition",
    "idea_brief_propose": "idea_brief_transition",
    "thinking_scratchpad_list": "thinking_list",
    "thinking_mind_map_list": "thinking_list",
    "thinking_scratchpad_show": "thinking_get",
    "thinking_mind_map_show": "thinking_get",
    "thinking_scratchpad_create": "scratchpad_update",
    "thinking_scratchpad_update": "scratchpad_update",
    "thinking_mind_map_create": "mind_map_update",
    "thinking_mind_map_update": "mind_map_update",
    "thinking_mind_map_node_create": "mind_map_node_update",
    "thinking_mind_map_node_update": "mind_map_node_update",
    "thinking_mind_map_node_position": "mind_map_node_update",
    "thinking_mind_map_node_delete": "mind_map_node_update",
    "thinking_mind_map_link_create": "mind_map_link_update",
    "thinking_mind_map_link_update": "mind_map_link_update",
    "thinking_mind_map_link_delete": "mind_map_link_update",
    "thinking_archive": "thinking_archive",
    # Dynamic behavior.
    "behavior_overlay_read": "behavior_overlay_get",
    "behavior_overlay_versions": "behavior_overlay_versions",
    "behavior_overlay_diff": "behavior_overlay_diff",
    "behavior_overlay_proposal_list": "behavior_overlay_proposal_list",
    "behavior_overlay_propose": "behavior_overlay_propose",
    "behavior_overlay_propose_for_engineer": "behavior_overlay_propose",
    "behavior_overlay_propose_for_role": "behavior_overlay_propose",
    "behavior_overlay_approve": "behavior_overlay_review",
    "behavior_overlay_reject": "behavior_overlay_review",
    "behavior_overlay_request_rollback": "behavior_overlay_rollback",
    "behavior_overlay_rollback": "behavior_overlay_rollback",
    "behavior_overlay_rollback_role": "behavior_overlay_rollback",
    # Journal and shared memory.
    "journal": "journal_write",
    "proposal_journal": "journal_write",
    "journal_read": "journal_list",
    "proposal_journal_read": "journal_list",
    "memory_publish": "memory_publish",
    "memory_list": "memory_list",
    "memory_read": "memory_get",
    "memory_pin": "memory_set_pin",
    "memory_unpin": "memory_set_pin",
    "memory_link": "memory_link",
    # Runtime, events, and reports.
    "boot_summary": "boot_summary",
    "session_map": "session_map",
    "hint_snooze": "hint_set_state",
    "semantic_recall": "semantic_recall",
    "streams_list": "stream_list",
    "stream_show": "stream_get",
    "events": "event_list",
    "events_recent": "event_list",
    "notifications": "event_delivery_update",
    "resume": "event_delivery_update",
    "digest_filter": "event_delivery_update",
    "mcp_calls": "telemetry_query",
    "attention_digest": "attention_digest",
    "group_health_brief": "group_health_brief",
    "wave_summary": "wave_summary",
    "completion_audit": "completion_audit",
    # Worktrees.
    "diff": "worktree_diff",
    "worktree_checkpoint": "worktree_checkpoint",
    "rebase": "worktree_rebase",
    "merge": "worktree_merge",
    "create_pr": "worktree_create_pr",
    "worktree_remove": "worktree_remove",
    "worktree_adopt": "worktree_adopt",
    "worktree_advance_boundary": "worktree_advance_boundary",
    # Specializations and Engineer management.
    "specializations_list": "specialization_list",
    "specialization_show": "specialization_get",
    "specialization_save": "specialization_save",
    "specialization_delete": "specialization_delete",
    "engineer_hire": "engineer_hire",
    "pending_hire_list": "hire_list",
    "pending_hire_status": "hire_list",
    "engineer_set_specializations": "engineer_update",
    "engineer_dismiss": "engineer_lifecycle",
    "engineer_rehire": "engineer_lifecycle",
    "engineer_restore": "engineer_lifecycle",
    # Settings and deploy state.
    "deploy_state": "deploy_get",
    "get_architect_settings": "settings_get",
}


CANONICAL_DESCRIPTIONS = {
    "tool_search": "Search deferred tools available to this caller and return matching canonical schemas.",
    "help_search": "Search maintained Torque help. Omit query to browse available help topics.",
    "help_query": "Answer a question from maintained Torque help with source excerpts.",
    "help_get": "Read one maintained Torque help topic.",
    "user_message": "Send a durable, non-blocking message to the owning user conversation.",
    "user_ask": "Ask the owning user a blocking question and pause event delivery until answered.",
    "user_note": "Post a persistent non-blocking note or soft question for the user.",
    "peer_list": "List same-level peers eligible for direct coordination.",
    "peer_message": "Send a message to an eligible same-level peer. Peer eligibility is derived from the caller.",
    "peer_inbox": "Read eligible same-level peer message threads.",
    "peer_reply": "Reply to an existing eligible same-level peer thread.",
    "agent_message": "Send a direct instruction or message to an eligible subordinate agent.",
    "supervisor_message": "Send a direct message to the caller's supervising agent.",
    "agent_reply": "Reply within an existing supervisor/subordinate message thread.",
    "task_dispatch": "Dispatch one task or a batch of tasks to eligible workers.",
    "area_link": "Add or remove a typed link between an Area and another planning resource.",
    "initiative_link": "Add or remove a typed link between an Initiative and a task or decision.",
    "area_note": "Create, update, or archive a typed Area note.",
    "behavior_overlay_propose": "Propose a Dynamic Behavior overlay for an eligible agent or role scope.",
    "behavior_overlay_review": "Approve or reject a visible Dynamic Behavior proposal.",
    "behavior_overlay_rollback": "Request an eligible agent or role overlay rollback.",
    "memory_set_pin": "Pin or unpin a shared-memory entry.",
    "event_delivery_update": "Update event-delivery preferences or resume paused delivery.",
    "engineer_lifecycle": "Dismiss, rehire, or restore an eligible hired Engineer.",
    "hire_list": "List pending hire requests or read one request by id.",
    "thinking_list": "List visible Thinking artifacts of the requested type.",
    "thinking_get": "Read one visible Thinking artifact.",
    "scratchpad_update": "Create or update a caller-owned Scratchpad note.",
    "mind_map_update": "Create or update a caller-owned Mind Map.",
    "mind_map_node_update": "Create, update, move, or delete a node in a caller-owned Mind Map.",
    "mind_map_link_update": "Create, update, or delete a link in a caller-owned Mind Map.",
    "thinking_archive": "Archive a caller-owned Thinking artifact.",
}


_EAGER_BY_KIND = {
    "worker": {
        "context", "area_list", "area_get", "help_search", "help_query", "help_get",
        "task_artifact_upload", "task_complete", "task_blocked", "task_error",
        "task_progress", "task_verify", "agent_ready", "agent_rename",
        "task_derive", "user_ask", "user_message", "user_message_loop_stop",
        "agent_reply", "memory_publish", "memory_list", "memory_get",
        "memory_set_pin", "memory_link",
    },
    "engineer": {
        "context", "tool_search", "help_query", "help_get", "board_summary",
        "boot_summary", "session_map", "task_list", "task_get", "task_create",
        "task_update", "task_move", "task_dispatch", "task_verify",
        "task_artifact_upload", "agent_list", "agent_get", "agent_message",
        "agent_ask_answer", "user_ask", "user_message", "user_note",
        "peer_list", "peer_message", "peer_inbox", "peer_reply",
        "supervisor_message", "agent_reply", "event_list",
    },
    "architect": {
        "context", "tool_search", "help_query", "help_get", "board_summary",
        "boot_summary", "task_list", "task_get", "task_create",
        "task_update", "task_claim", "task_reassign", "task_move",
        "event_list", "agent_list", "agent_message", "agent_reply",
        "user_ask", "user_message", "peer_list", "peer_message",
        "peer_inbox", "peer_reply", "area_list", "area_get",
        "initiative_list", "initiative_get", "decision_list",
        "journal_write", "journal_list",
    },
}


def strip_role_prefix(name: str) -> str:
    text = str(name or "").strip()
    for prefix in ROLE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def canonical_tool_name(name: str) -> str:
    """Return the canonical public name for a legacy or canonical tool."""

    text = str(name or "").strip()
    if not text:
        return ""
    if text in _SUFFIX_TO_CANONICAL.values():
        return text
    suffix = strip_role_prefix(text)
    return _SUFFIX_TO_CANONICAL.get(suffix, suffix)


_ROLE_TOOL_REFERENCE_RE = re.compile(
    r"\b(?:torque|engineer|architect)_"
    + r"(?:"
    + "|".join(
        sorted(
            (re.escape(suffix) for suffix in _SUFFIX_TO_CANONICAL),
            key=len,
            reverse=True,
        )
    )
    + r")\b"
)


def canonicalize_tool_references(text: str) -> str:
    """Rewrite known historical tool references in user-facing guidance.

    Event names and ordinary role-prefixed identifiers are left alone because
    the expression only matches suffixes that belong to registered tools.
    """

    return _ROLE_TOOL_REFERENCE_RE.sub(
        lambda match: canonical_tool_name(match.group(0)),
        str(text or ""),
    )


def _canonicalize_schema_text(value):
    """Rewrite historical tool references anywhere in a public schema."""

    if isinstance(value, str):
        return canonicalize_tool_references(value)
    if isinstance(value, list):
        return [_canonicalize_schema_text(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _canonicalize_schema_text(item)
            for key, item in value.items()
        }
    return value


def _merge_property(existing: dict | None, incoming: Mapping) -> dict:
    if not existing:
        return deepcopy(dict(incoming))
    merged = deepcopy(existing)
    incoming = dict(incoming)
    if merged.get("type") == incoming.get("type") == "array":
        if "items" not in merged and "items" in incoming:
            merged["items"] = deepcopy(incoming["items"])
    if "enum" in incoming:
        values = list(merged.get("enum", []) or [])
        for value in incoming.get("enum", []) or []:
            if value not in values:
                values.append(value)
        if values:
            merged["enum"] = values
    if not merged.get("description") and incoming.get("description"):
        merged["description"] = incoming["description"]
    return merged


def _merged_schema(specs: list[dict]) -> dict:
    properties: OrderedDict[str, dict] = OrderedDict()
    required_sets: list[set[str]] = []
    for spec in specs:
        schema = dict(spec.get("inputSchema") or {})
        for name, prop in dict(schema.get("properties") or {}).items():
            properties[name] = _merge_property(properties.get(name), prop)
        required_sets.append(set(schema.get("required") or []))
    required = set.intersection(*required_sets) if required_sets else set()
    schema = {"type": "object", "properties": dict(properties)}
    if required:
        schema["required"] = sorted(required)
    return schema


def _canonical_schema(name: str, schema: dict, *, caller_kind: str) -> dict:
    """Apply concise canonical arguments to operations that merged shapes."""

    props = dict(schema.get("properties") or {})
    required = list(schema.get("required") or [])
    one_of: list[dict] = []

    def add(key: str, value: dict) -> None:
        props[key] = value

    if name == "peer_message":
        for legacy in ("architect_id", "engineer_id"):
            props.pop(legacy, None)
        add("peer", {"type": "string", "description": "Eligible peer id, slug, or name."})
        required = ["peer", "message"]
    elif name == "peer_inbox":
        props.pop("peer_architect_id", None)
        props.pop("peer_engineer_id", None)
        add("peer", {"type": "string", "description": "Optional peer filter."})
    elif name == "agent_message":
        props.pop("engineer_id", None)
        add("agent", {"type": "string", "description": "Eligible subordinate agent id, slug, or name."})
        required = ["agent", "message"]
    elif name == "supervisor_message":
        props.pop("architect_id", None)
        add("supervisor", {"type": "string", "description": "Optional supervising agent reference; defaults to the caller's supervisor."})
        required = ["message"]
    elif name == "help_search":
        if "query" in props:
            props["query"] = {
                **props["query"],
                "minLength": 1,
            }
        one_of = [
            {
                "properties": {"audience": {}},
                "not": {
                    "anyOf": [
                        {"required": ["query"]},
                        {"required": ["limit"]},
                    ],
                },
                "additionalProperties": False,
            },
            {
                "properties": {"query": {}, "limit": {}},
                "required": ["query"],
                "not": {"required": ["audience"]},
                "additionalProperties": False,
            },
        ]
    elif name == "task_dispatch":
        props.pop("tasks", None)
        props.pop("agent_type", None)
        add(
            "entries",
            {
                "type": "array",
                "description": "Optional ordered batch. Each entry requires task and may include agent_group.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "agent_group": {"type": "string"},
                    },
                    "required": ["task"],
                },
            },
        )
        required = []
        single_keys = set(props) - {"entries", "max_concurrent"}
        batch_keys = {"entries", "max_concurrent", "provider"} & set(props)
        one_of = [
            {
                "properties": {key: {} for key in sorted(single_keys)},
                "required": ["task"],
                "not": {"required": ["entries"]},
                "additionalProperties": False,
            },
            {
                "properties": {key: {} for key in sorted(batch_keys)},
                "required": ["entries"],
                "not": {"required": ["task"]},
                "additionalProperties": False,
            },
        ]
    elif name == "task_artifact_upload":
        props.pop("task_id", None)
        if "task" in props:
            required = ["task"]
    elif name == "task_mark_covered":
        props.pop("covering_task_id", None)
    elif name == "area_get":
        required = ["area"]
    elif name == "initiative_get":
        required = ["initiative"]
    elif name == "area_link":
        props = {
            "area": {"type": "string"},
            "operation": {"type": "string", "enum": ["add", "remove"]},
            "target_kind": {
                "type": "string",
                "enum": ["task", "decision", "initiative", "area"],
            },
            "target": {"type": "string"},
            "relation": {
                "type": "string",
                "enum": ["related", "depends_on", "supports"],
            },
        }
        required = ["area", "operation", "target_kind", "target"]
    elif name == "initiative_link":
        props = {
            "initiative": {"type": "string"},
            "operation": {"type": "string", "enum": ["add", "remove"]},
            "target_kind": {"type": "string", "enum": ["task", "decision"]},
            "target": {"type": "string"},
        }
        required = ["initiative", "operation", "target_kind", "target"]
    elif name == "area_note":
        for legacy in ("area_id", "note_id"):
            props.pop(legacy, None)
        add("operation", {"type": "string", "enum": ["create", "update", "archive"]})
        required = ["area", "operation"]
        one_of = [
            {
                "properties": {"operation": {"const": "create"}},
                "required": ["note_type", "title"],
            },
            {
                "properties": {"operation": {"enum": ["update", "archive"]}},
                "required": ["note"],
            },
        ]
    elif name == "behavior_overlay_review":
        add("decision", {"type": "string", "enum": ["approve", "reject"]})
        required = ["proposal_id", "decision"]
    elif name == "behavior_overlay_propose":
        if caller_kind == "architect":
            for legacy in ("agent_id", "engineer_id", "role_kind"):
                props.pop(legacy, None)
            add("target_kind", {"type": "string", "enum": ["self", "agent", "role"]})
            add("target", {"type": "string", "description": "Agent reference or role kind for non-self targets."})
            required = [*required, "target_kind"]
            one_of = [
                {
                    "properties": {"target_kind": {"const": "self"}},
                    "not": {"required": ["target"]},
                },
                {
                    "properties": {"target_kind": {"enum": ["agent", "role"]}},
                    "required": ["target"],
                },
            ]
    elif name == "behavior_overlay_rollback":
        if caller_kind == "architect":
            for legacy in ("agent_id", "engineer_id", "role_kind"):
                props.pop(legacy, None)
            add("target_kind", {"type": "string", "enum": ["self", "agent", "role"]})
            add("target", {"type": "string", "description": "Agent reference or role kind for non-self targets."})
            required = [*required, "target_kind"]
            one_of = [
                {
                    "properties": {"target_kind": {"const": "self"}},
                    "not": {"required": ["target"]},
                },
                {
                    "properties": {"target_kind": {"enum": ["agent", "role"]}},
                    "required": ["target"],
                },
            ]
    elif name == "memory_set_pin":
        add("pinned", {"type": "boolean", "description": "True to pin; false to unpin."})
        required = ["entry_id", "pinned"]
    elif name == "event_delivery_update":
        operations = ["configure", "resume"] if caller_kind == "engineer" else ["configure"]
        add("operation", {
            "type": "string",
            "enum": operations,
            "description": "Configure delivery preferences or resume paused delivery.",
        })
        required = ["operation"]
        if caller_kind == "engineer":
            configure_keys = set(props)
            one_of = [
                {
                    "properties": {
                        **{key: {} for key in sorted(configure_keys)},
                        "operation": {"const": "configure"},
                    },
                    "required": ["operation"],
                    "additionalProperties": False,
                },
                {
                    "properties": {"operation": {"const": "resume"}},
                    "required": ["operation"],
                    "additionalProperties": False,
                },
            ]
    elif name == "engineer_lifecycle":
        add("operation", {"type": "string", "enum": ["dismiss", "rehire", "restore"]})
        required = ["engineer_id", "operation"]
        one_of = [
            {
                "properties": {
                    "engineer_id": {},
                    "operation": {"const": "dismiss"},
                    "reason": {},
                },
                "required": ["engineer_id", "operation"],
                "additionalProperties": False,
            },
            {
                "properties": {
                    "engineer_id": {},
                    "operation": {"enum": ["rehire", "restore"]},
                },
                "required": ["engineer_id", "operation"],
                "additionalProperties": False,
            },
        ]
    elif name == "hire_list":
        required = []
        one_of = [
            {
                "properties": {"hire_id": {}},
                "required": ["hire_id"],
                "additionalProperties": False,
            },
            {
                "properties": {"status_filter": {}},
                "not": {"required": ["hire_id"]},
                "additionalProperties": False,
            },
        ]
    elif name == "idea_brief_transition":
        for legacy in ("brief_id", "id"):
            props.pop(legacy, None)
        add("transition", {"type": "string", "enum": ["propose", "park", "archive"]})
        required = ["idea_brief", "transition"]
    elif name == "idea_brief_get":
        for legacy in ("brief_id", "id"):
            props.pop(legacy, None)
        required = ["idea_brief"]
    elif name == "idea_brief_update":
        for legacy in ("brief_id", "id"):
            props.pop(legacy, None)
        add("operation", {"type": "string", "enum": ["update", "refine"]})
        required = ["idea_brief", "operation"]
        one_of = [
            {
                "properties": {"operation": {"const": "refine"}},
                "required": ["refinement_note"],
            },
            {
                "properties": {"operation": {"const": "update"}},
                "not": {"required": ["refinement_note"]},
            },
        ]
    elif name == "thinking_list":
        add("artifact_type", {"type": "string", "enum": ["scratchpad", "mind_map"]})
        required = ["artifact_type"]
    elif name == "thinking_get":
        for legacy in ("note", "note_id", "id", "mind_map", "map_id"):
            props.pop(legacy, None)
        add("artifact_type", {"type": "string", "enum": ["scratchpad", "mind_map"]})
        add("artifact", {"type": "string"})
        required = ["artifact_type", "artifact"]
    elif name == "scratchpad_update":
        for legacy in ("note", "note_id", "id"):
            props.pop(legacy, None)
        add("scratchpad", {"type": "string", "description": "Scratchpad id or slug for update."})
        add("operation", {"type": "string", "enum": ["create", "update"]})
        required = ["operation"]
        one_of = [
            {
                "properties": {"operation": {"const": "create"}},
                "required": ["title"],
                "not": {"required": ["scratchpad"]},
            },
            {
                "properties": {"operation": {"const": "update"}},
                "required": ["scratchpad"],
            },
        ]
    elif name == "mind_map_update":
        for legacy in ("map_id", "id"):
            props.pop(legacy, None)
        add("operation", {"type": "string", "enum": ["create", "update"]})
        required = ["operation"]
        one_of = [
            {
                "properties": {"operation": {"const": "create"}},
                "required": ["title"],
                "not": {"required": ["mind_map"]},
            },
            {
                "properties": {"operation": {"const": "update"}},
                "required": ["mind_map"],
            },
        ]
    elif name == "mind_map_node_update":
        for legacy in ("map_id", "node_id", "id"):
            props.pop(legacy, None)
        add("operation", {"type": "string", "enum": ["create", "update", "move", "delete"]})
        required = ["mind_map", "operation"]
        one_of = [
            {
                "properties": {"operation": {"const": "create"}},
                "required": ["label"],
                "not": {"required": ["node"]},
            },
            {
                "properties": {"operation": {"enum": ["update", "move", "delete"]}},
                "required": ["node"],
            },
        ]
    elif name == "mind_map_link_update":
        for legacy in ("map_id", "link_id", "id", "source_node_id", "target_node_id"):
            props.pop(legacy, None)
        add("operation", {"type": "string", "enum": ["create", "update", "delete"]})
        required = ["mind_map", "operation"]
        one_of = [
            {
                "properties": {"operation": {"const": "create"}},
                "required": ["source", "target"],
                "not": {"required": ["link"]},
            },
            {
                "properties": {"operation": {"enum": ["update", "delete"]}},
                "required": ["link"],
            },
        ]
    elif name == "thinking_archive":
        add("artifact_type", {"type": "string", "enum": ["scratchpad", "mind_map"]})
        add("artifact", {"type": "string"})
        required = ["artifact_type", "artifact"]

    for canonical, aliases in (
        ("area", ("area_id",)),
        ("initiative", ("initiative_id",)),
    ):
        if canonical in props:
            for alias in aliases:
                props.pop(alias, None)

    result = {"type": "object", "properties": props}
    if required:
        result["required"] = list(dict.fromkeys(required))
    if one_of:
        result["oneOf"] = one_of
    return result


def canonicalize_tool_specs(
    tools: Iterable[dict],
    *,
    caller_kind: str,
    proposal_only: bool = False,
) -> list[dict]:
    """Merge authority-filtered legacy specs into one canonical caller surface."""

    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for tool in tools:
        name = canonical_tool_name(tool.get("name", ""))
        if not name:
            continue
        grouped.setdefault(name, []).append(tool)

    eager = _EAGER_BY_KIND.get(str(caller_kind or "").strip(), set())
    result = []
    for name, variants in grouped.items():
        proposal_variants = [
            variant for variant in variants
            if _is_proposal_variant(variant.get("name", ""))
        ]
        ordinary_variants = [
            variant for variant in variants
            if not _is_proposal_variant(variant.get("name", ""))
        ]
        if proposal_variants and ordinary_variants:
            variants = (
                proposal_variants if proposal_only else ordinary_variants
            )
        spec = {
            "name": name,
            "description": canonicalize_tool_references(
                CANONICAL_DESCRIPTIONS.get(
                    name,
                    str(variants[0].get("description", "") or ""),
                ),
            ),
            "inputSchema": _canonicalize_schema_text(
                _canonical_schema(
                    name,
                    _merged_schema(variants),
                    caller_kind=caller_kind,
                ),
            ),
            "_legacy_names": [
                str(variant.get("name", "") or "").strip()
                for variant in variants
            ],
        }
        if name not in eager:
            spec["deferred"] = True
        result.append(spec)
    return result


def _is_proposal_variant(name: str) -> bool:
    suffix = strip_role_prefix(name)
    return (
        "proposal" in suffix
        or suffix in {"task_propose", "decision_propose"}
    )


def authority_is_proposal_only(authority) -> bool:
    if authority is None:
        return False
    proposes = authority.has("task.propose") or authority.has("decision.propose")
    executes = (
        authority.has("task.create")
        or authority.has("decision.create")
        or authority.has("decision.update")
    )
    return proposes and not executes


def _candidate_with_suffix(
    candidates: list[str],
    suffix: str,
    *,
    caller_kind: str = "",
) -> str:
    matches = [name for name in candidates if name.endswith(suffix)]
    if caller_kind:
        preferred = next(
            (
                name for name in matches
                if name.startswith(f"{caller_kind}_")
            ),
            "",
        )
        if preferred:
            return preferred
    return matches[0] if matches else ""


def select_legacy_tool(
    canonical_name: str,
    candidates: Iterable[str],
    args: Mapping | None,
    *,
    caller_kind: str,
    authority=None,
) -> str:
    """Select the internal operation implementing one canonical call."""

    canonical_name = canonical_tool_name(canonical_name)
    candidates = [str(name or "").strip() for name in candidates if name]
    args = dict(args or {})
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    operation = str(
        args.get("operation", args.get("transition", args.get("decision", "")))
        or ""
    ).strip().lower()
    target_kind = str(args.get("target_kind", "") or "").strip().lower()

    suffix = ""
    if canonical_name == "help_search":
        suffix = "help_list" if not str(args.get("query", "") or "").strip() else "help_search"
    elif canonical_name == "task_dispatch":
        suffix = "batch_dispatch" if "entries" in args or "tasks" in args else "task_dispatch"
    elif canonical_name == "area_link" and target_kind:
        suffix = f"area_{'unlink' if operation == 'remove' else 'link'}_{target_kind}"
    elif canonical_name == "initiative_link" and target_kind:
        suffix = f"initiative_{'unlink' if operation == 'remove' else 'link'}_{target_kind}"
    elif canonical_name == "area_note" and operation:
        suffix = f"area_note_{operation}"
    elif canonical_name == "behavior_overlay_review" and operation:
        suffix = f"behavior_overlay_{operation}"
    elif canonical_name == "behavior_overlay_propose":
        if target_kind == "role":
            suffix = "behavior_overlay_propose_for_role"
        elif target_kind == "agent" and caller_kind == "architect":
            suffix = "behavior_overlay_propose_for_engineer"
        else:
            suffix = "behavior_overlay_propose"
    elif canonical_name == "behavior_overlay_rollback":
        if target_kind == "role":
            suffix = "behavior_overlay_rollback_role"
        elif caller_kind == "engineer":
            suffix = "behavior_overlay_request_rollback"
        else:
            suffix = "behavior_overlay_rollback"
    elif canonical_name == "memory_set_pin":
        suffix = "memory_pin" if bool(args.get("pinned", True)) else "memory_unpin"
    elif canonical_name == "event_delivery_update":
        if operation == "resume":
            suffix = "resume"
        elif caller_kind == "architect":
            suffix = "digest_filter"
        else:
            suffix = "notifications"
    elif canonical_name == "engineer_lifecycle" and operation:
        suffix = f"engineer_{operation}"
    elif canonical_name == "hire_list":
        suffix = "pending_hire_status" if str(args.get("hire_id", "") or "").strip() else "pending_hire_list"
    elif canonical_name == "idea_brief_transition" and operation:
        suffix = f"idea_brief_{operation}"
    elif canonical_name == "idea_brief_update":
        suffix = (
            "idea_brief_refine"
            if operation == "refine" or "refinement_note" in args
            else "idea_brief_update"
        )
    elif canonical_name == "thinking_list":
        suffix = (
            "thinking_mind_map_list"
            if args.get("artifact_type") == "mind_map"
            else "thinking_scratchpad_list"
        )
    elif canonical_name == "thinking_get":
        suffix = (
            "thinking_mind_map_show"
            if args.get("artifact_type") == "mind_map"
            else "thinking_scratchpad_show"
        )
    elif canonical_name == "scratchpad_update":
        suffix = (
            "thinking_scratchpad_create"
            if operation == "create" or not any(args.get(k) for k in ("scratchpad", "note_id", "id"))
            else "thinking_scratchpad_update"
        )
    elif canonical_name == "mind_map_update":
        suffix = (
            "thinking_mind_map_create"
            if operation == "create" or not any(args.get(k) for k in ("mind_map", "map_id", "id"))
            else "thinking_mind_map_update"
        )
    elif canonical_name == "mind_map_node_update":
        if operation == "delete":
            suffix = "thinking_mind_map_node_delete"
        elif operation == "move":
            suffix = "thinking_mind_map_node_position"
        elif operation == "create" or not any(args.get(k) for k in ("node", "node_id")):
            suffix = "thinking_mind_map_node_create"
        else:
            suffix = "thinking_mind_map_node_update"
    elif canonical_name == "mind_map_link_update":
        if operation == "delete":
            suffix = "thinking_mind_map_link_delete"
        elif operation == "create" or not any(args.get(k) for k in ("link", "link_id")):
            suffix = "thinking_mind_map_link_create"
        else:
            suffix = "thinking_mind_map_link_update"

    if suffix:
        selected = _candidate_with_suffix(
            candidates,
            suffix,
            caller_kind=caller_kind,
        )
        if selected:
            return selected

    if authority_is_proposal_only(authority):
        proposal = next(
            (
                name for name in candidates
                if "proposal" in strip_role_prefix(name)
            ),
            "",
        )
        if proposal:
            return proposal

    # Prefer the caller-specific implementation over the shared worker
    # implementation because it carries the richer caller-aware semantics.
    prefix = f"{caller_kind}_"
    specific = next((name for name in candidates if name.startswith(prefix)), "")
    return specific or candidates[0]


def translate_canonical_arguments(
    canonical_name: str,
    legacy_name: str,
    args: Mapping | None,
    *,
    caller_kind: str,
) -> dict:
    """Translate concise canonical arguments to an internal handler schema."""

    canonical_name = canonical_tool_name(canonical_name)
    translated = dict(args or {})
    operation = str(
        translated.get(
            "operation",
            translated.get("transition", translated.get("decision", "")),
        )
        or ""
    ).strip().lower()
    if canonical_name in {
        "area_link",
        "initiative_link",
        "area_note",
        "event_delivery_update",
        "engineer_lifecycle",
        "scratchpad_update",
        "mind_map_update",
        "mind_map_node_update",
        "mind_map_link_update",
    }:
        translated.pop("operation", None)
    if canonical_name == "idea_brief_update":
        translated.pop("operation", None)
    if canonical_name == "idea_brief_transition":
        translated.pop("transition", None)
    if canonical_name == "behavior_overlay_review":
        translated.pop("decision", None)
    target_kind = str(translated.pop("target_kind", "") or "").strip()

    if canonical_name == "help_search":
        if legacy_name.endswith("help_list"):
            translated.pop("query", None)
            translated.pop("limit", None)
        else:
            translated.pop("audience", None)
    elif canonical_name == "peer_message":
        peer = translated.pop("peer", "")
        translated[
            "architect_id" if caller_kind == "architect" else "engineer_id"
        ] = peer
    elif canonical_name == "peer_inbox":
        peer = translated.pop("peer", "")
        if peer:
            translated[
                "peer_architect_id"
                if caller_kind == "architect"
                else "peer_engineer_id"
            ] = peer
    elif canonical_name == "agent_message" and caller_kind == "architect":
        translated["engineer_id"] = translated.pop("agent", "")
    elif canonical_name == "supervisor_message":
        supervisor = translated.pop("supervisor", "")
        if supervisor:
            translated["architect_id"] = supervisor
    elif canonical_name == "task_dispatch" and legacy_name.endswith("batch_dispatch"):
        entries = translated.pop("entries", None)
        if entries is not None:
            translated["tasks"] = entries
    elif canonical_name == "area_link":
        target = translated.pop("target", "")
        if target_kind == "area":
            translated["target_area"] = target
        elif target_kind:
            translated[target_kind] = target
            translated.pop("relation", None)
    elif canonical_name == "initiative_link":
        target = translated.pop("target", "")
        if target_kind:
            translated[target_kind] = target
    elif canonical_name == "memory_set_pin":
        translated.pop("pinned", None)
    elif canonical_name == "event_delivery_update":
        if legacy_name.endswith("resume"):
            translated.clear()
    elif canonical_name == "engineer_lifecycle":
        if operation != "dismiss":
            translated.pop("reason", None)
    elif canonical_name == "hire_list":
        if legacy_name.endswith("pending_hire_status"):
            translated.pop("status_filter", None)
        else:
            translated.pop("hire_id", None)
    elif canonical_name == "thinking_get":
        artifact = translated.pop("artifact", "")
        translated.pop("artifact_type", None)
        if legacy_name.endswith("mind_map_show"):
            translated["mind_map"] = artifact
        else:
            translated["note"] = artifact
    elif canonical_name == "thinking_list":
        translated.pop("artifact_type", None)
    elif canonical_name == "scratchpad_update":
        scratchpad = translated.pop("scratchpad", "")
        if scratchpad:
            translated["note"] = scratchpad
    elif canonical_name == "thinking_archive":
        artifact = translated.pop("artifact", "")
        artifact_type = translated.pop("artifact_type", "")
        if artifact_type == "mind_map":
            translated["mind_map"] = artifact
        else:
            translated["note"] = artifact
    elif canonical_name == "behavior_overlay_propose":
        target = translated.pop("target", "")
        if target_kind == "agent":
            translated["engineer_id"] = target
        elif target_kind == "role":
            translated["role_kind"] = target
    elif canonical_name == "behavior_overlay_review":
        if legacy_name.endswith("behavior_overlay_reject"):
            translated.pop("expected_proposed_text_sha256", None)
    elif canonical_name == "behavior_overlay_rollback":
        target = translated.pop("target", "")
        if target_kind == "agent":
            translated["agent_id"] = target
        elif target_kind == "role":
            translated["role_kind"] = target

    return translated


def modernize_tool_authority(tool: Mapping, *, caller_kind: str) -> dict:
    """Return an internal spec using relationship-based capabilities.

    Handler names remain legacy during the migration, but their authority
    metadata must describe the canonical relationship rather than a concrete
    recipient kind.
    """

    spec = deepcopy(dict(tool or {}))
    name = str(spec.get("name", "") or "").strip()
    authority = dict(spec.get("authority") or {})
    requirements = []
    for raw in list(authority.get("requirements") or []):
        requirement = deepcopy(dict(raw or {}))
        capability = str(requirement.get("capability", "") or "").strip()
        if capability == "message.architect_peer":
            requirement["capability"] = "message.peer"
        elif capability == "message.worker":
            requirement["capability"] = "message.subordinate"
        elif capability == "message.engineer":
            if caller_kind == "architect":
                requirement["capability"] = "message.subordinate"
            elif any(
                marker in name
                for marker in (
                    "peer_list",
                    "peer_inbox",
                    "peer_inspect",
                    "peer_notify",
                    "peer_reply",
                )
            ):
                requirement["capability"] = "message.peer"
            else:
                requirement["capability"] = "message.supervisor"
                # Supervisor identity is resolved and enforced by the handler.
                requirement.pop("target_argument", None)
                requirement.pop("target_kind", None)
                requirement["minimum_scope"] = "self"
                requirement["handler_scoped"] = True
        elif capability == "message.ack_required":
            requirement["capability"] = "message.ack_request"
        elif capability == "settings.admin" and name.endswith(
            "get_architect_settings"
        ):
            requirement["capability"] = "settings.read"
        requirements.append(requirement)
    authority["requirements"] = requirements
    spec["authority"] = authority
    return spec
