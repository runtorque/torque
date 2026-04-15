//! Group CRUD commands.

use serde_json::{json, Value};

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

pub async fn add_group(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    {
        let mut st = ctx.state.lock().await;
        st.add_group(&name)?;
    }
    persist_group(ctx, &name).await?;
    flush(ctx).await;
    ok()
}

pub async fn remove_group(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let removed_ids: Vec<String> = {
        let mut st = ctx.state.lock().await;
        st.remove_group(&name)?
    };
    for rid in &removed_ids {
        ctx.db.delete_agent(rid).await?;
    }
    ctx.db.delete_group(&name).await?;
    super::agents::persist_selection(ctx).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "removed_agents": removed_ids }))
}

pub async fn rename_group(ctx: &CmdContext, req: &Value) -> CmdResult {
    let old = required_str(req, "from")?.to_string();
    let new_name = required_str(req, "to")?.to_string();
    {
        let mut st = ctx.state.lock().await;
        st.rename_group(&old, &new_name)?;
    }
    // DB: delete old row, insert new, remap group_members
    ctx.db.delete_group(&old).await?;
    persist_group(ctx, &new_name).await?;
    // agents and tasks keep their ids but the group_name foreign key changed —
    // re-persist all members and affected tasks.
    let (member_ids, task_ids) = {
        let st = ctx.state.lock().await;
        let members = st.groups.get(&new_name).cloned().unwrap_or_default();
        let tasks = st
            .tasks_by_group
            .get(&new_name)
            .map(|s| s.iter().cloned().collect::<Vec<_>>())
            .unwrap_or_default();
        (members, tasks)
    };
    for aid in &member_ids {
        let agent = {
            let st = ctx.state.lock().await;
            st.agents.get(aid).cloned()
        };
        if let Some(a) = agent {
            ctx.db.save_agent(&a).await?;
        }
    }
    for tid in &task_ids {
        let task = {
            let st = ctx.state.lock().await;
            st.board_tasks.get(tid).cloned()
        };
        if let Some(t) = task {
            ctx.db.save_board_task(&t).await?;
        }
    }
    flush(ctx).await;
    ok()
}

pub async fn move_group(ctx: &CmdContext, req: &Value) -> CmdResult {
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
    {
        let mut st = ctx.state.lock().await;
        st.reorder_groups(order)?;
    }
    // Persist ordinals
    let ordered = {
        let st = ctx.state.lock().await;
        st.groups_order.clone()
    };
    for (i, name) in ordered.iter().enumerate() {
        let slug = {
            let st = ctx.state.lock().await;
            st.group_slugs.get(name).cloned().unwrap_or_default()
        };
        ctx.db.save_group(name, &slug, i as i64).await?;
    }
    flush(ctx).await;
    ok()
}

async fn persist_group(ctx: &CmdContext, name: &str) -> Result<(), CmdError> {
    let (slug, ordinal, members, settings) = {
        let st = ctx.state.lock().await;
        let slug = st.group_slugs.get(name).cloned().unwrap_or_default();
        let ordinal = st
            .groups_order
            .iter()
            .position(|g| g == name)
            .unwrap_or(st.groups_order.len()) as i64;
        let members = st.groups.get(name).cloned().unwrap_or_default();
        let settings = st.group_settings.get(name).cloned().unwrap_or_default();
        (slug, ordinal, members, settings)
    };
    ctx.db.save_group(name, &slug, ordinal).await?;
    ctx.db.save_group_members(name, &members).await?;
    ctx.db.save_group_settings(name, &settings).await?;
    Ok(())
}

// Allow `optional_str` re-export usage elsewhere if needed.
#[allow(dead_code)]
fn _use_optional_str(v: &Value) -> Option<&str> {
    optional_str(v, "_")
}
