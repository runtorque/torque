//! Codex adapter stub.

use async_trait::async_trait;

use super::base::{AgentAdapter, InputReadyPolicy};

pub struct CodexAdapter;

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
}
