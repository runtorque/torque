"""Tests for engineer-side deliverable awareness (LOOM:241 follow-up).

When a task carries ``deliverable_required: true``, the engineer needs
to know the deliverable contract and the engineer-side
``engineer_task_upload_artifact`` tool — analogous to the worker
dispatch postscript's deliverable contract block.

Surfaces under test:
- ``engineer_task_show`` response includes the awareness block.
- ``architect_task_create`` with a deliverable surfaces the block in
  the response.
- Architect-to-engineer messages that reference a deliverable task
  include the awareness block in the recipient's message body.
- Tasks WITHOUT a deliverable contract get no awareness block (no
  regression on the read paths).
"""

import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EngineerDeliverableAwarenessHelperTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        from loom.state import BoardTask
        self.BoardTask = BoardTask
        from loom.server_prompts import build_engineer_deliverable_awareness
        self._build = build_engineer_deliverable_awareness

    def test_no_block_when_not_required(self):
        task = self.BoardTask(id="t1", task="Plain task", group="g")
        self.assertEqual(self._build(task), "")

    def test_block_includes_engineer_tool_with_task_id(self):
        task = self.BoardTask(
            id="LOOM:99",
            task="Audit MCP surface",
            group="g",
            deliverable_required=True,
            deliverable_type="report",
            deliverable_format="markdown",
            deliverable_artifact_title="Audit report",
        )
        block = self._build(task)
        self.assertIn("Deliverable contract on this task", block)
        self.assertIn("engineer_task_upload_artifact", block)
        self.assertIn('task_id="LOOM:99"', block)
        self.assertIn("Audit report", block)
        self.assertIn("report", block)
        self.assertIn("markdown", block)
        # Worker contract reference and gate note both surface.
        self.assertIn("loom_task_upload_artifact", block)
        self.assertIn("engineer_task_resolve", block)
        # Lazy-load hint (the tool is in ENGINEER_DEFERRED_TOOL_NAMES).
        self.assertIn("engineer_tool_search", block)
        self.assertIn("select:engineer_task_upload_artifact", block)

    def test_block_uses_deliverable_word_for_freeform_types(self):
        task = self.BoardTask(
            id="t-2",
            task="Investigate",
            group="g",
            deliverable_required=True,
            deliverable_type="diagnostic_log",
        )
        block = self._build(task)
        self.assertIn("<your full diagnostic_log>", block)

    def test_block_falls_back_to_task_title_when_no_artifact_title(self):
        task = self.BoardTask(
            id="t-3",
            task="Some task",
            group="g",
            deliverable_required=True,
            deliverable_type="report",
        )
        block = self._build(task)
        self.assertIn('title="Some task"', block)


class EngineerDeliverableAwarenessSurfaceTests(unittest.IsolatedAsyncioTestCase):
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
        self.state.board_lanes = [
            "Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["loom"] = []
        self.state._db_save_groups()

    def _add_architect(self, agent_id, name):
        cell = self.state_mod.AgentCell(
            id=agent_id, name=name, slug=name.lower(),
            group="loom", cell_type="agent", kind="architect",
            status="running", persistent=True,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["loom"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_engineer(self, agent_id, name, *, hired_by_architect_id=""):
        cell = self.state_mod.AgentCell(
            id=agent_id, name=name, slug=name.lower().replace(" ", "-"),
            group="loom", cell_type="agent", kind="engineer",
            status="running", persistent=True,
            hired_by_architect_id=hired_by_architect_id,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["loom"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_task(self, task_id, title, **kwargs):
        return self.state.board_add_task(
            title,
            kwargs.pop("group", "loom"),
            lane=kwargs.pop("lane", "Backlog"),
            id=task_id,
            **kwargs,
        )

    async def _handle_command(self, payload):
        if payload["cmd"] == "board_add_task":
            group = payload.get("group", "")
            gs = self.state.get_group_settings(group)
            extra = {}
            deliverable = payload.get("deliverable")
            if isinstance(deliverable, dict):
                extra["deliverable_required"] = bool(
                    deliverable.get("required", False))
                extra["deliverable_type"] = str(
                    deliverable.get("type", "") or "")
                extra["deliverable_format"] = str(
                    deliverable.get("format", "") or "")
                extra["deliverable_artifact_title"] = str(
                    deliverable.get("artifact_title", "") or "")
            task = self.state.board_add_task(
                payload.get("task", ""),
                group,
                lane=payload.get("lane", ""),
                description=payload.get("description", ""),
                labels=payload.get("labels", []),
                action_name=payload.get("action_name", "")
                or gs.board_default_action,
                assigned_engineer_id=payload.get("assigned_engineer_id", ""),
                created_by_engineer_id=payload.get("created_by_engineer_id", ""),
                **extra,
            )
            if not task:
                return {"type": "error", "message": "task create failed"}
            return {
                "type": "board_task_added",
                "task_id": task.id,
                "title": task.task,
            }
        if payload["cmd"] == "board_update_task":
            self.state.board_update_task(
                payload["id"],
                **{k: v for k, v in payload.items() if k not in {"cmd", "id"}},
            )
            return {"type": "ok"}
        if payload["cmd"] == "inject_mcp_message":
            return {"type": "ok", "delivered": True}
        return {"type": "ok"}

    async def _call_engineer(self, tool_name, args, caller_id):
        return await self.mcp_engineer_mod._dispatch_engineer_tool(
            tool_name, args, self._handle_command, self.state,
            caller_id=caller_id,
        )

    async def _call_architect(self, tool_name, args, caller_id):
        return await self.mcp_architect_mod._dispatch_architect_tool(
            tool_name, args, self._handle_command, self.state,
            caller_id=caller_id,
        )

    async def test_engineer_task_show_includes_awareness_for_deliverable_task(self):
        engineer = self._add_engineer("eng-1", "Eng")
        task = self._add_task(
            "LOOM:500", "Audit feature",
            assigned_engineer_id=engineer.id,
            deliverable_required=True,
            deliverable_type="report",
            deliverable_format="markdown",
            deliverable_artifact_title="Audit report",
        )
        text, is_error = await self._call_engineer(
            "engineer_task_show", {"task": task.id}, engineer.id,
        )
        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertIn("deliverable_awareness", data)
        block = data["deliverable_awareness"]
        self.assertIn("Deliverable contract on this task", block)
        self.assertIn("engineer_task_upload_artifact", block)
        self.assertIn('task_id="LOOM:500"', block)
        self.assertIn("Audit report", block)
        self.assertIn("engineer_tool_search", block)

    async def test_engineer_task_show_no_awareness_for_plain_task(self):
        engineer = self._add_engineer("eng-1", "Eng")
        task = self._add_task(
            "LOOM:501", "Plain task",
            assigned_engineer_id=engineer.id,
        )
        text, is_error = await self._call_engineer(
            "engineer_task_show", {"task": task.id}, engineer.id,
        )
        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertNotIn("deliverable_awareness", data)

    async def test_architect_task_create_with_deliverable_returns_awareness(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Alice", hired_by_architect_id=architect.id,
        )
        text, is_error = await self._call_architect(
            "architect_task_create",
            {
                "title": "Investigate regression",
                "group": "loom",
                "assigned_engineer_id": engineer.id,
                "deliverable": {
                    "required": True,
                    "type": "report",
                    "format": "markdown",
                    "artifact_title": "Regression report",
                },
            },
            architect.id,
        )
        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertIn("deliverable_awareness", data)
        block = data["deliverable_awareness"]
        self.assertIn("Deliverable contract on this task", block)
        self.assertIn("engineer_task_upload_artifact", block)
        self.assertIn("Regression report", block)
        self.assertIn("engineer_tool_search", block)

    async def test_architect_task_create_without_deliverable_omits_awareness(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Alice", hired_by_architect_id=architect.id,
        )
        text, is_error = await self._call_architect(
            "architect_task_create",
            {
                "title": "Plain task",
                "group": "loom",
                "assigned_engineer_id": engineer.id,
            },
            architect.id,
        )
        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertNotIn("deliverable_awareness", data)

    async def test_architect_message_referencing_deliverable_task_includes_block(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Alice", hired_by_architect_id=architect.id,
        )
        task = self._add_task(
            "LOOM:600", "Audit feature",
            assigned_engineer_id=engineer.id,
            deliverable_required=True,
            deliverable_type="report",
            deliverable_format="markdown",
            deliverable_artifact_title="Audit report",
        )
        text, is_error = await self._call_architect(
            "architect_engineer_message",
            {
                "engineer_id": engineer.id,
                "message": (
                    "Please pick up LOOM:600 and start with the rendering audit."
                ),
            },
            architect.id,
        )
        self.assertFalse(is_error, text)
        # The recipient's inbox entry has the awareness block appended.
        recipient_msg = engineer.mcp_messages[0]["message"]
        self.assertIn("LOOM:600", recipient_msg)
        self.assertIn("Deliverable contract on this task", recipient_msg)
        self.assertIn("engineer_task_upload_artifact", recipient_msg)
        self.assertIn('task_id="LOOM:600"', recipient_msg)
        # Sender entry stays clean (no awareness pollution on architect side).
        sender_msg = architect.mcp_messages[0]["message"]
        self.assertNotIn("Deliverable contract on this task", sender_msg)

    async def test_architect_message_without_task_reference_no_block(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Alice", hired_by_architect_id=architect.id,
        )
        # Task with deliverable exists but is not mentioned in the message.
        self._add_task(
            "LOOM:601", "Has a deliverable",
            assigned_engineer_id=engineer.id,
            deliverable_required=True,
            deliverable_type="report",
        )
        text, is_error = await self._call_architect(
            "architect_engineer_message",
            {
                "engineer_id": engineer.id,
                "message": "General check-in, no task referenced.",
            },
            architect.id,
        )
        self.assertFalse(is_error, text)
        recipient_msg = engineer.mcp_messages[0]["message"]
        self.assertNotIn("Deliverable contract on this task", recipient_msg)

    async def test_architect_message_referencing_plain_task_no_block(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Alice", hired_by_architect_id=architect.id,
        )
        self._add_task(
            "LOOM:602", "No deliverable",
            assigned_engineer_id=engineer.id,
        )
        text, is_error = await self._call_architect(
            "architect_engineer_message",
            {
                "engineer_id": engineer.id,
                "message": "Please look into LOOM:602 when free.",
            },
            architect.id,
        )
        self.assertFalse(is_error, text)
        recipient_msg = engineer.mcp_messages[0]["message"]
        self.assertIn("LOOM:602", recipient_msg)
        self.assertNotIn("Deliverable contract on this task", recipient_msg)


if __name__ == "__main__":
    unittest.main()
