//! WebSocket handler — sends snapshots on connect, deltas thereafter.

use std::sync::Arc;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;
use futures::{SinkExt, StreamExt};
use serde_json::json;

use loom_core::events::OutMessage;

use crate::app::AppState;

pub fn routes() -> Router<AppState> {
    Router::new().route("/ws", get(ws_upgrade))
}

async fn ws_upgrade(State(state): State<AppState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_ws(state, socket))
}

async fn handle_ws(app: AppState, socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();
    let bus = app.bus.clone();
    let state = app.state.clone();

    // Send initial snapshot.
    let snapshot = build_snapshot(&app).await;
    if sender
        .send(Message::Text(serde_json::to_string(&snapshot).unwrap_or_default()))
        .await
        .is_err()
    {
        return;
    }

    let mut rx = bus.subscribe();
    let send_task = tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(msg) => {
                    let text = serde_json::to_string(&msg).unwrap_or_default();
                    if sender.send(Message::Text(text)).await.is_err() {
                        break;
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                    // force resync
                    let snap = build_snapshot_locked(&state).await;
                    if sender
                        .send(Message::Text(serde_json::to_string(&snap).unwrap_or_default()))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    // Receive loop — handle `resync` and `send_text` pings. Full command
    // handling is on `/api/cmd`; the WS channel is primarily read-only.
    while let Some(Ok(msg)) = receiver.next().await {
        match msg {
            Message::Text(text) => {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v.get("type").and_then(|v| v.as_str()) == Some("resync") {
                        let snap = build_snapshot(&app).await;
                        app.bus.send(OutMessage::Snapshot {
                            seq: snap.get("seq").and_then(|v| v.as_u64()).unwrap_or(0),
                            state: snap,
                        });
                    }
                }
            }
            Message::Close(_) => break,
            _ => {}
        }
    }

    send_task.abort();
}

/// Build the same snapshot shape Python emits on connect.
pub async fn build_snapshot(app: &AppState) -> serde_json::Value {
    build_snapshot_locked(&app.state).await
}

async fn build_snapshot_locked(
    state: &Arc<tokio::sync::Mutex<loom_core::state::MatrixState>>,
) -> serde_json::Value {
    let st = state.lock().await;
    let agents: Vec<serde_json::Value> = st
        .agents
        .values()
        .map(|a| serde_json::to_value(a).unwrap_or_default())
        .collect();
    let groups: Vec<serde_json::Value> = st
        .groups_order
        .iter()
        .map(|name| {
            json!({
                "name": name,
                "slug": st.group_slugs.get(name).cloned().unwrap_or_default(),
                "agents": st.groups.get(name).cloned().unwrap_or_default(),
                "settings": st.group_settings.get(name).cloned(),
            })
        })
        .collect();
    let tasks: Vec<serde_json::Value> = st
        .board_tasks
        .values()
        .map(|t| serde_json::to_value(t).unwrap_or_default())
        .collect();
    json!({
        "type": "snapshot",
        "seq": st.current_seq(),
        "agents": agents,
        "groups": groups,
        "lanes": st.board_lanes,
        "tasks": tasks,
        "global_settings": st.global_settings,
    })
}
