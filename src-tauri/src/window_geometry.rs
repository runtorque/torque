//! Resolve persisted inner sizes / outer positions against the current desktop.
use crate::commands::window::WindowBounds;

#[derive(Clone, Debug)]
pub struct MonitorFrame {
    pub name: Option<String>,
    /// Physical work area, excluding menu bars, taskbars and docks.
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub scale: f64,
}

#[derive(Clone, Copy)]
pub struct WindowGeometryPolicy {
    pub default_size: (f64, f64),
    pub minimum_size: (f64, f64),
    /// Main-window persistence historically interpreted unmarked bounds as physical.
    pub legacy_physical: bool,
}

impl WindowGeometryPolicy {
    pub const MAIN: Self = Self {
        default_size: (1280.0, 800.0),
        minimum_size: (800.0, 600.0),
        legacy_physical: true,
    };
    pub const DETACHED: Self = Self {
        default_size: (900.0, 640.0),
        minimum_size: (420.0, 300.0),
        legacy_physical: false,
    };
}

pub struct RestoredGeometry {
    pub bounds: WindowBounds,
    pub minimum_size: (f64, f64),
}

fn positive(value: Option<f64>, fallback: f64) -> f64 {
    value
        .filter(|v| v.is_finite() && *v > 0.0)
        .unwrap_or(fallback)
}

fn physical_rect(
    bounds: &WindowBounds,
    frame: &MonitorFrame,
    policy: WindowGeometryPolicy,
) -> (Option<(f64, f64)>, f64, f64) {
    let factor = if bounds
        .physical
        .unwrap_or(policy.legacy_physical || bounds.display_id.is_some())
    {
        1.0
    } else {
        frame.scale
    };
    let position = bounds
        .x
        .zip(bounds.y)
        .filter(|(x, y)| x.is_finite() && y.is_finite())
        .map(|(x, y)| (x * factor, y * factor));
    let width = positive(
        bounds.width.map(|v| v * factor),
        policy.default_size.0 * frame.scale,
    );
    let height = positive(
        bounds.height.map(|v| v * factor),
        policy.default_size.1 * frame.scale,
    );
    (position, width, height)
}

fn overlap(bounds: &WindowBounds, frame: &MonitorFrame, policy: WindowGeometryPolicy) -> f64 {
    let (position, width, height) = physical_rect(bounds, frame, policy);
    let Some((x, y)) = position else {
        return 0.0;
    };
    let width = (x + width).min(frame.x + frame.width) - x.max(frame.x);
    let height = (y + height).min(frame.y + frame.height) - y.max(frame.y);
    width.max(0.0) * height.max(0.0)
}

/// Monitors are ordered with the primary first. Names are a fallback only: they
/// are not unique identifiers, and saved coordinates may outlive a layout change.
/// Decoration dimensions are logical (outer size minus inner size).
pub fn restore_bounds(
    bounds: &WindowBounds,
    monitors: &[MonitorFrame],
    policy: WindowGeometryPolicy,
    decorations: (f64, f64),
) -> Option<RestoredGeometry> {
    let monitors: Vec<_> = monitors
        .iter()
        .filter(|frame| {
            frame.x.is_finite()
                && frame.y.is_finite()
                && frame.width.is_finite()
                && frame.width > 0.0
                && frame.height.is_finite()
                && frame.height > 0.0
                && frame.scale.is_finite()
                && frame.scale > 0.0
        })
        .collect();
    let mut selected = None;
    let mut best_overlap = 0.0;
    for frame in &monitors {
        let area = overlap(bounds, frame, policy);
        if area > best_overlap {
            selected = Some(*frame);
            best_overlap = area;
        }
    }
    let frame = selected
        .or_else(|| {
            monitors
                .iter()
                .copied()
                .find(|frame| bounds.display_id.is_some() && bounds.display_id == frame.name)
        })
        .or_else(|| monitors.first().copied())?;
    let (position, width, height) = physical_rect(bounds, frame, policy);
    let border_width = positive(Some(decorations.0), 0.0) * frame.scale;
    let border_height = positive(Some(decorations.1), 0.0) * frame.scale;
    let available_width = (frame.width - border_width).floor().max(1.0);
    let available_height = (frame.height - border_height).floor().max(1.0);
    // A native minimum larger than the display must not defeat recovery.
    let min_width = (policy.minimum_size.0 * frame.scale).min(available_width);
    let min_height = (policy.minimum_size.1 * frame.scale).min(available_height);
    let width = width.round().max(min_width).min(available_width);
    let height = height.round().max(min_height).min(available_height);
    let max_x = frame.x + (available_width - width).max(0.0);
    let max_y = frame.y + (available_height - height).max(0.0);
    let (x, y) = match position {
        Some((x, y)) if overlap(bounds, frame, policy) > 0.0 => {
            (x.clamp(frame.x, max_x), y.clamp(frame.y, max_y))
        }
        _ => (
            frame.x + (max_x - frame.x) / 2.0,
            frame.y + (max_y - frame.y) / 2.0,
        ),
    };
    Some(RestoredGeometry {
        bounds: WindowBounds {
            physical: Some(true),
            x: Some(x.round()),
            y: Some(y.round()),
            width: Some(width),
            height: Some(height),
            display_id: frame.name.clone(),
        },
        minimum_size: (min_width, min_height),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(name: &str, x: f64, y: f64, width: f64, height: f64, scale: f64) -> MonitorFrame {
        MonitorFrame {
            name: Some(name.into()),
            x,
            y,
            width,
            height,
            scale,
        }
    }
    fn saved(x: f64, y: f64, width: f64, height: f64) -> WindowBounds {
        WindowBounds {
            physical: Some(true),
            x: Some(x),
            y: Some(y),
            width: Some(width),
            height: Some(height),
            ..Default::default()
        }
    }
    fn restored(bounds: &WindowBounds, monitors: &[MonitorFrame]) -> WindowBounds {
        restore_bounds(bounds, monitors, WindowGeometryPolicy::DETACHED, (0.0, 0.0))
            .unwrap()
            .bounds
    }

    #[test]
    fn preserves_secondary_with_negative_origin_and_duplicate_name() {
        let monitors = [
            frame("Display", 0.0, 0.0, 2400.0, 1600.0, 2.0),
            frame("Display", -1920.0, -400.0, 1920.0, 1080.0, 1.0),
        ];
        let mut bounds = saved(-1800.0, -300.0, 900.0, 640.0);
        bounds.display_id = Some("Display".into());
        assert_eq!(restored(&bounds, &monitors), bounds);
    }

    #[test]
    fn disconnected_monitor_recenters_on_primary() {
        let monitors = [frame("Primary", 0.0, 24.0, 1440.0, 836.0, 1.0)];
        let mut bounds = saved(-1800.0, -300.0, 900.0, 640.0);
        bounds.display_id = Some("Disconnected".into());
        let result = restored(&bounds, &monitors);
        assert_eq!((result.x, result.y), (Some(270.0), Some(122.0)));
        assert_eq!(result.display_id.as_deref(), Some("Primary"));
    }

    #[test]
    fn moved_monitor_recenters_on_named_display() {
        let monitors = [
            frame("Primary", 0.0, 0.0, 1440.0, 900.0, 1.0),
            frame("Secondary", 1440.0, 0.0, 1920.0, 1080.0, 1.0),
        ];
        let mut bounds = saved(-1800.0, -300.0, 900.0, 640.0);
        bounds.display_id = Some("Secondary".into());
        let result = restored(&bounds, &monitors);
        assert_eq!((result.x, result.y), (Some(1950.0), Some(220.0)));
    }

    #[test]
    fn oversized_main_fits_work_area_including_decorations() {
        let monitors = [frame("Retina", 0.0, 48.0, 2000.0, 1352.0, 2.0)];
        let result = restore_bounds(
            &saved(10.0, 50.0, 4000.0, 3000.0),
            &monitors,
            WindowGeometryPolicy::MAIN,
            (2.0, 28.0),
        )
        .unwrap();
        assert_eq!(
            (result.bounds.width, result.bounds.height),
            (Some(1996.0), Some(1296.0))
        );
        assert_eq!((result.bounds.x, result.bounds.y), (Some(0.0), Some(48.0)));
        assert_eq!(result.minimum_size, (1600.0, 1200.0));
    }

    #[test]
    fn small_display_lowers_minimum_to_keep_window_accessible() {
        let result = restore_bounds(
            &saved(5000.0, 5000.0, 2000.0, 1400.0),
            &[frame("Small", 0.0, 24.0, 640.0, 456.0, 1.0)],
            WindowGeometryPolicy::MAIN,
            (0.0, 28.0),
        )
        .unwrap();
        assert_eq!(result.minimum_size, (640.0, 428.0));
        assert_eq!(
            (result.bounds.width, result.bounds.height),
            (Some(640.0), Some(428.0))
        );
        assert_eq!((result.bounds.x, result.bounds.y), (Some(0.0), Some(24.0)));
    }

    #[test]
    fn physical_and_legacy_captures_do_not_double_on_retina() {
        let monitors = [frame("Retina", 0.0, 48.0, 3024.0, 1800.0, 2.0)];
        let mut bounds = saved(100.0, 100.0, 1952.0, 1308.0);
        bounds.display_id = Some("Retina".into());
        assert_eq!(restored(&bounds, &monitors), bounds);
        bounds.physical = None;
        assert_eq!(restored(&bounds, &monitors).width, Some(1952.0));
    }

    #[test]
    fn unmarked_main_captures_keep_historical_physical_units() {
        let mut bounds = saved(100.0, 100.0, 1952.0, 1308.0);
        bounds.physical = None;
        let result = restore_bounds(
            &bounds,
            &[frame("Retina", 0.0, 48.0, 3024.0, 1800.0, 2.0)],
            WindowGeometryPolicy::MAIN,
            (0.0, 0.0),
        )
        .unwrap()
        .bounds;
        assert_eq!((result.x, result.y), (Some(100.0), Some(100.0)));
        assert_eq!((result.width, result.height), (Some(1952.0), Some(1308.0)));
    }

    #[test]
    fn explicit_logical_units_override_legacy_display_marker() {
        let monitors = [frame("Retina", 0.0, 48.0, 3024.0, 1800.0, 2.0)];
        let mut bounds = saved(50.0, 50.0, 976.0, 654.0);
        bounds.physical = Some(false);
        bounds.display_id = Some("Retina".into());
        let result = restored(&bounds, &monitors);
        assert_eq!((result.x, result.y), (Some(100.0), Some(100.0)));
        assert_eq!((result.width, result.height), (Some(1952.0), Some(1308.0)));
    }

    #[test]
    fn logical_defaults_are_centered_in_physical_work_area() {
        let result = restored(
            &WindowBounds::default(),
            &[frame("Retina", 0.0, 48.0, 3024.0, 1800.0, 2.0)],
        );
        assert_eq!((result.width, result.height), (Some(1800.0), Some(1280.0)));
        assert_eq!((result.x, result.y), (Some(612.0), Some(308.0)));
    }

    #[test]
    fn mixed_scale_uses_selected_display_for_logical_minimum() {
        let result = restored(
            &saved(-1500.0, 50.0, 500.0, 350.0),
            &[
                frame("Retina", 0.0, 0.0, 2400.0, 1600.0, 2.0),
                frame("Secondary", -1920.0, 0.0, 1920.0, 1080.0, 1.0),
            ],
        );
        assert_eq!((result.width, result.height), (Some(500.0), Some(350.0)));
        assert_eq!(result.display_id.as_deref(), Some("Secondary"));
    }

    #[test]
    fn invalid_values_use_defaults_and_no_monitor_is_explicit() {
        let bounds = WindowBounds {
            x: Some(f64::NAN),
            y: Some(f64::INFINITY),
            width: Some(-1.0),
            height: Some(0.0),
            ..Default::default()
        };
        let result = restored(&bounds, &[frame("Primary", 0.0, 0.0, 1200.0, 800.0, 1.0)]);
        assert_eq!((result.x, result.y), (Some(150.0), Some(80.0)));
        assert_eq!((result.width, result.height), (Some(900.0), Some(640.0)));
        assert!(restore_bounds(&bounds, &[], WindowGeometryPolicy::MAIN, (0.0, 0.0)).is_none());
    }
}
