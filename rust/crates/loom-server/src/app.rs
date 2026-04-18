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
use tower_http::set_header::SetResponseHeaderLayer;

use crate::pty_backend::PtyBackend;
use crate::terminal_bridge::TerminalBridgeClient;
use crate::weaver_buffer::WeaverEventBuffer;
use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum UiAgentInput {
    Text(String),
    Submit,
}

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
    inner: Arc<StdMutex<HashMap<String, mpsc::UnboundedSender<UiAgentInput>>>>,
}

impl UiAgentRegistry {
    /// UI registers an agent. Returns the receiver to be drained from the
    /// main thread.
    pub fn register(&self, agent_id: String) -> mpsc::UnboundedReceiver<UiAgentInput> {
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

    /// Returns `true` iff the input was queued to a live UI-attached agent.
    fn send_input(&self, agent_id: &str, input: UiAgentInput) -> bool {
        let map = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(tx) = map.get(agent_id) {
            tx.send(input).is_ok()
        } else {
            false
        }
    }

    /// Returns `true` iff text was queued to a live UI-attached agent.
    pub fn send_text(&self, agent_id: &str, text: String) -> bool {
        self.send_input(agent_id, UiAgentInput::Text(text))
    }

    /// Returns `true` iff a submit action was queued to a live UI-attached agent.
    pub fn send_submit(&self, agent_id: &str) -> bool {
        self.send_input(agent_id, UiAgentInput::Submit)
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

#[derive(Clone, Default)]
pub struct TerminalHub {
    inner: Arc<Mutex<HashMap<String, TerminalSessionState>>>,
}

struct TerminalSessionState {
    session_id: String,
    buffer: String,
    tx: tokio::sync::broadcast::Sender<serde_json::Value>,
}

impl Default for TerminalSessionState {
    fn default() -> Self {
        let (tx, _) = tokio::sync::broadcast::channel(256);
        Self {
            session_id: String::new(),
            buffer: String::new(),
            tx,
        }
    }
}

impl TerminalHub {
    async fn ensure(&self, cell_id: &str) -> tokio::sync::broadcast::Sender<serde_json::Value> {
        let mut inner = self.inner.lock().await;
        inner.entry(cell_id.to_string()).or_default().tx.clone()
    }

    pub async fn subscribe(
        &self,
        cell_id: &str,
    ) -> tokio::sync::broadcast::Receiver<serde_json::Value> {
        self.ensure(cell_id).await.subscribe()
    }

    pub async fn snapshot(&self, cell_id: &str) -> (String, String) {
        let mut inner = self.inner.lock().await;
        let entry = inner.entry(cell_id.to_string()).or_default();
        (entry.session_id.clone(), entry.buffer.clone())
    }

    pub async fn set_session(&self, cell_id: &str, session_id: Option<String>, clear_buffer: bool) {
        let mut inner = self.inner.lock().await;
        let entry = inner.entry(cell_id.to_string()).or_default();
        entry.session_id = session_id.unwrap_or_default();
        if clear_buffer {
            entry.buffer.clear();
        }
    }

    pub async fn append_output(&self, cell_id: &str, session_id: &str, text: &str) {
        if text.is_empty() {
            return;
        }
        let mut inner = self.inner.lock().await;
        let entry = inner.entry(cell_id.to_string()).or_default();
        if entry.session_id != session_id {
            entry.session_id = session_id.to_string();
            entry.buffer.clear();
        }
        entry.buffer.push_str(text);
        const MAX_BUFFER_BYTES: usize = 200_000;
        if entry.buffer.len() > MAX_BUFFER_BYTES {
            let drop_len = entry.buffer.len() - MAX_BUFFER_BYTES;
            let trim_at = entry
                .buffer
                .char_indices()
                .map(|(idx, _)| idx)
                .find(|idx| *idx >= drop_len)
                .unwrap_or(0);
            entry.buffer.drain(..trim_at);
        }
        let _ = entry.tx.send(serde_json::json!({
            "type": "output",
            "cell_id": cell_id,
            "session_id": session_id,
            "data": text,
        }));
    }
}

#[derive(Clone)]
pub struct AppState {
    pub db: LoomDb,
    pub state: Arc<Mutex<MatrixState>>,
    pub bus: EventBus,
    /// PTY backend for dispatch & send_text. `None` in environments where the
    /// engine runs headless without a terminal layer (e.g. some unit tests).
    /// In standalone mode this is the supervised variant, which delegates
    /// PTY ownership to the detached `loom-pty-supervisor` sidecar so
    /// sessions survive daemon restarts.
    pub pty: Option<PtyBackend>,
    /// Registry of UI-attached agents. Dispatch routes through this when the
    /// target agent is mounted in the native window.
    pub ui_agents: UiAgentRegistry,
    /// Optional HTTP client for the thin Python+iTerm2 bridge runtime.
    pub terminal_bridge: TerminalBridgeClient,
    pub terminals: TerminalHub,
    pub weaver_buffer: WeaverEventBuffer,
    pub notifier: crate::notifications::NotificationManager,
}

pub struct ServerHandle {
    pub addr: SocketAddr,
    pub shutdown_tx: tokio::sync::oneshot::Sender<()>,
    pub app_state: AppState,
}

pub fn base_router() -> Router<AppState> {
    // Static assets (JS/CSS) get `Cache-Control: no-cache` so browser
    // always revalidates against the server. Without this, tower-http's
    // defaults combined with Chrome's heuristic caching produced hours
    // of "my fix isn't loading" confusion during dev. `no-cache` still
    // allows 304 revalidation via ETag, so the wire cost is minimal.
    let static_service = ServeDir::new(frontend_static_dir());
    let no_cache = SetResponseHeaderLayer::overriding(
        header::CACHE_CONTROL,
        axum::http::HeaderValue::from_static("no-cache"),
    );
    Router::new()
        .route("/", get(handle_index))
        .route("/attachments/{task_id}/{filename}", get(handle_attachment))
        .nest_service("/static", tower::ServiceBuilder::new()
            .layer(no_cache)
            .service(static_service))
        .merge(crate::ws::routes())
        .merge(crate::commands::routes())
        .merge(crate::terminal_bridge::routes())
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

    // Recompute task health on every throttled broadcast so any burst of
    // mutations picks up a fresh `health_state` before the frame goes out.
    // Mirrors Python `EventBus._fire_broadcast` coalescing
    // `recompute_task_health` with the trailing-edge broadcast.
    {
        let db_hook = db.clone();
        let hook: loom_core::events::PreFlushHook = std::sync::Arc::new(move |state_arc| {
            let db_hook = db_hook.clone();
            Box::pin(async move {
                let changed = {
                    let mut st = state_arc.lock().await;
                    loom_weaver::task_health::recompute_task_health(&mut st, None)
                };
                if changed.is_empty() {
                    return;
                }
                let tasks = {
                    let st = state_arc.lock().await;
                    changed
                        .iter()
                        .filter_map(|tid| st.board_tasks.get(tid).cloned())
                        .collect::<Vec<_>>()
                };
                for t in tasks {
                    if let Err(err) = db_hook.save_board_task(&t).await {
                        tracing::warn!(
                            task = %t.id,
                            ?err,
                            "task health save failed"
                        );
                    }
                }
            })
        });
        bus.set_pre_flush_hook(hook).await;
    }

    let terminals = TerminalHub::default();
    let (pty, mut pty_rx) = build_pty_backend(&config).await;

    let app_state = AppState {
        db: db.clone(),
        state: state.clone(),
        bus: bus.clone(),
        pty: Some(pty.clone()),
        ui_agents: UiAgentRegistry::default(),
        terminal_bridge: TerminalBridgeClient::from_env(),
        terminals: terminals.clone(),
        weaver_buffer: WeaverEventBuffer::default(),
        notifier: crate::notifications::NotificationManager::new(),
    };

    // PTY event pump → state mutations.
    {
        let app_clone = app_state.clone();
        tokio::spawn(async move {
            while let Some(evt) = pty_rx.recv().await {
                handle_pty_event(&app_clone, evt).await;
            }
        });
    }

    // When running on the supervised backend, reconcile the daemon's
    // persisted agents against the supervisor's live session list:
    //   * session alive in supervisor + cell persisted → re-adopt the
    //     existing session so output flows and the UI shows it as live.
    //   * cell persisted, session gone → mark the cell stopped so the
    //     UI doesn't pretend a dead terminal is still typeable.
    if let Some(supervised) = pty.supervised() {
        reconcile_supervised_sessions(&app_state, supervised.clone()).await;
    }

    // Spawn scheduler + periodic weaver buffer drain + worktree refresher.
    crate::scheduler::spawn(state.clone(), db.clone(), bus.clone());
    crate::scheduler::spawn_weaver_flusher(app_state.clone());
    crate::scheduler::spawn_worktree_refresher(app_state.clone());

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

/// Decide which PTY backend to run with. Honours `LOOM_PTY_BACKEND`:
///   * `"supervised"` or unset + `LOOM_STANDALONE=1` → try supervised,
///     fall back to local on failure with a warn log.
///   * `"local"` → always local.
async fn build_pty_backend(
    config: &ServerConfig,
) -> (PtyBackend, tokio::sync::mpsc::Receiver<loom_pty::PtyEvent>) {
    let explicit = std::env::var("LOOM_PTY_BACKEND").ok();
    let standalone = std::env::var("LOOM_STANDALONE")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    let want_supervised = match explicit.as_deref() {
        Some("supervised") => true,
        Some("local") => false,
        _ => standalone,
    };
    if want_supervised {
        match connect_supervised(&config.data_dir).await {
            Ok((backend, rx)) => return (backend, rx),
            Err(err) => {
                tracing::warn!(
                    ?err,
                    "supervised PTY backend unavailable, falling back to local"
                );
            }
        }
    }
    PtyBackend::new_local()
}

async fn connect_supervised(
    data_dir: &std::path::Path,
) -> Result<(PtyBackend, tokio::sync::mpsc::Receiver<loom_pty::PtyEvent>)> {
    use loom_pty_supervisor::{ensure_running, PtySupervisorClient, SupervisedPtyBackend, SupervisorPaths};
    let paths = SupervisorPaths::from_data_dir(data_dir.to_path_buf());
    let pong = ensure_running(&paths, std::time::Duration::from_secs(5)).await?;
    tracing::info!(
        socket = %paths.socket.display(),
        supervisor_pid = pong.pid,
        "PTY supervisor ready"
    );
    let client = PtySupervisorClient::connect(&paths.socket).await?;
    let (backend, rx) = SupervisedPtyBackend::new(client);
    let backend = PtyBackend::Supervised(Arc::new(backend));
    Ok((backend, rx))
}

/// On daemon startup with the supervised backend, reconcile persisted
/// agents against the supervisor's live sessions. Re-adopt survivors;
/// mark dead ones stopped so the UI doesn't pretend they're live.
async fn reconcile_supervised_sessions(
    app: &AppState,
    supervised: Arc<loom_pty_supervisor::SupervisedPtyBackend>,
) {
    // Pull live session ids from the supervisor — may have zero if the
    // supervisor itself was just spawned fresh.
    let live: std::collections::HashSet<String> = match supervised.client().list().await {
        Ok(list) => list
            .into_iter()
            .filter_map(|v| {
                v.get("session_id")
                    .and_then(|x| x.as_str())
                    .map(String::from)
            })
            .collect(),
        Err(err) => {
            tracing::warn!(?err, "supervisor list failed during startup reconcile");
            return;
        }
    };

    // Snapshot the cells we persisted as having a live session.
    let cells: Vec<(String, String)> = {
        let st = app.state.lock().await;
        st.agents
            .values()
            .filter(|c| c.session_id.is_some() && c.status != "stopped")
            .map(|c| (c.id.clone(), c.session_id.clone().unwrap_or_default()))
            .collect()
    };

    for (cell_id, session_id) in cells {
        if session_id.is_empty() {
            continue;
        }
        if live.contains(&session_id) {
            // Session survived; re-subscribe so output flows again.
            if let Err(err) = supervised.adopt(&cell_id, &session_id).await {
                tracing::warn!(?err, agent = %cell_id, "adopt session failed");
                mark_stopped(app, &cell_id).await;
            } else {
                tracing::info!(agent = %cell_id, session = %session_id, "re-adopted supervised session");
            }
        } else {
            mark_stopped(app, &cell_id).await;
        }
    }
}

async fn mark_stopped(app: &AppState, cell_id: &str) {
    let agent = {
        let mut st = app.state.lock().await;
        if let Some(cell) = st.agents.get_mut(cell_id) {
            cell.status = "stopped".into();
            cell.activity.clear();
            cell.activity_detail.clear();
        }
        st.emit_agent(cell_id);
        st.agents.get(cell_id).cloned()
    };
    if let Some(agent) = agent {
        let _ = app.db.save_agent(&agent).await;
    }
    let mut st = app.state.lock().await;
    if let Some((seq, ops)) = st.drain_deltas() {
        app.bus
            .send(loom_core::events::OutMessage::Delta { seq, ops });
    }
}

pub(crate) fn repo_root() -> PathBuf {
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
    let path = loom_core::config::attachments_dir()
        .join(&task_id)
        .join(&filename);
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
    match filename
        .rsplit('.')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
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

async fn handle_pty_event(app: &AppState, evt: loom_pty::PtyEvent) {
    use loom_core::events::OutMessage;
    use loom_pty::PtyEvent;

    let cell_id = match &evt {
        PtyEvent::Spawned { cell_id, .. }
        | PtyEvent::Output { cell_id, .. }
        | PtyEvent::Exited { cell_id, .. }
        | PtyEvent::Error { cell_id, .. } => cell_id.clone(),
    };

    let (agent_snapshot, panel_event, clear_buffer, pending_output, is_output_only) = {
        let mut st = app.state.lock().await;
        let Some(cell) = st.agents.get_mut(&cell_id) else {
            return;
        };
        let mut panel_event = None::<(String, String, String)>;
        let mut clear_buffer = false;
        let mut pending_output = None::<(String, String)>;
        let mut is_output_only = false;
        match evt {
            PtyEvent::Spawned { pid, .. } => {
                cell.status = "running".into();
                cell.session_id = Some(pid.to_string());
                clear_buffer = true;
                panel_event = Some((
                    "agent_started".into(),
                    "Session started".into(),
                    cell.current_task_id.clone(),
                ));
            }
            PtyEvent::Output { text, .. } => {
                // High-frequency path — every byte burst hits here. Do
                // NOT update state fields that would trigger a delta
                // broadcast (e.g. `last_event_at`). Serialising the whole
                // AgentCell on every chunk overwhelmed the WS bus and
                // starved command dispatch, which was the root cause of
                // the "UI freezes while agent runs / buttons stop
                // working" symptom. Terminal output still streams to WS
                // subscribers through the `TerminalHub` below. `text` is
                // already a valid UTF-8 String — the PTY reader thread
                // runs an incremental decoder that buffers partial
                // multibyte sequences across reads.
                if let Some(session_id) = cell.session_id.clone() {
                    pending_output = Some((session_id, text));
                }
                is_output_only = true;
            }
            PtyEvent::Exited { status, .. } => {
                cell.status = if status == 0 {
                    "stopped".into()
                } else {
                    "error".into()
                };
                cell.session_id = None;
                panel_event = Some((
                    "agent_finished".into(),
                    if status == 0 {
                        "Session ended".into()
                    } else {
                        format!("Session exited with status {status}")
                    },
                    cell.current_task_id.clone(),
                ));
            }
            PtyEvent::Error { message, .. } => {
                cell.error_message = message;
                cell.status = "error".into();
                cell.needs_attention = true;
                panel_event = Some((
                    "agent_error".into(),
                    cell.error_message.clone(),
                    cell.current_task_id.clone(),
                ));
            }
        }
        let agent_snapshot = cell.clone();
        if !is_output_only {
            st.emit_agent(&cell_id);
        }
        (
            agent_snapshot,
            panel_event,
            clear_buffer,
            pending_output,
            is_output_only,
        )
    };
    if clear_buffer {
        app.terminals
            .set_session(&cell_id, agent_snapshot.session_id.clone(), true)
            .await;
    }
    if let Some((session_id, text)) = pending_output {
        app.terminals
            .append_output(&cell_id, &session_id, &text)
            .await;
    }
    if agent_snapshot.session_id.is_none() {
        app.terminals.set_session(&cell_id, None, false).await;
    }
    if let Some((kind, message, task_id)) = panel_event {
        let _ = crate::commands::compat::record_panel_event(
            &app.state,
            &app.db,
            &app.weaver_buffer,
            &kind,
            &agent_snapshot.id,
            &agent_snapshot.name,
            &agent_snapshot.group,
            &message,
            &task_id,
            false,
        )
        .await;
    }
    // Skip the DB save + delta broadcast + weaver flush for high-frequency
    // Output events — nothing meaningful changed on the cell, and both
    // paths would otherwise drown the WS bus (see comment above).
    if is_output_only {
        return;
    }
    let _ = app.db.save_agent(&agent_snapshot).await;
    let mut st = app.state.lock().await;
    if let Some((seq, ops)) = st.drain_deltas() {
        app.bus.send(OutMessage::Delta { seq, ops });
    }
    drop(st);
    // CRITICAL: spawn the flush instead of awaiting it. `flush_group` can
    // call `wait_for_local_pty_ready`, which polls the weaver's terminal
    // buffer for up to 30 seconds. That buffer is only populated when
    // *this* task drains the PTY `mpsc` channel — so awaiting here
    // deadlocks the event pump: every subsequent `PtyEvent::Output` for
    // 30s piles up unread behind a flush that's waiting for bytes that
    // can't arrive. Symptom: agents freshly spawned appear to produce no
    // output for ~30s. The scheduler's 5s periodic flusher picks up any
    // events this task might miss by skipping the inline flush.
    let app_for_flush = app.clone();
    tokio::spawn(async move {
        app_for_flush
            .weaver_buffer
            .maybe_flush_due_for_app(&app_for_flush)
            .await;
    });
}
