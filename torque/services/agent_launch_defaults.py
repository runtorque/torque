"""Shared precedence for raw launch previews and executable launch configs."""

from ..state import normalize_codex_fast_mode


def resolve_fast_mode(*values) -> str:
    """Resolve launch preference from most to least specific scope."""
    for value in values:
        mode = normalize_codex_fast_mode(value, strict=False)
        if mode != "inherit":
            return mode
    return "inherit"


def worker_launch_overrides(settings, overrides: dict | None = None) -> dict:
    """Apply worker defaults after roles, with explicit launch values last.

    Fast mode is resolved separately: role/per-launch fast mode takes priority
    over worker and shared defaults. Keep commands raw until launch finalization.
    """
    merged = {}
    for target, source in (
        ("provider", "worker_provider"), ("command", "worker_boot_command"),
        ("model", "worker_model"), ("reasoning_effort", "worker_reasoning_effort"),
    ):
        value = getattr(settings, source, "")
        if value:
            merged[target] = value
    for key, value in (overrides or {}).items():
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        elif value is None:
            continue
        merged[key] = value
    return merged
