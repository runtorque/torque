//! Global + group settings commands.

use serde_json::{json, Value};

use loom_core::delta::DeltaOp;
use loom_core::state::{GlobalSettings, GroupSettings};

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

pub async fn get_config(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = optional_str(req, "group").unwrap_or("");
    let st = ctx.state.lock().await;
    let group_settings = st.group_settings.get(group).cloned().unwrap_or_default();
    Ok(json!({
        "type": "config",
        "profiles": ["Default"],
        "current_path": "",
        "current_profile": "",
        "group_cells": [],
        "group_settings": group_settings,
        "resolved_agent_defaults": {},
        "providers": provider_payload(),
        "templates": [],
        "playbooks": [],
        "runtime": runtime_payload(),
        "default_command": loom_core::config::default_command(),
        "global_settings": st.global_settings,
        "port": loom_core::config::DEFAULT_PORT,
    }))
}

pub async fn get_global_settings(ctx: &CmdContext) -> CmdResult {
    let st = ctx.state.lock().await;
    Ok(json!({
        "type": "global_settings",
        "settings": st.global_settings,
        "keybinding_defaults": {},
    }))
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
        let fields = struct_to_map(&merged)?;
        st.emit(DeltaOp::GlobalSettingsUpdate { fields });
    }
    ctx.db.save_global_settings(&merged).await?;
    flush(ctx).await;
    ok()
}

pub async fn get_group_settings(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?;
    let st = ctx.state.lock().await;
    let settings = st.group_settings.get(group).cloned().unwrap_or_default();
    Ok(json!({
        "type": "group_settings",
        "group": group,
        "settings": settings,
        "weaver_settings": st.weaver_settings.get(group).cloned().unwrap_or_default(),
        "resolved_agent_defaults": {},
        "profiles": ["Default"],
        "providers": provider_payload(),
        "templates": [],
        "actions": [],
        "playbooks": [],
        "runtime": runtime_payload(),
    }))
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
        let fields = struct_to_map(&merged)?;
        st.emit(DeltaOp::GroupSettingsUpdate { name: group.clone(), fields });
    }
    ctx.db.save_group_settings(&group, &merged).await?;
    flush(ctx).await;
    ok()
}

fn struct_to_map<T: serde::Serialize>(value: &T) -> Result<serde_json::Map<String, Value>, CmdError> {
    serde_json::to_value(value)?
        .as_object()
        .cloned()
        .ok_or_else(|| CmdError::BadRequest("expected object payload".into()))
}

fn provider_payload() -> Vec<Value> {
    loom_adapters::registry::get_providers()
        .into_iter()
        .filter(|name| *name != "generic")
        .map(|name| {
            let command = loom_adapters::registry::get_adapter(name)
                .as_ref()
                .map(|adapter| adapter.default_boot_command().to_string())
                .unwrap_or_default();
            json!({
                "name": name,
                "display_name": name,
                "command": command,
                "reasoning_efforts": [],
            })
        })
        .collect()
}

fn runtime_payload() -> Value {
    json!({
        "standalone": true,
        "embedded_terminal": false,
        "layout": "classic",
        "terminal_backend": "pty",
        "home_directory": dirs::home_dir().map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
        "profile": std::env::var("LOOM_PROFILE").unwrap_or_default(),
        "data_dir": loom_core::config::data_dir().to_string_lossy().to_string(),
        "default_command": loom_core::config::default_command(),
    })
}
