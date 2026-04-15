//! Native macOS UI shell.
//!
//! Owns the main window, menubar, and native views. Subscribes to the
//! in-process `EventBus` for state updates and dispatches mutations via the
//! command handlers directly (no HTTP/WS round trip). Terminal surfaces are
//! provided by `loom-ghostty` — one native `NSView` per cell.
//!
//! ## Layering
//!
//! - [`render`] holds the pure-Rust render functions (what text/shape the
//!   sidebar and content views should display for a given engine snapshot).
//!   These are unit-tested.
//! - [`bridge`] is the engine-facing glue — snapshots, dispatch, event bus
//!   subscription. No AppKit types here.
//! - [`appkit`] (feature-gated) wraps the AppKit object graph: NSApplication,
//!   NSWindow, NSSplitView, NSTextView, etc. Enabled with
//!   `cargo build --features appkit`.
//!
//! v1 ships the first two layers fully and the AppKit layer as scaffolding
//! for a Mac-connected developer to finish.

pub mod bridge;
pub mod render;

#[cfg(all(target_os = "macos", feature = "appkit"))]
pub mod appkit;
#[cfg(all(target_os = "macos", feature = "appkit"))]
pub mod board;
#[cfg(all(target_os = "macos", feature = "appkit"))]
pub mod ghostty_view;
#[cfg(all(target_os = "macos", feature = "appkit"))]
pub mod modal;
#[cfg(all(target_os = "macos", feature = "appkit"))]
pub mod panel_header;
#[cfg(all(target_os = "macos", feature = "appkit"))]
pub mod sidebar;

use loom_server::app::AppState;

/// Run the native UI. Blocks the main thread until the app quits.
///
/// Without the `appkit` feature this is a stub that prints a hint and returns
/// immediately. Useful for headless runs and CI builds.
#[cfg(all(target_os = "macos", feature = "appkit"))]
pub fn run(engine: AppState) -> anyhow::Result<()> {
    appkit::run(engine)
}

#[cfg(not(all(target_os = "macos", feature = "appkit")))]
pub fn run(_engine: AppState) -> anyhow::Result<()> {
    tracing::info!(
        "loom-ui-native built without `appkit` feature — UI stub only. \
         Rebuild with `cargo run --bin loom-app --features loom-ui-native/appkit` \
         on macOS to launch the native window. Engine stays up — Ctrl-C to quit."
    );
    // Keep the engine alive by blocking until SIGINT.
    let rt = tokio::runtime::Handle::current();
    rt.block_on(async {
        let _ = tokio::signal::ctrl_c().await;
    });
    Ok(())
}
