use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::daemon::DaemonSettings;
use crate::menu;

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct WindowBounds {
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
) -> Result<String, String> {
    detach_panel(&app, &settings, &window_state, panel, bounds)
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
) -> Result<String, String> {
    let panel = sanitize_panel(&panel).ok_or_else(|| "Unknown panel".to_string())?;

    if let Some(existing) = window_state.detached_label_for_panel(&panel) {
        if let Some(window) = app.get_webview_window(&existing) {
            let _ = window.set_focus();
            return Ok(existing);
        }
        window_state.remove_detached_by_label(&existing);
    }

    let label = make_detached_label(&panel);
    let url = detached_url(&settings.url(), &panel, &label)?;
    let mut builder = WebviewWindowBuilder::new(app, label.clone(), WebviewUrl::External(url))
        .title(format!("Torque — {}", panel_title(&panel)))
        .inner_size(
            bounds.as_ref().and_then(|b| b.width).unwrap_or(900.0),
            bounds.as_ref().and_then(|b| b.height).unwrap_or(640.0),
        )
        .min_inner_size(420.0, 300.0)
        .visible(true);

    if let Some(menu) = menu::build_detached_panel_menu(app)
        .map_err(|error| error.to_string())
        .ok()
    {
        builder = builder.menu(menu);
    }

    if let Some(clamped) = clamp_bounds(bounds, primary_monitor_frame(app)) {
        if let (Some(x), Some(y)) = (clamped.x, clamped.y) {
            builder = builder.position(x, y);
        }
    }

    let window = builder.build().map_err(|error| error.to_string())?;
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
        "board" | "actions" | "templates" | "context" | "events" | "engineer" => Some(panel),
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

fn detached_url(base: &str, panel: &str, label: &str) -> Result<tauri::Url, String> {
    let sep = if base.contains('?') { '&' } else { '?' };
    let url = format!(
        "{}{}panel={}&window={}",
        base.trim_end_matches('/'),
        sep,
        percent_encode(panel),
        percent_encode(label)
    );
    url.parse::<tauri::Url>()
        .map_err(|error| format!("Invalid detached window URL '{url}': {error}"))
}

fn percent_encode(value: &str) -> String {
    value
        .bytes()
        .flat_map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                vec![byte as char]
            }
            _ => format!("%{byte:02X}").chars().collect(),
        })
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MonitorFrame {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

fn primary_monitor_frame(app: &AppHandle) -> Option<MonitorFrame> {
    let monitor = app.primary_monitor().ok().flatten()?;
    let pos = monitor.position();
    let size = monitor.size();
    Some(MonitorFrame {
        x: pos.x as f64,
        y: pos.y as f64,
        width: size.width as f64,
        height: size.height as f64,
    })
}

pub fn clamp_bounds(
    bounds: Option<WindowBounds>,
    monitor: Option<MonitorFrame>,
) -> Option<WindowBounds> {
    let mut bounds = bounds?;
    let Some(frame) = monitor else {
        return Some(bounds);
    };
    let width = bounds
        .width
        .unwrap_or(900.0)
        .max(420.0)
        .min(frame.width.max(420.0));
    let height = bounds
        .height
        .unwrap_or(640.0)
        .max(300.0)
        .min(frame.height.max(300.0));
    bounds.width = Some(width);
    bounds.height = Some(height);
    let x = bounds
        .x
        .unwrap_or(frame.x + ((frame.width - width) / 2.0).max(0.0));
    let y = bounds
        .y
        .unwrap_or(frame.y + ((frame.height - height) / 2.0).max(0.0));
    let center_x = x + width / 2.0;
    let center_y = y + height / 2.0;
    let inside = center_x >= frame.x
        && center_x <= frame.x + frame.width
        && center_y >= frame.y
        && center_y <= frame.y + frame.height;
    if inside {
        bounds.x = Some(x);
        bounds.y = Some(y);
        return Some(bounds);
    }
    bounds.x = Some(frame.x + ((frame.width - width) / 2.0).max(0.0));
    bounds.y = Some(frame.y + ((frame.height - height) / 2.0).max(0.0));
    Some(bounds)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn panel_labels_round_trip() {
        let label = make_detached_label("engineer");
        assert!(label.starts_with("panel-engineer-"));
        assert_eq!(panel_from_label(&label).as_deref(), Some("engineer"));
        assert_eq!(sanitize_panel("Library"), None);
        assert_eq!(sanitize_panel("templates").as_deref(), Some("templates"));
    }

    #[test]
    fn clamp_recenters_offscreen_bounds() {
        let monitor = MonitorFrame {
            x: 0.0,
            y: 0.0,
            width: 1200.0,
            height: 800.0,
        };
        let clamped = clamp_bounds(
            Some(WindowBounds {
                x: Some(5000.0),
                y: Some(5000.0),
                width: Some(600.0),
                height: Some(400.0),
                display_id: None,
            }),
            Some(monitor),
        )
        .expect("bounds");
        assert_eq!(clamped.x, Some(300.0));
        assert_eq!(clamped.y, Some(200.0));
    }
}
