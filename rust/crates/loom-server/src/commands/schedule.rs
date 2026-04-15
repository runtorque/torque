//! Schedule CRUD — cron-style one-shot or recurring task creation.
//!
//! The scheduler tick (see `crate::scheduler`) already fires tasks whose
//! `scheduled_at` has arrived. These commands manage the `schedules` table:
//! descriptors of *when* + *what* to create. `schedule_run` bypasses the
//! scheduler and creates the task immediately.

use serde_json::{json, Value};
use uuid::Uuid;

use loom_core::delta::DeltaOp;
use loom_core::slug::{slugify, unique_slug};
use loom_core::state::{BoardTask, Schedule};
use loom_core::task_ids::{format_root_task_id, normalize_group_prefix};

use super::{flush, ok, required_str, CmdContext, CmdError, CmdResult};

pub async fn create(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let group = required_str(req, "group")?.to_string();

    let mut sched = Schedule {
        id: Uuid::new_v4().to_string(),
        name: name.clone(),
        slug: String::new(),
        task_template: optstr(req, "task_template"),
        description: optstr(req, "description"),
        group: group.clone(),
        action_name: optstr(req, "action_name"),
        action_vars: req
            .get("action_vars")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default(),
        agent_template: optstr(req, "agent_template"),
        labels: req
            .get("labels")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default(),
        cron_expr: optstr(req, "cron_expr"),
        scheduled_at: optstr(req, "scheduled_at"),
        timezone: optstr(req, "timezone"),
        enabled: req.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        last_run_at: String::new(),
        next_run_at: optstr(req, "next_run_at"),
        run_count: 0,
        last_task_id: String::new(),
        created_at: chrono::Utc::now().to_rfc3339(),
        updated_at: chrono::Utc::now().to_rfc3339(),
    };

    {
        let mut st = ctx.state.lock().await;
        if !st.groups.contains_key(&group) {
            return Err(CmdError::BadRequest(format!("group '{group}' not found")));
        }
        let existing: std::collections::HashSet<String> =
            st.schedules.values().map(|s| s.slug.clone()).collect();
        sched.slug = unique_slug(&slugify(&name), &existing);
        let op_payload = serde_json::to_value(&sched).unwrap_or(Value::Null);
        st.schedules.insert(sched.id.clone(), sched.clone());
        st.emit(DeltaOp::ScheduleUpsert(op_payload));
    }
    ctx.db.save_schedule(&sched).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "schedule_id": sched.id, "slug": sched.slug }))
}

pub async fn update(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let patch = req.get("fields").cloned().unwrap_or(Value::Null);

    let sched = {
        let mut st = ctx.state.lock().await;
        let Some(existing) = st.schedules.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("schedule '{id}' not found")));
        };
        let mut current = serde_json::to_value(&existing)?;
        if let Some(obj) = patch.as_object() {
            if let Some(cur) = current.as_object_mut() {
                for (k, v) in obj {
                    // id + slug are managed internally.
                    if k == "id" || k == "slug" || k == "created_at" || k == "run_count" {
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
        let updated: Schedule =
            serde_json::from_value(current).map_err(|e| CmdError::BadRequest(e.to_string()))?;
        let op_payload = serde_json::to_value(&updated).unwrap_or(Value::Null);
        st.schedules.insert(id.clone(), updated.clone());
        st.emit(DeltaOp::ScheduleUpsert(op_payload));
        updated
    };
    ctx.db.save_schedule(&sched).await?;
    flush(ctx).await;
    ok()
}

pub async fn remove(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    {
        let mut st = ctx.state.lock().await;
        if st.schedules.remove(&id).is_none() {
            return Err(CmdError::BadRequest(format!("schedule '{id}' not found")));
        }
        st.emit(DeltaOp::ScheduleRemove { id: id.clone() });
    }
    ctx.db.delete_schedule(&id).await?;
    flush(ctx).await;
    ok()
}

pub async fn enable(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_enabled(ctx, req, true).await
}

pub async fn disable(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_enabled(ctx, req, false).await
}

async fn set_enabled(ctx: &CmdContext, req: &Value, enabled: bool) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let sched = {
        let mut st = ctx.state.lock().await;
        let Some(sched) = st.schedules.get_mut(&id) else {
            return Err(CmdError::BadRequest(format!("schedule '{id}' not found")));
        };
        sched.enabled = enabled;
        sched.updated_at = chrono::Utc::now().to_rfc3339();
        let clone = sched.clone();
        st.emit(DeltaOp::ScheduleUpsert(
            serde_json::to_value(&clone).unwrap_or(Value::Null),
        ));
        clone
    };
    ctx.db.save_schedule(&sched).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "enabled": enabled }))
}

pub async fn list(ctx: &CmdContext, _req: &Value) -> CmdResult {
    let st = ctx.state.lock().await;
    let schedules: Vec<Value> = st
        .schedules
        .values()
        .map(|s| serde_json::to_value(s).unwrap_or(Value::Null))
        .collect();
    Ok(json!({ "schedules": schedules }))
}

/// Manual trigger: create the task this schedule would produce, bypassing
/// any timing/enabled checks. Bumps `run_count` and `last_*`.
pub async fn run(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let now = chrono::Utc::now().to_rfc3339();

    let (sched, task_id) = {
        let mut st = ctx.state.lock().await;
        let Some(sched) = st.schedules.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("schedule '{id}' not found")));
        };
        if !st.groups.contains_key(&sched.group) {
            return Err(CmdError::BadRequest(format!(
                "schedule group '{}' not found",
                sched.group
            )));
        }

        // Allocate a task id.
        let prefix = normalize_group_prefix(&sched.group);
        let counter = st.task_id_counters.entry(prefix.clone()).or_insert(0);
        *counter += 1;
        let trunk = *counter;
        let task_id = format_root_task_id(&sched.group, trunk);

        // Build the task.
        let title = if sched.task_template.is_empty() {
            sched.name.clone()
        } else {
            sched.task_template.clone()
        };
        let mut task = BoardTask::new_minimal(task_id.clone(), title);
        task.group = sched.group.clone();
        task.pipeline_root_id = task_id.clone();
        task.lane = "To Do".into();
        task.action_name = sched.action_name.clone();
        task.action_vars = sched.action_vars.clone();
        task.labels = sched.labels.clone();
        task.description = sched.description.clone();
        task.created_at = now.clone();
        task.updated_at = now.clone();
        task.lane_entered_at = now.clone();
        st.upsert_task(task.clone())?;

        // Update the schedule run metadata.
        if let Some(s) = st.schedules.get_mut(&id) {
            s.run_count += 1;
            s.last_run_at = now.clone();
            s.last_task_id = task_id.clone();
            s.updated_at = now.clone();
            let clone = s.clone();
            st.emit(DeltaOp::ScheduleUpsert(
                serde_json::to_value(&clone).unwrap_or(Value::Null),
            ));
        }
        (st.schedules.get(&id).cloned().unwrap(), task_id)
    };

    ctx.db.save_schedule(&sched).await?;
    let task = {
        let st = ctx.state.lock().await;
        st.board_tasks.get(&task_id).cloned()
    };
    if let Some(t) = task {
        ctx.db.save_board_task(&t).await?;
    }
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "schedule_id": sched.id,
        "task_id": task_id,
        "run_count": sched.run_count,
    }))
}

// -----

fn optstr(req: &Value, key: &str) -> String {
    req.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}
