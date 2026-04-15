//! Axum app assembly + startup.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex as StdMutex};

use anyhow::Result;
use axum::extract::Path;
use axum::http::{header, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use tokio::sync::{mpsc, Mutex};
use tower_http::services::ServeDir;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_pty::LocalPtyBackend;

/// Registry of agents whose terminal is hosted by the native UI (a
/// `GhosttyView`, not a `LocalPtyBackend` session). When an agent is in the
/// registry, dispatch routes its prompt through the registered channel
/// instead of spawning a PTY on the engine side.
///
/// The UI owns the receiver side — it drains pending text each refresh tick
/// and calls `GhosttyView::send_text`. This keeps the engine free of AppKit
/// concerns (no `dispatch2::Queue::main` inside the engine).
#[derive(Clone, Default)]
pub struct UiAgentRegistry {
    inner: Arc<StdMutex<HashMap<String, mpsc::UnboundedSender<String>>>>,
}

impl UiAgentRegistry {
    /// UI registers an agent. Returns the receiver to be drained from the
    /// main thread.
    pub fn register(&self, agent_id: String) -> mpsc::UnboundedReceiver<String> {
        let (tx, rx) = mpsc::unbounded_channel();
        let mut map = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        map.insert(agent_id, tx);
        rx
    }

    pub fn unregister(&self, agent_id: &str) {
        let mut map = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        map.remove(agent_id);
    }

    pub fn is_attached(&self, agent_id: &str) -> bool {
        let map = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        // A closed channel counts as detached.
        map.get(agent_id).map(|tx| !tx.is_closed()).unwrap_or(false)
    }

    /// Returns `true` iff the text was queued to a live UI-attached agent.
    pub fn send(&self, agent_id: &str, text: String) -> bool {
        let map = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(tx) = map.get(agent_id) {
            tx.send(text).is_ok()
        } else {
            false
        }
    }
}

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
    /// Registry of UI-attached agents. Dispatch routes through this when the
    /// target agent is mounted in the native window.
    pub ui_agents: UiAgentRegistry,
}

pub struct ServerHandle {
    pub addr: SocketAddr,
    pub shutdown_tx: tokio::sync::oneshot::Sender<()>,
    pub app_state: AppState,
}

pub fn base_router() -> Router<AppState> {
    Router::new()
        .route("/", get(handle_index))
        .route("/attachments/{task_id}/{filename}", get(handle_attachment))
        .nest_service("/static", ServeDir::new(frontend_static_dir()))
        .merge(crate::ws::routes())
        .merge(crate::commands::routes())
        .merge(crate::events::routes())
        .merge(crate::uploads::routes())
        .merge(crate::mcp::routes())
}

pub fn build_router(app_state: AppState) -> Router {
    base_router().with_state(app_state)
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
        ui_agents: UiAgentRegistry::default(),
    };

    // Spawn scheduler
    crate::scheduler::spawn(state.clone(), db.clone(), bus.clone());

    let router = build_router(app_state.clone());

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

fn repo_root() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .ancestors()
        .nth(3)
        .map(PathBuf::from)
        .unwrap_or(manifest_dir)
}

fn frontend_static_dir() -> PathBuf {
    repo_root().join("static")
}

fn frontend_index_path() -> PathBuf {
    repo_root().join("webview.html")
}

async fn handle_index() -> Result<Html<String>, (StatusCode, String)> {
    let html = tokio::fs::read_to_string(frontend_index_path())
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err.to_string()))?;
    Ok(Html(html))
}

async fn handle_attachment(
    Path((task_id, filename)): Path<(String, String)>,
) -> Result<Response, (StatusCode, String)> {
    if invalid_attachment_path_segment(&task_id) || invalid_attachment_path_segment(&filename) {
        return Err((StatusCode::BAD_REQUEST, "invalid attachment path".into()));
    }
    let path = loom_core::config::attachments_dir().join(&task_id).join(&filename);
    let body = tokio::fs::read(&path)
        .await
        .map_err(|_| (StatusCode::NOT_FOUND, "attachment not found".into()))?;
    let mime = guess_content_type(&filename);
    Ok((
        [
            (header::CONTENT_TYPE, mime),
            (header::CACHE_CONTROL, "max-age=3600"),
        ],
        body,
    )
        .into_response())
}

fn invalid_attachment_path_segment(value: &str) -> bool {
    value.contains('/') || value.contains('\\') || value.contains("..")
}

fn guess_content_type(filename: &str) -> &'static str {
    match filename.rsplit('.').next().unwrap_or("").to_ascii_lowercase().as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "txt" | "log" | "md" => "text/plain; charset=utf-8",
        "json" => "application/json",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
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
