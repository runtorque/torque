//! Opt-in real-window smoke test. Runs an isolated shell with no daemon or UI.
//! cargo run --offline --manifest-path src-tauri/Cargo.toml --example window_restore_qa
//! Add -- --verify-failure-exit to verify that a failed check exits with code 1.
use std::sync::{
    atomic::{AtomicI32, Ordering},
    Arc,
};
use std::time::Duration;
use tauri::{RunEvent, WebviewUrl, WebviewWindowBuilder};
use torque_desktop::commands::window::{restore_window_bounds, window_bounds, WindowBounds};
use torque_desktop::window_geometry::WindowGeometryPolicy;

fn check(app: &tauri::AppHandle) -> Result<(), String> {
    if std::env::args().any(|arg| arg == "--verify-failure-exit") {
        return Err("Intentional failure to verify the QA process exit status".into());
    }
    let mut monitors = app
        .available_monitors()
        .map_err(|error| error.to_string())?;
    let primary = app
        .primary_monitor()
        .map_err(|error| error.to_string())?
        .ok_or("Native QA requires a connected display")?;
    println!(
        "enumerated_monitors={}",
        serde_json::to_string(&monitors).unwrap()
    );
    if !monitors
        .iter()
        .any(|monitor| monitor.position() == primary.position())
    {
        monitors.insert(0, primary.clone());
    }
    println!(
        "verified_monitors={}",
        serde_json::to_string(&monitors).unwrap()
    );
    let area = primary.work_area();
    let scale = primary.scale_factor();
    let mut retained = None;
    for (name, policy) in [
        ("main", WindowGeometryPolicy::MAIN),
        ("detached", WindowGeometryPolicy::DETACHED),
    ] {
        for scenario in ["visible", "legacy", "offscreen", "oversized", "reopen"] {
            let mut bounds = match scenario {
                "offscreen" => WindowBounds {
                    physical: Some(true),
                    x: Some(-50000.0),
                    y: Some(-50000.0),
                    width: Some(1000.0 * scale),
                    height: Some(650.0 * scale),
                    display_id: Some("Disconnected QA display".into()),
                },
                "oversized" => WindowBounds {
                    physical: Some(true),
                    x: Some(area.position.x as f64),
                    y: Some(area.position.y as f64 - 100.0),
                    width: Some(50000.0),
                    height: Some(50000.0),
                    display_id: primary.name().cloned(),
                },
                "reopen" => retained.clone().ok_or("No retained capture")?,
                _ => WindowBounds {
                    physical: Some(true),
                    x: Some(area.position.x as f64 + 40.0),
                    y: Some(area.position.y as f64 + 40.0),
                    width: Some(1000.0 * scale),
                    height: Some(650.0 * scale),
                    display_id: primary.name().cloned(),
                },
            };
            if scenario == "legacy" {
                bounds.physical = None;
                if name == "main" {
                    bounds.display_id = None;
                }
            }
            let label = format!("geometry-qa-{name}-{scenario}");
            let window = WebviewWindowBuilder::new(
                app,
                &label,
                WebviewUrl::External("about:blank".parse().unwrap()),
            )
            .title("Torque window restoration QA")
            .inner_size(policy.default_size.0, policy.default_size.1)
            .min_inner_size(policy.minimum_size.0, policy.minimum_size.1)
            .visible(false)
            .focused(false)
            .disable_drag_drop_handler()
            .build()
            .map_err(|error| error.to_string())?;
            restore_window_bounds(&window, &bounds, policy)?;
            window.show().map_err(|error| error.to_string())?;
            // Native resize/move requests are asynchronous across the event loop.
            std::thread::sleep(Duration::from_millis(250));
            let captured = window_bounds(&window)?;
            let position = window.outer_position().map_err(|error| error.to_string())?;
            let size = window.outer_size().map_err(|error| error.to_string())?;
            // macOS can change the work area while the first test window is
            // shown (for example, as the Dock settles). Compare with the same
            // current desktop that restoration uses, not the startup capture.
            let mut current_monitors = window
                .available_monitors()
                .map_err(|error| error.to_string())?;
            if let Some(primary) = window
                .primary_monitor()
                .map_err(|error| error.to_string())?
            {
                current_monitors.retain(|monitor| monitor.position() != primary.position());
                current_monitors.insert(0, primary);
            }
            let fits = current_monitors.iter().any(|monitor| {
                let area = monitor.work_area();
                position.x >= area.position.x - 1
                    && position.y >= area.position.y - 1
                    && position.x as i64 + size.width as i64
                        <= area.position.x as i64 + area.size.width as i64 + 1
                    && position.y as i64 + size.height as i64
                        <= area.position.y as i64 + area.size.height as i64 + 1
            });
            println!(
                "{}",
                serde_json::json!({"policy": name, "scenario": scenario,
                "requested": bounds, "captured": captured, "outer_position": position,
                "outer_size": size, "current_monitors": current_monitors,
                "fits_work_area": fits})
            );
            if !fits {
                return Err(format!("{label} is outside the usable desktop"));
            }
            if matches!(scenario, "visible" | "legacy")
                && (captured.width != bounds.width || captured.height != bounds.height)
            {
                return Err(format!("{label} changed its saved physical size"));
            }
            if scenario == "reopen" && captured != bounds {
                return Err(format!("{label} changed its captured physical geometry"));
            }
            // Exercise the exact serialized geometry accepted after recreation.
            retained = Some(
                serde_json::from_str::<WindowBounds>(&serde_json::to_string(&captured).unwrap())
                    .unwrap(),
            );
            window.destroy().map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn main() {
    let mut context = tauri::generate_context!();
    // Never navigate to the configured live daemon or create its main window.
    context.config_mut().app.windows.clear();
    context.config_mut().identifier = "net.torque.window-restoration-qa".into();
    let app = tauri::Builder::default()
        .build(context)
        .expect("isolated native QA shell");
    let result_code = Arc::new(AtomicI32::new(1));
    let callback_result = result_code.clone();
    // run() exits the process itself; return so the QA verdict controls status.
    app.run_return(move |app, event| {
        if let RunEvent::ExitRequested {
            code: None, api, ..
        } = &event
        {
            api.prevent_exit();
        }
        if matches!(event, RunEvent::Ready) {
            let app = app.clone();
            let callback_result = callback_result.clone();
            std::thread::spawn(move || match check(&app) {
                Ok(()) => {
                    println!("native window restoration: 10 scenarios passed");
                    callback_result.store(0, Ordering::SeqCst);
                    app.exit(0);
                }
                Err(error) => {
                    eprintln!("native window restoration failed: {error}");
                    app.exit(1);
                }
            });
        }
    });
    std::process::exit(result_code.load(Ordering::SeqCst));
}
