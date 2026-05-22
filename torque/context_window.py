"""Context-window usage normalization helpers."""

import time
from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_context_window_usage(
    data: dict | None,
    *,
    now: float | None = None,
) -> dict:
    """Return normalized per-session context-window usage or ``{}``.

    ``used_tokens`` is the current model-visible context usage, not cumulative
    session usage. ``limit_tokens`` must be present so callers can compute a
    meaningful percentage.
    """
    if not isinstance(data, dict):
        return {}

    used_tokens = _safe_int(data.get("used_tokens"), -1)
    limit_tokens = _safe_int(data.get("limit_tokens"), 0)
    if used_tokens < 0 or limit_tokens <= 0:
        return {}

    provided_pct = _safe_float(data.get("used_pct"), -1.0)
    if provided_pct >= 0:
        used_pct = provided_pct
    else:
        used_pct = (used_tokens / limit_tokens) * 100

    updated_at = _safe_float(
        data.get("updated_at"),
        float(time.time() if now is None else now),
    )

    return {
        "source": str(data.get("source", "") or ""),
        "model": str(data.get("model", "") or ""),
        "session_id": str(data.get("session_id", "") or ""),
        "used_tokens": used_tokens,
        "limit_tokens": limit_tokens,
        "used_pct": round(used_pct, 2),
        "input_tokens": max(0, _safe_int(data.get("input_tokens"), 0)),
        "output_tokens": max(0, _safe_int(data.get("output_tokens"), 0)),
        "cached_input_tokens": max(
            0, _safe_int(data.get("cached_input_tokens"), 0)
        ),
        "reasoning_output_tokens": max(
            0, _safe_int(data.get("reasoning_output_tokens"), 0)
        ),
        "session_total_tokens": max(
            0, _safe_int(data.get("session_total_tokens"), 0)
        ),
        "updated_at": updated_at,
    }
