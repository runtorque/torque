//! Memory CRUD — shared entries that agents and humans publish.
//!
//! Each entry is lightly scoped (project / group / pipeline / task). The UI
//! surfaces them in the Memory panel; MCP `loom_memory_*` tools give agents
//! read/write access.

use serde_json::{json, Value};
use uuid::Uuid;

use loom_core::delta::DeltaOp;
use loom_core::state::{MemoryEntry, MemoryLink};

use super::{flush, required_str, CmdContext, CmdError, CmdResult};

/// Create a new memory entry. Required: `entry_type`, `title`, `content`.
/// Optional: group, project_key, scope_kind, scope_ref, task_id, pinned,
/// source_kind, source_id, source_name, retention_kind, expires_at.
pub async fn publish(ctx: &CmdContext, req: &Value) -> CmdResult {
    let entry_type = required_str(req, "entry_type")?.to_string();
    let title = required_str(req, "title")?.to_string();
    let content = req
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if title.chars().count() > 200 {
        return Err(CmdError::BadRequest("title exceeds 200 chars".into()));
    }
    if content.chars().count() > 4000 {
        return Err(CmdError::BadRequest("content exceeds 4000 chars".into()));
    }

    let now = chrono::Utc::now().to_rfc3339();
    let entry = MemoryEntry {
        id: Uuid::new_v4().to_string(),
        project_key: optstr(req, "project_key"),
        group_name: optstr(req, "group"),
        scope_kind: optstr(req, "scope_kind"),
        scope_ref: optstr(req, "scope_ref"),
        entry_type,
        title,
        content,
        pinned: req.get("pinned").and_then(|v| v.as_bool()).unwrap_or(false),
        task_id: optstr(req, "task_id"),
        source_kind: optstr(req, "source_kind"),
        source_id: optstr(req, "source_id"),
        source_name: optstr(req, "source_name"),
        retention_kind: req
            .get("retention_kind")
            .and_then(|v| v.as_str())
            .unwrap_or("durable")
            .to_string(),
        expires_at: optstr(req, "expires_at"),
        created_at: now.clone(),
        updated_at: now,
        links: Vec::new(),
    };

    {
        let mut st = ctx.state.lock().await;
        st.memory_entries.insert(entry.id.clone(), entry.clone());
        st.emit(DeltaOp::MemoryUpsert(
            serde_json::to_value(&entry).unwrap_or(Value::Null),
        ));
    }
    ctx.db.save_memory_entry(&entry).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "id": entry.id }))
}

/// List entries. Filters: `group`, `project_key`, `scope_kind`, `scope_ref`,
/// `entry_type`, `task_id`, `pinned_only`, `linked_target_kind`,
/// `linked_target_ref`, `search` (substring match, case-insensitive).
pub async fn list(ctx: &CmdContext, req: &Value) -> CmdResult {
    let group = optstr(req, "group");
    let project_key = optstr(req, "project_key");
    let scope_kind = optstr(req, "scope_kind");
    let scope_ref = optstr(req, "scope_ref");
    let entry_type = optstr(req, "entry_type");
    let task_id = optstr(req, "task_id");
    let pinned_only = req
        .get("pinned_only")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let linked_target_kind = optstr(req, "linked_target_kind");
    let linked_target_ref = optstr(req, "linked_target_ref");
    let search = optstr(req, "search").to_lowercase();

    let st = ctx.state.lock().await;
    let mut matches: Vec<&MemoryEntry> = st
        .memory_entries
        .values()
        .filter(|e| {
            (group.is_empty() || e.group_name == group)
                && (project_key.is_empty() || e.project_key == project_key)
                && (scope_kind.is_empty() || e.scope_kind == scope_kind)
                && (scope_ref.is_empty() || e.scope_ref == scope_ref)
                && (entry_type.is_empty() || e.entry_type == entry_type)
                && (task_id.is_empty() || e.task_id == task_id)
                && (!pinned_only || e.pinned)
                && (linked_target_kind.is_empty()
                    || e.links.iter().any(|l| {
                        l.target_kind == linked_target_kind
                            && (linked_target_ref.is_empty() || l.target_ref == linked_target_ref)
                    }))
                && (search.is_empty()
                    || e.title.to_lowercase().contains(&search)
                    || e.content.to_lowercase().contains(&search))
        })
        .collect();
    // Pinned first, then durable before transient, then newest first.
    matches.sort_by(|a, b| {
        b.pinned
            .cmp(&a.pinned)
            .then_with(|| a.retention_kind.cmp(&b.retention_kind))
            .then_with(|| b.created_at.cmp(&a.created_at))
            .then_with(|| a.id.cmp(&b.id))
    });
    let entries: Vec<Value> = matches
        .into_iter()
        .map(|e| serde_json::to_value(e).unwrap_or(Value::Null))
        .collect();
    Ok(json!({ "entries": entries }))
}

pub async fn read(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let st = ctx.state.lock().await;
    let entry = st
        .memory_entries
        .get(&id)
        .ok_or_else(|| CmdError::BadRequest(format!("memory entry '{id}' not found")))?;
    Ok(json!({ "entry": entry }))
}

pub async fn pin(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_pinned(ctx, req, true).await
}

pub async fn unpin(ctx: &CmdContext, req: &Value) -> CmdResult {
    set_pinned(ctx, req, false).await
}

async fn set_pinned(ctx: &CmdContext, req: &Value, pinned: bool) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let entry = {
        let mut st = ctx.state.lock().await;
        let Some(entry) = st.memory_entries.get_mut(&id) else {
            return Err(CmdError::BadRequest(format!("memory entry '{id}' not found")));
        };
        entry.pinned = pinned;
        entry.updated_at = chrono::Utc::now().to_rfc3339();
        let clone = entry.clone();
        st.emit(DeltaOp::MemoryUpsert(
            serde_json::to_value(&clone).unwrap_or(Value::Null),
        ));
        clone
    };
    ctx.db.save_memory_entry(&entry).await?;
    flush(ctx).await;
    Ok(json!({ "ok": true, "pinned": pinned }))
}

/// Attach (or detach) a link from an entry to a target object.
/// Payload: `{ "id": "<entry>", "target_kind": "task", "target_ref": "eng-1" }`.
/// Optional `detach: true` removes the link instead of adding it.
pub async fn link(ctx: &CmdContext, req: &Value) -> CmdResult {
    let id = required_str(req, "id")?.to_string();
    let target_kind = required_str(req, "target_kind")?.to_string();
    let target_ref = required_str(req, "target_ref")?.to_string();
    let detach = req.get("detach").and_then(|v| v.as_bool()).unwrap_or(false);

    let entry = {
        let mut st = ctx.state.lock().await;
        let Some(entry) = st.memory_entries.get_mut(&id) else {
            return Err(CmdError::BadRequest(format!("memory entry '{id}' not found")));
        };
        if detach {
            entry
                .links
                .retain(|l| !(l.target_kind == target_kind && l.target_ref == target_ref));
        } else if !entry
            .links
            .iter()
            .any(|l| l.target_kind == target_kind && l.target_ref == target_ref)
        {
            entry.links.push(MemoryLink {
                entry_id: id.clone(),
                target_kind: target_kind.clone(),
                target_ref: target_ref.clone(),
                created_at: chrono::Utc::now().to_rfc3339(),
            });
        }
        entry.updated_at = chrono::Utc::now().to_rfc3339();
        let clone = entry.clone();
        st.emit(DeltaOp::MemoryUpsert(
            serde_json::to_value(&clone).unwrap_or(Value::Null),
        ));
        clone
    };

    // Persist: bump the entry row (denormalized links go along in the JSON
    // blob), and add/remove the normalized row in memory_links.
    ctx.db.save_memory_entry(&entry).await?;
    if detach {
        // Full refresh: wipe + rewrite the remaining links.
        ctx.db.delete_memory_links(&entry.id).await?;
        for l in &entry.links {
            ctx.db.save_memory_link(l).await?;
        }
    } else {
        let link = entry
            .links
            .iter()
            .find(|l| l.target_kind == target_kind && l.target_ref == target_ref)
            .cloned();
        if let Some(l) = link {
            ctx.db.save_memory_link(&l).await?;
        }
    }
    flush(ctx).await;
    Ok(json!({ "ok": true, "detached": detach }))
}

fn optstr(req: &Value, key: &str) -> String {
    req.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}
