//! Compatibility-surface tests for Python-oriented upload/event/history
//! commands.

use std::net::SocketAddr;
use std::sync::{Arc, Mutex as StdMutex};

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;

static ENV_LOCK: StdMutex<()> = StdMutex::new(());

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
async fn get_events_and_history_commands_return_python_compat_shapes() {
    let (addr, _h) = spawn_test_server().await;

    let events = post(addr, json!({"cmd": "get_events", "limit": 10})).await;
    assert_eq!(events["ok"], true);
    assert_eq!(events["data"]["type"], "events_page");
    assert!(events["data"]["events"].is_array());

    let history = post(addr, json!({"cmd": "get_agent_history", "limit": 10})).await;
    assert_eq!(history["ok"], true);
    assert_eq!(history["data"]["type"], "agent_history_list");
    assert!(history["data"]["records"].is_array());

    let detail = post(addr, json!({"cmd": "get_agent_history_detail", "agent_id": "missing"})).await;
    assert_eq!(detail["ok"], false);
    assert_eq!(detail["error"], "Agent not found in history");
}

#[tokio::test]
async fn upload_endpoint_and_remove_attachment_command_roundtrip() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let temp = tempfile::tempdir().unwrap();
    std::env::set_var("LOOM_INSTALL_DIR", temp.path());

    let (addr, _h) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "group": "Eng"})).await;
    post(addr, json!({"cmd": "board_add_task", "group": "Eng", "task": "Ship it"})).await;

    let client = reqwest::Client::new();
    let boundary = "loom-test-boundary";
    let body = format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"task_id\"\r\n\r\neng-1\r\n\
         --{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"hello world.txt\"\r\n\
         Content-Type: text/plain\r\n\r\nhello from rust\r\n--{boundary}--\r\n"
    );
    let upload = client
        .post(format!("http://{}/api/upload", addr))
        .header(
            reqwest::header::CONTENT_TYPE,
            format!("multipart/form-data; boundary={boundary}"),
        )
        .body(body)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(upload["ok"], true);
    assert_eq!(upload["data"][0]["filename"], "hello_world.txt");

    let attachment_path = temp
        .path()
        .join("attachments")
        .join("eng-1")
        .join("hello_world.txt");
    assert!(attachment_path.exists());

    let removed = post(
        addr,
        json!({"cmd": "remove_attachment", "task_id": "eng-1", "filename": "hello_world.txt"}),
    )
    .await;
    assert_eq!(removed["ok"], true);
    assert!(!attachment_path.exists());

    std::env::remove_var("LOOM_INSTALL_DIR");
}
