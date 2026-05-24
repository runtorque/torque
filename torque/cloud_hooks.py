"""No-op extension seams for enterprise cloud connectors.

The open-core daemon never imports enterprise code unless an operator explicitly
opts in with a connector flag.  When disabled (the community default), every
function in this module is a cheap no-op.  Enterprise packages can register a
connector at this seam without duplicating daemon startup, remote ingress, or
direct-message observation logic.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

from . import config as torque_config
from .config import log

# Per-field provenance labels for the resolved relay config surfaced to the UI.
RELAY_SOURCE_SETTINGS = "settings"
RELAY_SOURCE_FILE = "ee_connector.json"
RELAY_SOURCE_ENV = "env"
RELAY_SOURCE_UNSET = ""

DirectMessageObserver = Callable[[dict[str, Any]], Any]
RemoteUserAgentIngress = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RecentDirectMessages = Callable[[int], list[dict[str, Any]]]
AgentRoster = Callable[[], list[dict[str, Any]]]
ReportConnectionState = Callable[[dict[str, Any]], None]
Unregister = Callable[[], None]

_DIRECT_MESSAGE_OBSERVERS: list[DirectMessageObserver] = []


@dataclass(frozen=True)
class CloudConnectorContext:
    """Runtime services passed to an optional enterprise connector."""

    state: Any
    remote_user_agent_message: RemoteUserAgentIngress
    register_direct_message_observer: Callable[[DirectMessageObserver], Unregister]
    profile: str = ""
    data_dir: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    # Optional: bounded recent user↔agent conversation rows for snapshot-on-open
    # (newest-first canonical rows).  ``None`` in community/legacy builds; the
    # connector then emits an empty snapshot.
    recent_direct_messages: RecentDirectMessages | None = None
    # Optional: agent-kind roster for the same group-scoped remote snapshot.
    # ``None`` in community/legacy builds; the connector then omits the additive
    # snapshot ``agents`` key for old payload compatibility.
    agent_roster: AgentRoster | None = None
    # Optional: report the connector's relay connection-state transitions to the
    # daemon as the ephemeral ``relay_connection`` signal.  ``None`` in
    # community/legacy builds.  The connector wraps every invocation in
    # try/except, so a missing or raising callback can never break the connector.
    report_connection_state: ReportConnectionState | None = None


@dataclass
class CloudConnectorRuntime:
    """Bookkeeping for a loaded enterprise connector, if any."""

    enabled: bool = False
    module_name: str = ""
    connector: Any = None
    started: bool = False
    error: str = ""
    unregister_callbacks: list[Unregister] = field(default_factory=list)


def register_direct_message_observer(observer: DirectMessageObserver) -> Unregister:
    """Register a callback for canonical direct-message row events.

    Observers receive a single event dict with ``type``, ``row``, ``state`` and
    ``agent_ids``.  The returned function unregisters the observer.  Community
    builds register nothing, so notification remains a no-op by default.
    """

    if not callable(observer):
        raise TypeError("direct-message observer must be callable")
    _DIRECT_MESSAGE_OBSERVERS.append(observer)

    def unregister() -> None:
        try:
            _DIRECT_MESSAGE_OBSERVERS.remove(observer)
        except ValueError:
            pass

    return unregister


def _direct_message_agent_ids(row: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for field in ("sender", "recipient"):
        kind = str((row or {}).get(f"{field}_kind", "") or "").strip()
        agent_id = str((row or {}).get(f"{field}_id", "") or "").strip()
        if kind == "user" or not agent_id or agent_id in ids:
            continue
        ids.append(agent_id)
    return ids


async def _await_observer_result(result: Awaitable[Any], event_type: str) -> None:
    try:
        await result
    except Exception:
        log.exception("Cloud direct-message observer failed for %s", event_type)


def notify_direct_message_observers(
    event_type: str,
    row: dict[str, Any] | None,
    *,
    state: Any = None,
) -> int:
    """Notify optional connector observers after local direct-message writes.

    Returns the number of observers scheduled/invoked.  Exceptions are logged and
    swallowed so an enterprise observer cannot break community message delivery.
    """

    if not _DIRECT_MESSAGE_OBSERVERS or not row:
        return 0
    event = {
        "type": str(event_type or "direct_message"),
        "row": dict(row),
        "state": state,
        "agent_ids": _direct_message_agent_ids(row),
    }
    invoked = 0
    for observer in list(_DIRECT_MESSAGE_OBSERVERS):
        try:
            result = observer(dict(event))
            invoked += 1
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    log.warning(
                        "Async cloud direct-message observer returned without "
                        "a running event loop for %s",
                        event_type,
                    )
                    if inspect.iscoroutine(result):
                        result.close()
                else:
                    loop.create_task(_await_observer_result(result, event_type))
        except Exception:
            log.exception("Cloud direct-message observer failed for %s", event_type)
    return invoked


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _read_ee_connector_json(data_dir: str) -> dict[str, Any]:
    """Best-effort read of ``ee_connector.json`` for source resolution.

    Purely informational for the relay source-indicator: unlike the connector's
    own loader we do NOT enforce file permissions or raise here, so a malformed
    or unreadable file simply contributes no file-sourced values.
    """

    if not data_dir:
        return {}
    try:
        path = Path(data_dir).expanduser() / "ee_connector.json"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        log.debug(
            "ee_connector.json read for relay source-indicator failed",
            exc_info=True,
        )
        return {}


def resolve_relay_config(settings: Any, *, data_dir: str = "") -> dict[str, Any]:
    """Resolve effective relay (cloud connector) config and its provenance.

    Precedence per field is Global Settings > ``ee_connector.json`` > env, which
    mirrors how the connector's own ``config_from_context`` merges
    ``context.config`` over the profile file over the environment. Only NON-EMPTY
    settings values are placed into the returned ``config`` (the context.config
    dict): leaving a field unset must fall through to the file/env fallback
    inside the connector rather than clobbering it with an empty string.

    The private key is referenced by path only — an inline PEM is never read,
    stored, or surfaced here.

    Returns ``{"config": {...}, "sources": {field: {"value", "source"}}}``.
    """

    settings_vals = {
        "enabled": bool(getattr(settings, "relay_enabled", False)),
        "relay_url": str(getattr(settings, "relay_url", "") or "").strip(),
        "daemon_id": str(getattr(settings, "relay_daemon_id", "") or "").strip(),
        "credential_id": str(
            getattr(settings, "relay_credential_id", "") or ""
        ).strip(),
        "private_key_path": str(
            getattr(settings, "relay_private_key_path", "") or ""
        ).strip(),
    }

    file_vals = _read_ee_connector_json(data_dir)

    def _file(*keys: str) -> str:
        for key in keys:
            val = str(file_vals.get(key, "") or "").strip()
            if val:
                return val
        return ""

    file_relay_url = _file("relay_url")
    file_daemon_id = _file("daemon_id")
    file_credential_id = _file("credential_id", "daemon_credential_id")
    file_key_path = _file("private_key_path", "daemon_private_key_path")
    # An inline PEM in the file still counts as a file-sourced key for the
    # provenance badge, but we only ever surface the path (never the secret).
    file_has_key = bool(
        file_key_path or _file("private_key_pem", "daemon_private_key_pem")
    )

    env_enabled = bool(getattr(torque_config, "CLOUD_CONNECTOR_ENABLED", False))
    env_relay_url = str(getattr(torque_config, "CLOUD_RELAY_URL", "") or "").strip()
    env_daemon_id = str(getattr(torque_config, "CLOUD_DAEMON_ID", "") or "").strip()
    env_credential_id = str(
        os.environ.get("TORQUE_EE_DAEMON_CREDENTIAL_ID", "") or ""
    ).strip()

    sources: dict[str, Any] = {}
    config: dict[str, Any] = {}

    # enabled — the settings toggle wins; the env flag is the fallback.
    if settings_vals["enabled"]:
        sources["enabled"] = {"value": True, "source": RELAY_SOURCE_SETTINGS}
    elif env_enabled:
        sources["enabled"] = {"value": True, "source": RELAY_SOURCE_ENV}
    else:
        sources["enabled"] = {"value": False, "source": RELAY_SOURCE_UNSET}
    config["enabled"] = sources["enabled"]["value"]

    def _resolve(
        field_key: str,
        settings_val: str,
        file_val: str,
        env_val: str,
        *,
        config_key: str,
        file_present: bool | None = None,
    ) -> None:
        present = bool(file_val) if file_present is None else file_present
        if settings_val:
            source, value = RELAY_SOURCE_SETTINGS, settings_val
        elif present:
            source, value = RELAY_SOURCE_FILE, file_val
        elif env_val:
            source, value = RELAY_SOURCE_ENV, env_val
        else:
            source, value = RELAY_SOURCE_UNSET, ""
        sources[field_key] = {"value": value, "source": source}
        # Only the settings value flows into context.config; file/env values are
        # left for the connector's own resolver so we never clobber its fallback.
        if settings_val:
            config[config_key] = settings_val

    _resolve(
        "relay_url", settings_vals["relay_url"], file_relay_url, env_relay_url,
        config_key="relay_url",
    )
    _resolve(
        "daemon_id", settings_vals["daemon_id"], file_daemon_id, env_daemon_id,
        config_key="daemon_id",
    )
    _resolve(
        "credential_id", settings_vals["credential_id"], file_credential_id,
        env_credential_id, config_key="credential_id",
    )
    _resolve(
        "private_key_path", settings_vals["private_key_path"], file_key_path,
        "", config_key="private_key_path", file_present=file_has_key,
    )

    return {"config": config, "sources": sources}


# ---------------------------------------------------------------------------
# Relay test-connectivity probe (daemon-side, actionable result)
# ---------------------------------------------------------------------------

# Structured probe statuses. The frontend "Test connection" button (owned by the
# Settings "Relay" section) renders ``status`` + ``message``; ``detail`` carries
# the non-secret technical specifics. An inline PEM is never read or surfaced.
RELAY_PROBE_OK = "ok"
RELAY_PROBE_REACHABLE_UNAUTHED = "reachable_unauthed"
RELAY_PROBE_CA_MISSING = "ca_missing"
RELAY_PROBE_UNREACHABLE = "unreachable"
RELAY_PROBE_AUTH_REJECTED = "auth_rejected"
RELAY_PROBE_DISABLED = "disabled"
RELAY_PROBE_MISCONFIGURED = "misconfigured"

RELAY_PROBE_TIMEOUT_SECONDS = 10.0


def _relay_probe_result(status: str, message: str, detail: str = "") -> dict[str, str]:
    return {
        "status": str(status),
        "message": str(message),
        "detail": str(detail or ""),
    }


def _relay_health_url(relay_url: str) -> str:
    """Derive the ``/health`` URL from a relay base/WS URL.

    ``wss``/``https`` map to ``https`` (the go-live TLS case); ``ws``/``http``
    and bare hosts map to ``http`` (loopback local-dev). Path/query are dropped
    in favour of ``/health``.
    """

    raw = str(relay_url or "").strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    scheme = (parsed.scheme or "").lower()
    http_scheme = "https" if scheme in ("https", "wss") else "http"
    netloc = parsed.netloc or parsed.path
    return urlunparse((http_scheme, netloc, "/health", "", "", ""))


def _import_relay_probe_deps() -> Any:
    """Lazily import the EE connector helpers the probe rides.

    Kept in a tiny indirection so (a) the community core never imports ``ee/``
    at module load and (b) tests can monkeypatch a missing-deps outcome. Raises
    ``ImportError`` (or similar) when the connector package is absent.
    """

    from torque_ee_connector.connector import (  # type: ignore
        _build_relay_ssl_context,
        _is_tls_verify_error,
        build_daemon_ws_url,
    )
    from torque_ee_connector.auth import (  # type: ignore
        load_private_key_pem_from_config,
        make_daemon_attach_headers,
    )

    return types.SimpleNamespace(
        build_relay_ssl_context=_build_relay_ssl_context,
        is_tls_verify_error=_is_tls_verify_error,
        build_daemon_ws_url=build_daemon_ws_url,
        load_private_key_pem_from_config=load_private_key_pem_from_config,
        make_daemon_attach_headers=make_daemon_attach_headers,
    )


async def _relay_probe_health(
    url: str, *, ssl_ctx: Any, timeout: float
) -> dict[str, Any]:
    """GET ``url`` and return ``{"status_code", "json"}``.

    Raises on transport/TLS errors so the orchestrator can classify them.
    """

    import aiohttp  # type: ignore

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        kwargs: dict[str, Any] = {}
        if ssl_ctx is not None:
            kwargs["ssl"] = ssl_ctx
        async with session.get(url, **kwargs) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = None
            return {"status_code": int(resp.status), "json": data}


async def _relay_probe_attach(
    ws_url: str, *, headers: dict[str, str] | None, ssl_ctx: Any, timeout: float
) -> dict[str, Any]:
    """Attempt a signed WS attach; return ``{"status_code": 101}`` on upgrade.

    Closes the socket immediately on success. A 4xx handshake rejection raises
    ``aiohttp.WSServerHandshakeError`` (carrying ``.status``); transport/TLS
    failures raise their native exceptions for the orchestrator to classify.
    """

    import aiohttp  # type: ignore

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        kwargs: dict[str, Any] = {"headers": headers or None}
        if ssl_ctx is not None:
            kwargs["ssl"] = ssl_ctx
        async with session.ws_connect(ws_url, **kwargs) as ws:
            await ws.close()
            return {"status_code": 101}


async def probe_relay_connection(
    settings: Any,
    *,
    data_dir: str = "",
    timeout: float = RELAY_PROBE_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Probe the configured relay and return a structured, actionable result.

    Defensive: never raises to the caller. Bounded by ``timeout`` (seconds). The
    probe RIDES the connector's certifi-backed SSL context, so TLS verification
    is independent of any ambient ``SSL_CERT_FILE`` — a CA-missing failure is
    surfaced as the distinct ``ca_missing`` status with an actionable message
    rather than an opaque "unreachable".

    Two checks, mirroring how the connector actually attaches:

    (a) TLS reachability — HTTPS GET to ``/health`` using the certifi context.
    (b) Auth dry-run — a signed WS attach (only when both ``credential_id`` and
        ``private_key_path`` are configured), expecting a ``101`` upgrade.

    Returns ``{"status", "message", "detail"}`` where ``status`` is one of the
    ``RELAY_PROBE_*`` values.
    """

    try:
        return await asyncio.wait_for(
            _probe_relay_connection_inner(settings, data_dir=data_dir, timeout=timeout),
            timeout=max(1.0, float(timeout) + 2.0),
        )
    except asyncio.TimeoutError:
        return _relay_probe_result(
            RELAY_PROBE_UNREACHABLE,
            "Relay did not respond within the time limit.",
            f"probe timed out after ~{timeout:g}s",
        )
    except Exception as exc:  # pragma: no cover - belt-and-suspenders
        log.exception("Relay connectivity probe failed unexpectedly")
        return _relay_probe_result(
            RELAY_PROBE_UNREACHABLE,
            "Relay probe failed unexpectedly.",
            f"{type(exc).__name__}: {exc}",
        )


async def _probe_relay_connection_inner(
    settings: Any, *, data_dir: str, timeout: float
) -> dict[str, str]:
    resolved = resolve_relay_config(settings, data_dir=data_dir)
    config = dict(resolved.get("config", {}))
    sources = dict(resolved.get("sources", {}))

    def _value(field_key: str) -> str:
        entry = sources.get(field_key) or {}
        return str(entry.get("value", "") or "").strip()

    if not bool(config.get("enabled")):
        return _relay_probe_result(
            RELAY_PROBE_DISABLED,
            "Relay is disabled. Enable it in Settings → Relay before testing connectivity.",
        )

    relay_url = _value("relay_url")
    if not relay_url:
        return _relay_probe_result(
            RELAY_PROBE_MISCONFIGURED,
            "No relay URL is configured. Set the relay URL in Settings → Relay.",
        )

    daemon_id = _value("daemon_id") or "default"
    credential_id = _value("credential_id")
    private_key_path = _value("private_key_path")

    try:
        deps = _import_relay_probe_deps()
    except Exception as exc:
        return _relay_probe_result(
            RELAY_PROBE_MISCONFIGURED,
            "The relay connector package is not installed for this daemon. "
            "Install the EE connector dependencies (ee/python on PYTHONPATH) to test connectivity.",
            f"{type(exc).__name__}: {exc}",
        )

    health_url = _relay_health_url(relay_url)
    is_tls = health_url.lower().startswith("https://")
    host = urlparse(health_url).netloc or relay_url

    ssl_ctx = None
    used_certifi = False
    if is_tls:
        try:
            ssl_ctx, used_certifi = deps.build_relay_ssl_context()
        except Exception as exc:
            return _relay_probe_result(
                RELAY_PROBE_MISCONFIGURED,
                "Could not build the relay TLS context.",
                f"{type(exc).__name__}: {exc}",
            )

    # (a) TLS reachability against /health.
    try:
        health = await _relay_probe_health(health_url, ssl_ctx=ssl_ctx, timeout=timeout)
    except Exception as exc:
        if is_tls and deps.is_tls_verify_error(exc):
            return _relay_probe_result(
                RELAY_PROBE_CA_MISSING,
                "Relay TLS certificate could not be verified. Install the EE connector "
                "dependencies (which bundle certifi), or point SSL_CERT_FILE at a valid CA bundle.",
                f"{type(exc).__name__}: {exc}",
            )
        return _relay_probe_result(
            RELAY_PROBE_UNREACHABLE,
            f"Could not reach the relay at {host}. Check the relay URL and your network connection.",
            f"{type(exc).__name__}: {exc}",
        )

    health_status = int(health.get("status_code", 0) or 0)
    if health_status != 200:
        return _relay_probe_result(
            RELAY_PROBE_UNREACHABLE,
            f"Relay at {host} responded with HTTP {health_status} instead of a healthy 200.",
            f"GET /health -> {health_status}",
        )

    health_json = health.get("json")
    auth_mode = ""
    if isinstance(health_json, dict):
        auth_mode = str(health_json.get("auth_mode", "") or "")
    certifi_note = " (verified with bundled certifi)" if used_certifi else ""

    # (b) Auth dry-run only when both credential_id and private_key_path are set.
    if not (credential_id and private_key_path):
        return _relay_probe_result(
            RELAY_PROBE_REACHABLE_UNAUTHED,
            f"Relay at {host} is reachable{certifi_note}. No daemon credentials are configured, "
            "so authentication was not tested. Set credential_id and private_key_path in "
            "Settings → Relay to verify the signed attach.",
            f"auth_mode={auth_mode}" if auth_mode else "",
        )

    try:
        private_key_pem = deps.load_private_key_pem_from_config(
            {"private_key_path": private_key_path}
        )
    except Exception as exc:
        return _relay_probe_result(
            RELAY_PROBE_MISCONFIGURED,
            "The daemon private key could not be loaded. "
            f"Check the key file at the configured path ({private_key_path}).",
            f"{type(exc).__name__}: {exc}",
        )
    if not private_key_pem:
        return _relay_probe_result(
            RELAY_PROBE_MISCONFIGURED,
            f"The daemon private key file at {private_key_path} is empty or unreadable.",
        )

    try:
        ws_url = deps.build_daemon_ws_url(relay_url, daemon_id)
    except Exception as exc:
        return _relay_probe_result(
            RELAY_PROBE_MISCONFIGURED,
            "Could not derive the relay WebSocket URL from the configured relay URL.",
            f"{type(exc).__name__}: {exc}",
        )

    try:
        headers = deps.make_daemon_attach_headers(
            ws_url=ws_url,
            daemon_id=daemon_id,
            credential_id=credential_id,
            private_key_pem=private_key_pem,
        )
    except Exception as exc:
        return _relay_probe_result(
            RELAY_PROBE_MISCONFIGURED,
            "Could not sign the relay attach request. "
            "Signed attach requires the cryptography dependency.",
            f"{type(exc).__name__}: {exc}",
        )

    ws_is_tls = ws_url.lower().startswith("wss://")
    attach_ssl_ctx = ssl_ctx if ws_is_tls else None
    try:
        await _relay_probe_attach(
            ws_url, headers=headers, ssl_ctx=attach_ssl_ctx, timeout=timeout
        )
    except Exception as exc:
        if ws_is_tls and deps.is_tls_verify_error(exc):
            return _relay_probe_result(
                RELAY_PROBE_CA_MISSING,
                "Relay TLS certificate could not be verified. Install the EE connector "
                "dependencies (which bundle certifi), or point SSL_CERT_FILE at a valid CA bundle.",
                f"{type(exc).__name__}: {exc}",
            )
        status_code = getattr(exc, "status", None)
        if isinstance(status_code, int) and 400 <= status_code < 500:
            return _relay_probe_result(
                RELAY_PROBE_AUTH_REJECTED,
                f"Relay at {host} is reachable but rejected the daemon credentials (HTTP {status_code}). "
                "Check credential_id and that the private key matches the registered credential.",
                f"attach -> {status_code}",
            )
        return _relay_probe_result(
            RELAY_PROBE_UNREACHABLE,
            f"Reached {host} but the WebSocket attach failed. Check the relay URL and network.",
            f"{type(exc).__name__}: {exc}",
        )

    return _relay_probe_result(
        RELAY_PROBE_OK,
        f"Relay at {host} is reachable{certifi_note} and accepted the daemon credentials.",
        f"auth_mode={auth_mode}" if auth_mode else "",
    )


async def mint_relay_device_link(
    runtime: CloudConnectorRuntime | None,
    *,
    label: str = "",
) -> dict[str, Any]:
    """Mint a single-use relay device-link establish code via the connector.

    Delegates to the running enterprise connector's ``mint_client_establish_code``
    (which sends a daemon-mediated mint over the authenticated relay WS and
    returns the raw code + establish URL exactly once).  Defensive: NEVER raises
    and NEVER persists the raw code — returns a structured result dict with
    ``{"ok": bool, ...}``.  Community/disabled builds report a clear, actionable
    error rather than an exception.
    """

    if not runtime or not runtime.enabled:
        return {"ok": False, "error": "relay_disabled", "message": "The relay connector is not enabled."}
    connector = runtime.connector
    if connector is None or not runtime.started:
        return {
            "ok": False,
            "error": "relay_not_started",
            "message": runtime.error or "The relay connector is not running.",
        }
    mint = getattr(connector, "mint_client_establish_code", None)
    if not callable(mint):
        return {
            "ok": False,
            "error": "mint_unsupported",
            "message": "The relay connector does not support device-link minting.",
        }
    try:
        result = mint(label=str(label or ""))
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # pragma: no cover - belt-and-suspenders
        log.exception("Relay device-link mint failed")
        return {"ok": False, "error": "mint_failed", "message": str(exc) or type(exc).__name__}
    if isinstance(result, dict):
        return dict(result)
    return {"ok": False, "error": "mint_invalid_response", "message": "Connector returned an unexpected mint result."}


async def start_cloud_connector(context: CloudConnectorContext) -> CloudConnectorRuntime:
    """Load and start the optional enterprise cloud connector.

    Disabled by default.  When enabled, the module named by
    ``TORQUE_CLOUD_CONNECTOR_MODULE`` (default ``torque_ee_connector``) may expose
    ``create_connector(context)`` returning an object with optional ``start()``,
    ``stop()``, and ``on_direct_message(event)`` methods.  A module-level
    ``start(context)``/``stop()`` shape is also accepted for thin adapters.
    """

    module_name = str(
        getattr(torque_config, "CLOUD_CONNECTOR_MODULE", "torque_ee_connector")
        or "torque_ee_connector"
    ).strip() or "torque_ee_connector"
    # Global Settings (relay_enabled, carried as context.config["enabled"]) is
    # the primary enable switch; the env flag remains the fallback when the
    # caller passes no explicit enabled hint.
    ctx_config = getattr(context, "config", None)
    ctx_config = ctx_config if isinstance(ctx_config, dict) else {}
    enabled = ctx_config.get("enabled")
    if enabled is None:
        enabled = getattr(torque_config, "CLOUD_CONNECTOR_ENABLED", False)
    runtime = CloudConnectorRuntime(
        enabled=bool(enabled),
        module_name=module_name,
    )
    if not runtime.enabled:
        return runtime

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        runtime.error = str(exc) or type(exc).__name__
        if isinstance(exc, ImportError):
            # Routine config/availability mistake (missing PYTHONPATH or an
            # absent optional dep like cryptography), not a crash.  Surface a
            # single actionable line; keep the full traceback at debug only so
            # the daemon log does not read like an unhandled startup failure.
            log.warning(
                "Cloud connector enabled (TORQUE_CLOUD_CONNECTOR_ENABLED) but "
                "module %r is not importable: %s. Ensure PYTHONPATH includes "
                "<repo>/ee/python and required deps (cryptography) are installed "
                "in this interpreter. Continuing WITHOUT the connector.",
                module_name,
                runtime.error,
            )
            log.debug(
                "Cloud connector import traceback for %s",
                module_name,
                exc_info=True,
            )
        else:
            # Unexpected error while importing the module — a real bug worth a
            # full traceback.
            log.exception(
                "Cloud connector enabled but module %s could not be imported",
                module_name,
            )
        return runtime

    try:
        factory = getattr(module, "create_connector", None)
        connector = await _maybe_await(factory(context)) if callable(factory) else module
        runtime.connector = connector
        observer = getattr(connector, "on_direct_message", None)
        if callable(observer):
            runtime.unregister_callbacks.append(
                register_direct_message_observer(observer)
            )
        if connector is module:
            starter = getattr(module, "start", None)
            if callable(starter):
                await _maybe_await(starter(context))
        else:
            starter = getattr(connector, "start", None)
            if callable(starter):
                await _maybe_await(starter())
        runtime.started = True
        log.info("Cloud connector started from %s", module_name)
    except Exception as exc:
        runtime.error = str(exc) or type(exc).__name__
        log.exception("Cloud connector startup failed for %s", module_name)
    return runtime


async def stop_cloud_connector(runtime: CloudConnectorRuntime | None) -> None:
    """Stop and unregister an optional enterprise cloud connector."""

    if not runtime:
        return
    connector = runtime.connector
    try:
        if runtime.started and connector is not None:
            stopper = getattr(connector, "stop", None)
            if callable(stopper):
                await _maybe_await(stopper())
    except Exception:
        log.exception("Cloud connector shutdown failed for %s", runtime.module_name)
    for unregister in list(runtime.unregister_callbacks):
        try:
            unregister()
        except Exception:
            log.exception("Cloud connector observer unregister failed")
    runtime.unregister_callbacks.clear()
    runtime.started = False
