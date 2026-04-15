use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::routing::post;
use axum::{Json, Router};
use futures::StreamExt;
use serde_json::{json, Value};
use tokio::sync::Mutex;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_server::app::AppState;
use loom_server::commands;
use loom_server::events as evt;
use loom_server::terminal_bridge;
use loom_server::uploads;
use loom_server::ws;

#[derive(Clone)]
struct BridgeStubState {
    calls: Arc<Mutex<Vec<(String, Value)>>>,
}

async fn spawn_bridge_stub_server() -> (SocketAddr, Arc<Mutex<Vec<(String, Value)>>>) {
    let calls = Arc::new(Mutex::new(Vec::<(String, Value)>::new()));
    let app_state = BridgeStubState {
        calls: calls.clone(),
    };
    let router = Router::new()
        .route("/bridge/create_session", post(stub_create_session))
        .route("/bridge/update_session", post(stub_echo_ok))
        .route("/bridge/close_session", post(stub_echo_ok))
        .route("/bridge/focus_session", post(stub_focus_ok))
        .route("/bridge/send_text", post(stub_echo_ok))
        .route("/bridge/write_input", post(stub_echo_ok))
        .route("/bridge/signal_input_ready", post(stub_echo_ok))
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, calls)
}

async fn stub_create_session(
    State(state): State<BridgeStubState>,
    Json(payload): Json<Value>,
) -> Json<Value> {
    state
        .calls
        .lock()
        .await
        .push(("create_session".into(), payload.clone()));
    let mut cell = payload.get("cell").cloned().unwrap_or_else(|| json!({}));
    cell["session_id"] = json!("bridge-session-1");
    cell["window_id"] = json!("bridge-window-1");
    cell["status"] = json!("idle");
    Json(json!({ "ok": true, "cell": cell }))
}

async fn stub_echo_ok(
    State(state): State<BridgeStubState>,
    Json(payload): Json<Value>,
) -> Json<Value> {
    let path = payload
        .get("text")
        .map(|_| "send_text")
        .or_else(|| payload.get("data").map(|_| "write_input"))
        .or_else(|| payload.get("session_id").map(|_| "close_or_signal"))
        .unwrap_or("update_or_close");
    state.calls.lock().await.push((path.into(), payload));
    Json(json!({ "ok": true }))
}

async fn stub_focus_ok(
    State(state): State<BridgeStubState>,
    Json(payload): Json<Value>,
) -> Json<Value> {
    state
        .calls
        .lock()
        .await
        .push(("focus_session".into(), payload));
    Json(json!({ "ok": true, "focused": true }))
}

async fn spawn_loom_server(bridge_url: Option<String>) -> (SocketAddr, Arc<Mutex<MatrixState>>) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let terminal_bridge_client = loom_server::terminal_bridge::TerminalBridgeClient::default();
    if let Some(url) = bridge_url {
        terminal_bridge_client.configure_url(url);
    }
    let app_state = AppState {
        db,
        state: state.clone(),
        bus,
        pty: None,
        ui_agents: Default::default(),
        terminal_bridge: terminal_bridge_client,
    };

    let router = Router::new()
        .merge(ws::routes())
        .merge(commands::routes())
        .merge(terminal_bridge::routes())
        .merge(evt::routes())
        .merge(uploads::routes())
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, state)
}

async fn post_json(addr: SocketAddr, path: &str, body: Value) -> Value {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://{}{}", addr, path))
        .json(&body)
        .send()
        .await
        .unwrap();
    resp.json::<Value>().await.unwrap()
}

async fn post_cmd(addr: SocketAddr, body: Value) -> Value {
    post_json(addr, "/api/cmd", body).await
}

#[tokio::test]
async fn add_agent_creates_session_via_terminal_bridge() {
    let (bridge_addr, calls) = spawn_bridge_stub_server().await;
    let (addr, state) = spawn_loom_server(Some(format!("http://{bridge_addr}"))).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    assert_eq!(response["ok"], true);
    let agent_id = response["agent_id"].as_str().unwrap().to_string();

    let calls = calls.lock().await;
    let create = calls
        .iter()
        .find(|(name, _)| name == "create_session")
        .expect("create_session call recorded");
    assert_eq!(create.1["cell"]["id"], agent_id);

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.session_id.as_deref(), Some("bridge-session-1"));
    assert_eq!(agent.window_id, "bridge-window-1");
}

#[tokio::test]
async fn send_text_routes_through_terminal_bridge() {
    let (bridge_addr, calls) = spawn_bridge_stub_server().await;
    let (addr, _state) = spawn_loom_server(Some(format!("http://{bridge_addr}"))).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = response["agent_id"].as_str().unwrap().to_string();

    let response = post_cmd(
        addr,
        json!({"cmd": "send_text", "cell_id": &agent_id, "text": "hello"}),
    )
    .await;
    assert_eq!(response["ok"], true);

    let calls = calls.lock().await;
    let send = calls
        .iter()
        .find(|(_, payload)| payload.get("text") == Some(&json!("hello")))
        .expect("send_text call recorded");
    assert_eq!(send.1["cell_id"], agent_id);
    assert_eq!(send.1["session_id"], "bridge-session-1");
}

#[tokio::test]
async fn focus_callback_updates_state_and_broadcasts_focus_delta() {
    let (addr, state) = spawn_loom_server(None).await;
    let url = format!("ws://{}/ws", addr);
    let (mut ws_stream, _) = connect_async(url).await.expect("ws connect");
    let _snapshot = next_json(&mut ws_stream).await;

    let response = post_json(
        addr,
        "/api/terminal-bridge/focus",
        json!({
            "active_session_id": "session-42",
            "current_window_id": "window-42"
        }),
    )
    .await;
    assert_eq!(response["ok"], true);

    let delta = tokio::time::timeout(Duration::from_secs(3), next_json(&mut ws_stream))
        .await
        .expect("focus delta");
    let ops = delta["ops"].as_array().unwrap();
    assert!(ops.iter().any(|op| {
        op["op"] == "focus_update"
            && op["active_session_id"] == "session-42"
            && op["current_window_id"] == "window-42"
    }));

    let st = state.lock().await;
    assert_eq!(st.active_session_id.as_deref(), Some("session-42"));
    assert_eq!(st.current_window_id.as_deref(), Some("window-42"));
}

#[tokio::test]
async fn agent_sync_callback_updates_existing_agent() {
    let (addr, state) = spawn_loom_server(None).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = response["agent_id"].as_str().unwrap().to_string();

    let response = post_json(
        addr,
        "/api/terminal-bridge/agent-sync",
        json!({
            "cell": {
                "id": agent_id,
                "name": "Worker",
                "group": "Eng",
                "session_id": "bridge-session-9",
                "window_id": "bridge-window-9",
                "status": "idle",
                "current_path": "/tmp/repo",
                "current_branch": "main",
                "git_root": "/tmp/repo",
                "agent_type": "codex"
            }
        }),
    )
    .await;
    assert_eq!(response["ok"], true);

    let st = state.lock().await;
    let agent = st.agents.values().next().unwrap();
    assert_eq!(agent.session_id.as_deref(), Some("bridge-session-9"));
    assert_eq!(agent.window_id, "bridge-window-9");
    assert_eq!(agent.current_path, "/tmp/repo");
    assert_eq!(agent.current_branch, "main");
    assert_eq!(agent.git_root, "/tmp/repo");
    assert_eq!(agent.agent_type, "codex");
}

async fn next_json<S>(ws: &mut S) -> Value
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    loop {
        match ws.next().await {
            Some(Ok(Message::Text(text))) => return serde_json::from_str(&text).unwrap(),
            Some(Ok(Message::Binary(_)))
            | Some(Ok(Message::Ping(_)))
            | Some(Ok(Message::Pong(_))) => continue,
            Some(Ok(Message::Frame(_))) => continue,
            Some(Ok(Message::Close(_))) | None => panic!("ws closed"),
            Some(Err(e)) => panic!("ws err: {e}"),
        }
    }
}
