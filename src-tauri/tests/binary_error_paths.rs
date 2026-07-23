use std::env;
use std::fs;
use std::io::Write;
use std::net::TcpListener;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

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

#[cfg(target_os = "macos")]
#[test]
fn owned_daemon_is_stopped_when_tauri_binary_receives_terminal_signals() {
    use nix::sys::signal::{self, Signal};
    use nix::unistd::Pid;
    use std::os::unix::process::ExitStatusExt;

    for signal_to_send in [Signal::SIGTERM, Signal::SIGINT] {
        let fixture = SpawnedFakeDaemonFixture::new(signal_to_send);
        let mut parent = fixture.spawn_tauri();
        let child_pid = fixture.wait_for_fake_daemon_ready(&mut parent);

        signal::kill(Pid::from_raw(parent.id() as i32), signal_to_send)
            .expect("signal Tauri parent");

        let status = wait_for_child_exit(&mut parent, Duration::from_secs(8))
            .expect("Tauri parent should exit after signal");
        assert_eq!(status.signal(), Some(signal_to_send as i32));

        assert!(
            wait_until(Duration::from_secs(8), || !pid_is_alive(child_pid)
                && !port_is_open(fixture.port)),
            "daemon child pid {child_pid} stayed alive or port {} stayed open after {signal_to_send:?}",
            fixture.port
        );
    }
}

#[cfg(target_os = "macos")]
struct SpawnedFakeDaemonFixture {
    root: PathBuf,
    data_dir: PathBuf,
    child_pid_file: PathBuf,
    port: u16,
}

#[cfg(target_os = "macos")]
impl SpawnedFakeDaemonFixture {
    fn new(signal_to_send: nix::sys::signal::Signal) -> Self {
        let root = env::temp_dir().join(format!(
            "torque-tauri-signal-{:?}-{}-{}",
            signal_to_send,
            std::process::id(),
            unique_suffix()
        ));
        let data_dir = root.join("data");
        let child_pid_file = root.join("child.pid");
        fs::create_dir_all(&data_dir).expect("create fake daemon temp dirs");
        let port = reserve_free_port();
        write_fake_daemon_script(&root, &data_dir, &child_pid_file, port);
        Self {
            root,
            data_dir,
            child_pid_file,
            port,
        }
    }

    fn spawn_tauri(&self) -> Child {
        Command::new(env!("CARGO_BIN_EXE_torque-desktop"))
            .env_clear()
            .env("HOME", &self.root)
            .env("PATH", env::var_os("PATH").unwrap_or_default())
            .env("TORQUE_REPO_ROOT", &self.root)
            .env("TORQUE_PYTHON_EXECUTABLE", python_executable())
            .env("TORQUE_DESKTOP_MODE", "spawn")
            .env("TORQUE_DESKTOP_PORT", self.port.to_string())
            .env("TORQUE_DESKTOP_PROFILE", "sigtest")
            .env("TORQUE_DESKTOP_DATA_DIR", &self.data_dir)
            .env("TORQUE_PORT", self.port.to_string())
            .env("TORQUE_PROFILE", "sigtest")
            .env("TORQUE_DATA_DIR", &self.data_dir)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn Tauri binary")
    }

    fn wait_for_fake_daemon_ready(&self, parent: &mut Child) -> u32 {
        let deadline = Instant::now() + Duration::from_secs(15);
        while Instant::now() < deadline {
            if let Some(status) = parent.try_wait().expect("poll Tauri parent") {
                panic!("Tauri parent exited before fake daemon was ready: {status}");
            }
            if let Ok(raw_pid) = fs::read_to_string(&self.child_pid_file) {
                let child_pid = raw_pid.trim().parse::<u32>().expect("parse child pid");
                if port_is_open(self.port) {
                    return child_pid;
                }
            }
            thread::sleep(Duration::from_millis(100));
        }
        panic!("fake daemon did not become ready on port {}", self.port);
    }
}

#[cfg(target_os = "macos")]
impl Drop for SpawnedFakeDaemonFixture {
    fn drop(&mut self) {
        if let Ok(raw_pid) = fs::read_to_string(&self.child_pid_file) {
            if let Ok(pid) = raw_pid.trim().parse::<u32>() {
                terminate_pid(pid);
            }
        }
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[cfg(target_os = "macos")]
fn write_fake_daemon_script(
    root: &std::path::Path,
    data_dir: &std::path::Path,
    child_pid_file: &std::path::Path,
    port: u16,
) {
    let script = root.join("torque.py");
    let body = format!(
        r#"
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHILD_PID_FILE = {child_pid_file:?}
PORT = {port}
RUNTIME = {{
    "ok": True,
    "data": {{
        "runtime": {{
            "standalone": True,
            "profile": "sigtest",
            "data_dir": {data_dir:?},
            "port": {port},
        }}
    }},
}}

with open(CHILD_PID_FILE, "w") as handle:
    handle.write(str(os.getpid()))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps(RUNTIME).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        body = json.dumps(RUNTIME).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
"#,
        child_pid_file = child_pid_file.display().to_string(),
        data_dir = data_dir.display().to_string(),
        port = port
    );
    let mut file = fs::File::create(script).expect("create fake torque.py");
    file.write_all(body.as_bytes())
        .expect("write fake torque.py");
}

#[cfg(target_os = "macos")]
fn python_executable() -> String {
    env::var("PYTHON").unwrap_or_else(|_| "python3".to_string())
}

#[cfg(target_os = "macos")]
fn reserve_free_port() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").expect("reserve free port");
    listener.local_addr().expect("reserved port").port()
}

#[cfg(target_os = "macos")]
fn port_is_open(port: u16) -> bool {
    let address = format!("127.0.0.1:{port}");
    TcpStream::connect_timeout(
        &address.parse().expect("parse localhost socket"),
        Duration::from_millis(200),
    )
    .is_ok()
}

#[cfg(target_os = "macos")]
fn wait_for_child_exit(child: &mut Child, timeout: Duration) -> Option<std::process::ExitStatus> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Some(status) = child.try_wait().expect("poll child") {
            return Some(status);
        }
        thread::sleep(Duration::from_millis(50));
    }
    None
}

#[cfg(target_os = "macos")]
fn wait_until(timeout: Duration, mut predicate: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if predicate() {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

#[cfg(target_os = "macos")]
fn pid_is_alive(pid: u32) -> bool {
    use nix::errno::Errno;
    use nix::sys::signal;
    use nix::unistd::Pid;

    match signal::kill(Pid::from_raw(pid as i32), None) {
        Ok(()) | Err(Errno::EPERM) => true,
        Err(Errno::ESRCH) => false,
        Err(_) => false,
    }
}

#[cfg(target_os = "macos")]
fn terminate_pid(pid: u32) {
    use nix::sys::signal::{self, Signal};
    use nix::unistd::Pid;

    let pid = Pid::from_raw(pid as i32);
    let _ = signal::kill(pid, Signal::SIGTERM);
    if wait_until(
        Duration::from_secs(2),
        || !pid_is_alive(pid.as_raw() as u32),
    ) {
        return;
    }
    let _ = signal::kill(pid, Signal::SIGKILL);
    let _ = wait_until(
        Duration::from_secs(2),
        || !pid_is_alive(pid.as_raw() as u32),
    );
}

fn unique_suffix() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system time")
        .as_nanos()
}
