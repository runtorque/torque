"""Claude Code adapter — full integration via HTTP hooks."""

import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
import shlex
from textwrap import dedent

from ..context_window import normalize_context_window_usage
from .base import AgentAdapter, AgentEvent, InputReadyPolicy
from .mcp_launch import build_stdio_launch_spec

_TORQUE_EVENT_URL_RE = re.compile(r"http://(?:localhost|127\.0\.0\.1):\d+/events")

# Skill directory prefix used to identify Torque-managed skills during cleanup
TORQUE_SKILL_PREFIX = "torque-"
_AUTO_MEMORY_ORIGINAL_REL_PATH = Path(
    ".torque/claude-auto-memory-original.json"
)
_STATUSLINE_ORIGINAL_REL_PATH = Path(
    ".torque/claude-statusline-original.json"
)
_STATUSLINE_PROXY_REL_PATH = Path(
    ".torque/claude-statusline-proxy.py"
)
_TORQUE_STATUSLINE_MARKER = "claude-statusline-proxy.py"

# ---------------------------------------------------------------------------
# Slash command (skill) definitions — injected into .claude/skills/
# ---------------------------------------------------------------------------

SKILLS = {
    "torque-status": {
        "frontmatter": {
            "name": "torque-status",
            "description": (
                "Show current Torque agent context, linked task, "
                "and pipeline state"
            ),
            "allowed-tools": "mcp__torque__torque_context",
        },
        "body": dedent("""\
            Show the current Torque agent context using the torque_context MCP tool.

            Format the output clearly:
            - Agent name, group, and status
            - Current task title, lane, and action
            - Pipeline info (parent task, depth) if this is a derived task
            - Worktree branch and diff stats if in a worktree
        """),
    },
    "torque-board": {
        "frontmatter": {
            "name": "torque-board",
            "description": (
                "Show the Torque task board with all lanes and tasks"
            ),
            "allowed-tools": "Bash",
        },
        "body": dedent("""\
            Show the current Torque task board.

            ## Board state
            !`torque board list`

            Summarize the board state: how many tasks in each lane,
            any tasks that need attention (blocked/error labels),
            and what's currently in progress.
        """),
    },
    "torque-done": {
        "frontmatter": {
            "name": "torque-done",
            "description": "Mark the current Torque task as complete",
            "allowed-tools": "mcp__torque__torque_done",
            "argument-hint": "[completion message]",
        },
        "body": dedent("""\
            Mark the current task as complete using the torque_done MCP tool.

            If $ARGUMENTS is provided, use it as the completion message.
            Otherwise, write a brief summary of what was accomplished.
        """),
    },
}


def _render_skill_md(skill: dict) -> str:
    """Render a skill dict into SKILL.md content with YAML frontmatter."""
    lines = ["---"]
    for key, value in skill["frontmatter"].items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(skill["body"])
    return "\n".join(lines)


@contextmanager
def _locked_mcp_json(mcp_file: Path):
    """Hold the per-worktree .mcp.json advisory lock."""
    lock_file = mcp_file.with_name(f"{mcp_file.name}.lock")
    with lock_file.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write_mcp_json(mcp_file: Path, data: dict):
    """Atomically replace .mcp.json using a temp file in the same directory."""
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{mcp_file.name}.", suffix=".tmp", dir=str(mcp_file.parent)
    )
    try:
        with os.fdopen(fd, "w") as tmp:
            tmp.write(json.dumps(data, indent=2) + "\n")
        os.rename(tmp_name, mcp_file)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _auto_memory_original_file(working_dir: str) -> Path:
    return Path(working_dir) / _AUTO_MEMORY_ORIGINAL_REL_PATH


def _remember_and_disable_auto_memory(working_dir: str, settings: dict) -> None:
    """Disable Claude Code auto-memory while preserving the user's setting."""
    marker = _auto_memory_original_file(working_dir)
    try:
        if not marker.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(
                    {
                        "present": "autoMemoryEnabled" in settings,
                        "value": settings.get("autoMemoryEnabled"),
                    },
                    indent=2,
                )
                + "\n"
            )
    except Exception:
        # Hook installation should still succeed; failing to write the
        # restoration marker only means cleanup will leave the safer disabled
        # value behind.
        pass
    settings["autoMemoryEnabled"] = False


def _restore_auto_memory_setting(working_dir: str, settings: dict) -> None:
    """Restore the auto-memory setting saved by install_hooks, if present."""
    marker = _auto_memory_original_file(working_dir)
    if not marker.exists():
        return
    try:
        data = json.loads(marker.read_text() or "{}")
        if data.get("present"):
            settings["autoMemoryEnabled"] = data.get("value")
        else:
            settings.pop("autoMemoryEnabled", None)
        marker.unlink(missing_ok=True)
    except Exception:
        pass


def _statusline_original_file(working_dir: str) -> Path:
    return Path(working_dir) / _STATUSLINE_ORIGINAL_REL_PATH


def _statusline_proxy_file(working_dir: str) -> Path:
    return Path(working_dir) / _STATUSLINE_PROXY_REL_PATH


def _is_torque_statusline_entry(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    command = str(entry.get("command", "") or "")
    return _TORQUE_STATUSLINE_MARKER in command


def _torque_statusline_command(working_dir: str) -> str:
    return "python3 " + shlex.quote(str(_statusline_proxy_file(working_dir)))


def _statusline_proxy_source(original_file: Path, event_url: str) -> str:
    """Return the Torque-managed Claude statusLine proxy script."""
    original_path = json.dumps(str(original_file))
    event_url_literal = json.dumps(str(event_url or _torque_hook_url()))
    return dedent(f"""\
        #!/usr/bin/env python3
        import hashlib
        import json
        import os
        import subprocess
        import sys
        import urllib.request

        ORIGINAL_FILE = {original_path}
        EVENT_URL = {event_url_literal}


        def _load_original():
            try:
                with open(ORIGINAL_FILE, "r") as fh:
                    data = json.load(fh)
            except Exception:
                return {{}}
            if not isinstance(data, dict) or not data.get("present"):
                return {{}}
            value = data.get("value")
            return value if isinstance(value, dict) else {{}}


        def _post_event(raw):
            try:
                payload = json.loads(raw.decode("utf-8") or "{{}}")
            except Exception:
                payload = {{}}
            if not isinstance(payload, dict):
                payload = {{}}
            payload.setdefault("hook_event_name", "StatusLine")
            body_for_hash = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            payload.setdefault(
                "event_id", hashlib.sha256(body_for_hash).hexdigest()
            )
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request = urllib.request.Request(
                EVENT_URL,
                data=body,
                headers={{
                    "Content-Type": "application/json",
                    "X-Torque-Cell-Id": os.environ.get("TORQUE_CELL_ID", ""),
                }},
                method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=1).read()
            except Exception:
                pass
            return payload


        def _forward_original(raw):
            original = _load_original()
            command = str(original.get("command", "") or "")
            if original.get("type") != "command" or not command:
                return False
            try:
                completed = subprocess.run(
                    command,
                    input=raw,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                sys.stdout.buffer.write(completed.stdout)
                sys.stderr.buffer.write(completed.stderr)
            except Exception:
                # A configured project-local statusLine existed, so avoid
                # printing Torque fallback text that would change the user's
                # visible line on proxy failures.
                pass
            return True


        def _neutral_status(payload):
            context = payload.get("context_window")
            if not isinstance(context, dict):
                context = {{}}
            pct = context.get("used_percentage")
            try:
                pct = float(pct)
            except Exception:
                pct = None
            if pct is None:
                sys.stdout.write("ctx --\\n")
            else:
                sys.stdout.write(f"ctx {{pct:.0f}}%\\n")


        def main():
            raw = sys.stdin.buffer.read()
            payload = _post_event(raw)
            if not _forward_original(raw):
                _neutral_status(payload)


        if __name__ == "__main__":
            main()
    """)


def _install_statusline_proxy(working_dir: str, settings: dict) -> None:
    """Install Torque's Claude statusLine proxy, preserving project-local config."""
    original_file = _statusline_original_file(working_dir)
    proxy_file = _statusline_proxy_file(working_dir)
    event_url = _torque_hook_url()
    existing = settings.get("statusLine")

    if not _is_torque_statusline_entry(existing):
        original_file.parent.mkdir(parents=True, exist_ok=True)
        original_file.write_text(
            json.dumps(
                {
                    "present": "statusLine" in settings,
                    "value": existing,
                },
                indent=2,
            )
            + "\n"
        )

    proxy_file.parent.mkdir(parents=True, exist_ok=True)
    proxy_file.write_text(_statusline_proxy_source(original_file, event_url))
    os.chmod(proxy_file, 0o755)
    settings["statusLine"] = {
        "type": "command",
        "command": _torque_statusline_command(working_dir),
    }


def _restore_statusline_setting(working_dir: str, settings: dict | None) -> None:
    """Restore the project-local Claude statusLine saved by install_hooks."""
    original_file = _statusline_original_file(working_dir)
    proxy_file = _statusline_proxy_file(working_dir)
    try:
        current = (settings or {}).get("statusLine") if isinstance(settings, dict) else None
        if (
            isinstance(settings, dict)
            and _is_torque_statusline_entry(current)
            and original_file.exists()
        ):
            data = json.loads(original_file.read_text() or "{}")
            if data.get("present"):
                settings["statusLine"] = data.get("value")
            else:
                settings.pop("statusLine", None)
        elif isinstance(settings, dict) and _is_torque_statusline_entry(current):
            settings.pop("statusLine", None)
        original_file.unlink(missing_ok=True)
        proxy_file.unlink(missing_ok=True)
    except Exception:
        pass


def _dict_value(source: dict, *keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _int_value(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _float_value(value, default: float = -1.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _positive_token_value(source: dict, *keys) -> int:
    return max(0, _int_value(_dict_value(source, *keys), 0))


def _claude_model_name(raw: dict) -> str:
    model = raw.get("model")
    if isinstance(model, dict):
        return str(
            model.get("id")
            or model.get("name")
            or model.get("display_name")
            or model.get("displayName")
            or ""
        )
    return str(model or "")


def _claude_statusline_context_window(raw: dict, timestamp: float) -> dict:
    context = raw.get("context_window") or raw.get("contextWindow")
    if not isinstance(context, dict):
        return {}

    limit_tokens = _int_value(
        _dict_value(
            context,
            "context_window_size",
            "contextWindowSize",
            "limit_tokens",
            "limitTokens",
        ),
        0,
    )
    input_tokens = _positive_token_value(
        context, "input_tokens", "inputTokens", "total_input_tokens", "totalInputTokens"
    )
    output_tokens = _positive_token_value(
        context,
        "output_tokens",
        "outputTokens",
        "total_output_tokens",
        "totalOutputTokens",
    )
    cached_input_tokens = _positive_token_value(
        context,
        "cached_input_tokens",
        "cachedInputTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
    )
    cache_creation_tokens = _positive_token_value(
        context, "cache_creation_input_tokens", "cacheCreationInputTokens"
    )
    if cache_creation_tokens:
        cached_input_tokens += cache_creation_tokens
    reasoning_output_tokens = _positive_token_value(
        context,
        "reasoning_output_tokens",
        "reasoningOutputTokens",
    )
    session_total_tokens = _positive_token_value(
        context,
        "session_total_tokens",
        "sessionTotalTokens",
        "total_session_tokens",
        "totalSessionTokens",
    )

    used_tokens = _int_value(
        _dict_value(
            context,
            "used_tokens",
            "usedTokens",
            "total_tokens",
            "totalTokens",
            "current_context_tokens",
            "currentContextTokens",
        ),
        0,
    )
    if used_tokens <= 0:
        used_tokens = input_tokens + output_tokens + reasoning_output_tokens

    used_pct = _float_value(
        _dict_value(context, "used_percentage", "usedPercentage", "used_pct", "usedPct")
    )
    if used_tokens <= 0 and used_pct >= 0 and limit_tokens > 0:
        used_tokens = round((used_pct / 100.0) * limit_tokens)

    # Claude StatusLine can emit null/zero values before the first model
    # response.  Keep context_window empty until both sides are meaningful.
    if used_tokens <= 0 or limit_tokens <= 0:
        return {}

    data = {
        "source": "claude_statusline",
        "model": _claude_model_name(raw),
        "session_id": str(raw.get("session_id") or raw.get("sessionId") or ""),
        "used_tokens": used_tokens,
        "limit_tokens": limit_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "session_total_tokens": session_total_tokens,
        "updated_at": timestamp,
    }
    if used_pct >= 0:
        data["used_pct"] = used_pct
    return normalize_context_window_usage(data, now=timestamp)


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


def _matches_claude_token(value: str) -> bool:
    token = os.path.basename((value or "").strip().lower())
    return (
        token in ("claude", "claude-code")
        or token.startswith("claude-")
        or token.startswith("claude_")
        or token.endswith("-claude")
        or token.endswith("_claude")
        or token.endswith("-claude-code")
        or token.endswith("_claude_code")
    )


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


def _is_torque_event_target(value: str) -> bool:
    return bool(_TORQUE_EVENT_URL_RE.search(value or ""))


def _is_torque_hook_entry(entry: dict) -> bool:
    """Check if a hook entry was installed by Torque (by URL marker)."""
    for hook in entry.get("hooks", []):
        if _is_torque_event_target(hook.get("url", "")):
            return True
        # Command hooks that curl to Torque
        if (
            hook.get("type") == "command"
            and _is_torque_event_target(hook.get("command", ""))
        ):
            return True
    return False


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude-code"
    display_name = "Claude Code"
    default_command = "claude"

    def match_process(self, process_name: str) -> bool:
        return _matches_claude_token(process_name)

    def match_command(self, command: str) -> bool:
        """Match against the boot command (e.g. 'claude', 'claude --model ...')."""
        first = command.strip().split()[0] if command.strip() else ""
        return _matches_claude_token(first)

    def get_env_vars(self, cell) -> dict[str, str]:
        return {"TORQUE_CELL_ID": cell.id}

    def inject_system_prompt(self, working_dir: str, text: str) -> str:
        if not text or not working_dir:
            return ""
        try:
            claude_dir = Path(working_dir) / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            (claude_dir / "instructions.md").write_text(
                text.rstrip() + "\n"
            )
        except Exception:
            return ""
        return ""

    def inject_persistent_prompt(self, working_dir: str,
                                 filename: str, text: str) -> str:
        if not text or not working_dir:
            return ""
        try:
            sp_dir = Path(working_dir) / ".torque"
            sp_dir.mkdir(parents=True, exist_ok=True)
            sp_path = sp_dir / filename
            sp_path.write_text(text.rstrip() + "\n")
            return f" --append-system-prompt-file {shlex.quote(str(sp_path))}"
        except Exception:
            return ""

    def uninstall_persistent_prompt(self, working_dir: str,
                                    filename: str = "") -> None:
        if not working_dir:
            return
        try:
            sp_dir = Path(working_dir) / ".torque"
            if filename:
                (sp_dir / filename).unlink(missing_ok=True)
            else:
                # Remove all Torque system prompt files
                if sp_dir.is_dir():
                    for f in sp_dir.glob("*-system-prompt*.md"):
                        f.unlink(missing_ok=True)
        except Exception:
            pass

    def resolve_model_flags(self, model: str) -> str:
        if not model:
            return ""
        return f" --model {shlex.quote(model)}"

    def resolve_reasoning_effort_flags(self, reasoning_effort: str) -> str:
        reasoning_effort = str(reasoning_effort or "").strip()
        if not reasoning_effort:
            return ""
        return f" --effort {shlex.quote(reasoning_effort)}"

    def get_reasoning_effort_options(self) -> list[str]:
        return ["low", "medium", "high", "xhigh", "max"]

    def get_resume_command(self, boot_cmd: str, session_id: str) -> str | None:
        import shlex
        return f"{boot_cmd} --resume {shlex.quote(session_id)}"

    def get_input_ready_policy(self) -> InputReadyPolicy:
        """Wait for the SessionStart hook before releasing the first send.

        post_ready_delay bumped 0.5 → 2.5s in 2026-05 after empirical evidence
        the hook fires before Claude's input pump is ready to receive the first
        prompt cleanly. The previous 0.5s was insufficient on slower environments.
        """
        return InputReadyPolicy(
            enabled=True,
            hook_event=True,
            timeout_seconds=30.0,
            poll_interval_seconds=0.5,
            stable_polls=2,
            post_ready_delay=2.5,
        )

    def is_input_ready_screen(self, screen_text: str) -> bool:
        """Retained for optional screen-based fallback policies."""
        lower = screen_text.lower()
        return "claude code" in lower or "claude.ai/code" in lower

    def get_input_chunks(self, body: str) -> list[str]:
        """Type multiline prompts using Claude Code's line-feed shortcut.

        Claude Code documents Ctrl+J (ASCII line feed) as a multiline input
        control sequence. Sending LF between lines avoids collapsed paste-mode
        blocks while keeping the final Enter as a real submit.
        """
        if not body:
            return []
        if "\n" not in body:
            return [body]
        lines = body.split("\n")
        chunks: list[str] = []
        for idx, line in enumerate(lines):
            if line:
                chunks.append(line)
            if idx < len(lines) - 1:
                chunks.append("\n")
        return chunks

    def get_hook_config(self, cell) -> dict | None:
        """Return the Claude Code hooks config to write for this cell.

        Most hooks use type: "http" (POST directly to Torque). SessionStart
        requires type: "command" (Claude Code limitation — HTTP hooks are
        not supported for SessionStart/WorktreeCreate/WorktreeRemove).
        """
        url = _torque_hook_url()
        timeout = 3
        cmd_timeout = 8

        def _http_hook(matcher=None):
            h = {"type": "http", "url": url, "timeout": timeout,
                 "headers": {"X-Torque-Cell-Id": "$TORQUE_CELL_ID"},
                 "allowedEnvVars": ["TORQUE_CELL_ID"]}
            entry = {"hooks": [h]}
            if matcher:
                entry["matcher"] = matcher
            return [entry]

        def _cmd_hook(matcher=None):
            """Command hook that curls to Torque (for events that don't support HTTP hooks)."""
            h = {"type": "command", "timeout": cmd_timeout,
                 "command": (
                     _torque_event_curl_command(url)
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
                "SessionEnd": _http_hook(),
                "SubagentStart": _http_hook(".*"),
                "SubagentStop": _http_hook(".*"),
                "StopFailure": _http_hook(".*"),
            }
        }

    def install_hooks(self, working_dir: str) -> bool:
        """Write Torque hooks into .claude/settings.local.json.

        Merges with any existing settings — Torque hooks are identified by
        their localhost /events target so they can be cleanly removed later,
        even across port changes.
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

            # Torque shared memory is the durable memory surface. Disable
            # Claude Code's provider-local MEMORY.md auto-memory for every
            # Torque-spawned Claude session while hooks are installed.
            _remember_and_disable_auto_memory(working_dir, existing)
            _install_statusline_proxy(working_dir, existing)

            # Remove any stale Torque hooks first
            existing_hooks = existing.get("hooks", {})
            for event in list(existing_hooks):
                existing_hooks[event] = [
                    entry for entry in existing_hooks[event]
                    if not _is_torque_hook_entry(entry)
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
            settings_file.write_text(json.dumps(existing, indent=2))
            return True
        except Exception:
            return False

    def uninstall_hooks(self, working_dir: str):
        """Remove Torque hooks from .claude/settings.local.json.

        If the file only contained Torque hooks, deletes it entirely.
        """
        settings_file = Path(working_dir) / ".claude" / "settings.local.json"
        if not settings_file.exists():
            _restore_statusline_setting(working_dir, None)
            return

        try:
            text = settings_file.read_text().strip()
            if not text:
                settings_file.unlink(missing_ok=True)
                return

            existing = json.loads(text)
            hooks = existing.get("hooks", {})

            # Remove Torque-managed hook entries
            for event in list(hooks):
                hooks[event] = [
                    entry for entry in hooks[event]
                    if not _is_torque_hook_entry(entry)
                ]
                if not hooks[event]:
                    del hooks[event]

            if hooks:
                existing["hooks"] = hooks
            else:
                existing.pop("hooks", None)

            _restore_auto_memory_setting(working_dir, existing)
            _restore_statusline_setting(working_dir, existing)

            # If nothing left, delete the file
            if not existing:
                settings_file.unlink(missing_ok=True)
            else:
                settings_file.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass  # Best-effort cleanup

    def install_mcp_config(self, working_dir: str, *,
                           mcp_entrypoint: str = "") -> bool:
        """Write Torque MCP server entry into .mcp.json.

        Merges with any existing .mcp.json so user's other MCP servers
        are preserved.  Uses ${TORQUE_CELL_ID} and ${TORQUE_PORT:-18932}
        env-var syntax which Claude Code interpolates natively.
        Returns True if config was installed successfully.
        """
        mcp_file = Path(working_dir) / ".mcp.json"

        torque_entry = build_stdio_launch_spec(mcp_entrypoint)
        if torque_entry:
            torque_entry["type"] = "stdio"
        else:
            torque_entry = {
                "type": "http",
                "url": "http://127.0.0.1:${TORQUE_PORT:-18932}/mcp",
                "headers": {"X-Torque-Cell-Id": "${TORQUE_CELL_ID}"},
            }

        try:
            with _locked_mcp_json(mcp_file):
                existing = {}
                if mcp_file.exists():
                    text = mcp_file.read_text().strip()
                    if text:
                        existing = json.loads(text)

                servers = existing.setdefault("mcpServers", {})
                servers["torque"] = torque_entry
                _atomic_write_mcp_json(mcp_file, existing)
            return True
        except Exception:
            return False

    def uninstall_mcp_config(self, working_dir: str):
        """Remove Torque MCP server entry from .mcp.json.

        If the file only contained the Torque entry, deletes it entirely.
        """
        mcp_file = Path(working_dir) / ".mcp.json"

        try:
            with _locked_mcp_json(mcp_file):
                if not mcp_file.exists():
                    return

                text = mcp_file.read_text().strip()
                if not text:
                    mcp_file.unlink(missing_ok=True)
                    return

                existing = json.loads(text)
                servers = existing.get("mcpServers", {})
                servers.pop("torque", None)

                if not servers:
                    mcp_file.unlink(missing_ok=True)
                else:
                    existing["mcpServers"] = servers
                    _atomic_write_mcp_json(mcp_file, existing)
        except Exception:
            pass  # Best-effort cleanup

    def install_skills(self, working_dir: str) -> bool:
        """Write Torque slash command skills into .claude/skills/.

        Each skill is a directory with a SKILL.md file.  Overwrites
        existing Torque skills to keep them in sync with the daemon version.
        Returns True if at least one skill was installed successfully.
        """
        skills_dir = Path(working_dir) / ".claude" / "skills"
        installed = 0
        for name, skill in SKILLS.items():
            try:
                skill_dir = skills_dir / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(_render_skill_md(skill))
                installed += 1
            except Exception:
                pass  # Best-effort per skill
        return installed > 0

    def uninstall_skills(self, working_dir: str):
        """Remove Torque-managed skills from .claude/skills/.

        Only removes directories matching the TORQUE_SKILL_PREFIX.
        """
        skills_dir = Path(working_dir) / ".claude" / "skills"
        if not skills_dir.exists():
            return
        try:
            for child in skills_dir.iterdir():
                if child.is_dir() and child.name.startswith(TORQUE_SKILL_PREFIX):
                    skill_md = child / "SKILL.md"
                    if skill_md.exists():
                        skill_md.unlink()
                    # Remove dir if empty
                    try:
                        child.rmdir()
                    except OSError:
                        pass  # Not empty — user added files
        except Exception:
            pass  # Best-effort cleanup

    def parse_event(self, raw: dict, cell) -> AgentEvent | None:
        """Parse a Claude Code hook HTTP payload into a normalized AgentEvent."""
        hook_event = raw.get("hook_event_name", "")
        now = time.time()

        if hook_event == "StatusLine":
            context_window = _claude_statusline_context_window(raw, now)
            if not context_window:
                return None
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="context_update",
                data={"context_window": context_window},
            )

        if hook_event == "SessionStart":
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="session_start",
                data={
                    "model": raw.get("model", ""),
                    "session_id": raw.get("session_id", ""),
                },
            )

        if hook_event in ("Stop", "SessionEnd"):
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="session_end",
                data={
                    "reason": raw.get("reason", "")
                    or ("completed" if hook_event == "Stop"
                        else "session_ended"),
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
