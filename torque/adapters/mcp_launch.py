"""Helpers for generating local MCP launch configs."""

from __future__ import annotations

import sys
from pathlib import Path


_DEFAULT_HTTP_ENTRYPOINT = "torque/mcp.py"


def build_stdio_launch_spec(mcp_entrypoint: str = "") -> dict | None:
    """Return a stdio launch spec for non-default local MCP entrypoints.

    Torque's default worker/legacy MCP surface still uses the shared HTTP
    endpoint. Engineer sessions use a local stdio proxy entrypoint so the
    bound engineer id is validated in the launched client path before tool
    calls are forwarded to the daemon.
    """
    entrypoint = str(mcp_entrypoint or "").strip()
    if not entrypoint or entrypoint == _DEFAULT_HTTP_ENTRYPOINT:
        return None

    module_name = entrypoint.replace("/", ".")
    if module_name.endswith(".py"):
        module_name = module_name[:-3]
    repo_root = str(Path(__file__).resolve().parents[2])
    bootstrap = (
        "import runpy, sys; "
        f"sys.path.insert(0, {repo_root!r}); "
        f"runpy.run_module({module_name!r}, run_name='__main__')"
    )
    return {
        "command": sys.executable or "python3",
        "args": ["-c", bootstrap],
    }
