//! Axum HTTP/WS server + command dispatcher.

pub mod app;
pub mod commands;
pub mod events;
pub mod mcp;
pub mod paths;
pub mod pty_backend;
pub mod scheduler;
pub mod terminal_bridge;
pub mod uploads;
pub mod weaver_buffer;
pub mod ws;

pub use app::{run_server, ServerConfig, ServerHandle};
