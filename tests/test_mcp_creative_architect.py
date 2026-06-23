import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPCreativeArchitectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_mod = importlib.import_module("torque.db")
        self.state_mod = importlib.import_module("torque.state")
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["g"] = []
        self.state.groups["other"] = []
        self.calls = []

        self.architect = self._add_agent("architect-1", "Creative", kind="architect")
        self.peer = self._add_agent("architect-2", "Peer", kind="architect")
        self.engineer = self._add_agent(
            "engineer-1",
            "Engineer",
            kind="engineer",
            hired_by_architect_id=self.architect.id,
        )
        self.worker = self._add_agent("worker-1", "Worker", kind="worker")
        self.cross_group_architect = self._add_agent(
            "architect-other",
            "Other",
            kind="architect",
            group="other",
        )
        self.state.assign_agent_class(
            self.architect.id,
            "creative-architect",
            actor_kind="user",
            base_dir=self.tmp.name,
        )
        self.state.apply_effective_agent_class_for_launch(
            self.architect,
            base_dir=self.tmp.name,
        )

    def _add_agent(self, agent_id, name, *, kind="architect", group="g", **kwargs):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            group=group,
            cell_type="agent",
            kind=kind,
            **kwargs,
        )
        self.state.agents[cell.id] = cell
        self.state.groups.setdefault(group, []).append(cell.id)
        self.state._db_save_agent(cell)
        return cell

    async def _handle_command(self, payload):
        self.calls.append(dict(payload))
        if payload.get("cmd") == "inject_mcp_message":
            return {"type": "ok", "delivered": True}
        return {"type": "ok"}

    def _handler(self):
        return self.mcp_mod.create_mcp_handler(self._handle_command, self.state)

    async def _call(self, tool_name, arguments=None, *, req_id=1, agent_id=None):
        handler = self._handler()
        return await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments or {}},
                },
                headers={"X-Torque-Cell-Id": agent_id or self.architect.id},
            )
        )

    async def _list_tools(self):
        handler = self._handler()
        response = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": self.architect.id},
            )
        )
        return {tool["name"] for tool in response.payload["result"]["tools"]}

    def _result_payload(self, response):
        self.assertNotIn("error", response.payload)
        result = response.payload["result"]
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def _error_text(self, response):
        if "error" in response.payload:
            return response.payload["error"]["message"]
        result = response.payload["result"]
        self.assertTrue(result["isError"], result["content"][0]["text"])
        return result["content"][0]["text"]

    async def test_creative_class_projects_context_proposal_and_thinking_tools_only(self):
        self.assertEqual(self.architect.effective_agent_class_id, "creative-architect")
        self.assertEqual(self.architect.effective_agent_profile_id, "class-policy-creative-architect")
        tool_names = await self._list_tools()

        for name in {
            "torque_context",
            "architect_board_summary",
            "architect_task_list",
            "architect_task_show",
            "architect_area_list",
            "architect_area_show",
            "architect_initiative_list",
            "architect_initiative_show",
            "architect_decision_list",
            "architect_events_recent",
            "architect_product_task_propose",
            "architect_product_decision_create",
            "architect_product_decision_update",
            "architect_product_decision_link",
            "architect_product_peer_list",
            "architect_product_peer_message",
            "architect_product_message_user",
            "architect_product_ask_user",
            "architect_thinking_scratchpad_list",
            "architect_thinking_scratchpad_show",
            "architect_thinking_scratchpad_create",
            "architect_thinking_scratchpad_update",
            "architect_thinking_mind_map_list",
            "architect_thinking_mind_map_show",
            "architect_thinking_mind_map_create",
            "architect_thinking_mind_map_update",
            "architect_thinking_mind_map_node_create",
            "architect_thinking_mind_map_node_update",
            "architect_thinking_mind_map_link_create",
            "architect_thinking_mind_map_link_update",
        }:
            self.assertIn(name, tool_names)

        denied = {
            "architect_tool_search",
            "torque_message_user",
            "torque_ask",
            "architect_engineer_hire",
            "architect_engineer_set_specializations",
            "architect_engineer_dismiss",
            "architect_task_create",
            "architect_task_update",
            "architect_task_move",
            "architect_task_reassign",
            "architect_task_mark_covered",
            "architect_engineer_message",
            "architect_engineer_feedback_request",
            "architect_engineer_answer",
            "architect_peer_message",
            "architect_peer_inbox",
            "architect_reply",
            "architect_message_user",
            "architect_ask",
            "architect_decision_create",
            "architect_decision_update",
            "architect_decision_link",
            "architect_area_create",
            "architect_initiative_create",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_behavior_overlay_approve",
            "architect_mcp_calls",
            "architect_merge",
        }
        self.assertFalse(denied & tool_names)

        before_tasks = set(self.state.board_tasks)
        for index, tool_name in enumerate(sorted(denied), start=10):
            response = await self._call(
                tool_name,
                {"architect_id": self.peer.id, "message": "raw", "title": "raw"},
                req_id=index,
            )
            self.assertIn("Unknown tool", self._error_text(response), tool_name)
        self.assertEqual(before_tasks, set(self.state.board_tasks))
        self.assertEqual([], self.calls)

        allowed = await self._call(
            "architect_product_task_propose",
            {"title": "Idea slice", "description": "Proposal only."},
            req_id=100,
        )
        task = self.state.board_tasks[self._result_payload(allowed)["id"]]
        self.assertEqual("queued", task.dispatch_state)
        self.assertEqual("", task.assigned_engineer_id)
        self.assertEqual("", task.agent_id)
        self.assertIn("product-proposal", task.labels)

    async def test_thinking_wrappers_are_same_group_read_and_caller_owned_write(self):
        peer_note = self.db.create_scratchpad_note({
            "group": "g",
            "title": "Peer note",
            "body": "Readable but not writable.",
            "created_by_kind": "architect",
            "created_by_id": self.peer.id,
        })
        other_note = self.db.create_scratchpad_note({
            "group": "other",
            "title": "Other note",
            "created_by_kind": "architect",
            "created_by_id": self.cross_group_architect.id,
        })

        created = await self._call(
            "architect_thinking_scratchpad_create",
            {"title": "Opportunity scratch", "body": "Diverge first."},
        )
        note = self._result_payload(created)["note"]
        self.assertEqual("g", note["group_name"])
        self.assertEqual("architect", note["created_by_kind"])
        self.assertEqual(self.architect.id, note["created_by_id"])
        self.assertTrue(note["caller_owned"])

        listed = await self._call("architect_thinking_scratchpad_list", {}, req_id=2)
        notes = self._result_payload(listed)["notes"]
        by_id = {item["id"]: item for item in notes}
        self.assertIn(note["id"], by_id)
        self.assertIn(peer_note["id"], by_id)
        self.assertNotIn(other_note["id"], by_id)
        self.assertTrue(by_id[note["id"]]["caller_owned"])
        self.assertFalse(by_id[peer_note["id"]]["caller_owned"])

        peer_update = await self._call(
            "architect_thinking_scratchpad_update",
            {"note": peer_note["id"], "body": "steal"},
            req_id=3,
        )
        self.assertIn("caller-owned", self._error_text(peer_update))
        self.assertEqual(
            "Readable but not writable.",
            self.db.load_scratchpad_note(peer_note["id"])["body"],
        )
        cross_group_update = await self._call(
            "architect_thinking_scratchpad_update",
            {"note": other_note["id"], "body": "cross"},
            req_id=4,
        )
        self.assertIn("not found", self._error_text(cross_group_update).lower())

        own_update = await self._call(
            "architect_thinking_scratchpad_update",
            {"note": note["id"], "body": "Converged slice."},
            req_id=5,
        )
        self.assertEqual("Converged slice.", self._result_payload(own_update)["note"]["body"])

        peer_map = self.db.create_mind_map({
            "group": "g",
            "title": "Peer map",
            "created_by_kind": "architect",
            "created_by_id": self.peer.id,
        })
        map_created = await self._call(
            "architect_thinking_mind_map_create",
            {"title": "Opportunity map", "description": "Patterns."},
            req_id=6,
        )
        mind_map = self._result_payload(map_created)["mind_map"]
        self.assertTrue(mind_map["caller_owned"])

        map_list = await self._call("architect_thinking_mind_map_list", {}, req_id=7)
        maps = {item["id"]: item for item in self._result_payload(map_list)["mind_maps"]}
        self.assertIn(mind_map["id"], maps)
        self.assertIn(peer_map["id"], maps)
        self.assertFalse(maps[peer_map["id"]]["caller_owned"])

        peer_node = await self._call(
            "architect_thinking_mind_map_node_create",
            {"mind_map": peer_map["id"], "label": "Nope"},
            req_id=8,
        )
        self.assertIn("caller-owned", self._error_text(peer_node))
        self.assertEqual([], self.db.list_mind_map_nodes(peer_map["id"]))

        first = await self._call(
            "architect_thinking_mind_map_node_create",
            {"mind_map": mind_map["id"], "label": "Problem", "x": 1, "y": 2},
            req_id=9,
        )
        first_node = self._result_payload(first)["node"]
        second = await self._call(
            "architect_thinking_mind_map_node_create",
            {"mind_map": mind_map["id"], "label": "Small slice"},
            req_id=10,
        )
        second_node = self._result_payload(second)["node"]
        moved = await self._call(
            "architect_thinking_mind_map_node_position",
            {"node": first_node["id"], "position": {"x": 9, "y": 10}},
            req_id=11,
        )
        self.assertEqual(9.0, self._result_payload(moved)["node"]["x"])
        link = await self._call(
            "architect_thinking_mind_map_link_create",
            {
                "mind_map": mind_map["id"],
                "source_node_id": first_node["id"],
                "target_node_id": second_node["id"],
                "label": "frames",
            },
            req_id=12,
        )
        link_payload = self._result_payload(link)["link"]
        link_update = await self._call(
            "architect_thinking_mind_map_link_update",
            {"link": link_payload["id"], "label": "converges to"},
            req_id=13,
        )
        self.assertEqual("converges to", self._result_payload(link_update)["link"]["label"])
