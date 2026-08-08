"""AgentCell dataclass and MatrixState persistence layer."""

import asyncio
import copy
import contextvars
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

try:
    import orjson
except ImportError:  # pragma: no cover - dependency is installed by Makefile.
    orjson = None

from .config import (
    DATA_DIR,
    DEFAULT_ARCHITECT_BOOT_NUDGE,
    DEFAULT_COMMAND,
    DEFAULT_ENGINEER_BOOT_NUDGE,
    log,
)
from . import cloud_hooks
from . import profiling
from .artifacts import normalize_artifacts, normalize_attachments
from .behavior_overlay import (
    BEHAVIOR_OVERLAY_COMBINED_MAX_BYTES,
    BEHAVIOR_OVERLAY_ROLE_KINDS,
    BehaviorOverlayScope,
    BehaviorOverlayValidationError,
    DEFAULT_BEHAVIOR_OVERLAY_TEXT,
    behavior_overlay_diff,
    coerce_behavior_overlay_scope,
    lint_overlay_text,
    overlay_text_bytes,
    overlay_text_sha256,
    render_behavior_overlay_block,
    validate_overlay_text,
    version_summary,
)
from .db import TorqueDB
from .commands.user_dm import user_dm_command_catalog
from .idea_briefs import (
    IDEA_BRIEF_TEXT_FIELDS,
    idea_brief_contract_metadata,
    idea_brief_is_archived,
)
from .metrics import (
    METRICS_RETENTION_SECONDS,
    METRICS_ROLLUP_RESOLUTION_SECONDS,
    METRICS_SCHEMA_VERSION,
    MetricsCollector,
)
from .engineer_ask_events import (
    ENGINEER_ASK_RESOLVED,
    ENGINEER_AWAITING_HUMAN_INPUT,
    emit_engineer_ask_resolved_event,
    emit_engineer_awaiting_human_input_event,
)
from .task_ids import (
    format_initiative_id,
    format_derived_task_id,
    format_root_task_id,
    is_canonical_initiative_id,
    is_canonical_task_id,
    normalize_group_prefix,
    parse_initiative_id,
    parse_task_id,
)
from .task_content import (
    TASK_MESSAGES_LIMIT,
    clamp_task_messages,
    compute_task_content_hash,
)
from .worktree_boundaries import clear_stale_successor_references
from .services.areas import AreaService
from .services.architect_governance import ArchitectGovernanceService
from .services.behavior_overlays import BehaviorOverlayService
from .services.idea_briefs import IdeaBriefService
from .services.initiatives import InitiativeService
from .services.journals import (
    _ENGINEER_WORKLOG_LIMIT,
    _safe_journal_filename,
    JournalService,
)
from .services.metrics import (
    MetricsService,
    _SYSTEM_HEALTH_AGE_BUCKETS,
    _SYSTEM_HEALTH_EVENT_KINDS,
    _SYSTEM_HEALTH_WINDOWS,
    _health_action_name,
    _health_bucket_index,
    _health_bucket_label,
    _health_percentile,
    _health_response_task_ids,
    _health_series,
    _health_task_group,
    _health_task_id,
    _health_task_value,
    _parse_health_timestamp,
)
from .services.operator_notices import OperatorNoticeService
from .services.task_watches import TaskWatchService
from .services.reminders import ReminderService
from .services.thinking import ThinkingService
from .perceived_empty import (
    coerce_perceived_empty_threshold,
    coerce_perceived_empty_window_seconds,
)

ARCHIVED_LANE = "Archived"
_RESERVED_LANES = ("Backlog", "To Do", "In Progress", "Done", ARCHIVED_LANE)
_DEFAULT_LANES = list(_RESERVED_LANES)
_DEFAULT_STATUS_BAR_VISIBILITY = {
    # Lean resting default for narrow workspaces: only active tasks and
    # attention are visible, while deploy remains opt-in-by-state (hidden until
    # there is something pending).
    "daemon_status": False,
    "claude_usage": False,
    "codex_usage": False,
    "deploy": True,
    "health": False,
    "workload": False,
    "tasks": True,
    "attention": True,
}
AI_GENERATION_PROVIDERS = ("anthropic", "openai_compatible")
AI_EMBEDDING_RUNTIMES = ("sentence_transformers",)
AI_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
AI_BOOT_SUMMARY_DEFAULT_MIN_INTERVAL_SECONDS = 600
AI_BOOT_SUMMARY_DEFAULT_MAX_REFRESHES_PER_HOUR = 20
AI_INDEX_CORPUS_KEYS = (
    "architect_journals",
    "engineer_journals",
    "decisions",
    "tasks",
    "engineer_peer_threads",
)
AGENT_TOMBSTONE_RETENTION_SECONDS = 7 * 86400
AGENT_MESSAGE_HISTORY_LIMIT = 100
PEER_MESSAGE_CACHE_LIMIT = 20
DIRECT_MESSAGE_CACHE_LIMIT = 100
AGENT_PEER_THREAD_MESSAGE_LIMIT = 100
AGENT_PEER_THREAD_SEED_ROW_LIMIT = 5000
ENGINEER_DISPATCH_SHAPE_EVENT_LIMIT = 100
_ENGINEER_DISPATCH_SHAPE_ORDER = ("serial", "batch", "warm_cluster")
_ENGINEER_DISPATCH_SHAPES = set(_ENGINEER_DISPATCH_SHAPE_ORDER)
_VERIFICATION_MODES = {"", "deploy", "restart"}
_VERIFICATION_STATES = {"", "pending", "attempted", "passed", "failed"}
_VERIFICATION_TEST_OUTCOMES = {
    "",
    "passed",
    "full_suite_passed",
    "full_suite_attempted",
    "unrelated_flake_accepted",
    "narrower_suite_accepted",
    "failed",
}
_VERIFICATION_REVIEWER_ACCEPTANCES = {
    "",
    "accepted_flake_evidence",
    "accepted_narrower_suite",
}
TASK_DISPATCH_STATE_QUEUED = "queued"
TASK_DISPATCH_STATE_LIVE = "live"
TASK_DISPATCH_STATES = {TASK_DISPATCH_STATE_QUEUED, TASK_DISPATCH_STATE_LIVE}
INITIATIVE_PLANNING_STATUSES = {
    "triage",
    "now",
    "next",
    "later",
    "parked",
    "shipped",
}
_ENGINEER_AUTONOMY_MODES = {
    "suggest_only",
    "dispatch_when_clear",
    "aggressive_auto_continue",
}
_DEFAULT_ENGINEER_AUTONOMY_MODE = "dispatch_when_clear"
_DEFAULT_ENGINEER_DEFAULT_WORKER_CONCURRENCY = 2
_ENGINEER_WAVE_SIZE_PREFERENCES = {
    "small",
    "balanced",
    "large",
}
_DEFAULT_ENGINEER_WAVE_SIZE_PREFERENCE = "small"
_ENGINEER_SAME_AGENT_FOLLOW_UP_PREFERENCES = {
    "balanced",
    "prefer_same_agent",
    "prefer_fresh_agent",
}
_DEFAULT_ENGINEER_SAME_AGENT_FOLLOW_UP_PREFERENCE = "balanced"
_ENGINEER_DIGEST_VERBOSITIES = {
    "compact",
    "balanced",
    "detailed",
}
_DEFAULT_ENGINEER_DIGEST_VERBOSITY = "balanced"
_ARCHITECT_AUTONOMY_MODES = {
    "dispatch_freely",
    "dispatch_after_confirm",
    "ask_always",
}
_DEFAULT_ARCHITECT_AUTONOMY_MODE = "dispatch_after_confirm"
_ARCHITECT_DIGEST_VERBOSITIES = {
    "terse",
    "balanced",
    "verbose",
}
_DEFAULT_ARCHITECT_DIGEST_VERBOSITY = "balanced"
_DEFAULT_ARCHITECT_PUSH_INTERVAL = 300
_DEFAULT_ARCHITECT_MAX_INTERVAL = 600
_DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL = 0
_DEFAULT_ARCHITECT_SUPPRESS_EMPTY_DIGESTS = True
_DEFAULT_ARCHITECT_JOURNAL_CHECKPOINT_FREQUENCY = "every_10_actions"
_DEFAULT_ARCHITECT_REVIEW_GATE_THRESHOLDS = {
    "ship_direct_max": 50,
    "review_default_above": 150,
    "self_review_bypass_allowed": False,
}
_ENGINEER_NOTIFICATION_PRESETS = {
    "quiet": {
        "digest_verbosity": "compact",
        "push_interval": 120,
        "max_interval": 600,
        "heartbeat_interval": 0,
        "enabled_events": [
            "task_derived",
            "task_health_alert",
        ],
    },
    "normal": {
        "digest_verbosity": "balanced",
        "push_interval": 60,
        "max_interval": 300,
        "heartbeat_interval": 300,
        "enabled_events": [
            "agent_started",
            "task_dispatched",
            "task_derived",
            "task_health_alert",
        ],
    },
    "noisy": {
        "digest_verbosity": "detailed",
        "push_interval": 30,
        "max_interval": 120,
        "heartbeat_interval": 60,
        "enabled_events": [
            "agent_started",
            "task_dispatched",
            "task_derived",
            "agent_progress",
            "task_health_alert",
        ],
    },
}
_ARCHITECT_DIGEST_LEGACY_DEFAULT_ENABLED_EVENTS = [
    "task_done",
    "task_blocked",
    "task_error",
    "task_ask",
    "task_derive",
    "task_completed",
    "agent_blocked",
    "agent_error",
    "ask_created",
    "task_derived",
    "pipeline_complete",
    "engineer_hired",
    "engineer_fired",
    "engineer_dismissed",
    "engineer_rehired",
    "workflow_breach",
    "engineer_queue_empty",
    "perceived_empty_episode",
    "engineer_peer_thread_opened",
    "engineer_peer_thread_active",
    ENGINEER_AWAITING_HUMAN_INPUT,
    ENGINEER_ASK_RESOLVED,
]
_ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS: list[str] = []
_ENGINEER_MESSAGE_EXPIRY_NOTE = "Expired because parent task completed."
_ENGINEER_ESCALATION_STYLES = {
    "ask_early",
    "note_then_ask",
    "keep_moving",
}
_DEFAULT_ENGINEER_ESCALATION_STYLE = "note_then_ask"
_WORKTREE_MERGE_CLEANUP_MODES = {
    "keep",
    "close",
    "remove",
    "close_remove",
    "auto_sweep",
}
_DEFAULT_WORKTREE_MERGE_CLEANUP = "keep"
_BOARD_SYNC_PROVIDERS = {"none", "github"}
_BOARD_SYNC_STATES = {"idle", "queued", "syncing", "error"}
_ENGINEER_MERGE_MODES = {
    "pr",
    "direct",
    "engineer-choice",
}
_DEFAULT_ENGINEER_MERGE_MODE = "pr"
_DEFAULT_GUIDANCE_HINT_CADENCE = 4
_MIN_GUIDANCE_HINT_CADENCE = 0
_MAX_GUIDANCE_HINT_CADENCE = 100
_ENGINEER_STREAM_CARD_LIMIT = 10
_ENGINEER_STREAM_CONTEXT_LIMIT = 5
_ENGINEER_STREAM_DELTA_TRIGGER_OPS = {
    "agent_remove",
    "agent_upsert",
    "group_remove",
    "group_rename",
    "group_update",
    "task_remove",
    "task_upsert",
}
XTERM_SCROLLBACK_DEFAULT = 2000
XTERM_SCROLLBACK_MIN = 100
XTERM_SCROLLBACK_MAX = 100_000
HOT_JSON_OFFLOAD_BYTES = 100 * 1024
HOT_JSON_OFFLOAD_DELTA_OPS = 25
COMPACT_SNAPSHOT_PROTOCOL = "compact-v1"
COMPACT_AGENT_MCP_MESSAGE_LIMIT = 20
COMPACT_AGENT_CHANGED_FILE_LIMIT = 100
COMPACT_AGENT_CLASS_FIELDS = (
    "id",
    "version",
    "base_kind",
    "name",
    "display_name",
    "primary_display_name",
    "primary_identity_label",
    "secondary_base_kind_label",
    "secondary_base_kind_metadata",
    "status",
    "lifecycle",
    "warnings",
    "external_connector_caveat",
    "builtin",
    "archived",
    "disabled",
)
COMPACT_BOARD_TASK_FIELDS = (
    "id",
    "task",
    "slug",
    "group",
    "lane",
    "position",
    "action_name",
    "labels",
    "agent_id",
    "assigned_engineer_id",
    "assigned_architect_id",
    "parent_task_id",
    "pipeline_depth",
    "status",
    "created_at",
    "updated_at",
    "scheduled_at",
    "dispatch_state",
    "depends_on",
    "provider",
    "external_id",
    "external_url",
    # Compact summaries for fields whose full value can grow with provider
    # metadata, nested evidence, or message history. Full values remain
    # available via task_detail.
    "board_sync",
    "health_state",
    "health_since",
    "health_details",
    "verification_state",
    "verification_mode",
    "messages",
    "messages_thread_summary",
    "lane_entered_at",
    "worktree_boundary",
    "resume_after_boundary_task_id",
    "deliverable_required",
    "deliverable_type",
    "requires_review",
    "pre_approved_by",
    "finalization_mode",
    "finalization_status",
    # Small drift-detection string; sync consumers compare it against the
    # persisted row, so it must ride every task summary and delta.
    "task_content_hash",
)


def _hot_json_default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError


def hot_json_dumps_bytes(payload) -> bytes:
    """Serialize a hot-path WS/API payload to JSON bytes."""
    if orjson is not None:
        return orjson.dumps(
            payload,
            default=_hot_json_default,
            option=(
                orjson.OPT_NON_STR_KEYS
                | orjson.OPT_PASSTHROUGH_DATETIME
            ),
        )
    return json.dumps(payload, default=_hot_json_default).encode("utf-8")


def hot_json_dumps(payload) -> str:
    """Serialize a hot-path WS payload to a string."""
    return hot_json_dumps_bytes(payload).decode("utf-8")


def _approx_json_size(value, *, budget: int = HOT_JSON_OFFLOAD_BYTES) -> int:
    """Return a cheap bounded size estimate for offload decisions."""
    total = 0
    stack = [(value, 0)]
    seen_containers: set[int] = set()
    while stack and total < budget:
        item, depth = stack.pop()
        if item is None or isinstance(item, bool):
            total += 4
        elif isinstance(item, (int, float)):
            total += 16
        elif isinstance(item, str):
            total += len(item) + 2
        elif isinstance(item, (bytes, bytearray)):
            total += len(item)
        elif isinstance(item, dict):
            item_id = id(item)
            if item_id in seen_containers:
                continue
            seen_containers.add(item_id)
            total += len(item) * 4
            for key, val in item.items():
                stack.append((key, depth + 1))
                stack.append((val, depth + 1))
        elif isinstance(item, (list, tuple)):
            item_id = id(item)
            if item_id in seen_containers:
                continue
            seen_containers.add(item_id)
            total += len(item) * 2
            for val in item:
                stack.append((val, depth + 1))
        else:
            total += len(str(item)) + 2
    return total


def hot_json_should_offload(payload) -> bool:
    """Whether serializing ``payload`` should leave the event loop."""
    if isinstance(payload, dict):
        payload_type = payload.get("type")
        if payload_type == "delta":
            ops = payload.get("ops") or []
            if len(ops) >= HOT_JSON_OFFLOAD_DELTA_OPS:
                return True
            return _approx_json_size(ops) >= HOT_JSON_OFFLOAD_BYTES
    return _approx_json_size(payload) >= HOT_JSON_OFFLOAD_BYTES


async def hot_json_dumps_bytes_async(
    payload, *, offload: Optional[bool] = None
) -> bytes:
    """Serialize JSON bytes, moving large payloads off the event loop."""
    if offload is None:
        offload = hot_json_should_offload(payload)
    if offload:
        return await asyncio.to_thread(hot_json_dumps_bytes, payload)
    return hot_json_dumps_bytes(payload)


async def hot_json_dumps_async(payload, *, offload: Optional[bool] = None) -> str:
    """Serialize JSON text, moving large payloads off the event loop."""
    data = await hot_json_dumps_bytes_async(payload, offload=offload)
    return data.decode("utf-8")


def normalize_xterm_scrollback(value, *, strict: bool = False) -> int:
    """Return a valid xterm.js scrollback line limit.

    Runtime updates use strict mode so invalid operator input is rejected
    rather than silently changed. Non-strict mode is used while loading old
    or hand-edited state so a bad persisted value cannot prevent startup.
    """
    raw = value
    try:
        if isinstance(raw, str):
            stripped = raw.strip()
            if not re.fullmatch(r"[+-]?\d+", stripped):
                raise ValueError
            value = int(stripped)
        elif isinstance(raw, float):
            if not raw.is_integer():
                raise ValueError
            value = int(raw)
        else:
            value = int(raw)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(
                "xterm_scrollback must be an integer between "
                f"{XTERM_SCROLLBACK_MIN} and {XTERM_SCROLLBACK_MAX}"
            )
        return XTERM_SCROLLBACK_DEFAULT
    if value < XTERM_SCROLLBACK_MIN or value > XTERM_SCROLLBACK_MAX:
        if strict:
            raise ValueError(
                "xterm_scrollback must be between "
                f"{XTERM_SCROLLBACK_MIN} and {XTERM_SCROLLBACK_MAX}"
            )
        return XTERM_SCROLLBACK_DEFAULT
    return value


def normalize_engineer_autonomy_mode(value) -> str:
    value = str(value or "").strip()
    if value in _ENGINEER_AUTONOMY_MODES:
        return value
    return _DEFAULT_ENGINEER_AUTONOMY_MODE


def normalize_default_worker_concurrency(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_ENGINEER_DEFAULT_WORKER_CONCURRENCY
    return max(1, value)


def normalize_engineer_wave_size_preference(value) -> str:
    value = str(value or "").strip()
    if value in _ENGINEER_WAVE_SIZE_PREFERENCES:
        return value
    return _DEFAULT_ENGINEER_WAVE_SIZE_PREFERENCE


def normalize_mcp_call_log_args_capture(value) -> str:
    value = str(value or "metadata").strip().lower()
    if value in {"off", "metadata", "full"}:
        return value
    return "metadata"


def normalize_relay_text(value) -> str:
    """Trim a relay (cloud connector) text setting. Empty == unset/fallback."""
    return str(value or "").strip()


def normalize_relay_enabled(value) -> bool:
    """Coerce the relay-enabled toggle, tolerating string forms from the UI."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def default_ai_index_corpus() -> dict[str, bool]:
    """Return the default corpus toggles for AI semantic indexing."""
    return {key: True for key in AI_INDEX_CORPUS_KEYS}


def normalize_codex_fast_mode(value, *, strict: bool = True) -> str:
    """Normalize the persisted three-state Codex Fast launch preference."""
    mode = str(value or "inherit").strip().lower()
    if mode not in {"inherit", "on", "off"}:
        if strict:
            raise ValueError("codex fast mode must be inherit, on, or off")
        return "inherit"
    return mode


def normalize_ai_generation_provider(value, *, strict: bool = True) -> str:
    provider = str(value or "").strip().lower()
    if provider not in AI_GENERATION_PROVIDERS:
        if not strict:
            return AI_GENERATION_PROVIDERS[0]
        raise ValueError(
            "ai_generation_provider must be one of "
            f"{', '.join(AI_GENERATION_PROVIDERS)}"
        )
    return provider


def normalize_ai_embedding_runtime(value, *, strict: bool = True) -> str:
    runtime = str(value or "").strip().lower()
    if runtime not in AI_EMBEDDING_RUNTIMES:
        if not strict:
            return AI_EMBEDDING_RUNTIMES[0]
        raise ValueError(
            "ai_embedding_runtime must be one of "
            f"{', '.join(AI_EMBEDDING_RUNTIMES)}"
        )
    return runtime


def normalize_ai_text(value) -> str:
    return str(value or "").strip()


def normalize_ai_index_corpus(value) -> dict[str, bool]:
    normalized = default_ai_index_corpus()
    if not isinstance(value, dict):
        return normalized
    for key in normalized:
        if key in value:
            normalized[key] = normalize_relay_enabled(value.get(key))
    return normalized


def normalize_ai_boot_summary_min_interval_seconds(value) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return AI_BOOT_SUMMARY_DEFAULT_MIN_INTERVAL_SECONDS
    return max(0, parsed)


def normalize_ai_boot_summary_max_refreshes_per_hour(value) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return AI_BOOT_SUMMARY_DEFAULT_MAX_REFRESHES_PER_HOUR
    return max(0, parsed)


def default_status_bar_visibility() -> dict[str, bool]:
    return dict(_DEFAULT_STATUS_BAR_VISIBILITY)


def normalize_status_bar_visibility(value) -> dict[str, bool]:
    """Merge a persisted/user status-bar visibility map with current defaults."""
    normalized = default_status_bar_visibility()
    if not isinstance(value, dict):
        return normalized
    for key in normalized:
        if key in value:
            normalized[key] = normalize_relay_enabled(value.get(key))
    return normalized


def normalize_event_ingest_max_rows(value) -> int:
    try:
        rows = int(value)
    except (TypeError, ValueError):
        return 100_000
    return max(1, rows)


def normalize_event_ingest_max_days(value) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 14
    return max(0, days)


def normalize_mcp_call_log_full_capture_tools(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_items = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple)):
        raw_items = value
    else:
        return []
    out = []
    seen = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_engineer_same_agent_follow_up_preference(value) -> str:
    value = str(value or "").strip()
    if value in _ENGINEER_SAME_AGENT_FOLLOW_UP_PREFERENCES:
        return value
    return _DEFAULT_ENGINEER_SAME_AGENT_FOLLOW_UP_PREFERENCE


def normalize_engineer_digest_verbosity(value) -> str:
    value = str(value or "").strip()
    if value in _ENGINEER_DIGEST_VERBOSITIES:
        return value
    return _DEFAULT_ENGINEER_DIGEST_VERBOSITY


def get_engineer_notification_preset(name) -> dict:
    preset = _ENGINEER_NOTIFICATION_PRESETS.get(
        str(name or "").strip().lower()
    )
    if not preset:
        raise ValueError(f"Unknown Engineer notification preset: {name}")
    return {
        "digest_verbosity": preset["digest_verbosity"],
        "push_interval": preset["push_interval"],
        "max_interval": preset["max_interval"],
        "heartbeat_interval": preset["heartbeat_interval"],
        "enabled_events": list(preset["enabled_events"]),
    }


def normalize_engineer_escalation_style(value) -> str:
    value = str(value or "").strip()
    if value in _ENGINEER_ESCALATION_STYLES:
        return value
    return _DEFAULT_ENGINEER_ESCALATION_STYLE


def normalize_architect_autonomy_mode(value, *, strict: bool = False) -> str:
    value = str(value or "").strip()
    if value in _ARCHITECT_AUTONOMY_MODES:
        return value
    if strict:
        raise ValueError(
            "architect_autonomy_mode must be one of: "
            + ", ".join(sorted(_ARCHITECT_AUTONOMY_MODES))
        )
    return _DEFAULT_ARCHITECT_AUTONOMY_MODE


def normalize_architect_digest_verbosity(value, *, strict: bool = False) -> str:
    value = str(value or "").strip()
    if value in _ARCHITECT_DIGEST_VERBOSITIES:
        return value
    if strict:
        raise ValueError(
            "architect_digest_verbosity must be one of: "
            + ", ".join(sorted(_ARCHITECT_DIGEST_VERBOSITIES))
        )
    return _DEFAULT_ARCHITECT_DIGEST_VERBOSITY


def normalize_architect_enabled_events(value) -> list[str]:
    """Return optional architect digest events, excluding the mandatory floor."""
    if isinstance(value, str):
        value = [token.strip() for token in value.split(",")]
    seen: set[str] = set()
    result: list[str] = []
    for item in (value or []):
        event_kind = str(item or "").strip()
        if (
                not event_kind
                or event_kind in ARCHITECT_MANDATORY_EVENTS
                or event_kind in seen):
            continue
        result.append(event_kind)
        seen.add(event_kind)
    return result


def normalize_architect_journal_checkpoint_frequency(
        value, *, strict: bool = False) -> str:
    value = str(value or "").strip()
    if (
            value == "manual_only"
            or re.fullmatch(r"every_[1-9]\d*_actions", value)
            or re.fullmatch(r"every_[1-9]\d*_minutes", value)):
        return value
    if strict:
        raise ValueError(
            "architect_journal_checkpoint_frequency must be manual_only, "
            "every_N_actions, or every_N_minutes"
        )
    return _DEFAULT_ARCHITECT_JOURNAL_CHECKPOINT_FREQUENCY


def normalize_architect_review_gate_thresholds(
        value, *, strict: bool = False) -> dict:
    defaults = dict(_DEFAULT_ARCHITECT_REVIEW_GATE_THRESHOLDS)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            if strict:
                raise ValueError(
                    "architect_review_gate_thresholds must be a JSON object"
                )
            return defaults
    if not isinstance(value, dict):
        if strict:
            raise ValueError("architect_review_gate_thresholds must be an object")
        return defaults

    result = defaults
    for key in ("ship_direct_max", "review_default_above"):
        if key not in value:
            continue
        try:
            parsed = int(value.get(key))
        except (TypeError, ValueError):
            if strict:
                raise ValueError(
                    f"architect_review_gate_thresholds.{key} must be an integer"
                )
            continue
        result[key] = max(0, parsed)
    if "self_review_bypass_allowed" in value:
        result["self_review_bypass_allowed"] = bool(
            value.get("self_review_bypass_allowed")
        )
    return result


def normalize_worktree_merge_cleanup(value) -> str:
    value = str(value or "").strip()
    if value in _WORKTREE_MERGE_CLEANUP_MODES:
        return value
    return _DEFAULT_WORKTREE_MERGE_CLEANUP


def normalize_engineer_merge_mode(value) -> str:
    value = str(value or "").strip().lower()
    if value in _ENGINEER_MERGE_MODES:
        return value
    return _DEFAULT_ENGINEER_MERGE_MODE


def normalize_guidance_hint_cadence(value) -> int:
    """Return the group-level recurring guidance-hint cadence.

    ``0`` preserves the legacy "show every time" behavior. Positive values
    show a hint on the first occurrence for an agent/session and then when the
    occurrence count is divisible by the configured cadence.
    """
    try:
        if isinstance(value, str):
            stripped = value.strip()
            if not re.fullmatch(r"[+-]?\d+", stripped):
                raise ValueError
            parsed = int(stripped)
        elif isinstance(value, float):
            if not value.is_integer():
                raise ValueError
            parsed = int(value)
        else:
            parsed = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_GUIDANCE_HINT_CADENCE
    return min(
        _MAX_GUIDANCE_HINT_CADENCE,
        max(_MIN_GUIDANCE_HINT_CADENCE, parsed),
    )


def _normalize_engineer_hint_snoozes(value) -> dict[str, float]:
    """Return durable Engineer hint snoozes keyed by deterministic fingerprint."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    now = time.time()
    for raw_key, raw_expires_at in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        try:
            expires_at = float(raw_expires_at or 0)
        except (TypeError, ValueError):
            continue
        if expires_at <= now:
            continue
        result[key] = expires_at
    return result


def merge_cleanup_flags(mode: str) -> tuple[bool, bool]:
    mode = normalize_worktree_merge_cleanup(mode)
    if mode == "close":
        return (True, False)
    if mode == "remove":
        return (False, True)
    if mode in {"close_remove", "auto_sweep"}:
        return (True, True)
    return (False, False)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


@dataclass
class BoardTask:
    id: str
    task: str                   # short title (required)
    description: str = ""       # longer context / details (optional)
    slug: str = ""              # auto-generated from task text
    group: str = ""             # owning group (must be an existing group)
    action_name: str = ""       # action used for prompt rendering (optional)
    action_vars: dict = field(default_factory=dict)  # action variable values
    agent_template: str = ""    # agent template used when creating a new agent
    instructions: str = ""      # DEPRECATED — kept for backward compat
    context: str = ""           # DEPRECATED — kept for backward compat
    criteria: str = ""          # DEPRECATED — kept for backward compat
    lane: str = "Backlog"
    position: int = 0
    agent_id: str = ""          # concrete agent working on this (optional)
    assigned_engineer_id: str = ""  # owning engineer responsible for the task
    assigned_architect_id: str = ""  # owning architect responsible for the task
    created_by_architect_id: str = ""  # architect provenance
    created_by_engineer_id: str = ""  # engineer provenance
    suggested_action: str = ""  # non-binding architect action hint
    suggested_specialization: str = ""  # non-binding routing hint: specialization slug
    reply_agent_id: str = ""    # worker expected to answer this follow-up
    labels: list[str] = field(default_factory=list)
    created_at: str = ""        # ISO 8601
    updated_at: str = ""        # ISO 8601
    lane_entered_at: str = ""   # ISO 8601 of most recent transition into lane
    # Provider link fields
    provider: str = ""          # "jira", "linear", "github"
    external_id: str = ""       # e.g. "PROJ-123"
    external_url: str = ""      # link back to provider
    # Board sync metadata. Common sync state is top-level; provider-specific
    # adapter data lives under the provider key (for example ``github``).
    board_sync: dict = field(default_factory=dict)
    # Pipeline fields (Phase 4b)
    parent_task_id: str = ""    # task this was derived from (empty for root tasks)
    pipeline_depth: int = 0     # 0 for root, auto-incremented from parent
    pipeline_root_id: str = ""  # ID of the chain's root task (self.id for roots)
    status: str = ""            # pipeline status (e.g. "On Review", "Fixing")
    # Scheduling
    scheduled_at: str = ""      # ISO 8601 — auto-dispatch when this time arrives
    # Dispatch visibility: queued = staged on the board, live = dispatched
    # to an engineer/worker via the dispatch/message path.
    dispatch_state: str = TASK_DISPATCH_STATE_QUEUED
    # Activity log — persisted history of agent reports on this task
    messages: list = field(default_factory=list)  # [{timestamp, action, message, agent_name}]
    # Inline engineer→agent messages that do not require a reply task.
    # [{timestamp, sender_agent_id, recipient_agent_id, content, reply_required}]
    messages_thread: list = field(default_factory=list)
    # Explicit dependencies — task IDs that must be Done before dispatch
    depends_on: list = field(default_factory=list)  # [task_id, ...]
    # Legacy image attachments — [{path, filename, mime_type}]
    attachments: list = field(default_factory=list)
    # Derived task-health snapshot (advisory only; never drives lane/status)
    health_state: str = "healthy"
    health_since: str = ""
    health_details: dict = field(default_factory=dict)
    # Structured task artifacts — logs, diffs, reports, snippets, docs, refs
    artifacts: list = field(default_factory=list)
    # Verification summary for user-visible/runtime-affecting work
    verification_mode: str = ""      # "" | deploy | restart
    verification_state: str = ""     # "" | pending | attempted | passed | failed
    verification_notes: str = ""
    verification_updated_at: str = ""
    verification_updated_by: str = ""
    verification_summary: dict = field(default_factory=dict)
    # Completion evidence captured from existing verification/merge/artifact
    # primitives. This is advisory surfacing, not a completion gate.
    completion_evidence: dict = field(default_factory=dict)
    # Task-scoped shared-worktree merge boundary metadata.
    worktree_boundary: dict = field(default_factory=dict)
    resume_after_boundary_task_id: str = ""
    # Archive metadata. Archived tasks move to the Archived lane but keep
    # their original lane and archive timestamp for restoration/reporting.
    archived_at: str = ""
    archived_from_lane: str = ""
    # Deliverable contract. Opt-in via the action's ``deliverable`` block
    # or an explicit kwarg on task-create. When ``deliverable_required``
    # is true, ``torque_done`` / ``torque_ready`` refuse until the worker
    # uploads a matching artifact via ``torque_task_upload_artifact``.
    deliverable_required: bool = False
    deliverable_type: str = ""           # advisory: report | plan | inventory | ...
    deliverable_format: str = ""         # advisory: markdown | json | yaml | ...
    deliverable_artifact_title: str = ""  # default upload title for the worker
    # Mandatory-review contract (TORQUE:256). When ``requires_review`` is true,
    # ``torque_done`` / ``torque_ready`` refuse until the worker either derives the
    # required transition (e.g. ``feature/review``) OR carries a non-empty
    # ``pre_approved_by`` set by a reviewer-issued ``pre_approved: true``
    # transition. Workers cannot self-grant the bypass.
    requires_review: bool = False
    pre_approved_by: str = ""
    # Explicit opt-in finalization contract. Legacy rows deliberately keep
    # ``legacy`` and no policy so migrations never reopen historical work.
    finalization_mode: str = "legacy"  # legacy | merge | review_only
    required_review_gates: list = field(default_factory=list)
    finalization_boundary: dict = field(default_factory=dict)
    finalization_audit: list = field(default_factory=list)
    finalization_status: dict = field(default_factory=dict)
    # Null/empty means UNVERSIONED: readable, but ineligible as pinned evidence.
    task_content_hash: str | None = None


@dataclass
class Schedule:
    """A scheduled task dispatch — one-shot or recurring."""
    id: str
    name: str                       # human-readable name (required)
    slug: str = ""                  # auto-generated from name
    # Task template fields
    task_template: str = ""         # task title ({date}, {time}, {datetime} placeholders)
    description: str = ""           # task description template
    group: str = ""                 # target group (required)
    action_name: str = ""           # action to attach
    action_vars: dict = field(default_factory=dict)
    agent_template: str = ""        # agent template override
    labels: list[str] = field(default_factory=list)
    # Trigger
    cron_expr: str = ""             # 5-field cron (empty = one-shot)
    scheduled_at: str = ""          # ISO 8601 for one-shot (empty = recurring)
    timezone: str = ""              # IANA timezone (empty = UTC)
    # State
    enabled: bool = True
    last_run_at: str = ""           # ISO 8601 of last fire
    next_run_at: str = ""           # ISO 8601 of next fire (computed)
    run_count: int = 0              # total times fired
    last_task_id: str = ""          # ID of most recently created task
    # Metadata
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AgentMessageLoop:
    """A user-scheduled direct-message loop for one agent."""
    id: str
    agent_id: str
    group_name: str = ""
    interval_seconds: int = 0
    message: str = ""
    status: str = "active"
    created_by: str = "user"
    stopped_by: str = ""
    stop_reason: str = ""
    created_at: float = 0
    updated_at: float = 0
    next_run_at: float = 0
    last_run_at: float = 0
    run_count: int = 0
    last_message_id: str = ""
    deferred_at: float = 0
    deferred_reason: str = ""


@dataclass
class AutoDispatchQueueEntry:
    task_id: str
    agent_group: str = ""
    max_concurrent: int = 1
    target_agent_id: str = ""
    engineer_owner_id: str = ""
    provider: str = ""
    enqueued_at: str = ""


@dataclass
class AgentCell:
    id: str
    name: str
    group: str
    slug: str = ""              # auto-generated from name
    cell_type: str = "agent"  # "agent" | "terminal"
    terminal_backend: str = "pty"  # current default; reserved for future backends
    runner_backend: str = "pty"  # agent runtime backend: pty
    session_id: Optional[str] = None
    profile: str = "Default"
    command: str = ""
    directory: str = ""  # working dir on create/relaunch
    tab_color: str = ""  # optional UI/session accent color (e.g. "#f85149")
    icon: str = ""  # custom icon character (from AGENT_ICONS set)
    template: str = ""  # template used to create this agent
    window_id: str = ""  # terminal/UI window this session lives in
    parent_id: str = ""  # for child terminals: the owning agent's ID
    # Runtime session turn-taking only; task occupancy is derived separately
    # by MatrixState.agent_is_busy().
    status: str = "stopped"  # idle | running | error | stopped
    # Runtime provenance is intentionally ephemeral. It identifies the
    # Torque daemon revision that supplied this live agent session, rather
    # than inferring it later from a mutable checkout.
    session_code_revision: str = ""
    session_started_at: float = 0.0
    session_daemon_started_at: float = 0.0
    current_process: str = ""  # foreground job name (tracked for terminals)
    current_path: str = ""  # working directory (tracked for terminals)
    current_branch: str = ""  # git branch (empty if not in a repo)
    git_root: str = ""  # git repo root (empty if not in a repo)
    worktree_path: str = ""  # git worktree path (if created via group setting)
    worktree_branch: str = ""  # git worktree branch name
    worktree_repo_root: str = ""  # original repo root (needed for cleanup)
    worktree_base_dir: str = ".torque/worktrees"  # worktree dir relative to repo root
    worktree_base_branch: str = ""  # branch the worktree forked from (e.g. main)
    worktree_auto_checkpoint: bool = False  # auto-checkpoint on session end
    checkpoint_on_progress: bool = False  # auto-checkpoint on torque ai progress/done
    worktree_merge_squash: bool = True  # squash when merging back to base
    # Agent awareness (Phase 1)
    agent_type: str = ""  # "claude-code", "codex", "gemini-cli", ""
    agent_session_id: str = ""  # agent's own session ID (e.g. Claude Code session)
    activity: str = ""  # "", "thinking", "tool_call", "writing", "waiting"
    activity_detail: str = ""  # e.g. "Editing server.py", "Running tests"
    # Activity clocks:
    # - last_progress_at is a real work signal and drives task health.
    # - last_heartbeat_at is passive liveness from monitors/session pings.
    # - last_activity_at/last_event_at are compatibility aliases for the
    #   mixed "anything happened" clock.
    last_progress_at: float = 0.0
    last_heartbeat_at: float = 0.0
    last_activity_at: float = 0.0
    last_event_at: float = 0.0  # legacy mixed timestamp of last event received
    last_event_text: str = ""  # last meaningful event description
    session_tokens_in: int = 0  # cumulative input tokens this session
    session_tokens_out: int = 0  # cumulative output tokens this session
    context_window: dict = field(default_factory=dict)  # current context usage
    provider_usage: dict | None = None  # provider rate-limit usage telemetry
    error_message: str = ""  # last error, cleared on next successful event
    needs_attention: bool = False  # agent waiting for input or stuck
    last_summary: str = ""  # last assistant message on Stop (for checkpoint msgs)
    # Context preservation (dispatch history)
    tasks_dispatched: int = 0  # number of tasks sent to this agent (persisted)
    created_by_engineer_id: str = ""  # immutable Engineer provenance (persisted)
    kind: str = ""  # "" | architect | engineer | worker | terminal
    role: str = ""  # worker-role slug mirrored from template during migration
    owner_engineer_id: str = ""  # owning engineer for worker/terminal agents
    hired_by_architect_id: str = ""  # architect provenance for hires
    engineer_specializations: list[str] = field(default_factory=list)  # ordered, primary first
    # Agent Classes: desired assignment is separate from the frozen effective
    # class authority applied at launch. The class snapshot is the MCP
    # enforcement source for class-launched sessions.
    agent_class_id: str = ""
    agent_class_version: str = ""
    agent_class_assigned_at: float = 0.0
    agent_class_assigned_by: str = ""
    effective_agent_class_id: str = ""
    effective_agent_class_version: str = ""
    effective_agent_class_snapshot: dict = field(default_factory=dict)
    # Launch-frozen platform extensions derived from the trusted live registry;
    # intentionally ephemeral and never inferred from snapshot metadata.
    effective_agent_class_platform_authority: dict = field(default_factory=dict)
    effective_agent_class_applied_at: float = 0.0
    dismissed_at: int = 0  # unix timestamp when an architect/engineer is paused/dismissed
    deleted_at: float = 0.0  # unix timestamp when the cell entered the restore window
    permanent_delete_after: float = 0.0  # unix timestamp when tombstone is purgeable
    persistent: bool = False  # architect/engineer survive across sessions
    queue_empty_emitted: bool = True  # suppress duplicate engineer queue-empty events
    current_task_id: str = ""  # most recently dispatched task (ephemeral)
    session_resume: bool = True  # whether relaunch should resume the prior session
    idle_timeout: int = 0  # per-agent idle timeout in minutes (0=disable)
    # Worktree status (Phase 2, ephemeral)
    worktree_dirty: bool = False  # has uncommitted changes
    worktree_diff: dict = field(default_factory=dict)  # {files, insertions, deletions}
    worktree_changed_files: list[str] = field(default_factory=list)  # changed paths vs base
    worktree_checkpoints: int = 0  # number of checkpoint commits
    worktree_behind: int = 0  # commits on base not in branch (ephemeral)
    worktree_ahead: int = 0  # commits on branch not in base (ephemeral)
    worktree_merged: bool = False  # branch merged into base (ephemeral)
    # MCP message log (ephemeral)
    mcp_messages: list = field(default_factory=list)  # [{action, message, timestamp}]
    # Checkpoint throttle (ephemeral)
    last_checkpoint_at: float = 0.0  # timestamp of last progress checkpoint
    # Engineer message tracking (ephemeral)
    pending_engineer_message: bool = False  # agent has unread message from engineer

    def __post_init__(self):
        """Normalize legacy activity clocks loaded from older snapshots.

        Only infer progress from legacy mixed clocks when neither split clock
        exists. Once ``last_heartbeat_at`` is present, a zero
        ``last_progress_at`` is meaningful and must stay zero across reloads.
        """
        progress = _safe_float(self.last_progress_at)
        heartbeat = _safe_float(self.last_heartbeat_at)
        legacy = max(
            _safe_float(self.last_activity_at),
            _safe_float(self.last_event_at),
        )
        if not progress and not heartbeat and legacy:
            self.last_progress_at = legacy
            self.last_heartbeat_at = legacy
        elif progress and not heartbeat:
            self.last_heartbeat_at = progress
        self._sync_activity_alias()
        self.runner_backend = str(self.runner_backend or "pty").strip() or "pty"

    def _sync_activity_alias(self) -> bool:
        """Keep compatibility clocks equal to max(progress, heartbeat)."""
        mixed = max(
            _safe_float(self.last_progress_at),
            _safe_float(self.last_heartbeat_at),
        )
        changed = (
            _safe_float(self.last_activity_at) != mixed
            or _safe_float(self.last_event_at) != mixed
        )
        self.last_activity_at = mixed
        self.last_event_at = mixed
        return changed

    def mark_progress(self, timestamp: float | None = None, *,
                      heartbeat: bool = True) -> bool:
        """Record a real work signal. Progress also counts as liveness."""
        ts = _safe_float(timestamp if timestamp is not None else time.time())
        if ts <= 0:
            return False
        before = (
            _safe_float(self.last_progress_at),
            _safe_float(self.last_heartbeat_at),
            _safe_float(self.last_activity_at),
            _safe_float(self.last_event_at),
        )
        self.last_progress_at = max(_safe_float(self.last_progress_at), ts)
        if heartbeat:
            self.last_heartbeat_at = max(_safe_float(self.last_heartbeat_at), ts)
        self._sync_activity_alias()
        after = (
            _safe_float(self.last_progress_at),
            _safe_float(self.last_heartbeat_at),
            _safe_float(self.last_activity_at),
            _safe_float(self.last_event_at),
        )
        return after != before

    def mark_heartbeat(self, timestamp: float | None = None) -> bool:
        """Record a passive liveness signal without advancing progress."""
        ts = _safe_float(timestamp if timestamp is not None else time.time())
        if ts <= 0:
            return False
        before = (
            _safe_float(self.last_heartbeat_at),
            _safe_float(self.last_activity_at),
            _safe_float(self.last_event_at),
        )
        self.last_heartbeat_at = max(_safe_float(self.last_heartbeat_at), ts)
        self._sync_activity_alias()
        after = (
            _safe_float(self.last_heartbeat_at),
            _safe_float(self.last_activity_at),
            _safe_float(self.last_event_at),
        )
        return after != before

    @property
    def is_tombstoned(self) -> bool:
        """Whether this cell is inside the reversible deletion window."""
        return _safe_float(self.deleted_at) > 0


# Fields that are ephemeral (not meaningful across restarts)
_EPHEMERAL_FIELDS = ("current_process", "current_path",
                     "current_branch", "git_root",
                     "activity", "activity_detail",
                     "last_event_text",
                     "session_tokens_in", "session_tokens_out",
                     "context_window",
                     "provider_usage",
                     "error_message", "needs_attention", "last_summary",
                     "current_task_id",
                     "worktree_dirty", "worktree_diff",
                     "worktree_changed_files",
                     "worktree_checkpoints", "last_checkpoint_at",
                     "mcp_messages",
                     "pending_engineer_message")


def _slugify(name: str, max_len: int = 40) -> str:
    """Convert a name to a URL-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "unnamed"


def _unique_slug(base: str, existing: set) -> str:
    """Ensure slug is unique by appending a numeric suffix."""
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _peer_message_timestamp(row: dict) -> float:
    try:
        return float(row.get("created_at", row.get("timestamp", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _peer_message_action(row: dict) -> str:
    return (
        "architect_peer_reply"
        if str(row.get("reply_to_id", "") or "").strip()
        else "architect_peer_message"
    )


def _agent_peer_message_action(row: dict) -> str:
    """Return the legacy MCP/action label for one canonical peer row."""
    sender_kind = str((row or {}).get("sender_kind", "") or "").strip()
    recipient_kind = str((row or {}).get("recipient_kind", "") or "").strip()
    has_reply = bool(str((row or {}).get("reply_to_id", "") or "").strip())
    if sender_kind == "architect" and recipient_kind == "engineer":
        return "architect_reply" if has_reply else "architect_message"
    if sender_kind == "engineer" and recipient_kind == "architect":
        return "engineer_reply" if has_reply else "engineer_message_architect"
    if sender_kind == "engineer" and recipient_kind == "engineer":
        return "engineer_peer_reply" if has_reply else "engineer_peer_notify"
    return _peer_message_action(row or {})


def _is_agent_peer_thread_row(row: dict) -> bool:
    """True when a row belongs to the read-only V1 agent↔agent Chat panel."""
    if not isinstance(row, dict) or _is_user_direct_message_row(row):
        return False
    sender_kind = str(row.get("sender_kind", "") or "").strip()
    recipient_kind = str(row.get("recipient_kind", "") or "").strip()
    allowed = {"architect", "engineer"}
    return (
        sender_kind in allowed
        and recipient_kind in allowed
    )


def _agent_peer_thread_pair_ids(row: dict) -> tuple[str, str]:
    """Return the canonical unordered participant-pair for one chat row."""
    if not _is_agent_peer_thread_row(row or {}):
        return ("", "")
    ids: list[str] = []
    for field in ("sender", "recipient"):
        agent_id = str((row or {}).get(f"{field}_id", "") or "").strip()
        if agent_id and agent_id not in ids:
            ids.append(agent_id)
    if len(ids) != 2:
        return ("", "")
    ordered = sorted(ids)
    return (ordered[0], ordered[1])


def _agent_peer_thread_pair_key(row: dict) -> str:
    first, second = _agent_peer_thread_pair_ids(row or {})
    if not first or not second:
        return ""
    return f"agent-pair:{first}:{second}"


def _agent_peer_thread_message_entry(row: dict) -> dict:
    delivery_state = str(row.get("delivery_state", "") or "").strip()
    if not delivery_state:
        delivery_state = "buffered"
    return {
        "id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "action": _agent_peer_message_action(row),
        "message": str(row.get("message", "") or ""),
        "timestamp": _peer_message_timestamp(row),
        "group": str(row.get("group_name", row.get("group", "")) or ""),
        "sender_id": str(row.get("sender_id", "") or "").strip(),
        "sender_kind": str(row.get("sender_kind", "") or "").strip(),
        "sender_name": str(row.get("sender_name", "") or ""),
        "recipient_id": str(row.get("recipient_id", "") or "").strip(),
        "recipient_kind": str(row.get("recipient_kind", "") or "").strip(),
        "recipient_name": str(row.get("recipient_name", "") or ""),
        "ack_required": bool(row.get("ack_required", False)),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered_at": _safe_float(row.get("delivered_at", 0)),
        "context_task_ids": list(row.get("context_task_ids", []) or []),
        "context_engineer_ids": list(row.get("context_engineer_ids", []) or []),
        "context_decision_ids": list(row.get("context_decision_ids", []) or []),
        "context_summary": str(row.get("context_summary", "") or ""),
        "context_snapshot": dict(row.get("context_snapshot", {}) or {}),
    }


def _peer_message_cache_entry(row: dict, agent_id: str) -> dict | None:
    """Project a canonical peer-message row into AgentCell.mcp_messages."""
    if not isinstance(row, dict):
        return None
    if _is_user_direct_message_row(row):
        return None
    agent_id = str(agent_id or "").strip()
    sender_id = str(row.get("sender_id", "") or "").strip()
    recipient_id = str(row.get("recipient_id", "") or "").strip()
    if not agent_id or agent_id not in {sender_id, recipient_id}:
        return None
    sender_kind = str(row.get("sender_kind", "") or "").strip() or "architect"
    recipient_kind = (
        str(row.get("recipient_kind", "") or "").strip() or "architect"
    )
    direction = "sent" if agent_id == sender_id else "received"
    peer_id = recipient_id if direction == "sent" else sender_id
    peer_kind = recipient_kind if direction == "sent" else sender_kind
    delivery_state = str(row.get("delivery_state", "") or "").strip()
    if not delivery_state:
        delivery_state = "buffered"
    context = {
        "task_ids": list(row.get("context_task_ids", []) or []),
        "engineer_ids": list(row.get("context_engineer_ids", []) or []),
        "decision_ids": list(row.get("context_decision_ids", []) or []),
        "summary": str(row.get("context_summary", "") or ""),
        "snapshot": dict(row.get("context_snapshot", {}) or {}),
    }
    return {
        "id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "action": _agent_peer_message_action(row),
        "message": str(row.get("message", "") or ""),
        "timestamp": _peer_message_timestamp(row),
        "group": str(row.get("group_name", row.get("group", "")) or ""),
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "recipient_id": recipient_id,
        "recipient_kind": recipient_kind,
        "peer_id": peer_id,
        "peer_kind": peer_kind,
        "direction": direction,
        "ack_required": bool(row.get("ack_required", False)),
        "context_task_ids": context["task_ids"],
        "context_engineer_ids": context["engineer_ids"],
        "context_decision_ids": context["decision_ids"],
        "context_summary": context["summary"],
        "context_snapshot": context["snapshot"],
        "context": context,
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered": delivery_state == "delivered",
        "buffered": delivery_state == "buffered",
        "delivered_at": _safe_float(row.get("delivered_at", 0)),
        "archived_at": _safe_float(row.get("archived_at", 0)),
    }


def _is_user_direct_message_row(row: dict) -> bool:
    """Return whether a row belongs in the user↔agent DM projection.

    Blocking escalation rows may be persisted between an agent and its
    immediate owner. Their resolver-stamped participants, not their
    ``message_type`` or compatibility thread id, decide whether they belong
    in the user conversation.
    """
    sender_kind = str((row or {}).get("sender_kind", "") or "").strip()
    sender_id = str((row or {}).get("sender_id", "") or "").strip()
    recipient_kind = str((row or {}).get("recipient_kind", "") or "").strip()
    recipient_id = str((row or {}).get("recipient_id", "") or "").strip()
    involves_user = (
        (sender_kind == "user" and sender_id == "user")
        or (recipient_kind == "user" and recipient_id == "user")
    )
    message_type = str((row or {}).get("message_type", "message") or "message").strip()
    # A blocking raise and its answer are user-DM rows only when the resolved
    # recipient is actually the user. Other system/audit display rows keep
    # their established projection behavior.
    if message_type in {"ask", "ask_reply"}:
        return involves_user
    return involves_user or message_type != "message" or bool(row.get("blocking", False))


def _direct_message_cache_entry(row: dict, agent_id: str) -> dict | None:
    """Project a canonical direct-message row for the below-terminal cache."""
    if not isinstance(row, dict) or not _is_user_direct_message_row(row):
        return None
    agent_id = str(agent_id or "").strip()
    sender_id = str(row.get("sender_id", "") or "").strip()
    recipient_id = str(row.get("recipient_id", "") or "").strip()
    if not agent_id or agent_id not in {sender_id, recipient_id}:
        return None
    sender_kind = str(row.get("sender_kind", "") or "").strip()
    recipient_kind = str(row.get("recipient_kind", "") or "").strip()
    direction = "sent" if agent_id == sender_id else "received"
    peer_id = recipient_id if direction == "sent" else sender_id
    peer_kind = recipient_kind if direction == "sent" else sender_kind
    peer_name = (
        str(row.get("recipient_name", "") or "")
        if direction == "sent"
        else str(row.get("sender_name", "") or "")
    )
    delivery_state = str(row.get("delivery_state", "") or "").strip()
    if not delivery_state:
        delivery_state = "buffered"
    read_at = _safe_float(row.get("read_at", 0))
    return {
        "id": str(row.get("id", "") or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "message": str(row.get("message", "") or ""),
        "message_type": str(row.get("message_type", "message") or "message"),
        "timestamp": _peer_message_timestamp(row),
        "group": str(row.get("group_name", row.get("group", "")) or ""),
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "sender_name": str(row.get("sender_name", "") or ""),
        "recipient_id": recipient_id,
        "recipient_kind": recipient_kind,
        "recipient_name": str(row.get("recipient_name", "") or ""),
        "peer_id": peer_id,
        "peer_kind": peer_kind,
        "peer_name": peer_name,
        "direction": direction,
        "ack_required": bool(row.get("ack_required", False)),
        "blocking": bool(row.get("blocking", False)),
        "source_task_id": str(row.get("source_task_id", "") or ""),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered": delivery_state == "delivered",
        "buffered": delivery_state == "buffered",
        "delivered_at": _safe_float(row.get("delivered_at", 0)),
        "read_at": read_at,
        "unread": read_at <= 0 and direction == "received",
        "archived_at": _safe_float(row.get("archived_at", 0)),
    }


def _is_peer_message_cache_entry(entry: dict) -> bool:
    return str((entry or {}).get("action", "") or "").strip() in {
        "architect_message",
        "architect_reply",
        "engineer_message_architect",
        "engineer_reply",
        "architect_peer_message",
        "architect_peer_reply",
        "engineer_peer_notify",
        "engineer_peer_reply",
    }


def _sort_direct_message_cache(entries: list[dict]) -> list[dict]:
    return sorted(
        entries,
        key=lambda item: (
            _safe_float((item or {}).get("timestamp", 0)),
            str((item or {}).get("id", "") or ""),
        ),
    )


def _sort_mcp_message_cache(entries: list[dict]) -> list[dict]:
    return sorted(
        entries,
        key=lambda item: (
            _safe_float((item or {}).get("timestamp", 0)),
            str((item or {}).get("id", "") or ""),
        ),
        reverse=True,
    )


@dataclass
class CriticalWriteCapture:
    command_name: str
    idempotency_key: str
    request_hash: str
    agents: dict[str, object] = field(default_factory=dict)
    deleted_agents: set[str] = field(default_factory=set)
    tasks: dict[str, object] = field(default_factory=dict)
    deleted_tasks: set[str] = field(default_factory=set)
    task_id_counters: dict[str, int] = field(default_factory=dict)
    pipeline_task_counters: dict[str, int] = field(default_factory=dict)
    task_id_aliases: dict[str, str] = field(default_factory=dict)


def _normalize_board_lanes(lanes) -> list[str]:
    current = []
    seen = set()
    for lane in lanes or []:
        if not lane or lane in seen:
            continue
        current.append(lane)
        seen.add(lane)
    for lane in _RESERVED_LANES:
        if lane not in seen:
            current.append(lane)
            seen.add(lane)
    return current


def _normalize_verification_snapshot_summary(summary) -> dict:
    if not isinstance(summary, dict):
        return {}
    out = {}
    text_keys = (
        "tests_run",
        "human_validation_pending",
        "isolated_rerun_evidence",
        "tested_sha",
    )
    for key in text_keys:
        value = summary.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value:
            out[key] = value

    test_outcome = str(summary.get("test_outcome", "") or "").strip()
    if test_outcome in _VERIFICATION_TEST_OUTCOMES and test_outcome:
        out["test_outcome"] = test_outcome

    reviewer_acceptance = str(
        summary.get("reviewer_acceptance", "") or ""
    ).strip()
    if (
        reviewer_acceptance in _VERIFICATION_REVIEWER_ACCEPTANCES
        and reviewer_acceptance
    ):
        out["reviewer_acceptance"] = reviewer_acceptance

    for key in (
        "manual_smoke_done",
        "deploy_needed",
        "deploy_attempted",
        "full_suite_attempted",
        "unrelated_flake_accepted",
        "live_smoke_pending",
    ):
        if key in summary:
            out[key] = bool(summary.get(key))
    return out


def _normalize_verification_run(run) -> dict:
    if not isinstance(run, dict):
        return {}
    report = run.get("report", {})
    if not isinstance(report, dict):
        report = {}
    normalized_report = {}
    mode = str(report.get("mode", "") or "").strip()
    if mode in _VERIFICATION_MODES and mode:
        normalized_report["mode"] = mode
    state = str(report.get("state", "") or "").strip()
    if state in _VERIFICATION_STATES and state:
        normalized_report["state"] = state
    notes = str(report.get("notes", "") or "").strip()
    if notes:
        normalized_report["notes"] = notes
    summary = _normalize_verification_snapshot_summary(
        report.get("summary", {})
    )
    if summary:
        normalized_report["summary"] = summary

    normalized = {}
    recorded_at = str(run.get("recorded_at", "") or "").strip()
    recorded_by = str(run.get("recorded_by", "") or "").strip()
    if recorded_at:
        normalized["recorded_at"] = recorded_at
    if recorded_by:
        normalized["recorded_by"] = recorded_by
    if normalized_report:
        normalized["report"] = normalized_report
    return normalized if normalized_report else {}


def _normalize_verification_summary(summary) -> dict:
    out = _normalize_verification_snapshot_summary(summary)
    if not isinstance(summary, dict):
        return out
    runs = []
    for run in summary.get("runs", []) or []:
        normalized = _normalize_verification_run(run)
        if normalized:
            runs.append(normalized)
    if runs:
        out["runs"] = runs
    return out


def _normalize_completion_evidence(evidence) -> dict:
    if not isinstance(evidence, dict):
        return {}
    source = _json_safe_copy(evidence, {})
    if not isinstance(source, dict):
        return {}
    status = str(source.get("status", "") or "").strip()
    if status and status not in {"verified", "evidence_attached"}:
        source.pop("status", None)
    sources = source.get("sources")
    if isinstance(sources, list):
        normalized_sources = []
        seen = set()
        for item in sources:
            value = str(item or "").strip()
            if value and value not in seen:
                normalized_sources.append(value)
                seen.add(value)
        source["sources"] = normalized_sources
    elif "sources" in source:
        source.pop("sources", None)
    if "verified" in source:
        source["verified"] = bool(source.get("verified"))
    return source


def _append_unique_string(values, value: str) -> list[str]:
    out: list[str] = []
    seen = set()
    for item in list(values or []):
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    text = str(value or "").strip()
    if text and text not in seen:
        out.append(text)
    return out


def _coverage_card_message(covered_by: dict) -> str:
    parts = []
    task_id = str(covered_by.get("task_id", "") or "").strip()
    if task_id:
        parts.append(f"task {task_id}")
    pr_url = str(covered_by.get("pr_url", "") or "").strip()
    if pr_url:
        parts.append(f"PR {pr_url}")
    sha = str(covered_by.get("sha", "") or "").strip()
    if sha:
        parts.append(f"SHA {sha}")
    if not parts:
        parts.append("provided evidence")
    message = "Marked covered by " + " · ".join(parts)
    tests_run = str(covered_by.get("tests_run", "") or "").strip()
    if tests_run:
        message += f" · Tests: {tests_run}"
    evidence = str(covered_by.get("evidence", "") or "").strip()
    if evidence:
        message += f" · Evidence: {evidence}"
    notes = str(covered_by.get("notes", "") or "").strip()
    if notes:
        message += f" · Notes: {notes}"
    return message


def _json_safe_copy(value, default):
    """Return a JSON-compatible deep copy, falling back to ``default``."""
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return copy.deepcopy(default)


def _normalize_board_sync_provider(provider) -> str:
    value = str(provider or "none").strip().lower()
    return value if value in _BOARD_SYNC_PROVIDERS else "none"


def _normalize_board_sync_github_settings(settings) -> dict:
    """Normalize nested GitHub board-sync group settings.

    The field is intentionally sparse: missing keys mean "use provider
    defaults" so default group settings do not grow noisy non-empty payloads.
    """
    source = _json_safe_copy(settings, {})
    if not isinstance(source, dict):
        return {}
    out: dict = {}
    text_keys = (
        "github_repo",
        "github_project_owner",
        "github_project_id",
        "github_project_status_field",
        "github_sync_default",
    )
    for key in text_keys:
        if key not in source:
            continue
        value = source.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value:
            out[key] = value
    if "github_project_number" in source:
        try:
            out["github_project_number"] = max(
                0,
                int(source.get("github_project_number") or 0),
            )
        except (TypeError, ValueError):
            out["github_project_number"] = 0
    for key in ("github_lane_status_map", "github_assignee_map"):
        if key in source:
            value = source.get(key)
            out[key] = value if isinstance(value, dict) else {}
    for key in ("github_close_issues_via_pr",
                "github_create_missing_labels"):
        if key in source:
            out[key] = bool(source.get(key))
    return out


def _normalize_board_sync(sync) -> dict:
    """Normalize task-scoped board-sync metadata.

    The column stores common sync state at the top level and provider-specific
    data under a provider key (``github`` for V1). Unknown JSON-compatible
    keys are preserved so future adapters can extend the payload without a
    migration.
    """
    source = _json_safe_copy(sync, {})
    if not isinstance(source, dict):
        return {}
    provider = str(source.get("provider", "") or "").strip().lower()
    if provider:
        source["provider"] = provider
        if provider in source and not isinstance(source.get(provider), dict):
            source.pop(provider, None)
    elif "provider" in source:
        source.pop("provider", None)
    if "version" in source:
        try:
            source["version"] = max(1, int(source.get("version") or 1))
        except (TypeError, ValueError):
            source.pop("version", None)
    if "enabled" in source:
        source["enabled"] = bool(source.get("enabled"))
    for key in (
            "last_push_at",
            "last_pull_at",
            "last_seen_provider_updated_at",
            "last_synced_hash",
            "last_error"):
        if key in source:
            value = source.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                value = str(value)
            source[key] = value.strip()
    if "sync_state" in source:
        state = str(source.get("sync_state", "") or "").strip().lower()
        if state in _BOARD_SYNC_STATES:
            source["sync_state"] = state
        else:
            source.pop("sync_state", None)
    return source


def board_task_is_archived(task: Optional[BoardTask]) -> bool:
    return bool(task and task.lane == ARCHIVED_LANE)


def board_task_is_closed(task: Optional[BoardTask]) -> bool:
    return bool(task and task.lane in {"Done", ARCHIVED_LANE})


def board_task_counts_as_done(task: Optional[BoardTask]) -> bool:
    if not task:
        return False
    if task.lane == "Done":
        return True
    return task.lane == ARCHIVED_LANE and task.archived_from_lane == "Done"


def board_task_compact(task: BoardTask) -> dict:
    """Return the compact board-card summary used by compact snapshots."""
    summary = {}
    for field_name in COMPACT_BOARD_TASK_FIELDS:
        if field_name == "messages":
            value = _compact_task_messages_preview(getattr(task, field_name))
        elif field_name == "messages_thread_summary":
            value = _compact_messages_thread_summary(task.messages_thread)
        elif field_name == "board_sync":
            value = _compact_board_sync_summary(task.board_sync)
        elif field_name == "health_details":
            value = _compact_health_details_summary(task.health_details)
        elif field_name == "worktree_boundary":
            value = _compact_worktree_boundary_summary(task.worktree_boundary)
        else:
            value = getattr(task, field_name)
        if isinstance(value, dict):
            value = dict(value)
        elif isinstance(value, list):
            value = list(value)
        summary[field_name] = value
    return summary


def agent_client_dict(agent: AgentCell) -> dict:
    """Return the bounded AgentCell projection sent to browser clients.

    Runtime authorization keeps using the complete in-memory AgentCell.  The
    browser only needs Agent Class identity/lifecycle metadata, its 20 visible
    MCP entries, and a bounded changed-file preview.
    """
    deferred_fields = {
        "effective_agent_class_snapshot",
        "mcp_messages",
        "worktree_changed_files",
    }
    payload = {
        item.name: copy.deepcopy(getattr(agent, item.name))
        for item in fields(agent)
        if item.name not in deferred_fields
    }
    class_snapshot = getattr(agent, "effective_agent_class_snapshot", None)
    if isinstance(class_snapshot, dict):
        class_summary = {
            key: copy.deepcopy(class_snapshot[key])
            for key in COMPACT_AGENT_CLASS_FIELDS
            if key in class_snapshot
        }
        metadata = class_snapshot.get("metadata")
        if isinstance(metadata, dict):
            metadata_summary = {
                key: copy.deepcopy(metadata[key])
                for key in ("archived", "disabled", "archived_at")
                if key in metadata
            }
            if metadata_summary:
                class_summary["metadata"] = metadata_summary
        payload["effective_agent_class_snapshot"] = class_summary
    else:
        payload["effective_agent_class_snapshot"] = {}

    mcp_messages = getattr(agent, "mcp_messages", None)
    if isinstance(mcp_messages, list):
        payload["mcp_messages"] = copy.deepcopy(
            mcp_messages[:COMPACT_AGENT_MCP_MESSAGE_LIMIT]
        )
        if len(mcp_messages) > COMPACT_AGENT_MCP_MESSAGE_LIMIT:
            payload["mcp_message_count"] = len(mcp_messages)
    else:
        payload["mcp_messages"] = []

    changed_files = getattr(agent, "worktree_changed_files", None)
    if isinstance(changed_files, list):
        payload["worktree_changed_files"] = copy.deepcopy(
            changed_files[:COMPACT_AGENT_CHANGED_FILE_LIMIT]
        )
        if len(changed_files) > COMPACT_AGENT_CHANGED_FILE_LIMIT:
            payload["worktree_changed_files_count"] = len(changed_files)
    else:
        payload["worktree_changed_files"] = []
    return payload


def _compact_task_messages_preview(messages) -> list[dict]:
    entries = list(messages or [])
    if not entries:
        return []
    last = entries[-1] if isinstance(entries[-1], dict) else {}
    action = str(last.get("action", "") or "").strip()
    count = len(entries)
    if count == 1:
        message = action or "1 update"
    elif action:
        message = f"{count} updates · last {action}"
    else:
        message = f"{count} updates"
    preview = {
        "count": count,
        "message": message,
    }
    if action:
        preview["action"] = action
    return [preview]


def _compact_messages_thread_summary(messages) -> dict:
    """Return a count/recipient summary for lazy inline-message hydration."""
    entries = [entry for entry in list(messages or []) if isinstance(entry, dict)]
    if not entries:
        return {}
    recipients: list[str] = []
    senders: list[str] = []
    reply_required = False
    last_ts = 0.0
    for entry in entries:
        recipient = str(entry.get("recipient_agent_id", "") or "").strip()
        sender = str(entry.get("sender_agent_id", "") or "").strip()
        if recipient and recipient not in recipients:
            recipients.append(recipient)
        if sender and sender not in senders:
            senders.append(sender)
        reply_required = reply_required or bool(entry.get("reply_required"))
        try:
            last_ts = max(last_ts, float(entry.get("timestamp", 0) or 0))
        except (TypeError, ValueError):
            pass
    summary = {
        "count": len(entries),
        "recipient_agent_ids": recipients[:8],
        "sender_agent_ids": senders[:8],
        "reply_required": reply_required,
    }
    if last_ts > 0:
        summary["last_timestamp"] = last_ts
    return summary


def _compact_board_sync_summary(sync) -> dict:
    """Return card-level board-sync state without provider history/details."""
    if not isinstance(sync, dict):
        return {}
    out: dict = {}
    for key in ("version", "provider", "enabled", "sync_state", "skipped"):
        if key in sync:
            out[key] = sync.get(key)
    last_error = str(sync.get("last_error", "") or "").strip()
    if last_error:
        out["last_error"] = last_error[:500]
    return out


def _compact_health_details_summary(details) -> dict:
    """Return fields needed by board/engineer summaries from health_details."""
    if not isinstance(details, dict):
        return {}
    out: dict = {}
    for key in (
            "aggregate",
            "source_task_id",
            "source_task_title",
            "last_activity_at",
            "silence_secs"):
        if key in details:
            out[key] = details.get(key)
    reasons = details.get("reasons")
    if isinstance(reasons, list) and reasons:
        out["reasons"] = [str(item) for item in reasons[:4]]
    reason = str(details.get("reason", "") or "").strip()
    if reason:
        out["reason"] = reason[:160]
    return out


def _compact_worktree_boundary_summary(boundary) -> dict:
    """Return branch/PR summary required for card and branch-overview UI."""
    if not isinstance(boundary, dict):
        return {}
    out: dict = {}
    for key in (
            "repo_root",
            "branch",
            "base_branch",
            "status",
            "recorded_at",
            "merged_at",
            "merge_commit_sha"):
        value = boundary.get(key, "")
        if value not in ("", None):
            out[key] = value
    pr = boundary.get("pr")
    if not isinstance(pr, dict):
        pr = boundary.get("pull_request")
    if isinstance(pr, dict):
        pr_out: dict = {}
        for key in ("url", "number", "state", "status", "merge_state", "head_sha"):
            value = pr.get(key, "")
            if value not in ("", None):
                pr_out[key] = value
        if pr_out:
            out["pr"] = pr_out
    for key in (
            "pr_url",
            "pr_number",
            "pr_state",
            "pr_status",
            "pr_merge_state",
            "pr_head_sha",
            "pr_pending"):
        value = boundary.get(key, "")
        if value not in ("", None):
            out[key] = value
    return out


def _normalize_messages_thread(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []
    out: list[dict] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        out.append({
            "timestamp": _safe_float(entry.get("timestamp")),
            "sender_agent_id": str(
                entry.get("sender_agent_id", "") or ""
            ).strip(),
            "recipient_agent_id": str(
                entry.get("recipient_agent_id", "") or ""
            ).strip(),
            "content": content,
            "reply_required": bool(entry.get("reply_required", False)),
        })
    out.sort(key=lambda item: _safe_float(item.get("timestamp")))
    return out


def _normalize_task_dispatch_state(value) -> str:
    state = str(value or "").strip().lower()
    if state in TASK_DISPATCH_STATES:
        return state
    return TASK_DISPATCH_STATE_QUEUED


def task_is_closed(task: Optional[BoardTask]) -> bool:
    return board_task_is_closed(task)


def task_counts_as_done(task: Optional[BoardTask]) -> bool:
    return board_task_counts_as_done(task)


def task_is_engineer_message_followup(task: Optional[BoardTask]) -> bool:
    if not task:
        return False
    labels = set(getattr(task, "labels", []) or [])
    return "torque:engineer-message" in labels


def task_suppresses_done_cascade(task: Optional[BoardTask]) -> bool:
    if not task:
        return False
    labels = set(task.labels or [])
    return "torque:human" in labels or task_is_engineer_message_followup(task)


def _normalize_verification_fields(fields: dict) -> None:
    if "verification_mode" in fields:
        mode = fields.get("verification_mode", "") or ""
        fields["verification_mode"] = mode if mode in _VERIFICATION_MODES else ""
    if "verification_state" in fields:
        state = fields.get("verification_state", "") or ""
        fields["verification_state"] = (
            state if state in _VERIFICATION_STATES else ""
        )
    for key in ("verification_notes", "verification_updated_at",
                "verification_updated_by"):
        if key in fields:
            value = fields.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                value = str(value)
            fields[key] = value.strip()
    if "verification_summary" in fields:
        fields["verification_summary"] = _normalize_verification_summary(
            fields.get("verification_summary")
        )
    if "completion_evidence" in fields:
        fields["completion_evidence"] = _normalize_completion_evidence(
            fields.get("completion_evidence")
        )
    # Finalization policy is explicit structured state; malformed state remains
    # visible as an empty/corrupt contract and fails closed in the evaluator.
    if "finalization_mode" in fields:
        from .finalization import normalize_mode
        fields["finalization_mode"] = normalize_mode(fields.get("finalization_mode"))
    if "required_review_gates" in fields:
        from .finalization import normalize_required_review_gates
        fields["required_review_gates"] = normalize_required_review_gates(fields.get("required_review_gates"))
    if "finalization_boundary" in fields:
        from .finalization import normalize_boundary
        fields["finalization_boundary"] = normalize_boundary(fields.get("finalization_boundary"), fields.get("finalization_mode", "legacy"))
    if "finalization_audit" in fields:
        from .finalization import normalize_audit
        fields["finalization_audit"] = normalize_audit(fields.get("finalization_audit"))
    if "finalization_status" in fields and not isinstance(fields.get("finalization_status"), dict):
        fields["finalization_status"] = {}


def _normalize_worktree_boundary(boundary) -> dict:
    if not isinstance(boundary, dict):
        return {}
    out = {}
    text_keys = (
        "version",
        "branch",
        "repo_root",
        "base_branch",
        "commit_sha",
        "kind",
        "status",
        "recorded_at",
        "recorded_by_agent_id",
        "message",
        "superseded_by_task_id",
        "merged_at",
        "merge_commit_sha",
        "reason",
    )
    for key in text_keys:
        value = boundary.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value:
            out[key] = value
    pr = boundary.get("pr")
    if not isinstance(pr, dict):
        pr = boundary.get("pull_request")
    if isinstance(pr, dict):
        normalized_pr = {}
        pr_text_keys = (
            "provider",
            "remote",
            "base_branch",
            "head_branch",
            "head_sha",
            "url",
            "state",
            "merge_state",
            "created_at",
            "updated_at",
            "merged_at",
            "merge_commit_sha",
        )
        for key in pr_text_keys:
            value = pr.get(key, "")
            if key == "head_branch" and not value:
                value = pr.get("branch", "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                value = str(value)
            value = value.strip()
            if value:
                normalized_pr[key] = value
        if pr.get("number") not in {"", None}:
            try:
                normalized_pr["number"] = int(pr.get("number"))
            except (TypeError, ValueError):
                normalized_pr["number"] = pr.get("number")
        requested_cleanup = pr.get("requested_cleanup", {})
        if isinstance(requested_cleanup, dict):
            cleanup = {}
            for key in (
                    "close_agent_on_merge",
                    "remove_worktree_on_merge",
                    "auto_move_to_done",
                    "preserve_merge_diff"):
                if key in requested_cleanup:
                    cleanup[key] = bool(requested_cleanup[key])
            if cleanup:
                normalized_pr["requested_cleanup"] = cleanup
        if normalized_pr:
            out["pr"] = normalized_pr
    # A boundary classifier is durable, machine-computed evidence. Preserve its
    # compact diagnostic fields; malformed/missing facts intentionally remain
    # unclassified and fail closed while a boundary is still open.
    code_delta = boundary.get("code_delta")
    if isinstance(code_delta, dict):
        normalized_delta = {}
        for key in (
                "state", "base_sha", "commit_sha", "comparison", "reason",
                "classified_at"):
            value = code_delta.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                value = str(value)
            if value.strip():
                normalized_delta[key] = value.strip()
        path_count = code_delta.get("path_count")
        if isinstance(path_count, int) and path_count >= 0:
            normalized_delta["path_count"] = path_count
        if normalized_delta:
            out["code_delta"] = normalized_delta
    return out


@dataclass
class GroupSettings:
    """Default settings applied when creating agents/terminals in a group."""
    # Group-level defaults
    default_directory: str = ""
    default_terminal_backend: str = "pty"
    profile: str = ""
    shell: str = ""
    tab_color: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    env_file: str = ""
    auto_terminals: int = 0
    max_agents: int = 0
    collapsed_default: bool = False
    filter_by_window: bool = False
    # Agent overrides
    agent_directory: str = ""
    agent_terminal_profile: str = ""
    agent_shell: str = ""
    agent_tab_color: str = ""
    agent_env_vars: dict[str, str] = field(default_factory=dict)
    agent_env_file: str = ""
    default_agent_template: str = ""
    agent_provider: str = ""  # adapter name ("claude-code", "codex", etc.) — empty = use default
    agent_boot_command: str = ""  # override default boot command (e.g. "codex")
    agent_model: str = ""  # default model override when provider supports it
    agent_reasoning_effort: str = ""  # default reasoning-effort override
    agent_fast_mode: str = "inherit"  # Codex Fast: inherit | on | off
    # Worker launch overrides. Empty strings inherit the agent_* group default.
    worker_provider: str = ""  # adapter override for workers (empty = use group default)
    worker_boot_command: str = ""  # boot command override for workers (empty = use group default)
    worker_model: str = ""  # model override for workers (empty = use group default)
    worker_reasoning_effort: str = ""  # reasoning override for workers (empty = use group default)
    worker_fast_mode: str = "inherit"  # Codex Fast: inherit | on | off
    git_worktree: bool = False
    worktree_base_dir: str = ".torque/worktrees"  # directory for worktrees (relative to repo)
    worktree_base_branch: str = ""  # branch to fork from (empty = current HEAD)
    worktree_auto_checkpoint: bool = True  # auto-checkpoint on agent stop
    checkpoint_on_progress: bool = False  # auto-checkpoint on torque ai progress/done
    worktree_merge_squash: bool = False  # squash commits when merging to main
    worktree_merge_instructions: str = ""  # additional instructions appended to merge prompt
    worktree_merge_cleanup: str = "keep"  # keep | close | remove | close_remove | auto_sweep
    worktree_merge_preserve_diff: bool = False  # save the pre-merge patch on the latest boundary task
    engineer_merge_mode: str = "pr"  # pr | direct | engineer-choice
    worktree_symlinks: list[str] = field(default_factory=list)  # repo-relative paths or glob patterns to symlink from repo root
    worktree_submodules: list[str] = field(default_factory=list)  # repo-relative submodule paths to materialize as nested linked worktrees
    worktree_symlink_gitignored_paths: bool = False  # symlink gitignored files/dirs from repo root into worktrees
    agent_session_resume: bool = True  # resume session on relaunch
    agent_idle_timeout: int = 0  # minutes before flagging agent as stuck (0=disable)
    guidance_hint_cadence: int = _DEFAULT_GUIDANCE_HINT_CADENCE  # 0=every time; otherwise 1st, then every N
    context_default_ttl_days: int = 30  # Shared Context entry lifetime, clamped to 1..60.
    engineer_hint_snoozes: dict[str, float] = field(default_factory=dict)  # hint fingerprint -> unix expiry
    # Agent notifications
    notifications: bool = False
    notify_on_finish: bool = True
    notify_on_error: bool = True
    notify_on_attention: bool = True
    # Terminal overrides
    terminal_name_prefix: str = ""
    terminal_boot_command: str = ""
    terminal_command_args: str = ""
    terminal_init_script: str = ""
    terminal_directory: str = ""
    terminal_profile: str = ""
    terminal_shell: str = ""
    terminal_tab_color: str = ""
    terminal_env_vars: dict[str, str] = field(default_factory=dict)
    terminal_env_file: str = ""
    terminal_always_custom_dialog: bool = False
    terminal_close_on_disconnect: bool = False  # remove terminal from Torque when tab closed
    # Board / Dispatch
    dispatch_lane: str = "In Progress"  # lane for dispatched tasks
    board_default_labels: list[str] = field(default_factory=list)  # default labels for new tasks
    board_default_lane: str = ""  # default lane for new tasks (empty = first lane)
    board_default_action: str = ""  # default action for new tasks
    board_sync_provider: str = "none"  # none | github (future providers reserved)
    board_sync_enabled: bool = False
    board_sync_github: dict = field(default_factory=dict)  # GitHub adapter settings
    # Engineer
    engineer_agent_id: str = ""  # designated engineer agent for this group
    default_engineer_specializations: list[str] = field(default_factory=list)  # ordered, applied at engineer creation
    # Architect settings (persisted in group_settings)
    architect_boot_command: str = ""
    architect_provider: str = ""
    architect_model: str = ""
    architect_reasoning_effort: str = ""
    architect_fast_mode: str = "inherit"  # Codex Fast: inherit | on | off
    architect_directory: str = ""
    architect_profile: str = ""
    architect_shell: str = ""
    architect_tab_color: str = ""
    architect_custom_instructions: str = ""
    architect_autonomy_mode: str = _DEFAULT_ARCHITECT_AUTONOMY_MODE
    architect_digest_verbosity: str = _DEFAULT_ARCHITECT_DIGEST_VERBOSITY
    architect_push_interval: int = _DEFAULT_ARCHITECT_PUSH_INTERVAL
    architect_max_interval: int = _DEFAULT_ARCHITECT_MAX_INTERVAL
    architect_heartbeat_interval: int = _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL
    architect_suppress_empty_digests: bool = (
        _DEFAULT_ARCHITECT_SUPPRESS_EMPTY_DIGESTS
    )
    architect_enabled_events: list[str] = field(
        default_factory=lambda: list(_ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS)
    )
    architect_journal_checkpoint_frequency: str = (
        _DEFAULT_ARCHITECT_JOURNAL_CHECKPOINT_FREQUENCY
    )
    architect_review_gate_thresholds: dict = field(
        default_factory=lambda: dict(_DEFAULT_ARCHITECT_REVIEW_GATE_THRESHOLDS)
    )
    engineer_behavior_requires_user_approval: bool = False


@dataclass
class ArchitectSettings:
    """Per-group architect configuration."""
    group: str = ""
    architect_boot_command: str = ""
    architect_provider: str = ""
    architect_model: str = ""
    architect_reasoning_effort: str = ""
    architect_fast_mode: str = "inherit"  # Codex Fast: inherit | on | off
    architect_directory: str = ""
    architect_profile: str = ""
    architect_shell: str = ""
    architect_tab_color: str = ""
    architect_custom_instructions: str = ""
    architect_autonomy_mode: str = _DEFAULT_ARCHITECT_AUTONOMY_MODE
    architect_digest_verbosity: str = _DEFAULT_ARCHITECT_DIGEST_VERBOSITY
    architect_push_interval: int = _DEFAULT_ARCHITECT_PUSH_INTERVAL
    architect_max_interval: int = _DEFAULT_ARCHITECT_MAX_INTERVAL
    architect_heartbeat_interval: int = _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL
    architect_suppress_empty_digests: bool = (
        _DEFAULT_ARCHITECT_SUPPRESS_EMPTY_DIGESTS
    )
    architect_enabled_events: list[str] = field(
        default_factory=lambda: list(_ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS)
    )
    architect_journal_checkpoint_frequency: str = (
        _DEFAULT_ARCHITECT_JOURNAL_CHECKPOINT_FREQUENCY
    )
    architect_review_gate_thresholds: dict = field(
        default_factory=lambda: dict(_DEFAULT_ARCHITECT_REVIEW_GATE_THRESHOLDS)
    )


@dataclass
class EngineerSettings:
    """Per-group engineer configuration."""
    group: str = ""
    push_interval: int = 60              # seconds between digest pushes (min: 10)
    max_interval: int = 300              # max seconds between normal digest pushes
    heartbeat_interval: int = 300        # quiet seconds before idle heartbeat digest (0 = off)
    default_worker_concurrency: int = 2  # default max_concurrent for dispatch waves
    autonomy_mode: str = "dispatch_when_clear"  # suggest_only | dispatch_when_clear | aggressive_auto_continue
    wave_size_preference: str = "small"  # small | balanced | large
    same_agent_follow_up_preference: str = "balanced"  # balanced | prefer_same_agent | prefer_fresh_agent
    digest_verbosity: str = "balanced"   # compact | balanced | detailed
    escalation_style: str = "note_then_ask"  # ask_early | note_then_ask | keep_moving
    paused: bool = False                 # user paused event pushes
    custom_instructions: str = ""        # user-defined instructions appended to engineer system prompt
    restrict_to_created_agents: bool = False  # limit Engineer agent visibility/control to its own created agents
    engineer_can_override_worker_provider: bool = True  # expose worker provider override in Engineer dispatch tools
    pending_question: str = ""           # question awaiting human reply (non-empty = awaiting input)
    pending_question_set_at: float = 0.0  # unix timestamp when pending_question was set
    pending_question_actor_id: str = ""  # engineer who set pending_question
    pending_note: str = ""               # non-blocking note/question for the human
    pending_note_kind: str = ""          # "note" | "question" | ""
    pending_note_set_at: float = 0.0     # unix timestamp when pending_note was set
    pending_note_actor_id: str = ""      # engineer who set pending_note
    engineer_provider: str = ""            # adapter name override (empty = use group default)
    engineer_boot_command: str = ""        # boot command override (empty = use provider default)
    engineer_model: str = ""               # model override for the designated engineer
    engineer_reasoning_effort: str = ""    # reasoning-effort override for the designated engineer
    engineer_fast_mode: str = "inherit"  # Codex Fast: inherit | on | off
    engineer_directory: str = ""           # directory override for the designated engineer
    engineer_profile: str = ""             # iTerm profile override for the designated engineer
    engineer_shell: str = ""               # shell override for the designated engineer
    engineer_tab_color: str = ""           # tab color override for the designated engineer
    enabled_events: list[str] = field(   # optional events (mandatory always on)
        default_factory=lambda: list(
            _ENGINEER_NOTIFICATION_PRESETS["normal"]["enabled_events"]
        )
    )


@dataclass
class AgentDigestSettings:
    """Per-agent digest delivery settings."""
    agent_id: str = ""
    paused: bool = False
    push_interval: int = 60
    max_interval: int = 300
    heartbeat_interval: int = 300
    digest_verbosity: str = "balanced"
    enabled_events: list[str] = field(
        default_factory=lambda: list(
            _ENGINEER_NOTIFICATION_PRESETS["normal"]["enabled_events"]
        )
    )
    architect_digest: bool = False
    wake_on_digest: bool = False
    suppress_empty: bool = False
    # Names of delivery fields explicitly overridden for this agent.  A row
    # can exist for bookkeeping/defaults without turning every stored value
    # into an override.
    override_fields: list[str] = field(default_factory=list)


@dataclass
class AgentSettings:
    """Nullable settings overrides for one Architect or Engineer."""
    agent_id: str = ""
    provider: Optional[str] = None
    boot_command: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    fast_mode: Optional[str] = None
    autonomy_mode: Optional[str] = None
    custom_instructions: Optional[str] = None
    default_worker_concurrency: Optional[int] = None
    wave_size_preference: Optional[str] = None
    same_agent_follow_up_preference: Optional[str] = None
    escalation_style: Optional[str] = None
    engineer_can_override_worker_provider: Optional[bool] = None
    restrict_to_created_agents: Optional[bool] = None


# Mandatory events — always included in engineer digests regardless of enabled_events.
ENGINEER_MANDATORY_EVENTS = frozenset({
    "task_completed", "agent_reply", "agent_error",
    "agent_blocked", "ask_created", "task_verification_updated",
    "worker_boot_doa", "perceived_empty_episode",
})

# Mandatory architect digest floor — a per-architect filter can never suppress
# these can't-miss safety/decision events. Keep this separate from engineer
# mandatory events so routine worker churn can stay quiet for architects.
ARCHITECT_MANDATORY_EVENTS = frozenset({
    "ask_created",
    ENGINEER_AWAITING_HUMAN_INPUT,
    "agent_error",
    "agent_blocked",
    "task_blocked",
})


@dataclass
class GlobalSettings:
    """App-wide settings for the Torque instance."""
    # General > Server
    default_command: str = ""        # empty = use config.DEFAULT_COMMAND (env var fallback)
    filter_by_window: bool = True    # global default for window filtering
    focus_new_tabs: bool = True      # switch focus to newly created tabs
    focus_on_click: bool = False     # single click also focuses the terminal
    xterm_scrollback: int = XTERM_SCROLLBACK_DEFAULT  # embedded xterm.js history lines
    # General > Board
    default_lanes: list[str] = field(default_factory=lambda: list(_DEFAULT_LANES))
    # Keybindings — action name → {key, ctrl, meta, alt, shift} overrides
    keybindings: dict[str, dict] = field(default_factory=dict)
    # Pipeline
    max_pipeline_depth: int = 10  # 0 = unlimited
    # Events
    max_event_log: int = 500  # max persisted panel events
    metrics_enabled: bool = True  # enable low-overhead daemon metrics ticks/history
    event_ingest_max_rows: int = 100_000
    event_ingest_max_days: int = 14
    mcp_call_log_args_capture: str = "metadata"  # off | metadata | full
    mcp_call_log_full_capture_tools: list[str] = field(default_factory=list)
    perceived_empty_probe_threshold: int = 5
    perceived_empty_window_seconds: int = 120
    # Status bar chip visibility — show/hide only, keyed by the frontend's
    # stable status-bar item ids. Missing/legacy keys merge with defaults.
    status_bar_visibility: dict[str, bool] = field(
        default_factory=default_status_bar_visibility
    )
    # Boot nudges — fallback kickoff text used when an architect/engineer
    # role has no initial_prompt configured. Workers/terminals are
    # unaffected (they don't receive a default nudge). Empty string disables
    # the nudge for that kind.
    architect_default_boot_nudge: str = DEFAULT_ARCHITECT_BOOT_NUDGE
    engineer_default_boot_nudge: str = DEFAULT_ENGINEER_BOOT_NUDGE
    # Relay (optional cloud connector). Settings-primary config that the daemon
    # merges into the connector's context.config; env vars and ee_connector.json
    # remain the fallback when a field is left unset. NEVER store inline PEM
    # here — the private key is referenced by path only and loaded at runtime.
    relay_enabled: bool = False
    relay_url: str = ""
    relay_daemon_id: str = ""
    relay_credential_id: str = ""
    relay_private_key_path: str = ""
    # AI settings (non-secret only). Raw provider keys are intentionally stored
    # outside GlobalSettings because this dataclass is persisted in snapshots,
    # global_settings_update deltas, and CLI/offline reads.
    ai_enabled: bool = False
    ai_generation_provider: str = "anthropic"
    ai_anthropic_model: str = ""
    ai_openai_compatible_base_url: str = ""
    ai_openai_compatible_model: str = ""
    ai_embedding_model: str = AI_DEFAULT_EMBEDDING_MODEL
    ai_embedding_runtime: str = "sentence_transformers"
    ai_index_corpus: dict[str, bool] = field(
        default_factory=default_ai_index_corpus
    )
    ai_boot_summary_enabled: bool = True
    ai_boot_summary_min_interval_seconds: int = (
        AI_BOOT_SUMMARY_DEFAULT_MIN_INTERVAL_SECONDS
    )
    ai_boot_summary_max_refreshes_per_hour: int = (
        AI_BOOT_SUMMARY_DEFAULT_MAX_REFRESHES_PER_HOUR
    )

    def __post_init__(self):
        self.xterm_scrollback = normalize_xterm_scrollback(
            self.xterm_scrollback
        )
        self.event_ingest_max_rows = normalize_event_ingest_max_rows(
            self.event_ingest_max_rows
        )
        self.event_ingest_max_days = normalize_event_ingest_max_days(
            self.event_ingest_max_days
        )
        self.metrics_enabled = normalize_relay_enabled(self.metrics_enabled)
        self.mcp_call_log_args_capture = normalize_mcp_call_log_args_capture(
            self.mcp_call_log_args_capture
        )
        self.mcp_call_log_full_capture_tools = (
            normalize_mcp_call_log_full_capture_tools(
                self.mcp_call_log_full_capture_tools
            )
        )
        self.perceived_empty_probe_threshold = (
            coerce_perceived_empty_threshold(
                self.perceived_empty_probe_threshold
            )
        )
        self.perceived_empty_window_seconds = (
            coerce_perceived_empty_window_seconds(
                self.perceived_empty_window_seconds
            )
        )
        self.status_bar_visibility = normalize_status_bar_visibility(
            self.status_bar_visibility
        )
        self.relay_enabled = normalize_relay_enabled(self.relay_enabled)
        self.relay_url = normalize_relay_text(self.relay_url)
        self.relay_daemon_id = normalize_relay_text(self.relay_daemon_id)
        self.relay_credential_id = normalize_relay_text(self.relay_credential_id)
        self.relay_private_key_path = normalize_relay_text(
            self.relay_private_key_path
        )
        self.ai_enabled = normalize_relay_enabled(self.ai_enabled)
        self.ai_generation_provider = normalize_ai_generation_provider(
            self.ai_generation_provider,
            strict=False,
        )
        self.ai_anthropic_model = normalize_ai_text(self.ai_anthropic_model)
        self.ai_openai_compatible_base_url = normalize_ai_text(
            self.ai_openai_compatible_base_url
        )
        self.ai_openai_compatible_model = normalize_ai_text(
            self.ai_openai_compatible_model
        )
        self.ai_embedding_model = (
            normalize_ai_text(self.ai_embedding_model)
            or AI_DEFAULT_EMBEDDING_MODEL
        )
        self.ai_embedding_runtime = normalize_ai_embedding_runtime(
            self.ai_embedding_runtime,
            strict=False,
        )
        self.ai_index_corpus = normalize_ai_index_corpus(
            self.ai_index_corpus
        )
        self.ai_boot_summary_enabled = normalize_relay_enabled(
            self.ai_boot_summary_enabled
        )
        self.ai_boot_summary_min_interval_seconds = (
            normalize_ai_boot_summary_min_interval_seconds(
                self.ai_boot_summary_min_interval_seconds
            )
        )
        self.ai_boot_summary_max_refreshes_per_hour = (
            normalize_ai_boot_summary_max_refreshes_per_hour(
                self.ai_boot_summary_max_refreshes_per_hour
            )
        )


# Imported after shared state models/helpers are defined so the mixins can
# reuse the compatibility symbols without moving the public state surface.
from .state_board_health import BoardHealthMixin
from .state_board_mutations import BoardMutationMixin
from .state_board_queries import BoardQueryMixin
from .state_core_views import StateCoreViewsMixin
from .state_lifecycle import StateLifecycleMixin
from .state_loading import StateLoadingMixin
from .state_messages import StateMessagesMixin
from .state_runtime import StateRuntimeMixin
from .state_services import StateServicesMixin
from .state_settings import StateSettingsMixin


class MatrixState(
    BoardHealthMixin, BoardMutationMixin, BoardQueryMixin, StateLifecycleMixin,
    StateSettingsMixin, StateMessagesMixin, StateLoadingMixin, StateRuntimeMixin,
    StateServicesMixin, StateCoreViewsMixin,
):
    """In-memory state for all groups and agents, with JSON persistence."""

    @property
    def journal_data_dir(self) -> Path:
        """Resolve the journal root lazily so runtime/test overrides still apply."""
        if self.db is not None:
            db_path = Path(getattr(self.db, "db_path", "") or "")
            if str(db_path) not in {"", ".", ":memory:"}:
                return db_path.parent
        return Path(DATA_DIR)

    def __init__(self, db: Optional[TorqueDB] = None):
        self.db: Optional[TorqueDB] = db
        self._initiative_service = InitiativeService(self)
        self._area_service = AreaService(self)
        self._thinking_service = ThinkingService(self)
        self._idea_brief_service = IdeaBriefService(self)
        self._architect_governance_service = ArchitectGovernanceService(self)
        self._behavior_overlay_service = BehaviorOverlayService(self)
        self._journal_service = JournalService(self)
        self._metrics_service = MetricsService(self)
        self._operator_notice_service = OperatorNoticeService(self)
        self._task_watch_service = TaskWatchService(self)
        self._reminder_service = ReminderService(self)
        self.boot_timestamp: float = time.time()
        self.boot_repo_root: str = ""
        self.boot_head_commit: str = ""
        self.boot_mainline_branch: str = ""
        self.boot_head_error: str = ""
        self.agents: dict[str, AgentCell] = {}
        self.groups: dict[str, list[str]] = {}
        self.group_slugs: dict[str, str] = {}  # group_name → slug
        self.group_settings: dict[str, GroupSettings] = {}
        self.active_session_id: Optional[str] = None
        self.current_window_id: Optional[str] = None
        self._children: dict[str, list[str]] = {}  # agent_id → [child terminal ids]
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._ws_client_ids: dict[web.WebSocketResponse, str] = {}
        self._ws_client_compact: dict[web.WebSocketResponse, bool] = {}
        self._ws_clients_lock = asyncio.Lock()
        # Browser focus is intentionally connection/client-scoped. Multiple
        # Torque browser sessions can view different embedded terminals through
        # the same daemon; one client's xterm resize/focus event must not yank
        # another client's active terminal selection.
        self._client_focus_state: dict[str, dict[str, Optional[str]]] = {}
        # Global settings
        self.global_settings: GlobalSettings = GlobalSettings()
        self.metrics_collector = MetricsCollector(
            enabled=self.global_settings.metrics_enabled
        )
        # Board (Phase 5)
        self.board_lanes: list[str] = list(_DEFAULT_LANES)
        self.board_tasks: dict[str, BoardTask] = {}
        self._task_upsert_observers: list[Callable[[dict], None]] = []
        self._delta_observers: list[
            tuple[Callable[[dict], None], frozenset[str] | None]
        ] = []
        # Secondary task indexes. Maintained incrementally by
        # `_index_task` / `_unindex_task`; hot-path consumers should not
        # have to scan the full board when they already know the relevant
        # group/parent/agent/dependency edge.
        self._tasks_by_group: dict[str, set[str]] = {}
        self._tasks_by_parent: dict[str, set[str]] = {}
        self._tasks_by_agent: dict[str, set[str]] = {}
        self._task_dependents_by_dep: dict[str, set[str]] = {}
        self._task_index_refs: dict[
            str, tuple[str, str, str, str, tuple[str, ...]]
        ] = {}
        self._task_health_dirty: set[str] = set()
        self._task_health_force_full: bool = True
        self._suppress_task_health_dirty: bool = False
        self._task_health_deadlines: dict[str, float] = {}
        self._task_health_last_recompute_ts: float = 0.0
        self.task_id_aliases: dict[str, str] = {}
        self.task_id_counters: dict[str, int] = {}
        self.pipeline_task_counters: dict[str, int] = {}
        self.schedules: dict[str, Schedule] = {}
        self.agent_message_loops: dict[str, AgentMessageLoop] = {}
        self.auto_dispatch_queues: dict[str, list[AutoDispatchQueueEntry]] = {}
        self.panel_active: str = ""  # '' | 'board' | 'actions' | 'events'
        self.board_panel_height: int = 0  # 0 = use CSS default
        # Browser/Tauri UI state mirrored through ui_state so standalone,
        # desktop, and detached-window sessions restore after daemon restart.
        self.active_group: str = ""
        # Legacy principal selector state mirrored through ui_state for older
        # clients/sessions. Empty string means "user"; architect ids are
        # persisted but no longer filter the agent grid.
        self.selected_principal_id: str = ""
        # Browser-local selectedAgentId mirrored through ui_state so detached
        # windows can hydrate the same Agent/Context panel focus.
        self.selected_agent_id: str = ""
        self.standalone_panel_layout: dict = {}
        self.detached_panels: dict[str, dict] = {}
        self.window_bounds: dict[str, dict] = {}
        self.workspace_sidebar_width: int = 0
        self.terminal_direct_messages_height: int = 0
        self.terminal_compose_height: int = 0
        self.engineer_panel_split_fraction: float = 0.30
        self.context_panel_split_ratio: float = 0.38
        self.supervisor_panel_state: dict = {}
        self.events_dismissed_attention: dict[str, float] = {}
        self.board_filters_by_group: dict[str, dict] = {}
        self.board_selected_lanes_by_group: dict[str, str] = {}
        self.board_hidden_wide_lanes_by_group: dict[str, dict] = {}
        self.board_saved_views_by_group: dict[str, list] = {}
        self.board_lane_sorts_by_group: dict[str, dict] = {}
        self.board_card_density_by_group: dict[str, str] = {}
        self.panel_log = None  # PanelEventLog, set from server.py
        # Engineer settings (per-group)
        self.engineer_settings: dict[str, EngineerSettings] = {}
        self.agent_digest_settings: dict[str, AgentDigestSettings] = {}
        self.agent_settings: dict[str, AgentSettings] = {}
        self.engineer_worklog: dict[str, list[dict]] = {}
        # In-memory only per-Engineer dispatch-shape ring buffer. This is an
        # advisory read model for live dispatch-affordance metrics; it is not
        # persisted and intentionally does not appear in snapshots.
        self.engineer_dispatch_shapes: dict[str, list[dict]] = {}
        self._engineer_dispatch_shape_seq: int = 0
        # Newest-first per-agent user message recall cache. The durable source
        # of truth is SQLite; this cache is intentionally bounded for live
        # deltas and snapshot assembly.
        self.agent_message_history: dict[str, list[dict]] = {}
        # Oldest-first per-agent direct-message cache for the user↔agent
        # below-terminal panel. The durable source of truth is SQLite; this
        # read model is bounded and replayed through direct_message_* deltas.
        self.direct_messages_by_agent: dict[str, list[dict]] = {}
        # In-memory-only counters for recurring, non-load-bearing guidance
        # hints. Keyed by hint type + agent id + provider session id so a
        # fresh boot/session sees the first occurrence again. Daemon restart
        # reset is acceptable and intentionally not persisted.
        self.guidance_hint_counters: dict[str, int] = {}
        # Newest-first thread aggregate for the read-only agent↔agent Chat
        # panel. Keyed strictly by canonical agent_peer_messages.thread_id;
        # messages inside each thread are oldest-first and tail-capped.
        self.agent_peer_threads: dict[str, dict] = {}
        # Ephemeral daemon-global relay connection-state signal for the status
        # bar.  Driven by the optional EE connector via set_relay_connection();
        # never persisted and reset to "disabled" on every restart. The full
        # distinct enum is preserved here (connecting/disconnected are not
        # collapsed); the UI groups them visually.
        self.relay_connection: dict = self._default_relay_connection()
        # Ephemeral resolved relay (cloud connector) config WITH per-field
        # provenance (settings / ee_connector.json / env / unset). Populated by
        # the server from GlobalSettings + file + env when the connector context
        # is (re)built; lets the Settings "Relay" UI show each effective value's
        # source. Never persisted; recomputed on boot and on relay-settings save.
        self.relay_config: dict = {"config": {}, "sources": {}}
        # Delta broadcast accumulator
        self._delta_ops: list[dict] = []
        self._seq: int = 0
        # Per-agent fingerprint of engineer-relevant fields. Lets
        # `_collect_engineer_affected_groups` skip recomputing a group's
        # engineer streams when an agent_upsert only changes ephemeral
        # fields (activity, path, last_event_at, etc.).
        self._agent_engineer_fingerprints: dict[str, tuple] = {}
        self._engineer_queue_empty_since: dict[str, float] = {}
        # Deferred engineer-stream recompute. `broadcast()` queues affected
        # groups into `_engineer_recompute_pending` and spawns a single
        # worker task that prefills branch-existence, computes each
        # group's stream payload, and emits a follow-up `engineer_streams`
        # delta. Keeps the primary delta frame off the git-subprocess
        # hot path so UI mutations feel instant.
        self._engineer_recompute_pending: set[str] = set()
        self._engineer_recompute_task = None
        self._engineer_recompute_shutdown = False
        self._engineer_recompute_handoff = None
        self._critical_write_capture_var: contextvars.ContextVar[
            CriticalWriteCapture | None
        ] = contextvars.ContextVar(
            f"matrix_state_critical_write_capture_{id(self)}",
            default=None,
        )

    # -- Per-client PTY focus ----------------------------------------------

    def _register_ws_client_locked(
        self,
        ws: web.WebSocketResponse,
        client_id: str = "",
        *,
        compact: bool = True,
    ) -> None:
        """Register a ready UI websocket plus its optional browser client id.

        Caller must hold ``_ws_clients_lock``. Terminal websocket connections
        are not registered here; this set is only for the main UI state socket.

        ``compact`` records the client's snapshot protocol: compact clients
        receive board_task_compact task deltas, legacy clients full bodies.
        """
        self._ws_clients.add(ws)
        client_id = str(client_id or "").strip()
        if client_id:
            self._ws_client_ids[ws] = client_id
        else:
            self._ws_client_ids.pop(ws, None)
        self._ws_client_compact[ws] = bool(compact)
        if not compact:
            # Deltas emitted after this client's snapshot was built but
            # before registration carry no full body. Backfill them so the
            # legacy client's first frame is not compact-shaped.
            for op in self._delta_ops:
                if op.get("op") != "task_upsert" or "_full" in op:
                    continue
                task = self.board_tasks.get(str(op.get("id", "") or ""))
                if task is not None:
                    op["_full"] = asdict(task)

    def _has_legacy_ws_clients(self) -> bool:
        return any(
            not compact for compact in self._ws_client_compact.values()
        )

    def _discard_ws_clients_locked(
        self,
        clients: set[web.WebSocketResponse],
    ) -> None:
        """Discard UI websockets and associated per-client metadata.

        Caller must hold ``_ws_clients_lock``.
        """
        if not clients:
            return
        self._ws_clients -= clients
        for ws in clients:
            self._ws_client_ids.pop(ws, None)
            self._ws_client_compact.pop(ws, None)

    def client_focus_state(
        self,
        client_id: str,
    ) -> dict[str, Optional[str]] | None:
        client_id = str(client_id or "").strip()
        if not client_id:
            return None
        focus = self._client_focus_state.get(client_id)
        return dict(focus) if focus is not None else None

    def set_client_focus_state(
        self,
        client_id: str,
        *,
        active_session_id: Optional[str],
        current_window_id: Optional[str] = "standalone",
    ) -> None:
        client_id = str(client_id or "").strip()
        if not client_id:
            return
        self._client_focus_state[client_id] = {
            "active_session_id": active_session_id,
            "current_window_id": current_window_id or "standalone",
        }

    def overlay_client_focus_state(self, payload: dict, client_id: str) -> dict:
        """Overlay a client's ephemeral focus state onto a state snapshot."""
        focus = self.client_focus_state(client_id)
        if not focus:
            return payload
        payload["active_session_id"] = focus.get("active_session_id")
        payload["current_window_id"] = focus.get("current_window_id")
        payload["client_scoped_focus"] = True
        return payload

    @staticmethod
    def _ops_include_focus_update(ops: list[dict]) -> bool:
        return any((op or {}).get("op") == "focus_update" for op in ops)

    @staticmethod
    def _ops_with_client_focus_overlay(
        ops: list[dict],
        focus: dict[str, Optional[str]],
    ) -> list[dict]:
        """Return delta ops with focus_update patched to client-local focus.

        Global PTY focus deltas are legacy/backcompat state. Browser clients
        that have established a per-client focus override must not have that
        local selection yanked by an unrelated global focus change, especially
        when the global active session stops.
        """
        active_session_id = focus.get("active_session_id")
        current_window_id = focus.get("current_window_id") or "standalone"
        patched: list[dict] = []
        for op in ops:
            if (op or {}).get("op") != "focus_update":
                patched.append(op)
                continue
            focus_op = dict(op)
            focus_op["active_session_id"] = active_session_id
            focus_op["current_window_id"] = current_window_id
            focus_op["client_scoped"] = True
            patched.append(focus_op)
        return patched

    async def send_client_focus_update(
        self,
        client_id: str,
        *,
        active_session_id: Optional[str],
        current_window_id: Optional[str] = "standalone",
    ) -> bool:
        """Send a client-scoped focus update without advancing global WS seq.

        Focus changes caused by embedded xterm sockets are local UI concerns,
        so they are delivered as a direct message to UI websockets with the
        same client id instead of as a global delta broadcast.
        """
        client_id = str(client_id or "").strip()
        if not client_id:
            return False
        self.set_client_focus_state(
            client_id,
            active_session_id=active_session_id,
            current_window_id=current_window_id,
        )
        payload = {
            "type": "focus_update",
            "client_scoped": True,
            "active_session_id": active_session_id,
            "current_window_id": current_window_id or "standalone",
        }
        msg = await hot_json_dumps_async(payload, offload=False)
        async with self._ws_clients_lock:
            clients = [
                ws for ws in self._ws_clients
                if self._ws_client_ids.get(ws) == client_id
            ]
        if not clients:
            return True
        results = await asyncio.gather(
            *(ws.send_str(msg) for ws in clients),
            return_exceptions=True,
        )
        dead: set[web.WebSocketResponse] = {
            ws for ws, result in zip(clients, results)
            if isinstance(result, BaseException)
        }
        if dead:
            async with self._ws_clients_lock:
                self._discard_ws_clients_locked(dead)
        return True

    async def clear_client_focus_for_session(
        self,
        session_id: str,
        *,
        current_window_id: Optional[str] = "standalone",
    ) -> None:
        """Clear any client-local focus entries pointing at a stopped session."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        affected = [
            client_id
            for client_id, focus in list(self._client_focus_state.items())
            if str((focus or {}).get("active_session_id") or "") == session_id
        ]
        for client_id in affected:
            await self.send_client_focus_update(
                client_id,
                active_session_id=None,
                current_window_id=current_window_id,
            )

    # -- Delta emission -----------------------------------------------------

    def _emit(self, op: str, **kwargs):
        """Accumulate a delta operation for the next broadcast."""
        old_task_refs = self._sync_task_indexes_from_delta(op, kwargs)
        self._track_task_health_delta(op, kwargs, old_task_refs=old_task_refs)
        self._maybe_clear_engineer_queue_empty_from_delta(op, kwargs)
        delta = {"op": op, **kwargs}
        self._delta_ops.append(delta)
        meter = self.metrics_collector
        if meter.enabled:
            meter.record_emit(op, kwargs)
        if op == "task_upsert":
            self._notify_task_upsert_observers(kwargs)
        self._notify_delta_observers(delta)

    def register_task_upsert_observer(
            self,
            observer: Callable[[dict], None],
    ) -> Callable[[], None]:
        """Register a provider-agnostic task-upsert observer.

        Observers are intentionally best-effort sidecars: they run after the
        delta is appended, and exceptions are logged without interrupting the
        state mutation that produced the task update.
        """
        if observer not in self._task_upsert_observers:
            self._task_upsert_observers.append(observer)

        def unregister() -> None:
            try:
                self._task_upsert_observers.remove(observer)
            except ValueError:
                pass

        return unregister

    def register_delta_observer(
            self,
            observer: Callable[[dict], None],
            *,
            ops: set[str] | frozenset[str] | None = None,
    ) -> Callable[[], None]:
        """Register a best-effort observer for selected delta operations.

        ``ops=None`` preserves the legacy subscribe-to-all behavior.  A
        filtered observer avoids both callback dispatch and the defensive
        deep copy for unrelated high-frequency state deltas.
        """
        normalized_ops = (
            frozenset(str(op) for op in ops if str(op))
            if ops is not None
            else None
        )
        entry = (observer, normalized_ops)
        if not any(existing[0] == observer for existing in self._delta_observers):
            self._delta_observers.append(entry)

        def unregister() -> None:
            try:
                self._delta_observers.remove(entry)
            except ValueError:
                pass

        return unregister

    def _notify_delta_observers(self, delta: dict) -> None:
        if not self._delta_observers:
            return
        op = str((delta or {}).get("op", "") or "")
        observers = [
            observer
            for observer, observed_ops in list(self._delta_observers)
            if observed_ops is None or op in observed_ops
        ]
        if not observers:
            return
        snapshot = copy.deepcopy(delta or {})
        for observer in observers:
            try:
                observer(snapshot)
            except Exception:
                log.exception("State delta observer failed")

    def _notify_task_upsert_observers(self, payload: dict) -> None:
        if not self._task_upsert_observers:
            return
        snapshot = dict(payload or {})
        for observer in list(self._task_upsert_observers):
            try:
                observer(snapshot)
            except Exception:
                log.exception("Task-upsert observer failed")

    def emit_task_upsert(self, task: "BoardTask") -> None:
        """Emit a task_upsert delta with the compact wire payload.

        Live deltas used to broadcast ``asdict(task)`` — an unbounded
        message log plus full artifact bodies, per report, per client —
        while snapshots already used ``board_task_compact``. Compact
        clients render from the summaries and lazy-load heavy fields via
        ``task_detail``. While a legacy-snapshot client is connected the
        op also carries the full body under ``_full``; ``broadcast()``
        strips it from the compact frame and expands it for legacy
        clients only.
        """
        payload = board_task_compact(task)
        if self._has_legacy_ws_clients():
            payload["_full"] = asdict(task)
        self._emit("task_upsert", **payload)

    def _emit_agent(self, cell: AgentCell, *, coalesce_ephemeral: bool = False):
        """Emit an agent_upsert delta with the bounded client projection."""
        payload = agent_client_dict(cell)
        if coalesce_ephemeral and self._coalesce_pending_agent_upsert(payload):
            return
        self._emit("agent_upsert", **payload)

    def flag_perceived_empty_episode(
        self,
        aid: str,
        *,
        detail: str = "",
    ) -> bool:
        """Surface a perceived-empty tool-result episode on an agent.

        This is intentionally surface-only: it sets ``needs_attention`` and
        emits/saves the agent row, but it does not pause, stop, or otherwise
        change the worker's execution state.
        """

        cell = self.agents.get(str(aid or "").strip())
        if not cell:
            return False
        message = str(detail or "Perceived-empty tool-result episode detected")
        cell.needs_attention = True
        cell.last_event_at = time.time()
        cell.last_event_text = message[:300]
        # Keep the current process/status intact.  Only fill activity_detail
        # when it is blank so this guardrail does not erase a more specific
        # worker-reported blocker/error.
        if not str(getattr(cell, "activity_detail", "") or "").strip():
            cell.activity_detail = message[:500]
        self._emit_agent(cell)
        self._db_save_agent(cell)
        return True

    def _coalesce_pending_agent_upsert(self, payload: dict) -> bool:
        """Replace the latest pending upsert for this agent with ``payload``.

        Agent telemetry such as context-window and provider rate-limit usage is
        high-frequency and already broadcast on a trailing-edge timer.  Keep at
        most one pending ``agent_upsert`` per agent for those telemetry events
        so a burst of statusLine updates becomes one latest-value delta.
        """
        agent_id = str((payload or {}).get("id", "") or "")
        if not agent_id:
            return False
        for index in range(len(self._delta_ops) - 1, -1, -1):
            op = self._delta_ops[index] or {}
            op_name = str(op.get("op", "") or "")
            op_id = str(op.get("id", "") or "")
            if op_id != agent_id:
                continue
            if op_name == "agent_remove":
                return False
            if op_name == "agent_upsert":
                self._delta_ops[index] = {"op": "agent_upsert", **payload}
                return True
        return False

    def _clear_engineer_queue_empty_emitted(self, engineer_id: str) -> bool:
        """Clear the one-shot queue-empty gate when an engineer picks up work."""
        engineer_id = str(engineer_id or "").strip()
        if not engineer_id:
            return False
        engineer = self.agents.get(engineer_id)
        if (
                not engineer
                or str(getattr(engineer, "kind", "") or "").strip() != "engineer"
                or not bool(getattr(engineer, "queue_empty_emitted", True))
        ):
            return False
        engineer.queue_empty_emitted = False
        self._engineer_queue_empty_since.pop(engineer.id, None)
        self._db_save_agent(engineer)
        return True

    def _maybe_clear_engineer_queue_empty_from_delta(
        self,
        op: str,
        payload: dict,
    ) -> None:
        """Observe central task/worker upserts and reopen queue-empty cycles."""
        if op == "task_upsert":
            lane = str((payload or {}).get("lane", "") or "").strip()
            if lane not in {"To Do", "In Progress"}:
                return
            self._clear_engineer_queue_empty_emitted(
                str((payload or {}).get("assigned_engineer_id", "") or "")
            )
            return

        if op != "agent_upsert":
            return
        kind = str((payload or {}).get("kind", "") or "").strip()
        status = str((payload or {}).get("status", "") or "").strip()
        if kind != "worker" or status == "stopped":
            return
        owner_id = str((payload or {}).get("owner_engineer_id", "") or "").strip()
        if not owner_id:
            owner_id = str(
                (payload or {}).get("created_by_engineer_id", "") or ""
            ).strip()
        self._clear_engineer_queue_empty_emitted(owner_id)

    def _clear_engineer_queue_empty_for_active_tasks(self) -> None:
        """Reopen queue-empty cycles for active assigned tasks loaded from DB."""
        active_engineer_ids = {
            str(getattr(task, "assigned_engineer_id", "") or "").strip()
            for task in self.board_tasks.values()
            if str(getattr(task, "lane", "") or "").strip()
            in {"To Do", "In Progress"}
        }
        for engineer_id in active_engineer_ids:
            self._clear_engineer_queue_empty_emitted(engineer_id)

    def _emit_group(self, name: str):
        """Emit a group_update delta with the current member list."""
        self._emit("group_update", name=name,
                   slug=self.group_slugs.get(name, ""),
                   agents=list(self.groups.get(name, [])))

    def _emit_decision(self, decision: dict | None):
        """Emit a decision delta for live UI sync."""
        if not decision:
            return
        payload = dict(decision)
        self._emit("decision_upsert", **payload)

    def _emit_pending_hire(self, pending_hire: dict | None):
        """Emit a pending-hire delta for live UI sync."""
        if not pending_hire:
            return
        payload = dict(pending_hire)
        status = str(payload.get("status", "") or "").strip()
        op = "pending_hire_upsert" if status == "pending" else "pending_hire_resolve"
        self._emit(op, **payload)

    @staticmethod
    def _default_relay_connection() -> dict:
        """Default relay_connection signal: cloud relay off in community builds."""
        return {
            "status": "disabled",
            "enabled": False,
            "relay_host": "",
            "daemon_id": "",
            "last_connected_at": "",
            "last_error": "",
            "retry_count": 0,
            "since": "",
        }

    def set_relay_connection(self, payload: dict | None) -> bool:
        """Store and broadcast the ephemeral relay connection-state signal.

        Producer-side throttling/coalescing already keeps the call rate low (the
        EE connector emits on status-enum change and debounces retry churn).
        This is the second line of defense against a delta storm: we DEDUPE here
        and only emit a ``relay_connection`` delta when the payload actually
        changed versus the current value.  The signal is ephemeral — never
        persisted, cleared back to "disabled" on restart.  Returns True iff a
        delta was emitted.
        """
        merged = self._default_relay_connection()
        if isinstance(payload, dict):
            for key in merged:
                if key in payload and payload[key] is not None:
                    merged[key] = payload[key]
        if merged == self.relay_connection:
            return False
        self.relay_connection = merged
        self._emit("relay_connection", **merged)
        # The connector reports out-of-band (not inside a command flow that
        # already calls broadcast()), so schedule a flush. Low call-rate by
        # construction: dedupe above + producer-side status/retry throttling.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (e.g. unit tests inspecting _delta_ops directly);
            # the delta stays queued for the next real broadcast.
            return True
        loop.create_task(self.broadcast())
        return True

    def set_relay_config(self, payload: dict | None) -> bool:
        """Store and broadcast the resolved relay config + provenance.

        Ephemeral (never persisted): recomputed by the server from GlobalSettings
        + ee_connector.json + env whenever the connector context is (re)built.
        Dedupes and only emits a ``relay_config`` delta on change. Returns True
        iff a delta was emitted.
        """
        merged = {"config": {}, "sources": {}}
        if isinstance(payload, dict):
            if isinstance(payload.get("config"), dict):
                merged["config"] = dict(payload["config"])
            if isinstance(payload.get("sources"), dict):
                merged["sources"] = copy.deepcopy(payload["sources"])
        if merged == self.relay_config:
            return False
        self.relay_config = merged
        self._emit("relay_config", **copy.deepcopy(merged))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return True
        loop.create_task(self.broadcast())
        return True

    # -- Task indexes --------------------------------------------------------

    def _task_index_values(self, task: "BoardTask"):
        deps = tuple(
            str(dep or "").strip()
            for dep in (getattr(task, "depends_on", []) or [])
            if str(dep or "").strip()
        )
        return (
            str(getattr(task, "group", "") or ""),
            str(getattr(task, "parent_task_id", "") or ""),
            str(getattr(task, "agent_id", "") or ""),
            str(getattr(task, "lane", "") or ""),
            deps,
        )

    @staticmethod
    def _task_index_ref_parts(refs):
        if not refs:
            return "", "", "", "", ()
        if len(refs) == 4:
            group, parent_id, agent_id, deps = refs
            return group, parent_id, agent_id, "", deps
        group, parent_id, agent_id, lane, deps = refs
        return group, parent_id, agent_id, lane, deps

    @staticmethod
    def _task_lane_counts_as_agent_open(lane: str) -> bool:
        return str(lane or "") not in {"Done", "Backlog", ARCHIVED_LANE}

    @staticmethod
    def _discard_indexed_id(index: dict[str, set[str]], key: str,
                            tid: str) -> None:
        if not key:
            return
        bucket = index.get(key)
        if bucket is None:
            return
        bucket.discard(tid)
        if not bucket:
            index.pop(key, None)

    def _remove_task_index_refs(self, tid: str, refs) -> None:
        if not refs:
            return
        group, parent_id, agent_id, _lane, deps = self._task_index_ref_parts(refs)
        self._discard_indexed_id(self._tasks_by_group, group, tid)
        self._discard_indexed_id(self._tasks_by_parent, parent_id, tid)
        self._discard_indexed_id(self._tasks_by_agent, agent_id, tid)
        for dep_id in deps or ():
            self._discard_indexed_id(self._task_dependents_by_dep, dep_id, tid)

    def _index_task(self, task: "BoardTask"):
        """Add/update a task in the secondary indexes. Idempotent."""
        if not task:
            return None
        tid = str(getattr(task, "id", "") or "")
        if not tid:
            return None
        old_refs = self._task_index_refs.get(tid)
        refs = self._task_index_values(task)
        if old_refs == refs:
            return old_refs
        self._remove_task_index_refs(tid, old_refs)
        group, parent_id, agent_id, _lane, deps = self._task_index_ref_parts(refs)
        group = str(getattr(task, "group", "") or "")
        self._tasks_by_group.setdefault(group, set()).add(tid)
        if parent_id:
            self._tasks_by_parent.setdefault(parent_id, set()).add(tid)
        if agent_id:
            self._tasks_by_agent.setdefault(agent_id, set()).add(tid)
        for dep_id in deps:
            self._task_dependents_by_dep.setdefault(dep_id, set()).add(tid)
        self._task_index_refs[tid] = refs
        return old_refs

    def _unindex_task(self, task_or_id) -> None:
        """Remove a task from the secondary indexes. Idempotent."""
        tid = task_or_id if isinstance(task_or_id, str) else task_or_id.id
        refs = self._task_index_refs.pop(tid, None)
        self._remove_task_index_refs(tid, refs)

    def _reindex_task_group(self, task: "BoardTask",
                            old_group: str, new_group: str) -> None:
        """Move a task between group buckets when its group changes."""
        self._index_task(task)

    def _rebuild_task_indexes(self) -> None:
        """Recompute all secondary indexes from scratch (used after bulk load)."""
        self._tasks_by_group = {}
        self._tasks_by_parent = {}
        self._tasks_by_agent = {}
        self._task_dependents_by_dep = {}
        self._task_index_refs = {}
        for task in self.board_tasks.values():
            self._index_task(task)

    def _sync_task_indexes_from_delta(self, op: str, payload: dict):
        if op != "task_upsert":
            return None
        tid = str((payload or {}).get("id", "") or "")
        task = self.board_tasks.get(tid)
        if not task:
            return None
        return self._index_task(task)

    def _mark_task_health_dirty(self, *task_ids: str) -> None:
        for task_id in task_ids:
            tid = str(task_id or "").strip()
            if tid:
                self._task_health_dirty.add(tid)

    def _track_task_health_delta(self, op: str, payload: dict,
                                 *, old_task_refs=None) -> None:
        """Remember the minimal task set affected by health-relevant ops."""
        if self._suppress_task_health_dirty:
            return
        if op == "task_upsert":
            tid = str((payload or {}).get("id", "") or "").strip()
            if not tid:
                return
            self._mark_task_health_dirty(tid)
            # A task mutation can unblock/block direct dependents.
            self._mark_task_health_dirty(
                *self._task_dependents_by_dep.get(tid, set())
            )
            # If the task moved between parents, both aggregate paths changed.
            new_task = self.board_tasks.get(tid)
            new_parent = str(getattr(new_task, "parent_task_id", "") or "")
            new_agent = str(getattr(new_task, "agent_id", "") or "")
            new_lane = str(getattr(new_task, "lane", "") or "")
            if old_task_refs:
                _old_group, old_parent, old_agent, old_lane, _deps = (
                    self._task_index_ref_parts(old_task_refs)
                )
                if old_parent and old_parent != new_parent:
                    self._mark_task_health_dirty(old_parent)
            else:
                old_agent = ""
                old_lane = ""

            old_open = (
                bool(old_agent)
                and self._task_lane_counts_as_agent_open(old_lane)
            )
            new_open = (
                bool(new_agent)
                and self._task_lane_counts_as_agent_open(new_lane)
            )
            if old_agent != new_agent or old_open != new_open:
                if old_open:
                    self._mark_task_health_dirty(
                        *self._tasks_by_agent.get(old_agent, set())
                    )
                if new_open:
                    self._mark_task_health_dirty(
                        *self._tasks_by_agent.get(new_agent, set())
                    )
            return

        if op == "task_remove":
            tid = str((payload or {}).get("id", "") or "").strip()
            if tid:
                self._mark_task_health_dirty(tid)
                self._mark_task_health_dirty(
                    *self._task_dependents_by_dep.get(tid, set())
                )
            return

        if op in {"agent_upsert", "agent_remove"}:
            agent_id = str((payload or {}).get("id", "") or "").strip()
            if agent_id:
                self._mark_task_health_dirty(
                    *self._tasks_by_agent.get(agent_id, set())
                )

    def _task_health_due_ids(self, now_ts: float) -> set[str]:
        stale_ids: list[str] = []
        due_ids: set[str] = set()
        for tid, deadline in list(self._task_health_deadlines.items()):
            if tid not in self.board_tasks:
                stale_ids.append(tid)
                continue
            try:
                due_at = float(deadline or 0.0)
            except (TypeError, ValueError):
                stale_ids.append(tid)
                continue
            if due_at <= 0:
                stale_ids.append(tid)
                continue
            if due_at <= now_ts:
                due_ids.add(tid)
        for tid in stale_ids:
            self._task_health_deadlines.pop(tid, None)
        return due_ids

    def has_pending_task_health_recompute(
            self,
            now_ts: float | None = None,
    ) -> bool:
        """Return whether a broadcast tick has health work to coalesce."""
        if self._task_health_force_full or self._task_health_dirty:
            return True
        if now_ts is None:
            return False
        return bool(self._task_health_due_ids(float(now_ts)))

    def tasks_in_group(self, group: str) -> list["BoardTask"]:
        """Return the BoardTask objects belonging to ``group`` via the index.

        Avoids iterating the full task table when the caller already
        knows which group it cares about. Falls back to a filtered scan
        if the group bucket is unset (defensive — should not happen
        once indexes are populated).
        """
        bucket = self._tasks_by_group.get(group)
        if bucket is None:
            # Lazy seed in case some code path bypassed the index.
            self._rebuild_task_indexes()
            bucket = self._tasks_by_group.get(group, set())
        return [self.board_tasks[tid] for tid in bucket
                if tid in self.board_tasks]

    def _engineer_stream_groups(self) -> list[str]:
        groups = set(self.groups)
        groups.update(self.group_settings)
        groups.update(self.engineer_settings)
        # Use the per-group task index instead of iterating all tasks.
        groups.update(self._tasks_by_group.keys())
        groups.update(
            str(getattr(cell, "group", "") or "").strip()
            for cell in self.iter_active_agents()
        )
        groups.discard("")
        return sorted(groups)

    def _engineer_streams_snapshot(self) -> dict[str, dict]:
        return {
            group: self._engineer_stream_payload(group)
            for group in self._engineer_stream_groups()
        }

    def _agent_engineer_fingerprint(self, op: dict) -> tuple:
        """Fingerprint the engineer-relevant fields of an agent_upsert op.

        Engineer stream identity/gating only depends on group, worktree
        identity, current task assignment, and whether the agent is
        busy. Everything else in the agent dict (activity, path, timers,
        summary text, tokens) is ephemeral and must not force a
        recompute — otherwise the per-second activity ticker would drive
        a git-forking stream recompute on every broadcast.
        """
        return (
            str(op.get("group", "") or ""),
            str(op.get("worktree_repo_root", "") or ""),
            str(op.get("git_root", "") or ""),
            str(op.get("worktree_branch", "") or ""),
            str(op.get("worktree_path", "") or ""),
            str(op.get("current_task_id", "") or ""),
            str(op.get("status", "") or ""),
            str(op.get("cell_type", "") or ""),
            _safe_float(op.get("deleted_at", 0.0) or 0.0),
        )

    def _collect_engineer_affected_groups(self, ops: list[dict]) -> set[str]:
        """Return the set of groups whose engineer streams need recomputing.

        Scans ``ops`` for trigger ops (task/agent/group mutations) and,
        as a side effect, updates the per-agent fingerprint cache used
        to dedupe ephemeral-only agent_upserts. The caller is expected
        to recompute + emit stream payloads for each returned group
        outside the broadcast hot path so the UI delta doesn't wait on
        git.
        """
        affected_groups: set[str] = set()
        for op in ops:
            op_name = str((op or {}).get("op", "") or "")
            if op_name not in _ENGINEER_STREAM_DELTA_TRIGGER_OPS:
                continue
            if op_name == "group_rename":
                affected_groups.add(str(op.get("old_name", "") or "").strip())
                affected_groups.add(str(op.get("new_name", "") or "").strip())
                continue
            if op_name in {"group_update", "group_remove"}:
                affected_groups.add(str(op.get("name", "") or "").strip())
                continue
            # Terminal cells don't participate in engineer streams; skip them
            # so a terminal upsert/remove doesn't force a group recompute.
            if op_name in {"agent_upsert", "agent_remove"}:
                cell_type = str(op.get("cell_type", "agent") or "agent").strip()
                if cell_type and cell_type != "agent":
                    continue
                agent_id = str(op.get("id", "") or "")
                if op_name == "agent_upsert" and agent_id:
                    fingerprint = self._agent_engineer_fingerprint(op)
                    if self._agent_engineer_fingerprints.get(
                            agent_id) == fingerprint:
                        continue
                    self._agent_engineer_fingerprints[agent_id] = fingerprint
                elif op_name == "agent_remove" and agent_id:
                    self._agent_engineer_fingerprints.pop(agent_id, None)
            affected_groups.add(str(op.get("group", "") or "").strip())
        affected_groups.discard("")
        return affected_groups

    def _emit_engineer_stream_ops(self, groups: set[str]) -> bool:
        """Emit `engineer_streams` delta ops for each group in ``groups``.

        Returns True when at least one op was emitted. Safe to call when
        the per-repo branch-exists cache is cold, but in practice
        callers should prefill first so no subprocess runs inline.
        """
        return self._emit_engineer_stream_payloads(
            self._compute_engineer_stream_payloads(groups)
        )

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        decisions = {}
        pending_hires = {}
        behavior_overlay_proposals, behavior_overlay_active = (
            self._behavior_overlay_service.snapshot()
        )
        if self.db:
            try:
                decisions = {
                    decision["id"]: decision
                    for decision in self.db.load_all_decisions(
                        include_archived=False
                    )
                }
            except Exception:
                log.exception("Failed to load decisions snapshot")
            try:
                pending_hires = {
                    pending_hire["id"]: pending_hire
                    for pending_hire in self.db.load_pending_hires(
                        status_filter="pending"
                    )
                }
            except Exception:
                log.exception("Failed to load pending hires snapshot")
        return {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
            "group_slugs": dict(self.group_slugs),
            "group_settings": {
                n: asdict(gs) for n, gs in self.group_settings.items()
            },
            "global_settings": asdict(self.global_settings),
            "children": self._children,
            "active_session_id": self.active_session_id,
            "current_window_id": self.current_window_id,
            "board_lanes": self.board_lanes,
            "board_tasks": {
                tid: asdict(t) for tid, t in self.board_tasks.items()
            },
            "task_id_aliases": dict(self.task_id_aliases),
            "task_id_counters": dict(self.task_id_counters),
            "pipeline_task_counters": dict(self.pipeline_task_counters),
            "schedules": {
                sid: asdict(s) for sid, s in self.schedules.items()
            },
            # Backend-owned catalog keeps terminal autocomplete in lockstep
            # with user→agent command recognition and discovery.
            "user_dm_commands": user_dm_command_catalog(),
            "agent_message_loops": self.agent_message_loops_snapshot(),
            "auto_dispatch_queues": {
                group: [asdict(entry) for entry in entries]
                for group, entries in self.auto_dispatch_queues.items()
            },
            "panel_active": self.panel_active,
            "board_panel_height": self.board_panel_height,
            "active_group": self.active_group,
            "selected_principal_id": self.selected_principal_id,
            "selected_agent_id": self.selected_agent_id,
            "standalone_panel_layout": self.standalone_panel_layout,
            "detached_panels": self.detached_panels,
            "window_bounds": self.window_bounds,
            "workspace_sidebar_width": self.workspace_sidebar_width,
            "terminal_direct_messages_height": (
                self.terminal_direct_messages_height
            ),
            "terminal_compose_height": self.terminal_compose_height,
            "engineer_panel_split_fraction": self.engineer_panel_split_fraction,
            "context_panel_split_ratio": self.context_panel_split_ratio,
            "supervisor_panel_state": self.supervisor_panel_state,
            "events_dismissed_attention": self.events_dismissed_attention,
            "board_filters_by_group": self.board_filters_by_group,
            "board_selected_lanes_by_group": self.board_selected_lanes_by_group,
            "board_hidden_wide_lanes_by_group": self.board_hidden_wide_lanes_by_group,
            "board_saved_views_by_group": self.board_saved_views_by_group,
            "board_lane_sorts_by_group": self.board_lane_sorts_by_group,
            "board_card_density_by_group": self.board_card_density_by_group,
            "panel_events": self.panel_log.get_recent(50) if self.panel_log else [],
            "engineer_settings": {
                n: asdict(ws) for n, ws in self.engineer_settings.items()
            },
            "architect_settings": {
                n: asdict(self.get_architect_settings(n))
                for n in self.groups
            },
            "agent_digest_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_digest_settings.items()
            },
            "agent_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_settings.items()
            },
            "resolved_agent_settings": {
                agent_id: self.resolve_agent_settings(agent_id)
                for agent_id, cell in self.agents.items()
                if cell.kind in {"architect", "engineer"}
            },
            "agent_message_history": self.agent_message_history_snapshot(),
            "direct_messages_by_agent": self.direct_messages_snapshot(),
            "agent_peer_threads": self.agent_peer_threads_snapshot(),
            # Engineer journal rows are stored with author_cell_id; expose the
            # UI snapshot cache with the same author key so focusing one
            # engineer never renders another engineer's group-mate entries.
            "engineer_journal": self.engineer_journal_snapshot_by_author(
                limit=50
            ),
            "engineer_worklog": {
                g: [dict(entry) for entry in entries]
                for g, entries in self.engineer_worklog.items()
            },
            "engineer_streams": self._engineer_streams_snapshot(),
            "decisions": decisions,
            "pending_hires": pending_hires,
            "operator_notices": self.operator_notices_snapshot(),
            "operator_notice_summary": self.operator_notice_summary(),
            "thinking": self.thinking_snapshot(),
            "idea_briefs": self.idea_brief_snapshot(),
            "behavior_overlay_proposals": behavior_overlay_proposals,
            "behavior_overlay_active": behavior_overlay_active,
            # Ephemeral daemon-global relay connection-state for the status bar.
            "relay_connection": dict(self.relay_connection),
            # Resolved relay config with per-field provenance for Settings.
            "relay_config": copy.deepcopy(self.relay_config),
        }

    def get_task_detail(self, task_id: str) -> dict | None:
        """Return the full BoardTask dict for a lazily-loaded task detail."""
        tid = self.resolve_board_task_id(task_id)
        if not tid:
            return None
        task = self.board_tasks.get(tid)
        if not task:
            return None
        return asdict(task)

    def get_archived_task_details(self, *, group: str = "") -> dict[str, dict]:
        """Return full archived task details, optionally scoped to a group."""
        group = str(group or "").strip()
        return {
            tid: asdict(task)
            for tid, task in self.board_tasks.items()
            if board_task_is_archived(task)
            and (not group or str(task.group or "") == group)
        }

    def engineer_ids_for_group(self, group: str) -> list[str]:
        """Return engineer cell ids in a group for author-keyed UI caches."""
        group = str(group or "").strip()
        if not group:
            return []
        ids: list[str] = []
        seen: set[str] = set()
        settings = self.group_settings.get(group)
        configured_id = str(
            getattr(settings, "engineer_agent_id", "") if settings else ""
        ).strip()
        if configured_id:
            ids.append(configured_id)
            seen.add(configured_id)
        for aid, agent in self.agents.items():
            if str(getattr(agent, "group", "") or "") != group:
                continue
            if str(getattr(agent, "cell_type", "") or "") != "agent":
                continue
            if str(getattr(agent, "kind", "") or "") != "engineer":
                continue
            aid = str(aid or "").strip()
            if aid and aid not in seen:
                ids.append(aid)
                seen.add(aid)
        return ids

    def engineer_journal_snapshot_by_author(
            self, group: str = "", limit: int = 50) -> dict[str, list[dict]]:
        """Return engineer journal entries keyed by author_cell_id."""
        groups = [str(group or "").strip()] if group else sorted(
            set(self.engineer_settings) | set(self.groups)
        )
        snapshot: dict[str, list[dict]] = {}
        for group_name in groups:
            if not group_name:
                continue
            for engineer_id in self.engineer_ids_for_group(group_name):
                snapshot[engineer_id] = self.journal_read(
                    group_name,
                    limit=limit,
                    author_cell_id=engineer_id,
                )
        return snapshot

    def record_engineer_dispatch_shape(
            self,
            engineer_id: str,
            *,
            group: str = "",
            source_tool: str = "",
            shape: str = "",
            task_ids: Optional[list[str]] = None,
            task_count: Optional[int] = None,
            outcome: str = "",
            hintable: bool = False,
            metadata: Optional[dict] = None,
            timestamp: str = "") -> dict:
        """Record one in-memory dispatch-shape metric event.

        Events are newest-first and capped per Engineer. This read model is
        intentionally volatile and used only for recent affordance metrics.
        """
        engineer_id = str(engineer_id or "").strip()
        if not engineer_id:
            return {}
        normalized_shape = str(shape or "").strip()
        if normalized_shape not in _ENGINEER_DISPATCH_SHAPES:
            raise ValueError(f"invalid engineer dispatch shape: {shape!r}")
        normalized_source = str(source_tool or "").strip()
        if not normalized_source:
            raise ValueError("source_tool is required")
        normalized_task_ids = [
            str(tid or "").strip()
            for tid in (task_ids or [])
            if str(tid or "").strip()
        ]
        if task_count is None:
            normalized_task_count = len(normalized_task_ids)
        else:
            try:
                normalized_task_count = max(0, int(task_count))
            except (TypeError, ValueError):
                normalized_task_count = len(normalized_task_ids)
        self._engineer_dispatch_shape_seq += 1
        record = {
            "id": self._engineer_dispatch_shape_seq,
            "timestamp": (
                str(timestamp or "").strip()
                or datetime.now(timezone.utc).isoformat()
            ),
            "engineer_id": engineer_id,
            "group": str(group or "").strip(),
            "source_tool": normalized_source,
            "shape": normalized_shape,
            "task_ids": normalized_task_ids,
            "task_count": normalized_task_count,
            "outcome": str(outcome or "").strip() or "ok",
            "hintable": bool(hintable),
            "metadata": copy.deepcopy(metadata)
            if isinstance(metadata, dict)
            else {},
        }
        events = self.engineer_dispatch_shapes.setdefault(engineer_id, [])
        events.insert(0, record)
        del events[ENGINEER_DISPATCH_SHAPE_EVENT_LIMIT:]
        return copy.deepcopy(record)

    def engineer_dispatch_shape_events(
            self,
            engineer_id: str,
            group: str = "",
            limit: int = 20,
            include_derives: bool = True) -> list[dict]:
        """Return recent dispatch-shape events for an Engineer."""
        engineer_id = str(engineer_id or "").strip()
        if not engineer_id:
            return []
        group = str(group or "").strip()
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = 20
        if limit_int == 0:
            return []
        events = []
        for event in self.engineer_dispatch_shapes.get(engineer_id, []):
            if group and str(event.get("group", "") or "") != group:
                continue
            if (
                    not include_derives
                    and str(event.get("source_tool", "") or "")
                    == "torque_derive"):
                continue
            events.append(copy.deepcopy(event))
            if limit_int >= 0 and len(events) >= limit_int:
                break
        return events

    def engineer_dispatch_shape_summary(
            self,
            engineer_id: str,
            group: str = "",
            window: int = 20) -> dict:
        """Return compact recent dispatch-shape counts for an Engineer."""
        try:
            window_int = max(0, int(window))
        except (TypeError, ValueError):
            window_int = 20
        events = self.engineer_dispatch_shape_events(
            engineer_id,
            group=group,
            limit=ENGINEER_DISPATCH_SHAPE_EVENT_LIMIT,
            include_derives=True,
        )
        direct_events = [
            event for event in events
            if str(event.get("source_tool", "") or "") != "torque_derive"
        ][:window_int]
        derive_events = [
            event for event in events
            if str(event.get("source_tool", "") or "") == "torque_derive"
        ][:window_int]
        counts = {shape: 0 for shape in _ENGINEER_DISPATCH_SHAPE_ORDER}
        derives_by_shape = {
            shape: 0 for shape in _ENGINEER_DISPATCH_SHAPE_ORDER
        }
        for event in direct_events:
            shape = str(event.get("shape", "") or "")
            if shape in counts:
                counts[shape] += 1
        for event in derive_events:
            shape = str(event.get("shape", "") or "")
            if shape in derives_by_shape:
                derives_by_shape[shape] += 1
        summary = {
            "window": window_int,
            "total": len(direct_events),
            "counts": counts,
            "hintable_serial": sum(
                1 for event in direct_events
                if event.get("shape") == "serial" and event.get("hintable")
            ),
            "derives_total": len(derive_events),
            "derives_by_shape": derives_by_shape,
        }
        selected_events = events[:window_int] if window_int else []
        if selected_events:
            summary["last_event_at"] = selected_events[0].get("timestamp", "")
            summary["oldest_event_at"] = selected_events[-1].get(
                "timestamp", ""
            )
        return summary

    def to_dict_compact(self) -> dict:
        """Return an opt-in compact snapshot for new lazy-loading clients.

        This intentionally does not call ``to_dict()``: the legacy snapshot
        performs synchronous decision/pending-hire/journal/engineer-stream reads
        and expands full BoardTask rows. Compact clients fetch those heavier
        slices with explicit lazy-load commands after the socket is ready.
        """
        behavior_overlay_proposals, behavior_overlay_active = (
            self._behavior_overlay_service.snapshot()
        )
        return {
            "snapshot_protocol": COMPACT_SNAPSHOT_PROTOCOL,
            "agents": {
                aid: agent_client_dict(agent)
                for aid, agent in self.agents.items()
            },
            "groups": self.groups,
            "group_slugs": dict(self.group_slugs),
            "group_settings": {
                n: asdict(gs) for n, gs in self.group_settings.items()
            },
            "global_settings": asdict(self.global_settings),
            "children": self._children,
            "active_session_id": self.active_session_id,
            "current_window_id": self.current_window_id,
            "board_lanes": self.board_lanes,
            "board_tasks": {
                tid: board_task_compact(t)
                for tid, t in self.board_tasks.items()
                if not board_task_is_archived(t)
            },
            "task_id_aliases": dict(self.task_id_aliases),
            "task_id_counters": dict(self.task_id_counters),
            "pipeline_task_counters": dict(self.pipeline_task_counters),
            "schedules": {
                sid: asdict(s) for sid, s in self.schedules.items()
            },
            "user_dm_commands": user_dm_command_catalog(),
            "agent_message_loops": self.agent_message_loops_snapshot(),
            "auto_dispatch_queues": {
                group: [asdict(entry) for entry in entries]
                for group, entries in self.auto_dispatch_queues.items()
            },
            "panel_active": self.panel_active,
            "board_panel_height": self.board_panel_height,
            "active_group": self.active_group,
            "selected_principal_id": self.selected_principal_id,
            "selected_agent_id": self.selected_agent_id,
            "standalone_panel_layout": self.standalone_panel_layout,
            "detached_panels": self.detached_panels,
            "window_bounds": self.window_bounds,
            "workspace_sidebar_width": self.workspace_sidebar_width,
            "terminal_direct_messages_height": (
                self.terminal_direct_messages_height
            ),
            "terminal_compose_height": self.terminal_compose_height,
            "engineer_panel_split_fraction": self.engineer_panel_split_fraction,
            "context_panel_split_ratio": self.context_panel_split_ratio,
            "supervisor_panel_state": self.supervisor_panel_state,
            "events_dismissed_attention": self.events_dismissed_attention,
            "board_filters_by_group": self.board_filters_by_group,
            "board_selected_lanes_by_group": self.board_selected_lanes_by_group,
            "board_hidden_wide_lanes_by_group": self.board_hidden_wide_lanes_by_group,
            "board_saved_views_by_group": self.board_saved_views_by_group,
            "board_lane_sorts_by_group": self.board_lane_sorts_by_group,
            "board_card_density_by_group": self.board_card_density_by_group,
            "panel_events": self.panel_log.get_recent(50) if self.panel_log else [],
            "engineer_settings": {
                n: asdict(ws) for n, ws in self.engineer_settings.items()
            },
            "architect_settings": {
                n: asdict(self.get_architect_settings(n))
                for n in self.groups
            },
            "agent_digest_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_digest_settings.items()
            },
            "agent_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_settings.items()
            },
            "resolved_agent_settings": {
                agent_id: self.resolve_agent_settings(agent_id)
                for agent_id, cell in self.agents.items()
                if cell.kind in {"architect", "engineer"}
            },
            "agent_message_history": self.agent_message_history_snapshot(),
            "direct_messages_by_agent": self.direct_messages_snapshot(),
            "agent_peer_threads": self.agent_peer_threads_snapshot(),
            "operator_notices": self.operator_notices_snapshot(),
            "operator_notice_summary": self.operator_notice_summary(),
            "thinking": self.thinking_snapshot(),
            "idea_briefs": self.idea_brief_snapshot(),
            "behavior_overlay_proposals": behavior_overlay_proposals,
            "behavior_overlay_active": behavior_overlay_active,
            # Ephemeral daemon-global relay connection-state for the status bar.
            "relay_connection": dict(self.relay_connection),
            # Resolved relay config with per-field provenance for Settings.
            "relay_config": copy.deepcopy(self.relay_config),
        }

    # -- Targeted persistence helpers ----------------------------------------

    def begin_critical_write_capture(
        self,
        *,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        """Capture agent/task persistence for one critical idempotent write."""
        if self._current_critical_write_capture() is not None:
            raise RuntimeError("critical write capture is already active")
        self._critical_write_capture_var.set(CriticalWriteCapture(
            command_name=str(command_name or ""),
            idempotency_key=str(idempotency_key or ""),
            request_hash=str(request_hash or ""),
        ))

    def _current_critical_write_capture(self) -> CriticalWriteCapture | None:
        return self._critical_write_capture_var.get()

    def _critical_write_capture_agent(self, cell: AgentCell) -> bool:
        capture = self._current_critical_write_capture()
        if capture is None:
            return False
        capture.deleted_agents.discard(str(cell.id or ""))
        capture.agents[str(cell.id or "")] = copy.deepcopy(cell)
        return True

    def _critical_write_capture_delete_agent(self, agent_id: str) -> bool:
        capture = self._current_critical_write_capture()
        aid = str(agent_id or "").strip()
        if capture is None or not aid:
            return False
        capture.agents.pop(aid, None)
        capture.deleted_agents.add(aid)
        return True

    def _critical_write_capture_task(self, task: BoardTask) -> bool:
        capture = self._current_critical_write_capture()
        if capture is None:
            return False
        capture.deleted_tasks.discard(str(task.id or ""))
        capture.tasks[str(task.id or "")] = copy.deepcopy(task)
        return True

    def _critical_write_capture_delete_task(self, task_id: str) -> bool:
        capture = self._current_critical_write_capture()
        tid = str(task_id or "").strip()
        if capture is None or not tid:
            return False
        capture.tasks.pop(tid, None)
        capture.deleted_tasks.add(tid)
        return True

    def _critical_write_capture_task_id_counter(self, group_prefix: str) -> bool:
        capture = self._current_critical_write_capture()
        prefix = normalize_group_prefix(group_prefix)
        if capture is None or not prefix:
            return False
        capture.task_id_counters[prefix] = max(
            1,
            int(self.task_id_counters.get(prefix, 1) or 1),
        )
        return True

    def _critical_write_capture_pipeline_task_counter(self, root_task_id: str) -> bool:
        capture = self._current_critical_write_capture()
        root_id = str(root_task_id or "").strip()
        if capture is None or not root_id:
            return False
        capture.pipeline_task_counters[root_id] = max(
            1,
            int(self.pipeline_task_counters.get(root_id, 1) or 1),
        )
        return True

    def _critical_write_capture_task_id_alias(self, legacy_id: str) -> bool:
        capture = self._current_critical_write_capture()
        alias = str(legacy_id or "").strip()
        if capture is None or not alias:
            return False
        task_id = str(self.task_id_aliases.get(alias, "") or "").strip()
        if not task_id:
            return False
        capture.task_id_aliases[alias] = task_id
        return True

    def finalize_critical_write_capture(self, response, *,
                                        delete_failed_write_key: str = "",
                                        surface: str = "internal"):
        """Persist one captured critical write atomically with its receipt."""
        capture = self._current_critical_write_capture()
        self._critical_write_capture_var.set(None)
        if not capture or not self.db:
            return
        self.db.persist_command_capture(
            agents=capture.agents,
            deleted_agents=capture.deleted_agents,
            tasks=capture.tasks,
            deleted_tasks=capture.deleted_tasks,
            task_id_counters=capture.task_id_counters,
            pipeline_task_counters=capture.pipeline_task_counters,
            task_id_aliases=capture.task_id_aliases,
            idempotency_key=capture.idempotency_key,
            surface=surface,
            command_name=capture.command_name,
            request_hash=capture.request_hash,
            response=response,
            delete_failed_write_key=delete_failed_write_key,
        )

    def clear_critical_write_capture(self) -> None:
        self._critical_write_capture_var.set(None)

    def _db_save_agent(self, cell: AgentCell):
        """Persist a single agent to SQLite."""
        if self.db:
            meter = self.metrics_collector
            started = time.perf_counter() if meter.enabled else 0.0
            recorded = False
            try:
                if self._critical_write_capture_agent(cell):
                    return
                self.db.save_agent_deferred(cell)
                if meter.enabled:
                    meter.record_db_write(
                        latency_ms=(time.perf_counter() - started) * 1000.0
                    )
                    recorded = True
            except Exception:
                log.exception("Failed to save agent %s", cell.id)
            finally:
                # Critical-write captures still represent a DB-bound mutation,
                # but they bypass save_agent_deferred. Count them separately so
                # write-rate telemetry does not disappear during idempotent
                # command flows.
                if meter.enabled and started and not recorded:
                    meter.record_db_write(
                        latency_ms=(time.perf_counter() - started) * 1000.0
                    )

    def _db_delete_agent(self, agent_id: str):
        if self.db:
            try:
                if self._critical_write_capture_delete_agent(agent_id):
                    return
                self.db.delete_agent(agent_id)
            except Exception:
                log.exception("Failed to delete agent %s", agent_id)

    def mark_agent_progress(self, cell_or_id, timestamp: float | None = None,
                            *, emit: bool = True,
                            persist: bool = True) -> bool:
        """Mark a cell's real-progress clock without touching callers' state."""
        cell = self.agents.get(cell_or_id) if isinstance(cell_or_id, str) \
            else cell_or_id
        if not cell:
            return False
        changed = cell.mark_progress(timestamp)
        if changed:
            if emit:
                self._emit_agent(cell)
            if persist:
                self._db_save_agent(cell)
        return changed

    def snapshot_agent_optimistic_state(self, cell_or_id) -> dict:
        """Capture the fields an optimistic Running mark may need to restore."""
        cell = self.agents.get(cell_or_id) if isinstance(cell_or_id, str) \
            else cell_or_id
        if not cell:
            return {}
        return {
            "status": str(getattr(cell, "status", "") or ""),
            "last_progress_at": _safe_float(getattr(
                cell, "last_progress_at", 0) or 0),
            "last_heartbeat_at": _safe_float(getattr(
                cell, "last_heartbeat_at", 0) or 0),
            "last_activity_at": _safe_float(getattr(
                cell, "last_activity_at", 0) or 0),
            "last_event_at": _safe_float(getattr(
                cell, "last_event_at", 0) or 0),
        }

    def restore_agent_optimistic_state(self, cell_or_id, snapshot: dict,
                                       *, emit: bool = True,
                                       persist: bool = False) -> bool:
        """Restore a cell to a pre-optimistic-send status/clock snapshot."""
        cell = self.agents.get(cell_or_id) if isinstance(cell_or_id, str) \
            else cell_or_id
        if not cell or not snapshot:
            return False
        before = self.snapshot_agent_optimistic_state(cell)
        cell.status = str(snapshot.get("status", "") or "idle")
        cell.last_progress_at = _safe_float(snapshot.get(
            "last_progress_at", 0) or 0)
        cell.last_heartbeat_at = _safe_float(snapshot.get(
            "last_heartbeat_at", 0) or 0)
        cell.last_activity_at = _safe_float(snapshot.get(
            "last_activity_at", 0) or 0)
        cell.last_event_at = _safe_float(snapshot.get(
            "last_event_at", 0) or 0)
        changed = before != self.snapshot_agent_optimistic_state(cell)
        if changed:
            if emit:
                self._emit_agent(cell)
            if persist:
                self._db_save_agent(cell)
        return changed

    def mark_agent_optimistic_running(
            self, cell_or_id, timestamp: float | None = None, *,
            emit: bool = True, persist: bool = False) -> bool:
        """Mark a live prompt target Running at enqueue/send time.

        A user or dispatcher sending text is the start edge of an agent turn,
        even before adapter hooks report the first real activity event.  This
        helper keeps that optimistic transition server-side so all clients see
        the same status delta, while normal session/activity events still
        reconcile the cell afterward.
        """
        cell = self.agents.get(cell_or_id) if isinstance(cell_or_id, str) \
            else cell_or_id
        if not cell:
            return False
        status_changed = cell.status != "running"
        if status_changed:
            cell.status = "running"
        progress_changed = cell.mark_progress(timestamp)
        changed = status_changed or progress_changed
        if changed:
            if emit:
                self._emit_agent(cell)
            if persist:
                self._db_save_agent(cell)
        return changed

    def mark_agent_heartbeat(self, cell_or_id, timestamp: float | None = None,
                             *, emit: bool = True,
                             persist: bool = False) -> bool:
        """Mark a cell's passive heartbeat clock without advancing progress."""
        cell = self.agents.get(cell_or_id) if isinstance(cell_or_id, str) \
            else cell_or_id
        if not cell:
            return False
        changed = cell.mark_heartbeat(timestamp)
        if changed:
            if emit:
                self._emit_agent(cell)
            if persist:
                self._db_save_agent(cell)
        return changed

    def _db_save_groups(self):
        """Persist all groups and their memberships."""
        if self.db:
            try:
                self.db.save_groups_and_members_deferred(
                    self.groups,
                    self.group_slugs,
                )
            except Exception:
                log.exception("Failed to save groups")

    def _db_save_group_settings(self, name: str):
        if self.db and name in self.group_settings:
            try:
                self.db.defer_write(
                    "group_settings",
                    "save_group_settings",
                    name,
                    self.group_settings[name],
                )
            except Exception:
                log.exception("Failed to save group settings for %s", name)

    def _db_delete_group(self, name: str):
        if self.db:
            try:
                self.db.delete_group(name)
            except Exception:
                log.exception("Failed to delete group %s", name)

    def _db_save_task(self, task: BoardTask):
        # Single chokepoint for the rolling activity-log bound: every append
        # site is followed by a save, so clamping here keeps the in-memory
        # list, the persisted blob, and the content hash consistent.
        clamp_task_messages(task)
        # Defensive invariant for direct mutation paths that bypass board_update_task.
        task.task_content_hash = compute_task_content_hash(task)
        if self.db:
            try:
                if self._critical_write_capture_task(task):
                    return
                self.db.save_board_task_deferred(task)
            except Exception:
                log.exception("Failed to save task %s", task.id)

    def _db_delete_task(self, task_id: str):
        if self.db:
            try:
                if self._critical_write_capture_delete_task(task_id):
                    return
                self.db.delete_board_task(task_id)
            except Exception:
                log.exception("Failed to delete task %s", task_id)

    def _db_save_task_id_counter(self, group_prefix: str):
        if self.db:
            try:
                if self._critical_write_capture_task_id_counter(group_prefix):
                    return
                self.db.defer_write(
                    "task_id_counters",
                    "save_task_id_counter",
                    group_prefix,
                    self.task_id_counters.get(group_prefix, 1),
                )
            except Exception:
                log.exception("Failed to save task ID counter %s", group_prefix)

    def _db_save_pipeline_task_counter(self, root_task_id: str):
        if self.db:
            try:
                if self._critical_write_capture_pipeline_task_counter(root_task_id):
                    return
                self.db.defer_write(
                    "pipeline_task_counters",
                    "save_pipeline_task_counter",
                    root_task_id,
                    self.pipeline_task_counters.get(root_task_id, 1),
                )
            except Exception:
                log.exception("Failed to save pipeline counter %s", root_task_id)

    def _db_save_task_id_alias(self, legacy_id: str):
        if self.db:
            try:
                task_id = self.task_id_aliases.get(legacy_id, "")
                if task_id:
                    task = self.board_tasks.get(self.resolve_task_alias(task_id))
                    if task:
                        self._db_save_task(task)
                    if self._critical_write_capture_task_id_alias(legacy_id):
                        return
                    self.db.defer_write(
                        "task_id_aliases",
                        "save_task_id_alias",
                        legacy_id,
                        task_id,
                    )
            except Exception:
                log.exception("Failed to save task ID alias %s", legacy_id)

    def _db_save_schedule(self, sched: Schedule):
        if self.db:
            try:
                self.db.defer_write("schedules", "save_schedule", sched)
            except Exception:
                log.exception("Failed to save schedule %s", sched.id)

    def _db_delete_schedule(self, sid: str):
        if self.db:
            try:
                self.db.delete_schedule(sid)
            except Exception:
                log.exception("Failed to delete schedule %s", sid)

    def _db_save_agent_message_loop(self, loop: AgentMessageLoop):
        if self.db:
            try:
                self.db.defer_write(
                    "agent_message_loops",
                    "save_agent_message_loop",
                    loop,
                )
            except Exception:
                log.exception("Failed to save agent message loop %s", loop.id)

    def _db_save_lanes(self):
        if self.db:
            try:
                self.db.defer_write(
                    "board_lanes",
                    "save_board_lanes",
                    self.board_lanes,
                )
            except Exception:
                log.exception("Failed to save board lanes")

    def _db_save_ui(self, key: str, value):
        if self.db:
            try:
                self.db.defer_write("ui_state", "save_ui_state", key, value)
            except Exception:
                log.exception("Failed to save UI state %s", key)

    def _db_save_global_settings(self):
        if self.db:
            try:
                self.db.defer_write(
                    "global_settings",
                    "save_global_settings",
                    self.global_settings,
                )
            except Exception:
                log.exception("Failed to save global settings")

    def _db_save_auto_dispatch_queue(self, group: str):
        if self.db:
            try:
                self.db.defer_write(
                    "auto_dispatch_queue",
                    "save_auto_dispatch_queue",
                    group,
                    self.auto_dispatch_queues.get(group, []),
                )
            except Exception:
                log.exception("Failed to save auto-dispatch queue for %s",
                              group)

    def _db_delete_auto_dispatch_queue(self, group: str):
        if self.db:
            try:
                self.db.delete_auto_dispatch_queue(group)
            except Exception:
                log.exception("Failed to delete auto-dispatch queue for %s",
                              group)

    async def flush_db_writes(self) -> None:
        """Wait for queued async SQLite writes to finish."""
        if self.db:
            await self.db.flush_async_writes()

    def auto_dispatch_queue_find(self, task_id: str):
        for group, entries in self.auto_dispatch_queues.items():
            for idx, entry in enumerate(entries):
                if entry.task_id == task_id:
                    return group, idx, entry
        return "", -1, None

    def auto_dispatch_queue_contains(self, task_id: str) -> bool:
        group, idx, _entry = self.auto_dispatch_queue_find(task_id)
        return bool(group and idx >= 0)

    def auto_dispatch_queue_raise_max_concurrent(
            self, group: str, task_id: str, max_concurrent: int):
        """Raise a queued task's cap without lowering or reordering it.

        Returns ``(entry, changed)`` when the task is already queued in
        ``group``; returns ``(None, False)`` when it is not.  Retried
        batch dispatches use this to refresh stale deferred entries after
        the engineer intentionally raises the engineer-group capacity cap.
        """
        found_group, _idx, entry = self.auto_dispatch_queue_find(task_id)
        if not entry or found_group != group:
            return None, False
        requested = max(1, int(max_concurrent or 1))
        if requested <= entry.max_concurrent:
            return entry, False
        entry.max_concurrent = requested
        self._db_save_auto_dispatch_queue(group)
        return entry, True

    def auto_dispatch_queue_update_dispatch_intent(
            self, group: str, task_id: str, *,
            agent_group: Optional[str] = None,
            target_agent_id: Optional[str] = None,
            engineer_owner_id: Optional[str] = None,
            provider: Optional[str] = None):
        """Update queued dispatch metadata in place without reordering.

        Batch dispatch can revisit tasks that were already persisted in the
        auto-dispatch queue (for example after an engineer arms actions before
        issuing the warm-cluster batch).  Preserve their queue position but
        refresh the grouping/ownership hints so the pump promotes them with
        the engineer's latest dispatch intent instead of treating them as
        independent stale entries.
        """
        found_group, _idx, entry = self.auto_dispatch_queue_find(task_id)
        if not entry or found_group != group:
            return None, False

        changed = False

        def _set_attr(name: str, value: Optional[str], *,
                      only_non_empty: bool = False) -> None:
            nonlocal changed
            if value is None:
                return
            cleaned = str(value or "").strip()
            if only_non_empty and not cleaned:
                return
            if getattr(entry, name) == cleaned:
                return
            setattr(entry, name, cleaned)
            changed = True

        _set_attr("agent_group", agent_group)
        _set_attr("target_agent_id", target_agent_id)
        _set_attr("engineer_owner_id", engineer_owner_id, only_non_empty=True)
        _set_attr("provider", provider, only_non_empty=True)

        if changed:
            self._db_save_auto_dispatch_queue(group)
        return entry, changed

    def auto_dispatch_queue_add(self, group: str, task_id: str, *,
                                agent_group: str = "",
                                max_concurrent: int = 1,
                                target_agent_id: str = "",
                                engineer_owner_id: str = "",
                                provider: str = ""):
        found_group, _idx, entry = self.auto_dispatch_queue_find(task_id)
        if entry:
            if found_group != group:
                self.auto_dispatch_queue_remove_task(task_id)
            else:
                return entry
        from datetime import datetime, timezone
        queue = self.auto_dispatch_queues.setdefault(group, [])
        entry = AutoDispatchQueueEntry(
            task_id=task_id,
            agent_group=agent_group.strip(),
            max_concurrent=max(1, int(max_concurrent or 1)),
            target_agent_id=target_agent_id.strip(),
            engineer_owner_id=str(engineer_owner_id or "").strip(),
            provider=str(provider or "").strip(),
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )
        queue.append(entry)
        self._db_save_auto_dispatch_queue(group)
        return entry

    def auto_dispatch_queue_remove_task(self, task_id: str) -> bool:
        group, idx, _entry = self.auto_dispatch_queue_find(task_id)
        if not group or idx < 0:
            return False
        queue = self.auto_dispatch_queues.get(group, [])
        queue.pop(idx)
        if queue:
            self._db_save_auto_dispatch_queue(group)
        else:
            self.auto_dispatch_queues.pop(group, None)
            self._db_delete_auto_dispatch_queue(group)
        return True

    def auto_dispatch_queue_bind_agent_group(self, group: str,
                                             agent_group: str,
                                             agent_id: str) -> int:
        if not group or not agent_group or not agent_id:
            return 0
        queue = self.auto_dispatch_queues.get(group, [])
        changed = 0
        for entry in queue:
            if entry.agent_group != agent_group:
                continue
            if entry.target_agent_id == agent_id:
                continue
            entry.target_agent_id = agent_id
            changed += 1
        if changed:
            self._db_save_auto_dispatch_queue(group)
        return changed

    def cleanup_stale_auto_dispatch_queue(self) -> int:
        removed = 0
        for group in list(self.auto_dispatch_queues):
            queue = self.auto_dispatch_queues.get(group, [])
            keep = []
            changed = False
            for entry in queue:
                task = self.board_tasks.get(entry.task_id)
                if (
                    not task
                    or task.group != group
                    or board_task_is_closed(task)
                    or (
                        task.agent_id
                        and task.agent_id != entry.target_agent_id
                    )
                ):
                    removed += 1
                    changed = True
                    continue
                keep.append(entry)
            if keep:
                if changed or len(keep) != len(queue):
                    self.auto_dispatch_queues[group] = keep
                    self._db_save_auto_dispatch_queue(group)
            else:
                self.auto_dispatch_queues.pop(group, None)
                self._db_delete_auto_dispatch_queue(group)
        return removed

    def cleanup_stale_boundary_successors(self, emit: bool = True) -> int:
        updated = clear_stale_successor_references(
            self.board_tasks.values()
        )
        for task in updated:
            if emit:
                self.emit_task_upsert(task)
            self._db_save_task(task)
        if updated and emit:
            self.recompute_task_health()
        return len(updated)

    # -- Peer message cache helpers -----------------------------------------









    # -- Agent peer thread aggregate helpers --------------------------------








    # -- Direct message cache helpers ---------------------------------------











    # -- Agent history helpers -----------------------------------------------















    # -- Group settings -----------------------------------------------------









    # -- Engineer settings & journal ------------------------------------------




























    # -- Initiative read/write compatibility facade -------------------------













    # -- Planning Area read/write helpers ----------------------------------

















    # -- Thinking read/write helpers ---------------------------------------































    # -- Idea Brief helpers ------------------------------------------------



























    # -- Dynamic Behavior overlays ------------------------------------------
































    # -- Architect journal and Engineer worklog -----------------------------








    # -- Global settings ----------------------------------------------------







    # -- Slug helpers -------------------------------------------------------
















    # -- Mutations ----------------------------------------------------------

























    # -- Board (Phase 5) ---------------------------------------------------


    # -- User direct-message loop CRUD --------------------------------------







    # -- Schedule CRUD ------------------------------------------------------






    # -- WebSocket broadcast ------------------------------------------------
