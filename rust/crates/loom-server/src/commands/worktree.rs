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

    let (repo_root, base_dir) = {
        let st = ctx.state.lock().await;
        let repo = agent_repo_root(&st, &agent_id)?;
        let base_dir = st.agents.get(&agent_id).and_then(|a| {
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
        (repo, base_dir)
    };

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

pub async fn remove(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();

    let (repo_root, path) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        if cell.worktree_path.is_empty() {
            return Err(CmdError::BadRequest("agent has no worktree".into()));
        }
        (
            PathBuf::from(&cell.worktree_repo_root),
            PathBuf::from(&cell.worktree_path),
        )
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
    let message = optional_str(req, "message")
        .unwrap_or("loom: checkpoint")
        .to_string();

    let path = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
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
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            PathBuf::from(&cell.worktree_path),
            cell.worktree_base_branch.clone(),
        )
    };
    let checkpoints = list_checkpoints(&path, &base).await.map_err(worktree_err)?;
    Ok(json!({ "checkpoints": checkpoints }))
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
    let path = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        PathBuf::from(&cell.worktree_path)
    };
    rollback_to(&path, &sha).await.map_err(worktree_err)?;
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
pub async fn merge(ctx: &CmdContext, req: &Value) -> CmdResult {
    let agent_id = required_str(req, "agent_id")?.to_string();
    let squash = req.get("squash").and_then(|v| v.as_bool()).unwrap_or(false);
    let message_override = optional_str(req, "message").unwrap_or("").to_string();
    let cleanup = req
        .get("cleanup")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

    let (repo_root, worktree_path, branch, base) = {
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
        )
    };
    if repo_root.is_empty() || branch.is_empty() {
        return Err(CmdError::BadRequest("agent has no worktree branch".into()));
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
        return Err(CmdError::BadRequest(format!(
            "git checkout {base}: {}",
            String::from_utf8_lossy(&out.stderr)
        )));
    }

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
                "ok": false,
                "error": String::from_utf8_lossy(&out.stderr).trim(),
            }));
        }
    }

    if cleanup && !worktree_path.is_empty() {
        let _ = tokio::process::Command::new("git")
            .args(["-C", &repo_root, "worktree", "remove", "--force", &worktree_path])
            .output()
            .await;
        let _ = tokio::process::Command::new("git")
            .args(["-C", &repo_root, "branch", "-D", &branch])
            .output()
            .await;
    }

    // Clear worktree fields on the cell.
    let agent = {
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
        st.agents.get(&agent_id).cloned().unwrap()
    };
    ctx.db.save_agent(&agent).await?;
    flush(ctx).await;
    Ok(json!({
        "ok": true,
        "branch": branch,
        "base": base,
        "squash": squash,
    }))
}

/// Rebase the agent's branch onto its base. Runs inside the worktree.
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
        return Err(CmdError::BadRequest("agent has no worktree".into()));
    }
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
            "ok": false,
            "error": stderr.trim(),
        }));
    }
    Ok(json!({ "ok": true, "base": base }))
}

/// Open a pull request for this agent's branch via the `gh` CLI. Requires
/// `gh auth login` to have been run. Returns `{url, number}` on success.
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
        worktree_path
    } else if !repo_root.is_empty() {
        repo_root
    } else {
        return Err(CmdError::BadRequest("agent has no git path".into()));
    };

    // Push the branch first — `gh pr create` requires an upstream.
    let push = tokio::process::Command::new("git")
        .args(["-C", &working_dir, "push", "-u", "origin", &branch])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("git push: {e}")))?;
    if !push.status.success() {
        return Ok(json!({
            "ok": false,
            "error": String::from_utf8_lossy(&push.stderr).trim(),
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
    ];
    if !base.is_empty() {
        args.push("--base");
        args.push(&base);
    }
    if draft {
        args.push("--draft");
    }
    let out = tokio::process::Command::new("gh")
        .args(&args)
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("gh pr create spawn: {e}")))?;
    if !out.status.success() {
        return Ok(json!({
            "ok": false,
            "error": String::from_utf8_lossy(&out.stderr).trim(),
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
    let (path, branch, base) = {
        let st = ctx.state.lock().await;
        let Some(cell) = st.agents.get(&agent_id) else {
            return Err(CmdError::BadRequest(format!(
                "agent '{agent_id}' not found"
            )));
        };
        (
            PathBuf::from(&cell.worktree_path),
            cell.worktree_branch.clone(),
            cell.worktree_base_branch.clone(),
        )
    };
    let merged = is_merged(&path, &branch, &base)
        .await
        .map_err(worktree_err)?;

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
