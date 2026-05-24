import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest import mock

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
                live_runtime_probe=lambda _port: None,
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

    def test_live_runtime_guard_refuses_matching_target_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target_dir = self.mod.profile_dir("desktop", home)
            seen_ports = []

            def probe(port):
                seen_ports.append(port)
                return {
                    "profile": "desktop",
                    "data_dir": str(target_dir),
                    "pid": 12345,
                }

            with self.assertRaises(self.mod.MigrationError) as ctx:
                self.mod._refuse_if_target_daemon_live(
                    target_dir,
                    profile_slug="desktop",
                    live_runtime_probe=probe,
                )

            self.assertIn("live Torque daemon", str(ctx.exception))
            self.assertIn("port 18933", str(ctx.exception))
            self.assertEqual(seen_ports, [18933])

    def test_live_runtime_guard_refuses_matching_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target_dir = self.mod.profile_dir("desktop", home)

            with self.assertRaises(self.mod.MigrationError):
                self.mod._refuse_if_target_daemon_live(
                    target_dir,
                    profile_slug="desktop",
                    live_runtime_probe=lambda _port: {
                        "profile": "Desktop",
                        "data_dir": str(home / "custom-data-dir"),
                    },
                )

    def test_force_skips_live_runtime_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source_db = self.mod.legacy_toolbelt_dir(home) / "torque.db"
            self._create_toolbelt_db(source_db)

            def fail_probe(_port):
                raise AssertionError("force should skip live daemon probes")

            self.mod.migrate_toolbelt_to_profile(
                profile="desktop",
                home=home,
                force=True,
                out=io.StringIO(),
                live_runtime_probe=fail_probe,
            )

    def test_stale_profile_pid_file_does_not_block_probe_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target_dir = self.mod.profile_dir("desktop", home)
            target_dir.mkdir(parents=True)
            (target_dir / "torque.pid").write_text("not-a-pid", encoding="utf-8")

            self.mod._refuse_if_target_daemon_live(
                target_dir,
                profile_slug="desktop",
                live_runtime_probe=lambda _port: None,
            )

    def test_candidate_ports_include_env_and_default_primary_ports(self):
        with mock.patch.dict(
            self.mod.os.environ,
            {"TORQUE_PORT": "19000", "TORQUE_DESKTOP_PORT": "19001"},
        ):
            ports = self.mod._candidate_live_daemon_ports("desktop")

        self.assertEqual(ports[:2], [19000, 19001])
        self.assertIn(18933, ports)
        self.assertIn(18932, ports)

    def test_fetch_live_runtime_config_posts_get_config(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return (
                    b'{"ok": true, "data": {"runtime": '
                    b'{"profile": "desktop", "data_dir": "/tmp/desktop"}}}'
                )

        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, request.data, timeout))
            return FakeResponse()

        with mock.patch.object(self.mod.urllib.request, "urlopen", fake_urlopen):
            runtime = self.mod._fetch_live_runtime_config(19000, timeout=0.1)

        self.assertEqual(
            runtime,
            {"profile": "desktop", "data_dir": "/tmp/desktop"},
        )
        self.assertEqual(calls[0][0], "http://127.0.0.1:19000/api/cmd")
        self.assertEqual(calls[0][1], b'{"cmd": "get_config"}')
        self.assertEqual(calls[0][2], 0.1)


if __name__ == "__main__":
    unittest.main()
