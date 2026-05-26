"""Narrow remote-ingress helpers for future enterprise channels.

The relay/connector should not be able to call arbitrary daemon commands.  This
module validates a small user→agent direct-message shape and delegates to the
same local handler used by the browser's ``user_agent_message`` command.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any
import uuid

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
_ALLOWED_REMOTE_COMMAND_PAYLOAD_FIELDS = {
    "command_id",
    "cmd",
    "args",
    "confirm",
    "issued_at",
    "nonce",
}
_ALLOWED_REMOTE_COMMANDS = {"restart_agent"}
_RESTART_AGENT_ARG_FIELDS = {"agent_id"}
_COMMAND_FRESHNESS_SKEW_SECONDS = 300


class RemoteIngressError(ValueError):
    """Raised when a remote command fails the allowlist boundary."""


class RemoteCommandError(RemoteIngressError):
    """Raised when a privileged remote command request is rejected."""

    def __init__(
            self,
            message: str,
            *,
            error_code: str = "remote_command_rejected",
            status: str = "rejected"):
        super().__init__(message)
        self.error_code = error_code
        self.status = status


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


def normalize_remote_command_request(
        data: Mapping[str, Any],
        *,
        now: datetime | None = None) -> dict[str, Any]:
    """Return a sanitized local command for the privileged relay command lane.

    This is deliberately separate from ``normalize_remote_user_agent_message``:
    commands never ride through the user↔agent conversation allowlist. V1 ships
    exactly one command, ``restart_agent``, mapped to the existing cell-scoped
    local ``restart_agent`` command.
    """

    if not isinstance(data, Mapping):
        raise RemoteCommandError(
            "remote command_request must be an object",
            error_code="invalid_command_request",
        )
    unknown = sorted(
        str(key)
        for key in data.keys()
        if key not in _ALLOWED_REMOTE_COMMAND_PAYLOAD_FIELDS
    )
    if unknown:
        raise RemoteCommandError(
            "unsupported remote command_request fields: "
            + ", ".join(unknown),
            error_code="invalid_command_request",
        )

    command_id = _command_uuid(data.get("command_id"))
    cmd = _clean_command_string(data.get("cmd"), "cmd")
    if cmd not in _ALLOWED_REMOTE_COMMANDS:
        raise RemoteCommandError(
            f"unsupported remote command: {cmd}",
            error_code="unsupported_command",
        )
    issued_at = _command_issued_at(data.get("issued_at"))
    _assert_command_fresh(issued_at, now=now)
    _clean_command_string(data.get("nonce"), "nonce")

    args = data.get("args")
    if not isinstance(args, Mapping):
        raise RemoteCommandError(
            "remote command_request.args must be an object",
            error_code="invalid_command_args",
        )
    if cmd == "restart_agent":
        unknown_args = sorted(str(key) for key in args.keys()
                              if key not in _RESTART_AGENT_ARG_FIELDS)
        if unknown_args:
            raise RemoteCommandError(
                "unsupported restart_agent args: " + ", ".join(unknown_args),
                error_code="invalid_command_args",
            )
        agent_id = _clean_command_string(args.get("agent_id"), "args.agent_id")
        if not bool(data.get("confirm")):
            raise RemoteCommandError(
                "restart_agent requires explicit confirmation",
                error_code="confirmation_required",
                status="confirmation_required",
            )
        return {
            "command_id": command_id,
            "cmd": "restart_agent",
            "id": agent_id,
        }

    raise RemoteCommandError(
        f"unsupported remote command: {cmd}",
        error_code="unsupported_command",
    )


def command_result_payload(
        *,
        command_id: str,
        ok: bool,
        status: str,
        result: Mapping[str, Any] | None = None,
        error_code: str = "",
        message: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ref_command_id": command_id,
        "ok": bool(ok),
        "status": status,
    }
    if result:
        payload["result"] = dict(result)
    if error_code:
        payload["error_code"] = error_code
    if message:
        payload["message"] = message
    return payload


async def ingest_remote_command_request(
        data: Mapping[str, Any],
        *,
        state: Any,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]],
        now: datetime | None = None) -> dict[str, Any]:
    """Validate a privileged remote command and invoke its local executor."""

    if not callable(handler):
        raise TypeError("remote command handler must be callable")
    raw_command_id = _raw_command_id(data)
    try:
        command = normalize_remote_command_request(data, now=now)
    except RemoteCommandError as exc:
        return command_result_payload(
            command_id=raw_command_id,
            ok=False,
            status=exc.status,
            error_code=exc.error_code,
            message=str(exc),
        )

    if command["cmd"] == "restart_agent":
        cell = getattr(state, "agents", {}).get(command["id"])
        if cell is None:
            return command_result_payload(
                command_id=command["command_id"],
                ok=False,
                status="rejected",
                error_code="agent_not_found",
                message="Agent not found",
            )

    try:
        result = await handler({"cmd": command["cmd"], "id": command["id"]})
    except Exception as exc:
        return command_result_payload(
            command_id=command["command_id"],
            ok=False,
            status="error",
            error_code="remote_command_failed",
            message=str(exc) or type(exc).__name__,
        )
    if isinstance(result, Mapping) and str(result.get("type", "") or "") == "error":
        return command_result_payload(
            command_id=command["command_id"],
            ok=False,
            status="rejected",
            error_code=str(result.get("error") or result.get("code")
                           or "remote_command_rejected"),
            message=str(result.get("message") or "remote command rejected"),
        )
    return command_result_payload(
        command_id=command["command_id"],
        ok=True,
        status="ok",
        result=dict(result) if isinstance(result, Mapping) else {},
        message="restart_agent executed",
    )


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


def _clean_command_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise RemoteCommandError(
            f"remote command_request.{field_name} must be a string",
            error_code="invalid_command_request",
        )
    text = value.strip()
    if not text:
        raise RemoteCommandError(
            f"remote command_request.{field_name} is required",
            error_code="invalid_command_request",
        )
    return text


def _command_uuid(value: Any) -> str:
    text = _clean_command_string(value, "command_id")
    try:
        uuid.UUID(text)
    except Exception as exc:
        raise RemoteCommandError(
            "remote command_request.command_id must be a UUID",
            error_code="invalid_command_request",
        ) from exc
    return text


def _command_issued_at(value: Any) -> datetime:
    text = _clean_command_string(value, "issued_at")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception as exc:
        raise RemoteCommandError(
            "remote command_request.issued_at must be an ISO-8601 timestamp",
            error_code="invalid_command_timestamp",
        ) from exc


def _assert_command_fresh(
        issued_at: datetime,
        *,
        now: datetime | None = None) -> None:
    baseline = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    if baseline.tzinfo is None:
        baseline = baseline.replace(tzinfo=timezone.utc)
    skew = abs((baseline - issued_at).total_seconds())
    if skew > _COMMAND_FRESHNESS_SKEW_SECONDS:
        raise RemoteCommandError(
            "remote command_request.issued_at is outside the allowed skew window",
            error_code="stale_command",
        )


def _raw_command_id(data: Mapping[str, Any]) -> str:
    try:
        value = data.get("command_id") if isinstance(data, Mapping) else ""
        return str(value or "").strip()
    except Exception:
        return ""
