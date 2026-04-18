//! SQLite persistence layer.
//!
//! Mirrors `loom/db.py` + `loom/db_schema.py`. WAL-mode sqlite, targeted write
//! methods, load-all on startup. Internally the connection is held in a
//! `Mutex` so the state layer can be mutated from the async runtime and
//! persisted via `tokio::task::spawn_blocking`.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use rusqlite::types::Value as SqlValue;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde::de::DeserializeOwned;
use tokio::sync::Mutex;

use crate::state::{
    AgentCell, BoardTask, GlobalSettings, GroupSettings, MatrixState, MemoryEntry, MemoryLink,
    Schedule, WeaverSettings,
};

const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL,
    cell_type TEXT NOT NULL DEFAULT 'agent',
    terminal_backend TEXT NOT NULL DEFAULT 'iterm2',
    session_id TEXT,
    profile TEXT NOT NULL DEFAULT 'Default',
    command TEXT NOT NULL DEFAULT '',
    directory TEXT NOT NULL DEFAULT '',
    tab_color TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    template TEXT NOT NULL DEFAULT '',
    window_id TEXT NOT NULL DEFAULT '',
    parent_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'stopped',
    worktree_path TEXT NOT NULL DEFAULT '',
    worktree_branch TEXT NOT NULL DEFAULT '',
    worktree_repo_root TEXT NOT NULL DEFAULT '',
    worktree_base_dir TEXT NOT NULL DEFAULT '.loom/worktrees',
    worktree_base_branch TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint INTEGER NOT NULL DEFAULT 0,
    checkpoint_on_progress INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash INTEGER NOT NULL DEFAULT 1,
    agent_type TEXT NOT NULL DEFAULT '',
    agent_session_id TEXT NOT NULL DEFAULT '',
    session_resume INTEGER NOT NULL DEFAULT 1,
    idle_timeout INTEGER NOT NULL DEFAULT 0,
    tasks_dispatched INTEGER NOT NULL DEFAULT 0,
    created_by_weaver_id TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS groups (
    name TEXT PRIMARY KEY,
    slug TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    ordinal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS group_members (
    group_name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_name, agent_id)
);

CREATE TABLE IF NOT EXISTS group_settings (
    group_name TEXT PRIMARY KEY,
    default_directory TEXT NOT NULL DEFAULT '',
    default_terminal_backend TEXT NOT NULL DEFAULT 'iterm2',
    profile TEXT NOT NULL DEFAULT '',
    shell TEXT NOT NULL DEFAULT '',
    tab_color TEXT NOT NULL DEFAULT '',
    env_vars TEXT NOT NULL DEFAULT '{}',
    env_file TEXT NOT NULL DEFAULT '',
    auto_terminals INTEGER NOT NULL DEFAULT 0,
    max_agents INTEGER NOT NULL DEFAULT 0,
    collapsed_default INTEGER NOT NULL DEFAULT 0,
    filter_by_window INTEGER NOT NULL DEFAULT 0,
    agent_directory TEXT NOT NULL DEFAULT '',
    agent_profile TEXT NOT NULL DEFAULT '',
    agent_shell TEXT NOT NULL DEFAULT '',
    agent_tab_color TEXT NOT NULL DEFAULT '',
    agent_env_vars TEXT NOT NULL DEFAULT '{}',
    agent_env_file TEXT NOT NULL DEFAULT '',
    default_agent_template TEXT NOT NULL DEFAULT '',
    agent_provider TEXT NOT NULL DEFAULT '',
    agent_boot_command TEXT NOT NULL DEFAULT '',
    agent_model TEXT NOT NULL DEFAULT '',
    agent_reasoning_effort TEXT NOT NULL DEFAULT '',
    git_worktree INTEGER NOT NULL DEFAULT 0,
    worktree_base_dir TEXT NOT NULL DEFAULT '.loom/worktrees',
    worktree_base_branch TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint INTEGER NOT NULL DEFAULT 0,
    checkpoint_on_progress INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash INTEGER NOT NULL DEFAULT 1,
    worktree_merge_instructions TEXT NOT NULL DEFAULT '',
    worktree_merge_cleanup TEXT NOT NULL DEFAULT 'keep',
    worktree_merge_preserve_diff INTEGER NOT NULL DEFAULT 0,
    worktree_symlinks TEXT NOT NULL DEFAULT '[]',
    agent_session_resume INTEGER NOT NULL DEFAULT 1,
    agent_idle_timeout INTEGER NOT NULL DEFAULT 0,
    agent_always_custom_dialog INTEGER NOT NULL DEFAULT 0,
    notifications INTEGER NOT NULL DEFAULT 0,
    notify_on_finish INTEGER NOT NULL DEFAULT 1,
    notify_on_error INTEGER NOT NULL DEFAULT 1,
    notify_on_attention INTEGER NOT NULL DEFAULT 1,
    terminal_name_prefix TEXT NOT NULL DEFAULT '',
    terminal_boot_command TEXT NOT NULL DEFAULT '',
    terminal_command_args TEXT NOT NULL DEFAULT '',
    terminal_init_script TEXT NOT NULL DEFAULT '',
    terminal_directory TEXT NOT NULL DEFAULT '',
    terminal_profile TEXT NOT NULL DEFAULT '',
    terminal_shell TEXT NOT NULL DEFAULT '',
    terminal_tab_color TEXT NOT NULL DEFAULT '',
    terminal_env_vars TEXT NOT NULL DEFAULT '{}',
    terminal_env_file TEXT NOT NULL DEFAULT '',
    terminal_always_custom_dialog INTEGER NOT NULL DEFAULT 0,
    terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0,
    dispatch_lane TEXT NOT NULL DEFAULT 'In Progress',
    dispatch_auto_terminals INTEGER NOT NULL DEFAULT 0,
    board_default_labels TEXT NOT NULL DEFAULT '[]',
    board_default_lane TEXT NOT NULL DEFAULT '',
    board_default_action TEXT NOT NULL DEFAULT '',
    weaver_agent_id TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS global_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_tasks (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    action_name TEXT NOT NULL DEFAULT '',
    action_vars TEXT NOT NULL DEFAULT '{}',
    agent_template TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    criteria TEXT NOT NULL DEFAULT '',
    lane TEXT NOT NULL DEFAULT 'Backlog',
    position INTEGER NOT NULL DEFAULT 0,
    agent_id TEXT NOT NULL DEFAULT '',
    reply_agent_id TEXT NOT NULL DEFAULT '',
    labels TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    lane_entered_at TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    external_url TEXT NOT NULL DEFAULT '',
    parent_task_id TEXT NOT NULL DEFAULT '',
    pipeline_depth INTEGER NOT NULL DEFAULT 0,
    pipeline_root_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    scheduled_at TEXT NOT NULL DEFAULT '',
    messages TEXT NOT NULL DEFAULT '[]',
    depends_on TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    health_state TEXT NOT NULL DEFAULT 'healthy',
    health_since TEXT NOT NULL DEFAULT '',
    health_details TEXT NOT NULL DEFAULT '{}',
    artifacts TEXT NOT NULL DEFAULT '[]',
    verification_mode TEXT NOT NULL DEFAULT '',
    verification_state TEXT NOT NULL DEFAULT '',
    verification_notes TEXT NOT NULL DEFAULT '',
    verification_updated_at TEXT NOT NULL DEFAULT '',
    verification_updated_by TEXT NOT NULL DEFAULT '',
    verification_summary TEXT NOT NULL DEFAULT '{}',
    worktree_boundary TEXT NOT NULL DEFAULT '{}',
    resume_after_boundary_task_id TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',
    archived_from_lane TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS board_lanes (
    name TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ui_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL DEFAULT '',
    task_template TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    action_name TEXT NOT NULL DEFAULT '',
    action_vars TEXT NOT NULL DEFAULT '{}',
    agent_template TEXT NOT NULL DEFAULT '',
    labels TEXT NOT NULL DEFAULT '[]',
    cron_expr TEXT NOT NULL DEFAULT '',
    scheduled_at TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT NOT NULL DEFAULT '',
    next_run_at TEXT NOT NULL DEFAULT '',
    run_count INTEGER NOT NULL DEFAULT 0,
    last_task_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS auto_dispatch_queue (
    group_name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    task_id TEXT NOT NULL,
    agent_group TEXT NOT NULL DEFAULT '',
    max_concurrent INTEGER NOT NULL DEFAULT 1,
    target_agent_id TEXT NOT NULL DEFAULT '',
    weaver_owner_id TEXT NOT NULL DEFAULT '',
    enqueued_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_name, position)
);

CREATE TABLE IF NOT EXISTS weaver_settings (
    group_name TEXT PRIMARY KEY,
    push_interval INTEGER NOT NULL DEFAULT 60,
    max_interval INTEGER NOT NULL DEFAULT 300,
    heartbeat_interval INTEGER NOT NULL DEFAULT 300,
    default_worker_concurrency INTEGER NOT NULL DEFAULT 2,
    autonomy_mode TEXT NOT NULL DEFAULT 'dispatch_when_clear',
    wave_size_preference TEXT NOT NULL DEFAULT 'small',
    same_agent_follow_up_preference TEXT NOT NULL DEFAULT 'balanced',
    digest_verbosity TEXT NOT NULL DEFAULT 'balanced',
    escalation_style TEXT NOT NULL DEFAULT 'note_then_ask',
    paused INTEGER NOT NULL DEFAULT 0,
    custom_instructions TEXT NOT NULL DEFAULT '',
    restrict_to_created_agents INTEGER NOT NULL DEFAULT 0,
    pending_question TEXT NOT NULL DEFAULT '',
    pending_note TEXT NOT NULL DEFAULT '',
    pending_note_kind TEXT NOT NULL DEFAULT '',
    enabled_events TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived","task_health_alert"]',
    weaver_provider TEXT NOT NULL DEFAULT '',
    weaver_boot_command TEXT NOT NULL DEFAULT '',
    weaver_model TEXT NOT NULL DEFAULT '',
    weaver_reasoning_effort TEXT NOT NULL DEFAULT '',
    weaver_directory TEXT NOT NULL DEFAULT '',
    weaver_profile TEXT NOT NULL DEFAULT '',
    weaver_shell TEXT NOT NULL DEFAULT '',
    weaver_tab_color TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS weaver_worklog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    entry TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS panel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    kind TEXT NOT NULL,
    cell_id TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_history (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT DEFAULT '',
    "group" TEXT DEFAULT '',
    agent_type TEXT DEFAULT '',
    template TEXT DEFAULT '',
    created_at REAL NOT NULL,
    removed_at REAL,
    worktree_branch TEXT DEFAULT '',
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0,
    total_tasks INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_title TEXT NOT NULL,
    started_at REAL NOT NULL,
    completed_at REAL,
    outcome TEXT DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agent_history(id)
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    task_id TEXT DEFAULT '',
    timestamp REAL NOT NULL,
    action TEXT NOT NULL,
    message TEXT DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agent_history(id)
);

CREATE TABLE IF NOT EXISTS weaver_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    timestamp REAL NOT NULL,
    entry_type TEXT NOT NULL,
    entry TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_id_counters (
    group_prefix TEXT PRIMARY KEY,
    next_root_number INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pipeline_task_counters (
    root_task_id TEXT PRIMARY KEY,
    next_child_number INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task_id_aliases (
    legacy_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    scope_kind TEXT NOT NULL DEFAULT '',
    scope_ref TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    retention_kind TEXT NOT NULL DEFAULT 'durable',
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_links (
    entry_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entry_id, target_kind, target_ref),
    FOREIGN KEY (entry_id) REFERENCES memory_entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_name);
CREATE INDEX IF NOT EXISTS idx_agents_group ON agents(group_name);
CREATE INDEX IF NOT EXISTS idx_board_tasks_group ON board_tasks(group_name);
CREATE INDEX IF NOT EXISTS idx_auto_dispatch_queue_group ON auto_dispatch_queue(group_name, position);
CREATE INDEX IF NOT EXISTS idx_worklog_group ON weaver_worklog(group_name);
CREATE INDEX IF NOT EXISTS idx_panel_events_group_id ON panel_events(group_name, id);
CREATE INDEX IF NOT EXISTS idx_panel_events_ts ON panel_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent ON agent_tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_agent ON agent_messages(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_task ON agent_messages(task_id);
CREATE INDEX IF NOT EXISTS idx_weaver_journal_group ON weaver_journal(group_name, id DESC);
CREATE INDEX IF NOT EXISTS idx_task_id_aliases_task ON task_id_aliases(task_id);
CREATE INDEX IF NOT EXISTS idx_memory_group ON memory_entries(group_name);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(scope_kind, scope_ref);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_memory_pinned ON memory_entries(pinned);
CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_kind, target_ref);
"#;

fn parse_json_or_default<T>(raw: &str) -> T
where
    T: DeserializeOwned + Default,
{
    serde_json::from_str(raw).unwrap_or_default()
}

fn row_string(row: &Row<'_>, name: &str) -> String {
    row.get::<_, String>(name).unwrap_or_default()
}

fn row_optional_string(row: &Row<'_>, name: &str) -> Option<String> {
    row.get::<_, Option<String>>(name)
        .unwrap_or(None)
        .filter(|s| !s.is_empty())
}

fn row_bool(row: &Row<'_>, name: &str, default: bool) -> bool {
    row.get::<_, i64>(name).map(|v| v != 0).unwrap_or(default)
}

fn row_i32(row: &Row<'_>, name: &str, default: i32) -> i32 {
    row.get::<_, i32>(name).unwrap_or(default)
}

fn row_i64(row: &Row<'_>, name: &str, default: i64) -> i64 {
    row.get::<_, i64>(name).unwrap_or(default)
}

fn clear_agent_ephemeral(agent: &mut AgentCell) {
    // Match loom/state.py::_EPHEMERAL_FIELDS.
    agent.current_process = String::new();
    agent.current_path = String::new();
    agent.current_branch = String::new();
    agent.git_root = String::new();
    agent.activity = String::new();
    agent.activity_detail = String::new();
    agent.last_event_at = 0.0;
    agent.last_event_text = String::new();
    agent.session_tokens_in = 0;
    agent.session_tokens_out = 0;
    agent.error_message = String::new();
    agent.needs_attention = false;
    agent.last_summary = String::new();
    agent.current_task_id = String::new();
    agent.worktree_dirty = false;
    agent.worktree_diff = serde_json::Map::new();
    agent.worktree_changed_files = Vec::new();
    agent.worktree_checkpoints = 0;
    agent.last_checkpoint_at = 0.0;
    agent.mcp_messages = Vec::new();
    agent.pending_weaver_message = false;
}

fn decode_agent_row(row: &Row<'_>) -> AgentCell {
    let data = row_string(row, "data");
    if !data.is_empty() {
        if let Ok(mut agent) = serde_json::from_str::<AgentCell>(&data) {
            clear_agent_ephemeral(&mut agent);
            return agent;
        }
    }

    let id = row_string(row, "id");
    let name = row_string(row, "name");
    let group = row_string(row, "group_name");
    let mut agent = AgentCell::new(id, name, group);
    agent.slug = row_string(row, "slug");
    agent.cell_type = row_string(row, "cell_type");
    if agent.cell_type.is_empty() {
        agent.cell_type = "agent".into();
    }
    agent.terminal_backend = row_string(row, "terminal_backend");
    if agent.terminal_backend.is_empty() {
        agent.terminal_backend = "iterm2".into();
    }
    agent.session_id = row_optional_string(row, "session_id");
    agent.profile = row_string(row, "profile");
    if agent.profile.is_empty() {
        agent.profile = "Default".into();
    }
    agent.command = row_string(row, "command");
    agent.directory = row_string(row, "directory");
    agent.tab_color = row_string(row, "tab_color");
    agent.icon = row_string(row, "icon");
    agent.template = row_string(row, "template");
    agent.window_id = row_string(row, "window_id");
    agent.parent_id = row_string(row, "parent_id");
    agent.status = row_string(row, "status");
    if agent.status.is_empty() {
        agent.status = "stopped".into();
    }
    agent.worktree_path = row_string(row, "worktree_path");
    agent.worktree_branch = row_string(row, "worktree_branch");
    agent.worktree_repo_root = row_string(row, "worktree_repo_root");
    agent.worktree_base_dir = row_string(row, "worktree_base_dir");
    if agent.worktree_base_dir.is_empty() {
        agent.worktree_base_dir = ".loom/worktrees".into();
    }
    agent.worktree_base_branch = row_string(row, "worktree_base_branch");
    agent.worktree_auto_checkpoint = row_bool(row, "worktree_auto_checkpoint", false);
    agent.checkpoint_on_progress = row_bool(row, "checkpoint_on_progress", false);
    agent.worktree_merge_squash = row_bool(row, "worktree_merge_squash", true);
    agent.agent_type = row_string(row, "agent_type");
    agent.agent_session_id = row_string(row, "agent_session_id");
    agent.session_resume = row_bool(row, "session_resume", true);
    agent.idle_timeout = row_i32(row, "idle_timeout", 0);
    agent.tasks_dispatched = row_i32(row, "tasks_dispatched", 0);
    agent.created_by_weaver_id = row_string(row, "created_by_weaver_id");
    clear_agent_ephemeral(&mut agent);
    agent
}

fn decode_board_task_row(row: &Row<'_>) -> BoardTask {
    let data = row_string(row, "data");
    if !data.is_empty() {
        if let Ok(task) = serde_json::from_str::<BoardTask>(&data) {
            return task;
        }
    }

    let mut task = BoardTask::new_minimal(row_string(row, "id"), row_string(row, "task"));
    task.description = row_string(row, "description");
    task.slug = row_string(row, "slug");
    task.group = row_string(row, "group_name");
    task.action_name = row_string(row, "action_name");
    task.action_vars = parse_json_or_default(&row_string(row, "action_vars"));
    task.agent_template = row_string(row, "agent_template");
    task.instructions = row_string(row, "instructions");
    task.context = row_string(row, "context");
    task.criteria = row_string(row, "criteria");
    task.lane = row_string(row, "lane");
    if task.lane.is_empty() {
        task.lane = "Backlog".into();
    }
    task.position = row_i64(row, "position", 0);
    task.agent_id = row_string(row, "agent_id");
    task.reply_agent_id = row_string(row, "reply_agent_id");
    task.labels = parse_json_or_default(&row_string(row, "labels"));
    task.created_at = row_string(row, "created_at");
    task.updated_at = row_string(row, "updated_at");
    task.lane_entered_at = row_string(row, "lane_entered_at");
    task.provider = row_string(row, "provider");
    task.external_id = row_string(row, "external_id");
    task.external_url = row_string(row, "external_url");
    task.parent_task_id = row_string(row, "parent_task_id");
    task.pipeline_depth = row_i32(row, "pipeline_depth", 0);
    task.pipeline_root_id = row_string(row, "pipeline_root_id");
    task.status = row_string(row, "status");
    task.scheduled_at = row_string(row, "scheduled_at");
    task.messages = parse_json_or_default(&row_string(row, "messages"));
    task.depends_on = parse_json_or_default(&row_string(row, "depends_on"));
    task.attachments = parse_json_or_default(&row_string(row, "attachments"));
    task.health_state = row_string(row, "health_state");
    if task.health_state.is_empty() {
        task.health_state = "healthy".into();
    }
    task.health_since = row_string(row, "health_since");
    task.health_details = parse_json_or_default(&row_string(row, "health_details"));
    task.artifacts = parse_json_or_default(&row_string(row, "artifacts"));
    task.verification_mode = row_string(row, "verification_mode");
    task.verification_state = row_string(row, "verification_state");
    task.verification_notes = row_string(row, "verification_notes");
    task.verification_updated_at = row_string(row, "verification_updated_at");
    task.verification_updated_by = row_string(row, "verification_updated_by");
    task.verification_summary = parse_json_or_default(&row_string(row, "verification_summary"));
    task.worktree_boundary = parse_json_or_default(&row_string(row, "worktree_boundary"));
    task.resume_after_boundary_task_id = row_string(row, "resume_after_boundary_task_id");
    task.archived_at = row_string(row, "archived_at");
    task.archived_from_lane = row_string(row, "archived_from_lane");
    task
}

fn decode_schedule_row(row: &Row<'_>) -> Schedule {
    let data = row_string(row, "data");
    if !data.is_empty() {
        if let Ok(schedule) = serde_json::from_str::<Schedule>(&data) {
            return schedule;
        }
    }

    Schedule {
        id: row_string(row, "id"),
        name: row_string(row, "name"),
        slug: row_string(row, "slug"),
        task_template: row_string(row, "task_template"),
        description: row_string(row, "description"),
        group: row_string(row, "group_name"),
        action_name: row_string(row, "action_name"),
        action_vars: parse_json_or_default(&row_string(row, "action_vars")),
        agent_template: row_string(row, "agent_template"),
        labels: parse_json_or_default(&row_string(row, "labels")),
        cron_expr: row_string(row, "cron_expr"),
        scheduled_at: row_string(row, "scheduled_at"),
        timezone: row_string(row, "timezone"),
        enabled: row_bool(row, "enabled", true),
        last_run_at: row_string(row, "last_run_at"),
        next_run_at: row_string(row, "next_run_at"),
        run_count: row_i64(row, "run_count", 0),
        last_task_id: row_string(row, "last_task_id"),
        created_at: row_string(row, "created_at"),
        updated_at: row_string(row, "updated_at"),
    }
}

fn decode_group_settings_row(row: &Row<'_>) -> GroupSettings {
    let data = row_string(row, "data");
    if !data.is_empty() {
        if let Ok(settings) = serde_json::from_str::<GroupSettings>(&data) {
            return settings;
        }
    }

    GroupSettings {
        default_directory: row_string(row, "default_directory"),
        default_terminal_backend: {
            let value = row_string(row, "default_terminal_backend");
            if value.is_empty() {
                "iterm2".into()
            } else {
                value
            }
        },
        profile: row_string(row, "profile"),
        shell: row_string(row, "shell"),
        tab_color: row_string(row, "tab_color"),
        env_vars: parse_json_or_default(&row_string(row, "env_vars")),
        env_file: row_string(row, "env_file"),
        auto_terminals: row_i32(row, "auto_terminals", 0),
        max_agents: row_i32(row, "max_agents", 0),
        collapsed_default: row_bool(row, "collapsed_default", false),
        filter_by_window: row_bool(row, "filter_by_window", false),
        agent_directory: row_string(row, "agent_directory"),
        agent_profile: row_string(row, "agent_profile"),
        agent_shell: row_string(row, "agent_shell"),
        agent_tab_color: row_string(row, "agent_tab_color"),
        agent_env_vars: parse_json_or_default(&row_string(row, "agent_env_vars")),
        agent_env_file: row_string(row, "agent_env_file"),
        default_agent_template: row_string(row, "default_agent_template"),
        agent_provider: row_string(row, "agent_provider"),
        agent_boot_command: row_string(row, "agent_boot_command"),
        agent_model: row_string(row, "agent_model"),
        agent_reasoning_effort: row_string(row, "agent_reasoning_effort"),
        git_worktree: row_bool(row, "git_worktree", false),
        worktree_base_dir: {
            let value = row_string(row, "worktree_base_dir");
            if value.is_empty() {
                ".loom/worktrees".into()
            } else {
                value
            }
        },
        worktree_base_branch: row_string(row, "worktree_base_branch"),
        worktree_auto_checkpoint: row_bool(row, "worktree_auto_checkpoint", false),
        checkpoint_on_progress: row_bool(row, "checkpoint_on_progress", false),
        worktree_merge_squash: row_bool(row, "worktree_merge_squash", true),
        worktree_merge_instructions: row_string(row, "worktree_merge_instructions"),
        worktree_merge_cleanup: {
            let value = row_string(row, "worktree_merge_cleanup");
            if value.is_empty() {
                "keep".into()
            } else {
                value
            }
        },
        worktree_merge_preserve_diff: row_bool(row, "worktree_merge_preserve_diff", false),
        worktree_symlinks: parse_json_or_default(&row_string(row, "worktree_symlinks")),
        agent_session_resume: row_bool(row, "agent_session_resume", true),
        agent_idle_timeout: row_i32(row, "agent_idle_timeout", 0),
        agent_always_custom_dialog: row_bool(row, "agent_always_custom_dialog", false),
        notifications: row_bool(row, "notifications", false),
        notify_on_finish: row_bool(row, "notify_on_finish", true),
        notify_on_error: row_bool(row, "notify_on_error", true),
        notify_on_attention: row_bool(row, "notify_on_attention", true),
        terminal_name_prefix: row_string(row, "terminal_name_prefix"),
        terminal_boot_command: row_string(row, "terminal_boot_command"),
        terminal_command_args: row_string(row, "terminal_command_args"),
        terminal_init_script: row_string(row, "terminal_init_script"),
        terminal_directory: row_string(row, "terminal_directory"),
        terminal_profile: row_string(row, "terminal_profile"),
        terminal_shell: row_string(row, "terminal_shell"),
        terminal_tab_color: row_string(row, "terminal_tab_color"),
        terminal_env_vars: parse_json_or_default(&row_string(row, "terminal_env_vars")),
        terminal_env_file: row_string(row, "terminal_env_file"),
        terminal_always_custom_dialog: row_bool(row, "terminal_always_custom_dialog", false),
        terminal_close_on_disconnect: row_bool(row, "terminal_close_on_disconnect", false),
        dispatch_lane: {
            let value = row_string(row, "dispatch_lane");
            if value.is_empty() {
                "In Progress".into()
            } else {
                value
            }
        },
        dispatch_auto_terminals: row_bool(row, "dispatch_auto_terminals", false),
        board_default_labels: parse_json_or_default(&row_string(row, "board_default_labels")),
        board_default_lane: row_string(row, "board_default_lane"),
        board_default_action: row_string(row, "board_default_action"),
        weaver_agent_id: row_string(row, "weaver_agent_id"),
    }
}

fn decode_weaver_settings_row(row: &Row<'_>) -> WeaverSettings {
    let data = row_string(row, "data");
    if !data.is_empty() {
        if let Ok(settings) = serde_json::from_str::<WeaverSettings>(&data) {
            return settings;
        }
    }

    WeaverSettings {
        group: row_string(row, "group_name"),
        push_interval: row_i32(row, "push_interval", 60),
        max_interval: row_i32(row, "max_interval", 300),
        heartbeat_interval: row_i32(row, "heartbeat_interval", 300),
        default_worker_concurrency: row_i32(row, "default_worker_concurrency", 2),
        autonomy_mode: {
            let value = row_string(row, "autonomy_mode");
            if value.is_empty() {
                "dispatch_when_clear".into()
            } else {
                value
            }
        },
        wave_size_preference: {
            let value = row_string(row, "wave_size_preference");
            if value.is_empty() {
                "small".into()
            } else {
                value
            }
        },
        same_agent_follow_up_preference: {
            let value = row_string(row, "same_agent_follow_up_preference");
            if value.is_empty() {
                "balanced".into()
            } else {
                value
            }
        },
        digest_verbosity: {
            let value = row_string(row, "digest_verbosity");
            if value.is_empty() {
                "balanced".into()
            } else {
                value
            }
        },
        escalation_style: {
            let value = row_string(row, "escalation_style");
            if value.is_empty() {
                "note_then_ask".into()
            } else {
                value
            }
        },
        paused: row_bool(row, "paused", false),
        custom_instructions: row_string(row, "custom_instructions"),
        restrict_to_created_agents: row_bool(row, "restrict_to_created_agents", false),
        pending_question: row_string(row, "pending_question"),
        pending_note: row_string(row, "pending_note"),
        pending_note_kind: row_string(row, "pending_note_kind"),
        weaver_provider: row_string(row, "weaver_provider"),
        weaver_boot_command: row_string(row, "weaver_boot_command"),
        weaver_model: row_string(row, "weaver_model"),
        weaver_reasoning_effort: row_string(row, "weaver_reasoning_effort"),
        weaver_directory: row_string(row, "weaver_directory"),
        weaver_profile: row_string(row, "weaver_profile"),
        weaver_shell: row_string(row, "weaver_shell"),
        weaver_tab_color: row_string(row, "weaver_tab_color"),
        enabled_events: parse_json_or_default(&row_string(row, "enabled_events")),
    }
}

fn column_exists(conn: &Connection, table: &str, column: &str) -> crate::Result<bool> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = stmt.query_map([], |r| r.get::<_, String>(1))?;
    for row in rows {
        if row? == column {
            return Ok(true);
        }
    }
    Ok(false)
}

fn ensure_column(
    conn: &Connection,
    table: &str,
    column: &str,
    definition: &str,
) -> crate::Result<()> {
    if !column_exists(conn, table, column)? {
        conn.execute(
            &format!("ALTER TABLE {table} ADD COLUMN {column} {definition}"),
            [],
        )?;
    }
    Ok(())
}

fn migrate_compat_schema(conn: &Connection) -> crate::Result<()> {
    conn.pragma_update(None, "foreign_keys", "ON")?;

    for (table, columns) in [
        (
            "agents",
            vec![
                ("slug", "TEXT NOT NULL DEFAULT ''"),
                ("cell_type", "TEXT NOT NULL DEFAULT 'agent'"),
                ("terminal_backend", "TEXT NOT NULL DEFAULT 'iterm2'"),
                ("session_id", "TEXT"),
                ("profile", "TEXT NOT NULL DEFAULT 'Default'"),
                ("command", "TEXT NOT NULL DEFAULT ''"),
                ("directory", "TEXT NOT NULL DEFAULT ''"),
                ("tab_color", "TEXT NOT NULL DEFAULT ''"),
                ("icon", "TEXT NOT NULL DEFAULT ''"),
                ("template", "TEXT NOT NULL DEFAULT ''"),
                ("window_id", "TEXT NOT NULL DEFAULT ''"),
                ("parent_id", "TEXT NOT NULL DEFAULT ''"),
                ("status", "TEXT NOT NULL DEFAULT 'stopped'"),
                ("worktree_path", "TEXT NOT NULL DEFAULT ''"),
                ("worktree_branch", "TEXT NOT NULL DEFAULT ''"),
                ("worktree_repo_root", "TEXT NOT NULL DEFAULT ''"),
                ("worktree_base_dir", "TEXT NOT NULL DEFAULT '.loom/worktrees'"),
                ("worktree_base_branch", "TEXT NOT NULL DEFAULT ''"),
                ("worktree_auto_checkpoint", "INTEGER NOT NULL DEFAULT 0"),
                ("checkpoint_on_progress", "INTEGER NOT NULL DEFAULT 0"),
                ("worktree_merge_squash", "INTEGER NOT NULL DEFAULT 1"),
                ("agent_type", "TEXT NOT NULL DEFAULT ''"),
                ("agent_session_id", "TEXT NOT NULL DEFAULT ''"),
                ("session_resume", "INTEGER NOT NULL DEFAULT 1"),
                ("idle_timeout", "INTEGER NOT NULL DEFAULT 0"),
                ("tasks_dispatched", "INTEGER NOT NULL DEFAULT 0"),
                ("created_by_weaver_id", "TEXT NOT NULL DEFAULT ''"),
                ("data", "TEXT NOT NULL DEFAULT ''"),
            ],
        ),
        (
            "groups",
            vec![
                ("slug", "TEXT NOT NULL DEFAULT ''"),
                ("position", "INTEGER NOT NULL DEFAULT 0"),
                ("ordinal", "INTEGER NOT NULL DEFAULT 0"),
            ],
        ),
        (
            "group_settings",
            vec![
                ("default_directory", "TEXT NOT NULL DEFAULT ''"),
                ("default_terminal_backend", "TEXT NOT NULL DEFAULT 'iterm2'"),
                ("profile", "TEXT NOT NULL DEFAULT ''"),
                ("shell", "TEXT NOT NULL DEFAULT ''"),
                ("tab_color", "TEXT NOT NULL DEFAULT ''"),
                ("env_vars", "TEXT NOT NULL DEFAULT '{}'"),
                ("env_file", "TEXT NOT NULL DEFAULT ''"),
                ("auto_terminals", "INTEGER NOT NULL DEFAULT 0"),
                ("max_agents", "INTEGER NOT NULL DEFAULT 0"),
                ("collapsed_default", "INTEGER NOT NULL DEFAULT 0"),
                ("filter_by_window", "INTEGER NOT NULL DEFAULT 0"),
                ("agent_directory", "TEXT NOT NULL DEFAULT ''"),
                ("agent_profile", "TEXT NOT NULL DEFAULT ''"),
                ("agent_shell", "TEXT NOT NULL DEFAULT ''"),
                ("agent_tab_color", "TEXT NOT NULL DEFAULT ''"),
                ("agent_env_vars", "TEXT NOT NULL DEFAULT '{}'"),
                ("agent_env_file", "TEXT NOT NULL DEFAULT ''"),
                ("default_agent_template", "TEXT NOT NULL DEFAULT ''"),
                ("agent_provider", "TEXT NOT NULL DEFAULT ''"),
                ("agent_boot_command", "TEXT NOT NULL DEFAULT ''"),
                ("agent_model", "TEXT NOT NULL DEFAULT ''"),
                ("agent_reasoning_effort", "TEXT NOT NULL DEFAULT ''"),
                ("git_worktree", "INTEGER NOT NULL DEFAULT 0"),
                ("worktree_base_dir", "TEXT NOT NULL DEFAULT '.loom/worktrees'"),
                ("worktree_base_branch", "TEXT NOT NULL DEFAULT ''"),
                ("worktree_auto_checkpoint", "INTEGER NOT NULL DEFAULT 0"),
                ("checkpoint_on_progress", "INTEGER NOT NULL DEFAULT 0"),
                ("worktree_merge_squash", "INTEGER NOT NULL DEFAULT 1"),
                ("worktree_merge_instructions", "TEXT NOT NULL DEFAULT ''"),
                ("worktree_merge_cleanup", "TEXT NOT NULL DEFAULT 'keep'"),
                ("worktree_merge_preserve_diff", "INTEGER NOT NULL DEFAULT 0"),
                ("worktree_symlinks", "TEXT NOT NULL DEFAULT '[]'"),
                ("agent_session_resume", "INTEGER NOT NULL DEFAULT 1"),
                ("agent_idle_timeout", "INTEGER NOT NULL DEFAULT 0"),
                ("agent_always_custom_dialog", "INTEGER NOT NULL DEFAULT 0"),
                ("notifications", "INTEGER NOT NULL DEFAULT 0"),
                ("notify_on_finish", "INTEGER NOT NULL DEFAULT 1"),
                ("notify_on_error", "INTEGER NOT NULL DEFAULT 1"),
                ("notify_on_attention", "INTEGER NOT NULL DEFAULT 1"),
                ("terminal_name_prefix", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_boot_command", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_command_args", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_init_script", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_directory", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_profile", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_shell", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_tab_color", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_env_vars", "TEXT NOT NULL DEFAULT '{}'"),
                ("terminal_env_file", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_always_custom_dialog", "INTEGER NOT NULL DEFAULT 0"),
                ("terminal_close_on_disconnect", "INTEGER NOT NULL DEFAULT 0"),
                ("dispatch_lane", "TEXT NOT NULL DEFAULT 'In Progress'"),
                ("dispatch_auto_terminals", "INTEGER NOT NULL DEFAULT 0"),
                ("board_default_labels", "TEXT NOT NULL DEFAULT '[]'"),
                ("board_default_lane", "TEXT NOT NULL DEFAULT ''"),
                ("board_default_action", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_agent_id", "TEXT NOT NULL DEFAULT ''"),
                ("data", "TEXT NOT NULL DEFAULT ''"),
            ],
        ),
        (
            "board_tasks",
            vec![
                ("task", "TEXT NOT NULL DEFAULT ''"),
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("slug", "TEXT NOT NULL DEFAULT ''"),
                ("group_name", "TEXT NOT NULL DEFAULT ''"),
                ("action_name", "TEXT NOT NULL DEFAULT ''"),
                ("action_vars", "TEXT NOT NULL DEFAULT '{}'"),
                ("agent_template", "TEXT NOT NULL DEFAULT ''"),
                ("instructions", "TEXT NOT NULL DEFAULT ''"),
                ("context", "TEXT NOT NULL DEFAULT ''"),
                ("criteria", "TEXT NOT NULL DEFAULT ''"),
                ("lane", "TEXT NOT NULL DEFAULT 'Backlog'"),
                ("position", "INTEGER NOT NULL DEFAULT 0"),
                ("agent_id", "TEXT NOT NULL DEFAULT ''"),
                ("reply_agent_id", "TEXT NOT NULL DEFAULT ''"),
                ("labels", "TEXT NOT NULL DEFAULT '[]'"),
                ("created_at", "TEXT NOT NULL DEFAULT ''"),
                ("updated_at", "TEXT NOT NULL DEFAULT ''"),
                ("lane_entered_at", "TEXT NOT NULL DEFAULT ''"),
                ("provider", "TEXT NOT NULL DEFAULT ''"),
                ("external_id", "TEXT NOT NULL DEFAULT ''"),
                ("external_url", "TEXT NOT NULL DEFAULT ''"),
                ("parent_task_id", "TEXT NOT NULL DEFAULT ''"),
                ("pipeline_depth", "INTEGER NOT NULL DEFAULT 0"),
                ("pipeline_root_id", "TEXT NOT NULL DEFAULT ''"),
                ("status", "TEXT NOT NULL DEFAULT ''"),
                ("scheduled_at", "TEXT NOT NULL DEFAULT ''"),
                ("messages", "TEXT NOT NULL DEFAULT '[]'"),
                ("depends_on", "TEXT NOT NULL DEFAULT '[]'"),
                ("attachments", "TEXT NOT NULL DEFAULT '[]'"),
                ("health_state", "TEXT NOT NULL DEFAULT 'healthy'"),
                ("health_since", "TEXT NOT NULL DEFAULT ''"),
                ("health_details", "TEXT NOT NULL DEFAULT '{}'"),
                ("artifacts", "TEXT NOT NULL DEFAULT '[]'"),
                ("verification_mode", "TEXT NOT NULL DEFAULT ''"),
                ("verification_state", "TEXT NOT NULL DEFAULT ''"),
                ("verification_notes", "TEXT NOT NULL DEFAULT ''"),
                ("verification_updated_at", "TEXT NOT NULL DEFAULT ''"),
                ("verification_updated_by", "TEXT NOT NULL DEFAULT ''"),
                ("verification_summary", "TEXT NOT NULL DEFAULT '{}'"),
                ("worktree_boundary", "TEXT NOT NULL DEFAULT '{}'"),
                ("resume_after_boundary_task_id", "TEXT NOT NULL DEFAULT ''"),
                ("archived_at", "TEXT NOT NULL DEFAULT ''"),
                ("archived_from_lane", "TEXT NOT NULL DEFAULT ''"),
                ("data", "TEXT NOT NULL DEFAULT ''"),
            ],
        ),
        (
            "schedules",
            vec![
                ("name", "TEXT NOT NULL DEFAULT ''"),
                ("slug", "TEXT NOT NULL DEFAULT ''"),
                ("task_template", "TEXT NOT NULL DEFAULT ''"),
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("group_name", "TEXT NOT NULL DEFAULT ''"),
                ("action_name", "TEXT NOT NULL DEFAULT ''"),
                ("action_vars", "TEXT NOT NULL DEFAULT '{}'"),
                ("agent_template", "TEXT NOT NULL DEFAULT ''"),
                ("labels", "TEXT NOT NULL DEFAULT '[]'"),
                ("cron_expr", "TEXT NOT NULL DEFAULT ''"),
                ("scheduled_at", "TEXT NOT NULL DEFAULT ''"),
                ("timezone", "TEXT NOT NULL DEFAULT ''"),
                ("enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("last_run_at", "TEXT NOT NULL DEFAULT ''"),
                ("next_run_at", "TEXT NOT NULL DEFAULT ''"),
                ("run_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_task_id", "TEXT NOT NULL DEFAULT ''"),
                ("created_at", "TEXT NOT NULL DEFAULT ''"),
                ("updated_at", "TEXT NOT NULL DEFAULT ''"),
                ("data", "TEXT NOT NULL DEFAULT ''"),
            ],
        ),
        (
            "weaver_settings",
            vec![
                ("push_interval", "INTEGER NOT NULL DEFAULT 60"),
                ("max_interval", "INTEGER NOT NULL DEFAULT 300"),
                ("heartbeat_interval", "INTEGER NOT NULL DEFAULT 300"),
                ("default_worker_concurrency", "INTEGER NOT NULL DEFAULT 2"),
                ("autonomy_mode", "TEXT NOT NULL DEFAULT 'dispatch_when_clear'"),
                ("wave_size_preference", "TEXT NOT NULL DEFAULT 'small'"),
                (
                    "same_agent_follow_up_preference",
                    "TEXT NOT NULL DEFAULT 'balanced'",
                ),
                ("digest_verbosity", "TEXT NOT NULL DEFAULT 'balanced'"),
                ("escalation_style", "TEXT NOT NULL DEFAULT 'note_then_ask'"),
                ("paused", "INTEGER NOT NULL DEFAULT 0"),
                ("custom_instructions", "TEXT NOT NULL DEFAULT ''"),
                ("restrict_to_created_agents", "INTEGER NOT NULL DEFAULT 0"),
                ("pending_question", "TEXT NOT NULL DEFAULT ''"),
                ("pending_note", "TEXT NOT NULL DEFAULT ''"),
                ("pending_note_kind", "TEXT NOT NULL DEFAULT ''"),
                (
                    "enabled_events",
                    "TEXT NOT NULL DEFAULT '[\"agent_started\",\"task_dispatched\",\"task_derived\",\"task_health_alert\"]'",
                ),
                ("weaver_provider", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_boot_command", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_model", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_reasoning_effort", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_directory", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_profile", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_shell", "TEXT NOT NULL DEFAULT ''"),
                ("weaver_tab_color", "TEXT NOT NULL DEFAULT ''"),
                ("data", "TEXT NOT NULL DEFAULT ''"),
            ],
        ),
    ] {
        for (column, definition) in columns {
            ensure_column(conn, table, column, definition)?;
        }
    }

    if column_exists(conn, "groups", "position")? && column_exists(conn, "groups", "ordinal")? {
        conn.execute(
            "UPDATE groups SET position = ordinal WHERE position = 0 AND ordinal != 0",
            [],
        )?;
        conn.execute(
            "UPDATE groups SET ordinal = position WHERE ordinal = 0 AND position != 0",
            [],
        )?;
    }

    Ok(())
}

/// Thread-safe handle around a single sqlite connection.
#[derive(Clone)]
pub struct LoomDb {
    inner: Arc<Mutex<Connection>>,
    path: Arc<PathBuf>,
}

impl LoomDb {
    pub fn open(path: impl AsRef<Path>) -> crate::Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(&path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.execute_batch(SCHEMA_SQL)?;
        migrate_compat_schema(&conn)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(conn)),
            path: Arc::new(path),
        })
    }

    /// Open an in-memory db — used by tests.
    pub fn in_memory() -> crate::Result<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(SCHEMA_SQL)?;
        migrate_compat_schema(&conn)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(conn)),
            path: Arc::new(PathBuf::from(":memory:")),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    // --- save methods ------------------------------------------------------

    pub async fn save_agent(&self, agent: &AgentCell) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(agent)?;
        conn.execute(
            "INSERT OR REPLACE INTO agents(
                id, name, slug, group_name, cell_type, terminal_backend, session_id, profile,
                command, directory, tab_color, icon, template, window_id, parent_id, status,
                worktree_path, worktree_branch, worktree_repo_root, worktree_base_dir,
                worktree_base_branch, worktree_auto_checkpoint, checkpoint_on_progress,
                worktree_merge_squash, agent_type, agent_session_id, session_resume,
                idle_timeout, tasks_dispatched, created_by_weaver_id, data
             ) VALUES (
                ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8,
                ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16,
                ?17, ?18, ?19, ?20,
                ?21, ?22, ?23,
                ?24, ?25, ?26, ?27,
                ?28, ?29, ?30, ?31
             )",
            params![
                agent.id,
                agent.name,
                agent.slug,
                agent.group,
                agent.cell_type,
                agent.terminal_backend,
                agent.session_id,
                agent.profile,
                agent.command,
                agent.directory,
                agent.tab_color,
                agent.icon,
                agent.template,
                agent.window_id,
                agent.parent_id,
                agent.status,
                agent.worktree_path,
                agent.worktree_branch,
                agent.worktree_repo_root,
                agent.worktree_base_dir,
                agent.worktree_base_branch,
                agent.worktree_auto_checkpoint as i64,
                agent.checkpoint_on_progress as i64,
                agent.worktree_merge_squash as i64,
                agent.agent_type,
                agent.agent_session_id,
                agent.session_resume as i64,
                agent.idle_timeout,
                agent.tasks_dispatched,
                agent.created_by_weaver_id,
                data,
            ],
        )?;
        Ok(())
    }

    pub async fn delete_agent(&self, id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM agents WHERE id = ?1", params![id])?;
        conn.execute("DELETE FROM group_members WHERE agent_id = ?1", params![id])?;
        Ok(())
    }

    pub async fn save_group(&self, name: &str, slug: &str, ordinal: i64) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO groups(name, slug, position, ordinal) VALUES (?1, ?2, ?3, ?4)",
            params![name, slug, ordinal, ordinal],
        )?;
        Ok(())
    }

    pub async fn delete_group(&self, name: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM groups WHERE name = ?1", params![name])?;
        conn.execute(
            "DELETE FROM group_members WHERE group_name = ?1",
            params![name],
        )?;
        conn.execute(
            "DELETE FROM group_settings WHERE group_name = ?1",
            params![name],
        )?;
        conn.execute(
            "DELETE FROM auto_dispatch_queue WHERE group_name = ?1",
            params![name],
        )?;
        conn.execute(
            "DELETE FROM weaver_settings WHERE group_name = ?1",
            params![name],
        )?;
        conn.execute(
            "DELETE FROM weaver_worklog WHERE group_name = ?1",
            params![name],
        )?;
        conn.execute(
            "DELETE FROM weaver_journal WHERE group_name = ?1",
            params![name],
        )?;
        Ok(())
    }

    pub async fn save_group_members(&self, group: &str, members: &[String]) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "DELETE FROM group_members WHERE group_name = ?1",
            params![group],
        )?;
        let mut stmt = conn.prepare(
            "INSERT INTO group_members(group_name, agent_id, position) VALUES (?1, ?2, ?3)",
        )?;
        for (i, aid) in members.iter().enumerate() {
            stmt.execute(params![group, aid, i as i64])?;
        }
        Ok(())
    }

    pub async fn save_group_settings(
        &self,
        group: &str,
        settings: &GroupSettings,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(settings)?;
        let env_vars = serde_json::to_string(&settings.env_vars)?;
        let agent_env_vars = serde_json::to_string(&settings.agent_env_vars)?;
        let worktree_symlinks = serde_json::to_string(&settings.worktree_symlinks)?;
        let terminal_env_vars = serde_json::to_string(&settings.terminal_env_vars)?;
        let board_default_labels = serde_json::to_string(&settings.board_default_labels)?;
        conn.execute(
            "INSERT OR REPLACE INTO group_settings(
                group_name, default_directory, default_terminal_backend, profile, shell, tab_color,
                env_vars, env_file, auto_terminals, max_agents, collapsed_default,
                filter_by_window, agent_directory, agent_profile, agent_shell,
                agent_tab_color, agent_env_vars, agent_env_file, default_agent_template,
                agent_provider, agent_boot_command, agent_model, agent_reasoning_effort,
                git_worktree, worktree_base_dir, worktree_base_branch,
                worktree_auto_checkpoint, checkpoint_on_progress, worktree_merge_squash,
                worktree_merge_instructions, worktree_merge_cleanup,
                worktree_merge_preserve_diff, worktree_symlinks, agent_session_resume,
                agent_idle_timeout, agent_always_custom_dialog, notifications,
                notify_on_finish, notify_on_error, notify_on_attention,
                terminal_name_prefix, terminal_boot_command, terminal_command_args,
                terminal_init_script, terminal_directory, terminal_profile,
                terminal_shell, terminal_tab_color, terminal_env_vars, terminal_env_file,
                terminal_always_custom_dialog, terminal_close_on_disconnect,
                dispatch_lane, dispatch_auto_terminals, board_default_labels,
                board_default_lane, board_default_action, weaver_agent_id, data
             ) VALUES (
                ?1, ?2, ?3, ?4, ?5, ?6,
                ?7, ?8, ?9, ?10, ?11,
                ?12, ?13, ?14, ?15,
                ?16, ?17, ?18, ?19,
                ?20, ?21, ?22, ?23,
                ?24, ?25, ?26,
                ?27, ?28, ?29,
                ?30, ?31,
                ?32, ?33, ?34,
                ?35, ?36, ?37,
                ?38, ?39, ?40,
                ?41, ?42, ?43,
                ?44, ?45, ?46,
                ?47, ?48, ?49, ?50,
                ?51, ?52,
                ?53, ?54, ?55,
                ?56, ?57, ?58, ?59
             )",
            params![
                group,
                settings.default_directory,
                settings.default_terminal_backend,
                settings.profile,
                settings.shell,
                settings.tab_color,
                env_vars,
                settings.env_file,
                settings.auto_terminals,
                settings.max_agents,
                settings.collapsed_default as i64,
                settings.filter_by_window as i64,
                settings.agent_directory,
                settings.agent_profile,
                settings.agent_shell,
                settings.agent_tab_color,
                agent_env_vars,
                settings.agent_env_file,
                settings.default_agent_template,
                settings.agent_provider,
                settings.agent_boot_command,
                settings.agent_model,
                settings.agent_reasoning_effort,
                settings.git_worktree as i64,
                settings.worktree_base_dir,
                settings.worktree_base_branch,
                settings.worktree_auto_checkpoint as i64,
                settings.checkpoint_on_progress as i64,
                settings.worktree_merge_squash as i64,
                settings.worktree_merge_instructions,
                settings.worktree_merge_cleanup,
                settings.worktree_merge_preserve_diff as i64,
                worktree_symlinks,
                settings.agent_session_resume as i64,
                settings.agent_idle_timeout,
                settings.agent_always_custom_dialog as i64,
                settings.notifications as i64,
                settings.notify_on_finish as i64,
                settings.notify_on_error as i64,
                settings.notify_on_attention as i64,
                settings.terminal_name_prefix,
                settings.terminal_boot_command,
                settings.terminal_command_args,
                settings.terminal_init_script,
                settings.terminal_directory,
                settings.terminal_profile,
                settings.terminal_shell,
                settings.terminal_tab_color,
                terminal_env_vars,
                settings.terminal_env_file,
                settings.terminal_always_custom_dialog as i64,
                settings.terminal_close_on_disconnect as i64,
                settings.dispatch_lane,
                settings.dispatch_auto_terminals as i64,
                board_default_labels,
                settings.board_default_lane,
                settings.board_default_action,
                settings.weaver_agent_id,
                data,
            ],
        )?;
        Ok(())
    }

    pub async fn save_global_settings(&self, settings: &GlobalSettings) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        if column_exists(&conn, "global_settings", "key")? {
            conn.execute("DELETE FROM global_settings", [])?;
            for (key, value) in [
                (
                    "default_command",
                    serde_json::to_string(&settings.default_command)?,
                ),
                (
                    "filter_by_window",
                    serde_json::to_string(&settings.filter_by_window)?,
                ),
                (
                    "focus_new_tabs",
                    serde_json::to_string(&settings.focus_new_tabs)?,
                ),
                (
                    "focus_on_click",
                    serde_json::to_string(&settings.focus_on_click)?,
                ),
                (
                    "default_lanes",
                    serde_json::to_string(&settings.default_lanes)?,
                ),
                ("keybindings", serde_json::to_string(&settings.keybindings)?),
                (
                    "max_pipeline_depth",
                    serde_json::to_string(&settings.max_pipeline_depth)?,
                ),
                (
                    "max_event_log",
                    serde_json::to_string(&settings.max_event_log)?,
                ),
            ] {
                conn.execute(
                    "INSERT INTO global_settings(key, value) VALUES (?1, ?2)",
                    params![key, value],
                )?;
            }
        } else {
            let data = serde_json::to_string(settings)?;
            conn.execute(
                "INSERT OR REPLACE INTO global_settings(id, data) VALUES (1, ?1)",
                params![data],
            )?;
        }
        Ok(())
    }

    pub async fn save_board_task(&self, task: &BoardTask) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(task)?;
        let action_vars = serde_json::to_string(&task.action_vars)?;
        let labels = serde_json::to_string(&task.labels)?;
        let messages = serde_json::to_string(&task.messages)?;
        let depends_on = serde_json::to_string(&task.depends_on)?;
        let attachments = serde_json::to_string(&task.attachments)?;
        let health_details = serde_json::to_string(&task.health_details)?;
        let artifacts = serde_json::to_string(&task.artifacts)?;
        let verification_summary = serde_json::to_string(&task.verification_summary)?;
        let worktree_boundary = serde_json::to_string(&task.worktree_boundary)?;
        conn.execute(
            "INSERT OR REPLACE INTO board_tasks(
                id, task, description, slug, group_name, action_name, action_vars,
                agent_template, instructions, context, criteria, lane, position,
                agent_id, reply_agent_id, labels, created_at, updated_at,
                lane_entered_at, provider, external_id, external_url,
                parent_task_id, pipeline_depth, pipeline_root_id, status,
                scheduled_at, messages, depends_on, attachments, health_state,
                health_since, health_details, artifacts, verification_mode,
                verification_state, verification_notes, verification_updated_at,
                verification_updated_by, verification_summary, worktree_boundary,
                resume_after_boundary_task_id, archived_at, archived_from_lane, data
             ) VALUES (
                ?1, ?2, ?3, ?4, ?5, ?6, ?7,
                ?8, ?9, ?10, ?11, ?12, ?13,
                ?14, ?15, ?16, ?17, ?18,
                ?19, ?20, ?21, ?22,
                ?23, ?24, ?25, ?26,
                ?27, ?28, ?29, ?30, ?31,
                ?32, ?33, ?34, ?35,
                ?36, ?37, ?38,
                ?39, ?40, ?41,
                ?42, ?43, ?44, ?45
             )",
            params![
                task.id,
                task.task,
                task.description,
                task.slug,
                task.group,
                task.action_name,
                action_vars,
                task.agent_template,
                task.instructions,
                task.context,
                task.criteria,
                task.lane,
                task.position,
                task.agent_id,
                task.reply_agent_id,
                labels,
                task.created_at,
                task.updated_at,
                task.lane_entered_at,
                task.provider,
                task.external_id,
                task.external_url,
                task.parent_task_id,
                task.pipeline_depth,
                task.pipeline_root_id,
                task.status,
                task.scheduled_at,
                messages,
                depends_on,
                attachments,
                task.health_state,
                task.health_since,
                health_details,
                artifacts,
                task.verification_mode,
                task.verification_state,
                task.verification_notes,
                task.verification_updated_at,
                task.verification_updated_by,
                verification_summary,
                worktree_boundary,
                task.resume_after_boundary_task_id,
                task.archived_at,
                task.archived_from_lane,
                data,
            ],
        )?;
        Ok(())
    }

    pub async fn delete_board_task(&self, id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM board_tasks WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub async fn save_board_lanes(&self, lanes: &[String]) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM board_lanes", [])?;
        let mut stmt = conn.prepare("INSERT INTO board_lanes(position, name) VALUES (?1, ?2)")?;
        for (i, lane) in lanes.iter().enumerate() {
            stmt.execute(params![i as i64, lane])?;
        }
        Ok(())
    }

    pub async fn save_schedule(&self, schedule: &Schedule) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(schedule)?;
        let action_vars = serde_json::to_string(&schedule.action_vars)?;
        let labels = serde_json::to_string(&schedule.labels)?;
        conn.execute(
            "INSERT OR REPLACE INTO schedules(
                id, name, slug, task_template, description, group_name, action_name,
                action_vars, agent_template, labels, cron_expr, scheduled_at,
                timezone, enabled, last_run_at, next_run_at, run_count,
                last_task_id, created_at, updated_at, data
             ) VALUES (
                ?1, ?2, ?3, ?4, ?5, ?6, ?7,
                ?8, ?9, ?10, ?11, ?12,
                ?13, ?14, ?15, ?16, ?17,
                ?18, ?19, ?20, ?21
             )",
            params![
                schedule.id,
                schedule.name,
                schedule.slug,
                schedule.task_template,
                schedule.description,
                schedule.group,
                schedule.action_name,
                action_vars,
                schedule.agent_template,
                labels,
                schedule.cron_expr,
                schedule.scheduled_at,
                schedule.timezone,
                schedule.enabled as i64,
                schedule.last_run_at,
                schedule.next_run_at,
                schedule.run_count,
                schedule.last_task_id,
                schedule.created_at,
                schedule.updated_at,
                data,
            ],
        )?;
        Ok(())
    }

    pub async fn delete_schedule(&self, id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM schedules WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub async fn save_weaver_settings(
        &self,
        group: &str,
        settings: &WeaverSettings,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(settings)?;
        let enabled_events = serde_json::to_string(&settings.enabled_events)?;
        conn.execute(
            "INSERT OR REPLACE INTO weaver_settings(
                group_name, push_interval, max_interval, heartbeat_interval,
                default_worker_concurrency, autonomy_mode, wave_size_preference,
                same_agent_follow_up_preference, digest_verbosity, escalation_style,
                paused, custom_instructions, restrict_to_created_agents,
                pending_question, pending_note, pending_note_kind, enabled_events,
                weaver_provider, weaver_boot_command, weaver_model,
                weaver_reasoning_effort, weaver_directory, weaver_profile,
                weaver_shell, weaver_tab_color, data
             ) VALUES (
                ?1, ?2, ?3, ?4,
                ?5, ?6, ?7,
                ?8, ?9, ?10,
                ?11, ?12, ?13,
                ?14, ?15, ?16, ?17,
                ?18, ?19, ?20,
                ?21, ?22, ?23,
                ?24, ?25, ?26
             )",
            params![
                group,
                settings.push_interval,
                settings.max_interval,
                settings.heartbeat_interval,
                settings.default_worker_concurrency,
                settings.autonomy_mode,
                settings.wave_size_preference,
                settings.same_agent_follow_up_preference,
                settings.digest_verbosity,
                settings.escalation_style,
                settings.paused as i64,
                settings.custom_instructions,
                settings.restrict_to_created_agents as i64,
                settings.pending_question,
                settings.pending_note,
                settings.pending_note_kind,
                enabled_events,
                settings.weaver_provider,
                settings.weaver_boot_command,
                settings.weaver_model,
                settings.weaver_reasoning_effort,
                settings.weaver_directory,
                settings.weaver_profile,
                settings.weaver_shell,
                settings.weaver_tab_color,
                data,
            ],
        )?;
        Ok(())
    }

    pub async fn append_weaver_journal(
        &self,
        group: &str,
        entry_type: &str,
        entry: &str,
        timestamp: f64,
    ) -> crate::Result<i64> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT INTO weaver_journal(group_name, timestamp, entry_type, entry)
             VALUES (?1, ?2, ?3, ?4)",
            params![group, timestamp, entry_type, entry],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Persist a Weaver worklog entry (task dispatched under this weaver's group)
    /// and return the row id. Callers should store the id back onto the in-memory
    /// entry so WS clients can correlate updates.
    pub async fn append_weaver_worklog(
        &self,
        group: &str,
        entry_json: &str,
        timestamp: f64,
    ) -> crate::Result<i64> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT INTO weaver_worklog(group_name, entry, ts) VALUES (?1, ?2, ?3)",
            params![group, entry_json, timestamp],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Cap the number of persisted worklog rows per group. Oldest rows are
    /// deleted first so the most recent `limit` entries survive.
    pub async fn trim_weaver_worklog(&self, group: &str, limit: usize) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "DELETE FROM weaver_worklog
             WHERE group_name = ?1
               AND id NOT IN (
                 SELECT id FROM weaver_worklog
                  WHERE group_name = ?1
                  ORDER BY id DESC
                  LIMIT ?2
               )",
            params![group, limit as i64],
        )?;
        Ok(())
    }

    pub async fn delete_weaver_journal_entry(&self, group: &str, id: i64) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "DELETE FROM weaver_journal WHERE id = ?1 AND group_name = ?2",
            params![id, group],
        )?;
        Ok(())
    }

    pub async fn save_task_id_counter(
        &self,
        group_prefix: &str,
        next_root_number: u64,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO task_id_counters(group_prefix, next_root_number)
             VALUES (?1, ?2)",
            params![group_prefix, next_root_number.max(1)],
        )?;
        Ok(())
    }

    pub async fn save_pipeline_task_counter(
        &self,
        root_task_id: &str,
        next_child_number: u64,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_task_counters(root_task_id, next_child_number)
             VALUES (?1, ?2)",
            params![root_task_id, next_child_number.max(1)],
        )?;
        Ok(())
    }

    pub async fn save_task_id_alias(&self, legacy_id: &str, task_id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO task_id_aliases(legacy_id, task_id) VALUES (?1, ?2)",
            params![legacy_id, task_id],
        )?;
        Ok(())
    }

    pub async fn save_panel_event(
        &self,
        kind: &str,
        cell_id: &str,
        agent_name: &str,
        group: &str,
        message: &str,
        task_id: &str,
        timestamp: f64,
    ) -> crate::Result<serde_json::Value> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT INTO panel_events(timestamp, kind, cell_id, agent_name, group_name, message, task_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![timestamp, kind, cell_id, agent_name, group, message, task_id],
        )?;
        let id = conn.last_insert_rowid();
        Ok(serde_json::json!({
            "id": id,
            "timestamp": timestamp,
            "kind": kind,
            "cell_id": cell_id,
            "agent_name": agent_name,
            "group": group,
            "group_name": group,
            "message": message,
            "task_id": task_id,
        }))
    }

    pub async fn append_panel_event(
        &self,
        kind: &str,
        cell_id: &str,
        agent_name: &str,
        group: &str,
        message: &str,
        task_id: &str,
        timestamp: f64,
    ) -> crate::Result<i64> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT INTO panel_events(timestamp, kind, cell_id, agent_name, group_name, message, task_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![timestamp, kind, cell_id, agent_name, group, message, task_id],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub async fn find_latest_panel_event(
        &self,
        kind: &str,
        cell_id: &str,
    ) -> crate::Result<Option<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let row = conn
            .query_row(
                "SELECT id, timestamp, kind, cell_id, agent_name, group_name, message, task_id
                 FROM panel_events
                 WHERE kind = ?1 AND cell_id = ?2
                 ORDER BY id DESC
                 LIMIT 1",
                params![kind, cell_id],
                |r| {
                    Ok(serde_json::json!({
                        "id": r.get::<_, i64>(0)?,
                        "timestamp": r.get::<_, f64>(1)?,
                        "kind": r.get::<_, String>(2)?,
                        "cell_id": r.get::<_, String>(3)?,
                        "agent_name": r.get::<_, String>(4)?,
                        "group": r.get::<_, String>(5)?,
                        "group_name": r.get::<_, String>(5)?,
                        "message": r.get::<_, String>(6)?,
                        "task_id": r.get::<_, String>(7)?,
                    }))
                },
            )
            .optional()?;
        Ok(row)
    }

    pub async fn update_panel_event(
        &self,
        id: i64,
        agent_name: &str,
        group: &str,
        message: &str,
        task_id: &str,
        timestamp: f64,
    ) -> crate::Result<serde_json::Value> {
        let conn = self.inner.lock().await;
        conn.execute(
            "UPDATE panel_events
             SET timestamp = ?2, agent_name = ?3, group_name = ?4, message = ?5, task_id = ?6
             WHERE id = ?1",
            params![id, timestamp, agent_name, group, message, task_id],
        )?;
        let row = conn
            .query_row(
                "SELECT id, timestamp, kind, cell_id, agent_name, group_name, message, task_id
                 FROM panel_events
                 WHERE id = ?1",
                params![id],
                |r| {
                    Ok(serde_json::json!({
                        "id": r.get::<_, i64>(0)?,
                        "timestamp": r.get::<_, f64>(1)?,
                        "kind": r.get::<_, String>(2)?,
                        "cell_id": r.get::<_, String>(3)?,
                        "agent_name": r.get::<_, String>(4)?,
                        "group": r.get::<_, String>(5)?,
                        "group_name": r.get::<_, String>(5)?,
                        "message": r.get::<_, String>(6)?,
                        "task_id": r.get::<_, String>(7)?,
                    }))
                },
            )
            .optional()?;
        row.ok_or_else(|| crate::Error::not_found(format!("panel event '{id}'")))
    }

    pub async fn trim_panel_events(&self, max_size: usize) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "DELETE FROM panel_events
             WHERE id NOT IN (
                SELECT id FROM panel_events
                ORDER BY id DESC
                LIMIT ?1
             )",
            params![max_size.max(1) as i64],
        )?;
        Ok(())
    }

    pub async fn save_agent_history_record(
        &self,
        id: &str,
        name: &str,
        slug: &str,
        group: &str,
        agent_type: &str,
        template: &str,
        created_at: f64,
        removed_at: Option<f64>,
        worktree_branch: &str,
        total_tokens_in: i64,
        total_tokens_out: i64,
        total_tasks: i64,
        status: &str,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO agent_history
                (id, name, slug, \"group\", agent_type, template,
                 created_at, removed_at, worktree_branch,
                 total_tokens_in, total_tokens_out, total_tasks, status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                id,
                name,
                slug,
                group,
                agent_type,
                template,
                created_at,
                removed_at,
                worktree_branch,
                total_tokens_in,
                total_tokens_out,
                total_tasks,
                status,
            ],
        )?;
        Ok(())
    }

    pub async fn update_agent_history_fields(
        &self,
        agent_id: &str,
        fields: &serde_json::Map<String, serde_json::Value>,
    ) -> crate::Result<()> {
        if fields.is_empty() {
            return Ok(());
        }
        let allowed = [
            "name",
            "slug",
            "group",
            "agent_type",
            "template",
            "removed_at",
            "worktree_branch",
            "total_tokens_in",
            "total_tokens_out",
            "total_tasks",
            "status",
        ];

        let mut parts = Vec::new();
        let mut values: Vec<SqlValue> = Vec::new();
        for (key, value) in fields {
            if !allowed.contains(&key.as_str()) {
                continue;
            }
            let col = if key == "group" {
                "\"group\""
            } else {
                key.as_str()
            };
            parts.push(format!("{col} = ?"));
            let sql_value = match key.as_str() {
                "removed_at" => value.as_f64().map(SqlValue::Real).unwrap_or(SqlValue::Null),
                "total_tokens_in" | "total_tokens_out" | "total_tasks" => {
                    SqlValue::Integer(value.as_i64().unwrap_or(0))
                }
                _ => SqlValue::Text(value.as_str().unwrap_or("").to_string()),
            };
            values.push(sql_value);
        }
        if parts.is_empty() {
            return Ok(());
        }

        values.push(SqlValue::Text(agent_id.to_string()));
        let conn = self.inner.lock().await;
        conn.execute(
            &format!("UPDATE agent_history SET {} WHERE id = ?", parts.join(", ")),
            params_from_iter(values),
        )?;
        Ok(())
    }

    pub async fn save_agent_task_record(
        &self,
        agent_id: &str,
        task_id: &str,
        task_title: &str,
        started_at: f64,
        completed_at: Option<f64>,
        outcome: &str,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT INTO agent_tasks(agent_id, task_id, task_title, started_at, completed_at, outcome)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![agent_id, task_id, task_title, started_at, completed_at, outcome],
        )?;
        Ok(())
    }

    pub async fn update_agent_task(
        &self,
        agent_id: &str,
        task_id: &str,
        completed_at: Option<f64>,
        outcome: Option<&str>,
    ) -> crate::Result<()> {
        let mut parts = Vec::new();
        let mut values: Vec<SqlValue> = Vec::new();
        if let Some(value) = completed_at {
            parts.push("completed_at = ?".to_string());
            values.push(SqlValue::Real(value));
        }
        if let Some(value) = outcome {
            parts.push("outcome = ?".to_string());
            values.push(SqlValue::Text(value.to_string()));
        }
        if parts.is_empty() {
            return Ok(());
        }
        values.push(SqlValue::Text(agent_id.to_string()));
        values.push(SqlValue::Text(task_id.to_string()));
        let conn = self.inner.lock().await;
        conn.execute(
            &format!(
                "UPDATE agent_tasks SET {} WHERE agent_id = ? AND task_id = ?",
                parts.join(", ")
            ),
            params_from_iter(values),
        )?;
        Ok(())
    }

    pub async fn save_agent_message_record(
        &self,
        agent_id: &str,
        task_id: &str,
        timestamp: f64,
        action: &str,
        message: &str,
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT INTO agent_messages(agent_id, task_id, timestamp, action, message)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![agent_id, task_id, timestamp, action, message],
        )?;
        Ok(())
    }

    pub async fn save_memory_entry(&self, entry: &MemoryEntry) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(entry)?;
        conn.execute(
            "INSERT OR REPLACE INTO memory_entries(
                id, project_key, group_name, scope_kind, scope_ref, entry_type,
                task_id, pinned, retention_kind, data
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                entry.id,
                entry.project_key,
                entry.group_name,
                entry.scope_kind,
                entry.scope_ref,
                entry.entry_type,
                entry.task_id,
                entry.pinned as i64,
                entry.retention_kind,
                data,
            ],
        )?;
        Ok(())
    }

    pub async fn delete_memory_entry(&self, id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM memory_entries WHERE id = ?1", params![id])?;
        // Cascading delete via FK handles memory_links; emit explicit DELETE
        // as a belt-and-suspenders guard in case FK pragmas aren't on.
        conn.execute("DELETE FROM memory_links WHERE entry_id = ?1", params![id])?;
        Ok(())
    }

    pub async fn save_memory_link(&self, link: &MemoryLink) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR IGNORE INTO memory_links(entry_id, target_kind, target_ref, created_at)
             VALUES (?1, ?2, ?3, ?4)",
            params![
                link.entry_id,
                link.target_kind,
                link.target_ref,
                link.created_at
            ],
        )?;
        Ok(())
    }

    pub async fn delete_memory_links(&self, entry_id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "DELETE FROM memory_links WHERE entry_id = ?1",
            params![entry_id],
        )?;
        Ok(())
    }

    pub async fn set_ui_state(&self, key: &str, value: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO ui_state(key, value) VALUES (?1, ?2)",
            params![key, value],
        )?;
        Ok(())
    }

    pub async fn get_ui_state(&self, key: &str) -> crate::Result<Option<String>> {
        let conn = self.inner.lock().await;
        let val = conn
            .query_row(
                "SELECT value FROM ui_state WHERE key = ?1",
                params![key],
                |r| r.get::<_, String>(0),
            )
            .optional()?;
        Ok(val)
    }

    pub async fn load_panel_events(
        &self,
        limit: usize,
        before_id: Option<i64>,
    ) -> crate::Result<Vec<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let limit = limit.min(200) as i64;
        let mut rows = Vec::new();
        if let Some(before_id) = before_id.filter(|id| *id > 0) {
            let mut stmt = conn.prepare(
                "SELECT id, timestamp, kind, cell_id, agent_name, group_name, message, task_id
                 FROM panel_events
                 WHERE id < ?1
                 ORDER BY id DESC
                 LIMIT ?2",
            )?;
            let iter = stmt.query_map(params![before_id, limit], |r| {
                Ok(serde_json::json!({
                    "id": r.get::<_, i64>(0)?,
                    "timestamp": r.get::<_, f64>(1)?,
                    "kind": r.get::<_, String>(2)?,
                    "cell_id": r.get::<_, String>(3)?,
                    "agent_name": r.get::<_, String>(4)?,
                    "group": r.get::<_, String>(5)?,
                    "group_name": r.get::<_, String>(5)?,
                    "message": r.get::<_, String>(6)?,
                    "task_id": r.get::<_, String>(7)?,
                }))
            })?;
            for row in iter {
                rows.push(row?);
            }
        } else {
            let mut stmt = conn.prepare(
                "SELECT id, timestamp, kind, cell_id, agent_name, group_name, message, task_id
                 FROM panel_events
                 ORDER BY id DESC
                 LIMIT ?1",
            )?;
            let iter = stmt.query_map(params![limit], |r| {
                Ok(serde_json::json!({
                    "id": r.get::<_, i64>(0)?,
                    "timestamp": r.get::<_, f64>(1)?,
                    "kind": r.get::<_, String>(2)?,
                    "cell_id": r.get::<_, String>(3)?,
                    "agent_name": r.get::<_, String>(4)?,
                    "group": r.get::<_, String>(5)?,
                    "group_name": r.get::<_, String>(5)?,
                    "message": r.get::<_, String>(6)?,
                    "task_id": r.get::<_, String>(7)?,
                }))
            })?;
            for row in iter {
                rows.push(row?);
            }
        }
        rows.reverse();
        Ok(rows)
    }

    pub async fn load_group_panel_events(
        &self,
        group: &str,
        after_id: i64,
        limit: usize,
        kinds: &[String],
    ) -> crate::Result<Vec<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let mut sql = String::from(
            "SELECT id, timestamp, kind, cell_id, agent_name, group_name, message, task_id
             FROM panel_events
             WHERE group_name = ?1 AND id > ?2",
        );
        let mut params = vec![
            SqlValue::from(group.to_string()),
            SqlValue::from(after_id.max(0)),
        ];
        if !kinds.is_empty() {
            sql.push_str(" AND kind IN (");
            for idx in 0..kinds.len() {
                if idx > 0 {
                    sql.push_str(", ");
                }
                sql.push_str(&format!("?{}", idx + 3));
                params.push(SqlValue::from(kinds[idx].clone()));
            }
            sql.push(')');
        }
        sql.push_str(&format!(" ORDER BY id ASC LIMIT ?{}", params.len() + 1));
        params.push(SqlValue::from(limit.max(1).min(200) as i64));

        let mut stmt = conn.prepare(&sql)?;
        let mut rows = stmt.query(params_from_iter(params.iter()))?;
        let mut events = Vec::new();
        while let Some(row) = rows.next()? {
            events.push(serde_json::json!({
                "id": row.get::<_, i64>(0)?,
                "timestamp": row.get::<_, f64>(1)?,
                "kind": row.get::<_, String>(2)?,
                "cell_id": row.get::<_, String>(3)?,
                "agent_name": row.get::<_, String>(4)?,
                "group": row.get::<_, String>(5)?,
                "group_name": row.get::<_, String>(5)?,
                "message": row.get::<_, String>(6)?,
                "task_id": row.get::<_, String>(7)?,
            }));
        }
        Ok(events)
    }

    pub async fn load_agent_history(
        &self,
        status_filter: &str,
        limit: usize,
        offset: usize,
    ) -> crate::Result<Vec<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let limit = limit.min(200) as i64;
        let offset = offset as i64;
        let sql_base = "SELECT id, name, slug, \"group\", agent_type, template, \
                               created_at, removed_at, worktree_branch, total_tokens_in, \
                               total_tokens_out, total_tasks, status \
                        FROM agent_history";
        let order_no_filter = " ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, created_at DESC LIMIT ?1 OFFSET ?2";
        // When a status filter is present, status=?1 already occupies slot
        // 1, so LIMIT/OFFSET must move to ?2/?3. Sharing one `order` string
        // across both branches previously collided on ?1 and tripped
        // SQLite's parameter-count check (got 3, needed 2).
        let order_with_filter = " ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, created_at DESC LIMIT ?2 OFFSET ?3";
        let mut out = Vec::new();
        if status_filter.is_empty() {
            let mut stmt = conn.prepare(&(sql_base.to_string() + order_no_filter))?;
            let rows = stmt.query_map(params![limit, offset], |r| {
                Ok(serde_json::json!({
                    "id": r.get::<_, String>(0)?,
                    "name": r.get::<_, String>(1)?,
                    "slug": r.get::<_, String>(2)?,
                    "group": r.get::<_, String>(3)?,
                    "agent_type": r.get::<_, String>(4)?,
                    "template": r.get::<_, String>(5)?,
                    "created_at": r.get::<_, f64>(6)?,
                    "removed_at": r.get::<_, Option<f64>>(7)?,
                    "worktree_branch": r.get::<_, String>(8)?,
                    "total_tokens_in": r.get::<_, i64>(9)?,
                    "total_tokens_out": r.get::<_, i64>(10)?,
                    "total_tasks": r.get::<_, i64>(11)?,
                    "status": r.get::<_, String>(12)?,
                }))
            })?;
            for row in rows {
                out.push(row?);
            }
        } else {
            let sql = format!("{sql_base} WHERE status=?1{order_with_filter}");
            let mut stmt = conn.prepare(&sql)?;
            let rows = stmt.query_map(params![status_filter, limit, offset], |r| {
                Ok(serde_json::json!({
                    "id": r.get::<_, String>(0)?,
                    "name": r.get::<_, String>(1)?,
                    "slug": r.get::<_, String>(2)?,
                    "group": r.get::<_, String>(3)?,
                    "agent_type": r.get::<_, String>(4)?,
                    "template": r.get::<_, String>(5)?,
                    "created_at": r.get::<_, f64>(6)?,
                    "removed_at": r.get::<_, Option<f64>>(7)?,
                    "worktree_branch": r.get::<_, String>(8)?,
                    "total_tokens_in": r.get::<_, i64>(9)?,
                    "total_tokens_out": r.get::<_, i64>(10)?,
                    "total_tasks": r.get::<_, i64>(11)?,
                    "status": r.get::<_, String>(12)?,
                }))
            })?;
            for row in rows {
                out.push(row?);
            }
        }
        Ok(out)
    }

    pub async fn load_agent_history_detail(
        &self,
        agent_id: &str,
    ) -> crate::Result<Option<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let row = conn
            .query_row(
                "SELECT id, name, slug, \"group\", agent_type, template, created_at, removed_at,
                        worktree_branch, total_tokens_in, total_tokens_out, total_tasks, status
                 FROM agent_history
                 WHERE id = ?1",
                params![agent_id],
                |r| {
                    Ok(serde_json::json!({
                        "id": r.get::<_, String>(0)?,
                        "name": r.get::<_, String>(1)?,
                        "slug": r.get::<_, String>(2)?,
                        "group": r.get::<_, String>(3)?,
                        "agent_type": r.get::<_, String>(4)?,
                        "template": r.get::<_, String>(5)?,
                        "created_at": r.get::<_, f64>(6)?,
                        "removed_at": r.get::<_, Option<f64>>(7)?,
                        "worktree_branch": r.get::<_, String>(8)?,
                        "total_tokens_in": r.get::<_, i64>(9)?,
                        "total_tokens_out": r.get::<_, i64>(10)?,
                        "total_tasks": r.get::<_, i64>(11)?,
                        "status": r.get::<_, String>(12)?,
                    }))
                },
            )
            .optional()?;
        Ok(row)
    }

    pub async fn load_agent_tasks(&self, agent_id: &str) -> crate::Result<Vec<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let mut stmt = conn.prepare(
            "SELECT id, agent_id, task_id, task_title, started_at, completed_at, outcome
             FROM agent_tasks
             WHERE agent_id = ?1
             ORDER BY started_at DESC",
        )?;
        let rows = stmt.query_map(params![agent_id], |r| {
            Ok(serde_json::json!({
                "id": r.get::<_, i64>(0)?,
                "agent_id": r.get::<_, String>(1)?,
                "task_id": r.get::<_, String>(2)?,
                "task_title": r.get::<_, String>(3)?,
                "started_at": r.get::<_, f64>(4)?,
                "completed_at": r.get::<_, Option<f64>>(5)?,
                "outcome": r.get::<_, String>(6)?,
            }))
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    pub async fn load_agent_messages(
        &self,
        agent_id: &str,
        limit: usize,
    ) -> crate::Result<Vec<serde_json::Value>> {
        let conn = self.inner.lock().await;
        let mut stmt = conn.prepare(
            "SELECT id, agent_id, task_id, timestamp, action, message
             FROM agent_messages
             WHERE agent_id = ?1
             ORDER BY timestamp DESC
             LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![agent_id, limit.min(200) as i64], |r| {
            Ok(serde_json::json!({
                "id": r.get::<_, i64>(0)?,
                "agent_id": r.get::<_, String>(1)?,
                "task_id": r.get::<_, String>(2)?,
                "timestamp": r.get::<_, f64>(3)?,
                "action": r.get::<_, String>(4)?,
                "message": r.get::<_, String>(5)?,
            }))
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    // --- load all ----------------------------------------------------------

    pub async fn load_all(&self) -> crate::Result<MatrixState> {
        let conn = self.inner.lock().await;
        let mut state = MatrixState::new();

        // groups
        let mut groups: Vec<(String, String, i64)> = Vec::new();
        {
            let mut stmt = conn.prepare(
                "SELECT name, slug, CASE
                     WHEN position != 0 THEN position
                     ELSE ordinal
                 END AS sort_order
                 FROM groups
                 ORDER BY sort_order, name",
            )?;
            let rows = stmt.query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, i64>(2)?,
                ))
            })?;
            for row in rows {
                groups.push(row?);
            }
        }
        for (name, slug, _ord) in groups {
            state.groups.insert(name.clone(), Vec::new());
            state.groups_order.push(name.clone());
            state.group_slugs.insert(name, slug);
        }

        // group members
        {
            let mut stmt = conn.prepare(
                "SELECT group_name, agent_id FROM group_members ORDER BY group_name, position",
            )?;
            let rows =
                stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for row in rows {
                let (group, aid) = row?;
                state.groups.entry(group).or_default().push(aid);
            }
        }

        // agents
        {
            let mut stmt = conn.prepare("SELECT * FROM agents")?;
            let rows = stmt.query_map([], |r| Ok(decode_agent_row(r)))?;
            for row in rows {
                let agent = row?;
                if !agent.parent_id.is_empty() {
                    state
                        .children
                        .entry(agent.parent_id.clone())
                        .or_default()
                        .push(agent.id.clone());
                }
                state.agents.insert(agent.id.clone(), agent);
            }
        }

        // group settings
        {
            let mut stmt = conn.prepare("SELECT * FROM group_settings")?;
            let rows = stmt.query_map([], |r| {
                Ok((
                    r.get::<_, String>("group_name")?,
                    decode_group_settings_row(r),
                ))
            })?;
            for row in rows {
                let (name, settings) = row?;
                state.group_settings.insert(name, settings);
            }
        }

        // global settings
        {
            if column_exists(&conn, "global_settings", "key")? {
                let mut payload = serde_json::Map::new();
                let mut stmt = conn.prepare("SELECT key, value FROM global_settings")?;
                let rows =
                    stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
                for row in rows {
                    let (key, value) = row?;
                    let parsed = serde_json::from_str::<serde_json::Value>(&value)
                        .unwrap_or_else(|_| serde_json::Value::String(value));
                    payload.insert(key, parsed);
                }
                if !payload.is_empty() {
                    if let Ok(settings) =
                        serde_json::from_value::<GlobalSettings>(serde_json::Value::Object(payload))
                    {
                        state.global_settings = settings;
                    }
                }
            } else {
                let row: Option<String> = conn
                    .query_row("SELECT data FROM global_settings WHERE id = 1", [], |r| {
                        r.get::<_, String>(0)
                    })
                    .optional()?;
                if let Some(data) = row {
                    if let Ok(s) = serde_json::from_str::<GlobalSettings>(&data) {
                        state.global_settings = s;
                    }
                }
            }
        }

        // board lanes
        {
            let mut stmt = conn.prepare("SELECT name FROM board_lanes ORDER BY position")?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
            let mut lanes: Vec<String> = Vec::new();
            for r in rows {
                lanes.push(r?);
            }
            if !lanes.is_empty() {
                state.board_lanes = lanes;
            }
        }

        // board tasks
        {
            let mut stmt = conn.prepare("SELECT * FROM board_tasks")?;
            let rows = stmt.query_map([], |r| Ok(decode_board_task_row(r)))?;
            for r in rows {
                let task = r?;
                state
                    .tasks_by_group
                    .entry(task.group.clone())
                    .or_default()
                    .insert(task.id.clone());
                state.board_tasks.insert(task.id.clone(), task);
            }
        }

        // schedules
        {
            let mut stmt = conn.prepare("SELECT * FROM schedules")?;
            let rows = stmt.query_map([], |r| Ok(decode_schedule_row(r)))?;
            for r in rows {
                let schedule = r?;
                state.schedules.insert(schedule.id.clone(), schedule);
            }
        }

        // auto dispatch queues
        {
            let mut stmt = conn.prepare(
                "SELECT group_name, position, task_id, agent_group, max_concurrent,
                        target_agent_id, weaver_owner_id, enqueued_at
                 FROM auto_dispatch_queue
                 ORDER BY group_name, position",
            )?;
            let rows = stmt.query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    crate::state::AutoDispatchQueueEntry {
                        task_id: r.get::<_, String>(2)?,
                        agent_group: r.get::<_, String>(3)?,
                        max_concurrent: r.get::<_, i32>(4)?,
                        target_agent_id: r.get::<_, String>(5)?,
                        weaver_owner_id: r.get::<_, String>(6)?,
                        enqueued_at: r.get::<_, String>(7)?,
                    },
                ))
            })?;
            for row in rows {
                let (group, entry) = row?;
                state
                    .auto_dispatch_queues
                    .entry(group)
                    .or_default()
                    .push(entry);
            }
        }

        // weaver settings
        {
            let mut stmt = conn.prepare("SELECT * FROM weaver_settings")?;
            let rows = stmt.query_map([], |r| {
                Ok((
                    r.get::<_, String>("group_name")?,
                    decode_weaver_settings_row(r),
                ))
            })?;
            for r in rows {
                let (group, settings) = r?;
                state.weaver_settings.insert(group, settings);
            }
        }
        {
            let mut stmt = conn.prepare(
                "SELECT group_name, entry
                 FROM weaver_worklog
                 ORDER BY group_name, id DESC",
            )?;
            let rows =
                stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for row in rows {
                let (group, entry) = row?;
                let parsed = serde_json::from_str::<serde_json::Value>(&entry)
                    .unwrap_or_else(|_| serde_json::json!({}));
                state.weaver_worklog.entry(group).or_default().push(parsed);
            }
        }
        {
            let mut stmt = conn.prepare(
                "SELECT id, group_name, timestamp, entry_type, entry
                 FROM weaver_journal
                 ORDER BY group_name, id ASC",
            )?;
            let rows = stmt.query_map([], |r| {
                Ok(serde_json::json!({
                    "id": r.get::<_, i64>(0)?,
                    "group": r.get::<_, String>(1)?,
                    "timestamp": r.get::<_, f64>(2)?,
                    "type": r.get::<_, String>(3)?,
                    "entry": r.get::<_, String>(4)?,
                }))
            })?;
            for row in rows {
                let entry = row?;
                let group = entry
                    .get("group")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                if !group.is_empty() {
                    state.weaver_journal.entry(group).or_default().push(entry);
                }
            }
        }

        // task-id metadata
        {
            let mut stmt = conn.prepare("SELECT legacy_id, task_id FROM task_id_aliases")?;
            let rows =
                stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for row in rows {
                let (legacy_id, task_id) = row?;
                if !legacy_id.is_empty() && !task_id.is_empty() {
                    state.task_id_aliases.insert(legacy_id, task_id);
                }
            }
        }
        {
            let mut stmt =
                conn.prepare("SELECT group_prefix, next_root_number FROM task_id_counters")?;
            let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, u64>(1)?)))?;
            for row in rows {
                let (prefix, next_root) = row?;
                if !prefix.is_empty() {
                    state.task_id_counters.insert(prefix, next_root);
                }
            }
        }
        {
            let mut stmt =
                conn.prepare("SELECT root_task_id, next_child_number FROM pipeline_task_counters")?;
            let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, u64>(1)?)))?;
            for row in rows {
                let (root_task_id, next_child) = row?;
                if !root_task_id.is_empty() {
                    state
                        .pipeline_task_counters
                        .insert(root_task_id, next_child);
                }
            }
        }

        // memory entries + denormalized links
        {
            // Pull links first so we can slot them into each entry as we go.
            let mut links_by_entry: std::collections::HashMap<String, Vec<MemoryLink>> =
                Default::default();
            {
                let mut stmt = conn.prepare(
                    "SELECT entry_id, target_kind, target_ref, created_at FROM memory_links",
                )?;
                let rows = stmt.query_map([], |r| {
                    Ok(MemoryLink {
                        entry_id: r.get::<_, String>(0)?,
                        target_kind: r.get::<_, String>(1)?,
                        target_ref: r.get::<_, String>(2)?,
                        created_at: r.get::<_, String>(3)?,
                    })
                })?;
                for row in rows {
                    let link = row?;
                    links_by_entry
                        .entry(link.entry_id.clone())
                        .or_default()
                        .push(link);
                }
            }

            let mut stmt = conn.prepare("SELECT data FROM memory_entries")?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
            for r in rows {
                let data = r?;
                match serde_json::from_str::<MemoryEntry>(&data) {
                    Ok(mut entry) => {
                        if let Some(links) = links_by_entry.remove(&entry.id) {
                            entry.links = links;
                        }
                        state.memory_entries.insert(entry.id.clone(), entry);
                    }
                    Err(err) => tracing::warn!("failed to deserialize memory row: {err}"),
                }
            }
        }

        // ui_state — restore the handful of keys the UI cares about.
        {
            let sel: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'selected_agent_id'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(v) = sel {
                // only restore if the agent still exists — otherwise silently drop
                if state.agents.contains_key(&v) {
                    state.selected_agent_id = Some(v);
                }
            }
            let panel: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'panel_active'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(v) = panel {
                state.panel_active = v;
            }
            let board_panel_height: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'board_panel_height'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(v) = board_panel_height {
                if let Ok(parsed) = v.parse::<i32>() {
                    state.board_panel_height = parsed;
                }
            }
            let standalone_panel_layout: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'standalone_panel_layout'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(v) = standalone_panel_layout {
                if !v.is_empty() {
                    if let Ok(layout) = serde_json::from_str::<serde_json::Value>(&v) {
                        state.standalone_panel_layout = layout;
                    }
                }
            }
            let events_dismissed_attention: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'events_dismissed_attention'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(v) = events_dismissed_attention {
                if let Ok(map) = serde_json::from_str::<std::collections::HashMap<String, f64>>(&v)
                {
                    state.events_dismissed_attention = map;
                }
            }

            let layout: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'content_layout'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(s) = layout {
                if !s.is_empty() {
                    if let Ok(node) = serde_json::from_str::<crate::state::LayoutNode>(&s) {
                        state.content_layout = Some(node);
                    }
                }
            }

            // Dock edges (Top/Left/Right/Bottom + ratios). If not persisted
            // yet (legacy install pre-dock-system), the default from
            // `MatrixState::new()` already sets Left=Sidebar + Bottom=Board.
            let edges: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'dock_edges'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(s) = edges {
                if !s.is_empty() {
                    if let Ok(e) = serde_json::from_str::<crate::state::DockEdges>(&s) {
                        state.dock_edges = e;
                    }
                }
            }

            // Per-group board view state — each is a JSON-encoded map.
            for (key, target) in [
                ("board_filters_by_group", "filters"),
                ("board_saved_views_by_group", "views"),
                ("board_lane_sorts_by_group", "sorts"),
            ] {
                let row: Option<String> = conn
                    .query_row(
                        "SELECT value FROM ui_state WHERE key = ?1",
                        rusqlite::params![key],
                        |r| r.get::<_, String>(0),
                    )
                    .optional()?;
                if let Some(s) = row {
                    if let Ok(map) = serde_json::from_str::<
                        std::collections::HashMap<String, serde_json::Value>,
                    >(&s)
                    {
                        match target {
                            "filters" => state.board_filters_by_group = map,
                            "views" => state.board_saved_views_by_group = map,
                            "sorts" => state.board_lane_sorts_by_group = map,
                            _ => {}
                        }
                    }
                }
            }
            // Card density is String, not Value.
            let row: Option<String> = conn
                .query_row(
                    "SELECT value FROM ui_state WHERE key = 'board_card_density_by_group'",
                    [],
                    |r| r.get::<_, String>(0),
                )
                .optional()?;
            if let Some(s) = row {
                if let Ok(map) =
                    serde_json::from_str::<std::collections::HashMap<String, String>>(&s)
                {
                    state.board_card_density_by_group = map;
                }
            }
        }

        Ok(state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::{AgentCell, Schedule};

    #[tokio::test]
    async fn open_creates_schema() {
        let db = LoomDb::in_memory().unwrap();
        // round-trip a global settings write
        let gs = GlobalSettings::default();
        db.save_global_settings(&gs).await.unwrap();
        let state = db.load_all().await.unwrap();
        assert!(state.global_settings.filter_by_window);
    }

    #[tokio::test]
    async fn save_and_load_agent_clears_ephemeral() {
        let db = LoomDb::in_memory().unwrap();
        db.save_group("Eng", "eng", 0).await.unwrap();
        let mut a = AgentCell::new("a1", "Worker", "Eng");
        a.activity = "thinking".into();
        a.slug = "eng:worker".into();
        db.save_agent(&a).await.unwrap();
        db.save_group_members("Eng", &["a1".into()]).await.unwrap();
        let state = db.load_all().await.unwrap();
        let a2 = state.agents.get("a1").expect("agent present");
        assert_eq!(a2.name, "Worker");
        assert_eq!(a2.activity, ""); // ephemeral cleared
    }

    #[tokio::test]
    async fn save_and_load_task() {
        let db = LoomDb::in_memory().unwrap();
        let mut t = BoardTask::new_minimal("eng-1", "Task");
        t.group = "Eng".into();
        t.slug = "task".into();
        db.save_board_task(&t).await.unwrap();
        let state = db.load_all().await.unwrap();
        assert_eq!(state.board_tasks["eng-1"].task, "Task");
        assert!(state.tasks_by_group.get("Eng").unwrap().contains("eng-1"));
    }

    #[tokio::test]
    async fn delete_agent_removes_row() {
        let db = LoomDb::in_memory().unwrap();
        let a = AgentCell::new("a1", "w", "g");
        db.save_agent(&a).await.unwrap();
        db.delete_agent("a1").await.unwrap();
        let state = db.load_all().await.unwrap();
        assert!(!state.agents.contains_key("a1"));
    }

    #[tokio::test]
    async fn save_methods_populate_python_compat_columns() {
        let db = LoomDb::in_memory().unwrap();
        db.save_group("Eng", "eng", 2).await.unwrap();

        let mut agent = AgentCell::new("a1", "Worker", "Eng");
        agent.status = "running".into();
        agent.worktree_path = "/tmp/eng".into();
        agent.tasks_dispatched = 4;
        db.save_agent(&agent).await.unwrap();

        let mut task = BoardTask::new_minimal("eng-1", "Ship it");
        task.group = "Eng".into();
        task.labels = vec!["rust".into()];
        task.depends_on = vec!["eng-0".into()];
        db.save_board_task(&task).await.unwrap();

        let schedule = Schedule {
            id: "sched-1".into(),
            name: "Nightly".into(),
            slug: String::new(),
            task_template: "Nightly".into(),
            description: String::new(),
            group: "Eng".into(),
            action_name: String::new(),
            action_vars: serde_json::Map::new(),
            agent_template: String::new(),
            labels: vec!["nightly".into()],
            cron_expr: "0 0 * * *".into(),
            scheduled_at: String::new(),
            timezone: "UTC".into(),
            enabled: true,
            last_run_at: String::new(),
            next_run_at: String::new(),
            run_count: 0,
            last_task_id: String::new(),
            created_at: String::new(),
            updated_at: String::new(),
        };
        db.save_schedule(&schedule).await.unwrap();

        let mut gs = GlobalSettings::default();
        gs.default_command = "codex".into();
        db.save_global_settings(&gs).await.unwrap();

        let conn = db.inner.lock().await;
        let (position, ordinal): (i64, i64) = conn
            .query_row(
                "SELECT position, ordinal FROM groups WHERE name = 'Eng'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(position, 2);
        assert_eq!(ordinal, 2);

        let (status, worktree_path, tasks_dispatched): (String, String, i64) = conn
            .query_row(
                "SELECT status, worktree_path, tasks_dispatched FROM agents WHERE id = 'a1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(status, "running");
        assert_eq!(worktree_path, "/tmp/eng");
        assert_eq!(tasks_dispatched, 4);

        let (title, labels, depends_on): (String, String, String) = conn
            .query_row(
                "SELECT task, labels, depends_on FROM board_tasks WHERE id = 'eng-1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(title, "Ship it");
        assert_eq!(labels, "[\"rust\"]");
        assert_eq!(depends_on, "[\"eng-0\"]");

        let (name, enabled): (String, i64) = conn
            .query_row(
                "SELECT name, enabled FROM schedules WHERE id = 'sched-1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(name, "Nightly");
        assert_eq!(enabled, 1);

        let rows: i64 = conn
            .query_row("SELECT COUNT(*) FROM global_settings", [], |r| r.get(0))
            .unwrap();
        assert!(rows >= 8);
    }

    #[tokio::test]
    async fn load_all_accepts_python_style_rows_without_blob_data() {
        let db = LoomDb::in_memory().unwrap();
        {
            let conn = db.inner.lock().await;
            conn.execute(
                "INSERT INTO groups(name, slug, position) VALUES ('Eng', 'eng', 0)",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO group_members(group_name, agent_id, position) VALUES ('Eng', 'a1', 0)",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO agents(
                    id, name, slug, group_name, terminal_backend, status, session_resume
                 ) VALUES ('a1', 'Worker', 'eng-worker', 'Eng', 'iterm2', 'running', 1)",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO group_settings(
                    group_name, env_vars, board_default_labels, dispatch_lane
                 ) VALUES ('Eng', '{\"RUST_LOG\":\"debug\"}', '[\"backend\"]', 'In Progress')",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO global_settings(key, value) VALUES
                    ('default_command', '\"codex\"'),
                    ('filter_by_window', 'false')",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO board_tasks(
                    id, task, group_name, lane, labels, action_vars, messages, depends_on,
                    verification_summary, worktree_boundary
                 ) VALUES (
                    'eng-1', 'Ship it', 'Eng', 'Backlog', '[\"rust\"]', '{}', '[]', '[\"eng-0\"]', '{}', '{}'
                 )",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO schedules(
                    id, name, group_name, labels, action_vars, enabled
                 ) VALUES ('sched-1', 'Nightly', 'Eng', '[\"nightly\"]', '{}', 0)",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO task_id_aliases(legacy_id, task_id) VALUES ('1', 'eng-1')",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO task_id_counters(group_prefix, next_root_number) VALUES ('eng', 7)",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO pipeline_task_counters(root_task_id, next_child_number)
                 VALUES ('eng-1', 3)",
                [],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO auto_dispatch_queue(
                    group_name, position, task_id, agent_group, max_concurrent,
                    target_agent_id, weaver_owner_id, enqueued_at
                 ) VALUES ('Eng', 0, 'eng-1', 'Eng', 2, 'a1', 'weaver-1', '2026-04-15T00:00:00Z')",
                [],
            )
            .unwrap();
        }

        let state = db.load_all().await.unwrap();
        assert_eq!(state.groups_order, vec!["Eng".to_string()]);
        assert_eq!(state.agents["a1"].status, "running");
        assert_eq!(
            state.group_settings["Eng"]
                .env_vars
                .get("RUST_LOG")
                .map(String::as_str),
            Some("debug")
        );
        assert_eq!(state.global_settings.default_command, "codex");
        assert!(!state.global_settings.filter_by_window);
        assert_eq!(state.board_tasks["eng-1"].task, "Ship it");
        assert_eq!(state.board_tasks["eng-1"].labels, vec!["rust".to_string()]);
        assert_eq!(
            state.board_tasks["eng-1"].depends_on,
            vec!["eng-0".to_string()]
        );
        assert!(!state.schedules["sched-1"].enabled);
        assert_eq!(state.task_id_aliases["1"], "eng-1");
        assert_eq!(state.task_id_counters["eng"], 7);
        assert_eq!(state.pipeline_task_counters["eng-1"], 3);
        assert_eq!(state.auto_dispatch_queues["Eng"][0].task_id, "eng-1");
        assert_eq!(state.auto_dispatch_queues["Eng"][0].max_concurrent, 2);
    }
}
