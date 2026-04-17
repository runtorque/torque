//! In-process PTY supervisor. Owns the PTY master FDs and child
//! processes; serves a set of local clients over a Unix domain socket.
//!
//! Port of `loom/pty_supervisor.py::PtySupervisor`. Kept intentionally
//! self-contained — no dependency on `loom-server` or `loom-core` so the
//! supervisor binary stays small and has no circular graph with
//! `loom-pty` (which only uses portable-pty locally).

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use anyhow::Result;
use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine;
use portable_pty::{CommandBuilder, MasterPty, PtySize};
use serde_json::{json, Value};
use tokio::net::{unix::OwnedWriteHalf, UnixListener, UnixStream};
use tokio::runtime::Handle;
use tokio::sync::{broadcast, Mutex};

use crate::protocol::{
    self, error_codes, read_frame, write_frame, BUFFER_LIMIT_BYTES, PROTOCOL_VERSION,
};

const SESSION_BROADCAST_CAPACITY: usize = 256;

/// One frame broadcast on a session's channel. Cheap to clone (Arc<Value>
/// internally via serde_json's `Value`).
#[derive(Clone)]
struct StreamFrame(Value);

struct SessionInner {
    session_id: String,
    cell_id: String,
    pid: u32,
    cols: u16,
    rows: u16,
    buffer: Vec<u8>,
    total_bytes: u64,
    closed: bool,
    exit_status: Option<i32>,
    writer: Box<dyn std::io::Write + Send>,
    master: Box<dyn MasterPty + Send>,
    broadcaster: broadcast::Sender<StreamFrame>,
}

impl SessionInner {
    fn snapshot_value(&self) -> Value {
        json!({
            "session_id": self.session_id,
            "cell_id": self.cell_id,
            "pid": self.pid,
            "alive": !self.closed,
            "cols": self.cols,
            "rows": self.rows,
            "total_bytes": self.total_bytes,
        })
    }
}

type SharedWriter = Arc<Mutex<OwnedWriteHalf>>;
type SessionMap = Arc<Mutex<HashMap<String, Arc<Mutex<SessionInner>>>>>;

pub struct PtySupervisor {
    sessions: SessionMap,
    pid: u32,
}

impl Default for PtySupervisor {
    fn default() -> Self {
        Self::new()
    }
}

impl PtySupervisor {
    pub fn new() -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
            pid: std::process::id(),
        }
    }

    /// Listen on a unix-domain socket and serve clients forever. Binds
    /// with mode 0600 and unlinks any stale socket file at `socket_path`.
    pub async fn serve(self: Arc<Self>, socket_path: &Path) -> Result<()> {
        if socket_path.exists() {
            let _ = std::fs::remove_file(socket_path);
        }
        if let Some(parent) = socket_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let listener = UnixListener::bind(socket_path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let perms = std::fs::Permissions::from_mode(0o600);
            let _ = std::fs::set_permissions(socket_path, perms);
        }
        tracing::info!(
            path = %socket_path.display(),
            pid = self.pid,
            "supervisor listening"
        );

        loop {
            let (stream, _) = match listener.accept().await {
                Ok(s) => s,
                Err(err) => {
                    tracing::warn!(?err, "accept failed");
                    continue;
                }
            };
            let this = self.clone();
            tokio::spawn(async move {
                if let Err(err) = this.handle_client(stream).await {
                    tracing::debug!(?err, "client disconnected");
                }
            });
        }
    }

    async fn handle_client(self: Arc<Self>, stream: UnixStream) -> Result<()> {
        let (read_half, write_half) = stream.into_split();
        let writer: SharedWriter = Arc::new(Mutex::new(write_half));
        let mut reader = tokio::io::BufReader::new(read_half);
        let mut subs: HashMap<String, tokio::task::JoinHandle<()>> = HashMap::new();

        loop {
            let msg = match read_frame(&mut reader).await {
                Ok(Some(v)) => v,
                Ok(None) => break,
                Err(err) => {
                    write_err(&writer, error_codes::PROTOCOL_ERROR, &err.to_string()).await;
                    break;
                }
            };
            let op = msg
                .get("op")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            match op.as_str() {
                "ping" => {
                    let mut w = writer.lock().await;
                    let _ = write_frame(
                        &mut *w,
                        &json!({
                            "type": "pong",
                            "version": PROTOCOL_VERSION,
                            "pid": self.pid,
                        }),
                    )
                    .await;
                }
                "create" => self.op_create(&msg, &writer).await,
                "write" => self.op_write(&msg, &writer).await,
                "resize" => self.op_resize(&msg, &writer).await,
                "close" => self.op_close(&msg, &writer).await,
                "list" => self.op_list(&writer).await,
                "subscribe" => self.op_subscribe(&msg, &writer, &mut subs).await,
                "unsubscribe" => self.op_unsubscribe(&msg, &writer, &mut subs).await,
                _ => {
                    write_err(
                        &writer,
                        error_codes::PROTOCOL_ERROR,
                        &format!("unknown op: {op:?}"),
                    )
                    .await;
                }
            }
        }
        for (_sid, task) in subs.drain() {
            task.abort();
        }
        Ok(())
    }

    // -- ops ------------------------------------------------------------

    async fn op_list(&self, writer: &SharedWriter) {
        let mut out: Vec<Value> = Vec::new();
        for sess in self.sessions.lock().await.values() {
            out.push(sess.lock().await.snapshot_value());
        }
        let mut w = writer.lock().await;
        let _ = write_frame(
            &mut *w,
            &json!({"type": "list", "sessions": out}),
        )
        .await;
    }

    async fn op_create(&self, msg: &Value, writer: &SharedWriter) {
        let req: protocol::CreateRequest = match serde_json::from_value(msg.clone()) {
            Ok(v) => v,
            Err(err) => {
                return write_err(
                    writer,
                    error_codes::PROTOCOL_ERROR,
                    &format!("bad create request: {err}"),
                )
                .await;
            }
        };
        if req.session_id.is_empty() || req.shell_argv.is_empty() {
            return write_err(
                writer,
                error_codes::PROTOCOL_ERROR,
                "create requires session_id and shell_argv",
            )
            .await;
        }
        {
            let sessions = self.sessions.lock().await;
            if sessions.contains_key(&req.session_id) {
                return write_err(writer, error_codes::SESSION_EXISTS, &req.session_id).await;
            }
        }

        // Spawn the PTY + child on a blocking thread. portable-pty's
        // API is sync; we can't hold the tokio runtime hostage on the
        // openpty/fork path.
        let req_for_spawn = req.clone();
        let spawn_result =
            tokio::task::spawn_blocking(move || spawn_pty(&req_for_spawn)).await;
        let SpawnOutcome {
            inner,
            reader,
            child,
        } = match spawn_result {
            Ok(Ok(v)) => v,
            Ok(Err(err)) => {
                return write_err(
                    writer,
                    error_codes::SHELL_SPAWN_FAILED,
                    &err.to_string(),
                )
                .await
            }
            Err(err) => {
                return write_err(
                    writer,
                    error_codes::SHELL_SPAWN_FAILED,
                    &err.to_string(),
                )
                .await
            }
        };

        let pid = inner.pid;
        let session_id = inner.session_id.clone();
        let arc = Arc::new(Mutex::new(inner));
        self.sessions
            .lock()
            .await
            .insert(session_id.clone(), arc.clone());

        // Optional startup command (e.g. "claude\r"), typed into the
        // shell after a short delay so `.zshrc` aliases resolve first.
        if let Some(cmd) = req.startup_command {
            if !cmd.is_empty() {
                let arc_for_startup = arc.clone();
                let delay = std::time::Duration::from_millis(req.startup_delay_ms.max(1));
                tokio::spawn(async move {
                    tokio::time::sleep(delay).await;
                    let mut sess = arc_for_startup.lock().await;
                    let _ = std::io::Write::write_all(&mut sess.writer, cmd.as_bytes());
                    let _ = std::io::Write::flush(&mut sess.writer);
                });
            }
        }

        spawn_reader_thread(arc.clone(), reader, Handle::current());
        spawn_waiter_thread(
            arc.clone(),
            child,
            self.sessions.clone(),
            Handle::current(),
        );

        let mut w = writer.lock().await;
        let _ = write_frame(
            &mut *w,
            &json!({
                "type": "ok",
                "op": "create",
                "session_id": session_id,
                "pid": pid,
            }),
        )
        .await;
    }

    async fn op_write(&self, msg: &Value, writer: &SharedWriter) {
        let session_id = msg.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
        let data_b64 = msg.get("data").and_then(|v| v.as_str()).unwrap_or("");
        let arc = self.sessions.lock().await.get(session_id).cloned();
        let Some(arc) = arc else {
            return write_err(writer, error_codes::UNKNOWN_SESSION, session_id).await;
        };
        let bytes = if data_b64.is_empty() {
            Vec::new()
        } else {
            match B64.decode(data_b64) {
                Ok(v) => v,
                Err(err) => {
                    return write_err(
                        writer,
                        error_codes::PROTOCOL_ERROR,
                        &format!("bad base64: {err}"),
                    )
                    .await;
                }
            }
        };
        if !bytes.is_empty() {
            let mut sess = arc.lock().await;
            if sess.closed {
                return write_err(writer, error_codes::UNKNOWN_SESSION, session_id).await;
            }
            if let Err(err) = std::io::Write::write_all(&mut sess.writer, &bytes) {
                return write_err(writer, error_codes::WRITE_FAILED, &err.to_string()).await;
            }
            let _ = std::io::Write::flush(&mut sess.writer);
        }
        let mut w = writer.lock().await;
        let _ = write_frame(&mut *w, &json!({"type": "ok", "op": "write"})).await;
    }

    async fn op_resize(&self, msg: &Value, writer: &SharedWriter) {
        let session_id = msg.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
        let cols = msg
            .get("cols")
            .and_then(|v| v.as_u64())
            .unwrap_or(80)
            .max(1) as u16;
        let rows = msg
            .get("rows")
            .and_then(|v| v.as_u64())
            .unwrap_or(24)
            .max(1) as u16;
        let arc = self.sessions.lock().await.get(session_id).cloned();
        let Some(arc) = arc else {
            return write_err(writer, error_codes::UNKNOWN_SESSION, session_id).await;
        };
        {
            let mut sess = arc.lock().await;
            sess.cols = cols;
            sess.rows = rows;
            let _ = sess.master.resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            });
        }
        let mut w = writer.lock().await;
        let _ = write_frame(&mut *w, &json!({"type": "ok", "op": "resize"})).await;
    }

    async fn op_close(&self, msg: &Value, writer: &SharedWriter) {
        let session_id = msg.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
        let arc = self.sessions.lock().await.get(session_id).cloned();
        let Some(arc) = arc else {
            return write_err(writer, error_codes::UNKNOWN_SESSION, session_id).await;
        };
        let pid = arc.lock().await.pid as i32;
        // Kill the process group — the child was spawned as its own
        // process leader via portable-pty's setsid-style start_new_session.
        // The waiter thread will reap and broadcast `exit`.
        unsafe {
            let _ = libc::kill(-pid, libc::SIGHUP);
        }
        let mut w = writer.lock().await;
        let _ = write_frame(&mut *w, &json!({"type": "ok", "op": "close"})).await;
    }

    async fn op_subscribe(
        &self,
        msg: &Value,
        writer: &SharedWriter,
        subs: &mut HashMap<String, tokio::task::JoinHandle<()>>,
    ) {
        let session_id = msg
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let arc = self.sessions.lock().await.get(&session_id).cloned();
        let Some(arc) = arc else {
            return write_err(writer, error_codes::UNKNOWN_SESSION, &session_id).await;
        };

        let (snapshot_msg, rx, already_exited) = {
            let sess = arc.lock().await;
            let snapshot = json!({
                "type": "snapshot",
                "session_id": session_id,
                "cell_id": sess.cell_id,
                "pid": sess.pid,
                "alive": !sess.closed,
                "cols": sess.cols,
                "rows": sess.rows,
                "total_bytes": sess.total_bytes,
                "data": B64.encode(&sess.buffer),
            });
            let rx = sess.broadcaster.subscribe();
            let exited = if sess.closed {
                Some(sess.exit_status)
            } else {
                None
            };
            (snapshot, rx, exited)
        };

        {
            let mut w = writer.lock().await;
            let _ = write_frame(
                &mut *w,
                &json!({
                    "type": "ok",
                    "op": "subscribe",
                    "session_id": session_id,
                }),
            )
            .await;
            let _ = write_frame(&mut *w, &snapshot_msg).await;
            if let Some(status) = already_exited {
                let _ = write_frame(
                    &mut *w,
                    &json!({
                        "type": "exit",
                        "session_id": session_id,
                        "exit_status": status,
                    }),
                )
                .await;
                return;
            }
        }

        // Stop any prior forwarder for this session on this connection
        // (re-subscribe is idempotent).
        if let Some(old) = subs.remove(&session_id) {
            old.abort();
        }
        let writer_task = writer.clone();
        let mut rx = rx;
        let task = tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(frame) => {
                        let mut w = writer_task.lock().await;
                        if write_frame(&mut *w, &frame.0).await.is_err() {
                            break;
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => continue,
                    Err(broadcast::error::RecvError::Closed) => break,
                }
            }
        });
        subs.insert(session_id, task);
    }

    async fn op_unsubscribe(
        &self,
        msg: &Value,
        writer: &SharedWriter,
        subs: &mut HashMap<String, tokio::task::JoinHandle<()>>,
    ) {
        let session_id = msg
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if let Some(task) = subs.remove(&session_id) {
            task.abort();
        }
        let mut w = writer.lock().await;
        let _ = write_frame(&mut *w, &json!({"type": "ok", "op": "unsubscribe"})).await;
    }
}

async fn write_err(writer: &SharedWriter, code: &str, message: &str) {
    let mut w = writer.lock().await;
    let _ = write_frame(
        &mut *w,
        &json!({
            "type": "error",
            "code": code,
            "message": message,
        }),
    )
    .await;
}

// -- Spawn / reader / waiter ---------------------------------------------

struct SpawnOutcome {
    inner: SessionInner,
    reader: Box<dyn std::io::Read + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
}

fn spawn_pty(req: &protocol::CreateRequest) -> Result<SpawnOutcome> {
    let pty_system = portable_pty::native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows: req.rows,
            cols: req.cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| anyhow::anyhow!(e.to_string()))?;

    let (bin, rest) = req
        .shell_argv
        .split_first()
        .ok_or_else(|| anyhow::anyhow!("shell_argv empty"))?;
    let mut cmd = CommandBuilder::new(bin);
    for arg in rest {
        cmd.arg(arg);
    }
    if let Some(cwd) = req.cwd.as_ref() {
        if !cwd.is_empty() {
            cmd.cwd(cwd);
        }
    }
    for (k, v) in &req.env {
        cmd.env(k, v);
    }

    let child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|e| anyhow::anyhow!(e.to_string()))?;
    let pid = child.process_id().unwrap_or(0);
    drop(pair.slave);

    let writer = pair
        .master
        .take_writer()
        .map_err(|e| anyhow::anyhow!(e.to_string()))?;
    let reader = pair
        .master
        .try_clone_reader()
        .map_err(|e| anyhow::anyhow!(e.to_string()))?;

    let (tx, _rx) = broadcast::channel(SESSION_BROADCAST_CAPACITY);
    Ok(SpawnOutcome {
        inner: SessionInner {
            session_id: req.session_id.clone(),
            cell_id: req.cell_id.clone(),
            pid,
            cols: req.cols,
            rows: req.rows,
            buffer: Vec::new(),
            total_bytes: 0,
            closed: false,
            exit_status: None,
            writer,
            master: pair.master,
            broadcaster: tx,
        },
        reader,
        child,
    })
}

fn spawn_reader_thread(
    arc: Arc<Mutex<SessionInner>>,
    mut reader: Box<dyn std::io::Read + Send>,
    rt: Handle,
) {
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match std::io::Read::read(&mut reader, &mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let chunk = buf[..n].to_vec();
                    let arc = arc.clone();
                    // Hop into the runtime to take the mutex + broadcast.
                    rt.block_on(async move {
                        let mut sess = arc.lock().await;
                        sess.total_bytes += n as u64;
                        sess.buffer.extend_from_slice(&chunk);
                        if sess.buffer.len() > BUFFER_LIMIT_BYTES {
                            let excess = sess.buffer.len() - BUFFER_LIMIT_BYTES;
                            sess.buffer.drain(..excess);
                        }
                        let total = sess.total_bytes;
                        let session_id = sess.session_id.clone();
                        let tx = sess.broadcaster.clone();
                        drop(sess);
                        let frame = json!({
                            "type": "output",
                            "session_id": session_id,
                            "data": B64.encode(&chunk),
                            "byte_offset": total,
                        });
                        let _ = tx.send(StreamFrame(frame));
                    });
                }
                Err(_) => break,
            }
        }
    });
}

fn spawn_waiter_thread(
    arc: Arc<Mutex<SessionInner>>,
    mut child: Box<dyn portable_pty::Child + Send + Sync>,
    sessions: SessionMap,
    rt: Handle,
) {
    std::thread::spawn(move || {
        let status = child.wait().ok();
        let exit_code = status.map(|s| s.exit_code() as i32);
        rt.block_on(async move {
            let (sid, tx, exit_value) = {
                let mut sess = arc.lock().await;
                sess.closed = true;
                sess.exit_status = exit_code;
                (
                    sess.session_id.clone(),
                    sess.broadcaster.clone(),
                    sess.exit_status,
                )
            };
            let frame = json!({
                "type": "exit",
                "session_id": sid,
                "exit_status": exit_value,
            });
            let _ = tx.send(StreamFrame(frame));
            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
            sessions.lock().await.remove(&sid);
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;

    async fn spawn_supervisor() -> (Arc<PtySupervisor>, std::path::PathBuf) {
        let sup = Arc::new(PtySupervisor::new());
        let tmp = tempfile::tempdir().unwrap();
        let sock = tmp.path().join("sup.sock");
        let path_for_serve = sock.clone();
        let sup_clone = sup.clone();
        tokio::spawn(async move {
            let _ = sup_clone.serve(&path_for_serve).await;
        });
        for _ in 0..80 {
            if sock.exists() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        }
        // The tempdir is dropped at the end of the test; keep the socket
        // alive by handing back the sock path only — the tempdir is in
        // the caller's scope via ownership of the PathBuf's parent.
        std::mem::forget(tmp); // leak in tests
        (sup, sock)
    }

    #[tokio::test]
    async fn ping_roundtrips() {
        let (_sup, sock) = spawn_supervisor().await;
        let stream = UnixStream::connect(&sock).await.unwrap();
        let (r, mut w) = stream.into_split();
        let mut reader = tokio::io::BufReader::new(r);
        write_frame(&mut w, &json!({"op": "ping"})).await.unwrap();
        let pong = read_frame(&mut reader).await.unwrap().unwrap();
        assert_eq!(pong.get("type").and_then(|v| v.as_str()), Some("pong"));
    }

    #[tokio::test]
    async fn create_list_and_exit() {
        let (_sup, sock) = spawn_supervisor().await;
        let stream = UnixStream::connect(&sock).await.unwrap();
        let (r, mut w) = stream.into_split();
        let mut reader = tokio::io::BufReader::new(r);

        write_frame(
            &mut w,
            &json!({
                "op": "create",
                "session_id": "s1",
                "cell_id": "cell-1",
                "shell_argv": ["/bin/sh", "-c", "echo hi; sleep 0.2; exit 0"],
                "cols": 80u16,
                "rows": 24u16,
            }),
        )
        .await
        .unwrap();
        let ok = read_frame(&mut reader).await.unwrap().unwrap();
        assert_eq!(ok.get("op").and_then(|v| v.as_str()), Some("create"));

        write_frame(&mut w, &json!({"op": "list"})).await.unwrap();
        let list = read_frame(&mut reader).await.unwrap().unwrap();
        let arr = list.get("sessions").and_then(|v| v.as_array()).unwrap();
        assert!(arr.iter().any(|s| s.get("session_id").and_then(|x| x.as_str()) == Some("s1")));

        // Subscribe and drain until we see the exit frame.
        write_frame(
            &mut w,
            &json!({"op": "subscribe", "session_id": "s1"}),
        )
        .await
        .unwrap();
        let mut saw_exit = false;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while std::time::Instant::now() < deadline {
            let frame = tokio::time::timeout(
                std::time::Duration::from_millis(1500),
                read_frame(&mut reader),
            )
            .await
            .ok()
            .and_then(|r| r.ok())
            .flatten();
            let Some(frame) = frame else { break };
            if frame.get("type").and_then(|v| v.as_str()) == Some("exit") {
                saw_exit = true;
                break;
            }
        }
        assert!(saw_exit, "expected to see exit frame after child completes");
        let _ = w.shutdown().await;
    }
}
