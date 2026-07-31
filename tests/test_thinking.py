import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ThinkingPersistenceTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.reload(importlib.import_module("torque.db"))
        self.schema = importlib.reload(importlib.import_module("torque.db_schema"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "torque.db"
        self.db = self.db_mod.TorqueDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

    def test_scratchpad_persists_without_mind_map_schema(self):
        note = self.db.create_scratchpad_note({"group": "Torque", "title": "Notes", "body": "Keep it simple."})
        self.assertEqual("TORQUE-S:1", note["id"])
        self.db.close()
        reopened = self.db_mod.TorqueDB(self.db_path)
        reopened.init()
        self.addCleanup(reopened.close)
        self.assertEqual("Notes", reopened.load_scratchpad_note(note["id"])["title"])
        tables = {row[0] for row in reopened._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("thinking_mind_maps", tables)

    def test_migration_drops_empty_legacy_mind_map_tables(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        for table in ("thinking_mind_maps", "thinking_mind_map_nodes", "thinking_mind_map_links", "thinking_mind_map_item_counters", "mind_map_id_counters"):
            conn.execute(f"CREATE TABLE {table} (id TEXT)")
        self.schema._migration_0027_remove_mind_maps(conn, lambda: None)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(tables & {"thinking_mind_maps", "thinking_mind_map_nodes", "thinking_mind_map_links", "thinking_mind_map_item_counters", "mind_map_id_counters"})


if __name__ == "__main__":
    unittest.main()
