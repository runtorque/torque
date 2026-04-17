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
    /// Wall-clock time when the currently-queued batch's first event arrived.
    /// Cleared after a successful flush. Used together with `last_push` to
    /// respect the operator-configured push/max intervals.
    buffer_started_at: HashMap<String, f64>,
    pending_flush: HashSet<String>,
}

impl WeaverEventBuffer {
    pub async fn record_event(&self, st: &mut MatrixState, event: &Value) {
        let group = event
            .get("group")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let kind = event
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        if group.is_empty() || kind.is_empty() || !should_buffer_event(st, &group, &kind) {
            return;
        }
        let now = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
        let push_interval = st.get_weaver_settings(&group).push_interval.max(0) as f64;
        let (buffered, first_queued_at, queued_snapshot) = {
            let mut inner = self.inner.lock().await;
            let (buffered, snapshot) = {
                let entry = inner.queued.entry(group.clone()).or_default();
                entry.push(event.clone());
                let snapshot = entry.iter().map(event_snapshot).collect::<Vec<_>>();
                (entry.len(), snapshot)
            };
            if buffered == 1 {
                inner.buffer_started_at.insert(group.clone(), now);
            }
            let first_queued_at = inner
                .buffer_started_at
                .get(&group)
                .copied()
                .unwrap_or(now);
            (buffered, first_queued_at, snapshot)
        };
        // Live update so weaver panels see the queue fill up without a
        // resync. Frontends render next_push_at + queued_events under
        // `state.weaver_buffer_stats[group]`.
        st.emit(loom_core::delta::DeltaOp::WeaverBufferStats {
            group,
            buffered_events: buffered,
            next_push_in: (first_queued_at + push_interval - now).max(0.0),
            next_push_at: first_queued_at + push_interval,
            queued_events: queued_snapshot,
            manual_flush_requested: false,
        });
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

        let now = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
        let mut stats = Map::new();
        let mut sent = Map::new();
        for group in groups {
            let queued = inner.queued.get(&group).cloned().unwrap_or_default();
            let sent_events = inner.sent.get(&group).cloned().unwrap_or_default();
            let ws = st.get_weaver_settings(&group);
            let push_interval = ws.push_interval.max(0) as f64;
            let first_queued_at = inner.buffer_started_at.get(&group).copied();
            let (next_push_in, next_push_at) = if queued.is_empty() {
                (push_interval, 0.0)
            } else if let Some(first) = first_queued_at {
                let deadline = first + push_interval;
                ((deadline - now).max(0.0), deadline)
            } else {
                (0.0, 0.0)
            };
            let queued_snapshot = queued.iter().map(event_snapshot).collect::<Vec<_>>();
            stats.insert(
                group.clone(),
                json!({
                    "buffered_events": queued.len(),
                    "next_push_in": next_push_in,
                    "next_push_at": next_push_at,
                    "queued_events": queued_snapshot,
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
        self.flush_group_inner(ctx, group, false).await
    }

    /// Force an immediate flush, bypassing the interval gate. Used by operator
    /// "flush now" actions.
    pub async fn force_flush_group(
        &self,
        ctx: &CmdContext,
        group: &str,
    ) -> Result<(), String> {
        self.flush_group_inner(ctx, group, true).await
    }

    async fn flush_group_inner(
        &self,
        ctx: &CmdContext,
        group: &str,
        force: bool,
    ) -> Result<(), String> {
        let (weaver_id, board_summary, push_interval, max_interval) = {
            let st = ctx.state.lock().await;
            let weaver_id = st.get_group_settings(group).weaver_agent_id.clone();
            if weaver_id.trim().is_empty() {
                return Ok(());
            }
            let Some(weaver) = st.agents.get(&weaver_id) else {
                tracing::debug!(group, "flush skipped: weaver cell missing");
                return Ok(());
            };
            // The weaver must be alive — but "idle" counts. Claude Code flips
            // the cell to `"idle"` when it hits its Stop hook (i.e. it has
            // just finished a turn and is waiting for new input). Digests
            // should flow at exactly that moment; requiring `"running"` meant
            // events piled up forever after the first turn.
            let status = weaver.status.as_str();
            if !matches!(status, "running" | "idle") {
                tracing::debug!(group, status, "flush skipped: weaver not running/idle");
                return Ok(());
            }
            let ws = st.get_weaver_settings(group);
            if ws.paused {
                tracing::debug!(group, "flush skipped: weaver paused");
                return Ok(());
            }
            if !weaver.activity.trim().is_empty() && weaver.activity != "waiting" {
                tracing::debug!(
                    group,
                    activity = %weaver.activity,
                    "flush skipped: weaver busy"
                );
                return Ok(());
            }
            (
                weaver_id,
                board_summary(&st, group),
                ws.push_interval.max(0) as f64,
                ws.max_interval.max(0) as f64,
            )
        };

        let now = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
        let events = {
            let mut inner = self.inner.lock().await;
            if inner.pending_flush.contains(group) {
                return Ok(());
            }
            if !force {
                let first_queued = match inner.buffer_started_at.get(group) {
                    Some(t) => *t,
                    None => return Ok(()),
                };
                let last_push = inner.last_push.get(group).copied().unwrap_or(0.0);
                if !interval_gate_fires(
                    now,
                    first_queued,
                    last_push,
                    push_interval,
                    max_interval,
                ) {
                    return Ok(());
                }
            }
            let Some(events) = inner.queued.remove(group) else {
                return Ok(());
            };
            if events.is_empty() {
                inner.buffer_started_at.remove(group);
                return Ok(());
            }
            inner.pending_flush.insert(group.to_string());
            events
        };

        let event_count = events.len();
        let digest = format_digest(&events, &board_summary);
        let delivered_at = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
        let send_result = send_text_to_cell_quiet(ctx, &weaver_id, &digest).await;

        let mut inner = self.inner.lock().await;
        inner.pending_flush.remove(group);
        match send_result {
            Ok(()) => {
                tracing::info!(
                    group,
                    weaver_id = %weaver_id,
                    count = event_count,
                    "weaver digest delivered"
                );
                inner.last_push.insert(group.to_string(), delivered_at);
                // Only clear `buffer_started_at` when no new events arrived
                // during the send. If `record_event` ran while we were
                // awaiting `send_text_to_cell_quiet`, it will have re-queued
                // events and reset `buffer_started_at` — we must NOT erase
                // that, or the next flush will return early on the None
                // guard above and the new events will pile up forever.
                let queue_empty = inner
                    .queued
                    .get(group)
                    .map(|v| v.is_empty())
                    .unwrap_or(true);
                if queue_empty {
                    inner.buffer_started_at.remove(group);
                }
                let sent_snapshot = {
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
                    sent.clone()
                };
                let remaining = inner.queued.get(group).map(|v| v.len()).unwrap_or(0);
                let queued_snapshot = inner
                    .queued
                    .get(group)
                    .map(|v| v.iter().map(event_snapshot).collect::<Vec<_>>())
                    .unwrap_or_default();
                let next_first = inner.buffer_started_at.get(group).copied();
                drop(inner);
                let mut st = ctx.state.lock().await;
                // Broadcast the updated sent-events buffer so live WS
                // clients' weaver panels show the new history without
                // needing a resync.
                st.emit(loom_core::delta::DeltaOp::WeaverSentEvents {
                    group: group.to_string(),
                    events: sent_snapshot,
                });
                // And the buffer stats — reflecting whatever is still queued
                // (could be non-empty if events arrived during the send).
                let (next_in, next_at) = if remaining == 0 {
                    (push_interval, delivered_at + push_interval)
                } else if let Some(first) = next_first {
                    let deadline = first + push_interval;
                    ((deadline - delivered_at).max(0.0), deadline)
                } else {
                    (0.0, 0.0)
                };
                st.emit(loom_core::delta::DeltaOp::WeaverBufferStats {
                    group: group.to_string(),
                    buffered_events: remaining,
                    next_push_in: next_in,
                    next_push_at: next_at,
                    queued_events: queued_snapshot,
                    manual_flush_requested: false,
                });
                if let Some((seq, ops)) = st.drain_deltas() {
                    ctx.bus
                        .send(loom_core::events::OutMessage::Delta { seq, ops });
                }
                drop(st);
                // If events arrived during the send and the weaver is still
                // idle, a fresh flush will be attempted at the next 5s
                // scheduler tick (scheduler.rs::spawn_weaver_flusher). We
                // already emitted the updated WeaverBufferStats above so
                // the UI reflects the still-queued tail immediately.
                let _ = remaining; // suppress unused warning when tracing is off
                Ok(())
            }
            Err(err) => {
                tracing::warn!(
                    group,
                    weaver_id = %weaver_id,
                    count = event_count,
                    error = ?err,
                    "weaver digest delivery failed — events restored to queue"
                );
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

/// Pure interval-gate predicate — `true` means "enough time has passed, flush
/// now". Mirrors the Python weaver's semantics:
///   * without a prior push, the push deadline is `first_queued + push_interval`
///   * with one,               it's `last_push + push_interval`
///   * the hard ceiling is always `first_queued + max_interval`
///   * effective deadline is `min(push_deadline, max_deadline)`
fn interval_gate_fires(
    now: f64,
    first_queued: f64,
    last_push: f64,
    push_interval: f64,
    max_interval: f64,
) -> bool {
    let push_deadline = if last_push > 0.0 {
        last_push + push_interval
    } else {
        first_queued + push_interval
    };
    let max_deadline = first_queued + max_interval;
    let deadline = push_deadline.min(max_deadline);
    now >= deadline
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

#[cfg(test)]
mod tests {
    use super::interval_gate_fires;

    #[test]
    fn gate_blocks_fresh_batch_before_push_interval() {
        // First event arrived at t=100, no prior push. push=60s, max=300s.
        // At t=110 (10s in) we should be blocked.
        assert!(!interval_gate_fires(110.0, 100.0, 0.0, 60.0, 300.0));
    }

    #[test]
    fn gate_fires_at_push_interval_on_fresh_batch() {
        // First event at t=100, no prior push. push=60s.
        // At t=160 exactly → should fire.
        assert!(interval_gate_fires(160.0, 100.0, 0.0, 60.0, 300.0));
    }

    #[test]
    fn gate_respects_last_push_when_present() {
        // last_push at t=200. push=60s. fresh event queued later at t=250.
        // Even though 50s elapsed since queuing, last_push+60 = 260 hasn't
        // elapsed yet — should be blocked at t=255.
        assert!(!interval_gate_fires(255.0, 250.0, 200.0, 60.0, 300.0));
        // At t=260 the push deadline is reached → fire.
        assert!(interval_gate_fires(260.0, 250.0, 200.0, 60.0, 300.0));
    }

    #[test]
    fn gate_max_interval_caps_push_deadline() {
        // Operator set push_interval absurdly high (10000s) but max=30s.
        // Event queued at 100 → max deadline = 130. Should fire at 130.
        assert!(!interval_gate_fires(120.0, 100.0, 0.0, 10_000.0, 30.0));
        assert!(interval_gate_fires(130.0, 100.0, 0.0, 10_000.0, 30.0));
    }

    #[test]
    fn gate_zero_intervals_always_fire() {
        // push=0, max=0 → fire immediately on any `now >= first_queued`.
        // Matches the existing test-fixture convention in weaver.rs.
        assert!(interval_gate_fires(100.0, 100.0, 0.0, 0.0, 0.0));
        assert!(interval_gate_fires(100.1, 100.0, 0.0, 0.0, 0.0));
    }

    #[test]
    fn gate_exact_boundary_fires() {
        // `now >= deadline` — at the exact second, we should flush, not block.
        assert!(interval_gate_fires(160.0, 100.0, 0.0, 60.0, 300.0));
    }
}
