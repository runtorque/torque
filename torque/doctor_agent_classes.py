"""Agent Class-specific diagnostics used by :mod:`torque.doctor`."""

from __future__ import annotations

import json
import sqlite3


def collect_frozen_missing_tools(conn: sqlite3.Connection | None) -> list[dict]:
    """Find launch-frozen public tools that the current registry no longer has."""

    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, name, effective_agent_class_snapshot FROM agents "
            "WHERE cell_type='agent' ORDER BY name, id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    from .mcp import ALL_TOOLS, missing_frozen_public_tools
    from .mcp_canonical import canonical_tool_name

    available_tools = tuple(
        canonical_tool_name(tool.get("name", "")) for tool in ALL_TOOLS
    )
    missing: list[dict] = []
    for agent_id, agent_name, raw_snapshot in rows:
        try:
            snapshot = json.loads(raw_snapshot or "{}")
        except (json.JSONDecodeError, TypeError):
            snapshot = {}
        if not snapshot:
            continue
        for tool_name in missing_frozen_public_tools(snapshot, available_tools):
            missing.append({
                "agent_id": str(agent_id or ""),
                "agent_name": str(agent_name or ""),
                "class_id": str(snapshot.get("id", "") or ""),
                "class_version": str(snapshot.get("version", "") or ""),
                "tool": tool_name,
            })
    return missing


def frozen_missing_tools_warning(report: dict) -> dict | None:
    classes = report.get("agent_classes", {}) or {}
    missing = list(classes.get("frozen_missing_tools", []) or [])
    if not missing:
        return None
    return {
        "name": "frozen_agent_class_missing_tools",
        "status": "warn",
        "details": {
            "count": len(missing),
            "references": missing,
            "hint": (
                "the frozen Agent Class launches with the removed tool skipped; "
                "review its ACL and relaunch after updating the class"
            ),
        },
    }


def format_frozen_missing_tools_warning(details: dict) -> list[str]:
    return [
        "  - frozen Agent Class "
        f"{entry.get('class_id') or '<unknown>'}@"
        f"{entry.get('class_version') or '<unknown>'} "
        "references removed public tool "
        f"{entry.get('tool') or '<unknown>'}; it is skipped"
        for entry in list(details.get("references", []) or [])
    ]
