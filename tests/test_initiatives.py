import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class InitiativePersistenceTests(unittest.TestCase):
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

    def test_initiative_id_parses_without_task_id_collision(self):
        self.assertEqual(
            self.task_ids.parse_initiative_id("TORQUE-I:1"),
            {"group_prefix": "TORQUE", "number": 1},
        )
        self.assertTrue(self.task_ids.is_canonical_initiative_id("TORQUE-I:1"))
        self.assertIsNone(self.task_ids.parse_task_id("TORQUE-I:1"))
        self.assertFalse(self.task_ids.is_canonical_task_id("TORQUE-I:1"))

    def test_schema_persists_initiatives_links_and_archive_across_reopen(self):
        initiative = self.db.create_initiative({
            "group": "Torque",
            "title": "Ship planning mode",
            "summary": "Product intent",
            "why": "Operators need planning context",
            "in_scope": "Backend foundation",
            "out_of_scope": "Planning UI",
            "done_definition": "APIs and MCP work",
            "planning_status": "now",
            "priority": "P1-ish",
            "owner_kind": "user",
            "created_by_kind": "user",
        })
        self.assertEqual(initiative["id"], "TORQUE-I:1")
        self.assertEqual(initiative["planning_status"], "now")
        self.assertEqual(initiative["priority"], "P1-ish")
        self.db.save_initiative_link(
            initiative["id"], "task", "TORQUE:1", created_by_kind="user"
        )
        self.db.save_initiative_link(
            initiative["id"], "task", "TORQUE:1", created_by_kind="user"
        )
        self.db.save_initiative_link(
            initiative["id"], "decision", "decision-1", created_by_kind="user"
        )
        self.assertEqual(len(self.db.list_initiative_links(initiative["id"])), 2)
        archived = self.db.archive_initiative(initiative["id"], archived_by_kind="user")
        self.assertTrue(archived["archived_at"])
        self.db.close()

        reopened = self.db_mod.TorqueDB(self.db_path)
        reopened.init()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.load_initiative("TORQUE-I:1")["title"], "Ship planning mode")
        self.assertEqual(reopened.list_initiatives(group="Torque"), [])
        self.assertEqual(len(reopened.list_initiatives(group="Torque", include_archived=True)), 1)
        self.assertEqual(len(reopened.list_initiative_links("TORQUE-I:1")), 2)


class InitiativeStateAndMCPTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_architect_mcp_scopes_writes_and_decision_links(self):
        task = self._add_task("TORQUE:1", "Build API", assigned_engineer_id="eng-1")
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

        text, is_error = await self._architect_tool(
            "architect_initiative_create",
            {"title": "Planning", "planning_status": "now", "priority": "urgent"},
        )
        self.assertFalse(is_error, text)
        created = json.loads(text)["initiative"]
        self.assertEqual(created["owner_kind"], "architect")
        self.assertEqual(created["owner_id"], "arch-1")

        text, is_error = await self._architect_tool(
            "architect_initiative_link_task",
            {"initiative": created["id"], "task": task.id},
        )
        self.assertFalse(is_error, text)
        before_labels = list(task.labels)
        before_decision_links = list(decision["linked_task_ids"])

        text, is_error = await self._architect_tool(
            "architect_initiative_link_decision",
            {"initiative": created["id"], "decision": "decision-owned"},
        )
        self.assertFalse(is_error, text)
        text, is_error = await self._architect_tool(
            "architect_initiative_link_decision",
            {"initiative": created["id"], "decision": "decision-other"},
        )
        self.assertTrue(is_error)
        self.assertEqual(task.labels, before_labels)
        self.assertEqual(
            self.state.load_decision("decision-owned")["linked_task_ids"],
            before_decision_links,
        )

        text, is_error = await self._architect_tool(
            "architect_initiative_update",
            {"initiative": created["id"], "summary": "steal"},
            caller_id="arch-2",
        )
        self.assertTrue(is_error)

    async def test_engineer_read_only_and_task_summary_is_board_derived(self):
        task = self._add_task("TORQUE:1", "Build API", assigned_engineer_id="eng-1")
        hidden = self._add_task("TORQUE:2", "Hidden", assigned_engineer_id="eng-2")
        initiative = self.db.create_initiative({
            "group": "Torque",
            "title": "Planning",
            "created_by_kind": "user",
            "owner_kind": "user",
        })
        self.db.save_initiative_link(initiative["id"], "task", task.id)
        self.db.save_initiative_link(initiative["id"], "task", hidden.id)
        self.state.save_decision({
            "id": "decision-hidden",
            "architect_id": "arch-1",
            "title": "Hidden decision",
            "rationale": "Engineers have no decision list surface",
        })
        self.db.save_initiative_link(
            initiative["id"], "decision", "decision-hidden"
        )

        text, is_error = await self._engineer_tool(
            "engineer_initiative_show", {"initiative": initiative["id"]}
        )
        self.assertFalse(is_error, text)
        self.assertNotIn(hidden.id, text)
        self.assertNotIn("decision-hidden", text)
        payload = json.loads(text)
        self.assertEqual(payload["links"]["tasks"], [task.id])
        self.assertEqual(payload["links"]["decisions"], [])
        self.assertEqual(payload["linked_tasks"]["count"], 1)
        self.assertEqual(payload["linked_tasks"]["hidden_count"], 1)
        self.assertEqual(payload["linked_decisions"]["count"], 0)
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 1)
        self.assertEqual(payload["linked_tasks"]["items"][0]["lane"], "Backlog")

        text, is_error = await self._engineer_tool(
            "engineer_initiative_list", {"include_links": True}
        )
        self.assertFalse(is_error, text)
        self.assertNotIn(hidden.id, text)
        self.assertNotIn("decision-hidden", text)

        self.state.board_update_task(task.id, lane="Done")
        text, is_error = await self._engineer_tool(
            "engineer_initiative_show", {"initiative": initiative["id"]}
        )
        self.assertFalse(is_error, text)
        self.assertNotIn(hidden.id, text)
        self.assertNotIn("decision-hidden", text)
        self.assertEqual(json.loads(text)["linked_tasks"]["items"][0]["lane"], "Done")
        self.assertEqual(self.db.load_initiative(initiative["id"])["planning_status"], "triage")

        text, is_error = await self._engineer_tool(
            "engineer_initiative_create", {"title": "Nope"}
        )
        self.assertTrue(is_error)
        self.assertIn("Unknown engineer tool", text)

    async def test_trusted_user_can_edit_architect_owned_and_same_group_decision_link(self):
        initiative = await self.state.create_initiative_async({
            "group": "Torque",
            "title": "Architect owned",
            "owner_kind": "architect",
            "owner_id": "arch-1",
            "created_by_kind": "architect",
            "created_by_id": "arch-1",
        })
        self.state.save_decision({
            "id": "decision-same-group",
            "architect_id": "arch-2",
            "title": "Same group",
            "rationale": "Visible to trusted user path",
        })
        from torque.server import _handle_initiative_command
        result = await _handle_initiative_command({
            "cmd": "initiative_update",
            "id": initiative["id"],
            "summary": "user edit",
            "actor_kind": "user",
        }, self.state)
        self.assertEqual(result["type"], "initiative_updated")
        self.assertEqual(result["initiative"]["summary"], "user edit")
        result = await _handle_initiative_command({
            "cmd": "initiative_link_decision",
            "id": initiative["id"],
            "decision_id": "decision-same-group",
            "actor_kind": "user",
        }, self.state)
        self.assertEqual(result["type"], "initiative_decision_linked")
        self.assertEqual(self.state.load_decision("decision-same-group")["linked_task_ids"], [])
