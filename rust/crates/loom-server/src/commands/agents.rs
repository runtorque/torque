//! Agent + terminal CRUD commands.

use std::collections::BTreeMap;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Map, Value};
use tracing::warn;
use uuid::Uuid;

use loom_core::state::{AgentCell, GroupSettings, WeaverSettings};

use loom_core::delta::DeltaOp;

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};
use crate::terminal_bridge::{bridge_manages_cell, CreateSessionOptions};

pub async fn add_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let group = required_str(req, "group")?.to_string();
    let bridge_configured = ctx.terminal_bridge.is_configured();
    let is_weaver = req
        .get("is_weaver")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let (group_settings, weaver_settings, default_command, existing_weaver_name) = {
        let st = ctx.state.lock().await;
        let group_settings = st.group_settings.get(&group).cloned().unwrap_or_default();
        let weaver_settings = if is_weaver {
            Some(st.get_weaver_settings(&group))
        } else {
            None
        };
        let existing_weaver_name = if is_weaver && !group_settings.weaver_agent_id.is_empty() {
            st.agents
                .get(&group_settings.weaver_agent_id)
                .map(|agent| agent.name.clone())
                .or_else(|| Some("unknown".to_string()))
        } else {
            None
        };
        (
            group_settings,
            weaver_settings,
            default_command_from_global(&st.global_settings.default_command),
            existing_weaver_name,
        )
    };
    if let Some(existing_name) = existing_weaver_name {
        return Err(CmdError::BadRequest(format!(
            "Group '{group}' already has a weaver: {existing_name}"
        )));
    }
    let effective_req = effective_agent_request(req, weaver_settings.as_ref());
    let action_system_prompt = effective_req
        .get("system_prompt")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    let mut cell = AgentCell::new(Uuid::new_v4().to_string(), &name, &group);
    cell.cell_type = "agent".into();
    apply_common_fields(&mut cell, &effective_req);
    apply_agent_defaults(&mut cell, &group_settings, &default_command, &effective_req);
    let weaver_prompt = if is_weaver {
        build_weaver_prompt(
            &group,
            &group_settings,
            weaver_settings
                .as_ref()
                .expect("weaver settings should exist"),
            &action_system_prompt,
        )
    } else {
        String::new()
    };
    // The first typed chat message for a fresh weaver: only the custom
    // instructions (if any), otherwise a short "start working" hint.
    // The full system prompt is delivered via
    // `--append-system-prompt-file`, so repeating it here would just
    // waste context.
    let weaver_first_message = if is_weaver {
        build_weaver_first_message(weaver_settings.as_ref())
    } else {
        String::new()
    };
    let mut bridge_options = build_agent_create_session_options(&group_settings, &effective_req);
    // Populate `system_prompt` for the iTerm2 Python bridge. The bridge
    // writes the value to `.loom/...` and appends
    // `--append-system-prompt-file` on its side, matching the LocalPty
    // path's behavior. Weavers get the weaver-role prompt; other agents
    // get the worker-role prompt. Previously only weavers were handled,
    // so bridge-managed workers shipped with no system prompt at all
    // and had no Loom MCP reporting guidance.
    bridge_options.system_prompt = if !weaver_prompt.is_empty() {
        weaver_prompt.clone()
    } else {
        loom_core::prompts::build_dispatch_persistent_prompt(&action_system_prompt)
    };
    if let Some(bk) = req.get("terminal_backend").and_then(|v| v.as_str()) {
        cell.terminal_backend = bk.to_string();
    } else if bridge_configured {
        cell.terminal_backend = "iterm2".into();
    }
    let agent_id = cell.id.clone();

    let (final_cell, designated_group_settings) = {
        let mut st = ctx.state.lock().await;
        st.add_agent(cell)?;
        let designated_group_settings = if is_weaver {
            let mut settings = st.get_group_settings(&group);
            settings.weaver_agent_id = agent_id.clone();
            let fields = value_to_object_map(&settings)?;
            st.group_settings.insert(group.clone(), settings.clone());
            st.emit(DeltaOp::GroupSettingsUpdate {
                name: group.clone(),
                fields,
            });
            Some(settings)
        } else {
            None
        };
        (
            st.agents.get(&agent_id).cloned().unwrap(),
            designated_group_settings,
        )
    };
    let final_cell = maybe_create_bridge_session(ctx, final_cell, &bridge_options).await?;
    // Auto-create a worktree if group settings enable it and the agent is
    // not a weaver (weavers always operate on the repo root). Mirrors
    // server_agent.py's behavior. Errors here are logged and skipped — the
    // agent still boots in the original directory.
    let final_cell = if !is_weaver
        && group_settings.git_worktree
        && effective_req
            .get("worktree")
            .and_then(Value::as_bool)
            .unwrap_or(true)
    {
        try_create_agent_worktree(ctx, final_cell, &group_settings).await
    } else {
        final_cell
    };
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
    if let Some(settings) = designated_group_settings.as_ref() {
        ctx.db.save_group_settings(&group, settings).await?;
    }
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
    // Fire the initial prompt delivery in the background — it waits up to
    // 30s for Claude Code's "ready" screen before typing. Blocking
    // `add_agent` on that would freeze the caller (and every queued WS
    // command) for the full duration, manifesting as a seemingly-dead UI
    // while the weaver boots.
    if is_weaver && !weaver_first_message.is_empty() {
        let ctx_spawn = ctx.clone();
        let cell_spawn = final_cell.clone();
        let prompt_spawn = weaver_first_message.clone();
        tokio::spawn(async move {
            if let Err(err) = deliver_agent_prompt(&ctx_spawn, &cell_spawn, &prompt_spawn).await {
                tracing::warn!(?err, agent_id = %cell_spawn.id, "weaver first-message delivery failed");
            }
        });
    }
    flush(ctx).await;
    Ok(json!({ "ok": true, "agent_id": final_cell.id, "slug": final_cell.slug }))
}

/// Best-effort worktree bootstrap. Called during `add_agent` when the group's
/// `git_worktree` setting is true. Silently no-ops for agents whose directory
/// isn't inside a git repo. On success, mutates the agent in-state + DB to
/// point `directory` at the fresh worktree path and records the
/// `worktree_path`, `worktree_branch`, `worktree_repo_root` fields.
pub(crate) async fn try_create_agent_worktree(
    ctx: &CmdContext,
    cell: AgentCell,
    group_settings: &GroupSettings,
) -> AgentCell {
    use std::path::{Path, PathBuf};

    if cell.directory.trim().is_empty() {
        return cell;
    }
    let start = PathBuf::from(crate::paths::expand_user_path_string(&cell.directory));
    let repo_root = find_git_root(&start);
    let Some(repo_root) = repo_root else {
        tracing::debug!(agent = %cell.name, "skipping worktree: not inside a git repo");
        return cell;
    };

    let base_dir = if !group_settings.worktree_base_dir.is_empty() {
        let candidate = crate::paths::expand_user_path(&group_settings.worktree_base_dir);
        if candidate.is_absolute() {
            candidate
        } else {
            repo_root.join(candidate)
        }
    } else {
        repo_root.join(".loom").join("worktrees")
    };
    let base_branch = group_settings.worktree_base_branch.clone();

    // Branch name: `loom/<sanitized-name>-<short-id>` — stable and
    // collision-resistant. Git branches reject `:` (used in our slug format)
    // and many other characters, so we sanitize aggressively: keep
    // [A-Za-z0-9._-] and replace everything else with `-`.
    let short_id: String = cell.id.chars().take(8).collect();
    let raw_name = if cell.name.is_empty() { &cell.slug } else { &cell.name };
    let sanitized: String = raw_name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-' {
            c
        } else {
            '-'
        })
        .collect();
    let branch = format!("loom/{}-{}", sanitized.trim_matches('-'), short_id);

    let mgr = loom_worktree::manager::WorktreeManager::new(&repo_root, Some(base_dir));
    let info = match mgr.create(&branch, &base_branch).await {
        Ok(info) => info,
        Err(err) => {
            tracing::warn!(agent = %cell.name, error = %err, "worktree create failed; agent will run in original directory");
            return cell;
        }
    };
    let _ = loom_worktree::gitignore::ensure_git_exclude(&repo_root);

    // Persist the worktree fields and swap `directory`.
    let agent_id = cell.id.clone();
    let mut st = ctx.state.lock().await;
    if let Some(updated) = st.agents.get_mut(&agent_id) {
        updated.worktree_path = info.path.to_string_lossy().to_string();
        updated.worktree_branch = info.branch.clone();
        updated.worktree_repo_root = info.repo_root.to_string_lossy().to_string();
        updated.worktree_base_branch = info.base_branch.clone();
        updated.directory = info.path.to_string_lossy().to_string();
        st.emit_agent(&agent_id);
        return st.agents.get(&agent_id).cloned().unwrap_or(cell);
    }
    drop(st);
    let _ = Path::new("");
    cell
}

/// Walk up from `start` looking for a `.git` directory or file. Returns the
/// first ancestor that is a git repo root, or None.
fn find_git_root(start: &std::path::Path) -> Option<std::path::PathBuf> {
    let mut cur = start.to_path_buf();
    for _ in 0..32 {
        if cur.join(".git").exists() {
            return Some(cur);
        }
        if !cur.pop() {
            break;
        }
    }
    None
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
    let (group, removed, removed_cells, updated_group_settings) = {
        let mut st = ctx.state.lock().await;
        let group = st
            .agents
            .get(&id)
            .map(|a| a.group.clone())
            .ok_or_else(|| CmdError::BadRequest(format!("agent '{id}' not found")))?;
        let clears_weaver = st.get_group_settings(&group).weaver_agent_id == id;
        let removed_cells = collect_removed_cells(&st, &id);
        let removed = st.remove_agent(&id)?;
        let updated_group_settings = if clears_weaver {
            Some(st.get_group_settings(&group))
        } else {
            None
        };
        (group, removed, removed_cells, updated_group_settings)
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
    // Force-remove each removed cell's worktree + branch. Matches
    // Python's `remove_agent` handler (`loom/server.py:2897-2903`)
    // which calls `_safe_remove_worktree(c)` for every cascade-closed
    // cell. Without this loop the branches + `.loom/worktrees/<name>/`
    // directories leak on every agent close — the user ends up with
    // hundreds of orphaned worktrees polluting `git worktree list` and
    // occupying disk space.
    for cell in &removed_cells {
        if cell.cell_type == "agent" && !cell.worktree_path.is_empty() {
            let _ = crate::commands::worktree::safe_remove_for_cell(ctx, cell).await;
        }
    }
    for rid in &removed {
        ctx.db.delete_agent(rid).await?;
    }
    if let Some(settings) = updated_group_settings.as_ref() {
        ctx.db.save_group_settings(&group, settings).await?;
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
        let mut new_cell: AgentCell =
            serde_json::from_value(current).map_err(|e| CmdError::BadRequest(e.to_string()))?;
        if !new_cell.directory.is_empty() {
            new_cell.directory = crate::paths::expand_user_path_string(&new_cell.directory);
        }
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
        cell.directory = crate::paths::expand_user_path_string(dir);
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

fn effective_agent_request(req: &Value, weaver_settings: Option<&WeaverSettings>) -> Value {
    let mut merged = req.clone();
    let Some(ws) = weaver_settings else {
        return merged;
    };
    let Some(obj) = merged.as_object_mut() else {
        return merged;
    };
    insert_string_override(obj, "provider", &ws.weaver_provider);
    insert_string_override(obj, "command", &ws.weaver_boot_command);
    insert_string_override(obj, "model", &ws.weaver_model);
    insert_string_override(obj, "reasoning_effort", &ws.weaver_reasoning_effort);
    insert_string_override(obj, "directory", &ws.weaver_directory);
    insert_string_override(obj, "profile", &ws.weaver_profile);
    insert_string_override(obj, "shell", &ws.weaver_shell);
    insert_string_override(obj, "tab_color", &ws.weaver_tab_color);
    merged
}

fn insert_string_override(map: &mut Map<String, Value>, key: &str, value: &str) {
    if !value.trim().is_empty() {
        map.insert(key.to_string(), Value::String(value.to_string()));
    }
}

fn value_to_object_map<T: Serialize>(value: &T) -> Result<Map<String, Value>, CmdError> {
    serde_json::to_value(value)?
        .as_object()
        .cloned()
        .ok_or_else(|| CmdError::BadRequest("expected object payload".into()))
}

pub(crate) fn build_weaver_prompt(
    group: &str,
    group_settings: &GroupSettings,
    weaver_settings: &WeaverSettings,
    action_system_prompt: &str,
) -> String {
    loom_weaver::weaver::build_weaver_system_prompt(
        group,
        Some(weaver_settings),
        action_system_prompt,
        Some(group_settings),
    )
}

/// First user-visible chat message for a freshly-booted weaver. The full
/// role/policy system prompt is delivered via `--append-system-prompt-file`,
/// so the first message shouldn't re-type it. When the operator set
/// weaver-specific custom instructions, that's what the weaver sees; when
/// they didn't, send a short kickoff hint so Claude Code starts its first
/// turn instead of sitting idle.
pub(crate) fn build_weaver_first_message(weaver_settings: Option<&WeaverSettings>) -> String {
    let custom = weaver_settings
        .map(|ws| ws.custom_instructions.trim().to_string())
        .unwrap_or_default();
    if !custom.is_empty() {
        return custom;
    }
    // Mirrors the wording used by the Python weaver's boot hint; kept
    // short so it doesn't look like an instructions dump to the operator
    // reading the transcript.
    "You're the weaver for this group. \
     Wait for a digest of panel events, then coordinate based on what you see. \
     Use `loom_context()` any time if you need to refresh your view of the board."
        .to_string()
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
        cell.directory = crate::paths::expand_user_path_string(&first_nonempty([
            settings.agent_directory.clone(),
            settings.default_directory.clone(),
            project_root_fallback(),
        ]));
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

pub(crate) async fn deliver_agent_prompt(
    ctx: &CmdContext,
    cell: &AgentCell,
    prompt: &str,
) -> Result<(), CmdError> {
    if prompt.trim().is_empty() {
        return Ok(());
    }

    tokio::time::sleep(Duration::from_millis(2000)).await;

    if ctx.ui_agents.is_attached(&cell.id) {
        ctx.ui_agents.send_text(&cell.id, prompt.to_string());
        tokio::time::sleep(Duration::from_millis(50)).await;
        ctx.ui_agents.send_submit(&cell.id);
        return Ok(());
    }

    if bridge_manages_cell(&ctx.terminal_bridge, cell) {
        if cell.session_id.is_none() {
            return Err(CmdError::BadRequest(format!(
                "agent '{}' has no live terminal bridge session",
                cell.id
            )));
        }
        ctx.terminal_bridge
            .send_text(&cell.id, cell.session_id.as_deref(), prompt, false)
            .await
            .map_err(|err| CmdError::BadRequest(err.to_string()))?;
        return Ok(());
    }

    let Some(pty) = ctx.pty.as_ref() else {
        return Err(CmdError::BadRequest(format!(
            "agent '{}' has no live delivery path",
            cell.id
        )));
    };
    let _ = pty;
    crate::commands::dispatch::send_prompt_to_local_pty(ctx, cell, prompt, true).await?;
    Ok(())
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
        cell.directory = crate::paths::expand_user_path_string(&first_nonempty([
            parent_worktree.to_string(),
            settings.terminal_directory.clone(),
            settings.default_directory.clone(),
            project_root_fallback(),
        ]));
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
    crate::paths::expand_user_path_string(
        &std::env::var("LOOM_PROJECT_ROOT")
            .ok()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| crate::app::repo_root().to_string_lossy().to_string()),
    )
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
