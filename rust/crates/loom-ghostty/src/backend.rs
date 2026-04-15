//! Ghostty-backed terminal backend.
//!
//! Implements the same surface as `loom_pty::LocalPtyBackend` so the server
//! can swap backends transparently. In v1 this is a thin wrapper that
//! delegates PTY lifecycle to `loom-pty` and — when the `ffi` feature is on —
//! additionally pipes bytes into a libghostty terminal state machine for
//! native rendering.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use loom_pty::{LocalPtyBackend, PtyEvent, PtySession};
use tokio::sync::mpsc;

pub struct GhosttyBackend {
    inner: Arc<LocalPtyBackend>,
}

impl GhosttyBackend {
    /// Construct a new backend. Callers own the returned event receiver and
    /// pump it into state + (in ffi mode) the libghostty renderer.
    pub fn new() -> (Self, mpsc::Receiver<PtyEvent>) {
        let (inner, rx) = LocalPtyBackend::new();
        (
            Self {
                inner: Arc::new(inner),
            },
            rx,
        )
    }

    pub async fn spawn(
        &self,
        cell_id: &str,
        command: &str,
        cwd: Option<PathBuf>,
        env: HashMap<String, String>,
        rows: u16,
        cols: u16,
    ) -> Result<PtySession> {
        #[cfg(feature = "ffi")]
        {
            // Phase 8: allocate a libghostty surface for this cell and start
            // feeding bytes from the PTY into it. For now, the pass-through
            // path is used unconditionally.
            crate::ffi::register_cell_surface(cell_id)?;
        }
        self.inner
            .spawn(cell_id, command, cwd, env, rows, cols)
            .await
    }

    pub async fn write(&self, cell_id: &str, data: &[u8]) -> Result<()> {
        self.inner.write(cell_id, data).await
    }

    pub async fn resize(&self, cell_id: &str, rows: u16, cols: u16) -> Result<()> {
        self.inner.resize(cell_id, rows, cols).await
    }

    pub async fn close(&self, cell_id: &str) -> Result<()> {
        #[cfg(feature = "ffi")]
        {
            crate::ffi::release_cell_surface(cell_id);
        }
        self.inner.close(cell_id).await
    }

    pub fn inner(&self) -> &LocalPtyBackend {
        &self.inner
    }
}
