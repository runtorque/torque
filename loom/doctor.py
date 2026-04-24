"""Kinds-refactor diagnostics shared by CLI and server."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import yaml

DOCTOR_SCHEMA_VERSION = 2
_KINDS_MIGRATION_VERSION_KEY = "schema_kinds_migration_version"
_KINDS_MIGRATION_MIGRATED_AT_KEY = "schema_kinds_migration_migrated_at"
_KINDS_BACKUP_NAME = "loom.db.pre-kinds.bak"
_SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60
_ONE_DAY_SECONDS = 24 * 60 * 60
_MCP_HEALTH_WINDOW_SECONDS = 60 * 60
# WorktreeManager currently derives the default branch suffix from ``cell.id[:7]``.
# Accept 6+ hex tails so the doctor stays tolerant of older/manual fixtures while
# still rejecting very short manual tails like ``-bad``.
_WORKTREE_SHORT_ID_RE = r"[a-f0-9]{6,}"
_NAMESPACED_WORKTREE_RE = re.compile(
    rf"^loom/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*-{_WORKTREE_SHORT_ID_RE}$"
)
_USER_WORKTREE_RE = re.compile(
    rf"^loom/user/[a-z0-9][a-z0-9-]*-{_WORKTREE_SHORT_ID_RE}$"
)
_LEGACY_WORKTREE_RE = re.compile(
    rf"^loom/[a-z0-9][a-z0-9-]*-{_WORKTREE_SHORT_ID_RE}$"
)


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


def _find_project_config_dir(base_dir: str = "", leaf: str = "roles") -> Path | None:
    d = Path(os.path.expanduser(base_dir) if base_dir else os.getcwd())
    if not d.is_dir():
        d = Path(os.getcwd())
    global_dir = (Path.home() / ".loom" / leaf).resolve()
    for _ in range(20):
        candidate = d / ".loom" / leaf
        if candidate.is_dir():
            if candidate.resolve() != global_dir:
                return candidate
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


def _collect_ignored_legacy_template_files(base_dir: str = "") -> list[dict]:
    entries = []
    scopes = []
    project_legacy_dir = _find_project_config_dir(base_dir, "agents")
    if project_legacy_dir:
        scopes.append({
            "legacy_dir": project_legacy_dir,
            "roles_dir": _find_project_config_dir(base_dir, "roles")
            or (project_legacy_dir.parent / "roles"),
        })
    global_legacy_dir = Path.home() / ".loom" / "agents"
    if global_legacy_dir.is_dir():
        scopes.append({
            "legacy_dir": global_legacy_dir,
            "roles_dir": Path.home() / ".loom" / "roles",
        })

    seen_dirs: set[str] = set()
    for scope in scopes:
        legacy_dir = Path(scope["legacy_dir"])
        legacy_key = str(legacy_dir.resolve())
        if legacy_key in seen_dirs:
            continue
        seen_dirs.add(legacy_key)
        roles_dir = Path(scope["roles_dir"])
        role_names = {name for name, _path in _iter_named_yaml_paths(roles_dir)}
        for name, path in _iter_named_yaml_paths(legacy_dir):
            if name in role_names:
                continue
            entries.append({
                "name": name,
                "path": str(path),
                "roles_dir": str(roles_dir),
            })
    return entries


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
            "empty_kind_with_task_history": [],
            "empty_kind_with_task_history_count": 0,
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
    empty_kind_with_task_history = _collect_empty_kind_agents_with_task_history(
        conn
    )
    return {
        "total": total,
        "engineer": counts["engineer"],
        "engineer_name": engineer_name,
        "worker": counts["worker"],
        "terminal": counts["terminal"],
        "architect": counts["architect"],
        "unmigrated": unmigrated,
        "empty_kind_with_task_history": empty_kind_with_task_history,
        "empty_kind_with_task_history_count": len(empty_kind_with_task_history),
    }


def _collect_empty_kind_agents_with_task_history(conn: sqlite3.Connection) -> list[dict]:
    if not _column_exists(conn, "agents", "kind"):
        return []
    tasks_dispatched_sql = (
        "tasks_dispatched"
        if _column_exists(conn, "agents", "tasks_dispatched")
        else "0 AS tasks_dispatched"
    )
    try:
        rows = conn.execute(
            f"SELECT id, name, slug, group_name, {tasks_dispatched_sql} "
            "FROM agents "
            "WHERE cell_type='agent' AND TRIM(COALESCE(kind, '')) = '' "
            "ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    history_totals: dict[str, int] = {}
    if _table_exists(conn, "agent_history"):
        try:
            for agent_id, total_tasks in conn.execute(
                "SELECT id, COALESCE(total_tasks, 0) FROM agent_history"
            ).fetchall():
                history_totals[str(agent_id or "")] = int(total_tasks or 0)
        except sqlite3.OperationalError:
            history_totals = {}

    task_counts: dict[str, int] = {}
    if _table_exists(conn, "agent_tasks"):
        try:
            for agent_id, count in conn.execute(
                "SELECT agent_id, COUNT(*) FROM agent_tasks GROUP BY agent_id"
            ).fetchall():
                task_counts[str(agent_id or "")] = int(count or 0)
        except sqlite3.OperationalError:
            task_counts = {}

    entries = []
    for agent_id, name, slug, group_name, tasks_dispatched in rows:
        agent_id = str(agent_id or "")
        task_history = max(
            int(tasks_dispatched or 0),
            history_totals.get(agent_id, 0),
            task_counts.get(agent_id, 0),
        )
        if task_history <= 0:
            continue
        entries.append({
            "id": agent_id,
            "name": str(name or ""),
            "slug": str(slug or ""),
            "group": str(group_name or ""),
            "task_history": task_history,
        })
    return entries


def _collect_engineers_section(conn: sqlite3.Connection) -> dict:
    if not _column_exists(conn, "agents", "kind"):
        return {
            "total": 0,
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
        "engineers": engineers,
        "binding_env_mismatches": binding_env_mismatches,
    }


def _collect_architects_section(conn: sqlite3.Connection) -> dict:
    if not _column_exists(conn, "agents", "kind"):
        return {
            "total": 0,
            "architects": [],
            "invalid_hired_by_architect": [],
            "dangling_decisions": [],
        }

    hired_counts = {}
    if _column_exists(conn, "agents", "hired_by_architect_id"):
        try:
            rows = conn.execute(
                "SELECT hired_by_architect_id, COUNT(*) FROM agents "
                "WHERE cell_type='agent' AND kind='engineer' "
                "AND hired_by_architect_id != '' "
                "GROUP BY hired_by_architect_id"
            ).fetchall()
            hired_counts = {
                str(architect_id or ""): int(count or 0)
                for architect_id, count in rows
                if str(architect_id or "").strip()
            }
        except sqlite3.OperationalError:
            hired_counts = {}

    architects = []
    invalid_hired_by_architect = []
    architect_binding_exists = _column_exists(conn, "agents", "hired_by_architect_id")
    try:
        if architect_binding_exists:
            rows = conn.execute(
                "SELECT rowid, id, name, slug, hired_by_architect_id "
                "FROM agents WHERE cell_type='agent' AND kind='architect' "
                "ORDER BY rowid"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rowid, id, name, slug, '' AS hired_by_architect_id "
                "FROM agents WHERE cell_type='agent' AND kind='architect' "
                "ORDER BY rowid"
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for _rowid, architect_id, name, slug, hired_by_architect_id in rows:
        architect_id = str(architect_id or "")
        invalid_binding = str(hired_by_architect_id or "").strip()
        entry = {
            "id": architect_id,
            "name": str(name or ""),
            "slug": str(slug or ""),
            "decision_count": 0,
            "hired_engineer_count": int(hired_counts.get(architect_id, 0) or 0),
        }
        architects.append(entry)
        if invalid_binding:
            invalid_hired_by_architect.append({
                "id": architect_id,
                "name": entry["name"],
                "slug": entry["slug"],
                "hired_by_architect_id": invalid_binding,
            })

    architect_ids = {entry["id"] for entry in architects if entry.get("id")}
    dangling_decisions = []
    if _table_exists(conn, "decisions"):
        try:
            rows = conn.execute(
                "SELECT id, architect_id, archived FROM decisions"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for decision_id, architect_id, archived in rows:
            architect_id = str(architect_id or "").strip()
            if not architect_id:
                continue
            if not int(archived or 0):
                for architect in architects:
                    if architect["id"] == architect_id:
                        architect["decision_count"] += 1
                        break
            # Archived decisions are intentionally retained after architect
            # deletion; only warn on active rows that still point at a missing
            # architect.
            if int(archived or 0):
                continue
            if architect_id not in architect_ids:
                dangling_decisions.append({
                    "id": str(decision_id or ""),
                    "architect_id": architect_id,
                })

    return {
        "total": len(architects),
        "architects": architects,
        "invalid_hired_by_architect": invalid_hired_by_architect,
        "dangling_decisions": dangling_decisions,
    }


def _collect_pending_hires_section(
    conn: sqlite3.Connection,
    *,
    architect_names: dict[str, str] | None = None,
) -> dict:
    section = {
        "pending": 0,
        "approved_recent": 0,
        "rejected_recent": 0,
        "stale_pending": 0,
        "stale_pending_hires": [],
    }
    if not _table_exists(conn, "pending_hires"):
        return section

    architect_names = architect_names or {}
    now_ts = int(datetime.now().timestamp())
    recent_cutoff = now_ts - _SEVEN_DAYS_SECONDS
    stale_cutoff = now_ts - _ONE_DAY_SECONDS
    try:
        rows = conn.execute(
            "SELECT id, architect_id, status, created_at, resolved_at "
            "FROM pending_hires"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    stale_entries = []
    for hire_id, architect_id, status, created_at, resolved_at in rows:
        hire_id = str(hire_id or "")
        architect_id = str(architect_id or "").strip()
        status = str(status or "").strip()
        created_at = int(created_at or 0)
        resolved_at = int(resolved_at or 0)
        if status == "pending":
            section["pending"] += 1
            if created_at and created_at < stale_cutoff:
                age_hours = max(1, int((now_ts - created_at) // 3600))
                architect_name = str(architect_names.get(architect_id, "") or "")
                stale_entries.append({
                    "id": hire_id,
                    "architect_id": architect_id,
                    "architect_name": architect_name,
                    "age_hours": age_hours,
                })
        elif status == "approved" and resolved_at >= recent_cutoff:
            section["approved_recent"] += 1
        elif status == "rejected" and resolved_at >= recent_cutoff:
            section["rejected_recent"] += 1

    section["stale_pending_hires"] = stale_entries
    section["stale_pending"] = len(stale_entries)
    return section


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


def _collect_task_aliases_section(conn: sqlite3.Connection) -> dict:
    section = {
        "total": 0,
        "missing_canonical_count": 0,
        "literal_collision_count": 0,
        "archived_literal_collision_count": 0,
        "missing_canonical": [],
        "literal_collisions": [],
        "strategy": "alias_precedence_archived_literals_hidden",
    }
    if not _table_exists(conn, "task_id_aliases"):
        return section

    try:
        alias_rows = conn.execute(
            "SELECT legacy_id, task_id FROM task_id_aliases ORDER BY legacy_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return section
    section["total"] = len(alias_rows)
    if not alias_rows or not _table_exists(conn, "board_tasks"):
        return section

    task_rows = {}
    try:
        for row in conn.execute(
            "SELECT id, task, lane, archived_at FROM board_tasks"
        ).fetchall():
            task_rows[str(row[0] or "")] = {
                "id": str(row[0] or ""),
                "title": str(row[1] or ""),
                "lane": str(row[2] or ""),
                "archived_at": str(row[3] or ""),
            }
    except sqlite3.OperationalError:
        return section

    for legacy_id, task_id in alias_rows:
        legacy = str(legacy_id or "").strip()
        canonical = str(task_id or "").strip()
        if not legacy or not canonical:
            continue
        legacy_row = task_rows.get(legacy)
        canonical_row = task_rows.get(canonical)
        if legacy_row and legacy != canonical:
            entry = {
                "legacy_id": legacy,
                "task_id": canonical,
                "legacy_title": legacy_row.get("title", ""),
                "legacy_lane": legacy_row.get("lane", ""),
                "legacy_archived_at": legacy_row.get("archived_at", ""),
                "canonical_exists": bool(canonical_row),
            }
            section["literal_collisions"].append(entry)
            if legacy_row.get("lane") == "Archived" or legacy_row.get("archived_at"):
                section["archived_literal_collision_count"] += 1
        if not canonical_row:
            section["missing_canonical"].append({
                "legacy_id": legacy,
                "task_id": canonical,
                "legacy_row_exists": bool(legacy_row),
                "legacy_title": (legacy_row or {}).get("title", ""),
                "legacy_lane": (legacy_row or {}).get("lane", ""),
                "legacy_archived_at": (legacy_row or {}).get("archived_at", ""),
            })

    section["literal_collision_count"] = len(section["literal_collisions"])
    section["missing_canonical_count"] = len(section["missing_canonical"])
    return section


def _collect_mcp_health_section(conn: sqlite3.Connection) -> dict:
    window = _MCP_HEALTH_WINDOW_SECONDS
    since = time.time() - window
    section = {
        "recent_window_seconds": window,
        "since": since,
        "totals": {},
        "surfaces": {},
        "pending_failed_writes": 0,
    }
    if not _table_exists(conn, "mcp_health_events"):
        return section
    try:
        rows = conn.execute(
            "SELECT surface, tool_name, event, COUNT(*) "
            "FROM mcp_health_events WHERE timestamp >= ? "
            "GROUP BY surface, tool_name, event "
            "ORDER BY surface, tool_name, event",
            (since,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for surface, tool_name, event, count in rows:
        surface = str(surface or "mcp")
        tool_name = str(tool_name or "")
        event = str(event or "")
        count = int(count or 0)
        section["totals"][event] = section["totals"].get(event, 0) + count
        surface_entry = section["surfaces"].setdefault(
            surface,
            {"events": {}, "tools": {}},
        )
        surface_entry["events"][event] = (
            surface_entry["events"].get(event, 0) + count
        )
        if tool_name:
            tool_entry = surface_entry["tools"].setdefault(tool_name, {})
            tool_entry[event] = tool_entry.get(event, 0) + count
    if _table_exists(conn, "failed_writes"):
        section["pending_failed_writes"] = int(
            _fetch_scalar(conn, "SELECT COUNT(*) FROM failed_writes", default=0)
            or 0
        )
    return section


def _classify_worker_worktree_branch(branch: str) -> str:
    branch = str(branch or "").strip()
    if not branch:
        return ""
    if _USER_WORKTREE_RE.match(branch) or _NAMESPACED_WORKTREE_RE.match(branch):
        return "namespaced"
    if _LEGACY_WORKTREE_RE.match(branch):
        return "legacy"
    return "nonconforming"


def _collect_worktrees_section(conn: sqlite3.Connection) -> dict:
    section = {
        "total_worker_branches": 0,
        "namespaced": 0,
        "legacy": 0,
        "nonconforming": 0,
        "nonconforming_branches": [],
    }
    if not _column_exists(conn, "agents", "kind"):
        return section

    try:
        rows = conn.execute(
            "SELECT id, name, slug, worktree_branch FROM agents "
            "WHERE cell_type='agent' AND kind='worker' AND worktree_branch != '' "
            "ORDER BY rowid"
        ).fetchall()
    except sqlite3.OperationalError:
        return section

    for agent_id, name, slug, worktree_branch in rows:
        category = _classify_worker_worktree_branch(str(worktree_branch or ""))
        if not category:
            continue
        section["total_worker_branches"] += 1
        if category == "namespaced":
            section["namespaced"] += 1
        elif category == "legacy":
            section["legacy"] += 1
        else:
            section["nonconforming"] += 1
            section["nonconforming_branches"].append({
                "id": str(agent_id or ""),
                "name": str(name or ""),
                "slug": str(slug or ""),
                "branch": str(worktree_branch or ""),
            })

    return section


def _collect_drift_section(conn: sqlite3.Connection) -> dict:
    role_exists = _column_exists(conn, "agents", "role")
    owner_exists = _column_exists(conn, "agents", "owner_engineer_id")
    assigned_exists = _column_exists(conn, "board_tasks", "assigned_engineer_id")
    legacy_task_owner_exists = _column_exists(conn, "board_tasks", "engineer_owner_id")

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
                "WHERE (created_by_engineer_id != '' OR owner_engineer_id != '') "
                "AND created_by_engineer_id != owner_engineer_id",
                default=0,
            )
            or 0
        )
    else:
        created_owner = int(
            _fetch_scalar(
                conn,
                "SELECT COUNT(*) FROM agents WHERE created_by_engineer_id != ''",
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
                "WHERE (engineer_owner_id != '' OR assigned_engineer_id != '') "
                "AND engineer_owner_id != assigned_engineer_id",
                default=0,
            )
            or 0
        )

    return {
        "agents_template_role": template_role,
        "agents_created_by_engineer_owner_engineer": created_owner,
        "board_tasks_engineer_owner_assigned_engineer": task_owner,
        "board_tasks_legacy_column_present": legacy_task_owner_exists,
    }


def _collect_roles_section() -> dict:
    roles_dir = Path.home() / ".loom" / "roles"
    role_entries = _iter_named_yaml_paths(roles_dir)
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
        "roles_with_preamble": roles_with_preamble,
        "roles_with_priorities": roles_with_priorities,
    }


def _collect_stage_6_cleanup_section(
    conn: sqlite3.Connection,
    *,
    base_dir: str = "",
) -> dict:
    ignored_legacy_files = _collect_ignored_legacy_template_files(base_dir)
    legacy_columns_present = any([
        _column_exists(conn, "agents", "template"),
        _column_exists(conn, "agents", "created_by_engineer_id"),
        _column_exists(conn, "board_tasks", "engineer_owner_id"),
    ])

    # The engineer namespace is the primary MCP surface. The historical alias
    # surface was removed before this rename pass, so there is no longer a live
    # module or tool prefix to detect here; keep the report field stable for
    # existing doctor consumers.
    engineer_tool_aliases_present = False

    return {
        "legacy_template_files_ignored": len(ignored_legacy_files),
        "ignored_legacy_files": ignored_legacy_files,
        "legacy_columns_present": legacy_columns_present,
        "engineer_tool_aliases_present": engineer_tool_aliases_present,
    }


def _check_migration_version(report: dict) -> dict:
    actual = int(report["migration"]["schema_kinds_migration_version"] or 0)
    return {
        "name": "migration_version",
        "status": "pass" if actual >= 4 else "fail",
        "details": {"expected_min": 4, "actual": actual},
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
    count = int(report["drift"]["agents_created_by_engineer_owner_engineer"] or 0)
    return {
        "name": "agents_created_by_owner_drift",
        "status": "pass" if count == 0 else "fail",
        "details": {"count": count},
    }


def _check_task_owner_drift(report: dict) -> dict:
    count = int(report["drift"]["board_tasks_engineer_owner_assigned_engineer"] or 0)
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


def _warn_task_aliases_missing_canonical(report: dict) -> dict | None:
    aliases = report.get("task_aliases", {}) or {}
    count = int(aliases.get("missing_canonical_count", 0) or 0)
    if count <= 0:
        return None
    return {
        "name": "task_aliases_missing_canonical",
        "status": "warn",
        "details": {
            "count": count,
            "aliases": list(aliases.get("missing_canonical", []) or []),
            "strategy": aliases.get(
                "strategy", "alias_precedence_archived_literals_hidden"
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
                "no engineer exists; create one from the Agent panel "
                "before using engineer MCP tools"
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


def _check_invalid_architect_hired_binding(report: dict) -> dict:
    architects = report.get("architects", {}) or {}
    invalid = list(architects.get("invalid_hired_by_architect", []) or [])
    return {
        "name": "invalid_architect_hired_by_architect",
        "status": "pass" if not invalid else "fail",
        "details": {
            "count": len(invalid),
            "invalid": invalid,
        },
    }


def _check_stage_6_legacy_columns_removed(report: dict) -> dict:
    cleanup = report.get("stage_6_cleanup", {}) or {}
    present = bool(cleanup.get("legacy_columns_present"))
    return {
        "name": "stage_6_legacy_columns_removed",
        "status": "pass" if not present else "fail",
        "details": {"present": present},
    }


def _check_stage_6_engineer_tool_aliases_removed(report: dict) -> dict:
    cleanup = report.get("stage_6_cleanup", {}) or {}
    present = bool(cleanup.get("engineer_tool_aliases_present"))
    return {
        "name": "stage_6_engineer_tool_aliases_removed",
        "status": "pass" if not present else "fail",
        "details": {"present": present},
    }


def _warn_ignored_legacy_template_files(report: dict) -> dict | None:
    cleanup = report.get("stage_6_cleanup", {}) or {}
    ignored = list(cleanup.get("ignored_legacy_files", []) or [])
    if not ignored:
        return None
    return {
        "name": "legacy_template_files_ignored",
        "status": "warn",
        "details": {
            "count": len(ignored),
            "files": [_humanize_path(str(entry.get("path", "") or "")) for entry in ignored],
            "hint": (
                "legacy template files in agents/ are ignored; "
                "move them into roles/"
            ),
        },
    }


def _warn_stale_pending_hires(report: dict) -> list[dict]:
    pending_hires = report.get("pending_hires", {}) or {}
    warnings = []
    for entry in list(pending_hires.get("stale_pending_hires", []) or []):
        warnings.append({
            "name": "stale_pending_hire",
            "status": "warn",
            "details": {
                "id": str(entry.get("id", "") or ""),
                "architect_id": str(entry.get("architect_id", "") or ""),
                "architect_name": str(entry.get("architect_name", "") or ""),
                "age_hours": int(entry.get("age_hours", 0) or 0),
            },
        })
    return warnings


def _warn_dangling_decision_architects(report: dict) -> list[dict]:
    architects = report.get("architects", {}) or {}
    warnings = []
    for entry in list(architects.get("dangling_decisions", []) or []):
        warnings.append({
            "name": "dangling_decision_architect",
            "status": "warn",
            "details": {
                "id": str(entry.get("id", "") or ""),
                "architect_id": str(entry.get("architect_id", "") or ""),
            },
        })
    return warnings


def _warn_nonconforming_worker_worktree_branches(report: dict) -> dict | None:
    worktrees = report.get("worktrees", {}) or {}
    branches = list(worktrees.get("nonconforming_branches", []) or [])
    if not branches:
        return None
    return {
        "name": "nonconforming_worker_worktree_branches",
        "status": "warn",
        "details": {
            "count": len(branches),
            "branches": branches,
        },
    }


def _warn_empty_kind_agents_with_task_history(report: dict) -> dict | None:
    agents = report.get("agents", {}) or {}
    entries = list(agents.get("empty_kind_with_task_history", []) or [])
    if not entries:
        return None
    return {
        "name": "empty_kind_agents_with_task_history",
        "status": "warn",
        "details": {
            "count": len(entries),
            "agents": entries,
            "hint": (
                "agent rows with kind='' and task history may be workers that "
                "missed the worker kind stamp"
            ),
        },
    }


_DOCTOR_CHECKS = [
    _check_migration_version,
    _check_unmigrated_agents,
    _check_template_role_drift,
    _check_created_owner_drift,
    _check_task_owner_drift,
    _check_invalid_architect_hired_binding,
    _check_stage_6_legacy_columns_removed,
    _check_stage_6_engineer_tool_aliases_removed,
]

_DOCTOR_WARNINGS = [
    _warn_unassigned_tasks_when_engineer_present,
    _warn_task_aliases_missing_canonical,
    _warn_ignored_legacy_template_files,
    _warn_no_engineers,
    _warn_engineer_binding_env_mismatch,
    _warn_stale_pending_hires,
    _warn_dangling_decision_architects,
    _warn_nonconforming_worker_worktree_branches,
    _warn_empty_kind_agents_with_task_history,
]


def build_doctor_report(conn: sqlite3.Connection, db_path: Path | str) -> dict:
    db_path = Path(db_path)
    agents = _collect_agents_section(conn)
    tasks = _collect_tasks_section(conn, engineer_count=int(agents["engineer"] or 0))
    architects = _collect_architects_section(conn)
    architect_names = {
        str(entry.get("id", "") or ""): str(entry.get("name", "") or "")
        for entry in list(architects.get("architects", []) or [])
        if str(entry.get("id", "") or "").strip()
    }
    report = {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "migration": _collect_migration_section(conn, db_path),
        "agents": agents,
        "tasks": tasks,
        "task_aliases": _collect_task_aliases_section(conn),
        "mcp_health": _collect_mcp_health_section(conn),
        "drift": _collect_drift_section(conn),
        "roles": _collect_roles_section(),
        "stage_6_cleanup": _collect_stage_6_cleanup_section(
            conn, base_dir=str(db_path.parent)
        ),
        "architects": architects,
        "pending_hires": _collect_pending_hires_section(
            conn,
            architect_names=architect_names,
        ),
        "worktrees": _collect_worktrees_section(conn),
    }
    report["roles_templates"] = {
        "roles_dir": report["roles"]["roles_dir"],
        "roles_file_count": report["roles"]["roles_file_count"],
    }
    report["engineers"] = _collect_engineers_section(conn)
    checks = [check(report) for check in _DOCTOR_CHECKS]
    warnings = []
    for fn in _DOCTOR_WARNINGS:
        warning = fn(report)
        if not warning:
            continue
        if isinstance(warning, list):
            warnings.extend(warning)
        else:
            warnings.append(warning)
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


def format_mcp_health_report(report: dict) -> str:
    mcp_health = report.get("mcp_health", report) or {}
    totals = mcp_health.get("totals", {}) or {}
    surfaces = mcp_health.get("surfaces", {}) or {}
    lines = [
        "Loom MCP health",
        "",
        "recent_window_seconds: "
        f"{int(mcp_health.get('recent_window_seconds', 0) or 0)}",
        "pending_failed_writes: "
        f"{int(mcp_health.get('pending_failed_writes', 0) or 0)}",
        "totals: "
        f"retries={int(totals.get('retry', 0) or 0)} "
        f"drops={int(totals.get('drop', 0) or 0)} "
        f"dedupes={int(totals.get('dedupe', 0) or 0)} "
        f"replays={int(totals.get('replay', 0) or 0)}",
        "",
        "surfaces:",
    ]
    if not surfaces:
        lines.append("  (no recent MCP reliability events)")
    for surface in sorted(surfaces):
        entry = surfaces.get(surface, {}) or {}
        events = entry.get("events", {}) or {}
        lines.append(
            f"  - {surface}: "
            f"retries={int(events.get('retry', 0) or 0)} "
            f"drops={int(events.get('drop', 0) or 0)} "
            f"dedupes={int(events.get('dedupe', 0) or 0)} "
            f"replays={int(events.get('replay', 0) or 0)}"
        )
        for tool_name in sorted((entry.get("tools", {}) or {})):
            tool_events = entry["tools"].get(tool_name, {}) or {}
            parts = [
                f"{name}={int(tool_events.get(name, 0) or 0)}"
                for name in sorted(tool_events)
            ]
            lines.append(f"      {tool_name}: " + ", ".join(parts))
    return "\n".join(lines)


def format_doctor_report(report: dict) -> str:
    migration = report.get("migration", {})
    agents = report.get("agents", {})
    engineers = report.get("engineers", {}) or {}
    architects = report.get("architects", {}) or {}
    pending_hires = report.get("pending_hires", {}) or {}
    worktrees = report.get("worktrees", {}) or {}
    tasks = report.get("tasks", {})
    task_aliases = report.get("task_aliases", {}) or {}
    mcp_health = report.get("mcp_health", {}) or {}
    drift = report.get("drift", {})
    roles = report.get("roles", {}) or {}
    stage_6_cleanup = report.get("stage_6_cleanup", {}) or {}
    warnings = list(report.get("warnings", []) or [])

    engineer_line = f"  engineer:    {int(agents.get('engineer', 0) or 0)}"
    engineer_name = str(agents.get("engineer_name", "") or "").strip()
    if engineer_name:
        engineer_line += f"   ({engineer_name})"

    roles_count = int(roles.get("roles_file_count", 0) or 0)
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
        "[architects]",
        f"  total:             {int(architects.get('total', 0) or 0)}",
        "  architects:",
    ])
    for architect in list(architects.get("architects", []) or []):
        lines.append(
            "    - "
            f"{architect.get('name', '')} "
            f"id={architect.get('id', '')} "
            f"decisions={int(architect.get('decision_count', 0) or 0)} "
            f"hired_engineers={int(architect.get('hired_engineer_count', 0) or 0)}"
        )
    lines.extend([
        "",
        "[pending_hires]",
        f"  pending:                  {int(pending_hires.get('pending', 0) or 0)}",
        "  approved:                 "
        f"{int(pending_hires.get('approved_recent', 0) or 0)} (in the last 7 days)",
        "  rejected:                 "
        f"{int(pending_hires.get('rejected_recent', 0) or 0)} (in the last 7 days)",
        "  stale_pending (>24h):     "
        f"{int(pending_hires.get('stale_pending', 0) or 0)}",
        "",
        "[worktrees]",
        "  total_worker_branches: "
        f"{int(worktrees.get('total_worker_branches', 0) or 0)}",
        "  namespaced (stage 5):  "
        f"{int(worktrees.get('namespaced', 0) or 0)}",
        "  legacy (pre-stage-5):  "
        f"{int(worktrees.get('legacy', 0) or 0)}",
        "  nonconforming:         "
        f"{int(worktrees.get('nonconforming', 0) or 0)}",
        "",
        "[tasks]",
        f"  total:       {int(tasks.get('total', 0) or 0)}",
        f"  assigned:    {int(tasks.get('assigned', 0) or 0)}",
        f"  unassigned:  {int(tasks.get('unassigned', 0) or 0)}",
        "  unassigned_when_engineer_present: "
        f"{int(tasks.get('unassigned_when_engineer_present', 0) or 0)}",
        "",
        "[task_aliases]",
        f"  total:                         {int(task_aliases.get('total', 0) or 0)}",
        "  literal_collisions:            "
        f"{int(task_aliases.get('literal_collision_count', 0) or 0)}",
        "  archived_literal_collisions:   "
        f"{int(task_aliases.get('archived_literal_collision_count', 0) or 0)}",
        "  missing_canonical:             "
        f"{int(task_aliases.get('missing_canonical_count', 0) or 0)}",
        "  strategy:                      "
        f"{task_aliases.get('strategy', 'alias_precedence_archived_literals_hidden')}",
        "",
        "[mcp_health]",
        "  recent_window_seconds:         "
        f"{int(mcp_health.get('recent_window_seconds', 0) or 0)}",
        "  pending_failed_writes:         "
        f"{int(mcp_health.get('pending_failed_writes', 0) or 0)}",
        "  retries:                       "
        f"{int((mcp_health.get('totals', {}) or {}).get('retry', 0) or 0)}",
        "  drops:                         "
        f"{int((mcp_health.get('totals', {}) or {}).get('drop', 0) or 0)}",
        "  dedupes:                       "
        f"{int((mcp_health.get('totals', {}) or {}).get('dedupe', 0) or 0)}",
        "  replays:                       "
        f"{int((mcp_health.get('totals', {}) or {}).get('replay', 0) or 0)}",
        "",
        "[drift]",
        "  agents.template ↔ role:                 "
        f"{int(drift.get('agents_template_role', 0) or 0)}",
        "  agents.created_by_engineer_id ↔ owner_engineer_id: "
        f"{int(drift.get('agents_created_by_engineer_owner_engineer', 0) or 0)}",
        "  board_tasks.engineer_owner_id ↔ assigned_engineer_id: "
        f"{int(drift.get('board_tasks_engineer_owner_assigned_engineer', 0) or 0)}",
        "",
        "[roles]",
        "  roles_dir:                      "
        f"{_humanize_path(str(roles.get('roles_dir', '')))} ({roles_count} files)",
        "  roles_with_preamble:            "
        f"{int(roles.get('roles_with_preamble', 0) or 0)}",
        "  roles_with_priorities:          "
        f"{int(roles.get('roles_with_priorities', 0) or 0)}",
        "",
        "[stage_6_cleanup]",
        "  legacy_template_files_ignored:  "
        f"{int(stage_6_cleanup.get('legacy_template_files_ignored', 0) or 0)}",
        "  legacy_columns_present:         "
        f"{str(bool(stage_6_cleanup.get('legacy_columns_present'))).lower()}",
        "  engineer_tool_aliases_present:    "
        f"{str(bool(stage_6_cleanup.get('engineer_tool_aliases_present'))).lower()}",
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
            elif name == "task_aliases_missing_canonical":
                aliases = details.get("aliases", []) or []
                summary = ", ".join(
                    f"{entry.get('legacy_id')}->{entry.get('task_id')}"
                    for entry in aliases[:7]
                )
                line = (
                    "  - task aliases point at missing canonical rows; "
                    "persist/review before restart"
                )
                if summary:
                    line += f": {summary}"
                lines.append(line)
            elif name == "legacy_template_files_ignored":
                files = ", ".join(details.get("files", []) or [])
                line = (
                    "  - legacy template files in agents/ are ignored; "
                    "move them into roles/"
                )
                if files:
                    line += f": {files}"
                lines.append(line)
            elif name == "no_engineers":
                lines.append(
                    "  - no engineer exists; create one from the Agent panel "
                    "before using engineer MCP tools"
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
            elif name == "stale_pending_hire":
                architect_name = str(details.get("architect_name", "") or "").strip()
                architect_display = architect_name or str(
                    details.get("architect_id", "") or "<unknown architect>"
                )
                lines.append(
                    "  - pending hire "
                    f"{details.get('id', '')} from {architect_display} "
                    f"has been waiting {int(details.get('age_hours', 0) or 0)} hours; "
                    "approve or reject"
                )
            elif name == "dangling_decision_architect":
                lines.append(
                    "  - decision "
                    f"{details.get('id', '')} points at missing architect "
                    f"{details.get('architect_id', '')}"
                )
            elif name == "nonconforming_worker_worktree_branches":
                branches = details.get("branches", []) or []
                summary = ", ".join(
                    str(entry.get("branch", "") or "")
                    for entry in branches
                    if str(entry.get("branch", "") or "").strip()
                )
                base = (
                    "  - worker worktree branches do not match stage-5 or legacy naming"
                )
                if summary:
                    base += f": {summary}"
                lines.append(base)
            elif name == "empty_kind_agents_with_task_history":
                entries = details.get("agents", []) or []
                summary = ", ".join(
                    str(entry.get("id", "") or "")
                    for entry in entries
                    if str(entry.get("id", "") or "").strip()
                )
                base = (
                    "  - agent rows with kind='' have task history; "
                    "run the worker-kind backfill migration"
                )
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
                    "  - agents.created_by_engineer_id ↔ owner_engineer_id drift: "
                    f"{details.get('count', 0)}"
                )
            elif name == "board_tasks_owner_drift":
                lines.append(
                    "  - board_tasks.engineer_owner_id ↔ assigned_engineer_id drift: "
                    f"{details.get('count', 0)}"
                )
            elif name == "invalid_architect_hired_by_architect":
                for entry in list(details.get("invalid", []) or []):
                    lines.append(
                        "  - architect "
                        f"{entry.get('name') or entry.get('id')}"
                        " has hired_by_architect_id, invalid state"
                    )
            elif name == "stage_6_legacy_columns_removed":
                lines.append(
                    "  - legacy kinds-refactor columns are still present; "
                    "complete the stage-6 cleanup migration"
                )
            elif name == "stage_6_engineer_tool_aliases_removed":
                lines.append(
                    "  - engineer_* MCP aliases are still present; "
                    "remove the legacy alias surface"
                )
            else:
                lines.append(f"  - {name}")
    return "\n".join(lines)
