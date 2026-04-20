import importlib
import json
import unittest
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MCPScopingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("loom.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)
        self.shared_mod = importlib.import_module("loom.mcp_tools_shared")
        self.shared_mod = importlib.reload(self.shared_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        state.groups["loom"] = []
        return state

    def _add_engineer(self, state, agent_id, name):
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
        state.agents[agent_id] = engineer
        state.groups["loom"].append(agent_id)
        return engineer

    def _add_legacy_weaver(self, state, agent_id, name, group):
        if group not in state.groups:
            state.groups[group] = []
        weaver = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group=group,
            cell_type="agent",
            status="running",
        )
        state.agents[agent_id] = weaver
        state.groups[group].append(agent_id)
        state.group_settings[group] = self.state_mod.GroupSettings(
            weaver_agent_id=agent_id
        )
        return weaver

    def _add_worker(self, state, agent_id, name, owner_id):
        worker = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group="loom",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=owner_id,
            created_by_weaver_id=owner_id,
            status="idle",
        )
        state.agents[agent_id] = worker
        state.groups["loom"].append(agent_id)
        return worker

    def _add_task(self, state, task_id, title, *, group="loom",
                  assigned_engineer_id=""):
        task = self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group=group,
            lane="Backlog",
            assigned_engineer_id=assigned_engineer_id,
        )
        state.board_tasks[task.id] = task
        return task

    async def test_engineer_agents_list_scopes_to_single_engineer(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        alice_worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        self._add_worker(state, "worker-orphan", "Orphan Worker", "")

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(
            [item["id"] for item in data["agents"]],
            [alice.id, alice_worker.id],
        )

    async def test_engineer_agents_list_scopes_between_two_engineers(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        alice_worker = self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        bob_worker = self._add_worker(state, "worker-b", "Bob Worker", bob.id)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        alice_text, alice_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )
        bob_text, bob_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=bob.id,
        )

        self.assertFalse(alice_error, alice_text)
        self.assertFalse(bob_error, bob_text)
        self.assertEqual(
            {item["id"] for item in json.loads(alice_text)["agents"]},
            {alice.id, alice_worker.id},
        )
        self.assertEqual(
            {item["id"] for item in json.loads(bob_text)["agents"]},
            {bob.id, bob_worker.id},
        )

    async def test_engineer_task_create_stamps_assigned_engineer(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "board_task_added", "task_id": "LOOM:1"}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_create",
            {
                "title": "Investigate regression",
                "description": "repro and fix",
                "assigned_engineer_id": "eng-bob",
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(calls[0]["assigned_engineer_id"], alice.id)
        self.assertEqual(json.loads(text)["task_id"], "LOOM:1")

    async def test_engineer_task_create_forces_caller_group(self):
        state = self._make_state()
        state.groups["other"] = []
        alice = self._add_engineer(state, "eng-alice", "Alice")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "board_task_added", "task_id": "LOOM:2"}

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_create",
            {
                "title": "Cross-group attempt",
                "group": "other",
            },
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(calls[0]["group"], "loom")
        self.assertEqual(calls[0]["assigned_engineer_id"], alice.id)

    async def test_engineer_task_edit_rejects_unassigned_task_in_other_group(self):
        state = self._make_state()
        state.groups["other"] = []
        alice = self._add_engineer(state, "eng-alice", "Alice")
        self._add_task(state, "task-b", "Other group task", group="other")

        async def fake_handle_command(_payload):
            self.fail("cross-group task should be rejected before mutation")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_edit",
            {"task": "task-b", "title": "Hacked"},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task not found")

    async def test_engineer_agent_message_rejects_out_of_scope_target(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        self._add_worker(state, "worker-b", "Bob Worker", bob.id)

        async def fake_handle_command(_payload):
            self.fail("out-of-scope target should be rejected before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agent_message",
            {"agent": "worker-b", "message": "hello"},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "agent not found in scope")

    async def test_weaver_alias_uses_default_weaver_named_engineer(self):
        state = self._make_state()
        weaver = self._add_engineer(state, "eng-weaver", "Weaver")
        alice = self._add_engineer(state, "eng-alice", "Alice")
        weaver_worker = self._add_worker(state, "worker-w", "Weaver Worker", weaver.id)
        self._add_worker(state, "worker-a", "Alice Worker", alice.id)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_agents_list",
            {},
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(
            {item["id"] for item in json.loads(text)["agents"]},
            {weaver.id, weaver_worker.id},
        )

    async def test_weaver_alias_uses_bound_engineer_session_scope(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        self._add_worker(state, "worker-a", "Alice Worker", alice.id)
        bob_worker = self._add_worker(state, "worker-b", "Bob Worker", bob.id)

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_agents_list",
            {},
            fake_handle_command,
            state,
            cell_id=bob.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(
            {item["id"] for item in json.loads(text)["agents"]},
            {bob.id, bob_worker.id},
        )

    async def test_weaver_alias_uses_bound_legacy_weaver_session_group(self):
        state = self._make_state()
        del state.groups["loom"]
        weaver_a = self._add_legacy_weaver(state, "weaver-a", "Weaver", "group-a")
        weaver_b = self._add_legacy_weaver(state, "weaver-b", "Beta Weaver", "group-b")
        self._add_task(state, "task-a", "Group A task", group="group-a")
        self._add_task(state, "task-b", "Group B task", group="group-b")

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        default_text, default_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_summary",
            {},
            fake_handle_command,
            state,
        )
        bound_text, bound_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_summary",
            {},
            fake_handle_command,
            state,
            cell_id=weaver_b.id,
        )

        self.assertFalse(default_error, default_text)
        self.assertFalse(bound_error, bound_text)
        self.assertEqual(json.loads(default_text)["group"], weaver_a.group)
        self.assertEqual(json.loads(bound_text)["group"], weaver_b.group)

    async def test_engineer_board_summary_excludes_unassigned_tasks(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        self._add_task(
            state,
            "task-owned",
            "Owned task",
            assigned_engineer_id=alice.id,
        )
        self._add_task(state, "task-unassigned", "Unassigned task")

        async def fake_handle_command(_payload):
            self.fail("read tool should not call handle_command")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_board_summary",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["tasks_total"], 1)
        self.assertEqual(data["lanes"]["Backlog"], 1)

    async def test_weaver_alias_returns_no_engineer_error_when_none_exist(self):
        state = self._make_state()

        async def fake_handle_command(_payload):
            self.fail("alias should fail before dispatch")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_agents_list",
            {},
            fake_handle_command,
            state,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, self.shared_mod.NO_ENGINEER_ALIAS_ERROR)

    async def test_deleted_engineer_session_returns_structured_error(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        state.groups["loom"].remove(alice.id)
        del state.agents[alice.id]

        async def fake_handle_command(_payload):
            self.fail("deleted engineer should fail before dispatch")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_agents_list",
            {},
            fake_handle_command,
            state,
            caller_id=alice.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(
            json.loads(text),
            {"type": "error", "message": f"no engineer with id={alice.id} exists"},
        )


class EngineerBindingValidationTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("loom.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["loom"] = []
        return state

    def test_validate_engineer_binding_requires_env_var(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            engineer_id, error = self.mcp_engineer_mod.validate_engineer_binding()

        self.assertEqual(engineer_id, "")
        self.assertEqual(error, "LOOM_ENGINEER_ID is required")

    def test_validate_engineer_binding_rejects_missing_engineer(self):
        state = self._make_state()
        with mock.patch.dict("os.environ", {"LOOM_ENGINEER_ID": "eng-missing"}, clear=True):
            engineer_id, error = self.mcp_engineer_mod.validate_engineer_binding(state)

        self.assertEqual(engineer_id, "")
        self.assertEqual(error, "no engineer with id=eng-missing exists")
