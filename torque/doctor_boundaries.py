"""Read-only doctor diagnostics for persisted code-boundary evidence."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from .worktree_boundaries import code_boundary_done_status


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def collect_stranded_code_boundary_roots(conn: sqlite3.Connection) -> dict:
    """Report actionable roots whose canonical code-boundary Done gate blocks.

    All task rows are loaded, including archived children, because boundary
    evidence remains pipeline evidence after a child is archived. Completed or
    archived roots are excluded because their Done gate is no longer actionable.
    This collector is strictly read-only: rerouted/superseded evidence is
    reported, never repaired or stamped.
    """
    empty = {"root_count": 0, "root_ids": [], "roots": []}
    if not _table_exists(conn, "board_tasks"):
        return empty
    required = ("id", "lane", "pipeline_root_id", "worktree_boundary")
    if not all(_column_exists(conn, "board_tasks", column) for column in required):
        return empty
    try:
        rows = conn.execute(
            "SELECT id, lane, pipeline_root_id, worktree_boundary "
            "FROM board_tasks"
        ).fetchall()
    except sqlite3.OperationalError:
        return empty

    tasks = []
    by_id = {}
    for task_id, lane, pipeline_root_id, raw_boundary in rows:
        try:
            boundary = json.loads(raw_boundary or "{}")
        except (json.JSONDecodeError, TypeError):
            boundary = {}
        if not isinstance(boundary, dict):
            boundary = {}
        task = SimpleNamespace(
            id=str(task_id or ""),
            lane=str(lane or ""),
            pipeline_root_id=str(pipeline_root_id or ""),
            worktree_boundary=boundary,
        )
        tasks.append(task)
        by_id[task.id] = task

    chains = {}
    for task in tasks:
        root_id = task.pipeline_root_id or task.id
        chains.setdefault(root_id, []).append(task)

    stranded = []
    for root_id in sorted(chains):
        root = by_id.get(root_id)
        if root is None or root.lane.strip().lower() in {"done", "archived"}:
            continue
        gate = code_boundary_done_status(chains[root_id])
        if gate["eligible"]:
            continue
        stranded.append({
            "root_id": root_id,
            "root_lane": root.lane,
            "blocking": list(gate.get("blocking", []) or []),
        })
    return {
        "root_count": len(stranded),
        "root_ids": [entry["root_id"] for entry in stranded],
        "roots": stranded,
    }


def stranded_code_boundary_roots_warning(report: dict) -> dict | None:
    section = report.get("stranded_code_boundary_roots", {}) or {}
    if not int(section.get("root_count", 0) or 0):
        return None
    return {
        "name": "stranded_code_boundary_roots",
        "status": "warn",
        "details": section,
    }


def format_stranded_code_boundary_roots_section(section: dict) -> list[str]:
    section = section or {}
    return [
        "[stranded_code_boundary_roots]",
        f"  root_count:  {int(section.get('root_count', 0) or 0)}",
        "  root_ids:    "
        f"{', '.join(list(section.get('root_ids', []) or [])) or '(none)'}",
    ]


def format_stranded_code_boundary_roots_warning(details: dict) -> str:
    details = details or {}
    root_ids = ", ".join(details.get("root_ids", []) or [])
    return (
        "  - actionable pipeline roots are blocked by persisted "
        "code-boundary evidence: "
        f"roots={int(details.get('root_count', 0) or 0)}"
        + (f" ({root_ids})" if root_ids else "")
        + "; report only—review merge/reroute evidence manually"
    )
