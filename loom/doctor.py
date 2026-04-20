"""Kinds-refactor diagnostics shared by CLI and server."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


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


def _iter_named_yaml_paths(root_dir: Path) -> list[tuple[str, Path]]:
    if not root_dir.is_dir():
        return []
    entries = []
    for file_path in sorted(root_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        rel = file_path.relative_to(root_dir)
        entries.append((str(rel.with_suffix("")), file_path))
    return entries


def _load_yaml_dict(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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


def _collect_engineers_section(conn: sqlite3.Connection) -> dict:
    if not _column_exists(conn, "agents", "kind"):
        return {
            "total": 0,
            "default_engineer_id": "",
            "default_engineer_name": "",
            "engineers": [],
            "binding_env_mismatches": [],
        }

    history_created_at = {}
    if _table_exists(conn, "agent_history"):
        try:
            rows = conn.execute(
                "SELECT id, MIN(created_at) FROM agent_history GROUP BY id"
            ).fetchall()
            history_created_at = {
                str(agent_id or ""): created_at
                for agent_id, created_at in rows
                if str(agent_id or "").strip()
            }
        except sqlite3.OperationalError:
            history_created_at = {}

    worker_counts = {}
    if _column_exists(conn, "agents", "owner_engineer_id"):
        try:
            worker_rows = conn.execute(
                "SELECT owner_engineer_id, COUNT(*) FROM agents "
                "WHERE cell_type='agent' AND kind='worker' AND owner_engineer_id != '' "
                "GROUP BY owner_engineer_id"
            ).fetchall()
            worker_counts = {
                str(engineer_id or ""): int(count or 0)
                for engineer_id, count in worker_rows
                if str(engineer_id or "").strip()
            }
        except sqlite3.OperationalError:
            worker_counts = {}

    task_counts = {}
    if _column_exists(conn, "board_tasks", "assigned_engineer_id"):
        try:
            task_rows = conn.execute(
                "SELECT assigned_engineer_id, COUNT(*) FROM board_tasks "
                "WHERE assigned_engineer_id != '' "
                "GROUP BY assigned_engineer_id"
            ).fetchall()
            task_counts = {
                str(engineer_id or ""): int(count or 0)
                for engineer_id, count in task_rows
                if str(engineer_id or "").strip()
            }
        except sqlite3.OperationalError:
            task_counts = {}

    engineers = []
    try:
        rows = conn.execute(
            "SELECT rowid, id, name, slug, persistent FROM agents "
            "WHERE cell_type='agent' AND kind='engineer' "
            "ORDER BY rowid"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for rowid, agent_id, name, slug, persistent in rows:
        agent_id = str(agent_id or "")
        engineers.append(
            {
                "id": agent_id,
                "name": str(name or ""),
                "slug": str(slug or ""),
                "persistent": int(persistent or 0),
                "worker_count": int(worker_counts.get(agent_id, 0) or 0),
                "task_count": int(task_counts.get(agent_id, 0) or 0),
                "_rowid": int(rowid or 0),
                "_created_at": history_created_at.get(agent_id),
            }
        )

    def _sort_key(entry: dict):
        created_at = entry.get("_created_at")
        if isinstance(created_at, (int, float)) and created_at:
            return (0, float(created_at), int(entry.get("_rowid", 0) or 0))
        return (1, int(entry.get("_rowid", 0) or 0))

    default_engineer_id = ""
    default_engineer_name = ""
    if len(engineers) == 1:
        default_engineer_id = engineers[0]["id"]
        default_engineer_name = engineers[0]["name"]
    elif engineers:
        weaver_named = [
            engineer for engineer in engineers
            if engineer.get("name", "") == "Weaver"
        ]
        candidates = weaver_named or engineers
        candidates = sorted(candidates, key=_sort_key)
        default_engineer_id = candidates[0]["id"]
        default_engineer_name = candidates[0]["name"]

    binding_env_mismatches = []
    for engineer in engineers:
        actual = str(engineer.get("loom_engineer_id", engineer["id"]) or "")
        if actual != engineer["id"]:
            binding_env_mismatches.append(
                {
                    "id": engineer["id"],
                    "name": engineer["name"],
                    "expected": engineer["id"],
                    "actual": actual,
                }
            )

    for engineer in engineers:
        engineer.pop("_rowid", None)
        engineer.pop("_created_at", None)

    return {
        "total": len(engineers),
        "default_engineer_id": default_engineer_id,
        "default_engineer_name": default_engineer_name,
        "engineers": engineers,
        "binding_env_mismatches": binding_env_mismatches,
    }


def _collect_tasks_section(conn: sqlite3.Connection, *, engineer_count: int) -> dict:
    total = int(_fetch_scalar(conn, "SELECT COUNT(*) FROM board_tasks", default=0) or 0)
    if not _column_exists(conn, "board_tasks", "assigned_engineer_id"):
        return {
            "total": total,
            "assigned": 0,
            "unassigned": total,
            "unassigned_when_engineer_present": total if engineer_count >= 1 else 0,
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
        "unassigned_when_engineer_present": (
            max(0, total - assigned) if engineer_count >= 1 else 0
        ),
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


def _collect_roles_section() -> dict:
    roles_dir = Path.home() / ".loom" / "roles"
    legacy_templates_dir = Path.home() / ".loom" / "agents"
    role_entries = _iter_named_yaml_paths(roles_dir)
    legacy_entries = _iter_named_yaml_paths(legacy_templates_dir)
    shadowed = sorted({name for name, _path in role_entries} & {
        name for name, _path in legacy_entries
    })
    roles_with_preamble = 0
    roles_with_priorities = 0
    for _name, path in role_entries:
        data = _load_yaml_dict(path)
        if str(data.get("preamble", "") or "").strip():
            roles_with_preamble += 1
        priorities = data.get("priorities")
        if isinstance(priorities, list) and any(
            str(item or "").strip() for item in priorities
        ):
            roles_with_priorities += 1
    return {
        "roles_dir": str(roles_dir),
        "roles_file_count": len(role_entries),
        "legacy_templates_dir": str(legacy_templates_dir),
        "legacy_templates_file_count": len(legacy_entries),
        "shadowed_legacy_templates": len(shadowed),
        "shadowed_legacy_slugs": shadowed,
        "roles_with_preamble": roles_with_preamble,
        "roles_with_priorities": roles_with_priorities,
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


def _warn_unassigned_tasks_when_engineer_present(report: dict) -> dict | None:
    engineer_count = int(report["agents"]["engineer"] or 0)
    count = int(report["tasks"]["unassigned_when_engineer_present"] or 0)
    if engineer_count < 1 or count <= 0:
        return None
    return {
        "name": "unassigned_tasks_when_engineer_present",
        "status": "warn",
        "details": {
            "count": count,
            "engineer_count": engineer_count,
        },
    }


def _warn_shadowed_legacy_templates(report: dict) -> dict | None:
    roles = report.get("roles", {}) or {}
    count = int(roles.get("shadowed_legacy_templates", 0) or 0)
    if count <= 0:
        return None
    return {
        "name": "shadowed_legacy_templates",
        "status": "warn",
        "details": {
            "count": count,
            "slugs": list(roles.get("shadowed_legacy_slugs", []) or []),
            "hint": (
                "legacy template shadowed by new role; "
                "consider migrating the legacy file"
            ),
        },
    }


def _warn_no_engineers(report: dict) -> dict | None:
    engineers = report.get("engineers", {}) or {}
    total = int(engineers.get("total", 0) or 0)
    if total != 0:
        return None
    return {
        "name": "no_engineers",
        "status": "warn",
        "details": {
            "count": 0,
            "hint": (
                "no engineer exists; weaver_* tool aliases will fail until one is created"
            ),
        },
    }


def _warn_ambiguous_default_engineer(report: dict) -> dict | None:
    engineers = report.get("engineers", {}) or {}
    entries = list(engineers.get("engineers", []) or [])
    if len(entries) <= 1:
        return None
    if any(str(entry.get("name", "") or "") == "Weaver" for entry in entries):
        return None
    return {
        "name": "ambiguous_default_engineer_routing",
        "status": "warn",
        "details": {
            "count": len(entries),
            "default_engineer_id": str(
                engineers.get("default_engineer_id", "") or ""
            ),
            "default_engineer_name": str(
                engineers.get("default_engineer_name", "") or ""
            ),
            "hint": (
                "multiple engineers but no canonical 'Weaver' for default routing; "
                "weaver_* aliases will pick the earliest by creation order"
            ),
        },
    }


def _warn_engineer_binding_env_mismatch(report: dict) -> dict | None:
    engineers = report.get("engineers", {}) or {}
    mismatches = list(engineers.get("binding_env_mismatches", []) or [])
    if not mismatches:
        return None
    return {
        "name": "engineer_binding_env_mismatch",
        "status": "warn",
        "details": {
            "count": len(mismatches),
            "mismatches": mismatches,
        },
    }


_DOCTOR_CHECKS = [
    _check_migration_version,
    _check_unmigrated_agents,
    _check_template_role_drift,
    _check_created_owner_drift,
    _check_task_owner_drift,
]

_DOCTOR_WARNINGS = [
    _warn_unassigned_tasks_when_engineer_present,
    _warn_shadowed_legacy_templates,
    _warn_no_engineers,
    _warn_ambiguous_default_engineer,
    _warn_engineer_binding_env_mismatch,
]


def build_doctor_report(conn: sqlite3.Connection, db_path: Path | str) -> dict:
    db_path = Path(db_path)
    agents = _collect_agents_section(conn)
    tasks = _collect_tasks_section(conn, engineer_count=int(agents["engineer"] or 0))
    report = {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "migration": _collect_migration_section(conn, db_path),
        "agents": agents,
        "tasks": tasks,
        "drift": _collect_drift_section(conn),
        "roles": _collect_roles_section(),
    }
    report["roles_templates"] = {
        "roles_dir": report["roles"]["roles_dir"],
        "roles_file_count": report["roles"]["roles_file_count"],
        "templates_dir": report["roles"]["legacy_templates_dir"],
        "templates_file_count": report["roles"]["legacy_templates_file_count"],
    }
    report["engineers"] = _collect_engineers_section(conn)
    checks = [check(report) for check in _DOCTOR_CHECKS]
    warnings = [warning for fn in _DOCTOR_WARNINGS if (warning := fn(report))]
    report["checks"] = checks
    report["warnings"] = warnings
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
    engineers = report.get("engineers", {}) or {}
    tasks = report.get("tasks", {})
    drift = report.get("drift", {})
    roles = report.get("roles", {}) or {}
    warnings = list(report.get("warnings", []) or [])

    engineer_line = f"  engineer:    {int(agents.get('engineer', 0) or 0)}"
    engineer_name = str(agents.get("engineer_name", "") or "").strip()
    if engineer_name:
        engineer_line += f"   ({engineer_name})"

    roles_count = int(roles.get("roles_file_count", 0) or 0)
    legacy_templates_count = int(
        roles.get("legacy_templates_file_count", 0) or 0
    )
    default_engineer_name = str(
        engineers.get("default_engineer_name", "") or ""
    ).strip()
    default_engineer_id = str(
        engineers.get("default_engineer_id", "") or ""
    ).strip()
    default_engineer_display = "<none>"
    if default_engineer_id:
        default_engineer_display = (
            f"{default_engineer_name or default_engineer_id} "
            f"(id={default_engineer_id})"
        )

    lines = [
        "Loom doctor — kinds refactor",
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
        "[engineers]",
        f"  total:                        {int(engineers.get('total', 0) or 0)}",
        f"  default (weaver_* routing):   {default_engineer_display}",
        "  engineers:",
    ]
    for engineer in list(engineers.get("engineers", []) or []):
        lines.append(
            "    - "
            f"{engineer.get('name', '')}  "
            f"kind=engineer  "
            f"persistent={int(engineer.get('persistent', 0) or 0)}  "
            f"workers={int(engineer.get('worker_count', 0) or 0)}  "
            f"tasks={int(engineer.get('task_count', 0) or 0)}"
        )
    lines.extend([
        "",
        "[tasks]",
        f"  total:       {int(tasks.get('total', 0) or 0)}",
        f"  assigned:    {int(tasks.get('assigned', 0) or 0)}",
        f"  unassigned:  {int(tasks.get('unassigned', 0) or 0)}",
        "  unassigned_when_engineer_present: "
        f"{int(tasks.get('unassigned_when_engineer_present', 0) or 0)}",
        "",
        "[drift]",
        "  agents.template ↔ role:                 "
        f"{int(drift.get('agents_template_role', 0) or 0)}",
        "  agents.created_by_weaver_id ↔ owner_engineer_id: "
        f"{int(drift.get('agents_created_by_weaver_owner_engineer', 0) or 0)}",
        "  board_tasks.weaver_owner_id ↔ assigned_engineer_id: "
        f"{int(drift.get('board_tasks_weaver_owner_assigned_engineer', 0) or 0)}",
        "",
        "[roles]",
        "  roles_dir:                      "
        f"{_humanize_path(str(roles.get('roles_dir', '')))} ({roles_count} files)",
        "  legacy_templates_dir:           "
        f"{_humanize_path(str(roles.get('legacy_templates_dir', '')))} "
        f"({legacy_templates_count} files)",
        "  shadowed_legacy_templates:      "
        f"{int(roles.get('shadowed_legacy_templates', 0) or 0)}",
        "  roles_with_preamble:            "
        f"{int(roles.get('roles_with_preamble', 0) or 0)}",
        "  roles_with_priorities:          "
        f"{int(roles.get('roles_with_priorities', 0) or 0)}",
        "",
        (
            "Result: PASS (with warnings)"
            if str(report.get("result", "fail")) == "pass" and warnings
            else f"Result: {str(report.get('result', 'fail')).upper()}"
        ),
    ])
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            name = str(warning.get("name", "") or "")
            details = warning.get("details", {}) or {}
            if name == "unassigned_tasks_when_engineer_present":
                lines.append(
                    "  - engineer present but unassigned tasks remain: "
                    f"{details.get('count', 0)}"
                )
            elif name == "shadowed_legacy_templates":
                slugs = ", ".join(details.get("slugs", []) or [])
                line = (
                    "  - legacy template shadowed by new role; "
                    "consider migrating the legacy file"
                )
                if slugs:
                    line += f": {slugs}"
                lines.append(line)
            elif name == "no_engineers":
                lines.append(
                    "  - no engineer exists; weaver_* tool aliases will fail until one is created"
                )
            elif name == "ambiguous_default_engineer_routing":
                lines.append(
                    "  - multiple engineers but no canonical 'Weaver' for default routing; "
                    "weaver_* aliases will pick the earliest by creation order"
                )
            elif name == "engineer_binding_env_mismatch":
                mismatches = details.get("mismatches", []) or []
                summary = ", ".join(
                    f"{m.get('name') or m.get('id')} "
                    f"(expected={m.get('expected')}, actual={m.get('actual')})"
                    for m in mismatches
                )
                base = "  - engineer LOOM_ENGINEER_ID mismatch detected"
                if summary:
                    base += f": {summary}"
                lines.append(base)
            else:
                lines.append(f"  - {name}")
    failed_checks = list(report.get("failed_checks", []) or [])
    if failed_checks:
        lines.append("Failed checks:")
        for check in report.get("checks", []) or []:
            if check.get("status") == "pass":
                continue
            name = str(check.get("name", "") or "")
            details = check.get("details", {}) or {}
            if name == "migration_version":
                lines.append(
                    "  - migration version is "
                    f"{details.get('actual', 0)} "
                    f"(expected >= {details.get('expected_min', 2)})"
                )
            elif name == "unmigrated_agents":
                lines.append(
                    "  - unmigrated agent rows: "
                    f"{details.get('count', 0)}"
                )
            elif name == "agents_template_role_drift":
                lines.append(
                    "  - agents.template ↔ role drift: "
                    f"{details.get('count', 0)}"
                )
            elif name == "agents_created_by_owner_drift":
                lines.append(
                    "  - agents.created_by_weaver_id ↔ owner_engineer_id drift: "
                    f"{details.get('count', 0)}"
                )
            elif name == "board_tasks_owner_drift":
                lines.append(
                    "  - board_tasks.weaver_owner_id ↔ assigned_engineer_id drift: "
                    f"{details.get('count', 0)}"
                )
            else:
                lines.append(f"  - {name}")
    return "\n".join(lines)
