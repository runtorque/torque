//! Glue between the AppKit UI and the engine.
//!
//! The UI calls `EngineBridge::dispatch(cmd, payload)` to mutate state. State
//! changes flow back through an async watcher on `AppState.bus` which is
//! marshalled onto the main thread by the window controller.

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
}

impl EngineBridge {
    pub fn new(state: AppState) -> Self {
        Self { state, runtime: tokio::runtime::Handle::current() }
    }

    pub fn cmd_ctx(&self) -> CmdContext {
        CmdContext {
            state: self.state.state.clone(),
            db: self.state.db.clone(),
            bus: self.state.bus.clone(),
            pty: self.state.pty.clone(),
            ui_agents: self.state.ui_agents.clone(),
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

    /// Block the current thread until the future finishes, using the tokio
    /// runtime that owns the engine. Safe to call from the main (UI) thread
    /// because we don't hold any Cocoa locks.
    pub fn block_on<F, T>(&self, fut: F) -> T
    where
        F: std::future::Future<Output = T> + Send + 'static,
        T: Send + 'static,
    {
        let handle = self.runtime.clone();
        std::thread::scope(|s| {
            let h = s.spawn(|| handle.block_on(fut));
            h.join().unwrap()
        })
    }

    /// Execute a command in the engine. Mirrors the HTTP `/api/cmd` handler
    /// but stays in-process.
    pub fn dispatch(&self, cmd: &str, body: Value) -> Result<Value, CmdError> {
        let ctx = self.cmd_ctx();
        let cmd = cmd.to_string();
        let mut body = body;
        if let Some(obj) = body.as_object_mut() {
            obj.insert("cmd".into(), Value::String(cmd.clone()));
        }
        self.block_on(async move {
            loom_server::commands::dispatch_command(&ctx, &cmd, &body).await
        })
    }

    /// Returns a clone of the full state (for the UI to snapshot).
    pub fn snapshot(&self) -> MatrixStateSnapshot {
        let state = self.state_arc();
        self.block_on(async move {
            let st = state.lock().await;
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
            }
        })
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
}

impl MatrixStateSnapshot {
    pub fn find_agent(&self, id: &str) -> Option<&loom_core::state::AgentCell> {
        self.agents.iter().find(|a| a.id == id)
    }
}

/// Resolve the command + working directory for a given agent cell.
///
/// Priority:
///   1. `cell.command` override if explicitly set.
///   2. `global_settings.default_command` if cell is an agent and override is
///      empty.
///   3. Fallback per cell_type: `"claude"` for agents, `"/bin/zsh"` for
///      terminals.
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
        "/bin/zsh".to_string()
    }
}

/// Working directory for the PTY — `cell.directory` if set, else None (lets
/// the shell inherit the current process's cwd).
pub fn resolve_cwd(cell: &loom_core::state::AgentCell) -> Option<String> {
    if cell.directory.is_empty() {
        None
    } else {
        Some(cell.directory.clone())
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
    fn resolve_command_terminal_uses_zsh_fallback() {
        let mut cell = AgentCell::new("a", "Logs", "g");
        cell.cell_type = "terminal".into();
        assert_eq!(resolve_command(&cell, ""), "/bin/zsh");
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
        assert_eq!(resolve_command(&cell, "codex"), "/bin/zsh");
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
}
