//! Deterministic task-health heuristics. Port of `loom/task_health.py`.

use std::collections::{HashMap, HashSet};

use chrono::{DateTime, TimeZone, Utc};
use loom_core::state::{board_task_counts_as_done, AgentCell, BoardTask, ARCHIVED_LANE};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

pub const HEALTH_HEALTHY: &str = "healthy";
pub const HEALTH_BLOCKED: &str = "blocked";
pub const HEALTH_STALE_IN_PROGRESS: &str = "stale-in-progress";
pub const HEALTH_IDLE_RISK: &str = "idle-risk";
pub const HEALTH_STALLED: &str = "stalled";
pub const HEALTH_THRASHING: &str = "thrashing";

pub const IDLE_RISK_AFTER_SECS: i64 = 10 * 60;
pub const STALLED_AFTER_SECS: i64 = 20 * 60;
const THRASH_WINDOW_SECS: f64 = 30.0 * 60.0;
const THRASH_MIN_MESSAGES: usize = 6;
const THRASH_MIN_TRANSITIONS: usize = 3;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskHealthSnapshot {
    pub state: String,
    #[serde(default)]
    pub details: Map<String, Value>,
}

pub fn health_severity(state: &str) -> i32 {
    match state {
        HEALTH_BLOCKED => 5,
        HEALTH_STALE_IN_PROGRESS => 4,
        HEALTH_STALLED => 3,
        HEALTH_THRASHING => 2,
        HEALTH_IDLE_RISK => 1,
        _ => 0,
    }
}

pub fn now_iso(now_ts: Option<f64>) -> String {
    match now_ts.and_then(timestamp_to_datetime) {
        Some(dt) => dt.to_rfc3339(),
        None => Utc::now().to_rfc3339(),
    }
}

pub fn compute_task_health(
    tasks_by_id: &HashMap<String, BoardTask>,
    agents_by_id: &HashMap<String, AgentCell>,
    now_ts: Option<f64>,
) -> HashMap<String, TaskHealthSnapshot> {
    let now_ts = now_ts.unwrap_or_else(|| Utc::now().timestamp() as f64);

    let active_tasks = tasks_by_id
        .iter()
        .filter_map(|(id, task)| {
            if task.lane == ARCHIVED_LANE {
                None
            } else {
                Some((id.clone(), task))
            }
        })
        .collect::<HashMap<_, _>>();

    let mut children_by_id: HashMap<String, Vec<&BoardTask>> = HashMap::new();
    for task in active_tasks.values() {
        if !task.parent_task_id.is_empty() && active_tasks.contains_key(&task.parent_task_id) {
            children_by_id
                .entry(task.parent_task_id.clone())
                .or_default()
                .push(*task);
        }
    }

    let locals_by_id = active_tasks
        .iter()
        .map(|(tid, task)| {
            (
                tid.clone(),
                compute_local_health(task, &active_tasks, agents_by_id, now_ts),
            )
        })
        .collect::<HashMap<_, _>>();

    fn compute_effective<'a>(
        task: &'a BoardTask,
        active_tasks: &HashMap<String, &'a BoardTask>,
        children_by_id: &HashMap<String, Vec<&'a BoardTask>>,
        locals_by_id: &HashMap<String, TaskHealthSnapshot>,
        out: &mut HashMap<String, TaskHealthSnapshot>,
    ) -> TaskHealthSnapshot {
        if let Some(existing) = out.get(&task.id) {
            return existing.clone();
        }
        let child_snapshots = children_by_id
            .get(&task.id)
            .into_iter()
            .flat_map(|children| children.iter())
            .filter(|child| child.lane != "Done" && child.lane != ARCHIVED_LANE)
            .map(|child| compute_effective(child, active_tasks, children_by_id, locals_by_id, out))
            .collect::<Vec<_>>();
        let local = locals_by_id
            .get(&task.id)
            .cloned()
            .unwrap_or_else(|| healthy_snapshot(&task.id, None));
        let snapshot = roll_up_health(task, local, child_snapshots, active_tasks);
        out.insert(task.id.clone(), snapshot.clone());
        snapshot
    }

    let mut effective = HashMap::new();
    for task in active_tasks.values() {
        compute_effective(
            task,
            &active_tasks,
            &children_by_id,
            &locals_by_id,
            &mut effective,
        );
    }
    effective
}

fn roll_up_health(
    _task: &BoardTask,
    local: TaskHealthSnapshot,
    child_snapshots: Vec<TaskHealthSnapshot>,
    tasks_by_id: &HashMap<String, &BoardTask>,
) -> TaskHealthSnapshot {
    if child_snapshots.is_empty() {
        return local;
    }

    let mut worst = local.clone();
    for snapshot in child_snapshots {
        if health_severity(&snapshot.state) > health_severity(&worst.state) {
            worst = snapshot;
        }
    }
    if worst.state == local.state && worst.details == local.details {
        return local;
    }

    let source_task_id = worst
        .details
        .get("source_task_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let source_title = tasks_by_id
        .get(&source_task_id)
        .map(|task| task.task.clone())
        .unwrap_or_default();

    let mut details = worst.details.clone();
    if !details.contains_key("reasons") {
        details.insert("reasons".into(), Value::Array(Vec::new()));
    }
    details.insert("aggregate".into(), Value::Bool(true));
    details.insert("source_task_id".into(), Value::String(source_task_id));
    details.insert("source_task_title".into(), Value::String(source_title));
    details.insert("local_state".into(), Value::String(local.state.clone()));
    details.insert(
        "local_reasons".into(),
        local
            .details
            .get("reasons")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new())),
    );
    TaskHealthSnapshot {
        state: worst.state,
        details,
    }
}

fn compute_local_health(
    task: &BoardTask,
    tasks_by_id: &HashMap<String, &BoardTask>,
    agents_by_id: &HashMap<String, AgentCell>,
    now_ts: f64,
) -> TaskHealthSnapshot {
    let agent = agents_by_id.get(&task.agent_id);
    if board_task_counts_as_done(Some(task)) || task.lane == ARCHIVED_LANE {
        return healthy_snapshot(&task.id, task_last_activity_ts(task, agent));
    }

    let labels = task
        .labels
        .iter()
        .map(|label| label.as_str())
        .collect::<HashSet<_>>();
    let mut reasons = Vec::new();
    if labels.contains("loom:human") || labels.contains("human") {
        reasons.push("awaiting_human");
    }
    if labels.contains("loom:blocked") || labels.contains("blocked") {
        reasons.push("explicit_blocked");
    }
    if has_unmet_dependencies(task, tasks_by_id) {
        reasons.push("dependency_blocked");
    }
    if agent.map(|cell| cell.activity.as_str()) == Some("waiting") {
        reasons.push("agent_waiting");
    }

    let last_activity_ts = task_last_activity_ts(task, agent);
    let mut details = Map::new();
    details.insert("aggregate".into(), Value::Bool(false));
    details.insert("source_task_id".into(), Value::String(task.id.clone()));
    details.insert(
        "last_activity_at".into(),
        Value::String(iso_or_empty(last_activity_ts)),
    );
    details.insert(
        "reasons".into(),
        Value::Array(
            reasons
                .iter()
                .map(|reason| Value::String((*reason).to_string()))
                .collect(),
        ),
    );
    if let Some(cell) = agent {
        if cell.last_event_at > 0.0 {
            details.insert(
                "agent_last_event_at".into(),
                Value::String(iso_or_empty(Some(cell.last_event_at))),
            );
        }
    }
    if !reasons.is_empty() {
        return TaskHealthSnapshot {
            state: HEALTH_BLOCKED.to_string(),
            details,
        };
    }

    let stale_reasons = stale_in_progress_reasons(task, tasks_by_id, agents_by_id);
    if !stale_reasons.is_empty() {
        details.insert(
            "reasons".into(),
            Value::Array(
                stale_reasons
                    .into_iter()
                    .map(|reason| Value::String(reason))
                    .collect(),
            ),
        );
        details.insert("agent_idle".into(), Value::Bool(true));
        return TaskHealthSnapshot {
            state: HEALTH_STALE_IN_PROGRESS.to_string(),
            details,
        };
    }

    if is_thrashing(task, now_ts) {
        details.insert("reasons".into(), json!(["message_churn"]));
        return TaskHealthSnapshot {
            state: HEALTH_THRASHING.to_string(),
            details,
        };
    }

    if !is_monitored_task(task) {
        details.insert("reasons".into(), json!(["not_active"]));
        return TaskHealthSnapshot {
            state: HEALTH_HEALTHY.to_string(),
            details,
        };
    }

    let Some(last_activity_ts) = last_activity_ts else {
        details.insert("reasons".into(), json!(["no_recent_signal"]));
        return TaskHealthSnapshot {
            state: HEALTH_STALLED.to_string(),
            details,
        };
    };

    let silence_secs = (now_ts - last_activity_ts).max(0.0) as i64;
    details.insert("silence_secs".into(), Value::Number(silence_secs.into()));
    if silence_secs >= STALLED_AFTER_SECS {
        details.insert("reasons".into(), json!(["no_progress_timeout"]));
        return TaskHealthSnapshot {
            state: HEALTH_STALLED.to_string(),
            details,
        };
    }
    if silence_secs >= IDLE_RISK_AFTER_SECS {
        details.insert("reasons".into(), json!(["progress_silence_warning"]));
        return TaskHealthSnapshot {
            state: HEALTH_IDLE_RISK.to_string(),
            details,
        };
    }

    details.insert("reasons".into(), json!(["recent_activity"]));
    TaskHealthSnapshot {
        state: HEALTH_HEALTHY.to_string(),
        details,
    }
}

fn healthy_snapshot(task_id: &str, last_activity_ts: Option<f64>) -> TaskHealthSnapshot {
    let reason = if last_activity_ts.is_some() {
        "task_done"
    } else {
        "task_archived"
    };
    TaskHealthSnapshot {
        state: HEALTH_HEALTHY.to_string(),
        details: Map::from_iter([
            ("reasons".into(), json!([reason])),
            ("aggregate".into(), Value::Bool(false)),
            ("source_task_id".into(), Value::String(task_id.to_string())),
            (
                "last_activity_at".into(),
                Value::String(iso_or_empty(last_activity_ts)),
            ),
        ]),
    }
}

fn is_monitored_task(task: &BoardTask) -> bool {
    task.lane == "In Progress" || !task.agent_id.is_empty()
}

fn has_unmet_dependencies(task: &BoardTask, tasks_by_id: &HashMap<String, &BoardTask>) -> bool {
    task.depends_on
        .iter()
        .filter_map(|dep_id| tasks_by_id.get(dep_id))
        .any(|dep| !board_task_counts_as_done(Some(dep)))
}

fn stale_in_progress_reasons(
    task: &BoardTask,
    tasks_by_id: &HashMap<String, &BoardTask>,
    agents_by_id: &HashMap<String, AgentCell>,
) -> Vec<String> {
    if task.lane != "In Progress" || task.agent_id.is_empty() {
        return Vec::new();
    }
    let Some(agent) = agents_by_id.get(&task.agent_id) else {
        return Vec::new();
    };
    if agent.cell_type != "agent" || agent.status != "running" || !agent.activity.is_empty() {
        return Vec::new();
    }
    if task_has_open_child(task, tasks_by_id)
        || agent_has_other_open_work(&task.agent_id, &task.id, tasks_by_id)
    {
        return Vec::new();
    }

    let mut reasons = Vec::new();
    if agent.worktree_checkpoints > 0 {
        reasons.push("checkpointed_worktree".to_string());
    }
    if agent.worktree_ahead > 0 {
        reasons.push("branch_ahead_of_base".to_string());
    }
    if !agent.worktree_dirty && !agent.worktree_path.is_empty() && agent.worktree_checkpoints > 0 {
        reasons.push("clean_checkpointed_branch".to_string());
    }
    reasons
}

fn task_has_open_child(task: &BoardTask, tasks_by_id: &HashMap<String, &BoardTask>) -> bool {
    tasks_by_id.values().any(|other| {
        other.parent_task_id == task.id && other.lane != "Done" && other.lane != ARCHIVED_LANE
    })
}

fn agent_has_other_open_work(
    agent_id: &str,
    task_id: &str,
    tasks_by_id: &HashMap<String, &BoardTask>,
) -> bool {
    tasks_by_id.values().any(|other| {
        other.id != task_id
            && other.agent_id == agent_id
            && other.lane != "Done"
            && other.lane != "Backlog"
            && other.lane != ARCHIVED_LANE
    })
}

fn task_last_activity_ts(task: &BoardTask, agent: Option<&AgentCell>) -> Option<f64> {
    let mut timestamps = Vec::new();
    if let Some(ts) = parse_iso(&task.updated_at) {
        timestamps.push(ts);
    }
    if let Some(ts) = parse_iso(&task.created_at) {
        timestamps.push(ts);
    }
    for msg in &task.messages {
        if let Some(ts) = message_timestamp(msg) {
            timestamps.push(ts);
        }
    }
    if let Some(agent) = agent {
        if agent.last_event_at > 0.0 {
            timestamps.push(agent.last_event_at);
        }
    }
    timestamps.into_iter().reduce(f64::max)
}

fn is_thrashing(task: &BoardTask, now_ts: f64) -> bool {
    let mut recent = Vec::new();
    for msg in &task.messages {
        let Some(ts) = message_timestamp(msg) else {
            continue;
        };
        if now_ts - ts > THRASH_WINDOW_SECS {
            continue;
        }
        let action = msg.get("action").and_then(Value::as_str).unwrap_or("");
        if matches!(action, "done" | "ready") {
            return false;
        }
        if let Some(category) = action_category(action) {
            recent.push(category);
        }
    }
    if recent.len() < THRASH_MIN_MESSAGES {
        return false;
    }
    let mut transitions = 0usize;
    let mut prev = recent[0];
    for category in recent.into_iter().skip(1) {
        if category != prev {
            transitions += 1;
            prev = category;
        }
    }
    transitions >= THRASH_MIN_TRANSITIONS
}

fn action_category(action: &str) -> Option<&'static str> {
    match action {
        "progress" | "derive" | "ask" => Some("progress"),
        "blocked" | "error" => Some("blocked"),
        _ => None,
    }
}

fn message_timestamp(msg: &Value) -> Option<f64> {
    if let Some(ts) = msg.get("timestamp").and_then(Value::as_f64) {
        return Some(ts);
    }
    msg.get("timestamp")
        .and_then(Value::as_str)
        .and_then(parse_iso)
}

pub fn parse_iso(ts: &str) -> Option<f64> {
    if ts.trim().is_empty() {
        return None;
    }
    DateTime::parse_from_rfc3339(ts)
        .ok()
        .map(|dt| dt.timestamp() as f64)
}

fn iso_or_empty(ts: Option<f64>) -> String {
    ts.and_then(timestamp_to_datetime)
        .map(|dt| dt.to_rfc3339())
        .unwrap_or_default()
}

fn timestamp_to_datetime(ts: f64) -> Option<DateTime<Utc>> {
    let secs = ts.trunc() as i64;
    let nanos = ((ts.fract().abs()) * 1_000_000_000.0) as u32;
    Utc.timestamp_opt(secs, nanos).single()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocked_dependency_beats_recent_activity() {
        let mut parent = BoardTask::new_minimal("g-1", "parent");
        parent.group = "g".into();
        parent.depends_on = vec!["g-2".into()];
        parent.updated_at = "2026-04-15T12:00:00+00:00".into();

        let mut dep = BoardTask::new_minimal("g-2", "dep");
        dep.group = "g".into();
        dep.lane = "In Progress".into();

        let snapshots = compute_task_health(
            &HashMap::from([(parent.id.clone(), parent), (dep.id.clone(), dep)]),
            &HashMap::new(),
            Some(1_000_000.0),
        );
        assert_eq!(
            snapshots.get("g-1").map(|snapshot| snapshot.state.as_str()),
            Some(HEALTH_BLOCKED)
        );
    }
}
