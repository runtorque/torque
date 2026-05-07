use std::env;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn attach_without_daemon_prints_bridge_error_without_tauri_panic() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("reserve free port");
    let port = listener.local_addr().expect("reserved port").port();
    drop(listener);

    let home = env::temp_dir().join(format!(
        "torque-tauri-binary-error-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos()
    ));
    std::fs::create_dir_all(&home).expect("create temp home");

    let output = Command::new(env!("CARGO_BIN_EXE_torque-desktop"))
        .env_clear()
        .env("HOME", &home)
        .env("PATH", env::var_os("PATH").unwrap_or_default())
        .env("TORQUE_DESKTOP_MODE", "attach")
        .env("TORQUE_DESKTOP_PORT", port.to_string())
        .output()
        .expect("run torque desktop binary");

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(1), "stderr: {stderr}");
    assert!(
        stderr.contains(&format!(
            "Error: No standalone Torque server is listening on http://127.0.0.1:{port}/ for attach mode."
        )),
        "stderr: {stderr}"
    );
    assert!(!stderr.contains("panicked"), "stderr: {stderr}");
    assert!(!stderr.contains("Failed to setup app"), "stderr: {stderr}");
    assert!(!stderr.contains("backtrace"), "stderr: {stderr}");

    let _ = std::fs::remove_dir_all(PathBuf::from(home));
}
