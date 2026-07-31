"""Task artifact identifier doctor diagnostics."""

from __future__ import annotations

import json
import sqlite3

from .artifacts import artifact_id_collisions


def collect_task_artifact_id_section(
    conn: sqlite3.Connection,
    *,
    table_exists,
    column_exists,
) -> dict:
    """Collect duplicate stored artifact ids without modifying evidence."""
    section = {
        "tasks_scanned": 0,
        "tasks_with_artifacts": 0,
        "collision_task_count": 0,
        "duplicate_id_count": 0,
        "collisions": [],
    }
    if (not table_exists(conn, "board_tasks")
            or not column_exists(conn, "board_tasks", "artifacts")):
        return section
    try:
        rows = conn.execute(
            "SELECT id, artifacts FROM board_tasks ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return section
    section["tasks_scanned"] = len(rows)
    for task_id, raw_artifacts in rows:
        try:
            artifacts = json.loads(raw_artifacts or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(artifacts, list) or not artifacts:
            continue
        section["tasks_with_artifacts"] += 1
        collisions = artifact_id_collisions(artifacts)
        if not collisions:
            continue
        section["collisions"].append({
            "task_id": str(task_id or ""),
            "ids": collisions,
        })
        section["duplicate_id_count"] += len(collisions)
    section["collision_task_count"] = len(section["collisions"])
    return section


def task_artifact_id_collision_warning(report: dict) -> dict | None:
    artifacts = report.get("task_artifact_ids", {}) or {}
    collisions = list(artifacts.get("collisions", []) or [])
    if not collisions:
        return None
    return {
        "name": "task_artifact_id_collisions",
        "status": "warn",
        "details": {
            "count": len(collisions),
            "collisions": collisions,
            "hint": (
                "historical artifact ids are preserved; use filename or path "
                "to disambiguate until records are deliberately repaired"
            ),
        },
    }


def format_task_artifact_id_section(section: dict) -> list[str]:
    return [
        "",
        "[task_artifact_ids]",
        "  tasks_scanned:       "
        f"{int(section.get('tasks_scanned', 0) or 0)}",
        "  collision_tasks:     "
        f"{int(section.get('collision_task_count', 0) or 0)}",
        "  duplicate_ids:       "
        f"{int(section.get('duplicate_id_count', 0) or 0)}",
    ]


def format_task_artifact_id_collision_warning(details: dict) -> str:
    collisions = details.get("collisions", []) or []
    summary = ", ".join(
        f"{entry.get('task_id')} ({', '.join(entry.get('ids', {}).keys())})"
        for entry in collisions[:7]
    )
    line = "  - task artifacts have duplicate ids; historical records were preserved"
    return line + (f": {summary}" if summary else "")
