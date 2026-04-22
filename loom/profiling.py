"""Lightweight in-process profiling hooks for Loom perf harness runs.

The production daemon keeps this module dormant unless one of the explicit
profiling flags is enabled.  The perf harness turns it on for a short-lived
standalone daemon and then reads the aggregated metrics through a profile-only
HTTP endpoint.
"""

from __future__ import annotations

import asyncio
import cProfile
import io
import logging
import os
import pstats
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Deque

_TRUE_VALUES = {"1", "true", "yes", "on"}
_PROFILE_ENV_NAMES = (
    "LOOM_PROFILE_ENABLED",
    "LOOM_PERF_PROFILE",
    "LOOM_PERF_HARNESS",
)
_DEFAULT_MAX_SAMPLES = 20_000
_DEFAULT_MAX_EVENTS = 250


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUE_VALUES


def is_enabled() -> bool:
    """Return whether expensive profiling instrumentation should be active."""
    if any(_env_truthy(name) for name in _PROFILE_ENV_NAMES):
        return True
    # LOOM_PROFILE already names the runtime profile in normal operation.  To
    # avoid surprising profile-name users, only boolean-looking values enable
    # instrumentation through this legacy/short flag.
    return str(os.environ.get("LOOM_PROFILE", "")).strip().lower() in _TRUE_VALUES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], pct: float) -> float:
    """Return an interpolated percentile for *values*.

    Kept small and dependency-free so the harness can use the same convention
    as the in-process recorder.
    """
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (float(pct) / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def summarize(values: list[float]) -> dict[str, float | int]:
    """Summarize a numeric sample list with the percentiles used by reports."""
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
        }
    vals = [float(v) for v in values]
    return {
        "count": len(vals),
        "min": min(vals),
        "max": max(vals),
        "mean": sum(vals) / len(vals),
        "p50": percentile(vals, 50),
        "p95": percentile(vals, 95),
        "p99": percentile(vals, 99),
    }


class ProfileRecorder:
    """Bounded sample/counter store used by profile-only endpoints."""

    def __init__(self, *, max_samples: int | None = None,
                 max_events: int = _DEFAULT_MAX_EVENTS):
        self.max_samples = int(max_samples or os.environ.get(
            "LOOM_PROFILE_MAX_SAMPLES", _DEFAULT_MAX_SAMPLES))
        self.max_events = max_events
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.started_at = time.time()
            self.started_at_iso = _now_iso()
            self.counters: dict[str, int] = defaultdict(int)
            self.gauges: dict[str, Any] = {}
            self.samples: dict[str, Deque[float]] = defaultdict(
                lambda: deque(maxlen=self.max_samples))
            self.recent_events: Deque[dict[str, Any]] = deque(
                maxlen=self.max_events)
            self.slow_callbacks: Deque[dict[str, Any]] = deque(maxlen=200)

    def incr(self, name: str, amount: int = 1) -> None:
        if not is_enabled():
            return
        with self._lock:
            self.counters[name] += int(amount)

    def set_gauge(self, name: str, value: Any) -> None:
        if not is_enabled():
            return
        with self._lock:
            self.gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        if not is_enabled():
            return
        with self._lock:
            self.samples[name].append(float(value))

    def observe_ms(self, name: str, seconds: float) -> None:
        self.observe(name, float(seconds) * 1000.0)

    def event(self, kind: str, **payload: Any) -> None:
        if not is_enabled():
            return
        entry = {"timestamp": time.time(), "kind": kind, **payload}
        with self._lock:
            self.recent_events.append(entry)

    def record_slow_callback(self, message: str, level: str = "WARNING") -> None:
        if not is_enabled():
            return
        entry = {
            "timestamp": time.time(),
            "level": level,
            "message": message,
        }
        with self._lock:
            self.slow_callbacks.append(entry)
            self.counters["asyncio_slow_callbacks"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = {
                name: summarize(list(values))
                for name, values in sorted(self.samples.items())
            }
            return {
                "enabled": is_enabled(),
                "started_at": self.started_at_iso,
                "uptime_sec": max(0.0, time.time() - self.started_at),
                "counters": dict(sorted(self.counters.items())),
                "gauges": dict(sorted(self.gauges.items())),
                "samples": samples,
                "slow_callbacks": list(self.slow_callbacks),
                "recent_events": list(self.recent_events),
            }


_RECORDER = ProfileRecorder()
_PROFILER: cProfile.Profile | None = None
_PROFILER_LOCK = threading.RLock()
_LOOP_LAG_TASK: asyncio.Task | None = None
_SLOW_HANDLER_INSTALLED = False


def recorder() -> ProfileRecorder:
    return _RECORDER


def reset() -> None:
    """Clear counters/samples and restart cProfile if it is active."""
    global _PROFILER
    _RECORDER.reset()
    with _PROFILER_LOCK:
        if _PROFILER is not None:
            _PROFILER.disable()
            _PROFILER = cProfile.Profile()
            _PROFILER.enable()


@contextmanager
def timer(name: str):
    """Record a wall-clock duration sample in milliseconds when enabled."""
    if not is_enabled():
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        _RECORDER.observe_ms(name, time.perf_counter() - started)


def start_cprofile() -> None:
    """Start process-wide cProfile collection for a harness run."""
    global _PROFILER
    if not is_enabled():
        return
    with _PROFILER_LOCK:
        if _PROFILER is None:
            _PROFILER = cProfile.Profile()
            _PROFILER.enable()
            _RECORDER.event("cprofile_start")


def cprofile_top(limit: int = 30, sort: str = "cumulative") -> list[dict[str, Any]]:
    """Return top cProfile functions sorted by cumulative or total time."""
    if not is_enabled():
        return []
    with _PROFILER_LOCK:
        if _PROFILER is None:
            return []
        _PROFILER.disable()
        try:
            stats = pstats.Stats(_PROFILER, stream=io.StringIO())
            key_index = 3 if sort == "cumulative" else 2
            rows = sorted(
                stats.stats.items(),
                key=lambda item: item[1][key_index],
                reverse=True,
            )[:max(1, int(limit))]
            result = []
            for (filename, line, func), (primitive, calls, total, cumulative, _callers) in rows:
                result.append({
                    "function": f"{filename}:{line}({func})",
                    "primitive_calls": primitive,
                    "calls": calls,
                    "total_sec": total,
                    "cumulative_sec": cumulative,
                    "per_call_total_sec": total / calls if calls else 0.0,
                    "per_call_cumulative_sec": cumulative / primitive if primitive else 0.0,
                })
            return result
        finally:
            _PROFILER.enable()


def cprofile_text(limit: int = 30, sort: str = "cumulative") -> str:
    """Return pstats text for artifacts/debugging."""
    if not is_enabled():
        return ""
    with _PROFILER_LOCK:
        if _PROFILER is None:
            return ""
        _PROFILER.disable()
        try:
            stream = io.StringIO()
            pstats.Stats(_PROFILER, stream=stream).sort_stats(sort).print_stats(limit)
            return stream.getvalue()
        finally:
            _PROFILER.enable()


class _AsyncioSlowCallbackHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - logging guard
        try:
            message = record.getMessage()
        except Exception:
            return
        if "Executing " in message and " took " in message:
            _RECORDER.record_slow_callback(message, record.levelname)


def _install_slow_callback_capture() -> None:
    global _SLOW_HANDLER_INSTALLED
    if _SLOW_HANDLER_INSTALLED:
        return
    logging.getLogger("asyncio").addHandler(_AsyncioSlowCallbackHandler())
    _SLOW_HANDLER_INSTALLED = True


async def _loop_lag_probe(interval: float) -> None:
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval
    while True:
        await asyncio.sleep(interval)
        now = loop.time()
        lag = max(0.0, now - expected)
        _RECORDER.observe("event_loop_lag_ms", lag * 1000.0)
        expected = now + interval


def configure_asyncio(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Enable asyncio debug, slow-callback capture, cProfile, and lag probe."""
    global _LOOP_LAG_TASK
    if not is_enabled():
        return
    loop = loop or asyncio.get_running_loop()
    loop.set_debug(True)
    slow_ms = float(os.environ.get("LOOM_PROFILE_SLOW_CALLBACK_MS", "50") or 50)
    loop.slow_callback_duration = max(0.001, slow_ms / 1000.0)
    _install_slow_callback_capture()
    start_cprofile()
    if _LOOP_LAG_TASK is None or _LOOP_LAG_TASK.done():
        interval = float(os.environ.get("LOOM_PROFILE_LOOP_LAG_INTERVAL", "0.05") or 0.05)
        _LOOP_LAG_TASK = loop.create_task(_loop_lag_probe(max(0.005, interval)))
    _RECORDER.event(
        "asyncio_debug_enabled",
        slow_callback_duration=loop.slow_callback_duration,
    )
