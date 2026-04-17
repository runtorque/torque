//! Glue between the AppKit UI and the engine.
//!
//! The UI polls `EngineBridge::snapshot()` every tick to render. To keep the
//! main (Cocoa) thread free of tokio lock contention, the bridge maintains a
//! cached snapshot that is refreshed by a background tokio task subscribed to
//! the engine's event bus. The main thread only takes a `std::sync::RwLock`
//! read lock — never a tokio mutex — so rendering is never blocked by a
//! long-running engine operation.
//!
//! Command dispatch is fire-and-forget: `bridge.dispatch()` spawns the future
//! on the runtime and returns immediately. Errors are logged. If a caller
//! needs the result synchronously, use `dispatch_blocking`.

use std::sync::Arc;

use serde_json::Value;
use tokio::sync::broadcast;

use loom_core::events::OutMessage;
use loom_core::state::MatrixState;
use loom_server::app::{AppState, UiAgentRegistry};
use loom_server::commands::{CmdContext, CmdError};

#[derive(Clone)]
pub struct EngineBridge {
    pub state: AppState,
    /// Tokio runtime handle — the UI sits on the main thread (Cocoa) so it
    /// needs to hand async work to tokio instead of calling .await directly.
    runtime: tokio::runtime::Handle,
    /// Cached snapshot refreshed by a background task. Reads from the main
    /// thread are cheap (std RwLock read) and never wait on a tokio mutex.
    snapshot_cache: Arc<std::sync::RwLock<MatrixStateSnapshot>>,
}

impl EngineBridge {
    pub fn new(state: AppState) -> Self {
        let runtime = tokio::runtime::Handle::current();

        // Seed the cache with a blocking snapshot before returning. The UI's
        // first tick will see real data rather than a stub.
        let initial = runtime.block_on(build_snapshot(&state));
        let snapshot_cache = Arc::new(std::sync::RwLock::new(initial));

        // Spawn the refresher. It rebuilds the cache whenever the engine
        // emits a delta / snapshot / event. A lagged receiver rebuilds from
        // the current state.
        {
            let state_clone = state.clone();
            let cache = snapshot_cache.clone();
            runtime.spawn(async move {
                let mut rx = state_clone.bus.subscribe();
                loop {
                    match rx.recv().await {
                        Ok(_) | Err(broadcast::error::RecvError::Lagged(_)) => {
                            let snap = build_snapshot(&state_clone).await;
                            if let Ok(mut w) = cache.write() {
                                *w = snap;
                            }
                        }
                        Err(broadcast::error::RecvError::Closed) => break,
                    }
                }
            });
        }

        Self {
            state,
            runtime,
            snapshot_cache,
        }
    }

    pub fn cmd_ctx(&self) -> CmdContext {
        CmdContext {
            state: self.state.state.clone(),
            db: self.state.db.clone(),
            bus: self.state.bus.clone(),
            pty: self.state.pty.clone(),
            ui_agents: self.state.ui_agents.clone(),
            terminal_bridge: self.state.terminal_bridge.clone(),
            terminals: self.state.terminals.clone(),
            weaver_buffer: self.state.weaver_buffer.clone(),
            notifier: self.state.notifier.clone(),
        }
    }

    /// The shared UI-attached-agents registry. The AppKit layer registers
    /// GhosttyView-backed agents here so dispatch routes prompts to them
    /// instead of spawning a duplicate PTY.
    pub fn ui_agents(&self) -> &UiAgentRegistry {
        &self.state.ui_agents
    }

    pub fn subscribe(&self) -> broadcast::Receiver<OutMessage> {
        self.state.bus.subscribe()
    }

    pub fn state_arc(&self) -> Arc<tokio::sync::Mutex<MatrixState>> {
        self.state.state.clone()
    }

    /// Fire-and-forget command dispatch. The future runs on the tokio
    /// runtime; errors are logged. Returns immediately so the UI main thread
    /// stays responsive. Use `dispatch_blocking` if the caller truly needs
    /// the result synchronously.
    pub fn dispatch(&self, cmd: &str, body: Value) -> Result<Value, CmdError> {
        let ctx = self.cmd_ctx();
        let cmd_str = cmd.to_string();
        let mut body = body;
        if let Some(obj) = body.as_object_mut() {
            obj.insert("cmd".into(), Value::String(cmd_str.clone()));
        }
        self.runtime.spawn(async move {
            if let Err(err) = loom_server::commands::dispatch_command(&ctx, &cmd_str, &body).await {
                tracing::warn!("dispatch({cmd_str}) failed: {err:?}");
            }
        });
        Ok(serde_json::json!({ "ok": true }))
    }

    /// Blocking dispatch — used when the caller needs the command's result
    /// value. Spawns a task on the runtime and blocks the current thread
    /// until it resolves. Avoid on the UI main thread unless necessary.
    pub fn dispatch_blocking(&self, cmd: &str, body: Value) -> Result<Value, CmdError> {
        let ctx = self.cmd_ctx();
        let cmd_str = cmd.to_string();
        let mut body = body;
        if let Some(obj) = body.as_object_mut() {
            obj.insert("cmd".into(), Value::String(cmd_str.clone()));
        }
        let handle = self.runtime.clone();
        let fut = async move { loom_server::commands::dispatch_command(&ctx, &cmd_str, &body).await };
        // Use a oneshot channel + spawn so we never call block_on on the
        // current thread (which may be tokio-owned or hold AppKit locks).
        let (tx, rx) = std::sync::mpsc::channel();
        handle.spawn(async move {
            let _ = tx.send(fut.await);
        });
        rx.recv().unwrap_or_else(|_| Err(CmdError::BadRequest(
            "dispatch oneshot channel dropped".into(),
        )))
    }

    /// Return the most recent cached snapshot. O(1); never blocks on tokio.
    pub fn snapshot(&self) -> MatrixStateSnapshot {
        self.snapshot_cache
            .read()
            .expect("snapshot cache poisoned")
            .clone()
    }
}

/// Small cloneable snapshot of the parts of state the UI needs to render.
/// Avoids holding the lock across the Cocoa call boundary.
#[derive(Debug, Clone)]
pub struct MatrixStateSnapshot {
    pub agents: Vec<loom_core::state::AgentCell>,
    pub groups_order: Vec<String>,
    pub group_slugs: std::collections::HashMap<String, String>,
    pub groups: std::collections::HashMap<String, Vec<String>>,
    pub board_lanes: Vec<String>,
    pub board_tasks: Vec<loom_core::state::BoardTask>,
    /// `GlobalSettings.default_command` — empty if unset.
    pub global_default_command: String,
    pub selected_agent_id: Option<String>,
    /// Effective center-zone layout (already resolved through default).
    pub content_layout: loom_core::state::LayoutNode,
    /// Full dock layout — center + edges + ratios. The UI uses this to
    /// build the outer window shell.
    pub dock_layout: loom_core::state::DockLayout,
}

impl MatrixStateSnapshot {
    pub fn find_agent(&self, id: &str) -> Option<&loom_core::state::AgentCell> {
        self.agents.iter().find(|a| a.id == id)
    }
}

/// Collect a fresh snapshot by locking the engine state briefly.
async fn build_snapshot(state: &AppState) -> MatrixStateSnapshot {
    let st = state.state.lock().await;
    MatrixStateSnapshot {
        agents: st.agents.values().cloned().collect(),
        groups_order: st.groups_order.clone(),
        group_slugs: st.group_slugs.clone(),
        groups: st
            .groups
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
        board_lanes: st.board_lanes.clone(),
        board_tasks: st.board_tasks.values().cloned().collect(),
        global_default_command: st.global_settings.default_command.clone(),
        selected_agent_id: st.selected_agent_id.clone(),
        content_layout: st.effective_layout(),
        dock_layout: st.effective_dock_layout(),
    }
}

/// Resolve the command + working directory for a given agent cell.
///
/// Priority:
///   1. `cell.command` override if explicitly set.
///   2. `global_settings.default_command` if cell is an agent and override is
///      empty.
///   3. Fallback per cell_type: `"claude"` for agents, empty for terminals.
///      (Empty means: let Ghostty spawn its default login shell so `.zshrc`
///      / `.zprofile` are sourced — see `GhosttyView::ensure_surface`.)
pub fn resolve_command(cell: &loom_core::state::AgentCell, global_default: &str) -> String {
    if !cell.command.is_empty() {
        return cell.command.clone();
    }
    let is_agent = cell.cell_type != "terminal";
    if is_agent && !global_default.is_empty() {
        return global_default.to_string();
    }
    if is_agent {
        "claude".to_string()
    } else {
        // Empty → Ghostty spawns its default login shell, which sources
        // the user's shell init scripts (.zprofile, .zshrc, etc.).
        String::new()
    }
}

/// Working directory for the PTY — `cell.directory` if set, else None (lets
/// the shell inherit the current process's cwd).
pub fn resolve_cwd(cell: &loom_core::state::AgentCell) -> Option<String> {
    if cell.directory.is_empty() {
        None
    } else {
        Some(loom_server::paths::expand_user_path_string(&cell.directory))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use loom_core::state::AgentCell;

    #[test]
    fn resolve_command_agent_uses_claude_fallback() {
        let mut cell = AgentCell::new("a", "Worker", "g");
        cell.cell_type = "agent".into();
        assert_eq!(resolve_command(&cell, ""), "claude");
    }

    #[test]
    fn resolve_command_terminal_returns_empty_for_default_login_shell() {
        // Empty string tells Ghostty to spawn its default login shell so the
        // user's shell init scripts are sourced.
        let mut cell = AgentCell::new("a", "Logs", "g");
        cell.cell_type = "terminal".into();
        assert_eq!(resolve_command(&cell, ""), "");
    }

    #[test]
    fn resolve_command_agent_honors_global_default() {
        let mut cell = AgentCell::new("a", "Worker", "g");
        cell.cell_type = "agent".into();
        assert_eq!(resolve_command(&cell, "codex"), "codex");
    }

    #[test]
    fn resolve_command_terminal_ignores_global_default() {
        // global_default is "boot command for agents" — terminals shouldn't
        // inherit `claude` from it.
        let mut cell = AgentCell::new("a", "Logs", "g");
        cell.cell_type = "terminal".into();
        assert_eq!(resolve_command(&cell, "codex"), "");
    }

    #[test]
    fn resolve_command_cell_override_wins() {
        let mut cell = AgentCell::new("a", "Worker", "g");
        cell.cell_type = "agent".into();
        cell.command = "gemini".into();
        assert_eq!(resolve_command(&cell, "codex"), "gemini");
    }

    #[test]
    fn resolve_cwd_empty_returns_none() {
        let cell = AgentCell::new("a", "Worker", "g");
        assert_eq!(resolve_cwd(&cell), None);
    }

    #[test]
    fn resolve_cwd_propagates_directory() {
        let mut cell = AgentCell::new("a", "Worker", "g");
        cell.directory = "/tmp/x".into();
        assert_eq!(resolve_cwd(&cell).as_deref(), Some("/tmp/x"));
    }

    #[test]
    fn resolve_cwd_expands_home_directory() {
        let mut cell = AgentCell::new("a", "Worker", "g");
        cell.directory = "~/tmp".into();
        assert_eq!(
            resolve_cwd(&cell).as_deref(),
            Some(loom_server::paths::expand_user_path_string("~/tmp").as_str())
        );
    }
}
