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
        Some(l) => serde_json::to_string(l)
            .map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?,
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
            if is_open { "board".to_string() } else { String::new() }
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
        ctx.db.set_ui_state("panel_active", &effective_panel).await?;
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
    let encoded = serde_json::to_string(&layout)
        .map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?;
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

/// Set per-group board filter state. Payload: `{ "group": "Eng", "filters": {...} }`.
/// Persisted as JSON in `ui_state[board_filters_by_group]`.
pub async fn set_filters(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_per_group_ui(
        ctx,
        req,
        "filters",
        "board_filters_by_group",
        |st| &mut st.board_filters_by_group,
    )
    .await
}

/// Set per-group saved views. Payload: `{ "group": "Eng", "views": [...] }`.
pub async fn set_saved_views(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_per_group_ui(
        ctx,
        req,
        "views",
        "board_saved_views_by_group",
        |st| &mut st.board_saved_views_by_group,
    )
    .await
}

/// Set per-group lane sort rules. Payload: `{ "group": "Eng", "sorts": {...} }`.
pub async fn set_lane_sorts(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_per_group_ui(
        ctx,
        req,
        "sorts",
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

async fn set_per_group_ui(
    ctx: &CmdContext,
    req: &Value,
    payload_key: &str,
    ui_state_key: &str,
    select: impl FnOnce(
        &mut loom_core::state::MatrixState,
    )
        -> &mut std::collections::HashMap<String, Value>,
) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let value = req.get(payload_key).cloned().ok_or_else(|| {
        CmdError::BadRequest(format!("missing '{payload_key}'"))
    })?;

    let full_map = {
        let mut st = ctx.state.lock().await;
        let map = select(&mut st);
        map.insert(group.clone(), value.clone());
        let clone = map.clone();
        st.emit(loom_core::delta::DeltaOp::UiUpdate {
            key: ui_state_key.to_string(),
            value: json!(clone),
        });
        st_map_clone(&st, ui_state_key)
    };

    let encoded = serde_json::to_string(&full_map)
        .map_err(|e| CmdError::BadRequest(format!("encode: {e}")))?;
    ctx.db.set_ui_state(ui_state_key, &encoded).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "group": group }))
}

/// Snapshot the relevant map by key name. Needed because the closure in
/// `set_per_group_ui` holds a `&mut` on the state and we need to clone before
/// releasing the lock.
fn st_map_clone(
    st: &loom_core::state::MatrixState,
    key: &str,
) -> std::collections::HashMap<String, Value> {
    match key {
        "board_filters_by_group" => st.board_filters_by_group.clone(),
        "board_saved_views_by_group" => st.board_saved_views_by_group.clone(),
        "board_lane_sorts_by_group" => st.board_lane_sorts_by_group.clone(),
        _ => Default::default(),
    }
}
