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
        .route("/bridge/close_session", post(stub_close_ok))
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

async fn spawn_bridge_failing_create_server() -> SocketAddr {
    async fn failing_create_session(
        Json(_payload): Json<Value>,
    ) -> (axum::http::StatusCode, Json<Value>) {
        (
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "ok": false, "error": "bridge create failed" })),
        )
    }

    let router = Router::new().route("/bridge/create_session", post(failing_create_session));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

async fn stub_create_session(
    State(state): State<BridgeStubState>,
    Json(payload): Json<Value>,
) -> Json<Value> {
    let session_num = {
        let mut calls = state.calls.lock().await;
        let session_num = calls
            .iter()
            .filter(|(name, _)| name == "create_session")
            .count()
            + 1;
        calls.push(("create_session".into(), payload.clone()));
        session_num
    };
    let mut cell = payload.get("cell").cloned().unwrap_or_else(|| json!({}));
    cell["session_id"] = json!(format!("bridge-session-{session_num}"));
    cell["window_id"] = json!(format!("bridge-window-{session_num}"));
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

async fn stub_close_ok(
    State(state): State<BridgeStubState>,
    Json(payload): Json<Value>,
) -> Json<Value> {
    state
        .calls
        .lock()
        .await
        .push(("close_session".into(), payload));
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
        terminals: Default::default(),
        weaver_buffer: loom_server::weaver_buffer::WeaverEventBuffer::default(),
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
    let agent_id = response["data"]["agent_id"].as_str().unwrap().to_string();

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
async fn add_agent_uses_provider_defaults_and_bridge_launch_payload() {
    let (bridge_addr, calls) = spawn_bridge_stub_server().await;
    let (addr, state) = spawn_loom_server(Some(format!("http://{bridge_addr}"))).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let update = post_cmd(
        addr,
        json!({
            "cmd": "update_group_settings",
            "group": "Eng",
            "settings": {
                "agent_provider": "codex",
                "agent_model": "gpt-5.4-mini",
                "agent_reasoning_effort": "high",
                "agent_shell": "/bin/bash",
                "env_vars": {"BASE": "1"},
                "agent_env_vars": {"AGENT_ONLY": "2"},
                "agent_env_file": "/tmp/agent.env"
            }
        }),
    )
    .await;
    assert_eq!(update["ok"], true);

    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    assert_eq!(response["ok"], true, "response: {response:?}");
    let agent_id = response["data"]["agent_id"].as_str().unwrap().to_string();

    let calls = calls.lock().await;
    let create = calls
        .iter()
        .find(|(name, _)| name == "create_session")
        .expect("create_session call recorded");
    assert_eq!(create.1["cell"]["id"], agent_id);
    assert_eq!(
        create.1["cell"]["command"],
        "codex --model gpt-5.4-mini -c model_reasoning_effort=high"
    );
    assert_eq!(create.1["cell"]["agent_type"], "codex");
    assert_eq!(create.1["shell"], "/bin/bash");
    assert_eq!(create.1["env_file"], "/tmp/agent.env");
    assert_eq!(create.1["env_vars"]["BASE"], "1");
    assert_eq!(create.1["env_vars"]["AGENT_ONLY"], "2");
    drop(calls);

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(
        agent.command,
        "codex --model gpt-5.4-mini -c model_reasoning_effort=high"
    );
    assert_eq!(agent.agent_type, "codex");
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
    let agent_id = response["data"]["agent_id"].as_str().unwrap().to_string();

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
async fn relaunch_agent_recreates_bridge_managed_session() {
    let (bridge_addr, calls) = spawn_bridge_stub_server().await;
    let (addr, state) = spawn_loom_server(Some(format!("http://{bridge_addr}"))).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let update = post_cmd(
        addr,
        json!({
            "cmd": "update_group_settings",
            "group": "Eng",
            "settings": {"agent_provider": "codex"}
        }),
    )
    .await;
    assert_eq!(update["ok"], true);
    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = response["data"]["agent_id"].as_str().unwrap().to_string();

    {
        let st = state.lock().await;
        let agent = st.agents.get(&agent_id).unwrap();
        assert_eq!(agent.session_id.as_deref(), Some("bridge-session-1"));
    }

    let response = post_cmd(addr, json!({"cmd": "relaunch_agent", "id": &agent_id})).await;
    assert_eq!(response["ok"], true);

    let calls = calls.lock().await;
    let create_calls = calls
        .iter()
        .filter(|(name, _)| name == "create_session")
        .collect::<Vec<_>>();
    assert_eq!(
        create_calls.len(),
        2,
        "expected add + relaunch create_session"
    );
    assert_eq!(create_calls[0].1["cell"]["id"], agent_id);
    assert_eq!(create_calls[1].1["cell"]["id"], agent_id);
    assert_eq!(create_calls[0].1["cell"]["command"], "codex");
    assert_eq!(create_calls[1].1["cell"]["command"], "codex");
    let close_call = calls
        .iter()
        .find(|(name, payload)| name == "close_session" && payload["cell_id"] == json!(agent_id))
        .expect("close_session call recorded");
    assert_eq!(close_call.1["session_id"], "bridge-session-1");
    drop(calls);

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.session_id.as_deref(), Some("bridge-session-2"));
    assert_eq!(agent.window_id, "bridge-window-2");
    assert_eq!(agent.status, "idle");
}

#[tokio::test]
async fn dispatch_to_existing_bridge_agent_recreates_session_and_sends_prompt() {
    let (bridge_addr, calls) = spawn_bridge_stub_server().await;
    let (addr, state) = spawn_loom_server(Some(format!("http://{bridge_addr}"))).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let update = post_cmd(
        addr,
        json!({
            "cmd": "update_group_settings",
            "group": "Eng",
            "settings": {
                "agent_provider": "codex",
                "agent_shell": "/bin/bash",
                "env_vars": {"BASE": "1"},
                "agent_env_vars": {"AGENT_ONLY": "2"},
                "agent_env_file": "/tmp/agent.env"
            }
        }),
    )
    .await;
    assert_eq!(update["ok"], true);

    let agent = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();

    {
        let mut st = state.lock().await;
        let worker = st.agents.get_mut(&agent_id).unwrap();
        worker.session_id = None;
        worker.status = "stopped".into();
    }

    let task = post_cmd(
        addr,
        json!({"cmd": "board_add_task", "task": "Resume and handle this task", "group": "Eng"}),
    )
    .await;
    let task_id = task["data"]["task_id"].as_str().unwrap().to_string();

    let response = post_cmd(
        addr,
        json!({
            "cmd": "dispatch_task",
            "task_id": &task_id,
            "agent_id": &agent_id,
            "force_no_action": true
        }),
    )
    .await;
    assert_eq!(response["ok"], true, "response: {response:?}");
    assert_eq!(response["data"]["agent_id"], agent_id);

    let calls = calls.lock().await;
    let create_calls = calls
        .iter()
        .filter(|(name, _)| name == "create_session")
        .collect::<Vec<_>>();
    assert_eq!(
        create_calls.len(),
        2,
        "expected add + dispatch create_session"
    );
    assert_eq!(create_calls[1].1["cell"]["id"], agent_id);
    assert_eq!(create_calls[1].1["shell"], "/bin/bash");
    assert_eq!(create_calls[1].1["env_file"], "/tmp/agent.env");
    assert_eq!(create_calls[1].1["env_vars"]["BASE"], "1");
    assert_eq!(create_calls[1].1["env_vars"]["AGENT_ONLY"], "2");
    assert_eq!(create_calls[1].1["target_window_id"], "bridge-window-1");

    let send_call = calls
        .iter()
        .find(|(name, payload)| {
            name == "send_text"
                && payload["cell_id"] == json!(agent_id)
                && payload["text"] == json!("Resume and handle this task")
        })
        .expect("dispatch send_text call recorded");
    assert_eq!(send_call.1["session_id"], "bridge-session-2");
    drop(calls);

    let st = state.lock().await;
    assert_eq!(
        st.agents.len(),
        1,
        "dispatch should reuse the existing agent"
    );
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.session_id.as_deref(), Some("bridge-session-2"));
    assert_eq!(agent.status, "idle");
    assert_eq!(st.board_tasks.get(&task_id).unwrap().agent_id, agent_id);
}

#[tokio::test]
async fn add_agent_returns_error_when_bridge_create_session_fails() {
    let bridge_addr = spawn_bridge_failing_create_server().await;
    let (addr, state) = spawn_loom_server(Some(format!("http://{bridge_addr}"))).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    assert_eq!(response["ok"], false, "response: {response:?}");
    assert!(response["error"].as_str().unwrap_or("").contains("bridge"));

    let st = state.lock().await;
    let agent = st
        .agents
        .values()
        .next()
        .expect("agent should remain visible");
    assert_eq!(agent.status, "error");
    assert!(agent.error_message.contains("bridge create"));
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
    let agent_id = response["data"]["agent_id"].as_str().unwrap().to_string();

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

#[tokio::test]
async fn agent_sync_callback_preserves_rust_owned_state() {
    let (addr, state) = spawn_loom_server(None).await;

    post_cmd(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let response = post_cmd(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = response["data"]["agent_id"].as_str().unwrap().to_string();

    {
        let mut st = state.lock().await;
        let agent = st.agents.get_mut(&agent_id).unwrap();
        agent.current_task_id = "eng-123".into();
        agent.activity = "tool_call".into();
        agent.activity_detail = "Dispatching task".into();
        agent.last_summary = "authoritative summary".into();
        agent.pending_weaver_message = true;
        agent.session_tokens_in = 77;
        agent.session_tokens_out = 88;
        agent.worktree_dirty = true;
    }

    let response = post_json(
        addr,
        "/api/terminal-bridge/agent-sync",
        json!({
            "cell": {
                "id": agent_id,
                "session_id": "bridge-session-10",
                "window_id": "bridge-window-10",
                "status": "idle",
                "current_process": "codex",
                "current_path": "/tmp/repo",
                "current_branch": "main",
                "git_root": "/tmp/repo",
                "agent_type": "codex",
                "current_task_id": "",
                "activity": "",
                "activity_detail": "",
                "last_summary": "",
                "pending_weaver_message": false,
                "session_tokens_in": 0,
                "session_tokens_out": 0,
                "worktree_dirty": false
            }
        }),
    )
    .await;
    assert_eq!(response["ok"], true);

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.session_id.as_deref(), Some("bridge-session-10"));
    assert_eq!(agent.window_id, "bridge-window-10");
    assert_eq!(agent.current_process, "codex");
    assert_eq!(agent.current_path, "/tmp/repo");
    assert_eq!(agent.current_branch, "main");
    assert_eq!(agent.git_root, "/tmp/repo");
    assert_eq!(agent.agent_type, "codex");
    assert_eq!(agent.current_task_id, "eng-123");
    assert_eq!(agent.activity, "tool_call");
    assert_eq!(agent.activity_detail, "Dispatching task");
    assert_eq!(agent.last_summary, "authoritative summary");
    assert!(agent.pending_weaver_message);
    assert_eq!(agent.session_tokens_in, 77);
    assert_eq!(agent.session_tokens_out, 88);
    assert!(agent.worktree_dirty);
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
