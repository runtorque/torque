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
        crate::paths::expand_user_path_string(&agent.directory)
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

    let (repo_root, base_dir, existing) = {
        let st = ctx.state.lock().await;
        let repo = agent_repo_root(&st, &agent_id)?;
        let agent = st.agents.get(&agent_id);
        let base_dir = agent.and_then(|a| {
            if a.worktree_base_dir.is_empty() {
                None
            } else {
                let candidate = crate::paths::expand_user_path(&a.worktree_base_dir);
                // Relative base_dir is interpreted relative to the repo root.
                let resolved = if candidate.is_absolute() {
                    candidate
                } else {
                    repo.join(candidate)
                };
                Some(resolved)
            }
        });
        let existing = agent
            .map(|a| (a.worktree_path.clone(), a.worktree_branch.clone()))
            .unwrap_or_default();
        (repo, base_dir, existing)
    };

    // Refuse to create a second worktree on top of an existing one.
    // Python guards this at `loom/server.py::dispatch_task` with
    // `if cell and not cell.worktree_path and cell.directory`, so a
    // repeat `worktree_create` for the same agent is a no-op. Without
    // this guard, Rust overwrites `cell.worktree_path`, orphaning the
    // old branch + worktree directory with no way to reach them.
    if !existing.0.is_empty() {
        return Ok(json!({
            "ok": false,
            "error": format!(
                "agent already has a worktree on branch '{}' at '{}'; remove it before creating a new one",
                existing.1, existing.0
            ),
            "path": existing.0,
            "branch": existing.1,
        }));
    }

    let mgr = WorktreeManager::new(&repo_root, base_dir);
    let info = mgr.create(&branch, &base).await.map_err(worktree_err)?;
    loom_worktree::gitignore::ensure_git_exclude(&repo_root).map_err(worktree_err)?;

    let agent = {
        let mut st = ctx.state.lock().await;
        let Some(cell) = st.agents.get_mut(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
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

/// Force-remove a cell's worktree + branch on disk. Returns true when
/// the git operations succeeded (or when the worktree was already
/// shared with another agent and therefore only unlinked from this
/// cell's fields). Mirrors Python's `_safe_remove_worktree`
/// (`loom/server.py:1314-1332`) — if any other agent still references
/// this worktree_path, the on-disk worktree stays and we only clear
/// the cell's own worktree fields.
pub(crate) async fn safe_remove_for_cell(
    ctx: &CmdContext,
    cell: &loom_core::state::AgentCell,
) -> bool {
    if cell.worktree_path.is_empty() {
        return true;
    }
    let shares_worktree = {
        let st = ctx.state.lock().await;
        st.agents
            .values()
            .any(|other| other.id != cell.id && other.worktree_path == cell.worktree_path)
    };
    if shares_worktree {
        tracing::info!(
            agent = %cell.id,
            path = %cell.worktree_path,
            "skipping on-disk worktree removal — shared with another agent"
        );
        return true;
    }
    let repo_root = if !cell.worktree_repo_root.is_empty() {
        PathBuf::from(&cell.worktree_repo_root)
    } else if !cell.git_root.is_empty() {
        PathBuf::from(&cell.git_root)
    } else {
        // Best-effort fallback: strip the last two path segments
        // (`.loom/worktrees/<name>`) off the worktree path. Matches
        // Python's `os.path.dirname` fallback at `worktree.py:1075`.
        PathBuf::from(&cell.worktree_path)
            .parent()
            .and_then(|p| p.parent())
            .and_then(|p| p.parent())
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from(&cell.worktree_path))
    };
    let mgr = WorktreeManager::new(&repo_root, None);
    let removed_ok = match mgr.remove(&PathBuf::from(&cell.worktree_path)).await {
        Ok(_) => true,
        Err(err) => {
            tracing::warn!(
                agent = %cell.id,
                path = %cell.worktree_path,
                ?err,
                "worktree remove failed"
            );
            false
        }
    };
    // Matching Python `WorktreeManager.remove_path` (`loom/worktree.py:988-1028`)
    // which also deletes the branch ref with `git branch -d` — the
    // safe variant, so a branch with unmerged work is preserved and
    // must be force-deleted manually. `WorktreeManager::remove` above
    // only unregisters the worktree; without this follow-up, the
    // branch ref leaks indefinitely.
    if !cell.worktree_branch.is_empty() {
        let out = tokio::process::Command::new("git")
            .arg("-C")
            .arg(&repo_root)
            .args(["branch", "-d", &cell.worktree_branch])
            .output()
            .await;
        if let Ok(out) = out {
            if !out.status.success() {
                tracing::warn!(
                    agent = %cell.id,
                    branch = %cell.worktree_branch,
                    stderr = %String::from_utf8_lossy(&out.stderr).trim(),
                    "branch delete after agent close failed (likely unmerged commits — use worktree_merge first or branch -D manually)"
                );
            }
        }
    }
    removed_ok
}

pub async fn remove(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();

    let cell = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        if cell.worktree_path.is_empty() {
            return Err(CmdError::BadRequest("agent has no worktree".into()));
        }
        cell.clone()
    };

    // Delegate to `safe_remove_for_cell` so we match Python's behavior
    // (`loom/server.py:3182-3187` → `_safe_remove_worktree` →
    // `WorktreeManager.remove_path`) which removes the worktree
    // directory AND the branch ref. The previous Rust handler only
    // unregistered the worktree, leaving a zombie `loom/<name>-<id>`
    // branch behind on every explicit remove.
    safe_remove_for_cell(ctx, &cell).await;

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
            cell.worktree_changed_files.clear();
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
    let message = optional_str(req, "message")
        .unwrap_or("loom: checkpoint")
        .to_string();
    let sha = do_checkpoint_for_agent(ctx, &agent_id, &message).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "sha": sha }))
}

/// Helper shared by the explicit `worktree_checkpoint` command and the
/// auto-triggers in the hook / ai_report paths. Returns the new commit sha
/// (or `None` if the worktree had no changes to commit). Updates the cell's
/// `worktree_checkpoints` + `last_checkpoint_at` and broadcasts the delta.
pub async fn do_checkpoint_for_agent(
    ctx: &CmdContext,
    agent_id: &str,
    message: &str,
) -> Result<Option<String>, CmdError> {
    let path = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        if cell.worktree_path.is_empty() {
            return Err(CmdError::BadRequest("agent has no worktree".into()));
        }
        PathBuf::from(&cell.worktree_path)
    };
    let sha = do_checkpoint(&path, message).await.map_err(worktree_err)?;

    // Update checkpoint count regardless of whether a new commit was
    // created (the count includes pre-existing commits on the branch).
    let base = {
        let st = ctx.state.lock().await;
        st.agents
            .get(agent_id)
            .map(|a| a.worktree_base_branch.clone())
            .unwrap_or_default()
    };
    if !base.is_empty() {
        let n = count_commits(&path, &base).await.unwrap_or(0);
        let agent = {
            let mut st = ctx.state.lock().await;
            if let Some(cell) = st.agents.get_mut(agent_id) {
                cell.worktree_checkpoints = n;
                cell.last_checkpoint_at = chrono::Utc::now().timestamp() as f64;
            }
            st.emit_agent(agent_id);
            st.agents.get(agent_id).cloned().unwrap()
        };
        ctx.db.save_agent(&agent).await?;
    }
    Ok(sha)
}

/// Format the Python-compatible subject + summary body for an automatic
/// checkpoint commit. Mirrors `_checkpoint_message` in loom/server.py.
pub fn auto_checkpoint_message(
    name: &str,
    checkpoints_so_far: i32,
    summary: &str,
) -> String {
    let n = checkpoints_so_far + 1;
    let subject = format!("loom: checkpoint {n} — {name}");
    let trimmed = summary.trim();
    if trimmed.is_empty() {
        subject
    } else {
        format!("{subject}\n\n{trimmed}")
    }
}

/// Minimum seconds between progress-triggered checkpoints per agent.
/// Matches Python's `_CHECKPOINT_INTERVAL`.
pub const PROGRESS_CHECKPOINT_INTERVAL_SECS: f64 = 300.0;

pub async fn history(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (path, mut base, branch) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            PathBuf::from(&cell.worktree_path),
            cell.worktree_base_branch.clone(),
            cell.worktree_branch.clone(),
        )
    };

    // Older agents may have been created before base_branch was resolved at
    // worktree creation time — recover by asking git directly. Uses `@{u}`
    // (upstream) if set, then falls back to "main" / "master". Keeps the
    // history view populated for those agents instead of silently showing
    // "No commits on this branch yet".
    if base.is_empty() && !path.as_os_str().is_empty() {
        base = resolve_fork_point(&path).await.unwrap_or_default();
        if !base.is_empty() {
            // Persist the recovered value so the next call (and other
            // worktree operations like diff/check_merge) find it.
            let mut st = ctx.state.lock().await;
            if let Some(cell) = st.agents.get_mut(&agent_id) {
                cell.worktree_base_branch = base.clone();
            }
            st.emit_agent(&agent_id);
            drop(st);
            if let Some(cell) = {
                let st = ctx.state.lock().await;
                st.agents.get(&agent_id).cloned()
            } {
                let _ = ctx.db.save_agent(&cell).await;
            }
            flush(ctx).await;
        }
    }

    let commits = if path.as_os_str().is_empty() || base.is_empty() {
        Vec::new()
    } else {
        list_checkpoints(&path, &base).await.map_err(worktree_err)?
    };
    Ok(json!({
        "type": "worktree_history",
        "id": agent_id,
        "branch": branch,
        "base_branch": base,
        "commits": commits,
    }))
}

/// Resolve a usable "fork point" branch for a worktree when the cell's
/// `worktree_base_branch` field is empty. Prefers the upstream of the
/// current branch; falls back to `main` / `master` by existence check.
async fn resolve_fork_point(worktree_path: &PathBuf) -> Option<String> {
    // 1. Upstream of the worktree's branch, e.g. origin/main.
    if let Ok(out) = tokio::process::Command::new("git")
        .args([
            "-C",
            &worktree_path.to_string_lossy(),
            "rev-parse",
            "--abbrev-ref",
            "@{upstream}",
        ])
        .output()
        .await
    {
        if out.status.success() {
            let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !name.is_empty() {
                return Some(name);
            }
        }
    }
    // 2. Common defaults — pick whichever branch actually exists.
    for candidate in ["main", "master"] {
        if let Ok(out) = tokio::process::Command::new("git")
            .args([
                "-C",
                &worktree_path.to_string_lossy(),
                "rev-parse",
                "--verify",
                candidate,
            ])
            .output()
            .await
        {
            if out.status.success() {
                return Some(candidate.to_string());
            }
        }
    }
    None
}

pub async fn diff(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (path, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            PathBuf::from(&cell.worktree_path),
            cell.worktree_base_branch.clone(),
        )
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
    let (path, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            PathBuf::from(&cell.worktree_path),
            if cell.worktree_base_branch.is_empty() {
                "main".to_string()
            } else {
                cell.worktree_base_branch.clone()
            },
        )
    };
    rollback_to(&path, &sha).await.map_err(worktree_err)?;

    // Mirror Python's post-rollback bookkeeping in `loom/server.py`
    // (`worktree_rollback` handler): refresh checkpoint count, clear
    // cached diff, persist, and broadcast so the UI's checkpoint badge
    // and dirty indicator match reality.
    let new_count = count_commits(&path, &base).await.unwrap_or(0);
    let agent = {
        let mut st = ctx.state.lock().await;
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            cell.worktree_checkpoints = new_count;
            cell.worktree_dirty = false;
            cell.worktree_diff = serde_json::Map::new();
            cell.worktree_changed_files.clear();
        }
        st.emit_agent(&agent_id);
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&agent).await?;
    flush(ctx).await;
    ok()
}

/// Full structured diff payload for the review view. Shells out to `git
/// diff --binary --find-renames --unified=3 {base}...HEAD`, parses the
/// unified patch into `[{old_path,new_path,status,hunks:[{header,lines:[...]}]}]`
/// so the frontend renders it side-by-side. Mirrors Python's
/// `_worktree_merge_diff_snapshot` + `_parse_unified_diff`.
pub async fn diff_full(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (name, path, branch, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        if cell.worktree_path.is_empty() {
            return Ok(json!({
                "type": "worktree_diff_full",
                "id": agent_id,
                "error": "Agent has no worktree.",
            }));
        }
        (
            cell.name.clone(),
            cell.worktree_path.clone(),
            cell.worktree_branch.clone(),
            if cell.worktree_base_branch.is_empty() {
                "main".to_string()
            } else {
                cell.worktree_base_branch.clone()
            },
        )
    };

    let diff_range = format!("{base}...HEAD");
    let output = tokio::process::Command::new("git")
        .args([
            "-C",
            &path,
            "diff",
            "--no-color",
            "--find-renames",
            "--binary",
            "--unified=3",
            &diff_range,
        ])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git diff spawn: {e}")))?;

    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Ok(json!({
            "type": "worktree_diff_full",
            "id": agent_id,
            "error": if err.is_empty() {
                "Failed to load worktree diff.".to_string()
            } else { err },
        }));
    }

    let patch_text = String::from_utf8_lossy(&output.stdout).to_string();
    let files = parse_unified_diff(&patch_text);
    let insertions = files
        .iter()
        .filter_map(|f| f.get("insertions").and_then(|v| v.as_u64()))
        .sum::<u64>();
    let deletions = files
        .iter()
        .filter_map(|f| f.get("deletions").and_then(|v| v.as_u64()))
        .sum::<u64>();
    let stats = json!({
        "files": files.len(),
        "insertions": insertions,
        "deletions": deletions,
    });

    Ok(json!({
        "type": "worktree_diff_full",
        "id": agent_id,
        "agent_name": name,
        "branch": branch,
        "base_branch": base,
        "stats": stats,
        "files": files,
    }))
}

/// Check whether merging `branch` back into `base` would conflict, using
/// `git merge-tree --write-tree --messages`. Returns `{clean: bool,
/// conflicts: [file_paths]}`.
pub async fn check_conflicts(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (repo_root, branch, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            if !cell.worktree_repo_root.is_empty() {
                cell.worktree_repo_root.clone()
            } else {
                cell.git_root.clone()
            },
            cell.worktree_branch.clone(),
            if cell.worktree_base_branch.is_empty() {
                "main".to_string()
            } else {
                cell.worktree_base_branch.clone()
            },
        )
    };
    if repo_root.is_empty() || branch.is_empty() {
        return Ok(json!({ "clean": true, "conflicts": [] }));
    }
    let output = tokio::process::Command::new("git")
        .args(["-C", &repo_root, "merge-tree", "--write-tree", "--name-only", &base, &branch])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git merge-tree: {e}")))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut lines: Vec<String> = stdout
        .lines()
        .skip(1)
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect();
    lines.sort();
    lines.dedup();
    Ok(json!({
        "clean": lines.is_empty(),
        "conflicts": lines,
    }))
}

/// Merge the agent's worktree branch back into its base. Supports squash +
/// non-squash. Optionally removes the worktree + branch afterward.
///
/// Invariants (mirroring Python `loom/server.py::handle_command["worktree_merge"]`
/// + `loom/worktree.py::WorktreeManager.server_merge`):
///   1. If the group opts into `worktree_auto_checkpoint`, commit pending
///      dirty files BEFORE touching git state, synchronously — not via a
///      detached task. Otherwise a racing cleanup can delete the worktree
///      before the checkpoint runs, silently losing work.
///   2. Refuse to merge a dirty worktree (after the optional checkpoint
///      above) or a merge that would produce conflicts.
///   3. Refuse to treat "Already up to date." as success. If the base
///      branch HEAD does not advance across the `git merge` call, no
///      commit landed and cleanup MUST NOT run.
///   4. Cleanup (worktree remove + branch delete) and the cell-field
///      clear are gated on the HEAD-advanced check; a failed or no-op
///      merge leaves the branch + worktree intact for operator recovery.
pub async fn merge(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let squash = req.get("squash").and_then(|v| v.as_bool()).unwrap_or(false);
    let message_override = optional_str(req, "message").unwrap_or("").to_string();
    let cleanup = req
        .get("cleanup")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

    let (
        repo_root,
        worktree_path,
        branch,
        base,
        auto_checkpoint,
        cp_count,
        last_summary,
        agent_name,
    ) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            if !cell.worktree_repo_root.is_empty() {
                cell.worktree_repo_root.clone()
            } else {
                cell.git_root.clone()
            },
            cell.worktree_path.clone(),
            cell.worktree_branch.clone(),
            if cell.worktree_base_branch.is_empty() {
                "main".to_string()
            } else {
                cell.worktree_base_branch.clone()
            },
            cell.worktree_auto_checkpoint,
            cell.worktree_checkpoints,
            cell.last_summary.clone(),
            cell.name.clone(),
        )
    };
    if repo_root.is_empty() || branch.is_empty() {
        return Ok(json!({
            "type": "worktree_merge",
            "id": agent_id,
            "ok": false,
            "error": "agent has no worktree branch",
        }));
    }

    // ── Fix D (synchronous pre-merge auto-checkpoint) ────────────────
    // Commit pending dirty files BEFORE merging so the merge has
    // something to merge and so a later cleanup cannot delete the
    // worktree out from under uncommitted work. Python runs this
    // synchronously via `EventBus.on_session_end → _on_agent_session_end`
    // in `loom/server.py`; the Rust port previously spawned it
    // asynchronously (see `events.rs::spawn_auto_checkpoint`) which
    // raced with this function's cleanup and silently lost work.
    if auto_checkpoint
        && !worktree_path.is_empty()
        && has_uncommitted_changes(&worktree_path).await
    {
        let message = auto_checkpoint_message(&agent_name, cp_count, &last_summary);
        match do_checkpoint_for_agent(ctx, &agent_id, &message).await {
            Ok(sha) => {
                if sha.is_some() {
                    tracing::info!(
                        agent = %agent_id,
                        "pre-merge auto-checkpoint committed"
                    );
                }
            }
            Err(err) => {
                tracing::warn!(
                    agent = %agent_id,
                    ?err,
                    "pre-merge auto-checkpoint failed; the next guard will block the merge"
                );
            }
        }
    }

    // ── Fix A (pre-merge validation) ─────────────────────────────────
    // Refuse to merge a dirty worktree or a branch that would produce
    // conflicts. Mirrors `_run_worktree_merge_check` in Python
    // (`loom/mcp_weaver.py`). We do this inside `merge()` rather than
    // in the MCP layer so every caller (MCP tool, WS cmd, HTTP cmd)
    // gets the same guarantee.
    let check = check_merge(ctx, req).await?;
    let check_clean = check
        .get("clean")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !check_clean {
        let dirty = check.get("dirty").and_then(Value::as_bool).unwrap_or(false);
        let has_conflicts = check
            .get("conflicts")
            .and_then(Value::as_array)
            .map(|c| !c.is_empty())
            .unwrap_or(false);
        let reported_error = check
            .get("error")
            .and_then(Value::as_str)
            .map(|s| s.to_string());
        let error = reported_error.unwrap_or_else(|| {
            if dirty {
                "worktree has uncommitted changes".into()
            } else if has_conflicts {
                "merge would produce conflicts".into()
            } else {
                "pre-merge check failed".into()
            }
        });
        return Ok(json!({
            "type": "worktree_merge",
            "id": agent_id,
            "ok": false,
            "error": error,
            "check": check,
        }));
    }

    let commit_message = if !message_override.is_empty() {
        message_override.clone()
    } else if squash {
        format!("Squash merge: {branch}")
    } else {
        format!("Merge branch '{branch}'")
    };

    // Ensure we're on base in the main worktree.
    let out = tokio::process::Command::new("git")
        .args(["-C", &repo_root, "checkout", &base])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git checkout: {e}")))?;
    if !out.status.success() {
        return Ok(json!({
            "type": "worktree_merge",
            "id": agent_id,
            "ok": false,
            "error": format!(
                "git checkout {base}: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ),
        }));
    }

    // ── Fix B (capture pre-merge HEAD) ────────────────────────────────
    // We compare this to the post-merge HEAD below to detect the
    // "Already up to date." path that git silently returns with exit 0.
    let base_head_before = git_head_sha(&repo_root).await;

    // Run the merge.
    let merge_args: Vec<&str> = if squash {
        vec!["-C", &repo_root, "merge", "--squash", &branch]
    } else {
        vec![
            "-C", &repo_root, "merge", "--no-ff", "-m", &commit_message, &branch,
        ]
    };
    let out = tokio::process::Command::new("git")
        .args(&merge_args)
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git merge: {e}")))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        let _ = tokio::process::Command::new("git")
            .args(["-C", &repo_root, "merge", "--abort"])
            .output()
            .await;
        return Ok(json!({
            "type": "worktree_merge",
            "id": agent_id,
            "ok": false,
            "error": stderr.trim(),
        }));
    }

    // For --squash we still need an explicit commit.
    if squash {
        let out = tokio::process::Command::new("git")
            .args(["-C", &repo_root, "commit", "-m", &commit_message])
            .output()
            .await
            .map_err(|e| CmdError::BadRequest(format!("git commit: {e}")))?;
        if !out.status.success() {
            return Ok(json!({
                "type": "worktree_merge",
                "id": agent_id,
                "ok": false,
                "error": String::from_utf8_lossy(&out.stderr).trim(),
            }));
        }
    }

    // ── Fix B/C (no-op detection + gated cleanup) ───────────────────
    // If the base branch HEAD did not advance, git reported "Already
    // up to date." (or `--no-ff` refused to create a degenerate
    // merge). No commit landed. DO NOT run cleanup — that would
    // delete the branch + worktree while pretending the merge
    // succeeded, which is exactly the silent data-loss bug this
    // function previously had.
    let base_head_after = git_head_sha(&repo_root).await;
    let merge_landed = match (&base_head_before, &base_head_after) {
        (Some(before), Some(after)) => before != after && !after.is_empty(),
        _ => false,
    };
    if !merge_landed {
        return Ok(json!({
            "type": "worktree_merge",
            "id": agent_id,
            "ok": false,
            "error": format!(
                "branch '{}' produced no new commits on '{}' (already up to date or degenerate merge); worktree preserved",
                branch, base
            ),
            "base_head": base_head_after.clone().unwrap_or_default(),
        }));
    }

    let merge_sha = base_head_after.clone().unwrap_or_default();

    // Cleanup now gated on `merge_landed == true`.
    if cleanup && !worktree_path.is_empty() {
        let remove = tokio::process::Command::new("git")
            .args(["-C", &repo_root, "worktree", "remove", "--force", &worktree_path])
            .output()
            .await;
        if let Ok(out) = remove {
            if !out.status.success() {
                tracing::warn!(
                    agent = %agent_id,
                    path = %worktree_path,
                    stderr = %String::from_utf8_lossy(&out.stderr).trim(),
                    "worktree remove after merge failed"
                );
            }
        }
        let del = tokio::process::Command::new("git")
            .args(["-C", &repo_root, "branch", "-D", &branch])
            .output()
            .await;
        if let Ok(out) = del {
            if !out.status.success() {
                tracing::warn!(
                    agent = %agent_id,
                    branch = %branch,
                    stderr = %String::from_utf8_lossy(&out.stderr).trim(),
                    "branch delete after merge failed"
                );
            }
        }
    }

    // Clear worktree fields on the cell AND mark any boundary tasks merged.
    let (agent, boundary_tasks) = {
        let mut st = ctx.state.lock().await;
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            if cleanup {
                cell.worktree_path.clear();
                cell.worktree_branch.clear();
                cell.worktree_merged = true;
                cell.worktree_dirty = false;
                cell.worktree_changed_files.clear();
            } else {
                cell.worktree_merged = true;
            }
        }
        st.emit_agent(&agent_id);
        let boundary_tasks = loom_weaver::streams::mark_branch_boundaries_merged(
            &mut st, &repo_root, &branch, &merge_sha, None,
        );
        (
            st.agents.get(&agent_id).cloned().unwrap(),
            boundary_tasks,
        )
    };
    ctx.db.save_agent(&agent).await?;
    for task in &boundary_tasks {
        ctx.db.save_board_task(task).await?;
    }
    flush(ctx).await;
    Ok(json!({
        "type": "worktree_merge",
        "id": agent_id,
        "ok": true,
        "branch": branch,
        "base": base,
        "squash": squash,
        "merge_sha": merge_sha,
        "boundary_tasks_merged": boundary_tasks.iter().map(|t| t.id.clone()).collect::<Vec<_>>(),
    }))
}

/// Return HEAD sha for `repo`, or None if git rev-parse fails.
async fn git_head_sha(repo: &str) -> Option<String> {
    let out = tokio::process::Command::new("git")
        .args(["-C", repo, "rev-parse", "HEAD"])
        .output()
        .await
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let sha = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if sha.is_empty() {
        None
    } else {
        Some(sha)
    }
}

/// Rebase the agent's branch onto its base. Runs inside the worktree.
///
/// Invariants (mirroring Python `loom/server.py` `worktree_rebase` handler):
///   1. Refuse to rebase if a `git rebase` would overwrite untracked files
///      in the worktree. Python's `rebase_untracked_overwrite_paths` guard
///      — the unchecked Rust path could silently clobber untracked work.
///   2. Refuse to rebase if the worktree has uncommitted changes. A
///      rebase onto dirty state produces undefined behavior; Python
///      blocks with an explicit message asking the operator to
///      checkpoint or commit first.
///   3. After a successful rebase, refresh the cell's
///      `worktree_checkpoints` (counts change after squash/drop),
///      `worktree_dirty`, `worktree_diff`, and broadcast the delta.
pub async fn rebase(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (path, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            cell.worktree_path.clone(),
            if cell.worktree_base_branch.is_empty() {
                "main".to_string()
            } else {
                cell.worktree_base_branch.clone()
            },
        )
    };
    if path.is_empty() {
        return Ok(json!({
            "type": "worktree_rebase",
            "id": agent_id,
            "ok": false,
            "error": "agent has no worktree",
        }));
    }

    // ── Guard 1: untracked overwrite ─────────────────────────────────
    let overwrite = rebase_untracked_overwrite_paths(&path, &base).await;
    if !overwrite.is_empty() {
        return Ok(json!({
            "type": "worktree_rebase",
            "id": agent_id,
            "ok": false,
            "error": format!(
                "Rebase would overwrite untracked files: {}. Commit, stash, or delete them first.",
                overwrite.join(", ")
            ),
            "overwrite_paths": overwrite,
            "conflicts": [],
        }));
    }

    // ── Guard 2: dirty worktree ──────────────────────────────────────
    if has_uncommitted_changes(&path).await {
        return Ok(json!({
            "type": "worktree_rebase",
            "id": agent_id,
            "ok": false,
            "error": "Worktree has uncommitted changes. Create a checkpoint or commit them before rebasing.",
            "conflicts": [],
        }));
    }

    let previous_head = git_head_sha(&path).await.unwrap_or_default();

    let out = tokio::process::Command::new("git")
        .args(["-C", &path, "rebase", &base])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git rebase: {e}")))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        let _ = tokio::process::Command::new("git")
            .args(["-C", &path, "rebase", "--abort"])
            .output()
            .await;
        return Ok(json!({
            "type": "worktree_rebase",
            "id": agent_id,
            "ok": false,
            "error": stderr.trim(),
            "conflicts": [],
        }));
    }

    // Successful rebase — refresh derived cell state so the UI's
    // checkpoint badge + dirty indicator match reality.
    let rebased_head = git_head_sha(&path).await.unwrap_or_default();
    let dirty_after = has_uncommitted_changes(&path).await;
    let new_count = count_commits(&PathBuf::from(&path), &base)
        .await
        .unwrap_or(0);

    let (agent, refreshed_boundary) = {
        let mut st = ctx.state.lock().await;
        let (boundary_repo, boundary_branch) = st
            .agents
            .get(&agent_id)
            .map(|cell| {
                let repo = if !cell.worktree_repo_root.is_empty() {
                    cell.worktree_repo_root.clone()
                } else {
                    cell.git_root.clone()
                };
                (repo, cell.worktree_branch.clone())
            })
            .unwrap_or_default();
        if let Some(cell) = st.agents.get_mut(&agent_id) {
            cell.worktree_checkpoints = new_count;
            cell.worktree_dirty = dirty_after;
            cell.worktree_diff = serde_json::Map::new();
            cell.worktree_changed_files.clear();
        }
        st.emit_agent(&agent_id);
        // After a *clean* rebase, re-anchor the latest open branch
        // boundary so the stream doesn't report "advanced beyond
        // reviewed boundary" on every rebase. Dirty rebases leave the
        // boundary alone — the operator hasn't committed yet.
        let refreshed = if !dirty_after {
            loom_weaver::streams::refresh_latest_boundary_after_rebase(
                &mut st,
                &boundary_repo,
                &boundary_branch,
                &previous_head,
                &rebased_head,
            )
        } else {
            None
        };
        (st.agents.get(&agent_id).cloned().unwrap(), refreshed)
    };
    ctx.db.save_agent(&agent).await?;
    if let Some(task) = refreshed_boundary {
        ctx.db.save_board_task(&task).await?;
    }
    flush(ctx).await;

    Ok(json!({
        "type": "worktree_rebase",
        "id": agent_id,
        "ok": true,
        "base": base,
        "previous_head": previous_head,
        "rebased_head": rebased_head,
    }))
}

/// Return worktree untracked files that would be overwritten by a rebase
/// onto `base`. Mirrors Python `WorktreeManager.rebase_untracked_overwrite_paths`
/// (`loom/worktree.py:738`) — intersect `git diff --name-only HEAD <base>`
/// with the current untracked-files list from `git ls-files --others
/// --exclude-standard`.
async fn rebase_untracked_overwrite_paths(worktree_path: &str, base: &str) -> Vec<String> {
    let diff_out = tokio::process::Command::new("git")
        .args(["-C", worktree_path, "diff", "--name-only", "-z", "HEAD", base])
        .output()
        .await;
    let target_paths: std::collections::HashSet<String> = match diff_out {
        Ok(out) if out.status.success() => String::from_utf8_lossy(&out.stdout)
            .split('\0')
            .filter(|p| !p.is_empty())
            .map(|s| s.to_string())
            .collect(),
        _ => return Vec::new(),
    };
    if target_paths.is_empty() {
        return Vec::new();
    }
    let untracked_out = tokio::process::Command::new("git")
        .args([
            "-C",
            worktree_path,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ])
        .output()
        .await;
    let untracked: Vec<String> = match untracked_out {
        Ok(out) if out.status.success() => String::from_utf8_lossy(&out.stdout)
            .split('\0')
            .filter(|p| !p.is_empty())
            .map(|s| s.to_string())
            .collect(),
        _ => return Vec::new(),
    };
    untracked
        .into_iter()
        .filter(|u| target_paths.contains(u))
        .collect()
}

/// Open a pull request for this agent's branch via the `gh` CLI. Requires
/// `gh auth login` to have been run. Returns `{url, number}` on success.
///
/// Invariants (mirroring Python `loom/worktree.py::create_pr`):
///   1. Fail early with a clear message if `gh` CLI is not installed
///      (Python line 2045-2056). Don't let `gh pr create` fail later
///      with an opaque error.
///   2. Fail early if this isn't a GitHub repo (Python line 2058-2069).
///   3. Fail early if the branch has no commits ahead of base (Python
///      line 2071-2075) — otherwise `gh pr create` reports a cryptic
///      "no commits between" GitHub API error.
///   4. If `gh pr create` fails with "already exists", look up the
///      existing PR URL (Python line 2102-2113) so the operator gets
///      something actionable back instead of a noisy failure.
pub async fn create_pr(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let title = optional_str(req, "title").unwrap_or("").to_string();
    let body = optional_str(req, "body").unwrap_or("").to_string();
    let draft = req.get("draft").and_then(|v| v.as_bool()).unwrap_or(false);

    let (repo_root, worktree_path, branch, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            cell.worktree_repo_root.clone(),
            cell.worktree_path.clone(),
            cell.worktree_branch.clone(),
            cell.worktree_base_branch.clone(),
        )
    };
    let working_dir = if !worktree_path.is_empty() {
        worktree_path.clone()
    } else if !repo_root.is_empty() {
        repo_root
    } else {
        return Err(CmdError::BadRequest("agent has no git path".into()));
    };
    let resolved_base = if base.is_empty() {
        "main".to_string()
    } else {
        base.clone()
    };

    // ── Guard 1: `gh` CLI available ──────────────────────────────────
    let gh_version = tokio::process::Command::new("gh")
        .arg("--version")
        .output()
        .await;
    let gh_ok = matches!(gh_version, Ok(ref out) if out.status.success());
    if !gh_ok {
        return Ok(json!({
            "ok": false,
            "error": "GitHub CLI (gh) is not installed.",
        }));
    }

    // ── Guard 2: this is a GitHub-hosted repo ────────────────────────
    let repo_view = tokio::process::Command::new("gh")
        .args(["-C", &working_dir, "repo", "view", "--json", "name"])
        .output()
        .await;
    match repo_view {
        Ok(out) if out.status.success() => {}
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr).to_string();
            let msg = if stderr.to_lowercase().contains("not a git repository") {
                "Not a git repository.".to_string()
            } else {
                format!("Not a GitHub repository: {}", stderr.trim())
            };
            return Ok(json!({
                "ok": false,
                "error": msg,
            }));
        }
        Err(e) => {
            return Ok(json!({
                "ok": false,
                "error": format!("gh repo view: {e}"),
            }));
        }
    }

    // ── Guard 3: branch has commits ahead of base ────────────────────
    let ahead = count_commits(&PathBuf::from(&working_dir), &resolved_base)
        .await
        .unwrap_or(0);
    if ahead == 0 {
        return Ok(json!({
            "ok": false,
            "error": format!(
                "Branch {} has no commits ahead of {}.",
                branch, resolved_base
            ),
        }));
    }

    // Push the branch — `gh pr create` requires an upstream.
    let push = tokio::process::Command::new("git")
        .args(["-C", &working_dir, "push", "-u", "origin", &branch])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git push: {e}")))?;
    if !push.status.success() {
        return Ok(json!({
            "ok": false,
            "error": format!(
                "Failed to push branch: {}",
                String::from_utf8_lossy(&push.stderr).trim()
            ),
        }));
    }

    let resolved_title = if !title.is_empty() {
        title
    } else {
        branch.clone()
    };
    let mut args = vec![
        "-C",
        &working_dir,
        "pr",
        "create",
        "--title",
        &resolved_title,
        "--body",
        &body,
        "--base",
        &resolved_base,
        "--head",
        &branch,
    ];
    if draft {
        args.push("--draft");
    }
    let out = tokio::process::Command::new("gh")
        .args(&args)
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("gh pr create spawn: {e}")))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        // If a PR already exists for this branch, return its URL rather
        // than bubbling the `gh` failure. Matches Python's recovery
        // path so the operator still gets a usable link.
        if stderr.to_lowercase().contains("already exists") {
            let view = tokio::process::Command::new("gh")
                .args([
                    "-C",
                    &working_dir,
                    "pr",
                    "view",
                    &branch,
                    "--json",
                    "url",
                    "-q",
                    ".url",
                ])
                .output()
                .await;
            if let Ok(view_out) = view {
                if view_out.status.success() {
                    let existing_url =
                        String::from_utf8_lossy(&view_out.stdout).trim().to_string();
                    if !existing_url.is_empty() {
                        return Ok(json!({
                            "ok": true,
                            "url": existing_url,
                            "branch": branch,
                            "existing": true,
                        }));
                    }
                }
            }
        }
        return Ok(json!({
            "ok": false,
            "error": format!("Failed to create PR: {}", stderr.trim()),
        }));
    }
    let url = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok(json!({
        "ok": true,
        "url": url,
        "branch": branch,
    }))
}

/// Minimal unified-diff parser. Ports loom/server_worktrees.py
/// `_parse_unified_diff`. Produces a JSON array of:
///   `{ path, old_path, new_path, status, hunks: [{ header, lines: [...] }] }`
fn parse_unified_diff(patch: &str) -> Vec<Value> {
    let mut files: Vec<serde_json::Map<String, Value>> = Vec::new();
    let mut current: Option<serde_json::Map<String, Value>> = None;
    let mut current_hunk: Option<serde_json::Map<String, Value>> = None;
    let mut insertions = 0u64;
    let mut deletions = 0u64;

    fn finish_file(
        files: &mut Vec<serde_json::Map<String, Value>>,
        current: &mut Option<serde_json::Map<String, Value>>,
        current_hunk: &mut Option<serde_json::Map<String, Value>>,
        insertions: &mut u64,
        deletions: &mut u64,
    ) {
        if let Some(mut file) = current.take() {
            if let Some(hunk) = current_hunk.take() {
                file.entry("hunks")
                    .or_insert_with(|| Value::Array(Vec::new()));
                if let Some(arr) = file.get_mut("hunks").and_then(|v| v.as_array_mut()) {
                    arr.push(Value::Object(hunk));
                }
            }
            file.insert("insertions".into(), json!(*insertions));
            file.insert("deletions".into(), json!(*deletions));
            files.push(file);
            *insertions = 0;
            *deletions = 0;
        }
    }

    for raw in patch.lines() {
        if raw.starts_with("diff --git ") {
            finish_file(
                &mut files,
                &mut current,
                &mut current_hunk,
                &mut insertions,
                &mut deletions,
            );
            let parts: Vec<&str> = raw.split(' ').collect();
            let a = parts.get(2).map(|s| s.trim_start_matches("a/")).unwrap_or("");
            let b = parts.get(3).map(|s| s.trim_start_matches("b/")).unwrap_or("");
            let mut file = serde_json::Map::new();
            file.insert("path".into(), Value::String(b.to_string()));
            file.insert("old_path".into(), Value::String(a.to_string()));
            file.insert("new_path".into(), Value::String(b.to_string()));
            file.insert("status".into(), Value::String("modified".into()));
            file.insert("hunks".into(), Value::Array(Vec::new()));
            current = Some(file);
            continue;
        }
        let Some(file) = current.as_mut() else {
            continue;
        };
        if raw.starts_with("new file mode") {
            file.insert("status".into(), Value::String("added".into()));
        } else if raw.starts_with("deleted file mode") {
            file.insert("status".into(), Value::String("deleted".into()));
        } else if let Some(rest) = raw.strip_prefix("rename from ") {
            file.insert("old_path".into(), Value::String(rest.to_string()));
            file.insert("status".into(), Value::String("renamed".into()));
        } else if let Some(rest) = raw.strip_prefix("rename to ") {
            file.insert("new_path".into(), Value::String(rest.to_string()));
            file.insert("path".into(), Value::String(rest.to_string()));
            file.insert("status".into(), Value::String("renamed".into()));
        } else if let Some(rest) = raw.strip_prefix("copy from ") {
            file.insert("old_path".into(), Value::String(rest.to_string()));
            file.insert("status".into(), Value::String("copied".into()));
        } else if let Some(rest) = raw.strip_prefix("copy to ") {
            file.insert("new_path".into(), Value::String(rest.to_string()));
            file.insert("path".into(), Value::String(rest.to_string()));
            file.insert("status".into(), Value::String("copied".into()));
        } else if raw.starts_with("Binary files ") || raw == "GIT binary patch" {
            file.insert("binary".into(), Value::Bool(true));
            current_hunk = None;
        } else if raw.starts_with("@@ ") {
            if let Some(hunk) = current_hunk.take() {
                if let Some(arr) = file.get_mut("hunks").and_then(|v| v.as_array_mut()) {
                    arr.push(Value::Object(hunk));
                }
            }
            let mut hunk = serde_json::Map::new();
            hunk.insert("header".into(), Value::String(raw.to_string()));
            hunk.insert("lines".into(), Value::Array(Vec::new()));
            current_hunk = Some(hunk);
        } else if let Some(hunk) = current_hunk.as_mut() {
            if raw.starts_with("+") && !raw.starts_with("+++ ") {
                if let Some(arr) = hunk.get_mut("lines").and_then(|v| v.as_array_mut()) {
                    arr.push(json!({"type": "add", "text": &raw[1..]}));
                }
                insertions += 1;
            } else if raw.starts_with("-") && !raw.starts_with("--- ") {
                if let Some(arr) = hunk.get_mut("lines").and_then(|v| v.as_array_mut()) {
                    arr.push(json!({"type": "del", "text": &raw[1..]}));
                }
                deletions += 1;
            } else if raw.starts_with(' ') {
                if let Some(arr) = hunk.get_mut("lines").and_then(|v| v.as_array_mut()) {
                    arr.push(json!({"type": "context", "text": &raw[1..]}));
                }
            } else if raw.starts_with("\\ No newline at end of file") {
                if let Some(arr) = hunk.get_mut("lines").and_then(|v| v.as_array_mut()) {
                    arr.push(json!({"type": "context", "text": raw}));
                }
            }
        }
    }
    finish_file(
        &mut files,
        &mut current,
        &mut current_hunk,
        &mut insertions,
        &mut deletions,
    );

    files.into_iter().map(Value::Object).collect()
}

pub async fn check_merge(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let (worktree_path, repo_root, branch, base, agent_name, squash) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            cell.worktree_path.clone(),
            if !cell.worktree_repo_root.is_empty() {
                cell.worktree_repo_root.clone()
            } else {
                cell.git_root.clone()
            },
            cell.worktree_branch.clone(),
            if cell.worktree_base_branch.is_empty() {
                "main".to_string()
            } else {
                cell.worktree_base_branch.clone()
            },
            cell.name.clone(),
            cell.worktree_merge_squash,
        )
    };
    if worktree_path.is_empty() || branch.is_empty() {
        return Ok(json!({
            "type": "worktree_check_merge",
            "id": agent_id,
            "clean": false,
            "dirty": false,
            "conflicts": [],
            "error": "No worktree",
        }));
    }

    // 1. Dirty check — any uncommitted changes in the worktree block the merge.
    let dirty = has_uncommitted_changes(&worktree_path).await;
    if dirty {
        return Ok(json!({
            "type": "worktree_check_merge",
            "id": agent_id,
            "clean": false,
            "dirty": true,
            "conflicts": [],
        }));
    }

    // 2. Run merge-tree --write-tree on the shared repo root. Success => clean
    //    merge, tree_sha on stdout. Failure => parse conflicts.
    let effective_repo = if !repo_root.is_empty() {
        repo_root
    } else {
        worktree_path.clone()
    };
    let out = tokio::process::Command::new("git")
        .args(["-C", &effective_repo, "merge-tree", "--write-tree", &base, &branch])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git merge-tree: {e}")))?;

    if out.status.success() {
        let tree_sha = String::from_utf8_lossy(&out.stdout)
            .lines()
            .next()
            .unwrap_or("")
            .trim()
            .to_string();
        let default_message = if squash {
            format!("Squash merge: {}", if branch.is_empty() { agent_name } else { branch.clone() })
        } else {
            format!("Merge branch '{}'", if branch.is_empty() { agent_name } else { branch.clone() })
        };
        return Ok(json!({
            "type": "worktree_check_merge",
            "id": agent_id,
            "clean": true,
            "dirty": false,
            "conflicts": [],
            "tree_sha": tree_sha,
            "default_message": default_message,
        }));
    }

    // Conflicts — parse merge-tree conflict output. Each conflicted file
    // shows up in the `Conflict` section. Minimal port: extract any path
    // after "CONFLICT" lines, deduped.
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    let mut conflicts: Vec<Value> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for line in stdout.lines().chain(stderr.lines()) {
        let line = line.trim();
        // merge-tree conflict reports look like:
        //   "<sha> <stage>\t<path>"  (with stage 1/2/3 for conflicts)
        // or a message line starting with "CONFLICT (...)".
        if let Some(rest) = line.strip_prefix("CONFLICT") {
            // CONFLICT (content): Merge conflict in <path>
            let reason = rest.trim_start_matches(|c: char| c == ' ' || c == '(' || c == ')' || c == ':');
            if let Some(path) = line.rsplit(" in ").next() {
                let path = path.trim();
                if !path.is_empty() && seen.insert(path.to_string()) {
                    conflicts.push(json!({ "path": path, "reason": reason }));
                }
            }
        } else if line.contains('\t') && line.split('\t').count() >= 2 {
            // Stage-style line; take the tab-separated path.
            let mut parts = line.splitn(2, '\t');
            let header = parts.next().unwrap_or("");
            let path = parts.next().unwrap_or("").trim();
            // Only include lines with a stage suffix (1/2/3) indicating a
            // conflict entry — plain info lines don't match.
            let looks_conflict = header.split_whitespace().count() == 3
                && matches!(header.split_whitespace().last(), Some("1" | "2" | "3"));
            if looks_conflict && !path.is_empty() && seen.insert(path.to_string()) {
                conflicts.push(json!({ "path": path, "reason": "merge conflict" }));
            }
        }
    }

    Ok(json!({
        "type": "worktree_check_merge",
        "id": agent_id,
        "clean": false,
        "dirty": false,
        "conflicts": conflicts,
    }))
}

/// True iff `git status --porcelain` shows any entries in the worktree.
async fn has_uncommitted_changes(worktree_path: &str) -> bool {
    let Ok(out) = tokio::process::Command::new("git")
        .args(["-C", worktree_path, "status", "--porcelain"])
        .output()
        .await
    else {
        return false;
    };
    !out.stdout.is_empty()
}

/// Retained for the ambient merge-state on cell load. Not called from the
/// WS path anymore (the frontend goes through `check_merge`).
#[allow(dead_code)]
async fn probe_merged(path: &PathBuf, branch: &str, base: &str) -> bool {
    is_merged(path, branch, base).await.unwrap_or(false)
}
