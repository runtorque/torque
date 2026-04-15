//! Global + group settings commands.

use serde_json::{json, Value};

use loom_core::delta::DeltaOp;
use loom_core::state::{GlobalSettings, GroupSettings};

use super::{flush, ok, required_str, CmdContext, CmdError, CmdResult};

pub async fn get_config(ctx: &CmdContext) -> CmdResult {
    let st = ctx.state.lock().await;
    Ok(json!({
        "default_command": loom_core::config::default_command(),
        "global_settings": st.global_settings,
        "port": loom_core::config::DEFAULT_PORT,
    }))
}

pub async fn get_global_settings(ctx: &CmdContext) -> CmdResult {
    let st = ctx.state.lock().await;
    Ok(serde_json::to_value(&st.global_settings)?)
}

pub async fn update_global_settings(ctx: &CmdContext, req: &Value) -> CmdResult {
    let patch = req.get("settings").cloned().unwrap_or(Value::Null);
    let merged: GlobalSettings = {
        let st = ctx.state.lock().await;
        let mut current = serde_json::to_value(&st.global_settings)?;
        if let Some(obj) = patch.as_object() {
            if let Some(cur_obj) = current.as_object_mut() {
                for (k, v) in obj {
                    cur_obj.insert(k.clone(), v.clone());
                }
            }
        }
        serde_json::from_value(current).map_err(|e| CmdError::BadRequest(e.to_string()))?
    };

    {
        let mut st = ctx.state.lock().await;
        st.global_settings = merged.clone();
        let value = serde_json::to_value(&merged)?;
        st.emit(DeltaOp::GlobalSettingsUpdate { settings: value });
    }
    ctx.db.save_global_settings(&merged).await?;
    flush(ctx).await;
    ok()
}

pub async fn get_group_settings(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?;
    let st = ctx.state.lock().await;
    let settings = st.group_settings.get(group).cloned().unwrap_or_default();
    Ok(serde_json::to_value(&settings)?)
}

pub async fn update_group_settings(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let patch = req.get("settings").cloned().unwrap_or(Value::Null);

    let merged: GroupSettings = {
        let st = ctx.state.lock().await;
        if !st.group_settings.contains_key(&group) {
            return Err(CmdError::BadRequest(format!("group '{group}' not found")));
        }
        let current = st.group_settings.get(&group).cloned().unwrap_or_default();
        let mut current_v = serde_json::to_value(&current)?;
        if let Some(obj) = patch.as_object() {
            if let Some(cur_obj) = current_v.as_object_mut() {
                for (k, v) in obj {
                    cur_obj.insert(k.clone(), v.clone());
                }
            }
        }
        serde_json::from_value(current_v).map_err(|e| CmdError::BadRequest(e.to_string()))?
    };

    {
        let mut st = ctx.state.lock().await;
        st.group_settings.insert(group.clone(), merged.clone());
        let value = serde_json::to_value(&merged)?;
        st.emit(DeltaOp::GroupSettingsUpdate {
            name: group.clone(),
            settings: value,
        });
    }
    ctx.db.save_group_settings(&group, &merged).await?;
    flush(ctx).await;
    ok()
}
