use serde::Deserialize;
use std::env;
use std::error::Error;
use std::fmt;
use std::fs;
use std::io::Read;
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

const DESKTOP_DEFAULT_PROFILE: &str = "desktop";
const DESKTOP_DEFAULT_PORT: u16 = 18933;
const DESKTOP_MODE_SPAWN: &str = "spawn";
const DESKTOP_MODE_ATTACH: &str = "attach";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const PROBE_TIMEOUT: Duration = Duration::from_millis(750);
const POLL_INTERVAL: Duration = Duration::from_millis(250);
const STOP_TIMEOUT: Duration = Duration::from_secs(5);
const DAEMON_GUARD_ARG: &str = "--torque-daemon-guard";
const GUARD_POLL_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DaemonMode {
    Spawn,
    Attach,
}

impl DaemonMode {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Spawn => DESKTOP_MODE_SPAWN,
            Self::Attach => DESKTOP_MODE_ATTACH,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DaemonSettings {
    pub mode: DaemonMode,
    pub port: u16,
    pub profile: String,
    pub data_dir: PathBuf,
    pub script_path: PathBuf,
    pub python_executable: PathBuf,
}

impl DaemonSettings {
    pub fn from_env() -> Result<Self, EnsureError> {
        Self::from_env_map(env::vars())
    }

    pub fn from_env_map<I, K, V>(vars: I) -> Result<Self, EnsureError>
    where
        I: IntoIterator<Item = (K, V)>,
        K: Into<String>,
        V: Into<String>,
    {
        let env_map: std::collections::HashMap<String, String> = vars
            .into_iter()
            .map(|(key, value)| (key.into(), value.into()))
            .collect();

        let mode_text = env_map
            .get("TORQUE_DESKTOP_MODE")
            .filter(|value| !value.trim().is_empty())
            .cloned()
            .unwrap_or_else(|| {
                if truthy(env_map.get("TORQUE_DESKTOP_ATTACH")) {
                    DESKTOP_MODE_ATTACH.to_string()
                } else {
                    DESKTOP_MODE_SPAWN.to_string()
                }
            });
        let mode = normalize_launch_mode(&mode_text)?;

        let profile = env_map
            .get("TORQUE_DESKTOP_PROFILE")
            .or_else(|| env_map.get("TORQUE_PROFILE"))
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| DESKTOP_DEFAULT_PROFILE.to_string());

        let port_text = env_map
            .get("TORQUE_DESKTOP_PORT")
            .or_else(|| env_map.get("TORQUE_PORT"))
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());
        let port = match port_text {
            Some(value) => value.parse::<u16>().map_err(|_| {
                EnsureError::Config(format!(
                    "Invalid desktop port '{}'. Expected a TCP port between 0 and 65535.",
                    value
                ))
            })?,
            None => DESKTOP_DEFAULT_PORT,
        };

        let home = env_map
            .get("HOME")
            .map(PathBuf::from)
            .or_else(|| env::var_os("HOME").map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("."));
        let data_dir = env_map
            .get("TORQUE_DESKTOP_DATA_DIR")
            .or_else(|| env_map.get("TORQUE_DATA_DIR"))
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .map(|value| expand_home_with(value, &home))
            .unwrap_or_else(|| {
                home.join(".torque")
                    .join("profiles")
                    .join(slugify_profile(&profile))
            });

        let repo_root = env_map
            .get("TORQUE_REPO_ROOT")
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .map(expand_home)
            .unwrap_or_else(default_repo_root);
        let python_executable = env_map
            .get("TORQUE_PYTHON_EXECUTABLE")
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .map(expand_home)
            .unwrap_or_else(|| find_on_path("python3").unwrap_or_else(|| PathBuf::from("python3")));

        Ok(Self {
            mode,
            port,
            profile,
            data_dir,
            script_path: repo_root.join("torque.py"),
            python_executable,
        })
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }

    fn server_env(&self) -> std::collections::HashMap<String, String> {
        let mut env_map: std::collections::HashMap<String, String> = env::vars().collect();
        env_map.insert("TORQUE_STANDALONE".into(), "1".into());
        env_map.insert("TORQUE_PROFILE".into(), self.profile.clone());
        env_map.insert("TORQUE_PORT".into(), self.port.to_string());
        env_map.insert(
            "TORQUE_DATA_DIR".into(),
            self.data_dir.display().to_string(),
        );
        env_map.insert("TORQUE_DESKTOP_PROFILE".into(), self.profile.clone());
        env_map.insert("TORQUE_DESKTOP_PORT".into(), self.port.to_string());
        env_map.insert(
            "TORQUE_DESKTOP_DATA_DIR".into(),
            self.data_dir.display().to_string(),
        );
        env_map.insert("TORQUE_DESKTOP_MODE".into(), self.mode.as_str().into());
        env_map.insert(
            "TORQUE_DESKTOP_ATTACH".into(),
            if self.mode == DaemonMode::Attach {
                "1"
            } else {
                "0"
            }
            .into(),
        );
        env_map
    }
}

#[derive(Debug)]
pub struct DaemonState {
    child: Mutex<Option<Child>>,
    parent_death_guard: Mutex<Option<Child>>,
    attached: AtomicBool,
}

impl DaemonState {
    fn attached() -> Self {
        Self {
            child: Mutex::new(None),
            parent_death_guard: Mutex::new(None),
            attached: AtomicBool::new(true),
        }
    }

    fn spawned(child: Child, parent_death_guard: Option<Child>) -> Self {
        Self {
            child: Mutex::new(Some(child)),
            parent_death_guard: Mutex::new(parent_death_guard),
            attached: AtomicBool::new(false),
        }
    }

    pub fn is_attached(&self) -> bool {
        self.attached.load(Ordering::SeqCst)
    }

    pub fn owned_child_id(&self) -> Option<u32> {
        if self.is_attached() {
            return None;
        }
        self.child
            .lock()
            .expect("daemon child lock poisoned")
            .as_ref()
            .map(Child::id)
    }

    #[cfg(test)]
    fn has_child(&self) -> bool {
        self.child
            .lock()
            .expect("daemon child lock poisoned")
            .is_some()
    }
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq)]
pub struct RuntimePayload {
    #[serde(default)]
    pub standalone: bool,
    #[serde(default)]
    pub profile: Option<String>,
    #[serde(default)]
    pub data_dir: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
}

#[derive(Debug)]
pub enum EnsureError {
    Config(String),
    Runtime(String),
    Io(std::io::Error),
}

impl fmt::Display for EnsureError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Config(message) | Self::Runtime(message) => formatter.write_str(message),
            Self::Io(error) => write!(formatter, "{}", error),
        }
    }
}

impl Error for EnsureError {}

impl From<std::io::Error> for EnsureError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Deserialize)]
struct ApiResponse {
    ok: bool,
    data: Option<ApiData>,
}

#[derive(Deserialize)]
struct ApiData {
    runtime: Option<RuntimePayload>,
}

pub fn probe_runtime(port: u16, timeout: Duration) -> Option<RuntimePayload> {
    let url = format!("http://127.0.0.1:{port}/api/cmd");
    let agent = ureq::AgentBuilder::new().timeout(timeout).build();
    let response = agent
        .post(&url)
        .set("Content-Type", "application/json")
        .send_string(r#"{"cmd":"get_config"}"#)
        .ok()?;
    let mut raw = String::new();
    response.into_reader().read_to_string(&mut raw).ok()?;
    let payload: ApiResponse = serde_json::from_str(&raw).ok()?;
    if !payload.ok {
        return None;
    }
    payload.data.and_then(|data| data.runtime)
}

pub fn ensure_server(settings: &DaemonSettings) -> Result<DaemonState, EnsureError> {
    if let Some(runtime) = probe_runtime(settings.port, PROBE_TIMEOUT) {
        if !runtime.standalone {
            return Err(EnsureError::Runtime(format!(
                "Port {} is already serving an iTerm2-hosted Torque instance. The desktop shell uses its own standalone profile and port by default so it does not attach to the live toolbelt daemon.",
                settings.port
            )));
        }

        let (matches_target, mismatches) = runtime_matches_target(&runtime, settings);
        if settings.mode == DaemonMode::Attach {
            if !matches_target {
                let details = if mismatches.is_empty() {
                    describe_runtime(&runtime)
                } else {
                    mismatches.join("; ")
                };
                return Err(EnsureError::Runtime(format!(
                    "Refusing to attach the desktop shell to an existing standalone Torque because {details}. Choose matching TORQUE_PROFILE/TORQUE_DATA_DIR/TORQUE_PORT values or launch a fresh desktop-owned server instead."
                )));
            }
            return Ok(DaemonState::attached());
        }

        if matches_target {
            return Err(EnsureError::Runtime(format!(
                "A matching standalone Torque server is already listening on {}. Reuse it with TORQUE_DESKTOP_MODE=attach (or torque desktop --attach) instead of starting another desktop-owned server.",
                settings.url()
            )));
        }

        return Err(EnsureError::Runtime(format!(
            "A different standalone Torque server is already listening on {} ({}). Choose a different TORQUE_PORT or attach only to the matching standalone runtime you intend to reuse.",
            settings.url(),
            describe_runtime(&runtime)
        )));
    }

    if settings.mode == DaemonMode::Attach {
        return Err(EnsureError::Runtime(format!(
            "No standalone Torque server is listening on {} for attach mode. Launch without --attach (or TORQUE_DESKTOP_MODE=spawn) to start a desktop-owned server.",
            settings.url()
        )));
    }

    if is_port_open(settings.port) {
        return Err(EnsureError::Runtime(format!(
            "Port {} is already in use by another process. Set TORQUE_PORT to a free port before launching the desktop shell.",
            settings.port
        )));
    }

    fs::create_dir_all(&settings.data_dir)?;
    let mut child = Command::new(&settings.python_executable)
        .arg(&settings.script_path)
        .current_dir(
            settings
                .script_path
                .parent()
                .unwrap_or_else(|| Path::new(".")),
        )
        .envs(settings.server_env())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let mut parent_death_guard = match spawn_parent_death_guard(child.id()) {
        Ok(guard) => guard,
        Err(error) => {
            terminate_child(&mut child);
            return Err(error);
        }
    };

    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if let Some(runtime) = probe_runtime(settings.port, PROBE_TIMEOUT) {
            if !runtime.standalone {
                terminate_child(&mut child);
                terminate_guard(&mut parent_death_guard);
                return Err(EnsureError::Runtime(format!(
                    "Port {} became reachable, but it is not serving standalone Torque. Refusing to attach the desktop shell to a non-standalone server.",
                    settings.port
                )));
            }
            return Ok(DaemonState::spawned(child, parent_death_guard));
        }
        if child.try_wait()?.is_some() {
            terminate_guard(&mut parent_death_guard);
            return Err(EnsureError::Runtime(format!(
                "The standalone Torque server exited before it was ready. Check {} for details.",
                settings.data_dir.join("torque.log").display()
            )));
        }
        thread::sleep(POLL_INTERVAL);
    }

    terminate_child(&mut child);
    terminate_guard(&mut parent_death_guard);
    Err(EnsureError::Runtime(format!(
        "Timed out waiting for the standalone Torque server to start. Check {} for details.",
        settings.data_dir.join("torque.log").display()
    )))
}

pub fn stop_server(state: &DaemonState) {
    if state.is_attached() {
        return;
    }
    let mut guard = match state.child.lock() {
        Ok(guard) => guard,
        Err(_) => return,
    };
    if let Some(mut child) = guard.take() {
        terminate_child(&mut child);
    }
    drop(guard);
    if let Ok(mut parent_death_guard) = state.parent_death_guard.lock() {
        terminate_guard(&mut parent_death_guard);
    }
}

pub fn is_parent_death_guard_arg(value: &std::ffi::OsStr) -> bool {
    value == std::ffi::OsStr::new(DAEMON_GUARD_ARG)
}

pub fn run_parent_death_guard(parent_pid: u32, child_pid: u32) {
    loop {
        if !pid_is_alive(child_pid) {
            return;
        }
        if parent_is_gone(parent_pid) {
            terminate_pid(child_pid);
            return;
        }
        thread::sleep(GUARD_POLL_INTERVAL);
    }
}

#[cfg(test)]
fn spawn_parent_death_guard(_child_pid: u32) -> Result<Option<Child>, EnsureError> {
    Ok(None)
}

#[cfg(not(test))]
fn spawn_parent_death_guard(child_pid: u32) -> Result<Option<Child>, EnsureError> {
    let executable = env::current_exe().map_err(|error| {
        EnsureError::Runtime(format!(
            "Unable to resolve Torque desktop executable for daemon guard: {error}"
        ))
    })?;
    let guard = Command::new(executable)
        .arg(DAEMON_GUARD_ARG)
        .arg(std::process::id().to_string())
        .arg(child_pid.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| {
            EnsureError::Runtime(format!(
                "Unable to start Torque desktop daemon guard: {error}"
            ))
        })?;
    Ok(Some(guard))
}

fn terminate_guard(guard: &mut Option<Child>) {
    let Some(mut guard) = guard.take() else {
        return;
    };
    if guard.try_wait().ok().flatten().is_some() {
        return;
    }
    let _ = guard.kill();
    let _ = guard.wait();
}

fn terminate_child(child: &mut Child) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }

    #[cfg(unix)]
    {
        let _ = nix::sys::signal::kill(
            nix::unistd::Pid::from_raw(child.id() as i32),
            nix::sys::signal::Signal::SIGTERM,
        );
    }
    #[cfg(not(unix))]
    {
        let _ = child.kill();
    }

    if wait_for_exit(child, STOP_TIMEOUT) {
        return;
    }
    let _ = child.kill();
    let _ = wait_for_exit(child, STOP_TIMEOUT);
}

fn wait_for_exit(child: &mut Child, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return true;
        }
        thread::sleep(Duration::from_millis(50));
    }
    false
}

#[cfg(unix)]
fn parent_is_gone(parent_pid: u32) -> bool {
    nix::unistd::getppid().as_raw() as u32 != parent_pid || !pid_is_alive(parent_pid)
}

#[cfg(not(unix))]
fn parent_is_gone(parent_pid: u32) -> bool {
    !pid_is_alive(parent_pid)
}

#[cfg(unix)]
fn pid_is_alive(pid: u32) -> bool {
    match nix::sys::signal::kill(nix::unistd::Pid::from_raw(pid as i32), None) {
        Ok(()) | Err(nix::errno::Errno::EPERM) => true,
        Err(nix::errno::Errno::ESRCH) => false,
        Err(_) => false,
    }
}

#[cfg(not(unix))]
fn pid_is_alive(_pid: u32) -> bool {
    true
}

#[cfg(unix)]
fn terminate_pid(pid: u32) {
    let raw_pid = nix::unistd::Pid::from_raw(pid as i32);
    let _ = nix::sys::signal::kill(raw_pid, nix::sys::signal::Signal::SIGTERM);
    if wait_for_pid_exit(pid, STOP_TIMEOUT) {
        return;
    }
    let _ = nix::sys::signal::kill(raw_pid, nix::sys::signal::Signal::SIGKILL);
    let _ = wait_for_pid_exit(pid, STOP_TIMEOUT);
}

#[cfg(not(unix))]
fn terminate_pid(_pid: u32) {}

fn wait_for_pid_exit(pid: u32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !pid_is_alive(pid) {
            return true;
        }
        thread::sleep(Duration::from_millis(50));
    }
    false
}

fn runtime_matches_target(
    runtime: &RuntimePayload,
    settings: &DaemonSettings,
) -> (bool, Vec<String>) {
    let mut mismatches = Vec::new();
    let mut saw_identity = false;

    if let Some(runtime_profile) = runtime
        .profile
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        saw_identity = true;
        if runtime_profile != settings.profile {
            mismatches.push(format!(
                "profile is '{}' instead of '{}'",
                runtime_profile, settings.profile
            ));
        }
    }

    if let Some(runtime_data_dir) = runtime
        .data_dir
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        saw_identity = true;
        let actual = comparable_path(Path::new(runtime_data_dir));
        let expected = comparable_path(&settings.data_dir);
        if actual != expected {
            mismatches.push(format!(
                "data dir is '{}' instead of '{}'",
                actual.display(),
                expected.display()
            ));
        }
    }

    if !saw_identity {
        mismatches.push("the runtime did not advertise a profile or data dir".to_string());
    }

    (mismatches.is_empty(), mismatches)
}

fn describe_runtime(runtime: &RuntimePayload) -> String {
    let mut details = Vec::new();
    if let Some(profile) = runtime
        .profile
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        details.push(format!("profile={profile}"));
    }
    if let Some(data_dir) = runtime
        .data_dir
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        details.push(format!("data_dir={data_dir}"));
    }
    if details.is_empty() {
        "no runtime metadata".to_string()
    } else {
        details.join(", ")
    }
}

fn is_port_open(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

fn normalize_launch_mode(value: &str) -> Result<DaemonMode, EnsureError> {
    let mode = value.trim().to_ascii_lowercase();
    if mode.is_empty() || mode == DESKTOP_MODE_SPAWN {
        return Ok(DaemonMode::Spawn);
    }
    if mode == DESKTOP_MODE_ATTACH {
        return Ok(DaemonMode::Attach);
    }
    Err(EnsureError::Config(format!(
        "Unsupported desktop launch mode '{}'. Expected 'spawn' or 'attach'.",
        value
    )))
}

fn truthy(value: Option<&String>) -> bool {
    value
        .map(|text| {
            matches!(
                text.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

fn slugify_profile(name: &str) -> String {
    let mut output = String::new();
    let mut previous_dash = false;
    for character in name.trim().to_ascii_lowercase().chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-') {
            output.push(character);
            previous_dash = false;
        } else if !previous_dash {
            output.push('-');
            previous_dash = true;
        }
    }
    let trimmed = output.trim_matches(&['.', '_', '-'][..]).to_string();
    if trimmed.is_empty() {
        "default".to_string()
    } else {
        trimmed
    }
}

fn expand_home(value: String) -> PathBuf {
    let home = env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("~"));
    expand_home_with(value, &home)
}

fn expand_home_with(value: String, home: &Path) -> PathBuf {
    if value == "~" {
        return home.to_path_buf();
    }
    if let Some(suffix) = value.strip_prefix("~/") {
        return home.join(suffix);
    }
    PathBuf::from(value)
}

fn default_repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from(".."))
}

fn find_on_path(binary: &str) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .map(|dir| dir.join(binary))
        .find(|candidate| candidate.is_file())
}

fn comparable_path(path: &Path) -> PathBuf {
    let expanded = expand_home(path.display().to_string());
    match expanded.canonicalize() {
        Ok(path) => path,
        Err(_) if expanded.is_absolute() => expanded,
        Err(_) => env::current_dir()
            .map(|cwd| cwd.join(&expanded))
            .unwrap_or(expanded),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;

    fn temp_path(name: &str) -> PathBuf {
        env::temp_dir().join(format!(
            "torque-tauri-{name}-{}-{}",
            std::process::id(),
            Instant::now().elapsed().as_nanos()
        ))
    }

    fn settings_for(
        port: u16,
        mode: DaemonMode,
        profile: &str,
        data_dir: PathBuf,
    ) -> DaemonSettings {
        DaemonSettings {
            mode,
            port,
            profile: profile.to_string(),
            data_dir,
            script_path: PathBuf::from("/tmp/torque.py"),
            python_executable: PathBuf::from("python3"),
        }
    }

    fn spawn_runtime_server(body: String) -> (u16, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let port = listener.local_addr().expect("test server addr").port();
        let handle = thread::spawn(move || {
            listener
                .set_nonblocking(true)
                .expect("set mock server nonblocking");
            let deadline = Instant::now() + Duration::from_millis(750);
            let mut served = 0;
            while served < 4 && Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        {
                            let mut reader = BufReader::new(&mut stream);
                            let mut line = String::new();
                            while reader.read_line(&mut line).unwrap_or(0) > 0 {
                                if line == "\r\n" {
                                    break;
                                }
                                line.clear();
                            }
                        }
                        let response = format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                            body.as_bytes().len(), body
                        );
                        let _ = stream.write_all(response.as_bytes());
                        let _ = stream.flush();
                        served += 1;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        (port, handle)
    }

    fn runtime_body(standalone: bool, profile: &str, data_dir: &Path) -> String {
        serde_json::json!({
            "ok": true,
            "data": {
                "runtime": {
                    "standalone": standalone,
                    "profile": profile,
                    "data_dir": data_dir.display().to_string()
                }
            }
        })
        .to_string()
    }

    #[test]
    fn settings_from_env_map_resolves_desktop_overrides() {
        let settings = DaemonSettings::from_env_map([
            ("TORQUE_DESKTOP_MODE", "attach"),
            ("TORQUE_DESKTOP_PROFILE", "Desk Top"),
            ("TORQUE_DESKTOP_PORT", "19001"),
            ("TORQUE_DESKTOP_DATA_DIR", "~/torque desktop"),
            ("TORQUE_REPO_ROOT", "/repo/root"),
            ("TORQUE_PYTHON_EXECUTABLE", "/custom/python3"),
            ("HOME", "/home/tester"),
        ])
        .expect("settings should resolve");

        assert_eq!(settings.mode, DaemonMode::Attach);
        assert_eq!(settings.profile, "Desk Top");
        assert_eq!(settings.port, 19001);
        assert_eq!(
            settings.data_dir,
            PathBuf::from("/home/tester/torque desktop")
        );
        assert_eq!(settings.script_path, PathBuf::from("/repo/root/torque.py"));
        assert_eq!(settings.python_executable, PathBuf::from("/custom/python3"));
    }

    #[test]
    fn settings_from_env_map_uses_profile_slug_defaults() {
        let settings = DaemonSettings::from_env_map([
            ("TORQUE_PROFILE", "Feature Profile!"),
            ("HOME", "/home/tester"),
            ("TORQUE_REPO_ROOT", "/repo/root"),
        ])
        .expect("settings should resolve");

        assert_eq!(settings.mode, DaemonMode::Spawn);
        assert_eq!(settings.port, DESKTOP_DEFAULT_PORT);
        assert_eq!(
            settings.data_dir,
            PathBuf::from("/home/tester/.torque/profiles/feature-profile")
        );
    }

    #[test]
    fn probe_runtime_happy_path_returns_runtime_payload() {
        let data_dir = temp_path("probe-happy");
        let (port, handle) = spawn_runtime_server(runtime_body(true, "desktop", &data_dir));

        let runtime = probe_runtime(port, Duration::from_secs(1)).expect("runtime payload");

        assert!(runtime.standalone);
        assert_eq!(runtime.profile.as_deref(), Some("desktop"));
        assert_eq!(
            runtime.data_dir.as_deref(),
            Some(data_dir.to_str().unwrap())
        );
        handle.join().expect("mock server should finish");
    }

    #[test]
    fn probe_runtime_timeout_returns_none() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind slow server");
        let port = listener.local_addr().expect("slow server addr").port();
        let handle = thread::spawn(move || {
            if let Ok((_stream, _)) = listener.accept() {
                thread::sleep(Duration::from_millis(250));
            }
        });

        assert_eq!(probe_runtime(port, Duration::from_millis(25)), None);
        handle.join().expect("slow server should finish");
    }

    #[test]
    fn ensure_server_refuses_attach_when_no_daemon_is_listening() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("reserve port");
        let port = listener.local_addr().expect("reserved addr").port();
        drop(listener);
        let settings = settings_for(
            port,
            DaemonMode::Attach,
            "desktop",
            temp_path("attach-none"),
        );

        let error = ensure_server(&settings).expect_err("attach mode should refuse missing daemon");

        assert_eq!(
            error.to_string(),
            format!(
                "No standalone Torque server is listening on http://127.0.0.1:{port}/ for attach mode. Launch without --attach (or TORQUE_DESKTOP_MODE=spawn) to start a desktop-owned server."
            )
        );
    }

    #[test]
    fn ensure_server_refuses_attach_to_mismatched_profile() {
        let data_dir = temp_path("attach-mismatch");
        let (port, handle) = spawn_runtime_server(runtime_body(true, "other", &data_dir));
        let settings = settings_for(port, DaemonMode::Attach, "desktop", data_dir);

        let error = ensure_server(&settings).expect_err("mismatch should be refused");

        assert_eq!(
            error.to_string(),
            "Refusing to attach the desktop shell to an existing standalone Torque because profile is 'other' instead of 'desktop'. Choose matching TORQUE_PROFILE/TORQUE_DATA_DIR/TORQUE_PORT values or launch a fresh desktop-owned server instead."
        );
        handle.join().expect("mock server should finish");
    }

    #[test]
    fn ensure_server_attaches_to_matching_runtime() {
        let data_dir = temp_path("attach-match");
        let (port, handle) = spawn_runtime_server(runtime_body(true, "desktop", &data_dir));
        let settings = settings_for(port, DaemonMode::Attach, "desktop", data_dir);

        let state = ensure_server(&settings).expect("matching attach should work");

        assert!(state.is_attached());
        assert!(!state.has_child());
        handle.join().expect("mock server should finish");
    }

    #[test]
    fn ensure_server_refuses_spawn_when_matching_runtime_already_listens() {
        let data_dir = temp_path("spawn-match");
        let (port, handle) = spawn_runtime_server(runtime_body(true, "desktop", &data_dir));
        let settings = settings_for(port, DaemonMode::Spawn, "desktop", data_dir);

        let error = ensure_server(&settings).expect_err("duplicate spawn should be refused");

        assert_eq!(
            error.to_string(),
            format!(
                "A matching standalone Torque server is already listening on http://127.0.0.1:{port}/. Reuse it with TORQUE_DESKTOP_MODE=attach (or torque desktop --attach) instead of starting another desktop-owned server."
            )
        );
        handle.join().expect("mock server should finish");
    }

    #[test]
    fn ensure_server_refuses_spawn_when_port_is_occupied_by_non_torque_process() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind occupied port");
        let port = listener.local_addr().expect("occupied addr").port();
        let settings = settings_for(port, DaemonMode::Spawn, "desktop", temp_path("occupied"));

        let error = ensure_server(&settings).expect_err("occupied port should be refused");

        assert_eq!(
            error.to_string(),
            format!(
                "Port {port} is already in use by another process. Set TORQUE_PORT to a free port before launching the desktop shell."
            )
        );
        drop(listener);
    }

    #[test]
    fn ensure_server_refuses_iterm2_hosted_runtime() {
        let data_dir = temp_path("iterm2-runtime");
        let (port, handle) = spawn_runtime_server(runtime_body(false, "desktop", &data_dir));
        let settings = settings_for(port, DaemonMode::Spawn, "desktop", data_dir);

        let error = ensure_server(&settings).expect_err("iTerm2 runtime should be refused");

        assert_eq!(
            error.to_string(),
            format!(
                "Port {port} is already serving an iTerm2-hosted Torque instance. The desktop shell uses its own standalone profile and port by default so it does not attach to the live toolbelt daemon."
            )
        );
        handle.join().expect("mock server should finish");
    }

    #[test]
    fn ensure_server_spawns_and_stop_server_terminates_owned_daemon() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("reserve spawn port");
        let port = listener.local_addr().expect("spawn addr").port();
        drop(listener);

        let data_dir = temp_path("spawn-success-data");
        let script_path = temp_path("spawn-success-server.py");
        fs::write(
            &script_path,
            r#"
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length') or '0')
        self.rfile.read(length)
        body = json.dumps({
            'ok': True,
            'data': {
                'runtime': {
                    'standalone': True,
                    'profile': os.environ['TORQUE_PROFILE'],
                    'data_dir': os.environ['TORQUE_DATA_DIR'],
                    'port': int(os.environ['TORQUE_PORT']),
                }
            }
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

ThreadingHTTPServer(('127.0.0.1', int(os.environ['TORQUE_PORT'])), Handler).serve_forever()
"#,
        )
        .expect("write fake daemon script");
        let python = env::var_os("PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("python3"));
        let settings = DaemonSettings {
            mode: DaemonMode::Spawn,
            port,
            profile: "desktop".to_string(),
            data_dir,
            script_path: script_path.clone(),
            python_executable: python,
        };

        let state = ensure_server(&settings).expect("fake daemon should become ready");
        assert!(!state.is_attached());
        assert!(state.has_child());

        stop_server(&state);
        assert!(!state.has_child());
        assert_eq!(probe_runtime(port, Duration::from_millis(100)), None);
        let _ = fs::remove_file(script_path);
    }
}
