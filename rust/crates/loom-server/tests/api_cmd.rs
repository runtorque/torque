//! Integration test: spin up the real axum server against an in-memory DB
//! and drive it through Python-compatible standalone command surfaces.

use std::net::SocketAddr;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;

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
        terminals: Default::default(),
    };

    let router = loom_server::app::build_router(app_state);
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
async fn ping_roundtrips_in_python_api_envelope() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(addr, json!({"cmd": "ping"})).await;
    assert_eq!(v["ok"], true);
    assert_eq!(v["data"]["pong"], true);
}

#[tokio::test]
async fn add_group_then_add_agent_supports_python_field_aliases() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    assert_eq!(v["ok"], true);

    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    assert_eq!(v["ok"], true);
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();
    let slug = v["data"]["slug"].as_str().unwrap();
    assert!(slug.starts_with("eng:"));

    let v = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(v["ok"], true);
    assert!(v["data"]["agents"].get(&agent_id).is_some());
    assert_eq!(v["data"]["groups"]["Eng"], json!([agent_id]));
}

#[tokio::test]
async fn board_task_update_accepts_top_level_python_fields() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;

    let v = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Fix bug", "group": "Eng"}),
    )
    .await;
    let task_id = v["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "board_update_task", "id": &task_id, "description": "Updated"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(
        snap["data"]["board_tasks"][&task_id]["description"],
        "Updated"
    );
}

#[tokio::test]
async fn remove_group_cascades() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
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

    let v = post(addr, json!({"cmd": "remove_group", "group": "Eng"})).await;
    assert_eq!(v["ok"], true);
    let removed = v["data"]["removed_agents"].as_array().unwrap();
    assert_eq!(removed.len(), 2);

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(snap["data"]["groups"].as_object().unwrap().len(), 0);
    assert_eq!(snap["data"]["agents"].as_object().unwrap().len(), 0);
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
    assert_eq!(v["ok"], true);
    assert_eq!(v["data"]["type"], "global_settings");
    assert_eq!(v["data"]["settings"]["default_command"], "codex");
    assert_eq!(v["data"]["settings"]["max_pipeline_depth"], 5);
}

#[tokio::test]
async fn rename_lane_migrates_tasks() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    post(addr, json!({"cmd": "board_add_lane", "name": "Review"})).await;

    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "T", "group": "Eng", "lane": "Review"}),
    )
    .await;
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "board_rename_lane", "from": "Review", "to": "QA"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(snap["data"]["board_tasks"][&task_id]["lane"], "QA");
}

#[tokio::test]
async fn reserved_lane_rename_rejected() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(
        addr,
        json!({"cmd": "board_rename_lane", "from": "Backlog", "to": "Inbox"}),
    )
    .await;
    assert_eq!(v["ok"], false);
    assert!(v["error"].is_string());
}

#[tokio::test]
async fn index_and_static_assets_are_served() {
    let (addr, _h) = spawn_test_server().await;
    let client = reqwest::Client::new();

    let index = client
        .get(format!("http://{}/", addr))
        .send()
        .await
        .unwrap()
        .text()
        .await
        .unwrap();
    assert!(index.contains("ws.js"));

    let static_resp = client
        .get(format!("http://{}/static/js/ws.js", addr))
        .send()
        .await
        .unwrap();
    assert!(static_resp.status().is_success());
    let js = static_resp.text().await.unwrap();
    assert!(js.contains("function connect()"));
}
