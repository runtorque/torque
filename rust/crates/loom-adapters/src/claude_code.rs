//! Claude Code adapter — full integration via HTTP hooks, MCP config, and
//! slash-command skills. Port of `loom/adapters/claude_code.py`.

use std::path::Path;

use anyhow::Result;
use async_trait::async_trait;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::fs;

use super::base::{AgentAdapter, AgentEvent, InputReadyPolicy};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LOOM_SKILL_PREFIX: &str = "loom-";
const HOOK_TIMEOUT_SECONDS: u32 = 3;

static LOOM_EVENT_URL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"http://(?:localhost|127\.0\.0\.1):\d+/events").expect("valid loom hook regex")
});

// Three slash-command skills mirrored from the Python adapter. Kept as static
// strings so the content matches Python byte-for-byte (modulo path separators).
const SKILL_LOOM_STATUS: &str = "---\n\
name: loom-status\n\
description: Show current Loom agent context, linked task, and pipeline state\n\
allowed-tools: mcp__loom__loom_context\n\
---\n\
\n\
Show the current Loom agent context using the loom_context MCP tool.\n\
\n\
Format the output clearly:\n\
- Agent name, group, and status\n\
- Current task title, lane, and action\n\
- Pipeline info (parent task, depth) if this is a derived task\n\
- Worktree branch and diff stats if in a worktree\n";

const SKILL_LOOM_BOARD: &str = "---\n\
name: loom-board\n\
description: Show the Loom task board with all lanes and tasks\n\
allowed-tools: Bash\n\
---\n\
\n\
Show the current Loom task board.\n\
\n\
## Board state\n\
!`loom board list`\n\
\n\
Summarize the board state: how many tasks in each lane,\n\
any tasks that need attention (blocked/error labels),\n\
and what's currently in progress.\n";

const SKILL_LOOM_DONE: &str = "---\n\
name: loom-done\n\
description: Mark the current Loom task as complete\n\
allowed-tools: mcp__loom__loom_done\n\
argument-hint: [completion message]\n\
---\n\
\n\
Mark the current task as complete using the loom_done MCP tool.\n\
\n\
If $ARGUMENTS is provided, use it as the completion message.\n\
Otherwise, write a brief summary of what was accomplished.\n";

const SKILLS: &[(&str, &str)] = &[
    ("loom-status", SKILL_LOOM_STATUS),
    ("loom-board", SKILL_LOOM_BOARD),
    ("loom-done", SKILL_LOOM_DONE),
];

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

fn current_loom_port() -> String {
    std::env::var("LOOM_PORT")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "18932".to_string())
}

fn loom_hook_url() -> String {
    format!("http://localhost:{}/events", current_loom_port())
}

fn is_loom_event_target(value: &str) -> bool {
    LOOM_EVENT_URL_RE.is_match(value)
}

/// A hook entry (the object with `hooks: [...]` + optional `matcher`) counts as
/// Loom-managed if any of its inner hooks targets a `/events` endpoint — either
/// as an `http` hook's `url` or as a `command` hook's `command` body.
fn is_loom_hook_entry(entry: &Value) -> bool {
    let Some(hooks) = entry.get("hooks").and_then(Value::as_array) else {
        return false;
    };
    hooks.iter().any(|hook| {
        if let Some(url) = hook.get("url").and_then(Value::as_str) {
            if is_loom_event_target(url) {
                return true;
            }
        }
        if hook.get("type").and_then(Value::as_str) == Some("command") {
            if let Some(cmd) = hook.get("command").and_then(Value::as_str) {
                if is_loom_event_target(cmd) {
                    return true;
                }
            }
        }
        false
    })
}

// ---------------------------------------------------------------------------
// Hook config builder
// ---------------------------------------------------------------------------

fn http_hook_entry(matcher: Option<&str>) -> Value {
    let hook = json!({
        "type": "http",
        "url": loom_hook_url(),
        "timeout": HOOK_TIMEOUT_SECONDS,
        "headers": { "X-Loom-Cell-Id": "$LOOM_CELL_ID" },
        "allowedEnvVars": ["LOOM_CELL_ID"],
    });
    let mut entry = Map::new();
    entry.insert("hooks".into(), Value::Array(vec![hook]));
    if let Some(m) = matcher {
        entry.insert("matcher".into(), Value::String(m.to_string()));
    }
    Value::Object(entry)
}

fn command_hook_entry(matcher: Option<&str>) -> Value {
    // SessionStart, WorktreeCreate, and WorktreeRemove silently ignore HTTP
    // hooks — they only accept `type: "command"`. We curl back to Loom.
    let cmd = format!(
        "curl -s -X POST {url} \
         -H \"Content-Type: application/json\" \
         -H \"X-Loom-Cell-Id: $LOOM_CELL_ID\" \
         -d \"$(cat)\" > /dev/null 2>&1",
        url = loom_hook_url()
    );
    let hook = json!({
        "type": "command",
        "timeout": HOOK_TIMEOUT_SECONDS,
        "command": cmd,
    });
    let mut entry = Map::new();
    entry.insert("hooks".into(), Value::Array(vec![hook]));
    if let Some(m) = matcher {
        entry.insert("matcher".into(), Value::String(m.to_string()));
    }
    Value::Object(entry)
}

/// Build the `{"hooks": {...}}` block that we merge into settings.local.json.
fn build_hook_config() -> Value {
    json!({
        "hooks": {
            "SessionStart": [command_hook_entry(None)],
            "PreToolUse": [http_hook_entry(Some(".*"))],
            "PostToolUse": [http_hook_entry(Some(".*"))],
            "PostToolUseFailure": [http_hook_entry(Some(".*"))],
            "Notification": [http_hook_entry(Some(".*"))],
            "Stop": [http_hook_entry(None)],
            "SubagentStart": [http_hook_entry(Some(".*"))],
            "SubagentStop": [http_hook_entry(Some(".*"))],
            "StopFailure": [http_hook_entry(Some(".*"))],
        }
    })
}

// ---------------------------------------------------------------------------
// File IO helpers
// ---------------------------------------------------------------------------

async fn read_json_if_present(path: &Path) -> Result<Option<Value>> {
    match fs::read_to_string(path).await {
        Ok(text) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                return Ok(None);
            }
            Ok(Some(serde_json::from_str(trimmed)?))
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err.into()),
    }
}

/// Strip any existing Loom-managed hook entries in-place. Returns the pruned
/// `hooks` map (may be empty).
fn prune_loom_hooks(hooks: &mut Map<String, Value>) {
    let events: Vec<String> = hooks.keys().cloned().collect();
    for event in events {
        let Some(Value::Array(entries)) = hooks.get_mut(&event) else {
            continue;
        };
        entries.retain(|e| !is_loom_hook_entry(e));
        if entries.is_empty() {
            hooks.remove(&event);
        }
    }
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

pub struct ClaudeCodeAdapter;

impl ClaudeCodeAdapter {
    /// Write (or merge into) `<working_dir>/.claude/settings.local.json` so
    /// Claude Code posts hook events to Loom. Idempotent: re-running strips
    /// stale Loom hooks (by URL marker) before re-adding fresh ones, so port
    /// changes and re-dispatches stay clean.
    pub async fn install_hooks(&self, working_dir: &Path) -> Result<()> {
        let claude_dir = working_dir.join(".claude");
        fs::create_dir_all(&claude_dir).await?;
        let settings_file = claude_dir.join("settings.local.json");

        let mut existing = read_json_if_present(&settings_file)
            .await?
            .unwrap_or_else(|| Value::Object(Map::new()));
        let existing_obj = match existing.as_object_mut() {
            Some(obj) => obj,
            None => {
                // Non-object file — replace entirely.
                existing = Value::Object(Map::new());
                existing.as_object_mut().unwrap()
            }
        };

        let mut hooks_map = existing_obj
            .remove("hooks")
            .and_then(|v| match v {
                Value::Object(m) => Some(m),
                _ => None,
            })
            .unwrap_or_default();

        prune_loom_hooks(&mut hooks_map);

        // Add fresh Loom hooks.
        let fresh = build_hook_config();
        if let Some(new_hooks) = fresh.get("hooks").and_then(Value::as_object) {
            for (event, entries) in new_hooks {
                let Some(entries_arr) = entries.as_array() else {
                    continue;
                };
                let bucket = hooks_map
                    .entry(event.clone())
                    .or_insert_with(|| Value::Array(Vec::new()));
                if let Some(arr) = bucket.as_array_mut() {
                    arr.extend(entries_arr.iter().cloned());
                }
            }
        }

        existing_obj.insert("hooks".into(), Value::Object(hooks_map));
        let out = serde_json::to_string_pretty(&existing)?;
        fs::write(&settings_file, out).await?;
        Ok(())
    }

    /// Strip Loom-managed hook entries back out. Deletes the file entirely if
    /// nothing non-Loom remains.
    pub async fn uninstall_hooks(&self, working_dir: &Path) -> Result<()> {
        let settings_file = working_dir.join(".claude").join("settings.local.json");
        let Some(mut existing) = read_json_if_present(&settings_file).await? else {
            // Missing or empty — if the file exists but is empty, remove it.
            if fs::metadata(&settings_file).await.is_ok() {
                let _ = fs::remove_file(&settings_file).await;
            }
            return Ok(());
        };
        let Some(existing_obj) = existing.as_object_mut() else {
            return Ok(());
        };

        if let Some(Value::Object(mut hooks_map)) = existing_obj.remove("hooks") {
            prune_loom_hooks(&mut hooks_map);
            if !hooks_map.is_empty() {
                existing_obj.insert("hooks".into(), Value::Object(hooks_map));
            }
        }

        if existing_obj.is_empty() {
            let _ = fs::remove_file(&settings_file).await;
        } else {
            let out = serde_json::to_string_pretty(&existing)?;
            fs::write(&settings_file, out).await?;
        }
        Ok(())
    }

    /// Write (or merge into) `<working_dir>/.mcp.json` so Claude Code exposes
    /// the Loom MCP tools. `${LOOM_PORT:-18932}` + `${LOOM_CELL_ID}` are
    /// interpolated by Claude Code itself at launch time.
    pub async fn install_mcp_config(&self, working_dir: &Path) -> Result<()> {
        let mcp_file = working_dir.join(".mcp.json");

        let mut existing = read_json_if_present(&mcp_file)
            .await?
            .unwrap_or_else(|| Value::Object(Map::new()));
        let existing_obj = match existing.as_object_mut() {
            Some(obj) => obj,
            None => {
                existing = Value::Object(Map::new());
                existing.as_object_mut().unwrap()
            }
        };

        let servers = existing_obj
            .entry("mcpServers".to_string())
            .or_insert_with(|| Value::Object(Map::new()));
        if let Some(map) = servers.as_object_mut() {
            map.insert(
                "loom".into(),
                json!({
                    "type": "http",
                    "url": "http://127.0.0.1:${LOOM_PORT:-18932}/mcp",
                    "headers": { "X-Loom-Cell-Id": "${LOOM_CELL_ID}" },
                }),
            );
        }

        let mut out = serde_json::to_string_pretty(&existing)?;
        out.push('\n');
        fs::write(&mcp_file, out).await?;
        Ok(())
    }

    /// Remove the `loom` entry from `mcpServers`. Deletes the file if no other
    /// servers remain.
    pub async fn uninstall_mcp_config(&self, working_dir: &Path) -> Result<()> {
        let mcp_file = working_dir.join(".mcp.json");
        let Some(mut existing) = read_json_if_present(&mcp_file).await? else {
            if fs::metadata(&mcp_file).await.is_ok() {
                let _ = fs::remove_file(&mcp_file).await;
            }
            return Ok(());
        };
        let Some(obj) = existing.as_object_mut() else {
            return Ok(());
        };
        if let Some(Value::Object(mut servers)) = obj.remove("mcpServers") {
            servers.remove("loom");
            if !servers.is_empty() {
                obj.insert("mcpServers".into(), Value::Object(servers));
            }
        }
        if obj.is_empty() {
            let _ = fs::remove_file(&mcp_file).await;
        } else {
            let mut out = serde_json::to_string_pretty(&existing)?;
            out.push('\n');
            fs::write(&mcp_file, out).await?;
        }
        Ok(())
    }

    /// Write the three `loom-*` slash-command skills under
    /// `<working_dir>/.claude/skills/<name>/SKILL.md`. Overwrites existing
    /// Loom skills to keep them in sync with the daemon version.
    pub async fn install_skills(&self, working_dir: &Path) -> Result<()> {
        let skills_dir = working_dir.join(".claude").join("skills");
        for (name, body) in SKILLS {
            let skill_dir = skills_dir.join(name);
            fs::create_dir_all(&skill_dir).await?;
            fs::write(skill_dir.join("SKILL.md"), *body).await?;
        }
        Ok(())
    }

    /// Remove only the `loom-*` directories under `.claude/skills/`. Leaves
    /// the skills dir itself alone (user may have other skills there).
    pub async fn uninstall_skills(&self, working_dir: &Path) -> Result<()> {
        let skills_dir = working_dir.join(".claude").join("skills");
        let mut rd = match fs::read_dir(&skills_dir).await {
            Ok(rd) => rd,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(err) => return Err(err.into()),
        };
        while let Some(entry) = rd.next_entry().await? {
            let name = entry.file_name();
            let Some(name_str) = name.to_str() else {
                continue;
            };
            if !name_str.starts_with(LOOM_SKILL_PREFIX) {
                continue;
            }
            let ft = entry.file_type().await?;
            if !ft.is_dir() {
                continue;
            }
            let path = entry.path();
            let skill_md = path.join("SKILL.md");
            let _ = fs::remove_file(&skill_md).await;
            // Try to rmdir — ignore failure if the user added other files.
            let _ = fs::remove_dir(&path).await;
        }
        Ok(())
    }
}

#[async_trait]
impl AgentAdapter for ClaudeCodeAdapter {
    fn provider_name(&self) -> &str {
        "claude-code"
    }

    fn default_boot_command(&self) -> &str {
        "claude"
    }

    fn input_ready_policy(&self) -> InputReadyPolicy {
        InputReadyPolicy::OnIdle
    }

    fn parse_hook(&self, payload: &serde_json::Value) -> Vec<AgentEvent> {
        let kind = payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let cell_id = payload
            .get("cell_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if cell_id.is_empty() || kind.is_empty() {
            return Vec::new();
        }
        let mut event = AgentEvent::new(cell_id, kind);
        event.raw = payload.clone();
        if let Some(detail) = payload.get("detail").and_then(|v| v.as_str()) {
            event.detail = detail.to_string();
        }
        vec![event]
    }

    async fn install_hooks(&self, working_dir: &Path) -> Result<()> {
        ClaudeCodeAdapter::install_hooks(self, working_dir).await
    }

    async fn uninstall_hooks(&self, working_dir: &Path) -> Result<()> {
        ClaudeCodeAdapter::uninstall_hooks(self, working_dir).await
    }

    async fn install_mcp_config(&self, working_dir: &Path) -> Result<()> {
        ClaudeCodeAdapter::install_mcp_config(self, working_dir).await
    }

    async fn uninstall_mcp_config(&self, working_dir: &Path) -> Result<()> {
        ClaudeCodeAdapter::uninstall_mcp_config(self, working_dir).await
    }

    async fn install_skills(&self, working_dir: &Path) -> Result<()> {
        ClaudeCodeAdapter::install_skills(self, working_dir).await
    }

    async fn uninstall_skills(&self, working_dir: &Path) -> Result<()> {
        ClaudeCodeAdapter::uninstall_skills(self, working_dir).await
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use tempfile::TempDir;

    /// `LOOM_PORT` is a process-wide env var — tests that touch it must
    /// serialize so they don't stomp on each other.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    async fn read_json(path: &Path) -> Value {
        let text = fs::read_to_string(path).await.expect("read back");
        serde_json::from_str(&text).expect("valid json")
    }

    #[tokio::test]
    async fn install_hooks_creates_settings_with_all_events() {
        let tmp = TempDir::new().unwrap();
        let adapter = ClaudeCodeAdapter;
        let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("LOOM_PORT", "18932");
        adapter.install_hooks(tmp.path()).await.unwrap();
        std::env::remove_var("LOOM_PORT");
        drop(guard);

        let settings = tmp.path().join(".claude/settings.local.json");
        assert!(settings.exists());
        let value = read_json(&settings).await;
        let hooks = value.get("hooks").and_then(Value::as_object).unwrap();
        for event in [
            "SessionStart",
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "Notification",
            "Stop",
            "SubagentStart",
            "SubagentStop",
            "StopFailure",
        ] {
            assert!(hooks.contains_key(event), "missing event {event}");
        }
        // SessionStart must be a command hook — HTTP hooks are silently
        // dropped by Claude Code for SessionStart.
        let ss = hooks
            .get("SessionStart")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .and_then(|e| e.get("hooks"))
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .unwrap();
        assert_eq!(ss.get("type").and_then(Value::as_str), Some("command"));
        assert!(ss
            .get("command")
            .and_then(Value::as_str)
            .unwrap()
            .contains("localhost:18932/events"));
        // PreToolUse: http hook with matcher ".*".
        let pre_entry = hooks
            .get("PreToolUse")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .unwrap();
        assert_eq!(pre_entry.get("matcher").and_then(Value::as_str), Some(".*"));
        let pre_hook = pre_entry
            .get("hooks")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .unwrap();
        assert_eq!(pre_hook.get("type").and_then(Value::as_str), Some("http"));
        assert!(pre_hook
            .get("url")
            .and_then(Value::as_str)
            .unwrap()
            .contains("/events"));
    }

    #[tokio::test]
    async fn install_hooks_dedupes_across_port_change() {
        let tmp = TempDir::new().unwrap();
        let adapter = ClaudeCodeAdapter;

        // Install on one port.
        {
            let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            std::env::set_var("LOOM_PORT", "18932");
            adapter.install_hooks(tmp.path()).await.unwrap();
            std::env::remove_var("LOOM_PORT");
            drop(guard);
        }
        // Then install on another port — old entries should be replaced, not
        // duplicated.
        {
            let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            std::env::set_var("LOOM_PORT", "28932");
            adapter.install_hooks(tmp.path()).await.unwrap();
            std::env::remove_var("LOOM_PORT");
            drop(guard);
        }

        let value = read_json(&tmp.path().join(".claude/settings.local.json")).await;
        let hooks = value.get("hooks").and_then(Value::as_object).unwrap();
        let pre = hooks.get("PreToolUse").and_then(Value::as_array).unwrap();
        assert_eq!(pre.len(), 1, "hooks should not accumulate on re-install");
        let pre_url = pre[0]
            .get("hooks")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .and_then(|h| h.get("url"))
            .and_then(Value::as_str)
            .unwrap();
        assert!(
            pre_url.contains(":28932/"),
            "expected new port in url, got {pre_url}"
        );
    }

    #[tokio::test]
    async fn uninstall_hooks_preserves_user_hooks() {
        let tmp = TempDir::new().unwrap();
        let settings_dir = tmp.path().join(".claude");
        fs::create_dir_all(&settings_dir).await.unwrap();
        let settings_file = settings_dir.join("settings.local.json");

        // Seed a user-authored hook on PreToolUse that points elsewhere.
        let seed = json!({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{
                        "type": "http",
                        "url": "http://example.com/webhook",
                    }],
                }],
            },
            "permissions": { "allow": ["Bash"] },
        });
        fs::write(&settings_file, serde_json::to_string_pretty(&seed).unwrap())
            .await
            .unwrap();

        let adapter = ClaudeCodeAdapter;
        {
            let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            std::env::set_var("LOOM_PORT", "18932");
            adapter.install_hooks(tmp.path()).await.unwrap();
            adapter.uninstall_hooks(tmp.path()).await.unwrap();
            std::env::remove_var("LOOM_PORT");
            drop(guard);
        }

        let value = read_json(&settings_file).await;
        // User's non-Loom hook must still be there.
        let pre = value
            .get("hooks")
            .and_then(|h| h.get("PreToolUse"))
            .and_then(Value::as_array)
            .expect("PreToolUse kept");
        assert_eq!(pre.len(), 1);
        let url = pre[0]
            .get("hooks")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .and_then(|h| h.get("url"))
            .and_then(Value::as_str)
            .unwrap();
        assert_eq!(url, "http://example.com/webhook");
        // Unrelated fields preserved.
        assert!(value.get("permissions").is_some());
    }

    #[tokio::test]
    async fn install_mcp_merges_with_existing_servers() {
        let tmp = TempDir::new().unwrap();
        let mcp_file = tmp.path().join(".mcp.json");
        let seed = json!({
            "mcpServers": {
                "other": { "type": "http", "url": "http://example.com/mcp" }
            }
        });
        fs::write(&mcp_file, serde_json::to_string_pretty(&seed).unwrap())
            .await
            .unwrap();

        let adapter = ClaudeCodeAdapter;
        adapter.install_mcp_config(tmp.path()).await.unwrap();

        let value = read_json(&mcp_file).await;
        let servers = value.get("mcpServers").and_then(Value::as_object).unwrap();
        assert!(servers.contains_key("other"));
        assert!(servers.contains_key("loom"));
        let loom_url = servers
            .get("loom")
            .and_then(|v| v.get("url"))
            .and_then(Value::as_str)
            .unwrap();
        assert!(loom_url.contains("${LOOM_PORT:-18932}"));
        assert!(loom_url.ends_with("/mcp"));
    }

    #[tokio::test]
    async fn uninstall_mcp_deletes_when_only_loom() {
        let tmp = TempDir::new().unwrap();
        let adapter = ClaudeCodeAdapter;
        adapter.install_mcp_config(tmp.path()).await.unwrap();
        let mcp_file = tmp.path().join(".mcp.json");
        assert!(mcp_file.exists());
        adapter.uninstall_mcp_config(tmp.path()).await.unwrap();
        assert!(!mcp_file.exists(), "empty mcp.json should be deleted");
    }

    #[tokio::test]
    async fn uninstall_mcp_preserves_other_servers() {
        let tmp = TempDir::new().unwrap();
        let mcp_file = tmp.path().join(".mcp.json");
        let adapter = ClaudeCodeAdapter;
        adapter.install_mcp_config(tmp.path()).await.unwrap();
        // Add a user entry.
        let mut v = read_json(&mcp_file).await;
        v.get_mut("mcpServers")
            .and_then(Value::as_object_mut)
            .unwrap()
            .insert(
                "other".into(),
                json!({ "type": "http", "url": "http://example.com/mcp" }),
            );
        fs::write(&mcp_file, serde_json::to_string_pretty(&v).unwrap())
            .await
            .unwrap();
        adapter.uninstall_mcp_config(tmp.path()).await.unwrap();
        let value = read_json(&mcp_file).await;
        let servers = value.get("mcpServers").and_then(Value::as_object).unwrap();
        assert!(servers.contains_key("other"));
        assert!(!servers.contains_key("loom"));
    }

    #[tokio::test]
    async fn install_skills_writes_three_files() {
        let tmp = TempDir::new().unwrap();
        let adapter = ClaudeCodeAdapter;
        adapter.install_skills(tmp.path()).await.unwrap();
        for name in ["loom-status", "loom-board", "loom-done"] {
            let p = tmp
                .path()
                .join(".claude/skills")
                .join(name)
                .join("SKILL.md");
            let text = fs::read_to_string(&p).await.expect(name);
            assert!(text.starts_with("---\nname: "));
            assert!(text.contains(&format!("name: {name}")));
        }
    }

    #[tokio::test]
    async fn uninstall_skills_only_removes_loom_prefixed() {
        let tmp = TempDir::new().unwrap();
        let adapter = ClaudeCodeAdapter;
        adapter.install_skills(tmp.path()).await.unwrap();

        // Seed a user skill that should survive.
        let user = tmp.path().join(".claude/skills/my-skill");
        fs::create_dir_all(&user).await.unwrap();
        fs::write(user.join("SKILL.md"), "user-content")
            .await
            .unwrap();

        adapter.uninstall_skills(tmp.path()).await.unwrap();

        for name in ["loom-status", "loom-board", "loom-done"] {
            let p = tmp
                .path()
                .join(".claude/skills")
                .join(name)
                .join("SKILL.md");
            assert!(!p.exists(), "{name} should be gone");
        }
        assert!(user.join("SKILL.md").exists());
    }

    #[test]
    fn loom_event_target_regex_matches_both_hosts() {
        assert!(is_loom_event_target("http://localhost:18932/events"));
        assert!(is_loom_event_target("http://127.0.0.1:28932/events"));
        assert!(is_loom_event_target(
            "curl http://localhost:18932/events -X POST"
        ));
        assert!(!is_loom_event_target("http://example.com/events"));
        assert!(!is_loom_event_target("http://localhost:18932/api/cmd"));
    }

    #[test]
    fn is_loom_hook_entry_matches_command_hooks() {
        let entry = json!({
            "hooks": [{
                "type": "command",
                "command": "curl -s http://localhost:18932/events",
            }]
        });
        assert!(is_loom_hook_entry(&entry));
    }

    #[test]
    fn is_loom_hook_entry_rejects_user_hook() {
        let entry = json!({
            "matcher": "Bash",
            "hooks": [{ "type": "http", "url": "http://example.com/hook" }]
        });
        assert!(!is_loom_hook_entry(&entry));
    }
}
