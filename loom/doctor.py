"""Stage 1 kinds-migration diagnostics shared by CLI and server."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

DOCTOR_SCHEMA_VERSION = 1
_KINDS_MIGRATION_VERSION_KEY = "schema_kinds_migration_version"
_KINDS_MIGRATION_MIGRATED_AT_KEY = "schema_kinds_migration_migrated_at"
_KINDS_BACKUP_NAME = "loom.db.pre-kinds.bak"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _count_yaml_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(
        1
        for file_path in path.rglob("*")
        if file_path.is_file() and file_path.suffix.lower() in {".yaml", ".yml"}
    )


def _fetch_scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return default
    if not row:
        return default
    return row[0]


def _fetch_meta(conn: sqlite3.Connection, key: str) -> str:
    value = _fetch_scalar(
        conn,
        "SELECT value FROM meta WHERE key=?",
        (key,),
        default="",
    )
    return str(value or "")


def _format_timestamp_display(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw


def _format_size(size_bytes: int) -> str:
    size = float(size_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return "0 B"


def _humanize_path(path: str) -> str:
    expanded = str(path or "")
    home = str(Path.home())
    if expanded == home:
        return "~"
    if expanded.startswith(home + os.sep):
        return "~/" + expanded[len(home) + 1:]
    return expanded


def _collect_migration_section(conn: sqlite3.Connection, db_path: Path) -> dict:
    version = int(_fetch_meta(conn, _KINDS_MIGRATION_VERSION_KEY) or 0)
    migrated_at = _fetch_meta(conn, _KINDS_MIGRATION_MIGRATED_AT_KEY)
    backup_path = db_path.with_name(_KINDS_BACKUP_NAME)
    backup_exists = backup_path.is_file()
    backup_size_bytes = backup_path.stat().st_size if backup_exists else 0
    backup_name = backup_path.name
    if backup_exists:
        backup_display = f"{backup_name} ({_format_size(backup_size_bytes)})"
    else:
        backup_display = f"{backup_name} (missing)"
    return {
        "schema_kinds_migration_version": version,
        "migrated_at": migrated_at,
        "migrated_at_display": _format_timestamp_display(migrated_at),
        "backup_path": str(backup_path),
        "backup_exists": backup_exists,
        "backup_size_bytes": backup_size_bytes,
        "backup_display": backup_display,
    }


def _collect_agents_section(conn: sqlite3.Connection) -> dict:
    total = int(_fetch_scalar(conn, "SELECT COUNT(*) FROM agents", default=0) or 0)
    kind_exists = _column_exists(conn, "agents", "kind")
    if not kind_exists:
        return {
            "total": total,
            "engineer": 0,
            "engineer_name": "",
            "worker": 0,
            "terminal": 0,
            "architect": 0,
            "unmigrated": total,
        }

    counts = {}
    for kind in ("engineer", "worker", "terminal", "architect"):
        counts[kind] = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM agents WHERE kind=?",
                (kind,),
                default=0,
            )
            or 0
        )
    unmigrated = int(
        _fetch_scalar(conn, "SELECT COUNT(*) FROM agents WHERE kind=''", default=0)
        or 0
    )
    engineer_name = ""
    if counts["engineer"] == 1:
        engineer_name = str(
            _fetch_scalar(
                conn,
                "SELECT name FROM agents WHERE kind='engineer' LIMIT 1",
                default="",
            )
            or ""
        )
    return {
        "total": total,
        "engineer": counts["engineer"],
        "engineer_name": engineer_name,
        "worker": counts["worker"],
        "terminal": counts["terminal"],
        "architect": counts["architect"],
        "unmigrated": unmigrated,
    }


def _collect_tasks_section(conn: sqlite3.Connection) -> dict:
    total = int(_fetch_scalar(conn, "SELECT COUNT(*) FROM board_tasks", default=0) or 0)
    if not _column_exists(conn, "board_tasks", "assigned_engineer_id"):
        return {
            "total": total,
            "assigned": 0,
            "unassigned": total,
        }
    assigned = int(
        _fetch_scalar(
            conn,
            "SELECT COUNT(*) FROM board_tasks WHERE assigned_engineer_id != ''",
            default=0,
        )
        or 0
    )
    return {
        "total": total,
        "assigned": assigned,
        "unassigned": max(0, total - assigned),
    }


def _collect_drift_section(conn: sqlite3.Connection) -> dict:
    role_exists = _column_exists(conn, "agents", "role")
    owner_exists = _column_exists(conn, "agents", "owner_engineer_id")
    assigned_exists = _column_exists(conn, "board_tasks", "assigned_engineer_id")
    legacy_task_owner_exists = _column_exists(conn, "board_tasks", "weaver_owner_id")

    if role_exists:
        template_role = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM agents "
                "WHERE (template != '' OR role != '') AND template != role",
                default=0,
            )
            or 0
        )
    else:
        template_role = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM agents WHERE template != ''",
                default=0,
            )
            or 0
        )

    if owner_exists:
        created_owner = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM agents "
                "WHERE (created_by_weaver_id != '' OR owner_engineer_id != '') "
                "AND created_by_weaver_id != owner_engineer_id",
                default=0,
            )
            or 0
        )
    else:
        created_owner = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM agents WHERE created_by_weaver_id != ''",
                default=0,
            )
            or 0
        )

    if not assigned_exists:
        task_owner = 0
    elif not legacy_task_owner_exists:
        # The current codebase never created this legacy board_tasks column on
        # disk, so stage 1c mirrors it opportunistically when present.
        task_owner = 0
    else:
        task_owner = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM board_tasks "
                "WHERE (weaver_owner_id != '' OR assigned_engineer_id != '') "
                "AND weaver_owner_id != assigned_engineer_id",
                default=0,
            )
            or 0
        )

    return {
        "agents_template_role": template_role,
        "agents_created_by_weaver_owner_engineer": created_owner,
        "board_tasks_weaver_owner_assigned_engineer": task_owner,
        "board_tasks_legacy_column_present": legacy_task_owner_exists,
    }


def _collect_roles_templates_section() -> dict:
    roles_dir = Path.home() / ".loom" / "roles"
    templates_dir = Path.home() / ".loom" / "agents"
    return {
        "roles_dir": str(roles_dir),
        "roles_file_count": _count_yaml_files(roles_dir),
        "templates_dir": str(templates_dir),
        "templates_file_count": _count_yaml_files(templates_dir),
    }


def _check_migration_version(report: dict) -> dict:
    actual = int(report["migration"]["schema_kinds_migration_version"] or 0)
    return {
        "name": "migration_version",
        "status": "pass" if actual >= 2 else "fail",
        "details": {"expected_min": 2, "actual": actual},
    }


def _check_unmigrated_agents(report: dict) -> dict:
    count = int(report["agents"]["unmigrated"] or 0)
    return {
        "name": "unmigrated_agents",
        "status": "pass" if count == 0 else "fail",
        "details": {"count": count},
    }


def _check_template_role_drift(report: dict) -> dict:
    count = int(report["drift"]["agents_template_role"] or 0)
    return {
        "name": "agents_template_role_drift",
        "status": "pass" if count == 0 else "fail",
        "details": {"count": count},
    }


def _check_created_owner_drift(report: dict) -> dict:
    count = int(report["drift"]["agents_created_by_weaver_owner_engineer"] or 0)
    return {
        "name": "agents_created_by_owner_drift",
        "status": "pass" if count == 0 else "fail",
        "details": {"count": count},
    }


def _check_task_owner_drift(report: dict) -> dict:
    count = int(report["drift"]["board_tasks_weaver_owner_assigned_engineer"] or 0)
    return {
        "name": "board_tasks_owner_drift",
        "status": "pass" if count == 0 else "fail",
        "details": {
            "count": count,
            "legacy_column_present": bool(
                report["drift"]["board_tasks_legacy_column_present"]
            ),
        },
    }


_DOCTOR_CHECKS = [
    _check_migration_version,
    _check_unmigrated_agents,
    _check_template_role_drift,
    _check_created_owner_drift,
    _check_task_owner_drift,
]


def build_doctor_report(conn: sqlite3.Connection, db_path: Path | str) -> dict:
    db_path = Path(db_path)
    report = {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "migration": _collect_migration_section(conn, db_path),
        "agents": _collect_agents_section(conn),
        "tasks": _collect_tasks_section(conn),
        "drift": _collect_drift_section(conn),
        "roles_templates": _collect_roles_templates_section(),
    }
    checks = [check(report) for check in _DOCTOR_CHECKS]
    report["checks"] = checks
    report["result"] = "pass" if all(c["status"] == "pass" for c in checks) else "fail"
    report["failed_checks"] = [c["name"] for c in checks if c["status"] != "pass"]
    return report


def build_doctor_report_for_db(db_path: Path | str) -> dict:
    db_path = Path(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return build_doctor_report(conn, db_path)
    finally:
        conn.close()


def format_doctor_report(report: dict) -> str:
    migration = report.get("migration", {})
    agents = report.get("agents", {})
    tasks = report.get("tasks", {})
    drift = report.get("drift", {})
    roles_templates = report.get("roles_templates", {})

    engineer_line = f"  engineer:    {int(agents.get('engineer', 0) or 0)}"
    engineer_name = str(agents.get("engineer_name", "") or "").strip()
    if engineer_name:
        engineer_line += f"   ({engineer_name})"

    roles_count = int(roles_templates.get("roles_file_count", 0) or 0)
    templates_count = int(roles_templates.get("templates_file_count", 0) or 0)

    lines = [
        "Loom doctor — stage 1 (kinds refactor)",
        "",
        "[migration]",
        "  schema_kinds_migration_version: "
        f"{migration.get('schema_kinds_migration_version', 0)}",
        "  migrated_at:                    "
        f"{migration.get('migrated_at_display') or migration.get('migrated_at', '')}",
        f"  backup:                         {migration.get('backup_display', '')}",
        "",
        "[agents]",
        f"  total:       {int(agents.get('total', 0) or 0)}",
        engineer_line,
        f"  worker:      {int(agents.get('worker', 0) or 0)}",
        f"  terminal:    {int(agents.get('terminal', 0) or 0)}",
        f"  architect:   {int(agents.get('architect', 0) or 0)}",
        f"  unmigrated:  {int(agents.get('unmigrated', 0) or 0)}   (rows with kind='')",
        "",
        "[tasks]",
        f"  total:       {int(tasks.get('total', 0) or 0)}",
        f"  assigned:    {int(tasks.get('assigned', 0) or 0)}",
        f"  unassigned:  {int(tasks.get('unassigned', 0) or 0)}",
        "",
        "[drift]",
        "  agents.template ↔ role:                 "
        f"{int(drift.get('agents_template_role', 0) or 0)}",
        "  agents.created_by_weaver_id ↔ owner_engineer_id: "
        f"{int(drift.get('agents_created_by_weaver_owner_engineer', 0) or 0)}",
        "  board_tasks.weaver_owner_id ↔ assigned_engineer_id: "
        f"{int(drift.get('board_tasks_weaver_owner_assigned_engineer', 0) or 0)}",
        "",
        "[roles/templates]",
        f"  {_humanize_path(str(roles_templates.get('roles_dir', '')))}: "
        f"{'(empty)' if roles_count == 0 else f'{roles_count} files'}",
        f"  {_humanize_path(str(roles_templates.get('templates_dir', '')))}: "
        f"{'(empty)' if templates_count == 0 else f'{templates_count} files'}",
        "",
        f"Result: {str(report.get('result', 'fail')).upper()}",
    ]
    failed_checks = list(report.get("failed_checks", []) or [])
    if failed_checks:
        lines.append("Failed checks: " + ", ".join(failed_checks))
    return "\n".join(lines)
