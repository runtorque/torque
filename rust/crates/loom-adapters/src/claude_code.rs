//! Claude Code adapter — HTTP hooks, session resume, event parsing.

use std::path::Path;

use anyhow::Result;
use async_trait::async_trait;

use super::base::{AgentAdapter, AgentEvent, InputReadyPolicy};

pub struct ClaudeCodeAdapter;

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
        // Minimal port — full mapping lands in Phase 5.
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

    async fn install_hooks(&self, _settings_dir: &Path) -> Result<()> {
        // TODO: port settings.local.json merge logic from claude_code.py
        Ok(())
    }

    async fn uninstall_hooks(&self, _settings_dir: &Path) -> Result<()> {
        Ok(())
    }
}
