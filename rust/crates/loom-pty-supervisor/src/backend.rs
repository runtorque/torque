//! `SupervisedPtyBackend` — a drop-in for `loom_pty::LocalPtyBackend`
//! that delegates PTY ownership to the sidecar process. Public surface
//! (`spawn`, `write`, `resize`, `close`, `has_session`) mirrors
//! `LocalPtyBackend` so the server's call sites can switch backends by
//! wrapping them in `loom_pty::PtyBackend` (see `backend_kind` in
//! loom-server).
//!
//! Output + exit events are forwarded onto a `mpsc::Sender<PtyEvent>`
//! identical in shape to the local backend's channel, so downstream
//! consumers (the event pump in `loom-server::app::handle_pty_event`)
//! don't need to change.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use loom_pty::session::PtyEvent;
use loom_pty::shell::build_shell_argv;
use tokio::sync::{mpsc, Mutex};

use crate::client::{PtySupervisorClient, SubscriptionEvent};
use crate::protocol::CreateRequest;

/// Tracks per-cell supervisor session ids + subscription tasks so we
/// can tear them down on `close()` and detect "has live session".
struct SupervisedSession {
    session_id: String,
    forwarder: tokio::task::JoinHandle<()>,
}

pub struct SupervisedPtyBackend {
    client: PtySupervisorClient,
    sessions: Arc<Mutex<HashMap<String, SupervisedSession>>>,
    tx: mpsc::Sender<PtyEvent>,
}

impl SupervisedPtyBackend {
    /// Wrap an already-connected `PtySupervisorClient` with a local
    /// event channel compatible with `loom_pty::LocalPtyBackend::new`.
    pub fn new(client: PtySupervisorClient) -> (Self, mpsc::Receiver<PtyEvent>) {
        let (tx, rx) = mpsc::channel(512);
        (
            Self {
                client,
                sessions: Arc::new(Mutex::new(HashMap::new())),
                tx,
            },
            rx,
        )
    }

    /// Drop-in replacement for `LocalPtyBackend::spawn`. Builds the
    /// shell argv locally (so alias resolution via `.zshrc` keeps
    /// working) then forwards to the supervisor.
    pub async fn spawn(
        &self,
        cell_id: &str,
        command: &str,
        cwd: Option<PathBuf>,
        env: HashMap<String, String>,
        rows: u16,
        cols: u16,
    ) -> Result<()> {
        let (shell_argv, startup_command) = build_shell_argv(command, &env);
        let session_id = cell_id.to_string();
        let req = CreateRequest {
            session_id: session_id.clone(),
            cell_id: cell_id.to_string(),
            shell_argv,
            env,
            cwd: cwd.map(|p| p.to_string_lossy().to_string()),
            cols,
            rows,
            startup_command,
            startup_delay_ms: 120,
        };
        let pid = self.client.create(&req).await?;

        // Subscribe so output starts flowing. The forwarder task drains
        // SubscriptionEvents and translates them to PtyEvents on our
        // mpsc channel. We drop the Snapshot here — the server's event
        // pump expects incremental output, and new PTYs have an empty
        // snapshot anyway.
        let mut rx = self.client.subscribe(&session_id).await?;
        let tx = self.tx.clone();
        let cell_id_for_task = cell_id.to_string();
        let sessions_for_task = self.sessions.clone();
        let forwarder = tokio::spawn(async move {
            forward_subscription(rx_drain_snapshot(&mut rx).await, rx, tx, cell_id_for_task.clone())
                .await;
            // On task exit, drop ourselves from the session map so
            // `has_session` returns false. The session is probably
            // already gone from the supervisor too.
            sessions_for_task.lock().await.remove(&cell_id_for_task);
        });

        let _ = self
            .tx
            .send(PtyEvent::Spawned {
                cell_id: cell_id.to_string(),
                pid,
            })
            .await;

        self.sessions.lock().await.insert(
            cell_id.to_string(),
            SupervisedSession {
                session_id,
                forwarder,
            },
        );
        Ok(())
    }

    pub async fn write(&self, cell_id: &str, data: &[u8]) -> Result<()> {
        let session_id = self.session_id_for(cell_id).await.ok_or_else(|| {
            anyhow::anyhow!("no supervisor session for cell {cell_id}")
        })?;
        self.client.write(&session_id, data).await
    }

    pub async fn resize(&self, cell_id: &str, rows: u16, cols: u16) -> Result<()> {
        let session_id = self.session_id_for(cell_id).await.ok_or_else(|| {
            anyhow::anyhow!("no supervisor session for cell {cell_id}")
        })?;
        self.client.resize(&session_id, rows, cols).await
    }

    pub async fn close(&self, cell_id: &str) -> Result<()> {
        let entry = self.sessions.lock().await.remove(cell_id);
        if let Some(entry) = entry {
            entry.forwarder.abort();
            let _ = self.client.close(&entry.session_id).await;
        }
        Ok(())
    }

    pub async fn has_session(&self, cell_id: &str) -> bool {
        self.sessions.lock().await.contains_key(cell_id)
    }

    /// Reconcile locally-tracked sessions against what the supervisor
    /// reports live. Returns the set of cell ids we thought were alive
    /// but aren't — the caller should transition those cells to
    /// `stopped` in the daemon state. Idempotent.
    pub async fn reconcile(&self) -> Result<Vec<String>> {
        let live: std::collections::HashSet<String> = self
            .client
            .list()
            .await?
            .into_iter()
            .filter_map(|v| {
                v.get("session_id")
                    .and_then(|x| x.as_str())
                    .map(String::from)
            })
            .collect();
        let mut dead: Vec<String> = Vec::new();
        let mut tracked = self.sessions.lock().await;
        tracked.retain(|cell_id, entry| {
            if live.contains(&entry.session_id) {
                true
            } else {
                dead.push(cell_id.clone());
                entry.forwarder.abort();
                false
            }
        });
        Ok(dead)
    }

    /// Re-attach to an existing supervisor session without re-spawning
    /// the child. Used by `reconnect_orphans` on daemon startup so the
    /// event pump resumes receiving output.
    pub async fn adopt(&self, cell_id: &str, session_id: &str) -> Result<()> {
        let rx = self.client.subscribe(session_id).await?;
        let tx = self.tx.clone();
        let cell_id_owned = cell_id.to_string();
        let sessions_for_task = self.sessions.clone();
        let mut rx = rx;
        let forwarder = tokio::spawn(async move {
            let snapshot = rx_drain_snapshot(&mut rx).await;
            forward_subscription(snapshot, rx, tx, cell_id_owned.clone()).await;
            sessions_for_task.lock().await.remove(&cell_id_owned);
        });
        self.sessions.lock().await.insert(
            cell_id.to_string(),
            SupervisedSession {
                session_id: session_id.to_string(),
                forwarder,
            },
        );
        Ok(())
    }

    async fn session_id_for(&self, cell_id: &str) -> Option<String> {
        self.sessions
            .lock()
            .await
            .get(cell_id)
            .map(|s| s.session_id.clone())
    }

    /// Access the underlying client — useful for the app startup path
    /// which needs to call `list` before any cells are adopted.
    pub fn client(&self) -> &PtySupervisorClient {
        &self.client
    }
}

/// Drain the first `Snapshot` frame off the subscription stream. The
/// supervisor guarantees the snapshot comes before any live output, so
/// this never blocks behind output frames. Returns `None` if the
/// stream closed first.
async fn rx_drain_snapshot(
    rx: &mut mpsc::UnboundedReceiver<SubscriptionEvent>,
) -> Option<SubscriptionEvent> {
    rx.recv().await
}

/// Forward live SubscriptionEvents as PtyEvents onto the daemon's
/// event channel. The snapshot (if any) is replayed as the first
/// `Output` event so downstream consumers see the buffer tail exactly
/// once.
async fn forward_subscription(
    snapshot: Option<SubscriptionEvent>,
    mut rx: mpsc::UnboundedReceiver<SubscriptionEvent>,
    tx: mpsc::Sender<PtyEvent>,
    cell_id: String,
) {
    if let Some(SubscriptionEvent::Snapshot { data, .. }) = snapshot {
        if !data.is_empty() {
            let text = String::from_utf8_lossy(&data).into_owned();
            let _ = tx
                .send(PtyEvent::Output {
                    cell_id: cell_id.clone(),
                    text,
                })
                .await;
        }
    }
    while let Some(event) = rx.recv().await {
        match event {
            SubscriptionEvent::Snapshot { .. } => {
                // A second snapshot means we re-subscribed (likely after
                // a supervisor reconnect). Let consumers know there's
                // been a discontinuity and replay the tail. For now we
                // just drop it — the client handles re-subscribe and
                // a fresh snapshot is only rare.
            }
            SubscriptionEvent::Output { data, .. } => {
                let text = String::from_utf8_lossy(&data).into_owned();
                if tx
                    .send(PtyEvent::Output {
                        cell_id: cell_id.clone(),
                        text,
                    })
                    .await
                    .is_err()
                {
                    break;
                }
            }
            SubscriptionEvent::Exit { exit_status, .. } => {
                let _ = tx
                    .send(PtyEvent::Exited {
                        cell_id: cell_id.clone(),
                        status: exit_status.unwrap_or(-1),
                    })
                    .await;
                break;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::PtySupervisor;

    async fn with_supervisor<F, Fut>(f: F)
    where
        F: FnOnce(std::path::PathBuf) -> Fut,
        Fut: std::future::Future<Output = ()>,
    {
        let sup = Arc::new(PtySupervisor::new());
        let tmp = tempfile::tempdir().unwrap();
        let sock = tmp.path().join("sup.sock");
        let sup_clone = sup.clone();
        let sock_for_serve = sock.clone();
        tokio::spawn(async move {
            let _ = sup_clone.serve(&sock_for_serve).await;
        });
        for _ in 0..80 {
            if sock.exists() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        }
        f(sock).await;
        drop(tmp);
    }

    #[tokio::test]
    async fn spawn_write_receive_close_roundtrip() {
        with_supervisor(|sock| async move {
            let client = PtySupervisorClient::connect(&sock).await.unwrap();
            let (backend, mut rx) = SupervisedPtyBackend::new(client);

            backend
                .spawn(
                    "cell-1",
                    "/bin/sh",
                    None,
                    {
                        // Force the /bin/sh -c fallback so no login shell
                        // sourcing delays the echo.
                        let mut env = HashMap::new();
                        env.insert("SHELL".into(), "/usr/local/bin/nosuch-shell".into());
                        env
                    },
                    24,
                    80,
                )
                .await
                .unwrap();

            assert!(backend.has_session("cell-1").await);

            backend.write("cell-1", b"echo supervised-hello\n").await.unwrap();

            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
            let mut saw_hello = false;
            let mut saw_spawned = false;
            while std::time::Instant::now() < deadline {
                let Ok(Some(event)) = tokio::time::timeout(
                    std::time::Duration::from_millis(500),
                    rx.recv(),
                )
                .await
                else {
                    break;
                };
                match event {
                    PtyEvent::Spawned { cell_id, .. } if cell_id == "cell-1" => {
                        saw_spawned = true;
                    }
                    PtyEvent::Output { text, cell_id } if cell_id == "cell-1" => {
                        if text.contains("supervised-hello") {
                            saw_hello = true;
                            break;
                        }
                    }
                    _ => {}
                }
            }
            assert!(saw_spawned, "expected Spawned event");
            assert!(saw_hello, "expected echoed 'supervised-hello' on channel");

            backend.close("cell-1").await.unwrap();
            assert!(!backend.has_session("cell-1").await);
        })
        .await;
    }
}
