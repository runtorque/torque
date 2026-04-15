//! WebSocket integration test — verifies Python-compatible state snapshots,
//! delta streaming, and direct command responses over `/ws`.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::Mutex;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;

async fn spawn_test_server() -> SocketAddr {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState {
        db,
        state,
        bus,
        pty: None,
        ui_agents: Default::default(),
    };

    let router = loom_server::app::build_router(app_state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

#[tokio::test]
async fn ws_sends_python_state_snapshot_on_connect_then_deltas_on_mutation() {
    let addr = spawn_test_server().await;

    let url = format!("ws://{}/ws", addr);
    let (mut ws, _) = connect_async(url).await.expect("ws connect");

    let snap = next_json(&mut ws).await;
    assert_eq!(snap["type"], "state");
    assert_eq!(snap["seq"], 0);
    assert!(snap["agents"].is_object());
    assert!(snap["groups"].is_object());
    assert!(snap["board_lanes"].is_array());

    let client = reqwest::Client::new();
    client
        .post(format!("http://{}/api/cmd", addr))
        .json(&json!({"cmd": "add_group", "group": "Eng"}))
        .send()
        .await
        .unwrap();

    let delta = tokio::time::timeout(Duration::from_secs(3), next_json(&mut ws))
        .await
        .expect("delta");
    assert_eq!(delta["type"], "delta");
    let ops = delta["ops"].as_array().unwrap();
    assert!(ops.iter().any(|op| op["op"] == "group_update" && op["name"] == "Eng"));
}

#[tokio::test]
async fn ws_accepts_browser_command_traffic_and_returns_direct_payloads() {
    let addr = spawn_test_server().await;

    let url = format!("ws://{}/ws", addr);
    let (mut ws, _) = connect_async(url).await.unwrap();
    let _ = next_json(&mut ws).await;

    ws.send(Message::Text(json!({"cmd": "get_global_settings"}).to_string()))
        .await
        .unwrap();
    let msg = tokio::time::timeout(Duration::from_secs(3), next_json(&mut ws))
        .await
        .unwrap();
    assert_eq!(msg["type"], "global_settings");
    assert!(msg["settings"].is_object());

    ws.send(Message::Text(json!({"cmd": "resync"}).to_string()))
        .await
        .unwrap();
    let msg = tokio::time::timeout(Duration::from_secs(3), next_json(&mut ws))
        .await
        .unwrap();
    assert_eq!(msg["type"], "state");
}

async fn next_json<S>(ws: &mut S) -> Value
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    loop {
        match ws.next().await {
            Some(Ok(Message::Text(text))) => return serde_json::from_str(&text).unwrap(),
            Some(Ok(Message::Binary(_))) | Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => continue,
            Some(Ok(Message::Close(_))) | None => panic!("ws closed"),
            Some(Ok(Message::Frame(_))) => continue,
            Some(Err(e)) => panic!("ws err: {e}"),
        }
    }
}
