import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    async def _flush_session_wake_tasks(self):
        tasks = list(getattr(self.mcp_mod, "_SESSION_WAKE_TASKS", set()) or [])
        if tasks:
            await asyncio.gather(*tasks)

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

    async def test_dispatch_tool_forwards_task_artifact_uploads(self):
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
            return {
                "type": "task_artifact_uploaded",
                "task_id": "task-1",
                "artifact": {"filename": "report.txt"},
            }

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_task_upload_artifact",
            {
                "filename": "report.txt",
                "content_text": "hello",
                "artifact_type": "generated_doc",
                "summary": "test upload",
            },
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["artifact"]["filename"], "report.txt")
        self.assertEqual(
            calls,
            [{
                "cmd": "task_upload_artifact",
                "cell_id": "agent-1",
                "filename": "report.txt",
                "content_text": "hello",
                "artifact_type": "generated_doc",
                "summary": "test upload",
            }],
        )

    async def test_loom_reply_forwards_optional_task_id(self):
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
            return {"type": "ok", "task_id": "LOOM:1:2"}

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_reply",
            {"task": "LOOM:1:2", "message": "Rebased successfully"},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok", "task_id": "LOOM:1:2"})
        self.assertEqual(
            calls,
            [{
                "cmd": "ai_report",
                "cell_id": "agent-1",
                "action": "reply",
                "task_id": "LOOM:1:2",
                "message": "Rebased successfully",
            }],
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
                "retention_kind": "durable",
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["entry"]["id"], "mem-1")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_list",
            {
                "scope_kind": "group",
                "scope_ref": "g",
                "pinned_only": True,
                "linked_target_kind": "task",
                "linked_target_ref": "task-1",
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["type"], "memory_entries")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_read",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_pin",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_memory_link",
            {"entry_id": "mem-1", "target_kind": "task", "target_ref": "task-1"},
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
                    "retention_kind": "durable",
                },
                {
                    "cmd": "memory_list",
                    "cell_id": "agent-1",
                    "scope_kind": "group",
                    "scope_ref": "g",
                    "pinned_only": True,
                    "linked_target_kind": "task",
                    "linked_target_ref": "task-1",
                },
                {
                    "cmd": "memory_read",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
                {
                    "cmd": "memory_pin",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
                {
                    "cmd": "memory_link",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                    "target_kind": "task",
                    "target_ref": "task-1",
                },
                {
                    "cmd": "memory_unpin",
                    "cell_id": "agent-1",
                    "entry_id": "mem-1",
                },
            ],
        )

    async def test_create_mcp_handler_exposes_only_current_mcp_surfaces(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [weaver.id, architect.id, engineer.id, worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        listed_missing_header = await handler(
            FakeRequest({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        )
        tool_names = [
            tool["name"]
            for tool in listed_missing_header.payload["result"]["tools"]
        ]
        self.assertIn("loom_progress", tool_names)
        self.assertIn("loom_verify", tool_names)
        self.assertIn("loom_task_upload_artifact", tool_names)
        self.assertIn("loom_memory_publish", tool_names)
        self.assertIn("loom_memory_read", tool_names)
        self.assertIn("loom_memory_link", tool_names)
        self.assertNotIn("weaver_board_summary", tool_names)
        self.assertNotIn("engineer_board_summary", tool_names)
        self.assertNotIn("architect_board_summary", tool_names)
        self.assertNotIn("architect_workspace_overview", tool_names)

        listed_worker = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
                headers={"X-Loom-Cell-Id": worker.id},
            )
        )
        worker_tool_names = [
            tool["name"] for tool in listed_worker.payload["result"]["tools"]
        ]
        self.assertIn("loom_progress", worker_tool_names)
        self.assertNotIn("weaver_board_summary", worker_tool_names)
        self.assertNotIn("engineer_board_summary", worker_tool_names)
        self.assertNotIn("architect_board_summary", worker_tool_names)
        self.assertNotIn("architect_workspace_overview", worker_tool_names)

        listed_architect = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
                headers={"X-Loom-Cell-Id": architect.id},
            )
        )
        architect_tool_names = [
            tool["name"] for tool in listed_architect.payload["result"]["tools"]
        ]
        self.assertIn("architect_workspace_overview", architect_tool_names)
        self.assertIn("architect_board_summary", architect_tool_names)
        self.assertIn("architect_events_recent", architect_tool_names)
        self.assertIn("architect_deploy_state", architect_tool_names)
        self.assertIn("architect_task_chain", architect_tool_names)
        self.assertIn("architect_task_move", architect_tool_names)
        self.assertIn("architect_decision_create", architect_tool_names)
        self.assertIn("architect_engineer_hire", architect_tool_names)
        self.assertIn("architect_engineer_message", architect_tool_names)
        self.assertIn("architect_engineer_journal_read", architect_tool_names)
        self.assertIn("architect_engineer_pending_question", architect_tool_names)
        self.assertIn("architect_ask", architect_tool_names)
        self.assertNotIn("weaver_board_summary", architect_tool_names)
        self.assertNotIn("engineer_board_summary", architect_tool_names)

        listed_engineer = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 121, "method": "tools/list"},
                headers={"X-Loom-Cell-Id": engineer.id},
            )
        )
        engineer_tool_names = [
            tool["name"] for tool in listed_engineer.payload["result"]["tools"]
        ]
        self.assertIn("engineer_board_summary", engineer_tool_names)
        self.assertIn("engineer_task_reassign", engineer_tool_names)
        self.assertIn("engineer_task_verify", engineer_tool_names)
        self.assertIn("engineer_message_architect", engineer_tool_names)
        self.assertIn("engineer_reply", engineer_tool_names)
        self.assertNotIn("weaver_board_summary", engineer_tool_names)
        self.assertNotIn("architect_workspace_overview", engineer_tool_names)

        listed_weaver = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 13, "method": "tools/list"},
                headers={"X-Loom-Cell-Id": weaver.id},
            )
        )
        weaver_tool_names = [
            tool["name"] for tool in listed_weaver.payload["result"]["tools"]
        ]
        self.assertNotIn("weaver_board_summary", weaver_tool_names)
        self.assertNotIn("engineer_board_summary", weaver_tool_names)
        self.assertNotIn("architect_board_summary", weaver_tool_names)

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

        removed_alias = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "weaver_agents_list", "arguments": {}},
                }
            )
        )
        self.assertEqual(
            removed_alias.payload["error"]["message"],
            (
                "weaver_* MCP tools were removed; use engineer_* or architect_* "
                "instead (for example engineer_agents_list)"
            ),
        )

        denied_architect_summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {"name": "architect_board_summary", "arguments": {}},
                },
                headers={"X-Loom-Cell-Id": worker.id},
            )
        )
        self.assertTrue(denied_architect_summary.payload["result"]["isError"])
        self.assertIn(
            "architect tools are only available inside a Loom-managed architect session",
            denied_architect_summary.payload["result"]["content"][0]["text"],
        )

        summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "engineer_board_summary", "arguments": {}},
                },
                headers={"X-Loom-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(summary.payload["result"]["isError"])
        parsed = json.loads(summary.payload["result"]["content"][0]["text"])
        self.assertEqual(parsed["group"], "g")
        self.assertEqual(calls, [])

    def test_loom_ask_tool_description_marks_it_as_blocking(self):
        ask_tool = next(
            tool for tool in self.mcp_mod.TOOLS
            if tool["name"] == "loom_ask"
        )
        reply_tool = next(
            tool for tool in self.mcp_mod.TOOLS
            if tool["name"] == "loom_reply"
        )

        self.assertIn("blocking human decision or approval", ask_tool["description"])
        self.assertIn("Do not use this for status updates", ask_tool["description"])
        self.assertIn("include the task id", reply_tool["description"])
        self.assertIn("task", reply_tool["inputSchema"]["properties"])

    async def test_loom_context_includes_combined_task_artifacts(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        task = state.board_add_task(
            "Review artifact uploads",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id=cell.id,
            attachments=[{"path": "/tmp/task-1/image.png", "filename": "image.png"}],
            artifacts=[{"type": "log", "title": "pytest", "path": "/tmp/task-1/pytest.log"}],
        )
        self.assertIsNotNone(task)

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_context",
            {},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        payload = json.loads(text)
        self.assertEqual(len(payload["tasks"]["task-1"]["task_artifacts"]), 2)
        self.assertEqual(payload["tasks"]["task-1"]["task_artifacts"][0]["url"], "/attachments/task-1/image.png")

    async def test_loom_context_includes_upstream_artifacts_for_derived_tasks(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        parent = self.state_mod.BoardTask(
            id="task-parent",
            task="Research auth patch",
            group="g",
            lane="Done",
            artifacts=[{
                "type": "generated_doc",
                "title": "Implementation plan",
                "path": "/tmp/task-parent/plan.md",
            }],
        )
        child = self.state_mod.BoardTask(
            id="task-child",
            task="Implement auth patch",
            group="g",
            lane="In Progress",
            agent_id=cell.id,
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        state.board_tasks[parent.id] = parent
        state.board_tasks[child.id] = child

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_mod._dispatch_tool(
            "loom_context",
            {},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        payload = json.loads(text)
        upstream = payload["tasks"]["task-child"]["upstream_artifacts"]
        self.assertEqual(len(upstream), 1)
        self.assertEqual(upstream[0]["source_task_id"], "task-parent")
        self.assertEqual(upstream[0]["source_task_label"], "Research auth patch")
        self.assertEqual(upstream[0]["url"], "/attachments/task-parent/plan.md")

    async def test_architect_session_wake_emits_once_per_mcp_session_in_background(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "loom_context", "arguments": {}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)), \
                    mock.patch("time.time", return_value=1000.0):
                payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=architect.id,
                    handle_command=fake_handle_command,
                    state=state,
                    mcp_session_id="mcp-session-1",
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["result"]["isError"])
                self.assertEqual(state.architect_journal_read(architect.id, limit=10), [])
                await self._flush_session_wake_tasks()
                entries = state.architect_journal_read(architect.id, limit=10)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["type"], "observation")
            self.assertEqual(
                entries[0]["entry"],
                "Session wake — previous checkpoint none recorded, architect id arch-1, group g.",
            )

            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)), \
                    mock.patch("time.time", return_value=1010.0):
                payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=architect.id,
                    handle_command=fake_handle_command,
                    state=state,
                    mcp_session_id="mcp-session-1",
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["result"]["isError"])
                await self._flush_session_wake_tasks()
                entries = state.architect_journal_read(architect.id, limit=10)

            self.assertEqual(len(entries), 1)

    async def test_architect_manual_wake_entry_dedupes_auto_wake(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "architect_journal",
                "arguments": {
                    "type": "observation",
                    "entry": "Session wake 2026-04-22",
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)), \
                    mock.patch("time.time", return_value=2000.0):
                payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=architect.id,
                    handle_command=fake_handle_command,
                    state=state,
                    mcp_session_id="mcp-session-manual",
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["result"]["isError"])
                await self._flush_session_wake_tasks()
                entries = state.architect_journal_read(architect.id, limit=10)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["entry"], "Session wake 2026-04-22")

    async def test_engineer_session_wake_includes_identity_and_repeats_for_new_session(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Builder",
            slug="builder",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=engineer.id
        )
        journal_entries = []

        def fake_journal_append(group, entry_type, entry, author_cell_id=""):
            record = {
                "id": len(journal_entries) + 1,
                "group": group,
                "timestamp": self.mcp_mod.time.time(),
                "type": entry_type,
                "entry": entry,
                "author_cell_id": author_cell_id,
            }
            journal_entries.insert(0, record)
            return record

        def fake_journal_read(group, limit=20, entry_type="", author_cell_id=""):
            filtered = [
                dict(item)
                for item in journal_entries
                if item["group"] == group
                and (not entry_type or item["type"] == entry_type)
                and (
                    not author_cell_id
                    or item.get("author_cell_id", "") == author_cell_id
                )
            ]
            return filtered[:limit]

        state.journal_append = fake_journal_append
        state.journal_read = fake_journal_read

        async def fake_handle_command(payload):
            if payload.get("cmd") == "weaver_journal_append":
                return fake_journal_append(
                    payload["group"],
                    payload["entry_type"],
                    payload["entry"],
                    author_cell_id=payload.get("author_cell_id", ""),
                )
            self.fail(f"Unexpected handle_command call: {payload}")

        with mock.patch("time.time", return_value=100.0):
            state.journal_append(
                "g",
                "checkpoint",
                "Prior checkpoint",
                author_cell_id=engineer.id,
            )

        body = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "engineer_session_map", "arguments": {}},
        }

        with mock.patch("time.time", return_value=4000.0):
            payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=engineer.id,
                handle_command=fake_handle_command,
                state=state,
                mcp_session_id="eng-session-1",
            )
            self.assertEqual(status, 200)
            self.assertFalse(payload["result"]["isError"])
            await self._flush_session_wake_tasks()

        entries = state.journal_read("g", limit=10, author_cell_id=engineer.id)
        wake_entries = [
            entry for entry in entries
            if str(entry.get("entry", "")).startswith("Session wake —")
        ]
        self.assertEqual(len(wake_entries), 1)
        self.assertIn("engineer id eng-1", wake_entries[0]["entry"])
        self.assertIn("name Builder", wake_entries[0]["entry"])
        self.assertIn("slug builder", wake_entries[0]["entry"])
        self.assertIn("group g.", wake_entries[0]["entry"])
        self.assertIn("previous checkpoint 1970-01-01 00:01 UTC", wake_entries[0]["entry"])
        self.assertIn("(1h ago)", wake_entries[0]["entry"])

        with mock.patch("time.time", return_value=8000.0):
            payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=engineer.id,
                handle_command=fake_handle_command,
                state=state,
                mcp_session_id="eng-session-2",
            )
            self.assertEqual(status, 200)
            self.assertFalse(payload["result"]["isError"])
            await self._flush_session_wake_tasks()

        wake_entries = [
            entry for entry in state.journal_read("g", limit=10, author_cell_id=engineer.id)
            if str(entry.get("entry", "")).startswith("Session wake —")
        ]
        self.assertEqual(len(wake_entries), 2)

    async def test_worker_session_does_not_auto_journal_session_wake(self):
        state = self.state_mod.MatrixState()
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[worker.id] = worker
        state.groups["g"] = [worker.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "loom_context", "arguments": {}},
        }

        with mock.patch("time.time", return_value=9000.0):
            payload, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=worker.id,
                handle_command=fake_handle_command,
                state=state,
                mcp_session_id="worker-session-1",
            )
            self.assertEqual(status, 200)
            self.assertFalse(payload["result"]["isError"])
            await self._flush_session_wake_tasks()

        self.assertEqual(state.journal_read("g", limit=10), [])
