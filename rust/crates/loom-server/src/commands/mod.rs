//! Command dispatcher.
//!
//! The Python server routes 120+ commands through one big `handle_command`
//! match. We split them into cohesive sub-modules, each exposing a function
//! per command. `dispatch` routes incoming `{"cmd": "..."}` bodies to the
//! right handler.

use std::sync::Arc;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json};
use axum::routing::post;
use axum::Router;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::{EventBus, OutMessage};
use loom_core::state::MatrixState;
use loom_pty::LocalPtyBackend;

use crate::app::{AppState, TerminalHub, UiAgentRegistry};
use crate::terminal_bridge::TerminalBridgeClient;
use crate::weaver_buffer::WeaverEventBuffer;

pub mod actions;
pub mod agents;
pub mod board;
pub mod compat;
pub mod dispatch;
pub mod groups;
pub mod memory;
pub mod schedule;
pub mod settings;
pub mod templates;
pub mod weaver;
pub mod worktree;

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/cmd", post(handle_cmd))
}

async fn handle_cmd(State(app): State<AppState>, Json(req): Json<Value>) -> impl IntoResponse {
    let cmd = req
        .get("cmd")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if cmd.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "ok": false, "error": "missing 'cmd'" })),
        )
            .into_response();
    }

    let ctx = CmdContext {
        state: app.state.clone(),
        db: app.db.clone(),
        bus: app.bus.clone(),
        pty: app.pty.clone(),
        ui_agents: app.ui_agents.clone(),
        terminal_bridge: app.terminal_bridge.clone(),
        terminals: app.terminals.clone(),
        weaver_buffer: app.weaver_buffer.clone(),
    };

    let result = dispatch_command(&ctx, &cmd, &req).await;
    match result {
        Ok(value) => {
            if value.get("type").and_then(|v| v.as_str()) == Some("error") {
                Json(json!({
                    "ok": false,
                    "error": value
                        .get("message")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Unknown error"),
                }))
                .into_response()
            } else {
                Json(json!({ "ok": true, "data": value })).into_response()
            }
        }
        Err(CmdError::NotImplemented) => Json(json!({
            "ok": false,
            "error": format!("command not implemented: {cmd}"),
        }))
        .into_response(),
        Err(CmdError::BadRequest(msg)) => {
            Json(json!({ "ok": false, "error": msg })).into_response()
        }
        Err(CmdError::Engine(err)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "ok": false, "error": err.to_string() })),
        )
            .into_response(),
    }
}

/// Context passed to every command handler.
#[derive(Clone)]
pub struct CmdContext {
    pub state: Arc<Mutex<MatrixState>>,
    pub db: LoomDb,
    pub bus: EventBus,
    pub pty: Option<Arc<LocalPtyBackend>>,
    pub ui_agents: UiAgentRegistry,
    pub terminal_bridge: TerminalBridgeClient,
    pub terminals: TerminalHub,
    pub weaver_buffer: WeaverEventBuffer,
}

#[derive(Debug)]
pub enum CmdError {
    NotImplemented,
    BadRequest(String),
    Engine(loom_core::Error),
}

impl From<loom_core::Error> for CmdError {
    fn from(err: loom_core::Error) -> Self {
        Self::Engine(err)
    }
}

impl From<serde_json::Error> for CmdError {
    fn from(err: serde_json::Error) -> Self {
        Self::BadRequest(format!("json: {err}"))
    }
}

pub type CmdResult = Result<Value, CmdError>;

/// Public entry point for in-process dispatch (used by native UI crates).
/// The HTTP handler thin-wraps this.
pub async fn dispatch_command(ctx: &CmdContext, cmd: &str, req: &Value) -> CmdResult {
    let normalized = normalize_request(ctx, cmd, req).await;
    dispatch(ctx, cmd, &normalized).await
}

/// Route the command to its handler.
async fn dispatch(ctx: &CmdContext, cmd: &str, req: &Value) -> CmdResult {
    match cmd {
        // read-only
        "ping" => Ok(json!({ "pong": true })),
        "get_config" => settings::get_config(ctx, req).await,
        "get_global_settings" => settings::get_global_settings(ctx).await,
        "get_group_settings" => settings::get_group_settings(ctx, req).await,
        "get_events" => compat::get_events(ctx, req).await,
        "get_agent_history" => compat::get_agent_history(ctx, req).await,
        "get_agent_history_detail" => compat::get_agent_history_detail(ctx, req).await,
        "events_dismiss" => compat::events_dismiss(ctx, req).await,

        // groups
        "add_group" => groups::add_group(ctx, req).await,
        "remove_group" => groups::remove_group(ctx, req).await,
        "rename_group" => groups::rename_group(ctx, req).await,
        "move_group" => groups::move_group(ctx, req).await,
        "update_group_settings" => settings::update_group_settings(ctx, req).await,

        // global settings
        "update_global_settings" => settings::update_global_settings(ctx, req).await,

        // agents
        "add_agent" => agents::add_agent(ctx, req).await,
        "add_terminal" => agents::add_terminal(ctx, req).await,
        "remove_agent" => agents::remove_agent(ctx, req).await,
        "update_agent" => agents::update_agent(ctx, req).await,
        "move_agent" => agents::move_agent(ctx, req).await,
        "focus_agent" => agents::focus_agent(ctx, req).await,
        "reparent_terminal" => agents::reparent_terminal(ctx, req).await,
        "reorder_child" => agents::reorder_child(ctx, req).await,
        "select_agent" => agents::select_agent(ctx, req).await,
        "clear_agent_context" => agents::clear_agent_context(ctx, req).await,

        // actions
        "list_actions" => actions::list_actions(ctx, req).await,
        "get_action" => actions::get_action(ctx, req).await,
        "render_action" => actions::render_action(ctx, req).await,
        "save_action" => actions::save_action(ctx, req).await,
        "delete_action" => actions::delete_action(ctx, req).await,
        "preview_prompt" => actions::preview_prompt(ctx, req).await,
        "discover_pipelines" => actions::discover_pipelines(ctx, req).await,

        // templates (agent config bundles)
        "list_templates" => templates::list_templates(ctx, req).await,
        "get_template" => templates::get_template(ctx, req).await,
        "save_template" => templates::save_template(ctx, req).await,
        "delete_template" => templates::delete_template(ctx, req).await,
        "render_template" => templates::render_template(ctx, req).await,

        // board
        "board_add_task" => board::add_task(ctx, req).await,
        "board_update_task" => board::update_task(ctx, req).await,
        "board_remove_task" => board::remove_task(ctx, req).await,
        "board_move_task" => board::move_task(ctx, req).await,
        "board_reorder_task" => board::reorder_task(ctx, req).await,
        "board_archive_task" => board::archive_task(ctx, req).await,
        "board_unarchive_task" => board::unarchive_task(ctx, req).await,
        "remove_attachment" => compat::remove_attachment(ctx, req).await,
        "board_add_lane" => board::add_lane(ctx, req).await,
        "board_rename_lane" => board::rename_lane(ctx, req).await,
        "board_remove_lane" => board::remove_lane(ctx, req).await,
        "board_reorder_lanes" => board::reorder_lanes(ctx, req).await,
        "board_verify_task" => board::verify_task(ctx, req).await,
        "board_set_panel" => board::set_panel(ctx, req).await,
        "set_layout" => board::set_layout(ctx, req).await,
        "standalone_set_panel_layout" => board::set_standalone_panel_layout(ctx, req).await,
        "dock_panel" => board::dock_panel(ctx, req).await,
        "set_dock_ratios" => board::set_dock_ratios(ctx, req).await,
        "board_set_filters" => board::set_filters(ctx, req).await,
        "board_set_saved_views" => board::set_saved_views(ctx, req).await,
        "board_set_lane_sorts" => board::set_lane_sorts(ctx, req).await,
        "board_set_card_density" => board::set_card_density(ctx, req).await,
        "task_chain" => board::task_chain(ctx, req).await,

        // weaver
        "weaver_board_summary" => weaver::board_summary(ctx, req).await,
        "weaver_streams_list" => weaver::streams_list(ctx, req).await,
        "weaver_stream_show" => weaver::stream_show(ctx, req).await,
        "weaver_board_list" => weaver::board_list(ctx, req).await,
        "weaver_task_show" => weaver::task_show(ctx, req).await,
        "weaver_agents_list" => weaver::agents_list(ctx, req).await,
        "weaver_agent_show" => weaver::agent_show(ctx, req).await,
        "weaver_actions_list" => weaver::actions_list(ctx, req).await,
        "weaver_action_show" => weaver::action_show(ctx, req).await,
        "weaver_events" => weaver::events(ctx, req).await,
        "weaver_journal_append" => weaver::journal_append(ctx, req).await,
        "weaver_journal_read" => weaver::journal_read(ctx, req).await,
        "weaver_journal_delete" => weaver::journal_delete(ctx, req).await,
        "weaver_session_map_read" => weaver::session_map_read(ctx, req).await,
        "weaver_update_settings" => weaver::update_settings(ctx, req).await,
        "weaver_ask" => weaver::ask(ctx, req).await,
        "weaver_note" => weaver::note(ctx, req).await,
        "weaver_dismiss_note" => weaver::dismiss_note(ctx, req).await,
        "weaver_pause" => weaver::pause(ctx, req).await,
        "weaver_resume" => weaver::resume(ctx, req).await,
        "weaver_reply" => weaver::reply(ctx, req).await,
        "weaver_notifications" => weaver::notifications(ctx, req).await,
        "weaver_launch_settings" => weaver::launch_settings(ctx, req).await,
        "weaver_flush_now" => weaver::flush_now(ctx, req).await,

        // worktree
        "worktree_create" => worktree::create(ctx, req).await,
        "worktree_remove" => worktree::remove(ctx, req).await,
        "worktree_list" => worktree::list(ctx, req).await,
        "worktree_prune" => worktree::prune(ctx, req).await,
        "worktree_checkpoint" => worktree::checkpoint_cmd(ctx, req).await,
        "worktree_history" => worktree::history(ctx, req).await,
        "worktree_diff" => worktree::diff(ctx, req).await,
        "worktree_rollback" => worktree::rollback(ctx, req).await,
        "worktree_check_merge" => worktree::check_merge(ctx, req).await,

        // dispatch + ai_report
        "dispatch_task" => dispatch::dispatch_task(ctx, req).await,
        "send_text" => dispatch::send_text(ctx, req).await,
        "broadcast_to_group" => dispatch::broadcast_to_group(ctx, req).await,
        "relaunch_agent" => dispatch::relaunch_agent(ctx, req).await,
        "ai_report" => dispatch::ai_report(ctx, req).await,
        "resolve_ask" => dispatch::resolve_ask(ctx, req).await,

        // schedules
        "schedule_create" => schedule::create(ctx, req).await,
        "schedule_update" => schedule::update(ctx, req).await,
        "schedule_remove" => schedule::remove(ctx, req).await,
        "schedule_enable" => schedule::enable(ctx, req).await,
        "schedule_disable" => schedule::disable(ctx, req).await,
        "schedule_list" => schedule::list(ctx, req).await,
        "schedule_run" => schedule::run(ctx, req).await,

        // memory
        "memory_publish" => memory::publish(ctx, req).await,
        "memory_list" => memory::list(ctx, req).await,
        "memory_read" => memory::read(ctx, req).await,
        "memory_pin" => memory::pin(ctx, req).await,
        "memory_unpin" => memory::unpin(ctx, req).await,
        "memory_link" => memory::link(ctx, req).await,

        // resync / state
        "refresh" | "resync" => {
            let snap = crate::ws::build_snapshot(&AppState {
                db: ctx.db.clone(),
                state: ctx.state.clone(),
                bus: ctx.bus.clone(),
                pty: ctx.pty.clone(),
                ui_agents: ctx.ui_agents.clone(),
                terminal_bridge: ctx.terminal_bridge.clone(),
                terminals: ctx.terminals.clone(),
                weaver_buffer: ctx.weaver_buffer.clone(),
            })
            .await;
            Ok(snap)
        }

        _ => Err(CmdError::NotImplemented),
    }
}

async fn normalize_request(ctx: &CmdContext, cmd: &str, req: &Value) -> Value {
    let Some(obj) = req.as_object() else {
        return req.clone();
    };
    let mut normalized = obj.clone();

    fn alias(obj: &mut serde_json::Map<String, Value>, from: &str, to: &str) {
        if !obj.contains_key(to) {
            if let Some(value) = obj.get(from).cloned() {
                obj.insert(to.to_string(), value);
            }
        }
    }

    match cmd {
        "add_group" | "remove_group" => alias(&mut normalized, "group", "name"),
        "rename_group" => {
            alias(&mut normalized, "group", "from");
            alias(&mut normalized, "new_name", "to");
        }
        "move_agent" => alias(&mut normalized, "target_group", "to_group"),
        "dispatch_task" | "preview_prompt" => alias(&mut normalized, "id", "task_id"),
        "resolve_ask" => {
            alias(&mut normalized, "id", "task_id");
            alias(&mut normalized, "answer", "reply");
        }
        "send_text" => alias(&mut normalized, "id", "cell_id"),
        "worktree_create"
        | "worktree_remove"
        | "worktree_checkpoint"
        | "worktree_history"
        | "worktree_diff"
        | "worktree_check_merge"
        | "worktree_rollback"
        | "worktree_create_pr"
        | "worktree_merge"
        | "worktree_rebase"
        | "worktree_diff_full" => alias(&mut normalized, "id", "agent_id"),
        "memory_read" | "memory_pin" | "memory_unpin" | "memory_link" => {
            alias(&mut normalized, "entry_id", "id");
        }
        "board_update_task" | "schedule_update" => {
            if !normalized.contains_key("fields") {
                let mut fields = serde_json::Map::new();
                for (key, value) in obj {
                    if key == "cmd" || key == "id" {
                        continue;
                    }
                    fields.insert(key.clone(), value.clone());
                }
                normalized.insert("fields".into(), Value::Object(fields));
            }
        }
        "move_group" => {
            if !normalized.contains_key("order") {
                if let Some(name) = normalized.get("group").and_then(|v| v.as_str()) {
                    let before = normalized
                        .get("before")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let order = {
                        let st = ctx.state.lock().await;
                        let mut items: Vec<String> = st
                            .groups_order
                            .iter()
                            .filter(|group| group.as_str() != name)
                            .cloned()
                            .collect();
                        if let Some(idx) = items.iter().position(|group| group == before) {
                            items.insert(idx, name.to_string());
                        } else {
                            items.push(name.to_string());
                        }
                        items
                    };
                    normalized.insert(
                        "order".into(),
                        Value::Array(order.into_iter().map(Value::String).collect()),
                    );
                }
            }
        }
        _ => {}
    }

    Value::Object(normalized)
}

/// Drain accumulated deltas from state and broadcast them.
pub async fn flush(ctx: &CmdContext) {
    let mut st = ctx.state.lock().await;
    let drained = st.drain_deltas();
    drop(st);
    if let Some((seq, ops)) = drained {
        ctx.bus.send(OutMessage::Delta { seq, ops });
    }
    ctx.weaver_buffer.maybe_flush_due(ctx).await;
}

/// Extract a required string field from the request body.
pub fn required_str<'a>(req: &'a Value, field: &str) -> Result<&'a str, CmdError> {
    req.get(field)
        .and_then(|v| v.as_str())
        .ok_or_else(|| CmdError::BadRequest(format!("missing '{field}'")))
}

/// Extract an optional string field.
pub fn optional_str<'a>(req: &'a Value, field: &str) -> Option<&'a str> {
    req.get(field).and_then(|v| v.as_str())
}

/// Convenience: return `{ ok: true }` after a mutation.
pub fn ok() -> CmdResult {
    Ok(json!({ "ok": true }))
}
