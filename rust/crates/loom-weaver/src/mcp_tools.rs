//! MCP tool specs. Port of `loom/mcp_weaver_tools/`.

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct ToolSpec {
    pub name: &'static str,
    pub description: &'static str,
}

pub const WEAVER_TOOLS: &[ToolSpec] = &[
    ToolSpec {
        name: "loom_progress",
        description: "Report progress on the current task.",
    },
    ToolSpec {
        name: "loom_done",
        description: "Mark the current task as complete.",
    },
    ToolSpec {
        name: "loom_ask",
        description: "Ask the human a question.",
    },
    ToolSpec {
        name: "loom_blocked",
        description: "Report the task is blocked.",
    },
    ToolSpec {
        name: "loom_derive",
        description: "Derive a new task from the current one.",
    },
];
