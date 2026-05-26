"""Normalize ephemeral provider usage telemetry from agent providers."""

from __future__ import annotations

import math
from typing import Any

_PROVIDER_USAGE_WINDOWS = (
    ("five_hour", ("five_hour", "fiveHour", "fiveHourLimit", "five_hour_limit")),
    ("seven_day", ("seven_day", "sevenDay", "sevenDayLimit", "seven_day_limit")),
)


def _dict_value(source: dict, *keys: str) -> Any:
    if not isinstance(source, dict):
        return None
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _float_value(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _integer_percentage(value: Any) -> int | None:
    parsed = _float_value(value)
    if parsed is None:
        return None
    if parsed < 0:
        return None
    return int(math.floor(min(100.0, parsed) + 0.5))


def _normalize_window(raw: Any) -> dict:
    unavailable = {
        "available": False,
        "used_percentage": None,
        "resets_at": None,
    }
    if not isinstance(raw, dict):
        return unavailable
    if raw.get("available") is False:
        return unavailable
    pct = _integer_percentage(
        _dict_value(raw, "used_percentage", "usedPercentage", "used_pct", "usedPct")
    )
    resets_at = _dict_value(raw, "resets_at", "resetsAt", "reset_at", "resetAt")
    resets_at = str(resets_at or "").strip()
    if pct is None or not resets_at:
        return unavailable
    return {
        "available": True,
        "used_percentage": pct,
        "resets_at": resets_at,
    }


def normalize_provider_usage_rate_limits(rate_limits: Any) -> dict | None:
    """Return Torque's provider_usage shape from a provider rate_limits payload.

    ``None`` means the provider did not expose rate-limit telemetry at all.
    When telemetry is present, each known window is present independently with
    ``available: false`` and null values if that window is absent/malformed.
    """
    if not isinstance(rate_limits, dict):
        return None
    usage: dict[str, dict] = {}
    for canonical, aliases in _PROVIDER_USAGE_WINDOWS:
        usage[canonical] = _normalize_window(_dict_value(rate_limits, *aliases))
    return usage


def normalize_provider_usage(value: Any) -> dict | None:
    """Normalize an already-mapped provider_usage payload defensively."""
    if not isinstance(value, dict):
        return None
    usage: dict[str, dict] = {}
    for canonical, aliases in _PROVIDER_USAGE_WINDOWS:
        usage[canonical] = _normalize_window(_dict_value(value, canonical, *aliases))
    return usage


def provider_usage_fingerprint(value: Any) -> tuple | None:
    """Fingerprint meaningful provider_usage changes for emission dedupe.

    Meaningful changes are availability flips, integer-percent used changes,
    and reset timestamp changes, per configured window.
    """
    usage = normalize_provider_usage(value)
    if usage is None:
        return None
    parts = []
    for canonical, _aliases in _PROVIDER_USAGE_WINDOWS:
        window = usage.get(canonical) or {}
        available = bool(window.get("available"))
        if not available:
            parts.append((canonical, False, None, None))
            continue
        parts.append((
            canonical,
            True,
            _integer_percentage(window.get("used_percentage")),
            str(window.get("resets_at") or ""),
        ))
    return tuple(parts)
