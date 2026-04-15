//! Integration tests for the batch of commands added alongside the initial
//! native UI: schedule CRUD, memory CRUD + links, and the per-group board
//! view state setters. Each test exercises the HTTP `/api/cmd` path.

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

async fn spawn() -> (SocketAddr, Arc<Mutex<MatrixState>>, LoomDb) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let app_state = loom_server::app::AppState {
        db: db.clone(),
        state: state.clone(),
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
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, state, db)
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

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

#[tokio::test]
async fn schedule_crud_round_trip() {
    let (addr, state, db) = spawn().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;

    let v = post(
        addr,
        json!({
            "cmd": "schedule_create",
            "name": "Nightly",
            "group": "Eng",
            "task_template": "Run nightly sync",
            "labels": ["cron"],
            "cron_expr": "0 0 * * *",
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "create: {v:?}");
    let id = v["schedule_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "schedule_update", "id": &id, "fields": {"task_template": "updated"}}),
    )
    .await;
    assert_eq!(v["ok"], true, "update: {v:?}");
    assert_eq!(state.lock().await.schedules[&id].task_template, "updated");

    post(addr, json!({"cmd": "schedule_disable", "id": &id})).await;
    assert!(!state.lock().await.schedules[&id].enabled);
    post(addr, json!({"cmd": "schedule_enable", "id": &id})).await;
    assert!(state.lock().await.schedules[&id].enabled);

    let v = post(addr, json!({"cmd": "schedule_list"})).await;
    assert_eq!(v["schedules"].as_array().unwrap().len(), 1);

    // schedule_run should create a task.
    let v = post(addr, json!({"cmd": "schedule_run", "id": &id})).await;
    assert_eq!(v["ok"], true);
    let task_id = v["task_id"].as_str().unwrap().to_string();
    {
        let st = state.lock().await;
        let task = st
            .board_tasks
            .get(&task_id)
            .expect("task from schedule_run");
        assert_eq!(task.lane, "To Do");
        assert_eq!(task.task, "updated");
        assert_eq!(st.schedules[&id].run_count, 1);
        assert_eq!(st.schedules[&id].last_task_id, task_id);
    }

    // Remove.
    post(addr, json!({"cmd": "schedule_remove", "id": &id})).await;
    assert!(!state.lock().await.schedules.contains_key(&id));

    // Verify persistence: reload from DB and confirm the schedule isn't there.
    let reloaded = db.load_all().await.unwrap();
    assert!(reloaded.schedules.is_empty());
}

#[tokio::test]
async fn schedule_create_rejects_missing_group() {
    let (addr, _, _) = spawn().await;
    let v = post(
        addr,
        json!({"cmd": "schedule_create", "name": "X", "group": "nope"}),
    )
    .await;
    assert!(v["error"].is_string());
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

#[tokio::test]
async fn memory_publish_and_list_filters() {
    let (addr, _state, db) = spawn().await;

    post(
        addr,
        json!({
            "cmd": "memory_publish",
            "entry_type": "finding",
            "title": "SQLite WAL mode",
            "content": "We use WAL for concurrent reads.",
            "group": "Eng",
            "scope_kind": "project",
            "pinned": true,
        }),
    )
    .await;
    post(
        addr,
        json!({
            "cmd": "memory_publish",
            "entry_type": "decision",
            "title": "Use minijinja",
            "content": "Drop-in for Jinja2.",
            "group": "Eng",
            "scope_kind": "group",
            "scope_ref": "Eng",
        }),
    )
    .await;
    post(
        addr,
        json!({
            "cmd": "memory_publish",
            "entry_type": "note",
            "title": "Gemini adapter",
            "content": "Still a stub.",
            "group": "Ops",
        }),
    )
    .await;

    // All Eng entries (2).
    let v = post(addr, json!({"cmd": "memory_list", "group": "Eng"})).await;
    let entries = v["entries"].as_array().unwrap();
    assert_eq!(entries.len(), 2);
    // Pinned sorts first.
    assert_eq!(entries[0]["title"], "SQLite WAL mode");

    // Filter by type.
    let v = post(
        addr,
        json!({"cmd": "memory_list", "entry_type": "decision"}),
    )
    .await;
    assert_eq!(v["entries"].as_array().unwrap().len(), 1);
    assert_eq!(v["entries"][0]["title"], "Use minijinja");

    // pinned_only.
    let v = post(addr, json!({"cmd": "memory_list", "pinned_only": true})).await;
    assert_eq!(v["entries"].as_array().unwrap().len(), 1);

    // Substring search (case insensitive).
    let v = post(addr, json!({"cmd": "memory_list", "search": "JINJA"})).await;
    assert_eq!(v["entries"].as_array().unwrap().len(), 1);

    // Persistence: reload from DB, count stays.
    let reloaded = db.load_all().await.unwrap();
    assert_eq!(reloaded.memory_entries.len(), 3);
}

#[tokio::test]
async fn memory_pin_unpin_and_link_detach() {
    let (addr, state, db) = spawn().await;

    let v = post(
        addr,
        json!({
            "cmd": "memory_publish",
            "entry_type": "note",
            "title": "scratch",
            "content": "x",
        }),
    )
    .await;
    let id = v["id"].as_str().unwrap().to_string();

    post(addr, json!({"cmd": "memory_pin", "id": &id})).await;
    assert!(state.lock().await.memory_entries[&id].pinned);
    post(addr, json!({"cmd": "memory_unpin", "id": &id})).await;
    assert!(!state.lock().await.memory_entries[&id].pinned);

    // Attach a link.
    let v = post(
        addr,
        json!({
            "cmd": "memory_link",
            "id": &id,
            "target_kind": "task",
            "target_ref": "eng-1"
        }),
    )
    .await;
    assert_eq!(v["ok"], true);
    assert_eq!(state.lock().await.memory_entries[&id].links.len(), 1);

    // Idempotent re-attach.
    post(
        addr,
        json!({
            "cmd": "memory_link",
            "id": &id,
            "target_kind": "task",
            "target_ref": "eng-1"
        }),
    )
    .await;
    assert_eq!(state.lock().await.memory_entries[&id].links.len(), 1);

    // Detach.
    post(
        addr,
        json!({
            "cmd": "memory_link",
            "id": &id,
            "target_kind": "task",
            "target_ref": "eng-1",
            "detach": true
        }),
    )
    .await;
    assert!(state.lock().await.memory_entries[&id].links.is_empty());

    // Persistence.
    let reloaded = db.load_all().await.unwrap();
    assert!(reloaded.memory_entries[&id].links.is_empty());
}

#[tokio::test]
async fn memory_publish_rejects_oversize_content() {
    let (addr, _, _) = spawn().await;
    let content = "x".repeat(4001);
    let v = post(
        addr,
        json!({
            "cmd": "memory_publish",
            "entry_type": "note",
            "title": "big",
            "content": content,
        }),
    )
    .await;
    assert!(
        v["error"]
            .as_str()
            .map(|e| e.contains("4000"))
            .unwrap_or(false),
        "expected size error, got: {v:?}"
    );
}

// ---------------------------------------------------------------------------
// Board view state
// ---------------------------------------------------------------------------

#[tokio::test]
async fn set_layout_persists_and_is_idempotent() {
    let (addr, state, db) = spawn().await;

    let layout = json!({
        "type": "split",
        "axis": "horizontal",
        "ratio": 0.6,
        "first": {"type": "leaf", "panel": {"kind": "board"}},
        "second": {
            "type": "split",
            "axis": "vertical",
            "ratio": 0.5,
            "first": {"type": "leaf", "panel": {"kind": "memory"}},
            "second": {"type": "leaf", "panel": {"kind": "terminal", "id": null}}
        }
    });
    let v = post(addr, json!({"cmd": "set_layout", "layout": layout.clone()})).await;
    assert_eq!(v["ok"], true, "response: {v:?}");

    {
        let st = state.lock().await;
        let stored = st.content_layout.as_ref().expect("layout should be set");
        // 3 leaves: board, memory, terminal.
        assert_eq!(stored.leaves().len(), 3);
    }

    // Persisted: a fresh load sees the same layout.
    let reloaded = db.load_all().await.unwrap();
    assert!(reloaded.content_layout.is_some());
    assert_eq!(reloaded.content_layout.unwrap().leaves().len(), 3);

    // Clear via null payload.
    let v = post(addr, json!({"cmd": "set_layout", "layout": null})).await;
    assert_eq!(v["ok"], true);
    {
        let st = state.lock().await;
        assert!(st.content_layout.is_none());
    }
    let reloaded = db.load_all().await.unwrap();
    assert!(reloaded.content_layout.is_none());
}

#[tokio::test]
async fn set_layout_rejects_invalid_payload() {
    let (addr, _, _) = spawn().await;
    let v = post(
        addr,
        json!({
            "cmd": "set_layout",
            "layout": {"type": "leaf", "panel": "not-an-object"}
        }),
    )
    .await;
    assert!(
        v["error"].as_str().is_some(),
        "expected validation error, got: {v:?}"
    );
}

#[tokio::test]
async fn board_view_state_persists_per_group() {
    let (addr, state, db) = spawn().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    post(addr, json!({"cmd": "add_group", "name": "Ops"})).await;

    post(
        addr,
        json!({
            "cmd": "board_set_filters",
            "group": "Eng",
            "filters": {"label": "blocked"}
        }),
    )
    .await;
    post(
        addr,
        json!({
            "cmd": "board_set_saved_views",
            "group": "Eng",
            "views": [{"id": "v1", "name": "My blocked"}]
        }),
    )
    .await;
    post(
        addr,
        json!({
            "cmd": "board_set_lane_sorts",
            "group": "Eng",
            "sorts": {"In Progress": "age_desc"}
        }),
    )
    .await;
    post(
        addr,
        json!({"cmd": "board_set_card_density", "group": "Eng", "density": "compact"}),
    )
    .await;
    post(
        addr,
        json!({"cmd": "board_set_card_density", "group": "Ops", "density": "expanded"}),
    )
    .await;

    {
        let st = state.lock().await;
        assert_eq!(st.board_filters_by_group["Eng"]["label"], "blocked");
        assert_eq!(st.board_saved_views_by_group["Eng"][0]["id"], "v1");
        assert_eq!(
            st.board_lane_sorts_by_group["Eng"]["In Progress"],
            "age_desc"
        );
        assert_eq!(st.board_card_density_by_group["Eng"], "compact");
        assert_eq!(st.board_card_density_by_group["Ops"], "expanded");
    }

    // Persistence: a fresh load should see the same state.
    let reloaded = db.load_all().await.unwrap();
    assert_eq!(reloaded.board_filters_by_group["Eng"]["label"], "blocked");
    assert_eq!(reloaded.board_card_density_by_group["Eng"], "compact");
    assert_eq!(reloaded.board_card_density_by_group["Ops"], "expanded");
}
