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
use loom_server::commands;
use loom_server::events as evt;
use loom_server::uploads;
use loom_server::ws;

async fn spawn_test_server() -> (SocketAddr, Arc<Mutex<MatrixState>>) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: None,
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
    (addr, state)
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
    assert!(task.messages.len() >= 1, "messages log should record the action");
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
