//! Checkpoint ring buffer on a worktree.

use std::path::Path;

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckpointEntry {
    pub sha: String,
    pub short_sha: String,
    pub message: String,
    #[serde(default)]
    pub body: String,
    /// ISO-8601 author date.
    pub date: String,
    pub timestamp: i64,
    #[serde(default)]
    pub insertions: u64,
    #[serde(default)]
    pub deletions: u64,
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

/// One git call: commits on branch vs base as `(ahead, behind)`. Ahead is the
/// number of commits the worktree has that `base` doesn't (== checkpoints);
/// behind is the number of commits `base` has gained since the fork point.
pub async fn ahead_behind(worktree_path: &Path, base: &str) -> Result<(i32, i32)> {
    let out = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args([
            "rev-list",
            "--left-right",
            "--count",
            &format!("{base}...HEAD"),
        ])
        .output()
        .await?;
    if !out.status.success() {
        return Ok((0, 0));
    }
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    // Format: "<behind>\t<ahead>"
    let mut parts = text.split_whitespace();
    let behind: i32 = parts.next().unwrap_or("0").parse().unwrap_or(0);
    let ahead: i32 = parts.next().unwrap_or("0").parse().unwrap_or(0);
    Ok((ahead, behind))
}

pub async fn list_checkpoints(worktree_path: &Path, base: &str) -> Result<Vec<CheckpointEntry>> {
    // Delimiter scheme mirrors Python's loom/worktree.py::list_checkpoints —
    // lets us cleanly recover multi-line commit bodies + per-commit numstat
    // output in a single `git log` invocation.
    const HDR: &str = "---LOOM_COMMIT---";
    const BODY: &str = "---LOOM_BODY---";
    const BODY_END: &str = "---LOOM_BODY_END---";
    let format = format!(
        "{HDR}%n%H%n%h%n%s%n%aI%n%ct%n{BODY}%n%b{BODY_END}",
    );
    let out = tokio::process::Command::new("git")
        .current_dir(worktree_path)
        .args([
            "log",
            &format!("--format={format}"),
            "--numstat",
            &format!("{base}..HEAD"),
        ])
        .output()
        .await?;
    if !out.status.success() {
        return Ok(Vec::new());
    }
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    let mut checkpoints = Vec::new();
    // The very first chunk before HDR is empty — skip it.
    for chunk in text.split(HDR).skip(1) {
        // Extract body between BODY…BODY_END and remove that section before
        // parsing the header lines + numstat rows.
        let (header_block, body) = match (chunk.find(BODY), chunk.find(BODY_END)) {
            (Some(start), Some(end)) if end > start => {
                let body_start = start + BODY.len();
                let body = chunk[body_start..end].trim().to_string();
                let stripped = format!(
                    "{}{}",
                    &chunk[..start],
                    &chunk[end + BODY_END.len()..]
                );
                (stripped, body)
            }
            _ => (chunk.to_string(), String::new()),
        };

        let mut lines = header_block.lines().filter(|l| !l.is_empty());
        let sha = lines.next().unwrap_or("").trim().to_string();
        let short_sha = lines.next().unwrap_or("").trim().to_string();
        let message = lines.next().unwrap_or("").trim().to_string();
        let date = lines.next().unwrap_or("").trim().to_string();
        let ts = lines
            .next()
            .unwrap_or("")
            .trim()
            .parse::<i64>()
            .unwrap_or(0);
        if sha.is_empty() {
            continue;
        }

        let mut insertions: u64 = 0;
        let mut deletions: u64 = 0;
        for stat_line in lines {
            let mut parts = stat_line.split('\t');
            let ins = parts.next().unwrap_or("");
            let dels = parts.next().unwrap_or("");
            // Binary files show `-` in place of counts — skip those.
            if ins != "-" {
                insertions += ins.parse::<u64>().unwrap_or(0);
            }
            if dels != "-" {
                deletions += dels.parse::<u64>().unwrap_or(0);
            }
        }

        checkpoints.push(CheckpointEntry {
            sha,
            short_sha,
            message,
            body,
            date,
            timestamp: ts,
            insertions,
            deletions,
        });
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
