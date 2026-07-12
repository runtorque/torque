import importlib
import json
import unittest

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPClassAuthorityTests(unittest.IsolatedAsyncioTestCase):
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
        for tool_name in {
            "torque_context",
            "architect_group_health_brief",
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
            "architect_proposal_root_backlog_hygiene",
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

    async def test_class_task_read_self_scope_filters_lists_and_denies_group_target(self):
        state, architect = self._state_with_architect()
        peer = self.state_mod.AgentCell(
            id="architect-2",
            name="Peer",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[peer.id] = peer
        state.groups["g"].append(peer.id)
        own_task = self.state_mod.BoardTask(
            id="task-own",
            task="Own task",
            group="g",
            lane="Backlog",
            created_by_architect_id=architect.id,
        )
        peer_task = self.state_mod.BoardTask(
            id="task-peer",
            task="Peer task",
            group="g",
            lane="Backlog",
            created_by_architect_id=peer.id,
        )
        state.board_tasks[own_task.id] = own_task
        state.board_tasks[peer_task.id] = peer_task
        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"task.read": "self"},
            },
        }

        handler = self.mcp_mod.create_mcp_handler(
            lambda payload: self.fail(f"Unexpected command: {payload}"),
            state,
        )
        listed = await handler(FakeRequest(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        names = {tool["name"] for tool in listed.payload["result"]["tools"]}
        self.assertIn("architect_task_list", names)
        self.assertIn("architect_task_show", names)

        task_list = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "architect_task_list",
                    "arguments": {},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        payload = json.loads(
            task_list.payload["result"]["content"][0]["text"]
        )
        self.assertEqual([item["id"] for item in payload["tasks"]], [own_task.id])
        self.assertEqual(payload["total"], 1)
        self.assertFalse(payload["truncated"])

        denied = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "architect_task_show",
                    "arguments": {"task": peer_task.id},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        self.assertIn("error", denied.payload)
        self.assertIn("Unknown tool", denied.payload["error"]["message"])

    async def test_class_telemetry_self_scope_filters_unbounded_call_rows(self):
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
        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"telemetry.read": "self"},
            },
        }

        async def fake_handle_command(payload):
            self.assertEqual(payload["cmd"], "architect_mcp_calls")
            return {
                "type": "mcp_calls",
                "calls": [
                    {"id": "call-1", "cell_id": architect.id},
                    {"id": "call-2", "cell_id": engineer.id},
                ],
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "architect_mcp_calls",
                    "arguments": {},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            [item["cell_id"] for item in payload["calls"]],
            [architect.id],
        )

    async def test_class_event_self_scope_filters_child_activity(self):
        state, architect = self._state_with_architect()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"].extend([engineer.id, worker.id])
        child_task = self.state_mod.BoardTask(
            id="task-child",
            task="Child task",
            group="g",
            lane="In Progress",
            assigned_engineer_id=engineer.id,
            agent_id=worker.id,
        )
        state.board_tasks[child_task.id] = child_task
        events = [
            {
                "id": "own-event",
                "kind": "agent_error",
                "timestamp": 2.0,
                "cell_id": architect.id,
                "agent_name": architect.name,
                "group": "g",
                "message": "Own event",
            },
            {
                "id": "child-event",
                "kind": "task_completed",
                "timestamp": 1.0,
                "cell_id": worker.id,
                "agent_name": worker.name,
                "group": "g",
                "task_id": child_task.id,
                "message": "Child event",
            },
        ]
        state.panel_log = type(
            "PanelLog",
            (),
            {"get_recent": lambda _self, _limit: list(reversed(events))},
        )()
        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"event.read": "self"},
            },
        }

        handler = self.mcp_mod.create_mcp_handler(
            lambda payload: self.fail(f"Unexpected command: {payload}"),
            state,
        )
        response = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "architect_events_recent",
                    "arguments": {},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            [item["id"] for item in payload["events"]],
            ["own-event"],
        )
        self.assertFalse(payload["truncated"])

    async def test_class_thinking_self_scope_filters_lists_and_denies_peer_artifact(self):
        state, architect = self._state_with_architect()
        own_note = {
            "id": "note-own",
            "group_name": "g",
            "created_by_kind": "architect",
            "created_by_id": architect.id,
            "title": "Own note",
        }
        peer_note = {
            "id": "note-peer",
            "group_name": "g",
            "created_by_kind": "architect",
            "created_by_id": "architect-2",
            "title": "Peer note",
        }
        notes = {own_note["id"]: own_note, peer_note["id"]: peer_note}
        state.list_scratchpad_notes = lambda **kwargs: list(notes.values())
        state.resolve_scratchpad_note_id = (
            lambda identifier, **kwargs: identifier if identifier in notes else ""
        )
        state.load_scratchpad_note = lambda note_id: notes.get(note_id)
        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"thinking.read": "self"},
            },
        }

        async def unexpected(payload):
            self.fail(f"Unexpected command: {payload}")

        handler = self.mcp_mod.create_mcp_handler(unexpected, state)
        response = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "architect_thinking_scratchpad_list",
                    "arguments": {},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            [item["id"] for item in payload["notes"]],
            [own_note["id"]],
        )

        denied = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "architect_thinking_scratchpad_show",
                    "arguments": {"note_id": peer_note["id"]},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        self.assertIn("error", denied.payload)
        self.assertIn("Unknown tool", denied.payload["error"]["message"])

    async def test_class_planning_self_scope_filters_lists_and_denies_peer_records(self):
        state, architect = self._state_with_architect()
        peer = self.state_mod.AgentCell(
            id="architect-2",
            name="Peer",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[peer.id] = peer
        state.groups["g"].append(peer.id)
        areas = {
            "area-own": {
                "id": "area-own",
                "group_name": "g",
                "owner_kind": "architect",
                "owner_id": architect.id,
                "created_by_kind": "architect",
                "created_by_id": architect.id,
            },
            "area-peer": {
                "id": "area-peer",
                "group_name": "g",
                "owner_kind": "architect",
                "owner_id": peer.id,
                "created_by_kind": "architect",
                "created_by_id": peer.id,
            },
        }
        initiatives = {
            "initiative-own": {
                "id": "initiative-own",
                "group_name": "g",
                "owner_kind": "architect",
                "owner_id": architect.id,
                "created_by_kind": "architect",
                "created_by_id": architect.id,
            },
            "initiative-peer": {
                "id": "initiative-peer",
                "group_name": "g",
                "owner_kind": "architect",
                "owner_id": peer.id,
                "created_by_kind": "architect",
                "created_by_id": peer.id,
            },
        }
        state.list_areas = lambda **kwargs: list(areas.values())
        state.resolve_area_id = (
            lambda identifier, **kwargs: identifier if identifier in areas else ""
        )
        state.load_area = lambda area_id: areas.get(area_id)
        state.area_payload = lambda area_id, **kwargs: dict(areas[area_id])
        state.list_initiatives = lambda **kwargs: list(initiatives.values())
        state.resolve_initiative_id = (
            lambda identifier, **kwargs: (
                identifier if identifier in initiatives else ""
            )
        )
        state.load_initiative = (
            lambda initiative_id: initiatives.get(initiative_id)
        )
        state.initiative_payload = (
            lambda initiative_id, **kwargs: dict(initiatives[initiative_id])
        )
        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {
                    "planning.area.read": "self",
                    "planning.area.write": "self",
                    "planning.initiative.read": "self",
                    "planning.initiative.write": "self",
                    "decision.propose": "self",
                },
            },
        }
        decisions = {
            "decision-peer": {
                "id": "decision-peer",
                "architect_id": peer.id,
                "status": "proposed",
            },
        }
        state.load_decision = lambda decision_id: decisions.get(decision_id)

        async def unexpected(payload):
            self.fail(f"Unexpected command: {payload}")

        handler = self.mcp_mod.create_mcp_handler(unexpected, state)
        for tool_name, result_key, own_id, peer_id, argument in (
            (
                "architect_proposal_area_list",
                "areas",
                "area-own",
                "area-peer",
                "area",
            ),
            (
                "architect_proposal_initiative_list",
                "initiatives",
                "initiative-own",
                "initiative-peer",
                "initiative",
            ),
        ):
            with self.subTest(tool=tool_name):
                response = await handler(FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": tool_name,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {}},
                    },
                    headers={"X-Torque-Cell-Id": architect.id},
                ))
                payload = json.loads(
                    response.payload["result"]["content"][0]["text"]
                )
                self.assertEqual(
                    [item["id"] for item in payload[result_key]],
                    [own_id],
                )

                show_name = tool_name.replace("_list", "_show")
                denied = await handler(FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": show_name,
                        "method": "tools/call",
                        "params": {
                            "name": show_name,
                            "arguments": {argument: peer_id},
                        },
                    },
                    headers={"X-Torque-Cell-Id": architect.id},
                ))
                self.assertIn("error", denied.payload)
                self.assertIn(
                    "Unknown tool",
                    denied.payload["error"]["message"],
                )

        for tool_name, arguments in (
            ("architect_area_update", {"area": "area-peer"}),
            (
                "architect_initiative_update",
                {"initiative": "initiative-peer"},
            ),
            (
                "architect_decision_proposal_update",
                {"id": "decision-peer"},
            ),
        ):
            with self.subTest(tool=tool_name):
                denied = await handler(FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": tool_name,
                        "method": "tools/call",
                        "params": {
                            "name": tool_name,
                            "arguments": arguments,
                        },
                    },
                    headers={"X-Torque-Cell-Id": architect.id},
                ))
                self.assertIn("error", denied.payload)
                self.assertIn(
                    "Unknown tool",
                    denied.payload["error"]["message"],
                )

    async def test_class_journal_self_scope_denies_group_scope_argument(self):
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
        engineer.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "engineer",
                "acl_mode": "allow",
                "capabilities": {"journal.read": "self"},
            },
        }

        async def unexpected(payload):
            self.fail(f"Unexpected command: {payload}")

        handler = self.mcp_mod.create_mcp_handler(unexpected, state)
        denied = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "engineer_journal_read",
                    "arguments": {"scope": "group"},
                },
            },
            headers={"X-Torque-Cell-Id": engineer.id},
        ))
        self.assertIn("error", denied.payload)
        self.assertIn("Unknown tool", denied.payload["error"]["message"])

    async def test_architect_reply_tools_project_one_message_capability_each(self):
        state, architect = self._state_with_architect()
        handler = self.mcp_mod.create_mcp_handler(
            lambda payload: self.fail(f"Unexpected command: {payload}"),
            state,
        )

        async def listed_names():
            response = await handler(FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            ))
            return {
                tool["name"]
                for tool in response.payload["result"]["tools"]
            }

        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"message.architect_peer": "group"},
            },
        }
        peer_names = await listed_names()
        self.assertIn("architect_peer_inbox", peer_names)
        self.assertIn("architect_peer_reply", peer_names)
        self.assertNotIn("architect_engineer_reply", peer_names)

        architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"message.engineer": "children"},
            },
        }
        engineer_names = await listed_names()
        self.assertIn("architect_engineer_reply", engineer_names)
        self.assertNotIn("architect_peer_inbox", engineer_names)
        self.assertNotIn("architect_peer_reply", engineer_names)



if __name__ == "__main__":
    unittest.main()
