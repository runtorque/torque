"""Schema DDL and migration helpers for LoomDB."""

import json
import sqlite3

SCHEMA_VERSION = "1"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    slug                  TEXT NOT NULL DEFAULT '',
    group_name            TEXT NOT NULL,
    cell_type             TEXT NOT NULL DEFAULT 'agent',
    terminal_backend      TEXT NOT NULL DEFAULT 'iterm2',
    session_id            TEXT,
    profile               TEXT NOT NULL DEFAULT 'Default',
    command               TEXT NOT NULL DEFAULT '',
    directory             TEXT NOT NULL DEFAULT '',
    tab_color             TEXT NOT NULL DEFAULT '',
    icon                  TEXT NOT NULL DEFAULT '',
    template              TEXT NOT NULL DEFAULT '',
    window_id             TEXT NOT NULL DEFAULT '',
    parent_id             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'stopped',
    worktree_path         TEXT NOT NULL DEFAULT '',
    worktree_branch       TEXT NOT NULL DEFAULT '',
    worktree_repo_root    TEXT NOT NULL DEFAULT '',
    worktree_base_dir     TEXT NOT NULL DEFAULT '.loom/worktrees',
    worktree_base_branch  TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint INTEGER NOT NULL DEFAULT 0,
    checkpoint_on_progress   INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash INTEGER NOT NULL DEFAULT 1,
    agent_type            TEXT NOT NULL DEFAULT '',
    agent_session_id      TEXT NOT NULL DEFAULT '',
    session_resume        INTEGER NOT NULL DEFAULT 1,
    idle_timeout          INTEGER NOT NULL DEFAULT 5,
    tasks_dispatched      INTEGER NOT NULL DEFAULT 0,
    created_by_weaver_id  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS groups (
    name     TEXT PRIMARY KEY,
    slug     TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS group_members (
    group_name TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_name, agent_id)
);

CREATE TABLE IF NOT EXISTS group_settings (
    group_name                  TEXT PRIMARY KEY,
    default_directory           TEXT NOT NULL DEFAULT '',
    default_terminal_backend    TEXT NOT NULL DEFAULT 'iterm2',
    profile                     TEXT NOT NULL DEFAULT '',
    shell                       TEXT NOT NULL DEFAULT '',
    tab_color                   TEXT NOT NULL DEFAULT '',
    env_vars                    TEXT NOT NULL DEFAULT '{}',
    env_file                    TEXT NOT NULL DEFAULT '',
    auto_terminals              INTEGER NOT NULL DEFAULT 0,
    max_agents                  INTEGER NOT NULL DEFAULT 0,
    collapsed_default           INTEGER NOT NULL DEFAULT 0,
    filter_by_window            INTEGER NOT NULL DEFAULT 0,
    agent_directory             TEXT NOT NULL DEFAULT '',
    agent_profile               TEXT NOT NULL DEFAULT '',
    agent_shell                 TEXT NOT NULL DEFAULT '',
    agent_tab_color             TEXT NOT NULL DEFAULT '',
    agent_env_vars              TEXT NOT NULL DEFAULT '{}',
    agent_env_file              TEXT NOT NULL DEFAULT '',
    default_agent_template      TEXT NOT NULL DEFAULT '',
    agent_provider              TEXT NOT NULL DEFAULT '',
    agent_boot_command          TEXT NOT NULL DEFAULT '',
    agent_model                 TEXT NOT NULL DEFAULT '',
    agent_reasoning_effort      TEXT NOT NULL DEFAULT '',
    git_worktree                INTEGER NOT NULL DEFAULT 0,
    worktree_base_dir           TEXT NOT NULL DEFAULT '.loom/worktrees',
    worktree_base_branch        TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint    INTEGER NOT NULL DEFAULT 0,
    checkpoint_on_progress      INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash       INTEGER NOT NULL DEFAULT 1,
    worktree_merge_instructions TEXT NOT NULL DEFAULT '',
    worktree_merge_cleanup      TEXT NOT NULL DEFAULT 'keep',
    worktree_merge_preserve_diff INTEGER NOT NULL DEFAULT 0,
    worktree_symlinks           TEXT NOT NULL DEFAULT '[]',
    agent_session_resume        INTEGER NOT NULL DEFAULT 1,
    agent_idle_timeout          INTEGER NOT NULL DEFAULT 5,
    agent_always_custom_dialog  INTEGER NOT NULL DEFAULT 0,
    notifications               INTEGER NOT NULL DEFAULT 0,
    notify_on_finish            INTEGER NOT NULL DEFAULT 1,
    notify_on_error             INTEGER NOT NULL DEFAULT 1,
    notify_on_attention         INTEGER NOT NULL DEFAULT 1,
    terminal_name_prefix        TEXT NOT NULL DEFAULT '',
    terminal_boot_command       TEXT NOT NULL DEFAULT '',
    terminal_command_args       TEXT NOT NULL DEFAULT '',
    terminal_init_script        TEXT NOT NULL DEFAULT '',
    terminal_directory          TEXT NOT NULL DEFAULT '',
    terminal_profile            TEXT NOT NULL DEFAULT '',
    terminal_shell              TEXT NOT NULL DEFAULT '',
    terminal_tab_color          TEXT NOT NULL DEFAULT '',
    terminal_env_vars           TEXT NOT NULL DEFAULT '{}',
    terminal_env_file           TEXT NOT NULL DEFAULT '',
    terminal_always_custom_dialog INTEGER NOT NULL DEFAULT 0,
    terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0,
    dispatch_lane               TEXT NOT NULL DEFAULT 'In Progress',
    dispatch_auto_terminals     INTEGER NOT NULL DEFAULT 0,
    board_default_labels        TEXT NOT NULL DEFAULT '[]',
    board_default_lane          TEXT NOT NULL DEFAULT '',
    board_default_action        TEXT NOT NULL DEFAULT '',
    weaver_agent_id             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS board_tasks (
    id             TEXT PRIMARY KEY,
    task           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    slug           TEXT NOT NULL DEFAULT '',
    group_name     TEXT NOT NULL DEFAULT '',
    action_name    TEXT NOT NULL DEFAULT '',
    action_vars    TEXT NOT NULL DEFAULT '{}',
    agent_template TEXT NOT NULL DEFAULT '',
    instructions   TEXT NOT NULL DEFAULT '',
    context        TEXT NOT NULL DEFAULT '',
    criteria       TEXT NOT NULL DEFAULT '',
    lane           TEXT NOT NULL DEFAULT 'Backlog',
    position       INTEGER NOT NULL DEFAULT 0,
    agent_id       TEXT NOT NULL DEFAULT '',
    reply_agent_id TEXT NOT NULL DEFAULT '',
    labels         TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    lane_entered_at TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL DEFAULT '',
    external_id    TEXT NOT NULL DEFAULT '',
    external_url   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '',
    attachments    TEXT NOT NULL DEFAULT '[]',
    health_state   TEXT NOT NULL DEFAULT 'healthy',
    health_since   TEXT NOT NULL DEFAULT '',
    health_details TEXT NOT NULL DEFAULT '{}',
    artifacts      TEXT NOT NULL DEFAULT '[]',
    verification_mode TEXT NOT NULL DEFAULT '',
    verification_state TEXT NOT NULL DEFAULT '',
    verification_notes TEXT NOT NULL DEFAULT '',
    verification_updated_at TEXT NOT NULL DEFAULT '',
    verification_updated_by TEXT NOT NULL DEFAULT '',
    verification_summary TEXT NOT NULL DEFAULT '{}',
    worktree_boundary TEXT NOT NULL DEFAULT '{}',
    resume_after_boundary_task_id TEXT NOT NULL DEFAULT '',
    archived_at    TEXT NOT NULL DEFAULT '',
    archived_from_lane TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS schedules (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL DEFAULT '',
    task_template   TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    group_name      TEXT NOT NULL DEFAULT '',
    action_name     TEXT NOT NULL DEFAULT '',
    action_vars     TEXT NOT NULL DEFAULT '{}',
    agent_template  TEXT NOT NULL DEFAULT '',
    labels          TEXT NOT NULL DEFAULT '[]',
    cron_expr       TEXT NOT NULL DEFAULT '',
    scheduled_at    TEXT NOT NULL DEFAULT '',
    timezone        TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT NOT NULL DEFAULT '',
    next_run_at     TEXT NOT NULL DEFAULT '',
    run_count       INTEGER NOT NULL DEFAULT 0,
    last_task_id    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS board_lanes (
    name     TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ui_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS panel_events (
    id         INTEGER PRIMARY KEY,
    timestamp  REAL    NOT NULL,
    kind       TEXT    NOT NULL,
    cell_id    TEXT    NOT NULL DEFAULT '',
    agent_name TEXT    NOT NULL DEFAULT '',
    group_name TEXT    NOT NULL DEFAULT '',
    message    TEXT    NOT NULL DEFAULT '',
    task_id    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_panel_events_ts ON panel_events (timestamp);

CREATE TABLE IF NOT EXISTS agent_history (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    slug              TEXT DEFAULT '',
    "group"           TEXT DEFAULT '',
    agent_type        TEXT DEFAULT '',
    template          TEXT DEFAULT '',
    created_at        REAL NOT NULL,
    removed_at        REAL,
    worktree_branch   TEXT DEFAULT '',
    total_tokens_in   INTEGER DEFAULT 0,
    total_tokens_out  INTEGER DEFAULT 0,
    total_tasks       INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    task_title  TEXT NOT NULL,
    started_at  REAL NOT NULL,
    completed_at REAL,
    outcome     TEXT DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agent_history(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent ON agent_tasks (agent_id);

CREATE TABLE IF NOT EXISTS agent_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    task_id     TEXT DEFAULT '',
    timestamp   REAL NOT NULL,
    action      TEXT NOT NULL,
    message     TEXT DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agent_history(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_agent ON agent_messages (agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_task ON agent_messages (task_id);

CREATE TABLE IF NOT EXISTS weaver_settings (
    group_name         TEXT PRIMARY KEY,
    push_interval      INTEGER NOT NULL DEFAULT 60,
    max_interval       INTEGER NOT NULL DEFAULT 300,
    heartbeat_interval INTEGER NOT NULL DEFAULT 300,
    default_worker_concurrency INTEGER NOT NULL DEFAULT 2,
    autonomy_mode      TEXT NOT NULL DEFAULT 'dispatch_when_clear',
    wave_size_preference TEXT NOT NULL DEFAULT 'small',
    same_agent_follow_up_preference TEXT NOT NULL DEFAULT 'balanced',
    digest_verbosity   TEXT NOT NULL DEFAULT 'balanced',
    escalation_style   TEXT NOT NULL DEFAULT 'note_then_ask',
    paused             INTEGER NOT NULL DEFAULT 0,
    custom_instructions TEXT NOT NULL DEFAULT '',
    restrict_to_created_agents INTEGER NOT NULL DEFAULT 0,
    pending_question   TEXT NOT NULL DEFAULT '',
    pending_note       TEXT NOT NULL DEFAULT '',
    pending_note_kind  TEXT NOT NULL DEFAULT '',
    enabled_events     TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived"]',
    weaver_provider    TEXT NOT NULL DEFAULT '',
    weaver_boot_command TEXT NOT NULL DEFAULT '',
    weaver_model       TEXT NOT NULL DEFAULT '',
    weaver_reasoning_effort TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS weaver_journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name  TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    entry_type  TEXT NOT NULL,
    entry       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weaver_journal_group
    ON weaver_journal(group_name, id DESC);

CREATE TABLE IF NOT EXISTS playbook_candidates (
    id                     TEXT PRIMARY KEY,
    group_name             TEXT NOT NULL DEFAULT '',
    family_key             TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'draft',
    created_at             REAL NOT NULL,
    updated_at             REAL NOT NULL,
    name                   TEXT NOT NULL DEFAULT '',
    root_action            TEXT NOT NULL DEFAULT '',
    labels                 TEXT NOT NULL DEFAULT '[]',
    normalized_task_family TEXT NOT NULL DEFAULT '',
    entry_action           TEXT NOT NULL DEFAULT '',
    agent_template         TEXT NOT NULL DEFAULT '',
    workflow               TEXT NOT NULL DEFAULT '[]',
    workflow_shape         TEXT NOT NULL DEFAULT '[]',
    dispatch_sequence      TEXT NOT NULL DEFAULT '[]',
    action_combination     TEXT NOT NULL DEFAULT '[]',
    constraints            TEXT NOT NULL DEFAULT '{}',
    evidence               TEXT NOT NULL DEFAULT '{}',
    supporting_runs        TEXT NOT NULL DEFAULT '[]',
    counterexamples        TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_playbook_candidates_group
    ON playbook_candidates(group_name, updated_at DESC);

CREATE TABLE IF NOT EXISTS playbooks (
    id                  TEXT PRIMARY KEY,
    group_name          TEXT NOT NULL DEFAULT '',
    source_candidate_id TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft',
    generated           INTEGER NOT NULL DEFAULT 1,
    review_required     INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    published_at        REAL,
    discarded_at        REAL,
    name                TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    match_data          TEXT NOT NULL DEFAULT '{}',
    entry_action        TEXT NOT NULL DEFAULT '',
    agent_template      TEXT NOT NULL DEFAULT '',
    workflow            TEXT NOT NULL DEFAULT '[]',
    constraints         TEXT NOT NULL DEFAULT '{}',
    evidence            TEXT NOT NULL DEFAULT '{}',
    publication_preview TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_playbooks_group_status
    ON playbooks(group_name, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS auto_dispatch_queue (
    group_name       TEXT NOT NULL,
    position         INTEGER NOT NULL DEFAULT 0,
    task_id          TEXT NOT NULL,
    agent_group      TEXT NOT NULL DEFAULT '',
    max_concurrent   INTEGER NOT NULL DEFAULT 1,
    target_agent_id  TEXT NOT NULL DEFAULT '',
    weaver_owner_id  TEXT NOT NULL DEFAULT '',
    enqueued_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_name, position)
);
CREATE INDEX IF NOT EXISTS idx_auto_dispatch_queue_group
    ON auto_dispatch_queue(group_name, position);

CREATE TABLE IF NOT EXISTS task_id_counters (
    group_prefix     TEXT PRIMARY KEY,
    next_root_number INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pipeline_task_counters (
    root_task_id       TEXT PRIMARY KEY,
    next_child_number  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task_id_aliases (
    legacy_id TEXT PRIMARY KEY,
    task_id   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_id_aliases_task
    ON task_id_aliases(task_id);

CREATE TABLE IF NOT EXISTS memory_entries (
    id          TEXT PRIMARY KEY,
    project_key TEXT NOT NULL DEFAULT '',
    group_name  TEXT NOT NULL DEFAULT '',
    scope_kind  TEXT NOT NULL,
    scope_ref   TEXT NOT NULL DEFAULT '',
    entry_type  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    pinned      INTEGER NOT NULL DEFAULT 0,
    task_id     TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    source_id   TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    retention_kind TEXT NOT NULL DEFAULT 'durable',
    expires_at  REAL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_entries_scope
    ON memory_entries(scope_kind, scope_ref, pinned, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_group
    ON memory_entries(group_name, pinned, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_project
    ON memory_entries(project_key, pinned, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_type
    ON memory_entries(entry_type, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(entry_id, target_kind, target_ref),
    FOREIGN KEY(entry_id) REFERENCES memory_entries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_memory_links_entry
    ON memory_links(entry_id, target_kind, target_ref);
CREATE INDEX IF NOT EXISTS idx_memory_links_target
    ON memory_links(target_kind, target_ref, created_at DESC, entry_id);
"""

def initialize_database(conn: sqlite3.Connection, backfill_agent_history):
    """Create tables and apply in-place SQLite migrations."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    # Migrate: add slug columns to existing tables
    for table in ("agents", "groups", "board_tasks"):
        try:
            conn.execute(f"SELECT slug FROM {table} LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN slug "
                f"TEXT NOT NULL DEFAULT ''")
            conn.commit()
    # Migrate: add dispatch_auto_terminals column
    try:
        conn.execute(
            "SELECT dispatch_auto_terminals FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "dispatch_auto_terminals INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migrate: add agent_provider column
    try:
        conn.execute(
            "SELECT agent_provider FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "agent_provider TEXT NOT NULL DEFAULT ''")
        conn.commit()
    for col in ("agent_model", "agent_reasoning_effort"):
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
    # Migrate: add terminal_close_on_disconnect column
    try:
        conn.execute(
            "SELECT terminal_close_on_disconnect FROM group_settings "
            "LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migrate: add memory retention columns
    try:
        conn.execute(
            "SELECT retention_kind FROM memory_entries LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE memory_entries ADD COLUMN "
            "retention_kind TEXT NOT NULL DEFAULT 'durable'")
        conn.commit()
    try:
        conn.execute(
            "SELECT expires_at FROM memory_entries LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE memory_entries ADD COLUMN expires_at REAL")
        conn.commit()
    rebuild_memory_retention_indexes(conn)
    # Migrate: add action_name and action_vars columns to board_tasks
    for col, default in [("action_name", "''"),
                         ("action_vars", "'{}'"),
                         ("agent_template", "''")]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add pipeline columns to board_tasks
    for col, default in [("parent_task_id", "''"),
                         ("pipeline_depth", "0"),
                         ("pipeline_root_id", "''")]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            col_type = "INTEGER" if col == "pipeline_depth" else "TEXT"
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add reply_agent_id column to board_tasks
    try:
        conn.execute(
            "SELECT reply_agent_id FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN reply_agent_id "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add description column to board_tasks
    try:
        conn.execute(
            "SELECT description FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN description "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add status column to board_tasks
    try:
        conn.execute("SELECT status FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN status "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add messages column to board_tasks
    try:
        conn.execute("SELECT messages FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN messages "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add scheduled_at column to board_tasks
    try:
        conn.execute(
            "SELECT scheduled_at FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN scheduled_at "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add depends_on column to board_tasks
    try:
        conn.execute(
            "SELECT depends_on FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN depends_on "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add attachments column to board_tasks
    try:
        conn.execute(
            "SELECT attachments FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN attachments "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add task health columns to board_tasks
    for col, default in [
        ("health_state", "'healthy'"),
        ("health_since", "''"),
        ("health_details", "'{}'"),
    ]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add artifacts column to board_tasks
    try:
        conn.execute(
            "SELECT artifacts FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN artifacts "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add verification columns to board_tasks
    for col, default in [
        ("lane_entered_at", "''"),
        ("verification_mode", "''"),
        ("verification_state", "''"),
        ("verification_notes", "''"),
        ("verification_updated_at", "''"),
        ("verification_updated_by", "''"),
        ("verification_summary", "'{}'"),
        ("worktree_boundary", "'{}'"),
        ("resume_after_boundary_task_id", "''"),
        ("archived_at", "''"),
        ("archived_from_lane", "''"),
    ]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: drop assignee column from board_tasks
    try:
        conn.execute(
            "SELECT assignee FROM board_tasks LIMIT 0")
        conn.execute(
            "ALTER TABLE board_tasks DROP COLUMN assignee")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already gone
    # Migrate: add tasks_dispatched column to agents
    for col, col_type, default in [
        ("tasks_dispatched", "INTEGER", "0"),
        ("created_by_weaver_id", "TEXT", "''"),
        ("template", "TEXT", "''"),
        ("worktree_base_dir", "TEXT", "'.loom/worktrees'"),
        ("worktree_auto_checkpoint", "INTEGER", "0"),
        ("checkpoint_on_progress", "INTEGER", "0"),
        ("worktree_merge_squash", "INTEGER", "1"),
        ("session_resume", "INTEGER", "1"),
        ("idle_timeout", "INTEGER", "5"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM agents LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE agents ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add default_agent_template column
    try:
        conn.execute(
            "SELECT default_agent_template FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "default_agent_template TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add board_default_* columns to group_settings
    for col, default in [("board_default_labels", "'[]'"),
                         ("board_default_lane", "''"),
                         ("board_default_action", "''")]:
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add env_file columns to group_settings
    for col in ("env_file", "agent_env_file", "terminal_env_file"):
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
    # Migrate: add worktree_symlinks column to group_settings
    try:
        conn.execute(
            "SELECT worktree_symlinks FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_symlinks TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add worktree_merge_cleanup column to group_settings
    try:
        conn.execute(
            "SELECT worktree_merge_cleanup FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_merge_cleanup TEXT NOT NULL DEFAULT 'keep'")
        conn.commit()
    # Migrate: add worktree_merge_preserve_diff column to group_settings
    try:
        conn.execute(
            "SELECT worktree_merge_preserve_diff FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_merge_preserve_diff INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migrate: add weaver_agent_id column to group_settings
    try:
        conn.execute(
            "SELECT weaver_agent_id FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "weaver_agent_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add checkpoint_on_progress to group_settings + agents
    for table in ("group_settings", "agents"):
        try:
            conn.execute(
                f"SELECT checkpoint_on_progress FROM {table} LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                "checkpoint_on_progress INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    for table, col in (
        ("agents", "terminal_backend"),
        ("group_settings", "default_terminal_backend"),
    ):
        try:
            conn.execute(f"SELECT {col} FROM {table} LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT 'iterm2'")
            conn.commit()
    # Migrate: add pending_question column to weaver_settings
    try:
        conn.execute(
            "SELECT pending_question FROM weaver_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE weaver_settings ADD COLUMN "
                "pending_question TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # table may not exist yet
    for col in ("pending_note", "pending_note_kind"):
        try:
            conn.execute(
                f"SELECT {col} FROM weaver_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE weaver_settings ADD COLUMN "
                    f"{col} TEXT NOT NULL DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "SELECT restrict_to_created_agents FROM weaver_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE weaver_settings ADD COLUMN "
                "restrict_to_created_agents INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # Migrate: add weaver_provider and launch override columns
    for col in ("weaver_provider", "weaver_boot_command",
                "weaver_model", "weaver_reasoning_effort"):
        try:
            conn.execute(
                f"SELECT {col} FROM weaver_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE weaver_settings ADD COLUMN "
                    f"{col} TEXT NOT NULL DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "SELECT heartbeat_interval FROM weaver_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE weaver_settings ADD COLUMN "
                "heartbeat_interval INTEGER NOT NULL DEFAULT 300")
            conn.execute(
                "UPDATE weaver_settings "
                "SET heartbeat_interval = max_interval")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    for col, default in (
        ("default_worker_concurrency", "2"),
        ("autonomy_mode", "'dispatch_when_clear'"),
        ("wave_size_preference", "'small'"),
        ("same_agent_follow_up_preference", "'balanced'"),
        ("digest_verbosity", "'balanced'"),
        ("escalation_style", "'note_then_ask'"),
    ):
        try:
            conn.execute(
                f"SELECT {col} FROM weaver_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE weaver_settings ADD COLUMN "
                    f"{col} "
                    f"{'INTEGER' if col == 'default_worker_concurrency' else 'TEXT'} "
                    f"NOT NULL DEFAULT {default}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "SELECT weaver_owner_id FROM auto_dispatch_queue LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE auto_dispatch_queue ADD COLUMN "
                "weaver_owner_id TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # Migrate: rename system labels with loom: prefix
    rows = conn.execute(
        "SELECT id, labels FROM board_tasks "
        "WHERE labels != '[]'"
    ).fetchall()
    migrated = False
    for tid, labels_json in rows:
        try:
            labels = json.loads(labels_json)
        except (json.JSONDecodeError, TypeError):
            continue
        new_labels = []
        changed = False
        for lb in labels:
            if lb in ("blocked", "derived", "error",
                      "human", "depth-limit"):
                new_labels.append("loom:" + lb)
                changed = True
            else:
                new_labels.append(lb)
        if changed:
            conn.execute(
                "UPDATE board_tasks SET labels = ? "
                "WHERE id = ?",
                (json.dumps(new_labels), tid))
            migrated = True
    if migrated:
        conn.commit()
    # Backfill agent history for existing agents
    backfill_agent_history()
    # Set schema version if not present
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,))
    conn.commit()

def rebuild_memory_retention_indexes(conn: sqlite3.Connection):
    """Ensure memory-entry indexes match the current retention schema."""
    for name in (
        "idx_memory_entries_scope",
        "idx_memory_entries_group",
        "idx_memory_entries_project",
        "idx_memory_entries_type",
        "idx_memory_entries_expiry",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_scope "
        "ON memory_entries("
        "scope_kind, scope_ref, pinned, retention_kind, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_group "
        "ON memory_entries("
        "group_name, pinned, retention_kind, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_project "
        "ON memory_entries("
        "project_key, pinned, retention_kind, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_type "
        "ON memory_entries(entry_type, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_expiry "
        "ON memory_entries(retention_kind, expires_at)"
    )
    conn.commit()
