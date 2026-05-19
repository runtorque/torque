import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPToolDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)

    async def _flush_session_wake_tasks(self):
        tasks = list(getattr(self.mcp_mod, "_SESSION_WAKE_TASKS", set()) or [])
        if tasks:
            await asyncio.gather(*tasks)

    async def test_dispatch_tool_maps_agent_reports_and_derive_returns_json(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["action"] == "derive":
                return {
                    "type": "ok",
                    "task_id": "task-2",
                    "agent_id": "agent-1",
                }
            return {"type": "ok"}

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_progress",
            {"message": "Running tests"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok"})

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_verify",
            {
                "state": "pending",
                "mode": "deploy",
                "notes": "Need manual smoke after deploy",
                "tests_run": "python3 -m unittest",
                "manual_smoke_done": True,
                "deploy_needed": True,
                "deploy_attempted": True,
                "human_validation_pending": "Confirm dashboard loads",
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok"})

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_derive",
            {
                "description": "Implement follow-up",
                "context": "Keep the matrix current.",
                "action": "feature/implement",
                "action_vars": {"TEST_COMMAND": "python3 -m unittest"},
                "group": "g",
            },
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            json.loads(text),
            {"type": "ok", "task_id": "task-2", "agent_id": "agent-1"},
        )
        self.assertEqual(
            calls,
            [
                {
                    "cmd": "ai_report",
                    "cell_id": "agent-1",
                    "action": "progress",
                    "message": "Running tests",
                },
                {
                    "cmd": "ai_report",
                    "cell_id": "agent-1",
                    "action": "verify",
                    "verification_state": "pending",
                    "verification_mode": "deploy",
                    "verification_notes": "Need manual smoke after deploy",
                    "tests_run": "python3 -m unittest",
                    "manual_smoke_done": True,
                    "deploy_needed": True,
                    "deploy_attempted": True,
                    "human_validation_pending": "Confirm dashboard loads",
                },
                {
                    "cmd": "ai_report",
                    "cell_id": "agent-1",
                    "action": "derive",
                    "message": "Implement follow-up",
                    "description": "Keep the matrix current.",
                    "action_name": "feature/implement",
                    "action_vars": {"TEST_COMMAND": "python3 -m unittest"},
                    "group": "g",
                },
            ],
        )

    async def test_dispatch_tool_forwards_task_artifact_uploads(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {
                "type": "task_artifact_uploaded",
                "task_id": "task-1",
                "artifact": {"filename": "report.txt"},
            }

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_task_upload_artifact",
            {
                "filename": "report.txt",
                "content_text": "hello",
                "artifact_type": "generated_doc",
                "summary": "test upload",
            },
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["artifact"]["filename"], "report.txt")
        self.assertEqual(
            calls,
            [{
                "cmd": "task_upload_artifact",
                "cell_id": "agent-1",
                "filename": "report.txt",
                "content_text": "hello",
                "artifact_type": "generated_doc",
                "summary": "test upload",
            }],
        )

    async def test_torque_reply_forwards_optional_task_id(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok", "task_id": "TORQUE:1:2"}

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_reply",
            {"task": "TORQUE:1:2", "message": "Rebased successfully"},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok", "task_id": "TORQUE:1:2"})
        self.assertEqual(
            calls,
            [{
                "cmd": "ai_report",
                "cell_id": "agent-1",
                "action": "reply",
                "task_id": "TORQUE:1:2",
                "message": "Rebased successfully",
            }],
        )

    async def test_dispatch_tool_maps_memory_commands(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "memory_list":
                return {"type": "memory_entries", "entries": []}
            return {"type": "memory_entry", "entry": {"id": "mem-1"}}

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_publish",
            {
                "entry_type": "decision",
                "content": "Use durable storage.",
                "title": "Storage choice",
                "scope_kind": "task",
                "scope_ref": "task-1",
                "pinned": True,
                "retention_kind": "durable",
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["entry"]["id"], "mem-1")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_list",
            {
                "scope_kind": "group",
                "scope_ref": "g",
                "pinned_only": True,
                "linked_target_kind": "task",
                "linked_target_ref": "task-1",
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["type"], "memory_entries")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_read",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_pin",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_link",
            {"entry_id": "mem-1", "target_kind": "task", "target_ref": "task-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_unpin",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        self.assertEqual(
            calls,
            [
                {
                    "cmd": "memory_publish",
                    "cell_id": "agent-1",
                    "entry_type": "decision",
                    "content": "Use durable storage.",
                    "title": "Storage choice",
                    "scope_kind": "task",
                    "scope_ref": "task-1",
                    "pinned": True,
                    "retention_kind": "durable",
                },
                {
                    "cmd": "memory_list",
                    "cell_id": "agent-1",
                    "scope_kind": "group",
                    "scope_ref": "g",
                    "pinned_only": True,
                    "linked_target_kind": "task",
                    "linked_target_ref": "task-1",
                },
                {
                    "cmd": "memory_read",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
                {
                    "cmd": "memory_pin",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
                {
                    "cmd": "memory_link",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                    "target_kind": "task",
                    "target_ref": "task-1",
                },
                {
                    "cmd": "memory_unpin",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
            ],
        )

    async def test_create_mcp_handler_exposes_only_current_mcp_surfaces(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        hired_engineer = self.state_mod.AgentCell(
            id="engineer-hired",
            name="Hired Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.agents[hired_engineer.id] = hired_engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [
            engineer.id,
            architect.id,
            engineer.id,
            hired_engineer.id,
            worker.id,
        ]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        listed_missing_header = await handler(
            FakeRequest({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        )
        tool_names = [
            tool["name"]
            for tool in listed_missing_header.payload["result"]["tools"]
        ]
        self.assertIn("torque_progress", tool_names)
        self.assertIn("torque_verify", tool_names)
        self.assertIn("torque_task_upload_artifact", tool_names)
        self.assertIn("torque_memory_publish", tool_names)
        self.assertIn("torque_memory_read", tool_names)
        self.assertIn("torque_memory_link", tool_names)
        self.assertNotIn("engineer_board_summary", tool_names)
        self.assertNotIn("engineer_board_summary", tool_names)
        self.assertNotIn("architect_board_summary", tool_names)
        self.assertNotIn("architect_tool_search", tool_names)

        listed_worker = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": worker.id},
            )
        )
        worker_tool_names = [
            tool["name"] for tool in listed_worker.payload["result"]["tools"]
        ]
        self.assertIn("torque_progress", worker_tool_names)
        self.assertNotIn("engineer_board_summary", worker_tool_names)
        self.assertNotIn("engineer_board_summary", worker_tool_names)
        self.assertNotIn("architect_board_summary", worker_tool_names)
        self.assertNotIn("architect_tool_search", worker_tool_names)

        listed_architect = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        architect_tool_names = [
            tool["name"] for tool in listed_architect.payload["result"]["tools"]
        ]
        self.assertIn("architect_tool_search", architect_tool_names)
        self.assertIn("architect_board_summary", architect_tool_names)
        self.assertIn("architect_events_recent", architect_tool_names)
        self.assertIn("architect_deploy_state", architect_tool_names)
        self.assertNotIn("architect_get_architect_settings", architect_tool_names)
        self.assertNotIn("architect_mcp_calls", architect_tool_names)
        self.assertNotIn("architect_engineer_dismiss", architect_tool_names)
        self.assertNotIn("architect_engineer_rehire", architect_tool_names)
        self.assertNotIn("architect_update_architect_settings", architect_tool_names)
        self.assertNotIn("architect_workspace_overview", architect_tool_names)
        self.assertIn("architect_task_list", architect_tool_names)
        self.assertIn("architect_task_chain", architect_tool_names)
        self.assertIn("architect_task_update", architect_tool_names)
        self.assertIn("architect_task_move", architect_tool_names)
        self.assertIn("architect_decision_create", architect_tool_names)
        self.assertIn("architect_engineer_hire", architect_tool_names)
        self.assertIn("architect_engineer_message", architect_tool_names)
        self.assertIn("architect_engineer_journal_read", architect_tool_names)
        self.assertIn("architect_engineer_pending_question", architect_tool_names)
        self.assertIn("architect_ask", architect_tool_names)
        self.assertNotIn("engineer_board_summary", architect_tool_names)
        self.assertNotIn("engineer_board_summary", architect_tool_names)

        listed_engineer = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 121, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        engineer_tool_names = [
            tool["name"] for tool in listed_engineer.payload["result"]["tools"]
        ]
        self.assertIn("engineer_tool_search", engineer_tool_names)
        self.assertIn("engineer_board_summary", engineer_tool_names)
        self.assertIn("engineer_task_verify", engineer_tool_names)
        self.assertNotIn("engineer_message_architect", engineer_tool_names)
        self.assertNotIn("engineer_reply", engineer_tool_names)
        self.assertNotIn("engineer_task_reassign", engineer_tool_names)
        self.assertNotIn("engineer_task_upload_artifact", engineer_tool_names)
        self.assertNotIn("engineer_launch_settings", engineer_tool_names)
        self.assertNotIn("engineer_specializations_list", engineer_tool_names)
        self.assertNotIn("engineer_mcp_calls", engineer_tool_names)
        self.assertNotIn("architect_workspace_overview", engineer_tool_names)
        self.assertNotIn("architect_board_summary", engineer_tool_names)

        listed_hired_engineer = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 122, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": hired_engineer.id},
            )
        )
        hired_engineer_tool_names = [
            tool["name"]
            for tool in listed_hired_engineer.payload["result"]["tools"]
        ]
        self.assertIn("engineer_message_architect", hired_engineer_tool_names)
        self.assertIn("engineer_reply", hired_engineer_tool_names)

        missing_header = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "torque_progress", "arguments": {"message": "hi"}},
                }
            )
        )
        self.assertTrue(missing_header.payload["result"]["isError"])
        self.assertIn(
            "X-Torque-Cell-Id header is required",
            missing_header.payload["result"]["content"][0]["text"],
        )

        removed_alias = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "engineer_agents_list", "arguments": {}},
                }
            )
        )
        self.assertTrue(removed_alias.payload["result"]["isError"])
        self.assertIn(
            "X-Torque-Cell-Id header is required",
            removed_alias.payload["result"]["content"][0]["text"],
        )

        denied_architect_summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {"name": "architect_board_summary", "arguments": {}},
                },
                headers={"X-Torque-Cell-Id": worker.id},
            )
        )
        self.assertTrue(denied_architect_summary.payload["result"]["isError"])
        self.assertIn(
            "architect tools are only available inside a Torque-managed architect session",
            denied_architect_summary.payload["result"]["content"][0]["text"],
        )

        summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "engineer_board_summary", "arguments": {}},
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(summary.payload["result"]["isError"])
        parsed = json.loads(summary.payload["result"]["content"][0]["text"])
        self.assertEqual(parsed["group"], "g")
        self.assertEqual(calls, [])

    async def test_engineer_board_summary_includes_caller_dispatch_shapes(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer One",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        other_engineer = self.state_mod.AgentCell(
            id="engineer-2",
            name="Engineer Two",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.agents[other_engineer.id] = other_engineer
        state.groups["g"] = [engineer.id, other_engineer.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.record_engineer_dispatch_shape(
            engineer.id,
            group="g",
            source_tool="engineer_task_dispatch",
            shape="serial",
            task_ids=["task-serial"],
            hintable=True,
        )
        state.record_engineer_dispatch_shape(
            engineer.id,
            group="g",
            source_tool="engineer_batch_dispatch",
            shape="batch",
            task_ids=["task-batch-a", "task-batch-b"],
            task_count=2,
        )
        state.record_engineer_dispatch_shape(
            engineer.id,
            group="g",
            source_tool="torque_derive",
            shape="warm_cluster",
            task_ids=["task-derive"],
        )
        state.record_engineer_dispatch_shape(
            other_engineer.id,
            group="g",
            source_tool="engineer_task_dispatch",
            shape="warm_cluster",
            task_ids=["other-task"],
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_board_summary",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        parsed = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            parsed["dispatch_shapes"]["counts"],
            {"serial": 1, "batch": 1, "warm_cluster": 0},
        )
        self.assertEqual(parsed["dispatch_shapes"]["hintable_serial"], 1)
        self.assertEqual(parsed["dispatch_shapes"]["derives_total"], 1)
        self.assertEqual(
            parsed["dispatch_shapes"]["derives_by_shape"],
            {"serial": 0, "batch": 0, "warm_cluster": 1},
        )

    def _parse_functions_block(self, text):
        self.assertTrue(text.startswith("<functions>"), text)
        self.assertTrue(text.endswith("</functions>"), text)
        inner = text[len("<functions>"):-len("</functions>")]
        return json.loads(inner)

    async def test_engineer_dispatch_provider_override_schema_tracks_setting(self):
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

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        async def props_for(tool_name):
            listed = await handler(
                FakeRequest(
                    {"jsonrpc": "2.0", "id": tool_name, "method": "tools/list"},
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            tools = listed.payload["result"]["tools"]
            tool = next(t for t in tools if t["name"] == tool_name)
            return tool["inputSchema"]["properties"]

        self.assertIn("agent_type", await props_for("engineer_task_dispatch"))
        self.assertIn("provider", await props_for("engineer_batch_dispatch"))
        agent_message_props = await props_for("engineer_agent_message")
        self.assertIn("reply_required", agent_message_props)
        self.assertTrue(agent_message_props["reply_required"]["default"])

        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=False,
        )

        self.assertNotIn("agent_type", await props_for("engineer_task_dispatch"))
        self.assertNotIn("provider", await props_for("engineer_batch_dispatch"))

        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=True,
        )

        self.assertIn("agent_type", await props_for("engineer_task_dispatch"))
        self.assertIn("provider", await props_for("engineer_batch_dispatch"))

    async def test_engineer_batch_dispatch_schema_frames_parallel_waves(self):
        tool = next(
            t for t in self.mcp_mod.ENGINEER_TOOLS
            if t["name"] == "engineer_batch_dispatch"
        )
        description = tool["description"]
        props = tool["inputSchema"]["properties"]

        self.assertIn("Boot N workers simultaneously", description)
        self.assertIn(
            "parallel velocity > review-boundary granularity",
            description,
        )
        self.assertIn("this batch's engineer-group", description)
        self.assertIn("warm-cluster queue", description)
        self.assertIn("prefer serial `engineer_task_dispatch`", description)
        self.assertIn("implement→review→fix checkpoints", description)
        agent_group_desc = (
            props["tasks"]["items"]["properties"]["agent_group"]["description"]
        )
        self.assertIn("warm-cluster same-agent affinity key", agent_group_desc)
        self.assertIn("not a capacity", agent_group_desc)
        max_desc = props["max_concurrent"]["description"]
        self.assertIn("Per-batch active-worker cap", max_desc)
        self.assertIn("engineer group's currently active", max_desc)
        self.assertIn("not an agent_group", max_desc)

    async def test_engineer_batch_dispatch_deferral_reports_group_and_refreshes_cap(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            created_by_engineer_id=engineer.id,
            owner_engineer_id=engineer.id,
            current_task_id="active",
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [engineer.id, worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.board_add_task(
            "Active",
            "g",
            lane="In Progress",
            id="active",
            agent_id=worker.id,
        )
        queued = state.board_add_task("Queued", "g", lane="Backlog", id="queued")

        async def fake_handle_command(payload):
            self.fail(f"Unexpected dispatch while at cap: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        async def call_batch(max_concurrent):
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": max_concurrent,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_batch_dispatch",
                            "arguments": {
                                "tasks": [{"task": queued.id}],
                                "max_concurrent": max_concurrent,
                            },
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            self.assertFalse(response.payload["result"]["isError"])
            return json.loads(response.payload["result"]["content"][0]["text"])

        deferred = await call_batch(1)
        item = deferred["results"][0]
        self.assertEqual(item["status"], "deferred")
        self.assertEqual(item["reason"], "max_concurrent_reached")
        self.assertEqual(item["engineer_group"], "g")
        self.assertEqual(item["active_count"], 1)
        self.assertEqual(item["cap"], 1)
        self.assertIn("engineer group 'g'", item["message"])
        self.assertIn("1/1", item["message"])

        raised = await call_batch(3)
        item = raised["results"][0]
        self.assertEqual(item["status"], "cap_raised")
        self.assertEqual(item["previous_max_concurrent"], 1)
        self.assertEqual(item["max_concurrent"], 3)
        self.assertEqual(state.auto_dispatch_queues["g"][0].max_concurrent, 3)

        lowered = await call_batch(2)
        item = lowered["results"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["reason"], "already_queued")
        self.assertEqual(item["current_max_concurrent"], 3)
        self.assertEqual(item["requested_max_concurrent"], 2)
        self.assertEqual(state.auto_dispatch_queues["g"][0].max_concurrent, 3)

    async def test_engineer_batch_dispatch_deferred_entry_auto_promotes_after_slot_frees(self):
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
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        first = state.board_add_task("First", "g", lane="Backlog", id="first")
        second = state.board_add_task("Second", "g", lane="Backlog", id="second")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            task = state.board_tasks[payload["id"]]
            agent_id = f"worker-{len(calls)}"
            agent = self.state_mod.AgentCell(
                id=agent_id,
                name=f"Worker {len(calls)}",
                group="g",
                cell_type="agent",
                created_by_engineer_id=engineer.id,
                owner_engineer_id=engineer.id,
                current_task_id=task.id,
            )
            state.agents[agent.id] = agent
            state.groups["g"].append(agent.id)
            task.agent_id = agent.id
            task.lane = "In Progress"
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_batch_dispatch",
                        "arguments": {
                            "tasks": [{"task": first.id}, {"task": second.id}],
                            "max_concurrent": 1,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(response.payload["result"]["isError"])
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["dispatched", "deferred"],
        )
        self.assertEqual(state.board_tasks[second.id].lane, "Backlog")
        self.assertEqual(state.auto_dispatch_queues["g"][0].task_id, second.id)

        state.agents["worker-1"].current_task_id = ""
        state.board_tasks[first.id].lane = "Done"
        dispatch_mod = importlib.reload(
            importlib.import_module("torque.server_dispatch")
        )
        panel_events = []
        dispatched = await dispatch_mod._pump_auto_dispatch_queue(
            state,
            fake_handle_command,
            lambda *args, **kwargs: panel_events.append((args, kwargs)),
            group="g",
        )

        self.assertEqual(dispatched[0]["task_id"], second.id)
        self.assertEqual(state.board_tasks[second.id].lane, "In Progress")
        self.assertNotIn("g", state.auto_dispatch_queues)
        self.assertEqual(len(calls), 2)
        self.assertEqual(panel_events[0][0][0], "task_auto_dispatched")

    async def test_engineer_batch_dispatch_provider_reaches_dispatch_payload(self):
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
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        task = state.board_add_task("Batch provider worker", "g", lane="Backlog")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_batch_dispatch",
                        "arguments": {
                            "tasks": [{"task": task.id}],
                            "provider": "claude-code",
                            "max_concurrent": 1,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertEqual(calls[0]["agent_type"], "claude-code")

    async def test_engineer_task_dispatch_provider_override_falls_back_when_disabled(self):
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
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=False,
        )
        task = state.board_add_task("Stale provider override", "g", lane="Backlog")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        with self.assertLogs("torque", level="WARNING") as logs:
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_task_dispatch",
                            "arguments": {
                                "task": task.id,
                                "agent_type": "claude-code",
                            },
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertNotIn("agent_type", calls[0])
        self.assertIn("falling back to group default", "\n".join(logs.output))

    async def test_engineer_task_dispatch_records_simple_serial_hintable_shape(self):
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
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        task = state.board_add_task("Simple serial dispatch", "g", lane="Backlog")

        async def fake_handle_command(payload):
            return {
                "type": "ok",
                "task_id": payload["id"],
                "agent_id": "worker-1",
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_task_dispatch",
                        "arguments": {"task": task.id},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        events = state.engineer_dispatch_shape_events(engineer.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_tool"], "engineer_task_dispatch")
        self.assertEqual(events[0]["shape"], "serial")
        self.assertTrue(events[0]["hintable"])
        self.assertEqual(events[0]["task_ids"], [task.id])
        self.assertFalse(events[0]["metadata"]["has_launch_overrides"])

    async def test_engineer_task_dispatch_records_overrides_and_existing_agent_as_not_hintable(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            owner_engineer_id=engineer.id,
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [engineer.id, worker.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        override_task = state.board_add_task("Override dispatch", "g", lane="Backlog")
        warm_task = state.board_add_task("Existing agent dispatch", "g", lane="Backlog")

        async def fake_handle_command(payload):
            return {
                "type": "ok",
                "task_id": payload["id"],
                "agent_id": payload.get("agent_id", "worker-created"),
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        for request_id, arguments in (
            (1, {"task": override_task.id, "name": "custom-worker"}),
            (2, {"task": warm_task.id, "agent": worker.id}),
        ):
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_task_dispatch",
                            "arguments": arguments,
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            self.assertFalse(response.payload["result"]["isError"])

        events = state.engineer_dispatch_shape_events(engineer.id)
        self.assertEqual([event["shape"] for event in events],
                         ["warm_cluster", "serial"])
        self.assertFalse(events[0]["hintable"])
        self.assertTrue(events[0]["metadata"]["existing_agent"])
        self.assertFalse(events[1]["hintable"])
        self.assertTrue(events[1]["metadata"]["has_launch_overrides"])

    async def test_engineer_task_dispatch_skips_dispatch_action_missing_metric(self):
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
        task = state.board_add_task("Missing action", "g", lane="Backlog")

        async def fake_handle_command(_payload):
            return {
                "type": "dispatch_action_missing",
                "action_name": "feature/review",
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_task_dispatch",
                        "arguments": {"task": task.id},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertEqual(state.engineer_dispatch_shape_events(engineer.id), [])

    async def test_engineer_batch_dispatch_records_batch_and_warm_cluster_shapes(self):
        async def run_batch(agent_groups):
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
            state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
            tasks = [
                state.board_add_task(f"Batch {idx}", "g", lane="Backlog")
                for idx in range(len(agent_groups))
            ]
            calls = []

            async def fake_handle_command(payload):
                calls.append(dict(payload))
                task = state.board_tasks[payload["id"]]
                agent_id = payload.get("agent_id") or f"worker-{len(calls)}"
                if agent_id not in state.agents:
                    state.agents[agent_id] = self.state_mod.AgentCell(
                        id=agent_id,
                        name=agent_id,
                        group="g",
                        cell_type="agent",
                        owner_engineer_id=engineer.id,
                        current_task_id=task.id,
                    )
                    state.groups["g"].append(agent_id)
                task.agent_id = agent_id
                task.lane = "In Progress"
                return {
                    "type": "ok",
                    "task_id": task.id,
                    "agent_id": agent_id,
                }

            handler = self.mcp_mod.create_mcp_handler(
                fake_handle_command,
                state,
            )
            entries = []
            for task, agent_group in zip(tasks, agent_groups):
                entry = {"task": task.id}
                if agent_group:
                    entry["agent_group"] = agent_group
                entries.append(entry)
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_batch_dispatch",
                            "arguments": {
                                "tasks": entries,
                                "max_concurrent": 5,
                            },
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            self.assertFalse(response.payload["result"]["isError"])
            return state.engineer_dispatch_shape_events(engineer.id)[0]

        batch_event = await run_batch(["", ""])
        self.assertEqual(batch_event["shape"], "batch")
        self.assertFalse(batch_event["hintable"])
        self.assertEqual(batch_event["task_count"], 2)
        self.assertEqual(batch_event["metadata"]["independent_entry_count"], 2)
        self.assertEqual(batch_event["metadata"]["clustered_entry_count"], 0)

        warm_event = await run_batch(["cluster-a", "cluster-a", ""])
        self.assertEqual(warm_event["shape"], "warm_cluster")
        self.assertEqual(warm_event["task_count"], 3)
        self.assertEqual(warm_event["metadata"]["clustered_entry_count"], 2)
        self.assertEqual(warm_event["metadata"]["independent_entry_count"], 1)

    async def test_engineer_architect_message_tools_require_hiring_architect(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        user_owned_engineer = self.state_mod.AgentCell(
            id="engineer-user",
            name="User Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        hired_engineer = self.state_mod.AgentCell(
            id="engineer-hired",
            name="Hired Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        legacy_empty_engineer = self.state_mod.AgentCell(
            id="engineer-empty",
            name="Legacy Empty Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id="",
        )
        for cell in (
            architect,
            user_owned_engineer,
            hired_engineer,
            legacy_empty_engineer,
        ):
            state.agents[cell.id] = cell
        state.groups["g"] = [
            architect.id,
            user_owned_engineer.id,
            hired_engineer.id,
            legacy_empty_engineer.id,
        ]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        async def listed_names(cell_id):
            response = await handler(
                FakeRequest(
                    {"jsonrpc": "2.0", "id": cell_id, "method": "tools/list"},
                    headers={"X-Torque-Cell-Id": cell_id},
                )
            )
            return {tool["name"] for tool in response.payload["result"]["tools"]}

        for cell in (user_owned_engineer, legacy_empty_engineer):
            names = await listed_names(cell.id)
            self.assertNotIn("engineer_message_architect", names)
            self.assertNotIn("engineer_reply", names)

            search = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": f"search-{cell.id}",
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_tool_search",
                            "arguments": {"query": "engineer_message_architect"},
                        },
                    },
                    headers={"X-Torque-Cell-Id": cell.id},
                )
            )
            payload = self._parse_functions_block(
                search.payload["result"]["content"][0]["text"]
            )
            self.assertEqual(payload["tools"], [])

            for tool_name in ("engineer_message_architect", "engineer_reply"):
                response = await handler(
                    FakeRequest(
                        {
                            "jsonrpc": "2.0",
                            "id": f"{cell.id}-{tool_name}",
                            "method": "tools/call",
                            "params": {"name": tool_name, "arguments": {}},
                        },
                        headers={"X-Torque-Cell-Id": cell.id},
                    )
                )
                self.assertIn("error", response.payload)
                self.assertIn("Unknown tool", response.payload["error"]["message"])

        hired_names = await listed_names(hired_engineer.id)
        self.assertIn("engineer_message_architect", hired_names)
        self.assertIn("engineer_reply", hired_names)

    async def test_tool_search_select_and_keyword_return_functions_block(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.groups["g"] = [engineer.id, architect.id]

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        exact = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_tool_search",
                        "arguments": {
                            "query": "select:engineer_task_upload_artifact",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(exact.payload["result"]["isError"])
        exact_payload = self._parse_functions_block(
            exact.payload["result"]["content"][0]["text"]
        )
        self.assertEqual(
            [tool["name"] for tool in exact_payload["tools"]],
            ["engineer_task_upload_artifact"],
        )
        self.assertNotIn("deferred", exact_payload["tools"][0])

        keyword = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_tool_search",
                        "arguments": {
                            "query": "artifact upload",
                            "max_results": 3,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        keyword_payload = self._parse_functions_block(
            keyword.payload["result"]["content"][0]["text"]
        )
        self.assertIn(
            "engineer_task_upload_artifact",
            [tool["name"] for tool in keyword_payload["tools"]],
        )

        architect_exact = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_tool_search",
                        "arguments": {
                            "query": "select:architect_mcp_calls",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        architect_payload = self._parse_functions_block(
            architect_exact.payload["result"]["content"][0]["text"]
        )
        self.assertEqual(
            [tool["name"] for tool in architect_payload["tools"]],
            ["architect_mcp_calls"],
        )

    async def test_deferred_tools_remain_callable_after_lazy_registration(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.groups["g"] = [engineer.id, architect.id]
        state.update_architect_settings("g", architect_provider="codex")

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        architect_settings = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_get_architect_settings",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertFalse(architect_settings.payload["result"]["isError"])
        settings_payload = json.loads(
            architect_settings.payload["result"]["content"][0]["text"]
        )
        self.assertEqual(settings_payload["settings"]["architect_provider"], "codex")

        engineer_show = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_specializations_list",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(engineer_show.payload["result"]["isError"])

    async def test_removed_architect_tools_are_not_registered_or_callable(self):
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

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {
            tool["name"] for tool in listed.payload["result"]["tools"]
        }
        removed_names = [
            "architect_" + "update_architect_settings",
            "architect_" + "workspace_overview",
        ]
        for tool_name in removed_names:
            self.assertNotIn(tool_name, tool_names)
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
            self.assertIn("error", response.payload)
            self.assertIn("Unknown tool", response.payload["error"]["message"])

    def test_removed_architect_mcp_tools_have_no_application_callers(self):
        removed_names = [
            "architect_" + "update_architect_settings",
            "architect_" + "workspace_overview",
        ]
        root = Path(__file__).resolve().parents[1]
        paths = list((root / "torque").glob("*.py")) + [root / "bin" / "torque"]
        for path in paths:
            text = path.read_text()
            for tool_name in removed_names:
                self.assertNotIn(tool_name, text, str(path))

    def test_torque_ask_tool_description_marks_it_as_blocking(self):
        ask_tool = next(
            tool for tool in self.mcp_mod.TOOLS
            if tool["name"] == "torque_ask"
        )
        reply_tool = next(
            tool for tool in self.mcp_mod.TOOLS
            if tool["name"] == "torque_reply"
        )

        self.assertIn("blocking human decision or approval", ask_tool["description"])
        self.assertIn("Do not use this for status updates", ask_tool["description"])
        self.assertIn("include the task id", reply_tool["description"])
        self.assertIn("task", reply_tool["inputSchema"]["properties"])

    async def test_torque_context_includes_combined_task_artifacts(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        task = state.board_add_task(
            "Review artifact uploads",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id=cell.id,
            attachments=[{"path": "/tmp/task-1/image.png", "filename": "image.png"}],
            artifacts=[{"type": "log", "title": "pytest", "path": "/tmp/task-1/pytest.log"}],
        )
        self.assertIsNotNone(task)

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_context",
            {},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        payload = json.loads(text)
        self.assertEqual(len(payload["tasks"]["task-1"]["task_artifacts"]), 2)
        self.assertEqual(payload["tasks"]["task-1"]["task_artifacts"][0]["url"], "/attachments/task-1/image.png")

    async def test_torque_context_includes_upstream_artifacts_for_derived_tasks(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        parent = self.state_mod.BoardTask(
            id="task-parent",
            task="Research auth patch",
            group="g",
            lane="Done",
            artifacts=[{
                "type": "generated_doc",
                "title": "Implementation plan",
                "path": "/tmp/task-parent/plan.md",
            }],
        )
        child = self.state_mod.BoardTask(
            id="task-child",
            task="Implement auth patch",
            group="g",
            lane="In Progress",
            agent_id=cell.id,
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        state.board_tasks[parent.id] = parent
        state.board_tasks[child.id] = child

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_context",
            {},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        payload = json.loads(text)
        upstream = payload["tasks"]["task-child"]["upstream_artifacts"]
        self.assertEqual(len(upstream), 1)
        self.assertEqual(upstream[0]["source_task_id"], "task-parent")
        self.assertEqual(upstream[0]["source_task_label"], "Research auth patch")
        self.assertEqual(upstream[0]["url"], "/attachments/task-parent/plan.md")

    async def test_architect_session_wake_emits_once_per_mcp_session_in_background(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "torque_context", "arguments": {}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)), \
                    mock.patch("time.time", return_value=1000.0):
                payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=architect.id,
                    handle_command=fake_handle_command,
                    state=state,
                    mcp_session_id="mcp-session-1",
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["result"]["isError"])
                self.assertEqual(state.architect_journal_read(architect.id, limit=10), [])
                await self._flush_session_wake_tasks()
                entries = state.architect_journal_read(architect.id, limit=10)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["type"], "observation")
            self.assertEqual(
                entries[0]["entry"],
                "Session wake — previous checkpoint none recorded, architect id arch-1, group g.",
            )

            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)), \
                    mock.patch("time.time", return_value=1010.0):
                payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=architect.id,
                    handle_command=fake_handle_command,
                    state=state,
                    mcp_session_id="mcp-session-1",
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["result"]["isError"])
                await self._flush_session_wake_tasks()
                entries = state.architect_journal_read(architect.id, limit=10)

            self.assertEqual(len(entries), 1)

    async def test_architect_manual_wake_entry_dedupes_auto_wake(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "architect_journal",
                "arguments": {
                    "type": "observation",
                    "entry": "Session wake 2026-04-22",
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)), \
                    mock.patch("time.time", return_value=2000.0):
                payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=architect.id,
                    handle_command=fake_handle_command,
                    state=state,
                    mcp_session_id="mcp-session-manual",
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["result"]["isError"])
                await self._flush_session_wake_tasks()
                entries = state.architect_journal_read(architect.id, limit=10)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["entry"], "Session wake 2026-04-22")

    async def test_architect_journal_gates_system_generated_entry_types(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]

        architect_journal = next(
            tool for tool in self.mcp_mod.ARCHITECT_TOOLS
            if tool["name"] == "architect_journal"
        )
        self.assertEqual(
            architect_journal["inputSchema"]["properties"]["type"]["enum"],
            ["decision", "observation", "checkpoint", "plan"],
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        async def call(entry_type, idx):
            body = {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": "architect_journal",
                    "arguments": {
                        "type": entry_type,
                        "entry": f"{entry_type} entry",
                    },
                },
            }
            return await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=architect.id,
                handle_command=fake_handle_command,
                state=state,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)):
                for idx, entry_type in enumerate(
                    ["decision", "observation", "checkpoint", "plan"],
                    start=1,
                ):
                    payload, status = await call(entry_type, idx)
                    self.assertEqual(status, 200)
                    self.assertFalse(payload["result"]["isError"])
                    result = json.loads(
                        payload["result"]["content"][0]["text"]
                    )
                    self.assertEqual(result["type"], entry_type)

                for idx, entry_type in enumerate(
                    ["note_dismissed", "qa"],
                    start=10,
                ):
                    payload, status = await call(entry_type, idx)
                    self.assertEqual(status, 200)
                    self.assertTrue(payload["result"]["isError"])
                    self.assertEqual(
                        payload["result"]["content"][0]["text"],
                        (
                            "type must be one of: decision, observation, "
                            "checkpoint, plan"
                        ),
                    )

                entries = state.architect_journal_read(architect.id, limit=10)

        self.assertEqual(len(entries), 4)
        self.assertCountEqual(
            [entry["type"] for entry in entries],
            ["decision", "observation", "checkpoint", "plan"],
        )

    async def test_engineer_session_wake_includes_identity_and_repeats_for_new_session(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Builder",
            slug="builder",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        journal_entries = []

        def fake_journal_append(group, entry_type, entry, author_cell_id=""):
            record = {
                "id": len(journal_entries) + 1,
                "group": group,
                "timestamp": self.mcp_mod.time.time(),
                "type": entry_type,
                "entry": entry,
                "author_cell_id": author_cell_id,
            }
            journal_entries.insert(0, record)
            return record

        def fake_journal_read(group, limit=20, entry_type="", author_cell_id=""):
            filtered = [
                dict(item)
                for item in journal_entries
                if item["group"] == group
                and (not entry_type or item["type"] == entry_type)
                and (
                    not author_cell_id
                    or item.get("author_cell_id", "") == author_cell_id
                )
            ]
            return filtered[:limit]

        state.journal_append = fake_journal_append
        state.journal_read = fake_journal_read

        async def fake_handle_command(payload):
            if payload.get("cmd") == "engineer_journal_append":
                return fake_journal_append(
                    payload["group"],
                    payload["entry_type"],
                    payload["entry"],
                    author_cell_id=payload.get("author_cell_id", ""),
                )
            self.fail(f"Unexpected handle_command call: {payload}")

        with mock.patch("time.time", return_value=100.0):
            state.journal_append(
                "g",
                "checkpoint",
                "Prior checkpoint",
                author_cell_id=engineer.id,
            )

        body = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "engineer_session_map", "arguments": {}},
        }

        with mock.patch("time.time", return_value=4000.0):
            payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=engineer.id,
                handle_command=fake_handle_command,
                state=state,
                mcp_session_id="eng-session-1",
            )
            self.assertEqual(status, 200)
            self.assertFalse(payload["result"]["isError"])
            await self._flush_session_wake_tasks()

        entries = state.journal_read("g", limit=10, author_cell_id=engineer.id)
        wake_entries = [
            entry for entry in entries
            if str(entry.get("entry", "")).startswith("Session wake —")
        ]
        self.assertEqual(len(wake_entries), 1)
        self.assertIn("engineer id eng-1", wake_entries[0]["entry"])
        self.assertIn("name Builder", wake_entries[0]["entry"])
        self.assertIn("slug builder", wake_entries[0]["entry"])
        self.assertIn("group g.", wake_entries[0]["entry"])
        self.assertIn("previous checkpoint 1970-01-01 00:01 UTC", wake_entries[0]["entry"])
        self.assertIn("(1h ago)", wake_entries[0]["entry"])

        with mock.patch("time.time", return_value=8000.0):
            payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=engineer.id,
                handle_command=fake_handle_command,
                state=state,
                mcp_session_id="eng-session-2",
            )
            self.assertEqual(status, 200)
            self.assertFalse(payload["result"]["isError"])
            await self._flush_session_wake_tasks()

        wake_entries = [
            entry for entry in state.journal_read("g", limit=10, author_cell_id=engineer.id)
            if str(entry.get("entry", "")).startswith("Session wake —")
        ]
        self.assertEqual(len(wake_entries), 2)

    async def test_worker_session_does_not_auto_journal_session_wake(self):
        state = self.state_mod.MatrixState()
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[worker.id] = worker
        state.groups["g"] = [worker.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "torque_context", "arguments": {}},
        }

        with mock.patch("time.time", return_value=9000.0):
            payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=worker.id,
                handle_command=fake_handle_command,
                state=state,
                mcp_session_id="worker-session-1",
            )
            self.assertEqual(status, 200)
            self.assertFalse(payload["result"]["isError"])
            await self._flush_session_wake_tasks()

        self.assertEqual(state.journal_read("g", limit=10), [])
