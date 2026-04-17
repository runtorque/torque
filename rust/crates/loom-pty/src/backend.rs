//! portable-pty backed terminal backend.
//!
//! Spawns a shell or command under a PTY, streams output bytes to a bounded
//! channel. Designed to plug into `loom-core::terminal::TerminalBackend` once
//! that trait lands.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result};
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
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
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
        let backend = Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
            tx,
        };
        (backend, rx)
    }

    /// Spawn a command as a PTY child, register under `cell_id`.
    ///
    /// **Always** spawns an interactive login shell (derived from `$SHELL`,
    /// falling back to `/bin/zsh`) so `.zprofile` / `.zshrc` /
    /// `.bash_profile` / `.bashrc` are sourced — user aliases defined there
    /// then Just Work. If `command` is non-empty *and* isn't itself a bare
    /// shell, it's typed into the shell's stdin after a short delay, so the
    /// actual process (e.g. `claude`) runs **inside** the interactive shell
    /// and inherits its aliases. This mirrors Python Loom's behavior
    /// (`local_pty.py::_prepare_create` → shell_argv = [shell, -il], then
    /// write_input of the command after 120ms).
    pub async fn spawn(
        &self,
        cell_id: &str,
        command: &str,
        cwd: Option<PathBuf>,
        env: HashMap<String, String>,
        rows: u16,
        cols: u16,
    ) -> Result<PtySession> {
        let pty_system = portable_pty::native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .context("openpty")?;

        let trimmed = command.trim();
        let is_bare_shell = matches!(
            trimmed,
            "/bin/zsh"
                | "/bin/bash"
                | "/bin/sh"
                | "/bin/fish"
                | "zsh"
                | "bash"
                | "sh"
                | "fish"
                | ""
        );
        // Resolve the user's interactive shell. We spawn it with -il for
        // both the "no command" case (terminal) and the "arbitrary command"
        // case (agent) so the login-shell init files always run.
        let shell_path = env
            .get("SHELL")
            .cloned()
            .or_else(|| std::env::var("SHELL").ok())
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| "/bin/zsh".to_string());
        let shell_is_login_capable = matches!(
            shell_path.rsplit('/').next().unwrap_or(""),
            "zsh" | "bash" | "fish" | "sh"
        );

        let mut cmd = if is_bare_shell {
            // Spawn the requested bare shell (or default shell) as login+interactive.
            let target = if trimmed.is_empty() { shell_path.as_str() } else { trimmed };
            let mut c = CommandBuilder::new(target);
            if shell_is_login_capable || matches!(trimmed, "zsh" | "bash" | "fish" | "/bin/zsh" | "/bin/bash" | "/bin/fish") {
                c.arg("-il");
            } else {
                c.arg("-i");
            }
            c
        } else if shell_is_login_capable {
            // Spawn the user's interactive login shell — the caller will
            // type `command` into it after spawn completes.
            let mut c = CommandBuilder::new(&shell_path);
            c.arg("-il");
            c
        } else {
            // Fallback for exotic shells that don't honor -il: run the command
            // via /bin/sh directly. Aliases won't apply but at least the
            // command runs. Matches the original fallback path.
            let mut c = CommandBuilder::new("/bin/sh");
            c.arg("-c");
            c.arg(command);
            c
        };

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
        // Stateful UTF-8 incremental decoder. PTY output arrives in chunks
        // that can split a multi-byte codepoint across `read()` calls; if we
        // emit each chunk as-is with `from_utf8_lossy`, the partial byte at
        // the chunk boundary becomes U+FFFD and the next chunk starts with
        // an orphan continuation byte that also becomes U+FFFD — emoji,
        // CJK, and box-drawing glyphs all get shredded, which manifests as
        // the garbled overlapping renders users saw. Mirror Python's
        // `codecs.getincrementaldecoder("utf-8")(errors="replace")` by
        // buffering trailing incomplete sequences and folding them into
        // the next read.
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            let mut carry: Vec<u8> = Vec::new();
            loop {
                match std::io::Read::read(&mut reader, &mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        carry.extend_from_slice(&buf[..n]);
                        let mut out = String::with_capacity(carry.len());
                        let mut cursor = 0usize;
                        loop {
                            match std::str::from_utf8(&carry[cursor..]) {
                                Ok(s) => {
                                    out.push_str(s);
                                    cursor = carry.len();
                                    break;
                                }
                                Err(e) => {
                                    let valid_end = cursor + e.valid_up_to();
                                    out.push_str(
                                        std::str::from_utf8(&carry[cursor..valid_end]).unwrap(),
                                    );
                                    match e.error_len() {
                                        None => {
                                            // Trailing incomplete sequence — hold it for the next read.
                                            cursor = valid_end;
                                            break;
                                        }
                                        Some(bad_len) => {
                                            // Real invalid byte(s) — replace and continue.
                                            out.push('\u{FFFD}');
                                            cursor = valid_end + bad_len;
                                        }
                                    }
                                }
                            }
                        }
                        carry.drain(..cursor);
                        if !out.is_empty()
                            && tx
                                .blocking_send(PtyEvent::Output {
                                    cell_id: cell.clone(),
                                    text: out,
                                })
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
            if !carry.is_empty() {
                // Flush any trailing bytes as replacement chars so nothing is lost.
                let text = String::from_utf8_lossy(&carry).into_owned();
                let _ = tx.blocking_send(PtyEvent::Output {
                    cell_id: cell.clone(),
                    text,
                });
            }
            if let Ok(status) = child.wait() {
                let _ = tx.blocking_send(PtyEvent::Exited {
                    cell_id: cell,
                    status: status.exit_code() as i32,
                });
            }
        });

        // If we spawned a wrapper shell and the caller actually wants a
        // different command to run inside it, schedule the keystrokes: wait
        // a tick for the interactive shell to source .zshrc (so aliases
        // like `claude → claude --dangerously-skip-permissions` apply),
        // then write `<command>\r` to the slave. Matches Python Loom's
        // `write_input(cmd + "\r")` after 120ms.
        let needs_startup_input =
            !is_bare_shell && shell_is_login_capable && !trimmed.is_empty();
        let startup_cmd = if needs_startup_input {
            Some(command.trim().to_string())
        } else {
            None
        };

        let writer_arc = Arc::new(Mutex::new(writer));

        self.sessions.lock().await.insert(
            cell_id.to_string(),
            PtyHandle {
                writer: writer_arc.clone(),
                master: Arc::new(Mutex::new(pair.master)),
                cell_id: cell_id.to_string(),
            },
        );

        if let Some(pid) = pid {
            let _ = self
                .tx
                .send(PtyEvent::Spawned {
                    cell_id: cell_id.to_string(),
                    pid,
                })
                .await;
        }

        if let Some(cmd) = startup_cmd {
            // Sleep inline before returning — so by the time `spawn`
            // resolves, the shell has seen the typed command and any
            // subsequent `send_text` / `write` from the caller lands
            // downstream of it (i.e. on the child process launched by
            // the shell, not on the shell's own prompt).
            //
            // 250ms covers most .zshrc sourcing on macOS; a trailing
            // \r lands on an empty prompt if we beat init.
            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
            let mut guard = writer_arc.lock().await;
            use std::io::Write as _;
            let _ = guard.write_all(cmd.as_bytes());
            let _ = guard.write_all(b"\r");
            let _ = guard.flush();
        }

        Ok(PtySession {
            cell_id: cell_id.to_string(),
            pid,
            rows,
            cols,
        })
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

    /// True iff a PTY session exists for `cell_id`. Used by dispatch to
    /// decide between "boot the agent" vs "send the prompt to the already
    /// running shell" when the UI dispatches a task to an existing agent.
    pub async fn has_session(&self, cell_id: &str) -> bool {
        self.sessions.lock().await.contains_key(cell_id)
    }
}
