"""AgentCell dataclass and MatrixState persistence layer."""

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from aiohttp import web

try:
    import orjson
except ImportError:  # pragma: no cover - dependency is installed by Makefile.
    orjson = None

from .config import DATA_DIR, DEFAULT_COMMAND, log
from . import profiling
from .artifacts import normalize_artifacts, normalize_attachments
from .db import LoomDB
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
_VERIFICATION_MODES = {"", "deploy", "restart"}
_VERIFICATION_STATES = {"", "pending", "attempted", "passed", "failed"}
_WEAVER_AUTONOMY_MODES = {
    "suggest_only",
    "dispatch_when_clear",
    "aggressive_auto_continue",
}
_DEFAULT_WEAVER_AUTONOMY_MODE = "dispatch_when_clear"
_DEFAULT_WEAVER_DEFAULT_WORKER_CONCURRENCY = 2
_WEAVER_WAVE_SIZE_PREFERENCES = {
    "small",
    "balanced",
    "large",
}
_DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE = "small"
_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCES = {
    "balanced",
    "prefer_same_agent",
    "prefer_fresh_agent",
}
_DEFAULT_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCE = "balanced"
_WEAVER_DIGEST_VERBOSITIES = {
    "compact",
    "balanced",
    "detailed",
}
_DEFAULT_WEAVER_DIGEST_VERBOSITY = "balanced"
_WEAVER_NOTIFICATION_PRESETS = {
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
]
_WEAVER_ESCALATION_STYLES = {
    "ask_early",
    "note_then_ask",
    "keep_moving",
}
_DEFAULT_WEAVER_ESCALATION_STYLE = "note_then_ask"
_WORKTREE_MERGE_CLEANUP_MODES = {
    "keep",
    "close",
    "remove",
    "close_remove",
}
_DEFAULT_WORKTREE_MERGE_CLEANUP = "keep"
_WEAVER_WORKLOG_LIMIT = 200
_WEAVER_STREAM_CARD_LIMIT = 10
_WEAVER_STREAM_CONTEXT_LIMIT = 5
_WEAVER_STREAM_DELTA_TRIGGER_OPS = {
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
    "health_state",
    "health_since",
    "health_details",
    "verification_state",
    "verification_mode",
    "verification_notes",
    "verification_summary",
    "messages",
    "lane_entered_at",
    "worktree_boundary",
    "resume_after_boundary_task_id",
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


def normalize_weaver_autonomy_mode(value) -> str:
    value = str(value or "").strip()
    if value in _WEAVER_AUTONOMY_MODES:
        return value
    return _DEFAULT_WEAVER_AUTONOMY_MODE


def normalize_default_worker_concurrency(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_WEAVER_DEFAULT_WORKER_CONCURRENCY
    return max(1, value)


def normalize_weaver_wave_size_preference(value) -> str:
    value = str(value or "").strip()
    if value in _WEAVER_WAVE_SIZE_PREFERENCES:
        return value
    return _DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE


def normalize_weaver_same_agent_follow_up_preference(value) -> str:
    value = str(value or "").strip()
    if value in _WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCES:
        return value
    return _DEFAULT_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCE


def normalize_weaver_digest_verbosity(value) -> str:
    value = str(value or "").strip()
    if value in _WEAVER_DIGEST_VERBOSITIES:
        return value
    return _DEFAULT_WEAVER_DIGEST_VERBOSITY


def get_weaver_notification_preset(name) -> dict:
    preset = _WEAVER_NOTIFICATION_PRESETS.get(
        str(name or "").strip().lower()
    )
    if not preset:
        raise ValueError(f"Unknown Weaver notification preset: {name}")
    return {
        "digest_verbosity": preset["digest_verbosity"],
        "push_interval": preset["push_interval"],
        "max_interval": preset["max_interval"],
        "heartbeat_interval": preset["heartbeat_interval"],
        "enabled_events": list(preset["enabled_events"]),
    }


def normalize_weaver_escalation_style(value) -> str:
    value = str(value or "").strip()
    if value in _WEAVER_ESCALATION_STYLES:
        return value
    return _DEFAULT_WEAVER_ESCALATION_STYLE


def normalize_worktree_merge_cleanup(value) -> str:
    value = str(value or "").strip()
    if value in _WORKTREE_MERGE_CLEANUP_MODES:
        return value
    return _DEFAULT_WORKTREE_MERGE_CLEANUP


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
    reply_agent_id: str = ""    # worker expected to answer this follow-up
    labels: list[str] = field(default_factory=list)
    created_at: str = ""        # ISO 8601
    updated_at: str = ""        # ISO 8601
    lane_entered_at: str = ""   # ISO 8601 of most recent transition into lane
    # Provider fields (unused in v1, ready for sync)
    provider: str = ""          # "jira", "linear", "github"
    external_id: str = ""       # e.g. "PROJ-123"
    external_url: str = ""      # link back to provider
    # Pipeline fields (Phase 4b)
    parent_task_id: str = ""    # task this was derived from (empty for root tasks)
    pipeline_depth: int = 0     # 0 for root, auto-incremented from parent
    pipeline_root_id: str = ""  # ID of the chain's root task (self.id for roots)
    status: str = ""            # pipeline status (e.g. "On Review", "Fixing")
    # Scheduling
    scheduled_at: str = ""      # ISO 8601 — auto-dispatch when this time arrives
    # Activity log — persisted history of agent reports on this task
    messages: list = field(default_factory=list)  # [{timestamp, action, message, agent_name}]
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
    weaver_owner_id: str = ""
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
    worktree_base_dir: str = ".loom/worktrees"  # worktree dir relative to repo root
    worktree_base_branch: str = ""  # branch the worktree forked from (e.g. main)
    worktree_auto_checkpoint: bool = False  # auto-checkpoint on session end
    checkpoint_on_progress: bool = False  # auto-checkpoint on loom ai progress/done
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
    error_message: str = ""  # last error, cleared on next successful event
    needs_attention: bool = False  # agent waiting for input or stuck
    last_summary: str = ""  # last assistant message on Stop (for checkpoint msgs)
    # Context preservation (dispatch history)
    tasks_dispatched: int = 0  # number of tasks sent to this agent (persisted)
    created_by_weaver_id: str = ""  # immutable Weaver provenance (persisted)
    kind: str = ""  # "" | architect | engineer | worker | terminal
    role: str = ""  # worker-role slug mirrored from template during migration
    owner_engineer_id: str = ""  # owning engineer for worker/terminal agents
    hired_by_architect_id: str = ""  # architect provenance for hires
    dismissed_at: int = 0  # unix timestamp when an engineer is paused/dismissed
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
    # Weaver message tracking (ephemeral)
    pending_weaver_message: bool = False  # agent has unread message from weaver

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


# Fields that are ephemeral (not meaningful across restarts)
_EPHEMERAL_FIELDS = ("current_process", "current_path",
                     "current_branch", "git_root",
                     "activity", "activity_detail",
                     "last_event_text",
                     "session_tokens_in", "session_tokens_out",
                     "error_message", "needs_attention", "last_summary",
                     "current_task_id",
                     "worktree_dirty", "worktree_diff",
                     "worktree_changed_files",
                     "worktree_checkpoints", "last_checkpoint_at",
                     "mcp_messages",
                     "pending_weaver_message")


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


def task_is_closed(task: Optional[BoardTask]) -> bool:
    return board_task_is_closed(task)


def task_counts_as_done(task: Optional[BoardTask]) -> bool:
    return board_task_counts_as_done(task)


def task_suppresses_done_cascade(task: Optional[BoardTask]) -> bool:
    if not task:
        return False
    labels = set(task.labels or [])
    return bool(labels.intersection({"loom:human", "loom:weaver-message"}))


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
    worktree_base_dir: str = ".loom/worktrees"  # directory for worktrees (relative to repo)
    worktree_base_branch: str = ""  # branch to fork from (empty = current HEAD)
    worktree_auto_checkpoint: bool = True  # auto-checkpoint on agent stop
    checkpoint_on_progress: bool = False  # auto-checkpoint on loom ai progress/done
    worktree_merge_squash: bool = False  # squash commits when merging to main
    worktree_merge_instructions: str = ""  # additional instructions appended to merge prompt
    worktree_merge_cleanup: str = "keep"  # keep | close | remove | close_remove
    worktree_merge_preserve_diff: bool = False  # save the pre-merge patch on the latest boundary task
    worktree_symlinks: list[str] = field(default_factory=list)  # repo-relative paths or glob patterns to symlink from repo root
    agent_session_resume: bool = True  # resume session on relaunch
    agent_idle_timeout: int = 0  # minutes before flagging agent as stuck (0=disable)
    agent_always_custom_dialog: bool = False
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
    terminal_close_on_disconnect: bool = False  # remove terminal from Loom when tab closed
    # Board / Dispatch
    dispatch_lane: str = "In Progress"  # lane for dispatched tasks
    dispatch_auto_terminals: bool = False  # create child terminals on dispatch
    board_default_labels: list[str] = field(default_factory=list)  # default labels for new tasks
    board_default_lane: str = ""  # default lane for new tasks (empty = first lane)
    board_default_action: str = ""  # default action for new tasks
    # Weaver
    weaver_agent_id: str = ""  # designated weaver agent for this group


@dataclass
class WeaverSettings:
    """Per-group weaver configuration."""
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
    custom_instructions: str = ""        # user-defined instructions appended to weaver system prompt
    restrict_to_created_agents: bool = False  # limit Weaver agent visibility/control to its own created agents
    pending_question: str = ""           # question awaiting human reply (non-empty = awaiting input)
    pending_note: str = ""               # non-blocking note/question for the human
    pending_note_kind: str = ""          # "note" | "question" | ""
    weaver_provider: str = ""            # adapter name override (empty = use group default)
    weaver_boot_command: str = ""        # boot command override (empty = use provider default)
    weaver_model: str = ""               # model override for the designated weaver
    weaver_reasoning_effort: str = ""    # reasoning-effort override for the designated weaver
    weaver_directory: str = ""           # directory override for the designated weaver
    weaver_profile: str = ""             # iTerm profile override for the designated weaver
    weaver_shell: str = ""               # shell override for the designated weaver
    weaver_tab_color: str = ""           # tab color override for the designated weaver
    enabled_events: list[str] = field(   # optional events (mandatory always on)
        default_factory=lambda: list(
            _WEAVER_NOTIFICATION_PRESETS["normal"]["enabled_events"]
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
            _WEAVER_NOTIFICATION_PRESETS["normal"]["enabled_events"]
        )
    )
    architect_digest: bool = False
    wake_on_digest: bool = False


# Mandatory events — always included in weaver digests regardless of enabled_events.
WEAVER_MANDATORY_EVENTS = frozenset({
    "task_completed", "agent_reply", "agent_error",
    "agent_blocked", "ask_created", "task_verification_updated",
})


@dataclass
class GlobalSettings:
    """App-wide settings for the Loom instance."""
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

    def __post_init__(self):
        self.xterm_scrollback = normalize_xterm_scrollback(
            self.xterm_scrollback
        )


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self, db: Optional[LoomDB] = None):
        self.db: Optional[LoomDB] = db
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
        self.standalone_panel_layout: dict = {}
        self.events_dismissed_attention: dict[str, float] = {}
        self.board_filters_by_group: dict[str, dict] = {}
        self.board_saved_views_by_group: dict[str, list] = {}
        self.board_lane_sorts_by_group: dict[str, dict] = {}
        self.board_card_density_by_group: dict[str, str] = {}
        self.panel_log = None  # PanelEventLog, set from server.py
        # Weaver settings (per-group)
        self.weaver_settings: dict[str, WeaverSettings] = {}
        self.agent_digest_settings: dict[str, AgentDigestSettings] = {}
        self.weaver_worklog: dict[str, list[dict]] = {}
        # Delta broadcast accumulator
        self._delta_ops: list[dict] = []
        self._seq: int = 0
        # Per-agent fingerprint of weaver-relevant fields. Lets
        # `_collect_weaver_affected_groups` skip recomputing a group's
        # weaver streams when an agent_upsert only changes ephemeral
        # fields (activity, path, last_event_at, etc.).
        self._agent_weaver_fingerprints: dict[str, tuple] = {}
        self._engineer_queue_empty_since: dict[str, float] = {}
        # Deferred weaver-stream recompute. `broadcast()` queues affected
        # groups into `_weaver_recompute_pending` and spawns a single
        # worker task that prefills branch-existence, computes each
        # group's stream payload, and emits a follow-up `weaver_streams`
        # delta. Keeps the primary delta frame off the git-subprocess
        # hot path so UI mutations feel instant.
        self._weaver_recompute_pending: set[str] = set()
        self._weaver_recompute_task = None

    # -- Delta emission -----------------------------------------------------

    def _emit(self, op: str, **kwargs):
        """Accumulate a delta operation for the next broadcast."""
        old_task_refs = self._sync_task_indexes_from_delta(op, kwargs)
        self._track_task_health_delta(op, kwargs, old_task_refs=old_task_refs)
        self._maybe_clear_engineer_queue_empty_from_delta(op, kwargs)
        self._delta_ops.append({"op": op, **kwargs})

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
                (payload or {}).get("created_by_weaver_id", "") or ""
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

    def _weaver_stream_groups(self) -> list[str]:
        groups = set(self.groups)
        groups.update(self.group_settings)
        groups.update(self.weaver_settings)
        # Use the per-group task index instead of iterating all tasks.
        groups.update(self._tasks_by_group.keys())
        groups.update(
            str(getattr(cell, "group", "") or "").strip()
            for cell in self.agents.values()
        )
        groups.discard("")
        return sorted(groups)

    def _weaver_stream_counts(self, streams: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for stream in streams:
            state_name = str(stream.get("state", "") or "").strip()
            if not state_name:
                continue
            counts[state_name] = counts.get(state_name, 0) + 1
        return counts

    def _weaver_stream_payload(self, group: str) -> dict:
        from .worktree_streams import compute_worktree_streams

        try:
            streams = compute_worktree_streams(
                self,
                group=group,
                visibility_limit=_WEAVER_STREAM_CONTEXT_LIMIT,
                include_orphaned=False,
            )
        except Exception:
            log.exception("Failed to compute weaver streams for %s", group)
            streams = []
        streams = [
            stream for stream in streams
            if str(stream.get("state", "") or "").strip() != "merged"
        ]
        return {
            "count": len(streams),
            "by_state": self._weaver_stream_counts(streams),
            "items": streams[:_WEAVER_STREAM_CARD_LIMIT],
            "truncated": len(streams) > _WEAVER_STREAM_CARD_LIMIT,
        }

    def _weaver_streams_snapshot(self) -> dict[str, dict]:
        return {
            group: self._weaver_stream_payload(group)
            for group in self._weaver_stream_groups()
        }

    def _agent_weaver_fingerprint(self, op: dict) -> tuple:
        """Fingerprint the weaver-relevant fields of an agent_upsert op.

        Weaver stream identity/gating only depends on group, worktree
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
        )

    def _collect_weaver_affected_groups(self, ops: list[dict]) -> set[str]:
        """Return the set of groups whose weaver streams need recomputing.

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
            if op_name not in _WEAVER_STREAM_DELTA_TRIGGER_OPS:
                continue
            if op_name == "group_rename":
                affected_groups.add(str(op.get("old_name", "") or "").strip())
                affected_groups.add(str(op.get("new_name", "") or "").strip())
                continue
            if op_name in {"group_update", "group_remove"}:
                affected_groups.add(str(op.get("name", "") or "").strip())
                continue
            # Terminal cells don't participate in weaver streams; skip them
            # so a terminal upsert/remove doesn't force a group recompute.
            if op_name in {"agent_upsert", "agent_remove"}:
                cell_type = str(op.get("cell_type", "agent") or "agent").strip()
                if cell_type and cell_type != "agent":
                    continue
                agent_id = str(op.get("id", "") or "")
                if op_name == "agent_upsert" and agent_id:
                    fingerprint = self._agent_weaver_fingerprint(op)
                    if self._agent_weaver_fingerprints.get(
                            agent_id) == fingerprint:
                        continue
                    self._agent_weaver_fingerprints[agent_id] = fingerprint
                elif op_name == "agent_remove" and agent_id:
                    self._agent_weaver_fingerprints.pop(agent_id, None)
            affected_groups.add(str(op.get("group", "") or "").strip())
        affected_groups.discard("")
        return affected_groups

    def _emit_weaver_stream_ops(self, groups: set[str]) -> bool:
        """Emit `weaver_streams` delta ops for each group in ``groups``.

        Returns True when at least one op was emitted. Safe to call when
        the per-repo branch-exists cache is cold, but in practice
        callers should prefill first so no subprocess runs inline.
        """
        if not groups:
            return False
        known_groups = set(self._weaver_stream_groups())
        existing_groups = {
            str((op or {}).get("group", "") or "").strip()
            for op in self._delta_ops
            if str((op or {}).get("op", "") or "") in {
                "weaver_streams",
                "weaver_streams_update",
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
                    "weaver_streams",
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
                payload = self._weaver_stream_payload(group)
            except Exception:
                log.exception(
                    "weaver stream payload failed for group '%s'", group
                )
                continue
            self._emit("weaver_streams", group=group, streams=payload)
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
            "standalone_panel_layout": self.standalone_panel_layout,
            "events_dismissed_attention": self.events_dismissed_attention,
            "board_filters_by_group": self.board_filters_by_group,
            "board_saved_views_by_group": self.board_saved_views_by_group,
            "board_lane_sorts_by_group": self.board_lane_sorts_by_group,
            "board_card_density_by_group": self.board_card_density_by_group,
            "panel_events": self.panel_log.get_recent(50) if self.panel_log else [],
            "weaver_settings": {
                n: asdict(ws) for n, ws in self.weaver_settings.items()
            },
            "agent_digest_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_digest_settings.items()
            },
            "weaver_journal": {
                g: self.journal_read(g, limit=50)
                for g in self.weaver_settings
            },
            "weaver_worklog": {
                g: [dict(entry) for entry in entries]
                for g, entries in self.weaver_worklog.items()
            },
            "weaver_streams": self._weaver_streams_snapshot(),
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

    def to_dict_compact(self) -> dict:
        """Return an opt-in compact snapshot for new lazy-loading clients.

        This intentionally does not call ``to_dict()``: the legacy snapshot
        performs synchronous decision/pending-hire/journal/weaver-stream reads
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
            "standalone_panel_layout": self.standalone_panel_layout,
            "events_dismissed_attention": self.events_dismissed_attention,
            "board_filters_by_group": self.board_filters_by_group,
            "board_saved_views_by_group": self.board_saved_views_by_group,
            "board_lane_sorts_by_group": self.board_lane_sorts_by_group,
            "board_card_density_by_group": self.board_card_density_by_group,
            "panel_events": self.panel_log.get_recent(50) if self.panel_log else [],
            "weaver_settings": {
                n: asdict(ws) for n, ws in self.weaver_settings.items()
            },
            "agent_digest_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_digest_settings.items()
            },
        }

    # -- Targeted persistence helpers ----------------------------------------

    def _db_save_agent(self, cell: AgentCell):
        """Persist a single agent to SQLite."""
        if self.db:
            try:
                self.db.save_agent_deferred(cell)
            except Exception:
                log.exception("Failed to save agent %s", cell.id)

    def _db_delete_agent(self, agent_id: str):
        if self.db:
            try:
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
                self.db.save_board_task_deferred(task)
            except Exception:
                log.exception("Failed to save task %s", task.id)

    def _db_delete_task(self, task_id: str):
        if self.db:
            try:
                self.db.delete_board_task(task_id)
            except Exception:
                log.exception("Failed to delete task %s", task_id)

    def _db_save_task_id_counter(self, group_prefix: str):
        if self.db:
            try:
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

    def auto_dispatch_queue_add(self, group: str, task_id: str, *,
                                agent_group: str = "",
                                max_concurrent: int = 1,
                                target_agent_id: str = "",
                                weaver_owner_id: str = ""):
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
            weaver_owner_id=str(weaver_owner_id or "").strip(),
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

    # -- Agent history helpers -----------------------------------------------

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
            self.db.update_agent_history(
                cell.id,
                removed_at=time.time(),
                status="removed",
                total_tokens_in=prev_in + cell.session_tokens_in,
                total_tokens_out=prev_out + cell.session_tokens_out,
            )
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
                                weaver_group: str = "",
                                weaver_id: str = ""):
        """Record a task dispatch in history."""
        import time
        ts = time.time()
        if cell.mark_progress(ts):
            self._emit_agent(cell)
        weaver_group = str(weaver_group or "").strip()
        weaver_id = str(weaver_id or "").strip()
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
            if weaver_group:
                entry = {
                    "group": weaver_group,
                    "task_id": task.id,
                    "task_title": task.task,
                    "agent_id": cell.id,
                    "agent_name": cell.name,
                    "agent_slug": cell.slug,
                    "agent_owned": bool(
                        weaver_id and cell.created_by_weaver_id == weaver_id
                    ),
                    "started_at": ts,
                }
                if self.db:
                    entry["id"] = self.db.save_weaver_task_log_entry(entry)
                    self.db.trim_weaver_task_log(
                        weaver_group,
                        limit=_WEAVER_WORKLOG_LIMIT,
                    )
                else:
                    entries = self.weaver_worklog.get(weaver_group, [])
                    newest_id = entries[0]["id"] if entries else 0
                    entry["id"] = int(newest_id or 0) + 1
                self._append_weaver_worklog_entry(weaver_group, entry)
        except Exception:
            log.exception("Failed to record dispatch history %s → %s",
                          cell.id, task.id)

    def history_record_message(self, cell_id: str, action: str,
                               message: str, task_id: str = ""):
        """Record an agent message (loom ai report) in history."""
        import time
        ts = time.time()
        cell = self.agents.get(cell_id)
        if cell:
            if cell.mark_progress(ts):
                self._emit_agent(cell)
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

    def load(self):
        from .config import DB_FILE, STATE_FILE
        if not self.db:
            return

        # Migration: if DB is empty but state.json exists, import it
        if not self.db.has_data() and STATE_FILE.exists():
            self.db.migrate_from_json(STATE_FILE)

        data = self.db.load_all()
        if not data.get("agents") and not data.get("groups"):
            return  # empty DB, nothing to load

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
                if "loom:archived" in labels and raw.get("lane") != ARCHIVED_LANE:
                    prior_lane = raw.get("lane") or ""
                    raw["lane"] = ARCHIVED_LANE
                    raw["archived_from_lane"] = raw.get("archived_from_lane") \
                        or prior_lane
                    raw["archived_at"] = raw.get("archived_at") \
                        or raw.get("updated_at", "") or raw.get("created_at", "")
                    labels = [label for label in labels
                              if label != "loom:archived"]
                raw["labels"] = labels
                _normalize_verification_fields(raw)
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
            for aid, cell in self.agents.items():
                cell.pending_weaver_message = bool(
                    self.agent_pending_weaver_reply_tasks(aid)
                )
            # panel_active: new key; backward compat from board_panel_open
            pa = data.get("panel_active", "")
            if not pa and data.get("board_panel_open"):
                pa = "board"
            self.panel_active = pa
            self.board_panel_height = data.get("board_panel_height", 0)
            self.standalone_panel_layout = data.get(
                "standalone_panel_layout", {}
            ) or {}
            self.events_dismissed_attention = data.get(
                "events_dismissed_attention", {}
            ) or {}
            self.board_filters_by_group = data.get(
                "board_filters_by_group", {}
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
            # Weaver settings
            if self.db:
                ws_fields = set(WeaverSettings.__dataclass_fields__)
                for gname, raw in self.db.load_all_weaver_settings().items():
                    filtered = {k: v for k, v in raw.items() if k in ws_fields}
                    if "autonomy_mode" in filtered:
                        filtered["autonomy_mode"] = (
                            normalize_weaver_autonomy_mode(
                                filtered["autonomy_mode"])
                        )
                    if "default_worker_concurrency" in filtered:
                        filtered["default_worker_concurrency"] = (
                            normalize_default_worker_concurrency(
                                filtered["default_worker_concurrency"])
                        )
                    if "wave_size_preference" in filtered:
                        filtered["wave_size_preference"] = (
                            normalize_weaver_wave_size_preference(
                                filtered["wave_size_preference"])
                        )
                    if "same_agent_follow_up_preference" in filtered:
                        filtered["same_agent_follow_up_preference"] = (
                            normalize_weaver_same_agent_follow_up_preference(
                                filtered["same_agent_follow_up_preference"])
                        )
                    if "digest_verbosity" in filtered:
                        filtered["digest_verbosity"] = (
                            normalize_weaver_digest_verbosity(
                                filtered["digest_verbosity"])
                        )
                    if "escalation_style" in filtered:
                        filtered["escalation_style"] = (
                            normalize_weaver_escalation_style(
                                filtered["escalation_style"])
                        )
                    self.weaver_settings[gname] = WeaverSettings(**filtered)
                ads_fields = set(AgentDigestSettings.__dataclass_fields__)
                for agent_id, raw in self.db.load_all_agent_digest_settings().items():
                    if agent_id not in self.agents:
                        continue
                    filtered = {
                        k: v for k, v in raw.items() if k in ads_fields
                    }
                    if "digest_verbosity" in filtered:
                        filtered["digest_verbosity"] = (
                            normalize_weaver_digest_verbosity(
                                filtered["digest_verbosity"]
                            )
                        )
                    self.agent_digest_settings[agent_id] = (
                        AgentDigestSettings(**filtered)
                    )
                self._backfill_architect_digest_defaults()
                for gname in self.groups:
                    entries = self.db.load_weaver_task_log(
                        gname,
                        limit=_WEAVER_WORKLOG_LIMIT,
                    )
                    if entries:
                        self.weaver_worklog[gname] = entries
            cleaned = self.cleanup_orphaned_attention(emit=False)
            self.recompute_task_health(emit=False, persist=False)
            if cleaned["asks"] or cleaned["weaver_questions"]:
                log.info(
                    "Expired %d orphaned ask(s) and cleared %d stale weaver question(s)",
                    cleaned["asks"],
                    cleaned["weaver_questions"],
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
                elif key in {"agent_model", "agent_reasoning_effort"}:
                    value = str(value or "").strip()
                setattr(gs, key, value)
        self._emit("group_settings_update", name=name, **asdict(gs))
        self._db_save_group_settings(name)

    # -- Weaver settings & journal ------------------------------------------

    def get_weaver_for_group(self, group: str) -> Optional[AgentCell]:
        """Return the weaver agent for a group, or None."""
        gs = self.group_settings.get(group)
        if not gs or not gs.weaver_agent_id:
            return None
        return self.agents.get(gs.weaver_agent_id)

    def get_weaver_settings(self, group: str) -> WeaverSettings:
        """Return weaver settings for a group, creating defaults if needed."""
        return self.weaver_settings.get(group, WeaverSettings(group=group))

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
            kwargs["enabled_events"] = list(
                _ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS
            )
        return AgentDigestSettings(
            agent_id=agent_id,
            push_interval=300 if is_architect else 60,
            architect_digest=is_architect,
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
        legacy_weaver = self.get_weaver_for_group(cell.group)
        if not legacy_weaver or legacy_weaver.id != agent_id:
            return self._default_agent_digest_settings(agent_id, cell)
        ws = self.get_weaver_settings(cell.group)
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
            digest_verbosity=normalize_weaver_digest_verbosity(
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
            if "engineer_queue_empty" in enabled:
                continue
            enabled.append("engineer_queue_empty")
            settings.enabled_events = enabled
            if not bool(getattr(settings, "architect_digest", False)):
                settings.architect_digest = True
            changed.append((agent_id, settings))
        if self.db:
            for agent_id, settings in changed:
                self.db.save_agent_digest_settings(agent_id, asdict(settings))

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
                value = normalize_weaver_digest_verbosity(value)
            elif key in {"paused", "architect_digest", "wake_on_digest"}:
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

    def update_weaver_settings(self, group: str, **fields):
        """Update weaver settings for a group."""
        ws = self.weaver_settings.get(group)
        if ws is None:
            ws = WeaverSettings(group=group)
            self.weaver_settings[group] = ws
        valid = set(WeaverSettings.__dataclass_fields__)
        for key, value in fields.items():
            if key in valid:
                if key == "autonomy_mode":
                    value = normalize_weaver_autonomy_mode(value)
                elif key == "default_worker_concurrency":
                    value = normalize_default_worker_concurrency(value)
                elif key == "wave_size_preference":
                    value = normalize_weaver_wave_size_preference(value)
                elif key == "same_agent_follow_up_preference":
                    value = normalize_weaver_same_agent_follow_up_preference(
                        value)
                elif key == "digest_verbosity":
                    value = normalize_weaver_digest_verbosity(value)
                elif key == "escalation_style":
                    value = normalize_weaver_escalation_style(value)
                elif key == "restrict_to_created_agents":
                    value = bool(value)
                elif key in {
                        "weaver_model", "weaver_reasoning_effort",
                        "weaver_directory", "weaver_profile",
                        "weaver_shell", "weaver_tab_color"}:
                    value = str(value or "").strip()
                setattr(ws, key, value)
        d = asdict(ws)
        d.pop("group", None)
        self._emit("weaver_settings_update", group=group, **d)
        if self.db:
            self.db.save_weaver_settings(group, asdict(ws))
        legacy_weaver = self.get_weaver_for_group(group)
        if legacy_weaver and legacy_weaver.id in self.agent_digest_settings:
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
                self.update_agent_digest_settings(legacy_weaver.id, **digest_fields)

    def weaver_restricts_to_created_agents(self, group: str) -> bool:
        """Return whether the group's Weaver is restricted to owned agents."""
        return bool(
            self.get_weaver_settings(group).restrict_to_created_agents
        )

    def agent_is_visible_to_weaver(self, weaver_id: str, agent_id: str) -> bool:
        """Return whether ``agent_id`` is visible/controllable to ``weaver_id``.

        Visibility is always limited to agent cells in the same group. When the
        per-group restriction is enabled, only agents whose immutable Weaver
        provenance matches the caller are visible.
        """
        weaver = self.agents.get(str(weaver_id or "").strip())
        agent = self.agents.get(str(agent_id or "").strip())
        if not weaver or weaver.cell_type != "agent":
            return False
        if not agent or agent.cell_type != "agent":
            return False
        if not weaver.group or agent.group != weaver.group:
            return False
        if not self.weaver_restricts_to_created_agents(weaver.group):
            return True
        return bool(agent.created_by_weaver_id == weaver.id)

    def _save_weaver_settings(self, group: str, emit: bool = True):
        ws = self.weaver_settings.get(group)
        if not ws:
            return
        d = asdict(ws)
        d.pop("group", None)
        if emit:
            self._emit("weaver_settings_update", group=group, **d)
        if self.db:
            self.db.save_weaver_settings(group, asdict(ws))

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
            if "loom:human" not in (task.labels or []):
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
        if "loom:human" not in (task.labels or []) or board_task_is_closed(task):
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
            for label in ("loom:blocked", "loom:error"):
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
                "agent_name": "Loom",
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

    def cleanup_orphaned_attention(self, emit: bool = True) -> dict[str, int]:
        """Expire asks and pending weaver questions whose source agent is gone."""
        cleaned = {"asks": 0, "weaver_questions": 0}
        live_agents = set(self.agents)

        for group, ws in self.weaver_settings.items():
            gs = self.group_settings.get(group)
            weaver_id = gs.weaver_agent_id if gs else ""
            if ws.pending_question and (not weaver_id or weaver_id not in live_agents):
                ws.pending_question = ""
                ws.paused = False
                ws.pending_note = ""
                ws.pending_note_kind = ""
                self._save_weaver_settings(group, emit=emit)
                cleaned["weaver_questions"] += 1
            elif ws.pending_note and (not weaver_id or weaver_id not in live_agents):
                ws.pending_note = ""
                ws.pending_note_kind = ""
                self._save_weaver_settings(group, emit=emit)

        reason = "Ask expired because the source agent is no longer available."
        for task in list(self.board_tasks.values()):
            if "loom:human" not in (task.labels or []) or board_task_is_closed(task):
                continue
            parent = self.board_tasks.get(task.parent_task_id)
            parent_agent_id = parent.agent_id if parent else ""
            if not parent or not parent_agent_id or parent_agent_id not in live_agents:
                if self._expire_orphaned_ask(task, reason, emit=emit):
                    cleaned["asks"] += 1

        return cleaned

    def journal_append(self, group: str, entry_type: str,
                       entry: str, author_cell_id: str = "") -> dict:
        """Append an entry to the weaver journal. Returns the entry dict."""
        import time
        ts = time.time()
        entry_id = 0
        author_cell_id = str(author_cell_id or "").strip()
        if self.db:
            entry_id = self.db.save_journal_entry(
                group,
                ts,
                entry_type,
                entry,
                author_cell_id=author_cell_id,
            )
        evt = {"id": entry_id, "group": group, "timestamp": ts,
               "type": entry_type, "entry": entry,
               "author_cell_id": author_cell_id}
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

    def architect_journal_append(self, architect_id: str, entry_type: str,
                                 entry: str) -> dict:
        """Append one architect journal entry to its JSONL file."""
        import time

        architect_id = str(architect_id or "").strip()
        if not architect_id:
            raise ValueError("architect_id is required")
        record = {
            "id": uuid.uuid4().hex[:12],
            "architect_id": architect_id,
            "timestamp": time.time(),
            "type": str(entry_type or "").strip(),
            "entry": str(entry or ""),
        }
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

    def _append_weaver_worklog_entry(self, group: str, entry: dict):
        """Append a Weaver worklog entry to in-memory state and emit it."""
        if not group:
            return
        item = dict(entry or {})
        item["group"] = group
        entries = self.weaver_worklog.setdefault(group, [])
        entries.insert(0, item)
        if len(entries) > _WEAVER_WORKLOG_LIMIT:
            del entries[_WEAVER_WORKLOG_LIMIT:]
        self._emit("weaver_worklog_append", group=group, entry=dict(item))

    def weaver_worklog_read(self, group: str, limit: int = 50) -> list[dict]:
        """Return recent persisted/current designated-engineer worklog entries for a group."""
        entries = self.weaver_worklog.get(group, [])
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
            self.weaver_settings.pop(name, None)
            self.weaver_worklog.pop(name, None)
            for r in removed:
                self.delete_agent_digest_settings(r.id)
            if self.db:
                self.db.delete_weaver_settings(name)
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
            if old in self.weaver_worklog:
                self.weaver_worklog[new] = self.weaver_worklog.pop(old)
                for entry in self.weaver_worklog[new]:
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
                self.db.rename_weaver_task_log_group(old, new)
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

    def remove_agent(self, aid: str) -> list[AgentCell]:
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
        self.cleanup_orphaned_attention()
        # Unlink from board tasks
        for t in self.board_tasks.values():
            if t.agent_id == aid:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
        # Clear weaver designation if this agent was the weaver
        gs = self.group_settings.get(cell.group)
        if gs and gs.weaver_agent_id == aid:
            gs.weaver_agent_id = ""
            self._emit("group_settings_update", name=cell.group, **asdict(gs))
            self._db_save_group_settings(cell.group)
        for r in removed:
            self.delete_agent_digest_settings(r.id)
        for r in removed:
            self._db_delete_agent(r.id)
        self._db_save_groups()
        return removed

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
        return [c for c in self.agents.values() if c.agent_type]

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
        if clear_attention:
            for label in ("loom:blocked", "loom:error"):
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
        if task_suppresses_done_cascade(task):
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

        if changed and recompute:
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
        parent = self.board_tasks.get(task.parent_task_id)
        self._clear_parent_awaiting_input(parent, exclude_task_id=task.id)
        self.recompute_task_health()

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

        LOOM:88 review-derived fixes are structurally parented to the reviewed
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
            if "loom:human" in labels:
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

    def agent_pending_weaver_reply_tasks(self, agent_id: str) -> list[BoardTask]:
        """Return open Weaver follow-up tasks awaiting ``agent_id`` replies."""
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

        If there are no delta ops (e.g. broadcast after a focus change
        where the _emit was called), this is a no-op — the delta was
        already queued by _emit().

        Weaver-stream recompute + `git for-each-ref` prefill are deferred
        to a background worker (`_weaver_recompute_worker`) so UI
        mutations don't wait on git. The worker fires a follow-up
        broadcast with the computed `weaver_streams` ops; clients treat
        it as any other delta frame.
        """
        # Cheap pre-lock bail-out: if nothing has been emitted, skip.
        if not self._delta_ops:
            return
        # Collect weaver-stream affected groups before draining ops so
        # the background worker has something to chew on after the
        # primary frame goes out. Fingerprint cache is updated as a
        # side effect (dedupes ephemeral-only agent_upserts).
        weaver_groups = self._collect_weaver_affected_groups(self._delta_ops)
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
            async with self._ws_clients_lock:
                self._ws_clients -= dead
        if weaver_groups:
            self._schedule_weaver_recompute(weaver_groups)

    def _schedule_weaver_recompute(self, groups: set[str]) -> None:
        """Queue a deferred weaver-stream recompute for ``groups``.

        Spawns a single worker task. If one is already running, merges
        the new groups into its pending set — the worker re-checks
        after each iteration so it drains everything before exiting.
        """
        if not groups:
            return
        self._weaver_recompute_pending |= set(groups)
        task = self._weaver_recompute_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop; caller is off the async path. Skip silently —
            # the next real broadcast will re-queue anything still dirty.
            return
        self._weaver_recompute_task = loop.create_task(
            self._weaver_recompute_worker()
        )

    async def _weaver_recompute_worker(self) -> None:
        """Drain `_weaver_recompute_pending`: prefill branch existence,
        compute stream payloads, broadcast a follow-up delta."""
        from .worktree_streams import prefill_branch_exists_for_state
        try:
            while self._weaver_recompute_pending:
                pending = self._weaver_recompute_pending
                self._weaver_recompute_pending = set()
                try:
                    await prefill_branch_exists_for_state(self)
                except Exception:
                    log.exception("Branch-exists prefill failed")
                if self._emit_weaver_stream_ops(pending):
                    # The follow-up broadcast's own _collect_weaver_*
                    # call returns empty (weaver_streams isn't a trigger
                    # op), so this does not recurse into another worker.
                    await self.broadcast()
        finally:
            self._weaver_recompute_task = None
