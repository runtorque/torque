"""Agent runtime backend selectors and guards."""

from __future__ import annotations

PTY_RUNNER_BACKEND = "pty"
CODEX_SDK_READONLY_BACKEND = "codex-sdk-readonly"
RUNNER_BACKENDS = {PTY_RUNNER_BACKEND, CODEX_SDK_READONLY_BACKEND}


def normalize_runner_backend(value: object = "", agent_type: str = "") -> str:
    """Return a supported runner backend for an agent launch/cell.

    Empty/legacy values resolve to PTY. The SDK runner is a Codex-only beta and
    must be explicitly selected; invalid values fail closed before launch side
    effects can run.
    """
    backend = str(value or "").strip().lower() or PTY_RUNNER_BACKEND
    if backend not in RUNNER_BACKENDS:
        raise ValueError(
            "runner_backend must be one of: "
            + ", ".join(sorted(RUNNER_BACKENDS))
        )
    agent = str(agent_type or "").strip().lower()
    if backend == CODEX_SDK_READONLY_BACKEND and agent != "codex":
        raise ValueError(
            "runner_backend='codex-sdk-readonly' is only supported for "
            "Codex agents (agent_type/provider 'codex')."
        )
    return backend


def runner_backend_for_cell(cell) -> str:
    """Return normalized backend for an existing cell, treating legacy rows as PTY."""
    return str(getattr(cell, "runner_backend", "") or "").strip().lower() or PTY_RUNNER_BACKEND


def is_codex_sdk_readonly(cell_or_backend) -> bool:
    """Whether a cell/backend denotes the SDK read-only runtime."""
    if isinstance(cell_or_backend, str):
        backend = cell_or_backend
    else:
        backend = runner_backend_for_cell(cell_or_backend)
    return str(backend or "").strip().lower() == CODEX_SDK_READONLY_BACKEND
