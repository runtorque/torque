import importlib
import json
import unittest

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPHelpDocsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)

    async def test_worker_help_tools_are_listed_and_dispatch_without_state_reads(self):
        state = self.state_mod.MatrixState()
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        state.agents[worker.id] = worker
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "unexpected"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(FakeRequest(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"X-Torque-Cell-Id": worker.id},
        ))
        names = {tool["name"] for tool in listed.payload["result"]["tools"]}
        self.assertIn("torque_help_list", names)
        self.assertIn("torque_help_query", names)

        response = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "torque_help_search",
                    "arguments": {"query": "MCP scoping", "limit": 2},
                },
            },
            headers={"X-Torque-Cell-Id": worker.id},
        ))
        self.assertFalse(response.payload["result"]["isError"])
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(payload["type"], "help_search")
        self.assertIn(payload["status"], {"ok", "no_answer"})
        self.assertEqual(calls, [])

    async def test_product_manager_class_can_query_help_but_not_hidden_state_tools(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Product Manager",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]
        state.assign_agent_class(
            architect.id,
            "product-manager",
            actor_kind="user",
        )
        state.apply_effective_agent_class_for_launch(architect)

        async def fake_handle_command(payload):
            self.fail(f"Help MCP should not call server command handler: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(FakeRequest(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        names = {tool["name"] for tool in listed.payload["result"]["tools"]}
        self.assertIn("architect_help_list", names)
        self.assertIn("architect_help_query", names)
        self.assertNotIn("architect_mcp_calls", names)
        self.assertNotIn("architect_engineer_hire", names)

        response = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "architect_help_query",
                    "arguments": {"question": "What is MCP scoping?", "limit": 1},
                },
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        self.assertFalse(response.payload["result"]["isError"])
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(payload["type"], "help_query")
        self.assertIn(payload["status"], {"answered", "no_answer"})
        self.assertTrue(payload.get("source_model", {}).get("restricted_safe"))

        denied = await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "architect_mcp_calls", "arguments": {}},
            },
            headers={"X-Torque-Cell-Id": architect.id},
        ))
        self.assertIn("error", denied.payload)
        self.assertIn("Unknown tool", denied.payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
