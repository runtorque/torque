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

use crate::app::{AppState, UiAgentRegistry};
use crate::terminal_bridge::TerminalBridgeClient;

pub mod actions;
pub mod agents;
pub mod board;
pub mod dispatch;
pub mod groups;
pub mod memory;
pub mod schedule;
pub mod settings;
pub mod templates;
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
            Json(json!({ "error": "missing cmd field" })),
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
    };

    let result = dispatch(&ctx, &cmd, &req).await;
    match result {
        Ok(value) => Json(value).into_response(),
        Err(CmdError::NotImplemented) => (
            StatusCode::NOT_IMPLEMENTED,
            Json(json!({ "error": format!("command not implemented: {cmd}") })),
        )
            .into_response(),
        Err(CmdError::BadRequest(msg)) => {
            (StatusCode::BAD_REQUEST, Json(json!({ "error": msg }))).into_response()
        }
        Err(CmdError::Engine(err)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": err.to_string() })),
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
    dispatch(ctx, cmd, req).await
}

/// Route the command to its handler.
async fn dispatch(ctx: &CmdContext, cmd: &str, req: &Value) -> CmdResult {
    match cmd {
        // read-only
        "ping" => Ok(json!({ "pong": true })),
        "get_config" => settings::get_config(ctx).await,
        "get_global_settings" => settings::get_global_settings(ctx).await,
        "get_group_settings" => settings::get_group_settings(ctx, req).await,

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
        "board_add_lane" => board::add_lane(ctx, req).await,
        "board_rename_lane" => board::rename_lane(ctx, req).await,
        "board_remove_lane" => board::remove_lane(ctx, req).await,
        "board_reorder_lanes" => board::reorder_lanes(ctx, req).await,
        "board_verify_task" => board::verify_task(ctx, req).await,
        "board_set_panel" => board::set_panel(ctx, req).await,
        "set_layout" | "standalone_set_panel_layout" => board::set_layout(ctx, req).await,
        "dock_panel" => board::dock_panel(ctx, req).await,
        "set_dock_ratios" => board::set_dock_ratios(ctx, req).await,
        "board_set_filters" => board::set_filters(ctx, req).await,
        "board_set_saved_views" => board::set_saved_views(ctx, req).await,
        "board_set_lane_sorts" => board::set_lane_sorts(ctx, req).await,
        "board_set_card_density" => board::set_card_density(ctx, req).await,
        "task_chain" => board::task_chain(ctx, req).await,

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
            })
            .await;
            Ok(snap)
        }

        _ => Err(CmdError::NotImplemented),
    }
}

/// Drain accumulated deltas from state and broadcast them.
pub async fn flush(ctx: &CmdContext) {
    let mut st = ctx.state.lock().await;
    if let Some((seq, ops)) = st.drain_deltas() {
        ctx.bus.send(OutMessage::Delta { seq, ops });
    }
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
