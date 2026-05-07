use std::env;
use std::ffi::OsString;
use std::process;
use std::sync::Arc;

use tauri::{Manager, RunEvent};
use torque_desktop::daemon::{self, DaemonSettings};

fn main() {
    let args: Vec<OsString> = env::args_os().collect();
    if args
        .get(1)
        .is_some_and(|arg| daemon::is_parent_death_guard_arg(arg))
    {
        if let Err(error) = run_daemon_guard_from_args(&args) {
            eprintln!("Error: {error}");
            process::exit(1);
        }
        return;
    }

    if let Err(error) = run_app() {
        eprintln!("Error: {error}");
        process::exit(1);
    }
}

fn run_app() -> Result<(), String> {
    let settings = DaemonSettings::from_env().map_err(|error| error.to_string())?;
    let daemon_state =
        Arc::new(daemon::ensure_server(&settings).map_err(|error| error.to_string())?);
    let _cleanup = DaemonCleanupGuard::new(daemon_state.clone());

    let setup_settings = settings.clone();
    let setup_daemon_state = daemon_state.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            app.manage(setup_daemon_state.clone());
            if let Err(error) = show_main_window(app, &setup_settings) {
                eprintln!("Error: {error}");
                daemon::stop_server(&setup_daemon_state);
                process::exit(1);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .map_err(|error| error.to_string())?;

    app.run(|app_handle, event| match event {
        RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::CloseRequested { .. },
            ..
        } if label == "main" => {
            stop_managed_daemon(app_handle);
            app_handle.exit(0);
        }
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            stop_managed_daemon(app_handle);
        }
        _ => {}
    });

    daemon::stop_server(&daemon_state);
    Ok(())
}

fn show_main_window(app: &mut tauri::App, settings: &DaemonSettings) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window was not created".to_string())?;
    if settings.port != 18933 {
        let url = settings.url().parse().map_err(|error| {
            format!(
                "Unable to parse Torque desktop URL '{}': {error}",
                settings.url()
            )
        })?;
        window.navigate(url).map_err(|error| error.to_string())?;
    }
    window.show().map_err(|error| error.to_string())?;
    Ok(())
}

fn stop_managed_daemon(app_handle: &tauri::AppHandle) {
    if let Some(state) = app_handle.try_state::<Arc<daemon::DaemonState>>() {
        daemon::stop_server(&state);
    }
}

struct DaemonCleanupGuard {
    state: Arc<daemon::DaemonState>,
}

impl DaemonCleanupGuard {
    fn new(state: Arc<daemon::DaemonState>) -> Self {
        Self { state }
    }
}

impl Drop for DaemonCleanupGuard {
    fn drop(&mut self) {
        daemon::stop_server(&self.state);
    }
}

fn run_daemon_guard_from_args(args: &[OsString]) -> Result<(), String> {
    let parent_pid = parse_guard_pid(args.get(2), "parent")?;
    let child_pid = parse_guard_pid(args.get(3), "daemon child")?;
    daemon::run_parent_death_guard(parent_pid, child_pid);
    Ok(())
}

fn parse_guard_pid(value: Option<&OsString>, label: &str) -> Result<u32, String> {
    let Some(value) = value else {
        return Err(format!("Missing {label} pid for daemon guard"));
    };
    let value = value.to_string_lossy();
    value
        .parse::<u32>()
        .ok()
        .filter(|pid| *pid > 0)
        .ok_or_else(|| format!("Invalid {label} pid '{value}' for daemon guard"))
}
