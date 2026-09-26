use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, LogicalPosition, LogicalSize, Manager, PhysicalPosition, PhysicalSize, Position,
    Size, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

use crate::daemon::DaemonSettings;
use crate::menu;
use crate::window_geometry::{restore_bounds, MonitorFrame, WindowGeometryPolicy};

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct WindowBounds {
    /// Native captures use physical pixels; omitted legacy/default sizes are logical.
    #[serde(default)]
    pub physical: Option<bool>,
    pub x: Option<f64>,
    pub y: Option<f64>,
    pub width: Option<f64>,
    pub height: Option<f64>,
    #[serde(default)]
    pub display_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct DetachedWindowInfo {
    pub panel: String,
    pub label: String,
}

#[derive(Default)]
pub struct NativeWindowState {
    active_label: Mutex<String>,
    detached_by_panel: Mutex<HashMap<String, String>>,
    app_exiting: Mutex<bool>,
}

impl NativeWindowState {
    pub fn set_active_label(&self, label: impl Into<String>) {
        if let Ok(mut active) = self.active_label.lock() {
            *active = label.into();
        }
    }

    pub fn active_label(&self) -> String {
        self.active_label
            .lock()
            .map(|label| label.clone())
            .unwrap_or_default()
    }

    pub fn set_app_exiting(&self, exiting: bool) {
        if let Ok(mut value) = self.app_exiting.lock() {
            *value = exiting;
        }
    }

    pub fn app_exiting(&self) -> bool {
        self.app_exiting.lock().map(|value| *value).unwrap_or(false)
    }

    pub fn remember_detached(&self, panel: impl Into<String>, label: impl Into<String>) {
        if let Ok(mut detached) = self.detached_by_panel.lock() {
            detached.insert(panel.into(), label.into());
        }
    }

    pub fn detached_label_for_panel(&self, panel: &str) -> Option<String> {
        self.detached_by_panel
            .lock()
            .ok()
            .and_then(|detached| detached.get(panel).cloned())
    }

    pub fn remove_detached_by_label(&self, label: &str) -> Option<String> {
        let mut detached = self.detached_by_panel.lock().ok()?;
        let panel = detached
            .iter()
            .find(|(_, existing)| existing.as_str() == label)
            .map(|(panel, _)| panel.clone())?;
        detached.remove(&panel);
        Some(panel)
    }

    pub fn detached_infos(&self) -> Vec<DetachedWindowInfo> {
        self.detached_by_panel
            .lock()
            .map(|detached| {
                detached
                    .iter()
                    .map(|(panel, label)| DetachedWindowInfo {
                        panel: panel.clone(),
                        label: label.clone(),
                    })
                    .collect()
            })
            .unwrap_or_default()
    }
}

#[tauri::command]
pub async fn detach(
    app: AppHandle,
    settings: tauri::State<'_, DaemonSettings>,
    window_state: tauri::State<'_, NativeWindowState>,
    panel: String,
    bounds: Option<WindowBounds>,
    section: Option<String>,
) -> Result<String, String> {
    detach_panel(&app, &settings, &window_state, panel, bounds, section)
}

#[tauri::command]
pub fn focus_window(app: AppHandle, label: String) -> Result<(), String> {
    let window = app
        .get_webview_window(&label)
        .ok_or_else(|| format!("No Torque window named '{label}'"))?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn reattach(
    app: AppHandle,
    window_state: tauri::State<'_, NativeWindowState>,
    label: String,
) -> Result<(), String> {
    reattach_label(&app, &window_state, &label)
}

#[tauri::command]
pub fn close_window(
    app: AppHandle,
    window_state: tauri::State<'_, NativeWindowState>,
    label: Option<String>,
) -> Result<(), String> {
    let target = label.unwrap_or_else(|| window_state.active_label());
    if target.is_empty() || target == "main" {
        if let Some(main) = app.get_webview_window("main") {
            return main.close().map_err(|error| error.to_string());
        }
        return Ok(());
    }
    reattach_label(&app, &window_state, &target)
}

#[tauri::command]
pub fn reattach_all(
    app: AppHandle,
    window_state: tauri::State<'_, NativeWindowState>,
) -> Result<(), String> {
    let labels: Vec<String> = window_state
        .detached_infos()
        .into_iter()
        .map(|info| info.label)
        .collect();
    for label in labels {
        let _ = reattach_label(&app, &window_state, &label);
    }
    Ok(())
}

#[tauri::command]
pub fn list_detached(window_state: tauri::State<'_, NativeWindowState>) -> Vec<DetachedWindowInfo> {
    window_state.detached_infos()
}

#[tauri::command]
pub fn current_window_bounds(window: WebviewWindow) -> Result<WindowBounds, String> {
    window_bounds(&window)
}

pub fn detach_panel(
    app: &AppHandle,
    settings: &DaemonSettings,
    window_state: &NativeWindowState,
    panel: String,
    bounds: Option<WindowBounds>,
    section: Option<String>,
) -> Result<String, String> {
    let panel = sanitize_panel(&panel).ok_or_else(|| "Unknown panel".to_string())?;

    let label = make_detached_label(&panel);
    let url = detached_url(&settings.frontend_url(), &panel, &label, section.as_deref())?;

    if let Some(existing) = window_state.detached_label_for_panel(&panel) {
        if let Some(window) = app.get_webview_window(&existing) {
            let _ = window.set_focus();
            return Ok(existing);
        }
        window_state.remove_detached_by_label(&existing);
    }

    let mut builder = WebviewWindowBuilder::new(app, label.clone(), WebviewUrl::External(url))
        .title(format!("Torque — {}", panel_title(&panel)))
        .inner_size(900.0, 640.0)
        .min_inner_size(420.0, 300.0)
        // HTML5 drops belong to the frontend (terminal, attachments, compose).
        .disable_drag_drop_handler()
        .visible(false);
    if let Ok(menu) = menu::build_detached_panel_menu(app) {
        builder = builder.menu(menu);
    }
    let window = builder.build().map_err(|error| error.to_string())?;
    restore_window_bounds(&window, &bounds.unwrap_or_default(), WindowGeometryPolicy::DETACHED)?;
    window.show().map_err(|error| error.to_string())?;
    window_state.remember_detached(panel, label.clone());
    window_state.set_active_label(label.clone());
    let _ = window.set_focus();
    Ok(label)
}

pub fn reattach_label(
    app: &AppHandle,
    window_state: &NativeWindowState,
    label: &str,
) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(label) {
        window.close().map_err(|error| error.to_string())?;
    } else if label != "main" {
        window_state.remove_detached_by_label(label);
    }
    Ok(())
}

fn requested_window_size(bounds: &WindowBounds) -> Size {
    let width = bounds.width.unwrap_or(900.0);
    let height = bounds.height.unwrap_or(640.0);
    if bounds.physical.unwrap_or(bounds.display_id.is_some()) {
        Size::Physical(PhysicalSize::new(
            width.round() as u32,
            height.round() as u32,
        ))
    } else {
        Size::Logical(LogicalSize::new(width, height))
    }
}

pub fn window_bounds(window: &WebviewWindow) -> Result<WindowBounds, String> {
    let position = window
        .outer_position()
        .map_err(|error| error.to_string())
        .ok();
    let size = window.inner_size().map_err(|error| error.to_string())?;
    let display_id = window
        .current_monitor()
        .ok()
        .flatten()
        .and_then(|monitor| monitor.name().cloned());
    Ok(WindowBounds {
        physical: Some(true),
        x: position.as_ref().map(|pos| pos.x as f64),
        y: position.as_ref().map(|pos| pos.y as f64),
        width: Some(size.width as f64),
        height: Some(size.height as f64),
        display_id,
    })
}

pub fn sanitize_panel(panel: &str) -> Option<String> {
    let panel = panel.trim().to_ascii_lowercase();
    match panel.as_str() {
        "board" | "actions" | "templates" | "context" | "events" | "engineer" | "agents"
        | "terminal" | "planning" | "control" => Some(panel),
        _ => None,
    }
}

pub fn panel_title(panel: &str) -> &'static str {
    match panel {
        "board" => "Board",
        "actions" => "Actions",
        "templates" => "Library",
        "context" => "Context",
        "events" => "Events",
        "engineer" => "Agent",
        "agents" => "Agents",
        "terminal" => "Terminal",
        "planning" => "Planning",
        "control" => "Control Center",
        _ => "Panel",
    }
}

pub fn make_detached_label(panel: &str) -> String {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("panel-{}-{:x}", panel, stamp & 0x00ff_ffff)
}

pub fn panel_from_label(label: &str) -> Option<String> {
    let rest = label.strip_prefix("panel-")?;
    let panel = rest.split('-').next().unwrap_or("");
    sanitize_panel(panel)
}

fn detached_url(
    base: &str,
    panel: &str,
    label: &str,
    section: Option<&str>,
) -> Result<tauri::Url, String> {
    if let Some(section) = section {
        if panel != "control"
            || !matches!(
                section,
                "mission"
                    | "activity"
                    | "history"
                    | "context"
                    | "logs"
                    | "chat"
                    | "pipelines"
                    | "actions"
                    | "catalog"
                    | "settings"
                    | "help"
            )
        {
            return Err("Unknown detached Control Center section".to_string());
        }
    }
    let mut url = base
        .parse::<tauri::Url>()
        .map_err(|error| format!("Invalid detached window URL '{base}': {error}"))?;
    let retained: Vec<(String, String)> = url
        .query_pairs()
        .filter(|(key, _)| !matches!(key.as_ref(), "panel" | "window" | "section"))
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect();
    {
        let mut query = url.query_pairs_mut();
        query
            .clear()
            .extend_pairs(retained)
            .append_pair("panel", panel)
            .append_pair("window", label);
        if let Some(section) = section {
            query.append_pair("section", section);
        }
    }
    Ok(url)
}

/// Both main and detached windows use the same current-monitor recovery path.
pub fn restore_window_bounds(
    window: &WebviewWindow,
    bounds: &WindowBounds,
    policy: WindowGeometryPolicy,
) -> Result<(), String> {
    let mut monitors = window.available_monitors().unwrap_or_default();
    if let Ok(Some(primary)) = window.primary_monitor() {
        monitors.retain(|monitor| monitor.position() != primary.position());
        monitors.insert(0, primary);
    }
    let frames: Vec<_> = monitors
        .iter()
        .map(|monitor| {
            let area = monitor.work_area();
            MonitorFrame {
                name: monitor.name().cloned(),
                x: area.position.x as f64,
                y: area.position.y as f64,
                width: area.size.width as f64,
                height: area.size.height as f64,
                scale: monitor.scale_factor(),
            }
        })
        .collect();
    let scale = window.scale_factor().unwrap_or(1.0);
    let decorations = window
        .outer_size()
        .ok()
        .zip(window.inner_size().ok())
        .map(|(outer, inner)| {
            (
                outer.width.saturating_sub(inner.width) as f64 / scale,
                outer.height.saturating_sub(inner.height) as f64 / scale,
            )
        })
        .unwrap_or((0.0, 0.0));
    let restored = restore_bounds(bounds, &frames, policy, decorations);
    let geometry = restored
        .as_ref()
        .map(|result| &result.bounds)
        .unwrap_or(bounds);
    // Move first: DPI changes must precede applying the saved physical size.
    if let (Some(x), Some(y)) = (geometry.x, geometry.y) {
        let position = if geometry.physical.unwrap_or(geometry.display_id.is_some()) {
            Position::Physical(PhysicalPosition::new(x.round() as i32, y.round() as i32))
        } else {
            Position::Logical(LogicalPosition::new(x, y))
        };
        window
            .set_position(position)
            .map_err(|error| error.to_string())?;
    } else {
        window.center().map_err(|error| error.to_string())?;
    }
    if let Some(ref result) = restored {
        window
            .set_min_size(Some(Size::Physical(PhysicalSize::new(
                result.minimum_size.0.round() as u32,
                result.minimum_size.1.round() as u32,
            ))))
            .map_err(|error| error.to_string())?;
    }
    window
        .set_size(requested_window_size(geometry))
        .map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persisted_physical_sizes_do_not_double_on_retina() {
        let mut bounds = WindowBounds {
            width: Some(1952.0),
            height: Some(1308.0),
            physical: Some(true),
            ..Default::default()
        };
        assert_eq!(
            requested_window_size(&bounds).to_physical::<u32>(2.0),
            PhysicalSize::new(1952, 1308)
        );
        bounds.physical = None;
        assert_eq!(
            requested_window_size(&bounds).to_physical::<u32>(2.0),
            PhysicalSize::new(3904, 2616)
        );
        bounds.display_id = Some("Legacy monitor".into());
        assert_eq!(
            requested_window_size(&bounds).to_physical::<u32>(2.0),
            PhysicalSize::new(1952, 1308)
        );
        let captured = serde_json::to_string(&WindowBounds {
            physical: Some(true),
            ..bounds
        })
        .unwrap();
        let restored: WindowBounds = serde_json::from_str(&captured).unwrap();
        assert_eq!(
            requested_window_size(&restored).to_physical::<u32>(2.0),
            PhysicalSize::new(1952, 1308)
        );
    }

    #[test]
    fn panel_labels_round_trip() {
        let label = make_detached_label("engineer");
        assert!(label.starts_with("panel-engineer-"));
        assert_eq!(panel_from_label(&label).as_deref(), Some("engineer"));
        assert_eq!(sanitize_panel("Library"), None);
        assert_eq!(sanitize_panel("templates").as_deref(), Some("templates"));
        assert_eq!(sanitize_panel("agents").as_deref(), Some("agents"));
        assert_eq!(sanitize_panel("terminal").as_deref(), Some("terminal"));
        assert_eq!(sanitize_panel("planning").as_deref(), Some("planning"));
        assert_eq!(sanitize_panel("control").as_deref(), Some("control"));
    }

    #[test]
    fn detached_url_preserves_route_base_for_relative_assets() {
        let url = detached_url(
            "http://127.0.0.1:18933/ui-next/",
            "board",
            "panel-board-1",
            None,
        )
        .expect("detached URL");

        assert_eq!(
            url.as_str(),
            "http://127.0.0.1:18933/ui-next/?panel=board&window=panel-board-1"
        );
    }

    #[test]
    fn detached_control_sections_are_bounded_and_preserve_other_url_parts() {
        for section in [
            "mission",
            "activity",
            "history",
            "context",
            "logs",
            "chat",
            "pipelines",
            "actions",
            "catalog",
            "settings",
            "help",
        ] {
            let url = detached_url(
                "http://127.0.0.1:18933/ui-next/?onboarding=0&panel=board&section=old#anchor",
                "control",
                "control window",
                Some(section),
            )
            .unwrap();
            let pairs: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
            assert_eq!(pairs.get("section").map(String::as_str), Some(section));
            assert_eq!(pairs.get("panel").map(String::as_str), Some("control"));
            assert_eq!(
                pairs.get("window").map(String::as_str),
                Some("control window")
            );
            assert_eq!(pairs.get("onboarding").map(String::as_str), Some("0"));
            assert_eq!(
                url.query_pairs().filter(|(key, _)| key == "panel").count(),
                1
            );
            assert_eq!(url.path(), "/ui-next/");
            assert_eq!(url.fragment(), Some("anchor"));
        }
        for section in ["", "unknown", "CONTEXT", "context&panel=board"] {
            assert!(
                detached_url("http://127.0.0.1:18933/", "control", "test", Some(section)).is_err()
            );
        }
        assert!(detached_url("http://127.0.0.1:18933/", "board", "test", Some("context")).is_err());
        assert!(detached_url("http://127.0.0.1:18933/", "control", "test", None).is_ok());
    }

    #[test]
    fn restoration_keeps_titlebar_reachable() {
        let restored = restore_bounds(
            &WindowBounds {
                physical: Some(true),
                x: Some(100.0),
                y: Some(-100.0),
                width: Some(600.0),
                height: Some(400.0),
                ..Default::default()
            },
            &[MonitorFrame {
                name: None,
                scale: 1.0,
                x: 0.0,
                y: 24.0,
                width: 1200.0,
                height: 776.0,
            }],
            WindowGeometryPolicy::DETACHED,
            (0.0, 0.0),
        )
        .unwrap()
        .bounds;
        assert_eq!(restored.y, Some(24.0));
    }

    #[test]
    fn clamp_recenters_offscreen_bounds() {
        let monitor = MonitorFrame {
            name: None,
            scale: 1.0,
            x: 0.0,
            y: 0.0,
            width: 1200.0,
            height: 800.0,
        };
        let clamped = restore_bounds(
            &WindowBounds {
                physical: None,
                x: Some(5000.0),
                y: Some(5000.0),
                width: Some(600.0),
                height: Some(400.0),
                display_id: None,
            },
            &[monitor],
            WindowGeometryPolicy::DETACHED,
            (0.0, 0.0),
        )
        .expect("bounds")
        .bounds;
        assert_eq!(clamped.x, Some(300.0));
        assert_eq!(clamped.y, Some(200.0));
    }
}
