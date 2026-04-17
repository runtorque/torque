//! Sidecar process lifecycle: ping, spawn-if-down, pid file management.
//!
//! Port of `loom/pty_supervisor.py::ensure_running` + `spawn_detached`.

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::Result;
use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;

use crate::protocol::{
    read_frame, write_frame, DEFAULT_LOG_FILE_NAME, DEFAULT_PID_FILE_NAME, DEFAULT_SOCKET_NAME,
};

/// Absolute filesystem paths for a supervisor instance — derived from
/// a single `data_dir` root so the sidecar, client, and installer agree.
#[derive(Debug, Clone)]
pub struct SupervisorPaths {
    pub data_dir: PathBuf,
    pub socket: PathBuf,
    pub pid_file: PathBuf,
    pub log_file: PathBuf,
}

impl SupervisorPaths {
    pub fn from_data_dir(data_dir: impl Into<PathBuf>) -> Self {
        let data_dir = data_dir.into();
        Self {
            socket: data_dir.join(DEFAULT_SOCKET_NAME),
            pid_file: data_dir.join(DEFAULT_PID_FILE_NAME),
            log_file: data_dir.join(DEFAULT_LOG_FILE_NAME),
            data_dir,
        }
    }
}

/// Response from a successful `ping` — used by `ensure_running` to tell
/// "supervisor live and responsive" from "stale socket file".
#[derive(Debug, Clone)]
pub struct PingResult {
    pub pid: u32,
    pub version: u32,
}

pub async fn ping(socket: &Path, timeout: Duration) -> Option<PingResult> {
    if !socket.exists() {
        return None;
    }
    let stream = tokio::time::timeout(timeout, UnixStream::connect(socket))
        .await
        .ok()?
        .ok()?;
    let (r, mut w) = stream.into_split();
    let mut reader = tokio::io::BufReader::new(r);
    if tokio::time::timeout(timeout, write_frame(&mut w, &json!({"op": "ping"})))
        .await
        .is_err()
    {
        return None;
    }
    let resp = tokio::time::timeout(timeout, read_frame(&mut reader))
        .await
        .ok()?
        .ok()?;
    let resp: Value = resp?;
    if resp.get("type").and_then(|v| v.as_str()) != Some("pong") {
        return None;
    }
    let pid = resp.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let version = resp.get("version").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    // Clean shutdown of the ping socket.
    let _ = w.shutdown().await;
    Some(PingResult { pid, version })
}

/// If a supervisor is already running at `paths.socket`, return its pong.
/// Otherwise unlink any stale socket/pid files and spawn a fresh sidecar,
/// waiting up to `startup_deadline` for its socket to come up.
///
/// The sidecar binary is the current executable — re-exec'd with
/// `--pty-supervisor --data-dir <path>`. That keeps us from shipping a
/// separate binary; the `loom-app` front door dispatches into
/// `loom_pty_supervisor::supervisor_main` when it sees that flag.
pub async fn ensure_running(
    paths: &SupervisorPaths,
    startup_deadline: Duration,
) -> Result<PingResult> {
    // Already up?
    if let Some(p) = ping(&paths.socket, Duration::from_secs(1)).await {
        return Ok(p);
    }
    // Remove stale socket + pid files. Leaving a stale socket makes
    // `UnixStream::connect` fail with ECONNREFUSED rather than
    // ENOENT — the ping above returned None so we already know the
    // socket isn't responsive.
    let _ = std::fs::remove_file(&paths.socket);
    let _ = std::fs::remove_file(&paths.pid_file);

    spawn_detached(paths)?;

    // Poll until the supervisor answers a ping or we hit the deadline.
    let start = std::time::Instant::now();
    while start.elapsed() < startup_deadline {
        if let Some(p) = ping(&paths.socket, Duration::from_millis(500)).await {
            return Ok(p);
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    anyhow::bail!(
        "supervisor did not come up within {:?} at {}",
        startup_deadline,
        paths.socket.display()
    )
}

/// Locate the `loom-pty-supervisor` binary. Search order:
///   1. `LOOM_PTY_SUPERVISOR_BIN` env var (absolute path).
///   2. Next to the currently-running executable, i.e. same directory
///      as `loom-app`. Typical after `cargo build` or an installed
///      bundle.
///   3. Bare `loom-pty-supervisor` on `PATH`.
pub fn resolve_supervisor_binary() -> PathBuf {
    if let Ok(p) = std::env::var("LOOM_PTY_SUPERVISOR_BIN") {
        if !p.is_empty() {
            return PathBuf::from(p);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sibling = dir.join("loom-pty-supervisor");
            if sibling.exists() {
                return sibling;
            }
        }
    }
    PathBuf::from("loom-pty-supervisor")
}

/// Fork a detached supervisor process. The child joins a new session
/// (setsid) so SIGHUP to the daemon doesn't cascade, matching Python's
/// `start_new_session=True`. stdout/stderr redirect to the log file;
/// stdin is /dev/null.
pub fn spawn_detached(paths: &SupervisorPaths) -> Result<u32> {
    std::fs::create_dir_all(&paths.data_dir)?;

    let bin = resolve_supervisor_binary();
    let mut cmd = std::process::Command::new(&bin);
    cmd.arg("--data-dir").arg(&paths.data_dir);

    // Redirect stdio to the log file so the child doesn't inherit the
    // parent's tty — if the daemon is killed, the sidecar doesn't get
    // SIGHUP from the controlling terminal.
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&paths.log_file)?;
    let log_err = log.try_clone()?;
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(log))
        .stderr(std::process::Stdio::from(log_err));

    // setsid() in the child so SIGHUP to the daemon doesn't cascade to
    // the supervisor. `process_group(0)` is Rust's stdlib equivalent of
    // Python's `start_new_session=True`.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }

    let child = cmd.spawn()?;
    let pid = child.id();
    // Best-effort pid-file write from the parent side so even if the
    // supervisor crashes before its own pid write, a tombstone exists.
    let _ = std::fs::write(&paths.pid_file, pid.to_string());
    // Drop the child handle — the sidecar is now independent. Rust
    // would otherwise reap it on drop in debug, which defeats the point
    // of detaching.
    std::mem::forget(child);
    Ok(pid)
}
