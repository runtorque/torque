//! Dispatch, send_text, broadcast, and ai_report.

use std::time::Duration;

use serde_json::{json, Value};

use loom_actions::context::LoomContextBuilder;

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

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
        let target_agent_id = if !task.agent_id.is_empty() && st.agents.contains_key(&task.agent_id) {
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

    // Spawn the PTY if this is a fresh agent.
    if let Some(pty) = &ctx_pty(ctx).await {
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
                env.insert(
                    loom_core::config::ENV_CELL_ID.to_string(),
                    agent.id.clone(),
                );
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

pub async fn send_text(ctx: &CmdContext, req: &Value) -> CmdResult {
    let cell_id = required_str(req, "cell_id")?.to_string();
    let text = required_str(req, "text")?.to_string();

    if let Some(pty) = ctx_pty(ctx).await {
        let st = ctx.state.lock().await;
        if !st.agents.contains_key(&cell_id) {
            return Err(CmdError::BadRequest(format!("agent '{cell_id}' not found")));
        }
        drop(st);
        pty.write(&cell_id, text.as_bytes())
            .await
            .map_err(|e| CmdError::BadRequest(e.to_string()))?;
    }
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

    if let Some(pty) = ctx_pty(ctx).await {
        for id in &targets {
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
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
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
    flush(ctx).await;
    Ok(json!({ "ok": true, "action": action, "task_id": task_id }))
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async fn ctx_pty(ctx: &CmdContext) -> Option<std::sync::Arc<loom_pty::LocalPtyBackend>> {
    ctx.pty.clone()
}
