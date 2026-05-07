use std::env;
use std::ffi::OsString;
use std::process;
use std::sync::Arc;

use tauri::{Manager, RunEvent};
use torque_desktop::commands;
use torque_desktop::commands::window::NativeWindowState;
use torque_desktop::daemon::{self, DaemonSettings};
use torque_desktop::menu;

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
    let first_run_required = commands::runtime::first_run_required_before_daemon_start(
        commands::runtime::home_dir_from_env(),
    );
    let daemon_state =
        Arc::new(daemon::ensure_server(&settings).map_err(|error| error.to_string())?);
    let _cleanup = DaemonCleanupGuard::new(daemon_state.clone());

    let setup_settings = settings.clone();
    let setup_daemon_state = daemon_state.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .menu(|handle| menu::build_main_menu(handle))
        .invoke_handler(tauri::generate_handler![
            commands::window::detach,
            commands::window::focus_window,
            commands::window::reattach,
            commands::window::reattach_all,
            commands::window::close_window,
            commands::window::list_detached,
            commands::window::current_window_bounds,
            commands::shell::reveal_log_dir,
            commands::shell::open_external,
            commands::shell::open_cheatsheet,
            commands::dialog::confirm,
            commands::runtime::runtime_config,
        ])
        .on_menu_event(handle_menu_event)
        .setup(move |app| {
            app.manage(setup_daemon_state.clone());
            app.manage(setup_settings.clone());
            app.manage(NativeWindowState::default());
            if let Some(window_state) = app.try_state::<NativeWindowState>() {
                window_state.set_active_label("main");
            }
            if let Err(error) = show_main_window(app, &setup_settings, first_run_required) {
                eprintln!("Error: {error}");
                daemon::stop_server(&setup_daemon_state);
                process::exit(1);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .map_err(|error| error.to_string())?;

    app.run(|app_handle, event| handle_run_event(app_handle, event));

    daemon::stop_server(&daemon_state);
    Ok(())
}

fn show_main_window(
    app: &mut tauri::App,
    settings: &DaemonSettings,
    first_run_required: bool,
) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window was not created".to_string())?;
    if settings.port != 18933 || first_run_required {
        let mut target = settings.url();
        if first_run_required {
            target = format!("{}?onboarding=1", target.trim_end_matches('/'));
        }
        let url = target.parse().map_err(|error| {
            format!(
                "Unable to parse Torque desktop URL '{}': {error}",
                target
            )
        })?;
        window.navigate(url).map_err(|error| error.to_string())?;
    }
    window.show().map_err(|error| error.to_string())?;
    Ok(())
}

fn handle_run_event(app_handle: &tauri::AppHandle, event: RunEvent) {
    match event {
        RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::CloseRequested { .. },
            ..
        } if label == "main" => {
            stop_managed_daemon(app_handle);
            app_handle.exit(0);
        }
        RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::Focused(true),
            ..
        } => {
            if let Some(window_state) = app_handle.try_state::<NativeWindowState>() {
                window_state.set_active_label(label);
            }
        }
        RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_),
            ..
        } if commands::window::panel_from_label(&label).is_some() => {
            if let Some(window) = app_handle.get_webview_window(&label) {
                let _ = window.eval(
                    "window.torqueDetachedWindowBoundsChanged && window.torqueDetachedWindowBoundsChanged();",
                );
            }
        }
        RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::Destroyed,
            ..
        } if commands::window::panel_from_label(&label).is_some() => {
            if let Some(window_state) = app_handle.try_state::<NativeWindowState>() {
                if let Some(panel) = window_state.remove_detached_by_label(&label) {
                    notify_main_window_detached_closed(app_handle, &panel, &label);
                }
            }
        }
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            stop_managed_daemon(app_handle);
        }
        _ => {}
    }
}

fn handle_menu_event(app: &tauri::AppHandle, event: tauri::menu::MenuEvent) {
    let id = event.id().as_ref();
    let Some(window_state) = app.try_state::<NativeWindowState>() else {
        return;
    };
    match id {
        menu::MENU_NEW_GROUP => eval_active(app, &window_state, "window.openAddGroup && window.openAddGroup();"),
        menu::MENU_NEW_ARCHITECT => eval_active(
            app,
            &window_state,
            "window.openAddArchitectModal && window.openAddArchitectModal((window._activeGroup && window._activeGroup()) || '');",
        ),
        menu::MENU_CLOSE_WINDOW => {
            let label = window_state.active_label();
            if label != "main" && !label.is_empty() {
                let _ = commands::window::reattach_label(app, &window_state, &label);
            } else if let Some(window) = app.get_webview_window("main") {
                let _ = window.close();
            }
        }
        menu::MENU_RELOAD => eval_active(app, &window_state, "window.location.reload();"),
        menu::MENU_FORCE_RELOAD => eval_active(app, &window_state, "window.location.reload();"),
        menu::MENU_DEVTOOLS => {
            if let Some(window) = active_window(app, &window_state) {
                #[cfg(any(debug_assertions, feature = "devtools"))]
                window.open_devtools();
            }
        }
        menu::MENU_DETACH_ACTIVE_PANEL => eval_active(
            app,
            &window_state,
            "window.detachActivePanel && window.detachActivePanel();",
        ),
        menu::MENU_REATTACH_PANEL => {
            let label = window_state.active_label();
            if label != "main" && !label.is_empty() {
                let _ = commands::window::reattach_label(app, &window_state, &label);
            }
        }
        menu::MENU_REATTACH_ALL => {
            for info in window_state.detached_infos() {
                let _ = commands::window::reattach_label(app, &window_state, &info.label);
            }
        }
        menu::MENU_SHOW_LOGS => eval_active(app, &window_state, "window.openLogViewer && window.openLogViewer();"),
        menu::MENU_REVEAL_LOGS => {
            if let Some(settings) = app.try_state::<DaemonSettings>() {
                if let Some(path) = settings.data_dir.join("torque.log").parent() {
                    let _ = commands::shell::open_path(path);
                }
            }
        }
        menu::MENU_DOCUMENTATION => {
            let _ = commands::shell::open_external(
                "https://github.com/aleksanderarruda/torque".to_string(),
            );
        }
        menu::MENU_KEYBOARD_SHORTCUTS => eval_active(app, &window_state, "window.openCheatsheet && window.openCheatsheet();"),
        menu::MENU_SHOW_WELCOME => eval_active(app, &window_state, "window.openWelcome && window.openWelcome({ manual: true });"),
        _ => {}
    }
}

fn active_window(
    app: &tauri::AppHandle,
    window_state: &NativeWindowState,
) -> Option<tauri::WebviewWindow> {
    let active = window_state.active_label();
    if !active.is_empty() {
        if let Some(window) = app.get_webview_window(&active) {
            return Some(window);
        }
    }
    app.get_webview_window("main")
}

fn eval_active(app: &tauri::AppHandle, window_state: &NativeWindowState, js: &str) {
    if let Some(window) = active_window(app, window_state) {
        let _ = window.eval(js);
    }
}

fn notify_main_window_detached_closed(app: &tauri::AppHandle, panel: &str, label: &str) {
    let Some(main) = app.get_webview_window("main") else {
        return;
    };
    let js = format!(
        "window.torqueDetachedWindowClosed && window.torqueDetachedWindowClosed({panel:?}, {label:?});"
    );
    let _ = main.eval(js);
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
