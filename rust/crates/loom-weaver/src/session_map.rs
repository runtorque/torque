//! Deterministic Session Map read model for Weaver recovery.

use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap};

use chrono::Utc;
use loom_core::state::{
    board_task_is_closed, AutoDispatchQueueEntry, BoardTask, MatrixState, ARCHIVED_LANE,
};
use serde_json::{json, Value};

use crate::hints::compute_weaver_hints;
use crate::streams::{compute_worktree_streams, stream_identity_for_agent};
use crate::task_health::{compute_task_health, health_severity};

const ITEM_LIMIT: usize = 10;
const JOURNAL_SCAN_LIMIT: usize = 40;
const JOURNAL_TYPES: &[&str] = &["checkpoint", "decision", "plan"];

pub fn build_weaver_session_map(
    state: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
) -> Value {
    build_weaver_session_map_with_limits(state, group, weaver_id, ITEM_LIMIT, JOURNAL_SCAN_LIMIT)
}

pub fn build_weaver_session_map_with_limits(
    state: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    item_limit: usize,
    journal_scan_limit: usize,
) -> Value {
    let group = group.trim();
    if group.is_empty() {
        return json!({});
    }
    let item_limit = item_limit.max(1);
    let journal_scan_limit = journal_scan_limit.max(item_limit);

    let tasks = state
        .board_tasks
        .values()
        .filter(|task| task.group == group)
        .cloned()
        .collect::<Vec<_>>();
    let visible_tasks = tasks
        .iter()
        .filter(|task| task.lane != ARCHIVED_LANE)
        .cloned()
        .collect::<Vec<_>>();
    let archived_tasks = tasks
        .iter()
        .filter(|task| task.lane == ARCHIVED_LANE)
        .cloned()
        .collect::<Vec<_>>();

    let health = compute_task_health(&state.board_tasks, &state.agents, None);
    let mut streams = compute_worktree_streams(state, group, item_limit.max(5), false)
        .into_iter()
        .filter(|stream| stream.get("state").and_then(Value::as_str) != Some("merged"))
        .map(|stream| scrub_stream(state, weaver_id, stream))
        .collect::<Vec<_>>();
    streams.sort_by(stream_sort_key);

    let asks = build_pending_asks(&visible_tasks, item_limit);
    let human_gates = build_human_gates(state, &streams, item_limit);
    let task_health = build_task_health(state, &visible_tasks, &health, item_limit);
    let verification = build_verification(&visible_tasks, item_limit);
    let branch_boundaries = build_branch_boundaries(state, &streams, weaver_id, item_limit);
    let agents = build_agents(state, group, weaver_id, item_limit);
    let queued_follow_up = build_queued_follow_up(state, group, &streams, weaver_id, item_limit);
    let journal = build_journal(state, group, item_limit, journal_scan_limit);
    let hints = build_hints(state, group, weaver_id, item_limit);

    let mut lane_counts = BTreeMap::<String, i64>::new();
    for lane_name in &state.board_lanes {
        if lane_name != ARCHIVED_LANE {
            lane_counts.insert(lane_name.clone(), 0);
        }
    }
    for task in &visible_tasks {
        if let Some(count) = lane_counts.get_mut(&task.lane) {
            *count += 1;
        }
    }

    json!({
        "group": group,
        "overview": {
            "generated_at": Utc::now().to_rfc3339(),
            "tasks_total": visible_tasks.len(),
            "archived_total": archived_tasks.len(),
            "lanes": lane_counts,
            "active_stream_count": streams.len(),
            "active_agent_count": agents.get("active_count").and_then(Value::as_u64).unwrap_or(0),
            "pending_ask_count": asks.get("count").and_then(Value::as_u64).unwrap_or(0),
            "human_gate_count": human_gates.get("count").and_then(Value::as_u64).unwrap_or(0),
            "unhealthy_task_count": task_health
                .get("counts")
                .and_then(Value::as_object)
                .map(|counts| counts.iter().filter(|(state_name, _)| state_name.as_str() != "healthy").map(|(_, count)| count.as_u64().unwrap_or(0)).sum::<u64>())
                .unwrap_or(0),
            "queued_follow_up_count": queued_follow_up.get("count").and_then(Value::as_u64).unwrap_or(0),
            "journal_entry_count": journal.get("count").and_then(Value::as_u64).unwrap_or(0),
        },
        "streams": {
            "count": streams.len(),
            "by_state": stream_state_counts(&streams),
            "items": streams.iter().take(item_limit).cloned().collect::<Vec<_>>(),
            "truncated": streams.len() > item_limit,
        },
        "asks": asks,
        "human_gates": human_gates,
        "task_health": task_health,
        "verification": verification,
        "branch_boundaries": branch_boundaries,
        "agents": agents,
        "queued_follow_up": queued_follow_up,
        "journal": journal,
        "hints": hints,
    })
}

fn build_pending_asks(tasks: &[BoardTask], limit: usize) -> Value {
    let mut items = tasks
        .iter()
        .filter(|task| {
            task.labels.iter().any(|label| label == "loom:human")
                && !board_task_is_closed(Some(task))
        })
        .map(|task| {
            json!({
                "id": task.id,
                "title": task.task,
                "parent_task_id": task.parent_task_id,
                "lane": task.lane,
                "created_at": task.created_at,
            })
        })
        .collect::<Vec<_>>();
    items.sort_by(|a, b| {
        a.get("created_at")
            .and_then(Value::as_str)
            .unwrap_or("")
            .cmp(b.get("created_at").and_then(Value::as_str).unwrap_or(""))
            .then_with(|| {
                a.get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase()
                    .cmp(
                        &b.get("title")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_lowercase(),
                    )
            })
            .then_with(|| {
                a.get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    json!({
        "count": items.len(),
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn build_human_gates(state: &MatrixState, streams: &[Value], limit: usize) -> Value {
    let mut items = Vec::new();
    for stream in streams {
        let queue_gate = stream
            .get("queue_gate")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let validation_state = stream
            .get("validation_state")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let gate_type = queue_gate
            .get("gate_type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        if gate_type != "human_validation" && validation_state != "pending_human_validation" {
            continue;
        }
        let blocking_task_id = queue_gate
            .get("blocking_task_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let blocking_task = state.board_tasks.get(&blocking_task_id);
        items.push(json!({
            "stream_id": stream.get("stream_id").and_then(Value::as_str).unwrap_or(""),
            "stream_title": stream_title(stream),
            "branch": stream.get("branch").and_then(Value::as_str).unwrap_or(""),
            "gate_reason": queue_gate.get("reason").and_then(Value::as_str).unwrap_or("").trim().to_string().if_empty_then(|| stream.get("gate_reason").and_then(Value::as_str).unwrap_or("").trim().to_string()),
            "recommended_next_action": stream.get("recommended_next_action").and_then(Value::as_str).unwrap_or(""),
            "blocking_task_id": blocking_task_id,
            "blocking_task_title": blocking_task.map(|task| task.task.clone()).unwrap_or_default(),
        }));
    }
    items.sort_by(|a, b| {
        a.get("branch")
            .and_then(Value::as_str)
            .unwrap_or("")
            .cmp(b.get("branch").and_then(Value::as_str).unwrap_or(""))
            .then_with(|| {
                stream_title(a)
                    .to_lowercase()
                    .cmp(&stream_title(b).to_lowercase())
            })
            .then_with(|| {
                a.get("stream_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("stream_id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    json!({
        "count": items.len(),
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn build_task_health(
    state: &MatrixState,
    tasks: &[BoardTask],
    health: &HashMap<String, crate::task_health::TaskHealthSnapshot>,
    limit: usize,
) -> Value {
    let mut counts = BTreeMap::from([
        ("blocked".to_string(), 0u64),
        ("healthy".to_string(), 0u64),
        ("idle-risk".to_string(), 0u64),
        ("stale-in-progress".to_string(), 0u64),
        ("stalled".to_string(), 0u64),
        ("thrashing".to_string(), 0u64),
    ]);
    let mut items = Vec::new();
    for task in tasks {
        if task.labels.iter().any(|label| label == "loom:human") {
            continue;
        }
        let snapshot = health.get(&task.id);
        let health_state = snapshot
            .map(|snapshot| snapshot.state.as_str())
            .unwrap_or_else(|| {
                if task.health_state.trim().is_empty() {
                    "healthy"
                } else {
                    task.health_state.as_str()
                }
            });
        *counts.entry(health_state.to_string()).or_insert(0) += 1;
        if board_task_is_closed(Some(task)) || health_state == "healthy" {
            continue;
        }
        let details = snapshot
            .map(|snapshot| &snapshot.details)
            .unwrap_or(&task.health_details);
        let source_task_id = details
            .get("source_task_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let source_task = if source_task_id.is_empty() {
            None
        } else {
            state.board_tasks.get(source_task_id)
        };
        items.push(json!({
            "id": task.id,
            "title": task.task,
            "health_state": health_state,
            "health_since": effective_health_since(task, snapshot),
            "via": source_task.filter(|source_task| source_task.id != task.id).map(|source_task| source_task.task.clone()).unwrap_or_default(),
        }));
    }
    items.sort_by(|a, b| {
        let a_state = a
            .get("health_state")
            .and_then(Value::as_str)
            .unwrap_or("healthy");
        let b_state = b
            .get("health_state")
            .and_then(Value::as_str)
            .unwrap_or("healthy");
        health_severity(b_state)
            .cmp(&health_severity(a_state))
            .then_with(|| {
                a.get("health_since")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("health_since").and_then(Value::as_str).unwrap_or(""))
            })
            .then_with(|| {
                a.get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase()
                    .cmp(
                        &b.get("title")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_lowercase(),
                    )
            })
            .then_with(|| {
                a.get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    json!({
        "counts": counts,
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn build_verification(tasks: &[BoardTask], limit: usize) -> Value {
    let mut counts = BTreeMap::from([
        ("attempted".to_string(), 0u64),
        ("failed".to_string(), 0u64),
        ("passed".to_string(), 0u64),
        ("pending".to_string(), 0u64),
    ]);
    let mut items = Vec::new();
    for task in tasks {
        let verification_state = task.verification_state.trim();
        if verification_state.is_empty() || !counts.contains_key(verification_state) {
            continue;
        }
        *counts.entry(verification_state.to_string()).or_insert(0) += 1;
        if !matches!(verification_state, "pending" | "failed") {
            continue;
        }
        let detail = task
            .verification_summary
            .get("human_validation_pending")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .unwrap_or(task.verification_notes.as_str())
            .trim()
            .to_string();
        items.push(json!({
            "id": task.id,
            "title": task.task,
            "verification_state": verification_state,
            "verification_mode": task.verification_mode,
            "detail": detail,
        }));
    }
    items.sort_by(|a, b| {
        let a_failed = a.get("verification_state").and_then(Value::as_str) == Some("failed");
        let b_failed = b.get("verification_state").and_then(Value::as_str) == Some("failed");
        (!a_failed)
            .cmp(&!b_failed)
            .then_with(|| {
                a.get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase()
                    .cmp(
                        &b.get("title")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_lowercase(),
                    )
            })
            .then_with(|| {
                a.get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    json!({
        "counts": counts,
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn build_branch_boundaries(
    state: &MatrixState,
    streams: &[Value],
    weaver_id: Option<&str>,
    limit: usize,
) -> Value {
    let mut items = Vec::new();
    let mut seen = BTreeMap::<String, bool>::new();
    for stream in streams {
        let repo_root = stream
            .get("repo_root")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let branch = stream
            .get("branch")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if repo_root.is_empty() || branch.is_empty() {
            continue;
        }
        let key = format!("{}::{}", repo_root, branch);
        if seen.insert(key, true).is_some() {
            continue;
        }
        let agent_id = stream
            .get("agent_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let agent = if agent_id.is_empty() {
            None
        } else {
            state.agents.get(&agent_id)
        };
        let mut item = json!({
            "repo_root": repo_root,
            "branch": branch,
            "latest_boundary_task_id": stream.get("latest_boundary_task_id").and_then(Value::as_str).unwrap_or(""),
            "latest_boundary_task": stream.get("latest_boundary_task_title").and_then(Value::as_str).unwrap_or(""),
            "latest_boundary_status": stream.get("latest_boundary_status").and_then(Value::as_str).unwrap_or(""),
            "latest_boundary_recorded_at": stream.get("latest_boundary_recorded_at").and_then(Value::as_str).unwrap_or(""),
            "latest_boundary_commit_sha": stream.get("latest_reviewed_commit_sha").and_then(Value::as_str).unwrap_or(""),
            "queued_followups": stream.get("queued_followups").cloned().unwrap_or_else(|| json!([])),
            "started_followups": stream.get("started_followups").cloned().unwrap_or_else(|| json!([])),
            "queued_count": stream.get("queued_followups").and_then(Value::as_array).map(|items| items.len()).unwrap_or(0),
            "started_count": stream.get("started_followups").and_then(Value::as_array).map(|items| items.len()).unwrap_or(0),
            "branch_advanced": stream.get("branch_advanced").and_then(Value::as_bool).unwrap_or(false),
            "partial_review_safe": stream.get("partial_review_safe").and_then(Value::as_bool).unwrap_or(false),
            "stream_id": stream.get("stream_id").and_then(Value::as_str).unwrap_or(""),
            "stream_title": stream_title(stream),
            "foreground_task_id": stream.get("foreground_task_id").and_then(Value::as_str).unwrap_or(""),
            "foreground_task_title": stream.get("foreground_task_title").and_then(Value::as_str).unwrap_or(""),
        });
        if !agent_id.is_empty() && agent_visible(state, weaver_id, &agent_id) {
            item["agent_id"] = Value::String(agent_id.clone());
            item["agent_name"] = Value::String(
                stream
                    .get("agent_name")
                    .and_then(Value::as_str)
                    .filter(|value| !value.trim().is_empty())
                    .map(str::to_string)
                    .or_else(|| {
                        stream
                            .get("agent_slug")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .or_else(|| agent.map(|agent| agent.name.clone()))
                    .unwrap_or_default(),
            );
        } else if !agent_id.is_empty() {
            item["agent_id"] = Value::String(String::new());
            item["agent_name"] = Value::String(String::new());
            if hidden_agent_marker(state, weaver_id) {
                item["agent_hidden"] = Value::Bool(true);
            }
        }
        items.push(item);
    }
    items.sort_by(|a, b| {
        (!a.get("partial_review_safe")
            .and_then(Value::as_bool)
            .unwrap_or(false))
        .cmp(
            &!b.get("partial_review_safe")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        )
        .then_with(|| {
            a.get("branch")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("branch").and_then(Value::as_str).unwrap_or(""))
        })
        .then_with(|| {
            a.get("latest_boundary_recorded_at")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(
                    b.get("latest_boundary_recorded_at")
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                )
        })
        .then_with(|| {
            a.get("stream_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("stream_id").and_then(Value::as_str).unwrap_or(""))
        })
    });
    json!({
        "count": items.len(),
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn build_agents(state: &MatrixState, group: &str, weaver_id: Option<&str>, limit: usize) -> Value {
    let designated_weaver_id = state.get_group_settings(group).weaver_agent_id;
    let effective_weaver_id = weaver_id
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(designated_weaver_id.as_str())
        .trim()
        .to_string();
    let mut counts = BTreeMap::from([
        ("error".to_string(), 0u64),
        ("idle".to_string(), 0u64),
        ("running".to_string(), 0u64),
        ("stopped".to_string(), 0u64),
    ]);
    let mut total = 0u64;
    let mut needs_attention = 0u64;
    let mut items = Vec::new();
    for cell in state.agents.values() {
        if cell.cell_type != "agent" || cell.group != group {
            continue;
        }
        if !effective_weaver_id.is_empty() && cell.id == effective_weaver_id {
            continue;
        }
        if !agent_visible(state, Some(effective_weaver_id.as_str()), &cell.id) {
            continue;
        }
        total += 1;
        if cell.needs_attention {
            needs_attention += 1;
        }
        let status = if cell.status.trim().is_empty() {
            "stopped"
        } else {
            cell.status.as_str()
        };
        *counts.entry(status.to_string()).or_insert(0) += 1;
        if status == "stopped" {
            continue;
        }
        let current_task = state.agent_current_task(&cell.id);
        let identity = stream_identity_for_agent(cell);
        items.push(json!({
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "status": status,
            "type": cell.agent_type,
            "needs_attention": cell.needs_attention,
            "current_task_id": current_task.map(|task| task.id.clone()).unwrap_or_default(),
            "current_task": current_task.map(|task| task.task.clone()).unwrap_or_default(),
            "activity": cell.activity,
            "activity_detail": cell.activity_detail,
            "branch": identity.as_ref().map(|(_, branch)| branch.clone()).unwrap_or_default(),
            "repo_root": identity.as_ref().map(|(repo_root, _)| repo_root.clone()).unwrap_or_default(),
        }));
    }
    items.sort_by(|a, b| {
        (a.get("status").and_then(Value::as_str) != Some("running"))
            .cmp(&(b.get("status").and_then(Value::as_str) != Some("running")))
            .then_with(|| {
                (!a.get("needs_attention")
                    .and_then(Value::as_bool)
                    .unwrap_or(false))
                .cmp(
                    &!b.get("needs_attention")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                )
            })
            .then_with(|| {
                a.get("name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase()
                    .cmp(
                        &b.get("name")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_lowercase(),
                    )
            })
            .then_with(|| {
                a.get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    json!({
        "total": total,
        "active_count": items.len(),
        "needs_attention": needs_attention,
        "by_status": counts,
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn build_queued_follow_up(
    state: &MatrixState,
    group: &str,
    streams: &[Value],
    weaver_id: Option<&str>,
    limit: usize,
) -> Value {
    let mut items = Vec::new();
    for stream in streams {
        let stream_title = stream_title(stream);
        let branch = stream.get("branch").and_then(Value::as_str).unwrap_or("");
        let gate_reason = stream
            .get("queue_gate")
            .and_then(Value::as_object)
            .and_then(|gate| gate.get("reason"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string()
            .if_empty_then(|| {
                stream
                    .get("gate_reason")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim()
                    .to_string()
            });
        for queue_item in stream
            .get("queue_items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            items.push(json!({
                "source": "stream_queue",
                "task_id": queue_item.get("task_id").and_then(Value::as_str).unwrap_or(""),
                "task_title": queue_item.get("task_title").and_then(Value::as_str).unwrap_or(""),
                "lane": queue_item.get("lane").and_then(Value::as_str).unwrap_or(""),
                "queue_state": queue_item.get("queue_state").and_then(Value::as_str).unwrap_or(""),
                "deps_met": queue_item.get("deps_met").and_then(Value::as_bool).unwrap_or(false),
                "held": queue_item.get("held").and_then(Value::as_bool).unwrap_or(false),
                "stream_id": stream.get("stream_id").and_then(Value::as_str).unwrap_or(""),
                "stream_title": stream_title,
                "branch": branch,
                "gate_reason": gate_reason,
            }));
        }
    }

    for entry in state
        .auto_dispatch_queues
        .get(group)
        .cloned()
        .unwrap_or_default()
    {
        items.push(dispatch_queue_item(state, &entry, weaver_id));
    }

    items.sort_by(queued_follow_up_sort_key);
    json!({
        "count": items.len(),
        "items": items.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": items.len() > limit,
    })
}

fn dispatch_queue_item(
    state: &MatrixState,
    entry: &AutoDispatchQueueEntry,
    weaver_id: Option<&str>,
) -> Value {
    let task = state.board_tasks.get(&entry.task_id);
    let target_agent_id = entry.target_agent_id.trim();
    let target_agent = if target_agent_id.is_empty() {
        None
    } else {
        state.agents.get(target_agent_id)
    };
    let mut item = json!({
        "source": "dispatch_queue",
        "task_id": entry.task_id,
        "task_title": task.map(|task| task.task.clone()).unwrap_or_else(|| entry.task_id.clone()),
        "lane": task.map(|task| task.lane.clone()).unwrap_or_default(),
        "deps_met": task.map(|task| state.board_deps_met(task)).unwrap_or(true),
        "agent_group": entry.agent_group,
        "max_concurrent": entry.max_concurrent,
        "enqueued_at": entry.enqueued_at,
    });
    if !target_agent_id.is_empty() && agent_visible(state, weaver_id, target_agent_id) {
        item["target_agent_id"] = Value::String(target_agent_id.to_string());
        item["target_agent_name"] = Value::String(
            target_agent
                .map(|agent| {
                    if !agent.name.trim().is_empty() {
                        agent.name.clone()
                    } else if !agent.slug.trim().is_empty() {
                        agent.slug.clone()
                    } else {
                        target_agent_id.to_string()
                    }
                })
                .unwrap_or_else(|| target_agent_id.to_string()),
        );
    } else if !target_agent_id.is_empty() {
        item["target_agent_id"] = Value::String(String::new());
        item["target_agent_name"] = Value::String(String::new());
        if hidden_agent_marker(state, weaver_id) {
            item["target_agent_hidden"] = Value::Bool(true);
        }
    }
    item
}

fn build_journal(
    state: &MatrixState,
    group: &str,
    limit: usize,
    journal_scan_limit: usize,
) -> Value {
    let mut entries = state.weaver_journal.get(group).cloned().unwrap_or_default();
    entries.sort_by(|a, b| compare_journal_entries_desc(a, b));
    let filtered = entries
        .into_iter()
        .filter(|entry| {
            JOURNAL_TYPES.contains(&entry.get("type").and_then(Value::as_str).unwrap_or(""))
        })
        .take(journal_scan_limit)
        .map(|entry| {
            json!({
                "id": entry.get("id").and_then(Value::as_i64).unwrap_or(0),
                "type": entry.get("type").and_then(Value::as_str).unwrap_or(""),
                "timestamp": entry.get("timestamp").cloned().unwrap_or(Value::Null),
                "entry": entry.get("entry").and_then(Value::as_str).unwrap_or(""),
            })
        })
        .collect::<Vec<_>>();
    let mut counts = BTreeMap::<String, u64>::new();
    for entry_type in JOURNAL_TYPES {
        counts.insert((*entry_type).to_string(), 0);
    }
    for entry in &filtered {
        if let Some(entry_type) = entry.get("type").and_then(Value::as_str) {
            *counts.entry(entry_type.to_string()).or_insert(0) += 1;
        }
    }
    json!({
        "count": filtered.len(),
        "counts": counts,
        "items": filtered.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": filtered.len() > limit,
    })
}

fn build_hints(state: &MatrixState, group: &str, weaver_id: Option<&str>, limit: usize) -> Value {
    let hints = compute_weaver_hints(state, group, weaver_id, limit);
    json!({
        "count": hints.len(),
        "items": hints.iter().take(limit).cloned().collect::<Vec<_>>(),
        "truncated": hints.len() > limit,
    })
}

fn stream_state_counts(streams: &[Value]) -> Value {
    let mut counts = BTreeMap::<String, u64>::new();
    for stream in streams {
        let state_name = stream
            .get("state")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if !state_name.is_empty() {
            *counts.entry(state_name.to_string()).or_insert(0) += 1;
        }
    }
    json!(counts)
}

fn scrub_stream(state: &MatrixState, weaver_id: Option<&str>, mut stream: Value) -> Value {
    let agent_id = stream
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if agent_id.is_empty() || agent_visible(state, weaver_id, &agent_id) {
        return stream;
    }
    if let Some(obj) = stream.as_object_mut() {
        obj.insert("agent_id".into(), Value::String(String::new()));
        obj.insert("agent_name".into(), Value::String(String::new()));
        obj.insert("agent_slug".into(), Value::String(String::new()));
        if hidden_agent_marker(state, weaver_id) {
            obj.insert("agent_hidden".into(), Value::Bool(true));
        }
    }
    stream
}

fn agent_visible(state: &MatrixState, weaver_id: Option<&str>, agent_id: &str) -> bool {
    let agent_id = agent_id.trim();
    if agent_id.is_empty() {
        return false;
    }
    let Some(weaver_id) = weaver_id.map(str::trim).filter(|value| !value.is_empty()) else {
        return true;
    };
    state.agent_is_visible_to_weaver(weaver_id, agent_id)
}

fn hidden_agent_marker(state: &MatrixState, weaver_id: Option<&str>) -> bool {
    let Some(weaver_id) = weaver_id.map(str::trim).filter(|value| !value.is_empty()) else {
        return false;
    };
    let Some(weaver) = state.agents.get(weaver_id) else {
        return false;
    };
    state.weaver_restricts_to_created_agents(&weaver.group)
}

fn stream_title(stream: &Value) -> String {
    for key in [
        "foreground_task_title",
        "latest_boundary_task_title",
        "branch",
        "stream_id",
    ] {
        if let Some(value) = stream.get(key).and_then(Value::as_str) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    "Stream".to_string()
}

fn stream_sort_key(a: &Value, b: &Value) -> Ordering {
    (a.get("state").and_then(Value::as_str) != Some("awaiting_human_validation"))
        .cmp(&(b.get("state").and_then(Value::as_str) != Some("awaiting_human_validation")))
        .then_with(|| {
            a.get("branch")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("branch").and_then(Value::as_str).unwrap_or(""))
        })
        .then_with(|| {
            stream_title(a)
                .to_lowercase()
                .cmp(&stream_title(b).to_lowercase())
        })
        .then_with(|| {
            a.get("stream_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("stream_id").and_then(Value::as_str).unwrap_or(""))
        })
}

fn queued_follow_up_sort_key(a: &Value, b: &Value) -> Ordering {
    let order = |item: &Value| -> (i32, i32, String, String, String) {
        if item.get("source").and_then(Value::as_str) == Some("stream_queue") {
            let queue_state_order = match item
                .get("queue_state")
                .and_then(Value::as_str)
                .unwrap_or("")
            {
                "ready_to_resume" => 0,
                "paused_by_validation" => 1,
                "paused_by_blocker" => 2,
                "paused_by_review" => 3,
                "held" => 4,
                _ => 5,
            };
            (
                0,
                queue_state_order,
                item.get("branch")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                item.get("task_title")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase(),
                item.get("task_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            )
        } else {
            (
                1,
                0,
                item.get("enqueued_at")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                item.get("task_title")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase(),
                item.get("task_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            )
        }
    };
    order(a).cmp(&order(b))
}

fn effective_health_since(
    task: &BoardTask,
    snapshot: Option<&crate::task_health::TaskHealthSnapshot>,
) -> String {
    if let Some(snapshot) = snapshot {
        if task.health_state == snapshot.state && !task.health_since.trim().is_empty() {
            return task.health_since.clone();
        }
        if let Some(last_activity_at) = snapshot
            .details
            .get("last_activity_at")
            .and_then(Value::as_str)
        {
            if !last_activity_at.trim().is_empty() {
                return last_activity_at.to_string();
            }
        }
    }
    if !task.health_since.trim().is_empty() {
        task.health_since.clone()
    } else if !task.updated_at.trim().is_empty() {
        task.updated_at.clone()
    } else {
        task.created_at.clone()
    }
}

fn journal_entry_sort_key(entry: &Value) -> (i64, f64) {
    (
        entry.get("id").and_then(Value::as_i64).unwrap_or(0),
        entry
            .get("timestamp")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
    )
}

fn compare_journal_entries_desc(a: &Value, b: &Value) -> Ordering {
    let (a_id, a_ts) = journal_entry_sort_key(a);
    let (b_id, b_ts) = journal_entry_sort_key(b);
    b_id.cmp(&a_id)
        .then_with(|| b_ts.partial_cmp(&a_ts).unwrap_or(Ordering::Equal))
}

trait StringExt {
    fn if_empty_then<F: FnOnce() -> String>(self, fallback: F) -> String;
}

impl StringExt for String {
    fn if_empty_then<F: FnOnce() -> String>(self, fallback: F) -> String {
        if self.trim().is_empty() {
            fallback()
        } else {
            self
        }
    }
}
