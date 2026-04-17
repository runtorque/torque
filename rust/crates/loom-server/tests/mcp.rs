//! MCP protocol integration test.

use std::net::SocketAddr;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::{AgentCell, BoardTask, MatrixState};

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
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
        terminals: Default::default(),
        weaver_buffer: loom_server::weaver_buffer::WeaverEventBuffer::default(),
        notifier: loom_server::notifications::NotificationManager::new(),
    };

    let router = loom_server::app::base_router().with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

async fn spawn_test_server_with_state() -> (SocketAddr, Arc<Mutex<MatrixState>>) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: None,
        ui_agents: Default::default(),
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
        terminals: Default::default(),
        weaver_buffer: loom_server::weaver_buffer::WeaverEventBuffer::default(),
        notifier: loom_server::notifications::NotificationManager::new(),
    };

    let router = loom_server::app::base_router().with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, state)
}

async fn mcp_call(addr: SocketAddr, method: &str, params: Value) -> Value {
    mcp_call_with_header(addr, method, params, None).await
}

async fn mcp_call_with_header(
    addr: SocketAddr,
    method: &str,
    params: Value,
    cell_id: Option<&str>,
) -> Value {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    });
    let client = reqwest::Client::new();
    let mut req = client.post(format!("http://{}/mcp", addr));
    if let Some(cell_id) = cell_id {
        req = req.header("X-Loom-Cell-Id", cell_id);
    }
    req.json(&body)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap()
}

async fn mcp_notify(addr: SocketAddr, method: &str, params: Value) -> reqwest::StatusCode {
    let body = json!({
        "jsonrpc": "2.0",
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
        .status()
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
    assert_eq!(resp["result"]["protocolVersion"], "2025-03-26");
    assert!(resp["result"]["instructions"]
        .as_str()
        .unwrap()
        .contains("Loom manages AI agent sessions"));
}

#[tokio::test]
async fn mcp_initialized_notification_is_accepted() {
    let addr = spawn_test_server().await;
    let status = mcp_notify(addr, "notifications/initialized", json!({})).await;
    assert_eq!(status, reqwest::StatusCode::ACCEPTED);
}

#[tokio::test]
async fn mcp_ping_returns_empty_result() {
    let addr = spawn_test_server().await;
    let resp = mcp_call(addr, "ping", json!({})).await;
    assert_eq!(resp["jsonrpc"], "2.0");
    assert_eq!(resp["result"], json!({}));
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
async fn mcp_tools_list_returns_weaver_orchestration_tools_for_designated_weaver() {
    let (addr, state) = spawn_test_server_with_state().await;
    cmd_call(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let add = cmd_call(
        addr,
        json!({"cmd": "add_agent", "name": "Weaver", "group": "Eng"}),
    )
    .await;
    let weaver_id = add["data"]["agent_id"].as_str().unwrap();
    {
        let mut st = state.lock().await;
        let settings = st
            .group_settings
            .get_mut("Eng")
            .expect("group settings should exist");
        settings.weaver_agent_id = weaver_id.to_string();
    }

    let resp = mcp_call_with_header(addr, "tools/list", json!({}), Some(weaver_id)).await;
    let tools = resp["result"]["tools"].as_array().unwrap();
    let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert!(names.contains(&"weaver_board_summary"));
    assert!(names.contains(&"weaver_task_create"));
    assert!(names.contains(&"weaver_task_edit"));
    assert!(names.contains(&"weaver_task_verify"));
    assert!(names.contains(&"weaver_task_move"));
    assert!(names.contains(&"weaver_task_dispatch"));
    assert!(names.contains(&"weaver_batch_dispatch"));
    assert!(names.contains(&"weaver_task_resolve"));
    assert!(names.contains(&"weaver_agent_message"));
    assert!(names.contains(&"weaver_agent_close"));
    assert!(names.contains(&"weaver_agent_relaunch"));
}

#[tokio::test]
async fn mcp_weaver_task_create_creates_board_task() {
    let (addr, state) = spawn_test_server_with_state().await;
    cmd_call(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let add = cmd_call(
        addr,
        json!({"cmd": "add_agent", "name": "Weaver", "group": "Eng"}),
    )
    .await;
    let weaver_id = add["data"]["agent_id"].as_str().unwrap();
    {
        let mut st = state.lock().await;
        let settings = st
            .group_settings
            .get_mut("Eng")
            .expect("group settings should exist");
        settings.weaver_agent_id = weaver_id.to_string();
    }

    let resp = mcp_call_with_header(
        addr,
        "tools/call",
        json!({
            "name": "weaver_task_create",
            "arguments": {
                "title": "Ship MCP tools",
                "description": "Create from weaver MCP",
                "lane": "Backlog",
            }
        }),
        Some(weaver_id),
    )
    .await;
    assert!(resp["error"].is_null(), "unexpected error: {resp:?}");
    let task_id = resp["result"]["loom"]["task_id"]
        .as_str()
        .unwrap()
        .to_string();

    let snap = cmd_call(addr, json!({"cmd": "refresh"})).await;
    let task = snap["data"]["board_tasks"].get(&task_id).unwrap();
    assert_eq!(task["task"], "Ship MCP tools");
    assert_eq!(task["description"], "Create from weaver MCP");
    assert_eq!(task["group"], "Eng");
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
    let task_id = task_resp["data"]["task_id"].as_str().unwrap().to_string();
    let disp = cmd_call(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    let agent_id = disp["data"]["agent_id"].as_str().unwrap().to_string();

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
    let agent = snap["data"]["agents"].get(&agent_id).unwrap();
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
    let parent_id = task_resp["data"]["task_id"].as_str().unwrap().to_string();
    let disp = cmd_call(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &parent_id, "force_no_action": true}),
    )
    .await;
    let agent_id = disp["data"]["agent_id"].as_str().unwrap().to_string();

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
    let chain_arr = chain["data"]["chain"].as_array().unwrap();
    assert!(chain_arr.iter().any(|t| t["id"] == parent_id));
}

#[tokio::test]
async fn mcp_derive_review_fix_routes_back_to_parent_implementer() {
    let (addr, state) = spawn_test_server_with_state().await;
    let (implementer_id, reviewer_id) = {
        let mut st = state.lock().await;
        st.add_group("Eng").unwrap();

        let mut implementer = AgentCell::new("impl-1", "Implementer", "Eng");
        implementer.status = "idle".into();
        implementer.session_id = Some("impl-session".into());
        st.add_agent(implementer.clone()).unwrap();

        let mut reviewer = AgentCell::new("review-1", "Reviewer", "Eng");
        reviewer.status = "running".into();
        reviewer.session_id = Some("review-session".into());
        reviewer.current_task_id = "eng-2".into();
        st.add_agent(reviewer.clone()).unwrap();

        let mut root = BoardTask::new_minimal("eng-1", "Implement feature");
        root.group = "Eng".into();
        root.lane = "In Progress".into();
        root.action_name = "feature/implement".into();
        root.agent_id = implementer.id.clone();
        st.upsert_task(root).unwrap();

        let mut review = BoardTask::new_minimal("eng-2", "Review feature");
        review.group = "Eng".into();
        review.lane = "In Progress".into();
        review.action_name = "feature/review".into();
        review.agent_id = reviewer.id.clone();
        review.parent_task_id = "eng-1".into();
        review.pipeline_root_id = "eng-1".into();
        review.pipeline_depth = 1;
        st.upsert_task(review).unwrap();

        st.drain_deltas();
        (implementer.id, reviewer.id)
    };

    let resp = mcp_call(
        addr,
        "tools/call",
        json!({
            "name": "loom_derive",
            "arguments": {
                "agent_id": &reviewer_id,
                "description": "Fix the issues found",
                "action": "feature/implement",
                "context": "Please address the blocking review issues.",
            }
        }),
    )
    .await;
    assert!(resp["error"].is_null(), "derive failed: {resp:?}");

    let new_id = resp["result"]["loom"]["task_id"].as_str().unwrap();
    let snap = cmd_call(addr, json!({"cmd": "resync"})).await;
    let task = snap["data"]["board_tasks"].get(new_id).unwrap();
    assert_eq!(task["agent_id"], implementer_id);
    assert_eq!(task["lane"], "In Progress");
    assert_eq!(task["parent_task_id"], "eng-2");
    assert_eq!(task["pipeline_root_id"], "eng-1");
    assert_eq!(snap["data"]["agents"][&reviewer_id]["current_task_id"], "");
}
