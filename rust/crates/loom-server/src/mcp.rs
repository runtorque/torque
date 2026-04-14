//! MCP JSON-RPC handler — POST /mcp
//!
//! Implements a minimal subset of the Model Context Protocol: `initialize`,
//! `tools/list`, and `tools/call` for the `loom_*` weaver tool set. Each tool
//! call maps to our internal `ai_report` logic.

use axum::extract::State;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Json;
use axum::Router;
use serde_json::{json, Value};

use crate::app::AppState;
use crate::commands::{dispatch as dispatch_cmd, CmdContext};

pub fn routes() -> Router<AppState> {
    Router::new().route("/mcp", post(handle_mcp))
}

async fn handle_mcp(State(app): State<AppState>, Json(req): Json<Value>) -> impl IntoResponse {
    let id = req.get("id").cloned().unwrap_or(Value::Null);
    let method = req.get("method").and_then(|v| v.as_str()).unwrap_or("");

    let ctx = CmdContext {
        db: app.db.clone(),
        state: app.state.clone(),
        bus: app.bus.clone(),
        pty: app.pty.clone(),
    };

    let result = match method {
        "initialize" => Ok(json!({
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "loom", "version": env!("CARGO_PKG_VERSION")},
            "capabilities": {"tools": {}},
        })),
        "tools/list" => Ok(json!({ "tools": tool_specs() })),
        "tools/call" => handle_tool_call(&ctx, &req).await,
        other => Err(format!("method not supported: {other}")),
    };

    match result {
        Ok(value) => Json(json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": value,
        })),
        Err(msg) => Json(json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32603, "message": msg},
        })),
    }
}

fn tool_specs() -> Vec<Value> {
    vec![
        json!({"name": "loom_progress", "description": "Report progress on the current task.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}),
        json!({"name": "loom_done", "description": "Mark the current task as complete.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}}}),
        json!({"name": "loom_ready", "description": "Complete the task and unlink this agent for reuse.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}}}),
        json!({"name": "loom_blocked", "description": "Report the task is blocked.", "inputSchema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}),
        json!({"name": "loom_error", "description": "Report an error encountered.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}),
        json!({"name": "loom_ask", "description": "Ask the human a clarifying question.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}),
        json!({"name": "loom_context", "description": "Read back the current agent/task context.", "inputSchema": {"type": "object"}}),
        json!({"name": "loom_derive", "description": "Derive a follow-up task and dispatch it.", "inputSchema": {"type": "object", "properties": {"description": {"type": "string"}, "action": {"type": "string"}}, "required": ["description"]}}),
    ]
}

async fn handle_tool_call(ctx: &CmdContext, req: &Value) -> Result<Value, String> {
    let params = req.get("params").cloned().unwrap_or(Value::Null);
    let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let args = params.get("arguments").cloned().unwrap_or(Value::Null);

    // agent_id comes from the LOOM_CELL_ID header or arguments.
    let agent_id = args
        .get("agent_id")
        .and_then(|v| v.as_str())
        .or_else(|| req.get("agent_id").and_then(|v| v.as_str()))
        .unwrap_or("")
        .to_string();

    if agent_id.is_empty() {
        return Err("missing agent_id (set via LOOM_CELL_ID env var on spawn)".into());
    }

    let action = match name {
        "loom_progress" => "progress",
        "loom_done" => "done",
        "loom_ready" => "ready",
        "loom_blocked" => "blocked",
        "loom_error" => "error",
        "loom_ask" => "ask",
        "loom_derive" => {
            // Not ai_report — creates a new task.
            return derive_task(ctx, &agent_id, &args).await;
        }
        "loom_context" => {
            return agent_context(ctx, &agent_id).await;
        }
        other => return Err(format!("unknown tool: {other}")),
    };

    let message = args
        .get("message")
        .or(args.get("reason"))
        .or(args.get("question"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let report_req = json!({
        "action": action,
        "agent_id": agent_id,
        "message": message,
    });
    let value = dispatch_cmd::ai_report(ctx, &report_req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    Ok(json!({
        "content": [{"type": "text", "text": format!("ok: {action}")}],
        "loom": value,
    }))
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
        "loom": {
            "agent": agent,
            "task": task,
        }
    }))
}

async fn derive_task(ctx: &CmdContext, agent_id: &str, args: &Value) -> Result<Value, String> {
    let description = args
        .get("description")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing 'description'".to_string())?
        .to_string();
    let action_name = args
        .get("action")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    // Parent task inherits group + pipeline root.
    let (parent_task, parent_depth, group, max_depth) = {
        let st = ctx.state.lock().await;
        let agent = st
            .agents
            .get(agent_id)
            .ok_or_else(|| format!("agent '{agent_id}' not found"))?;
        let pt = if agent.current_task_id.is_empty() {
            None
        } else {
            st.board_tasks.get(&agent.current_task_id).cloned()
        };
        let depth = pt.as_ref().map(|t| t.pipeline_depth).unwrap_or(0);
        let group = pt
            .as_ref()
            .map(|t| t.group.clone())
            .unwrap_or_else(|| agent.group.clone());
        (pt, depth, group, st.global_settings.max_pipeline_depth)
    };

    if max_depth > 0 && parent_depth + 1 > max_depth {
        return Err("max_pipeline_depth exceeded".into());
    }

    // Insert new task via board_add_task path.
    let add_req = json!({
        "task": &description,
        "group": &group,
        "action_name": &action_name,
        "labels": ["derived"],
    });
    let v = crate::commands::board::add_task(ctx, &add_req)
        .await
        .map_err(|e| format!("{e:?}"))?;
    let new_id = v.get("task_id").and_then(|v| v.as_str()).unwrap_or("").to_string();

    // Patch parent linkage + depth.
    if let Some(parent) = parent_task {
        let mut st = ctx.state.lock().await;
        if let Some(t) = st.board_tasks.get_mut(&new_id) {
            t.parent_task_id = parent.id.clone();
            t.pipeline_depth = parent.pipeline_depth + 1;
            t.pipeline_root_id = if parent.pipeline_root_id.is_empty() {
                parent.id.clone()
            } else {
                parent.pipeline_root_id.clone()
            };
        }
        st.emit_task(&new_id);
        if let Some((seq, ops)) = st.drain_deltas() {
            ctx.bus.send(loom_core::events::OutMessage::Delta { seq, ops });
        }
    }

    Ok(json!({
        "content": [{"type": "text", "text": format!("derived task {new_id}")}],
        "loom": {"task_id": new_id, "description": description, "action": action_name},
    }))
}
