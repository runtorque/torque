import io
import logging
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from loom.db import LoomDB

install_aiohttp_stub()
from loom.state import AgentCell, BoardTask


class Stage6CleanupMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _path(self, filename: str) -> Path:
        return Path(self.tmp.name) / filename

    def _seed_stage5_db(self, filename: str) -> Path:
        path = self._path(filename)
        db = LoomDB(path)
        db.init()
        db.save_agent(
            AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                slug="worker",
                kind="worker",
                role="researcher",
                owner_engineer_id="weaver-1",
            )
        )
        db.save_board_task(
            BoardTask(
                id="TASK:1",
                task="Cleanup me",
                group="g",
                assigned_engineer_id="weaver-1",
            )
        )
        db.close()

        conn = sqlite3.connect(str(path))
        conn.execute(
            "ALTER TABLE agents ADD COLUMN template TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE agents ADD COLUMN created_by_weaver_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN weaver_owner_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "UPDATE agents SET template=role, created_by_weaver_id=owner_engineer_id"
        )
        conn.execute(
            "UPDATE board_tasks SET weaver_owner_id=assigned_engineer_id"
        )
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='schema_kinds_migration_version'"
        )
        conn.commit()
        conn.close()
        return path

    def _seed_pre_stage1_db(self, filename: str) -> Path:
        path = self._path(filename)
        db = LoomDB(path)
        db.init()
        db.save_agent(
            AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                slug="worker",
                kind="worker",
                role="researcher",
                owner_engineer_id="weaver-1",
            )
        )
        db.save_board_task(
            BoardTask(
                id="TASK:1",
                task="Guard me",
                group="g",
                assigned_engineer_id="weaver-1",
            )
        )
        db.close()

        conn = sqlite3.connect(str(path))
        conn.execute(
            "ALTER TABLE agents ADD COLUMN template TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE agents ADD COLUMN created_by_weaver_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN weaver_owner_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "UPDATE agents SET template='researcher', created_by_weaver_id='weaver-1', "
            "kind='', role='', owner_engineer_id='', hired_by_architect_id='', persistent=0 "
            "WHERE id='worker-1'"
        )
        conn.execute(
            "UPDATE board_tasks SET weaver_owner_id='weaver-1', assigned_engineer_id='', "
            "created_by_architect_id='', suggested_action='' WHERE id='TASK:1'"
        )
        conn.execute(
            "DELETE FROM meta WHERE key='schema_kinds_migration_version'"
        )
        conn.commit()
        conn.close()
        return path

    def test_upgrade_from_stage5_drops_legacy_columns_and_preserves_data(self):
        path = self._seed_stage5_db("stage5-upgrade.db")

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="WARNING") as cm:
            migrated.init()

        self.assertIn(
            "migration cleanup: dropping legacy columns with 2 mirrored rows",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )
        agent_columns = {
            row[1]
            for row in migrated._conn.execute("PRAGMA table_info(agents)").fetchall()
        }
        task_columns = {
            row[1]
            for row in migrated._conn.execute("PRAGMA table_info(board_tasks)").fetchall()
        }
        self.assertNotIn("template", agent_columns)
        self.assertNotIn("created_by_weaver_id", agent_columns)
        self.assertNotIn("weaver_owner_id", task_columns)
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id FROM agents WHERE id='worker-1'"
            ).fetchone(),
            ("worker", "researcher", "weaver-1"),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='TASK:1'"
            ).fetchone(),
            ("weaver-1",),
        )

    def test_upgrade_guard_refuses_unmigrated_pre_stage1_rows(self):
        path = self._seed_pre_stage1_db("pre-stage1.db")

        guarded = LoomDB(path)
        self.addCleanup(guarded.close)
        stderr = io.StringIO()

        with self.assertLogs("loom", level="ERROR") as cm:
            with self.assertRaises(SystemExit) as exc:
                with redirect_stderr(stderr):
                    guarded.init()

        self.assertNotEqual(exc.exception.code, 0)
        expected = (
            "ERROR: this version requires a prior kinds-refactor migration.\n"
            "Install version X.Y first, boot once so the migration runs, then upgrade.\n"
            "Current DB has unmigrated rows with legacy columns populated."
        )
        self.assertEqual(stderr.getvalue().strip(), expected)
        self.assertIn(expected, "\n".join(cm.output))

    def test_clean_v3_boot_skips_cleanup_and_emits_no_warnings(self):
        path = self._path("clean-v3.db")
        seeded = LoomDB(path)
        seeded.init()
        seeded.close()

        logger = logging.getLogger("loom")
        captured = []
        handler = logging.Handler()
        handler.setLevel(logging.WARNING)
        handler.emit = lambda record: captured.append(record.getMessage())
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        rerun = LoomDB(path)
        self.addCleanup(rerun.close)
        stderr = io.StringIO()
        try:
            with redirect_stderr(stderr):
                rerun.init()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(captured, [])
        self.assertEqual(
            rerun._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )
