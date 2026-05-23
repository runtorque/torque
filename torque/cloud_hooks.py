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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

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
