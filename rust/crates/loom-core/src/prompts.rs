//! Shared prompt text helpers. Mirrors `loom/server_prompts.py`.
//!
//! `build_loom_system_prompt` is the persistent, worker-role system prompt
//! injected via each adapter's `inject_persistent_prompt` (Claude Code writes
//! it to `.loom/loom-system-prompt-<cell-id>.md` and appends
//! `--append-system-prompt-file <path>` to the boot command).

/// Persistent Loom system prompt appended to every dispatched worker's
/// boot command. Content matches Python's `build_loom_system_prompt` (see
/// loom/server_prompts.py) so the two ports stay behaviorally identical.
pub fn build_loom_system_prompt() -> String {
    // Keep this in sync with loom/server_prompts.py::build_loom_system_prompt.
    // Tests in that file assert specific substrings ("loom_done",
    // "loom_ask", "loom_ready", "loom_derive", "loom_context"), so the tool
    // list must stay intact.
    r#"# Loom Agent

You are running inside Loom, an AI agent orchestration system.
Loom tracks your task, manages your worktree, and coordinates
you with other agents in a pipeline.

## Reporting tools

Use the Loom MCP tools to report progress and completion:

- `loom_done(message="summary")` — task complete, no follow-up needed
- `loom_ready()` — task complete and release this agent for future work
- `loom_progress(message="current activity")` — update your activity status
- `loom_blocked(reason="reason")` — signal that you need help
- `loom_error(message="message")` — report an unrecoverable error
- `loom_verify(state="passed", tests_run="...", notes="...")` — record manual deploy/restart/smoke verification details when relevant
- `loom_derive(description="title", action="action-name", context="details")` — create a subtask and dispatch it according to the allowed transition
- `loom_ask(question="question", description="details")` — request a blocking human decision or approval when the task cannot continue safely without it
- `loom_context()` — view your current task, agent info, and pipeline state

## Important

Always signal completion via one of the tools above.
Your dispatch prompt specifies which transitions are available —
use those to determine valid `derive` targets.
Use `loom_ask` only when a blocking human answer or approval is
required to continue safely. If you can keep moving, do so.
For status updates, non-blocking observations, or optional
follow-up ideas, continue working and report them via
`loom_progress`, `loom_done`, `loom_blocked`, or derived-task
context instead of pausing the task.
When in doubt, call `loom_context()` to see your current state.
"#
    .to_string()
}

/// Assemble a dispatch-level persistent prompt: any caller-supplied header
/// (e.g. from the group's `system_prompt` launch config) followed by the
/// standard `build_loom_system_prompt` body. Matches Python's
/// `_build_dispatch_persistent_prompt` in loom/server.py.
pub fn build_dispatch_persistent_prompt(extra_system_prompt: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    let extra = extra_system_prompt.trim_end();
    if !extra.is_empty() {
        parts.push(extra.to_string());
    }
    parts.push(build_loom_system_prompt().trim_end().to_string());
    let mut out = parts.join("\n\n");
    out.push('\n');
    out
}

/// Stable per-cell filename for the persistent prompt file. Using the cell
/// id keeps it unique per agent so uninstall + reinstall on rename/reparent
/// don't collide.
pub fn persistent_prompt_filename(cell_id: &str) -> String {
    format!("loom-system-prompt-{cell_id}.md")
}

/// Filename for a group's weaver system prompt. Group-scoped because only
/// one weaver exists per group at a time.
pub fn weaver_prompt_filename(group: &str) -> String {
    // Match Python's loom/weaver.py::prompt_filename.
    format!("weaver-system-prompt-{group}.md")
}

/// Strip any previously-appended `--append-system-prompt-file <path>` flag
/// from a boot command so we can re-apply it idempotently. Without this,
/// every reboot of the agent would stack a second (and third…) copy of the
/// flag onto `cell.command`.
///
/// Recognises both `--append-system-prompt-file <path>` (space-separated)
/// and `--append-system-prompt-file=<path>` (equals form). The path token
/// may be single- or double-quoted.
pub fn strip_append_system_prompt_flag(command: &str) -> String {
    const FLAG: &str = "--append-system-prompt-file";
    let mut out = command.to_string();
    loop {
        let Some(idx) = out.find(FLAG) else {
            break;
        };
        let after = idx + FLAG.len();
        let rest = &out[after..];
        // Find end of the value token.
        let mut chars = rest.char_indices();
        let (value_start_rel, value_end_rel) = match chars.next() {
            Some((_, '=')) => {
                // equals form: value starts at rest[1..]
                let value_start = 1;
                let value_end = scan_quoted_or_unquoted(&rest[value_start..]);
                (value_start, value_start + value_end)
            }
            Some((_, c)) if c.is_whitespace() => {
                // space-separated form: skip whitespace, then consume value
                let trimmed = rest.trim_start();
                let value_start = rest.len() - trimmed.len();
                let value_end = value_start + scan_quoted_or_unquoted(trimmed);
                (value_start, value_end)
            }
            _ => {
                // Something weird follows the flag token — leave it alone and
                // bail out of the loop to avoid an infinite spin.
                break;
            }
        };
        let abs_end = after + value_end_rel;
        let _ = value_start_rel;
        // Also sweep any leading space so we don't leave "  " in the command.
        let trim_start = out[..idx].trim_end().len();
        out = format!("{}{}", &out[..trim_start], &out[abs_end..]);
    }
    out.trim().to_string()
}

fn scan_quoted_or_unquoted(s: &str) -> usize {
    let bytes = s.as_bytes();
    if bytes.is_empty() {
        return 0;
    }
    match bytes[0] {
        b'\'' => {
            // Single-quoted: scan until matching quote. Shell single quotes
            // don't support escapes; the closing quote ends the token.
            let mut i = 1;
            while i < bytes.len() && bytes[i] != b'\'' {
                i += 1;
            }
            (i + 1).min(bytes.len())
        }
        b'"' => {
            let mut i = 1;
            while i < bytes.len() {
                if bytes[i] == b'\\' && i + 1 < bytes.len() {
                    i += 2;
                    continue;
                }
                if bytes[i] == b'"' {
                    return i + 1;
                }
                i += 1;
            }
            bytes.len()
        }
        _ => {
            // Unquoted: scan until whitespace.
            let mut i = 0;
            while i < bytes.len() && !(bytes[i] as char).is_whitespace() {
                i += 1;
            }
            i
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_prompt_mentions_core_reporting_tools() {
        let text = build_loom_system_prompt();
        for needle in &[
            "loom_done",
            "loom_ready",
            "loom_progress",
            "loom_blocked",
            "loom_error",
            "loom_derive",
            "loom_ask",
            "loom_context",
        ] {
            assert!(text.contains(needle), "prompt missing {needle}: {text}");
        }
    }

    #[test]
    fn dispatch_prompt_prepends_extra_system_prompt() {
        let out = build_dispatch_persistent_prompt("You are a cautious refactorer.");
        assert!(out.starts_with("You are a cautious refactorer."));
        assert!(out.contains("# Loom Agent"));
        assert!(out.ends_with('\n'));
    }

    #[test]
    fn dispatch_prompt_handles_empty_extra() {
        let out = build_dispatch_persistent_prompt("");
        assert!(out.starts_with("# Loom Agent"));
        assert!(out.ends_with('\n'));
    }

    #[test]
    fn per_cell_filename_is_stable_and_cell_scoped() {
        assert_eq!(
            persistent_prompt_filename("abc-123"),
            "loom-system-prompt-abc-123.md"
        );
    }

    #[test]
    fn strip_removes_space_separated_flag() {
        let s = "claude --append-system-prompt-file /tmp/x.md --model sonnet";
        assert_eq!(strip_append_system_prompt_flag(s), "claude --model sonnet");
    }

    #[test]
    fn strip_removes_quoted_path_flag() {
        let s = "claude --append-system-prompt-file '/tmp/weird dir/x.md' --foo";
        assert_eq!(strip_append_system_prompt_flag(s), "claude --foo");
    }

    #[test]
    fn strip_removes_equals_form() {
        let s = "claude --append-system-prompt-file=/tmp/x.md --foo";
        assert_eq!(strip_append_system_prompt_flag(s), "claude --foo");
    }

    #[test]
    fn strip_is_idempotent_on_clean_command() {
        let s = "claude --model sonnet";
        assert_eq!(strip_append_system_prompt_flag(s), "claude --model sonnet");
    }

    #[test]
    fn strip_handles_multiple_stacked_flags() {
        let s = "claude --append-system-prompt-file /tmp/a.md --append-system-prompt-file /tmp/b.md";
        assert_eq!(strip_append_system_prompt_flag(s), "claude");
    }
}
