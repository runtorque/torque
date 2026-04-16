//! Agent + terminal CRUD commands.

use std::collections::BTreeMap;

use serde_json::{json, Value};
use tracing::warn;
use uuid::Uuid;

use loom_core::state::AgentCell;

use loom_core::delta::DeltaOp;

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};
use crate::terminal_bridge::{bridge_manages_cell, CreateSessionOptions};

pub async fn add_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let group = required_str(req, "group")?.to_string();
    let bridge_configured = ctx.terminal_bridge.is_configured();

    let mut cell = AgentCell::new(Uuid::new_v4().to_string(), &name, &group);
    cell.cell_type = "agent".into();
    apply_common_fields(&mut cell, req);
    let bridge_options = {
        let st = ctx.state.lock().await;
        let settings = st.group_settings.get(&group).cloned().unwrap_or_default();
        let default_command = default_command_from_global(&st.global_settings.default_command);
        apply_agent_defaults(&mut cell, &settings, &default_command, req);
        build_agent_create_session_options(&settings, req)
    };
    if let Some(bk) = req.get("terminal_backend").and_then(|v| v.as_str()) {
        cell.terminal_backend = bk.to_string();
    } else if bridge_configured {
        cell.terminal_backend = "iterm2".into();
    }
    let agent_id = cell.id.clone();

    let final_cell = {
        let mut st = ctx.state.lock().await;
        st.add_agent(cell)?;
        st.agents.get(&agent_id).cloned().unwrap()
    };
    let final_cell = maybe_create_bridge_session(ctx, final_cell, &bridge_options).await?;
    ctx.db.save_agent(&final_cell).await?;
    ctx.db
        .save_agent_history_record(
            &final_cell.id,
            &final_cell.name,
            &final_cell.slug,
            &final_cell.group,
            &final_cell.agent_type,
            &final_cell.template,
            now_ts(),
            None,
            &final_cell.worktree_branch,
            0,
            0,
            0,
            "active",
        )
        .await?;
    persist_group_members(ctx, &group).await?;
    if !bridge_manages_cell(&ctx.terminal_bridge, &final_cell) {
        crate::commands::dispatch::spawn_cell_session(
            ctx,
            &final_cell,
            Some(&bridge_options.env_vars),
        )
        .await?;
    }
    if bridge_bootstrap_failed(&final_cell) {
        flush(ctx).await;
        return Err(CmdError::BadRequest(final_cell.error_message.clone()));
    }
    flush(ctx).await;
    Ok(json!({ "ok": true, "agent_id": final_cell.id, "slug": final_cell.slug }))
}

pub async fn add_terminal(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let group = required_str(req, "group")?.to_string();
    let bridge_configured = ctx.terminal_bridge.is_configured();

    let mut cell = AgentCell::new(Uuid::new_v4().to_string(), &name, &group);
    cell.cell_type = "terminal".into();
    if let Some(pid) = optional_str(req, "parent_id") {
        cell.parent_id = pid.to_string();
    }
    apply_common_fields(&mut cell, req);
    let launch_env = {
        let st = ctx.state.lock().await;
        let settings = st.group_settings.get(&group).cloned().unwrap_or_default();
        let parent_worktree = cell
            .parent_id
            .as_str()
            .is_empty()
            .then_some(String::new())
            .unwrap_or_else(|| {
                st.agents
                    .get(&cell.parent_id)
                    .map(|parent| parent.worktree_path.clone())
                    .unwrap_or_default()
            });
        apply_terminal_defaults(&mut cell, &settings, req, &parent_worktree)
    };
    if let Some(bk) = req.get("terminal_backend").and_then(|v| v.as_str()) {
        cell.terminal_backend = bk.to_string();
    } else if bridge_configured {
        cell.terminal_backend = "iterm2".into();
    }
    let agent_id = cell.id.clone();

    let final_cell = {
        let mut st = ctx.state.lock().await;
        st.add_agent(cell)?;
        st.agents.get(&agent_id).cloned().unwrap()
    };
    let final_cell =
        maybe_create_bridge_session(ctx, final_cell, &CreateSessionOptions::default()).await?;
    ctx.db.save_agent(&final_cell).await?;
    ctx.db
        .save_agent_history_record(
            &final_cell.id,
            &final_cell.name,
            &final_cell.slug,
            &final_cell.group,
            &final_cell.agent_type,
            &final_cell.template,
            now_ts(),
            None,
            &final_cell.worktree_branch,
            0,
            0,
            0,
            "active",
        )
        .await?;
    persist_group_members(ctx, &group).await?;
    if !bridge_manages_cell(&ctx.terminal_bridge, &final_cell) {
        crate::commands::dispatch::spawn_cell_session(ctx, &final_cell, Some(&launch_env)).await?;
    }
    flush(ctx).await;
    Ok(json!({ "ok": true, "agent_id": final_cell.id, "slug": final_cell.slug }))
}

pub async fn remove_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let (group, removed, removed_cells) = {
        let mut st = ctx.state.lock().await;
        let group = st
            .agents
            .get(&id)
            .map(|a| a.group.clone())
            .ok_or_else(|| CmdError::BadRequest(format!("agent '{id}' not found")))?;
        let removed_cells = collect_removed_cells(&st, &id);
        let removed = st.remove_agent(&id)?;
        (group, removed, removed_cells)
    };
    for cell in &removed_cells {
        if bridge_manages_cell(&ctx.terminal_bridge, cell) && cell.session_id.is_some() {
            if let Err(err) = ctx
                .terminal_bridge
                .close_session(&cell.id, cell.session_id.as_deref())
                .await
            {
                warn!(?err, agent_id = %cell.id, "terminal bridge close_session failed");
            }
        }
    }
    for rid in &removed {
        ctx.db.delete_agent(rid).await?;
    }
    for cell in removed_cells {
        let mut fields = serde_json::Map::new();
        fields.insert("removed_at".into(), json!(now_ts()));
        fields.insert("status".into(), json!("removed"));
        fields.insert(
            "total_tokens_in".into(),
            json!(cell.session_tokens_in.max(0)),
        );
        fields.insert(
            "total_tokens_out".into(),
            json!(cell.session_tokens_out.max(0)),
        );
        let _ = ctx.db.update_agent_history_fields(&cell.id, &fields).await;
    }
    persist_group_members(ctx, &group).await?;
    persist_selection(ctx).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "removed": removed }))
}

/// Mirror the in-memory `selected_agent_id` to the `ui_state` table. Call after
/// cascade-removing agents/groups, since selection may have been auto-cleared.
pub(crate) async fn persist_selection(ctx: &CmdContext) -> Result<(), CmdError> {
    let value = {
        let st = ctx.state.lock().await;
        st.selected_agent_id.clone().unwrap_or_default()
    };
    ctx.db.set_ui_state("selected_agent_id", &value).await?;
    Ok(())
}

pub async fn update_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let patch = req.get("fields").cloned().unwrap_or(Value::Null);

    let (agent, group, old_name) = {
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
        (agent, group, old_name)
    };

    let agent = maybe_update_bridge_session(ctx, agent, &old_name).await?;
    ctx.db.save_agent(&agent).await?;
    let mut history_fields = serde_json::Map::new();
    history_fields.insert("name".into(), json!(agent.name));
    history_fields.insert("slug".into(), json!(agent.slug));
    history_fields.insert("group".into(), json!(agent.group));
    history_fields.insert("agent_type".into(), json!(agent.agent_type));
    history_fields.insert("template".into(), json!(agent.template));
    let _ = ctx
        .db
        .update_agent_history_fields(&agent.id, &history_fields)
        .await;
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
    let mut history_fields = serde_json::Map::new();
    history_fields.insert("group".into(), json!(agent.group));
    history_fields.insert("slug".into(), json!(agent.slug));
    let _ = ctx
        .db
        .update_agent_history_fields(&agent.id, &history_fields)
        .await;
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
            st.children
                .entry(new_parent.clone())
                .or_default()
                .push(id.clone());
        }
        // regenerate slug
        let name = cell.name.clone();
        let group = cell.group.clone();
        let parent_ref = if new_parent.is_empty() {
            None
        } else {
            Some(new_parent.as_str())
        };
        let new_slug = st.make_agent_slug(&name, parent_ref, &group);
        if let Some(a) = st.agents.get_mut(&id) {
            a.slug = new_slug;
        }
        st.emit_agent(&id);
        (group, st.agents.get(&id).cloned().unwrap())
    };

    ctx.db.save_agent(&agent).await?;
    let mut history_fields = serde_json::Map::new();
    history_fields.insert("slug".into(), json!(agent.slug));
    let _ = ctx
        .db
        .update_agent_history_fields(&agent.id, &history_fields)
        .await;
    let _ = group;
    flush(ctx).await;
    ok()
}

/// Reset an agent's session-level context: clears dispatched-tasks counter,
/// clears the linked task, and drops the session id so the next dispatch
/// treats it as a fresh boot. Does not kill the PTY — use `relaunch_agent`
/// for that.
pub async fn clear_agent_context(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let agent = {
        let mut st = ctx.state.lock().await;
        if !st.agents.contains_key(&id) {
            return Err(CmdError::BadRequest(format!("agent '{id}' not found")));
        }
        if let Some(cell) = st.agents.get_mut(&id) {
            cell.tasks_dispatched = 0;
            cell.current_task_id.clear();
            cell.session_id = None;
            cell.activity.clear();
            cell.activity_detail.clear();
            cell.error_message.clear();
            cell.needs_attention = false;
            cell.last_summary.clear();
        }
        st.emit_agent(&id);
        st.agents.get(&id).cloned().unwrap()
    };
    ctx.db.save_agent(&agent).await?;
    flush(ctx).await;
    ok()
}

pub async fn focus_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let selected_root = {
        let mut st = ctx.state.lock().await;
        let cell = st
            .agents
            .get(&id)
            .cloned()
            .ok_or_else(|| CmdError::BadRequest(format!("agent '{id}' not found")))?;
        let selected_root = if cell.cell_type == "terminal" && !cell.parent_id.is_empty() {
            Some(cell.parent_id.clone())
        } else {
            Some(cell.id.clone())
        };
        st.select_agent(selected_root.as_deref())
            .map_err(|e| CmdError::BadRequest(e.to_string()))?;
        st.active_session_id = cell.session_id.clone();
        st.current_window_id = if cell.window_id.is_empty() {
            None
        } else {
            Some(cell.window_id.clone())
        };
        let active_session_id = st.active_session_id.clone();
        let current_window_id = st.current_window_id.clone();
        st.emit(DeltaOp::FocusUpdate {
            active_session_id,
            current_window_id,
        });
        selected_root
    };
    let stored = selected_root.unwrap_or_default();
    ctx.db.set_ui_state("selected_agent_id", &stored).await?;
    flush(ctx).await;
    ok()
}

/// Set (or clear) the UI's selected agent. Persists to `ui_state` and emits a
/// `ui_update` delta. Passing `id: null` (or omitting it) clears selection.
pub async fn select_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = req.get("id").and_then(|v| match v {
        Value::Null => None,
        Value::String(s) if s.is_empty() => None,
        Value::String(s) => Some(s.clone()),
        _ => None,
    });
    {
        let mut st = ctx.state.lock().await;
        st.select_agent(id.as_deref())?;
    }
    // persist
    let stored = id.clone().unwrap_or_default();
    ctx.db.set_ui_state("selected_agent_id", &stored).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "selected_agent_id": id }))
}

pub async fn reorder_child(ctx: &CmdContext, req: &Value) -> CmdResult {
    let parent_id = required_str(req, "parent_id")?.to_string();
    let order: Vec<String> = req
        .get("order")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let group = {
        let mut st = ctx.state.lock().await;
        if !st.children.contains_key(&parent_id) && !st.agents.contains_key(&parent_id) {
            return Err(CmdError::BadRequest(format!(
                "parent '{parent_id}' not found"
            )));
        }
        st.children.insert(parent_id.clone(), order);
        st.agents
            .get(&parent_id)
            .map(|a| a.group.clone())
            .unwrap_or_default()
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

pub(crate) fn resolve_provider_command(
    provider: &str,
    boot_command: &str,
    default_command: &str,
) -> (String, String) {
    if !provider.trim().is_empty() {
        let adapter_cmd = loom_adapters::registry::get_default_command_for_provider(provider);
        if !adapter_cmd.is_empty() {
            return (
                if boot_command.trim().is_empty() {
                    adapter_cmd
                } else {
                    boot_command.to_string()
                },
                provider.to_string(),
            );
        }
    }
    (
        if boot_command.trim().is_empty() {
            default_command.to_string()
        } else {
            boot_command.to_string()
        },
        String::new(),
    )
}

pub(crate) fn resolve_agent_launch_command(
    provider: &str,
    boot_command: &str,
    default_command: &str,
    model: &str,
    reasoning_effort: &str,
) -> (String, String) {
    let (mut command, resolved_provider) =
        resolve_provider_command(provider, boot_command, default_command);
    let effective_provider = if !resolved_provider.is_empty() {
        resolved_provider
    } else if !provider.trim().is_empty() {
        provider.to_string()
    } else {
        loom_adapters::registry::detect_by_command(&command)
            .unwrap_or("")
            .to_string()
    };
    if boot_command.trim().is_empty() && !effective_provider.is_empty() {
        if let Some(adapter) = loom_adapters::registry::get_adapter(&effective_provider) {
            command.push_str(&adapter.resolve_model_flags(model));
            command.push_str(&adapter.resolve_reasoning_effort_flags(reasoning_effort));
        }
    }
    let agent_type = if !effective_provider.is_empty() {
        effective_provider
    } else {
        loom_adapters::registry::detect_by_command(&command)
            .unwrap_or("")
            .to_string()
    };
    (command, agent_type)
}

pub(crate) fn default_command_from_global(global_default_command: &str) -> String {
    if global_default_command.trim().is_empty() {
        loom_core::config::default_command()
    } else {
        global_default_command.to_string()
    }
}

pub(crate) fn apply_agent_defaults(
    cell: &mut AgentCell,
    settings: &loom_core::state::GroupSettings,
    default_command: &str,
    req: &Value,
) {
    if cell.directory.is_empty() {
        cell.directory = first_nonempty([
            settings.agent_directory.clone(),
            settings.default_directory.clone(),
            project_root_fallback(),
        ]);
    }
    if cell.profile == "Default" {
        cell.profile = first_nonempty([
            settings.agent_profile.clone(),
            settings.profile.clone(),
            "Default".to_string(),
        ]);
    }
    if cell.tab_color.is_empty() {
        cell.tab_color =
            first_nonempty([settings.agent_tab_color.clone(), settings.tab_color.clone()]);
    }
    if cell.template.is_empty() && !settings.default_agent_template.is_empty() {
        cell.template = settings.default_agent_template.clone();
    }
    let provider = req
        .get("provider")
        .and_then(|v| v.as_str())
        .unwrap_or(&settings.agent_provider);
    let model = req
        .get("model")
        .and_then(|v| v.as_str())
        .unwrap_or(&settings.agent_model);
    let reasoning_effort = req
        .get("reasoning_effort")
        .and_then(|v| v.as_str())
        .unwrap_or(&settings.agent_reasoning_effort);
    let raw_command = first_nonempty([cell.command.clone(), settings.agent_boot_command.clone()]);
    let (command, agent_type) = resolve_agent_launch_command(
        provider,
        &raw_command,
        default_command,
        model,
        reasoning_effort,
    );
    cell.command = command;
    if !agent_type.is_empty() {
        cell.agent_type = agent_type;
    }
    cell.terminal_backend = "pty".into();
}

fn build_agent_create_session_options(
    settings: &loom_core::state::GroupSettings,
    req: &Value,
) -> CreateSessionOptions {
    CreateSessionOptions {
        env_vars: merged_env(
            &settings.env_vars,
            &settings.agent_env_vars,
            req.get("env_vars").and_then(|v| v.as_object()),
        ),
        env_file: first_nonempty([
            optional_str(req, "env_file").unwrap_or("").to_string(),
            settings.agent_env_file.clone(),
            settings.env_file.clone(),
        ]),
        shell: first_nonempty([
            optional_str(req, "shell").unwrap_or("").to_string(),
            settings.agent_shell.clone(),
            settings.shell.clone(),
            std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into()),
        ]),
        ..Default::default()
    }
}

fn apply_terminal_defaults(
    cell: &mut AgentCell,
    settings: &loom_core::state::GroupSettings,
    req: &Value,
    parent_worktree: &str,
) -> BTreeMap<String, String> {
    if cell.profile == "Default" {
        cell.profile = first_nonempty([
            optional_str(req, "profile").unwrap_or("").to_string(),
            settings.terminal_profile.clone(),
            settings.profile.clone(),
            "Default".to_string(),
        ]);
    }
    if cell.directory.is_empty() {
        cell.directory = first_nonempty([
            parent_worktree.to_string(),
            settings.terminal_directory.clone(),
            settings.default_directory.clone(),
            project_root_fallback(),
        ]);
    }
    if cell.tab_color.is_empty() {
        let terminal_color = settings.terminal_tab_color.clone();
        cell.tab_color = if terminal_color == "none" {
            String::new()
        } else {
            first_nonempty([terminal_color, settings.tab_color.clone()])
        };
    }
    let shell = first_nonempty([
        optional_str(req, "shell").unwrap_or("").to_string(),
        settings.terminal_shell.clone(),
        settings.shell.clone(),
        std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into()),
    ]);
    let command_args = first_nonempty([
        optional_str(req, "command_args").unwrap_or("").to_string(),
        settings.terminal_command_args.clone(),
    ]);
    let mut command =
        first_nonempty([cell.command.clone(), settings.terminal_boot_command.clone()]);
    if !command_args.is_empty() {
        command = if command.is_empty() {
            command_args
        } else {
            format!("{command} {command_args}")
        };
    }
    let init_script = first_nonempty([
        optional_str(req, "init_script").unwrap_or("").to_string(),
        settings.terminal_init_script.clone(),
    ]);
    let env_vars = merged_env(
        &settings.env_vars,
        &settings.terminal_env_vars,
        req.get("env_vars").and_then(|v| v.as_object()),
    );
    cell.command = build_terminal_launch_command(&command, &shell, &init_script, &env_vars);
    cell.terminal_backend = "pty".into();
    env_vars
}

pub(crate) fn merged_env(
    base: &BTreeMap<String, String>,
    extra: &BTreeMap<String, String>,
    req: Option<&serde_json::Map<String, Value>>,
) -> BTreeMap<String, String> {
    let mut env = base.clone();
    env.extend(extra.clone());
    if let Some(req) = req {
        for (key, value) in req {
            if let Some(value) = value.as_str() {
                env.insert(key.clone(), value.to_string());
            }
        }
    }
    env
}

fn build_terminal_launch_command(
    command: &str,
    shell: &str,
    init_script: &str,
    env_vars: &BTreeMap<String, String>,
) -> String {
    let launch = if !command.trim().is_empty() {
        command.trim().to_string()
    } else {
        shell.trim().to_string()
    };
    let mut parts = Vec::new();
    for (key, value) in env_vars {
        if key.trim().is_empty() {
            continue;
        }
        parts.push(format!(
            "export {}={}",
            shell_escape(key),
            shell_escape(value)
        ));
    }
    if !init_script.trim().is_empty() {
        parts.push(init_script.trim().to_string());
    }
    if !launch.is_empty() {
        parts.push(format!("exec {launch}"));
    }
    parts.join("\n")
}

fn shell_escape(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn project_root_fallback() -> String {
    std::env::var("LOOM_PROJECT_ROOT")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| crate::app::repo_root().to_string_lossy().to_string())
}

pub(crate) fn first_nonempty<const N: usize>(values: [String; N]) -> String {
    values
        .into_iter()
        .find(|value| !value.is_empty())
        .unwrap_or_default()
}

pub(crate) fn bridge_bootstrap_failed(cell: &AgentCell) -> bool {
    cell.status == "error" && !cell.error_message.trim().is_empty()
}

fn now_ts() -> f64 {
    chrono::Utc::now().timestamp_millis() as f64 / 1000.0
}

async fn persist_group_members(ctx: &CmdContext, group: &str) -> Result<(), CmdError> {
    let members = {
        let st = ctx.state.lock().await;
        st.groups.get(group).cloned().unwrap_or_default()
    };
    ctx.db.save_group_members(group, &members).await?;
    Ok(())
}

async fn maybe_create_bridge_session(
    ctx: &CmdContext,
    cell: AgentCell,
    options: &CreateSessionOptions,
) -> Result<AgentCell, CmdError> {
    if !bridge_manages_cell(&ctx.terminal_bridge, &cell) {
        return Ok(cell);
    }

    let bridged = match ctx.terminal_bridge.create_session(&cell, options).await {
        Ok(Some(updated)) => updated,
        Ok(None) => cell,
        Err(err) => {
            warn!(?err, agent_id = %cell.id, "terminal bridge create_session failed");
            let mut errored = cell;
            errored.status = "error".into();
            errored.error_message = err.to_string();
            errored
        }
    };

    let final_cell = {
        let mut st = ctx.state.lock().await;
        st.agents.insert(bridged.id.clone(), bridged.clone());
        st.emit_agent(&bridged.id);
        st.agents.get(&bridged.id).cloned().unwrap()
    };
    Ok(final_cell)
}

async fn maybe_update_bridge_session(
    ctx: &CmdContext,
    agent: AgentCell,
    old_name: &str,
) -> Result<AgentCell, CmdError> {
    if !bridge_manages_cell(&ctx.terminal_bridge, &agent) || agent.session_id.is_none() {
        return Ok(agent);
    }

    let bridged = match ctx.terminal_bridge.update_session(&agent, old_name).await {
        Ok(Some(updated)) => updated,
        Ok(None) => agent,
        Err(err) => {
            warn!(?err, agent_id = %agent.id, "terminal bridge update_session failed");
            agent
        }
    };
    let final_agent = {
        let mut st = ctx.state.lock().await;
        st.agents.insert(bridged.id.clone(), bridged.clone());
        st.emit_agent(&bridged.id);
        st.agents.get(&bridged.id).cloned().unwrap()
    };
    Ok(final_agent)
}

fn collect_removed_cells(st: &loom_core::state::MatrixState, id: &str) -> Vec<AgentCell> {
    fn walk(st: &loom_core::state::MatrixState, id: &str, out: &mut Vec<AgentCell>) {
        if let Some(cell) = st.agents.get(id).cloned() {
            out.push(cell);
        }
        if let Some(children) = st.children.get(id) {
            for child_id in children {
                walk(st, child_id, out);
            }
        }
    }

    let mut out = Vec::new();
    walk(st, id, &mut out);
    out
}
