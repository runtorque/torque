"""Codex CLI adapter — full integration via command hooks and MCP."""

import json
import re
import shlex
import time
from pathlib import Path

from .base import AgentAdapter, AgentEvent

# Marker URL used to identify Loom-managed hooks during cleanup
LOOM_HOOK_URL = "http://localhost:18932/events"

# Marker comment for Loom-managed MCP config section
_MCP_MARKER = "# -- Loom MCP server (managed by Loom, do not edit) --"
_FEATURE_MARKER = "# -- Loom Codex hooks feature (managed by Loom, do not edit) --"

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


def _truncate(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s


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

    def get_resume_command(self, boot_cmd: str, session_id: str) -> str | None:
        base = boot_cmd.strip().split()[0]
        return f"{base} resume {shlex.quote(session_id)}"

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
                "PostToolUse": _cmd_hook(".*"),
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
