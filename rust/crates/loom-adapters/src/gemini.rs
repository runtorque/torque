use async_trait::async_trait;

use super::base::AgentAdapter;

pub struct GeminiCliAdapter;

#[async_trait]
impl AgentAdapter for GeminiCliAdapter {
    fn provider_name(&self) -> &str {
        "gemini-cli"
    }
    fn default_boot_command(&self) -> &str {
        "gemini"
    }
}
