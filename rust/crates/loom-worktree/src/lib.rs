//! Git worktree management: create/remove/list, checkpoints, diff, merge detection.

pub mod manager;
pub mod diff;
pub mod merge;
pub mod checkpoint;
pub mod gitignore;

pub use manager::WorktreeManager;
