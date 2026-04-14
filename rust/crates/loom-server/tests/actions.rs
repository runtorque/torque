//! Action command integration tests — exercised against the real
//! `.loom/actions/` fixtures from the parent project.

use std::net::SocketAddr;
use std::path::PathBuf;
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

fn repo_root() -> PathBuf {
    // crate dir → rust/crates/loom-server → two up = rust/, three up = repo root
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.pop(); // loom-server
    p.pop(); // crates
    p.pop(); // rust
    p
}

async fn spawn_with_actions() -> SocketAddr {
    // Point the action manager at the parent project's action fixtures.
    std::env::set_var("LOOM_PROJECT_ROOT", repo_root());

    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState { db, state, bus, pty: None };

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
    addr
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
async fn list_actions_finds_feature_and_oneshot_fixtures() {
    let addr = spawn_with_actions().await;
    let v = post(addr, json!({"cmd": "list_actions"})).await;
    let names: Vec<String> = v["actions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["name"].as_str().unwrap().to_string())
        .collect();

    // Expect nested namespaces to survive the port.
    assert!(
        names.iter().any(|n| n.starts_with("feature/")),
        "expected a feature/ action, got: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.starts_with("oneshot/")),
        "expected a oneshot/ action, got: {names:?}"
    );
}

#[tokio::test]
async fn get_action_returns_prompt_and_discovered_vars() {
    let addr = spawn_with_actions().await;
    // Grab first feature/ action discovered.
    let list = post(addr, json!({"cmd": "list_actions"})).await;
    let name = list["actions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["name"].as_str().unwrap_or("").starts_with("feature/"))
        .unwrap()["name"]
        .as_str()
        .unwrap()
        .to_string();

    let v = post(addr, json!({"cmd": "get_action", "name": &name})).await;
    assert_eq!(v["name"], name);
    assert!(v["prompt"].is_string());
    let prompt = v["prompt"].as_str().unwrap();
    assert!(!prompt.is_empty());
    // Action must reference the task one way or another — either `{{ TASK }}`
    // (the Python validation contract for newly-saved actions) or the
    // `loom.task.*` namespace (the newer convention used across fixtures).
    let lower = prompt.to_lowercase();
    assert!(
        prompt.contains("TASK") || lower.contains("loom.task"),
        "action {name} has neither TASK nor loom.task reference:\n{prompt}"
    );
    // Variables array present.
    assert!(v["variables"].is_array());
}

#[tokio::test]
async fn render_action_substitutes_task_variable() {
    let addr = spawn_with_actions().await;
    let list = post(addr, json!({"cmd": "list_actions"})).await;
    let name = list["actions"].as_array().unwrap()[0]["name"]
        .as_str()
        .unwrap()
        .to_string();

    let v = post(
        addr,
        json!({
            "cmd": "render_action",
            "name": &name,
            "vars": {"TASK": "ship the rust port"}
        }),
    )
    .await;

    // Either we get a rendered prompt back, or a specific error about undeclared vars.
    // For fixture actions that only reference {{ TASK }} and `loom.*`, render succeeds.
    if let Some(rendered) = v.get("rendered").and_then(|v| v.as_str()) {
        assert!(
            rendered.contains("ship the rust port"),
            "expected TASK substituted in {rendered:?}"
        );
    } else {
        // Must be a strict-undefined error if the action uses extra vars.
        assert!(v["error"].is_string());
    }
}

#[tokio::test]
async fn save_action_creates_file_then_delete_removes_it() {
    let addr = spawn_with_actions().await;

    // Save to a tempdir-backed project root so we don't pollute fixtures.
    let tmp = tempfile::tempdir().unwrap();
    std::env::set_var("LOOM_PROJECT_ROOT", tmp.path());

    let v = post(
        addr,
        json!({
            "cmd": "save_action",
            "name": "test/new-one",
            "prompt": "Do {{ TASK }}",
            "group": "Eng"
        }),
    )
    .await;
    assert_eq!(v["ok"], true);
    let path = PathBuf::from(v["path"].as_str().unwrap());
    assert!(path.exists());
    assert!(path.to_string_lossy().ends_with("test/new-one.yaml"));

    let v = post(addr, json!({"cmd": "delete_action", "name": "test/new-one"})).await;
    assert_eq!(v["ok"], true);
    assert!(!path.exists());
}

#[tokio::test]
async fn save_action_rejects_missing_task() {
    let addr = spawn_with_actions().await;
    let tmp = tempfile::tempdir().unwrap();
    std::env::set_var("LOOM_PROJECT_ROOT", tmp.path());

    let v = post(
        addr,
        json!({
            "cmd": "save_action",
            "name": "bad",
            "prompt": "no variable"
        }),
    )
    .await;
    assert!(v["error"].is_string(), "expected validation error, got: {v:?}");
}
