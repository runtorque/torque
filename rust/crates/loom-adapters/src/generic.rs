use async_trait::async_trait;

use super::base::AgentAdapter;

pub struct GenericAdapter;

#[async_trait]
impl AgentAdapter for GenericAdapter {
    fn provider_name(&self) -> &str {
        "generic"
    }
    fn default_boot_command(&self) -> &str {
        "bash"
    }
}
