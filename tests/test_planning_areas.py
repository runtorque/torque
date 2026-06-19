import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class PlanningAreaPersistenceTests(unittest.TestCase):
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

    def test_area_id_parses_without_task_or_initiative_collision(self):
        self.assertEqual(
            self.task_ids.parse_area_id("TORQUE-A:1"),
            {"group_prefix": "TORQUE", "number": 1},
        )
        self.assertTrue(self.task_ids.is_canonical_area_id("TORQUE-A:1"))
        self.assertIsNone(self.task_ids.parse_task_id("TORQUE-A:1"))
        self.assertFalse(self.task_ids.is_canonical_task_id("TORQUE-A:1"))
        self.assertIsNone(self.task_ids.parse_initiative_id("TORQUE-A:1"))
        self.assertFalse(self.task_ids.is_canonical_initiative_id("TORQUE-A:1"))

    def test_schema_persists_areas_links_notes_and_archive_across_reopen(self):
        area = self.db.create_area({
            "group": "Torque",
            "title": "Planning Areas",
            "area_type": "product-system",
            "lifecycle": "active_investment",
            "summary": "Compact map",
            "user_purpose": "Inspect product map",
            "system_purpose": "Anchor orchestration context",
            "in_scope": "Backend",
            "out_of_scope": "Wiki pages",
            "owner_kind": "user",
            "created_by_kind": "user",
        })
        self.assertEqual(area["id"], "TORQUE-A:1")
        self.assertEqual(area["lifecycle"], "active_investment")
        self.db.save_area_link(area["id"], "task", "TORQUE:1", created_by_kind="user")
        self.db.save_area_link(area["id"], "decision", "decision-1", created_by_kind="user")
        target = self.db.create_area({"group": "Torque", "title": "Runtime"})
        self.db.save_area_link(area["id"], "area", target["id"], relation="supports")
        note = self.db.create_area_note(area["id"], {
            "note_type": "invariant",
            "title": "Board remains truth",
            "body": "Area links do not move tasks.",
            "target_type": "task",
            "target_id": "TORQUE:1",
        })
        self.db.archive_area_note(note["id"], archived_by_kind="user")
        archived = self.db.archive_area(area["id"], archived_by_kind="user")
        self.assertTrue(archived["archived_at"])
        self.db.close()

        reopened = self.db_mod.TorqueDB(self.db_path)
        reopened.init()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.load_area("TORQUE-A:1")["title"], "Planning Areas")
        self.assertEqual(reopened.list_areas(group="Torque"), [target])
        self.assertEqual(len(reopened.list_areas(group="Torque", include_archived=True)), 2)
        self.assertEqual(len(reopened.list_area_links("TORQUE-A:1")), 3)
        self.assertEqual(len(reopened.list_area_notes("TORQUE-A:1", include_archived=True)), 1)
        self.assertEqual(reopened.list_area_notes("TORQUE-A:1"), [])


class PlanningAreaStateAndMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("torque.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_architect = importlib.import_module("torque.mcp_architect")
        self.mcp_architect = importlib.reload(self.mcp_architect)
        self.mcp_engineer = importlib.import_module("torque.mcp_engineer")
        self.mcp_engineer = importlib.reload(self.mcp_engineer)
        self.mcp = importlib.import_module("torque.mcp")
        self.mcp = importlib.reload(self.mcp)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["Torque"] = []
        self.state._db_save_groups()
        self.arch = self._add_agent("arch-1", "Blueprint", "architect")
        self.other_arch = self._add_agent("arch-2", "Other", "architect")
        self.engineer = self._add_agent("eng-1", "Courier", "engineer")
        self.other_engineer = self._add_agent("eng-2", "Other Engineer", "engineer")
        self.worker = self._add_agent("worker-1", "Worker", "worker")

    def _add_agent(self, agent_id, name, kind):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="Torque",
            cell_type="agent",
            kind=kind,
            status="running",
            persistent=True,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["Torque"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    async def _handle_command(self, payload):
        raise AssertionError(f"unexpected handle_command call: {payload}")

    async def _architect_tool(self, name, args, caller_id="arch-1"):
        text, is_error = await self.mcp_architect._dispatch_architect_tool(
            name, args, self._handle_command, self.state, caller_id=caller_id
        )
        return text, is_error

    async def _engineer_tool(self, name, args, caller_id="eng-1"):
        text, is_error = await self.mcp_engineer._dispatch_engineer_tool(
            name, args, self._handle_command, self.state, caller_id=caller_id
        )
        return text, is_error

    async def _worker_tool(self, name, args, caller_id="worker-1"):
        text, is_error = await self.mcp._dispatch_tool(
            name, args, caller_id, self._handle_command, self.state
        )
        return text, is_error

    def _add_task(self, task_id, title, **kwargs):
        task = self.state.board_add_task(
            title,
            "Torque",
            lane=kwargs.pop("lane", "Backlog"),
            id=task_id,
            **kwargs,
        )
        self.assertIsNotNone(task)
        return task

    async def test_architect_mcp_scopes_area_writes_links_notes_no_mutation(self):
        task = self._add_task("TORQUE:1", "Build API", assigned_engineer_id="eng-1", agent_id="worker-1")
        decision = self.state.save_decision({
            "id": "decision-owned",
            "architect_id": "arch-1",
            "title": "Approved",
            "rationale": "Go",
        })
        self.state.save_decision({
            "id": "decision-other",
            "architect_id": "arch-2",
            "title": "Other",
            "rationale": "No",
        })
        initiative = self.db.create_initiative({
            "group": "Torque",
            "title": "Initiative",
            "created_by_kind": "user",
            "owner_kind": "user",
        })

        text, is_error = await self._architect_tool(
            "architect_area_create",
            {"title": "Areas", "lifecycle": "active_investment"},
        )
        self.assertFalse(is_error, text)
        created = json.loads(text)["area"]
        self.assertEqual(created["id"], "TORQUE-A:1")
        self.assertEqual(created["owner_kind"], "architect")
        self.assertEqual(created["owner_id"], "arch-1")

        before_labels = list(task.labels)
        before_decision_links = list(decision["linked_task_ids"])
        text, is_error = await self._architect_tool(
            "architect_area_link_task", {"area": created["id"], "task": task.id}
        )
        self.assertFalse(is_error, text)
        text, is_error = await self._architect_tool(
            "architect_area_link_decision", {"area": created["id"], "decision": "decision-owned"}
        )
        self.assertFalse(is_error, text)
        text, is_error = await self._architect_tool(
            "architect_area_link_decision", {"area": created["id"], "decision": "decision-other"}
        )
        self.assertTrue(is_error)
        text, is_error = await self._architect_tool(
            "architect_area_link_initiative", {"area": created["id"], "initiative": initiative["id"]}
        )
        self.assertFalse(is_error, text)
        target = await self.state.create_area_async({
            "group": "Torque",
            "title": "Runtime",
            "created_by_kind": "architect",
            "created_by_id": "arch-1",
            "owner_kind": "architect",
            "owner_id": "arch-1",
        })
        text, is_error = await self._architect_tool(
            "architect_area_link_area",
            {"area": created["id"], "target_area": target["id"], "relation": "supports"},
        )
        self.assertFalse(is_error, text)
        text, is_error = await self._architect_tool(
            "architect_area_note_create",
            {
                "area": created["id"],
                "note_type": "invariant",
                "title": "No mutation",
                "body": "Links are durable rows only.",
                "target_type": "task",
                "target_id": task.id,
            },
        )
        self.assertFalse(is_error, text)
        note = json.loads(text)["note"]
        text, is_error = await self._architect_tool(
            "architect_area_note_update",
            {"area": created["id"], "note": str(note["id"]), "body": "Updated"},
        )
        self.assertFalse(is_error, text)

        self.assertEqual(task.labels, before_labels)
        self.assertEqual(self.state.load_decision("decision-owned")["linked_task_ids"], before_decision_links)

        text, is_error = await self._architect_tool(
            "architect_area_update",
            {"area": created["id"], "summary": "steal"},
            caller_id="arch-2",
        )
        self.assertTrue(is_error)

    async def test_engineer_and_worker_area_reads_filter_tasks_and_hide_decisions(self):
        visible = self._add_task("TORQUE:1", "Visible", assigned_engineer_id="eng-1", agent_id="worker-1")
        hidden = self._add_task("TORQUE:2", "Hidden", assigned_engineer_id="eng-2")
        area = self.db.create_area({
            "group": "Torque",
            "title": "Planning",
            "created_by_kind": "user",
            "owner_kind": "user",
        })
        self.db.save_area_link(area["id"], "task", visible.id)
        self.db.save_area_link(area["id"], "task", hidden.id)
        self.state.save_decision({
            "id": "decision-hidden",
            "architect_id": "arch-1",
            "title": "Hidden decision",
            "rationale": "No decision details for non-architects",
        })
        self.db.save_area_link(area["id"], "decision", "decision-hidden")
        self.db.create_area_note(area["id"], {
            "note_type": "caveat",
            "title": "Bounded",
            "body": "Compact notes only",
        })

        text, is_error = await self._engineer_tool("engineer_area_show", {"area": area["id"]})
        self.assertFalse(is_error, text)
        self.assertNotIn(hidden.id, text)
        self.assertNotIn("decision-hidden", text)
        self.assertNotIn("Hidden decision", text)
        payload = json.loads(text)
        self.assertEqual(payload["links"]["tasks"], [visible.id])
        self.assertEqual(payload["links"]["decisions"], [])
        self.assertEqual(payload["linked_tasks"]["count"], 1)
        self.assertEqual(payload["linked_tasks"]["hidden_count"], 1)
        self.assertEqual(payload["linked_decisions"]["count"], 0)
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 1)
        self.assertEqual(payload["notes"][0]["note_type"], "caveat")

        text, is_error = await self._worker_tool("torque_area_show", {"area": area["id"]})
        self.assertFalse(is_error, text)
        self.assertNotIn(hidden.id, text)
        self.assertNotIn("decision-hidden", text)
        payload = json.loads(text)
        self.assertEqual(payload["links"]["tasks"], [visible.id])
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 1)

        text, is_error = await self._engineer_tool("engineer_area_create", {"title": "Nope"})
        self.assertTrue(is_error)
        self.assertIn("Unknown engineer tool", text)
        text, is_error = await self._worker_tool("torque_area_create", {"title": "Nope"})
        self.assertTrue(is_error)
        self.assertIn("Unknown tool", text)

    async def test_trusted_user_server_area_commands_validate_same_group_and_note_target(self):
        area = await self.state.create_area_async({
            "group": "Torque",
            "title": "User editable",
            "owner_kind": "architect",
            "owner_id": "arch-1",
            "created_by_kind": "architect",
            "created_by_id": "arch-1",
        })
        self.state.save_decision({
            "id": "decision-same-group",
            "architect_id": "arch-2",
            "title": "Same group",
            "rationale": "Trusted user can link same-group decisions",
        })
        from torque.server import _handle_area_command
        result = await _handle_area_command({
            "cmd": "area_update",
            "id": area["id"],
            "summary": "user edit",
            "actor_kind": "user",
        }, self.state)
        self.assertEqual(result["type"], "area_updated")
        self.assertEqual(result["area"]["summary"], "user edit")
        result = await _handle_area_command({
            "cmd": "area_link_decision",
            "id": area["id"],
            "decision_id": "decision-same-group",
            "actor_kind": "user",
        }, self.state)
        self.assertEqual(result["type"], "area_linked")
        self.assertEqual(self.state.load_decision("decision-same-group")["linked_task_ids"], [])
        result = await _handle_area_command({
            "cmd": "area_note_create",
            "id": area["id"],
            "note_type": "follow_up",
            "title": "Link seed decision",
            "target_type": "decision",
            "target_id": "decision-same-group",
            "actor_kind": "user",
        }, self.state)
        self.assertEqual(result["type"], "area_note_created")
