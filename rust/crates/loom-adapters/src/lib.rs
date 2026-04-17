//! Agent-awareness adapters: Claude Code, Codex, Gemini CLI, generic fallback.

pub mod base;
pub mod claude_code;
pub mod codex;
pub mod gemini;
pub mod generic;
pub mod registry;

pub use base::{AgentAdapter, AgentEvent, InputReadyPolicy};
pub use registry::{detect_agent_type, detect_by_command, get_adapter, get_providers};

/// Process-wide test lock over `LOOM_PORT`.
///
/// Every adapter reads `LOOM_PORT` to render hook URLs + MCP configs, so any
/// test that sets/unsets that env var must hold this lock for its whole
/// `install_hooks` / `install_mcp_config` critical section. Per-module locks
/// don't coordinate across modules — two tests in different files can stomp
/// each other's `LOOM_PORT` value mid-install.
#[cfg(test)]
pub(crate) static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
