"""Read-only PTY supervisor session payload helpers."""

from __future__ import annotations

import logging
import shlex
import time
from typing import Any, Callable

from .pty_supervisor_client import SupervisorUnavailable

log = logging.getLogger("torque.server_supervisor")

SUPERVISOR_MISSING_FIELDS = ["started_at", "exit_status", "input_bytes"]
_UNAVAILABLE_MESSAGE = (
    "PTY supervisor is only available in standalone embedded-terminal mode."
)


def _runtime_payload(runtime_payload_func: Callable[..., dict] | None,
                     bridge: Any, state: Any) -> dict:
    if runtime_payload_func is None:
        return {}
    try:
        payload = runtime_payload_func(bridge=bridge, state=state)
    except TypeError:
        payload = runtime_payload_func()
    return payload or {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_shell_argv(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value is None or value == "":
        return []
    return [str(value)]


def _join_argv(argv: list[str]) -> str:
    if not argv:
        return ""
    try:
        return shlex.join(argv)
    except Exception:
        return " ".join(shlex.quote(str(item)) for item in argv)


def _owner_payload(cell: Any) -> dict | None:
    if cell is None:
        return None
    return {
        "id": str(getattr(cell, "id", "") or ""),
        "name": str(getattr(cell, "name", "") or ""),
        "group": str(getattr(cell, "group", "") or ""),
        "cell_type": str(getattr(cell, "cell_type", "") or ""),
        "kind": str(getattr(cell, "kind", "") or ""),
        "status": str(getattr(cell, "status", "") or ""),
    }


def normalize_supervisor_session(info: dict, state: Any) -> dict:
    """Return a UI-safe supervisor session row enriched from MatrixState.

    The supervisor list protocol intentionally omits lifecycle/process details
    such as start time and exit status. Keep those as explicit ``None``/
    ``missing_fields`` markers rather than inferring or persisting anything.
    """
    raw = info or {}
    session_id = str(raw.get("session_id") or "")
    cell_id = str(raw.get("cell_id") or "")
    agents = getattr(state, "agents", {}) or {}
    cell = agents.get(cell_id) if cell_id else None
    shell_argv = _normalize_shell_argv(raw.get("shell_argv"))
    shell_command = _join_argv(shell_argv)
    cell_command = str(getattr(cell, "command", "") or "").strip() if cell else ""
    cwd = str(raw.get("cwd") or "")
    current_path = str(getattr(cell, "current_path", "") or "").strip() if cell else ""

    return {
        "session_id": session_id,
        "cell_id": cell_id,
        "pid": _safe_int(raw.get("pid")),
        "alive": bool(raw.get("alive")),
        "cols": _safe_int(raw.get("cols")),
        "rows": _safe_int(raw.get("rows")),
        "total_bytes": _safe_int(raw.get("total_bytes")),
        "cwd": cwd,
        "current_path": current_path or cwd,
        "shell_argv": shell_argv,
        "display_command": cell_command or shell_command,
        "bootstrap_dir": str(raw.get("bootstrap_dir") or ""),
        "owner": _owner_payload(cell),
        "orphan": cell is None or str(getattr(cell, "session_id", "") or "") != session_id,
        "started_at": None,
        "missing_fields": list(SUPERVISOR_MISSING_FIELDS),
    }


def _unavailable_payload(runtime: dict, message: str, *, error: str = "") -> dict:
    payload = {
        "type": "supervisor_sessions",
        "available": False,
        "mode": runtime.get("mode") or "",
        "terminal_backend": runtime.get("terminal_backend") or "",
        "refreshed_at": time.time(),
        "sessions": [],
        "message": message,
        "missing_fields": list(SUPERVISOR_MISSING_FIELDS),
    }
    if error:
        payload["error"] = error
    return payload


async def build_supervisor_sessions_payload(
    bridge: Any,
    state: Any,
    runtime_payload_func: Callable[..., dict] | None,
) -> dict:
    """Build the WebSocket response for ``supervisor_sessions_list``.

    This is deliberately duck-typed: only ``SupervisedPtyAdapter`` exposes
    ``list_supervisor_sessions``. Other backends return a non-error unavailable
    response so the diagnostic panel can render a stable empty state.
    """
    runtime = _runtime_payload(runtime_payload_func, bridge, state)
    if not hasattr(bridge, "list_supervisor_sessions"):
        return _unavailable_payload(runtime, _UNAVAILABLE_MESSAGE)

    try:
        sessions = await bridge.list_supervisor_sessions()
    except SupervisorUnavailable as exc:
        log.warning("PTY supervisor list unavailable: %s", exc)
        return _unavailable_payload(
            runtime,
            "PTY supervisor is unavailable; session list could not be read.",
            error=str(exc),
        )
    except Exception as exc:  # pragma: no cover - log path, payload covered.
        log.exception("PTY supervisor list failed")
        return _unavailable_payload(
            runtime,
            "PTY supervisor session list failed.",
            error=str(exc),
        )

    return {
        "type": "supervisor_sessions",
        "available": True,
        "mode": runtime.get("mode") or "",
        "terminal_backend": runtime.get("terminal_backend") or "",
        "refreshed_at": time.time(),
        "sessions": [
            normalize_supervisor_session(info, state)
            for info in list(sessions or [])
        ],
        "missing_fields": list(SUPERVISOR_MISSING_FIELDS),
    }
