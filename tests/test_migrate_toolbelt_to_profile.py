import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from torque.db_schema import initialize_database


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "migrate_toolbelt_to_profile.py"
)


def load_script_module():
    loader = SourceFileLoader("migrate_toolbelt_to_profile", str(_SCRIPT_PATH))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class ToolbeltProfileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_script_module()

    def _create_toolbelt_db(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            initialize_database(conn, lambda: None)
            conn.execute(
                "INSERT INTO agents "
                "(id, name, group_name, terminal_backend, "
                "session_id, window_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "agent-1",
                    "Courier",
                    "Torque",
                    "iterm2",
                    "iterm-session-1",
                    "iterm-window-1",
                    "running",
                ),
            )
            conn.execute(
                "INSERT INTO group_settings (group_name, default_terminal_backend) "
                "VALUES (?, ?)",
                ("Torque", "iterm2"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_imports_normalizes_backs_up_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source_dir = self.mod.legacy_toolbelt_dir(home)
            source_db = source_dir / "torque.db"
            source_log = source_dir / "torque.log"
            self._create_toolbelt_db(source_db)
            source_log.write_text("legacy log\n", encoding="utf-8")

            target_dir = self.mod.profile_dir("desktop", home)
            target_dir.mkdir(parents=True)
            target_db = target_dir / "torque.db"
            target_db.write_bytes(b"old target db")

            out = io.StringIO()
            summary = self.mod.migrate_toolbelt_to_profile(
                profile="desktop",
                home=home,
                now=lambda: datetime(2026, 5, 24, 12, 34, 56),
                out=out,
            )

            self.assertEqual(summary.imported_agents, 1)
            self.assertEqual(summary.imported_group_settings, 1)
            self.assertEqual(summary.agents_marked_stopped, 1)
            self.assertEqual(summary.agent_sessions_cleared, 1)
            self.assertEqual(summary.agent_windows_cleared, 1)
            self.assertEqual(summary.agent_backends_rewritten, 1)
            self.assertEqual(summary.group_backends_rewritten, 1)

            conn = sqlite3.connect(str(target_db))
            try:
                row = conn.execute(
                    "SELECT status, session_id, window_id, terminal_backend "
                    "FROM agents WHERE id='agent-1'"
                ).fetchone()
                group_row = conn.execute(
                    "SELECT default_terminal_backend FROM group_settings "
                    "WHERE group_name='Torque'"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("stopped", None, "", "pty"))
            self.assertEqual(group_row, ("pty",))

            backup_dir = (
                home
                / ".torque"
                / "backups"
                / "toolbelt-to-profile-20260524-123456"
            )
            self.assertEqual(summary.backup_dir, backup_dir)
            self.assertTrue((backup_dir / "source-torque.db").exists())
            self.assertEqual(
                (backup_dir / "source-torque.log").read_text(encoding="utf-8"),
                "legacy log\n",
            )
            self.assertEqual(
                (backup_dir / "target-torque.db").read_bytes(),
                b"old target db",
            )

            text = out.getvalue()
            self.assertIn("Toolbelt → profile migration complete.", text)
            self.assertIn("agents:         1", text)
            self.assertIn("agents marked stopped:             1", text)
            self.assertIn("agent terminal_backend → pty:       1", text)
            self.assertIn("group default_terminal_backend → pty: 1", text)
            self.assertIn("Backups:", text)
            self.assertIn("Next launch: make run", text)


if __name__ == "__main__":
    unittest.main()
