import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class CommunicationGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("loom.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_architect_mod = importlib.import_module("loom.mcp_architect")
        self.mcp_architect_mod = importlib.reload(self.mcp_architect_mod)
        self.mcp_engineer_mod = importlib.import_module("loom.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "loom.db"
        self.db = self.db_mod.LoomDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["loom"] = []
        self.state._db_save_groups()
        self.handle_calls = []

    def _add_architect(self, agent_id: str, name: str):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="loom",
            cell_type="agent",
            kind="architect",
            status="running",
            persistent=True,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["loom"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_engineer(self, agent_id: str, name: str, *, hired_by_architect_id: str = ""):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="loom",
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
            hired_by_architect_id=hired_by_architect_id,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["loom"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_worker(self, agent_id: str, name: str, owner_engineer_id: str):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="loom",
            cell_type="agent",
            kind="worker",
            status="idle",
            owner_engineer_id=owner_engineer_id,
            created_by_engineer_id=owner_engineer_id,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["loom"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _snapshot(self, *cells):
        return {
            "task_ids": sorted(self.state.board_tasks),
            "message_counts": {
                cell.id: len(list(getattr(cell, "mcp_messages", []) or []))
                for cell in cells
            },
            "handle_calls": len(self.handle_calls),
        }

    def _assert_snapshot_unchanged(self, before, *cells):
        self.assertEqual(sorted(self.state.board_tasks), before["task_ids"])
        self.assertEqual(len(self.handle_calls), before["handle_calls"])
        for cell in cells:
            self.assertEqual(
                len(list(getattr(cell, "mcp_messages", []) or [])),
                before["message_counts"][cell.id],
            )

    async def _handle_command(self, payload):
        self.handle_calls.append(dict(payload))
        if payload["cmd"] == "engineer_message":
            return {"type": "ok", "agent_id": payload["agent_id"]}
        self.fail(f"Unexpected command: {payload}")

    async def _call_architect(self, tool_name: str, args: dict, caller_id: str):
        return await self.mcp_architect_mod._dispatch_architect_tool(
            tool_name,
            args,
            self._handle_command,
            self.state,
            caller_id=caller_id,
        )

    async def _call_engineer(self, tool_name: str, args: dict, caller_id: str):
        return await self.mcp_engineer_mod._dispatch_engineer_tool(
            tool_name,
            args,
            self._handle_command,
            self.state,
            caller_id=caller_id,
        )

    async def test_architect_tool_surface_excludes_worker_targeting_tools(self):
        tool_names = {tool["name"] for tool in self.mcp_architect_mod.ARCHITECT_TOOLS}
        self.assertNotIn("architect_agent_message", tool_names)
        self.assertFalse(any("worker" in name for name in tool_names))

        architect = self._add_architect("arch-1", "Architect")
        worker = self._add_worker("worker-1", "Worker", "")
        before = self._snapshot(architect, worker)

        text, is_error = await self._call_architect(
            "architect_agent_message",
            {"agent": worker.id, "message": "hello"},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Unknown architect tool: architect_agent_message")
        self._assert_snapshot_unchanged(before, architect, worker)

    async def test_architect_cannot_message_worker(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer("eng-1", "Engineer", hired_by_architect_id=architect.id)
        worker = self._add_worker("worker-1", "Worker", engineer.id)
        before = self._snapshot(architect, engineer, worker)

        text, is_error = await self._call_architect(
            "architect_engineer_message",
            {"engineer_id": worker.id, "message": "hidden"},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "engineer not found in scope")
        self._assert_snapshot_unchanged(before, architect, engineer, worker)

    async def test_architect_cannot_message_visible_non_hired_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        visible_engineer = self._add_engineer("eng-visible", "Visible Engineer")
        before = self._snapshot(architect, visible_engineer)

        text, is_error = await self._call_architect(
            "architect_engineer_message",
            {"engineer_id": visible_engineer.id, "message": "hidden"},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "engineer not found in scope")
        self._assert_snapshot_unchanged(before, architect, visible_engineer)

    async def test_engineer_cannot_message_other_engineers_worker(self):
        engineer_a = self._add_engineer("eng-a", "Alice")
        engineer_b = self._add_engineer("eng-b", "Bob")
        worker_b = self._add_worker("worker-b", "Bob Worker", engineer_b.id)
        before = self._snapshot(engineer_a, engineer_b, worker_b)

        text, is_error = await self._call_engineer(
            "engineer_agent_message",
            {"agent": worker_b.id, "message": "hello"},
            engineer_a.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "agent not found in scope")
        self._assert_snapshot_unchanged(before, engineer_a, engineer_b, worker_b)

    async def test_engineer_cannot_message_non_hiring_architect(self):
        architect_a = self._add_architect("arch-a", "Architect A")
        architect_b = self._add_architect("arch-b", "Architect B")
        engineer = self._add_engineer(
            "eng-a",
            "Engineer",
            hired_by_architect_id=architect_a.id,
        )
        before = self._snapshot(architect_a, architect_b, engineer)

        text, is_error = await self._call_engineer(
            "engineer_message_architect",
            {"architect_id": architect_b.id, "message": "wrong architect"},
            engineer.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "architect not found in scope")
        self._assert_snapshot_unchanged(before, architect_a, architect_b, engineer)

    async def test_valid_cross_kind_pairs_succeed(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1",
            "Engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_worker("worker-1", "Worker", engineer.id)

        architect_text, architect_error = await self._call_architect(
            "architect_engineer_message",
            {"engineer_id": engineer.id, "message": "Need a product decision."},
            architect.id,
        )
        self.assertFalse(architect_error, architect_text)
        architect_payload = json.loads(architect_text)
        self.assertTrue(architect_payload["message_id"])
        self.assertEqual(engineer.mcp_messages[0]["action"], "architect_message")

        engineer_arch_text, engineer_arch_error = await self._call_engineer(
            "engineer_message_architect",
            {"architect_id": architect.id, "message": "Need product guidance."},
            engineer.id,
        )
        self.assertFalse(engineer_arch_error, engineer_arch_text)
        self.assertEqual(architect.mcp_messages[0]["action"], "engineer_message_architect")

        agent_text, agent_error = await self._call_engineer(
            "engineer_agent_message",
            {"agent": worker.id, "message": "Please update the tests."},
            engineer.id,
        )
        self.assertFalse(agent_error, agent_text)
        self.assertEqual(self.handle_calls[-1]["cmd"], "engineer_message")
        self.assertEqual(self.handle_calls[-1]["agent_id"], worker.id)
