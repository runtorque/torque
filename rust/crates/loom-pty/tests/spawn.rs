//! End-to-end PTY test — spawns a short-lived command and verifies output.

use std::time::Duration;

use loom_pty::{LocalPtyBackend, PtyEvent};

#[tokio::test]
async fn spawn_echo_emits_output_and_exits() {
    let (pty, mut rx) = LocalPtyBackend::new();

    pty.spawn(
        "test-cell",
        "sh -c 'echo hello-from-pty; exit 0'",
        None,
        Default::default(),
        24,
        80,
    )
    .await
    .expect("spawn");

    let mut saw_output = false;
    let mut saw_exit = false;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    while tokio::time::Instant::now() < deadline {
        match tokio::time::timeout(Duration::from_millis(500), rx.recv()).await {
            Ok(Some(PtyEvent::Output { bytes, .. })) => {
                let text = String::from_utf8_lossy(&bytes);
                if text.contains("hello-from-pty") {
                    saw_output = true;
                }
            }
            Ok(Some(PtyEvent::Exited { status, .. })) => {
                saw_exit = true;
                assert_eq!(status, 0);
                break;
            }
            Ok(Some(PtyEvent::Error { message, .. })) => {
                panic!("pty error: {message}");
            }
            Ok(Some(_)) => continue,
            Ok(None) => break,
            Err(_) => continue,
        }
    }

    assert!(saw_output, "expected 'hello-from-pty' in PTY output");
    assert!(saw_exit, "expected PTY to emit an Exited event");
}
