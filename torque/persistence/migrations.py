"""SQLite schema backup and agent-kind migration persistence."""

import json
import logging
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from torque import __version__
from torque.db_schema import SCHEMA_VERSION
from torque.persistence.common import slugify as _slugify, unique_value as _unique_value


log = logging.getLogger("torque")

def _create_sqlite_backup(source_path: Path, backup_path: Path) -> None:
    """Create and verify an atomic SQLite backup without replacing one."""

    source_path = Path(source_path)
    backup_path = Path(backup_path)
    if backup_path.exists():
        raise FileExistsError(f"SQLite backup already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = backup_path.with_name(
        f".{backup_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    source = target = None
    try:
        source = sqlite3.connect(str(source_path))
        target = sqlite3.connect(str(temp_path))
        source.backup(target)
        check = target.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(
                f"SQLite backup verification failed for {source_path}"
            )
        target.close()
        target = None
        source.close()
        source = None
        os.replace(temp_path, backup_path)
    except Exception:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        temp_path.unlink(missing_ok=True)
        raise

_KINDS_SCHEMA_MIGRATION_VERSION = 1

_KINDS_BACKFILL_MIGRATION_VERSION = 2

_KINDS_CLEANUP_MIGRATION_VERSION = 3

_KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION = 4

_KINDS_SCHEMA_MIGRATION_VERSION_KEY = "schema_kinds_migration_version"

_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY = "schema_kinds_migration_migrated_at"

_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY = "schema_kinds_task_assignment_fixup_applied"

_KINDS_SCHEMA_BACKUP_NAME = "torque.db.pre-kinds.bak"

_KINDS_ENGINEER_OVERRIDE_ENV = "TORQUE_MIGRATE_ENGINEER_ID"

_KINDS_ENGINEER_GROUP = "torque"

_KINDS_ENGINEER_NAME = "Engineer"

def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class MigrationPersistenceMixin:
    """Manage verified backups and additive schema/kinds migrations."""

    def _kinds_backup_path(self) -> Path:
        return self.db_path.with_name(_KINDS_SCHEMA_BACKUP_NAME)

    def _schema_backup_path(self) -> Path:
        return self.db_path.with_name(
            f"{self.db_path.name}.pre-schema-v{SCHEMA_VERSION}.bak"
        )

    def _current_schema_migration_version(self) -> int:
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return 0
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            table = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if not table:
                return 0
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            return int((row or (0,))[0] or 0)
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return 0
        finally:
            if conn is not None:
                conn.close()

    def _maybe_backup_pending_schema_migration(self) -> None:
        """Create one verified SQLite backup before each schema target."""

        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        try:
            target_version = int(SCHEMA_VERSION)
        except (TypeError, ValueError):
            raise RuntimeError("SCHEMA_VERSION must be an integer") from None
        if self._current_schema_migration_version() >= target_version:
            return
        backup_path = self._schema_backup_path()
        if backup_path.exists():
            return
        _create_sqlite_backup(self.db_path, backup_path)
        log.info(
            "migration: created pre-schema-v%s backup at %s",
            SCHEMA_VERSION,
            backup_path,
        )

    def _read_meta_value(self, key: str, *, db_path: Optional[Path] = None) -> Optional[str]:
        if db_path is None and self._conn is not None:
            try:
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key=?",
                    (key,),
                ).fetchone()
                return None if not row else str(row[0])
            except sqlite3.OperationalError:
                return None
        path = Path(db_path or self.db_path)
        if not path.exists():
            return None
        conn = None
        try:
            conn = sqlite3.connect(str(path))
            table = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='meta'"
            ).fetchone()
            if not table:
                return None
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?",
                (key,),
            ).fetchone()
            return None if not row else str(row[0])
        except sqlite3.OperationalError:
            return None
        finally:
            if conn is not None:
                conn.close()

    def _current_kinds_migration_version(self) -> int:
        raw = self._read_meta_value(_KINDS_SCHEMA_MIGRATION_VERSION_KEY)
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _maybe_backup_pre_kinds_db(self):
        if not self.db_path.exists():
            return
        if self._kinds_backup_path().exists():
            return
        if self._current_kinds_migration_version() >= _KINDS_SCHEMA_MIGRATION_VERSION:
            return

        backup_path = self._kinds_backup_path()
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        source = target = None
        try:
            source = sqlite3.connect(str(self.db_path))
            target = sqlite3.connect(str(backup_path))
            source.backup(target)
        except Exception:
            if backup_path.exists():
                backup_path.unlink()
            raise
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

        log.info("migration: created pre-kinds backup at %s", backup_path)

    def _mark_kinds_schema_ready_if_needed(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
        """Advance the legacy stage marker after its refusal gate passes.

        Migration 7 owns the columns.  The separate marker remains until the
        historical data backfill and destructive cleanup stages are ledgered.
        """

        if self._current_kinds_migration_version() >= _KINDS_SCHEMA_MIGRATION_VERSION:
            return

        migrated_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (
                _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                str(_KINDS_SCHEMA_MIGRATION_VERSION),
            ),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY, migrated_at),
        )
        if manage_transaction:
            self._conn.commit()
        log.info(
            "migration: kinds schema applied (version=1, backup=%s)",
            self._kinds_backup_path(),
        )

    def _run_kinds_legacy_stages(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
        """Advance restart-safe kinds stages behind the guarded finalizer."""

        kwargs = {"manage_transaction": manage_transaction}
        self._mark_kinds_schema_ready_if_needed(**kwargs)
        self._backfill_kinds_if_needed(**kwargs)
        self._fixup_kinds_task_assignments_if_needed(**kwargs)
        self._cleanup_kinds_legacy_columns_if_needed(**kwargs)
        self._backfill_empty_worker_kinds_if_needed(**kwargs)

    def _find_engineer_candidate_ids(self) -> list[str]:
        template_sql = self._optional_column_sql("agents", "template")
        created_by_sql = self._optional_column_sql(
            "agents", "created_by_engineer_id"
        )
        rows = self._conn.execute(
            f"SELECT id, name, {template_sql}, {created_by_sql} "
            "FROM agents WHERE cell_type='agent' AND group_name=?",
            (_KINDS_ENGINEER_GROUP,),
        ).fetchall()
        referenced_ids = {
            str(created_by_engineer_id or "").strip()
            for _agent_id, _name, _template, created_by_engineer_id in rows
            if str(created_by_engineer_id or "").strip()
        }
        candidates = set()
        for agent_id, name, template, created_by_engineer_id in rows:
            agent_id = str(agent_id or "").strip()
            if not agent_id:
                continue
            if re.search(r"engineer", str(name or ""), re.IGNORECASE):
                candidates.add(agent_id)
                continue
            if str(template or "").strip().lower() == "engineer":
                candidates.add(agent_id)
                continue
            if not str(created_by_engineer_id or "").strip() and agent_id in referenced_ids:
                candidates.add(agent_id)
        return sorted(candidates)

    def _configured_engineer_candidate_id(self) -> str:
        row = self._conn.execute(
            "SELECT engineer_agent_id FROM group_settings WHERE group_name=?",
            (_KINDS_ENGINEER_GROUP,),
        ).fetchone()
        configured_id = str(row[0] or "").strip() if row else ""
        if not configured_id:
            return ""
        exists = self._conn.execute(
            "SELECT 1 FROM agents "
            "WHERE id=? AND cell_type='agent' AND group_name=? "
            "LIMIT 1",
            (configured_id, _KINDS_ENGINEER_GROUP),
        ).fetchone()
        return configured_id if exists else ""

    def _resolve_engineer_backfill_target(self) -> tuple[Optional[str], bool]:
        configured_id = self._configured_engineer_candidate_id()
        if configured_id:
            return configured_id, True

        candidate_ids = self._find_engineer_candidate_ids()
        if not candidate_ids:
            log.info("migration: no Engineer found, skipping engineer backfill")
            return None, True
        if len(candidate_ids) == 1:
            return candidate_ids[0], True

        override = str(os.getenv(_KINDS_ENGINEER_OVERRIDE_ENV, "") or "").strip()
        if override and override in candidate_ids:
            return override, True

        if override:
            log.error(
                "migration: multiple Engineer candidates found %s; %s=%r did not match",
                candidate_ids,
                _KINDS_ENGINEER_OVERRIDE_ENV,
                override,
            )
        else:
            log.error(
                "migration: multiple Engineer candidates found %s; set %s to continue",
                candidate_ids,
                _KINDS_ENGINEER_OVERRIDE_ENV,
            )
        return None, False

    def _resolve_backfilled_engineer_identity(self, engineer_id: str) -> tuple[str, str]:
        rows = self._conn.execute(
            "SELECT id, name, slug FROM agents"
        ).fetchall()
        existing_names = {
            str(name or "").strip()
            for agent_id, name, _slug in rows
            if str(agent_id or "") != str(engineer_id or "")
            and str(name or "").strip()
        }
        target_name = _unique_value(_KINDS_ENGINEER_NAME, existing_names)
        existing_slugs = {
            str(slug or "").strip()
            for agent_id, _name, slug in rows
            if str(agent_id or "") != str(engineer_id or "")
            and str(slug or "").strip()
        }
        target_slug = _unique_value(_slugify(target_name), existing_slugs)
        return target_name, target_slug

    def _column_exists(self, table: str, column: str) -> bool:
        try:
            self._conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
            return True
        except sqlite3.OperationalError:
            return False

    def _optional_column_sql(self, table: str, column: str) -> str:
        if self._column_exists(table, column):
            return _quote_ident(column)
        return f"'' AS {_quote_ident(column)}"

    def _refuse_unmigrated_legacy_rows_if_needed(self) -> None:
        version = self._current_kinds_migration_version()
        if version >= _KINDS_SCHEMA_MIGRATION_VERSION:
            return
        if self._count_unmigrated_legacy_rows() <= 0:
            return
        current_major = int(str(__version__ or "2.0.0").split(".", 1)[0] or 2)
        prior_major = max(1, current_major - 1)
        message = (
            "ERROR: this version requires a prior kinds-refactor migration.\n"
            f"Install Torque {prior_major}.x first, boot once so the kinds-refactor migration runs, then upgrade to Torque {__version__}.\n"
            "Current DB has unmigrated rows with legacy columns populated."
        )
        print(message, file=sys.stderr)
        log.error(message)
        raise SystemExit(1)

    def _count_unmigrated_legacy_rows(self) -> int:
        count = 0
        if self._column_exists("agents", "template"):
            if not self._column_exists("agents", "kind"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents WHERE TRIM(COALESCE(template, '')) != ''"
                ).fetchone()
            elif not self._column_exists("agents", "role"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(template, '')) != '' AND TRIM(COALESCE(kind, '')) = ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(template, '')) != '' "
                    "AND (TRIM(COALESCE(kind, '')) = '' OR TRIM(COALESCE(role, '')) = '')"
                ).fetchone()
            count += int(row[0] or 0)

        if self._column_exists("agents", "created_by_engineer_id"):
            if not self._column_exists("agents", "kind"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_engineer_id, '')) != ''"
                ).fetchone()
            elif not self._column_exists("agents", "owner_engineer_id"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_engineer_id, '')) != '' "
                    "AND TRIM(COALESCE(kind, '')) = ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE TRIM(COALESCE(created_by_engineer_id, '')) != '' "
                    "AND (TRIM(COALESCE(kind, '')) = '' "
                    "OR TRIM(COALESCE(owner_engineer_id, '')) = '')"
                ).fetchone()
            count += int(row[0] or 0)

        if self._column_exists("board_tasks", "engineer_owner_id"):
            if not self._column_exists("board_tasks", "assigned_engineer_id"):
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM board_tasks "
                    "WHERE TRIM(COALESCE(engineer_owner_id, '')) != ''"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM board_tasks "
                    "WHERE TRIM(COALESCE(engineer_owner_id, '')) != '' "
                    "AND TRIM(COALESCE(assigned_engineer_id, '')) = ''"
                ).fetchone()
            count += int(row[0] or 0)

        return count

    def _legacy_cleanup_row_counts(self) -> tuple[int, int]:
        mirrored_rows: set[tuple[str, str]] = set()
        drift_rows: set[tuple[str, str]] = set()

        if self._column_exists("agents", "template") and self._column_exists("agents", "role"):
            for agent_id, template, role in self._conn.execute(
                "SELECT id, template, role FROM agents"
            ).fetchall():
                template = str(template or "").strip()
                role = str(role or "").strip()
                if not template:
                    continue
                key = ("agents", str(agent_id or ""))
                if template == role:
                    mirrored_rows.add(key)
                else:
                    drift_rows.add(key)

        if (
            self._column_exists("agents", "created_by_engineer_id")
            and self._column_exists("agents", "owner_engineer_id")
        ):
            for agent_id, legacy_owner, owner_engineer_id in self._conn.execute(
                "SELECT id, created_by_engineer_id, owner_engineer_id FROM agents"
            ).fetchall():
                legacy_owner = str(legacy_owner or "").strip()
                owner_engineer_id = str(owner_engineer_id or "").strip()
                if not legacy_owner:
                    continue
                key = ("agents", str(agent_id or ""))
                if legacy_owner == owner_engineer_id:
                    mirrored_rows.add(key)
                else:
                    drift_rows.add(key)

        if (
            self._column_exists("board_tasks", "engineer_owner_id")
            and self._column_exists("board_tasks", "assigned_engineer_id")
        ):
            for task_id, legacy_owner, assigned_engineer_id in self._conn.execute(
                "SELECT id, engineer_owner_id, assigned_engineer_id FROM board_tasks"
            ).fetchall():
                legacy_owner = str(legacy_owner or "").strip()
                assigned_engineer_id = str(assigned_engineer_id or "").strip()
                if not legacy_owner:
                    continue
                key = ("board_tasks", str(task_id or ""))
                if legacy_owner == assigned_engineer_id:
                    mirrored_rows.add(key)
                else:
                    drift_rows.add(key)

        return len(mirrored_rows), len(drift_rows)

    def _captured_table_supporting_sql(self, table: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name=? AND type IN ('index', 'trigger') "
            "AND sql IS NOT NULL ORDER BY type, name",
            (table,),
        ).fetchall()
        return [str(sql) for _type, _name, sql in rows if sql]

    def _rebuild_table_without_columns(
        self,
        table: str,
        *,
        new_table: str,
        drop_columns: set[str],
    ) -> None:
        columns = self._conn.execute(
            f"PRAGMA table_info({_quote_ident(table)})"
        ).fetchall()
        kept_columns = [row for row in columns if str(row[1]) not in drop_columns]
        if len(kept_columns) == len(columns):
            return

        defs: list[str] = []
        pk_columns: list[tuple[int, str]] = []
        for _cid, name, col_type, notnull, default, pk in kept_columns:
            parts = [_quote_ident(name)]
            if col_type:
                parts.append(str(col_type))
            if notnull:
                parts.append("NOT NULL")
            if default is not None:
                parts.append(f"DEFAULT {default}")
            defs.append(" ".join(parts))
            if pk:
                pk_columns.append((int(pk), str(name)))
        if pk_columns:
            ordered = ", ".join(
                _quote_ident(name) for _pk, name in sorted(pk_columns)
            )
            defs.append(f"PRIMARY KEY ({ordered})")

        create_sql = (
            f"CREATE TABLE {_quote_ident(new_table)} (\n    "
            + ",\n    ".join(defs)
            + "\n)"
        )
        supporting_sql = self._captured_table_supporting_sql(table)
        copy_columns = ", ".join(_quote_ident(row[1]) for row in kept_columns)

        self._conn.execute(create_sql)
        self._conn.execute(
            f"INSERT INTO {_quote_ident(new_table)} ({copy_columns}) "
            f"SELECT {copy_columns} FROM {_quote_ident(table)}"
        )
        self._conn.execute(f"DROP TABLE {_quote_ident(table)}")
        self._conn.execute(
            f"ALTER TABLE {_quote_ident(new_table)} RENAME TO {_quote_ident(table)}"
        )
        for sql in supporting_sql:
            self._conn.execute(sql)

    def _cleanup_kinds_legacy_columns_if_needed(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
        version = self._current_kinds_migration_version()
        if version >= _KINDS_CLEANUP_MIGRATION_VERSION:
            return

        agents_has_legacy = any(
            self._column_exists("agents", col)
            for col in ("template", "created_by_engineer_id")
        )
        tasks_has_legacy = self._column_exists("board_tasks", "engineer_owner_id")
        if version < _KINDS_BACKFILL_MIGRATION_VERSION and (
            agents_has_legacy or tasks_has_legacy
        ):
            return
        if not agents_has_legacy and not tasks_has_legacy:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_CLEANUP_MIGRATION_VERSION),
                ),
            )
            if manage_transaction:
                self._conn.commit()
            return

        mirrored_rows, drift_rows = self._legacy_cleanup_row_counts()
        if mirrored_rows > 0 and drift_rows == 0:
            log.warning(
                "migration cleanup: dropping legacy columns with %d mirrored rows",
                mirrored_rows,
            )

        try:
            if manage_transaction:
                self._conn.execute("BEGIN")
            self._rebuild_table_without_columns(
                "agents",
                new_table="agents_new",
                drop_columns={"template", "created_by_engineer_id"},
            )
            self._rebuild_table_without_columns(
                "board_tasks",
                new_table="board_tasks_new",
                drop_columns={"engineer_owner_id"},
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_CLEANUP_MIGRATION_VERSION),
                ),
            )
            if manage_transaction:
                self._conn.commit()
        except Exception:
            if manage_transaction:
                self._conn.rollback()
            raise


    def _backfill_empty_worker_kinds_if_needed(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
        version = self._current_kinds_migration_version()
        if version < _KINDS_CLEANUP_MIGRATION_VERSION:
            return
        if version >= _KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION:
            return

        updated = 0
        try:
            if manage_transaction:
                self._conn.execute("BEGIN")
            cursor = self._conn.execute(
                "UPDATE agents SET kind='worker' "
                "WHERE cell_type='agent' "
                "AND TRIM(COALESCE(kind, '')) = '' "
                "AND ("
                "TRIM(COALESCE(owner_engineer_id, '')) != '' "
                "OR COALESCE(persistent, 0) = 0"
                ")"
            )
            updated = max(0, int(cursor.rowcount or 0))
            migrated_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_WORKER_KIND_BACKFILL_MIGRATION_VERSION),
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY, migrated_at),
            )
            if manage_transaction:
                self._conn.commit()
        except Exception:
            if manage_transaction:
                self._conn.rollback()
            raise

        log.info(
            "migration: empty worker kind backfill applied (updated=%d)",
            updated,
        )

    def _load_group_engineer_map(self) -> dict[str, str]:
        valid_agent_ids_by_group: dict[str, set[str]] = {}
        for agent_id, group_name in self._conn.execute(
            "SELECT id, group_name FROM agents WHERE cell_type='agent'"
        ).fetchall():
            agent_id = str(agent_id or "").strip()
            group_name = str(group_name or "").strip()
            if not agent_id or not group_name:
                continue
            valid_agent_ids_by_group.setdefault(group_name, set()).add(agent_id)

        rows = self._conn.execute(
            "SELECT group_name, engineer_agent_id FROM group_settings"
        ).fetchall()
        group_engineer_map: dict[str, str] = {}
        for group_name, engineer_agent_id in rows:
            group_name = str(group_name or "").strip()
            if not group_name:
                continue
            engineer_agent_id = str(engineer_agent_id or "").strip()
            if (
                engineer_agent_id
                and engineer_agent_id
                in valid_agent_ids_by_group.get(group_name, set())
            ):
                group_engineer_map[group_name] = engineer_agent_id
                continue
            if engineer_agent_id:
                log.warning(
                    "migration: ignoring stale engineer_agent_id=%r for group=%r",
                    engineer_agent_id,
                    group_name,
                )
            group_engineer_map[group_name] = ""
        return group_engineer_map

    def _backfill_task_assignments_from_group_settings(
        self,
        group_engineer_map: dict[str, str],
        *,
        only_unassigned: bool,
        reset_stage1_fields: bool,
    ) -> int:
        updated = 0
        if only_unassigned:
            rows = self._conn.execute(
                "SELECT id, group_name FROM board_tasks WHERE assigned_engineer_id=''"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, group_name FROM board_tasks"
            ).fetchall()

        for task_id, group_name in rows:
            task_id = str(task_id or "").strip()
            if not task_id:
                continue
            assigned_engineer_id = group_engineer_map.get(
                str(group_name or "").strip(),
                "",
            )
            if only_unassigned:
                if not assigned_engineer_id:
                    continue
                self._conn.execute(
                    "UPDATE board_tasks SET assigned_engineer_id=? WHERE id=?",
                    (assigned_engineer_id, task_id),
                )
                updated += 1
                continue

            if reset_stage1_fields:
                self._conn.execute(
                    "UPDATE board_tasks "
                    "SET assigned_engineer_id=?, "
                    "created_by_architect_id='', "
                    "suggested_action='' "
                    "WHERE id=?",
                    (assigned_engineer_id, task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE board_tasks SET assigned_engineer_id=? WHERE id=?",
                    (assigned_engineer_id, task_id),
                )
        return updated

    def _backfill_kinds_if_needed(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
        version = self._current_kinds_migration_version()
        if version < _KINDS_SCHEMA_MIGRATION_VERSION:
            return
        if version >= _KINDS_BACKFILL_MIGRATION_VERSION:
            return

        engineer_id, proceed = self._resolve_engineer_backfill_target()
        if not proceed:
            return

        worker_count = 0
        task_count = 0
        try:
            if manage_transaction:
                self._conn.execute("BEGIN")

            engineer_name = engineer_slug = ""
            if engineer_id:
                engineer_name, engineer_slug = self._resolve_backfilled_engineer_identity(
                    engineer_id
                )

            template_sql = self._optional_column_sql("agents", "template")
            created_by_sql = self._optional_column_sql(
                "agents", "created_by_engineer_id"
            )
            rows = self._conn.execute(
                f"SELECT id, cell_type, {template_sql}, {created_by_sql} "
                "FROM agents"
            ).fetchall()
            for agent_id, cell_type, template, created_by_engineer_id in rows:
                agent_id = str(agent_id or "").strip()
                cell_type = str(cell_type or "").strip()
                if not agent_id:
                    continue
                if cell_type == "terminal":
                    self._conn.execute(
                        "UPDATE agents SET kind='terminal' WHERE id=?",
                        (agent_id,),
                    )
                    continue
                if cell_type != "agent":
                    continue
                if engineer_id and agent_id == engineer_id:
                    self._conn.execute(
                        "UPDATE agents "
                        "SET name=?, slug=?, kind='engineer', role='', "
                        "owner_engineer_id='', hired_by_architect_id='', persistent=1 "
                        "WHERE id=?",
                        (engineer_name, engineer_slug, agent_id),
                    )
                    continue
                self._conn.execute(
                    "UPDATE agents "
                    "SET kind='worker', role=?, owner_engineer_id=?, "
                    "hired_by_architect_id='', persistent=0 "
                    "WHERE id=?",
                    (
                        str(template or ""),
                        str(created_by_engineer_id or ""),
                        agent_id,
                    ),
                )
                worker_count += 1

            task_count = int(
                self._conn.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0]
            )
            group_engineer_map = self._load_group_engineer_map()
            configured_torque_engineer_id = group_engineer_map.get(_KINDS_ENGINEER_GROUP, "")
            self._backfill_task_assignments_from_group_settings(
                group_engineer_map,
                only_unassigned=False,
                reset_stage1_fields=True,
            )

            migrated_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (
                    _KINDS_SCHEMA_MIGRATION_VERSION_KEY,
                    str(_KINDS_BACKFILL_MIGRATION_VERSION),
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_SCHEMA_MIGRATION_MIGRATED_AT_KEY, migrated_at),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY, "1"),
            )
            if manage_transaction:
                self._conn.commit()
        except Exception:
            if manage_transaction:
                self._conn.rollback()
            raise

        log.info(
            "migration: kinds backfill applied (engineer=%s, workers=%d, tasks=%d)",
            engineer_id,
            worker_count,
            task_count,
        )

    def _fixup_kinds_task_assignments_if_needed(
        self,
        *,
        manage_transaction: bool = True,
    ) -> None:
        version = self._current_kinds_migration_version()
        if version != _KINDS_BACKFILL_MIGRATION_VERSION:
            return

        fixup_applied = str(
            self._read_meta_value(_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY) or ""
        ).strip()
        if fixup_applied == "1":
            return

        updated = 0
        try:
            if manage_transaction:
                self._conn.execute("BEGIN")
            group_engineer_map = self._load_group_engineer_map()
            updated = self._backfill_task_assignments_from_group_settings(
                group_engineer_map,
                only_unassigned=True,
                reset_stage1_fields=False,
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KINDS_TASK_ASSIGNMENT_FIXUP_APPLIED_KEY, "1"),
            )
            if manage_transaction:
                self._conn.commit()
        except Exception:
            if manage_transaction:
                self._conn.rollback()
            raise

        log.info("migration: kinds task assignment fixup applied (updated=%d)", updated)

    # -- Migration ----------------------------------------------------------

    def migrate_from_json(self, json_path: Path):
        """Import state from a state.json file into SQLite."""
        if not json_path.exists():
            return False
        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cannot read %s for migration: %s", json_path, exc)
            return False

        log.info("Migrating state from %s to SQLite", json_path)
        self.save_all(data)

        # Rename to .bak
        bak = json_path.with_suffix(".json.bak")
        try:
            json_path.rename(bak)
            log.info("Renamed %s → %s", json_path.name, bak.name)
        except OSError as exc:
            log.warning("Could not rename %s: %s", json_path, exc)

        return True
