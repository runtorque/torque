"""Read-only orchestration health and metrics projections."""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import Any

from ..metrics import (
    METRICS_RETENTION_SECONDS,
    METRICS_ROLLUP_RESOLUTION_SECONDS,
    METRICS_SCHEMA_VERSION,
)

ARCHIVED_LANE = "Archived"
_SYSTEM_HEALTH_WINDOWS = {
    "24h": (24 * 3600, 3600),
    "7d": (7 * 86400, 86400),
    "30d": (30 * 86400, 86400),
}
_SYSTEM_HEALTH_EVENT_KINDS = {
    "task_dispatched",
    "task_queued",
    "task_auto_dispatched",
    "worker_boot_doa",
    "engineer_queue_empty",
}
_SYSTEM_HEALTH_AGE_BUCKETS = (
    ("<1h", 0, 3600),
    ("1-4h", 3600, 4 * 3600),
    ("4-24h", 4 * 3600, 24 * 3600),
    ("1-3d", 24 * 3600, 3 * 86400),
    ("3-7d", 3 * 86400, 7 * 86400),
    ("7d+", 7 * 86400, None),
)

def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default

def _parse_health_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value) if value else 0.0
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0

def _health_bucket_index(ts: float, since: float, bucket_seconds: int,
                         bucket_count: int) -> int:
    try:
        ts = float(ts or 0.0)
    except (TypeError, ValueError):
        return -1
    if ts < since:
        return -1
    idx = int((ts - since) // bucket_seconds)
    if idx < 0:
        return -1
    return min(idx, bucket_count - 1)

def _health_percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v or 0.0) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    q = max(0.0, min(1.0, float(quantile or 0.0)))
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + ((ordered[hi] - ordered[lo]) * frac)

def _health_series(bucket_count: int, fill=0):
    return [copy.deepcopy(fill) for _ in range(bucket_count)]

def _health_bucket_label(start: float, bucket_seconds: int) -> str:
    fmt = "%m-%d %H:%M" if bucket_seconds < 86400 else "%m-%d"
    return datetime.fromtimestamp(start, timezone.utc).strftime(fmt)

def _health_task_value(task, name: str, default=None):
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)

def _health_task_group(task) -> str:
    return str(
        _health_task_value(task, "group", None)
        or _health_task_value(task, "group_name", "")
        or ""
    ).strip()

def _health_action_name(task) -> str:
    return str(_health_task_value(task, "action_name", "") or "").strip().lower()

def _health_task_id(task) -> str:
    return str(_health_task_value(task, "id", "") or "").strip()

def _health_response_task_ids(response: dict) -> list[str]:
    if not isinstance(response, dict):
        return []
    ids = []
    direct = str(response.get("task_id", "") or "").strip()
    if direct:
        ids.append(direct)
    results = response.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("task_id", "") or "").strip()
            if tid:
                ids.append(tid)
    return ids


def normalize_default_worker_concurrency(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, value)


class MetricsService:
    """Compute metrics projections from a MatrixState read model."""

    def __init__(self, state: Any):
        self._state = state

    def __getattr__(self, name: str):
        # Projection methods intentionally read through the state object while
        # keeping their own internal helper calls on this service.
        return getattr(self._state, name)

    def system_health_metrics(
        self,
        window: str = "24h",
        group: str = "",
        *,
        now: float | None = None,
    ) -> dict:
        """Return read-only orchestration health metrics for the dock panel."""
        window = str(window or "24h").strip()
        if window not in _SYSTEM_HEALTH_WINDOWS:
            raise ValueError("window must be one of: 24h, 7d, 30d")
        window_seconds, bucket_seconds = _SYSTEM_HEALTH_WINDOWS[window]
        until = float(time.time() if now is None else now)
        since = until - window_seconds
        bucket_count = int(window_seconds // bucket_seconds)
        group = str(group or "").strip()
        scope = "group" if group else "all_groups"
        buckets = [
            {
                "start": since + (idx * bucket_seconds),
                "end": since + ((idx + 1) * bucket_seconds),
                "label": _health_bucket_label(
                    since + (idx * bucket_seconds),
                    bucket_seconds,
                ),
            }
            for idx in range(bucket_count)
        ]
        notes = [
            (
                "Task age is a current lane-age distribution; V1 does not "
                "persist historical lane-transition samples."
            ),
            (
                "Merge latency is boundary recorded_at→merged_at; durable "
                "PR-created timestamps are unavailable in V1."
            ),
            "Dispatch shape is coverage-limited to MCP idempotency rows.",
            (
                "Engineer utilization is worker busy-time divided by current "
                "engineer concurrency capacity."
            ),
        ]
        if group and group not in self.groups:
            notes.append(
                f"Group '{group}' is not currently present; returning any "
                "matching historical rows plus empty current-state metrics."
            )

        events: list[dict] = []
        agent_tasks: list[dict] = []
        mcp_calls: list[dict] = []
        mcp_idempotency_storage: dict = {}
        if self.db:
            events = self.db.load_panel_events_window(
                since,
                until,
                group=group,
                kinds=sorted(_SYSTEM_HEALTH_EVENT_KINDS),
            )
            agent_tasks = self.db.load_agent_tasks_window(
                since,
                until,
                group=group,
            )
            mcp_calls = self.db.load_mcp_dispatch_calls_window(
                since,
                until,
                group=group,
            )
            if hasattr(self.db, "mcp_idempotency_storage_stats"):
                try:
                    mcp_idempotency_storage = self.db.mcp_idempotency_storage_stats()
                except Exception:
                    mcp_idempotency_storage = {}
        elif self.panel_log:
            recent = self.panel_log.get_recent(
                getattr(self.panel_log, "_max_size", 500)
            )
            events = [
                evt for evt in recent
                if str(evt.get("kind", "") or "") in _SYSTEM_HEALTH_EVENT_KINDS
                and since <= _safe_float(evt.get("timestamp", 0.0)) <= until
                and (not group or str(evt.get("group", "") or "") == group)
            ]
            notes.append(
                "Panel event metrics are limited to the in-memory recent "
                "event ring because no SQLite database is attached."
            )
        else:
            notes.append("No SQLite database or panel event log is attached.")

        tasks = [
            task for task in self.board_tasks.values()
            if not group or _health_task_group(task) == group
        ]
        task_groups_by_id = {
            _health_task_id(task): _health_task_group(task)
            for task in self.board_tasks.values()
        }

        series = {
            "dispatches": _health_series(bucket_count, 0),
            "task_queued": _health_series(bucket_count, 0),
            "task_auto_dispatched": _health_series(bucket_count, 0),
            "worker_boot_doa": _health_series(bucket_count, 0),
            "engineer_queue_empty": _health_series(bucket_count, 0),
            "reviews": _health_series(bucket_count, 0),
            "merges": _health_series(bucket_count, 0),
            "busy_seconds": _health_series(bucket_count, 0.0),
            "capacity_seconds": _health_series(bucket_count, 0.0),
            "utilization_pct": _health_series(bucket_count, 0.0),
        }
        event_counts = {
            "task_dispatched": 0,
            "task_queued": 0,
            "task_auto_dispatched": 0,
            "worker_boot_doa": 0,
            "engineer_queue_empty": 0,
        }
        for evt in events:
            kind = str(evt.get("kind", "") or "")
            if kind not in event_counts:
                continue
            idx = _health_bucket_index(
                _safe_float(evt.get("timestamp", 0.0)),
                since,
                bucket_seconds,
                bucket_count,
            )
            if idx < 0:
                continue
            event_counts[kind] += 1
            if kind == "task_dispatched":
                series["dispatches"][idx] += 1
            else:
                series[kind][idx] += 1

        shape = {
            "serial_tool_calls": 0,
            "batch_tool_calls": 0,
            "batch_entries": 0,
            "statuses": {},
            "unscoped_tool_calls": 0,
            "scoped_tool_calls": 0,
        }
        for call in mcp_calls:
            response = call.get("response") if isinstance(call, dict) else {}
            if not isinstance(response, dict):
                response = {}
            call_group = str(call.get("group", "") or "").strip()
            if not call_group:
                response_groups = {
                    task_groups_by_id.get(tid, "")
                    for tid in _health_response_task_ids(response)
                }
                response_groups = {g for g in response_groups if g}
                if len(response_groups) == 1:
                    call_group = next(iter(response_groups))
            if group and call_group and call_group != group:
                continue
            if call_group:
                shape["scoped_tool_calls"] += 1
            else:
                shape["unscoped_tool_calls"] += 1
            tool_name = str(call.get("tool_name", "") or "")
            if tool_name.endswith("engineer_task_dispatch"):
                shape["serial_tool_calls"] += 1
                status = str(
                    response.get("status", "")
                    or response.get("type", "")
                    or ""
                )
                if status:
                    shape["statuses"][status] = (
                        shape["statuses"].get(status, 0) + 1
                    )
            elif tool_name.endswith("engineer_batch_dispatch"):
                shape["batch_tool_calls"] += 1
                results = response.get("results")
                if isinstance(results, list):
                    shape["batch_entries"] += len(results)
                    for item in results:
                        if not isinstance(item, dict):
                            continue
                        status = str(item.get("status", "") or "unknown")
                        shape["statuses"][status] = (
                            shape["statuses"].get(status, 0) + 1
                        )
        dispatch_tool_entries = (
            shape["serial_tool_calls"] + shape["batch_entries"]
        )
        dispatch_tool_calls = (
            shape["serial_tool_calls"] + shape["batch_tool_calls"]
        )
        shape_partial = bool(
            shape["unscoped_tool_calls"]
            or dispatch_tool_entries != event_counts["task_dispatched"]
        )
        if not mcp_calls and event_counts["task_dispatched"]:
            notes.append(
                "No MCP idempotency rows were found for dispatch shape in "
                "this window."
            )
        storage_warnings = list(
            (mcp_idempotency_storage or {}).get("warnings", []) or []
        )
        if storage_warnings:
            notes.append(
                "MCP idempotency storage is above warning thresholds: "
                + ", ".join(str(item) for item in storage_warnings)
            )

        review_summary, review_distribution = self._system_health_reviews(
            tasks,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            bucket_count=bucket_count,
            review_series=series["reviews"],
        )
        merge_summary = self._system_health_merges(
            tasks,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            bucket_count=bucket_count,
            merge_series=series["merges"],
            window_seconds=window_seconds,
        )
        task_age = self._system_health_task_age(tasks, now=until)
        utilization = self._system_health_utilization(
            agent_tasks,
            group=group,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            buckets=buckets,
            busy_series=series["busy_seconds"],
            capacity_series=series["capacity_seconds"],
            utilization_series=series["utilization_pct"],
        )
        utilization["queue_empty_count"] = event_counts["engineer_queue_empty"]
        if utilization["capacity_seconds"] <= 0 and utilization["busy_seconds"] > 0:
            notes.append(
                "Worker busy-time exists but current engineer capacity is "
                "zero for this scope, so utilization percentage is unavailable."
            )

        dispatch_count = event_counts["task_dispatched"]
        window_hours = max(window_seconds / 3600.0, 1e-9)
        doa_count = event_counts["worker_boot_doa"]
        doa_rate = (doa_count / dispatch_count) if dispatch_count else 0.0

        return {
            "type": "system_health_metrics",
            "generated_at": until,
            "window": window,
            "group": group,
            "scope": scope,
            "since": since,
            "until": until,
            "bucket_seconds": bucket_seconds,
            "buckets": buckets,
            "summary": {
                "dispatch": {
                    "count": dispatch_count,
                    "workers_per_hour": dispatch_count / window_hours,
                    "queued_count": event_counts["task_queued"],
                    "autoresume_count": event_counts["task_auto_dispatched"],
                },
                "dispatch_shape": shape,
                "review_cycles": review_summary,
                "merge": merge_summary,
                "task_age": {
                    "lanes": len(task_age),
                    "tasks": sum(v["count"] for v in task_age.values()),
                },
                "worker_boot_doa": {
                    "count": doa_count,
                    "denominator": dispatch_count,
                    "rate": doa_rate,
                },
                "utilization": utilization,
            },
            "series": series,
            "distributions": {
                "dispatch_shape_statuses": shape["statuses"],
                "review_cycles": review_distribution,
                "task_age_by_lane": task_age,
            },
            "coverage": {
                "dispatch_shape": {
                    "available": bool(mcp_calls),
                    "partial": shape_partial,
                    "dispatch_events": dispatch_count,
                    "dispatch_tool_calls": dispatch_tool_calls,
                    "dispatch_tool_entries": dispatch_tool_entries,
                    "unscoped_tool_calls": shape["unscoped_tool_calls"],
                    "scoped_tool_calls": shape["scoped_tool_calls"],
                },
                "merge": {
                    "source": "worktree_boundary",
                    "latency_label": "boundary-to-merge",
                },
                "task_age": {
                    "source": "current_lane_entered_at",
                    "historical": False,
                },
                "utilization": {
                    "source": "agent_tasks intervals",
                    "capacity_source": (
                        "current engineer count × configured concurrency"
                    ),
                },
                "mcp_idempotency_storage": mcp_idempotency_storage,
            },
            "notes": notes,
        }

    def metrics_history(
        self,
        window: str = "24h",
        group: str = "",
        *,
        now: float | None = None,
    ) -> dict:
        """Return the published v1 metrics history payload.

        The bucket grid is shared by perf rollups and workflow aggregates.
        Perf comes from the bounded metrics rollup table; workflow metrics are
        derived on-demand from existing durable tables/current board state.
        """
        window = str(window or "24h").strip()
        if window not in _SYSTEM_HEALTH_WINDOWS:
            raise ValueError("window must be one of: 24h, 7d, 30d")
        window_seconds, bucket_seconds = _SYSTEM_HEALTH_WINDOWS[window]
        now_value = float(time.time() if now is None else now)
        until = (
            int(now_value // bucket_seconds) * bucket_seconds
        )
        if until < now_value:
            until += bucket_seconds
        since = until - window_seconds
        bucket_count = int(window_seconds // bucket_seconds)
        buckets = [
            {
                "start": int(since + (idx * bucket_seconds)),
                "end": int(since + ((idx + 1) * bucket_seconds)),
                "label": _health_bucket_label(
                    since + (idx * bucket_seconds),
                    bucket_seconds,
                ),
            }
            for idx in range(bucket_count)
        ]

        requested_group = str(group or "").strip()
        resolved_group = requested_group or str(self.active_group or "").strip()
        scope = "group" if resolved_group else "all_groups"
        notes: list[str] = []
        if requested_group and requested_group not in self.groups:
            notes.append(
                f"Group '{requested_group}' is not currently present; "
                "workflow series are returned empty for that scope."
            )
        perf = self._metrics_perf_history(
            since=since,
            until=until,
            buckets=buckets,
            bucket_seconds=bucket_seconds,
            notes=notes,
        )
        workflow, coverage, workflow_notes = self._metrics_workflow_history(
            window=window,
            group=resolved_group,
            now=until,
        )
        notes.extend(workflow_notes)

        return {
            "type": "metrics_history",
            "schema_version": METRICS_SCHEMA_VERSION,
            "generated_at": now_value,
            "window": window,
            "group": resolved_group,
            "scope": scope,
            "bucket_seconds": bucket_seconds,
            "buckets": buckets,
            "perf": perf,
            "workflow": workflow,
            "coverage": coverage,
            "notes": notes,
        }

    def _metrics_perf_history(
        self,
        *,
        since: float,
        until: float,
        buckets: list[dict],
        bucket_seconds: int,
        notes: list[str],
    ) -> dict:
        bucket_count = len(buckets)
        series = {
            "event_loop_lag_p95_ms": _health_series(bucket_count, 0.0),
            "ws_deltas_per_s": _health_series(bucket_count, 0.0),
            "db_write_latency_p95_ms": _health_series(bucket_count, 0.0),
            "rss_mb": _health_series(bucket_count, 0.0),
            "cpu_pct": _health_series(bucket_count, 0.0),
        }
        accum = [
            {
                "samples": 0,
                "ws": 0.0,
                "rss": 0.0,
                "cpu": 0.0,
                "lag_p95": 0.0,
                "db_latency_p95": 0.0,
            }
            for _ in range(bucket_count)
        ]
        rows: list[dict] = []
        if self.db and hasattr(self.db, "load_metrics_perf_rollups"):
            rows = self.db.load_metrics_perf_rollups(since, until)
        else:
            notes.append("No SQLite metrics rollup table is attached.")
        for row in rows:
            idx = _health_bucket_index(
                _safe_float(row.get("bucket_start", 0.0)),
                since,
                bucket_seconds,
                bucket_count,
            )
            if idx < 0:
                continue
            sample_count = max(1, int(row.get("sample_count", 1) or 1))
            item = accum[idx]
            item["samples"] += sample_count
            item["ws"] += (
                _safe_float(row.get("ws_deltas_per_s", 0.0)) * sample_count
            )
            item["rss"] += _safe_float(row.get("rss_mb", 0.0)) * sample_count
            item["cpu"] += _safe_float(row.get("cpu_pct", 0.0)) * sample_count
            item["lag_p95"] = max(
                item["lag_p95"],
                _safe_float(row.get("event_loop_lag_p95_ms", 0.0)),
            )
            item["db_latency_p95"] = max(
                item["db_latency_p95"],
                _safe_float(row.get("db_write_latency_p95_ms", 0.0)),
            )
        for idx, item in enumerate(accum):
            samples = item["samples"]
            if samples <= 0:
                continue
            series["event_loop_lag_p95_ms"][idx] = item["lag_p95"]
            series["ws_deltas_per_s"][idx] = item["ws"] / samples
            series["db_write_latency_p95_ms"][idx] = item["db_latency_p95"]
            series["rss_mb"][idx] = item["rss"] / samples
            series["cpu_pct"][idx] = item["cpu"] / samples
        series["retention"] = {
            "kept_seconds": METRICS_RETENTION_SECONDS,
            "rollup_resolution_seconds": METRICS_ROLLUP_RESOLUTION_SECONDS,
        }
        return series

    def _metrics_workflow_history(
        self,
        *,
        window: str,
        group: str,
        now: float,
    ) -> tuple[dict, dict, list[str]]:
        health = self.system_health_metrics(window=window, group=group, now=now)
        summary = health.get("summary", {}) or {}
        series = health.get("series", {}) or {}
        distributions = health.get("distributions", {}) or {}
        health_coverage = health.get("coverage", {}) or {}
        shape = summary.get("dispatch_shape", {}) or {}
        shape_statuses = dict(shape.get("statuses", {}) or {})
        shape_coverage = health_coverage.get("dispatch_shape", {}) or {}
        review = summary.get("review_cycles", {}) or {}
        merge = summary.get("merge", {}) or {}
        boot_doa = summary.get("worker_boot_doa", {}) or {}
        utilization = summary.get("utilization", {}) or {}
        task_age_by_lane = {}
        for lane, stats in (distributions.get("task_age_by_lane", {}) or {}).items():
            task_age_by_lane[lane] = {
                "p50": _safe_float(stats.get("p50_seconds", 0.0)),
                "p90": _safe_float(stats.get("p90_seconds", 0.0)),
                "max": _safe_float(stats.get("max_seconds", 0.0)),
                "buckets": dict(stats.get("buckets", {}) or {}),
            }
        workflow = {
            "dispatch": {
                "series": list(series.get("dispatches", []) or []),
                "workers_per_hour": _safe_float(
                    (summary.get("dispatch", {}) or {}).get(
                        "workers_per_hour",
                        0.0,
                    )
                ),
            },
            "dispatch_shape": {
                "serial": int(shape.get("serial_tool_calls", 0) or 0),
                "batch": int(shape.get("batch_tool_calls", 0) or 0),
                "batch_entries": {
                    "dispatched": int(shape_statuses.get("dispatched", 0) or 0),
                    "queued": int(shape_statuses.get("queued", 0) or 0),
                    "deferred": int(shape_statuses.get("deferred", 0) or 0),
                    "failed": int(shape_statuses.get("failed", 0) or 0),
                },
                "coverage": {
                    "partial": bool(shape_coverage.get("partial", True)),
                },
            },
            "review_cycles": {
                "avg_rounds": _safe_float(review.get("average_rounds", 0.0)),
                "first_pass_clean_pct": _safe_float(
                    review.get("first_pass_clean_pct", 0.0)
                ),
                "series": list(series.get("reviews", []) or []),
            },
            "merge": {
                "merged_per_bucket": list(series.get("merges", []) or []),
                "median_boundary_to_merge_s": _safe_float(
                    merge.get("median_boundary_to_merge_seconds", 0.0)
                ),
                "open_boundaries": int(merge.get("open_count", 0) or 0),
                "stale_boundaries": int(merge.get("stale_open_count", 0) or 0),
            },
            "task_age": {
                "by_lane": task_age_by_lane,
            },
            "boot_doa": {
                "series": list(series.get("worker_boot_doa", []) or []),
                "rate": _safe_float(boot_doa.get("rate", 0.0)),
            },
            "utilization": {
                "series": list(series.get("utilization_pct", []) or []),
                "busy_seconds": _safe_float(utilization.get("busy_seconds", 0.0)),
                "capacity_seconds": _safe_float(
                    utilization.get("capacity_seconds", 0.0)
                ),
            },
        }
        coverage = {
            "dispatch_shape": {
                "partial": bool(shape_coverage.get("partial", True)),
                "reason": (
                    "mcp_idempotency coverage is incomplete"
                    if bool(shape_coverage.get("partial", True))
                    else ""
                ),
            },
            "merge": {
                "partial": False,
                "reason": "boundary-to-merge uses worktree_boundary timestamps",
            },
            "task_age": {
                "partial": False,
                "reason": "current lane age distribution, not lane history",
            },
            "utilization": {
                "partial": False,
                "reason": (
                    "worker busy-time divided by current engineer concurrency "
                    "capacity"
                ),
            },
        }
        return workflow, coverage, list(health.get("notes", []) or [])

    def _system_health_reviews(
        self,
        tasks: list,
        *,
        since: float,
        until: float,
        bucket_seconds: int,
        bucket_count: int,
        review_series: list[int],
    ) -> tuple[dict, list[dict]]:
        roots: dict[str, list] = {}
        for task in tasks:
            root_id = str(
                _health_task_value(task, "pipeline_root_id", "") or ""
            ).strip() or _health_task_id(task)
            roots.setdefault(root_id, []).append(task)
        implementation_actions = {
            "feature/implement",
            "feature/fix-review",
            "feature/implement-preapproved",
        }
        root_items = []
        total_rounds = 0
        first_pass_clean = 0
        total_fix_rounds = 0
        for root_id, chain in roots.items():
            chain.sort(key=lambda task: (
                _parse_health_timestamp(
                    _health_task_value(task, "created_at", "")
                ),
                _health_task_id(task),
            ))
            reviews = [
                task for task in chain
                if _health_action_name(task) == "feature/review"
            ]
            for review in reviews:
                ts = _parse_health_timestamp(
                    _health_task_value(review, "created_at", "")
                )
                if since <= ts <= until:
                    idx = _health_bucket_index(
                        ts,
                        since,
                        bucket_seconds,
                        bucket_count,
                    )
                    if idx >= 0:
                        review_series[idx] += 1
            if not reviews:
                continue
            first_review_ts = _parse_health_timestamp(
                _health_task_value(reviews[0], "created_at", "")
            )
            if not (since <= first_review_ts <= until):
                continue
            fix_rounds = 0
            for task in chain:
                created_ts = _parse_health_timestamp(
                    _health_task_value(task, "created_at", "")
                )
                if (
                    created_ts > first_review_ts
                    and _health_action_name(task) in implementation_actions
                ):
                    fix_rounds += 1
            rounds = len(reviews)
            is_clean = rounds == 1 and fix_rounds == 0
            if is_clean:
                first_pass_clean += 1
            total_rounds += rounds
            total_fix_rounds += fix_rounds
            root_items.append({
                "pipeline_root_id": root_id,
                "review_rounds": rounds,
                "fix_rounds": fix_rounds,
                "first_pass_clean": is_clean,
                "first_review_at": first_review_ts,
            })
        root_count = len(root_items)
        return {
            "roots_count": root_count,
            "average_rounds": (total_rounds / root_count) if root_count else 0.0,
            "first_pass_clean_count": first_pass_clean,
            "first_pass_clean_pct": (
                first_pass_clean / root_count if root_count else 0.0
            ),
            "fix_rounds": total_fix_rounds,
            "review_tasks": sum(item["review_rounds"] for item in root_items),
        }, root_items

    def _system_health_merges(
        self,
        tasks: list,
        *,
        since: float,
        until: float,
        bucket_seconds: int,
        bucket_count: int,
        merge_series: list[int],
        window_seconds: int,
    ) -> dict:
        merged_count = 0
        lead_times = []
        open_count = 0
        stale_open_count = 0
        for task in tasks:
            boundary = _health_task_value(task, "worktree_boundary", {}) or {}
            if not isinstance(boundary, dict) or not boundary:
                continue
            status = str(boundary.get("status", "") or "").strip().lower()
            if status == "merged":
                merged_at = _parse_health_timestamp(boundary.get("merged_at"))
                if since <= merged_at <= until:
                    merged_count += 1
                    idx = _health_bucket_index(
                        merged_at,
                        since,
                        bucket_seconds,
                        bucket_count,
                    )
                    if idx >= 0:
                        merge_series[idx] += 1
                    recorded_at = _parse_health_timestamp(
                        boundary.get("recorded_at")
                    )
                    if recorded_at and merged_at >= recorded_at:
                        lead_times.append(merged_at - recorded_at)
                continue
            if not status and not (
                boundary.get("repo_root") and boundary.get("branch")
            ):
                continue
            open_count += 1
            recorded_at = _parse_health_timestamp(boundary.get("recorded_at"))
            if recorded_at and (until - recorded_at) > window_seconds:
                stale_open_count += 1
        return {
            "merged_count": merged_count,
            "median_boundary_to_merge_seconds": _health_percentile(
                lead_times,
                0.5,
            ),
            "open_count": open_count,
            "stale_open_count": stale_open_count,
        }

    def _system_health_task_age(self, tasks: list, *, now: float) -> dict:
        by_lane: dict[str, list[float]] = {}
        for task in tasks:
            lane = str(_health_task_value(task, "lane", "") or "").strip()
            if not lane or lane == ARCHIVED_LANE:
                continue
            anchor = _parse_health_timestamp(
                _health_task_value(task, "lane_entered_at", "")
            ) or _parse_health_timestamp(
                _health_task_value(task, "created_at", "")
            )
            if not anchor:
                continue
            by_lane.setdefault(lane, []).append(max(0.0, now - anchor))
        out = {}
        for lane in sorted(by_lane):
            ages = by_lane[lane]
            buckets = {name: 0 for name, _start, _end in _SYSTEM_HEALTH_AGE_BUCKETS}
            for age in ages:
                for name, start, end in _SYSTEM_HEALTH_AGE_BUCKETS:
                    if age >= start and (end is None or age < end):
                        buckets[name] += 1
                        break
            out[lane] = {
                "count": len(ages),
                "p50_seconds": _health_percentile(ages, 0.5),
                "p90_seconds": _health_percentile(ages, 0.9),
                "max_seconds": max(ages) if ages else 0.0,
                "buckets": buckets,
            }
        return out

    def _system_health_utilization(
        self,
        agent_tasks: list[dict],
        *,
        group: str,
        since: float,
        until: float,
        bucket_seconds: int,
        buckets: list[dict],
        busy_series: list[float],
        capacity_series: list[float],
        utilization_series: list[float],
    ) -> dict:
        for row in agent_tasks:
            row_group = str(row.get("group", "") or "").strip()
            if group and row_group and row_group != group:
                continue
            start = max(_safe_float(row.get("started_at", 0.0)), since)
            completed = row.get("completed_at")
            end_raw = until if completed in (None, "") else _safe_float(completed)
            end = min(max(end_raw, start), until)
            if end <= start:
                continue
            for idx, bucket in enumerate(buckets):
                overlap = max(
                    0.0,
                    min(end, bucket["end"]) - max(start, bucket["start"]),
                )
                if overlap > 0:
                    busy_series[idx] += overlap

        groups_for_capacity = [group] if group else list(self.groups.keys())
        total_capacity_per_bucket = 0.0
        for group_name in groups_for_capacity:
            if not group_name:
                continue
            engineer_count = 0
            for cell in self.iter_active_agents():
                if (
                    getattr(cell, "cell_type", "") == "agent"
                    and str(getattr(cell, "kind", "") or "") == "engineer"
                    and str(getattr(cell, "group", "") or "") == group_name
                ):
                    engineer_count += 1
            concurrency = normalize_default_worker_concurrency(
                self.get_engineer_settings(
                    group_name
                ).default_worker_concurrency
            )
            total_capacity_per_bucket += (
                engineer_count * concurrency * bucket_seconds
            )
        for idx in range(len(buckets)):
            capacity_series[idx] = total_capacity_per_bucket
            if total_capacity_per_bucket > 0:
                utilization_series[idx] = min(
                    100.0,
                    (busy_series[idx] / total_capacity_per_bucket) * 100.0,
                )
        busy_seconds = sum(busy_series)
        capacity_seconds = sum(capacity_series)
        return {
            "busy_seconds": busy_seconds,
            "capacity_seconds": capacity_seconds,
            "percent": min(
                100.0,
                (busy_seconds / capacity_seconds) * 100.0,
            ) if capacity_seconds > 0 else 0.0,
            "queue_empty_count": 0,
        }
