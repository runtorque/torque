//! Checkpoint ring buffer on a worktree.

use std::path::Path;

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckpointEntry {
    pub sha: String,
    pub message: String,
    pub timestamp: i64,
}

/// Create a checkpoint commit on the current branch of the worktree.
/// No-op if there are no changes.
pub async fn checkpoint(worktree_path: &Path, message: &str) -> Result<Option<String>> {
    let status = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args(["status", "--porcelain"])
        .output()
        .await?;
    if status.stdout.is_empty() {
        return Ok(None);
    }

    let add = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args(["add", "-A"])
        .output()
        .await
        .context("git add")?;
    if !add.status.success() {
        bail!("git add failed: {}", String::from_utf8_lossy(&add.stderr));
    }

    let commit = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args(["commit", "-m", message])
        .output()
        .await
        .context("git commit")?;
    if !commit.status.success() {
        bail!(
            "git commit failed: {}",
            String::from_utf8_lossy(&commit.stderr)
        );
    }

    let rev = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args(["rev-parse", "HEAD"])
        .output()
        .await?;
    let sha = String::from_utf8_lossy(&rev.stdout).trim().to_string();
    Ok(Some(sha))
}

pub async fn count_commits(worktree_path: &Path, base: &str) -> Result<i32> {
    let out = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args(["rev-list", "--count", &format!("{base}..HEAD")])
        .output()
        .await?;
    if !out.status.success() {
        return Ok(0);
    }
    let n: i32 = String::from_utf8_lossy(&out.stdout)
        .trim()
        .parse()
        .unwrap_or(0);
    Ok(n)
}

pub async fn list_checkpoints(worktree_path: &Path, base: &str) -> Result<Vec<CheckpointEntry>> {
    let out = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args([
            "log",
            &format!("{base}..HEAD"),
            "--pretty=%H\t%s\t%ct",
        ])
        .output()
        .await?;
    let mut checkpoints = Vec::new();
    for line in String::from_utf8_lossy(&out.stdout).lines() {
        let mut parts = line.splitn(3, '\t');
        let sha = parts.next().unwrap_or("").to_string();
        let message = parts.next().unwrap_or("").to_string();
        let ts = parts.next().unwrap_or("0").parse::<i64>().unwrap_or(0);
        if sha.is_empty() {
            continue;
        }
        checkpoints.push(CheckpointEntry { sha, message, timestamp: ts });
    }
    Ok(checkpoints)
}

pub async fn rollback_to(worktree_path: &Path, sha: &str) -> Result<()> {
    let out = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args(["reset", "--hard", sha])
        .output()
        .await?;
    if !out.status.success() {
        bail!("git reset failed: {}", String::from_utf8_lossy(&out.stderr));
    }
    Ok(())
}
