import importlib
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class IdeaBriefPersistenceTests(unittest.TestCase):
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

    def test_idea_brief_ids_do_not_collide_with_tasks_or_thinking(self):
        parsed = self.task_ids.parse_idea_brief_id("TORQUE-IB:1")
        self.assertEqual(parsed, {"group_prefix": "TORQUE", "number": 1})
        self.assertEqual(self.task_ids.format_idea_brief_id("Torque", 2), "TORQUE-IB:2")
        self.assertIsNone(self.task_ids.parse_task_id("TORQUE-IB:1"))
        self.assertIsNone(self.task_ids.parse_scratchpad_note_id("TORQUE-IB:1"))
        self.assertIsNone(self.task_ids.parse_mind_map_id("TORQUE-IB:1"))

    def test_schema_persists_idea_brief_fields_and_review_proposal(self):
        note = self.db.create_scratchpad_note({
            "group": "Torque",
            "title": "Signal",
            "body": "Users need synthesis.",
        })
        brief = self.db.create_idea_brief({
            "group": "Torque",
            "title": "Synthesis brief",
            "problem_opportunity": "Thinking artifacts need a reviewable synthesis.",
            "why_it_matters": "Blueprint needs durable context before planning.",
            "proposed_shape": "A structured brief linked to notes.",
            "smallest_useful_version": "Draft + park + propose.",
            "risks_tradeoffs": "May look like a task if copy is careless.",
            "open_questions": "Who reviews first?",
            "thinking_links": [{"type": "scratchpad_note", "id": note["id"], "summary": "source"}],
            "source_context": {"task": "TORQUE:945"},
            "created_by_kind": "architect",
            "created_by_id": "arch-1",
        })
        self.assertEqual(brief["id"], "TORQUE-IB:1")
        self.assertEqual(brief["status"], "draft")
        self.assertEqual(brief["thinking_links"][0]["id"], note["id"])
        refined = self.db.refine_idea_brief(brief["id"], {
            "smallest_useful_version": "One backend contract and safe wrappers.",
            "refinement_note": "Narrowed to Wave A.",
            "updated_by_kind": "architect",
            "updated_by_id": "arch-1",
        })
        self.assertEqual(refined["smallest_useful_version"], "One backend contract and safe wrappers.")
        self.assertEqual(refined["refinement_log"][0]["note"], "Narrowed to Wave A.")
        proposed = self.db.propose_idea_brief(
            brief["id"],
            proposed_by_kind="architect",
            proposed_by_id="arch-1",
            note="Ready for Blueprint review.",
        )
        self.assertEqual(proposed["status"], "proposed")
        self.assertTrue(proposed["proposal"]["proposal_only"])
        self.assertFalse(proposed["proposal"]["auto_dispatch"])
        self.assertEqual(proposed["proposal"]["created_task_id"], "")
        parked = self.db.park_idea_brief(
            brief["id"], parked_by_kind="architect", parked_by_id="arch-1"
        )
        self.assertEqual(parked["status"], "parked")
        archived = self.db.archive_idea_brief(
            brief["id"], archived_by_kind="architect", archived_by_id="arch-1"
        )
        self.assertEqual(archived["status"], "archived")
        self.assertTrue(archived["archived"])
        self.assertEqual(self.db.list_idea_briefs(group="Torque"), [])
        self.db.close()

        reopened = self.db_mod.TorqueDB(self.db_path)
        reopened.init()
        self.addCleanup(reopened.close)
        rows = reopened.list_idea_briefs(group="Torque", include_archived=True)
        self.assertEqual([row["id"] for row in rows], [brief["id"]])
        self.assertEqual(rows[0]["proposal"]["review_scope"], "product_safe_review")


class IdeaBriefStateAndServerTests(unittest.IsolatedAsyncioTestCase):
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
            if isinstance(const, type(main_code)) and const.co_name == "handle_command"
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

    async def test_server_contract_deltas_scoping_and_no_dispatch_on_propose(self):
        emitted = []
        self.state._emit = lambda op, **payload: emitted.append({"op": op, **payload})
        handle_command = self._extract_server_handle_command()
        note = await handle_command({
            "cmd": "scratchpad_note_create",
            "group": "Torque",
            "title": "Opportunity notes",
            "body": "A brief should cite this.",
        })
        mind_map = await handle_command({"cmd": "mind_map_create", "group": "Torque", "title": "Map"})
        node = await handle_command({
            "cmd": "mind_map_node_create",
            "group": "Torque",
            "mind_map_id": mind_map["mind_map"]["id"],
            "label": "Smallest useful version",
        })
        before_tasks = set(self.state.board_tasks)

        implicit_create_propose = await handle_command({
            "cmd": "idea_brief_create",
            "group": "Torque",
            "problem_opportunity": "Create should not bypass review proposal.",
            "status": "proposed",
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(implicit_create_propose["type"], "error")
        self.assertIn("created as drafts", implicit_create_propose["message"])

        created = await handle_command({
            "cmd": "idea_brief_create",
            "group": "Torque",
            "title": "Idea Brief workflow",
            "problem_opportunity": "Catalyst needs a durable synthesis artifact.",
            "why_it_matters": "Reviewers need traceable context.",
            "proposed_shape": "Structured plain fields with Thinking links.",
            "smallest_useful_version": "Backend contract first.",
            "risks_tradeoffs": "Avoid implementation pressure.",
            "open_questions": "What should Panelsmith show first?",
            "thinking_links": [
                {"type": "scratchpad_note", "id": note["note"]["id"], "summary": "source note"},
                {"type": "mind_map_node", "id": node["node"]["id"], "reason": "small slice"},
            ],
            "source_context": {"decision": "decision-12acf9ee0894"},
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(created["type"], "idea_brief_created")
        brief = created["idea_brief"]
        self.assertEqual(brief["id"], "TORQUE-IB:1")
        self.assertEqual(brief["thinking_links"][1]["type"], "mind_map_node")
        self.assertEqual(emitted[-1]["op"], "idea_brief_upsert")
        for title in ("Second brief", "Third brief"):
            self.db.create_idea_brief({
                "group": "Torque",
                "title": title,
                "problem_opportunity": "List rows must be body-free.",
                "created_by_kind": "architect",
                "created_by_id": "arch-1",
            })
        listed = await handle_command({
            "cmd": "idea_brief_list",
            "group": "Torque",
            "limit": 2,
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(3, listed["idea_briefs_total"])
        self.assertEqual(2, listed["idea_briefs_returned"])
        self.assertTrue(listed["idea_briefs_capped"])
        self.assertEqual(
            {
                "id", "slug", "title", "status", "group", "created_by_id",
                "created_by_kind", "updated_at", "archived", "caller_owned",
            },
            set(listed["idea_briefs"][0]),
        )

        implicit_propose = await handle_command({
            "cmd": "idea_brief_update",
            "group": "Torque",
            "idea_brief": brief["id"],
            "status": "proposed",
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(implicit_propose["type"], "error")
        self.assertIn("idea_brief_propose", implicit_propose["message"])

        shown = await handle_command({
            "cmd": "idea_brief_show",
            "group": "Torque",
            "idea_brief": brief["slug"],
        })
        self.assertEqual(shown["type"], "idea_brief")
        self.assertEqual(shown["id"], brief["id"])
        self.assertEqual({"decision": "decision-12acf9ee0894"}, shown["source_context"])
        for raw_field in (
                "thinking_links_json", "source_context_json", "proposal_json",
                "refinement_log_json"):
            self.assertNotIn(raw_field, shown)
        scoped = await handle_command({
            "cmd": "idea_brief_show",
            "group": "Other",
            "idea_brief": brief["id"],
        })
        self.assertEqual(scoped["code"], "out_of_scope")

        refined = await handle_command({
            "cmd": "idea_brief_refine",
            "group": "Torque",
            "idea_brief": brief["id"],
            "refinement_note": "Tighten scope.",
            "open_questions": "Which UI smoke checks?",
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(refined["type"], "idea_brief_refined")
        self.assertEqual(refined["idea_brief"]["refinement_log"][0]["note"], "Tighten scope.")
        parked = await handle_command({
            "cmd": "idea_brief_park",
            "group": "Torque",
            "idea_brief": brief["id"],
            "reason": "Waiting for review window.",
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(parked["idea_brief"]["status"], "parked")
        proposed = await handle_command({
            "cmd": "idea_brief_propose",
            "group": "Torque",
            "idea_brief": brief["id"],
            "proposal_note": "Ready for Blueprint.",
            "actor_kind": "architect",
            "actor_id": "arch-1",
        })
        self.assertEqual(proposed["type"], "idea_brief_proposed")
        self.assertEqual(proposed["idea_brief"]["status"], "proposed")
        self.assertEqual(before_tasks, set(self.state.board_tasks))
        self.assertEqual("product_safe_review", proposed["review_scope"])
        self.assertFalse(proposed["proposal"]["auto_assign"])
        snapshot = self.state.to_dict()["idea_briefs"]
        self.assertIn(brief["id"], snapshot)

    async def test_server_rejects_cross_group_thinking_links(self):
        handle_command = self._extract_server_handle_command()
        other_note = await handle_command({
            "cmd": "scratchpad_note_create",
            "group": "Other",
            "title": "Other note",
        })
        result = await handle_command({
            "cmd": "idea_brief_create",
            "group": "Torque",
            "problem_opportunity": "Cannot cite another group.",
            "thinking_links": [{"type": "scratchpad_note", "id": other_note["note"]["id"]}],
        })
        self.assertEqual(result["type"], "error")
        self.assertEqual(result["code"], "validation_error")
        self.assertIn("Scratchpad note not found", result["message"])
