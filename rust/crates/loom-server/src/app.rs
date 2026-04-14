//! Axum app assembly + startup.

use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::Result;
use axum::Router;
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_pty::LocalPtyBackend;

pub struct ServerConfig {
    pub bind: SocketAddr,
    pub data_dir: std::path::PathBuf,
}

impl Default for ServerConfig {
    fn default() -> Self {
        let port: u16 = std::env::var("LOOM_PORT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(loom_core::config::DEFAULT_PORT);
        Self {
            bind: SocketAddr::from(([127, 0, 0, 1], port)),
            data_dir: loom_core::config::data_dir(),
        }
    }
}

#[derive(Clone)]
pub struct AppState {
    pub db: LoomDb,
    pub state: Arc<Mutex<MatrixState>>,
    pub bus: EventBus,
    /// PTY backend for dispatch & send_text. `None` in environments where the
    /// engine runs headless without a terminal layer (e.g. some unit tests).
    pub pty: Option<Arc<LocalPtyBackend>>,
}

pub struct ServerHandle {
    pub addr: SocketAddr,
    pub shutdown_tx: tokio::sync::oneshot::Sender<()>,
    pub app_state: AppState,
}

pub async fn run_server(config: ServerConfig) -> Result<ServerHandle> {
    loom_core::config::ensure_data_dir()?;

    let db = LoomDb::open(loom_core::config::db_path())?;
    let state = db.load_all().await?;
    let state = Arc::new(Mutex::new(state));
    let bus = EventBus::new();

    let (pty, mut pty_rx) = LocalPtyBackend::new();
    let pty = Arc::new(pty);

    // PTY event pump → state mutations.
    {
        let state_clone = state.clone();
        let bus_clone = bus.clone();
        tokio::spawn(async move {
            while let Some(evt) = pty_rx.recv().await {
                handle_pty_event(&state_clone, &bus_clone, evt).await;
            }
        });
    }

    let app_state = AppState {
        db: db.clone(),
        state: state.clone(),
        bus: bus.clone(),
        pty: Some(pty),
    };

    // Spawn scheduler
    crate::scheduler::spawn(state.clone(), db.clone(), bus.clone());

    let router = Router::new()
        .merge(crate::ws::routes())
        .merge(crate::commands::routes())
        .merge(crate::events::routes())
        .merge(crate::uploads::routes())
        .merge(crate::mcp::routes())
        .with_state(app_state.clone());

    let listener = tokio::net::TcpListener::bind(config.bind).await?;
    let addr = listener.local_addr()?;
    let (tx, rx) = tokio::sync::oneshot::channel::<()>();

    tokio::spawn(async move {
        let server = axum::serve(listener, router).with_graceful_shutdown(async move {
            let _ = rx.await;
        });
        if let Err(err) = server.await {
            tracing::error!("server error: {err}");
        }
    });

    Ok(ServerHandle {
        addr,
        shutdown_tx: tx,
        app_state,
    })
}

async fn handle_pty_event(
    state: &Arc<Mutex<MatrixState>>,
    bus: &EventBus,
    evt: loom_pty::PtyEvent,
) {
    use loom_core::events::OutMessage;
    use loom_pty::PtyEvent;

    let cell_id = match &evt {
        PtyEvent::Spawned { cell_id, .. }
        | PtyEvent::Output { cell_id, .. }
        | PtyEvent::Exited { cell_id, .. }
        | PtyEvent::Error { cell_id, .. } => cell_id.clone(),
    };

    let mut st = state.lock().await;
    let Some(cell) = st.agents.get_mut(&cell_id) else {
        return;
    };
    match evt {
        PtyEvent::Spawned { pid, .. } => {
            cell.status = "running".into();
            cell.session_id = Some(pid.to_string());
        }
        PtyEvent::Output { .. } => {
            cell.last_event_at = chrono::Utc::now().timestamp() as f64;
        }
        PtyEvent::Exited { status, .. } => {
            cell.status = if status == 0 { "stopped".into() } else { "error".into() };
            cell.session_id = None;
        }
        PtyEvent::Error { message, .. } => {
            cell.error_message = message;
            cell.status = "error".into();
        }
    }
    st.emit_agent(&cell_id);
    if let Some((seq, ops)) = st.drain_deltas() {
        bus.send(OutMessage::Delta { seq, ops });
    }
}
