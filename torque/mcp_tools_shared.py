"""Shared MCP tool implementation for engineer-scoped orchestration tools.

This module contains the shared read/write tool logic used by the
``engineer_*`` and ``architect_*`` namespaces. Scoping is caller-driven
via ``caller_kind`` + ``caller_id``.

Security note: v1 keeps Torque's local-trust model. Environment/header
spoofing protections are out of scope for this stage. The server HTTP
command surface is user-operated and trusted; cross-kind communication
graph enforcement lives in these MCP tool surfaces.
"""

import copy
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone

from .behavior_overlay import (
    BEHAVIOR_OVERLAY_ROLE_KINDS,
    BehaviorOverlayScope,
    proposal_summary,
    version_summary,
)
from .config import log
from .db import canonical_user_agent_thread_id
from .deploy_state import architect_deploy_state_payload
from .dispatch_registry import AsyncHandlerRegistry
from .digest_routing import resolve_digest_recipients
from .direct_message_mirrors import save_direct_ask_mirror
from .idea_briefs import (
    IDEA_BRIEF_PROPOSAL_SCOPE,
    idea_brief_contract_metadata,
    idea_brief_is_archived,
)
from .ai_recall import normalize_recall_limit, semantic_recall_payload
from .mcp_retry import (
    derive_idempotency_key,
    is_mcp_pr_phase,
    is_mcp_pr_phase_retryable,
)
from .help_docs import dispatch_help_tool
from .mcp_engineer_tools.shared import (
    active_worker_ids as _active_worker_ids,
    blocked_dependency_titles as _blocked_dependency_titles,
    format_worktree_conflicts as _format_worktree_conflicts,
    resolve_agent as _resolve_agent,
    resolve_task as _resolve_task,
    run_worktree_merge_check as _run_worktree_merge_check,
    run_worktree_merge_check_with_options as _run_worktree_merge_check_with_options,
    worktree_boundary_overview as _worktree_boundary_overview,
    is_busy_agent as _is_busy_agent,
)
from .server_artifacts import serialize_task_for_mcp
from .server_prompts import build_engineer_deliverable_awareness
from .group_health_brief import build_group_health_brief
from .identity import prepend_agent_identity_anchor
from .state import (
    ARCHITECT_MANDATORY_EVENTS,
    ARCHIVED_LANE,
    board_task_is_closed,
    get_engineer_notification_preset,
    normalize_architect_enabled_events,
    normalize_default_worker_concurrency,
    normalize_engineer_digest_verbosity,
    task_counts_as_done,
    task_is_engineer_message_followup,
)
from .task_health import HEALTH_SEVERITY
from .engineer_hints import compute_engineer_hints
from .engineer_session_map import build_engineer_session_map
from .execution_scope import (
    engineer_architect_close_denied_message,
    engineer_architect_task_routing_denied_message,
    is_architect_execution_target,
)
from .worktree_streams import (
    compute_worktree_streams,
    member_task_ids_for_stream,
    merge_report_snippet_from_merge_result,
    prefill_merge_readiness_for_state,
)
from .worktree_boundaries import latest_boundary_task, task_boundary
from .mcp_scoped.common import (
    _ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT,
    _TASK_DISPATCH_LAUNCH_OVERRIDE_ARGS,
    engineer_not_found_error,
    normalize_tool_name,
    _optional_bool_arg,
    _record_engineer_dispatch_shape,
    _engineer_dispatch_shape_summary,
    _has_task_dispatch_launch_overrides,
    _batch_dispatch_shape,
    tool_name_with_prefix,
    _effective_owner_engineer_id,
    _effective_assigned_engineer_id,
    _task_created_by_classifier,
    _summary_task_title,
    _task_board_sync_inline_state,
    _attach_task_board_sync_inline_state,
    _task_review_inline_state,
    _attach_task_review_inline_state,
    _is_engineer_like_cell,
    _is_architect_cell,
    _agent_is_tombstoned,
    _resolve_agent_including_tombstoned,
    _agent_dismissed_at,
    _engineer_dismissed_error,
    _architect_dismissed_error,
    _caller_group,
    _dedupe_strings,
    _architect_visible_engineers,
    _architect_hired_engineer_ids,
    _resolve_architect_engineer,
    _resolve_architect_hired_engineer,
    _resolve_behavior_overlay_architect_target,
    _behavior_overlay_visible_to_architect,
    _behavior_role_scope_for_caller,
    _behavior_scope_from_mcp_args,
    _resolve_group_engineer,
    _event_task_chain,
    _visible_agent_ids_for_caller,
    _filter_agents_for_caller,
    _filter_tasks_for_caller,
    _state_agent_visible_to_caller,
    _resolve_visible_agent,
    _tombstoned_merge_target_visible_to_caller,
    _worktree_path_args,
    _has_path_target_args,
    _validate_exactly_one_worktree_target,
    _engineer_can_access_worktree_branch,
    _driverless_payload_from_args,
    authorize_caller,
    build_scoped_state_view,
    _agent_peer_message_row_to_entry,
    _load_architect_decision,
    _peer_row_context,
    _thread_requires_architect_reply,
)
from .mcp_scoped.architect_reports import (
    _ARCHITECT_ATTENTION_DEFAULT_LIMIT,
    _ARCHITECT_ATTENTION_MAX_LIMIT,
    _ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT,
    _ARCHITECT_BOARD_SUMMARY_TASK_LIMIT,
    _ARCHITECT_COMPLETION_AUDIT_DEFAULT_LIMIT,
    _ARCHITECT_COMPLETION_AUDIT_MAX_LIMIT,
    _ARCHITECT_COMPLETION_AUDIT_RESPONSE_LIMIT,
    _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    _ARCHITECT_TASK_LIST_DEFAULT_LIMIT,
    _ARCHITECT_WAVE_SUMMARY_DEFAULT_LIMIT,
    _ARCHITECT_WAVE_SUMMARY_MAX_LIMIT,
    _ARCHITECT_WAVE_SUMMARY_RESPONSE_LIMIT,
    _architect_attention_digest_json,
    _architect_attention_peer_ack_items,
    _architect_attention_stream_item,
    _architect_attention_task_item,
    _architect_board_summary_json,
    _architect_board_summary_task_item,
    _architect_completion_audit_json,
    _architect_peer_ack_candidates,
    _architect_task_creator_filter_matches,
    _architect_task_list_sort_key,
    _architect_wave_scope_from_args,
    _architect_wave_summary_json,
    _board_sync_summary_payload,
    _bounded_items,
    _compact_json,
    _completion_audit_branch_sections,
    _completion_audit_engineer_questions,
    _completion_audit_peer_ack_items,
    _completion_audit_scope_engineer_ids,
    _completion_audit_task_gate_reasons,
    _completion_audit_task_item,
    _completion_audit_verification_caveats,
    _normalize_architect_attention_limit,
    _normalize_architect_completion_audit_limit,
    _normalize_architect_task_list_label_filter,
    _normalize_architect_task_list_limit,
    _normalize_architect_wave_summary_limit,
    _stream_owned_by_hired_engineer,
    _stream_owner_engineer_ids,
    _stream_recommended_next_action,
    _stream_stale_base,
    _stream_unhealthy_task_items,
    _task_parked_or_deferred,
    _validate_architect_task_creator_filter,
    _validate_suggested_action,
    _validate_task_update_action_name,
    _wave_summary_bool_known,
    _wave_summary_category,
    _wave_summary_collect_task_ids,
    _wave_summary_evidence,
    _wave_summary_group_completed,
    _wave_summary_known,
    _wave_summary_latest_message,
    _wave_summary_merge_and_boundary,
    _wave_summary_missing_evidence_count,
    _wave_summary_review,
    _wave_summary_task_base,
    _wave_summary_task_item,
    _wave_summary_task_sort_key,
    _wave_summary_text,
    _wave_summary_unknown,
    _wave_summary_verification,
)
from .mcp_scoped.architect_activity import (
    _ARCHITECT_EVENTS_RECENT_DEFAULT_LIMIT,
    _ARCHITECT_EVENTS_RECENT_LOAD_LIMIT,
    _ARCHITECT_EVENTS_RECENT_MAX_LIMIT,
    _ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT,
    _ARCHITECT_EVENTS_RECENT_RESPONSE_LIMIT,
    _ARCHITECT_TASK_CHAIN_NODE_LIMIT,
    _architect_engineer_pending_question_json,
    _architect_event_visible,
    _architect_events_recent_json,
    _architect_peer_message_event,
    _architect_peer_message_summary,
    _architect_task_chain_json,
    _architect_task_chain_root_visible,
    _build_task_chain_tree,
    _clip_event_message,
    _collect_task_chain_tasks,
    _event_agent_kind,
    _event_attribution,
    _event_involves_engineer,
    _load_recent_architect_peer_rows,
    _load_recent_panel_events,
    _normalize_architect_events_limit,
    _normalize_since,
    _peer_row_created_at,
    _peer_row_involves_engineer,
    _peer_thread_pending_reply_info,
    _recent_architect_peer_message_events,
    _resolve_architect_pending_question_engineer,
    _resolve_task_chain_root,
    _task_chain_depth,
    _task_chain_sort_key,
)
from .mcp_scoped.planning import (
    _normalize_decision_links,
    _initiative_visible_task_ids_for_caller,
    _initiative_visible_decision_ids_for_caller,
    _initiative_scope_group,
    _initiative_from_args,
    _architect_can_write_initiative,
    _initiative_read_json,
    _initiative_task_link_target,
    _initiative_decision_link_target,
    _area_scope_group,
    _area_from_args,
    _architect_can_write_area,
    _area_read_json,
    _area_task_link_target,
    _area_decision_link_target,
    _area_initiative_link_target,
    _area_area_link_target,
    _area_note_target_error,
)
from .mcp_scoped.health import (
    _HEALTH_SUMMARY_LIMIT,
    _HEALTH_SUMMARY_SILENT_AFTER_SECS,
    _STREAM_STATES,
    _agent_health_payload_for_response,
    _agent_visible_to_engineer,
    _clip_health_fragment,
    _engineer_streams,
    _format_health_clock,
    _format_health_duration,
    _fresh_health_details,
    _health_summary,
    _limit_health_summary,
    _parse_health_timestamp,
    _resolve_stream_payload,
    _safe_int,
    _source_agent_for_health,
    _source_task_for_health,
    _stream_payload_for_engineer,
    _stream_state_counts,
    _task_agent_payload_for_engineer,
    _task_health_payload_for_response,
)
from .mcp_scoped.worktree_tools import (
    _worktree_result_pr_url,
    _worktree_result_phase,
    _worktree_result_error,
    _format_worktree_pr_error,
    _worktree_merge_branch_base,
    _worktree_merge_default_message,
    _worktree_merge_success_payload,
)
from .mcp_scoped.peer_context import (
    _load_architect_pending_hire,
    _resolve_architect_for_engineer,
    _resolve_architect_peer,
    _resolve_architect_peer_filter,
    _architect_current_task_summary,
    _architect_peer_item,
    _architect_peer_list_json,
    _engineer_peer_hiring_architect_id,
    _engineer_peer_item,
    _engineer_peer_eligibility_error,
    _resolve_engineer_peer,
    _resolve_engineer_peer_filter,
    _engineer_peer_list_json,
    _engineer_peer_stream_ref_items,
    _engineer_peer_stream_snapshot,
    _normalize_engineer_peer_context,
    _peer_context_task_snapshot,
    _peer_context_engineer_snapshot,
    _decision_excerpt,
    _peer_context_decision_snapshot,
    _normalize_architect_peer_context,
    _normalize_agent_user_message_context,
)
from .mcp_scoped.proposals import (
    _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT,
    _ARCHITECT_PEER_INBOX_MAX_LIMIT,
    _PRODUCT_TASK_LABELS,
    _PROPOSAL_CONTEXT_MARKER,
    _PROPOSAL_DECISION_MARKER,
    _PROPOSAL_PEER_MARKER,
    _add_proposal_peer_marker,
    _architect_idea_brief_tool,
    _architect_task_owned_by_caller,
    _architect_thinking_tool,
    _caller_authority_allows_capability,
    _caller_has_behavior_overlay_admin,
    _cell_group,
    _cell_has_proposal_peer_authority,
    _cell_id,
    _cell_kind,
    _decision_has_product_marker,
    _decision_proposal_linked_task_ids,
    _idea_brief_item_group,
    _idea_brief_owned_by_caller,
    _idea_brief_patch_from_args,
    _idea_brief_ref,
    _idea_brief_ref_from_args,
    _load_product_decision,
    _mind_map_link_ref_from_args,
    _mind_map_node_ref_from_args,
    _mind_map_ref_from_args,
    _normalize_proposal_context,
    _parse_timestampish,
    _product_area_read_json,
    _product_area_ref,
    _product_decision_metadata,
    _product_decisions_for_architect,
    _product_initiative_read_json,
    _product_initiative_ref,
    _product_task_items,
    _product_task_summary,
    _product_task_visible_for_architect,
    _product_visible_decision_ids_for_architect,
    _product_visible_task_ids_for_architect,
    _proposal_board_summary_json,
    _proposal_peer_allowlist_contains,
    _proposal_peer_eligible,
    _proposal_peer_inbox_json,
    _proposal_peer_route_message_for_task,
    _proposal_peer_row_visible,
    _proposal_peer_scope_reason,
    _require_caller_owned_idea_brief,
    _require_caller_owned_thinking_item,
    _resolve_idea_brief_for_caller,
    _resolve_product_architect_peer,
    _resolve_product_architect_peer_filter,
    _resolve_thinking_mind_map,
    _resolve_thinking_mind_map_for_node_or_link,
    _resolve_thinking_scratchpad_note,
    _restricted_behavior_overlay_proposal_allowed,
    _restricted_behavior_overlay_scope_allowed,
    _routed_product_proposal_root_pickup_authorization,
    _routed_product_root_coverage_authorization,
    _row_has_proposal_peer_marker,
    _row_proposal_context,
    _task_has_covers_label,
    _task_has_product_label,
    _task_proposal_list_json,
    _task_proposal_show_json,
    _thinking_bool_arg,
    _thinking_group_for_caller,
    _thinking_item_group,
    _thinking_item_owned_by_caller,
    _thinking_limit,
    _with_idea_brief_owner_flag,
    _with_thinking_owner_flag,
)
from .mcp_scoped.peer_inbox import (
    _ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT,
    _architect_can_inspect_engineer_peer_thread,
    _architect_can_recall_peer_thread,
    _architect_engineer_peer_inspect_json,
    _architect_engineer_peer_threads_json,
    _architect_peer_inbox_json,
    _architect_recall_candidate_visible,
    _candidate_group,
    _candidate_owner_id,
    _candidate_participants,
    _candidate_source_id,
    _candidate_source_type,
    _candidate_visibility,
    _engineer_can_recall_peer_thread,
    _engineer_peer_existing_message_matches_pair,
    _engineer_peer_inbox_json,
    _engineer_peer_inspect_json,
    _engineer_peer_live_context,
    _engineer_peer_thread_belongs_to_pair,
    _engineer_peer_thread_ids,
    _engineer_peer_thread_rows_for_inspect,
    _engineer_peer_thread_rows_for_recall,
    _engineer_peer_thread_summary,
    _engineer_recall_candidate_visible,
    _is_engineer_peer_row,
    _peer_message_id_from_idempotency_key,
    _semantic_recall_json,
    _thread_context_from_rows,
    _thread_pair_key_for_rows,
    _thread_requires_engineer_reply,
    _validate_architect_peer_message_length,
)
from .mcp_scoped.messaging import (
    _ARCHITECT_FEEDBACK_DEFAULT_CATEGORIES,
    _ARCHITECT_FEEDBACK_DEFAULT_PROMPT,
    _ARCHITECT_FEEDBACK_REQUEST_ID_RE,
    _ARCHITECT_FEEDBACK_REQUEST_ID_VALUE_RE,
    _ARCHITECT_FEEDBACK_STATUS_LOAD_LIMIT,
    _agent_user_direct_message_conflicts_with_existing,
    _agent_user_direct_message_id_from_idempotency_key,
    _agent_user_direct_message_reply_thread_id,
    _append_cross_kind_message,
    _architect_dispatch_message_for_task,
    _architect_engineer_feedback_request_json,
    _architect_engineer_feedback_status_json,
    _architect_feedback_hired_engineers,
    _deliver_architect_engineer_message,
    _deliverable_awareness_for_referenced_tasks,
    _direct_message_agent_kind,
    _direct_user_message_response,
    _emit_engineer_peer_architect_event,
    _engineer_peer_digest_message,
    _feedback_request_candidate_rows,
    _feedback_request_id_from_row,
    _feedback_status_item_for_request,
    _format_engineer_feedback_message,
    _inject_architect_peer_message,
    _inject_mcp_message,
    _load_existing_agent_user_direct_message_for_idempotency,
    _load_existing_peer_message_for_idempotency,
    _load_message_entry,
    _mcp_worker_provider_override_arg,
    _normalize_feedback_categories,
    _normalize_feedback_request_id,
    _notify_agent_user_direct_message,
    _requested_user_agent_thread_mismatch,
    _resolve_architect_dispatch_task,
    _sanitize_mcp_worker_provider_override,
    _save_architect_peer_message,
    _save_engineer_peer_message,
    _send_architect_engineer_message,
    _update_cross_kind_peer_delivery,
    _validate_agent_user_direct_message_reply_to_id,
    mark_cross_kind_message_delivery,
    save_agent_user_direct_message_from_mcp,
)

_DECISION_STATUSES = {"proposed", "accepted", "revised", "rejected"}
_JOURNAL_ENTRY_TYPE_NAMES = (
    "decision", "observation", "checkpoint", "plan",
    "note_dismissed", "qa",
)
_JOURNAL_ENTRY_TYPES = set(_JOURNAL_ENTRY_TYPE_NAMES)
_ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPE_NAMES = (
    "decision", "observation", "checkpoint", "plan",
)
_ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPES = set(
    _ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPE_NAMES
)
_PRODUCT_TASK_DEFAULT_LABELS = ("product-proposal", "proposal-only")
_PRODUCT_TASK_UNSAFE_LANES = frozenset({
    "To Do",
    "In Progress",
    "Done",
    ARCHIVED_LANE,
})
_PROPOSAL_JOURNAL_ENTRY_TYPES = {"observation", "checkpoint", "plan"}
_PRODUCT_TASK_PROPOSAL_FORBIDDEN_ARGS = frozenset({
    "assigned_engineer_id",
    "assigned_architect_id",
    "agent_id",
    "dispatch",
    "dispatch_message",
    "scheduled_at",
    "action_name",
    "action",
    "action_vars",
    "provider",
    "external_id",
    "external_url",
    "worker",
    "worker_id",
    "worktree",
    "worktree_path",
    "worktree_branch",
    "branch",
    "repo_root",
    "agent_template",
    "command",
    "model",
    "reasoning_effort",
    "owner_engineer_id",
})
_DISPATCH_SHAPE_VALID_BATCH_STATUSES = {
    "dispatched",
    "queued",
    "deferred",
    "cap_raised",
}

# ---------------------------------------------------------------------------
# Shared scoping helpers
# ---------------------------------------------------------------------------




















































































































































_ARCHITECT_READ_TOOL_NAMES = frozenset({
    "action_show",
    "actions_list",
    "agent_show",
    "agents_list",
    "attention_digest",
    "behavior_overlay_diff",
    "behavior_overlay_proposal_list",
    "behavior_overlay_read",
    "behavior_overlay_versions",
    "boot_summary",
    "completion_audit",
    "board_list",
    "board_summary",
    "decision_list",
    "deploy_state",
    "diff",
    "engineer_peer_inspect",
    "engineer_peer_threads",
    "engineer_journal_read",
    "engineer_feedback_status",
    "engineer_list",
    "engineer_pending_question",
    "events",
    "events_recent",
    "get_architect_settings",
    "help_list",
    "help_query",
    "help_search",
    "help_show",
    "area_list",
    "area_show",
    "initiative_list",
    "initiative_show",
    "journal_read",
    "mcp_calls",
    "pending_hire_list",
    "pending_hire_status",
    "peer_inbox",
    "peer_list",
    "proposal_area_list",
    "proposal_area_show",
    "proposal_board_summary",
    "decision_proposal_list",
    "proposal_initiative_list",
    "proposal_initiative_show",
    "proposal_journal_read",
    "proposal_peer_inbox",
    "proposal_peer_list",
    "task_proposal_list",
    "task_proposal_show",
    "semantic_recall",
    "session_map",
    "specialization_show",
    "specializations_list",
    "group_health_brief",
    "stream_show",
    "streams_list",
    "task_chain",
    "task_list",
    "task_show",
    "thinking_mind_map_list",
    "thinking_mind_map_show",
    "thinking_scratchpad_list",
    "thinking_scratchpad_show",
    "wave_summary",
})




















































































































































































































































































































































































































































































































































from .mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from .mcp_scoped.dispatch_proposal import dispatch_proposal
from .mcp_scoped.dispatch_architect_reads import dispatch_architect_reads
from .mcp_scoped.dispatch_inventory import dispatch_inventory
from .mcp_scoped.dispatch_tasks import dispatch_tasks
from .mcp_scoped.dispatch_planning import dispatch_planning
from .mcp_scoped.dispatch_communications import dispatch_communications
from .mcp_scoped.dispatch_worktrees import dispatch_worktrees
from .mcp_scoped import peer_inbox as _peer_inbox_module

SCOPED_DOMAIN_DISPATCHERS = (
    dispatch_proposal,
    dispatch_architect_reads,
    dispatch_inventory,
    dispatch_tasks,
    dispatch_planning,
    dispatch_communications,
    dispatch_worktrees,
)


async def dispatch_scoped_tool(name, args, handle_command, state, *,
                               tool_prefix: str, caller_kind: str,
                               caller_id: str,
                               idempotency_key: str = ""):
    """Execute a scoped tool call.

    Returns ``(text, is_error)`` or ``(text, is_error, cacheable)`` when the
    MCP idempotency layer should not cache a recoverable refusal.
    """

    # Preserve the facade's long-standing monkeypatch/embedding seam while
    # implementation lives in the peer-inbox domain module.
    _peer_inbox_module.semantic_recall_payload = semantic_recall_payload
    _peer_inbox_module._resolve_engineer_peer_filter = (
        _resolve_engineer_peer_filter
    )
    _peer_inbox_module._resolve_engineer_peer = _resolve_engineer_peer
    _peer_inbox_module._peer_row_involves_engineer = (
        _peer_row_involves_engineer
    )

    _engineer_cell, _engineer_group, caller_kind, auth_error, auth_structured = authorize_caller(
        state, caller_kind=caller_kind, caller_id=caller_id
    )
    if auth_error:
        return auth_error, auth_structured

    persist_missing = getattr(state, "persist_missing_aliased_tasks", None)
    if callable(persist_missing):
        persist_missing()

    view_state = build_scoped_state_view(
        state, caller_kind=caller_kind, caller_id=caller_id,
        caller_cell=_engineer_cell, caller_group=_engineer_group,
    )
    real_state = state
    state = view_state
    tool_name = normalize_tool_name(name, tool_prefix)
    if tool_name in {
            "attention_digest", "completion_audit", "group_health_brief",
            "session_map", "stream_show", "streams_list", "wave_summary",
    }:
        try:
            await prefill_merge_readiness_for_state(
                real_state, group=_engineer_group,
            )
        except Exception:
            # A read may still proceed, but unknown readiness must never
            # masquerade as a merge recommendation.
            log.exception("Failed to prefill merge readiness for %s", tool_name)
    _raw_handle_command = handle_command
    if (
            caller_kind == "architect"
            and _agent_dismissed_at(_engineer_cell)
            and tool_name not in _ARCHITECT_READ_TOOL_NAMES):
        return _architect_dismissed_error(caller_id), True

    async def handle_command(payload):
        command_payload = dict(payload or {})
        if idempotency_key and not str(
                command_payload.get("idempotency_key", "") or "").strip():
            command_payload["idempotency_key"] = derive_idempotency_key(
                idempotency_key,
                command_payload,
            )
        return await _raw_handle_command(command_payload)

    if tool_name in {"help_list", "help_show", "help_search", "help_query"}:
        return dispatch_help_tool(name, args, prefix=tool_prefix)


    context = ScopedDispatchContext(
        name=name,
        args=dict(args or {}),
        handle_command=handle_command,
        state=state,
        real_state=real_state,
        tool_prefix=tool_prefix,
        caller_kind=caller_kind,
        caller_id=caller_id,
        idempotency_key=idempotency_key,
        caller_cell=_engineer_cell,
        caller_group=_engineer_group,
    )
    for domain_dispatcher in SCOPED_DOMAIN_DISPATCHERS:
        result = await domain_dispatcher(context)
        if result is not UNHANDLED:
            return result
    return f"Unknown {tool_prefix.rstrip('_')} tool: {name}", True
