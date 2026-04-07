"""SQLite persistence layer for Loom state.

Used by:
- The daemon (write path): save persistent fields on mutation
- The CLI (read path): direct SQLite reads for status/task queries

Uses synchronous sqlite3 — the daemon is single-threaded asyncio and
current save() already does sync file I/O.  Single-row upserts are faster
than json.dumps() + write_text().
"""

import json
import logging
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("loom")

SCHEMA_VERSION = "1"

# -- AgentCell columns persisted to SQLite ----------------------------------
# Ephemeral fields are NOT stored (they reset on restart anyway).

_AGENT_PERSISTED_COLS = [
    "id", "name", "slug", "group_name", "cell_type", "session_id", "profile",
    "command", "directory", "tab_color", "icon", "template", "window_id",
    "parent_id", "status", "worktree_path", "worktree_branch",
    "worktree_repo_root", "worktree_base_dir", "worktree_base_branch",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "agent_type",
    "agent_session_id", "session_resume", "idle_timeout",
    "tasks_dispatched",
]

# GroupSettings fields that store dicts — persisted as JSON text.
_GS_JSON_FIELDS = {"env_vars", "agent_env_vars", "terminal_env_vars",
                    "board_default_labels", "worktree_symlinks"}

# GroupSettings fields that are booleans — stored as INTEGER 0/1.
_GS_BOOL_FIELDS = {
    "collapsed_default", "filter_by_window", "git_worktree",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash",
    "agent_session_resume", "agent_always_custom_dialog",
    "dispatch_auto_terminals",
    "notifications", "notify_on_finish", "notify_on_error",
    "notify_on_attention", "terminal_always_custom_dialog",
    "terminal_close_on_disconnect",
}


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

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
    idle_timeout          INTEGER NOT NULL DEFAULT 5
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
    git_worktree                INTEGER NOT NULL DEFAULT 0,
    worktree_base_dir           TEXT NOT NULL DEFAULT '.loom/worktrees',
    worktree_base_branch        TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint    INTEGER NOT NULL DEFAULT 0,
    checkpoint_on_progress      INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash       INTEGER NOT NULL DEFAULT 1,
    worktree_merge_instructions TEXT NOT NULL DEFAULT '',
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
    labels         TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL DEFAULT '',
    external_id    TEXT NOT NULL DEFAULT '',
    external_url   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '',
    attachments    TEXT NOT NULL DEFAULT '[]',
    health_state   TEXT NOT NULL DEFAULT 'healthy',
    health_since   TEXT NOT NULL DEFAULT '',
    health_details TEXT NOT NULL DEFAULT '{}',
    artifacts      TEXT NOT NULL DEFAULT '[]'
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
    paused             INTEGER NOT NULL DEFAULT 0,
    custom_instructions TEXT NOT NULL DEFAULT '',
    pending_question   TEXT NOT NULL DEFAULT '',
    enabled_events     TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived"]',
    weaver_provider    TEXT NOT NULL DEFAULT '',
    weaver_boot_command TEXT NOT NULL DEFAULT ''
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
"""


class LoomDB:
    """SQLite persistence for Loom state."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def init(self):
        """Open connection, enable WAL, create tables if needed."""
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        # Migrate: add slug columns to existing tables
        for table in ("agents", "groups", "board_tasks"):
            try:
                self._conn.execute(f"SELECT slug FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN slug "
                    f"TEXT NOT NULL DEFAULT ''")
                self._conn.commit()
        # Migrate: add dispatch_auto_terminals column
        try:
            self._conn.execute(
                "SELECT dispatch_auto_terminals FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "dispatch_auto_terminals INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()
        # Migrate: add agent_provider column
        try:
            self._conn.execute(
                "SELECT agent_provider FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "agent_provider TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        # Migrate: add terminal_close_on_disconnect column
        try:
            self._conn.execute(
                "SELECT terminal_close_on_disconnect FROM group_settings "
                "LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()
        # Migrate: add action_name and action_vars columns to board_tasks
        for col, default in [("action_name", "''"),
                             ("action_vars", "'{}'"),
                             ("agent_template", "''")]:
            try:
                self._conn.execute(
                    f"SELECT {col} FROM board_tasks LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE board_tasks ADD COLUMN {col} "
                    f"TEXT NOT NULL DEFAULT {default}")
                self._conn.commit()
        # Migrate: add pipeline columns to board_tasks
        for col, default in [("parent_task_id", "''"),
                             ("pipeline_depth", "0"),
                             ("pipeline_root_id", "''")]:
            try:
                self._conn.execute(
                    f"SELECT {col} FROM board_tasks LIMIT 0")
            except sqlite3.OperationalError:
                col_type = "INTEGER" if col == "pipeline_depth" else "TEXT"
                self._conn.execute(
                    f"ALTER TABLE board_tasks ADD COLUMN {col} "
                    f"{col_type} NOT NULL DEFAULT {default}")
                self._conn.commit()
        # Migrate: add description column to board_tasks
        try:
            self._conn.execute(
                "SELECT description FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN description "
                "TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        # Migrate: add status column to board_tasks
        try:
            self._conn.execute("SELECT status FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN status "
                "TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        # Migrate: add messages column to board_tasks
        try:
            self._conn.execute("SELECT messages FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN messages "
                "TEXT NOT NULL DEFAULT '[]'")
            self._conn.commit()
        # Migrate: add scheduled_at column to board_tasks
        try:
            self._conn.execute(
                "SELECT scheduled_at FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN scheduled_at "
                "TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        # Migrate: add depends_on column to board_tasks
        try:
            self._conn.execute(
                "SELECT depends_on FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN depends_on "
                "TEXT NOT NULL DEFAULT '[]'")
            self._conn.commit()
        # Migrate: add attachments column to board_tasks
        try:
            self._conn.execute(
                "SELECT attachments FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN attachments "
                "TEXT NOT NULL DEFAULT '[]'")
            self._conn.commit()
        # Migrate: add task health columns to board_tasks
        for col, default in [
            ("health_state", "'healthy'"),
            ("health_since", "''"),
            ("health_details", "'{}'"),
        ]:
            try:
                self._conn.execute(
                    f"SELECT {col} FROM board_tasks LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE board_tasks ADD COLUMN {col} "
                    f"TEXT NOT NULL DEFAULT {default}")
                self._conn.commit()
        # Migrate: add artifacts column to board_tasks
        try:
            self._conn.execute(
                "SELECT artifacts FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE board_tasks ADD COLUMN artifacts "
                "TEXT NOT NULL DEFAULT '[]'")
            self._conn.commit()
        # Migrate: drop assignee column from board_tasks
        try:
            self._conn.execute(
                "SELECT assignee FROM board_tasks LIMIT 0")
            self._conn.execute(
                "ALTER TABLE board_tasks DROP COLUMN assignee")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already gone
        # Migrate: add tasks_dispatched column to agents
        for col, col_type, default in [
            ("tasks_dispatched", "INTEGER", "0"),
            ("template", "TEXT", "''"),
            ("worktree_base_dir", "TEXT", "'.loom/worktrees'"),
            ("worktree_auto_checkpoint", "INTEGER", "0"),
            ("checkpoint_on_progress", "INTEGER", "0"),
            ("worktree_merge_squash", "INTEGER", "1"),
            ("session_resume", "INTEGER", "1"),
            ("idle_timeout", "INTEGER", "5"),
        ]:
            try:
                self._conn.execute(f"SELECT {col} FROM agents LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE agents ADD COLUMN {col} "
                    f"{col_type} NOT NULL DEFAULT {default}")
                self._conn.commit()
        # Migrate: add default_agent_template column
        try:
            self._conn.execute(
                "SELECT default_agent_template FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "default_agent_template TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        # Migrate: add board_default_* columns to group_settings
        for col, default in [("board_default_labels", "'[]'"),
                             ("board_default_lane", "''"),
                             ("board_default_action", "''")]:
            try:
                self._conn.execute(
                    f"SELECT {col} FROM group_settings LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE group_settings ADD COLUMN "
                    f"{col} TEXT NOT NULL DEFAULT {default}")
                self._conn.commit()
        # Migrate: add env_file columns to group_settings
        for col in ("env_file", "agent_env_file", "terminal_env_file"):
            try:
                self._conn.execute(
                    f"SELECT {col} FROM group_settings LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE group_settings ADD COLUMN "
                    f"{col} TEXT NOT NULL DEFAULT ''")
                self._conn.commit()
        # Migrate: add worktree_symlinks column to group_settings
        try:
            self._conn.execute(
                "SELECT worktree_symlinks FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "worktree_symlinks TEXT NOT NULL DEFAULT '[]'")
            self._conn.commit()
        # Migrate: add weaver_agent_id column to group_settings
        try:
            self._conn.execute(
                "SELECT weaver_agent_id FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "weaver_agent_id TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        # Migrate: add checkpoint_on_progress to group_settings + agents
        for table in ("group_settings", "agents"):
            try:
                self._conn.execute(
                    f"SELECT checkpoint_on_progress FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN "
                    "checkpoint_on_progress INTEGER NOT NULL DEFAULT 0")
                self._conn.commit()
        # Migrate: add pending_question column to weaver_settings
        try:
            self._conn.execute(
                "SELECT pending_question FROM weaver_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                self._conn.execute(
                    "ALTER TABLE weaver_settings ADD COLUMN "
                    "pending_question TEXT NOT NULL DEFAULT ''")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # table may not exist yet
        # Migrate: add weaver_provider and weaver_boot_command columns
        for col in ("weaver_provider", "weaver_boot_command"):
            try:
                self._conn.execute(
                    f"SELECT {col} FROM weaver_settings LIMIT 0")
            except sqlite3.OperationalError:
                try:
                    self._conn.execute(
                        f"ALTER TABLE weaver_settings ADD COLUMN "
                        f"{col} TEXT NOT NULL DEFAULT ''")
                    self._conn.commit()
                except sqlite3.OperationalError:
                    pass
        # Migrate: rename system labels with loom: prefix
        rows = self._conn.execute(
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
                self._conn.execute(
                    "UPDATE board_tasks SET labels = ? "
                    "WHERE id = ?",
                    (json.dumps(new_labels), tid))
                migrated = True
        if migrated:
            self._conn.commit()
        # Backfill agent history for existing agents
        self.backfill_agent_history()
        # Set schema version if not present
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if not row:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,))
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- Write methods (daemon) ---------------------------------------------

    def save_agent(self, cell):
        """Upsert a single agent/terminal cell (persisted fields only)."""
        self._conn.execute("""
            INSERT OR REPLACE INTO agents
                (id, name, slug, group_name, cell_type, session_id, profile,
                 command, directory, tab_color, icon, template, window_id,
                 parent_id, status, worktree_path, worktree_branch,
                 worktree_repo_root, worktree_base_dir, worktree_base_branch,
                 worktree_auto_checkpoint, checkpoint_on_progress,
                 worktree_merge_squash,
                 agent_type, agent_session_id, session_resume, idle_timeout,
                 tasks_dispatched)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cell.id, cell.name, cell.slug, cell.group, cell.cell_type,
            cell.session_id, cell.profile, cell.command, cell.directory,
            cell.tab_color, cell.icon, cell.template, cell.window_id,
            cell.parent_id, cell.status, cell.worktree_path,
            cell.worktree_branch, cell.worktree_repo_root,
            cell.worktree_base_dir, cell.worktree_base_branch,
            int(cell.worktree_auto_checkpoint),
            int(cell.checkpoint_on_progress),
            int(cell.worktree_merge_squash), cell.agent_type,
            cell.agent_session_id, int(cell.session_resume),
            cell.idle_timeout,
            cell.tasks_dispatched,
        ))
        self._conn.commit()

    def delete_agent(self, agent_id: str):
        self._conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        self._conn.execute(
            "DELETE FROM group_members WHERE agent_id=?", (agent_id,))
        self._conn.commit()

    def save_group(self, name: str, position: int):
        self._conn.execute(
            "INSERT OR REPLACE INTO groups (name, position) VALUES (?,?)",
            (name, position))
        self._conn.commit()

    def delete_group(self, name: str):
        self._conn.execute("DELETE FROM groups WHERE name=?", (name,))
        self._conn.execute(
            "DELETE FROM group_members WHERE group_name=?", (name,))
        self._conn.execute(
            "DELETE FROM group_settings WHERE group_name=?", (name,))
        self._conn.commit()

    def save_groups(self, groups: dict, slugs: dict = None):
        """Bulk-save all groups with positions and slugs."""
        slugs = slugs or {}
        self._conn.execute("DELETE FROM groups")
        for pos, name in enumerate(groups):
            self._conn.execute(
                "INSERT INTO groups (name, slug, position) VALUES (?,?,?)",
                (name, slugs.get(name, ""), pos))
        self._conn.commit()

    def save_group_members(self, group_name: str, agent_ids: list):
        """Replace the membership list for a group."""
        self._conn.execute(
            "DELETE FROM group_members WHERE group_name=?", (group_name,))
        for pos, aid in enumerate(agent_ids):
            self._conn.execute(
                "INSERT INTO group_members (group_name, agent_id, position) "
                "VALUES (?,?,?)", (group_name, aid, pos))
        self._conn.commit()

    def save_group_settings(self, group_name: str, gs):
        """Upsert group settings."""
        d = asdict(gs)
        # Convert dicts to JSON text, bools to int
        for k in _GS_JSON_FIELDS:
            if k in d:
                d[k] = json.dumps(d[k])
        for k in _GS_BOOL_FIELDS:
            if k in d:
                d[k] = int(d[k])

        cols = ["group_name"] + list(d.keys())
        placeholders = ",".join(["?"] * len(cols))
        col_str = ",".join(cols)
        self._conn.execute(
            f"INSERT OR REPLACE INTO group_settings ({col_str}) "
            f"VALUES ({placeholders})",
            [group_name] + list(d.values()))
        self._conn.commit()

    def delete_group_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM group_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def save_board_task(self, task):
        """Upsert a board task."""
        d = asdict(task)
        labels = json.dumps(d.pop("labels", []))
        action_vars = json.dumps(d.pop("action_vars", {}))
        messages = json.dumps(d.pop("messages", []))
        depends_on = json.dumps(d.pop("depends_on", []))
        attachments = json.dumps(d.pop("attachments", []))
        health_details = json.dumps(d.pop("health_details", {}))
        artifacts = json.dumps(d.pop("artifacts", []))
        # Map 'group' to 'group_name' for the DB column
        group_name = d.pop("group", "")
        self._conn.execute("""
            INSERT OR REPLACE INTO board_tasks
                (id, task, description, slug, group_name,
                 action_name, action_vars, agent_template,
                 instructions, context, criteria,
                 lane, position, agent_id, labels, created_at,
                 updated_at, provider, external_id, external_url,
                 parent_task_id, pipeline_depth, pipeline_root_id, status,
                 scheduled_at, messages, depends_on, attachments,
                 health_state, health_since, health_details, artifacts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d["id"], d["task"], d.get("description", ""),
            d["slug"], group_name,
            d.get("action_name", ""), action_vars,
            d.get("agent_template", ""),
            d["instructions"], d["context"], d["criteria"],
            d["lane"], d["position"],
            d["agent_id"], labels, d["created_at"],
            d["updated_at"], d["provider"], d["external_id"],
            d["external_url"],
            d.get("parent_task_id", ""), d.get("pipeline_depth", 0),
            d.get("pipeline_root_id", ""), d.get("status", ""),
            d.get("scheduled_at", ""),
            messages, depends_on, attachments,
            d.get("health_state", "healthy"),
            d.get("health_since", ""),
            health_details,
            artifacts,
        ))
        self._conn.commit()

    def delete_board_task(self, task_id: str):
        self._conn.execute(
            "DELETE FROM board_tasks WHERE id=?", (task_id,))
        self._conn.commit()

    def save_schedule(self, sched):
        """Upsert a schedule."""
        d = asdict(sched)
        labels = json.dumps(d.pop("labels", []))
        action_vars = json.dumps(d.pop("action_vars", {}))
        group_name = d.pop("group", "")
        self._conn.execute("""
            INSERT OR REPLACE INTO schedules
                (id, name, slug, task_template, description, group_name,
                 action_name, action_vars, agent_template, labels,
                 cron_expr, scheduled_at, timezone, enabled,
                 last_run_at, next_run_at, run_count, last_task_id,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d["id"], d["name"], d["slug"],
            d.get("task_template", ""), d.get("description", ""),
            group_name,
            d.get("action_name", ""), action_vars,
            d.get("agent_template", ""), labels,
            d.get("cron_expr", ""), d.get("scheduled_at", ""),
            d.get("timezone", ""), 1 if d.get("enabled", True) else 0,
            d.get("last_run_at", ""), d.get("next_run_at", ""),
            d.get("run_count", 0), d.get("last_task_id", ""),
            d["created_at"], d["updated_at"],
        ))
        self._conn.commit()

    def delete_schedule(self, sid: str):
        self._conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
        self._conn.commit()

    def save_board_lanes(self, lanes: list):
        """Replace all lanes with new ordered list."""
        self._conn.execute("DELETE FROM board_lanes")
        for pos, name in enumerate(lanes):
            self._conn.execute(
                "INSERT INTO board_lanes (name, position) VALUES (?,?)",
                (name, pos))
        self._conn.commit()

    def save_ui_state(self, key: str, value):
        self._conn.execute(
            "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?,?)",
            (key, str(value)))
        self._conn.commit()

    def save_global_settings(self, gs):
        """Persist global settings as key-value pairs."""
        d = asdict(gs)
        self._conn.execute("DELETE FROM global_settings")
        for key, value in d.items():
            self._conn.execute(
                "INSERT INTO global_settings (key, value) VALUES (?,?)",
                (key, json.dumps(value)))
        self._conn.commit()

    # -- Panel events -------------------------------------------------------

    def save_panel_event(self, evt: dict) -> int:
        """Insert a panel event and return the assigned row ID."""
        self._conn.execute(
            "INSERT INTO panel_events "
            "(id, timestamp, kind, cell_id, agent_name, group_name, "
            "message, task_id) VALUES (?,?,?,?,?,?,?,?)",
            (evt["id"], evt["timestamp"], evt["kind"],
             evt.get("cell_id", ""), evt.get("agent_name", ""),
             evt.get("group", ""), evt.get("message", ""),
             evt.get("task_id", "")))
        self._conn.commit()
        return evt["id"]

    def update_panel_event(self, evt: dict):
        """Update an existing panel event row."""
        self._conn.execute(
            "UPDATE panel_events SET timestamp=?, kind=?, cell_id=?, "
            "agent_name=?, group_name=?, message=?, task_id=? WHERE id=?",
            (evt["timestamp"], evt["kind"], evt.get("cell_id", ""),
             evt.get("agent_name", ""), evt.get("group", ""),
             evt.get("message", ""), evt.get("task_id", ""),
             evt["id"]))
        self._conn.commit()

    def trim_panel_events(self, max_size: int):
        """Delete oldest events beyond *max_size*."""
        self._conn.execute(
            "DELETE FROM panel_events WHERE id NOT IN "
            "(SELECT id FROM panel_events ORDER BY id DESC LIMIT ?)",
            (max_size,))
        self._conn.commit()

    def load_panel_events(self, limit: int = 50,
                          before_id: int = 0) -> list[dict]:
        """Load a page of panel events ordered by id DESC.

        Returns dicts with 'group' key (matching in-memory format).
        """
        if before_id:
            rows = self._conn.execute(
                "SELECT id, timestamp, kind, cell_id, agent_name, "
                "group_name, message, task_id FROM panel_events "
                "WHERE id < ? ORDER BY id DESC LIMIT ?",
                (before_id, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, timestamp, kind, cell_id, agent_name, "
                "group_name, message, task_id FROM panel_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        events = []
        for r in rows:
            events.append({
                "id": r[0], "timestamp": r[1], "kind": r[2],
                "cell_id": r[3], "agent_name": r[4], "group": r[5],
                "message": r[6], "task_id": r[7],
            })
        # Return in ascending id order (oldest first)
        events.reverse()
        return events

    def get_panel_event_max_id(self) -> int:
        """Return the highest panel_events id, or 0 if empty."""
        row = self._conn.execute(
            "SELECT MAX(id) FROM panel_events").fetchone()
        return row[0] if row and row[0] is not None else 0

    # -- Weaver settings & journal -------------------------------------------

    def save_weaver_settings(self, group_name: str, settings: dict):
        """Upsert weaver settings for a group."""
        enabled_events = json.dumps(
            settings.get("enabled_events",
                         ["agent_started", "task_dispatched", "task_derived"]))
        self._conn.execute("""
            INSERT OR REPLACE INTO weaver_settings
                (group_name, push_interval, max_interval, paused,
                 custom_instructions, pending_question, enabled_events,
                 weaver_provider, weaver_boot_command)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            group_name,
            settings.get("push_interval", 60),
            settings.get("max_interval", 300),
            1 if settings.get("paused", False) else 0,
            settings.get("custom_instructions", ""),
            settings.get("pending_question", ""),
            enabled_events,
            settings.get("weaver_provider", ""),
            settings.get("weaver_boot_command", ""),
        ))
        self._conn.commit()

    def load_weaver_settings(self, group_name: str) -> dict | None:
        """Load weaver settings for a group. Returns None if not set."""
        row = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, paused, "
            "custom_instructions, pending_question, enabled_events, "
            "weaver_provider, weaver_boot_command "
            "FROM weaver_settings "
            "WHERE group_name=?", (group_name,)).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[6])
        except (json.JSONDecodeError, TypeError):
            enabled = ["agent_started", "task_dispatched", "task_derived"]
        return {
            "group": row[0],
            "push_interval": row[1],
            "max_interval": row[2],
            "paused": bool(row[3]),
            "custom_instructions": row[4],
            "pending_question": row[5],
            "enabled_events": enabled,
            "weaver_provider": row[7] if len(row) > 7 else "",
            "weaver_boot_command": row[8] if len(row) > 8 else "",
        }

    def delete_weaver_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM weaver_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def load_all_weaver_settings(self) -> dict[str, dict]:
        """Load weaver settings for all groups. Returns {group: settings}."""
        rows = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, paused, "
            "custom_instructions, pending_question, enabled_events, "
            "weaver_provider, weaver_boot_command "
            "FROM weaver_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[6])
            except (json.JSONDecodeError, TypeError):
                enabled = ["agent_started", "task_dispatched", "task_derived"]
            result[row[0]] = {
                "group": row[0],
                "push_interval": row[1],
                "max_interval": row[2],
                "paused": bool(row[3]),
                "custom_instructions": row[4],
                "pending_question": row[5],
                "enabled_events": enabled,
                "weaver_provider": row[7] if len(row) > 7 else "",
                "weaver_boot_command": row[8] if len(row) > 8 else "",
            }
        return result

    def save_journal_entry(self, group_name: str, timestamp: float,
                           entry_type: str, entry: str) -> int:
        """Insert a weaver journal entry. Returns the new row ID."""
        c = self._conn.execute(
            "INSERT INTO weaver_journal "
            "(group_name, timestamp, entry_type, entry) "
            "VALUES (?,?,?,?)",
            (group_name, timestamp, entry_type, entry))
        self._conn.commit()
        return c.lastrowid

    def load_journal_entries(self, group_name: str, limit: int = 20,
                             entry_type: str = "") -> list[dict]:
        """Load recent journal entries for a group, newest first."""
        if entry_type:
            rows = self._conn.execute(
                "SELECT id, group_name, timestamp, entry_type, entry "
                "FROM weaver_journal WHERE group_name=? AND entry_type=? "
                "ORDER BY id DESC LIMIT ?",
                (group_name, entry_type, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, group_name, timestamp, entry_type, entry "
                "FROM weaver_journal WHERE group_name=? "
                "ORDER BY id DESC LIMIT ?",
                (group_name, limit)).fetchall()
        return [{"id": r[0], "group": r[1], "timestamp": r[2],
                 "type": r[3], "entry": r[4]} for r in rows]

    # -- Agent history -------------------------------------------------------

    def save_agent_history(self, record: dict):
        """Insert or replace an agent history record."""
        self._conn.execute("""
            INSERT OR REPLACE INTO agent_history
                (id, name, slug, "group", agent_type, template,
                 created_at, removed_at, worktree_branch,
                 total_tokens_in, total_tokens_out, total_tasks, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            record["id"], record["name"], record.get("slug", ""),
            record.get("group", ""), record.get("agent_type", ""),
            record.get("template", ""), record["created_at"],
            record.get("removed_at"), record.get("worktree_branch", ""),
            record.get("total_tokens_in", 0),
            record.get("total_tokens_out", 0),
            record.get("total_tasks", 0),
            record.get("status", "active"),
        ))
        self._conn.commit()

    def update_agent_history(self, agent_id: str, **fields):
        """Update specific fields on an agent history record."""
        if not fields:
            return
        allowed = {"name", "slug", "group", "agent_type", "template",
                   "removed_at", "worktree_branch", "total_tokens_in",
                   "total_tokens_out", "total_tasks", "status"}
        parts = []
        vals = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            col = f'"{k}"' if k == "group" else k
            parts.append(f"{col}=?")
            vals.append(v)
        if not parts:
            return
        vals.append(agent_id)
        self._conn.execute(
            f"UPDATE agent_history SET {','.join(parts)} WHERE id=?",
            vals)
        self._conn.commit()

    def save_agent_task(self, record: dict):
        """Insert an agent-task association."""
        self._conn.execute(
            "INSERT INTO agent_tasks "
            "(agent_id, task_id, task_title, started_at, completed_at, "
            "outcome) VALUES (?,?,?,?,?,?)",
            (record["agent_id"], record["task_id"],
             record["task_title"], record["started_at"],
             record.get("completed_at"), record.get("outcome", "")))
        self._conn.commit()

    def update_agent_task(self, agent_id: str, task_id: str, **fields):
        """Update an agent-task record (completed_at, outcome)."""
        parts = []
        vals = []
        for k in ("completed_at", "outcome"):
            if k in fields:
                parts.append(f"{k}=?")
                vals.append(fields[k])
        if not parts:
            return
        vals.extend([agent_id, task_id])
        self._conn.execute(
            f"UPDATE agent_tasks SET {','.join(parts)} "
            f"WHERE agent_id=? AND task_id=?", vals)
        self._conn.commit()

    def save_agent_message(self, record: dict):
        """Insert an agent message record."""
        self._conn.execute(
            "INSERT INTO agent_messages "
            "(agent_id, task_id, timestamp, action, message) "
            "VALUES (?,?,?,?,?)",
            (record["agent_id"], record.get("task_id", ""),
             record["timestamp"], record["action"],
             record.get("message", "")))
        self._conn.commit()

    def load_agent_history(self, status_filter: str = "",
                           limit: int = 50, offset: int = 0
                           ) -> list[dict]:
        """Load agent history records, active first."""
        sql = (
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status FROM agent_history")
        params: list = []
        if status_filter:
            sql += " WHERE status=?"
            params.append(status_filter)
        sql += (" ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END,"
                " created_at DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_history_detail(self, agent_id: str
                                  ) -> Optional[dict]:
        """Load a single agent history record."""
        row = self._conn.execute(
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status "
            "FROM agent_history WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return dict(zip(cols, row))

    def load_agent_tasks(self, agent_id: str) -> list[dict]:
        """Load task associations for an agent."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, task_title, started_at, "
            "completed_at, outcome FROM agent_tasks "
            "WHERE agent_id=? ORDER BY started_at DESC",
            (agent_id,)).fetchall()
        cols = ["id", "agent_id", "task_id", "task_title",
                "started_at", "completed_at", "outcome"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_messages(self, agent_id: str,
                            limit: int = 100) -> list[dict]:
        """Load messages for an agent, newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, timestamp, action, message "
            "FROM agent_messages WHERE agent_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (agent_id, limit)).fetchall()
        cols = ["id", "agent_id", "task_id", "timestamp",
                "action", "message"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_messages_by_task(self, task_id: str,
                                    limit: int = 100) -> list[dict]:
        """Load messages for a task, newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, timestamp, action, message "
            "FROM agent_messages WHERE task_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (task_id, limit)).fetchall()
        cols = ["id", "agent_id", "task_id", "timestamp",
                "action", "message"]
        return [dict(zip(cols, r)) for r in rows]

    def load_all_agent_tasks(self) -> list[dict]:
        """Load all agent-task associations ordered by start time."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, task_title, started_at, "
            "completed_at, outcome FROM agent_tasks "
            "ORDER BY started_at ASC, id ASC").fetchall()
        cols = ["id", "agent_id", "task_id", "task_title",
                "started_at", "completed_at", "outcome"]
        return [dict(zip(cols, r)) for r in rows]

    def load_all_agent_history_records(self) -> list[dict]:
        """Load all persisted agent history records."""
        rows = self._conn.execute(
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status "
            "FROM agent_history ORDER BY created_at ASC").fetchall()
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return [dict(zip(cols, r)) for r in rows]

    # -- Playbook candidates -----------------------------------------------

    def replace_playbook_candidates(self, candidates: list[dict],
                                    group_name: str = ""):
        """Replace persisted draft playbook candidates."""
        if group_name:
            self._conn.execute(
                "DELETE FROM playbook_candidates WHERE group_name=?",
                (group_name,))
        else:
            self._conn.execute("DELETE FROM playbook_candidates")

        for candidate in candidates:
            self._conn.execute("""
                INSERT OR REPLACE INTO playbook_candidates
                    (id, group_name, family_key, status, created_at,
                     updated_at, name, root_action, labels,
                     normalized_task_family, entry_action, agent_template,
                     workflow, workflow_shape, dispatch_sequence,
                     action_combination, constraints, evidence,
                     supporting_runs, counterexamples)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                candidate["id"],
                candidate.get("group", ""),
                candidate.get("family_key", ""),
                candidate.get("status", "draft"),
                candidate.get("created_at", 0.0),
                candidate.get("updated_at", candidate.get("created_at", 0.0)),
                candidate.get("name", ""),
                candidate.get("root_action", ""),
                json.dumps(candidate.get("labels", [])),
                candidate.get("normalized_task_family", ""),
                candidate.get("entry_action", ""),
                candidate.get("agent_template", ""),
                json.dumps(candidate.get("workflow", [])),
                json.dumps(candidate.get("workflow_shape",
                                         candidate.get("workflow", []))),
                json.dumps(candidate.get("dispatch_sequence", [])),
                json.dumps(candidate.get("action_combination", [])),
                json.dumps(candidate.get("constraints", {})),
                json.dumps(candidate.get("evidence", {})),
                json.dumps(candidate.get("supporting_runs", [])),
                json.dumps(candidate.get("counterexamples", [])),
            ))
        self._conn.commit()

    def load_playbook_candidates(self, group_name: str = "",
                                 limit: int = 50) -> list[dict]:
        """Load persisted draft playbook candidates."""
        params: list = [limit]
        sql = (
            "SELECT id, group_name, family_key, status, created_at, "
            "updated_at, name, root_action, labels, "
            "normalized_task_family, entry_action, agent_template, "
            "workflow, workflow_shape, dispatch_sequence, "
            "action_combination, constraints, evidence, "
            "supporting_runs, counterexamples "
            "FROM playbook_candidates"
        )
        if group_name:
            sql += " WHERE group_name=?"
            params = [group_name, limit]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "group", "family_key", "status", "created_at",
                "updated_at", "name", "root_action", "labels",
                "normalized_task_family", "entry_action", "agent_template",
                "workflow", "workflow_shape", "dispatch_sequence",
                "action_combination", "constraints", "evidence",
                "supporting_runs", "counterexamples"]
        decoded = []
        for row in rows:
            item = dict(zip(cols, row))
            for key, default in (
                ("labels", []),
                ("workflow", []),
                ("workflow_shape", []),
                ("dispatch_sequence", []),
                ("action_combination", []),
                ("constraints", {}),
                ("evidence", {}),
                ("supporting_runs", []),
                ("counterexamples", []),
            ):
                try:
                    item[key] = json.loads(item.get(key, json.dumps(default)))
                except (json.JSONDecodeError, TypeError):
                    item[key] = default.copy() if isinstance(default, dict) else list(default)
            decoded.append(item)
        return decoded

    def load_playbook_candidate(self, candidate_id: str) -> Optional[dict]:
        """Load one persisted draft playbook candidate by ID."""
        rows = self.load_playbook_candidates(limit=1000)
        for row in rows:
            if row["id"] == candidate_id:
                return row
        return None

    def save_playbook(self, playbook: dict):
        """Insert or replace a generated or published playbook record."""
        self._conn.execute("""
            INSERT OR REPLACE INTO playbooks
                (id, group_name, source_candidate_id, status, generated,
                 review_required, created_at, updated_at, published_at,
                 discarded_at, name, description, match_data, entry_action,
                 agent_template, workflow, constraints, evidence,
                 publication_preview)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            playbook["id"],
            playbook.get("group", ""),
            playbook.get("source_candidate_id", ""),
            playbook.get("status", "draft"),
            1 if playbook.get("generated", True) else 0,
            1 if playbook.get("review_required", True) else 0,
            playbook.get("created_at", 0.0),
            playbook.get("updated_at", playbook.get("created_at", 0.0)),
            playbook.get("published_at"),
            playbook.get("discarded_at"),
            playbook.get("name", ""),
            playbook.get("description", ""),
            json.dumps(playbook.get("match", {})),
            playbook.get("entry_action", ""),
            playbook.get("agent_template", ""),
            json.dumps(playbook.get("workflow", [])),
            json.dumps(playbook.get("constraints", {})),
            json.dumps(playbook.get("evidence", {})),
            json.dumps(playbook.get("publication_preview", {})),
        ))
        self._conn.commit()

    def load_playbooks(self, group_name: str = "", status_filter: str = "",
                       limit: int = 50) -> list[dict]:
        """Load persisted playbook drafts or published recipes."""
        sql = (
            "SELECT id, group_name, source_candidate_id, status, generated, "
            "review_required, created_at, updated_at, published_at, "
            "discarded_at, name, description, match_data, entry_action, "
            "agent_template, workflow, constraints, evidence, "
            "publication_preview FROM playbooks"
        )
        clauses = []
        params: list = []
        if group_name:
            clauses.append("group_name=?")
            params.append(group_name)
        if status_filter:
            clauses.append("status=?")
            params.append(status_filter)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "group", "source_candidate_id", "status", "generated",
                "review_required", "created_at", "updated_at",
                "published_at", "discarded_at", "name", "description",
                "match", "entry_action", "agent_template", "workflow",
                "constraints", "evidence", "publication_preview"]
        decoded = []
        for row in rows:
            item = dict(zip(cols, row))
            item["generated"] = bool(item.get("generated", 1))
            item["review_required"] = bool(item.get("review_required", 1))
            for key, default in (
                ("match", {}),
                ("workflow", []),
                ("constraints", {}),
                ("evidence", {}),
                ("publication_preview", {}),
            ):
                try:
                    item[key] = json.loads(item.get(key, json.dumps(default)))
                except (json.JSONDecodeError, TypeError):
                    item[key] = default.copy() if isinstance(default, dict) else list(default)
            decoded.append(item)
        return decoded

    def load_playbook(self, playbook_id: str) -> Optional[dict]:
        """Load one persisted playbook draft or published recipe."""
        rows = self.load_playbooks(limit=1000)
        for row in rows:
            if row["id"] == playbook_id:
                return row
        return None

    def backfill_agent_history(self):
        """Create history records for existing agents that lack them."""
        import time
        rows = self._conn.execute(
            "SELECT id, name, slug, group_name, agent_type, template, "
            "worktree_branch, tasks_dispatched FROM agents "
            "WHERE cell_type='agent' AND id NOT IN "
            "(SELECT id FROM agent_history)").fetchall()
        for r in rows:
            self._conn.execute("""
                INSERT OR IGNORE INTO agent_history
                    (id, name, slug, "group", agent_type, template,
                     created_at, worktree_branch, total_tasks, status)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (r[0], r[1], r[2], r[3], r[4], r[5],
                  time.time(), r[6], r[7], "active"))
        self._conn.commit()

    # -- Bulk save (transitional) -------------------------------------------

    def save_all(self, state_dict: dict):
        """Bulk-write entire state to DB (used by migrate_from_json)."""
        c = self._conn.cursor()
        try:
            # Agents
            c.execute("DELETE FROM agents")
            for aid, a in state_dict.get("agents", {}).items():
                c.execute("""
                    INSERT INTO agents
                        (id, name, slug, group_name, cell_type, session_id,
                         profile, command, directory, tab_color, icon,
                         template, window_id, parent_id, status,
                         worktree_path, worktree_branch, worktree_repo_root,
                         worktree_base_dir, worktree_base_branch,
                         worktree_auto_checkpoint, checkpoint_on_progress,
                         worktree_merge_squash,
                         agent_type, agent_session_id, session_resume,
                         idle_timeout, tasks_dispatched)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    a.get("id", aid),
                    a.get("name", ""),
                    a.get("slug", ""),
                    a.get("group", ""),
                    a.get("cell_type", "agent"),
                    a.get("session_id"),
                    a.get("profile", "Default"),
                    a.get("command", ""),
                    a.get("directory", ""),
                    a.get("tab_color", ""),
                    a.get("icon", ""),
                    a.get("template", ""),
                    a.get("window_id", ""),
                    a.get("parent_id", ""),
                    a.get("status", "stopped"),
                    a.get("worktree_path", ""),
                    a.get("worktree_branch", ""),
                    a.get("worktree_repo_root", ""),
                    a.get("worktree_base_dir", ".loom/worktrees"),
                    a.get("worktree_base_branch", ""),
                    int(a.get("worktree_auto_checkpoint", False)),
                    int(a.get("checkpoint_on_progress", False)),
                    int(a.get("worktree_merge_squash", True)),
                    a.get("agent_type", ""),
                    a.get("agent_session_id", ""),
                    int(a.get("session_resume", True)),
                    a.get("idle_timeout", 5),
                    a.get("tasks_dispatched", 0),
                ))

            # Groups + members
            c.execute("DELETE FROM groups")
            c.execute("DELETE FROM group_members")
            group_slugs = state_dict.get("group_slugs", {})
            for pos, (gname, members) in enumerate(
                    state_dict.get("groups", {}).items()):
                c.execute(
                    "INSERT INTO groups (name, slug, position) VALUES (?,?,?)",
                    (gname, group_slugs.get(gname, ""), pos))
                for mpos, aid in enumerate(members):
                    c.execute(
                        "INSERT INTO group_members "
                        "(group_name, agent_id, position) VALUES (?,?,?)",
                        (gname, aid, mpos))

            # Group settings
            c.execute("DELETE FROM group_settings")
            for gname, gs in state_dict.get("group_settings", {}).items():
                d = dict(gs) if isinstance(gs, dict) else asdict(gs)
                for k in _GS_JSON_FIELDS:
                    if k in d:
                        d[k] = json.dumps(d[k])
                for k in _GS_BOOL_FIELDS:
                    if k in d:
                        d[k] = int(d[k])
                cols = ["group_name"] + list(d.keys())
                placeholders = ",".join(["?"] * len(cols))
                col_str = ",".join(cols)
                c.execute(
                    f"INSERT INTO group_settings ({col_str}) "
                    f"VALUES ({placeholders})",
                    [gname] + list(d.values()))

            # Board lanes
            c.execute("DELETE FROM board_lanes")
            for pos, lane in enumerate(
                    state_dict.get("board_lanes", [])):
                c.execute(
                    "INSERT INTO board_lanes (name, position) VALUES (?,?)",
                    (lane, pos))

            # Board tasks
            c.execute("DELETE FROM board_tasks")
            for tid, t in state_dict.get("board_tasks", {}).items():
                d = dict(t) if isinstance(t, dict) else asdict(t)
                labels = json.dumps(d.pop("labels", []))
                action_vars = json.dumps(d.pop("action_vars", {}))
                messages = json.dumps(d.pop("messages", []))
                depends_on = json.dumps(d.pop("depends_on", []))
                attachments = json.dumps(d.pop("attachments", []))
                health_details = json.dumps(d.pop("health_details", {}))
                artifacts = json.dumps(d.pop("artifacts", []))
                group_name = d.pop("group", "")
                c.execute("""
                    INSERT INTO board_tasks
                        (id, task, description, slug, group_name,
                         action_name, agent_template,
                         action_vars, instructions, context,
                         criteria, lane, position, agent_id, labels,
                         created_at, updated_at, provider, external_id,
                         external_url, parent_task_id, pipeline_depth,
                         pipeline_root_id, status, scheduled_at, messages,
                         depends_on, attachments, health_state, health_since,
                         health_details, artifacts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    d.get("id", tid), d.get("task", ""),
                    d.get("description", ""), d.get("slug", ""),
                    group_name, d.get("action_name", ""),
                    d.get("agent_template", ""), action_vars,
                    d.get("instructions", ""),
                    d.get("context", ""), d.get("criteria", ""),
                    d.get("lane", "Backlog"), d.get("position", 0),
                    d.get("agent_id", ""), labels,
                    d.get("created_at", ""), d.get("updated_at", ""),
                    d.get("provider", ""), d.get("external_id", ""),
                    d.get("external_url", ""),
                    d.get("parent_task_id", ""), d.get("pipeline_depth", 0),
                    d.get("pipeline_root_id", ""), d.get("status", ""),
                    d.get("scheduled_at", ""), messages, depends_on,
                    attachments, d.get("health_state", "healthy"),
                    d.get("health_since", ""), health_details,
                    artifacts,
                ))

            # UI state
            c.execute("DELETE FROM ui_state")
            for key in ("panel_active", "board_panel_height"):
                val = state_dict.get(key)
                if val is not None:
                    c.execute(
                        "INSERT INTO ui_state (key, value) VALUES (?,?)",
                        (key, str(val)))
            if state_dict.get("board_filters_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_filters_by_group",
                        json.dumps(state_dict.get("board_filters_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_saved_views_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_saved_views_by_group",
                        json.dumps(state_dict.get("board_saved_views_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_lane_sorts_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_lane_sorts_by_group",
                        json.dumps(state_dict.get("board_lane_sorts_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_card_density_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_card_density_by_group",
                        json.dumps(state_dict.get("board_card_density_by_group")
                                   or {}),
                    ),
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- Read methods (daemon startup + CLI) --------------------------------

    def load_all(self) -> dict:
        """Load full state from SQLite. Returns dict matching state.json
        structure for easy consumption by MatrixState.load()."""
        c = self._conn.cursor()

        # Agents
        agents = {}
        for row in c.execute(
                "SELECT * FROM agents").fetchall():
            cols = [d[0] for d in c.description]
            d = dict(zip(cols, row))
            # Map group_name back to 'group' for AgentCell
            d["group"] = d.pop("group_name")
            d["worktree_auto_checkpoint"] = bool(
                d.get("worktree_auto_checkpoint", 0))
            d["checkpoint_on_progress"] = bool(
                d.get("checkpoint_on_progress", 0))
            d["worktree_merge_squash"] = bool(
                d.get("worktree_merge_squash", 1))
            d["session_resume"] = bool(d.get("session_resume", 1))
            agents[d["id"]] = d

        # Groups (ordered)
        groups = {}
        group_slugs = {}
        for row in c.execute(
                "SELECT name, slug FROM groups ORDER BY position"):
            groups[row[0]] = []
            if row[1]:
                group_slugs[row[0]] = row[1]

        # Group members
        for row in c.execute(
                "SELECT group_name, agent_id FROM group_members "
                "ORDER BY group_name, position"):
            gname, aid = row
            if gname in groups:
                groups[gname].append(aid)

        # Group settings
        group_settings = {}
        rows = c.execute("SELECT * FROM group_settings").fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            for row in rows:
                d = dict(zip(cols, row))
                gname = d.pop("group_name")
                # Decode JSON fields
                for k in _GS_JSON_FIELDS:
                    if k in d and isinstance(d[k], str):
                        try:
                            d[k] = json.loads(d[k])
                        except (json.JSONDecodeError, TypeError):
                            d[k] = [] if k in {"board_default_labels",
                                                "worktree_symlinks"} else {}
                # Decode booleans
                for k in _GS_BOOL_FIELDS:
                    if k in d:
                        d[k] = bool(d[k])
                group_settings[gname] = d

        # Board lanes
        board_lanes = [row[0] for row in c.execute(
            "SELECT name FROM board_lanes ORDER BY position")]

        # Board tasks
        board_tasks = {}
        rows = c.execute("SELECT * FROM board_tasks").fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            for row in rows:
                d = dict(zip(cols, row))
                # Map group_name back to 'group'
                d["group"] = d.pop("group_name")
                # Decode labels JSON
                try:
                    d["labels"] = json.loads(d.get("labels", "[]"))
                except (json.JSONDecodeError, TypeError):
                    d["labels"] = []
                # Decode action_vars JSON
                try:
                    d["action_vars"] = json.loads(
                        d.get("action_vars", "{}"))
                except (json.JSONDecodeError, TypeError):
                    d["action_vars"] = {}
                # Decode messages JSON
                try:
                    d["messages"] = json.loads(
                        d.get("messages", "[]"))
                except (json.JSONDecodeError, TypeError):
                    d["messages"] = []
                # Decode attachments JSON
                try:
                    d["attachments"] = json.loads(
                        d.get("attachments", "[]"))
                except (json.JSONDecodeError, TypeError):
                    d["attachments"] = []
                # Decode artifacts JSON
                try:
                    d["artifacts"] = json.loads(
                        d.get("artifacts", "[]"))
                except (json.JSONDecodeError, TypeError):
                    d["artifacts"] = []
                # Decode depends_on JSON
                try:
                    d["depends_on"] = json.loads(
                        d.get("depends_on", "[]"))
                except (json.JSONDecodeError, TypeError):
                    d["depends_on"] = []
                try:
                    d["health_details"] = json.loads(
                        d.get("health_details", "{}"))
                except (json.JSONDecodeError, TypeError):
                    d["health_details"] = {}
                board_tasks[d["id"]] = d

        # UI state
        ui = {}
        for row in c.execute("SELECT key, value FROM ui_state"):
            ui[row[0]] = row[1]
        try:
            board_filters_by_group = json.loads(
                ui.get("board_filters_by_group", "{}") or "{}"
            )
            if not isinstance(board_filters_by_group, dict):
                board_filters_by_group = {}
        except Exception:
            board_filters_by_group = {}
        try:
            board_saved_views_by_group = json.loads(
                ui.get("board_saved_views_by_group", "{}") or "{}"
            )
            if not isinstance(board_saved_views_by_group, dict):
                board_saved_views_by_group = {}
        except Exception:
            board_saved_views_by_group = {}
        try:
            board_lane_sorts_by_group = json.loads(
                ui.get("board_lane_sorts_by_group", "{}") or "{}"
            )
            if not isinstance(board_lane_sorts_by_group, dict):
                board_lane_sorts_by_group = {}
        except Exception:
            board_lane_sorts_by_group = {}
        try:
            board_card_density_by_group = json.loads(
                ui.get("board_card_density_by_group", "{}") or "{}"
            )
            if not isinstance(board_card_density_by_group, dict):
                board_card_density_by_group = {}
        except Exception:
            board_card_density_by_group = {}

        # Global settings
        global_settings = {}
        for row in c.execute("SELECT key, value FROM global_settings"):
            try:
                global_settings[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                global_settings[row[0]] = row[1]

        # Schedules
        schedules = {}
        try:
            rows = c.execute("SELECT * FROM schedules").fetchall()
            if rows:
                cols = [d[0] for d in c.description]
                for row in rows:
                    d = dict(zip(cols, row))
                    d["group"] = d.pop("group_name", "")
                    d["enabled"] = bool(d.get("enabled", 1))
                    try:
                        d["labels"] = json.loads(d.get("labels", "[]"))
                    except (json.JSONDecodeError, TypeError):
                        d["labels"] = []
                    try:
                        d["action_vars"] = json.loads(
                            d.get("action_vars", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        d["action_vars"] = {}
                    schedules[d["id"]] = d
        except Exception:
            pass  # table may not exist yet on first load

        return {
            "agents": agents,
            "groups": groups,
            "group_slugs": group_slugs,
            "group_settings": group_settings,
            "board_lanes": board_lanes,
            "board_tasks": board_tasks,
            "schedules": schedules,
            "panel_active": ui.get("panel_active", "")
                or ("board" if ui.get("board_panel_open", "False") == "True"
                    else ""),
            "board_panel_height": int(ui.get("board_panel_height", "0")),
            "board_filters_by_group": board_filters_by_group,
            "board_saved_views_by_group": board_saved_views_by_group,
            "board_lane_sorts_by_group": board_lane_sorts_by_group,
            "board_card_density_by_group": board_card_density_by_group,
            "global_settings": global_settings,
        }

    def has_data(self) -> bool:
        """Check if the DB has any persisted state."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM agents").fetchone()
        groups = self._conn.execute(
            "SELECT COUNT(*) FROM groups").fetchone()
        return (row and row[0] > 0) or (groups and groups[0] > 0)

    # -- Migration ----------------------------------------------------------

    def migrate_from_json(self, json_path: Path):
        """Import state from a state.json file into SQLite."""
        if not json_path.exists():
            return False
        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cannot read %s for migration: %s", json_path, exc)
            return False

        log.info("Migrating state from %s to SQLite", json_path)
        self.save_all(data)

        # Rename to .bak
        bak = json_path.with_suffix(".json.bak")
        try:
            json_path.rename(bak)
            log.info("Renamed %s → %s", json_path.name, bak.name)
        except OSError as exc:
            log.warning("Could not rename %s: %s", json_path, exc)

        return True
