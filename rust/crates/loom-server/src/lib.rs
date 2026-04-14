//! Axum HTTP/WS server + command dispatcher.

pub mod app;
pub mod ws;
pub mod commands;
pub mod events;
pub mod mcp;
pub mod scheduler;
pub mod uploads;

pub use app::{run_server, ServerConfig, ServerHandle};
