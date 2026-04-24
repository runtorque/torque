import importlib
import json
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class _CapturingBridge:
    def __init__(self):
        self.capabilities = types.SimpleNamespace(
            supports_embedded_terminal=False,
        )
        self.create_session_calls = []

    async def create_session(self, cell, **kwargs):
        cell.session_id = kwargs.get("session_id", f"session-{cell.id}")
        self.create_session_calls.append({
            "cell": cell,
            "kwargs": kwargs,
        })


class ArchitectHireFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("loom.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.server_agent_mod = importlib.import_module("loom.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_architect_mod = importlib.import_module("loom.mcp_architect")
        self.mcp_architect_mod = importlib.reload(self.mcp_architect_mod)

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

    def _add_architect(self, agent_id: str, name: str):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
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

    def _launch_config(self, directory: str) -> dict:
        return {
            "profile": "Default",
            "command": "codex",
            "directory": directory,
            "tab_color": "",
            "icon": "",
            "env_vars": {"BASE": "1"},
            "env_file": str(Path(directory) / ".env"),
            "shell": "zsh",
            "system_prompt": "Engineer system prompt",
            "initial_prompt": "",
            "template": "",
            "session_resume": True,
            "idle_timeout": 0,
            "worktree": False,
            "terminals": [],
            "agent_type": "codex",
        }

    async def _call_architect(self, tool_name: str, args: dict, caller_id: str):
        async def _unexpected_handle(_payload):
            self.fail("architect_pending_hire_* should not need handle_command")

        return await self.mcp_architect_mod._dispatch_architect_tool(
            tool_name,
            args,
            _unexpected_handle,
            self.state,
            caller_id=caller_id,
        )

    async def test_architect_engineer_hire_creates_pending_row_and_emits_delta(self):
        architect = self._add_architect("arch-1", "Architect")

        result = await self.server_mod._handle_architect_engineer_hire_command(
            {
                "architect_id": architect.id,
                "name": "Alice",
                "command": "codex --full-auto",
                "provider": "codex",
                "directory": "/tmp/project",
            },
            self.state,
        )

        self.assertEqual(result["status"], "pending")
        pending_hire = self.state.load_pending_hire(result["hire_id"])
        self.assertIsNotNone(pending_hire)
        self.assertEqual(pending_hire["architect_id"], architect.id)
        self.assertEqual(pending_hire["requested_name"], "Alice")
        self.assertEqual(pending_hire["requested_command"], "codex --full-auto")
        self.assertEqual(pending_hire["requested_provider"], "codex")
        self.assertEqual(pending_hire["requested_directory"], "/tmp/project")
        self.assertEqual(self.state._delta_ops[-1]["op"], "pending_hire_upsert")

    async def test_pending_hire_approve_spawns_hired_engineer_and_resolves_row(self):
        architect = self._add_architect("arch-1", "Architect")
        pending_hire = self.state.save_pending_hire({
            "id": "hire-1",
            "architect_id": architect.id,
            "requested_name": "Alice",
            "requested_command": "codex --full-auto",
            "requested_provider": "codex",
            "requested_directory": "",
            "status": "pending",
        })
        self.assertIsNotNone(pending_hire)
        self.state._delta_ops = []

        bridge = _CapturingBridge()
        service = self.server_agent_mod.AgentLaunchService(
            state=self.state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=None,
        )

        async def fake_resolve_base_dir(group):
            self.assertEqual(group, "loom")
            return self.tmp.name

        def fake_resolve_engineer_launch_config(group, *, base_dir="", explicit_template="", overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(base_dir, self.tmp.name)
            self.assertEqual(explicit_template, "")
            self.assertEqual(
                overrides,
                {"command": "codex --full-auto", "provider": "codex"},
            )
            return self._launch_config(self.tmp.name)

        sent_prompts = []

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append({
                "cell_id": cell.id,
                "prompt": prompt,
                "kwargs": kwargs,
            })

        result = await self.server_mod._handle_pending_hire_approve_command(
            {"id": pending_hire["id"]},
            self.state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
            create_agent_with_config=service.create_agent_with_config,
            send_agent_prompt=fake_send_agent_prompt,
        )

        engineer = self.state.agents[result["engineer_id"]]
        self.assertEqual(engineer.kind, "engineer")
        self.assertTrue(engineer.persistent)
        self.assertEqual(engineer.hired_by_architect_id, architect.id)
        self.assertEqual(result["slug"], engineer.slug)
        updated_hire = self.state.load_pending_hire(pending_hire["id"])
        self.assertEqual(updated_hire["status"], "approved")
        self.assertEqual(updated_hire["created_engineer_id"], engineer.id)
        op_names = [op["op"] for op in self.state._delta_ops]
        self.assertIn("agent_upsert", op_names)
        self.assertIn("pending_hire_resolve", op_names)
        self.assertEqual(bridge.create_session_calls[0]["kwargs"]["env_vars"]["LOOM_ENGINEER_ID"], engineer.id)
        self.assertEqual(len(sent_prompts), 1)

        second = await self.server_mod._handle_pending_hire_approve_command(
            {"id": pending_hire["id"]},
            self.state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
            create_agent_with_config=service.create_agent_with_config,
            send_agent_prompt=fake_send_agent_prompt,
        )
        self.assertEqual(second["engineer_id"], engineer.id)
        self.assertEqual(len([cell for cell in self.state.agents.values() if cell.kind == "engineer"]), 1)

    async def test_pending_hire_reject_stores_note_and_emits_resolve(self):
        architect = self._add_architect("arch-1", "Architect")
        pending_hire = self.state.save_pending_hire({
            "id": "hire-1",
            "architect_id": architect.id,
            "requested_name": "Alice",
            "status": "pending",
        })
        self.assertIsNotNone(pending_hire)
        self.state._delta_ops = []

        result = await self.server_mod._handle_pending_hire_reject_command(
            {"id": pending_hire["id"], "note": "Not now"},
            self.state,
        )

        self.assertEqual(result, {"ok": True})
        updated = self.state.load_pending_hire(pending_hire["id"])
        self.assertEqual(updated["status"], "rejected")
        self.assertEqual(updated["resolution_note"], "Not now")
        self.assertEqual(updated["created_engineer_id"], "")
        self.assertEqual(self.state._delta_ops[-1]["op"], "pending_hire_resolve")
        self.assertEqual(
            len([cell for cell in self.state.agents.values() if cell.kind == "engineer"]),
            0,
        )

    async def test_architect_pending_hire_list_is_scoped_to_owner(self):
        first = self._add_architect("arch-1", "Architect")
        second = self._add_architect("arch-2", "Other Architect")
        self.state.save_pending_hire({
            "id": "hire-1",
            "architect_id": first.id,
            "requested_name": "Alice",
            "status": "pending",
        })
        self.state.save_pending_hire({
            "id": "hire-2",
            "architect_id": second.id,
            "requested_name": "Bob",
            "status": "pending",
        })

        list_text, list_error = await self._call_architect(
            "architect_pending_hire_list",
            {},
            first.id,
        )
        self.assertFalse(list_error, list_text)
        hires = json.loads(list_text)["pending_hires"]
        self.assertEqual([item["id"] for item in hires], ["hire-1"])

        status_text, status_error = await self._call_architect(
            "architect_pending_hire_status",
            {"hire_id": "hire-2"},
            first.id,
        )
        self.assertTrue(status_error)
        self.assertEqual(status_text, "Pending hire not found")

    async def test_snapshot_only_includes_live_decisions_and_pending_hires(self):
        architect = self._add_architect("arch-1", "Architect")
        self.state.save_decision({
            "id": "decision-live",
            "architect_id": architect.id,
            "title": "Live decision",
            "rationale": "Keep it visible",
            "status": "proposed",
        })
        self.state.save_decision({
            "id": "decision-archived",
            "architect_id": architect.id,
            "title": "Archived decision",
            "rationale": "Hide from live snapshot",
            "status": "revised",
            "archived": True,
        })
        self.state.save_pending_hire({
            "id": "hire-pending",
            "architect_id": architect.id,
            "requested_name": "Pending Engineer",
            "status": "pending",
        })
        self.state.save_pending_hire({
            "id": "hire-approved",
            "architect_id": architect.id,
            "requested_name": "Approved Engineer",
            "status": "approved",
        })

        payload = self.state.to_dict()

        self.assertEqual(set(payload["decisions"]), {"decision-live"})
        self.assertEqual(set(payload["pending_hires"]), {"hire-pending"})
