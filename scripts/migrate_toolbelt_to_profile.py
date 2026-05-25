#!/usr/bin/env python3
"""One-off migration from the legacy iTerm2 Toolbelt DB to a profile DB.

This is intentionally small and single-user focused.  It copies the legacy
Toolbelt SQLite state into ``~/.torque/profiles/<profile>/torque.db``, runs the
current schema initializer, and rewrites iTerm2-backed cells so the standalone
runtime starts them as stopped PTY-backed cells instead of trying to adopt live
iTerm2 sessions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, TextIO

# Make the script runnable directly from a checkout without installing Torque.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from torque.db_schema import initialize_database  # noqa: E402

LEGACY_TOOLBELT_RELATIVE = Path(
    "Library/Application Support/iTerm2/Scripts/torque/torque"
)
TOOLBELT_DEFAULT_PORT = 18932
DESKTOP_DEFAULT_PORT = 18933


class MigrationError(RuntimeError):
    """Expected operator-facing migration failure."""


@dataclass
class MigrationSummary:
    profile: str
    profile_slug: str
    source_db: Path
    target_db: Path
    backup_dir: Path
    backup_paths: list[Path]
    imported_agents: int
    imported_group_settings: int
    agents_marked_stopped: int
    agent_sessions_cleared: int
    agent_windows_cleared: int
    agent_backends_rewritten: int
    group_backends_rewritten: int

    @property
    def next_launch_command(self) -> str:
        if self.profile_slug == "standalone":
            return "make standalone"
        if self.profile_slug == "desktop":
            return "make run"
        return f"TORQUE_PROFILE={self.profile_slug} make run"


def _slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


def legacy_toolbelt_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / LEGACY_TOOLBELT_RELATIVE


def profile_dir(profile: str, home: Path | None = None) -> Path:
    return (
        (home or Path.home())
        / ".torque"
        / "profiles"
        / _slugify_profile(profile)
    )


def _sqlite_sidecars(db_path: Path) -> Iterable[Path]:
    yield Path(str(db_path) + "-wal")
    yield Path(str(db_path) + "-shm")


def _timestamp(now: Callable[[], datetime] | None = None) -> str:
    return (now or datetime.now)().strftime("%Y%m%d-%H%M%S")


def _unique_backup_dir(root: Path) -> Path:
    if not root.exists():
        return root
    for idx in range(1, 100):
        candidate = root.with_name(f"{root.name}-{idx}")
        if not candidate.exists():
            return candidate
    raise MigrationError(
        f"Could not allocate a unique backup directory under {root.parent}"
    )


def _backup_file(path: Path, backup_dir: Path, prefix: str) -> Path | None:
    if not path.exists():
        return None
    dest = backup_dir / f"{prefix}-{path.name}"
    shutil.copy2(path, dest)
    return dest


def _backup_inputs(
    *,
    source_db: Path,
    source_log: Path,
    target_db: Path,
    backup_dir: Path,
) -> list[Path]:
    backup_dir.mkdir(parents=True, exist_ok=False)
    backups: list[Path] = []
    for path, prefix in (
        (source_db, "source"),
        (source_log, "source"),
        (target_db, "target"),
    ):
        backed = _backup_file(path, backup_dir, prefix)
        if backed is not None:
            backups.append(backed)
    # WAL/SHM files are cheap to preserve and may contain uncheckpointed state.
    for path in _sqlite_sidecars(source_db):
        backed = _backup_file(path, backup_dir, "source")
        if backed is not None:
            backups.append(backed)
    for path in _sqlite_sidecars(target_db):
        backed = _backup_file(path, backup_dir, "target")
        if backed is not None:
            backups.append(backed)
    return backups


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_live_pid(pid_file: Path) -> int | None:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def _normalize_path(path: Path | str) -> Path:
    return Path(os.path.expanduser(str(path))).resolve(strict=False)


def _parse_port(value: object) -> int | None:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _candidate_live_daemon_ports(profile_slug: str) -> list[int]:
    """Ports worth probing for a live primary runtime.

    Primary desktop defaults to 18933; standalone/toolbelt defaults to 18932.
    Env overrides cover custom launches made from the same shell.
    """
    ports: list[int] = []

    def add(port: int | None) -> None:
        if port is not None and port not in ports:
            ports.append(port)

    add(_parse_port(os.environ.get("TORQUE_PORT")))
    add(_parse_port(os.environ.get("TORQUE_DESKTOP_PORT")))
    if profile_slug == "desktop":
        add(DESKTOP_DEFAULT_PORT)
        add(TOOLBELT_DEFAULT_PORT)
    else:
        add(TOOLBELT_DEFAULT_PORT)
        add(DESKTOP_DEFAULT_PORT)
    return ports


def _fetch_live_runtime_config(port: int, *, timeout: float = 0.35) -> dict | None:
    payload = json.dumps({"cmd": "get_config"}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/cmd",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read())
    except (
        OSError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
        socket.timeout,
        urllib.error.URLError,
    ):
        return None
    if not isinstance(decoded, dict) or not decoded.get("ok"):
        return None
    data = decoded.get("data")
    if not isinstance(data, dict):
        return None
    runtime = data.get("runtime")
    return runtime if isinstance(runtime, dict) else None


def _runtime_matches_target(
    runtime: dict,
    *,
    target_directory: Path,
    profile_slug: str,
) -> bool:
    data_dir = str(runtime.get("data_dir", "") or "").strip()
    if data_dir:
        try:
            if _normalize_path(data_dir) == _normalize_path(target_directory):
                return True
        except (OSError, RuntimeError):
            pass

    profile = str(runtime.get("profile", "") or "").strip()
    return bool(profile) and _slugify_profile(profile) == profile_slug


def _refuse_if_target_daemon_live(
    target_directory: Path,
    *,
    profile_slug: str,
    live_runtime_probe: Callable[[int], dict | None] | None = None,
) -> None:
    # Legacy/possible future cheap path: if a profile pid file exists and is
    # live, stop.  Primary desktop/standalone currently do not write this file,
    # so also probe actual default/env runtime ports below.
    pid_file = target_directory / "torque.pid"
    pid = _read_live_pid(pid_file)
    if pid is not None:
        raise MigrationError(
            f"Refusing to overwrite {target_directory}: live target daemon "
            f"pid {pid} found in {pid_file}. Stop Torque first, then rerun "
            "this script."
        )

    probe = live_runtime_probe or _fetch_live_runtime_config
    for port in _candidate_live_daemon_ports(profile_slug):
        runtime = probe(port)
        if not runtime or not _runtime_matches_target(
            runtime,
            target_directory=target_directory,
            profile_slug=profile_slug,
        ):
            continue
        runtime_data_dir = str(runtime.get("data_dir", "") or "").strip()
        runtime_profile = str(runtime.get("profile", "") or "").strip()
        runtime_pid = str(runtime.get("pid", "") or "").strip()
        pid_part = f" pid={runtime_pid}" if runtime_pid else ""
        raise MigrationError(
            f"Refusing to overwrite {target_directory}: live Torque daemon"
            f"{pid_part} on port {port} is using profile "
            f"{runtime_profile or '(unset)'} / data dir "
            f"{runtime_data_dir or '(unset)'}. Stop Torque first, then rerun "
            "this script (or pass --force if you accept the risk)."
        )


def _replace_target_from_source(source_db: Path, target_db: Path) -> None:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    # Remove any previous DB and sidecars after backing them up; stale WAL files
    # next to a newly copied DB are unsafe.
    for path in (target_db, *tuple(_sqlite_sidecars(target_db))):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    source_uri = f"file:{source_db}?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    target_conn = sqlite3.connect(str(target_db))
    try:
        source_conn.backup(target_conn)
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()


def _count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _legacy_toolbelt_agent_ids(conn: sqlite3.Connection) -> list[str]:
    if not _column_exists(conn, "agents", "terminal_backend"):
        return []
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM agents "
            "WHERE COALESCE(terminal_backend, 'iterm2') = 'iterm2'"
        ).fetchall()
    ]


def _legacy_toolbelt_group_names(conn: sqlite3.Connection) -> list[str]:
    if not _column_exists(
        conn, "group_settings", "default_terminal_backend"
    ):
        return []
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT group_name FROM group_settings "
            "WHERE COALESCE(default_terminal_backend, 'iterm2') = 'iterm2'"
        ).fetchall()
    ]


def _in_clause(values: list[str]) -> tuple[str, list[str]]:
    placeholders = ",".join(["?"] * len(values))
    return f"({placeholders})", values


def _backfill_agent_history(conn: sqlite3.Connection) -> None:
    if _column_exists(conn, "agents", "role") and _column_exists(
        conn, "agents", "template"
    ):
        template_expr = (
            "CASE WHEN TRIM(COALESCE(role, '')) != '' "
            "THEN role ELSE template END"
        )
    elif _column_exists(conn, "agents", "role"):
        template_expr = "role"
    elif _column_exists(conn, "agents", "template"):
        template_expr = "template"
    else:
        template_expr = "''"
    rows = conn.execute(
        f"SELECT id, name, slug, group_name, agent_type, {template_expr}, "
        "worktree_branch, tasks_dispatched FROM agents "
        "WHERE cell_type='agent' AND id NOT IN "
        "(SELECT id FROM agent_history)"
    ).fetchall()
    created_at = datetime.now().timestamp()
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_history
                (id, name, slug, "group", agent_type, template,
                 created_at, worktree_branch, total_tasks, status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                created_at,
                row[6],
                row[7],
                "active",
            ),
        )
    conn.commit()


def _run_schema_and_normalize(target_db: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(target_db))
    try:
        legacy_agent_ids = _legacy_toolbelt_agent_ids(conn)
        legacy_group_names = _legacy_toolbelt_group_names(conn)
        legacy_agent_where = (
            "COALESCE(terminal_backend, 'iterm2') = 'iterm2'"
        )
        agents_marked_stopped = (
            _count(
                conn,
                "SELECT COUNT(*) FROM agents "
                f"WHERE {legacy_agent_where} AND status != 'stopped'",
            )
            if _column_exists(conn, "agents", "status")
            else 0
        )
        agent_sessions_cleared = (
            _count(
                conn,
                "SELECT COUNT(*) FROM agents "
                f"WHERE {legacy_agent_where} AND session_id IS NOT NULL",
            )
            if _column_exists(conn, "agents", "session_id")
            else 0
        )
        agent_windows_cleared = (
            _count(
                conn,
                "SELECT COUNT(*) FROM agents "
                f"WHERE {legacy_agent_where} "
                "AND COALESCE(window_id, '') != ''",
            )
            if _column_exists(conn, "agents", "window_id")
            else 0
        )

        initialize_database(conn, lambda: _backfill_agent_history(conn))
        imported_agents = _count(conn, "SELECT COUNT(*) FROM agents")
        imported_group_settings = _count(
            conn, "SELECT COUNT(*) FROM group_settings"
        )

        if legacy_agent_ids:
            clause, params = _in_clause(legacy_agent_ids)
            conn.execute(
                "UPDATE agents SET "
                "status = 'stopped', "
                "session_id = NULL, "
                "window_id = '', "
                "terminal_backend = 'pty' "
                f"WHERE id IN {clause}",
                params,
            )
        if legacy_group_names:
            clause, params = _in_clause(legacy_group_names)
            conn.execute(
                "UPDATE group_settings SET default_terminal_backend = 'pty' "
                f"WHERE group_name IN {clause}",
                params,
            )
        conn.commit()
        return {
            "imported_agents": imported_agents,
            "imported_group_settings": imported_group_settings,
            "agents_marked_stopped": agents_marked_stopped,
            "agent_sessions_cleared": agent_sessions_cleared,
            "agent_windows_cleared": agent_windows_cleared,
            "agent_backends_rewritten": len(legacy_agent_ids),
            "group_backends_rewritten": len(legacy_group_names),
        }
    finally:
        conn.close()


def print_summary(summary: MigrationSummary, *, out: TextIO = sys.stdout) -> None:
    print("Toolbelt → profile migration complete.", file=out)
    print(f"  Profile:    {summary.profile_slug}", file=out)
    print(f"  Source DB:  {summary.source_db}", file=out)
    print(f"  Target DB:  {summary.target_db}", file=out)
    print("", file=out)
    print("Imported:", file=out)
    print(f"  agents:         {summary.imported_agents}", file=out)
    print(f"  group settings: {summary.imported_group_settings}", file=out)
    print("", file=out)
    print("Normalized legacy iTerm2 runtime rows:", file=out)
    print(
        f"  agents marked stopped:             {summary.agents_marked_stopped}",
        file=out,
    )
    print(
        f"  agent session_id values cleared:    {summary.agent_sessions_cleared}",
        file=out,
    )
    print(
        f"  agent window_id values cleared:     {summary.agent_windows_cleared}",
        file=out,
    )
    print(
        f"  agent terminal_backend → pty:       {summary.agent_backends_rewritten}",
        file=out,
    )
    print(
        "  group default_terminal_backend → pty: "
        f"{summary.group_backends_rewritten}",
        file=out,
    )
    print("", file=out)
    print(f"Backups: {summary.backup_dir}", file=out)
    for path in summary.backup_paths:
        print(f"  - {path}", file=out)
    print("", file=out)
    print(f"Next launch: {summary.next_launch_command}", file=out)
    print(
        "  (Use `make run` for the desktop profile, "
        "or `make standalone` for standalone.)",
        file=out,
    )


def migrate_toolbelt_to_profile(
    *,
    profile: str = "desktop",
    home: Path | None = None,
    now: Callable[[], datetime] | None = None,
    force: bool = False,
    out: TextIO = sys.stdout,
    live_runtime_probe: Callable[[int], dict | None] | None = None,
) -> MigrationSummary:
    home = home or Path.home()
    source_directory = legacy_toolbelt_dir(home)
    source_db = source_directory / "torque.db"
    source_log = source_directory / "torque.log"
    if not source_db.exists():
        raise MigrationError(f"Legacy Toolbelt DB not found: {source_db}")

    profile_slug = _slugify_profile(profile)
    target_directory = profile_dir(profile, home)
    if not force:
        _refuse_if_target_daemon_live(
            target_directory,
            profile_slug=profile_slug,
            live_runtime_probe=live_runtime_probe,
        )
    target_db = target_directory / "torque.db"

    backup_root = (
        home
        / ".torque"
        / "backups"
        / f"toolbelt-to-profile-{_timestamp(now)}"
    )
    backup_dir = _unique_backup_dir(backup_root)
    backups = _backup_inputs(
        source_db=source_db,
        source_log=source_log,
        target_db=target_db,
        backup_dir=backup_dir,
    )

    _replace_target_from_source(source_db, target_db)
    counts = _run_schema_and_normalize(target_db)

    summary = MigrationSummary(
        profile=profile,
        profile_slug=profile_slug,
        source_db=source_db,
        target_db=target_db,
        backup_dir=backup_dir,
        backup_paths=backups,
        **counts,
    )
    print_summary(summary, out=out)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-off migration from legacy iTerm2 Toolbelt torque.db to "
            "~/.torque/profiles/<profile>/torque.db."
        )
    )
    parser.add_argument(
        "--profile",
        default="desktop",
        help="Target Torque profile name (default: desktop).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore live target profile daemon guards.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        migrate_toolbelt_to_profile(profile=args.profile, force=args.force)
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
