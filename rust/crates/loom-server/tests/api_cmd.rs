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
use loom_pty::PtyEvent;

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
        weaver_buffer: loom_server::weaver_buffer::WeaverEventBuffer::default(),
        notifier: loom_server::notifications::NotificationManager::new(),
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
    let (pty, rx) = loom_server::pty_backend::PtyBackend::new_local();
    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: Some(pty),
        ui_agents: Default::default(),
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
        terminals: Default::default(),
        weaver_buffer: loom_server::weaver_buffer::WeaverEventBuffer::default(),
        notifier: loom_server::notifications::NotificationManager::new(),
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

async fn mcp_tool_call(addr: SocketAddr, agent_id: &str, name: &str, arguments: Value) -> Value {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://{}/mcp", addr))
        .header("X-Loom-Cell-Id", agent_id)
        .json(&json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            }
        }))
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
async fn add_group_accepts_optional_default_directory() {
    let (addr, _h) = spawn_test_server().await;
    let v = post(
        addr,
        json!({"cmd": "add_group", "group": "Eng", "default_directory": "/tmp/eng"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(
        snap["data"]["group_settings"]["Eng"]["default_directory"],
        "/tmp/eng"
    );

    // Blank directory omitted behaves like before — field is empty.
    let v = post(addr, json!({"cmd": "add_group", "group": "Ops"})).await;
    assert_eq!(v["ok"], true);
    let snap = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(
        snap["data"]["group_settings"]["Ops"]["default_directory"],
        ""
    );
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

    // Opt out of worktree auto-creation so we can assert directory=repo_root
    // exactly. Worktree behavior is covered separately by add_agent_autocreates_worktree.
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng", "worktree": false}),
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
async fn add_agent_expands_tilde_directory_before_spawn_and_storage() {
    let (addr, state, mut pty_rx) = spawn_test_server_with_pty().await;
    let home = dirs::home_dir().expect("home directory should exist in tests");
    let tmp = tempfile::Builder::new()
        .prefix("loom-home-dir-")
        .tempdir_in(&home)
        .unwrap();
    let relative = tmp.path().strip_prefix(&home).unwrap();
    let requested_directory = format!("~/{}", relative.to_string_lossy());

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
            "directory": requested_directory,
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

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).expect("agent missing after add");
    assert_eq!(
        agent.directory,
        tmp.path().to_string_lossy().to_string(),
        "agent directory should be expanded before storage"
    );
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
async fn add_weaver_designates_group_and_delivers_initial_prompt() {
    let (addr, state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let prompt_capture = tmp.path().join("weaver-prompt.txt");
    let working_dir = tmp.path().to_string_lossy().to_string();
    let command = format!("cat > {}", prompt_capture.to_string_lossy());

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let v = post(
        addr,
        json!({
            "cmd": "weaver_update_settings",
            "group": "Eng",
            "autonomy_mode": "aggressive_auto_continue",
            "default_worker_concurrency": 3,
            "custom_instructions": "Keep the board moving.",
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "weaver_update_settings response: {v:?}");

    let v = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Weaver",
            "group": "Eng",
            "is_weaver": true,
            "command": command,
            "directory": working_dir,
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "add_agent response: {v:?}");
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

    // The typed first message is the weaver's custom instructions only —
    // the role/policy system prompt is delivered via
    // `--append-system-prompt-file` so the typed chat turn shouldn't
    // repeat it. Anchor on the custom-instructions sentinel.
    let prompt_text = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if contents.contains("Keep the board moving.") {
                    break contents;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for Weaver prompt capture");

    assert!(
        prompt_text.contains("Keep the board moving."),
        "typed message should include custom instructions: {prompt_text:?}"
    );
    assert!(
        !prompt_text.contains("You are the Weaver"),
        "typed message should NOT include system-prompt header: {prompt_text:?}"
    );
    assert!(
        !prompt_text.contains("## Operating Policy"),
        "typed message should NOT include policy section: {prompt_text:?}"
    );

    let st = state.lock().await;
    assert_eq!(st.get_group_settings("Eng").weaver_agent_id, agent_id);
}

#[tokio::test]
async fn relaunch_weaver_replays_weaver_prompt() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let prompt_capture = tmp.path().join("weaver-prompt.txt");
    let working_dir = tmp.path().to_string_lossy().to_string();
    let command = format!("cat > {}", prompt_capture.to_string_lossy());

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let v = post(
        addr,
        json!({
            "cmd": "weaver_update_settings",
            "group": "Eng",
            "custom_instructions": "Keep the board moving.",
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "weaver_update_settings response: {v:?}");

    let v = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Weaver",
            "group": "Eng",
            "is_weaver": true,
            "command": command,
            "directory": working_dir,
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "add_agent response: {v:?}");
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

    tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if contents.contains("Keep the board moving.") {
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for initial Weaver prompt capture");

    tokio::fs::remove_file(&prompt_capture)
        .await
        .expect("failed to clear prompt capture before relaunch");

    let v = post(addr, json!({"cmd": "relaunch_agent", "id": agent_id})).await;
    assert_eq!(v["ok"], true, "relaunch_agent response: {v:?}");

    let prompt_text = tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if contents.contains("Keep the board moving.") {
                    break contents;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for relaunched Weaver prompt capture");

    assert!(prompt_text.contains("Keep the board moving."));
    assert!(
        !prompt_text.contains("You are the Weaver"),
        "relaunch typed message should NOT include system-prompt header: {prompt_text:?}"
    );
}

#[tokio::test]
async fn weaver_without_custom_instructions_gets_kickoff_hint() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let prompt_capture = tmp.path().join("weaver-prompt.txt");
    let working_dir = tmp.path().to_string_lossy().to_string();
    let command = format!("cat > {}", prompt_capture.to_string_lossy());

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    // Intentionally DO NOT set custom_instructions — we want the
    // kickoff-hint fallback path.
    let v = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Weaver",
            "group": "Eng",
            "is_weaver": true,
            "command": command,
            "directory": working_dir,
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "add_agent response: {v:?}");

    let prompt_text = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if !contents.trim().is_empty() {
                    break contents;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for kickoff hint capture");

    assert!(
        prompt_text.contains("You're the weaver for this group."),
        "expected kickoff hint: {prompt_text:?}"
    );
    assert!(
        !prompt_text.contains("## Operating Policy"),
        "kickoff hint should not contain system-prompt policy section: {prompt_text:?}"
    );
}

#[tokio::test]
async fn removing_weaver_clears_group_designation_and_allows_recreate() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let working_dir = tmp.path().to_string_lossy().to_string();

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;

    let first = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Weaver",
            "group": "Eng",
            "is_weaver": true,
            "command": "cat",
            "directory": working_dir,
        }),
    )
    .await;
    assert_eq!(first["ok"], true, "first add_agent response: {first:?}");
    let first_id = first["data"]["agent_id"].as_str().unwrap().to_string();

    let removed = post(addr, json!({"cmd": "remove_agent", "id": first_id})).await;
    assert_eq!(removed["ok"], true, "remove_agent response: {removed:?}");

    let config = post(addr, json!({"cmd": "get_group_settings", "group": "Eng"})).await;
    assert_eq!(
        config["ok"], true,
        "get_group_settings response: {config:?}"
    );
    assert_eq!(config["data"]["settings"]["weaver_agent_id"], "");

    let second = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Weaver 2",
            "group": "Eng",
            "is_weaver": true,
            "command": "cat",
            "directory": tmp.path().to_string_lossy().to_string(),
        }),
    )
    .await;
    assert_eq!(second["ok"], true, "second add_agent response: {second:?}");
    assert_ne!(
        second["data"]["agent_id"].as_str().unwrap(),
        "",
        "recreated weaver should get a fresh agent id"
    );
}

#[tokio::test]
async fn preview_prompt_supports_project_feature_action_task_context_fields() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;

    let task = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Implement standalone parity",
            "description": "Carry the Python action context into Rust.",
            "group": "Eng",
            "action_name": "feature/implement",
        }),
    )
    .await;
    assert_eq!(task["ok"], true, "board_add_task response: {task:?}");
    let task_id = task["data"]["task_id"].as_str().unwrap().to_string();

    let preview = post(addr, json!({"cmd": "preview_prompt", "id": task_id})).await;
    assert_eq!(preview["ok"], true, "preview_prompt response: {preview:?}");
    let prompt = preview["data"]["prompt"].as_str().unwrap_or("");
    assert!(prompt.contains("Implement standalone parity"));
    assert!(prompt.contains("Carry the Python action context into Rust."));
}

#[tokio::test]
async fn dispatch_task_renders_project_feature_action_and_sends_prompt() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let prompt_capture = tmp.path().join("dispatch-prompt.txt");
    let working_dir = tmp.path().to_string_lossy().to_string();
    let command = format!("cat > {}", prompt_capture.to_string_lossy());

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let agent = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Worker",
            "group": "Eng",
            "command": command,
            "directory": working_dir,
        }),
    )
    .await;
    assert_eq!(agent["ok"], true, "add_agent response: {agent:?}");
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();

    let task = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Implement standalone parity",
            "description": "Carry the Python action context into Rust.",
            "group": "Eng",
            "action_name": "feature/implement",
            "agent_id": agent_id,
        }),
    )
    .await;
    assert_eq!(task["ok"], true, "board_add_task response: {task:?}");
    let task_id = task["data"]["task_id"].as_str().unwrap().to_string();

    let dispatched = post(addr, json!({"cmd": "dispatch_task", "id": task_id})).await;
    assert_eq!(
        dispatched["ok"], true,
        "dispatch_task response: {dispatched:?}"
    );

    let prompt_text = tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if contents.contains("Implement standalone parity") {
                    break contents;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for dispatched prompt capture");

    assert!(prompt_text.contains("Implement standalone parity"));
    assert!(prompt_text.contains("Carry the Python action context into Rust."));
}

#[tokio::test]
async fn board_add_task_preserves_child_lineage_fields() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;

    let root = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Root", "group": "Eng"}),
    )
    .await;
    let root_id = root["data"]["task_id"].as_str().unwrap().to_string();

    let child = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Need review",
            "group": "Eng",
            "parent_task_id": &root_id,
            "pipeline_root_id": &root_id,
            "pipeline_depth": 1,
            "status": "Awaiting Reply",
            "reply_agent_id": "agent-1",
            "labels": ["loom:human", "loom:derived"],
        }),
    )
    .await;
    assert_eq!(child["ok"], true, "board_add_task response: {child:?}");
    let child_id = child["data"]["task_id"].as_str().unwrap().to_string();
    assert_ne!(child_id, root_id);

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    let task = &snap["data"]["board_tasks"][&child_id];
    assert_eq!(task["parent_task_id"], root_id);
    assert_eq!(task["pipeline_root_id"], root_id);
    assert_eq!(task["pipeline_depth"], 1);
    assert_eq!(task["status"], "Awaiting Reply");
    assert_eq!(task["reply_agent_id"], "agent-1");
}

#[tokio::test]
async fn loom_ask_creates_human_child_task_and_marks_parent_awaiting_input() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let working_dir = tmp.path().to_string_lossy().to_string();

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let agent = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Worker",
            "group": "Eng",
            "command": "cat",
            "directory": working_dir,
        }),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();
    let root = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Implement feature",
            "group": "Eng",
            "agent_id": &agent_id,
        }),
    )
    .await;
    let root_id = root["data"]["task_id"].as_str().unwrap().to_string();
    let dispatched = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &root_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(dispatched["ok"], true, "dispatch response: {dispatched:?}");

    let ask = mcp_tool_call(
        addr,
        &agent_id,
        "loom_ask",
        json!({
            "question": "Need clarification",
            "description": "Should I ship A or B?"
        }),
    )
    .await;
    assert!(ask["error"].is_null(), "loom_ask response: {ask:?}");
    let ask_id = ask["result"]["loom"]["ask_task_id"]
        .as_str()
        .unwrap()
        .to_string();

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    let root_task = &snap["data"]["board_tasks"][&root_id];
    assert_eq!(root_task["status"], "Awaiting Input");
    let ask_task = &snap["data"]["board_tasks"][&ask_id];
    assert_eq!(ask_task["task"], "Need clarification");
    assert_eq!(ask_task["description"], "Should I ship A or B?");
    assert_eq!(ask_task["lane"], "Backlog");
    assert_eq!(ask_task["parent_task_id"], root_id);
    assert_eq!(ask_task["pipeline_root_id"], root_id);
    let labels = ask_task["labels"].as_array().cloned().unwrap_or_default();
    assert!(labels.iter().any(|label| label == "loom:human"));
}

#[tokio::test]
async fn resolve_ask_delivers_answer_and_clears_parent_status() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let prompt_capture = tmp.path().join("worker.txt");
    let working_dir = tmp.path().to_string_lossy().to_string();
    let command = format!("cat > {}", prompt_capture.to_string_lossy());

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let agent = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Worker",
            "group": "Eng",
            "command": command,
            "directory": working_dir,
        }),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();
    let root = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Implement feature",
            "group": "Eng",
            "agent_id": &agent_id,
        }),
    )
    .await;
    let root_id = root["data"]["task_id"].as_str().unwrap().to_string();
    let dispatched = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &root_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(dispatched["ok"], true, "dispatch response: {dispatched:?}");

    let ask = mcp_tool_call(
        addr,
        &agent_id,
        "loom_ask",
        json!({"question": "Need clarification"}),
    )
    .await;
    assert!(ask["error"].is_null(), "loom_ask response: {ask:?}");
    let ask_id = ask["result"]["loom"]["ask_task_id"]
        .as_str()
        .unwrap()
        .to_string();

    let resolved = post(
        addr,
        json!({
            "cmd": "resolve_ask",
            "task_id": &ask_id,
            "reply": "Choose option B"
        }),
    )
    .await;
    assert_eq!(resolved["ok"], true, "resolve_ask response: {resolved:?}");

    let delivered = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if contents.contains("Answer to your question")
                    && contents.contains("Choose option B")
                {
                    break contents;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for ask reply delivery");
    assert!(delivered.contains("Answer to your question \"Need clarification\":"));
    assert!(delivered.contains("Choose option B"));

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    assert_eq!(snap["data"]["board_tasks"][&ask_id]["lane"], "Done");
    assert_eq!(snap["data"]["board_tasks"][&root_id]["status"], "");
}

#[tokio::test]
async fn send_text_submits_to_idle_local_pty_agent() {
    let (addr, _state, _pty_rx) = spawn_test_server_with_pty().await;
    let tmp = tempfile::tempdir().unwrap();
    let prompt_capture = tmp.path().join("send-text.txt");
    let working_dir = tmp.path().to_string_lossy().to_string();
    let command = format!("cat > {}", prompt_capture.to_string_lossy());

    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    let agent = post(
        addr,
        json!({
            "cmd": "add_agent",
            "name": "Worker",
            "group": "Eng",
            "command": command,
            "directory": working_dir,
        }),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();

    let sent = post(
        addr,
        json!({"cmd": "send_text", "cell_id": &agent_id, "text": "resume work"}),
    )
    .await;
    assert_eq!(sent["ok"], true, "send_text response: {sent:?}");

    let delivered = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            if let Ok(contents) = tokio::fs::read_to_string(&prompt_capture).await {
                if contents.contains("resume work") {
                    break contents;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("timed out waiting for send_text delivery");
    assert!(delivered.contains("resume work"));
}

#[tokio::test]
async fn loom_reply_completes_weaver_followup_task() {
    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;

    let agent = post(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();

    let followup = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Weaver: Check status",
            "description": "Follow-up from the weaver",
            "group": "Eng",
            "lane": "Backlog",
            "status": "Awaiting Reply",
            "labels": ["loom:weaver-message"],
            "reply_agent_id": agent_id,
        }),
    )
    .await;
    let task_id = followup["data"]["task_id"].as_str().unwrap().to_string();

    let reply = mcp_tool_call(
        addr,
        &agent_id,
        "loom_reply",
        json!({"task": task_id, "message": "On it"}),
    )
    .await;
    assert!(reply["error"].is_null(), "loom_reply response: {reply:?}");

    let snap = post(addr, json!({"cmd": "refresh"})).await;
    let task = snap["data"]["board_tasks"].get(&task_id).unwrap();
    assert_eq!(task["lane"], "Done");
    assert_eq!(task["status"], "");
    assert!(task["messages"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["action"] == "reply" && entry["message"] == "On it"));
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
