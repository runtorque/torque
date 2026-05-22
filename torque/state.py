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
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

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
from .db import TorqueDB
from .engineer_ask_events import (
    ENGINEER_ASK_RESOLVED,
    ENGINEER_AWAITING_HUMAN_INPUT,
    emit_engineer_ask_resolved_event,
    emit_engineer_awaiting_human_input_event,
)
from .task_ids import (
    format_derived_task_id,
    format_root_task_id,
    is_canonical_task_id,
    normalize_group_prefix,
    parse_task_id,
)
from .worktree_boundaries import clear_stale_successor_references

ARCHIVED_LANE = "Archived"
_RESERVED_LANES = ("Backlog", "To Do", "In Progress", "Done", ARCHIVED_LANE)
_DEFAULT_LANES = list(_RESERVED_LANES)
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
_ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS = [
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
    ENGINEER_AWAITING_HUMAN_INPUT,
    ENGINEER_ASK_RESOLVED,
]
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
_ENGINEER_WORKLOG_LIMIT = 200
_ENGINEER_STREAM_CARD_LIMIT = 10
_ENGINEER_STREAM_CONTEXT_LIMIT = 5
_SYSTEM_HEALTH_WINDOWS = {
    "24h": (24 * 3600, 3600),
    "7d": (7 * 86400, 86400),
    "30d": (30 * 86400, 86400),
}
_SYSTEM_HEALTH_EVENT_KINDS = {
    "task_dispatched",
    "task_queued",
    "task_auto_dispatched",
    "worker_boot_doa",
    "engineer_queue_empty",
}
_SYSTEM_HEALTH_AGE_BUCKETS = (
    ("<1h", 0, 3600),
    ("1-4h", 3600, 4 * 3600),
    ("4-24h", 4 * 3600, 24 * 3600),
    ("1-3d", 24 * 3600, 3 * 86400),
    ("3-7d", 3 * 86400, 7 * 86400),
    ("7d+", 7 * 86400, None),
)
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
    "parent_task_id",
    "pipeline_depth",
    "status",
    "created_at",
    "updated_at",
    "scheduled_at",
    "depends_on",
    "provider",
    "external_id",
    "external_url",
    "board_sync",
    "health_state",
    "health_since",
    "health_details",
    "verification_state",
    "verification_mode",
    "verification_notes",
    "verification_summary",
    "messages",
    "messages_thread",
    "lane_entered_at",
    "worktree_boundary",
    "resume_after_boundary_task_id",
    "deliverable_required",
    "deliverable_type",
    "requires_review",
    "pre_approved_by",
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


def merge_cleanup_flags(mode: str) -> tuple[bool, bool]:
    mode = normalize_worktree_merge_cleanup(mode)
    if mode == "close":
        return (True, False)
    if mode == "remove":
        return (False, True)
    if mode == "close_remove":
        return (True, True)
    return (False, False)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _parse_health_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value) if value else 0.0
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


def _health_bucket_index(ts: float, since: float, bucket_seconds: int,
                         bucket_count: int) -> int:
    try:
        ts = float(ts or 0.0)
    except (TypeError, ValueError):
        return -1
    if ts < since:
        return -1
    idx = int((ts - since) // bucket_seconds)
    if idx < 0:
        return -1
    return min(idx, bucket_count - 1)


def _health_percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v or 0.0) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    q = max(0.0, min(1.0, float(quantile or 0.0)))
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + ((ordered[hi] - ordered[lo]) * frac)


def _health_series(bucket_count: int, fill=0):
    return [copy.deepcopy(fill) for _ in range(bucket_count)]


def _health_bucket_label(start: float, bucket_seconds: int) -> str:
    fmt = "%m-%d %H:%M" if bucket_seconds < 86400 else "%m-%d"
    return datetime.fromtimestamp(start, timezone.utc).strftime(fmt)


def _health_task_value(task, name: str, default=None):
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def _health_task_group(task) -> str:
    return str(
        _health_task_value(task, "group", None)
        or _health_task_value(task, "group_name", "")
        or ""
    ).strip()


def _health_action_name(task) -> str:
    return str(_health_task_value(task, "action_name", "") or "").strip().lower()


def _health_task_id(task) -> str:
    return str(_health_task_value(task, "id", "") or "").strip()


def _health_response_task_ids(response: dict) -> list[str]:
    if not isinstance(response, dict):
        return []
    ids = []
    direct = str(response.get("task_id", "") or "").strip()
    if direct:
        ids.append(direct)
    results = response.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("task_id", "") or "").strip()
            if tid:
                ids.append(tid)
    return ids


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
    terminal_backend: str = "iterm2"  # "iterm2" | "pty" (future backends)
    session_id: Optional[str] = None
    profile: str = "Default"
    command: str = ""
    directory: str = ""  # working dir on create/relaunch
    tab_color: str = ""  # hex color for iTerm2 tab (e.g. "#f85149")
    icon: str = ""  # custom icon character (from AGENT_ICONS set)
    template: str = ""  # template used to create this agent
    window_id: str = ""  # iTerm2 window this session lives in
    parent_id: str = ""  # for child terminals: the owning agent's ID
    status: str = "stopped"  # idle | running | error | stopped
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


def _safe_journal_filename(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return token or "architect"


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
        and "architect" in {sender_kind, recipient_kind}
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
    sender_kind = str((row or {}).get("sender_kind", "") or "").strip()
    recipient_kind = str((row or {}).get("recipient_kind", "") or "").strip()
    message_type = str((row or {}).get("message_type", "message") or "message").strip()
    blocking = bool((row or {}).get("blocking", False))
    return (
        sender_kind == "user"
        or recipient_kind == "user"
        or message_type != "message"
        or blocking
    )


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
        "architect_peer_message",
        "architect_peer_reply",
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


def _normalize_verification_summary(summary) -> dict:
    if not isinstance(summary, dict):
        return {}
    out = {}
    text_keys = ("tests_run", "human_validation_pending")
    for key in text_keys:
        value = summary.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value:
            out[key] = value
    for key in ("manual_smoke_done", "deploy_needed", "deploy_attempted"):
        if key in summary:
            out[key] = bool(summary.get(key))
    return out


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
        else:
            value = getattr(task, field_name)
        if isinstance(value, dict):
            value = dict(value)
        elif isinstance(value, list):
            value = list(value)
        summary[field_name] = value
    return summary


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
    return out


@dataclass
class GroupSettings:
    """Default settings applied when creating agents/terminals in a group."""
    # Group-level defaults
    default_directory: str = ""
    default_terminal_backend: str = "iterm2"
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
    agent_profile: str = ""
    agent_shell: str = ""
    agent_tab_color: str = ""
    agent_env_vars: dict[str, str] = field(default_factory=dict)
    agent_env_file: str = ""
    default_agent_template: str = ""
    agent_provider: str = ""  # adapter name ("claude-code", "codex", etc.) — empty = use default
    agent_boot_command: str = ""  # override default boot command (e.g. "codex")
    agent_model: str = ""  # default model override when provider supports it
    agent_reasoning_effort: str = ""  # default reasoning-effort override
    git_worktree: bool = False
    worktree_base_dir: str = ".torque/worktrees"  # directory for worktrees (relative to repo)
    worktree_base_branch: str = ""  # branch to fork from (empty = current HEAD)
    worktree_auto_checkpoint: bool = True  # auto-checkpoint on agent stop
    checkpoint_on_progress: bool = False  # auto-checkpoint on torque ai progress/done
    worktree_merge_squash: bool = False  # squash commits when merging to main
    worktree_merge_instructions: str = ""  # additional instructions appended to merge prompt
    worktree_merge_cleanup: str = "keep"  # keep | close | remove | close_remove
    worktree_merge_preserve_diff: bool = False  # save the pre-merge patch on the latest boundary task
    engineer_merge_mode: str = "pr"  # pr | direct | engineer-choice
    worktree_symlinks: list[str] = field(default_factory=list)  # repo-relative paths or glob patterns to symlink from repo root
    worktree_symlink_gitignored_paths: bool = False  # symlink gitignored files/dirs from repo root into worktrees
    agent_session_resume: bool = True  # resume session on relaunch
    agent_idle_timeout: int = 0  # minutes before flagging agent as stuck (0=disable)
    guidance_hint_cadence: int = _DEFAULT_GUIDANCE_HINT_CADENCE  # 0=every time; otherwise 1st, then every N
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
class ArchitectSettings:
    """Per-group architect configuration."""
    group: str = ""
    architect_boot_command: str = ""
    architect_provider: str = ""
    architect_model: str = ""
    architect_reasoning_effort: str = ""
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


# Mandatory events — always included in engineer digests regardless of enabled_events.
ENGINEER_MANDATORY_EVENTS = frozenset({
    "task_completed", "agent_reply", "agent_error",
    "agent_blocked", "ask_created", "task_verification_updated",
    "worker_boot_doa",
})


@dataclass
class GlobalSettings:
    """App-wide settings for the Torque instance."""
    # General > Server
    default_command: str = ""        # empty = use config.DEFAULT_COMMAND (env var fallback)
    filter_by_window: bool = True    # global default for window filtering
    focus_new_tabs: bool = True      # switch focus to newly created tabs
    focus_on_click: bool = False     # single click also focuses the iTerm2 tab
    xterm_scrollback: int = XTERM_SCROLLBACK_DEFAULT  # embedded xterm.js history lines
    # General > Board
    default_lanes: list[str] = field(default_factory=lambda: list(_DEFAULT_LANES))
    # Keybindings — action name → {modifiers, keycode, character} overrides
    keybindings: dict[str, dict] = field(default_factory=dict)
    # Pipeline
    max_pipeline_depth: int = 10  # 0 = unlimited
    # Events
    max_event_log: int = 500  # max persisted panel events
    event_ingest_max_rows: int = 100_000
    event_ingest_max_days: int = 14
    mcp_call_log_args_capture: str = "metadata"  # off | metadata | full
    mcp_call_log_full_capture_tools: list[str] = field(default_factory=list)
    # Boot nudges — fallback kickoff text used when an architect/engineer
    # role has no initial_prompt configured. Workers/terminals are
    # unaffected (they don't receive a default nudge). Empty string disables
    # the nudge for that kind.
    architect_default_boot_nudge: str = DEFAULT_ARCHITECT_BOOT_NUDGE
    engineer_default_boot_nudge: str = DEFAULT_ENGINEER_BOOT_NUDGE

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
        self.mcp_call_log_args_capture = normalize_mcp_call_log_args_capture(
            self.mcp_call_log_args_capture
        )
        self.mcp_call_log_full_capture_tools = (
            normalize_mcp_call_log_full_capture_tools(
                self.mcp_call_log_full_capture_tools
            )
        )


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self, db: Optional[TorqueDB] = None):
        self.db: Optional[TorqueDB] = db
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
        self._ws_clients_lock = asyncio.Lock()
        # Global settings
        self.global_settings: GlobalSettings = GlobalSettings()
        # Board (Phase 5)
        self.board_lanes: list[str] = list(_DEFAULT_LANES)
        self.board_tasks: dict[str, BoardTask] = {}
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
        self.task_id_aliases: dict[str, str] = {}
        self.task_id_counters: dict[str, int] = {}
        self.pipeline_task_counters: dict[str, int] = {}
        self.schedules: dict[str, Schedule] = {}
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
        self._critical_write_capture_var: contextvars.ContextVar[
            CriticalWriteCapture | None
        ] = contextvars.ContextVar(
            f"matrix_state_critical_write_capture_{id(self)}",
            default=None,
        )

    @staticmethod
    def agent_is_tombstoned(cell) -> bool:
        """Return True when ``cell`` is inside the soft-delete window."""
        return bool(cell and _safe_float(getattr(cell, "deleted_at", 0.0)) > 0)

    def iter_agents(self, *, include_tombstoned: bool = False):
        """Iterate cells, excluding tombstones unless explicitly requested."""
        for cell in self.agents.values():
            if not include_tombstoned and self.agent_is_tombstoned(cell):
                continue
            yield cell

    def iter_active_agents(self):
        """Iterate non-tombstoned cells."""
        return self.iter_agents(include_tombstoned=False)

    def get_active_agent(self, agent_id: str) -> Optional[AgentCell]:
        """Return a non-tombstoned cell by id, or None."""
        cell = self.agents.get(str(agent_id or "").strip())
        if self.agent_is_tombstoned(cell):
            return None
        return cell

    def system_health_metrics(
        self,
        window: str = "24h",
        group: str = "",
        *,
        now: float | None = None,
    ) -> dict:
        """Return read-only orchestration health metrics for the dock panel."""
        window = str(window or "24h").strip()
        if window not in _SYSTEM_HEALTH_WINDOWS:
            raise ValueError("window must be one of: 24h, 7d, 30d")
        window_seconds, bucket_seconds = _SYSTEM_HEALTH_WINDOWS[window]
        until = float(time.time() if now is None else now)
        since = until - window_seconds
        bucket_count = int(window_seconds // bucket_seconds)
        group = str(group or "").strip()
        scope = "group" if group else "all_groups"
        buckets = [
            {
                "start": since + (idx * bucket_seconds),
                "end": since + ((idx + 1) * bucket_seconds),
                "label": _health_bucket_label(
                    since + (idx * bucket_seconds),
                    bucket_seconds,
                ),
            }
            for idx in range(bucket_count)
        ]
        notes = [
            (
                "Task age is a current lane-age distribution; V1 does not "
                "persist historical lane-transition samples."
            ),
            (
                "Merge latency is boundary recorded_at→merged_at; durable "
                "PR-created timestamps are unavailable in V1."
            ),
            "Dispatch shape is coverage-limited to MCP idempotency rows.",
            (
                "Engineer utilization is worker busy-time divided by current "
                "engineer concurrency capacity."
            ),
        ]
        if group and group not in self.groups:
            notes.append(
                f"Group '{group}' is not currently present; returning any "
                "matching historical rows plus empty current-state metrics."
            )

        events: list[dict] = []
        agent_tasks: list[dict] = []
        mcp_calls: list[dict] = []
        if self.db:
            events = self.db.load_panel_events_window(
                since,
                until,
                group=group,
                kinds=sorted(_SYSTEM_HEALTH_EVENT_KINDS),
            )
            agent_tasks = self.db.load_agent_tasks_window(
                since,
                until,
                group=group,
            )
            mcp_calls = self.db.load_mcp_dispatch_calls_window(
                since,
                until,
                group=group,
            )
        elif self.panel_log:
            recent = self.panel_log.get_recent(
                getattr(self.panel_log, "_max_size", 500)
            )
            events = [
                evt for evt in recent
                if str(evt.get("kind", "") or "") in _SYSTEM_HEALTH_EVENT_KINDS
                and since <= _safe_float(evt.get("timestamp", 0.0)) <= until
                and (not group or str(evt.get("group", "") or "") == group)
            ]
            notes.append(
                "Panel event metrics are limited to the in-memory recent "
                "event ring because no SQLite database is attached."
            )
        else:
            notes.append("No SQLite database or panel event log is attached.")

        tasks = [
            task for task in self.board_tasks.values()
            if not group or _health_task_group(task) == group
        ]
        task_groups_by_id = {
            _health_task_id(task): _health_task_group(task)
            for task in self.board_tasks.values()
        }

        series = {
            "dispatches": _health_series(bucket_count, 0),
            "task_queued": _health_series(bucket_count, 0),
            "task_auto_dispatched": _health_series(bucket_count, 0),
            "worker_boot_doa": _health_series(bucket_count, 0),
            "engineer_queue_empty": _health_series(bucket_count, 0),
            "reviews": _health_series(bucket_count, 0),
            "merges": _health_series(bucket_count, 0),
            "busy_seconds": _health_series(bucket_count, 0.0),
            "capacity_seconds": _health_series(bucket_count, 0.0),
            "utilization_pct": _health_series(bucket_count, 0.0),
        }
        event_counts = {
            "task_dispatched": 0,
            "task_queued": 0,
            "task_auto_dispatched": 0,
            "worker_boot_doa": 0,
            "engineer_queue_empty": 0,
        }
        for evt in events:
            kind = str(evt.get("kind", "") or "")
            if kind not in event_counts:
                continue
            idx = _health_bucket_index(
                _safe_float(evt.get("timestamp", 0.0)),
                since,
                bucket_seconds,
                bucket_count,
            )
            if idx < 0:
                continue
            event_counts[kind] += 1
            if kind == "task_dispatched":
                series["dispatches"][idx] += 1
            else:
                series[kind][idx] += 1

        shape = {
            "serial_tool_calls": 0,
            "batch_tool_calls": 0,
            "batch_entries": 0,
            "statuses": {},
            "unscoped_tool_calls": 0,
            "scoped_tool_calls": 0,
        }
        for call in mcp_calls:
            response = call.get("response") if isinstance(call, dict) else {}
            if not isinstance(response, dict):
                response = {}
            call_group = str(call.get("group", "") or "").strip()
            if not call_group:
                response_groups = {
                    task_groups_by_id.get(tid, "")
                    for tid in _health_response_task_ids(response)
                }
                response_groups = {g for g in response_groups if g}
                if len(response_groups) == 1:
                    call_group = next(iter(response_groups))
            if group and call_group and call_group != group:
                continue
            if call_group:
                shape["scoped_tool_calls"] += 1
            else:
                shape["unscoped_tool_calls"] += 1
            tool_name = str(call.get("tool_name", "") or "")
            if tool_name.endswith("engineer_task_dispatch"):
                shape["serial_tool_calls"] += 1
                status = str(
                    response.get("status", "")
                    or response.get("type", "")
                    or ""
                )
                if status:
                    shape["statuses"][status] = (
                        shape["statuses"].get(status, 0) + 1
                    )
            elif tool_name.endswith("engineer_batch_dispatch"):
                shape["batch_tool_calls"] += 1
                results = response.get("results")
                if isinstance(results, list):
                    shape["batch_entries"] += len(results)
                    for item in results:
                        if not isinstance(item, dict):
                            continue
                        status = str(item.get("status", "") or "unknown")
                        shape["statuses"][status] = (
                            shape["statuses"].get(status, 0) + 1
                        )
        dispatch_tool_entries = (
            shape["serial_tool_calls"] + shape["batch_entries"]
        )
        dispatch_tool_calls = (
            shape["serial_tool_calls"] + shape["batch_tool_calls"]
        )
        shape_partial = bool(
            shape["unscoped_tool_calls"]
            or dispatch_tool_entries != event_counts["task_dispatched"]
        )
        if not mcp_calls and event_counts["task_dispatched"]:
            notes.append(
                "No MCP idempotency rows were found for dispatch shape in "
                "this window."
            )

        review_summary, review_distribution = self._system_health_reviews(
            tasks,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            bucket_count=bucket_count,
            review_series=series["reviews"],
        )
        merge_summary = self._system_health_merges(
            tasks,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            bucket_count=bucket_count,
            merge_series=series["merges"],
            window_seconds=window_seconds,
        )
        task_age = self._system_health_task_age(tasks, now=until)
        utilization = self._system_health_utilization(
            agent_tasks,
            group=group,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            buckets=buckets,
            busy_series=series["busy_seconds"],
            capacity_series=series["capacity_seconds"],
            utilization_series=series["utilization_pct"],
        )
        utilization["queue_empty_count"] = event_counts["engineer_queue_empty"]
        if utilization["capacity_seconds"] <= 0 and utilization["busy_seconds"] > 0:
            notes.append(
                "Worker busy-time exists but current engineer capacity is "
                "zero for this scope, so utilization percentage is unavailable."
            )

        dispatch_count = event_counts["task_dispatched"]
        window_hours = max(window_seconds / 3600.0, 1e-9)
        doa_count = event_counts["worker_boot_doa"]
        doa_rate = (doa_count / dispatch_count) if dispatch_count else 0.0

        return {
            "type": "system_health_metrics",
            "generated_at": until,
            "window": window,
            "group": group,
            "scope": scope,
            "since": since,
            "until": until,
            "bucket_seconds": bucket_seconds,
            "buckets": buckets,
            "summary": {
                "dispatch": {
                    "count": dispatch_count,
                    "workers_per_hour": dispatch_count / window_hours,
                    "queued_count": event_counts["task_queued"],
                    "autoresume_count": event_counts["task_auto_dispatched"],
                },
                "dispatch_shape": shape,
                "review_cycles": review_summary,
                "merge": merge_summary,
                "task_age": {
                    "lanes": len(task_age),
                    "tasks": sum(v["count"] for v in task_age.values()),
                },
                "worker_boot_doa": {
                    "count": doa_count,
                    "denominator": dispatch_count,
                    "rate": doa_rate,
                },
                "utilization": utilization,
            },
            "series": series,
            "distributions": {
                "dispatch_shape_statuses": shape["statuses"],
                "review_cycles": review_distribution,
                "task_age_by_lane": task_age,
            },
            "coverage": {
                "dispatch_shape": {
                    "available": bool(mcp_calls),
                    "partial": shape_partial,
                    "dispatch_events": dispatch_count,
                    "dispatch_tool_calls": dispatch_tool_calls,
                    "dispatch_tool_entries": dispatch_tool_entries,
                    "unscoped_tool_calls": shape["unscoped_tool_calls"],
                    "scoped_tool_calls": shape["scoped_tool_calls"],
                },
                "merge": {
                    "source": "worktree_boundary",
                    "latency_label": "boundary-to-merge",
                },
                "task_age": {
                    "source": "current_lane_entered_at",
                    "historical": False,
                },
                "utilization": {
                    "source": "agent_tasks intervals",
                    "capacity_source": (
                        "current engineer count × configured concurrency"
                    ),
                },
            },
            "notes": notes,
        }

    def _system_health_reviews(
        self,
        tasks: list,
        *,
        since: float,
        until: float,
        bucket_seconds: int,
        bucket_count: int,
        review_series: list[int],
    ) -> tuple[dict, list[dict]]:
        roots: dict[str, list] = {}
        for task in tasks:
            root_id = str(
                _health_task_value(task, "pipeline_root_id", "") or ""
            ).strip() or _health_task_id(task)
            roots.setdefault(root_id, []).append(task)
        implementation_actions = {
            "feature/implement",
            "feature/fix-review",
            "feature/implement-preapproved",
        }
        root_items = []
        total_rounds = 0
        first_pass_clean = 0
        total_fix_rounds = 0
        for root_id, chain in roots.items():
            chain.sort(key=lambda task: (
                _parse_health_timestamp(
                    _health_task_value(task, "created_at", "")
                ),
                _health_task_id(task),
            ))
            reviews = [
                task for task in chain
                if _health_action_name(task) == "feature/review"
            ]
            for review in reviews:
                ts = _parse_health_timestamp(
                    _health_task_value(review, "created_at", "")
                )
                if since <= ts <= until:
                    idx = _health_bucket_index(
                        ts,
                        since,
                        bucket_seconds,
                        bucket_count,
                    )
                    if idx >= 0:
                        review_series[idx] += 1
            if not reviews:
                continue
            first_review_ts = _parse_health_timestamp(
                _health_task_value(reviews[0], "created_at", "")
            )
            if not (since <= first_review_ts <= until):
                continue
            fix_rounds = 0
            for task in chain:
                created_ts = _parse_health_timestamp(
                    _health_task_value(task, "created_at", "")
                )
                if (
                    created_ts > first_review_ts
                    and _health_action_name(task) in implementation_actions
                ):
                    fix_rounds += 1
            rounds = len(reviews)
            is_clean = rounds == 1 and fix_rounds == 0
            if is_clean:
                first_pass_clean += 1
            total_rounds += rounds
            total_fix_rounds += fix_rounds
            root_items.append({
                "pipeline_root_id": root_id,
                "review_rounds": rounds,
                "fix_rounds": fix_rounds,
                "first_pass_clean": is_clean,
                "first_review_at": first_review_ts,
            })
        root_count = len(root_items)
        return {
            "roots_count": root_count,
            "average_rounds": (total_rounds / root_count) if root_count else 0.0,
            "first_pass_clean_count": first_pass_clean,
            "first_pass_clean_pct": (
                first_pass_clean / root_count if root_count else 0.0
            ),
            "fix_rounds": total_fix_rounds,
            "review_tasks": sum(item["review_rounds"] for item in root_items),
        }, root_items

    def _system_health_merges(
        self,
        tasks: list,
        *,
        since: float,
        until: float,
        bucket_seconds: int,
        bucket_count: int,
        merge_series: list[int],
        window_seconds: int,
    ) -> dict:
        merged_count = 0
        lead_times = []
        open_count = 0
        stale_open_count = 0
        for task in tasks:
            boundary = _health_task_value(task, "worktree_boundary", {}) or {}
            if not isinstance(boundary, dict) or not boundary:
                continue
            status = str(boundary.get("status", "") or "").strip().lower()
            if status == "merged":
                merged_at = _parse_health_timestamp(boundary.get("merged_at"))
                if since <= merged_at <= until:
                    merged_count += 1
                    idx = _health_bucket_index(
                        merged_at,
                        since,
                        bucket_seconds,
                        bucket_count,
                    )
                    if idx >= 0:
                        merge_series[idx] += 1
                    recorded_at = _parse_health_timestamp(
                        boundary.get("recorded_at")
                    )
                    if recorded_at and merged_at >= recorded_at:
                        lead_times.append(merged_at - recorded_at)
                continue
            if not status and not (
                boundary.get("repo_root") and boundary.get("branch")
            ):
                continue
            open_count += 1
            recorded_at = _parse_health_timestamp(boundary.get("recorded_at"))
            if recorded_at and (until - recorded_at) > window_seconds:
                stale_open_count += 1
        return {
            "merged_count": merged_count,
            "median_boundary_to_merge_seconds": _health_percentile(
                lead_times,
                0.5,
            ),
            "open_count": open_count,
            "stale_open_count": stale_open_count,
        }

    def _system_health_task_age(self, tasks: list, *, now: float) -> dict:
        by_lane: dict[str, list[float]] = {}
        for task in tasks:
            lane = str(_health_task_value(task, "lane", "") or "").strip()
            if not lane or lane == ARCHIVED_LANE:
                continue
            anchor = _parse_health_timestamp(
                _health_task_value(task, "lane_entered_at", "")
            ) or _parse_health_timestamp(
                _health_task_value(task, "created_at", "")
            )
            if not anchor:
                continue
            by_lane.setdefault(lane, []).append(max(0.0, now - anchor))
        out = {}
        for lane in sorted(by_lane):
            ages = by_lane[lane]
            buckets = {name: 0 for name, _start, _end in _SYSTEM_HEALTH_AGE_BUCKETS}
            for age in ages:
                for name, start, end in _SYSTEM_HEALTH_AGE_BUCKETS:
                    if age >= start and (end is None or age < end):
                        buckets[name] += 1
                        break
            out[lane] = {
                "count": len(ages),
                "p50_seconds": _health_percentile(ages, 0.5),
                "p90_seconds": _health_percentile(ages, 0.9),
                "max_seconds": max(ages) if ages else 0.0,
                "buckets": buckets,
            }
        return out

    def _system_health_utilization(
        self,
        agent_tasks: list[dict],
        *,
        group: str,
        since: float,
        until: float,
        bucket_seconds: int,
        buckets: list[dict],
        busy_series: list[float],
        capacity_series: list[float],
        utilization_series: list[float],
    ) -> dict:
        for row in agent_tasks:
            row_group = str(row.get("group", "") or "").strip()
            if group and row_group and row_group != group:
                continue
            start = max(_safe_float(row.get("started_at", 0.0)), since)
            completed = row.get("completed_at")
            end_raw = until if completed in (None, "") else _safe_float(completed)
            end = min(max(end_raw, start), until)
            if end <= start:
                continue
            for idx, bucket in enumerate(buckets):
                overlap = max(
                    0.0,
                    min(end, bucket["end"]) - max(start, bucket["start"]),
                )
                if overlap > 0:
                    busy_series[idx] += overlap

        groups_for_capacity = [group] if group else list(self.groups.keys())
        total_capacity_per_bucket = 0.0
        for group_name in groups_for_capacity:
            if not group_name:
                continue
            engineer_count = 0
            for cell in self.iter_active_agents():
                if (
                    getattr(cell, "cell_type", "") == "agent"
                    and str(getattr(cell, "kind", "") or "") == "engineer"
                    and str(getattr(cell, "group", "") or "") == group_name
                ):
                    engineer_count += 1
            concurrency = normalize_default_worker_concurrency(
                self.get_engineer_settings(
                    group_name
                ).default_worker_concurrency
            )
            total_capacity_per_bucket += (
                engineer_count * concurrency * bucket_seconds
            )
        for idx in range(len(buckets)):
            capacity_series[idx] = total_capacity_per_bucket
            if total_capacity_per_bucket > 0:
                utilization_series[idx] = min(
                    100.0,
                    (busy_series[idx] / total_capacity_per_bucket) * 100.0,
                )
        busy_seconds = sum(busy_series)
        capacity_seconds = sum(capacity_series)
        return {
            "busy_seconds": busy_seconds,
            "capacity_seconds": capacity_seconds,
            "percent": min(
                100.0,
                (busy_seconds / capacity_seconds) * 100.0,
            ) if capacity_seconds > 0 else 0.0,
            "queue_empty_count": 0,
        }

    def selected_agent_id_for_session(self, session_id: str) -> str:
        """Return the selectable parent agent for a terminal session."""
        sid = str(session_id or "").strip()
        if not sid:
            return ""
        for cell in self.iter_active_agents():
            if cell.session_id != sid:
                continue
            if cell.cell_type == "terminal":
                parent = self.get_active_agent(cell.parent_id)
                return parent.id if parent else ""
            return cell.id
        return ""

    def sync_ui_selection_to_session(
        self,
        session_id: str,
        *,
        emit: bool = True,
        persist: bool = True,
    ) -> str:
        """Mirror a known focused terminal session into persisted UI state."""
        selected_id = self.selected_agent_id_for_session(session_id)
        if not selected_id:
            return ""
        cell = self.get_active_agent(selected_id)
        if not cell:
            return ""
        if self.selected_agent_id != selected_id:
            self.selected_agent_id = selected_id
            if emit:
                self._emit(
                    "ui_update",
                    key="selected_agent_id",
                    value=self.selected_agent_id,
                )
            if persist:
                self._db_save_ui("selected_agent_id", self.selected_agent_id)
        if cell.group and self.active_group != cell.group:
            self.active_group = cell.group
            if emit:
                self._emit(
                    "ui_update",
                    key="active_group",
                    value=self.active_group,
                )
            if persist:
                self._db_save_ui("active_group", self.active_group)
        return selected_id

    # -- Delta emission -----------------------------------------------------

    def _emit(self, op: str, **kwargs):
        """Accumulate a delta operation for the next broadcast."""
        old_task_refs = self._sync_task_indexes_from_delta(op, kwargs)
        self._track_task_health_delta(op, kwargs, old_task_refs=old_task_refs)
        self._maybe_clear_engineer_queue_empty_from_delta(op, kwargs)
        delta = {"op": op, **kwargs}
        self._delta_ops.append(delta)

    def _emit_agent(self, cell: AgentCell):
        """Emit an agent_upsert delta with the full agent dict."""
        self._emit("agent_upsert", **asdict(cell))

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
        op = "decision_remove" if payload.get("archived") else "decision_upsert"
        self._emit(op, **payload)

    def _emit_pending_hire(self, pending_hire: dict | None):
        """Emit a pending-hire delta for live UI sync."""
        if not pending_hire:
            return
        payload = dict(pending_hire)
        status = str(payload.get("status", "") or "").strip()
        op = "pending_hire_upsert" if status == "pending" else "pending_hire_resolve"
        self._emit(op, **payload)

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

    def has_pending_task_health_recompute(self) -> bool:
        """Return whether a broadcast tick has health work to coalesce."""
        return bool(self._task_health_force_full or self._task_health_dirty)

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

    def _engineer_stream_counts(self, streams: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for stream in streams:
            state_name = str(stream.get("state", "") or "").strip()
            if not state_name:
                continue
            counts[state_name] = counts.get(state_name, 0) + 1
        return counts

    def _engineer_stream_payload(self, group: str) -> dict:
        from .worktree_streams import compute_worktree_streams

        try:
            streams = compute_worktree_streams(
                self,
                group=group,
                visibility_limit=_ENGINEER_STREAM_CONTEXT_LIMIT,
                include_orphaned=False,
            )
        except Exception:
            log.exception("Failed to compute engineer streams for %s", group)
            streams = []
        streams = [
            stream for stream in streams
            if str(stream.get("state", "") or "").strip() != "merged"
        ]
        return {
            "count": len(streams),
            "by_state": self._engineer_stream_counts(streams),
            "items": streams[:_ENGINEER_STREAM_CARD_LIMIT],
            "truncated": len(streams) > _ENGINEER_STREAM_CARD_LIMIT,
        }

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
        if not groups:
            return False
        known_groups = set(self._engineer_stream_groups())
        existing_groups = {
            str((op or {}).get("group", "") or "").strip()
            for op in self._delta_ops
            if str((op or {}).get("op", "") or "") in {
                "engineer_streams",
                "engineer_streams_update",
            }
        }
        emitted = False
        for group in groups:
            if not group or group in existing_groups:
                continue
            if group not in known_groups:
                # Group was just removed; emit an empty payload so clients
                # clear it from their local state.
                self._emit(
                    "engineer_streams",
                    group=group,
                    streams={
                        "count": 0,
                        "by_state": {},
                        "items": [],
                        "truncated": False,
                    },
                )
                emitted = True
                continue
            try:
                payload = self._engineer_stream_payload(group)
            except Exception:
                log.exception(
                    "engineer stream payload failed for group '%s'", group
                )
                continue
            self._emit("engineer_streams", group=group, streams=payload)
            emitted = True
        return emitted

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        decisions = {}
        pending_hires = {}
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
        return {
            "snapshot_protocol": COMPACT_SNAPSHOT_PROTOCOL,
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
            "agent_message_history": self.agent_message_history_snapshot(),
            "direct_messages_by_agent": self.direct_messages_snapshot(),
            "agent_peer_threads": self.agent_peer_threads_snapshot(),
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
            try:
                if self._critical_write_capture_agent(cell):
                    return
                self.db.save_agent_deferred(cell)
            except Exception:
                log.exception("Failed to save agent %s", cell.id)

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
                self._emit("task_upsert", **asdict(task))
            self._db_save_task(task)
        if updated and emit:
            self.recompute_task_health()
        return len(updated)

    # -- Peer message cache helpers -----------------------------------------

    def _upsert_peer_message_cache_entry(
        self,
        cell: AgentCell,
        entry: dict,
        *,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
    ) -> bool:
        if not cell or not entry or not entry.get("id"):
            return False
        message_id = str(entry.get("id", "") or "").strip()
        before = [dict(item) for item in (cell.mcp_messages or [])]
        kept = [
            dict(item)
            for item in (cell.mcp_messages or [])
            if str((item or {}).get("id", "") or "").strip() != message_id
        ]
        kept.append(dict(entry))
        cell.mcp_messages = _sort_mcp_message_cache(kept)[:max(1, limit)]
        return before != cell.mcp_messages

    def refresh_peer_message_cache_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
        emit: bool = True,
    ) -> list[dict]:
        """Rebuild one agent's bounded peer-message UI cache from SQLite."""
        aid = str(agent_id or "").strip()
        cell = self.agents.get(aid)
        if not aid or not cell:
            return []
        if not self.db:
            return [
                dict(entry)
                for entry in (cell.mcp_messages or [])
                if _is_peer_message_cache_entry(entry)
            ][:limit]
        rows = self.db.load_agent_peer_messages_for_agent(aid, limit=limit)
        peer_entries = [
            entry
            for entry in (
                _peer_message_cache_entry(row, aid) for row in rows
            )
            if entry
        ]
        non_peer_entries = [
            dict(entry)
            for entry in (cell.mcp_messages or [])
            if not _is_peer_message_cache_entry(entry)
        ]
        before = [dict(item) for item in (cell.mcp_messages or [])]
        cell.mcp_messages = _sort_mcp_message_cache(
            peer_entries + non_peer_entries
        )[:max(1, limit)]
        if emit and before != cell.mcp_messages:
            self._emit_agent(cell)
        return peer_entries

    def seed_peer_message_caches(
        self,
        *,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
        emit: bool = False,
    ) -> int:
        """Seed recent Architect peer messages after restart/load."""
        seeded = 0
        for cell in list(self.agents.values()):
            if str(getattr(cell, "kind", "") or "").strip() != "architect":
                continue
            entries = self.refresh_peer_message_cache_for_agent(
                cell.id,
                limit=limit,
                emit=emit,
            )
            if entries:
                seeded += 1
        return seeded

    def append_peer_message_to_caches(
        self,
        row: dict,
        *,
        emit: bool = True,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
    ) -> list[str]:
        """Project a canonical peer-message row into participant caches."""
        changed: list[str] = []
        for agent_id in (
            str((row or {}).get("sender_id", "") or "").strip(),
            str((row or {}).get("recipient_id", "") or "").strip(),
        ):
            if not agent_id or agent_id in changed:
                continue
            cell = self.agents.get(agent_id)
            entry = _peer_message_cache_entry(row or {}, agent_id)
            if not cell or not entry:
                continue
            if self._upsert_peer_message_cache_entry(
                cell,
                entry,
                limit=limit,
            ):
                changed.append(agent_id)
                if emit:
                    self._emit_agent(cell)
        return changed

    def save_peer_message(
        self,
        row: dict,
        *,
        emit: bool = True,
        cache_participants: bool = True,
    ) -> dict | None:
        """Persist a peer message and update bounded live UI/read-model caches."""
        if not self.db:
            return None
        saved = self.db.save_agent_peer_message(row)
        if saved:
            if cache_participants:
                self.append_peer_message_to_caches(saved, emit=emit)
            self.upsert_agent_peer_thread(saved, emit=emit)
        return saved

    def load_peer_messages_for_architect(
        self,
        architect_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        if not self.db:
            return []
        return self.db.load_peer_messages_for_architect(
            architect_id,
            limit=limit,
            since=since,
            peer_id=peer_id,
            thread_id=thread_id,
            include_archived=include_archived,
        )

    def mark_peer_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
        cache_participants: bool = True,
    ) -> dict | None:
        """Persist delivery state and update participant cache entries."""
        if not self.db:
            return None
        saved = self.db.mark_peer_message_delivered(
            message_id,
            delivered=delivered,
            reason=reason,
            delivered_at=delivered_at,
        )
        if not saved:
            return None
        if cache_participants:
            self.append_peer_message_to_caches(saved, emit=emit)
        self.upsert_agent_peer_thread(saved, emit=emit)
        return saved

    def update_peer_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
        cache_participants: bool = True,
    ) -> dict | None:
        """Persist an explicit peer-message delivery state in participant caches."""
        if not self.db:
            return None
        saved = self.db.update_agent_peer_message_delivery(
            message_id,
            delivery_state,
            reason=reason,
            delivered_at=delivered_at,
        )
        if not saved:
            return None
        if cache_participants:
            self.append_peer_message_to_caches(saved, emit=emit)
        self.upsert_agent_peer_thread(saved, emit=emit)
        return saved

    # -- Agent peer thread aggregate helpers --------------------------------

    def _agent_peer_thread_participant(self, row: dict, field: str) -> dict:
        agent_id = str((row or {}).get(f"{field}_id", "") or "").strip()
        row_group = str(
            (row or {}).get("group_name", (row or {}).get("group", "")) or ""
        )
        cell = self.agents.get(agent_id)
        kind = (
            str(getattr(cell, "kind", "") or "").strip()
            if cell else ""
        ) or str((row or {}).get(f"{field}_kind", "") or "").strip()
        name = (
            str(getattr(cell, "name", "") or "").strip()
            if cell else ""
        ) or str((row or {}).get(f"{field}_name", "") or "").strip() or agent_id
        group = (
            str(getattr(cell, "group", "") or "").strip()
            if cell else ""
        ) or row_group
        return {
            "id": agent_id,
            "kind": kind,
            "name": name,
            "group": group,
        }

    def _build_agent_peer_thread(
        self,
        rows: list[dict],
        *,
        message_limit: int = AGENT_PEER_THREAD_MESSAGE_LIMIT,
    ) -> dict | None:
        scoped = [
            dict(row)
            for row in (rows or [])
            if _is_agent_peer_thread_row(row)
        ]
        if not scoped:
            return None
        scoped.sort(
            key=lambda row: (
                _peer_message_timestamp(row),
                str((row or {}).get("id", "") or ""),
            )
        )
        first = scoped[0]
        thread_id = _agent_peer_thread_pair_key(first)
        if not thread_id:
            return None
        pair_ids = list(_agent_peer_thread_pair_ids(first))
        if not pair_ids[0] or not pair_ids[1]:
            return None
        scoped = [
            row for row in scoped
            if list(_agent_peer_thread_pair_ids(row)) == pair_ids
        ]
        if not scoped:
            return None
        participants_by_id: dict[str, dict] = {}
        for row in scoped:
            for field in ("sender", "recipient"):
                participant = self._agent_peer_thread_participant(row, field)
                pid = str(participant.get("id", "") or "").strip()
                if not pid or pid not in pair_ids:
                    continue
                existing = participants_by_id.get(pid, {})
                merged = dict(existing)
                for key, value in participant.items():
                    if str(value or "").strip():
                        merged[key] = value
                    elif key not in merged:
                        merged[key] = value
                participants_by_id[pid] = merged
        participants: list[dict] = [
            participants_by_id.get(pid)
            or {"id": pid, "kind": "", "name": pid, "group": ""}
            for pid in pair_ids
        ]
        participant_ids = list(pair_ids)

        messages = [_agent_peer_thread_message_entry(row) for row in scoped]
        last_row = max(
            scoped,
            key=lambda row: (
                _peer_message_timestamp(row),
                str((row or {}).get("id", "") or ""),
            ),
        )
        last_message = _agent_peer_thread_message_entry(last_row)
        group = str(last_row.get("group_name", last_row.get("group", "")) or "")
        if not group:
            for row in scoped:
                group = str(row.get("group_name", row.get("group", "")) or "")
                if group:
                    break
        ack_required_count = sum(
            1 for row in scoped if bool(row.get("ack_required", False))
        )
        pending_delivery_count = sum(
            1 for row in scoped
            if str(row.get("delivery_state", "") or "buffered").strip()
            == "buffered"
        )
        requires_reply_participant_ids: list[str] = []
        for row in scoped:
            if not bool(row.get("ack_required", False)):
                continue
            recipient_id = str(row.get("recipient_id", "") or "").strip()
            if recipient_id and recipient_id not in requires_reply_participant_ids:
                requires_reply_participant_ids.append(recipient_id)
        title_participants = sorted(
            participants,
            key=lambda participant: (
                (
                    str((participant or {}).get("name", "") or "").strip()
                    or str((participant or {}).get("id", "") or "").strip()
                ).casefold(),
                str((participant or {}).get("id", "") or "").strip(),
            ),
        )
        names = [
            str((participant or {}).get("name", "") or "").strip()
            or str((participant or {}).get("id", "") or "").strip()
            for participant in title_participants
        ]
        if len(names) >= 2:
            title = f"{names[0]} ↔ {names[1]}"
            if len(names) > 2:
                title += f" +{len(names) - 2}"
        elif names:
            title = names[0]
        else:
            title = thread_id

        limit = max(1, int(message_limit or AGENT_PEER_THREAD_MESSAGE_LIMIT))
        truncated = len(messages) > limit
        return {
            "thread_id": thread_id,
            "group": group,
            "title": title,
            "participants": participants,
            "participant_ids": participant_ids,
            "last_activity_at": _peer_message_timestamp(last_row),
            "last_message_id": last_message["id"],
            "last_message": last_message,
            "message_count": len(messages),
            "ack_required_count": ack_required_count,
            "pending_delivery_count": pending_delivery_count,
            "requires_reply_participant_ids": requires_reply_participant_ids,
            "messages": messages[-limit:],
            "truncated": truncated,
        }

    def _sorted_agent_peer_threads(self, threads: dict[str, dict]) -> dict[str, dict]:
        return dict(sorted(
            ((str(tid or ""), dict(thread)) for tid, thread in threads.items()
             if tid and thread),
            key=lambda item: (
                _safe_float((item[1] or {}).get("last_activity_at", 0)),
                str((item[1] or {}).get("last_message_id", "") or ""),
                item[0],
            ),
            reverse=True,
        ))

    def agent_peer_threads_snapshot(
        self,
        *,
        message_limit: int = AGENT_PEER_THREAD_MESSAGE_LIMIT,
    ) -> dict[str, dict]:
        """Return an ordered, bounded copy of the agent↔agent thread aggregate."""
        snapshot: dict[str, dict] = {}
        limit = max(1, int(message_limit or AGENT_PEER_THREAD_MESSAGE_LIMIT))
        for thread_id, thread in self._sorted_agent_peer_threads(
                self.agent_peer_threads).items():
            item = copy.deepcopy(thread)
            messages = list(item.get("messages", []) or [])
            item["messages"] = messages[-limit:]
            item["truncated"] = bool(item.get("truncated", False)) or (
                int(item.get("message_count", len(messages)) or 0) > len(
                    item["messages"]
                )
            )
            snapshot[thread_id] = item
        return snapshot

    def seed_agent_peer_threads(
        self,
        *,
        limit: int = AGENT_PEER_THREAD_SEED_ROW_LIMIT,
        emit: bool = False,
    ) -> int:
        """Seed the read-only Chat panel thread aggregate from SQLite."""
        if not self.db:
            return 0
        loader = getattr(self.db, "load_recent_agent_peer_chat_messages", None)
        if not callable(loader):
            return 0
        by_pair: dict[str, list[dict]] = {}
        for row in loader(limit=limit):
            if not _is_agent_peer_thread_row(row):
                continue
            pair_key = _agent_peer_thread_pair_key(row)
            if not pair_key:
                continue
            by_pair.setdefault(pair_key, []).append(row)
        threads: dict[str, dict] = {}
        for pair_key, rows in by_pair.items():
            thread = self._build_agent_peer_thread(rows)
            if thread:
                threads[pair_key] = thread
        self.agent_peer_threads = self._sorted_agent_peer_threads(threads)
        if emit:
            for pair_key, thread in self.agent_peer_threads.items():
                self._emit(
                    "agent_peer_thread_upsert",
                    thread_id=pair_key,
                    group=thread.get("group", ""),
                    thread=copy.deepcopy(thread),
                )
        return len(self.agent_peer_threads)

    def upsert_agent_peer_thread(
        self,
        row: dict,
        *,
        emit: bool = True,
    ) -> dict | None:
        """Refresh and optionally emit a complete thread replacement."""
        if not _is_agent_peer_thread_row(row or {}):
            return None
        pair_key = _agent_peer_thread_pair_key(row or {})
        first_id, second_id = _agent_peer_thread_pair_ids(row or {})
        if not pair_key or not first_id or not second_id:
            return None
        rows: list[dict]
        loader = (
            getattr(self.db, "load_agent_peer_chat_messages_for_pair", None)
            if self.db else None
        )
        if callable(loader):
            rows = loader(
                first_id,
                second_id,
                limit=AGENT_PEER_THREAD_SEED_ROW_LIMIT,
            )
        else:
            rows = [dict(row)]
        thread = self._build_agent_peer_thread(rows)
        if not thread:
            self.remove_agent_peer_thread(pair_key, emit=emit)
            return None
        self.agent_peer_threads[pair_key] = thread
        self.agent_peer_threads = self._sorted_agent_peer_threads(
            self.agent_peer_threads
        )
        if emit:
            self._emit(
                "agent_peer_thread_upsert",
                thread_id=pair_key,
                group=thread.get("group", ""),
                thread=copy.deepcopy(thread),
            )
        return thread

    def remove_agent_peer_thread(
        self,
        thread_id: str,
        *,
        emit: bool = True,
    ) -> bool:
        tid = str(thread_id or "").strip()
        if not tid or tid not in self.agent_peer_threads:
            return False
        self.agent_peer_threads.pop(tid, None)
        if emit:
            self._emit("agent_peer_thread_remove", thread_id=tid)
        return True

    # -- Direct message cache helpers ---------------------------------------

    def _direct_message_agent_ids(self, row: dict) -> list[str]:
        ids: list[str] = []
        for field in ("sender", "recipient"):
            kind = str((row or {}).get(f"{field}_kind", "") or "").strip()
            agent_id = str((row or {}).get(f"{field}_id", "") or "").strip()
            if kind == "user" or not agent_id or agent_id in ids:
                continue
            ids.append(agent_id)
        return ids

    def _upsert_direct_message_cache_entry(
        self,
        agent_id: str,
        entry: dict,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
    ) -> bool:
        aid = str(agent_id or "").strip()
        if not aid or not entry or not entry.get("id"):
            return False
        message_id = str(entry.get("id", "") or "").strip()
        before = [
            dict(item)
            for item in self.direct_messages_by_agent.get(aid, [])
        ]
        kept = [
            dict(item)
            for item in self.direct_messages_by_agent.get(aid, [])
            if str((item or {}).get("id", "") or "").strip() != message_id
        ]
        kept.append(dict(entry))
        sorted_entries = _sort_direct_message_cache(kept)
        if len(sorted_entries) > max(1, limit):
            sorted_entries = sorted_entries[-max(1, limit):]
        self.direct_messages_by_agent[aid] = sorted_entries
        return before != sorted_entries

    def refresh_direct_message_cache_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
        emit: bool = True,
    ) -> list[dict]:
        """Rebuild one agent's bounded direct-message cache from SQLite."""
        aid = str(agent_id or "").strip()
        if not aid:
            return []
        cell = self.agents.get(aid)
        if not cell:
            return []
        if not self.db:
            return [
                dict(entry)
                for entry in self.direct_messages_by_agent.get(aid, [])
            ][:limit]
        rows = self.db.load_direct_messages_for_agent(aid, limit=limit)
        entries = [
            entry
            for entry in (
                _direct_message_cache_entry(row, aid) for row in rows
            )
            if entry
        ]
        entries = _sort_direct_message_cache(entries)
        before = [
            dict(item)
            for item in self.direct_messages_by_agent.get(aid, [])
        ]
        self.direct_messages_by_agent[aid] = entries[-max(1, limit):]
        if emit and before != self.direct_messages_by_agent[aid]:
            for entry in self.direct_messages_by_agent[aid]:
                self._emit(
                    "direct_message_upsert",
                    id=entry["id"],
                    agent_id=aid,
                    group=str(getattr(cell, "group", "") or ""),
                    message=dict(entry),
                    limit=limit,
                )
        return self.direct_messages_by_agent[aid]

    def seed_direct_message_caches(
        self,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
        emit: bool = False,
    ) -> int:
        """Seed recent direct-message/display rows after restart/load."""
        seeded = 0
        for cell in list(self.agents.values()):
            if str(getattr(cell, "cell_type", "") or "") != "agent":
                continue
            entries = self.refresh_direct_message_cache_for_agent(
                cell.id,
                limit=limit,
                emit=emit,
            )
            if entries:
                seeded += 1
        return seeded

    def direct_messages_snapshot(
        self,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
    ) -> dict[str, list[dict]]:
        """Return a bounded copy of the per-agent direct-message cache."""
        snapshot: dict[str, list[dict]] = {}
        for agent_id, entries in self.direct_messages_by_agent.items():
            aid = str(agent_id or "").strip()
            if not aid:
                continue
            bounded = _sort_direct_message_cache([
                dict(entry) for entry in (entries or [])
            ])[-max(1, limit):]
            snapshot[aid] = bounded
        return snapshot

    def append_direct_message_to_caches(
        self,
        row: dict,
        *,
        emit: bool = True,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
    ) -> list[str]:
        """Project a canonical direct-message row into participant caches."""
        if not _is_user_direct_message_row(row or {}):
            return []
        changed: list[str] = []
        for agent_id in self._direct_message_agent_ids(row or {}):
            cell = self.agents.get(agent_id)
            entry = _direct_message_cache_entry(row or {}, agent_id)
            if not cell or not entry:
                continue
            if self._upsert_direct_message_cache_entry(
                agent_id,
                entry,
                limit=limit,
            ):
                changed.append(agent_id)
                if emit:
                    self._emit(
                        "direct_message_upsert",
                        id=entry["id"],
                        agent_id=agent_id,
                        group=entry.get("group", ""),
                        message=dict(entry),
                        limit=limit,
                    )
        return changed

    def save_direct_message(
        self,
        row: dict,
        *,
        emit: bool = True,
    ) -> dict | None:
        """Persist a direct message and update bounded live UI caches."""
        if not self.db:
            return None
        saved = self.db.save_direct_message(row)
        if saved:
            self.append_direct_message_to_caches(saved, emit=emit)
            cloud_hooks.notify_direct_message_observers(
                "direct_message_saved",
                saved,
                state=self,
            )
        return saved

    def update_direct_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
    ) -> dict | None:
        """Persist direct-message delivery state and update live caches."""
        if not self.db:
            return None
        saved = self.db.update_direct_message_delivery(
            message_id,
            delivery_state,
            reason=reason,
            delivered_at=delivered_at,
        )
        if not saved:
            return None
        self.append_direct_message_to_caches(saved, emit=emit)
        cloud_hooks.notify_direct_message_observers(
            "direct_message_delivery_updated",
            saved,
            state=self,
        )
        return saved

    def mark_direct_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
    ) -> dict | None:
        """Convenience wrapper for delivered/failed direct messages."""
        return self.update_direct_message_delivery(
            message_id,
            "delivered" if delivered else "failed",
            reason="" if delivered else reason,
            delivered_at=delivered_at,
            emit=emit,
        )

    def mark_direct_message_read(
        self,
        message_id: str,
        *,
        read_at: float | None = None,
        reader_id: str = "",
        emit: bool = True,
    ) -> dict | None:
        """Persist direct-message UI read state without changing delivery."""
        if not self.db:
            return None
        saved = self.db.mark_direct_message_read(
            message_id,
            read_at=read_at,
            reader_id=reader_id,
        )
        if not saved:
            return None
        changed = []
        for agent_id in self._direct_message_agent_ids(saved):
            entry = _direct_message_cache_entry(saved, agent_id)
            if not entry:
                continue
            if self._upsert_direct_message_cache_entry(agent_id, entry):
                changed.append((agent_id, entry))
        if emit:
            for agent_id, entry in changed:
                self._emit(
                    "direct_message_read",
                    id=entry["id"],
                    agent_id=agent_id,
                    group=entry.get("group", ""),
                    read_at=entry.get("read_at", 0),
                    message=dict(entry),
                )
        cloud_hooks.notify_direct_message_observers(
            "direct_message_read",
            saved,
            state=self,
        )
        return saved

    # -- Agent history helpers -----------------------------------------------

    def _normalize_agent_message_history_entry(self, entry: dict) -> dict:
        return {
            "id": int(_safe_float(entry.get("id"), 0)),
            "agent_id": str(entry.get("agent_id", "") or "").strip(),
            "message": str(entry.get("message", "") or ""),
            "sent_at": _safe_float(entry.get("sent_at")),
        }

    def agent_message_history_read(
            self, agent_id: str,
            limit: int = AGENT_MESSAGE_HISTORY_LIMIT) -> list[dict]:
        """Return newest-first user message recall entries for one agent."""
        aid = str(agent_id or "").strip()
        if not aid:
            return []
        try:
            limit = max(1, min(int(limit or AGENT_MESSAGE_HISTORY_LIMIT), 1000))
        except (TypeError, ValueError):
            limit = AGENT_MESSAGE_HISTORY_LIMIT
        if self.db:
            try:
                rows = self.db.load_agent_message_history(aid, limit=limit)
                history = [
                    self._normalize_agent_message_history_entry(row)
                    for row in rows
                ]
                self.agent_message_history[aid] = history[
                    :AGENT_MESSAGE_HISTORY_LIMIT
                ]
                return history
            except Exception:
                log.exception("Failed to load message history for %s", aid)
        return [
            self._normalize_agent_message_history_entry(row)
            for row in self.agent_message_history.get(aid, [])[:limit]
        ]

    def agent_message_history_snapshot(
            self, limit: int = AGENT_MESSAGE_HISTORY_LIMIT) -> dict[str, list[dict]]:
        """Return bounded newest-first recall history for cells in state."""
        snapshot: dict[str, list[dict]] = {}
        for aid in self.agents:
            entries = self.agent_message_history_read(aid, limit=limit)
            if entries:
                snapshot[aid] = entries
        return snapshot

    def record_message_history(self, agent_id: str, message: str) -> dict | None:
        """Persist and publish a user-sent message for per-agent recall."""
        aid = str(agent_id or "").strip()
        text = str(message or "")
        if not aid or not text.strip():
            return None
        ts = time.time()
        entry = {
            "id": int(ts * 1000000),
            "agent_id": aid,
            "message": text,
            "sent_at": ts,
        }
        if self.db:
            try:
                entry = self.db.save_agent_message_history(entry)
            except Exception:
                log.exception("Failed to record message history for %s", aid)
                return None
        entry = self._normalize_agent_message_history_entry(entry)
        history = self.agent_message_history.setdefault(aid, [])
        history.insert(0, entry)
        del history[AGENT_MESSAGE_HISTORY_LIMIT:]
        self._emit(
            "agent_message_history_append",
            agent_id=aid,
            entry=entry,
            limit=AGENT_MESSAGE_HISTORY_LIMIT,
        )
        return entry

    def history_record_agent(self, cell: AgentCell):
        """Record a new agent in the history table."""
        if not self.db:
            return
        import time
        try:
            self.db.save_agent_history({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "group": cell.group,
                "agent_type": cell.agent_type,
                "template": cell.template,
                "created_at": time.time(),
                "worktree_branch": cell.worktree_branch,
                "status": "active",
            })
        except Exception:
            log.exception("Failed to record agent history %s", cell.id)

    def history_remove_agent(self, cell: AgentCell):
        """Mark an agent as removed in history, snapshot final tokens."""
        if not self.db:
            return
        import time
        try:
            # Read current totals and add session tokens
            rec = self.db.load_agent_history_detail(cell.id)
            prev_in = rec["total_tokens_in"] if rec else 0
            prev_out = rec["total_tokens_out"] if rec else 0
            already_removed = bool(rec and rec.get("removed_at"))
            fields = {
                "removed_at": (rec or {}).get("removed_at") or time.time(),
                "total_tokens_in": (
                    prev_in if already_removed
                    else prev_in + cell.session_tokens_in
                ),
                "total_tokens_out": (
                    prev_out if already_removed
                    else prev_out + cell.session_tokens_out
                ),
            }
            if (rec or {}).get("status") != "merged":
                fields["status"] = "removed"
            self.db.update_agent_history(cell.id, **fields)
        except Exception:
            log.exception("Failed to update agent history on remove %s",
                          cell.id)

    def history_update_agent(self, cell: AgentCell, **fields):
        """Update arbitrary fields on an agent's history record."""
        if not self.db:
            return
        try:
            self.db.update_agent_history(cell.id, **fields)
        except Exception:
            log.exception("Failed to update agent history %s", cell.id)

    def history_record_dispatch(self, cell: AgentCell, task: BoardTask, *,
                                engineer_group: str = "",
                                engineer_id: str = ""):
        """Record a task dispatch in history."""
        import time
        ts = time.time()
        if cell.mark_progress(ts):
            self._emit_agent(cell)
        engineer_group = str(engineer_group or "").strip()
        engineer_id = str(engineer_id or "").strip()
        try:
            self._db_save_agent(cell)
            if self.db:
                self.db.save_agent_task({
                    "agent_id": cell.id,
                    "task_id": task.id,
                    "task_title": task.task,
                    "started_at": ts,
                })
                self.db.update_agent_history(
                    cell.id, total_tasks=(
                        self.db.load_agent_history_detail(cell.id) or {}
                    ).get("total_tasks", 0) + 1)
            if engineer_group:
                entry = {
                    "group": engineer_group,
                    "task_id": task.id,
                    "task_title": task.task,
                    "agent_id": cell.id,
                    "agent_name": cell.name,
                    "agent_slug": cell.slug,
                    "agent_owned": bool(
                        engineer_id and cell.created_by_engineer_id == engineer_id
                    ),
                    "started_at": ts,
                }
                if self.db:
                    entry["id"] = self.db.save_engineer_task_log_entry(entry)
                    self.db.trim_engineer_task_log(
                        engineer_group,
                        limit=_ENGINEER_WORKLOG_LIMIT,
                    )
                else:
                    entries = self.engineer_worklog.get(engineer_group, [])
                    newest_id = entries[0]["id"] if entries else 0
                    entry["id"] = int(newest_id or 0) + 1
                self._append_engineer_worklog_entry(engineer_group, entry)
        except Exception:
            log.exception("Failed to record dispatch history %s → %s",
                          cell.id, task.id)

    def history_record_message(self, cell_id: str, action: str,
                               message: str, task_id: str = "",
                               *, mark_progress: bool = True):
        """Record an agent message (torque ai report) in history."""
        import time
        ts = time.time()
        cell = self.agents.get(cell_id)
        if cell:
            if mark_progress and cell.mark_progress(ts):
                self._emit_agent(cell)
            if mark_progress:
                self._db_save_agent(cell)
        if not self.db:
            return
        try:
            self.db.save_agent_message({
                "agent_id": cell_id,
                "task_id": task_id,
                "timestamp": ts,
                "action": action,
                "message": message,
            })
        except Exception:
            log.exception("Failed to record agent message %s/%s",
                          cell_id, action)

    def history_complete_task(self, agent_id: str, task_id: str,
                              outcome: str):
        """Mark an agent-task association as completed."""
        if not self.db:
            return
        import time
        try:
            self.db.update_agent_task(
                agent_id, task_id,
                completed_at=time.time(), outcome=outcome)
        except Exception:
            log.exception("Failed to complete agent task %s/%s",
                          agent_id, task_id)

    def history_snapshot_tokens(self, cell: AgentCell):
        """Snapshot current session tokens into history totals."""
        if not self.db or not (cell.session_tokens_in
                               or cell.session_tokens_out):
            return
        try:
            rec = self.db.load_agent_history_detail(cell.id)
            if not rec:
                return
            self.db.update_agent_history(
                cell.id,
                total_tokens_in=(rec["total_tokens_in"]
                                 + cell.session_tokens_in),
                total_tokens_out=(rec["total_tokens_out"]
                                  + cell.session_tokens_out),
            )
        except Exception:
            log.exception("Failed to snapshot tokens %s", cell.id)

    def history_reconcile_tombstoned_agents(self) -> int:
        """Correct legacy history rows for tombstoned agents."""
        if not self.db:
            return 0
        reconciled = 0
        for cell in list(self.agents.values()):
            if cell.cell_type != "agent" or not self.agent_is_tombstoned(cell):
                continue
            rec = self.db.load_agent_history_detail(cell.id)
            if not rec:
                continue
            if rec.get("removed_at") and rec.get("status") != "active":
                continue
            self.history_remove_agent(cell)
            reconciled += 1
        return reconciled

    def load(self):
        from .config import DB_FILE, STATE_FILE
        if not self.db:
            return

        # Migration: if DB is empty but state.json exists, import it
        if not self.db.has_data() and STATE_FILE.exists():
            self.db.migrate_from_json(STATE_FILE)

        data = self.db.load_all()

        try:
            fields = set(AgentCell.__dataclass_fields__)
            for aid, raw in data.get("agents", {}).items():
                filtered = {k: v for k, v in raw.items() if k in fields}
                cell = AgentCell(**filtered)
                cell.status = "stopped"
                for f in _EPHEMERAL_FIELDS:
                    setattr(cell, f, type(getattr(cell, f))())
                self.agents[aid] = cell
            self.groups = data.get("groups", {})
            for gname in list(self.groups):
                self.groups[gname] = [
                    aid for aid in self.groups[gname] if aid in self.agents
                ]
            # Restore group settings (backward-compat: missing key → empty)
            gs_fields = set(GroupSettings.__dataclass_fields__)
            for gname, raw in data.get("group_settings", {}).items():
                if gname in self.groups:
                    filtered = {k: v for k, v in raw.items() if k in gs_fields}
                    if "worktree_merge_cleanup" in filtered:
                        filtered["worktree_merge_cleanup"] = (
                            normalize_worktree_merge_cleanup(
                                filtered["worktree_merge_cleanup"])
                        )
                    if "engineer_merge_mode" in filtered:
                        filtered["engineer_merge_mode"] = (
                            normalize_engineer_merge_mode(
                                filtered["engineer_merge_mode"])
                        )
                    if "guidance_hint_cadence" in filtered:
                        filtered["guidance_hint_cadence"] = (
                            normalize_guidance_hint_cadence(
                                filtered["guidance_hint_cadence"])
                        )
                    if "board_sync_provider" in filtered:
                        filtered["board_sync_provider"] = (
                            _normalize_board_sync_provider(
                                filtered["board_sync_provider"])
                        )
                    if "board_sync_enabled" in filtered:
                        filtered["board_sync_enabled"] = bool(
                            filtered["board_sync_enabled"])
                    if "board_sync_github" in filtered:
                        filtered["board_sync_github"] = (
                            _normalize_board_sync_github_settings(
                                filtered["board_sync_github"])
                        )
                    self._normalize_architect_settings_mapping(
                        filtered,
                        strict=False,
                    )
                    self.group_settings[gname] = GroupSettings(**filtered)
            # Promote orphaned children whose parent was deleted
            for aid, cell in self.agents.items():
                if cell.parent_id and cell.parent_id not in self.agents:
                    log.warning("Orphaned child '%s' (parent %s gone) "
                                "— promoting to standalone", cell.name,
                                cell.parent_id)
                    cell.parent_id = ""
                    if cell.group in self.groups:
                        self.groups[cell.group].append(aid)
            self._rebuild_children()
            # Global settings
            gs_raw = data.get("global_settings")
            if gs_raw:
                gls_fields = set(GlobalSettings.__dataclass_fields__)
                gls_filtered = {k: v for k, v in gs_raw.items()
                                if k in gls_fields}
                self.global_settings = GlobalSettings(**gls_filtered)
            # Board state — use global default_lanes as fallback
            default = (self.global_settings.default_lanes
                       or list(_DEFAULT_LANES))
            self.board_lanes = _normalize_board_lanes(
                data.get("board_lanes") or default
            )
            bt_fields = set(BoardTask.__dataclass_fields__)
            first_group = next(iter(self.groups), "")
            for tid, raw in data.get("board_tasks", {}).items():
                # Migrate renamed fields: title→task, description→instructions
                if "title" in raw and "task" not in raw:
                    raw["task"] = raw.pop("title")
                if "description" in raw and "instructions" not in raw:
                    raw["instructions"] = raw.pop("description")
                if not raw.get("group"):
                    raw["group"] = first_group
                raw["attachments"] = normalize_attachments(
                    raw.get("attachments", []))
                raw["artifacts"] = normalize_artifacts(
                    raw.get("artifacts", []))
                labels = list(raw.get("labels", []) or [])
                if "torque:archived" in labels and raw.get("lane") != ARCHIVED_LANE:
                    prior_lane = raw.get("lane") or ""
                    raw["lane"] = ARCHIVED_LANE
                    raw["archived_from_lane"] = raw.get("archived_from_lane") \
                        or prior_lane
                    raw["archived_at"] = raw.get("archived_at") \
                        or raw.get("updated_at", "") or raw.get("created_at", "")
                    labels = [label for label in labels
                              if label != "torque:archived"]
                raw["labels"] = labels
                raw["messages_thread"] = _normalize_messages_thread(
                    raw.get("messages_thread", [])
                )
                _normalize_verification_fields(raw)
                raw["board_sync"] = _normalize_board_sync(
                    raw.get("board_sync", {})
                )
                raw["worktree_boundary"] = _normalize_worktree_boundary(
                    raw.get("worktree_boundary", {})
                )
                resume_after = raw.get("resume_after_boundary_task_id", "")
                if resume_after is None:
                    resume_after = ""
                if not isinstance(resume_after, str):
                    resume_after = str(resume_after)
                raw["resume_after_boundary_task_id"] = resume_after.strip()
                filtered = {k: v for k, v in raw.items() if k in bt_fields}
                self.board_tasks[tid] = BoardTask(**filtered)
            self.task_id_aliases = {
                str(old_id or ""): str(new_id or "")
                for old_id, new_id in (data.get("task_id_aliases", {}) or {}).items()
                if old_id and new_id
            }
            self.task_id_counters = {
                normalize_group_prefix(prefix): max(1, int(next_root or 1))
                for prefix, next_root in (data.get("task_id_counters", {}) or {}).items()
                if prefix
            }
            self.pipeline_task_counters = {
                str(root_id or ""): max(1, int(next_child or 1))
                for root_id, next_child in (
                    data.get("pipeline_task_counters", {}) or {}
                ).items()
                if root_id
            }
            self._rebuild_task_indexes()
            self._clear_engineer_queue_empty_for_active_tasks()
            self.cleanup_stale_boundary_successors(emit=False)
            historical_ghosts = self.cleanup_resolved_engineer_message_followups(
                emit=False
            )
            log.info(
                "Retroactive cleanup: %d historical ghosts expired.",
                historical_ghosts,
            )
            for aid, cell in self.agents.items():
                cell.pending_engineer_message = bool(
                    self.agent_pending_engineer_reply_tasks(aid)
                )
            self.seed_peer_message_caches(emit=False)
            self.seed_direct_message_caches(emit=False)
            self.seed_agent_peer_threads(emit=False)
            reconciled_history = self.history_reconcile_tombstoned_agents()
            if reconciled_history:
                log.info(
                    "Reconciled %d tombstoned agent history rows.",
                    reconciled_history,
                )
            # panel_active: new key; backward compat from board_panel_open
            pa = data.get("panel_active", "")
            if not pa and data.get("board_panel_open"):
                pa = "board"
            self.panel_active = pa
            self.board_panel_height = data.get("board_panel_height", 0)
            self.active_group = str(data.get("active_group", "") or "")
            self.selected_principal_id = str(
                data.get("selected_principal_id", "") or ""
            )
            self.selected_agent_id = str(
                data.get("selected_agent_id", "") or ""
            )
            self.standalone_panel_layout = data.get(
                "standalone_panel_layout", {}
            ) or {}
            self.detached_panels = data.get("detached_panels", {}) or {}
            self.window_bounds = data.get("window_bounds", {}) or {}
            try:
                self.workspace_sidebar_width = int(
                    data.get("workspace_sidebar_width", 0) or 0
                )
            except (TypeError, ValueError):
                self.workspace_sidebar_width = 0
            try:
                self.terminal_direct_messages_height = max(
                    0,
                    int(data.get("terminal_direct_messages_height", 0) or 0),
                )
            except (TypeError, ValueError):
                self.terminal_direct_messages_height = 0
            try:
                self.engineer_panel_split_fraction = float(
                    data.get("engineer_panel_split_fraction", 0.30) or 0.30
                )
            except (TypeError, ValueError):
                self.engineer_panel_split_fraction = 0.30
            try:
                self.context_panel_split_ratio = float(
                    data.get("context_panel_split_ratio", 0.38) or 0.38
                )
            except (TypeError, ValueError):
                self.context_panel_split_ratio = 0.38
            self.supervisor_panel_state = data.get(
                "supervisor_panel_state", {}
            ) or {}
            self.events_dismissed_attention = data.get(
                "events_dismissed_attention", {}
            ) or {}
            self.board_filters_by_group = data.get(
                "board_filters_by_group", {}
            ) or {}
            self.board_selected_lanes_by_group = data.get(
                "board_selected_lanes_by_group", {}
            ) or {}
            self.board_hidden_wide_lanes_by_group = data.get(
                "board_hidden_wide_lanes_by_group", {}
            ) or {}
            self.board_saved_views_by_group = data.get(
                "board_saved_views_by_group", {}
            ) or {}
            self.board_lane_sorts_by_group = data.get(
                "board_lane_sorts_by_group", {}
            ) or {}
            self.board_card_density_by_group = data.get(
                "board_card_density_by_group", {}
            ) or {}
            # Slug migration: generate slugs for resources that lack them.
            # Groups first (terminal slugs may reference group slugs),
            # then agents (terminal slugs reference parent agent slugs),
            # then terminals.
            slug_dirty = False
            self.group_slugs = data.get("group_slugs", {})
            for gname in self.groups:
                if gname not in self.group_slugs:
                    self.group_slugs[gname] = self._unique_group_slug(gname)
                    slug_dirty = True
            # Agents (non-terminal) first
            for aid, cell in self.agents.items():
                if cell.cell_type != "terminal" and not cell.slug:
                    cell.slug = self._unique_agent_slug(cell.name,
                                                       exclude_id=aid)
                    self._db_save_agent(cell)
                    slug_dirty = True
            # Terminals: generate or migrate to parent:name format
            for aid, cell in self.agents.items():
                if cell.cell_type == "terminal" and (
                        not cell.slug or ":" not in cell.slug):
                    cell.slug = self._unique_terminal_slug(
                        cell.name, parent_id=cell.parent_id,
                        group=cell.group, exclude_id=aid)
                    self._db_save_agent(cell)
                    slug_dirty = True
            for tid, task in self.board_tasks.items():
                if not task.slug:
                    task.slug = self._unique_task_slug(task.task,
                                                      exclude_id=tid)
                    self._db_save_task(task)
                    slug_dirty = True
            # Schedules
            sched_fields = set(Schedule.__dataclass_fields__)
            for sid, raw in data.get("schedules", {}).items():
                filtered = {k: v for k, v in raw.items()
                            if k in sched_fields}
                self.schedules[sid] = Schedule(**filtered)
            for sid, sched in self.schedules.items():
                if not sched.slug:
                    sched.slug = self._unique_schedule_slug(
                        sched.name, exclude_id=sid)
                    self._db_save_schedule(sched)
                    slug_dirty = True
            adq_fields = set(AutoDispatchQueueEntry.__dataclass_fields__)
            for gname, entries in data.get("auto_dispatch_queues", {}).items():
                if gname not in self.groups or not isinstance(entries, list):
                    continue
                restored = []
                for raw in entries:
                    if not isinstance(raw, dict):
                        continue
                    filtered = {
                        k: v for k, v in raw.items() if k in adq_fields
                    }
                    if not filtered.get("task_id"):
                        continue
                    restored.append(AutoDispatchQueueEntry(**filtered))
                if restored:
                    self.auto_dispatch_queues[gname] = restored
            self.cleanup_stale_auto_dispatch_queue()
            if slug_dirty:
                self._db_save_groups()
                log.info("Generated slugs for existing resources")
            # Engineer settings
            if self.db:
                ws_fields = set(EngineerSettings.__dataclass_fields__)
                for gname, raw in self.db.load_all_engineer_settings().items():
                    filtered = {k: v for k, v in raw.items() if k in ws_fields}
                    if "autonomy_mode" in filtered:
                        filtered["autonomy_mode"] = (
                            normalize_engineer_autonomy_mode(
                                filtered["autonomy_mode"])
                        )
                    if "default_worker_concurrency" in filtered:
                        filtered["default_worker_concurrency"] = (
                            normalize_default_worker_concurrency(
                                filtered["default_worker_concurrency"])
                        )
                    if "wave_size_preference" in filtered:
                        filtered["wave_size_preference"] = (
                            normalize_engineer_wave_size_preference(
                                filtered["wave_size_preference"])
                        )
                    if "same_agent_follow_up_preference" in filtered:
                        filtered["same_agent_follow_up_preference"] = (
                            normalize_engineer_same_agent_follow_up_preference(
                                filtered["same_agent_follow_up_preference"])
                        )
                    if "digest_verbosity" in filtered:
                        filtered["digest_verbosity"] = (
                            normalize_engineer_digest_verbosity(
                                filtered["digest_verbosity"])
                        )
                    if "escalation_style" in filtered:
                        filtered["escalation_style"] = (
                            normalize_engineer_escalation_style(
                                filtered["escalation_style"])
                        )
                    self.engineer_settings[gname] = EngineerSettings(**filtered)
                ads_fields = set(AgentDigestSettings.__dataclass_fields__)
                for agent_id, raw in self.db.load_all_agent_digest_settings().items():
                    if agent_id not in self.agents:
                        continue
                    filtered = {
                        k: v for k, v in raw.items() if k in ads_fields
                    }
                    if "digest_verbosity" in filtered:
                        filtered["digest_verbosity"] = (
                            normalize_engineer_digest_verbosity(
                                filtered["digest_verbosity"]
                            )
                        )
                    self.agent_digest_settings[agent_id] = (
                        AgentDigestSettings(**filtered)
                    )
                self._backfill_architect_digest_defaults()
                self._backfill_architect_suppress_empty_once()
                for gname in self.groups:
                    entries = self.db.load_engineer_task_log(
                        gname,
                        limit=_ENGINEER_WORKLOG_LIMIT,
                    )
                    if entries:
                        self.engineer_worklog[gname] = entries
            cleaned = self.cleanup_orphaned_attention(emit=False)
            self.recompute_task_health(emit=False, persist=False)
            if cleaned["asks"] or cleaned["engineer_questions"]:
                log.info(
                    "Expired %d orphaned ask(s) and cleared %d stale engineer question(s)",
                    cleaned["asks"],
                    cleaned["engineer_questions"],
                )
        except (TypeError, KeyError):
            log.exception("Failed to load state from SQLite")

    def _rebuild_children(self):
        """Rebuild the parent→children index from parent_id fields."""
        self._children = {}
        for aid, cell in self.agents.items():
            if cell.cell_type == "agent":
                self._children.setdefault(aid, [])
        for aid, cell in self.agents.items():
            if cell.parent_id and cell.parent_id in self._children:
                self._children[cell.parent_id].append(aid)

    # -- Group settings -----------------------------------------------------

    def get_group_settings(self, name: str) -> GroupSettings:
        """Return group settings, creating defaults if group has none."""
        return self.group_settings.get(name, GroupSettings())

    def should_show_guidance_hint(self, hint_type: str, cell) -> bool:
        """Return whether a recurring soft guidance hint should be shown.

        Counters are ephemeral and scoped to a specific hint, agent, and
        provider session. ``guidance_hint_cadence=0`` preserves legacy
        every-time behavior; positive values show on the first occurrence and
        then every Nth occurrence.
        """
        hint_type = str(hint_type or "").strip()
        if not hint_type or not cell:
            return True
        agent_id = str(getattr(cell, "id", "") or "").strip()
        session_id = str(getattr(cell, "session_id", "") or "").strip()
        if not agent_id or not session_id:
            return True

        group = str(getattr(cell, "group", "") or "").strip()
        cadence = normalize_guidance_hint_cadence(
            getattr(
                self.get_group_settings(group),
                "guidance_hint_cadence",
                _DEFAULT_GUIDANCE_HINT_CADENCE,
            )
        )
        key = f"{hint_type}:{agent_id}:{session_id}"
        count = int(self.guidance_hint_counters.get(key, 0) or 0) + 1
        self.guidance_hint_counters[key] = count
        if cadence == 0:
            return True
        return count == 1 or count % cadence == 0

    def _normalize_architect_settings_mapping(
            self, fields: dict, *, strict: bool) -> dict:
        if "architect_autonomy_mode" in fields:
            fields["architect_autonomy_mode"] = normalize_architect_autonomy_mode(
                fields["architect_autonomy_mode"],
                strict=strict,
            )
        if "architect_digest_verbosity" in fields:
            fields["architect_digest_verbosity"] = (
                normalize_architect_digest_verbosity(
                    fields["architect_digest_verbosity"],
                    strict=strict,
                )
            )
        if "architect_journal_checkpoint_frequency" in fields:
            fields["architect_journal_checkpoint_frequency"] = (
                normalize_architect_journal_checkpoint_frequency(
                    fields["architect_journal_checkpoint_frequency"],
                    strict=strict,
                )
            )
        if "architect_review_gate_thresholds" in fields:
            fields["architect_review_gate_thresholds"] = (
                normalize_architect_review_gate_thresholds(
                    fields["architect_review_gate_thresholds"],
                    strict=strict,
                )
            )
        for bool_key in ("architect_suppress_empty_digests",):
            if bool_key in fields:
                raw = fields[bool_key]
                if isinstance(raw, str):
                    fields[bool_key] = (
                        raw.strip().lower() in {"1", "true", "yes", "on"}
                    )
                else:
                    fields[bool_key] = bool(raw)
        for int_key, default_val, min_val in (
                ("architect_push_interval",
                    _DEFAULT_ARCHITECT_PUSH_INTERVAL, 0),
                ("architect_max_interval",
                    _DEFAULT_ARCHITECT_MAX_INTERVAL, 0),
                ("architect_heartbeat_interval",
                    _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL, 0)):
            if int_key in fields:
                raw = fields[int_key]
                try:
                    parsed = int(raw)
                except (TypeError, ValueError):
                    parsed = default_val
                if parsed < min_val:
                    parsed = min_val
                fields[int_key] = parsed
        if "architect_enabled_events" in fields:
            raw = fields["architect_enabled_events"]
            if isinstance(raw, str):
                raw = [token.strip() for token in raw.split(",")]
            fields["architect_enabled_events"] = [
                str(item).strip() for item in (raw or [])
                if str(item).strip()
            ]
        for key in (
                "architect_boot_command", "architect_provider",
                "architect_model", "architect_reasoning_effort",
                "architect_directory", "architect_profile",
                "architect_shell", "architect_tab_color",
                "architect_custom_instructions"):
            if key in fields:
                fields[key] = str(fields[key] or "").strip()
        return fields

    def get_architect_settings(self, group: str) -> ArchitectSettings:
        """Return architect settings for a group, backed by group_settings."""
        group = str(group or "").strip()
        gs = self.get_group_settings(group)
        values = {}
        for key in ArchitectSettings.__dataclass_fields__:
            if key == "group":
                continue
            if hasattr(gs, key):
                value = getattr(gs, key)
                if key == "architect_review_gate_thresholds":
                    value = normalize_architect_review_gate_thresholds(value)
                values[key] = value
        self._normalize_architect_settings_mapping(values, strict=False)
        return ArchitectSettings(group=group, **values)

    def _architect_cells_for_group(self, group: str) -> list[AgentCell]:
        group = str(group or "").strip()
        return [
            cell for cell in self.iter_active_agents()
            if cell.cell_type == "agent"
            and str(getattr(cell, "kind", "") or "").strip() == "architect"
            and str(getattr(cell, "group", "") or "").strip() == group
        ]

    def _sync_architect_digest_settings(self, group: str,
                                        fields: dict) -> None:
        digest_field_map = {
            "architect_push_interval": "push_interval",
            "architect_max_interval": "max_interval",
            "architect_heartbeat_interval": "heartbeat_interval",
            "architect_digest_verbosity": "digest_verbosity",
            "architect_suppress_empty_digests": "suppress_empty",
            "architect_enabled_events": "enabled_events",
        }
        digest_updates = {
            digest_field_map[key]: fields[key]
            for key in digest_field_map
            if key in fields
        }
        if not digest_updates:
            return
        for architect in self._architect_cells_for_group(group):
            self.update_agent_digest_settings(architect.id, **digest_updates)

    def update_architect_settings(self, group: str, **fields) -> dict:
        """Update architect settings for a group.

        These settings are persisted in ``group_settings`` so read-only CLI
        paths and group snapshots see one source of truth.
        """
        group = str(group or "").strip()
        if group not in self.groups:
            return {}
        gs = self.group_settings.get(group)
        if gs is None:
            gs = GroupSettings()
            self.group_settings[group] = gs
        fields = dict(fields or {})
        if "custom_instructions" in fields and "architect_custom_instructions" not in fields:
            fields["architect_custom_instructions"] = fields.pop(
                "custom_instructions"
            )
        valid = set(ArchitectSettings.__dataclass_fields__) - {"group"}
        candidate = {
            key: value for key, value in fields.items()
            if key in valid
        }
        self._normalize_architect_settings_mapping(candidate, strict=True)
        applied = {}
        for key, value in candidate.items():
            setattr(gs, key, value)
            applied[key] = value
        if not applied:
            return {}
        payload = asdict(self.get_architect_settings(group))
        payload.pop("group", None)
        self._emit("architect_settings_update", group=group, **payload)
        self._emit("group_settings_update", name=group, **asdict(gs))
        self._db_save_group_settings(group)
        self._sync_architect_digest_settings(group, applied)
        return applied

    def update_group_settings(self, name: str, **fields):
        """Update group settings. Creates GroupSettings entry if needed."""
        if name not in self.groups:
            return
        gs = self.group_settings.get(name)
        if gs is None:
            gs = GroupSettings()
            self.group_settings[name] = gs
        valid = set(GroupSettings.__dataclass_fields__)
        for key, value in fields.items():
            if key in valid:
                if key == "worktree_merge_cleanup":
                    value = normalize_worktree_merge_cleanup(value)
                elif key == "engineer_merge_mode":
                    value = normalize_engineer_merge_mode(value)
                elif key == "guidance_hint_cadence":
                    value = normalize_guidance_hint_cadence(value)
                elif key == "board_sync_provider":
                    value = _normalize_board_sync_provider(value)
                elif key == "board_sync_enabled":
                    value = bool(value)
                elif key == "board_sync_github":
                    value = _normalize_board_sync_github_settings(value)
                elif key in {"agent_model", "agent_reasoning_effort"}:
                    value = str(value or "").strip()
                elif key in (
                        set(ArchitectSettings.__dataclass_fields__) - {"group"}):
                    normalized = self._normalize_architect_settings_mapping(
                        {key: value},
                        strict=True,
                    )
                    value = normalized[key]
                setattr(gs, key, value)
        self._emit("group_settings_update", name=name, **asdict(gs))
        if any(
                key in (set(ArchitectSettings.__dataclass_fields__) - {"group"})
                for key in fields):
            payload = asdict(self.get_architect_settings(name))
            payload.pop("group", None)
            self._emit("architect_settings_update", group=name, **payload)
        self._db_save_group_settings(name)
        self._sync_architect_digest_settings(name, fields)

    # -- Engineer settings & journal ------------------------------------------

    def get_engineer_for_group(self, group: str) -> Optional[AgentCell]:
        """Return the engineer agent for a group, or None."""
        gs = self.group_settings.get(group)
        if not gs or not gs.engineer_agent_id:
            return None
        return self.get_active_agent(gs.engineer_agent_id)

    def get_engineer_settings(self, group: str) -> EngineerSettings:
        """Return engineer settings for a group, creating defaults if needed."""
        return self.engineer_settings.get(group, EngineerSettings(group=group))

    def _default_agent_digest_settings(
        self,
        agent_id: str,
        cell=None,
    ) -> AgentDigestSettings:
        """Return kind-aware default digest settings for one recipient."""
        agent_id = str(agent_id or "").strip()
        if cell is None:
            cell = self.agents.get(agent_id)
        is_architect = bool(
            cell and str(getattr(cell, "kind", "") or "").strip() == "architect"
        )
        kwargs = {}
        if is_architect:
            arch = self.get_architect_settings(
                getattr(cell, "group", "") or ""
            )
            enabled = list(arch.architect_enabled_events or [])
            if not enabled:
                enabled = list(_ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS)
            kwargs["enabled_events"] = enabled
            kwargs["push_interval"] = int(
                arch.architect_push_interval
                if arch.architect_push_interval is not None
                else _DEFAULT_ARCHITECT_PUSH_INTERVAL
            )
            kwargs["max_interval"] = int(
                arch.architect_max_interval
                if arch.architect_max_interval is not None
                else _DEFAULT_ARCHITECT_MAX_INTERVAL
            )
            kwargs["heartbeat_interval"] = int(
                arch.architect_heartbeat_interval
                if arch.architect_heartbeat_interval is not None
                else _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL
            )
            kwargs["digest_verbosity"] = normalize_architect_digest_verbosity(
                arch.architect_digest_verbosity
            )
            kwargs["suppress_empty"] = bool(
                arch.architect_suppress_empty_digests
            )
            return AgentDigestSettings(
                agent_id=agent_id,
                architect_digest=True,
                wake_on_digest=False,
                **kwargs,
            )
        return AgentDigestSettings(
            agent_id=agent_id,
            push_interval=60,
            architect_digest=False,
            wake_on_digest=False,
            **kwargs,
        )

    def _legacy_agent_digest_settings(self, agent_id: str) -> AgentDigestSettings:
        agent_id = str(agent_id or "").strip()
        cell = self.agents.get(agent_id)
        if not cell:
            return AgentDigestSettings(agent_id=agent_id)
        if str(getattr(cell, "kind", "") or "").strip() == "architect":
            return self._default_agent_digest_settings(agent_id, cell)
        legacy_engineer = self.get_engineer_for_group(cell.group)
        if not legacy_engineer or legacy_engineer.id != agent_id:
            return self._default_agent_digest_settings(agent_id, cell)
        ws = self.get_engineer_settings(cell.group)
        push_interval = getattr(ws, "push_interval", 60)
        if push_interval is None:
            push_interval = 60
        max_interval = getattr(ws, "max_interval", 300)
        if max_interval is None:
            max_interval = 300
        heartbeat_interval = getattr(ws, "heartbeat_interval", 300)
        if heartbeat_interval is None:
            heartbeat_interval = 300
        return AgentDigestSettings(
            agent_id=agent_id,
            paused=bool(getattr(ws, "paused", False)),
            push_interval=int(push_interval),
            max_interval=int(max_interval),
            heartbeat_interval=int(heartbeat_interval),
            digest_verbosity=normalize_engineer_digest_verbosity(
                getattr(ws, "digest_verbosity", "balanced")
            ),
            enabled_events=list(getattr(ws, "enabled_events", []) or []),
            architect_digest=(cell.kind == "architect"),
            wake_on_digest=False,
        )

    def get_agent_digest_settings(self, agent_id: str) -> AgentDigestSettings:
        """Return digest settings for one engineer/architect recipient."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return AgentDigestSettings()
        settings = self.agent_digest_settings.get(agent_id)
        if settings is not None:
            return settings
        return self._legacy_agent_digest_settings(agent_id)

    def _backfill_architect_digest_defaults(self) -> None:
        """Add new architect-default digest events to existing settings rows."""
        changed = []
        for agent_id, settings in self.agent_digest_settings.items():
            cell = self.agents.get(agent_id)
            if str(getattr(cell, "kind", "") or "").strip() != "architect":
                continue
            enabled = list(getattr(settings, "enabled_events", []) or [])
            backfill_event_kinds = [
                "engineer_queue_empty",
                ENGINEER_AWAITING_HUMAN_INPUT,
                ENGINEER_ASK_RESOLVED,
            ]
            missing = [
                event_kind for event_kind in backfill_event_kinds
                if event_kind not in enabled
            ]
            if not missing:
                continue
            enabled.extend(missing)
            settings.enabled_events = enabled
            if not bool(getattr(settings, "architect_digest", False)):
                settings.architect_digest = True
            changed.append((agent_id, settings))
        if self.db:
            for agent_id, settings in changed:
                self.db.save_agent_digest_settings(agent_id, asdict(settings))

    def _backfill_architect_suppress_empty_once(self) -> None:
        """One-time: flip ``suppress_empty=True`` on pre-existing architect rows.

        The ``suppress_empty`` column was added with default 0 to keep the
        migration trivial. Without this backfill, architects whose digest
        settings row predates the new column keep emitting empty heartbeat
        digests — which is exactly the user complaint that motivated the
        new flag. We run this exactly once (gated by a ``ui_state``
        marker) so a user who later explicitly sets the flag back to
        False is not overwritten on subsequent boots.
        """
        marker_key = "architect_digest_suppress_empty_backfilled"
        if not self.db:
            return
        try:
            already = self.db.load_ui_state_value(marker_key)
        except Exception:
            log.exception("Failed to read backfill marker %s", marker_key)
            return
        if already:
            return
        changed = []
        for agent_id, settings in self.agent_digest_settings.items():
            cell = self.agents.get(agent_id)
            if str(getattr(cell, "kind", "") or "").strip() != "architect":
                continue
            if bool(getattr(settings, "suppress_empty", False)):
                continue
            settings.suppress_empty = True
            changed.append((agent_id, settings))
        for agent_id, settings in changed:
            self.db.save_agent_digest_settings(agent_id, asdict(settings))
        self.db.defer_write(
            "ui_state", "save_ui_state", marker_key, "1",
        )

    def update_agent_digest_settings(self, agent_id: str, **fields):
        """Update digest settings for one engineer/architect recipient."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        settings = self.agent_digest_settings.get(agent_id)
        if settings is None:
            settings = AgentDigestSettings(
                **asdict(self.get_agent_digest_settings(agent_id))
            )
            settings.agent_id = agent_id
            self.agent_digest_settings[agent_id] = settings
        valid = set(AgentDigestSettings.__dataclass_fields__)
        for key, value in fields.items():
            if key not in valid:
                continue
            if key == "digest_verbosity":
                value = normalize_engineer_digest_verbosity(value)
            elif key in {"paused", "architect_digest", "wake_on_digest",
                         "suppress_empty"}:
                value = bool(value)
            setattr(settings, key, value)
        payload = asdict(settings)
        self._emit(
            "agent_digest_update",
            group=getattr(self.agents.get(agent_id), "group", "") or "",
            **payload,
        )
        if self.db:
            self.db.save_agent_digest_settings(agent_id, payload)

    def ensure_agent_digest_settings(self, agent_id: str) -> AgentDigestSettings | None:
        """Persist a default digest-settings row when one does not exist yet."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return None
        if agent_id in self.agent_digest_settings:
            return self.agent_digest_settings[agent_id]
        self.update_agent_digest_settings(agent_id)
        return self.agent_digest_settings.get(agent_id)

    def _normalize_engineer_settings_value(self, key: str, value):
        if key == "autonomy_mode":
            return normalize_engineer_autonomy_mode(value)
        if key == "default_worker_concurrency":
            return normalize_default_worker_concurrency(value)
        if key == "wave_size_preference":
            return normalize_engineer_wave_size_preference(value)
        if key == "same_agent_follow_up_preference":
            return normalize_engineer_same_agent_follow_up_preference(value)
        if key == "digest_verbosity":
            return normalize_engineer_digest_verbosity(value)
        if key == "escalation_style":
            return normalize_engineer_escalation_style(value)
        if key in {
                "restrict_to_created_agents",
                "engineer_can_override_worker_provider"}:
            return bool(value)
        if key in {"pending_question_set_at", "pending_note_set_at"}:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0
        if key in {"pending_question_actor_id", "pending_note_actor_id"}:
            return str(value or "").strip()
        if key in {
                "engineer_model", "engineer_reasoning_effort",
                "engineer_directory", "engineer_profile",
                "engineer_shell", "engineer_tab_color"}:
            return str(value or "").strip()
        return value

    def _apply_engineer_settings_fields(
            self, group: str, fields: dict) -> tuple[EngineerSettings, dict]:
        fields = dict(fields or {})
        pending_question_actor_id = str(
            fields.pop("_pending_question_actor_id", "") or ""
        ).strip()
        pending_note_actor_id = str(
            fields.pop("_pending_note_actor_id", "") or ""
        ).strip()
        ws = self.engineer_settings.get(group)
        if ws is None:
            ws = EngineerSettings(group=group)
            self.engineer_settings[group] = ws
        previous_pending_question = str(
            getattr(ws, "pending_question", "") or ""
        )
        previous_pending_actor_id = str(
            getattr(ws, "pending_question_actor_id", "") or ""
        ).strip()
        previous_pending_note = str(
            getattr(ws, "pending_note", "") or ""
        )
        previous_pending_note_actor_id = str(
            getattr(ws, "pending_note_actor_id", "") or ""
        ).strip()
        valid = set(EngineerSettings.__dataclass_fields__)
        applied = {}
        for key, value in fields.items():
            if key in valid:
                value = self._normalize_engineer_settings_value(key, value)
                setattr(ws, key, value)
                applied[key] = value
        if (
                "pending_question" in applied
                and "pending_question_set_at" not in applied):
            current_pending_question = str(
                getattr(ws, "pending_question", "") or ""
            )
            if current_pending_question:
                pending_question_actor_changed = bool(
                    pending_question_actor_id
                    and pending_question_actor_id != previous_pending_actor_id
                )
                pending_question_is_new = (
                    current_pending_question != previous_pending_question
                    or pending_question_actor_changed
                )
                if pending_question_is_new:
                    ws.pending_question_set_at = time.time()
                    applied["pending_question_set_at"] = ws.pending_question_set_at
                    if (
                            pending_question_actor_id
                            or "pending_question_actor_id" not in applied):
                        ws.pending_question_actor_id = pending_question_actor_id
                        applied["pending_question_actor_id"] = (
                            pending_question_actor_id
                        )
                if pending_question_actor_id:
                    ws.pending_question_actor_id = pending_question_actor_id
                    applied["pending_question_actor_id"] = pending_question_actor_id
            elif previous_pending_question:
                ws.pending_question_set_at = 0.0
                ws.pending_question_actor_id = ""
                applied["pending_question_set_at"] = 0.0
                applied["pending_question_actor_id"] = ""
        if (
                "pending_note" in applied
                and "pending_note_set_at" not in applied):
            current_pending_note = str(getattr(ws, "pending_note", "") or "")
            if current_pending_note:
                pending_note_actor_changed = bool(
                    pending_note_actor_id
                    and pending_note_actor_id != previous_pending_note_actor_id
                )
                pending_note_is_new = (
                    current_pending_note != previous_pending_note
                    or pending_note_actor_changed
                )
                if pending_note_is_new:
                    ws.pending_note_set_at = time.time()
                    applied["pending_note_set_at"] = ws.pending_note_set_at
                    if (
                            pending_note_actor_id
                            or "pending_note_actor_id" not in applied):
                        ws.pending_note_actor_id = pending_note_actor_id
                        applied["pending_note_actor_id"] = pending_note_actor_id
                if pending_note_actor_id:
                    ws.pending_note_actor_id = pending_note_actor_id
                    applied["pending_note_actor_id"] = pending_note_actor_id
            elif previous_pending_note:
                ws.pending_note_set_at = 0.0
                ws.pending_note_actor_id = ""
                applied["pending_note_set_at"] = 0.0
                applied["pending_note_actor_id"] = ""
        d = asdict(ws)
        d.pop("group", None)
        self._emit("engineer_settings_update", group=group, **d)
        if "pending_question" in applied:
            current_pending_question = str(
                getattr(ws, "pending_question", "") or ""
            )
            current_pending_actor_id = str(
                getattr(ws, "pending_question_actor_id", "") or ""
            ).strip()
            if current_pending_question and (
                    current_pending_question != previous_pending_question
                    or (
                        current_pending_actor_id
                        and current_pending_actor_id != previous_pending_actor_id
                    )):
                emit_engineer_awaiting_human_input_event(
                    self,
                    group=group,
                    question=current_pending_question,
                    engineer_id=current_pending_actor_id,
                )
            elif previous_pending_question and not current_pending_question:
                emit_engineer_ask_resolved_event(
                    self,
                    group=group,
                    question=previous_pending_question,
                    engineer_id=(
                        pending_question_actor_id
                        or previous_pending_actor_id
                    ),
                )
        return ws, applied

    def _sync_legacy_engineer_digest_settings(self, group: str,
                                            fields: dict) -> None:
        legacy_engineer = self.get_engineer_for_group(group)
        if legacy_engineer and legacy_engineer.id in self.agent_digest_settings:
            digest_fields = {
                key: fields[key]
                for key in (
                    "paused",
                    "push_interval",
                    "max_interval",
                    "heartbeat_interval",
                    "digest_verbosity",
                    "enabled_events",
                )
                if key in fields
            }
            if digest_fields:
                self.update_agent_digest_settings(legacy_engineer.id, **digest_fields)

    def update_engineer_settings(self, group: str, **fields):
        """Update engineer settings for a group."""
        ws, applied = self._apply_engineer_settings_fields(group, fields)
        if self.db:
            self.db.save_engineer_settings(group, asdict(ws))
        self._sync_legacy_engineer_digest_settings(group, applied)

    async def update_engineer_settings_async(self, group: str, **fields) -> bool:
        """Update and await persistence for engineer settings for a group."""
        ws, applied = self._apply_engineer_settings_fields(group, fields)
        if self.db:
            await self.db.save_engineer_settings_async(group, asdict(ws))
        self._sync_legacy_engineer_digest_settings(group, applied)
        return True

    def engineer_restricts_to_created_agents(self, group: str) -> bool:
        """Return whether the group's Engineer is restricted to owned agents."""
        return bool(
            self.get_engineer_settings(group).restrict_to_created_agents
        )

    def agent_is_visible_to_engineer(self, engineer_id: str, agent_id: str) -> bool:
        """Return whether ``agent_id`` is visible/controllable to ``engineer_id``.

        Visibility is always limited to cells in the same group. Engineer scope
        is strict across the agent-kind hierarchy: an engineer sees itself,
        architects/user-level principals for scope-up coordination, and only
        workers/terminals owned by that engineer.
        """
        engineer = self.agents.get(str(engineer_id or "").strip())
        agent = self.agents.get(str(agent_id or "").strip())
        if self.agent_is_tombstoned(engineer) or self.agent_is_tombstoned(agent):
            return False
        if not engineer or engineer.cell_type != "agent":
            return False
        if str(getattr(engineer, "kind", "") or "").strip() != "engineer":
            return False
        if not agent or agent.cell_type not in {"agent", "terminal"}:
            return False
        if not engineer.group or agent.group != engineer.group:
            return False

        engineer_id = str(getattr(engineer, "id", "") or "").strip()
        if not engineer_id:
            return False
        if str(getattr(agent, "id", "") or "").strip() == engineer_id:
            return True

        kind = str(getattr(agent, "kind", "") or "").strip()
        if kind in {"architect", "user", "human"}:
            return True
        if kind == "engineer":
            return False

        owner_id = str(getattr(agent, "owner_engineer_id", "") or "").strip()
        created_by_id = str(
            getattr(agent, "created_by_engineer_id", "") or ""
        ).strip()
        if owner_id == engineer_id or created_by_id == engineer_id:
            return True

        if agent.cell_type == "terminal":
            parent_id = str(getattr(agent, "parent_id", "") or "").strip()
            if parent_id == engineer_id:
                return True
            parent = self.agents.get(parent_id)
            if (
                    parent
                    and str(getattr(parent, "group", "") or "").strip()
                    == engineer.group
            ):
                parent_owner_id = str(
                    getattr(parent, "owner_engineer_id", "") or ""
                ).strip()
                parent_created_by_id = str(
                    getattr(parent, "created_by_engineer_id", "") or ""
                ).strip()
                if (
                        str(getattr(parent, "id", "") or "").strip()
                        == engineer_id
                        or parent_owner_id == engineer_id
                        or parent_created_by_id == engineer_id
                ):
                    return True

        return False

    def _save_engineer_settings(self, group: str, emit: bool = True):
        ws = self.engineer_settings.get(group)
        if not ws:
            return
        d = asdict(ws)
        d.pop("group", None)
        if emit:
            self._emit("engineer_settings_update", group=group, **d)
        if self.db:
            self.db.save_engineer_settings(group, asdict(ws))

    def delete_agent_digest_settings(self, agent_id: str):
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        self.agent_digest_settings.pop(agent_id, None)
        if self.db:
            self.db.delete_agent_digest_settings(agent_id)

    def _open_human_asks_for_parent(self, parent_task_id: str,
                                    exclude_task_id: str = "") -> list[BoardTask]:
        asks: list[BoardTask] = []
        if not parent_task_id:
            return asks
        for task in self.board_tasks.values():
            if task.id == exclude_task_id:
                continue
            if task.parent_task_id != parent_task_id:
                continue
            if board_task_is_closed(task):
                continue
            if "torque:human" not in (task.labels or []):
                continue
            asks.append(task)
        return asks

    def _clear_parent_awaiting_input(self, parent: Optional[BoardTask],
                                     exclude_task_id: str = "",
                                     emit: bool = True):
        if not parent:
            return
        if self._open_human_asks_for_parent(parent.id, exclude_task_id):
            return

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        if parent.status:
            parent.status = ""
            parent.updated_at = now_iso
            if emit:
                self._emit("task_upsert", **asdict(parent))
            self._db_save_task(parent)

        root_id = parent.pipeline_root_id or parent.id
        if root_id == parent.id:
            return
        root = self.board_tasks.get(root_id)
        if root and root.status:
            root.status = ""
            root.updated_at = now_iso
            if emit:
                self._emit("task_upsert", **asdict(root))
            self._db_save_task(root)

    def _expire_orphaned_ask(self, task: BoardTask, reason: str,
                             emit: bool = True) -> bool:
        if "torque:human" not in (task.labels or []) or board_task_is_closed(task):
            return False

        from datetime import datetime, timezone
        parent = self.board_tasks.get(task.parent_task_id)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        changed = False

        if task.agent_id:
            task.agent_id = ""
            changed = True
        if task.status:
            task.status = ""
            changed = True
        if task.lane != "Done":
            task.lane = "Done"
            max_pos = max(
                (t.position for t in self.board_tasks.values()
                 if t.lane == "Done" and t.id != task.id),
                default=-1,
            )
            task.position = max_pos + 1
            for label in ("torque:blocked", "torque:error"):
                if label in task.labels:
                    task.labels.remove(label)
            changed = True
        if reason and not any(
                m.get("action") == "system"
                and m.get("message") == reason
                for m in (task.messages or [])):
            task.messages.append({
                "timestamp": now.timestamp(),
                "action": "system",
                "message": reason,
                "agent_name": "Torque",
            })
            changed = True

        if changed:
            task.updated_at = now_iso
            if emit:
                self._emit("task_upsert", **asdict(task))
            self._db_save_task(task)

        self._clear_parent_awaiting_input(
            parent, exclude_task_id=task.id, emit=emit)
        return changed

    def _agent_persisted_in_db(self, agent_id: str) -> bool:
        agent_id = str(agent_id or "").strip()
        if not agent_id or not self.db:
            return False
        try:
            return bool(self.db.agent_exists(agent_id))
        except Exception:
            log.exception("Failed to check persisted agent %s", agent_id)
            return False

    def _attention_source_agent_available(
            self, agent_id: str, live_agents: set[str], *,
            allow_persisted_agent_fallback: bool) -> bool:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return False
        if agent_id in live_agents:
            return True
        return (
            allow_persisted_agent_fallback
            and self._agent_persisted_in_db(agent_id)
        )

    def cleanup_orphaned_attention(
            self, emit: bool = True, *,
            allow_persisted_agent_fallback: bool = True) -> dict[str, int]:
        """Expire asks and pending engineer questions whose source agent is gone."""
        cleaned = {"asks": 0, "engineer_questions": 0}
        live_agents = {
            aid for aid, cell in self.agents.items()
            if not self.agent_is_tombstoned(cell)
        }

        for group, ws in self.engineer_settings.items():
            gs = self.group_settings.get(group)
            engineer_id = gs.engineer_agent_id if gs else ""
            question_source_id = (
                str(getattr(ws, "pending_question_actor_id", "") or "").strip()
                or engineer_id
            )
            question_source_available = self._attention_source_agent_available(
                question_source_id,
                live_agents,
                allow_persisted_agent_fallback=allow_persisted_agent_fallback,
            )
            engineer_available = self._attention_source_agent_available(
                engineer_id,
                live_agents,
                allow_persisted_agent_fallback=allow_persisted_agent_fallback,
            )
            if ws.pending_question and not question_source_available:
                stale_question = ws.pending_question
                stale_actor_id = (
                    str(getattr(ws, "pending_question_actor_id", "") or "").strip()
                    or engineer_id
                )
                log.warning(
                    "Clearing stale engineer pending question for group=%s "
                    "source_agent_id=%r in_memory=%s persisted=%s "
                    "pending_question_len=%d",
                    group,
                    question_source_id,
                    bool(question_source_id and question_source_id in live_agents),
                    bool(
                        question_source_id
                        and self._agent_persisted_in_db(question_source_id)
                    ),
                    len(ws.pending_question or ""),
                )
                ws.pending_question = ""
                ws.pending_question_set_at = 0.0
                ws.pending_question_actor_id = ""
                ws.paused = False
                ws.pending_note = ""
                ws.pending_note_kind = ""
                self._save_engineer_settings(group, emit=emit)
                if emit:
                    emit_engineer_ask_resolved_event(
                        self,
                        group=group,
                        question=stale_question,
                        engineer_id=stale_actor_id,
                    )
                cleaned["engineer_questions"] += 1
            elif ws.pending_note and not engineer_available:
                ws.pending_note = ""
                ws.pending_note_kind = ""
                self._save_engineer_settings(group, emit=emit)

        reason = "Ask expired because the source agent is no longer available."
        for task in list(self.board_tasks.values()):
            if "torque:human" not in (task.labels or []) or board_task_is_closed(task):
                continue
            parent = self.board_tasks.get(task.parent_task_id)
            parent_agent_id = parent.agent_id if parent else ""
            parent_agent_available = self._attention_source_agent_available(
                parent_agent_id,
                live_agents,
                allow_persisted_agent_fallback=allow_persisted_agent_fallback,
            )
            if not parent or not parent_agent_available:
                if self._expire_orphaned_ask(task, reason, emit=emit):
                    cleaned["asks"] += 1

        return cleaned

    def journal_append(self, group: str, entry_type: str,
                       entry: str, author_cell_id: str = "",
                       timestamp: float | None = None,
                       source_key: str = "") -> dict:
        """Append an entry to the engineer journal. Returns the entry dict."""
        import time
        try:
            ts = float(timestamp) if timestamp is not None else time.time()
        except (TypeError, ValueError):
            ts = time.time()
        entry_id = 0
        inserted = True
        author_cell_id = str(author_cell_id or "").strip()
        source_key = str(source_key or "").strip()
        if self.db:
            entry_id, inserted = self.db.save_journal_entry(
                group,
                ts,
                entry_type,
                entry,
                author_cell_id=author_cell_id,
                source_key=source_key,
                return_inserted=True,
            )
        evt = {"id": entry_id, "group": group, "timestamp": ts,
               "type": entry_type, "entry": entry,
               "author_cell_id": author_cell_id}
        if not inserted:
            evt["duplicate"] = True
            return evt
        self._emit("journal_append", **evt)
        return evt

    def journal_read(self, group: str, limit: int = 20,
                     entry_type: str = "",
                     author_cell_id: str = "") -> list[dict]:
        """Read recent journal entries for a group."""
        if self.db:
            return self.db.load_journal_entries(
                group,
                limit,
                entry_type,
                author_cell_id=str(author_cell_id or "").strip(),
            )
        return []

    def load_decision(self, decision_id: str) -> dict | None:
        """Load one persisted architect decision."""
        if self.db:
            try:
                return self.db.load_decision(decision_id)
            except Exception:
                log.exception("Failed to load decision %s", decision_id)
        return None

    def save_decision(self, row_dict: dict) -> dict | None:
        """Persist one architect decision and return the normalized row."""
        if self.db:
            try:
                saved = self.db.save_decision(row_dict)
                self._emit_decision(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save decision %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    async def save_decision_async(self, row_dict: dict) -> dict | None:
        """Persist one architect decision off the event loop."""
        if self.db:
            try:
                saved = await self.db.save_decision_async(row_dict)
                self._emit_decision(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save decision %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    def load_decisions_for_architect(self, architect_id: str, *,
                                     include_archived: bool = False) -> list[dict]:
        """Load persisted decisions for one architect."""
        if self.db:
            try:
                return self.db.load_decisions_for_architect(
                    architect_id,
                    include_archived=include_archived,
                )
            except Exception:
                log.exception(
                    "Failed to load decisions for architect %s",
                    architect_id,
                )
        return []

    def load_all_decisions(self, *, include_archived: bool = False) -> list[dict]:
        """Load all persisted architect decisions."""
        if self.db:
            try:
                return self.db.load_all_decisions(
                    include_archived=include_archived,
                )
            except Exception:
                log.exception("Failed to load decisions")
        return []

    def delete_decision(self, decision_id: str) -> dict | None:
        """Soft-delete one architect decision."""
        if self.db:
            try:
                deleted = self.db.delete_decision(decision_id)
                self._emit_decision(deleted)
                return deleted
            except Exception:
                log.exception("Failed to delete decision %s", decision_id)
        return None

    def hard_delete_decision(self, decision_id: str) -> None:
        """Permanently delete one architect decision."""
        if self.db:
            try:
                self.db.hard_delete_decision(decision_id)
                self._emit("decision_remove", id=str(decision_id or "").strip())
            except Exception:
                log.exception("Failed to hard-delete decision %s", decision_id)

    def load_pending_hire(self, hire_id: str) -> dict | None:
        """Load one persisted pending hire."""
        if self.db:
            try:
                return self.db.load_pending_hire(hire_id)
            except Exception:
                log.exception("Failed to load pending hire %s", hire_id)
        return None

    def save_pending_hire(self, row_dict: dict) -> dict | None:
        """Persist one pending-hire row and emit the matching delta."""
        if self.db:
            try:
                saved = self.db.save_pending_hire(row_dict)
                self._emit_pending_hire(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save pending hire %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    async def save_pending_hire_async(self, row_dict: dict) -> dict | None:
        """Persist one pending-hire row off the event loop."""
        if self.db:
            try:
                saved = await self.db.save_pending_hire_async(row_dict)
                self._emit_pending_hire(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save pending hire %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    def load_pending_hires(self, *, status_filter: str = "",
                           architect_id: str = "") -> list[dict]:
        """Load pending-hire rows from persistence."""
        if self.db:
            try:
                return self.db.load_pending_hires(
                    status_filter=status_filter,
                    architect_id=architect_id,
                )
            except Exception:
                log.exception(
                    "Failed to load pending hires status=%s architect=%s",
                    status_filter,
                    architect_id,
                )
        return []

    def delete_pending_hire(self, hire_id: str) -> None:
        """Permanently delete one pending-hire row."""
        if self.db:
            try:
                self.db.delete_pending_hire(hire_id)
                self._emit("pending_hire_resolve", id=str(hire_id or "").strip())
            except Exception:
                log.exception("Failed to delete pending hire %s", hire_id)

    def _architect_journal_path(self, architect_id: str) -> Path:
        return Path(DATA_DIR) / "architect_journals" / (
            _safe_journal_filename(architect_id) + ".jsonl"
        )

    def _architect_journal_entry_id(self, architect_id: str,
                                    idempotency_key: str) -> str:
        digest = hashlib.sha256(
            f"{architect_id}:{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return digest[:12]

    def _recover_architect_journal_entry(self, architect_id: str, *,
                                         record_id: str,
                                         request_hash: str = "") -> dict | None:
        for existing in self.architect_journal_read(
            architect_id,
            limit=1_000_000,
        ):
            if str((existing or {}).get("id", "") or "") != record_id:
                continue
            existing_hash = str(
                (existing or {}).get("request_hash", "") or ""
            ).strip()
            if request_hash and existing_hash and existing_hash != request_hash:
                raise ValueError(
                    "Idempotency key was reused for a different architect journal append"
                )
            return existing
        return None

    def architect_journal_append(self, architect_id: str, entry_type: str,
                                 entry: str, *,
                                 idempotency_key: str = "",
                                 request_hash: str = "") -> dict:
        """Append one architect journal entry to its JSONL file."""
        import time

        architect_id = str(architect_id or "").strip()
        if not architect_id:
            raise ValueError("architect_id is required")
        idem_key = str(idempotency_key or "").strip()
        request_hash = str(request_hash or "").strip()
        if idem_key:
            receipt = self.db.load_command_receipt(idem_key) if self.db else None
            if receipt:
                existing_hash = str(receipt.get("request_hash", "") or "").strip()
                if request_hash and existing_hash and existing_hash != request_hash:
                    raise ValueError(
                        "Idempotency key was reused for a different architect journal append"
                    )
                response = receipt.get("response")
                if isinstance(response, dict):
                    return response
            recovered = self._recover_architect_journal_entry(
                architect_id,
                record_id=self._architect_journal_entry_id(
                    architect_id,
                    idem_key,
                ),
                request_hash=request_hash,
            )
            if recovered:
                if self.db:
                    self.db.save_command_receipt(
                        idempotency_key=idem_key,
                        surface="internal",
                        command_name="architect_journal_append",
                        request_hash=request_hash,
                        response=recovered,
                    )
                return recovered
        record = {
            "id": (
                self._architect_journal_entry_id(architect_id, idem_key)
                if idem_key else uuid.uuid4().hex[:12]
            ),
            "architect_id": architect_id,
            "timestamp": time.time(),
            "type": str(entry_type or "").strip(),
            "entry": str(entry or ""),
        }
        if request_hash:
            record["request_hash"] = request_hash
        path = self._architect_journal_path(architect_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if idem_key and self.db:
            self.db.save_command_receipt(
                idempotency_key=idem_key,
                surface="internal",
                command_name="architect_journal_append",
                request_hash=request_hash,
                response=record,
            )
        self._emit("architect_journal_append", **record)
        return record

    def architect_journal_read(self, architect_id: str, *,
                               since: float = 0,
                               limit: int = 20) -> list[dict]:
        """Read recent architect journal entries, newest first."""
        architect_id = str(architect_id or "").strip()
        if not architect_id:
            return []
        try:
            since_value = float(since or 0)
        except (TypeError, ValueError):
            since_value = 0.0
        try:
            limit_value = int(limit or 20)
        except (TypeError, ValueError):
            limit_value = 20
        if limit_value <= 0:
            return []

        path = self._architect_journal_path(architect_id)
        if not path.exists():
            return []

        entries = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(item, dict):
                    continue
                item_architect_id = str(
                    item.get("architect_id", architect_id) or architect_id
                ).strip()
                if item_architect_id != architect_id:
                    continue
                try:
                    timestamp = float(item.get("timestamp", 0) or 0)
                except (TypeError, ValueError):
                    timestamp = 0.0
                if since_value and timestamp <= since_value:
                    continue
                item["architect_id"] = architect_id
                item["timestamp"] = timestamp
                entries.append(item)
        if len(entries) > limit_value:
            entries = entries[-limit_value:]
        entries.reverse()
        return entries

    def _append_engineer_worklog_entry(self, group: str, entry: dict):
        """Append a Engineer worklog entry to in-memory state and emit it."""
        if not group:
            return
        item = dict(entry or {})
        item["group"] = group
        entries = self.engineer_worklog.setdefault(group, [])
        entries.insert(0, item)
        if len(entries) > _ENGINEER_WORKLOG_LIMIT:
            del entries[_ENGINEER_WORKLOG_LIMIT:]
        self._emit("engineer_worklog_append", group=group, entry=dict(item))

    def engineer_worklog_read(self, group: str, limit: int = 50) -> list[dict]:
        """Return recent persisted/current designated-engineer worklog entries for a group."""
        entries = self.engineer_worklog.get(group, [])
        if limit <= 0:
            return []
        return [dict(entry) for entry in entries[:limit]]

    # -- Global settings ----------------------------------------------------

    def get_default_command(self) -> str:
        """Return the effective default boot command.

        Priority: global_settings > env var > 'claude'
        """
        return self.global_settings.default_command or DEFAULT_COMMAND

    def update_global_settings(self, **fields):
        """Update global settings fields."""
        valid = set(GlobalSettings.__dataclass_fields__)
        updates = {}
        for key, value in fields.items():
            if key in valid:
                if key == "xterm_scrollback":
                    value = normalize_xterm_scrollback(value, strict=True)
                elif key == "event_ingest_max_rows":
                    value = normalize_event_ingest_max_rows(value)
                elif key == "event_ingest_max_days":
                    value = normalize_event_ingest_max_days(value)
                elif key == "mcp_call_log_args_capture":
                    value = normalize_mcp_call_log_args_capture(value)
                elif key == "mcp_call_log_full_capture_tools":
                    value = normalize_mcp_call_log_full_capture_tools(value)
                updates[key] = value
        for key, value in updates.items():
            setattr(self.global_settings, key, value)
        self._emit("global_settings_update",
                    **asdict(self.global_settings))
        self._db_save_global_settings()

    def next_cell_name(self, group: str, cell_type: str) -> str:
        """Generate the next auto-name based on group prefix settings."""
        gs = self.get_group_settings(group)
        prefix = gs.terminal_name_prefix if cell_type == "terminal" else ""
        if not prefix:
            prefix = "Agent" if cell_type == "agent" else "Terminal"
        existing = {a.name for a in self.agents.values()
                    if a.group == group}
        i = 1
        while f"{prefix} {i}" in existing:
            i += 1
        return f"{prefix} {i}"

    # -- Slug helpers -------------------------------------------------------

    def _unique_agent_slug(self, name: str, exclude_id: str = "") -> str:
        """Generate a unique slug for an agent."""
        base = _slugify(name)
        existing = {c.slug for c in self.agents.values()
                    if c.id != exclude_id and c.slug}
        return _unique_slug(base, existing)

    def _unique_terminal_slug(self, name: str, parent_id: str = "",
                              group: str = "",
                              exclude_id: str = "") -> str:
        """Generate a unique slug for a terminal: ``parent:name``."""
        if parent_id:
            parent = self.agents.get(parent_id)
            prefix = parent.slug if parent else ""
        else:
            prefix = self.group_slugs.get(group, _slugify(group))
        base = _slugify(name)
        full = f"{prefix}:{base}" if prefix else base
        existing = {c.slug for c in self.agents.values()
                    if c.id != exclude_id and c.slug}
        return _unique_slug(full, existing)

    def _unique_group_slug(self, name: str, exclude_name: str = "") -> str:
        """Generate a unique slug for a group."""
        base = _slugify(name)
        existing = {s for n, s in self.group_slugs.items()
                    if n != exclude_name and s}
        return _unique_slug(base, existing)

    def _unique_task_slug(self, task_text: str, exclude_id: str = "") -> str:
        """Generate a unique slug for a board task."""
        base = _slugify(task_text)
        existing = {t.slug for t in self.board_tasks.values()
                    if t.id != exclude_id and t.slug}
        return _unique_slug(base, existing)

    def normalized_group_prefix(self, group_name: str) -> str:
        return normalize_group_prefix(group_name)

    def group_prefix_conflict(self, group_name: str,
                              exclude_name: str = "") -> str:
        wanted = self.normalized_group_prefix(group_name)
        for existing_name in self.groups:
            if existing_name == exclude_name:
                continue
            if self.normalized_group_prefix(existing_name) == wanted:
                return existing_name
        return ""

    def resolve_task_alias(self, task_id: str) -> str:
        """Return the canonical task ID for ``task_id``.

        Legacy aliases are the compatibility boundary for historical IDs.
        Aliases intentionally take precedence over an exact in-memory task row
        with the same ID: archived literal rows may still exist in SQLite, but
        normal reads and writes should target the live canonical task that the
        alias names.  Follow short alias chains defensively and stop on cycles.
        """
        value = str(task_id or "").strip()
        seen = set()
        while value and value in self.task_id_aliases and value not in seen:
            seen.add(value)
            next_value = str(self.task_id_aliases.get(value, "") or "").strip()
            if not next_value or next_value == value:
                break
            value = next_value
        return value

    def resolve_board_task_id(self, identifier: str, *,
                              allow_prefix: bool = True) -> str:
        """Resolve a board task ID/alias/prefix to a live canonical ID.

        The alias map is authoritative.  If an identifier is an alias whose
        target is missing from in-memory state, return an empty string instead
        of falling back to a literal archived row with the same ID.
        """
        ident = str(identifier or "").strip()
        if not ident:
            return ""

        aliased = self.resolve_task_alias(ident)
        if aliased != ident:
            return aliased if aliased in self.board_tasks else ""

        if ident in self.board_tasks:
            return ident

        if not allow_prefix:
            return ""

        matches: list[str] = []
        seen: set[str] = set()

        # Prefixes can match legacy aliases as well as canonical IDs.  When a
        # literal ID is also an alias key, hide the archived literal row and
        # expose only the alias target.
        for legacy_id in sorted(self.task_id_aliases):
            if not legacy_id.startswith(ident):
                continue
            target_id = self.resolve_task_alias(legacy_id)
            if target_id in self.board_tasks and target_id not in seen:
                matches.append(target_id)
                seen.add(target_id)

        hidden_literal_ids = set(self.task_id_aliases)
        for task_id in sorted(self.board_tasks):
            if task_id in hidden_literal_ids:
                continue
            if task_id.startswith(ident) and task_id not in seen:
                matches.append(task_id)
                seen.add(task_id)

        if len(matches) == 1:
            return matches[0]
        return ""

    def _db_board_task_exists(self, task_id: str) -> bool:
        if not self.db:
            return False
        tid = str(task_id or "").strip()
        if not tid:
            return False
        try:
            exists = getattr(self.db, "board_task_exists", None)
            if callable(exists):
                return bool(exists(tid))
            conn = getattr(self.db, "_conn", None)
            if conn is None:
                return False
            row = conn.execute(
                "SELECT 1 FROM board_tasks WHERE id=? LIMIT 1",
                (tid,),
            ).fetchone()
            return bool(row)
        except Exception:
            log.exception("Failed to check persisted task %s", tid)
            return False

    def ensure_board_task_persisted(self, task_id: str) -> bool:
        """Persist an in-memory task if its canonical row is absent in DB."""
        tid = self.resolve_task_alias(task_id)
        task = self.board_tasks.get(tid)
        if not task or not self.db:
            return False
        if self._db_board_task_exists(tid):
            return False
        self._db_save_task(task)
        return True

    def persist_missing_aliased_tasks(self) -> list[str]:
        """Persist aliased canonical tasks that only exist in memory."""
        persisted: list[str] = []
        for legacy_id in sorted(self.task_id_aliases):
            task_id = self.resolve_task_alias(legacy_id)
            if task_id and task_id in self.board_tasks:
                if self.ensure_board_task_persisted(task_id):
                    persisted.append(task_id)
        return persisted

    def _new_ephemeral_task_id(self) -> str:
        while True:
            tid = uuid.uuid4().hex[:8]
            if tid in self.board_tasks:
                continue
            if tid in self.task_id_aliases:
                continue
            if tid in set(self.task_id_aliases.values()):
                continue
            return tid

    def _alias_or_use_task_id(self, candidate_id: str) -> tuple[str, str] | None:
        """Return ``(task_id, alias_id)`` for a requested/candidate ID.

        If the candidate collides with an archived literal row, keep the
        archived row intact and create a hash-primary-key task addressed by the
        literal ID alias.  Non-archived collisions are rejected so callers do
        not accidentally hide a live task.
        """
        candidate = str(candidate_id or "").strip()
        if not candidate:
            return None
        existing = self.board_tasks.get(candidate)
        if existing:
            if board_task_is_archived(existing):
                return self._new_ephemeral_task_id(), candidate
            return None
        if candidate in self.task_id_aliases:
            target = self.resolve_task_alias(candidate)
            if target not in self.board_tasks:
                return self._new_ephemeral_task_id(), candidate
            return None
        return candidate, ""

    def _allocate_root_task_id(self, group_name: str) -> str:
        prefix = self.normalized_group_prefix(group_name)
        next_root = max(1, int(self.task_id_counters.get(prefix, 1) or 1))
        self.task_id_counters[prefix] = next_root + 1
        self._db_save_task_id_counter(prefix)
        return format_root_task_id(prefix, next_root)

    def _allocate_derived_task_id(self, group_name: str, root_task_id: str) -> str:
        root_id = self.resolve_task_alias(root_task_id)
        parsed = parse_task_id(root_id)
        if not parsed:
            raise ValueError(f"Cannot derive from non-canonical root ID: {root_task_id}")
        prefix = self.normalized_group_prefix(group_name)
        next_child = max(
            1,
            int(self.pipeline_task_counters.get(root_id, 1) or 1),
        )
        self.pipeline_task_counters[root_id] = next_child + 1
        self._db_save_pipeline_task_counter(root_id)
        return format_derived_task_id(prefix, parsed["root_number"], next_child)

    # -- Mutations ----------------------------------------------------------

    def add_group(self, name: str):
        if name and name not in self.groups \
                and not self.group_prefix_conflict(name):
            self.groups[name] = []
            self.group_slugs[name] = self._unique_group_slug(name)
            self._emit_group(name)
            self._emit("groups_reorder", groups=list(self.groups.keys()))
            self._db_save_groups()

    def remove_group(self, name: str) -> list[AgentCell]:
        removed: list[AgentCell] = []
        if name in self.groups:
            for aid in self.groups[name]:
                cell = self.agents.pop(aid, None)
                if cell:
                    removed.append(cell)
                    self._emit("agent_remove", id=aid,
                               group=cell.group,
                               cell_type=cell.cell_type)
                    # Cascade: remove child terminals
                    for child_id in self._children.pop(aid, []):
                        child = self.agents.pop(child_id, None)
                        if child:
                            removed.append(child)
                            self._emit("agent_remove", id=child_id,
                                       group=child.group,
                                       cell_type=child.cell_type)
            del self.groups[name]
            self.group_slugs.pop(name, None)
            self.group_settings.pop(name, None)
            if name in self.board_filters_by_group:
                del self.board_filters_by_group[name]
                self._emit("ui_update", key="board_filters_by_group",
                           value=self.board_filters_by_group)
                self._db_save_ui(
                    "board_filters_by_group",
                    json.dumps(self.board_filters_by_group),
                )
            if self.active_group == name:
                self.active_group = ""
                self._emit("ui_update", key="active_group",
                           value=self.active_group)
                self._db_save_ui("active_group", self.active_group)
            if name in self.board_selected_lanes_by_group:
                del self.board_selected_lanes_by_group[name]
                self._emit("ui_update", key="board_selected_lanes_by_group",
                           value=self.board_selected_lanes_by_group)
                self._db_save_ui(
                    "board_selected_lanes_by_group",
                    json.dumps(self.board_selected_lanes_by_group),
                )
            if name in self.board_hidden_wide_lanes_by_group:
                del self.board_hidden_wide_lanes_by_group[name]
                self._emit("ui_update",
                           key="board_hidden_wide_lanes_by_group",
                           value=self.board_hidden_wide_lanes_by_group)
                self._db_save_ui(
                    "board_hidden_wide_lanes_by_group",
                    json.dumps(self.board_hidden_wide_lanes_by_group),
                )
            if name in self.board_saved_views_by_group:
                del self.board_saved_views_by_group[name]
                self._emit("ui_update", key="board_saved_views_by_group",
                           value=self.board_saved_views_by_group)
                self._db_save_ui(
                    "board_saved_views_by_group",
                    json.dumps(self.board_saved_views_by_group),
                )
            if name in self.board_lane_sorts_by_group:
                del self.board_lane_sorts_by_group[name]
                self._emit("ui_update", key="board_lane_sorts_by_group",
                           value=self.board_lane_sorts_by_group)
                self._db_save_ui(
                    "board_lane_sorts_by_group",
                    json.dumps(self.board_lane_sorts_by_group),
                )
            if name in self.board_card_density_by_group:
                del self.board_card_density_by_group[name]
                self._emit("ui_update", key="board_card_density_by_group",
                           value=self.board_card_density_by_group)
                self._db_save_ui(
                    "board_card_density_by_group",
                    json.dumps(self.board_card_density_by_group),
                )
            self.auto_dispatch_queues.pop(name, None)
            self._db_delete_auto_dispatch_queue(name)
            self.engineer_settings.pop(name, None)
            self.engineer_worklog.pop(name, None)
            for r in removed:
                if r.cell_type == "agent":
                    self.history_remove_agent(r)
                self.delete_agent_digest_settings(r.id)
            if self.db:
                self.db.delete_engineer_settings(name)
            self._emit("group_remove", name=name)
            self._emit("groups_reorder", groups=list(self.groups.keys()))
            for r in removed:
                self._db_delete_agent(r.id)
            self._db_delete_group(name)
            self._db_save_groups()
        return removed

    def rename_group(self, old: str, new: str):
        if old in self.groups and new and new not in self.groups \
                and not self.group_prefix_conflict(new, exclude_name=old):
            self.groups[new] = self.groups.pop(old)
            self.group_slugs.pop(old, None)
            self.group_slugs[new] = self._unique_group_slug(new)
            if old in self.group_settings:
                self.group_settings[new] = self.group_settings.pop(old)
            if old in self.engineer_worklog:
                self.engineer_worklog[new] = self.engineer_worklog.pop(old)
                for entry in self.engineer_worklog[new]:
                    entry["group"] = new
            if old in self.board_filters_by_group:
                self.board_filters_by_group[new] = \
                    self.board_filters_by_group.pop(old)
                self._emit("ui_update", key="board_filters_by_group",
                           value=self.board_filters_by_group)
                self._db_save_ui(
                    "board_filters_by_group",
                    json.dumps(self.board_filters_by_group),
                )
            if self.active_group == old:
                self.active_group = new
                self._emit("ui_update", key="active_group",
                           value=self.active_group)
                self._db_save_ui("active_group", self.active_group)
            if old in self.board_selected_lanes_by_group:
                self.board_selected_lanes_by_group[new] = \
                    self.board_selected_lanes_by_group.pop(old)
                self._emit("ui_update", key="board_selected_lanes_by_group",
                           value=self.board_selected_lanes_by_group)
                self._db_save_ui(
                    "board_selected_lanes_by_group",
                    json.dumps(self.board_selected_lanes_by_group),
                )
            if old in self.board_hidden_wide_lanes_by_group:
                self.board_hidden_wide_lanes_by_group[new] = \
                    self.board_hidden_wide_lanes_by_group.pop(old)
                self._emit("ui_update",
                           key="board_hidden_wide_lanes_by_group",
                           value=self.board_hidden_wide_lanes_by_group)
                self._db_save_ui(
                    "board_hidden_wide_lanes_by_group",
                    json.dumps(self.board_hidden_wide_lanes_by_group),
                )
            if old in self.board_saved_views_by_group:
                self.board_saved_views_by_group[new] = \
                    self.board_saved_views_by_group.pop(old)
                self._emit("ui_update", key="board_saved_views_by_group",
                           value=self.board_saved_views_by_group)
                self._db_save_ui(
                    "board_saved_views_by_group",
                    json.dumps(self.board_saved_views_by_group),
                )
            if old in self.board_lane_sorts_by_group:
                self.board_lane_sorts_by_group[new] = \
                    self.board_lane_sorts_by_group.pop(old)
                self._emit("ui_update", key="board_lane_sorts_by_group",
                           value=self.board_lane_sorts_by_group)
                self._db_save_ui(
                    "board_lane_sorts_by_group",
                    json.dumps(self.board_lane_sorts_by_group),
                )
            if old in self.board_card_density_by_group:
                self.board_card_density_by_group[new] = \
                    self.board_card_density_by_group.pop(old)
                self._emit("ui_update", key="board_card_density_by_group",
                           value=self.board_card_density_by_group)
                self._db_save_ui(
                    "board_card_density_by_group",
                    json.dumps(self.board_card_density_by_group),
                )
            for aid in self.groups[new]:
                if aid in self.agents:
                    self.agents[aid].group = new
                    self._emit_agent(self.agents[aid])
                    for child_id in self._children.get(aid, []):
                        if child_id in self.agents:
                            self.agents[child_id].group = new
                            self._emit_agent(self.agents[child_id])
            self._emit("group_rename", old_name=old, new_name=new,
                       slug=self.group_slugs.get(new, ""))
            # Persist: rename group, update agent group fields
            self._db_save_groups()
            if new in self.group_settings:
                self._db_save_group_settings(new)
            if self.db:
                self.db.rename_engineer_task_log_group(old, new)
            for aid in self.groups[new]:
                if aid in self.agents:
                    self._db_save_agent(self.agents[aid])
                    for child_id in self._children.get(aid, []):
                        if child_id in self.agents:
                            self._db_save_agent(self.agents[child_id])

    def _add_cell(
        self,
        name: str,
        group: str,
        cell_type: str,
        terminal_backend: str = "",
        profile: str = "Default",
        command: str = "",
        directory: str = "",
        tab_color: str = "",
        icon: str = "",
        parent_id: str = "",
    ) -> Optional[AgentCell]:
        # Child terminals inherit group from parent
        if parent_id:
            parent = self.agents.get(parent_id)
            if not parent or parent.cell_type != "agent":
                return None
            group = parent.group
        elif group not in self.groups:
            return None
        gs = self.get_group_settings(group)
        # Max agents cap
        if cell_type == "agent" and not parent_id:
            if gs.max_agents > 0:
                current = sum(1 for aid in self.groups.get(group, [])
                              if self.agents.get(aid)
                              and self.agents[aid].cell_type == "agent")
                if current >= gs.max_agents:
                    log.warning("Group '%s' at max_agents cap (%d)",
                                group, gs.max_agents)
                    return None
        aid = uuid.uuid4().hex[:8]
        if cell_type == "terminal":
            slug = self._unique_terminal_slug(name, parent_id=parent_id,
                                              group=group)
        else:
            slug = self._unique_agent_slug(name)
        cell = AgentCell(
            id=aid,
            name=name,
            group=group,
            slug=slug,
            cell_type=cell_type,
            terminal_backend=terminal_backend
            or gs.default_terminal_backend
            or "iterm2",
            profile=profile,
            command=command or (self.get_default_command() if cell_type == "agent" else ""),
            directory=directory,
            tab_color=tab_color,
            icon=icon,
            parent_id=parent_id,
        )
        self.agents[aid] = cell
        if parent_id:
            self._children.setdefault(parent_id, []).append(aid)
        else:
            self.groups[group].append(aid)
        if cell_type == "agent":
            self._children[aid] = []
        self._emit_agent(cell)
        if parent_id:
            self._emit_agent(self.agents[parent_id])  # children changed
        else:
            self._emit_group(group)
        self._db_save_agent(cell)
        self._db_save_groups()
        log.info("Cell created: id=%s type=%s parent=%s tab_color=%r "
                 "directory=%r", aid, cell_type, parent_id or "none",
                 cell.tab_color, cell.directory)
        return cell

    def update_agent(self, aid: str, **fields):
        """Update mutable fields on an existing cell."""
        cell = self.agents.get(aid)
        if not cell:
            return
        for key in ("name", "tab_color", "icon"):
            if key in fields:
                setattr(cell, key, fields[key])
        if "name" in fields:
            if cell.cell_type == "terminal":
                cell.slug = self._unique_terminal_slug(
                    cell.name, parent_id=cell.parent_id,
                    group=cell.group, exclude_id=aid)
            else:
                cell.slug = self._unique_agent_slug(cell.name,
                                                    exclude_id=aid)
                # Cascade: update children's slug prefixes
                for child_id in self._children.get(aid, []):
                    child = self.agents.get(child_id)
                    if child:
                        child.slug = self._unique_terminal_slug(
                            child.name, parent_id=aid,
                            group=child.group, exclude_id=child_id)
                        self._emit_agent(child)
                        self._db_save_agent(child)
        self._emit_agent(cell)
        self._db_save_agent(cell)

    def add_agent(self, **kw) -> Optional[AgentCell]:
        return self._add_cell(cell_type="agent", **kw)

    def add_terminal(self, **kw) -> Optional[AgentCell]:
        kw.setdefault("command", "")
        return self._add_cell(cell_type="terminal", **kw)

    def _agent_cascade_cells(self, aid: str) -> list[AgentCell]:
        """Return a root cell plus child terminals in deletion order."""
        cell = self.agents.get(str(aid or "").strip())
        if not cell:
            return []
        ordered: list[AgentCell] = []
        seen: set[str] = set()

        def add(cell_id: str) -> None:
            if not cell_id or cell_id in seen:
                return
            seen.add(cell_id)
            current = self.agents.get(cell_id)
            if not current:
                return
            ordered.append(current)
            for child_id in list(self._children.get(cell_id, [])):
                add(child_id)

        add(cell.id)
        return ordered

    def _prepare_tombstoned_cell(self, cell: AgentCell, now: float) -> None:
        cell.deleted_at = now
        cell.permanent_delete_after = now + AGENT_TOMBSTONE_RETENTION_SECONDS
        cell.status = "stopped"
        cell.session_id = None
        cell.current_task_id = ""
        cell.current_process = ""
        cell.current_path = ""
        cell.current_branch = ""
        cell.git_root = ""
        cell.activity = ""
        cell.activity_detail = ""
        cell.error_message = ""
        cell.needs_attention = False

    def _hard_delete_agent(self, aid: str, *,
                           record_history: bool = True) -> list[AgentCell]:
        removed: list[AgentCell] = []
        cell = self.agents.pop(aid, None)
        if not cell:
            return removed
        removed.append(cell)
        self._emit("agent_remove", id=aid,
                   group=cell.group, cell_type=cell.cell_type)
        # Remove from group list (top-level items only)
        if not cell.parent_id and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
            self._emit_group(cell.group)
        # If this is a child terminal, remove from parent's children list
        if cell.parent_id and cell.parent_id in self._children:
            self._children[cell.parent_id] = [
                x for x in self._children[cell.parent_id] if x != aid
            ]
        # Cascade: remove child terminals
        for child_id in self._children.pop(aid, []):
            child = self.agents.pop(child_id, None)
            if child:
                removed.append(child)
                self._emit("agent_remove", id=child_id,
                           group=child.group,
                           cell_type=child.cell_type)
        # Unlink from board tasks
        removed_ids = {r.id for r in removed}
        for t in self.board_tasks.values():
            if t.agent_id in removed_ids:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
        # Clear engineer designation only when the row is permanently purged.
        gs = self.group_settings.get(cell.group)
        if gs and gs.engineer_agent_id == aid:
            gs.engineer_agent_id = ""
            self._emit("group_settings_update", name=cell.group, **asdict(gs))
            self._db_save_group_settings(cell.group)
        for r in removed:
            if record_history and r.cell_type == "agent":
                self.history_remove_agent(r)
            self.delete_agent_digest_settings(r.id)
            self._db_delete_agent(r.id)
        self.cleanup_orphaned_attention(allow_persisted_agent_fallback=False)
        self._db_save_groups()
        return removed

    def remove_agent(self, aid: str) -> list[AgentCell]:
        """Soft-delete an agent cell for the 7-day restore window.

        Standalone terminals remain immediate hard deletes; soft-delete is for
        agent cells and child terminals that cascade from an agent tombstone.
        """
        cell = self.agents.get(aid)
        if not cell:
            return []
        if cell.cell_type == "terminal":
            return self._hard_delete_agent(aid)

        now = time.time()
        tombstoned = self._agent_cascade_cells(aid)
        tombstoned_ids = {c.id for c in tombstoned}
        for target in tombstoned:
            self._prepare_tombstoned_cell(target, now)
            self._emit_agent(target)
            self._db_save_agent(target)
            if target.cell_type == "agent":
                self.history_remove_agent(target)

        # Existing hard-delete semantics detached tasks from the deleted cell.
        # Keep that irreversible transfer at tombstone time so active routing
        # never targets a hidden/restorable cell.
        for t in self.board_tasks.values():
            if t.agent_id in tombstoned_ids:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)

        self.cleanup_orphaned_attention(allow_persisted_agent_fallback=False)
        return tombstoned

    def restore_agent(self, aid: str) -> list[AgentCell]:
        """Restore a tombstoned agent and its tombstoned child terminals."""
        cell = self.agents.get(str(aid or "").strip())
        if not cell:
            return []
        targets = self._agent_cascade_cells(cell.id)
        restored: list[AgentCell] = []
        for target in targets:
            if not self.agent_is_tombstoned(target):
                continue
            target.deleted_at = 0.0
            target.permanent_delete_after = 0.0
            restored.append(target)
            self._emit_agent(target)
            self._db_save_agent(target)
        return restored

    def purge_agent_now(self, aid: str) -> list[AgentCell]:
        """Permanently delete an agent/tombstone immediately."""
        return self._hard_delete_agent(str(aid or "").strip())

    def purge_tombstoned_agents(self, now: float | None = None) -> list[AgentCell]:
        """Permanently delete tombstones whose restore window has expired."""
        ts = _safe_float(now if now is not None else time.time())
        purged: list[AgentCell] = []
        due_ids = [
            cell.id for cell in list(self.agents.values())
            if self.agent_is_tombstoned(cell)
            and _safe_float(getattr(cell, "permanent_delete_after", 0.0)) <= ts
        ]
        for aid in due_ids:
            if aid not in self.agents:
                continue
            purged.extend(self._hard_delete_agent(aid))
        return purged

    def move_agent(self, aid: str, target_group: str, before: str = ""):
        cell = self.agents.get(aid)
        if not cell or target_group not in self.groups:
            return
        old_group = cell.group
        # Detach from parent if this is a child terminal being moved
        if cell.parent_id:
            old_parent = cell.parent_id
            if old_parent in self._children:
                self._children[old_parent] = [
                    x for x in self._children[old_parent] if x != aid
                ]
            cell.parent_id = ""
        # Remove from group list (may not be there if was a child)
        if cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
        if before and before in self.groups[target_group]:
            idx = self.groups[target_group].index(before)
            self.groups[target_group].insert(idx, aid)
        else:
            self.groups[target_group].append(aid)
        cell.group = target_group
        self._emit_agent(cell)
        # Move children along
        for child_id in self._children.get(aid, []):
            child = self.agents.get(child_id)
            if child:
                child.group = target_group
                self._emit_agent(child)
        if old_group != target_group:
            self._emit_group(old_group)
        self._emit_group(target_group)
        self._db_save_agent(cell)
        for child_id in self._children.get(aid, []):
            child = self.agents.get(child_id)
            if child:
                self._db_save_agent(child)
        self._db_save_groups()

    def reorder_child(self, aid: str, parent_id: str, before: str = ""):
        """Reorder a child terminal within its parent's children list."""
        if parent_id not in self._children:
            return
        children = self._children[parent_id]
        if aid not in children:
            return
        children.remove(aid)
        if before and before in children:
            idx = children.index(before)
            children.insert(idx, aid)
        else:
            children.append(aid)
        # Children order is derived from _children, emit parent for rebuild
        self._emit_agent(self.agents[parent_id])
        # Children order is in-memory only (_children), group_members tracks it
        self._db_save_groups()

    def reparent_terminal(self, aid: str, new_parent_id: str):
        """Attach a terminal to an agent (or detach if new_parent_id is empty)."""
        cell = self.agents.get(aid)
        if not cell or cell.cell_type != "terminal":
            return
        new_parent = self.agents.get(new_parent_id) if new_parent_id else None
        if new_parent_id and (not new_parent or new_parent.cell_type != "agent"):
            return
        old_parent_id = cell.parent_id
        # Detach from old parent
        if cell.parent_id and cell.parent_id in self._children:
            self._children[cell.parent_id] = [
                x for x in self._children[cell.parent_id] if x != aid
            ]
        # Remove from group list if standalone
        if not cell.parent_id and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
        # Attach to new parent
        if new_parent_id:
            cell.parent_id = new_parent_id
            cell.group = new_parent.group
            self._children.setdefault(new_parent_id, []).append(aid)
        else:
            cell.parent_id = ""
            if cell.group in self.groups:
                self.groups[cell.group].append(aid)
        # Regenerate slug with new parent prefix
        cell.slug = self._unique_terminal_slug(
            cell.name, parent_id=cell.parent_id,
            group=cell.group, exclude_id=aid)
        self._emit_agent(cell)
        if old_parent_id and old_parent_id in self.agents:
            self._emit_agent(self.agents[old_parent_id])
        if new_parent_id:
            self._emit_agent(new_parent)
        self._emit_group(cell.group)
        self._db_save_agent(cell)
        if old_parent_id and old_parent_id in self.agents:
            self._db_save_agent(self.agents[old_parent_id])
        self._db_save_groups()

    def move_group(self, name: str, before: str = ""):
        if name not in self.groups:
            return
        items = [(k, v) for k, v in self.groups.items() if k != name]
        value = self.groups[name]
        idx = next((i for i, (k, _) in enumerate(items) if k == before), -1)
        if idx >= 0:
            items.insert(idx, (name, value))
        else:
            items.append((name, value))
        self.groups = dict(items)
        self._emit("groups_reorder", groups=list(self.groups.keys()))
        self._db_save_groups()

    def cells_with_awareness(self) -> list[AgentCell]:
        """Return cells that have agent awareness active (agent_type set)."""
        return [c for c in self.iter_active_agents() if c.agent_type]

    # -- Board (Phase 5) ---------------------------------------------------

    def _board_reindex(self, lane: str):
        """Reindex positions for all tasks in a lane."""
        tasks = sorted(
            [t for t in self.board_tasks.values() if t.lane == lane],
            key=lambda t: t.position,
        )
        for i, t in enumerate(tasks):
            t.position = i

    def _task_health_ancestors(self, task_id: str) -> list[str]:
        ancestors = []
        seen = {task_id}
        task = self.board_tasks.get(task_id)
        pid = getattr(task, "parent_task_id", "") if task else ""
        while pid and pid not in seen:
            seen.add(pid)
            parent = self.board_tasks.get(pid)
            if not parent:
                break
            ancestors.append(pid)
            pid = parent.parent_task_id
        return ancestors

    def _task_health_depth(self, task_id: str) -> int:
        depth = 0
        seen = {task_id}
        task = self.board_tasks.get(task_id)
        pid = getattr(task, "parent_task_id", "") if task else ""
        while pid and pid not in seen:
            seen.add(pid)
            parent = self.board_tasks.get(pid)
            if not parent:
                break
            depth += 1
            pid = parent.parent_task_id
        return depth

    def _task_health_context(self, task: BoardTask) -> dict[str, BoardTask]:
        ids = {task.id}
        ids.update(getattr(task, "depends_on", []) or [])
        ids.update(self._tasks_by_parent.get(task.id, set()))
        agent_id = str(getattr(task, "agent_id", "") or "")
        if agent_id:
            ids.update(self._tasks_by_agent.get(agent_id, set()))
        return {
            tid: task
            for tid in ids
            if (task := self.board_tasks.get(tid))
            and task.lane != ARCHIVED_LANE
        }

    def _compute_incremental_task_health(self, task_ids: set[str],
                                         now_ts: float | None):
        from .task_health import (
            ARCHIVED_LANE as HEALTH_ARCHIVED_LANE,
            TaskHealthSnapshot,
            _compute_local_health,
            _roll_up_health,
        )
        if now_ts is None:
            from datetime import datetime, timezone
            now_ts = datetime.now(timezone.utc).timestamp()

        target_ids: set[str] = set()
        for tid in task_ids:
            if tid not in self.board_tasks:
                continue
            target_ids.add(tid)
            target_ids.update(self._task_health_ancestors(tid))

        snapshots = {}
        # Children before parents so aggregate rollups see fresh snapshots
        # for the changed path and stored snapshots for untouched siblings.
        ordered = sorted(
            target_ids,
            key=lambda tid: (self._task_health_depth(tid), tid),
            reverse=True,
        )
        for tid in ordered:
            task = self.board_tasks.get(tid)
            if not task or task.lane == HEALTH_ARCHIVED_LANE:
                continue
            local = _compute_local_health(
                task,
                self._task_health_context(task),
                self.agents,
                now_ts,
            )
            child_snapshots = []
            for child_id in self._tasks_by_parent.get(tid, set()):
                child = self.board_tasks.get(child_id)
                if not child or child.lane in {"Done", HEALTH_ARCHIVED_LANE}:
                    continue
                snapshot = snapshots.get(child_id)
                if snapshot is None:
                    snapshot = TaskHealthSnapshot(
                        state=child.health_state or "healthy",
                        details=dict(child.health_details or {}),
                    )
                child_snapshots.append(snapshot)
            snapshots[tid] = _roll_up_health(
                task,
                local,
                child_snapshots,
                self.board_tasks,
            )
        return snapshots

    def recompute_task_health(self, now_ts: float | None = None,
                              *, emit: bool = True,
                              persist: bool = True) -> list[str]:
        """Recompute advisory health for dirty tasks and their ancestors.

        Health is deterministic and derived from persisted task signals plus
        live agent state. It never mutates task lanes or statuses. Routine
        broadcast ticks use the dirty set and become a near-no-op when no
        health-affecting deltas have been queued; explicit timestamped calls
        still run a full scan for time-based idle/stalled transitions.
        """
        if not self.board_tasks:
            self._task_health_dirty.clear()
            self._task_health_force_full = False
            return []

        force_full = self._task_health_force_full or now_ts is not None
        dirty_ids = set(self._task_health_dirty)
        if not force_full and not dirty_ids:
            return []

        from .task_health import compute_task_health, now_iso

        if force_full:
            snapshots = compute_task_health(self.board_tasks, self.agents,
                                            now_ts=now_ts)
        else:
            snapshots = self._compute_incremental_task_health(
                dirty_ids,
                now_ts,
            )
        changed = []
        changed_set = set()
        changed_task_ids = []
        snapshot_now = now_iso(now_ts)
        for tid, snapshot in snapshots.items():
            task = self.board_tasks.get(tid)
            if not task:
                continue
            old_state = task.health_state or "healthy"
            old_source = ""
            if isinstance(task.health_details, dict):
                old_source = task.health_details.get("source_task_id", "")
            new_source = snapshot.details.get("source_task_id", "")
            state_changed = old_state != snapshot.state
            source_changed = old_source != new_source
            next_since = task.health_since
            if state_changed or source_changed or not next_since:
                next_since = snapshot_now
            if (task.health_state == snapshot.state
                    and task.health_since == next_since
                    and task.health_details == snapshot.details):
                continue
            task.health_state = snapshot.state
            task.health_since = next_since
            task.health_details = snapshot.details
            changed.append(tid)
            changed_set.add(tid)
            changed_task_ids.append(task)

        if not changed:
            self._task_health_dirty.clear()
            self._task_health_force_full = False
            return []

        # If a task changes, re-emit and persist any open ancestors so root
        # cards can reflect descendant health without waiting for their own
        # direct mutation.
        for task in list(changed_task_ids):
            pid = task.parent_task_id
            while pid:
                parent = self.board_tasks.get(pid)
                if not parent or pid in changed_set:
                    break
                changed_set.add(pid)
                changed.append(pid)
                changed_task_ids.append(parent)
                pid = parent.parent_task_id

        for tid in changed:
            task = self.board_tasks.get(tid)
            if not task:
                continue
            if emit:
                self._suppress_task_health_dirty = True
                try:
                    self._emit("task_upsert", **asdict(task))
                finally:
                    self._suppress_task_health_dirty = False
            if persist and self.db:
                self._db_save_task(task)
        self._task_health_dirty.clear()
        self._task_health_force_full = False
        return changed

    def _board_next_lane_position(self, lane: str, *, exclude_id: str = "") -> int:
        return max(
            (t.position for t in self.board_tasks.values()
             if t.lane == lane and t.id != exclude_id),
            default=-1,
        ) + 1

    def _board_apply_archive_state(self, task: BoardTask, *,
                                   lane: str,
                                   archived_at: str,
                                   archived_from_lane: str,
                                   position: Optional[int] = None,
                                   clear_attention: bool = False,
                                   unlink_agent: bool = False):
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        old_lane = task.lane
        task.lane = lane
        task.archived_at = archived_at
        task.archived_from_lane = archived_from_lane
        if lane == ARCHIVED_LANE:
            task.health_state = "healthy"
            task.health_since = now_iso
        if clear_attention:
            for label in ("torque:blocked", "torque:error"):
                if label in task.labels:
                    task.labels.remove(label)
        if unlink_agent:
            task.agent_id = ""
        if position is not None:
            task.position = position
        else:
            task.position = self._board_next_lane_position(
                lane, exclude_id=task.id
            )
        task.updated_at = now_iso
        if old_lane != lane:
            task.lane_entered_at = now_iso
        self._emit("task_upsert", **asdict(task))
        self._db_save_task(task)

    def board_add_task(self, task: str, group: str, lane: str = "",
                       **kwargs) -> Optional[BoardTask]:
        if not task:
            return None
        if not group or group not in self.groups:
            return None
        if not lane:
            lane = self.board_lanes[0] if self.board_lanes else "Backlog"
        if lane not in self.board_lanes:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        explicit_id = kwargs.pop("id", None)
        parent_task_id = self.resolve_task_alias(
            kwargs.get("parent_task_id", "") or ""
        )
        pipeline_root_id = self.resolve_task_alias(
            kwargs.get("pipeline_root_id", "") or ""
        )
        if parent_task_id:
            kwargs["parent_task_id"] = parent_task_id
        if pipeline_root_id:
            kwargs["pipeline_root_id"] = pipeline_root_id
        alias_id = ""
        if explicit_id:
            resolved = self._alias_or_use_task_id(explicit_id)
            if not resolved:
                return None
            tid, alias_id = resolved
        elif parent_task_id or pipeline_root_id:
            root_id = pipeline_root_id or parent_task_id
            try:
                candidate_id = self._allocate_derived_task_id(group, root_id)
                resolved = self._alias_or_use_task_id(candidate_id)
                if not resolved:
                    return None
                tid, alias_id = resolved
            except ValueError:
                tid = self._new_ephemeral_task_id()
        else:
            while True:
                candidate_id = self._allocate_root_task_id(group)
                resolved = self._alias_or_use_task_id(candidate_id)
                if resolved:
                    tid, alias_id = resolved
                    break
        task_slug = self._unique_task_slug(task)
        # Validate depends_on: strip non-existent IDs
        if "depends_on" in kwargs:
            deps = kwargs["depends_on"]
            if isinstance(deps, list):
                normalized = []
                for dep_id in deps:
                    resolved_dep = self.resolve_task_alias(dep_id)
                    if resolved_dep in self.board_tasks:
                        normalized.append(resolved_dep)
                kwargs["depends_on"] = normalized
            else:
                kwargs.pop("depends_on", None)
        if "attachments" in kwargs:
            kwargs["attachments"] = normalize_attachments(
                kwargs["attachments"])
        if "artifacts" in kwargs:
            kwargs["artifacts"] = normalize_artifacts(kwargs["artifacts"])
        if "messages_thread" in kwargs:
            kwargs["messages_thread"] = _normalize_messages_thread(
                kwargs["messages_thread"]
            )
        if "board_sync" in kwargs:
            kwargs["board_sync"] = _normalize_board_sync(kwargs["board_sync"])
        _normalize_verification_fields(kwargs)
        bt = BoardTask(
            id=tid,
            task=task,
            slug=task_slug,
            group=group,
            lane=lane,
            position=self._board_next_lane_position(lane),
            created_at=now,
            updated_at=now,
            lane_entered_at=now,
            **{k: v for k, v in kwargs.items()
               if k in BoardTask.__dataclass_fields__ and k not in
               ("id", "task", "slug", "group", "lane", "position",
                "created_at", "updated_at", "lane_entered_at")},
        )
        self.board_tasks[tid] = bt
        if alias_id and alias_id != tid:
            self.task_id_aliases[alias_id] = tid
            self._db_save_task_id_alias(alias_id)
        if is_canonical_task_id(tid):
            parsed = parse_task_id(tid)
            if parsed:
                prefix = parsed["prefix"]
                self.task_id_counters[prefix] = max(
                    self.task_id_counters.get(prefix, 1),
                    parsed["root_number"] + 1,
                )
                self._db_save_task_id_counter(prefix)
                if parsed["child_number"] is not None:
                    root_id = kwargs.get("pipeline_root_id", "") or format_root_task_id(
                        prefix, parsed["root_number"]
                    )
                    self.pipeline_task_counters[root_id] = max(
                        self.pipeline_task_counters.get(root_id, 1),
                        parsed["child_number"] + 1,
                    )
                    self._db_save_pipeline_task_counter(root_id)
        self._emit("task_upsert", **asdict(bt))
        self._db_save_task(bt)
        self.recompute_task_health()
        return bt

    def board_update_task(self, tid: str, **fields):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            return
        self.ensure_board_task_persisted(tid)
        # Validate depends_on: strip self-refs, missing IDs, cycles
        if "depends_on" in fields:
            deps = fields["depends_on"]
            if not isinstance(deps, list):
                deps = []
            deps = [
                self.resolve_task_alias(d) for d in deps
                if self.resolve_task_alias(d) != tid
                and self.resolve_task_alias(d) in self.board_tasks
            ]
            if self._board_check_dep_cycle(tid, deps):
                return  # would create a cycle
            fields["depends_on"] = deps
        if "attachments" in fields:
            fields["attachments"] = normalize_attachments(
                fields["attachments"])
        if "artifacts" in fields:
            fields["artifacts"] = normalize_artifacts(fields["artifacts"])
        if "messages_thread" in fields:
            fields["messages_thread"] = _normalize_messages_thread(
                fields["messages_thread"]
            )
        if "board_sync" in fields:
            fields["board_sync"] = _normalize_board_sync(fields["board_sync"])
        _normalize_verification_fields(fields)
        valid = set(BoardTask.__dataclass_fields__) - {"id", "slug", "created_at"}
        old_lane = task.lane
        new_lane = fields.get("lane", old_lane)
        lane_changed = "lane" in fields and new_lane != old_lane
        if "lane" in fields and new_lane not in self.board_lanes:
            return
        if lane_changed and (new_lane == ARCHIVED_LANE or old_lane == ARCHIVED_LANE):
            archive_position = fields.pop("position", None)
            fields.pop("lane", None)
            if fields:
                self.board_update_task(tid, **fields)
            if new_lane == ARCHIVED_LANE:
                self.board_archive_task(tid, position=archive_position)
            else:
                self.board_unarchive_task(
                    tid, lane=new_lane, position=archive_position
                )
            return
        for key, value in fields.items():
            if key in valid:
                setattr(task, key, value)
        if "task" in fields:
            task.slug = self._unique_task_slug(task.task, exclude_id=tid)
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        task.updated_at = now_iso
        if lane_changed:
            task.lane_entered_at = now_iso
            if "position" not in fields:
                task.position = self._board_next_lane_position(
                    new_lane, exclude_id=tid
                )
        self._emit("task_upsert", **asdict(task))
        self._db_save_task(task)
        if lane_changed and new_lane == "Done":
            self.board_cascade_done(tid, recompute=False)
        self.recompute_task_health()

    def board_remove_task(self, tid: str):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.pop(tid, None)
        if task:
            self._mark_task_health_dirty(task.parent_task_id)
            if (
                    task.agent_id
                    and self._task_lane_counts_as_agent_open(task.lane)
            ):
                self._mark_task_health_dirty(
                    *self._tasks_by_agent.get(task.agent_id, set())
                )
            self._unindex_task(task)
            self.auto_dispatch_queue_remove_task(tid)
            self._emit("task_remove", id=tid, group=task.group)
            self._db_delete_task(tid)
            # Clean up dependency references in other tasks
            for t in self.board_tasks.values():
                if tid in t.depends_on:
                    t.depends_on.remove(tid)
                    self._emit("task_upsert", **asdict(t))
                    self._db_save_task(t)
            if not self.cleanup_stale_boundary_successors():
                self.recompute_task_health()

    def board_move_task(self, tid: str, lane: str,
                        position: Optional[int] = None,
                        clear_status: bool = False):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or lane not in self.board_lanes:
            return
        if lane == ARCHIVED_LANE and task.lane == ARCHIVED_LANE:
            if clear_status and task.status:
                self.board_update_task(tid, status="")
            return
        if clear_status:
            task.status = ""
        if lane == ARCHIVED_LANE:
            self.board_archive_task(tid, position=position)
            return
        if task.lane == ARCHIVED_LANE:
            self.board_unarchive_task(tid, lane=lane, position=position)
            return
        old_lane = task.lane
        self._board_apply_archive_state(
            task,
            lane=lane,
            archived_at="",
            archived_from_lane="",
            position=position,
            clear_attention=(lane == "Done"),
        )
        if lane == "Done":
            self.board_cascade_done(tid, recompute=False)
        self.recompute_task_health()

    def _append_engineer_message_expiry_note(self, task: BoardTask,
                                             timestamp: float) -> bool:
        if any(
                message.get("action") == "system"
                and message.get("message") == _ENGINEER_MESSAGE_EXPIRY_NOTE
                for message in (task.messages or [])):
            return False
        task.messages.append({
            "timestamp": timestamp,
            "action": "system",
            "message": _ENGINEER_MESSAGE_EXPIRY_NOTE,
            "agent_name": "Torque",
        })
        return True

    def _sync_pending_engineer_message_for_agent(
            self, agent_id: str, *, emit: bool = True) -> bool:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return False
        cell = self.agents.get(agent_id)
        if not cell:
            return False
        pending = bool(self.agent_pending_engineer_reply_tasks(agent_id))
        if cell.pending_engineer_message == pending:
            return False
        cell.pending_engineer_message = pending
        if emit:
            self._emit_agent(cell)
        return True

    def _expire_engineer_message_task(self, task: BoardTask, *,
                                      emit: bool = True) -> bool:
        if (
                not task_is_engineer_message_followup(task)
                or board_task_is_closed(task)
        ):
            return False
        if "Done" not in self.board_lanes:
            return False

        now = datetime.now(timezone.utc)
        changed = self._append_engineer_message_expiry_note(
            task, now.timestamp()
        )
        if task.status:
            task.status = ""
            changed = True
        if task.lane != "Done" or task.archived_at or task.archived_from_lane:
            if emit:
                self._board_apply_archive_state(
                    task,
                    lane="Done",
                    archived_at="",
                    archived_from_lane="",
                    clear_attention=True,
                )
            else:
                old_lane = task.lane
                task.lane = "Done"
                task.archived_at = ""
                task.archived_from_lane = ""
                task.position = self._board_next_lane_position(
                    "Done", exclude_id=task.id
                )
                for label in ("torque:blocked", "torque:error"):
                    if label in task.labels:
                        task.labels.remove(label)
                task.updated_at = now.isoformat()
                if old_lane != "Done":
                    task.lane_entered_at = task.updated_at
                self._index_task(task)
                self._db_save_task(task)
            return True
        if changed:
            task.updated_at = now.isoformat()
            if emit:
                self._emit("task_upsert", **asdict(task))
            else:
                self._index_task(task)
            self._db_save_task(task)
            return True
        return False

    def expire_engineer_message_descendants(
            self, parent_task_id: str, *, emit: bool = True) -> int:
        """Expire open Engineer-message follow-ups under a resolved parent."""
        expired = 0
        reply_agent_ids: set[str] = set()
        for descendant in self.task_open_descendants(parent_task_id):
            if not task_is_engineer_message_followup(descendant):
                continue
            reply_agent_id = str(
                getattr(descendant, "reply_agent_id", "") or ""
            ).strip()
            if self._expire_engineer_message_task(descendant, emit=emit):
                expired += 1
                if reply_agent_id:
                    reply_agent_ids.add(reply_agent_id)

        for agent_id in reply_agent_ids:
            self._sync_pending_engineer_message_for_agent(agent_id, emit=emit)
        return expired

    def cleanup_resolved_engineer_message_followups(
            self, *, emit: bool = True) -> int:
        """Expire historical Engineer-message ghosts below resolved parents.

        This is intentionally idempotent: already-Done/archived follow-ups are
        not returned by ``task_open_descendants`` and therefore are not touched
        on subsequent runs.
        """
        expired = 0
        for task in sorted(
                self.board_tasks.values(),
                key=lambda task: (task.pipeline_depth, task.created_at, task.id),
        ):
            if task_counts_as_done(task):
                expired += self.expire_engineer_message_descendants(
                    task.id,
                    emit=emit,
                )
        return expired

    def board_cascade_done(self, tid: str, *,
                           recompute: bool = True) -> list[str]:
        """Complete ancestors whose entire descendant tree is done.

        Derived tasks suspend their parents in an active lane while follow-up
        work runs.  Once a descendant lands in Done and there are no open
        descendants left under an ancestor, that ancestor should also count as
        complete.  Keep this in the state layer so board moves, server-side
        reports, and other mutation paths all share the same cascade behavior.
        """
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or not task_counts_as_done(task):
            return []
        expired = self.expire_engineer_message_descendants(tid)
        if task_suppresses_done_cascade(task):
            if expired and recompute:
                self.recompute_task_health()
            return []
        if "Done" not in self.board_lanes:
            return []

        changed: list[str] = []
        self._cascade_review_handoff_completions(task, changed)
        pid = task.parent_task_id
        while pid:
            parent = self.board_tasks.get(pid)
            if not parent:
                break
            next_pid = parent.parent_task_id
            if task_suppresses_done_cascade(parent):
                break
            if board_task_is_closed(parent):
                pid = next_pid
                continue
            if self.task_has_unresolved_descendants(parent.id):
                break

            parent.status = ""
            self._board_apply_archive_state(
                parent,
                lane="Done",
                archived_at="",
                archived_from_lane="",
                clear_attention=True,
            )
            changed.append(parent.id)
            self._cascade_review_handoff_completions(parent, changed)
            pid = next_pid

        if (changed or expired) and recompute:
            self.recompute_task_health()
        return changed

    def board_archive_task(self, tid: str, *,
                           position: Optional[int] = None):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or ARCHIVED_LANE not in self.board_lanes:
            return
        if task.lane == ARCHIVED_LANE:
            return
        archived_from_lane = task.lane
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._board_apply_archive_state(
            task,
            lane=ARCHIVED_LANE,
            archived_at=now_iso,
            archived_from_lane=archived_from_lane,
            position=position,
            unlink_agent=True,
        )
        if archived_from_lane == "Done":
            self.expire_engineer_message_descendants(tid)
        parent = self.board_tasks.get(task.parent_task_id)
        self._clear_parent_awaiting_input(parent, exclude_task_id=task.id)
        self.recompute_task_health()

    def board_archive_tasks(self, tids) -> list[str]:
        """Archive multiple board tasks as one atomic persisted operation."""
        requested: list[str] = []
        missing: list[str] = []
        seen: set[str] = set()
        for raw_tid in tids or []:
            tid = self.resolve_task_alias(str(raw_tid or ""))
            if not tid or tid not in self.board_tasks:
                missing.append(str(raw_tid or ""))
                continue
            if tid in seen:
                continue
            seen.add(tid)
            requested.append(tid)
        if missing:
            raise ValueError(
                "Task not found: " + ", ".join(tid or "(empty)" for tid in missing)
            )
        if not requested:
            return []
        if ARCHIVED_LANE not in self.board_lanes:
            raise ValueError("Archived lane is not configured")

        archive_targets = [
            tid for tid in requested
            if self.board_tasks[tid].lane != ARCHIVED_LANE
        ]
        if not archive_targets:
            return []

        before_tasks = {
            tid: copy.deepcopy(task)
            for tid, task in self.board_tasks.items()
        }
        before_agents = {
            aid: copy.deepcopy(agent)
            for aid, agent in self.agents.items()
        }
        before_delta_len = len(self._delta_ops)
        before_health_dirty = set(self._task_health_dirty)
        before_health_force_full = self._task_health_force_full
        existing_capture = self._current_critical_write_capture()
        before_capture = copy.deepcopy(existing_capture) if existing_capture else None
        temp_capture = bool(self.db and existing_capture is None)

        if temp_capture:
            self._critical_write_capture_var.set(CriticalWriteCapture(
                command_name="board_archive_tasks",
                idempotency_key="",
                request_hash="",
            ))

        try:
            for tid in archive_targets:
                self.board_archive_task(tid)

            if temp_capture:
                capture = self._current_critical_write_capture()
                self._critical_write_capture_var.set(None)
                tasks_to_save = list((capture.tasks if capture else {}).values())
                if tasks_to_save:
                    self.db.save_board_tasks(tasks_to_save)
        except Exception:
            if temp_capture:
                self._critical_write_capture_var.set(None)
            elif existing_capture is not None:
                self._critical_write_capture_var.set(before_capture)
            self.board_tasks = before_tasks
            self.agents = before_agents
            self._rebuild_task_indexes()
            self._delta_ops = self._delta_ops[:before_delta_len]
            self._task_health_dirty = before_health_dirty
            self._task_health_force_full = before_health_force_full
            raise

        return [
            tid for tid in archive_targets
            if self.board_tasks.get(tid)
            and self.board_tasks[tid].lane == ARCHIVED_LANE
        ]

    def board_unarchive_task(self, tid: str, *,
                             lane: str = "",
                             position: Optional[int] = None):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or task.lane != ARCHIVED_LANE:
            return
        target_lane = lane or task.archived_from_lane or "Done"
        if target_lane == ARCHIVED_LANE or target_lane not in self.board_lanes:
            target_lane = "Done" if "Done" in self.board_lanes else self.board_lanes[0]
        self._board_apply_archive_state(
            task,
            lane=target_lane,
            archived_at="",
            archived_from_lane="",
            position=position,
            clear_attention=(target_lane == "Done"),
        )
        self.recompute_task_health()

    def board_reorder_task(self, tid: str, position: int):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            return
        lane_tasks = sorted(
            [t for t in self.board_tasks.values()
             if t.lane == task.lane and t.id != tid],
            key=lambda t: t.position,
        )
        lane_tasks.insert(min(position, len(lane_tasks)), task)
        for i, t in enumerate(lane_tasks):
            t.position = i
            self._emit("task_upsert", **asdict(t))
        for t in lane_tasks:
            self._db_save_task(t)

    def board_add_lane(self, name: str, position: Optional[int] = None):
        if not name or name in self.board_lanes:
            return
        if position is not None:
            self.board_lanes.insert(min(position, len(self.board_lanes)), name)
        else:
            self.board_lanes.append(name)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_rename_lane(self, old_name: str, new_name: str):
        if (old_name in _RESERVED_LANES or old_name not in self.board_lanes
                or not new_name or new_name in self.board_lanes):
            return
        idx = self.board_lanes.index(old_name)
        self.board_lanes[idx] = new_name
        for t in self.board_tasks.values():
            if t.lane == old_name:
                t.lane = new_name
                self._emit("task_upsert", **asdict(t))
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
        for t in self.board_tasks.values():
            if t.lane == new_name:
                self._db_save_task(t)

    def board_remove_lane(self, name: str, move_tasks_to: str = ""):
        if (name in _RESERVED_LANES or name not in self.board_lanes
                or len(self.board_lanes) <= 1):
            return
        from datetime import datetime, timezone
        self.board_lanes.remove(name)
        target = move_tasks_to if move_tasks_to in self.board_lanes \
            else self.board_lanes[0]
        now_iso = datetime.now(timezone.utc).isoformat()
        max_pos = max(
            (t.position for t in self.board_tasks.values()
             if t.lane == target),
            default=-1,
        )
        for t in self.board_tasks.values():
            if t.lane == name:
                max_pos += 1
                t.lane = target
                t.position = max_pos
                t.updated_at = now_iso
                t.lane_entered_at = now_iso
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_reorder_lanes(self, lanes: list[str]):
        if set(lanes) != set(self.board_lanes):
            return
        self.board_lanes = lanes
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_get_children(self, task_id: str) -> list[BoardTask]:
        """Return direct children of a task (derived tasks)."""
        task_id = self.resolve_task_alias(task_id)
        return [t for t in self.board_tasks.values()
                if t.parent_task_id == task_id]

    def _board_check_dep_cycle(self, source_id: str,
                               new_deps: list[str]) -> bool:
        """DFS: True if adding new_deps to source_id creates a cycle."""
        visited = set()
        stack = list(new_deps)
        while stack:
            tid = stack.pop()
            if tid == source_id:
                return True
            if tid in visited:
                continue
            visited.add(tid)
            t = self.board_tasks.get(tid)
            if t:
                stack.extend(t.depends_on)
        return False

    def board_deps_met(self, task: BoardTask) -> bool:
        """True if all depends_on tasks are Done (or deleted)."""
        for dep_id in task.depends_on:
            dep = self.board_tasks.get(dep_id)
            if dep and not board_task_counts_as_done(dep):
                return False
        return True

    def board_get_dependents(self, task_id: str) -> list[BoardTask]:
        """Tasks that have task_id in their depends_on."""
        task_id = self.resolve_task_alias(task_id)
        return [t for t in self.board_tasks.values()
                if task_id in t.depends_on]

    def board_get_chain(self, task_id: str) -> list[BoardTask]:
        """Return all tasks in the same pipeline chain, ordered by depth."""
        task_id = self.resolve_task_alias(task_id)
        task = self.board_tasks.get(task_id)
        if not task:
            return []
        root_id = task.pipeline_root_id or task.id
        chain = [t for t in self.board_tasks.values()
                 if (t.pipeline_root_id == root_id) or (t.id == root_id)]
        chain.sort(key=lambda t: (t.pipeline_depth, t.created_at))
        return chain

    @staticmethod
    def _task_action_name(task: Optional[BoardTask]) -> str:
        return str(getattr(task, "action_name", "") or "").strip().lower()

    def _is_review_handoff_source(self, task: Optional[BoardTask]) -> bool:
        return self._task_action_name(task) == "feature/review"

    def _is_review_handoff_followup(self, review: Optional[BoardTask],
                                    task: Optional[BoardTask]) -> bool:
        """Return whether ``task`` is the implementation follow-up for review.

        TORQUE:88 review-derived fixes are structurally parented to the reviewed
        implementation task (the review's parent) so dispatch inherits the
        implementer's worktree instead of the reviewer's branch.  They still
        behave like logical handoff descendants of the review for execution
        slots and completion cascades.
        """
        if not review or not task or review.id == task.id:
            return False
        if not self._is_review_handoff_source(review):
            return False
        if self._task_action_name(task) not in {
            "feature/implement",
            "feature/fix-review",
        }:
            return False
        review_parent_id = str(
            getattr(review, "parent_task_id", "") or ""
        ).strip()
        if not review_parent_id:
            return False
        task_parent_id = str(
            getattr(task, "parent_task_id", "") or ""
        ).strip()
        if task_parent_id != review_parent_id:
            return False
        review_root_id = str(
            getattr(review, "pipeline_root_id", "") or review.id
        ).strip()
        task_root_id = str(
            getattr(task, "pipeline_root_id", "") or task.id
        ).strip()
        if review_root_id != task_root_id:
            return False
        if int(getattr(task, "pipeline_depth", 0) or 0) <= int(
                getattr(review, "pipeline_depth", 0) or 0):
            return False
        review_created = str(getattr(review, "created_at", "") or "")
        task_created = str(getattr(task, "created_at", "") or "")
        if review_created and task_created and task_created < review_created:
            return False
        return True

    def review_handoff_followups(self, review_id: str) -> list[BoardTask]:
        review_id = self.resolve_task_alias(review_id)
        review = self.board_tasks.get(review_id)
        if not self._is_review_handoff_source(review):
            return []
        followups = [
            task for task in self.board_tasks.values()
            if self._is_review_handoff_followup(review, task)
        ]
        followups.sort(key=lambda t: (t.pipeline_depth, t.created_at, t.id))
        return followups

    def _review_handoff_reviews_for_followup(
            self, task: Optional[BoardTask]) -> list[BoardTask]:
        if not task:
            return []
        reviews = [
            review for review in self.board_tasks.values()
            if self._is_review_handoff_followup(review, task)
        ]
        reviews.sort(key=lambda t: (t.pipeline_depth, t.created_at, t.id))
        return reviews

    def _cascade_review_handoff_completions(
            self, task: Optional[BoardTask],
            changed: list[str]) -> None:
        """Complete review tasks whose sibling fix handoff is fully resolved."""
        if not task or not task_counts_as_done(task):
            return
        if self.task_has_unresolved_descendants(task.id):
            return
        for review in self._review_handoff_reviews_for_followup(task):
            if task_suppresses_done_cascade(review):
                continue
            if board_task_is_closed(review):
                continue
            if self.task_has_unresolved_descendants(review.id):
                continue
            review.status = ""
            self._board_apply_archive_state(
                review,
                lane="Done",
                archived_at="",
                archived_from_lane="",
                clear_attention=True,
            )
            if review.id not in changed:
                changed.append(review.id)

    def task_open_descendants(self, task_id: str) -> list[BoardTask]:
        """Return all unresolved descendants for ``task_id``."""
        task_id = self.resolve_task_alias(task_id)
        if not task_id:
            return []
        descendants = []
        stack = self.board_get_children(task_id)
        stack.extend(self.review_handoff_followups(task_id))
        seen = set()
        while stack:
            current = stack.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            stack.extend(self.board_get_children(current.id))
            if self._is_review_handoff_source(current):
                stack.extend(self.review_handoff_followups(current.id))
            if task_counts_as_done(current):
                continue
            descendants.append(current)
        descendants.sort(key=lambda t: (t.pipeline_depth, t.created_at, t.id))
        return descendants

    def task_has_unresolved_descendants(self, task_id: str) -> bool:
        """Return whether any descendant branch is still unresolved."""
        return bool(self.task_open_descendants(task_id))

    def task_has_live_handoff_descendants(self, task_id: str) -> bool:
        """Return whether work has been handed off beyond this task.

        A descendant counts as a live handoff once it is clearly on another
        execution path: queued, started, assigned to an agent, or awaiting a
        human reply. Plain non-human Backlog children do *not* count; those can
        represent a derive that created a task before dispatch actually
        succeeded, and should not free the current agent slot yet.
        """
        for descendant in self.task_open_descendants(task_id):
            labels = set(descendant.labels or [])
            if descendant.lane in {"To Do", "In Progress"}:
                return True
            if descendant.agent_id:
                return True
            if "torque:human" in labels:
                return True
        return False

    def task_occupies_execution_slot(self, task: Optional[BoardTask], *,
                                     agent_id: str = "") -> bool:
        """Return whether ``task`` still occupies an agent's live slot."""
        if not task or board_task_is_closed(task):
            return False
        if agent_id and task.agent_id != agent_id:
            return False
        if task.lane in {"Backlog", "Done", ARCHIVED_LANE}:
            return False
        if self.task_has_live_handoff_descendants(task.id):
            return False
        return True

    def agent_active_tasks(self, agent_id: str) -> list[BoardTask]:
        tasks = [
            t for t in self.board_tasks.values()
            if self.task_occupies_execution_slot(t, agent_id=agent_id)
        ]
        tasks.sort(
            key=lambda t: (t.lane != "In Progress", t.position,
                           t.created_at, t.id)
        )
        return tasks

    def agent_current_task(self, agent_id: str) -> Optional[BoardTask]:
        cell = self.agents.get(agent_id)
        if cell and cell.current_task_id:
            task = self.board_tasks.get(cell.current_task_id)
            if self.task_occupies_execution_slot(task, agent_id=agent_id):
                return task
        tasks = self.agent_active_tasks(agent_id)
        return tasks[0] if tasks else None

    def agent_pending_engineer_reply_tasks(self, agent_id: str) -> list[BoardTask]:
        """Return open Engineer follow-up tasks awaiting ``agent_id`` replies."""
        tasks = [
            task for task in self.board_tasks.values()
            if task.reply_agent_id == agent_id and not board_task_is_closed(task)
        ]
        tasks.sort(key=lambda task: (task.created_at, task.id))
        return tasks

    def agent_is_busy(self, agent_id: str) -> bool:
        cell = self.agents.get(agent_id)
        if cell and cell.current_task_id:
            task = self.board_tasks.get(cell.current_task_id)
            if task and self.task_occupies_execution_slot(
                    task, agent_id=agent_id):
                return True
        return self.agent_current_task(agent_id) is not None

    def extract_playbook_candidates(self, group: str = "") -> list[dict]:
        """Mine and persist draft playbook candidates from task history."""
        if not self.db:
            return []
        from .playbooks import extract_playbook_candidates

        candidates = extract_playbook_candidates(self, group=group)
        self.db.replace_playbook_candidates(candidates, group_name=group)
        return candidates

    def list_playbook_candidates(self, group: str = "",
                                 limit: int = 50) -> list[dict]:
        """Load persisted draft playbook candidates."""
        if not self.db:
            return []
        return self.db.load_playbook_candidates(group_name=group, limit=limit)

    def save_playbook(self, playbook: dict):
        """Persist a generated draft or published playbook."""
        if not self.db:
            return
        self.db.save_playbook(playbook)

    def list_playbooks(self, group: str = "", status: str = "",
                       limit: int = 50) -> list[dict]:
        """Load persisted playbook drafts or published recipes."""
        if not self.db:
            return []
        return self.db.load_playbooks(
            group_name=group, status_filter=status, limit=limit)

    def get_playbook(self, playbook_id: str) -> Optional[dict]:
        """Load one persisted playbook draft or published recipe."""
        if not self.db:
            return None
        return self.db.load_playbook(playbook_id)

    def board_unlink_agent(self, agent_id: str):
        """Unlink an agent from all tasks (called when agent is removed)."""
        changed = False
        for t in self.board_tasks.values():
            if t.agent_id == agent_id:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
                changed = True
        if changed:
            self.recompute_task_health()

    # -- Schedule CRUD ------------------------------------------------------

    def _unique_schedule_slug(self, name: str, exclude_id: str = "") -> str:
        base = _slugify(name)
        existing = {s.slug for s in self.schedules.values()
                    if s.id != exclude_id and s.slug}
        return _unique_slug(base, existing)

    def schedule_add(self, name: str, group: str, **kwargs) -> Optional[Schedule]:
        if not name or not group:
            return None
        if group not in self.groups:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        sid = uuid.uuid4().hex[:8]
        slug = self._unique_schedule_slug(name)
        sched = Schedule(
            id=sid, name=name, slug=slug, group=group,
            created_at=now, updated_at=now,
            **{k: v for k, v in kwargs.items()
               if k in Schedule.__dataclass_fields__ and k not in
               ("id", "name", "slug", "group", "created_at", "updated_at")},
        )
        self.schedules[sid] = sched
        self._emit("schedule_upsert", **asdict(sched))
        self._db_save_schedule(sched)
        return sched

    def schedule_update(self, sid: str, **fields):
        sched = self.schedules.get(sid)
        if not sched:
            return
        valid = set(Schedule.__dataclass_fields__) - {"id", "slug", "created_at"}
        for key, value in fields.items():
            if key in valid:
                setattr(sched, key, value)
        if "name" in fields:
            sched.slug = self._unique_schedule_slug(sched.name, exclude_id=sid)
        from datetime import datetime, timezone
        sched.updated_at = datetime.now(timezone.utc).isoformat()
        self._emit("schedule_upsert", **asdict(sched))
        self._db_save_schedule(sched)

    def schedule_remove(self, sid: str):
        sched = self.schedules.pop(sid, None)
        if sched:
            self._emit("schedule_remove", id=sid)
            self._db_delete_schedule(sid)

    def schedule_get_due(self, now_iso: str) -> list[Schedule]:
        """Return enabled schedules whose next_run_at is <= now."""
        return [s for s in self.schedules.values()
                if s.enabled and s.next_run_at and s.next_run_at <= now_iso]

    # -- WebSocket broadcast ------------------------------------------------

    def snapshot_msg(self) -> str:
        """Generate a full state snapshot message (for initial connect / resync)."""
        msg = hot_json_dumps({
            "type": "state", "seq": self._seq, **self.to_dict()})
        if profiling.is_enabled():
            profiling.recorder().observe(
                "snapshot_json_bytes", len(msg.encode("utf-8")))
        return msg

    async def snapshot_msg_async(self) -> str:
        """Generate a full state snapshot without serializing on the event loop."""
        return await hot_json_dumps_async({
            "type": "state", "seq": self._seq, **self.to_dict()})

    async def broadcast(self):
        """Send accumulated deltas to all WS clients.

        WS delivery is explicitly fire-and-forget/best-effort: there is no
        ACK/replay protocol for individual frames. Clients recover from
        disconnects or missed deltas by requesting a fresh durable snapshot;
        high-level events additionally rely on panel_events persistence.

        If there are no delta ops (e.g. broadcast after a focus change
        where the _emit was called), this is a no-op — the delta was
        already queued by _emit().

        Engineer-stream recompute + `git for-each-ref` prefill are deferred
        to a background worker (`_engineer_recompute_worker`) so UI
        mutations don't wait on git. The worker fires a follow-up
        broadcast with the computed `engineer_streams` ops; clients treat
        it as any other delta frame.
        """
        # Cheap pre-lock bail-out: if nothing has been emitted, skip.
        if not self._delta_ops:
            return
        # Collect engineer-stream affected groups before draining ops so
        # the background worker has something to chew on after the
        # primary frame goes out. Fingerprint cache is updated as a
        # side effect (dedupes ephemeral-only agent_upserts).
        engineer_groups = self._collect_engineer_affected_groups(self._delta_ops)
        async with self._ws_clients_lock:
            if not self._delta_ops:
                return
            self._seq += 1
            op_count = len(self._delta_ops)
            ops = self._delta_ops
            self._delta_ops = []
            try:
                msg = await hot_json_dumps_async({
                    "type": "delta", "seq": self._seq,
                    "ops": ops,
                })
            except Exception:
                self._seq -= 1
                self._delta_ops = ops + self._delta_ops
                raise
            clients = list(self._ws_clients)
        if profiling.is_enabled():
            payload_bytes = len(msg.encode("utf-8"))
            profiling.recorder().observe("ws_delta_payload_bytes", payload_bytes)
            profiling.recorder().observe("ws_delta_ops_count", op_count)
            profiling.recorder().observe("ws_clients_per_broadcast", len(clients))
        # Send to every client concurrently so a slow/stuck client
        # doesn't stall delivery to the others (the lock above already
        # guarantees ordering — only one broadcast is in flight at a time).
        with profiling.timer("ws_broadcast_ms"):
            results = await asyncio.gather(
                *(ws.send_str(msg) for ws in clients),
                return_exceptions=True,
            )
        dead: set[web.WebSocketResponse] = {
            ws for ws, result in zip(clients, results)
            if isinstance(result, BaseException)
        }
        if dead:
            db = getattr(self, "db", None)
            if db and hasattr(db, "record_mcp_health_event_safe"):
                db.record_mcp_health_event_safe(
                    surface="ws",
                    event="drop",
                    error=f"failed clients: {len(dead)}",
                )
            async with self._ws_clients_lock:
                self._ws_clients -= dead
        if engineer_groups:
            self._schedule_engineer_recompute(engineer_groups)

    def _schedule_engineer_recompute(self, groups: set[str]) -> None:
        """Queue a deferred engineer-stream recompute for ``groups``.

        Spawns a single worker task. If one is already running, merges
        the new groups into its pending set — the worker re-checks
        after each iteration so it drains everything before exiting.
        """
        if not groups:
            return
        self._engineer_recompute_pending |= set(groups)
        task = self._engineer_recompute_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop; caller is off the async path. Skip silently —
            # the next real broadcast will re-queue anything still dirty.
            return
        self._engineer_recompute_task = loop.create_task(
            self._engineer_recompute_worker()
        )

    async def _engineer_recompute_worker(self) -> None:
        """Drain `_engineer_recompute_pending`: prefill branch existence,
        compute stream payloads, broadcast a follow-up delta."""
        from .worktree_streams import prefill_branch_exists_for_state
        try:
            while self._engineer_recompute_pending:
                pending = self._engineer_recompute_pending
                self._engineer_recompute_pending = set()
                try:
                    await prefill_branch_exists_for_state(self)
                except Exception:
                    log.exception("Branch-exists prefill failed")
                if self._emit_engineer_stream_ops(pending):
                    # The follow-up broadcast's own _collect_engineer_*
                    # call returns empty (engineer_streams isn't a trigger
                    # op), so this does not recurse into another worker.
                    await self.broadcast()
        finally:
            self._engineer_recompute_task = None
