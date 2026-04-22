import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EngineerJournalScopingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("loom.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("loom.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = self.db_mod.LoomDB(Path(self.tmp.name) / "loom.db")
        self.db.init()
        self.addCleanup(self.db.close)

        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["loom"] = []

    def _add_engineer(self, agent_id, name):
        engineer = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group="loom",
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
        )
        self.state.agents[agent_id] = engineer
        self.state.groups["loom"].append(agent_id)
        return engineer

    async def _unexpected_handle_command(self, payload):
        self.fail(f"read tool should not call handle_command: {payload}")

    async def test_engineer_journal_read_defaults_to_calling_author(self):
        alice = self._add_engineer("eng-alice", "Alice")
        bob = self._add_engineer("eng-bob", "Bob")
        self.state.journal_append(
            "loom",
            "plan",
            "Alice's plan",
            author_cell_id=alice.id,
        )
        self.state.journal_append(
            "loom",
            "plan",
            "Bob's plan",
            author_cell_id=bob.id,
        )

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_journal_read",
            {"tail": 10},
            self._unexpected_handle_command,
            self.state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        entries = json.loads(text)["entries"]
        self.assertEqual([entry["entry"] for entry in entries], ["Alice's plan"])
        self.assertEqual({entry["author_cell_id"] for entry in entries}, {alice.id})

    async def test_engineer_journal_read_scope_group_returns_group_view(self):
        alice = self._add_engineer("eng-alice", "Alice")
        bob = self._add_engineer("eng-bob", "Bob")
        self.state.journal_append(
            "loom",
            "plan",
            "Alice's plan",
            author_cell_id=alice.id,
        )
        self.state.journal_append(
            "loom",
            "decision",
            "Bob's decision",
            author_cell_id=bob.id,
        )

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_journal_read",
            {"tail": 10, "scope": "group"},
            self._unexpected_handle_command,
            self.state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        entries = json.loads(text)["entries"]
        self.assertEqual(
            [entry["entry"] for entry in entries],
            ["Bob's decision", "Alice's plan"],
        )
        self.assertEqual(
            [entry["author_cell_id"] for entry in entries],
            [bob.id, alice.id],
        )

    async def test_engineer_session_map_journal_uses_self_scoped_default(self):
        alice = self._add_engineer("eng-alice", "Alice")
        bob = self._add_engineer("eng-bob", "Bob")
        self.state.journal_append(
            "loom",
            "checkpoint",
            "Alice checkpoint",
            author_cell_id=alice.id,
        )
        self.state.journal_append(
            "loom",
            "checkpoint",
            "Bob checkpoint",
            author_cell_id=bob.id,
        )

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_session_map",
            {},
            self._unexpected_handle_command,
            self.state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        journal_items = json.loads(text)["journal"]["items"]
        self.assertEqual(
            [item["entry"] for item in journal_items],
            ["Alice checkpoint"],
        )

        group_text, group_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_journal_read",
            {"tail": 10, "scope": "group"},
            self._unexpected_handle_command,
            self.state,
            caller_id=alice.id,
        )
        self.assertFalse(group_error, group_text)
        self.assertEqual(
            [entry["entry"] for entry in json.loads(group_text)["entries"]],
            ["Bob checkpoint", "Alice checkpoint"],
        )
