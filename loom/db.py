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
    "command", "directory", "tab_color", "icon", "window_id", "parent_id",
    "status", "worktree_path", "worktree_branch", "worktree_repo_root",
    "worktree_base_branch", "agent_type", "agent_session_id",
]

# GroupSettings fields that store dicts — persisted as JSON text.
_GS_JSON_FIELDS = {"env_vars", "agent_env_vars", "terminal_env_vars"}

# GroupSettings fields that are booleans — stored as INTEGER 0/1.
_GS_BOOL_FIELDS = {
    "collapsed_default", "filter_by_window", "git_worktree",
    "worktree_auto_checkpoint", "worktree_merge_squash",
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
    window_id             TEXT NOT NULL DEFAULT '',
    parent_id             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'stopped',
    worktree_path         TEXT NOT NULL DEFAULT '',
    worktree_branch       TEXT NOT NULL DEFAULT '',
    worktree_repo_root    TEXT NOT NULL DEFAULT '',
    worktree_base_branch  TEXT NOT NULL DEFAULT '',
    agent_type            TEXT NOT NULL DEFAULT '',
    agent_session_id      TEXT NOT NULL DEFAULT ''
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
    auto_terminals              INTEGER NOT NULL DEFAULT 0,
    max_agents                  INTEGER NOT NULL DEFAULT 0,
    collapsed_default           INTEGER NOT NULL DEFAULT 0,
    filter_by_window            INTEGER NOT NULL DEFAULT 0,
    agent_directory             TEXT NOT NULL DEFAULT '',
    agent_profile               TEXT NOT NULL DEFAULT '',
    agent_shell                 TEXT NOT NULL DEFAULT '',
    agent_tab_color             TEXT NOT NULL DEFAULT '',
    agent_env_vars              TEXT NOT NULL DEFAULT '{}',
    agent_boot_command          TEXT NOT NULL DEFAULT '',
    git_worktree                INTEGER NOT NULL DEFAULT 0,
    worktree_base_dir           TEXT NOT NULL DEFAULT '.loom/worktrees',
    worktree_base_branch        TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint    INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash       INTEGER NOT NULL DEFAULT 1,
    worktree_merge_instructions TEXT NOT NULL DEFAULT '',
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
    terminal_always_custom_dialog INTEGER NOT NULL DEFAULT 0,
    terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0,
    dispatch_lane               TEXT NOT NULL DEFAULT 'In Progress',
    dispatch_auto_terminals     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS board_tasks (
    id             TEXT PRIMARY KEY,
    task           TEXT NOT NULL,
    slug           TEXT NOT NULL DEFAULT '',
    group_name     TEXT NOT NULL DEFAULT '',
    template_name  TEXT NOT NULL DEFAULT '',
    template_vars  TEXT NOT NULL DEFAULT '{}',
    instructions   TEXT NOT NULL DEFAULT '',
    context        TEXT NOT NULL DEFAULT '',
    criteria       TEXT NOT NULL DEFAULT '',
    lane           TEXT NOT NULL DEFAULT 'Backlog',
    position       INTEGER NOT NULL DEFAULT 0,
    assignee       TEXT NOT NULL DEFAULT '',
    agent_id       TEXT NOT NULL DEFAULT '',
    labels         TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL DEFAULT '',
    external_id    TEXT NOT NULL DEFAULT '',
    external_url   TEXT NOT NULL DEFAULT ''
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
        # Migrate: add template_name and template_vars columns to board_tasks
        for col, default in [("template_name", "''"),
                             ("template_vars", "'{}'")]:
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
                 command, directory, tab_color, icon, window_id, parent_id,
                 status, worktree_path, worktree_branch, worktree_repo_root,
                 worktree_base_branch, agent_type, agent_session_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cell.id, cell.name, cell.slug, cell.group, cell.cell_type,
            cell.session_id, cell.profile, cell.command, cell.directory,
            cell.tab_color, cell.icon, cell.window_id, cell.parent_id,
            cell.status, cell.worktree_path, cell.worktree_branch,
            cell.worktree_repo_root, cell.worktree_base_branch,
            cell.agent_type, cell.agent_session_id,
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
        template_vars = json.dumps(d.pop("template_vars", {}))
        # Map 'group' to 'group_name' for the DB column
        group_name = d.pop("group", "")
        self._conn.execute("""
            INSERT OR REPLACE INTO board_tasks
                (id, task, slug, group_name, template_name, template_vars,
                 instructions, context, criteria,
                 lane, position, assignee, agent_id, labels, created_at,
                 updated_at, provider, external_id, external_url,
                 parent_task_id, pipeline_depth, pipeline_root_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d["id"], d["task"], d["slug"], group_name,
            d.get("template_name", ""), template_vars,
            d["instructions"], d["context"], d["criteria"],
            d["lane"], d["position"],
            d["assignee"], d["agent_id"], labels, d["created_at"],
            d["updated_at"], d["provider"], d["external_id"],
            d["external_url"],
            d.get("parent_task_id", ""), d.get("pipeline_depth", 0),
            d.get("pipeline_root_id", ""),
        ))
        self._conn.commit()

    def delete_board_task(self, task_id: str):
        self._conn.execute(
            "DELETE FROM board_tasks WHERE id=?", (task_id,))
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
                         window_id, parent_id, status, worktree_path,
                         worktree_branch, worktree_repo_root,
                         worktree_base_branch, agent_type, agent_session_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    a.get("window_id", ""),
                    a.get("parent_id", ""),
                    a.get("status", "stopped"),
                    a.get("worktree_path", ""),
                    a.get("worktree_branch", ""),
                    a.get("worktree_repo_root", ""),
                    a.get("worktree_base_branch", ""),
                    a.get("agent_type", ""),
                    a.get("agent_session_id", ""),
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
                template_vars = json.dumps(d.pop("template_vars", {}))
                group_name = d.pop("group", "")
                c.execute("""
                    INSERT INTO board_tasks
                        (id, task, slug, group_name, template_name,
                         template_vars, instructions, context,
                         criteria, lane, position, assignee, agent_id, labels,
                         created_at, updated_at, provider, external_id,
                         external_url, parent_task_id, pipeline_depth,
                         pipeline_root_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    d.get("id", tid), d.get("task", ""), d.get("slug", ""),
                    group_name, d.get("template_name", ""), template_vars,
                    d.get("instructions", ""),
                    d.get("context", ""), d.get("criteria", ""),
                    d.get("lane", "Backlog"), d.get("position", 0),
                    d.get("assignee", ""), d.get("agent_id", ""), labels,
                    d.get("created_at", ""), d.get("updated_at", ""),
                    d.get("provider", ""), d.get("external_id", ""),
                    d.get("external_url", ""),
                    d.get("parent_task_id", ""), d.get("pipeline_depth", 0),
                    d.get("pipeline_root_id", ""),
                ))

            # UI state
            c.execute("DELETE FROM ui_state")
            for key in ("board_panel_open", "board_panel_height"):
                val = state_dict.get(key)
                if val is not None:
                    c.execute(
                        "INSERT INTO ui_state (key, value) VALUES (?,?)",
                        (key, str(val)))

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
                            d[k] = {}
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
                # Decode template_vars JSON
                try:
                    d["template_vars"] = json.loads(
                        d.get("template_vars", "{}"))
                except (json.JSONDecodeError, TypeError):
                    d["template_vars"] = {}
                board_tasks[d["id"]] = d

        # UI state
        ui = {}
        for row in c.execute("SELECT key, value FROM ui_state"):
            ui[row[0]] = row[1]

        # Global settings
        global_settings = {}
        for row in c.execute("SELECT key, value FROM global_settings"):
            try:
                global_settings[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                global_settings[row[0]] = row[1]

        return {
            "agents": agents,
            "groups": groups,
            "group_slugs": group_slugs,
            "group_settings": group_settings,
            "board_lanes": board_lanes,
            "board_tasks": board_tasks,
            "board_panel_open": ui.get("board_panel_open", "False") == "True",
            "board_panel_height": int(ui.get("board_panel_height", "0")),
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
