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

    fn resolve_model_flags(&self, model: &str) -> String {
        let model = model.trim();
        if model.is_empty() {
            String::new()
        } else {
            format!(" --model {}", shell_words::quote(model))
        }
    }
}
