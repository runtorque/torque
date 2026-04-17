//! Python-compat read/write command surfaces that the existing JS frontend
//! expects beyond the core CRUD flows.

use std::path::PathBuf;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::delta::DeltaOp;
use loom_core::state::MatrixState;

use crate::weaver_buffer::WeaverEventBuffer;
use loom_core::db::LoomDb;

use super::{ok, optional_str, required_str, CmdContext, CmdResult};

pub async fn get_events(ctx: &CmdContext, req: &Value) -> CmdResult {
    let before_id = req
        .get("before_id")
        .and_then(|v| v.as_i64())
        .filter(|id| *id > 0);
    let limit = req
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(50)
        .min(200) as usize;
    let events = ctx.db.load_panel_events(limit, before_id).await?;
    Ok(json!({
        "type": "events_page",
        "events": events,
    }))
}

pub async fn get_agent_history(ctx: &CmdContext, req: &Value) -> CmdResult {
    let status_filter = optional_str(req, "status").unwrap_or("");
    let limit = req
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(50)
        .min(200) as usize;
    let offset = req.get("offset").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
    let records = ctx
        .db
        .load_agent_history(status_filter, limit, offset)
        .await?;
    Ok(json!({
        "type": "agent_history_list",
        "records": records,
    }))
}

pub async fn get_agent_history_detail(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?;
    let message_limit = req
        .get("message_limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(100)
        .min(200) as usize;

    let Some(record) = ctx.db.load_agent_history_detail(agent_id).await? else {
        return Ok(json!({
            "type": "error",
            "message": "Agent not found in history",
        }));
    };

    let mut tasks = ctx.db.load_agent_tasks(agent_id).await?;
    if tasks.is_empty() {
        let st = ctx.state.lock().await;
        for task in st
            .board_tasks
            .values()
            .filter(|task| task.agent_id == agent_id)
        {
            tasks.push(json!({
                "id": 0,
                "agent_id": agent_id,
                "task_id": task.id,
                "task_title": task.task,
                "started_at": 0.0,
                "completed_at": serde_json::Value::Null,
                "outcome": if task.lane == "Done" { "done" } else { "" },
            }));
        }
    }

    let messages = ctx.db.load_agent_messages(agent_id, message_limit).await?;
    Ok(json!({
        "type": "agent_history_detail",
        "record": record,
        "tasks": tasks,
        "messages": messages,
    }))
}

pub async fn remove_attachment(ctx: &CmdContext, req: &Value) -> CmdResult {
    let task_id = required_str(req, "task_id")?.to_string();
    let filename = required_str(req, "filename")?.to_string();
    let path = loom_core::config::attachments_dir()
        .join(&task_id)
        .join(sanitize_filename(&filename));
    if path.is_file() {
        let _ = std::fs::remove_file(&path);
    }

    let maybe_task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&task_id).cloned() else {
            return ok();
        };
        task.attachments
            .retain(|item| item.get("filename").and_then(|v| v.as_str()).unwrap_or("") != filename);
        task.artifacts
            .retain(|item| item.get("filename").and_then(|v| v.as_str()).unwrap_or("") != filename);
        st.upsert_task(task.clone())?;
        Some(task)
    };

    if let Some(task) = maybe_task {
        ctx.db.save_board_task(&task).await?;
        super::flush(ctx).await;
    }
    ok()
}

pub async fn events_dismiss(ctx: &CmdContext, req: &Value) -> CmdResult {
    let item_id = required_str(req, "id")?.to_string();
    let timestamp = req
        .get("timestamp")
        .and_then(|v| v.as_f64())
        .filter(|value| *value > 0.0)
        .unwrap_or_else(|| chrono::Utc::now().timestamp_millis() as f64 / 1000.0);
    let value = {
        let mut st = ctx.state.lock().await;
        st.events_dismissed_attention.insert(item_id, timestamp);
        let value =
            serde_json::to_value(&st.events_dismissed_attention).unwrap_or_else(|_| json!({}));
        st.emit(DeltaOp::UiUpdate {
            key: "events_dismissed_attention".into(),
            value: value.clone(),
        });
        value
    };
    ctx.db
        .set_ui_state("events_dismissed_attention", &value.to_string())
        .await?;
    super::flush(ctx).await;
    ok()
}

fn sanitize_filename(value: &str) -> PathBuf {
    PathBuf::from(value.replace('/', "_").replace('\\', "_").replace(' ', "_"))
}

pub async fn record_panel_event(
    state: &Arc<Mutex<MatrixState>>,
    db: &LoomDb,
    weaver_buffer: &WeaverEventBuffer,
    kind: &str,
    cell_id: &str,
    agent_name: &str,
    group: &str,
    message: &str,
    task_id: &str,
    replace_last: bool,
) -> Result<serde_json::Value, loom_core::Error> {
    let timestamp = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
    let event = if replace_last {
        if let Some(existing) = db.find_latest_panel_event(kind, cell_id).await? {
            let id = existing.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
            db.update_panel_event(id, agent_name, group, message, task_id, timestamp)
                .await?
        } else {
            db.save_panel_event(
                kind, cell_id, agent_name, group, message, task_id, timestamp,
            )
            .await?
        }
    } else {
        db.save_panel_event(
            kind, cell_id, agent_name, group, message, task_id, timestamp,
        )
        .await?
    };
    db.trim_panel_events(500).await?;
    let mut st = state.lock().await;
    weaver_buffer.record_event(&st, &event).await;
    st.emit(DeltaOp::EventAppend(event.clone()));
    Ok(event)
}
