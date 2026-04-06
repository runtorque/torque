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
            if payload["cmd"] == "worktree_check_conflicts":
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
                {"cmd": "worktree_check_conflicts", "id": cell.id},
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
            if payload["cmd"] == "worktree_check_conflicts":
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
