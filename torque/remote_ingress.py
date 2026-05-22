"""Narrow remote-ingress helpers for future enterprise channels.

The relay/connector should not be able to call arbitrary daemon commands.  This
module validates a small user→agent direct-message shape and delegates to the
same local handler used by the browser's ``user_agent_message`` command.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

_ALLOWED_USER_AGENT_MESSAGE_FIELDS = {
    "agent_id",
    "cell_id",
    "target_agent_id",
    "thread_id",
    "reply_to_id",
    "message",
    "text",
    "idempotency_key",
    "idempotencyKey",
    "client_message_id",
    "clientMessageId",
}
_AGENT_ID_FIELDS = ("agent_id", "cell_id", "target_agent_id")
_MESSAGE_FIELDS = ("message", "text")
_IDEMPOTENCY_FIELDS = (
    "idempotency_key",
    "idempotencyKey",
    "client_message_id",
    "clientMessageId",
)


class RemoteIngressError(ValueError):
    """Raised when a remote command fails the allowlist boundary."""


def _string_field(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RemoteIngressError(f"remote user_agent_message.{key} must be a string")
    return value.strip() if key != "message" and key != "text" else value


def _first_non_empty_string(data: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _string_field(data, key)
        if value.strip():
            return value
    return ""


def normalize_remote_user_agent_message(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a sanitized local ``user_agent_message`` command.

    Only direct-message fields are accepted; notably, callers cannot pass
    ``cmd`` or any other daemon command surface through this helper.
    """

    if not isinstance(data, Mapping):
        raise RemoteIngressError("remote user_agent_message must be an object")
    unknown = sorted(str(key) for key in data.keys() if key not in _ALLOWED_USER_AGENT_MESSAGE_FIELDS)
    if unknown:
        raise RemoteIngressError(
            "unsupported remote user_agent_message fields: " + ", ".join(unknown)
        )

    agent_id = _first_non_empty_string(data, _AGENT_ID_FIELDS)
    if not agent_id:
        raise RemoteIngressError("remote user_agent_message.agent_id is required")
    message = _first_non_empty_string(data, _MESSAGE_FIELDS)
    if not message.strip():
        raise RemoteIngressError("remote user_agent_message.message is required")

    command: dict[str, Any] = {
        "cmd": "user_agent_message",
        "agent_id": agent_id.strip(),
        "message": message,
    }
    thread_id = _string_field(data, "thread_id")
    if thread_id:
        command["thread_id"] = thread_id
    reply_to_id = _string_field(data, "reply_to_id")
    if reply_to_id:
        command["reply_to_id"] = reply_to_id
    idempotency_key = _first_non_empty_string(data, _IDEMPOTENCY_FIELDS)
    if idempotency_key:
        command["idempotency_key"] = idempotency_key.strip()
    return command


async def ingest_remote_user_agent_message(
    data: Mapping[str, Any],
    *,
    state: Any,
    send_prompt: Any,
    handler: Callable[[dict[str, Any], Any, Any], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Validate a remote user message and invoke the local direct-message path."""

    if not callable(handler):
        raise TypeError("remote ingress handler must be callable")
    command = normalize_remote_user_agent_message(data)
    return await handler(command, state, send_prompt)
