//! Shared Weaver hint synthesis helpers.

use std::collections::HashMap;

use loom_core::state::{board_task_is_closed, AgentCell, BoardTask, MatrixState};
use serde_json::{json, Map, Value};

use crate::streams::{compute_worktree_streams, member_task_ids_for_stream};
use crate::task_health::{
    compute_task_health, TaskHealthSnapshot, HEALTH_IDLE_RISK, HEALTH_STALE_IN_PROGRESS,
    HEALTH_STALLED,
};

pub const WEAVER_HINT_RESEND_COOLDOWN_SECS: i64 = 30 * 60;
pub const WEAVER_HINT_GROUP_CLEANUP_MIN: usize = 2;
pub const WEAVER_HINT_GROUP_READY_TO_MERGE_MIN: usize = 2;
pub const WEAVER_HINT_GROUP_IDLE_RISK_MIN: usize = 2;

const MAX_PREVIEW_ITEMS: usize = 3;
const SEVERE_LONG_RUNNING_STATES: &[&str] = &[HEALTH_STALLED, HEALTH_STALE_IN_PROGRESS];
const LONG_RUNNING_STATES: &[&str] = &[HEALTH_STALLED, HEALTH_STALE_IN_PROGRESS, HEALTH_IDLE_RISK];

pub fn compute_weaver_hints(
    state: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    visibility_limit: usize,
) -> Vec<Value> {
    let group = group.trim();
    if group.is_empty() {
        return Vec::new();
    }

    let visibility_limit = visibility_limit.max(1);
    let streams = compute_worktree_streams(state, group, visibility_limit, false);
    let mut task_to_stream_id = HashMap::<String, String>::new();
    for stream in &streams {
        let stream_id = stream
            .get("stream_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if stream_id.is_empty() {
            continue;
        }
        for task_id in member_task_ids_for_stream(stream) {
            if !task_id.trim().is_empty() {
                task_to_stream_id
                    .entry(task_id)
                    .or_insert_with(|| stream_id.to_string());
            }
        }
    }

    let health = compute_task_health(&state.board_tasks, &state.agents, None);

    let mut hints = Vec::new();
    hints.extend(merged_cleanup_hints(state, group, weaver_id));
    hints.extend(ready_to_merge_hints(state, group, weaver_id, &streams));
    hints.extend(long_running_hints(
        state,
        group,
        weaver_id,
        &health,
        &task_to_stream_id,
    ));
    hints.sort_by(|a, b| {
        b.get("priority")
            .and_then(Value::as_i64)
            .unwrap_or(0)
            .cmp(&a.get("priority").and_then(Value::as_i64).unwrap_or(0))
            .then_with(|| {
                a.get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("kind").and_then(Value::as_str).unwrap_or(""))
            })
            .then_with(|| {
                a.get("fingerprint")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("fingerprint").and_then(Value::as_str).unwrap_or(""))
            })
    });
    hints
}

fn merged_cleanup_hints(state: &MatrixState, group: &str, weaver_id: Option<&str>) -> Vec<Value> {
    let mut candidates = state
        .agents
        .values()
        .filter(|cell| is_visible_actionable_agent(state, cell, group, weaver_id))
        .filter(|cell| !cell.worktree_path.trim().is_empty())
        .filter(|cell| cell.worktree_merged)
        .filter(|cell| !state.agent_is_busy(&cell.id))
        .filter(|cell| !cell.needs_attention)
        .filter(|cell| cell.status != "error")
        .cloned()
        .collect::<Vec<_>>();

    if candidates.len() < WEAVER_HINT_GROUP_CLEANUP_MIN {
        return Vec::new();
    }

    candidates.sort_by(|a, b| {
        agent_label(a)
            .to_lowercase()
            .cmp(&agent_label(b).to_lowercase())
            .then_with(|| a.id.cmp(&b.id))
    });
    let names = candidates.iter().map(agent_label).collect::<Vec<_>>();
    let agent_ids = candidates
        .iter()
        .map(|cell| Value::String(cell.id.clone()))
        .collect::<Vec<_>>();
    vec![json!({
        "kind": "merged_cleanup",
        "priority": 90,
        "fingerprint": format!("merged_cleanup:{}", candidates.iter().map(|cell| cell.id.clone()).collect::<Vec<_>>().join(",")),
        "message": format!(
            "{} idle agents have merged branches ready for cleanup: {}",
            candidates.len(),
            preview_list(&names),
        ),
        "agent_ids": agent_ids,
        "task_ids": [],
        "stream_ids": [],
    })]
}

fn ready_to_merge_hints(
    state: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    streams: &[Value],
) -> Vec<Value> {
    let mut candidates = Vec::<(Value, AgentCell)>::new();
    for stream in streams {
        if stream.get("state").and_then(Value::as_str) != Some("ready_to_merge") {
            continue;
        }
        let agent_id = stream
            .get("agent_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if agent_id.is_empty() {
            continue;
        }
        let Some(agent) = state.agents.get(agent_id) else {
            continue;
        };
        if !is_visible_actionable_agent(state, agent, group, weaver_id) {
            continue;
        }
        if state.agent_is_busy(agent_id) {
            continue;
        }
        candidates.push((stream.clone(), agent.clone()));
    }

    if candidates.len() < WEAVER_HINT_GROUP_READY_TO_MERGE_MIN {
        return Vec::new();
    }

    candidates.sort_by(|a, b| {
        a.0.get("branch")
            .and_then(Value::as_str)
            .unwrap_or("")
            .cmp(b.0.get("branch").and_then(Value::as_str).unwrap_or(""))
            .then_with(|| {
                stream_label(&a.0, &a.1)
                    .to_lowercase()
                    .cmp(&stream_label(&b.0, &b.1).to_lowercase())
            })
            .then_with(|| {
                a.0.get("stream_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.0.get("stream_id").and_then(Value::as_str).unwrap_or(""))
            })
    });

    let labels = candidates
        .iter()
        .map(|(stream, agent)| stream_label(stream, agent))
        .collect::<Vec<_>>();
    let agent_ids = candidates
        .iter()
        .map(|(_, agent)| Value::String(agent.id.clone()))
        .collect::<Vec<_>>();
    let stream_ids = candidates
        .iter()
        .filter_map(|(stream, _)| stream.get("stream_id").and_then(Value::as_str))
        .filter(|value| !value.trim().is_empty())
        .map(|value| Value::String(value.to_string()))
        .collect::<Vec<_>>();

    vec![json!({
        "kind": "ready_to_merge",
        "priority": 80,
        "fingerprint": format!("ready_to_merge:{}", stream_ids.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(",")),
        "message": format!(
            "{} idle streams are ready to merge: {}",
            candidates.len(),
            preview_list(&labels),
        ),
        "agent_ids": agent_ids,
        "task_ids": [],
        "stream_ids": stream_ids,
    })]
}

fn long_running_hints(
    state: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    health: &HashMap<String, TaskHealthSnapshot>,
    task_to_stream_id: &HashMap<String, String>,
) -> Vec<Value> {
    let mut selected = HashMap::<String, String>::new();
    for task in state.board_tasks.values() {
        if task.group != group || board_task_is_closed(Some(task)) {
            continue;
        }
        let Some(snapshot) = health.get(&task.id) else {
            continue;
        };
        if !LONG_RUNNING_STATES.contains(&snapshot.state.as_str()) {
            continue;
        }
        let source_task_id = snapshot
            .details
            .get("source_task_id")
            .and_then(Value::as_str)
            .unwrap_or(&task.id)
            .trim()
            .to_string();
        let replace = selected
            .get(&source_task_id)
            .and_then(|incumbent_id| {
                state
                    .board_tasks
                    .get(incumbent_id)
                    .zip(health.get(incumbent_id))
            })
            .map(|(incumbent_task, incumbent_snapshot)| {
                prefer_long_running_candidate(snapshot, incumbent_snapshot, task, incumbent_task)
            })
            .unwrap_or(true);
        if replace {
            selected.insert(source_task_id, task.id.clone());
        }
    }

    let mut severe = Vec::new();
    let mut idle_risk = Vec::<(BoardTask, BoardTask, AgentCell)>::new();
    for candidate_id in selected.values() {
        let Some(task) = state.board_tasks.get(candidate_id) else {
            continue;
        };
        let Some(snapshot) = health.get(candidate_id) else {
            continue;
        };
        let source_task_id = snapshot
            .details
            .get("source_task_id")
            .and_then(Value::as_str)
            .unwrap_or(&task.id)
            .trim();
        let mut source_task = state
            .board_tasks
            .get(source_task_id)
            .unwrap_or(task)
            .clone();
        if board_task_is_closed(Some(&source_task)) {
            source_task = task.clone();
        }
        let agent_id = if !source_task.agent_id.trim().is_empty() {
            source_task.agent_id.clone()
        } else {
            task.agent_id.clone()
        };
        if agent_id.trim().is_empty() {
            continue;
        }
        let Some(agent) = state.agents.get(&agent_id) else {
            continue;
        };
        if !is_visible_actionable_agent(state, agent, group, weaver_id) {
            continue;
        }
        if !state.agent_pending_weaver_reply_tasks(&agent_id).is_empty() {
            continue;
        }
        if SEVERE_LONG_RUNNING_STATES.contains(&snapshot.state.as_str()) {
            if let Some(hint) = severe_long_running_hint(
                task,
                &source_task,
                agent,
                snapshot,
                task_to_stream_id
                    .get(&source_task.id)
                    .cloned()
                    .unwrap_or_default(),
            ) {
                severe.push(hint);
            }
        } else if snapshot.state == HEALTH_IDLE_RISK {
            idle_risk.push((task.clone(), source_task, agent.clone()));
        }
    }

    let mut hints = severe;
    if idle_risk.len() >= WEAVER_HINT_GROUP_IDLE_RISK_MIN {
        idle_risk.sort_by(|a, b| {
            agent_label(&a.2)
                .to_lowercase()
                .cmp(&agent_label(&b.2).to_lowercase())
                .then_with(|| {
                    task_label(&a.1)
                        .to_lowercase()
                        .cmp(&task_label(&b.1).to_lowercase())
                })
                .then_with(|| a.1.id.cmp(&b.1.id))
        });
        let preview = idle_risk
            .iter()
            .map(|(_, source_task, agent)| {
                format!("{}/{}", agent_label(agent), task_label(source_task))
            })
            .collect::<Vec<_>>();
        let task_ids = idle_risk
            .iter()
            .map(|(_, source_task, _)| Value::String(source_task.id.clone()))
            .collect::<Vec<_>>();
        let agent_ids = idle_risk
            .iter()
            .map(|(_, _, agent)| Value::String(agent.id.clone()))
            .collect::<Vec<_>>();
        let stream_ids = idle_risk
            .iter()
            .filter_map(|(_, source_task, _)| task_to_stream_id.get(&source_task.id))
            .filter(|value| !value.trim().is_empty())
            .cloned()
            .map(Value::String)
            .collect::<Vec<_>>();
        hints.push(json!({
            "kind": "idle_risk_followup",
            "priority": 50,
            "fingerprint": format!("idle_risk:{}", task_ids.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(",")),
            "message": format!(
                "{} active tasks are at idle risk without Weaver follow-up: {}",
                idle_risk.len(),
                preview_list(&preview),
            ),
            "agent_ids": agent_ids,
            "task_ids": task_ids,
            "stream_ids": stream_ids,
        }));
    }
    hints
}

fn severe_long_running_hint(
    task: &BoardTask,
    source_task: &BoardTask,
    agent: &AgentCell,
    snapshot: &TaskHealthSnapshot,
    stream_id: String,
) -> Option<Value> {
    let silence_secs = snapshot
        .details
        .get("silence_secs")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let duration = if silence_secs > 0 {
        format_duration(silence_secs)
    } else {
        String::new()
    };
    let agent_name = agent_label(agent);
    let task_name = task_label(source_task);
    let (message, priority) = if snapshot.state == HEALTH_STALLED {
        if !duration.is_empty() {
            (
                format!(
                    "{} has been stalled on {} for {} without Weaver follow-up",
                    agent_name, task_name, duration
                ),
                70,
            )
        } else {
            (
                format!(
                    "{} has stalled on {} without Weaver follow-up",
                    agent_name, task_name
                ),
                70,
            )
        }
    } else {
        let stale_reason = stale_reason(&snapshot.details);
        if !stale_reason.is_empty() {
            (
                format!(
                    "{} looks idle on {} ({}) without Weaver follow-up",
                    agent_name, task_name, stale_reason
                ),
                60,
            )
        } else {
            (
                format!(
                    "{} looks idle on {} without Weaver follow-up",
                    agent_name, task_name
                ),
                60,
            )
        }
    };
    Some(json!({
        "kind": "long_running_followup",
        "priority": priority,
        "fingerprint": format!("{}:{}:{}", snapshot.state, source_task.id, agent.id),
        "message": message,
        "agent_ids": [agent.id],
        "task_ids": [source_task.id],
        "stream_ids": if stream_id.trim().is_empty() { Vec::<String>::new() } else { vec![stream_id] },
        "health_state": snapshot.state,
        "source_task_id": task.id,
    }))
}

fn prefer_long_running_candidate(
    candidate: &TaskHealthSnapshot,
    incumbent: &TaskHealthSnapshot,
    candidate_task: &BoardTask,
    incumbent_task: &BoardTask,
) -> bool {
    let candidate_aggregate = candidate
        .details
        .get("aggregate")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let incumbent_aggregate = incumbent
        .details
        .get("aggregate")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let candidate_silence = candidate
        .details
        .get("silence_secs")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let incumbent_silence = incumbent
        .details
        .get("silence_secs")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    (!candidate_aggregate && incumbent_aggregate)
        || (candidate_aggregate == incumbent_aggregate && candidate_silence > incumbent_silence)
        || (candidate_aggregate == incumbent_aggregate
            && candidate_silence == incumbent_silence
            && candidate_task.id < incumbent_task.id)
}

fn is_visible_actionable_agent(
    state: &MatrixState,
    cell: &AgentCell,
    group: &str,
    weaver_id: Option<&str>,
) -> bool {
    if cell.cell_type != "agent" || cell.group != group {
        return false;
    }
    let weaver_id = weaver_id.unwrap_or("").trim();
    if !weaver_id.is_empty() && cell.id == weaver_id {
        return false;
    }
    if !weaver_id.is_empty() && !state.agent_is_visible_to_weaver(weaver_id, &cell.id) {
        return false;
    }
    true
}

fn agent_label(cell: &AgentCell) -> String {
    let label = if !cell.slug.trim().is_empty() {
        &cell.slug
    } else if !cell.name.trim().is_empty() {
        &cell.name
    } else {
        &cell.id
    };
    label.trim().to_string()
}

fn task_label(task: &BoardTask) -> String {
    let title = if !task.task.trim().is_empty() {
        task.task.trim()
    } else {
        task.id.trim()
    };
    truncate_label(title, 48)
}

fn stream_label(stream: &Value, agent: &AgentCell) -> String {
    let branch = stream
        .get("branch")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if !branch.is_empty() {
        return truncate_label(branch, 48);
    }
    truncate_label(&agent_label(agent), 48)
}

fn truncate_label(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        value.to_string()
    } else {
        let mut out = value
            .chars()
            .take(limit.saturating_sub(1))
            .collect::<String>();
        out.push('…');
        out
    }
}

fn preview_list(items: &[String]) -> String {
    let preview = items
        .iter()
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect::<Vec<_>>();
    if preview.is_empty() {
        return "none".to_string();
    }
    let shown = preview
        .iter()
        .take(MAX_PREVIEW_ITEMS)
        .cloned()
        .collect::<Vec<_>>();
    let extra = preview.len().saturating_sub(shown.len());
    let mut text = shown.join(", ");
    if extra > 0 {
        text.push_str(&format!(" (+{} more)", extra));
    }
    text
}

fn stale_reason(details: &Map<String, Value>) -> String {
    let reasons = details
        .get("reasons")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect::<Vec<_>>();
    let mut parts = Vec::new();
    if reasons
        .iter()
        .any(|reason| reason == "clean_checkpointed_branch")
    {
        parts.push("clean checkpointed branch".to_string());
    } else if reasons
        .iter()
        .any(|reason| reason == "checkpointed_worktree")
    {
        parts.push("checkpointed worktree".to_string());
    }
    if reasons
        .iter()
        .any(|reason| reason == "branch_ahead_of_base")
    {
        parts.push("branch ahead of base".to_string());
    }
    parts.join(", ")
}

fn format_duration(seconds: i64) -> String {
    let minutes = std::cmp::max(1, seconds / 60);
    if minutes < 60 {
        format!("{}m", minutes)
    } else {
        let hours = minutes / 60;
        let rem = minutes % 60;
        if rem > 0 {
            format!("{}h {}m", hours, rem)
        } else {
            format!("{}h", hours)
        }
    }
}
