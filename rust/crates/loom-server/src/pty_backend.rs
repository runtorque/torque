//! Thin dispatch wrapper over the two PTY backends.
//!
//! * `Local` — `LocalPtyBackend`, PTYs owned inside the daemon process.
//!   Fast, simple, but sessions die when the daemon exits.
//! * `Supervised` — `SupervisedPtyBackend`, PTYs owned by a detached
//!   sidecar (`loom-pty-supervisor`). Sessions survive daemon restarts.
//!
//! Every call site in the server goes through this enum so the choice
//! of backend can be flipped at startup without churning the dispatch
//! code.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use loom_pty::{LocalPtyBackend, PtyEvent};
use loom_pty_supervisor::SupervisedPtyBackend;
use tokio::sync::mpsc;

#[derive(Clone)]
pub enum PtyBackend {
    Local(Arc<LocalPtyBackend>),
    Supervised(Arc<SupervisedPtyBackend>),
}

impl PtyBackend {
    pub fn new_local() -> (Self, mpsc::Receiver<PtyEvent>) {
        let (backend, rx) = LocalPtyBackend::new();
        (Self::Local(Arc::new(backend)), rx)
    }

    pub fn new_supervised(
        backend: SupervisedPtyBackend,
        rx: mpsc::Receiver<PtyEvent>,
    ) -> (Self, mpsc::Receiver<PtyEvent>) {
        (Self::Supervised(Arc::new(backend)), rx)
    }

    pub async fn spawn(
        &self,
        cell_id: &str,
        command: &str,
        cwd: Option<PathBuf>,
        env: HashMap<String, String>,
        rows: u16,
        cols: u16,
    ) -> Result<()> {
        match self {
            // The local backend returns a `PtySession` from spawn; we
            // don't need it here (the event channel does the heavy
            // lifting). Discard to match the supervised signature.
            Self::Local(b) => {
                b.spawn(cell_id, command, cwd, env, rows, cols).await?;
                Ok(())
            }
            Self::Supervised(b) => b.spawn(cell_id, command, cwd, env, rows, cols).await,
        }
    }

    pub async fn write(&self, cell_id: &str, data: &[u8]) -> Result<()> {
        match self {
            Self::Local(b) => b.write(cell_id, data).await,
            Self::Supervised(b) => b.write(cell_id, data).await,
        }
    }

    pub async fn resize(&self, cell_id: &str, rows: u16, cols: u16) -> Result<()> {
        match self {
            Self::Local(b) => b.resize(cell_id, rows, cols).await,
            Self::Supervised(b) => b.resize(cell_id, rows, cols).await,
        }
    }

    pub async fn close(&self, cell_id: &str) -> Result<()> {
        match self {
            Self::Local(b) => b.close(cell_id).await,
            Self::Supervised(b) => b.close(cell_id).await,
        }
    }

    pub async fn has_session(&self, cell_id: &str) -> bool {
        match self {
            Self::Local(b) => b.has_session(cell_id).await,
            Self::Supervised(b) => b.has_session(cell_id).await,
        }
    }

    /// Only the supervised variant can re-attach to an existing
    /// supervisor-owned session on startup. The local variant owns its
    /// PTYs and they're gone by the time this would be called.
    pub async fn adopt(&self, cell_id: &str, session_id: &str) -> Result<()> {
        match self {
            Self::Local(_) => {
                anyhow::bail!("adopt is only supported on the supervised backend")
            }
            Self::Supervised(b) => b.adopt(cell_id, session_id).await,
        }
    }

    pub fn is_supervised(&self) -> bool {
        matches!(self, Self::Supervised(_))
    }

    pub fn supervised(&self) -> Option<&Arc<SupervisedPtyBackend>> {
        match self {
            Self::Supervised(b) => Some(b),
            _ => None,
        }
    }
}
