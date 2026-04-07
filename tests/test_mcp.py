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

    async def test_dispatch_tool_maps_agent_reports_and_self_dispatch(self):
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
                    "proceed": True,
                    "task": "Implement follow-up",
                    "description": "Derived inline",
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
        self.assertIn("Task derived to self. Proceed with:", text)
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
                    "action": "derive",
                    "message": "Implement follow-up",
                    "description": "Keep the matrix current.",
                    "action_name": "feature/implement",
                    "action_vars": {"TEST_COMMAND": "python3 -m unittest"},
                    "group": "g",
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
