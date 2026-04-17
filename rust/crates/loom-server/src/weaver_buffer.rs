use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::Arc;

use serde_json::{json, Map, Value};
use tokio::sync::Mutex;

use loom_core::state::{MatrixState, ARCHIVED_LANE, WEAVER_MANDATORY_EVENTS};

use crate::app::AppState;
use crate::commands::{dispatch::send_text_to_cell_quiet, CmdContext};

#[derive(Clone, Default)]
pub struct WeaverEventBuffer {
    inner: Arc<Mutex<WeaverEventBufferState>>,
}

#[derive(Default)]
struct WeaverEventBufferState {
    queued: HashMap<String, Vec<Value>>,
    sent: HashMap<String, Vec<Value>>,
    last_push: HashMap<String, f64>,
    pending_flush: HashSet<String>,
}

impl WeaverEventBuffer {
    pub async fn record_event(&self, st: &MatrixState, event: &Value) {
        let group = event
            .get("group")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let kind = event
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if group.is_empty() || kind.is_empty() || !should_buffer_event(st, group, kind) {
            return;
        }
        let mut inner = self.inner.lock().await;
        inner
            .queued
            .entry(group.to_string())
            .or_default()
            .push(event.clone());
    }

    pub async fn export_state(&self, st: &MatrixState) -> (Value, Value) {
        let inner = self.inner.lock().await;
        let mut groups = BTreeSet::new();
        for (group, settings) in &st.group_settings {
            if !settings.weaver_agent_id.trim().is_empty() {
                groups.insert(group.clone());
            }
        }
        groups.extend(inner.queued.keys().cloned());
        groups.extend(inner.sent.keys().cloned());

        let mut stats = Map::new();
        let mut sent = Map::new();
        for group in groups {
            let queued = inner.queued.get(&group).cloned().unwrap_or_default();
            let sent_events = inner.sent.get(&group).cloned().unwrap_or_default();
            let push_interval = st.get_weaver_settings(&group).push_interval.max(0);
            stats.insert(
                group.clone(),
                json!({
                    "buffered_events": queued.len(),
                    "next_push_in": if queued.is_empty() { push_interval } else { 0 },
                    "next_push_at": 0,
                    "queued_events": queued,
                    "manual_flush_requested": false,
                }),
            );
            if !sent_events.is_empty() {
                sent.insert(group, Value::Array(sent_events));
            }
        }
        (Value::Object(stats), Value::Object(sent))
    }

    pub async fn maybe_flush_due(&self, ctx: &CmdContext) {
        let groups = {
            let inner = self.inner.lock().await;
            inner.queued.keys().cloned().collect::<Vec<_>>()
        };
        for group in groups {
            let _ = self.flush_group(ctx, &group).await;
        }
    }

    pub async fn maybe_flush_due_for_app(&self, app: &AppState) {
        let ctx = CmdContext {
            state: app.state.clone(),
            db: app.db.clone(),
            bus: app.bus.clone(),
            pty: app.pty.clone(),
            ui_agents: app.ui_agents.clone(),
            terminal_bridge: app.terminal_bridge.clone(),
            terminals: app.terminals.clone(),
            weaver_buffer: app.weaver_buffer.clone(),
        };
        self.maybe_flush_due(&ctx).await;
    }

    pub async fn flush_group(&self, ctx: &CmdContext, group: &str) -> Result<(), String> {
        let (weaver_id, board_summary) = {
            let st = ctx.state.lock().await;
            let weaver_id = st.get_group_settings(group).weaver_agent_id.clone();
            if weaver_id.trim().is_empty() {
                return Ok(());
            }
            let Some(weaver) = st.agents.get(&weaver_id) else {
                return Ok(());
            };
            if weaver.status != "running" {
                return Ok(());
            }
            if st.get_weaver_settings(group).paused {
                return Ok(());
            }
            if !weaver.activity.trim().is_empty() && weaver.activity != "waiting" {
                return Ok(());
            }
            (weaver_id, board_summary(&st, group))
        };

        let events = {
            let mut inner = self.inner.lock().await;
            if inner.pending_flush.contains(group) {
                return Ok(());
            }
            let Some(events) = inner.queued.remove(group) else {
                return Ok(());
            };
            if events.is_empty() {
                return Ok(());
            }
            inner.pending_flush.insert(group.to_string());
            events
        };

        let digest = format_digest(&events, &board_summary);
        let delivered_at = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
        let send_result = send_text_to_cell_quiet(ctx, &weaver_id, &digest).await;

        let mut inner = self.inner.lock().await;
        inner.pending_flush.remove(group);
        match send_result {
            Ok(()) => {
                inner.last_push.insert(group.to_string(), delivered_at);
                let sent = inner.sent.entry(group.to_string()).or_default();
                for event in events {
                    let mut snapshot = event_snapshot(&event);
                    if let Some(obj) = snapshot.as_object_mut() {
                        obj.insert("delivered_at".into(), json!(delivered_at));
                    }
                    sent.push(snapshot);
                }
                if sent.len() > 200 {
                    let excess = sent.len() - 200;
                    sent.drain(0..excess);
                }
                drop(inner);
                let mut st = ctx.state.lock().await;
                if let Some((seq, ops)) = st.drain_deltas() {
                    ctx.bus
                        .send(loom_core::events::OutMessage::Delta { seq, ops });
                }
                Ok(())
            }
            Err(err) => {
                let queued = inner.queued.entry(group.to_string()).or_default();
                let mut restored = events;
                restored.extend(queued.clone());
                *queued = restored;
                Err(format!("{err:?}"))
            }
        }
    }
}

fn should_buffer_event(st: &MatrixState, group: &str, kind: &str) -> bool {
    if st
        .get_group_settings(group)
        .weaver_agent_id
        .trim()
        .is_empty()
    {
        return false;
    }
    WEAVER_MANDATORY_EVENTS.contains(&kind)
        || st
            .get_weaver_settings(group)
            .enabled_events
            .iter()
            .any(|event| event == kind)
}

fn board_summary(st: &MatrixState, group: &str) -> String {
    let mut counts: HashMap<String, usize> = HashMap::new();
    for task in st.board_tasks.values().filter(|task| task.group == group) {
        *counts.entry(task.lane.clone()).or_insert(0) += 1;
    }
    if counts.is_empty() {
        return "empty".into();
    }
    let mut lanes = counts.into_iter().collect::<Vec<_>>();
    lanes.sort_by(|a, b| {
        lane_order(&a.0)
            .cmp(&lane_order(&b.0))
            .then_with(|| a.0.cmp(&b.0))
    });
    lanes
        .into_iter()
        .map(|(lane, count)| format!("{count} {lane}"))
        .collect::<Vec<_>>()
        .join(" · ")
}

fn lane_order(lane: &str) -> i32 {
    match lane {
        "Backlog" => 0,
        "To Do" => 1,
        "In Progress" => 2,
        "Done" => 3,
        ARCHIVED_LANE => 4,
        _ => 99,
    }
}

fn format_digest(events: &[Value], board_summary: &str) -> String {
    let mut lines = vec![format!(
        "## Loom Digest ({} event{})",
        events.len(),
        if events.len() == 1 { "" } else { "s" }
    )];
    if events.is_empty() {
        lines.push("  No new events since last digest.".into());
    } else {
        for event in events {
            lines.push(format!("  {}", format_event_line(event)));
        }
    }
    lines.push(String::new());
    lines.push(format!("Board: {board_summary}"));
    lines.push("---".into());
    lines.join("\n")
}

fn format_event_line(event: &Value) -> String {
    let kind = event.get("kind").and_then(Value::as_str).unwrap_or("");
    let agent = event
        .get("agent_name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let message = event
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or("")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    match (agent.is_empty(), message.is_empty()) {
        (false, false) => format!("{kind}: {agent} — {message}"),
        (true, false) => format!("{kind}: {message}"),
        (false, true) => format!("{kind}: {agent}"),
        (true, true) => kind.to_string(),
    }
}

fn event_snapshot(event: &Value) -> Value {
    json!({
        "id": event.get("id").and_then(Value::as_i64).unwrap_or(0),
        "timestamp": event.get("timestamp").and_then(Value::as_f64).unwrap_or(0.0),
        "kind": event.get("kind").and_then(Value::as_str).unwrap_or(""),
        "cell_id": event.get("cell_id").and_then(Value::as_str).unwrap_or(""),
        "agent_name": event.get("agent_name").and_then(Value::as_str).unwrap_or(""),
        "group": event.get("group").and_then(Value::as_str).unwrap_or(""),
        "message": event.get("message").and_then(Value::as_str).unwrap_or(""),
        "task_id": event.get("task_id").and_then(Value::as_str).unwrap_or(""),
    })
}
