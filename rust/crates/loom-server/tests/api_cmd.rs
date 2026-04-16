//! Integration test: spin up the real axum server against an in-memory DB
//! and drive it through Python-compatible standalone command surfaces.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_pty::{LocalPtyBackend, PtyEvent};

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

async fn spawn_test_server_with_pty() -> (
    SocketAddr,
    Arc<Mutex<MatrixState>>,
    tokio::sync::mpsc::Receiver<PtyEvent>,
) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let (pty, rx) = LocalPtyBackend::new();
    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: Some(Arc::new(pty)),
        ui_agents: Default::default(),
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
        terminals: Default::default(),
    };

    let router = loom_server::app::build_router(app_state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, state, rx)
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
async fn get_config_uses_global_default_command_in_runtime_payload() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(
        addr,
        json!({
            "cmd": "update_global_settings",
            "settings": {"default_command": "codex"}
        }),
    )
    .await;
    assert_eq!(v["ok"], true);

    let v = post(addr, json!({"cmd": "get_config"})).await;
    assert_eq!(v["ok"], true, "response: {v:?}");
    assert_eq!(v["data"]["default_command"], "codex");
    assert_eq!(v["data"]["runtime"]["default_command"], "codex");
}

#[tokio::test]
async fn get_config_falls_back_to_repo_root_for_current_path() {
    let (addr, _h) = spawn_test_server().await;
    let expected = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .map(PathBuf::from)
        .unwrap()
        .to_string_lossy()
        .to_string();

    let v = post(addr, json!({"cmd": "get_config"})).await;
    assert_eq!(v["ok"], true, "response: {v:?}");
    assert_eq!(v["data"]["current_path"], expected);
    assert_eq!(v["data"]["resolved_agent_defaults"]["directory"], expected);
}

#[tokio::test]
async fn get_config_resolves_provider_default_command_when_boot_command_is_blank() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let v = post(
        addr,
        json!({
            "cmd": "update_group_settings",
            "group": "Eng",
            "settings": {
                "agent_provider": "codex",
                "agent_model": "gpt-5.4-mini",
                "agent_reasoning_effort": "high",
                "agent_boot_command": ""
            }
        }),
    )
    .await;
    assert_eq!(v["ok"], true);

    let v = post(addr, json!({"cmd": "get_config", "group": "Eng"})).await;
    assert_eq!(v["ok"], true, "response: {v:?}");
    assert_eq!(v["data"]["resolved_agent_defaults"]["provider"], "codex");
    assert_eq!(
        v["data"]["resolved_agent_defaults"]["command"],
        "codex --model gpt-5.4-mini -c model_reasoning_effort=high"
    );
    let reasoning_efforts = v["data"]["providers"]
        .as_array()
        .unwrap()
        .iter()
        .find(|provider| provider["name"] == "codex")
        .and_then(|provider| provider["reasoning_efforts"].as_array())
        .cloned()
        .unwrap_or_default();
    assert!(reasoning_efforts.iter().any(|value| value == "high"));
}

#[tokio::test]
async fn add_agent_starts_local_pty_session_and_uses_repo_root_defaults() {
    let (addr, state, mut pty_rx) = spawn_test_server_with_pty().await;
    let expected_repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .map(PathBuf::from)
        .unwrap()
        .to_string_lossy()
        .to_string();

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let v = post(
        addr,
        json!({
            "cmd": "update_global_settings",
            "settings": {"default_command": "codex"}
        }),
    )
    .await;
    assert_eq!(v["ok"], true);

    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    assert_eq!(v["ok"], true, "add_agent response: {v:?}");
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

    let spawned = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match pty_rx.recv().await {
                Some(PtyEvent::Spawned { cell_id, .. }) if cell_id == agent_id => break true,
                Some(_) => continue,
                None => break false,
            }
        }
    })
    .await
    .expect("timed out waiting for PTY spawn event");
    assert!(spawned, "PTY event stream closed before agent spawn");

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).expect("agent missing after add");
    assert_eq!(agent.command, "codex");
    assert_eq!(agent.agent_type, "codex");
    assert_eq!(agent.directory, expected_repo_root);
}

#[tokio::test]
async fn add_agent_installs_codex_mcp_and_hook_config_before_spawn() {
    let (addr, _state, mut pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let working_dir = tmp.path().to_string_lossy().to_string();

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let v = post(
        addr,
        json!({
            "cmd": "update_global_settings",
            "settings": {"default_command": "codex"}
        }),
    )
    .await;
    assert_eq!(v["ok"], true);

    let v = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Worker",
            "group": "Eng",
            "directory": working_dir,
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "add_agent response: {v:?}");
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

    let spawned = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match pty_rx.recv().await {
                Some(PtyEvent::Spawned { cell_id, .. }) if cell_id == agent_id => break true,
                Some(_) => continue,
                None => break false,
            }
        }
    })
    .await
    .expect("timed out waiting for PTY spawn event");
    assert!(spawned, "PTY event stream closed before agent spawn");

    let config = tokio::fs::read_to_string(tmp.path().join(".codex").join("config.toml"))
        .await
        .expect("codex config should be installed before spawn");
    assert!(config.contains("[mcp_servers.loom]"));
    assert!(config.contains("env_http_headers = { \"X-Loom-Cell-Id\" = \"LOOM_CELL_ID\" }"));
    let hooks = tokio::fs::read_to_string(tmp.path().join(".codex").join("hooks.json"))
        .await
        .expect("codex hooks should be installed before spawn");
    assert!(hooks.contains("SessionStart"));
    assert!(hooks.contains("PreToolUse"));
    assert!(hooks.contains("Stop"));
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
