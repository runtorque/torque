//! Ensure .loom/worktrees is excluded from git without polluting .gitignore.
//!
//! Matches the behavior of `loom/worktree.py::ensure_git_exclude` — write to
//! `.git/info/exclude` so the exclusion is local to the clone.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::Result;

const EXCLUDE_MARKER: &str = "# loom:worktrees";
const EXCLUDE_PATTERN: &str = ".loom/worktrees/";

pub fn ensure_git_exclude(repo_root: &Path) -> Result<()> {
    let exclude_path: PathBuf = repo_root.join(".git").join("info").join("exclude");
    if let Some(parent) = exclude_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let existing = fs::read_to_string(&exclude_path).unwrap_or_default();
    if existing.lines().any(|l| l.trim() == EXCLUDE_PATTERN) {
        return Ok(());
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&exclude_path)?;
    if !existing.is_empty() && !existing.ends_with('\n') {
        writeln!(f)?;
    }
    writeln!(f, "{}", EXCLUDE_MARKER)?;
    writeln!(f, "{}", EXCLUDE_PATTERN)?;
    Ok(())
}
