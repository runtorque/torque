"""Low-overhead daemon metrics collection and aggregation.

The collector is intentionally tiny on hot paths: callers check ``enabled`` and
then bump in-memory counters only.  Aggregation, process gauges, websocket
emission, and SQLite rollup writes happen from the background daemon task.
"""

from __future__ import annotations

import asyncio
import math
import os
import platform
import resource
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

METRICS_SCHEMA_VERSION = 1
METRICS_TICK_INTERVAL_SECONDS = 2.0
METRICS_TICK_INTERVAL_CAP_SECONDS = 2.0
METRICS_ROLLUP_RESOLUTION_SECONDS = 60
METRICS_RETENTION_SECONDS = 31 * 86400
FRONTEND_RENDER_WINDOW_SECONDS = 10.0
FRONTEND_RENDER_STALENESS_SECONDS = 6.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] + ((ordered[hi] - ordered[lo]) * frac)


def _rss_mb() -> float:
    """Return best-effort process RSS in MB without extra dependencies."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.  Other Unix platforms vary, but
    # KiB is the safest common fallback.
    if platform.system() == "Darwin":
        return float(usage) / (1024.0 * 1024.0)
    return float(usage) / 1024.0


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class _IntervalCounters:
    ws_deltas: int = 0
    ws_bytes: int = 0
    db_writes: int = 0
    dispatches: int = 0
    ws_subscribers: int = 0
    db_latencies_ms: list[float] = field(default_factory=list)
    event_loop_lag_ms: list[float] = field(default_factory=list)
    task_health_recomputes: int = 0
    task_health_duration_ms: list[float] = field(default_factory=list)
    task_health_changed_tasks: int = 0
    task_health_active_tasks: int = 0
    task_health_target_tasks: int = 0
    task_health_modes: dict[str, int] = field(default_factory=dict)


class MetricsCollector:
    """In-memory metrics collector with branch-only disabled cost."""

    def __init__(self, *, enabled: bool = True):
        self.enabled = bool(enabled)
        self._interval = _IntervalCounters()
        self._last_aggregate_at = time.time()
        self._rolling_ticks: deque[dict] = deque()
        self._rolling_lag_samples: deque[tuple[float, float]] = deque()
        self._inline_overhead_ns = 0
        self._inline_calls = 0
        self._last_process_sample: tuple[float, float] | None = None
        self._frontend_render_samples: deque[dict] = deque(maxlen=500)
        self.last_agg_tick_ms = 0.0

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.enabled == enabled:
            return
        self.enabled = enabled
        if not enabled:
            self.reset_interval()

    def reset_interval(self) -> None:
        self._interval = _IntervalCounters()
        self._inline_overhead_ns = 0
        self._inline_calls = 0
        self._last_aggregate_at = time.time()

    def _add_inline_overhead(self, started_ns: int) -> None:
        self._inline_overhead_ns += max(0, time.perf_counter_ns() - started_ns)
        self._inline_calls += 1

    def record_emit(self, op: str, payload: dict | None = None) -> None:
        """Observe a state delta append.  No I/O; in-memory counters only."""
        started = time.perf_counter_ns()
        if op == "event_append" and (payload or {}).get("kind") == "task_dispatched":
            self._interval.dispatches += 1
        self._add_inline_overhead(started)

    def record_ws_delta(
        self,
        *,
        op_count: int,
        payload_bytes: int,
        subscribers: int,
    ) -> None:
        started = time.perf_counter_ns()
        self._interval.ws_deltas += max(0, int(op_count or 0))
        self._interval.ws_bytes += max(0, int(payload_bytes or 0))
        self._interval.ws_subscribers = max(0, int(subscribers or 0))
        self._add_inline_overhead(started)

    def record_db_write(self, *, latency_ms: float) -> None:
        started = time.perf_counter_ns()
        self._interval.db_writes += 1
        self._interval.db_latencies_ms.append(max(0.0, float(latency_ms or 0.0)))
        self._add_inline_overhead(started)

    def record_event_loop_lag(self, lag_ms: float) -> None:
        if not self.enabled:
            return
        self._interval.event_loop_lag_ms.append(max(0.0, float(lag_ms or 0.0)))

    def record_task_health_recompute(
            self,
            *,
            mode: str,
            active_count: int,
            target_count: int,
            changed_count: int,
            duration_ms: float,
    ) -> None:
        """Observe advisory task-health recompute cost. No I/O."""
        if not self.enabled:
            return
        started = time.perf_counter_ns()
        interval = self._interval
        interval.task_health_recomputes += 1
        interval.task_health_duration_ms.append(max(0.0, float(duration_ms or 0.0)))
        interval.task_health_changed_tasks += max(0, int(changed_count or 0))
        interval.task_health_active_tasks = max(0, int(active_count or 0))
        interval.task_health_target_tasks += max(0, int(target_count or 0))
        mode_key = str(mode or "unknown")
        interval.task_health_modes[mode_key] = (
            interval.task_health_modes.get(mode_key, 0) + 1
        )
        self._add_inline_overhead(started)

    def record_frontend_render(self, payload: dict | None = None) -> None:
        """Best-effort ingest for Panelsmith's frontend render reporter.

        The published v1 tick/history schema does not surface these samples
        yet; keeping them in memory lets the command be accepted without
        changing the wire contract.
        """
        if not self.enabled:
            return
        started = time.perf_counter_ns()
        payload = payload or {}
        sample = {
            "timestamp": time.time(),
            "surface": str(payload.get("surface", "") or ""),
            "count": int(
                payload.get("count", payload.get("frames", 1)) or 1
            ),
            "duration_ms": _safe_float(
                payload.get(
                    "duration_ms",
                    payload.get(
                        "render_ms",
                        payload.get("render_ms_p95", payload.get("ms", 0.0)),
                    ),
                ),
                0.0,
            ),
            "render_per_s": (
                _safe_float(payload.get("render_per_s"), 0.0)
                if payload.get("render_per_s") is not None else None
            ),
        }
        self._frontend_render_samples.append(sample)
        self._add_inline_overhead(started)

    def _frontend_payload(self, now: float) -> dict | None:
        if not self._frontend_render_samples:
            return None
        latest_ts = float(self._frontend_render_samples[-1].get("timestamp", 0.0))
        if latest_ts <= 0 or (now - latest_ts) > FRONTEND_RENDER_STALENESS_SECONDS:
            return None
        cutoff = now - FRONTEND_RENDER_WINDOW_SECONDS
        samples = [
            sample for sample in self._frontend_render_samples
            if float(sample.get("timestamp", 0.0) or 0.0) >= cutoff
        ]
        if not samples:
            return None
        explicit_rates = [
            float(sample["render_per_s"])
            for sample in samples
            if sample.get("render_per_s") is not None
        ]
        if explicit_rates:
            render_per_s = sum(explicit_rates) / len(explicit_rates)
        else:
            first_ts = min(float(sample.get("timestamp", now) or now)
                           for sample in samples)
            elapsed = max(1.0, min(FRONTEND_RENDER_WINDOW_SECONDS, now - first_ts))
            render_per_s = (
                sum(max(0, int(sample.get("count", 0) or 0))
                    for sample in samples)
                / elapsed
            )
        durations = [
            float(sample.get("duration_ms", 0.0) or 0.0)
            for sample in samples
            if float(sample.get("duration_ms", 0.0) or 0.0) >= 0.0
        ]
        return {
            "render_per_s": float(render_per_s),
            "render_ms_p95": _percentile(durations, 0.95),
        }

    def disabled_tick(self, *, now: float | None = None) -> dict:
        generated_at = float(time.time() if now is None else now)
        return {
            "type": "metrics_tick",
            "schema_version": METRICS_SCHEMA_VERSION,
            "generated_at": generated_at,
            "enabled": False,
            "interval_ms": 0,
            "perf": {
                "event_loop_lag_ms": {"p50": 0.0, "p95": 0.0, "max": 0.0},
                "ws": {"deltas_per_s": 0.0, "bytes_per_s": 0.0, "subscribers": 0},
                "db": {"writes_per_s": 0.0, "write_latency_p95_ms": 0.0},
                "proc": {"rss_mb": 0.0, "cpu_pct": 0.0},
                "frontend": None,
                "task_health": {
                    "recomputes": 0,
                    "duration_ms_p95": 0.0,
                    "changed_tasks": 0,
                    "active_tasks": 0,
                    "target_tasks": 0,
                    "modes": {},
                },
                "live": {"agents": 0, "ptys": 0, "prompt_queue_depth": 0},
            },
            "windows": {
                "1m": {
                    "ws_deltas_per_s": 0.0,
                    "db_writes_per_s": 0.0,
                    "event_loop_lag_p95_ms": 0.0,
                },
                "5m": {
                    "ws_deltas_per_s": 0.0,
                    "db_writes_per_s": 0.0,
                    "event_loop_lag_p95_ms": 0.0,
                },
            },
            "meter_overhead": {
                "agg_tick_ms": 0.0,
                "collect_overhead_pct": 0.0,
            },
        }

    def aggregate_tick(
        self,
        *,
        live: dict | None = None,
        now: float | None = None,
        interval_seconds: float | None = None,
    ) -> dict:
        """Drain interval counters and return the published metrics_tick."""
        aggregate_started = time.perf_counter()
        generated_at = float(time.time() if now is None else now)
        elapsed = float(interval_seconds or 0.0)
        if elapsed <= 0:
            elapsed = max(0.001, generated_at - self._last_aggregate_at)
        self._last_aggregate_at = generated_at

        interval = self._interval
        self._interval = _IntervalCounters(
            ws_subscribers=interval.ws_subscribers,
        )

        lag_samples = list(interval.event_loop_lag_ms)
        db_latencies = list(interval.db_latencies_ms)
        task_health_durations = list(interval.task_health_duration_ms)
        ws_deltas_per_s = interval.ws_deltas / elapsed
        ws_bytes_per_s = interval.ws_bytes / elapsed
        db_writes_per_s = interval.db_writes / elapsed
        lag_p50 = _percentile(lag_samples, 0.5)
        lag_p95 = _percentile(lag_samples, 0.95)
        lag_max = max(lag_samples) if lag_samples else 0.0
        db_latency_p95 = _percentile(db_latencies, 0.95)

        self._rolling_ticks.append({
            "timestamp": generated_at,
            "elapsed": elapsed,
            "ws_deltas": interval.ws_deltas,
            "db_writes": interval.db_writes,
        })
        for sample in lag_samples:
            self._rolling_lag_samples.append((generated_at, sample))
        self._prune_rolling(generated_at)

        proc = self._process_gauges(generated_at)
        live_payload = {
            "agents": int((live or {}).get("agents", 0) or 0),
            "ptys": int((live or {}).get("ptys", 0) or 0),
            "prompt_queue_depth": int(
                (live or {}).get("prompt_queue_depth", 0) or 0
            ),
        }
        collect_overhead_pct = (
            (self._inline_overhead_ns / 1_000_000_000.0) / elapsed * 100.0
        ) if elapsed > 0 else 0.0
        self._inline_overhead_ns = 0
        self._inline_calls = 0

        payload = {
            "type": "metrics_tick",
            "schema_version": METRICS_SCHEMA_VERSION,
            "generated_at": generated_at,
            "enabled": True,
            "interval_ms": int(round(elapsed * 1000.0)),
            "perf": {
                "event_loop_lag_ms": {
                    "p50": lag_p50,
                    "p95": lag_p95,
                    "max": lag_max,
                },
                "ws": {
                    "deltas_per_s": ws_deltas_per_s,
                    "bytes_per_s": ws_bytes_per_s,
                    "subscribers": interval.ws_subscribers,
                },
                "db": {
                    "writes_per_s": db_writes_per_s,
                    "write_latency_p95_ms": db_latency_p95,
                },
                "proc": proc,
                "frontend": self._frontend_payload(generated_at),
                "task_health": {
                    "recomputes": int(interval.task_health_recomputes),
                    "duration_ms_p95": _percentile(task_health_durations, 0.95),
                    "changed_tasks": int(interval.task_health_changed_tasks),
                    "active_tasks": int(interval.task_health_active_tasks),
                    "target_tasks": int(interval.task_health_target_tasks),
                    "modes": dict(interval.task_health_modes),
                },
                "live": live_payload,
            },
            "windows": {
                "1m": self._window_payload(generated_at, 60.0),
                "5m": self._window_payload(generated_at, 300.0),
            },
            "meter_overhead": {
                "agg_tick_ms": 0.0,
                "collect_overhead_pct": collect_overhead_pct,
            },
        }
        agg_tick_ms = (time.perf_counter() - aggregate_started) * 1000.0
        self.last_agg_tick_ms = agg_tick_ms
        payload["meter_overhead"]["agg_tick_ms"] = agg_tick_ms
        return payload

    def _prune_rolling(self, now: float) -> None:
        cutoff = now - 300.0
        while self._rolling_ticks and self._rolling_ticks[0]["timestamp"] < cutoff:
            self._rolling_ticks.popleft()
        while self._rolling_lag_samples and self._rolling_lag_samples[0][0] < cutoff:
            self._rolling_lag_samples.popleft()

    def _window_payload(self, now: float, seconds: float) -> dict:
        cutoff = now - seconds
        elapsed = 0.0
        ws_deltas = 0
        db_writes = 0
        for item in self._rolling_ticks:
            if item["timestamp"] < cutoff:
                continue
            elapsed += float(item.get("elapsed", 0.0) or 0.0)
            ws_deltas += int(item.get("ws_deltas", 0) or 0)
            db_writes += int(item.get("db_writes", 0) or 0)
        lag_values = [
            value for ts, value in self._rolling_lag_samples
            if ts >= cutoff
        ]
        return {
            "ws_deltas_per_s": (ws_deltas / elapsed) if elapsed > 0 else 0.0,
            "db_writes_per_s": (db_writes / elapsed) if elapsed > 0 else 0.0,
            "event_loop_lag_p95_ms": _percentile(lag_values, 0.95),
        }

    def _process_gauges(self, now: float) -> dict:
        process_time = time.process_time()
        cpu_pct = 0.0
        previous = self._last_process_sample
        if previous:
            prev_wall, prev_cpu = previous
            wall_delta = max(0.001, now - prev_wall)
            cpu_delta = max(0.0, process_time - prev_cpu)
            cpu_pct = (cpu_delta / wall_delta) * 100.0
        self._last_process_sample = (now, process_time)
        return {"rss_mb": _rss_mb(), "cpu_pct": cpu_pct}

    @staticmethod
    def rollup_row_from_tick(payload: dict) -> dict:
        generated_at = float(payload.get("generated_at", time.time()) or time.time())
        bucket_start = int(generated_at // METRICS_ROLLUP_RESOLUTION_SECONDS) * (
            METRICS_ROLLUP_RESOLUTION_SECONDS
        )
        perf = payload.get("perf", {}) if isinstance(payload, dict) else {}
        event_loop = perf.get("event_loop_lag_ms", {}) if isinstance(perf, dict) else {}
        ws = perf.get("ws", {}) if isinstance(perf, dict) else {}
        db = perf.get("db", {}) if isinstance(perf, dict) else {}
        proc = perf.get("proc", {}) if isinstance(perf, dict) else {}
        return {
            "bucket_start": bucket_start,
            "bucket_seconds": METRICS_ROLLUP_RESOLUTION_SECONDS,
            "sample_count": 1,
            "event_loop_lag_p95_ms": float(event_loop.get("p95", 0.0) or 0.0),
            "ws_deltas_per_s": float(ws.get("deltas_per_s", 0.0) or 0.0),
            "db_write_latency_p95_ms": float(
                db.get("write_latency_p95_ms", 0.0) or 0.0
            ),
            "rss_mb": float(proc.get("rss_mb", 0.0) or 0.0),
            "cpu_pct": float(proc.get("cpu_pct", 0.0) or 0.0),
            "updated_at": generated_at,
        }


class MetricsDaemon:
    """Background metrics aggregation loop."""

    def __init__(
        self,
        *,
        state,
        db,
        send_tick: Callable[[dict], Awaitable[None]],
        live_sampler: Callable[[], dict] | None = None,
        interval_seconds: float = METRICS_TICK_INTERVAL_SECONDS,
    ):
        self.state = state
        self.db = db
        self.send_tick = send_tick
        self.live_sampler = live_sampler or (lambda: {})
        self.interval_seconds = min(
            max(0.1, float(interval_seconds or METRICS_TICK_INTERVAL_SECONDS)),
            METRICS_TICK_INTERVAL_CAP_SECONDS,
        )
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._last_enabled = bool(
            getattr(getattr(state, "global_settings", None), "metrics_enabled", True)
        )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="metrics-daemon")

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time()
        last_tick = time.time()
        trim_counter = 0
        while not self._stopped.is_set():
            interval = self.interval_seconds
            expected += interval
            try:
                await asyncio.sleep(max(0.0, expected - loop.time()))
            except asyncio.CancelledError:
                raise
            now_loop = loop.time()
            now = time.time()
            lag_ms = max(0.0, (now_loop - expected) * 1000.0)
            collector = getattr(self.state, "metrics_collector", None)
            if collector is None:
                last_tick = now
                continue
            enabled = bool(
                getattr(getattr(self.state, "global_settings", None),
                        "metrics_enabled", True)
            )
            collector.set_enabled(enabled)
            if not enabled:
                if self._last_enabled:
                    await self.send_tick(collector.disabled_tick(now=now))
                self._last_enabled = False
                last_tick = now
                continue

            self._last_enabled = True
            collector.record_event_loop_lag(lag_ms)
            elapsed = max(0.001, now - last_tick)
            last_tick = now
            payload = collector.aggregate_tick(
                live=self.live_sampler(),
                now=now,
                interval_seconds=elapsed,
            )
            await self.send_tick(payload)
            if self.db:
                row = collector.rollup_row_from_tick(payload)
                self.db.defer_write(
                    "metrics_perf_rollups",
                    "save_metrics_perf_rollup",
                    row,
                )
                trim_counter += 1
                # Trim approximately once per minute at the default cadence.
                if trim_counter >= max(1, int(60 / self.interval_seconds)):
                    trim_counter = 0
                    cutoff = now - METRICS_RETENTION_SECONDS
                    self.db.defer_write(
                        "metrics_perf_rollups",
                        "trim_metrics_perf_rollups",
                        cutoff,
                    )
