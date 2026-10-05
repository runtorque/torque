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
    let parsed = validated_external_url(&url)?;
    open_target(parsed.as_str())
}

fn validated_external_url(value: &str) -> Result<tauri::Url, String> {
    let parsed =
        tauri::Url::parse(value.trim()).map_err(|_| "External URL is invalid".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("Only absolute http(s) URLs can be opened externally".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("External URLs containing credentials are not allowed".to_string());
    }
    Ok(parsed)
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
        let mut command = Command::new("explorer.exe");
        command.arg(target);
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

    #[test]
    fn external_url_validation_rejects_credentials_and_relative_urls() {
        assert!(super::validated_external_url("https://user:secret@example.com").is_err());
        assert!(super::validated_external_url("//example.com/path").is_err());
        assert_eq!(
            super::validated_external_url("https://example.com/docs?q=1")
                .expect("valid URL")
                .as_str(),
            "https://example.com/docs?q=1"
        );
    }
}
