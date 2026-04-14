//! Adapter registry + lookup helpers.

use std::sync::Arc;

use once_cell::sync::Lazy;

use super::base::AgentAdapter;
use super::{claude_code::ClaudeCodeAdapter, codex::CodexAdapter, gemini::GeminiCliAdapter, generic::GenericAdapter};

static PROVIDERS: Lazy<Vec<(&'static str, Arc<dyn AgentAdapter>)>> = Lazy::new(|| {
    vec![
        ("claude-code", Arc::new(ClaudeCodeAdapter) as Arc<dyn AgentAdapter>),
        ("codex", Arc::new(CodexAdapter)),
        ("gemini-cli", Arc::new(GeminiCliAdapter)),
        ("generic", Arc::new(GenericAdapter)),
    ]
});

pub fn get_providers() -> Vec<&'static str> {
    PROVIDERS.iter().map(|(name, _)| *name).collect()
}

pub fn get_adapter(name: &str) -> Option<Arc<dyn AgentAdapter>> {
    PROVIDERS
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, a)| a.clone())
}

/// Detect by boot command (e.g. `claude` → claude-code).
pub fn detect_by_command(cmd: &str) -> Option<&'static str> {
    let first = cmd.split_whitespace().next().unwrap_or("");
    match first {
        "claude" | "claude-code" => Some("claude-code"),
        "codex" => Some("codex"),
        "gemini" | "gemini-cli" => Some("gemini-cli"),
        _ => None,
    }
}

/// Detect from a process name + command line (coarse heuristic).
pub fn detect_agent_type(process: &str, cmdline: &str) -> Option<&'static str> {
    if let Some(by_cmd) = detect_by_command(cmdline) {
        return Some(by_cmd);
    }
    match process {
        "claude" => Some("claude-code"),
        "codex" => Some("codex"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_lists_all() {
        let ps = get_providers();
        assert!(ps.contains(&"claude-code"));
        assert!(ps.contains(&"codex"));
        assert!(ps.contains(&"gemini-cli"));
        assert!(ps.contains(&"generic"));
    }

    #[test]
    fn detect_by_command_known() {
        assert_eq!(detect_by_command("claude"), Some("claude-code"));
        assert_eq!(detect_by_command("codex --watch"), Some("codex"));
        assert_eq!(detect_by_command("bash"), None);
    }
}
