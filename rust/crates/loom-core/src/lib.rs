//! Core Loom engine: in-memory state, delta ops, SQLite persistence, event bus.

pub mod artifacts;
pub mod config;
pub mod db;
pub mod delta;
pub mod error;
pub mod events;
pub mod prompts;
pub mod slug;
pub mod state;
pub mod task_ids;

pub use error::{Error, Result};
