import importlib
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ThinkingPersistenceTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("torque.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.task_ids = importlib.import_module("torque.task_ids")
        self.task_ids = importlib.reload(self.task_ids)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "torque.db"
        self.db = self.db_mod.TorqueDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

    def test_thinking_ids_do_not_collide_with_task_or_planning_ids(self):
        self.assertEqual(
            self.task_ids.parse_scratchpad_note_id("TORQUE-S:1"),
            {"group_prefix": "TORQUE", "number": 1},
        )
        self.assertEqual(
            self.task_ids.parse_mind_map_id("TORQUE-M:1"),
            {"group_prefix": "TORQUE", "number": 1},
        )
        self.assertIsNone(self.task_ids.parse_task_id("TORQUE-S:1"))
        self.assertIsNone(self.task_ids.parse_task_id("TORQUE-M:1"))
        self.assertIsNone(self.task_ids.parse_initiative_id("TORQUE-S:1"))
        self.assertIsNone(self.task_ids.parse_area_id("TORQUE-M:1"))

    def test_schema_persists_scratchpad_and_mind_maps_across_reopen(self):
        note = self.db.create_scratchpad_note({
            "group": "Torque",
            "title": "Rough launch notes",
            "body": "Keep Thinking separate from Planning.",
            "context": {"source": "TORQUE:926"},
            "links": [{"type": "task", "id": "TORQUE:927"}],
            "created_by_kind": "user",
        })
        self.assertEqual(note["id"], "TORQUE-S:1")
        self.assertEqual(note["context"]["source"], "TORQUE:926")
        self.assertEqual(note["links"][0]["id"], "TORQUE:927")

        mind_map = self.db.create_mind_map({
            "group": "Torque",
            "title": "Idea graph",
            "description": "Concept map",
            "metadata": {"anchor": "decision-1d6b14a02d32"},
            "created_by_kind": "user",
        })
        self.assertEqual(mind_map["id"], "TORQUE-M:1")
        first = self.db.create_mind_map_node(mind_map["id"], {
            "label": "Scratchpad",
            "notes": "Free-form notes",
            "node_type": "concept",
            "tags": ["notes"],
            "color": "#ffcc00",
            "x": 10,
            "y": 20,
        })
        second = self.db.create_mind_map_node(mind_map["id"], {
            "label": "Mind Map",
            "position": {"x": 40, "y": 50, "lane": "main"},
        })
        link = self.db.create_mind_map_link(mind_map["id"], {
            "source_node_id": first["id"],
            "target_node_id": second["id"],
            "label": "feeds",
            "link_type": "relationship",
        })
        self.assertEqual(link["id"], "TORQUE-M:1:L1")
        self.assertEqual(first["id"], "TORQUE-M:1:N1")
        self.assertEqual(second["position"]["lane"], "main")

        self.db.update_mind_map_node(first["id"], {"x": 15, "y": 25})
        self.db.reorder_mind_map_nodes(mind_map["id"], [second["id"], first["id"]])
        self.assertEqual(
            [node["id"] for node in self.db.list_mind_map_nodes(mind_map["id"])],
            [second["id"], first["id"]],
        )
        archived_note = self.db.archive_scratchpad_note(
            note["id"], archived_by_kind="user"
        )
        deleted_link = self.db.delete_mind_map_link(link["id"], deleted_by_kind="user")
        deleted_map = self.db.delete_mind_map(mind_map["id"], deleted_by_kind="user")
        self.assertTrue(archived_note["archived"])
        self.assertTrue(deleted_link["deleted"])
        self.assertTrue(deleted_map["deleted"])
        self.db.close()

        reopened = self.db_mod.TorqueDB(self.db_path)
        reopened.init()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.list_scratchpad_notes(group="Torque"), [])
        self.assertEqual(
            len(reopened.list_scratchpad_notes(group="Torque", include_archived=True)),
            1,
        )
        self.assertEqual(reopened.list_mind_maps(group="Torque"), [])
        self.assertEqual(
            len(reopened.list_mind_maps(group="Torque", include_deleted=True)),
            1,
        )
        payload = reopened.mind_map_payload(
            "TORQUE-M:1",
            include_deleted=True,
        )
        self.assertEqual(payload["node_count"], 0)
        self.assertEqual(payload["link_count"], 0)
        self.assertEqual(len(payload["nodes"]), 2)
        self.assertEqual(len(payload["links"]), 1)


class ThinkingStateAndServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("torque.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "Done", "Archived"]
        self.state.groups["Torque"] = []
        self.state.groups["Other"] = []
        self.state._db_save_groups()

    @staticmethod
    def _closure_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_server_handle_command(self):
        server_mod = importlib.import_module("torque.server")
        server_mod = importlib.reload(server_mod)
        main_code = server_mod.main.__code__
        handle_code = next(
            const for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )
        closure_values = {name: None for name in handle_code.co_freevars}
        closure_values.update({
            "db": self.db,
            "state": self.state,
            "panel_log": None,
            "bridge": types.SimpleNamespace(),
            "handle_command": None,
        })
        closure = tuple(
            self._closure_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    async def test_state_snapshot_deltas_and_server_crud_shape(self):
        emitted = []
        self.state._emit = lambda op, **payload: emitted.append({"op": op, **payload})
        handle_command = self._extract_server_handle_command()

        created = await handle_command({
            "cmd": "scratchpad_note_create",
            "group": "Torque",
            "title": "Backend contract",
            "body": "Notes are group-scoped.",
            "context": {"kind": "task", "id": "TORQUE:927"},
            "actor_kind": "user",
        })
        self.assertEqual(created["type"], "scratchpad_note_created")
        note = created["note"]
        self.assertEqual(note["id"], "TORQUE-S:1")
        self.assertEqual(note["group_name"], "Torque")
        self.assertEqual(note["context"]["id"], "TORQUE:927")
        self.assertIn("created_at", note)
        self.assertIn("updated_at", note)
        self.assertEqual(emitted[-1]["op"], "thinking_scratchpad_note_upsert")

        listed = await handle_command({
            "cmd": "scratchpad_note_list",
            "group": "Torque",
        })
        self.assertEqual(listed["type"], "scratchpad_note_list")
        self.assertEqual([item["id"] for item in listed["notes"]], [note["id"]])
        shown = await handle_command({
            "cmd": "scratchpad_note_show",
            "group": "Torque",
            "note": note["slug"],
        })
        self.assertEqual(shown["type"], "scratchpad_note")
        self.assertEqual(shown["id"], note["id"])

        scoped = await handle_command({
            "cmd": "scratchpad_note_show",
            "group": "Other",
            "note": note["id"],
        })
        self.assertEqual(scoped["type"], "error")
        self.assertEqual(scoped["code"], "out_of_scope")

        archived = await handle_command({
            "cmd": "scratchpad_note_archive",
            "group": "Torque",
            "note": note["id"],
        })
        self.assertEqual(archived["type"], "scratchpad_note_archived")
        hidden = await handle_command({
            "cmd": "scratchpad_note_show",
            "group": "Torque",
            "note": note["id"],
        })
        self.assertEqual(hidden["code"], "archived")
        active_note = await handle_command({
            "cmd": "scratchpad_note_create",
            "group": "Torque",
            "title": "Active backend note",
            "body": "Visible in the default Thinking snapshot.",
            "actor_kind": "user",
        })

        mind_map = await handle_command({
            "cmd": "mind_map_create",
            "group": "Torque",
            "title": "Concepts",
            "description": "UI-ready graph",
            "metadata": {"future_consumer": "idea-brief"},
        })
        self.assertEqual(mind_map["type"], "mind_map_created")
        map_id = mind_map["mind_map"]["id"]
        self.assertEqual(emitted[-1]["op"], "thinking_mind_map_upsert")
        first = await handle_command({
            "cmd": "mind_map_node_create",
            "group": "Torque",
            "map_id": map_id,
            "label": "Scratchpad",
            "x": 1,
            "y": 2,
        })
        second = await handle_command({
            "cmd": "mind_map_node_create",
            "group": "Torque",
            "map_id": map_id,
            "label": "Map",
            "position": {"x": 3, "y": 4},
        })
        link = await handle_command({
            "cmd": "mind_map_link_create",
            "group": "Torque",
            "map_id": map_id,
            "source_node_id": first["node"]["id"],
            "target_node_id": second["node"]["id"],
            "label": "connects",
            "link_type": "edge",
        })
        self.assertEqual(link["type"], "mind_map_link_created")

        positioned = await handle_command({
            "cmd": "mind_map_node_position",
            "group": "Torque",
            "node_id": first["node"]["id"],
            "x": 9,
            "y": 10,
        })
        self.assertEqual(positioned["type"], "mind_map_node_positioned")
        self.assertEqual(positioned["node"]["position"]["x"], 9.0)
        reordered = await handle_command({
            "cmd": "mind_map_node_reorder",
            "group": "Torque",
            "map_id": map_id,
            "nodes": [second["node"]["id"], first["node"]["id"]],
        })
        self.assertEqual(reordered["type"], "mind_map_node_reordered")
        self.assertEqual(
            [node["id"] for node in reordered["nodes"]],
            [second["node"]["id"], first["node"]["id"]],
        )
        detail = await handle_command({
            "cmd": "mind_map_show",
            "group": "Torque",
            "map_id": map_id,
        })
        self.assertEqual(detail["type"], "mind_map")
        self.assertEqual(detail["node_count"], 2)
        self.assertEqual(detail["link_count"], 1)
        self.assertEqual(len(detail["nodes"]), 2)
        self.assertEqual(len(detail["links"]), 1)
        self.assertIn("created_by_kind", detail["nodes"][0])
        self.assertIn("updated_by_kind", detail["links"][0])

        snapshot = self.state.to_dict()["thinking"]
        self.assertIn(active_note["note"]["id"], snapshot["scratchpad_notes"])
        self.assertIn(map_id, snapshot["mind_maps"])
        compact = self.state.to_dict_compact()["thinking"]
        self.assertIn(map_id, compact["mind_maps"])

    async def test_planning_commands_and_state_are_not_changed_by_thinking(self):
        initiative = self.db.create_initiative({
            "group": "Torque",
            "title": "Existing initiative",
            "created_by_kind": "user",
        })
        area = self.db.create_area({
            "group": "Torque",
            "title": "Existing area",
            "created_by_kind": "user",
        })
        handle_command = self._extract_server_handle_command()
        await handle_command({
            "cmd": "scratchpad_note_create",
            "group": "Torque",
            "title": "Thinking only",
        })
        await handle_command({
            "cmd": "mind_map_create",
            "group": "Torque",
            "title": "Thinking graph",
        })

        initiatives = await handle_command({
            "cmd": "initiative_list",
            "group": "Torque",
        })
        areas = await handle_command({
            "cmd": "area_list",
            "group": "Torque",
        })
        self.assertEqual([item["id"] for item in initiatives["initiatives"]], [initiative["id"]])
        self.assertEqual([item["id"] for item in areas["areas"]], [area["id"]])
        self.assertNotIn("thinking", initiatives)
        self.assertNotIn("thinking", areas)
