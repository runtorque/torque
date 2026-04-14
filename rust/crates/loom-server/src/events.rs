//! Hook receiver — POST /events
//!
//! Claude Code / Codex post hook payloads here. The handler classifies the
//! payload via the registered adapter and updates agent state.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Json;
use axum::Router;
use serde_json::json;

use crate::app::AppState;

pub fn routes() -> Router<AppState> {
    Router::new().route("/events", post(handle_event))
}

async fn handle_event(
    State(_app): State<AppState>,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    // Phase 5: classify via adapter, update cell.
    tracing::debug!("hook payload: {}", payload);
    (StatusCode::OK, Json(json!({ "ok": true })))
}
