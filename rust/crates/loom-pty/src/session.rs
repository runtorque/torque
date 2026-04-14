//! PTY session types + event enum.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PtySession {
    pub cell_id: String,
    pub pid: Option<u32>,
    pub rows: u16,
    pub cols: u16,
}

#[derive(Debug, Clone)]
pub enum PtyEvent {
    Spawned { cell_id: String, pid: u32 },
    Output { cell_id: String, bytes: Vec<u8> },
    Exited { cell_id: String, status: i32 },
    Error { cell_id: String, message: String },
}
