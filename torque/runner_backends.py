"""Agent runtime backend selectors and guards."""

from __future__ import annotations

PTY_RUNNER_BACKEND = "pty"
RUNNER_BACKENDS = {PTY_RUNNER_BACKEND}


def normalize_runner_backend(value: object = "", agent_type: str = "") -> str:
    """Return a supported runner backend for an agent launch/cell.

    Empty/legacy values resolve to PTY. Any explicit non-PTY value fails closed
    before launch side effects can run.
    """
    backend = str(value or "").strip().lower() or PTY_RUNNER_BACKEND
    if backend not in RUNNER_BACKENDS:
        raise ValueError(
            "runner_backend must be one of: "
            + ", ".join(sorted(RUNNER_BACKENDS))
        )
    return backend


def runner_backend_for_cell(cell) -> str:
    """Return normalized backend for an existing cell, treating legacy rows as PTY."""
    backend = str(getattr(cell, "runner_backend", "") or "").strip().lower()
    return backend if backend in RUNNER_BACKENDS else PTY_RUNNER_BACKEND
