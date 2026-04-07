import importlib
import json
import unittest

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPToolDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_mod = importlib.import_module("loom.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)

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
            "loom_progress",
            {"message": "Running tests"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok"})

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_verify",
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
            "loom_derive",
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
            "loom_memory_publish",
            {
                "entry_type": "decision",
                "content": "Use durable storage.",
                "title": "Storage choice",
                "scope_kind": "task",
                "scope_ref": "task-1",
                "pinned": True,
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["entry"]["id"], "mem-1")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_list",
            {"scope_kind": "group", "scope_ref": "g", "pinned_only": True},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["type"], "memory_entries")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_pin",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_unpin",
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
                },
                {
                    "cmd": "memory_list",
                    "cell_id": "agent-1",
                    "scope_kind": "group",
                    "scope_ref": "g",
                    "pinned_only": True,
                },
                {
                    "cmd": "memory_pin",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
                {
                    "cmd": "memory_unpin",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
            ],
        )

    async def test_create_mcp_handler_lists_tools_and_enforces_agent_header(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        listed = await handler(
            FakeRequest({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        )
        tool_names = [tool["name"] for tool in listed.payload["result"]["tools"]]
        self.assertIn("loom_progress", tool_names)
        self.assertIn("loom_verify", tool_names)
        self.assertIn("loom_memory_publish", tool_names)
        self.assertIn("weaver_board_summary", tool_names)

        missing_header = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "loom_progress", "arguments": {"message": "hi"}},
                }
            )
        )
        self.assertTrue(missing_header.payload["result"]["isError"])
        self.assertIn(
            "X-Loom-Cell-Id header is required",
            missing_header.payload["result"]["content"][0]["text"],
        )

        summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "weaver_board_summary", "arguments": {}},
                }
            )
        )
        self.assertFalse(summary.payload["result"]["isError"])
        parsed = json.loads(summary.payload["result"]["content"][0]["text"])
        self.assertEqual(parsed["group"], "g")
        self.assertEqual(calls, [])
