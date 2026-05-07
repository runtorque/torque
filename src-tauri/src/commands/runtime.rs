use std::env;
use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::State;

use crate::daemon::DaemonSettings;

#[derive(Clone, Debug, Serialize)]
pub struct RuntimeConfig {
    pub profile: String,
    pub port: u16,
    pub data_dir: String,
    pub url: String,
    pub log_path: String,
    pub first_run_sentinel: String,
}

#[tauri::command]
pub fn runtime_config(settings: State<'_, DaemonSettings>) -> RuntimeConfig {
    runtime_config_from_settings(&settings)
}

pub fn runtime_config_from_settings(settings: &DaemonSettings) -> RuntimeConfig {
    let sentinel = first_run_sentinel_path(home_dir_from_env());
    RuntimeConfig {
        profile: settings.profile.clone(),
        port: settings.port,
        data_dir: settings.data_dir.display().to_string(),
        url: settings.url(),
        log_path: settings.data_dir.join("torque.log").display().to_string(),
        first_run_sentinel: sentinel.display().to_string(),
    }
}

pub fn home_dir_from_env() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn first_run_sentinel_path(home: impl AsRef<Path>) -> PathBuf {
    home.as_ref().join(".torque").join(".first_run_complete")
}

pub fn first_run_required_before_daemon_start(home: impl AsRef<Path>) -> bool {
    let root = home.as_ref().join(".torque");
    let sentinel = root.join(".first_run_complete");
    if sentinel.exists() {
        return false;
    }
    !root.exists()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_home(label: &str) -> PathBuf {
        env::temp_dir().join(format!(
            "torque-tauri-runtime-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos()
        ))
    }

    #[test]
    fn first_run_required_only_when_torque_root_was_absent() {
        let fresh = temp_home("fresh");
        assert!(first_run_required_before_daemon_start(&fresh));

        let existing = temp_home("existing");
        fs::create_dir_all(existing.join(".torque")).expect("create root");
        assert!(!first_run_required_before_daemon_start(&existing));

        let sentinel_home = temp_home("sentinel");
        fs::create_dir_all(sentinel_home.join(".torque")).expect("create root");
        fs::write(first_run_sentinel_path(&sentinel_home), "1").expect("write sentinel");
        assert!(!first_run_required_before_daemon_start(&sentinel_home));

        let _ = fs::remove_dir_all(fresh);
        let _ = fs::remove_dir_all(existing);
        let _ = fs::remove_dir_all(sentinel_home);
    }
}
