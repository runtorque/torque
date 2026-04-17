//! Worktree manager — create, remove, list, prune.

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorktreeInfo {
    pub path: PathBuf,
    pub branch: String,
    pub base_branch: String,
    pub repo_root: PathBuf,
}

pub struct WorktreeManager {
    repo_root: PathBuf,
    base_dir: PathBuf,
}

impl WorktreeManager {
    pub fn new(repo_root: impl Into<PathBuf>, base_dir: Option<PathBuf>) -> Self {
        let repo_root = repo_root.into();
        let base_dir = base_dir.unwrap_or_else(|| repo_root.join(".loom").join("worktrees"));
        Self {
            repo_root,
            base_dir,
        }
    }

    pub fn repo_root(&self) -> &Path {
        &self.repo_root
    }

    pub fn base_dir(&self) -> &Path {
        &self.base_dir
    }

    /// Create a worktree using `git worktree add`. Branch is created if it
    /// doesn't already exist. If `base_branch` is empty, falls back to the
    /// main worktree's currently checked-out branch so downstream code
    /// (checkpoint listing, diff vs base) always has a valid ref.
    pub async fn create(&self, branch: &str, base_branch: &str) -> Result<WorktreeInfo> {
        std::fs::create_dir_all(&self.base_dir)?;
        let short = branch.rsplit_once('/').map(|(_, s)| s).unwrap_or(branch);
        let path = self.base_dir.join(short);
        if path.exists() {
            bail!("worktree path already exists: {}", path.display());
        }
        // Resolve the fork point before we spawn the worktree so we can
        // persist it regardless of whether the caller passed one. Mirrors
        // Python's fallback to `get_current_branch(repo_root)` when
        // `base_branch` is empty (see loom/worktree.py line 850).
        let resolved_base = if base_branch.is_empty() {
            current_branch(&self.repo_root).await.unwrap_or_default()
        } else {
            base_branch.to_string()
        };
        let mut cmd = tokio::process::Command::new("git");
        cmd.current_dir(&self.repo_root)
            .arg("worktree")
            .arg("add")
            .arg("-B")
            .arg(branch);
        cmd.arg(&path);
        if !resolved_base.is_empty() {
            cmd.arg(&resolved_base);
        }
        let out = cmd.output().await.context("git worktree add")?;
        if !out.status.success() {
            bail!(
                "git worktree add failed: {}",
                String::from_utf8_lossy(&out.stderr)
            );
        }
        Ok(WorktreeInfo {
            path,
            branch: branch.to_string(),
            base_branch: resolved_base,
            repo_root: self.repo_root.clone(),
        })
    }

    /// Remove a worktree via `git worktree remove`.
    pub async fn remove(&self, path: impl AsRef<Path>) -> Result<()> {
        let out = tokio::process::Command::new("git")
            .current_dir(&self.repo_root)
            .arg("worktree")
            .arg("remove")
            .arg("--force")
            .arg(path.as_ref())
            .output()
            .await
            .context("git worktree remove")?;
        if !out.status.success() {
            bail!(
                "git worktree remove failed: {}",
                String::from_utf8_lossy(&out.stderr)
            );
        }
        Ok(())
    }

    pub async fn prune(&self) -> Result<()> {
        let out = tokio::process::Command::new("git")
            .current_dir(&self.repo_root)
            .arg("worktree")
            .arg("prune")
            .output()
            .await
            .context("git worktree prune")?;
        if !out.status.success() {
            bail!(
                "git worktree prune failed: {}",
                String::from_utf8_lossy(&out.stderr)
            );
        }
        Ok(())
    }

    /// List all worktrees known to git.
    pub async fn list(&self) -> Result<Vec<WorktreeInfo>> {
        let out = tokio::process::Command::new("git")
            .current_dir(&self.repo_root)
            .arg("worktree")
            .arg("list")
            .arg("--porcelain")
            .output()
            .await
            .context("git worktree list")?;
        if !out.status.success() {
            bail!(
                "git worktree list failed: {}",
                String::from_utf8_lossy(&out.stderr)
            );
        }
        parse_worktree_list(&String::from_utf8_lossy(&out.stdout), &self.repo_root)
    }
}

/// Resolve the branch currently checked out at `repo_root`. Returns `None`
/// if the repo is in a detached-HEAD state (which would otherwise leak the
/// literal string "HEAD" as a branch name).
async fn current_branch(repo_root: &Path) -> Option<String> {
    let out = tokio::process::Command::new("git")
        .current_dir(repo_root)
        .args(["symbolic-ref", "--short", "HEAD"])
        .output()
        .await
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if name.is_empty() {
        None
    } else {
        Some(name)
    }
}

fn parse_worktree_list(porcelain: &str, repo_root: &Path) -> Result<Vec<WorktreeInfo>> {
    let mut out = Vec::new();
    let mut current_path: Option<PathBuf> = None;
    let mut current_branch = String::new();
    for line in porcelain.lines() {
        if let Some(rest) = line.strip_prefix("worktree ") {
            if let Some(path) = current_path.take() {
                out.push(WorktreeInfo {
                    path,
                    branch: std::mem::take(&mut current_branch),
                    base_branch: String::new(),
                    repo_root: repo_root.to_path_buf(),
                });
            }
            current_path = Some(PathBuf::from(rest));
        } else if let Some(rest) = line.strip_prefix("branch ") {
            current_branch = rest.strip_prefix("refs/heads/").unwrap_or(rest).to_string();
        }
    }
    if let Some(path) = current_path {
        out.push(WorktreeInfo {
            path,
            branch: current_branch,
            base_branch: String::new(),
            repo_root: repo_root.to_path_buf(),
        });
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_list_handles_multiple_entries() {
        let porcelain = "worktree /repo/main\nHEAD abc\nbranch refs/heads/main\n\nworktree /repo/feature\nHEAD def\nbranch refs/heads/feature\n";
        let wt = parse_worktree_list(porcelain, Path::new("/repo")).unwrap();
        assert_eq!(wt.len(), 2);
        assert_eq!(wt[0].branch, "main");
        assert_eq!(wt[1].branch, "feature");
    }
}
