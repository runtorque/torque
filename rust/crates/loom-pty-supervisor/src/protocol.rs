//! Wire protocol for the PTY supervisor. Length-prefixed JSON frames —
//! 4-byte big-endian length header followed by a UTF-8 JSON body. Port of
//! `loom/pty_supervisor.py::{read_frame,write_frame}`.
//!
//! Ops and responses use `op: "<name>"` request fields and `type: "ok" |
//! "error" | "pong" | "list" | ...` response fields. Stream frames
//! (`snapshot`, `output`, `exit`) are pushed from the supervisor at any
//! time once a client subscribes.

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Protocol version. Bumped if we change framing or op semantics in a
/// backwards-incompatible way.
pub const PROTOCOL_VERSION: u32 = 1;

pub const DEFAULT_SOCKET_NAME: &str = "pty_supervisor.sock";
pub const DEFAULT_PID_FILE_NAME: &str = "pty_supervisor.pid";
pub const DEFAULT_LOG_FILE_NAME: &str = "pty_supervisor.log";

/// Soft cap on frame size. Snapshot replay can carry the full session
/// buffer tail (up to `BUFFER_LIMIT_BYTES` base64-encoded), so the cap is
/// generous.
pub const BUFFER_LIMIT_BYTES: usize = 256 * 1024;
pub const MAX_FRAME_BYTES: usize = 2 * BUFFER_LIMIT_BYTES + 4096;

/// Read one length-prefixed JSON frame. `Ok(None)` on clean EOF before
/// the header. Errors propagate so callers can close the connection.
pub async fn read_frame<R: AsyncReadExt + Unpin>(
    reader: &mut R,
) -> anyhow::Result<Option<serde_json::Value>> {
    let mut header = [0u8; 4];
    match reader.read_exact(&mut header).await {
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e.into()),
    }
    let len = u32::from_be_bytes(header) as usize;
    if len == 0 {
        return Ok(Some(serde_json::Value::Object(Default::default())));
    }
    if len > MAX_FRAME_BYTES {
        anyhow::bail!("frame too large: {len} bytes");
    }
    let mut body = vec![0u8; len];
    reader.read_exact(&mut body).await?;
    Ok(Some(serde_json::from_slice(&body)?))
}

/// Write one length-prefixed JSON frame.
pub async fn write_frame<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    msg: &serde_json::Value,
) -> anyhow::Result<()> {
    let body = serde_json::to_vec(msg)?;
    if body.len() > MAX_FRAME_BYTES {
        anyhow::bail!("frame too large: {} bytes", body.len());
    }
    let header = (body.len() as u32).to_be_bytes();
    writer.write_all(&header).await?;
    writer.write_all(&body).await?;
    writer.flush().await?;
    Ok(())
}

// -- Request / response shapes -------------------------------------------
//
// The supervisor's wire format is untyped JSON — we use serde_json::Value
// at the transport layer and these helpers for ergonomic construction.
// The Python port chose the same approach so the field set stays easy to
// extend without bumping the version.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateRequest {
    pub session_id: String,
    pub cell_id: String,
    pub shell_argv: Vec<String>,
    #[serde(default)]
    pub env: std::collections::HashMap<String, String>,
    #[serde(default)]
    pub cwd: Option<String>,
    pub cols: u16,
    pub rows: u16,
    #[serde(default)]
    pub startup_command: Option<String>,
    /// Millis to wait after opening the shell before typing
    /// `startup_command`. Matches Python's 120ms default.
    #[serde(default)]
    pub startup_delay_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionSnapshot {
    pub session_id: String,
    pub cell_id: String,
    pub pid: u32,
    pub alive: bool,
    pub cols: u16,
    pub rows: u16,
    pub total_bytes: u64,
}

/// Kinds of errors the supervisor returns in the `code` field. Callers
/// can branch on these without parsing the human-readable `message`.
pub mod error_codes {
    pub const PROTOCOL_ERROR: &str = "protocol_error";
    pub const INTERNAL_ERROR: &str = "internal_error";
    pub const SESSION_EXISTS: &str = "session_exists";
    pub const UNKNOWN_SESSION: &str = "unknown_session";
    pub const SHELL_SPAWN_FAILED: &str = "shell_spawn_failed";
    pub const WRITE_FAILED: &str = "write_failed";
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    #[tokio::test]
    async fn roundtrips_a_frame() {
        let (mut a, mut b) = duplex(64 * 1024);
        let sent = serde_json::json!({"op": "ping"});
        write_frame(&mut a, &sent).await.unwrap();
        let got = read_frame(&mut b).await.unwrap().unwrap();
        assert_eq!(got, sent);
    }

    #[tokio::test]
    async fn clean_eof_returns_none() {
        let (a, mut b) = duplex(64);
        drop(a);
        let got = read_frame(&mut b).await.unwrap();
        assert!(got.is_none());
    }

    #[tokio::test]
    async fn empty_frame_is_empty_object() {
        let (mut a, mut b) = duplex(64);
        a.write_all(&[0, 0, 0, 0]).await.unwrap();
        a.flush().await.unwrap();
        drop(a);
        let got = read_frame(&mut b).await.unwrap().unwrap();
        assert_eq!(got, serde_json::json!({}));
    }

    #[tokio::test]
    async fn oversize_is_rejected() {
        let (mut a, mut b) = duplex(64);
        let bad = ((MAX_FRAME_BYTES + 1) as u32).to_be_bytes();
        a.write_all(&bad).await.unwrap();
        a.flush().await.unwrap();
        drop(a);
        let err = read_frame(&mut b).await.unwrap_err();
        assert!(err.to_string().contains("frame too large"));
    }
}
