//! Agent-awareness adapters: Claude Code, Codex, Gemini CLI, generic fallback.

pub mod base;
pub mod claude_code;
pub mod codex;
pub mod gemini;
pub mod generic;
pub mod registry;

pub use base::{AgentAdapter, AgentEvent, InputReadyPolicy};
pub use registry::{detect_agent_type, detect_by_command, get_adapter, get_providers};
