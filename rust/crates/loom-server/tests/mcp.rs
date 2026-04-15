//! MCP protocol integration test.

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
use loom_server::mcp;
use loom_server::uploads;
use loom_server::ws;

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

    let router = Router::new()
        .merge(ws::routes())
        .merge(commands::routes())
        .merge(evt::routes())
        .merge(uploads::routes())
        .merge(mcp::routes())
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

async fn mcp_call(addr: SocketAddr, method: &str, params: Value) -> Value {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    });
    let client = reqwest::Client::new();
    client
        .post(format!("http://{}/mcp", addr))
        .json(&body)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap()
}

async fn cmd_call(addr: SocketAddr, body: Value) -> Value {
    let client = reqwest::Client::new();
    client
        .post(format!("http://{}/api/cmd", addr))
        .json(&body)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap()
}

#[tokio::test]
async fn mcp_initialize_returns_server_info() {
    let addr = spawn_test_server().await;
    let resp = mcp_call(addr, "initialize", json!({})).await;
    assert_eq!(resp["jsonrpc"], "2.0");
    assert_eq!(resp["result"]["serverInfo"]["name"], "loom");
    assert!(resp["result"]["protocolVersion"].is_string());
}

#[tokio::test]
async fn mcp_tools_list_returns_loom_tools() {
    let addr = spawn_test_server().await;
    let resp = mcp_call(addr, "tools/list", json!({})).await;
    let tools = resp["result"]["tools"].as_array().unwrap();
    let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert!(names.contains(&"loom_progress"));
    assert!(names.contains(&"loom_done"));
    assert!(names.contains(&"loom_derive"));
    assert!(names.contains(&"loom_ask"));
}

#[tokio::test]
async fn mcp_progress_updates_agent_activity() {
    let addr = spawn_test_server().await;
    cmd_call(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let task_resp = cmd_call(
        addr,
        json!({"cmd": "board_add_task", "task": "T", "group": "Eng"}),
    )
    .await;
    let task_id = task_resp["task_id"].as_str().unwrap().to_string();
    let disp = cmd_call(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    let agent_id = disp["agent_id"].as_str().unwrap().to_string();

    // Tool call: loom_progress
    let resp = mcp_call(
        addr,
        "tools/call",
        json!({
            "name": "loom_progress",
            "arguments": {"agent_id": &agent_id, "message": "halfway there"}
        }),
    )
    .await;
    assert!(resp["error"].is_null(), "unexpected error: {resp:?}");

    // Verify via resync
    let snap = cmd_call(addr, json!({"cmd": "resync"})).await;
    let agent = snap["agents"]
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["id"] == agent_id)
        .unwrap();
    assert_eq!(agent["activity_detail"], "halfway there");
}

#[tokio::test]
async fn mcp_derive_creates_child_task_with_parent_linkage() {
    let addr = spawn_test_server().await;
    cmd_call(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let task_resp = cmd_call(
        addr,
        json!({"cmd": "board_add_task", "task": "Root task", "group": "Eng"}),
    )
    .await;
    let parent_id = task_resp["task_id"].as_str().unwrap().to_string();
    let disp = cmd_call(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &parent_id, "force_no_action": true}),
    )
    .await;
    let agent_id = disp["agent_id"].as_str().unwrap().to_string();

    let resp = mcp_call(
        addr,
        "tools/call",
        json!({
            "name": "loom_derive",
            "arguments": {"agent_id": &agent_id, "description": "child work"}
        }),
    )
    .await;
    assert!(resp["error"].is_null(), "got error: {resp:?}");
    let new_id = resp["result"]["loom"]["task_id"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(!new_id.is_empty());

    // chain command shows the parent
    let chain = cmd_call(addr, json!({"cmd": "task_chain", "id": &new_id})).await;
    let chain_arr = chain["chain"].as_array().unwrap();
    assert!(chain_arr.iter().any(|t| t["id"] == parent_id));
}
