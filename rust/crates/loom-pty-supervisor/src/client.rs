//! Daemon-side client for the PTY supervisor.
//!
//! Port of `loom/pty_supervisor_client.py`. One persistent UDS connection
//! demultiplexes two streams:
//!
//! * **Request/response** — `ping`, `create`, `write`, `resize`, `close`,
//!   `list`, `subscribe`, `unsubscribe`. The client attaches a one-shot
//!   to each in-flight request and routes the first matching response.
//! * **Stream frames** — `snapshot`, `output`, `exit`. Routed by
//!   `session_id` to a per-session `mpsc` channel (`SubscriptionEvent`).
//!
//! On connection drop the client auto-reconnects with exponential backoff.
//! After a successful reconnect it compares the supervisor PID to detect
//! "fresh instance" (supervisor crashed + respawned) and re-subscribes to
//! every session the daemon is still tracking. The `on_reconnect`
//! callback fires with `{previous_pid, supervisor_pid, fresh}` so
//! higher-level code can reconcile orphan sessions.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine;
use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tokio::net::unix::OwnedWriteHalf;
use tokio::net::UnixStream;
use tokio::sync::{mpsc, oneshot, Mutex};

use crate::protocol::{read_frame, write_frame};

const RECONNECT_BACKOFF_START_MS: u64 = 500;
const RECONNECT_BACKOFF_MAX_MS: u64 = 5_000;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);

/// Stream event delivered to subscribers of a session.
#[derive(Debug, Clone)]
pub enum SubscriptionEvent {
    /// Buffer tail replayed on (re-)subscribe — everything before this
    /// point has been seen by the supervisor but not necessarily by the
    /// current subscriber, so downstream consumers typically reset their
    /// display and replay this payload.
    Snapshot {
        session_id: String,
        cell_id: String,
        pid: u32,
        alive: bool,
        cols: u16,
        rows: u16,
        total_bytes: u64,
        data: Vec<u8>,
    },
    Output {
        session_id: String,
        data: Vec<u8>,
        byte_offset: u64,
    },
    Exit {
        session_id: String,
        exit_status: Option<i32>,
    },
}

/// Per-reconnect diagnostic. `fresh = true` means the supervisor's PID
/// changed across the reconnect — i.e. the sidecar crashed and a new one
/// respawned, so every previously-known session is gone and callers
/// should finalize them.
#[derive(Debug, Clone)]
pub struct ReconnectInfo {
    pub previous_pid: Option<u32>,
    pub supervisor_pid: u32,
    pub fresh: bool,
}

type Pending = Arc<Mutex<Vec<PendingRequest>>>;
type Subscriptions = Arc<Mutex<HashMap<String, mpsc::UnboundedSender<SubscriptionEvent>>>>;

struct PendingRequest {
    /// Matching rule: for ops that get a `{type: "ok", op: X, ...}`
    /// response, we also check `op`. For `list` we expect `type: list`;
    /// for `ping`, `type: pong`. Errors match by `type == "error"`.
    want_type: &'static str,
    want_op: Option<&'static str>,
    tx: oneshot::Sender<Result<Value>>,
}

/// Daemon-side handle. Cheap to `Clone` — internally reference-counted.
#[derive(Clone)]
pub struct PtySupervisorClient {
    socket: PathBuf,
    writer: Arc<Mutex<Option<OwnedWriteHalf>>>,
    pending: Pending,
    subs: Subscriptions,
    on_reconnect: Arc<Mutex<Option<Box<dyn Fn(ReconnectInfo) + Send + Sync>>>>,
    last_pid: Arc<Mutex<Option<u32>>>,
}

impl PtySupervisorClient {
    /// Open a persistent connection to the supervisor at `socket`.
    /// Spawns the dispatcher task on the current runtime.
    pub async fn connect(socket: impl Into<PathBuf>) -> Result<Self> {
        let socket = socket.into();
        let client = Self {
            socket: socket.clone(),
            writer: Arc::new(Mutex::new(None)),
            pending: Arc::new(Mutex::new(Vec::new())),
            subs: Arc::new(Mutex::new(HashMap::new())),
            on_reconnect: Arc::new(Mutex::new(None)),
            last_pid: Arc::new(Mutex::new(None)),
        };
        client.spawn_dispatcher().await?;
        Ok(client)
    }

    /// Set a callback invoked after every (re-)connect with the fresh /
    /// same-instance diagnostic.
    pub async fn on_reconnect<F>(&self, f: F)
    where
        F: Fn(ReconnectInfo) + Send + Sync + 'static,
    {
        *self.on_reconnect.lock().await = Some(Box::new(f));
    }

    // -- High-level ops ------------------------------------------------

    pub async fn ping(&self) -> Result<u32> {
        let v = self
            .request(
                json!({"op": "ping"}),
                "pong",
                None,
            )
            .await?;
        Ok(v.get("pid").and_then(|p| p.as_u64()).unwrap_or(0) as u32)
    }

    pub async fn create(
        &self,
        req: &crate::protocol::CreateRequest,
    ) -> Result<u32> {
        let mut body = serde_json::to_value(req)?;
        body.as_object_mut()
            .ok_or_else(|| anyhow::anyhow!("create req not an object"))?
            .insert("op".into(), json!("create"));
        let v = self.request(body, "ok", Some("create")).await?;
        let pid = v.get("pid").and_then(|p| p.as_u64()).unwrap_or(0) as u32;
        Ok(pid)
    }

    pub async fn write(&self, session_id: &str, data: &[u8]) -> Result<()> {
        let body = json!({
            "op": "write",
            "session_id": session_id,
            "data": B64.encode(data),
        });
        self.request(body, "ok", Some("write")).await?;
        Ok(())
    }

    pub async fn resize(&self, session_id: &str, rows: u16, cols: u16) -> Result<()> {
        let body = json!({
            "op": "resize",
            "session_id": session_id,
            "rows": rows,
            "cols": cols,
        });
        self.request(body, "ok", Some("resize")).await?;
        Ok(())
    }

    pub async fn close(&self, session_id: &str) -> Result<()> {
        let body = json!({
            "op": "close",
            "session_id": session_id,
        });
        self.request(body, "ok", Some("close")).await?;
        Ok(())
    }

    pub async fn list(&self) -> Result<Vec<Value>> {
        let v = self
            .request(json!({"op": "list"}), "list", None)
            .await?;
        Ok(v.get("sessions")
            .and_then(|s| s.as_array())
            .cloned()
            .unwrap_or_default())
    }

    /// Subscribe to a session's output stream. Returns a receiver — close
    /// it to unsubscribe (the client will emit an `unsubscribe` op).
    pub async fn subscribe(
        &self,
        session_id: &str,
    ) -> Result<mpsc::UnboundedReceiver<SubscriptionEvent>> {
        let (tx, rx) = mpsc::unbounded_channel();
        self.subs.lock().await.insert(session_id.to_string(), tx);
        let body = json!({"op": "subscribe", "session_id": session_id});
        // The supervisor sends three frames in sequence: the `ok`
        // response, then `snapshot`, then live `output`/`exit`. We only
        // wait for the `ok` here; the snapshot + subsequent frames get
        // routed by the dispatcher to the subscription channel.
        self.request(body, "ok", Some("subscribe")).await?;
        Ok(rx)
    }

    pub async fn unsubscribe(&self, session_id: &str) -> Result<()> {
        self.subs.lock().await.remove(session_id);
        let body = json!({"op": "unsubscribe", "session_id": session_id});
        self.request(body, "ok", Some("unsubscribe")).await?;
        Ok(())
    }

    /// IDs of sessions we're currently subscribed to. Used by the
    /// dispatcher on reconnect to re-send `subscribe` ops.
    pub async fn tracked_session_ids(&self) -> Vec<String> {
        self.subs.lock().await.keys().cloned().collect()
    }

    // -- Internals -----------------------------------------------------

    async fn request(
        &self,
        body: Value,
        want_type: &'static str,
        want_op: Option<&'static str>,
    ) -> Result<Value> {
        // Enqueue the pending entry BEFORE sending so a racing response
        // can't arrive before we register the matcher.
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.push(PendingRequest {
            want_type,
            want_op,
            tx,
        });
        let send_result = {
            let mut guard = self.writer.lock().await;
            match guard.as_mut() {
                Some(w) => write_frame(w, &body).await,
                None => Err(anyhow::anyhow!("not connected")),
            }
        };
        if let Err(err) = send_result {
            // Drop the pending entry we just added.
            self.pending.lock().await.pop();
            return Err(err);
        }
        let resp = tokio::time::timeout(REQUEST_TIMEOUT, rx)
            .await
            .map_err(|_| anyhow::anyhow!("supervisor request timeout"))??;
        resp
    }

    async fn spawn_dispatcher(&self) -> Result<()> {
        // First connect synchronously so the caller gets an immediate
        // error if the supervisor is down. After that, the dispatcher
        // reconnects in the background.
        let stream = UnixStream::connect(&self.socket)
            .await
            .map_err(|e| anyhow::anyhow!("connect {}: {e}", self.socket.display()))?;
        let (read, write) = stream.into_split();
        *self.writer.lock().await = Some(write);

        let this = self.clone();
        tokio::spawn(async move {
            let mut reader = tokio::io::BufReader::new(read);
            let mut initial = true;
            loop {
                // On first iteration we already have a live connection.
                // On subsequent iterations we've just completed a
                // reconnect, so send `ping` + re-subscribe to learn
                // whether the supervisor is the same instance.
                if !initial {
                    if let Err(err) = this.post_reconnect().await {
                        tracing::warn!(?err, "post_reconnect failed");
                    }
                }
                initial = false;

                loop {
                    match read_frame(&mut reader).await {
                        Ok(Some(frame)) => {
                            this.dispatch_frame(frame).await;
                        }
                        Ok(None) => break, // EOF
                        Err(err) => {
                            tracing::debug!(?err, "supervisor frame read error");
                            break;
                        }
                    }
                }

                // Flush pending requests with a transport error so callers
                // wake up promptly instead of waiting for REQUEST_TIMEOUT.
                {
                    let mut pending = this.pending.lock().await;
                    for PendingRequest { tx, .. } in pending.drain(..) {
                        let _ = tx.send(Err(anyhow::anyhow!("supervisor disconnected")));
                    }
                }
                {
                    let mut guard = this.writer.lock().await;
                    *guard = None;
                }

                // Reconnect with exponential backoff.
                let mut delay = RECONNECT_BACKOFF_START_MS;
                let new_reader = loop {
                    tokio::time::sleep(Duration::from_millis(delay)).await;
                    match UnixStream::connect(&this.socket).await {
                        Ok(stream) => {
                            let (r, w) = stream.into_split();
                            *this.writer.lock().await = Some(w);
                            break tokio::io::BufReader::new(r);
                        }
                        Err(_) => {
                            delay = (delay * 2).min(RECONNECT_BACKOFF_MAX_MS);
                        }
                    }
                };
                reader = new_reader;
            }
        });
        Ok(())
    }

    /// After a successful reconnect: ping to learn the supervisor's PID,
    /// decide if it's a fresh instance, notify `on_reconnect`, and
    /// re-subscribe to every session we had before.
    async fn post_reconnect(&self) -> Result<()> {
        let pid = self.ping().await?;
        let previous = *self.last_pid.lock().await;
        let fresh = previous.map(|prev| prev != pid).unwrap_or(false);
        *self.last_pid.lock().await = Some(pid);

        if let Some(cb) = self.on_reconnect.lock().await.as_ref() {
            cb(ReconnectInfo {
                previous_pid: previous,
                supervisor_pid: pid,
                fresh,
            });
        }

        if fresh {
            // The supervisor lost all sessions — drop our subscription
            // map so callers don't expect output frames that will never
            // come. They'll be re-subscribed by higher layers after
            // reconciling against `list`.
            self.subs.lock().await.clear();
        } else {
            let sids: Vec<String> = self.subs.lock().await.keys().cloned().collect();
            for sid in sids {
                let body = json!({"op": "subscribe", "session_id": sid});
                let _ = self.request(body, "ok", Some("subscribe")).await;
            }
        }
        Ok(())
    }

    async fn dispatch_frame(&self, frame: Value) {
        let ty = frame
            .get("type")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        match ty.as_str() {
            "snapshot" | "output" | "exit" => {
                let Some(sid) = frame
                    .get("session_id")
                    .and_then(|v| v.as_str())
                    .map(String::from)
                else {
                    return;
                };
                let event = match ty.as_str() {
                    "snapshot" => SubscriptionEvent::Snapshot {
                        session_id: sid.clone(),
                        cell_id: frame
                            .get("cell_id")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        pid: frame.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                        alive: frame.get("alive").and_then(|v| v.as_bool()).unwrap_or(true),
                        cols: frame.get("cols").and_then(|v| v.as_u64()).unwrap_or(80) as u16,
                        rows: frame.get("rows").and_then(|v| v.as_u64()).unwrap_or(24) as u16,
                        total_bytes: frame
                            .get("total_bytes")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0),
                        data: frame
                            .get("data")
                            .and_then(|v| v.as_str())
                            .and_then(|s| B64.decode(s).ok())
                            .unwrap_or_default(),
                    },
                    "output" => SubscriptionEvent::Output {
                        session_id: sid.clone(),
                        data: frame
                            .get("data")
                            .and_then(|v| v.as_str())
                            .and_then(|s| B64.decode(s).ok())
                            .unwrap_or_default(),
                        byte_offset: frame
                            .get("byte_offset")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0),
                    },
                    _ => SubscriptionEvent::Exit {
                        session_id: sid.clone(),
                        exit_status: frame.get("exit_status").and_then(|v| v.as_i64()).map(|n| n as i32),
                    },
                };
                if let Some(sender) = self.subs.lock().await.get(&sid) {
                    let _ = sender.send(event);
                }
            }
            _ => {
                // Request/response frame. First matching pending entry
                // wins. Frames with no match are dropped (logged).
                let mut pending = self.pending.lock().await;
                let mut idx_match: Option<usize> = None;
                let req_op = frame.get("op").and_then(|v| v.as_str());
                for (i, req) in pending.iter().enumerate() {
                    if ty == "error" {
                        idx_match = Some(i);
                        break;
                    }
                    if ty != req.want_type {
                        continue;
                    }
                    if let Some(op_want) = req.want_op {
                        if req_op != Some(op_want) {
                            continue;
                        }
                    }
                    idx_match = Some(i);
                    break;
                }
                if let Some(i) = idx_match {
                    let req = pending.remove(i);
                    drop(pending);
                    if ty == "error" {
                        let msg = frame
                            .get("message")
                            .and_then(|v| v.as_str())
                            .unwrap_or("supervisor error")
                            .to_string();
                        let code = frame
                            .get("code")
                            .and_then(|v| v.as_str())
                            .unwrap_or("error")
                            .to_string();
                        let _ = req.tx.send(Err(anyhow::anyhow!("{code}: {msg}")));
                    } else {
                        let _ = req.tx.send(Ok(frame));
                    }
                } else {
                    tracing::debug!(frame = %frame, "unmatched supervisor frame");
                }
            }
        }
    }

    /// Drop the persistent connection. Pending requests fail; the
    /// dispatcher will reconnect automatically.
    pub async fn shutdown(&self) {
        if let Some(mut w) = self.writer.lock().await.take() {
            let _ = w.shutdown().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::CreateRequest;
    use crate::supervisor::PtySupervisor;

    async fn spawn_supervisor() -> std::path::PathBuf {
        let sup = Arc::new(PtySupervisor::new());
        let tmp = tempfile::tempdir().unwrap();
        let sock = tmp.path().join("sup.sock");
        let path_for_serve = sock.clone();
        tokio::spawn(async move {
            let _ = sup.serve(&path_for_serve).await;
        });
        for _ in 0..80 {
            if sock.exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        std::mem::forget(tmp);
        sock
    }

    #[tokio::test]
    async fn ping_via_client() {
        let sock = spawn_supervisor().await;
        let client = PtySupervisorClient::connect(&sock).await.unwrap();
        let pid = client.ping().await.unwrap();
        assert!(pid > 0);
    }

    #[tokio::test]
    async fn create_write_list_close_via_client() {
        let sock = spawn_supervisor().await;
        let client = PtySupervisorClient::connect(&sock).await.unwrap();

        let req = CreateRequest {
            session_id: "s1".into(),
            cell_id: "cell-1".into(),
            shell_argv: vec!["/bin/sh".into(), "-c".into(), "cat".into()],
            env: Default::default(),
            cwd: None,
            cols: 80,
            rows: 24,
            startup_command: None,
            startup_delay_ms: 0,
        };
        let pid = client.create(&req).await.unwrap();
        assert!(pid > 0);

        let list = client.list().await.unwrap();
        assert_eq!(list.len(), 1);

        client.write("s1", b"hello\n").await.unwrap();

        let mut rx = client.subscribe("s1").await.unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        let mut saw_snapshot = false;
        let mut saw_output_with_hello = false;
        while std::time::Instant::now() < deadline {
            let Ok(Some(event)) =
                tokio::time::timeout(Duration::from_millis(500), rx.recv()).await
            else {
                break;
            };
            match event {
                SubscriptionEvent::Snapshot { .. } => saw_snapshot = true,
                SubscriptionEvent::Output { data, .. } => {
                    if String::from_utf8_lossy(&data).contains("hello") {
                        saw_output_with_hello = true;
                        break;
                    }
                }
                _ => {}
            }
        }
        assert!(saw_snapshot, "expected snapshot on subscribe");
        assert!(
            saw_output_with_hello,
            "expected to see 'hello' echoed back from `cat`"
        );

        client.close("s1").await.unwrap();
    }
}
