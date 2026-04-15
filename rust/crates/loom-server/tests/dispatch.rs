//! Dispatch + ai_report integration test.
//!
//! The PTY backend is not attached in tests, so the dispatcher exercises the
//! state transitions (task lane, agent link, tasks_dispatched counter)
//! without actually spawning a shell. PTY behaviour is unit-tested in
//! `loom-pty` separately.

use std::net::SocketAddr;
use std::sync::Arc;

use axum::Router;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_server::app::UiAgentRegistry;
use loom_server::commands;
use loom_server::events as evt;
use loom_server::uploads;
use loom_server::ws;

async fn spawn_test_server() -> (SocketAddr, Arc<Mutex<MatrixState>>) {
    let (addr, state, _reg) = spawn_test_server_full().await;
    (addr, state)
}

async fn spawn_test_server_full() -> (SocketAddr, Arc<Mutex<MatrixState>>, UiAgentRegistry) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let ui_agents = UiAgentRegistry::default();
    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: None,
        ui_agents: ui_agents.clone(),
    };

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
    (addr, state, ui_agents)
}

async fn post(addr: SocketAddr, body: Value) -> Value {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://{}/api/cmd", addr))
        .json(&body)
        .send()
        .await
        .unwrap();
    resp.json::<Value>().await.unwrap()
}

#[tokio::test]
async fn dispatch_without_action_creates_agent_and_moves_task() {
    let (addr, state) = spawn_test_server().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Do the thing", "group": "Eng"}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(v["ok"], true, "dispatch response: {v:?}");
    let agent_id = v["agent_id"].as_str().unwrap().to_string();

    let st = state.lock().await;
    let task = st.board_tasks.get(&task_id).unwrap();
    assert_eq!(task.lane, "In Progress");
    assert_eq!(task.agent_id, agent_id);

    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.tasks_dispatched, 1);
    assert_eq!(agent.current_task_id, task_id);
}

#[tokio::test]
async fn ai_report_done_moves_task_to_done_lane() {
    let (addr, state) = spawn_test_server().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Thing", "group": "Eng"}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    let agent_id = v["agent_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({
            "cmd": "ai_report",
            "action": "done",
            "agent_id": &agent_id,
            "message": "completed"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "ai_report done response: {v:?}");

    let st = state.lock().await;
    let task = st.board_tasks.get(&task_id).unwrap();
    assert_eq!(task.lane, "Done");
    assert!(
        task.messages.len() >= 1,
        "messages log should record the action"
    );
    let last = task.messages.last().unwrap();
    assert_eq!(last["action"], "done");

    let agent = st.agents.get(&agent_id).unwrap();
    assert!(agent.current_task_id.is_empty());
}

#[tokio::test]
async fn ai_report_blocked_labels_task() {
    let (addr, state) = spawn_test_server().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "X", "group": "Eng"}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();
    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    let agent_id = v["agent_id"].as_str().unwrap().to_string();

    post(
        addr,
        json!({
            "cmd": "ai_report",
            "action": "blocked",
            "agent_id": &agent_id,
            "message": "need info"
        }),
    )
    .await;

    let st = state.lock().await;
    let task = st.board_tasks.get(&task_id).unwrap();
    assert!(task.labels.iter().any(|l| l == "blocked"));
    let agent = st.agents.get(&agent_id).unwrap();
    assert!(agent.needs_attention);
}

#[tokio::test]
async fn dispatch_routes_through_ui_registry_when_attached() {
    let (addr, state, ui_agents) = spawn_test_server_full().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    // Add an agent and register it in the UI.
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "W", "group": "Eng"}),
    )
    .await;
    let agent_id = v["agent_id"].as_str().unwrap().to_string();
    let mut rx = ui_agents.register(agent_id.clone());

    // Create a task pinned to that agent and dispatch it.
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Hello", "group": "Eng", "agent_id": &agent_id}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(v["ok"], true, "dispatch response: {v:?}");

    // Expect at least two messages: the rendered prompt + a bare "\r".
    let mut received: Vec<String> = Vec::new();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while received.len() < 2 && std::time::Instant::now() < deadline {
        match tokio::time::timeout(std::time::Duration::from_millis(250), rx.recv()).await {
            Ok(Some(s)) => received.push(s),
            Ok(None) => break,
            Err(_) => {}
        }
    }
    assert!(
        received.iter().any(|s| s.contains("Hello")),
        "expected rendered prompt to include task text, got: {received:?}"
    );
    assert!(
        received.iter().any(|s| s == "\r"),
        "expected trailing \\r send, got: {received:?}"
    );

    // PTY backend was None; task state should still be updated.
    let st = state.lock().await;
    assert_eq!(st.board_tasks.get(&task_id).unwrap().lane, "In Progress");
}

#[tokio::test]
async fn send_text_routes_through_ui_registry_when_attached() {
    let (addr, _state, ui_agents) = spawn_test_server_full().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "W", "group": "Eng"}),
    )
    .await;
    let agent_id = v["agent_id"].as_str().unwrap().to_string();
    let mut rx = ui_agents.register(agent_id.clone());

    let v = post(
        addr,
        json!({"cmd": "send_text", "cell_id": &agent_id, "text": "hi there"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    let got = tokio::time::timeout(std::time::Duration::from_secs(2), rx.recv())
        .await
        .expect("send_text should route to UI registry")
        .expect("sender must still be open");
    assert_eq!(got, "hi there");
}

#[tokio::test]
async fn dispatch_missing_action_returns_warning() {
    let (addr, _state) = spawn_test_server().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "X",
            "group": "Eng",
            "action_name": "does-not-exist"
        }),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();

    // Pin to a tempdir so the action manager doesn't find any fixtures.
    let tmp = tempfile::tempdir().unwrap();
    std::env::set_var("LOOM_PROJECT_ROOT", tmp.path());

    let v = post(addr, json!({"cmd": "dispatch_task", "task_id": &task_id})).await;
    assert_eq!(v["warning"], "dispatch_action_missing", "got: {v:?}");
}

#[tokio::test]
async fn clear_agent_context_resets_counters_and_link() {
    let (addr, state) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "W", "group": "Eng"}),
    )
    .await;
    let agent_id = v["agent_id"].as_str().unwrap().to_string();
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "X", "group": "Eng"}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();
    post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;

    let v = post(addr, json!({"cmd": "clear_agent_context", "id": &agent_id})).await;
    assert_eq!(v["ok"], true);

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.tasks_dispatched, 0);
    assert_eq!(agent.current_task_id, "");
    assert!(!agent.needs_attention);
}

#[tokio::test]
async fn board_verify_task_updates_verification_state() {
    let (addr, state) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "X", "group": "Eng"}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({
            "cmd": "board_verify_task",
            "id": &task_id,
            "state": "verified",
            "mode": "manual",
            "notes": "looks good",
            "updated_by": "human",
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "response: {v:?}");

    let st = state.lock().await;
    let task = st.board_tasks.get(&task_id).unwrap();
    assert_eq!(task.verification_state, "verified");
    assert_eq!(task.verification_mode, "manual");
    assert_eq!(task.verification_notes, "looks good");
    assert_eq!(task.verification_updated_by, "human");
    assert!(!task.verification_updated_at.is_empty());
}

#[tokio::test]
async fn board_set_panel_persists_and_idempotent_returns_early() {
    let (addr, state) = spawn_test_server().await;
    let v = post(addr, json!({"cmd": "board_set_panel", "panel": "weaver"})).await;
    assert_eq!(v["ok"], true);
    assert_eq!(v["panel"], "weaver");

    {
        let st = state.lock().await;
        assert_eq!(st.panel_active, "weaver");
    }

    // Idempotent reselect: still OK but doesn't emit a fresh delta.
    let v = post(addr, json!({"cmd": "board_set_panel", "panel": "weaver"})).await;
    assert_eq!(v["ok"], true);
}

#[tokio::test]
async fn resolve_ask_moves_ask_to_done_and_logs_reply() {
    let (addr, state) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;

    // Create a parent task + an ask child.
    let parent = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Parent", "group": "Eng"}),
    )
    .await;
    let parent_id = parent["task_id"].as_str().unwrap().to_string();

    let ask = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Need info", "group": "Eng"}),
    )
    .await;
    let ask_id = ask["task_id"].as_str().unwrap().to_string();

    // Link ask to parent + flag it as human via update.
    post(
        addr,
        json!({
            "cmd": "board_update_task",
            "id": &ask_id,
            "fields": {"parent_task_id": &parent_id, "labels": ["human"]}
        }),
    )
    .await;

    let v = post(
        addr,
        json!({
            "cmd": "resolve_ask",
            "task_id": &ask_id,
            "reply": "Go with option B"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "response: {v:?}");
    assert_eq!(v["parent_task_id"], parent_id);

    let st = state.lock().await;
    assert_eq!(st.board_tasks.get(&ask_id).unwrap().lane, "Done");

    let parent = st.board_tasks.get(&parent_id).unwrap();
    let last = parent.messages.last().expect("reply should be appended");
    assert_eq!(last["action"], "ask_reply");
    assert_eq!(last["message"], "Go with option B");
    assert_eq!(last["from_task_id"], ask_id);
}
