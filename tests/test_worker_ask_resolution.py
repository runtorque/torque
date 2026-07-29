import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class WorkerAskResolutionTests(unittest.IsolatedAsyncioTestCase):
    """Worker asks are explicitly answerable by their owning Engineer."""

    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_mod = importlib.import_module("torque.db")
        self.state_mod = importlib.import_module("torque.state")
        self.mcp_mod = importlib.reload(importlib.import_module("torque.mcp"))
        self.communication_mod = importlib.import_module(
            "torque.server_communication"
        )
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        self.state.groups["g"] = []

        self.architect = self._add_agent("arch-1", "Architect", "architect")
        self.engineer = self._add_agent(
            "eng-1", "Engineer", "engineer", hired_by_architect_id=self.architect.id,
        )
        self.worker = self._add_agent(
            "worker-1", "Worker", "worker", owner_engineer_id=self.engineer.id,
        )
        self.parent = self.state.board_add_task(
            "Implement release", "g", lane="In Progress", id="parent",
            agent_id=self.worker.id, assigned_engineer_id=self.engineer.id,
        )
        # This is the legacy durable shape produced before Worker asks gained
        # an Engineer ownership stamp.  The reply Worker remains its sole
        # ownership anchor.
        self.ask = self.state.board_add_task(
            "Which release gate applies?", "g", lane="Backlog", id="ask",
            labels=["torque:human", "torque:derived", "torque:non-user-ask"],
            parent_task_id=self.parent.id, pipeline_depth=1,
            pipeline_root_id=self.parent.id, reply_agent_id=self.worker.id,
        )

    def _add_agent(self, agent_id, name, kind, **kwargs):
        cell = self.state_mod.AgentCell(
            id=agent_id, name=name, group="g", cell_type="agent", kind=kind,
            **kwargs,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["g"].append(cell.id)
        self.state._db_save_agent(cell)
        return cell

    async def _handle_command(self, payload):
        if payload.get("cmd") != "resolve_ask":
            return {"type": "error", "message": "Unexpected command"}
        return await self.communication_mod._resolve_human_ask_task(
            self.state,
            self.state.board_tasks[payload["id"]],
            payload.get("answer", ""),
            self._send_prompt,
        )

    async def _send_prompt(self, *_args, **_kwargs):
        self.fail("offline answer delivery must buffer rather than inject")

    async def _call(self, agent_id, name, arguments, request_id):
        handler = self.mcp_mod.create_mcp_handler(self._handle_command, self.state)
        return await handler(FakeRequest(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={"X-Torque-Cell-Id": agent_id},
        ))

    @staticmethod
    def _tool_text(response):
        result = response.payload["result"]
        return result, result["content"][0]["text"]

    @staticmethod
    def _freeze_self_task_update(engineer):
        engineer.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "engineer",
                "acl_mode": "allow",
                "capabilities": {"task.update": "self"},
            },
        }

    async def test_owned_engineer_resolves_worker_ask_and_releases_parent_cascade(self):
        # Before resolution, both the read surface and cascade see the same
        # open Worker ask.
        before = await self._call(
            self.architect.id, "agent_ask_get",
            {"engineer_id": self.engineer.id}, 1,
        )
        before_result, before_text = self._tool_text(before)
        self.assertFalse(before_result["isError"], before_text)
        before_payload = json.loads(before_text)
        self.assertEqual(before_payload["ask_level"], "worker")
        self.assertEqual(before_payload["ask_task_id"], self.ask.id)
        self.assertEqual(self.state.task_open_descendants(self.parent.id), [self.ask])

        # The Architect has a different, Engineer-level answer path.  Its
        # refusal identifies this worker/engineer level mismatch.
        wrong_level = await self._call(
            self.architect.id, "agent_ask_answer",
            {"engineer_id": self.engineer.id, "answer": "Use review."}, 2,
        )
        wrong_result, wrong_text = self._tool_text(wrong_level)
        self.assertTrue(wrong_result["isError"])
        self.assertIn("worker-level ask", wrong_text)
        self.assertIn("agent_ask_answer(task=ask)", wrong_text)

        # The owning Engineer has the typed resolution route.  This exercises
        # public MCP selection, the legacy reply-Worker authorization
        # fallback under a frozen self-only authority, reply delivery, state
        # change, and the normal Done cascade without a manual board move or
        # force.
        self._freeze_self_task_update(self.engineer)
        resolved = await self._call(
            self.engineer.id, "agent_ask_answer",
            {"task": self.ask.id, "answer": "Use the review gate."}, 3,
        )
        resolved_result, resolved_text = self._tool_text(resolved)
        self.assertFalse(resolved_result["isError"], resolved_text)
        self.assertEqual(json.loads(resolved_text)["type"], "ok")
        self.assertEqual(self.ask.lane, "Done")
        self.assertNotIn("torque:human", self.ask.labels)
        self.assertIn("torque:ask-resolved", self.ask.labels)
        self.assertEqual(self.parent.lane, "Done")
        self.assertEqual(self.state.task_open_descendants(self.parent.id), [])

        after = await self._call(
            self.architect.id, "agent_ask_get",
            {"engineer_id": self.engineer.id}, 4,
        )
        after_result, after_text = self._tool_text(after)
        self.assertFalse(after_result["isError"], after_text)
        after_payload = json.loads(after_text)
        self.assertEqual(after_payload["question"], "")
        self.assertNotIn("ask_task_id", after_payload)
        self.assertEqual(after_payload["note"], "No pending question for engineer.")

    async def test_legacy_worker_ask_refuses_other_engineer(self):
        other_engineer = self._add_agent("eng-2", "Other Engineer", "engineer")
        self._freeze_self_task_update(self.engineer)
        self._freeze_self_task_update(other_engineer)

        denied = await self._call(
            other_engineer.id, "agent_ask_answer",
            {"task": self.ask.id, "answer": "Use the review gate."}, 5,
        )
        denied_text = denied.payload["error"]["message"]
        self.assertIn("target is outside", denied_text)
        self.assertEqual(self.ask.lane, "Backlog")
        self.assertNotIn("torque:ask-resolved", self.ask.labels)

    async def test_legacy_worker_ask_refuses_stale_owner_and_non_ask_task(self):
        self._freeze_self_task_update(self.engineer)
        self.worker.owner_engineer_id = "removed-engineer"

        stale = await self._call(
            self.engineer.id, "agent_ask_answer",
            {"task": self.ask.id, "answer": "Use the review gate."}, 6,
        )
        stale_text = stale.payload["error"]["message"]
        self.assertIn("target is outside", stale_text)
        self.assertEqual(self.ask.lane, "Backlog")

        self.worker.owner_engineer_id = self.engineer.id
        ordinary_task = self.state.board_add_task(
            "Not a human ask", "g", lane="Backlog", id="ordinary-task",
            reply_agent_id=self.worker.id,
        )
        ordinary = await self._call(
            self.engineer.id, "agent_ask_answer",
            {"task": ordinary_task.id, "answer": "No action."}, 7,
        )
        ordinary_text = ordinary.payload["error"]["message"]
        self.assertIn("target is outside", ordinary_text)
        self.assertEqual(ordinary_task.lane, "Backlog")
