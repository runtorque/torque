//! In-memory state + dataclass-equivalent structs.
//!
//! Ports the Python `MatrixState` and its dataclasses (`AgentCell`,
//! `BoardTask`, `GroupSettings`, `WeaverSettings`, `GlobalSettings`,
//! `Schedule`, `AutoDispatchQueueEntry`). JSON field names are preserved so
//! the existing JS frontend can consume deltas unchanged.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::delta::DeltaOp;
use crate::slug::{slugify, unique_slug};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

pub const ARCHIVED_LANE: &str = "Archived";
pub const RESERVED_LANES: &[&str] = &["Backlog", "To Do", "In Progress", "Done", ARCHIVED_LANE];

pub fn default_lanes() -> Vec<String> {
    RESERVED_LANES.iter().map(|s| s.to_string()).collect()
}

pub const DEFAULT_WEAVER_AUTONOMY_MODE: &str = "dispatch_when_clear";
pub const DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE: &str = "small";
pub const DEFAULT_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCE: &str = "balanced";
pub const DEFAULT_WEAVER_DIGEST_VERBOSITY: &str = "balanced";
pub const DEFAULT_WEAVER_ESCALATION_STYLE: &str = "note_then_ask";
pub const DEFAULT_WORKTREE_MERGE_CLEANUP: &str = "keep";

// Mandatory weaver events — always pushed regardless of config. These are the
// events the weaver *must* see to keep its mental model accurate: any task
// it dispatched starting, deriving, asking a question, or finishing. Without
// these, the worklog and digest stay empty even when real work is happening.
pub const WEAVER_MANDATORY_EVENTS: &[&str] = &[
    "task_dispatched",
    "task_derived",
    "task_completed",
    "agent_started",
    "agent_finished",
    "agent_reply",
    "agent_error",
    "agent_blocked",
    "ask_created",
    "task_verification_updated",
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn default_true() -> bool {
    true
}

fn default_ten() -> i32 {
    10
}

fn default_five_hundred() -> i32 {
    500
}

fn default_idle_timeout() -> i32 {
    0
}

fn default_two() -> i32 {
    2
}

fn default_sixty() -> i32 {
    60
}

fn default_three_hundred() -> i32 {
    300
}

fn default_loom_worktrees() -> String {
    ".loom/worktrees".to_string()
}

fn default_backlog() -> String {
    "Backlog".to_string()
}

fn default_stopped() -> String {
    "stopped".to_string()
}

fn default_profile() -> String {
    "Default".to_string()
}

fn default_agent_cell_type() -> String {
    "agent".to_string()
}

fn default_iterm2_backend() -> String {
    // Legacy default — in Rust Loom we default to "pty" once the Ghostty
    // backend lands. Kept as "iterm2" for JSON compat during the port.
    "iterm2".to_string()
}

fn default_in_progress() -> String {
    "In Progress".to_string()
}

fn default_healthy() -> String {
    "healthy".to_string()
}

fn default_dispatch_when_clear() -> String {
    DEFAULT_WEAVER_AUTONOMY_MODE.to_string()
}

fn default_small() -> String {
    DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE.to_string()
}

fn default_balanced() -> String {
    DEFAULT_WEAVER_DIGEST_VERBOSITY.to_string()
}

fn default_note_then_ask() -> String {
    DEFAULT_WEAVER_ESCALATION_STYLE.to_string()
}

fn default_keep() -> String {
    DEFAULT_WORKTREE_MERGE_CLEANUP.to_string()
}

fn default_weaver_enabled_events() -> Vec<String> {
    vec![
        "agent_started".into(),
        "task_dispatched".into(),
        "task_derived".into(),
        "task_health_alert".into(),
    ]
}

// ---------------------------------------------------------------------------
// BoardTask
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoardTask {
    pub id: String,
    pub task: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub group: String,
    #[serde(default)]
    pub action_name: String,
    #[serde(default)]
    pub action_vars: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub agent_template: String,
    #[serde(default)]
    pub instructions: String, // deprecated
    #[serde(default)]
    pub context: String, // deprecated
    #[serde(default)]
    pub criteria: String, // deprecated
    #[serde(default = "default_backlog")]
    pub lane: String,
    #[serde(default)]
    pub position: i64,
    #[serde(default)]
    pub agent_id: String,
    #[serde(default)]
    pub reply_agent_id: String,
    #[serde(default)]
    pub labels: Vec<String>,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub lane_entered_at: String,
    #[serde(default)]
    pub provider: String,
    #[serde(default)]
    pub external_id: String,
    #[serde(default)]
    pub external_url: String,
    #[serde(default)]
    pub parent_task_id: String,
    #[serde(default)]
    pub pipeline_depth: i32,
    #[serde(default)]
    pub pipeline_root_id: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub scheduled_at: String,
    #[serde(default)]
    pub messages: Vec<serde_json::Value>,
    #[serde(default)]
    pub depends_on: Vec<String>,
    #[serde(default)]
    pub attachments: Vec<serde_json::Value>,
    #[serde(default = "default_healthy")]
    pub health_state: String,
    #[serde(default)]
    pub health_since: String,
    #[serde(default)]
    pub health_details: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub artifacts: Vec<serde_json::Value>,
    #[serde(default)]
    pub verification_mode: String,
    #[serde(default)]
    pub verification_state: String,
    #[serde(default)]
    pub verification_notes: String,
    #[serde(default)]
    pub verification_updated_at: String,
    #[serde(default)]
    pub verification_updated_by: String,
    #[serde(default)]
    pub verification_summary: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub worktree_boundary: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub resume_after_boundary_task_id: String,
    #[serde(default)]
    pub archived_at: String,
    #[serde(default)]
    pub archived_from_lane: String,
}

impl BoardTask {
    pub fn new_minimal(id: impl Into<String>, task: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            task: task.into(),
            description: String::new(),
            slug: String::new(),
            group: String::new(),
            action_name: String::new(),
            action_vars: serde_json::Map::new(),
            agent_template: String::new(),
            instructions: String::new(),
            context: String::new(),
            criteria: String::new(),
            lane: "Backlog".into(),
            position: 0,
            agent_id: String::new(),
            reply_agent_id: String::new(),
            labels: Vec::new(),
            created_at: String::new(),
            updated_at: String::new(),
            lane_entered_at: String::new(),
            provider: String::new(),
            external_id: String::new(),
            external_url: String::new(),
            parent_task_id: String::new(),
            pipeline_depth: 0,
            pipeline_root_id: String::new(),
            status: String::new(),
            scheduled_at: String::new(),
            messages: Vec::new(),
            depends_on: Vec::new(),
            attachments: Vec::new(),
            health_state: "healthy".into(),
            health_since: String::new(),
            health_details: serde_json::Map::new(),
            artifacts: Vec::new(),
            verification_mode: String::new(),
            verification_state: String::new(),
            verification_notes: String::new(),
            verification_updated_at: String::new(),
            verification_updated_by: String::new(),
            verification_summary: serde_json::Map::new(),
            worktree_boundary: serde_json::Map::new(),
            resume_after_boundary_task_id: String::new(),
            archived_at: String::new(),
            archived_from_lane: String::new(),
        }
    }
}

pub fn board_task_is_archived(task: Option<&BoardTask>) -> bool {
    matches!(task, Some(t) if t.lane == ARCHIVED_LANE)
}

pub fn board_task_is_closed(task: Option<&BoardTask>) -> bool {
    matches!(task, Some(t) if t.lane == "Done" || t.lane == ARCHIVED_LANE)
}

pub fn board_task_counts_as_done(task: Option<&BoardTask>) -> bool {
    match task {
        None => false,
        Some(t) if t.lane == "Done" => true,
        Some(t) => t.lane == ARCHIVED_LANE && t.archived_from_lane == "Done",
    }
}

// ---------------------------------------------------------------------------
// AgentCell
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentCell {
    pub id: String,
    pub name: String,
    pub group: String,
    #[serde(default)]
    pub slug: String,
    #[serde(default = "default_agent_cell_type")]
    pub cell_type: String,
    #[serde(default = "default_iterm2_backend")]
    pub terminal_backend: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default = "default_profile")]
    pub profile: String,
    #[serde(default)]
    pub command: String,
    #[serde(default)]
    pub directory: String,
    #[serde(default)]
    pub tab_color: String,
    #[serde(default)]
    pub icon: String,
    #[serde(default)]
    pub template: String,
    #[serde(default)]
    pub window_id: String,
    #[serde(default)]
    pub parent_id: String,
    #[serde(default = "default_stopped")]
    pub status: String,

    // terminal tracking
    #[serde(default)]
    pub current_process: String,
    #[serde(default)]
    pub current_path: String,
    #[serde(default)]
    pub current_branch: String,
    #[serde(default)]
    pub git_root: String,

    // worktree config
    #[serde(default)]
    pub worktree_path: String,
    #[serde(default)]
    pub worktree_branch: String,
    #[serde(default)]
    pub worktree_repo_root: String,
    #[serde(default = "default_loom_worktrees")]
    pub worktree_base_dir: String,
    #[serde(default)]
    pub worktree_base_branch: String,
    #[serde(default)]
    pub worktree_auto_checkpoint: bool,
    #[serde(default)]
    pub checkpoint_on_progress: bool,
    #[serde(default = "default_true")]
    pub worktree_merge_squash: bool,

    // agent awareness
    #[serde(default)]
    pub agent_type: String,
    #[serde(default)]
    pub agent_session_id: String,
    #[serde(default)]
    pub activity: String,
    #[serde(default)]
    pub activity_detail: String,
    #[serde(default)]
    pub last_event_at: f64,
    #[serde(default)]
    pub last_event_text: String,
    #[serde(default)]
    pub session_tokens_in: i64,
    #[serde(default)]
    pub session_tokens_out: i64,
    #[serde(default)]
    pub error_message: String,
    #[serde(default)]
    pub needs_attention: bool,
    #[serde(default)]
    pub last_summary: String,

    // dispatch history
    #[serde(default)]
    pub tasks_dispatched: i32,
    #[serde(default)]
    pub created_by_weaver_id: String,
    #[serde(default)]
    pub current_task_id: String,
    #[serde(default = "default_true")]
    pub session_resume: bool,
    #[serde(default = "default_idle_timeout")]
    pub idle_timeout: i32,

    // worktree status (ephemeral)
    #[serde(default)]
    pub worktree_dirty: bool,
    #[serde(default)]
    pub worktree_diff: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub worktree_changed_files: Vec<String>,
    #[serde(default)]
    pub worktree_checkpoints: i32,
    #[serde(default)]
    pub worktree_behind: i32,
    #[serde(default)]
    pub worktree_ahead: i32,
    #[serde(default)]
    pub worktree_merged: bool,

    #[serde(default)]
    pub mcp_messages: Vec<serde_json::Value>,
    #[serde(default)]
    pub last_checkpoint_at: f64,
    #[serde(default)]
    pub pending_weaver_message: bool,
}

impl AgentCell {
    pub fn new(id: impl Into<String>, name: impl Into<String>, group: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            group: group.into(),
            slug: String::new(),
            cell_type: "agent".into(),
            terminal_backend: "iterm2".into(),
            session_id: None,
            profile: "Default".into(),
            command: String::new(),
            directory: String::new(),
            tab_color: String::new(),
            icon: String::new(),
            template: String::new(),
            window_id: String::new(),
            parent_id: String::new(),
            status: "stopped".into(),
            current_process: String::new(),
            current_path: String::new(),
            current_branch: String::new(),
            git_root: String::new(),
            worktree_path: String::new(),
            worktree_branch: String::new(),
            worktree_repo_root: String::new(),
            worktree_base_dir: ".loom/worktrees".into(),
            worktree_base_branch: String::new(),
            worktree_auto_checkpoint: false,
            checkpoint_on_progress: false,
            worktree_merge_squash: true,
            agent_type: String::new(),
            agent_session_id: String::new(),
            activity: String::new(),
            activity_detail: String::new(),
            last_event_at: 0.0,
            last_event_text: String::new(),
            session_tokens_in: 0,
            session_tokens_out: 0,
            error_message: String::new(),
            needs_attention: false,
            last_summary: String::new(),
            tasks_dispatched: 0,
            created_by_weaver_id: String::new(),
            current_task_id: String::new(),
            session_resume: true,
            idle_timeout: 0,
            worktree_dirty: false,
            worktree_diff: serde_json::Map::new(),
            worktree_changed_files: Vec::new(),
            worktree_checkpoints: 0,
            worktree_behind: 0,
            worktree_ahead: 0,
            worktree_merged: false,
            mcp_messages: Vec::new(),
            last_checkpoint_at: 0.0,
            pending_weaver_message: false,
        }
    }
}

// Ephemeral fields — cleared on load (not persisted meaningfully).
// Mirrors `loom/state.py::_EPHEMERAL_FIELDS`. Actual clearing happens in
// `loom-core::db::clear_agent_ephemeral`; keep the two lists in sync.
pub const EPHEMERAL_FIELDS: &[&str] = &[
    "current_process",
    "current_path",
    "current_branch",
    "git_root",
    "activity",
    "activity_detail",
    "last_event_at",
    "last_event_text",
    "session_tokens_in",
    "session_tokens_out",
    "error_message",
    "needs_attention",
    "last_summary",
    "current_task_id",
    "worktree_dirty",
    "worktree_diff",
    "worktree_changed_files",
    "worktree_checkpoints",
    "last_checkpoint_at",
    "mcp_messages",
    "pending_weaver_message",
];

// ---------------------------------------------------------------------------
// GroupSettings
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupSettings {
    #[serde(default)]
    pub default_directory: String,
    #[serde(default = "default_iterm2_backend")]
    pub default_terminal_backend: String,
    #[serde(default)]
    pub profile: String,
    #[serde(default)]
    pub shell: String,
    #[serde(default)]
    pub tab_color: String,
    #[serde(default)]
    pub env_vars: BTreeMap<String, String>,
    #[serde(default)]
    pub env_file: String,
    #[serde(default)]
    pub auto_terminals: i32,
    #[serde(default)]
    pub max_agents: i32,
    #[serde(default)]
    pub collapsed_default: bool,
    #[serde(default)]
    pub filter_by_window: bool,

    // agent overrides
    #[serde(default)]
    pub agent_directory: String,
    #[serde(default)]
    pub agent_profile: String,
    #[serde(default)]
    pub agent_shell: String,
    #[serde(default)]
    pub agent_tab_color: String,
    #[serde(default)]
    pub agent_env_vars: BTreeMap<String, String>,
    #[serde(default)]
    pub agent_env_file: String,
    #[serde(default)]
    pub default_agent_template: String,
    #[serde(default)]
    pub agent_provider: String,
    #[serde(default)]
    pub agent_boot_command: String,
    #[serde(default)]
    pub agent_model: String,
    #[serde(default)]
    pub agent_reasoning_effort: String,
    #[serde(default = "default_true")]
    pub git_worktree: bool,
    #[serde(default = "default_loom_worktrees")]
    pub worktree_base_dir: String,
    #[serde(default)]
    pub worktree_base_branch: String,
    #[serde(default = "default_true")]
    pub worktree_auto_checkpoint: bool,
    #[serde(default)]
    pub checkpoint_on_progress: bool,
    #[serde(default)]
    pub worktree_merge_squash: bool,
    #[serde(default)]
    pub worktree_merge_instructions: String,
    #[serde(default = "default_keep")]
    pub worktree_merge_cleanup: String,
    #[serde(default)]
    pub worktree_merge_preserve_diff: bool,
    #[serde(default)]
    pub worktree_symlinks: Vec<String>,
    #[serde(default = "default_true")]
    pub agent_session_resume: bool,
    #[serde(default = "default_idle_timeout")]
    pub agent_idle_timeout: i32,
    #[serde(default)]
    pub agent_always_custom_dialog: bool,

    // notifications
    #[serde(default)]
    pub notifications: bool,
    #[serde(default = "default_true")]
    pub notify_on_finish: bool,
    #[serde(default = "default_true")]
    pub notify_on_error: bool,
    #[serde(default = "default_true")]
    pub notify_on_attention: bool,

    // terminal overrides
    #[serde(default)]
    pub terminal_name_prefix: String,
    #[serde(default)]
    pub terminal_boot_command: String,
    #[serde(default)]
    pub terminal_command_args: String,
    #[serde(default)]
    pub terminal_init_script: String,
    #[serde(default)]
    pub terminal_directory: String,
    #[serde(default)]
    pub terminal_profile: String,
    #[serde(default)]
    pub terminal_shell: String,
    #[serde(default)]
    pub terminal_tab_color: String,
    #[serde(default)]
    pub terminal_env_vars: BTreeMap<String, String>,
    #[serde(default)]
    pub terminal_env_file: String,
    #[serde(default)]
    pub terminal_always_custom_dialog: bool,
    #[serde(default)]
    pub terminal_close_on_disconnect: bool,

    // board / dispatch
    #[serde(default = "default_in_progress")]
    pub dispatch_lane: String,
    #[serde(default)]
    pub dispatch_auto_terminals: bool,
    #[serde(default)]
    pub board_default_labels: Vec<String>,
    #[serde(default)]
    pub board_default_lane: String,
    #[serde(default)]
    pub board_default_action: String,

    // weaver
    #[serde(default)]
    pub weaver_agent_id: String,
}

impl Default for GroupSettings {
    /// Go through serde so `#[serde(default = "…")]` field defaults are
    /// honored (e.g. `git_worktree = true`, `worktree_auto_checkpoint = true`).
    /// Falls back to `unwrap_or_else` safety net, though the `{}` payload
    /// should always deserialize cleanly.
    fn default() -> Self {
        serde_json::from_value::<Self>(serde_json::json!({})).unwrap_or_else(|_| Self {
            default_directory: String::new(),
            default_terminal_backend: default_iterm2_backend(),
            profile: String::new(),
            shell: String::new(),
            tab_color: String::new(),
            env_vars: BTreeMap::new(),
            env_file: String::new(),
            auto_terminals: 0,
            max_agents: 0,
            collapsed_default: false,
            filter_by_window: false,
            agent_directory: String::new(),
            agent_profile: String::new(),
            agent_shell: String::new(),
            agent_tab_color: String::new(),
            agent_env_vars: BTreeMap::new(),
            agent_env_file: String::new(),
            default_agent_template: String::new(),
            agent_provider: String::new(),
            agent_boot_command: String::new(),
            agent_model: String::new(),
            agent_reasoning_effort: String::new(),
            git_worktree: true,
            worktree_base_dir: default_loom_worktrees(),
            worktree_base_branch: String::new(),
            worktree_auto_checkpoint: true,
            checkpoint_on_progress: false,
            worktree_merge_squash: false,
            worktree_merge_instructions: String::new(),
            worktree_merge_cleanup: default_keep(),
            worktree_merge_preserve_diff: false,
            worktree_symlinks: Vec::new(),
            agent_session_resume: true,
            agent_idle_timeout: 0,
            agent_always_custom_dialog: false,
            notifications: false,
            notify_on_finish: true,
            notify_on_error: true,
            notify_on_attention: true,
            terminal_name_prefix: String::new(),
            terminal_boot_command: String::new(),
            terminal_command_args: String::new(),
            terminal_init_script: String::new(),
            terminal_directory: String::new(),
            terminal_profile: String::new(),
            terminal_shell: String::new(),
            terminal_tab_color: String::new(),
            terminal_env_vars: BTreeMap::new(),
            terminal_env_file: String::new(),
            terminal_always_custom_dialog: false,
            terminal_close_on_disconnect: false,
            dispatch_lane: default_in_progress(),
            dispatch_auto_terminals: false,
            board_default_labels: Vec::new(),
            board_default_lane: String::new(),
            board_default_action: String::new(),
            weaver_agent_id: String::new(),
        })
    }
}

// ---------------------------------------------------------------------------
// WeaverSettings
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WeaverSettings {
    #[serde(default)]
    pub group: String,
    #[serde(default = "default_sixty")]
    pub push_interval: i32,
    #[serde(default = "default_three_hundred")]
    pub max_interval: i32,
    #[serde(default = "default_three_hundred")]
    pub heartbeat_interval: i32,
    #[serde(default = "default_two")]
    pub default_worker_concurrency: i32,
    #[serde(default = "default_dispatch_when_clear")]
    pub autonomy_mode: String,
    #[serde(default = "default_small")]
    pub wave_size_preference: String,
    #[serde(default = "default_balanced")]
    pub same_agent_follow_up_preference: String,
    #[serde(default = "default_balanced")]
    pub digest_verbosity: String,
    #[serde(default = "default_note_then_ask")]
    pub escalation_style: String,
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub custom_instructions: String,
    #[serde(default)]
    pub restrict_to_created_agents: bool,
    #[serde(default)]
    pub pending_question: String,
    #[serde(default)]
    pub pending_note: String,
    #[serde(default)]
    pub pending_note_kind: String,
    #[serde(default)]
    pub weaver_provider: String,
    #[serde(default)]
    pub weaver_boot_command: String,
    #[serde(default)]
    pub weaver_model: String,
    #[serde(default)]
    pub weaver_reasoning_effort: String,
    #[serde(default)]
    pub weaver_directory: String,
    #[serde(default)]
    pub weaver_profile: String,
    #[serde(default)]
    pub weaver_shell: String,
    #[serde(default)]
    pub weaver_tab_color: String,
    #[serde(default = "default_weaver_enabled_events")]
    pub enabled_events: Vec<String>,
}

impl Default for WeaverSettings {
    fn default() -> Self {
        Self {
            group: String::new(),
            push_interval: 60,
            max_interval: 300,
            heartbeat_interval: 300,
            default_worker_concurrency: 2,
            autonomy_mode: DEFAULT_WEAVER_AUTONOMY_MODE.into(),
            wave_size_preference: DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE.into(),
            same_agent_follow_up_preference: DEFAULT_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCE.into(),
            digest_verbosity: DEFAULT_WEAVER_DIGEST_VERBOSITY.into(),
            escalation_style: DEFAULT_WEAVER_ESCALATION_STYLE.into(),
            paused: false,
            custom_instructions: String::new(),
            restrict_to_created_agents: false,
            pending_question: String::new(),
            pending_note: String::new(),
            pending_note_kind: String::new(),
            weaver_provider: String::new(),
            weaver_boot_command: String::new(),
            weaver_model: String::new(),
            weaver_reasoning_effort: String::new(),
            weaver_directory: String::new(),
            weaver_profile: String::new(),
            weaver_shell: String::new(),
            weaver_tab_color: String::new(),
            enabled_events: default_weaver_enabled_events(),
        }
    }
}

// ---------------------------------------------------------------------------
// GlobalSettings
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlobalSettings {
    #[serde(default)]
    pub default_command: String,
    #[serde(default = "default_true")]
    pub filter_by_window: bool,
    #[serde(default = "default_true")]
    pub focus_new_tabs: bool,
    #[serde(default)]
    pub focus_on_click: bool,
    #[serde(default = "default_lanes")]
    pub default_lanes: Vec<String>,
    #[serde(default)]
    pub keybindings: BTreeMap<String, serde_json::Value>,
    #[serde(default = "default_ten")]
    pub max_pipeline_depth: i32,
    #[serde(default = "default_five_hundred")]
    pub max_event_log: i32,
}

impl Default for GlobalSettings {
    fn default() -> Self {
        Self {
            default_command: String::new(),
            filter_by_window: true,
            focus_new_tabs: true,
            focus_on_click: false,
            default_lanes: default_lanes(),
            keybindings: BTreeMap::new(),
            max_pipeline_depth: 10,
            max_event_log: 500,
        }
    }
}

// ---------------------------------------------------------------------------
// Schedule + AutoDispatchQueueEntry
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Schedule {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub task_template: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub group: String,
    #[serde(default)]
    pub action_name: String,
    #[serde(default)]
    pub action_vars: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub agent_template: String,
    #[serde(default)]
    pub labels: Vec<String>,
    #[serde(default)]
    pub cron_expr: String,
    #[serde(default)]
    pub scheduled_at: String,
    #[serde(default)]
    pub timezone: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub last_run_at: String,
    #[serde(default)]
    pub next_run_at: String,
    #[serde(default)]
    pub run_count: i64,
    #[serde(default)]
    pub last_task_id: String,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AutoDispatchQueueEntry {
    pub task_id: String,
    #[serde(default)]
    pub agent_group: String,
    #[serde(default)]
    pub max_concurrent: i32,
    #[serde(default)]
    pub target_agent_id: String,
    #[serde(default)]
    pub weaver_owner_id: String,
    #[serde(default)]
    pub enqueued_at: String,
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

/// A shared memory entry — finding, decision, warning, handoff, or note.
/// Lightly scoped (project / group / pipeline / task) so agents can retrieve
/// the ones relevant to their current context.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryEntry {
    pub id: String,
    #[serde(default)]
    pub project_key: String,
    #[serde(default)]
    pub group_name: String,
    /// `task` | `pipeline` | `group` | `project` — what the scope_ref refers to.
    #[serde(default)]
    pub scope_kind: String,
    /// ID of the referenced scope (task id, pipeline root id, group name,
    /// project key). Empty if `scope_kind == "project"` for a global entry.
    #[serde(default)]
    pub scope_ref: String,
    /// `finding` | `decision` | `warning` | `handoff` | `note`.
    #[serde(default)]
    pub entry_type: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub pinned: bool,
    #[serde(default)]
    pub task_id: String,
    /// What produced this entry (e.g. `agent`, `weaver`, `human`).
    #[serde(default)]
    pub source_kind: String,
    #[serde(default)]
    pub source_id: String,
    #[serde(default)]
    pub source_name: String,
    /// `durable` | `transient`. Transient entries may be expired.
    #[serde(default = "default_durable")]
    pub retention_kind: String,
    /// RFC3339 timestamp or empty.
    #[serde(default)]
    pub expires_at: String,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    /// Denormalized link targets, populated on load (`memory_links` join).
    #[serde(default)]
    pub links: Vec<MemoryLink>,
}

fn default_durable() -> String {
    "durable".to_string()
}

/// A many-to-many association between a memory entry and another Loom object.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryLink {
    pub entry_id: String,
    /// `task` | `agent` | `pipeline`.
    pub target_kind: String,
    pub target_ref: String,
    #[serde(default)]
    pub created_at: String,
}

// ---------------------------------------------------------------------------
// Multi-pane content layout
// ---------------------------------------------------------------------------

/// Identifies which kind of panel a leaf in the content layout hosts. The UI
/// renders one view per kind. `Terminal` is the only kind currently rendered
/// as a real surface; other kinds (board / actions / weaver / etc) show a
/// placeholder until those panels are ported.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PanelKind {
    /// Selected-agent terminal — falls back to `selected_agent_id` if `id` is
    /// `None`. Use `id: Some(...)` to pin a specific agent's terminal to a pane.
    Terminal {
        id: Option<String>,
    },
    /// The agent + terminal tree. Defaults to the Left dock zone. Movable
    /// like any other panel, per the dock system.
    Sidebar,
    Board,
    Actions,
    Memory,
    Events,
    Templates,
    Context {
        agent_id: Option<String>,
    },
    Weaver {
        group: Option<String>,
    },
    /// Empty pane — shown as the placeholder hint text.
    Placeholder,
}

impl Default for PanelKind {
    fn default() -> Self {
        // Default content area: the currently selected agent's terminal (or
        // placeholder if none selected).
        PanelKind::Terminal { id: None }
    }
}

/// Tree of nested splits that defines the content area layout. Leaves are
/// `PanelKind`s; inner nodes split horizontally or vertically.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum LayoutNode {
    Leaf {
        panel: PanelKind,
    },
    Split {
        axis: SplitAxis,
        /// Position of the divider as a fraction in [0, 1]. The UI clamps.
        ratio: f64,
        first: Box<LayoutNode>,
        second: Box<LayoutNode>,
    },
}

impl Default for LayoutNode {
    fn default() -> Self {
        LayoutNode::Leaf {
            panel: PanelKind::default(),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SplitAxis {
    /// Children laid out left-to-right (a vertical divider).
    Horizontal,
    /// Children laid out top-to-bottom (a horizontal divider).
    Vertical,
}

impl LayoutNode {
    /// Walk the leaves left-to-right / top-to-bottom.
    pub fn leaves(&self) -> Vec<&PanelKind> {
        let mut out = Vec::new();
        self.collect(&mut out);
        out
    }

    fn collect<'a>(&'a self, out: &mut Vec<&'a PanelKind>) {
        match self {
            LayoutNode::Leaf { panel } => out.push(panel),
            LayoutNode::Split { first, second, .. } => {
                first.collect(out);
                second.collect(out);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Dock layout (edge-zones around the center tileable grid)
// ---------------------------------------------------------------------------

/// Named regions panels can dock to.
///
/// - `Top`/`Left`/`Right`/`Bottom` are edge bars around the center. A panel
///   docked there gets that bar; only one panel per zone for v1 (tabbed
///   docks are a v2 extension).
/// - `Center` is the always-visible tileable-grid area (the existing
///   `LayoutNode` tree with terminals + any panels the user nested there).
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum DockZone {
    Top,
    Left,
    Right,
    Bottom,
    Center,
}

/// Edge-zone sizes as fractions of the window in the relevant dimension.
/// `top`/`bottom` are fractions of window height; `left`/`right` of width.
/// Sum must leave room for the center (we clamp each to [0.0, 0.7]).
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct DockRatios {
    #[serde(default = "default_ratio_top")]
    pub top: f64,
    #[serde(default = "default_ratio_left")]
    pub left: f64,
    #[serde(default = "default_ratio_right")]
    pub right: f64,
    #[serde(default = "default_ratio_bottom")]
    pub bottom: f64,
}

fn default_ratio_top() -> f64 {
    0.0
}
fn default_ratio_left() -> f64 {
    0.22
}
fn default_ratio_right() -> f64 {
    0.0
}
fn default_ratio_bottom() -> f64 {
    0.32
}

impl Default for DockRatios {
    fn default() -> Self {
        Self {
            top: default_ratio_top(),
            left: default_ratio_left(),
            right: default_ratio_right(),
            bottom: default_ratio_bottom(),
        }
    }
}

impl DockRatios {
    pub fn clamp(self) -> Self {
        Self {
            top: self.top.clamp(0.0, 0.7),
            left: self.left.clamp(0.0, 0.7),
            right: self.right.clamp(0.0, 0.7),
            bottom: self.bottom.clamp(0.0, 0.7),
        }
    }
}

/// Edge-zone configuration for the dock system. The `Center` zone lives
/// in `MatrixState::content_layout` (unchanged); this struct tracks only
/// the four optional edge bars around it.
///
/// A single-panel zone is a `Leaf(Panel)` layout. Nested splits work the
/// same as in the center (so a zone can itself host a split of panels).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DockEdges {
    #[serde(default)]
    pub top: Option<LayoutNode>,
    #[serde(default)]
    pub left: Option<LayoutNode>,
    #[serde(default)]
    pub right: Option<LayoutNode>,
    #[serde(default)]
    pub bottom: Option<LayoutNode>,
    #[serde(default)]
    pub ratios: DockRatios,
}

impl Default for DockEdges {
    fn default() -> Self {
        Self::default_for_fresh_install()
    }
}

impl DockEdges {
    /// Fresh-install defaults: sidebar on the left, Board at the bottom,
    /// top + right hidden.
    pub fn default_for_fresh_install() -> Self {
        Self {
            top: None,
            left: Some(LayoutNode::Leaf {
                panel: PanelKind::Sidebar,
            }),
            right: None,
            bottom: Some(LayoutNode::Leaf {
                panel: PanelKind::Board,
            }),
            ratios: DockRatios::default(),
        }
    }

    pub fn layout_for(&self, zone: DockZone) -> Option<&LayoutNode> {
        match zone {
            DockZone::Top => self.top.as_ref(),
            DockZone::Left => self.left.as_ref(),
            DockZone::Right => self.right.as_ref(),
            DockZone::Bottom => self.bottom.as_ref(),
            DockZone::Center => None,
        }
    }

    /// Set (or clear) an edge zone. Returns `true` if the value changed.
    pub fn set_edge(&mut self, zone: DockZone, layout: Option<LayoutNode>) -> bool {
        let slot = match zone {
            DockZone::Top => &mut self.top,
            DockZone::Left => &mut self.left,
            DockZone::Right => &mut self.right,
            DockZone::Bottom => &mut self.bottom,
            DockZone::Center => return false,
        };
        let new_json = serde_json::to_string(&layout).unwrap_or_default();
        let old_json = serde_json::to_string(&*slot).unwrap_or_default();
        if new_json == old_json {
            return false;
        }
        *slot = layout;
        true
    }
}

/// Full dock view (edges + center) — used for snapshotting out to the UI.
#[derive(Debug, Clone, Serialize)]
pub struct DockLayout {
    pub top: Option<LayoutNode>,
    pub left: Option<LayoutNode>,
    pub right: Option<LayoutNode>,
    pub bottom: Option<LayoutNode>,
    pub center: LayoutNode,
    pub ratios: DockRatios,
}

impl Default for DockLayout {
    fn default() -> Self {
        let edges = DockEdges::default_for_fresh_install();
        Self {
            top: edges.top,
            left: edges.left,
            right: edges.right,
            bottom: edges.bottom,
            center: LayoutNode::default(),
            ratios: edges.ratios,
        }
    }
}

// ---------------------------------------------------------------------------
// MatrixState
// ---------------------------------------------------------------------------

/// In-memory authoritative state. All mutations flow through &mut self methods
/// that emit delta ops; `drain_deltas` hands them to the broadcast layer.
pub struct MatrixState {
    pub agents: HashMap<String, AgentCell>,
    pub groups: HashMap<String, Vec<String>>, // group name → ordered member IDs
    pub groups_order: Vec<String>,            // ordered group names
    pub group_slugs: HashMap<String, String>,
    pub group_settings: HashMap<String, GroupSettings>,
    pub active_session_id: Option<String>,
    pub current_window_id: Option<String>,

    pub children: HashMap<String, Vec<String>>, // agent_id → child terminal ids

    pub global_settings: GlobalSettings,

    pub board_lanes: Vec<String>,
    pub board_tasks: HashMap<String, BoardTask>,
    pub tasks_by_group: HashMap<String, HashSet<String>>,
    pub task_id_aliases: HashMap<String, String>,
    pub task_id_counters: HashMap<String, u64>,
    pub pipeline_task_counters: HashMap<String, u64>,

    pub schedules: HashMap<String, Schedule>,
    pub auto_dispatch_queues: HashMap<String, Vec<AutoDispatchQueueEntry>>,

    pub panel_active: String,
    pub board_panel_height: i32,
    pub standalone_panel_layout: serde_json::Value,
    pub events_dismissed_attention: HashMap<String, f64>,
    pub board_filters_by_group: HashMap<String, serde_json::Value>,
    pub board_saved_views_by_group: HashMap<String, serde_json::Value>,
    pub board_lane_sorts_by_group: HashMap<String, serde_json::Value>,
    pub board_card_density_by_group: HashMap<String, String>,

    pub weaver_settings: HashMap<String, WeaverSettings>,
    pub weaver_journal: HashMap<String, Vec<serde_json::Value>>,
    pub weaver_worklog: HashMap<String, Vec<serde_json::Value>>,

    /// Memory entries, keyed by id. Links are denormalized onto each entry.
    pub memory_entries: HashMap<String, MemoryEntry>,

    // UI state (persisted)
    pub selected_agent_id: Option<String>,
    /// Center-zone layout tree. `None` means "fall back to a single Terminal
    /// leaf bound to `selected_agent_id` (or Placeholder if unset)" — the
    /// default behavior for users who haven't customized the layout.
    pub content_layout: Option<LayoutNode>,
    /// Edge zones (top / left / right / bottom) + ratios. The center lives
    /// in `content_layout`. Together they form the full [`DockLayout`]
    /// surfaced to the UI via [`MatrixState::effective_dock_layout`].
    pub dock_edges: DockEdges,

    // Delta accumulator
    delta_ops: Vec<DeltaOp>,
    seq: u64,
}

impl Default for MatrixState {
    fn default() -> Self {
        Self::new()
    }
}

impl MatrixState {
    pub fn new() -> Self {
        Self {
            agents: HashMap::new(),
            groups: HashMap::new(),
            groups_order: Vec::new(),
            group_slugs: HashMap::new(),
            group_settings: HashMap::new(),
            active_session_id: None,
            current_window_id: None,
            children: HashMap::new(),
            global_settings: GlobalSettings::default(),
            board_lanes: default_lanes(),
            board_tasks: HashMap::new(),
            tasks_by_group: HashMap::new(),
            task_id_aliases: HashMap::new(),
            task_id_counters: HashMap::new(),
            pipeline_task_counters: HashMap::new(),
            schedules: HashMap::new(),
            auto_dispatch_queues: HashMap::new(),
            panel_active: String::new(),
            board_panel_height: 0,
            standalone_panel_layout: serde_json::json!({}),
            events_dismissed_attention: HashMap::new(),
            board_filters_by_group: HashMap::new(),
            board_saved_views_by_group: HashMap::new(),
            board_lane_sorts_by_group: HashMap::new(),
            board_card_density_by_group: HashMap::new(),
            weaver_settings: HashMap::new(),
            weaver_journal: HashMap::new(),
            weaver_worklog: HashMap::new(),
            memory_entries: HashMap::new(),
            selected_agent_id: None,
            content_layout: None,
            dock_edges: DockEdges::default_for_fresh_install(),
            delta_ops: Vec::new(),
            seq: 0,
        }
    }

    // ---- Delta plumbing ---------------------------------------------------

    pub fn emit(&mut self, op: DeltaOp) {
        self.delta_ops.push(op);
    }

    pub fn emit_agent(&mut self, id: &str) {
        if let Some(cell) = self.agents.get(id) {
            let value = serde_json::to_value(cell).unwrap_or(serde_json::Value::Null);
            self.delta_ops.push(DeltaOp::AgentUpsert(value));
        }
    }

    pub fn emit_group(&mut self, name: &str) {
        let slug = self.group_slugs.get(name).cloned().unwrap_or_default();
        let agents = self.groups.get(name).cloned().unwrap_or_default();
        self.delta_ops.push(DeltaOp::GroupUpdate {
            name: name.to_string(),
            slug,
            agents,
        });
    }

    pub fn emit_task(&mut self, id: &str) {
        if let Some(task) = self.board_tasks.get(id) {
            let value = serde_json::to_value(task).unwrap_or(serde_json::Value::Null);
            self.delta_ops.push(DeltaOp::TaskUpsert(value));
        }
    }

    /// Prepend a weaver worklog entry to the in-memory list (newest first)
    /// and emit a delta so live WS clients see it instantly. `entry` should
    /// already include `id`, `group`, `task_id`, `task_title`, `agent_id`,
    /// `agent_name`, `agent_slug`, `started_at`. Capped at 200 entries
    /// per group to bound memory — older entries fall off the tail.
    pub fn append_weaver_worklog_entry(
        &mut self,
        group: &str,
        entry: serde_json::Value,
    ) {
        const WORKLOG_MEMORY_LIMIT: usize = 200;
        let entries = self.weaver_worklog.entry(group.to_string()).or_default();
        entries.insert(0, entry.clone());
        if entries.len() > WORKLOG_MEMORY_LIMIT {
            entries.truncate(WORKLOG_MEMORY_LIMIT);
        }
        self.delta_ops.push(DeltaOp::WeaverWorklogAppend {
            group: group.to_string(),
            entry,
        });
    }

    /// Pop all accumulated deltas, bump the seq, return a message tuple.
    pub fn drain_deltas(&mut self) -> Option<(u64, Vec<DeltaOp>)> {
        if self.delta_ops.is_empty() {
            return None;
        }
        self.seq += 1;
        let ops = std::mem::take(&mut self.delta_ops);
        Some((self.seq, ops))
    }

    pub fn current_seq(&self) -> u64 {
        self.seq
    }

    // ---- Weaver / board helpers -----------------------------------------

    pub fn tasks_in_group(&self, group: &str) -> Vec<&BoardTask> {
        let mut tasks = self
            .tasks_by_group
            .get(group)
            .into_iter()
            .flat_map(|ids| ids.iter())
            .filter_map(|id| self.board_tasks.get(id))
            .collect::<Vec<_>>();
        tasks.sort_by(|a, b| {
            a.position
                .cmp(&b.position)
                .then_with(|| a.created_at.cmp(&b.created_at))
                .then_with(|| a.id.cmp(&b.id))
        });
        tasks
    }

    pub fn get_group_settings(&self, name: &str) -> GroupSettings {
        self.group_settings.get(name).cloned().unwrap_or_default()
    }

    pub fn get_weaver_settings(&self, group: &str) -> WeaverSettings {
        self.weaver_settings
            .get(group)
            .cloned()
            .unwrap_or_else(|| WeaverSettings {
                group: group.to_string(),
                ..WeaverSettings::default()
            })
    }

    pub fn update_weaver_settings(
        &mut self,
        group: &str,
        patch: serde_json::Map<String, serde_json::Value>,
    ) -> crate::Result<WeaverSettings> {
        let current = self
            .weaver_settings
            .get(group)
            .cloned()
            .unwrap_or_else(|| WeaverSettings {
                group: group.to_string(),
                ..WeaverSettings::default()
            });
        let mut merged = serde_json::to_value(current)?;
        if let Some(obj) = merged.as_object_mut() {
            for (k, v) in patch {
                obj.insert(k, v);
            }
        }
        let next: WeaverSettings = serde_json::from_value(merged)
            .map_err(|e| crate::Error::validation(format!("invalid weaver settings: {e}")))?;
        self.weaver_settings.insert(group.to_string(), next.clone());
        let mut fields = serde_json::to_value(&next)
            .ok()
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_default();
        fields.remove("group");
        self.emit(DeltaOp::WeaverSettingsUpdate {
            group: group.to_string(),
            fields,
        });
        Ok(next)
    }

    pub fn weaver_restricts_to_created_agents(&self, group: &str) -> bool {
        self.get_weaver_settings(group).restrict_to_created_agents
    }

    pub fn agent_is_visible_to_weaver(&self, weaver_id: &str, agent_id: &str) -> bool {
        let Some(weaver) = self.agents.get(weaver_id) else {
            return false;
        };
        let Some(agent) = self.agents.get(agent_id) else {
            return false;
        };
        if weaver.cell_type != "agent" || agent.cell_type != "agent" {
            return false;
        }
        if weaver.group.is_empty() || agent.group != weaver.group {
            return false;
        }
        if !self.weaver_restricts_to_created_agents(&weaver.group) {
            return true;
        }
        agent.created_by_weaver_id == weaver.id
    }

    pub fn resolve_task_ident(&self, ident: &str) -> Option<String> {
        let ident = ident.trim();
        if ident.is_empty() {
            return None;
        }
        if self.board_tasks.contains_key(ident) {
            return Some(ident.to_string());
        }
        let mut prefix_matches = self
            .board_tasks
            .keys()
            .filter(|id| id.starts_with(ident))
            .cloned()
            .collect::<Vec<_>>();
        prefix_matches.sort();
        if prefix_matches.len() == 1 {
            return prefix_matches.into_iter().next();
        }
        None
    }

    pub fn resolve_agent_ident(&self, ident: &str) -> Option<String> {
        let ident = ident.trim();
        if ident.is_empty() {
            return None;
        }
        if let Some(agent) = self.agents.get(ident) {
            if agent.cell_type == "agent" {
                return Some(agent.id.clone());
            }
        }
        let ident_lower = ident.to_lowercase();
        for agent in self.agents.values() {
            if agent.cell_type != "agent" {
                continue;
            }
            if agent.slug.to_lowercase() == ident_lower || agent.name.to_lowercase() == ident_lower
            {
                return Some(agent.id.clone());
            }
        }
        let mut prefix_matches = self
            .agents
            .values()
            .filter(|agent| agent.cell_type == "agent" && agent.id.starts_with(ident))
            .map(|agent| agent.id.clone())
            .collect::<Vec<_>>();
        prefix_matches.sort();
        if prefix_matches.len() == 1 {
            return prefix_matches.into_iter().next();
        }
        None
    }

    pub fn board_deps_met(&self, task: &BoardTask) -> bool {
        task.depends_on
            .iter()
            .all(|dep_id| board_task_counts_as_done(self.board_tasks.get(dep_id)))
    }

    /// Tasks that declare `task_id` in their `depends_on` list.
    pub fn board_get_dependents(&self, task_id: &str) -> Vec<&BoardTask> {
        self.board_tasks
            .values()
            .filter(|t| t.depends_on.iter().any(|d| d == task_id))
            .collect()
    }

    /// Direct children of `task_id` — tasks whose `parent_task_id` equals it.
    pub fn board_get_children(&self, task_id: &str) -> Vec<&BoardTask> {
        self.board_tasks
            .values()
            .filter(|t| t.parent_task_id == task_id)
            .collect()
    }

    /// Walk the subtree rooted at `task_id` and return every descendant that
    /// is not yet Done/Archived-as-Done. Mirrors Python
    /// `MatrixState.task_open_descendants`.
    pub fn task_open_descendants(&self, task_id: &str) -> Vec<&BoardTask> {
        if task_id.is_empty() {
            return Vec::new();
        }
        let mut out = Vec::new();
        let mut stack: Vec<&BoardTask> = self.board_get_children(task_id);
        while let Some(current) = stack.pop() {
            for child in self.board_get_children(&current.id) {
                stack.push(child);
            }
            if board_task_counts_as_done(Some(current)) {
                continue;
            }
            out.push(current);
        }
        out.sort_by(|a, b| {
            a.pipeline_depth
                .cmp(&b.pipeline_depth)
                .then_with(|| a.created_at.cmp(&b.created_at))
                .then_with(|| a.id.cmp(&b.id))
        });
        out
    }

    /// True if any descendant of `task_id` is still unresolved.
    pub fn task_has_unresolved_descendants(&self, task_id: &str) -> bool {
        !self.task_open_descendants(task_id).is_empty()
    }

    pub fn task_occupies_execution_slot(&self, task: Option<&BoardTask>, agent_id: &str) -> bool {
        let Some(task) = task else { return false };
        if board_task_is_closed(Some(task)) {
            return false;
        }
        if !agent_id.is_empty() && task.agent_id != agent_id {
            return false;
        }
        !matches!(task.lane.as_str(), "Backlog" | "Done" | ARCHIVED_LANE)
    }

    pub fn agent_active_tasks(&self, agent_id: &str) -> Vec<&BoardTask> {
        let mut tasks = self
            .board_tasks
            .values()
            .filter(|task| self.task_occupies_execution_slot(Some(task), agent_id))
            .collect::<Vec<_>>();
        tasks.sort_by(|a, b| {
            (a.lane != "In Progress")
                .cmp(&(b.lane != "In Progress"))
                .then_with(|| a.position.cmp(&b.position))
                .then_with(|| a.created_at.cmp(&b.created_at))
                .then_with(|| a.id.cmp(&b.id))
        });
        tasks
    }

    pub fn agent_current_task(&self, agent_id: &str) -> Option<&BoardTask> {
        if let Some(agent) = self.agents.get(agent_id) {
            if !agent.current_task_id.is_empty() {
                let task = self.board_tasks.get(&agent.current_task_id);
                if self.task_occupies_execution_slot(task, agent_id) {
                    return task;
                }
            }
        }
        self.agent_active_tasks(agent_id).into_iter().next()
    }

    pub fn agent_pending_weaver_reply_tasks(&self, agent_id: &str) -> Vec<&BoardTask> {
        let mut tasks = self
            .board_tasks
            .values()
            .filter(|task| task.reply_agent_id == agent_id && !board_task_is_closed(Some(task)))
            .collect::<Vec<_>>();
        tasks.sort_by(|a, b| {
            a.created_at
                .cmp(&b.created_at)
                .then_with(|| a.id.cmp(&b.id))
        });
        tasks
    }

    pub fn agent_is_busy(&self, agent_id: &str) -> bool {
        self.agent_current_task(agent_id).is_some()
    }

    // ---- Slug helpers -----------------------------------------------------

    /// All existing agent/terminal slugs, for uniqueness checks.
    pub fn all_agent_slugs(&self) -> HashSet<String> {
        self.agents
            .values()
            .filter_map(|a| {
                if a.slug.is_empty() {
                    None
                } else {
                    Some(a.slug.clone())
                }
            })
            .collect()
    }

    pub fn all_task_slugs(&self) -> HashSet<String> {
        self.board_tasks
            .values()
            .filter_map(|t| {
                if t.slug.is_empty() {
                    None
                } else {
                    Some(t.slug.clone())
                }
            })
            .collect()
    }

    pub fn all_group_slugs(&self) -> HashSet<String> {
        self.group_slugs.values().cloned().collect()
    }

    /// Generate a unique slug for an agent/terminal derived from its name, taking
    /// into account the parent chain (child terminals are prefixed with their
    /// parent's slug).
    pub fn make_agent_slug(&self, name: &str, parent_id: Option<&str>, group_name: &str) -> String {
        let base = slugify(name);
        let existing = self.all_agent_slugs();
        match parent_id.and_then(|pid| self.agents.get(pid)) {
            Some(parent) if !parent.slug.is_empty() => {
                unique_slug(&format!("{}:{}", parent.slug, base), &existing)
            }
            _ => {
                // standalone terminal: prefix with group slug
                let group_slug = self
                    .group_slugs
                    .get(group_name)
                    .cloned()
                    .unwrap_or_else(|| slugify(group_name));
                unique_slug(&format!("{}:{}", group_slug, base), &existing)
            }
        }
    }

    // ---- Group mutations --------------------------------------------------

    pub fn add_group(&mut self, name: &str) -> crate::Result<()> {
        if name.is_empty() {
            return Err(crate::Error::validation("group name empty"));
        }
        if self.groups.contains_key(name) {
            return Err(crate::Error::Conflict(format!(
                "group '{name}' already exists"
            )));
        }
        if let Some(conflict) = self.group_prefix_conflict(name, None) {
            return Err(crate::Error::Conflict(format!(
                "group '{name}' would share a task-ID prefix with existing group '{conflict}'"
            )));
        }
        let slug = unique_slug(&slugify(name), &self.all_group_slugs());
        self.groups.insert(name.to_string(), Vec::new());
        self.groups_order.push(name.to_string());
        self.group_slugs.insert(name.to_string(), slug);
        self.group_settings
            .insert(name.to_string(), GroupSettings::default());
        self.emit_group(name);
        Ok(())
    }

    /// Return the name of an existing group whose normalized task-ID prefix
    /// collides with `candidate`, or `None` if no collision. Mirrors
    /// `loom/state.py::group_prefix_conflict`.
    pub fn group_prefix_conflict(
        &self,
        candidate: &str,
        exclude_name: Option<&str>,
    ) -> Option<String> {
        let wanted = crate::task_ids::normalize_group_prefix(candidate);
        for existing in self.groups.keys() {
            if exclude_name == Some(existing.as_str()) {
                continue;
            }
            if crate::task_ids::normalize_group_prefix(existing) == wanted {
                return Some(existing.clone());
            }
        }
        None
    }

    pub fn remove_group(&mut self, name: &str) -> crate::Result<Vec<String>> {
        let Some(members) = self.groups.remove(name) else {
            return Err(crate::Error::not_found(format!("group '{name}'")));
        };
        self.groups_order.retain(|g| g != name);
        self.group_slugs.remove(name);
        self.group_settings.remove(name);

        // cascade delete all members + their children
        let mut removed = Vec::new();
        for aid in &members {
            removed.extend(self.cascade_remove_agent(aid));
        }

        self.emit(DeltaOp::GroupRemove {
            name: name.to_string(),
        });
        for aid in &removed {
            self.emit(DeltaOp::AgentRemove { id: aid.clone() });
        }
        self.clear_selection_if_removed(&removed);
        Ok(removed)
    }

    pub fn rename_group(&mut self, old: &str, new: &str) -> crate::Result<()> {
        if old == new {
            return Ok(());
        }
        if self.groups.contains_key(new) {
            return Err(crate::Error::Conflict(format!(
                "group '{new}' already exists"
            )));
        }
        if let Some(conflict) = self.group_prefix_conflict(new, Some(old)) {
            return Err(crate::Error::Conflict(format!(
                "renaming to '{new}' would share a task-ID prefix with existing group '{conflict}'"
            )));
        }
        let Some(members) = self.groups.remove(old) else {
            return Err(crate::Error::not_found(format!("group '{old}'")));
        };
        self.groups.insert(new.to_string(), members.clone());
        for g in &mut self.groups_order {
            if g == old {
                *g = new.to_string();
            }
        }
        if let Some(slug) = self.group_slugs.remove(old) {
            self.group_slugs.insert(new.to_string(), slug);
        }
        if let Some(gs) = self.group_settings.remove(old) {
            self.group_settings.insert(new.to_string(), gs);
        }
        // update each member's group field
        for aid in &members {
            if let Some(agent) = self.agents.get_mut(aid) {
                agent.group = new.to_string();
            }
        }
        self.emit(DeltaOp::GroupRename {
            old_name: old.into(),
            new_name: new.into(),
        });
        // reindex tasks
        if let Some(ids) = self.tasks_by_group.remove(old) {
            for tid in &ids {
                if let Some(t) = self.board_tasks.get_mut(tid) {
                    t.group = new.to_string();
                }
            }
            self.tasks_by_group.insert(new.to_string(), ids);
        }
        Ok(())
    }

    pub fn reorder_groups(&mut self, order: Vec<String>) -> crate::Result<()> {
        let existing: HashSet<&str> = self.groups_order.iter().map(|s| s.as_str()).collect();
        for name in &order {
            if !existing.contains(name.as_str()) {
                return Err(crate::Error::not_found(format!("group '{name}'")));
            }
        }
        // any groups not listed are appended in their current relative order
        let requested: HashSet<&str> = order.iter().map(|s| s.as_str()).collect();
        let mut final_order = order.clone();
        for g in &self.groups_order {
            if !requested.contains(g.as_str()) {
                final_order.push(g.clone());
            }
        }
        self.groups_order = final_order.clone();
        self.emit(DeltaOp::GroupsReorder {
            groups: final_order,
        });
        Ok(())
    }

    // ---- Agent mutations --------------------------------------------------

    pub fn add_agent(&mut self, mut cell: AgentCell) -> crate::Result<()> {
        if !self.groups.contains_key(&cell.group) {
            return Err(crate::Error::not_found(format!("group '{}'", cell.group)));
        }
        if self.agents.contains_key(&cell.id) {
            return Err(crate::Error::Conflict(format!(
                "agent id '{}' already exists",
                cell.id
            )));
        }
        if cell.slug.is_empty() {
            let pid = if cell.parent_id.is_empty() {
                None
            } else {
                Some(cell.parent_id.as_str())
            };
            cell.slug = self.make_agent_slug(&cell.name, pid, &cell.group);
        }
        let group = cell.group.clone();
        let parent = if cell.parent_id.is_empty() {
            None
        } else {
            Some(cell.parent_id.clone())
        };

        self.groups.get_mut(&group).unwrap().push(cell.id.clone());
        if let Some(pid) = &parent {
            self.children
                .entry(pid.clone())
                .or_default()
                .push(cell.id.clone());
        }
        let id = cell.id.clone();
        self.agents.insert(id.clone(), cell);
        self.emit_agent(&id);
        self.emit_group(&group);
        Ok(())
    }

    /// Remove a single agent and its children, return the removed IDs.
    pub fn cascade_remove_agent(&mut self, id: &str) -> Vec<String> {
        let mut removed = Vec::new();
        let Some(agent) = self.agents.remove(id) else {
            return removed;
        };
        removed.push(id.to_string());

        // detach from group
        if let Some(list) = self.groups.get_mut(&agent.group) {
            list.retain(|x| x != id);
        }
        // detach from parent
        if !agent.parent_id.is_empty() {
            if let Some(list) = self.children.get_mut(&agent.parent_id) {
                list.retain(|x| x != id);
            }
        }
        // cascade
        if let Some(children) = self.children.remove(id) {
            for cid in children {
                removed.extend(self.cascade_remove_agent(&cid));
            }
        }
        removed
    }

    pub fn remove_agent(&mut self, id: &str) -> crate::Result<Vec<String>> {
        let Some(agent) = self.agents.get(id) else {
            return Err(crate::Error::not_found(format!("agent '{id}'")));
        };
        let group = agent.group.clone();
        let clears_weaver = self
            .group_settings
            .get(&group)
            .map(|settings| settings.weaver_agent_id == id)
            .unwrap_or(false);
        let removed = self.cascade_remove_agent(id);
        if removed.is_empty() {
            return Err(crate::Error::not_found(format!("agent '{id}'")));
        }
        for rid in &removed {
            self.emit(DeltaOp::AgentRemove { id: rid.clone() });
        }
        if clears_weaver {
            let settings = self.group_settings.entry(group.clone()).or_default();
            settings.weaver_agent_id.clear();
            let fields = serde_json::to_value(&*settings)
                .unwrap_or(serde_json::Value::Null)
                .as_object()
                .cloned()
                .unwrap_or_default();
            self.emit(DeltaOp::GroupSettingsUpdate {
                name: group.clone(),
                fields,
            });
        }
        self.emit_group(&group);
        self.clear_selection_if_removed(&removed);
        Ok(removed)
    }

    /// Replace the content-area layout tree. Pass `None` to clear and fall
    /// back to the default single-Terminal layout.
    pub fn set_layout(&mut self, layout: Option<LayoutNode>) {
        self.content_layout = layout.clone();
        let payload = match &layout {
            Some(l) => serde_json::to_value(l).unwrap_or(serde_json::Value::Null),
            None => serde_json::Value::Null,
        };
        self.emit(DeltaOp::UiUpdate {
            key: "content_layout".into(),
            value: payload,
        });
    }

    /// Resolve the effective layout: returns the customized layout if set,
    /// otherwise a default Leaf(Terminal { id: None }) which the UI binds
    /// to `selected_agent_id`.
    pub fn effective_layout(&self) -> LayoutNode {
        self.content_layout.clone().unwrap_or_default()
    }

    /// Full dock layout — edges + center, with defaults filled in. Used by
    /// the UI for rendering the outer window shell.
    pub fn effective_dock_layout(&self) -> DockLayout {
        DockLayout {
            top: self.dock_edges.top.clone(),
            left: self.dock_edges.left.clone(),
            right: self.dock_edges.right.clone(),
            bottom: self.dock_edges.bottom.clone(),
            center: self.effective_layout(),
            ratios: self.dock_edges.ratios,
        }
    }

    /// Replace a single edge zone. Pass `layout: None` to hide that edge.
    /// Passing `DockZone::Center` routes to `set_layout` (the center zone's
    /// source of truth).
    pub fn set_dock_edge(&mut self, zone: DockZone, layout: Option<LayoutNode>) {
        if matches!(zone, DockZone::Center) {
            // Center lives in content_layout — route through set_layout so
            // snapshot + CLI paths keep working.
            self.set_layout(layout);
            return;
        }
        if !self.dock_edges.set_edge(zone, layout) {
            return;
        }
        let payload = serde_json::to_value(&self.dock_edges).unwrap_or(serde_json::Value::Null);
        self.emit(DeltaOp::UiUpdate {
            key: "dock_edges".into(),
            value: payload,
        });
    }

    /// Update edge-zone size ratios (called e.g. from a splitter drag).
    pub fn set_dock_ratios(&mut self, ratios: DockRatios) {
        let clamped = ratios.clamp();
        if clamped == self.dock_edges.ratios {
            return;
        }
        self.dock_edges.ratios = clamped;
        let payload = serde_json::to_value(&self.dock_edges).unwrap_or(serde_json::Value::Null);
        self.emit(DeltaOp::UiUpdate {
            key: "dock_edges".into(),
            value: payload,
        });
    }

    /// Set (or clear) the UI's selected agent. `None` deselects.
    pub fn select_agent(&mut self, id: Option<&str>) -> crate::Result<()> {
        if let Some(aid) = id {
            if !self.agents.contains_key(aid) {
                return Err(crate::Error::not_found(format!("agent '{aid}'")));
            }
        }
        let new = id.map(|s| s.to_string());
        if self.selected_agent_id == new {
            return Ok(());
        }
        self.selected_agent_id = new.clone();
        self.emit(DeltaOp::UiUpdate {
            key: "selected_agent_id".into(),
            value: serde_json::to_value(new).unwrap_or(serde_json::Value::Null),
        });
        Ok(())
    }

    /// Clear selection if the selected agent is in the removed set.
    fn clear_selection_if_removed(&mut self, removed: &[String]) {
        let Some(selected) = self.selected_agent_id.as_ref() else {
            return;
        };
        if removed.iter().any(|r| r == selected) {
            self.selected_agent_id = None;
            self.emit(DeltaOp::UiUpdate {
                key: "selected_agent_id".into(),
                value: serde_json::Value::Null,
            });
        }
    }

    /// Rename an agent. Regenerates slug and cascades to children (child slugs
    /// are prefixed with the parent's slug).
    pub fn rename_agent(&mut self, id: &str, new_name: &str) -> crate::Result<()> {
        let Some(agent) = self.agents.get(id) else {
            return Err(crate::Error::not_found(format!("agent '{id}'")));
        };
        let parent_id = if agent.parent_id.is_empty() {
            None
        } else {
            Some(agent.parent_id.clone())
        };
        let group = agent.group.clone();
        let new_slug = self.make_agent_slug(new_name, parent_id.as_deref(), &group);

        let agent = self.agents.get_mut(id).unwrap();
        agent.name = new_name.to_string();
        let old_slug = std::mem::take(&mut agent.slug);
        agent.slug = new_slug.clone();

        // cascade to children — their slug format is `{parent.slug}:{tail}`.
        let child_ids = self.children.get(id).cloned().unwrap_or_default();
        for cid in child_ids {
            if let Some(child) = self.agents.get_mut(&cid) {
                let tail = child
                    .slug
                    .rsplit_once(':')
                    .map(|(_, tail)| tail.to_string())
                    .unwrap_or_else(|| child.slug.clone());
                let new_child_slug = format!("{}:{}", new_slug, tail);
                // no collision check for children — parent slug already unique
                child.slug = new_child_slug;
                let child_id = child.id.clone();
                self.emit_agent(&child_id);
            }
        }
        drop(old_slug);
        self.emit_agent(id);
        Ok(())
    }

    // ---- Board tasks ------------------------------------------------------

    pub fn index_task(&mut self, task: &BoardTask) {
        self.tasks_by_group
            .entry(task.group.clone())
            .or_default()
            .insert(task.id.clone());
    }

    pub fn unindex_task(&mut self, task: &BoardTask) {
        if let Some(set) = self.tasks_by_group.get_mut(&task.group) {
            set.remove(&task.id);
        }
    }

    pub fn upsert_task(&mut self, mut task: BoardTask) -> crate::Result<()> {
        // if replacing an existing task with a different group, reindex
        if let Some(existing) = self.board_tasks.get(&task.id) {
            if existing.group != task.group {
                let old_group = existing.group.clone();
                if let Some(set) = self.tasks_by_group.get_mut(&old_group) {
                    set.remove(&task.id);
                }
            }
        }
        if task.slug.is_empty() {
            task.slug = unique_slug(&slugify(&task.task), &self.all_task_slugs());
        }
        self.index_task(&task);
        let id = task.id.clone();
        self.board_tasks.insert(id.clone(), task);
        self.emit_task(&id);
        Ok(())
    }

    pub fn remove_task(&mut self, id: &str) -> crate::Result<()> {
        let Some(task) = self.board_tasks.remove(id) else {
            return Err(crate::Error::not_found(format!("task '{id}'")));
        };
        self.unindex_task(&task);
        self.emit(DeltaOp::TaskRemove { id: id.to_string() });
        Ok(())
    }

    /// Walk up the parent chain of a task and return all ancestors (closest first).
    pub fn task_chain(&self, id: &str) -> Vec<&BoardTask> {
        let mut chain = Vec::new();
        let mut cur = self.board_tasks.get(id);
        while let Some(task) = cur {
            chain.push(task);
            if task.parent_task_id.is_empty() {
                break;
            }
            cur = self.board_tasks.get(&task.parent_task_id);
        }
        chain
    }

    // ---- Lane management --------------------------------------------------

    pub fn set_lanes(&mut self, lanes: Vec<String>) -> crate::Result<()> {
        // reserved lanes are always present and cannot be removed / renamed.
        let reserved: HashSet<&str> = RESERVED_LANES.iter().copied().collect();
        let mut final_lanes: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for lane in lanes {
            if lane.is_empty() || seen.contains(&lane) {
                continue;
            }
            seen.insert(lane.clone());
            final_lanes.push(lane);
        }
        for lane in RESERVED_LANES {
            if !seen.contains(*lane) {
                final_lanes.push(lane.to_string());
                seen.insert(lane.to_string());
            }
        }
        // reject rename of reserved lanes implicitly: reserved names must appear verbatim.
        for lane in RESERVED_LANES {
            if !final_lanes.iter().any(|l| l == *lane) && reserved.contains(lane) {
                return Err(crate::Error::validation(format!(
                    "reserved lane '{lane}' must be present"
                )));
            }
        }
        self.board_lanes = final_lanes.clone();
        self.emit(DeltaOp::LanesUpdate { lanes: final_lanes });
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh_state_with_group(name: &str) -> MatrixState {
        let mut s = MatrixState::new();
        s.add_group(name).unwrap();
        s
    }

    #[test]
    fn default_state_has_reserved_lanes() {
        let s = MatrixState::new();
        assert!(s.board_lanes.contains(&"Backlog".into()));
        assert!(s.board_lanes.contains(&"Archived".into()));
    }

    #[test]
    fn add_group_emits_delta_and_generates_slug() {
        let mut s = MatrixState::new();
        s.add_group("Engineering").unwrap();
        assert_eq!(
            s.group_slugs.get("Engineering").map(|s| s.as_str()),
            Some("engineering")
        );
        let (seq, ops) = s.drain_deltas().unwrap();
        assert_eq!(seq, 1);
        assert_eq!(ops.len(), 1);
    }

    #[test]
    fn duplicate_group_rejected() {
        let mut s = fresh_state_with_group("Eng");
        let err = s.add_group("Eng").unwrap_err();
        matches!(err, crate::Error::Conflict(_));
    }

    #[test]
    fn add_agent_requires_group() {
        let mut s = MatrixState::new();
        let err = s
            .add_agent(AgentCell::new("a1", "Agent One", "missing"))
            .unwrap_err();
        matches!(err, crate::Error::NotFound(_));
    }

    #[test]
    fn add_agent_generates_slug_prefixed_with_group() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();
        let slug = &s.agents["a1"].slug;
        assert!(slug.starts_with("eng:"), "slug was {slug:?}");
    }

    #[test]
    fn child_terminal_inherits_parent_slug_prefix() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("p1", "Parent", "Eng")).unwrap();
        let parent_slug = s.agents["p1"].slug.clone();
        let mut child = AgentCell::new("c1", "Logs", "Eng");
        child.parent_id = "p1".into();
        s.add_agent(child).unwrap();
        let child_slug = &s.agents["c1"].slug;
        assert!(
            child_slug.starts_with(&format!("{parent_slug}:")),
            "child slug {child_slug:?} missing parent prefix"
        );
    }

    #[test]
    fn rename_agent_cascades_slug_to_children() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("p1", "Parent", "Eng")).unwrap();
        let mut child = AgentCell::new("c1", "Logs", "Eng");
        child.parent_id = "p1".into();
        s.add_agent(child).unwrap();

        s.rename_agent("p1", "Renamed").unwrap();
        let parent_slug = &s.agents["p1"].slug;
        let child_slug = &s.agents["c1"].slug;
        assert!(child_slug.starts_with(&format!("{parent_slug}:")));
    }

    #[test]
    fn remove_group_cascades_to_members_and_children() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("p1", "Parent", "Eng")).unwrap();
        let mut child = AgentCell::new("c1", "Logs", "Eng");
        child.parent_id = "p1".into();
        s.add_agent(child).unwrap();

        let removed = s.remove_group("Eng").unwrap();
        assert!(removed.contains(&"p1".into()));
        assert!(removed.contains(&"c1".into()));
        assert!(!s.agents.contains_key("p1"));
        assert!(!s.agents.contains_key("c1"));
    }

    #[test]
    fn reserved_lanes_cannot_be_removed() {
        let mut s = MatrixState::new();
        s.set_lanes(vec!["Backlog".into(), "Custom".into()])
            .unwrap();
        assert!(s.board_lanes.contains(&"In Progress".into()));
        assert!(s.board_lanes.contains(&"Done".into()));
        assert!(s.board_lanes.contains(&"Archived".into()));
        assert!(s.board_lanes.contains(&"Custom".into()));
    }

    #[test]
    fn task_upsert_assigns_slug_and_indexes_by_group() {
        let mut s = fresh_state_with_group("Eng");
        let mut t = BoardTask::new_minimal("eng-1", "Fix bug");
        t.group = "Eng".into();
        s.upsert_task(t).unwrap();
        assert_eq!(s.board_tasks["eng-1"].slug, "fix-bug");
        assert!(s.tasks_by_group.get("Eng").unwrap().contains("eng-1"));
    }

    #[test]
    fn task_remove_unindexes() {
        let mut s = fresh_state_with_group("Eng");
        let mut t = BoardTask::new_minimal("eng-1", "Fix bug");
        t.group = "Eng".into();
        s.upsert_task(t).unwrap();
        s.remove_task("eng-1").unwrap();
        assert!(!s.tasks_by_group.get("Eng").unwrap().contains("eng-1"));
    }

    #[test]
    fn drain_deltas_bumps_seq_only_when_ops_present() {
        let mut s = MatrixState::new();
        assert!(s.drain_deltas().is_none());
        s.add_group("Eng").unwrap();
        let (seq, _) = s.drain_deltas().unwrap();
        assert_eq!(seq, 1);
        assert!(s.drain_deltas().is_none());
        s.add_group("Ops").unwrap();
        let (seq2, _) = s.drain_deltas().unwrap();
        assert_eq!(seq2, 2);
    }

    #[test]
    fn select_agent_sets_field_and_emits_ui_update() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();
        s.drain_deltas();

        s.select_agent(Some("a1")).unwrap();
        assert_eq!(s.selected_agent_id.as_deref(), Some("a1"));
        let (_, ops) = s.drain_deltas().unwrap();
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["op"], "ui_update");
        assert_eq!(v["key"], "selected_agent_id");
        assert_eq!(v["value"], "a1");
    }

    #[test]
    fn select_agent_unknown_id_rejected() {
        let mut s = MatrixState::new();
        assert!(s.select_agent(Some("nope")).is_err());
    }

    #[test]
    fn select_agent_none_clears() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();
        s.select_agent(Some("a1")).unwrap();
        s.drain_deltas();

        s.select_agent(None).unwrap();
        assert!(s.selected_agent_id.is_none());
        let (_, ops) = s.drain_deltas().unwrap();
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["op"], "ui_update");
        assert_eq!(v["key"], "selected_agent_id");
        assert!(v["value"].is_null());
    }

    #[test]
    fn select_agent_idempotent_when_unchanged() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();
        s.select_agent(Some("a1")).unwrap();
        s.drain_deltas();
        s.select_agent(Some("a1")).unwrap();
        assert!(
            s.drain_deltas().is_none(),
            "no delta for identical reselect"
        );
    }

    #[test]
    fn removing_selected_agent_clears_selection() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();
        s.select_agent(Some("a1")).unwrap();
        s.drain_deltas();

        s.remove_agent("a1").unwrap();
        assert!(s.selected_agent_id.is_none());
        let (_, ops) = s.drain_deltas().unwrap();
        let has_ui_clear = ops.iter().any(|op| {
            let v = serde_json::to_value(op).unwrap();
            v["op"] == "ui_update" && v["key"] == "selected_agent_id" && v["value"].is_null()
        });
        assert!(
            has_ui_clear,
            "expected ui_update clearing selection, got: {:?}",
            ops
        );
    }

    #[test]
    fn removing_group_cascades_selection_clear() {
        let mut s = fresh_state_with_group("Eng");
        s.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();
        let mut child = AgentCell::new("c1", "Logs", "Eng");
        child.parent_id = "a1".into();
        s.add_agent(child).unwrap();
        s.select_agent(Some("c1")).unwrap();
        s.drain_deltas();

        s.remove_group("Eng").unwrap();
        assert!(s.selected_agent_id.is_none());
    }

    #[test]
    fn layout_default_is_single_terminal_leaf() {
        let l = LayoutNode::default();
        assert!(matches!(
            l,
            LayoutNode::Leaf {
                panel: PanelKind::Terminal { id: None }
            }
        ));
        assert_eq!(l.leaves().len(), 1);
    }

    #[test]
    fn layout_split_serializes_with_axis_and_ratio() {
        let split = LayoutNode::Split {
            axis: SplitAxis::Horizontal,
            ratio: 0.5,
            first: Box::new(LayoutNode::Leaf {
                panel: PanelKind::Board,
            }),
            second: Box::new(LayoutNode::Leaf {
                panel: PanelKind::Terminal {
                    id: Some("a1".into()),
                },
            }),
        };
        let v = serde_json::to_value(&split).unwrap();
        assert_eq!(v["type"], "split");
        assert_eq!(v["axis"], "horizontal");
        assert_eq!(v["first"]["type"], "leaf");
        assert_eq!(v["first"]["panel"]["kind"], "board");
        assert_eq!(v["second"]["panel"]["kind"], "terminal");
        assert_eq!(v["second"]["panel"]["id"], "a1");
    }

    #[test]
    fn layout_leaves_walk_in_order() {
        let l = LayoutNode::Split {
            axis: SplitAxis::Vertical,
            ratio: 0.6,
            first: Box::new(LayoutNode::Leaf {
                panel: PanelKind::Board,
            }),
            second: Box::new(LayoutNode::Split {
                axis: SplitAxis::Horizontal,
                ratio: 0.5,
                first: Box::new(LayoutNode::Leaf {
                    panel: PanelKind::Memory,
                }),
                second: Box::new(LayoutNode::Leaf {
                    panel: PanelKind::Terminal {
                        id: Some("a1".into()),
                    },
                }),
            }),
        };
        let leaves = l.leaves();
        assert_eq!(leaves.len(), 3);
        assert!(matches!(leaves[0], PanelKind::Board));
        assert!(matches!(leaves[1], PanelKind::Memory));
        assert!(matches!(
            leaves[2],
            PanelKind::Terminal { id: Some(s) } if s == "a1"
        ));
    }

    #[test]
    fn set_layout_emits_ui_update_delta() {
        let mut s = MatrixState::new();
        s.set_layout(Some(LayoutNode::Leaf {
            panel: PanelKind::Board,
        }));
        let (_, ops) = s.drain_deltas().unwrap();
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["op"], "ui_update");
        assert_eq!(v["key"], "content_layout");
        assert_eq!(v["value"]["type"], "leaf");
        assert_eq!(v["value"]["panel"]["kind"], "board");
    }

    #[test]
    fn set_layout_none_emits_null() {
        let mut s = MatrixState::new();
        s.set_layout(Some(LayoutNode::Leaf {
            panel: PanelKind::Memory,
        }));
        s.drain_deltas();

        s.set_layout(None);
        let (_, ops) = s.drain_deltas().unwrap();
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["op"], "ui_update");
        assert_eq!(v["key"], "content_layout");
        assert!(v["value"].is_null());
    }

    #[test]
    fn task_chain_walks_parents() {
        let mut s = fresh_state_with_group("Eng");
        let mut root = BoardTask::new_minimal("eng-1", "Root");
        root.group = "Eng".into();
        s.upsert_task(root).unwrap();
        let mut mid = BoardTask::new_minimal("eng-1a", "Mid");
        mid.group = "Eng".into();
        mid.parent_task_id = "eng-1".into();
        s.upsert_task(mid).unwrap();
        let mut leaf = BoardTask::new_minimal("eng-1a1", "Leaf");
        leaf.group = "Eng".into();
        leaf.parent_task_id = "eng-1a".into();
        s.upsert_task(leaf).unwrap();

        let chain = s.task_chain("eng-1a1");
        assert_eq!(chain.len(), 3);
        assert_eq!(chain[0].id, "eng-1a1");
        assert_eq!(chain[2].id, "eng-1");
    }

    // ---------- DockEdges / DockLayout ----------

    #[test]
    fn dock_edges_default_has_sidebar_left_board_bottom() {
        let edges = DockEdges::default();
        assert!(edges.top.is_none());
        assert!(edges.right.is_none());
        let left = edges.left.as_ref().expect("left default = sidebar");
        assert!(matches!(
            left,
            LayoutNode::Leaf {
                panel: PanelKind::Sidebar
            }
        ));
        let bottom = edges.bottom.as_ref().expect("bottom default = board");
        assert!(matches!(
            bottom,
            LayoutNode::Leaf {
                panel: PanelKind::Board
            }
        ));
    }

    #[test]
    fn effective_dock_layout_combines_center_and_edges() {
        let s = MatrixState::new();
        let dock = s.effective_dock_layout();
        // Center defaults to a Terminal(None) leaf when content_layout is None.
        match &dock.center {
            LayoutNode::Leaf {
                panel: PanelKind::Terminal { id },
            } => assert!(id.is_none()),
            other => panic!("expected Terminal leaf, got {other:?}"),
        }
        // Edges default: sidebar left, board bottom.
        assert!(dock.left.is_some());
        assert!(dock.bottom.is_some());
    }

    #[test]
    fn set_dock_edge_emits_delta_and_mutates_slot() {
        let mut s = MatrixState::new();
        s.drain_deltas();
        s.set_dock_edge(
            DockZone::Right,
            Some(LayoutNode::Leaf {
                panel: PanelKind::Memory,
            }),
        );
        assert!(s.dock_edges.right.is_some());
        let (_, ops) = s.drain_deltas().expect("delta emitted");
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["op"], "ui_update");
        assert_eq!(v["key"], "dock_edges");
        assert_eq!(v["value"]["right"]["panel"]["kind"], "memory");
    }

    #[test]
    fn set_dock_edge_idempotent_on_unchanged_value() {
        let mut s = MatrixState::new();
        // Left default is Some(Sidebar). Setting it to the same value is a
        // no-op (no delta emitted).
        s.drain_deltas();
        s.set_dock_edge(
            DockZone::Left,
            Some(LayoutNode::Leaf {
                panel: PanelKind::Sidebar,
            }),
        );
        assert!(s.drain_deltas().is_none());
    }

    #[test]
    fn set_dock_edge_center_routes_to_set_layout() {
        let mut s = MatrixState::new();
        s.drain_deltas();
        let new_layout = LayoutNode::Leaf {
            panel: PanelKind::Actions,
        };
        s.set_dock_edge(DockZone::Center, Some(new_layout.clone()));
        assert!(matches!(
            s.content_layout.as_ref().unwrap(),
            LayoutNode::Leaf {
                panel: PanelKind::Actions
            }
        ));
        // Delta carries `content_layout`, not `dock_edges`.
        let (_, ops) = s.drain_deltas().expect("delta emitted");
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["key"], "content_layout");
        assert!(v["value"].is_object());
    }

    #[test]
    fn set_dock_ratios_clamps_and_emits_delta() {
        let mut s = MatrixState::new();
        s.drain_deltas();
        // Exceed the 0.7 clamp — values must be squashed.
        s.set_dock_ratios(DockRatios {
            top: 0.95,
            left: 0.10,
            right: 0.40,
            bottom: 0.60,
        });
        assert!((s.dock_edges.ratios.top - 0.7).abs() < 1e-9);
        assert!((s.dock_edges.ratios.left - 0.10).abs() < 1e-9);
        let (_, ops) = s.drain_deltas().expect("delta emitted");
        let v = serde_json::to_value(&ops[0]).unwrap();
        assert_eq!(v["op"], "ui_update");
        assert_eq!(v["key"], "dock_edges");
        assert_eq!(v["value"]["ratios"]["top"], 0.7);
    }
}
