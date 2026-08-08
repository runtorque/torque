"""Versioned SQLite migration ledger regression coverage."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from torque.db import TorqueDB, _create_sqlite_backup
from torque.db_schema import (
    AGENT_DIGEST_RUNTIME_COLUMNS,
    AGENT_CLASS_AUDIT_COLUMNS,
    AGENT_KIND_COLUMNS,
    AGENT_LIFECYCLE_COLUMNS,
    AGENT_MESSAGE_LOOP_RUNTIME_COLUMNS,
    AGENT_RUNTIME_COLUMNS,
    AUTO_DISPATCH_RUNTIME_COLUMNS,
    BOARD_TASK_COMPLETION_COLUMNS,
    BOARD_TASK_ROUTING_COLUMNS,
    BOARD_TASK_RUNTIME_COLUMNS,
    DECISION_COLUMNS,
    ENGINEER_JOURNAL_PROVENANCE_COLUMNS,
    ENGINEER_SETTINGS_COLUMNS,
    GLOBAL_SETTINGS_CACHE_COLUMNS,
    GROUP_CONTEXT_TTL_COLUMNS,
    GROUP_OPERATIONAL_COLUMNS,
    GROUP_PROVIDER_RUNTIME_COLUMNS,
    MEMORY_RETENTION_COLUMNS,
    PENDING_HIRE_LIFECYCLE_COLUMNS,
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
    SLUGGED_ENTITY_TABLES,
    SchemaMigration,
    _apply_schema_migrations,
    finalize_database_migrations,
    initialize_database,
)


class SchemaMigrationLedgerTests(unittest.TestCase):
    @staticmethod
    def _post_init_runners(kinds_runner=lambda: None):
        return {
            8: kinds_runner,
            9: lambda: None,
            10: lambda: None,
            28: lambda: None,
        }

    def test_fresh_database_records_contiguous_migration_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            self.addCleanup(db.close)
            db.init()

            rows = db._conn.execute(
                "SELECT version, name, checksum FROM schema_migrations "
                "ORDER BY version"
            ).fetchall()
            meta_version = db._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()

        self.assertEqual(
            [row[0] for row in rows],
            [migration.version for migration in SCHEMA_MIGRATIONS],
        )
        self.assertEqual(
            [(row[1], row[2]) for row in rows],
            [
                (migration.name, migration.checksum)
                for migration in SCHEMA_MIGRATIONS
            ],
        )
        self.assertEqual(meta_version, (SCHEMA_VERSION,))

    def test_meta_only_legacy_database_is_adopted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('legacy_marker', 'kept')"
            )
            conn.commit()
            conn.close()

            first = TorqueDB(path)
            first.init()
            first_rows = first._conn.execute(
                "SELECT version, applied_at FROM schema_migrations "
                "ORDER BY version"
            ).fetchall()
            first.close()

            second = TorqueDB(path)
            self.addCleanup(second.close)
            second.init()
            second_rows = second._conn.execute(
                "SELECT version, applied_at FROM schema_migrations "
                "ORDER BY version"
            ).fetchall()
            marker = second._conn.execute(
                "SELECT value FROM meta WHERE key='legacy_marker'"
            ).fetchone()

        self.assertEqual(first_rows, second_rows)
        self.assertEqual(marker, ("kept",))

    def test_post_baseline_migration_and_ledger_row_are_atomic(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)

        def baseline(connection, _backfill):
            connection.execute(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

        def failing(connection, _backfill):
            connection.execute("CREATE TABLE must_rollback (id INTEGER)")
            raise RuntimeError("injected migration failure")

        migrations = (
            SchemaMigration(1, "baseline", "v1", baseline),
            SchemaMigration(2, "failing", "v2", failing),
        )

        with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
            _apply_schema_migrations(conn, lambda: None, migrations=migrations)

        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        meta_version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        rolled_back = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='must_rollback'"
        ).fetchone()
        self.assertEqual(rows, [(1,)])
        self.assertEqual(meta_version, ("1",))
        self.assertIsNone(rolled_back)

    def test_checksum_drift_fails_closed(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)

        def baseline(connection, _backfill):
            connection.execute(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

        first = (SchemaMigration(1, "baseline", "original", baseline),)
        _apply_schema_migrations(conn, lambda: None, migrations=first)

        changed = (SchemaMigration(1, "baseline", "changed", baseline),)
        with self.assertRaisesRegex(RuntimeError, "ledger mismatch"):
            _apply_schema_migrations(conn, lambda: None, migrations=changed)

    def test_applied_ledger_repairs_stale_meta_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            self.addCleanup(db.close)
            db.init()
            db._conn.execute(
                "UPDATE meta SET value='stale' WHERE key='schema_version'"
            )
            db._conn.commit()

            _apply_schema_migrations(db._conn, db.backfill_agent_history)
            repaired = db._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()

        self.assertEqual(repaired, (SCHEMA_VERSION,))

    def test_version_three_owns_board_task_routing_columns_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "torque.db"
            current = TorqueDB(path)
            current.init()
            current.close()

            conn = sqlite3.connect(path)
            for column in BOARD_TASK_ROUTING_COLUMNS:
                conn.execute(f"ALTER TABLE board_tasks DROP COLUMN {column}")
            conn.execute("DELETE FROM schema_migrations WHERE version>=3")
            conn.execute(
                "UPDATE meta SET value='2' WHERE key='schema_version'"
            )
            conn.commit()
            conn.close()

            upgraded = TorqueDB(path)
            self.addCleanup(upgraded.close)
            upgraded.init()

            columns = {
                row[1]
                for row in upgraded._conn.execute(
                    "PRAGMA table_info(board_tasks)"
                ).fetchall()
            }
            ledger = upgraded._conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            backup_path = path.with_name(
                f"torque.db.pre-schema-v{SCHEMA_VERSION}.bak"
            )
            backup_mtime = backup_path.stat().st_mtime_ns

            backup = sqlite3.connect(backup_path)
            self.addCleanup(backup.close)
            backup_columns = {
                row[1]
                for row in backup.execute(
                    "PRAGMA table_info(board_tasks)"
                ).fetchall()
            }
            backup_version = backup.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()

            upgraded.close()
            rerun = TorqueDB(path)
            self.addCleanup(rerun.close)
            rerun.init()
            rerun_backup_mtime = backup_path.stat().st_mtime_ns

        self.assertTrue(set(BOARD_TASK_ROUTING_COLUMNS) <= columns)
        self.assertIn((3, "board_task_routing_contract"), ledger)
        self.assertEqual(ledger[-1], (30, "per_agent_settings"))
        self.assertFalse(set(BOARD_TASK_ROUTING_COLUMNS) & backup_columns)
        self.assertEqual(backup_version, (2,))
        self.assertEqual(rerun_backup_mtime, backup_mtime)

    def test_version_four_owns_agent_lifecycle_and_class_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "torque.db"
            current = TorqueDB(path)
            current.init()
            current.close()

            conn = sqlite3.connect(path)
            for column in AGENT_LIFECYCLE_COLUMNS:
                conn.execute(f"ALTER TABLE agents DROP COLUMN {column}")
            for column in AGENT_MESSAGE_LOOP_RUNTIME_COLUMNS:
                conn.execute(
                    f"ALTER TABLE agent_message_loops DROP COLUMN {column}"
                )
            for column in PENDING_HIRE_LIFECYCLE_COLUMNS:
                conn.execute(f"ALTER TABLE pending_hires DROP COLUMN {column}")
            conn.execute("DROP TABLE agent_class_audit")
            conn.execute("DROP TABLE decisions")
            conn.execute("DELETE FROM schema_migrations WHERE version>=4")
            conn.execute(
                "UPDATE meta SET value='3' WHERE key='schema_version'"
            )
            conn.commit()
            conn.close()

            upgraded = TorqueDB(path)
            self.addCleanup(upgraded.close)
            upgraded.init()

            agent_columns = {
                row[1]
                for row in upgraded._conn.execute(
                    "PRAGMA table_info(agents)"
                ).fetchall()
            }
            loop_columns = {
                row[1]
                for row in upgraded._conn.execute(
                    "PRAGMA table_info(agent_message_loops)"
                ).fetchall()
            }
            hire_columns = {
                row[1]
                for row in upgraded._conn.execute(
                    "PRAGMA table_info(pending_hires)"
                ).fetchall()
            }
            audit_columns = {
                row[1]
                for row in upgraded._conn.execute(
                    "PRAGMA table_info(agent_class_audit)"
                ).fetchall()
            }
            decision_columns = {
                row[1]
                for row in upgraded._conn.execute(
                    "PRAGMA table_info(decisions)"
                ).fetchall()
            }
            ledger = upgraded._conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()

            backup_path = path.with_name(
                f"torque.db.pre-schema-v{SCHEMA_VERSION}.bak"
            )
            backup = sqlite3.connect(backup_path)
            backup_agent_columns = {
                row[1]
                for row in backup.execute("PRAGMA table_info(agents)")
            }
            backup_tables = {
                row[0]
                for row in backup.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            backup_version = backup.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            backup.close()

        self.assertTrue(set(AGENT_LIFECYCLE_COLUMNS) <= agent_columns)
        self.assertTrue(
            set(AGENT_MESSAGE_LOOP_RUNTIME_COLUMNS) <= loop_columns
        )
        self.assertTrue(set(PENDING_HIRE_LIFECYCLE_COLUMNS) <= hire_columns)
        self.assertTrue(set(AGENT_CLASS_AUDIT_COLUMNS) <= audit_columns)
        self.assertTrue(set(DECISION_COLUMNS) <= decision_columns)
        self.assertIn((4, "agent_lifecycle_contract"), ledger)
        self.assertEqual(ledger[-1], (30, "per_agent_settings"))
        self.assertFalse(set(AGENT_LIFECYCLE_COLUMNS) & backup_agent_columns)
        self.assertNotIn("agent_class_audit", backup_tables)
        self.assertNotIn("decisions", backup_tables)
        self.assertEqual(backup_version, (3,))

    def test_version_seven_owns_agent_kind_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "torque.db"
            current = TorqueDB(path)
            current.init()
            current.close()

            conn = sqlite3.connect(path)
            for column in AGENT_KIND_COLUMNS:
                conn.execute(f"ALTER TABLE agents DROP COLUMN {column}")
            conn.execute("DELETE FROM schema_migrations WHERE version>=7")
            conn.execute(
                "UPDATE meta SET value='6' WHERE key='schema_version'"
            )
            conn.commit()
            conn.close()

            upgraded = TorqueDB(path)
            self.addCleanup(upgraded.close)
            upgraded.init()
            columns = {
                row[1]
                for row in upgraded._conn.execute("PRAGMA table_info(agents)")
            }
            ledger = upgraded._conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertTrue(set(AGENT_KIND_COLUMNS) <= columns)
        self.assertIn((7, "agent_kind_schema"), ledger)
        self.assertEqual(ledger[-1], (30, "per_agent_settings"))

    def test_version_twenty_five_owns_finalization_policy_columns(self):
        finalization_columns = (
            "finalization_mode",
            "required_review_gates",
            "finalization_boundary",
            "finalization_audit",
            "finalization_status",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "torque.db"
            current = TorqueDB(path)
            current.init()
            current.close()

            conn = sqlite3.connect(path)
            for column in finalization_columns:
                conn.execute(f"ALTER TABLE board_tasks DROP COLUMN {column}")
            conn.execute("DELETE FROM schema_migrations WHERE version>=25")
            conn.execute(
                "UPDATE meta SET value='24' WHERE key='schema_version'"
            )
            conn.commit()
            conn.close()

            upgraded = TorqueDB(path)
            self.addCleanup(upgraded.close)
            upgraded.init()
            columns = {
                row[1]
                for row in upgraded._conn.execute("PRAGMA table_info(board_tasks)")
            }
            ledger = upgraded._conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertTrue(set(finalization_columns) <= columns)
        self.assertEqual(ledger[-1], (30, "per_agent_settings"))

    def test_version_twenty_six_owns_group_context_ttl_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "torque.db"
            current = TorqueDB(path)
            current.init()
            current.close()

            conn = sqlite3.connect(path)
            conn.execute(
                "ALTER TABLE group_settings DROP COLUMN context_default_ttl_days"
            )
            conn.execute("DELETE FROM schema_migrations WHERE version>=26")
            conn.execute(
                "UPDATE meta SET value='25' WHERE key='schema_version'"
            )
            conn.commit()
            conn.close()

            upgraded = TorqueDB(path)
            self.addCleanup(upgraded.close)
            upgraded.init()
            columns = {
                row[1]
                for row in upgraded._conn.execute("PRAGMA table_info(group_settings)")
            }
            ledger = upgraded._conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertTrue(set(GROUP_CONTEXT_TTL_COLUMNS) <= columns)
        self.assertEqual(ledger[-1], (30, "per_agent_settings"))

    def test_post_init_phase_waits_for_kinds_stage_four(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)

        initialize_database(conn, lambda: None)
        before = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        before_meta = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        skipped = finalize_database_migrations(conn, lambda: None)

        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        conn.commit()
        finalized = finalize_database_migrations(
            conn,
            lambda: None,
            post_init_runners=self._post_init_runners(),
        )
        after = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        after_meta = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()

        self.assertEqual(before, [(version,) for version in range(1, 8)])
        self.assertEqual(before_meta, ("7",))
        self.assertFalse(skipped)
        self.assertTrue(finalized)
        self.assertEqual(after[-1], (30, "per_agent_settings"))
        self.assertEqual(after_meta, (SCHEMA_VERSION,))

    def test_post_init_runner_is_retryable_and_skipped_after_ledger(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        calls = []

        def incomplete_runner():
            calls.append("incomplete")

        self.assertFalse(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(incomplete_runner),
            )
        )

        def completing_runner():
            calls.append("complete")
            conn.execute(
                "INSERT INTO meta(key, value) VALUES "
                "('schema_kinds_migration_version', '4')"
            )

        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(completing_runner),
            )
        )

        def must_not_run():
            raise AssertionError("completed post-init runner was called again")

        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(must_not_run),
            )
        )
        self.assertEqual(calls, ["incomplete", "complete"])

    def test_automatic_post_init_migration_repairs_journal_provenance(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        conn.commit()

        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(),
            )
        )

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(engineer_journal)")
        }
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(engineer_journal)")
        }
        ledger = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=11"
        ).fetchone()
        self.assertTrue(set(ENGINEER_JOURNAL_PROVENANCE_COLUMNS) <= columns)
        self.assertIn("idx_engineer_journal_group_author", indexes)
        self.assertIn("idx_engineer_journal_source_key", indexes)
        self.assertEqual(ledger, ("engineer_journal_provenance",))

    def test_automatic_post_init_migration_repairs_entity_slugs(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        for table in SLUGGED_ENTITY_TABLES:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN slug")
        conn.commit()

        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(),
            )
        )

        for table in SLUGGED_ENTITY_TABLES:
            columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            self.assertIn("slug", columns)
        ledger = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=12"
        ).fetchone()
        self.assertEqual(ledger, ("persisted_entity_slugs",))

    def test_automatic_post_init_migration_repairs_provider_runtime_settings(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        for column in GROUP_PROVIDER_RUNTIME_COLUMNS:
            conn.execute(f"ALTER TABLE group_settings DROP COLUMN {column}")
        conn.commit()

        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(),
            )
        )

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(group_settings)")
        }
        ledger = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=13"
        ).fetchone()
        self.assertTrue(set(GROUP_PROVIDER_RUNTIME_COLUMNS) <= columns)
        self.assertEqual(ledger, ("group_provider_runtime_settings",))

    def test_applied_provider_contract_repairs_without_reledgering(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        conn.commit()
        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(),
            )
        )
        applied_at = conn.execute(
            "SELECT applied_at FROM schema_migrations WHERE version=13"
        ).fetchone()
        for column in GROUP_PROVIDER_RUNTIME_COLUMNS:
            conn.execute(f"ALTER TABLE group_settings DROP COLUMN {column}")
        conn.commit()

        initialize_database(conn, lambda: None)

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(group_settings)")
        }
        repaired_applied_at = conn.execute(
            "SELECT applied_at FROM schema_migrations WHERE version=13"
        ).fetchone()
        self.assertTrue(set(GROUP_PROVIDER_RUNTIME_COLUMNS) <= columns)
        self.assertEqual(repaired_applied_at, applied_at)

    def test_applied_operational_contracts_repair_partial_schema(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        conn.commit()
        self.assertTrue(
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(),
            )
        )
        ledger_before = conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        contracts = (
            ("board_tasks", BOARD_TASK_RUNTIME_COLUMNS),
            ("board_tasks", BOARD_TASK_COMPLETION_COLUMNS),
            ("agents", AGENT_RUNTIME_COLUMNS),
            ("group_settings", GROUP_OPERATIONAL_COLUMNS),
            ("engineer_settings", ENGINEER_SETTINGS_COLUMNS),
            ("agent_digest_settings", AGENT_DIGEST_RUNTIME_COLUMNS),
            ("global_settings", GLOBAL_SETTINGS_CACHE_COLUMNS),
            ("auto_dispatch_queue", AUTO_DISPATCH_RUNTIME_COLUMNS),
        )
        for table, columns in contracts:
            for column in columns:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        for index_name in (
            "idx_memory_entries_scope",
            "idx_memory_entries_group",
            "idx_memory_entries_project",
            "idx_memory_entries_expiry",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")
        for column in MEMORY_RETENTION_COLUMNS:
            conn.execute(f"ALTER TABLE memory_entries DROP COLUMN {column}")
        conn.commit()

        initialize_database(conn, lambda: None)

        for table, columns in contracts + (
            ("memory_entries", MEMORY_RETENTION_COLUMNS),
        ):
            actual = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            self.assertTrue(set(columns) <= actual, table)
        ledger_after = conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(ledger_after, ledger_before)

    def test_post_init_runner_and_ledger_row_roll_back_together(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)

        def failing_runner():
            conn.execute(
                "INSERT INTO meta(key, value) VALUES "
                "('schema_kinds_migration_version', '4')"
            )
            conn.execute("CREATE TABLE must_rollback_post_init(id INTEGER)")
            raise RuntimeError("injected post-init failure")

        with self.assertRaisesRegex(RuntimeError, "injected post-init"):
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners=self._post_init_runners(failing_runner),
            )

        marker = conn.execute(
            "SELECT value FROM meta "
            "WHERE key='schema_kinds_migration_version'"
        ).fetchone()
        ledger = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=8"
        ).fetchone()
        table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='must_rollback_post_init'"
        ).fetchone()
        self.assertIsNone(marker)
        self.assertIsNone(ledger)
        self.assertIsNone(table)

    def test_later_post_init_runner_rolls_back_without_losing_prior_entry(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn, lambda: None)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES "
            "('schema_kinds_migration_version', '4')"
        )
        conn.commit()

        def failing_digest_runner():
            conn.execute("CREATE TABLE digest_runner_rollback(id INTEGER)")
            raise RuntimeError("injected digest runner failure")

        with self.assertRaisesRegex(RuntimeError, "injected digest"):
            finalize_database_migrations(
                conn,
                lambda: None,
                post_init_runners={
                    8: lambda: None,
                    9: failing_digest_runner,
                    10: lambda: None,
                },
            )

        ledger = conn.execute(
            "SELECT version FROM schema_migrations WHERE version>=8 "
            "ORDER BY version"
        ).fetchall()
        table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='digest_runner_rollback'"
        ).fetchone()
        self.assertEqual(ledger, [(8,)])
        self.assertIsNone(table)

    @staticmethod
    def _memory_entry(entry_id, *, created_at, expires_at, pinned=False,
                      retention_kind="durable"):
        return {
            "id": entry_id,
            "project_key": "/synthetic",
            "group_name": "g",
            "scope_kind": "group",
            "scope_ref": "g",
            "entry_type": "decision",
            "title": entry_id,
            "content": "Synthetic migration fixture.",
            "pinned": pinned,
            "task_id": "",
            "source_kind": "agent",
            "source_id": "agent-1",
            "source_name": "Worker",
            "retention_kind": retention_kind,
            "expires_at": expires_at,
            "created_at": created_at,
            "updated_at": created_at,
        }

    @staticmethod
    def _make_legacy_memory_ttl_migration_pending(db):
        """Reset synthetic version-28+ state to exercise the v28 runner."""
        db._conn.execute("DELETE FROM schema_migrations WHERE version>=28")
        db._conn.execute(
            "UPDATE meta SET value='27' WHERE key='schema_version'"
        )
        db._conn.execute(
            "DELETE FROM meta WHERE key='legacy_memory_ttl_backfill_version'"
        )
        db._conn.commit()

    def test_legacy_memory_ttl_runner_is_ledgered_backed_up_and_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-memory-ttl.db"
            now = 2_000_000_000.0
            initial = TorqueDB(path)
            initial.init()
            initial.save_memory_entry(self._memory_entry(
                "older-than-60-days", created_at=now - 61 * 24 * 60 * 60,
                expires_at=None,
            ))
            initial.save_memory_entry(self._memory_entry(
                "45-days-old", created_at=now - 45 * 24 * 60 * 60,
                expires_at=None,
            ))
            initial.save_memory_entry(self._memory_entry(
                "pinned-formerly-durable", created_at=now - 20 * 24 * 60 * 60,
                expires_at=None, pinned=True,
            ))
            fresh_expiry = now + 30 * 24 * 60 * 60
            initial.save_memory_entry(self._memory_entry(
                "fresh-already-stamped", created_at=now,
                expires_at=fresh_expiry, retention_kind="ttl",
            ))
            initial.save_memory_link({
                "entry_id": "older-than-60-days",
                "target_kind": "task",
                "target_ref": "TORQUE:synthetic",
                "created_at": now,
            })
            self._make_legacy_memory_ttl_migration_pending(initial)
            initial.close()

            backup_path = path.with_name(
                f"{path.name}.pre-schema-v{SCHEMA_VERSION}.bak"
            )
            backup_path.unlink(missing_ok=True)
            migrated = TorqueDB(path)
            self.addCleanup(migrated.close)
            with mock.patch(
                "torque.persistence.migrations.time.time", return_value=now
            ), mock.patch(
                "torque.db_memory.time.time", return_value=now
            ), self.assertLogs("torque", level="INFO") as logs:
                migrated.init()

            report = next(
                line for line in logs.output
                if "legacy shared-memory TTL" in line
            )
            rows = migrated._conn.execute(
                "SELECT id, retention_kind, expires_at FROM memory_entries "
                "ORDER BY id"
            ).fetchall()
            ledger = migrated._conn.execute(
                "SELECT name, checksum FROM schema_migrations WHERE version=28"
            ).fetchone()
            backup = sqlite3.connect(backup_path)
            self.addCleanup(backup.close)
            backup_version = backup.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            backup_expiry = backup.execute(
                "SELECT expires_at FROM memory_entries "
                "WHERE id='older-than-60-days'"
            ).fetchone()
            backup_exists = backup_path.exists()

        migration = next(item for item in SCHEMA_MIGRATIONS if item.version == 28)
        self.assertEqual(SCHEMA_VERSION, "30")
        self.assertEqual(migration.version, 28)
        self.assertEqual(migration.name, "legacy_memory_ttl_backfill")
        self.assertTrue(migration.requires_runner)
        self.assertEqual(ledger, (migration.name, migration.checksum))
        self.assertTrue(backup_exists)
        self.assertEqual(backup_version, (27,))
        self.assertEqual(backup_expiry, (None,))
        self.assertIn("before=4", report)
        self.assertIn("stamped=3", report)
        self.assertIn("already-stamped-and-skipped=1", report)
        self.assertIn("expired-by-stamping=1", report)
        # The post-init runner precedes the startup sweep: 4 before -> 3 after.
        self.assertEqual([row[0] for row in rows], [
            "45-days-old", "fresh-already-stamped", "pinned-formerly-durable",
        ])
        self.assertEqual(rows[0][1:], ("ttl", now + 15 * 24 * 60 * 60))
        self.assertEqual(rows[1][2], fresh_expiry)
        self.assertEqual(rows[2][1:], ("ttl", now + 40 * 24 * 60 * 60))
        # Links use ON DELETE CASCADE, so the expired entry leaves no dangling row.
        self.assertEqual(
            migrated.load_memory_links(entry_id="older-than-60-days"), []
        )

        expiry_before_retry = migrated._conn.execute(
            "SELECT id, expires_at FROM memory_entries ORDER BY id"
        ).fetchall()
        self._make_legacy_memory_ttl_migration_pending(migrated)
        with mock.patch(
            "torque.persistence.migrations.time.time", return_value=now
        ):
            retry_counts = migrated._migrate_legacy_memory_entries_to_ttl_if_needed()
        expiry_after_retry = migrated._conn.execute(
            "SELECT id, expires_at FROM memory_entries ORDER BY id"
        ).fetchall()
        self.assertEqual(retry_counts, {
            "before": 3,
            "stamped": 0,
            "already_stamped_skipped": 3,
            "expired_by_stamping": 0,
        })
        self.assertEqual(expiry_after_retry, expiry_before_retry)

    def test_legacy_memory_ttl_runner_logs_already_stamped_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "already-stamped-memory.db"
            now = 2_000_000_000.0
            db = TorqueDB(path)
            db.init()
            db.save_memory_entry(self._memory_entry(
                "fresh", created_at=now,
                expires_at=now + 30 * 24 * 60 * 60, retention_kind="ttl",
            ))
            self._make_legacy_memory_ttl_migration_pending(db)
            db.close()

            migrated = TorqueDB(path)
            self.addCleanup(migrated.close)
            with mock.patch(
                "torque.persistence.migrations.time.time", return_value=now
            ), mock.patch(
                "torque.db_memory.time.time", return_value=now
            ), self.assertLogs("torque", level="INFO") as logs:
                migrated.init()

        report = next(
            line for line in logs.output
            if "legacy shared-memory TTL" in line
        )
        self.assertIn("before=1", report)
        self.assertIn("stamped=0", report)
        self.assertIn("already-stamped-and-skipped=1", report)
        self.assertIn("expired-by-stamping=0", report)

    def test_legacy_memory_ttl_runner_logs_benign_empty_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty-memory.db"
            db = TorqueDB(path)
            db.init()
            self._make_legacy_memory_ttl_migration_pending(db)
            db.close()

            migrated = TorqueDB(path)
            self.addCleanup(migrated.close)
            with self.assertLogs("torque", level="INFO") as logs:
                migrated.init()

        report = next(
            line for line in logs.output
            if "legacy shared-memory TTL" in line
        )
        self.assertIn("before=0", report)
        self.assertIn("stamped=0", report)
        self.assertIn("already-stamped-and-skipped=0", report)
        self.assertIn("expired-by-stamping=0", report)

    def test_legacy_memory_ttl_runner_failure_during_stamp_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "rollback-during-stamp.db")
            self.addCleanup(db.close)
            db.init()
            db.save_memory_entry(self._memory_entry(
                "legacy", created_at=1.0, expires_at=None,
            ))
            self._make_legacy_memory_ttl_migration_pending(db)

            def failing_stamp():
                db._conn.execute(
                    "UPDATE memory_entries SET expires_at=999 WHERE id='legacy'"
                )
                raise RuntimeError("injected stamp failure")

            with mock.patch.object(db, "_stamp_legacy_memory_entries_to_ttl",
                                   side_effect=failing_stamp):
                with self.assertRaisesRegex(RuntimeError, "injected stamp"):
                    finalize_database_migrations(
                        db._conn,
                        db.backfill_agent_history,
                        post_init_runners=db._post_init_migration_runners(),
                    )

            expiry = db._conn.execute(
                "SELECT expires_at FROM memory_entries WHERE id='legacy'"
            ).fetchone()
            gate = db._conn.execute(
                "SELECT value FROM meta WHERE key='legacy_memory_ttl_backfill_version'"
            ).fetchone()
            ledger = db._conn.execute(
                "SELECT version FROM schema_migrations WHERE version=28"
            ).fetchone()

        self.assertEqual(expiry, (None,))
        self.assertIsNone(gate)
        self.assertIsNone(ledger)

    def test_legacy_memory_ttl_runner_failure_after_stamp_rolls_back_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "rollback-after-stamp.db")
            self.addCleanup(db.close)
            db.init()
            db.save_memory_entry(self._memory_entry(
                "legacy", created_at=1.0, expires_at=None,
            ))
            self._make_legacy_memory_ttl_migration_pending(db)
            db._conn.execute("""
                CREATE TRIGGER fail_ttl_ledger
                BEFORE INSERT ON schema_migrations
                WHEN NEW.version=28
                BEGIN
                    SELECT RAISE(ABORT, 'injected ledger failure');
                END
            """)
            db._conn.commit()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected ledger"):
                finalize_database_migrations(
                    db._conn,
                    db.backfill_agent_history,
                    post_init_runners=db._post_init_migration_runners(),
                )

            expiry = db._conn.execute(
                "SELECT expires_at FROM memory_entries WHERE id='legacy'"
            ).fetchone()
            gate = db._conn.execute(
                "SELECT value FROM meta WHERE key='legacy_memory_ttl_backfill_version'"
            ).fetchone()
            ledger = db._conn.execute(
                "SELECT version FROM schema_migrations WHERE version=28"
            ).fetchone()
            self.assertEqual(expiry, (None,))
            self.assertIsNone(gate)
            self.assertIsNone(ledger)

            # With no stranded body gate, a retry can ledger the migration.
            db._conn.execute("DROP TRIGGER fail_ttl_ledger")
            db._conn.commit()
            self.assertTrue(finalize_database_migrations(
                db._conn,
                db.backfill_agent_history,
                post_init_runners=db._post_init_migration_runners(),
            ))
            retry_ledger = db._conn.execute(
                "SELECT version FROM schema_migrations WHERE version=28"
            ).fetchone()

        self.assertEqual(retry_ledger, (28,))

    def test_legacy_memory_ttl_has_one_ledgered_invocation_path(self):
        import inspect

        init_source = inspect.getsource(TorqueDB.init)
        runners_source = inspect.getsource(TorqueDB._post_init_migration_runners)
        self.assertNotIn(
            "self._migrate_legacy_memory_entries_to_ttl_if_needed()",
            init_source,
        )
        self.assertEqual(
            runners_source.count("_migrate_legacy_memory_entries_to_ttl_if_needed("),
            1,
        )

    def test_atomic_backup_failure_leaves_source_and_destination_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "source.db"
            backup_path = Path(tmp) / "source.db.pre-schema-test.bak"
            conn = sqlite3.connect(source_path)
            conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker(value) VALUES ('kept')")
            conn.commit()
            conn.close()

            with mock.patch(
                "torque.db.os.replace",
                side_effect=OSError("injected replace failure"),
            ):
                with self.assertRaisesRegex(OSError, "injected replace"):
                    _create_sqlite_backup(source_path, backup_path)

            check = sqlite3.connect(source_path)
            self.addCleanup(check.close)
            marker = check.execute("SELECT value FROM marker").fetchone()
            temporary_files = list(Path(tmp).glob(".*.tmp"))
            backup_exists = backup_path.exists()

        self.assertEqual(marker, ("kept",))
        self.assertFalse(backup_exists)
        self.assertEqual(temporary_files, [])


if __name__ == "__main__":
    unittest.main()
