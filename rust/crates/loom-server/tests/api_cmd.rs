//! Integration test: spin up the real axum server against an in-memory DB
//! and drive it through a few commands. Mirrors a subset of
//! `tests/test_server_modules.py` parity.

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

async fn spawn_test_server() -> (SocketAddr, tokio::task::JoinHandle<()>) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState {
        db,
        state,
        bus,
        pty: None,
        ui_agents: Default::default(),
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
    };

    let router = Router::new()
        .merge(ws::routes())
        .merge(commands::routes())
        .merge(evt::routes())
        .merge(uploads::routes())
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, handle)
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
async fn ping_roundtrips() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(addr, json!({"cmd": "ping"})).await;
    assert_eq!(v["pong"], true);
}

#[tokio::test]
async fn add_group_then_add_agent() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    assert_eq!(v["ok"], true);

    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    assert_eq!(v["ok"], true);
    let agent_id = v["agent_id"].as_str().unwrap().to_string();
    let slug = v["slug"].as_str().unwrap();
    assert!(slug.starts_with("eng:"));

    let v = post(addr, json!({"cmd": "resync"})).await;
    let agents = v["agents"].as_array().unwrap();
    assert!(agents.iter().any(|a| a["id"] == agent_id));
    assert_eq!(v["groups"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn board_task_full_lifecycle() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;

    let v = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Fix bug", "group": "Eng"}),
    )
    .await;
    assert_eq!(v["ok"], true);
    let task_id = v["task_id"].as_str().unwrap().to_string();
    assert!(task_id.starts_with("eng-"));

    // Move to In Progress
    let v = post(
        addr,
        json!({"cmd": "board_move_task", "id": &task_id, "lane": "In Progress"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    // Verify in snapshot
    let snap = post(addr, json!({"cmd": "resync"})).await;
    let tasks = snap["tasks"].as_array().unwrap();
    let task = tasks.iter().find(|t| t["id"] == task_id).unwrap();
    assert_eq!(task["lane"], "In Progress");

    // Archive
    let v = post(addr, json!({"cmd": "board_archive_task", "id": &task_id})).await;
    assert_eq!(v["ok"], true);

    let snap = post(addr, json!({"cmd": "resync"})).await;
    let task = snap["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["id"] == task_id)
        .unwrap();
    assert_eq!(task["lane"], "Archived");
    assert_eq!(task["archived_from_lane"], "In Progress");

    // Remove
    let v = post(addr, json!({"cmd": "board_remove_task", "id": &task_id})).await;
    assert_eq!(v["ok"], true);
}

#[tokio::test]
async fn remove_group_cascades() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    post(
        addr,
        json!({"cmd": "add_agent", "name": "A", "group": "Eng"}),
    )
    .await;
    post(
        addr,
        json!({"cmd": "add_agent", "name": "B", "group": "Eng"}),
    )
    .await;

    let v = post(addr, json!({"cmd": "remove_group", "name": "Eng"})).await;
    assert_eq!(v["ok"], true);
    let removed = v["removed_agents"].as_array().unwrap();
    assert_eq!(removed.len(), 2);

    let snap = post(addr, json!({"cmd": "resync"})).await;
    assert_eq!(snap["groups"].as_array().unwrap().len(), 0);
    assert_eq!(snap["agents"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn global_settings_roundtrip() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(
        addr,
        json!({
            "cmd": "update_global_settings",
            "settings": {"default_command": "codex", "max_pipeline_depth": 5}
        }),
    )
    .await;
    assert_eq!(v["ok"], true);

    let v = post(addr, json!({"cmd": "get_global_settings"})).await;
    assert_eq!(v["default_command"], "codex");
    assert_eq!(v["max_pipeline_depth"], 5);
}

#[tokio::test]
async fn rename_lane_migrates_tasks() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    post(addr, json!({"cmd": "board_add_lane", "name": "Review"})).await;

    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "T", "group": "Eng", "lane": "Review"}),
    )
    .await;
    let task_id = t["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "board_rename_lane", "from": "Review", "to": "QA"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    let snap = post(addr, json!({"cmd": "resync"})).await;
    let task = snap["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["id"] == task_id)
        .unwrap();
    assert_eq!(task["lane"], "QA");
}

#[tokio::test]
async fn reserved_lane_rename_rejected() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(
        addr,
        json!({"cmd": "board_rename_lane", "from": "Backlog", "to": "Inbox"}),
    )
    .await;
    assert!(v["error"].is_string());
}
