"""Claude Code adapter — full integration via HTTP hooks."""

import json
import time
from pathlib import Path

from .base import AgentAdapter, AgentEvent

# Marker URL used to identify Loom-managed hooks during cleanup
LOOM_HOOK_URL = "http://localhost:18932/events"


# Tool name → human-readable activity detail
_TOOL_ACTIVITIES = {
    "Bash": lambda inp: f"Running: {_truncate(inp.get('command', ''), 40)}",
    "Edit": lambda inp: f"Editing {_basename(inp.get('file_path', ''))}",
    "Write": lambda inp: f"Writing {_basename(inp.get('file_path', ''))}",
    "Read": lambda inp: f"Reading {_basename(inp.get('file_path', ''))}",
    "Grep": lambda inp: "Searching codebase",
    "Glob": lambda inp: "Searching files",
    "Agent": lambda inp: f"Subagent: {inp.get('description', 'working')}",
    "WebFetch": lambda inp: "Fetching web page",
    "WebSearch": lambda inp: "Searching web",
    "Skill": lambda inp: f"Running /{inp.get('skill', '?')}",
}


def _truncate(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _is_loom_hook_entry(entry: dict) -> bool:
    """Check if a hook entry was installed by Loom (by URL marker)."""
    for hook in entry.get("hooks", []):
        if hook.get("url") == LOOM_HOOK_URL:
            return True
        # Command hooks that curl to Loom
        if hook.get("type") == "command" and LOOM_HOOK_URL in hook.get("command", ""):
            return True
    return False


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude-code"
    display_name = "Claude Code"

    def match_process(self, process_name: str) -> bool:
        return process_name.lower() in ("claude", "claude-code")

    def match_command(self, command: str) -> bool:
        """Match against the boot command (e.g. 'claude', 'claude --model ...')."""
        first = command.strip().split()[0] if command.strip() else ""
        return first.lower() in ("claude", "claude-code")

    def get_env_vars(self, cell) -> dict[str, str]:
        return {"LOOM_CELL_ID": cell.id}

    def get_hook_config(self, cell) -> dict | None:
        """Return the Claude Code hooks config to write for this cell.

        Most hooks use type: "http" (POST directly to Loom). SessionStart
        requires type: "command" (Claude Code limitation — HTTP hooks are
        not supported for SessionStart/WorktreeCreate/WorktreeRemove).
        """
        url = "http://localhost:18932/events"
        timeout = 3

        def _http_hook(matcher=None):
            h = {"type": "http", "url": url, "timeout": timeout,
                 "headers": {"X-Loom-Cell-Id": "$LOOM_CELL_ID"},
                 "allowedEnvVars": ["LOOM_CELL_ID"]}
            entry = {"hooks": [h]}
            if matcher:
                entry["matcher"] = matcher
            return [entry]

        def _cmd_hook(matcher=None):
            """Command hook that curls to Loom (for events that don't support HTTP hooks)."""
            h = {"type": "command", "timeout": timeout,
                 "command": (
                     'curl -s -X POST ' + url
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
                # SessionStart only supports command hooks (Claude Code limitation)
                "SessionStart": _cmd_hook(),
                "PreToolUse": _http_hook(".*"),
                "PostToolUse": _http_hook(".*"),
                "PostToolUseFailure": _http_hook(".*"),
                "Notification": _http_hook(".*"),
                "Stop": _http_hook(),
                "SubagentStart": _http_hook(".*"),
                "SubagentStop": _http_hook(".*"),
                "StopFailure": _http_hook(".*"),
            }
        }

    def install_hooks(self, working_dir: str) -> bool:
        """Write Loom hooks into .claude/settings.local.json.

        Merges with any existing settings — Loom hooks are identified by
        their URL (LOOM_HOOK_URL) so they can be cleanly removed later.
        Returns True if hooks were installed successfully.
        """
        settings_dir = Path(working_dir) / ".claude"
        settings_file = settings_dir / "settings.local.json"

        try:
            settings_dir.mkdir(parents=True, exist_ok=True)

            # Read existing settings (if any)
            existing = {}
            if settings_file.exists():
                text = settings_file.read_text().strip()
                if text:
                    existing = json.loads(text)

            # Remove any stale Loom hooks first
            existing_hooks = existing.get("hooks", {})
            for event in list(existing_hooks):
                existing_hooks[event] = [
                    entry for entry in existing_hooks[event]
                    if not _is_loom_hook_entry(entry)
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
            settings_file.write_text(json.dumps(existing, indent=2))
            return True
        except Exception:
            return False

    def uninstall_hooks(self, working_dir: str):
        """Remove Loom hooks from .claude/settings.local.json.

        If the file only contained Loom hooks, deletes it entirely.
        """
        settings_file = Path(working_dir) / ".claude" / "settings.local.json"
        if not settings_file.exists():
            return

        try:
            text = settings_file.read_text().strip()
            if not text:
                settings_file.unlink(missing_ok=True)
                return

            existing = json.loads(text)
            hooks = existing.get("hooks", {})

            # Remove Loom-managed hook entries
            for event in list(hooks):
                hooks[event] = [
                    entry for entry in hooks[event]
                    if not _is_loom_hook_entry(entry)
                ]
                if not hooks[event]:
                    del hooks[event]

            if hooks:
                existing["hooks"] = hooks
            else:
                existing.pop("hooks", None)

            # If nothing left, delete the file
            if not existing:
                settings_file.unlink(missing_ok=True)
            else:
                settings_file.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass  # Best-effort cleanup

    def parse_event(self, raw: dict, cell) -> AgentEvent | None:
        """Parse a Claude Code hook HTTP payload into a normalized AgentEvent."""
        hook_event = raw.get("hook_event_name", "")
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
                    "reason": "completed",
                    "summary": raw.get("last_assistant_message", ""),
                },
            )

        if hook_event == "PreToolUse":
            tool = raw.get("tool_name", "")
            inp = raw.get("tool_input", {})
            detail_fn = _TOOL_ACTIVITIES.get(tool)
            detail = detail_fn(inp) if detail_fn else f"Using {tool}"
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_start",
                data={"tool": tool, "input": inp, "detail": detail},
            )

        if hook_event == "PostToolUse":
            tool = raw.get("tool_name", "")
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_end",
                data={"tool": tool, "success": True},
            )

        if hook_event == "PostToolUseFailure":
            tool = raw.get("tool_name", "")
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_end",
                data={"tool": tool, "success": False},
            )

        if hook_event == "Notification":
            ntype = raw.get("notification_type", "")
            if ntype == "permission_prompt":
                # Agent is blocked — needs user to approve an action
                return AgentEvent(
                    cell_id=cell.id, timestamp=now,
                    event_type="waiting",
                    data={"reason": "permission needed"},
                )
            if ntype == "idle_prompt":
                # Agent finished its turn — waiting for next user message
                return AgentEvent(
                    cell_id=cell.id, timestamp=now,
                    event_type="session_end",
                    data={
                        "reason": "idle",
                        "summary": raw.get("last_assistant_message", ""),
                    },
                )
            return None

        if hook_event == "StopFailure":
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="error",
                data={
                    "error": raw.get("error", "unknown"),
                    "detail": raw.get("error_details", ""),
                },
            )

        if hook_event == "SubagentStart":
            agent_type = raw.get("agent_type", "")
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="activity_change",
                data={"activity": "subagent", "detail": f"Subagent: {agent_type}"},
            )

        if hook_event == "SubagentStop":
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="activity_change",
                data={"activity": "thinking", "detail": ""},
            )

        return None
