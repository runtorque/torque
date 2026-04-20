"""AgentCell dataclass and MatrixState persistence layer."""

import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from aiohttp import web

from .config import DEFAULT_COMMAND, log
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
    last_event_at: float = 0.0  # timestamp of last event received
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
    persistent: bool = False  # architect/engineer survive across sessions
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


# Fields that are ephemeral (not meaningful across restarts)
_EPHEMERAL_FIELDS = ("current_process", "current_path",
                     "current_branch", "git_root",
                     "activity", "activity_detail",
                     "last_event_at", "last_event_text",
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


def task_is_closed(task: Optional[BoardTask]) -> bool:
    return board_task_is_closed(task)


def task_counts_as_done(task: Optional[BoardTask]) -> bool:
    return board_task_counts_as_done(task)


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
    # General > Board
    default_lanes: list[str] = field(default_factory=lambda: list(_DEFAULT_LANES))
    # Keybindings — action name → {modifiers, keycode, character} overrides
    keybindings: dict[str, dict] = field(default_factory=dict)
    # Pipeline
    max_pipeline_depth: int = 10  # 0 = unlimited
    # Events
    max_event_log: int = 500  # max persisted panel events


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self, db: Optional[LoomDB] = None):
        self.db: Optional[LoomDB] = db
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
        # Secondary index: group → set of task ids. Maintained
        # incrementally by `_index_task` / `_unindex_task` (called from
        # the few places that mutate task identity or group). Lets
        # hot-path consumers like compute_worktree_streams skip iterating
        # the full task table when most tasks belong to other groups.
        self._tasks_by_group: dict[str, set[str]] = {}
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
        self.weaver_worklog: dict[str, list[dict]] = {}
        # Delta broadcast accumulator
        self._delta_ops: list[dict] = []
        self._seq: int = 0
        # Per-agent fingerprint of weaver-relevant fields. Lets
        # `_collect_weaver_affected_groups` skip recomputing a group's
        # weaver streams when an agent_upsert only changes ephemeral
        # fields (activity, path, last_event_at, etc.).
        self._agent_weaver_fingerprints: dict[str, tuple] = {}
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
        self._delta_ops.append({"op": op, **kwargs})

    def _emit_agent(self, cell: AgentCell):
        """Emit an agent_upsert delta with the full agent dict."""
        self._emit("agent_upsert", **asdict(cell))

    def _emit_group(self, name: str):
        """Emit a group_update delta with the current member list."""
        self._emit("group_update", name=name,
                   slug=self.group_slugs.get(name, ""),
                   agents=list(self.groups.get(name, [])))

    # -- Task indexes --------------------------------------------------------

    def _index_task(self, task: "BoardTask") -> None:
        """Add a task to the secondary indexes. Idempotent."""
        group = str(getattr(task, "group", "") or "")
        bucket = self._tasks_by_group.setdefault(group, set())
        bucket.add(task.id)

    def _unindex_task(self, task_or_id) -> None:
        """Remove a task from the secondary indexes. Idempotent."""
        if isinstance(task_or_id, str):
            tid = task_or_id
            group = ""
            for g, members in self._tasks_by_group.items():
                if tid in members:
                    group = g
                    break
        else:
            tid = task_or_id.id
            group = str(getattr(task_or_id, "group", "") or "")
        bucket = self._tasks_by_group.get(group)
        if bucket is not None:
            bucket.discard(tid)
            if not bucket:
                self._tasks_by_group.pop(group, None)

    def _reindex_task_group(self, task: "BoardTask",
                            old_group: str, new_group: str) -> None:
        """Move a task between group buckets when its group changes."""
        if old_group == new_group:
            return
        old_bucket = self._tasks_by_group.get(old_group)
        if old_bucket is not None:
            old_bucket.discard(task.id)
            if not old_bucket:
                self._tasks_by_group.pop(old_group, None)
        self._tasks_by_group.setdefault(new_group, set()).add(task.id)

    def _rebuild_task_indexes(self) -> None:
        """Recompute all secondary indexes from scratch (used after bulk load)."""
        self._tasks_by_group = {}
        for task in self.board_tasks.values():
            self._index_task(task)

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
            "weaver_journal": {
                g: self.journal_read(g, limit=50)
                for g in self.weaver_settings
            },
            "weaver_worklog": {
                g: [dict(entry) for entry in entries]
                for g, entries in self.weaver_worklog.items()
            },
            "weaver_streams": self._weaver_streams_snapshot(),
        }

    # -- Targeted persistence helpers ----------------------------------------

    def _db_save_agent(self, cell: AgentCell):
        """Persist a single agent to SQLite."""
        if self.db:
            try:
                self.db.save_agent(cell)
            except Exception:
                log.exception("Failed to save agent %s", cell.id)

    def _db_delete_agent(self, agent_id: str):
        if self.db:
            try:
                self.db.delete_agent(agent_id)
            except Exception:
                log.exception("Failed to delete agent %s", agent_id)

    def _db_save_groups(self):
        """Persist all groups and their memberships."""
        if self.db:
            try:
                self.db.save_groups(self.groups, self.group_slugs)
                for gname, members in self.groups.items():
                    self.db.save_group_members(gname, members)
            except Exception:
                log.exception("Failed to save groups")

    def _db_save_group_settings(self, name: str):
        if self.db and name in self.group_settings:
            try:
                self.db.save_group_settings(name, self.group_settings[name])
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
                self.db.save_board_task(task)
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
                self.db.save_task_id_counter(
                    group_prefix,
                    self.task_id_counters.get(group_prefix, 1),
                )
            except Exception:
                log.exception("Failed to save task ID counter %s", group_prefix)

    def _db_save_pipeline_task_counter(self, root_task_id: str):
        if self.db:
            try:
                self.db.save_pipeline_task_counter(
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
                    self.db.save_task_id_alias(legacy_id, task_id)
            except Exception:
                log.exception("Failed to save task ID alias %s", legacy_id)

    def _db_save_schedule(self, sched: Schedule):
        if self.db:
            try:
                self.db.save_schedule(sched)
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
                self.db.save_board_lanes(self.board_lanes)
            except Exception:
                log.exception("Failed to save board lanes")

    def _db_save_ui(self, key: str, value):
        if self.db:
            try:
                self.db.save_ui_state(key, value)
            except Exception:
                log.exception("Failed to save UI state %s", key)

    def _db_save_global_settings(self):
        if self.db:
            try:
                self.db.save_global_settings(self.global_settings)
            except Exception:
                log.exception("Failed to save global settings")

    def _db_save_auto_dispatch_queue(self, group: str):
        if self.db:
            try:
                self.db.save_auto_dispatch_queue(
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
        weaver_group = str(weaver_group or "").strip()
        weaver_id = str(weaver_id or "").strip()
        try:
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
        if not self.db:
            return
        import time
        try:
            self.db.save_agent_message({
                "agent_id": cell_id,
                "task_id": task_id,
                "timestamp": time.time(),
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
                       entry: str) -> dict:
        """Append an entry to the weaver journal. Returns the entry dict."""
        import time
        ts = time.time()
        entry_id = 0
        if self.db:
            entry_id = self.db.save_journal_entry(group, ts, entry_type, entry)
        evt = {"id": entry_id, "group": group, "timestamp": ts,
               "type": entry_type, "entry": entry}
        self._emit("journal_append", **evt)
        return evt

    def journal_read(self, group: str, limit: int = 20,
                     entry_type: str = "") -> list[dict]:
        """Read recent journal entries for a group."""
        if self.db:
            return self.db.load_journal_entries(group, limit, entry_type)
        return []

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
        """Return recent persisted/current Weaver worklog entries for a group."""
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
        for key, value in fields.items():
            if key in valid:
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
        value = str(task_id or "").strip()
        return self.task_id_aliases.get(value, value)

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

    def recompute_task_health(self, now_ts: float | None = None,
                              *, emit: bool = True,
                              persist: bool = True) -> list[str]:
        """Recompute advisory health for all tasks.

        Health is deterministic and derived from persisted task signals plus
        live agent state. It never mutates task lanes or statuses.
        """
        if not self.board_tasks:
            return []

        from .task_health import compute_task_health, now_iso

        snapshots = compute_task_health(self.board_tasks, self.agents,
                                        now_ts=now_ts)
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
                self._emit("task_upsert", **asdict(task))
            if persist and self.db:
                self._db_save_task(task)
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
        if explicit_id:
            tid = explicit_id
        elif parent_task_id or pipeline_root_id:
            root_id = pipeline_root_id or parent_task_id
            try:
                tid = self._allocate_derived_task_id(group, root_id)
            except ValueError:
                tid = uuid.uuid4().hex[:8]
        else:
            tid = self._allocate_root_task_id(group)
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
        self._index_task(bt)
        if explicit_id and explicit_id != tid:
            self.task_id_aliases[explicit_id] = tid
            self._db_save_task_id_alias(explicit_id)
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
        task = self.board_tasks.get(tid)
        if not task:
            return
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
        old_group = task.group
        for key, value in fields.items():
            if key in valid:
                setattr(task, key, value)
        if "task" in fields:
            task.slug = self._unique_task_slug(task.task, exclude_id=tid)
        if "group" in fields and task.group != old_group:
            self._reindex_task_group(task, old_group, task.group)
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
        self.recompute_task_health()

    def board_remove_task(self, tid: str):
        task = self.board_tasks.pop(tid, None)
        if task:
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
                        position: Optional[int] = None):
        task = self.board_tasks.get(tid)
        if not task or lane not in self.board_lanes:
            return
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
        self.recompute_task_health()

    def board_archive_task(self, tid: str, *,
                           position: Optional[int] = None):
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
        return [t for t in self.board_tasks.values()
                if task_id in t.depends_on]

    def board_get_chain(self, task_id: str) -> list[BoardTask]:
        """Return all tasks in the same pipeline chain, ordered by depth."""
        task = self.board_tasks.get(task_id)
        if not task:
            return []
        root_id = task.pipeline_root_id or task.id
        chain = [t for t in self.board_tasks.values()
                 if (t.pipeline_root_id == root_id) or (t.id == root_id)]
        chain.sort(key=lambda t: (t.pipeline_depth, t.created_at))
        return chain

    def task_open_descendants(self, task_id: str) -> list[BoardTask]:
        """Return all unresolved descendants for ``task_id``."""
        if not task_id:
            return []
        descendants = []
        stack = self.board_get_children(task_id)
        while stack:
            current = stack.pop()
            stack.extend(self.board_get_children(current.id))
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
        return json.dumps({
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
            msg = json.dumps({
                "type": "delta", "seq": self._seq,
                "ops": self._delta_ops,
            })
            self._delta_ops = []
            clients = list(self._ws_clients)
        # Send to every client concurrently so a slow/stuck client
        # doesn't stall delivery to the others (the lock above already
        # guarantees ordering — only one broadcast is in flight at a time).
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
