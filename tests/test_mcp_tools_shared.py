import importlib
import json
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MCPToolsSharedArchitectTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.shared_mod = importlib.import_module("loom.mcp_tools_shared")
        self.shared_mod = importlib.reload(self.shared_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        state.groups["loom"] = []
        return state

    def _add_agent(self, state, agent_id, name, *, kind, hired_by_architect_id="", owner_engineer_id=""):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="loom",
            cell_type="agent",
            kind=kind,
            hired_by_architect_id=hired_by_architect_id,
            owner_engineer_id=owner_engineer_id,
            created_by_weaver_id=owner_engineer_id,
            status="running",
        )
        state.agents[cell.id] = cell
        state.groups["loom"].append(cell.id)
        return cell

    def _add_task(self, state, task_id, title, **kwargs):
        task = self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group="loom",
            lane="Backlog",
            **kwargs,
        )
        state.board_tasks[task.id] = task
        return task

    def test_filter_agents_for_architect_sees_self_and_hired_engineers_only(self):
        state = self._make_state()
        architect = self._add_agent(state, "arch-1", "Architect", kind="architect")
        hired = self._add_agent(
            state,
            "eng-hired",
            "Hired Engineer",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        self._add_agent(state, "eng-user", "User Engineer", kind="engineer")
        self._add_agent(
            state,
            "worker-1",
            "Worker One",
            kind="worker",
            owner_engineer_id=hired.id,
        )

        visible = self.shared_mod._filter_agents_for_caller(
            state,
            "architect",
            architect.id,
        )

        self.assertEqual(set(visible), {architect.id, hired.id})

    def test_filter_tasks_for_architect_sees_all_group_tasks(self):
        state = self._make_state()
        architect = self._add_agent(state, "arch-1", "Architect", kind="architect")
        hired = self._add_agent(
            state,
            "eng-hired",
            "Hired Engineer",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        user_engineer = self._add_agent(state, "eng-user", "User Engineer", kind="engineer")

        created = self._add_task(
            state,
            "task-created",
            "Created by architect",
            assigned_engineer_id=user_engineer.id,
            created_by_architect_id=architect.id,
        )
        hired_task = self._add_task(
            state,
            "task-hired",
            "Assigned to hired engineer",
            assigned_engineer_id=hired.id,
        )
        self._add_task(
            state,
            "task-user",
            "User task",
            assigned_engineer_id=user_engineer.id,
        )
        state.groups["other"] = []
        other_group = self.state_mod.BoardTask(
            id="task-other-group",
            task="Other group task",
            group="other",
            lane="Backlog",
        )
        state.board_tasks[other_group.id] = other_group

        visible = self.shared_mod._filter_tasks_for_caller(
            state,
            "architect",
            architect.id,
        )

        self.assertEqual(set(visible), {created.id, hired_task.id, "task-user"})

    def test_authorize_caller_accepts_architect(self):
        state = self._make_state()
        architect = self._add_agent(state, "arch-1", "Architect", kind="architect")

        cell, group, effective_kind, error_text, is_error = self.shared_mod.authorize_caller(
            state,
            caller_kind="architect",
            caller_id=architect.id,
        )

        self.assertFalse(is_error)
        self.assertEqual(cell.id, architect.id)
        self.assertEqual(group, "loom")
        self.assertEqual(effective_kind, "architect")
        self.assertEqual(error_text, "")

    def test_architect_board_summary_json_trims_tasks_to_response_budget(self):
        summary = {
            "group": "loom",
            "streams": {
                "count": 1,
                "items": [{"summary": "stream-context " * 500}],
                "truncated": False,
            },
        }
        items = [
            {
                "id": f"task-{idx}",
                "slug": f"task-{idx}",
                "title": "Task " + ("x" * 110),
                "lane": "Backlog",
                "labels": [],
                "status": "",
                "assigned_engineer_id": "eng-1",
                "created_by": "architect:arch-1",
                "health_state": "healthy",
                "updated_at": "2026-04-21T00:00:00+00:00",
            }
            for idx in range(self.shared_mod._ARCHITECT_BOARD_SUMMARY_TASK_LIMIT)
        ]

        text = self.shared_mod._architect_board_summary_json(summary, items)

        self.assertLessEqual(
            len(text),
            self.shared_mod._ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT,
        )
        payload = json.loads(text)
        self.assertEqual(payload["tasks"]["count"], len(items))
        self.assertTrue(payload["tasks"]["truncated"])
        self.assertLess(
            len(payload["tasks"]["items"]),
            self.shared_mod._ARCHITECT_BOARD_SUMMARY_TASK_LIMIT,
        )
