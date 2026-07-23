import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPProposalWrapperTests(unittest.IsolatedAsyncioTestCase):
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

        self.architect = self._add_agent("architect-1", "Architect", kind="architect")
        self.peer = self._add_agent("architect-2", "Productmind", kind="architect")
        self.torqly = self._add_agent("a5a7fc9e", "Torqly", kind="architect")
        self.full_peer = self._add_agent("architect-3", "Full Peer", kind="architect")
        self.cross_group_architect = self._add_agent(
            "architect-other",
            "Other Group Architect",
            kind="architect",
            group="other",
        )
        self.engineer = self._add_agent(
            "engineer-1",
            "Engineer",
            kind="engineer",
            hired_by_architect_id=self.architect.id,
        )
        self.torqly_engineer = self._add_agent(
            "engineer-torqly",
            "Torqly Engineer",
            kind="engineer",
            hired_by_architect_id=self.torqly.id,
        )
        self.worker = self._add_agent("worker-1", "Worker", kind="worker")
        self.state.assign_agent_class(
            self.architect.id,
            "product-manager",
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
        if payload.get("cmd") == "board_pickup_architect_task":
            try:
                return self.state.board_pickup_architect_task(
                    payload.get("id", ""),
                    architect_id=payload.get("architect_id", ""),
                    actor_name=payload.get("actor_name", ""),
                    actor_kind=payload.get("actor_kind", ""),
                    reason=payload.get("reason", ""),
                    source=payload.get("source", ""),
                    authorization=payload.get("authorization", {}),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}
        if payload.get("cmd") == "board_mark_task_covered":
            try:
                return self.state.board_mark_task_covered(
                    payload.get("id", ""),
                    covering_task_id=payload.get("covering_task_id", ""),
                    pr_url=payload.get("pr_url", ""),
                    sha=payload.get("sha", ""),
                    tests_run=payload.get("tests_run", ""),
                    evidence=payload.get("evidence", ""),
                    notes=payload.get("notes", ""),
                    actor_name=payload.get("actor_name", ""),
                    actor_id=payload.get("actor_id", ""),
                    actor_kind=payload.get("actor_kind", ""),
                    authorization=payload.get("authorization", {}),
                    move_to_done=bool(payload.get("move_to_done", False)),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}
        return {"type": "ok"}

    def _handler(self):
        return self.mcp_mod.create_mcp_handler(self._handle_command, self.state)

    def _freeze_default_architect(self, cell):
        """Apply the real built-in default Architect authority at launch."""
        self.state.assign_agent_class(
            cell.id,
            "default-architect",
            actor_kind="user",
            base_dir=self.tmp.name,
        )
        self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=self.tmp.name,
        )
        self.assertEqual("default-architect", cell.effective_agent_class_id)
        self.assertTrue(cell.effective_agent_class_snapshot)

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

    async def test_pm_class_projects_every_tool_with_matching_capabilities(self):
        self.assertEqual(self.architect.effective_agent_class_id, "product-manager")
        tool_names = await self._list_tools()

        for name in {
            "context", "tool_search", "task_create", "peer_list",
            "peer_message", "peer_inbox", "peer_reply", "user_message",
            "raise", "journal_write",
        }:
            self.assertIn(name, tool_names)

        denied = {
            "engineer_hire",
            "engineer_lifecycle",
            "engineer_specializations_update",
            "task_claim",
            "task_update",
            "task_move",
            "task_reassign",
            "agent_message",
            "feedback_request",
            "behavior_overlay_admin",
            "deploy_get",
            "settings_get",
            "telemetry_query",
            "agent_ask_answer",
            "architect_engineer_hire",
            "architect_engineer_rehire",
            "architect_engineer_dismiss",
            "architect_engineer_set_specializations",
            "architect_pending_hire_list",
            "architect_task_create",
            "architect_task_pickup",
            "architect_task_update",
            "architect_task_move",
            "architect_task_reassign",
            "architect_engineer_message",
            "architect_engineer_feedback_request",
            "architect_behavior_overlay_propose_for_engineer",
            "architect_behavior_overlay_propose_for_role",
            "architect_behavior_overlay_approve",
            "architect_behavior_overlay_reject",
            "architect_behavior_overlay_rollback_role",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_mcp_calls",
            "architect_engineer_reply",
            "architect_decision_create",
            "architect_decision_update",
        }
        self.assertFalse(denied & tool_names)

        for index, tool_name in enumerate(sorted(denied), start=10):
            response = await self._call(
                tool_name,
                {"architect_id": self.peer.id, "message": "raw", "title": "raw"},
                req_id=index,
            )
            self.assertIn("Unknown tool", self._error_text(response), tool_name)
        self.assertEqual([], self.calls)

    async def test_routed_product_proposal_task_can_be_picked_up_by_same_group_architect(self):
        # Exercise the public JSON-RPC route under a frozen default Architect
        # snapshot.  A plain unclassified test caller skips the class gate and
        # would not catch a pre-handler target-scope denial.
        self._freeze_default_architect(self.torqly)
        proposal = await self._call(
            "architect_task_propose",
            {
                "title": "Direct pickup root",
                "description": "PM proposal that should remain the root.",
                "suggested_action": "feature/implement",
            },
            req_id=2,
        )
        task_id = self._result_payload(proposal)["id"]
        task = self.state.board_tasks[task_id]
        self.assertEqual("", task.assigned_architect_id)
        self.assertEqual("", task.assigned_engineer_id)

        routed = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Please pick up this product-proposal root directly.",
                "context_task_ids": [task_id],
                "context_summary": "Direct Architect pickup request.",
            },
            req_id=3,
        )
        route_id = self._result_payload(routed)["message_id"]

        pickup = await self._call(
            "task_claim",
            {
                "task": task_id,
                "reason": "Accepted as original implementation root.",
                "source": f"Blueprint peer message {route_id}",
            },
            req_id=4,
            agent_id=self.torqly.id,
        )
        payload = self._result_payload(pickup)
        self.assertEqual("task_picked_up", payload["type"])
        self.assertEqual(self.torqly.id, payload["assigned_architect_id"])

        refreshed = self.state.board_tasks[task_id]
        self.assertEqual(self.torqly.id, refreshed.assigned_architect_id)
        self.assertEqual(self.architect.id, refreshed.created_by_architect_id)
        evidence = refreshed.completion_evidence
        self.assertIn("architect_pickup", evidence["sources"])
        pickup_evidence = evidence["architect_pickup"]
        self.assertEqual(
            "",
            pickup_evidence["previous_assignment"]["assigned_architect_id"],
        )
        self.assertEqual(
            "routed_product_proposal_root_pickup",
            pickup_evidence["authorization"]["scope"],
        )
        self.assertEqual(route_id, pickup_evidence["authorization"]["route_message_id"])
        self.assertTrue(pickup_evidence["authorization"]["route_thread_id"])
        self.assertIn(route_id, pickup_evidence["source"])
        self.assertEqual("architect_pickup", refreshed.messages[-1]["action"])

    async def test_architect_task_pickup_denies_unrouted_non_pm_or_already_claimed_tasks(self):
        self._freeze_default_architect(self.torqly)
        unrouted = self.state.board_add_task(
            "Unrouted product task",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        no_route = await self._call(
            "task_claim",
            {"task": unrouted.id},
            req_id=20,
            agent_id=self.torqly.id,
        )
        self.assertIn("route evidence", self._error_text(no_route))
        self.assertEqual("", unrouted.assigned_architect_id)

        normal_architect_task = self.state.board_add_task(
            "Normal architect task",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.torqly.id,
        )
        normal = await self._call(
            "task_claim",
            {"task": normal_architect_task.id},
            req_id=21,
            agent_id=self.full_peer.id,
        )
        self.assertIn("product proposal", self._error_text(normal))

        proposal = await self._call(
            "architect_task_propose",
            {"title": "Already claimed proposal root"},
            req_id=22,
        )
        claimed_id = self._result_payload(proposal)["id"]
        self.state.board_update_task(claimed_id, assigned_architect_id=self.peer.id)
        await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Route to Torqly but already claimed elsewhere.",
                "context_task_ids": [claimed_id],
            },
            req_id=23,
        )
        claimed = await self._call(
            "task_claim",
            {"task": claimed_id},
            req_id=24,
            agent_id=self.torqly.id,
        )
        self.assertIn("already assigned", self._error_text(claimed))
        self.assertEqual(self.peer.id, self.state.board_tasks[claimed_id].assigned_architect_id)

    async def test_frozen_default_architect_claim_rejects_invalid_route_evidence_without_mutation(self):
        self._freeze_default_architect(self.torqly)
        proposal = await self._call(
            "architect_task_propose",
            {"title": "Invalid-route frozen claim root"},
            req_id=30,
        )
        task_id = self._result_payload(proposal)["id"]
        task = self.state.board_tasks[task_id]
        # It is inbound and names the task, but lacks the durable product-peer
        # marker required by the claim handler.  The class gate must not hide
        # this typed handler denial as an unknown public tool.
        self.db.save_agent_peer_message({
            "id": "invalid-route-evidence",
            "thread_id": "invalid-route-thread",
            "group_name": "g",
            "sender_id": self.architect.id,
            "sender_kind": "architect",
            "recipient_id": self.torqly.id,
            "recipient_kind": "architect",
            "message": "This is not a product-peer route.",
            "created_at": 1.0,
            "context_task_ids": [task_id],
            "context_summary": "Invalid route fixture.",
            "context_snapshot": {},
        })
        before_assignment = task.assigned_architect_id
        before_evidence = dict(task.completion_evidence)
        before_messages = list(task.messages)

        denied = await self._call(
            "task_claim",
            {"task": task_id, "source": "invalid-route-evidence"},
            req_id=31,
            agent_id=self.torqly.id,
        )

        text = self._error_text(denied)
        self.assertNotIn("Unknown tool", text)
        self.assertIn("route evidence", text)
        self.assertEqual(before_assignment, task.assigned_architect_id)
        self.assertEqual(before_evidence, task.completion_evidence)
        self.assertEqual(before_messages, task.messages)

    async def test_public_task_mark_covered_routes_to_existing_handler(self):
        self._freeze_default_architect(self.torqly)
        covered = self.state.board_add_task(
            "Canonical covered root",
            "g",
            created_by_architect_id=self.torqly.id,
        )
        covering = self.state.board_add_task(
            "Canonical covering task",
            "g",
            created_by_architect_id=self.torqly.id,
        )

        response = await self._call(
            "task_mark_covered",
            {
                "task": covered.id,
                "covering_task": covering.id,
                "notes": "Canonical public handler registration coverage.",
                "move_to_done": True,
            },
            req_id=40,
            agent_id=self.torqly.id,
        )
        payload = self._result_payload(response)
        self.assertEqual("task_marked_covered", payload["type"])
        self.assertEqual("Done", self.state.board_tasks[covered.id].lane)
        evidence = self.state.board_tasks[covered.id].completion_evidence
        self.assertEqual(covering.id, evidence["covered_by"]["task_id"])
        self.assertEqual("covered_by", self.state.board_tasks[covered.id].messages[-1]["action"])

        other_owned = self.state.board_add_task(
            "Other Architect task",
            "g",
            created_by_architect_id=self.peer.id,
        )
        before_evidence = dict(other_owned.completion_evidence)
        denied = await self._call(
            "task_mark_covered",
            {
                "task": other_owned.id,
                "covering_task": covering.id,
                "notes": "Must remain denied.",
            },
            req_id=41,
            agent_id=self.torqly.id,
        )
        self.assertIn("not created by this architect", self._error_text(denied))
        self.assertEqual(before_evidence, other_owned.completion_evidence)
        self.assertEqual("Backlog", other_owned.lane)

    async def test_frozen_default_architect_marks_routed_proposal_root_covered(self):
        self._freeze_default_architect(self.torqly)
        root = self.state.board_add_task(
            "Frozen canonical covered proposal root",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        covering = self.state.board_add_task(
            "Frozen canonical covering task",
            "g",
            labels=[f"covers:{root.id}"],
            created_by_architect_id=self.torqly.id,
        )
        self.db.save_agent_peer_message({
            "id": "frozen-cover-route",
            "thread_id": "frozen-cover-thread",
            "group_name": "g",
            "sender_id": self.architect.id,
            "sender_kind": "architect",
            "recipient_id": self.torqly.id,
            "recipient_kind": "architect",
            "message": "Please cover this routed product proposal root.",
            "created_at": 1.0,
            "context_task_ids": [root.id],
            "context_summary": "Canonical frozen coverage route.",
            "context_snapshot": {
                "proposal_peer": {"marker": "torque.proposal_peer.v1"},
            },
        })

        response = await self._call(
            "task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "notes": "Covered through the canonical frozen route.",
            },
            req_id=50,
            agent_id=self.torqly.id,
        )

        payload = self._result_payload(response)
        self.assertEqual("task_marked_covered", payload["type"])
        evidence = root.completion_evidence["covered_by"]
        self.assertEqual(covering.id, evidence["task_id"])
        self.assertEqual(
            "routed_product_proposal_root",
            evidence["authorization"]["scope"],
        )
        self.assertEqual(
            "frozen-cover-route",
            evidence["authorization"]["route_message_id"],
        )

    async def test_pm_engineer_and_worker_do_not_get_architect_pickup_surface(self):
        for agent_id in (self.architect.id, self.engineer.id, self.worker.id):
            response = await self._call(
                "architect_task_pickup",
                {"task": "TORQUE:1130"},
                req_id=30,
                agent_id=agent_id,
            )
            self.assertRegex(
                self._error_text(response),
                "Unknown tool|architect tools are only available",
            )

    async def test_task_proposal_is_queued_unassigned_and_rejects_dispatch_fields(self):
        response = await self._call(
            "architect_task_propose",
            {
                "title": "Draft onboarding problem statement",
                "description": "Product proposal only.",
                "labels": ["discovery"],
                "suggested_action": "feature/implement",
                "suggested_specialization": "prompts-config",
            },
        )
        payload = self._result_payload(response)
        task_id = payload["id"]
        task = self.state.board_tasks[task_id]

        self.assertEqual("queued", task.dispatch_state)
        self.assertEqual("", task.assigned_engineer_id)
        self.assertEqual("", task.assigned_architect_id)
        self.assertEqual("", task.agent_id)
        self.assertEqual("", task.action_name)
        self.assertEqual({}, task.action_vars)
        self.assertEqual("", task.scheduled_at)
        self.assertEqual(self.architect.id, task.created_by_architect_id)
        self.assertEqual("feature/implement", task.suggested_action)
        self.assertEqual("prompts-config", task.suggested_specialization)
        self.assertIn("product-proposal", task.labels)
        self.assertIn("proposal-only", task.labels)
        self.assertIn("normal queued Board task", payload["caveat"])
        self.assertEqual([], self.calls)

        before = set(self.state.board_tasks)
        rejected = await self._call(
            "architect_task_propose",
            {"title": "Unsafe", "assigned_engineer_id": self.engineer.id},
            req_id=2,
        )
        self.assertIn("assigned_engineer_id", self._error_text(rejected))
        self.assertEqual(before, set(self.state.board_tasks))

        rejected_architect = await self._call(
            "architect_task_propose",
            {"title": "Unsafe architect", "assigned_architect_id": self.architect.id},
            req_id=3,
        )
        self.assertIn("assigned_architect_id", self._error_text(rejected_architect))
        self.assertEqual(before, set(self.state.board_tasks))

    async def test_product_decisions_are_proposed_only_owned_and_no_engineer_links(self):
        task_resp = await self._call("architect_task_propose", {"title": "Proposal task"})
        task_id = self._result_payload(task_resp)["id"]

        create = await self._call(
            "architect_decision_propose",
            {
                "title": "Propose prioritization model",
                "rationale": "Need PM review before acceptance.",
                "linked_task_ids": [task_id],
            },
            req_id=2,
        )
        decision = self._result_payload(create)["decision"]
        self.assertEqual("proposed", decision["status"])
        self.assertEqual([task_id], decision["linked_task_ids"])
        self.assertEqual([], decision["linked_engineer_ids"])
        self.assertEqual(
            "torque.decision_proposal.v1",
            decision["metadata"]["product_proposal"]["marker"],
        )

        accepted = await self._call(
            "architect_decision_propose",
            {"title": "Bad", "rationale": "No", "status": "accepted"},
            req_id=3,
        )
        self.assertIn("must remain proposed", self._error_text(accepted))

        update_bad = await self._call(
            "architect_decision_proposal_update",
            {"id": decision["id"], "status": "accepted"},
            req_id=4,
        )
        self.assertIn("must remain proposed", self._error_text(update_bad))

        link_bad = await self._call(
            "architect_decision_proposal_link",
            {"id": decision["id"], "engineer_id": self.engineer.id, "task_id": task_id},
            req_id=5,
        )
        self.assertIn("cannot link engineers", self._error_text(link_bad))

        other = self.state.save_decision({
            "id": "decision-other",
            "architect_id": self.peer.id,
            "title": "Other",
            "rationale": "Other architect",
            "status": "proposed",
            "metadata": {"product_proposal": {"marker": "torque.decision_proposal.v1"}},
        })
        self.assertIsNotNone(other)
        update_other = await self._call(
            "architect_decision_proposal_update",
            {"id": "decision-other", "title": "steal"},
            req_id=6,
        )
        self.assertIn("Unknown tool", self._error_text(update_other))

    async def test_proposal_area_show_hides_non_product_task_and_raw_decision_links(self):
        hidden_task = self.state.board_add_task(
            "Hidden task",
            "g",
            lane="Backlog",
            labels=["internal"],
        )
        product_task = self.state.board_add_task(
            "Product task",
            "g",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
        )
        raw_decision = self.state.save_decision({
            "id": "decision-raw",
            "architect_id": self.architect.id,
            "title": "Accepted secret",
            "rationale": "Not product-scoped",
            "status": "accepted",
        })
        self.assertIsNotNone(raw_decision)
        product_decision_resp = await self._call(
            "architect_decision_propose",
            {
                "title": "Visible product decision",
                "rationale": "Proposed-only product decision.",
                "linked_task_ids": [product_task.id],
            },
            req_id=2,
        )
        product_decision = self._result_payload(product_decision_resp)["decision"]
        area = self.db.create_area({
            "group": "g",
            "title": "PM scoped area",
            "created_by_kind": "user",
            "owner_kind": "user",
        })
        self.db.save_area_link(area["id"], "task", hidden_task.id)
        self.db.save_area_link(area["id"], "task", product_task.id)
        self.db.save_area_link(area["id"], "decision", "decision-raw")
        self.db.save_area_link(area["id"], "decision", product_decision["id"])

        response = await self._call(
            "architect_proposal_area_show",
            {"area": area["id"]},
            req_id=3,
        )
        response_text = response.payload["result"]["content"][0]["text"]
        payload = self._result_payload(response)

        self.assertNotIn(hidden_task.id, response_text)
        self.assertNotIn("Hidden task", response_text)
        self.assertNotIn("decision-raw", response_text)
        self.assertNotIn("Accepted secret", response_text)
        self.assertEqual(payload["links"]["tasks"], [product_task.id])
        self.assertEqual(payload["hidden_link_counts"]["tasks"], 1)
        self.assertEqual(payload["linked_tasks"]["count"], 1)
        self.assertEqual(payload["linked_tasks"]["hidden_count"], 1)
        self.assertEqual(
            [item["title"] for item in payload["linked_tasks"]["items"]],
            ["Product task"],
        )
        self.assertEqual(payload["links"]["decisions"], [product_decision["id"]])
        self.assertEqual(payload["hidden_link_counts"]["decisions"], 1)
        self.assertEqual(payload["linked_decisions"]["count"], 1)
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 1)
        self.assertEqual(payload["linked_decisions"]["ids"], [product_decision["id"]])
        self.assertEqual(
            [item["title"] for item in payload["linked_decisions"]["items"]],
            ["Visible product decision"],
        )

        list_response = await self._call(
            "architect_proposal_area_list",
            {"include_links": True},
            req_id=4,
        )
        list_text = list_response.payload["result"]["content"][0]["text"]
        list_payload = self._result_payload(list_response)
        listed_area = next(item for item in list_payload["areas"] if item["id"] == area["id"])
        self.assertNotIn(hidden_task.id, list_text)
        self.assertNotIn("Hidden task", list_text)
        self.assertNotIn("decision-raw", list_text)
        self.assertNotIn("Accepted secret", list_text)
        self.assertEqual(listed_area["links"]["tasks"], [product_task.id])
        self.assertEqual(listed_area["hidden_link_counts"]["tasks"], 1)
        self.assertEqual(listed_area["links"]["decisions"], [product_decision["id"]])
        self.assertEqual(listed_area["hidden_link_counts"]["decisions"], 1)

    async def test_proposal_initiative_show_hides_non_product_task_and_raw_decision_links(self):
        hidden_task = self.state.board_add_task(
            "Hidden initiative task",
            "g",
            lane="Backlog",
            labels=["internal"],
        )
        product_task = self.state.board_add_task(
            "Product initiative task",
            "g",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
        )
        raw_decision = self.state.save_decision({
            "id": "decision-raw-initiative",
            "architect_id": self.architect.id,
            "title": "Accepted initiative secret",
            "rationale": "Not product-scoped",
            "status": "accepted",
        })
        self.assertIsNotNone(raw_decision)
        product_decision_resp = await self._call(
            "architect_decision_propose",
            {
                "title": "Visible initiative product decision",
                "rationale": "Proposed-only product decision.",
                "linked_task_ids": [product_task.id],
            },
            req_id=2,
        )
        product_decision = self._result_payload(product_decision_resp)["decision"]
        initiative = self.db.create_initiative({
            "group": "g",
            "title": "PM scoped initiative",
            "created_by_kind": "user",
            "owner_kind": "user",
        })
        self.db.save_initiative_link(initiative["id"], "task", hidden_task.id)
        self.db.save_initiative_link(initiative["id"], "task", product_task.id)
        self.db.save_initiative_link(initiative["id"], "decision", "decision-raw-initiative")
        self.db.save_initiative_link(initiative["id"], "decision", product_decision["id"])

        response = await self._call(
            "architect_proposal_initiative_show",
            {"initiative": initiative["id"]},
            req_id=3,
        )
        response_text = response.payload["result"]["content"][0]["text"]
        payload = self._result_payload(response)

        self.assertNotIn(hidden_task.id, response_text)
        self.assertNotIn("Hidden initiative task", response_text)
        self.assertNotIn("decision-raw-initiative", response_text)
        self.assertNotIn("Accepted initiative secret", response_text)
        self.assertEqual(payload["links"]["tasks"], [product_task.id])
        self.assertEqual(payload["linked_tasks"]["count"], 1)
        self.assertEqual(payload["linked_tasks"]["hidden_count"], 1)
        self.assertEqual(
            [item["title"] for item in payload["linked_tasks"]["items"]],
            ["Product initiative task"],
        )
        self.assertEqual(payload["links"]["decisions"], [product_decision["id"]])
        self.assertEqual(payload["linked_decisions"]["count"], 1)
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 1)
        self.assertEqual(payload["linked_decisions"]["items"], [product_decision["id"]])

        list_response = await self._call(
            "architect_proposal_initiative_list",
            {"include_links": True},
            req_id=4,
        )
        list_text = list_response.payload["result"]["content"][0]["text"]
        list_payload = self._result_payload(list_response)
        listed_initiative = next(
            item for item in list_payload["initiatives"]
            if item["id"] == initiative["id"]
        )
        self.assertNotIn(hidden_task.id, list_text)
        self.assertNotIn("Hidden initiative task", list_text)
        self.assertNotIn("decision-raw-initiative", list_text)
        self.assertNotIn("Accepted initiative secret", list_text)
        self.assertEqual(listed_initiative["links"]["tasks"], [product_task.id])
        self.assertEqual(listed_initiative["linked_tasks"]["hidden_count"], 1)
        self.assertEqual(listed_initiative["links"]["decisions"], [product_decision["id"]])
        self.assertEqual(listed_initiative["linked_decisions"]["hidden_count"], 1)

    async def test_proposal_peer_list_and_message_include_same_group_full_architect_with_anchors(self):
        product_task = self.state.board_add_task(
            "TORQUE:909 Product Manager permanence",
            "g",
            lane="Backlog",
            id="TORQUE:909",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        self.assertIsNotNone(product_task)
        decision = self.state.save_decision({
            "id": "decision-54e82d9a220f",
            "architect_id": self.architect.id,
            "title": "Product Manager permanence proposal",
            "rationale": "Proposed product scope for PM dogfood state.",
            "status": "proposed",
            "linked_task_ids": ["TORQUE:909"],
            "metadata": {
                "product_proposal": {
                    "marker": "torque.decision_proposal.v1",
                    "owner_architect_id": self.architect.id,
                    "proposed_only": True,
                },
            },
        })
        self.assertIsNotNone(decision)

        peer_list = await self._call("architect_proposal_peer_list", {}, req_id=2)
        peers = self._result_payload(peer_list)["architects"]
        peer_ids = {item["id"] for item in peers}

        self.assertIn(self.peer.id, peer_ids)
        self.assertIn("a5a7fc9e", peer_ids)
        torqly = next(item for item in peers if item["id"] == "a5a7fc9e")
        self.assertEqual("product-peer-authority", torqly["proposal_peer_scope"])
        self.assertNotIn(self.cross_group_architect.id, peer_ids)
        self.assertNotIn(self.engineer.id, peer_ids)
        self.assertNotIn(self.worker.id, peer_ids)

        sent = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": "a5a7fc9e",
                "message": "Torqly, please review the product-planning permanence proposal.",
                "context_task_ids": ["TORQUE:909"],
                "context_decision_ids": ["decision-54e82d9a220f"],
                "context_summary": "Product-scoped product-planning permanence/TORQUE:909 handoff.",
            },
            req_id=3,
        )
        payload = self._result_payload(sent)
        self.assertEqual("a5a7fc9e", payload["recipient_architect_id"])
        saved = self.db.load_agent_peer_message(payload["message_id"])
        self.assertEqual(["TORQUE:909"], saved["context_task_ids"])
        self.assertEqual(["decision-54e82d9a220f"], saved["context_decision_ids"])
        self.assertEqual(
            "torque.proposal_peer.v1",
            saved["context_snapshot"]["proposal_peer"]["marker"],
        )
        self.assertEqual(
            "torque.proposal_context.v1",
            saved["context_snapshot"]["proposal_context"]["marker"],
        )
        self.assertEqual(1, len(self.calls))
        self.assertEqual("inject_mcp_message", self.calls[0]["cmd"])
        self.assertEqual("a5a7fc9e", self.calls[0]["agent_id"])

        reply = await self._call(
            "architect_proposal_peer_reply",
            {
                "message_id": payload["message_id"],
                "message": "Adding a product-scoped follow-up for Torqly.",
                "context_task_ids": ["TORQUE:909"],
                "context_decision_ids": ["decision-54e82d9a220f"],
            },
            req_id=4,
        )
        reply_payload = self._result_payload(reply)
        reply_row = self.db.load_agent_peer_message(reply_payload["message_id"])
        self.assertEqual(payload["thread_id"], reply_payload["thread_id"])
        self.assertEqual(payload["thread_id"], reply_row["thread_id"])
        self.assertEqual(
            "torque.proposal_peer.v1",
            reply_row["context_snapshot"]["proposal_peer"]["marker"],
        )
        self.assertEqual(["TORQUE:909"], reply_row["context_task_ids"])
        self.assertEqual(["decision-54e82d9a220f"], reply_row["context_decision_ids"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual("a5a7fc9e", self.calls[1]["agent_id"])

    async def test_proposal_peer_message_denies_cross_group_non_architect_and_mixed_context_before_side_effects(self):
        product_task = self.state.board_add_task(
            "PM anchor",
            "g",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        self.assertIsNotNone(product_task)
        attempts = [
            (
                {
                    "architect_id": self.cross_group_architect.id,
                    "message": "cross group",
                    "context_task_ids": [product_task.id],
                },
                "Unknown tool",
            ),
            (
                {
                    "architect_id": self.engineer.id,
                    "message": "engineer",
                    "context_task_ids": [product_task.id],
                },
                "Architect not found",
            ),
            (
                {
                    "architect_id": self.worker.id,
                    "message": "worker",
                    "context_task_ids": [product_task.id],
                },
                "Architect not found",
            ),
            (
                {
                    "architect_id": self.torqly.id,
                    "message": "mixed context",
                    "context_task_ids": [product_task.id],
                    "context_engineer_ids": [self.engineer.id],
                },
                "context_engineer_ids",
            ),
        ]

        for index, (args, expected) in enumerate(attempts, start=30):
            before_rows = self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)
            before_calls = list(self.calls)
            response = await self._call(
                "architect_proposal_peer_message",
                args,
                req_id=index,
            )
            self.assertIn(expected, self._error_text(response))
            self.assertEqual(
                [row["id"] for row in before_rows],
                [row["id"] for row in self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)],
            )
            self.assertEqual(before_calls, self.calls)

    async def test_proposal_peer_threads_are_marker_filtered_and_ack_requires_anchor(self):
        task_resp = await self._call("architect_task_propose", {"title": "Anchor task"})
        task_id = self._result_payload(task_resp)["id"]

        before_rows = self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)
        before_calls = list(self.calls)
        unanchored = await self._call(
            "architect_proposal_peer_message",
            {"architect_id": self.torqly.id, "message": "unanchored product peer"},
            req_id=20,
        )
        self.assertIn("product-scope anchor", self._error_text(unanchored))
        self.assertEqual(
            [row["id"] for row in before_rows],
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)],
        )
        self.assertEqual(before_calls, self.calls)

        no_anchor = await self._call(
            "architect_proposal_peer_message",
            {"architect_id": self.peer.id, "message": "ack?", "ack_required": True},
            req_id=2,
        )
        self.assertIn("product-scope anchor", self._error_text(no_anchor))
        self.assertEqual([], self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20))

        sent = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.peer.id,
                "message": "Please review this product proposal.",
                "ack_required": True,
                "context_task_ids": [task_id],
            },
            req_id=3,
        )
        sent_payload = self._result_payload(sent)
        product_message_id = sent_payload["message_id"]
        saved = self.db.load_agent_peer_message(product_message_id)
        self.assertEqual("torque.proposal_peer.v1", saved["context_snapshot"]["proposal_peer"]["marker"])
        self.assertTrue(saved["ack_required"])

        # Raw Architect↔Architect row without marker and Architect↔Engineer row stay hidden.
        self.db.save_agent_peer_message({
            "id": "msg-raw-peer",
            "thread_id": "thread-raw-peer",
            "group_name": "g",
            "sender_id": self.peer.id,
            "sender_kind": "architect",
            "recipient_id": self.architect.id,
            "recipient_kind": "architect",
            "message": "raw hidden",
            "created_at": 2,
        })
        self.db.save_agent_peer_message({
            "id": "msg-engineer",
            "thread_id": "thread-engineer",
            "group_name": "g",
            "sender_id": self.engineer.id,
            "sender_kind": "engineer",
            "recipient_id": self.architect.id,
            "recipient_kind": "architect",
            "message": "engineer hidden",
            "created_at": 3,
            "context_snapshot": {"proposal_peer": {"marker": "torque.proposal_peer.v1"}},
        })

        inbox = await self._call("architect_proposal_peer_inbox", {}, req_id=4)
        threads = self._result_payload(inbox)["threads"]
        self.assertEqual([sent_payload["thread_id"]], [thread["thread_id"] for thread in threads])
        self.assertEqual([product_message_id], [threads[0]["messages"][0]["id"]])

        before_rows = self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)
        before_calls = list(self.calls)
        mixed_context_reply = await self._call(
            "architect_proposal_peer_reply",
            {
                "message_id": product_message_id,
                "message": "reply with invalid engineer context",
                "context_engineer_ids": [self.engineer.id],
            },
            req_id=45,
        )
        self.assertIn("context_engineer_ids", self._error_text(mixed_context_reply))
        self.assertEqual(
            [row["id"] for row in before_rows],
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)],
        )
        self.assertEqual(before_calls, self.calls)

        before_rows = self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)
        raw_reply = await self._call(
            "architect_proposal_peer_reply",
            {"message_id": "msg-raw-peer", "message": "no"},
            req_id=5,
        )
        self.assertIn("product-peer scope", self._error_text(raw_reply))
        self.assertEqual(
            [row["id"] for row in before_rows],
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)],
        )

        reply = await self._call(
            "architect_proposal_peer_reply",
            {"message_id": product_message_id, "message": "Acknowledged.", "ack_required": True},
            req_id=6,
        )
        reply_payload = self._result_payload(reply)
        reply_row = self.db.load_agent_peer_message(reply_payload["message_id"])
        self.assertEqual(sent_payload["thread_id"], reply_row["thread_id"])
        self.assertEqual("torque.proposal_peer.v1", reply_row["context_snapshot"]["proposal_peer"]["marker"])

    async def test_user_message_and_journal_wrappers_validate_product_scope(self):
        invalid_user = await self._call(
            "architect_proposal_message_user",
            {"message": "Hidden context", "context_task_ids": ["TORQUE:404"]},
        )
        self.assertIn("Task not found", self._error_text(invalid_user))
        self.assertEqual([], self.db.load_direct_messages_for_agent(self.architect.id, limit=20))

        bad_journal = await self._call(
            "architect_proposal_journal",
            {"type": "decision", "entry": "No decision journal rows for PM."},
            req_id=2,
        )
        self.assertIn("observation, checkpoint, plan", self._error_text(bad_journal))

        good_journal = await self._call(
            "architect_proposal_journal",
            {"type": "observation", "entry": "Scratch product-planning recovery note."},
            req_id=3,
        )
        self._result_payload(good_journal)
        read = await self._call("architect_proposal_journal_read", {}, req_id=4)
        entries = self._result_payload(read)["entries"]
        self.assertIn("observation", [entry["type"] for entry in entries])
        self.assertTrue(any("Scratch product-planning recovery note" in entry.get("entry", "") for entry in entries))
