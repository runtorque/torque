//! Codex adapter — full integration via command hooks and MCP.

use std::path::Path;

use anyhow::Result;
use async_trait::async_trait;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::fs;

use super::base::{AgentAdapter, AgentEvent, InputReadyPolicy};

const HOOK_TIMEOUT_SECONDS: u32 = 3;
const MCP_MARKER: &str = "# -- Loom MCP server (managed by Loom, do not edit) --";
const FEATURE_MARKER: &str = "# -- Loom Codex hooks feature (managed by Loom, do not edit) --";

static LOOM_EVENT_URL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"http://(?:localhost|127\.0\.0\.1):\d+/events").expect("valid loom hook regex")
});

pub struct CodexAdapter;

fn truncate(value: &str, max_len: usize) -> String {
    if value.chars().count() > max_len {
        format!("{}...", value.chars().take(max_len).collect::<String>())
    } else {
        value.to_string()
    }
}

fn tool_detail(tool: &str, input: &Value) -> String {
    if matches!(
        tool.to_ascii_lowercase().as_str(),
        "bash" | "shell" | "command"
    ) {
        let cmd = input
            .get("command")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .or_else(|| {
                input
                    .get("cmd")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
            });
        if let Some(cmd) = cmd {
            return format!("Running: {}", truncate(cmd, 40));
        }
    }
    if !tool.is_empty() {
        format!("Using {tool}")
    } else {
        "Working".to_string()
    }
}

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

fn loom_mcp_url() -> String {
    format!("http://127.0.0.1:{}/mcp", current_loom_port())
}

fn is_loom_hook_entry(entry: &Value) -> bool {
    let Some(hooks) = entry.get("hooks").and_then(Value::as_array) else {
        return false;
    };
    hooks.iter().any(|hook| {
        hook.get("type").and_then(Value::as_str) == Some("command")
            && hook
                .get("command")
                .and_then(Value::as_str)
                .map(|value| LOOM_EVENT_URL_RE.is_match(value))
                .unwrap_or(false)
    })
}

fn command_hook_entry(matcher: Option<&str>) -> Value {
    let cmd = format!(
        "curl -s -X POST {url} -H \"Content-Type: application/json\" -H \"X-Loom-Cell-Id: $LOOM_CELL_ID\" -d \"$(cat)\" > /dev/null 2>&1",
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

fn build_hook_config() -> Value {
    json!({
        "hooks": {
            "SessionStart": [command_hook_entry(None)],
            "PreToolUse": [command_hook_entry(Some(".*"))],
            // PostToolUse intentionally omitted to match Python parity and
            // avoid Codex's noisy per-tool terminal message.
            "Stop": [command_hook_entry(None)],
        }
    })
}

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

fn prune_loom_hooks(hooks: &mut Map<String, Value>) {
    let events: Vec<String> = hooks.keys().cloned().collect();
    for event in events {
        let Some(Value::Array(entries)) = hooks.get_mut(&event) else {
            continue;
        };
        entries.retain(|entry| !is_loom_hook_entry(entry));
        if entries.is_empty() {
            hooks.remove(&event);
        }
    }
}

fn find_table_section(content: &str, header: &str) -> Option<(usize, usize, usize)> {
    let needle = format!("[{header}]");
    let mut search = 0usize;
    while let Some(rel) = content[search..].find(&needle) {
        let start = search + rel;
        let boundary_ok = start == 0 || content.as_bytes().get(start - 1) == Some(&b'\n');
        let header_end = start + needle.len();
        let trailer_ok =
            header_end == content.len() || content.as_bytes().get(header_end) == Some(&b'\n');
        if boundary_ok && trailer_ok {
            let line_end = if header_end == content.len() {
                header_end
            } else {
                header_end + 1
            };
            let section_end = content[line_end..]
                .find("\n[")
                .map(|off| line_end + off + 1)
                .unwrap_or(content.len());
            return Some((start, line_end, section_end));
        }
        search = header_end;
    }
    None
}

fn strip_managed_feature_block(body: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    let mut lines = body.lines();
    while let Some(line) = lines.next() {
        if line.trim_end() == FEATURE_MARKER {
            let _ = lines.next();
            continue;
        }
        out.push(line);
    }
    if out.is_empty() {
        String::new()
    } else {
        let mut text = out.join("\n");
        text.push('\n');
        text
    }
}

fn ensure_codex_hooks_enabled(content: &str) -> String {
    let mut content = content.to_string();

    let managed_section = format!("\n{FEATURE_MARKER}\n[features]\ncodex_hooks = true\n");
    let managed_section_no_prefix = format!("{FEATURE_MARKER}\n[features]\ncodex_hooks = true\n");
    content = content.replace(&managed_section, "\n");
    content = content.replace(&managed_section_no_prefix, "");

    if let Some((start, line_end, end)) = find_table_section(&content, "features") {
        let body = &content[line_end..end];
        let body = strip_managed_feature_block(body);
        let mut lines: Vec<String> = Vec::new();
        let mut replaced = false;
        for line in body.lines() {
            if line.trim_start().starts_with("codex_hooks") {
                if !replaced {
                    lines.push(FEATURE_MARKER.to_string());
                    lines.push("codex_hooks = true".to_string());
                    replaced = true;
                }
                continue;
            }
            lines.push(line.to_string());
        }
        if !replaced {
            lines.push(FEATURE_MARKER.to_string());
            lines.push("codex_hooks = true".to_string());
        }
        let mut new_section = String::from("[features]\n");
        if !lines.is_empty() {
            new_section.push_str(&lines.join("\n"));
            new_section.push('\n');
        }
        let mut updated = String::new();
        updated.push_str(&content[..start]);
        updated.push_str(&new_section);
        updated.push_str(&content[end..]);
        return updated;
    }

    let mut updated = content.trim_end_matches('\n').to_string();
    if !updated.is_empty() {
        updated.push('\n');
    }
    updated.push('\n');
    updated.push_str(FEATURE_MARKER);
    updated.push('\n');
    updated.push_str("[features]\n");
    updated.push_str("codex_hooks = true\n");
    updated
}

fn remove_codex_hooks_enabled(content: &str) -> String {
    let managed_section = format!("\n{FEATURE_MARKER}\n[features]\ncodex_hooks = true\n");
    let managed_section_no_prefix = format!("{FEATURE_MARKER}\n[features]\ncodex_hooks = true\n");
    let mut content = content.replace(&managed_section, "\n");
    content = content.replace(&managed_section_no_prefix, "");

    if let Some((start, line_end, end)) = find_table_section(&content, "features") {
        let body = strip_managed_feature_block(&content[line_end..end]);
        if body.trim().is_empty() {
            let mut updated = String::new();
            updated.push_str(&content[..start]);
            updated.push_str(&content[end..]);
            return updated;
        }
        let mut updated = String::new();
        updated.push_str(&content[..start]);
        updated.push_str("[features]\n");
        updated.push_str(&body);
        updated.push_str(&content[end..]);
        return updated;
    }

    content
}

fn remove_managed_mcp_section(content: &str) -> String {
    let mut updated = content.to_string();
    while let Some(marker_start) = updated.find(MCP_MARKER) {
        let remove_start =
            if marker_start > 0 && updated.as_bytes().get(marker_start - 1) == Some(&b'\n') {
                marker_start - 1
            } else {
                marker_start
            };
        let marker_line_end = updated[marker_start..]
            .find('\n')
            .map(|off| marker_start + off + 1)
            .unwrap_or(updated.len());
        let end = updated[marker_line_end..]
            .find("\n[")
            .map(|off| marker_line_end + off + 1)
            .unwrap_or(updated.len());
        updated.replace_range(remove_start..end, "");
    }
    updated
}
#[async_trait]
impl AgentAdapter for CodexAdapter {
    fn provider_name(&self) -> &str {
        "codex"
    }

    fn default_boot_command(&self) -> &str {
        "codex"
    }

    fn resolve_model_flags(&self, model: &str) -> String {
        let model = model.trim();
        if model.is_empty() {
            String::new()
        } else {
            format!(" --model {}", shell_words::quote(model))
        }
    }

    fn resolve_reasoning_effort_flags(&self, reasoning_effort: &str) -> String {
        let reasoning_effort = reasoning_effort.trim();
        if reasoning_effort.is_empty() {
            String::new()
        } else {
            format!(
                " -c model_reasoning_effort={}",
                shell_words::quote(reasoning_effort)
            )
        }
    }

    fn reasoning_effort_options(&self) -> Vec<&'static str> {
        vec!["low", "medium", "high"]
    }

    fn input_ready_policy(&self) -> InputReadyPolicy {
        InputReadyPolicy::OnPrompt
    }

    fn parse_hook(&self, payload: &serde_json::Value) -> Vec<AgentEvent> {
        let hook_event = payload
            .get("hook_event_name")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .or_else(|| payload.get("type").and_then(Value::as_str))
            .unwrap_or("");
        let cell_id = payload.get("cell_id").and_then(Value::as_str).unwrap_or("");
        if cell_id.is_empty() || hook_event.is_empty() {
            return Vec::new();
        }
        let mut event = match hook_event {
            "SessionStart" => AgentEvent::new(cell_id, "session_start"),
            "Stop" => {
                let mut event = AgentEvent::new(cell_id, "session_end");
                event.summary = payload
                    .get("last_assistant_message")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                event
            }
            "PreToolUse" => {
                let tool = payload
                    .get("tool_name")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
                    .or_else(|| payload.get("name").and_then(Value::as_str))
                    .unwrap_or("");
                let input = payload
                    .get("tool_input")
                    .cloned()
                    .or_else(|| payload.get("input").cloned())
                    .unwrap_or_else(|| json!({}));
                let mut event = AgentEvent::new(cell_id, "tool_start");
                event.detail = tool_detail(tool, &input);
                event
            }
            "PostToolUse" => AgentEvent::new(cell_id, "tool_end"),
            _ => return Vec::new(),
        };
        event.raw = payload.clone();
        vec![event]
    }

    async fn install_hooks(&self, working_dir: &Path) -> Result<()> {
        let codex_dir = working_dir.join(".codex");
        fs::create_dir_all(&codex_dir).await?;
        let hooks_file = codex_dir.join("hooks.json");

        let mut existing = read_json_if_present(&hooks_file)
            .await?
            .unwrap_or_else(|| Value::Object(Map::new()));
        let existing_obj = match existing.as_object_mut() {
            Some(obj) => obj,
            None => {
                existing = Value::Object(Map::new());
                existing.as_object_mut().unwrap()
            }
        };

        let mut hooks_map = existing_obj
            .remove("hooks")
            .and_then(|v| match v {
                Value::Object(map) => Some(map),
                _ => None,
            })
            .unwrap_or_default();

        prune_loom_hooks(&mut hooks_map);

        if let Some(new_hooks) = build_hook_config().get("hooks").and_then(Value::as_object) {
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
        let mut out = serde_json::to_string_pretty(&existing)?;
        out.push('\n');
        fs::write(&hooks_file, out).await?;
        Ok(())
    }

    async fn uninstall_hooks(&self, working_dir: &Path) -> Result<()> {
        let hooks_file = working_dir.join(".codex").join("hooks.json");
        let Some(mut existing) = read_json_if_present(&hooks_file).await? else {
            if fs::metadata(&hooks_file).await.is_ok() {
                let _ = fs::remove_file(&hooks_file).await;
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
            let _ = fs::remove_file(&hooks_file).await;
        } else {
            let mut out = serde_json::to_string_pretty(&existing)?;
            out.push('\n');
            fs::write(&hooks_file, out).await?;
        }
        Ok(())
    }

    async fn install_mcp_config(&self, working_dir: &Path) -> Result<()> {
        let codex_dir = working_dir.join(".codex");
        fs::create_dir_all(&codex_dir).await?;
        let config_file = codex_dir.join("config.toml");

        let mut content = match fs::read_to_string(&config_file).await {
            Ok(text) => text,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(err) => return Err(err.into()),
        };

        content = remove_managed_mcp_section(&content);
        content = ensure_codex_hooks_enabled(&content);
        content = content.trim_end_matches('\n').to_string();
        if !content.is_empty() {
            content.push('\n');
        }
        content.push('\n');
        content.push_str(MCP_MARKER);
        content.push('\n');
        content.push_str("[mcp_servers.loom]\n");
        content.push_str(&format!("url = \"{}\"\n", loom_mcp_url()));
        content.push_str("env_http_headers = { \"X-Loom-Cell-Id\" = \"LOOM_CELL_ID\" }\n");
        fs::write(&config_file, content).await?;
        Ok(())
    }

    async fn uninstall_mcp_config(&self, working_dir: &Path) -> Result<()> {
        let config_file = working_dir.join(".codex").join("config.toml");
        let content = match fs::read_to_string(&config_file).await {
            Ok(text) => text,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(err) => return Err(err.into()),
        };
        let updated = remove_managed_mcp_section(&remove_codex_hooks_enabled(&content))
            .trim()
            .to_string();
        if updated.is_empty() {
            let _ = fs::remove_file(&config_file).await;
        } else {
            fs::write(&config_file, format!("{}\n", updated)).await?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn parse_hook_maps_session_start() {
        let adapter = CodexAdapter;
        let events = adapter.parse_hook(&json!({
            "cell_id": "agent-1",
            "type": "SessionStart",
        }));
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "session_start");
    }

    #[test]
    fn parse_hook_maps_shell_tool_to_running_detail() {
        let adapter = CodexAdapter;
        let events = adapter.parse_hook(&json!({
            "cell_id": "agent-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": { "command": "echo hello from codex" },
        }));
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "tool_start");
        assert_eq!(events[0].detail, "Running: echo hello from codex");
    }

    #[test]
    fn parse_hook_maps_stop_to_session_end() {
        let adapter = CodexAdapter;
        let events = adapter.parse_hook(&json!({
            "cell_id": "agent-1",
            "type": "Stop",
            "last_assistant_message": "Finished",
        }));
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "session_end");
        assert_eq!(events[0].summary, "Finished");
    }

    #[tokio::test]
    async fn install_mcp_config_rewrites_port_and_preserves_user_content() {
        let tmp = tempdir().unwrap();
        let codex_dir = tmp.path().join(".codex");
        fs::create_dir_all(&codex_dir).await.unwrap();
        fs::write(
            codex_dir.join("config.toml"),
            "model = \"gpt-5.4\"\n\n# -- Loom Codex hooks feature (managed by Loom, do not edit) --\n[features]\ncodex_hooks = true\n\n# -- Loom MCP server (managed by Loom, do not edit) --\n[mcp_servers.loom]\nurl = \"http://127.0.0.1:1234/mcp\"\nenv_http_headers = { \"X-Loom-Cell-Id\" = \"LOOM_CELL_ID\" }\n",
        )
        .await
        .unwrap();
        let expected_url = loom_mcp_url();

        CodexAdapter.install_mcp_config(tmp.path()).await.unwrap();

        let installed = fs::read_to_string(codex_dir.join("config.toml"))
            .await
            .unwrap();
        assert!(installed.contains("model = \"gpt-5.4\""));
        assert!(installed.contains("[features]"));
        assert!(installed.contains("codex_hooks = true"));
        assert!(installed.contains(&format!("url = \"{}\"", expected_url)));
        assert_eq!(installed.matches("[mcp_servers.loom]").count(), 1);
    }

    #[tokio::test]
    async fn install_hooks_merges_and_rewrites_existing_loom_entries() {
        let tmp = tempdir().unwrap();
        let codex_dir = tmp.path().join(".codex");
        fs::create_dir_all(&codex_dir).await.unwrap();
        fs::write(
            codex_dir.join("hooks.json"),
            serde_json::to_string_pretty(&json!({
                "hooks": {
                    "SessionStart": [{
                        "hooks": [{
                            "type": "command",
                            "timeout": 3,
                            "command": "curl -s -X POST http://localhost:1234/events -d \"$(cat)\" >/dev/null 2>&1"
                        }]
                    }],
                    "Notification": [{
                        "hooks": [{
                            "type": "command",
                            "timeout": 3,
                            "command": "echo keep-me"
                        }]
                    }]
                }
            }))
            .unwrap(),
        )
        .await
        .unwrap();
        let expected_hook_url = loom_hook_url();

        CodexAdapter.install_hooks(tmp.path()).await.unwrap();

        let hooks: Value = serde_json::from_str(
            &fs::read_to_string(codex_dir.join("hooks.json"))
                .await
                .unwrap(),
        )
        .unwrap();
        let session_start = hooks["hooks"]["SessionStart"].as_array().unwrap();
        assert_eq!(session_start.len(), 1);
        let session_cmd = session_start[0]["hooks"][0]["command"].as_str().unwrap();
        assert!(session_cmd.contains(&expected_hook_url));
        let pre_tool = hooks["hooks"]["PreToolUse"].as_array().unwrap();
        assert_eq!(pre_tool[0]["matcher"], ".*");
        let notification = hooks["hooks"]["Notification"].as_array().unwrap();
        assert_eq!(notification.len(), 1);
        assert_eq!(notification[0]["hooks"][0]["command"], "echo keep-me");
    }
}
