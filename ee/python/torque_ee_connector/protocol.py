"""Language-agnostic relay protocol helpers for the EE Python connector.

This mirrors ``ee/relay/src/core/protocol.ts`` closely enough for the local
Python daemon to validate and emit V1 envelopes without importing the TS relay.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

RELAY_PROTOCOL_VERSION = 1

RELAY_ENDPOINT_KINDS = (
    "daemon",
    "remote-client",
    "channel",
    "relay",
)

RELAY_MESSAGE_KINDS = (
    "hello",
    "ready",
    "ping",
    "pong",
    "snapshot_request",
    "snapshot",
    "user_message",
    "agent_message",
    "ask",
    "ask_reply",
    "ack",
    "error",
    "channel_event",
    # Daemon→relay establish-code mint request, and the relay→daemon response
    # carrying the raw single-use code exactly once. These ride the authenticated
    # daemon WS only (see EnterpriseConnector.mint_client_establish_code).
    "mint_client_establish_code",
    "mint_client_establish_code_result",
)

_ACK_DELIVERY_STATES = {"delivered", "acked", "failed"}


class RelayProtocolError(ValueError):
    """Raised when a relay envelope violates the V1 wire contract."""


def now_iso() -> str:
    """Return a UTC ISO-8601 timestamp compatible with the TS relay."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def new_relay_id(prefix: str = "msg") -> str:
    clean_prefix = str(prefix or "msg").strip() or "msg"
    return f"{clean_prefix}-{uuid.uuid4()}"


def parse_relay_envelope_json(text: str | bytes | bytearray) -> dict[str, Any]:
    try:
        if isinstance(text, (bytes, bytearray)):
            text = bytes(text).decode("utf-8")
        raw = json.loads(str(text or "{}"))
    except Exception as exc:  # pragma: no cover - exact JSON error varies
        raise RelayProtocolError(f"invalid relay JSON: {exc}") from exc
    return parse_relay_envelope(raw)


def parse_relay_envelope(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, Mapping):
        raise RelayProtocolError("relay envelope must be an object")
    raw = dict(input_value)
    if raw.get("v") != RELAY_PROTOCOL_VERSION:
        raise RelayProtocolError(
            f"unsupported relay protocol version: {raw.get('v')!s}"
        )
    created_at = _clean_required_string(raw.get("created_at"), "created_at")
    _validate_timestamp(created_at, "created_at")
    envelope: dict[str, Any] = {
        "v": RELAY_PROTOCOL_VERSION,
        "id": _clean_required_string(raw.get("id"), "id"),
        "daemon_id": _clean_required_string(raw.get("daemon_id"), "daemon_id"),
        "source": _normalize_endpoint(raw.get("source"), "source"),
        "target": _normalize_endpoint(raw.get("target"), "target"),
        "kind": _normalize_message_kind(raw.get("kind")),
        "created_at": created_at,
        "payload": _normalize_json_object(raw.get("payload") or {}, "payload"),
    }
    trace_id = _clean_optional_string(raw.get("trace_id"), "trace_id")
    if trace_id:
        envelope["trace_id"] = trace_id
    _validate_envelope_semantics(envelope)
    return envelope


def make_relay_envelope(
    *,
    daemon_id: str,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    kind: str,
    payload: Mapping[str, Any] | None = None,
    id: str = "",
    trace_id: str = "",
    created_at: str = "",
) -> dict[str, Any]:
    clean_daemon_id = _clean_required_string(daemon_id, "daemon_id")
    clean_kind = _normalize_message_kind(kind)
    clean_created_at = (
        _clean_optional_string(created_at, "created_at") or now_iso()
    )
    _validate_timestamp(clean_created_at, "created_at")
    envelope: dict[str, Any] = {
        "v": RELAY_PROTOCOL_VERSION,
        "id": _clean_optional_string(id, "id")
        or new_relay_id("ack" if clean_kind == "ack" else "msg"),
        "daemon_id": clean_daemon_id,
        "source": _normalize_endpoint(source, "source"),
        "target": _normalize_endpoint(target, "target"),
        "kind": clean_kind,
        "created_at": clean_created_at,
        "payload": _normalize_json_object(payload or {}, "payload"),
    }
    clean_trace_id = _clean_optional_string(trace_id, "trace_id")
    if clean_trace_id:
        envelope["trace_id"] = clean_trace_id
    _validate_envelope_semantics(envelope)
    return envelope


def make_ack_envelope(
    *,
    daemon_id: str,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    ack_id: str,
    ack_kind: str = "",
    delivery_state: str = "",
    reason: str = "",
    id: str = "",
    trace_id: str = "",
    created_at: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ack_id": _clean_required_string(ack_id, "ack_id"),
    }
    if ack_kind:
        payload["ack_kind"] = _normalize_message_kind(ack_kind)
    if delivery_state:
        state = _clean_required_string(delivery_state, "delivery_state")
        if state not in _ACK_DELIVERY_STATES:
            raise RelayProtocolError(
                f"ack.payload.delivery_state is invalid: {delivery_state!s}"
            )
        payload["delivery_state"] = state
    clean_reason = _clean_optional_string(reason, "reason")
    if clean_reason:
        payload["reason"] = clean_reason
    return make_relay_envelope(
        daemon_id=daemon_id,
        source=source,
        target=target,
        kind="ack",
        payload=payload,
        id=id,
        trace_id=trace_id,
        created_at=created_at,
    )


def make_error_envelope(
    *,
    daemon_id: str,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    code: str,
    message: str,
    retryable: bool | None = None,
    ref_id: str = "",
    id: str = "",
    trace_id: str = "",
    created_at: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": _clean_required_string(code, "code"),
        "message": _clean_required_string(message, "message"),
    }
    if retryable is not None:
        if not isinstance(retryable, bool):
            raise RelayProtocolError("error.payload.retryable must be a boolean")
        payload["retryable"] = retryable
    clean_ref_id = _clean_optional_string(ref_id, "ref_id")
    if clean_ref_id:
        payload["ref_id"] = clean_ref_id
    return make_relay_envelope(
        daemon_id=daemon_id,
        source=source,
        target=target,
        kind="error",
        payload=payload,
        id=id,
        trace_id=trace_id,
        created_at=created_at,
    )


def dumps_envelope(envelope: Mapping[str, Any]) -> str:
    parsed = parse_relay_envelope(envelope)
    return json.dumps(parsed, separators=(",", ":"), sort_keys=True)


def timestamp_from_epoch(value: Any) -> str:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        return now_iso()
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _normalize_endpoint(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RelayProtocolError(f"{field_name} endpoint is required")
    kind = _clean_required_string(value.get("kind"), f"{field_name}.kind")
    if kind not in RELAY_ENDPOINT_KINDS:
        raise RelayProtocolError(f"{field_name}.kind is invalid: {kind}")
    result: dict[str, Any] = {
        "kind": kind,
        "id": _clean_required_string(value.get("id"), f"{field_name}.id"),
    }
    user_id = _clean_optional_string(value.get("user_id"), f"{field_name}.user_id")
    platform = _clean_optional_string(
        value.get("platform"),
        f"{field_name}.platform",
    )
    if user_id:
        result["user_id"] = user_id
    if platform:
        result["platform"] = platform
    return result


def _normalize_message_kind(value: Any) -> str:
    kind = _clean_required_string(value, "kind")
    if kind not in RELAY_MESSAGE_KINDS:
        raise RelayProtocolError(f"kind is invalid: {kind}")
    return kind


def _normalize_json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RelayProtocolError(f"{field_name} must be an object")
    result = dict(value)
    _assert_json_value(result, field_name)
    return result


def _assert_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise RelayProtocolError(f"{path} must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_json_value(item, f"{path}.{key}")
        return
    raise RelayProtocolError(f"{path} must be JSON-serializable")


def _validate_envelope_semantics(envelope: Mapping[str, Any]) -> None:
    daemon_id = str(envelope.get("daemon_id") or "")
    source = dict(envelope.get("source") or {})
    target = dict(envelope.get("target") or {})
    if source.get("kind") == "daemon" and source.get("id") != daemon_id:
        raise RelayProtocolError("source daemon id must match daemon_id")
    if target.get("kind") == "daemon" and target.get("id") != daemon_id:
        raise RelayProtocolError("target daemon id must match daemon_id")
    if envelope.get("kind") == "ack":
        payload = dict(envelope.get("payload") or {})
        _clean_required_string(payload.get("ack_id"), "ack.payload.ack_id")
        if payload.get("ack_kind") is not None:
            _normalize_message_kind(payload.get("ack_kind"))
        if payload.get("delivery_state") is not None:
            delivery_state = _clean_required_string(
                payload.get("delivery_state"),
                "ack.payload.delivery_state",
            )
            if delivery_state not in _ACK_DELIVERY_STATES:
                raise RelayProtocolError(
                    "ack.payload.delivery_state is invalid: "
                    f"{payload.get('delivery_state')!s}"
                )
        _clean_optional_string(payload.get("reason"), "ack.payload.reason")
    if envelope.get("kind") == "error":
        payload = dict(envelope.get("payload") or {})
        _clean_required_string(payload.get("code"), "error.payload.code")
        _clean_required_string(payload.get("message"), "error.payload.message")
        _clean_optional_string(payload.get("ref_id"), "error.payload.ref_id")
        if payload.get("retryable") is not None and not isinstance(
            payload.get("retryable"),
            bool,
        ):
            raise RelayProtocolError("error.payload.retryable must be a boolean")


def _validate_timestamp(value: str, field_name: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise RelayProtocolError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc


def _clean_required_string(value: Any, field_name: str) -> str:
    text = _clean_optional_string(value, field_name)
    if not text:
        raise RelayProtocolError(f"{field_name} is required")
    return text


def _clean_optional_string(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RelayProtocolError(f"{field_name} must be a string")
    return value.strip()
