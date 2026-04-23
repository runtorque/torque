import importlib
import json
import stat
import tempfile
import unittest
from datetime import datetime, timezone
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
        self.handle_calls = []

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

    def _save_panel_event(self, event_id: int, kind: str, *,
                          cell_id: str = "", agent_name: str = "",
                          group: str = "loom", message: str = "",
                          task_id: str = "", timestamp: float | None = None):
        self.db.save_panel_event({
            "id": event_id,
            "timestamp": float(timestamp if timestamp is not None else event_id),
            "kind": kind,
            "cell_id": cell_id,
            "agent_name": agent_name,
            "group": group,
            "message": message,
            "task_id": task_id,
        })

    async def _handle_command(self, payload):
        self.handle_calls.append(dict(payload))
        if payload["cmd"] == "board_add_task":
            group = payload.get("group", "")
            gs = self.state.get_group_settings(group)
            task = self.state.board_add_task(
                payload.get("task", ""),
                group,
                lane=payload.get("lane", ""),
                description=payload.get("description", ""),
                labels=payload.get("labels", []),
                action_name=payload.get("action_name", "") or gs.board_default_action,
                assigned_engineer_id=payload.get("assigned_engineer_id", ""),
                created_by_engineer_id=payload.get("created_by_engineer_id", ""),
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
        if payload["cmd"] == "inject_mcp_message":
            agent = self.state.agents.get(payload.get("agent_id", ""))
            if not agent or not getattr(agent, "session_id", ""):
                return {"type": "ok", "delivered": False, "reason": "no_session"}
            return {"type": "ok", "delivered": True}
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
        alice.dismissed_at = 42
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
        self.assertNotIn("worker-a", engineers)
        self.assertEqual(engineers[alice.id]["current_task_id"], visible_task.id)
        self.assertEqual(engineers[alice.id]["current_task"], visible_task.task)
        self.assertEqual(engineers[alice.id]["dismissed_at"], 42)
        self.assertEqual(engineers[user_engineer.id]["current_task_id"], hidden_task.id)
        self.assertEqual(engineers[user_engineer.id]["current_task"], hidden_task.task)

    async def test_architect_events_recent_scopes_to_group_hires_and_created_tasks(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=other_architect.id
        )
        user_engineer = self._add_engineer("eng-user", "User Owned")
        alice_worker = self._add_worker("worker-alice", "Alice Worker", alice.id)
        bob_worker = self._add_worker("worker-bob", "Bob Worker", bob.id)
        user_worker = self._add_worker("worker-user", "User Worker", user_engineer.id)

        alice_task = self._add_task(
            "LOOM:201",
            "Alice task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        other_task = self._add_task(
            "LOOM:202",
            "Other architect task",
            assigned_engineer_id=bob.id,
            created_by_architect_id=other_architect.id,
        )
        architect_created_other_worker_task = self._add_task(
            "LOOM:203",
            "Architect-originated task",
            assigned_engineer_id=bob.id,
            created_by_architect_id=architect.id,
        )
        user_task = self._add_task(
            "LOOM:204",
            "User-visible engineer task",
            assigned_engineer_id=user_engineer.id,
        )

        self._save_panel_event(
            1, "task_completed", cell_id=alice_worker.id,
            agent_name=alice_worker.name, task_id=alice_task.id,
            message="visible via hired worker",
        )
        self._save_panel_event(
            2, "task_completed", cell_id=bob_worker.id,
            agent_name=bob_worker.name, task_id=other_task.id,
            message="hidden other architect",
        )
        self._save_panel_event(
            3, "task_completed", cell_id=bob_worker.id,
            agent_name=bob_worker.name,
            task_id=architect_created_other_worker_task.id,
            message="visible via creator",
        )
        self._save_panel_event(
            4, "task_completed", cell_id=user_worker.id,
            agent_name=user_worker.name, task_id=user_task.id,
            message="hidden user-visible engineer",
        )
        self._save_panel_event(
            5, "agent_error", cell_id=architect.id,
            agent_name=architect.name, message="visible own event",
        )
        self._save_panel_event(
            6, "task_completed", cell_id=alice_worker.id,
            agent_name=alice_worker.name, group="other",
            task_id=alice_task.id, message="hidden other group",
        )

        text, is_error = await self._call(
            "architect_events_recent",
            {"limit": 10},
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertFalse(payload["truncated"])
        self.assertEqual(
            [event["id"] for event in payload["events"]],
            ["5", "3", "1"],
        )
        events = {event["id"]: event for event in payload["events"]}
        self.assertEqual(events["1"]["assigned_engineer_id"], alice.id)
        self.assertEqual(events["1"]["owner_engineer_id"], alice.id)
        self.assertEqual(events["1"]["agent_kind"], "worker")
        self.assertEqual(
            events["3"]["created_by_architect_id"],
            architect.id,
        )
        self.assertEqual(events["3"]["owner_engineer_id"], bob.id)
        self.assertEqual(events["5"]["agent_kind"], "architect")
        self.assertNotIn("2", events)
        self.assertNotIn("4", events)
        self.assertNotIn("6", events)

    async def test_architect_events_recent_filters_kind_since_and_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        courier = self._add_engineer(
            "eng-courier", "Courier", hired_by_architect_id=architect.id
        )
        alice_worker = self._add_worker("worker-alice", "Alice Worker", alice.id)
        courier_worker = self._add_worker("worker-courier", "Courier Worker", courier.id)
        alice_task = self._add_task(
            "LOOM:211",
            "Alice task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        courier_task = self._add_task(
            "LOOM:212",
            "Courier task",
            assigned_engineer_id=courier.id,
            created_by_architect_id=architect.id,
        )

        self._save_panel_event(
            1, "agent_error", cell_id=alice_worker.id,
            agent_name=alice_worker.name, task_id=alice_task.id,
            message="old error", timestamp=10,
        )
        self._save_panel_event(
            2, "task_completed", cell_id=alice_worker.id,
            agent_name=alice_worker.name, task_id=alice_task.id,
            message="wrong kind", timestamp=20,
        )
        self._save_panel_event(
            3, "agent_error", cell_id=courier_worker.id,
            agent_name=courier_worker.name, task_id=courier_task.id,
            message="wrong engineer", timestamp=30,
        )
        self._save_panel_event(
            4, "agent_error", cell_id=alice_worker.id,
            agent_name=alice_worker.name, task_id=alice_task.id,
            message="matching event", timestamp=40,
        )

        text, is_error = await self._call(
            "architect_events_recent",
            {
                "kind_filter": "agent_error",
                "since": 25,
                "engineer_id": alice.id,
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual([event["id"] for event in payload["events"]], ["4"])
        self.assertEqual(payload["events"][0]["message"], "matching event")

    async def test_architect_events_recent_shows_loom108_attribution_and_recipients(self):
        architect = self._add_architect("arch-1", "Architect")
        assigned = self._add_engineer(
            "eng-assigned", "Assigned Engineer",
            hired_by_architect_id=architect.id,
        )
        owner = self._add_engineer("eng-owner", "Worker Owner")
        worker = self._add_worker("worker-1", "Worker Bee", owner.id)
        task = self._add_task(
            "LOOM:108",
            "Architect-created attribution bug",
            assigned_engineer_id=assigned.id,
            created_by_architect_id=architect.id,
            agent_id=worker.id,
        )
        self.state.update_agent_digest_settings(assigned.id, enabled_events=["task_done"])
        self.state.update_agent_digest_settings(architect.id)
        self.state.update_agent_digest_settings(owner.id)

        self._save_panel_event(
            1,
            "task_done",
            cell_id=worker.id,
            agent_name=worker.name,
            task_id=task.id,
            message="Implemented fix with a very long body " + ("x " * 200),
        )

        text, is_error = await self._call(
            "architect_events_recent",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertLess(len(text), 10_000)
        payload = json.loads(text)
        self.assertEqual(len(payload["events"]), 1)
        event = payload["events"][0]
        self.assertEqual(event["id"], "1")
        self.assertEqual(event["kind"], "task_done")
        self.assertEqual(event["cell_id"], worker.id)
        self.assertEqual(event["agent_name"], worker.name)
        self.assertEqual(event["agent_kind"], "worker")
        self.assertEqual(event["task_id"], task.id)
        self.assertEqual(event["assigned_engineer_id"], assigned.id)
        self.assertEqual(event["created_by_architect_id"], architect.id)
        self.assertEqual(event["owner_engineer_id"], owner.id)
        self.assertEqual(event["digest_recipients"], [assigned.id, architect.id])
        self.assertLessEqual(
            len(event["message"]),
            self.shared_mod._ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT,
        )

    async def test_architect_events_recent_default_response_stays_bounded(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Engineer", hired_by_architect_id=architect.id
        )
        worker = self._add_worker("worker-1", "Worker", engineer.id)
        task = self._add_task(
            "LOOM:220",
            "Bounded response task",
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
        )
        for idx in range(25):
            self._save_panel_event(
                idx + 1,
                "task_completed",
                cell_id=worker.id,
                agent_name=worker.name,
                task_id=task.id,
                message="long summary " + ("x" * 1000),
            )

        text, is_error = await self._call(
            "architect_events_recent",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertLess(len(text), 10_000)
        payload = json.loads(text)
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["events"]), 20)
        self.assertEqual(payload["events"][0]["id"], "25")
        self.assertEqual(payload["events"][-1]["id"], "6")

    async def test_architect_deploy_state_tool_returns_boot_and_pending_counts(self):
        architect = self._add_architect("arch-1", "Architect")
        self.state.boot_timestamp = 100.0
        self.state.boot_repo_root = "/repo"
        self.state.boot_head_commit = "boot-sha"
        self.state.boot_mainline_branch = "main"

        def fake_git(_repo_root, *args):
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "main"
            if args == ("rev-parse", "HEAD"):
                return "current-sha"
            if args == ("rev-list", "--count", "boot-sha..main"):
                return "2"
            if args == ("log", "--format=%B", "--reverse", "boot-sha..main"):
                return "Merge LOOM:118\nMerge LOOM:119"
            raise AssertionError(f"unexpected git call: {args}")

        with mock.patch("loom.deploy_state._run_git", side_effect=fake_git), \
                mock.patch("loom.deploy_state.time.time", return_value=160.0):
            text, is_error = await self._call(
                "architect_deploy_state",
                {},
                architect.id,
            )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["boot_timestamp"], 100.0)
        self.assertEqual(payload["boot_head_commit"], "boot-sha")
        self.assertEqual(payload["current_head_commit"], "current-sha")
        self.assertEqual(payload["daemon_uptime_seconds"], 60)
        self.assertEqual(payload["pending_deploy"]["count"], 2)
        self.assertEqual(
            payload["pending_deploy"]["loom_task_ids"],
            ["LOOM:118", "LOOM:119"],
        )

    async def test_architect_workspace_overview_shape_and_scoping(self):
        architect = self._add_architect("arch-1", "Loomer")
        other_architect = self._add_architect("arch-2", "Other Architect")
        panelsmith = self._add_engineer(
            "eng-panelsmith", "Panelsmith", hired_by_architect_id=architect.id
        )
        courier = self._add_engineer(
            "eng-courier", "Courier", hired_by_architect_id=architect.id
        )
        other_hired = self._add_engineer(
            "eng-other", "Other", hired_by_architect_id=other_architect.id
        )
        user_visible = self._add_engineer("eng-user", "User Visible")
        panelsmith.activity_detail = "Reviewing the workspace overview"
        panelsmith.last_event_at = 111.0
        self.state.update_agent_digest_settings(panelsmith.id, paused=True)

        panel_worker = self._add_worker("worker-panel", "Panel Worker", panelsmith.id)
        panel_worker.status = "running"
        panel_worker.activity_detail = "Editing mcp_tools_shared.py"
        panel_worker.worktree_repo_root = "/repo"
        panel_worker.git_root = "/repo"
        panel_worker.worktree_branch = "loom/panelsmith"
        panel_worker.worktree_path = "/repo/.loom/worktrees/panel"
        panel_worker.last_event_at = 222.0
        courier_worker = self._add_worker("worker-courier", "Courier Worker", courier.id)
        courier_worker.status = "idle"
        courier_worker.worktree_repo_root = "/repo"
        courier_worker.git_root = "/repo"
        courier_worker.worktree_branch = "loom/courier"
        courier_worker.worktree_path = "/repo/.loom/worktrees/courier"
        hidden_worker = self._add_worker("worker-hidden", "Hidden Worker", other_hired.id)
        hidden_worker.worktree_branch = "loom/hidden"

        panel_task = self._add_task(
            "LOOM:1",
            "Implement compact workspace overview with a title that should be clipped "
            "before it becomes too verbose for the architect context window",
            lane="In Progress",
            assigned_engineer_id=panelsmith.id,
            created_by_architect_id=architect.id,
        )
        panel_task.agent_id = panel_worker.id
        panel_task.created_at = "2026-04-22T10:00:00+00:00"
        panel_task.updated_at = "2026-04-22T10:30:00+00:00"
        panel_task.lane_entered_at = "2026-04-22T10:15:00+00:00"
        panel_worker.current_task_id = panel_task.id
        courier_task = self._add_task(
            "LOOM:2",
            "Review workspace overview response shape",
            lane="To Do",
            assigned_engineer_id=courier.id,
            created_by_architect_id=architect.id,
        )
        courier_task.created_at = "2026-04-22T11:00:00+00:00"
        self._add_task(
            "LOOM:3",
            "Archived own task must stay out",
            lane="Archived",
            assigned_engineer_id=courier.id,
            created_by_architect_id=architect.id,
        )
        self._add_task(
            "LOOM:4",
            "Other architect task must stay out of my_tasks",
            lane="To Do",
            assigned_engineer_id=other_hired.id,
            created_by_architect_id=other_architect.id,
        )

        self.state.save_pending_hire({
            "id": "hire-own",
            "architect_id": architect.id,
            "requested_name": "Extra Engineer",
            "status": "pending",
        })
        self.state.save_pending_hire({
            "id": "hire-other",
            "architect_id": other_architect.id,
            "requested_name": "Hidden Engineer",
            "status": "pending",
        })
        message_text, message_error = await self._call_engineer(
            "engineer_message_architect",
            {"architect_id": architect.id, "message": "Panels are ready for review."},
            panelsmith.id,
        )
        self.assertFalse(message_error, message_text)

        text, is_error = await self._call(
            "architect_workspace_overview",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertLess(len(text), 4096)
        overview = json.loads(text)
        self.assertEqual(
            set(overview),
            {
                "architect",
                "architect_self_state",
                "engineers",
                "my_tasks",
                "pending_hires",
                "unread_messages",
            },
        )
        self.assertEqual(
            overview["architect"],
            {"id": architect.id, "name": "Loomer", "group": "loom"},
        )
        self.assertEqual(
            overview["architect_self_state"],
            {
                "last_journal_entry_at": None,
                "last_decision_at": None,
                "journal_entry_count": 0,
                "decision_count": 0,
            },
        )

        engineer_ids = {item["id"] for item in overview["engineers"]}
        self.assertEqual(engineer_ids, {panelsmith.id, courier.id, user_visible.id})
        self.assertNotIn(other_hired.id, engineer_ids)
        self.assertNotIn(hidden_worker.id, json.dumps(overview))

        panelsmith_item = next(
            item for item in overview["engineers"] if item["id"] == panelsmith.id
        )
        self.assertTrue(panelsmith_item["digest_paused"])
        self.assertEqual(panelsmith_item["last_progress_at"], 111.0)
        self.assertEqual(
            panelsmith_item["queue"]["in_progress"],
            [{
                "task_id": panel_task.id,
                "title": (
                    "Implement compact workspace overview with a title that should "
                    "be clipped before…"
                ),
                "since": "2026-04-22T10:15:00+00:00",
            }],
        )
        self.assertEqual(panelsmith_item["workers"][0]["id"], panel_worker.id)
        self.assertEqual(
            panelsmith_item["workers"][0]["current_task_id"],
            panel_task.id,
        )
        self.assertEqual(
            panelsmith_item["recent_streams"][0]["branch"],
            "loom/panelsmith",
        )

        courier_item = next(
            item for item in overview["engineers"] if item["id"] == courier.id
        )
        self.assertEqual(
            courier_item["queue"]["to_do"],
            [{"task_id": courier_task.id, "title": courier_task.task}],
        )
        self.assertEqual(courier_item["workers"][0]["id"], courier_worker.id)

        self.assertEqual(
            {task["task_id"] for task in overview["my_tasks"]},
            {panel_task.id, courier_task.id},
        )
        self.assertEqual(
            overview["my_tasks"][0]["assigned_engineer_name"],
            "Courier",
        )
        self.assertEqual(
            overview["pending_hires"],
            [{
                "hire_id": "hire-own",
                "requested_name": "Extra Engineer",
                "status": "pending",
            }],
        )
        self.assertEqual(overview["unread_messages"][0]["peer_id"], panelsmith.id)
        self.assertEqual(
            overview["unread_messages"][0]["peer_name"],
            "Panelsmith",
        )
        self.assertEqual(
            overview["unread_messages"][0]["snippet"],
            "Panels are ready for review.",
        )

    async def test_task_reads_resolve_literal_alias_to_hash_task(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer("eng-1", "Engineer")
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Archived header task",
            group="loom",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Keep track of which agent moved a task",
            description="Live canonical description",
            group="loom",
            lane="Backlog",
            action_name="feature/implement",
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
        )
        self.db.save_board_task(archived)
        self.db.save_task_id_alias("LOOM:51", live.id)
        self.state.board_tasks[archived.id] = archived
        self.state.board_tasks[live.id] = live
        self.state.task_id_aliases["LOOM:51"] = live.id

        text, is_error = await self._call(
            "architect_task_show",
            {"task": "LOOM:51"},
            architect.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["id"], live.id)
        self.assertEqual(data["title"], live.task)
        self.assertEqual(data["description"], "Live canonical description")
        self.assertEqual(data["action"], "feature/implement")
        self.assertTrue(self.db.board_task_exists(live.id))

        text, is_error = await self._call_engineer(
            "engineer_task_show",
            {"task": "LOOM:51"},
            engineer.id,
        )
        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["id"], live.id)

        summary_text, is_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )
        self.assertFalse(is_error, summary_text)
        summary = json.loads(summary_text)
        self.assertEqual(summary["tasks_total"], 1)
        self.assertEqual(summary["tasks"]["items"][0]["id"], live.id)

        list_text, is_error = await self._call_engineer(
            "engineer_board_list",
            {},
            engineer.id,
        )
        self.assertFalse(is_error, list_text)
        lanes = json.loads(list_text)["lanes"]
        self.assertEqual(lanes["Backlog"][0]["id"], live.id)

    async def test_architect_task_show_refreshes_and_promotes_health(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        base_ts = 1_700_000_000
        alice.status = "idle"
        alice.last_event_at = base_ts
        task = self._add_task(
            "task-visible",
            "Architect-visible silent work",
            lane="In Progress",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        task.agent_id = alice.id
        alice.current_task_id = task.id
        last_activity = datetime.fromtimestamp(
            base_ts,
            tz=timezone.utc,
        ).isoformat()
        task.health_state = "idle-risk"
        task.health_details = {
            "aggregate": False,
            "source_task_id": task.id,
            "last_activity_at": last_activity,
            "reasons": ["progress_silence_warning"],
            "agent_last_event_at": last_activity,
            "silence_secs": 12,
        }

        with mock.patch("loom.mcp_tools_shared.time.time", return_value=base_ts + 900):
            text, is_error = await self._call(
                "architect_task_show",
                {"task": task.id},
                architect.id,
            )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(data["health_state"], "idle-risk")
        self.assertEqual(data["health_details"]["silence_secs"], 900)
        self.assertIn("Silent 15 min", data["health_summary"])
        self.assertIn("status=idle", data["health_summary"])
        self.assertLessEqual(len(data["health_summary"]), 120)
        self.assertEqual(data["created_by"], f"architect:{architect.id}")

    async def test_architect_board_summary_reads_full_group_with_created_by_attribution(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=other_architect.id
        )
        user_engineer = self._add_engineer("eng-user", "User Owned")
        own_task = self._add_task(
            "task-own",
            "Architect-created task\nsecond line must not leak",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            description="full description must stay out of board summary",
            action_vars={"secret": "action-vars-must-not-leak"},
            messages=[{"action": "progress", "message": "message must not leak"}],
            health_details={"nested": "health-details-must-not-leak"},
            verification_summary={"nested": "verification-summary-must-not-leak"},
        )
        peer_task = self._add_task(
            "task-peer",
            "Peer-created task",
            assigned_engineer_id=bob.id,
            created_by_architect_id=other_architect.id,
        )
        engineer_task = self._add_task(
            "task-engineer",
            "Engineer-created task",
            assigned_engineer_id=alice.id,
            created_by_engineer_id=alice.id,
        )
        system_task = self._add_task(
            "task-system",
            "Derived system task",
            parent_task_id=own_task.id,
            pipeline_depth=1,
            pipeline_root_id=own_task.id,
        )
        user_task = self._add_task(
            "task-user",
            "Legacy user task",
            assigned_engineer_id=user_engineer.id,
        )
        self.state.groups["other"] = []
        self.state._db_save_groups()
        self._add_task("task-other-group", "Other group task", group="other")

        reloaded = self.state_mod.MatrixState(db=self.db)
        reloaded.load()
        self.state = reloaded

        text, is_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        summary = json.loads(text)
        self.assertEqual(summary["tasks_total"], 5)
        self.assertEqual(summary["lanes"]["Backlog"], 5)
        self.assertFalse(summary["tasks"]["truncated"])
        task_items = {
            item["id"]: item for item in summary["tasks"]["items"]
        }
        self.assertEqual(
            set(task_items),
            {
                own_task.id,
                peer_task.id,
                engineer_task.id,
                system_task.id,
                user_task.id,
            },
        )
        self.assertEqual(
            {
                task_id: item["created_by"]
                for task_id, item in task_items.items()
            },
            {
                own_task.id: f"architect:{architect.id}",
                peer_task.id: f"architect:{other_architect.id}",
                engineer_task.id: f"engineer:{alice.id}",
                system_task.id: "system",
                user_task.id: "user",
            },
        )
        expected_task_item_keys = {
            "id",
            "slug",
            "title",
            "lane",
            "labels",
            "status",
            "assigned_engineer_id",
            "created_by",
            "health_state",
            "updated_at",
        }
        for item in task_items.values():
            self.assertEqual(set(item), expected_task_item_keys)
        self.assertEqual(task_items[own_task.id]["title"], "Architect-created task")
        summary_text = json.dumps(summary)
        self.assertNotIn("second line must not leak", summary_text)
        self.assertNotIn("full description must stay out", summary_text)
        self.assertNotIn("action-vars-must-not-leak", summary_text)
        self.assertNotIn("message must not leak", summary_text)
        self.assertNotIn("health-details-must-not-leak", summary_text)
        self.assertNotIn("verification-summary-must-not-leak", summary_text)

        for task, expected in (
            (own_task, f"architect:{architect.id}"),
            (peer_task, f"architect:{other_architect.id}"),
            (engineer_task, f"engineer:{alice.id}"),
            (system_task, "system"),
            (user_task, "user"),
        ):
            with self.subTest(task=task.id):
                show_text, show_error = await self._call(
                    "architect_task_show",
                    {"task": task.id},
                    architect.id,
                )
                self.assertFalse(show_error, show_text)
                shown = json.loads(show_text)
                self.assertEqual(shown["created_by"], expected)
                if task.id == own_task.id:
                    self.assertIn("second line must not leak", shown["title"])
                    self.assertIn(
                        "full description must stay out",
                        shown["description"],
                    )
                    self.assertEqual(
                        shown["messages"][0]["message"],
                        "message must not leak",
                    )

    async def test_architect_board_summary_bounds_large_task_excerpt(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        expected_task_item_keys = {
            "id",
            "slug",
            "title",
            "lane",
            "labels",
            "status",
            "assigned_engineer_id",
            "created_by",
            "health_state",
            "updated_at",
        }
        for idx in range(205):
            self._add_task(
                f"task-{idx:03d}",
                f"Task {idx:03d} first line " + ("x" * 180)
                + "\nSECOND-LINE-SENTINEL",
                assigned_engineer_id=alice.id,
                created_by_architect_id=architect.id,
                description="FULL-DESCRIPTION-SENTINEL " * 80,
                action_vars={"payload": "ACTION-VARS-SENTINEL" * 20},
                messages=[{
                    "action": "progress",
                    "message": "MESSAGE-SENTINEL" * 20,
                }],
                health_details={"nested": "HEALTH-DETAILS-SENTINEL" * 20},
                verification_summary={
                    "nested": "VERIFICATION-SUMMARY-SENTINEL" * 20,
                },
            )

        text, is_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertLess(len(text), 10_000)
        summary = json.loads(text)
        self.assertEqual(summary["tasks_total"], 205)
        self.assertEqual(summary["lanes"]["Backlog"], 205)
        self.assertEqual(summary["tasks"]["count"], 205)
        self.assertTrue(summary["tasks"]["truncated"])
        task_items = summary["tasks"]["items"]
        self.assertEqual(
            len(task_items),
            self.shared_mod._ARCHITECT_BOARD_SUMMARY_TASK_LIMIT,
        )
        for item in task_items:
            self.assertEqual(set(item), expected_task_item_keys)
            self.assertNotIn("\n", item["title"])
            self.assertLessEqual(
                len(item["title"]),
                self.shared_mod._ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT,
            )
            self.assertFalse(any(isinstance(value, dict) for value in item.values()))
        self.assertNotIn("SECOND-LINE-SENTINEL", text)
        self.assertNotIn("FULL-DESCRIPTION-SENTINEL", text)
        self.assertNotIn("ACTION-VARS-SENTINEL", text)
        self.assertNotIn("MESSAGE-SENTINEL", text)
        self.assertNotIn("HEALTH-DETAILS-SENTINEL", text)
        self.assertNotIn("VERIFICATION-SUMMARY-SENTINEL", text)

    async def test_architect_task_create_stamps_architect_fields_and_rejects_out_of_scope_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        visible_engineer = self._add_engineer("eng-visible", "Visible Engineer")
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

        visible_denied_text, visible_denied_error = await self._call(
            "architect_task_create",
            {
                "title": "Visible engineer assign",
                "group": "loom",
                "assigned_engineer_id": visible_engineer.id,
            },
            architect.id,
        )

        self.assertTrue(visible_denied_error)
        self.assertEqual(visible_denied_text, "engineer not found in scope")
        self.assertEqual(len(self.state.board_tasks), 1)

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

    async def test_architect_ask_creates_visible_human_attention_task(self):
        architect = self._add_architect("arch-1", "Architect")
        self.state.get_group_settings("loom").board_default_action = "feature/implement"

        text, is_error = await self._call(
            "architect_ask",
            {
                "question": "Should we cut reporting from this milestone?",
                "description": "Option A: cut reporting. Option B: delay launch.",
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "ok")
        task = self.state.board_tasks[payload["task_id"]]
        self.assertEqual(task.task, "Should we cut reporting from this milestone?")
        self.assertEqual(
            task.description,
            "Option A: cut reporting. Option B: delay launch.",
        )
        self.assertEqual(task.group, architect.group)
        self.assertEqual(task.lane, "Backlog")
        self.assertEqual(task.status, "Awaiting Input")
        self.assertIn("loom:human", task.labels)
        self.assertIn("architect-ask", task.labels)
        self.assertEqual(task.action_name, "")
        self.assertEqual(task.created_by_architect_id, architect.id)
        self.assertEqual(task.reply_agent_id, architect.id)
        self.assertEqual(task.assigned_engineer_id, "")
        self.assertEqual(task.agent_id, "")

        summary_text, summary_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )
        self.assertFalse(summary_error, summary_text)
        summary = json.loads(summary_text)
        self.assertEqual(summary["asks"]["count"], 1)
        self.assertEqual(summary["asks"]["items"][0]["id"], task.id)
        self.assertEqual(
            summary["asks"]["items"][0]["created_by"],
            f"architect:{architect.id}",
        )

        architect.mcp_messages.insert(0, {
            "id": "msg-user-reply",
            "action": "architect_ask_reply",
            "message": "Cut reporting and ship the smaller milestone.",
            "timestamp": 123.0,
            "peer_id": "user",
            "peer_kind": "human",
            "peer_name": "User",
            "direction": "received",
            "task_id": task.id,
        })
        overview_text, overview_error = await self._call(
            "architect_workspace_overview",
            {},
            architect.id,
        )
        self.assertFalse(overview_error, overview_text)
        overview = json.loads(overview_text)
        self.assertEqual(overview["unread_messages"][0]["peer_name"], "User")
        self.assertEqual(
            overview["unread_messages"][0]["snippet"],
            "Cut reporting and ship the smaller milestone.",
        )

    async def test_architect_task_reassign_only_allows_tasks_created_by_caller(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        hired_peer = self._add_engineer(
            "eng-peer", "Peer", hired_by_architect_id=architect.id
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

        visible_denied_text, visible_denied_error = await self._call(
            "architect_task_reassign",
            {"task": own_task.id, "new_engineer_id": user_engineer.id},
            architect.id,
        )
        self.assertTrue(visible_denied_error)
        self.assertEqual(visible_denied_text, "engineer not found in scope")
        self.assertEqual(self.state.board_tasks[own_task.id].assigned_engineer_id, alice.id)

        ok_text, ok_error = await self._call(
            "architect_task_reassign",
            {"task": own_task.id, "new_engineer_id": hired_peer.id},
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        self.assertEqual(
            self.state.board_tasks[own_task.id].assigned_engineer_id,
            hired_peer.id,
        )

    async def test_architect_task_create_and_reassign_reject_non_engineer_targets(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        engineer = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        worker = self._add_worker("worker-1", "Worker", engineer.id)
        task = self._add_task(
            "task-own",
            "Architect-owned task",
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
        )

        worker_create_text, worker_create_error = await self._call(
            "architect_task_create",
            {
                "title": "Wrong target",
                "group": "loom",
                "assigned_engineer_id": worker.id,
            },
            architect.id,
        )
        self.assertTrue(worker_create_error)
        self.assertEqual(worker_create_text, "engineer not found in scope")
        self.assertEqual(len(self.state.board_tasks), 1)

        architect_create_text, architect_create_error = await self._call(
            "architect_task_create",
            {
                "title": "Wrong target",
                "group": "loom",
                "assigned_engineer_id": other_architect.id,
            },
            architect.id,
        )
        self.assertTrue(architect_create_error)
        self.assertEqual(architect_create_text, "engineer not found in scope")
        self.assertEqual(len(self.state.board_tasks), 1)

        worker_reassign_text, worker_reassign_error = await self._call(
            "architect_task_reassign",
            {"task": task.id, "new_engineer_id": worker.id},
            architect.id,
        )
        self.assertTrue(worker_reassign_error)
        self.assertEqual(worker_reassign_text, "engineer not found in scope")
        self.assertEqual(self.state.board_tasks[task.id].assigned_engineer_id, engineer.id)

        architect_reassign_text, architect_reassign_error = await self._call(
            "architect_task_reassign",
            {"task": task.id, "new_engineer_id": other_architect.id},
            architect.id,
        )
        self.assertTrue(architect_reassign_error)
        self.assertEqual(architect_reassign_text, "engineer not found in scope")
        self.assertEqual(self.state.board_tasks[task.id].assigned_engineer_id, engineer.id)

    async def test_architect_task_create_and_reassign_reject_dismissed_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        engineer.dismissed_at = 123
        peer = self._add_engineer(
            "eng-peer", "Peer", hired_by_architect_id=architect.id
        )
        task = self._add_task(
            "task-own",
            "Architect-owned task",
            assigned_engineer_id=peer.id,
            created_by_architect_id=architect.id,
        )

        create_text, create_error = await self._call(
            "architect_task_create",
            {
                "title": "Dismissed target",
                "group": "loom",
                "assigned_engineer_id": engineer.id,
            },
            architect.id,
        )
        self.assertTrue(create_error)
        create_payload = json.loads(create_text)
        self.assertEqual(create_payload["reason"], "engineer_dismissed")
        self.assertEqual(len(self.state.board_tasks), 1)

        reassign_text, reassign_error = await self._call(
            "architect_task_reassign",
            {"task": task.id, "new_engineer_id": engineer.id},
            architect.id,
        )
        self.assertTrue(reassign_error)
        reassign_payload = json.loads(reassign_text)
        self.assertEqual(reassign_payload["reason"], "engineer_dismissed")
        self.assertEqual(self.state.board_tasks[task.id].assigned_engineer_id, peer.id)

    async def test_architect_and_engineer_messaging_respects_hiring_scope(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        other_hired = self._add_engineer(
            "eng-other", "Other Hired", hired_by_architect_id=other_architect.id
        )
        user_visible = self._add_engineer("eng-user", "User Visible")

        ok_text, ok_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": hired.id, "message": "Need a scope decision."},
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        delivered = json.loads(ok_text)
        self.assertTrue(delivered["message_id"])
        self.assertEqual(hired.mcp_messages[0]["action"], "architect_message")
        self.assertTrue(
            hired.mcp_messages[0]["message"].startswith(
                "You are Hired (engineer, id=eng-hired).\n\n"
                "Need a scope decision."
            ),
            hired.mcp_messages[0]["message"],
        )
        self.assertEqual(architect.mcp_messages[0]["action"], "architect_message")
        self.assertEqual(
            architect.mcp_messages[0]["message"],
            "Need a scope decision.",
        )
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

        visible_text, visible_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": user_visible.id, "message": "Also hidden"},
            architect.id,
        )
        self.assertTrue(visible_error)
        self.assertEqual(visible_text, "engineer not found in scope")

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

    async def test_engineer_message_architect_ack_required_defaults_false_and_propagates(self):
        architect = self._add_architect("arch-1", "Architect")
        architect.session_id = "session-architect"
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )

        status_text, status_error = await self._call_engineer(
            "engineer_message_architect",
            {"architect_id": architect.id, "message": "Status: going quiet."},
            hired.id,
        )

        self.assertFalse(status_error, status_text)
        self.assertFalse(architect.mcp_messages[0]["ack_required"])
        self.assertFalse(hired.mcp_messages[0]["ack_required"])
        injects = [
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
        ]
        self.assertEqual(len(injects), 1)
        self.assertFalse(injects[-1]["ack_required"])

        question_text, question_error = await self._call_engineer(
            "engineer_message_architect",
            {
                "architect_id": architect.id,
                "message": "Should I cut scope?",
                "ack_required": True,
            },
            hired.id,
        )

        self.assertFalse(question_error, question_text)
        self.assertTrue(architect.mcp_messages[0]["ack_required"])
        self.assertTrue(hired.mcp_messages[0]["ack_required"])
        injects = [
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
        ]
        self.assertEqual(len(injects), 2)
        self.assertTrue(injects[-1]["ack_required"])

    async def test_architect_message_to_dismissed_engineer_buffers_for_rehire(self):
        architect = self._add_architect("arch-1", "Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        hired.dismissed_at = 123
        hired.session_id = None

        ok_text, ok_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": hired.id, "message": "Read this when you return."},
            architect.id,
        )

        self.assertFalse(ok_error, ok_text)
        delivered = json.loads(ok_text)
        self.assertTrue(delivered["message_id"])
        self.assertEqual(
            hired.mcp_messages[0]["message"],
            "You are Hired (engineer, id=eng-hired).\n\n"
            "Read this when you return.",
        )
        self.assertFalse(hired.mcp_messages[0]["delivered"])
        self.assertTrue(hired.mcp_messages[0]["buffered"])
        self.assertEqual(hired.mcp_messages[0]["delivery_reason"], "no_session")

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

    async def test_architect_workspace_overview_self_state_reads_latest_history_counts(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        with mock.patch.object(self.state_mod, "DATA_DIR", Path(self.tmp.name)):
            with mock.patch("time.time", side_effect=[100.0, 200.0, 300.0, 400.0]):
                for entry_type, entry in (
                    ("checkpoint", "Old checkpoint"),
                    ("decision", "Use the compact rollup"),
                    ("checkpoint", "New checkpoint"),
                    ("observation", "Not part of self-state"),
                ):
                    write_text, write_error = await self._call(
                        "architect_journal",
                        {"type": entry_type, "entry": entry},
                        architect.id,
                    )
                    self.assertFalse(write_error, write_text)

            self.state.save_decision({
                "id": "decision-old",
                "architect_id": architect.id,
                "title": "Old decision",
                "rationale": "First recorded direction",
                "created_at": 150,
                "updated_at": 150,
            })
            self.state.save_decision({
                "id": "decision-new",
                "architect_id": architect.id,
                "title": "New decision",
                "rationale": "Latest recorded direction",
                "created_at": 250,
                "updated_at": 250,
            })
            self.state.save_decision({
                "id": "decision-other",
                "architect_id": other_architect.id,
                "title": "Other decision",
                "rationale": "Must not affect this architect",
                "created_at": 500,
                "updated_at": 500,
            })

            text, is_error = await self._call(
                "architect_workspace_overview",
                {},
                architect.id,
            )
            self.assertFalse(is_error, text)
            self.assertEqual(
                json.loads(text)["architect_self_state"],
                {
                    "last_journal_entry_at": 400.0,
                    "last_decision_at": 250.0,
                    "journal_entry_count": 4,
                    "decision_count": 2,
                },
            )

            empty_text, empty_error = await self._call(
                "architect_workspace_overview",
                {},
                other_architect.id,
            )
            self.assertFalse(empty_error, empty_text)
            self.assertEqual(
                json.loads(empty_text)["architect_self_state"],
                {
                    "last_journal_entry_at": None,
                    "last_decision_at": 500.0,
                    "journal_entry_count": 0,
                    "decision_count": 1,
                },
            )

            empty_architect = self._add_architect("arch-empty", "Empty Architect")
            no_history_text, no_history_error = await self._call(
                "architect_workspace_overview",
                {},
                empty_architect.id,
            )
            self.assertFalse(no_history_error, no_history_text)
            self.assertEqual(
                json.loads(no_history_text)["architect_self_state"],
                {
                    "last_journal_entry_at": None,
                    "last_decision_at": None,
                    "journal_entry_count": 0,
                    "decision_count": 0,
                },
            )


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
