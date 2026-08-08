"""aiohttp server, WebSocket command handler, and runtime entry point."""

import asyncio
import contextlib
import hashlib
import inspect
import json
import mimetypes
import os
import re
import shlex
import shutil
import signal
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import aiohttp
from aiohttp import web
from . import ai_deps
from .ai_embeddings import LocalEmbeddingService
from .ai_index import AIIndexService
from .ai_summaries import AISummaryService
from . import cloud_hooks
from . import config as torque_config
from . import profiling
from .agent_classes import (
    AGENT_CLASS_SCHEMA_VERSION,
    append_agent_class_prompt_block,
    agent_class_authoring_contract,
    agent_class_context_for_cell,
    archive_custom_agent_class,
    delete_custom_agent_class,
    enriched_agent_class_preview,
    load_agent_classes,
    agent_class_definition_by_id,
    save_custom_agent_class,
    validate_agent_class_draft,
)
from .config import (
    WS_PORT,
    DB_FILE,
    WEBVIEW_FILE,
    STANDALONE,
    BIND_HOST,
    ATTACHMENTS_DIR,
    DATA_DIR,
    log,
)
from .db import TorqueDB, canonical_user_agent_thread_id
from .daemon_owner import ProfileDaemonOwner
from .deploy_state import architect_deploy_state_payload, capture_deploy_boot_state
from .mission_control import build_mission_control_summary
from .remote_ingress import (
    ingest_remote_command_request,
    ingest_remote_user_agent_message,
)
from .direct_message_mirrors import (
    ask_recipient_is_user,
    ask_task_labels_for_owner_recipient,
    direct_ask_mirror_source_key,
    save_direct_ask_mirror,
    save_direct_ask_reply_mirror,
)
from .doctor import build_doctor_report_for_db
from dataclasses import asdict, dataclass
from .state import (
    ARCHIVED_LANE,
    AI_DEFAULT_EMBEDDING_MODEL,
    AI_EMBEDDING_RUNTIMES,
    AI_GENERATION_PROVIDERS,
    ArchitectSettings,
    BoardTask,
    EngineerSettings,
    COMPACT_SNAPSHOT_PROTOCOL,
    MatrixState,
    default_ai_index_corpus,
    hot_json_dumps_async,
    hot_json_dumps_bytes_async,
    merge_cleanup_flags,
    normalize_architect_review_gate_thresholds,
    normalize_default_worker_concurrency,
    normalize_engineer_merge_mode,
    task_counts_as_done,
    task_is_closed,
)
from .events import (
    EventLog,
    EventBus,
    EventIngestDrainer,
    PanelEventLog,
    build_event_ingest_envelope,
    get_cell_event_stream,
    health_check,
)
from .event_ingest_db import event_call_row_from_record, redact_event_for_mcp_call_log
from .perceived_empty import PerceivedEmptyDetector
from .metrics import MetricsDaemon
from .adapters import get_adapter, get_providers_async
from .adapters.base import AgentEvent
from .notifications import NotificationManager
from .worktree import (
    ExistingWorktreeTarget,
    WorktreeManager,
    build_diff_scope_context,
    format_stale_base_warning,
    stale_base_post_rebase_evidence_template,
)
from .worktree_boundaries import (
    advance_latest_boundary_after_mechanical_commit,
    boundary_pr_metadata,
    boundary_submodule_branches,
    boundary_summary,
    branch_boundary_tasks,
    latest_boundary_base_branch,
    latest_boundary_task,
    classify_boundary_code_delta,
    mark_branch_boundaries_merged,
    queued_successor_tasks,
    refresh_latest_boundary_after_rebase,
    retarget_queued_successor_tasks,
    started_successor_tasks,
    task_boundary,
)
from .worktree_streams import compute_worktree_streams, prefill_branch_exists_for_state
from .actions import (
    ActionManager,
    DEFAULT_REVIEW_REQUIRED_ABOVE_LOC,
    TORQUE_CONTEXT_STUB,
)
from .artifacts import (
    normalize_artifacts,
    task_artifacts,
)
from .attachment_uploads import (
    AttachmentUploadError,
    save_message_attachment_stream,
)
from .server_routes import build_event_routes, build_http_routes
from .server_artifacts import (
    describe_task_artifact_for_digest,
    finalize_task_attachments,
    remove_task_owned_artifacts_by_filename,
    serialize_upstream_task_artifacts,
    serialize_task_artifact,
    store_preserved_merge_diff,
    store_task_upload,
)
from .server_board_sync import BoardSyncManager
from .task_ids import is_canonical_task_id, is_draft_task_token
from .memory import (
    build_memory_entry,
    build_memory_link,
    build_prompt_memory_block,
    detect_current_task,
    infer_project_key,
    load_visible_memory_entries,
    normalize_entry_type,
    normalize_link_target_kind,
)
from .roles import RoleManager
from .specializations import SpecializationManager
from .external_tickets import (
    ExternalTicketError,
    build_completion_comment,
    import_ticket as import_external_ticket,
    normalize_link as normalize_external_link,
    open_ticket_url,
    post_ticket_comment,
    push_ticket_status,
)
from .execution_scope import (
    engineer_architect_close_denied_message,
    engineer_architect_task_routing_denied_message,
    is_architect_execution_target,
)
from .board_sync import get_provider as get_board_sync_provider
from .mcp import create_mcp_handler, dispatch_mcp_rpc_body
from .mcp_retry import api_request_hash, is_api_write_command, replay_failed_writes
from .identity import (
    agent_identity_anchor,
    agent_kind_for_identity,
    prepend_agent_identity_anchor,
)


AUTO_CLOSE_SPAWNED_LABEL = "torque:auto-close-spawned-agent"

from .server_agent_common import (
    _resolve_agent_id,
    _should_show_guidance_hint,
)
from .commands.user_dm import (
    parse_user_dm_command,
    user_dm_command_supports_provider,
)
from .server_communication import (
    GUIDANCE_HINT_USER_DIRECT_REPLY,
    USER_AGENT_LOOP_MIN_INTERVAL_SECONDS,
    USER_AGENT_LOOP_MAX_INTERVAL_SECONDS,
    USER_AGENT_LOOP_MAX_MESSAGE_CHARS,
    _append_mcp_message,
    _engineer_display_name,
    _summarize_engineer_message,
    _engineer_followup_task_title,
    _format_mcp_message_prompt,
    _format_engineer_message_prompt,
    inject_mcp_message,
    send_optimistic_agent_text,
    _format_injected_mcp_message_prompt,
    _mark_cross_kind_message_delivery,
    _peer_message_row_replay_entry,
    _is_canonical_peer_replay_entry,
    _user_direct_message_id_from_idempotency_key,
    _user_agent_message_idempotency_key,
    _parse_user_agent_loop_interval,
    _format_user_agent_loop_interval,
    _parse_user_agent_loop_command,
    _user_direct_message_reply_tool,
    _format_user_direct_message_prompt,
    _ask_reply_question_from_direct_row,
    _format_ask_reply_direct_message_prompt,
    _direct_message_delivery_response,
    _user_agent_message_conflicts_with_existing,
    _queue_user_direct_message_to_agent,
    _save_user_agent_system_audit_message,
    _save_user_agent_loop_audit_message,
    _user_agent_loop_response,
    _handle_user_agent_loop_command,
    _restore_user_agent_restart_focus,
    _user_agent_restart_response,
    _handle_user_agent_restart_command,
    _user_agent_read_only_command_response,
    _user_agent_unsupported_command_response,
    _replay_buffered_cross_kind_messages,
    _make_agent_session_start_handler,
    _inherit_assigned_engineer_for_derived_task,
    _emit_task_artifact_uploaded_event,
    _engineer_inline_thread_parent,
    _append_engineer_inline_thread_message,
    _create_engineer_followup_task,
    _resolve_pending_engineer_reply_task,
    _send_engineer_message_to_agent,
    _handle_engineer_reply,
    _ask_reply_target_for_task,
    _resolve_human_ask_task,
    resolve_blocked_task_reply,
    _is_architect_ask_task,
    _architect_ask_reply_prompt,
    _resolve_architect_ask_task,
    _queue_cell_prompt_send,
)
from .server_engineer_commands import (
    _append_engineer_journal_entry,
    _engineer_journal_source_key,
    _handle_digest_pause_resume_command,
    _handle_engineer_dismiss_note_command,
    _handle_engineer_flush_now_command,
)
from .server_user_commands import (
    _handle_reminder_command,
    _handle_task_watch_command,
)
from .server_agent_operations import (
    GUIDANCE_HINT_IDENTITY_LAUNCH,
    _resolve_pending_engineer_specializations,
    _project_specialization_names,
    _normalize_engineer_specialization_selection,
    _launch_resolver_for_cell,
    _relaunch_agent_after_worktree_removal,
    _resolve_engineer_group,
    _resolve_engineer_cell,
    _agent_dismissed_at,
    _relaunch_command_base,
    _engineer_dismissed_error,
    _engineer_tombstoned_error,
    _architect_dismissed_error,
    _validate_engineer_lifecycle_authority,
    _validate_architect_lifecycle_authority,
    _effective_owner_engineer_id,
    _dismissal_close_cells,
    _close_cell_session_preserving_state,
    _resolve_architect_cell,
    _engineer_name_exists,
    _architect_name_exists,
    _agent_name_exists_for_kind,
    _behavior_overlay_prompt_block_for_cell,
    _architect_persistent_prompt_text,
    _snapshot_dataclass_like,
    _preview_group_settings_for_prompt,
    _preview_engineer_settings_for_prompt,
    _preview_architect_settings_for_prompt,
    _build_group_system_prompt_preview,
    _agent_overrides_from_role_settings,
    _requested_agent_class_id,
    _apply_agent_class_launch_selection,
    _handle_add_engineer_command,
    _handle_add_architect_command,
    _handle_add_worker_command,
    _handle_agent_class_launch_command,
    _handle_architect_engineer_hire_command,
    _handle_pending_hire_approve_command,
    _handle_pending_hire_reject_command,
    _handle_set_engineer_specializations_command,
    _handle_pending_hire_list_command,
)

from .server_ai_runtime import initialize_ai_runtime
from .server_event_ingest_runtime import initialize_event_ingest_runtime
from .server_ai_settings import (
    _REDACTED_SECRET_VALUE,
    _redact_command_log_value,
    _redact_command_log_payload,
    _empty_ai_secret_metadata,
    _ai_secret_metadata,
    _build_ai_settings_response,
    _ai_settings_updates_from_payload,
    _iter_clear_ai_secret_providers,
    _save_ai_secret_updates,
    _emit_ai_settings_update_delta,
    _ai_embedding_rebuild_confirmation_response,
    _apply_ai_settings_update_command,
)


GUIDANCE_HINT_IDENTITY_DISPATCH = "agent_identity_anchor.dispatch"

from .commands.behavior_overlays import (
    BEHAVIOR_OVERLAY_COMMAND_NAMES,
    BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES,
    BEHAVIOR_OVERLAY_READ_COMMAND_NAMES,
    _BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY,
    _BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY,
    _behavior_overlay_error,
    _behavior_overlay_maybe_create_user_task,
    _behavior_overlay_scope_kwargs,
    _handle_behavior_overlay_architect_approve_command,
    _handle_behavior_overlay_architect_reject_command,
    _handle_behavior_overlay_diff_command,
    _handle_behavior_overlay_proposals_command,
    _handle_behavior_overlay_propose_command,
    _handle_behavior_overlay_read_command,
    _handle_behavior_overlay_reject_command,
    _handle_behavior_overlay_user_approve_command,
    _handle_behavior_overlay_user_reject_command,
    _handle_behavior_overlay_user_rollback_command,
    _handle_behavior_overlay_versions_command,
)
from .commands.board import (
    BOARD_ARCHIVE_COMMAND_NAMES,
    _handle_board_archive_command,
    _handle_board_archive_tasks_command,
    _handle_board_unarchive_command,
    _resolve_task_id,
)
from .commands.board_operations import (
    BOARD_OPERATION_COMMAND_NAMES,
    BoardOperationRuntime,
    handle_board_operation_command,
)
from .commands.agent_operations import (
    AGENT_OPERATION_COMMAND_NAMES,
    AgentOperationRuntime,
    handle_agent_operation_command,
)
from .commands.direct import (
    DIRECT_COMMAND_NAMES,
    DirectCommandRuntime,
    handle_direct_command,
)
from .commands.prompt_preview import (
    PROMPT_PREVIEW_COMMAND_NAMES,
    PromptPreviewRuntime,
    handle_prompt_preview_command,
)
from .commands.runtime_settings import (
    RUNTIME_SETTINGS_COMMAND_NAMES,
    RuntimeSettingsCommandRuntime,
    handle_runtime_settings_command,
)
from .commands.asks import (
    ASK_COMMAND_NAMES,
    AskCommandRuntime,
    handle_ask_command,
)
from .commands.pipelines import (
    PIPELINE_COMMAND_NAMES,
    PipelineCommandRuntime,
    handle_pipeline_command,
)
from .commands.agents import (
    AGENT_LIFECYCLE_COMMAND_NAMES,
    _handle_purge_agent_now_command,
    _handle_recently_deleted_agents_command,
    _handle_remove_agent_command,
    _handle_restore_agent_command,
    _restore_or_purge_authority_error,
)
from .commands.agent_classes import (
    AGENT_CLASS_COMMAND_NAMES,
    _AGENT_CLASS_COMMAND_REGISTRY,
    _agent_class_authoring_payload_from_command,
    _handle_agent_class_command,
)
from .commands.schedules import (
    SCHEDULE_COMMAND_NAMES,
    _SCHEDULE_COMMAND_REGISTRY,
    _handle_schedule_command,
)
from .commands.roles import _handle_role_template_command
from .commands.catalog import (
    CATALOG_COMMAND_NAMES,
    CatalogCommandRuntime,
    _CATALOG_COMMAND_REGISTRY,
    handle_catalog_command,
)
from .commands.settings import (
    SETTINGS_MUTATION_COMMAND_NAMES,
    SETTINGS_READ_COMMAND_NAMES,
    _SETTINGS_MUTATION_COMMAND_REGISTRY,
    _SETTINGS_READ_COMMAND_REGISTRY,
    _handle_settings_mutation_command,
    _handle_settings_read_command,
)
from .commands.memory import (
    MEMORY_COMMAND_NAMES,
    _MEMORY_COMMAND_REGISTRY,
    _handle_memory_command,
)
from .commands.operator_notices import (
    OPERATOR_NOTICE_COMMAND_NAMES,
    _OPERATOR_NOTICE_COMMAND_REGISTRY,
)
from .commands.ai_reports import (
    AIReportCommandRuntime,
    TORQUE_AI_MCP_REPORT_ACTIONS as _TORQUE_AI_MCP_REPORT_ACTIONS,
    TORQUE_AI_MCP_REPORT_TOOL_NAMES as _TORQUE_AI_MCP_REPORT_TOOL_NAMES,
    handle_ai_report_command,
)
from .commands.worktrees import (
    WORKTREE_COMMAND_NAMES,
    WorktreeCommandRuntime,
    handle_worktree_command,
)
from .services.worktrees import (
    WorktreeOrchestrationDependencies,
    configure_worktree_orchestration,
    _worktree_merge_preserve_diff_enabled,
    _worktree_merge_auto_done_candidate,
    _maybe_auto_move_merged_task_to_done,
    _worktree_merge_error,
    _target_from_cell,
    _target_from_existing_worktree,
    _target_has_driverless_payload,
    _target_branch_from_payload,
    _reconcile_worktree_branch,
    _active_agent_owning_worktree_target_for_state,
    _resolve_worktree_command_target_value,
    _engineer_merge_mode_for_cell,
    _github_pr_closing_refs_enabled,
    _add_task_with_pipeline_relatives,
    _active_pr_closing_ref_tasks,
    _linked_github_issues_for_pr,
    _append_github_closing_refs_to_pr_body,
    _log_pr_task_ref_rewrite,
    _preflight_worktree_merge_gates,
    _capture_worktree_merge_preserve_diff,
    _capture_worktree_merge_resume_targets,
    _worktree_merge_requested_cleanup,
    _auto_force_push_metadata,
    _attach_auto_force_push_metadata,
    _append_post_success_warning,
    _post_success_result_error,
    _post_success_cleanup_warning,
    _sha_equal,
    _pr_status_indicates_merged,
    _merge_commit_sha_from_status,
    _merge_commit_sha_from_sources,
    _candidate_pr_statuses,
    _pr_selector_from_sources,
    _base_match_from_result,
    _persist_preserved_merge_diff_warning_only,
    _derive_handoff_accepted,
    _record_engineer_dispatch_shape_metric,
    _record_derive_dispatch_shape_metric,
    _latest_open_boundary_task_for_cell,
    _record_nested_submodule_metadata_on_latest_boundary,
    _ee_pr_flow_submodules,
    _legacy_nested_submodules,
    _combine_nested_submodule_results,
    _summarize_paths,
    _untracked_overwrite_message,
    _stale_base_warning,
    _stale_base_rebase_command,
    _stale_base_post_rebase_evidence_required,
    _stale_base_suggestion,
    _attach_stale_base_guidance,
    _attach_stale_base,
    _stale_base_check_merge_result,
    _stale_base_merge_result,
    _stale_base_review_derive_result,
    _stale_base_force_enabled,
    _boundary_mismatch_force_enabled,
    _boundary_mismatch_check_allowed,
    _short_boundary_sha,
    _boundary_recorded_sha,
    _boundary_tip_sha,
    _normalize_boundary_tip_mismatch_info,
    _ensure_boundary_tip_mismatch_info,
    _boundary_tip_mismatch_message,
    _boundary_gate_message,
    _maybe_reject_stale_base_review_derive,
    _pipeline_root_id_for_task,
    _agent_pipeline_root_ids,
    _configured_worktree_submodules_for_cell,
    _repo_rel_path,
    _is_reconcilable_nested_gitlink_conflict,
    _review_cycle_merge_sibling_candidates,
    _git_stdout,
    _review_cycle_sibling_branch_divergence,
    _sibling_branch_divergence_merge_result,
    _sibling_branch_divergence_gate_for_merge,
    _workflow_breach_worker_for_task,
    _workflow_breach_engineer_for,
    _workflow_breach_active_task_for_worker,
    _format_workflow_breach_message,
    _scope_domain_for_cell,
    _emit_workflow_breach_event,
    _handle_workflow_breach_command,
    _emit_stale_base_catch_workflow_breach,
    _emit_stale_base_override_workflow_breach,
    _boundary_mismatch_override_actor,
    _boundary_mismatch_override_reason,
    _emit_boundary_mismatch_override_workflow_breach,
    _confirm_pr_merged_and_base_at_merge,
    _post_success_guard_warning,
    _fallback_successful_worktree_merge_result,
    _latest_merged_pr_boundary_for_post_success,
    _recover_authoritative_post_success_from_boundary,
    _resolve_already_merged_sha,
    _finalize_successful_worktree_merge,
    _finalize_successful_driverless_worktree_merge,
    _run_direct_worktree_merge,
    _run_pr_worktree_merge,
)
from .commands.task_dispatch import (
    TaskDispatchRuntime,
    handle_dispatch_task_command,
)
from .commands.engineer_operations import (
    ENGINEER_OPERATION_COMMAND_NAMES,
    EngineerOperationRuntime,
    handle_engineer_operation_command,
)
from .commands.ui_state import (
    UI_STATE_COMMAND_NAMES,
    _UI_STATE_COMMAND_REGISTRY,
    _handle_ui_state_command,
)
from .commands.planning import (
    PLANNING_COMMAND_NAMES,
    _PLANNING_COMMAND_REGISTRY,
    _area_actor_from_data,
    _area_error,
    _area_note_target_fields,
    _decision_belongs_to_group,
    _handle_area_command,
    _handle_idea_brief_command,
    _handle_initiative_command,
    _handle_scratchpad_command,
    _idea_brief_error,
    _idea_brief_patch_from_data,
    _idea_brief_ref_from_data,
    _idea_brief_scope_error,
    _initiative_actor_from_data,
    _initiative_error,
    _thinking_actor_from_data,
    _thinking_bool,
    _thinking_error,
    _thinking_group_from_data,
    _thinking_item_scope_error,
    _validate_area_note_target,
)


def _build_ai_report_command_runtime(
    *, state, action_mgr, worktree_mgr, board_sync_manager, bridge,
    dispatch_command, panel_event, panel_log, resolve_base_dir,
    checkpoint_message, checkpoint_on_report,
    checkpoint_worktree_with_submodules, close_agent_session_only,
    ownership_engineer_id_for_dispatch_source, record_task_boundary,
) -> AIReportCommandRuntime:
    """Compose transport/runtime integrations for the AI-report domain."""
    return AIReportCommandRuntime(
        state=state,
        action_mgr=action_mgr,
        worktree_mgr=worktree_mgr,
        board_sync_manager=board_sync_manager,
        bridge=bridge,
        dispatch_command=dispatch_command,
        panel_event=panel_event,
        panel_log=panel_log,
        resolve_base_dir=resolve_base_dir,
        ai_derive_parent_task=_ai_derive_parent_task,
        append_mcp_message=_append_mcp_message,
        apply_verification_report=_apply_verification_report,
        auto_resolve_product_proposal_roots_and_enqueue=(
            _auto_resolve_product_proposal_roots_and_enqueue
        ),
        capture_auto_resume_targets=_capture_auto_resume_targets,
        checkpoint_message=checkpoint_message,
        checkpoint_on_report=checkpoint_on_report,
        checkpoint_worktree_with_submodules=checkpoint_worktree_with_submodules,
        close_agent_session_only=close_agent_session_only,
        derive_handoff_accepted=_derive_handoff_accepted,
        find_reusable_review_fix_task=_find_reusable_review_fix_task,
        handle_engineer_reply=_handle_engineer_reply,
        inherit_assigned_engineer_for_derived_task=(
            _inherit_assigned_engineer_for_derived_task
        ),
        maybe_apply_review_required_gate=_maybe_apply_review_required_gate,
        maybe_auto_close_root_done_agents=_maybe_auto_close_root_done_agents,
        maybe_auto_resume_targets=_maybe_auto_resume_targets,
        maybe_reject_stale_base_review_derive=(
            _maybe_reject_stale_base_review_derive
        ),
        nearest_ancestor_agent_for_action_stage=(
            _nearest_ancestor_agent_for_action_stage
        ),
        ownership_engineer_id_for_dispatch_source=(
            ownership_engineer_id_for_dispatch_source
        ),
        prior_live_reviewer_agent_for_chain=(
            _prior_live_reviewer_agent_for_chain
        ),
        promote_task_for_active_report=_promote_task_for_active_report,
        pump_auto_dispatch_queue=_pump_auto_dispatch_queue,
        record_derive_dispatch_shape_metric=(
            _record_derive_dispatch_shape_metric
        ),
        record_review_verdict_evidence=_record_review_verdict_evidence,
        record_task_boundary=record_task_boundary,
        record_task_completion_evidence_snapshot=(
            _record_task_completion_evidence_snapshot
        ),
        refresh_reused_derived_task=_refresh_reused_derived_task,
        reject_completion_with_open_descendants=(
            _reject_completion_with_open_descendants
        ),
        reject_mandatory_review_done_without_ship=(
            _reject_mandatory_review_done_without_ship
        ),
        reject_missing_deliverable=_reject_missing_deliverable,
        reject_pending_review=_reject_pending_review,
        resolve_agent_id=_resolve_agent_id,
        resolve_ai_report_task=_resolve_ai_report_task,
        resolve_feature_review_derive_stream_backstop_task=(
            _resolve_feature_review_derive_stream_backstop_task
        ),
        review_event_message=_review_event_message,
        shared_review_checkpoint_block_reason=(
            _shared_review_checkpoint_block_reason
        ),
    )

from .server_actions import _action_to_yaml
from .server_agent import (
    AgentLaunchService,
    _append_task_artifacts,
    _build_self_dispatch_prompt,
    _copy_worktree_context,
    _new_agent_prompt_sequence,
    _startup_prompt_for_new_agent,
    mcp_entrypoint_for_cell,
    resolve_default_boot_nudge,
    runtime_env_vars_for_cell,
)
from .server_dispatch import (
    _cells_share_worktree_context,
    _capture_auto_resume_targets,
    _find_active_worktree_owner,
    _maybe_auto_resume_targets,
    _pump_auto_dispatch_queue,
    _pump_auto_dispatch_queue_forever,
    _scheduler_loop,
    _should_handoff_shared_worktree,
    _should_queue_existing_agent_dispatch,
)
from .server_supervisor import (
    SupervisorLivenessWatchdog,
    build_supervisor_health_projection,
    build_supervisor_restart_payload,
    build_supervisor_sessions_payload,
    build_supervisor_terminate_payload,
    supervisor_health_fingerprint,
)
from .server_worktrees import (
    WorktreeCommandTarget,
    _append_pr_url_to_squash_body,
    _collect_linked_github_issues,
    _generate_merge_message,
    _pr_merge_failure_allows_auto,
    _pr_result_metadata,
    _record_pr_metadata_on_latest_boundary,
    _rewrite_pr_torque_task_refs_metadata,
    _split_merge_message_for_pr,
    _worktree_diff_updater,
    _worktree_full_diff,
    _worktree_merge_diff_snapshot,
)
from .worktree_streams import compute_worktree_stream
from .server_prompts import (
    build_dispatch_postscript,
    build_torque_system_prompt,
    compute_commit_hint,
    deliverable_word,
    reviewer_assignment_disclosure,
)
from .engineer_session_map import build_engineer_session_map
from .codex_usage_backfill import (
    refresh_codex_provider_usage_for_agents,
)


def _read_torque_version() -> str:
    candidates = [
        torque_config.SCRIPT_DIR / "VERSION",
        torque_config.SCRIPT_DIR.parent / "VERSION",
        Path(__file__).resolve().parents[1] / "VERSION",
    ]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "unknown"


_STARTED_AT: float = time.time()
_TORQUE_VERSION: str = _read_torque_version()
_LOG_LINE_RE = re.compile(
    r"^(?P<clock>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<message>.*)$"
)
_LOG_TAIL_BYTES = 256 * 1024
_LOG_MAX_LINES = 500
_CODEX_PROVIDER_USAGE_BACKFILL_INTERVAL_SECONDS = 300
_ACTIVE_DAEMON_OWNER: ProfileDaemonOwner | None = None


def _runtime_payload(*, bridge=None, state=None) -> dict:
    runtime_mode = (
        "desktop"
        if os.environ.get("TORQUE_DESKTOP_MODE", "").strip()
        else "standalone"
    )
    capabilities = getattr(bridge, "capabilities", None)
    embedded_terminal = bool(
        getattr(capabilities, "supports_embedded_terminal", False)
    )
    if state is not None and hasattr(state, "get_default_command"):
        default_command = state.get_default_command()
    else:
        default_command = torque_config.DEFAULT_COMMAND
    supervisor_health = build_supervisor_health_projection(
        bridge,
        profile_skip_pty=bool(getattr(torque_config, "PROFILE_SKIP_PTY", False)),
    )
    return {
        "mode": runtime_mode,
        "standalone": STANDALONE,
        "embedded_terminal": embedded_terminal,
        "layout": "ide" if embedded_terminal else "classic",
        "terminal_backend": "pty",
        "home_directory": str(Path.home()),
        "profile": os.environ.get("TORQUE_PROFILE", "").strip(),
        "data_dir": str(DATA_DIR),
        "port": WS_PORT,
        "default_command": default_command,
        "version": _TORQUE_VERSION,
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "daemon_id": (
            _ACTIVE_DAEMON_OWNER.daemon_id if _ACTIVE_DAEMON_OWNER else ""
        ),
        "log_path": str(DATA_DIR / "torque.log"),
        "supervisor_log_path": str(DATA_DIR / "pty_supervisor.log"),
        "supervisor": supervisor_health,
    }


def _parse_log_line(line: str, *, today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    text = line.rstrip("\n")
    match = _LOG_LINE_RE.match(text)
    if not match:
        return {
            "ts": 0.0,
            "level": "",
            "logger": "torque",
            "message": text,
            "raw": text,
        }
    hour, minute, second = [
        int(part) for part in match.group("clock").split(":")
    ]
    stamped = today.replace(
        hour=hour,
        minute=minute,
        second=second,
        microsecond=0,
    )
    return {
        "ts": stamped.timestamp(),
        "level": match.group("level").strip(),
        "logger": "torque",
        "message": match.group("message"),
        "raw": text,
    }


def _tail_log_entries(
    log_path: Path,
    *,
    since: float = 0.0,
    limit: int = _LOG_MAX_LINES,
    tail_bytes: int = _LOG_TAIL_BYTES,
) -> dict:
    limit = max(1, min(int(limit or _LOG_MAX_LINES), 2000))
    try:
        stat = log_path.stat()
    except FileNotFoundError:
        return {
            "lines": [],
            "cursor": time.time(),
            "size": 0,
            "inode": "",
            "path": str(log_path),
        }
    start = max(0, stat.st_size - max(4096, int(tail_bytes or _LOG_TAIL_BYTES)))
    with log_path.open("rb") as handle:
        handle.seek(start)
        if start:
            handle.readline()  # discard a partial first line
        raw = handle.read()
    today = datetime.now(timezone.utc)
    entries = []
    for raw_line in raw.decode("utf-8", "replace").splitlines():
        entry = _parse_log_line(raw_line, today=today)
        if since and entry["ts"] and entry["ts"] <= since:
            continue
        entries.append(entry)
    if len(entries) > limit:
        entries = entries[-limit:]
    cursor = max([entry.get("ts", 0.0) for entry in entries] + [since, time.time()])
    inode = str(getattr(stat, "st_ino", "") or "")
    return {
        "lines": entries,
        "cursor": cursor,
        "size": stat.st_size,
        "inode": inode,
        "path": str(log_path),
    }


def _log_path_for_target(target: str) -> tuple[str, Path]:
    normalized = str(target or "daemon").strip().lower()
    if normalized == "supervisor":
        return "supervisor", DATA_DIR / "pty_supervisor.log"
    return "daemon", DATA_DIR / "torque.log"








def _relay_snapshot_group(state: MatrixState) -> str:
    """Return the group used for relay snapshots, matching roster semantics."""
    group = str(getattr(state, "active_group", "") or "").strip()
    if not group:
        groups = [
            str(name or "").strip()
            for name in getattr(state, "groups", {}).keys()
            if str(name or "").strip()
        ]
        if len(groups) == 1:
            group = groups[0]
    return group


def _relay_agent_roster(state: MatrixState) -> list[dict]:
    """Return the group-scoped, non-tombstoned agent roster for relay snapshots."""
    group = _relay_snapshot_group(state)
    if not group:
        return []
    roster: list[dict] = []
    for cell in getattr(state, "agents", {}).values():
        if state.agent_is_tombstoned(cell):
            continue
        if str(getattr(cell, "group", "") or "") != group:
            continue
        roster.append({
            "id": getattr(cell, "id", ""),
            "name": getattr(cell, "name", ""),
            "kind": getattr(cell, "kind", ""),
        })
    return roster


def _relay_agent_state_snapshot(state: MatrixState) -> list[dict]:
    """Return group-scoped current ephemeral agent state for relay snapshots."""
    group = _relay_snapshot_group(state)
    if not group:
        return []
    rows: list[dict] = []
    for cell in getattr(state, "agents", {}).values():
        if state.agent_is_tombstoned(cell):
            continue
        if str(getattr(cell, "group", "") or "") != group:
            continue
        context_window = getattr(cell, "context_window", None)
        provider_usage = getattr(cell, "provider_usage", None)
        rows.append({
            "id": getattr(cell, "id", ""),
            "name": getattr(cell, "name", ""),
            "kind": getattr(cell, "kind", ""),
            "agent_type": getattr(cell, "agent_type", ""),
            "runner_backend": getattr(cell, "runner_backend", "pty"),
            "status": getattr(cell, "status", ""),
            "activity_detail": getattr(cell, "activity_detail", ""),
            "needs_attention": bool(getattr(cell, "needs_attention", False)),
            "context_window": (
                dict(context_window) if isinstance(context_window, dict) else None
            ),
            "provider_usage": (
                dict(provider_usage) if isinstance(provider_usage, dict) else None
            ),
        })
    return rows


# Worktree merge, PR, preflight, and workflow-breach orchestration lives in
# torque.services.worktrees and is re-exported above for compatibility.



from .server_review import (
    _REVIEW_GATE_ACTION,
    _ai_derive_parent_task,
    _resolve_inherited_worktree_source,
    _agent_can_receive_dispatch,
    _promote_task_for_active_report,
    _worktree_branch_has_commits_ahead,
    _stream_review_derive_parent_task,
    _stream_has_open_feature_review_boundary,
    _resolve_feature_review_derive_stream_backstop_task,
    _reject_completion_with_open_descendants,
    _nearest_ancestor_agent_for_action_stage,
    _prior_live_reviewer_agent_for_chain,
    _looks_like_review_task,
    _is_feature_review_task,
    _task_ancestry_has_agent,
    _active_shared_worktree_review_for_cell,
    _shared_review_checkpoint_block_reason,
    _normalized_review_verdict_line,
    _review_verdict_from_message,
    _normalize_review_followup_classification,
    _review_followup_classification_from_message,
    _task_review_evidence,
    _review_event_message,
    _build_review_verdict_payload,
    _amend_review_verdict_evidence,
    _record_review_verdict_evidence,
    _review_task_has_ship_verdict,
    _coerce_action_bool,
    _action_is_implementation_depth,
    _review_gate_threshold_from_action,
    _explicit_review_gate_threshold_from_action,
    _review_gate_policy_from_loc_gate,
    _review_gate_transition_policy,
    _review_gate_policy_from_action_threshold,
    _review_gate_task_chain,
    _review_gate_architect_id,
    _review_gate_architect_policy,
    _chain_has_shipped_review,
    _feature_review_transition_is_mandatory,
    _action_requires_mandatory_feature_review,
    _task_is_pipeline_descendant,
    _task_has_shipped_review_descendant,
    _mandatory_review_done_error,
    _task_has_matching_deliverable_artifact,
    _reject_missing_deliverable,
    _task_upload_actor_source,
    _task_upload_engineer_scope_error,
    _reject_pending_review,
    _reject_mandatory_review_done_without_ship,
    _review_gate_diff_size,
    _review_gate_skip_audit_message,
    _maybe_apply_review_required_gate,
)

def _shipped_review_cleanup_candidates(state: MatrixState, merged_cell) -> list:
    """Return reviewer agents whose Ship verdict should be cleaned post-merge."""
    if not state or not merged_cell:
        return []
    root_ids = {
        str(getattr(task, "pipeline_root_id", "") or task.id).strip()
        for task in state.board_tasks.values()
        if getattr(task, "agent_id", "") == getattr(merged_cell, "id", "")
    }
    root_ids.discard("")
    if not root_ids:
        return []

    candidates = []
    seen_agent_ids = set()
    for root_id in sorted(root_ids):
        for task in state.board_get_chain(root_id):
            if not task_counts_as_done(task):
                continue
            if not _is_feature_review_task(task):
                continue
            if not _review_task_has_ship_verdict(task):
                continue
            agent_id = str(getattr(task, "agent_id", "") or "").strip()
            if (
                not agent_id
                or agent_id == getattr(merged_cell, "id", "")
                or agent_id in seen_agent_ids
            ):
                continue
            if _agent_has_open_assigned_tasks(state, agent_id):
                continue
            if _agent_has_targeted_auto_dispatch_work(state, agent_id):
                continue
            if _agent_has_pending_engineer_followups(state, agent_id):
                continue
            cell = state.agents.get(agent_id)
            if not cell or getattr(cell, "cell_type", "") != "agent":
                continue
            seen_agent_ids.add(agent_id)
            candidates.append(cell)
    return candidates


async def _cleanup_shipped_reviewers_for_merged_cell(
        state: MatrixState,
        merged_cell,
        cleanup_after_merge,
) -> dict:
    """Close/remove Ship reviewers after their parent branch has merged."""
    summary = {
        "close_agent": True,
        "remove_worktree": True,
        "agents": [],
        "agent_closed": 0,
        "worktree_removed": 0,
        "errors": [],
    }
    for reviewer in _shipped_review_cleanup_candidates(state, merged_cell):
        summary["agents"].append(reviewer.id)
        cleanup = await cleanup_after_merge(
            reviewer,
            close_agent=True,
            remove_worktree=True,
        )
        if cleanup.get("agent_closed"):
            summary["agent_closed"] += 1
        if cleanup.get("worktree_removed"):
            summary["worktree_removed"] += 1
        summary["errors"].extend(cleanup.get("errors", []) or [])
    return summary


def _is_generic_review_fix_task(task) -> bool:
    if not task:
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    return action_name == "feature/fix-review" or "review-fix" in labels


def _find_reusable_review_fix_task(state: MatrixState, task,
                                   action_name: str):
    """Return an unresolved generic review-fix task in the active review loop."""
    if str(action_name or "").strip().lower() != "feature/fix-review":
        return None
    review_task = task if _looks_like_review_task(task) else None
    ancestor_id = str(getattr(task, "parent_task_id", "") or "").strip() if task else ""
    while not review_task and ancestor_id:
        ancestor = state.board_tasks.get(ancestor_id)
        if not ancestor:
            break
        if _looks_like_review_task(ancestor):
            review_task = ancestor
            break
        ancestor_id = str(getattr(ancestor, "parent_task_id", "") or "").strip()
    if not review_task:
        return None

    candidates = [
        child for child in state.board_get_children(review_task.id)
        if not task_is_closed(child)
        and _is_generic_review_fix_task(child)
    ]
    candidates.sort(
        key=lambda current: (
            getattr(current, "lane", "") != "In Progress",
            getattr(current, "lane", "") != "To Do",
            getattr(current, "created_at", "") or "",
            getattr(current, "id", "") or "",
        )
    )
    return candidates[0] if candidates else None


def _merge_reused_task_description(existing: str, incoming: str) -> str:
    existing = str(existing or "").strip()
    incoming = str(incoming or "").strip()
    if not incoming:
        return existing
    if not existing or existing == incoming:
        return incoming
    if incoming in existing:
        return existing
    return existing + "\n\n" + incoming


def _refresh_reused_derived_task(task, *, message: str,
                                 description: str = "",
                                 action_vars: dict | None = None) -> None:
    """Update a reused derived task so follow-up prompts use fresh guidance."""
    if not task:
        return
    message = str(message or "").strip()
    if message:
        task.task = message
    task.description = _merge_reused_task_description(
        getattr(task, "description", "") or "",
        description,
    )
    if action_vars:
        merged_vars = dict(getattr(task, "action_vars", {}) or {})
        merged_vars.update(action_vars)
        task.action_vars = merged_vars


def _agent_has_open_assigned_tasks(state: MatrixState, agent_id: str) -> bool:
    """Return whether the agent still owns any unresolved board task."""
    if not agent_id:
        return False
    for current in state.board_tasks.values():
        if current.agent_id != agent_id:
            continue
        if task_is_closed(current):
            continue
        return True
    return False


_WORKTREE_REMOVAL_FRESH_AGENT_SECONDS = 5 * 60


def _timestamp_to_unix(value) -> float:
    if isinstance(value, (int, float)):
        return float(value or 0.0)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _worktree_path_contains(path: str, candidate: str) -> bool:
    path = str(path or "").strip()
    candidate = str(candidate or "").strip()
    if not path or not candidate:
        return False
    try:
        root = os.path.realpath(os.path.expanduser(path))
        child = os.path.realpath(os.path.expanduser(candidate))
        return os.path.commonpath([root, child]) == root
    except Exception:
        return path == candidate or candidate.startswith(path.rstrip("/") + "/")


def _worktree_entry_matches_agent(repo_root: str, path: str, agent) -> bool:
    """Best-effort match from a git worktree entry to a live Torque agent.

    The primary key is ``agent.worktree_path``, but cleanup safety must still
    recognize an active worker whose tracking was partially cleared by a prior
    failed cleanup. In that case the terminal's current/directory/git_root and
    Torque's default worktree path basename (<agent id>) still identify the
    attached worktree.
    """
    if not agent or getattr(agent, "cell_type", "") != "agent":
        return False
    path = str(path or "").strip()
    if not path:
        return False

    agent_repo = str(
        getattr(agent, "worktree_repo_root", "")
        or getattr(agent, "git_root", "")
        or ""
    ).strip()
    if agent_repo == repo_root:
        if _worktree_path_contains(path, getattr(agent, "worktree_path", "")):
            return True
    else:
        # Linked worktrees report their own path as git_root from inside the
        # worker terminal, not the main repo root.
        git_root = str(getattr(agent, "git_root", "") or "").strip()
        if git_root and _worktree_path_contains(path, git_root):
            return True

    for attr in ("worktree_path", "directory", "current_path"):
        value = str(getattr(agent, attr, "") or "").strip()
        if value and _worktree_path_contains(path, value):
            return True

    try:
        basename = os.path.basename(os.path.realpath(path))
    except Exception:
        basename = os.path.basename(path)
    agent_id = str(getattr(agent, "id", "") or "").strip()
    return bool(agent_id and basename == agent_id)


def _fresh_assigned_task_for_agent(
        state: MatrixState,
        agent_id: str,
        *,
        now: float,
        threshold: float = _WORKTREE_REMOVAL_FRESH_AGENT_SECONDS):
    if not agent_id:
        return None
    newest = None
    newest_ts = 0.0
    for task in state.board_tasks.values():
        if getattr(task, "agent_id", "") != agent_id:
            continue
        if task_is_closed(task):
            continue
        ts = (
            _timestamp_to_unix(getattr(task, "lane_entered_at", ""))
            or _timestamp_to_unix(getattr(task, "updated_at", ""))
            or _timestamp_to_unix(getattr(task, "created_at", ""))
        )
        if ts and now - ts <= threshold and ts >= newest_ts:
            newest = task
            newest_ts = ts
    return newest


def _worktree_removal_refusal_reason(
        state: MatrixState,
        cell,
        *,
        now: float | None = None,
        threshold: float = _WORKTREE_REMOVAL_FRESH_AGENT_SECONDS) -> str:
    """Return a hard-refusal reason for active/fresh worktree removal."""
    if not state or not cell or not getattr(cell, "worktree_path", ""):
        return ""
    queued_followups = [
        task for task in state.board_tasks.values()
        if getattr(task, "agent_id", "") == getattr(cell, "id", "")
        and getattr(task, "lane", "") in {"Backlog", "To Do"}
    ]
    if queued_followups:
        task_ids = ", ".join(
            str(getattr(task, "id", "") or "")
            for task in queued_followups[:3]
        )
        verb = "exists" if len(queued_followups) == 1 else "exist"
        extra = "" if len(queued_followups) <= 3 else ", …"
        return (
            "skipped: worktree release declined because a queued follow-up "
            f"{verb} on worker '{getattr(cell, 'name', '') or cell.id}' "
            f"({task_ids}{extra})"
        )
    if state.agent_is_tombstoned(cell):
        return ""

    status = str(getattr(cell, "status", "") or "").strip().lower()
    non_stopped = status not in {"", "stopped", "error"}
    now = float(now if now is not None else time.time())
    name = str(getattr(cell, "name", "") or getattr(cell, "id", "") or "agent")

    if getattr(cell, "session_id", None) and status != "stopped":
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (attached session)"
        )
    if status == "running":
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (running)"
        )
    if non_stopped and _agent_has_open_assigned_tasks(state, cell.id):
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (open assigned task)"
        )

    latest_activity = max(
        _timestamp_to_unix(getattr(cell, "last_progress_at", 0.0)),
        _timestamp_to_unix(getattr(cell, "last_heartbeat_at", 0.0)),
        _timestamp_to_unix(getattr(cell, "last_activity_at", 0.0)),
        _timestamp_to_unix(getattr(cell, "last_event_at", 0.0)),
    )
    if non_stopped and latest_activity and now - latest_activity <= threshold:
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (recent activity)"
        )

    fresh_task = _fresh_assigned_task_for_agent(
        state,
        cell.id,
        now=now,
        threshold=threshold,
    )
    if non_stopped and fresh_task:
        return (
            "skipped: worktree belongs to active/fresh agent "
            f"'{name}' (recently dispatched task {fresh_task.id})"
        )
    return ""


def _agent_has_targeted_auto_dispatch_work(state: MatrixState,
                                           agent_id: str) -> bool:
    """Return whether a queued auto-dispatch entry is pinned to this agent."""
    if not agent_id:
        return False
    for entries in state.auto_dispatch_queues.values():
        for entry in entries:
            if entry.target_agent_id != agent_id:
                continue
            queued = state.board_tasks.get(entry.task_id)
            if queued and not task_is_closed(queued):
                return True
    return False


def _agent_has_pending_engineer_followups(state: MatrixState,
                                        agent_id: str) -> bool:
    """Return whether the agent still owes the designated engineer a visible reply."""
    if not agent_id:
        return False
    if state.agent_pending_engineer_reply_tasks(agent_id):
        return True
    cell = state.agents.get(agent_id)
    return bool(cell and cell.pending_engineer_message)


async def _maybe_auto_close_root_done_agents(
        state: MatrixState,
        task,
        *,
        action_mgr: ActionManager,
        resolve_base_dir,
        close_agent,
) -> list[str]:
    """Auto-close agents whose completed actions opt in after root completion."""
    if not task:
        return []
    root_id = str(getattr(task, "pipeline_root_id", "") or task.id).strip()
    root_task = state.board_tasks.get(root_id) if root_id else None
    if not root_task:
        root_task = task
    if not task_counts_as_done(root_task):
        return []
    if state.task_has_unresolved_descendants(root_task.id):
        return []

    base_dir_cache: dict[str, str] = {}
    auto_close_cache: dict[tuple[str, str], bool] = {}
    candidates = []
    seen_agent_ids = set()

    for chain_task in state.board_get_chain(root_task.id):
        if not task_counts_as_done(chain_task):
            continue
        agent_id = str(getattr(chain_task, "agent_id", "") or "").strip()
        action_name = str(getattr(chain_task, "action_name", "") or "").strip()
        if not agent_id or not action_name or agent_id in seen_agent_ids:
            continue

        group = str(getattr(chain_task, "group", "") or root_task.group or "").strip()
        if group not in base_dir_cache:
            base_dir_cache[group] = await resolve_base_dir(group)
        base_dir = base_dir_cache[group]
        cache_key = (base_dir, action_name)
        if cache_key not in auto_close_cache:
            auto_close_cache[cache_key] = action_mgr.get_auto_close_on_done(
                action_name,
                base_dir=base_dir,
            )
        if not auto_close_cache[cache_key]:
            continue
        if AUTO_CLOSE_SPAWNED_LABEL not in (getattr(chain_task, "labels", []) or []):
            continue
        if _agent_has_open_assigned_tasks(state, agent_id):
            continue
        if _agent_has_targeted_auto_dispatch_work(state, agent_id):
            continue
        if _agent_has_pending_engineer_followups(state, agent_id):
            continue

        cell = state.agents.get(agent_id)
        if not cell or cell.cell_type != "agent":
            continue
        seen_agent_ids.add(agent_id)
        candidates.append(cell)

    closed = []
    for candidate in candidates:
        await close_agent(candidate)
        closed.append(candidate.id)
    return closed








































































































def _is_closing_ui_ws_error(exc: Exception) -> bool:
    client_reset = getattr(
        getattr(aiohttp, "client_exceptions", None),
        "ClientConnectionResetError",
        None,
    )
    if client_reset and isinstance(exc, client_reset):
        return True
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return True
    text = str(exc or "").lower()
    return "closing transport" in text or "write eof" in text


def _truthy_compact_value(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {
        "1",
        "true",
        "yes",
        "compact",
        COMPACT_SNAPSHOT_PROTOCOL,
    }


def _payload_wants_compact_snapshot(payload: dict | None) -> bool:
    payload = payload or {}
    return (
        str(payload.get("protocol_version", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or str(payload.get("snapshot_protocol", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or _truthy_compact_value(payload.get("compact"))
    )


def _request_wants_compact_snapshot(request) -> bool:
    query = getattr(request, "query", {}) or {}
    return (
        str(query.get("protocol_version", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or str(query.get("snapshot_protocol", "") or "").strip()
        == COMPACT_SNAPSHOT_PROTOCOL
        or _truthy_compact_value(query.get("compact"))
    )


# ``deploy`` is intentionally listed here even though v1 does not implement a
# handler: the worker-context guard must still preemptively reject an in-daemon
# deploy attempt until a future deploy API exists.
_API_DAEMON_LIFECYCLE_COMMANDS = {"restart", "stop", "deploy"}
_DAEMON_STOP_RESULT_TYPE = "daemon_stop"
_DAEMON_STOP_TRIGGER_DELAY_SECONDS = 0.05


class _DaemonStopState:
    """Small shared state for graceful daemon-stop request draining."""

    def __init__(self) -> None:
        self.requested = False

    def request(self) -> bool:
        first_request = not self.requested
        self.requested = True
        return first_request

    def should_reject_api_request(self, cmd: str) -> bool:
        return self.requested and str(cmd or "").strip().lower() != "stop"


def _daemon_stop_result(*, already_requested: bool = False) -> dict:
    return {
        "type": _DAEMON_STOP_RESULT_TYPE,
        "message": (
            "Torque daemon stop already requested"
            if already_requested else
            "Torque daemon stopping"
        ),
    }


def _is_daemon_stop_result(result: dict | None) -> bool:
    return isinstance(result, dict) and result.get("type") == _DAEMON_STOP_RESULT_TYPE


def _daemon_stop_rejection_payload() -> dict:
    return {
        "ok": False,
        "error": "Torque daemon is stopping",
        "type": _DAEMON_STOP_RESULT_TYPE,
    }


async def _handle_daemon_stop_command(
    *,
    daemon_stop_state: _DaemonStopState,
    schedule_daemon_stop,
    state,
) -> dict:
    already_requested = not daemon_stop_state.request()
    if already_requested:
        log.info("Stop requested while daemon stop already pending")
    else:
        log.info("Stop requested — draining requests and shutting down")
        # Persist all agents (status etc.) before stop, mirroring restart.
        # Helper daemons are intentionally left running for PID-file adoption
        # by the next daemon; this matches current restart semantics and is
        # audited by TORQUE:358.
        for cell in list(state.agents.values()):
            try:
                state._db_save_agent(cell)
            except Exception:
                log.exception(
                    "Failed to persist agent '%s' before daemon stop",
                    getattr(cell, "id", ""),
                )

    # Schedule even after best-effort cleanup failures, and also on repeated
    # stop requests so a prior failed/cleared stop task cannot strand the
    # daemon in a requested-but-not-stopping state.
    schedule_daemon_stop()
    return _daemon_stop_result(already_requested=already_requested)


def _mark_shutdown_runtime_agents_stopped(state) -> int:
    """Mark live runtime cells stopped before daemon teardown.

    PTY shutdown can happen after websocket clients are gone (or, in
    supervisor mode, without terminating sessions at all). Persist the
    stopped status from the server shutdown path itself so the UI and SQLite
    do not retain a stale "running" status after the daemon exits.
    """
    iter_cells = getattr(state, "iter_active_agents", None)
    if callable(iter_cells):
        cells = list(iter_cells())
    else:
        cells = list(getattr(state, "agents", {}).values())

    stopped = 0
    for cell in cells:
        try:
            status = str(getattr(cell, "status", "") or "")
            session_id = getattr(cell, "session_id", None)
            if status not in {"running", "idle"} and not session_id:
                continue
            if status == "stopped" and not session_id:
                continue

            previous_session_id = session_id
            cell.status = "stopped"
            cell.session_id = None
            cell.current_process = ""
            cell.current_path = ""
            cell.current_branch = ""
            cell.git_root = ""
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False

            mark_heartbeat = getattr(state, "mark_agent_heartbeat", None)
            if callable(mark_heartbeat):
                mark_heartbeat(cell, emit=False)
            emit_agent = getattr(state, "_emit_agent", None)
            if callable(emit_agent):
                emit_agent(cell)
            save_agent = getattr(state, "_db_save_agent", None)
            if callable(save_agent):
                save_agent(cell)

            if (previous_session_id
                    and getattr(state, "active_session_id", None)
                    == previous_session_id):
                state.active_session_id = None
                emit = getattr(state, "_emit", None)
                if callable(emit):
                    emit(
                        "focus_update",
                        active_session_id=None,
                        current_window_id=(
                            getattr(state, "current_window_id", "")
                            or "standalone"
                        ),
                    )
            stopped += 1
        except Exception:
            log.exception(
                "Failed to mark agent '%s' stopped during daemon shutdown",
                getattr(cell, "id", ""),
            )
    return stopped


async def _shutdown_daemon_runtime(
    *,
    terminal_clients,
    ui_ws_clients,
    panel_log,
    event_ingest_drainer,
    event_ingest_client,
    cloud_connector_runtime=None,
    ai_index_service=None,
    ai_summary_service=None,
    bridge,
    runner,
    state,
    db,
) -> None:
    """Run the daemon shutdown drain sequence in one shared place."""
    stopped_count = _mark_shutdown_runtime_agents_stopped(state)
    if stopped_count:
        try:
            await state.broadcast()
        except Exception:
            log.exception("Shutdown stopped-status broadcast failed")
    shutdown_engineer_recompute = getattr(
        state, "shutdown_engineer_recompute", None,
    )
    if callable(shutdown_engineer_recompute):
        try:
            await shutdown_engineer_recompute()
        except Exception:
            log.exception("Engineer-stream recompute shutdown failed")
    for ws_clients in terminal_clients.values():
        for ws_client in list(ws_clients):
            try:
                await ws_client.close()
            except Exception:
                pass
    terminal_clients.clear()
    for ws_client in list(ui_ws_clients):
        try:
            await ws_client.close()
        except Exception:
            pass
    ui_ws_clients.clear()
    try:
        await panel_log.aclose()
    except Exception:
        log.exception("Panel event log shutdown flush failed")
    try:
        await event_ingest_drainer.stop()
    except Exception:
        log.exception("Event ingest drainer shutdown failed")
    try:
        await event_ingest_client.aclose()
    except Exception:
        log.exception("Event ingest client shutdown failed")
    try:
        await cloud_hooks.stop_cloud_connector(cloud_connector_runtime)
    except Exception:
        log.exception("Cloud connector shutdown drain failed")
    if ai_index_service is not None:
        try:
            await ai_index_service.shutdown()
        except Exception:
            log.exception("AI index service shutdown failed")
    if ai_summary_service is not None:
        try:
            await ai_summary_service.shutdown()
        except Exception:
            log.exception("AI summary service shutdown failed")
    try:
        await bridge.shutdown()
    except Exception:
        log.exception("Terminal adapter shutdown failed")
    try:
        await runner.cleanup()
    except Exception:
        log.exception("HTTP runner cleanup failed")
    try:
        await state.flush_db_writes()
        await db.close_async_writes()
    except Exception:
        log.exception("Async SQLite write queue shutdown failed")
    try:
        db.close()
    except Exception:
        log.exception("SQLite database close failed")



def _api_worker_context_guard(data: dict | None, headers=None,
                              remote: str = "") -> dict | None:
    data = data or {}
    cmd = str(data.get("cmd", "") or "").strip().lower()
    force = data.get("force")
    if (
            cmd not in _API_DAEMON_LIFECYCLE_COMMANDS
            or force is True
            or str(force or "").strip().lower() in {"1", "true", "yes", "on"}):
        return None
    headers = headers or {}
    cell_id = next((str(value).strip() for value in (
        headers.get("TORQUE_CELL_ID"),
        headers.get("X-Torque-Cell-Id"),
        data.get("TORQUE_CELL_ID"),
        data.get("torque_cell_id"),
        data.get("cell_id"),
    ) if str(value or "").strip()), "")
    if not cell_id:
        return None
    message = (
        f"Refusing HTTP /api/cmd {cmd} from Torque worker context "
        f"(TORQUE_CELL_ID={cell_id}). Restarting/stopping/deploying Torque "
        "from inside a live worker can corrupt dispatch state. If this is "
        "intentional, retry with force=true."
    )
    log.warning(
        "Rejected worker HTTP /api/cmd lifecycle request: cmd=%s "
        "cell_id=%s source=%s",
        cmd,
        cell_id,
        str(remote or "unknown").strip() or "unknown",
    )
    return {"message": message, "status": 403}


async def _send_ui_ws_json(ws, payload: dict) -> bool:
    if not ws or getattr(ws, "closed", False):
        return False
    try:
        await ws.send_str(await hot_json_dumps_async(payload))
        return True
    except Exception as exc:
        if _is_closing_ui_ws_error(exc):
            return False
        raise


async def _hot_json_response(
    payload: dict, *, status: int = 200
) -> web.Response:
    body = await hot_json_dumps_bytes_async(payload)
    return web.Response(
        body=body, status=status, content_type="application/json")


_UI_CLIENT_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]")


def _normalize_ui_client_id(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _UI_CLIENT_ID_RE.sub("", raw)[:96]


def _ui_client_id_from_request(request) -> str:
    try:
        return _normalize_ui_client_id(request.query.get("client_id", ""))
    except Exception:
        return ""


async def _register_ready_ui_ws_client(state: MatrixState, ws,
                                       payload_factory,
                                       client_id: str = "") -> bool:
    connect_started = time.perf_counter()
    async with state._ws_clients_lock:
        state._discard_ws_clients_locked({ws})
    while True:
        payload = payload_factory()
        if asyncio.iscoroutine(payload):
            payload = await payload
        if profiling.is_enabled() and payload.get("type") == "state":
            payload_bytes = len(json.dumps(payload).encode("utf-8"))
            profiling.recorder().observe("snapshot_json_bytes", payload_bytes)
        if not await _send_ui_ws_json(ws, payload):
            return False
        async with state._ws_clients_lock:
            if state._seq == int(payload.get("seq", 0) or 0):
                state._register_ws_client_locked(ws, client_id)
                profiling.recorder().incr("ws_connects")
                profiling.recorder().observe_ms(
                    "ws_connect_latency_ms",
                    time.perf_counter() - connect_started,
                )
                return True




async def _handle_send_text_command(data, state: MatrixState, send_prompt) -> bool:
    cell = state.agents.get(data.get("id"))
    return await _queue_cell_prompt_send(
        cell,
        data.get("text", ""),
        send_prompt,
    )


async def _handle_send_user_message_command(data, state: MatrixState,
                                            bridge) -> bool:
    cell_id = str(data.get("cell_id") or data.get("id") or "").strip()
    text = str(data.get("text") or "")
    if not cell_id or not text.strip():
        return False
    cell = state.agents.get(cell_id)
    if not cell or not getattr(cell, "session_id", ""):
        return False
    optimistic_baseline = state.snapshot_agent_optimistic_state(cell)
    optimistic_at = time.time()
    optimistic_marked = state.mark_agent_optimistic_running(
        cell,
        optimistic_at,
        emit=True,
        persist=False,
    )
    if optimistic_marked:
        await state.broadcast()
    try:
        await bridge.send_text(cell.session_id, text)
    except Exception:
        if (
            optimistic_marked
            and getattr(cell, "status", "") == "running"
            and not getattr(cell, "activity", "")
            and float(getattr(cell, "last_progress_at", 0) or 0) <= optimistic_at
        ):
            if state.restore_agent_optimistic_state(
                    cell,
                    optimistic_baseline,
                    emit=True,
                    persist=False):
                await state.broadcast()
        raise
    state.record_message_history(cell.id, text)
    return True


async def _handle_user_agent_message_command(data, state: MatrixState,
                                             send_prompt, *,
                                             restart_agent=None) -> dict:
    """Persist and non-interruptively deliver a user→agent direct message."""
    target_ident = str(
        data.get("agent_id")
        or data.get("cell_id")
        or data.get("target_agent_id")
        or ""
    ).strip()
    target_id = _resolve_agent_id(state, target_ident)
    target = state.get_active_agent(target_id) if target_id else None
    if not target or getattr(target, "cell_type", "") != "agent":
        return {
            "type": "error",
            "message": f"Agent not found: {target_ident}",
        }
    message_text = str(data.get("message") or data.get("text") or "")
    if not message_text.strip():
        return {"type": "error", "message": "Message is required"}
    if not getattr(state, "db", None):
        return {
            "type": "error",
            "message": "Direct message store is unavailable",
        }
    stripped_message = message_text.strip()
    command = parse_user_dm_command(stripped_message)
    if bool(data.get("_loop_delivery")) and command and command.id == "fast":
        # A scheduled natural-language message must not repeatedly toggle a
        # provider mode merely because its body happens to equal this command.
        command = None
    if command:
        # Registry-owned recognition keeps these trusted convenience actions
        # closed: unsupported slash-like prose still follows normal delivery.
        command_handlers = {
            "loop": lambda: _handle_user_agent_loop_command(
                data, state, target, stripped_message),
            "loop-cancel": lambda: _handle_user_agent_loop_command(
                data, state, target, stripped_message),
            "restart": lambda: _handle_user_agent_restart_command(
                data, state, target, restart_agent),
            "status": lambda: _user_agent_read_only_command_response(
                data, state, target, "status"),
            "usage": lambda: _user_agent_read_only_command_response(
                data, state, target, "usage"),
            "commands": lambda: _user_agent_read_only_command_response(
                data, state, target, "commands"),
            "watch": lambda: _handle_task_watch_command(data, state, target, stripped_message, "watch"),
            "watches": lambda: _handle_task_watch_command(data, state, target, stripped_message, "watches"),
            "unwatch": lambda: _handle_task_watch_command(data, state, target, stripped_message, "unwatch"),
            "remind": lambda: _handle_reminder_command(data, state, target, stripped_message, "remind"),
            "remind-cancel": lambda: _handle_reminder_command(data, state, target, stripped_message, "remind"),
            "reminders": lambda: _handle_reminder_command(data, state, target, stripped_message, "reminders"),
        }
        handler = command_handlers.get(command.id)
        if handler and not (
            bool(data.get("_loop_delivery"))
                and command.id in {"restart", "status", "usage", "commands", "watch", "watches", "unwatch", "remind", "remind-cancel", "reminders"}
        ):
            result = handler()
            if inspect.isawaitable(result):
                return await result
            return result
        if not user_dm_command_supports_provider(
                command, getattr(target, "agent_type", "")):
            return _user_agent_unsupported_command_response(
                data, state, target, command.id)

    idempotency_key = _user_agent_message_idempotency_key(data)
    message_id = _user_direct_message_id_from_idempotency_key(idempotency_key)
    if not message_id:
        message_id = "msg-" + uuid.uuid4().hex[:12]
    reply_to_id = str(data.get("reply_to_id", "") or "").strip()
    requested_thread_id = str(data.get("thread_id", "") or "").strip()
    thread_id = requested_thread_id or canonical_user_agent_thread_id(target.id)
    recipient_kind = str(getattr(target, "kind", "") or "").strip() or "worker"
    recipient_name = str(getattr(target, "name", "") or "").strip()
    message_type = "message"
    context_snapshot = {}
    if command and command.execution_mode == "provider_passthrough":
        # Only closed-registry commands that passed the provider capability
        # check above reach this literal provider-session delivery path.
        message_text = command.insert
        message_type = "slash_command"
        context_snapshot = {"slash_command": command.id}
    elif bool(data.get("_loop_delivery")):
        message_type = "loop"
        context_snapshot = {
            "loop_id": str(data.get("_loop_id", "") or "").strip(),
        }

    existing = (
        state.db.load_direct_message(message_id)
        if idempotency_key and getattr(state, "db", None)
        else None
    )
    if existing:
        if _user_agent_message_conflicts_with_existing(
                existing,
                target,
                message=message_text,
                reply_to_id=reply_to_id):
            return {
                "type": "error",
                "message": (
                    "idempotency key was reused for a different "
                    "user_agent_message"
                ),
            }
        append_direct = getattr(state, "append_direct_message_to_caches", None)
        if callable(append_direct):
            append_direct(existing)
        return _direct_message_delivery_response(existing, deduped=True)

    row = {
        "id": message_id,
        "thread_id": thread_id,
        "reply_to_id": reply_to_id,
        "idempotency_key": idempotency_key,
        "group_name": str(getattr(target, "group", "") or "").strip(),
        "sender_id": "user",
        "sender_kind": "user",
        "sender_name": "User",
        "recipient_id": target.id,
        "recipient_kind": recipient_kind,
        "recipient_name": recipient_name,
        "message": message_text,
        "message_type": message_type,
        "created_at": time.time(),
        "blocking": False,
        "context_snapshot": context_snapshot,
        "delivery_state": "buffered",
        "delivery_reason": "",
    }
    saved = state.save_direct_message(row)
    if not saved:
        return {
            "type": "error",
            "message": "Failed to save direct message",
        }
    delivered = await _queue_user_direct_message_to_agent(
        state,
        target,
        saved,
        send_prompt,
        emit=True,
    )
    return _direct_message_delivery_response(delivered or saved)


async def _handle_user_agent_turn_cancel_command(data, state: MatrixState,
                                                  send_prompt) -> dict:
    """Bounded operator cancellation for one durable, user-originated DM."""
    target_id = _resolve_agent_id(state, str(data.get("agent_id") or "").strip())
    target = state.get_active_agent(target_id) if target_id else None
    if not target or getattr(target, "cell_type", "") != "agent":
        return {"type": "error", "message": "Agent is no longer available"}
    session_id = str(data.get("session_id") or "").strip()
    source_key = str(data.get("turn_idempotency_key") or "").strip()
    cancel_key = _user_agent_message_idempotency_key(data)
    message_id = _user_direct_message_id_from_idempotency_key(source_key)
    if not session_id or not message_id or not cancel_key:
        return {"type": "error", "message": "A current submitted message is required"}
    source = state.db.load_direct_message(message_id) if getattr(state, "db", None) else None
    if not source or (
            str(source.get("sender_kind", "")) != "user"
            or str(source.get("recipient_id", "")) != target.id
            or str(source.get("idempotency_key", "")) != source_key
            or str(source.get("message_type", "")) not in {"message", "slash_command"}):
        return {"type": "error", "message": "That submitted message is not cancellable"}
    audit_id = _user_direct_message_id_from_idempotency_key(cancel_key)
    existing = state.db.load_direct_message(audit_id)
    if existing:
        snapshot = existing.get("context_snapshot", {}) or {}
        if (str(existing.get("sender_kind", "")) != "system"
                or str(snapshot.get("cancelled_message_id", "")) != message_id
                or str(snapshot.get("cancel_session_id", "")) != session_id):
            return {"type": "error", "message": "Cancellation retry key conflicts"}
        return {"type": "ok", "outcome": snapshot.get("cancel_outcome", "no_active_turn"),
                "message_id": audit_id, "deduped": True}
    cancel = getattr(send_prompt, "cancel_user_direct_turn", None)
    if not callable(cancel):
        outcome = "unsupported_provider"
    else:
        result = await cancel(target, message_id=message_id, session_id=session_id)
        outcome = str((result or {}).get("outcome", "interrupt_failed"))
    labels = {
        "cancelled_queued": "Cancelled the queued user message before provider delivery.",
        "interrupted": "Interrupted the active user message turn.",
        "unsupported_provider": "This provider cannot safely interrupt the active turn.",
        "session_replaced": "The target session changed; no turn was interrupted.",
        "no_active_turn": "No active turn remains for that submitted message.",
        "interrupt_failed": "Could not interrupt the active turn; it was left unchanged.",
    }
    if outcome == "cancelled_queued":
        state.update_direct_message_delivery(message_id, "cancelled",
                                             reason="cancelled_by_user", emit=True)
    audit = _save_user_agent_system_audit_message(
        state, target, labels.get(outcome, labels["interrupt_failed"]),
        message_id=audit_id, idempotency_key=cancel_key,
        context_snapshot={"cancelled_message_id": message_id,
                          "cancel_session_id": session_id,
                          "cancel_outcome": outcome},
    )
    if not audit:
        return {"type": "error", "message": "Failed to record cancellation outcome"}
    return {"type": "ok", "outcome": outcome, "message_id": audit_id,
            "deduped": False}


async def _deliver_engineer_reply_and_resume(state: MatrixState, engineer, *,
                                           group: str,
                                           answer: str,
                                           send_prompt,
                                           engineer_buffer) -> dict:
    ws = state.get_engineer_settings(group)
    question = str(getattr(ws, "pending_question", "") or "").strip()
    author_cell_id = (
        str(getattr(ws, "pending_question_actor_id", "") or "").strip()
        or str(getattr(engineer, "id", "") or "").strip()
    )
    try:
        question_timestamp = float(
            getattr(ws, "pending_question_set_at", 0) or 0
        )
    except (TypeError, ValueError):
        question_timestamp = 0.0
    formatted = (
        "\n"
        "## Human Reply\n"
        f"{answer}\n"
        "---\n"
    )
    reply_row = None
    if question:
        source_key = direct_ask_mirror_source_key(
            group=group,
            agent_id=author_cell_id or str(getattr(engineer, "id", "") or ""),
            timestamp=question_timestamp,
            question=question,
        )
        reply_row = save_direct_ask_reply_mirror(
            state,
            engineer,
            answer,
            question=question,
            source_key=source_key,
            created_at=time.time(),
            delivery_state="buffered",
        )
    if reply_row:
        await _queue_user_direct_message_to_agent(
            state,
            engineer,
            reply_row,
            send_prompt,
            emit=True,
        )
    else:
        await _queue_cell_prompt_send(
            engineer,
            formatted,
            send_prompt,
            prime_input_ready=True,
            wait_for_delivery=True,
        )
    await state.update_engineer_settings_async(
        group,
        pending_question="",
        paused=False,
        _pending_question_actor_id=getattr(engineer, "id", "") or "",
    )
    engineer_buffer.on_delivery_resumed(group)
    if question:
        _append_engineer_journal_entry(
            state,
            group,
            "qa",
            f"Question:\n{question}\n\nAnswer:\n{str(answer or '').strip()}",
            author_cell_id=author_cell_id,
            source_key=_engineer_journal_source_key(
                "qa",
                group,
                author_cell_id,
                question_timestamp,
                question,
            ),
        )
        if not reply_row:
            asking_agent = state.agents.get(author_cell_id) or engineer
            source_key = direct_ask_mirror_source_key(
                group=group,
                agent_id=(
                    author_cell_id
                    or str(getattr(engineer, "id", "") or "")
                ),
                timestamp=question_timestamp,
                question=question,
            )
            save_direct_ask_reply_mirror(
                state,
                asking_agent,
                answer,
                question=question,
                source_key=source_key,
                created_at=time.time(),
            )
    state.journal_append(
        group,
        "observation",
        f"Human replied: {answer}",
    )
    return {"type": "ok"}


def _pending_question_reply_target(state: MatrixState, group: str):
    """Return the agent that should receive a human reply for pending_question.

    Actor-scoped engineer asks should reply to the engineer that asked the
    question. Legacy rows without an actor fall back to the group engineer.
    """
    ws = state.get_engineer_settings(group)
    actor_id = str(
        getattr(ws, "pending_question_actor_id", "") or ""
    ).strip()
    if actor_id:
        return state.agents.get(actor_id), "Engineer"
    return state.get_engineer_for_group(group), "Engineer"


def _sanitize_engineer_worker_provider_override(
    state: MatrixState,
    group: str,
    data: dict,
    requested_provider: str,
) -> str:
    """Return allowed worker provider override or '' to use group defaults."""
    requested_provider = str(requested_provider or "").strip()
    if not requested_provider:
        return ""
    engineer_id = str(data.get("_engineer_dispatch_id", "") or "").strip()
    if not engineer_id:
        return requested_provider
    settings = state.get_engineer_settings(group)
    if getattr(settings, "engineer_can_override_worker_provider", True):
        return requested_provider
    log.warning(
        "Engineer %s attempted worker provider override '%s' in group %s "
        "while provider overrides are disabled; falling back to group default",
        engineer_id,
        requested_provider,
        group,
    )
    return ""


def _worker_provider_override_from_dispatch(data: dict) -> str:
    """Return requested worker provider override from new/legacy API names."""
    provider = str(data.get("provider", "") or "").strip()
    agent_type = str(data.get("agent_type", "") or "").strip()
    if provider and agent_type and provider != agent_type:
        raise ValueError(
            "provider and agent_type overrides disagree; use one provider "
            "value for the new worker"
        )
    return provider or agent_type


def _resolve_ai_report_task(state: MatrixState, cell, *,
                            task_id: str = "") -> Optional[BoardTask]:
    """Resolve the task an agent report should apply to.

    Prefer an explicit task id. Otherwise ignore stale ``current_task_id``
    pointers that no longer occupy the agent's live execution slot, and fall
    back to the state-derived active task before using the older linked-task
    heuristic.
    """
    if not cell:
        return None
    if task_id:
        return state.board_tasks.get(_resolve_task_id(state, task_id))
    if cell.current_task_id:
        current = state.board_tasks.get(cell.current_task_id)
        if state.task_occupies_execution_slot(current, agent_id=cell.id):
            return current
    current = state.agent_current_task(cell.id)
    if current:
        return current
    linked = [
        t for t in state.board_tasks.values()
        if t.agent_id == cell.id
        and t.lane not in ("Done", "Backlog", ARCHIVED_LANE)
    ]
    if len(linked) == 1:
        return linked[0]
    return None


async def _handle_doctor_command(db: TorqueDB, bridge=None) -> dict:
    # build_doctor_report probes the PTY supervisor socket (a bounded but
    # blocking call), so run it off the event loop on a fresh read-only
    # connection rather than stalling the daemon — and never reuse db._conn
    # across the worker thread.
    report = await asyncio.to_thread(
        build_doctor_report_for_db, db.db_path, runtime_python=sys.executable)
    # Inject live runtime state the offline DB can't know: sessions whose
    # input-write circuit breaker is open (agents that stopped draining stdin).
    snapshot = getattr(bridge, "supervisor_write_breaker_snapshot", None)
    if callable(snapshot):
        try:
            breakers = dict(snapshot() or {})
        except Exception:
            breakers = {}
        sup = report.setdefault("pty_supervisor", {})
        sup["open_write_breakers"] = breakers
        sup["stuck_sessions"] = len(breakers)
    sup = report.setdefault("pty_supervisor", {})
    projection = build_supervisor_health_projection(
        bridge,
        profile_skip_pty=bool(getattr(torque_config, "PROFILE_SKIP_PTY", False)),
    )
    sup["health"] = projection
    sup["state"] = projection.get("state")
    sup["connected"] = projection.get("connected")
    sup["reconnect_count"] = projection.get("reconnect_count", 0)
    sup["last_reconnect_at"] = projection.get("last_reconnect_at")
    sup["last_op_latency_ms"] = projection.get("last_op_latency_ms")
    sup["time_since_last_successful_op"] = projection.get(
        "time_since_last_successful_op")
    live_metrics = getattr(bridge, "supervisor_metrics", None)
    if callable(live_metrics):
        try:
            metrics = await live_metrics()
            if isinstance(metrics, dict):
                sup["metrics"] = dict(metrics)
        except Exception:
            log.debug("Doctor supervisor metrics refresh failed", exc_info=True)
    return report


_INTERNAL_FAILED_WRITE_PREFIX = "internal:"
_NO_COMMAND_RECEIPT = object()
_CRITICAL_BOARD_COMMANDS = {
    "blocked_task_reply",
    "architect_task_update",
    "board_add_task",
    "board_amend_task",
    "board_update_task",
    "board_move_task",
    "board_archive_task",
    "board_archive_tasks",
    "board_unarchive_task",
    "board_verify_task",
    "board_pickup_architect_task",
    "board_remove_task",
}
_CRITICAL_AI_REPORT_ACTIONS = {
    "done",
    "blocked",
    "error",
    "ask",
    "derive",
    "ready",
    "verify",
    "name",
    "reply",
}


def _internal_failed_write_key(idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    return f"{_INTERNAL_FAILED_WRITE_PREFIX}{key}" if key else ""


def _critical_command_name(data: dict) -> str:
    cmd = str((data or {}).get("cmd", "") or "").strip()
    if cmd == "ai_report":
        action = str((data or {}).get("action", "") or "").strip()
        if action in _CRITICAL_AI_REPORT_ACTIONS:
            return f"ai_report:{action}"
        return ""
    if cmd in _CRITICAL_BOARD_COMMANDS:
        return cmd
    if cmd == "architect_journal_append":
        return cmd
    return ""


def _critical_command_needs_capture(data: dict) -> bool:
    cmd = str((data or {}).get("cmd", "") or "").strip()
    return cmd == "ai_report" or cmd in _CRITICAL_BOARD_COMMANDS


def _critical_command_caller_id(data: dict) -> str:
    for key in ("cell_id", "architect_id", "agent_id", "id"):
        value = str((data or {}).get(key, "") or "").strip()
        if value:
            return value
    return ""


def _critical_command_conflict_result(command_name: str) -> dict:
    return {
        "type": "error",
        "message": (
            "idempotency key was reused for a different internal command "
            f"({command_name or 'unknown'})"
        ),
    }


def _load_internal_command_receipt(
    db: TorqueDB | None,
    payload: dict,
) -> tuple[object, str, str]:
    key = str((payload or {}).get("idempotency_key", "") or "").strip()
    command_name = _critical_command_name(payload)
    if not db or not key or not command_name:
        return _NO_COMMAND_RECEIPT, "", ""
    request_hash = api_request_hash(payload)
    existing = db.load_command_receipt(key)
    if not existing:
        return _NO_COMMAND_RECEIPT, key, request_hash
    existing_hash = str(existing.get("request_hash", "") or "").strip()
    if existing_hash and existing_hash != request_hash:
        return _critical_command_conflict_result(command_name), key, request_hash
    return existing.get("response"), key, request_hash


async def replay_internal_failed_write_payload(
    db: TorqueDB,
    payload: dict,
    handle_command,
):
    """Replay one queued critical internal command using command receipts."""
    cached, _key, _request_hash = _load_internal_command_receipt(db, payload)
    if cached is not _NO_COMMAND_RECEIPT:
        return cached
    result = await handle_command(payload)
    # Mirror handle_command's deliverable_missing semantics: surface as a
    # semantic failure so the replay caller doesn't treat the refusal as
    # success. The receipt-save path inside handle_command already
    # avoids persisting a command receipt for this result type, so a
    # subsequent retry after the worker uploads an artifact will re-run
    # the gate cleanly.
    if isinstance(result, dict) and result.get("type") == "deliverable_missing":
        return {
            "ok": False,
            "type": "deliverable_missing",
            "error": result.get(
                "message",
                "Deliverable artifact required before completion.",
            ),
        }
    if isinstance(result, dict) and result.get("type") == "review_required":
        # Mirror deliverable_missing replay semantics for the
        # mandatory-review gate (TORQUE:256). Don't cache the refusal.
        return {
            "ok": False,
            "type": "review_required",
            "error": result.get(
                "message",
                "Review required by action contract before completion.",
            ),
        }
    return result


async def replay_api_failed_write_payload(
    db: TorqueDB,
    payload: dict,
    handle_command,
):
    """Replay one queued /api/cmd write with live API idempotency semantics."""
    payload = dict(payload or {})
    cmd = str(payload.get("cmd", "") or "")
    idempotency_key = str(payload.get("idempotency_key", "") or "").strip()
    request_hash = ""
    if idempotency_key and is_api_write_command(cmd):
        request_hash = api_request_hash(payload)
        existing = db.load_mcp_idempotency(idempotency_key)
        if existing:
            existing_hash = str(existing.get("request_hash", "") or "")
            if existing_hash and existing_hash != request_hash:
                db.record_mcp_health_event(
                    surface="api",
                    tool_name=cmd,
                    event="idempotency_conflict",
                )
                return {
                    "ok": False,
                    "error": (
                        "idempotency key was reused for a different "
                        f"API command ({cmd})"
                    ),
                }
            try:
                cached = json.loads(existing.get("response_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                cached = {}
            db.record_mcp_health_event(
                surface="api",
                tool_name=cmd,
                event="dedupe",
            )
            return cached

    result = await handle_command(payload)
    # Mirror the direct /api/cmd hard-gate semantics: a deliverable_missing
    # refusal is a semantic failure, not a successful write. Surface it as
    # an error envelope and DO NOT save it as an idempotency response —
    # otherwise a later same-key retry (after the artifact is uploaded)
    # would be deduped to the cached refusal instead of re-running the
    # gate. Same shape as handle_api_cmd's deliverable_missing branch.
    if result and result.get("type") == "deliverable_missing":
        return {
            "ok": False,
            "type": "deliverable_missing",
            "error": result.get(
                "message",
                "Deliverable artifact required before completion.",
            ),
        }
    if result and result.get("type") == "review_required":
        # Mirror deliverable_missing semantics: review_required is a
        # recoverable refusal — don't cache it as an idempotency response,
        # so a same-key retry after the worker derives the review re-runs
        # the gate (TORQUE:256).
        return {
            "ok": False,
            "type": "review_required",
            "error": result.get(
                "message",
                "Review required by action contract before completion.",
            ),
        }
    if idempotency_key and is_api_write_command(cmd):
        db.save_mcp_idempotency(
            idempotency_key=idempotency_key,
            surface="api",
            tool_name=cmd,
            request_hash=request_hash or api_request_hash(payload),
            response={"ok": True, "data": result if result else {}},
        )
    return result


from .server_prompt_context import (
    _agent_kind_for_context,
    _agent_is_worker_for_role_preamble,
    _agent_role_slug,
    _agent_owner_engineer_name,
    _owner_is_user_from_ids,
    _agent_owner_is_user,
    _normalize_prompt_block,
    _assemble_worker_prompt,
    _build_torque_context,
    _resolve_memory_cell_and_task,
    _resolve_memory_scope_ref,
    _resolve_memory_link_ref,
)

from .server_evidence import (
    _apply_verification_report,
    _completion_evidence_text,
    _task_verification_evidence,
    _task_artifact_evidence,
    _completion_evidence_status,
    _merge_completion_evidence,
    _save_completion_evidence_task,
    _task_tests_run_completion_evidence,
    _covering_task_final_ship_evidence,
    _auto_resolve_product_proposal_roots_for_covering_task,
    _auto_resolve_product_proposal_roots_and_enqueue,
    _task_text_field,
    _proposal_root_backlog_hygiene_item,
    _proposal_root_backlog_hygiene_inventory,
    _proposal_root_backlog_hygiene_authorized_for_architect,
    _finalize_already_covered_proposal_roots,
    _record_task_completion_evidence_snapshot,
    _origin_verification_evidence,
    _merge_evidence_matches_boundary,
    _record_merge_completion_evidence,
)











































































from .server_command_reads import (
    _handle_task_detail_command,
    _handle_agent_message_history_command,
    _handle_decisions_snapshot_command,
    _handle_pending_hires_snapshot_command,
    _handle_archived_tasks_command,
    _architect_ui_tool_is_read,
    _handle_engineer_journal_snapshot_command,
    _handle_architect_journal_read_command,
    _event_ingest_config_payload,
    _configure_event_ingest_client,
    _mcp_observation_event_id,
    _record_mcp_call_observation,
    _mcp_call_rows_for_ui,
    _engineer_mcp_visible_cell_ids,
    _architect_mcp_visible_cell_ids,
    _parse_mcp_call_query_params,
    _handle_mcp_calls_command,
)

from .server_agent_lifecycle import (
    _handle_engineer_dismiss_command,
    _handle_engineer_rehire_command,
    _handle_architect_dismiss_command,
    _handle_architect_rehire_command,
    _handle_delete_engineer_command,
    _handle_rename_engineer_command,
    _handle_delete_architect_command,
    _dispatch_architect_ui_tool,
    _handle_relaunch_agent_command,
    _handle_restart_agent_command,
)

def _build_engineer_operation_runtime(
    deliver_engineer_reply_and_resume,
    panel_event,
    send_agent_prompt,
    send_engineer_message_to_agent,
    bridge,
    critical_idempotency_key,
    critical_request_hash,
    db,
    engineer_buffer,
    state,
) -> EngineerOperationRuntime:
    """Compose runtime integrations for Engineer operations."""
    return EngineerOperationRuntime(
        agent_dismissed_at=_agent_dismissed_at,
        architect_dismissed_error=_architect_dismissed_error,
        deliver_engineer_reply_and_resume=deliver_engineer_reply_and_resume,
        format_injected_mcp_message_prompt=_format_injected_mcp_message_prompt,
        handle_digest_pause_resume_command=_handle_digest_pause_resume_command,
        handle_engineer_dismiss_note_command=_handle_engineer_dismiss_note_command,
        handle_engineer_flush_now_command=_handle_engineer_flush_now_command,
        panel_event=panel_event,
        pending_question_reply_target=_pending_question_reply_target,
        resolve_agent_id=_resolve_agent_id,
        send_agent_prompt=send_agent_prompt,
        send_engineer_message_to_agent=send_engineer_message_to_agent,
        agent_identity_anchor=agent_identity_anchor,
        bridge=bridge,
        build_engineer_session_map=build_engineer_session_map,
        critical_idempotency_key=critical_idempotency_key,
        critical_request_hash=critical_request_hash,
        db=db,
        direct_ask_mirror_source_key=direct_ask_mirror_source_key,
        engineer_buffer=engineer_buffer,
        save_direct_ask_mirror=save_direct_ask_mirror,
        send_optimistic_agent_text=send_optimistic_agent_text,
        state=state,
    )


def _build_board_operation_runtime(
    panel_event,
    resolve_base_dir,
    resolve_deliverable_for_create,
    board_sync_manager,
    handle_command,
    state,
) -> BoardOperationRuntime:
    """Compose runtime integrations for board and external-ticket commands."""
    return BoardOperationRuntime(
        ATTACHMENTS_DIR=ATTACHMENTS_DIR,
        ExternalTicketError=ExternalTicketError,
        apply_verification_report=_apply_verification_report,
        capture_auto_resume_targets=_capture_auto_resume_targets,
        emit_task_artifact_uploaded_event=_emit_task_artifact_uploaded_event,
        engineer_tombstoned_error=_engineer_tombstoned_error,
        finalize_already_covered_proposal_roots=(
            _finalize_already_covered_proposal_roots
        ),
        handle_board_archive_command=_handle_board_archive_command,
        handle_board_archive_tasks_command=_handle_board_archive_tasks_command,
        handle_board_unarchive_command=_handle_board_unarchive_command,
        handle_workflow_breach_command=_handle_workflow_breach_command,
        maybe_auto_resume_targets=_maybe_auto_resume_targets,
        panel_event=panel_event,
        record_task_completion_evidence_snapshot=(
            _record_task_completion_evidence_snapshot
        ),
        resolve_base_dir=resolve_base_dir,
        resolve_deliverable_for_create=resolve_deliverable_for_create,
        resolve_task_id=_resolve_task_id,
        task_upload_actor_source=_task_upload_actor_source,
        task_upload_engineer_scope_error=_task_upload_engineer_scope_error,
        board_sync_manager=board_sync_manager,
        build_completion_comment=build_completion_comment,
        finalize_task_attachments=finalize_task_attachments,
        handle_command=handle_command,
        import_external_ticket=import_external_ticket,
        is_canonical_task_id=is_canonical_task_id,
        is_draft_task_token=is_draft_task_token,
        normalize_artifacts=normalize_artifacts,
        normalize_external_link=normalize_external_link,
        open_ticket_url=open_ticket_url,
        post_ticket_comment=post_ticket_comment,
        push_ticket_status=push_ticket_status,
        remove_task_owned_artifacts_by_filename=(
            remove_task_owned_artifacts_by_filename
        ),
        serialize_task_artifact=serialize_task_artifact,
        state=state,
        store_task_upload=store_task_upload,
        task_counts_as_done=task_counts_as_done,
        task_is_closed=task_is_closed,
    )


def _build_agent_operation_runtime(
    apply_persistent_prompt,
    build_cell_persistent_prompt,
    is_designated_engineer,
    panel_event,
    persistent_prompt_filename,
    resolve_base_dir,
    resolve_agent_launch_config,
    resolve_architect_launch_config,
    resolve_engineer_launch_config,
    resolve_worker_launch_config,
    suggest_template_agent_name,
    create_agent_with_config,
    create_child_terminals,
    send_agent_prompt,
    restart_agent_session,
    cleanup_purged_agents,
    close_agent_session_only,
    bridge,
    engineer_buffer,
    handle_command,
    specialization_mgr,
    state,
    worktree_mgr,
) -> AgentOperationRuntime:
    """Compose integrations for agent lifecycle and session commands."""
    return AgentOperationRuntime(
        apply_persistent_prompt=apply_persistent_prompt,
        build_cell_persistent_prompt=build_cell_persistent_prompt,
        cleanup_purged_agents=cleanup_purged_agents,
        close_agent_session_only=close_agent_session_only,
        create_agent_with_config=create_agent_with_config,
        create_child_terminals=create_child_terminals,
        dispatch_architect_ui_tool=_dispatch_architect_ui_tool,
        handle_add_architect_command=_handle_add_architect_command,
        handle_add_engineer_command=_handle_add_engineer_command,
        handle_add_worker_command=_handle_add_worker_command,
        handle_agent_class_launch_command=_handle_agent_class_launch_command,
        handle_architect_dismiss_command=_handle_architect_dismiss_command,
        handle_architect_engineer_hire_command=(
            _handle_architect_engineer_hire_command
        ),
        handle_architect_rehire_command=_handle_architect_rehire_command,
        handle_delete_architect_command=_handle_delete_architect_command,
        handle_delete_engineer_command=_handle_delete_engineer_command,
        handle_engineer_dismiss_command=_handle_engineer_dismiss_command,
        handle_engineer_rehire_command=_handle_engineer_rehire_command,
        handle_pending_hire_approve_command=(
            _handle_pending_hire_approve_command
        ),
        handle_pending_hire_list_command=_handle_pending_hire_list_command,
        handle_pending_hire_reject_command=_handle_pending_hire_reject_command,
        handle_purge_agent_now_command=_handle_purge_agent_now_command,
        handle_recently_deleted_agents_command=(
            _handle_recently_deleted_agents_command
        ),
        handle_relaunch_agent_command=_handle_relaunch_agent_command,
        handle_remove_agent_command=_handle_remove_agent_command,
        handle_rename_engineer_command=_handle_rename_engineer_command,
        handle_restart_agent_command=_handle_restart_agent_command,
        handle_restore_agent_command=_handle_restore_agent_command,
        handle_send_text_command=_handle_send_text_command,
        handle_send_user_message_command=_handle_send_user_message_command,
        handle_set_engineer_specializations_command=(
            _handle_set_engineer_specializations_command
        ),
        handle_user_agent_message_command=_handle_user_agent_message_command,
        handle_user_agent_turn_cancel_command=_handle_user_agent_turn_cancel_command,
        is_designated_engineer=is_designated_engineer,
        new_agent_prompt_sequence=_new_agent_prompt_sequence,
        normalize_engineer_specialization_selection=(
            _normalize_engineer_specialization_selection
        ),
        normalize_ui_client_id=_normalize_ui_client_id,
        panel_event=panel_event,
        persistent_prompt_filename=persistent_prompt_filename,
        project_specialization_names=_project_specialization_names,
        resolve_agent_launch_config=resolve_agent_launch_config,
        resolve_architect_cell=_resolve_architect_cell,
        resolve_architect_launch_config=resolve_architect_launch_config,
        resolve_base_dir=resolve_base_dir,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        resolve_pending_engineer_specializations=(
            _resolve_pending_engineer_specializations
        ),
        resolve_worker_launch_config=resolve_worker_launch_config,
        restart_agent_session=restart_agent_session,
        send_agent_prompt=send_agent_prompt,
        should_show_guidance_hint=_should_show_guidance_hint,
        startup_prompt_for_new_agent=_startup_prompt_for_new_agent,
        suggest_template_agent_name=suggest_template_agent_name,
        bridge=bridge,
        engineer_buffer=engineer_buffer,
        handle_command=handle_command,
        specialization_mgr=specialization_mgr,
        state=state,
        worktree_mgr=worktree_mgr,
    )


def _build_direct_command_runtime(
    compute_worktree_streams,
    current_board_tasks_for_agent,
    enrich_history_record,
    history_records_with_live_agents,
    live_history_record,
    prefill_branch_exists_for_state,
    relay_settings_fingerprint,
    resolve_base_dir,
    restart_cloud_connector,
    bridge,
    catalog_command_runtime,
    cloud_connector_runtime_holder,
    db,
    event_ingest_client,
    event_log,
    panel_log,
    specialization_mgr,
    state,
    template_mgr,
    action_mgr,
    sort_history_records,
) -> DirectCommandRuntime:
    """Compose integrations for non-broadcast command responses."""
    return DirectCommandRuntime(
        DATA_DIR=DATA_DIR,
        agent_overrides_from_role_settings=_agent_overrides_from_role_settings,
        architect_deploy_state_payload=architect_deploy_state_payload,
        build_ai_settings_response=_build_ai_settings_response,
        build_group_system_prompt_preview=_build_group_system_prompt_preview,
        current_board_tasks_for_agent=current_board_tasks_for_agent,
        compute_worktree_streams=compute_worktree_streams,
        dispatch_architect_ui_tool=_dispatch_architect_ui_tool,
        enrich_history_record=enrich_history_record,
        handle_agent_message_history_command=(
            _handle_agent_message_history_command
        ),
        handle_architect_journal_read_command=(
            _handle_architect_journal_read_command
        ),
        handle_archived_tasks_command=_handle_archived_tasks_command,
        handle_decisions_snapshot_command=_handle_decisions_snapshot_command,
        handle_doctor_command=_handle_doctor_command,
        handle_engineer_journal_snapshot_command=(
            _handle_engineer_journal_snapshot_command
        ),
        handle_mcp_calls_command=_handle_mcp_calls_command,
        handle_pending_hires_snapshot_command=(
            _handle_pending_hires_snapshot_command
        ),
        handle_task_detail_command=_handle_task_detail_command,
        history_records_with_live_agents=history_records_with_live_agents,
        live_history_record=live_history_record,
        prefill_branch_exists_for_state=prefill_branch_exists_for_state,
        preview_architect_settings_for_prompt=(
            _preview_architect_settings_for_prompt
        ),
        preview_engineer_settings_for_prompt=(
            _preview_engineer_settings_for_prompt
        ),
        preview_group_settings_for_prompt=_preview_group_settings_for_prompt,
        relay_settings_fingerprint=relay_settings_fingerprint,
        resolve_base_dir=resolve_base_dir,
        restart_cloud_connector=restart_cloud_connector,
        runtime_payload=_runtime_payload,
        sort_history_records=sort_history_records,
        action_mgr=action_mgr,
        bridge=bridge,
        catalog_command_runtime=catalog_command_runtime,
        cloud_connector_runtime_holder=cloud_connector_runtime_holder,
        db=db,
        event_ingest_client=event_ingest_client,
        event_log=event_log,
        panel_log=panel_log,
        specialization_mgr=specialization_mgr,
        state=state,
        template_mgr=template_mgr,
    )


def _build_prompt_preview_runtime(
    build_postscript,
    resolve_base_dir,
    action_mgr,
    state,
    template_mgr,
) -> PromptPreviewRuntime:
    """Compose integrations for deterministic worker prompt previews."""
    return PromptPreviewRuntime(
        assemble_worker_prompt=_assemble_worker_prompt,
        behavior_overlay_prompt_block_for_cell=(
            _behavior_overlay_prompt_block_for_cell
        ),
        build_postscript=build_postscript,
        build_torque_context=_build_torque_context,
        resolve_base_dir=resolve_base_dir,
        action_mgr=action_mgr,
        state=state,
        template_mgr=template_mgr,
    )


def _build_runtime_settings_command_runtime(
    persistent_prompt_filename,
    relay_settings_fingerprint,
    restart_cloud_connector,
    safe_remove_worktree,
    ai_index_service,
    ai_summary_service,
    bridge,
    db,
    event_bus,
    event_ingest_client,
    event_ingest_configured,
    panel_log,
    state,
    worktree_mgr,
) -> RuntimeSettingsCommandRuntime:
    """Compose integrations for group and live settings changes."""
    return RuntimeSettingsCommandRuntime(
        apply_ai_settings_update_command=_apply_ai_settings_update_command,
        build_ai_settings_response=_build_ai_settings_response,
        configure_event_ingest_client=_configure_event_ingest_client,
        persistent_prompt_filename=persistent_prompt_filename,
        relay_settings_fingerprint=relay_settings_fingerprint,
        restart_cloud_connector=restart_cloud_connector,
        safe_remove_worktree=safe_remove_worktree,
        ai_index_service=ai_index_service,
        ai_summary_service=ai_summary_service,
        bridge=bridge,
        db=db,
        event_bus=event_bus,
        event_ingest_client=event_ingest_client,
        event_ingest_configured=event_ingest_configured,
        panel_log=panel_log,
        state=state,
        worktree_mgr=worktree_mgr,
    )


def _build_task_dispatch_runtime(
    build_dispatch_persistent_prompt,
    build_postscript,
    create_agent_with_config,
    create_child_terminals,
    panel_event,
    record_task_dispatch,
    resolve_base_dir,
    resolve_worker_launch_config,
    send_agent_prompt,
    action_mgr,
    state,
    template_mgr,
    worktree_mgr,
) -> TaskDispatchRuntime:
    """Compose runtime integrations for task dispatch."""
    return TaskDispatchRuntime(
        agent_can_receive_dispatch=_agent_can_receive_dispatch,
        agent_dismissed_at=_agent_dismissed_at,
        append_task_artifacts=_append_task_artifacts,
        apply_agent_class_launch_selection=_apply_agent_class_launch_selection,
        assemble_worker_prompt=_assemble_worker_prompt,
        behavior_overlay_prompt_block_for_cell=_behavior_overlay_prompt_block_for_cell,
        build_dispatch_persistent_prompt=build_dispatch_persistent_prompt,
        build_postscript=build_postscript,
        build_self_dispatch_prompt=_build_self_dispatch_prompt,
        build_torque_context=_build_torque_context,
        copy_worktree_context=_copy_worktree_context,
        create_agent_with_config=create_agent_with_config,
        create_child_terminals=create_child_terminals,
        engineer_dismissed_error=_engineer_dismissed_error,
        engineer_tombstoned_error=_engineer_tombstoned_error,
        find_active_worktree_owner=_find_active_worktree_owner,
        new_agent_prompt_sequence=_new_agent_prompt_sequence,
        owner_is_user_from_ids=_owner_is_user_from_ids,
        panel_event=panel_event,
        record_task_dispatch=record_task_dispatch,
        resolve_base_dir=resolve_base_dir,
        resolve_inherited_worktree_source=_resolve_inherited_worktree_source,
        resolve_task_id=_resolve_task_id,
        resolve_worker_launch_config=resolve_worker_launch_config,
        resolve_worktree_command_target_value=_resolve_worktree_command_target_value,
        sanitize_engineer_worker_provider_override=_sanitize_engineer_worker_provider_override,
        send_agent_prompt=send_agent_prompt,
        should_handoff_shared_worktree=_should_handoff_shared_worktree,
        should_queue_existing_agent_dispatch=_should_queue_existing_agent_dispatch,
        should_show_guidance_hint=_should_show_guidance_hint,
        startup_prompt_for_new_agent=_startup_prompt_for_new_agent,
        worker_provider_override_from_dispatch=_worker_provider_override_from_dispatch,
        action_mgr=action_mgr,
        build_prompt_memory_block=build_prompt_memory_block,
        engineer_architect_task_routing_denied_message=engineer_architect_task_routing_denied_message,
        is_architect_execution_target=is_architect_execution_target,
        normalize_default_worker_concurrency=normalize_default_worker_concurrency,
        prepend_agent_identity_anchor=prepend_agent_identity_anchor,
        reviewer_assignment_disclosure=reviewer_assignment_disclosure,
        state=state,
        task_counts_as_done=task_counts_as_done,
        template_mgr=template_mgr,
        worktree_mgr=worktree_mgr,
    )


def _build_worktree_command_runtime(
    apply_persistent_prompt,
    boundary_reason_message,
    broadcast_toast,
    build_cell_persistent_prompt,
    checkpoint_message,
    checkpoint_worktree_with_submodules,
    classify_repo_worktrees,
    cleanup_after_merge,
    is_designated_engineer,
    latest_boundary_state_for_cell,
    mark_branch_boundaries_merged,
    panel_event,
    persistent_prompt_filename,
    resolve_agent_launch_config,
    resolve_architect_launch_config,
    resolve_base_dir,
    resolve_engineer_launch_config,
    resolve_worker_launch_config,
    safe_remove_worktree_result,
    save_task_record,
    send_agent_prompt,
    worktree_submodules_for_cell,
    board_sync_manager,
    bridge,
    handle_command,
    state,
    worktree_mgr,
) -> WorktreeCommandRuntime:
    """Compose runtime integrations for worktree commands."""
    return WorktreeCommandRuntime(
        ExistingWorktreeTarget=ExistingWorktreeTarget,
        apply_persistent_prompt=apply_persistent_prompt,
        attach_stale_base=_attach_stale_base,
        boundary_gate_message=_boundary_gate_message,
        boundary_mismatch_check_allowed=_boundary_mismatch_check_allowed,
        boundary_reason_message=boundary_reason_message,
        broadcast_toast=broadcast_toast,
        build_cell_persistent_prompt=build_cell_persistent_prompt,
        checkpoint_message=checkpoint_message,
        checkpoint_worktree_with_submodules=checkpoint_worktree_with_submodules,
        classify_repo_worktrees=classify_repo_worktrees,
        cleanup_after_merge=cleanup_after_merge,
        configured_worktree_submodules_for_cell=_configured_worktree_submodules_for_cell,
        emit_stale_base_catch_workflow_breach=_emit_stale_base_catch_workflow_breach,
        emit_workflow_breach_event=_emit_workflow_breach_event,
        engineer_merge_mode_for_cell=_engineer_merge_mode_for_cell,
        generate_merge_message=_generate_merge_message,
        is_designated_engineer=is_designated_engineer,
        is_reconcilable_nested_gitlink_conflict=_is_reconcilable_nested_gitlink_conflict,
        latest_boundary_state_for_cell=latest_boundary_state_for_cell,
        launch_resolver_for_cell=_launch_resolver_for_cell,
        log_pr_task_ref_rewrite=_log_pr_task_ref_rewrite,
        mark_branch_boundaries_merged=mark_branch_boundaries_merged,
        panel_event=panel_event,
        persistent_prompt_filename=persistent_prompt_filename,
        reconcile_worktree_branch=_reconcile_worktree_branch,
        recover_authoritative_post_success_from_boundary=_recover_authoritative_post_success_from_boundary,
        relaunch_agent_after_worktree_removal=_relaunch_agent_after_worktree_removal,
        resolve_agent_launch_config=resolve_agent_launch_config,
        resolve_architect_launch_config=resolve_architect_launch_config,
        resolve_base_dir=resolve_base_dir,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        resolve_worker_launch_config=resolve_worker_launch_config,
        resolve_worktree_command_target_value=_resolve_worktree_command_target_value,
        rewrite_pr_torque_task_refs_metadata=_rewrite_pr_torque_task_refs_metadata,
        run_direct_worktree_merge=_run_direct_worktree_merge,
        run_pr_worktree_merge=_run_pr_worktree_merge,
        safe_remove_worktree_result=safe_remove_worktree_result,
        save_task_record=save_task_record,
        scope_domain_for_cell=_scope_domain_for_cell,
        send_agent_prompt=send_agent_prompt,
        shared_review_checkpoint_block_reason=_shared_review_checkpoint_block_reason,
        stale_base_check_merge_result=_stale_base_check_merge_result,
        stale_base_force_enabled=_stale_base_force_enabled,
        stale_base_post_rebase_evidence_required=_stale_base_post_rebase_evidence_required,
        stale_base_warning=_stale_base_warning,
        target_branch_from_payload=_target_branch_from_payload,
        target_has_driverless_payload=_target_has_driverless_payload,
        untracked_overwrite_message=_untracked_overwrite_message,
        workflow_breach_active_task_for_worker=_workflow_breach_active_task_for_worker,
        worktree_full_diff=_worktree_full_diff,
        worktree_merge_error=_worktree_merge_error,
        worktree_merge_requested_cleanup=_worktree_merge_requested_cleanup,
        worktree_submodules_for_cell=worktree_submodules_for_cell,
        advance_latest_boundary_after_mechanical_commit=advance_latest_boundary_after_mechanical_commit,
        board_sync_manager=board_sync_manager,
        bridge=bridge,
        get_adapter=get_adapter,
        handle_command=handle_command,
        mcp_entrypoint_for_cell=mcp_entrypoint_for_cell,
        refresh_latest_boundary_after_rebase=refresh_latest_boundary_after_rebase,
        runtime_env_vars_for_cell=runtime_env_vars_for_cell,
        state=state,
        worktree_mgr=worktree_mgr,
    )


configure_worktree_orchestration(
    WorktreeOrchestrationDependencies(
        cleanup_shipped_reviewers_for_merged_cell=(
            _cleanup_shipped_reviewers_for_merged_cell
        ),
        origin_verification_evidence=_origin_verification_evidence,
        record_merge_completion_evidence=_record_merge_completion_evidence,
        review_task_has_ship_verdict=_review_task_has_ship_verdict,
        resolve_agent_id=_resolve_agent_id,
        worktree_entry_matches_agent=_worktree_entry_matches_agent,
        worktree_path_contains=_worktree_path_contains,
    )
)


def _acquire_profile_daemon_owner() -> ProfileDaemonOwner:
    """Acquire the resolved DATA_DIR before authoritative runtime startup."""

    global _ACTIVE_DAEMON_OWNER
    profile = os.environ.get("TORQUE_PROFILE", "").strip() or "default"
    source_path = torque_config.SCRIPT_DIR / "torque.py"
    if not source_path.exists():
        source_path = torque_config.SCRIPT_DIR
    try:
        owner = ProfileDaemonOwner.acquire(
            data_dir=DATA_DIR,
            profile=profile,
            port=WS_PORT,
            source_path=source_path,
        )
    except Exception as exc:
        log.error("Torque backend ownership acquisition failed: %s", exc)
        raise
    _ACTIVE_DAEMON_OWNER = owner
    log.info("Torque daemon profile ownership acquired (%s)", owner.label)
    return owner


def _release_profile_daemon_owner(owner: ProfileDaemonOwner) -> None:
    global _ACTIVE_DAEMON_OWNER
    log.info("Releasing Torque daemon profile ownership (%s)", owner.label)
    if _ACTIVE_DAEMON_OWNER is owner:
        _ACTIVE_DAEMON_OWNER = None
    owner.release()


async def main(connection=None):
    """Run the main backend while holding authoritative profile ownership."""
    owner = _acquire_profile_daemon_owner()
    log.info(
        "Torque starting (port=%d, daemon=%s)",
        WS_PORT,
        owner.label,
    )
    profiling.configure_asyncio(asyncio.get_running_loop())
    cloud_connector_runtime = None
    db = TorqueDB(DB_FILE)
    db.init()
    log.info("SQLite database opened at %s", DB_FILE)
    state = MatrixState(db=db)
    state.load()
    db.enable_async_writes(True)
    ai_runtime = initialize_ai_runtime(
        db=db,
        state=state,
        data_dir=DATA_DIR,
        broadcast_callback=state.broadcast,
    )
    embedding_service = ai_runtime.embedding_service
    ai_index_service = ai_runtime.index_service
    ai_summary_service = ai_runtime.summary_service
    capture_deploy_boot_state(state, torque_config.SCRIPT_DIR)
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    event_log = EventLog()
    panel_log = PanelEventLog(
        max_size=state.global_settings.max_event_log, db=db)
    state.panel_log = panel_log
    notifier = NotificationManager(state)
    state.notification_manager = notifier
    notifier.start()
    event_bus = EventBus(state, event_log, notifier, panel_log=panel_log)
    event_bus.start()
    asyncio.create_task(health_check(state, event_log, event_bus, notifier))

    # Defence in depth against the proc-ceiling root cause: reap any orphaned
    # event-ingest / pty-supervisor sidecars left behind by killed daemons
    # whose temp data dirs have since been deleted. Spare our own DATA_DIR and
    # any sibling profile so a co-resident live daemon's sidecars survive.
    try:
        from . import sidecar_reaper

        spare_dirs = [DATA_DIR]
        profiles_root = Path.home() / ".torque" / "profiles"
        if profiles_root.exists():
            spare_dirs.extend(p for p in profiles_root.iterdir() if p.is_dir())
        reaped = await asyncio.to_thread(
            sidecar_reaper.reap_orphaned_sidecars, spare_data_dirs=spare_dirs
        )
        if reaped:
            log.info("Reaped %d orphaned sidecar(s) at startup", len(reaped))
        # Also reap leaked embedding ProcessPoolExecutor workers reparented to
        # init by an earlier unclean daemon death (PPID 1 — see reaper docs).
        reaped_workers = await asyncio.to_thread(
            sidecar_reaper.reap_orphaned_mp_workers
        )
        if reaped_workers:
            log.info(
                "Reaped %d orphaned multiprocessing worker(s) at startup",
                len(reaped_workers),
            )
    except Exception:
        log.exception("Orphaned-sidecar reap at startup failed (non-fatal)")

    event_ingest_runtime = await initialize_event_ingest_runtime(
        data_dir=DATA_DIR,
        event_bus=event_bus,
        state=state,
        daemon_identity=owner.label,
        configure_client=_configure_event_ingest_client,
    )
    event_ingest_client = event_ingest_runtime.client
    event_ingest_configured = event_ingest_runtime.configured
    event_ingest_drainer = event_ingest_runtime.drainer
    _ensure_event_ingest_configured = event_ingest_runtime.ensure_configured
    perceived_empty_detector = PerceivedEmptyDetector()
    log.info("Event bus, event-ingest client, health monitor, "
             "and notifications initialized")

    supervisor_banner: dict | None = None
    from .local_pty import LocalPtyAdapter, SupervisedPtyAdapter

    if torque_config.PROFILE_SKIP_PTY:
        pty_bridge = LocalPtyAdapter(state)
        log.info("Profile mode — PTY supervisor skipped")
    else:
        from . import pty_supervisor

        pty_bridge = None
        try:
            sock_path = pty_supervisor.ensure_running(DATA_DIR)
            pty_bridge = SupervisedPtyAdapter(state, sock_path)
            log.info(
                "Standalone mode — using PTY supervisor at %s", sock_path)
        except Exception as exc:
            log.exception(
                "PTY supervisor unavailable — falling back to in-memory "
                "(terminals will not survive daemon restart)")
            supervisor_banner = {
                "kind": "supervisor_unavailable",
                "message": (
                    "PTY supervisor unavailable — terminals will not "
                    "survive a Torque restart. See torque.log for details."
                ),
                "detail": str(exc),
            }
            pty_bridge = LocalPtyAdapter(state)
    bridge = pty_bridge
    worktree_mgr = WorktreeManager()
    action_mgr = ActionManager()
    template_mgr = RoleManager()
    specialization_mgr = SpecializationManager()

    # Install the fail-closed worktree-isolation guard hook for every repo
    # root we already know about, so existing checkouts are protected without
    # waiting for the next worktree creation (TORQUE:580). Idempotent and
    # never clobbers a foreign pre-commit hook.
    try:
        from .worktree import ensure_worktree_isolation_guard
        _guarded_roots: set[str] = set()
        for _cell in list(state.agents.values()):
            _root = (getattr(_cell, "worktree_repo_root", "") or "").strip()
            if _root and _root not in _guarded_roots:
                _guarded_roots.add(_root)
                ensure_worktree_isolation_guard(_root)
    except Exception:
        log.debug("Could not install worktree-isolation guard at startup",
                  exc_info=True)
    agent_launch = AgentLaunchService(
        state=state,
        connection=connection,
        bridge=bridge,
        worktree_mgr=worktree_mgr,
        template_mgr=template_mgr,
    )

    def _resolve_engineer_specializations_preamble(cell) -> str:
        """Return the combined specialization preamble for an engineer cell."""
        if not cell:
            return ""
        names = list(
            getattr(cell, "engineer_specializations", []) or [])
        if not names:
            return ""
        base_dir = getattr(cell, "directory", "") or ""
        try:
            return specialization_mgr.render_engineer_preamble(
                names, base_dir=base_dir)
        except Exception:
            log.exception(
                "failed to render engineer specializations for %s",
                getattr(cell, "id", ""))
            return ""

    from .engineer import EngineerEventBuffer
    async def _inject_digest_message(target, message: str, **kwargs):
        await inject_mcp_message(state, bridge, target, message, **kwargs)

    engineer_buffer = EngineerEventBuffer(
        state,
        bridge,
        inject_message=_inject_digest_message,
    )
    engineer_buffer.start()
    event_bus._engineer_buffer = engineer_buffer
    panel_log.on_event = engineer_buffer.on_panel_event
    log.info("Engineer event buffer started")


    def _worktree_remove_skip_result(cell, reason: str, *,
                                     shared_with: list | None = None) -> dict:
        return {
            "ok": False,
            "worktree_removed": False,
            "branch_deleted": False,
            "skipped": True,
            "reason": reason,
            "message": reason,
            "agent_id": getattr(cell, "id", ""),
            "agent_name": getattr(cell, "name", ""),
            "path": getattr(cell, "worktree_path", ""),
            "branch": getattr(cell, "worktree_branch", ""),
            "shared_with": [
                {
                    "id": getattr(agent, "id", ""),
                    "name": getattr(agent, "name", ""),
                }
                for agent in (shared_with or [])
            ],
            "mismatches": [],
        }

    def _clear_worktree_tracking(cell) -> None:
        cell.worktree_path = ""
        cell.worktree_branch = ""
        cell.worktree_base_branch = ""
        cell.worktree_repo_root = ""
        cell.worktree_dirty = False
        cell.worktree_diff = {}
        cell.worktree_changed_files = []
        cell.worktree_checkpoints = 0
        cell.worktree_ahead = 0
        cell.worktree_behind = 0
        cell.worktree_merged = False

    def _worktree_submodules_for_cell(cell) -> list[str]:
        if not cell:
            return []
        try:
            gs = state.get_group_settings(getattr(cell, "group", "") or "")
            return list(getattr(gs, "worktree_submodules", []) or [])
        except Exception:
            return []

    async def _checkpoint_worktree_with_submodules(cell, message: str = ""):
        submodules = _worktree_submodules_for_cell(cell)
        if submodules:
            return await worktree_mgr.checkpoint(
                cell,
                message=message,
                worktree_submodules=submodules,
            )
        return await worktree_mgr.checkpoint(cell, message=message)

    async def _safe_remove_worktree_result(cell) -> dict:
        """Remove a worktree only when it is not active/shared, then verify."""
        if not cell or not cell.worktree_path:
            return {
                "ok": True,
                "worktree_removed": True,
                "branch_deleted": True,
                "skipped": True,
                "message": "No worktree path configured",
                "mismatches": [],
            }

        refusal = _worktree_removal_refusal_reason(state, cell)
        if refusal:
            log.info("Skipping worktree removal for '%s' — %s",
                     cell.name, refusal)
            return _worktree_remove_skip_result(cell, refusal)

        same_path = str(cell.worktree_path or "")
        other_users = [
            a for a in state.agents.values()
            if a.id != cell.id
            and not state.agent_is_tombstoned(a)
            and (
                _worktree_path_contains(same_path, getattr(a, "worktree_path", ""))
                or _worktree_path_contains(same_path, getattr(a, "directory", ""))
                or _worktree_path_contains(same_path, getattr(a, "current_path", ""))
                or _worktree_path_contains(same_path, getattr(a, "git_root", ""))
            )
        ]
        active_other_users = []
        for other in other_users:
            other_reason = _worktree_removal_refusal_reason(state, other)
            status = str(getattr(other, "status", "") or "").strip().lower()
            non_stopped = status not in {"", "stopped", "error"}
            latest_activity = max(
                _timestamp_to_unix(getattr(other, "last_progress_at", 0.0)),
                _timestamp_to_unix(getattr(other, "last_heartbeat_at", 0.0)),
                _timestamp_to_unix(getattr(other, "last_activity_at", 0.0)),
                _timestamp_to_unix(getattr(other, "last_event_at", 0.0)),
            )
            if other_reason or (
                getattr(other, "session_id", None) and status != "stopped"
            ) or status == "running" or (
                non_stopped and _agent_has_open_assigned_tasks(state, other.id)
            ) or (
                non_stopped
                and latest_activity
                and time.time() - latest_activity <= (
                    _WORKTREE_REMOVAL_FRESH_AGENT_SECONDS
                )
            ):
                active_other_users.append(other)
        if active_other_users:
            names = ", ".join(a.name for a in active_other_users)
            reason = (
                "skipped: worktree belongs to active/fresh agent "
                f"shared with {names}"
            )
            log.info("Skipping worktree removal for '%s' — %s",
                     cell.name, reason)
            return _worktree_remove_skip_result(
                cell,
                reason,
                shared_with=active_other_users,
            )

        if hasattr(worktree_mgr, "remove_result"):
            submodules = _worktree_submodules_for_cell(cell)
            if submodules:
                result = await worktree_mgr.remove_result(
                    cell,
                    worktree_submodules=submodules,
                )
            else:
                result = await worktree_mgr.remove_result(cell)
        else:
            ok = await worktree_mgr.remove(cell)
            result = {
                "ok": bool(ok),
                "worktree_removed": bool(ok),
                "branch_deleted": bool(ok),
                "skipped": False,
                "message": (
                    "Worktree removed" if ok
                    else "Worktree removal failed"
                ),
                "mismatches": [],
            }

        if result.get("worktree_removed"):
            # If inactive/tombstoned cells shared the same worktree metadata,
            # reconcile their Torque tracking with the verified git state too.
            for other in other_users:
                _clear_worktree_tracking(other)
                state._emit_agent(other)
                state._db_save_agent(other)
        return result

    async def _safe_remove_worktree(cell):
        result = await _safe_remove_worktree_result(cell)
        return bool(result.get("ok"))

    async def _cleanup_after_merge(cell, *,
                                   close_agent: bool = False,
                                   remove_worktree: bool = False) -> dict:
        """Apply optional post-merge cleanup and return a status summary."""
        cleanup = {
            "close_agent": close_agent,
            "remove_worktree": remove_worktree,
            "agent_closed": False,
            "worktree_removed": False,
            "errors": [],
        }
        if not close_agent and not remove_worktree:
            return cleanup

        if close_agent:
            removed = await _close_agent_session_only(
                cell,
                errors=cleanup["errors"],
            )
            cleanup["agent_closed"] = True
            if remove_worktree:
                removed_worktree = False
                for c in removed:
                    if not c.worktree_path:
                        continue
                    remove_result = await _safe_remove_worktree_result(c)
                    if remove_result.get("worktree_removed"):
                        removed_worktree = True
                    if not remove_result.get("ok"):
                        cleanup["errors"].append(
                            remove_result.get("message")
                            or f"Failed to remove worktree for '{c.name}'."
                        )
                    for mismatch in remove_result.get("mismatches", []) or []:
                        cleanup["errors"].append(
                            f"Worktree removal mismatch for '{c.name}': "
                            f"{mismatch}"
                        )
                cleanup["worktree_removed"] = removed_worktree
            return cleanup

        repo_root = cell.worktree_repo_root
        remove_result = await _safe_remove_worktree_result(cell)
        ok = bool(remove_result.get("ok"))
        if remove_result.get("worktree_removed"):
            cleanup["worktree_removed"] = True
        if not ok:
            cleanup["errors"].append(
                remove_result.get("message")
                or f"Failed to remove worktree for '{cell.name}'."
            )
        for mismatch in remove_result.get("mismatches", []) or []:
            cleanup["errors"].append(
                f"Worktree removal mismatch for '{cell.name}': {mismatch}"
            )
        if remove_result.get("worktree_removed") and repo_root:
            cell.directory = repo_root
        if remove_result.get("worktree_removed") \
                and cell.cell_type == "agent" and cell.session_id:
            await _relaunch_agent_after_worktree_removal(
                cell,
                bridge=bridge,
                state=state,
                resolve_base_dir=_resolve_base_dir,
                resolve_agent_launch_config=_resolve_agent_launch_config,
                resolve_engineer_launch_config=_resolve_engineer_launch_config,
                resolve_architect_launch_config=_resolve_architect_launch_config,
                resolve_worker_launch_config=_resolve_worker_launch_config,
                is_designated_engineer=_is_designated_engineer,
                apply_persistent_prompt=_apply_persistent_prompt,
                build_cell_persistent_prompt=_build_cell_persistent_prompt,
                send_agent_prompt=_send_agent_prompt,
            )
        else:
            state._emit_agent(cell)
            state._db_save_agent(cell)
        return cleanup

    async def _close_agent_session_only(cell, *,
                                        errors: list | None = None) -> list:
        """Tombstone an agent and close live sessions without final cleanup."""
        if not cell:
            return []
        session_ids = {
            c.id: c.session_id
            for c in state._agent_cascade_cells(cell.id)
            if c.session_id
        }
        removed = state.remove_agent(cell.id)
        for c in removed:
            session_id = session_ids.get(c.id)
            if session_id:
                try:
                    await bridge.close_session(session_id)
                except Exception as exc:
                    if errors is not None:
                        errors.append(
                            f"Failed to close session for '{c.name}': {exc}"
                        )
                    log.exception("Failed to close session for '%s'", c.name)
        return removed

    async def _cleanup_purged_agents(removed: list, *,
                                     errors: list | None = None) -> None:
        """Run irreversible filesystem/runtime cleanup for hard-purged cells."""
        for c in removed:
            if c.session_id:
                try:
                    await bridge.close_session(c.session_id)
                except Exception as exc:
                    if errors is not None:
                        errors.append(
                            f"Failed to close session for '{c.name}': {exc}"
                        )
                    log.exception("Failed to close session for '%s'", c.name)
            if c.agent_type and c.directory:
                adapter = get_adapter(c.agent_type)
                try:
                    expanded_dir = os.path.expanduser(c.directory)
                    if hasattr(adapter, "cleanup_agent_config"):
                        adapter.cleanup_agent_config(c, expanded_dir)
                    if hasattr(adapter, "uninstall_hooks"):
                        adapter.uninstall_hooks(expanded_dir)
                    if hasattr(adapter, "uninstall_mcp_config"):
                        adapter.uninstall_mcp_config(expanded_dir)
                    adapter.uninstall_persistent_prompt(
                        expanded_dir,
                        _persistent_prompt_filename(c))
                except Exception:
                    log.exception(
                        "Failed agent cleanup while closing '%s'",
                        c.name,
                    )
            event_bus.cleanup_cell(c.id)
            worktree_mgr.forget_refresh_state(c.id)
            if c.worktree_path:
                ok = await _safe_remove_worktree(c)
                if not ok and errors is not None:
                    errors.append(f"Failed to remove worktree for '{c.name}'.")

    async def _tombstone_sweeper():
        """Periodically purge expired soft-deleted agents."""
        while True:
            await asyncio.sleep(300)
            try:
                removed = state.purge_tombstoned_agents()
                if removed:
                    await _cleanup_purged_agents(removed)
                    await state.broadcast()
                    log.info("Purged %d expired agent tombstone(s)", len(removed))
            except Exception:
                log.exception("Agent tombstone sweeper failed")

    async def _codex_provider_usage_backfill_refresher():
        """Periodically refresh Codex account-usage telemetry."""
        while True:
            await asyncio.sleep(_CODEX_PROVIDER_USAGE_BACKFILL_INTERVAL_SECONDS)
            try:
                report = refresh_codex_provider_usage_for_agents(
                    state,
                )
                if report.changed:
                    await state.broadcast()
                log.info(report.summary())
            except Exception:
                log.exception("Codex provider_usage backfill refresh failed")

    def _checkpoint_message(cell) -> str:
        """Build a checkpoint commit message from the agent's last summary."""
        summary = cell.last_summary.strip()
        n = cell.worktree_checkpoints + 1
        subject = f"torque: checkpoint {n} — {cell.name}"
        if summary:
            return f"{subject}\n\n{summary}"
        return subject

    async def _on_agent_session_end(cell):
        """Handle agent turn completion: auto-checkpoint."""
        agent_launch.retire_user_direct_turn(cell)
        state.history_snapshot_tokens(cell)
        # Auto-checkpoint
        if cell.worktree_path and cell.cell_type == "agent":
            if not cell.worktree_auto_checkpoint:
                return
            block_reason = _shared_review_checkpoint_block_reason(
                state,
                cell,
            )
            if block_reason:
                log.info("Skipping session-end checkpoint: %s", block_reason)
                return
            msg = _checkpoint_message(cell)
            sha = await _checkpoint_worktree_with_submodules(cell, msg)
            if sha:
                state._db_save_agent(cell)

    # Minimum seconds between progress-triggered checkpoints per agent.
    _CHECKPOINT_INTERVAL = 300  # 5 minutes

    async def _checkpoint_on_report(cell, message: str = ""):
        """Checkpoint worktree on ai progress/done if enabled and throttled."""
        if not cell.worktree_path or cell.cell_type != "agent":
            return
        if not cell.checkpoint_on_progress:
            return
        block_reason = _shared_review_checkpoint_block_reason(state, cell)
        if block_reason:
            log.info("Skipping progress checkpoint: %s", block_reason)
            return
        now = time.time()
        if (cell.last_checkpoint_at
                and now - cell.last_checkpoint_at < _CHECKPOINT_INTERVAL):
            return
        n = cell.worktree_checkpoints + 1
        subject = f"torque: checkpoint {n} — {cell.name}"
        if message:
            msg = f"{subject}\n\n{message}"
        else:
            msg = subject
        sha = await _checkpoint_worktree_with_submodules(cell, msg)
        if sha:
            cell.last_checkpoint_at = now
            state._db_save_agent(cell)

    async def _broadcast_toast(message, level="info"):
        """Send a toast notification to all WS clients."""
        if str(level or "").strip().lower() == "error":
            state.publish_operator_notice_best_effort(
                notice_type="alert",
                severity="error",
                category="background_operation",
                title="Background operation failed",
                message=message,
                source="server",
                action_kind="open_inbox",
            )
            return
        msg = json.dumps({"type": "toast", "message": message,
                          "level": level})
        dead = set()
        for ws_client in state._ws_clients:
            try:
                await ws_client.send_str(msg)
            except Exception:
                dead.add(ws_client)
        if dead:
            async with state._ws_clients_lock:
                state._discard_ws_clients_locked(dead)

    # Persistent supervisor-health banner. Only populated in standalone
    # mode when the supervisor is unavailable / restarted. Latest state
    # is replayed to each newly connected WS client.
    supervisor_banner_state: dict = {"banner": supervisor_banner}
    supervisor_runtime_fingerprint: dict = {"value": None}

    async def _publish_supervisor_runtime(force: bool = False) -> None:
        projection = build_supervisor_health_projection(
            bridge,
            profile_skip_pty=bool(
                getattr(torque_config, "PROFILE_SKIP_PTY", False)),
        )
        fp = supervisor_health_fingerprint(projection)
        if not force and supervisor_runtime_fingerprint.get("value") == fp:
            return
        supervisor_runtime_fingerprint["value"] = fp
        state._emit("runtime", **_runtime_payload(bridge=bridge, state=state))
        await state.broadcast()

    async def _broadcast_system_banner(banner):
        supervisor_banner_state["banner"] = banner
        payload = json.dumps({"type": "system_banner", "banner": banner})
        dead = set()
        for ws_client in state._ws_clients:
            try:
                await ws_client.send_str(payload)
            except Exception:
                dead.add(ws_client)
        if dead:
            async with state._ws_clients_lock:
                state._discard_ws_clients_locked(dead)

    async def _on_supervisor_event(kind, detail):
        """Translate SupervisedPtyAdapter events into user-visible
        banner + macOS notification.
        """
        if kind == "fresh_instance":
            banner = {
                "kind": "supervisor_restarted",
                "message": (
                    "PTY supervisor restarted — open terminals were "
                    "lost. Relaunch affected sessions from the UI."
                ),
            }
            await _broadcast_system_banner(banner)
            notifier.on_system_alert(
                "Torque — supervisor restarted",
                "Open terminals were lost. Relaunch them from the UI.")
        elif kind == "restart_requested":
            banner = {
                "kind": "supervisor_restarting",
                "message": (
                    "PTY supervisor is restarting in place; terminal "
                    "operations will resume after reconnect."
                ),
            }
            await _broadcast_system_banner(banner)
        elif kind == "restart_failed":
            banner = {
                "kind": "supervisor_restart_failed",
                "message": (
                    "PTY supervisor restart did not start; live terminals "
                    "remain attached."
                ),
                "detail": str((detail or {}).get("error") or ""),
            }
            await _broadcast_system_banner(banner)
        elif kind == "restarted":
            report = dict((detail or {}).get("restart_report") or {})
            adopted = int(report.get("adopted_sessions", 0) or 0)
            banner = {
                "kind": "supervisor_restarted",
                "message": (
                    "PTY supervisor restarted in place; live terminals "
                    "were preserved."
                ),
                "detail": f"adopted_sessions={adopted}",
            }
            await _broadcast_system_banner(banner)
        elif kind in {"restart_worker_loss", "restart_failed_lost"}:
            lost = int((detail or {}).get("lost_sessions", 0) or 0)
            banner = {
                "kind": "supervisor_restart_lost",
                "message": (
                    "PTY supervisor restart could not preserve every "
                    "terminal; affected sessions were marked lost."
                ),
                "detail": f"lost_sessions={lost}",
            }
            await _broadcast_system_banner(banner)
            notifier.on_system_alert(
                "Torque — supervisor restart lost terminals",
                "Some open terminals were lost during supervisor restart.")
        elif kind == "supervisor_lost":
            lost = int((detail or {}).get("lost_sessions", 0) or 0)
            banner = {
                "kind": "supervisor_lost",
                "message": (
                    "PTY supervisor died — open terminals were lost. "
                    "Torque is attempting a bounded respawn."
                ),
                "detail": f"lost_sessions={lost}",
            }
            await _broadcast_system_banner(banner)
        elif kind == "reconnected":
            # Routine reconnect to the same instance — clear banner.
            await _broadcast_system_banner(None)
        elif kind == "respawned":
            banner = {
                "kind": "supervisor_respawned",
                "message": (
                    "PTY supervisor was respawned; reconnecting terminal "
                    "control."
                ),
            }
            await _broadcast_system_banner(banner)
        elif kind == "respawn_failed":
            failed = int((detail or {}).get("failed_respawns", 0) or 0)
            max_retries = int((detail or {}).get("max_retries", 0) or 0)
            banner = {
                "kind": "supervisor_respawn_failed",
                "message": (
                    "PTY supervisor respawn failed; retrying with bounded "
                    "backoff."
                ),
                "detail": f"{failed}/{max_retries}",
            }
            await _broadcast_system_banner(banner)
        elif kind == "down":
            failed = int((detail or {}).get("failed_respawns", 0) or 0)
            max_retries = int((detail or {}).get("max_retries", 0) or 0)
            banner = {
                "kind": "supervisor_down",
                "message": (
                    "PTY supervisor is down — auto-respawn stopped after "
                    "bounded failures. Relaunch Torque from a non-worker "
                    "shell to restore embedded terminals."
                ),
                "detail": f"{failed}/{max_retries} failed respawns",
            }
            await _broadcast_system_banner(banner)
            notifier.on_system_alert(
                "Torque — supervisor down",
                "PTY supervisor auto-respawn stopped after repeated failures.")
        elif kind == "connect_failed":
            banner = {
                "kind": "supervisor_unavailable",
                "message": (
                    "Lost connection to the PTY supervisor — "
                    "terminal output may stall until it comes back."
                ),
            }
            await _broadcast_system_banner(banner)
        await _publish_supervisor_runtime(force=True)

    # Duck-type: only SupervisedPtyAdapter has this attribute.
    if hasattr(bridge, "on_supervisor_event"):
        bridge.on_supervisor_event = _on_supervisor_event

    supervisor_watchdog = None
    if hasattr(bridge, "supervisor_connected"):
        from . import pty_supervisor as _pty_supervisor_mod
        supervisor_watchdog = SupervisorLivenessWatchdog(
            bridge=bridge,
            data_dir=DATA_DIR,
            ensure_running=_pty_supervisor_mod.ensure_running,
            pid_alive=_pty_supervisor_mod._pid_alive,
            publish_state=_publish_supervisor_runtime,
            emit_event=_on_supervisor_event,
        )

    async def _on_agent_session_end_detected(cell, data=None):
        """Convert bridge-detected turn completion into a normal AgentEvent."""
        if str(getattr(cell, "agent_type", "") or "") != "codex":
            return
        if str(getattr(cell, "status", "") or "") != "running":
            return
        payload = {
            "reason": "pty_idle_screen",
            "source": "codex_idle_screen_backstop",
        }
        payload.update(dict(data or {}))
        await event_bus.emit(AgentEvent(
            cell_id=cell.id,
            timestamp=time.time(),
            event_type="session_end",
            data=payload,
        ))

    # Signal bridge when agent TUI is ready (hook-based session_start)
    event_bus.on_session_start = _make_agent_session_start_handler(
        state,
        bridge,
        lambda: _send_agent_prompt,
    )
    # Handle agent turn completion (hook-based session_end)
    event_bus.on_session_end = _on_agent_session_end
    # Handle codex turn completion detected by the PTY idle-screen backstop.
    if hasattr(bridge, "on_agent_session_end_detected"):
        bridge.on_agent_session_end_detected = _on_agent_session_end_detected
    bridge.on_agent_event = event_bus.emit
    # Also checkpoint when the terminal session is actually closed (tab closed)
    bridge.on_session_terminated = _on_agent_session_end
    event_ingest_drainer.start()
    log.info(
        "Durable event-ingest drainer started after EventBus callbacks "
        "(daemon=%s)",
        owner.label,
    )

    async def _state_payload(*, compact: bool = False) -> dict:
        # Prefill the per-repo branch cache before legacy state.to_dict()
        # runs — otherwise the sync engineer-stream snapshot inside it would
        # fork `git show-ref` per branch on the event loop, stalling the WS.
        if not compact:
            try:
                from .worktree_streams import prefill_branch_exists_for_state
                await prefill_branch_exists_for_state(state)
            except Exception:
                log.exception("Branch-exists prefill failed for state payload")
        state_payload = state.to_dict_compact() if compact else state.to_dict()
        return {
            "type": "state",
            "seq": state._seq,
            **state_payload,
            **engineer_buffer.export_state(),
            "providers": await get_providers_async(),
            "runtime": _runtime_payload(bridge=bridge, state=state),
        }

    terminal_clients: dict[str, set[web.WebSocketResponse]] = {}
    daemon_stop_state = _DaemonStopState()
    daemon_stop_event = asyncio.Event()
    daemon_stop_task: asyncio.Task | None = None

    async def _broadcast_metrics_tick(payload: dict) -> None:
        msg = await hot_json_dumps_async(payload, offload=False)
        async with state._ws_clients_lock:
            clients = list(state._ws_clients)
        if not clients:
            return
        results = await asyncio.gather(
            *(ws.send_str(msg) for ws in clients),
            return_exceptions=True,
        )
        dead: set[web.WebSocketResponse] = {
            ws for ws, result in zip(clients, results)
            if isinstance(result, BaseException)
        }
        if dead:
            async with state._ws_clients_lock:
                state._discard_ws_clients_locked(dead)

    def _metrics_live_sampler() -> dict:
        active = list(state.iter_active_agents())
        prompt_tails = getattr(agent_launch, "_prompt_queue_tails", {}) or {}
        sample = {
            "agents": sum(
                1 for cell in active
                if getattr(cell, "cell_type", "") == "agent"
            ),
            "ptys": sum(
                1 for cell in active
                if getattr(cell, "session_id", None)
            ),
            "prompt_queue_depth": len(prompt_tails),
        }
        refresh_metrics = getattr(worktree_mgr, "refresh_metrics_snapshot", None)
        if callable(refresh_metrics):
            try:
                sample["worktree_refresh"] = refresh_metrics()
            except Exception:
                log.debug("Worktree refresh metrics snapshot failed", exc_info=True)
        # daemon↔supervisor hop health (when the supervised adapter is active).
        connected_fn = getattr(bridge, "supervisor_connected", None)
        if callable(connected_fn):
            try:
                sample["supervisor_connected"] = bool(connected_fn())
                sample["supervisor_latency_ms"] = bridge.supervisor_last_latency_ms()
                snapshot = getattr(
                    bridge, "supervisor_write_breaker_snapshot", None)
                sample["stuck_sessions"] = (
                    len(snapshot()) if callable(snapshot) else 0)
            except Exception:
                pass
        return sample

    def _schedule_daemon_stop() -> None:
        nonlocal daemon_stop_task
        if daemon_stop_event.is_set():
            return
        if daemon_stop_task and not daemon_stop_task.done():
            return

        async def _trigger_stop_after_response_grace() -> None:
            await asyncio.sleep(_DAEMON_STOP_TRIGGER_DELAY_SECONDS)
            daemon_stop_event.set()

        daemon_stop_task = asyncio.create_task(_trigger_stop_after_response_grace())

    async def _broadcast_terminal_output(cell_id: str, session_id: str, text: str):
        if not text:
            return
        payload = json.dumps({
            "type": "output",
            "cell_id": cell_id,
            "session_id": session_id,
            "data": text,
        })
        dead = set()
        for ws_client in terminal_clients.get(cell_id, set()):
            try:
                await ws_client.send_str(payload)
            except Exception:
                dead.add(ws_client)
        if dead:
            terminal_clients.get(cell_id, set()).difference_update(dead)

    bridge.on_terminal_output = _broadcast_terminal_output

    async def _on_terminal_disconnected(cell):
        """Auto-remove a terminal when its tab is closed (close_on_disconnect)."""
        log.info("Auto-removing terminal '%s' (close_on_disconnect)", cell.name)
        removed = state.remove_agent(cell.id)
        for c in removed:
            event_bus.cleanup_cell(c.id)
            worktree_mgr.forget_refresh_state(c.id)

    bridge.on_terminal_disconnected = _on_terminal_disconnected
    await bridge.start()
    log.info("Startup checkpoint: bridge started")
    await bridge.reconnect_orphans()
    if supervisor_watchdog is not None:
        supervisor_watchdog.start()
        log.info("Startup checkpoint: supervisor liveness watchdog scheduled")
    try:
        report = refresh_codex_provider_usage_for_agents(state)
        if report.changed:
            await state.broadcast()
        log.info(report.summary())
    except Exception:
        log.exception("Codex provider_usage backfill on startup failed")
    state.sync_ui_selection_to_session(
        state.active_session_id or "",
        emit=False,
    )
    log.info("Startup checkpoint: orphan reconnect complete")
    asyncio.create_task(_worktree_diff_updater(state, worktree_mgr))
    log.info("Startup checkpoint: worktree diff updater scheduled")
    asyncio.create_task(_tombstone_sweeper())
    log.info("Startup checkpoint: tombstone sweeper scheduled")
    asyncio.create_task(_codex_provider_usage_backfill_refresher())
    log.info("Startup checkpoint: Codex provider_usage backfill scheduled")
    metrics_daemon = MetricsDaemon(
        state=state,
        db=db,
        send_tick=_broadcast_metrics_tick,
        live_sampler=_metrics_live_sampler,
    )
    metrics_daemon.start()
    log.info("Startup checkpoint: metrics daemon scheduled")

    _resolve_base_dir = agent_launch.resolve_base_dir

    def _resolve_deliverable_for_create(
        action_name: str,
        base_dir: str,
        explicit: dict | None,
    ) -> dict:
        """Resolve a task's deliverable contract at create time.

        Explicit kwargs (from the MCP/HTTP caller) win over the action's
        ``deliverable`` block. Returns the normalized contract dict.
        """
        from .actions import normalize_deliverable
        contract = {"required": False, "type": "", "format": "",
                    "artifact_title": ""}
        if action_name:
            try:
                contract = action_mgr.get_deliverable(action_name, base_dir)
            except Exception:
                log.exception(
                    "Failed to load action deliverable for '%s'", action_name)
        if isinstance(explicit, dict) and explicit:
            override = normalize_deliverable(explicit)
            for key in ("required", "type", "format", "artifact_title"):
                ev = override.get(key)
                if key == "required":
                    if "required" in explicit:
                        contract["required"] = bool(ev)
                elif ev:
                    contract[key] = ev
        return contract

    # AgentLaunchService owns these launch semantics. Bind its callables
    # directly instead of keeping a second layer of nested forwarding helpers
    # inside the server composition root.
    _resolve_provider_command = agent_launch.resolve_provider_command
    _suggest_template_agent_name = agent_launch.suggest_template_agent_name
    _resolve_agent_launch_config = agent_launch.resolve_agent_launch_config
    _resolve_engineer_launch_config = agent_launch.resolve_engineer_launch_config
    _resolve_worker_launch_config = agent_launch.resolve_worker_launch_config
    _resolve_architect_launch_config = agent_launch.resolve_architect_launch_config
    _create_child_terminals = agent_launch.create_child_terminals
    _persistent_prompt_filename = agent_launch.persistent_prompt_filename
    _apply_persistent_prompt = agent_launch.apply_persistent_prompt
    _create_agent_with_config = agent_launch.create_agent_with_config

    async def _send_agent_prompt(cell, prompt: str, *,
                                 delay: float = 0,
                                 persist: bool = False,
                                 background: bool = False,
                                 prime_input_ready: bool = False,
                                 settled_submit: bool = False,
                                 user_direct_message_id: str = ""):
        return await agent_launch.send_agent_prompt(
            cell,
            prompt,
            delay=delay,
            persist=persist,
            background=background,
            prime_input_ready=prime_input_ready,
            settled_submit=settled_submit,
            user_direct_message_id=user_direct_message_id,
        )

    _send_agent_prompt.cancel_user_direct_turn = agent_launch.cancel_user_direct_turn

    async def _restart_agent_session(command: dict) -> dict | None:
        return await _handle_restart_agent_command(
            command,
            state,
            bridge=bridge,
            worktree_mgr=worktree_mgr,
            resolve_base_dir=_resolve_base_dir,
            resolve_agent_launch_config=_resolve_agent_launch_config,
            resolve_engineer_launch_config=_resolve_engineer_launch_config,
            resolve_architect_launch_config=_resolve_architect_launch_config,
            resolve_worker_launch_config=_resolve_worker_launch_config,
            apply_persistent_prompt=_apply_persistent_prompt,
            build_cell_persistent_prompt=_build_cell_persistent_prompt,
            persistent_prompt_filename=_persistent_prompt_filename,
            is_designated_engineer=_is_designated_engineer,
            send_agent_prompt=_send_agent_prompt,
            clear_digest_backlog_for_restart=(
                engineer_buffer.clear_digest_backlog_for_restart
            ),
        )

    async def _ingest_remote_user_agent_message(payload: dict) -> dict:
        return await ingest_remote_user_agent_message(
            payload,
            state=state,
            send_prompt=_send_agent_prompt,
            handler=_handle_user_agent_message_command,
            restart_agent=_restart_agent_session,
        )

    async def _ingest_remote_command(payload: dict) -> dict:
        async def _dispatch_remote_command(command: dict) -> dict | None:
            if command.get("cmd") != "restart_agent":
                return {
                    "type": "error",
                    "code": "unsupported_command",
                    "message": "unsupported remote command",
                }
            return await _restart_agent_session(command)

        result = await ingest_remote_command_request(
            payload,
            state=state,
            handler=_dispatch_remote_command,
        )
        log.info(
            "Relay remote command audit: command_id=%s cmd=%s args=%s status=%s ok=%s error=%s",
            payload.get("command_id", ""),
            payload.get("cmd", ""),
            payload.get("args", {}),
            result.get("status", ""),
            result.get("ok", False),
            result.get("error_code", ""),
        )
        return result

    def _recent_user_direct_messages(limit: int) -> list[dict]:
        """Bounded recent user↔agent rows for the remote snapshot-on-open.

        Returns newest-first canonical direct-message rows from the same
        agent_peer_messages source that feeds live egress; the connector
        applies the user-destined gate + payload shaping.  Never unbounded.
        """
        db = getattr(state, "db", None)
        loader = getattr(db, "load_recent_user_direct_messages", None) if db else None
        if not callable(loader):
            return []
        try:
            return loader(limit=max(1, int(limit or 1)))
        except Exception:
            log.exception("recent user direct-message snapshot load failed")
            return []

    # -- Cloud connector (relay) wiring ---------------------------------------
    # Mutable holder so a relay-settings save can stop + restart the connector
    # in place (apply-on-change) without a daemon restart.
    cloud_connector_runtime_holder: list = [None]

    def _build_cloud_connector_context() -> cloud_hooks.CloudConnectorContext:
        """Construct the connector context from Global Settings (settings-primary,
        env / ee_connector.json fallback for unset fields)."""
        resolved = cloud_hooks.resolve_relay_config(
            state.global_settings, data_dir=str(DATA_DIR)
        )
        # Publish resolved config + per-field provenance for the Settings UI.
        state.set_relay_config(resolved)
        config = dict(resolved.get("config", {}))
        config["module"] = torque_config.CLOUD_CONNECTOR_MODULE
        return cloud_hooks.CloudConnectorContext(
            state=state,
            remote_user_agent_message=_ingest_remote_user_agent_message,
            remote_command=_ingest_remote_command,
            recent_direct_messages=_recent_user_direct_messages,
            agent_roster=lambda: _relay_agent_roster(state),
            agent_state_snapshot=lambda: _relay_agent_state_snapshot(state),
            register_direct_message_observer=(
                cloud_hooks.register_direct_message_observer
            ),
            register_state_delta_observer=(
                cloud_hooks.register_state_delta_observer
            ),
            report_connection_state=(
                lambda payload: state.set_relay_connection(payload)
            ),
            profile=str(os.environ.get("TORQUE_PROFILE", "") or ""),
            data_dir=str(DATA_DIR),
            config=config,
        )

    def _relay_settings_fingerprint() -> tuple:
        gs = state.global_settings
        return (
            bool(gs.relay_enabled),
            gs.relay_url,
            gs.relay_daemon_id,
            gs.relay_credential_id,
            gs.relay_private_key_path,
        )

    async def _restart_cloud_connector() -> None:
        """Apply-on-change: stop the running connector and start a fresh one with
        the current settings-derived config. Defensive / non-fatal — a relay
        misconfig must never crash the settings-save path. The :601
        relay_connection signal surfaces the resulting connect/disconnect/error.
        """
        try:
            await cloud_hooks.stop_cloud_connector(
                cloud_connector_runtime_holder[0]
            )
        except Exception:
            log.exception("Cloud connector stop during settings apply failed")
        runtime = None
        try:
            runtime = await cloud_hooks.start_cloud_connector(
                _build_cloud_connector_context()
            )
        except Exception:
            log.exception("Cloud connector restart during settings apply failed")
        cloud_connector_runtime_holder[0] = runtime
        # When the connector is now disabled, clear the relay signal back to
        # "disabled" (a disabled connector reports nothing on its own).
        if runtime is not None and not runtime.enabled:
            state.set_relay_connection(None)

    # -- Persistent system prompt ---------------------------------------------

    def _build_dispatch_persistent_prompt(system_prompt: str = "",
                                          owner_is_user: bool = False) -> str:
        parts = []
        if system_prompt:
            parts.append(system_prompt.rstrip())
        parts.append(
            build_torque_system_prompt(owner_is_user=owner_is_user).rstrip()
        )
        return "\n\n".join(parts) + "\n"

    def _build_cell_persistent_prompt(cell, launch_cfg: dict) -> str:
        if cell.cell_type != "agent" or not launch_cfg.get("agent_type"):
            return ""

        def _with_agent_class_prompt(base_prompt: str) -> str:
            return append_agent_class_prompt_block(base_prompt, cell)

        gs = state.get_group_settings(cell.group)
        if gs.engineer_agent_id == cell.id or cell.kind == "engineer":
            from .engineer import build_engineer_system_prompt
            ws = state.get_engineer_settings(cell.group)
            spec_preamble = _resolve_engineer_specializations_preamble(cell)
            return _with_agent_class_prompt(build_engineer_system_prompt(
                cell.group, ws, launch_cfg.get("system_prompt", ""),
                group_settings=gs,
                specializations_preamble=spec_preamble,
                owner_is_user=_agent_owner_is_user(cell),
                behavior_overlay_block=_behavior_overlay_prompt_block_for_cell(
                    state,
                    cell,
                ),
                agent_class_snapshot=getattr(
                    cell,
                    "effective_agent_class_snapshot",
                    {},
                )))
        if cell.kind == "architect":
            return _with_agent_class_prompt(_architect_persistent_prompt_text(
                group=cell.group,
                action_system_prompt=launch_cfg.get("system_prompt", ""),
                state=state,
                architect_id=cell.id,
            ))
        return _with_agent_class_prompt(_build_dispatch_persistent_prompt(
            launch_cfg.get("system_prompt", ""),
            owner_is_user=_agent_owner_is_user(cell)))

    def _is_designated_engineer(cell) -> bool:
        if not cell or cell.cell_type != "agent":
            return False
        gs = state.get_group_settings(cell.group)
        return bool(gs and gs.engineer_agent_id == cell.id)

    def _ownership_engineer_id_for_dispatch_source(cell) -> str:
        """Return the immutable Engineer owner id to stamp on new agents."""
        if not cell or cell.cell_type != "agent":
            return ""
        owner_id = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
        if owner_id:
            return owner_id
        if _is_designated_engineer(cell):
            return cell.id
        return ""

    def _record_task_dispatch(cell, task, lane: str) -> None:
        """Link a task to an agent and persist dispatch history."""
        repo_root = cell.worktree_repo_root or cell.git_root or ""
        next_boundary_task_id = ""
        if cell.worktree_branch and repo_root:
            latest = latest_boundary_task(
                state.board_tasks.values(),
                repo_root=repo_root,
                branch=cell.worktree_branch,
                statuses={"open"},
            )
            boundary_root_id = ""
            if latest:
                boundary_root_id = str(
                    getattr(latest, "pipeline_root_id", "")
                    or getattr(latest, "parent_task_id", "")
                    or latest.id
                ).strip()
            # Keep the persisted successor edge truthful at its write site.
            # Re-dispatching the reviewed stream root is also the supported
            # recovery for an edge written by older versions; the read-side
            # started-successor gate remains strict for genuine follow-ups.
            if (
                latest
                and latest.id != task.id
                and task.id != boundary_root_id
            ):
                next_boundary_task_id = latest.id
        if task.resume_after_boundary_task_id != next_boundary_task_id:
            task.resume_after_boundary_task_id = next_boundary_task_id
        state.board_update_task(
            task.id,
            agent_id=cell.id,
            lane=lane,
            dispatch_state="live",
        )
        state.auto_dispatch_queue_remove_task(task.id)
        cell.current_task_id = task.id
        state.mark_agent_progress(cell, emit=False)

    def _iso_to_unix(ts: str) -> float | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            return None

    def _current_board_tasks_for_agent(agent_id: str) -> list[dict]:
        """Best-effort fallback for active agents missing persisted task rows."""
        tasks = []
        for task in state.board_tasks.values():
            if task.agent_id != agent_id:
                continue
            outcome = ""
            if task_counts_as_done(task):
                outcome = "done"
            elif task.lane == "Archived":
                outcome = "archived"
            elif "torque:error" in (task.labels or []):
                outcome = "error"
            elif "torque:blocked" in (task.labels or []):
                outcome = "blocked"
            tasks.append({
                "agent_id": agent_id,
                "task_id": task.id,
                "task_title": task.task,
                "started_at": (_iso_to_unix(task.updated_at)
                                or _iso_to_unix(task.created_at)),
                "completed_at": (_iso_to_unix(task.updated_at)
                                  if task_is_closed(task) else None),
                "outcome": outcome,
            })
        tasks.sort(key=lambda t: t.get("started_at") or 0, reverse=True)
        return tasks

    def _live_history_status(cell, record: dict | None = None) -> str:
        """Return the history status implied by the current live cell."""
        existing = str((record or {}).get("status", "") or "").strip()
        if state.agent_is_tombstoned(cell):
            return "merged" if existing == "merged" else "removed"
        if bool(getattr(cell, "worktree_merged", False)):
            return "merged"
        status = str(getattr(cell, "status", "") or "").strip()
        kind = str(getattr(cell, "kind", "") or "").strip()
        if status and status != "stopped":
            return "active"
        if bool(getattr(cell, "persistent", False)) and kind in {"architect", "engineer"}:
            return "active"
        return existing or "active"

    def _enrich_history_record(record: dict) -> dict:
        """Overlay live metadata and task counts for active agents."""
        if not record:
            return record
        record = dict(record)
        cell = state.agents.get(record.get("id", ""))
        if cell and cell.cell_type == "agent":
            live_count = max(
                int(cell.tasks_dispatched or 0),
                len(_current_board_tasks_for_agent(cell.id)),
            )
            record.update({
                "name": cell.name or record.get("name", ""),
                "slug": cell.slug or record.get("slug", ""),
                "group": cell.group or record.get("group", ""),
                "agent_type": cell.agent_type or record.get("agent_type", ""),
                "template": cell.template or record.get("template", ""),
                "worktree_branch": (
                    cell.worktree_branch or record.get("worktree_branch", "")
                ),
                "kind": str(getattr(cell, "kind", "") or "").strip(),
                "status": _live_history_status(cell, record),
            })
            record["total_tasks"] = max(
                int(record.get("total_tasks") or 0), live_count)
        return record

    def _live_history_record(cell, base: dict | None = None) -> dict:
        """Synthesize/refresh a history row from a live agent cell."""
        base = dict(base or {})
        live_count = max(
            int(getattr(cell, "tasks_dispatched", 0) or 0),
            len(_current_board_tasks_for_agent(cell.id)),
        )
        return {
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "group": cell.group,
            "agent_type": cell.agent_type,
            "template": cell.template,
            "created_at": base.get("created_at")
                or getattr(cell, "last_activity_at", 0)
                or getattr(cell, "last_heartbeat_at", 0),
            "removed_at": base.get("removed_at"),
            "worktree_branch": cell.worktree_branch
                or base.get("worktree_branch", ""),
            "total_tokens_in": int(base.get("total_tokens_in") or 0),
            "total_tokens_out": int(base.get("total_tokens_out") or 0),
            "total_tasks": max(int(base.get("total_tasks") or 0), live_count),
            "status": _live_history_status(cell, base),
            "kind": str(getattr(cell, "kind", "") or "").strip(),
        }

    def _sort_history_records(records: list[dict]) -> list[dict]:
        return sorted(
            records,
            key=lambda r: (
                0 if r.get("status") == "active" else 1,
                -(float(r.get("created_at") or 0)),
            ),
        )

    def _history_records_with_live_agents(records: list[dict]) -> list[dict]:
        """Merge live agents so stale persisted status can't hide them."""
        by_id = {str(r.get("id", "") or ""): _enrich_history_record(r)
                 for r in records}
        for cell in state.agents.values():
            if getattr(cell, "cell_type", "") != "agent":
                continue
            base = by_id.get(cell.id) or db.load_agent_history_detail(cell.id)
            by_id[cell.id] = _live_history_record(cell, base)
        return list(by_id.values())

    def _save_task_record(task) -> None:
        if not task:
            return
        task.updated_at = datetime.now(timezone.utc).isoformat()
        state._emit("task_upsert", **asdict(task))
        state._db_save_task(task)

    def _boundary_base_branch_for_worktree(repo_root: str, branch: str) -> str:
        if not repo_root or not branch:
            return ""
        return latest_boundary_base_branch(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=branch,
        )

    def _worktree_owner_for_entry(repo_root: str, path: str):
        repo_root = str(repo_root or "").strip()
        path = str(path or "").strip()
        if not repo_root or not path:
            return None
        for agent in state.iter_active_agents():
            if _worktree_entry_matches_agent(repo_root, path, agent):
                return agent
        return None

    async def _classify_repo_worktrees(repo_root: str) -> list[dict]:
        repo_root = str(repo_root or "").strip()
        if not repo_root:
            return []
        entries = await worktree_mgr.list_worktrees(repo_root)
        items: list[dict] = []
        for entry in entries:
            branch = str(entry.get("branch", "") or "").strip()
            path = str(entry.get("path", "") or "").strip()
            is_torque_branch = branch.startswith("torque/")
            owner = _worktree_owner_for_entry(repo_root, path)
            if not is_torque_branch and not owner:
                continue

            exists = bool(path) and os.path.isdir(path)
            admin_stale = bool(entry.get("prunable")) or not exists
            base_branch = (
                str(getattr(owner, "worktree_base_branch", "") or "").strip()
                if owner else ""
            )
            if not base_branch and branch:
                base_branch = _boundary_base_branch_for_worktree(
                    repo_root,
                    branch,
                )

            dirty = False
            merged = False
            if owner:
                dirty = bool(getattr(owner, "worktree_dirty", False))
                if base_branch:
                    merged = bool(getattr(owner, "worktree_merged", False))
            elif exists and base_branch and branch:
                probe = SimpleNamespace(
                    name=branch,
                    worktree_path=path,
                    worktree_repo_root=repo_root,
                    worktree_branch=branch,
                    worktree_base_branch=base_branch,
                )
                dirty = await worktree_mgr.has_uncommitted_changes(probe)
                merged = await worktree_mgr.is_branch_merged(
                    repo_root,
                    branch=branch,
                    base_branch=base_branch,
                )

            prunable = False
            prune_reason = ""
            if owner:
                prune_reason = "owned_by_agent"
            elif admin_stale:
                prunable = True
                prune_reason = "stale_admin"
            elif not base_branch:
                prune_reason = "unknown_base_branch"
            elif dirty:
                prune_reason = "dirty"
            elif not merged:
                prune_reason = "not_merged"
            else:
                prunable = True
                prune_reason = "merged_clean"

            items.append({
                "path": path,
                "branch": branch,
                "branch_ref": str(entry.get("branch_ref", "") or ""),
                "head_sha": str(entry.get("head_sha", "") or ""),
                "base_branch": base_branch,
                "exists": exists,
                "admin_stale": admin_stale,
                "dirty": dirty,
                "merged": merged,
                "prunable": prunable,
                "prune_reason": prune_reason,
                "owner_agent_id": getattr(owner, "id", "") if owner else "",
                "owner_agent_name": getattr(owner, "name", "") if owner else "",
            })
        items.sort(key=lambda item: (item["branch"], item["path"]))
        return items

    def _branch_boundary_tasks_for_cell(cell, statuses: set[str] | None = None
                                        ) -> list:
        repo_root = ""
        if cell:
            repo_root = cell.worktree_repo_root or cell.git_root or ""
        if not cell or not repo_root or not cell.worktree_branch:
            return []
        return branch_boundary_tasks(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=cell.worktree_branch,
            statuses=statuses,
        )

    async def _latest_boundary_state_for_cell(cell) -> dict:
        if not cell or not cell.worktree_path:
            return {"latest": None, "clean": None, "reason": ""}

        latest = latest_boundary_task(
            state.board_tasks.values(),
            repo_root=cell.worktree_repo_root or cell.git_root or "",
            branch=cell.worktree_branch,
            statuses={"open"},
        )
        if not latest:
            return {"latest": None, "clean": None, "reason": ""}

        queued = queued_successor_tasks(state.board_tasks.values(), latest.id)
        started = started_successor_tasks(state.board_tasks.values(), latest.id)
        summary = boundary_summary(
            latest,
            queued_followers=queued,
            started_followers=started,
        )
        summary["clean_mergeable"] = False

        boundary = task_boundary(latest)
        commit_sha = boundary.get("commit_sha", "")
        if not commit_sha:
            summary["reason"] = boundary.get("reason", "") or "missing_commit_sha"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }

        head_sha = await worktree_mgr.current_head(cell)
        summary["head_sha"] = head_sha or ""
        submodules = _worktree_submodules_for_cell(cell)
        current_submodules = []
        if submodules and hasattr(worktree_mgr, "nested_submodule_head_states"):
            try:
                current_submodules = await worktree_mgr.nested_submodule_head_states(
                    cell,
                    submodules,
                )
            except Exception:
                log.exception(
                    "Failed to verify nested submodule boundary for '%s'",
                    cell.name,
                )
                current_submodules = []
        if current_submodules:
            summary["submodules"] = current_submodules
        if started:
            summary["reason"] = "started_successor"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        if not head_sha:
            summary["reason"] = "missing_head_sha"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        if head_sha != commit_sha:
            recorded_submodules = boundary_submodule_branches(boundary)
            reconcile_check = getattr(
                worktree_mgr,
                "gitlink_reconciliation_boundary_state",
                None,
            )
            if (
                submodules
                and recorded_submodules
                and callable(reconcile_check)
            ):
                try:
                    reconciliation = await reconcile_check(
                        cell,
                        boundary_commit_sha=commit_sha,
                        head_sha=head_sha,
                        recorded_submodules=recorded_submodules,
                        current_submodules=current_submodules,
                        worktree_submodules=submodules,
                    )
                except Exception:
                    log.exception(
                        "Failed to verify gitlink reconciliation boundary "
                        "for '%s'",
                        cell.name,
                    )
                    reconciliation = {}
                if reconciliation.get("ok"):
                    summary["clean_mergeable"] = True
                    summary["gitlink_reconciliation"] = reconciliation
                    return {"latest": summary, "clean": summary, "reason": ""}
            summary["reason"] = "branch_tip_moved"
            await _ensure_boundary_tip_mismatch_info(
                worktree_mgr,
                cell,
                summary,
            )
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        recorded_submodules = boundary_submodule_branches(boundary)
        if recorded_submodules:
            current_by_path = {
                item.get("path", ""): item for item in current_submodules
            }
            for recorded in recorded_submodules:
                current = current_by_path.get(recorded.get("path", ""))
                if not current:
                    summary["reason"] = "missing_submodule_head_sha"
                    summary["submodule_mismatch"] = recorded
                    return {
                        "latest": summary,
                        "clean": None,
                        "reason": summary["reason"],
                    }
                if current.get("commit_sha", "") != recorded.get("commit_sha", ""):
                    summary["reason"] = "submodule_branch_tip_moved"
                    summary["submodule_mismatch"] = {
                        "recorded": recorded,
                        "current": current,
                    }
                    return {
                        "latest": summary,
                        "clean": None,
                        "reason": summary["reason"],
                    }

        summary["clean_mergeable"] = True
        return {"latest": summary, "clean": summary, "reason": ""}

    def _boundary_reason_message(reason: str, boundary: dict | None = None) -> str:
        if reason == "started_successor":
            if boundary and boundary.get("started_followers"):
                follower = boundary["started_followers"][0]
                return (
                    "Latest task boundary is no longer cleanly mergeable "
                    f"because follow-up task \"{follower.get('task_title', '')}\""
                    " has already started."
                )
            return (
                "Latest task boundary is no longer cleanly mergeable "
                "because a follow-up task has already started."
            )
        if reason == "branch_tip_moved":
            return _boundary_tip_mismatch_message(boundary)
        if reason == "submodule_branch_tip_moved":
            return (
                "Latest task boundary no longer matches the nested submodule "
                "branch tip. A newer submodule commit or external rewrite "
                "moved the branch pair."
            )
        if reason == "missing_submodule_head_sha":
            return (
                "Cannot verify the nested submodule branch tip for the latest "
                "task boundary."
            )
        if reason == "missing_head_sha":
            return "Cannot verify the current branch tip for the latest task boundary."
        if reason == "missing_commit_sha":
            return "Latest task boundary is missing its recorded commit SHA."
        if boundary:
            task_title = boundary.get("task_title", "")
            if task_title:
                return f"Latest task boundary for \"{task_title}\" is not mergeable."
        return "Latest task boundary is not mergeable."

    def _task_boundary_checkpoint_message(task, cell, message: str) -> str:
        subject = f"torque: task boundary — {task.task[:72]}"
        body_lines = [f"Task: {task.task}"]
        if cell.worktree_branch:
            body_lines.append(f"Branch: {cell.worktree_branch}")
        if message and message.strip() and message.strip() != "Done":
            body_lines.append("")
            body_lines.append(message.strip())
        return subject + "\n\n" + "\n".join(body_lines)

    async def _record_task_boundary(task, cell, message: str = "") -> dict | None:
        if not task or not cell or not cell.worktree_path:
            return None

        dirty = await worktree_mgr.has_uncommitted_changes(cell)
        boundary_sha = ""
        kind = "marker"
        reason = ""
        if dirty:
            boundary_sha = await _checkpoint_worktree_with_submodules(
                cell,
                _task_boundary_checkpoint_message(task, cell, message),
            ) or ""
            kind = "checkpoint"
            if not boundary_sha:
                reason = "checkpoint_failed"
        else:
            boundary_sha = await worktree_mgr.current_head(cell) or ""
            if not boundary_sha:
                reason = "missing_head_sha"

        recorded_at = datetime.now(timezone.utc).isoformat()
        submodule_states = []
        submodules = _worktree_submodules_for_cell(cell)
        if submodules and hasattr(worktree_mgr, "nested_submodule_head_states"):
            try:
                submodule_states = await worktree_mgr.nested_submodule_head_states(
                    cell,
                    submodules,
                )
            except Exception:
                log.exception(
                    "Failed to record nested submodule boundary for '%s'",
                    cell.name,
                )

        for older in _branch_boundary_tasks_for_cell(cell, statuses={"open"}):
            if older.id == task.id:
                continue
            older_boundary = dict(task_boundary(older))
            older_boundary["status"] = "superseded"
            older_boundary["superseded_by_task_id"] = task.id
            older_boundary.pop("reason", None)
            older.worktree_boundary = older_boundary
            _save_task_record(older)

        task.worktree_boundary = {
            "version": "1",
            "branch": cell.worktree_branch or "",
            "repo_root": cell.worktree_repo_root or cell.git_root or "",
            "base_branch": cell.worktree_base_branch or "",
            "commit_sha": boundary_sha,
            "kind": kind,
            "status": "open" if boundary_sha else "invalid",
            "recorded_at": recorded_at,
            "recorded_by_agent_id": cell.id,
            "message": message.strip(),
            "superseded_by_task_id": "",
            "merged_at": "",
            "merge_commit_sha": "",
            "reason": reason,
        }
        if submodule_states:
            task.worktree_boundary["submodules"] = submodule_states
        task.worktree_boundary["code_delta"] = await classify_boundary_code_delta(
            worktree_path=cell.worktree_path,
            base_branch=cell.worktree_base_branch,
            commit_sha=boundary_sha,
        )
        _save_task_record(task)

        for queued_task in retarget_queued_successor_tasks(
                state.board_tasks.values(),
                agent_id=cell.id,
                boundary_task_id=task.id,
                exclude_task_id=task.id):
            _save_task_record(queued_task)

        return dict(task.worktree_boundary)

    def _mark_branch_boundaries_merged(cell, merge_sha: str,
                                       merged_task_ids=()) -> None:
        if not cell or not merged_task_ids:
            return
        repo_root = cell.worktree_repo_root or cell.git_root or ""
        for branch_task in mark_branch_boundaries_merged(
                state.board_tasks.values(),
                repo_root=repo_root,
                branch=cell.worktree_branch or "",
                merge_sha=merge_sha,
                task_ids=merged_task_ids):
            _save_task_record(branch_task)

    # -- Postscript builder -------------------------------------------------

    def _build_postscript(task, amgr, base_dir="", is_clean=True,
                          cell=None):
        """Build the torque-ai instruction block appended to dispatch prompts.

        Only shows commands relevant to the action's transitions.
        ``done``, ``blocked``, and ``error`` are always included.
        ``derive`` only appears when the action declares transitions.
        ``ask`` only appears when an ``ask`` transition exists.

        When ``is_clean`` is False (agent already has context from prior
        tasks), emits an abbreviated version with derive/ask commands
        if the action has transitions.
        """
        # Resolve transitions for this action
        transitions = []
        if task.action_name:
            transitions = amgr.get_transitions(task.action_name,
                                               base_dir)

        is_impl = bool(
            task.action_name
            and amgr.is_implementation_depth(task.action_name, base_dir)
        )
        commit_hint = compute_commit_hint(
            has_worktree_branch=bool(cell and cell.worktree_branch),
            is_implementation=is_impl,
            auto_checkpoint=bool(cell and cell.worktree_auto_checkpoint),
            checkpoint_on_progress=bool(
                cell and cell.checkpoint_on_progress),
        )

        # Pipeline context for derived tasks
        pipeline_context = ""
        if task.parent_task_id:
            max_d = state.global_settings.max_pipeline_depth or "∞"
            parent = state.board_tasks.get(task.parent_task_id)
            root = state.board_tasks.get(task.pipeline_root_id)
            ctx = (f"This task is part of a pipeline "
                   f"(depth {task.pipeline_depth}/{max_d}).")
            if parent:
                p_agent = ""
                if parent.agent_id:
                    a = state.agents.get(parent.agent_id)
                    if a:
                        p_agent = f", agent: {a.slug or a.name}"
                p_status = ""
                if parent.status:
                    p_status = f", status: {parent.status}"
                ctx += (f"\nParent task: \"{parent.task[:80]}\" "
                        f"({parent.lane}{p_status}{p_agent})")
            if root and root.id != (parent.id if parent else ""):
                ctx += f"\nRoot task: \"{root.task[:80]}\""
            pipeline_context = ctx

        return build_dispatch_postscript(
            transitions=transitions,
            is_clean=is_clean,
            commit_hint=commit_hint,
            pipeline_context=pipeline_context,
            deliverable_required=bool(
                getattr(task, "deliverable_required", False)),
            deliverable_type=str(
                getattr(task, "deliverable_type", "") or ""),
            deliverable_format=str(
                getattr(task, "deliverable_format", "") or ""),
            deliverable_artifact_title=str(
                getattr(task, "deliverable_artifact_title", "") or ""),
            task_title=str(getattr(task, "task", "") or ""),
            requires_review=bool(
                getattr(task, "requires_review", False)),
            pre_approved_by=str(
                getattr(task, "pre_approved_by", "") or ""),
        )

    # -- Command handler ----------------------------------------------------

    catalog_command_runtime = CatalogCommandRuntime(
        state=state,
        db=db,
        action_mgr=action_mgr,
        template_mgr=template_mgr,
        specialization_mgr=specialization_mgr,
        resolve_base_dir=_resolve_base_dir,
        handle_set_engineer_specializations_command=(
            _handle_set_engineer_specializations_command
        ),
        action_to_yaml=_action_to_yaml,
    )

    async def handle_command(data: dict) -> dict | None:
        """Handle a command, return a direct-response dict or None.

        Direct-response commands (get_config, get_group_settings,
        worktree_history) return immediately without broadcasting.
        Mutation commands broadcast state to all WS clients and
        optionally return a result dict. This HTTP command surface is
        operated by the user and intentionally trusted; MCP tool
        surfaces enforce architect/engineer/worker communication scope.
        """
        cmd = data.get("cmd")
        log.info("CMD %s %s", cmd, _redact_command_log_payload(data))
        critical_command_name = _critical_command_name(data)
        critical_idempotency_key = str(
            (data or {}).get("idempotency_key", "") or ""
        ).strip()
        critical_request_hash = ""
        critical_failed_write_key = ""
        critical_capture_active = False
        if db and critical_command_name and critical_idempotency_key:
            await state.flush_db_writes()
            cached_result, critical_idempotency_key, critical_request_hash = (
                _load_internal_command_receipt(db, data)
            )
            if cached_result is not _NO_COMMAND_RECEIPT:
                return cached_result
            critical_failed_write_key = _internal_failed_write_key(
                critical_idempotency_key
            )
            db.enqueue_failed_write(
                idempotency_key=critical_failed_write_key,
                endpoint="/internal/cmd",
                method="POST",
                surface="internal",
                tool_name=critical_command_name,
                caller_id=_critical_command_caller_id(data),
                payload=dict(data or {}),
                attempts=0,
                last_error="pending",
            )
            if _critical_command_needs_capture(data):
                state.begin_critical_write_capture(
                    command_name=critical_command_name,
                    idempotency_key=critical_idempotency_key,
                    request_hash=critical_request_hash,
                )
                critical_capture_active = True

        if cmd in DIRECT_COMMAND_NAMES:
            return await handle_direct_command(
                data,
                _build_direct_command_runtime(
                    compute_worktree_streams=compute_worktree_streams,
                    current_board_tasks_for_agent=(
                        _current_board_tasks_for_agent
                    ),
                    enrich_history_record=_enrich_history_record,
                    history_records_with_live_agents=(
                        _history_records_with_live_agents
                    ),
                    live_history_record=_live_history_record,
                    prefill_branch_exists_for_state=(
                        prefill_branch_exists_for_state
                    ),
                    relay_settings_fingerprint=_relay_settings_fingerprint,
                    resolve_base_dir=_resolve_base_dir,
                    restart_cloud_connector=_restart_cloud_connector,
                    bridge=bridge,
                    catalog_command_runtime=catalog_command_runtime,
                    cloud_connector_runtime_holder=(
                        cloud_connector_runtime_holder
                    ),
                    db=db,
                    event_ingest_client=event_ingest_client,
                    event_log=event_log,
                    panel_log=panel_log,
                    specialization_mgr=specialization_mgr,
                    state=state,
                    template_mgr=template_mgr,
                    action_mgr=action_mgr,
                    sort_history_records=_sort_history_records,
                ),
            )

        # -- Mutation commands: broadcast state at the end --
        result = None
        try:
            if cmd == "refresh":
                pass

            elif cmd == "resync":
                # Client detected a sequence gap — send full snapshot
                return await _state_payload()

            elif cmd in RUNTIME_SETTINGS_COMMAND_NAMES:
                result = await handle_runtime_settings_command(
                    data,
                    _build_runtime_settings_command_runtime(
                        persistent_prompt_filename=(
                            _persistent_prompt_filename
                        ),
                        relay_settings_fingerprint=_relay_settings_fingerprint,
                        restart_cloud_connector=_restart_cloud_connector,
                        safe_remove_worktree=_safe_remove_worktree,
                        ai_index_service=ai_index_service,
                        ai_summary_service=ai_summary_service,
                        bridge=bridge,
                        db=db,
                        event_bus=event_bus,
                        event_ingest_client=event_ingest_client,
                        event_ingest_configured=event_ingest_configured,
                        panel_log=panel_log,
                        state=state,
                        worktree_mgr=worktree_mgr,
                    ),
                )

            elif cmd in AGENT_OPERATION_COMMAND_NAMES:
                result = await handle_agent_operation_command(
                    data,
                    _build_agent_operation_runtime(
                        apply_persistent_prompt=_apply_persistent_prompt,
                        build_cell_persistent_prompt=(
                            _build_cell_persistent_prompt
                        ),
                        is_designated_engineer=_is_designated_engineer,
                        panel_event=_panel_event,
                        persistent_prompt_filename=(
                            _persistent_prompt_filename
                        ),
                        resolve_base_dir=_resolve_base_dir,
                        resolve_agent_launch_config=(
                            _resolve_agent_launch_config
                        ),
                        resolve_architect_launch_config=(
                            _resolve_architect_launch_config
                        ),
                        resolve_engineer_launch_config=(
                            _resolve_engineer_launch_config
                        ),
                        resolve_worker_launch_config=(
                            _resolve_worker_launch_config
                        ),
                        suggest_template_agent_name=(
                            _suggest_template_agent_name
                        ),
                        create_agent_with_config=_create_agent_with_config,
                        create_child_terminals=_create_child_terminals,
                        send_agent_prompt=_send_agent_prompt,
                        restart_agent_session=_restart_agent_session,
                        cleanup_purged_agents=_cleanup_purged_agents,
                        close_agent_session_only=_close_agent_session_only,
                        bridge=bridge,
                        engineer_buffer=engineer_buffer,
                        handle_command=handle_command,
                        specialization_mgr=specialization_mgr,
                        state=state,
                        worktree_mgr=worktree_mgr,
                    ),
                )

            elif cmd in WORKTREE_COMMAND_NAMES:
                result = await handle_worktree_command(
                    data,
                    _build_worktree_command_runtime(
                        apply_persistent_prompt=_apply_persistent_prompt,
                        boundary_reason_message=_boundary_reason_message,
                        broadcast_toast=_broadcast_toast,
                        build_cell_persistent_prompt=_build_cell_persistent_prompt,
                        checkpoint_message=_checkpoint_message,
                        checkpoint_worktree_with_submodules=_checkpoint_worktree_with_submodules,
                        classify_repo_worktrees=_classify_repo_worktrees,
                        cleanup_after_merge=_cleanup_after_merge,
                        is_designated_engineer=_is_designated_engineer,
                        latest_boundary_state_for_cell=_latest_boundary_state_for_cell,
                        mark_branch_boundaries_merged=_mark_branch_boundaries_merged,
                        panel_event=_panel_event,
                        persistent_prompt_filename=_persistent_prompt_filename,
                        resolve_agent_launch_config=_resolve_agent_launch_config,
                        resolve_architect_launch_config=_resolve_architect_launch_config,
                        resolve_base_dir=_resolve_base_dir,
                        resolve_engineer_launch_config=_resolve_engineer_launch_config,
                        resolve_worker_launch_config=_resolve_worker_launch_config,
                        safe_remove_worktree_result=_safe_remove_worktree_result,
                        save_task_record=_save_task_record,
                        send_agent_prompt=_send_agent_prompt,
                        worktree_submodules_for_cell=_worktree_submodules_for_cell,
                        board_sync_manager=board_sync_manager,
                        bridge=bridge,
                        handle_command=handle_command,
                        state=state,
                        worktree_mgr=worktree_mgr,
                    ),
                )

            elif cmd in BOARD_OPERATION_COMMAND_NAMES:
                result = await handle_board_operation_command(
                    data,
                    _build_board_operation_runtime(
                        panel_event=_panel_event,
                        resolve_base_dir=_resolve_base_dir,
                        resolve_deliverable_for_create=_resolve_deliverable_for_create,
                        board_sync_manager=board_sync_manager,
                        handle_command=handle_command,
                        state=state,
                    ),
                )

            elif cmd == "dispatch_task":
                result = await handle_dispatch_task_command(
                    data,
                    _build_task_dispatch_runtime(
                        build_dispatch_persistent_prompt=_build_dispatch_persistent_prompt,
                        build_postscript=_build_postscript,
                        create_agent_with_config=_create_agent_with_config,
                        create_child_terminals=_create_child_terminals,
                        panel_event=_panel_event,
                        record_task_dispatch=_record_task_dispatch,
                        resolve_base_dir=_resolve_base_dir,
                        resolve_worker_launch_config=_resolve_worker_launch_config,
                        send_agent_prompt=_send_agent_prompt,
                        action_mgr=action_mgr,
                        state=state,
                        template_mgr=template_mgr,
                        worktree_mgr=worktree_mgr,
                    ),
                )

            elif cmd in ASK_COMMAND_NAMES:
                result = await handle_ask_command(
                    data,
                    AskCommandRuntime(
                        is_architect_ask_task=_is_architect_ask_task,
                        panel_event=_panel_event,
                        resolve_architect_ask_task=_resolve_architect_ask_task,
                        resolve_human_ask_task=_resolve_human_ask_task,
                        send_agent_prompt=_send_agent_prompt,
                        bridge=bridge,
                        state=state,
                    ),
                )

            elif cmd in PROMPT_PREVIEW_COMMAND_NAMES:
                result = await handle_prompt_preview_command(
                    data,
                    _build_prompt_preview_runtime(
                        build_postscript=_build_postscript,
                        resolve_base_dir=_resolve_base_dir,
                        action_mgr=action_mgr,
                        state=state,
                        template_mgr=template_mgr,
                    ),
                )

            elif cmd in MEMORY_COMMAND_NAMES:
                memory_result = await _MEMORY_COMMAND_REGISTRY.dispatch(
                    cmd,
                    data,
                    state,
                    resolve_cell_and_task=_resolve_memory_cell_and_task,
                    resolve_scope_ref=_resolve_memory_scope_ref,
                    resolve_link_ref=_resolve_memory_link_ref,
                    resolve_task_id=_resolve_task_id,
                )
                result = memory_result.value

            elif cmd in OPERATOR_NOTICE_COMMAND_NAMES:
                notice_result = await _OPERATOR_NOTICE_COMMAND_REGISTRY.dispatch(
                    cmd,
                    data,
                    state,
                )
                result = notice_result.value

            elif cmd in UI_STATE_COMMAND_NAMES:
                ui_state_result = await _UI_STATE_COMMAND_REGISTRY.dispatch(
                    cmd,
                    data,
                    state,
                )
                result = ui_state_result.value

            # -- Schedule commands ------------------------------------------

            elif cmd in SCHEDULE_COMMAND_NAMES:
                schedule_result = await _SCHEDULE_COMMAND_REGISTRY.dispatch(
                    cmd,
                    data,
                    state,
                    dispatch_command=handle_command,
                    panel_event=_panel_event,
                )
                result = schedule_result.value

            elif cmd == "blocked_task_reply":
                task = state.board_tasks.get(_resolve_task_id(
                    state, data.get("task_id", "")))
                actor = state.agents.get(str(data.get("actor_id", "") or ""))
                if not task or not actor:
                    result = {"type": "error", "message": "Task or architect not found"}
                else:
                    result = await resolve_blocked_task_reply(
                        state, task, actor, data.get("answer", ""),
                        send_prompt=_send_agent_prompt,
                        relaunch_agent=(
                            lambda payload: _handle_relaunch_agent_command(
                                payload, state, bridge=bridge,
                                worktree_mgr=worktree_mgr,
                                resolve_base_dir=_resolve_base_dir,
                                resolve_agent_launch_config=_resolve_agent_launch_config,
                                resolve_engineer_launch_config=_resolve_engineer_launch_config,
                                resolve_architect_launch_config=_resolve_architect_launch_config,
                                resolve_worker_launch_config=_resolve_worker_launch_config,
                                apply_persistent_prompt=_apply_persistent_prompt,
                                build_cell_persistent_prompt=_build_cell_persistent_prompt,
                                persistent_prompt_filename=_persistent_prompt_filename,
                                is_designated_engineer=_is_designated_engineer,
                                send_agent_prompt=_send_agent_prompt)),
                        panel_event=_panel_event,
                        reply_id=str(data.get("reply_id", "") or ""),
                    )

            elif cmd == "ai_report":
                result = await handle_ai_report_command(
                    data,
                    _build_ai_report_command_runtime(
                        state=state,
                        action_mgr=action_mgr,
                        worktree_mgr=worktree_mgr,
                        board_sync_manager=board_sync_manager,
                        bridge=bridge,
                        dispatch_command=handle_command,
                        panel_event=_panel_event,
                        panel_log=panel_log,
                        resolve_base_dir=_resolve_base_dir,
                        checkpoint_message=_checkpoint_message,
                        checkpoint_on_report=_checkpoint_on_report,
                        checkpoint_worktree_with_submodules=(
                            _checkpoint_worktree_with_submodules
                        ),
                        close_agent_session_only=_close_agent_session_only,
                        ownership_engineer_id_for_dispatch_source=(
                            _ownership_engineer_id_for_dispatch_source
                        ),
                        record_task_boundary=_record_task_boundary,
                    ),
                )

            elif cmd in PIPELINE_COMMAND_NAMES:
                result = await handle_pipeline_command(
                    data,
                    PipelineCommandRuntime(
                        resolve_base_dir=_resolve_base_dir,
                        action_mgr=action_mgr,
                        state=state,
                    ),
                )

            # -- Engineer commands ------------------------------------------

            elif cmd in ENGINEER_OPERATION_COMMAND_NAMES:
                result = await handle_engineer_operation_command(
                    data,
                    _build_engineer_operation_runtime(
                        deliver_engineer_reply_and_resume=_deliver_engineer_reply_and_resume,
                        panel_event=_panel_event,
                        send_agent_prompt=_send_agent_prompt,
                        send_engineer_message_to_agent=_send_engineer_message_to_agent,
                        bridge=bridge,
                        critical_idempotency_key=critical_idempotency_key,
                        critical_request_hash=critical_request_hash,
                        db=db,
                        engineer_buffer=engineer_buffer,
                        state=state,
                    ),
                )

            elif cmd == "stop":
                result = await _handle_daemon_stop_command(
                    daemon_stop_state=daemon_stop_state,
                    schedule_daemon_stop=_schedule_daemon_stop,
                    state=state,
                )

            elif cmd == "restart":
                log.info("Restart requested — cleaning up and re-executing")
                # Persist all agents (status etc.) before restart
                for cell in state.agents.values():
                    state._db_save_agent(cell)
                owner.prepare_exec_handoff()
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception:
                    owner.cancel_exec_handoff()
                    raise

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            result = {"type": "error", "message": str(exc)}

        if db and critical_command_name and critical_idempotency_key:
            try:
                # A deliverable_missing refusal is a recoverable hard-gate
                # failure: the worker can flip it to passing by uploading
                # an artifact and retrying. Persist NEITHER the command
                # receipt nor the captured state so a same-key retry
                # re-runs the gate cleanly. We still clean up the
                # failed-write queue entry — there is no value in
                # replaying the same refusal.
                is_deliverable_missing = (
                    isinstance(result, dict)
                    and result.get("type") == "deliverable_missing"
                )
                # Same recoverable-refusal semantics for the
                # mandatory-review gate (TORQUE:256): don't cache the
                # receipt, drop the queued failed-write so a retry after
                # the worker derives the review re-runs the gate cleanly.
                is_review_required = (
                    isinstance(result, dict)
                    and result.get("type") == "review_required"
                )
                if is_deliverable_missing or is_review_required:
                    db.delete_failed_write_by_key(critical_failed_write_key)
                elif critical_capture_active:
                    state.finalize_critical_write_capture(
                        result,
                        delete_failed_write_key=critical_failed_write_key,
                        surface="internal",
                    )
                else:
                    db.save_command_receipt(
                        idempotency_key=critical_idempotency_key,
                        surface="internal",
                        command_name=critical_command_name,
                        request_hash=critical_request_hash or api_request_hash(data),
                        response=result,
                    )
                    db.delete_failed_write_by_key(critical_failed_write_key)
            except Exception as exc:
                log.exception(
                    "Failed to persist internal command receipt for %s",
                    critical_command_name,
                )
                result = {"type": "error", "message": str(exc)}
            finally:
                if critical_capture_active:
                    state.clear_critical_write_capture()

        await state.broadcast()
        return result

    # -- Events endpoint (agent hooks) ----------------------------------------

    event_routes = build_event_routes(
        db=db,
        event_ingest_client=event_ingest_client,
        ensure_event_ingest_configured=_ensure_event_ingest_configured,
        notifier=notifier,
        perceived_empty_detector=perceived_empty_detector,
        state=state,
        panel_log=panel_log,
        handle_command=handle_command,
        torque_ai_mcp_report_tool_names=_TORQUE_AI_MCP_REPORT_TOOL_NAMES,
        mcp_call_rows_for_ui=_mcp_call_rows_for_ui,
        record_mcp_call_observation=_record_mcp_call_observation,
        replay_api_failed_write_payload=replay_api_failed_write_payload,
        replay_internal_failed_write_payload=replay_internal_failed_write_payload,
    )
    handle_events = event_routes.handle_events
    _panel_event = event_routes.panel_event
    _replay_failed_write = event_routes.replay_failed_write
    _run_failed_write_replay = event_routes.run_failed_write_replay
    _observe_direct_mcp_call = event_routes.observe_direct_mcp_call

    board_sync_manager = BoardSyncManager(
        state,
        panel_event=_panel_event,
        toast=_broadcast_toast,
    )
    board_sync_manager.start()
    log.info("Board sync manager started")



    asyncio.create_task(_run_failed_write_replay())

    # -- Scheduler ----------------------------------------------------------

    asyncio.create_task(
        _pump_auto_dispatch_queue_forever(
            state, handle_command, _panel_event
        )
    )
    asyncio.create_task(
        _scheduler_loop(state, handle_command, _panel_event))
    log.info("Task scheduler and auto-dispatch queue pump started")
    log.info("Startup checkpoint: scheduler tasks scheduled")

    cloud_connector_runtime = await cloud_hooks.start_cloud_connector(
        _build_cloud_connector_context()
    )
    cloud_connector_runtime_holder[0] = cloud_connector_runtime
    if cloud_connector_runtime.enabled and not cloud_connector_runtime.started:
        log.warning(
            "Cloud connector not started (module=%s, error=%s)",
            cloud_connector_runtime.module_name,
            cloud_connector_runtime.error,
        )

    # -- HTTP / WS routes ---------------------------------------------------

    http_routes = build_http_routes(
        state=state,
        handle_command=handle_command,
        runtime_payload=_runtime_payload, state_payload=_state_payload,
        supervisor_banner_state=supervisor_banner_state,
        bridge=bridge,
        terminal_clients=terminal_clients,
        daemon_stop_state=daemon_stop_state,
        db=db,
        attachments_dir=ATTACHMENTS_DIR,
        data_dir=DATA_DIR,
        log_max_lines=_LOG_MAX_LINES,
        api_worker_context_guard=_api_worker_context_guard,
        daemon_stop_rejection_payload=_daemon_stop_rejection_payload,
        hot_json_response=_hot_json_response,
        log_path_for_target=_log_path_for_target,
        payload_wants_compact_snapshot=_payload_wants_compact_snapshot,
        register_ready_ui_ws_client=_register_ready_ui_ws_client,
        request_wants_compact_snapshot=_request_wants_compact_snapshot,
        send_ui_ws_json=_send_ui_ws_json,
        tail_log_entries=_tail_log_entries,
        ui_client_id_from_request=_ui_client_id_from_request,
    )
    handle_index = http_routes.handle_index
    handle_ws = http_routes.handle_ws
    handle_terminal_ws = http_routes.handle_terminal_ws
    handle_api_cmd = http_routes.handle_api_cmd
    handle_profile_get = http_routes.handle_profile_get
    handle_profile_reset = http_routes.handle_profile_reset
    handle_profile_synthetic_agents = http_routes.handle_profile_synthetic_agents
    handle_upload = http_routes.handle_upload
    handle_upload_cleanup = http_routes.handle_upload_cleanup
    handle_attachment_upload = http_routes.handle_attachment_upload
    handle_logs = http_routes.handle_logs
    handle_ui_state = http_routes.handle_ui_state
    handle_serve_attachment = http_routes.handle_serve_attachment
    _cleanup_orphan_attachments = http_routes.cleanup_orphan_attachments

    _cleanup_orphan_attachments()

    # -- Start server -------------------------------------------------------

    app_server = web.Application()
    app_server.router.add_get("/", handle_index)
    app_server.router.add_get("/api/runtime", http_routes.handle_runtime)
    app_server.router.add_get("/ws", handle_ws)
    app_server.router.add_get("/ws/terminal/{cell_id}", handle_terminal_ws)
    app_server.router.add_get("/logs", handle_logs)
    app_server.router.add_get("/api/ui_state", handle_ui_state)
    app_server.router.add_post("/events", handle_events)
    app_server.router.add_post("/api/cmd", handle_api_cmd)
    if profiling.is_enabled():
        app_server.router.add_get("/api/profile", handle_profile_get)
        app_server.router.add_post("/api/profile/reset", handle_profile_reset)
        app_server.router.add_post(
            "/api/profile/synthetic_agents",
            handle_profile_synthetic_agents,
        )

    app_server.router.add_post(
        "/mcp",
        create_mcp_handler(
            handle_command,
            state,
            mcp_call_observer=_observe_direct_mcp_call,
        ),
    )
    app_server.router.add_post("/api/upload", handle_upload)
    app_server.router.add_post("/api/upload/cleanup", handle_upload_cleanup)
    app_server.router.add_post("/api/attachment/upload", handle_attachment_upload)
    app_server.router.add_get(
        "/attachments/{task_id}/{filename}", handle_serve_attachment)
    from .config import SCRIPT_DIR
    app_server.router.add_static("/static", SCRIPT_DIR / "static")
    log.info("Startup checkpoint: routes registered")

    runner = web.AppRunner(app_server)
    log.info("Startup checkpoint: AppRunner created")
    try:
        await runner.setup()
        log.info("Startup checkpoint: runner setup complete")
        site = web.TCPSite(runner, BIND_HOST, WS_PORT, reuse_address=True)
        log.info("Startup checkpoint: TCPSite created")
        try:
            await site.start()
        except OSError as exc:
            log.error("Cannot bind port %d: %s — is another instance running?",
                      WS_PORT, exc)
            raise
        log.info("Startup checkpoint: site start complete")
        log.info("HTTP/WS server listening on %s:%d", BIND_HOST, WS_PORT)

        log.info("Open http://127.0.0.1:%d/ in a browser", WS_PORT)

        # Route OS-signal termination (app restart/upgrade, quit, `kill`) through
        # the graceful cleanup in the `finally` below.  Without this, SIGTERM/
        # SIGINT bypass cleanup entirely, leaving the embedding
        # ProcessPoolExecutor worker blocked on its input queue and reparented to
        # launchd — leaking ~4 GB per restart.  Mirrors the SIGTERM/SIGINT
        # handling already in pty_supervisor and event_ingest_daemon.
        _stop_loop = asyncio.get_running_loop()

        def _request_daemon_stop(signum: int) -> None:
            log.info("Received signal %s; initiating graceful daemon shutdown",
                     signum)
            daemon_stop_event.set()

        for _stop_sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                _stop_loop.add_signal_handler(
                    _stop_sig, _request_daemon_stop, _stop_sig)

        await daemon_stop_event.wait()
    finally:
        try:
            if supervisor_watchdog is not None:
                try:
                    await supervisor_watchdog.stop()
                except Exception:
                    log.exception("Supervisor liveness watchdog shutdown failed")
            try:
                await metrics_daemon.stop()
            except Exception:
                log.exception("Metrics daemon shutdown failed")
            await board_sync_manager.stop()
            await _shutdown_daemon_runtime(
                terminal_clients=terminal_clients,
                ui_ws_clients=state._ws_clients,
                panel_log=panel_log,
                event_ingest_drainer=event_ingest_drainer,
                event_ingest_client=event_ingest_client,
                cloud_connector_runtime=cloud_connector_runtime_holder[0],
                ai_index_service=ai_index_service,
                ai_summary_service=ai_summary_service,
                bridge=bridge,
                runner=runner,
                state=state,
                db=db,
            )
        finally:
            _release_profile_daemon_owner(owner)
