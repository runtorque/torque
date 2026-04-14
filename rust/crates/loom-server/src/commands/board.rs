//! Board task commands.

use serde_json::{json, Value};

use loom_core::state::{BoardTask, ARCHIVED_LANE};
use loom_core::task_ids::{format_root_task_id, normalize_group_prefix};

use super::{flush, ok, required_str, CmdContext, CmdError, CmdResult};

pub async fn add_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let title = required_str(req, "task")?.to_string();
    let group = required_str(req, "group")?.to_string();

    let id = {
        let mut st = ctx.state.lock().await;
        if !st.groups.contains_key(&group) {
            return Err(CmdError::BadRequest(format!("group '{group}' not found")));
        }
        let prefix = normalize_group_prefix(&group);
        let counter = st.task_id_counters.entry(prefix.clone()).or_insert(0);
        *counter += 1;
        let trunk = *counter;
        format_root_task_id(&group, trunk)
    };

    let mut task = BoardTask::new_minimal(id.clone(), title.clone());
    task.group = group.clone();
    task.pipeline_root_id = id.clone();
    task.lane = req
        .get("lane")
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or_else(|| "Backlog".into());
    task.created_at = chrono::Utc::now().to_rfc3339();
    task.updated_at = task.created_at.clone();
    task.lane_entered_at = task.created_at.clone();

    if let Some(desc) = req.get("description").and_then(|v| v.as_str()) {
        task.description = desc.to_string();
    }
    if let Some(action) = req.get("action_name").and_then(|v| v.as_str()) {
        task.action_name = action.to_string();
    }
    if let Some(vars) = req.get("action_vars").and_then(|v| v.as_object()) {
        task.action_vars = vars.clone();
    }
    if let Some(labels) = req.get("labels").and_then(|v| v.as_array()) {
        task.labels = labels
            .iter()
            .filter_map(|v| v.as_str().map(String::from))
            .collect();
    }
    if let Some(agent_id) = req.get("agent_id").and_then(|v| v.as_str()) {
        task.agent_id = agent_id.to_string();
    }

    {
        let mut st = ctx.state.lock().await;
        st.upsert_task(task.clone())?;
    }
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "task_id": task.id, "slug": task.slug }))
}

pub async fn update_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let patch = req.get("fields").cloned().unwrap_or(Value::Null);

    let task = {
        let mut st = ctx.state.lock().await;
        let Some(existing) = st.board_tasks.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{id}' not found")));
        };
        let mut current = serde_json::to_value(&existing)?;
        if let Some(obj) = patch.as_object() {
            if let Some(cur) = current.as_object_mut() {
                for (k, v) in obj {
                    if k == "id" || k == "slug" || k == "group" {
                        continue;
                    }
                    cur.insert(k.clone(), v.clone());
                }
            }
        }
        if let Some(cur) = current.as_object_mut() {
            cur.insert(
                "updated_at".into(),
                Value::String(chrono::Utc::now().to_rfc3339()),
            );
        }
        let updated: BoardTask =
            serde_json::from_value(current).map_err(|e| CmdError::BadRequest(e.to_string()))?;
        st.upsert_task(updated.clone())?;
        updated
    };
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    ok()
}

pub async fn remove_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    {
        let mut st = ctx.state.lock().await;
        st.remove_task(&id)?;
    }
    ctx.db.delete_board_task(&id).await?;
    flush(ctx).await;
    ok()
}

pub async fn move_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let lane = required_str(req, "lane")?.to_string();
    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{id}' not found")));
        };
        if !st.board_lanes.contains(&lane) {
            return Err(CmdError::BadRequest(format!("lane '{lane}' not found")));
        }
        task.lane = lane;
        task.lane_entered_at = chrono::Utc::now().to_rfc3339();
        task.updated_at = task.lane_entered_at.clone();
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    ok()
}

pub async fn reorder_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let position = req
        .get("position")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{id}' not found")));
        };
        task.position = position;
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    ok()
}

pub async fn archive_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{id}' not found")));
        };
        let from_lane = task.lane.clone();
        task.archived_from_lane = from_lane;
        task.archived_at = chrono::Utc::now().to_rfc3339();
        task.lane = ARCHIVED_LANE.into();
        task.updated_at = task.archived_at.clone();
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    ok()
}

pub async fn unarchive_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{id}' not found")));
        };
        if task.lane != ARCHIVED_LANE {
            return Err(CmdError::BadRequest("task is not archived".into()));
        }
        let target = if task.archived_from_lane.is_empty() {
            "Backlog".to_string()
        } else {
            std::mem::take(&mut task.archived_from_lane)
        };
        task.lane = target;
        task.archived_at = String::new();
        task.lane_entered_at = chrono::Utc::now().to_rfc3339();
        task.updated_at = task.lane_entered_at.clone();
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    ok()
}

pub async fn add_lane(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let lanes = {
        let mut st = ctx.state.lock().await;
        let mut current = st.board_lanes.clone();
        if !current.contains(&name) {
            current.insert(current.len().saturating_sub(1), name);
        }
        st.set_lanes(current.clone())?;
        current
    };
    ctx.db.save_board_lanes(&lanes).await?;
    flush(ctx).await;
    ok()
}

pub async fn rename_lane(ctx: &CmdContext, req: &Value) -> CmdResult {
    let from = required_str(req, "from")?.to_string();
    let to = required_str(req, "to")?.to_string();
    if loom_core::state::RESERVED_LANES.contains(&from.as_str()) {
        return Err(CmdError::BadRequest(format!("cannot rename reserved lane '{from}'")));
    }
    let lanes = {
        let mut st = ctx.state.lock().await;
        let new_lanes: Vec<String> = st
            .board_lanes
            .iter()
            .map(|l| if l == &from { to.clone() } else { l.clone() })
            .collect();
        st.set_lanes(new_lanes.clone())?;
        // update any task currently in the renamed lane
        let affected: Vec<String> = st
            .board_tasks
            .values()
            .filter(|t| t.lane == from)
            .map(|t| t.id.clone())
            .collect();
        for tid in &affected {
            if let Some(t) = st.board_tasks.get_mut(tid) {
                t.lane = to.clone();
            }
            st.emit_task(tid);
        }
        new_lanes
    };
    ctx.db.save_board_lanes(&lanes).await?;
    flush(ctx).await;
    ok()
}

pub async fn remove_lane(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    if loom_core::state::RESERVED_LANES.contains(&name.as_str()) {
        return Err(CmdError::BadRequest(format!("cannot remove reserved lane '{name}'")));
    }
    let lanes = {
        let mut st = ctx.state.lock().await;
        let new_lanes: Vec<String> = st.board_lanes.iter().filter(|l| *l != &name).cloned().collect();
        st.set_lanes(new_lanes.clone())?;
        // tasks in the removed lane fall back to Backlog
        let affected: Vec<String> = st
            .board_tasks
            .values()
            .filter(|t| t.lane == name)
            .map(|t| t.id.clone())
            .collect();
        for tid in &affected {
            if let Some(t) = st.board_tasks.get_mut(tid) {
                t.lane = "Backlog".into();
            }
            st.emit_task(tid);
        }
        new_lanes
    };
    ctx.db.save_board_lanes(&lanes).await?;
    flush(ctx).await;
    ok()
}

pub async fn reorder_lanes(ctx: &CmdContext, req: &Value) -> CmdResult {
    let order: Vec<String> = req
        .get("order")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();
    if order.is_empty() {
        return Err(CmdError::BadRequest("missing 'order'".into()));
    }
    let lanes = {
        let mut st = ctx.state.lock().await;
        st.set_lanes(order.clone())?;
        order
    };
    ctx.db.save_board_lanes(&lanes).await?;
    flush(ctx).await;
    ok()
}

pub async fn task_chain(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let st = ctx.state.lock().await;
    let chain: Vec<Value> = st
        .task_chain(&id)
        .into_iter()
        .map(|t| serde_json::to_value(t).unwrap_or(Value::Null))
        .collect();
    Ok(json!({ "chain": chain }))
}
