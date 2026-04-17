//! Misc commands — playbooks (stubbed), external tickets (via `gh` CLI),
//! restart, keybindings stubs, task_upload_artifact.
//!
//! These exist to keep the UI's command surface from hitting
//! `command not implemented` errors. Where Python Loom has a fuller
//! implementation backed by SQLite tables / rich state (playbooks,
//! external ticket sync), the Rust port returns empty/ok results until
//! that schema lands — the surface is intentionally a no-op so that
//! panels querying it render an empty-state gracefully.

use std::path::PathBuf;

use serde_json::{json, Value};

use super::{ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

// ------------------------------------------------------------------
// Playbooks
// ------------------------------------------------------------------

pub async fn get_playbooks(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = optional_str(req, "group").unwrap_or("").to_string();
    let status = optional_str(req, "status").unwrap_or("").to_string();
    Ok(json!({
        "type": "playbooks",
        "group": group,
        "status": status,
        "playbooks": [],
    }))
}

pub async fn get_playbook(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?;
    Ok(json!({
        "type": "error",
        "message": format!("Playbook '{id}' not found"),
    }))
}

pub async fn list_playbook_candidates(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    Ok(json!({
        "type": "playbook_candidates",
        "candidates": [],
    }))
}

pub async fn get_playbook_candidates(ctx: &CmdContext, req: &Value) -> CmdResult {
    list_playbook_candidates(ctx, req).await
}

pub async fn extract_playbook_candidates(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    Ok(json!({
        "type": "playbook_candidates_extracted",
        "count": 0,
    }))
}

pub async fn generate_playbook_draft(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    Ok(json!({
        "type": "error",
        "message": "Playbook generation is not yet available in the Rust port.",
    }))
}

pub async fn publish_playbook_draft(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    Ok(json!({
        "type": "error",
        "message": "Playbook publishing is not yet available in the Rust port.",
    }))
}

pub async fn discard_playbook_draft(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    Ok(json!({ "ok": true, "discarded": true }))
}

// ------------------------------------------------------------------
// External tickets (Jira / GitHub via `gh`)
// ------------------------------------------------------------------

pub async fn external_import_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let url = required_str(req, "url")?.to_string();
    let group = optional_str(req, "group").unwrap_or("").to_string();

    let issue = fetch_gh_issue(&url).await?;
    let title = issue
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or(url.as_str())
        .to_string();
    let body = issue
        .get("body")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let number = issue
        .get("number")
        .and_then(Value::as_i64)
        .map(|n| n.to_string())
        .unwrap_or_default();

    let task_id = {
        let mut st = ctx.state.lock().await;
        let mut task = loom_core::state::BoardTask::new_minimal(
            &format!(
                "ext-{}",
                if number.is_empty() {
                    chrono::Utc::now().timestamp_millis().to_string()
                } else {
                    number.clone()
                }
            ),
            &title,
        );
        task.group = group;
        task.description = body;
        task.provider = "github".into();
        task.external_url = url.clone();
        task.external_id = number.clone();
        st.upsert_task(task.clone())?;
        task.id
    };

    let task = {
        let st = ctx.state.lock().await;
        st.board_tasks.get(&task_id).cloned()
    };
    if let Some(t) = task {
        ctx.db.save_board_task(&t).await?;
    }
    super::flush(ctx).await;
    Ok(json!({
        "ok": true,
        "task_id": task_id,
        "title": title,
    }))
}

pub async fn external_link_task(ctx: &CmdContext, req: &Value) -> CmdResult {
    let task_id = required_str(req, "task_id")?.to_string();
    let url = required_str(req, "url")?.to_string();
    let kind = optional_str(req, "kind")
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            if url.contains("github.com") {
                "github".into()
            } else if url.contains("jira") {
                "jira".into()
            } else {
                "link".into()
            }
        });
    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&task_id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };
        task.provider = kind;
        task.external_url = url.clone();
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    super::flush(ctx).await;
    Ok(json!({"ok": true, "task_id": task_id, "url": url}))
}

pub async fn external_open_task(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let url = required_str(req, "url")?.to_string();
    // Best-effort: let the OS open it. Non-fatal if it fails (headless CI).
    let _ = tokio::process::Command::new("open").arg(&url).spawn();
    Ok(json!({"ok": true, "url": url}))
}

pub async fn external_push_task_status(_ctx: &CmdContext, req: &Value) -> CmdResult {
    // `gh issue close` / `gh issue reopen` bridge. We just return ok so
    // the frontend button works; the actual HTTP push happens from the
    // user's own `gh` CLI when they want to close the issue. Keeping
    // this a no-op avoids surprise state changes to real trackers.
    let task_id = optional_str(req, "task_id").unwrap_or("");
    Ok(json!({"ok": true, "task_id": task_id}))
}

pub async fn external_post_task_comment(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let url = optional_str(req, "url").unwrap_or("").to_string();
    let body = optional_str(req, "body").unwrap_or("").to_string();
    if url.is_empty() || body.is_empty() {
        return Ok(json!({"ok": false, "error": "url and body required"}));
    }
    // Try to parse `gh issue comment <URL>` compatible target.
    let out = tokio::process::Command::new("gh")
        .args(["issue", "comment", &url, "--body", &body])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("gh spawn: {e}")))?;
    if out.status.success() {
        Ok(json!({"ok": true, "url": url}))
    } else {
        Ok(json!({
            "ok": false,
            "error": String::from_utf8_lossy(&out.stderr).trim(),
        }))
    }
}

async fn fetch_gh_issue(url: &str) -> Result<Value, CmdError> {
    let out = tokio::process::Command::new("gh")
        .args([
            "issue",
            "view",
            url,
            "--json",
            "title,body,number,state,labels,url",
        ])
        .output()
        .await
        .map_err(|e| CmdError::BadRequest(format!("gh issue view spawn: {e}")))?;
    if !out.status.success() {
        return Err(CmdError::BadRequest(format!(
            "gh issue view failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        )));
    }
    serde_json::from_slice(&out.stdout)
        .map_err(|e| CmdError::BadRequest(format!("parse gh output: {e}")))
}

// ------------------------------------------------------------------
// Artifacts
// ------------------------------------------------------------------

pub async fn task_upload_artifact(ctx: &CmdContext, req: &Value) -> CmdResult {
    // Command-envelope variant. The multipart streaming endpoint lives
    // on `/api/task/{id}/upload` in `uploads.rs`; this command lets the
    // CLI / agents drop a text or pre-decoded blob via the same
    // command envelope the UI uses for everything else. Accepts either
    // `content` (raw text) or `bytes` (already-decoded binary as an
    // array of u8) — whichever the caller finds convenient.
    let task_id = required_str(req, "task_id")?.to_string();
    let filename = required_str(req, "filename")?.to_string();
    let bytes: Vec<u8> = if let Some(arr) = req.get("bytes").and_then(|v| v.as_array()) {
        arr.iter()
            .filter_map(|v| v.as_u64())
            .map(|n| n as u8)
            .collect()
    } else if let Some(content) = req.get("content").and_then(|v| v.as_str()) {
        content.as_bytes().to_vec()
    } else {
        return Err(CmdError::BadRequest(
            "task_upload_artifact requires 'content' or 'bytes'".into(),
        ));
    };
    let dir = loom_core::config::attachments_dir().join(&task_id);
    std::fs::create_dir_all(&dir).map_err(|e| CmdError::BadRequest(e.to_string()))?;
    let safe = sanitize_filename(&filename);
    let path = dir.join(&safe);
    std::fs::write(&path, &bytes).map_err(|e| CmdError::BadRequest(e.to_string()))?;
    let meta = json!({
        "filename": safe.to_string_lossy(),
        "size": bytes.len(),
        "content_type": guess_mime(&safe),
        "uploaded_at": chrono::Utc::now().timestamp_millis() as f64 / 1000.0,
    });
    let task = {
        let mut st = ctx.state.lock().await;
        let Some(mut task) = st.board_tasks.get(&task_id).cloned() else {
            return Err(CmdError::BadRequest(format!("task '{task_id}' not found")));
        };
        task.attachments.push(meta.clone());
        st.upsert_task(task.clone())?;
        task
    };
    ctx.db.save_board_task(&task).await?;
    super::flush(ctx).await;
    Ok(json!({"ok": true, "attachment": meta}))
}

fn sanitize_filename(value: &str) -> PathBuf {
    PathBuf::from(value.replace('/', "_").replace('\\', "_").replace(' ', "_"))
}

fn guess_mime(path: &PathBuf) -> &'static str {
    match path.extension().and_then(|e| e.to_str()) {
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("svg") => "image/svg+xml",
        Some("pdf") => "application/pdf",
        Some("txt") | Some("log") => "text/plain",
        Some("json") => "application/json",
        Some("md") => "text/markdown",
        _ => "application/octet-stream",
    }
}

// ------------------------------------------------------------------
// weaver_message — direct operator → weaver text injection
// ------------------------------------------------------------------

pub async fn weaver_message(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = required_str(req, "group")?.to_string();
    let message = required_str(req, "message")?.to_string();
    let weaver_id = {
        let st = ctx.state.lock().await;
        st.get_group_settings(&group).weaver_agent_id.clone()
    };
    if weaver_id.trim().is_empty() {
        return Err(CmdError::BadRequest(format!(
            "group '{group}' has no weaver"
        )));
    }
    super::dispatch::send_text_to_cell(ctx, &weaver_id, &message).await?;
    ok()
}

// ------------------------------------------------------------------
// Restart + keybindings (standalone no-ops)
// ------------------------------------------------------------------

pub async fn restart(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    // Schedule a process exec on the next tick so the current response
    // makes it back to the client before we vanish. The parent launcher
    // (or the user's `make deploy`) brings us back up.
    tokio::spawn(async {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt as _;
            let mut args = std::env::args();
            let program = args.next().unwrap_or_else(|| "loom-app".into());
            let rest: Vec<String> = args.collect();
            let _err = std::process::Command::new(program).args(rest).exec();
        }
        #[cfg(not(unix))]
        {
            std::process::exit(0);
        }
    });
    Ok(json!({"ok": true, "restarting": true}))
}

pub async fn suspend_keybindings(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    // iTerm2-specific — no-op in standalone. Accept so the Python UI
    // doesn't surface an error banner when talking to the Rust daemon.
    ok()
}

pub async fn resume_keybindings(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    ok()
}
