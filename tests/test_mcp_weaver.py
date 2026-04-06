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
        self.mcp_weaver = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver = importlib.reload(self.mcp_weaver)

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
        text, is_error = await self.mcp_weaver._dispatch_weaver_tool(
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
