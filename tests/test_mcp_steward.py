import importlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPStewardBriefTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_mod = importlib.import_module("torque.db")
        self.state_mod = importlib.import_module("torque.state")
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)
        self.old_data_dir = self.state_mod.DATA_DIR
        self.state_mod.DATA_DIR = str(Path(self.tmp.name) / "data")
        self.addCleanup(lambda: setattr(self.state_mod, "DATA_DIR", self.old_data_dir))
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["g"] = []
        self.calls = []

        self.steward = self._add_agent("steward-1", "Steward", kind="architect")
        self.torqly = self._add_agent("torqly-1", "Torqly", kind="architect")
        self.engineer = self._add_agent(
            "engineer-1",
            "Forge",
            kind="engineer",
            hired_by_architect_id=self.torqly.id,
            engineer_specializations=["runtime-maintenance"],
        )
        self.state.assign_agent_class(
            self.steward.id,
            "torque-steward",
            actor_kind="user",
            base_dir=self.tmp.name,
        )
        self.state.apply_effective_agent_class_for_launch(
            self.steward,
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
        if payload.get("cmd") == "board_add_task":
            task = self.state.board_add_task(
                payload.get("task", ""),
                payload.get("group", "g"),
                lane=payload.get("lane", "Backlog"),
                description=payload.get("description", ""),
                labels=payload.get("labels", []),
            )
            return {"type": "ok", "task_id": task.id}
        if payload.get("cmd") == "board_update_task":
            task_id = str(payload.get("id", "") or "")
            task = self.state.board_tasks.get(task_id)
            if not task:
                return {"type": "error", "message": "task not found"}
            for key in (
                "created_by_architect_id",
                "reply_agent_id",
                "assigned_engineer_id",
                "agent_id",
                "action_name",
                "status",
            ):
                if key in payload:
                    setattr(task, key, payload[key])
            self.state._db_save_task(task)
            return {"type": "ok", "task_id": task.id}
        return {"type": "ok"}

    def _handler(self):
        return self.mcp_mod.create_mcp_handler(self._handle_command, self.state)

    async def _call(self, tool_name, arguments=None, *, req_id=1, agent_id=None):
        return await self._handler()(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments or {}},
                },
                headers={"X-Torque-Cell-Id": agent_id or self.steward.id},
            )
        )

    async def _list_tools(self):
        response = await self._handler()(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": self.steward.id},
            )
        )
        return {tool["name"] for tool in response.payload["result"]["tools"]}

    def _payload(self, response):
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

    async def test_steward_projection_exposes_operating_brief_and_communication_but_no_mutations(self):
        tools = await self._list_tools()

        self.assertIn("architect_steward_operating_brief", tools)
        self.assertIn("architect_help_query", tools)
        self.assertIn("architect_events_recent", tools)
        self.assertIn("architect_ask", tools)
        self.assertIn("architect_message_user", tools)
        self.assertIn("architect_journal", tools)
        self.assertIn("architect_journal_read", tools)
        self.assertIn("architect_peer_list", tools)
        self.assertIn("architect_peer_message", tools)
        for denied in {
            "architect_tool_search",
            "torque_ask",
            "torque_message_user",
            "architect_task_create",
            "architect_task_pickup",
            "architect_task_update",
            "architect_engineer_hire",
            "architect_engineer_message",
            "architect_engineer_answer",
            "architect_decision_create",
            "architect_decision_update",
            "architect_decision_link",
            "architect_behavior_overlay_read",
            "architect_behavior_overlay_propose",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_product_message_user",
            "architect_product_peer_message",
            "architect_product_journal",
            "architect_digest_filter",
            "architect_board_summary",
        }:
            self.assertNotIn(denied, tools)
            response = await self._call(denied, {"title": "nope"})
            self.assertIn("Unknown tool", self._error_text(response))
        self.assertEqual([], self.calls)

    async def test_steward_user_message_and_ask_create_durable_user_thread_records(self):
        message_response = await self._call(
            "architect_message_user",
            {"message": "Status: release smoke is pending operator confirmation."},
        )
        message_payload = self._payload(message_response)
        message_id = message_payload["message_id"]
        message_row = self.db.load_direct_message(message_id)
        self.assertIsNotNone(message_row)
        self.assertEqual(self.steward.id, message_row["sender_id"])
        self.assertEqual("architect", message_row["sender_kind"])
        self.assertEqual("user", message_row["recipient_kind"])
        self.assertEqual("message", message_row["message_type"])

        ask_response = await self._call(
            "architect_ask",
            {
                "question": "Should Steward recommend deferring launch until live smoke passes?",
                "description": "Need a blocking confirmation before recommending next steps.",
            },
        )
        ask_payload = self._payload(ask_response)
        task = self.state.board_tasks[ask_payload["task_id"]]
        self.assertEqual("Awaiting Input", task.status)
        self.assertEqual(self.steward.id, task.created_by_architect_id)
        self.assertEqual(self.steward.id, task.reply_agent_id)
        self.assertEqual("", task.assigned_engineer_id)
        self.assertIn("architect-ask", task.labels)
        ask_rows = self.db.load_direct_messages_for_agent(
            self.steward.id,
            peer_id="user",
            limit=20,
        )
        ask_mirrors = [
            row for row in ask_rows
            if row.get("message_type") == "ask"
            and row.get("source_task_id") == task.id
        ]
        self.assertEqual(1, len(ask_mirrors))
        self.assertEqual("user", ask_mirrors[0]["recipient_kind"])
        self.assertTrue(ask_mirrors[0]["blocking"])

    async def test_steward_journal_is_own_architect_journal_only(self):
        self.state.architect_journal_append(
            self.torqly.id,
            "observation",
            "Torqly-only note",
        )
        append_response = await self._call(
            "architect_journal",
            {
                "type": "checkpoint",
                "entry": "Recorded Steward communication wave checkpoint.",
                "architect_id": self.torqly.id,
            },
        )
        append_payload = self._payload(append_response)
        self.assertEqual(self.steward.id, append_payload["architect_id"])

        read_response = await self._call("architect_journal_read", {"limit": 10})
        read_payload = self._payload(read_response)
        entries = read_payload["entries"]
        self.assertEqual([self.steward.id], sorted({entry["architect_id"] for entry in entries}))
        self.assertIn(
            "Recorded Steward communication wave checkpoint.",
            [entry["entry"] for entry in entries],
        )
        self.assertNotIn("Torqly-only note", [entry["entry"] for entry in entries])

    async def test_steward_peer_list_and_message_are_same_group_architect_only(self):
        other_group_architect = self._add_agent(
            "arch-other-group",
            "Other Group Architect",
            kind="architect",
            group="other",
        )
        _engineer_peer = self.engineer

        list_response = await self._call("architect_peer_list", {})
        list_payload = self._payload(list_response)
        peer_ids = {item["id"] for item in list_payload["architects"]}
        self.assertIn(self.torqly.id, peer_ids)
        self.assertNotIn(self.steward.id, peer_ids)
        self.assertNotIn(self.engineer.id, peer_ids)
        self.assertNotIn(other_group_architect.id, peer_ids)

        message_response = await self._call(
            "architect_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Steward handoff nudge: please review pending smoke confirmation.",
                "context_summary": "Same-group Architect coordination only.",
            },
        )
        message_payload = self._payload(message_response)
        peer_row = self.db.load_agent_peer_message(message_payload["message_id"])
        self.assertIsNotNone(peer_row)
        self.assertEqual(self.steward.id, peer_row["sender_id"])
        self.assertEqual(self.torqly.id, peer_row["recipient_id"])
        self.assertEqual("architect", peer_row["recipient_kind"])
        self.assertIn("handoff nudge", peer_row["message"])

        cross_group = await self._call(
            "architect_peer_message",
            {
                "architect_id": other_group_architect.id,
                "message": "This must not cross group scope.",
            },
        )
        self.assertIn("architect not found in scope", self._error_text(cross_group))

        engineer_target = await self._call(
            "architect_peer_message",
            {
                "architect_id": self.engineer.id,
                "message": "This must not target an Engineer.",
            },
        )
        self.assertIn("Architect not found", self._error_text(engineer_target))

    async def test_operating_brief_reports_anomalies_and_responsible_actors_read_only(self):
        now_ts = 2_000_000_000
        old = datetime.fromtimestamp(now_ts - 48 * 3600, tz=timezone.utc).isoformat()

        parent = self.state.board_add_task(
            "Ship release card",
            "g",
            lane="In Progress",
            assigned_engineer_id=self.engineer.id,
            labels=["release"],
            suggested_specialization="runtime-maintenance",
        )
        parent.updated_at = old
        parent.health_state = "stale-in-progress"
        parent.worktree_boundary = {
            "repo_root": "/repo",
            "branch": "torque/forge/release",
            "base_branch": "main",
            "status": "open",
            "recorded_at": old,
        }
        child = self.state.board_add_task(
            "Review release card",
            "g",
            lane="Done",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            action_name="feature/review",
            labels=["review"],
        )
        child.updated_at = old
        ask = self.state.board_add_task(
            "Need operator smoke answer",
            "g",
            lane="Backlog",
            labels=["torque:human", "operator-smoke"],
            status="Awaiting Input",
        )
        ask.updated_at = old
        review = self.state.board_add_task(
            "Review slow implementation",
            "g",
            lane="In Progress",
            action_name="feature/review",
            labels=["review"],
        )
        review.updated_at = old
        worker = self._add_agent(
            "worker-1",
            "Silent Worker",
            kind="worker",
            owner_engineer_id=self.engineer.id,
            status="running",
            last_progress_at=now_ts - 5 * 3600,
        )
        worker.current_task_id = parent.id
        parent.agent_id = worker.id
        unused = self._add_agent("worker-2", "Unused Worker", kind="worker", status="idle")
        unused.tasks_dispatched = 0

        response = await self._call(
            "architect_steward_operating_brief",
            {"mode": "onboarding", "now_ts": now_ts, "limit_per_section": 3, "stale_after_hours": 24, "silent_after_hours": 2},
        )
        payload = self._payload(response)

        self.assertEqual("steward_operating_brief", payload["type"])
        self.assertEqual("onboarding", payload["mode"])
        self.assertTrue(payload["authority_contract"]["mutation_performed"] is False)
        self.assertIn("observed_facts", payload)
        self.assertIn("inferred_risks", payload)
        self.assertIn("suggested_next_steps", payload)
        self.assertEqual(1, payload["anomalies"]["blocked_asks"]["count"])
        self.assertGreaterEqual(payload["anomalies"]["stale_handoffs"]["count"], 1)
        self.assertEqual(1, payload["anomalies"]["stale_reviews"]["count"])
        self.assertEqual(1, payload["anomalies"]["branch_boundary_merge_gates"]["count"])
        self.assertEqual(1, payload["anomalies"]["silent_agents_workstreams"]["count"])
        self.assertEqual(1, payload["anomalies"]["dangling_unused_workers"]["count"])
        actors = {item["responsible_actor"] for item in payload["responsible_agent_suggestions"]}
        self.assertIn("user", actors)
        self.assertTrue(any("Forge" in actor for actor in actors))
        self.assertTrue(payload["scoping"]["reads_only"])
        self.assertIn("docs/reference/help.md", {ref["source_path"] for ref in payload["onboarding"]["help_refs"]})
        self.assertEqual([], self.calls)
