use std::path::Path;
use std::process::Command;

use tauri::{State, WebviewWindow};

use crate::daemon::DaemonSettings;

#[tauri::command]
pub fn reveal_log_dir(settings: State<'_, DaemonSettings>) -> Result<(), String> {
    let log_dir = settings
        .data_dir
        .join("torque.log")
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "Unable to resolve Torque log directory".to_string())?;
    open_path(&log_dir)
}

#[tauri::command]
pub fn open_external(url: String) -> Result<(), String> {
    let trimmed = url.trim();
    if !(trimmed.starts_with("https://") || trimmed.starts_with("http://")) {
        return Err("Only http(s) URLs can be opened externally".to_string());
    }
    open_target(trimmed)
}

#[tauri::command]
pub fn open_cheatsheet(window: WebviewWindow) -> Result<(), String> {
    window
        .eval("window.openCheatsheet && window.openCheatsheet();")
        .map_err(|error| error.to_string())
}

pub fn open_path(path: &Path) -> Result<(), String> {
    open_target(&path.display().to_string())
}

pub fn open_target(target: &str) -> Result<(), String> {
    let mut command = platform_open_command(target);
    command
        .spawn()
        .map_err(|error| format!("Unable to open '{target}': {error}"))?;
    Ok(())
}

fn platform_open_command(target: &str) -> Command {
    #[cfg(target_os = "macos")]
    {
        let mut command = Command::new("open");
        command.arg(target);
        command
    }
    #[cfg(target_os = "windows")]
    {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", target]);
        command
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        let mut command = Command::new("xdg-open");
        command.arg(target);
        command
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn external_url_validation_rejects_file_urls() {
        assert!(super::open_external("file:///tmp/secret".to_string()).is_err());
        assert!(super::open_external("mailto:test@example.com".to_string()).is_err());
    }
}
