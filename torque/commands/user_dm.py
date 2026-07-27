"""Authoritative catalog and parser for operator user→agent DM commands.

Only the command endpoint calls this parser.  It intentionally recognizes a
small, closed grammar: surrounding whitespace is ignored, command spelling is
case-sensitive, and arguments are accepted only by ``/loop``'s established
grammar.  Everything else remains an ordinary direct message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import time


def _user_dm_usage_number(value, *, positive: bool = False) -> int | None:
    """Return a finite whole telemetry count without coercing malformed input."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        return None
    result = int(numeric)
    # A context window is necessarily bounded; this prevents hostile snapshots
    # from creating an unbounded decimal rendering work item.
    if result > 10 ** 12:
        return None
    return result if not positive or result > 0 else None


def _user_dm_usage_label(value, *, source: bool = False) -> str:
    """Keep optional snapshot labels bounded and free of markup/secrets."""
    text = str(value or "").strip()
    if not text or len(text) > 96:
        return ""
    if source:
        # These are the only current operator-visible context sources.
        return text if text in {"codex_transcript", "claude_statusline"} else ""
    return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", text) else ""


def _user_dm_usage_reset_timestamp(value, *, now: float) -> float | None:
    """Parse one bounded future reset time; stale/malformed values are absent."""
    if isinstance(value, bool):
        return None
    timestamp = None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric) and numeric > 0:
            timestamp = numeric / 1000 if numeric > 100_000_000_000 else numeric
    elif isinstance(value, str):
        text = value.strip()
        if not text or len(text) > 80:
            return None
        try:
            numeric = float(text)
        except (TypeError, ValueError, OverflowError):
            numeric = None
        if numeric is not None:
            if math.isfinite(numeric) and numeric > 0:
                timestamp = numeric / 1000 if numeric > 100_000_000_000 else numeric
        else:
            try:
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                timestamp = datetime.fromisoformat(text).timestamp()
            except (TypeError, ValueError, OverflowError):
                return None
    if timestamp is None or not math.isfinite(timestamp) or timestamp <= now:
        return None
    try:
        datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return timestamp


def _user_dm_usage_reset_display(timestamp: float, *, now: float) -> str:
    """Use a fixed UTC absolute time plus a compact future-relative label."""
    seconds = max(0, int(math.ceil(timestamp - now)))
    if seconds < 60:
        relative = "in <1m"
    elif seconds < 3600:
        relative = f"in {int(math.ceil(seconds / 60))}m"
    elif seconds < 86400:
        relative = f"in {seconds // 3600}h" + (
            f" {int(math.ceil((seconds % 3600) / 60))}m"
            if seconds % 3600 else ""
        )
    else:
        relative = f"in {seconds // 86400}d" + (
            f" {seconds % 86400 // 3600}h"
            if seconds % 86400 >= 3600 else ""
        )
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    ) + f" ({relative})"


def _user_dm_usage_context_line(target) -> tuple[str, bool]:
    context = getattr(target, "context_window", None)
    if not isinstance(context, dict) or not context:
        return "- Context window: Not reported", False
    used = _user_dm_usage_number(context.get("used_tokens"))
    limit = _user_dm_usage_number(context.get("limit_tokens"), positive=True)
    if used is None or limit is None:
        return "- Context window: Unavailable", False
    percent = (used / limit) * 100
    if not math.isfinite(percent) or percent < 0:
        return "- Context window: Unavailable", False
    labels = [
        _user_dm_usage_label(context.get("source"), source=True),
        _user_dm_usage_label(context.get("model")),
    ]
    label = " · ".join(part for part in labels if part)
    prefix = f"- Context window ({label})" if label else "- Context window"
    return (
        f"{prefix}: `{used:,}` / `{limit:,}` tokens "
        f"({percent:.2f}% used)",
        True,
    )


def _user_dm_usage_provider_line(target, *, window_id: str, label: str,
                                 now: float) -> tuple[str, bool]:
    usage = getattr(target, "provider_usage", None)
    if not isinstance(usage, dict):
        return f"- Provider {label}: Not reported", False
    window = usage.get(window_id)
    if not isinstance(window, dict):
        return f"- Provider {label}: Not reported", False
    if window.get("available") is not True:
        return f"- Provider {label}: Unavailable", False
    try:
        percent = float(window.get("used_percentage"))
    except (TypeError, ValueError, OverflowError):
        percent = float("nan")
    if not math.isfinite(percent) or percent < 0:
        return f"- Provider {label}: Unavailable", False
    # The provider-usage contract is a 0–100 integer percentage.  Preserve
    # that contract for legacy/raw snapshots rather than exposing over-range
    # provider payloads as a fabricated quota value.
    percent = min(100.0, percent)
    reset = _user_dm_usage_reset_timestamp(window.get("resets_at"), now=now)
    if reset is None:
        return f"- Provider {label}: Unavailable", False
    return (
        f"- Provider {label}: `{int(math.floor(percent + 0.5))}%` used · "
        f"resets `{_user_dm_usage_reset_display(reset, now=now)}`",
        True,
    )


def _user_dm_usage_message(target, *, now: float | None = None) -> str:
    """Format only the exact target's current bounded usage snapshot."""
    ts = float(now if now is not None else time.time())
    lines = ["**Agent usage**"]
    context_line, has_context = _user_dm_usage_context_line(target)
    lines.append(context_line)
    five_hour_line, has_five_hour = _user_dm_usage_provider_line(
        target, window_id="five_hour", label="5-hour", now=ts,
    )
    lines.append(five_hour_line)
    seven_day_line, has_seven_day = _user_dm_usage_provider_line(
        target, window_id="seven_day", label="7-day", now=ts,
    )
    lines.append(seven_day_line)
    if not (has_context or has_five_hour or has_seven_day):
        lines.append("- No current usage is reported for this agent.")
    return "\n".join(lines)




@dataclass(frozen=True, slots=True)
class UserDMCommand:
    """One supported operator convenience in the user→agent DM lane."""

    id: str
    label: str
    usage: str
    insert: str
    help: str
    aliases: tuple[str, ...]
    grammar: str
    execution_mode: str
    safety: str
    search: str
    providers: tuple[str, ...] = ()


# This is deliberately data rather than frontend metadata.  The server uses
# it for recognition and exposes its safe projection to the terminal composer.
USER_DM_COMMANDS: tuple[UserDMCommand, ...] = (
    UserDMCommand(
        id="compact",
        label="/compact",
        usage="/compact",
        insert="/compact",
        help="Ask the agent to compact its context before continuing.",
        aliases=("/compact",),
        grammar="exact",
        execution_mode="provider_passthrough",
        safety="narrow_provider_session_passthrough",
        search="compact /compact context summary",
    ),
    UserDMCommand(
        id="fast",
        label="/fast",
        usage="/fast",
        insert="/fast",
        help="Toggle Codex Fast mode for this agent.",
        aliases=("/fast",),
        grammar="exact",
        execution_mode="provider_passthrough",
        safety="codex_provider_exact_only",
        search="fast /fast codex fast mode",
        providers=("codex",),
    ),
    UserDMCommand(
        id="restart",
        label="/restart",
        usage="/restart",
        insert="/restart",
        help="Restart only this DM agent with a fresh session.",
        aliases=("/restart",),
        grammar="exact",
        execution_mode="lifecycle",
        safety="operator_lifecycle_exact_only",
        search="restart /restart relaunch fresh session current dm agent",
    ),
    UserDMCommand(
        id="loop",
        label="/loop every <interval> <message>",
        usage="/loop every 10m check status",
        insert="/loop every 10m ",
        help=(
            "Start a recurring user message. Use 1m–24h with s/m/h units, "
            "then add the message."
        ),
        aliases=("/loop every", "/loop cancel"),
        grammar="/loop or /loop <arguments>; established loop parser validates arguments",
        execution_mode="scheduler",
        safety="bounded_user_schedule",
        search="loop every interval message recurring schedule /loop every",
    ),
    UserDMCommand(
        id="loop-cancel",
        label="/loop cancel",
        usage="/loop cancel",
        insert="/loop cancel",
        help="Cancel the active user-message loop for this agent.",
        aliases=("/loop cancel",),
        grammar="exact",
        execution_mode="scheduler",
        safety="bounded_user_schedule",
        search="loop cancel stop recurring schedule /loop cancel",
    ),
    UserDMCommand(
        id="remind",
        label="/remind in <delay> <message>",
        usage="/remind in 10m check the deploy",
        insert="/remind in 10m ",
        help="Create a one-shot reminder. Use 1m–30d with m/h/d units.",
        aliases=("/remind",),
        grammar="/remind in <integer m/h/d delay> <message>, or /remind cancel <id|all>",
        execution_mode="local_reminder",
        safety="requester_scoped_no_prompt",
        search="remind in delay message one shot reminder /remind",
    ),
    UserDMCommand(
        id="reminders",
        label="/reminders",
        usage="/reminders",
        insert="/reminders",
        help="List your active one-shot reminders for this direct-message thread.",
        aliases=("/reminders",),
        grammar="exact",
        execution_mode="local_reminder",
        safety="requester_scoped_no_prompt",
        search="reminders active list one shot /reminders",
    ),
    UserDMCommand(
        id="remind-cancel",
        label="/remind cancel <reminder-id|all>",
        usage="/remind cancel rem-abc123 or /remind cancel all",
        insert="/remind cancel ",
        help="Cancel one active one-shot reminder or all of your active reminders.",
        aliases=("/remind cancel",),
        grammar="/remind cancel <reminder-id|all>",
        execution_mode="local_reminder",
        safety="requester_scoped_no_prompt",
        search="remind cancel reminder id all stop one shot /remind cancel",
    ),
    UserDMCommand(
        id="watch",
        label="/watch <task-id> [<task-id> ...]",
        usage="/watch TORQUE:123 TORQUE:124",
        insert="/watch ",
        help="Notify once when all named tasks are Done.",
        aliases=("/watch",),
        grammar="/watch followed by 1–20 unique task IDs",
        execution_mode="local_task_watch",
        safety="requester_scoped_no_prompt",
        search="watch task completion notify done /watch",
    ),
    UserDMCommand(
        id="watches",
        label="/watches",
        usage="/watches",
        insert="/watches",
        help="List this agent's active task-completion watches.",
        aliases=("/watches",),
        grammar="exact",
        execution_mode="local_task_watch",
        safety="requester_scoped_no_prompt",
        search="watches active task completion notify /watches",
    ),
    UserDMCommand(
        id="unwatch",
        label="/unwatch <watch-id|all>",
        usage="/unwatch watch-abc123 or /unwatch all",
        insert="/unwatch ",
        help="Cancel one active task-completion watch or all of this agent's watches.",
        aliases=("/unwatch",),
        grammar="/unwatch followed by a watch id or all",
        execution_mode="local_task_watch",
        safety="requester_scoped_no_prompt",
        search="unwatch cancel task completion notify /unwatch",
    ),
    UserDMCommand(
        id="usage",
        label="/usage",
        usage="/usage",
        insert="/usage",
        help="Show this agent's current context and provider quota without prompting it.",
        aliases=("/usage",),
        grammar="exact",
        execution_mode="read_only_response",
        safety="read_only_target_snapshot_no_prompt",
        search="usage /usage context window provider quota limits reset",
    ),
    UserDMCommand(
        id="status",
        label="/status",
        usage="/status",
        insert="/status",
        help="Show Torque's current operator-visible status without prompting the agent.",
        aliases=("/status",),
        grammar="exact",
        execution_mode="read_only_response",
        safety="read_only_no_prompt",
        search="status /status activity task attention loop worktree",
    ),
    UserDMCommand(
        id="commands",
        label="/commands",
        usage="/commands",
        insert="/commands",
        help="List supported direct-message slash commands.",
        aliases=("/commands",),
        grammar="exact",
        execution_mode="read_only_response",
        safety="read_only_no_prompt",
        search="commands /commands help slash command catalog",
    ),
)

_BY_ID = {command.id: command for command in USER_DM_COMMANDS}


def user_dm_command_supports_provider(
        command: UserDMCommand, provider: str) -> bool:
    """Return whether a catalog command is safe for an effective provider."""
    normalized = str(provider or "").strip().lower()
    return not command.providers or normalized in command.providers


def user_dm_command_catalog(*, provider: str | None = None) -> list[dict]:
    """Return the safe snapshot/API projection consumed by the composer.

    A provider-scoped caller receives only commands safely supported by that
    target.  The unscoped snapshot retains provider metadata so the composer
    can make the same target-specific choice without inventing a second menu.
    """
    return [
        {
            "id": command.id,
            "label": command.label,
            "usage": command.usage,
            "insert": command.insert,
            "help": command.help,
            "aliases": list(command.aliases),
            "grammar": command.grammar,
            "execution_mode": command.execution_mode,
            "safety": command.safety,
            "search": command.search,
            "providers": list(command.providers),
        }
        for command in USER_DM_COMMANDS
        if provider is None or user_dm_command_supports_provider(command, provider)
    ]


def user_dm_command_by_id(command_id: str) -> UserDMCommand | None:
    return _BY_ID.get(str(command_id or "").strip())


def parse_user_dm_command(message: str) -> UserDMCommand | None:
    """Recognize only documented command text, otherwise return ``None``.

    Leading and trailing whitespace is tolerated.  Case is intentionally not
    normalized, matching the pre-registry exact command behavior.  ``/loop``
    keeps its historical space-only prefix grammar so the existing loop parser
    remains the sole authority for interval and message validation.
    """
    text = str(message or "").strip()
    for command in USER_DM_COMMANDS:
        if command.id == "loop":
            continue
        if text in command.aliases:
            return command
    if text == "/loop" or text.startswith("/loop "):
        return _BY_ID["loop"]
    # Watch verbs intentionally claim their own malformed/case-variant forms
    # so those never become provider prompts.  Unrelated slash prose such as
    # /watchdog remains an ordinary direct message.
    for verb in ("watch", "watches", "unwatch", "remind", "reminders"):
        lower = text.lower()
        token = "/" + verb
        if lower == token or (lower.startswith(token) and len(lower) > len(token) and lower[len(token)].isspace()):
            return _BY_ID[verb]
    return None
