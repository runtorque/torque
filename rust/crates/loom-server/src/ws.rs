//! WebSocket handler — sends Python-compatible full state on connect and
//! accepts the existing browser command traffic over the same socket.

use std::sync::Arc;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, State};
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::mpsc;

use loom_core::events::OutMessage;

use crate::app::AppState;
use crate::commands::{CmdContext, CmdError};

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/ws", get(ws_upgrade))
        .route("/ws/terminal/{cell_id}", get(terminal_ws_upgrade))
        .route("/ws/terminal/:cell_id", get(terminal_ws_upgrade))
}

async fn ws_upgrade(State(state): State<AppState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_ws(state, socket))
}

async fn terminal_ws_upgrade(
    State(state): State<AppState>,
    Path(cell_id): Path<String>,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_terminal_ws(state, cell_id, socket))
}

async fn handle_ws(app: AppState, socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();
    let bus = app.bus.clone();
    let app_for_lag = app.clone();
    let ctx = CmdContext {
        state: app.state.clone(),
        db: app.db.clone(),
        bus: app.bus.clone(),
        pty: app.pty.clone(),
        ui_agents: app.ui_agents.clone(),
    };

    let snapshot = build_snapshot(&app).await;
    if send_json(&mut sender, &snapshot).await.is_err() {
        return;
    }

    let (cmd_tx, mut cmd_rx) = mpsc::unbounded_channel::<Value>();
    let mut rx = bus.subscribe();
    let send_task = tokio::spawn(async move {
        loop {
            tokio::select! {
                maybe_cmd = cmd_rx.recv() => {
                    let Some(value) = maybe_cmd else { break; };
                    if send_json(&mut sender, &value).await.is_err() {
                        break;
                    }
                }
                recv = rx.recv() => {
                    match recv {
                        Ok(OutMessage::Delta { seq, ops }) => {
                            let value = json!({ "type": "delta", "seq": seq, "ops": ops });
                            if send_json(&mut sender, &value).await.is_err() {
                                break;
                            }
                        }
                        Ok(OutMessage::Snapshot { state, .. }) => {
                            if send_json(&mut sender, &state).await.is_err() {
                                break;
                            }
                        }
                        Ok(OutMessage::Event(value)) => {
                            if send_json(&mut sender, &value).await.is_err() {
                                break;
                            }
                        }
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                            let snap = build_snapshot(&app_for_lag).await;
                            if send_json(&mut sender, &snap).await.is_err() {
                                break;
                            }
                        }
                        Err(_) => break,
                    }
                }
            }
        }
    });

    while let Some(Ok(msg)) = receiver.next().await {
        match msg {
            Message::Text(text) => {
                let mut payload = match serde_json::from_str::<Value>(&text) {
                    Ok(value) => value,
                    Err(_) => continue,
                };
                if payload.get("type").and_then(|v| v.as_str()) == Some("resync")
                    && payload.get("cmd").is_none()
                {
                    payload = json!({ "cmd": "resync" });
                }
                let Some(cmd) = payload
                    .get("cmd")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                else {
                    continue;
                };
                let response = match crate::commands::dispatch_command(&ctx, &cmd, &payload).await {
                    Ok(value) => value,
                    Err(err) => json!({
                        "type": "error",
                        "message": cmd_error_message(&cmd, err),
                    }),
                };
                if cmd_tx.send(response).is_err() {
                    break;
                }
            }
            Message::Close(_) => break,
            _ => {}
        }
    }

    send_task.abort();
}

async fn handle_terminal_ws(app: AppState, cell_id: String, socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();
    let mut rx = app.terminals.subscribe(&cell_id).await;
    let (session_id, data) = app.terminals.snapshot(&cell_id).await;
    let snapshot = json!({
        "type": "snapshot",
        "cell_id": cell_id,
        "session_id": session_id,
        "data": data,
    });
    if send_json(&mut sender, &snapshot).await.is_err() {
        return;
    }

    loop {
        tokio::select! {
            maybe_msg = receiver.next() => {
                let Some(Ok(msg)) = maybe_msg else { break; };
                match msg {
                    Message::Text(text) => {
                        let Ok(payload) = serde_json::from_str::<Value>(&text) else { continue; };
                        let pty = app.pty.clone();
                        let cell = {
                            let st = app.state.lock().await;
                            st.agents.get(&cell_id).cloned()
                        };
                        let Some(cell) = cell else { continue; };
                        match payload.get("type").and_then(|v| v.as_str()).unwrap_or("") {
                            "input" => {
                                if let Some(pty) = &pty {
                                    let data = payload.get("data").and_then(|v| v.as_str()).unwrap_or("");
                                    let _ = pty.write(&cell_id, data.as_bytes()).await;
                                }
                            }
                            "resize" => {
                                if let Some(pty) = &pty {
                                    let cols = payload.get("cols").and_then(|v| v.as_u64()).unwrap_or(120) as u16;
                                    let rows = payload.get("rows").and_then(|v| v.as_u64()).unwrap_or(40) as u16;
                                    let _ = pty.resize(&cell_id, rows, cols).await;
                                }
                            }
                            "focus" => {
                                let mut st = app.state.lock().await;
                                st.active_session_id = cell.session_id.clone();
                                st.current_window_id = if cell.window_id.is_empty() {
                                    None
                                } else {
                                    Some(cell.window_id.clone())
                                };
                                let active_session_id = st.active_session_id.clone();
                                let current_window_id = st.current_window_id.clone();
                                st.emit(loom_core::delta::DeltaOp::FocusUpdate {
                                    active_session_id,
                                    current_window_id,
                                });
                                drop(st);
                                crate::commands::flush(&crate::commands::CmdContext {
                                    state: app.state.clone(),
                                    db: app.db.clone(),
                                    bus: app.bus.clone(),
                                    pty: app.pty.clone(),
                                    ui_agents: app.ui_agents.clone(),
                                }).await;
                            }
                            _ => {}
                        }
                    }
                    Message::Close(_) => break,
                    _ => {}
                }
            }
            recv = rx.recv() => {
                match recv {
                    Ok(value) => {
                        if send_json(&mut sender, &value).await.is_err() {
                            break;
                        }
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                        let (session_id, data) = app.terminals.snapshot(&cell_id).await;
                        let snapshot = json!({
                            "type": "snapshot",
                            "cell_id": cell_id,
                            "session_id": session_id,
                            "data": data,
                        });
                        if send_json(&mut sender, &snapshot).await.is_err() {
                            break;
                        }
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                }
            }
        }
    }
}

async fn send_json(
    sender: &mut futures::stream::SplitSink<WebSocket, Message>,
    value: &Value,
) -> Result<(), ()> {
    sender
        .send(Message::Text(
            serde_json::to_string(value).unwrap_or_default(),
        ))
        .await
        .map_err(|_| ())
}

fn cmd_error_message(cmd: &str, err: CmdError) -> String {
    match err {
        CmdError::NotImplemented => format!("command not implemented: {cmd}"),
        CmdError::BadRequest(message) => message,
        CmdError::Engine(err) => err.to_string(),
    }
}

/// Build the same snapshot shape Python emits on connect.
pub async fn build_snapshot(app: &AppState) -> Value {
    let panel_events = app.db.load_panel_events(50, None).await.unwrap_or_default();
    build_snapshot_locked(&app.state, &panel_events, app.pty.is_some()).await
}

async fn build_snapshot_locked(
    state: &Arc<tokio::sync::Mutex<loom_core::state::MatrixState>>,
    panel_events: &[Value],
    embedded_terminal: bool,
) -> Value {
    let st = state.lock().await;

    let mut groups = serde_json::Map::new();
    for name in &st.groups_order {
        groups.insert(
            name.clone(),
            serde_json::to_value(st.groups.get(name).cloned().unwrap_or_default())
                .unwrap_or_else(|_| json!([])),
        );
    }

    json!({
        "type": "state",
        "seq": st.current_seq(),
        "agents": &st.agents,
        "groups": groups,
        "group_slugs": &st.group_slugs,
        "group_settings": &st.group_settings,
        "global_settings": &st.global_settings,
        "children": &st.children,
        "active_session_id": &st.active_session_id,
        "current_window_id": &st.current_window_id,
        "board_lanes": &st.board_lanes,
        "board_tasks": &st.board_tasks,
        "task_id_aliases": &st.task_id_aliases,
        "task_id_counters": &st.task_id_counters,
        "pipeline_task_counters": &st.pipeline_task_counters,
        "schedules": &st.schedules,
        "auto_dispatch_queues": &st.auto_dispatch_queues,
        "panel_active": &st.panel_active,
        "board_panel_height": st.board_panel_height,
        "standalone_panel_layout": &st.standalone_panel_layout,
        "events_dismissed_attention": &st.events_dismissed_attention,
        "board_filters_by_group": &st.board_filters_by_group,
        "board_saved_views_by_group": &st.board_saved_views_by_group,
        "board_lane_sorts_by_group": &st.board_lane_sorts_by_group,
        "board_card_density_by_group": &st.board_card_density_by_group,
        "weaver_settings": &st.weaver_settings,
        "weaver_journal": {},
        "weaver_worklog": &st.weaver_worklog,
        "weaver_streams": {},
        "weaver_session_maps": {},
        "weaver_buffer_stats": {},
        "weaver_sent_events": {},
        "panel_events": panel_events,
        "selected_agent_id": &st.selected_agent_id,
        "content_layout": &st.content_layout,
        "dock_edges": &st.dock_edges,
        "providers": providers_payload(),
        "runtime": runtime_payload(embedded_terminal),
    })
}

fn providers_payload() -> Vec<Value> {
    loom_adapters::registry::get_providers()
        .into_iter()
        .filter(|name| *name != "generic")
        .map(|name| {
            let command = loom_adapters::registry::get_adapter(name)
                .as_ref()
                .map(|adapter| adapter.default_boot_command().to_string())
                .unwrap_or_default();
            json!({
                "name": name,
                "display_name": name,
                "command": command,
                "reasoning_efforts": [],
            })
        })
        .collect()
}

fn runtime_payload(embedded_terminal: bool) -> Value {
    json!({
        "standalone": true,
        "embedded_terminal": embedded_terminal,
        "layout": if embedded_terminal { "ide" } else { "classic" },
        "terminal_backend": "pty",
        "home_directory": dirs::home_dir().map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
        "profile": std::env::var("LOOM_PROFILE").unwrap_or_default(),
        "data_dir": loom_core::config::data_dir().to_string_lossy().to_string(),
        "desktop_shell": std::env::var("LOOM_DESKTOP_SHELL").unwrap_or_default(),
        "port": std::env::var("LOOM_PORT")
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(loom_core::config::DEFAULT_PORT),
        "default_command": loom_core::config::default_command(),
    })
}
