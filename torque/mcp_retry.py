"""MCP HTTP reliability helpers.

This module stays dependency-light: retry/backoff is built on asyncio.sleep and
HTTP pooling uses the aiohttp dependency already used by Torque's server.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger("torque")

IDEMPOTENCY_ARG = "_torque_idempotency_key"
IDEMPOTENCY_HEADER = "X-Torque-Idempotency-Key"
DEFAULT_MAX_RETRIES = 3
DEFAULT_ATTEMPTS = DEFAULT_MAX_RETRIES + 1
DEFAULT_BACKOFFS = (0.5, 1.5, 3.0)
DEFAULT_HTTP_TIMEOUT = 30

# Agent-local torque_* writes. Read tools and memory reads/lists are omitted.
_TORQUE_WRITE_TOOLS = {
    "torque_task_upload_artifact",
    "torque_done",
    "torque_blocked",
    "torque_error",
    "torque_progress",
    "torque_verify",
    "torque_ready",
    "torque_name",
    "torque_derive",
    "torque_ask",
    "torque_message_user",
    "torque_reply",
    "torque_memory_publish",
    "torque_memory_pin",
    "torque_memory_link",
    "torque_memory_unpin",
}

# Scoped architect_/engineer_ tool suffixes with side effects.
_SCOPED_WRITE_SUFFIXES = {
    "launch_settings",
    "notifications",
    "resume",
    "engineer_hire",
    "engineer_set_specializations",
    "engineer_dismiss",
    "engineer_rehire",
    "engineer_restore",
    "task_create",
    "task_update",
    "task_reassign",
    "task_edit",
    "task_upload_artifact",
    "task_mark_covered",
    "task_verify",
    "task_move",
    "task_dispatch",
    "batch_dispatch",
    "task_resolve",
    "decision_create",
    "decision_update",
    "decision_link",
    "hint_snooze",
    "journal",
    "engineer_message",
    "peer_message",
    "message_user",
    "message_architect",
    "reply",
    "agent_message",
    "ask",
    "note",
    "agent_close",
    "agent_relaunch",
    "merge",
    "rebase",
    "create_pr",
    "worktree_remove",
    "worktree_checkpoint",
    "specialization_save",
    "specialization_delete",
    "behavior_overlay_propose",
    "behavior_overlay_propose_for_engineer",
    "behavior_overlay_propose_for_role",
    "behavior_overlay_request_rollback",
    "behavior_overlay_approve",
    "behavior_overlay_reject",
    "behavior_overlay_rollback",
    "behavior_overlay_rollback_role",
}


_PR_PHASES = frozenset({
    "github_preflight",
    "github_remote",
    "remote_base_sync",
    "push_branch",
    "pr_create",
    "pr_merge",
    "nested_submodule_pr_preflight",
    "nested_submodule_pr_push",
    "nested_submodule_pr_create",
    "nested_submodule_pr_merge",
    "nested_submodule_pr_sync",
    "nested_submodule_gitlink_bump",
})
_PR_NON_RETRYABLE_PHASES = frozenset({
    "github_preflight",
    "nested_submodule_pr_preflight",
    "nested_submodule_gitlink_bump",
})
_PR_TRANSIENT_ERROR_FRAGMENTS = (
    "temporary",
    "transient",
    "timeout",
    "timed out",
    "try again",
    "retry",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection closed",
    "server disconnected",
    "network",
    "dns",
    "could not resolve host",
    "failed to resolve",
    "econnreset",
    "etimedout",
    "econnrefused",
    "eaddrnotavail",
    "rate limit",
    "too many requests",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)
_PR_NON_RETRYABLE_ERROR_FRAGMENTS = (
    "not installed",
    "not executable",
    "authentication failed",
    "not a github repository",
    "requires a github remote",
    "none found",
    "cannot be fast-forwarded",
    "resolve divergence",
    "merge conflict",
    "conflict on github",
    "not mergeable",
    "dirty",
    "would be overwritten",
    "no commits ahead",
    "required",
    "invalid",
)


def is_mcp_pr_phase(phase: str) -> bool:
    """Return true when *phase* is part of Torque's PR merge pipeline."""
    return str(phase or "").strip() in _PR_PHASES


def is_mcp_pr_phase_retryable(phase: str, error: str = "") -> bool:
    """Classify whether a PR-phase tool error should be retried.

    GitHub preflight failures are configuration/authentication problems in
    Torque's flow and must not be retried. Other PR phases are retried only
    when the error text looks transport/transient (network, timeout, rate
    limiting, service unavailable, explicit retry hint, etc.) so deterministic
    merge conflicts or local branch-divergence errors do not get masked.
    """
    phase = str(phase or "").strip()
    if not phase or phase in _PR_NON_RETRYABLE_PHASES:
        return False
    if phase not in _PR_PHASES:
        return False
    lowered = str(error or "").lower()
    if any(
        fragment in lowered
        for fragment in _PR_NON_RETRYABLE_ERROR_FRAGMENTS
    ):
        return False
    return any(fragment in lowered for fragment in _PR_TRANSIENT_ERROR_FRAGMENTS)

_API_READ_COMMANDS = {
    "refresh",
    "get_config",
    "get_group_settings",
    "get_global_settings",
    "get_ai_settings",
    "doctor",
    "get_events",
    "get_cell_events",
    "agent_history_list",
    "agent_history_show",
    "agent_history_messages",
    "worktree_list",
    "worktree_history",
    "worktree_diff",
    "worktree_merge_check",
    "task_chain",
    "discover_pipelines",
    "list_actions",
    "render_action",
    "preview_prompt",
    "list_roles",
    "list_templates",
    "memory_list",
    "memory_read",
    "engineer_journal_read",
    "architect_task_list",
    "architect_mcp_calls",
    "engineer_mcp_calls",
    "mcp_calls",
    "architect_decision_list",
    "pending_hire_list",
    "behavior_overlay_read",
    "behavior_overlay_versions",
    "behavior_overlay_proposals",
    "behavior_overlay_diff",
}

_shared_session: Any = None
_shared_connector: Any = None
_shared_loop: asyncio.AbstractEventLoop | None = None


@dataclass
class RetryExhausted(Exception):
    """Raised when a transient HTTP call exhausts all retry attempts."""

    message: str
    attempts: int
    last_error: str
    transient: bool = True

    def __str__(self) -> str:  # pragma: no cover - dataclass repr is noisy
        return self.message


class TransientHTTPStatus(Exception):
    def __init__(self, status: int, body: bytes | str = b""):
        self.status = int(status or 0)
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        self.body = str(body or "")
        super().__init__(f"HTTP {self.status}: {self.body[:200]}")


def mcp_tool_surface(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name.startswith("architect_"):
        return "architect"
    if name.startswith("engineer_"):
        return "engineer"
    if name.startswith("torque_"):
        return "torque"
    return "mcp"


def normalize_scoped_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    for prefix in ("architect_", "engineer_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def is_mcp_write_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    if not name:
        return False
    if name in _TORQUE_WRITE_TOOLS:
        return True
    return normalize_scoped_tool_name(name) in _SCOPED_WRITE_SUFFIXES


def is_api_write_command(cmd: str) -> bool:
    cmd = str(cmd or "").strip()
    return bool(cmd) and cmd not in _API_READ_COMMANDS


def new_idempotency_key() -> str:
    return uuid.uuid4().hex


def _json_dumps_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _strip_idempotency_from_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _strip_idempotency_from_value(v)
            for k, v in value.items()
            if str(k) not in {IDEMPOTENCY_ARG, "idempotency_key"}
        }
    if isinstance(value, list):
        return [_strip_idempotency_from_value(v) for v in value]
    return value


def mcp_request_hash(body: dict) -> str:
    """Stable hash for a tool call, excluding JSON-RPC id and idempotency key."""
    clone = _strip_idempotency_from_value(dict(body or {}))
    clone.pop("id", None)
    return hashlib.sha256(_json_dumps_canonical(clone).encode("utf-8")).hexdigest()


def api_request_hash(payload: dict) -> str:
    clone = _strip_idempotency_from_value(dict(payload or {}))
    clone.pop("idempotency_key", None)
    return hashlib.sha256(_json_dumps_canonical(clone).encode("utf-8")).hexdigest()


def derive_idempotency_key(base_key: str, payload: Any) -> str:
    """Derive a stable sub-key for an internal write inside one outer call."""
    key = str(base_key or "").strip()
    if not key:
        return ""
    clone = _strip_idempotency_from_value(payload)
    digest = hashlib.sha256(
        (key + "\0" + _json_dumps_canonical(clone)).encode("utf-8")
    ).hexdigest()
    return f"{key}:{digest[:16]}"


def mcp_idempotency_key_from_arguments(arguments: dict | None) -> str:
    if not isinstance(arguments, dict):
        return ""
    return str(
        arguments.get(IDEMPOTENCY_ARG)
        or arguments.get("idempotency_key")
        or ""
    ).strip()


def mcp_idempotency_key_from_body(body: dict, *, header_key: str = "") -> str:
    params = (body or {}).get("params", {})
    args = params.get("arguments", {}) if isinstance(params, dict) else {}
    return mcp_idempotency_key_from_arguments(args) or str(header_key or "").strip()


def strip_mcp_idempotency_args(arguments: dict | None) -> dict:
    if not isinstance(arguments, dict):
        return {}
    cleaned = dict(arguments)
    cleaned.pop(IDEMPOTENCY_ARG, None)
    cleaned.pop("idempotency_key", None)
    return cleaned


def ensure_mcp_payload_idempotency(payload: dict) -> tuple[dict, str, str]:
    """Return a copy of *payload* with a hidden idempotency key for writes.

    The public MCP schema is unchanged: the key is an implementation-private
    argument understood by Torque's own server and stripped before tool dispatch.
    """
    body = dict(payload or {})
    if body.get("method") != "tools/call":
        return body, "", ""
    params = dict(body.get("params", {}) or {})
    tool_name = str(params.get("name", "") or "").strip()
    if not is_mcp_write_tool(tool_name):
        return body, "", tool_name
    args = params.get("arguments", {})
    if not isinstance(args, dict):
        args = {}
    else:
        args = dict(args)
    key = mcp_idempotency_key_from_arguments(args)
    if not key:
        key = new_idempotency_key()
        args[IDEMPOTENCY_ARG] = key
    params["arguments"] = args
    body["params"] = params
    return body, key, tool_name


def ensure_api_payload_idempotency(payload: dict) -> tuple[dict, str, str]:
    body = dict(payload or {})
    cmd = str(body.get("cmd", "") or "").strip()
    if not is_api_write_command(cmd):
        return body, "", cmd
    key = str(body.get("idempotency_key", "") or "").strip()
    if not key:
        key = new_idempotency_key()
        body["idempotency_key"] = key
    return body, key, cmd


def _errno_from_exception(exc: BaseException) -> int | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        err_no = getattr(current, "errno", None)
        if isinstance(err_no, int):
            return err_no
        os_error = getattr(current, "os_error", None)
        if isinstance(os_error, BaseException):
            err_no = getattr(os_error, "errno", None)
            if isinstance(err_no, int):
                return err_no
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


def is_transient_exception(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionResetError, BrokenPipeError)):
        return True
    err_no = _errno_from_exception(exc)
    if err_no in {
        errno.EADDRNOTAVAIL,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EPIPE,
    }:
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) is None:
        text = str(exc).lower()
        if any(fragment in text for fragment in (
            "can't assign requested address",
            "cannot assign requested address",
            "connection reset",
            "urlopen timed out",
            "timed out",
            "server disconnected",
            "connection refused",
        )):
            return True
    # aiohttp classes are optional in unit tests, so duck-type on module names
    # and common base class names rather than importing at module import time.
    name = type(exc).__name__.lower()
    module = type(exc).__module__.lower()
    if "aiohttp" in module and any(token in name for token in (
        "timeout",
        "connector",
        "connection",
        "serverdisconnected",
        "clientoserror",
        "clientpayload",
    )):
        return True
    if isinstance(exc, socket.timeout):
        return True
    return False


def is_transient_http_status(status: int) -> bool:
    return int(status or 0) >= 500


async def get_shared_client_session():
    """Return an event-loop-local aiohttp ClientSession with keep-alive."""
    global _shared_session, _shared_connector, _shared_loop
    import aiohttp

    loop = asyncio.get_running_loop()
    if (
        _shared_session is not None
        and not getattr(_shared_session, "closed", False)
        and _shared_loop is loop
    ):
        return _shared_session

    if _shared_session is not None and not getattr(_shared_session, "closed", False):
        await _shared_session.close()
    connector_kwargs = {
        "limit": 128,
        "limit_per_host": 0,
        "ttl_dns_cache": 300,
        "keepalive_timeout": 30,
        "force_close": False,
    }
    try:
        _shared_connector = aiohttp.TCPConnector(**connector_kwargs)
    except TypeError:  # pragma: no cover - older aiohttp compatibility
        connector_kwargs.pop("ttl_dns_cache", None)
        _shared_connector = aiohttp.TCPConnector(**connector_kwargs)
    _shared_session = aiohttp.ClientSession(connector=_shared_connector)
    _shared_loop = loop
    return _shared_session


async def close_shared_client_session() -> None:
    global _shared_session, _shared_connector, _shared_loop
    if _shared_session is not None and not getattr(_shared_session, "closed", False):
        await _shared_session.close()
    _shared_session = None
    _shared_connector = None
    _shared_loop = None


async def retry_async(
    operation: Callable[[int], Awaitable[Any]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoffs: tuple[float, ...] = DEFAULT_BACKOFFS,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    on_retry: Callable[[int, BaseException], Awaitable[Any] | Any] | None = None,
) -> Any:
    last_error = ""
    attempts = max(1, int(attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            return await operation(attempt)
        except Exception as exc:
            last_error = str(exc) or type(exc).__name__
            transient = isinstance(exc, TransientHTTPStatus) or is_transient_exception(exc)
            if not transient or attempt >= attempts:
                if transient:
                    raise RetryExhausted(
                        f"transient write failed after {attempt} attempts: {last_error}",
                        attempts=attempt,
                        last_error=last_error,
                    ) from exc
                raise
            if on_retry is not None:
                maybe = on_retry(attempt, exc)
                if asyncio.iscoroutine(maybe):
                    await maybe
            delay = backoffs[min(attempt - 1, len(backoffs) - 1)] if backoffs else 0
            if delay > 0:
                await sleep(delay)
    raise RetryExhausted(
        f"transient write failed after {attempts} attempts: {last_error}",
        attempts=attempts,
        last_error=last_error,
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


async def post_json_bytes_with_retry(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    db=None,
    surface: str = "mcp",
    tool_name: str = "",
) -> bytes:
    """POST JSON with pooled aiohttp and retry transient failures/5xx."""
    import aiohttp

    headers = dict(headers or {})
    headers.setdefault("Content-Type", "application/json")
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async def _once(_attempt: int) -> bytes:
        session = await get_shared_client_session()
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=client_timeout,
        ) as response:
            body = await response.read()
            if is_transient_http_status(response.status):
                raise TransientHTTPStatus(response.status, body)
            return body

    async def _on_retry(attempt: int, exc: BaseException) -> None:
        record_mcp_health_event_safe(
            db,
            surface=surface,
            tool_name=tool_name,
            event="retry",
            error=str(exc) or type(exc).__name__,
        )
        log.info(
            "Retrying %s/%s after transient MCP HTTP failure: %s",
            surface,
            tool_name or "?",
            exc,
        )

    return await retry_async(_once, attempts=attempts, on_retry=_on_retry)


def record_mcp_health_event_safe(
    db,
    *,
    surface: str,
    tool_name: str = "",
    event: str,
    error: str = "",
) -> None:
    if db is None:
        return
    try:
        db.record_mcp_health_event(
            surface=surface,
            tool_name=tool_name,
            event=event,
            error=error,
        )
    except Exception:
        log.debug("Failed to record MCP health event", exc_info=True)


def queue_failed_mcp_write(
    *,
    payload: dict,
    caller_id: str,
    idempotency_key: str,
    tool_name: str,
    last_error: str,
    attempts: int,
) -> bool:
    """Persist an exhausted MCP write for next-daemon-boot replay."""
    if not idempotency_key:
        return False
    from .db import TorqueDB
    from .config import DB_FILE

    db = TorqueDB(DB_FILE)
    db.init()
    try:
        db.enqueue_failed_write(
            idempotency_key=idempotency_key,
            endpoint="/mcp",
            method="POST",
            surface=mcp_tool_surface(tool_name),
            tool_name=tool_name,
            caller_id=caller_id,
            payload=payload,
            attempts=attempts,
            last_error=last_error,
        )
        db.record_mcp_health_event(
            surface=mcp_tool_surface(tool_name),
            tool_name=tool_name,
            event="drop",
            error=last_error,
        )
        return True
    except Exception:
        log.exception("Failed to persist queued MCP write %s", idempotency_key)
        return False
    finally:
        db.close()


def torque_mcp_url() -> str:
    import os

    port = str(os.environ.get("TORQUE_PORT", "18932") or "").strip() or "18932"
    return f"http://127.0.0.1:{port}/mcp"


async def replay_failed_writes(db, sender: Callable[[dict], Awaitable[Any]], *, limit: int = 100) -> dict:
    """Replay persisted failed writes using a caller-provided sender.

    The sender receives a failed-write row dict and should return normally when
    the write reached Torque's command surface. Tool-level validation errors count
    as delivered because the durable queue's job is transport recovery, not
    semantic correction.
    """
    summary = {"attempted": 0, "replayed": 0, "failed": 0}
    if db is None:
        return summary
    try:
        writes = db.load_failed_writes(limit=limit)
    except Exception:
        log.exception("Failed to load queued MCP writes")
        return summary
    for write in writes:
        summary["attempted"] += 1
        surface = str(write.get("surface", "") or "mcp")
        tool_name = str(write.get("tool_name", "") or "")
        try:
            await sender(write)
        except Exception as exc:
            summary["failed"] += 1
            error = str(exc) or type(exc).__name__
            try:
                db.mark_failed_write_attempt(write.get("id"), error)
                db.record_mcp_health_event(
                    surface=surface,
                    tool_name=tool_name,
                    event="replay_failed",
                    error=error,
                )
            except Exception:
                log.debug("Failed to update failed-write replay status", exc_info=True)
            continue
        try:
            db.delete_failed_write(write.get("id"))
            db.record_mcp_health_event(
                surface=surface,
                tool_name=tool_name,
                event="replay",
            )
        except Exception:
            log.debug("Failed to delete replayed failed write", exc_info=True)
        summary["replayed"] += 1
    return summary
