//! Core Loom engine: in-memory state, delta ops, SQLite persistence, event bus.

pub mod config;
pub mod task_ids;
pub mod artifacts;
pub mod slug;
pub mod delta;
pub mod state;
pub mod db;
pub mod events;
pub mod error;

pub use error::{Error, Result};
