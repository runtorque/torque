//! Dispatch + ai_report integration test.
//!
//! The PTY backend is not attached in tests, so the dispatcher exercises the
//! state transitions (task lane, agent link, tasks_dispatched counter)
//! without actually spawning a shell. PTY behaviour is unit-tested in
//! `loom-pty` separately.

use std::net::SocketAddr;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_server::app::{UiAgentInput, UiAgentRegistry};

async fn spawn_test_server() -> (SocketAddr, Arc<Mutex<MatrixState>>) {
    let (addr, state, _reg) = spawn_test_server_full().await;
    (addr, state)
}

async fn spawn_test_server_full() -> (SocketAddr, Arc<Mutex<MatrixState>>, UiAgentRegistry) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let ui_agents = UiAgentRegistry::default();
    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: None,
        ui_agents: ui_agents.clone(),
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
    (addr, state, ui_agents)
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
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(v["ok"], true, "dispatch response: {v:?}");
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

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
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

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
    assert!(
        task.messages.len() >= 1,
        "messages log should record the action"
    );
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
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();
    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();

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
async fn dispatch_routes_through_ui_registry_when_attached() {
    let (addr, state, ui_agents) = spawn_test_server_full().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    // Add an agent and register it in the UI.
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "W", "group": "Eng"}),
    )
    .await;
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();
    let mut rx = ui_agents.register(agent_id.clone());

    // Create a task pinned to that agent and dispatch it.
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Hello", "group": "Eng", "agent_id": &agent_id}),
    )
    .await;
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(v["ok"], true, "dispatch response: {v:?}");

    // Expect at least two messages: the rendered prompt + a bare "\r".
    let mut received: Vec<UiAgentInput> = Vec::new();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while received.len() < 2 && std::time::Instant::now() < deadline {
        match tokio::time::timeout(std::time::Duration::from_millis(250), rx.recv()).await {
            Ok(Some(s)) => received.push(s),
            Ok(None) => break,
            Err(_) => {}
        }
    }
    assert!(
        received
            .iter()
            .any(|s| matches!(s, UiAgentInput::Text(text) if text.contains("Hello"))),
        "expected rendered prompt to include task text, got: {received:?}"
    );
    assert!(
        received.iter().any(|s| matches!(s, UiAgentInput::Submit)),
        "expected trailing submit event, got: {received:?}"
    );

    // PTY backend was None; task state should still be updated.
    let st = state.lock().await;
    assert_eq!(st.board_tasks.get(&task_id).unwrap().lane, "In Progress");
}

#[tokio::test]
async fn send_text_routes_through_ui_registry_when_attached() {
    let (addr, _state, ui_agents) = spawn_test_server_full().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "W", "group": "Eng"}),
    )
    .await;
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();
    let mut rx = ui_agents.register(agent_id.clone());

    let v = post(
        addr,
        json!({"cmd": "send_text", "cell_id": &agent_id, "text": "hi there"}),
    )
    .await;
    assert_eq!(v["ok"], true);

    let got = tokio::time::timeout(std::time::Duration::from_secs(2), rx.recv())
        .await
        .expect("send_text should route to UI registry")
        .expect("sender must still be open");
    assert_eq!(got, UiAgentInput::Text("hi there".into()));
    let submit = tokio::time::timeout(std::time::Duration::from_secs(2), rx.recv())
        .await
        .expect("send_text should submit through UI registry")
        .expect("sender must still be open");
    assert_eq!(submit, UiAgentInput::Submit);
}

#[tokio::test]
async fn dispatch_prefers_request_body_agent_id_over_creating_new_agent() {
    let (addr, state) = spawn_test_server().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let agent = post(
        addr,
        json!({"cmd": "add_agent", "name": "Existing", "group": "Eng"}),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();

    let task = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Reuse the existing agent", "group": "Eng"}),
    )
    .await;
    let task_id = task["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({
            "cmd": "dispatch_task",
            "task_id": &task_id,
            "agent_id": &agent_id,
            "force_no_action": true
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "dispatch response: {v:?}");
    assert_eq!(v["data"]["agent_id"], agent_id);

    let st = state.lock().await;
    assert_eq!(
        st.agents.len(),
        1,
        "dispatch should reuse the existing agent"
    );
    let task = st.board_tasks.get(&task_id).unwrap();
    assert_eq!(task.agent_id, agent_id);
    assert_eq!(task.lane, "In Progress");
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
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();

    // Pin to a tempdir so the action manager doesn't find any fixtures.
    let tmp = tempfile::tempdir().unwrap();
    std::env::set_var("LOOM_PROJECT_ROOT", tmp.path());

    let v = post(addr, json!({"cmd": "dispatch_task", "task_id": &task_id})).await;
    assert_eq!(
        v["data"]["warning"], "dispatch_action_missing",
        "got: {v:?}"
    );
}

#[tokio::test]
async fn clear_agent_context_resets_counters_and_link() {
    let (addr, state) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let v = post(
        addr,
        json!({"cmd": "add_agent", "name": "W", "group": "Eng"}),
    )
    .await;
    let agent_id = v["data"]["agent_id"].as_str().unwrap().to_string();
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "X", "group": "Eng"}),
    )
    .await;
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();
    post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &task_id, "force_no_action": true}),
    )
    .await;

    let v = post(addr, json!({"cmd": "clear_agent_context", "id": &agent_id})).await;
    assert_eq!(v["ok"], true);

    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert_eq!(agent.tasks_dispatched, 0);
    assert_eq!(agent.current_task_id, "");
    assert!(!agent.needs_attention);
}

#[tokio::test]
async fn board_verify_task_updates_verification_state() {
    let (addr, state) = spawn_test_server().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "X", "group": "Eng"}),
    )
    .await;
    let task_id = t["data"]["task_id"].as_str().unwrap().to_string();

    let v = post(
        addr,
        json!({
            "cmd": "board_verify_task",
            "id": &task_id,
            "state": "verified",
            "mode": "manual",
            "notes": "looks good",
            "updated_by": "human",
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "response: {v:?}");

    let st = state.lock().await;
    let task = st.board_tasks.get(&task_id).unwrap();
    assert_eq!(task.verification_state, "verified");
    assert_eq!(task.verification_mode, "manual");
    assert_eq!(task.verification_notes, "looks good");
    assert_eq!(task.verification_updated_by, "human");
    assert!(!task.verification_updated_at.is_empty());
}

#[tokio::test]
async fn board_set_panel_persists_and_idempotent_returns_early() {
    let (addr, state) = spawn_test_server().await;
    let v = post(addr, json!({"cmd": "board_set_panel", "panel": "weaver"})).await;
    assert_eq!(v["ok"], true);
    assert_eq!(v["data"]["panel"], "weaver");

    {
        let st = state.lock().await;
        assert_eq!(st.panel_active, "weaver");
    }

    // Idempotent reselect: still OK but doesn't emit a fresh delta.
    let v = post(addr, json!({"cmd": "board_set_panel", "panel": "weaver"})).await;
    assert_eq!(v["ok"], true);
}

#[tokio::test]
async fn resolve_ask_moves_ask_to_done_and_logs_reply() {
    let (addr, state, ui_agents) = spawn_test_server_full().await;
    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;

    let agent = post(
        addr,
        json!({"cmd": "add_agent", "name": "Worker", "group": "Eng"}),
    )
    .await;
    let agent_id = agent["data"]["agent_id"].as_str().unwrap().to_string();
    let mut rx = ui_agents.register(agent_id.clone());

    // Create a parent task + an ask child.
    let parent = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Parent", "group": "Eng", "agent_id": &agent_id}),
    )
    .await;
    let parent_id = parent["data"]["task_id"].as_str().unwrap().to_string();

    let ask = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Need info", "group": "Eng"}),
    )
    .await;
    let ask_id = ask["data"]["task_id"].as_str().unwrap().to_string();

    // Link ask to parent + flag it as human via update.
    post(
        addr,
        json!({
            "cmd": "board_update_task",
            "id": &ask_id,
            "fields": {"parent_task_id": &parent_id, "labels": ["loom:human"]}
        }),
    )
    .await;

    let v = post(
        addr,
        json!({
            "cmd": "resolve_ask",
            "task_id": &ask_id,
            "reply": "Go with option B"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "response: {v:?}");
    assert_eq!(v["data"]["parent_task_id"], parent_id);

    let delivered = tokio::time::timeout(std::time::Duration::from_secs(2), rx.recv())
        .await
        .expect("resolve_ask should deliver a reply to the parent agent")
        .expect("sender must still be open");
    assert!(matches!(delivered, UiAgentInput::Text(text) if text.contains("Go with option B")));

    let st = state.lock().await;
    assert_eq!(st.board_tasks.get(&ask_id).unwrap().lane, "Done");

    let parent = st.board_tasks.get(&parent_id).unwrap();
    let last = parent.messages.last().expect("reply should be appended");
    assert_eq!(last["action"], "ask_reply");
    assert_eq!(last["message"], "Go with option B");
    assert_eq!(last["from_task_id"], ask_id);
}

#[tokio::test]
async fn ai_report_done_cascades_to_root_when_all_descendants_done() {
    // Reproduces the live weaver report: root task stays In Progress even
    // after ask + review + fix children are all Done. Cascade must walk up
    // from the deepest Done descendant to promote the root.
    let (addr, state) = spawn_test_server().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Root work", "group": "Eng"}),
    )
    .await;
    let root_id = t["data"]["task_id"].as_str().unwrap().to_string();

    // Dispatch the root so it enters In Progress and has a linked agent.
    let dispatch = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &root_id, "force_no_action": true}),
    )
    .await;
    assert_eq!(dispatch["ok"], true);
    let worker_id = dispatch["data"]["agent_id"]
        .as_str()
        .unwrap()
        .to_string();

    // Manually build the two-level child hierarchy the derive path would
    // produce: root → review (derived from root) → fix (derived from review).
    let review = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Review it",
            "group": "Eng",
            "parent_task_id": &root_id,
            "pipeline_root_id": &root_id,
            "labels": ["loom:derived"]
        }),
    )
    .await;
    let review_id = review["data"]["task_id"].as_str().unwrap().to_string();

    let fix = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Fix it",
            "group": "Eng",
            "parent_task_id": &review_id,
            "pipeline_root_id": &root_id,
            "labels": ["loom:derived"]
        }),
    )
    .await;
    let fix_id = fix["data"]["task_id"].as_str().unwrap().to_string();

    // The live scenario: reviewer back-derives without closing its own
    // task, so review stays In Progress (with a status badge like
    // "Awaiting Fix") while the worker takes over on the fix. Put review
    // into In Progress to mirror that.
    post(
        addr,
        json!({"cmd": "board_move_task", "id": &review_id, "lane": "In Progress"}),
    )
    .await;
    // Pipeline-aware status the derive path stamps. Leaving non-empty so
    // the cascade has to tolerate it.
    {
        let mut st = state.lock().await;
        if let Some(r) = st.board_tasks.get_mut(&review_id) {
            r.status = "Awaiting Fix".into();
        }
    }

    // Point worker at the deepest child, then fire loom_done on it.
    {
        let mut st = state.lock().await;
        let worker = st.agents.get_mut(&worker_id).unwrap();
        worker.current_task_id = fix_id.clone();
    }
    let done = post(
        addr,
        json!({
            "cmd": "ai_report",
            "action": "done",
            "agent_id": &worker_id,
            "task_id": &fix_id,
            "message": "all set"
        }),
    )
    .await;
    assert_eq!(done["ok"], true, "ai_report response: {done:?}");

    let st = state.lock().await;
    let root = st.board_tasks.get(&root_id).unwrap();
    assert_eq!(
        root.lane, "Done",
        "cascade should have promoted root after all descendants hit Done"
    );
    assert_eq!(st.board_tasks.get(&review_id).unwrap().lane, "Done");
    assert_eq!(st.board_tasks.get(&fix_id).unwrap().lane, "Done");
    let worker = st.agents.get(&worker_id).unwrap();
    assert!(
        worker.current_task_id.is_empty(),
        "done should clear the worker's current_task_id (got {:?})",
        worker.current_task_id
    );
}

#[tokio::test]
async fn resolve_ask_cascades_to_parent_after_sibling_done() {
    // Exact live-weaver scenario: worker emits loom_done on loom-1a3
    // (deepest fix task) BEFORE the human has answered the ask (loom-1a).
    // That cascade stops at loom-1 because loom-1a is still open. The
    // human then resolves the ask, which must continue the cascade and
    // mark loom-1 Done.
    let (addr, state, ui_agents) = spawn_test_server_full().await;

    post(addr, json!({"cmd": "add_group", "name": "Eng"})).await;
    let t = post(
        addr,
        json!({"cmd": "board_add_task", "task": "Root work", "group": "Eng"}),
    )
    .await;
    let root_id = t["data"]["task_id"].as_str().unwrap().to_string();
    let dispatch = post(
        addr,
        json!({"cmd": "dispatch_task", "task_id": &root_id, "force_no_action": true}),
    )
    .await;
    let worker_id = dispatch["data"]["agent_id"].as_str().unwrap().to_string();

    // Ask task opened while worker still on loom-1. Label `loom:human`
    // replicates handle_agent_ask.
    let ask = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Which option?",
            "group": "Eng",
            "parent_task_id": &root_id,
            "pipeline_root_id": &root_id,
            "lane": "Backlog",
            "labels": ["loom:human", "loom:derived"]
        }),
    )
    .await;
    let ask_id = ask["data"]["task_id"].as_str().unwrap().to_string();

    // Reviewer branch + fix branch.
    let review = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Review it",
            "group": "Eng",
            "parent_task_id": &root_id,
            "pipeline_root_id": &root_id,
            "lane": "In Progress",
            "labels": ["loom:derived"]
        }),
    )
    .await;
    let review_id = review["data"]["task_id"].as_str().unwrap().to_string();
    let fix = post(
        addr,
        json!({
            "cmd": "board_add_task",
            "task": "Fix it",
            "group": "Eng",
            "parent_task_id": &review_id,
            "pipeline_root_id": &root_id,
            "labels": ["loom:derived"]
        }),
    )
    .await;
    let fix_id = fix["data"]["task_id"].as_str().unwrap().to_string();

    // Worker finishes the fix first.
    {
        let mut st = state.lock().await;
        st.agents.get_mut(&worker_id).unwrap().current_task_id = fix_id.clone();
    }
    post(
        addr,
        json!({
            "cmd": "ai_report",
            "action": "done",
            "agent_id": &worker_id,
            "task_id": &fix_id,
            "message": "fix shipped"
        }),
    )
    .await;
    // At this point review should have cascaded to Done, but root should
    // still be In Progress because the ask is open.
    {
        let st = state.lock().await;
        assert_eq!(st.board_tasks.get(&review_id).unwrap().lane, "Done");
        assert_ne!(
            st.board_tasks.get(&root_id).unwrap().lane,
            "Done",
            "root should NOT be Done yet — ask is still open"
        );
    }

    // Human answers the ask. This must continue the cascade.
    let ask_task = {
        let st = state.lock().await;
        st.board_tasks.get(&ask_id).cloned().unwrap()
    };
    // Give the ask a linked agent so resolve_ask can route the reply; use
    // the worker as the parent agent (matches the real flow where the
    // asking agent is still registered against loom-1).
    let mut rx = ui_agents.register(worker_id.clone());
    {
        let mut st = state.lock().await;
        let parent = st.board_tasks.get_mut(&root_id).unwrap();
        parent.agent_id = worker_id.clone();
        let _ = ask_task;
    }

    post(
        addr,
        json!({
            "cmd": "resolve_ask",
            "task_id": &ask_id,
            "reply": "option B"
        }),
    )
    .await;
    // Drain any text that resolve_ask delivered to the worker so the
    // channel doesn't fill up.
    let _ = tokio::time::timeout(std::time::Duration::from_millis(500), rx.recv()).await;

    let st = state.lock().await;
    assert_eq!(st.board_tasks.get(&ask_id).unwrap().lane, "Done");
    assert_eq!(
        st.board_tasks.get(&root_id).unwrap().lane,
        "Done",
        "resolve_ask cascade should have promoted root now that every child is Done"
    );
    // The worker was still linked to root (never fired its own loom_done
    // because the done landed on the fixer). Cascade-promoting root to
    // Done should have cleared the worker's stale current_task_id.
    let worker = st.agents.get(&worker_id).unwrap();
    assert!(
        worker.current_task_id.is_empty(),
        "cascade should have unlinked the worker from the promoted root (got {:?})",
        worker.current_task_id
    );
}
