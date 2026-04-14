//! WebSocket integration test — verifies snapshot-on-connect then deltas.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::Mutex;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_server::commands;
use loom_server::events as evt;
use loom_server::uploads;
use loom_server::ws;

async fn spawn_test_server() -> SocketAddr {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState { db, state, bus, pty: None };

    let router = Router::new()
        .merge(ws::routes())
        .merge(commands::routes())
        .merge(evt::routes())
        .merge(uploads::routes())
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

#[tokio::test]
async fn ws_sends_snapshot_on_connect_then_deltas_on_mutation() {
    let addr = spawn_test_server().await;

    let url = format!("ws://{}/ws", addr);
    let (mut ws, _) = connect_async(url).await.expect("ws connect");

    // First message: snapshot.
    let snap = next_json(&mut ws).await;
    assert_eq!(snap["type"], "snapshot");
    assert_eq!(snap["seq"], 0);
    assert!(snap["agents"].is_array());
    assert!(snap["groups"].is_array());
    assert!(snap["lanes"].is_array());

    // Drive a mutation via /api/cmd
    let client = reqwest::Client::new();
    let body = json!({"cmd": "add_group", "name": "Eng"});
    client
        .post(format!("http://{}/api/cmd", addr))
        .json(&body)
        .send()
        .await
        .unwrap();

    // Second message should be a delta with group_update.
    let delta = tokio::time::timeout(Duration::from_secs(3), next_json(&mut ws))
        .await
        .expect("delta")
        ;
    assert_eq!(delta["type"], "delta");
    assert!(delta["seq"].as_u64().unwrap() >= 1);
    let ops = delta["ops"].as_array().unwrap();
    assert!(ops.iter().any(|op| op["op"] == "group_update" && op["name"] == "Eng"));
}

#[tokio::test]
async fn ws_resync_on_demand() {
    let addr = spawn_test_server().await;

    let url = format!("ws://{}/ws", addr);
    let (mut ws, _) = connect_async(url).await.unwrap();

    // initial snapshot
    let _ = next_json(&mut ws).await;

    // send a resync request
    ws.send(Message::Text(json!({"type": "resync"}).to_string()))
        .await
        .unwrap();

    // expect a snapshot back
    let msg = tokio::time::timeout(Duration::from_secs(3), next_json(&mut ws))
        .await
        .unwrap();
    assert_eq!(msg["type"], "snapshot");
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
