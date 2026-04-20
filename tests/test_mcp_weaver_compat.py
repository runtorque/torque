import importlib
import json
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MCPWeaverCompatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("loom.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)
        self.mcp_weaver_mod = importlib.import_module("loom.mcp_weaver")
        self.mcp_weaver_mod = importlib.reload(self.mcp_weaver_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        state.groups["loom"] = []

        weaver = self.state_mod.AgentCell(
            id="eng-weaver",
            name="Weaver",
            slug="weaver",
            group="loom",
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker One",
            slug="worker-one",
            group="loom",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=weaver.id,
            created_by_weaver_id=weaver.id,
            status="idle",
            activity="waiting",
            activity_detail="Idle",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Fix login",
            slug="fix-login",
            group="loom",
            lane="Backlog",
            agent_id=worker.id,
            assigned_engineer_id=weaver.id,
            labels=["bug"],
            health_state="healthy",
            health_since="",
        )
        state.agents[weaver.id] = weaver
        state.agents[worker.id] = worker
        state.groups["loom"] = [weaver.id, worker.id]
        state.group_settings["loom"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id,
        )
        state.board_tasks[task.id] = task
        return state, weaver

    async def _call(self, module, tool_name, state, weaver_id):
        async def fake_handle_command(_payload):
            self.fail("snapshot read tools should not call handle_command")

        if tool_name.startswith("engineer_"):
            text, is_error = await module._dispatch_engineer_tool(
                tool_name,
                {},
                fake_handle_command,
                state,
                caller_id=weaver_id,
            )
        else:
            text, is_error = await module._dispatch_weaver_tool(
                tool_name,
                {},
                fake_handle_command,
                state,
            )
        self.assertFalse(is_error, text)
        return json.loads(text)

    async def test_single_engineer_alias_matches_expected_snapshot(self):
        state, weaver = self._make_state()
        expected = {
            "weaver_agents_list": {
                "agents": [
                    {
                        "activity": "",
                        "activity_detail": "",
                        "current_task": "",
                        "current_task_id": "",
                        "group": "loom",
                        "id": "eng-weaver",
                        "name": "Weaver",
                        "slug": "weaver",
                        "status": "running",
                    },
                    {
                        "activity": "waiting",
                        "activity_detail": "Idle",
                        "current_task": "",
                        "current_task_id": "",
                        "group": "loom",
                        "id": "worker-1",
                        "name": "Worker One",
                        "slug": "worker-one",
                        "status": "idle",
                    },
                ],
            },
            "weaver_board_list": {
                "lanes": {
                    "Backlog": [
                        {
                            "action": "",
                            "agent": "worker-one",
                            "external_id": "",
                            "external_url": "",
                            "group": "loom",
                            "health_since": "",
                            "health_state": "healthy",
                            "id": "task-1",
                            "labels": ["bug"],
                            "parent_task_id": "",
                            "provider": "",
                            "slug": "fix-login",
                            "status": "",
                            "title": "Fix login",
                            "verification_mode": "",
                            "verification_state": "",
                        }
                    ]
                }
            },
            "weaver_board_summary": {
                "agents": {
                    "active": [
                        {
                            "current_task": "",
                            "current_task_id": "",
                            "id": "worker-1",
                            "name": "Worker One",
                            "needs_attention": False,
                            "slug": "worker-one",
                            "status": "idle",
                            "type": "",
                        }
                    ],
                    "active_count": 1,
                    "by_status": {
                        "error": 0,
                        "idle": 1,
                        "running": 0,
                        "stopped": 0,
                    },
                    "needs_attention": 0,
                    "total": 1,
                    "truncated": False,
                },
                "archived_total": 0,
                "asks": {"count": 0, "items": [], "truncated": False},
                "branch_boundaries": {"count": 0, "items": [], "truncated": False},
                "group": "loom",
                "hints": {"count": 0, "items": [], "truncated": False},
                "labels": {"deferred": 0, "ready": 0},
                "lanes": {"Backlog": 1, "Done": 0, "In Progress": 0, "To Do": 0},
                "streams": {
                    "by_state": {
                        "awaiting_human_validation": 0,
                        "fixing_blockers": 0,
                        "implementing": 0,
                        "merged": 0,
                        "ready_to_merge": 0,
                        "reviewing": 0,
                    },
                    "count": 0,
                    "items": [],
                    "truncated": False,
                },
                "task_health": {
                    "counts": {
                        "blocked": 0,
                        "healthy": 1,
                        "idle-risk": 0,
                        "stale-in-progress": 0,
                        "stalled": 0,
                        "thrashing": 0,
                    },
                    "truncated": False,
                    "unhealthy": [],
                },
                "tasks_total": 1,
                "verification": {
                    "counts": {
                        "attempted": 0,
                        "failed": 0,
                        "passed": 0,
                        "pending": 0,
                    },
                    "items": [],
                    "truncated": False,
                },
            },
        }

        self.assertEqual(
            await self._call(self.mcp_weaver_mod, "weaver_agents_list", state, weaver.id),
            expected["weaver_agents_list"],
        )
        self.assertEqual(
            await self._call(self.mcp_weaver_mod, "weaver_board_list", state, weaver.id),
            expected["weaver_board_list"],
        )
        self.assertEqual(
            await self._call(self.mcp_weaver_mod, "weaver_board_summary", state, weaver.id),
            expected["weaver_board_summary"],
        )

        self.assertEqual(
            await self._call(self.mcp_engineer_mod, "engineer_agents_list", state, weaver.id),
            expected["weaver_agents_list"],
        )
        self.assertEqual(
            await self._call(self.mcp_engineer_mod, "engineer_board_list", state, weaver.id),
            expected["weaver_board_list"],
        )
        self.assertEqual(
            await self._call(self.mcp_engineer_mod, "engineer_board_summary", state, weaver.id),
            expected["weaver_board_summary"],
        )
