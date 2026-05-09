"""Codex CLI adapter — full integration via command hooks and MCP."""

import fcntl
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path

from .base import AgentAdapter, AgentEvent, InputReadyPolicy
from .mcp_launch import build_stdio_launch_spec

_TORQUE_EVENT_URL_RE = re.compile(r"http://(?:localhost|127\.0\.0\.1):\d+/events")

# Marker comment for Torque-managed MCP config section
_MCP_MARKER = "# -- Torque MCP server (managed by Torque, do not edit) --"
_FEATURE_MARKER = "# -- Torque Codex hooks feature (managed by Torque, do not edit) --"
_INSTRUCTIONS_MARKER = (
    "# -- Torque Codex model instructions (managed by Torque, do not edit) --"
)
_HOOK_TRUST_MARKER_PREFIX = "# -- Torque Codex hook trust:"
_HOOK_TRUST_MARKER_SUFFIX = "(managed by Torque, do not edit) --"

# Marker for Torque-managed AGENTS.md section (persistent system prompt)
_LEGACY_AGENTS_MARKER = "<!-- Torque system prompt (managed by Torque, do not edit) -->"
_ANY_AGENTS_SECTION_RE = re.compile(
    r"<!-- Torque system prompt(?:: [^\n]+)? "
    r"\(managed by Torque, do not edit\) -->\n"
    r"(?:(?!<!-- Torque system prompt(?:: [^\n]+)? "
    r"\(managed by Torque, do not edit\) -->)[\s\S])*?"
    r"<!-- Torque system prompt(?:: [^\n]+)? "
    r"\(managed by Torque, do not edit\) -->\n?"
)

# Regex to match the Torque MCP section in config.toml (marker through next
# section header or EOF)
_MCP_SECTION_RE = re.compile(
    r"\n?" + re.escape(_MCP_MARKER) + r"\n"
    r"\[mcp_servers\.torque\]\n"
    r"(?:(?!\n\[)[^\n]*\n)*",
)
_FEATURES_SECTION_RE = re.compile(
    r"(?ms)(^|\n)\[features\]\n(?P<body>(?:(?!\n\[).*\n?)*)"
)
_FEATURE_LINE_RE = re.compile(r"(?m)^hooks\s*=.*(?:\n|$)")
_MANAGED_FEATURE_BLOCK_RE = re.compile(
    r"\n?" + re.escape(_FEATURE_MARKER)
    + r"\n(?:hooks|codex_hooks)\s*=\s*true\n?"
)
_MANAGED_FEATURE_SECTION_RE = re.compile(
    r"\n?" + re.escape(_FEATURE_MARKER)
    + r"\n\[features\]\n(?:hooks|codex_hooks)\s*=\s*true\n?"
)
_INSTRUCTIONS_BLOCK_RE = re.compile(
    r"\n?" + re.escape(_INSTRUCTIONS_MARKER) + r"\n"
    r'model_instructions_file = "(?:[^"\\]|\\.)*"\n?',
)
_CODEX_HOOK_EVENT_LABELS = {
    "SessionStart": "session_start",
    "PreToolUse": "pre_tool_use",
    "Stop": "stop",
}


def _agents_marker(filename: str) -> str:
    """Return a stable marker for a specific Torque-managed prompt block."""
    return (
        f"<!-- Torque system prompt: {filename} "
        f"(managed by Torque, do not edit) -->"
    )


def _remove_agents_section(content: str, filename: str = "") -> str:
    """Remove a Torque-managed section from AGENTS.md content."""
    if not filename:
        return _ANY_AGENTS_SECTION_RE.sub("", content)
    for marker in (_agents_marker(filename), _LEGACY_AGENTS_MARKER):
        section_re = re.compile(
            re.escape(marker) + r"\n"
            r"(?:(?!" + re.escape(marker) + r")[\s\S])*?"
            + re.escape(marker) + r"\n?"
        )
        content = section_re.sub("", content)
    return content


def _truncate(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s


def _current_torque_port() -> str:
    port = (os.environ.get("TORQUE_PORT", "18932") or "").strip()
    return port or "18932"



def _torque_event_curl_command(url: str) -> str:
    """Return a command hook that posts stdin JSON durably to /events.

    The Python shim adds a deterministic event_id when the agent hook payload
    does not provide one, allowing the /events ingest idempotency layer to
    dedupe bounded curl retries. curl uses --fail plus retry so transient 503s
    are not silently dropped by the hook source.
    """
    py = (
        "import sys,json,hashlib;"
        "p=json.load(sys.stdin);"
        "b=json.dumps(p,sort_keys=True,separators=(',',':')).encode();"
        "p.setdefault('event_id',hashlib.sha256(b).hexdigest());"
        "print(json.dumps(p,separators=(',',':')))"
    )
    curl = (
        "curl --fail --show-error --silent --retry 3 --retry-delay 1"
        + " --retry-connrefused -X POST " + shlex.quote(url)
        + ' -H "Content-Type: application/json"'
        + ' -H "X-Torque-Cell-Id: $TORQUE_CELL_ID"'
        + ' --data-binary @"$tmp"'
    )
    # Use a temp file rather than piping into curl so curl can rewind the
    # request body for HTTP 503 retry attempts.
    return (
        "tmp=$(mktemp); "
        "trap 'rm -f \"$tmp\"' EXIT; "
        "python3 -c " + shlex.quote(py) + ' > "$tmp" && '
        + curl
    )

def _torque_hook_url() -> str:
    return f"http://localhost:{_current_torque_port()}/events"


def _torque_mcp_url() -> str:
    return f"http://127.0.0.1:{_current_torque_port()}/mcp"


def _matches_codex_token(value: str) -> bool:
    token = os.path.basename((value or "").strip().lower())
    return (
        token == "codex"
        or token.startswith("codex-")
        or token.startswith("codex_")
        or token.endswith("-codex")
        or token.endswith("_codex")
    )


_VALUE_OPTS = {
    "-c", "--config",
    "--enable", "--disable",
    "--remote", "--remote-auth-token-env",
    "-i", "--image",
    "-m", "--model",
    "--local-provider",
    "-p", "--profile",
    "-s", "--sandbox",
    "-a", "--ask-for-approval",
    "-C", "--cd",
    "--add-dir",
}


def _split_boot_args(boot_cmd: str) -> tuple[list[str], str]:
    """Split a Codex boot command into option args and trailing prompt."""
    parts = shlex.split(boot_cmd)
    if len(parts) <= 1:
        return ([], "")
    args = parts[1:]
    opts: list[str] = []
    prompt = ""
    i = 0
    while i < len(args):
        part = args[i]
        if part == "--":
            prompt = " ".join(args[i + 1:])
            break
        if part.startswith("-"):
            opts.append(part)
            if "=" not in part and part in _VALUE_OPTS and i + 1 < len(args):
                i += 1
                opts.append(args[i])
            i += 1
            continue
        prompt = " ".join(args[i:])
        break
    return (opts, prompt)


def _is_torque_hook(hook: dict) -> bool:
    """Check if a single hook entry was installed by Torque (by URL marker)."""
    cmd = hook.get("command", "")
    return bool(_TORQUE_EVENT_URL_RE.search(cmd))


def _remove_empty_features_sections(content: str) -> str:
    """Remove a [features] table left empty by managed-line cleanup."""
    def _replace(match: re.Match[str]) -> str:
        if match.group("body").strip():
            return match.group(0)
        return match.group(1)

    return _FEATURES_SECTION_RE.sub(_replace, content)


def _ensure_codex_hooks_enabled(content: str) -> str:
    """Ensure config.toml enables Codex hooks for Torque-managed sessions."""
    content = _MANAGED_FEATURE_SECTION_RE.sub("", content)

    def _replace_features(match: re.Match[str]) -> str:
        prefix = match.group(1)
        body = _MANAGED_FEATURE_BLOCK_RE.sub("", match.group("body"))
        if not _FEATURE_LINE_RE.search(body):
            if body and not body.endswith("\n"):
                body += "\n"
            body += f"{_FEATURE_MARKER}\nhooks = true\n"
        return f"{prefix}[features]\n{body}"

    updated, replaced = _FEATURES_SECTION_RE.subn(_replace_features, content, count=1)
    if replaced:
        return updated

    content = content.rstrip("\n")
    if content:
        content += "\n"
    content += f"\n{_FEATURE_MARKER}\n[features]\nhooks = true\n"
    return content


def _remove_codex_hooks_enabled(content: str) -> str:
    """Remove Torque-managed Codex hook feature config from config.toml."""
    content = _MANAGED_FEATURE_SECTION_RE.sub("", content)
    content = _MANAGED_FEATURE_BLOCK_RE.sub("", content)
    return _remove_empty_features_sections(content)


def _codex_home() -> Path:
    home = (os.environ.get("CODEX_HOME") or "").strip()
    if home:
        return Path(home).expanduser()
    return Path.home() / ".codex"


def _codex_absolute_path(path: Path) -> str:
    # Match Codex's AbsolutePathBuf normalization: expand ~ and remove dot
    # components without resolving symlinks (Path.resolve() canonicalizes
    # /var to /private/var on macOS, which would change hook-state keys).
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def _hook_trust_marker(source_path: str) -> str:
    return (
        f"{_HOOK_TRUST_MARKER_PREFIX} {source_path} "
        f"{_HOOK_TRUST_MARKER_SUFFIX}"
    )


def _remove_hook_trust_block(content: str, source_path: str) -> str:
    marker = _hook_trust_marker(source_path)
    block_re = re.compile(
        r"\n?" + re.escape(marker) + r"\n.*?\n" + re.escape(marker) + r"\n?",
        re.DOTALL,
    )
    return block_re.sub("\n", content)


def _codex_command_hook_hash(
    event_label: str,
    matcher: str | None,
    hook: dict,
) -> str:
    """Return Codex v0.130 HookHandler current_hash for a command hook.

    Codex trusts unmanaged hooks by comparing hooks.state[*].trusted_hash to
    a SHA-256 over a normalized TOML-derived identity.  This mirrors
    codex-rs/hooks/src/engine/discovery.rs::command_hook_hash.
    """
    timeout = int(hook.get("timeout") or 600)
    normalized_hook = {
        "async": bool(hook.get("async", False)),
        "command": str(hook.get("command", "")),
        "timeout": max(timeout, 1),
        "type": "command",
    }
    status_message = hook.get("statusMessage")
    if status_message is not None:
        normalized_hook["statusMessage"] = status_message

    identity = {
        "event_name": event_label,
        "hooks": [normalized_hook],
    }
    if matcher is not None:
        identity["matcher"] = matcher
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _codex_hook_trust_entries(working_dir: str, hooks: dict) -> tuple[str, list[tuple[str, str]]]:
    source_path = _codex_absolute_path(Path(working_dir) / ".codex" / "hooks.json")
    entries: list[tuple[str, str]] = []
    for event_name, groups in hooks.items():
        event_label = _CODEX_HOOK_EVENT_LABELS.get(event_name)
        if not event_label:
            continue
        for group_index, group in enumerate(groups or []):
            matcher = group.get("matcher")
            for handler_index, hook in enumerate(group.get("hooks", []) or []):
                if hook.get("type") != "command" or not _is_torque_hook(hook):
                    continue
                key = f"{source_path}:{event_label}:{group_index}:{handler_index}"
                entries.append((
                    key,
                    _codex_command_hook_hash(event_label, matcher, hook),
                ))
    return source_path, entries


def _update_codex_user_config(update) -> bool:
    try:
        codex_home = _codex_home()
        codex_home.mkdir(parents=True, exist_ok=True)
        config_file = codex_home / "config.toml"
        lock_file = codex_home / ".torque-hooks.lock"
        with open(lock_file, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            content = config_file.read_text() if config_file.exists() else ""
            updated = update(content)
            if updated.strip():
                config_file.write_text(updated.strip() + "\n")
            else:
                config_file.unlink(missing_ok=True)
            fcntl.flock(lock, fcntl.LOCK_UN)
        return True
    except Exception:
        return False


def _install_codex_hook_trust(working_dir: str, hooks: dict) -> bool:
    source_path, entries = _codex_hook_trust_entries(working_dir, hooks)
    if not entries:
        return True

    marker = _hook_trust_marker(source_path)
    lines = [marker]
    for key, trusted_hash in entries:
        lines.extend([
            f"[hooks.state.{json.dumps(key)}]",
            f"trusted_hash = {json.dumps(trusted_hash)}",
            "",
        ])
    lines.append(marker)
    block = "\n".join(lines).rstrip() + "\n"

    def _update(content: str) -> str:
        content = _remove_hook_trust_block(content, source_path).strip()
        return f"{content}\n\n{block}" if content else block

    return _update_codex_user_config(_update)


def _uninstall_codex_hook_trust(working_dir: str) -> bool:
    source_path = _codex_absolute_path(Path(working_dir) / ".codex" / "hooks.json")

    def _update(content: str) -> str:
        return _remove_hook_trust_block(content, source_path)

    return _update_codex_user_config(_update)


def _set_model_instructions_file(content: str, path: str = "") -> str:
    """Set or clear Torque-managed model instructions in config.toml."""
    content = _INSTRUCTIONS_BLOCK_RE.sub("", content).lstrip("\n")
    if not path:
        return content
    block = (
        f"{_INSTRUCTIONS_MARKER}\n"
        f"model_instructions_file = {json.dumps(path)}\n"
    )
    if not content:
        return block
    return block + "\n" + content


class CodexAdapter(AgentAdapter):
    name = "codex"
    display_name = "Codex"
    default_command = "codex"

    def match_process(self, process_name: str) -> bool:
        return _matches_codex_token(process_name)

    def match_command(self, command: str) -> bool:
        first = command.strip().split()[0] if command.strip() else ""
        return _matches_codex_token(first)

    def get_env_vars(self, cell) -> dict[str, str]:
        return {"TORQUE_CELL_ID": cell.id}

    def inject_system_prompt(self, working_dir: str, text: str) -> str:
        del working_dir
        if not text:
            return ""
        return f" {shlex.quote(text)}"

    def inject_persistent_prompt(self, working_dir: str,
                                 filename: str, text: str) -> str:
        """Persist Torque instructions via model_instructions_file config."""
        if not text or not working_dir or not filename:
            return ""
        try:
            torque_dir = Path(working_dir) / ".torque"
            torque_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = torque_dir / filename
            prompt_path.write_text(text.rstrip() + "\n")

            config_dir = Path(working_dir) / ".codex"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file = config_dir / "config.toml"
            content = config_file.read_text() if config_file.exists() else ""
            config_file.write_text(
                _set_model_instructions_file(content, str(prompt_path))
            )

            # Clean up any stale Torque-managed AGENTS.md sections left by older
            # versions so Codex only sees the configured instructions file.
            agents_file = Path(working_dir) / ".codex" / "AGENTS.md"
            if agents_file.exists():
                cleaned = _remove_agents_section(agents_file.read_text(), "")
                if cleaned.strip():
                    agents_file.write_text(cleaned.strip() + "\n")
                else:
                    agents_file.unlink(missing_ok=True)
        except Exception:
            return ""
        return ""

    def uninstall_persistent_prompt(self, working_dir: str,
                                    filename: str = "") -> None:
        """Remove Torque-managed prompt file and stale AGENTS.md sections."""
        try:
            torque_dir = Path(working_dir) / ".torque"
            if filename:
                (torque_dir / filename).unlink(missing_ok=True)
            else:
                for f in torque_dir.glob("torque-system-prompt-*.md"):
                    f.unlink(missing_ok=True)

            config_file = Path(working_dir) / ".codex" / "config.toml"
            if config_file.exists():
                updated = _set_model_instructions_file(
                    config_file.read_text(), ""
                ).strip()
                if updated:
                    config_file.write_text(updated + "\n")
                else:
                    config_file.unlink(missing_ok=True)

            agents_file = Path(working_dir) / ".codex" / "AGENTS.md"
            if not agents_file.exists():
                return
            content = _remove_agents_section(agents_file.read_text(), filename)
            if content.strip():
                agents_file.write_text(content.strip() + "\n")
            else:
                agents_file.unlink(missing_ok=True)
        except Exception:
            pass

    def startup_prompt_from_persistent_prompt(self, text: str) -> str:
        """Codex historically saw Torque's persistent prompt as the first turn."""
        return text or ""

    def resolve_model_flags(self, model: str) -> str:
        if not model:
            return ""
        return f" --model {shlex.quote(model)}"

    def resolve_reasoning_effort_flags(self, reasoning_effort: str) -> str:
        reasoning_effort = str(reasoning_effort or "").strip()
        if not reasoning_effort:
            return ""
        return f" -c model_reasoning_effort={shlex.quote(reasoning_effort)}"

    def get_reasoning_effort_options(self) -> list[str]:
        return ["low", "medium", "high", "xhigh"]

    def get_resume_command(self, boot_cmd: str, session_id: str) -> str | None:
        parts = shlex.split(boot_cmd)
        if not parts:
            return None
        opts, prompt = _split_boot_args(boot_cmd)
        cmd = [parts[0], "resume", *opts, session_id]
        if prompt:
            cmd.append(prompt)
        return " ".join(shlex.quote(p) for p in cmd)

    def get_input_ready_policy(self) -> InputReadyPolicy:
        """Wait for the Codex composer to fully initialize before first send.

        post_ready_delay=2.5s: the composer banner ('OpenAI Codex' + model
        + directory + '›') can render before the input pump and MCP layer
        are actually ready to consume the first prompt. A buffer after
        screen-detection avoids dispatching into a half-initialized runtime
        (the timing race observed in 2026-05).
        """
        return InputReadyPolicy(
            enabled=True,
            timeout_seconds=8.0,
            poll_interval_seconds=0.25,
            stable_polls=2,
            post_ready_delay=2.5,
        )

    def is_input_ready_screen(self, screen_text: str) -> bool:
        lower = screen_text.lower()
        return (
            "openai codex" in lower
            and "model:" in lower
            and "directory:" in lower
            and "›" in screen_text
        )

    def get_hook_config(self, cell) -> dict | None:
        """Return the Codex hooks.json structure for Torque integration.

        All hooks use command type with curl to POST to Torque's /events
        endpoint.  Codex hooks receive JSON on stdin.
        """
        timeout = 8

        def _cmd_hook(matcher=None):
            h = {"type": "command", "timeout": timeout,
                 "command": (
                     _torque_event_curl_command(_torque_hook_url())
                 )}
            entry = {"hooks": [h]}
            if matcher:
                entry["matcher"] = matcher
            return [entry]

        return {
            "hooks": {
                "SessionStart": _cmd_hook(),
                "PreToolUse": _cmd_hook(".*"),
                # PostToolUse omitted — Codex command hooks print a noisy
                # "Running PostToolUse hook" message for every tool call.
                # The tool_end state reset is cosmetic and happens naturally
                # when the next PreToolUse or Stop event arrives.
                "Stop": _cmd_hook(),
            }
        }

    def install_hooks(self, working_dir: str) -> bool:
        """Write Torque hooks into .codex/hooks.json.

        Merges with any existing hooks — Torque hooks are identified by
        the localhost /events target in their command string.
        Returns True if hooks were installed successfully.
        """
        hooks_dir = Path(working_dir) / ".codex"
        hooks_file = hooks_dir / "hooks.json"

        try:
            hooks_dir.mkdir(parents=True, exist_ok=True)

            existing = {}
            if hooks_file.exists():
                text = hooks_file.read_text().strip()
                if text:
                    existing = json.loads(text)

            # Remove any stale Torque hooks first
            existing_hooks = existing.get("hooks", {})
            for event in list(existing_hooks):
                existing_hooks[event] = [
                    entry for entry in existing_hooks[event]
                    if not any(_is_torque_hook(h) for h in entry.get("hooks", []))
                ]
                if not existing_hooks[event]:
                    del existing_hooks[event]

            # Add fresh Torque hooks
            torque_config = self.get_hook_config(None)
            for event, entries in torque_config["hooks"].items():
                if event in existing_hooks:
                    existing_hooks[event].extend(entries)
                else:
                    existing_hooks[event] = entries

            existing["hooks"] = existing_hooks
            hooks_file.write_text(json.dumps(existing, indent=2))
            return _install_codex_hook_trust(working_dir, existing_hooks)
        except Exception:
            return False

    def uninstall_hooks(self, working_dir: str):
        """Remove Torque hooks from .codex/hooks.json.

        If the file only contained Torque hooks, deletes it entirely.
        """
        hooks_file = Path(working_dir) / ".codex" / "hooks.json"
        if not hooks_file.exists():
            _uninstall_codex_hook_trust(working_dir)
            return

        try:
            text = hooks_file.read_text().strip()
            if not text:
                hooks_file.unlink(missing_ok=True)
                _uninstall_codex_hook_trust(working_dir)
                return

            existing = json.loads(text)
            hooks = existing.get("hooks", {})

            for event in list(hooks):
                hooks[event] = [
                    entry for entry in hooks[event]
                    if not any(_is_torque_hook(h) for h in entry.get("hooks", []))
                ]
                if not hooks[event]:
                    del hooks[event]

            if hooks:
                existing["hooks"] = hooks
            else:
                existing.pop("hooks", None)

            if not existing:
                hooks_file.unlink(missing_ok=True)
            else:
                hooks_file.write_text(json.dumps(existing, indent=2))
            _uninstall_codex_hook_trust(working_dir)
        except Exception:
            pass  # Best-effort cleanup

    def install_mcp_config(self, working_dir: str, *,
                           mcp_entrypoint: str = "") -> bool:
        """Write Torque MCP server entry into .codex/config.toml.

        Uses regex text manipulation for both reading and writing to
        avoid needing a TOML writer dependency.
        Returns True if config was installed successfully.
        """
        config_dir = Path(working_dir) / ".codex"
        config_file = config_dir / "config.toml"

        stdio_spec = build_stdio_launch_spec(mcp_entrypoint)
        if stdio_spec:
            torque_section = (
                f"\n{_MCP_MARKER}\n"
                "[mcp_servers.torque]\n"
                f"command = {json.dumps(stdio_spec['command'])}\n"
                f"args = {json.dumps(stdio_spec['args'])}\n"
            )
        else:
            torque_section = (
                f"\n{_MCP_MARKER}\n"
                "[mcp_servers.torque]\n"
                f'url = "{_torque_mcp_url()}"\n'
                'env_http_headers = { "X-Torque-Cell-Id" = "TORQUE_CELL_ID" }\n'
            )

        try:
            config_dir.mkdir(parents=True, exist_ok=True)

            content = ""
            if config_file.exists():
                content = config_file.read_text()

            # Remove existing Torque MCP section before mutating the rest of the
            # file so we don't accidentally consume adjacent managed blocks.
            content = _MCP_SECTION_RE.sub("", content)

            # Enable Codex hooks so hooks.json is actually loaded.
            content = _ensure_codex_hooks_enabled(content)

            # Append fresh section
            content = content.rstrip("\n") + "\n" + torque_section
            config_file.write_text(content)
            return True
        except Exception:
            return False

    def uninstall_mcp_config(self, working_dir: str):
        """Remove Torque MCP server entry from .codex/config.toml.

        If the file becomes empty, deletes it entirely.
        """
        config_file = Path(working_dir) / ".codex" / "config.toml"
        if not config_file.exists():
            return

        try:
            content = config_file.read_text()
            content = _remove_codex_hooks_enabled(content)
            content = _MCP_SECTION_RE.sub("", content)
            content = content.strip()

            if not content:
                config_file.unlink(missing_ok=True)
            else:
                config_file.write_text(content + "\n")
        except Exception:
            pass  # Best-effort cleanup

    def parse_event(self, raw: dict, cell) -> AgentEvent | None:
        """Parse a Codex hook payload into a normalized AgentEvent.

        Codex hooks receive JSON on stdin and POST it to Torque via curl.
        The payload structure differs from Claude Code — field names
        are mapped defensively.
        """
        # Codex hooks include a "type" or "hook_event_name" field
        hook_event = raw.get("hook_event_name", "") or raw.get("type", "")
        now = time.time()

        if hook_event == "SessionStart":
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="session_start",
                data={
                    "model": raw.get("model", ""),
                    "session_id": raw.get("session_id", ""),
                },
            )

        if hook_event == "Stop":
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="session_end",
                data={
                    "reason": raw.get("stop_reason")
                    or raw.get("stopReason", "completed"),
                    "summary": raw.get("last_assistant_message") or "",
                },
            )

        if hook_event == "PreToolUse":
            tool = raw.get("tool_name", "") or raw.get("name", "")
            inp = raw.get("tool_input", {}) or raw.get("input", {})
            # Codex currently fires Pre/PostToolUse only for Bash
            if tool.lower() in ("bash", "shell", "command"):
                cmd = inp.get("command", "") or inp.get("cmd", "")
                detail = f"Running: {_truncate(cmd, 40)}" if cmd else f"Using {tool}"
            else:
                detail = f"Using {tool}" if tool else "Working"
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_start",
                data={"tool": tool, "input": inp, "detail": detail},
            )

        if hook_event == "PostToolUse":
            tool = raw.get("tool_name", "") or raw.get("name", "")
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_end",
                data={"tool": tool, "success": True},
            )

        return None
