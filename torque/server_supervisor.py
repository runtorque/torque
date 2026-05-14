"""PTY supervisor session payload and control helpers."""

from __future__ import annotations

import logging
import math
import shlex
import time
from typing import Any, Callable

from .pty_supervisor_client import SupervisorUnavailable

log = logging.getLogger("torque.server_supervisor")

SUPERVISOR_MISSING_FIELDS = ["exit_status", "input_bytes"]
_UNAVAILABLE_MESSAGE = (
    "PTY supervisor is only available in standalone embedded-terminal mode."
)
_SUPERVISOR_ROW_ID = "__supervisor__"


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


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


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


def _row_missing_fields(started_at: float | None) -> list[str]:
    fields = list(SUPERVISOR_MISSING_FIELDS)
    if started_at is None:
        return ["started_at", *fields]
    return fields


def normalize_supervisor_session(info: dict, state: Any) -> dict:
    """Return a UI-safe supervisor session row enriched from MatrixState."""
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
    started_at = _safe_float(raw.get("started_at"))

    return {
        "row_type": "session",
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
        "started_at": started_at,
        "terminable": bool(session_id),
        "missing_fields": _row_missing_fields(started_at),
    }


def normalize_supervisor_process(info: dict | None) -> dict | None:
    """Return the synthetic top row for the supervisor process itself."""
    raw = info or {}
    pid = _safe_int(raw.get("pid"))
    started_at = _safe_float(raw.get("started_at"))
    if not pid and started_at is None:
        return None
    return {
        "row_type": "supervisor",
        "session_id": _SUPERVISOR_ROW_ID,
        "cell_id": "",
        "pid": pid,
        "alive": True,
        "cols": 0,
        "rows": 0,
        "total_bytes": 0,
        "cwd": "",
        "current_path": "",
        "shell_argv": [],
        "display_command": "PTY supervisor",
        "bootstrap_dir": "",
        "owner": None,
        "orphan": False,
        "started_at": started_at,
        "terminable": False,
        "missing_fields": _row_missing_fields(started_at),
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


async def _read_supervisor_state(bridge: Any) -> tuple[list, dict | None]:
    if hasattr(bridge, "list_supervisor_state"):
        snapshot = await bridge.list_supervisor_state()
    else:
        snapshot = await bridge.list_supervisor_sessions()
    if isinstance(snapshot, dict):
        return list(snapshot.get("sessions") or []), snapshot.get("supervisor") or None
    return list(snapshot or []), None


async def build_supervisor_sessions_payload(
    bridge: Any,
    state: Any,
    runtime_payload_func: Callable[..., dict] | None,
) -> dict:
    """Build the WebSocket response for ``supervisor_sessions_list``.

    This is deliberately duck-typed: only ``SupervisedPtyAdapter`` exposes
    supervisor list/control helpers. Other backends return a non-error
    unavailable response so the diagnostic panel can render a stable empty
    state.
    """
    runtime = _runtime_payload(runtime_payload_func, bridge, state)
    if not (hasattr(bridge, "list_supervisor_state")
            or hasattr(bridge, "list_supervisor_sessions")):
        return _unavailable_payload(runtime, _UNAVAILABLE_MESSAGE)

    try:
        sessions, supervisor_info = await _read_supervisor_state(bridge)
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

    rows = []
    supervisor_row = normalize_supervisor_process(supervisor_info)
    if supervisor_row:
        rows.append(supervisor_row)
    rows.extend(normalize_supervisor_session(info, state)
                for info in list(sessions or []))

    return {
        "type": "supervisor_sessions",
        "available": True,
        "mode": runtime.get("mode") or "",
        "terminal_backend": runtime.get("terminal_backend") or "",
        "refreshed_at": time.time(),
        "sessions": rows,
        "missing_fields": list(SUPERVISOR_MISSING_FIELDS),
    }


async def build_supervisor_terminate_payload(
    bridge: Any,
    state: Any,
    runtime_payload_func: Callable[..., dict] | None,
    session_id: str,
) -> dict:
    """Terminate a supervisor PTY session and return a refreshed list payload."""
    runtime = _runtime_payload(runtime_payload_func, bridge, state)
    session_id = str(session_id or "").strip()
    if not session_id or session_id == _SUPERVISOR_ROW_ID:
        payload = _unavailable_payload(
            runtime,
            "A valid supervisor session id is required for termination.",
        )
        payload["terminate_session_id"] = session_id
        return payload
    if not hasattr(bridge, "terminate_supervisor_session"):
        payload = _unavailable_payload(runtime, _UNAVAILABLE_MESSAGE)
        payload["terminate_session_id"] = session_id
        return payload

    try:
        await bridge.terminate_supervisor_session(session_id)
    except SupervisorUnavailable as exc:
        log.warning("PTY supervisor terminate unavailable: %s", exc)
        payload = _unavailable_payload(
            runtime,
            "PTY supervisor is unavailable; session could not be terminated.",
            error=str(exc),
        )
        payload["terminate_session_id"] = session_id
        return payload
    except Exception as exc:
        log.exception("PTY supervisor terminate failed for %s", session_id)
        payload = _unavailable_payload(
            runtime,
            "PTY supervisor session terminate failed.",
            error=str(exc),
        )
        payload["terminate_session_id"] = session_id
        return payload

    payload = await build_supervisor_sessions_payload(
        bridge, state, runtime_payload_func)
    payload["terminate_session_id"] = session_id
    payload["terminated_session_id"] = session_id
    payload.setdefault("message", "Supervisor session terminated.")
    return payload
