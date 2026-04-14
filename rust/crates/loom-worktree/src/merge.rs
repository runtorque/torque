//! Merge detection. Mirrors `loom/worktree.py::is_merged` + `check_base_advanced`.

use std::path::Path;

use anyhow::Result;

/// Returns true if `branch` has been merged into `base` via either:
/// 1. direct ancestry (`git merge-base --is-ancestor`), or
/// 2. `merge-tree --write-tree` producing an identical tree to `base`.
pub async fn is_merged(repo_path: &Path, branch: &str, base: &str) -> Result<bool> {
    // ancestry check
    let out = tokio::process::Command::new("git")
        .current_dir(repo_path)
        .args(["merge-base", "--is-ancestor", branch, base])
        .output()
        .await?;
    if out.status.success() {
        return Ok(true);
    }

    // merge-tree simulation
    let out = tokio::process::Command::new("git")
        .current_dir(repo_path)
        .args(["merge-tree", "--write-tree", "--messages", base, branch])
        .output()
        .await?;
    if !out.status.success() {
        return Ok(false);
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    // The first line is the merged tree SHA. If it equals base's tree, the
    // branch introduces no new changes.
    let first_line = stdout.lines().next().unwrap_or("").trim();
    if first_line.is_empty() {
        return Ok(false);
    }
    let base_tree = tokio::process::Command::new("git")
        .current_dir(repo_path)
        .args(["rev-parse", &format!("{}^{{tree}}", base)])
        .output()
        .await?;
    if !base_tree.status.success() {
        return Ok(false);
    }
    let base_tree_sha = String::from_utf8_lossy(&base_tree.stdout).trim().to_string();
    Ok(first_line == base_tree_sha)
}

/// Fallback for squash merges: check whether the base branch advanced and
/// the new commits touch every file the branch changed.
pub async fn check_base_advanced(
    _repo_path: &Path,
    _branch: &str,
    _base: &str,
) -> Result<bool> {
    // TODO: port full heuristic from `loom/worktree.py::check_base_advanced`.
    Ok(false)
}
