//! Weaver board/session-map/streams commands.

use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap, HashSet};

use chrono::Utc;
use serde_json::{json, Map, Value};

use loom_core::delta::DeltaOp;
use loom_core::state::{board_task_is_closed, AgentCell, BoardTask, MatrixState, ARCHIVED_LANE};
use loom_weaver::hints::compute_weaver_hints;
use loom_weaver::session_map::build_weaver_session_map;
use loom_weaver::streams::{compute_worktree_streams, stream_identity_for_agent};
use loom_weaver::task_health::{compute_task_health, TaskHealthSnapshot};

use super::{flush, optional_str, required_str, CmdContext, CmdError, CmdResult};

const DEFAULT_LIMIT: usize = 10;
const MAX_JOURNAL_ENTRIES: usize = 200;

pub async fn board_summary(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();
    let summary = {
        let st = ctx.state.lock().await;
        build_board_summary(&st, &group, some_if_not_empty(&weaver_id))
    };
    Ok(summary)
}

pub async fn streams_list(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let state_filter = optional_str(req, "state").unwrap_or("").trim().to_string();
    let branch_filter = optional_str(req, "branch").unwrap_or("").trim().to_string();
    let repo_root_filter = optional_str(req, "repo_root")
        .unwrap_or("")
        .trim()
        .to_string();
    let include_orphaned = req
        .get("include_orphaned")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let include_merged = req
        .get("include_merged")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();

    let streams = {
        let st = ctx.state.lock().await;
        filtered_streams(
            &st,
            &group,
            some_if_not_empty(&weaver_id),
            &state_filter,
            &branch_filter,
            &repo_root_filter,
            include_orphaned,
            include_merged,
        )
    };
    Ok(json!({
        "group": group,
        "count": streams.len(),
        "streams": streams,
    }))
}

pub async fn stream_show(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let stream_ident = optional_str(req, "stream").unwrap_or("").trim().to_string();
    let repo_root = optional_str(req, "repo_root")
        .unwrap_or("")
        .trim()
        .to_string();
    let branch = optional_str(req, "branch").unwrap_or("").trim().to_string();
    let task_ident = optional_str(req, "task").unwrap_or("").trim().to_string();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();

    let stream = {
        let st = ctx.state.lock().await;
        let mut task_id = String::new();
        if !task_ident.is_empty() {
            task_id = st
                .resolve_task_ident(&task_ident)
                .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?;
            let task = st
                .board_tasks
                .get(&task_id)
                .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?;
            if task.group != group {
                return Err(CmdError::BadRequest("Task not found".into()));
            }
        }
        let streams = filtered_streams(
            &st,
            &group,
            some_if_not_empty(&weaver_id),
            "",
            "",
            "",
            true,
            true,
        );
        resolve_stream_payload(&streams, &stream_ident, &repo_root, &branch, &task_id)
            .ok_or_else(|| CmdError::BadRequest("Stream not found".into()))?
    };
    Ok(stream)
}

pub async fn board_list(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let lane_filter = optional_str(req, "lane").unwrap_or("").trim().to_string();
    let label_filter = optional_str(req, "label").unwrap_or("").trim().to_string();
    let health_filter = optional_str(req, "health").unwrap_or("").trim().to_string();
    let search = optional_str(req, "search").unwrap_or("").to_lowercase();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();

    let lanes = {
        let st = ctx.state.lock().await;
        let health = compute_task_health(&st.board_tasks, &st.agents, None);
        build_board_list(
            &st,
            &group,
            some_if_not_empty(&weaver_id),
            &health,
            &lane_filter,
            &label_filter,
            &health_filter,
            &search,
        )
    };
    Ok(json!({ "lanes": lanes }))
}

pub async fn task_show(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let task_ident = required_str(req, "task")?.to_string();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();

    let payload = {
        let st = ctx.state.lock().await;
        let task_id = st
            .resolve_task_ident(&task_ident)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?;
        let task = st
            .board_tasks
            .get(&task_id)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?;
        if task.group != group {
            return Err(CmdError::BadRequest("Task not found".into()));
        }
        let health = compute_task_health(&st.board_tasks, &st.agents, None);
        build_task_show(&st, task, some_if_not_empty(&weaver_id), &health)
    };
    Ok(payload)
}

pub async fn agents_list(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();
    let agents = {
        let st = ctx.state.lock().await;
        build_agents_list(&st, &group, some_if_not_empty(&weaver_id))
    };
    Ok(json!({ "agents": agents }))
}

pub async fn agent_show(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let agent_ident = required_str(req, "agent")?.to_string();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();

    let payload = {
        let st = ctx.state.lock().await;
        let agent_id =
            resolve_visible_agent_id(&st, &group, some_if_not_empty(&weaver_id), &agent_ident)
                .ok_or_else(|| CmdError::BadRequest(format!("Agent not found: {agent_ident}")))?;
        let agent = st
            .agents
            .get(&agent_id)
            .ok_or_else(|| CmdError::BadRequest(format!("Agent not found: {agent_ident}")))?;
        build_agent_show(&st, &group, agent)
    };
    Ok(payload)
}

pub async fn actions_list(ctx: &CmdContext, req: &Value) -> CmdResult {
    let mut forwarded = req.clone();
    if forwarded.get("group").is_none() {
        if let Some(obj) = forwarded.as_object_mut() {
            obj.insert(
                "group".into(),
                Value::String(required_str(req, "group")?.to_string()),
            );
        }
    }
    crate::commands::actions::list_actions(ctx, &forwarded).await
}

pub async fn action_show(ctx: &CmdContext, req: &Value) -> CmdResult {
    let mut forwarded = req.clone();
    if forwarded.get("group").is_none() {
        if let Some(obj) = forwarded.as_object_mut() {
            obj.insert(
                "group".into(),
                Value::String(required_str(req, "group")?.to_string()),
            );
        }
    }
    crate::commands::actions::get_action(ctx, &forwarded).await
}

pub async fn task_create(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let title = required_str(req, "title")?.to_string();
    let mut payload = json!({
        "task": title,
        "group": group,
        "description": optional_str(req, "description").unwrap_or(""),
        "lane": optional_str(req, "lane").unwrap_or(""),
        "action_name": optional_str(req, "action").unwrap_or(""),
        "action_vars": req.get("action_vars").cloned().unwrap_or_else(|| json!({})),
        "labels": req.get("labels").cloned().unwrap_or_else(|| json!([])),
        "verification_mode": optional_str(req, "verification_mode").unwrap_or(""),
        "verification_state": optional_str(req, "verification_state").unwrap_or(""),
        "verification_notes": optional_str(req, "verification_notes").unwrap_or(""),
        "verification_summary": req
            .get("verification_summary")
            .cloned()
            .unwrap_or_else(|| json!({})),
    });
    if let Some(obj) = payload.as_object_mut() {
        obj.retain(|_, value| match value {
            Value::String(s) => !s.is_empty(),
            Value::Array(a) => !a.is_empty(),
            Value::Object(o) => !o.is_empty(),
            _ => true,
        });
    }
    crate::commands::board::add_task(ctx, &payload).await
}

pub async fn task_edit(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let task_ident = required_str(req, "task")?;
    let task_id = {
        let st = ctx.state.lock().await;
        resolve_group_task_id(&st, &group, task_ident)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?
    };

    let mut fields = Map::new();
    if let Some(value) = req.get("title") {
        fields.insert("task".into(), value.clone());
    }
    if let Some(value) = req.get("description") {
        fields.insert("description".into(), value.clone());
    }
    if let Some(value) = req.get("labels") {
        fields.insert("labels".into(), value.clone());
    }
    if let Some(value) = req.get("action") {
        fields.insert("action_name".into(), value.clone());
    }
    if let Some(value) = req.get("action_vars") {
        fields.insert("action_vars".into(), value.clone());
    }
    for key in [
        "verification_mode",
        "verification_state",
        "verification_notes",
        "verification_summary",
    ] {
        if let Some(value) = req.get(key) {
            fields.insert(key.to_string(), value.clone());
        }
    }

    crate::commands::board::update_task(
        ctx,
        &json!({
            "id": task_id,
            "fields": fields,
        }),
    )
    .await
}

pub async fn task_verify(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let task_ident = required_str(req, "task")?;
    let task_id = {
        let st = ctx.state.lock().await;
        resolve_group_task_id(&st, &group, task_ident)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?
    };
    let actor_name = {
        let st = ctx.state.lock().await;
        let weaver_id = optional_str(req, "_weaver_id").unwrap_or("");
        st.agents
            .get(weaver_id)
            .map(agent_display_name)
            .unwrap_or_else(|| "weaver".into())
    };

    crate::commands::board::verify_task(
        ctx,
        &json!({
            "id": task_id,
            "state": optional_str(req, "state").unwrap_or(""),
            "mode": optional_str(req, "mode").unwrap_or(""),
            "notes": optional_str(req, "notes").unwrap_or(""),
            "updated_by": format!("weaver:{actor_name}"),
        }),
    )
    .await
}

pub async fn task_move(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let task_ident = required_str(req, "task")?;
    let task_id = {
        let st = ctx.state.lock().await;
        resolve_group_task_id(&st, &group, task_ident)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?
    };
    crate::commands::board::move_task(
        ctx,
        &json!({
            "id": task_id,
            "lane": required_str(req, "lane")?,
        }),
    )
    .await
}

pub async fn task_dispatch(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = required_str(req, "_weaver_id")?.to_string();
    let task_ident = required_str(req, "task")?;
    let task_id = {
        let st = ctx.state.lock().await;
        resolve_group_task_id(&st, &group, task_ident)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?
    };

    let mut payload = json!({
        "task_id": task_id,
        "_weaver_dispatch_group": group,
        "_weaver_dispatch_id": weaver_id,
    });
    if let Some(agent_ident) = optional_str(req, "agent")
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        let agent_id = {
            let st = ctx.state.lock().await;
            resolve_visible_agent_id(&st, &group, Some(&weaver_id), agent_ident)
                .ok_or_else(|| CmdError::BadRequest(format!("Agent not found: {agent_ident}")))?
        };
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("agent_id".into(), Value::String(agent_id));
        }
    } else if let Some(obj) = payload.as_object_mut() {
        obj.insert(
            "_created_by_weaver_id".into(),
            Value::String(weaver_id.clone()),
        );
        let (target_session_id, target_window_id) = {
            let st = ctx.state.lock().await;
            let weaver = st.agents.get(&weaver_id);
            (
                weaver
                    .and_then(|cell| cell.session_id.as_ref())
                    .map(|value| Value::String(value.clone()))
                    .unwrap_or(Value::Null),
                weaver
                    .map(|cell| Value::String(cell.window_id.clone()))
                    .unwrap_or(Value::Null),
            )
        };
        if !target_session_id.is_null() {
            obj.insert("target_session_id".into(), target_session_id);
        }
        if !target_window_id.is_null()
            && target_window_id
                .as_str()
                .is_some_and(|value| !value.is_empty())
        {
            obj.insert("target_window_id".into(), target_window_id);
        }
    }

    for key in ["name", "agent_type", "command", "model", "reasoning_effort"] {
        if let Some(value) = req.get(key).cloned() {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert(key.into(), value);
            }
        }
    }

    crate::commands::dispatch::dispatch_task(ctx, &payload).await
}

pub async fn batch_dispatch(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = required_str(req, "_weaver_id")?.to_string();
    let raw_tasks = req
        .get("tasks")
        .and_then(Value::as_array)
        .cloned()
        .ok_or_else(|| CmdError::BadRequest("tasks must be a non-empty array".into()))?;
    if raw_tasks.is_empty() {
        return Err(CmdError::BadRequest(
            "tasks must be a non-empty array".into(),
        ));
    }

    let max_concurrent = match req.get("max_concurrent").and_then(Value::as_i64) {
        Some(value) if value >= 1 => value as usize,
        Some(_) => {
            return Err(CmdError::BadRequest(
                "max_concurrent must be an integer >= 1".into(),
            ))
        }
        None => {
            let st = ctx.state.lock().await;
            st.get_weaver_settings(&group)
                .default_worker_concurrency
                .max(1) as usize
        }
    };

    let active_before = {
        let st = ctx.state.lock().await;
        active_worker_ids(&st, &group, Some(&weaver_id)).len()
    };
    let mut active_agents = {
        let st = ctx.state.lock().await;
        active_worker_ids(&st, &group, Some(&weaver_id))
    };
    let mut group_agents = HashMap::<String, String>::new();
    let mut seen_task_ids = HashSet::<String>::new();
    let mut results = Vec::<Value>::new();

    for (idx, raw_entry) in raw_tasks.iter().enumerate() {
        let Some(entry) = raw_entry.as_object() else {
            results.push(json!({
                "index": idx,
                "status": "failed",
                "reason": "invalid_entry",
                "message": "Each tasks entry must be an object.",
            }));
            continue;
        };
        let task_ident = entry
            .get("task")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        if task_ident.is_empty() {
            results.push(json!({
                "index": idx,
                "status": "failed",
                "reason": "invalid_entry",
                "message": "Each tasks entry must include a task string.",
            }));
            continue;
        }
        let agent_group = entry
            .get("agent_group")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();

        let task_id = {
            let st = ctx.state.lock().await;
            resolve_group_task_id(&st, &group, &task_ident)
        };
        let Some(task_id) = task_id else {
            results.push(json!({
                "index": idx,
                "task": task_ident,
                "status": "failed",
                "reason": "task_not_found",
                "message": "Task not found.",
            }));
            continue;
        };
        if !seen_task_ids.insert(task_id.clone()) {
            results.push(json!({
                "index": idx,
                "task": task_ident,
                "task_id": task_id,
                "status": "failed",
                "reason": "duplicate_task",
                "message": "Task appears more than once in this batch.",
            }));
            continue;
        }

        let validation_error = {
            let st = ctx.state.lock().await;
            match st.board_tasks.get(&task_id) {
                None => Some(("task_not_found", "Task not found.".to_string())),
                Some(task) if !task.agent_id.trim().is_empty() => Some((
                    "already_assigned",
                    "Task is already linked to an agent.".to_string(),
                )),
                Some(task) if task.lane == ARCHIVED_LANE => {
                    Some(("already_archived", "Task is archived.".to_string()))
                }
                Some(task) if task.lane == "Done" => {
                    Some(("already_done", "Task is already Done.".to_string()))
                }
                Some(task) if task.lane == "In Progress" => Some((
                    "already_in_progress",
                    "Task is already in the dispatch lane.".to_string(),
                )),
                Some(task) if !st.board_deps_met(task) => {
                    let unmet = blocked_dependency_titles(&st, task);
                    Some((
                        "blocked_by_dependencies",
                        format!("Blocked by dependencies: {}", unmet.join(", ")),
                    ))
                }
                Some(_) => None,
            }
        };
        if let Some((reason, message)) = validation_error {
            results.push(json!({
                "index": idx,
                "task": task_ident,
                "task_id": task_id,
                "status": "failed",
                "reason": reason,
                "message": message,
            }));
            continue;
        }

        let target_agent_id = if !agent_group.is_empty() {
            group_agents.get(&agent_group).cloned().unwrap_or_default()
        } else {
            String::new()
        };
        if !target_agent_id.is_empty() && active_agents.contains(&target_agent_id) {
            results.push(json!({
                "index": idx,
                "task": task_ident,
                "task_id": task_id,
                "status": "deferred",
                "reason": "agent_group_busy",
                "message": "The agent_group worker is still busy; dispatch this follow-up after it finishes.",
                "agent_group": agent_group,
                "agent_id": target_agent_id,
            }));
            continue;
        }
        if target_agent_id.is_empty() && active_agents.len() >= max_concurrent {
            results.push(json!({
                "index": idx,
                "task": task_ident,
                "task_id": task_id,
                "status": "deferred",
                "reason": "max_concurrent_reached",
                "message": "Dispatch would exceed max_concurrent for the group.",
                "agent_group": agent_group,
            }));
            continue;
        }

        let mut payload = json!({
            "group": group,
            "_weaver_id": weaver_id,
            "task": task_id,
        });
        if !target_agent_id.is_empty() {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert("agent".into(), Value::String(target_agent_id.clone()));
            }
        }
        let result = task_dispatch(ctx, &payload).await;
        match result {
            Ok(value) => {
                let resolved_agent_id = value
                    .get("agent_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                if !resolved_agent_id.is_empty() {
                    active_agents.insert(resolved_agent_id.clone());
                    if !agent_group.is_empty() {
                        group_agents.insert(agent_group.clone(), resolved_agent_id.clone());
                    }
                }
                results.push(json!({
                    "index": idx,
                    "task": task_ident,
                    "task_id": task_id,
                    "status": "dispatched",
                    "agent_group": agent_group,
                    "agent_id": resolved_agent_id,
                }));
            }
            Err(err) => {
                results.push(json!({
                    "index": idx,
                    "task": task_ident,
                    "task_id": task_id,
                    "status": "failed",
                    "reason": "dispatch_error",
                    "message": match err {
                        CmdError::BadRequest(msg) => msg,
                        CmdError::Engine(engine) => engine.to_string(),
                        CmdError::NotImplemented => "dispatch not implemented".to_string(),
                    },
                }));
            }
        }
    }

    Ok(json!({
        "type": "ok",
        "max_concurrent": max_concurrent,
        "active_before": active_before,
        "active_after": active_agents.len(),
        "results": results,
    }))
}

pub async fn task_resolve(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let task_ident = required_str(req, "task")?;
    let task_id = {
        let st = ctx.state.lock().await;
        resolve_group_task_id(&st, &group, task_ident)
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?
    };
    crate::commands::dispatch::resolve_ask(
        ctx,
        &json!({
            "task_id": task_id,
            "reply": required_str(req, "answer")?,
        }),
    )
    .await
}

pub async fn events(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let since_id = req.get("since_id").and_then(Value::as_u64).unwrap_or(0);
    let limit = req.get("limit").and_then(Value::as_u64).unwrap_or(50) as usize;
    let types = req
        .get("types")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect::<Vec<_>>();

    let events = ctx
        .db
        .load_group_panel_events(&group, since_id as i64, limit, &types)
        .await?;
    let cursor = events
        .last()
        .and_then(|event| event.get("id"))
        .and_then(Value::as_u64)
        .unwrap_or(since_id);
    Ok(json!({
        "events": events,
        "cursor": cursor,
    }))
}

pub async fn journal_append(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let entry_type = req
        .get("entry_type")
        .or_else(|| req.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let entry = required_str(req, "entry")?.to_string();
    let item = append_journal_entry(ctx, &group, &entry_type, &entry).await?;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "id": item.get("id").and_then(Value::as_i64).unwrap_or(0),
    }))
}

pub async fn journal_read(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let tail = req.get("tail").and_then(Value::as_u64).unwrap_or(20) as usize;
    let entry_type = req
        .get("entry_type")
        .or_else(|| req.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let entries = {
        let st = ctx.state.lock().await;
        read_journal_entries(&st, &group, tail, &entry_type)
    };
    Ok(json!({
        "type": "journal",
        "entries": entries,
    }))
}

pub async fn journal_delete(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let entry_id = req
        .get("entry_id")
        .or_else(|| req.get("id"))
        .and_then(Value::as_i64)
        .ok_or_else(|| CmdError::BadRequest("missing 'entry_id'".into()))?;

    ctx.db.delete_weaver_journal_entry(&group, entry_id).await?;
    {
        let mut st = ctx.state.lock().await;
        if let Some(entries) = st.weaver_journal.get_mut(&group) {
            entries.retain(|entry| entry.get("id").and_then(Value::as_i64) != Some(entry_id));
        }
        st.emit(DeltaOp::JournalDelete {
            group: group.clone(),
            id: entry_id,
        });
    }
    flush(ctx).await;
    Ok(json!({ "ok": true }))
}

pub async fn session_map_read(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = optional_str(req, "_weaver_id")
        .unwrap_or("")
        .trim()
        .to_string();
    let session_map = {
        let st = ctx.state.lock().await;
        build_weaver_session_map(&st, &group, some_if_not_empty(&weaver_id))
    };
    Ok(json!({
        "type": "weaver_session_map",
        "group": group,
        "session_map": session_map,
    }))
}

pub async fn update_settings(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let settings = apply_settings_patch(ctx, &group, settings_patch_from_req(req)).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn ask(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let question = required_str(req, "question")?.to_string();
    if question.trim().is_empty() {
        return Err(CmdError::BadRequest("Question is required".into()));
    }
    let settings = apply_settings_patch(
        ctx,
        &group,
        Map::from_iter([
            ("pending_question".into(), Value::String(question.clone())),
            ("paused".into(), Value::Bool(true)),
        ]),
    )
    .await?;
    let _ = append_journal_entry(
        ctx,
        &group,
        "observation",
        &format!("Asked human: {question}"),
    )
    .await?;
    let _ = append_panel_event(ctx, "ask_created", &group, &question, "").await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn note(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let message = required_str(req, "message")?.to_string();
    let kind = optional_str(req, "kind")
        .unwrap_or("note")
        .trim()
        .to_string();
    if message.trim().is_empty() {
        return Err(CmdError::BadRequest("Message is required".into()));
    }
    if !matches!(kind.as_str(), "note" | "question") {
        return Err(CmdError::BadRequest(
            "kind must be 'note' or 'question'".into(),
        ));
    }
    let settings = apply_settings_patch(
        ctx,
        &group,
        Map::from_iter([
            ("pending_note".into(), Value::String(message.clone())),
            ("pending_note_kind".into(), Value::String(kind.clone())),
        ]),
    )
    .await?;
    let _ = append_journal_entry(
        ctx,
        &group,
        "observation",
        &format!("Human note ({kind}): {message}"),
    )
    .await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn agent_message(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = required_str(req, "_weaver_id")?.to_string();
    let agent_ident = required_str(req, "agent")?.to_string();
    let message = required_str(req, "message")?.to_string();
    if message.trim().is_empty() {
        return Err(CmdError::BadRequest("Message is required".into()));
    }
    let agent_id = {
        let st = ctx.state.lock().await;
        resolve_visible_agent_id(&st, &group, Some(&weaver_id), &agent_ident)
            .ok_or_else(|| CmdError::BadRequest(format!("Agent not found: {agent_ident}")))?
    };
    send_weaver_message_to_agent(ctx, &weaver_id, &agent_id, &message).await
}

pub async fn agent_close(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = required_str(req, "_weaver_id")?.to_string();
    let agent_ident = required_str(req, "agent")?.to_string();
    let agent_id = {
        let st = ctx.state.lock().await;
        resolve_visible_agent_id(&st, &group, Some(&weaver_id), &agent_ident)
            .ok_or_else(|| CmdError::BadRequest(format!("Agent not found: {agent_ident}")))?
    };
    crate::commands::agents::remove_agent(ctx, &json!({ "id": agent_id })).await
}

pub async fn agent_relaunch(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let weaver_id = required_str(req, "_weaver_id")?.to_string();
    let agent_ident = required_str(req, "agent")?.to_string();
    let agent_id = {
        let st = ctx.state.lock().await;
        resolve_visible_agent_id(&st, &group, Some(&weaver_id), &agent_ident)
            .ok_or_else(|| CmdError::BadRequest(format!("Agent not found: {agent_ident}")))?
    };
    crate::commands::dispatch::relaunch_agent(ctx, &json!({ "id": agent_id })).await
}

pub async fn dismiss_note(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let settings = apply_settings_patch(
        ctx,
        &group,
        Map::from_iter([
            ("pending_note".into(), Value::String(String::new())),
            ("pending_note_kind".into(), Value::String(String::new())),
        ]),
    )
    .await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn pause(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let settings = apply_settings_patch(
        ctx,
        &group,
        Map::from_iter([("paused".into(), Value::Bool(true))]),
    )
    .await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn resume(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let settings = apply_settings_patch(
        ctx,
        &group,
        Map::from_iter([
            ("paused".into(), Value::Bool(false)),
            ("pending_question".into(), Value::String(String::new())),
        ]),
    )
    .await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn reply(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let answer = required_str(req, "answer")?.to_string();
    if answer.trim().is_empty() {
        return Err(CmdError::BadRequest("Answer is required".into()));
    }
    let weaver_id = {
        let st = ctx.state.lock().await;
        st.get_group_settings(&group).weaver_agent_id
    };
    if weaver_id.trim().is_empty() {
        return Err(CmdError::BadRequest("Weaver is not running".into()));
    }
    crate::commands::dispatch::send_text_to_cell(ctx, &weaver_id, &format_human_reply(&answer))
        .await
        .map_err(|_| CmdError::BadRequest("Weaver is not running".into()))?;
    let settings = apply_settings_patch(
        ctx,
        &group,
        Map::from_iter([
            ("paused".into(), Value::Bool(false)),
            ("pending_question".into(), Value::String(String::new())),
        ]),
    )
    .await?;
    let _ = append_journal_entry(
        ctx,
        &group,
        "observation",
        &format!("Human replied: {answer}"),
    )
    .await?;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "delivered": true,
        "settings": settings,
    }))
}

pub async fn notifications(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let mut patch = Map::new();
    if let Some(preset) = optional_str(req, "preset") {
        for (key, value) in notification_preset(preset)? {
            patch.insert(key, value);
        }
    }
    if let Some(value) = optional_str(req, "digest_verbosity") {
        patch.insert("digest_verbosity".into(), Value::String(value.to_string()));
    }
    if let Some(value) = req.get("push_interval").and_then(Value::as_i64) {
        patch.insert(
            "push_interval".into(),
            Value::Number((value.max(10)).into()),
        );
    }
    if let Some(value) = req.get("max_interval").and_then(Value::as_i64) {
        patch.insert("max_interval".into(), Value::Number((value.max(30)).into()));
    }
    if let Some(value) = req.get("heartbeat_interval").and_then(Value::as_i64) {
        patch.insert(
            "heartbeat_interval".into(),
            Value::Number((if value <= 0 { 0 } else { value.max(30) }).into()),
        );
    }

    let current_enabled = {
        let st = ctx.state.lock().await;
        st.get_weaver_settings(&group).enabled_events
    };
    if req.get("preset").is_some() || req.get("enable").is_some() || req.get("disable").is_some() {
        let mut events = patch
            .get("enabled_events")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_else(|| {
                current_enabled
                    .into_iter()
                    .map(Value::String)
                    .collect::<Vec<_>>()
            })
            .into_iter()
            .filter_map(|value| value.as_str().map(str::to_string))
            .collect::<HashSet<_>>();
        for value in req
            .get("enable")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            if let Some(event) = value.as_str() {
                events.insert(event.to_string());
            }
        }
        for value in req
            .get("disable")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            if let Some(event) = value.as_str() {
                events.remove(event);
            }
        }
        let mut events = events.into_iter().collect::<Vec<_>>();
        events.sort();
        patch.insert(
            "enabled_events".into(),
            Value::Array(events.into_iter().map(Value::String).collect()),
        );
    }

    let settings = apply_settings_patch(ctx, &group, patch).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn launch_settings(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let mut patch = Map::new();
    for (from, to) in [
        ("provider", "weaver_provider"),
        ("command", "weaver_boot_command"),
        ("model", "weaver_model"),
        ("reasoning_effort", "weaver_reasoning_effort"),
    ] {
        if let Some(value) = optional_str(req, from) {
            patch.insert(to.into(), Value::String(value.to_string()));
        }
    }
    let settings = apply_settings_patch(ctx, &group, patch).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "settings": settings }))
}

pub async fn flush_now(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    // Operator asked for "flush now" — bypass the interval gate.
    let _ = ctx.weaver_buffer.force_flush_group(ctx, &group).await;
    Ok(json!({ "ok": true }))
}

fn build_board_summary(st: &MatrixState, group: &str, weaver_id: Option<&str>) -> Value {
    let tasks = st
        .board_tasks
        .values()
        .filter(|task| task.group == group)
        .cloned()
        .collect::<Vec<_>>();
    let archived_tasks = tasks
        .iter()
        .filter(|task| task.lane == ARCHIVED_LANE)
        .cloned()
        .collect::<Vec<_>>();
    let visible_tasks = tasks
        .iter()
        .filter(|task| task.lane != ARCHIVED_LANE)
        .cloned()
        .collect::<Vec<_>>();
    let health = compute_task_health(&st.board_tasks, &st.agents, None);

    let mut lane_counts = BTreeMap::<String, u64>::new();
    let mut extra_lanes = BTreeMap::<String, u64>::new();
    for lane_name in &st.board_lanes {
        if lane_name != ARCHIVED_LANE {
            lane_counts.insert(lane_name.clone(), 0);
        }
    }
    let mut label_counts =
        BTreeMap::from([("deferred".to_string(), 0u64), ("ready".to_string(), 0u64)]);
    let mut health_counts = BTreeMap::from([
        ("blocked".to_string(), 0u64),
        ("healthy".to_string(), 0u64),
        ("idle-risk".to_string(), 0u64),
        ("stale-in-progress".to_string(), 0u64),
        ("stalled".to_string(), 0u64),
        ("thrashing".to_string(), 0u64),
    ]);
    let mut verification_counts = BTreeMap::from([
        ("attempted".to_string(), 0u64),
        ("failed".to_string(), 0u64),
        ("passed".to_string(), 0u64),
        ("pending".to_string(), 0u64),
    ]);
    let mut unhealthy = Vec::new();
    let mut pending_asks = Vec::new();
    let mut verification_items = Vec::new();

    for task in &visible_tasks {
        if let Some(count) = lane_counts.get_mut(&task.lane) {
            *count += 1;
        } else {
            *extra_lanes.entry(task.lane.clone()).or_insert(0) += 1;
        }
        let labels = task.labels.iter().cloned().collect::<HashSet<_>>();
        for label_name in ["ready", "deferred"] {
            if labels.contains(label_name) {
                *label_counts.entry(label_name.to_string()).or_insert(0) += 1;
            }
        }
        let snapshot = health.get(&task.id);
        let health_state = effective_health_state(task, snapshot);
        *health_counts.entry(health_state.clone()).or_insert(0) += 1;
        if !board_task_is_closed(Some(task)) && health_state != "healthy" {
            unhealthy.push(json!({
                "id": task.id,
                "title": task.task,
                "health_state": health_state,
                "health_since": effective_health_since(task, snapshot),
            }));
        }
        let verification_state = task.verification_state.trim();
        if !board_task_is_closed(Some(task)) && verification_counts.contains_key(verification_state)
        {
            *verification_counts
                .entry(verification_state.to_string())
                .or_insert(0) += 1;
            if matches!(verification_state, "pending" | "failed") {
                verification_items.push(json!({
                    "id": task.id,
                    "title": task.task,
                    "verification_state": verification_state,
                    "verification_mode": task.verification_mode,
                    "verification_notes": task.verification_notes,
                }));
            }
        }
        if labels.contains("loom:human") && !board_task_is_closed(Some(task)) {
            pending_asks.push(json!({
                "id": task.id,
                "title": task.task,
                "parent_task_id": task.parent_task_id,
            }));
        }
    }

    let mut ordered_lanes = lane_counts;
    for (lane_name, count) in extra_lanes {
        ordered_lanes.insert(lane_name, count);
    }

    let designated_weaver_id = st.get_group_settings(group).weaver_agent_id;
    let effective_weaver_id = weaver_id
        .unwrap_or(designated_weaver_id.as_str())
        .trim()
        .to_string();
    let mut agent_status_counts = BTreeMap::from([
        ("error".to_string(), 0u64),
        ("idle".to_string(), 0u64),
        ("running".to_string(), 0u64),
        ("stopped".to_string(), 0u64),
    ]);
    let mut total_agents = 0u64;
    let mut needs_attention = 0u64;
    let mut active_agents = Vec::new();
    let mut boundary_items = Vec::new();
    let mut seen_branch_keys = HashSet::<String>::new();

    let mut agents = st
        .agents
        .values()
        .filter(|cell| cell.cell_type == "agent" && cell.group == group)
        .filter(|cell| effective_weaver_id.is_empty() || cell.id != effective_weaver_id)
        .filter(|cell| agent_visible(st, some_if_not_empty(&effective_weaver_id), &cell.id))
        .cloned()
        .collect::<Vec<_>>();
    agents.sort_by(|a, b| {
        agent_display_name(a)
            .to_lowercase()
            .cmp(&agent_display_name(b).to_lowercase())
            .then_with(|| a.id.cmp(&b.id))
    });

    let summary_streams = compute_weaver_streams(
        st,
        group,
        some_if_not_empty(&effective_weaver_id),
        false,
        false,
    );

    for cell in &agents {
        total_agents += 1;
        if cell.needs_attention {
            needs_attention += 1;
        }
        let status = if cell.status.trim().is_empty() {
            "stopped"
        } else {
            cell.status.as_str()
        };
        *agent_status_counts.entry(status.to_string()).or_insert(0) += 1;
        let current_task = st.agent_current_task(&cell.id);
        if let Some((repo_root, branch)) = stream_identity_for_agent(cell) {
            let boundary_key = format!("{}::{}", repo_root, branch);
            if seen_branch_keys.insert(boundary_key) {
                if let Some(stream) = summary_streams.iter().find(|stream| {
                    stream.get("repo_root").and_then(Value::as_str) == Some(repo_root.as_str())
                        && stream.get("branch").and_then(Value::as_str) == Some(branch.as_str())
                }) {
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
                        "agent_id": cell.id,
                        "agent_name": cell.name,
                        "agent_slug": cell.slug,
                        "current_task_id": current_task.map(|task| task.id.clone()).unwrap_or_default(),
                        "current_task": current_task.map(|task| task.task.clone()).unwrap_or_default(),
                    });
                    if !agent_visible(st, some_if_not_empty(&effective_weaver_id), &cell.id)
                        && hidden_agent_marker(st, some_if_not_empty(&effective_weaver_id))
                    {
                        item["agent_id"] = Value::String(String::new());
                        item["agent_name"] = Value::String(String::new());
                        item["agent_slug"] = Value::String(String::new());
                        item["agent_hidden"] = Value::Bool(true);
                    }
                    boundary_items.push(item);
                }
            }
        }
        if status == "stopped" {
            continue;
        }
        active_agents.push(json!({
            "id": cell.id,
            "name": cell.name,
            "slug": cell.slug,
            "type": cell.agent_type,
            "status": status,
            "current_task_id": current_task.map(|task| task.id.clone()).unwrap_or_default(),
            "current_task": current_task.map(|task| task.task.clone()).unwrap_or_default(),
            "needs_attention": cell.needs_attention,
        }));
    }

    pending_asks.sort_by(simple_title_id_sort);
    unhealthy.sort_by(|a, b| {
        let a_state = a
            .get("health_state")
            .and_then(Value::as_str)
            .unwrap_or("healthy");
        let b_state = b
            .get("health_state")
            .and_then(Value::as_str)
            .unwrap_or("healthy");
        b_state
            .cmp(a_state)
            .then_with(|| {
                a.get("health_since")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("health_since").and_then(Value::as_str).unwrap_or(""))
            })
            .then_with(|| simple_title_id_sort(a, b))
    });
    verification_items.sort_by(|a, b| {
        let a_failed = a.get("verification_state").and_then(Value::as_str) == Some("failed");
        let b_failed = b.get("verification_state").and_then(Value::as_str) == Some("failed");
        (!a_failed)
            .cmp(&!b_failed)
            .then_with(|| simple_title_id_sort(a, b))
    });
    boundary_items.sort_by(|a, b| {
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
    });

    let hints = compute_weaver_hints(
        st,
        group,
        some_if_not_empty(&effective_weaver_id),
        DEFAULT_LIMIT,
    );

    json!({
        "group": group,
        "tasks_total": visible_tasks.len(),
        "archived_total": archived_tasks.len(),
        "lanes": ordered_lanes,
        "labels": label_counts,
        "task_health": {
            "counts": health_counts,
            "unhealthy": unhealthy.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": unhealthy.len() > DEFAULT_LIMIT,
        },
        "hints": {
            "count": hints.len(),
            "items": hints.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": hints.len() > DEFAULT_LIMIT,
        },
        "asks": {
            "count": pending_asks.len(),
            "items": pending_asks.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": pending_asks.len() > DEFAULT_LIMIT,
        },
        "verification": {
            "counts": verification_counts,
            "items": verification_items.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": verification_items.len() > DEFAULT_LIMIT,
        },
        "agents": {
            "total": total_agents,
            "active_count": active_agents.len(),
            "needs_attention": needs_attention,
            "by_status": agent_status_counts,
            "active": active_agents.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": active_agents.len() > DEFAULT_LIMIT,
        },
        "streams": {
            "count": summary_streams.len(),
            "by_state": stream_state_counts(&summary_streams),
            "items": summary_streams.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": summary_streams.len() > DEFAULT_LIMIT,
        },
        "branch_boundaries": {
            "count": boundary_items.len(),
            "items": boundary_items.iter().take(DEFAULT_LIMIT).cloned().collect::<Vec<_>>(),
            "truncated": boundary_items.len() > DEFAULT_LIMIT,
        },
    })
}

fn build_board_list(
    st: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    health: &HashMap<String, TaskHealthSnapshot>,
    lane_filter: &str,
    label_filter: &str,
    health_filter: &str,
    search: &str,
) -> Value {
    let mut lanes = BTreeMap::<String, Vec<Value>>::new();
    for task in st.board_tasks.values() {
        if task.group != group {
            continue;
        }
        if task.lane == ARCHIVED_LANE && lane_filter != ARCHIVED_LANE {
            continue;
        }
        if !lane_filter.is_empty() && task.lane != lane_filter {
            continue;
        }
        if !label_filter.is_empty() && !task.labels.iter().any(|label| label == label_filter) {
            continue;
        }
        let health_state = effective_health_state(task, health.get(&task.id));
        if !health_filter.is_empty() && health_state != health_filter {
            continue;
        }
        if !search.is_empty() {
            let haystack = format!(
                "{} {}",
                task.task.to_lowercase(),
                task.description.to_lowercase()
            );
            if !haystack.contains(search) {
                continue;
            }
        }
        let (agent_name, agent_hidden) = task_agent_payload(st, weaver_id, &task.agent_id);
        let mut item = json!({
            "id": task.id,
            "slug": task.slug,
            "title": task.task,
            "group": task.group,
            "labels": task.labels,
            "action": task.action_name,
            "agent": agent_name,
            "status": task.status,
            "health_state": health_state,
            "verification_state": task.verification_state,
            "verification_mode": task.verification_mode,
            "provider": task.provider,
            "external_id": task.external_id,
            "external_url": task.external_url,
            "health_since": effective_health_since(task, health.get(&task.id)),
            "parent_task_id": task.parent_task_id,
        });
        if agent_hidden {
            item["agent_hidden"] = Value::Bool(true);
        }
        lanes.entry(task.lane.clone()).or_default().push(item);
    }
    let mut ordered = serde_json::Map::new();
    for lane_name in &st.board_lanes {
        if *lane_name == ARCHIVED_LANE && lane_filter != ARCHIVED_LANE {
            continue;
        }
        if let Some(tasks) = lanes.remove(lane_name) {
            ordered.insert(lane_name.clone(), Value::Array(tasks));
        }
    }
    for (lane_name, tasks) in lanes {
        ordered.insert(lane_name, Value::Array(tasks));
    }
    Value::Object(ordered)
}

fn build_task_show(
    st: &MatrixState,
    task: &BoardTask,
    weaver_id: Option<&str>,
    health: &HashMap<String, TaskHealthSnapshot>,
) -> Value {
    let mut payload = serde_json::to_value(task).unwrap_or(Value::Null);
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("title".into(), Value::String(task.task.clone()));
        obj.insert("action".into(), Value::String(task.action_name.clone()));
        obj.insert(
            "health_state".into(),
            Value::String(effective_health_state(task, health.get(&task.id))),
        );
        obj.insert(
            "health_since".into(),
            Value::String(effective_health_since(task, health.get(&task.id))),
        );
        if let Some(snapshot) = health.get(&task.id) {
            obj.insert(
                "health_details".into(),
                Value::Object(snapshot.details.clone()),
            );
        }
    }
    if !task.agent_id.trim().is_empty() {
        let (agent_name, agent_hidden) = task_agent_payload(st, weaver_id, &task.agent_id);
        if let Some(obj) = payload.as_object_mut() {
            if agent_hidden {
                obj.insert("agent_id".into(), Value::String(String::new()));
                if hidden_agent_marker(st, weaver_id) {
                    obj.insert("agent_hidden".into(), Value::Bool(true));
                }
            }
            obj.insert("agent_name".into(), Value::String(agent_name));
        }
    }
    if let Some(obj) = payload.as_object_mut() {
        if !task.messages.is_empty() {
            let take_from = task.messages.len().saturating_sub(10);
            obj.insert(
                "messages".into(),
                Value::Array(task.messages[take_from..].to_vec()),
            );
        }
        if !task.pipeline_root_id.trim().is_empty() || !task.parent_task_id.trim().is_empty() {
            let mut chain = Vec::new();
            for chain_task in st.task_chain(&task.id) {
                if chain_task.group != task.group {
                    continue;
                }
                let (agent_name, agent_hidden) =
                    task_agent_payload(st, weaver_id, &chain_task.agent_id);
                let mut item = json!({
                    "id": chain_task.id,
                    "title": chain_task.task,
                    "lane": chain_task.lane,
                    "status": chain_task.status,
                    "health_state": effective_health_state(chain_task, health.get(&chain_task.id)),
                    "verification_state": chain_task.verification_state,
                    "depth": chain_task.pipeline_depth,
                    "agent": agent_name,
                });
                if agent_hidden {
                    item["agent_hidden"] = Value::Bool(true);
                }
                chain.push(item);
            }
            obj.insert("pipeline_chain".into(), Value::Array(chain));
        }
    }
    payload
}

fn build_agents_list(st: &MatrixState, group: &str, weaver_id: Option<&str>) -> Vec<Value> {
    st.agents
        .values()
        .filter(|cell| cell.cell_type == "agent" && cell.group == group)
        .filter(|cell| agent_visible(st, weaver_id, &cell.id))
        .map(|cell| {
            let current_task = st.agent_current_task(&cell.id);
            json!({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "status": cell.status,
                "group": cell.group,
                "current_task_id": current_task.map(|task| task.id.clone()).unwrap_or_default(),
                "current_task": current_task.map(|task| task.task.clone()).unwrap_or_default(),
                "activity": cell.activity,
                "activity_detail": cell.activity_detail,
            })
        })
        .collect()
}

fn build_agent_show(st: &MatrixState, group: &str, cell: &AgentCell) -> Value {
    let mut payload = json!({
        "id": cell.id,
        "name": cell.name,
        "slug": cell.slug,
        "agent_type": cell.agent_type,
        "status": cell.status,
        "group": cell.group,
        "directory": cell.directory,
        "git_root": cell.git_root,
        "activity": cell.activity,
        "activity_detail": cell.activity_detail,
        "error_message": cell.error_message,
        "needs_attention": cell.needs_attention,
        "tasks_dispatched": cell.tasks_dispatched,
        "session": {
            "session_id": cell.agent_session_id,
            "tokens_in": cell.session_tokens_in,
            "tokens_out": cell.session_tokens_out,
        },
    });

    if !cell.worktree_path.trim().is_empty() {
        let repo_root = if !cell.worktree_repo_root.trim().is_empty() {
            cell.worktree_repo_root.clone()
        } else {
            cell.git_root.clone()
        };
        let mut worktree = json!({
            "path": cell.worktree_path,
            "branch": cell.worktree_branch,
            "base_branch": cell.worktree_base_branch,
            "dirty": cell.worktree_dirty,
            "diff": cell.worktree_diff,
            "checkpoints": cell.worktree_checkpoints,
            "ahead": cell.worktree_ahead,
            "behind": cell.worktree_behind,
            "merged": cell.worktree_merged,
        });
        let mut boundary_tasks = st
            .board_tasks
            .values()
            .filter(|task| task.group == group)
            .filter(|task| {
                task.worktree_boundary
                    .get("repo_root")
                    .and_then(Value::as_str)
                    == Some(repo_root.as_str())
            })
            .filter(|task| {
                task.worktree_boundary.get("branch").and_then(Value::as_str)
                    == Some(cell.worktree_branch.as_str())
            })
            .map(|task| {
                json!({
                    "task_id": task.id,
                    "task_title": task.task,
                    "lane": task.lane,
                    "boundary": task.worktree_boundary,
                    "resume_after_boundary_task_id": task.resume_after_boundary_task_id,
                })
            })
            .collect::<Vec<_>>();
        boundary_tasks.sort_by(|a, b| {
            a.get("boundary")
                .and_then(Value::as_object)
                .and_then(|boundary| boundary.get("recorded_at"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(
                    b.get("boundary")
                        .and_then(Value::as_object)
                        .and_then(|boundary| boundary.get("recorded_at"))
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                )
                .then_with(|| {
                    a.get("task_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .cmp(b.get("task_id").and_then(Value::as_str).unwrap_or(""))
                })
        });
        if !boundary_tasks.is_empty() {
            worktree["task_boundaries"] = Value::Array(boundary_tasks);
        }
        if let Some(stream) = compute_worktree_streams(st, group, DEFAULT_LIMIT, true)
            .into_iter()
            .find(|stream| {
                stream.get("repo_root").and_then(Value::as_str) == Some(repo_root.as_str())
                    && stream.get("branch").and_then(Value::as_str)
                        == Some(cell.worktree_branch.as_str())
            })
        {
            let current_task = st
                .agent_current_task(&cell.id)
                .filter(|task| task.group == group);
            let overview = json!({
                "repo_root": repo_root,
                "branch": cell.worktree_branch,
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
                "current_task_id": current_task.map(|task| task.id.clone()).unwrap_or_default(),
                "current_task": current_task.map(|task| task.task.clone()).unwrap_or_default(),
            });
            worktree["boundary_overview"] = overview;
        }
        payload["worktree"] = worktree;
    }

    if let Some(children_ids) = st.children.get(&cell.id) {
        if !children_ids.is_empty() {
            let terminals = children_ids
                .iter()
                .filter_map(|child_id| st.agents.get(child_id))
                .map(|terminal| {
                    json!({
                        "name": terminal.name,
                        "slug": terminal.slug,
                        "status": terminal.status,
                        "current_process": terminal.current_process,
                        "current_path": terminal.current_path,
                    })
                })
                .collect::<Vec<_>>();
            payload["terminals"] = Value::Array(terminals);
        }
    }

    let tasks = st
        .board_tasks
        .values()
        .filter(|task| task.group == group && task.agent_id == cell.id)
        .map(|task| {
            let mut item = json!({
                "id": task.id,
                "slug": task.slug,
                "title": task.task,
                "lane": task.lane,
                "status": task.status,
                "labels": task.labels,
                "action": task.action_name,
                "resume_after_boundary_task_id": task.resume_after_boundary_task_id,
            });
            if !task.worktree_boundary.is_empty() {
                item["worktree_boundary"] = Value::Object(task.worktree_boundary.clone());
            }
            if !task.messages.is_empty() {
                item["messages"] = Value::Array(task.messages.clone());
            }
            item
        })
        .collect::<Vec<_>>();
    if !tasks.is_empty() {
        payload["tasks"] = Value::Array(tasks);
    }

    if let Some(current_task) = st.agent_current_task(&cell.id) {
        if current_task.group == group {
            payload["current_task_id"] = Value::String(current_task.id.clone());
        }
    }
    payload
}

fn filtered_streams(
    st: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    state_filter: &str,
    branch_filter: &str,
    repo_root_filter: &str,
    include_orphaned: bool,
    include_merged: bool,
) -> Vec<Value> {
    compute_weaver_streams(st, group, weaver_id, include_orphaned, include_merged)
        .into_iter()
        .filter(|stream| {
            state_filter.is_empty()
                || stream.get("state").and_then(Value::as_str) == Some(state_filter)
        })
        .filter(|stream| {
            branch_filter.is_empty()
                || stream.get("branch").and_then(Value::as_str) == Some(branch_filter)
        })
        .filter(|stream| {
            repo_root_filter.is_empty()
                || stream.get("repo_root").and_then(Value::as_str) == Some(repo_root_filter)
        })
        .collect()
}

fn compute_weaver_streams(
    st: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    include_orphaned: bool,
    include_merged: bool,
) -> Vec<Value> {
    compute_worktree_streams(st, group, DEFAULT_LIMIT, include_orphaned)
        .into_iter()
        .filter(|stream| {
            include_merged || stream.get("state").and_then(Value::as_str) != Some("merged")
        })
        .map(|stream| scrub_stream(st, weaver_id, stream))
        .collect()
}

fn scrub_stream(st: &MatrixState, weaver_id: Option<&str>, mut stream: Value) -> Value {
    let agent_id = stream
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if agent_id.is_empty() || agent_visible(st, weaver_id, &agent_id) {
        return stream;
    }
    if let Some(obj) = stream.as_object_mut() {
        obj.insert("agent_id".into(), Value::String(String::new()));
        obj.insert("agent_name".into(), Value::String(String::new()));
        obj.insert("agent_slug".into(), Value::String(String::new()));
        if hidden_agent_marker(st, weaver_id) {
            obj.insert("agent_hidden".into(), Value::Bool(true));
        }
    }
    stream
}

fn resolve_stream_payload(
    streams: &[Value],
    stream_ident: &str,
    repo_root: &str,
    branch: &str,
    task_id: &str,
) -> Option<Value> {
    if !task_id.trim().is_empty() {
        return streams
            .iter()
            .find(|stream| {
                loom_weaver::streams::member_task_ids_for_stream(stream).contains(task_id)
            })
            .cloned();
    }
    if !stream_ident.trim().is_empty() {
        let ident = stream_ident.trim();
        if let Some(stream) = streams
            .iter()
            .find(|stream| stream.get("stream_id").and_then(Value::as_str) == Some(ident))
        {
            return Some(stream.clone());
        }
        if let Some(stream) = streams.iter().find(|stream| {
            let repo_root = stream
                .get("repo_root")
                .and_then(Value::as_str)
                .unwrap_or("");
            let branch = stream.get("branch").and_then(Value::as_str).unwrap_or("");
            format!("{}::{}", repo_root, branch) == ident || branch == ident
        }) {
            return Some(stream.clone());
        }
    }
    if !branch.trim().is_empty() {
        let mut matches = streams
            .iter()
            .filter(|stream| stream.get("branch").and_then(Value::as_str) == Some(branch.trim()))
            .filter(|stream| {
                repo_root.trim().is_empty()
                    || stream.get("repo_root").and_then(Value::as_str) == Some(repo_root.trim())
            })
            .cloned()
            .collect::<Vec<_>>();
        matches.sort_by(|a, b| {
            a.get("stream_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("stream_id").and_then(Value::as_str).unwrap_or(""))
        });
        if matches.len() == 1 {
            return matches.into_iter().next();
        }
    }
    None
}

fn resolve_visible_agent_id(
    st: &MatrixState,
    group: &str,
    weaver_id: Option<&str>,
    ident: &str,
) -> Option<String> {
    let agent_id = st.resolve_agent_ident(ident)?;
    let agent = st.agents.get(&agent_id)?;
    if agent.group != group
        || agent.cell_type != "agent"
        || !agent_visible(st, weaver_id, &agent_id)
    {
        return None;
    }
    Some(agent_id)
}

fn resolve_group_task_id(st: &MatrixState, group: &str, ident: &str) -> Option<String> {
    let task_id = st.resolve_task_ident(ident)?;
    let task = st.board_tasks.get(&task_id)?;
    if task.group != group {
        return None;
    }
    Some(task_id)
}

fn active_worker_ids(st: &MatrixState, group: &str, weaver_id: Option<&str>) -> HashSet<String> {
    st.agents
        .values()
        .filter(|cell| cell.cell_type == "agent")
        .filter(|cell| cell.group == group)
        .filter(|cell| weaver_id.map(str::trim).unwrap_or("") != cell.id)
        .filter(|cell| agent_visible(st, weaver_id, &cell.id))
        .filter(|cell| st.agent_is_busy(&cell.id))
        .map(|cell| cell.id.clone())
        .collect()
}

fn blocked_dependency_titles(st: &MatrixState, task: &BoardTask) -> Vec<String> {
    task.depends_on
        .iter()
        .filter_map(|dep_id| st.board_tasks.get(dep_id))
        .filter(|dependency| !loom_core::state::board_task_counts_as_done(Some(dependency)))
        .map(|dependency| dependency.task.chars().take(40).collect::<String>())
        .collect()
}

fn summarize_weaver_message(message: &str, limit: usize) -> String {
    let summary = message
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or_else(|| message.trim());
    if summary.is_empty() {
        return "Weaver follow-up".into();
    }
    let char_count = summary.chars().count();
    if char_count <= limit {
        return summary.to_string();
    }
    let mut truncated = summary
        .chars()
        .take(limit.saturating_sub(1))
        .collect::<String>();
    truncated.push('…');
    truncated
}

fn weaver_followup_task_title(message: &str) -> String {
    format!("Weaver: {}", summarize_weaver_message(message, 72))
}

fn format_weaver_message_prompt(message: &str, task_id: &str) -> String {
    format!(
        "\n## Message from Weaver\n{message}\n\nTask: {task_id}\nReply with: loom_reply(task=\"{task_id}\", message=\"your response\")\n---\n"
    )
}

fn resolve_pending_weaver_reply_task(
    st: &MatrixState,
    agent_id: &str,
    task_id: &str,
) -> Result<String, CmdError> {
    let pending = st.agent_pending_weaver_reply_tasks(agent_id);
    let task_id = task_id.trim();
    if !task_id.is_empty() {
        let resolved = st
            .resolve_task_ident(task_id)
            .ok_or_else(|| CmdError::BadRequest(format!("Task not found: {task_id}")))?;
        let task = st
            .board_tasks
            .get(&resolved)
            .ok_or_else(|| CmdError::BadRequest(format!("Task not found: {task_id}")))?;
        if task.reply_agent_id != agent_id {
            return Err(CmdError::BadRequest(format!(
                "Task {} is not awaiting a reply from this agent",
                task.id
            )));
        }
        if board_task_is_closed(Some(task)) {
            return Err(CmdError::BadRequest(format!(
                "Task {} is already closed",
                task.id
            )));
        }
        return Ok(task.id.clone());
    }
    match pending.len() {
        0 => Err(CmdError::BadRequest(
            "No pending weaver message to reply to".into(),
        )),
        1 => Ok(pending[0].id.clone()),
        _ => {
            let mut ids = pending
                .iter()
                .take(5)
                .map(|task| task.id.clone())
                .collect::<Vec<_>>()
                .join(", ");
            if pending.len() > 5 {
                ids.push_str(", …");
            }
            Err(CmdError::BadRequest(format!(
                "Multiple pending weaver messages; reply with task=<id>. Open reply tasks: {ids}"
            )))
        }
    }
}

fn format_human_reply(answer: &str) -> String {
    format!("\n## Human Reply\n{answer}\n---\n")
}

async fn append_journal_entry(
    ctx: &CmdContext,
    group: &str,
    entry_type: &str,
    entry: &str,
) -> Result<Value, CmdError> {
    if !matches!(
        entry_type,
        "decision" | "observation" | "checkpoint" | "plan"
    ) {
        return Err(CmdError::BadRequest(
            "entry_type must be one of: decision, observation, checkpoint, plan".into(),
        ));
    }
    if entry.trim().is_empty() {
        return Err(CmdError::BadRequest("Entry text is required".into()));
    }
    let ts = Utc::now().timestamp() as f64;
    let id = ctx
        .db
        .append_weaver_journal(group, entry_type, entry, ts)
        .await?;
    let item = json!({
        "id": id,
        "group": group,
        "type": entry_type,
        "entry": entry,
        "timestamp": ts,
    });
    {
        let mut st = ctx.state.lock().await;
        let entries = st.weaver_journal.entry(group.to_string()).or_default();
        entries.push(item.clone());
        if entries.len() > MAX_JOURNAL_ENTRIES {
            let excess = entries.len() - MAX_JOURNAL_ENTRIES;
            entries.drain(0..excess);
        }
        st.emit(DeltaOp::JournalAppend(item.clone()));
    }
    Ok(item)
}

async fn append_panel_event(
    ctx: &CmdContext,
    kind: &str,
    group: &str,
    message: &str,
    task_id: &str,
) -> Result<Value, CmdError> {
    let (cell_id, agent_name) = {
        let st = ctx.state.lock().await;
        let weaver_id = st.get_group_settings(group).weaver_agent_id;
        let agent_name = st
            .agents
            .get(&weaver_id)
            .map(|agent| agent.name.clone())
            .unwrap_or_default();
        (weaver_id, agent_name)
    };
    crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        &ctx.weaver_buffer,
        kind,
        &cell_id,
        &agent_name,
        group,
        message,
        task_id,
        false,
    )
    .await
    .map_err(CmdError::from)
}

pub async fn send_weaver_message_to_agent(
    ctx: &CmdContext,
    weaver_id: &str,
    target_agent_id: &str,
    message: &str,
) -> CmdResult {
    let (target, add_payload) = {
        let st = ctx.state.lock().await;
        let target = st
            .agents
            .get(target_agent_id)
            .cloned()
            .ok_or_else(|| CmdError::BadRequest("Agent not found".into()))?;
        let active_task = st.agent_current_task(target_agent_id).cloned();
        let mut labels = vec![Value::String("loom:weaver-message".into())];
        let mut payload = Map::from_iter([
            (
                "task".into(),
                Value::String(weaver_followup_task_title(message)),
            ),
            ("description".into(), Value::String(message.to_string())),
            ("lane".into(), Value::String("Backlog".into())),
            ("status".into(), Value::String("Awaiting Reply".into())),
            (
                "reply_agent_id".into(),
                Value::String(target_agent_id.to_string()),
            ),
        ]);
        if let Some(task) = active_task {
            labels.insert(0, Value::String("loom:derived".into()));
            payload.insert("group".into(), Value::String(task.group.clone()));
            payload.insert("parent_task_id".into(), Value::String(task.id.clone()));
            payload.insert(
                "pipeline_depth".into(),
                Value::Number((task.pipeline_depth + 1).into()),
            );
            payload.insert(
                "pipeline_root_id".into(),
                Value::String(if task.pipeline_root_id.is_empty() {
                    task.id
                } else {
                    task.pipeline_root_id
                }),
            );
        } else {
            payload.insert("group".into(), Value::String(target.group.clone()));
        }
        payload.insert("labels".into(), Value::Array(labels));
        (target, Value::Object(payload))
    };

    let created = crate::commands::board::add_task(ctx, &add_payload).await?;
    let task_id = created
        .get("task_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if task_id.is_empty() {
        return Err(CmdError::BadRequest(
            "Failed to create Weaver follow-up task".into(),
        ));
    }

    let prompt = format_weaver_message_prompt(message, &task_id);
    if let Err(err) =
        crate::commands::dispatch::send_text_to_cell(ctx, target_agent_id, &prompt).await
    {
        let _ = crate::commands::board::remove_task(ctx, &json!({ "id": task_id })).await;
        return Err(CmdError::BadRequest(format!(
            "Failed to send message: {err:?}"
        )));
    }

    let (task, agent, weaver_name) = {
        let mut st = ctx.state.lock().await;
        let weaver_name = st
            .agents
            .get(weaver_id)
            .map(agent_display_name)
            .unwrap_or_else(|| "Weaver".into());
        let mut task =
            st.board_tasks.get(&task_id).cloned().ok_or_else(|| {
                CmdError::BadRequest("Failed to create Weaver follow-up task".into())
            })?;
        task.messages.push(json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "action": "weaver_message",
            "message": message,
            "agent_name": weaver_name,
        }));
        st.upsert_task(task.clone())?;
        let Some(agent) = st.agents.get_mut(target_agent_id) else {
            return Err(CmdError::BadRequest("Agent not found".into()));
        };
        agent.pending_weaver_message = true;
        let agent = agent.clone();
        st.emit_task(&task_id);
        st.emit_agent(target_agent_id);
        (task, agent, weaver_name)
    };

    ctx.db.save_board_task(&task).await?;
    ctx.db.save_agent(&agent).await?;
    ctx.db
        .save_agent_message_record(
            &agent.id,
            &task.id,
            Utc::now().timestamp() as f64,
            "weaver_message",
            message,
        )
        .await?;
    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        &ctx.weaver_buffer,
        "weaver_message",
        &agent.id,
        &agent.name,
        &target.group,
        &message.chars().take(200).collect::<String>(),
        &task.id,
        false,
    )
    .await;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "task_id": task.id,
        "agent_id": agent.id,
        "agent_name": agent_display_name(&agent),
        "weaver_name": weaver_name,
    }))
}

pub async fn handle_weaver_reply(
    ctx: &CmdContext,
    agent_id: &str,
    task_id: &str,
    message: &str,
) -> CmdResult {
    if message.trim().is_empty() {
        return Err(CmdError::BadRequest("Reply message is required".into()));
    }

    let resolved_task_id = {
        let st = ctx.state.lock().await;
        resolve_pending_weaver_reply_task(&st, agent_id, task_id)?
    };

    let (task, agent, group) = {
        let mut st = ctx.state.lock().await;
        let mut task = st
            .board_tasks
            .get(&resolved_task_id)
            .cloned()
            .ok_or_else(|| CmdError::BadRequest("Task not found".into()))?;
        task.messages.push(json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "action": "reply",
            "message": message,
            "agent_name": st.agents.get(agent_id).map(|cell| cell.name.clone()).unwrap_or_default(),
        }));
        task.status.clear();
        if !board_task_is_closed(Some(&task)) {
            task.lane = "Done".into();
            task.lane_entered_at = chrono::Utc::now().to_rfc3339();
        }
        task.updated_at = chrono::Utc::now().to_rfc3339();
        st.upsert_task(task.clone())?;
        let still_pending = st
            .agent_pending_weaver_reply_tasks(agent_id)
            .into_iter()
            .any(|pending| pending.id != task.id);
        let agent = {
            let agent = st
                .agents
                .get_mut(agent_id)
                .ok_or_else(|| CmdError::BadRequest("agent not found".into()))?;
            agent.pending_weaver_message = still_pending;
            agent.clone()
        };
        let group = agent.group.clone();
        st.emit_task(&task.id);
        st.emit_agent(agent_id);
        (task, agent, group)
    };

    ctx.db.save_board_task(&task).await?;
    ctx.db.save_agent(&agent).await?;
    ctx.db
        .save_agent_message_record(
            &agent.id,
            &task.id,
            Utc::now().timestamp() as f64,
            "reply",
            message,
        )
        .await?;
    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        &ctx.weaver_buffer,
        "agent_reply",
        &agent.id,
        &agent.name,
        &group,
        &message.chars().take(200).collect::<String>(),
        &task.id,
        false,
    )
    .await;
    flush(ctx).await;
    Ok(json!({ "ok": true, "task_id": task.id }))
}

fn read_journal_entries(
    st: &MatrixState,
    group: &str,
    tail: usize,
    entry_type: &str,
) -> Vec<Value> {
    let mut entries = st.weaver_journal.get(group).cloned().unwrap_or_default();
    entries.sort_by(|a, b| compare_journal_entries_desc(a, b));
    entries
        .into_iter()
        .filter(|entry| {
            entry_type.is_empty() || entry.get("type").and_then(Value::as_str) == Some(entry_type)
        })
        .take(tail.max(1))
        .collect()
}

fn journal_sort_key(entry: &Value) -> (i64, f64) {
    (
        entry.get("id").and_then(Value::as_i64).unwrap_or(0),
        entry
            .get("timestamp")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
    )
}

fn compare_journal_entries_desc(a: &Value, b: &Value) -> Ordering {
    let (a_id, a_ts) = journal_sort_key(a);
    let (b_id, b_ts) = journal_sort_key(b);
    b_id.cmp(&a_id)
        .then_with(|| b_ts.partial_cmp(&a_ts).unwrap_or(Ordering::Equal))
}

async fn apply_settings_patch(
    ctx: &CmdContext,
    group: &str,
    patch: Map<String, Value>,
) -> Result<Value, CmdError> {
    let settings = {
        let mut st = ctx.state.lock().await;
        st.update_weaver_settings(group, patch)?
    };
    ctx.db.save_weaver_settings(group, &settings).await?;
    Ok(serde_json::to_value(settings).unwrap_or(Value::Null))
}

fn settings_patch_from_req(req: &Value) -> Map<String, Value> {
    let mut patch = Map::new();
    for key in [
        "push_interval",
        "max_interval",
        "heartbeat_interval",
        "default_worker_concurrency",
        "autonomy_mode",
        "wave_size_preference",
        "same_agent_follow_up_preference",
        "digest_verbosity",
        "escalation_style",
        "pending_note",
        "pending_note_kind",
        "pending_question",
        "custom_instructions",
        "enabled_events",
        "paused",
        "restrict_to_created_agents",
        "weaver_provider",
        "weaver_boot_command",
        "weaver_model",
        "weaver_reasoning_effort",
        "weaver_directory",
        "weaver_profile",
        "weaver_shell",
        "weaver_tab_color",
    ] {
        if let Some(value) = req.get(key) {
            patch.insert(key.to_string(), value.clone());
        }
    }
    patch
}

fn notification_preset(name: &str) -> Result<Vec<(String, Value)>, CmdError> {
    let (digest_verbosity, push_interval, max_interval, heartbeat_interval, enabled_events) =
        match name.trim().to_lowercase().as_str() {
            "quiet" => (
                "compact",
                120,
                600,
                0,
                vec!["task_derived", "task_health_alert"],
            ),
            "normal" => (
                "balanced",
                60,
                300,
                300,
                vec![
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ],
            ),
            "noisy" => (
                "detailed",
                30,
                120,
                60,
                vec![
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "agent_progress",
                    "task_health_alert",
                ],
            ),
            _ => {
                return Err(CmdError::BadRequest(
                    "Unknown notification preset. Use quiet, normal, or noisy.".into(),
                ))
            }
        };
    Ok(vec![
        (
            "digest_verbosity".into(),
            Value::String(digest_verbosity.into()),
        ),
        ("push_interval".into(), Value::Number(push_interval.into())),
        ("max_interval".into(), Value::Number(max_interval.into())),
        (
            "heartbeat_interval".into(),
            Value::Number(heartbeat_interval.into()),
        ),
        (
            "enabled_events".into(),
            Value::Array(
                enabled_events
                    .into_iter()
                    .map(|value| Value::String(value.into()))
                    .collect(),
            ),
        ),
    ])
}

fn effective_health_state(task: &BoardTask, snapshot: Option<&TaskHealthSnapshot>) -> String {
    snapshot
        .map(|snapshot| snapshot.state.clone())
        .unwrap_or_else(|| {
            if task.health_state.trim().is_empty() {
                "healthy".into()
            } else {
                task.health_state.clone()
            }
        })
}

fn effective_health_since(task: &BoardTask, snapshot: Option<&TaskHealthSnapshot>) -> String {
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

fn task_agent_payload(st: &MatrixState, weaver_id: Option<&str>, agent_id: &str) -> (String, bool) {
    let agent_id = agent_id.trim();
    if agent_id.is_empty() {
        return (String::new(), false);
    }
    let Some(agent) = st.agents.get(agent_id) else {
        return (String::new(), false);
    };
    if agent_visible(st, weaver_id, agent_id) {
        let name = if !agent.name.trim().is_empty() {
            agent.name.clone()
        } else if !agent.slug.trim().is_empty() {
            agent.slug.clone()
        } else {
            agent_id.to_string()
        };
        (name, false)
    } else {
        (String::new(), true)
    }
}

fn simple_title_id_sort(a: &Value, b: &Value) -> Ordering {
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
        .then_with(|| {
            a.get("id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("id").and_then(Value::as_str).unwrap_or(""))
        })
}

fn stream_state_counts(streams: &[Value]) -> Value {
    let mut counts = BTreeMap::<String, u64>::new();
    for stream in streams {
        if let Some(state_name) = stream.get("state").and_then(Value::as_str) {
            if !state_name.trim().is_empty() {
                *counts.entry(state_name.to_string()).or_insert(0) += 1;
            }
        }
    }
    json!(counts)
}

fn agent_visible(st: &MatrixState, weaver_id: Option<&str>, agent_id: &str) -> bool {
    let agent_id = agent_id.trim();
    if agent_id.is_empty() {
        return false;
    }
    if let Some(weaver_id) = weaver_id.map(str::trim).filter(|value| !value.is_empty()) {
        st.agent_is_visible_to_weaver(weaver_id, agent_id)
    } else {
        true
    }
}

fn hidden_agent_marker(st: &MatrixState, weaver_id: Option<&str>) -> bool {
    let Some(weaver_id) = weaver_id.map(str::trim).filter(|value| !value.is_empty()) else {
        return false;
    };
    let Some(weaver) = st.agents.get(weaver_id) else {
        return false;
    };
    st.weaver_restricts_to_created_agents(&weaver.group)
}

fn agent_display_name(agent: &AgentCell) -> String {
    if !agent.slug.trim().is_empty() {
        agent.slug.clone()
    } else if !agent.name.trim().is_empty() {
        agent.name.clone()
    } else {
        agent.id.clone()
    }
}

fn some_if_not_empty(value: &str) -> Option<&str> {
    if value.trim().is_empty() {
        None
    } else {
        Some(value)
    }
}
