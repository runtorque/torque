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
    let base_tree_sha = String::from_utf8_lossy(&base_tree.stdout)
        .trim()
        .to_string();
    Ok(first_line == base_tree_sha)
}

/// Fallback for squash merges that `is_merged` can't detect because of
/// overlapping changes. Returns true when (a) `base` has advanced past
/// `pre_merge_sha` and (b) the commits it gained touch every file the branch
/// changed since the fork point. Mirrors `loom/worktree.py::check_base_advanced`.
pub async fn check_base_advanced(
    repo_root: &Path,
    branch: &str,
    base: &str,
    pre_merge_sha: &str,
) -> Result<bool> {
    if branch.is_empty() || base.is_empty() || pre_merge_sha.is_empty() {
        return Ok(false);
    }

    let current_sha = match git_stdout(repo_root, &["rev-parse", base]).await? {
        Some(sha) => sha,
        None => return Ok(false),
    };
    if current_sha == pre_merge_sha {
        return Ok(false);
    }

    let base_files = match git_stdout(
        repo_root,
        &["diff", "--name-only", pre_merge_sha, &current_sha],
    )
    .await?
    {
        Some(out) => lines_set(&out),
        None => return Ok(false),
    };

    let fork = match git_stdout(repo_root, &["merge-base", base, branch]).await? {
        Some(sha) => sha,
        None => return Ok(false),
    };

    let branch_files = match git_stdout(repo_root, &["diff", "--name-only", &fork, branch]).await? {
        Some(out) => lines_set(&out),
        None => return Ok(false),
    };

    if branch_files.is_empty() {
        return Ok(false);
    }

    Ok(branch_files.is_subset(&base_files))
}

async fn git_stdout(repo_root: &Path, args: &[&str]) -> Result<Option<String>> {
    let out = tokio::process::Command::new("git")
        .current_dir(repo_root)
        .args(args)
        .output()
        .await?;
    if !out.status.success() {
        return Ok(None);
    }
    Ok(Some(
        String::from_utf8_lossy(&out.stdout).trim().to_string(),
    ))
}

fn lines_set(text: &str) -> std::collections::HashSet<String> {
    text.lines()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}
