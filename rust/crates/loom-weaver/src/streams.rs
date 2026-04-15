//! Computed worktree streams. Partial port of `loom/worktree_streams.py`.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::Path;
use std::process::Command;

use chrono::Utc;
use loom_core::state::{
    board_task_counts_as_done, AgentCell, BoardTask, MatrixState, ARCHIVED_LANE,
};
use serde_json::{json, Value};

use crate::task_health::parse_iso;

const QUEUED_LANES: &[&str] = &["Backlog", "To Do"];
const VISIBILITY_LABELS: &[&str] = &["loom:weaver-message"];
const WORKFLOW_LABELS: &[&str] = &["loom:derived", "review-fix", "loom:human"];
const HELD_LABELS: &[&str] = &["loom:hold", "hold"];
const REVIEW_HINTS: &[&str] = &["review", "re-review"];
const VALIDATION_HINTS: &[&str] = &["validate", "validation", "manual smoke", "smoke test"];
const MERGE_CONFLICT_HINTS: &[&str] = &["merge conflict", "resolve conflict", "conflict"];
const PRODUCT_ACTIONS: &[&str] = &[
    "feature/implement",
    "oneshot/feature",
    "oneshot/fix",
    "oneshot/improvement",
];
const NON_PRODUCT_ACTION_HINTS: &[&str] = &[
    "review",
    "validate",
    "validation",
    "merge",
    "conflict",
    "research",
];

pub fn branch_key(repo_root: &str, branch: &str) -> String {
    format!("{}::{}", repo_root.trim(), branch.trim())
}

pub fn stream_id(repo_root: &str, branch: &str) -> String {
    format!("stream:{}", branch_key(repo_root, branch))
}

pub fn stream_identity_for_agent(cell: &AgentCell) -> Option<(String, String)> {
    let repo_root = if !cell.worktree_repo_root.is_empty() {
        cell.worktree_repo_root.clone()
    } else {
        cell.git_root.clone()
    };
    let branch = cell.worktree_branch.clone();
    if repo_root.trim().is_empty() || branch.trim().is_empty() {
        return None;
    }
    Some((repo_root, branch))
}

pub fn member_task_ids_for_stream(stream: &Value) -> BTreeSet<String> {
    let mut ids = BTreeSet::new();
    for key in [
        "product_task_ids",
        "workflow_task_ids",
        "queued_task_ids",
        "started_task_ids",
    ] {
        for item in stream
            .get(key)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if !item.trim().is_empty() {
                ids.insert(item.to_string());
            }
        }
    }
    for key in [
        "foreground_task_id",
        "latest_boundary_task_id",
        "active_review_task_id",
        "active_blocker_task_id",
        "ready_to_resume_task_id",
    ] {
        if let Some(item) = stream.get(key).and_then(Value::as_str) {
            if !item.trim().is_empty() {
                ids.insert(item.to_string());
            }
        }
    }
    for key in ["recent_visibility_items", "queue_items"] {
        for item in stream
            .get(key)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(task_id) = item.get("task_id").and_then(Value::as_str) {
                if !task_id.trim().is_empty() {
                    ids.insert(task_id.to_string());
                }
            }
        }
    }
    for key in ["blocking_task_id", "source_task_id"] {
        if let Some(task_id) = stream
            .get("queue_gate")
            .and_then(Value::as_object)
            .and_then(|gate| gate.get(key))
            .and_then(Value::as_str)
        {
            if !task_id.trim().is_empty() {
                ids.insert(task_id.to_string());
            }
        }
    }
    ids
}

pub fn compute_worktree_stream_for_task(
    state: &MatrixState,
    task_id: &str,
    group: &str,
    visibility_limit: usize,
) -> Option<Value> {
    compute_worktree_streams(state, group, visibility_limit, true)
        .into_iter()
        .find(|stream| member_task_ids_for_stream(stream).contains(task_id))
}

pub fn compute_worktree_streams(
    state: &MatrixState,
    group: &str,
    visibility_limit: usize,
    include_orphaned: bool,
) -> Vec<Value> {
    let tasks = if group.is_empty() {
        state
            .board_tasks
            .values()
            .filter(|task| task.lane != ARCHIVED_LANE)
            .cloned()
            .collect::<Vec<_>>()
    } else {
        state
            .tasks_in_group(group)
            .into_iter()
            .filter(|task| task.lane != ARCHIVED_LANE)
            .cloned()
            .collect::<Vec<_>>()
    };
    let agents = state
        .agents
        .values()
        .filter(|cell| cell.cell_type == "agent" && (group.is_empty() || cell.group == group))
        .cloned()
        .collect::<Vec<_>>();

    let mut seeds_by_stream = BTreeMap::<String, BTreeSet<String>>::new();
    let mut tasks_by_id = HashMap::<String, BoardTask>::new();
    let mut stream_key_by_task = HashMap::<String, String>::new();

    for task in &tasks {
        tasks_by_id.insert(task.id.clone(), task.clone());
        if let Some((repo_root, branch)) = task_boundary_identity(task) {
            let key = branch_key(&repo_root, &branch);
            seeds_by_stream
                .entry(key.clone())
                .or_default()
                .insert(task.id.clone());
            stream_key_by_task.insert(task.id.clone(), key);
        }
    }

    for agent in &agents {
        if let Some((repo_root, branch)) = stream_identity_for_agent(agent) {
            let key = branch_key(&repo_root, &branch);
            let seeds = seeds_by_stream.entry(key.clone()).or_default();
            if !agent.current_task_id.is_empty() && tasks_by_id.contains_key(&agent.current_task_id)
            {
                seeds.insert(agent.current_task_id.clone());
                stream_key_by_task.insert(agent.current_task_id.clone(), key.clone());
            }
            for task in tasks_by_id.values() {
                if task.agent_id == agent.id || task.reply_agent_id == agent.id {
                    seeds.insert(task.id.clone());
                    stream_key_by_task.insert(task.id.clone(), key.clone());
                }
            }
        }
    }

    for task in tasks_by_id.values() {
        if !task.resume_after_boundary_task_id.is_empty() {
            if let Some(key) = stream_key_by_task.get(&task.resume_after_boundary_task_id) {
                seeds_by_stream
                    .entry(key.clone())
                    .or_default()
                    .insert(task.id.clone());
            }
        }
    }

    let mut streams = Vec::new();
    for key in seeds_by_stream.keys() {
        let mut split = key.splitn(2, "::");
        let repo_root = split.next().unwrap_or("").to_string();
        let branch = split.next().unwrap_or("").to_string();
        let Some(stream) = compute_stream_for_identity(
            state,
            &tasks_by_id,
            &agents,
            &repo_root,
            &branch,
            visibility_limit,
        ) else {
            continue;
        };
        if include_orphaned
            || stream.get("stream_presence").and_then(Value::as_str) != Some("orphaned")
        {
            streams.push(stream);
        }
    }

    streams.sort_by(|a, b| {
        let a_ts = a
            .get("last_activity_at")
            .and_then(Value::as_str)
            .and_then(parse_iso)
            .unwrap_or(0.0);
        let b_ts = b
            .get("last_activity_at")
            .and_then(Value::as_str)
            .and_then(parse_iso)
            .unwrap_or(0.0);
        b_ts.partial_cmp(&a_ts)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                a.get("branch")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("branch").and_then(Value::as_str).unwrap_or(""))
            })
            .then_with(|| {
                a.get("stream_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("stream_id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    streams
}

fn compute_stream_for_identity(
    state: &MatrixState,
    tasks_by_id: &HashMap<String, BoardTask>,
    agents: &[AgentCell],
    repo_root: &str,
    branch: &str,
    visibility_limit: usize,
) -> Option<Value> {
    let key = branch_key(repo_root, branch);
    let stream_tasks = tasks_by_id
        .values()
        .filter(|task| task_matches_stream(task, agents, &key, tasks_by_id))
        .cloned()
        .collect::<Vec<_>>();
    if stream_tasks.is_empty() {
        return None;
    }

    let boundary_tasks = sorted_tasks(
        stream_tasks
            .iter()
            .filter(|task| {
                task_boundary_identity(task)
                    .as_ref()
                    .map(|(r, b)| branch_key(r, b))
                    == Some(key.clone())
            })
            .cloned()
            .collect(),
    );
    let latest_boundary = boundary_tasks.last().cloned();
    let review_boundaries = boundary_tasks
        .iter()
        .filter(|task| is_review_task(task))
        .cloned()
        .collect::<Vec<_>>();
    let latest_review_boundary = review_boundaries.last().cloned();

    let mut product_tasks = Vec::new();
    let mut workflow_tasks = Vec::new();
    let mut visibility_tasks = Vec::new();
    for task in &stream_tasks {
        match classify_stream_task(task, tasks_by_id) {
            "visibility" => visibility_tasks.push(task.clone()),
            "workflow" => workflow_tasks.push(task.clone()),
            _ => product_tasks.push(task.clone()),
        }
    }
    product_tasks = sorted_tasks(product_tasks);
    workflow_tasks = sorted_tasks(workflow_tasks);

    let open_non_visibility = sorted_open_tasks(
        stream_tasks
            .iter()
            .filter(|task| !board_task_counts_as_done(Some(task)) && !is_visibility_task(task))
            .cloned()
            .collect(),
    );
    let queued_tasks = sorted_open_tasks(
        open_non_visibility
            .iter()
            .filter(|task| QUEUED_LANES.contains(&task.lane.as_str()))
            .cloned()
            .collect(),
    );
    let started_tasks = sorted_open_tasks(
        open_non_visibility
            .iter()
            .filter(|task| !QUEUED_LANES.contains(&task.lane.as_str()))
            .cloned()
            .collect(),
    );
    let review_tasks = sorted_open_tasks(
        open_non_visibility
            .iter()
            .filter(|task| is_review_task(task))
            .cloned()
            .collect(),
    );
    let blocker_tasks = sorted_open_tasks(
        open_non_visibility
            .iter()
            .filter(|task| is_blocker_task(task, tasks_by_id))
            .cloned()
            .collect(),
    );
    let validation_tasks = sorted_open_tasks(
        open_non_visibility
            .iter()
            .filter(|task| is_validation_task(task))
            .cloned()
            .collect(),
    );

    let reference_boundary = latest_review_boundary
        .clone()
        .or_else(|| latest_boundary.clone());
    let queued_followers = reference_boundary
        .as_ref()
        .map(|boundary| queued_successor_tasks(&stream_tasks, &boundary.id))
        .unwrap_or_default();
    let started_followers = reference_boundary
        .as_ref()
        .map(|boundary| started_successor_tasks(&stream_tasks, &boundary.id))
        .unwrap_or_default();

    let merged = is_merged_stream(&boundary_tasks, agents, repo_root, branch);
    let (validation_state, validation_record) = compute_validation_state(&stream_tasks);
    let pending_validation = validation_state == "pending_human_validation";
    let merge_conflict = blocker_tasks
        .iter()
        .any(|task| is_merge_conflict_task(task));
    let partial_review_safe = latest_review_boundary.is_some() && started_followers.is_empty();
    let branch_advanced = latest_review_boundary.is_some() && !started_followers.is_empty();

    let code_state = if merged {
        "merged"
    } else if merge_conflict {
        "merge_conflict"
    } else if !blocker_tasks.is_empty() {
        "review_blocked"
    } else if latest_review_boundary.is_some() && partial_review_safe && review_tasks.is_empty() {
        "reviewed_clean"
    } else {
        "implementing"
    };

    let open_product_tasks = open_non_visibility
        .iter()
        .filter(|task| classify_stream_task(task, tasks_by_id) == "product")
        .cloned()
        .collect::<Vec<_>>();

    let stream_state = if merged {
        "merged"
    } else if merge_conflict || !blocker_tasks.is_empty() {
        "fixing_blockers"
    } else if !review_tasks.is_empty() {
        "reviewing"
    } else if pending_validation && code_state == "reviewed_clean" {
        "awaiting_human_validation"
    } else if code_state == "reviewed_clean"
        && open_product_tasks.is_empty()
        && queued_tasks.is_empty()
    {
        "ready_to_merge"
    } else {
        "implementing"
    };

    let merge_state = if merged {
        "merged"
    } else if stream_state == "ready_to_merge" {
        "ready"
    } else {
        "not_ready"
    };

    let active_blocker_task = blocker_tasks.first().cloned();
    let active_review_task = review_tasks.first().cloned();
    let foreground_task = select_foreground_task(
        state,
        repo_root,
        branch,
        &stream_tasks,
        &started_tasks,
        active_blocker_task.as_ref(),
        active_review_task.as_ref(),
    );
    let owner_agent = select_owner_agent(
        state,
        agents,
        repo_root,
        branch,
        foreground_task.as_ref(),
        &stream_tasks,
    );
    let visibility_items = recent_visibility_items(&visibility_tasks, visibility_limit);

    let mut gate_reason = compute_gate_reason(
        stream_state,
        active_blocker_task.as_ref(),
        active_review_task.as_ref(),
        &validation_state,
        validation_record.as_ref(),
        &validation_tasks,
        merge_conflict,
        branch_advanced,
    );
    let queue_gate = compute_queue_gate(
        &queued_tasks
            .iter()
            .filter(|task| classify_stream_task(task, tasks_by_id) == "product")
            .cloned()
            .collect::<Vec<_>>(),
        tasks_by_id,
        active_blocker_task.as_ref(),
        active_review_task.as_ref(),
        &validation_state,
        validation_record.as_ref(),
        &validation_tasks,
        code_state,
        &gate_reason,
        merge_conflict,
    );
    let queue_items = compute_queue_items(
        state,
        &queued_tasks
            .iter()
            .filter(|task| classify_stream_task(task, tasks_by_id) == "product")
            .cloned()
            .collect::<Vec<_>>(),
        &queue_gate,
        active_review_task.as_ref(),
        foreground_task.as_ref(),
    );
    let queue_counts = queue_state_counts(&queue_items);
    let ready_to_resume_item = queue_items
        .iter()
        .find(|item| item.get("queue_state").and_then(Value::as_str) == Some("ready_to_resume"));
    if let Some(reason) = queue_gate.get("reason").and_then(Value::as_str) {
        if !reason.is_empty() {
            gate_reason = reason.to_string();
        }
    }
    let recommended_next_action = recommended_next_action(
        stream_state,
        &validation_state,
        &validation_tasks,
        merge_conflict,
        !open_product_tasks.is_empty() || !queued_tasks.is_empty(),
    );
    let latest_reviewed_commit_sha = latest_review_boundary
        .as_ref()
        .and_then(boundary_commit_sha)
        .unwrap_or_default();
    let last_activity_at = latest_activity_iso(
        &stream_tasks,
        &agents
            .iter()
            .filter(|agent| {
                stream_identity_for_agent(agent)
                    == Some((repo_root.to_string(), branch.to_string()))
            })
            .cloned()
            .collect::<Vec<_>>(),
    );
    let stream_presence = classify_stream_presence(
        repo_root,
        branch,
        &stream_tasks,
        &open_non_visibility,
        merged,
    );

    Some(json!({
        "stream_id": stream_id(repo_root, branch),
        "group": owner_agent.as_ref().map(|agent| agent.group.clone()).unwrap_or_else(|| stream_group(&stream_tasks)),
        "repo_root": repo_root,
        "branch": branch,
        "stream_presence": stream_presence,
        "branch_exists_locally": branch_exists_locally(repo_root, branch),
        "agent_id": owner_agent.as_ref().map(|agent| agent.id.clone()).unwrap_or_default(),
        "agent_name": owner_agent.as_ref().map(|agent| agent.name.clone()).unwrap_or_default(),
        "agent_slug": owner_agent.as_ref().map(|agent| agent.slug.clone()).unwrap_or_default(),
        "foreground_task_id": foreground_task.as_ref().map(|task| task.id.clone()).unwrap_or_default(),
        "foreground_task_title": foreground_task.as_ref().map(|task| task.task.clone()).unwrap_or_default(),
        "product_task_ids": product_tasks.iter().map(|task| Value::String(task.id.clone())).collect::<Vec<_>>(),
        "workflow_task_ids": workflow_tasks.iter().map(|task| Value::String(task.id.clone())).collect::<Vec<_>>(),
        "queued_task_ids": queued_tasks.iter().map(|task| Value::String(task.id.clone())).collect::<Vec<_>>(),
        "started_task_ids": started_tasks.iter().map(|task| Value::String(task.id.clone())).collect::<Vec<_>>(),
        "recent_visibility_items": visibility_items,
        "latest_boundary_task_id": latest_boundary.as_ref().map(|task| task.id.clone()).unwrap_or_default(),
        "latest_boundary_task_title": latest_boundary.as_ref().map(|task| task.task.clone()).unwrap_or_default(),
        "latest_boundary_recorded_at": latest_boundary.as_ref().and_then(boundary_recorded_at).unwrap_or_default(),
        "latest_boundary_status": latest_boundary.as_ref().and_then(boundary_status).unwrap_or_default(),
        "latest_reviewed_commit_sha": latest_reviewed_commit_sha,
        "active_review_task_id": active_review_task.as_ref().map(|task| task.id.clone()).unwrap_or_default(),
        "active_blocker_task_id": active_blocker_task.as_ref().map(|task| task.id.clone()).unwrap_or_default(),
        "state": stream_state,
        "code_state": code_state,
        "validation_state": validation_state,
        "merge_state": merge_state,
        "queue_gate": queue_gate,
        "queue_items": queue_items,
        "queue_counts": queue_counts,
        "ready_to_resume_task_id": ready_to_resume_item
            .and_then(|item| item.get("task_id"))
            .and_then(Value::as_str)
            .unwrap_or(""),
        "ready_to_resume_task_title": ready_to_resume_item
            .and_then(|item| item.get("task_title"))
            .and_then(Value::as_str)
            .unwrap_or(""),
        "can_auto_resume": ready_to_resume_item
            .and_then(|item| item.get("deps_met"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
        "gate_reason": gate_reason,
        "recommended_next_action": recommended_next_action,
        "branch_advanced": branch_advanced,
        "partial_review_safe": partial_review_safe,
        "last_activity_at": last_activity_at,
        "queued_followups": queued_followers.iter().map(task_list_entry).collect::<Vec<_>>(),
        "started_followups": started_followers.iter().map(task_list_entry).collect::<Vec<_>>(),
    }))
}

fn task_matches_stream(
    task: &BoardTask,
    agents: &[AgentCell],
    stream_key: &str,
    tasks_by_id: &HashMap<String, BoardTask>,
) -> bool {
    if let Some((repo_root, branch)) = task_boundary_identity(task) {
        return branch_key(&repo_root, &branch) == stream_key;
    }
    if !task.resume_after_boundary_task_id.is_empty() {
        if let Some(boundary) = tasks_by_id.get(&task.resume_after_boundary_task_id) {
            if let Some((repo_root, branch)) = task_boundary_identity(boundary) {
                if branch_key(&repo_root, &branch) == stream_key {
                    return true;
                }
            }
        }
    }
    for agent_id in [&task.agent_id, &task.reply_agent_id] {
        if agent_id.is_empty() {
            continue;
        }
        if let Some(agent) = agents.iter().find(|agent| agent.id == *agent_id) {
            if let Some((repo_root, branch)) = stream_identity_for_agent(agent) {
                if branch_key(&repo_root, &branch) == stream_key {
                    return true;
                }
            }
        }
    }
    false
}

fn task_boundary_identity(task: &BoardTask) -> Option<(String, String)> {
    let repo_root = task
        .worktree_boundary
        .get("repo_root")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let branch = task
        .worktree_boundary
        .get("branch")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if repo_root.is_empty() || branch.is_empty() {
        None
    } else {
        Some((repo_root, branch))
    }
}

fn boundary_status(task: &BoardTask) -> Option<String> {
    task.worktree_boundary
        .get("status")
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn boundary_recorded_at(task: &BoardTask) -> Option<String> {
    task.worktree_boundary
        .get("recorded_at")
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn boundary_commit_sha(task: &BoardTask) -> Option<String> {
    task.worktree_boundary
        .get("commit_sha")
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn classify_stream_presence(
    repo_root: &str,
    branch: &str,
    stream_tasks: &[BoardTask],
    open_non_visibility_tasks: &[BoardTask],
    merged: bool,
) -> &'static str {
    if merged {
        return "dormant";
    }
    if !open_non_visibility_tasks.is_empty() {
        return "live";
    }
    if branch_exists_locally(repo_root, branch) || !stream_tasks.is_empty() {
        return "dormant";
    }
    "orphaned"
}

fn classify_stream_task(
    task: &BoardTask,
    tasks_by_id: &HashMap<String, BoardTask>,
) -> &'static str {
    if is_visibility_task(task) {
        return "visibility";
    }
    if is_workflow_task(task, tasks_by_id) {
        return "workflow";
    }
    "product"
}

fn is_visibility_task(task: &BoardTask) -> bool {
    let labels = task
        .labels
        .iter()
        .map(|label| label.to_lowercase())
        .collect::<HashSet<_>>();
    !labels.is_disjoint(
        &VISIBILITY_LABELS
            .iter()
            .map(|label| (*label).to_string())
            .collect(),
    )
}

fn task_text(task: &BoardTask) -> String {
    format!(
        "{} {} {}",
        task.task.to_lowercase(),
        task.description.to_lowercase(),
        task.status.to_lowercase()
    )
}

fn is_workflow_task(task: &BoardTask, tasks_by_id: &HashMap<String, BoardTask>) -> bool {
    let labels = task
        .labels
        .iter()
        .map(|label| label.to_lowercase())
        .collect::<HashSet<_>>();
    if !labels.is_disjoint(
        &WORKFLOW_LABELS
            .iter()
            .map(|label| (*label).to_string())
            .collect(),
    ) {
        return true;
    }
    let action = task.action_name.to_lowercase();
    if PRODUCT_ACTIONS.contains(&action.as_str()) {
        return false;
    }
    if NON_PRODUCT_ACTION_HINTS
        .iter()
        .any(|hint| action.contains(hint))
    {
        return true;
    }
    let text = task_text(task);
    if REVIEW_HINTS
        .iter()
        .chain(VALIDATION_HINTS)
        .chain(MERGE_CONFLICT_HINTS)
        .any(|hint| text.contains(hint))
    {
        return true;
    }
    if !task.parent_task_id.is_empty() {
        if let Some(parent) = tasks_by_id.get(&task.parent_task_id) {
            return is_review_task(parent) || is_validation_task(parent);
        }
    }
    false
}

fn is_review_task(task: &BoardTask) -> bool {
    if is_visibility_task(task) {
        return false;
    }
    let action = task.action_name.to_lowercase();
    action.contains("review")
        || REVIEW_HINTS
            .iter()
            .any(|hint| task_text(task).contains(hint))
}

fn is_validation_task(task: &BoardTask) -> bool {
    if is_visibility_task(task) {
        return false;
    }
    let action = task.action_name.to_lowercase();
    action.contains("validate")
        || action.contains("validation")
        || VALIDATION_HINTS
            .iter()
            .any(|hint| task_text(task).contains(hint))
}

fn is_merge_conflict_task(task: &BoardTask) -> bool {
    !is_visibility_task(task)
        && MERGE_CONFLICT_HINTS
            .iter()
            .any(|hint| task_text(task).contains(hint))
}

fn has_review_ancestor(task: &BoardTask, tasks_by_id: &HashMap<String, BoardTask>) -> bool {
    let mut current_id = task.parent_task_id.clone();
    while !current_id.is_empty() {
        let Some(current) = tasks_by_id.get(&current_id) else {
            return false;
        };
        if is_review_task(current) {
            return true;
        }
        current_id = current.parent_task_id.clone();
    }
    false
}

fn is_blocker_task(task: &BoardTask, tasks_by_id: &HashMap<String, BoardTask>) -> bool {
    if is_visibility_task(task) || is_review_task(task) || is_validation_task(task) {
        return false;
    }
    if is_merge_conflict_task(task) {
        return true;
    }
    if has_review_ancestor(task, tasks_by_id) {
        return true;
    }
    task.status.to_lowercase() == "fixing"
}

fn select_foreground_task(
    state: &MatrixState,
    repo_root: &str,
    branch: &str,
    stream_tasks: &[BoardTask],
    started_tasks: &[BoardTask],
    blocker_task: Option<&BoardTask>,
    review_task: Option<&BoardTask>,
) -> Option<BoardTask> {
    if let Some(task) = blocker_task {
        if !QUEUED_LANES.contains(&task.lane.as_str()) {
            return Some(task.clone());
        }
    }
    if let Some(task) = review_task {
        if !QUEUED_LANES.contains(&task.lane.as_str()) {
            return Some(task.clone());
        }
    }
    for agent in state.agents.values() {
        if agent.cell_type != "agent" {
            continue;
        }
        if stream_identity_for_agent(agent) != Some((repo_root.to_string(), branch.to_string())) {
            continue;
        }
        if let Some(current) = state.agent_current_task(&agent.id) {
            if stream_tasks.iter().any(|task| task.id == current.id) && !is_visibility_task(current)
            {
                return Some(current.clone());
            }
        }
    }
    started_tasks.first().cloned()
}

fn select_owner_agent(
    state: &MatrixState,
    agents: &[AgentCell],
    repo_root: &str,
    branch: &str,
    foreground_task: Option<&BoardTask>,
    stream_tasks: &[BoardTask],
) -> Option<AgentCell> {
    if let Some(task) = foreground_task {
        if let Some(agent) = state.agents.get(&task.agent_id) {
            if stream_identity_for_agent(agent) == Some((repo_root.to_string(), branch.to_string()))
            {
                return Some(agent.clone());
            }
        }
    }
    let stream_task_ids = stream_tasks
        .iter()
        .map(|task| task.id.as_str())
        .collect::<HashSet<_>>();
    let mut candidates = agents
        .iter()
        .filter(|agent| {
            stream_identity_for_agent(agent) == Some((repo_root.to_string(), branch.to_string()))
        })
        .cloned()
        .collect::<Vec<_>>();
    candidates.sort_by(|a, b| {
        let a_current = state
            .agent_current_task(&a.id)
            .map(|task| stream_task_ids.contains(task.id.as_str()))
            .unwrap_or(false);
        let b_current = state
            .agent_current_task(&b.id)
            .map(|task| stream_task_ids.contains(task.id.as_str()))
            .unwrap_or(false);
        b_current
            .cmp(&a_current)
            .then_with(|| (b.last_event_at as i64).cmp(&(a.last_event_at as i64)))
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
            .then_with(|| a.id.cmp(&b.id))
    });
    candidates.into_iter().next()
}

fn task_queue_state_counts(items: &[Value]) -> BTreeMap<String, i64> {
    let mut counts = BTreeMap::new();
    for item in items {
        if let Some(state) = item.get("queue_state").and_then(Value::as_str) {
            *counts.entry(state.to_string()).or_insert(0) += 1;
        }
    }
    counts
}

fn queue_state_counts(items: &[Value]) -> Value {
    Value::Object(
        task_queue_state_counts(items)
            .into_iter()
            .map(|(k, v)| (k, Value::Number(v.into())))
            .collect(),
    )
}

fn queued_successor_tasks(tasks: &[BoardTask], boundary_task_id: &str) -> Vec<BoardTask> {
    sorted_tasks(
        tasks
            .iter()
            .filter(|task| {
                task.resume_after_boundary_task_id == boundary_task_id
                    && QUEUED_LANES.contains(&task.lane.as_str())
            })
            .cloned()
            .collect(),
    )
}

fn started_successor_tasks(tasks: &[BoardTask], boundary_task_id: &str) -> Vec<BoardTask> {
    sorted_tasks(
        tasks
            .iter()
            .filter(|task| {
                task.resume_after_boundary_task_id == boundary_task_id
                    && !QUEUED_LANES.contains(&task.lane.as_str())
            })
            .cloned()
            .collect(),
    )
}

fn compute_validation_state(tasks: &[BoardTask]) -> (String, Option<Value>) {
    let mut latest: Option<(f64, Value)> = None;
    for task in tasks {
        let summary = task.verification_summary.clone();
        let has_signal = !task.verification_state.is_empty()
            || !task.verification_notes.is_empty()
            || !summary.is_empty();
        if !has_signal {
            continue;
        }
        let ts = parse_iso(&task.verification_updated_at)
            .or_else(|| task_activity_ts(task))
            .unwrap_or(0.0);
        let record = json!({
            "task_id": task.id,
            "verification_state": task.verification_state,
            "verification_notes": task.verification_notes,
            "summary": summary,
        });
        if latest
            .as_ref()
            .map(|(latest_ts, _)| ts >= *latest_ts)
            .unwrap_or(true)
        {
            latest = Some((ts, record));
        }
    }
    let Some((_ts, record)) = latest else {
        return ("none".into(), None);
    };
    let state = record
        .get("verification_state")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let summary = record.get("summary").and_then(Value::as_object);
    let notes = record
        .get("verification_notes")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_lowercase();
    let pending_text = summary
        .and_then(|summary| summary.get("human_validation_pending"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_lowercase();
    let combined = format!("{pending_text} {notes}");
    let next = if combined.contains("waiv") {
        "waived"
    } else if state == "failed" {
        if !pending_text.is_empty()
            || summary
                .and_then(|summary| summary.get("manual_smoke_done"))
                .and_then(Value::as_bool)
                .unwrap_or(false)
        {
            "pending_human_validation"
        } else {
            "automated_only"
        }
    } else if state == "passed"
        || summary
            .and_then(|summary| summary.get("manual_smoke_done"))
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        "validated"
    } else if state == "pending" || !pending_text.is_empty() {
        "pending_human_validation"
    } else if state == "attempted"
        || summary
            .and_then(|summary| summary.get("tests_run"))
            .is_some()
        || summary
            .and_then(|summary| summary.get("deploy_attempted"))
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        "automated_only"
    } else {
        "none"
    };
    (next.into(), Some(record))
}

fn compute_gate_reason(
    stream_state: &str,
    blocker_task: Option<&BoardTask>,
    review_task: Option<&BoardTask>,
    validation_state: &str,
    validation_record: Option<&Value>,
    validation_tasks: &[BoardTask],
    merge_conflict: bool,
    branch_advanced: bool,
) -> String {
    if stream_state == "merged" {
        return String::new();
    }
    if merge_conflict {
        return blocker_task
            .map(|task| task.task.clone())
            .unwrap_or_else(|| "Merge conflict must be resolved".into());
    }
    if let Some(task) = blocker_task {
        return task.task.clone();
    }
    if let Some(task) = review_task {
        return task.task.clone();
    }
    if validation_state == "pending_human_validation" {
        if let Some(record) = validation_record {
            if let Some(text) = record
                .get("summary")
                .and_then(Value::as_object)
                .and_then(|summary| summary.get("human_validation_pending"))
                .and_then(Value::as_str)
            {
                if !text.trim().is_empty() {
                    return text.to_string();
                }
            }
            if let Some(text) = record.get("verification_notes").and_then(Value::as_str) {
                if !text.trim().is_empty() {
                    return text.to_string();
                }
            }
        }
        if let Some(task) = validation_tasks.first() {
            return task.task.clone();
        }
        return "Human or runtime validation is still pending".into();
    }
    if branch_advanced {
        return "The branch has advanced beyond the latest reviewed boundary; the reviewed commit is no longer current.".into();
    }
    String::new()
}

fn compute_queue_gate(
    queue_tasks: &[BoardTask],
    tasks_by_id: &HashMap<String, BoardTask>,
    blocker_task: Option<&BoardTask>,
    review_task: Option<&BoardTask>,
    validation_state: &str,
    validation_record: Option<&Value>,
    validation_tasks: &[BoardTask],
    code_state: &str,
    gate_reason: &str,
    merge_conflict: bool,
) -> Value {
    if merge_conflict {
        if let Some(blocker_task) = blocker_task {
            return json!({
                "gate_type": "merge_conflict",
                "blocking_task_id": blocker_task.id,
                "source_task_id": nearest_review_ancestor_id(blocker_task, tasks_by_id).or_else(|| review_task.map(|task| task.id.clone())).unwrap_or_default(),
                "reason": if gate_reason.is_empty() { blocker_task.task.clone() } else { gate_reason.to_string() },
                "clears_when": "conflict_resolved",
            });
        }
    }
    if let Some(blocker_task) = blocker_task {
        return json!({
            "gate_type": "review_blocker",
            "blocking_task_id": blocker_task.id,
            "source_task_id": nearest_review_ancestor_id(blocker_task, tasks_by_id).or_else(|| review_task.map(|task| task.id.clone())).unwrap_or_default(),
            "reason": if gate_reason.is_empty() { blocker_task.task.clone() } else { gate_reason.to_string() },
            "clears_when": "review_passes",
        });
    }
    if validation_state == "pending_human_validation" && code_state == "reviewed_clean" {
        let blocking_task_id = validation_tasks
            .first()
            .map(|task| task.id.clone())
            .or_else(|| {
                validation_record.and_then(|record| {
                    record
                        .get("task_id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
            })
            .unwrap_or_default();
        return json!({
            "gate_type": "human_validation",
            "blocking_task_id": blocking_task_id,
            "source_task_id": validation_record
                .and_then(|record| record.get("task_id"))
                .and_then(Value::as_str)
                .unwrap_or(""),
            "reason": if gate_reason.is_empty() { "Human validation must complete before queued work resumes" } else { gate_reason },
            "clears_when": "human_validation_cleared",
        });
    }
    if let Some(held) = queue_tasks.first().filter(|task| is_held_task(task)) {
        return json!({
            "gate_type": "manual_hold",
            "blocking_task_id": held.id,
            "source_task_id": held.id,
            "reason": held.task,
            "clears_when": "manual_hold_released",
        });
    }
    Value::Object(Default::default())
}

fn compute_queue_items(
    state: &MatrixState,
    queue_tasks: &[BoardTask],
    queue_gate: &Value,
    review_task: Option<&BoardTask>,
    active_mutable_task: Option<&BoardTask>,
) -> Vec<Value> {
    let gate_type = queue_gate
        .get("gate_type")
        .and_then(Value::as_str)
        .unwrap_or("");
    let held_task_id = queue_gate
        .get("blocking_task_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    queue_tasks
        .iter()
        .enumerate()
        .map(|(index, task)| {
            let deps_met = state.board_deps_met(task);
            let queue_state = if matches!(gate_type, "review_blocker" | "merge_conflict") {
                "paused_by_blocker"
            } else if gate_type == "human_validation" {
                "paused_by_validation"
            } else if gate_type == "manual_hold" && task.id == held_task_id {
                "held"
            } else if review_task.is_some() {
                "paused_by_review"
            } else if active_mutable_task.is_some() {
                "queued"
            } else if index == 0 && deps_met {
                "ready_to_resume"
            } else {
                "queued"
            };
            json!({
                "task_id": task.id,
                "task_title": task.task,
                "lane": task.lane,
                "queue_state": queue_state,
                "deps_met": deps_met,
                "resume_after_boundary_task_id": task.resume_after_boundary_task_id,
                "held": is_held_task(task),
            })
        })
        .collect()
}

fn nearest_review_ancestor_id(
    task: &BoardTask,
    tasks_by_id: &HashMap<String, BoardTask>,
) -> Option<String> {
    let mut current_id = task.id.clone();
    loop {
        let current = tasks_by_id.get(&current_id)?;
        if is_review_task(current) {
            return Some(current.id.clone());
        }
        if current.parent_task_id.is_empty() {
            return None;
        }
        current_id = current.parent_task_id.clone();
    }
}

fn recommended_next_action(
    stream_state: &str,
    validation_state: &str,
    validation_tasks: &[BoardTask],
    merge_conflict: bool,
    has_open_product_tasks: bool,
) -> &'static str {
    if stream_state == "merged" {
        "none"
    } else if merge_conflict {
        "resolve_merge_conflict"
    } else if stream_state == "fixing_blockers" {
        "address_review_blockers"
    } else if stream_state == "reviewing" {
        "wait_for_review"
    } else if validation_state == "pending_human_validation" {
        if validation_tasks.is_empty() {
            "merge_after_validation"
        } else {
            "run_manual_validation"
        }
    } else if stream_state == "ready_to_merge" {
        "merge"
    } else if has_open_product_tasks {
        "continue_implementation"
    } else {
        "continue_implementation"
    }
}

fn recent_visibility_items(tasks: &[BoardTask], limit: usize) -> Vec<Value> {
    let mut items = Vec::<(f64, Value)>::new();
    for task in tasks {
        let mut task_items = Vec::<(f64, Value)>::new();
        for message in &task.messages {
            let Some(action) = message.get("action").and_then(Value::as_str) else {
                continue;
            };
            if action != "weaver_message" && action != "reply" {
                continue;
            }
            let ts = message
                .get("timestamp")
                .and_then(Value::as_f64)
                .or_else(|| {
                    message
                        .get("timestamp")
                        .and_then(Value::as_str)
                        .and_then(parse_iso)
                })
                .unwrap_or(0.0);
            let summary = summarize_visibility_message(
                message.get("message").and_then(Value::as_str).unwrap_or(""),
                72,
            );
            task_items.push((
                ts,
                json!({
                    "kind": if action == "reply" { "agent_reply" } else { "weaver_message" },
                    "task_id": task.id,
                    "task_title": task.task,
                    "summary": summary,
                    "timestamp": if ts > 0.0 { chrono::DateTime::<Utc>::from_timestamp(ts as i64, 0).map(|dt| Value::String(dt.to_rfc3339())).unwrap_or_else(|| Value::String(String::new())) } else { Value::String(String::new()) },
                }),
            ));
        }
        if task_items.is_empty() {
            let ts = parse_iso(&task.created_at).unwrap_or(0.0);
            task_items.push((
                ts,
                json!({
                    "kind": "weaver_message",
                    "task_id": task.id,
                    "task_title": task.task,
                    "summary": summarize_visibility_message(if task.description.is_empty() { &task.task } else { &task.description }, 72),
                    "timestamp": task.created_at,
                }),
            ));
        }
        items.extend(task_items);
    }
    items.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    items
        .into_iter()
        .take(limit)
        .map(|(_, item)| item)
        .collect()
}

fn summarize_visibility_message(message: &str, limit: usize) -> String {
    let summary = message
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or(message)
        .trim();
    if summary.len() <= limit {
        summary.to_string()
    } else {
        format!("{}…", summary[..limit.saturating_sub(1)].trim_end())
    }
}

fn is_merged_stream(
    boundary_tasks: &[BoardTask],
    agents: &[AgentCell],
    repo_root: &str,
    branch: &str,
) -> bool {
    boundary_tasks
        .last()
        .and_then(boundary_status)
        .map(|status| status == "merged")
        .unwrap_or(false)
        || agents.iter().any(|agent| {
            stream_identity_for_agent(agent) == Some((repo_root.to_string(), branch.to_string()))
                && agent.worktree_merged
        })
}

fn stream_group(stream_tasks: &[BoardTask]) -> String {
    let mut counts = BTreeMap::<String, i64>::new();
    for task in stream_tasks {
        if !task.group.is_empty() {
            *counts.entry(task.group.clone()).or_insert(0) += 1;
        }
    }
    counts
        .into_iter()
        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| b.0.cmp(&a.0)))
        .map(|(group, _)| group)
        .unwrap_or_default()
}

fn task_activity_ts(task: &BoardTask) -> Option<f64> {
    let mut values = Vec::new();
    if let Some(ts) = parse_iso(&task.updated_at) {
        values.push(ts);
    }
    if let Some(ts) = parse_iso(&task.created_at) {
        values.push(ts);
    }
    for msg in &task.messages {
        if let Some(ts) = msg.get("timestamp").and_then(Value::as_f64) {
            values.push(ts);
        } else if let Some(ts) = msg
            .get("timestamp")
            .and_then(Value::as_str)
            .and_then(parse_iso)
        {
            values.push(ts);
        }
    }
    values.into_iter().reduce(f64::max)
}

fn latest_activity_iso(tasks: &[BoardTask], agents: &[AgentCell]) -> String {
    let mut ts = tasks
        .iter()
        .filter_map(task_activity_ts)
        .collect::<Vec<_>>();
    ts.extend(
        agents
            .iter()
            .filter_map(|agent| (agent.last_event_at > 0.0).then_some(agent.last_event_at)),
    );
    ts.into_iter()
        .reduce(f64::max)
        .and_then(|ts| {
            let secs = ts as i64;
            chrono::DateTime::<chrono::Utc>::from_timestamp(secs, 0)
        })
        .map(|dt| dt.to_rfc3339())
        .unwrap_or_default()
}

fn sorted_tasks(mut tasks: Vec<BoardTask>) -> Vec<BoardTask> {
    tasks.sort_by(|a, b| {
        task_canonical_order(a)
            .cmp(&task_canonical_order(b))
            .then_with(|| a.created_at.cmp(&b.created_at))
            .then_with(|| a.id.cmp(&b.id))
    });
    tasks
}

fn sorted_open_tasks(mut tasks: Vec<BoardTask>) -> Vec<BoardTask> {
    tasks.sort_by(|a, b| {
        lane_priority(&a.lane)
            .cmp(&lane_priority(&b.lane))
            .then_with(|| {
                task_activity_ts(b)
                    .partial_cmp(&task_activity_ts(a))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| task_canonical_order(a).cmp(&task_canonical_order(b)))
            .then_with(|| a.id.cmp(&b.id))
    });
    tasks
}

fn task_canonical_order(task: &BoardTask) -> (u64, u64, String) {
    if let Some(parsed) = loom_core::task_ids::parse_task_id(&task.id) {
        (
            parsed.trunk,
            parsed.derived_suffix.len() as u64,
            task.id.clone(),
        )
    } else {
        (u64::MAX, u64::MAX, task.id.clone())
    }
}

fn lane_priority(lane: &str) -> i32 {
    match lane {
        "In Progress" => 0,
        "To Do" => 1,
        "Backlog" => 2,
        _ => 3,
    }
}

fn is_held_task(task: &BoardTask) -> bool {
    task.labels
        .iter()
        .map(|label| label.to_lowercase())
        .any(|label| HELD_LABELS.contains(&label.as_str()))
}

fn task_list_entry(task: &BoardTask) -> Value {
    json!({
        "id": task.id,
        "title": task.task,
        "lane": task.lane,
    })
}

fn branch_exists_locally(repo_root: &str, branch: &str) -> bool {
    if repo_root.trim().is_empty() || branch.trim().is_empty() {
        return false;
    }
    if !Path::new(repo_root).exists() {
        return false;
    }
    Command::new("git")
        .args([
            "-C",
            repo_root,
            "show-ref",
            "--verify",
            "--quiet",
            &format!("refs/heads/{branch}"),
        ])
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stream_members_include_primary_fields() {
        let stream = json!({
            "product_task_ids": ["a"],
            "workflow_task_ids": ["b"],
            "foreground_task_id": "c",
            "queue_gate": {"blocking_task_id": "d"},
        });
        let ids = member_task_ids_for_stream(&stream);
        assert!(ids.contains("a"));
        assert!(ids.contains("b"));
        assert!(ids.contains("c"));
        assert!(ids.contains("d"));
    }
}
