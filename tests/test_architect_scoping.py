import importlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ArchitectScopingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.db_mod = importlib.import_module("loom.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.shared_mod = importlib.import_module("loom.mcp_tools_shared")
        self.shared_mod = importlib.reload(self.shared_mod)
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
            owner_engineer_id=owner_engineer_id,
            created_by_weaver_id=owner_engineer_id,
            status="idle",
        )
        self.state.agents[cell.id] = cell
        self.state.groups["loom"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_task(self, task_id: str, title: str, **kwargs):
        task = self.state.board_add_task(
            title,
            kwargs.pop("group", "loom"),
            lane=kwargs.pop("lane", "Backlog"),
            id=task_id,
            **kwargs,
        )
        self.assertIsNotNone(task)
        return task

    async def _handle_command(self, payload):
        if payload["cmd"] == "board_add_task":
            task = self.state.board_add_task(
                payload.get("task", ""),
                payload.get("group", ""),
                lane=payload.get("lane", ""),
                description=payload.get("description", ""),
                labels=payload.get("labels", []),
                assigned_engineer_id=payload.get("assigned_engineer_id", ""),
            )
            if not task:
                return {"type": "error", "message": "task create failed"}
            return {"type": "board_task_added", "task_id": task.id, "title": task.task}
        if payload["cmd"] == "board_update_task":
            self.state.board_update_task(
                payload["id"],
                **{k: v for k, v in payload.items() if k not in {"cmd", "id"}},
            )
            return {"type": "ok"}
        self.fail(f"Unexpected command: {payload}")

    async def _call(self, tool_name: str, args: dict, caller_id: str):
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

    async def test_architect_engineer_list_scopes_to_hired_and_user_visible_engineers(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        self._add_engineer("eng-bob", "Bob", hired_by_architect_id=other_architect.id)
        user_engineer = self._add_engineer("eng-user", "User Owned")
        self._add_worker("worker-a", "Alice Worker", alice.id)
        visible_task = self._add_task(
            "task-visible",
            "Architect-visible work",
            lane="In Progress",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        hidden_task = self._add_task(
            "task-hidden",
            "Other work",
            lane="In Progress",
            assigned_engineer_id=user_engineer.id,
        )
        visible_task.agent_id = alice.id
        hidden_task.agent_id = user_engineer.id
        self.state.agents[alice.id].current_task_id = visible_task.id
        self.state.agents[user_engineer.id].current_task_id = hidden_task.id

        visible_agents = self.shared_mod._filter_agents_for_caller(
            self.state,
            "architect",
            architect.id,
        )
        self.assertEqual(set(visible_agents), {architect.id, alice.id})

        text, is_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        engineers = {
            item["id"]: item
            for item in json.loads(text)["engineers"]
        }
        self.assertEqual(
            {engineer_id: item["relation"] for engineer_id, item in engineers.items()},
            {
                alice.id: "hired",
                user_engineer.id: "visible",
            },
        )
        self.assertEqual(engineers[alice.id]["current_task_id"], visible_task.id)
        self.assertEqual(engineers[alice.id]["current_task"], visible_task.task)
        self.assertEqual(engineers[user_engineer.id]["current_task_id"], "")
        self.assertEqual(engineers[user_engineer.id]["current_task"], "")

    async def test_architect_task_create_stamps_architect_fields_and_rejects_out_of_scope_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=other_architect.id
        )

        text, is_error = await self._call(
            "architect_task_create",
            {
                "title": "Investigate regression",
                "group": "loom",
                "description": "Repro and isolate root cause",
                "suggested_action": "feature/implement",
                "action_vars": {"TEST_COMMAND": "python3 -m unittest"},
                "assigned_engineer_id": alice.id,
                "labels": ["bug"],
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        created = json.loads(text)
        task = self.state.board_tasks[created["task_id"]]
        self.assertEqual(task.assigned_engineer_id, alice.id)
        self.assertEqual(task.created_by_architect_id, architect.id)
        self.assertEqual(task.suggested_action, "feature/implement")
        self.assertEqual(task.action_name, "")
        self.assertEqual(task.action_vars, {"TEST_COMMAND": "python3 -m unittest"})

        denied_text, denied_error = await self._call(
            "architect_task_create",
            {
                "title": "Cross-architect assign",
                "group": "loom",
                "assigned_engineer_id": bob.id,
            },
            architect.id,
        )

        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "engineer not found in scope")

    async def test_architect_task_reassign_only_allows_tasks_created_by_caller(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        user_engineer = self._add_engineer("eng-user", "User Owned")
        own_task = self._add_task(
            "task-own",
            "Architect-owned task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        other_task = self._add_task(
            "task-other",
            "Other architect task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=other_architect.id,
        )

        denied_text, denied_error = await self._call(
            "architect_task_reassign",
            {"task": other_task.id, "new_engineer_id": user_engineer.id},
            architect.id,
        )
        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "Task was not created by this architect")
        self.assertEqual(self.state.board_tasks[other_task.id].assigned_engineer_id, alice.id)

        ok_text, ok_error = await self._call(
            "architect_task_reassign",
            {"task": own_task.id, "new_engineer_id": user_engineer.id},
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        self.assertEqual(
            self.state.board_tasks[own_task.id].assigned_engineer_id,
            user_engineer.id,
        )

    async def test_architect_and_engineer_messaging_respects_hiring_scope(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        other_hired = self._add_engineer(
            "eng-other", "Other Hired", hired_by_architect_id=other_architect.id
        )

        ok_text, ok_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": hired.id, "message": "Need a scope decision."},
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        delivered = json.loads(ok_text)
        self.assertTrue(delivered["message_id"])
        self.assertEqual(hired.mcp_messages[0]["action"], "architect_message")
        self.assertEqual(architect.mcp_messages[0]["action"], "architect_message")
        self.assertTrue(hired.pending_weaver_message)
        self.assertEqual(
            [op["op"] for op in self.state._delta_ops[-2:]],
            ["agent_upsert", "agent_upsert"],
        )

        denied_text, denied_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": other_hired.id, "message": "Hidden"},
            architect.id,
        )
        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "engineer not found in scope")

        engineer_ok_text, engineer_ok_error = await self._call_engineer(
            "engineer_message_architect",
            {"architect_id": architect.id, "message": "Need product guidance."},
            hired.id,
        )
        self.assertFalse(engineer_ok_error, engineer_ok_text)
        self.assertEqual(
            architect.mcp_messages[0]["action"],
            "engineer_message_architect",
        )
        self.assertFalse(hired.pending_weaver_message)

        engineer_denied_text, engineer_denied_error = await self._call_engineer(
            "engineer_message_architect",
            {"architect_id": other_architect.id, "message": "Wrong architect"},
            hired.id,
        )
        self.assertTrue(engineer_denied_error)
        self.assertEqual(engineer_denied_text, "architect not found in scope")

    async def test_architect_and_engineer_replies_follow_existing_threads(self):
        architect = self._add_architect("arch-1", "Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )

        first_text, first_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": hired.id, "message": "Please confirm the plan."},
            architect.id,
        )
        self.assertFalse(first_error, first_text)
        thread = json.loads(first_text)

        reply_text, reply_error = await self._call_engineer(
            "engineer_reply",
            {
                "message_id": thread["message_id"],
                "message": "Confirmed; I need one more decision.",
            },
            hired.id,
        )
        self.assertFalse(reply_error, reply_text)
        reply = json.loads(reply_text)
        self.assertEqual(architect.mcp_messages[0]["action"], "engineer_reply")
        self.assertEqual(architect.mcp_messages[0]["thread_id"], thread["thread_id"])

        architect_reply_text, architect_reply_error = await self._call(
            "architect_reply",
            {
                "message_id": reply["message_id"],
                "message": "Approved. Continue on the safer path.",
            },
            architect.id,
        )
        self.assertFalse(architect_reply_error, architect_reply_text)
        architect_reply = json.loads(architect_reply_text)
        self.assertEqual(
            hired.mcp_messages[0]["action"],
            "architect_reply",
        )
        self.assertEqual(
            hired.mcp_messages[0]["thread_id"],
            architect_reply["thread_id"],
        )

    async def test_architect_decisions_are_scoped_to_owner(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        user_engineer = self._add_engineer("eng-user", "User Owned")
        task = self._add_task(
            "task-1",
            "Architect task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )

        create_text, create_error = await self._call(
            "architect_decision_create",
            {
                "title": "Ship safer path",
                "rationale": "Reduces production risk",
                "linked_task_ids": [task.id],
                "linked_engineer_ids": [alice.id],
            },
            architect.id,
        )
        self.assertFalse(create_error, create_text)
        created = json.loads(create_text)
        decision = self.state.load_decision(created["id"])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["architect_id"], architect.id)
        self.assertEqual(decision["status"], "proposed")
        self.assertEqual(decision["linked_task_ids"], [task.id])
        self.assertEqual(decision["linked_engineer_ids"], [alice.id])
        self.assertGreater(decision["created_at"], 0)
        self.assertEqual(self.state._delta_ops[-1]["op"], "decision_upsert")

        update_text, update_error = await self._call(
            "architect_decision_update",
            {
                "id": decision["id"],
                "status": "accepted",
                "linked_engineer_ids": [alice.id, user_engineer.id],
            },
            architect.id,
        )
        self.assertFalse(update_error, update_text)
        updated = json.loads(update_text)
        self.assertEqual(updated["status"], "accepted")
        self.assertEqual(
            updated["linked_engineer_ids"],
            [alice.id, user_engineer.id],
        )

        list_text, list_error = await self._call(
            "architect_decision_list",
            {},
            architect.id,
        )
        self.assertFalse(list_error, list_text)
        self.assertEqual(len(json.loads(list_text)["decisions"]), 1)

        other_text, other_error = await self._call(
            "architect_decision_list",
            {},
            other_architect.id,
        )
        self.assertFalse(other_error, other_text)
        self.assertEqual(json.loads(other_text)["decisions"], [])

        relink_text, relink_error = await self._call(
            "architect_decision_link",
            {"id": decision["id"], "engineer_id": user_engineer.id},
            architect.id,
        )
        self.assertFalse(relink_error, relink_text)
        relinked = json.loads(relink_text)
        self.assertEqual(
            relinked["linked_engineer_ids"],
            [alice.id, user_engineer.id],
        )

        archive_text, archive_error = await self._call(
            "architect_decision_update",
            {"id": decision["id"], "archived": True},
            architect.id,
        )
        self.assertFalse(archive_error, archive_text)
        self.assertEqual(self.state._delta_ops[-1]["op"], "decision_remove")

    async def test_architect_journal_round_trips_and_uses_private_file_permissions(self):
        architect = self._add_architect("arch-1", "Architect")
        with mock.patch.object(self.state_mod, "DATA_DIR", Path(self.tmp.name)):
            write_text, write_error = await self._call(
                "architect_journal",
                {"type": "plan", "entry": "Review the rollout strategy."},
                architect.id,
            )
            self.assertFalse(write_error, write_text)
            written = json.loads(write_text)
            self.assertEqual(written["type"], "plan")

            read_text, read_error = await self._call(
                "architect_journal_read",
                {"limit": 10},
                architect.id,
            )
            self.assertFalse(read_error, read_text)
            entries = json.loads(read_text)["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["entry"], "Review the rollout strategy.")

            path = self.state._architect_journal_path(architect.id)
            self.assertTrue(path.exists())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class ArchitectBindingValidationTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_architect_mod = importlib.import_module("loom.mcp_architect")
        self.mcp_architect_mod = importlib.reload(self.mcp_architect_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["loom"] = []
        return state

    def test_validate_architect_binding_requires_env_var(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            architect_id, error = self.mcp_architect_mod.validate_architect_binding()

        self.assertEqual(architect_id, "")
        self.assertEqual(error, "LOOM_ARCHITECT_ID is required")

    def test_exit_if_invalid_architect_binding_rejects_missing_architect(self):
        state = self._make_state()
        with mock.patch.dict("os.environ", {"LOOM_ARCHITECT_ID": "arch-missing"}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                self.mcp_architect_mod.exit_if_invalid_architect_binding(state)

        self.assertEqual(ctx.exception.code, 2)

    def test_exit_if_invalid_architect_binding_rejects_non_architect_agent(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            slug="engineer",
            group="loom",
            cell_type="agent",
            kind="engineer",
            status="running",
        )
        state.agents[engineer.id] = engineer
        state.groups["loom"].append(engineer.id)

        with mock.patch.dict("os.environ", {"LOOM_ARCHITECT_ID": engineer.id}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                self.mcp_architect_mod.exit_if_invalid_architect_binding(state)

        self.assertEqual(ctx.exception.code, 2)
