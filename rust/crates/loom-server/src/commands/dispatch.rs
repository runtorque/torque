//! Dispatch, send_text, broadcast, and ai_report.

use std::path::Path;
use std::time::Duration;

use serde_json::{json, Value};

use loom_actions::context::LoomContextBuilder;
use loom_core::state::AgentCell;

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

/// Install provider-specific hooks, MCP config, and slash-command skills into
/// `working_dir` for the adapter that matches `boot_command`. Best-effort —
/// failures are logged and don't block dispatch, matching Python parity.
async fn install_provider_integration(working_dir: &Path, boot_command: &str) {
    let Some(provider) = loom_adapters::detect_by_command(boot_command) else {
        return;
    };
    let Some(adapter) = loom_adapters::get_adapter(provider) else {
        return;
    };
    if let Err(err) = adapter.install_hooks(working_dir).await {
        tracing::warn!(?err, ?working_dir, provider, "install_hooks failed");
    }
    if let Err(err) = adapter.install_mcp_config(working_dir).await {
        tracing::warn!(?err, ?working_dir, provider, "install_mcp_config failed");
    }
    if let Err(err) = adapter.install_skills(working_dir).await {
        tracing::warn!(?err, ?working_dir, provider, "install_skills failed");
    }
}

// ---------------------------------------------------------------------------
// dispatch_task
// ---------------------------------------------------------------------------

pub async fn dispatch_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let task_id = required_str(req, "task_id")?.to_string();
    let force_no_action = req
        .get("force_no_action")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // Load task + resolve target agent.
    let (task, target_agent_id, is_new_agent, dispatch_lane) = {
        let mut st = ctx.state.lock().await;
        let Some(task) = st.board_tasks.get(&task_id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };

        let dispatch_lane = st
            .group_settings
            .get(&task.group)
            .map(|g| g.dispatch_lane.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "In Progress".to_string());

        // Resolve agent: existing (task.agent_id) or create new.
        let target_agent_id = if !task.agent_id.is_empty() && st.agents.contains_key(&task.agent_id)
        {
            task.agent_id.clone()
        } else {
            // Create a new agent under the task's group.
            if !st.groups.contains_key(&task.group) {
                return Err(CmdError::BadRequest(format!(
                    "group '{}' not found",
                    task.group
                )));
            }
            let agent_name = format!("{}-worker", task.slug);
            let mut cell = loom_core::state::AgentCell::new(
                uuid::Uuid::new_v4().to_string(),
                &agent_name,
                &task.group,
            );
            cell.cell_type = "agent".into();
            cell.command = loom_core::config::default_command();
            if let Some(dir) = std::env::var("LOOM_PROJECT_ROOT").ok() {
                cell.directory = dir;
            }
            let id = cell.id.clone();
            st.add_agent(cell)?;
            id
        };
        let is_new = !task.agent_id.eq(&target_agent_id) && task.agent_id.is_empty();

        (task, target_agent_id, is_new, dispatch_lane)
    };

    // Render the prompt.
    let rendered = if task.action_name.is_empty() || force_no_action {
        // Fall back to the task's raw text (+ description).
        if task.description.is_empty() {
            task.task.clone()
        } else {
            format!("{}\n\n{}", task.task, task.description)
        }
    } else {
        // Action-based render.
        let project = crate::commands::actions::project_actions_root_pub();
        let user = Some(crate::commands::actions::user_actions_root_pub());
        let mgr = loom_actions::manager::ActionManager::new(project, user);
        match mgr.get_action(&task.action_name) {
            Ok(info) => {
                let mut vars = task.action_vars.clone();
                vars.insert("TASK".into(), Value::String(task.task.clone()));
                let st = ctx.state.lock().await;
                let agent = st.agents.get(&target_agent_id).cloned();
                let loom_ctx = match agent.as_ref() {
                    Some(a) => LoomContextBuilder::new(&st).agent(a).task(&task).build(),
                    None => LoomContextBuilder::new(&st).task(&task).build(),
                };
                drop(st);
                loom_actions::render::render_prompt(&info.prompt, &vars, &loom_ctx)
                    .map_err(|e| CmdError::BadRequest(format!("render: {e}")))?
            }
            Err(loom_actions::manager::ActionError::NotFound(_)) => {
                return Ok(json!({
                    "warning": "dispatch_action_missing",
                    "action": task.action_name,
                    "task_id": task.id,
                }));
            }
            Err(other) => return Err(CmdError::BadRequest(other.to_string())),
        }
    };

    // Provider-specific hook install. Runs before the prompt is sent so the
    // hooks are in place the moment the agent boots (or, for a re-dispatch,
    // so any port change is reflected). Idempotent + best-effort.
    {
        let (cmd, cwd) = {
            let st = ctx.state.lock().await;
            st.agents
                .get(&target_agent_id)
                .map(|a| (a.command.clone(), a.directory.clone()))
                .unwrap_or_default()
        };
        if !cwd.is_empty() {
            let resolved_cmd = if cmd.is_empty() {
                loom_core::config::default_command()
            } else {
                cmd
            };
            let dir = std::path::PathBuf::from(&cwd);
            install_provider_integration(&dir, &resolved_cmd).await;
        }
    }

    // Route: if the agent is UI-attached (a GhosttyView is mounted for it),
    // send through the UI registry — Ghostty owns that agent's PTY. Otherwise
    // fall through to the engine's own LocalPtyBackend.
    if ctx.ui_agents.is_attached(&target_agent_id) {
        if is_new_agent {
            tokio::time::sleep(Duration::from_millis(2000)).await;
        }
        // `send_text` writes to Ghostty's display buffer as IME-style text and
        // is the path the UI currently uses. We send the prompt body, pause
        // briefly so it's treated as a paste, then send the newline separately
        // to land outside bracketed paste mode.
        ctx.ui_agents.send(&target_agent_id, rendered.clone());
        tokio::time::sleep(Duration::from_millis(50)).await;
        ctx.ui_agents.send(&target_agent_id, "\r".to_string());
    } else if let Some(pty) = &ctx_pty(ctx).await {
        let agent = {
            let st = ctx.state.lock().await;
            st.agents.get(&target_agent_id).cloned()
        };
        if let Some(agent) = agent {
            if agent.status != "running" {
                // Boot the agent's command.
                let command = if agent.command.is_empty() {
                    loom_core::config::default_command()
                } else {
                    agent.command.clone()
                };
                let cwd = if agent.directory.is_empty() {
                    None
                } else {
                    Some(std::path::PathBuf::from(&agent.directory))
                };
                let mut env = std::collections::HashMap::new();
                env.insert(loom_core::config::ENV_CELL_ID.to_string(), agent.id.clone());
                let _ = pty.spawn(&agent.id, &command, cwd, env, 40, 120).await;
                // Boot delay — let the agent's prompt appear before we send text.
                if is_new_agent {
                    tokio::time::sleep(Duration::from_millis(2000)).await;
                }
            }
        }

        // Send the prompt + newline (separate write for the newline to escape bracketed paste).
        let _ = pty.write(&target_agent_id, rendered.as_bytes()).await;
        tokio::time::sleep(Duration::from_millis(50)).await;
        let _ = pty.write(&target_agent_id, b"\r").await;
    }

    // Update task + agent state.
    let (task_final, agent_final) = {
        let mut st = ctx.state.lock().await;
        if let Some(t) = st.board_tasks.get_mut(&task_id) {
            t.agent_id = target_agent_id.clone();
            t.lane = dispatch_lane.clone();
            t.lane_entered_at = chrono::Utc::now().to_rfc3339();
            t.updated_at = t.lane_entered_at.clone();
        }
        st.emit_task(&task_id);
        if let Some(a) = st.agents.get_mut(&target_agent_id) {
            a.tasks_dispatched += 1;
            a.current_task_id = task_id.clone();
        }
        st.emit_agent(&target_agent_id);
        (
            st.board_tasks.get(&task_id).cloned().unwrap(),
            st.agents.get(&target_agent_id).cloned().unwrap(),
        )
    };

    ctx.db.save_board_task(&task_final).await?;
    ctx.db.save_agent(&agent_final).await?;
    if ctx
        .db
        .load_agent_history_detail(&agent_final.id)
        .await?
        .is_none()
    {
        ctx.db
            .save_agent_history_record(
                &agent_final.id,
                &agent_final.name,
                &agent_final.slug,
                &agent_final.group,
                &agent_final.agent_type,
                &agent_final.template,
                now_ts(),
                None,
                &agent_final.worktree_branch,
                0,
                0,
                0,
                "active",
            )
            .await?;
    }
    ctx.db
        .save_agent_task_record(
            &agent_final.id,
            &task_final.id,
            &task_final.task,
            now_ts(),
            None,
            "",
        )
        .await?;
    let total_tasks = ctx
        .db
        .load_agent_history_detail(&agent_final.id)
        .await?
        .and_then(|value| value.get("total_tasks").and_then(|v| v.as_i64()))
        .unwrap_or(0)
        + 1;
    let mut history_fields = serde_json::Map::new();
    history_fields.insert("total_tasks".into(), json!(total_tasks));
    history_fields.insert("status".into(), json!("active"));
    let _ = ctx
        .db
        .update_agent_history_fields(&agent_final.id, &history_fields)
        .await;
    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        "task_dispatched",
        &agent_final.id,
        &agent_final.name,
        &agent_final.group,
        &task_final.task.chars().take(80).collect::<String>(),
        &task_final.id,
        false,
    )
    .await;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "task_id": task_final.id,
        "agent_id": agent_final.id,
    }))
}

// ---------------------------------------------------------------------------
// send_text / broadcast_to_group
// ---------------------------------------------------------------------------

pub async fn send_text_to_cell(
    ctx: &CmdContext,
    cell_id: &str,
    text: &str,
) -> Result<(), CmdError> {
    {
        let st = ctx.state.lock().await;
        if !st.agents.contains_key(cell_id) {
            return Err(CmdError::BadRequest(format!("agent '{cell_id}' not found")));
        }
    }

    if ctx.ui_agents.send(cell_id, text.to_string()) {
        return Ok(());
    }

    if let Some(pty) = ctx_pty(ctx).await {
        pty.write(cell_id, text.as_bytes())
            .await
            .map_err(|e| CmdError::BadRequest(e.to_string()))?;
        return Ok(());
    }

    Err(CmdError::BadRequest(format!(
        "agent '{cell_id}' has no live delivery path"
    )))
}

pub async fn send_text(ctx: &CmdContext, req: &Value) -> CmdResult {
    let cell_id = required_str(req, "cell_id")?.to_string();
    let text = required_str(req, "text")?.to_string();
    send_text_to_cell(ctx, &cell_id, &text).await?;
    ok()
}

pub async fn broadcast_to_group(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let text = required_str(req, "text")?.to_string();
    let include_children = req
        .get("include_children")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let targets: Vec<String> = {
        let st = ctx.state.lock().await;
        let Some(members) = st.groups.get(&group) else {
            return Err(CmdError::BadRequest(format!("group '{group}' not found")));
        };
        let mut ids = Vec::new();
        for aid in members {
            ids.push(aid.clone());
            if include_children {
                if let Some(children) = st.children.get(aid) {
                    ids.extend(children.iter().cloned());
                }
            }
        }
        ids
    };

    let pty = ctx_pty(ctx).await;
    for id in &targets {
        if ctx.ui_agents.is_attached(id) {
            ctx.ui_agents.send(id, text.clone());
            continue;
        }
        if let Some(pty) = &pty {
            let _ = pty.write(id, text.as_bytes()).await;
        }
    }
    Ok(json!({ "ok": true, "sent_to": targets.len() }))
}

pub async fn relaunch_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    if let Some(pty) = ctx_pty(ctx).await {
        // Close existing session
        let _ = pty.close(&id).await;
    }
    // Mark agent stopped; next dispatch respawns it.
    let agent = {
        let mut st = ctx.state.lock().await;
        if let Some(cell) = st.agents.get_mut(&id) {
            cell.status = "stopped".into();
            cell.session_id = None;
        }
        st.emit_agent(&id);
        st.agents.get(&id).cloned()
    };
    if let Some(a) = agent {
        ctx.db.save_agent(&a).await?;
        if a.cell_type == "terminal" || !a.command.is_empty() {
            spawn_cell_session(ctx, &a, None).await?;
        }
    }
    flush(ctx).await;
    ok()
}

// ---------------------------------------------------------------------------
// ai_report — unified handler for all `loom ai` actions
// ---------------------------------------------------------------------------

pub async fn ai_report(ctx: &CmdContext, req: &Value) -> CmdResult {
    let action = required_str(req, "action")?.to_string();
    let agent_id = optional_str(req, "agent_id")
        .map(String::from)
        .ok_or_else(|| CmdError::BadRequest("missing agent_id".into()))?;
    let message = optional_str(req, "message").unwrap_or("").to_string();

    // Resolve the linked task (agent.current_task_id).
    let task_id = {
        let st = ctx.state.lock().await;
        st.agents
            .get(&agent_id)
            .map(|a| a.current_task_id.clone())
            .unwrap_or_default()
    };

    let (task, agent) = {
        let mut st = ctx.state.lock().await;
        if !st.agents.contains_key(&agent_id) {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        }

        // Update agent ephemeral state based on action.
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            match action.as_str() {
                "progress" => {
                    cell.activity = "running".into();
                    cell.activity_detail = message.clone();
                    cell.error_message.clear();
                    cell.needs_attention = false;
                }
                "blocked" => {
                    cell.needs_attention = true;
                    cell.activity_detail = format!("blocked: {message}");
                }
                "error" => {
                    cell.error_message = message.clone();
                    cell.activity = "error".into();
                }
                "done" | "ready" => {
                    cell.activity_detail = "done".into();
                    cell.current_task_id.clear();
                    if action == "ready" {
                        // unlink
                    }
                }
                "ask" => {
                    cell.needs_attention = true;
                    cell.activity_detail = format!("ask: {message}");
                }
                _ => {}
            }
            cell.last_event_at = chrono::Utc::now().timestamp() as f64;
        }
        st.emit_agent(&agent_id);

        // Update linked task.
        let mut task = None;
        if !task_id.is_empty() {
            if let Some(t) = st.board_tasks.get_mut(&task_id) {
                match action.as_str() {
                    "done" | "ready" => {
                        t.lane = "Done".into();
                        t.lane_entered_at = chrono::Utc::now().to_rfc3339();
                    }
                    "blocked" => {
                        if !t.labels.iter().any(|l| l == "blocked") {
                            t.labels.push("blocked".into());
                        }
                    }
                    "error" => {
                        if !t.labels.iter().any(|l| l == "error") {
                            t.labels.push("error".into());
                        }
                    }
                    _ => {}
                }
                // Append to messages log.
                let entry = json!({
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                    "action": action,
                    "message": message,
                    "agent_id": agent_id,
                });
                t.messages.push(entry);
                t.updated_at = chrono::Utc::now().to_rfc3339();
                task = Some(t.clone());
            }
            st.emit_task(&task_id);
        }
        let agent = st.agents.get(&agent_id).cloned().unwrap();
        (task, agent)
    };

    if let Some(t) = &task {
        ctx.db.save_board_task(t).await?;
    }
    ctx.db.save_agent(&agent).await?;
    ctx.db
        .save_agent_message_record(&agent.id, &task_id, now_ts(), &action, &message)
        .await?;
    match action.as_str() {
        "done" | "ready" => {
            if !task_id.is_empty() {
                let outcome = if action == "ready" { "ready" } else { "done" };
                let _ = ctx
                    .db
                    .update_agent_task(&agent.id, &task_id, Some(now_ts()), Some(outcome))
                    .await;
            }
            let _ = crate::commands::compat::record_panel_event(
                &ctx.state,
                &ctx.db,
                "task_completed",
                &agent.id,
                &agent.name,
                &agent.group,
                if message.is_empty() {
                    "Task completed"
                } else {
                    &message
                },
                &task_id,
                false,
            )
            .await;
        }
        "blocked" => {
            let _ = crate::commands::compat::record_panel_event(
                &ctx.state,
                &ctx.db,
                "agent_blocked",
                &agent.id,
                &agent.name,
                &agent.group,
                &message,
                &task_id,
                false,
            )
            .await;
        }
        "error" => {
            let _ = crate::commands::compat::record_panel_event(
                &ctx.state,
                &ctx.db,
                "agent_error",
                &agent.id,
                &agent.name,
                &agent.group,
                &message,
                &task_id,
                false,
            )
            .await;
        }
        "progress" => {
            let _ = crate::commands::compat::record_panel_event(
                &ctx.state,
                &ctx.db,
                "agent_progress",
                &agent.id,
                &agent.name,
                &agent.group,
                &message,
                &task_id,
                true,
            )
            .await;
        }
        "verify" => {
            let _ = crate::commands::compat::record_panel_event(
                &ctx.state,
                &ctx.db,
                "task_verification_updated",
                &agent.id,
                &agent.name,
                &agent.group,
                &message,
                &task_id,
                false,
            )
            .await;
        }
        "ask" => {
            let _ = crate::commands::compat::record_panel_event(
                &ctx.state,
                &ctx.db,
                "ask_created",
                &agent.id,
                &agent.name,
                &agent.group,
                &message,
                &task_id,
                false,
            )
            .await;
        }
        _ => {}
    }
    flush(ctx).await;
    Ok(json!({ "ok": true, "action": action, "task_id": task_id }))
}

// ---------------------------------------------------------------------------
// resolve_ask — human replies to a `loom_ask` from an agent
// ---------------------------------------------------------------------------

/// The operator answers a pending ask from an agent. The ask task (in Backlog
/// with a `human` label) is marked Done; the parent task's "Awaiting Input"
/// status clears; the reply is appended to the parent task's message log so
/// the agent sees it on next tick.
pub async fn resolve_ask(ctx: &CmdContext, req: &Value) -> CmdResult {
    let task_id = required_str(req, "task_id")?.to_string();
    let reply = required_str(req, "reply")?.to_string();

    let (ask_task, parent) = {
        let mut st = ctx.state.lock().await;
        let Some(mut ask_task) = st.board_tasks.get(&task_id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };
        // Mark the ask resolved: move to Done.
        ask_task.lane = "Done".into();
        ask_task.lane_entered_at = chrono::Utc::now().to_rfc3339();
        ask_task.updated_at = ask_task.lane_entered_at.clone();
        ask_task.messages.push(json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "action": "reply",
            "message": reply,
        }));
        st.upsert_task(ask_task.clone())?;

        // Update the parent (if any): clear ask status, append reply to its
        // message log.
        let parent = if !ask_task.parent_task_id.is_empty() {
            if let Some(mut parent) = st.board_tasks.get(&ask_task.parent_task_id).cloned() {
                parent.messages.push(json!({
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                    "action": "ask_reply",
                    "message": reply,
                    "from_task_id": ask_task.id,
                }));
                parent.updated_at = chrono::Utc::now().to_rfc3339();
                st.upsert_task(parent.clone())?;
                Some(parent)
            } else {
                None
            }
        } else {
            None
        };
        (ask_task, parent)
    };

    ctx.db.save_board_task(&ask_task).await?;
    if let Some(p) = &parent {
        ctx.db.save_board_task(p).await?;
    }
    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        "ask_resolved",
        "",
        "",
        &ask_task.group,
        &reply,
        &ask_task.id,
        false,
    )
    .await;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "task_id": ask_task.id,
        "parent_task_id": parent.as_ref().map(|p| p.id.clone()),
    }))
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async fn ctx_pty(ctx: &CmdContext) -> Option<std::sync::Arc<loom_pty::LocalPtyBackend>> {
    ctx.pty.clone()
}

pub async fn spawn_cell_session(
    ctx: &CmdContext,
    cell: &AgentCell,
    extra_env: Option<&std::collections::BTreeMap<String, String>>,
) -> Result<(), CmdError> {
    let Some(pty) = ctx_pty(ctx).await else {
        return Ok(());
    };
    if cell.status == "running" && cell.session_id.is_some() {
        return Ok(());
    }
    let command = if cell.command.is_empty() {
        loom_core::config::default_command()
    } else {
        cell.command.clone()
    };
    let cwd = if cell.directory.is_empty() {
        None
    } else {
        Some(std::path::PathBuf::from(&cell.directory))
    };
    let mut env = std::collections::HashMap::new();
    env.insert(loom_core::config::ENV_CELL_ID.to_string(), cell.id.clone());
    if let Some(extra) = extra_env {
        env.extend(extra.iter().map(|(k, v)| (k.clone(), v.clone())));
    }
    pty.spawn(&cell.id, &command, cwd, env, 40, 120)
        .await
        .map_err(|e| CmdError::BadRequest(e.to_string()))?;
    Ok(())
}

fn now_ts() -> f64 {
    chrono::Utc::now().timestamp_millis() as f64 / 1000.0
}
