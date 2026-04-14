//! Worktree commands. Create, remove, list, prune, checkpoint, diff.
//!
//! The owning agent's `worktree_*` fields are updated atomically so deltas
//! reach the UI for instant badge updates.

use std::path::PathBuf;

use serde_json::{json, Value};

use loom_worktree::{
    checkpoint::{checkpoint as do_checkpoint, count_commits, list_checkpoints, rollback_to},
    diff::diff_vs_base,
    manager::WorktreeManager,
    merge::is_merged,
};

use super::{flush, ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

fn worktree_err<E: std::fmt::Display>(err: E) -> CmdError {
    CmdError::BadRequest(format!("worktree: {err}"))
}

fn agent_repo_root(
    ctx_state: &loom_core::state::MatrixState,
    agent_id: &str,
) -> Result<PathBuf, CmdError> {
    let agent = ctx_state
        .agents
        .get(agent_id)
        .ok_or_else(|| CmdError::BadRequest(format!("agent '{agent_id}' not found")))?;
    let root = if !agent.worktree_repo_root.is_empty() {
        agent.worktree_repo_root.clone()
    } else if !agent.git_root.is_empty() {
        agent.git_root.clone()
    } else if !agent.directory.is_empty() {
        agent.directory.clone()
    } else {
        return Err(CmdError::BadRequest(format!(
            "no git repo root known for agent '{agent_id}'"
        )));
    };
    Ok(PathBuf::from(root))
}

pub async fn create(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let branch = required_str(req, "branch")?.to_string();
    let base = optional_str(req, "base").unwrap_or("").to_string();

    let (repo_root, base_dir) = {
        let st = ctx.state.lock().await;
        let repo = agent_repo_root(&st, &agent_id)?;
        let base_dir = st.agents.get(&agent_id).and_then(|a| {
            if a.worktree_base_dir.is_empty() {
                None
            } else {
                let candidate = PathBuf::from(&a.worktree_base_dir);
                // Relative base_dir is interpreted relative to the repo root.
                let resolved = if candidate.is_absolute() {
                    candidate
                } else {
                    repo.join(candidate)
                };
                Some(resolved)
            }
        });
        (repo, base_dir)
    };

    let mgr = WorktreeManager::new(&repo_root, base_dir);
    let info = mgr.create(&branch, &base).await.map_err(worktree_err)?;
    loom_worktree::gitignore::ensure_git_exclude(&repo_root).map_err(worktree_err)?;

    let agent = {
        let mut st = ctx.state.lock().await;
        let Some(cell) = st.agents.get_mut(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        cell.worktree_path = info.path.to_string_lossy().to_string();
        cell.worktree_branch = info.branch.clone();
        cell.worktree_repo_root = info.repo_root.to_string_lossy().to_string();
        cell.worktree_base_branch = info.base_branch.clone();
        st.emit_agent(&agent_id);
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&agent).await?;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "path": info.path.to_string_lossy(),
        "branch": info.branch,
    }))
}

pub async fn remove(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();

    let (repo_root, path) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        if cell.worktree_path.is_empty() {
            return Err(CmdError::BadRequest("agent has no worktree".into()));
        }
        (PathBuf::from(&cell.worktree_repo_root), PathBuf::from(&cell.worktree_path))
    };

    let mgr = WorktreeManager::new(&repo_root, None);
    mgr.remove(&path).await.map_err(worktree_err)?;

    let agent = {
        let mut st = ctx.state.lock().await;
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            cell.worktree_path.clear();
            cell.worktree_branch.clear();
            cell.worktree_repo_root.clear();
            cell.worktree_base_branch.clear();
            cell.worktree_checkpoints = 0;
            cell.worktree_dirty = false;
            cell.worktree_diff = serde_json::Map::new();
        }
        st.emit_agent(&agent_id);
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&agent).await?;
    flush(ctx).await;
    ok()
}

pub async fn list(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let repo_root: PathBuf = match optional_str(req, "repo_root") {
        Some(s) if !s.is_empty() => PathBuf::from(s),
        _ => std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    };
    let mgr = WorktreeManager::new(&repo_root, None);
    let items = mgr.list().await.map_err(worktree_err)?;
    Ok(json!({ "worktrees": items }))
}

pub async fn prune(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let repo_root: PathBuf = match optional_str(req, "repo_root") {
        Some(s) if !s.is_empty() => PathBuf::from(s),
        _ => std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    };
    let mgr = WorktreeManager::new(&repo_root, None);
    mgr.prune().await.map_err(worktree_err)?;
    ok()
}

pub async fn checkpoint_cmd(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let message = optional_str(req, "message").unwrap_or("loom: checkpoint").to_string();

    let path = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        if cell.worktree_path.is_empty() {
            return Err(CmdError::BadRequest("agent has no worktree".into()));
        }
        PathBuf::from(&cell.worktree_path)
    };
    let sha = do_checkpoint(&path, &message).await.map_err(worktree_err)?;

    // update checkpoint count
    let base = {
        let st = ctx.state.lock().await;
        st.agents
            .get(&agent_id)
            .map(|a| a.worktree_base_branch.clone())
            .unwrap_or_default()
    };
    if !base.is_empty() {
        let n = count_commits(&path, &base).await.unwrap_or(0);
        let agent = {
            let mut st = ctx.state.lock().await;
            if let Some(cell) = st.agents.get_mut(&agent_id) {
                cell.worktree_checkpoints = n;
                cell.last_checkpoint_at = chrono::Utc::now().timestamp() as f64;
            }
            st.emit_agent(&agent_id);
            st.agents.get(&agent_id).cloned().unwrap()
        };
        ctx.db.save_agent(&agent).await?;
    }
    flush(ctx).await;
    Ok(json!({ "ok": true, "sha": sha }))
}

pub async fn history(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (path, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        (PathBuf::from(&cell.worktree_path), cell.worktree_base_branch.clone())
    };
    let checkpoints = list_checkpoints(&path, &base).await.map_err(worktree_err)?;
    Ok(json!({ "checkpoints": checkpoints }))
}

pub async fn diff(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (path, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        (PathBuf::from(&cell.worktree_path), cell.worktree_base_branch.clone())
    };
    let path_clone = path.clone();
    let base_clone = base.clone();
    let summary = tokio::task::spawn_blocking(move || diff_vs_base(&path_clone, &base_clone))
        .await
        .map_err(|e| CmdError::BadRequest(e.to_string()))?
        .map_err(worktree_err)?;

    // update ephemeral state
    let agent = {
        let mut st = ctx.state.lock().await;
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            cell.worktree_dirty = summary.files > 0;
            cell.worktree_changed_files = summary.changed_files.clone();
            let mut diff_obj = serde_json::Map::new();
            diff_obj.insert("files".into(), summary.files.into());
            diff_obj.insert("insertions".into(), summary.insertions.into());
            diff_obj.insert("deletions".into(), summary.deletions.into());
            cell.worktree_diff = diff_obj;
        }
        st.emit_agent(&agent_id);
        st.agents.get(&agent_id).cloned()
    };
    if agent.is_some() {
        // diff state is ephemeral — skip db write
    }
    flush(ctx).await;
    Ok(serde_json::to_value(&summary)?)
}

pub async fn rollback(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let sha = required_str(req, "sha")?.to_string();
    let path = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        PathBuf::from(&cell.worktree_path)
    };
    rollback_to(&path, &sha).await.map_err(worktree_err)?;
    ok()
}

pub async fn check_merge(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (path, branch, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!("agent '{agent_id}' not found")));
        };
        (
            PathBuf::from(&cell.worktree_path),
            cell.worktree_branch.clone(),
            cell.worktree_base_branch.clone(),
        )
    };
    let merged = is_merged(&path, &branch, &base).await.map_err(worktree_err)?;

    let agent = {
        let mut st = ctx.state.lock().await;
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            cell.worktree_merged = merged;
        }
        st.emit_agent(&agent_id);
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&agent).await?;
    flush(ctx).await;
    Ok(json!({ "merged": merged }))
}
