//! Git worktree management: create/remove/list, checkpoints, diff, merge detection.

pub mod checkpoint;
pub mod diff;
pub mod gitignore;
pub mod manager;
pub mod merge;

pub use manager::WorktreeManager;
