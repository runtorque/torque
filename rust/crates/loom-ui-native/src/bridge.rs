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
use loom_server::app::AppState;
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
        }
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
}
