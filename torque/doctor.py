"""Kinds-refactor diagnostics shared by CLI and server."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import yaml

from . import ai_deps
from . import install_locations
from .agent_classes import (
    AGENT_CLASS_SCHEMA_VERSION,
    agent_class_authoring_contract,
    agent_class_cell_status,
    enriched_agent_class_preview,
    validate_all_agent_classes,
)
from .mcp_idempotency import collect_mcp_idempotency_storage_stats
from .worktree_boundaries import code_boundary_done_status
from .doctor_agent_classes import (
    collect_frozen_missing_tools,
    format_frozen_missing_tools_warning,
    frozen_missing_tools_warning,
)
from . import doctor_artifacts, doctor_branches
DOCTOR_SCHEMA_VERSION = 3
_KINDS_MIGRATION_VERSION_KEY = "schema_kinds_migration_version"
_KINDS_MIGRATION_MIGRATED_AT_KEY = "schema_kinds_migration_migrated_at"
_KINDS_BACKUP_NAME = "torque.db.pre-kinds.bak"
_SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60
_ONE_DAY_SECONDS = 24 * 60 * 60
_MCP_HEALTH_WINDOW_SECONDS = 60 * 60
# WorktreeManager currently derives the default branch suffix from ``cell.id[:7]``.
# Accept 6+ hex tails so the doctor stays tolerant of older/manual fixtures while
# still rejecting very short manual tails like ``-bad``.
_WORKTREE_SHORT_ID_RE = r"[a-f0-9]{6,}"
_NAMESPACED_WORKTREE_RE = re.compile(
    rf"^torque/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*-{_WORKTREE_SHORT_ID_RE}$"
)
_USER_WORKTREE_RE = re.compile(
    rf"^torque/user/[a-z0-9][a-z0-9-]*-{_WORKTREE_SHORT_ID_RE}$"
)
_LEGACY_WORKTREE_RE = re.compile(
    rf"^torque/[a-z0-9][a-z0-9-]*-{_WORKTREE_SHORT_ID_RE}$"
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


def _fetch_global_setting(conn: sqlite3.Connection, key: str, default=None):
    try:
        row = conn.execute(
            "SELECT value FROM global_settings WHERE key=?",
            (key,),
        ).fetchone()
    except sqlite3.OperationalError:
        return default
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return row[0]


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


def _resolve_path_for_compare(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:
        return path.expanduser()


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        _resolve_path_for_compare(path).relative_to(_resolve_path_for_compare(parent))
        return True
    except ValueError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    return _resolve_path_for_compare(left) == _resolve_path_for_compare(right)


def _classify_data_dir(data_dir: Path) -> tuple[str, str]:
    legacy_dir = install_locations.legacy_toolbelt_dir()
    if _same_path(data_dir, legacy_dir):
        return "legacy_toolbelt", ""

    profiles_root = install_locations.profiles_root()
    try:
        rel = _resolve_path_for_compare(data_dir).relative_to(
            _resolve_path_for_compare(profiles_root)
        )
    except ValueError:
        return "custom", ""
    if rel.parts:
        return "primary_profile", rel.parts[0]
    return "unknown", ""


def _classify_runtime_python(runtime_python: str | Path | None) -> str:
    if not str(runtime_python or "").strip():
        return "unknown"
    path = Path(os.path.expanduser(str(runtime_python)))
    if _same_path(path, install_locations.primary_runtime_python()):
        return "primary_runtime"

    path_text = str(path)
    legacy_markers = (
        "Library/Application Support/iTerm2",
        ".config/iterm2/AppSupport",
        "iterm2env",
    )
    if any(marker in path_text for marker in legacy_markers):
        return "legacy_appsupport"
    return "custom"


def _collect_runtime_locations_section(
    db_path: Path | str,
    *,
    runtime_python: str | Path | None = None,
) -> dict:
    db_path = Path(db_path).expanduser()
    data_dir = db_path.parent
    data_dir_kind, profile_guess = _classify_data_dir(data_dir)
    primary_runtime_python = install_locations.primary_runtime_python()
    legacy_candidates = install_locations.legacy_iterm2_python_candidates()
    runtime_python_text = str(runtime_python or "").strip()
    return {
        "db_path": str(db_path),
        "data_dir": str(data_dir),
        "data_dir_kind": data_dir_kind,
        "profile_guess": profile_guess,
        "primary_runtime_python": str(primary_runtime_python),
        "primary_runtime_python_exists": primary_runtime_python.is_file(),
        "runtime_python": runtime_python_text,
        "runtime_python_kind": _classify_runtime_python(runtime_python_text),
        "legacy_toolbelt_dir": str(install_locations.legacy_toolbelt_dir()),
        "legacy_toolbelt_dir_exists": install_locations.legacy_toolbelt_dir().exists(),
        "legacy_toolbelt_db_exists": (
            install_locations.legacy_toolbelt_dir() / "torque.db"
        ).exists(),
        "legacy_project_iterm2env": str(
            install_locations.legacy_project_iterm2env_dir()
        ),
        "legacy_project_iterm2env_exists": (
            install_locations.legacy_project_iterm2env_dir().exists()
        ),
        "legacy_iterm2_python_count": len(legacy_candidates),
        "legacy_iterm2_python_samples": [
            str(path) for path in legacy_candidates[:3]
        ],
    }


def _collect_multiprocessing_children_section(
    *,
    ps_output: str | None = None,
    runner=subprocess.check_output,
) -> dict:
    """Collect observable Python multiprocessing spawn/resource-tracker rows.

    Torque's local AI embedding service is the only daemon path expected to
    create ``multiprocessing.spawn`` children.  This section is intentionally
    diagnostic-only: doctor must not kill or reap these processes from a worker
    context, but it should make accumulation visible for leak investigations.
    """

    if ps_output is None:
        try:
            ps_output = runner(
                ["ps", "-axo", "pid=,ppid=,rss=,etime=,command="],
                text=True,
                errors="replace",
            )
        except Exception as exc:
            return {
                "available": False,
                "error": str(exc),
                "count": 0,
                "total_rss_bytes": 0,
                "max_rss_bytes": 0,
                "processes": [],
            }
    processes = []
    total_rss_kb = 0
    max_rss_kb = 0
    for line in str(ps_output or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid_raw, ppid_raw, rss_raw, etime, command = parts
        if (
            "multiprocessing.spawn" not in command
            and "spawn_main" not in command
            and "multiprocessing.resource_tracker" not in command
        ):
            continue
        try:
            pid = int(pid_raw)
            ppid = int(ppid_raw)
            rss_kb = int(rss_raw)
        except ValueError:
            continue
        kind = (
            "resource_tracker"
            if "resource_tracker" in command
            else "spawn_worker"
        )
        total_rss_kb += max(0, rss_kb)
        max_rss_kb = max(max_rss_kb, rss_kb)
        processes.append({
            "pid": pid,
            "ppid": ppid,
            "rss_bytes": max(0, rss_kb) * 1024,
            "etime": etime,
            "kind": kind,
            "command": command[:500],
        })
    return {
        "available": True,
        "count": len(processes),
        "spawn_worker_count": sum(
            1 for proc in processes if proc.get("kind") == "spawn_worker"
        ),
        "resource_tracker_count": sum(
            1 for proc in processes if proc.get("kind") == "resource_tracker"
        ),
        "total_rss_bytes": total_rss_kb * 1024,
        "max_rss_bytes": max_rss_kb * 1024,
        "processes": processes[:20],
    }


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
    global_dir = (Path.home() / ".torque" / leaf).resolve()
    for _ in range(20):
        candidate = d / ".torque" / leaf
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
    global_legacy_dir = Path.home() / ".torque" / "agents"
    if global_legacy_dir.is_dir():
        scopes.append({
            "legacy_dir": global_legacy_dir,
            "roles_dir": Path.home() / ".torque" / "roles",
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
    specialization_column_exists = _column_exists(
        conn, "agents", "engineer_specializations"
    )
    try:
        specialization_column = (
            "engineer_specializations" if specialization_column_exists
            else "'[]' AS engineer_specializations"
        )
        rows = conn.execute(
            f"SELECT rowid, id, name, slug, persistent, {specialization_column} FROM agents "
            "WHERE cell_type='agent' AND kind='engineer' "
            "ORDER BY rowid"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for rowid, agent_id, name, slug, persistent, raw_specializations in rows:
        agent_id = str(agent_id or "")
        try:
            specializations = json.loads(raw_specializations or "[]")
        except (TypeError, ValueError):
            specializations = []
        if not isinstance(specializations, list):
            specializations = []
        specializations = [
            str(item).strip() for item in specializations if str(item).strip()
        ]
        engineers.append(
            {
                "id": agent_id,
                "name": str(name or ""),
                "slug": str(slug or ""),
                "persistent": int(persistent or 0),
                "worker_count": int(worker_counts.get(agent_id, 0) or 0),
                "task_count": int(task_counts.get(agent_id, 0) or 0),
                "specializations": specializations,
                "specialization_display": (
                    ", ".join(specializations) if specializations
                    else "generalist"
                ),
                "_rowid": int(rowid or 0),
                "_created_at": history_created_at.get(agent_id),
            }
        )

    binding_env_mismatches = []
    for engineer in engineers:
        actual = str(engineer.get("torque_engineer_id", engineer["id"]) or "")
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

    generalists = [
        {
            "id": engineer["id"],
            "name": engineer["name"],
            "slug": engineer["slug"],
        }
        for engineer in engineers
        if not engineer["specializations"]
    ]
    return {
        "total": len(engineers),
        "engineers": engineers,
        "generalists": generalists,
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


def _collect_mcp_idempotency_storage_section(conn: sqlite3.Connection) -> dict:
    return collect_mcp_idempotency_storage_stats(conn)


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
        "isolation_guard_repos": [],
        "isolation_guard_missing": [],
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

    section.update(_collect_isolation_guard_status(conn))
    return section


def _collect_isolation_guard_status(conn: sqlite3.Connection) -> dict:
    """Report which repo roots have the worktree-isolation guard hook.

    The guard is the fail-closed pre-commit hook (TORQUE:580) that blocks a
    worker from committing into the shared main checkout. We surface repos
    where it is missing so the operator can reinstall it.
    """
    status = {"isolation_guard_repos": [], "isolation_guard_missing": []}
    if not _column_exists(conn, "agents", "worktree_repo_root"):
        return status
    try:
        rows = conn.execute(
            "SELECT DISTINCT worktree_repo_root FROM agents "
            "WHERE worktree_repo_root != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        return status

    try:
        from .worktree import worktree_isolation_guard_installed
    except Exception:
        return status

    for (repo_root,) in rows:
        repo_root = str(repo_root or "").strip()
        if not repo_root or not os.path.isdir(repo_root):
            continue
        installed = bool(worktree_isolation_guard_installed(repo_root))
        status["isolation_guard_repos"].append({
            "repo_root": repo_root,
            "installed": installed,
        })
        if not installed:
            status["isolation_guard_missing"].append(repo_root)
    return status


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
    roles_dir = Path.home() / ".torque" / "roles"
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


def _collect_agent_classes_section(conn: sqlite3.Connection | None = None, base_dir: str = "") -> dict:
    validation = validate_all_agent_classes(base_dir=base_dir)
    classes = list(validation.get("classes", []) or [])
    previews = [enriched_agent_class_preview(definition, base_dir=base_dir) for definition in classes]
    assignments: list[dict] = []
    audit_recent: list[dict] = []
    frozen_missing_tools = collect_frozen_missing_tools(conn)
    if conn is not None and _table_exists(conn, "agents"):
        cols = [
            "id", "name", "kind", "cell_type", "agent_class_id",
            "agent_class_version", "agent_class_assigned_at",
            "agent_class_assigned_by", "effective_agent_class_id",
            "effective_agent_class_version", "effective_agent_class_snapshot",
            "effective_agent_class_applied_at",
        ]
        if all(_column_exists(conn, "agents", col) for col in cols):
            try:
                for row in conn.execute(
                    "SELECT " + ",".join(cols) + " FROM agents "
                    "WHERE cell_type='agent' ORDER BY name, id"
                ).fetchall():
                    item = dict(zip(cols, row))
                    try:
                        item["effective_agent_class_snapshot"] = json.loads(
                            item.get("effective_agent_class_snapshot") or "{}"
                        )
                    except (json.JSONDecodeError, TypeError):
                        item["effective_agent_class_snapshot"] = {}
                    cell = SimpleNamespace(**item)
                    status = agent_class_cell_status(cell, base_dir=base_dir)
                    if status.get("assigned_class_id") or status.get("effective_class_id"):
                        assignments.append(status)
            except sqlite3.OperationalError:
                assignments = []
    if conn is not None and _table_exists(conn, "agent_class_audit"):
        try:
            rows = conn.execute(
                "SELECT agent_id, agent_name, event, assigned_class_id, "
                "assigned_class_version, effective_class_id, "
                "effective_class_version, snapshot_hash, message, created_at "
                "FROM agent_class_audit ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            audit_recent = [
                {
                    "agent_id": str(row[0] or ""),
                    "agent_name": str(row[1] or ""),
                    "event": str(row[2] or ""),
                    "assigned_class_id": str(row[3] or ""),
                    "assigned_class_version": str(row[4] or ""),
                    "effective_class_id": str(row[5] or ""),
                    "effective_class_version": str(row[6] or ""),
                    "snapshot_hash": str(row[7] or ""),
                    "message": str(row[8] or ""),
                    "created_at": float(row[9] or 0),
                }
                for row in rows
            ]
        except sqlite3.OperationalError:
            audit_recent = []
    authoring_contract = agent_class_authoring_contract()
    return {
        "config_path": ".torque/agent_classes/",
        "base_dir": str(base_dir or os.getcwd()),
        "class_count": int(validation.get("class_count", 0) or 0),
        "valid": bool(validation.get("valid")),
        "error_count": int(validation.get("error_count", 0) or 0),
        "warning_count": int(validation.get("warning_count", 0) or 0),
        "classes": [definition.as_preview_dict() for definition in classes],
        "dry_run_previews": previews,
        "assignments": assignments,
        "assignment_count": len(assignments),
        "audit_recent": audit_recent,
        "audit_recent_count": len(audit_recent),
        "frozen_missing_tools": frozen_missing_tools,
        "frozen_missing_tool_count": len(frozen_missing_tools),
        "issues": [issue.as_dict() for issue in list(validation.get("issues", []) or [])],
        "runtime_enforcement": "launch_frozen_agent_class_authority",
        "schema_version": AGENT_CLASS_SCHEMA_VERSION,
        "authoring_contract": authoring_contract,
        "capability_bucket_count": len(authoring_contract.get("capability_bucket_catalog", []) or []),
        "restriction_bucket_count": len(authoring_contract.get("restriction_bucket_catalog", []) or []),
        "external_connector_caveat": (
            "External connector exposure is informational only in Wave 7; "
            "Agent Classes do not enforce connector governance."
        ),
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


def _warn_engineer_generalist_specialization(report: dict) -> dict | None:
    engineers = report.get("engineers", {}) or {}
    generalists = list(engineers.get("generalists", []) or [])
    if not generalists:
        return None
    return {
        "name": "engineer_generalist_specialization",
        "status": "warn",
        "details": {
            "count": len(generalists),
            "engineers": generalists,
            "hint": (
                "empty specialization bindings are routable generalists: "
                "explicit assignment works and hint-based routing uses them "
                "as a lowest-preference fallback after matching specialists"
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


def _check_agent_classes_valid(report: dict) -> dict:
    classes = report.get("agent_classes", {}) or {}
    errors = int(classes.get("error_count", 0) or 0)
    return {
        "name": "agent_classes_valid",
        "status": "pass" if errors == 0 else "fail",
        "details": {
            "error_count": errors,
            "issues": list(classes.get("issues", []) or []),
        },
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


def _warn_worktree_isolation_guard_missing(report: dict) -> dict | None:
    worktrees = report.get("worktrees", {}) or {}
    missing = list(worktrees.get("isolation_guard_missing", []) or [])
    if not missing:
        return None
    return {
        "name": "worktree_isolation_guard_missing",
        "status": "warn",
        "details": {
            "count": len(missing),
            "repos": [_humanize_path(str(repo)) for repo in missing],
            "hint": (
                "the worktree-isolation pre-commit guard (TORQUE:580) is not "
                "installed in these repos; worker commits into the shared main "
                "checkout will not be blocked. It self-installs on the next "
                "worktree creation, or remove a foreign pre-commit hook so "
                "Torque can install its own."
            ),
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


def _warn_legacy_toolbelt_data_dir(report: dict) -> dict | None:
    runtime = report.get("runtime_locations", {}) or {}
    if runtime.get("data_dir_kind") != "legacy_toolbelt":
        return None
    return {
        "name": "legacy_toolbelt_data_dir",
        "status": "warn",
        "details": {
            "data_dir": runtime.get("data_dir", ""),
            "hint": (
                "doctor is reading legacy Toolbelt data under iTerm2 "
                "AppSupport; migrate deliberately with "
                "scripts/migrate_toolbelt_to_profile.py before manual cleanup"
            ),
        },
    }


def _warn_legacy_appsupport_python_runtime(report: dict) -> dict | None:
    runtime = report.get("runtime_locations", {}) or {}
    if runtime.get("runtime_python_kind") != "legacy_appsupport":
        return None
    return {
        "name": "legacy_appsupport_python_runtime",
        "status": "warn",
        "details": {
            "runtime_python": runtime.get("runtime_python", ""),
            "primary_runtime_python": runtime.get("primary_runtime_python", ""),
            "hint": (
                "live daemon is running from legacy iTerm2/AppSupport Python; "
                "from a non-worker shell run make deps && make deploy, then "
                "relaunch with make run or make standalone"
            ),
        },
    }


def _warn_primary_runtime_missing(report: dict) -> dict | None:
    runtime = report.get("runtime_locations", {}) or {}
    if runtime.get("primary_runtime_python_exists"):
        return None
    if runtime.get("runtime_python_kind") != "legacy_appsupport":
        return None
    return {
        "name": "primary_runtime_missing",
        "status": "warn",
        "details": {
            "primary_runtime_python": runtime.get("primary_runtime_python", ""),
            "hint": "run make deps to create Torque's owned runtime",
        },
    }


def _collect_ai_section(conn: sqlite3.Connection) -> dict:
    dependency_status = ai_deps.embeddings_dependency_status()
    desired_model_id = _fetch_global_setting(
        conn,
        "ai_embedding_model",
        "BAAI/bge-m3",
    )
    index_state = {
        "desired_model_id": desired_model_id,
        "active_model_id": "",
        "active_dims": 0,
        "status": "not_built",
        "rebuild_required": False,
        "rebuild_reason": "",
        "last_error": "",
    }
    index_counts = {
        "sources": 0,
        "chunks": 0,
        "indexed": 0,
        "pending": 0,
        "stale": 0,
        "errors": 0,
        "model_mismatch_chunks": 0,
    }
    try:
        row = conn.execute(
            "SELECT desired_model_id, active_model_id, active_dims, status, "
            "rebuild_required, rebuild_reason, last_error "
            "FROM ai_index_state WHERE id='default'"
        ).fetchone()
        if row:
            index_state.update({
                "desired_model_id": row[0] or desired_model_id,
                "active_model_id": row[1] or "",
                "active_dims": int(row[2] or 0),
                "status": row[3] or "not_built",
                "rebuild_required": bool(row[4]),
                "rebuild_reason": row[5] or "",
                "last_error": row[6] or "",
            })
    except sqlite3.Error:
        pass
    try:
        source_rows = conn.execute(
            "SELECT state, COUNT(*) FROM ai_embedding_sources GROUP BY state"
        ).fetchall()
        by_state = {str(state or ""): int(count or 0) for state, count in source_rows}
        chunks = int(conn.execute(
            "SELECT COUNT(*) FROM ai_embedding_chunks"
        ).fetchone()[0] or 0)
        active_model = str(index_state.get("active_model_id", "") or "")
        active_dims = int(index_state.get("active_dims", 0) or 0)
        mismatch = 0
        if chunks and (active_model or active_dims):
            mismatch = int(conn.execute(
                "SELECT COUNT(*) FROM ai_embedding_chunks "
                "WHERE embedding_model_id!=? OR embedding_dims!=?",
                (active_model, active_dims),
            ).fetchone()[0] or 0)
        index_counts.update({
            "sources": sum(by_state.values()),
            "chunks": chunks,
            "indexed": by_state.get("indexed", 0),
            "pending": by_state.get("pending", 0),
            "stale": by_state.get("stale", 0),
            "errors": by_state.get("error", 0),
            "model_mismatch_chunks": mismatch,
        })
    except sqlite3.Error:
        pass
    return {
        "enabled": bool(_fetch_global_setting(conn, "ai_enabled", False)),
        "embeddings_dependency": {
            "status": dependency_status,
            "packages": list(ai_deps.AI_DEPENDENCY_PACKAGES),
            "missing_packages": ai_deps.missing_ai_dependency_packages(),
            "install_hint": ai_deps.AI_DEPS_INSTALL_HINT,
        },
        "desired_model_id": desired_model_id,
        "index_state": index_state,
        "index_counts": index_counts,
    }


def _warn_ai_optional_deps_missing(report: dict) -> dict | None:
    ai = report.get("ai", {}) or {}
    dependency = ai.get("embeddings_dependency", {}) or {}
    if not bool(ai.get("enabled")):
        return None
    if dependency.get("status") != "missing":
        return None
    packages = list(dependency.get("missing_packages", []) or [])
    if not packages:
        packages = list(dependency.get("packages", []) or [])
    return {
        "name": "ai_optional_deps_missing",
        "status": "warn",
        "details": {
            "packages": packages,
            "install_hint": dependency.get(
                "install_hint",
                ai_deps.AI_DEPS_INSTALL_HINT,
            ),
            "hint": (
                "AI is enabled but optional embedding dependencies are missing; "
                f"run {dependency.get('install_hint', ai_deps.AI_DEPS_INSTALL_HINT)}"
            ),
        },
    }


def _warn_ai_index_rebuild_pending(report: dict) -> dict | None:
    ai = report.get("ai", {}) or {}
    state = ai.get("index_state", {}) or {}
    counts = ai.get("index_counts", {}) or {}
    desired = str(state.get("desired_model_id", "") or ai.get("desired_model_id", "") or "")
    active = str(state.get("active_model_id", "") or "")
    chunks = int(counts.get("chunks", 0) or 0)
    rebuild_required = bool(state.get("rebuild_required"))
    mismatch = chunks > 0 and desired and active and desired != active
    if not rebuild_required and not mismatch:
        return None
    return {
        "name": "ai_index_rebuild_pending",
        "status": "warn",
        "details": {
            "desired_model_id": desired,
            "active_model_id": active,
            "chunks": chunks,
            "rebuild_required": rebuild_required,
            "reason": str(state.get("rebuild_reason", "") or "embedding_model_change"),
        },
    }


def _warn_ai_index_chunk_model_mismatch(report: dict) -> dict | None:
    ai = report.get("ai", {}) or {}
    counts = ai.get("index_counts", {}) or {}
    mismatch = int(counts.get("model_mismatch_chunks", 0) or 0)
    if mismatch <= 0:
        return None
    state = ai.get("index_state", {}) or {}
    return {
        "name": "ai_index_chunk_model_mismatch",
        "status": "warn",
        "details": {
            "count": mismatch,
            "active_model_id": str(state.get("active_model_id", "") or ""),
            "active_dims": int(state.get("active_dims", 0) or 0),
        },
    }


def _collect_pty_supervisor_section(db_path: Path) -> dict:
    """Probe the PTY supervisor socket directly (no daemon required).

    A present-but-unreachable socket is the signature of a down or wedged
    supervisor — the "supervisor disconnected / can't send messages" class of
    incident — and this surfaces it without log spelunking.
    """
    from .pty_supervisor import DEFAULT_SOCKET_NAME, _metrics_socket, _ping_socket
    socket_path = Path(db_path).parent / DEFAULT_SOCKET_NAME
    section = {
        "socket_path": str(socket_path),
        "socket_present": socket_path.exists(),
        "reachable": False,
        "pid": None,
        "started_at": None,
        "uptime": None,
        "protocol_version": None,
        "ping_ms": None,
        "metrics": {},
    }
    if not section["socket_present"]:
        return section
    started = time.monotonic()
    pong = _ping_socket(socket_path, timeout=2.0)
    if isinstance(pong, dict) and pong.get("type") == "pong":
        section["reachable"] = True
        section["pid"] = pong.get("pid")
        section["started_at"] = pong.get("started_at")
        try:
            section["uptime"] = round(
                max(0.0, time.time() - float(pong.get("started_at") or 0.0)),
                1,
            )
        except (TypeError, ValueError):
            section["uptime"] = None
        section["protocol_version"] = pong.get("version")
        section["ping_ms"] = round((time.monotonic() - started) * 1000, 1)
        metrics = _metrics_socket(socket_path, timeout=2.0)
        if isinstance(metrics, dict) and metrics.get("type") == "metrics":
            section["metrics"] = dict(metrics.get("metrics") or {})
    return section


def _check_pty_supervisor_reachable(report: dict) -> dict:
    sup = report.get("pty_supervisor", {}) or {}
    present = bool(sup.get("socket_present"))
    reachable = bool(sup.get("reachable"))
    # No socket = supervisor simply not running (e.g. daemon stopped) — not a
    # failure. Socket present but not answering = down/wedged — that's a fail.
    status = "fail" if (present and not reachable) else "pass"
    return {
        "name": "pty_supervisor_reachable",
        "status": status,
        "details": {
            "socket_present": present,
            "reachable": reachable,
            "ping_ms": sup.get("ping_ms"),
        },
    }

def _warn_mcp_idempotency_storage(report: dict) -> dict | None:
    storage = report.get("mcp_idempotency_storage", {}) or {}
    warnings = list(storage.get("warnings", []) or [])
    if not warnings:
        return None
    return {
        "name": "mcp_idempotency_storage_bloat",
        "status": "warn",
        "details": {
            "warnings": warnings,
            "row_count": int(storage.get("row_count", 0) or 0),
            "response_bytes": int(storage.get("response_bytes", 0) or 0),
            "max_response_bytes": int(storage.get("max_response_bytes", 0) or 0),
            "avg_response_bytes": float(storage.get("avg_response_bytes", 0.0) or 0.0),
            "table_bytes": storage.get("table_bytes"),
            "dbstat_available": bool(storage.get("dbstat_available")),
            "hint": (
                "new Torque versions compact MCP idempotency receipts in "
                "bounded batches; after enough compaction, run VACUUM from a "
                "non-worker shell during maintenance if disk must be reclaimed"
            ),
        },
    }

def _warn_stuck_input_sessions(report: dict) -> dict | None:
    sup = report.get("pty_supervisor", {}) or {}
    breakers = sup.get("open_write_breakers") or {}
    if not breakers:
        return None
    return {
        "name": "stuck_input_sessions",
        "details": {
            "count": len(breakers),
            "sessions": sorted(breakers.keys()),
        },
    }


def _collect_stranded_code_boundary_roots_section(
    conn: sqlite3.Connection,
) -> dict:
    """Report actionable roots whose canonical code-boundary Done gate blocks.

    All task rows are loaded, including archived children, because boundary
    evidence remains pipeline evidence after a child is archived.  Completed
    or archived roots are excluded: their Done transition is no longer an
    actionable gate.  This collector is deliberately read-only; in particular
    rerouted/superseded evidence is reported, never repaired or stamped.
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


def _warn_stranded_code_boundary_roots(report: dict) -> dict | None:
    section = report.get("stranded_code_boundary_roots", {}) or {}
    if not int(section.get("root_count", 0) or 0):
        return None
    return {
        "name": "stranded_code_boundary_roots",
        "status": "warn",
        "details": section,
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
    _check_agent_classes_valid,
    _check_pty_supervisor_reachable,
]

_DOCTOR_WARNINGS = [
    _warn_stranded_code_boundary_roots,
    _warn_unassigned_tasks_when_engineer_present,
    _warn_task_aliases_missing_canonical,
    doctor_artifacts.task_artifact_id_collision_warning,
    _warn_ignored_legacy_template_files,
    frozen_missing_tools_warning,
    _warn_no_engineers,
    _warn_engineer_generalist_specialization,
    _warn_engineer_binding_env_mismatch,
    _warn_stale_pending_hires,
    _warn_dangling_decision_architects,
    _warn_nonconforming_worker_worktree_branches,
    _warn_worktree_isolation_guard_missing,
    _warn_empty_kind_agents_with_task_history,
    _warn_legacy_toolbelt_data_dir,
    _warn_legacy_appsupport_python_runtime,
    _warn_primary_runtime_missing,
    _warn_ai_optional_deps_missing,
    _warn_ai_index_rebuild_pending,
    _warn_ai_index_chunk_model_mismatch,
    _warn_mcp_idempotency_storage,
    _warn_stuck_input_sessions,
]


def build_doctor_report(
    conn: sqlite3.Connection,
    db_path: Path | str,
    *,
    runtime_python: str | Path | None = None,
    project_base_dir: str | Path | None = None,
) -> dict:
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
        "runtime_locations": _collect_runtime_locations_section(
            db_path, runtime_python=runtime_python
        ),
        "agents": agents,
        "tasks": tasks,
        "stranded_code_boundary_roots": (
            _collect_stranded_code_boundary_roots_section(conn)
        ),
        "task_artifact_ids": doctor_artifacts.collect_task_artifact_id_section(
            conn, table_exists=_table_exists, column_exists=_column_exists),
        "task_aliases": _collect_task_aliases_section(conn),
        "mcp_health": _collect_mcp_health_section(conn),
        "mcp_idempotency_storage": _collect_mcp_idempotency_storage_section(conn),
        "drift": _collect_drift_section(conn),
        "roles": _collect_roles_section(),
        "agent_classes": _collect_agent_classes_section(
            conn,
            str(project_base_dir or os.getcwd())
        ),
        "stage_6_cleanup": _collect_stage_6_cleanup_section(
            conn, base_dir=str(db_path.parent)
        ),
        "architects": architects,
        "pending_hires": _collect_pending_hires_section(
            conn,
            architect_names=architect_names,
        ),
        "worktrees": _collect_worktrees_section(conn),
        "squash_branch_cleanup": doctor_branches.collect_squash_branch_cleanup_section(conn, project_base_dir),
        "nested_branch_cleanup": doctor_branches.collect_nested_branch_cleanup_section(conn, project_base_dir),
        "ai": _collect_ai_section(conn),
        "multiprocessing_children": _collect_multiprocessing_children_section(),
        "pty_supervisor": _collect_pty_supervisor_section(db_path),
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


def build_doctor_report_for_db(
    db_path: Path | str,
    *,
    runtime_python: str | Path | None = None,
    project_base_dir: str | Path | None = None,
) -> dict:
    db_path = Path(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return build_doctor_report(
            conn,
            db_path,
            runtime_python=runtime_python,
            project_base_dir=project_base_dir,
        )
    finally:
        conn.close()


def format_mcp_health_report(report: dict) -> str:
    mcp_health = report.get("mcp_health", report) or {}
    totals = mcp_health.get("totals", {}) or {}
    surfaces = mcp_health.get("surfaces", {}) or {}
    lines = [
        "Torque MCP health",
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
    runtime_locations = report.get("runtime_locations", {}) or {}
    agents = report.get("agents", {})
    engineers = report.get("engineers", {}) or {}
    architects = report.get("architects", {}) or {}
    pending_hires = report.get("pending_hires", {}) or {}
    worktrees = report.get("worktrees", {}) or {}
    tasks = report.get("tasks", {})
    stranded_roots = report.get("stranded_code_boundary_roots", {}) or {}
    task_aliases = report.get("task_aliases", {}) or {}
    mcp_health = report.get("mcp_health", {}) or {}
    mcp_idempotency_storage = report.get("mcp_idempotency_storage", {}) or {}
    drift = report.get("drift", {})
    roles = report.get("roles", {}) or {}
    agent_classes = report.get("agent_classes", {}) or {}
    stage_6_cleanup = report.get("stage_6_cleanup", {}) or {}
    ai = report.get("ai", {}) or {}
    ai_dependency = ai.get("embeddings_dependency", {}) or {}
    ai_index_state = ai.get("index_state", {}) or {}
    ai_index_counts = ai.get("index_counts", {}) or {}
    multiprocessing_children = report.get("multiprocessing_children", {}) or {}
    pty_supervisor = report.get("pty_supervisor", {}) or {}
    pty_metrics = pty_supervisor.get("metrics", {}) or {}
    pty_health = pty_supervisor.get("health", {}) or {}
    warnings = list(report.get("warnings", []) or [])

    def _pty_health_value(name: str):
        if name in pty_supervisor:
            return pty_supervisor.get(name)
        return pty_health.get(name)

    def _format_optional_bool(value) -> str:
        if value is None:
            return "—"
        return str(bool(value)).lower()

    def _format_optional_value(value) -> str:
        if value is None:
            return "—"
        return str(value)

    engineer_line = f"  engineer:    {int(agents.get('engineer', 0) or 0)}"
    engineer_name = str(agents.get("engineer_name", "") or "").strip()
    if engineer_name:
        engineer_line += f"   ({engineer_name})"

    roles_count = int(roles.get("roles_file_count", 0) or 0)
    lines = [
        "Torque doctor — kinds refactor",
        "",
        "[migration]",
        "  schema_kinds_migration_version: "
        f"{migration.get('schema_kinds_migration_version', 0)}",
        "  migrated_at:                    "
        f"{migration.get('migrated_at_display') or migration.get('migrated_at', '')}",
        f"  backup:                         {migration.get('backup_display', '')}",
        "",
        "[runtime_locations]",
        "  data_dir:                       "
        f"{_humanize_path(str(runtime_locations.get('data_dir', '')))}",
        "  data_dir_kind:                  "
        f"{runtime_locations.get('data_dir_kind', '')}",
        "  profile_guess:                  "
        f"{runtime_locations.get('profile_guess', '')}",
        "  db_path:                        "
        f"{_humanize_path(str(runtime_locations.get('db_path', '')))}",
        "  primary_runtime_python:         "
        f"{_humanize_path(str(runtime_locations.get('primary_runtime_python', '')))}",
        "  primary_runtime_python_exists:  "
        f"{str(bool(runtime_locations.get('primary_runtime_python_exists'))).lower()}",
        "  runtime_python:                 "
        f"{_humanize_path(str(runtime_locations.get('runtime_python', '') or '(not supplied)'))}",
        "  runtime_python_kind:            "
        f"{runtime_locations.get('runtime_python_kind', '')}",
        "  legacy_toolbelt_db_exists:      "
        f"{str(bool(runtime_locations.get('legacy_toolbelt_db_exists'))).lower()}",
        "  legacy_iterm2_python_count:     "
        f"{int(runtime_locations.get('legacy_iterm2_python_count', 0) or 0)}",
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
            f"tasks={int(engineer.get('task_count', 0) or 0)}  "
            f"specializations={engineer.get('specialization_display', '')}"
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
        "  isolation_guard_missing: "
        f"{len(list(worktrees.get('isolation_guard_missing', []) or []))}",
        "",
        *doctor_branches.format_squash_branch_cleanup_section(report.get("squash_branch_cleanup", {})),
        *doctor_branches.format_nested_branch_cleanup_section(report.get("nested_branch_cleanup", {})),
        "[tasks]",
        f"  total:       {int(tasks.get('total', 0) or 0)}",
        f"  assigned:    {int(tasks.get('assigned', 0) or 0)}",
        f"  unassigned:  {int(tasks.get('unassigned', 0) or 0)}",
        "  unassigned_when_engineer_present: "
        f"{int(tasks.get('unassigned_when_engineer_present', 0) or 0)}",
        *doctor_artifacts.format_task_artifact_id_section(report.get("task_artifact_ids", {})),
        "",
        "[stranded_code_boundary_roots]",
        "  root_count:  "
        f"{int(stranded_roots.get('root_count', 0) or 0)}",
        "  root_ids:    "
        f"{', '.join(list(stranded_roots.get('root_ids', []) or [])) or '(none)'}",
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
        "[mcp_idempotency_storage]",
        "  rows:                          "
        f"{int(mcp_idempotency_storage.get('row_count', 0) or 0)}",
        "  compacted_rows:                "
        f"{int(mcp_idempotency_storage.get('compacted_row_count', 0) or 0)}",
        "  response_bytes:                "
        f"{_format_size(int(mcp_idempotency_storage.get('response_bytes', 0) or 0))}",
        "  max_response_bytes:            "
        f"{_format_size(int(mcp_idempotency_storage.get('max_response_bytes', 0) or 0))}",
        "  avg_response_bytes:            "
        f"{_format_size(int(float(mcp_idempotency_storage.get('avg_response_bytes', 0.0) or 0.0)))}",
        "  table_bytes:                   "
        f"{_format_size(int(mcp_idempotency_storage.get('table_bytes') or 0)) if mcp_idempotency_storage.get('table_bytes') is not None else '— (dbstat unavailable)'}",
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
        "[agent_classes]",
        "  config_path:                    "
        f"{agent_classes.get('config_path', '.torque/agent_classes/')}",
        "  class_count:                    "
        f"{int(agent_classes.get('class_count', 0) or 0)}",
        "  schema_version:                 "
        f"{int(agent_classes.get('schema_version', 0) or 0)}",
        "  error_count:                    "
        f"{int(agent_classes.get('error_count', 0) or 0)}",
        "  runtime_enforcement:            "
        f"{agent_classes.get('runtime_enforcement', '')}",
        "  capability_buckets:             "
        f"{int(agent_classes.get('capability_bucket_count', 0) or 0)}",
        "  restriction_buckets:            "
        f"{int(agent_classes.get('restriction_bucket_count', 0) or 0)}",
        "  assignment_count:               "
        f"{int(agent_classes.get('assignment_count', 0) or 0)}",
        "  audit_recent_count:             "
        f"{int(agent_classes.get('audit_recent_count', 0) or 0)}",
        "  frozen_missing_tool_count:      "
        f"{int(agent_classes.get('frozen_missing_tool_count', 0) or 0)}",
        "  external_connector_caveat:      "
        f"{agent_classes.get('external_connector_caveat', '')}",
        "",
        "[stage_6_cleanup]",
        "  legacy_template_files_ignored:  "
        f"{int(stage_6_cleanup.get('legacy_template_files_ignored', 0) or 0)}",
        "  legacy_columns_present:         "
        f"{str(bool(stage_6_cleanup.get('legacy_columns_present'))).lower()}",
        "  engineer_tool_aliases_present:    "
        f"{str(bool(stage_6_cleanup.get('engineer_tool_aliases_present'))).lower()}",
        "",
        "[ai]",
        "  enabled:                        "
        f"{str(bool(ai.get('enabled'))).lower()}",
        "  embeddings_dependency_status:   "
        f"{ai_dependency.get('status', '')}",
        "  embeddings_dependency_packages: "
        f"{', '.join(list(ai_dependency.get('packages', []) or []))}",
        "  install_hint:                   "
        f"{ai_dependency.get('install_hint', ai_deps.AI_DEPS_INSTALL_HINT)}",
        "  desired_embedding_model:        "
        f"{ai_index_state.get('desired_model_id', ai.get('desired_model_id', ''))}",
        "  active_embedding_model:         "
        f"{ai_index_state.get('active_model_id', '')}",
        "  active_embedding_dims:          "
        f"{int(ai_index_state.get('active_dims', 0) or 0)}",
        "  index_status:                   "
        f"{ai_index_state.get('status', '')}",
        "  index_chunks:                   "
        f"{int(ai_index_counts.get('chunks', 0) or 0)}",
        "  index_model_mismatch_chunks:    "
        f"{int(ai_index_counts.get('model_mismatch_chunks', 0) or 0)}",
        "  rebuild_required:               "
        f"{str(bool(ai_index_state.get('rebuild_required'))).lower()}",
        "",
        "[multiprocessing_children]",
        "  available:                      "
        f"{str(bool(multiprocessing_children.get('available'))).lower()}",
        "  count:                          "
        f"{int(multiprocessing_children.get('count', 0) or 0)}",
        "  spawn_worker_count:             "
        f"{int(multiprocessing_children.get('spawn_worker_count', 0) or 0)}",
        "  resource_tracker_count:         "
        f"{int(multiprocessing_children.get('resource_tracker_count', 0) or 0)}",
        "  total_rss:                      "
        f"{_format_size(int(multiprocessing_children.get('total_rss_bytes', 0) or 0))}",
        "  max_rss:                        "
        f"{_format_size(int(multiprocessing_children.get('max_rss_bytes', 0) or 0))}",
        "",
        "[pty_supervisor]",
        "  state:                          "
        f"{_format_optional_value(_pty_health_value('state'))}",
        "  connected:                      "
        f"{_format_optional_bool(_pty_health_value('connected'))}",
        "  socket_present:                 "
        f"{str(bool(pty_supervisor.get('socket_present'))).lower()}",
        "  reachable:                      "
        f"{str(bool(pty_supervisor.get('reachable'))).lower()}",
        "  pid:                            "
        f"{pty_supervisor.get('pid') if pty_supervisor.get('pid') is not None else '—'}",
        "  uptime_seconds:                 "
        f"{pty_supervisor.get('uptime') if pty_supervisor.get('uptime') is not None else '—'}",
        "  ping_ms:                        "
        f"{pty_supervisor.get('ping_ms') if pty_supervisor.get('ping_ms') is not None else '—'}",
        "  reconnect_count:                "
        f"{int(pty_supervisor.get('reconnect_count', 0) or 0)}",
        "  last_op_latency_ms:             "
        f"{pty_supervisor.get('last_op_latency_ms') if pty_supervisor.get('last_op_latency_ms') is not None else '—'}",
        "  time_since_last_successful_op:  "
        f"{_format_optional_value(_pty_health_value('time_since_last_successful_op'))}",
        "  sessions_current:               "
        f"{int(pty_metrics.get('sessions_current', 0) or 0)}",
        "  sessions_peak:                  "
        f"{int(pty_metrics.get('sessions_peak', 0) or 0)}",
        "  sessions_created_total:         "
        f"{int(pty_metrics.get('sessions_created_total', 0) or 0)}",
        "  bytes_read:                     "
        f"{int(pty_metrics.get('bytes_read', 0) or 0)}",
        "  bytes_written:                  "
        f"{int(pty_metrics.get('bytes_written', 0) or 0)}",
        "  read_loop_failures:             "
        f"{int(pty_metrics.get('read_loop_failures', 0) or 0)}",
        "  write_deadline_hits:            "
        f"{int(pty_metrics.get('write_deadline_hits', 0) or 0)}",
        "  stuck_input_sessions:           "
        f"{pty_supervisor.get('stuck_sessions') if 'stuck_sessions' in pty_supervisor else '— (daemon offline)'}",
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
            elif name == "stranded_code_boundary_roots":
                root_ids = ", ".join(details.get("root_ids", []) or [])
                lines.append(
                    "  - actionable pipeline roots are blocked by persisted "
                    "code-boundary evidence: "
                    f"roots={int(details.get('root_count', 0) or 0)}"
                    + (f" ({root_ids})" if root_ids else "")
                    + "; report only—review merge/reroute evidence manually"
                )
            elif name == "mcp_idempotency_storage_bloat":
                table_bytes = details.get("table_bytes")
                table_display = (
                    _format_size(int(table_bytes or 0))
                    if table_bytes is not None
                    else "dbstat unavailable"
                )
                lines.append(
                    "  - MCP idempotency storage is above warning thresholds"
                    f": rows={int(details.get('row_count', 0) or 0)} "
                    f"response_bytes={_format_size(int(details.get('response_bytes', 0) or 0))} "
                    f"max_receipt={_format_size(int(details.get('max_response_bytes', 0) or 0))} "
                    f"table={table_display}; compacted receipts require a "
                    "manual VACUUM to reclaim already-allocated SQLite pages"
                )
            elif name == "stuck_input_sessions":
                sessions = details.get("sessions", []) or []
                summary = ", ".join(s[:12] for s in sessions[:6])
                line = (
                    f"  - {details.get('count', 0)} agent session(s) have an "
                    "open input-write breaker (agent not draining stdin); "
                    "restart the affected agent"
                )
                if summary:
                    line += f": {summary}"
                lines.append(line)
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
            elif name == "task_artifact_id_collisions":
                lines.append(doctor_artifacts.format_task_artifact_id_collision_warning(details))
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
                base = "  - engineer TORQUE_ENGINEER_ID mismatch detected"
                if summary:
                    base += f": {summary}"
                lines.append(base)
            elif name == "engineer_generalist_specialization":
                entries = details.get("engineers", []) or []
                summary = ", ".join(
                    str(entry.get("name") or entry.get("id") or "")
                    for entry in entries
                )
                line = (
                    "  - Engineer(s) with empty specialization bindings are "
                    "routable generalists; explicit assignment works and "
                    "hint routing uses them only after matching specialists"
                )
                if summary:
                    line += f": {summary}"
                lines.append(line)
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
            elif name == "legacy_toolbelt_data_dir":
                lines.append(
                    "  - doctor is reading legacy Toolbelt data under "
                    "iTerm2/AppSupport; migrate with "
                    "scripts/migrate_toolbelt_to_profile.py before manual cleanup"
                )
            elif name == "legacy_appsupport_python_runtime":
                runtime_python = _humanize_path(
                    str(details.get("runtime_python", "") or "")
                )
                line = (
                    "  - live daemon is running from legacy iTerm2/AppSupport "
                    "Python; run make deps && make deploy from a non-worker "
                    "shell, then relaunch"
                )
                if runtime_python:
                    line += f": {runtime_python}"
                lines.append(line)
            elif name == "primary_runtime_missing":
                primary_python = _humanize_path(
                    str(details.get("primary_runtime_python", "") or "")
                )
                line = "  - Torque-owned runtime is missing; run make deps"
                if primary_python:
                    line += f": {primary_python}"
                lines.append(line)
            elif name == "ai_optional_deps_missing":
                packages = ", ".join(details.get("packages", []) or [])
                hint = str(
                    details.get("install_hint", ai_deps.AI_DEPS_INSTALL_HINT) or ""
                )
                line = (
                    "  - AI is enabled but optional embedding dependencies are "
                    "missing"
                )
                if packages:
                    line += f": {packages}"
                if hint:
                    line += f" (run {hint})"
                lines.append(line)
            elif name == "ai_index_rebuild_pending":
                line = (
                    "  - AI vector index rebuild pending"
                    f": desired={details.get('desired_model_id', '')}"
                    f" active={details.get('active_model_id', '')}"
                    f" chunks={int(details.get('chunks', 0) or 0)}"
                )
                reason = str(details.get("reason", "") or "")
                if reason:
                    line += f" reason={reason}"
                lines.append(line)
            elif name == "ai_index_chunk_model_mismatch":
                lines.append(
                    "  - AI vector index has chunks for a non-active "
                    "embedding model/dims"
                    f": {int(details.get('count', 0) or 0)}"
                )
            elif name == "frozen_agent_class_missing_tools":
                lines.extend(format_frozen_missing_tools_warning(details))
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
            elif name == "agent_classes_valid":
                issues = list(details.get("issues", []) or [])
                if not issues:
                    lines.append("  - Agent Class definitions are invalid")
                for issue in issues[:10]:
                    path = _humanize_path(str(issue.get("path", "") or ""))
                    location = f" ({path})" if path else ""
                    lines.append(
                        "  - Agent Class validation failed"
                        f"[{issue.get('code', '')}]{location}: "
                        f"{issue.get('message', '')}"
                    )
            else:
                lines.append(f"  - {name}")
    return "\n".join(lines)
