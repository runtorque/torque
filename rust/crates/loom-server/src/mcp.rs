//! MCP JSON-RPC handler — POST /mcp.

use axum::extract::State;
use axum::http::HeaderMap;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Json;
use axum::Router;
use serde_json::{json, Value};

use crate::app::AppState;
use crate::commands::{dispatch as dispatch_cmd, weaver as weaver_cmd, CmdContext};

const PROTOCOL_VERSION: &str = "2025-03-26";
const INSTRUCTIONS: &str = "Loom manages AI agent sessions and tasks in iTerm2. Use these tools to report progress, complete tasks, derive subtasks, and coordinate with other agents in the pipeline.";

pub fn routes() -> Router<AppState> {
    Router::new().route("/mcp", post(handle_mcp))
}

async fn handle_mcp(
    State(app): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<Value>,
) -> impl IntoResponse {
    let id = req.get("id").cloned().unwrap_or(Value::Null);
    let method = req.get("method").and_then(Value::as_str).unwrap_or("");
    let cell_id = headers
        .get("X-Loom-Cell-Id")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_string();

    let ctx = CmdContext {
        db: app.db.clone(),
        state: app.state.clone(),
        bus: app.bus.clone(),
        pty: app.pty.clone(),
        ui_agents: app.ui_agents.clone(),
        terminal_bridge: app.terminal_bridge.clone(),
        terminals: app.terminals.clone(),
        weaver_buffer: app.weaver_buffer.clone(),
            notifier: app.notifier.clone(),
    };

    if req.get("id").is_none() {
        return StatusCode::ACCEPTED.into_response();
    }

    let result = match method {
        "initialize" => Ok(json!({
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "loom", "version": env!("CARGO_PKG_VERSION")},
            "capabilities": {"tools": {}},
            "instructions": INSTRUCTIONS,
        })),
        "ping" => Ok(json!({})),
        "tools/list" => Ok(json!({ "tools": tool_specs(&ctx, &cell_id).await })),
        "tools/call" => handle_tool_call(&ctx, &req, &cell_id).await,
        other => Err(format!("method not supported: {other}")),
    };

    match result {
        Ok(value) => Json(json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": value,
        }))
        .into_response(),
        Err(msg) => Json(json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32603, "message": msg},
        }))
        .into_response(),
    }
}

async fn tool_specs(ctx: &CmdContext, cell_id: &str) -> Vec<Value> {
    let mut tools = loom_tool_specs();
    if is_designated_weaver(ctx, cell_id).await {
        tools.extend(loom_weaver::mcp_tools::weaver_tool_specs());
    }
    tools
}

fn loom_tool_specs() -> Vec<Value> {
    vec![
        json!({"name": "loom_progress", "description": "Report progress on the current task.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}),
        json!({"name": "loom_done", "description": "Mark the current task as complete.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}}}),
        json!({"name": "loom_ready", "description": "Complete the task and unlink this agent for reuse.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}}}),
        json!({"name": "loom_blocked", "description": "Report the task is blocked.", "inputSchema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}),
        json!({"name": "loom_error", "description": "Report an error encountered.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}),
        json!({"name": "loom_ask", "description": "Pause for human input only when the current task cannot proceed safely without a blocking human decision or approval.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "description": {"type": "string"}}, "required": ["question"]}}),
        json!({"name": "loom_context", "description": "Read back the current agent/task context.", "inputSchema": {"type": "object"}}),
        json!({"name": "loom_derive", "description": "Derive a follow-up task and dispatch it.", "inputSchema": {"type": "object", "properties": {"description": {"type": "string"}, "context": {"type": "string"}, "action": {"type": "string"}, "action_vars": {"type": "object"}, "group": {"type": "string"}}, "required": ["description"]}}),
        json!({"name": "loom_verify", "description": "Record verification status on the current task.", "inputSchema": {"type": "object", "properties": {"state": {"type": "string"}, "notes": {"type": "string"}, "mode": {"type": "string"}}, "required": ["state"]}}),
        json!({"name": "loom_name", "description": "Update the current agent's display name.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}),
        json!({"name": "loom_reply", "description": "Reply to a message from the weaver (orchestrator agent).", "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "message": {"type": "string"}}, "required": ["message"]}}),
        json!({"name": "loom_memory_publish", "description": "Publish a shared memory entry.", "inputSchema": {"type": "object", "properties": {"entry_type": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}, "group": {"type": "string"}, "scope_kind": {"type": "string"}, "scope_ref": {"type": "string"}, "task_id": {"type": "string"}, "pinned": {"type": "boolean"}}, "required": ["entry_type", "title"]}}),
        json!({"name": "loom_memory_list", "description": "List memory entries with optional filters.", "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}, "entry_type": {"type": "string"}, "task_id": {"type": "string"}, "pinned_only": {"type": "boolean"}, "search": {"type": "string"}}}}),
        json!({"name": "loom_memory_read", "description": "Read a single memory entry by id.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}),
        json!({"name": "loom_memory_pin", "description": "Pin a memory entry.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}),
        json!({"name": "loom_memory_unpin", "description": "Unpin a memory entry.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}),
        json!({"name": "loom_memory_link", "description": "Link a memory entry to a task/agent/pipeline.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "target_kind": {"type": "string"}, "target_ref": {"type": "string"}, "detach": {"type": "boolean"}}, "required": ["id", "target_kind", "target_ref"]}}),
        json!({"name": "loom_task_upload_artifact", "description": "Attach a text artifact to the current task.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}),
    ]
}

async fn handle_tool_call(ctx: &CmdContext, req: &Value, cell_id: &str) -> Result<Value, String> {
    let params = req.get("params").cloned().unwrap_or(Value::Null);
    let name = params.get("name").and_then(Value::as_str).unwrap_or("");
    let args = params.get("arguments").cloned().unwrap_or(Value::Null);

    if name.starts_with("weaver_") {
        return handle_weaver_tool_call(ctx, name, &args, cell_id).await;
    }

    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .or_else(|| req.get("agent_id").and_then(Value::as_str))
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(cell_id)
        .trim()
        .to_string();

    if agent_id.is_empty() {
        return Err("missing agent_id (set via X-Loom-Cell-Id header or arguments)".into());
    }

    let action = match name {
        "loom_progress" => "progress",
        "loom_done" => "done",
        "loom_ready" => "ready",
        "loom_blocked" => "blocked",
        "loom_error" => "error",
        "loom_ask" => "ask",
        "loom_derive" => return derive_task(ctx, &agent_id, &args).await,
        "loom_context" => return agent_context(ctx, &agent_id).await,
        "loom_verify" => return verify_current_task(ctx, &agent_id, &args).await,
        "loom_name" => return rename_agent(ctx, &agent_id, &args).await,
        "loom_reply" => "reply",
        "loom_memory_publish"
        | "loom_memory_list"
        | "loom_memory_read"
        | "loom_memory_pin"
        | "loom_memory_unpin"
        | "loom_memory_link" => return memory_proxy(ctx, name, &agent_id, &args).await,
        "loom_task_upload_artifact" => {
            return upload_artifact_proxy(ctx, &agent_id, &args).await
        }
        other => return Err(format!("unknown tool: {other}")),
    };

    let message = args
        .get("message")
        .or_else(|| args.get("reason"))
        .or_else(|| args.get("question"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    let report_req = json!({
        "action": action,
        "agent_id": agent_id,
        "message": message,
        "task_id": args
            .get("task_id")
            .cloned()
            .or_else(|| args.get("task").cloned())
            .unwrap_or(Value::Null),
        "description": args.get("description").cloned().unwrap_or(Value::Null),
    });
    let value = dispatch_cmd::ai_report(ctx, &report_req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": format!("ok: {action}")}],
        "loom": value,
    }))
}

async fn handle_weaver_tool_call(
    ctx: &CmdContext,
    name: &str,
    args: &Value,
    cell_id: &str,
) -> Result<Value, String> {
    let (weaver_id, group) = authorize_weaver_cell(ctx, cell_id).await?;
    let req = with_group_and_weaver(args, &group, &weaver_id);
    let value = match name {
        "weaver_board_summary" => weaver_cmd::board_summary(ctx, &req).await,
        "weaver_session_map" => weaver_cmd::session_map_read(ctx, &req).await,
        "weaver_streams_list" => weaver_cmd::streams_list(ctx, &req).await,
        "weaver_stream_show" => weaver_cmd::stream_show(ctx, &req).await,
        "weaver_board_list" => weaver_cmd::board_list(ctx, &req).await,
        "weaver_task_show" => weaver_cmd::task_show(ctx, &req).await,
        "weaver_agents_list" => weaver_cmd::agents_list(ctx, &req).await,
        "weaver_agent_show" => weaver_cmd::agent_show(ctx, &req).await,
        "weaver_actions_list" => weaver_cmd::actions_list(ctx, &req).await,
        "weaver_action_show" => weaver_cmd::action_show(ctx, &req).await,
        "weaver_task_create" => weaver_cmd::task_create(ctx, &req).await,
        "weaver_task_edit" => weaver_cmd::task_edit(ctx, &req).await,
        "weaver_task_verify" => weaver_cmd::task_verify(ctx, &req).await,
        "weaver_task_move" => weaver_cmd::task_move(ctx, &req).await,
        "weaver_task_dispatch" => weaver_cmd::task_dispatch(ctx, &req).await,
        "weaver_batch_dispatch" => weaver_cmd::batch_dispatch(ctx, &req).await,
        "weaver_task_resolve" => weaver_cmd::task_resolve(ctx, &req).await,
        "weaver_events" => weaver_cmd::events(ctx, &req).await,
        "weaver_launch_settings" => weaver_cmd::launch_settings(ctx, &req).await,
        "weaver_notifications" => weaver_cmd::notifications(ctx, &req).await,
        "weaver_resume" => weaver_cmd::resume(ctx, &req).await,
        "weaver_journal" => {
            let mut req = req.clone();
            if let Some(obj) = req.as_object_mut() {
                if let Some(value) = obj.remove("type") {
                    obj.insert("entry_type".into(), value);
                }
            }
            weaver_cmd::journal_append(ctx, &req).await
        }
        "weaver_journal_read" => {
            let mut req = req.clone();
            if let Some(obj) = req.as_object_mut() {
                if let Some(value) = obj.remove("type") {
                    obj.insert("entry_type".into(), value);
                }
            }
            weaver_cmd::journal_read(ctx, &req).await
        }
        "weaver_ask" => weaver_cmd::ask(ctx, &req).await,
        "weaver_note" => weaver_cmd::note(ctx, &req).await,
        "weaver_agent_message" => weaver_cmd::agent_message(ctx, &req).await,
        "weaver_agent_close" => weaver_cmd::agent_close(ctx, &req).await,
        "weaver_agent_relaunch" => weaver_cmd::agent_relaunch(ctx, &req).await,
        "weaver_merge" | "weaver_rebase" | "weaver_create_pr" | "weaver_diff"
        | "weaver_worktree_checkpoint" | "weaver_worktree_remove" => {
            return weaver_worktree_proxy(ctx, name, &req).await
        }
        "weaver_task_upload_artifact" => {
            return weaver_upload_artifact_proxy(ctx, &req).await
        }
        other => return Err(format!("unknown weaver tool: {other}")),
    }
    .map_err(|e| format!("{e:?}"))?;

    let text = if value.is_string() {
        value.as_str().unwrap_or("").to_string()
    } else {
        serde_json::to_string(&value).unwrap_or_else(|_| "{}".to_string())
    };
    Ok(json!({
        "content": [{"type": "text", "text": text}],
        "loom": value,
    }))
}

fn with_group_and_weaver(args: &Value, group: &str, weaver_id: &str) -> Value {
    let mut req = args.clone();
    if !req.is_object() {
        req = json!({});
    }
    if let Some(obj) = req.as_object_mut() {
        let has_group = obj
            .get("group")
            .and_then(Value::as_str)
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false);
        if !has_group {
            obj.insert("group".into(), Value::String(group.to_string()));
        }
        obj.insert("_weaver_id".into(), Value::String(weaver_id.to_string()));
    }
    req
}

async fn is_designated_weaver(ctx: &CmdContext, cell_id: &str) -> bool {
    authorize_weaver_cell(ctx, cell_id).await.is_ok()
}

async fn authorize_weaver_cell(
    ctx: &CmdContext,
    cell_id: &str,
) -> Result<(String, String), String> {
    let cell_id = cell_id.trim();
    if cell_id.is_empty() {
        return Err("missing X-Loom-Cell-Id header for weaver tools".into());
    }
    let st = ctx.state.lock().await;
    let cell = st
        .agents
        .get(cell_id)
        .ok_or_else(|| format!("agent '{cell_id}' not found"))?;
    if cell.cell_type != "agent" {
        return Err("weaver tools require an agent cell".into());
    }
    let group = cell.group.trim().to_string();
    if group.is_empty() {
        return Err("weaver tools require an agent with a group".into());
    }
    let designated = st.get_group_settings(&group).weaver_agent_id;
    if designated != cell.id {
        return Err(
            "weaver tools are only available to the designated Weaver for the group".into(),
        );
    }
    Ok((cell.id.clone(), group))
}

async fn agent_context(ctx: &CmdContext, agent_id: &str) -> Result<Value, String> {
    let st = ctx.state.lock().await;
    let agent = st
        .agents
        .get(agent_id)
        .ok_or_else(|| format!("agent '{agent_id}' not found"))?;
    let task = if !agent.current_task_id.is_empty() {
        st.board_tasks.get(&agent.current_task_id).cloned()
    } else {
        None
    };
    Ok(json!({
        "content": [{"type": "text", "text": "context ready"}],
        "loom": {"agent": agent, "task": task},
    }))
}

async fn derive_task(ctx: &CmdContext, agent_id: &str, args: &Value) -> Result<Value, String> {
    let title = args
        .get("description")
        .and_then(Value::as_str)
        .ok_or_else(|| "missing 'description'".to_string())?
        .to_string();
    let context = args
        .get("context")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let action_name = args
        .get("action")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let derive_group = args
        .get("group")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let action_vars = args
        .get("action_vars")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    let action_mgr = loom_actions::manager::ActionManager::new(
        crate::commands::actions::project_actions_root_pub(),
        Some(crate::commands::actions::user_actions_root_pub()),
    );

    let (agent, parent_task, group, max_depth, transitions) = {
        let st = ctx.state.lock().await;
        let agent = st
            .agents
            .get(agent_id)
            .cloned()
            .ok_or_else(|| format!("agent '{agent_id}' not found"))?;
        let parent_task = st.agent_current_task(agent_id).cloned();
        let Some(parent_task) = parent_task else {
            return Err("No linked task to derive from".into());
        };
        let group = parent_task.group.clone();
        let parent_action = parent_task.action_name.clone();
        let transitions = if parent_action.trim().is_empty() {
            Vec::new()
        } else {
            action_mgr
                .get_action(&parent_action)
                .map(|info| info.transitions)
                .unwrap_or_default()
        };
        let target_max_depth = if action_name.trim().is_empty() {
            None
        } else {
            action_mgr
                .get_action(&action_name)
                .ok()
                .and_then(|info| info.max_depth)
        };
        (
            agent,
            parent_task,
            group,
            target_max_depth.unwrap_or(st.global_settings.max_pipeline_depth),
            transitions,
        )
    };

    if !transitions.is_empty()
        && !action_name.trim().is_empty()
        && !transitions
            .iter()
            .any(|transition| transition.action == action_name)
    {
        let valid = transitions
            .iter()
            .filter_map(|transition| {
                let action = transition.action.trim();
                if action.is_empty() {
                    None
                } else {
                    Some(action.to_string())
                }
            })
            .collect::<Vec<_>>();
        return Err(format!(
            "Action '{}' cannot transition to '{}'. Valid: {}",
            parent_task.action_name,
            action_name,
            valid.join(", ")
        ));
    }

    if max_depth > 0 && parent_task.pipeline_depth + 1 > max_depth {
        return Err("max_pipeline_depth exceeded".into());
    }

    let derive_status = transitions
        .iter()
        .find(|transition| transition.action == action_name)
        .and_then(|transition| {
            let status = transition.status.trim();
            if status.is_empty() {
                None
            } else {
                Some(status.to_string())
            }
        })
        .or_else(|| {
            let action = action_name.trim();
            if action.is_empty() {
                None
            } else {
                Some(action.to_string())
            }
        })
        .unwrap_or_default();
    let root_id = if parent_task.pipeline_root_id.trim().is_empty() {
        parent_task.id.clone()
    } else {
        parent_task.pipeline_root_id.clone()
    };

    {
        let mut st = ctx.state.lock().await;
        if let Some(source_agent) = st.agents.get_mut(agent_id) {
            source_agent.activity.clear();
            source_agent.activity_detail.clear();
            source_agent.needs_attention = false;
            source_agent.error_message.clear();
        }
        st.emit_agent(agent_id);
        if let Some(task) = st.board_tasks.get_mut(&parent_task.id) {
            task.status = derive_status.clone();
            task.updated_at = chrono::Utc::now().to_rfc3339();
            task.messages.push(json!({
                "timestamp": chrono::Utc::now().to_rfc3339(),
                "action": "derive",
                "message": title,
                "agent_id": agent_id,
                "agent_name": agent.name,
            }));
        }
        st.emit_task(&parent_task.id);
        if root_id != parent_task.id {
            if let Some(root_task) = st.board_tasks.get_mut(&root_id) {
                root_task.status = derive_status.clone();
                root_task.updated_at = chrono::Utc::now().to_rfc3339();
            }
            st.emit_task(&root_id);
        }
    }

    let add_req = json!({
        "task": title,
        "group": if derive_group.is_empty() { group } else { derive_group },
        "lane": "Backlog",
        "action_name": action_name,
        "action_vars": action_vars,
        "labels": ["loom:derived"],
        "parent_task_id": parent_task.id,
        "pipeline_depth": parent_task.pipeline_depth + 1,
        "pipeline_root_id": root_id,
        "description": context,
    });
    let value = crate::commands::board::add_task(ctx, &add_req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    let new_id = value
        .get("task_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if new_id.is_empty() {
        return Err("Failed to create derived task".into());
    }

    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        &ctx.weaver_buffer,
        "task_derived",
        agent_id,
        &agent.name,
        &parent_task.group,
        &title.chars().take(80).collect::<String>(),
        &new_id,
        false,
    )
    .await;

    let dispatch_target_id = {
        let st = ctx.state.lock().await;
        let transition_target = transitions
            .iter()
            .find(|transition| transition.action == action_name)
            .map(|transition| transition.target.trim().to_string())
            .unwrap_or_default();
        match transition_target.as_str() {
            "self" => Some(agent_id.to_string()),
            "parent" => {
                let parent_id = parent_task.parent_task_id.trim();
                if parent_id.is_empty() {
                    None
                } else {
                    st.board_tasks.get(parent_id).and_then(|task| {
                        let agent_id = task.agent_id.trim();
                        if agent_id.is_empty() || !st.agents.contains_key(agent_id) {
                            None
                        } else {
                            Some(agent_id.to_string())
                        }
                    })
                }
            }
            "root" => st.board_tasks.get(&root_id).and_then(|task| {
                let agent_id = task.agent_id.trim();
                if agent_id.is_empty() || !st.agents.contains_key(agent_id) {
                    None
                } else {
                    Some(agent_id.to_string())
                }
            }),
            _ if !transitions.is_empty() && !action_name.trim().is_empty() => {
                nearest_ancestor_agent_for_action_stage(&st, &parent_task, &action_name)
            }
            _ => None,
        }
    };

    let dispatch_req = if let Some(target_id) = dispatch_target_id.clone() {
        json!({"task_id": new_id, "agent_id": target_id})
    } else {
        json!({"task_id": new_id})
    };
    let dispatch_result = dispatch_cmd::dispatch_task(ctx, &dispatch_req)
        .await
        .map_err(|err| format!("{err:?}"))?;
    let dispatched_agent_id = dispatch_result
        .get("agent_id")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or(dispatch_target_id.clone());
    if dispatch_result
        .get("ok")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let updated_agent = {
            let mut st = ctx.state.lock().await;
            if let Some(source_agent) = st.agents.get_mut(agent_id) {
                if source_agent.current_task_id == parent_task.id {
                    source_agent.current_task_id.clear();
                    let cloned = source_agent.clone();
                    st.emit_agent(agent_id);
                    Some(cloned)
                } else {
                    None
                }
            } else {
                None
            }
        };
        if let Some(updated_agent) = updated_agent {
            let _ = ctx.db.save_agent(&updated_agent).await;
        }
    }

    Ok(json!({
        "content": [{"type": "text", "text": format!("derived task {new_id}")}],
        "loom": {"task_id": new_id, "description": title, "action": action_name, "agent_id": dispatched_agent_id},
    }))
}

fn agent_can_receive_dispatch(agent: Option<&loom_core::state::AgentCell>) -> bool {
    matches!(
        agent,
        Some(cell)
            if cell.cell_type == "agent"
                && cell.session_id.is_some()
                && cell.status != "stopped"
                && cell.status != "error"
    )
}

fn nearest_ancestor_agent_for_action_stage(
    st: &loom_core::state::MatrixState,
    task: &loom_core::state::BoardTask,
    action_name: &str,
) -> Option<String> {
    if action_name.trim().is_empty() {
        return None;
    }
    let mut ancestor_id = task.parent_task_id.clone();
    while !ancestor_id.trim().is_empty() {
        let ancestor = st.board_tasks.get(&ancestor_id)?;
        if ancestor.action_name == action_name && !ancestor.agent_id.trim().is_empty() {
            let agent = st.agents.get(&ancestor.agent_id);
            if agent_can_receive_dispatch(agent) {
                return Some(ancestor.agent_id.clone());
            }
        }
        ancestor_id = ancestor.parent_task_id.clone();
    }
    None
}

async fn verify_current_task(
    ctx: &CmdContext,
    agent_id: &str,
    args: &Value,
) -> Result<Value, String> {
    let state = args
        .get("state")
        .and_then(Value::as_str)
        .ok_or_else(|| "missing 'state'".to_string())?
        .to_string();
    let task_id = {
        let st = ctx.state.lock().await;
        let agent = st
            .agents
            .get(agent_id)
            .ok_or_else(|| format!("agent '{agent_id}' not found"))?;
        agent.current_task_id.clone()
    };
    if task_id.is_empty() {
        return Err("agent has no current task to verify".into());
    }
    let req = json!({
        "id": task_id,
        "state": state,
        "mode": args.get("mode").cloned().unwrap_or(Value::Null),
        "notes": args.get("notes").cloned().unwrap_or(Value::Null),
        "updated_by": format!("agent:{agent_id}"),
    });
    let value = crate::commands::board::verify_task(ctx, &req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": format!("verification: {state}")}],
        "loom": value,
    }))
}

async fn rename_agent(ctx: &CmdContext, agent_id: &str, args: &Value) -> Result<Value, String> {
    let new_name = args
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| "missing 'name'".to_string())?
        .to_string();
    if new_name.is_empty() {
        return Err("empty name".into());
    }
    let agent = {
        let mut st = ctx.state.lock().await;
        if !st.agents.contains_key(agent_id) {
            return Err(format!("agent '{agent_id}' not found"));
        }
        st.rename_agent(agent_id, &new_name)
            .map_err(|e| format!("{e:?}"))?;
        let agent = st.agents.get(agent_id).cloned().unwrap();
        if let Some((seq, ops)) = st.drain_deltas() {
            ctx.bus
                .send(loom_core::events::OutMessage::Delta { seq, ops });
        }
        agent
    };
    ctx.db
        .save_agent(&agent)
        .await
        .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": format!("renamed to {new_name}")}],
        "loom": {"agent_id": agent_id, "name": new_name, "slug": agent.slug},
    }))
}

async fn memory_proxy(
    ctx: &CmdContext,
    tool: &str,
    agent_id: &str,
    args: &Value,
) -> Result<Value, String> {
    let mut req = args.clone();
    if tool == "loom_memory_publish" {
        if let Some(obj) = req.as_object_mut() {
            if !obj.contains_key("source_kind") {
                obj.insert("source_kind".into(), Value::String("agent".into()));
            }
            obj.insert("source_id".into(), Value::String(agent_id.to_string()));
            let name = {
                let st = ctx.state.lock().await;
                st.agents.get(agent_id).map(|agent| agent.name.clone())
            };
            if let Some(name) = name {
                obj.insert("source_name".into(), Value::String(name));
            }
        }
    }
    let value = match tool {
        "loom_memory_publish" => crate::commands::memory::publish(ctx, &req).await,
        "loom_memory_list" => crate::commands::memory::list(ctx, &req).await,
        "loom_memory_read" => crate::commands::memory::read(ctx, &req).await,
        "loom_memory_pin" => crate::commands::memory::pin(ctx, &req).await,
        "loom_memory_unpin" => crate::commands::memory::unpin(ctx, &req).await,
        "loom_memory_link" => crate::commands::memory::link(ctx, &req).await,
        other => return Err(format!("unknown memory tool: {other}")),
    }
    .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": format!("ok: {tool}")}],
        "loom": value,
    }))
}

/// Proxy for `loom_task_upload_artifact` — forwards to the command surface.
async fn upload_artifact_proxy(
    ctx: &CmdContext,
    agent_id: &str,
    args: &Value,
) -> Result<Value, String> {
    let mut req = args.clone();
    if let Some(obj) = req.as_object_mut() {
        if !obj.contains_key("task_id") {
            let task_id = {
                let st = ctx.state.lock().await;
                st.agents
                    .get(agent_id)
                    .map(|agent| agent.current_task_id.clone())
                    .unwrap_or_default()
            };
            if !task_id.is_empty() {
                obj.insert("task_id".into(), Value::String(task_id));
            }
        }
    }
    let value = crate::commands::misc::task_upload_artifact(ctx, &req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": "ok: loom_task_upload_artifact"}],
        "loom": value,
    }))
}

/// Proxy for the weaver's worktree/merge/rebase/PR tools. These map
/// directly onto the agent-scoped `worktree_*` commands — the weaver
/// tool just carries `agent` instead of `agent_id` and (for
/// worktree-removal) `force` as a bool.
async fn weaver_worktree_proxy(
    ctx: &CmdContext,
    tool: &str,
    args: &Value,
) -> Result<Value, String> {
    let agent_ref = args
        .get("agent")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if agent_ref.is_empty() {
        return Err("missing 'agent'".into());
    }
    let agent_id = {
        let st = ctx.state.lock().await;
        st.agents
            .values()
            .find(|cell| {
                cell.id == agent_ref || cell.slug == agent_ref || cell.name == agent_ref
            })
            .map(|cell| cell.id.clone())
    }
    .ok_or_else(|| format!("agent '{agent_ref}' not found"))?;

    let mut req = args.clone();
    if let Some(obj) = req.as_object_mut() {
        obj.insert("agent_id".into(), Value::String(agent_id));
    }
    let value = match tool {
        "weaver_merge" => crate::commands::worktree::merge(ctx, &req).await,
        "weaver_rebase" => crate::commands::worktree::rebase(ctx, &req).await,
        "weaver_create_pr" => crate::commands::worktree::create_pr(ctx, &req).await,
        "weaver_diff" => crate::commands::worktree::diff_full(ctx, &req).await,
        "weaver_worktree_checkpoint" => {
            crate::commands::worktree::checkpoint_cmd(ctx, &req).await
        }
        "weaver_worktree_remove" => crate::commands::worktree::remove(ctx, &req).await,
        other => return Err(format!("unknown weaver worktree tool: {other}")),
    }
    .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": format!("ok: {tool}")}],
        "loom": value,
    }))
}

/// Proxy for `weaver_task_upload_artifact`. Maps `task` → `task_id` and
/// forwards to the shared upload handler.
async fn weaver_upload_artifact_proxy(
    ctx: &CmdContext,
    args: &Value,
) -> Result<Value, String> {
    let mut req = args.clone();
    if let Some(obj) = req.as_object_mut() {
        if let Some(task) = obj.remove("task") {
            obj.insert("task_id".into(), task);
        }
    }
    let value = crate::commands::misc::task_upload_artifact(ctx, &req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": "ok: weaver_task_upload_artifact"}],
        "loom": value,
    }))
}
