//! File upload endpoints — POST /api/upload and cleanup.

use std::path::{Path, PathBuf};

use axum::extract::{Multipart, State};
use axum::http::StatusCode;
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

async fn handle_upload(
    State(_app): State<AppState>,
    mut multipart: Multipart,
) -> impl IntoResponse {
    let mut task_id = String::new();
    let mut saved = Vec::new();

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name == "task_id" {
            match field.text().await {
                Ok(value) => task_id = value.trim().to_string(),
                Err(err) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(json!({ "ok": false, "error": err.to_string() })),
                    )
                        .into_response()
                }
            }
            continue;
        }
        if name != "file" {
            continue;
        }
        if task_id.is_empty() {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({
                    "ok": false,
                    "error": "task_id must come before file parts",
                })),
            )
                .into_response();
        }

        let mut filename = sanitize_filename(field.file_name().unwrap_or("artifact.bin"));
        let content_type = field
            .content_type()
            .map(str::to_string)
            .unwrap_or_else(|| content_type_for_filename(&filename).to_string());
        let bytes = match field.bytes().await {
            Ok(bytes) => bytes,
            Err(err) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "ok": false, "error": err.to_string() })),
                )
                    .into_response()
            }
        };

        let attachment_dir = loom_core::config::attachments_dir().join(&task_id);
        if let Err(err) = tokio::fs::create_dir_all(&attachment_dir).await {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "ok": false, "error": err.to_string() })),
            )
                .into_response();
        }

        let path = dedupe_destination(&attachment_dir, &mut filename);
        if let Err(err) = tokio::fs::write(&path, &bytes).await {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "ok": false, "error": err.to_string() })),
            )
                .into_response();
        }

        saved.push(json!({
            "path": path.to_string_lossy(),
            "filename": filename,
            "mime_type": content_type,
            "size_bytes": bytes.len(),
        }));
    }

    if task_id.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "ok": false, "error": "missing task_id" })),
        )
            .into_response();
    }

    Json(json!({ "ok": true, "data": saved })).into_response()
}

async fn handle_upload_cleanup(
    State(app): State<AppState>,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    let task_id = payload
        .get("task_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if task_id.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "ok": false, "error": "missing task_id" })),
        )
            .into_response();
    }

    let task_exists = {
        let state = app.state.lock().await;
        state.board_tasks.contains_key(&task_id)
    };
    if !task_exists {
        let attachment_dir = loom_core::config::attachments_dir().join(&task_id);
        if attachment_dir.is_dir() {
            let _ = tokio::fs::remove_dir_all(attachment_dir).await;
        }
    }
    Json(json!({ "ok": true })).into_response()
}

fn sanitize_filename(value: &str) -> String {
    let fallback = "artifact.bin".to_string();
    let raw = Path::new(value)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(&fallback);
    let sanitized = raw.replace('/', "_").replace('\\', "_").replace(' ', "_");
    if sanitized.is_empty() {
        fallback
    } else {
        sanitized
    }
}

fn dedupe_destination(dir: &Path, filename: &mut String) -> PathBuf {
    let mut path = dir.join(&*filename);
    if !path.exists() {
        return path;
    }
    let stem = Path::new(filename)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("artifact")
        .to_string();
    let ext = Path::new(filename)
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!(".{value}"))
        .unwrap_or_default();
    let mut idx = 1;
    while path.exists() {
        *filename = format!("{stem}_{idx}{ext}");
        path = dir.join(&*filename);
        idx += 1;
    }
    path
}

fn content_type_for_filename(filename: &str) -> &'static str {
    let ext = Path::new(filename)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "txt" | "log" | "md" => "text/plain",
        "json" => "application/json",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
}
