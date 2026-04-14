//! portable-pty backed terminal backend.
//!
//! Spawns a shell or command under a PTY, streams output bytes to a bounded
//! channel. Designed to plug into `loom-core::terminal::TerminalBackend` once
//! that trait lands.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{bail, Context, Result};
use portable_pty::{CommandBuilder, PtySize};
use tokio::sync::{mpsc, Mutex};

use super::session::{PtyEvent, PtySession};

/// Spawn handle for one PTY — the writer and a kill token.
pub struct PtyHandle {
    writer: Arc<Mutex<Box<dyn std::io::Write + Send>>>,
    master: Arc<Mutex<Box<dyn portable_pty::MasterPty + Send>>>,
    cell_id: String,
}

impl PtyHandle {
    pub fn cell_id(&self) -> &str {
        &self.cell_id
    }

    pub async fn write(&self, bytes: &[u8]) -> Result<()> {
        let mut w = self.writer.lock().await;
        use std::io::Write as _;
        w.write_all(bytes)?;
        Ok(())
    }

    pub async fn resize(&self, rows: u16, cols: u16) -> Result<()> {
        let master = self.master.lock().await;
        master
            .resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
            .map_err(|e| anyhow::anyhow!(e.to_string()))?;
        Ok(())
    }
}

pub struct LocalPtyBackend {
    sessions: Arc<Mutex<HashMap<String, PtyHandle>>>,
    tx: mpsc::Sender<PtyEvent>,
}

impl LocalPtyBackend {
    pub fn new() -> (Self, mpsc::Receiver<PtyEvent>) {
        let (tx, rx) = mpsc::channel(512);
        let backend = Self { sessions: Arc::new(Mutex::new(HashMap::new())), tx };
        (backend, rx)
    }

    /// Spawn a command as a PTY child, register under `cell_id`.
    pub async fn spawn(
        &self,
        cell_id: &str,
        command: &str,
        cwd: Option<PathBuf>,
        env: HashMap<String, String>,
        rows: u16,
        cols: u16,
    ) -> Result<PtySession> {
        if command.trim().is_empty() {
            bail!("empty command");
        }
        let pty_system = portable_pty::native_pty_system();
        let pair = pty_system
            .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
            .context("openpty")?;

        // Route through /bin/sh -c so the shell handles quoting, pipes, and
        // env expansion. Matches the Python `subprocess.Popen(..., shell=True)`
        // behavior used in loom/local_pty.py.
        let mut cmd = CommandBuilder::new("/bin/sh");
        cmd.arg("-c");
        cmd.arg(command);

        if let Some(cwd) = cwd {
            cmd.cwd(cwd);
        }
        for (k, v) in env {
            cmd.env(k, v);
        }

        let mut child = pair
            .slave
            .spawn_command(cmd)
            .map_err(|e| anyhow::anyhow!(e.to_string()))?;
        let pid = child.process_id();
        drop(pair.slave);

        let writer = pair
            .master
            .take_writer()
            .map_err(|e| anyhow::anyhow!(e.to_string()))?;

        // Reader loop on a blocking thread.
        let mut reader = pair
            .master
            .try_clone_reader()
            .map_err(|e| anyhow::anyhow!(e.to_string()))?;
        let tx = self.tx.clone();
        let cell = cell_id.to_string();
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            loop {
                match std::io::Read::read(&mut reader, &mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        let bytes = buf[..n].to_vec();
                        if tx
                            .blocking_send(PtyEvent::Output { cell_id: cell.clone(), bytes })
                            .is_err()
                        {
                            break;
                        }
                    }
                    Err(err) => {
                        let _ = tx.blocking_send(PtyEvent::Error {
                            cell_id: cell.clone(),
                            message: err.to_string(),
                        });
                        break;
                    }
                }
            }
            if let Ok(status) = child.wait() {
                let _ = tx.blocking_send(PtyEvent::Exited {
                    cell_id: cell,
                    status: status.exit_code() as i32,
                });
            }
        });

        let handle = PtyHandle {
            writer: Arc::new(Mutex::new(writer)),
            master: Arc::new(Mutex::new(pair.master)),
            cell_id: cell_id.to_string(),
        };

        self.sessions.lock().await.insert(cell_id.to_string(), handle);

        if let Some(pid) = pid {
            let _ = self
                .tx
                .send(PtyEvent::Spawned { cell_id: cell_id.to_string(), pid })
                .await;
        }

        Ok(PtySession { cell_id: cell_id.to_string(), pid, rows, cols })
    }

    pub async fn write(&self, cell_id: &str, data: &[u8]) -> Result<()> {
        let sessions = self.sessions.lock().await;
        let handle = sessions
            .get(cell_id)
            .ok_or_else(|| anyhow::anyhow!("no session"))?;
        handle.write(data).await
    }

    pub async fn resize(&self, cell_id: &str, rows: u16, cols: u16) -> Result<()> {
        let sessions = self.sessions.lock().await;
        let handle = sessions
            .get(cell_id)
            .ok_or_else(|| anyhow::anyhow!("no session"))?;
        handle.resize(rows, cols).await
    }

    pub async fn close(&self, cell_id: &str) -> Result<()> {
        let mut sessions = self.sessions.lock().await;
        sessions.remove(cell_id);
        Ok(())
    }
}

