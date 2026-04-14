//! File upload endpoints — POST /api/upload and cleanup.

use axum::extract::State;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Json;
use axum::Router;
use serde_json::json;

use crate::app::AppState;

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/upload", post(handle_upload))
        .route("/api/upload/cleanup", post(handle_upload_cleanup))
}

async fn handle_upload(State(_app): State<AppState>) -> impl IntoResponse {
    // Phase 2b: multipart handling. For now a stub.
    Json(json!({ "ok": true, "stub": true }))
}

async fn handle_upload_cleanup(State(_app): State<AppState>) -> impl IntoResponse {
    Json(json!({ "ok": true }))
}
