//! Thin bridge between the Rust backend and the Python+iTerm2 runtime.

use std::path::PathBuf;
use std::sync::{Arc, Mutex as StdMutex};

use anyhow::{bail, Context, Result};
use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use loom_core::delta::DeltaOp;
use loom_core::events::OutMessage;
use loom_core::state::AgentCell;

use crate::app::AppState;

#[derive(Debug, Clone, Default)]
pub struct TerminalBridgeClient {
    http: reqwest::Client,
    bridge_url: Arc<StdMutex<Option<String>>>,
}

impl TerminalBridgeClient {
    pub fn from_env() -> Self {
        let client = Self::default();
        if let Ok(url) = std::env::var("LOOM_TERMINAL_BRIDGE_URL") {
            client.configure_url(url);
        } else if let Ok(url) = std::env::var("LOOM_RUST_BRIDGE_URL") {
            client.configure_url(url);
        }
        client
    }

    pub fn configure_url(&self, url: impl Into<String>) {
        let url = normalize_bridge_url(url.into());
        let mut slot = self.bridge_url.lock().unwrap_or_else(|e| e.into_inner());
        *slot = url;
    }

    pub fn current_url(&self) -> Option<String> {
        self.bridge_url
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    pub fn is_configured(&self) -> bool {
        self.current_url().is_some()
    }

    fn endpoint(&self, path: &str) -> Result<String> {
        let Some(base) = self.current_url() else {
            bail!("terminal bridge is not configured")
        };
        Ok(format!(
            "{}/{}",
            base.trim_end_matches('/'),
            path.trim_start_matches('/')
        ))
    }

    async fn post(&self, path: &str, payload: Value) -> Result<Value> {
        let url = self.endpoint(path)?;
        let response = self
            .http
            .post(&url)
            .json(&payload)
            .send()
            .await
            .with_context(|| format!("POST {url}"))?;
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        let value: Value = serde_json::from_str(&body).unwrap_or_else(|_| json!({ "raw": body }));
        if !status.is_success() {
            bail!("bridge {} failed with {}: {}", path, status, body);
        }
        if value.get("ok").and_then(|v| v.as_bool()) == Some(false) {
            let message = value
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("bridge call failed");
            bail!("bridge {} error: {}", path, message);
        }
        Ok(value)
    }

    pub async fn create_session(&self, cell: &AgentCell) -> Result<Option<AgentCell>> {
        let value = self
            .post("/bridge/create_session", json!({ "cell": cell }))
            .await?;
        parse_cell_response(value)
    }

    pub async fn update_session(
        &self,
        cell: &AgentCell,
        old_name: &str,
    ) -> Result<Option<AgentCell>> {
        let value = self
            .post(
                "/bridge/update_session",
                json!({ "cell": cell, "old_name": old_name }),
            )
            .await?;
        parse_cell_response(value)
    }

    pub async fn close_session(&self, cell_id: &str, session_id: Option<&str>) -> Result<()> {
        let _ = self
            .post(
                "/bridge/close_session",
                json!({ "cell_id": cell_id, "session_id": session_id }),
            )
            .await?;
        Ok(())
    }

    pub async fn focus_session(&self, cell_id: &str, session_id: Option<&str>) -> Result<bool> {
        let value = self
            .post(
                "/bridge/focus_session",
                json!({ "cell_id": cell_id, "session_id": session_id }),
            )
            .await?;
        Ok(value
            .get("focused")
            .and_then(|v| v.as_bool())
            .unwrap_or(false))
    }

    pub async fn send_text(
        &self,
        cell_id: &str,
        session_id: Option<&str>,
        text: &str,
        settled_submit: bool,
    ) -> Result<()> {
        let _ = self
            .post(
                "/bridge/send_text",
                json!({
                    "cell_id": cell_id,
                    "session_id": session_id,
                    "text": text,
                    "settled_submit": settled_submit,
                }),
            )
            .await?;
        Ok(())
    }

    pub async fn write_input(
        &self,
        cell_id: &str,
        session_id: Option<&str>,
        data: &str,
    ) -> Result<()> {
        let _ = self
            .post(
                "/bridge/write_input",
                json!({ "cell_id": cell_id, "session_id": session_id, "data": data }),
            )
            .await?;
        Ok(())
    }

    pub async fn signal_input_ready(&self, cell_id: &str, session_id: Option<&str>) -> Result<()> {
        let _ = self
            .post(
                "/bridge/signal_input_ready",
                json!({ "cell_id": cell_id, "session_id": session_id }),
            )
            .await?;
        Ok(())
    }
}

fn normalize_bridge_url(value: String) -> Option<String> {
    let trimmed = value.trim().trim_end_matches('/').to_string();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

fn parse_cell_response(value: Value) -> Result<Option<AgentCell>> {
    let Some(cell_value) = value.get("cell") else {
        return Ok(None);
    };
    let cell: AgentCell =
        serde_json::from_value(cell_value.clone()).context("deserialize bridge cell response")?;
    Ok(Some(cell))
}

#[derive(Debug, Clone, Deserialize)]
struct ConfigureRequest {
    #[serde(default)]
    bridge_url: String,
    #[allow(dead_code)]
    #[serde(default)]
    profile: String,
    #[allow(dead_code)]
    #[serde(default)]
    data_dir: String,
}

#[derive(Debug, Clone, Deserialize)]
struct FocusCallbackRequest {
    #[serde(default)]
    active_session_id: String,
    #[serde(default)]
    current_window_id: String,
}

#[derive(Debug, Clone, Deserialize)]
struct AgentSyncRequest {
    cell: AgentCell,
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/terminal-bridge/runtime", get(get_runtime))
        .route("/api/terminal-bridge/configure", post(configure_bridge))
        .route("/api/terminal-bridge/focus", post(push_focus_update))
        .route("/api/terminal-bridge/agent-sync", post(push_agent_sync))
}

async fn get_runtime(State(app): State<AppState>) -> Json<Value> {
    let port = std::env::var("LOOM_PORT")
        .ok()
        .and_then(|v| v.parse::<u16>().ok())
        .unwrap_or(loom_core::config::DEFAULT_PORT);
    let bridge_url = app.terminal_bridge.current_url();
    Json(json!({
        "runtime": {
            "profile": std::env::var("LOOM_PROFILE").unwrap_or_default(),
            "data_dir": loom_core::config::data_dir(),
            "port": port,
            "bridge_url": bridge_url,
            "terminal_backend": std::env::var("LOOM_TERMINAL_BACKEND").unwrap_or_else(|_| "pty".into()),
            "default_command": loom_core::config::default_command(),
        }
    }))
}

async fn configure_bridge(
    State(app): State<AppState>,
    Json(req): Json<ConfigureRequest>,
) -> Json<Value> {
    app.terminal_bridge.configure_url(req.bridge_url.clone());
    Json(json!({
        "ok": true,
        "bridge_url": app.terminal_bridge.current_url(),
    }))
}

async fn push_focus_update(
    State(app): State<AppState>,
    Json(req): Json<FocusCallbackRequest>,
) -> Json<Value> {
    {
        let mut st = app.state.lock().await;
        st.active_session_id = empty_to_none(req.active_session_id.clone());
        st.current_window_id = empty_to_none(req.current_window_id.clone());
        let active_session_id = st.active_session_id.clone();
        let current_window_id = st.current_window_id.clone();
        st.emit(DeltaOp::FocusUpdate {
            active_session_id,
            current_window_id,
        });
    }
    flush_state(&app).await;
    Json(json!({ "ok": true }))
}

async fn push_agent_sync(
    State(app): State<AppState>,
    Json(req): Json<AgentSyncRequest>,
) -> Json<Value> {
    let synced = {
        let mut st = app.state.lock().await;
        let Some(existing) = st.agents.get(&req.cell.id).cloned() else {
            return Json(json!({ "ok": true, "ignored": true }));
        };
        let mut cell = existing;
        apply_synced_fields(&mut cell, &req.cell);
        st.agents.insert(req.cell.id.clone(), cell.clone());
        st.emit_agent(&req.cell.id);
        cell
    };
    let _ = app.db.save_agent(&synced).await;
    flush_state(&app).await;
    Json(json!({ "ok": true, "cell": synced }))
}

async fn flush_state(app: &AppState) {
    let mut st = app.state.lock().await;
    if let Some((seq, ops)) = st.drain_deltas() {
        app.bus.send(OutMessage::Delta { seq, ops });
    }
}

fn empty_to_none(value: String) -> Option<String> {
    let trimmed = value.trim().to_string();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

fn apply_synced_fields(dst: &mut AgentCell, src: &AgentCell) {
    dst.session_id = src.session_id.clone();
    dst.window_id = src.window_id.clone();
    dst.status = src.status.clone();
    dst.current_process = src.current_process.clone();
    dst.current_path = src.current_path.clone();
    dst.current_branch = src.current_branch.clone();
    dst.git_root = src.git_root.clone();
    dst.agent_type = src.agent_type.clone();
    dst.agent_session_id = src.agent_session_id.clone();
    dst.activity = src.activity.clone();
    dst.activity_detail = src.activity_detail.clone();
    dst.last_event_at = src.last_event_at;
    dst.last_event_text = src.last_event_text.clone();
    dst.session_tokens_in = src.session_tokens_in;
    dst.session_tokens_out = src.session_tokens_out;
    dst.error_message = src.error_message.clone();
    dst.needs_attention = src.needs_attention;
    dst.last_summary = src.last_summary.clone();
    dst.current_task_id = src.current_task_id.clone();
    dst.session_resume = src.session_resume;
    dst.idle_timeout = src.idle_timeout;
    dst.worktree_dirty = src.worktree_dirty;
    dst.worktree_diff = src.worktree_diff.clone();
    dst.worktree_changed_files = src.worktree_changed_files.clone();
    dst.worktree_checkpoints = src.worktree_checkpoints;
    dst.worktree_behind = src.worktree_behind;
    dst.worktree_ahead = src.worktree_ahead;
    dst.worktree_merged = src.worktree_merged;
    dst.mcp_messages = src.mcp_messages.clone();
    dst.last_checkpoint_at = src.last_checkpoint_at;
    dst.pending_weaver_message = src.pending_weaver_message;
}

pub fn bridge_manages_cell(client: &TerminalBridgeClient, cell: &AgentCell) -> bool {
    client.is_configured() && cell.terminal_backend != "pty"
}

pub fn bridge_target_data_dir() -> PathBuf {
    loom_core::config::data_dir()
}
