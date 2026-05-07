use tauri::{Manager, RunEvent};
use torque_desktop::daemon::{self, DaemonSettings};

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let settings = DaemonSettings::from_env()?;
            let state = daemon::ensure_server(&settings)?;
            app.manage(state);

            let window = app
                .get_webview_window("main")
                .ok_or("main window was not created")?;
            if settings.port != 18933 {
                let url = settings.url().parse()?;
                window.navigate(url)?;
            }
            window.show()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Torque desktop")
        .run(|app_handle, event| match event {
            RunEvent::WindowEvent {
                label,
                event: tauri::WindowEvent::CloseRequested { .. },
                ..
            } if label == "main" => {
                if let Some(state) = app_handle.try_state::<daemon::DaemonState>() {
                    daemon::stop_server(&state);
                }
                app_handle.exit(0);
            }
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                if let Some(state) = app_handle.try_state::<daemon::DaemonState>() {
                    daemon::stop_server(&state);
                }
            }
            _ => {}
        });
}
