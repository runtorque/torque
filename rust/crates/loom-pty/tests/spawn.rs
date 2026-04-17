//! End-to-end PTY test — spawns a short-lived command and verifies output.

use std::time::Duration;

use loom_pty::{LocalPtyBackend, PtyEvent};

// The PTY backend now always wraps arbitrary commands in the user's
// interactive login shell so `.zshrc` aliases apply — which means the
// shell echoes the typed command line back to its output before
// executing it. The tests below account for that: they use a sentinel
// token produced only by the command's *output* (not by its echo) when
// checking for correctness, and they terminate the shell with an
// explicit `exit` so the tests don't hang.
//
// `TERM=dumb` is set in the environment so the shell doesn't emit
// starship / oh-my-zsh prompts that would otherwise contribute
// stray codepoints.

fn test_env() -> std::collections::HashMap<String, String> {
    let mut env = std::collections::HashMap::new();
    env.insert("TERM".into(), "dumb".into());
    env.insert("PS1".into(), "$ ".into());
    // Point at bash for tests — it reliably supports `-il` and the
    // POSIX features (`seq`, `$(...)`, `printf '%.s...'`) these tests
    // use. We also set `BASH_ENV=/dev/null` so no user rc files
    // contaminate stdout with prompt noise.
    env.insert("SHELL".into(), "/bin/bash".into());
    env.insert("BASH_ENV".into(), "/dev/null".into());
    env
}

#[tokio::test]
async fn spawn_echo_emits_output_and_exits() {
    let (pty, mut rx) = LocalPtyBackend::new();

    pty.spawn(
        "test-cell",
        "printf '===HELLO-FROM-PTY===\\n'; exit 0",
        None,
        test_env(),
        24,
        80,
    )
    .await
    .expect("spawn");

    let mut saw_output = false;
    let mut saw_exit = false;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(8);
    while tokio::time::Instant::now() < deadline {
        match tokio::time::timeout(Duration::from_millis(500), rx.recv()).await {
            Ok(Some(PtyEvent::Output { text, .. })) => {
                if text.contains("===HELLO-FROM-PTY===") {
                    saw_output = true;
                }
            }
            Ok(Some(PtyEvent::Exited { status, .. })) => {
                saw_exit = true;
                // The login shell's exit status, not the command's.
                // With `exit 0` typed at the shell, that should be 0.
                assert_eq!(status, 0);
                break;
            }
            Ok(Some(PtyEvent::Error { message, .. })) => {
                panic!("pty error: {message}");
            }
            Ok(Some(_)) => continue,
            Ok(None) => break,
            Err(_) => continue,
        }
    }

    assert!(saw_output, "expected sentinel in PTY output");
    assert!(saw_exit, "expected PTY to emit an Exited event");
}

#[tokio::test]
async fn spawn_utf8_multibyte_not_shredded() {
    // Regression: a 3-byte codepoint (em dash, an emoji, a box-drawing
    // glyph) must not be corrupted even if its bytes straddle a PTY read
    // boundary. We wrap the em-dash run in unique sentinels so we can
    // isolate the command's *output* from the shell's command-echo.
    let (pty, mut rx) = LocalPtyBackend::new();
    pty.spawn(
        "utf8-cell",
        // Print 200 em-dashes between unique sentinels, then exit.
        // The sentinels are placed inside a variable expansion so they
        // appear in the command echo only once, letting us distinguish
        // the shell's echo of the command from the command's actual
        // stdout. We split the sentinel across `printf` calls so the
        // literal `STARTOK`/`ENDOK` tokens never appear in the command
        // itself — only after joining via shell expansion.
        "A=ZAP; B=END; printf '~~%s1~~' \"$A\"; printf '%.s—' $(seq 1 200); printf '~~%s2~~\\n' \"$A\"; exit 0",
        None,
        test_env(),
        24,
        80,
    )
    .await
    .expect("spawn");

    let mut collected = String::new();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(8);
    while tokio::time::Instant::now() < deadline {
        match tokio::time::timeout(Duration::from_millis(500), rx.recv()).await {
            Ok(Some(PtyEvent::Output { text, .. })) => {
                collected.push_str(&text);
            }
            Ok(Some(PtyEvent::Exited { .. })) => break,
            Ok(Some(PtyEvent::Error { message, .. })) => panic!("pty error: {message}"),
            Ok(Some(_)) => continue,
            Ok(None) => break,
            Err(_) => continue,
        }
    }

    // Shell echoes the command line back — including line-wrap repaints
    // on narrow terminals, so the sentinels appear multiple times in
    // the echo. The *real* output uses sentinels `~~ZAP1~~` /
    // `~~ZAP2~~`, which are constructed at runtime from `$A` so they
    // never appear in the typed command string. Carve the window
    // between them.
    let start = collected
        .find("~~ZAP1~~")
        .map(|i| i + "~~ZAP1~~".len())
        .expect("output must contain ~~ZAP1~~ sentinel");
    let end = collected[start..]
        .find("~~ZAP2~~")
        .map(|i| start + i)
        .expect("output must contain ~~ZAP2~~ sentinel");
    let window = &collected[start..end];

    let dashes = window.chars().filter(|c| *c == '—').count();
    let replacements = collected.chars().filter(|c| *c == '\u{FFFD}').count();
    assert_eq!(
        dashes, 200,
        "expected 200 em-dashes, got {dashes}. collected = {:?}",
        collected
    );
    assert_eq!(
        replacements, 0,
        "decoder shredded a multibyte codepoint: {replacements} replacement chars"
    );
}
