//! Detached PTY supervisor sidecar.
//!
//! Invoked by the Loom daemon via `loom_pty_supervisor::lifecycle::ensure_running`
//! with `--data-dir <path>`. Binds `<data_dir>/pty_supervisor.sock`,
//! writes its PID to `<data_dir>/pty_supervisor.pid`, and serves until
//! it receives SIGTERM / SIGINT.

use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use loom_pty_supervisor::{lifecycle::SupervisorPaths, PtySupervisor};

fn parse_args() -> PathBuf {
    let mut args = std::env::args().skip(1);
    let mut data_dir: Option<PathBuf> = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--data-dir" => {
                data_dir = args.next().map(PathBuf::from);
            }
            "--help" | "-h" => {
                eprintln!("Usage: loom-pty-supervisor --data-dir <path>");
                std::process::exit(0);
            }
            other => {
                eprintln!("unknown arg: {other}");
                std::process::exit(2);
            }
        }
    }
    data_dir.unwrap_or_else(|| {
        eprintln!("--data-dir is required");
        std::process::exit(2);
    })
}

fn main() -> Result<()> {
    let data_dir = parse_args();
    std::fs::create_dir_all(&data_dir)?;
    let paths = SupervisorPaths::from_data_dir(data_dir);

    // Write pid file before serve() so the parent can read it even
    // before our first successful bind.
    let pid = std::process::id();
    std::fs::write(&paths.pid_file, pid.to_string())?;

    // Log via tracing → stderr, which the parent redirected to the log
    // file when it spawned us.
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    let result = runtime.block_on(async move {
        let sup = Arc::new(PtySupervisor::new());
        let socket = paths.socket.clone();
        let pid_file = paths.pid_file.clone();
        let serve = tokio::spawn({
            let sup = sup.clone();
            async move { sup.serve(&socket).await }
        });

        // Wait for a termination signal. On SIGTERM/SIGINT we tear down
        // the socket + pid file cleanly. Sessions keep running until
        // they exit on their own — not ideal but matches the "supervisor
        // outlives the daemon" model: `close` is only ever issued by
        // the daemon, never by signals to the supervisor itself.
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        let mut int = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;
        tokio::select! {
            _ = term.recv() => tracing::info!("received SIGTERM, shutting down"),
            _ = int.recv() => tracing::info!("received SIGINT, shutting down"),
            res = serve => {
                tracing::warn!(?res, "serve loop returned unexpectedly");
            }
        }
        let _ = std::fs::remove_file(&paths.socket);
        let _ = std::fs::remove_file(&pid_file);
        Ok::<_, anyhow::Error>(())
    });
    result?;
    Ok(())
}
