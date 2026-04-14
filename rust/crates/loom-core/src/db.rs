//! SQLite persistence layer.
//!
//! Mirrors `loom/db.py` + `loom/db_schema.py`. WAL-mode sqlite, targeted write
//! methods, load-all on startup. Internally the connection is held in a
//! `Mutex` so the state layer can be mutated from the async runtime and
//! persisted via `tokio::task::spawn_blocking`.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use rusqlite::{params, Connection, OptionalExtension};
use tokio::sync::Mutex;

use crate::state::{
    AgentCell, BoardTask, GlobalSettings, GroupSettings, MatrixState, MemoryEntry, MemoryLink,
    Schedule, WeaverSettings,
};

const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    parent_id TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    name TEXT PRIMARY KEY,
    slug TEXT NOT NULL DEFAULT '',
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
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_tasks (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_lanes (
    position INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weaver_settings (
    group_name TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weaver_worklog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    entry TEXT NOT NULL,
    ts REAL NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_worklog_group ON weaver_worklog(group_name);
CREATE INDEX IF NOT EXISTS idx_memory_group ON memory_entries(group_name);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(scope_kind, scope_ref);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_memory_pinned ON memory_entries(pinned);
CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_kind, target_ref);
"#;

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
        Ok(Self { inner: Arc::new(Mutex::new(conn)), path: Arc::new(path) })
    }

    /// Open an in-memory db — used by tests.
    pub fn in_memory() -> crate::Result<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(SCHEMA_SQL)?;
        Ok(Self { inner: Arc::new(Mutex::new(conn)), path: Arc::new(PathBuf::from(":memory:")) })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    // --- save methods ------------------------------------------------------

    pub async fn save_agent(&self, agent: &AgentCell) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(agent)?;
        conn.execute(
            "INSERT OR REPLACE INTO agents(id, slug, name, group_name, parent_id, data)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![agent.id, agent.slug, agent.name, agent.group, agent.parent_id, data],
        )?;
        Ok(())
    }

    pub async fn delete_agent(&self, id: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM agents WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub async fn save_group(&self, name: &str, slug: &str, ordinal: i64) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute(
            "INSERT OR REPLACE INTO groups(name, slug, ordinal) VALUES (?1, ?2, ?3)",
            params![name, slug, ordinal],
        )?;
        Ok(())
    }

    pub async fn delete_group(&self, name: &str) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM groups WHERE name = ?1", params![name])?;
        conn.execute("DELETE FROM group_members WHERE group_name = ?1", params![name])?;
        conn.execute("DELETE FROM group_settings WHERE group_name = ?1", params![name])?;
        Ok(())
    }

    pub async fn save_group_members(
        &self,
        group: &str,
        members: &[String],
    ) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        conn.execute("DELETE FROM group_members WHERE group_name = ?1", params![group])?;
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
        conn.execute(
            "INSERT OR REPLACE INTO group_settings(group_name, data) VALUES (?1, ?2)",
            params![group, data],
        )?;
        Ok(())
    }

    pub async fn save_global_settings(&self, settings: &GlobalSettings) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(settings)?;
        conn.execute(
            "INSERT OR REPLACE INTO global_settings(id, data) VALUES (1, ?1)",
            params![data],
        )?;
        Ok(())
    }

    pub async fn save_board_task(&self, task: &BoardTask) -> crate::Result<()> {
        let conn = self.inner.lock().await;
        let data = serde_json::to_string(task)?;
        conn.execute(
            "INSERT OR REPLACE INTO board_tasks(id, slug, group_name, data) VALUES (?1, ?2, ?3, ?4)",
            params![task.id, task.slug, task.group, data],
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
        conn.execute(
            "INSERT OR REPLACE INTO schedules(id, data) VALUES (?1, ?2)",
            params![schedule.id, data],
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
        conn.execute(
            "INSERT OR REPLACE INTO weaver_settings(group_name, data) VALUES (?1, ?2)",
            params![group, data],
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
            params![link.entry_id, link.target_kind, link.target_ref, link.created_at],
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

    // --- load all ----------------------------------------------------------

    pub async fn load_all(&self) -> crate::Result<MatrixState> {
        let conn = self.inner.lock().await;
        let mut state = MatrixState::new();

        // groups
        let mut groups: Vec<(String, String, i64)> = Vec::new();
        {
            let mut stmt = conn.prepare("SELECT name, slug, ordinal FROM groups ORDER BY ordinal")?;
            let rows = stmt.query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?))
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
            let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for row in rows {
                let (group, aid) = row?;
                state.groups.entry(group).or_default().push(aid);
            }
        }

        // agents
        {
            let mut stmt = conn.prepare("SELECT data FROM agents")?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
            for data in rows {
                let json = data?;
                match serde_json::from_str::<AgentCell>(&json) {
                    Ok(mut agent) => {
                        // Clear ephemeral fields on load.
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

                        if !agent.parent_id.is_empty() {
                            state
                                .children
                                .entry(agent.parent_id.clone())
                                .or_default()
                                .push(agent.id.clone());
                        }
                        state.agents.insert(agent.id.clone(), agent);
                    }
                    Err(err) => {
                        tracing::warn!("failed to deserialize agent row: {err}");
                    }
                }
            }
        }

        // group settings
        {
            let mut stmt = conn.prepare("SELECT group_name, data FROM group_settings")?;
            let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for row in rows {
                let (name, data) = row?;
                if let Ok(s) = serde_json::from_str::<GroupSettings>(&data) {
                    state.group_settings.insert(name, s);
                }
            }
        }

        // global settings
        {
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
            let mut stmt = conn.prepare("SELECT data FROM board_tasks")?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
            for r in rows {
                let data = r?;
                match serde_json::from_str::<BoardTask>(&data) {
                    Ok(task) => {
                        state
                            .tasks_by_group
                            .entry(task.group.clone())
                            .or_default()
                            .insert(task.id.clone());
                        state.board_tasks.insert(task.id.clone(), task);
                    }
                    Err(err) => tracing::warn!("failed to deserialize task row: {err}"),
                }
            }
        }

        // schedules
        {
            let mut stmt = conn.prepare("SELECT data FROM schedules")?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
            for r in rows {
                let data = r?;
                if let Ok(s) = serde_json::from_str::<Schedule>(&data) {
                    state.schedules.insert(s.id.clone(), s);
                }
            }
        }

        // weaver settings
        {
            let mut stmt = conn.prepare("SELECT group_name, data FROM weaver_settings")?;
            let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for r in rows {
                let (g, data) = r?;
                if let Ok(s) = serde_json::from_str::<WeaverSettings>(&data) {
                    state.weaver_settings.insert(g, s);
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
                    if let Ok(e) =
                        serde_json::from_str::<crate::state::DockEdges>(&s)
                    {
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
                    if let Ok(map) =
                        serde_json::from_str::<std::collections::HashMap<String, serde_json::Value>>(&s)
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
    use crate::state::AgentCell;

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
}
