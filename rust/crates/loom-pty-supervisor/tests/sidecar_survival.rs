//! Integration test: spawn the real `loom-pty-supervisor` binary as a
//! detached child, create a session, disconnect the client, reconnect,
//! and verify the session is still alive. This is the core value prop
//! of the supervisor — if this test passes, PTY sessions survive daemon
//! restarts in practice.

use std::path::PathBuf;
use std::time::Duration;

use loom_pty_supervisor::{
    ensure_running, protocol::CreateRequest, PtySupervisorClient, SupervisorPaths,
};

fn sidecar_binary() -> PathBuf {
    // `cargo test` sets CARGO_BIN_EXE_<name> for bins in the same crate.
    PathBuf::from(env!("CARGO_BIN_EXE_loom-pty-supervisor"))
}

#[tokio::test]
async fn supervisor_survives_client_disconnect() {
    let tmp = tempfile::tempdir().unwrap();
    let data_dir = tmp.path().to_path_buf();
    let paths = SupervisorPaths::from_data_dir(data_dir.clone());

    // Point ensure_running at the binary we just built.
    std::env::set_var("LOOM_PTY_SUPERVISOR_BIN", sidecar_binary());
    let pong = ensure_running(&paths, Duration::from_secs(10))
        .await
        .expect("supervisor should start");
    assert!(pong.pid > 0);
    let original_pid = pong.pid;

    // Session 1: cat-based session that will stay alive until closed.
    {
        let client = PtySupervisorClient::connect(&paths.socket).await.unwrap();
        let req = CreateRequest {
            session_id: "survive-1".into(),
            cell_id: "cell-x".into(),
            shell_argv: vec!["/bin/cat".into()],
            env: Default::default(),
            cwd: None,
            cols: 80,
            rows: 24,
            startup_command: None,
            startup_delay_ms: 0,
        };
        let pid = client.create(&req).await.unwrap();
        assert!(pid > 0);
        client.write("survive-1", b"hello across restart\n").await.unwrap();
        // Drop the client — the supervisor keeps owning the session.
        drop(client);
    }

    tokio::time::sleep(Duration::from_millis(200)).await;

    // Reconnect from scratch and verify the session is still there.
    {
        let client = PtySupervisorClient::connect(&paths.socket).await.unwrap();
        let pong_pid = client.ping().await.unwrap();
        assert_eq!(
            pong_pid, original_pid,
            "supervisor should still be the same process"
        );
        let sessions = client.list().await.unwrap();
        assert!(
            sessions
                .iter()
                .any(|s| s.get("session_id").and_then(|v| v.as_str()) == Some("survive-1")),
            "session survive-1 should still be tracked"
        );
        // Close the session cleanly so the test doesn't leave an
        // orphan cat process.
        client.close("survive-1").await.unwrap();
    }

    // Best-effort supervisor teardown so the tempdir cleanup doesn't
    // race an active listener. Killing the supervisor's process group
    // via its pid file is enough.
    if let Ok(pid_text) = std::fs::read_to_string(&paths.pid_file) {
        if let Ok(pid) = pid_text.trim().parse::<i32>() {
            unsafe {
                libc::kill(pid, libc::SIGTERM);
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    }
}
