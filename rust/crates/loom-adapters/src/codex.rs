//! Codex adapter.

use async_trait::async_trait;
use serde_json::{json, Value};

use super::base::{AgentAdapter, AgentEvent, InputReadyPolicy};

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

#[async_trait]
impl AgentAdapter for CodexAdapter {
    fn provider_name(&self) -> &str {
        "codex"
    }

    fn default_boot_command(&self) -> &str {
        "codex"
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
}

#[cfg(test)]
mod tests {
    use serde_json::json;

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
}
