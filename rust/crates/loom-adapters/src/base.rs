//! Base types for agent adapters. Ports `loom/adapters/base.py`.

use serde::{Deserialize, Serialize};
use std::time::Duration;

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
    /// Agent-provider session id, e.g. Claude Code's `session_id` from the
    /// SessionStart hook. Promoted onto `AgentCell.agent_session_id` so the
    /// next `relaunch_agent` can pass `--resume <id>`.
    #[serde(default)]
    pub session_id: String,
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
            session_id: String::new(),
            raw: serde_json::Value::Null,
        }
    }
}

/// Minimum interface for an adapter.
#[async_trait::async_trait]
pub trait AgentAdapter: Send + Sync {
    fn provider_name(&self) -> &str;
    fn default_boot_command(&self) -> &str;
    fn resolve_model_flags(&self, _model: &str) -> String {
        String::new()
    }
    fn resolve_reasoning_effort_flags(&self, _reasoning_effort: &str) -> String {
        String::new()
    }
    fn reasoning_effort_options(&self) -> Vec<&'static str> {
        Vec::new()
    }
    fn input_ready_policy(&self) -> InputReadyPolicy {
        InputReadyPolicy::OnPrompt
    }
    fn input_ready_timeout(&self) -> Duration {
        Duration::from_secs(0)
    }
    fn input_ready_poll_interval(&self) -> Duration {
        Duration::from_millis(250)
    }
    fn input_ready_stable_polls(&self) -> usize {
        1
    }
    fn input_ready_post_ready_delay(&self) -> Duration {
        Duration::from_secs(0)
    }
    fn is_input_ready_screen(&self, _screen_text: &str) -> bool {
        true
    }
    fn submit_key(&self) -> &str {
        "\r"
    }
    fn multiline_submit_delay(&self) -> Duration {
        Duration::from_millis(300)
    }
    fn input_chunks(&self, body: &str) -> Vec<String> {
        if body.is_empty() {
            Vec::new()
        } else {
            vec![body.to_string()]
        }
    }

    /// Parse a hook payload into structured events. Default = no events.
    fn parse_hook(&self, _payload: &serde_json::Value) -> Vec<AgentEvent> {
        Vec::new()
    }

    /// Install any hooks this provider needs. `working_dir` is the agent's
    /// working directory — config files land under `<working_dir>/.claude/`
    /// and `<working_dir>/.mcp.json`.
    async fn install_hooks(&self, _working_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    /// Remove previously-installed hooks.
    async fn uninstall_hooks(&self, _working_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    /// Register the Loom MCP server with the provider so the agent can call
    /// `loom_*` tools (progress, done, memory, etc).
    async fn install_mcp_config(&self, _working_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    async fn uninstall_mcp_config(&self, _working_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    /// Install provider-specific slash-command shortcuts (Claude Code skills,
    /// Codex commands, ...).
    async fn install_skills(&self, _working_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    async fn uninstall_skills(&self, _working_dir: &std::path::Path) -> anyhow::Result<()> {
        Ok(())
    }

    /// Write a persistent system prompt for this agent and return the CLI
    /// flag suffix that should be appended to the boot command (including a
    /// leading space), or an empty string if the adapter embeds the prompt
    /// via a config file rather than a flag.
    ///
    /// Mirrors `AgentAdapter.inject_persistent_prompt` in Python
    /// (loom/adapters/base.py). Errors should be swallowed (return "") —
    /// the dispatch path cannot fail because of a missing filesystem.
    async fn inject_persistent_prompt(
        &self,
        _working_dir: &std::path::Path,
        _filename: &str,
        _text: &str,
    ) -> String {
        String::new()
    }

    /// Remove previously-installed persistent prompt files. Called when an
    /// agent is deleted so `.loom/` doesn't accrue stale prompts.
    async fn uninstall_persistent_prompt(
        &self,
        _working_dir: &std::path::Path,
        _filename: &str,
    ) {
    }

    /// Given the agent's boot command + a persisted provider session id,
    /// return the command that resumes that session — or `None` if the
    /// provider doesn't support resume. Mirrors Python
    /// `AgentAdapter.get_resume_command`.
    fn resume_command(&self, _boot_cmd: &str, _session_id: &str) -> Option<String> {
        None
    }
}
