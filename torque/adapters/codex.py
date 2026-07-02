"""Codex CLI adapter — full integration via command hooks and MCP."""

import fcntl
import hashlib
import json
import os
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import AgentAdapter, AgentEvent, InputReadyPolicy
from ..context_window import normalize_context_window_usage
from ..provider_usage import normalize_provider_usage_rate_limits
from .. import config as torque_config

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
    r"(?:(?!\n\[)[^\n]*\n)*"
    r"(?:\[mcp_servers\.torque\.env\]\n(?:(?!\n\[)[^\n]*\n)*)?",
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
    "PermissionRequest": "permission_request",
    "Stop": "stop",
}

_CODEX_TORQUE_CONFIG_ROOT = "codex/agents"
_CODEX_APPROVAL_SANDBOX_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
# Codex represents configuration supplied by repeated `--config key=value`
# session flags as an unmanaged synthetic config layer. Hook trust keys for
# hooks supplied through those same flags must therefore use this synthetic
# source path, not the Torque-owned file where we persist an audit copy.
_CODEX_SESSION_FLAGS_HOOK_SOURCE_PATH = "/config.toml"



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



def _torque_event_curl_command(
    url: str,
    *,
    discard_response_stdout: bool = False,
) -> str:
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
    if discard_response_stdout:
        # PermissionRequest hooks can make allow/deny decisions via stdout.
        # Torque is a passive observer, so that hook must emit no response body.
        curl += " --output /dev/null"
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


def _codex_opts_with_approval_sandbox_bypass(opts: list[str]) -> list[str]:
    """Return Codex CLI opts with Torque's required non-interactive bypass.

    Current Codex builds use ``--dangerously-bypass-approvals-and-sandbox`` to
    run autonomously in an externally-sandboxed runtime.  When Torque adds that
    flag, drop explicit approval/sandbox policy options from the managed launch
    command so the generated fresh/resume invocations do not carry conflicting
    policy knobs.  Other user/model/config flags are preserved.
    """
    filtered: list[str] = []
    has_bypass = False
    i = 0
    while i < len(opts):
        part = opts[i]
        if part == _CODEX_APPROVAL_SANDBOX_BYPASS_FLAG:
            has_bypass = True
            filtered.append(part)
            i += 1
            continue
        if part in ("-s", "--sandbox", "-a", "--ask-for-approval"):
            i += 2
            continue
        if (
            part.startswith("-s=")
            or part.startswith("-a=")
            or part.startswith("--sandbox=")
            or part.startswith("--ask-for-approval=")
        ):
            i += 1
            continue
        filtered.append(part)
        i += 1
    if not has_bypass:
        filtered.append(_CODEX_APPROVAL_SANDBOX_BYPASS_FLAG)
    return filtered


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


def _torque_data_dir() -> Path:
    explicit = (os.environ.get("TORQUE_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(torque_config.DATA_DIR).expanduser()


def _codex_agent_config_dir(cell_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(cell_id or "agent")).strip(".-")
    return _torque_data_dir() / _CODEX_TORQUE_CONFIG_ROOT / (safe_id or "agent")


def _codex_agent_config_file(cell_id: str) -> Path:
    return _codex_agent_config_dir(cell_id) / "config.toml"


def _codex_agent_launch_script_file(cell_id: str) -> Path:
    return _codex_agent_config_dir(cell_id) / "launch.sh"


def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""))


def _toml_inline_string_map(values: dict[str, str]) -> str:
    return "{ " + ", ".join(
        f"{_toml_string(k)} = {_toml_string(v)}"
        for k, v in values.items()
    ) + " }"


def _toml_inline_hook(hook: dict) -> str:
    parts = []
    for key in ("type", "command", "command_windows", "statusMessage"):
        if key in hook and hook.get(key) is not None:
            parts.append(f"{key} = {_toml_string(str(hook.get(key) or ''))}")
    if "timeout" in hook:
        parts.append(f"timeout = {int(hook.get('timeout') or 600)}")
    if "async" in hook:
        parts.append(f"async = {str(bool(hook.get('async'))).lower()}")
    return "{ " + ", ".join(parts) + " }"


def _toml_inline_hook_group(group: dict) -> str:
    parts = []
    if group.get("matcher") is not None:
        parts.append(f"matcher = {_toml_string(str(group.get('matcher') or ''))}")
    hooks = group.get("hooks", []) or []
    parts.append("hooks = [" + ", ".join(_toml_inline_hook(h) for h in hooks) + "]")
    return "{ " + ", ".join(parts) + " }"


def _toml_inline_hook_state(entries: list[tuple[str, str]]) -> str:
    return "{ " + ", ".join(
        (
            f"{_toml_string(key)} = "
            f"{{ trusted_hash = {_toml_string(trusted_hash)} }}"
        )
        for key, trusted_hash in entries
    ) + " }"


def _codex_config_cli_flags(config: dict, *, config_path: Path) -> list[str]:
    flags = [
        f"features.hooks=true",
        (
            "mcp_servers.torque.url="
            + _toml_string(config["mcp_servers"]["torque"]["url"])
        ),
        (
            "mcp_servers.torque.env_http_headers="
            + _toml_inline_string_map(
                config["mcp_servers"]["torque"]["env_http_headers"]
            )
        ),
    ]
    instructions_file = str(config.get("model_instructions_file") or "")
    if instructions_file:
        flags.append("model_instructions_file=" + _toml_string(instructions_file))
    for event_name, groups in (config.get("hooks") or {}).items():
        flags.append(
            f"hooks.{event_name}=["
            + ", ".join(_toml_inline_hook_group(group) for group in groups or [])
            + "]"
        )
    _source, trust_entries = _codex_hook_trust_entries_for_source(
        _CODEX_SESSION_FLAGS_HOOK_SOURCE_PATH,
        config.get("hooks") or {},
    )
    if trust_entries:
        # Codex's CLI override parser splits override paths on '.' literally,
        # so keys containing `/config.toml` cannot be expressed as
        # `hooks.state.<key>.trusted_hash=...`. Override the whole state table
        # instead; the inline TOML value preserves the synthetic source key.
        flags.append("hooks.state=" + _toml_inline_hook_state(trust_entries))
    result: list[str] = []
    for flag in flags:
        result.extend(["--config", flag])
    return result


def _append_codex_config_cli_flags(command: str, config: dict, config_path: Path) -> str:
    parts = shlex.split(command) if command else ["codex"]
    if not parts:
        parts = ["codex"]
    flags = _codex_config_cli_flags(config, config_path=config_path)
    opts, prompt = _split_boot_args(command or parts[0])
    opts = _codex_opts_with_approval_sandbox_bypass(opts)
    assembled = [parts[0], *opts, *flags]
    if prompt:
        assembled.append(prompt)
    return " ".join(shlex.quote(p) for p in assembled)


def _codex_resume_config_cli_command(command: str, config: dict, config_path: Path) -> str:
    parts = shlex.split(command) if command else ["codex"]
    if not parts:
        parts = ["codex"]
    flags = _codex_config_cli_flags(config, config_path=config_path)
    opts, prompt = _split_boot_args(command or parts[0])
    opts = _codex_opts_with_approval_sandbox_bypass(opts)
    assembled = [parts[0], "resume", *opts, *flags]
    rendered = " ".join(shlex.quote(p) for p in assembled)
    rendered += ' "$@"'
    if prompt:
        rendered += " " + shlex.quote(prompt)
    return rendered


def _render_codex_agent_config(config: dict, *, source_path: str = "") -> str:
    lines = [
        "# Torque-owned Codex agent config (generated; do not edit)",
    ]
    instructions_file = str(config.get("model_instructions_file") or "")
    if instructions_file:
        lines.append(f"model_instructions_file = {_toml_string(instructions_file)}")
        lines.append("")
    lines.extend([
        "[features]",
        "hooks = true",
        "",
        "[mcp_servers.torque]",
        f"url = {_toml_string(config['mcp_servers']['torque']['url'])}",
        (
            "env_http_headers = "
            + _toml_inline_string_map(
                config["mcp_servers"]["torque"]["env_http_headers"]
            )
        ),
        "",
    ])
    hooks = config.get("hooks") or {}
    if hooks:
        lines.append("[hooks]")
        lines.append("")
    for event_name, groups in hooks.items():
        for group in groups or []:
            lines.append(f"[[hooks.{event_name}]]")
            if group.get("matcher") is not None:
                lines.append(f"matcher = {_toml_string(str(group.get('matcher') or ''))}")
            for hook in group.get("hooks", []) or []:
                lines.append(f"[[hooks.{event_name}.hooks]]")
                for key in ("type", "command", "command_windows", "statusMessage"):
                    if key in hook and hook.get(key) is not None:
                        lines.append(f"{key} = {_toml_string(str(hook.get(key) or ''))}")
                if "timeout" in hook:
                    lines.append(f"timeout = {int(hook.get('timeout') or 600)}")
                if "async" in hook:
                    lines.append(f"async = {str(bool(hook.get('async'))).lower()}")
            lines.append("")
    if source_path:
        _source, trust_entries = _codex_hook_trust_entries_for_source(
            source_path, hooks
        )
        for key, trusted_hash in trust_entries:
            lines.append(f"[hooks.state.{_toml_string(key)}]")
            lines.append(f"trusted_hash = {_toml_string(trusted_hash)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _codex_absolute_path(path: Path) -> str:
    # Match Codex's AbsolutePathBuf hook source key for project config:
    # expand ~ and remove dot components without resolving symlinked prefixes.
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def _resolved_codex_absolute_path(path: Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _codex_source_paths(path: Path) -> list[str]:
    paths = [_codex_absolute_path(path)]
    resolved_path = _resolved_codex_absolute_path(path)
    if resolved_path not in paths:
        paths.append(resolved_path)
    return paths


def _codex_hook_source_paths(working_dir: str) -> list[str]:
    hooks_path = Path(working_dir) / ".codex" / "hooks.json"
    return _codex_source_paths(hooks_path)


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
    """Return Codex HookHandler current_hash for a command hook.

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


def _codex_hook_trust_entries_for_source(
    source_path: str, hooks: dict
) -> tuple[str, list[tuple[str, str]]]:
    source_paths = _codex_source_paths(Path(source_path))
    source_path = source_paths[0]
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
                trusted_hash = _codex_command_hook_hash(event_label, matcher, hook)
                for path in source_paths:
                    key = f"{path}:{event_label}:{group_index}:{handler_index}"
                    entries.append((key, trusted_hash))
    return source_path, entries


def _codex_hook_trust_entries(working_dir: str, hooks: dict) -> tuple[str, list[tuple[str, str]]]:
    source_paths = _codex_hook_source_paths(working_dir)
    return _codex_hook_trust_entries_for_source(source_paths[0], hooks)


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
        for path in _codex_hook_source_paths(working_dir):
            content = _remove_hook_trust_block(content, path)
        content = content.strip()
        return f"{content}\n\n{block}" if content else block

    return _update_codex_user_config(_update)


def _uninstall_codex_hook_trust(working_dir: str) -> bool:
    def _update(content: str) -> str:
        for path in _codex_hook_source_paths(working_dir):
            content = _remove_hook_trust_block(content, path)
        return content

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


def _dict_value(source: dict, *keys: str):
    if not isinstance(source, dict):
        return None
    for key in keys:
        if key in source:
            return source.get(key)
    return None


def _codex_tool_input_payload(raw: dict):
    if not isinstance(raw, dict):
        return {}
    if "tool_input" in raw:
        value = raw.get("tool_input")
    else:
        value = raw.get("input", {})
    return {} if value is None else value


def _codex_tool_activity_detail(tool: str, tool_input) -> str:
    """Return a human-facing detail for Codex PreToolUse-style payloads."""
    tool = str(tool or "").strip()
    inp = tool_input if isinstance(tool_input, dict) else {}
    normalized = tool.lower().replace("-", "_")

    if normalized in ("bash", "shell", "command"):
        cmd = str(inp.get("command", "") or inp.get("cmd", "") or "").strip()
        return f"Running: {_truncate(cmd, 40)}" if cmd else f"Using {tool}"

    if normalized in ("apply_patch", "applypatch", "patch"):
        return "Applying patch"

    if tool.startswith("mcp__"):
        return tool

    # Codex MCP payloads may arrive either as a fully-qualified mcp__ tool name
    # or as a generic MCP tool plus server/tool fields.  Preserve the
    # fully-qualified shape when possible so the existing card/UI humanizers can
    # count and label the call without exposing raw ids to operators.
    if normalized in ("mcp", "mcp_tool", "mcp_tool_call"):
        server = str(
            _dict_value(inp, "server", "server_name", "mcp_server") or ""
        ).strip()
        mcp_tool = str(
            _dict_value(inp, "tool_name", "name", "tool") or ""
        ).strip()
        if mcp_tool.startswith("mcp__"):
            return mcp_tool
        if server and mcp_tool:
            return f"mcp__{server}__{mcp_tool}"
        return tool or "Using MCP"

    return f"Using {tool}" if tool else "Working"


def _read_text_tail(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    start = max(0, size - max(1, int(max_bytes or 1)))
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    if start > 0:
        _prefix, sep, remainder = data.partition(b"\n")
        data = remainder if sep else b""
    return data.decode("utf-8", errors="replace")


def _codex_context_window_from_token_count(
    info: dict,
    *,
    model: str = "",
    session_id: str = "",
    timestamp: float | None = None,
) -> dict:
    if not isinstance(info, dict):
        return {}
    last = _dict_value(info, "last_token_usage", "last") or {}
    total = _dict_value(info, "total_token_usage", "total") or {}
    limit_tokens = _dict_value(
        info, "model_context_window", "modelContextWindow"
    )
    return normalize_context_window_usage(
        {
            "source": "codex_transcript",
            "model": model,
            "session_id": session_id,
            "used_tokens": _dict_value(last, "total_tokens", "totalTokens"),
            "limit_tokens": limit_tokens,
            "input_tokens": _dict_value(last, "input_tokens", "inputTokens"),
            "output_tokens": _dict_value(last, "output_tokens", "outputTokens"),
            "cached_input_tokens": _dict_value(
                last, "cached_input_tokens", "cachedInputTokens"
            ),
            "reasoning_output_tokens": _dict_value(
                last, "reasoning_output_tokens", "reasoningOutputTokens"
            ),
            "session_total_tokens": _dict_value(
                total, "total_tokens", "totalTokens"
            ),
        },
        now=timestamp,
    )


def _codex_reset_epoch_to_iso(value) -> str | None:
    try:
        timestamp = float(value)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _codex_window_minutes(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _codex_provider_usage_from_rate_limits(rate_limits) -> dict | None:
    if not isinstance(rate_limits, dict):
        return None

    assembled: dict[str, dict] = {}
    fallback_windows = {
        "primary": "five_hour",
        "secondary": "seven_day",
    }
    for source_name, fallback_window in fallback_windows.items():
        raw_window = rate_limits.get(source_name)
        if not isinstance(raw_window, dict):
            continue
        window_minutes = _codex_window_minutes(raw_window.get("window_minutes"))
        if window_minutes == 300:
            canonical = "five_hour"
        elif window_minutes == 10080:
            canonical = "seven_day"
        else:
            canonical = fallback_window
        assembled[canonical] = {
            "used_percentage": raw_window.get("used_percent"),
            "resets_at": _codex_reset_epoch_to_iso(raw_window.get("resets_at")),
        }

    return normalize_provider_usage_rate_limits(assembled)


def _latest_codex_context_and_provider_usage_from_transcript(
    transcript_path: str,
    *,
    model: str = "",
    session_id: str = "",
    timestamp: float | None = None,
    max_bytes: int = 1_000_000,
) -> tuple[dict, dict | None]:
    """Extract latest Codex token_count usage from a bounded JSONL tail."""
    if not transcript_path:
        return ({}, None)
    try:
        path = Path(str(transcript_path)).expanduser()
        if not path.is_file():
            return ({}, None)
        text = _read_text_tail(path, max_bytes)
    except Exception:
        return ({}, None)

    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "token_count":
            continue
        context_window = _codex_context_window_from_token_count(
            payload.get("info") or {},
            model=model,
            session_id=session_id,
            timestamp=timestamp,
        )
        provider_usage = _codex_provider_usage_from_rate_limits(
            payload.get("rate_limits")
        )
        if context_window or provider_usage is not None:
            return (context_window, provider_usage)
    return ({}, None)


def _latest_codex_context_window_from_transcript(
    transcript_path: str,
    *,
    model: str = "",
    session_id: str = "",
    timestamp: float | None = None,
    max_bytes: int = 1_000_000,
) -> dict:
    context_window, _provider_usage = (
        _latest_codex_context_and_provider_usage_from_transcript(
            transcript_path,
            model=model,
            session_id=session_id,
            timestamp=timestamp,
            max_bytes=max_bytes,
        )
    )
    return context_window


def _codex_context_window_from_raw(raw: dict, timestamp: float) -> dict:
    context_window, _provider_usage = _codex_context_and_provider_usage_from_raw(
        raw, timestamp
    )
    return context_window


def _codex_context_and_provider_usage_from_raw(
    raw: dict, timestamp: float
) -> tuple[dict, dict | None]:
    if not isinstance(raw, dict):
        return ({}, None)
    transcript_path = raw.get("transcript_path") or raw.get("transcriptPath") or ""
    model = str(raw.get("model", "") or "")
    session_id = str(raw.get("session_id", "") or raw.get("sessionId", "") or "")
    return _latest_codex_context_and_provider_usage_from_transcript(
        transcript_path,
        model=model,
        session_id=session_id,
        timestamp=timestamp,
    )


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
        """Persist Torque instructions for the per-agent Codex config layer."""
        if not text or not working_dir or not filename:
            return ""
        try:
            torque_dir = Path(working_dir) / ".torque"
            torque_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = torque_dir / filename
            prompt_path.write_text(text.rstrip() + "\n")
        except Exception:
            return ""
        return ""

    def uninstall_persistent_prompt(self, working_dir: str,
                                    filename: str = "") -> None:
        """Remove only Torque-owned prompt files; leave project .codex alone."""
        try:
            torque_dir = Path(working_dir) / ".torque"
            if filename:
                (torque_dir / filename).unlink(missing_ok=True)
            else:
                for f in torque_dir.glob("torque-system-prompt-*.md"):
                    f.unlink(missing_ok=True)
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
        if _matches_codex_token(parts[0]):
            # Inject the bypass only for raw codex commands. When boot_cmd is
            # the Torque launch shim, its resume line already carries the
            # bypass and Codex rejects the flag appearing twice.
            opts = _codex_opts_with_approval_sandbox_bypass(opts)
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

        def _cmd_hook(matcher=None, *, discard_response_stdout=False):
            h = {"type": "command", "timeout": timeout,
                 "command": (
                     _torque_event_curl_command(
                         _torque_hook_url(),
                         discard_response_stdout=discard_response_stdout,
                     )
                 )}
            entry = {"hooks": [h]}
            if matcher:
                entry["matcher"] = matcher
            return [entry]

        return {
            "hooks": {
                "SessionStart": _cmd_hook(),
                "PreToolUse": _cmd_hook(".*"),
                # PermissionRequest can be policy-bearing in Codex.  Torque's
                # hook is deliberately passive: it observes the prompt and
                # suppresses the curl response body so stdout never emits an
                # allow/deny decision.
                "PermissionRequest": _cmd_hook(
                    discard_response_stdout=True,
                ),
                # PostToolUse omitted — Codex command hooks print a noisy
                # "Running PostToolUse hook" message for every tool call.
                # The tool_end state reset is cosmetic and happens naturally
                # when the next PreToolUse or Stop event arrives.
                "Stop": _cmd_hook(),
            }
        }

    def install_hooks(self, working_dir: str) -> bool:
        """Codex hooks are generated into Torque-owned per-agent config."""
        del working_dir
        return True

    def uninstall_hooks(self, working_dir: str):
        """Leave user/project .codex hook files untouched."""
        del working_dir

    def install_mcp_config(self, working_dir: str, *,
                           mcp_entrypoint: str = "",
                           mcp_env: dict[str, str] | None = None) -> bool:
        """Codex MCP config is generated into Torque-owned per-agent config."""
        del working_dir, mcp_entrypoint, mcp_env
        return True

    def uninstall_mcp_config(self, working_dir: str):
        """Leave user/project .codex config files untouched."""
        del working_dir

    def _agent_config_payload(self, cell, working_dir: str) -> tuple[Path, dict]:
        config_file = _codex_agent_config_file(getattr(cell, "id", ""))
        prompt_file = (
            Path(working_dir) / ".torque" / f"torque-system-prompt-{getattr(cell, 'id', '')}.md"
        )
        payload = {
            "mcp_servers": {
                "torque": {
                    "url": _torque_mcp_url(),
                    "env_http_headers": {
                        "X-Torque-Cell-Id": "TORQUE_CELL_ID",
                    },
                },
            },
            "hooks": (self.get_hook_config(cell) or {}).get("hooks", {}),
        }
        if prompt_file.exists():
            payload["model_instructions_file"] = str(prompt_file)
        return config_file, payload

    def refresh_agent_config(self, cell, working_dir: str, *,
                             mcp_entrypoint: str = "",
                             mcp_env: dict[str, str] | None = None) -> bool:
        """Write Torque-owned per-agent Codex config under the Torque data dir."""
        del mcp_entrypoint, mcp_env
        try:
            config_file, payload = self._agent_config_payload(cell, working_dir)
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(
                _render_codex_agent_config(
                    payload,
                    source_path=str(config_file),
                )
            )
            return True
        except Exception:
            return False

    def prepare_launch_command(self, cell, working_dir: str, command: str, *,
                               mcp_entrypoint: str = "",
                               mcp_env: dict[str, str] | None = None) -> str:
        """Generate per-agent config and a short Torque-owned launch shim.

        The effective Codex invocation still uses explicit ``--config``
        overrides so the generated hooks/MCP/trust state are in Codex's active
        launch layer.  The full command is written into a per-agent script
        under ``TORQUE_DATA_DIR`` and the PTY only receives that short script
        path.  Sending the full hook config inline through a zsh PTY can exceed
        the terminal's canonical input-line limit, leaving only ``/bin/zsh``
        running with no Codex child.
        """
        del mcp_entrypoint, mcp_env
        try:
            config_file, payload = self._agent_config_payload(cell, working_dir)
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(
                _render_codex_agent_config(
                    payload,
                    source_path=str(config_file),
                )
            )
            full_command = _append_codex_config_cli_flags(
                command or "codex", payload, config_file)
            resume_command = _codex_resume_config_cli_command(
                command or "codex", payload, config_file)
            launch_script = _codex_agent_launch_script_file(getattr(cell, "id", ""))
            launch_script.write_text(
                "#!/bin/sh\n"
                "# Torque-owned Codex launch shim (generated; do not edit).\n"
                'if [ "${1:-}" = "resume" ]; then\n'
                "  shift\n"
                "  exec " + resume_command + "\n"
                "fi\n"
                "exec " + full_command + "\n"
            )
            launch_script.chmod(0o700)
            return shlex.quote(str(launch_script))
        except Exception:
            return command

    def cleanup_agent_config(self, cell, working_dir: str) -> None:
        """Remove only Torque-owned generated Codex config for this agent."""
        del working_dir
        try:
            config_dir = _codex_agent_config_dir(getattr(cell, "id", ""))
            for filename in ("config.toml", "launch.sh"):
                (config_dir / filename).unlink(missing_ok=True)
            try:
                config_dir.rmdir()
                config_dir.parent.rmdir()
            except OSError:
                pass
        except Exception:
            pass

    def parse_event(self, raw: dict, cell) -> AgentEvent | None:
        """Parse a Codex hook payload into a normalized AgentEvent.

        Codex hooks receive JSON on stdin and POST it to Torque via curl.
        The payload structure differs from Claude Code — field names
        are mapped defensively.
        """
        # Codex hooks include a "type" or "hook_event_name" field
        hook_event = raw.get("hook_event_name", "") or raw.get("type", "")
        now = time.time()
        context_window, provider_usage = _codex_context_and_provider_usage_from_raw(
            raw, now
        )

        def _attach_live_usage(data: dict) -> dict:
            session_id = raw.get("session_id", "") or raw.get("sessionId", "")
            if session_id:
                data["session_id"] = session_id
            if context_window:
                data["context_window"] = context_window
            if provider_usage is not None:
                data["provider_usage"] = provider_usage
            return data

        if hook_event == "SessionStart":
            data = {
                "model": raw.get("model", ""),
                "session_id": raw.get("session_id", ""),
            }
            _attach_live_usage(data)
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="session_start",
                data=data,
            )

        if hook_event == "Stop":
            data = {
                "reason": raw.get("stop_reason")
                or raw.get("stopReason", "completed"),
                "summary": raw.get("last_assistant_message") or "",
            }
            _attach_live_usage(data)
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="session_end",
                data=data,
            )

        if hook_event == "PreToolUse":
            tool = raw.get("tool_name", "") or raw.get("name", "")
            inp = _codex_tool_input_payload(raw)
            # Codex PreToolUse now covers shell, apply_patch, and MCP calls.
            # Keep details tool-aware instead of assuming a Bash-only payload.
            detail = _codex_tool_activity_detail(tool, inp)
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_start",
                data=_attach_live_usage({
                    "tool": tool,
                    "input": inp,
                    "detail": detail,
                }),
            )

        if hook_event == "PermissionRequest":
            tool = raw.get("tool_name", "") or raw.get("name", "")
            inp = _codex_tool_input_payload(raw)
            detail = _codex_tool_activity_detail(tool, inp)
            reason = "Waiting for approval"
            if detail and detail != "Working":
                reason += f": {detail}"
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="waiting",
                data=_attach_live_usage({
                    "tool": tool,
                    "input": inp,
                    "reason": reason,
                }),
            )

        if hook_event == "PostToolUse":
            tool = raw.get("tool_name", "") or raw.get("name", "")
            return AgentEvent(
                cell_id=cell.id, timestamp=now,
                event_type="tool_end",
                data={"tool": tool, "success": True},
            )

        return None
