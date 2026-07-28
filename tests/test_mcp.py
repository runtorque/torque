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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_mod = importlib.import_module("torque.mcp")
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
            "torque_progress",
            {"message": "Running tests"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok"})

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_verify",
            {
                "state": "pending",
                "mode": "deploy",
                "notes": "Need manual smoke after deploy",
                "tests_run": "python3 -m unittest",
                "manual_smoke_done": True,
                "deploy_needed": True,
                "deploy_attempted": True,
                "human_validation_pending": "Confirm dashboard loads",
                "test_outcome": "unrelated_flake_accepted",
                "full_suite_attempted": True,
                "unrelated_flake_accepted": True,
                "isolated_rerun_evidence": "focused rerun passed",
                "reviewer_acceptance": "accepted_flake_evidence",
                "live_smoke_pending": True,
            },
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok"})

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_derive",
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
                    "test_outcome": "unrelated_flake_accepted",
                    "full_suite_attempted": True,
                    "unrelated_flake_accepted": True,
                    "isolated_rerun_evidence": "focused rerun passed",
                    "reviewer_acceptance": "accepted_flake_evidence",
                    "live_smoke_pending": True,
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

    async def test_direct_mcp_call_notifies_observer_for_agent_mcp_panel(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell
        observations = []

        async def fake_handle_command(payload):
            raise AssertionError(f"unexpected command: {payload}")

        async def observe_mcp_call(observation):
            observations.append(dict(observation))

        body = {
            "jsonrpc": "2.0",
            "id": "ctx-1",
            "method": "tools/call",
            "params": {
                "name": "torque_context",
                "arguments": {"_torque_idempotency_key": "hidden"},
            },
        }
        response, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            body,
            cell_id=cell.id,
            handle_command=fake_handle_command,
            state=state,
            mcp_session_id="sess-1",
            mcp_call_observer=observe_mcp_call,
        )

        self.assertEqual(status, 200)
        self.assertNotIn("error", response)
        self.assertEqual(len(observations), 1)
        observed = observations[0]
        self.assertEqual(observed["cell_id"], "agent-1")
        self.assertEqual(observed["tool_name"], "mcp__torque__torque_context")
        self.assertEqual(observed["raw_tool_name"], "torque_context")
        self.assertEqual(observed["hook_event_name"], "PostToolUse")
        self.assertEqual(observed["session_id"], "sess-1")
        self.assertEqual(observed["request_id"], "ctx-1")
        self.assertEqual(observed["arguments"], {})
        self.assertFalse(observed["is_error"])
        self.assertIn("result", observed)

    async def test_public_transport_hides_target_existence_for_projected_tools(self):
        """Projected canonical calls deny missing and out-of-scope targets alike."""
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        restricted_architect = self.state_mod.AgentCell(
            id="architect-restricted",
            name="Restricted Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        peer = self.state_mod.AgentCell(
            id="architect-peer",
            name="Peer Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        child_engineer = self.state_mod.AgentCell(
            id="engineer-child",
            name="Child Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=restricted_architect.id,
        )
        restricted_architect.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {
                    "task.read": "self",
                    "task.update": "self",
                    "task.mark_covered": "self",
                    "task.reassign": "children",
                },
            },
        }
        for cell in (
            architect,
            restricted_architect,
            worker,
            peer,
            child_engineer,
        ):
            state.agents[cell.id] = cell
        state.groups["g"] = [
            architect.id,
            restricted_architect.id,
            worker.id,
            peer.id,
            child_engineer.id,
        ]
        peer_task = self.state_mod.BoardTask(
            id="TORQUE:peer",
            task="Peer task",
            group="g",
            lane="Backlog",
            created_by_architect_id=peer.id,
        )
        own_task = self.state_mod.BoardTask(
            id="TORQUE:own",
            task="Own task",
            group="g",
            lane="Backlog",
            created_by_architect_id=restricted_architect.id,
        )
        child_task = self.state_mod.BoardTask(
            id="TORQUE:child",
            task="Child task",
            group="g",
            lane="Backlog",
            assigned_engineer_id=child_engineer.id,
        )
        for task in (peer_task, own_task, child_task):
            state.board_tasks[task.id] = task
        before_tasks = {
            task.id: (task.lane, dict(task.completion_evidence), list(task.messages))
            for task in state.board_tasks.values()
        }
        calls = []

        async def unexpected_command(payload):
            calls.append(dict(payload))
            self.fail(f"Unexpected command: {payload}")

        handler = self.mcp_mod.create_mcp_handler(unexpected_command, state)

        async def call(request_id, name, arguments, *, cell_id=restricted_architect.id):
            return await handler(FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers={"X-Torque-Cell-Id": cell_id},
            ))

        listed = await handler(FakeRequest(
            {"jsonrpc": "2.0", "id": "listed", "method": "tools/list"},
            headers={"X-Torque-Cell-Id": restricted_architect.id},
        ))
        restricted_names = {
            tool["name"] for tool in listed.payload["result"]["tools"]
        }
        self.assertTrue(
            {"task_get", "task_claim", "task_mark_covered", "task_reassign"}
            <= restricted_names
        )
        self.assertIn("task_coverage_reconcile", restricted_names)

        peer_target = await call("peer", "task_get", {"task": peer_task.id})
        missing_task = await call("missing-task", "task_get", {"task": "TORQUE:missing"})
        unfrozen_missing_task = await call(
            "unfrozen-missing-task",
            "task_get",
            {"task": "TORQUE:missing"},
            cell_id=architect.id,
        )
        missing_covering = await call(
            "missing-covering",
            "task_mark_covered",
            {"task": own_task.id, "covering_task": "TORQUE:missing-covering"},
        )
        peer_primary_covered = await call(
            "peer-primary-covered",
            "task_mark_covered",
            {"task": peer_task.id, "covering_task": own_task.id},
        )
        missing_primary_covered = await call(
            "missing-primary-covered",
            "task_mark_covered",
            {"task": "TORQUE:missing-primary", "covering_task": own_task.id},
        )
        peer_claim = await call(
            "peer-claim",
            "task_claim",
            {"task": peer_task.id},
        )
        missing_claim = await call(
            "missing-claim",
            "task_claim",
            {"task": "TORQUE:missing-claim"},
        )
        coverage_peer_second = await call(
            "coverage-peer-second",
            "task_coverage_reconcile",
            {"task_ids": [own_task.id, peer_task.id]},
        )
        coverage_missing_first = await call(
            "coverage-missing-first",
            "task_coverage_reconcile",
            {"task_ids": ["TORQUE:missing-coverage", own_task.id]},
        )
        missing_first_target = await call(
            "missing-first-target",
            "task_reassign",
            {"task": "TORQUE:missing", "new_engineer_id": child_engineer.id},
        )
        missing_second_target = await call(
            "missing-second-target",
            "task_reassign",
            {"new_engineer_id": "engineer-missing", "task": child_task.id},
        )

        def error_text(response):
            self.assertIn("error", response.payload)
            return response.payload["error"]["message"]

        expected = {
            "task_get": "Known tool is not authorized: task_get",
            "task_mark_covered": "Known tool is not authorized: task_mark_covered",
            "task_reassign": "Known tool is not authorized: task_reassign",
        }
        peer_text = error_text(peer_target)
        missing_task_text = error_text(missing_task)
        unfrozen_missing_task_text = error_text(unfrozen_missing_task)
        missing_covering_text = error_text(missing_covering)
        peer_primary_covered_text = error_text(peer_primary_covered)
        missing_primary_covered_text = error_text(missing_primary_covered)
        peer_claim_text = error_text(peer_claim)
        missing_claim_text = error_text(missing_claim)
        coverage_peer_second_text = error_text(coverage_peer_second)
        coverage_missing_first_text = error_text(coverage_missing_first)
        missing_first_text = error_text(missing_first_target)
        missing_second_text = error_text(missing_second_target)
        self.assertEqual(expected["task_get"], peer_text)
        self.assertEqual(peer_text, missing_task_text)
        self.assertEqual(peer_text, unfrozen_missing_task_text)
        self.assertEqual(expected["task_mark_covered"], missing_covering_text)
        self.assertEqual(missing_covering_text, peer_primary_covered_text)
        self.assertEqual(peer_primary_covered_text, missing_primary_covered_text)
        self.assertEqual(
            "Known tool is not authorized: task_claim",
            peer_claim_text,
        )
        self.assertEqual(peer_claim_text, missing_claim_text)
        self.assertEqual(
            "Known tool is not authorized: task_coverage_reconcile",
            coverage_peer_second_text,
        )
        self.assertEqual(coverage_peer_second_text, coverage_missing_first_text)
        self.assertEqual(expected["task_reassign"], missing_first_text)
        self.assertEqual(missing_first_text, missing_second_text)
        self.assertEqual([], calls)
        self.assertEqual(
            before_tasks,
            {
                task.id: (task.lane, dict(task.completion_evidence), list(task.messages))
                for task in state.board_tasks.values()
            },
        )

        unknown = await call("unknown", "not_a_tool", {})
        hidden = await call(
            "hidden",
            "task_coverage_reconcile",
            {"task_ids": [peer_task.id]},
            cell_id=worker.id,
        )
        unavailable = await call(
            "unavailable",
            "task_coverage_reconcile",
            {"task_ids": [peer_task.id]},
            cell_id=architect.id,
        )
        self.assertIn("Unknown tool: not_a_tool", error_text(unknown))
        self.assertIn("Unknown tool: task_coverage_reconcile", error_text(hidden))
        self.assertNotIn("error", unavailable.payload)
        unavailable_payload = json.loads(
            unavailable.payload["result"]["content"][0]["text"]
        )
        self.assertTrue(unavailable.payload["result"]["isError"])
        self.assertEqual(
            "recognized_but_not_yet_available",
            unavailable_payload["status"],
        )
        self.assertEqual(
            ["TORQUE:1228 is merged", "the caller session is relaunched"],
            unavailable_payload["activation_conditions"],
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
            "torque_task_upload_artifact",
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

    async def test_torque_reply_forwards_optional_task_id(self):
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
            return {"type": "ok", "task_id": "TORQUE:1:2"}

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_reply",
            {"task": "TORQUE:1:2", "message": "Rebased successfully"},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        self.assertEqual(json.loads(text), {"type": "ok", "task_id": "TORQUE:1:2"})
        self.assertEqual(
            calls,
            [{
                "cmd": "ai_report",
                "cell_id": "agent-1",
                "action": "reply",
                "task_id": "TORQUE:1:2",
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
            "torque_memory_publish",
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
            "torque_memory_list",
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
            "torque_memory_read",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_pin",
            {"entry_id": "mem-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_link",
            {"entry_id": "mem-1", "target_kind": "task", "target_ref": "task-1"},
            cell.id,
            fake_handle_command,
            state,
        )
        self.assertFalse(is_error)

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_memory_unpin",
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

    async def test_torque_message_user_persists_worker_direct_message(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            worker = self.state_mod.AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                cell_type="agent",
            )
            state.agents[worker.id] = worker
            state.groups["g"] = [worker.id]

            calls = []

            async def fake_handle_command(payload):
                calls.append(dict(payload))
                return {"type": "ok"}

            text, is_error = await self.mcp_mod._dispatch_tool(
                "torque_message_user",
                {
                    "message": "I have a user-visible update.",
                    "thread_id": "client-thread-is-normalized",
                },
                worker.id,
                fake_handle_command,
                state,
            )

            self.assertFalse(is_error)
            payload = json.loads(text)
            row = db.load_direct_message(payload["message_id"])
            self.assertIsNotNone(row)
            self.assertEqual(row["sender_id"], worker.id)
            self.assertEqual(row["sender_kind"], "worker")
            self.assertEqual(row["sender_name"], "Worker")
            self.assertEqual(row["recipient_id"], "user")
            self.assertEqual(row["recipient_kind"], "user")
            self.assertEqual(row["recipient_name"], "User")
            self.assertEqual(row["message_type"], "message")
            self.assertFalse(row["blocking"])
            self.assertEqual(row["delivery_state"], "delivered")
            self.assertEqual(row["read_at"], 0)
            self.assertEqual(
                row["thread_id"],
                db_mod.canonical_user_agent_thread_id(worker.id),
            )
            self.assertEqual(payload["thread_id"], row["thread_id"])
            self.assertEqual(payload["delivery"]["state"], "delivered")
            self.assertFalse(payload["deduped"])
            self.assertEqual(calls, [])
            self.assertEqual(len(state.board_tasks), 0)
            self.assertEqual(
                state.direct_messages_by_agent[worker.id][0]["id"],
                row["id"],
            )

            text, is_error = await self.mcp_mod._dispatch_tool(
                "torque_message_user",
                {"message": "No explicit thread is needed."},
                worker.id,
                fake_handle_command,
                state,
            )
            self.assertFalse(is_error)
            omitted_payload = json.loads(text)
            omitted_row = db.load_direct_message(omitted_payload["message_id"])
            self.assertEqual(
                omitted_row["thread_id"],
                db_mod.canonical_user_agent_thread_id(worker.id),
            )
            self.assertEqual(omitted_payload["thread_id"], omitted_row["thread_id"])
            db.close()

    async def test_torque_message_user_notifies_user_best_effort(self):
        db_mod = importlib.import_module("torque.db")
        notifications_mod = importlib.import_module("torque.notifications")
        notifications_mod = importlib.reload(notifications_mod)
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            worker = self.state_mod.AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                cell_type="agent",
                kind="worker",
            )
            state.agents[worker.id] = worker
            state.groups["g"] = [worker.id]
            state.group_settings["g"] = self.state_mod.GroupSettings(
                notifications=True,
                notify_on_attention=True,
            )
            manager = notifications_mod.NotificationManager(state)
            state.notification_manager = manager
            manager.start()

            async def fake_handle_command(_payload):
                return {"type": "ok"}

            sent = []
            orig_send = notifications_mod._send_notification

            async def fake_send(title, body):
                sent.append((title, body))

            notifications_mod._send_notification = fake_send
            try:
                text, is_error = await self.mcp_mod._dispatch_tool(
                    "torque_message_user",
                    {"message": "Visible update\nMore detail"},
                    worker.id,
                    fake_handle_command,
                    state,
                )
                await asyncio.sleep(0)
            finally:
                notifications_mod._send_notification = orig_send

            self.assertFalse(is_error, text)
            payload = json.loads(text)
            self.assertIsNotNone(db.load_direct_message(payload["message_id"]))
            self.assertEqual(
                sent,
                [("Torque message from Worker", "Worker: Visible update")],
            )

            state.group_settings["g"].notifications = False
            notifications_mod._send_notification = fake_send
            try:
                text, is_error = await self.mcp_mod._dispatch_tool(
                    "torque_message_user",
                    {"message": "Suppressed while disabled"},
                    worker.id,
                    fake_handle_command,
                    state,
                )
                await asyncio.sleep(0)
            finally:
                notifications_mod._send_notification = orig_send

            self.assertFalse(is_error, text)
            self.assertEqual(len(sent), 1)

            state.group_settings["g"].notifications = True

            async def failing_send(_title, _body):
                raise RuntimeError("notification unavailable")

            notifications_mod._send_notification = failing_send
            try:
                with self.assertLogs("torque", level="ERROR"):
                    text, is_error = await self.mcp_mod._dispatch_tool(
                        "torque_message_user",
                        {"message": "Persist despite notification failure"},
                        worker.id,
                        fake_handle_command,
                        state,
                    )
                    await asyncio.sleep(0)
            finally:
                notifications_mod._send_notification = orig_send

            self.assertFalse(is_error, text)
            payload = json.loads(text)
            self.assertIsNotNone(db.load_direct_message(payload["message_id"]))
            db.close()

    async def test_torque_stop_user_message_loop_only_stops_callers_loop(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            worker = self.state_mod.AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                cell_type="agent",
                kind="worker",
            )
            other = self.state_mod.AgentCell(
                id="worker-2",
                name="Other",
                group="g",
                cell_type="agent",
                kind="worker",
            )
            state.agents[worker.id] = worker
            state.agents[other.id] = other
            state.groups["g"] = [worker.id, other.id]
            loop = state.agent_message_loop_add(
                agent_id=worker.id,
                group_name="g",
                interval_seconds=600,
                message="check in",
            )
            state.agent_message_loop_add(
                agent_id=other.id,
                group_name="g",
                interval_seconds=600,
                message="other check in",
            )

            async def fake_handle_command(_payload):
                raise AssertionError("self-stop must not dispatch commands")

            text, is_error = await self.mcp_mod._dispatch_tool(
                "torque_stop_user_message_loop",
                {"reason": "done now"},
                worker.id,
                fake_handle_command,
                state,
            )

            self.assertFalse(is_error, text)
            payload = json.loads(text)
            self.assertEqual(payload["loop"]["id"], loop.id)
            self.assertEqual(payload["loop"]["status"], "stopped")
            self.assertEqual(state.agent_message_loops[loop.id].status, "stopped")
            self.assertIsNotNone(state.active_agent_message_loop_for_agent(other.id))
            audit = db.load_direct_message(payload["audit_message_id"])
            self.assertIsNotNone(audit)
            self.assertEqual(audit["message_type"], "system")
            self.assertIn("Receiving agent stopped /loop", audit["message"])
            self.assertIn("done now", audit["message"])
            db.close()

    async def test_architect_and_engineer_message_user_persist_direct_rows(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            architect = self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            engineer = self.state_mod.AgentCell(
                id="eng-1",
                name="Engineer",
                group="g",
                cell_type="agent",
                kind="engineer",
                hired_by_architect_id=architect.id,
            )
            state.agents[architect.id] = architect
            state.agents[engineer.id] = engineer
            state.groups["g"] = [architect.id, engineer.id]
            state.board_add_task(
                "Visible task",
                "g",
                lane="Backlog",
                id="task-1",
                assigned_engineer_id=engineer.id,
            )

            notifications = []
            state.on_direct_user_message = lambda row: notifications.append(
                dict(row)
            )
            calls = []

            async def fake_handle_command(payload):
                calls.append(dict(payload))
                return {"type": "ok"}

            arch_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "architect_message_user",
                    "arguments": {
                        "message": "Architect update for the user.",
                        "context_task_ids": ["task-1"],
                    },
                },
            }
            arch_result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                arch_body,
                cell_id=architect.id,
                handle_command=fake_handle_command,
                state=state,
            )
            self.assertEqual(status, 200)
            self.assertFalse(arch_result["result"]["isError"])
            arch_payload = json.loads(
                arch_result["result"]["content"][0]["text"]
            )
            arch_row = db.load_direct_message(arch_payload["message_id"])
            self.assertEqual(arch_row["sender_kind"], "architect")
            self.assertEqual(arch_row["recipient_kind"], "user")
            self.assertEqual(
                arch_row["thread_id"],
                db_mod.canonical_user_agent_thread_id(architect.id),
            )
            self.assertEqual(arch_row["context_task_ids"], ["task-1"])
            self.assertEqual(arch_row["delivery_state"], "delivered")

            eng_body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "engineer_message_user",
                    "arguments": {
                        "message": "Engineer update for the user.",
                        "context_task_ids": ["task-1"],
                    },
                },
            }
            eng_result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                eng_body,
                cell_id=engineer.id,
                handle_command=fake_handle_command,
                state=state,
            )
            self.assertEqual(status, 200)
            self.assertFalse(eng_result["result"]["isError"])
            eng_payload = json.loads(
                eng_result["result"]["content"][0]["text"]
            )
            eng_row = db.load_direct_message(eng_payload["message_id"])
            self.assertEqual(eng_row["sender_kind"], "engineer")
            self.assertEqual(eng_row["recipient_kind"], "user")
            self.assertEqual(
                eng_row["thread_id"],
                db_mod.canonical_user_agent_thread_id(engineer.id),
            )
            self.assertEqual(eng_row["context_task_ids"], ["task-1"])
            self.assertEqual(eng_row["message_type"], "message")
            self.assertFalse(eng_row["blocking"])
            self.assertEqual(calls, [])
            self.assertEqual(len(state.board_tasks), 1)
            self.assertEqual(len(notifications), 2)
            db.close()

    async def test_architect_message_user_preserves_bound_architect_lane(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            blueprint = self.state_mod.AgentCell(
                id="88b5b1ee",
                name="Blueprint",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            torqly = self.state_mod.AgentCell(
                id="a5a7fc9e",
                name="Torqly",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            state.agents[blueprint.id] = blueprint
            state.agents[torqly.id] = torqly
            state.groups["g"] = [blueprint.id, torqly.id]

            notifications = []
            state.on_direct_user_message = lambda row: notifications.append(
                dict(row)
            )

            async def fake_handle_command(payload):
                self.fail(f"architect_message_user should not call command: {payload}")

            async def call_architect_message_user(agent_id, arguments, req_id):
                body = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_message_user",
                        "arguments": arguments,
                    },
                }
                result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=agent_id,
                    handle_command=fake_handle_command,
                    state=state,
                )
                self.assertEqual(status, 200)
                return result

            blueprint_thread = db_mod.canonical_user_agent_thread_id(blueprint.id)
            blueprint_default_result = await call_architect_message_user(
                blueprint.id,
                {"message": "Blueprint default-lane update for the user."},
                101,
            )
            self.assertFalse(blueprint_default_result["result"]["isError"])
            blueprint_default_payload = json.loads(
                blueprint_default_result["result"]["content"][0]["text"]
            )
            blueprint_default_row = db.load_direct_message(
                blueprint_default_payload["message_id"]
            )
            self.assertEqual(blueprint_default_payload["thread_id"], blueprint_thread)
            self.assertEqual(blueprint_default_payload["agent_id"], blueprint.id)
            self.assertEqual(blueprint_default_payload["sender_id"], blueprint.id)
            self.assertEqual(blueprint_default_payload["sender_kind"], "architect")
            self.assertEqual(blueprint_default_row["thread_id"], blueprint_thread)
            self.assertEqual(blueprint_default_row["sender_id"], blueprint.id)
            self.assertEqual(blueprint_default_row["sender_kind"], "architect")

            blueprint_result = await call_architect_message_user(
                blueprint.id,
                {
                    "message": "Blueprint explicit-lane update for the user.",
                    "thread_id": blueprint_thread,
                },
                102,
            )
            self.assertFalse(blueprint_result["result"]["isError"])
            blueprint_payload = json.loads(
                blueprint_result["result"]["content"][0]["text"]
            )
            blueprint_row = db.load_direct_message(
                blueprint_payload["message_id"]
            )
            self.assertEqual(blueprint_payload["thread_id"], blueprint_thread)
            self.assertEqual(blueprint_payload["agent_id"], blueprint.id)
            self.assertEqual(blueprint_payload["sender_id"], blueprint.id)
            self.assertEqual(blueprint_payload["sender_kind"], "architect")
            self.assertEqual(blueprint_row["thread_id"], blueprint_thread)
            self.assertEqual(blueprint_row["sender_id"], blueprint.id)
            self.assertEqual(blueprint_row["sender_kind"], "architect")

            torqly_result = await call_architect_message_user(
                torqly.id,
                {"message": "Torqly update for the user."},
                103,
            )
            self.assertFalse(torqly_result["result"]["isError"])
            torqly_payload = json.loads(
                torqly_result["result"]["content"][0]["text"]
            )
            torqly_thread = db_mod.canonical_user_agent_thread_id(torqly.id)
            torqly_row = db.load_direct_message(torqly_payload["message_id"])
            self.assertEqual(torqly_payload["thread_id"], torqly_thread)
            self.assertEqual(torqly_payload["agent_id"], torqly.id)
            self.assertEqual(torqly_payload["sender_id"], torqly.id)
            self.assertEqual(torqly_payload["sender_kind"], "architect")
            self.assertEqual(torqly_row["thread_id"], torqly_thread)
            self.assertEqual(torqly_row["sender_id"], torqly.id)
            self.assertEqual(torqly_row["sender_kind"], "architect")

            self.assertEqual(
                [row["id"] for row in state.direct_messages_by_agent[blueprint.id]],
                [blueprint_default_row["id"], blueprint_row["id"]],
            )
            self.assertEqual(
                [row["id"] for row in state.direct_messages_by_agent[torqly.id]],
                [torqly_row["id"]],
            )
            self.assertEqual(len(notifications), 3)
            self.assertEqual(
                [row["thread_id"] for row in notifications],
                [blueprint_thread, blueprint_thread, torqly_thread],
            )
            self.assertEqual(
                [row["sender_id"] for row in notifications],
                [blueprint.id, blueprint.id, torqly.id],
            )
            self.assertEqual(
                [row["id"] for row in db.load_direct_messages_for_thread(
                    blueprint_thread
                )],
                [blueprint_default_row["id"], blueprint_row["id"]],
            )
            self.assertEqual(
                [row["id"] for row in db.load_direct_messages_for_thread(
                    torqly_thread
                )],
                [torqly_row["id"]],
            )

            db.close()

    async def test_architect_message_user_rejects_other_architect_thread(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            blueprint = self.state_mod.AgentCell(
                id="88b5b1ee",
                name="Blueprint",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            torqly = self.state_mod.AgentCell(
                id="a5a7fc9e",
                name="Torqly",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            state.agents[blueprint.id] = blueprint
            state.agents[torqly.id] = torqly
            state.groups["g"] = [blueprint.id, torqly.id]
            notifications = []
            state.on_direct_user_message = lambda row: notifications.append(
                dict(row)
            )

            async def fake_handle_command(payload):
                self.fail(f"architect_message_user should not call command: {payload}")

            async def assert_rejects(caller_id, requested_thread, expected_thread, req_id):
                body = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_message_user",
                        "arguments": {
                            "message": "Do not silently re-attribute this.",
                            "thread_id": requested_thread,
                        },
                    },
                }

                result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                    body,
                    cell_id=caller_id,
                    handle_command=fake_handle_command,
                    state=state,
                )

                self.assertEqual(status, 200)
                self.assertTrue(result["result"]["isError"])
                text = result["result"]["content"][0]["text"]
                self.assertIn("thread_id is for a different user-agent lane", text)
                self.assertIn(expected_thread, text)

            await assert_rejects(
                torqly.id,
                db_mod.canonical_user_agent_thread_id(blueprint.id),
                db_mod.canonical_user_agent_thread_id(torqly.id),
                201,
            )
            await assert_rejects(
                blueprint.id,
                db_mod.canonical_user_agent_thread_id(torqly.id),
                db_mod.canonical_user_agent_thread_id(blueprint.id),
                202,
            )
            self.assertEqual(notifications, [])
            self.assertEqual(
                db.load_direct_messages_for_thread(
                    db_mod.canonical_user_agent_thread_id(blueprint.id)
                ),
                [],
            )
            self.assertEqual(
                db.load_direct_messages_for_thread(
                    db_mod.canonical_user_agent_thread_id(torqly.id)
                ),
                [],
            )
            self.assertNotIn(blueprint.id, state.direct_messages_by_agent)
            self.assertNotIn(torqly.id, state.direct_messages_by_agent)
            db.close()

    async def test_architect_message_user_without_reply_id_is_proactive(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            architect = self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            state.agents[architect.id] = architect
            state.groups["g"] = [architect.id]
            thread_id = db_mod.canonical_user_agent_thread_id(architect.id)
            state.save_direct_message({
                "id": "msg-user-1",
                "thread_id": thread_id,
                "group_name": "g",
                "sender_id": "user",
                "sender_kind": "user",
                "sender_name": "User",
                "recipient_id": architect.id,
                "recipient_kind": "architect",
                "recipient_name": "Architect",
                "message": "Can you give me a quick status?",
                "message_type": "message",
                "created_at": 10.0,
                "delivery_state": "delivered",
            })
            notifications = []
            state.on_direct_user_message = lambda row: notifications.append(dict(row))

            async def fake_handle_command(payload):
                self.fail(f"architect_message_user should not call command: {payload}")

            body = {
                "jsonrpc": "2.0",
                "id": 401,
                "method": "tools/call",
                "params": {
                    "name": "architect_message_user",
                    "arguments": {"message": "Yes — implementation is underway."},
                },
            }
            result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=architect.id,
                handle_command=fake_handle_command,
                state=state,
            )
            self.assertEqual(status, 200)
            self.assertFalse(result["result"]["isError"])
            payload = json.loads(result["result"]["content"][0]["text"])
            row = db.load_direct_message(payload["message_id"])
            self.assertEqual(payload["reply_to_id"], "")
            self.assertEqual(row["reply_to_id"], "")
            self.assertEqual(row["thread_id"], thread_id)
            self.assertEqual(row["sender_id"], architect.id)
            self.assertEqual(row["recipient_id"], "user")
            self.assertEqual([item["id"] for item in notifications], [row["id"]])
            db.close()

    async def test_architect_message_user_history_does_not_block_proactive_message(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            architect = self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            state.agents[architect.id] = architect
            state.groups["g"] = [architect.id]
            thread_id = db_mod.canonical_user_agent_thread_id(architect.id)
            for idx, created_at in (("1", 10.0), ("2", 20.0)):
                state.save_direct_message({
                    "id": f"msg-user-{idx}",
                    "thread_id": thread_id,
                    "group_name": "g",
                    "sender_id": "user",
                    "sender_kind": "user",
                    "recipient_id": architect.id,
                    "recipient_kind": "architect",
                    "message": f"Question {idx}",
                    "message_type": "message",
                    "created_at": created_at,
                    "delivery_state": "delivered",
                })

            async def fake_handle_command(payload):
                self.fail(f"architect_message_user should not call command: {payload}")

            text, is_error = await self.mcp_mod._dispatch_architect_tool(
                "architect_message_user",
                {"message": "Fresh status after restart."},
                fake_handle_command,
                state,
                caller_id=architect.id,
            )
            self.assertFalse(is_error)
            payload = json.loads(text)
            self.assertEqual(payload["reply_to_id"], "")
            saved = db.load_direct_message(payload["message_id"])
            self.assertEqual(saved["reply_to_id"], "")
            self.assertEqual(
                [row["id"] for row in db.load_direct_messages_for_thread(thread_id)],
                ["msg-user-1", "msg-user-2", payload["message_id"]],
            )
            db.close()

    async def test_architect_message_user_after_prior_reply_can_start_fresh_message(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            architect = self.state_mod.AgentCell(
                id="arch-1",
                name="Architect",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            state.agents[architect.id] = architect
            state.groups["g"] = [architect.id]
            thread_id = db_mod.canonical_user_agent_thread_id(architect.id)
            state.save_direct_message({
                "id": "msg-user-1",
                "thread_id": thread_id,
                "group_name": "g",
                "sender_id": "user",
                "sender_kind": "user",
                "recipient_id": architect.id,
                "recipient_kind": "architect",
                "message": "Can you answer this?",
                "message_type": "message",
                "created_at": 10.0,
                "delivery_state": "delivered",
            })
            state.save_direct_message({
                "id": "msg-arch-reply",
                "thread_id": thread_id,
                "reply_to_id": "msg-user-1",
                "group_name": "g",
                "sender_id": architect.id,
                "sender_kind": "architect",
                "recipient_id": "user",
                "recipient_kind": "user",
                "message": "Already answered.",
                "message_type": "message",
                "created_at": 20.0,
                "delivery_state": "delivered",
            })

            async def fake_handle_command(payload):
                self.fail(f"architect_message_user should not call command: {payload}")

            text, is_error = await self.mcp_mod._dispatch_architect_tool(
                "architect_message_user",
                {"message": "A new proactive status."},
                fake_handle_command,
                state,
                caller_id=architect.id,
            )
            self.assertFalse(is_error)
            payload = json.loads(text)
            self.assertEqual(payload["reply_to_id"], "")
            self.assertEqual(len(db.load_direct_messages_for_thread(thread_id)), 3)
            db.close()

    async def test_architect_message_user_rejects_other_architect_reply_to_id(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            blueprint = self.state_mod.AgentCell(
                id="88b5b1ee",
                name="Blueprint",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            torqly = self.state_mod.AgentCell(
                id="a5a7fc9e",
                name="Torqly",
                group="g",
                cell_type="agent",
                kind="architect",
            )
            state.agents[blueprint.id] = blueprint
            state.agents[torqly.id] = torqly
            state.groups["g"] = [blueprint.id, torqly.id]
            blueprint_thread = db_mod.canonical_user_agent_thread_id(blueprint.id)
            state.save_direct_message({
                "id": "msg-blueprint-user",
                "thread_id": blueprint_thread,
                "group_name": "g",
                "sender_id": "user",
                "sender_kind": "user",
                "recipient_id": blueprint.id,
                "recipient_kind": "architect",
                "message": "Question for Blueprint only.",
                "message_type": "message",
                "created_at": 10.0,
                "delivery_state": "delivered",
            })

            async def fake_handle_command(payload):
                self.fail(f"architect_message_user should not call command: {payload}")

            text, is_error = await self.mcp_mod._dispatch_architect_tool(
                "architect_message_user",
                {
                    "message": "Torqly must not hijack Blueprint's thread.",
                    "reply_to_id": "msg-blueprint-user",
                },
                fake_handle_command,
                state,
                caller_id=torqly.id,
            )
            self.assertTrue(is_error)
            self.assertIn("reply_to_id does not belong to this agent", text)
            self.assertEqual(
                [
                    row["id"] for row in db.load_direct_messages_for_thread(
                        blueprint_thread
                    )
                ],
                ["msg-blueprint-user"],
            )
            db.close()

    async def test_message_user_rejects_other_agent_thread_for_worker_and_engineer(self):
        db_mod = importlib.import_module("torque.db")
        with tempfile.TemporaryDirectory() as tmp:
            db = db_mod.TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)
            worker = self.state_mod.AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                cell_type="agent",
                kind="worker",
            )
            other_worker = self.state_mod.AgentCell(
                id="worker-2",
                name="Other Worker",
                group="g",
                cell_type="agent",
                kind="worker",
            )
            engineer = self.state_mod.AgentCell(
                id="eng-1",
                name="Engineer",
                group="g",
                cell_type="agent",
                kind="engineer",
            )
            other_engineer = self.state_mod.AgentCell(
                id="eng-2",
                name="Other Engineer",
                group="g",
                cell_type="agent",
                kind="engineer",
            )
            for cell in (worker, other_worker, engineer, other_engineer):
                state.agents[cell.id] = cell
            state.groups["g"] = [
                worker.id,
                other_worker.id,
                engineer.id,
                other_engineer.id,
            ]

            async def fake_handle_command(payload):
                self.fail(f"message_user should not call command: {payload}")

            text, is_error = await self.mcp_mod._dispatch_tool(
                "torque_message_user",
                {
                    "message": "Do not spoof this worker lane.",
                    "thread_id": db_mod.canonical_user_agent_thread_id(
                        other_worker.id
                    ),
                },
                worker.id,
                fake_handle_command,
                state,
            )
            self.assertTrue(is_error)
            self.assertIn("thread_id is for a different user-agent lane", text)
            self.assertIn(db_mod.canonical_user_agent_thread_id(worker.id), text)

            body = {
                "jsonrpc": "2.0",
                "id": 301,
                "method": "tools/call",
                "params": {
                    "name": "engineer_message_user",
                    "arguments": {
                        "message": "Do not spoof this engineer lane.",
                        "thread_id": db_mod.canonical_user_agent_thread_id(
                            other_engineer.id
                        ),
                    },
                },
            }
            result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=engineer.id,
                handle_command=fake_handle_command,
                state=state,
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["result"]["isError"])
            error_text = result["result"]["content"][0]["text"]
            self.assertIn(
                "thread_id is for a different user-agent lane",
                error_text,
            )
            self.assertIn(
                db_mod.canonical_user_agent_thread_id(engineer.id),
                error_text,
            )
            self.assertEqual(
                db.load_direct_messages_for_thread(
                    db_mod.canonical_user_agent_thread_id(worker.id)
                ),
                [],
            )
            self.assertEqual(
                db.load_direct_messages_for_thread(
                    db_mod.canonical_user_agent_thread_id(engineer.id)
                ),
                [],
            )
            db.close()

    async def test_message_user_rejects_empty_message(self):
        state = self.state_mod.MatrixState()
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[worker.id] = worker

        async def fake_handle_command(_payload):
            return {"type": "ok"}

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_message_user",
            {"message": "   "},
            worker.id,
            fake_handle_command,
            state,
        )
        self.assertTrue(is_error)
        self.assertEqual(text, "message is required")

    async def test_create_mcp_handler_exposes_only_current_mcp_surfaces(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
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
        hired_engineer = self.state_mod.AgentCell(
            id="engineer-hired",
            name="Hired Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.agents[hired_engineer.id] = hired_engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [
            engineer.id,
            architect.id,
            engineer.id,
            hired_engineer.id,
            worker.id,
        ]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
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
        self.assertIn("task_progress", tool_names)
        self.assertIn("task_verify", tool_names)
        self.assertIn("task_artifact_upload", tool_names)
        self.assertIn("memory_publish", tool_names)
        self.assertIn("memory_get", tool_names)
        self.assertIn("memory_link", tool_names)
        self.assertFalse(any(
            name.startswith(("torque_", "engineer_", "architect_"))
            for name in tool_names
        ))

        listed_worker = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": worker.id},
            )
        )
        worker_tool_names = [
            tool["name"] for tool in listed_worker.payload["result"]["tools"]
        ]
        self.assertIn("task_progress", worker_tool_names)
        self.assertIn("user_message", worker_tool_names)
        worker_message_tool = next(
            tool
            for tool in listed_worker.payload["result"]["tools"]
            if tool["name"] == "user_message"
        )
        self.assertNotIn(
            "thread_id",
            worker_message_tool["inputSchema"]["properties"],
        )
        self.assertIn(
            "reply_to_id",
            worker_message_tool["inputSchema"]["properties"],
        )
        self.assertNotIn("board_summary", worker_tool_names)
        self.assertNotIn("tool_search", worker_tool_names)

        listed_architect = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        architect_tool_names = [
            tool["name"] for tool in listed_architect.payload["result"]["tools"]
        ]
        for name in {
            "tool_search", "board_summary", "boot_summary", "task_list",
            "task_get", "task_create", "task_update", "task_move",
            "event_list", "agent_list", "agent_message", "peer_list",
            "peer_message", "peer_inbox", "decision_list", "journal_write",
            "journal_list", "user_message", "worktree_merge",
            "worktree_rebase",
        }:
            self.assertIn(name, architect_tool_names)
        self.assertFalse(any(
            name.startswith(("torque_", "architect_"))
            for name in architect_tool_names
        ))
        self.assertTrue(
            {"engineer_hire", "engineer_update", "engineer_lifecycle"}
            <= set(architect_tool_names)
        )
        architect_message_tool = next(
            tool
            for tool in listed_architect.payload["result"]["tools"]
            if tool["name"] == "user_message"
        )
        self.assertNotIn(
            "thread_id",
            architect_message_tool["inputSchema"]["properties"],
        )
        self.assertIn(
            "reply_to_id",
            architect_message_tool["inputSchema"]["properties"],
        )
        self.assertIn("raise", architect_tool_names)
        self.assertNotIn("user_ask", architect_tool_names)

        listed_engineer = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 121, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        engineer_tool_names = [
            tool["name"] for tool in listed_engineer.payload["result"]["tools"]
        ]
        self.assertIn("tool_search", engineer_tool_names)
        self.assertIn("board_summary", engineer_tool_names)
        self.assertIn("task_verify", engineer_tool_names)
        self.assertIn("task_artifact_upload", engineer_tool_names)
        self.assertIn("user_message", engineer_tool_names)
        engineer_message_tool = next(
            tool
            for tool in listed_engineer.payload["result"]["tools"]
            if tool["name"] == "user_message"
        )
        self.assertNotIn(
            "thread_id",
            engineer_message_tool["inputSchema"]["properties"],
        )
        self.assertIn(
            "reply_to_id",
            engineer_message_tool["inputSchema"]["properties"],
        )
        self.assertNotIn("supervisor_message", engineer_tool_names)
        self.assertNotIn("agent_reply", engineer_tool_names)
        self.assertIn("worktree_merge", engineer_tool_names)
        self.assertIn("worktree_rebase", engineer_tool_names)
        self.assertFalse(any(
            name.startswith(("torque_", "engineer_", "architect_"))
            for name in engineer_tool_names
        ))

        listed_hired_engineer = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 122, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": hired_engineer.id},
            )
        )
        hired_engineer_tool_names = [
            tool["name"]
            for tool in listed_hired_engineer.payload["result"]["tools"]
        ]
        self.assertIn("supervisor_message", hired_engineer_tool_names)
        self.assertIn("agent_reply", hired_engineer_tool_names)

        missing_header = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "torque_progress", "arguments": {"message": "hi"}},
                }
            )
        )
        self.assertTrue(missing_header.payload["result"]["isError"])
        self.assertIn(
            "X-Torque-Cell-Id header is required",
            missing_header.payload["result"]["content"][0]["text"],
        )

        removed_alias = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "engineer_agents_list", "arguments": {}},
                }
            )
        )
        self.assertEqual(removed_alias.payload["error"]["code"], -32602)
        self.assertIn("Unknown tool", removed_alias.payload["error"]["message"])

        denied_architect_summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {"name": "architect_board_summary", "arguments": {}},
                },
                headers={"X-Torque-Cell-Id": worker.id},
            )
        )
        self.assertEqual(
            denied_architect_summary.payload["error"]["code"],
            -32602,
        )
        self.assertIn(
            "Unknown tool",
            denied_architect_summary.payload["error"]["message"],
        )

        summary = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "engineer_board_summary", "arguments": {}},
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(summary.payload["result"]["isError"])
        parsed = json.loads(summary.payload["result"]["content"][0]["text"])
        self.assertEqual(parsed["group"], "g")
        self.assertEqual(calls, [])

    async def test_engineer_board_summary_includes_caller_dispatch_shapes(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer One",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        other_engineer = self.state_mod.AgentCell(
            id="engineer-2",
            name="Engineer Two",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.agents[other_engineer.id] = other_engineer
        state.groups["g"] = [engineer.id, other_engineer.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.record_engineer_dispatch_shape(
            engineer.id,
            group="g",
            source_tool="engineer_task_dispatch",
            shape="serial",
            task_ids=["task-serial"],
            hintable=True,
        )
        state.record_engineer_dispatch_shape(
            engineer.id,
            group="g",
            source_tool="engineer_batch_dispatch",
            shape="batch",
            task_ids=["task-batch-a", "task-batch-b"],
            task_count=2,
        )
        state.record_engineer_dispatch_shape(
            engineer.id,
            group="g",
            source_tool="torque_derive",
            shape="warm_cluster",
            task_ids=["task-derive"],
        )
        state.record_engineer_dispatch_shape(
            other_engineer.id,
            group="g",
            source_tool="engineer_task_dispatch",
            shape="warm_cluster",
            task_ids=["other-task"],
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_board_summary",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        parsed = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            parsed["dispatch_shapes"]["counts"],
            {"serial": 1, "batch": 1, "warm_cluster": 0},
        )
        self.assertEqual(parsed["dispatch_shapes"]["hintable_serial"], 1)
        self.assertEqual(parsed["dispatch_shapes"]["derives_total"], 1)
        self.assertEqual(
            parsed["dispatch_shapes"]["derives_by_shape"],
            {"serial": 0, "batch": 0, "warm_cluster": 1},
        )

    def _parse_functions_block(self, text):
        self.assertTrue(text.startswith("<functions>"), text)
        self.assertTrue(text.endswith("</functions>"), text)
        inner = text[len("<functions>"):-len("</functions>")]
        return json.loads(inner)

    async def test_engineer_dispatch_provider_override_schema_tracks_setting(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        async def props_for(tool_name):
            listed = await handler(
                FakeRequest(
                    {"jsonrpc": "2.0", "id": tool_name, "method": "tools/list"},
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            tools = listed.payload["result"]["tools"]
            tool = next(t for t in tools if t["name"] == tool_name)
            return tool["inputSchema"]["properties"]

        task_dispatch_props = await props_for("task_dispatch")
        self.assertNotIn("agent_type", task_dispatch_props)
        self.assertIn("provider", task_dispatch_props)
        self.assertIn("entries", task_dispatch_props)
        agent_message_props = await props_for("agent_message")
        self.assertIn("reply_required", agent_message_props)
        self.assertTrue(agent_message_props["reply_required"]["default"])

        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=False,
        )

        task_dispatch_props = await props_for("task_dispatch")
        self.assertNotIn("agent_type", task_dispatch_props)
        self.assertNotIn("provider", task_dispatch_props)

        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=True,
        )

        task_dispatch_props = await props_for("task_dispatch")
        self.assertNotIn("agent_type", task_dispatch_props)
        self.assertIn("provider", task_dispatch_props)

    async def test_architect_engineer_specialization_tool_schemas(self):
        hire_tool = next(
            tool for tool in self.mcp_mod.ARCHITECT_TOOLS
            if tool["name"] == "architect_engineer_hire"
        )
        hire_props = hire_tool["inputSchema"]["properties"]
        self.assertIn("specializations", hire_props)
        self.assertEqual(hire_props["specializations"]["type"], "array")

        set_tool = next(
            tool for tool in self.mcp_mod.ARCHITECT_TOOLS
            if tool["name"] == "architect_engineer_set_specializations"
        )
        set_schema = set_tool["inputSchema"]
        self.assertEqual(
            set_schema["required"],
            ["engineer_id", "specializations"],
        )
        self.assertEqual(
            set_schema["properties"]["specializations"]["type"],
            "array",
        )
        self.assertIn("Full-replace", set_tool["description"])

    async def test_engineer_batch_dispatch_schema_frames_parallel_waves(self):
        tool = next(
            t for t in self.mcp_mod.ENGINEER_TOOLS
            if t["name"] == "engineer_batch_dispatch"
        )
        description = tool["description"]
        props = tool["inputSchema"]["properties"]

        self.assertIn("Boot N workers simultaneously", description)
        self.assertIn(
            "parallel velocity > review-boundary granularity",
            description,
        )
        self.assertIn("this batch's engineer-group", description)
        self.assertIn("warm-cluster queue", description)
        self.assertIn("prefer serial `engineer_task_dispatch`", description)
        self.assertIn("implement→review→fix checkpoints", description)
        agent_group_desc = (
            props["tasks"]["items"]["properties"]["agent_group"]["description"]
        )
        self.assertIn("warm-cluster same-agent affinity key", agent_group_desc)
        self.assertIn("not a capacity", agent_group_desc)
        max_desc = props["max_concurrent"]["description"]
        self.assertIn("Per-batch active-worker cap", max_desc)
        self.assertIn("engineer group's currently active", max_desc)
        self.assertIn("not an agent_group", max_desc)

    async def test_engineer_merge_schema_is_eager_and_includes_pr_title_body(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer

        async def fake_handle_command(_payload):
            self.fail("tools/list should not dispatch commands")

        response, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
            cell_id=engineer.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        tool = next(
            tool
            for tool in response["result"]["tools"]
            if tool["name"] == "worktree_merge"
        )
        props = tool["inputSchema"]["properties"]

        self.assertIn("pr_title", props)
        self.assertIn("pr_body", props)
        self.assertIn("short imperative", props["pr_title"]["description"])
        self.assertIn("Markdown", props["pr_body"]["description"])
        self.assertIn("TORQUE:123", props["pr_body"]["description"])
        self.assertIn(
            "engineer_merge_mode",
            props["force_direct"]["description"],
        )

    async def test_architect_can_merge_hired_engineer_worktree_without_search(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Torqly",
            group="g",
            cell_type="agent",
            kind="architect",
            effective_agent_class_snapshot={
                "effective_authority": {
                    "schema_version": 1,
                    "base_kind": "architect",
                    "acl_mode": "allow",
                    "capabilities": {
                        "self.read": "self",
                        "worktree.merge": "children",
                    },
                },
            },
        )
        courier = self.state_mod.AgentCell(
            id="engineer-1",
            name="Courier",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            worktree_path="/tmp/courier",
            worktree_branch="torque/courier/reviewed",
            worktree_base_branch="main",
        )
        state.agents[architect.id] = architect
        state.agents[courier.id] = courier
        state.groups["g"] = [architect.id, courier.id]
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": courier.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "id": courier.id,
                    "ok": True,
                    "sha": "reviewed-sha",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        listed, listed_status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "tools/list",
                "params": {},
            },
            cell_id=architect.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(listed_status, 200)
        listed_names = {
            tool["name"] for tool in listed["result"]["tools"]
        }
        self.assertIn("worktree_merge", listed_names)
        self.assertIn("worktree_rebase", listed_names)

        result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "worktree_merge",
                    "arguments": {"agent": courier.id},
                },
            },
            cell_id=architect.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(
            json.loads(result["result"]["content"][0]["text"])["sha"],
            "reviewed-sha",
        )
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )
        self.assertEqual(calls[-1]["actor_agent_id"], architect.id)

    async def test_architect_can_merge_task_linked_user_worker_worktree(self):
        """A user-owned worker inherits only its creating Architect's stream scope."""
        state = self.state_mod.MatrixState()
        merge_authority = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {
                    "self.read": "self",
                    "worktree.merge": "children",
                },
            },
        }
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Torqly",
            group="g",
            cell_type="agent",
            kind="architect",
            effective_agent_class_snapshot=merge_authority,
        )
        peer_architect = self.state_mod.AgentCell(
            id="architect-2",
            name="Peer",
            group="g",
            cell_type="agent",
            kind="architect",
            effective_agent_class_snapshot=merge_authority,
        )
        user_worker = self.state_mod.AgentCell(
            id="worker-user",
            name="User Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            worktree_path="/tmp/user-worker",
            worktree_branch="torque/user/user-worker",
            worktree_base_branch="main",
        )
        task = self.state_mod.BoardTask(
            id="TORQUE:worker-stream",
            task="Reviewed user-worker stream",
            group="g",
            lane="In Progress",
            agent_id=user_worker.id,
            created_by_architect_id=architect.id,
        )
        state.agents = {
            architect.id: architect,
            peer_architect.id: peer_architect,
            user_worker.id: user_worker,
        }
        state.groups["g"] = list(state.agents)
        state.board_tasks[task.id] = task
        foreign_worker = self.state_mod.AgentCell(
            id="worker-foreign",
            name="Foreign User Worker",
            group="other-group",
            cell_type="agent",
            kind="worker",
            worktree_path="/tmp/foreign-worker",
            worktree_branch="torque/user/foreign-worker",
            worktree_base_branch="main",
        )
        foreign_task = self.state_mod.BoardTask(
            id="TORQUE:foreign-stream",
            task="Foreign user-worker stream",
            group="other-group",
            lane="In Progress",
            agent_id=foreign_worker.id,
            created_by_architect_id=architect.id,
        )
        state.agents[foreign_worker.id] = foreign_worker
        state.groups["other-group"] = [foreign_worker.id]
        state.board_tasks[foreign_task.id] = foreign_task
        calls = []

        async def green_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": user_worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "id": user_worker.id,
                    "ok": True,
                    "sha": "user-worker-reviewed-sha",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "worktree_merge",
                    "arguments": {"agent": user_worker.id},
                },
            },
            cell_id=architect.id,
            handle_command=green_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(
            json.loads(result["result"]["content"][0]["text"])["sha"],
            "user-worker-reviewed-sha",
        )
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )
        self.assertEqual(calls[-1]["actor_agent_id"], architect.id)

        calls.clear()
        denied, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "worktree_merge",
                    "arguments": {"agent": user_worker.id},
                },
            },
            cell_id=peer_architect.id,
            handle_command=green_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertEqual(calls, [])
        self.assertIn(
            "Known tool is not authorized",
            denied["error"]["message"],
        )

        cross_group_denied, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "worktree_merge",
                    "arguments": {"agent": foreign_worker.id},
                },
            },
            cell_id=architect.id,
            handle_command=green_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertEqual(calls, [])
        self.assertIn(
            "Known tool is not authorized",
            cross_group_denied["error"]["message"],
        )

    async def test_architect_user_worker_merge_keeps_preflight_gate(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Torqly",
            group="g",
            cell_type="agent",
            kind="architect",
            effective_agent_class_snapshot={
                "effective_authority": {
                    "schema_version": 1,
                    "base_kind": "architect",
                    "acl_mode": "allow",
                    "capabilities": {"worktree.merge": "children"},
                },
            },
        )
        user_worker = self.state_mod.AgentCell(
            id="worker-user",
            name="User Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            worktree_path="/tmp/user-worker",
            worktree_branch="torque/user/user-worker",
            worktree_base_branch="main",
        )
        task = self.state_mod.BoardTask(
            id="TORQUE:worker-stream",
            task="Unreviewed user-worker stream",
            group="g",
            lane="In Progress",
            agent_id=user_worker.id,
            created_by_architect_id=architect.id,
        )
        state.agents = {architect.id: architect, user_worker.id: user_worker}
        state.groups["g"] = list(state.agents)
        state.board_tasks[task.id] = task
        calls = []

        async def blocked_handle_command(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "worktree_check_merge")
            return {
                "type": "worktree_check_merge",
                "id": user_worker.id,
                "clean": False,
                "conflicts": [],
                "error": "Review is required before merge.",
            }

        result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "worktree_merge",
                    "arguments": {"agent": user_worker.id},
                },
            },
            cell_id=architect.id,
            handle_command=blocked_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["result"]["isError"])
        self.assertIn(
            "Review is required before merge.",
            result["result"]["content"][0]["text"],
        )
        self.assertEqual([call["cmd"] for call in calls], ["worktree_check_merge"])

    async def test_architect_listed_worktree_rebase_is_callable(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Torqly",
            group="g",
            cell_type="agent",
            kind="architect",
            effective_agent_class_snapshot={
                "effective_authority": {
                    "schema_version": 1,
                    "base_kind": "architect",
                    "acl_mode": "allow",
                    "capabilities": {
                        "self.read": "self",
                        "worktree.merge": "children",
                    },
                },
            },
        )
        courier = self.state_mod.AgentCell(
            id="engineer-1",
            name="Courier",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            worktree_path="/tmp/courier",
            worktree_branch="torque/courier/reviewed",
            worktree_base_branch="main",
        )
        state.agents[architect.id] = architect
        state.agents[courier.id] = courier
        state.groups["g"] = [architect.id, courier.id]
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": courier.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_rebase":
                return {
                    "type": "worktree_rebase",
                    "id": courier.id,
                    "ok": True,
                    "post_rebase_head_sha": "rebased-sha",
                }
            self.fail(f"Unexpected command: {payload}")

        listed, listed_status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
            cell_id=architect.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(listed_status, 200)
        listed_names = {
            tool["name"] for tool in listed["result"]["tools"]
        }
        self.assertIn("worktree_rebase", listed_names)

        result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "worktree_rebase",
                    "arguments": {"agent": courier.id},
                },
            },
            cell_id=architect.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["isError"])
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["post_rebase_head_sha"], "rebased-sha")
        self.assertTrue(payload["merge_ready"])
        self.assertEqual(
            [call["cmd"] for call in calls],
            [
                "worktree_check_merge",
                "worktree_rebase",
                "worktree_check_merge",
            ],
        )

    async def test_architect_authorized_task_verify_is_projected_and_callable(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Torqly",
            group="g",
            cell_type="agent",
            kind="architect",
            effective_agent_class_snapshot={
                "effective_authority": {
                    "schema_version": 1,
                    "base_kind": "architect",
                    "acl_mode": "allow",
                    "capabilities": {
                        "self.read": "self",
                        "task.verify": "self",
                    },
                },
            },
        )
        task = self.state_mod.BoardTask(
            id="TORQUE:1",
            task="Verify rollout",
            group="g",
            lane="In Progress",
            created_by_architect_id=architect.id,
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]
        state.board_tasks[task.id] = task
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "task_updated", "id": task.id}

        listed, listed_status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
            cell_id=architect.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(listed_status, 200)
        listed_names = {
            tool["name"] for tool in listed["result"]["tools"]
        }
        self.assertIn("task_verify", listed_names)

        result, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "task_verify",
                    "arguments": {
                        "task": task.id,
                        "tests_run": "focused suite",
                        "state": "passed",
                    },
                },
            },
            cell_id=architect.id,
            handle_command=fake_handle_command,
            state=state,
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(
            calls,
            [{
                "cmd": "board_verify_task",
                "id": task.id,
                "actor_name": architect.name,
                "verification_state": "passed",
                "tests_run": "focused suite",
            }],
        )

    async def test_engineer_batch_dispatch_deferral_reports_group_and_refreshes_cap(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            created_by_engineer_id=engineer.id,
            owner_engineer_id=engineer.id,
            current_task_id="active",
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [engineer.id, worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.board_add_task(
            "Active",
            "g",
            lane="In Progress",
            id="active",
            agent_id=worker.id,
        )
        queued = state.board_add_task("Queued", "g", lane="Backlog", id="queued")

        async def fake_handle_command(payload):
            self.fail(f"Unexpected dispatch while at cap: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        async def call_batch(max_concurrent):
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": max_concurrent,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_batch_dispatch",
                            "arguments": {
                                "tasks": [{"task": queued.id}],
                                "max_concurrent": max_concurrent,
                            },
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            self.assertFalse(response.payload["result"]["isError"])
            return json.loads(response.payload["result"]["content"][0]["text"])

        deferred = await call_batch(1)
        item = deferred["results"][0]
        self.assertEqual(item["status"], "deferred")
        self.assertEqual(item["reason"], "max_concurrent_reached")
        self.assertEqual(item["engineer_group"], "g")
        self.assertEqual(item["active_count"], 1)
        self.assertEqual(item["cap"], 1)
        self.assertIn("engineer group 'g'", item["message"])
        self.assertIn("1/1", item["message"])

        raised = await call_batch(3)
        item = raised["results"][0]
        self.assertEqual(item["status"], "cap_raised")
        self.assertEqual(item["previous_max_concurrent"], 1)
        self.assertEqual(item["max_concurrent"], 3)
        self.assertEqual(state.auto_dispatch_queues["g"][0].max_concurrent, 3)

        lowered = await call_batch(2)
        item = lowered["results"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["reason"], "already_queued")
        self.assertEqual(item["current_max_concurrent"], 3)
        self.assertEqual(item["requested_max_concurrent"], 2)
        self.assertEqual(state.auto_dispatch_queues["g"][0].max_concurrent, 3)

    async def test_engineer_batch_dispatch_deferred_entry_auto_promotes_after_slot_frees(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        first = state.board_add_task("First", "g", lane="Backlog", id="first")
        second = state.board_add_task("Second", "g", lane="Backlog", id="second")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            task = state.board_tasks[payload["id"]]
            agent_id = f"worker-{len(calls)}"
            agent = self.state_mod.AgentCell(
                id=agent_id,
                name=f"Worker {len(calls)}",
                group="g",
                cell_type="agent",
                created_by_engineer_id=engineer.id,
                owner_engineer_id=engineer.id,
                current_task_id=task.id,
            )
            state.agents[agent.id] = agent
            state.groups["g"].append(agent.id)
            task.agent_id = agent.id
            task.lane = "In Progress"
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_batch_dispatch",
                        "arguments": {
                            "tasks": [{"task": first.id}, {"task": second.id}],
                            "max_concurrent": 1,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(response.payload["result"]["isError"])
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["dispatched", "deferred"],
        )
        self.assertEqual(state.board_tasks[second.id].lane, "Backlog")
        self.assertEqual(state.auto_dispatch_queues["g"][0].task_id, second.id)

        state.agents["worker-1"].current_task_id = ""
        state.board_tasks[first.id].lane = "Done"
        dispatch_mod = importlib.reload(
            importlib.import_module("torque.server_dispatch")
        )
        panel_events = []
        dispatched = await dispatch_mod._pump_auto_dispatch_queue(
            state,
            fake_handle_command,
            lambda *args, **kwargs: panel_events.append((args, kwargs)),
            group="g",
        )

        self.assertEqual(dispatched[0]["task_id"], second.id)
        self.assertEqual(state.board_tasks[second.id].lane, "In Progress")
        self.assertNotIn("g", state.auto_dispatch_queues)
        self.assertEqual(len(calls), 2)
        self.assertEqual(panel_events[0][0][0], "task_auto_dispatched")

    async def test_engineer_batch_dispatch_reclusters_existing_armed_queue_entries(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        first = state.board_add_task(
            "First armed queued task",
            "g",
            lane="To Do",
            id="first",
            action_name="feature/implement",
        )
        second = state.board_add_task(
            "Second armed queued task",
            "g",
            lane="To Do",
            id="second",
            action_name="feature/implement",
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        state.auto_dispatch_queue_add("g", first.id, max_concurrent=1)
        state.auto_dispatch_queue_add("g", second.id, max_concurrent=1)

        async def fail_if_batch_dispatches(payload):
            self.fail(
                "already-queued batch entries should update queue intent, "
                f"not dispatch synchronously: {payload}"
            )

        handler = self.mcp_mod.create_mcp_handler(
            fail_if_batch_dispatches,
            state,
        )
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_batch_dispatch",
                        "arguments": {
                            "tasks": [
                                {"task": first.id, "agent_group": "cluster-a"},
                                {"task": second.id, "agent_group": "cluster-a"},
                            ],
                            "max_concurrent": 2,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        payload = json.loads(response.payload["result"]["content"][0]["text"])
        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["cap_raised", "cap_raised"],
        )
        self.assertEqual(
            [item["agent_group"] for item in payload["results"]],
            ["cluster-a", "cluster-a"],
        )
        self.assertEqual(
            [entry.agent_group for entry in state.auto_dispatch_queues["g"]],
            ["cluster-a", "cluster-a"],
        )

        calls = []

        async def pump_handle_command(command_payload):
            calls.append(dict(command_payload))
            task = state.board_tasks[command_payload["id"]]
            if command_payload.get("create_agent"):
                agent = self.state_mod.AgentCell(
                    id="worker-1",
                    name="Worker 1",
                    group="g",
                    cell_type="agent",
                    session_id="sess-worker-1",
                    status="running",
                    created_by_engineer_id=engineer.id,
                    owner_engineer_id=engineer.id,
                    current_task_id=task.id,
                )
                state.agents[agent.id] = agent
                state.groups["g"].append(agent.id)
                task.agent_id = agent.id
                task.lane = "In Progress"
                return {"type": "ok", "task_id": task.id,
                        "agent_id": agent.id}

            self.assertEqual(command_payload.get("agent_id"), "worker-1")
            agent = state.agents["worker-1"]
            task.agent_id = agent.id
            task.lane = "In Progress"
            agent.current_task_id = task.id
            return {"type": "ok", "task_id": task.id, "agent_id": agent.id}

        dispatch_mod = importlib.reload(
            importlib.import_module("torque.server_dispatch")
        )

        first_pump = await dispatch_mod._pump_auto_dispatch_queue(
            state,
            pump_handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual([call["id"] for call in calls], [first.id])
        self.assertTrue(calls[0].get("create_agent"))
        self.assertEqual(first_pump[0]["agent_id"], "worker-1")
        self.assertEqual(state.board_tasks[first.id].agent_id, "worker-1")
        self.assertEqual(state.board_tasks[second.id].agent_id, "")
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].target_agent_id,
            "worker-1",
        )

        state.agents["worker-1"].current_task_id = ""
        state.board_tasks[first.id].lane = "Done"

        second_pump = await dispatch_mod._pump_auto_dispatch_queue(
            state,
            pump_handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual([call["id"] for call in calls], [first.id, second.id])
        self.assertFalse(calls[1].get("create_agent", False))
        self.assertEqual(calls[1].get("agent_id"), "worker-1")
        self.assertEqual(second_pump[0]["agent_id"], "worker-1")
        self.assertEqual(state.board_tasks[second.id].agent_id, "worker-1")
        self.assertNotIn("g", state.auto_dispatch_queues)
        self.assertEqual(
            [
                agent_id for agent_id in state.agents
                if agent_id.startswith("worker-")
            ],
            ["worker-1"],
        )

    async def test_engineer_batch_dispatch_provider_reaches_dispatch_payload(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        task = state.board_add_task("Batch provider worker", "g", lane="Backlog")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_batch_dispatch",
                        "arguments": {
                            "tasks": [{"task": task.id}],
                            "provider": "claude-code",
                            "max_concurrent": 1,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertEqual(calls[0]["agent_type"], "claude-code")

    async def test_engineer_task_dispatch_provider_alias_reaches_dispatch_payload(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        task = state.board_add_task("Task provider worker", "g", lane="Backlog")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_task_dispatch",
                        "arguments": {
                            "task": task.id,
                            "provider": "claude-code",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertEqual(calls[0]["agent_type"], "claude-code")

    async def test_engineer_task_dispatch_provider_override_falls_back_when_disabled(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=False,
        )
        task = state.board_add_task("Stale provider override", "g", lane="Backlog")
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        with self.assertLogs("torque", level="WARNING") as logs:
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_task_dispatch",
                            "arguments": {
                                "task": task.id,
                                "agent_type": "claude-code",
                            },
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertNotIn("agent_type", calls[0])
        self.assertIn("falling back to group default", "\n".join(logs.output))

    async def test_engineer_task_dispatch_records_simple_serial_hintable_shape(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        task = state.board_add_task("Simple serial dispatch", "g", lane="Backlog")

        async def fake_handle_command(payload):
            return {
                "type": "ok",
                "task_id": payload["id"],
                "agent_id": "worker-1",
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_task_dispatch",
                        "arguments": {"task": task.id},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        events = state.engineer_dispatch_shape_events(engineer.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_tool"], "engineer_task_dispatch")
        self.assertEqual(events[0]["shape"], "serial")
        self.assertTrue(events[0]["hintable"])
        self.assertEqual(events[0]["task_ids"], [task.id])
        self.assertFalse(events[0]["metadata"]["has_launch_overrides"])

    async def test_engineer_task_dispatch_records_overrides_and_existing_agent_as_not_hintable(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            owner_engineer_id=engineer.id,
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["g"] = [engineer.id, worker.id]
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        override_task = state.board_add_task("Override dispatch", "g", lane="Backlog")
        warm_task = state.board_add_task("Existing agent dispatch", "g", lane="Backlog")

        async def fake_handle_command(payload):
            return {
                "type": "ok",
                "task_id": payload["id"],
                "agent_id": payload.get("agent_id", "worker-created"),
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        for request_id, arguments in (
            (1, {"task": override_task.id, "name": "custom-worker"}),
            (2, {"task": warm_task.id, "agent": worker.id}),
        ):
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_task_dispatch",
                            "arguments": arguments,
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            self.assertFalse(response.payload["result"]["isError"])

        events = state.engineer_dispatch_shape_events(engineer.id)
        self.assertEqual([event["shape"] for event in events],
                         ["warm_cluster", "serial"])
        self.assertFalse(events[0]["hintable"])
        self.assertTrue(events[0]["metadata"]["existing_agent"])
        self.assertFalse(events[1]["hintable"])
        self.assertTrue(events[1]["metadata"]["has_launch_overrides"])

    async def test_engineer_task_dispatch_skips_dispatch_action_missing_metric(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        task = state.board_add_task("Missing action", "g", lane="Backlog")

        async def fake_handle_command(_payload):
            return {
                "type": "dispatch_action_missing",
                "action_name": "feature/review",
            }

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        response = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_task_dispatch",
                        "arguments": {"task": task.id},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )

        self.assertFalse(response.payload["result"]["isError"])
        self.assertEqual(state.engineer_dispatch_shape_events(engineer.id), [])

    async def test_engineer_batch_dispatch_records_batch_and_warm_cluster_shapes(self):
        async def run_batch(agent_groups):
            state = self.state_mod.MatrixState()
            engineer = self.state_mod.AgentCell(
                id="engineer-1",
                name="Engineer",
                group="g",
                cell_type="agent",
                kind="engineer",
            )
            state.agents[engineer.id] = engineer
            state.groups["g"] = [engineer.id]
            state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
            tasks = [
                state.board_add_task(f"Batch {idx}", "g", lane="Backlog")
                for idx in range(len(agent_groups))
            ]
            calls = []

            async def fake_handle_command(payload):
                calls.append(dict(payload))
                task = state.board_tasks[payload["id"]]
                agent_id = payload.get("agent_id") or f"worker-{len(calls)}"
                if agent_id not in state.agents:
                    state.agents[agent_id] = self.state_mod.AgentCell(
                        id=agent_id,
                        name=agent_id,
                        group="g",
                        cell_type="agent",
                        owner_engineer_id=engineer.id,
                        current_task_id=task.id,
                    )
                    state.groups["g"].append(agent_id)
                task.agent_id = agent_id
                task.lane = "In Progress"
                return {
                    "type": "ok",
                    "task_id": task.id,
                    "agent_id": agent_id,
                }

            handler = self.mcp_mod.create_mcp_handler(
                fake_handle_command,
                state,
            )
            entries = []
            for task, agent_group in zip(tasks, agent_groups):
                entry = {"task": task.id}
                if agent_group:
                    entry["agent_group"] = agent_group
                entries.append(entry)
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "engineer_batch_dispatch",
                            "arguments": {
                                "tasks": entries,
                                "max_concurrent": 5,
                            },
                        },
                    },
                    headers={"X-Torque-Cell-Id": engineer.id},
                )
            )
            self.assertFalse(response.payload["result"]["isError"])
            return state.engineer_dispatch_shape_events(engineer.id)[0]

        batch_event = await run_batch(["", ""])
        self.assertEqual(batch_event["shape"], "batch")
        self.assertFalse(batch_event["hintable"])
        self.assertEqual(batch_event["task_count"], 2)
        self.assertEqual(batch_event["metadata"]["independent_entry_count"], 2)
        self.assertEqual(batch_event["metadata"]["clustered_entry_count"], 0)

        warm_event = await run_batch(["cluster-a", "cluster-a", ""])
        self.assertEqual(warm_event["shape"], "warm_cluster")
        self.assertEqual(warm_event["task_count"], 3)
        self.assertEqual(warm_event["metadata"]["clustered_entry_count"], 2)
        self.assertEqual(warm_event["metadata"]["independent_entry_count"], 1)

    async def test_engineer_architect_message_tools_require_hiring_architect(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        user_owned_engineer = self.state_mod.AgentCell(
            id="engineer-user",
            name="User Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        hired_engineer = self.state_mod.AgentCell(
            id="engineer-hired",
            name="Hired Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        legacy_empty_engineer = self.state_mod.AgentCell(
            id="engineer-empty",
            name="Legacy Empty Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id="",
        )
        for cell in (
            architect,
            user_owned_engineer,
            hired_engineer,
            legacy_empty_engineer,
        ):
            state.agents[cell.id] = cell
        state.groups["g"] = [
            architect.id,
            user_owned_engineer.id,
            hired_engineer.id,
            legacy_empty_engineer.id,
        ]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        async def listed_names(cell_id):
            response = await handler(
                FakeRequest(
                    {"jsonrpc": "2.0", "id": cell_id, "method": "tools/list"},
                    headers={"X-Torque-Cell-Id": cell_id},
                )
            )
            return {tool["name"] for tool in response.payload["result"]["tools"]}

        for cell in (user_owned_engineer, legacy_empty_engineer):
            names = await listed_names(cell.id)
            self.assertNotIn("supervisor_message", names)
            self.assertNotIn("agent_reply", names)

            search = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": f"search-{cell.id}",
                        "method": "tools/call",
                        "params": {
                            "name": "tool_search",
                            "arguments": {"query": "supervisor message"},
                        },
                    },
                    headers={"X-Torque-Cell-Id": cell.id},
                )
            )
            payload = self._parse_functions_block(
                search.payload["result"]["content"][0]["text"]
            )
            self.assertEqual(payload["tools"], [])

            for tool_name in ("engineer_message_architect", "engineer_reply"):
                response = await handler(
                    FakeRequest(
                        {
                            "jsonrpc": "2.0",
                            "id": f"{cell.id}-{tool_name}",
                            "method": "tools/call",
                            "params": {"name": tool_name, "arguments": {}},
                        },
                        headers={"X-Torque-Cell-Id": cell.id},
                    )
                )
                self.assertIn("error", response.payload)
                self.assertIn("Unknown tool", response.payload["error"]["message"])

        hired_names = await listed_names(hired_engineer.id)
        self.assertIn("supervisor_message", hired_names)
        self.assertIn("agent_reply", hired_names)

    async def test_tool_search_select_and_keyword_return_functions_block(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.groups["g"] = [engineer.id, architect.id]

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)

        exact = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "tool_search",
                        "arguments": {
                            "query": "select:telemetry_query",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(exact.payload["result"]["isError"])
        exact_payload = self._parse_functions_block(
            exact.payload["result"]["content"][0]["text"]
        )
        self.assertEqual(
            [tool["name"] for tool in exact_payload["tools"]],
            ["telemetry_query"],
        )
        self.assertNotIn("deferred", exact_payload["tools"][0])

        keyword = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_tool_search",
                        "arguments": {
                            "query": "mcp call history",
                            "max_results": 3,
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        keyword_payload = self._parse_functions_block(
            keyword.payload["result"]["content"][0]["text"]
        )
        self.assertIn(
            "telemetry_query",
            [tool["name"] for tool in keyword_payload["tools"]],
        )

        architect_exact = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "tool_search",
                        "arguments": {
                            "query": "select:telemetry_query",
                        },
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        architect_payload = self._parse_functions_block(
            architect_exact.payload["result"]["content"][0]["text"]
        )
        self.assertEqual(
            [tool["name"] for tool in architect_payload["tools"]],
            ["telemetry_query"],
        )

    async def test_deferred_tools_remain_callable_after_lazy_registration(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.groups["g"] = [engineer.id, architect.id]
        state.update_architect_settings("g", architect_provider="codex")

        async def fake_handle_command(payload):
            if payload.get("cmd") == "list_specializations":
                return {"type": "specializations", "items": []}
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        architect_settings = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "architect_get_architect_settings",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        self.assertFalse(architect_settings.payload["result"]["isError"])
        settings_payload = json.loads(
            architect_settings.payload["result"]["content"][0]["text"]
        )
        self.assertEqual(settings_payload["settings"]["architect_provider"], "codex")

        engineer_show = await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "engineer_specializations_list",
                        "arguments": {},
                    },
                },
                headers={"X-Torque-Cell-Id": engineer.id},
            )
        )
        self.assertFalse(engineer_show.payload["result"]["isError"])

    async def test_removed_architect_tools_are_not_registered_or_callable(self):
        state = self.state_mod.MatrixState()
        architect = self.state_mod.AgentCell(
            id="architect-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.groups["g"] = [architect.id]

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        handler = self.mcp_mod.create_mcp_handler(fake_handle_command, state)
        listed = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": architect.id},
            )
        )
        tool_names = {
            tool["name"] for tool in listed.payload["result"]["tools"]
        }
        removed_names = [
            "architect_" + "update_architect_settings",
            "architect_" + "workspace_overview",
        ]
        for tool_name in removed_names:
            self.assertNotIn(tool_name, tool_names)
            response = await handler(
                FakeRequest(
                    {
                        "jsonrpc": "2.0",
                        "id": tool_name,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {}},
                    },
                    headers={"X-Torque-Cell-Id": architect.id},
                )
            )
            self.assertIn("error", response.payload)
            self.assertIn("Unknown tool", response.payload["error"]["message"])

    def test_removed_architect_mcp_tools_have_no_application_callers(self):
        removed_names = [
            "architect_" + "update_architect_settings",
            "architect_" + "workspace_overview",
        ]
        root = Path(__file__).resolve().parents[1]
        paths = list((root / "torque").glob("*.py")) + [root / "bin" / "torque"]
        for path in paths:
            text = path.read_text()
            for tool_name in removed_names:
                self.assertNotIn(tool_name, text, str(path))

    def test_torque_ask_tool_description_marks_it_as_blocking(self):
        ask_tool = next(
            tool for tool in self.mcp_mod.TOOLS
            if tool["name"] == "torque_ask"
        )
        reply_tool = next(
            tool for tool in self.mcp_mod.TOOLS
            if tool["name"] == "torque_reply"
        )

        self.assertIn("blocking human decision or approval", ask_tool["description"])
        self.assertIn("Do not use this for status updates", ask_tool["description"])
        self.assertIn("include the task id", reply_tool["description"])
        self.assertIn("task", reply_tool["inputSchema"]["properties"])

    async def test_torque_context_includes_combined_task_artifacts(self):
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
            "torque_context",
            {},
            cell.id,
            fake_handle_command,
            state,
        )

        self.assertFalse(is_error)
        payload = json.loads(text)
        self.assertEqual(len(payload["tasks"]["task-1"]["task_artifacts"]), 2)
        self.assertEqual(payload["tasks"]["task-1"]["task_artifacts"][0]["url"], "/attachments/task-1/image.png")

    async def test_torque_context_includes_upstream_artifacts_for_derived_tasks(self):
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
            "torque_context",
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
            "params": {"name": "torque_context", "arguments": {}},
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

    async def test_architect_journal_gates_system_generated_entry_types(self):
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

        architect_journal = next(
            tool for tool in self.mcp_mod.ARCHITECT_TOOLS
            if tool["name"] == "architect_journal"
        )
        self.assertEqual(
            architect_journal["inputSchema"]["properties"]["type"]["enum"],
            ["decision", "observation", "checkpoint", "plan"],
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        async def call(entry_type, idx):
            body = {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": "architect_journal",
                    "arguments": {
                        "type": entry_type,
                        "entry": f"{entry_type} entry",
                    },
                },
            }
            return await self.mcp_mod.dispatch_mcp_rpc_body(
                body,
                cell_id=architect.id,
                handle_command=fake_handle_command,
                state=state,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.state_mod, "DATA_DIR", Path(tmpdir)):
                for idx, entry_type in enumerate(
                    ["decision", "observation", "checkpoint", "plan"],
                    start=1,
                ):
                    payload, status = await call(entry_type, idx)
                    self.assertEqual(status, 200)
                    self.assertFalse(payload["result"]["isError"])
                    result = json.loads(
                        payload["result"]["content"][0]["text"]
                    )
                    self.assertEqual(result["type"], entry_type)

                for idx, entry_type in enumerate(
                    ["note_dismissed", "qa"],
                    start=10,
                ):
                    payload, status = await call(entry_type, idx)
                    self.assertEqual(status, 200)
                    self.assertTrue(payload["result"]["isError"])
                    self.assertEqual(
                        payload["result"]["content"][0]["text"],
                        (
                            "type must be one of: decision, observation, "
                            "checkpoint, plan"
                        ),
                    )

                entries = state.architect_journal_read(architect.id, limit=10)

        self.assertEqual(len(entries), 4)
        self.assertCountEqual(
            [entry["type"] for entry in entries],
            ["decision", "observation", "checkpoint", "plan"],
        )

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
            engineer_agent_id=engineer.id
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
            if payload.get("cmd") == "engineer_journal_append":
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
            "params": {"name": "torque_context", "arguments": {}},
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
