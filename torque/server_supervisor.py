"""PTY supervisor session payload and control helpers."""

from __future__ import annotations

import asyncio
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
SUPERVISOR_HEALTH_STATES = {
    "up",
    "degraded",
    "restarting",
    "down",
    "unavailable",
    "na_profile",
}


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


def _maybe_call(obj: Any, name: str, default: Any = None) -> Any:
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn()
    except Exception:
        log.debug("Supervisor health accessor failed: %s", name,
                  exc_info=True)
        return default


def _safe_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _duration_since(started_at: Any, *, now: float) -> float | None:
    started = _safe_float(started_at)
    if started is None:
        return None
    return round(max(0.0, now - started), 1)


def build_supervisor_health_projection(
    bridge: Any,
    *,
    profile_skip_pty: bool = False,
    now: float | None = None,
) -> dict:
    """Return daemon-side PTY supervisor health for the runtime snapshot.

    This is deliberately synchronous and duck-typed so it can be called from
    the hot /app state payload builder. Potentially blocking supervisor ops
    (metrics wire reads) are cached by the adapter/watchdog and only the cache
    is read here.
    """
    now = float(now if now is not None else time.time())
    if profile_skip_pty:
        return {
            "state": "na_profile",
            "supervisor_pid": None,
            "uptime": None,
            "connected": False,
            "last_op_latency_ms": None,
            "last_reconnect_at": None,
            "reconnect_count": 0,
            "session_count": 0,
            "metrics": {},
            "time_since_last_successful_op": None,
        }

    connected_fn = getattr(bridge, "supervisor_connected", None)
    if not callable(connected_fn):
        return {
            "state": "unavailable",
            "supervisor_pid": None,
            "uptime": None,
            "connected": False,
            "last_op_latency_ms": None,
            "last_reconnect_at": None,
            "reconnect_count": 0,
            "session_count": 0,
            "metrics": {},
            "time_since_last_successful_op": None,
        }

    connected = bool(_maybe_call(bridge, "supervisor_connected", False))
    watchdog = _maybe_call(bridge, "supervisor_watchdog_status", {}) or {}
    override_state = str((watchdog or {}).get("state") or "").strip()
    if override_state in {"restarting", "down", "unavailable"}:
        state = override_state
    else:
        state = "up" if connected else "degraded"

    metrics = _maybe_call(bridge, "supervisor_metrics_snapshot", {}) or {}
    session_count = _safe_optional_int(metrics.get("sessions_current"))
    if session_count is None:
        session_count = _safe_optional_int(
            _maybe_call(bridge, "supervisor_session_count", 0)) or 0

    last_success = _maybe_call(bridge, "supervisor_last_successful_op_at", None)
    latency = _maybe_call(bridge, "supervisor_last_latency_ms", None)
    try:
        latency = None if latency is None else round(float(latency), 1)
    except (TypeError, ValueError):
        latency = None

    return {
        "state": state,
        "supervisor_pid": _safe_optional_int(
            _maybe_call(bridge, "supervisor_pid", None)),
        "uptime": _duration_since(
            _maybe_call(bridge, "supervisor_started_at", None),
            now=now,
        ),
        "connected": connected,
        "last_op_latency_ms": latency,
        "last_reconnect_at": _maybe_call(
            bridge, "supervisor_last_reconnect_at", None),
        "reconnect_count": int(
            _maybe_call(bridge, "supervisor_reconnect_count", 0) or 0),
        "session_count": int(session_count),
        "metrics": dict(metrics or {}),
        "time_since_last_successful_op": _duration_since(
            last_success,
            now=now,
        ),
    }


def supervisor_health_fingerprint(projection: dict | None) -> tuple:
    """Stable key for deciding whether to emit a supervisor-health delta.

    Continuously increasing durations (uptime/time_since_last_successful_op)
    are intentionally excluded so the watchdog does not create an idle delta
    stream just because time passed.
    """
    payload = dict(projection or {})
    payload.pop("uptime", None)
    payload.pop("time_since_last_successful_op", None)
    metrics = payload.get("metrics") or {}
    if isinstance(metrics, dict):
        ops_total = dict(metrics.get("ops_total") or {})
        # Polling the metrics op increments its own counter. Ignore that
        # self-observation in the change key so an otherwise idle supervisor
        # does not emit a runtime delta every watchdog tick.
        ops_total.pop("metrics", None)
        payload["metrics"] = (
            tuple(sorted(ops_total.items())),
            tuple(sorted((metrics.get("errors_total") or {}).items())),
            int(metrics.get("bytes_written", 0) or 0),
            int(metrics.get("bytes_read", 0) or 0),
            int(metrics.get("sessions_current", 0) or 0),
            int(metrics.get("sessions_peak", 0) or 0),
            int(metrics.get("sessions_created_total", 0) or 0),
            int(metrics.get("read_loop_failures", 0) or 0),
            int(metrics.get("write_deadline_hits", 0) or 0),
        )
    return tuple(sorted(payload.items()))


class SupervisorLivenessWatchdog:
    """Daemon-side bounded supervisor liveness/respawn guard.

    The watchdog never kills a healthy supervisor. It only calls
    ``ensure_running`` after the process is already judged dead (pid not alive
    or repeated reconnect failures). Failed respawns are bounded by a retry
    window and circuit breaker to avoid infinite respawn storms.
    """

    def __init__(
        self,
        *,
        bridge: Any,
        data_dir: Any,
        ensure_running: Callable[..., Any],
        pid_alive: Callable[[int], bool],
        publish_state: Callable[[], Any] | None = None,
        emit_event: Callable[[str, dict], Any] | None = None,
        interval_seconds: float = 5.0,
        reconnect_failure_threshold: int = 3,
        max_retries: int = 3,
        retry_window_seconds: float = 60.0,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        time_func: Callable[[], float] = time.time,
        monotonic_func: Callable[[], float] = time.monotonic,
    ):
        self.bridge = bridge
        self.data_dir = data_dir
        self.ensure_running = ensure_running
        self.pid_alive = pid_alive
        self.publish_state = publish_state
        self.emit_event = emit_event
        self.interval_seconds = max(0.1, float(interval_seconds or 5.0))
        self.reconnect_failure_threshold = max(
            1, int(reconnect_failure_threshold or 1))
        self.max_retries = max(1, int(max_retries or 1))
        self.retry_window_seconds = max(1.0, float(retry_window_seconds or 1.0))
        self.base_backoff_seconds = max(0.0, float(base_backoff_seconds or 0.0))
        self.max_backoff_seconds = max(
            self.base_backoff_seconds,
            float(max_backoff_seconds or self.base_backoff_seconds),
        )
        self.time_func = time_func
        self.monotonic_func = monotonic_func
        self._task: asyncio.Task | None = None
        self._closed = False
        self._failed_attempts: list[float] = []
        self._next_attempt_at = 0.0
        self._circuit_open = False
        self._down_emitted_for_open_circuit = False
        self._lost_marked = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._closed = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        while not self._closed:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Supervisor liveness watchdog tick failed")
            await asyncio.sleep(self.interval_seconds)

    async def _maybe_await(self, result: Any) -> None:
        if asyncio.iscoroutine(result):
            await result

    async def _publish(self) -> None:
        if self.publish_state:
            await self._maybe_await(self.publish_state())

    async def _emit_event(self, kind: str, detail: dict) -> None:
        if self.emit_event:
            await self._maybe_await(self.emit_event(kind, detail))

    async def _emit_down_once(self, detail: dict) -> None:
        if self._down_emitted_for_open_circuit:
            return
        await self._emit_event("down", detail)
        self._down_emitted_for_open_circuit = True

    def _set_status(self, **status: Any) -> None:
        setter = getattr(self.bridge, "set_supervisor_watchdog_status", None)
        if callable(setter):
            setter(status)

    def _connected(self) -> bool:
        return bool(_maybe_call(self.bridge, "supervisor_connected", False))

    def _pid_dead(self) -> bool:
        pid = _safe_optional_int(_maybe_call(self.bridge, "supervisor_pid", None))
        if not pid:
            return False
        try:
            return not bool(self.pid_alive(pid))
        except Exception:
            log.debug("Supervisor pid_alive probe failed", exc_info=True)
            return False

    def _reconnect_failures_exceeded(self) -> bool:
        failures = int(_maybe_call(
            self.bridge, "supervisor_reconnect_failures", 0) or 0)
        return failures >= self.reconnect_failure_threshold

    def _watchdog_pause_deadline(self) -> float | None:
        status = _maybe_call(
            self.bridge, "supervisor_watchdog_status", {}) or {}
        if not bool(status.get("watchdog_paused")):
            return None
        deadline = _safe_float(status.get("watchdog_pause_until"))
        if deadline is None:
            deadline = _safe_float(status.get("restart_deadline_at"))
        # A pause without a valid deadline must not permanently disable the
        # watchdog; treat it as already expired so normal liveness handling
        # resumes on this tick.
        return deadline if deadline is not None else 0.0

    def _prune_attempts(self, now_mono: float) -> None:
        cutoff = now_mono - self.retry_window_seconds
        self._failed_attempts = [
            ts for ts in self._failed_attempts if ts >= cutoff
        ]

    def _open_circuit(self, *, error: str = "") -> None:
        self._circuit_open = True
        self._set_status(
            state="down",
            circuit_open=True,
            failed_respawns=len(self._failed_attempts),
            max_retries=self.max_retries,
            last_error=error,
            updated_at=self.time_func(),
        )

    async def _mark_sessions_lost_once(self) -> int:
        if self._lost_marked:
            return 0
        self._lost_marked = True
        marker = getattr(self.bridge, "mark_supervisor_lost", None)
        if not callable(marker):
            return 0
        try:
            lost = marker(reason="supervisor_lost")
            if asyncio.iscoroutine(lost):
                lost = await lost
            lost = int(lost or 0)
        except Exception:
            log.exception("Failed to mark supervisor-lost sessions")
            lost = 0
        if lost:
            await self._emit_event("supervisor_lost", {"lost_sessions": lost})
        return lost

    async def _refresh_metrics(self) -> None:
        metrics = getattr(self.bridge, "supervisor_metrics", None)
        if callable(metrics):
            try:
                result = metrics()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.debug("Supervisor metrics refresh failed", exc_info=True)

    async def check_once(self) -> None:
        if not callable(getattr(self.bridge, "supervisor_connected", None)):
            return
        connected = self._connected()
        pause_deadline = self._watchdog_pause_deadline()
        if pause_deadline is not None:
            now = self.time_func()
            if pause_deadline > now:
                if connected:
                    self._failed_attempts.clear()
                    self._next_attempt_at = 0.0
                    self._circuit_open = False
                    self._down_emitted_for_open_circuit = False
                    self._lost_marked = False
                    await self._refresh_metrics()
                await self._publish()
                return
            self._set_status(
                state="",
                restart_deadline_expired_at=now,
                updated_at=now,
            )

        if connected:
            self._failed_attempts.clear()
            self._next_attempt_at = 0.0
            self._circuit_open = False
            self._down_emitted_for_open_circuit = False
            self._lost_marked = False
            self._set_status(state="", updated_at=self.time_func())
            await self._refresh_metrics()
            await self._publish()
            return

        dead = self._pid_dead() or self._reconnect_failures_exceeded()
        if not dead:
            await self._publish()
            return

        await self._mark_sessions_lost_once()
        now_mono = self.monotonic_func()
        self._prune_attempts(now_mono)
        if self._circuit_open or len(self._failed_attempts) >= self.max_retries:
            self._open_circuit()
            await self._emit_down_once({
                "failed_respawns": len(self._failed_attempts),
                "max_retries": self.max_retries,
            })
            await self._publish()
            return
        if now_mono < self._next_attempt_at:
            self._set_status(
                state="restarting",
                next_attempt_at=self.time_func()
                + max(0.0, self._next_attempt_at - now_mono),
                failed_respawns=len(self._failed_attempts),
                max_retries=self.max_retries,
                updated_at=self.time_func(),
            )
            await self._publish()
            return

        self._set_status(
            state="restarting",
            failed_respawns=len(self._failed_attempts),
            max_retries=self.max_retries,
            updated_at=self.time_func(),
        )
        await self._publish()
        try:
            await asyncio.to_thread(self.ensure_running, self.data_dir)
        except Exception as exc:
            error = str(exc)
            self._failed_attempts.append(now_mono)
            self._prune_attempts(now_mono)
            if len(self._failed_attempts) >= self.max_retries:
                self._open_circuit(error=error)
                await self._emit_down_once({
                    "failed_respawns": len(self._failed_attempts),
                    "max_retries": self.max_retries,
                    "error": error,
                })
            else:
                backoff = min(
                    self.max_backoff_seconds,
                    self.base_backoff_seconds
                    * (2 ** max(0, len(self._failed_attempts) - 1)),
                )
                self._next_attempt_at = now_mono + backoff
                self._set_status(
                    state="restarting",
                    failed_respawns=len(self._failed_attempts),
                    max_retries=self.max_retries,
                    next_attempt_at=self.time_func() + backoff,
                    last_error=error,
                    updated_at=self.time_func(),
                )
                await self._emit_event("respawn_failed", {
                    "failed_respawns": len(self._failed_attempts),
                    "max_retries": self.max_retries,
                    "backoff_seconds": backoff,
                    "error": error,
                })
            await self._publish()
            return

        self._failed_attempts.clear()
        self._next_attempt_at = 0.0
        self._set_status(
            state="restarting",
            failed_respawns=0,
            max_retries=self.max_retries,
            last_respawn_at=self.time_func(),
            updated_at=self.time_func(),
        )
        await self._emit_event("respawned", {})
        await self._publish()


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


async def build_supervisor_restart_payload(
    bridge: Any,
    state: Any,
    runtime_payload_func: Callable[..., dict] | None,
    *,
    timeout: float = 10.0,
    data_dir: Any = None,
    ensure_running: Callable[..., Any] | None = None,
) -> dict:
    """Request a safe in-place supervisor restart.

    Non-supervisor/profile modes return a stable no-op/unavailable payload so
    the frontend can call this command without special-casing profile mode.
    """
    runtime = _runtime_payload(runtime_payload_func, bridge, state)
    restart = getattr(bridge, "restart_supervisor", None)
    if not callable(restart):
        return {
            "type": "supervisor_restart",
            "ok": True,
            "available": False,
            "message": _UNAVAILABLE_MESSAGE,
            "runtime": runtime,
        }
    try:
        result = restart(
            timeout=timeout,
            data_dir=data_dir,
            ensure_running=ensure_running,
        )
        if asyncio.iscoroutine(result):
            result = await result
    except SupervisorUnavailable as exc:
        log.warning("PTY supervisor restart unavailable: %s", exc)
        return {
            "type": "supervisor_restart",
            "ok": False,
            "available": False,
            "message": "PTY supervisor restart did not complete.",
            "error": str(exc),
            "runtime": runtime,
        }
    except Exception as exc:
        log.exception("PTY supervisor restart failed")
        return {
            "type": "supervisor_restart",
            "ok": False,
            "available": True,
            "message": "PTY supervisor restart failed.",
            "error": str(exc),
            "runtime": runtime,
        }

    return {
        "type": "supervisor_restart",
        "ok": True,
        "available": True,
        "message": "PTY supervisor restarted in place.",
        "restart": dict(result or {}),
        "runtime": _runtime_payload(runtime_payload_func, bridge, state),
    }
