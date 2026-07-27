"""Authoritative catalog and parser for operator user→agent DM commands.

Only the command endpoint calls this parser.  It intentionally recognizes a
small, closed grammar: surrounding whitespace is ignored, command spelling is
case-sensitive, and arguments are accepted only by ``/loop``'s established
grammar.  Everything else remains an ordinary direct message.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    for verb in ("watch", "watches", "unwatch"):
        lower = text.lower()
        token = "/" + verb
        if lower == token or (lower.startswith(token) and len(lower) > len(token) and lower[len(token)].isspace()):
            return _BY_ID[verb]
    return None
