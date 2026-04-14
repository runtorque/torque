//! Loom desktop app entry point.
//!
//! Starts the engine (HTTP on 127.0.0.1:18932 for CLI/hooks/MCP) and then
//! hands the main thread to the native UI. The UI subscribes to the engine's
//! event bus in-process and dispatches commands directly — no HTTP round
//! trips for local interactions.

use anyhow::Result;
use loom_server::{run_server, ServerConfig};
use tracing_subscriber::{fmt, EnvFilter};

fn main() -> Result<()> {
    fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info,loom=debug")),
        )
        .init();

    tracing::info!("loom-app starting");

    // Build the tokio runtime ourselves — the AppKit run loop owns the main
    // thread, and the engine does its async work on tokio worker threads.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    // Start the engine inside the runtime.
    let handle = runtime.block_on(async {
        let cfg = ServerConfig::default();
        run_server(cfg).await
    })?;

    tracing::info!("engine listening on http://{}", handle.addr);

    if cfg!(feature = "headless") {
        tracing::info!("headless mode — no native UI, engine stays up until SIGINT");
        runtime.block_on(async {
            let _ = tokio::signal::ctrl_c().await;
        });
        let _ = handle.shutdown_tx.send(());
        return Ok(());
    }

    // Enter the tokio runtime context so the UI thread can obtain a handle
    // via `tokio::runtime::Handle::current()`.
    let _guard = runtime.enter();

    // The native UI takes over the main thread and blocks until quit.
    // The tokio runtime keeps running on its worker threads for engine tasks.
    #[cfg(target_os = "macos")]
    {
        loom_ui_native::run(handle.app_state.clone())?;
    }
    #[cfg(not(target_os = "macos"))]
    {
        tracing::warn!("native UI is macOS-only; running headless");
        runtime.block_on(async {
            let _ = tokio::signal::ctrl_c().await;
        });
    }

    let _ = handle.shutdown_tx.send(());
    Ok(())
}
