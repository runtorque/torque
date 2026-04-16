//! Weaver command + MCP integration tests.

use std::net::SocketAddr;
use std::sync::Arc;

use axum::Router;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::{AgentCell, BoardTask, MatrixState, WeaverSettings};
use loom_server::app::{UiAgentInput, UiAgentRegistry};
use loom_server::commands;
use loom_server::events as evt;
use loom_server::mcp;
use loom_server::uploads;
use loom_server::ws;

async fn spawn() -> (SocketAddr, Arc<Mutex<MatrixState>>, LoomDb, UiAgentRegistry) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();
    let ui_agents = UiAgentRegistry::default();
    let app_state = loom_server::app::AppState {
        db: db.clone(),
        state: state.clone(),
        bus,
        pty: None,
        ui_agents: ui_agents.clone(),
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
        terminals: Default::default(),
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
    (addr, state, db, ui_agents)
}

async fn post(addr: SocketAddr, body: Value) -> Value {
    let client = reqwest::Client::new();
    let value = client
        .post(format!("http://{}/api/cmd", addr))
        .json(&body)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    if value.get("ok").and_then(Value::as_bool) == Some(true) {
        if let Some(data) = value.get("data").and_then(Value::as_object) {
            let mut flattened = data.clone();
            flattened.insert("ok".into(), Value::Bool(true));
            return Value::Object(flattened);
        }
    }
    value
}

async fn mcp_call(addr: SocketAddr, method: &str, params: Value, cell_id: Option<&str>) -> Value {
    let client = reqwest::Client::new();
    let mut req = client.post(format!("http://{}/mcp", addr)).json(&json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }));
    if let Some(cell_id) = cell_id {
        req = req.header("X-Loom-Cell-Id", cell_id);
    }
    req.send().await.unwrap().json::<Value>().await.unwrap()
}

async fn seed_weaver_group(state: &Arc<Mutex<MatrixState>>, db: &LoomDb) -> (String, String) {
    let now = "2026-04-15T12:00:00Z";
    let (weaver_id, worker_id, weaver, worker, tasks, settings) = {
        let mut st = state.lock().await;
        st.add_group("Eng").unwrap();

        let mut weaver = AgentCell::new("weaver-1", "Weaver", "Eng");
        weaver.slug = "eng:weaver".into();
        weaver.status = "running".into();
        st.add_agent(weaver.clone()).unwrap();

        let mut worker = AgentCell::new("worker-1", "Worker", "Eng");
        worker.slug = "eng:worker".into();
        worker.status = "running".into();
        worker.git_root = "/repo".into();
        worker.worktree_repo_root = "/repo".into();
        worker.worktree_branch = "loom/feature".into();
        worker.current_task_id = "eng-1".into();
        worker.created_by_weaver_id = weaver.id.clone();
        st.add_agent(worker.clone()).unwrap();

        let mut group_settings = st.get_group_settings("Eng");
        group_settings.weaver_agent_id = weaver.id.clone();
        st.group_settings
            .insert("Eng".into(), group_settings.clone());

        let mut weaver_settings = WeaverSettings::default();
        weaver_settings.group = "Eng".into();
        st.weaver_settings
            .insert("Eng".into(), weaver_settings.clone());

        let mut boundary = BoardTask::new_minimal("eng-1", "Implement feature");
        boundary.group = "Eng".into();
        boundary.lane = "In Progress".into();
        boundary.agent_id = worker.id.clone();
        boundary.created_at = now.into();
        boundary.updated_at = now.into();
        boundary.lane_entered_at = now.into();
        boundary.status = "working".into();
        boundary
            .worktree_boundary
            .insert("repo_root".into(), json!("/repo"));
        boundary
            .worktree_boundary
            .insert("branch".into(), json!("loom/feature"));
        boundary
            .worktree_boundary
            .insert("status".into(), json!("open"));
        boundary
            .worktree_boundary
            .insert("recorded_at".into(), json!(now));
        boundary.messages.push(json!({
            "timestamp": now,
            "action": "progress",
            "message": "working",
        }));
        st.upsert_task(boundary.clone()).unwrap();

        let mut followup = BoardTask::new_minimal("eng-1a1", "Resume after review");
        followup.group = "Eng".into();
        followup.lane = "To Do".into();
        followup.created_at = now.into();
        followup.updated_at = now.into();
        followup.lane_entered_at = now.into();
        followup.resume_after_boundary_task_id = "eng-1".into();
        st.upsert_task(followup.clone()).unwrap();

        let mut ask = BoardTask::new_minimal("eng-2", "Need approval");
        ask.group = "Eng".into();
        ask.lane = "To Do".into();
        ask.created_at = now.into();
        ask.updated_at = now.into();
        ask.lane_entered_at = now.into();
        ask.labels.push("loom:human".into());
        st.upsert_task(ask.clone()).unwrap();

        st.auto_dispatch_queues.insert(
            "Eng".into(),
            vec![loom_core::state::AutoDispatchQueueEntry {
                task_id: "eng-1a1".into(),
                agent_group: "Eng".into(),
                max_concurrent: 1,
                target_agent_id: String::new(),
                weaver_owner_id: weaver.id.clone(),
                enqueued_at: now.into(),
            }],
        );

        st.drain_deltas();
        (
            weaver.id.clone(),
            worker.id.clone(),
            weaver,
            worker,
            vec![boundary, followup, ask],
            weaver_settings,
        )
    };

    db.save_group("Eng", "eng", 0).await.unwrap();
    db.save_group_members("Eng", &[weaver_id.clone(), worker_id.clone()])
        .await
        .unwrap();
    db.save_group_settings("Eng", &{
        let st = state.lock().await;
        st.get_group_settings("Eng")
    })
    .await
    .unwrap();
    db.save_weaver_settings("Eng", &settings).await.unwrap();
    db.save_agent(&weaver).await.unwrap();
    db.save_agent(&worker).await.unwrap();
    for task in tasks {
        db.save_board_task(&task).await.unwrap();
    }

    (weaver_id, worker_id)
}

#[tokio::test]
async fn weaver_journal_roundtrip_and_session_map_counts() {
    let (addr, state, db, _ui_agents) = spawn().await;
    let (_weaver_id, _worker_id) = seed_weaver_group(&state, &db).await;

    let append = post(
        addr,
        json!({
            "cmd": "weaver_journal_append",
            "group": "Eng",
            "entry_type": "decision",
            "entry": "Proceed with the feature branch",
        }),
    )
    .await;
    assert_eq!(append["ok"], true, "append: {append:?}");

    let read = post(
        addr,
        json!({
            "cmd": "weaver_journal_read",
            "group": "Eng",
            "tail": 10,
        }),
    )
    .await;
    assert_eq!(read["type"], "journal");
    assert_eq!(read["entries"].as_array().unwrap().len(), 1);
    assert_eq!(read["entries"][0]["type"], "decision");

    let session_map = post(
        addr,
        json!({"cmd": "weaver_session_map_read", "group": "Eng"}),
    )
    .await;
    assert_eq!(session_map["type"], "weaver_session_map");
    let map = &session_map["session_map"];
    assert_eq!(map["overview"]["tasks_total"], 3);
    assert_eq!(map["overview"]["pending_ask_count"], 1);
    assert_eq!(map["overview"]["active_stream_count"], 1);
    assert_eq!(map["overview"]["queued_follow_up_count"], 1);
    assert_eq!(map["overview"]["journal_entry_count"], 1);
    assert_eq!(map["streams"]["count"], 1);
    assert_eq!(map["streams"]["items"][0]["branch"], "loom/feature");
    assert_eq!(
        map["journal"]["items"][0]["entry"],
        "Proceed with the feature branch"
    );
}

#[tokio::test]
async fn mcp_tools_list_only_exposes_weaver_tools_to_designated_weaver() {
    let (addr, state, db, _ui_agents) = spawn().await;
    let (weaver_id, worker_id) = seed_weaver_group(&state, &db).await;

    let no_header = mcp_call(addr, "tools/list", json!({}), None).await;
    let no_header_names = no_header["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(no_header_names.contains(&"loom_progress"));
    assert!(!no_header_names.contains(&"weaver_board_summary"));

    let worker = mcp_call(addr, "tools/list", json!({}), Some(&worker_id)).await;
    let worker_names = worker["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(!worker_names.contains(&"weaver_board_summary"));
    assert!(!worker_names.contains(&"weaver_session_map"));

    let weaver = mcp_call(addr, "tools/list", json!({}), Some(&weaver_id)).await;
    let weaver_names = weaver["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(weaver_names.contains(&"weaver_board_summary"));
    assert!(weaver_names.contains(&"weaver_session_map"));
    assert!(weaver_names.contains(&"weaver_note"));
}

#[tokio::test]
async fn mcp_weaver_board_summary_and_stream_show_work() {
    let (addr, state, db, _ui_agents) = spawn().await;
    let (weaver_id, worker_id) = seed_weaver_group(&state, &db).await;

    let denied = mcp_call(
        addr,
        "tools/call",
        json!({"name": "weaver_board_summary", "arguments": {}}),
        Some(&worker_id),
    )
    .await;
    assert!(denied["error"]["message"]
        .as_str()
        .unwrap()
        .contains("designated Weaver"));

    let summary = mcp_call(
        addr,
        "tools/call",
        json!({"name": "weaver_board_summary", "arguments": {}}),
        Some(&weaver_id),
    )
    .await;
    assert!(summary["error"].is_null(), "summary error: {summary:?}");
    let loom = &summary["result"]["loom"];
    assert_eq!(loom["group"], "Eng");
    assert_eq!(loom["streams"]["count"], 1);
    assert_eq!(loom["asks"]["count"], 1);

    let stream = mcp_call(
        addr,
        "tools/call",
        json!({"name": "weaver_stream_show", "arguments": {"task": "eng-1"}}),
        Some(&weaver_id),
    )
    .await;
    assert!(stream["error"].is_null(), "stream error: {stream:?}");
    let stream_payload = &stream["result"]["loom"];
    assert_eq!(stream_payload["branch"], "loom/feature");
    let product_ids = stream_payload["product_task_ids"].as_array().unwrap();
    assert!(product_ids.iter().any(|value| value == "eng-1"));
}

#[tokio::test]
async fn weaver_reply_delivers_text_and_notifications_persist() {
    let (addr, state, db, ui_agents) = spawn().await;
    let (weaver_id, _worker_id) = seed_weaver_group(&state, &db).await;
    let mut rx = ui_agents.register(weaver_id.clone());

    {
        let mut st = state.lock().await;
        st.weaver_settings.insert(
            "Eng".into(),
            WeaverSettings {
                group: "Eng".into(),
                pending_question: "Ship it?".into(),
                paused: true,
                ..WeaverSettings::default()
            },
        );
    }
    db.save_weaver_settings("Eng", &{
        let st = state.lock().await;
        st.get_weaver_settings("Eng")
    })
    .await
    .unwrap();

    let reply = post(
        addr,
        json!({
            "cmd": "weaver_reply",
            "group": "Eng",
            "answer": "Ship it",
        }),
    )
    .await;
    assert_eq!(reply["ok"], true, "reply: {reply:?}");
    assert_eq!(reply["delivered"], true);
    let sent = tokio::time::timeout(std::time::Duration::from_secs(2), rx.recv())
        .await
        .expect("reply delivery")
        .expect("channel open");
    assert_eq!(
        sent,
        UiAgentInput::Text("\n## Human Reply\nShip it\n---\n".into())
    );
    {
        let st = state.lock().await;
        let settings = st.get_weaver_settings("Eng");
        assert_eq!(settings.pending_question, "");
        assert!(!settings.paused);
    }

    let notifications = post(
        addr,
        json!({
            "cmd": "weaver_notifications",
            "group": "Eng",
            "preset": "noisy",
        }),
    )
    .await;
    assert_eq!(
        notifications["ok"], true,
        "notifications: {notifications:?}"
    );
    assert_eq!(notifications["settings"]["digest_verbosity"], "detailed");
    assert_eq!(notifications["settings"]["push_interval"], 30);
    assert!(notifications["settings"]["enabled_events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "agent_progress"));

    let reloaded = db.load_all().await.unwrap();
    let reloaded_settings = reloaded.get_weaver_settings("Eng");
    assert_eq!(reloaded_settings.digest_verbosity, "detailed");
    assert_eq!(reloaded_settings.push_interval, 30);
    assert!(reloaded_settings
        .enabled_events
        .iter()
        .any(|value| value == "agent_progress"));
}

#[tokio::test]
async fn weaver_events_use_stable_panel_event_ids() {
    let (addr, state, db, _ui_agents) = spawn().await;
    let (_weaver_id, _worker_id) = seed_weaver_group(&state, &db).await;

    let id1 = db
        .append_panel_event(
            "agent_started",
            "weaver-1",
            "Weaver",
            "Eng",
            "weaver booted",
            "",
            200.0,
        )
        .await
        .unwrap();
    let id2 = db
        .append_panel_event(
            "ask_created",
            "weaver-1",
            "Weaver",
            "Eng",
            "Need approval",
            "eng-2",
            100.0,
        )
        .await
        .unwrap();

    let first = post(
        addr,
        json!({
            "cmd": "weaver_events",
            "group": "Eng",
            "since_id": 0,
            "limit": 10,
        }),
    )
    .await;
    let events = first["events"].as_array().unwrap();
    assert_eq!(events.len(), 2, "first events: {first:?}");
    assert_eq!(events[0]["id"].as_i64(), Some(id1));
    assert_eq!(events[0]["kind"], "agent_started");
    assert_eq!(events[1]["id"].as_i64(), Some(id2));
    assert_eq!(events[1]["kind"], "ask_created");
    assert_eq!(first["cursor"].as_i64(), Some(id2));

    let filtered = post(
        addr,
        json!({
            "cmd": "weaver_events",
            "group": "Eng",
            "since_id": 0,
            "limit": 10,
            "types": ["ask_created"],
        }),
    )
    .await;
    let filtered_events = filtered["events"].as_array().unwrap();
    assert_eq!(filtered_events.len(), 1, "filtered: {filtered:?}");
    assert_eq!(filtered_events[0]["id"].as_i64(), Some(id2));
    assert_eq!(filtered_events[0]["task_id"], "eng-2");

    let id3 = db
        .append_panel_event(
            "task_dispatched",
            "worker-1",
            "Worker",
            "Eng",
            "picked up work",
            "eng-1",
            50.0,
        )
        .await
        .unwrap();
    let second = post(
        addr,
        json!({
            "cmd": "weaver_events",
            "group": "Eng",
            "since_id": id2,
            "limit": 10,
        }),
    )
    .await;
    let second_events = second["events"].as_array().unwrap();
    assert_eq!(second_events.len(), 1, "second events: {second:?}");
    assert_eq!(second_events[0]["id"].as_i64(), Some(id3));
    assert_eq!(second_events[0]["kind"], "task_dispatched");
}

#[tokio::test]
async fn weaver_reply_keeps_pending_question_when_not_deliverable() {
    let (addr, state, db, _ui_agents) = spawn().await;
    let (weaver_id, _worker_id) = seed_weaver_group(&state, &db).await;

    {
        let mut st = state.lock().await;
        if let Some(weaver) = st.agents.get_mut(&weaver_id) {
            weaver.status = "stopped".into();
            weaver.session_id = None;
        }
        st.weaver_settings.insert(
            "Eng".into(),
            WeaverSettings {
                group: "Eng".into(),
                pending_question: "Ship it?".into(),
                paused: true,
                ..WeaverSettings::default()
            },
        );
    }
    db.save_weaver_settings("Eng", &{
        let st = state.lock().await;
        st.get_weaver_settings("Eng")
    })
    .await
    .unwrap();

    let reply = post(
        addr,
        json!({
            "cmd": "weaver_reply",
            "group": "Eng",
            "answer": "Ship it",
        }),
    )
    .await;
    assert_eq!(reply["error"], "Weaver is not running", "reply: {reply:?}");

    {
        let st = state.lock().await;
        let settings = st.get_weaver_settings("Eng");
        assert_eq!(settings.pending_question, "Ship it?");
        assert!(settings.paused);
        let journal = st.weaver_journal.get("Eng").cloned().unwrap_or_default();
        assert!(
            !journal
                .iter()
                .any(|entry| entry["entry"] == "Human replied: Ship it"),
            "journal should not append on failed delivery: {journal:?}"
        );
    }
}
