import importlib
import json
import unittest

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPProfilePolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)

    def _state_with_architect(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        return state, architect

    async def test_explicit_full_architect_profile_preserves_tool_projection_and_direct_calls(self):
        baseline_state, baseline_architect = self._state_with_architect()
        projected_state, projected_architect = self._state_with_architect()
        projected_state.agent_profile_overrides = {
            projected_architect.id: "full-architect",
        }
        baseline_handler = self.mcp_mod.create_mcp_handler(
            lambda payload: self.fail(f"Unexpected baseline command: {payload}"),
            baseline_state,
        )

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            self.fail(f"Unexpected projected command: {payload}")

        projected_handler = self.mcp_mod.create_mcp_handler(
            fake_handle_command,
            projected_state,
        )

        baseline = await baseline_handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": baseline_architect.id},
            )
        )
        projected = await projected_handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": projected_architect.id},
            )
        )

        baseline_names = [tool["name"] for tool in baseline.payload["result"]["tools"]]
        projected_names = [tool["name"] for tool in projected.payload["result"]["tools"]]
        self.assertEqual(projected_names, baseline_names)
        self.assertIn("architect_engineer_hire", projected_names)
        self.assertIn("architect_peer_message", projected_names)

        settings = await projected_handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_get_architect_settings",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": projected_architect.id},
            )
        )
        self.assertFalse(settings.payload["result"]["isError"])
        payload = json.loads(settings.payload["result"]["content"][0]["text"])
        self.assertEqual(payload["type"], "architect_settings")

    async def test_product_manager_profile_hides_and_denies_dangerous_architect_tools(self):
        state, architect = self._state_with_architect()
        state.agent_profile_overrides = {architect.id: "product-manager-draft"}
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}

        self.assertIn("architect_product_board_summary", tool_names)
        self.assertIn("architect_product_peer_message", tool_names)
        self.assertIn("architect_product_decision_list", tool_names)
        for allowed_overlay_tool in {
            "architect_behavior_overlay_read",
            "architect_behavior_overlay_versions",
            "architect_behavior_overlay_diff",
            "architect_behavior_overlay_proposal_list",
            "architect_behavior_overlay_propose",
            "architect_behavior_overlay_rollback",
        }:
            self.assertIn(allowed_overlay_tool, tool_names)

        denied_tools = {
            "architect_tool_search",
            "architect_engineer_hire",
            "architect_engineer_set_specializations",
            "architect_engineer_dismiss",
            "architect_peer_message",
            "architect_task_create",
            "architect_task_pickup",
            "architect_task_update",
            "architect_task_reassign",
            "architect_task_move",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_engineer_message",
            "architect_engineer_answer",
            "architect_decision_create",
            "architect_decision_update",
            "architect_area_create",
            "architect_initiative_create",
            "architect_mcp_calls",
            "architect_behavior_overlay_propose_for_engineer",
            "architect_behavior_overlay_propose_for_role",
            "architect_behavior_overlay_approve",
            "architect_behavior_overlay_reject",
            "architect_behavior_overlay_rollback_role",
        }
        self.assertFalse(denied_tools & tool_names)

        for tool_name in sorted(denied_tools):
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": tool_name,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {}},
                    },
                    headers={"X-Torque-Cell-Id": architect.id},
                )
            )
            self.assertIn("error", response.payload, tool_name)
            self.assertIn("Unknown tool", response.payload["error"]["message"])

        self.assertEqual(calls, [])

        proposed = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": "overlay-propose",
                    "method": "tools/call",
                    "params": {
                        "name": "architect_behavior_overlay_propose",
                        "arguments": {
                            "text": "Prefer product-safe proposal language.",
                            "rationale": "PM class can propose its own behavior guidance.",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertFalse(proposed.payload["result"]["isError"])
        self.assertEqual(calls[-1]["cmd"], "behavior_overlay_propose")
        self.assertEqual(calls[-1]["agent_id"], architect.id)
        self.assertEqual(calls[-1]["proposed_by_kind"], "architect")

        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        state.agents[engineer.id] = engineer
        state.groups["g"].append(engineer.id)
        cross_scope = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": "overlay-cross",
                    "method": "tools/call",
                    "params": {
                        "name": "architect_behavior_overlay_read",
                        "arguments": {"agent_id": engineer.id},
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertTrue(cross_scope.payload["result"]["isError"])
        self.assertIn("own overlay", cross_scope.payload["result"]["content"][0]["text"])

    async def test_desired_assignment_alone_does_not_affect_running_session_until_effective_snapshot(self):
        state, architect = self._state_with_architect()
        architect.agent_profile_id = "product-manager-draft"
        architect.agent_profile_version = "1"

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}
        self.assertIn("architect_engineer_hire", tool_names)
        self.assertIn("architect_peer_inbox", tool_names)

        architect.effective_agent_profile_id = "product-manager-draft"
        architect.effective_agent_profile_version = "1"
        listed_after_launch = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names_after_launch = {
            tool["name"] for tool in listed_after_launch.payload["result"]["tools"]
        }
        self.assertNotIn("architect_engineer_hire", tool_names_after_launch)
        self.assertNotIn("architect_peer_inbox", tool_names_after_launch)
        self.assertNotIn("architect_reply", tool_names_after_launch)

    async def test_frozen_full_profile_snapshot_preserves_mcp_projection_after_launch(self):
        state, architect = self._state_with_architect()
        state.apply_effective_agent_profile_for_launch(architect)

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}

        self.assertEqual(architect.effective_agent_profile_id, "full-architect")
        self.assertIn("torque_context", tool_names)
        self.assertIn("architect_engineer_hire", tool_names)
        self.assertIn("architect_peer_inbox", tool_names)

    async def test_frozen_product_manager_snapshot_allows_safe_tools_and_denies_mixed_peer_tools(self):
        state, architect = self._state_with_architect()
        state.assign_agent_profile(
            architect.id,
            "product-manager-draft",
            actor_kind="user",
        )
        state.apply_effective_agent_profile_for_launch(architect)

        async def fake_handle_command(payload):
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}

        self.assertEqual(architect.effective_agent_profile_id, "product-manager-draft")
        self.assertIn("torque_context", tool_names)
        self.assertIn("architect_product_board_summary", tool_names)
        self.assertIn("architect_product_peer_message", tool_names)
        self.assertIn("architect_behavior_overlay_read", tool_names)
        self.assertIn("architect_behavior_overlay_propose", tool_names)
        self.assertNotIn("architect_peer_inbox", tool_names)
        self.assertNotIn("architect_reply", tool_names)
        self.assertNotIn("architect_engineer_hire", tool_names)
        self.assertNotIn("architect_behavior_overlay_propose_for_engineer", tool_names)
        self.assertNotIn("architect_behavior_overlay_propose_for_role", tool_names)

    async def test_torque_steward_class_hides_tool_search_and_lists_read_observation_tools(self):
        state, architect = self._state_with_architect()
        state.assign_agent_class(
            architect.id,
            "torque-steward",
            actor_kind="user",
        )
        state.apply_effective_agent_class_for_launch(architect)
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}

        self.assertEqual(architect.effective_agent_class_id, "torque-steward")
        self.assertEqual(architect.effective_agent_profile_id, "class-policy-torque-steward")
        for tool_name in {
            "torque_context",
            "architect_steward_operating_brief",
            "architect_events_recent",
            "architect_events_recent",
            "architect_ask",
            "architect_message_user",
            "architect_peer_list",
            "architect_peer_message",
            "architect_journal",
            "architect_journal_read",
        }:
            self.assertIn(tool_name, tool_names)

        # Steward grants telemetry reads, but architect_mcp_calls is deferred
        # today. With raw tool search denied, it is not part of the Steward
        # user-facing tools/list surface until a narrower wrapper is designed.
        self.assertNotIn("architect_mcp_calls", tool_names)

        denied_tools = {
            "architect_tool_search",
            "engineer_tool_search",
            "architect_engineer_hire",
            "architect_engineer_set_specializations",
            "architect_engineer_message",
            "architect_engineer_answer",
            "architect_task_create",
            "architect_task_pickup",
            "architect_task_update",
            "architect_task_reassign",
            "architect_task_move",
            "architect_task_mark_covered",
            "architect_pm_root_backlog_hygiene",
            "architect_area_create",
            "architect_initiative_create",
            "architect_decision_create",
            "architect_decision_update",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_behavior_overlay_read",
            "architect_behavior_overlay_versions",
            "architect_behavior_overlay_diff",
            "architect_behavior_overlay_proposal_list",
            "architect_behavior_overlay_propose",
            "architect_behavior_overlay_rollback",
        }
        self.assertFalse(denied_tools & tool_names)

        for tool_name in sorted(denied_tools):
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": tool_name,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {}},
                    },
                    headers={"X-Torque-Cell-Id": architect.id},
                )
            )
            self.assertIn("error", response.payload, tool_name)
            self.assertIn("Unknown tool", response.payload["error"]["message"])

        self.assertEqual(calls, [])

    async def test_product_manager_profile_cannot_use_peer_tools_for_engineer_threads(self):
        state, architect = self._state_with_architect()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        state.agents[engineer.id] = engineer
        state.groups["g"].append(engineer.id)
        state.agent_profile_overrides = {architect.id: "product-manager-draft"}

        class FakePeerMessageDB:
            def __init__(self):
                self.load_calls = []

            def load_agent_peer_messages_for_agent(self, *args, **kwargs):
                self.load_calls.append((args, kwargs))
                return [{
                    "id": "msg-engineer-thread",
                    "thread_id": "thread-engineer",
                    "sender_id": engineer.id,
                    "sender_kind": "engineer",
                    "recipient_id": architect.id,
                    "recipient_kind": "architect",
                    "message": "restricted engineer thread",
                    "ack_required": True,
                    "created_at": 123.0,
                }]

            def load_agent_peer_message(self, message_id):
                self.load_calls.append(((message_id,), {"single": True}))
                return {
                    "id": "msg-engineer-thread",
                    "thread_id": "thread-engineer",
                    "sender_id": engineer.id,
                    "sender_kind": "engineer",
                    "recipient_id": architect.id,
                    "recipient_kind": "architect",
                    "message": "restricted engineer thread",
                    "ack_required": True,
                    "created_at": 123.0,
                }

        fake_db = FakePeerMessageDB()
        state.db = fake_db
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}
        self.assertIn("architect_product_peer_message", tool_names)
        self.assertIn("architect_product_peer_inbox", tool_names)
        self.assertIn("architect_product_peer_reply", tool_names)
        self.assertNotIn("architect_peer_message", tool_names)
        self.assertNotIn("architect_peer_inbox", tool_names)
        self.assertNotIn("architect_reply", tool_names)

        search = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_tool_search",
                        "arguments": {
                            "query": "select:architect_peer_inbox,architect_reply",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertIn("error", search.payload)
        self.assertIn("Unknown tool", search.payload["error"]["message"])

        inbox = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "architect_peer_inbox", "arguments": {}},
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertIn("error", inbox.payload)
        self.assertIn("Unknown tool", inbox.payload["error"]["message"])

        reply = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_reply",
                        "arguments": {
                            "message_id": "msg-engineer-thread",
                            "message": "should not deliver",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertIn("error", reply.payload)
        self.assertIn("Unknown tool", reply.payload["error"]["message"])

        self.assertEqual(fake_db.load_calls, [])
        self.assertEqual(calls, [])

    async def test_restricted_engineer_profile_hides_dispatch_worktree_and_specialization_tools(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.agent_profile_overrides = {
            engineer.id: {
                "id": "read-only-engineer-test",
                "version": "1",
                "base_kind": "engineer",
                "grants": [
                    "observe.self_context",
                    "observe.board_summary",
                    "observe.task_detail",
                    "planning.area_read",
                    "planning.initiative_read",
                    "decision.list",
                ],
            }
        }

        async def fake_handle_command(payload):
            self.fail(f"Denied tool should not reach command layer: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        tool_names = {tool["name"] for tool in listed.payload["result"]["tools"]}

        self.assertIn("engineer_tool_search", tool_names)
        self.assertIn("engineer_board_summary", tool_names)
        self.assertIn("engineer_task_show", tool_names)
        for tool_name in {
            "engineer_task_dispatch",
            "engineer_batch_dispatch",
            "engineer_merge",
            "engineer_rebase",
            "engineer_create_pr",
            "engineer_worktree_remove",
            "engineer_specializations_list",
            "engineer_specialization_save",
            "engineer_agent_message",
            "engineer_peer_notify",
        }:
            self.assertNotIn(tool_name, tool_names)

        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_task_dispatch",
                        "arguments": {"task": "unsafe dispatch"},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertIn("error", response.payload)
        self.assertIn("Unknown tool", response.payload["error"]["message"])
