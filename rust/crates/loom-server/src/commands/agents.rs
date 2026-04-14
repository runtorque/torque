//! Agent + terminal CRUD commands.

use serde_json::{json, Value};
use uuid::Uuid;

use loom_core::state::AgentCell;

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

pub async fn add_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let group = required_str(req, "group")?.to_string();

    let mut cell = AgentCell::new(Uuid::new_v4().to_string(), &name, &group);
    cell.cell_type = "agent".into();
    apply_common_fields(&mut cell, req);
    let agent_id = cell.id.clone();

    let final_cell = {
        let mut st = ctx.state.lock().await;
        st.add_agent(cell)?;
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&final_cell).await?;
    persist_group_members(ctx, &group).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "agent_id": final_cell.id, "slug": final_cell.slug }))
}

pub async fn add_terminal(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let group = required_str(req, "group")?.to_string();

    let mut cell = AgentCell::new(Uuid::new_v4().to_string(), &name, &group);
    cell.cell_type = "terminal".into();
    if let Some(pid) = optional_str(req, "parent_id") {
        cell.parent_id = pid.to_string();
    }
    apply_common_fields(&mut cell, req);
    let agent_id = cell.id.clone();

    let final_cell = {
        let mut st = ctx.state.lock().await;
        st.add_agent(cell)?;
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&final_cell).await?;
    persist_group_members(ctx, &group).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "agent_id": final_cell.id, "slug": final_cell.slug }))
}

pub async fn remove_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let (group, removed) = {
        let mut st = ctx.state.lock().await;
        let group = st
            .agents
            .get(&id)
            .map(|a| a.group.clone())
            .ok_or_else(|| CmdError::BadRequest(format!("agent '{id}' not found")))?;
        let removed = st.remove_agent(&id)?;
        (group, removed)
    };
    for rid in &removed {
        ctx.db.delete_agent(rid).await?;
    }
    persist_group_members(ctx, &group).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "removed": removed }))
}

pub async fn update_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let patch = req.get("fields").cloned().unwrap_or(Value::Null);

    let (agent, group) = {
        let mut st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&id) else {
            return Err(CmdError::BadRequest(format!("agent '{id}' not found")));
        };
        let mut current = serde_json::to_value(cell)?;
        if let Some(obj) = patch.as_object() {
            if let Some(cur) = current.as_object_mut() {
                for (k, v) in obj {
                    // id, slug, group are handled by dedicated commands
                    if k == "id" || k == "slug" || k == "group" || k == "parent_id" {
                        continue;
                    }
                    cur.insert(k.clone(), v.clone());
                }
            }
        }
        let new_name = current
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let old_name = cell.name.clone();

        // Deserialize back
        let new_cell: AgentCell =
            serde_json::from_value(current).map_err(|e| CmdError::BadRequest(e.to_string()))?;
        let group = new_cell.group.clone();
        st.agents.insert(id.clone(), new_cell);

        if new_name != old_name {
            // rename triggers slug cascade
            st.rename_agent(&id, &new_name)?;
        }
        st.emit_agent(&id);
        let agent = st.agents.get(&id).cloned().unwrap();
        (agent, group)
    };

    ctx.db.save_agent(&agent).await?;
    // child slugs may have changed — persist them too
    let children = {
        let st = ctx.state.lock().await;
        st.children.get(&id).cloned().unwrap_or_default()
    };
    for cid in children {
        let c = {
            let st = ctx.state.lock().await;
            st.agents.get(&cid).cloned()
        };
        if let Some(c) = c {
            ctx.db.save_agent(&c).await?;
        }
    }
    let _ = group;
    flush(ctx).await;
    ok()
}

pub async fn move_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let to = required_str(req, "to_group")?.to_string();

    let (from_group, agent) = {
        let mut st = ctx.state.lock().await;
        let agent = st
            .agents
            .get(&id)
            .cloned()
            .ok_or_else(|| CmdError::BadRequest(format!("agent '{id}' not found")))?;
        if !st.groups.contains_key(&to) {
            return Err(CmdError::BadRequest(format!("group '{to}' not found")));
        }
        let from = agent.group.clone();
        if from == to {
            return ok();
        }
        // remove from source
        if let Some(list) = st.groups.get_mut(&from) {
            list.retain(|x| x != &id);
        }
        st.groups.get_mut(&to).unwrap().push(id.clone());
        if let Some(a) = st.agents.get_mut(&id) {
            a.group = to.clone();
        }
        st.emit_group(&from);
        st.emit_group(&to);
        st.emit_agent(&id);
        (from, st.agents.get(&id).cloned().unwrap())
    };

    ctx.db.save_agent(&agent).await?;
    persist_group_members(ctx, &from_group).await?;
    persist_group_members(ctx, &to).await?;
    flush(ctx).await;
    ok()
}

pub async fn reparent_terminal(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let new_parent = optional_str(req, "parent_id").unwrap_or("").to_string();

    let (group, agent) = {
        let mut st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&id).cloned() else {
            return Err(CmdError::BadRequest(format!("agent '{id}' not found")));
        };
        // detach from old parent
        if !cell.parent_id.is_empty() {
            if let Some(list) = st.children.get_mut(&cell.parent_id) {
                list.retain(|x| x != &id);
            }
        }
        if let Some(a) = st.agents.get_mut(&id) {
            a.parent_id = new_parent.clone();
        }
        if !new_parent.is_empty() {
            st.children.entry(new_parent.clone()).or_default().push(id.clone());
        }
        // regenerate slug
        let name = cell.name.clone();
        let group = cell.group.clone();
        let parent_ref = if new_parent.is_empty() { None } else { Some(new_parent.as_str()) };
        let new_slug = st.make_agent_slug(&name, parent_ref, &group);
        if let Some(a) = st.agents.get_mut(&id) {
            a.slug = new_slug;
        }
        st.emit_agent(&id);
        (group, st.agents.get(&id).cloned().unwrap())
    };

    ctx.db.save_agent(&agent).await?;
    let _ = group;
    flush(ctx).await;
    ok()
}

pub async fn reorder_child(ctx: &CmdContext, req: &Value) -> CmdResult {
    let parent_id = required_str(req, "parent_id")?.to_string();
    let order: Vec<String> = req
        .get("order")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();

    let group = {
        let mut st = ctx.state.lock().await;
        if !st.children.contains_key(&parent_id) && !st.agents.contains_key(&parent_id) {
            return Err(CmdError::BadRequest(format!("parent '{parent_id}' not found")));
        }
        st.children.insert(parent_id.clone(), order);
        st.agents.get(&parent_id).map(|a| a.group.clone()).unwrap_or_default()
    };
    if !group.is_empty() {
        persist_group_members(ctx, &group).await?;
    }
    flush(ctx).await;
    ok()
}

// ---- helpers --------------------------------------------------------------

fn apply_common_fields(cell: &mut AgentCell, req: &Value) {
    if let Some(cmd) = req.get("command").and_then(|v| v.as_str()) {
        cell.command = cmd.to_string();
    }
    if let Some(dir) = req.get("directory").and_then(|v| v.as_str()) {
        cell.directory = dir.to_string();
    }
    if let Some(profile) = req.get("profile").and_then(|v| v.as_str()) {
        cell.profile = profile.to_string();
    }
    if let Some(color) = req.get("tab_color").and_then(|v| v.as_str()) {
        cell.tab_color = color.to_string();
    }
    if let Some(icon) = req.get("icon").and_then(|v| v.as_str()) {
        cell.icon = icon.to_string();
    }
    if let Some(tpl) = req.get("template").and_then(|v| v.as_str()) {
        cell.template = tpl.to_string();
    }
    if let Some(bk) = req.get("terminal_backend").and_then(|v| v.as_str()) {
        cell.terminal_backend = bk.to_string();
    }
}

async fn persist_group_members(ctx: &CmdContext, group: &str) -> Result<(), CmdError> {
    let members = {
        let st = ctx.state.lock().await;
        st.groups.get(group).cloned().unwrap_or_default()
    };
    ctx.db.save_group_members(group, &members).await?;
    Ok(())
}
