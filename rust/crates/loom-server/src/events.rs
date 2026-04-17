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
                        cell.last_event_text = "Session started".into();
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
                        cell.last_event_text = "Session ended".into();
                    }
                    "activity_change" | "progress" => {
                        // Claude Code can emit trailing `thinking` /
                        // empty-detail progress events after a turn
                        // has ended (e.g. SubagentStop tails). Do not
                        // revive an idle/stopped agent on those
                        // passive events — mirrors loom/events.py
                        // `_apply` guard.
                        let passive_tail = cell.status != "running"
                            && cell.activity.is_empty()
                            && event.detail.is_empty();
                        if !passive_tail {
                            cell.activity = "running".into();
                            cell.activity_detail = event.detail.clone();
                            cell.needs_attention = false;
                            if !event.detail.is_empty() {
                                cell.last_event_text = event.detail.clone();
                            }
                        }
                    }
                    "tool_start" => {
                        cell.activity = "tool_call".into();
                        cell.activity_detail = event.detail.clone();
                        cell.needs_attention = false;
                        if !event.detail.is_empty() {
                            cell.last_event_text = event.detail.clone();
                        }
                    }
                    "tool_end" => {
                        cell.activity = "thinking".into();
                        cell.activity_detail.clear();
                    }
                    "waiting" => {
                        cell.activity = "waiting".into();
                        cell.activity_detail = event.detail.clone();
                        cell.needs_attention = true;
                        if !event.detail.is_empty() {
                            cell.last_event_text = event.detail.clone();
                        } else {
                            cell.last_event_text = "Waiting on input".into();
                        }
                    }
                    "error" => {
                        cell.activity = "error".into();
                        cell.error_message = if !event.error_message.is_empty() {
                            event.error_message.clone()
                        } else {
                            event.detail.clone()
                        };
                        cell.needs_attention = true;
                        cell.last_event_text = if !cell.error_message.is_empty() {
                            cell.error_message.clone()
                        } else {
                            "Error".into()
                        };
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
                        // Same passive-tail suppression as the
                        // state-update block above — don't flood the
                        // event log with meaningless `agent_progress`
                        // rows that arrive after session_end.
                        let passive_tail = agent.status != "running"
                            && agent.activity.is_empty()
                            && event.detail.is_empty();
                        if passive_tail {
                            None
                        } else {
                            Some((
                                "agent_progress".to_string(),
                                event.detail.clone(),
                                true,
                            ))
                        }
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
                &app.weaver_buffer,
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
        drop(st);
        // Auto-checkpoint on session_end when the cell opted in. Spawn off
        // the hook path so we don't block the HTTP response on git I/O.
        // Mirrors Python's `_on_agent_session_end` in loom/server.py.
        if event.kind == "session_end"
            && agent.cell_type == "agent"
            && agent.worktree_auto_checkpoint
            && !agent.worktree_path.is_empty()
        {
            spawn_auto_checkpoint(&app, &agent);
        }
        // Spawn the flush — see comment in `app.rs handle_pty_event`.
        // Awaiting here can deadlock the hook HTTP handler on the weaver's
        // own wait-for-ready when the weaver's startup hook fires.
        let app_for_flush = app.clone();
        tokio::spawn(async move {
            app_for_flush
                .weaver_buffer
                .maybe_flush_due_for_app(&app_for_flush)
                .await;
        });
    }
    (StatusCode::OK, Json(json!({ "ok": true })))
}

/// Spawn a background task to run a git checkpoint for an agent, updating
/// `worktree_checkpoints` + `last_checkpoint_at` on success. Used by the
/// auto-trigger paths (session_end, ai progress).
fn spawn_auto_checkpoint(app: &AppState, agent: &loom_core::state::AgentCell) {
    let app = app.clone();
    let agent_id = agent.id.clone();
    let message = crate::commands::worktree::auto_checkpoint_message(
        &agent.name,
        agent.worktree_checkpoints,
        &agent.last_summary,
    );
    tokio::spawn(async move {
        let ctx = crate::commands::CmdContext {
            state: app.state.clone(),
            db: app.db.clone(),
            bus: app.bus.clone(),
            pty: app.pty.clone(),
            ui_agents: app.ui_agents.clone(),
            terminal_bridge: app.terminal_bridge.clone(),
            terminals: app.terminals.clone(),
            weaver_buffer: app.weaver_buffer.clone(),
        };
        match crate::commands::worktree::do_checkpoint_for_agent(&ctx, &agent_id, &message).await {
            Ok(sha) => {
                if sha.is_some() {
                    tracing::info!(agent = %agent_id, "auto-checkpoint committed");
                }
                crate::commands::flush(&ctx).await;
            }
            Err(err) => {
                tracing::warn!(agent = %agent_id, ?err, "auto-checkpoint failed");
            }
        }
    });
}

fn canonical_path(path: &str) -> String {
    crate::paths::canonical_user_path(path)
}
