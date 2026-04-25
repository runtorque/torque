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

from .base import AgentAdapter, AgentEvent, InputReadyPolicy
from .mcp_launch import build_stdio_launch_spec

_LOOM_EVENT_URL_RE = re.compile(r"http://(?:localhost|127\.0\.0\.1):\d+/events")

# Skill directory prefix used to identify Loom-managed skills during cleanup
LOOM_SKILL_PREFIX = "loom-"

# ---------------------------------------------------------------------------
# Slash command (skill) definitions — injected into .claude/skills/
# ---------------------------------------------------------------------------

SKILLS = {
    "loom-status": {
        "frontmatter": {
            "name": "loom-status",
            "description": (
                "Show current Loom agent context, linked task, "
                "and pipeline state"
            ),
            "allowed-tools": "mcp__loom__loom_context",
        },
        "body": dedent("""\
            Show the current Loom agent context using the loom_context MCP tool.

            Format the output clearly:
            - Agent name, group, and status
            - Current task title, lane, and action
            - Pipeline info (parent task, depth) if this is a derived task
            - Worktree branch and diff stats if in a worktree
        """),
    },
    "loom-board": {
        "frontmatter": {
            "name": "loom-board",
            "description": (
                "Show the Loom task board with all lanes and tasks"
            ),
            "allowed-tools": "Bash",
        },
        "body": dedent("""\
            Show the current Loom task board.

            ## Board state
            !`loom board list`

            Summarize the board state: how many tasks in each lane,
            any tasks that need attention (blocked/error labels),
            and what's currently in progress.
        """),
    },
    "loom-done": {
        "frontmatter": {
            "name": "loom-done",
            "description": "Mark the current Loom task as complete",
            "allowed-tools": "mcp__loom__loom_done",
            "argument-hint": "[completion message]",
        },
        "body": dedent("""\
            Mark the current task as complete using the loom_done MCP tool.

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


def _current_loom_port() -> str:
    port = (os.environ.get("LOOM_PORT", "18932") or "").strip()
    return port or "18932"



def _loom_event_curl_command(url: str) -> str:
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
        + ' -H "X-Loom-Cell-Id: $LOOM_CELL_ID"'
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

def _loom_hook_url() -> str:
    return f"http://localhost:{_current_loom_port()}/events"


def _is_loom_event_target(value: str) -> bool:
    return bool(_LOOM_EVENT_URL_RE.search(value or ""))


def _is_loom_hook_entry(entry: dict) -> bool:
    """Check if a hook entry was installed by Loom (by URL marker)."""
    for hook in entry.get("hooks", []):
        if _is_loom_event_target(hook.get("url", "")):
            return True
        # Command hooks that curl to Loom
        if (
            hook.get("type") == "command"
            and _is_loom_event_target(hook.get("command", ""))
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
        return {"LOOM_CELL_ID": cell.id}

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
            sp_dir = Path(working_dir) / ".loom"
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
            sp_dir = Path(working_dir) / ".loom"
            if filename:
                (sp_dir / filename).unlink(missing_ok=True)
            else:
                # Remove all Loom system prompt files
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
        """Wait for the SessionStart hook before releasing the first send."""
        return InputReadyPolicy(
            enabled=True,
            hook_event=True,
            timeout_seconds=30.0,
            poll_interval_seconds=0.5,
            stable_polls=2,
            post_ready_delay=0.5,
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

        Most hooks use type: "http" (POST directly to Loom). SessionStart
        requires type: "command" (Claude Code limitation — HTTP hooks are
        not supported for SessionStart/WorktreeCreate/WorktreeRemove).
        """
        url = _loom_hook_url()
        timeout = 3
        cmd_timeout = 8

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
            h = {"type": "command", "timeout": cmd_timeout,
                 "command": (
                     _loom_event_curl_command(url)
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
        """Write Loom hooks into .claude/settings.local.json.

        Merges with any existing settings — Loom hooks are identified by
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

    def install_mcp_config(self, working_dir: str, *,
                           mcp_entrypoint: str = "") -> bool:
        """Write Loom MCP server entry into .mcp.json.

        Merges with any existing .mcp.json so user's other MCP servers
        are preserved.  Uses ${LOOM_CELL_ID} and ${LOOM_PORT:-18932}
        env-var syntax which Claude Code interpolates natively.
        Returns True if config was installed successfully.
        """
        mcp_file = Path(working_dir) / ".mcp.json"

        loom_entry = build_stdio_launch_spec(mcp_entrypoint)
        if loom_entry:
            loom_entry["type"] = "stdio"
        else:
            loom_entry = {
                "type": "http",
                "url": "http://127.0.0.1:${LOOM_PORT:-18932}/mcp",
                "headers": {"X-Loom-Cell-Id": "${LOOM_CELL_ID}"},
            }

        try:
            with _locked_mcp_json(mcp_file):
                existing = {}
                if mcp_file.exists():
                    text = mcp_file.read_text().strip()
                    if text:
                        existing = json.loads(text)

                servers = existing.setdefault("mcpServers", {})
                servers["loom"] = loom_entry
                _atomic_write_mcp_json(mcp_file, existing)
            return True
        except Exception:
            return False

    def uninstall_mcp_config(self, working_dir: str):
        """Remove Loom MCP server entry from .mcp.json.

        If the file only contained the Loom entry, deletes it entirely.
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
                servers.pop("loom", None)

                if not servers:
                    mcp_file.unlink(missing_ok=True)
                else:
                    existing["mcpServers"] = servers
                    _atomic_write_mcp_json(mcp_file, existing)
        except Exception:
            pass  # Best-effort cleanup

    def install_skills(self, working_dir: str) -> bool:
        """Write Loom slash command skills into .claude/skills/.

        Each skill is a directory with a SKILL.md file.  Overwrites
        existing Loom skills to keep them in sync with the daemon version.
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
        """Remove Loom-managed skills from .claude/skills/.

        Only removes directories matching the LOOM_SKILL_PREFIX.
        """
        skills_dir = Path(working_dir) / ".claude" / "skills"
        if not skills_dir.exists():
            return
        try:
            for child in skills_dir.iterdir():
                if child.is_dir() and child.name.startswith(LOOM_SKILL_PREFIX):
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
