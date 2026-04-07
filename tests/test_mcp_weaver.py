from datetime import datetime
import importlib
import json
import sys
import types
import unittest


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class WeaverBatchDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        return state, weaver

    def _add_task(self, state, task_id, title, lane="Backlog", agent_id=""):
        task = self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group="g",
            lane=lane,
            agent_id=agent_id,
        )
        state.board_tasks[task.id] = task
        return task

    def _make_handle_command(self, state, fail_task_ids=None):
        fail_task_ids = fail_task_ids or {}
        counter = {"value": 0}

        async def handle_command(data):
            self.assertEqual(data["cmd"], "dispatch_task")
            tid = data["id"]
            task = state.board_tasks[tid]
            if tid in fail_task_ids:
                return fail_task_ids[tid]
            if data.get("create_agent"):
                counter["value"] += 1
                aid = f"agent-{counter['value']}"
                cell = self.state_mod.AgentCell(
                    id=aid,
                    name=f"worker-{counter['value']}",
                    group="g",
                    cell_type="agent",
                )
                cell.current_task_id = tid
                state.agents[aid] = cell
                state.groups["g"].append(aid)
                task.agent_id = aid
                task.lane = "In Progress"
                return None

            agent_id = data["agent_id"]
            cell = state.agents[agent_id]
            if cell.current_task_id and cell.current_task_id != tid:
                task.agent_id = agent_id
                task.lane = "To Do"
                return {
                    "type": "queued",
                    "task_id": tid,
                    "agent_id": agent_id,
                }

            cell.current_task_id = tid
            task.agent_id = agent_id
            task.lane = "In Progress"
            return None

        return handle_command

    def test_ask_and_note_tool_descriptions_explain_pause_behavior(self):
        ask_tool = next(
            tool for tool in self.mcp_weaver_mod.WEAVER_TOOLS
            if tool["name"] == "weaver_ask"
        )
        note_tool = next(
            tool for tool in self.mcp_weaver_mod.WEAVER_TOOLS
            if tool["name"] == "weaver_note"
        )

        self.assertIn("blocking human decision", ask_tool["description"])
        self.assertIn("use weaver_note for those", ask_tool["description"])
        self.assertIn("next-wave proposals", note_tool["description"])
        self.assertIn("does not pause event delivery", note_tool["description"])

    async def _dispatch(self, state, weaver, args, handle_command):
        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_batch_dispatch",
            args,
            handle_command,
            state,
            cell_id=weaver.id,
        )
        self.assertFalse(is_error, text)
        return json.loads(text)

    async def test_batch_dispatch_respects_capacity(self):
        state, weaver = self._make_state()
        active = self.state_mod.AgentCell(
            id="agent-active",
            name="active",
            group="g",
            cell_type="agent",
            current_task_id="task-active",
        )
        state.agents[active.id] = active
        state.groups["g"].append(active.id)
        self._add_task(
            state,
            "task-active",
            "Already running",
            lane="In Progress",
            agent_id=active.id,
        )
        self._add_task(state, "task-1", "First")
        self._add_task(state, "task-2", "Second")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [{"task": "task-1"}, {"task": "task-2"}],
                "max_concurrent": 2,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(payload["active_before"], 1)
        self.assertEqual(payload["active_after"], 2)
        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["dispatched", "deferred"],
        )
        self.assertEqual(
            [entry.task_id for entry in state.auto_dispatch_queues["g"]],
            ["task-2"],
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].max_concurrent,
            2,
        )

    async def test_batch_dispatch_continues_after_validation_failure(self):
        state, weaver = self._make_state()
        self._add_task(state, "task-1", "Valid task")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [{"task": "missing"}, {"task": "task-1"}],
                "max_concurrent": 1,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(payload["results"][0]["status"], "failed")
        self.assertEqual(payload["results"][0]["reason"], "task_not_found")
        self.assertEqual(payload["results"][1]["status"], "dispatched")

    async def test_batch_dispatch_queues_tasks_in_same_agent_group(self):
        state, weaver = self._make_state()
        self._add_task(state, "task-1", "One")
        self._add_task(state, "task-2", "Two")
        self._add_task(state, "task-3", "Three")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [
                    {"task": "task-1", "agent_group": "oneshot"},
                    {"task": "task-2", "agent_group": "oneshot"},
                    {"task": "task-3"},
                ],
                "max_concurrent": 1,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["dispatched", "queued", "deferred"],
        )
        self.assertEqual(
            payload["results"][0]["agent_id"],
            payload["results"][1]["agent_id"],
        )

    async def test_batch_dispatch_persists_deferred_agent_group_entries(self):
        state, weaver = self._make_state()
        active = self.state_mod.AgentCell(
            id="agent-active",
            name="active",
            group="g",
            cell_type="agent",
            current_task_id="task-active",
        )
        state.agents[active.id] = active
        state.groups["g"].append(active.id)
        self._add_task(
            state,
            "task-active",
            "Already running",
            lane="In Progress",
            agent_id=active.id,
        )
        self._add_task(state, "task-1", "First")
        self._add_task(state, "task-2", "Second")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [
                    {"task": "task-1", "agent_group": "followup"},
                    {"task": "task-2", "agent_group": "followup"},
                ],
                "max_concurrent": 1,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["deferred", "deferred"],
        )
        self.assertEqual(
            [entry.task_id for entry in state.auto_dispatch_queues["g"]],
            ["task-1", "task-2"],
        )
        self.assertEqual(
            [entry.agent_group for entry in state.auto_dispatch_queues["g"]],
            ["followup", "followup"],
        )

    async def test_batch_dispatch_dispatches_without_overlap_confirmation(self):
        state, weaver = self._make_state()
        active = self.state_mod.AgentCell(
            id="agent-active",
            name="active",
            group="g",
            cell_type="agent",
            current_task_id="task-active",
        )
        state.agents[active.id] = active
        state.groups["g"].append(active.id)
        self._add_task(
            state,
            "task-active",
            "Auth flow implementation",
            lane="In Progress",
            agent_id=active.id,
        )
        self._add_task(state, "task-1", "Auth flow review")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [{"task": "task-1"}],
                "max_concurrent": 2,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(
            payload["results"][0]["status"],
            "dispatched",
        )

    async def test_batch_dispatch_excludes_weaver_from_active_count(self):
        state, weaver = self._make_state()
        weaver.current_task_id = "weaver-task"
        self._add_task(
            state,
            "weaver-task",
            "Orchestration",
            lane="In Progress",
            agent_id=weaver.id,
        )
        self._add_task(state, "task-1", "Worker task")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [{"task": "task-1"}],
                "max_concurrent": 1,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(payload["active_before"], 0)
        self.assertEqual(payload["results"][0]["status"], "dispatched")

    async def test_batch_dispatch_continues_after_dispatch_error(self):
        state, weaver = self._make_state()
        self._add_task(state, "task-1", "Broken task")
        self._add_task(state, "task-2", "Good task")

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [{"task": "task-1"}, {"task": "task-2"}],
                "max_concurrent": 1,
            },
            self._make_handle_command(
                state,
                fail_task_ids={
                    "task-1": {"type": "error", "message": "boom"},
                },
            ),
        )

        self.assertEqual(payload["results"][0]["status"], "failed")
        self.assertEqual(payload["results"][0]["reason"], "dispatch_error")
        self.assertEqual(payload["results"][1]["status"], "dispatched")

    async def test_batch_dispatch_blocks_tasks_with_unmet_dependencies(self):
        state, weaver = self._make_state()
        self._add_task(state, "dep-1", "Finish migration", lane="To Do")
        blocked = state.board_add_task(
            "Ship release",
            "g",
            id="task-1",
            depends_on=["dep-1"],
        )
        ready = state.board_add_task(
            "Write changelog",
            "g",
            id="task-2",
        )

        self.assertIsNotNone(blocked)
        self.assertIsNotNone(ready)

        payload = await self._dispatch(
            state,
            weaver,
            {
                "tasks": [{"task": "task-1"}, {"task": "task-2"}],
                "max_concurrent": 1,
            },
            self._make_handle_command(state),
        )

        self.assertEqual(payload["results"][0]["status"], "failed")
        self.assertEqual(
            payload["results"][0]["reason"],
            "blocked_by_dependencies",
        )
        self.assertIn("Finish migration", payload["results"][0]["message"])
        self.assertEqual(payload["results"][1]["status"], "dispatched")


class WeaverDispatchToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    async def test_dispatch_forwards_caller_window_context_when_creating_agent(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
            session_id="session-123",
            window_id="window-abc",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        task = state.board_add_task("Investigate bug", "g")

        self.assertIsNotNone(task)
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            return {"type": "ok"}

        _, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_dispatch",
            {"task": task.id},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(captured["cmd"], "dispatch_task")
        self.assertEqual(captured["id"], task.id)
        self.assertTrue(captured["create_agent"])
        self.assertEqual(captured["target_session_id"], weaver.session_id)
        self.assertEqual(captured["target_window_id"], weaver.window_id)

    async def test_dispatch_forwards_agent_type_and_command_for_new_agent(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
            session_id="session-123",
            window_id="window-abc",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        task = state.board_add_task("Investigate bug", "g")

        self.assertIsNotNone(task)
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            return {"type": "ok"}

        _, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_dispatch",
            {
                "task": task.id,
                "agent_type": "codex",
                "command": "codex --model gpt-5",
            },
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(captured["agent_type"], "codex")
        self.assertEqual(captured["command"], "codex --model gpt-5")

    async def test_dispatch_to_existing_agent_forwards_agent_id_only(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
            session_id="session-123",
            window_id="window-abc",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            group="g",
            slug="worker-one",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.agents[worker.id] = worker
        state.groups["g"] = [weaver.id, worker.id]
        task = state.board_add_task("Investigate bug", "g")

        self.assertIsNotNone(task)
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            return {"type": "ok"}

        _, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_dispatch",
            {"task": task.id, "agent": worker.slug},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            captured,
            {"cmd": "dispatch_task", "id": task.id, "agent_id": worker.id},
        )

    async def test_dispatch_ignores_legacy_force_flag(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        task = state.board_add_task("Investigate overlap", "g")

        self.assertIsNotNone(task)
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            return {"type": "ok"}

        _, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_dispatch",
            {"task": task.id, "force": True},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertNotIn("force", captured)


class WeaverBoardSummaryToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    async def test_board_summary_returns_compact_group_overview(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            slug="weaver",
            cell_type="agent",
            status="idle",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            group="g",
            slug="worker-one",
            cell_type="agent",
            status="running",
            agent_type="codex",
            current_task_id="task-progress",
        )
        stopped = self.state_mod.AgentCell(
            id="worker-2",
            name="Worker Two",
            group="g",
            slug="worker-two",
            cell_type="agent",
            status="stopped",
            agent_type="claude-code",
        )
        other_group = self.state_mod.AgentCell(
            id="worker-3",
            name="Other Group Worker",
            group="other",
            slug="other-worker",
            cell_type="agent",
            status="running",
        )

        state.agents[weaver.id] = weaver
        state.agents[worker.id] = worker
        state.agents[stopped.id] = stopped
        state.agents[other_group.id] = other_group
        state.groups["g"] = [weaver.id, worker.id, stopped.id]
        state.groups["other"] = [other_group.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )

        state.board_add_task(
            "Ready task",
            "g",
            lane="Backlog",
            id="task-ready",
            labels=["ready"],
        )
        state.board_add_task(
            "Deferred task",
            "g",
            lane="To Do",
            id="task-deferred",
            labels=["deferred"],
        )
        state.board_add_task(
            "Active task",
            "g",
            lane="In Progress",
            id="task-progress",
            agent_id=worker.id,
        )
        state.board_add_task(
            "Review plan",
            "g",
            lane="Backlog",
            id="ask-1",
            labels=["loom:human"],
            parent_task_id="task-progress",
        )
        state.board_add_task(
            "Other group task",
            "other",
            lane="Backlog",
            id="other-task",
            labels=["ready"],
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_summary",
            {},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        summary = json.loads(text)

        self.assertEqual(summary["group"], "g")
        self.assertEqual(summary["tasks_total"], 4)
        self.assertEqual(
            summary["lanes"],
            {"Backlog": 2, "To Do": 1, "In Progress": 1, "Done": 0},
        )
        self.assertEqual(summary["labels"], {"ready": 1, "deferred": 1})
        self.assertEqual(summary["asks"]["count"], 1)
        self.assertFalse(summary["asks"]["truncated"])
        self.assertEqual(
            summary["asks"]["items"],
            [{
                "id": "ask-1",
                "title": "Review plan",
                "parent_task_id": "task-progress",
            }],
        )
        self.assertEqual(
            summary["task_health"]["counts"],
            {
                "healthy": 2,
                "blocked": 2,
                "stale-in-progress": 0,
                "idle-risk": 0,
                "stalled": 0,
                "thrashing": 0,
            },
        )
        self.assertEqual(
            [item["id"] for item in summary["task_health"]["unhealthy"]],
            ["task-progress", "ask-1"],
        )
        self.assertFalse(summary["task_health"]["truncated"])
        self.assertEqual(summary["agents"]["total"], 2)
        self.assertEqual(summary["agents"]["active_count"], 1)
        self.assertEqual(
            summary["agents"]["by_status"],
            {"idle": 0, "running": 1, "error": 0, "stopped": 1},
        )
        self.assertEqual(
            summary["agents"]["active"],
            [{
                "id": "worker-1",
                "name": "Worker One",
                "slug": "worker-one",
                "type": "codex",
                "status": "running",
                "current_task_id": "task-progress",
                "current_task": "Active task",
                "needs_attention": False,
            }],
        )
        self.assertNotIn("tasks", summary)

    async def test_board_summary_omits_dispatch_overlap_summary(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            slug="weaver",
            cell_type="agent",
            status="idle",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_summary",
            {},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        summary = json.loads(text)
        self.assertNotIn("dispatch_overlap", summary)

    async def test_task_show_includes_health_snapshot(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            slug="weaver",
            cell_type="agent",
            status="idle",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            group="g",
            slug="worker-one",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.agents[worker.id] = worker
        state.groups["g"] = [weaver.id, worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )

        task = state.board_add_task(
            "Investigate regression",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id=worker.id,
        )
        self.assertIsNotNone(task)
        task.created_at = "2026-04-06T00:00:00+00:00"
        task.updated_at = "2026-04-06T00:00:00+00:00"
        state.recompute_task_health(
            now_ts=datetime.fromisoformat(
                "2026-04-06T00:21:00+00:00"
            ).timestamp(),
            persist=False,
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_show",
            {"task": "task-1"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        shown = json.loads(text)
        self.assertEqual(shown["health_state"], "stalled")
        self.assertEqual(
            shown["health_details"]["reasons"],
            ["no_progress_timeout"],
        )

    async def test_board_list_can_filter_by_health_state(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            slug="weaver",
            cell_type="agent",
            status="idle",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )

        stalled = state.board_add_task(
            "Investigate regression",
            "g",
            lane="In Progress",
            id="task-1",
        )
        healthy = state.board_add_task(
            "Write release notes",
            "g",
            lane="Backlog",
            id="task-2",
        )
        self.assertIsNotNone(stalled)
        self.assertIsNotNone(healthy)
        stalled.health_state = "stalled"
        stalled.health_since = "2026-04-06T00:21:00+00:00"

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_list",
            {"health": "stalled"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        payload = json.loads(text)
        self.assertEqual(list(payload["lanes"].keys()), ["In Progress"])
        self.assertEqual(
            [item["id"] for item in payload["lanes"]["In Progress"]],
            ["task-1"],
        )

    async def test_task_show_and_board_list_include_external_ticket_metadata(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            slug="weaver",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        task = state.board_add_task(
            "Investigate sync",
            "g",
            id="task-ext",
            provider="github",
            external_id="openai/example#7",
            external_url="https://github.com/openai/example/issues/7",
        )
        self.assertIsNotNone(task)

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_show",
            {"task": "task-ext"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )
        self.assertFalse(is_error)
        shown = json.loads(text)
        self.assertEqual(shown["provider"], "github")
        self.assertEqual(shown["external_id"], "openai/example#7")
        self.assertEqual(
            shown["external_url"],
            "https://github.com/openai/example/issues/7",
        )

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_list",
            {},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )
        self.assertFalse(is_error)
        lanes = json.loads(text)["lanes"]
        self.assertEqual(lanes["Backlog"][0]["provider"], "github")
        self.assertEqual(
            lanes["Backlog"][0]["external_id"],
            "openai/example#7",
        )


class WeaverMergeToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    async def test_merge_forwards_cleanup_flags_only_when_enabled(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {"type": "ok", "clean": True}
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {
                        "close_agent": True,
                        "remove_worktree": True,
                        "agent_closed": True,
                        "worktree_removed": True,
                        "errors": [],
                    },
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_merge",
            {
                "agent": cell.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            },
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertFalse(is_error)
        result = json.loads(text)
        self.assertEqual(result["sha"], "abc123")
        self.assertEqual(
            calls,
            [
                {"cmd": "worktree_check_merge", "id": cell.id},
                {
                    "cmd": "worktree_merge",
                    "id": cell.id,
                    "close_agent_on_merge": True,
                    "remove_worktree_on_merge": True,
                },
            ],
        )

    async def test_merge_reports_cleanup_errors_clearly(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        async def fake_handle_command(payload):
            if payload["cmd"] == "worktree_check_merge":
                return {"type": "ok", "clean": True}
            if payload["cmd"] == "worktree_merge":
                return {
                    "type": "worktree_merge",
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {
                        "close_agent": True,
                        "remove_worktree": True,
                        "agent_closed": True,
                        "worktree_removed": False,
                        "errors": ["Failed to remove worktree for 'Worker'."],
                    },
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_merge",
            {
                "agent": cell.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            },
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertTrue(is_error)
        self.assertIn("cleanup failed", text)
        self.assertIn("Failed to remove worktree for 'Worker'.", text)

    async def test_merge_stops_when_conflicts_are_detected(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "ok",
                    "clean": False,
                    "conflicts": ["README.md"],
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_merge",
            {"agent": cell.id},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Merge has conflicts", text)
        self.assertIn("README.md", text)
        self.assertIn("weaver_rebase", text)
        self.assertEqual(
            calls,
            [{"cmd": "worktree_check_merge", "id": cell.id}],
        )

    async def test_merge_reports_boundary_blockers_without_conflict_flow(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        async def fake_handle_command(payload):
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "clean": False,
                    "error": "Latest task boundary is no longer cleanly mergeable because a follow-up task has already started.",
                    "boundary": {
                        "task_id": "task-1",
                        "task_title": "Implement feature",
                    },
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_merge",
            {"agent": cell.id},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Latest task boundary", text)


class WeaverDiffToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    async def test_diff_summary_forwards_filters_and_returns_json(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            return {
                "type": "ok",
                "summary": {
                    "stats": {"files": 1, "insertions": 3, "deletions": 1},
                    "files": [{
                        "path": "config/app.yml",
                        "status": "modified",
                        "insertions": 3,
                        "deletions": 1,
                        "binary": False,
                        "signals": ["config"],
                    }],
                    "interesting_files": [{
                        "path": "config/app.yml",
                        "signals": ["config"],
                    }],
                    "signal_counts": {"config": 1},
                },
            }

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_diff",
            {
                "agent": cell.slug,
                "summary_only": True,
                "paths": ["config/app.yml"],
            },
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            captured,
            {
                "cmd": "worktree_diff",
                "id": cell.id,
                "stat_only": False,
                "summary_only": True,
                "paths": ["config/app.yml"],
            },
        )
        data = json.loads(text)
        self.assertEqual(data["summary"]["stats"]["files"], 1)
        self.assertEqual(data["summary"]["files"][0]["signals"], ["config"])


class WeaverRebaseToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    def test_rebase_tool_is_registered(self):
        tool = next(
            t for t in self.mcp_weaver_mod.WEAVER_TOOLS
            if t["name"] == "weaver_rebase"
        )
        self.assertIn("weaver_merge reports conflicts", tool["description"])

    def _make_cell(self):
        return self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )

    async def test_rebase_returns_post_rebase_merge_readiness(self):
        state = self.state_mod.MatrixState()
        cell = self._make_cell()
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                if len(calls) == 1:
                    return {
                        "type": "worktree_check_merge",
                        "clean": False,
                        "conflicts": [
                            {"path": "README.md", "reason": "content"},
                        ],
                    }
                return {
                    "type": "worktree_check_merge",
                    "clean": True,
                    "default_message": "Merge worker",
                    "boundary": {"task_id": "task-1"},
                    "clean_boundary": {"task_id": "task-1"},
                }
            if payload["cmd"] == "worktree_rebase":
                return {"type": "worktree_rebase", "ok": True}
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_rebase",
            {"agent": cell.slug},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertFalse(is_error)
        result = json.loads(text)
        self.assertTrue(result["merge_ready"])
        self.assertEqual(result["default_message"], "Merge worker")
        self.assertEqual(
            result["conflicts_before"],
            [{"path": "README.md", "reason": "content"}],
        )
        self.assertEqual(result["conflicts_after"], [])
        self.assertEqual(
            calls,
            [
                {"cmd": "worktree_check_merge", "id": cell.id},
                {"cmd": "worktree_rebase", "id": cell.id},
                {"cmd": "worktree_check_merge", "id": cell.id},
            ],
        )

    async def test_rebase_reports_conflict_context_when_rebase_fails(self):
        state = self.state_mod.MatrixState()
        cell = self._make_cell()
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        async def fake_handle_command(payload):
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "clean": False,
                    "conflicts": [
                        {"path": "README.md", "reason": "content"},
                        {"path": "app.py", "reason": "modify/delete"},
                    ],
                }
            if payload["cmd"] == "worktree_rebase":
                return {
                    "type": "worktree_rebase",
                    "ok": False,
                    "error": "Rebase failed — conflicts require manual resolution",
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_rebase",
            {"agent": cell.id},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Rebase failed", text)
        self.assertIn("README.md (content)", text)
        self.assertIn("app.py (modify/delete)", text)
        self.assertIn("worktree should be clean", text)

    async def test_rebase_preserves_merge_safety_checks(self):
        state = self.state_mod.MatrixState()
        cell = self._make_cell()
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "clean": False,
                    "error": "Latest task boundary is no longer cleanly mergeable because a follow-up task has already started.",
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_rebase",
            {"agent": cell.id},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Latest task boundary", text)
        self.assertEqual(
            calls,
            [{"cmd": "worktree_check_merge", "id": cell.id}],
        )

    async def test_rebase_surfaces_command_errors(self):
        state = self.state_mod.MatrixState()
        cell = self._make_cell()
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        async def fake_handle_command(payload):
            if payload["cmd"] == "worktree_check_merge":
                return {"type": "worktree_check_merge", "clean": True}
            if payload["cmd"] == "worktree_rebase":
                return {
                    "type": "error",
                    "message": "git executable not available",
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_rebase",
            {"agent": cell.id},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "git executable not available")


class WeaverAgentLifecycleToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    async def test_agent_relaunch_forwards_to_handle_command(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]

        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            return {"type": "ok"}

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_agent_relaunch",
            {"agent": cell.slug},
            fake_handle_command,
            state,
            cell_id=cell.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(captured, {"cmd": "relaunch_agent", "id": cell.id})
        self.assertIn("relaunched", text)


class WeaverRecoveryToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    async def test_agent_show_includes_worktree_terminals_and_task_history(self):
        state = self.state_mod.MatrixState()
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            agent_type="codex",
            status="running",
            directory="/repo",
            git_root="/repo",
            activity="thinking",
            activity_detail="Reviewing changes",
            error_message="",
            needs_attention=True,
            tasks_dispatched=3,
            agent_session_id="session-123",
            session_tokens_in=120,
            session_tokens_out=240,
            current_task_id="task-1",
            worktree_path="/tmp/worktree",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
            worktree_dirty=True,
            worktree_diff={"files": 2, "insertions": 5, "deletions": 1},
            worktree_checkpoints=4,
            worktree_ahead=2,
            worktree_behind=1,
            worktree_merged=False,
        )
        terminal = self.state_mod.AgentCell(
            id="term-1",
            name="Logs",
            slug="worker:logs",
            group="g",
            cell_type="terminal",
            status="running",
            current_process="tail",
            current_path="/repo/logs",
            parent_id=agent.id,
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            slug="investigate-regression",
            task="Investigate regression",
            group="g",
            lane="In Progress",
            status="On Review",
            labels=["ready"],
            action_name="oneshot/feature",
            agent_id=agent.id,
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "base_branch": "main",
                "commit_sha": "abc123",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
            messages=[{"action": "progress", "message": "Halfway there"}],
        )

        state.agents[agent.id] = agent
        state.agents[terminal.id] = terminal
        state.groups["g"] = [agent.id, terminal.id]
        state._children[agent.id] = [terminal.id]
        state.board_tasks[task.id] = task

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_agent_show",
            {"agent": agent.slug},
            fake_handle_command,
            state,
            cell_id=agent.id,
        )

        self.assertFalse(is_error)
        data = json.loads(text)
        self.assertEqual(data["id"], agent.id)
        self.assertEqual(data["agent_type"], "codex")
        self.assertEqual(data["current_task_id"], task.id)
        self.assertEqual(data["worktree"]["branch"], "loom/worker")
        self.assertEqual(data["worktree"]["diff"], agent.worktree_diff)
        self.assertEqual(
            data["worktree"]["task_boundaries"][0]["boundary"]["commit_sha"],
            "abc123",
        )
        self.assertEqual(
            data["terminals"],
            [{
                "name": "Logs",
                "slug": "worker:logs",
                "status": "running",
                "current_process": "tail",
                "current_path": "/repo/logs",
            }],
        )
        self.assertEqual(
            data["tasks"],
            [{
                "id": "task-1",
                "slug": "investigate-regression",
                "title": "Investigate regression",
                "lane": "In Progress",
                "status": "On Review",
                "labels": ["ready"],
                "action": "oneshot/feature",
                "worktree_boundary": {
                    "version": "1",
                    "repo_root": "/repo",
                    "branch": "loom/worker",
                    "base_branch": "main",
                    "commit_sha": "abc123",
                    "status": "open",
                    "recorded_at": "2026-04-07T10:00:00+00:00",
                },
                "resume_after_boundary_task_id": "",
                "messages": [{"action": "progress", "message": "Halfway there"}],
            }],
        )

    async def test_journal_append_and_read_forward_group_and_filters(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )

        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "weaver_journal_append":
                return {"type": "ok", "entry": {"id": 7, "type": payload["entry_type"]}}
            if payload["cmd"] == "weaver_journal_read":
                return {
                    "type": "ok",
                    "entries": [{"id": 7, "type": payload["entry_type"] or "decision"}],
                }
            self.fail(f"Unexpected command: {payload}")

        append_text, append_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_journal",
            {"type": "decision", "entry": "Ship the safer path."},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )
        read_text, read_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_journal_read",
            {"tail": 5, "type": "decision"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(append_error)
        self.assertFalse(read_error)
        self.assertEqual(json.loads(append_text)["entry"]["id"], 7)
        self.assertEqual(json.loads(read_text)["entries"][0]["type"], "decision")
        self.assertEqual(
            calls,
            [
                {
                    "cmd": "weaver_journal_append",
                    "group": "g",
                    "entry_type": "decision",
                    "entry": "Ship the safer path.",
                },
                {
                    "cmd": "weaver_journal_read",
                    "group": "g",
                    "tail": 5,
                    "entry_type": "decision",
                },
            ],
        )


class WeaverEventAndInteractionToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)
        self.events_mod = importlib.import_module("loom.events")
        self.events_mod = importlib.reload(self.events_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(group="g")
        return state, weaver

    async def test_events_filter_by_group_and_type(self):
        state, weaver = self._make_state()
        state.panel_log = self.events_mod.PanelEventLog()
        state.panel_log.append("task_completed", "", "", "g", "Done")
        state.panel_log.append("agent_progress", "", "", "other", "Other")
        state.panel_log.append("ask_created", "", "", "g", "Need review")

        async def fake_handle_command(payload):
            self.fail(f"Unexpected handle_command call: {payload}")

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_events",
            {"types": ["ask_created"]},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        data = json.loads(text)
        self.assertEqual(
            data["events"],
            [{
                "id": 3,
                "timestamp": data["events"][0]["timestamp"],
                "kind": "ask_created",
                "cell_id": "",
                "agent_name": "",
                "group": "g",
                "message": "Need review",
                "task_id": "",
            }],
        )
        self.assertEqual(data["cursor"], 3)

    async def test_notifications_clamp_intervals_and_update_enabled_events(self):
        state, weaver = self._make_state()
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(
            group="g",
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
            enabled_events=["agent_started", "task_derived"],
        )
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            state.update_weaver_settings(
                payload["group"],
                push_interval=payload["push_interval"],
                max_interval=payload["max_interval"],
                heartbeat_interval=payload["heartbeat_interval"],
                enabled_events=payload["enabled_events"],
            )
            return {"type": "ok"}

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_notifications",
            {
                "push_interval": 5,
                "max_interval": 20,
                "heartbeat_interval": 20,
                "enable": ["agent_progress"],
                "disable": ["agent_started"],
            },
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            captured,
            {
                "cmd": "weaver_update_settings",
                "group": "g",
                "push_interval": 10,
                "max_interval": 30,
                "heartbeat_interval": 30,
                "enabled_events": ["agent_progress", "task_derived"],
            },
        )
        settings = json.loads(text)["settings"]
        self.assertEqual(settings["push_interval"], 10)
        self.assertEqual(settings["max_interval"], 30)
        self.assertEqual(settings["heartbeat_interval"], 30)
        self.assertEqual(settings["enabled_events"], ["agent_progress", "task_derived"])

    async def test_notifications_allow_disabling_idle_heartbeat(self):
        state, weaver = self._make_state()
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(
            group="g",
            heartbeat_interval=300,
        )
        captured = {}

        async def fake_handle_command(payload):
            captured.update(payload)
            state.update_weaver_settings(
                payload["group"],
                heartbeat_interval=payload["heartbeat_interval"],
            )
            return {"type": "ok"}

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_notifications",
            {"heartbeat_interval": 0},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            captured,
            {
                "cmd": "weaver_update_settings",
                "group": "g",
                "heartbeat_interval": 0,
            },
        )
        settings = json.loads(text)["settings"]
        self.assertEqual(settings["heartbeat_interval"], 0)

    async def test_ask_and_resolve_forward_expected_payloads(self):
        state, weaver = self._make_state()
        ask = state.board_add_task(
            "Need human review",
            "g",
            id="ask-1",
            labels=["loom:human"],
        )
        self.assertIsNotNone(ask)
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "weaver_ask":
                return {"type": "ok"}
            if payload["cmd"] == "resolve_ask":
                return {"type": "ok", "resolved": True}
            self.fail(f"Unexpected command: {payload}")

        ask_text, ask_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_ask",
            {"question": "Which task should go first?"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )
        resolve_text, resolve_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_resolve",
            {"task": ask.id, "answer": "Take the review task first."},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(ask_error)
        self.assertFalse(resolve_error)
        self.assertIn("Event pushes have been paused", ask_text)
        self.assertEqual(json.loads(resolve_text), {"type": "ok", "resolved": True})
        self.assertEqual(
            calls,
            [
                {
                    "cmd": "weaver_ask",
                    "group": "g",
                    "question": "Which task should go first?",
                },
                {
                    "cmd": "resolve_ask",
                    "id": ask.id,
                    "answer": "Take the review task first.",
                },
            ],
        )

    async def test_board_summary_and_task_show_include_verification_metadata(self):
        state, weaver = self._make_state()
        state.board_add_task(
            "Deploy billing changes",
            "g",
            id="task-1",
            lane="In Progress",
            verification_mode="deploy",
            verification_state="pending",
            verification_notes="Need manual smoke after deploy",
            verification_summary={
                "tests_run": "python3 -m unittest",
                "deploy_needed": True,
                "human_validation_pending": "Confirm billing dashboard loads",
            },
        )

        async def fake_handle_command(payload):
            self.fail(f"Unexpected command: {payload}")

        summary_text, summary_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_board_summary",
            {},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )
        task_text, task_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_show",
            {"task": "task-1"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(summary_error)
        self.assertFalse(task_error)

        summary = json.loads(summary_text)
        task = json.loads(task_text)
        self.assertEqual(summary["verification"]["counts"]["pending"], 1)
        self.assertEqual(summary["verification"]["items"][0]["id"], "task-1")
        self.assertEqual(task["verification_mode"], "deploy")
        self.assertEqual(task["verification_state"], "pending")
        self.assertEqual(
            task["verification_summary"]["human_validation_pending"],
            "Confirm billing dashboard loads",
        )

    async def test_task_verify_forwards_checkpoint_payload(self):
        state, weaver = self._make_state()
        state.board_add_task(
            "Deploy billing changes",
            "g",
            id="task-1",
        )
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {
                "type": "verification_updated",
                "task_id": "task-1",
                "message": "Verification updated: state=failed",
            }

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_verify",
            {
                "task": "task-1",
                "mode": "deploy",
                "attempted": True,
                "smoke": "failed",
                "notes": "Smoke failed on login redirect",
                "tests_run": "python3 -m unittest",
                "human_validation_pending": "Confirm fixed build",
            },
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            json.loads(text),
            {
                "type": "verification_updated",
                "task_id": "task-1",
                "message": "Verification updated: state=failed",
            },
        )
        self.assertEqual(
            calls,
            [
                {
                    "cmd": "board_verify_task",
                    "id": "task-1",
                    "actor_name": "Weaver",
                    "verification_mode": "deploy",
                    "verification_notes": "Smoke failed on login redirect",
                    "tests_run": "python3 -m unittest",
                    "human_validation_pending": "Confirm fixed build",
                    "deploy_attempted": True,
                    "smoke_status": "failed",
                },
            ],
        )

    async def test_task_verify_can_clear_attempted_and_smoke_fields(self):
        state, weaver = self._make_state()
        state.board_add_task(
            "Deploy billing changes",
            "g",
            id="task-1",
        )
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            return {
                "type": "verification_updated",
                "task_id": "task-1",
                "message": "Verification updated",
            }

        text, is_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_task_verify",
            {
                "task": "task-1",
                "attempted": False,
                "smoke": "clear",
            },
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(
            json.loads(text),
            {
                "type": "verification_updated",
                "task_id": "task-1",
                "message": "Verification updated",
            },
        )
        self.assertEqual(
            calls,
            [
                {
                    "cmd": "board_verify_task",
                    "id": "task-1",
                    "actor_name": "Weaver",
                    "deploy_attempted": False,
                    "manual_smoke_done": False,
                },
            ],
        )

    async def test_note_posts_without_pausing_event_delivery(self):
        state, weaver = self._make_state()
        calls = []

        async def fake_handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "weaver_note":
                return {"type": "ok"}
            self.fail(f"Unexpected command: {payload}")

        note_text, note_error = await self.mcp_weaver_mod._dispatch_weaver_tool(
            "weaver_note",
            {"message": "FYI: branch looks mergeable", "kind": "question"},
            fake_handle_command,
            state,
            cell_id=weaver.id,
        )

        self.assertFalse(note_error)
        self.assertIn("without pausing event delivery", note_text)
        self.assertEqual(
            calls,
            [
                {
                    "cmd": "weaver_note",
                    "group": "g",
                    "message": "FYI: branch looks mergeable",
                    "kind": "question",
                },
            ],
        )
