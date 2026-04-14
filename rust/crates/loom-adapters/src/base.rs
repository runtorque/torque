//! Base types for agent adapters. Ports `loom/adapters/base.py`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputReadyPolicy {
    OnPrompt,
    OnIdle,
    Always,
    Never,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentEvent {
    pub cell_id: String,
    pub timestamp: f64,
    pub kind: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub tokens_in: i64,
    #[serde(default)]
    pub tokens_out: i64,
    #[serde(default)]
    pub needs_attention: bool,
    #[serde(default)]
    pub error_message: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub raw: serde_json::Value,
}

impl AgentEvent {
    pub fn new(cell_id: impl Into<String>, kind: impl Into<String>) -> Self {
        Self {
            cell_id: cell_id.into(),
            timestamp: chrono::Utc::now().timestamp() as f64,
            kind: kind.into(),
            detail: String::new(),
            tokens_in: 0,
            tokens_out: 0,
            needs_attention: false,
            error_message: String::new(),
            summary: String::new(),
            raw: serde_json::Value::Null,
        }
    }
}

/// Minimum interface for an adapter.
#[async_trait::async_trait]
pub trait AgentAdapter: Send + Sync {
    fn provider_name(&self) -> &str;
    fn default_boot_command(&self) -> &str;
    fn input_ready_policy(&self) -> InputReadyPolicy {
        InputReadyPolicy::OnPrompt
    }

    /// Parse a hook payload into structured events. Default = no events.
    fn parse_hook(&self, _payload: &serde_json::Value) -> Vec<AgentEvent> {
        Vec::new()
    }

    /// Install any hooks this provider needs into `settings_dir`.
    async fn install_hooks(&self, _settings_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    /// Remove previously-installed hooks.
    async fn uninstall_hooks(&self, _settings_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }
}
