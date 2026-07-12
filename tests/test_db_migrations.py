"""Versioned SQLite migration ledger regression coverage."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from torque.db import TorqueDB
from torque.db_schema import (
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
    SchemaMigration,
    _apply_schema_migrations,
)


class SchemaMigrationLedgerTests(unittest.TestCase):
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
        rolled_back = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='must_rollback'"
        ).fetchone()
        self.assertEqual(rows, [(1,)])
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


if __name__ == "__main__":
    unittest.main()
