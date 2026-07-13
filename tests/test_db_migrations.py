"""Versioned SQLite migration ledger regression coverage."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from torque.db import TorqueDB, _create_sqlite_backup
from torque.db_schema import (
    AGENT_CLASS_AUDIT_COLUMNS,
    AGENT_KIND_COLUMNS,
    AGENT_LIFECYCLE_COLUMNS,
    AGENT_MESSAGE_LOOP_RUNTIME_COLUMNS,
    BOARD_TASK_ROUTING_COLUMNS,
    DECISION_COLUMNS,
    ENGINEER_JOURNAL_PROVENANCE_COLUMNS,
    PENDING_HIRE_LIFECYCLE_COLUMNS,
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
    SchemaMigration,
    _apply_schema_migrations,
    finalize_database_migrations,
    initialize_database,
)


class SchemaMigrationLedgerTests(unittest.TestCase):
    @staticmethod
    def _post_init_runners(kinds_runner=lambda: None):
        return {8: kinds_runner, 9: lambda: None, 10: lambda: None}

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
        self.assertEqual(ledger[-1], (11, "engineer_journal_provenance"))
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
        self.assertEqual(ledger[-1], (11, "engineer_journal_provenance"))
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
        self.assertEqual(ledger[-1], (11, "engineer_journal_provenance"))

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
        self.assertEqual(after[-1], (11, "engineer_journal_provenance"))
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
