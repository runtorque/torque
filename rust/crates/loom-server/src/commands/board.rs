//! Board task commands.

use serde_json::{json, Value};

use loom_core::state::{BoardTask, ARCHIVED_LANE};
use loom_core::task_ids::{
    format_root_task_id, is_canonical_task_id, is_draft_task_token, normalize_group_prefix,
};

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

fn resolve_task_ref(st: &loom_core::state::MatrixState, ident: Option<&str>) -> String {
    let value = ident.unwrap_or("").trim();
    if value.is_empty() {
        return String::new();
    }
    st.task_id_aliases
        .get(value)
        .cloned()
        .or_else(|| st.resolve_task_ident(value))
        .unwrap_or_else(|| value.to_string())
}

fn allocate_derived_task_id(
    st: &mut loom_core::state::MatrixState,
    root_id: &str,
) -> (String, u64) {
    let counter = st
        .pipeline_task_counters
        .entry(root_id.to_string())
        .or_insert(1);
    let next_child = (*counter).max(1);
    *counter = next_child + 1;
    let task_id = if next_child == 1 {
        format!("{root_id}a")
    } else {
        format!("{root_id}a{next_child}")
    };
    (task_id, *counter)
}

pub async fn add_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let title = required_str(req, "task")?.to_string();
    let group = required_str(req, "group")?.to_string();
    let incoming_id = optional_str(req, "id")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);

    // A `draft-*` token (or any other non-canonical id) is a transient
    // placeholder the frontend generates so attachments can be uploaded
    // before the task exists — it must not be persisted as the real id.
    // Strip it off here; the canonical-allocation path below will mint a
    // proper `<group-slug>-<N>` id. The stripped value is surfaced as
    // `draft_upload_id` in the response so the client can rekey any
    // in-flight attachment references.
    let (explicit_id, draft_upload_id) = match incoming_id {
        Some(id) if is_draft_task_token(&id) || !is_canonical_task_id(&id) => (None, Some(id)),
        other => (other, None),
    };

    let (id, prefix, next_root_number, next_child_number, parent_task_id, pipeline_root_id) = {
        let mut st = ctx.state.lock().await;
        if !st.groups.contains_key(&group) {
            return Err(CmdError::BadRequest(format!("group '{group}' not found")));
        }
        let parent_task_id = resolve_task_ref(&st, optional_str(req, "parent_task_id"));
        let explicit_root = resolve_task_ref(&st, optional_str(req, "pipeline_root_id"));
        let derived_root_id = if !explicit_root.is_empty() {
            explicit_root.clone()
        } else {
            parent_task_id.clone()
        };
        let prefix = normalize_group_prefix(&group);
        let (id, next_root_number, next_child_number) = if let Some(explicit_id) =
            explicit_id.clone()
        {
            (explicit_id, 0, None)
        } else if !derived_root_id.is_empty() {
            let (task_id, next_child_number) = allocate_derived_task_id(&mut st, &derived_root_id);
            (task_id, 0, Some(next_child_number))
        } else {
            let counter = st.task_id_counters.entry(prefix.clone()).or_insert(0);
            *counter += 1;
            let trunk = *counter;
            (format_root_task_id(&group, trunk), trunk, None)
        };
        let pipeline_root_id = if !explicit_root.is_empty() {
            explicit_root
        } else if !derived_root_id.is_empty() {
            derived_root_id
        } else {
            id.clone()
        };
        (
            id,
            prefix,
            next_root_number,
            next_child_number,
            parent_task_id,
            pipeline_root_id,
        )
    };

    let mut task = BoardTask::new_minimal(id.clone(), title.clone());
    task.group = group.clone();
    task.parent_task_id = parent_task_id;
    task.pipeline_root_id = pipeline_root_id.clone();
    task.lane = req
        .get("lane")
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or_else(|| "Backlog".into());
    // Validate the lane exists, falling back silently if not — matches
    // Python `state.board_add_task` which returns None for unknown lanes;
    // here we keep the task but coerce to Backlog so we don't corrupt the
    // board with a ghost lane.
    {
        let st = ctx.state.lock().await;
        if !st.board_lanes.contains(&task.lane) {
            return Err(CmdError::BadRequest(format!(
                "lane '{}' not found",
                task.lane
            )));
        }
    }
    task.created_at = chrono::Utc::now().to_rfc3339();
    task.updated_at = task.created_at.clone();
    task.lane_entered_at = task.created_at.clone();

    if let Some(desc) = req.get("description").and_then(|v| v.as_str()) {
        task.description = desc.to_string();
    }
    if let Some(action) = req.get("action_name").and_then(|v| v.as_str()) {
        task.action_name = action.to_string();
    }
    if let Some(depth) = req.get("pipeline_depth").and_then(|v| v.as_i64()) {
        task.pipeline_depth = depth as i32;
    } else if !task.parent_task_id.is_empty() {
        task.pipeline_depth = 1;
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
    if let Some(reply_agent_id) = req.get("reply_agent_id").and_then(|v| v.as_str()) {
        task.reply_agent_id = reply_agent_id.to_string();
    }
    if let Some(status) = req.get("status").and_then(|v| v.as_str()) {
        task.status = status.to_string();
    }

    let task = {
        let mut st = ctx.state.lock().await;
        st.upsert_task(task.clone())?;
        st.board_tasks.get(&id).cloned().unwrap_or(task)
    };
    if next_root_number > 0 {
        ctx.db
            .save_task_id_counter(&prefix, next_root_number as u64)
            .await?;
    }
    if let Some(next_child_number) = next_child_number {
        ctx.db
            .save_pipeline_task_counter(&pipeline_root_id, next_child_number)
            .await?;
    }
    ctx.db.save_board_task(&task).await?;
    if let Some(ref draft_id) = draft_upload_id {
        if draft_id != &task.id {
            move_draft_attachments(draft_id, &task.id).await;
        }
    }
    flush(ctx).await;
    let mut response = json!({ "ok": true, "task_id": task.id, "slug": task.slug });
    if let Some(draft_id) = draft_upload_id {
        response["draft_upload_id"] = Value::String(draft_id);
    }
    Ok(response)
}

/// Move any files uploaded under `attachments/<draft_id>/` over to
/// `attachments/<canonical_id>/`. Best-effort — logged and ignored on error
/// so a filesystem hiccup can't fail task creation.
async fn move_draft_attachments(draft_id: &str, canonical_id: &str) {
    let base = loom_core::config::attachments_dir();
    let src = base.join(draft_id);
    let dst = base.join(canonical_id);
    if !src.exists() {
        return;
    }
    if dst.exists() {
        let mut entries = match tokio::fs::read_dir(&src).await {
            Ok(entries) => entries,
            Err(err) => {
                tracing::warn!(src = %src.display(), ?err, "failed to read draft attachments dir");
                return;
            }
        };
        while let Ok(Some(entry)) = entries.next_entry().await {
            let target = dst.join(entry.file_name());
            if target.exists() {
                let _ = tokio::fs::remove_file(&target).await;
            }
            if let Err(err) = tokio::fs::rename(entry.path(), &target).await {
                tracing::warn!(to = %target.display(), ?err, "failed to move draft attachment");
            }
        }
        if let Err(err) = tokio::fs::remove_dir_all(&src).await {
            tracing::warn!(src = %src.display(), ?err, "failed to clean up draft attachments dir");
        }
    } else if let Err(err) = tokio::fs::rename(&src, &dst).await {
        tracing::warn!(
            src = %src.display(),
            dst = %dst.display(),
            ?err,
            "failed to move draft attachments dir",
        );
    }
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

    // Delete any uploaded attachments for this task. Matches Python
    // `server.py:4221-4223` — keeps the attachments directory from
    // accumulating orphan files.
    let attachments = loom_core::config::attachments_dir().join(&id);
    if attachments.exists() {
        if let Err(err) = tokio::fs::remove_dir_all(&attachments).await {
            tracing::warn!(task = %id, ?err, "failed to remove task attachments dir");
        }
    }

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
    let position = req.get("position").and_then(|v| v.as_i64()).unwrap_or(0);
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
        return Err(CmdError::BadRequest(format!(
            "cannot rename reserved lane '{from}'"
        )));
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
        return Err(CmdError::BadRequest(format!(
            "cannot remove reserved lane '{name}'"
        )));
    }
    let requested_target = optional_str(req, "move_tasks_to")
        .unwrap_or("")
        .trim()
        .to_string();

    let (lanes, moved_tasks) = {
        let mut st = ctx.state.lock().await;
        if !st.board_lanes.contains(&name) {
            return Err(CmdError::BadRequest(format!("lane '{name}' not found")));
        }
        if st.board_lanes.len() <= 1 {
            return Err(CmdError::BadRequest(
                "cannot remove the last remaining lane".into(),
            ));
        }

        let new_lanes: Vec<String> = st
            .board_lanes
            .iter()
            .filter(|l| *l != &name)
            .cloned()
            .collect();

        // Pick the target lane: explicit `move_tasks_to` if it still exists,
        // otherwise the first remaining lane. Matches Python
        // `MatrixState.board_remove_lane`.
        let target = if !requested_target.is_empty() && new_lanes.contains(&requested_target) {
            requested_target
        } else {
            new_lanes[0].clone()
        };

        st.set_lanes(new_lanes.clone())?;

        // Stack the migrated tasks at the bottom of the target lane so we
        // don't collide with existing positions.
        let now = chrono::Utc::now().to_rfc3339();
        let mut max_pos = st
            .board_tasks
            .values()
            .filter(|t| t.lane == target)
            .map(|t| t.position)
            .max()
            .unwrap_or(-1);
        let affected: Vec<String> = st
            .board_tasks
            .values()
            .filter(|t| t.lane == name)
            .map(|t| t.id.clone())
            .collect();
        let mut moved = Vec::with_capacity(affected.len());
        for tid in &affected {
            if let Some(t) = st.board_tasks.get_mut(tid) {
                max_pos += 1;
                t.lane = target.clone();
                t.position = max_pos;
                t.updated_at = now.clone();
                t.lane_entered_at = now.clone();
                moved.push(t.clone());
            }
            st.emit_task(tid);
        }
        (new_lanes, moved)
    };

    ctx.db.save_board_lanes(&lanes).await?;
    for t in &moved_tasks {
        ctx.db.save_board_task(t).await?;
    }
    flush(ctx).await;
    ok()
}

pub async fn reorder_lanes(ctx: &CmdContext, req: &Value) -> CmdResult {
    let order: Vec<String> = req
        .get("order")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
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

/// Set or clear a task's verification state. Emits a task_upsert delta with
/// the updated verification_* fields populated. `state` must be one of the
/// known values; the UI uses this to draw the "On Review / Verified / Failed"
/// badge.
pub async fn verify_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let state_field = req
        .get("state")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let mode = req
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let notes = req
        .get("notes")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let updated_by = req
        .get("updated_by")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{id}' not found")));
        };
        task.verification_state = state_field;
        if !mode.is_empty() {
            task.verification_mode = mode;
        }
        task.verification_notes = notes;
        task.verification_updated_by = updated_by;
        task.verification_updated_at = chrono::Utc::now().to_rfc3339();
        task.updated_at = task.verification_updated_at.clone();
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "task_id": task.id }))
}

/// Replace the content-area layout tree. Payload `{ "layout": <node> }`
/// (or `{ "layout": null }` to clear and fall back to the single-Terminal
/// default). Persisted to `ui_state[content_layout]`.
pub async fn set_layout(ctx: &CmdContext, req: &Value) -> CmdResult {
    let node_value = req.get("layout").cloned().unwrap_or(Value::Null);

    let layout: Option<loom_core::state::LayoutNode> = if node_value.is_null() {
        None
    } else {
        let parsed = serde_json::from_value(node_value)
            .map_err(|e| CmdError::BadRequest(format!("invalid layout: {e}")))?;
        Some(parsed)
    };

    {
        let mut st = ctx.state.lock().await;
        st.set_layout(layout.clone());
    }

    let stored = match &layout {
        Some(l) => {
            serde_json::to_string(l).map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?
        }
        None => String::new(),
    };
    ctx.db.set_ui_state("content_layout", &stored).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true }))
}

/// Place (or clear) the panel tree in an edge dock zone. Payload:
/// `{ "zone": "top" | "left" | "right" | "bottom" | "center", "layout": <LayoutNode> | null }`.
/// Passing `zone: "center"` routes to `set_layout` — the center's source of
/// truth is `content_layout`. Edge zones are persisted to
/// `ui_state[dock_edges]` as a single JSON blob.
pub async fn dock_panel(ctx: &CmdContext, req: &Value) -> CmdResult {
    let zone_str = req
        .get("zone")
        .and_then(|v| v.as_str())
        .ok_or_else(|| CmdError::BadRequest("zone required".into()))?
        .to_string();
    let zone: loom_core::state::DockZone = match zone_str.as_str() {
        "top" => loom_core::state::DockZone::Top,
        "left" => loom_core::state::DockZone::Left,
        "right" => loom_core::state::DockZone::Right,
        "bottom" => loom_core::state::DockZone::Bottom,
        "center" => loom_core::state::DockZone::Center,
        other => {
            return Err(CmdError::BadRequest(format!(
                "unknown dock zone '{other}' (expected top/left/right/bottom/center)"
            )));
        }
    };

    let node_value = req.get("layout").cloned().unwrap_or(Value::Null);
    let layout: Option<loom_core::state::LayoutNode> = if node_value.is_null() {
        None
    } else {
        let parsed = serde_json::from_value(node_value)
            .map_err(|e| CmdError::BadRequest(format!("invalid layout: {e}")))?;
        Some(parsed)
    };

    let (persisted_edges, persisted_center) = {
        let mut st = ctx.state.lock().await;
        st.set_dock_edge(zone, layout.clone());
        let edges_json = serde_json::to_string(&st.dock_edges)
            .map_err(|e| CmdError::BadRequest(format!("encode edges: {e}")))?;
        let center_json = match (&zone, &st.content_layout) {
            (loom_core::state::DockZone::Center, Some(l)) => Some(
                serde_json::to_string(l)
                    .map_err(|e| CmdError::BadRequest(format!("encode center: {e}")))?,
            ),
            (loom_core::state::DockZone::Center, None) => Some(String::new()),
            _ => None,
        };
        (edges_json, center_json)
    };

    if let Some(center_json) = persisted_center {
        ctx.db.set_ui_state("content_layout", &center_json).await?;
    } else {
        ctx.db.set_ui_state("dock_edges", &persisted_edges).await?;
    }
    flush(ctx).await;
    Ok(json!({ "ok": true }))
}

/// Update dock edge-zone size ratios. Payload:
/// `{ "top": 0.0, "left": 0.22, "right": 0.0, "bottom": 0.32 }`. Missing
/// fields keep their current values. Persisted via `dock_edges`.
pub async fn set_dock_ratios(ctx: &CmdContext, req: &Value) -> CmdResult {
    let persisted_edges = {
        let mut st = ctx.state.lock().await;
        let mut ratios = st.dock_edges.ratios;
        if let Some(v) = req.get("top").and_then(|v| v.as_f64()) {
            ratios.top = v;
        }
        if let Some(v) = req.get("left").and_then(|v| v.as_f64()) {
            ratios.left = v;
        }
        if let Some(v) = req.get("right").and_then(|v| v.as_f64()) {
            ratios.right = v;
        }
        if let Some(v) = req.get("bottom").and_then(|v| v.as_f64()) {
            ratios.bottom = v;
        }
        st.set_dock_ratios(ratios);
        serde_json::to_string(&st.dock_edges)
            .map_err(|e| CmdError::BadRequest(format!("encode edges: {e}")))?
    };
    ctx.db.set_ui_state("dock_edges", &persisted_edges).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true }))
}

/// Set the active panel in the UI (one of `board`, `actions`, `templates`,
/// `context`, `events`, `weaver`, `memory`, etc). Persisted to `ui_state` and
/// broadcast via a `ui_update` delta so other WS clients (e.g. the Python
/// browser UI) stay in sync.
pub async fn set_panel(ctx: &CmdContext, req: &Value) -> CmdResult {
    let panel = req
        .get("panel")
        .and_then(|v| v.as_str())
        .or_else(|| req.get("active").and_then(|v| v.as_str()))
        .unwrap_or("")
        .to_string();
    let height = req.get("height").and_then(|v| v.as_i64()).map(|v| v as i32);
    let open = req.get("open").and_then(|v| v.as_bool());

    let (changed, effective_panel, effective_height) = {
        let mut changed = false;
        let mut st = ctx.state.lock().await;
        let mut effective_height = st.board_panel_height;
        if let Some(next_height) = height {
            if st.board_panel_height != next_height {
                st.board_panel_height = next_height;
                st.emit(loom_core::delta::DeltaOp::UiUpdate {
                    key: "board_panel_height".into(),
                    value: json!(next_height),
                });
                changed = true;
            }
            effective_height = st.board_panel_height;
        }
        let next_panel = if let Some(is_open) = open {
            if is_open {
                "board".to_string()
            } else {
                String::new()
            }
        } else {
            panel.clone()
        };
        if st.panel_active != next_panel {
            st.panel_active = next_panel.clone();
            st.emit(loom_core::delta::DeltaOp::UiUpdate {
                key: "panel_active".into(),
                value: json!(next_panel),
            });
            changed = true;
        }
        let effective_panel = st.panel_active.clone();
        (changed, effective_panel, effective_height)
    };
    if changed {
        if height.is_some() {
            ctx.db
                .set_ui_state("board_panel_height", &effective_height.to_string())
                .await?;
        }
        ctx.db
            .set_ui_state("panel_active", &effective_panel)
            .await?;
        flush(ctx).await;
        Ok(json!({ "ok": true, "panel": effective_panel, "height": effective_height }))
    } else {
        ok()
    }
}

/// Persist the legacy standalone panel layout object unchanged so the
/// existing browser client can restore and sync its panel workspace.
pub async fn set_standalone_panel_layout(ctx: &CmdContext, req: &Value) -> CmdResult {
    let layout = req.get("layout").cloned().unwrap_or_else(|| json!({}));
    let encoded =
        serde_json::to_string(&layout).map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?;
    {
        let mut st = ctx.state.lock().await;
        st.standalone_panel_layout = layout.clone();
        st.emit(loom_core::delta::DeltaOp::UiUpdate {
            key: "standalone_panel_layout".into(),
            value: layout,
        });
    }
    ctx.db
        .set_ui_state("standalone_panel_layout", &encoded)
        .await?;
    flush(ctx).await;
    ok()
}

/// Replace the entire filters-by-group map.
/// Payload: `{ "filters_by_group": { "Eng": {...}, "Ops": {...} } }`.
/// Matches the Python server's shape — the browser persists its full
/// in-memory map on every filter change (see
/// `static/js/board/view-state.js:_boardPersistFilterState`). Accepting
/// a per-group `{"group":…, "filters":…}` payload here would trip
/// `missing 'group'` any time the board or history panel re-persists
/// state.
pub async fn set_filters(ctx: &CmdContext, req: &Value) -> CmdResult {
    replace_per_group_ui(
        ctx,
        req,
        "filters_by_group",
        "board_filters_by_group",
        |st| &mut st.board_filters_by_group,
    )
    .await
}

/// Replace the entire saved-views-by-group map.
/// Payload: `{ "saved_views_by_group": { "Eng": [...], … } }`.
pub async fn set_saved_views(ctx: &CmdContext, req: &Value) -> CmdResult {
    replace_per_group_ui(
        ctx,
        req,
        "saved_views_by_group",
        "board_saved_views_by_group",
        |st| &mut st.board_saved_views_by_group,
    )
    .await
}

/// Replace the entire lane-sorts-by-group map.
/// Payload: `{ "lane_sorts_by_group": { "Eng": {...}, … } }`.
pub async fn set_lane_sorts(ctx: &CmdContext, req: &Value) -> CmdResult {
    replace_per_group_ui(
        ctx,
        req,
        "lane_sorts_by_group",
        "board_lane_sorts_by_group",
        |st| &mut st.board_lane_sorts_by_group,
    )
    .await
}

/// Set per-group card density. Payload: `{ "group": "Eng", "density": "compact" }`.
pub async fn set_card_density(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let density = required_str(req, "density")?.to_string();

    let full_map = {
        let mut st = ctx.state.lock().await;
        st.board_card_density_by_group
            .insert(group.clone(), density.clone());
        let map = st.board_card_density_by_group.clone();
        st.emit(loom_core::delta::DeltaOp::UiUpdate {
            key: "board_card_density_by_group".into(),
            value: json!(map),
        });
        map
    };

    let encoded = serde_json::to_string(&full_map)
        .map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?;
    ctx.db
        .set_ui_state("board_card_density_by_group", &encoded)
        .await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "group": group, "density": density }))
}

/// Replace an entire per-group UI map from a single payload field. The
/// payload shape matches what the Python server accepts:
/// `{ "<payload_key>": { "<group>": <value>, … } }`. Non-dict payloads
/// reset the map to empty (Python behavior — the browser sometimes
/// sends `null` to clear state).
async fn replace_per_group_ui(
    ctx: &CmdContext,
    req: &Value,
    payload_key: &str,
    ui_state_key: &str,
    select: impl FnOnce(
        &mut loom_core::state::MatrixState,
    ) -> &mut std::collections::HashMap<String, Value>,
) -> CmdResult {
    let incoming: std::collections::HashMap<String, Value> = match req.get(payload_key) {
        Some(Value::Object(map)) => map
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
        _ => std::collections::HashMap::new(),
    };

    let full_map = {
        let mut st = ctx.state.lock().await;
        let target = select(&mut st);
        *target = incoming;
        let clone = target.clone();
        st.emit(loom_core::delta::DeltaOp::UiUpdate {
            key: ui_state_key.to_string(),
            value: json!(clone),
        });
        clone
    };

    let encoded = serde_json::to_string(&full_map)
        .map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?;
    ctx.db.set_ui_state(ui_state_key, &encoded).await?;
    flush(ctx).await;
    ok()
}

