//! Shell-argv helpers shared by the local PTY backend and the supervisor
//! client. The "spawn a login shell, then type the user's command into
//! stdin" pattern needs to be identical across both so `.zshrc` aliases
//! resolve the same way regardless of which backend is in play.

use std::collections::HashMap;

/// Build the `(argv, startup_command)` pair for a new PTY.
///
/// * If `command` is a bare shell (`/bin/zsh`, `bash`, etc.) — spawn it
///   as login + interactive, with no startup keystrokes.
/// * If `command` is anything else — spawn the user's login shell
///   (`$SHELL`, falling back to `/bin/zsh`) and return the command as
///   `Some("<command>\r")` so the caller types it in after the shell
///   has sourced its rc files. This is how `.zshrc` aliases like
///   `alias claude="claude --dangerously-skip-permissions"` continue to
///   apply — matches Python's 120ms post-spawn `write_input` pattern.
/// * If the user's shell isn't a known login-capable shell, fall back
///   to `/bin/sh -c <command>` and accept that aliases won't apply.
pub fn build_shell_argv(
    command: &str,
    env: &HashMap<String, String>,
) -> (Vec<String>, Option<String>) {
    let trimmed = command.trim();
    let is_bare_shell = matches!(
        trimmed,
        "/bin/zsh"
            | "/bin/bash"
            | "/bin/sh"
            | "/bin/fish"
            | "zsh"
            | "bash"
            | "sh"
            | "fish"
            | ""
    );
    let shell_path = env
        .get("SHELL")
        .cloned()
        .or_else(|| std::env::var("SHELL").ok())
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| "/bin/zsh".to_string());
    let shell_is_login_capable = matches!(
        shell_path.rsplit('/').next().unwrap_or(""),
        "zsh" | "bash" | "fish" | "sh"
    );

    if is_bare_shell {
        let target = if trimmed.is_empty() { shell_path.as_str() } else { trimmed };
        let flag = if shell_is_login_capable
            || matches!(trimmed, "zsh" | "bash" | "fish" | "/bin/zsh" | "/bin/bash" | "/bin/fish")
        {
            "-il"
        } else {
            "-i"
        };
        (vec![target.to_string(), flag.to_string()], None)
    } else if shell_is_login_capable {
        // Login shell + typed command after the rc files run.
        let startup = format!("{}\r", trimmed);
        (vec![shell_path, "-il".to_string()], Some(startup))
    } else {
        // Give up on aliases; run the command under /bin/sh -c directly.
        (
            vec![
                "/bin/sh".to_string(),
                "-c".to_string(),
                command.to_string(),
            ],
            None,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env_with_shell(shell: &str) -> HashMap<String, String> {
        let mut env = HashMap::new();
        env.insert("SHELL".into(), shell.into());
        env
    }

    #[test]
    fn bare_zsh_is_spawned_as_login_shell_no_startup_cmd() {
        let (argv, startup) = build_shell_argv("zsh", &env_with_shell("/bin/zsh"));
        assert_eq!(argv, vec!["zsh", "-il"]);
        assert!(startup.is_none());
    }

    #[test]
    fn claude_gets_shell_plus_startup_cmd() {
        let (argv, startup) = build_shell_argv("claude", &env_with_shell("/bin/zsh"));
        assert_eq!(argv, vec!["/bin/zsh", "-il"]);
        assert_eq!(startup.as_deref(), Some("claude\r"));
    }

    #[test]
    fn exotic_shell_falls_back_to_sh_dash_c() {
        let (argv, startup) = build_shell_argv("claude", &env_with_shell("/usr/local/bin/xonsh"));
        assert_eq!(argv, vec!["/bin/sh", "-c", "claude"]);
        assert!(startup.is_none());
    }

    #[test]
    fn empty_command_spawns_default_shell() {
        let (argv, startup) = build_shell_argv("", &env_with_shell("/bin/zsh"));
        assert_eq!(argv, vec!["/bin/zsh", "-il"]);
        assert!(startup.is_none());
    }
}
