//! Hook receiver — POST /events
//!
//! Claude Code / Codex post hook payloads here. The handler classifies the
//! payload via the registered adapter and updates agent state.

use axum::extract::State;
use axum::http::HeaderMap;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Json;
use axum::Router;
use serde_json::json;

use crate::app::AppState;
use loom_core::events::OutMessage;

pub fn routes() -> Router<AppState> {
    Router::new().route("/events", post(handle_event))
}

async fn handle_event(
    State(app): State<AppState>,
    headers: HeaderMap,
    Json(mut payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    tracing::debug!("hook payload: {}", payload);
    let header_cell_id = headers
        .get("X-Loom-Cell-Id")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_string();
    let body_cell_id = payload
        .get("cell_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let initial_cell_id = if !body_cell_id.is_empty() {
        body_cell_id.to_string()
    } else {
        header_cell_id.clone()
    };
    let cwd = payload
        .get("cwd")
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string());
    let resolved_cell_id = {
        let st = app.state.lock().await;
        if !initial_cell_id.is_empty() && st.agents.contains_key(&initial_cell_id) {
            Some(initial_cell_id.clone())
        } else if let Some(cwd) = cwd.as_deref() {
            let target = canonical_path(cwd);
            st.agents
                .values()
                .find(|cell| {
                    cell.session_id.is_some()
                        && !cell.directory.is_empty()
                        && canonical_path(&cell.directory) == target
                })
                .map(|cell| cell.id.clone())
        } else {
            None
        }
    };
    let Some(cell_id) = resolved_cell_id else {
        return (StatusCode::OK, Json(json!({ "ok": true, "events": 0 })));
    };
    if payload.get("cell_id").is_none() {
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("cell_id".into(), json!(cell_id));
        }
    }
    let adapter = {
        let st = app.state.lock().await;
        st.agents.get(&cell_id).and_then(|cell| {
            if !cell.agent_type.is_empty() {
                loom_adapters::registry::get_adapter(&cell.agent_type)
            } else {
                loom_adapters::registry::detect_by_command(&cell.command)
                    .and_then(loom_adapters::registry::get_adapter)
            }
        })
    };
    let events = adapter
        .map(|adapter| adapter.parse_hook(&payload))
        .unwrap_or_default();
    for event in &events {
        let (agent, panel) = {
            let mut st = app.state.lock().await;
            let (agent, panel) = {
                let Some(cell) = st.agents.get_mut(&event.cell_id) else {
                    continue;
                };
                match event.kind.as_str() {
                    "session_start" => {
                        cell.status = "running".into();
                        cell.activity.clear();
                        cell.activity_detail.clear();
                        cell.error_message.clear();
                        cell.needs_attention = false;
                    }
                    "session_end" => {
                        cell.activity.clear();
                        cell.activity_detail.clear();
                        cell.needs_attention = false;
                        if cell.status != "stopped" {
                            cell.status = "idle".into();
                        }
                        if !event.summary.is_empty() {
                            cell.last_summary = event.summary.clone();
                        }
                    }
                    "activity_change" | "progress" => {
                        cell.activity = "running".into();
                        cell.activity_detail = event.detail.clone();
                        cell.needs_attention = false;
                    }
                    "tool_start" => {
                        cell.activity = "tool_call".into();
                        cell.activity_detail = event.detail.clone();
                        cell.needs_attention = false;
                    }
                    "tool_end" => {
                        cell.activity = "thinking".into();
                        cell.activity_detail.clear();
                    }
                    "waiting" => {
                        cell.activity = "waiting".into();
                        cell.activity_detail = event.detail.clone();
                        cell.needs_attention = true;
                    }
                    "error" => {
                        cell.activity = "error".into();
                        cell.error_message = if !event.error_message.is_empty() {
                            event.error_message.clone()
                        } else {
                            event.detail.clone()
                        };
                        cell.needs_attention = true;
                    }
                    "cost_update" => {
                        cell.session_tokens_in += event.tokens_in;
                        cell.session_tokens_out += event.tokens_out;
                    }
                    _ => {}
                }
                cell.last_event_at = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
                let agent = cell.clone();
                let panel = match event.kind.as_str() {
                    "session_start" => Some((
                        "agent_started".to_string(),
                        "Session started".to_string(),
                        false,
                    )),
                    "session_end" => Some((
                        "agent_finished".to_string(),
                        if event.summary.is_empty() {
                            "Session ended".to_string()
                        } else {
                            event.summary.clone()
                        },
                        false,
                    )),
                    "progress" | "activity_change" => {
                        Some(("agent_progress".to_string(), event.detail.clone(), true))
                    }
                    "waiting" => Some(("agent_waiting".to_string(), event.detail.clone(), false)),
                    "error" => Some((
                        "agent_error".to_string(),
                        if agent.error_message.is_empty() {
                            event.detail.clone()
                        } else {
                            agent.error_message.clone()
                        },
                        false,
                    )),
                    _ => None,
                };
                (agent, panel)
            };
            st.emit_agent(&event.cell_id);
            (agent, panel)
        };
        let _ = app.db.save_agent(&agent).await;
        if !matches!(event.kind.as_str(), "cost_update") {
            let linked_task = agent.current_task_id.clone();
            let _ = app
                .db
                .save_agent_message_record(
                    &agent.id,
                    &linked_task,
                    chrono::Utc::now().timestamp_millis() as f64 / 1000.0,
                    &event.kind,
                    if !event.detail.is_empty() {
                        &event.detail
                    } else if !event.summary.is_empty() {
                        &event.summary
                    } else {
                        ""
                    },
                )
                .await;
        }
        if let Some((kind, message, replace_last)) = panel {
            let _ = crate::commands::compat::record_panel_event(
                &app.state,
                &app.db,
                &kind,
                &agent.id,
                &agent.name,
                &agent.group,
                &message,
                &agent.current_task_id,
                replace_last,
            )
            .await;
        }
        let mut st = app.state.lock().await;
        if let Some((seq, ops)) = st.drain_deltas() {
            app.bus.send(OutMessage::Delta { seq, ops });
        }
    }
    (StatusCode::OK, Json(json!({ "ok": true })))
}

fn canonical_path(path: &str) -> String {
    let candidate = std::path::Path::new(path);
    std::fs::canonicalize(candidate)
        .unwrap_or_else(|_| candidate.to_path_buf())
        .to_string_lossy()
        .to_string()
}
