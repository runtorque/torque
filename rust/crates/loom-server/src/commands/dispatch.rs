//! Dispatch, send_text, broadcast, and ai_report.

use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tracing::warn;

use loom_actions::context::LoomContextBuilder;
use loom_adapters::{AgentAdapter, InputReadyPolicy};
use loom_core::state::AgentCell;

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};
use crate::terminal_bridge::{bridge_manages_cell, CreateSessionOptions};

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

fn resolve_ai_report_task_id(
    st: &loom_core::state::MatrixState,
    agent_id: &str,
    explicit: &str,
) -> String {
    let explicit = explicit.trim();
    if !explicit.is_empty() {
        return st
            .resolve_task_ident(explicit)
            .or_else(|| st.task_id_aliases.get(explicit).cloned())
            .unwrap_or_else(|| explicit.to_string());
    }
    let Some(agent) = st.agents.get(agent_id) else {
        return String::new();
    };
    if !agent.current_task_id.is_empty() {
        if let Some(current) = st.board_tasks.get(&agent.current_task_id) {
            if st.task_occupies_execution_slot(Some(current), agent_id) {
                return current.id.clone();
            }
        }
    }
    if let Some(current) = st.agent_current_task(agent_id) {
        return current.id.clone();
    }
    let mut linked = st
        .board_tasks
        .values()
        .filter(|task| {
            task.agent_id == agent_id
                && task.lane != "Done"
                && task.lane != "Backlog"
                && task.lane != loom_core::state::ARCHIVED_LANE
        })
        .collect::<Vec<_>>();
    linked.sort_by(|a, b| a.id.cmp(&b.id));
    if linked.len() == 1 {
        linked[0].id.clone()
    } else {
        String::new()
    }
}

fn parent_has_open_human_asks(
    st: &loom_core::state::MatrixState,
    parent_task_id: &str,
    exclude_task_id: &str,
) -> bool {
    st.board_tasks.values().any(|task| {
        task.id != exclude_task_id
            && task.parent_task_id == parent_task_id
            && !loom_core::state::board_task_is_closed(Some(task))
            && task.labels.iter().any(|label| label == "loom:human")
    })
}

/// Copy the worktree fields of `source_agent_id` onto `target_agent_id`.
/// Returns true if the source had a worktree and the target was updated.
/// Mirrors the explicit `inherit_worktree_from` branch in
/// `loom/server.py:4520-4540`.
fn apply_inherited_worktree(
    st: &mut loom_core::state::MatrixState,
    target_agent_id: &str,
    source_agent_id: &str,
) -> bool {
    let src = match st.agents.get(source_agent_id) {
        Some(agent) if !agent.worktree_path.is_empty() => agent.clone(),
        _ => return false,
    };
    if let Some(target) = st.agents.get_mut(target_agent_id) {
        target.worktree_path = src.worktree_path.clone();
        target.worktree_branch = src.worktree_branch.clone();
        target.worktree_repo_root = src.worktree_repo_root.clone();
        target.worktree_base_branch = src.worktree_base_branch.clone();
        target.worktree_changed_files = src.worktree_changed_files.clone();
        target.directory = src.worktree_path.clone();
        st.emit_agent(target_agent_id);
        return true;
    }
    false
}

/// Walk the `parent_task_id` chain until we find an agent with an active
/// worktree, then copy it to `target_agent_id`. Mirrors the HITL / derive
/// fallback branch in `loom/server.py:4541-4573`.
fn inherit_worktree_from_parent_chain(
    st: &mut loom_core::state::MatrixState,
    target_agent_id: &str,
    start_parent_task_id: &str,
) -> bool {
    let mut pid = start_parent_task_id.to_string();
    while !pid.is_empty() {
        let parent_task = match st.board_tasks.get(&pid) {
            Some(t) => t.clone(),
            None => break,
        };
        if !parent_task.agent_id.is_empty()
            && apply_inherited_worktree(st, target_agent_id, &parent_task.agent_id)
        {
            return true;
        }
        pid = parent_task.parent_task_id.clone();
    }
    false
}

#[derive(Default)]
struct CascadeResult {
    tasks: Vec<loom_core::state::BoardTask>,
    unlinked_agents: Vec<loom_core::state::AgentCell>,
}

/// Walk up the parent chain from `task_id`, completing ancestors whose
/// descendant branches are all Done. Mirrors `_cascade_done` in
/// `loom/server.py`. Returns every task that was moved to Done (and every
/// agent whose stale `current_task_id` got cleared) so the caller can
/// persist them.
///
/// Also unlinks any agent whose `current_task_id` pointed at a promoted
/// task — cascade-closing a task means its worker is idle, even if that
/// worker never fired its own `loom_done` (e.g. a reviewer handed work
/// off and we only see the terminal `done` from the fixer).
fn cascade_done(st: &mut loom_core::state::MatrixState, task_id: &str) -> CascadeResult {
    let mut out = CascadeResult::default();
    let Some(start) = st.board_tasks.get(task_id) else {
        return out;
    };
    let mut pid = start.parent_task_id.clone();
    while !pid.is_empty() {
        let parent = match st.board_tasks.get(&pid) {
            Some(p) => p.clone(),
            None => break,
        };
        if loom_core::state::board_task_is_closed(Some(&parent)) {
            pid = parent.parent_task_id.clone();
            continue;
        }
        if st.task_has_unresolved_descendants(&pid) {
            break;
        }
        let now = chrono::Utc::now().to_rfc3339();
        let mut promoted = parent.clone();
        promoted.status.clear();
        promoted.lane = "Done".into();
        promoted.lane_entered_at = now.clone();
        promoted.updated_at = now;
        if st.upsert_task(promoted.clone()).is_err() {
            break;
        }
        // Clear any agent whose current_task_id pointed at the promoted
        // task — the agent has nothing linked anymore.
        let stale_agents: Vec<String> = st
            .agents
            .values()
            .filter(|a| a.current_task_id == promoted.id)
            .map(|a| a.id.clone())
            .collect();
        for aid in stale_agents {
            if let Some(agent) = st.agents.get_mut(&aid) {
                agent.current_task_id.clear();
                out.unlinked_agents.push(agent.clone());
            }
            st.emit_agent(&aid);
        }
        out.tasks.push(promoted.clone());
        pid = promoted.parent_task_id.clone();
    }
    out
}

fn clear_parent_awaiting_input(
    st: &mut loom_core::state::MatrixState,
    parent_task_id: &str,
    exclude_task_id: &str,
) -> Option<(
    loom_core::state::BoardTask,
    Option<loom_core::state::BoardTask>,
)> {
    if parent_task_id.trim().is_empty()
        || parent_has_open_human_asks(st, parent_task_id, exclude_task_id)
    {
        return None;
    }
    let now = chrono::Utc::now().to_rfc3339();
    let Some(parent) = st.board_tasks.get(parent_task_id).cloned() else {
        return None;
    };

    let mut updated_parent = parent.clone();
    updated_parent.status.clear();
    updated_parent.updated_at = now.clone();
    st.upsert_task(updated_parent.clone()).ok()?;

    let root_id = if updated_parent.pipeline_root_id.is_empty() {
        updated_parent.id.clone()
    } else {
        updated_parent.pipeline_root_id.clone()
    };
    if root_id == updated_parent.id {
        return Some((updated_parent, None));
    }
    let Some(root) = st.board_tasks.get(&root_id).cloned() else {
        return Some((updated_parent, None));
    };
    let mut updated_root = root.clone();
    updated_root.status.clear();
    updated_root.updated_at = now;
    st.upsert_task(updated_root.clone()).ok()?;
    Some((updated_parent, Some(updated_root)))
}

async fn mark_agent_running_quiet(
    ctx: &CmdContext,
    agent_id: &str,
) -> Result<Option<AgentCell>, CmdError> {
    let agent = {
        let mut st = ctx.state.lock().await;
        let Some(agent) = st.agents.get_mut(agent_id) else {
            return Ok(None);
        };
        agent.status = "running".into();
        agent.error_message.clear();
        agent.last_event_at = chrono::Utc::now().timestamp() as f64;
        let cloned = agent.clone();
        st.emit_agent(agent_id);
        cloned
    };
    ctx.db.save_agent(&agent).await?;
    Ok(Some(agent))
}

async fn mark_agent_running(
    ctx: &CmdContext,
    agent_id: &str,
) -> Result<Option<AgentCell>, CmdError> {
    let agent = mark_agent_running_quiet(ctx, agent_id).await?;
    flush(ctx).await;
    Ok(agent)
}

// ---------------------------------------------------------------------------
// dispatch_task
// ---------------------------------------------------------------------------

pub async fn dispatch_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let task_id = required_str(req, "task_id")?.to_string();
    let requested_agent_id = optional_str(req, "agent_id")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);
    let force_no_action = req
        .get("force_no_action")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // Load task + resolve target agent.
    let (task, target_agent_id, is_new_agent, dispatch_lane, new_agent_group_settings) = {
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

        let mut created_new_agent = false;
        let mut new_agent_group_settings: Option<loom_core::state::GroupSettings> = None;

        // Resolve agent: request-body agent_id wins, then task.agent_id, else create new.
        let target_agent_id = if let Some(agent_id) = requested_agent_id.as_ref() {
            if !st.agents.contains_key(agent_id) {
                return Err(CmdError::BadRequest(format!(
                    "agent '{agent_id}' not found"
                )));
            }
            agent_id.clone()
        } else if !task.agent_id.is_empty() && st.agents.contains_key(&task.agent_id) {
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
            let settings = st
                .group_settings
                .get(&task.group)
                .cloned()
                .unwrap_or_default();
            let default_command = crate::commands::agents::default_command_from_global(
                &st.global_settings.default_command,
            );
            crate::commands::agents::apply_agent_defaults(
                &mut cell,
                &settings,
                &default_command,
                req,
            );
            if ctx.terminal_bridge.is_configured() {
                cell.terminal_backend = "iterm2".into();
            }
            if cell.directory.is_empty() {
                cell.directory = crate::paths::expand_user_path_string(
                    &crate::commands::agents::first_nonempty([
                        std::env::var("LOOM_PROJECT_ROOT").unwrap_or_default(),
                        crate::app::repo_root().to_string_lossy().to_string(),
                    ]),
                );
            }
            let id = cell.id.clone();
            st.add_agent(cell)?;
            created_new_agent = true;
            new_agent_group_settings = Some(settings);
            id
        };

        (
            task,
            target_agent_id,
            created_new_agent,
            dispatch_lane,
            new_agent_group_settings,
        )
    };

    // Bootstrap a git worktree for the fresh worker. Three paths, in order:
    //   1. Explicit `inherit_worktree_from` request field — copy the named
    //      source agent's worktree fields (pipeline dispatch).
    //   2. No worktree yet + task has a `parent_task_id` — walk the parent
    //      chain until we find an agent with a worktree (HITL / derive).
    //   3. Fresh `worktree_create` if the group opted in.
    // Matches `loom/server.py:4519-4573`. Must run *before* the PTY spawns
    // so the shell starts inside the worktree.
    if is_new_agent {
        let inherit_from = optional_str(req, "inherit_worktree_from")
            .unwrap_or("")
            .trim()
            .to_string();

        // Path 1 + 2: inherit from another agent.
        let inherited = if !inherit_from.is_empty() {
            let mut st = ctx.state.lock().await;
            apply_inherited_worktree(&mut st, &target_agent_id, &inherit_from)
        } else if !task.parent_task_id.is_empty() {
            let mut st = ctx.state.lock().await;
            inherit_worktree_from_parent_chain(&mut st, &target_agent_id, &task.parent_task_id)
        } else {
            false
        };

        if inherited {
            let cell_snapshot = {
                let st = ctx.state.lock().await;
                st.agents.get(&target_agent_id).cloned()
            };
            if let Some(cell) = cell_snapshot {
                let _ = ctx.db.save_agent(&cell).await;
            }
        } else if let Some(settings) = new_agent_group_settings.as_ref() {
            // Path 3: fresh worktree.
            let worktree_wanted = settings.git_worktree
                && req
                    .get("worktree")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(true);
            if worktree_wanted {
                let cell_snapshot = {
                    let st = ctx.state.lock().await;
                    st.agents.get(&target_agent_id).cloned()
                };
                if let Some(cell) = cell_snapshot {
                    let updated =
                        crate::commands::agents::try_create_agent_worktree(ctx, cell, settings)
                            .await;
                    let _ = ctx.db.save_agent(&updated).await;
                }
            }
        }
    }

    // Render the prompt.
    let mut rendered = if task.action_name.is_empty() || force_no_action {
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

    // Append uploaded attachments and artifacts (+ upstream handoff) so the
    // agent sees them in the dispatched prompt. Mirrors
    // `_append_task_artifacts` in `loom/server_agent.py`.
    let upstream_artifacts: Vec<Value> = req
        .get("upstream_artifacts")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    rendered = loom_core::artifacts::append_task_artifacts(
        &rendered,
        &task.attachments,
        &task.artifacts,
        &upstream_artifacts,
    );

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
    let bridge_options = bridge_create_session_options(ctx, &target_agent_id).await;
    let bridge_agent =
        ensure_bridge_session(ctx, &target_agent_id, bridge_options.as_ref()).await?;
    if let Some(agent) = bridge_agent.as_ref() {
        if crate::commands::agents::bridge_bootstrap_failed(agent) {
            flush(ctx).await;
            return Err(CmdError::BadRequest(agent.error_message.clone()));
        }
    }
    let bridge_managed = bridge_agent
        .as_ref()
        .map(|a| bridge_manages_cell(&ctx.terminal_bridge, a))
        .unwrap_or(false);
    if ctx.ui_agents.is_attached(&target_agent_id) {
        if is_new_agent {
            tokio::time::sleep(Duration::from_millis(2000)).await;
        }
        // `send_text` writes to Ghostty's display buffer as IME-style text and
        // is the path the UI currently uses. We send the prompt body, pause
        // briefly so it's treated as a paste, then send the newline separately
        // to land outside bracketed paste mode.
        ctx.ui_agents.send_text(&target_agent_id, rendered.clone());
        tokio::time::sleep(Duration::from_millis(50)).await;
        ctx.ui_agents.send_submit(&target_agent_id);
    } else if let Some(agent) = bridge_agent
        .as_ref()
        .filter(|a| bridge_manages_cell(&ctx.terminal_bridge, a) && a.session_id.is_some())
    {
        if is_new_agent {
            tokio::time::sleep(Duration::from_millis(2000)).await;
        }
        ctx.terminal_bridge
            .send_text(
                &target_agent_id,
                agent.session_id.as_deref(),
                &rendered,
                false,
            )
            .await
            .map_err(|e| CmdError::BadRequest(e.to_string()))?;
    } else if bridge_managed {
        return Err(CmdError::BadRequest(format!(
            "agent '{target_agent_id}' has no live terminal bridge session"
        )));
    } else if let Some(pty) = &ctx_pty(ctx).await {
        let agent = {
            let st = ctx.state.lock().await;
            st.agents.get(&target_agent_id).cloned()
        };
        if let Some(agent) = agent {
            // Only boot a fresh shell if the agent has no live PTY session.
            // Checking `agent.status != "running"` was wrong: an agent that
            // finished a turn is "idle" but its shell + Claude are still
            // alive. Spawning a new PTY on top of that would leave the old
            // session orphaned and re-run the boot command in a second
            // shell, which is what the user saw as "a new terminal opens
            // and claude runs again".
            let has_live_session = pty.has_session(&agent.id).await;
            let mut spawned_now = false;
            if !has_live_session {
                // Boot the agent's command. Strip any stale append-system-
                // prompt-file flag before re-applying so we don't stack.
                let base_command = if agent.command.is_empty() {
                    loom_core::config::default_command()
                } else {
                    agent.command.clone()
                };
                let base_command =
                    loom_core::prompts::strip_append_system_prompt_flag(&base_command);
                let cwd = if agent.directory.is_empty() {
                    None
                } else {
                    Some(crate::paths::expand_user_path(&agent.directory))
                };
                let command =
                    apply_persistent_prompt_flag(ctx, &agent, &base_command, cwd.as_deref()).await;
                let mut env = std::collections::HashMap::new();
                env.insert(loom_core::config::ENV_CELL_ID.to_string(), agent.id.clone());
                let _ = pty.spawn(&agent.id, &command, cwd, env, 40, 120).await;
                spawned_now = true;
            }
            send_prompt_to_local_pty(
                ctx,
                &agent,
                &rendered,
                spawned_now || is_new_agent || agent.tasks_dispatched == 0,
            )
            .await?;
        }
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
        &ctx.weaver_buffer,
        "task_dispatched",
        &agent_final.id,
        &agent_final.name,
        &agent_final.group,
        &task_final.task.chars().take(80).collect::<String>(),
        &task_final.id,
        false,
    )
    .await;

    // Weaver worklog: record this dispatch so the group's weaver sees it in
    // the worklog panel + digest. Mirrors loom/state.py `_append_weaver_worklog_entry`
    // called from the Python dispatch path. We persist first so the entry
    // gets a stable id, then append to the in-memory list under that id so
    // clients receive the same value via WS delta.
    {
        let ts = now_ts();
        let weaver_agent_id = {
            let st = ctx.state.lock().await;
            st.group_settings
                .get(&task_final.group)
                .map(|g| g.weaver_agent_id.clone())
                .unwrap_or_default()
        };
        if !weaver_agent_id.is_empty() {
            let mut entry = serde_json::json!({
                "group": task_final.group,
                "task_id": task_final.id,
                "task_title": task_final.task,
                "agent_id": agent_final.id,
                "agent_name": agent_final.name,
                "agent_slug": agent_final.slug,
                "agent_owned": agent_final.created_by_weaver_id == weaver_agent_id
                    && !weaver_agent_id.is_empty(),
                "started_at": ts,
            });
            let entry_json = serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into());
            if let Ok(row_id) = ctx
                .db
                .append_weaver_worklog(&task_final.group, &entry_json, ts)
                .await
            {
                if let Some(obj) = entry.as_object_mut() {
                    obj.insert("id".into(), serde_json::json!(row_id));
                }
                let _ = ctx.db.trim_weaver_worklog(&task_final.group, 200).await;
            }
            let mut st = ctx.state.lock().await;
            st.append_weaver_worklog_entry(&task_final.group, entry);
        }
    }

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

pub(crate) async fn send_text_to_cell_quiet(
    ctx: &CmdContext,
    cell_id: &str,
    text: &str,
) -> Result<(), CmdError> {
    let agent = {
        let st = ctx.state.lock().await;
        st.agents.get(cell_id).cloned()
    }
    .ok_or_else(|| CmdError::BadRequest(format!("agent '{cell_id}' not found")))?;

    if text == "\r" || text == "\n" || text == "\r\n" {
        if ctx.ui_agents.send_submit(cell_id) {
            return Ok(());
        }
    } else if ctx.ui_agents.is_attached(cell_id) {
        ctx.ui_agents.send_text(cell_id, text.to_string());
        tokio::time::sleep(Duration::from_millis(50)).await;
        ctx.ui_agents.send_submit(cell_id);
        return Ok(());
    }

    let bridge_agent = ensure_bridge_session_quiet(ctx, &cell_id, None).await?;
    if let Some(agent) = bridge_agent.as_ref() {
        if bridge_manages_cell(&ctx.terminal_bridge, &agent) && agent.session_id.is_some() {
            ctx.terminal_bridge
                .send_text(&cell_id, agent.session_id.as_deref(), &text, false)
                .await
                .map_err(|e| CmdError::BadRequest(e.to_string()))?;
            return Ok(());
        }
    }
    if bridge_agent
        .as_ref()
        .map(|a| bridge_manages_cell(&ctx.terminal_bridge, a))
        .unwrap_or(false)
    {
        return Err(CmdError::BadRequest(format!(
            "agent '{cell_id}' has no live terminal bridge session"
        )));
    }

    if let Some(pty) = ctx_pty(ctx).await {
        if text == "\r" || text == "\n" || text == "\r\n" {
            pty.write(cell_id, b"\r")
                .await
                .map_err(|e| CmdError::BadRequest(e.to_string()))?;
        } else {
            send_prompt_to_local_pty(ctx, &agent, text, true).await?;
        }
        return Ok(());
    }

    Err(CmdError::BadRequest(format!(
        "agent '{cell_id}' has no live delivery path"
    )))
}

pub async fn send_text_to_cell(
    ctx: &CmdContext,
    cell_id: &str,
    text: &str,
) -> Result<(), CmdError> {
    send_text_to_cell_quiet(ctx, cell_id, text).await?;
    let _ = mark_agent_running(ctx, cell_id).await?;
    Ok(())
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
            if text == "\r" || text == "\n" || text == "\r\n" {
                ctx.ui_agents.send_submit(id);
            } else {
                ctx.ui_agents.send_text(id, text.clone());
            }
            continue;
        }
        if let Some(agent) = ensure_bridge_session(ctx, id, None).await? {
            if bridge_manages_cell(&ctx.terminal_bridge, &agent) && agent.session_id.is_some() {
                if let Err(err) = ctx
                    .terminal_bridge
                    .send_text(id, agent.session_id.as_deref(), &text, false)
                    .await
                {
                    warn!(?err, agent_id = %id, "terminal bridge broadcast send_text failed");
                }
                continue;
            } else if bridge_manages_cell(&ctx.terminal_bridge, &agent) {
                warn!(agent_id = %id, "skipping broadcast because the terminal bridge session is not ready");
                continue;
            }
        }
        if let Some(pty) = &pty {
            let _ = pty.write(id, text.as_bytes()).await;
        }
    }
    Ok(json!({ "ok": true, "sent_to": targets.len() }))
}

pub async fn relaunch_agent(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let bridge_cell = {
        let st = ctx.state.lock().await;
        st.agents.get(&id).cloned()
    };
    if let Some(cell) = bridge_cell.as_ref() {
        if bridge_manages_cell(&ctx.terminal_bridge, cell) && cell.session_id.is_some() {
            if let Err(err) = ctx
                .terminal_bridge
                .close_session(&id, cell.session_id.as_deref())
                .await
            {
                warn!(?err, agent_id = %id, "terminal bridge relaunch close_session failed");
            }
        }
    }
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
        // Build two separate strings for weaver relaunch:
        //   * `system_prompt`: the full role/policy block, handed to the
        //     adapter so `--append-system-prompt-file` is re-injected on
        //     boot (bridge path passes this through CreateSessionOptions;
        //     LocalPty path re-injects via apply_persistent_prompt_flag).
        //   * `first_message`: custom instructions alone, or a short
        //     kickoff hint. Never the full system prompt — that's already
        //     in the file.
        let (weaver_system_prompt, weaver_first_message) = {
            let st = ctx.state.lock().await;
            let group_settings = st.get_group_settings(&a.group);
            if group_settings.weaver_agent_id == a.id {
                let ws = st.get_weaver_settings(&a.group);
                (
                    Some(crate::commands::agents::build_weaver_prompt(
                        &a.group,
                        &group_settings,
                        &ws,
                        "",
                    )),
                    Some(crate::commands::agents::build_weaver_first_message(Some(
                        &ws,
                    ))),
                )
            } else {
                (None, None)
            }
        };
        let mut delivery_cell = a.clone();
        if bridge_manages_cell(&ctx.terminal_bridge, &a) {
            let settings = {
                let st = ctx.state.lock().await;
                st.group_settings.get(&a.group).cloned().unwrap_or_default()
            };
            let options = CreateSessionOptions {
                env_vars: crate::commands::agents::merged_env(
                    &settings.env_vars,
                    &settings.agent_env_vars,
                    None,
                ),
                env_file: crate::commands::agents::first_nonempty([
                    settings.agent_env_file.clone(),
                    settings.env_file.clone(),
                ]),
                shell: crate::commands::agents::first_nonempty([
                    settings.agent_shell.clone(),
                    settings.shell.clone(),
                    std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into()),
                ]),
                target_window_id: a.window_id.clone(),
                system_prompt: weaver_system_prompt.clone().unwrap_or_default(),
                ..Default::default()
            };
            if let Some(agent) = ensure_bridge_session(ctx, &a.id, Some(&options)).await? {
                if crate::commands::agents::bridge_bootstrap_failed(&agent) {
                    flush(ctx).await;
                    return Err(CmdError::BadRequest(agent.error_message));
                }
                delivery_cell = agent;
            }
        } else if a.cell_type == "terminal" || !a.command.is_empty() {
            spawn_cell_session(ctx, &a, None).await?;
        }
        if let Some(msg) = weaver_first_message.as_ref() {
            if !msg.is_empty() {
                crate::commands::agents::deliver_agent_prompt(ctx, &delivery_cell, msg).await?;
            }
        }
    }
    flush(ctx).await;
    ok()
}

// ---------------------------------------------------------------------------
// ai_report — unified handler for all `loom ai` actions
// ---------------------------------------------------------------------------

async fn handle_agent_ask(
    ctx: &CmdContext,
    agent_id: &str,
    task_id: &str,
    question: &str,
    description: &str,
) -> CmdResult {
    if question.trim().is_empty() {
        return Err(CmdError::BadRequest("Ask requires a question".into()));
    }

    let (agent, parent_task, root_task, ask_task, next_child_number) = {
        let mut st = ctx.state.lock().await;
        if task_id.trim().is_empty() {
            return Err(CmdError::BadRequest("No linked task to derive from".into()));
        }
        let Some(parent) = st.board_tasks.get(task_id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };
        let Some(cell) = st.agents.get_mut(agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };

        let now = chrono::Utc::now().to_rfc3339();
        cell.activity.clear();
        cell.activity_detail.clear();
        cell.needs_attention = true;
        cell.error_message.clear();
        cell.last_event_at = chrono::Utc::now().timestamp() as f64;
        let agent = cell.clone();
        st.emit_agent(agent_id);

        let mut updated_parent = parent.clone();
        updated_parent.status = "Awaiting Input".into();
        updated_parent.updated_at = now.clone();
        updated_parent.messages.push(json!({
            "timestamp": now,
            "action": "ask",
            "message": question,
            "agent_id": agent_id,
            "agent_name": agent.name,
        }));
        st.upsert_task(updated_parent.clone())?;

        let mut updated_root = None;
        let root_id = if parent.pipeline_root_id.is_empty() {
            parent.id.clone()
        } else {
            parent.pipeline_root_id.clone()
        };
        if root_id != parent.id {
            if let Some(root) = st.board_tasks.get(&root_id).cloned() {
                let mut root = root;
                root.status = "Awaiting Input".into();
                root.updated_at = chrono::Utc::now().to_rfc3339();
                st.upsert_task(root.clone())?;
                updated_root = Some(root);
            }
        }

        let (ask_id, next_child_number) = {
            let counter = st
                .pipeline_task_counters
                .entry(root_id.clone())
                .or_insert(1);
            let next_child = (*counter).max(1);
            *counter = next_child + 1;
            let ask_id = if next_child == 1 {
                format!("{root_id}a")
            } else {
                format!("{root_id}a{next_child}")
            };
            (ask_id, *counter)
        };
        let mut ask_task = loom_core::state::BoardTask::new_minimal(ask_id, question.to_string());
        ask_task.group = updated_parent.group.clone();
        ask_task.description = description.to_string();
        ask_task.lane = "Backlog".into();
        ask_task.parent_task_id = updated_parent.id.clone();
        ask_task.pipeline_depth = updated_parent.pipeline_depth + 1;
        ask_task.pipeline_root_id = root_id.clone();
        ask_task.labels = vec!["loom:human".into(), "loom:derived".into()];
        ask_task.created_at = chrono::Utc::now().to_rfc3339();
        ask_task.updated_at = ask_task.created_at.clone();
        ask_task.lane_entered_at = ask_task.created_at.clone();
        st.upsert_task(ask_task.clone())?;
        let ask_task = st
            .board_tasks
            .get(&ask_task.id)
            .cloned()
            .unwrap_or(ask_task);

        (
            agent,
            updated_parent,
            updated_root,
            ask_task,
            next_child_number,
        )
    };

    ctx.db.save_agent(&agent).await?;
    ctx.db.save_board_task(&parent_task).await?;
    if let Some(root_task) = &root_task {
        ctx.db.save_board_task(root_task).await?;
    }
    ctx.db
        .save_pipeline_task_counter(&ask_task.pipeline_root_id, next_child_number)
        .await?;
    ctx.db.save_board_task(&ask_task).await?;
    ctx.db
        .save_agent_message_record(&agent.id, &parent_task.id, now_ts(), "ask", question)
        .await?;

    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        &ctx.weaver_buffer,
        "ask_created",
        &agent.id,
        &agent.name,
        &agent.group,
        question,
        &ask_task.id,
        false,
    )
    .await;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "action": "ask",
        "task_id": parent_task.id,
        "ask_task_id": ask_task.id,
    }))
}

pub async fn ai_report(ctx: &CmdContext, req: &Value) -> CmdResult {
    let action = required_str(req, "action")?.to_string();
    let agent_id = optional_str(req, "agent_id")
        .map(String::from)
        .ok_or_else(|| CmdError::BadRequest("missing agent_id".into()))?;
    let message = optional_str(req, "message").unwrap_or("").to_string();
    let description = optional_str(req, "description").unwrap_or("").to_string();
    let explicit_task_id = optional_str(req, "task_id").unwrap_or("").to_string();
    let task_id = {
        let st = ctx.state.lock().await;
        resolve_ai_report_task_id(&st, &agent_id, &explicit_task_id)
    };

    if action == "ask" {
        return handle_agent_ask(ctx, &agent_id, &task_id, &message, &description).await;
    }
    if action == "reply" {
        return crate::commands::weaver::handle_weaver_reply(ctx, &agent_id, &task_id, &message)
            .await;
    }

    let (task, agent, cascaded): (Option<loom_core::state::BoardTask>, loom_core::state::AgentCell, CascadeResult) = {
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
                _ => {}
            }
            cell.last_event_at = chrono::Utc::now().timestamp() as f64;
        }
        st.emit_agent(&agent_id);

        // Update linked task.
        let mut task = None;
        let mut cascaded = CascadeResult::default();
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

            // Cascade completion: if all siblings of the parent are now Done,
            // promote the parent; walk the chain.
            if matches!(action.as_str(), "done" | "ready") {
                cascaded = cascade_done(&mut st, &task_id);
            }
        }
        let agent = st.agents.get(&agent_id).cloned().unwrap();
        (task, agent, cascaded)
    };

    if let Some(t) = &task {
        ctx.db.save_board_task(t).await?;
    }
    for promoted in &cascaded.tasks {
        ctx.db.save_board_task(promoted).await?;
    }
    for agent in &cascaded.unlinked_agents {
        ctx.db.save_agent(agent).await?;
    }
    ctx.db.save_agent(&agent).await?;
    ctx.db
        .save_agent_message_record(&agent.id, &task_id, now_ts(), &action, &message)
        .await?;

    // Progress-triggered auto-checkpoint. Python throttles to one commit per
    // 5 minutes via `_checkpoint_on_report`; mirror that exactly — including
    // the "no-op if no worktree or not opted in" guards — so an agent that
    // reports progress every few seconds doesn't spam commits.
    if action == "progress"
        && agent.cell_type == "agent"
        && agent.checkpoint_on_progress
        && !agent.worktree_path.is_empty()
    {
        let now = chrono::Utc::now().timestamp() as f64;
        let due = agent.last_checkpoint_at == 0.0
            || (now - agent.last_checkpoint_at)
                >= crate::commands::worktree::PROGRESS_CHECKPOINT_INTERVAL_SECS;
        if due {
            let msg = crate::commands::worktree::auto_checkpoint_message(
                &agent.name,
                agent.worktree_checkpoints,
                &message,
            );
            match crate::commands::worktree::do_checkpoint_for_agent(ctx, &agent.id, &msg).await {
                Ok(sha) => {
                    if sha.is_some() {
                        tracing::info!(agent = %agent.id, "progress-checkpoint committed");
                    }
                }
                Err(err) => {
                    tracing::warn!(agent = %agent.id, ?err, "progress-checkpoint failed");
                }
            }
        }
    }

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
                &ctx.weaver_buffer,
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
                &ctx.weaver_buffer,
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
                &ctx.weaver_buffer,
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
                &ctx.weaver_buffer,
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
                &ctx.weaver_buffer,
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
    if reply.trim().is_empty() {
        return Err(CmdError::BadRequest("Answer is required".into()));
    }

    let (question, parent_agent_id, agent_name, agent_group) = {
        let st = ctx.state.lock().await;
        let Some(ask_task) = st.board_tasks.get(&task_id) else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };
        if !ask_task.labels.iter().any(|label| label == "loom:human") {
            return Err(CmdError::BadRequest("Not an ask task".into()));
        }
        if ask_task.parent_task_id.is_empty() {
            return Err(CmdError::BadRequest("Ask task has no parent".into()));
        }
        let Some(parent) = st.board_tasks.get(&ask_task.parent_task_id) else {
            return Err(CmdError::BadRequest("Parent task not found".into()));
        };
        if parent.agent_id.trim().is_empty() {
            return Err(CmdError::BadRequest(
                "Parent task has no linked agent".into(),
            ));
        }
        let Some(agent) = st.agents.get(&parent.agent_id) else {
            return Err(CmdError::BadRequest("Parent agent not available".into()));
        };
        (
            ask_task.task.clone(),
            parent.agent_id.clone(),
            agent.name.clone(),
            agent.group.clone(),
        )
    };

    let truncated_question = if question.chars().count() > 120 {
        let mut truncated = question.chars().take(120).collect::<String>();
        truncated.push('…');
        truncated
    } else {
        question.clone()
    };
    send_text_to_cell(
        ctx,
        &parent_agent_id,
        &format!("Answer to your question \"{truncated_question}\":\n{reply}"),
    )
    .await
    .map_err(|_| CmdError::BadRequest("Parent agent not available".into()))?;

    let (ask_task, parent, root, cascaded) = {
        let mut st = ctx.state.lock().await;
        let Some(mut ask_task) = st.board_tasks.get(&task_id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };
        if !loom_core::state::board_task_is_closed(Some(&ask_task)) {
            ask_task.lane = "Done".into();
        }
        ask_task.status.clear();
        ask_task.lane_entered_at = chrono::Utc::now().to_rfc3339();
        ask_task.updated_at = ask_task.lane_entered_at.clone();
        ask_task.messages.push(json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "action": "reply",
            "message": reply,
        }));
        st.upsert_task(ask_task.clone())?;

        let Some(mut parent) = st.board_tasks.get(&ask_task.parent_task_id).cloned() else {
            return Err(CmdError::BadRequest("Parent task not found".into()));
        };
        parent.messages.push(json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "action": "ask_reply",
            "message": reply,
            "from_task_id": ask_task.id,
        }));
        parent.updated_at = chrono::Utc::now().to_rfc3339();
        st.upsert_task(parent.clone())?;

        let (parent, root) = match clear_parent_awaiting_input(&mut st, &parent.id, &ask_task.id) {
            Some((updated_parent, updated_root)) => {
                if updated_parent.updated_at > parent.updated_at {
                    parent = updated_parent;
                }
                (parent, updated_root)
            }
            None => (parent, None),
        };

        // Marking the ask Done can unblock ancestor cascade: if a sibling
        // `loom_done` already fired and stopped at an open ancestor because
        // this ask was still pending, it's time to close that ancestor out.
        // Mirrors Python server.py which cascades on every Done transition.
        let cascaded = cascade_done(&mut st, &ask_task.id);

        (ask_task, Some(parent), root, cascaded)
    };

    ctx.db.save_board_task(&ask_task).await?;
    if let Some(p) = &parent {
        ctx.db.save_board_task(p).await?;
    }
    if let Some(root) = &root {
        ctx.db.save_board_task(root).await?;
    }
    for promoted in &cascaded.tasks {
        ctx.db.save_board_task(promoted).await?;
    }
    for agent in &cascaded.unlinked_agents {
        ctx.db.save_agent(agent).await?;
    }
    let _ = crate::commands::compat::record_panel_event(
        &ctx.state,
        &ctx.db,
        &ctx.weaver_buffer,
        "ask_resolved",
        &parent_agent_id,
        &agent_name,
        &agent_group,
        &format!("Resolved: {truncated_question}"),
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

async fn ctx_pty(ctx: &CmdContext) -> Option<crate::pty_backend::PtyBackend> {
    ctx.pty.clone()
}

fn adapter_for_cell(cell: &AgentCell) -> Option<Arc<dyn AgentAdapter>> {
    if !cell.agent_type.trim().is_empty() {
        loom_adapters::get_adapter(cell.agent_type.trim())
    } else if !cell.command.trim().is_empty() {
        loom_adapters::detect_by_command(cell.command.trim()).and_then(loom_adapters::get_adapter)
    } else {
        None
    }
}

async fn wait_for_local_pty_ready(ctx: &CmdContext, cell: &AgentCell, adapter: &dyn AgentAdapter) {
    match adapter.input_ready_policy() {
        InputReadyPolicy::Never | InputReadyPolicy::Always => return,
        InputReadyPolicy::OnPrompt | InputReadyPolicy::OnIdle => {}
    }
    let timeout = adapter.input_ready_timeout();
    if timeout.is_zero() {
        return;
    }
    let deadline = Instant::now() + timeout;
    let poll_interval = adapter.input_ready_poll_interval();
    let stable_required = adapter.input_ready_stable_polls().max(1);
    let mut stable_polls = 0usize;
    while Instant::now() < deadline {
        let (session_id, buffer) = ctx.terminals.snapshot(&cell.id).await;
        if !session_id.is_empty() && adapter.is_input_ready_screen(&buffer) {
            stable_polls += 1;
            if stable_polls >= stable_required {
                let post_ready_delay = adapter.input_ready_post_ready_delay();
                if !post_ready_delay.is_zero() {
                    tokio::time::sleep(post_ready_delay).await;
                }
                return;
            }
        } else {
            stable_polls = 0;
        }
        tokio::time::sleep(poll_interval).await;
    }
}

async fn wait_for_local_pty_echo(ctx: &CmdContext, cell: &AgentCell, body: &str) {
    let needle = body
        .lines()
        .rev()
        .find_map(|line| {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(
                    trimmed
                        .chars()
                        .rev()
                        .take(96)
                        .collect::<String>()
                        .chars()
                        .rev()
                        .collect::<String>(),
                )
            }
        })
        .unwrap_or_default();
    if needle.is_empty() {
        return;
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline {
        let (_, buffer) = ctx.terminals.snapshot(&cell.id).await;
        if buffer.contains(&needle) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

pub(crate) async fn send_prompt_to_local_pty(
    ctx: &CmdContext,
    cell: &AgentCell,
    text: &str,
    wait_for_ready: bool,
) -> Result<(), CmdError> {
    let Some(pty) = ctx_pty(ctx).await else {
        return Err(CmdError::BadRequest(format!(
            "agent '{}' has no live delivery path",
            cell.id
        )));
    };
    let body = text.trim_end_matches(['\r', '\n']);
    if body.is_empty() {
        return Ok(());
    }
    let adapter = adapter_for_cell(cell);
    if wait_for_ready {
        if let Some(adapter) = adapter.as_ref() {
            wait_for_local_pty_ready(ctx, cell, adapter.as_ref()).await;
        }
    }
    let chunks = adapter
        .as_ref()
        .map(|adapter| adapter.input_chunks(body))
        .unwrap_or_else(|| vec![body.to_string()]);
    for chunk in chunks {
        if chunk.is_empty() {
            continue;
        }
        pty.write(&cell.id, chunk.as_bytes())
            .await
            .map_err(|err| CmdError::BadRequest(err.to_string()))?;
    }
    wait_for_local_pty_echo(ctx, cell, body).await;
    let delay = adapter
        .as_ref()
        .map(|adapter| adapter.multiline_submit_delay())
        .unwrap_or_else(|| Duration::from_millis(300));
    if !delay.is_zero() {
        tokio::time::sleep(delay).await;
    }
    let submit_key = adapter
        .as_ref()
        .map(|adapter| adapter.submit_key().to_string())
        .unwrap_or_else(|| "\r".to_string());
    if !submit_key.is_empty() {
        pty.write(&cell.id, submit_key.as_bytes())
            .await
            .map_err(|err| CmdError::BadRequest(err.to_string()))?;
    }
    Ok(())
}

async fn ensure_bridge_session_quiet(
    ctx: &CmdContext,
    cell_id: &str,
    options: Option<&CreateSessionOptions>,
) -> Result<Option<loom_core::state::AgentCell>, CmdError> {
    let agent = {
        let st = ctx.state.lock().await;
        st.agents.get(cell_id).cloned()
    };
    let Some(agent) = agent else {
        return Ok(None);
    };
    if !bridge_manages_cell(&ctx.terminal_bridge, &agent) {
        return Ok(Some(agent));
    }
    if agent.session_id.is_some() {
        return Ok(Some(agent));
    }

    let default_options = CreateSessionOptions::default();
    let bridged = match ctx
        .terminal_bridge
        .create_session(&agent, options.unwrap_or(&default_options))
        .await
    {
        Ok(Some(updated)) => updated,
        Ok(None) => agent,
        Err(err) => {
            warn!(?err, agent_id = %cell_id, "terminal bridge create_session during dispatch failed");
            let mut errored = agent;
            errored.status = "error".into();
            errored.error_message = err.to_string();
            errored
        }
    };
    let saved = {
        let mut st = ctx.state.lock().await;
        st.agents.insert(bridged.id.clone(), bridged.clone());
        st.emit_agent(&bridged.id);
        st.agents.get(&bridged.id).cloned().unwrap()
    };
    ctx.db.save_agent(&saved).await?;
    Ok(Some(saved))
}

async fn ensure_bridge_session(
    ctx: &CmdContext,
    cell_id: &str,
    options: Option<&CreateSessionOptions>,
) -> Result<Option<loom_core::state::AgentCell>, CmdError> {
    let saved = ensure_bridge_session_quiet(ctx, cell_id, options).await?;
    flush(ctx).await;
    Ok(saved)
}

async fn bridge_create_session_options(
    ctx: &CmdContext,
    cell_id: &str,
) -> Option<CreateSessionOptions> {
    let (agent, settings) = {
        let st = ctx.state.lock().await;
        let agent = st.agents.get(cell_id)?.clone();
        let settings = st
            .group_settings
            .get(&agent.group)
            .cloned()
            .unwrap_or_default();
        (agent, settings)
    };
    Some(CreateSessionOptions {
        env_vars: crate::commands::agents::merged_env(
            &settings.env_vars,
            &settings.agent_env_vars,
            None,
        ),
        env_file: crate::commands::agents::first_nonempty([
            settings.agent_env_file.clone(),
            settings.env_file.clone(),
        ]),
        shell: crate::commands::agents::first_nonempty([
            settings.agent_shell.clone(),
            settings.shell.clone(),
            std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into()),
        ]),
        target_window_id: agent.window_id,
        ..Default::default()
    })
}

pub async fn spawn_cell_session(
    ctx: &CmdContext,
    cell: &AgentCell,
    extra_env: Option<&std::collections::BTreeMap<String, String>>,
) -> Result<(), CmdError> {
    if bridge_manages_cell(&ctx.terminal_bridge, cell) {
        return Ok(());
    }
    let Some(pty) = ctx_pty(ctx).await else {
        return Ok(());
    };
    if cell.status == "running" && cell.session_id.is_some() {
        return Ok(());
    }
    let base_command = if cell.command.is_empty() {
        loom_core::config::default_command()
    } else {
        cell.command.clone()
    };
    // Strip any stale append-system-prompt-file flag we appended on a prior
    // boot so we don't stack the flag on each respawn.
    let base_command = loom_core::prompts::strip_append_system_prompt_flag(&base_command);
    // If the cell opted in to session resume and we've persisted a provider
    // session id from a prior SessionStart hook, rewrite the boot command
    // to the adapter's resume form (e.g. `claude --resume <sid>`). Matches
    // Python local_pty.py:609 + iterm2_bridge_core.py:469.
    let base_command = if cell.session_resume && !cell.agent_session_id.trim().is_empty() {
        if let Some(adapter) = adapter_for_cell(cell) {
            adapter
                .resume_command(&base_command, &cell.agent_session_id)
                .unwrap_or(base_command)
        } else {
            base_command
        }
    } else {
        base_command
    };
    let cwd = if cell.directory.is_empty() {
        None
    } else {
        Some(crate::paths::expand_user_path(&cell.directory))
    };
    if let Some(dir) = cwd.as_deref() {
        install_provider_integration(dir, &base_command).await;
    }
    // Install the persistent Loom system prompt file (worker-role prompt,
    // or the weaver prompt for a designated weaver cell) and append the
    // adapter's boot flag. Best-effort — if the adapter doesn't produce a
    // flag (e.g. Codex writes to a config file), the command goes out
    // unchanged. Mirrors Python's `apply_persistent_prompt` in server_agent.
    let command = apply_persistent_prompt_flag(ctx, cell, &base_command, cwd.as_deref()).await;
    let mut env = std::collections::HashMap::new();
    // Inherit the parent environment so the user's PATH, HOME, SSH agent,
    // shell-specific XDG dirs, etc. are all present. Strip iTerm-injected
    // vars so the child shell's TUI apps don't try to talk to a terminal
    // emulator that isn't on the other end of this PTY. Then layer on our
    // own vars last so they win.
    for (k, v) in std::env::vars() {
        if k.starts_with("ITERM_") {
            continue;
        }
        if matches!(
            k.as_str(),
            "LC_TERMINAL"
                | "LC_TERMINAL_VERSION"
                | "TERM_SESSION_ID"
                | "TERM_FEATURES"
                | "TERMINFO_DIRS"
                | "TERM_PROGRAM"
                | "TERM_PROGRAM_VERSION"
                | "STARSHIP_SESSION_KEY"
                | "STARSHIP_SHELL"
        ) {
            continue;
        }
        env.insert(k, v);
    }
    env.insert("TERM".into(), "xterm-256color".into());
    env.insert("COLORTERM".into(), "truecolor".into());
    env.insert("CLAUDE_GATEWAY_NO_AUTO_UPDATE".into(), "true".into());
    env.insert("DISABLE_AUTOUPDATER".into(), "1".into());
    env.insert("LOOM_STANDALONE_PTY".into(), "1".into());
    env.insert(loom_core::config::ENV_CELL_ID.to_string(), cell.id.clone());
    if let Some(extra) = extra_env {
        env.extend(extra.iter().map(|(k, v)| (k.clone(), v.clone())));
    }
    pty.spawn(&cell.id, &command, cwd, env, 40, 120)
        .await
        .map_err(|e| CmdError::BadRequest(e.to_string()))?;
    Ok(())
}

/// Build the adapter-specific persistent prompt for `cell` and return the
/// boot command with the appropriate CLI flag appended (e.g.
/// `claude --append-system-prompt-file <path>` for Claude Code).
/// No-op when the cell has no working directory, no detected adapter, or
/// the adapter doesn't emit a flag. Called every spawn — the adapter
/// writes the prompt file fresh so changes to group / weaver settings are
/// picked up on the next boot.
async fn apply_persistent_prompt_flag(
    ctx: &CmdContext,
    cell: &AgentCell,
    base_command: &str,
    working_dir: Option<&std::path::Path>,
) -> String {
    let Some(dir) = working_dir else {
        return base_command.to_string();
    };
    // Resolve which adapter drives this cell. Prefer the explicit
    // `agent_type`; fall back to command-sniffing for legacy cells that
    // never got an agent_type written.
    let adapter = if !cell.agent_type.is_empty() {
        loom_adapters::registry::get_adapter(&cell.agent_type)
    } else {
        loom_adapters::registry::detect_by_command(base_command)
            .and_then(loom_adapters::registry::get_adapter)
    };
    let Some(adapter) = adapter else {
        return base_command.to_string();
    };

    // Pick the right prompt body: designated weaver cells get the weaver
    // prompt; everyone else gets the worker system prompt. User-supplied
    // system_prompt overrides (Python's `launch_cfg["system_prompt"]`)
    // aren't persisted in GroupSettings yet, so we feed an empty extra
    // prompt for now — the core Loom instructions still go through.
    let (prompt_text, filename) = {
        let st = ctx.state.lock().await;
        let gs = st.get_group_settings(&cell.group);
        let is_weaver = !gs.weaver_agent_id.is_empty() && gs.weaver_agent_id == cell.id;
        if is_weaver {
            let ws = st.get_weaver_settings(&cell.group);
            let body = loom_weaver::weaver::build_weaver_system_prompt(
                &cell.group,
                Some(&ws),
                "",
                Some(&gs),
            );
            (body, loom_core::prompts::weaver_prompt_filename(&cell.group))
        } else {
            let body = loom_core::prompts::build_dispatch_persistent_prompt("");
            (body, loom_core::prompts::persistent_prompt_filename(&cell.id))
        }
    };

    if prompt_text.trim().is_empty() {
        return base_command.to_string();
    }

    let suffix = adapter
        .inject_persistent_prompt(dir, &filename, &prompt_text)
        .await;
    if suffix.is_empty() {
        base_command.to_string()
    } else {
        format!("{}{}", base_command.trim_end(), suffix)
    }
}

fn now_ts() -> f64 {
    chrono::Utc::now().timestamp_millis() as f64 / 1000.0
}
