//! Diff summary helpers. Uses `git2` for speed where it pays.

use std::path::Path;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DiffSummary {
    pub files: usize,
    pub insertions: usize,
    pub deletions: usize,
    pub changed_files: Vec<String>,
}

/// Diff the worktree against a base ref (e.g. `main`).
///
/// Uses `git2` so we don't have to parse porcelain output.
pub fn diff_vs_base(worktree_path: &Path, base: &str) -> Result<DiffSummary> {
    let repo = git2::Repository::open(worktree_path).context("open worktree")?;

    let base_commit = match repo.revparse_single(base) {
        Ok(obj) => obj.peel_to_commit().ok(),
        Err(_) => None,
    };
    let base_tree = match base_commit {
        Some(c) => c.tree().ok(),
        None => None,
    };

    let mut diff_opts = git2::DiffOptions::new();
    diff_opts
        .include_untracked(true)
        .recurse_untracked_dirs(true);

    let diff = match base_tree {
        Some(tree) => repo.diff_tree_to_workdir_with_index(Some(&tree), Some(&mut diff_opts))?,
        None => repo.diff_tree_to_workdir_with_index(None, Some(&mut diff_opts))?,
    };

    let stats = diff.stats()?;

    let mut changed_files = Vec::new();
    diff.foreach(
        &mut |delta, _| {
            if let Some(path) = delta.new_file().path() {
                changed_files.push(path.to_string_lossy().to_string());
            }
            true
        },
        None,
        None,
        None,
    )?;

    Ok(DiffSummary {
        files: stats.files_changed(),
        insertions: stats.insertions(),
        deletions: stats.deletions(),
        changed_files,
    })
}
