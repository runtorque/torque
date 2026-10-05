import asyncio
import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

from torque.agent_order import ordered_terminal_children
from torque.db import TorqueDB


def records():
    return {
        "owner": {"id": "owner", "name": "Owner", "group": "g", "cell_type": "agent"},
        "other": {"id": "other", "name": "Other", "group": "g", "cell_type": "agent"},
        "first": {"id": "first", "name": "First", "group": "g", "cell_type": "terminal", "parent_id": "owner"},
        "second": {"id": "second", "name": "Second", "group": "g", "cell_type": "terminal", "parent_id": "owner"},
        "remote": {"id": "remote", "name": "Remote", "group": "g", "cell_type": "terminal", "parent_id": "other"},
    }


class TerminalOrderTests(unittest.TestCase):
    def test_saved_order_cannot_reparent_duplicate_or_hide_children(self):
        saved = {"owner": ["second", "second", "remote", "missing", {}, 2],
                 "other": "not a list", "missing-parent": ["first"]}
        self.assertEqual(ordered_terminal_children(records(), saved),
                         {"owner": ["second", "first"], "other": ["remote"]})
        for malformed in (None, [], "invalid"):
            with self.subTest(saved=malformed):
                self.assertEqual(ordered_terminal_children(records(), malformed),
                                 {"owner": ["first", "second"], "other": ["remote"]})

    def test_bulk_snapshot_and_offline_cli_preserve_child_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "torque.db"
            db = TorqueDB(path)
            db.init()
            try:
                expected = {"owner": ["second", "first"], "other": ["remote"]}
                db.save_all({"agents": records(), "groups": {"g": ["owner", "other"]}, "children": expected})
                self.assertEqual(db.load_all()["children"], expected)
                cli_path = Path(__file__).resolve().parents[1] / "bin" / "torque"
                loader = SourceFileLoader("torque_cli_child_order", str(cli_path))
                spec = importlib.util.spec_from_loader(loader.name, loader)
                cli = importlib.util.module_from_spec(spec)
                loader.exec_module(cli)
                cli.TORQUE_DB = str(path)
                self.assertEqual(cli.db_read_state()["children"], expected)
                db.save_ui_state("children", "not JSON")
                fallback = {"owner": ["first", "second"], "other": ["remote"]}
                self.assertEqual(cli.db_read_state()["children"], fallback)
                self.assertEqual(db.load_all()["children"], fallback)
            finally:
                db.close()

    def test_deferred_order_captures_the_submitted_list(self):
        with tempfile.TemporaryDirectory() as directory:
            db = TorqueDB(Path(directory) / "torque.db")
            db.init()
            try:
                db.save_all({"agents": records(), "groups": {"g": ["owner", "other"]}})

                async def write():
                    order = {"owner": ["second", "first"], "other": ["remote"]}
                    db.save_groups_and_members_deferred({"g": ["owner", "other"]}, {}, order)
                    order["owner"].reverse()
                    await db.flush_async_writes()

                asyncio.run(write())
                self.assertEqual(db.load_all()["children"],
                                 {"owner": ["second", "first"], "other": ["remote"]})
            finally:
                db.close()
