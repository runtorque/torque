"""Minimal 5-field cron expression parser.

Supports: *, */N, N, N-M, N,M  for each of the five fields
(minute, hour, day-of-month, month, day-of-week).

No external dependencies — uses stdlib datetime + zoneinfo.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Field ranges: (min, max)
_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0=Sun … 6=Sat)
]

_DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3,
              "thu": 4, "fri": 5, "sat": 6}
_MON_NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _parse_field(token: str, lo: int, hi: int, names: dict | None = None
                 ) -> set[int]:
    """Parse one cron field token into a set of valid integers."""
    result: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if names:
            for n, v in names.items():
                part = part.lower().replace(n, str(v))
        if part == "*":
            return set(range(lo, hi + 1))
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"Step must be positive: {token}")
            if base == "*":
                start = lo
            elif "-" in base:
                a, b = base.split("-", 1)
                start, end = int(a), int(b)
                return set(range(start, end + 1, step))
            else:
                start = int(base)
            result |= set(range(start, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            result |= set(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    # Validate
    for v in result:
        if v < lo or v > hi:
            raise ValueError(
                f"Value {v} out of range [{lo}-{hi}] in: {token}")
    return result


def parse_cron(expr: str) -> tuple[set, set, set, set, set]:
    """Parse a 5-field cron expression into sets of valid values.

    Returns (minutes, hours, days_of_month, months, days_of_week).
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Expected 5 fields, got {len(fields)}: {expr!r}")
    names_map = [None, None, None, _MON_NAMES, _DOW_NAMES]
    return tuple(
        _parse_field(f, lo, hi, nm)
        for f, (lo, hi), nm in zip(fields, _FIELD_RANGES, names_map)
    )


def next_run(expr: str, after: datetime, tz: str = "") -> datetime:
    """Compute the next fire time after *after* for a cron expression.

    Returns a timezone-aware datetime in UTC.  *tz* is an IANA timezone
    name (e.g. "America/New_York"); empty means UTC.
    """
    minutes, hours, doms, months, dows = parse_cron(expr)
    zone = ZoneInfo(tz) if tz else timezone.utc
    # Work in the target timezone so DST is handled correctly.
    dt = after.astimezone(zone)
    # Advance to the next minute boundary.
    dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Cap iterations to prevent infinite loops on impossible expressions
    # (e.g. Feb 31).  366 days * 1440 minutes would be extreme — 527040.
    # We'll cap at 4 years of minutes as a generous upper bound.
    max_iter = 366 * 24 * 60 * 4
    for _ in range(max_iter):
        if dt.month not in months:
            # Skip to first day of next valid month.
            if dt.month == 12:
                dt = dt.replace(year=dt.year + 1, month=1, day=1,
                                hour=0, minute=0)
            else:
                dt = dt.replace(month=dt.month + 1, day=1,
                                hour=0, minute=0)
            continue
        if dt.day not in doms or dt.isoweekday() % 7 not in dows:
            dt = dt.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if dt.hour not in hours:
            dt = dt.replace(minute=0) + timedelta(hours=1)
            continue
        if dt.minute not in minutes:
            dt += timedelta(minutes=1)
            continue
        # Match!
        return dt.astimezone(timezone.utc)

    raise ValueError(f"No matching time found within 4 years for: {expr!r}")
