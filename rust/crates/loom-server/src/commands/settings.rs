//! Global + group settings commands.

use std::path::PathBuf;

use serde_json::{json, Value};

use loom_core::delta::DeltaOp;
use loom_core::state::{GlobalSettings, GroupSettings};

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

pub async fn get_config(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = optional_str(req, "group").unwrap_or("");
    let st = ctx.state.lock().await;
    let group_settings = st.group_settings.get(group).cloned().unwrap_or_default();
    let (current_path, current_profile, group_cells) = current_context_payload(&st, group);
    let resolved_agent_defaults = resolved_agent_defaults(&group_settings);
    drop(st);
    let templates = crate::commands::templates::list_templates(ctx, req)
        .await?
        .get("templates")
        .cloned()
        .unwrap_or_else(|| json!([]));
    Ok(json!({
        "type": "config",
        "profiles": ["Default"],
        "current_path": current_path,
        "current_profile": current_profile,
        "group_cells": group_cells,
        "group_settings": group_settings,
        "resolved_agent_defaults": resolved_agent_defaults,
        "providers": provider_payload(),
        "templates": templates,
        "playbooks": [],
        "runtime": runtime_payload(ctx.pty.is_some()),
        "default_command": loom_core::config::default_command(),
        "global_settings": ctx.state.lock().await.global_settings.clone(),
        "port": runtime_port(),
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
    drop(st);
    let templates = crate::commands::templates::list_templates(ctx, req)
        .await?
        .get("templates")
        .cloned()
        .unwrap_or_else(|| json!([]));
    let actions = crate::commands::actions::list_actions(ctx, req)
        .await?
        .get("actions")
        .cloned()
        .unwrap_or_else(|| json!([]));
    Ok(json!({
        "type": "group_settings",
        "group": group,
        "settings": settings,
        "weaver_settings": ctx.state.lock().await.weaver_settings.get(group).cloned().unwrap_or_default(),
        "resolved_agent_defaults": resolved_agent_defaults(&settings),
        "profiles": ["Default"],
        "providers": provider_payload(),
        "templates": templates,
        "actions": actions,
        "playbooks": [],
        "runtime": runtime_payload(ctx.pty.is_some()),
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

fn runtime_payload_with_embedded(embedded_terminal: bool) -> Value {
    json!({
        "standalone": true,
        "embedded_terminal": embedded_terminal,
        "layout": if embedded_terminal { "ide" } else { "classic" },
        "terminal_backend": "pty",
        "home_directory": dirs::home_dir().map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
        "profile": std::env::var("LOOM_PROFILE").unwrap_or_default(),
        "data_dir": loom_core::config::data_dir().to_string_lossy().to_string(),
        "desktop_shell": std::env::var("LOOM_DESKTOP_SHELL").unwrap_or_default(),
        "port": runtime_port(),
        "default_command": loom_core::config::default_command(),
    })
}

fn runtime_payload(embedded_terminal: bool) -> Value {
    runtime_payload_with_embedded(embedded_terminal)
}

fn runtime_port() -> u16 {
    std::env::var("LOOM_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(loom_core::config::DEFAULT_PORT)
}

fn current_context_payload(
    st: &loom_core::state::MatrixState,
    group: &str,
) -> (String, String, Vec<Value>) {
    let mut current_path = String::new();
    let mut current_profile = std::env::var("LOOM_PROFILE").unwrap_or_else(|_| "Default".into());
    let mut group_cells = Vec::new();

    for agent_id in st.groups.get(group).cloned().unwrap_or_default() {
        let Some(cell) = st.agents.get(&agent_id) else { continue };
        let path = if !cell.current_path.is_empty() {
            cell.current_path.clone()
        } else {
            cell.directory.clone()
        };
        if current_path.is_empty() && !path.is_empty() {
            current_path = path.clone();
            if !cell.profile.is_empty() {
                current_profile = cell.profile.clone();
            }
        }
        if cell.session_id.is_some() && !path.is_empty() {
            group_cells.push(json!({
                "id": cell.id,
                "name": cell.name,
                "current_path": path,
            }));
        }
    }

    if current_path.is_empty() {
        current_path = std::env::var("LOOM_PROJECT_ROOT")
            .ok()
            .filter(|value| !value.is_empty())
            .or_else(|| {
                std::env::current_dir()
                    .ok()
                    .map(|path| path.to_string_lossy().to_string())
            })
            .unwrap_or_default();
    }

    (current_path, current_profile, group_cells)
}

fn resolved_agent_defaults(settings: &GroupSettings) -> Value {
    let command = if !settings.agent_boot_command.is_empty() {
        settings.agent_boot_command.clone()
    } else {
        loom_core::config::default_command()
    };
    let provider = if !settings.agent_provider.is_empty() {
        settings.agent_provider.clone()
    } else {
        loom_adapters::registry::detect_by_command(&command)
            .unwrap_or("")
            .to_string()
    };
    json!({
        "provider": provider,
        "command": command,
        "model": settings.agent_model,
        "reasoning_effort": settings.agent_reasoning_effort,
        "directory": first_nonempty([
            settings.agent_directory.clone(),
            settings.default_directory.clone(),
            project_root_or_cwd(),
        ]),
        "profile": first_nonempty([
            settings.agent_profile.clone(),
            settings.profile.clone(),
            "Default".to_string(),
        ]),
        "shell": first_nonempty([
            settings.agent_shell.clone(),
            settings.shell.clone(),
            std::env::var("SHELL").unwrap_or_default(),
        ]),
        "tab_color": first_nonempty([settings.agent_tab_color.clone(), settings.tab_color.clone()]),
        "env_vars": if settings.agent_env_vars.is_empty() { settings.env_vars.clone() } else { settings.agent_env_vars.clone() },
        "env_file": first_nonempty([settings.agent_env_file.clone(), settings.env_file.clone()]),
        "template": settings.default_agent_template,
        "worktree": settings.git_worktree,
        "worktree_base_dir": settings.worktree_base_dir,
        "worktree_base_branch": settings.worktree_base_branch,
        "worktree_auto_checkpoint": settings.worktree_auto_checkpoint,
        "checkpoint_on_progress": settings.checkpoint_on_progress,
        "worktree_merge_squash": settings.worktree_merge_squash,
        "session_resume": settings.agent_session_resume,
        "idle_timeout": settings.agent_idle_timeout,
        "icon": "",
    })
}

fn first_nonempty<const N: usize>(values: [String; N]) -> String {
    values
        .into_iter()
        .find(|value| !value.is_empty())
        .unwrap_or_default()
}

fn project_root_or_cwd() -> String {
    std::env::var("LOOM_PROJECT_ROOT")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| {
            std::env::current_dir()
                .ok()
                .map(PathBuf::into_os_string)
                .and_then(|value| value.into_string().ok())
        })
        .unwrap_or_default()
}
