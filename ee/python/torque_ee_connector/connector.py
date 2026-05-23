"""Enterprise outbound relay connector for Channels Phase 4.

The open-core daemon loads this package only when explicitly enabled through the
``torque.cloud_hooks`` seam.  This connector keeps the daemon as the source of
truth: inbound relay ``user_message`` envelopes are routed to the existing
``user_agent_message`` command path, and local direct-message observer events are
published back to the relay as V1 JSON envelopes.

Loopback standalone development can use the local-dev unauthenticated mode.
Reachable/non-loopback relays require Phase 4 signed daemon attach headers.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from .auth import cryptography_available, load_private_key_pem_from_config, make_daemon_attach_headers
from .protocol import (
    RelayProtocolError,
    dumps_envelope,
    make_ack_envelope,
    make_error_envelope,
    make_relay_envelope,
    parse_relay_envelope,
    parse_relay_envelope_json,
    timestamp_from_epoch,
)

try:  # pragma: no cover - exercised inside the daemon; tests use fakes.
    from torque.config import log
except Exception:  # pragma: no cover - allows package-local tests/imports.
    import logging

    log = logging.getLogger("torque.ee.connector")

_LOCAL_RELAY_HOSTS = {"localhost", "127.0.0.1", "::1"}
_IDEMPOTENCY_FIELDS = (
    "idempotency_key",
    "idempotencyKey",
    "client_message_id",
    "clientMessageId",
)
_DIRECT_MESSAGE_OUTBOUND_TYPES = {"message", "ask", "ask_reply"}
CONNECTOR_DEBUG_BUFFER_LIMIT = 200
# Bounded recent-conversation snapshot sent on a client snapshot_request.
# Aligns with the daemon's per-agent DIRECT_MESSAGE_CACHE_LIMIT (100) and stays
# well under the ~500 frontend tail-cap.  NEVER unbounded.
SNAPSHOT_DEFAULT_MESSAGE_LIMIT = 100
SNAPSHOT_MAX_MESSAGE_LIMIT = 1000


@dataclass(frozen=True)
class ConnectorConfig:
    """Runtime config for the outbound relay connector."""

    relay_url: str
    daemon_id: str
    ws_url: str
    profile: str = ""
    reconnect_initial_seconds: float = 0.25
    reconnect_max_seconds: float = 5.0
    outbound_queue_size: int = 500
    auth_mode: str = "local-dev-unauthenticated"
    credential_id: str = ""
    private_key_pem: str = ""


@dataclass
class EnterpriseConnector:
    """Outbound WebSocket connector attached through ``torque.cloud_hooks``."""

    context: Any
    config: ConnectorConfig | None = None
    started: bool = False
    observed_events: list[str] = field(default_factory=list)
    received_envelopes: list[str] = field(default_factory=list)
    sent_envelopes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._outbound_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._queue_size_from_context()
        )
        self._run_task: asyncio.Task[Any] | None = None
        self._ws: Any = None
        self._session: Any = None
        self._owns_session = False
        self._epoch = 0

    async def start(self) -> None:
        """Validate config and start the background outbound WS loop."""

        if self.started:
            return
        self.config = self.config or config_from_context(self.context)
        self._stop_event.clear()
        self._run_task = asyncio.create_task(
            self._run_forever(),
            name="torque-ee-relay-connector",
        )
        self.started = True
        log.info(
            "Torque EE connector enabled for local relay %s as daemon_id=%s",
            self.config.ws_url,
            self.config.daemon_id,
        )

    async def stop(self) -> None:
        """Stop the background connector loop and close network resources."""

        self._stop_event.set()
        task = self._run_task
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_ws()
        await self._close_session()
        self._connected_event.clear()
        self.started = False

    async def on_direct_message(self, event: dict[str, Any]) -> None:
        """Observe local direct-message rows and enqueue outbound envelopes."""

        event_type = str((event or {}).get("type", "") or "").strip()
        if event_type:
            _append_debug(self.observed_events, event_type)
        if not self.started or event_type != "direct_message_saved":
            return
        envelope = self._envelope_for_direct_message_event(event)
        if not envelope:
            return
        try:
            self._outbound_queue.put_nowait(envelope)
        except asyncio.QueueFull:
            log.warning(
                "EE connector outbound queue full; dropping direct message %s",
                ((event or {}).get("row") or {}).get("id", ""),
            )

    async def _run_forever(self) -> None:
        assert self.config is not None
        backoff = self.config.reconnect_initial_seconds
        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                backoff = self.config.reconnect_initial_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stop_event.is_set():
                    log.warning("EE relay connector disconnected: %s", exc)
            self._connected_event.clear()
            if self._stop_event.is_set():
                break
            await self._sleep_or_stop(backoff)
            backoff = min(backoff * 2, self.config.reconnect_max_seconds)

    async def _connect_once(self) -> None:
        assert self.config is not None
        session = await self._ensure_session()
        headers = self._attach_headers()
        async with session.ws_connect(self.config.ws_url, headers=headers or None) as ws:
            self._ws = ws
            self._connected_event.set()
            await self._send_hello()
            receive_task = asyncio.create_task(self._receive_loop(ws))
            send_task = asyncio.create_task(self._send_loop(ws))
            done, pending = await asyncio.wait(
                {receive_task, send_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
        self._ws = None

    async def _receive_loop(self, ws: Any) -> None:
        async for message in ws:
            if self._stop_event.is_set():
                return
            text = _ws_message_text(message)
            if text is None:
                continue
            try:
                envelope = parse_relay_envelope_json(text)
            except RelayProtocolError as exc:
                await self._send_error(
                    code="relay_protocol_error",
                    message=str(exc),
                    ref_id="",
                )
                continue
            await self._handle_envelope(envelope)

    async def _send_loop(self, ws: Any) -> None:
        while not self._stop_event.is_set():
            envelope = await self._outbound_queue.get()
            try:
                await self._send_envelope(envelope, ws=ws)
            except Exception:
                self._requeue_front_best_effort(envelope)
                raise
            finally:
                self._outbound_queue.task_done()

    async def _handle_envelope(self, envelope: Mapping[str, Any]) -> None:
        parsed = parse_relay_envelope(envelope)
        kind = str(parsed.get("kind", "") or "")
        envelope_id = str(parsed.get("id", "") or "")
        _append_debug(self.received_envelopes, envelope_id)
        if kind == "ready":
            payload = parsed.get("payload") or {}
            try:
                self._epoch = int((payload or {}).get("epoch") or 0)
            except (TypeError, ValueError):
                self._epoch = 0
            return
        if kind == "ping":
            await self._send_control_reply(parsed, "pong")
            return
        if kind == "pong":
            return
        if kind == "error":
            log.warning("EE relay error envelope received: %s", parsed.get("payload"))
            return
        if kind == "ack":
            return
        if kind == "user_message":
            await self._handle_user_message(parsed)
            return
        if kind == "snapshot_request":
            await self._handle_snapshot_request(parsed)
            return
        await self._send_error(
            code="unsupported_relay_kind",
            message=f"unsupported relay kind for local daemon: {kind}",
            ref_id=envelope_id,
            trace_id=str(parsed.get("trace_id", "") or ""),
        )

    async def _handle_user_message(self, envelope: Mapping[str, Any]) -> None:
        payload = dict(envelope.get("payload") or {})
        if not any(str(payload.get(field, "") or "").strip() for field in _IDEMPOTENCY_FIELDS):
            payload["idempotency_key"] = str(envelope.get("id", "") or "").strip()
        try:
            result = await self._call_remote_ingress(payload)
        except Exception as exc:
            log.exception("Remote user_message ingress failed")
            await self._send_ack(
                envelope,
                delivery_state="failed",
                reason=str(exc) or type(exc).__name__,
            )
            await self._send_error(
                code="remote_ingress_failed",
                message=str(exc) or type(exc).__name__,
                ref_id=str(envelope.get("id", "") or ""),
                trace_id=str(envelope.get("trace_id", "") or ""),
                target=envelope.get("source"),
            )
            return
        if str((result or {}).get("type", "") or "").strip() == "error":
            code, message, retryable = _remote_ingress_error_details(result)
            await self._send_ack(
                envelope,
                delivery_state="failed",
                reason=message,
            )
            await self._send_error(
                code=code,
                message=message,
                retryable=retryable,
                ref_id=str(envelope.get("id", "") or ""),
                trace_id=str(envelope.get("trace_id", "") or ""),
                target=envelope.get("source"),
            )
            return
        await self._send_ack(envelope, delivery_state="acked")

    async def _call_remote_ingress(self, payload: dict[str, Any]) -> dict[str, Any]:
        ingress = getattr(self.context, "remote_user_agent_message", None)
        if not callable(ingress) and isinstance(self.context, Mapping):
            ingress = self.context.get("remote_user_agent_message")
        if not callable(ingress):
            raise RuntimeError("remote user-message ingress is unavailable")
        result = ingress(payload)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Mapping):
            return dict(result)
        return {"type": "ok", "result": str(result)}

    async def _handle_snapshot_request(self, envelope: Mapping[str, Any]) -> None:
        """Answer a client snapshot_request with bounded recent conversation.

        Recent user↔agent rows are drawn from the SAME DM-mirror source that
        feeds live egress and shaped through the SAME gate + payload builder, so
        a snapshot row is byte-identical to the live envelope the client would
        have received.  In particular asks are gated to user-destined via
        ``_wire_kind_for_direct_message_row`` (the canonical resolver-stamped
        gate from TORQUE:534): owner-routed asks never enter the snapshot.
        """
        assert self.config is not None
        limit = self._snapshot_limit_from_request(envelope)
        rows = await self._call_recent_direct_messages(limit)
        messages: list[dict[str, Any]] = []
        for row in rows:
            wire_kind = _wire_kind_for_direct_message_row(row)
            if not wire_kind:
                continue
            agent_id = _agent_id_for_direct_message(row)
            entry = _direct_message_payload(row, agent_id)
            entry["kind"] = wire_kind
            messages.append(entry)
        # Provider returns newest-first; present oldest-first so the client can
        # append the snapshot in chronological order like the live stream.
        messages.reverse()
        if len(messages) > limit:
            messages = messages[-limit:]
        await self._send_envelope(
            make_relay_envelope(
                daemon_id=self.config.daemon_id,
                source={"kind": "daemon", "id": self.config.daemon_id},
                target=_snapshot_client_target(envelope.get("source")),
                kind="snapshot",
                payload={
                    "lane": "user-agent",
                    "messages": messages,
                    "count": len(messages),
                    "limit": limit,
                    "truncated": bool(len(rows) >= limit),
                    "ref_id": str(envelope.get("id", "") or ""),
                },
                trace_id=str(envelope.get("trace_id", "") or ""),
            )
        )

    async def _call_recent_direct_messages(self, limit: int) -> list[dict[str, Any]]:
        provider = getattr(self.context, "recent_direct_messages", None)
        if not callable(provider) and isinstance(self.context, Mapping):
            provider = self.context.get("recent_direct_messages")
        if not callable(provider):
            return []
        try:
            result = provider(limit)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            log.exception("EE connector recent direct-message provider failed")
            return []
        if not isinstance(result, list):
            return []
        return [dict(row) for row in result if isinstance(row, Mapping)]

    def _snapshot_message_cap(self) -> int:
        config = _context_config(self.context)
        raw = config.get("snapshot_message_limit") or os.environ.get(
            "TORQUE_EE_CONNECTOR_SNAPSHOT_LIMIT",
            SNAPSHOT_DEFAULT_MESSAGE_LIMIT,
        )
        try:
            return max(1, min(int(raw), SNAPSHOT_MAX_MESSAGE_LIMIT))
        except (TypeError, ValueError):
            return SNAPSHOT_DEFAULT_MESSAGE_LIMIT

    def _snapshot_limit_from_request(self, envelope: Mapping[str, Any]) -> int:
        cap = self._snapshot_message_cap()
        payload = envelope.get("payload") or {}
        requested = payload.get("limit") if isinstance(payload, Mapping) else None
        if requested is None:
            return cap
        try:
            req = int(requested)
        except (TypeError, ValueError):
            return cap
        if req < 1:
            return cap
        return min(req, cap)


    def _attach_headers(self) -> dict[str, str]:
        assert self.config is not None
        if self.config.auth_mode != "signed-attach-v1":
            return {}
        return make_daemon_attach_headers(
            ws_url=self.config.ws_url,
            daemon_id=self.config.daemon_id,
            credential_id=self.config.credential_id,
            private_key_pem=self.config.private_key_pem,
        )

    async def _send_hello(self) -> None:
        assert self.config is not None
        await self._send_envelope(
            make_relay_envelope(
                daemon_id=self.config.daemon_id,
                source={"kind": "daemon", "id": self.config.daemon_id},
                target={"kind": "relay", "id": "relay"},
                kind="hello",
                payload={
                    "protocol_version": 1,
                    "capabilities": [
                        "remote_user_message_ingress",
                        "direct_message_observer",
                    ],
                    "profile": self.config.profile,
                    "auth": self.config.auth_mode,
                },
            )
        )

    async def _send_control_reply(
        self,
        envelope: Mapping[str, Any],
        kind: str,
    ) -> None:
        assert self.config is not None
        await self._send_envelope(
            make_relay_envelope(
                daemon_id=self.config.daemon_id,
                source={"kind": "daemon", "id": self.config.daemon_id},
                target={"kind": "relay", "id": "relay"},
                kind=kind,
                payload={"ref_id": str(envelope.get("id", "") or "")},
                trace_id=str(envelope.get("trace_id", "") or ""),
            )
        )

    async def _send_ack(
        self,
        envelope: Mapping[str, Any],
        *,
        delivery_state: str,
        reason: str = "",
    ) -> None:
        assert self.config is not None
        await self._send_envelope(
            make_ack_envelope(
                daemon_id=self.config.daemon_id,
                source={"kind": "daemon", "id": self.config.daemon_id},
                target={"kind": "relay", "id": "relay"},
                ack_id=str(envelope.get("id", "") or ""),
                ack_kind=str(envelope.get("kind", "") or ""),
                delivery_state=delivery_state,
                reason=reason,
                trace_id=str(envelope.get("trace_id", "") or ""),
            )
        )

    async def _send_error(
        self,
        *,
        code: str,
        message: str,
        retryable: bool | None = False,
        ref_id: str = "",
        trace_id: str = "",
        target: Mapping[str, Any] | None = None,
    ) -> None:
        assert self.config is not None
        await self._send_envelope(
            make_error_envelope(
                daemon_id=self.config.daemon_id,
                source={"kind": "daemon", "id": self.config.daemon_id},
                target=target or {"kind": "relay", "id": "relay"},
                code=code,
                message=message,
                retryable=retryable,
                ref_id=ref_id,
                trace_id=trace_id,
            )
        )

    async def _send_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        ws: Any = None,
    ) -> None:
        text = dumps_envelope(envelope)
        target_ws = ws or self._ws
        if target_ws is None:
            raise RuntimeError("relay websocket is not connected")
        send = getattr(target_ws, "send_str", None)
        if callable(send):
            result = send(text)
        else:
            result = target_ws.send(text)
        if inspect.isawaitable(result):
            await result
        _append_debug(self.sent_envelopes, str(envelope.get("id", "") or ""))

    def _envelope_for_direct_message_event(
        self,
        event: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        assert self.config is not None
        row = dict((event or {}).get("row") or {})
        wire_kind = _wire_kind_for_direct_message_row(row)
        if not wire_kind:
            return None
        message_id = str(row.get("id", "") or "").strip()
        if not message_id:
            return None
        agent_id = _agent_id_for_direct_message(row)
        payload = _direct_message_payload(row, agent_id)
        return make_relay_envelope(
            id=f"dm:{message_id}:{wire_kind}",
            daemon_id=self.config.daemon_id,
            source={"kind": "daemon", "id": self.config.daemon_id},
            target={"kind": "remote-client", "id": "user", "user_id": "user"},
            kind=wire_kind,
            created_at=timestamp_from_epoch(row.get("created_at")),
            payload=payload,
        )

    async def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        context_config = _context_config(self.context)
        session_factory = context_config.get("session_factory")
        if callable(session_factory):
            self._session = session_factory()
            self._owns_session = True
            return self._session
        try:
            import aiohttp  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "aiohttp is required for the Torque EE relay connector"
            ) from exc
        self._session = aiohttp.ClientSession()
        self._owns_session = True
        return self._session

    async def _close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        close = getattr(ws, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _close_session(self) -> None:
        session = self._session
        self._session = None
        if session is None or not self._owns_session:
            return
        close = getattr(session, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        self._owns_session = False

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            return

    def _requeue_front_best_effort(self, envelope: dict[str, Any]) -> None:
        try:
            self._outbound_queue.put_nowait(envelope)
        except asyncio.QueueFull:
            pass

    def _queue_size_from_context(self) -> int:
        config = _context_config(self.context)
        raw = config.get("outbound_queue_size") or os.environ.get(
            "TORQUE_EE_CONNECTOR_OUTBOUND_QUEUE_SIZE",
            "500",
        )
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 500


def create_connector(context: Any) -> EnterpriseConnector:
    """Factory consumed by ``torque.cloud_hooks.start_cloud_connector``."""

    return EnterpriseConnector(context=context)


def config_from_context(context: Any) -> ConnectorConfig:
    context_config = _merged_context_config(context)
    relay_url = _first_non_empty(
        context_config.get("relay_url"),
        os.environ.get("TORQUE_EE_RELAY_URL"),
        os.environ.get("TORQUE_CLOUD_RELAY_URL"),
    )
    if not relay_url:
        raise ValueError("TORQUE_EE_RELAY_URL is required when EE connector is enabled")
    daemon_id = _first_non_empty(
        context_config.get("daemon_id"),
        os.environ.get("TORQUE_EE_DAEMON_ID"),
        os.environ.get("TORQUE_CLOUD_DAEMON_ID"),
        getattr(context, "profile", ""),
        os.path.basename(str(getattr(context, "data_dir", "") or "")),
        "default",
    )
    profile = _first_non_empty(
        getattr(context, "profile", ""),
        context_config.get("profile"),
    )
    ws_url = build_daemon_ws_url(relay_url, daemon_id)
    local_relay = _is_local_relay_url(ws_url)
    credential_id = _first_non_empty(
        context_config.get("credential_id"),
        context_config.get("daemon_credential_id"),
        os.environ.get("TORQUE_EE_DAEMON_CREDENTIAL_ID"),
    )
    private_key_pem = _first_non_empty(
        load_private_key_pem_from_config(context_config),
        os.environ.get("TORQUE_EE_DAEMON_PRIVATE_KEY_PEM"),
    )
    auth_mode = "signed-attach-v1" if credential_id or private_key_pem else "local-dev-unauthenticated"
    if auth_mode == "signed-attach-v1":
        if not credential_id or not private_key_pem:
            raise ValueError("signed relay attach requires both credential_id and private_key_pem")
        if not cryptography_available():
            raise ValueError("cryptography is unavailable; signed relay attach is disabled cleanly")
    if not local_relay:
        if auth_mode != "signed-attach-v1":
            raise ValueError(
                "Phase-4 EE connector only supports local relay URLs unless signed daemon auth is configured"
            )
        if not ws_url.startswith("wss://"):
            raise ValueError("remote relay URLs require wss:// or https:// when signed daemon auth is enabled")
    return ConnectorConfig(
        relay_url=relay_url,
        daemon_id=daemon_id,
        ws_url=ws_url,
        profile=profile,
        reconnect_initial_seconds=_float_config(
            context_config,
            "reconnect_initial_seconds",
            "TORQUE_EE_CONNECTOR_RECONNECT_INITIAL_SECONDS",
            0.25,
        ),
        reconnect_max_seconds=_float_config(
            context_config,
            "reconnect_max_seconds",
            "TORQUE_EE_CONNECTOR_RECONNECT_MAX_SECONDS",
            5.0,
        ),
        outbound_queue_size=_int_config(
            context_config,
            "outbound_queue_size",
            "TORQUE_EE_CONNECTOR_OUTBOUND_QUEUE_SIZE",
            500,
        ),
        auth_mode=auth_mode,
        credential_id=credential_id,
        private_key_pem=private_key_pem,
    )


def build_daemon_ws_url(relay_url: str, daemon_id: str) -> str:
    """Return the daemon WS endpoint for a relay base URL or full WS URL."""

    raw = str(relay_url or "").strip()
    if not raw:
        raise ValueError("relay_url is required")
    parsed = urlparse(raw)
    if "://" not in raw:
        parsed = urlparse(f"http://{raw}")
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError(f"unsupported relay URL scheme: {parsed.scheme}")
    ws_scheme = "wss" if scheme in {"https", "wss"} else "ws"
    path = parsed.path or ""
    if not path.rstrip("/").endswith("/ws"):
        path = f"{path.rstrip('/')}/v1/daemon/{quote(str(daemon_id or '').strip(), safe='')}/ws"
    return urlunparse((
        ws_scheme,
        parsed.netloc,
        path,
        "",
        parsed.query,
        "",
    ))


def _is_local_relay_url(ws_url: str) -> bool:
    parsed = urlparse(ws_url)
    host = (parsed.hostname or "").strip().lower()
    if host in _LOCAL_RELAY_HOSTS or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback


def _assert_local_relay_url(ws_url: str) -> None:
    if _is_local_relay_url(ws_url):
        return
    parsed = urlparse(ws_url)
    host = (parsed.hostname or "").strip().lower()
    raise ValueError(
        "Phase-4 EE connector only supports local relay URLs unless signed daemon auth is configured; "
        f"got host {host!r}"
    )


def _row_recipient_is_user(row: Mapping[str, Any]) -> bool:
    """Whether the row's resolved recipient is the canonical user.

    The ``recipient_*`` fields are stamped onto the persisted direct-message
    row by the daemon's canonical owner-aware resolver
    (``torque.direct_message_mirrors.resolve_ask_recipient``).  The connector
    consumes that resolution; it never re-derives ownership.
    """
    return (
        str(row.get("recipient_kind", "") or "").strip() == "user"
        and str(row.get("recipient_id", "") or "").strip() == "user"
    )


def _wire_kind_for_direct_message_row(row: Mapping[str, Any]) -> str:
    message_type = str(row.get("message_type", "message") or "message").strip()
    if message_type not in _DIRECT_MESSAGE_OUTBOUND_TYPES:
        return ""
    sender_kind = str(row.get("sender_kind", "") or "").strip()
    recipient_kind = str(row.get("recipient_kind", "") or "").strip()
    if message_type == "ask":
        # Only genuinely user-destined asks reach the remote-client/user lane.
        # Owner-routed asks (recipient is an engineer/architect) stay local.
        return "ask" if _row_recipient_is_user(row) else ""
    if message_type == "ask_reply":
        # A reply mirrors the original ask with sender/recipient swapped, so the
        # answerer is the sender.  Egress only replies to user-destined asks,
        # i.e. where the user is the answerer.
        return "ask_reply" if sender_kind == "user" else ""
    if message_type == "message" and sender_kind != "user" and recipient_kind == "user":
        return "agent_message"
    return ""


def _snapshot_client_target(source: Mapping[str, Any] | None) -> dict[str, Any]:
    """Target the snapshot back at the requesting remote client.

    Echoes the request's remote-client endpoint so the relay routes the
    snapshot to the asking client; falls back to the canonical single-user
    remote-client lane when the source is missing/unexpected.
    """
    src = dict(source or {})
    if str(src.get("kind", "") or "").strip() == "remote-client":
        target: dict[str, Any] = {
            "kind": "remote-client",
            "id": str(src.get("id", "") or "user").strip() or "user",
        }
        user_id = str(src.get("user_id", "") or "").strip()
        if user_id:
            target["user_id"] = user_id
        return target
    return {"kind": "remote-client", "id": "user", "user_id": "user"}


def _agent_id_for_direct_message(row: Mapping[str, Any]) -> str:
    for prefix in ("sender", "recipient"):
        kind = str(row.get(f"{prefix}_kind", "") or "").strip()
        value = str(row.get(f"{prefix}_id", "") or "").strip()
        if kind != "user" and value:
            return value
    return ""


def _direct_message_payload(row: Mapping[str, Any], agent_id: str) -> dict[str, Any]:
    payload = {
        "message_id": str(row.get("id", "") or "").strip(),
        "agent_id": str(agent_id or "").strip(),
        "thread_id": str(row.get("thread_id", "") or "").strip(),
        "reply_to_id": str(row.get("reply_to_id", "") or "").strip(),
        "message": str(row.get("message", "") or ""),
        "message_type": str(row.get("message_type", "message") or "message"),
        "group": str(row.get("group_name", row.get("group", "")) or ""),
        "sender_id": str(row.get("sender_id", "") or "").strip(),
        "sender_kind": str(row.get("sender_kind", "") or "").strip(),
        "sender_name": str(row.get("sender_name", "") or ""),
        "recipient_id": str(row.get("recipient_id", "") or "").strip(),
        "recipient_kind": str(row.get("recipient_kind", "") or "").strip(),
        "recipient_name": str(row.get("recipient_name", "") or ""),
        "created_at": timestamp_from_epoch(row.get("created_at")),
        "blocking": bool(row.get("blocking", False)),
        "ack_required": bool(row.get("ack_required", False)),
        "source_task_id": str(row.get("source_task_id", "") or "").strip(),
        "delivery_state": str(row.get("delivery_state", "") or "").strip(),
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered_at": timestamp_from_epoch(row.get("delivered_at"))
        if row.get("delivered_at")
        else "",
        "read_at": timestamp_from_epoch(row.get("read_at"))
        if row.get("read_at")
        else "",
    }
    return {key: value for key, value in payload.items() if value != ""}


def _append_debug(buffer: list[str], value: str) -> None:
    buffer.append(value)
    overflow = len(buffer) - CONNECTOR_DEBUG_BUFFER_LIMIT
    if overflow > 0:
        del buffer[:overflow]


def _remote_ingress_error_details(result: Mapping[str, Any] | None) -> tuple[str, str, bool]:
    raw = dict(result or {})
    code = _first_non_empty(
        raw.get("code"),
        raw.get("error_code"),
        "remote_ingress_failed",
    )
    message = _first_non_empty(
        raw.get("message"),
        raw.get("error"),
        "remote ingress failed",
    )
    retryable = raw.get("retryable")
    return code, message, retryable if isinstance(retryable, bool) else False


def _ws_message_text(message: Any) -> str | None:
    data = getattr(message, "data", message)
    if isinstance(data, str):
        return data
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).decode("utf-8")
    return None


def _merged_context_config(context: Any) -> dict[str, Any]:
    config = _profile_connector_config(context)
    config.update(_context_config(context))
    return config


def _profile_connector_config(context: Any) -> dict[str, Any]:
    data_dir = str(getattr(context, "data_dir", "") or "")
    if isinstance(context, Mapping):
        data_dir = str(context.get("data_dir", data_dir) or data_dir)
    if not data_dir:
        return {}
    path = Path(data_dir).expanduser() / "ee_connector.json"
    if not path.exists():
        return {}
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return {}
    if mode & 0o077:
        raise ValueError("EE connector config file must not be group/world accessible; expected mode 0600")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return dict(data or {}) if isinstance(data, Mapping) else {}


def _context_config(context: Any) -> dict[str, Any]:
    if isinstance(context, Mapping):
        config = context.get("config", {})
    else:
        config = getattr(context, "config", {})
    return dict(config or {}) if isinstance(config, Mapping) else {}


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _float_config(config: Mapping[str, Any], key: str, env_name: str, default: float) -> float:
    raw = config.get(key, os.environ.get(env_name, default))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default


def _int_config(config: Mapping[str, Any], key: str, env_name: str, default: int) -> int:
    raw = config.get(key, os.environ.get(env_name, default))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default
