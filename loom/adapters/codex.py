"""Codex CLI adapter — full integration via command hooks and MCP."""

import json
import re
import shlex
import time
from pathlib import Path

from .base import AgentAdapter, AgentEvent, InputReadyPolicy

# Marker URL used to identify Loom-managed hooks during cleanup
LOOM_HOOK_URL = "http://localhost:18932/events"

# Marker comment for Loom-managed MCP config section
_MCP_MARKER = "# -- Loom MCP server (managed by Loom, do not edit) --"
_FEATURE_MARKER = "# -- Loom Codex hooks feature (managed by Loom, do not edit) --"
_INSTRUCTIONS_MARKER = (
    "# -- Loom Codex model instructions (managed by Loom, do not edit) --"
)

# Marker for Loom-managed AGENTS.md section (persistent system prompt)
_LEGACY_AGENTS_MARKER = "<!-- Loom system prompt (managed by Loom, do not edit) -->"
_ANY_AGENTS_SECTION_RE = re.compile(
    r"<!-- Loom system prompt(?:: [^\n]+)? "
    r"\(managed by Loom, do not edit\) -->\n"
    r"(?:(?!<!-- Loom system prompt(?:: [^\n]+)? "
    r"\(managed by Loom, do not edit\) -->)[\s\S])*?"
    r"<!-- Loom system prompt(?:: [^\n]+)? "
    r"\(managed by Loom, do not edit\) -->\n?"
)

# Regex to match the Loom MCP section in config.toml (marker through next
# section header or EOF)
_MCP_SECTION_RE = re.compile(
    r"\n?" + re.escape(_MCP_MARKER) + r"\n"
    r"\[mcp_servers\.loom\]\n"
    r"(?:(?!\n\[)[^\n]*\n)*",
)
_FEATURES_SECTION_RE = re.compile(
    r"(?ms)(^|\n)\[features\]\n(?P<body>(?:(?!\n\[).*\n?)*)"
)
_FEATURE_LINE_RE = re.compile(r"(?m)^codex_hooks\s*=.*(?:\n|$)")
_MANAGED_FEATURE_BLOCK_RE = re.compile(
    r"\n?" + re.escape(_FEATURE_MARKER) + r"\ncodex_hooks = true\n?"
)
_MANAGED_FEATURE_SECTION_RE = re.compile(
    r"\n?" + re.escape(_FEATURE_MARKER) + r"\n\[features\]\ncodex_hooks = true\n?"
)
_INSTRUCTIONS_BLOCK_RE = re.compile(
    r"\n?" + re.escape(_INSTRUCTIONS_MARKER) + r"\n"
    r'model_instructions_file = "(?:[^"\\]|\\.)*"\n?',
)


def _agents_marker(filename: str) -> str:
    """Return a stable marker for a specific Loom-managed prompt block."""
    return (
        f"<!-- Loom system prompt: {filename} "
        f"(managed by Loom, do not edit) -->"
    )


def _remove_agents_section(content: str, filename: str = "") -> str:
    """Remove a Loom-managed section from AGENTS.md content."""
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


def _is_loom_hook(hook: dict) -> bool:
    """Check if a single hook entry was installed by Loom (by URL marker)."""
    cmd = hook.get("command", "")
    return LOOM_HOOK_URL in cmd


def _ensure_codex_hooks_enabled(content: str) -> str:
    """Ensure config.toml enables Codex hooks for Loom-managed sessions."""
    content = _MANAGED_FEATURE_SECTION_RE.sub("", content)

    def _replace_features(match: re.Match[str]) -> str:
        prefix = match.group(1)
        body = _MANAGED_FEATURE_BLOCK_RE.sub("", match.group("body"))
        body, replaced = _FEATURE_LINE_RE.subn(
            f"{_FEATURE_MARKER}\ncodex_hooks = true\n", body, count=1
        )
        if not replaced:
            if body and not body.endswith("\n"):
                body += "\n"
            body += f"{_FEATURE_MARKER}\ncodex_hooks = true\n"
        return f"{prefix}[features]\n{body}"

    updated, replaced = _FEATURES_SECTION_RE.subn(_replace_features, content, count=1)
    if replaced:
        return updated

    content = content.rstrip("\n")
    if content:
        content += "\n"
    content += f"\n{_FEATURE_MARKER}\n[features]\ncodex_hooks = true\n"
    return content


def _remove_codex_hooks_enabled(content: str) -> str:
    """Remove Loom-managed Codex hook feature config from config.toml."""
    content = _MANAGED_FEATURE_SECTION_RE.sub("", content)
    content = _MANAGED_FEATURE_BLOCK_RE.sub("", content)
    return content


def _set_model_instructions_file(content: str, path: str = "") -> str:
    """Set or clear Loom-managed model instructions in config.toml."""
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
        return process_name.lower() == "codex"

    def match_command(self, command: str) -> bool:
        first = command.strip().split()[0] if command.strip() else ""
        return first.lower() == "codex"

    def get_env_vars(self, cell) -> dict[str, str]:
        return {"LOOM_CELL_ID": cell.id}

    def inject_system_prompt(self, working_dir: str, text: str) -> str:
        del working_dir
        if not text:
            return ""
        return f" {shlex.quote(text)}"

    def inject_persistent_prompt(self, working_dir: str,
                                 filename: str, text: str) -> str:
        """Persist Loom instructions via model_instructions_file config."""
        if not text or not working_dir or not filename:
            return ""
        try:
            loom_dir = Path(working_dir) / ".loom"
            loom_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = loom_dir / filename
            prompt_path.write_text(text.rstrip() + "\n")

            config_dir = Path(working_dir) / ".codex"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file = config_dir / "config.toml"
            content = config_file.read_text() if config_file.exists() else ""
            config_file.write_text(
                _set_model_instructions_file(content, str(prompt_path))
            )

            # Clean up any stale Loom-managed AGENTS.md sections left by older
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
        """Remove Loom-managed prompt file and stale AGENTS.md sections."""
        try:
            loom_dir = Path(working_dir) / ".loom"
            if filename:
                (loom_dir / filename).unlink(missing_ok=True)
            else:
                for f in loom_dir.glob("loom-system-prompt-*.md"):
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

    def resolve_model_flags(self, model: str) -> str:
        if not model:
            return ""
        return f" --model {shlex.quote(model)}"

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
        """Wait for the Codex composer to fully initialize before first send."""
        return InputReadyPolicy(
            enabled=True,
            timeout_seconds=8.0,
            poll_interval_seconds=0.25,
            stable_polls=2,
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
        """Return the Codex hooks.json structure for Loom integration.

        All hooks use command type with curl to POST to Loom's /events
        endpoint.  Codex hooks receive JSON on stdin.
        """
        timeout = 3

        def _cmd_hook(matcher=None):
            h = {"type": "command", "timeout": timeout,
                 "command": (
                     'curl -s -X POST ' + LOOM_HOOK_URL
                     + ' -H "Content-Type: application/json"'
                     + ' -H "X-Loom-Cell-Id: $LOOM_CELL_ID"'
                     + ' -d "$(cat)" > /dev/null 2>&1'
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
        """Write Loom hooks into .codex/hooks.json.

        Merges with any existing hooks — Loom hooks are identified by
        the LOOM_HOOK_URL in their command string.
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

            # Remove any stale Loom hooks first
            existing_hooks = existing.get("hooks", {})
            for event in list(existing_hooks):
                existing_hooks[event] = [
                    entry for entry in existing_hooks[event]
                    if not any(_is_loom_hook(h) for h in entry.get("hooks", []))
                ]
                if not existing_hooks[event]:
                    del existing_hooks[event]

            # Add fresh Loom hooks
            loom_config = self.get_hook_config(None)
            for event, entries in loom_config["hooks"].items():
                if event in existing_hooks:
                    existing_hooks[event].extend(entries)
                else:
                    existing_hooks[event] = entries

            existing["hooks"] = existing_hooks
            hooks_file.write_text(json.dumps(existing, indent=2))
            return True
        except Exception:
            return False

    def uninstall_hooks(self, working_dir: str):
        """Remove Loom hooks from .codex/hooks.json.

        If the file only contained Loom hooks, deletes it entirely.
        """
        hooks_file = Path(working_dir) / ".codex" / "hooks.json"
        if not hooks_file.exists():
            return

        try:
            text = hooks_file.read_text().strip()
            if not text:
                hooks_file.unlink(missing_ok=True)
                return

            existing = json.loads(text)
            hooks = existing.get("hooks", {})

            for event in list(hooks):
                hooks[event] = [
                    entry for entry in hooks[event]
                    if not any(_is_loom_hook(h) for h in entry.get("hooks", []))
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
        except Exception:
            pass  # Best-effort cleanup

    def install_mcp_config(self, working_dir: str) -> bool:
        """Write Loom MCP server entry into .codex/config.toml.

        Uses regex text manipulation for both reading and writing to
        avoid needing a TOML writer dependency.
        Returns True if config was installed successfully.
        """
        config_dir = Path(working_dir) / ".codex"
        config_file = config_dir / "config.toml"

        loom_section = (
            f"\n{_MCP_MARKER}\n"
            "[mcp_servers.loom]\n"
            'url = "http://127.0.0.1:18932/mcp"\n'
            'env_http_headers = { "X-Loom-Cell-Id" = "LOOM_CELL_ID" }\n'
        )

        try:
            config_dir.mkdir(parents=True, exist_ok=True)

            content = ""
            if config_file.exists():
                content = config_file.read_text()

            # Remove existing Loom MCP section before mutating the rest of the
            # file so we don't accidentally consume adjacent managed blocks.
            content = _MCP_SECTION_RE.sub("", content)

            # Enable Codex hooks so hooks.json is actually loaded.
            content = _ensure_codex_hooks_enabled(content)

            # Append fresh section
            content = content.rstrip("\n") + "\n" + loom_section
            config_file.write_text(content)
            return True
        except Exception:
            return False

    def uninstall_mcp_config(self, working_dir: str):
        """Remove Loom MCP server entry from .codex/config.toml.

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

        Codex hooks receive JSON on stdin and POST it to Loom via curl.
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
