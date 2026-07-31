import importlib
import json
import stat
import tempfile
import types
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
        self.db_mod = importlib.import_module("torque.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.server_dispatch_mod = importlib.import_module(
            "torque.server_dispatch"
        )
        self.server_dispatch_mod = importlib.reload(self.server_dispatch_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.shared_mod = importlib.import_module("torque.mcp_tools_shared")
        self.shared_mod = importlib.reload(self.shared_mod)
        self.streams_mod = importlib.import_module("torque.worktree_streams")
        self.streams_mod = importlib.reload(self.streams_mod)
        self.mcp_architect_mod = importlib.import_module("torque.mcp_architect")
        self.mcp_architect_mod = importlib.reload(self.mcp_architect_mod)
        self.mcp_engineer_mod = importlib.import_module("torque.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project_specs = Path(self.tmp.name) / ".torque" / "specializations"
        self.project_specs.mkdir(parents=True)
        for name in (
                "ui-ux",
                "orchestration-core",
                "runtime-pty",
                "desktop-shell",
                "worktree-release",
                "prompts-config",
                "quality-observability",
        ):
            self.project_specs.joinpath(f"{name}.yaml").write_text(
                f"name: {name}\npreamble: {name} focus.\n",
                encoding="utf-8",
            )
        self.db_path = Path(self.tmp.name) / "torque.db"
        self.db = self.db_mod.TorqueDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["torque"] = []
        self.state._db_save_groups()
        self.handle_calls = []

    def _add_architect(self, agent_id: str, name: str, *, group: str = "torque"):
        self.state.groups.setdefault(group, [])
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group=group,
            cell_type="agent",
            kind="architect",
            status="running",
            persistent=True,
        )
        self.state.agents[cell.id] = cell
        self.state.groups[group].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_engineer(self, agent_id: str, name: str, *,
                      hired_by_architect_id: str = "",
                      group: str = "torque"):
        self.state.groups.setdefault(group, [])
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group=group,
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
            hired_by_architect_id=hired_by_architect_id,
        )
        self.state.agents[cell.id] = cell
        self.state.groups[group].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_worker(self, agent_id: str, name: str, owner_engineer_id: str):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="torque",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=owner_engineer_id,
            created_by_engineer_id=owner_engineer_id,
            status="idle",
        )
        self.state.agents[cell.id] = cell
        self.state.groups["torque"].append(cell.id)
        self.state._db_save_agent(cell)
        self.state._db_save_groups()
        return cell

    def _add_task(self, task_id: str, title: str, **kwargs):
        task = self.state.board_add_task(
            title,
            kwargs.pop("group", "torque"),
            lane=kwargs.pop("lane", "Backlog"),
            id=task_id,
            **kwargs,
        )
        self.assertIsNotNone(task)
        return task

    def _save_panel_event(self, event_id: int, kind: str, *,
                          cell_id: str = "", agent_name: str = "",
                          group: str = "torque", message: str = "",
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
        if payload["cmd"] == "board_mark_task_covered":
            try:
                return self.state.board_mark_task_covered(
                    payload["id"],
                    covering_task_id=payload.get("covering_task_id", ""),
                    pr_url=payload.get("pr_url", ""),
                    sha=payload.get("sha", ""),
                    tests_run=payload.get("tests_run", ""),
                    evidence=payload.get("evidence", ""),
                    notes=payload.get("notes", ""),
                    actor_name=payload.get("actor_name", ""),
                    actor_id=payload.get("actor_id", ""),
                    actor_kind=payload.get("actor_kind", ""),
                    authorization=payload.get("authorization", {}),
                    move_to_done=payload.get("move_to_done", False),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}
        if payload["cmd"] == "architect_proposal_root_backlog_hygiene":
            architect_id = str(payload.get("architect_id", "") or "").strip()
            architect = self.state.agents.get(architect_id)
            if not architect or getattr(architect, "kind", "") != "architect":
                return {"type": "error", "message": "architect not found"}
            return self.server_mod._finalize_already_covered_proposal_roots(
                self.state,
                apply=bool(payload.get("apply", False)),
                task_ids=payload.get("task_ids", []) or [],
                limit=int(payload.get("limit", 0) or 0),
                architect_id=architect_id,
                group=getattr(architect, "group", "") or "",
            )
        if payload["cmd"] == "list_actions":
            return {
                "type": "actions", "group": payload.get("group", ""),
                "actions": [{"name": "feature/implement"}, {"name": "oneshot/fix"}],
            }
        if payload["cmd"] == "board_move_task":
            task = self.state.board_tasks.get(payload.get("id", ""))
            if not task:
                return {"type": "error", "message": "Task not found"}
            lane = str(payload.get("lane", "") or "").strip()
            if not lane:
                return {"type": "error", "message": "lane is required"}
            if lane not in self.state.board_lanes:
                return {"type": "error", "message": f"Unknown lane: {lane}"}
            clear_status = payload.get("clear_status", False)
            if not isinstance(clear_status, bool):
                clear_status = False
            previous_lane = task.lane
            self.state.board_move_task(
                task.id,
                lane,
                payload.get("position"),
                clear_status=clear_status,
            )
            moved = self.state.board_tasks.get(task.id)
            return {
                "type": "task_moved",
                "task_id": task.id,
                "previous_lane": previous_lane,
                "new_lane": moved.lane if moved else lane,
                "status": moved.status if moved else "",
            }
        if payload["cmd"] == "inject_mcp_message":
            agent = self.state.agents.get(payload.get("agent_id", ""))
            if not agent or not getattr(agent, "session_id", ""):
                return {"type": "ok", "delivered": False, "reason": "no_session"}
            return {"type": "ok", "delivered": True}
        if payload["cmd"] == "architect_engineer_set_specializations":
            async def fake_resolve_base_dir(_group):
                return self.tmp.name

            return await self.server_mod._handle_set_engineer_specializations_command(
                payload,
                self.state,
                resolve_base_dir=fake_resolve_base_dir,
                specialization_mgr=self.server_mod.SpecializationManager(),
                architect_id=str(payload.get("architect_id", "") or ""),
            )
        if payload["cmd"] == "engineer_reply":
            group = payload.get("group", "")
            if not str(payload.get("answer", "") or "").strip():
                return {"type": "error", "message": "Answer is required"}
            self.state.update_engineer_settings(
                group,
                pending_question="",
                paused=False,
            )
            return {"type": "ok"}
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

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_server_handle_command(self, *, board_sync_manager=None):
        """Return the real server handle_command closure for live-path tests."""
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )

        class DummyBridge:
            async def list_profiles(self):
                return []

            async def get_launch_context(self):
                return types.SimpleNamespace(current_path="", current_profile="")

            async def update_session(self, *_args, **_kwargs):
                return None

        class DummyWorktreeManager:
            async def diff_summary(self, _cell, *, non_test_only=False):
                return {"files": 0, "insertions": 0, "deletions": 0}

            async def checkpoint(self, _cell, *, message=""):
                return ""

        async def noop_async(*_args, **_kwargs):
            return None

        closure_values = {name: None for name in handle_code.co_freevars}
        closure_values.update({
            "_broadcast_toast": noop_async,
            "_checkpoint_message": lambda _cell: "checkpoint",
            "_checkpoint_on_report": noop_async,
            "_cleanup_after_merge": noop_async,
            "_close_agent_session_only": noop_async,
            "_panel_event": lambda *args, **kwargs: None,
            "_record_task_boundary": noop_async,
            "_resolve_base_dir": noop_async,
            "_runtime_payload": lambda: {},
            "action_mgr": None,
            "board_sync_manager": board_sync_manager,
            "bridge": DummyBridge(),
            "db": self.db,
            "handle_command": None,
            "panel_log": types.SimpleNamespace(
                replace_last=lambda *args, **kwargs: {}
            ),
            "state": self.state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
            "worktree_mgr": DummyWorktreeManager(),
        })
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            self.server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    async def _call_with_server_handle(
            self, tool_name: str, args: dict, caller_id: str,
            *, board_sync_manager=None):
        return await self.mcp_architect_mod._dispatch_architect_tool(
            tool_name,
            args,
            self._extract_server_handle_command(
                board_sync_manager=board_sync_manager,
            ),
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

    async def test_architect_engineer_list_exposes_live_worker_occupancy(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        engineer.status = "idle"
        worker = self._add_worker("worker-a", "Alice Worker", engineer.id)
        parent = self._add_task(
            "task-parent",
            "Engineer-owned implementation",
            lane="In Progress",
            agent_id=engineer.id,
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
            dispatch_state="live",
        )
        worker_task = self._add_task(
            "task-worker",
            "Live worker implementation",
            lane="In Progress",
            agent_id=worker.id,
            assigned_engineer_id=engineer.id,
            dispatch_state="live",
        )
        worker.status = "running"
        worker.current_task_id = worker_task.id
        self.assertEqual(engineer.current_task_id, "")

        text, is_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        row = next(
            item for item in json.loads(text)["engineers"]
            if item["id"] == engineer.id
        )
        self.assertNotIn("status", row)
        self.assertEqual(row["session_status"], "idle")
        self.assertTrue(row["is_busy"])
        self.assertEqual(row["current_task_id"], parent.id)
        self.assertEqual(row["current_task"], parent.task)
        self.assertEqual(engineer.current_task_id, "")

    async def test_architect_engineer_list_exposes_staged_occupancy_without_changing_promotion(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        engineer.status = "idle"
        staged = self._add_task(
            "task-staged",
            "Staged implementation",
            lane="To Do",
            agent_id=engineer.id,
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
            dispatch_state="queued",
        )
        self.state.auto_dispatch_queue_add(
            "torque",
            staged.id,
            target_agent_id=engineer.id,
            max_concurrent=1,
        )

        def promotion_signature():
            active = self.server_dispatch_mod._agent_active_current_task(
                self.state, engineer.id
            )
            blocking = self.server_dispatch_mod._agent_blocking_active_task(
                self.state,
                engineer.id,
                ignore_task_id=staged.id,
            )
            return {
                "current_task_id": engineer.current_task_id,
                "active_task_id": active.id if active else "",
                "blocking_task_id": blocking.id if blocking else "",
                "queue": [
                    {
                        "task_id": entry.task_id,
                        "target_agent_id": entry.target_agent_id,
                        "max_concurrent": entry.max_concurrent,
                    }
                    for entry in self.state.auto_dispatch_queues["torque"]
                ],
            }

        before = promotion_signature()
        text, is_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )
        after = promotion_signature()

        self.assertFalse(is_error, text)
        row = next(
            item for item in json.loads(text)["engineers"]
            if item["id"] == engineer.id
        )
        self.assertEqual(row["session_status"], "idle")
        self.assertTrue(row["is_busy"])
        self.assertEqual(row["current_task_id"], staged.id)
        self.assertEqual(row["current_task"], staged.task)
        self.assertEqual(before, after)
        self.assertEqual(engineer.current_task_id, "")

        dispatch_calls = []

        async def handle_dispatch(payload):
            dispatch_calls.append(dict(payload))
            self.state.board_update_task(
                staged.id,
                lane="In Progress",
                dispatch_state="live",
            )
            return {
                "type": "ok",
                "task_id": staged.id,
                "agent_id": engineer.id,
            }

        promoted = await self.server_mod._pump_auto_dispatch_queue(
            self.state,
            handle_dispatch,
            lambda *args, **kwargs: None,
            group="torque",
        )

        self.assertEqual(dispatch_calls, [{
            "cmd": "dispatch_task",
            "id": staged.id,
            "agent_id": engineer.id,
        }])
        self.assertEqual(promoted, [{
            "group": "torque",
            "task_id": staged.id,
            "agent_id": engineer.id,
        }])

    async def test_architect_engineer_list_occupancy_does_not_block_second_dispatch(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        engineer.status = "idle"
        tasks = [
            self._add_task(
                f"task-{index}",
                f"Staged implementation {index}",
                lane="To Do",
                agent_id=engineer.id,
                assigned_engineer_id=engineer.id,
                created_by_architect_id=architect.id,
                action_name="feature/implement",
                dispatch_state="queued",
            )
            for index in (1, 2)
        ]

        first_text, first_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": engineer.id,
                "task": tasks[0].id,
                "message": "Start the first task.",
            },
            architect.id,
        )
        self.assertFalse(first_error, first_text)

        roster_text, roster_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )
        self.assertFalse(roster_error, roster_text)
        row = next(
            item for item in json.loads(roster_text)["engineers"]
            if item["id"] == engineer.id
        )
        self.assertTrue(row["is_busy"])
        self.assertEqual(engineer.current_task_id, "")

        second_text, second_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": engineer.id,
                "task": tasks[1].id,
                "message": "Start the second task too.",
            },
            architect.id,
        )

        self.assertFalse(second_error, second_text)
        self.assertEqual(
            [task.dispatch_state for task in tasks],
            ["live", "live"],
        )
        self.assertEqual(engineer.current_task_id, "")

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
            "TORQUE:201",
            "Alice task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        other_task = self._add_task(
            "TORQUE:202",
            "Other architect task",
            assigned_engineer_id=bob.id,
            created_by_architect_id=other_architect.id,
        )
        architect_created_other_worker_task = self._add_task(
            "TORQUE:203",
            "Architect-originated task",
            assigned_engineer_id=bob.id,
            created_by_architect_id=architect.id,
        )
        user_task = self._add_task(
            "TORQUE:204",
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
            "TORQUE:211",
            "Alice task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        courier_task = self._add_task(
            "TORQUE:212",
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

    async def test_architect_events_recent_merges_peer_messages_without_leaking(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-2", "Peer")
        unrelated = self._add_architect("arch-3", "Unrelated")
        self.db.save_agent_peer_message({
            "id": "msg-visible-peer",
            "thread_id": "msg-visible-peer",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Can you review the handoff?",
            "created_at": 50.0,
            "ack_required": True,
            "context_task_ids": ["TORQUE:501"],
            "context_summary": "Handoff needs owner confirmation.",
            "delivery_state": "delivered",
        })
        self.db.save_agent_peer_message({
            "id": "msg-hidden-peer",
            "thread_id": "msg-hidden-peer",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": unrelated.id,
            "recipient_kind": "architect",
            "message": "Hidden from the caller.",
            "created_at": 60.0,
            "ack_required": True,
            "delivery_state": "buffered",
        })
        self._save_panel_event(
            1,
            "agent_error",
            cell_id=architect.id,
            agent_name=architect.name,
            message="Visible panel event",
            timestamp=40.0,
        )

        text, is_error = await self._call(
            "architect_events_recent",
            {"limit": 10},
            architect.id,
        )

        self.assertFalse(is_error, text)
        events = json.loads(text)["events"]
        self.assertEqual([event["id"] for event in events], ["msg-visible-peer", "1"])
        peer_event = events[0]
        self.assertEqual(peer_event["kind"], "architect_peer_message")
        self.assertEqual(peer_event["direction"], "received")
        self.assertEqual(peer_event["peer_architect_id"], peer.id)
        self.assertEqual(peer_event["peer_name"], peer.name)
        self.assertTrue(peer_event["ack_required"])
        self.assertTrue(peer_event["requires_reply"])
        self.assertEqual(peer_event["context"]["task_ids"], ["TORQUE:501"])
        self.assertEqual(
            peer_event["context"]["summary"],
            "Handoff needs owner confirmation.",
        )

        filtered_text, filtered_error = await self._call(
            "architect_events_recent",
            {"kind_filter": "architect_peer_message"},
            architect.id,
        )
        self.assertFalse(filtered_error, filtered_text)
        filtered = json.loads(filtered_text)["events"]
        self.assertEqual([event["id"] for event in filtered], ["msg-visible-peer"])

        inbox_text, inbox_error = await self._call(
            "architect_peer_inbox",
            {"detail": True},
            architect.id,
        )
        self.assertFalse(inbox_error, inbox_text)
        inbox_ids = {
            msg["id"]
            for thread in json.loads(inbox_text)["threads"]
            for msg in thread["messages"]
        }
        self.assertEqual(inbox_ids, {"msg-visible-peer"})

    async def test_architect_events_recent_shows_torque108_attribution_and_recipients(self):
        architect = self._add_architect("arch-1", "Architect")
        assigned = self._add_engineer(
            "eng-assigned", "Assigned Engineer",
            hired_by_architect_id=architect.id,
        )
        owner = self._add_engineer("eng-owner", "Worker Owner")
        worker = self._add_worker("worker-1", "Worker Bee", owner.id)
        task = self._add_task(
            "TORQUE:108",
            "Architect-created attribution bug",
            assigned_engineer_id=assigned.id,
            created_by_architect_id=architect.id,
            agent_id=worker.id,
        )
        self.state.update_agent_digest_settings(assigned.id, enabled_events=["task_done"])
        self.state.update_agent_digest_settings(architect.id, enabled_events=["task_done"])
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

    async def test_architect_events_recent_stamps_engineer_ask_digest_recipient(self):
        architect = self._add_architect("arch-1", "Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        user_engineer = self._add_engineer("eng-user", "User Engineer")

        self._save_panel_event(
            1,
            "engineer_awaiting_human_input",
            cell_id=hired.id,
            agent_name=hired.name,
            message="Awaiting human input: Need approval?",
        )
        self._save_panel_event(
            2,
            "engineer_awaiting_human_input",
            cell_id=user_engineer.id,
            agent_name=user_engineer.name,
            message="Awaiting human input: User-owned engineer question",
        )

        text, is_error = await self._call(
            "architect_events_recent",
            {"limit": 10, "kind_filter": "engineer_awaiting_human_input"},
            architect.id,
        )

        self.assertFalse(is_error, text)
        events = json.loads(text)["events"]
        self.assertEqual([event["id"] for event in events], ["1"])
        self.assertEqual(events[0]["digest_recipients"], [architect.id])
        self.assertEqual(events[0]["cell_id"], hired.id)

    async def test_architect_events_recent_default_response_stays_bounded(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-1", "Engineer", hired_by_architect_id=architect.id
        )
        worker = self._add_worker("worker-1", "Worker", engineer.id)
        task = self._add_task(
            "TORQUE:220",
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
                return "Merge TORQUE:118\nMerge TORQUE:119"
            raise AssertionError(f"unexpected git call: {args}")

        with mock.patch("torque.deploy_state._run_git", side_effect=fake_git), \
                mock.patch("torque.deploy_state.time.time", return_value=160.0):
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
            payload["pending_deploy"]["torque_task_ids"],
            ["TORQUE:118", "TORQUE:119"],
        )

    # Alias-resolution coverage: architect/engineer task-read MCP tools must
    # follow `state.task_id_aliases` from a literal TORQUE:N legacy id to the
    # canonical hash id, and must not surface the archived literal task.
    async def test_task_reads_resolve_literal_alias_to_hash_task(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer("eng-1", "Engineer")
        archived = self.state_mod.BoardTask(
            id="TORQUE:51",
            task="Archived header task",
            group="torque",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Keep track of which agent moved a task",
            description="Live canonical description",
            group="torque",
            lane="Backlog",
            action_name="feature/implement",
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
        )
        self.db.save_board_task(archived)
        self.db.save_task_id_alias("TORQUE:51", live.id)
        self.state.board_tasks[archived.id] = archived
        self.state.board_tasks[live.id] = live
        self.state.task_id_aliases["TORQUE:51"] = live.id

        text, is_error = await self._call(
            "architect_task_show",
            {"task": "TORQUE:51"},
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
            {"task": "TORQUE:51"},
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

        with mock.patch("torque.mcp_tools_shared.time.time", return_value=base_ts + 900):
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

    async def test_architect_task_chain_returns_rooted_tree_for_visible_pipeline(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        worker_one = self._add_worker("worker-1", "Worker One", engineer.id)
        worker_two = self._add_worker("worker-2", "Worker Two", engineer.id)
        worker_three = self._add_worker("worker-3", "Worker Three", engineer.id)

        root = self._add_task(
            "TORQUE:75",
            "Pipeline root",
            lane="Done",
            status="shipped",
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
        )
        child_one = self._add_task(
            "TORQUE:75:1",
            "Implementation",
            lane="Done",
            status="merged",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            action_name="feature/implement",
            agent_id=worker_one.id,
        )
        child_two = self._add_task(
            "TORQUE:75:2",
            "Follow-up review",
            lane="Done",
            status="approved",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            action_name="feature/review",
            agent_id=worker_two.id,
        )
        grandchild_one = self._add_task(
            "TORQUE:75:3",
            "Fix blockers",
            lane="In Progress",
            status="editing",
            parent_task_id=child_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            action_name="feature/fix-review",
            agent_id=worker_one.id,
        )
        grandchild_two = self._add_task(
            "TORQUE:75:4",
            "Verification",
            lane="Done",
            status="passed",
            parent_task_id=child_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            action_name="feature/verify",
            agent_id=worker_two.id,
        )
        leaf = self._add_task(
            "TORQUE:75:5",
            "Ship follow-up",
            lane="In Progress",
            status="awaiting-review",
            parent_task_id=grandchild_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=3,
            action_name="feature/review",
            agent_id=worker_three.id,
        )

        text, is_error = await self._call(
            "architect_task_chain",
            {"task": leaf.id},
            architect.id,
        )

        self.assertFalse(is_error, text)
        data = json.loads(text)
        self.assertEqual(
            data["root"],
            {
                "task_id": root.id,
                "title": root.task,
                "lane": "Done",
                "status": "shipped",
                "assigned_engineer_id": engineer.id,
            },
        )
        self.assertEqual(data["focus_task_id"], leaf.id)
        self.assertEqual(
            data["stats"],
            {
                "total_nodes": 6,
                "done": 4,
                "in_progress": 2,
                "max_depth": 3,
            },
        )
        self.assertEqual(data["tree"]["task_id"], root.id)
        self.assertEqual(data["tree"]["children"][0]["task_id"], child_one.id)
        self.assertEqual(data["tree"]["children"][1]["task_id"], child_two.id)
        self.assertEqual(
            [item["task_id"] for item in data["tree"]["children"][0]["children"]],
            [grandchild_one.id, grandchild_two.id],
        )
        self.assertEqual(
            data["tree"]["children"][0]["children"][0]["children"][0]["task_id"],
            leaf.id,
        )
        self.assertEqual(
            data["tree"]["children"][0]["children"][0]["children"][0]["agent_id"],
            worker_three.id,
        )
        self.assertEqual(
            data["tree"]["children"][0]["children"][0]["children"][0]["status"],
            "awaiting-review",
        )

    async def test_architect_task_chain_rejects_out_of_scope_root(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        self._add_engineer("eng-alice", "Alice", hired_by_architect_id=architect.id)
        hidden_engineer = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=other_architect.id
        )
        hidden_root = self._add_task(
            "task-hidden-root",
            "Hidden pipeline root",
            assigned_engineer_id=hidden_engineer.id,
            created_by_architect_id=other_architect.id,
        )
        self._add_task(
            "task-hidden-child",
            "Hidden pipeline child",
            parent_task_id=hidden_root.id,
            pipeline_root_id=hidden_root.id,
            pipeline_depth=1,
        )

        text, is_error = await self._call(
            "architect_task_chain",
            {"task": hidden_root.id},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task chain root not visible to this architect")

    async def test_architect_task_chain_uses_same_group_scoped_resolver_as_task_show(self):
        architect = self._add_architect("arch-1", "Architect")
        self.state.groups["other"] = []
        self.state._db_save_groups()
        other_task = self._add_task(
            "task-other-group",
            "Cross-group task must stay hidden",
            group="other",
            created_by_architect_id=architect.id,
        )

        text, is_error = await self._call(
            "architect_task_chain",
            {"task": other_task.id},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task not found")

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
        self.state.save_peer_message({
            "id": "peer-summary-1",
            "thread_id": "peer-summary-1",
            "group_name": "torque",
            "sender_id": other_architect.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Please confirm this architecture split.",
            "created_at": 42.0,
            "ack_required": True,
        })
        self.state.save_peer_message({
            "id": "peer-summary-2",
            "thread_id": "peer-summary-2",
            "group_name": "torque",
            "sender_id": architect.id,
            "sender_kind": "architect",
            "recipient_id": other_architect.id,
            "recipient_kind": "architect",
            "message": "FYI on the UI surface.",
            "created_at": 43.0,
            "ack_required": False,
        })
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
        self.assertEqual(summary["peer_messages"]["recent_count"], 2)
        self.assertEqual(summary["peer_messages"]["requires_reply_count"], 1)
        self.assertEqual(summary["peer_messages"]["oldest_unanswered_at"], 42.0)
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
            "dispatch_state",
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

    async def test_architect_summaries_exclude_engineer_message_followups(self):
        architect = self._add_architect("arch-1", "Architect")
        real_task = self._add_task(
            "task-real",
            "Implement visible work",
            created_by_architect_id=architect.id,
        )
        followup = self._add_task(
            "task-reply",
            "Engineer: Need status",
            labels=["torque:engineer-message"],
            status="Awaiting Reply",
            reply_agent_id="worker-1",
            created_by_engineer_id="eng-1",
        )

        text, is_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        summary = json.loads(text)
        self.assertEqual(summary["tasks_total"], 1)
        self.assertEqual(summary["lanes"]["Backlog"], 1)
        self.assertEqual(summary["pending_message_followups"], 1)
        self.assertEqual(
            [item["id"] for item in summary["tasks"]["items"]],
            [real_task.id],
        )

        list_text, list_error = await self._call(
            "architect_task_list",
            {"lane_filter": "Backlog"},
            architect.id,
        )
        self.assertFalse(list_error, list_text)
        task_list = json.loads(list_text)
        self.assertEqual(task_list["total"], 1)
        self.assertEqual(
            [item["id"] for item in task_list["tasks"]],
            [real_task.id],
        )

        include_text, include_error = await self._call(
            "architect_task_list",
            {
                "lane_filter": "Backlog",
                "include_engineer_messages": True,
            },
            architect.id,
        )
        self.assertFalse(include_error, include_text)
        included = json.loads(include_text)
        self.assertEqual(included["total"], 2)
        self.assertEqual(
            {item["id"] for item in included["tasks"]},
            {real_task.id, followup.id},
        )

    async def test_architect_board_reads_include_board_sync_state(self):
        architect = self._add_architect("arch-1", "Architect")
        task = self._add_task(
            "task-synced",
            "Synced task",
            created_by_architect_id=architect.id,
            board_sync={
                "provider": "github",
                "sync_state": "queued",
                "last_error": "",
                "github": {"project_item_id": "not-exposed-in-compact-state"},
            },
        )

        summary_text, summary_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )
        self.assertFalse(summary_error, summary_text)
        summary_item = json.loads(summary_text)["tasks"]["items"][0]
        self.assertEqual(
            summary_item["board_sync"],
            {
                "provider": "github",
                "sync_state": "queued",
                "last_error": "",
            },
        )

        show_text, show_error = await self._call(
            "architect_task_show",
            {"task": task.id},
            architect.id,
        )
        self.assertFalse(show_error, show_text)
        self.assertEqual(
            json.loads(show_text)["board_sync"],
            {
                "provider": "github",
                "sync_state": "queued",
                "last_error": "",
            },
        )

        list_text, list_error = await self._call(
            "architect_task_list",
            {},
            architect.id,
        )
        self.assertFalse(list_error, list_text)
        listed = json.loads(list_text)["tasks"][0]
        self.assertEqual(listed["board_sync"]["sync_state"], "queued")
        self.assertNotIn("github", listed["board_sync"])

    async def test_architect_board_summary_reports_peer_message_counts_for_caller(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-2", "Peer")
        unrelated = self._add_architect("arch-3", "Unrelated")
        self.db.save_agent_peer_message({
            "id": "msg-answered-question",
            "thread_id": "thread-answered",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Please confirm the rollout plan.",
            "created_at": 10.0,
            "ack_required": True,
            "delivery_state": "delivered",
        })
        self.db.save_agent_peer_message({
            "id": "msg-answer",
            "thread_id": "thread-answered",
            "reply_to_id": "msg-answered-question",
            "group_name": "torque",
            "sender_id": architect.id,
            "sender_kind": "architect",
            "recipient_id": peer.id,
            "recipient_kind": "architect",
            "message": "Confirmed.",
            "created_at": 20.0,
            "delivery_state": "delivered",
        })
        self.db.save_agent_peer_message({
            "id": "msg-pending-question",
            "thread_id": "thread-pending",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Need your API boundary decision.",
            "created_at": 30.0,
            "ack_required": True,
            "delivery_state": "delivered",
        })
        self.db.save_agent_peer_message({
            "id": "msg-buffered-fyi",
            "thread_id": "thread-buffered",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "FYI for your next wake.",
            "created_at": 40.0,
            "delivery_state": "buffered",
        })
        self.db.save_agent_peer_message({
            "id": "msg-unrelated",
            "thread_id": "thread-unrelated",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": unrelated.id,
            "recipient_kind": "architect",
            "message": "Hidden from arch-1.",
            "created_at": 50.0,
            "ack_required": True,
            "delivery_state": "buffered",
        })

        text, is_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        peer_messages = json.loads(text)["peer_messages"]
        self.assertEqual(peer_messages["recent_count"], 4)
        self.assertEqual(peer_messages["sent_count"], 1)
        self.assertEqual(peer_messages["received_count"], 3)
        self.assertEqual(peer_messages["unread_count"], 1)
        self.assertEqual(peer_messages["ack_required_pending_count"], 1)
        self.assertEqual(peer_messages["requires_reply_count"], 1)
        self.assertEqual(peer_messages["oldest_unanswered_at"], 30.0)
        self.assertEqual(peer_messages["latest_message_at"], 40.0)

    async def test_architect_attention_digest_surfaces_actionable_gates(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        peer = self._add_architect("arch-peer", "Peer Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired Engineer", hired_by_architect_id=architect.id
        )
        other_hired = self._add_engineer(
            "eng-other", "Other Engineer", hired_by_architect_id=other_architect.id
        )
        worker = self._add_worker("worker-hired", "Worker Hired", hired.id)
        worker.worktree_repo_root = "/repo"
        worker.git_root = "/repo"
        worker.worktree_branch = "torque/hired-ready"
        worker.status = "idle"
        other_worker = self._add_worker(
            "worker-other", "Worker Other", other_hired.id
        )
        other_worker.worktree_repo_root = "/repo"
        other_worker.git_root = "/repo"
        other_worker.worktree_branch = "torque/other-ready"
        other_worker.status = "idle"
        blocker_worker = self._add_worker(
            "worker-blocker", "Worker Blocker", hired.id
        )
        blocker_worker.worktree_repo_root = "/repo"
        blocker_worker.git_root = "/repo"
        blocker_worker.worktree_branch = "torque/hired-blocker"
        blocker_worker.status = "running"

        ask = self._add_task(
            "task-ask",
            "Need product call",
            labels=["torque:human"],
            created_by_architect_id=architect.id,
        )
        parked = self._add_task(
            "task-parked",
            "Deferred follow-up",
            labels=["deferred"],
            created_by_architect_id=architect.id,
        )
        parked_human = self._add_task(
            "task-parked-human",
            "Deferred human ask",
            labels=["torque:human", "deferred"],
            created_by_architect_id=architect.id,
        )
        parked_unhealthy = self._add_task(
            "task-parked-unhealthy",
            "Deferred unhealthy work",
            lane="In Progress",
            labels=["deferred"],
            assigned_engineer_id=hired.id,
            created_by_architect_id=architect.id,
        )
        unhealthy = self._add_task(
            "task-unhealthy",
            "Unhealthy active work",
            lane="In Progress",
            assigned_engineer_id=hired.id,
            created_by_architect_id=architect.id,
        )
        ready_product = self._add_task(
            "task-ready",
            "Ready product",
            lane="Done",
            agent_id=worker.id,
            assigned_engineer_id=hired.id,
            created_by_architect_id=architect.id,
            verification_state="passed",
            completion_evidence={
                "completion": {
                    "acceptance_deviation": {
                        "statement": "Delivered the API contract only.",
                        "reason": "UI integration needs product review.",
                        "agent_id": worker.id,
                        "agent_name": worker.name,
                        "recorded_at": "2026-04-07T11:00:00+00:00",
                    },
                    "acceptance_deviation_attempt": {
                        "statement": "",
                        "reason": "The final deployment check is deferred.",
                        "missing_fields": ["statement"],
                        "agent_id": worker.id,
                        "agent_name": worker.name,
                        "recorded_at": "2026-04-07T11:01:00+00:00",
                    },
                },
            },
        )
        self._add_task(
            "task-ready-review",
            "Review ready product",
            lane="Done",
            action_name="feature/review",
            parent_task_id=ready_product.id,
            pipeline_root_id=ready_product.id,
            pipeline_depth=1,
            agent_id=worker.id,
            assigned_engineer_id=hired.id,
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/hired-ready",
                "base_branch": "main",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "recorded_at": "2026-04-07T11:30:00+00:00",
                },
            },
        )
        other_product = self._add_task(
            "task-other-ready",
            "Other architect ready product",
            lane="Done",
            agent_id=other_worker.id,
            assigned_engineer_id=other_hired.id,
            created_by_architect_id=other_architect.id,
        )
        self._add_task(
            "task-other-ready-review",
            "Review other ready product",
            lane="Done",
            action_name="feature/review",
            parent_task_id=other_product.id,
            pipeline_root_id=other_product.id,
            pipeline_depth=1,
            agent_id=other_worker.id,
            assigned_engineer_id=other_hired.id,
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/other-ready",
                "status": "open",
                "recorded_at": "2026-04-07T12:30:00+00:00",
                "commit_sha": "def456",
                "recorded_by_agent_id": other_worker.id,
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "recorded_at": "2026-04-07T12:30:00+00:00",
                },
            },
        )
        blocker_product = self._add_task(
            "task-blocker",
            "Blocked product",
            lane="Done",
            agent_id=blocker_worker.id,
            assigned_engineer_id=hired.id,
        )
        blocker_review = self._add_task(
            "task-blocker-review",
            "Review blocked product",
            lane="Done",
            action_name="feature/review",
            parent_task_id=blocker_product.id,
            pipeline_root_id=blocker_product.id,
            pipeline_depth=1,
            agent_id=blocker_worker.id,
            assigned_engineer_id=hired.id,
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/hired-blocker",
                "status": "open",
                "recorded_at": "2026-04-07T10:30:00+00:00",
                "commit_sha": "aaa111",
                "recorded_by_agent_id": blocker_worker.id,
            },
            completion_evidence={
                "review": {
                    "verdict": "block",
                    "follow_up_classification": "blocking",
                    "recorded_at": "2026-04-07T10:30:00+00:00",
                },
            },
        )
        blocker_fix = self._add_task(
            "task-blocker-fix",
            "Fix review blockers",
            lane="In Progress",
            action_name="feature/implement",
            parent_task_id=blocker_review.id,
            pipeline_root_id=blocker_product.id,
            pipeline_depth=2,
            agent_id=blocker_worker.id,
            assigned_engineer_id=hired.id,
            labels=["review-fix"],
        )
        unhealthy.health_state = "stalled"
        unhealthy.health_since = "2026-04-07T13:00:00+00:00"
        blocker_fix.health_state = "idle-risk"
        blocker_fix.health_since = "2026-04-07T13:05:00+00:00"
        parked_unhealthy.health_state = "stalled"
        parked_unhealthy.health_since = "2026-04-07T13:10:00+00:00"

        with mock.patch("time.time", return_value=1234.5):
            self.state.update_engineer_settings(
                "torque",
                pending_question="Need approval for scope cut?",
                paused=True,
                _pending_question_actor_id=hired.id,
            )
        self.state.save_pending_hire({
            "id": "hire-1",
            "architect_id": architect.id,
            "requested_name": "QA Engineer",
            "requested_command": "codex",
            "requested_provider": "codex",
            "requested_specializations": ["quality-observability"],
            "status": "pending",
            "created_at": 99,
        })
        self.state.save_pending_hire({
            "id": "hire-other",
            "architect_id": other_architect.id,
            "requested_name": "Other QA",
            "status": "pending",
            "created_at": 100,
        })
        self.db.save_agent_peer_message({
            "id": "peer-needs-ack",
            "thread_id": "peer-thread",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Please ack the handoff.",
            "created_at": 50.0,
            "ack_required": True,
            "context_task_ids": [ask.id],
            "context_summary": "handoff",
        })
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/hired-ready", "main", {
                "state": "fresh", "stale": False,
                "source": "merge_readiness_check", "merge_clean": True,
            },
        )

        text, is_error = await self._call(
            "architect_attention_digest",
            {"limit_per_section": 3},
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "architect_attention_digest")
        self.assertEqual(payload["group"], "torque")
        sections = payload["sections"]
        self.assertEqual(
            [item["id"] for item in sections["blocking_asks"]["items"]],
            [ask.id],
        )
        self.assertEqual(
            sections["engineer_pending_questions"]["items"][0]["engineer_id"],
            hired.id,
        )
        self.assertEqual(
            sections["peer_ack_required"]["items"][0]["thread_id"],
            "peer-thread",
        )
        ready_ids = {
            task_id
            for item in sections["ready_to_merge_streams"]["items"]
            for task_id in item["product_task_ids"]
        }
        self.assertIn(ready_product.id, ready_ids)
        self.assertNotIn(other_product.id, ready_ids)
        ready_stream = next(
            item for item in sections["ready_to_merge_streams"]["items"]
            if ready_product.id in item["product_task_ids"]
        )
        self.assertEqual(ready_stream["completion_deviations"], [{
            "task_id": ready_product.id,
            "task_title": ready_product.task,
            "statement": "Delivered the API contract only.",
            "reason": "UI integration needs product review.",
            "agent_id": worker.id,
            "agent_name": worker.name,
            "recorded_at": "2026-04-07T11:00:00+00:00",
        }])
        self.assertEqual(
            ready_stream["completion_deviation_disclosure_attempts"], [{
                "task_id": ready_product.id,
                "task_title": ready_product.task,
                "statement": "",
                "reason": "The final deployment check is deferred.",
                "missing_fields": ["statement"],
                "agent_id": worker.id,
                "agent_name": worker.name,
                "recorded_at": "2026-04-07T11:01:00+00:00",
            }],
        )
        blocker_ids = {
            item.get("active_blocker_task_id", "")
            for item in sections["blocker_or_stale_streams"]["items"]
        }
        self.assertIn(blocker_fix.id, blocker_ids)
        unhealthy_ids = {
            item["id"] for item in sections["unhealthy_tasks"]["items"]
        }
        self.assertIn(unhealthy.id, unhealthy_ids)
        unhealthy_item = next(
            item for item in sections["unhealthy_tasks"]["items"]
            if item["id"] == unhealthy.id
        )
        self.assertEqual(unhealthy_item["assigned_engineer_id"], hired.id)
        self.assertEqual(unhealthy_item["health_state"], "stalled")
        self.assertEqual(
            unhealthy_item["health_since"], "2026-04-07T13:00:00+00:00"
        )
        unhealthy_stream_task_ids = {
            task["id"]
            for stream in sections["unhealthy_streams"]["items"]
            for task in stream["unhealthy_tasks"]
        }
        self.assertIn(blocker_fix.id, unhealthy_stream_task_ids)
        self.assertEqual(
            [item["id"] for item in sections["pending_hires"]["items"]],
            ["hire-1"],
        )
        self.assertEqual(payload["parked_deferred"]["count"], 3)
        section_text = json.dumps(sections)
        for parked_task in (parked, parked_human, parked_unhealthy):
            self.assertNotIn(
                parked_task.id,
                section_text,
                "parked/deferred work must stay out of attention sections",
            )
        self.assertEqual(
            payload["scoping"]["ready_merge_and_stream_loops"],
            "hired_engineers_only",
        )

    async def test_architect_attention_digest_counts_peer_ack_truncation(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-peer", "Peer Architect")
        for idx in range(3):
            self.db.save_agent_peer_message({
                "id": f"peer-needs-ack-{idx}",
                "thread_id": f"peer-thread-{idx}",
                "group_name": "torque",
                "sender_id": peer.id,
                "sender_kind": "architect",
                "recipient_id": architect.id,
                "recipient_kind": "architect",
                "message": f"Please ack handoff {idx}.",
                "created_at": 50.0 + idx,
                "ack_required": True,
            })

        text, is_error = await self._call(
            "architect_attention_digest",
            {"limit_per_section": 2},
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        peer_section = payload["sections"]["peer_ack_required"]
        self.assertEqual(peer_section["count"], 3)
        self.assertEqual(len(peer_section["items"]), 2)
        self.assertTrue(peer_section["truncated"])
        self.assertEqual(payload["attention_count"], 3)

    async def test_architect_attention_digest_discloses_real_peer_load_window(self):
        """The digest must not present a full 1,000-row source window as complete."""
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-peer", "Peer Architect")
        load_limit = self.shared_mod._ARCHITECT_PEER_SUMMARY_LOAD_LIMIT

        # This is intentionally the real production window, not a lowered
        # test constant. Every row satisfies the peer-loader's production
        # non-user, non-blocking, non-archived message predicate.
        for idx in range(load_limit):
            self.db.save_agent_peer_message({
                "id": f"synthetic-peer-noise-{idx:04d}",
                "thread_id": f"synthetic-noise-thread-{idx:04d}",
                "group_name": "torque",
                "sender_id": peer.id,
                "sender_kind": "architect",
                "recipient_id": architect.id,
                "recipient_kind": "architect",
                "message": "Synthetic non-pending peer traffic.",
                "message_type": "message",
                "blocking": False,
                "archived_at": 0,
                "created_at": 10_000.0 + idx,
                "ack_required": False,
            })
        self.db.save_agent_peer_message({
            "id": "synthetic-pending-beyond-real-window",
            "thread_id": "synthetic-pending-beyond-real-window",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Synthetic pending ack beyond the real load window.",
            "message_type": "message",
            "blocking": False,
            "archived_at": 0,
            "created_at": 1.0,
            "ack_required": True,
        })
        matching_count = self.db._conn.execute(
            "SELECT COUNT(*) FROM agent_peer_messages WHERE "
            "(sender_id=? OR recipient_id=?) "
            "AND sender_kind!='user' AND recipient_kind!='user' "
            "AND message_type='message' AND blocking=0 AND archived_at=0",
            (architect.id, architect.id),
        ).fetchone()[0]
        self.assertEqual(matching_count, load_limit + 1)
        loaded_rows = self.db.load_agent_peer_messages_for_agent(
            architect.id,
            limit=load_limit,
        )
        self.assertEqual(len(loaded_rows), load_limit)
        self.assertNotIn(
            "synthetic-pending-beyond-real-window",
            {row["id"] for row in loaded_rows},
        )

        text, is_error = await self._call(
            "architect_attention_digest",
            {"limit_per_section": 20},
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertTrue(payload["attention_count_is_bounded"])
        peer_section = payload["sections"]["peer_ack_required"]
        self.assertFalse(peer_section["truncated"])
        self.assertTrue(peer_section["source_truncated"])
        self.assertEqual(peer_section["load_limit"], load_limit)
        self.assertEqual(peer_section["count_scope"], "loaded_source_window")

    def test_bounded_items_separates_display_and_source_truncation(self):
        source_only = self.shared_mod._bounded_items(
            [],
            1,
            source_truncated=True,
            source_limit=1000,
        )
        display_only = self.shared_mod._bounded_items(
            [{"id": "one"}, {"id": "two"}],
            1,
            source_truncated=False,
            source_limit=None,
        )
        full_window = self.shared_mod._bounded_items(
            [{"id": str(index)} for index in range(1000)],
            1,
            source_truncated=False,
            source_limit=1000,
        )

        self.assertFalse(source_only["truncated"])
        self.assertTrue(source_only["source_truncated"])
        self.assertEqual(source_only["load_limit"], 1000)
        self.assertEqual(source_only["count_scope"], "loaded_source_window")
        self.assertTrue(display_only["truncated"])
        self.assertFalse(display_only["source_truncated"])
        self.assertEqual(display_only["count_scope"], "complete_set")
        self.assertTrue(full_window["source_truncated"])
        self.assertEqual(full_window["count_scope"], "loaded_source_window")

    def test_peer_pending_reply_predicate_matches_thread_aggregate(self):
        """Keep the in-memory digest predicate aligned with the SQL aggregate."""
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-peer", "Peer Architect")
        cases = {
            "pending": [(peer.id, 10.0, True), (architect.id, 9.0, False)],
            "answered": [(peer.id, 20.0, True), (architect.id, 21.0, False)],
            "not-an-ack": [(peer.id, 30.0, False)],
            "same-time": [(peer.id, 40.0, True), (architect.id, 40.0, False)],
        }
        for thread_id, messages in cases.items():
            for index, (sender_id, created_at, ack_required) in enumerate(messages):
                self.db.save_agent_peer_message({
                    "id": f"predicate-{thread_id}-{index}",
                    "thread_id": thread_id,
                    "group_name": "torque",
                    "sender_id": sender_id,
                    "sender_kind": "architect",
                    "recipient_id": (
                        peer.id if sender_id == architect.id else architect.id
                    ),
                    "recipient_kind": "architect",
                    "message": "Synthetic predicate case.",
                    "message_type": "message",
                    "blocking": False,
                    "archived_at": 0,
                    "created_at": created_at,
                    "ack_required": ack_required,
                })

        aggregate_pending = {
            row["thread_id"]
            for row in self.db.load_agent_peer_threads_for_agent(
                architect.id,
                limit=100,
                sender_kind="architect",
                recipient_kind="architect",
                requires_reply=True,
            )
        }
        digest_pending = set()
        for thread_id in cases:
            messages = self.db.load_agent_peer_messages_for_agent(
                architect.id,
                limit=100,
                thread_id=thread_id,
            )
            if self.shared_mod._thread_requires_architect_reply(
                    messages, architect.id):
                digest_pending.add(thread_id)

        self.assertEqual(digest_pending, {"pending"})
        self.assertEqual(aggregate_pending, digest_pending)

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
            "dispatch_state",
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
                "group": "torque",
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
        self.assertEqual(task.dispatch_state, "queued")

        visible_denied_text, visible_denied_error = await self._call(
            "architect_task_create",
            {
                "title": "Visible engineer assign",
                "group": "torque",
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
                "group": "torque",
                "assigned_engineer_id": bob.id,
            },
            architect.id,
        )

        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "engineer not found in scope")

    async def test_architect_task_create_rejects_unknown_suggested_action_before_write(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )

        text, is_error = await self._call(
            "architect_task_create",
            {
                "title": "Invalid action hint",
                "group": "torque",
                "assigned_engineer_id": alice.id,
                "suggested_action": "bogus/nonexistent-action",
            },
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertIn("Unknown suggested_action 'bogus/nonexistent-action'", text)
        self.assertIn("ActionManager.list_actions()", text)
        self.assertEqual(self.state.board_tasks, {})
        self.assertEqual(
            self.handle_calls,
            [{"cmd": "list_actions", "group": "torque"}],
        )

    async def test_architect_task_create_refuses_actionless_atomic_dispatch(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        alice.session_id = "session-alice"

        text, is_error = await self._call(
            "architect_task_create",
            {
                "title": "Implement task dispatch state",
                "group": "torque",
                "description": "Wire backend field and create dispatch.",
                "assigned_engineer_id": alice.id,
                "suggested_action": "feature/implement",
                "dispatch": True,
                "dispatch_message": "Start this immediately.",
            },
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertIn("requires an action binding before dispatch", text)
        # A suggestion is deliberately not promoted to a binding and the
        # atomic create leaves no queued-or-live partial task behind.
        self.assertEqual(self.state.board_tasks, {})
        self.assertEqual(alice.mcp_messages, [])
        self.assertEqual(
            self.handle_calls,
            [{"cmd": "list_actions", "group": "torque"}],
        )

    async def test_architect_task_create_can_dispatch_when_group_default_is_bound(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        alice.session_id = "session-alice"
        self.state.group_settings["torque"] = self.state_mod.GroupSettings(
            board_default_action="feature/implement"
        )
        self.assertEqual(
            self.state.get_group_settings("torque").board_default_action,
            "feature/implement",
        )

        text, is_error = await self._call(
            "architect_task_create",
            {
                "title": "Implement task dispatch state",
                "group": "torque",
                "assigned_engineer_id": alice.id,
                "dispatch": True,
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        task = self.state.board_tasks[payload["task_id"]]
        self.assertEqual(task.action_name, "feature/implement")
        self.assertEqual(task.dispatch_state, "live")
        self.assertEqual(payload["dispatch_state"], "live")

    async def test_architect_message_explicit_task_marks_existing_queued_task_live(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )

        created_text, created_error = await self._call(
            "architect_task_create",
            {
                "title": "Stage then dispatch",
                "group": "torque",
                "assigned_engineer_id": alice.id,
            },
            architect.id,
        )
        self.assertFalse(created_error, created_text)
        task_id = json.loads(created_text)["task_id"]
        self.assertEqual(self.state.board_tasks[task_id].dispatch_state, "queued")

        # The Engineer—not the Architect's suggestion—chooses and binds the
        # action while the task is staged.
        edit_text, edit_error = await self._call_engineer(
            "engineer_task_edit",
            {"task": task_id, "action": "feature/implement"},
            alice.id,
        )
        self.assertFalse(edit_error, edit_text)
        self.assertEqual(
            self.state.board_tasks[task_id].action_name, "feature/implement"
        )

        message_text, message_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": alice.id,
                "task": task_id,
                "message": f"Please pick up {task_id} now.",
            },
            architect.id,
        )

        self.assertFalse(message_error, message_text)
        payload = json.loads(message_text)
        self.assertEqual(payload["task_id"], task_id)
        self.assertEqual(payload["dispatch_state"], "live")
        self.assertEqual(self.state.board_tasks[task_id].dispatch_state, "live")
        self.assertEqual(self.state.board_tasks[task_id].lane, "To Do")

    async def test_architect_message_explicit_task_refuses_actionless_dispatch(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        task = self._add_task(
            "TORQUE:1",
            "Choose action before dispatch",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            dispatch_state="queued",
        )

        text, is_error = await self._call(
            "architect_engineer_message",
            {"engineer_id": alice.id, "task": task.id, "message": "Start."},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertIn("requires an action binding before dispatch", text)
        self.assertEqual(task.dispatch_state, "queued")
        self.assertEqual(alice.mcp_messages, [])

    async def test_architect_message_task_references_do_not_dispatch_without_task(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        referenced = self._add_task(
            "TORQUE:1",
            "Referenced staged task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            action_name="feature/implement",
            dispatch_state="queued",
        )
        other_staged = self._add_task(
            "TORQUE:10",
            "Other staged task",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            dispatch_state="queued",
        )

        unrelated_text, unrelated_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": alice.id,
                "message": "Please review the deployment discussion.",
            },
            architect.id,
        )

        self.assertFalse(unrelated_error, unrelated_text)
        unrelated_payload = json.loads(unrelated_text)
        self.assertNotIn("task_id", unrelated_payload)
        self.assertNotIn("dispatch_advisory", unrelated_payload)
        self.assertEqual(referenced.dispatch_state, "queued")
        self.assertEqual(other_staged.dispatch_state, "queued")

        context_text, context_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": alice.id,
                "message": (
                    f"The merge note cites {referenced.id} for context only; "
                    "please continue the unrelated discussion."
                ),
            },
            architect.id,
        )

        self.assertFalse(context_error, context_text)
        context_payload = json.loads(context_text)
        self.assertNotIn("task_id", context_payload)
        self.assertEqual(
            context_payload["dispatch_advisory"],
            "This message referenced one eligible staged task but did not "
            "dispatch it. Pass task=<task id or slug> explicitly to dispatch.",
        )
        self.assertEqual(referenced.dispatch_state, "queued")
        self.assertEqual(other_staged.dispatch_state, "queued")

        ambiguous_text, ambiguous_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": alice.id,
                "message": f"Compare {referenced.id} with {other_staged.id}.",
            },
            architect.id,
        )

        self.assertFalse(ambiguous_error, ambiguous_text)
        ambiguous_payload = json.loads(ambiguous_text)
        self.assertNotIn("task_id", ambiguous_payload)
        self.assertNotIn("dispatch_advisory", ambiguous_payload)
        self.assertEqual(referenced.dispatch_state, "queued")
        self.assertEqual(other_staged.dispatch_state, "queued")

        dispatch_text, dispatch_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": alice.id,
                "task": referenced.id,
                "message": f"Please pick up {referenced.id} now.",
            },
            architect.id,
        )

        self.assertFalse(dispatch_error, dispatch_text)
        dispatch_payload = json.loads(dispatch_text)
        self.assertEqual(dispatch_payload["task_id"], referenced.id)
        self.assertNotIn("dispatch_advisory", dispatch_payload)
        self.assertEqual(
            self.state.board_tasks[referenced.id].dispatch_state,
            "live",
        )
        self.assertEqual(other_staged.dispatch_state, "queued")

    async def test_architect_message_task_slug_context_does_not_dispatch(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        queued = self._add_task(
            "TORQUE:20",
            "Review dashboard",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            dispatch_state="queued",
        )

        context_text, context_error = await self._call(
            "architect_engineer_message",
            {
                "engineer_id": alice.id,
                "message": f"Please check {queued.slug} notes for context only.",
            },
            architect.id,
        )

        self.assertFalse(context_error, context_text)
        context_payload = json.loads(context_text)
        self.assertNotIn("task_id", context_payload)
        self.assertEqual(
            context_payload["dispatch_advisory"],
            "This message referenced one eligible staged task but did not "
            "dispatch it. Pass task=<task id or slug> explicitly to dispatch.",
        )
        self.assertEqual(queued.dispatch_state, "queued")

    async def test_architect_tools_exclude_and_reject_tombstoned_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        now = 123.0
        engineer.deleted_at = now
        engineer.permanent_delete_after = now + 7 * 86400

        list_text, list_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )
        self.assertFalse(list_error, list_text)
        self.assertEqual(json.loads(list_text)["engineers"], [])

        create_text, create_error = await self._call(
            "architect_task_create",
            {
                "title": "Assign tombstone",
                "group": "torque",
                "assigned_engineer_id": engineer.id,
            },
            architect.id,
        )
        self.assertTrue(create_error)
        self.assertEqual(create_text, "engineer is tombstoned")

        visible_text, visible_error = await self._call(
            "architect_engineer_list",
            {"include_tombstoned": True},
            architect.id,
        )
        self.assertFalse(visible_error, visible_text)
        engineers = json.loads(visible_text)["engineers"]
        self.assertEqual(len(engineers), 1)
        self.assertEqual(engineers[0]["id"], engineer.id)
        self.assertEqual(engineers[0]["deleted_at"], now)

    async def test_architect_engineer_list_includes_specializations(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        alice.engineer_specializations = ["ui-ux", "security-focus"]
        self.state._db_save_agent(alice)
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=architect.id
        )

        text, is_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        engineers = {
            item["id"]: item for item in json.loads(text)["engineers"]
        }
        self.assertEqual(
            engineers[alice.id]["specializations"],
            ["ui-ux", "security-focus"],
        )
        self.assertEqual(engineers[bob.id]["specializations"], [])
        self.assertEqual(engineers[bob.id]["specialization_display"], "generalist")
        self.assertEqual(
            engineers[alice.id]["specialization_display"],
            "ui-ux, security-focus",
        )

    async def test_architect_engineer_feedback_request_fans_out_to_hired_only(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        dave = self._add_engineer(
            "eng-dave", "Dave", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=other_architect.id
        )
        user_visible = self._add_engineer("eng-user", "User Visible")
        task_count = len(self.state.board_tasks)

        text, is_error = await self._call(
            "architect_engineer_feedback_request",
            {},
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "engineer_feedback_request")
        self.assertTrue(payload["request_id"].startswith("feedback-"))
        self.assertEqual(payload["requested_count"], 2)
        self.assertEqual(
            {item["engineer_id"] for item in payload["requested"]},
            {alice.id, dave.id},
        )
        self.assertIn("What worked well?", payload["categories"])
        self.assertEqual(len(self.state.board_tasks), task_count)
        self.assertEqual(len(alice.mcp_messages), 1)
        self.assertEqual(len(dave.mcp_messages), 1)
        self.assertEqual(bob.mcp_messages, [])
        self.assertEqual(user_visible.mcp_messages, [])
        self.assertIn(
            f"feedback_request_id: {payload['request_id']}",
            alice.mcp_messages[0]["message"],
        )
        injects = [
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
        ]
        self.assertEqual(
            {call["agent_id"] for call in injects},
            {alice.id, dave.id},
        )
        rows = self.db.load_agent_peer_messages_for_agent(architect.id)
        feedback_rows = [
            row for row in rows
            if row.get("context_summary")
            == f"feedback_request_id={payload['request_id']}"
        ]
        self.assertEqual(len(feedback_rows), 2)

    async def test_architect_engineer_feedback_status_tracks_thread_replies(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        dave = self._add_engineer(
            "eng-dave", "Dave", hired_by_architect_id=architect.id
        )
        self._add_engineer("eng-user", "User Visible")

        request_text, request_error = await self._call(
            "architect_engineer_feedback_request",
            {
                "request_id": "retro-1",
                "prompt": "Share release wave feedback.",
                "categories": ["wins", "friction"],
            },
            architect.id,
        )
        self.assertFalse(request_error, request_text)
        request_payload = json.loads(request_text)
        by_engineer = {
            item["engineer_id"]: item
            for item in request_payload["requested"]
        }

        reply_text, reply_error = await self._call_engineer(
            "engineer_reply",
            {
                "message_id": by_engineer[alice.id]["message_id"],
                "message": "wins: fast reviews; friction: tracking replies",
            },
            alice.id,
        )
        self.assertFalse(reply_error, reply_text)
        reply_payload = json.loads(reply_text)

        status_text, status_error = await self._call(
            "architect_engineer_feedback_status",
            {"request_id": "retro-1"},
            architect.id,
        )

        self.assertFalse(status_error, status_text)
        status = json.loads(status_text)
        self.assertEqual(status["type"], "engineer_feedback_status")
        self.assertEqual(status["request_id"], "retro-1")
        self.assertEqual(status["requested_count"], 2)
        self.assertEqual(status["replied_count"], 1)
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual(
            {item["engineer_id"] for item in status["replied"]},
            {alice.id},
        )
        self.assertEqual(
            {item["engineer_id"] for item in status["pending"]},
            {dave.id},
        )
        replied = status["replied"][0]
        self.assertEqual(
            replied["request_message_id"],
            by_engineer[alice.id]["message_id"],
        )
        self.assertEqual(
            replied["thread_id"],
            by_engineer[alice.id]["thread_id"],
        )
        self.assertEqual(replied["reply_message_id"], reply_payload["message_id"])
        pending = status["pending"][0]
        self.assertEqual(
            pending["request_message_id"],
            by_engineer[dave.id]["message_id"],
        )
        self.assertEqual(
            status["reply_detection"],
            "engineer->architect messages in each request thread",
        )

        latest_text, latest_error = await self._call(
            "architect_engineer_feedback_status",
            {},
            architect.id,
        )
        self.assertFalse(latest_error, latest_text)
        self.assertEqual(json.loads(latest_text)["request_id"], "retro-1")

        row_count_before_repeat = len(
            self.db.load_agent_peer_messages_for_agent(architect.id)
        )
        inject_count_before_repeat = len([
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
        ])
        repeat_text, repeat_error = await self._call(
            "architect_engineer_feedback_request",
            {"request_id": "retro-1"},
            architect.id,
        )
        self.assertFalse(repeat_error, repeat_text)
        repeat = json.loads(repeat_text)
        self.assertTrue(repeat["deduped"])
        self.assertEqual(repeat["requested_count"], 2)
        self.assertEqual(
            len(self.db.load_agent_peer_messages_for_agent(architect.id)),
            row_count_before_repeat,
        )
        self.assertEqual(
            len([
                call for call in self.handle_calls
                if call.get("cmd") == "inject_mcp_message"
            ]),
            inject_count_before_repeat,
        )

    async def test_architect_engineer_set_specializations_replaces_and_persists(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        self.state._delta_ops = []

        text, is_error = await self._call(
            "architect_engineer_set_specializations",
            {
                "engineer_id": "alice",
                "specializations": [
                    "ui-ux",
                    "",
                    "orchestration-core",
                    "ui-ux",
                ],
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "engineer_specializations")
        self.assertEqual(payload["engineer_id"], alice.id)
        self.assertEqual(
            payload["specializations"],
            ["ui-ux", "orchestration-core"],
        )
        self.assertEqual(payload["primary_specialization"], "ui-ux")
        self.assertEqual(
            alice.engineer_specializations,
            ["ui-ux", "orchestration-core"],
        )
        self.assertEqual(self.state._delta_ops[-1]["op"], "agent_upsert")
        self.assertEqual(
            self.db.load_all()["agents"][alice.id]["engineer_specializations"],
            ["ui-ux", "orchestration-core"],
        )

        list_text, list_error = await self._call(
            "architect_engineer_list",
            {},
            architect.id,
        )
        self.assertFalse(list_error, list_text)
        engineers = {
            item["id"]: item for item in json.loads(list_text)["engineers"]
        }
        self.assertEqual(
            engineers[alice.id]["specializations"],
            ["ui-ux", "orchestration-core"],
        )

        self.state._delta_ops = []
        clear_text, clear_error = await self._call(
            "architect_engineer_set_specializations",
            {"engineer_id": alice.id, "specializations": []},
            architect.id,
        )
        self.assertFalse(clear_error, clear_text)
        clear_payload = json.loads(clear_text)
        self.assertEqual(clear_payload["specializations"], [])
        self.assertEqual(clear_payload["primary_specialization"], "")
        self.assertEqual(alice.engineer_specializations, [])
        self.assertEqual(self.state._delta_ops[-1]["op"], "agent_upsert")

        self.state._delta_ops = []
        noop_text, noop_error = await self._call(
            "architect_engineer_set_specializations",
            {"engineer_id": alice.id, "specializations": []},
            architect.id,
        )
        self.assertFalse(noop_error, noop_text)
        self.assertEqual(self.state._delta_ops, [])

    async def test_architect_engineer_set_specializations_rejects_invalid_and_out_of_scope(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=other_architect.id
        )
        user_engineer = self._add_engineer("eng-user", "User Owned")

        invalid_text, invalid_error = await self._call(
            "architect_engineer_set_specializations",
            {
                "engineer_id": alice.id,
                "specializations": ["ui-ux", "local-only"],
            },
            architect.id,
        )
        self.assertTrue(invalid_error)
        self.assertIn("Unknown specialization", invalid_text)
        self.assertIn("Valid specializations:", invalid_text)
        self.assertEqual(alice.engineer_specializations, [])

        other_text, other_error = await self._call(
            "architect_engineer_set_specializations",
            {"engineer_id": bob.id, "specializations": ["ui-ux"]},
            architect.id,
        )
        self.assertTrue(other_error)
        self.assertEqual(other_text, "engineer not found in scope")

        user_text, user_error = await self._call(
            "architect_engineer_set_specializations",
            {"engineer_id": user_engineer.id, "specializations": ["ui-ux"]},
            architect.id,
        )
        self.assertTrue(user_error)
        self.assertEqual(user_text, "engineer not found in scope")

    async def test_architect_engineer_set_specializations_updates_routing_surfaces(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )

        set_text, set_error = await self._call(
            "architect_engineer_set_specializations",
            {"engineer_id": alice.id, "specializations": ["ui-ux"]},
            architect.id,
        )
        self.assertFalse(set_error, set_text)

        ok_text, ok_error = await self._call(
            "architect_task_create",
            {
                "title": "Polish task modal layout",
                "group": "torque",
                "assigned_engineer_id": alice.id,
                "suggested_specialization": "ui-ux",
            },
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        ok_payload = json.loads(ok_text)
        self.assertNotIn("suggested_specialization_warning", ok_payload)

        other_task = self._add_task(
            "task-runtime",
            "PTY reconnection",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            suggested_specialization="runtime-pty",
        )
        filtered_text, filtered_error = await self._call(
            "architect_board_summary",
            {"specialization_engineer_id": alice.id},
            architect.id,
        )
        self.assertFalse(filtered_error, filtered_text)
        filtered = json.loads(filtered_text)
        self.assertNotIn(
            other_task.id,
            {item["id"] for item in filtered["tasks"]["items"]},
        )
        self.assertIn(
            ok_payload["task_id"],
            {item["id"] for item in filtered["tasks"]["items"]},
        )

    async def test_architect_task_create_persists_specialization_and_warns_on_mismatch(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        alice.engineer_specializations = ["ui-ux"]
        self.state._db_save_agent(alice)
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=architect.id
        )
        bob.engineer_specializations = ["events"]
        self.state._db_save_agent(bob)

        ok_text, ok_error = await self._call(
            "architect_task_create",
            {
                "title": "Polish task modal layout",
                "group": "torque",
                "assigned_engineer_id": alice.id,
                "suggested_specialization": "ui-ux",
            },
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        ok_payload = json.loads(ok_text)
        self.assertNotIn("suggested_specialization_warning", ok_payload)
        ok_task = self.state.board_tasks[ok_payload["task_id"]]
        self.assertEqual(ok_task.suggested_specialization, "ui-ux")

        warn_text, warn_error = await self._call(
            "architect_task_create",
            {
                "title": "UI polish for settings",
                "group": "torque",
                "assigned_engineer_id": bob.id,
                "suggested_specialization": "ui-ux",
            },
            architect.id,
        )
        self.assertFalse(warn_error, warn_text)
        warn_payload = json.loads(warn_text)
        self.assertIn("suggested_specialization_warning", warn_payload)
        self.assertIn("ui-ux", warn_payload["suggested_specialization_warning"])
        warn_task = self.state.board_tasks[warn_payload["task_id"]]
        self.assertEqual(warn_task.suggested_specialization, "ui-ux")

    async def test_architect_board_summary_filters_by_specialization(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        alice.engineer_specializations = ["ui-ux"]
        self.state._db_save_agent(alice)

        ui_task = self._add_task(
            "task-ui",
            "Polish header",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            suggested_specialization="ui-ux",
        )
        events_task = self._add_task(
            "task-events",
            "Add event digest",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            suggested_specialization="events",
        )
        untagged_task = self._add_task(
            "task-untagged",
            "General work",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )

        unfiltered_text, unfiltered_error = await self._call(
            "architect_board_summary",
            {},
            architect.id,
        )
        self.assertFalse(unfiltered_error, unfiltered_text)
        unfiltered = json.loads(unfiltered_text)
        self.assertEqual(
            {item["id"] for item in unfiltered["tasks"]["items"]},
            {ui_task.id, events_task.id, untagged_task.id},
        )
        ui_item = next(
            item for item in unfiltered["tasks"]["items"]
            if item["id"] == ui_task.id
        )
        self.assertEqual(ui_item["suggested_specialization"], "ui-ux")

        filtered_text, filtered_error = await self._call(
            "architect_board_summary",
            {"specialization_engineer_id": alice.id},
            architect.id,
        )
        self.assertFalse(filtered_error, filtered_text)
        filtered = json.loads(filtered_text)
        self.assertEqual(
            {item["id"] for item in filtered["tasks"]["items"]},
            {ui_task.id},
        )

    async def test_architect_board_summary_generalist_filter_includes_all_specializations(self):
        architect = self._add_architect("arch-1", "Architect")
        generalist = self._add_engineer(
            "eng-generalist", "Generalist", hired_by_architect_id=architect.id
        )
        ui_task = self._add_task(
            "task-ui", "Polish header", assigned_engineer_id=generalist.id,
            created_by_architect_id=architect.id, suggested_specialization="ui-ux",
        )
        events_task = self._add_task(
            "task-events", "Add event digest", assigned_engineer_id=generalist.id,
            created_by_architect_id=architect.id, suggested_specialization="events",
        )

        text, is_error = await self._call(
            "architect_board_summary",
            {"specialization_engineer_id": generalist.id},
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(
            {item["id"] for item in json.loads(text)["tasks"]["items"]},
            {ui_task.id, events_task.id},
        )

    async def test_architect_board_summary_rejects_architect_as_specialization_engineer(self):
        architect = self._add_architect("arch-1", "Architect")
        specialist = self._add_engineer(
            "eng-specialist", "Specialist", hired_by_architect_id=architect.id
        )
        specialist.engineer_specializations = ["ui-ux"]
        self.state._db_save_agent(specialist)
        self._add_task(
            "task-ui", "Polish header", assigned_engineer_id=specialist.id,
            created_by_architect_id=architect.id, suggested_specialization="ui-ux",
        )

        text, is_error = await self._call(
            "architect_board_summary",
            {"specialization_engineer_id": architect.id},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "engineer not found in scope")

    async def test_architect_task_create_generalist_has_no_specialization_mismatch_warning(self):
        architect = self._add_architect("arch-1", "Architect")
        generalist = self._add_engineer(
            "eng-generalist", "Generalist", hired_by_architect_id=architect.id
        )

        text, is_error = await self._call(
            "architect_task_create",
            {
                "title": "Generalist fallback",
                "group": "torque",
                "assigned_engineer_id": generalist.id,
                "suggested_specialization": "ui-ux",
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertNotIn(
            "suggested_specialization_warning", json.loads(text)
        )

    async def test_architect_task_list_filters_and_scopes_to_architect_group(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=architect.id
        )
        self.state.groups["other"] = []
        self.state._db_save_groups()

        own_triage_p3 = self._add_task(
            "task-own-triage-p3",
            "Own triage P3",
            labels=["triage", "P3"],
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        own_triage_p2_todo = self._add_task(
            "task-own-triage-p2",
            "Own triage P2",
            lane="To Do",
            labels=["triage", "P2"],
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        own_p3_only_bob = self._add_task(
            "task-own-p3",
            "Own P3 only",
            labels=["P3"],
            assigned_engineer_id=bob.id,
            created_by_architect_id=architect.id,
        )
        engineer_created = self._add_task(
            "task-engineer",
            "Engineer-created triage P3",
            lane="To Do",
            labels=["triage", "P3"],
            assigned_engineer_id=bob.id,
            created_by_engineer_id=alice.id,
        )
        user_created = self._add_task(
            "task-user",
            "User-created triage",
            labels=["triage"],
            assigned_engineer_id=alice.id,
        )
        system_created = self._add_task(
            "task-system",
            "System-created triage",
            labels=["triage"],
            parent_task_id=own_triage_p3.id,
            pipeline_root_id=own_triage_p3.id,
        )
        other_arch_created = self._add_task(
            "task-other-architect",
            "Other architect triage",
            labels=["triage"],
            assigned_engineer_id=bob.id,
            created_by_architect_id=other_architect.id,
        )
        archived = self._add_task(
            "task-archived",
            "Archived triage",
            labels=["triage"],
            archived_at="2026-04-01T00:00:00+00:00",
            created_by_architect_id=architect.id,
        )
        cross_group = self._add_task(
            "task-cross-group",
            "Cross-group triage must stay hidden",
            group="other",
            labels=["triage"],
            created_by_architect_id=architect.id,
        )

        async def listed_ids(args):
            text, is_error = await self._call(
                "architect_task_list",
                args,
                architect.id,
            )
            self.assertFalse(is_error, text)
            payload = json.loads(text)
            return payload, {item["id"] for item in payload["tasks"]}

        payload, ids = await listed_ids({"label_filter": "triage"})
        self.assertEqual(payload["type"], "task_list")
        self.assertFalse(payload["truncated"])
        self.assertEqual(
            ids,
            {
                own_triage_p3.id,
                own_triage_p2_todo.id,
                engineer_created.id,
                user_created.id,
                system_created.id,
                other_arch_created.id,
            },
        )
        self.assertNotIn(archived.id, ids)
        self.assertNotIn(cross_group.id, ids)
        self.assertEqual(payload["total"], len(ids))
        sample_item = payload["tasks"][0]
        self.assertTrue({
            "id",
            "title",
            "lane",
            "labels",
            "assigned_engineer_id",
            "created_by",
            "status",
            "updated_at",
        }.issubset(sample_item))

        _payload, ids = await listed_ids({"label_filter": ["triage", "P3"]})
        self.assertEqual(ids, {own_triage_p3.id, engineer_created.id})

        _payload, ids = await listed_ids({"lane_filter": "To Do"})
        self.assertEqual(ids, {own_triage_p2_todo.id, engineer_created.id})

        _payload, ids = await listed_ids({
            "assigned_engineer_id_filter": bob.id,
        })
        self.assertEqual(
            ids,
            {own_p3_only_bob.id, engineer_created.id, other_arch_created.id},
        )

        _payload, ids = await listed_ids({"creator_filter": "user"})
        self.assertEqual(ids, {user_created.id})

        _payload, ids = await listed_ids({"creator_filter": "architect"})
        self.assertEqual(
            ids,
            {
                own_triage_p3.id,
                own_triage_p2_todo.id,
                own_p3_only_bob.id,
                other_arch_created.id,
            },
        )

        _payload, ids = await listed_ids({"creator_filter": f"engineer:{alice.id}"})
        self.assertEqual(ids, {engineer_created.id})

        _payload, ids = await listed_ids({"creator_filter": "system"})
        self.assertEqual(ids, {system_created.id})

        _payload, ids = await listed_ids({
            "label_filter": "triage",
            "lane_filter": "To Do",
        })
        self.assertEqual(ids, {own_triage_p2_todo.id, engineer_created.id})

        _payload, ids = await listed_ids({"archived": True})
        self.assertEqual(ids, {archived.id})

    async def test_architect_task_list_truncates_with_total_count(self):
        architect = self._add_architect("arch-1", "Architect")
        for idx in range(5):
            self._add_task(
                f"task-bulk-{idx}",
                f"Bulk task {idx}",
                labels=["bulk"],
                created_by_architect_id=architect.id,
            )

        text, is_error = await self._call(
            "architect_task_list",
            {"label_filter": "bulk", "limit": 2},
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "task_list")
        self.assertEqual(payload["total"], 5)
        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(payload["tasks"]), 2)

    async def test_architect_task_list_validates_creator_filter_before_scanning(self):
        architect = self._add_architect("arch-1", "Architect")

        text, is_error = await self._call(
            "architect_task_list",
            {"label_filter": "no-matching-label", "creator_filter": "bogus"},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(
            text,
            "creator_filter must be one of: user, architect, engineer:<id>, system",
        )

    async def test_architect_task_update_edits_own_task_fields_and_labels(self):
        architect = self._add_architect("arch-1", "Architect")
        task = self._add_task(
            "task-update-own",
            "Original title",
            description="Original description",
            labels=["old", "keep"],
            created_by_architect_id=architect.id,
        )
        task.updated_at = "2000-01-01T00:00:00+00:00"
        self.state._db_save_task(task)

        title_text, title_error = await self._call(
            "architect_task_update",
            {"task": task.id, "title": "Updated title"},
            architect.id,
        )
        self.assertFalse(title_error, title_text)
        title_payload = json.loads(title_text)
        self.assertEqual(title_payload["type"], "ok")
        self.assertEqual(title_payload["task_id"], task.id)
        self.assertEqual(title_payload["updated_fields"], ["title"])
        updated = self.state.board_tasks[task.id]
        self.assertEqual(updated.task, "Updated title")
        self.assertEqual(updated.description, "Original description")
        self.assertEqual(updated.labels, ["old", "keep"])
        self.assertNotEqual(updated.updated_at, "2000-01-01T00:00:00+00:00")

        desc_text, desc_error = await self._call(
            "architect_task_update",
            {"task": task.id, "description": "Updated description"},
            architect.id,
        )
        self.assertFalse(desc_error, desc_text)
        self.assertEqual(json.loads(desc_text)["updated_fields"], ["description"])
        updated = self.state.board_tasks[task.id]
        self.assertEqual(updated.task, "Updated title")
        self.assertEqual(updated.description, "Updated description")
        self.assertEqual(updated.labels, ["old", "keep"])

        labels_text, labels_error = await self._call(
            "architect_task_update",
            {"task": task.id, "labels": ["new", "triage"]},
            architect.id,
        )
        self.assertFalse(labels_error, labels_text)
        self.assertEqual(json.loads(labels_text)["updated_fields"], ["labels"])
        updated = self.state.board_tasks[task.id]
        self.assertEqual(updated.task, "Updated title")
        self.assertEqual(updated.description, "Updated description")
        self.assertEqual(updated.labels, ["new", "triage"])

        clear_text, clear_error = await self._call(
            "architect_task_update",
            {"task": task.id, "labels": []},
            architect.id,
        )
        self.assertFalse(clear_error, clear_text)
        self.assertEqual(self.state.board_tasks[task.id].labels, [])

        all_text, all_error = await self._call(
            "architect_task_update",
            {
                "task": task.id,
                "title": "Final title",
                "description": "Final description",
                "labels": ["final"],
            },
            architect.id,
        )
        self.assertFalse(all_error, all_text)
        self.assertEqual(
            json.loads(all_text)["updated_fields"],
            ["title", "description", "labels"],
        )
        updated = self.state.board_tasks[task.id]
        self.assertEqual(updated.task, "Final title")
        self.assertEqual(updated.description, "Final description")
        self.assertEqual(updated.labels, ["final"])

    async def test_architect_task_update_rejects_known_tracked_provider_title_limit(self):
        """The static GitHub constraint refuses before mutation or queueing."""
        architect = self._add_architect("arch-title-limit", "Architect")
        self.state.update_group_settings(
            "torque",
            board_sync_provider="github",
            board_sync_enabled=True,
        )
        task = self._add_task(
            "task-title-limit",
            "Original title",
            description="Original description",
            provider="github",
            external_id="owner/repo#1",
            created_by_architect_id=architect.id,
        )

        class FakeBoardSyncManager:
            def __init__(self):
                self.calls = []

            def enqueue_for_local_change(self, task_id, *, reason, fields):
                self.calls.append((task_id, reason, tuple(fields)))

        manager = FakeBoardSyncManager()
        rejected, rejected_is_error = await self._call_with_server_handle(
            "architect_task_update",
            {"task": task.id, "title": "x" * 257},
            architect.id,
            board_sync_manager=manager,
        )
        self.assertTrue(rejected_is_error)
        self.assertIn("GitHub board sync", rejected)
        self.assertIn("256", rejected)
        self.assertEqual(self.state.board_tasks[task.id].task, "Original title")
        self.assertEqual(manager.calls, [])

        accepted, accepted_is_error = await self._call_with_server_handle(
            "architect_task_update",
            {"task": task.id, "title": "Normal title"},
            architect.id,
            board_sync_manager=manager,
        )
        self.assertFalse(accepted_is_error, accepted)
        self.assertEqual(self.state.board_tasks[task.id].task, "Normal title")
        self.assertEqual(
            manager.calls,
            [(task.id, "task_update", ("task",))],
        )

    async def test_architect_task_update_rejects_auto_tracked_title_but_allows_opt_out(self):
        architect = self._add_architect("arch-auto-title-limit", "Architect")
        self.state.update_group_settings(
            "torque",
            board_sync_provider="github",
            board_sync_enabled=True,
            board_sync_github={"github_sync_default": "all"},
        )
        auto_tracked = self._add_task(
            "task-auto-title-limit",
            "Original title",
            created_by_architect_id=architect.id,
        )
        self.assertEqual(auto_tracked.board_sync, {})
        self.assertEqual(auto_tracked.provider, "")
        self.assertEqual(auto_tracked.external_id, "")

        rejected, rejected_is_error = await self._call(
            "architect_task_update",
            {"task": auto_tracked.id, "title": "x" * 257},
            architect.id,
        )
        self.assertTrue(rejected_is_error)
        self.assertIn("GitHub board sync", rejected)
        self.assertEqual(auto_tracked.task, "Original title")

        opted_out = self._add_task(
            "task-auto-title-opt-out",
            "Original opt-out title",
            board_sync={"enabled": False},
            created_by_architect_id=architect.id,
        )
        accepted, accepted_is_error = await self._call(
            "architect_task_update",
            {"task": opted_out.id, "title": "x" * 257},
            architect.id,
        )
        self.assertFalse(accepted_is_error, accepted)
        self.assertEqual(opted_out.task, "x" * 257)

    async def test_architect_task_update_allows_existing_long_title_non_title_edits_and_shortening(self):
        architect = self._add_architect("arch-existing-long", "Architect")
        self.state.update_group_settings(
            "torque",
            board_sync_provider="github",
            board_sync_enabled=True,
        )
        task = self._add_task(
            "task-existing-long",
            "x" * 257,
            description="Original description",
            provider="github",
            external_id="owner/repo#2",
            created_by_architect_id=architect.id,
        )

        description_result, description_error = await self._call(
            "architect_task_update",
            {"task": task.id, "description": "Still editable"},
            architect.id,
        )
        self.assertFalse(description_error, description_result)
        self.assertEqual(self.state.board_tasks[task.id].task, "x" * 257)
        self.assertEqual(self.state.board_tasks[task.id].description, "Still editable")

        title_result, title_error = await self._call(
            "architect_task_update",
            {"task": task.id, "title": "Short repair"},
            architect.id,
        )
        self.assertFalse(title_error, title_result)
        self.assertEqual(self.state.board_tasks[task.id].task, "Short repair")

    async def test_architect_task_update_allows_long_title_without_active_known_provider_limit(self):
        architect = self._add_architect("arch-no-sync-limit", "Architect")
        task = self._add_task(
            "task-no-sync-limit",
            "Original title",
            created_by_architect_id=architect.id,
        )
        for provider, enabled in (("github", False), ("none", True), ("unknown", True)):
            with self.subTest(provider=provider, enabled=enabled):
                self.state.update_group_settings(
                    "torque",
                    board_sync_provider=provider,
                    board_sync_enabled=enabled,
                )
                text, is_error = await self._call(
                    "architect_task_update",
                    {"task": task.id, "title": "x" * 257},
                    architect.id,
                )
                self.assertFalse(is_error, text)
                self.assertEqual(self.state.board_tasks[task.id].task, "x" * 257)

    async def test_architect_task_update_uses_static_limit_without_provider_reachability(self):
        from torque.board_sync import BoardSyncFieldConstraints

        architect = self._add_architect("arch-static-limit", "Architect")
        self.state.update_group_settings(
            "torque",
            board_sync_provider="github",
            board_sync_enabled=True,
        )
        task = self._add_task(
            "task-static-limit",
            "Original title",
            provider="github",
            external_id="external-1",
            created_by_architect_id=architect.id,
        )

        class OfflineProvider:
            name = "offline-static"

            def field_constraints(self):
                return BoardSyncFieldConstraints(label_name_max_length=3)

            async def preflight(self, _settings):  # pragma: no cover - must not run
                raise AssertionError("label validation must not preflight")

        with mock.patch(
                "torque.server_board_sync.get_provider",
                return_value=OfflineProvider()) as provider_lookup:
            text, is_error = await self._call(
                "architect_task_update",
                {"task": task.id, "labels": ["four"]},
                architect.id,
            )

        self.assertTrue(is_error)
        self.assertIn("Offline Static board sync", text)
        provider_lookup.assert_called_once_with("github")
        self.assertEqual(self.state.board_tasks[task.id].labels, [])

    async def test_architect_task_update_refuses_only_incoming_over_limit_labels(self):
        """GitHub's declared label cap guards writes without blocking repair."""
        architect = self._add_architect("arch-label-limit", "Architect")
        self.state.update_group_settings(
            "torque",
            board_sync_provider="github",
            board_sync_enabled=True,
        )
        task = self._add_task(
            "task-label-limit",
            "Original title",
            description="Original description",
            labels=["normal"],
            provider="github",
            external_id="owner/repo#3",
            created_by_architect_id=architect.id,
        )

        rejected, rejected_is_error = await self._call(
            "architect_task_update",
            {"task": task.id, "labels": ["x" * 51]},
            architect.id,
        )
        self.assertTrue(rejected_is_error)
        self.assertIn("Label name is 51 characters", rejected)
        self.assertIn("50", rejected)
        self.assertEqual(self.state.board_tasks[task.id].labels, ["normal"])

        accepted, accepted_is_error = await self._call(
            "architect_task_update",
            {"task": task.id, "labels": ["triage"]},
            architect.id,
        )
        self.assertFalse(accepted_is_error, accepted)
        self.assertEqual(self.state.board_tasks[task.id].labels, ["triage"])

        # Existing invalid records remain editable through unrelated fields;
        # the guard inspects the incoming labels only.
        self.state.board_update_task(task.id, labels=["x" * 51])
        description_result, description_is_error = await self._call(
            "architect_task_update",
            {"task": task.id, "description": "Still editable"},
            architect.id,
        )
        self.assertFalse(description_is_error, description_result)
        self.assertEqual(self.state.board_tasks[task.id].labels, ["x" * 51])
        self.assertEqual(self.state.board_tasks[task.id].description, "Still editable")

        repaired, repaired_is_error = await self._call(
            "architect_task_update",
            {"task": task.id, "labels": ["short"]},
            architect.id,
        )
        self.assertFalse(repaired_is_error, repaired)
        self.assertEqual(self.state.board_tasks[task.id].labels, ["short"])

    async def test_architect_task_update_allows_long_labels_without_active_known_provider_limit(self):
        architect = self._add_architect("arch-no-label-limit", "Architect")
        task = self._add_task(
            "task-no-label-limit",
            "Original title",
            created_by_architect_id=architect.id,
        )
        for provider, enabled in (("github", False), ("none", True), ("unknown", True)):
            with self.subTest(provider=provider, enabled=enabled):
                self.state.update_group_settings(
                    "torque",
                    board_sync_provider=provider,
                    board_sync_enabled=enabled,
                )
                text, is_error = await self._call(
                    "architect_task_update",
                    {"task": task.id, "labels": ["x" * 51]},
                    architect.id,
                )
                self.assertFalse(is_error, text)
                self.assertEqual(self.state.board_tasks[task.id].labels, ["x" * 51])

    async def test_architect_task_update_refuses_only_while_dispatch_stream_is_active(self):
        creator = self._add_architect("arch-creator", "Creator")
        execution_architect = self._add_architect("arch-execution", "Executor")
        engineer = self._add_engineer(
            "eng-assigned", "Assigned Engineer",
            hired_by_architect_id=execution_architect.id,
        )
        worker = self._add_worker("worker-dispatched", "Dispatched Worker", engineer.id)
        worker.status = "running"
        self.state._db_save_agent(worker)
        assigned_undispatched = self._add_task(
            "task-assigned-undispatched-update",
            "Assigned but staged",
            description="This has not been dispatched.",
            assigned_engineer_id=engineer.id,
            assigned_architect_id=execution_architect.id,
            agent_id="worker-already-assigned",
            lane="In Progress",
            dispatch_state="queued",
            created_by_architect_id=creator.id,
        )

        queued_text, queued_error = await self._call(
            "architect_task_update",
            {"task": assigned_undispatched.id, "title": "Still amendable"},
            creator.id,
        )
        self.assertFalse(queued_error, queued_text)
        self.assertEqual(
            self.state.board_tasks[assigned_undispatched.id].task,
            "Still amendable",
        )

        dispatched = self._add_task(
            "task-dispatched-update",
            "Original title",
            description="Original description",
            labels=["original", "keep"],
            action_name="feature/implement",
            action_vars={"original": "value"},
            assigned_engineer_id=engineer.id,
            assigned_architect_id=execution_architect.id,
            agent_id=worker.id,
            lane="In Progress",
            dispatch_state="live",
            created_by_architect_id=creator.id,
        )
        dispatched.updated_at = "2000-01-01T00:00:00+00:00"
        self.state._db_save_task(dispatched)
        before_refusal = {
            "task": dispatched.task,
            "description": dispatched.description,
            "labels": list(dispatched.labels),
            "action_name": dispatched.action_name,
            "action_vars": dict(dispatched.action_vars),
            "required_review_gates": list(dispatched.required_review_gates),
            "updated_at": dispatched.updated_at,
        }
        rejected_patch = {
            "task": dispatched.id,
            "title": "Amended title",
            "description": "Amended description",
            "labels": ["amended"],
            "action_name": "oneshot/fix",
            "action_vars": {"amended": "value"},
            "required_review_gates": [
                {"id": "review-one"}, {"id": "review-two"},
            ],
        }

        for caller_id in (creator.id, execution_architect.id):
            with self.subTest(caller_id=caller_id):
                text, is_error = await self._call(
                    "architect_task_update",
                    rejected_patch,
                    caller_id,
                )
                self.assertTrue(is_error)
                error = json.loads(text)
                self.assertEqual(error["reason"], "task_dispatched")
                self.assertEqual(error["dispatch_state"], "live")
                self.assertEqual(
                    error["message"],
                    "Task has active dispatched work. Stop the active execution "
                    "stream before editing it.",
                )

        # The refusal returned before validating or forwarding the patch, so
        # both in-memory and persisted task fields (including its update
        # version/timestamp) remain exactly as they were.
        self.assertEqual(self.handle_calls, [
            {
                "cmd": "board_update_task",
                "id": assigned_undispatched.id,
                "task": "Still amendable",
            },
        ])
        refused = self.state.board_tasks[dispatched.id]
        self.assertEqual(refused.task, before_refusal["task"])
        self.assertEqual(refused.description, before_refusal["description"])
        self.assertEqual(refused.labels, before_refusal["labels"])
        self.assertEqual(refused.action_name, before_refusal["action_name"])
        self.assertEqual(refused.action_vars, before_refusal["action_vars"])
        self.assertEqual(
            refused.required_review_gates,
            before_refusal["required_review_gates"],
        )
        self.assertEqual(refused.updated_at, before_refusal["updated_at"])
        persisted_refused = self.db.load_all()["board_tasks"][dispatched.id]
        for field in ("task", "description", "labels", "action_name", "action_vars", "required_review_gates", "updated_at"):
            with self.subTest(persisted_field=field):
                self.assertEqual(persisted_refused[field], before_refusal[field])

        # A stopped worker leaves durable dispatch history intact, but no work
        # is in flight, so a correction must be allowed without a fake relane.
        worker.status = "stopped"
        self.state._db_save_agent(worker)

        resumed_text, resumed_error = await self._call(
            "architect_task_update",
            rejected_patch,
            execution_architect.id,
        )
        self.assertFalse(resumed_error, resumed_text)
        resumed = self.state.board_tasks[dispatched.id]
        self.assertEqual(resumed.dispatch_state, "live")
        self.assertEqual(resumed.task, "Amended title")
        self.assertEqual(resumed.description, "Amended description")
        self.assertEqual(resumed.labels, ["amended"])
        self.assertEqual(resumed.action_name, "oneshot/fix")
        self.assertEqual(resumed.action_vars, {"amended": "value"})
        self.assertEqual(
            resumed.required_review_gates,
            [{"id": "review-one", "role": ""}, {"id": "review-two", "role": ""}],
        )

    async def test_architect_task_update_sets_action_fields_and_rejects_unknown(self):
        architect = self._add_architect("arch-1", "Architect")
        task = self._add_task(
            "task-update-action",
            "Action target",
            action_name="feature/implement",
            action_vars={"old": "value", "drop": "me"},
            suggested_action="feature/implement",
            created_by_architect_id=architect.id,
        )

        text, is_error = await self._call(
            "architect_task_update",
            {
                "task": task.id,
                "action_name": "oneshot/fix",
                "action_vars": {"mode": "focused"},
                "suggested_action": "oneshot/fix",
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(
            json.loads(text)["updated_fields"],
            ["action_name", "action_vars", "suggested_action"],
        )
        updated = self.state.board_tasks[task.id]
        self.assertEqual(updated.action_name, "oneshot/fix")
        self.assertEqual(updated.action_vars, {"mode": "focused"})
        self.assertEqual(updated.suggested_action, "oneshot/fix")
        persisted = self.db.load_all()["board_tasks"][task.id]
        self.assertEqual(persisted["action_name"], "oneshot/fix")
        self.assertEqual(persisted["action_vars"], {"mode": "focused"})
        self.assertEqual(persisted["suggested_action"], "oneshot/fix")

        bad_text, bad_error = await self._call(
            "architect_task_update",
            {"task": task.id, "action_name": "bogus/nonexistent-action"},
            architect.id,
        )

        self.assertTrue(bad_error)
        self.assertIn("Unknown action_name 'bogus/nonexistent-action'", bad_text)
        self.assertIn("ActionManager.list_actions()", bad_text)
        self.assertEqual(self.state.board_tasks[task.id].action_name, "oneshot/fix")
        self.assertEqual(self.state.board_tasks[task.id].action_vars, {"mode": "focused"})

        bad_hint_text, bad_hint_error = await self._call(
            "architect_task_update",
            {"task": task.id, "suggested_action": "bogus/nonexistent-action"},
            architect.id,
        )

        self.assertTrue(bad_hint_error)
        self.assertIn("Unknown suggested_action 'bogus/nonexistent-action'", bad_hint_text)
        self.assertIn("ActionManager.list_actions()", bad_hint_text)
        self.assertEqual(self.state.board_tasks[task.id].suggested_action, "oneshot/fix")

    async def test_architect_task_update_enforces_creator_and_group_scope(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        engineer = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        own_task = self._add_task(
            "task-own",
            "Own task",
            created_by_architect_id=architect.id,
        )
        user_task = self._add_task("task-user-update", "User task")
        engineer_task = self._add_task(
            "task-engineer-update",
            "Engineer task",
            created_by_engineer_id=engineer.id,
        )
        system_task = self._add_task(
            "task-system-update",
            "System task",
            parent_task_id=own_task.id,
            pipeline_root_id=own_task.id,
        )
        other_arch_task = self._add_task(
            "task-other-architect-update",
            "Other architect task",
            created_by_architect_id=other_architect.id,
        )
        self.state.groups["other"] = []
        self.state._db_save_groups()
        cross_group = self._add_task(
            "task-cross-group-update",
            "Cross-group task",
            group="other",
            created_by_architect_id=architect.id,
        )

        for task in (engineer_task, system_task, other_arch_task):
            with self.subTest(task=task.id):
                text, is_error = await self._call(
                    "architect_task_update",
                    {
                        "task": task.id,
                        "title": "Should not update",
                        "action_name": "oneshot/fix",
                        "suggested_action": "oneshot/fix",
                    },
                    architect.id,
                )
                self.assertTrue(is_error)
                expected = (
                    "Task has engineer provenance; claim it with task_claim before "
                    "action-binding, reassignment, or dispatch. Only the Architect "
                    "who hired its filing Engineer may claim it."
                    if task.id == engineer_task.id
                    else "Task was not created by this architect"
                )
                self.assertEqual(text, expected)
                self.assertNotEqual(
                    self.state.board_tasks[task.id].task,
                    "Should not update",
                )
                self.assertEqual(self.state.board_tasks[task.id].action_name, "")
                self.assertEqual(self.state.board_tasks[task.id].suggested_action, "")

        user_text, user_error = await self._call(
            "architect_task_update",
            {
                "task": user_task.id,
                "title": "Architect-edited user task",
                "description": "Filled in by architect",
                "labels": ["bug", "post-wave-12"],
            },
            architect.id,
        )
        self.assertFalse(user_error, user_text)
        self.assertEqual(
            self.state.board_tasks[user_task.id].task,
            "Architect-edited user task",
        )
        self.assertEqual(
            self.state.board_tasks[user_task.id].description,
            "Filled in by architect",
        )
        self.assertEqual(
            self.state.board_tasks[user_task.id].labels,
            ["bug", "post-wave-12"],
        )

        cross_text, cross_error = await self._call(
            "architect_task_update",
            {"task": cross_group.id, "title": "Should not update"},
            architect.id,
        )
        self.assertTrue(cross_error)
        self.assertEqual(cross_text, "Task not found")
        self.assertEqual(
            self.state.board_tasks[cross_group.id].task,
            "Cross-group task",
        )

    async def test_architect_task_update_rejects_empty_fields_and_bad_labels(self):
        architect = self._add_architect("arch-1", "Architect")
        task = self._add_task(
            "task-update-invalid",
            "Original",
            description="Original description",
            labels=["keep"],
            created_by_architect_id=architect.id,
        )

        for args, expected in (
            ({"title": ""}, "title is required"),
            ({"description": "   "}, "description is required"),
            ({"labels": "triage"}, "labels must be a list"),
            ({"action_vars": "mode=fast"}, "action_vars must be an object"),
            ({}, "At least one editable field is required"),
        ):
            with self.subTest(args=args):
                text, is_error = await self._call(
                    "architect_task_update",
                    {"task": task.id, **args},
                    architect.id,
                )
                self.assertTrue(is_error)
                self.assertEqual(text, expected)

        self.assertEqual(self.state.board_tasks[task.id].task, "Original")
        self.assertEqual(
            self.state.board_tasks[task.id].description,
            "Original description",
        )
        self.assertEqual(self.state.board_tasks[task.id].labels, ["keep"])

    async def test_architect_ask_creates_visible_human_attention_task(self):
        architect = self._add_architect("arch-1", "Architect")
        self.state.get_group_settings("torque").board_default_action = "feature/implement"

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
        self.assertIn("torque:human", task.labels)
        self.assertIn("architect-ask", task.labels)
        self.assertEqual(task.action_name, "")
        self.assertEqual(task.created_by_architect_id, architect.id)
        self.assertEqual(task.reply_agent_id, architect.id)
        self.assertEqual(task.assigned_engineer_id, "")
        self.assertEqual(task.agent_id, "")
        direct_rows = self.db.load_direct_messages_for_agent(architect.id)
        ask_rows = [
            row for row in direct_rows
            if row["source_task_id"] == task.id and row["message_type"] == "ask"
        ]
        self.assertEqual(len(ask_rows), 1)
        self.assertEqual(ask_rows[0]["sender_id"], architect.id)
        self.assertEqual(ask_rows[0]["sender_kind"], "architect")
        self.assertEqual(ask_rows[0]["recipient_id"], "user")
        self.assertEqual(ask_rows[0]["recipient_kind"], "user")
        self.assertTrue(ask_rows[0]["blocking"])
        self.assertEqual(
            self.state.direct_messages_by_agent[architect.id][0]["id"],
            ask_rows[0]["id"],
        )
        self.assertEqual(architect.mcp_messages, [])

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

    async def test_task_reassign_transfers_linked_worker_and_preserves_scope(self):
        architect = self._add_architect("arch-1", "Architect")
        previous_owner = self._add_engineer(
            "eng-previous", "Previous", hired_by_architect_id=architect.id
        )
        receiving_owner = self._add_engineer(
            "eng-receiving", "Receiving", hired_by_architect_id=architect.id
        )
        unrelated = self._add_engineer("eng-unrelated", "Unrelated")
        worker = self._add_worker("worker-1", "Worker", previous_owner.id)
        task = self._add_task(
            "task-transfer",
            "Transfer a live worker",
            lane="In Progress",
            assigned_engineer_id=previous_owner.id,
            created_by_architect_id=architect.id,
            agent_id=worker.id,
        )

        # Verbatim receiving-seat before state: the worker is outside scope.
        before_text, before_error = await self._call_engineer(
            "engineer_agent_show", {"agent": worker.id}, receiving_owner.id
        )
        self.assertTrue(before_error)
        self.assertEqual(before_text, "agent not found in scope")

        reassign_text, reassign_error = await self._call(
            "architect_task_reassign",
            {"task": task.id, "new_engineer_id": receiving_owner.id},
            architect.id,
        )
        self.assertFalse(reassign_error, reassign_text)
        reassign = json.loads(reassign_text)
        self.assertEqual(reassign["assigned_engineer_id"], receiving_owner.id)
        self.assertEqual(reassign["worker_transfer"], {
            "status": "transferred",
            "transferred_count": 1,
            "worker_ids": [worker.id],
        })
        self.assertEqual(reassign["orphaned_task_worker_count"], 0)
        self.assertEqual(worker.owner_engineer_id, receiving_owner.id)
        # Creation provenance remains intact; explicit ownership controls scope.
        self.assertEqual(worker.created_by_engineer_id, previous_owner.id)
        await self.state.flush_db_writes()
        persisted = self.db._conn.execute(
            "SELECT t.assigned_engineer_id, a.owner_engineer_id "
            "FROM board_tasks t JOIN agents a ON a.id=t.agent_id "
            "WHERE t.id=?",
            (task.id,),
        ).fetchone()
        self.assertEqual(persisted, (receiving_owner.id, receiving_owner.id))

        # Verbatim receiving-seat after state: the worker is now accessible.
        after_text, after_error = await self._call_engineer(
            "engineer_agent_show", {"agent": worker.id}, receiving_owner.id
        )
        self.assertFalse(after_error, after_text)
        self.assertEqual(json.loads(after_text)["id"], worker.id)

        previous_text, previous_error = await self._call_engineer(
            "engineer_agent_show", {"agent": worker.id}, previous_owner.id
        )
        unrelated_text, unrelated_error = await self._call_engineer(
            "engineer_agent_show", {"agent": worker.id}, unrelated.id
        )
        self.assertTrue(previous_error)
        self.assertEqual(previous_text, "agent not found in scope")
        self.assertTrue(unrelated_error)
        self.assertEqual(unrelated_text, "agent not found in scope")

        # Unrelated task mutations never move worker ownership.
        self.state.board_update_task(task.id, lane="Done")
        self.assertEqual(worker.owner_engineer_id, receiving_owner.id)

    async def test_task_reassign_without_live_workers_reports_warning_and_orphans(self):
        architect = self._add_architect("arch-1", "Architect")
        previous_owner = self._add_engineer(
            "eng-previous", "Previous", hired_by_architect_id=architect.id
        )
        receiving_owner = self._add_engineer(
            "eng-receiving", "Receiving", hired_by_architect_id=architect.id
        )
        task = self._add_task(
            "task-no-worker",
            "Reassign without a worker",
            assigned_engineer_id=previous_owner.id,
            created_by_architect_id=architect.id,
        )

        text, error = await self._call(
            "architect_task_reassign",
            {"task": task.id, "new_engineer_id": receiving_owner.id},
            architect.id,
        )
        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["worker_transfer"], {
            "status": "not_applicable",
            "transferred_count": 0,
            "worker_ids": [],
            "warning": (
                "No live workers are linked to this task; no worker "
                "ownership changed."
            ),
        })
        self.assertEqual(payload["orphaned_task_worker_count"], 0)

    async def test_task_reassign_persistence_failure_leaves_memory_unchanged(self):
        architect = self._add_architect("arch-1", "Architect")
        previous_owner = self._add_engineer(
            "eng-previous", "Previous", hired_by_architect_id=architect.id
        )
        receiving_owner = self._add_engineer(
            "eng-receiving", "Receiving", hired_by_architect_id=architect.id
        )
        worker = self._add_worker("worker-1", "Worker", previous_owner.id)
        task = self._add_task(
            "task-transfer-failure",
            "Do not project a failed transfer",
            lane="In Progress",
            assigned_engineer_id=previous_owner.id,
            created_by_architect_id=architect.id,
            agent_id=worker.id,
        )

        # Inject the failure after SQLite stages the worker row and before it
        # writes the task row.  The transaction must roll back both durable
        # rows, and the dispatcher must return before it changes memory.
        db_type = type(self.db)
        original_insert_agent = db_type._insert_agent_row
        staged_worker_ids = []

        def record_worker_stage(db, executor, cell):
            if getattr(cell, "id", "") == worker.id:
                staged_worker_ids.append(worker.id)
            return original_insert_agent(db, executor, cell)

        def fail_task_stage(_db, _executor, _task):
            raise RuntimeError("injected task-row failure")

        with mock.patch.object(
                db_type, "_insert_agent_row", new=record_worker_stage
        ), mock.patch.object(
                db_type, "_insert_board_task_row", new=fail_task_stage
        ):
            text, error = await self._call(
                "architect_task_reassign",
                {"task": task.id, "new_engineer_id": receiving_owner.id},
                architect.id,
            )

        self.assertTrue(error)
        self.assertEqual(staged_worker_ids, [worker.id])
        self.assertEqual(
            text,
            "Failed to persist task and worker ownership transfer",
        )
        self.assertEqual(task.assigned_engineer_id, previous_owner.id)
        self.assertEqual(worker.owner_engineer_id, previous_owner.id)
        persisted = self.db._conn.execute(
            "SELECT t.assigned_engineer_id, a.owner_engineer_id "
            "FROM board_tasks t JOIN agents a ON a.id=t.agent_id "
            "WHERE t.id=?",
            (task.id,),
        ).fetchone()
        self.assertEqual(persisted, (previous_owner.id, previous_owner.id))

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
                "group": "torque",
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
                "group": "torque",
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
                "group": "torque",
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

    async def test_dismissed_architect_mcp_allows_reads_and_rejects_mutations(self):
        architect = self._add_architect("arch-1", "Architect")
        architect.dismissed_at = 123
        engineer = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        self._add_task(
            "task-visible",
            "Existing work",
            assigned_engineer_id=engineer.id,
            created_by_architect_id=architect.id,
        )
        decision = self.state.save_decision({
            "id": "decision-visible",
            "architect_id": architect.id,
            "title": "Keep durable decisions readable",
            "rationale": "Dismiss is a pause, not an archive.",
            "status": "accepted",
        })
        self.assertIsNotNone(decision)

        read_text, read_error = await self._call(
            "architect_task_list",
            {},
            architect.id,
        )
        self.assertFalse(read_error)
        self.assertIn("task-visible", read_text)

        decision_text, decision_error = await self._call(
            "architect_decision_list",
            {},
            architect.id,
        )
        self.assertFalse(decision_error, decision_text)
        decision_payload = json.loads(decision_text)
        self.assertEqual(
            [item["id"] for item in decision_payload["decisions"]],
            ["decision-visible"],
        )

        create_text, create_error = await self._call(
            "architect_task_create",
            {
                "title": "Blocked mutation",
                "group": "torque",
                "assigned_engineer_id": engineer.id,
            },
            architect.id,
        )
        self.assertTrue(create_error)
        create_payload = json.loads(create_text)
        self.assertEqual(create_payload["reason"], "architect_dismissed")
        self.assertEqual(create_payload["architect_id"], architect.id)
        self.assertEqual(len(self.handle_calls), 0)

    async def test_architect_task_move_uses_group_scope_and_can_clear_status(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        task = self._add_task(
            "task-move",
            "Cleanup lane drift",
            lane="Backlog",
            status="Fixing",
            created_by_architect_id=other_architect.id,
        )

        preserve_text, preserve_error = await self._call(
            "architect_task_move",
            {"task": task.id, "new_lane": "To Do"},
            architect.id,
        )

        self.assertFalse(preserve_error, preserve_text)
        preserve_payload = json.loads(preserve_text)
        self.assertEqual(
            preserve_payload,
            {
                "type": "task_moved",
                "task_id": task.id,
                "previous_lane": "Backlog",
                "new_lane": "To Do",
                "status": "Fixing",
            },
        )
        self.assertEqual(self.state.board_tasks[task.id].lane, "To Do")
        self.assertEqual(self.state.board_tasks[task.id].status, "Fixing")

        with mock.patch.object(self.state, "_emit", wraps=self.state._emit) as emit_mock:
            clear_text, clear_error = await self._call(
                "architect_task_move",
                {"task": task.id, "new_lane": "Done", "clear_status": True},
                architect.id,
            )

        self.assertFalse(clear_error, clear_text)
        clear_payload = json.loads(clear_text)
        self.assertEqual(
            clear_payload,
            {
                "type": "task_moved",
                "task_id": task.id,
                "previous_lane": "To Do",
                "new_lane": "Done",
                "status": "",
            },
        )
        moved = self.state.board_tasks[task.id]
        self.assertEqual(moved.lane, "Done")
        self.assertEqual(moved.status, "")

        db_task = self.db.load_all()["board_tasks"][task.id]
        self.assertEqual(db_task["lane"], "Done")
        self.assertEqual(db_task["status"], "")

        task_upserts = [
            call
            for call in emit_mock.call_args_list
            if call.args and call.args[0] == "task_upsert"
        ]
        self.assertTrue(task_upserts)
        self.assertTrue(any(
            call.kwargs.get("id") == task.id
            and call.kwargs.get("lane") == "Done"
            and call.kwargs.get("status") == ""
            for call in task_upserts
        ))

    async def test_architect_task_move_rejects_unknown_lane(self):
        architect = self._add_architect("arch-1", "Architect")
        task = self._add_task(
            "task-move",
            "Cleanup lane drift",
            lane="To Do",
            status="Fixing",
            created_by_architect_id=architect.id,
        )

        text, is_error = await self._call(
            "architect_task_move",
            {"task": task.id, "new_lane": "No Such Lane"},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Unknown lane: No Such Lane")
        self.assertEqual(self.state.board_tasks[task.id].lane, "To Do")
        self.assertEqual(self.state.board_tasks[task.id].status, "Fixing")

    async def test_architect_task_move_rejects_out_of_group_task(self):
        architect = self._add_architect("arch-1", "Architect")
        self.state.groups["other"] = []
        self.state._db_save_groups()
        hidden_task = self.state.board_add_task(
            "Other group task",
            "other",
            lane="Backlog",
            id="task-other-group",
        )
        self.assertIsNotNone(hidden_task)

        text, is_error = await self._call(
            "architect_task_move",
            {"task": hidden_task.id, "new_lane": "Done"},
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task not found")
        self.assertEqual(self.state.board_tasks[hidden_task.id].lane, "Backlog")

    async def test_architect_task_mark_covered_records_evidence_and_done(self):
        architect = self._add_architect("arch-1", "Architect")
        covered = self._add_task(
            "TORQUE:833",
            "Triage card covered elsewhere",
            lane="To Do",
            status="Needs triage",
            created_by_architect_id=architect.id,
        )
        covering = self._add_task(
            "TORQUE:855",
            "Implementation task",
            lane="In Progress",
            created_by_architect_id=architect.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {
                "task": covered.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/999",
                "sha": "abc1234",
                "notes": "Covered by implementation.",
                "move_to_done": True,
            },
            architect.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "task_marked_covered")
        refreshed = self.state.board_tasks[covered.id]
        self.assertEqual(refreshed.lane, "Done")
        self.assertEqual(refreshed.status, "")
        self.assertEqual(refreshed.completion_evidence["sources"], ["covered_by"])
        self.assertEqual(refreshed.completion_evidence["covered_by"]["task_id"], covering.id)
        self.assertEqual(refreshed.completion_evidence["covered_by"]["pr_url"], "https://github.com/runtorque/torque/pull/999")
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by")

        db_task = self.db.load_all()["board_tasks"][covered.id]
        self.assertEqual(db_task["lane"], "Done")
        self.assertEqual(
            db_task["completion_evidence"]["covered_by"]["sha"],
            "abc1234",
        )

    async def test_architect_task_mark_covered_rejects_other_architect_task(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        task = self._add_task(
            "TORQUE:834",
            "Other architect card",
            created_by_architect_id=other_architect.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {
                "task": task.id,
                "pr_url": "https://github.com/runtorque/torque/pull/1",
            },
            architect.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task was not created by this architect")
        self.assertEqual(self.state.board_tasks[task.id].completion_evidence, {})
        self.assertEqual(self.state.board_tasks[task.id].lane, "Backlog")

    async def test_architect_task_mark_covered_allows_routed_proposal_root(self):
        pm = self._add_architect("pm-1", "Blueprint")
        torqly = self._add_architect("arch-1", "Torqly")
        root = self._add_task(
            "TORQUE:991",
            "product proposal root",
            lane="In Progress",
            status="Covered elsewhere",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        self.db.save_agent_peer_message({
            "id": "route-msg-991",
            "thread_id": "route-thread-991",
            "group_name": "torque",
            "sender_id": pm.id,
            "sender_kind": "architect",
            "recipient_id": torqly.id,
            "recipient_kind": "architect",
            "message": "Torqly, please route and manage this product root.",
            "created_at": 1.0,
            "context_task_ids": [root.id],
            "context_summary": "Explicit route request for TORQUE:991.",
            "context_snapshot": {
                "proposal_peer": {"marker": "torque.proposal_peer.v1"},
            },
        })
        covering = self._add_task(
            "TORQUE:1200",
            "Covering implementation",
            lane="Done",
            labels=["covers:TORQUE:991"],
            created_by_architect_id=torqly.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/991",
                "sha": "deadbeef",
                "tests_run": "make test",
                "notes": "Covered by routed implementation stream.",
                "move_to_done": True,
            },
            torqly.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "task_marked_covered")
        refreshed = self.state.board_tasks[root.id]
        self.assertEqual(refreshed.lane, "Done")
        evidence = refreshed.completion_evidence["covered_by"]
        self.assertEqual(evidence["task_id"], covering.id)
        self.assertEqual(evidence["pr_url"], "https://github.com/runtorque/torque/pull/991")
        self.assertEqual(evidence["sha"], "deadbeef")
        self.assertEqual(evidence["tests_run"], "make test")
        self.assertEqual(evidence["recorded_by_id"], torqly.id)
        self.assertEqual(evidence["recorded_by_kind"], "architect")
        authorization = evidence["authorization"]
        self.assertEqual(authorization["scope"], "routed_product_proposal_root")
        self.assertEqual(
            authorization["source"],
            "covering_task_label_and_proposal_peer",
        )
        self.assertEqual(authorization["root_creator_architect_id"], pm.id)
        self.assertEqual(authorization["route_message_id"], "route-msg-991")
        self.assertIn("proposal-only", authorization["product_labels"])
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by")

    async def test_architect_task_mark_covered_live_handler_accepts_covering_task(self):
        pm = self._add_architect("pm-live", "Blueprint")
        torqly = self._add_architect("arch-live", "Torqly")
        root = self._add_task(
            "TORQUE:1036",
            "product proposal root",
            lane="Backlog",
            status="queued",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        self.db.save_agent_peer_message({
            "id": "route-msg-1036",
            "thread_id": "route-thread-1036",
            "group_name": "torque",
            "sender_id": pm.id,
            "sender_kind": "architect",
            "recipient_id": torqly.id,
            "recipient_kind": "architect",
            "message": "Torqly, please accept and route this product proposal root.",
            "created_at": 1.0,
            "context_task_ids": [root.id],
            "context_summary": "Explicit PM route request for TORQUE:1036.",
            "context_snapshot": {
                "proposal_peer": {"marker": "torque.proposal_peer.v1"},
            },
        })
        covering = self._add_task(
            "TORQUE:1037",
            "Torqly covering implementation",
            lane="In Progress",
            labels=["covers:TORQUE:1036"],
            created_by_architect_id=torqly.id,
        )

        text, is_error = await self._call_with_server_handle(
            "architect_task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/836",
                "sha": "1036feed",
                "tests_run": "live validation",
                "notes": "One-root validation: TORQUE:1037 covers TORQUE:1036.",
                "move_to_done": False,
            },
            torqly.id,
        )

        self.assertFalse(is_error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "task_marked_covered")
        refreshed = self.state.board_tasks[root.id]
        self.assertEqual(refreshed.lane, "Backlog")
        evidence = refreshed.completion_evidence["covered_by"]
        self.assertEqual(evidence["task_id"], covering.id)
        self.assertEqual(evidence["authorization"]["scope"], "routed_product_proposal_root")
        self.assertEqual(evidence["authorization"]["route_message_id"], "route-msg-1036")

    async def test_architect_task_mark_covered_live_handler_accepts_covering_task_id_alias(self):
        pm = self._add_architect("pm-alias", "Blueprint")
        torqly = self._add_architect("arch-alias", "Torqly")
        root = self._add_task(
            "TORQUE:1046",
            "product-proposal alias product root",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:1047",
            "Torqly alias covering implementation",
            labels=["covers:TORQUE:1046"],
            created_by_architect_id=torqly.id,
        )

        text, is_error = await self._call_with_server_handle(
            "architect_task_mark_covered",
            {
                "task": root.id,
                "covering_task_id": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/1047",
                "notes": "Explicit cover-label evidence is present.",
            },
            torqly.id,
        )

        self.assertFalse(is_error, text)
        evidence = self.state.board_tasks[root.id].completion_evidence["covered_by"]
        self.assertEqual(evidence["task_id"], covering.id)
        self.assertEqual(
            evidence["authorization"]["source"],
            "covering_task_label",
        )

    async def test_architect_task_mark_covered_rejects_routed_proposal_root_without_completion_evidence(self):
        pm = self._add_architect("pm-no-evidence", "Blueprint")
        torqly = self._add_architect("arch-no-evidence", "Torqly")
        root = self._add_task(
            "TORQUE:1048",
            "product proposal root without completion evidence",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:1049",
            "Torqly covering task",
            labels=["covers:TORQUE:1048"],
            created_by_architect_id=torqly.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {"task": root.id, "covering_task": covering.id},
            torqly.id,
        )

        self.assertTrue(is_error)
        self.assertIn("requires PR/SHA/tests or notes evidence", text)
        self.assertEqual(self.state.board_tasks[root.id].completion_evidence, {})

    async def test_architect_task_mark_covered_rejects_proposal_root_with_non_caller_covering_task(self):
        pm = self._add_architect("pm-non-caller", "Blueprint")
        torqly = self._add_architect("arch-non-caller", "Torqly")
        other_architect = self._add_architect("arch-cover-owner", "Other Architect")
        root = self._add_task(
            "TORQUE:1050",
            "product proposal root with other covering owner",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:1051",
            "Other architect covering task",
            labels=["covers:TORQUE:1050"],
            created_by_architect_id=other_architect.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/1051",
            },
            torqly.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(
            text,
            "Routed product proposal roots require a covering_task created "
            "by this architect",
        )
        self.assertEqual(self.state.board_tasks[root.id].completion_evidence, {})

    async def test_architect_task_mark_covered_rejects_proposal_root_without_route_evidence(self):
        pm = self._add_architect("pm-1", "Blueprint")
        torqly = self._add_architect("arch-1", "Torqly")
        root = self._add_task(
            "TORQUE:997",
            "Unrouted product proposal root",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:1201",
            "Unlabeled implementation",
            created_by_architect_id=torqly.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/997",
            },
            torqly.id,
        )

        self.assertTrue(is_error)
        self.assertIn("covers:<task> label", text)
        self.assertEqual(self.state.board_tasks[root.id].completion_evidence, {})
        self.assertEqual(self.state.board_tasks[root.id].lane, "Backlog")

    async def test_architect_task_mark_covered_rejects_non_proposal_root_even_with_cover_label(self):
        other_architect = self._add_architect("arch-other", "Other Architect")
        torqly = self._add_architect("arch-1", "Torqly")
        root = self._add_task(
            "TORQUE:999",
            "Non-proposal root",
            created_by_architect_id=other_architect.id,
        )
        covering = self._add_task(
            "TORQUE:1202",
            "Labeled implementation",
            labels=["covers:TORQUE:999"],
            created_by_architect_id=torqly.id,
        )

        text, is_error = await self._call(
            "architect_task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "pr_url": "https://github.com/runtorque/torque/pull/999",
            },
            torqly.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task was not created by this architect")
        self.assertEqual(self.state.board_tasks[root.id].completion_evidence, {})

    def _set_final_covering_evidence(self, covering, *,
                                     pr_url="https://github.com/runtorque/torque/pull/1087",
                                     sha="abc1087",
                                     tests_run="make test",
                                     origin_verified=True):
        if tests_run is not None:
            covering.verification_summary = {"tests_run": tests_run}
        merge = {
            "origin_verified": origin_verified,
            "origin_summary": f"origin/main == {sha}" if sha else "",
        }
        if pr_url is not None:
            merge["pr_url"] = pr_url
        if sha is not None:
            merge["sha"] = sha
        covering.completion_evidence = {
            "status": "verified" if origin_verified else "evidence_attached",
            "sources": ["merge"],
            "merge": merge,
        }
        self.state._db_save_task(covering)

    async def test_auto_resolves_routed_proposal_root_after_covering_task_ships(self):
        pm = self._add_architect("pm-auto", "Blueprint")
        torqly = self._add_architect("arch-auto", "Torqly")
        root = self._add_task(
            "TORQUE:1087",
            "product proposal root",
            lane="In Progress",
            status="Covered by Torqly stream",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:1111",
            "Torqly covering implementation",
            lane="Done",
            labels=["covers:TORQUE:1087", "orchestration-core"],
            created_by_architect_id=torqly.id,
        )
        self._set_final_covering_evidence(covering)

        result = self.server_mod._auto_resolve_product_proposal_roots_for_covering_task(
            self.state,
            covering,
        )

        self.assertEqual([item["task_id"] for item in result], [root.id])
        refreshed = self.state.board_tasks[root.id]
        self.assertEqual(refreshed.lane, "Done")
        self.assertEqual(refreshed.status, "")
        covered_by = refreshed.completion_evidence["covered_by"]
        self.assertEqual(covered_by["task_id"], covering.id)
        self.assertEqual(covered_by["pr_url"], "https://github.com/runtorque/torque/pull/1087")
        self.assertEqual(covered_by["sha"], "abc1087")
        self.assertEqual(covered_by["tests_run"], "make test")
        self.assertTrue(covered_by["moved_to_done"])
        self.assertEqual(covered_by["recorded_by"], "Torque")
        self.assertEqual(covered_by["recorded_by_id"], torqly.id)
        self.assertEqual(covered_by["recorded_by_kind"], "system")
        authorization = covered_by["authorization"]
        self.assertEqual(authorization["scope"], "routed_product_proposal_root")
        self.assertEqual(authorization["source"], "covering_task_label")
        self.assertTrue(authorization["auto_resolved"])
        self.assertEqual(
            authorization["auto_resolve_source"],
            "covering_task_final_ship_evidence",
        )
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by")
        db_task = self.db.load_all()["board_tasks"][root.id]
        self.assertEqual(db_task["lane"], "Done")
        self.assertEqual(
            db_task["completion_evidence"]["covered_by"]["task_id"],
            covering.id,
        )

    async def test_auto_resolves_when_evidence_precedes_done_transition(self):
        pm = self._add_architect("pm-order", "Blueprint")
        torqly = self._add_architect("arch-order", "Torqly")
        root = self._add_task(
            "TORQUE:2087",
            "product proposal root with pre-attached evidence",
            lane="In Progress",
            status="Covered by Torqly stream",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:2111",
            "Torqly covering implementation with early evidence",
            lane="In Progress",
            labels=["covers:TORQUE:2087"],
            created_by_architect_id=torqly.id,
        )
        self._set_final_covering_evidence(
            covering,
            pr_url="https://github.com/runtorque/torque/pull/2111",
            sha="2111feed",
            tests_run="python3 -m unittest tests.test_architect_scoping",
        )

        class FakeBoardSyncManager:
            def __init__(self):
                self.calls = []

            def enqueue_for_local_change(self, task_id, *, reason, fields):
                self.calls.append((task_id, reason, tuple(fields)))

        board_sync_manager = FakeBoardSyncManager()

        # This matches the worker done/ready ordering: evidence can be
        # snapshotted while the covering task is not Done yet, so the
        # pre-Done auto-resolve attempt must not be the only chance.
        changed = self.server_mod._record_task_completion_evidence_snapshot(
            self.state,
            covering,
            action="done",
            message="Implemented and tested.",
            board_sync_manager=board_sync_manager,
        )
        self.assertTrue(changed)
        self.assertEqual(self.state.board_tasks[root.id].lane, "In Progress")
        self.assertEqual(self.state.board_tasks[root.id].completion_evidence, {})
        self.assertEqual(board_sync_manager.calls, [])

        self.state.board_move_task(covering.id, "Done")
        resolved = self.server_mod._auto_resolve_product_proposal_roots_and_enqueue(
            self.state,
            covering,
            board_sync_manager=board_sync_manager,
        )

        self.assertEqual([item["task_id"] for item in resolved], [root.id])
        refreshed = self.state.board_tasks[root.id]
        self.assertEqual(refreshed.lane, "Done")
        covered_by = refreshed.completion_evidence["covered_by"]
        self.assertEqual(covered_by["task_id"], covering.id)
        self.assertEqual(covered_by["sha"], "2111feed")
        self.assertEqual(
            covered_by["tests_run"],
            "python3 -m unittest tests.test_architect_scoping",
        )
        self.assertTrue(covered_by["authorization"]["auto_resolved"])
        self.assertEqual(
            board_sync_manager.calls,
            [(
                root.id,
                "auto_proposal_root_covered",
                ("completion_evidence", "messages", "lane"),
            )],
        )

    async def test_auto_resolve_requires_done_pr_sha_origin_and_tests(self):
        pm = self._add_architect("pm-required", "Blueprint")
        torqly = self._add_architect("arch-required", "Torqly")

        cases = [
            ("not-done", {"lane": "In Progress"}, {}),
            ("missing-pr", {"lane": "Done"}, {"pr_url": None}),
            ("missing-sha", {"lane": "Done"}, {"sha": None}),
            ("missing-tests", {"lane": "Done"}, {"tests_run": None}),
            ("pending-origin", {"lane": "Done"}, {"origin_verified": False}),
        ]
        for suffix, task_kwargs, evidence_kwargs in cases:
            with self.subTest(suffix=suffix):
                root = self._add_task(
                    f"TORQUE:{2000 + len(self.state.board_tasks)}",
                    f"proposal root {suffix}",
                    lane="In Progress",
                    labels=["product-proposal", "proposal-only"],
                    created_by_architect_id=pm.id,
                )
                covering = self._add_task(
                    f"TORQUE:{3000 + len(self.state.board_tasks)}",
                    f"Covering {suffix}",
                    labels=[f"covers:{root.id}"],
                    created_by_architect_id=torqly.id,
                    **task_kwargs,
                )
                self._set_final_covering_evidence(covering, **evidence_kwargs)

                result = self.server_mod._auto_resolve_product_proposal_roots_for_covering_task(
                    self.state,
                    covering,
                )

                self.assertEqual(result, [])
                refreshed = self.state.board_tasks[root.id]
                self.assertEqual(refreshed.lane, "In Progress")
                self.assertEqual(refreshed.completion_evidence, {})

    async def test_auto_resolve_does_not_bulk_close_unlabeled_or_cross_scope_roots(self):
        pm = self._add_architect("pm-bulk", "Blueprint")
        torqly = self._add_architect("arch-bulk", "Torqly")
        self.state.groups["other"] = []
        self.state._db_save_groups()
        covered = self._add_task(
            "TORQUE:2100",
            "Covered proposal root",
            lane="In Progress",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        unlabeled = self._add_task(
            "TORQUE:2101",
            "Unlabeled proposal root",
            lane="In Progress",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        cross_scope_pm = self._add_architect("pm-other", "Other PM", group="other")
        cross_scope = self._add_task(
            "TORQUE:2102",
            "Other-group proposal root",
            group="other",
            lane="In Progress",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=cross_scope_pm.id,
        )
        covering = self._add_task(
            "TORQUE:3100",
            "Single covering implementation",
            lane="Done",
            labels=[f"covers:{covered.id}", f"covers:{cross_scope.id}"],
            created_by_architect_id=torqly.id,
        )
        self._set_final_covering_evidence(covering)

        result = self.server_mod._auto_resolve_product_proposal_roots_for_covering_task(
            self.state,
            covering,
        )

        self.assertEqual([item["task_id"] for item in result], [covered.id])
        self.assertEqual(self.state.board_tasks[covered.id].lane, "Done")
        self.assertEqual(self.state.board_tasks[unlabeled.id].lane, "In Progress")
        self.assertEqual(self.state.board_tasks[unlabeled.id].completion_evidence, {})
        self.assertEqual(self.state.board_tasks[cross_scope.id].lane, "In Progress")
        self.assertEqual(self.state.board_tasks[cross_scope.id].completion_evidence, {})

    async def test_auto_resolve_rejects_authority_mismatch(self):
        pm = self._add_architect("pm-mismatch", "Blueprint")
        root = self._add_task(
            "TORQUE:2200",
            "proposal root with invalid covering owner",
            lane="In Progress",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        covering = self._add_task(
            "TORQUE:3200",
            "Covering task without architect authority",
            lane="Done",
            labels=[f"covers:{root.id}"],
            created_by_architect_id="missing-architect",
        )
        self._set_final_covering_evidence(covering)

        result = self.server_mod._auto_resolve_product_proposal_roots_for_covering_task(
            self.state,
            covering,
        )

        self.assertEqual(result, [])
        self.assertEqual(self.state.board_tasks[root.id].lane, "In Progress")
        self.assertEqual(self.state.board_tasks[root.id].completion_evidence, {})

    async def test_task_coverage_reconcile_distinguishes_unknown_denied_and_unavailable(self):
        """The provisional path is recognized, inert, and not an ACL denial."""
        architect = self._add_architect("arch-reconcile", "Reconcile Architect")
        engineer = self._add_engineer("eng-reconcile", "Reconcile Engineer")
        baseline_task_ids = set(self.state.board_tasks)

        unavailable_text, unavailable_error = await self._call(
            "architect_task_coverage_reconcile",
            {"task_ids": ["TORQUE:not-resolved"], "apply": True},
            architect.id,
        )
        unavailable = json.loads(unavailable_text)
        self.assertTrue(unavailable_error)
        self.assertEqual(unavailable["type"], "tool_unavailable")
        self.assertEqual(unavailable["status"], "recognized_but_not_yet_available")
        self.assertEqual(unavailable["activation_task"], "TORQUE:1228")
        self.assertEqual(
            unavailable["activation_conditions"],
            ["TORQUE:1228 is merged", "the caller session is relaunched"],
        )
        for fragment in (
            "NOT YET AVAILABLE",
            "TORQUE:1228 is merged",
            "caller session is relaunched",
        ):
            self.assertIn(fragment, unavailable["message"])
        # A merge-only promise would leave frozen existing sessions misled.
        self.assertNotIn("TORQUE:1228 will activate", unavailable["message"])
        self.assertEqual(self.handle_calls, [])
        self.assertEqual(set(self.state.board_tasks), baseline_task_ids)

        engineer_text, engineer_error = await self._call_engineer(
            "engineer_task_coverage_reconcile",
            {"task_ids": ["TORQUE:not-resolved"]},
            engineer.id,
        )
        self.assertTrue(engineer_error)
        self.assertEqual(json.loads(engineer_text), unavailable)
        self.assertEqual(self.handle_calls, [])

        # Keep all three outcomes in one matrix. The worker context is not
        # entitled to this recognized operation, while an unregistered name
        # remains genuinely unknown.
        unknown_text, unknown_error = await self._call(
            "architect_not_a_tool", {}, architect.id,
        )
        from torque.mcp_scoped.dispatch_context import ScopedDispatchContext
        from torque.mcp_scoped.dispatch_tasks import dispatch_tasks

        async def unexpected_command(_payload):
            self.fail("recognized unavailable/denied route must not call commands")

        denied_text, denied_error = await dispatch_tasks(ScopedDispatchContext(
            name="worker_task_coverage_reconcile",
            args={"task_ids": ["TORQUE:not-resolved"]},
            handle_command=unexpected_command,
            state=self.state,
            real_state=self.state,
            tool_prefix="worker_",
            caller_kind="worker",
            caller_id="worker-untrusted",
            idempotency_key="",
            caller_cell=None,
            caller_group="torque",
        ))
        outcomes = {
            "unknown": (unknown_text, unknown_error),
            "known_but_unauthorized": (denied_text, denied_error),
            "known_authorized_but_unavailable": (unavailable_text, unavailable_error),
        }
        self.assertTrue(all(is_error for _text, is_error in outcomes.values()))
        self.assertIn("Unknown architect tool", outcomes["unknown"][0])
        self.assertIn("Authorization denied", outcomes["known_but_unauthorized"][0])
        self.assertEqual(
            json.loads(outcomes["known_authorized_but_unavailable"][0])["status"],
            "recognized_but_not_yet_available",
        )
        self.assertEqual(
            len({text for text, _is_error in outcomes.values()}), 3,
        )

    async def test_proposal_root_backlog_hygiene_dry_run_and_apply_preserves_evidence(self):
        pm = self._add_architect("pm-hygiene", "Blueprint")
        torqly = self._add_architect("arch-hygiene", "Torqly")
        eligible_root = self._add_task(
            "TORQUE:3001",
            "Already-covered proposal root",
            lane="Backlog",
            status="Covered by Torqly stream",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        eligible_covering = self._add_task(
            "TORQUE:3002",
            "Final covering implementation",
            lane="Done",
            labels=["covers:TORQUE:3001"],
            created_by_architect_id=torqly.id,
        )
        auth = {
            "scope": "routed_product_proposal_root",
            "source": "covering_task_label_and_proposal_peer",
            "covered_task_id": eligible_root.id,
            "root_creator_architect_id": pm.id,
            "covering_task_id": eligible_covering.id,
            "product_labels": ["product-proposal", "proposal-only"],
            "route_message_id": "msg-route",
            "route_thread_id": "msg-route",
            "route_sender_id": pm.id,
        }
        self.state.board_mark_task_covered(
            eligible_root.id,
            covering_task_id=eligible_covering.id,
            pr_url="https://github.com/runtorque/torque/pull/3002",
            sha="abc3002",
            tests_run="make test",
            notes="Original final evidence notes.",
            actor_name="torqly",
            actor_id=torqly.id,
            actor_kind="architect",
            authorization=auth,
            move_to_done=False,
        )
        original_covered_by = dict(
            self.state.board_tasks[eligible_root.id]
            .completion_evidence["covered_by"]
        )

        pending_root = self._add_task(
            "TORQUE:963",
            "Help popup remains separately audited",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        pending_covering = self._add_task(
            "TORQUE:3010",
            "Pending closure audit",
            lane="To Do",
            labels=["covers:TORQUE:963"],
            created_by_architect_id=torqly.id,
        )
        self.state.board_mark_task_covered(
            pending_root.id,
            covering_task_id=pending_covering.id,
            notes="Accepted/routed; evidence pending.",
            actor_name="torqly",
            actor_id=torqly.id,
            actor_kind="architect",
            authorization={
                "scope": "routed_product_proposal_root",
                "source": "covering_task_label_and_proposal_peer",
                "covered_task_id": pending_root.id,
                "root_creator_architect_id": pm.id,
                "covering_task_id": pending_covering.id,
                "product_labels": ["product-proposal", "proposal-only"],
            },
            move_to_done=False,
        )

        dry_run = self.server_mod._finalize_already_covered_proposal_roots(
            self.state,
            apply=False,
        )

        self.assertNotEqual(dry_run.get("status"), "recognized_but_not_yet_available")
        self.assertEqual(dry_run["inventory_count"], 2)
        self.assertEqual(dry_run["eligible_count"], 1)
        self.assertEqual(dry_run["ineligible_count"], 1)
        self.assertEqual(dry_run["applied_count"], 0)
        reasons = {item["task_id"]: item["reason"] for item in dry_run["items"]}
        self.assertEqual(
            reasons[eligible_root.id],
            "eligible_final_covered_by_evidence",
        )
        self.assertEqual(reasons[pending_root.id], "pending_coverage")
        self.assertEqual(self.state.board_tasks[eligible_root.id].lane, "Backlog")
        self.assertEqual(self.state.board_tasks[pending_root.id].lane, "Backlog")

        class FakeBoardSyncManager:
            def __init__(self):
                self.calls = []

            def enqueue_for_local_change(self, task_id, *, reason, fields):
                self.calls.append((task_id, reason, tuple(fields)))

        sync = FakeBoardSyncManager()
        applied = self.server_mod._finalize_already_covered_proposal_roots(
            self.state,
            apply=True,
            board_sync_manager=sync,
        )

        self.assertEqual(applied["applied_count"], 1)
        refreshed = self.state.board_tasks[eligible_root.id]
        self.assertEqual(refreshed.lane, "Done")
        covered_by = refreshed.completion_evidence["covered_by"]
        for key in (
                "recorded_at", "recorded_by", "recorded_by_id",
                "recorded_by_kind", "task_id", "pr_url", "sha", "tests_run",
                "notes", "authorization"):
            self.assertEqual(covered_by[key], original_covered_by[key])
        self.assertTrue(covered_by["moved_to_done"])
        self.assertEqual(covered_by["finalized_by"], "Torque")
        self.assertEqual(covered_by["finalized_by_id"], torqly.id)
        self.assertEqual(refreshed.messages[-1]["action"], "covered_by_finalized")
        self.assertEqual(self.state.board_tasks[pending_root.id].lane, "Backlog")
        self.assertEqual(
            sync.calls,
            [(
                eligible_root.id,
                "proposal_root_backlog_hygiene_finalized",
                ("completion_evidence", "messages", "lane"),
            )],
        )

    async def test_architect_proposal_root_backlog_hygiene_scopes_to_caller_covering_task(self):
        pm = self._add_architect("pm-hygiene-scope", "Blueprint")
        torqly = self._add_architect("arch-hygiene-scope", "Torqly")
        other_architect = self._add_architect("arch-other-scope", "Other")
        owned_root = self._add_task(
            "TORQUE:3301",
            "Already-covered root for Torqly",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        owned_covering = self._add_task(
            "TORQUE:3302",
            "Torqly covering work",
            lane="Done",
            labels=["covers:TORQUE:3301"],
            created_by_architect_id=torqly.id,
        )
        other_root = self._add_task(
            "TORQUE:3311",
            "Already-covered root for another architect",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )
        other_covering = self._add_task(
            "TORQUE:3312",
            "Other covering work",
            lane="Done",
            labels=["covers:TORQUE:3311"],
            created_by_architect_id=other_architect.id,
        )

        for root, covering, architect in (
                (owned_root, owned_covering, torqly),
                (other_root, other_covering, other_architect)):
            self.state.board_mark_task_covered(
                root.id,
                covering_task_id=covering.id,
                pr_url=f"https://github.com/runtorque/torque/pull/{covering.id[-4:]}",
                sha=f"sha-{covering.id[-4:]}",
                tests_run="make test",
                notes="Durable final evidence.",
                actor_name=architect.name,
                actor_id=architect.id,
                actor_kind="architect",
                authorization={
                    "scope": "routed_product_proposal_root",
                    "source": "covering_task_label_and_proposal_peer",
                    "covered_task_id": root.id,
                    "root_creator_architect_id": pm.id,
                    "covering_task_id": covering.id,
                    "product_labels": ["product-proposal", "proposal-only"],
                },
                move_to_done=False,
            )

        text, is_error = await self._call(
            "architect_proposal_root_backlog_hygiene",
            {"apply": False},
            torqly.id,
        )

        self.assertFalse(is_error, text)
        dry_run = json.loads(text)
        reasons = {item["task_id"]: item["reason"] for item in dry_run["items"]}
        self.assertEqual(dry_run["eligible_count"], 1)
        self.assertEqual(
            reasons[owned_root.id],
            "eligible_final_covered_by_evidence",
        )
        self.assertEqual(
            reasons[other_root.id],
            "covering_task_not_created_by_architect",
        )

        text, is_error = await self._call(
            "architect_proposal_root_backlog_hygiene",
            {"apply": True},
            torqly.id,
        )

        self.assertFalse(is_error, text)
        applied = json.loads(text)
        self.assertEqual(applied["applied_count"], 1)
        self.assertEqual(self.state.board_tasks[owned_root.id].lane, "Done")
        self.assertEqual(self.state.board_tasks[other_root.id].lane, "Backlog")
        self.assertEqual(
            self.state.board_tasks[owned_root.id]
            .completion_evidence["covered_by"]["finalized_by_id"],
            torqly.id,
        )

    async def test_proposal_root_backlog_hygiene_server_rejects_non_architect_actor(self):
        worker = self.state_mod.AgentCell(
            id="worker-hygiene",
            name="Worker",
            slug="worker",
            group="torque",
            cell_type="agent",
            kind="worker",
        )
        self.state.agents[worker.id] = worker
        self.state.groups["torque"].append(worker.id)

        result = await self._extract_server_handle_command()({
            "cmd": "architect_proposal_root_backlog_hygiene",
            "architect_id": worker.id,
            "apply": True,
        })

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["message"], "architect not found")

    async def test_architect_routed_proposal_root_does_not_allow_reassign(self):
        pm = self._add_architect("pm-1", "Blueprint")
        torqly = self._add_architect("arch-1", "Torqly")
        engineer = self._add_engineer(
            "eng-1",
            "Torqly Engineer",
            hired_by_architect_id=torqly.id,
        )
        root = self._add_task(
            "TORQUE:1001",
            "product proposal root",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=pm.id,
        )

        text, is_error = await self._call(
            "architect_task_reassign",
            {"task": root.id, "new_engineer_id": engineer.id},
            torqly.id,
        )

        self.assertTrue(is_error)
        self.assertEqual(text, "Task was not created by this architect")
        self.assertEqual(self.state.board_tasks[root.id].assigned_engineer_id, "")

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
        self.assertTrue(hired.pending_engineer_message)
        self.assertEqual(
            [op["op"] for op in self.state._delta_ops[-4:]],
            [
                "agent_peer_thread_upsert",
                "agent_upsert",
                "agent_upsert",
                "agent_peer_thread_upsert",
            ],
        )
        self.assertIsNotNone(self.db.load_agent_peer_message(delivered["message_id"]))

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
        self.assertFalse(hired.pending_engineer_message)

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
            "architect_engineer_reply",
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

    async def test_architect_peer_tools_happy_path_context_inbox_and_reply(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-2", "Peer")
        peer.session_id = "peer-session"
        hired = self._add_engineer(
            "eng-hired", "Hired", hired_by_architect_id=architect.id
        )
        task = self._add_task(
            "task-peer-1",
            "Peer context task",
            assigned_engineer_id=hired.id,
            created_by_architect_id=architect.id,
        )
        decision = self.db.save_decision({
            "id": "decision-1",
            "architect_id": architect.id,
            "title": "Use direct peer messages",
            "rationale": "Keeps coordination off the board",
            "status": "accepted",
            "linked_task_ids": [task.id],
            "linked_engineer_ids": [hired.id],
        })

        list_text, list_error = await self._call(
            "architect_peer_list",
            {},
            architect.id,
        )
        self.assertFalse(list_error, list_text)
        listed = json.loads(list_text)
        self.assertEqual(
            [item["id"] for item in listed["architects"]],
            [peer.id],
        )

        message_text, message_error = await self._call(
            "architect_peer_message",
            {
                "architect_id": peer.id,
                "message": "Can you sanity-check this API boundary?",
                "ack_required": True,
                "context_task_ids": [task.id],
                "context_engineer_ids": [hired.id],
                "context_decision_ids": [decision["id"]],
                "context_summary": "API ownership is ambiguous.",
            },
            architect.id,
        )
        self.assertFalse(message_error, message_text)
        sent = json.loads(message_text)
        self.assertEqual(sent["type"], "ok")
        self.assertEqual(sent["recipient_architect_id"], peer.id)
        self.assertEqual(sent["delivery"]["state"], "delivered")
        persisted = self.db.load_agent_peer_message(sent["message_id"])
        self.assertEqual(persisted["context_task_ids"], [task.id])
        self.assertEqual(persisted["context_engineer_ids"], [hired.id])
        self.assertEqual(persisted["context_decision_ids"], [decision["id"]])
        self.assertEqual(
            persisted["context_snapshot"]["decisions"][0]["title"],
            "Use direct peer messages",
        )
        self.assertEqual(architect.mcp_messages[0]["action"], "architect_peer_message")
        self.assertEqual(architect.mcp_messages[0]["direction"], "sent")
        self.assertTrue(architect.mcp_messages[0]["delivered"])
        self.assertEqual(peer.mcp_messages[0]["direction"], "received")
        self.assertTrue(peer.mcp_messages[0]["delivered"])
        self.assertTrue(peer.mcp_messages[0]["ack_required"])
        injects = [
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
        ]
        self.assertEqual(len(injects), 1)
        self.assertEqual(injects[-1]["agent_id"], peer.id)
        self.assertTrue(injects[-1]["ack_required"])

        self.db.save_direct_message({
            "id": "direct-user-peer",
            "group_name": peer.group,
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": peer.id,
            "recipient_kind": "architect",
            "message": "user direct messages must not pollute peer inbox",
            "created_at": 999.0,
            "ack_required": True,
            "delivery_state": "buffered",
        })
        self.db.save_agent_peer_message({
            "id": "engineer-thread-hidden-from-peer-inbox",
            "thread_id": "engineer-thread-hidden-from-peer-inbox",
            "group_name": peer.group,
            "sender_id": hired.id,
            "sender_kind": "engineer",
            "recipient_id": peer.id,
            "recipient_kind": "architect",
            "message": "Engineer threads require message.engineer authority.",
            "created_at": 998.0,
            "ack_required": True,
            "delivery_state": "buffered",
        })
        inbox_text, inbox_error = await self._call(
            "architect_peer_inbox",
            {"requires_reply": True, "detail": True},
            peer.id,
        )
        self.assertFalse(inbox_error, inbox_text)
        inbox = json.loads(inbox_text)
        self.assertEqual(inbox["type"], "architect_peer_inbox")
        self.assertEqual(len(inbox["threads"]), 1)
        self.assertEqual(inbox["threads"][0]["thread_id"], sent["thread_id"])
        self.assertTrue(inbox["threads"][0]["requires_reply"])
        self.assertEqual(
            inbox["threads"][0]["messages"][0]["context"]["task_ids"],
            [task.id],
        )

        reply_text, reply_error = await self._call(
            "architect_peer_reply",
            {
                "message_id": sent["message_id"],
                "message": "Looks good; please confirm after review.",
                "ack_required": True,
            },
            peer.id,
        )
        self.assertFalse(reply_error, reply_text)
        reply = json.loads(reply_text)
        self.assertEqual(reply["thread_id"], sent["thread_id"])
        self.assertEqual(architect.mcp_messages[0]["action"], "architect_peer_reply")
        self.assertEqual(architect.mcp_messages[0]["direction"], "received")
        self.assertTrue(architect.mcp_messages[0]["ack_required"])

        answered_text, answered_error = await self._call(
            "architect_peer_inbox",
            {"requires_reply": True},
            peer.id,
        )
        self.assertFalse(answered_error, answered_text)
        self.assertEqual(json.loads(answered_text)["threads"], [])

    async def test_architect_peer_scoping_rules_and_dismissed_buffering(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-2", "Peer")
        dismissed_peer = self._add_architect("arch-dismissed", "Dismissed")
        dismissed_peer.dismissed_at = 99
        dismissed_peer.session_id = "should-not-inject"
        self.state._db_save_agent(dismissed_peer)
        other_group_architect = self._add_architect(
            "arch-other-group",
            "Other Group",
            group="other",
        )
        tombstoned = self._add_architect("arch-tomb", "Tomb")
        tombstoned.deleted_at = 123.0
        self.state._db_save_agent(tombstoned)
        engineer = self._add_engineer("eng-1", "Engineer")

        default_list_text, default_list_error = await self._call(
            "architect_peer_list",
            {},
            architect.id,
        )
        self.assertFalse(default_list_error, default_list_text)
        self.assertEqual(
            [item["id"] for item in json.loads(default_list_text)["architects"]],
            [peer.id],
        )

        dismissed_list_text, dismissed_list_error = await self._call(
            "architect_peer_list",
            {"include_dismissed": True},
            architect.id,
        )
        self.assertFalse(dismissed_list_error, dismissed_list_text)
        self.assertEqual(
            [item["id"] for item in json.loads(dismissed_list_text)["architects"]],
            [dismissed_peer.id, peer.id],
        )

        for target, expected in [
            (architect.id, "cannot message self"),
            (other_group_architect.id, "architect not found in scope"),
            (tombstoned.id, "architect is tombstoned"),
            (engineer.id, f"Architect not found: {engineer.id}"),
        ]:
            text, is_error = await self._call(
                "architect_peer_message",
                {"architect_id": target, "message": "hello"},
                architect.id,
            )
            self.assertTrue(is_error, target)
            self.assertEqual(text, expected)

        architect.dismissed_at = 77
        self.state._db_save_agent(architect)
        dismissed_text, dismissed_error = await self._call(
            "architect_peer_message",
            {"architect_id": peer.id, "message": "blocked"},
            architect.id,
        )
        self.assertTrue(dismissed_error)
        self.assertEqual(json.loads(dismissed_text)["reason"], "architect_dismissed")
        architect.dismissed_at = 0
        self.state._db_save_agent(architect)

        buffered_text, buffered_error = await self._call(
            "architect_peer_message",
            {"architect_id": dismissed_peer.id, "message": "read on rehire"},
            architect.id,
        )
        self.assertFalse(buffered_error, buffered_text)
        buffered = json.loads(buffered_text)
        self.assertEqual(buffered["delivery"], {
            "state": "buffered",
            "reason": "recipient_dismissed",
        })
        persisted = self.db.load_agent_peer_message(buffered["message_id"])
        self.assertEqual(persisted["delivery_state"], "buffered")
        self.assertEqual(persisted["delivery_reason"], "recipient_dismissed")
        injects = [
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
            and call.get("agent_id") == dismissed_peer.id
        ]
        self.assertEqual(injects, [])

    async def test_engineer_peer_notify_inspect_scope_matrix(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        bob = self._add_engineer(
            "eng-bob", "Bob", hired_by_architect_id=architect.id
        )
        bob.session_id = "bob-session"
        self.state._db_save_agent(bob)
        charlie = self._add_engineer(
            "eng-charlie", "Charlie", hired_by_architect_id=architect.id
        )
        other_hire = self._add_engineer(
            "eng-other-hire", "Other Hire", hired_by_architect_id=other_architect.id
        )
        user_owned = self._add_engineer("eng-user", "User Owned")
        other_group = self._add_engineer(
            "eng-other-group",
            "Other Group",
            hired_by_architect_id=architect.id,
            group="other",
        )
        dismissed = self._add_engineer(
            "eng-dismissed", "Dismissed", hired_by_architect_id=architect.id
        )
        dismissed.dismissed_at = 99
        self.state._db_save_agent(dismissed)
        tombstoned = self._add_engineer(
            "eng-tomb", "Tomb", hired_by_architect_id=architect.id
        )
        tombstoned.deleted_at = 123.0
        self.state._db_save_agent(tombstoned)
        alice_worker = self._add_worker("worker-alice", "Alice Worker", alice.id)
        bob_worker = self._add_worker("worker-bob", "Bob Worker", bob.id)
        visible_task = self._add_task(
            "TORQUE:801",
            "Peer-visible context",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
        )
        hidden_task = self._add_task(
            "TORQUE:802",
            "Bob-only context",
            assigned_engineer_id=bob.id,
            created_by_architect_id=architect.id,
        )
        hidden_branch = "torque/bob/hidden"
        bob_worker.worktree_path = "/repo/.torque/worktrees/worker-bob"
        bob_worker.worktree_repo_root = "/repo"
        bob_worker.worktree_branch = hidden_branch
        bob_worker.git_root = "/repo"
        bob_worker.current_task_id = hidden_task.id
        hidden_task.agent_id = bob_worker.id
        self.state._db_save_agent(bob_worker)
        self.state._db_save_task(hidden_task)

        list_text, list_error = await self._call_engineer(
            "engineer_peer_list",
            {},
            alice.id,
        )
        self.assertFalse(list_error, list_text)
        listed = json.loads(list_text)
        self.assertEqual(listed["type"], "engineer_peers")
        self.assertEqual(
            [item["id"] for item in listed["engineers"]],
            [bob.id, charlie.id],
        )
        for item in listed["engineers"]:
            self.assertNotIn("current_task_id", item)
            self.assertNotIn("worktree_path", item)

        dismissed_text, dismissed_error = await self._call_engineer(
            "engineer_peer_list",
            {"include_dismissed": True},
            alice.id,
        )
        self.assertFalse(dismissed_error, dismissed_text)
        self.assertEqual(
            [item["id"] for item in json.loads(dismissed_text)["engineers"]],
            [bob.id, charlie.id, dismissed.id],
        )

        for target in [alice.id, other_hire.id, user_owned.id, other_group.id,
                       tombstoned.id, architect.id, bob_worker.id]:
            text, is_error = await self._call_engineer(
                "engineer_peer_notify",
                {
                    "engineer_id": target,
                    "message": "look here",
                    "context_task_ids": [visible_task.id],
                },
                alice.id,
            )
            self.assertTrue(is_error, target)

        summary_only_text, summary_only_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": bob.id,
                "message": "look here",
                "context_summary": "summary alone is not enough",
            },
            alice.id,
        )
        self.assertTrue(summary_only_error)
        self.assertIn("context_task_ids or context_stream_refs", summary_only_text)

        hidden_context_text, hidden_context_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": bob.id,
                "message": "look here",
                "context_task_ids": [hidden_task.id],
            },
            alice.id,
        )
        self.assertTrue(hidden_context_error)
        self.assertEqual(hidden_context_text, f"Task not found: {hidden_task.id}")

        streams_text, streams_error = await self._call_engineer(
            "engineer_streams_list",
            {"include_orphaned": True},
            alice.id,
        )
        self.assertFalse(streams_error, streams_text)
        self.assertEqual(json.loads(streams_text)["count"], 0)
        hidden_stream_show_text, hidden_stream_show_error = await self._call_engineer(
            "engineer_stream_show",
            {"repo_root": "/repo", "branch": hidden_branch},
            alice.id,
        )
        self.assertTrue(hidden_stream_show_error)
        hidden_stream_context_text, hidden_stream_context_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": charlie.id,
                "message": "hidden stream should not leak",
                "context_stream_refs": [
                    {"repo_root": "/repo", "branch": hidden_branch},
                ],
            },
            alice.id,
        )
        self.assertTrue(hidden_stream_context_error)
        self.assertEqual(hidden_stream_context_text, hidden_stream_show_text)

        before_task_ids = set(self.state.board_tasks)
        notify_text, notify_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": bob.id,
                "message": "Please inspect the signed-off design scope.",
                "context_task_ids": [visible_task.id],
                "context_summary": "Scope/auth check requested.",
                "ack_required": True,
            },
            alice.id,
        )
        self.assertFalse(notify_error, notify_text)
        notify = json.loads(notify_text)
        self.assertEqual(notify["type"], "ok")
        self.assertEqual(notify["recipient_engineer_id"], bob.id)
        self.assertEqual(notify["delivery"], {"state": "delivered", "reason": ""})
        self.assertEqual(set(self.state.board_tasks), before_task_ids)
        persisted = self.db.load_agent_peer_message(notify["message_id"])
        self.assertEqual(persisted["sender_kind"], "engineer")
        self.assertEqual(persisted["recipient_kind"], "engineer")
        self.assertEqual(persisted["context_task_ids"], [visible_task.id])
        self.assertEqual(
            persisted["context_snapshot"]["inspect_grant"]["supervising_architect_id"],
            architect.id,
        )
        injects = [
            call for call in self.handle_calls
            if call.get("cmd") == "inject_mcp_message"
        ]
        self.assertEqual([call["agent_id"] for call in injects], [bob.id])
        self.assertEqual(injects[0]["sender_kind"], "engineer")
        self.assertTrue(injects[0]["ack_required"])

        same_pair_continue_text, same_pair_continue_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": bob.id,
                "message": "same pair follow-up",
                "thread_id": notify["thread_id"],
                "context_task_ids": [visible_task.id],
            },
            alice.id,
        )
        self.assertFalse(same_pair_continue_error, same_pair_continue_text)
        self.assertEqual(
            json.loads(same_pair_continue_text)["thread_id"],
            notify["thread_id"],
        )

        dave = self._add_engineer(
            "eng-dave", "Dave", hired_by_architect_id=architect.id
        )
        charlie_task = self._add_task(
            "TORQUE:803",
            "Charlie-visible context",
            assigned_engineer_id=charlie.id,
            created_by_architect_id=architect.id,
        )
        charlie_notify_text, charlie_notify_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": dave.id,
                "message": "charlie/dave thread",
                "context_task_ids": [charlie_task.id],
            },
            charlie.id,
        )
        self.assertFalse(charlie_notify_error, charlie_notify_text)
        charlie_notify = json.loads(charlie_notify_text)
        collision_text, collision_error = await self._call_engineer(
            "engineer_peer_notify",
            {
                "engineer_id": bob.id,
                "message": "must not collide with another pair",
                "thread_id": charlie_notify["thread_id"],
                "context_task_ids": [visible_task.id],
            },
            alice.id,
        )
        self.assertTrue(collision_error)
        self.assertEqual(collision_text, "thread not found in scope")
        charlie_inspect_text, charlie_inspect_error = await self._call(
            "architect_engineer_peer_inspect",
            {"thread_id": charlie_notify["thread_id"]},
            architect.id,
        )
        self.assertFalse(charlie_inspect_error, charlie_inspect_text)
        self.assertEqual(
            json.loads(charlie_inspect_text)["context"]["task_ids"],
            [charlie_task.id],
        )

        agents_text, agents_error = await self._call_engineer(
            "engineer_agents_list", {}, alice.id
        )
        self.assertFalse(agents_error, agents_text)
        visible_agent_ids = {item["id"] for item in json.loads(agents_text)["agents"]}
        self.assertIn(alice.id, visible_agent_ids)
        self.assertIn(alice_worker.id, visible_agent_ids)
        self.assertNotIn(bob.id, visible_agent_ids)
        self.assertNotIn(bob_worker.id, visible_agent_ids)
        for target in [bob.id, bob_worker.id]:
            show_text, show_error = await self._call_engineer(
                "engineer_agent_show", {"agent": target}, alice.id
            )
            self.assertTrue(show_error, target)
            self.assertEqual(show_text, "agent not found in scope")
            message_text, message_error = await self._call_engineer(
                "engineer_agent_message",
                {"agent": target, "message": "generic path denied"},
                alice.id,
            )
            self.assertTrue(message_error, target)
            self.assertEqual(message_text, "agent not found in scope")

        for participant in [alice, bob]:
            inspect_text, inspect_error = await self._call_engineer(
                "engineer_peer_inspect",
                {"message_id": notify["message_id"]},
                participant.id,
            )
            self.assertFalse(inspect_error, inspect_text)
            inspected = json.loads(inspect_text)
            self.assertEqual(inspected["type"], "engineer_peer_inspect")
            self.assertEqual(inspected["thread_id"], notify["thread_id"])
            self.assertEqual(inspected["context"]["task_ids"], [visible_task.id])
            self.assertIn(visible_task.id, [task["id"] for task in inspected["live"]["tasks"]])

        outsider_text, outsider_error = await self._call_engineer(
            "engineer_peer_inspect",
            {"message_id": notify["message_id"]},
            charlie.id,
        )
        self.assertTrue(outsider_error)
        self.assertEqual(outsider_text, "thread not found in scope")

        self.state.update_agent_digest_settings(
            architect.id,
            architect_digest=True,
            enabled_events=[],
        )
        threads_text, threads_error = await self._call(
            "architect_engineer_peer_threads",
            {"engineer_id": alice.id},
            architect.id,
        )
        self.assertFalse(threads_error, threads_text)
        threads = json.loads(threads_text)
        self.assertEqual(threads["type"], "engineer_peer_threads")
        self.assertEqual([thread["thread_id"] for thread in threads["threads"]], [notify["thread_id"]])
        inspect_text, inspect_error = await self._call(
            "architect_engineer_peer_inspect",
            {"thread_id": notify["thread_id"]},
            architect.id,
        )
        self.assertFalse(inspect_error, inspect_text)
        self.assertEqual(json.loads(inspect_text)["context"]["task_ids"], [visible_task.id])
        other_threads_text, other_threads_error = await self._call(
            "architect_engineer_peer_threads",
            {},
            other_architect.id,
        )
        self.assertFalse(other_threads_error, other_threads_text)
        self.assertEqual(json.loads(other_threads_text)["threads"], [])
        other_inspect_text, other_inspect_error = await self._call(
            "architect_engineer_peer_inspect",
            {"thread_id": notify["thread_id"]},
            other_architect.id,
        )
        self.assertTrue(other_inspect_error)
        self.assertEqual(other_inspect_text, "thread not found in scope")

    async def test_architect_decisions_are_same_group_read_and_owner_write(self):
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
        self.assertEqual(
            [item["id"] for item in json.loads(other_text)["decisions"]],
            [decision["id"]],
        )
        get_text, get_error = await self._call(
            "architect_decision_get",
            {"id": decision["id"]},
            other_architect.id,
        )
        self.assertFalse(get_error, get_text)
        self.assertEqual(json.loads(get_text)["id"], decision["id"])

        peer_update_text, peer_update_error = await self._call(
            "architect_decision_update",
            {"id": decision["id"], "title": "Peer must not edit"},
            other_architect.id,
        )
        self.assertTrue(peer_update_error)
        self.assertEqual(peer_update_text, "Decision not found")
        self.assertEqual(self.state.load_decision(decision["id"])["title"], "Ship safer path")

        cross_group_architect = self._add_architect(
            "arch-other-group", "Other Group", group="other"
        )
        cross_group_decision = self.state.save_decision({
            "id": "decision-cross-group",
            "architect_id": cross_group_architect.id,
            "title": "Do not disclose",
            "rationale": "Cross-group boundary",
            "status": "proposed",
        })
        self.assertIsNotNone(cross_group_decision)
        cross_get_text, cross_get_error = await self._call(
            "architect_decision_get",
            {"id": cross_group_decision["id"]},
            other_architect.id,
        )
        self.assertTrue(cross_get_error)
        self.assertEqual(cross_get_text, "Decision not found")

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
        self.assertEqual(self.state._delta_ops[-1]["op"], "decision_upsert")
        self.assertTrue(self.state._delta_ops[-1]["archived"])

    async def test_architect_read_defaults_are_bounded_and_report_totals(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-2", "Peer")
        long_text = "x" * 4_000
        for index in range(7):
            self.state.save_decision({
                "id": f"decision-{index}", "architect_id": architect.id,
                "title": f"Decision {index}", "rationale": long_text,
                "status": "accepted",
            })
            self.db.save_agent_peer_message({
                "id": f"message-{index}", "thread_id": f"thread-{index}",
                "group_name": architect.group, "sender_id": peer.id,
                "sender_kind": "architect", "recipient_id": architect.id,
                "recipient_kind": "architect", "message": long_text,
                "created_at": float(index + 1),
            })

        decision_text, decision_error = await self._call(
            "architect_decision_list", {}, architect.id,
        )
        self.assertFalse(decision_error, decision_text)
        decisions = json.loads(decision_text)
        self.assertEqual((decisions["decisions_total"], decisions["decisions_returned"]), (7, 6))
        self.assertTrue(decisions["decisions_capped"])
        self.assertTrue(decisions["decisions"][0]["rationale_truncated"])

        inbox_text, inbox_error = await self._call(
            "architect_peer_inbox", {}, architect.id,
        )
        self.assertFalse(inbox_error, inbox_text)
        inbox = json.loads(inbox_text)
        self.assertEqual((inbox["threads_total"], inbox["threads_returned"]), (7, 6))
        self.assertTrue(inbox["threads_capped"])
        self.assertTrue(inbox["threads"][0]["last_message"]["message_truncated"])

        with mock.patch.object(self.state_mod, "DATA_DIR", Path(self.tmp.name)):
            for index in range(7):
                self.state.architect_journal_append(
                    architect.id, "observation", long_text,
                )
            journal_text, journal_error = await self._call(
                "architect_journal_read", {}, architect.id,
            )
        self.assertFalse(journal_error, journal_text)
        journal = json.loads(journal_text)
        self.assertEqual((journal["entries_total"], journal["entries_returned"]), (7, 6))
        self.assertTrue(journal["entries_capped"])
        self.assertTrue(journal["entries"][0]["entry_truncated"])

        full_text, full_error = await self._call(
            "architect_peer_inbox", {"detail": True, "limit": 1}, architect.id,
        )
        self.assertFalse(full_error, full_text)
        self.assertEqual(json.loads(full_text)["threads"][0]["messages"][0]["message"], long_text)

    async def test_architect_wave_summary_from_decision_groups_evidence_and_exclusions(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        product = self._add_task(
            "TORQUE:100",
            "Ship wave root",
            lane="Done",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            labels=["wave-a"],
        )
        impl = self._add_task(
            "TORQUE:100:1",
            "Implement shipped helper",
            lane="Done",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            action_name="feature/implement",
            suggested_specialization="orchestration-core",
            labels=["P1", "wave-summary"],
        )
        impl.worktree_boundary = {
            "status": "merged",
            "commit_sha": "head123",
            "merge_commit_sha": "squash789",
            "recorded_at": "2026-06-17T12:00:00+00:00",
            "pr": {"url": "https://github.com/acme/repo/pull/7"},
        }
        impl.completion_evidence = {
            "sources": ["merge"],
            "merge": {
                "sha": "squash789",
                "pr_url": "https://github.com/acme/repo/pull/7",
                "origin_verified": True,
                "origin_summary": "origin/main == squash789",
                "origin_ref": "origin/main",
                "origin_sha": "squash789",
            },
        }
        impl.verification_state = "passed"
        impl.verification_summary = {
            "tests_run": "pytest tests/test_architect_scoping.py",
            "deploy_attempted": False,
            "live_smoke_pending": True,
        }
        review = self._add_task(
            "TORQUE:100:2",
            "Review shipped helper",
            lane="Done",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            action_name="feature/review",
            labels=["review"],
        )
        review.worktree_boundary = {
            "status": "open",
            "commit_sha": "head123",
            "recorded_at": "2026-06-17T12:05:00+00:00",
        }
        review.completion_evidence = {
            "sources": ["review"],
            "review": {
                "verdict": "ship",
                "follow_up_classification": "none",
                "source_action": "done",
                "recorded_at": "2026-06-17T12:10:00+00:00",
            },
        }
        active = self._add_task(
            "TORQUE:100:3",
            "Update release notes",
            lane="In Progress",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
        )
        parked = self._add_task(
            "TORQUE:100:4",
            "Park future audit",
            lane="Backlog",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            labels=["deferred"],
        )
        decision = self.state.save_decision({
            "id": "decision-wave",
            "architect_id": architect.id,
            "title": "Wave A",
            "rationale": "Summarize linked wave",
            "status": "accepted",
            "linked_task_ids": [product.id],
            "linked_engineer_ids": [alice.id],
        })

        text, error = await self._call(
            "architect_wave_summary",
            {"decision_id": decision["id"], "limit_per_section": 5},
            architect.id,
        )
        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "wave_summary")
        self.assertEqual(payload["source"]["decision"]["id"], decision["id"])
        self.assertIn(impl.id, payload["source"]["expanded_task_ids"])
        categories = {
            item["category"]: item
            for item in payload["sections"]["completed_by_category"]["categories"]
        }
        shipped_items = categories["orchestration-core"]["items"]
        shipped = next(item for item in shipped_items if item["id"] == impl.id)
        evidence = shipped["evidence"]
        self.assertEqual(evidence["pr_url"], "https://github.com/acme/repo/pull/7")
        self.assertEqual(evidence["squash_sha"], "squash789")
        self.assertTrue(evidence["origin_verification"]["verified"])
        self.assertEqual(
            evidence["verification"]["tests_run"],
            "pytest tests/test_architect_scoping.py",
        )
        review_items = categories["review"]["items"]
        self.assertEqual(review_items[0]["evidence"]["review"]["verdict"], "ship")
        remaining_ids = [
            item["id"]
            for item in payload["sections"]["remaining_active"]["items"]
        ]
        self.assertIn(active.id, remaining_ids)
        parked_ids = [
            item["id"]
            for item in payload["sections"]["parked_deferred"]["items"]
        ]
        self.assertIn(parked.id, parked_ids)
        self.assertIn("worker-context deploys/restarts", " ".join(payload["caveats"]))

        other_text, other_error = await self._call(
            "architect_wave_summary",
            {"decision_id": decision["id"]},
            other_architect.id,
        )
        self.assertFalse(other_error, other_text)
        self.assertEqual(
            json.loads(other_text)["source"]["decision"]["id"],
            decision["id"],
        )

    async def test_architect_wave_summary_from_explicit_tasks_marks_missing_evidence_unknown(self):
        architect = self._add_architect("arch-1", "Architect")
        task = self._add_task(
            "TORQUE:200",
            "Done without recorded evidence",
            lane="Done",
            created_by_architect_id=architect.id,
            labels=["cleanup"],
        )

        text, error = await self._call(
            "architect_wave_summary",
            {"task_ids": [task.id], "limit_per_section": 2},
            architect.id,
        )
        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["source"]["task_ids"], [task.id])
        category = payload["sections"]["completed_by_category"]["categories"][0]
        self.assertEqual(category["category"], "cleanup")
        evidence = category["items"][0]["evidence"]
        self.assertEqual(evidence["pr_url"], "unknown/not recorded")
        self.assertEqual(evidence["squash_sha"], "unknown/not recorded")
        self.assertEqual(evidence["verification"], "unknown/not recorded")
        self.assertEqual(payload["counts"]["completed_missing_recorded_evidence"], 1)

        both_text, both_error = await self._call(
            "architect_wave_summary",
            {"decision_id": "decision-x", "task_ids": [task.id]},
            architect.id,
        )
        self.assertTrue(both_error)
        self.assertEqual(both_text, "Provide exactly one of decision_id or task_ids")

    async def test_architect_completion_audit_recommends_complete_when_no_gates_or_caveats(self):
        architect = self._add_architect("arch-1", "Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        root = self._add_task(
            "TORQUE:300",
            "Complete goal root",
            lane="Done",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            verification_state="passed",
        )
        root.verification_summary = {
            "tests_run": "pytest tests/test_architect_scoping.py",
            "deploy_needed": False,
            "live_smoke_pending": False,
        }
        decision = self.state.save_decision({
            "id": "decision-complete",
            "architect_id": architect.id,
            "title": "Complete goal",
            "status": "accepted",
            "linked_task_ids": [root.id],
            "linked_engineer_ids": [alice.id],
        })

        text, error = await self._call(
            "architect_completion_audit",
            {"decision_id": decision["id"], "limit_per_section": 5},
            architect.id,
        )

        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["type"], "completion_audit")
        self.assertEqual(payload["recommendation"], "complete")
        self.assertEqual(payload["counts"]["remaining_gates"], 0)
        self.assertEqual(payload["counts"]["verification_caveats"], 0)
        self.assertIn(root.id, payload["source"]["expanded_task_ids"])

    async def test_architect_completion_audit_recommends_not_complete_for_remaining_gates(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-peer", "Peer Architect")
        alice = self._add_engineer(
            "eng-alice", "Alice", hired_by_architect_id=architect.id
        )
        worker = self._add_worker("worker-1", "Worker", owner_engineer_id=alice.id)
        active = self._add_task(
            "TORQUE:310",
            "Active scoped work",
            lane="In Progress",
            assigned_engineer_id=alice.id,
            created_by_architect_id=architect.id,
            labels=["torque:human"],
            completion_evidence={
                "completion": {
                    "acceptance_deviation": {
                        "statement": "Delivered the service contract only.",
                        "reason": "The UI follow-up is separately assigned.",
                        "agent_id": worker.id,
                        "agent_name": worker.name,
                        "recorded_at": "2026-06-17T11:00:00+00:00",
                    },
                    "acceptance_deviation_attempt": {
                        "statement": "The UI acceptance item is deferred.",
                        "reason": "",
                        "missing_fields": ["reason"],
                        "agent_id": worker.id,
                        "agent_name": worker.name,
                        "recorded_at": "2026-06-17T11:01:00+00:00",
                    },
                },
            },
        )
        review = self._add_task(
            "TORQUE:310:1",
            "Review scoped work",
            lane="Done",
            action_name="feature/review",
            parent_task_id=active.id,
            pipeline_root_id=active.id,
            pipeline_depth=1,
            agent_id=worker.id,
            assigned_engineer_id=alice.id,
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/alice-ready",
                "status": "open",
                "recorded_at": "2026-06-17T12:00:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "recorded_at": "2026-06-17T12:00:00+00:00",
                },
            },
        )
        parked = self._add_task(
            "TORQUE:310:2",
            "Deferred follow-up",
            lane="Backlog",
            parent_task_id=active.id,
            pipeline_root_id=active.id,
            pipeline_depth=1,
            labels=["deferred"],
        )
        with mock.patch("time.time", return_value=123.0):
            self.state.update_engineer_settings(
                "torque",
                pending_question="Should this scope wait?",
                paused=True,
                _pending_question_actor_id=alice.id,
            )
        self.state.save_pending_hire({
            "id": "hire-audit",
            "architect_id": architect.id,
            "requested_name": "QA",
            "requested_provider": "codex",
            "status": "pending",
            "created_at": 10,
        })
        self.db.save_agent_peer_message({
            "id": "peer-audit-ack",
            "thread_id": "peer-audit",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Please ack scoped handoff.",
            "created_at": 50.0,
            "ack_required": True,
            "context_task_ids": [active.id],
            "context_engineer_ids": [alice.id],
            "context_summary": "completion audit",
        })
        decision = self.state.save_decision({
            "id": "decision-incomplete",
            "architect_id": architect.id,
            "title": "Incomplete goal",
            "status": "accepted",
            "linked_task_ids": [active.id],
            "linked_engineer_ids": [alice.id],
        })

        text, error = await self._call(
            "architect_completion_audit",
            {"decision_id": decision["id"], "limit_per_section": 5},
            architect.id,
        )

        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["recommendation"], "not_complete")
        self.assertGreater(payload["counts"]["remaining_gates"], 0)
        self.assertEqual(payload["sections"]["blocking_asks"]["items"][0]["id"], active.id)
        self.assertEqual(
            payload["sections"]["engineer_pending_questions"]["items"][0]["engineer_id"],
            alice.id,
        )
        self.assertEqual(
            payload["sections"]["peer_ack_required"]["items"][0]["thread_id"],
            "peer-audit",
        )
        self.assertEqual(
            payload["sections"]["pending_hires"]["items"][0]["id"],
            "hire-audit",
        )
        branch_task_ids = {
            task_id
            for item in payload["sections"]["branch_boundaries"]["open_or_unmerged"]["items"]
            for task_id in item.get("workflow_task_ids", []) + item.get("product_task_ids", [])
        }
        self.assertIn(review.id, branch_task_ids)
        audit_branch = next(
            item
            for item in payload["sections"]["branch_boundaries"]["open_or_unmerged"]["items"]
            if review.id in item.get("workflow_task_ids", [])
        )
        self.assertEqual(audit_branch["completion_deviations"], [{
            "task_id": active.id,
            "task_title": active.task,
            "statement": "Delivered the service contract only.",
            "reason": "The UI follow-up is separately assigned.",
            "agent_id": worker.id,
            "agent_name": worker.name,
            "recorded_at": "2026-06-17T11:00:00+00:00",
        }])
        self.assertEqual(
            audit_branch["completion_deviation_disclosure_attempts"], [{
                "task_id": active.id,
                "task_title": active.task,
                "statement": "The UI acceptance item is deferred.",
                "reason": "",
                "missing_fields": ["reason"],
                "agent_id": worker.id,
                "agent_name": worker.name,
                "recorded_at": "2026-06-17T11:01:00+00:00",
            }],
        )
        parked_ids = [
            item["id"] for item in payload["sections"]["parked_deferred"]["items"]
        ]
        self.assertIn(parked.id, parked_ids)

    async def test_architect_completion_audit_scans_peer_acks_before_output_bound(self):
        architect = self._add_architect("arch-1", "Architect")
        peer = self._add_architect("arch-peer", "Peer Architect")
        task = self._add_task(
            "TORQUE:315",
            "Complete scoped work with old peer ack",
            lane="Done",
            created_by_architect_id=architect.id,
            verification_state="passed",
        )
        task.verification_summary = {
            "tests_run": "python3 -m unittest tests.test_architect_scoping",
            "deploy_needed": False,
            "live_smoke_pending": False,
        }
        decision = self.state.save_decision({
            "id": "decision-peer-ack-bound",
            "architect_id": architect.id,
            "title": "Peer ack bound",
            "status": "accepted",
            "linked_task_ids": [task.id],
            "linked_engineer_ids": [],
        })
        for idx in range(self.shared_mod._ARCHITECT_ATTENTION_MAX_LIMIT + 1):
            self.db.save_agent_peer_message({
                "id": f"peer-newer-other-{idx}",
                "thread_id": f"peer-newer-other-{idx}",
                "group_name": "torque",
                "sender_id": peer.id,
                "sender_kind": "architect",
                "recipient_id": architect.id,
                "recipient_kind": "architect",
                "message": f"Newer unrelated ack {idx}",
                "created_at": 1000.0 + idx,
                "ack_required": True,
                "context_task_ids": [f"TORQUE:other-{idx}"],
                "context_decision_ids": [f"decision-other-{idx}"],
                "context_summary": "unrelated newer ack",
            })
        self.db.save_agent_peer_message({
            "id": "peer-older-matching",
            "thread_id": "peer-older-matching",
            "group_name": "torque",
            "sender_id": peer.id,
            "sender_kind": "architect",
            "recipient_id": architect.id,
            "recipient_kind": "architect",
            "message": "Older scoped ack still needs response.",
            "created_at": 10.0,
            "ack_required": True,
            "context_task_ids": [task.id],
            "context_decision_ids": [decision["id"]],
            "context_summary": "matching old ack",
        })

        text, error = await self._call(
            "architect_completion_audit",
            {"decision_id": decision["id"], "limit_per_section": 5},
            architect.id,
        )

        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["recommendation"], "not_complete")
        self.assertEqual(payload["sections"]["peer_ack_required"]["count"], 1)
        self.assertEqual(
            payload["sections"]["peer_ack_required"]["items"][0]["thread_id"],
            "peer-older-matching",
        )
        gate_kinds = {
            item["kind"]
            for item in payload["sections"]["remaining_gates"]["items"]
        }
        self.assertIn("peer_ack_required", gate_kinds)

    async def test_architect_completion_audit_recommends_complete_with_caveats_for_unknown_evidence(self):
        architect = self._add_architect("arch-1", "Architect")
        shipped = self._add_task(
            "TORQUE:320",
            "Shipped without recorded smoke",
            lane="Done",
            created_by_architect_id=architect.id,
            labels=["cleanup"],
        )
        parked = self._add_task(
            "TORQUE:320:1",
            "Park future cleanup",
            lane="Backlog",
            parent_task_id=shipped.id,
            pipeline_root_id=shipped.id,
            pipeline_depth=1,
            labels=["parked"],
        )

        text, error = await self._call(
            "architect_completion_audit",
            {"task_ids": [shipped.id], "limit_per_section": 5},
            architect.id,
        )

        self.assertFalse(error, text)
        payload = json.loads(text)
        self.assertEqual(payload["recommendation"], "complete_with_caveats")
        self.assertEqual(payload["counts"]["remaining_gates"], 0)
        caveat_kinds = {
            item["kind"]
            for item in payload["sections"]["verification_caveats"]["items"]
        }
        self.assertIn("verification_unknown", caveat_kinds)
        self.assertIn("deploy_need_unknown", caveat_kinds)
        self.assertEqual(
            payload["sections"]["parked_deferred"]["items"][0]["id"],
            parked.id,
        )
        self.assertIn("unknown/not-recorded", payload["recommendation_rules"]["complete_with_caveats"])

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

    async def test_architect_mcp_calls_scopes_to_architect_group(self):
        architect = self._add_architect("arch-1", "Architect")
        worker = self._add_worker("worker-a", "Worker A", "")
        other = self.state_mod.AgentCell(
            id="other-group-worker",
            name="Other Worker",
            slug="other-worker",
            group="other",
            cell_type="agent",
            kind="worker",
        )
        self.state.agents[other.id] = other
        self.state.groups["other"] = [other.id]
        self.state._db_save_agent(other)
        self.state._db_save_groups()
        calls = []

        async def fake_handle_command(payload):
            calls.append(payload)
            self.assertEqual(payload["cmd"], "architect_mcp_calls")
            self.assertEqual(payload["caller_id"], architect.id)
            return {"type": "mcp_calls", "calls": [{"cell_id": payload["agent_id"]}]}

        text, is_error = await self.mcp_architect_mod._dispatch_architect_tool(
            "architect_mcp_calls",
            {"agent_id": worker.id, "limit": 5},
            fake_handle_command,
            self.state,
            caller_id=architect.id,
        )
        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["calls"], [{"cell_id": worker.id}])
        self.assertEqual(calls[-1]["limit"], 5)

        hidden_text, hidden_error = await self.mcp_architect_mod._dispatch_architect_tool(
            "architect_mcp_calls",
            {"agent_id": other.id},
            fake_handle_command,
            self.state,
            caller_id=architect.id,
        )
        self.assertTrue(hidden_error)
        self.assertEqual(hidden_text, f"Agent not found: {other.id}")

    async def test_architect_engineer_journal_read_scopes_to_hired_engineers(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        hired = self._add_engineer(
            "eng-hired",
            "Hired Engineer",
            hired_by_architect_id=architect.id,
        )
        other_hired = self._add_engineer(
            "eng-other",
            "Other Engineer",
            hired_by_architect_id=other_architect.id,
        )

        with mock.patch("time.time", side_effect=[100.0, 200.0, 300.0, 400.0]):
            self.state.journal_append(
                "torque",
                "checkpoint",
                "Old checkpoint",
                author_cell_id=hired.id,
            )
            self.state.journal_append(
                "torque",
                "plan",
                "Recent plan",
                author_cell_id=hired.id,
            )
            self.state.journal_append(
                "torque",
                "observation",
                "Recent observation",
                author_cell_id=hired.id,
            )
            self.state.journal_append(
                "torque",
                "plan",
                "Other engineer plan",
                author_cell_id=other_hired.id,
            )

        read_text, read_error = await self._call(
            "architect_engineer_journal_read",
            {
                "engineer_id": hired.id,
                "since": 150,
                "limit": 10,
                "type_filter": "plan",
            },
            architect.id,
        )

        self.assertFalse(read_error, read_text)
        payload = json.loads(read_text)
        self.assertEqual(payload["type"], "journal")
        self.assertEqual(
            payload["entries"],
            [{
                "id": 2,
                "group": "torque",
                "timestamp": 200.0,
                "type": "plan",
                "entry": "Recent plan",
                "author_cell_id": hired.id,
            }],
        )
        self.assertEqual(self.handle_calls, [])

        denied_text, denied_error = await self._call(
            "architect_engineer_journal_read",
            {"engineer_id": other_hired.id},
            architect.id,
        )
        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "engineer not found in scope")

    async def test_architect_engineer_pending_question_reads_hired_engineer_question(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        hired = self._add_engineer(
            "eng-hired",
            "Hired Engineer",
            hired_by_architect_id=architect.id,
        )
        other_hired = self._add_engineer(
            "eng-other",
            "Other Engineer",
            hired_by_architect_id=other_architect.id,
        )
        question = "Need product approval for the rollout plan?\nFull context stays intact."

        with mock.patch("time.time", return_value=1234.5):
            self.state.update_engineer_settings(
                "torque",
                pending_question=question,
                paused=True,
                _pending_question_actor_id=hired.id,
            )

        read_text, read_error = await self._call(
            "architect_engineer_pending_question",
            {"engineer_id": hired.id},
            architect.id,
        )

        self.assertFalse(read_error, read_text)
        payload = json.loads(read_text)
        self.assertEqual(payload["type"], "engineer_pending_question")
        self.assertEqual(payload["engineer_id"], hired.id)
        self.assertEqual(payload["question"], question)
        self.assertEqual(payload["set_at"], 1234.5)
        self.assertTrue(payload["paused"])
        self.assertEqual(payload["note"], "Question is awaiting human input.")
        self.assertEqual(self.handle_calls, [])

        reloaded = self.state_mod.MatrixState(db=self.db)
        reloaded.load()
        persisted_text, persisted_error = await self.mcp_architect_mod._dispatch_architect_tool(
            "architect_engineer_pending_question",
            {"engineer_id": hired.id},
            self._handle_command,
            reloaded,
            caller_id=architect.id,
        )
        self.assertFalse(persisted_error, persisted_text)
        persisted_payload = json.loads(persisted_text)
        self.assertEqual(persisted_payload["question"], question)
        self.assertEqual(persisted_payload["set_at"], 1234.5)

        with mock.patch("time.time", return_value=2345.6):
            self.state.update_engineer_settings(
                "torque",
                pending_question="Secret question from other architect's engineer",
                paused=True,
                _pending_question_actor_id=other_hired.id,
            )

        empty_text, empty_error = await self._call(
            "architect_engineer_pending_question",
            {"engineer_id": hired.id},
            architect.id,
        )
        self.assertFalse(empty_error, empty_text)
        empty_payload = json.loads(empty_text)
        self.assertEqual(empty_payload["engineer_id"], hired.id)
        self.assertEqual(empty_payload["question"], "")
        self.assertEqual(empty_payload["set_at"], 0.0)
        self.assertFalse(empty_payload["paused"])
        self.assertEqual(empty_payload["note"], "No pending question for engineer.")

        other_text, other_error = await self._call(
            "architect_engineer_pending_question",
            {"engineer_id": other_hired.id},
            other_architect.id,
        )
        self.assertFalse(other_error, other_text)
        other_payload = json.loads(other_text)
        self.assertEqual(
            other_payload["question"],
            "Secret question from other architect's engineer",
        )
        self.assertEqual(other_payload["set_at"], 2345.6)

        denied_text, denied_error = await self._call(
            "architect_engineer_pending_question",
            {"engineer_id": other_hired.id},
            architect.id,
        )
        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "engineer not found in scope")

    async def test_architect_engineer_answer_resolves_owner_routed_ask(self):
        architect = self._add_architect("arch-1", "Architect")
        other_architect = self._add_architect("arch-2", "Other Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired Engineer", hired_by_architect_id=architect.id
        )
        other_hired = self._add_engineer(
            "eng-other", "Other Engineer", hired_by_architect_id=other_architect.id
        )
        self.state.update_engineer_settings(
            "torque",
            pending_question="Need product approval for the rollout plan?",
            paused=True,
            _pending_question_actor_id=hired.id,
        )

        # Missing answer is rejected before any command is issued.
        empty_text, empty_error = await self._call(
            "architect_engineer_answer",
            {"engineer_id": hired.id, "answer": "   "},
            architect.id,
        )
        self.assertTrue(empty_error)
        self.assertEqual(empty_text, "answer is required")
        self.assertEqual(self.handle_calls, [])

        # Out-of-scope engineer is denied (same gating as the read surface).
        denied_text, denied_error = await self._call(
            "architect_engineer_answer",
            {"engineer_id": other_hired.id, "answer": "Approved"},
            architect.id,
        )
        self.assertTrue(denied_error)
        self.assertEqual(denied_text, "engineer not found in scope")
        self.assertEqual(self.handle_calls, [])

        # Happy path: routes through engineer_reply and clears the question.
        ok_text, ok_error = await self._call(
            "architect_engineer_answer",
            {"engineer_id": hired.id, "answer": "Ship the rollout plan."},
            architect.id,
        )
        self.assertFalse(ok_error, ok_text)
        payload = json.loads(ok_text)
        self.assertEqual(payload["type"], "ok")
        self.assertEqual(payload["engineer_id"], hired.id)
        self.assertEqual(payload["group"], "torque")
        reply_calls = [
            call for call in self.handle_calls
            if call.get("cmd") == "engineer_reply"
        ]
        self.assertEqual(len(reply_calls), 1)
        self.assertEqual(reply_calls[0]["group"], "torque")
        self.assertEqual(reply_calls[0]["answer"], "Ship the rollout plan.")
        ws = self.state.get_engineer_settings("torque")
        self.assertEqual(str(getattr(ws, "pending_question", "") or ""), "")

        # Answering again with no pending question is rejected.
        self.handle_calls.clear()
        none_text, none_error = await self._call(
            "architect_engineer_answer",
            {"engineer_id": hired.id, "answer": "Already answered"},
            architect.id,
        )
        self.assertTrue(none_error)
        self.assertEqual(none_text, "No pending blocking question for that engineer")
        self.assertEqual(self.handle_calls, [])

    async def test_architect_engineer_answer_rejects_mismatched_actor(self):
        architect = self._add_architect("arch-1", "Architect")
        hired = self._add_engineer(
            "eng-hired", "Hired Engineer", hired_by_architect_id=architect.id
        )
        peer = self._add_engineer(
            "eng-peer", "Peer Engineer", hired_by_architect_id=architect.id
        )
        # A different engineer in the group owns the current pending question.
        self.state.update_engineer_settings(
            "torque",
            pending_question="Peer's blocking question",
            paused=True,
            _pending_question_actor_id=peer.id,
        )
        text, error = await self._call(
            "architect_engineer_answer",
            {"engineer_id": hired.id, "answer": "Answer"},
            architect.id,
        )
        self.assertTrue(error)
        self.assertEqual(text, "No pending blocking question for that engineer")
        self.assertEqual(self.handle_calls, [])

    async def test_worker_ask_is_discoverable_but_refuses_architect_answer_at_the_wrong_level(self):
        architect = self._add_architect("arch-1", "Architect")
        engineer = self._add_engineer(
            "eng-hired", "Hired Engineer", hired_by_architect_id=architect.id,
        )
        worker = self._add_worker("worker-1", "Worker", engineer.id)
        parent = self._add_task(
            "task-parent", "Implement release", lane="In Progress",
            agent_id=worker.id, assigned_engineer_id=engineer.id,
        )
        ask = self._add_task(
            "task-ask", "Which release gate applies?", labels=["torque:human"],
            parent_task_id=parent.id, reply_agent_id=worker.id,
            assigned_engineer_id=engineer.id,
        )

        read_text, read_error = await self._call(
            "architect_engineer_pending_question",
            {"engineer_id": engineer.id},
            architect.id,
        )
        self.assertFalse(read_error, read_text)
        read_payload = json.loads(read_text)
        self.assertEqual(read_payload["ask_level"], "worker")
        self.assertEqual(read_payload["ask_task_id"], ask.id)
        self.assertEqual(read_payload["question"], ask.task)
        self.assertIn("owning Engineer", read_payload["note"])

        answer_text, answer_error = await self._call(
            "architect_engineer_answer",
            {"engineer_id": engineer.id, "answer": "Use the review gate."},
            architect.id,
        )
        self.assertTrue(answer_error)
        self.assertIn("worker-level ask", answer_text)
        self.assertIn("agent_ask_answer(task=task-ask)", answer_text)
        self.assertEqual(self.handle_calls, [])



class ArchitectBindingValidationTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_architect_mod = importlib.import_module("torque.mcp_architect")
        self.mcp_architect_mod = importlib.reload(self.mcp_architect_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["torque"] = []
        return state

    def test_validate_architect_binding_requires_env_var(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            architect_id, error = self.mcp_architect_mod.validate_architect_binding()

        self.assertEqual(architect_id, "")
        self.assertEqual(error, "TORQUE_ARCHITECT_ID is required")

    def test_exit_if_invalid_architect_binding_rejects_missing_architect(self):
        state = self._make_state()
        with mock.patch.dict("os.environ", {"TORQUE_ARCHITECT_ID": "arch-missing"}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                self.mcp_architect_mod.exit_if_invalid_architect_binding(state)

        self.assertEqual(ctx.exception.code, 2)

    def test_exit_if_invalid_architect_binding_rejects_non_architect_agent(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            slug="engineer",
            group="torque",
            cell_type="agent",
            kind="engineer",
            status="running",
        )
        state.agents[engineer.id] = engineer
        state.groups["torque"].append(engineer.id)

        with mock.patch.dict("os.environ", {"TORQUE_ARCHITECT_ID": engineer.id}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                self.mcp_architect_mod.exit_if_invalid_architect_binding(state)

        self.assertEqual(ctx.exception.code, 2)

    def test_validate_architect_binding_prefers_current_cell_over_stale_architect_env(self):
        state = self._make_state()
        blueprint = self.state_mod.AgentCell(
            id="88b5b1ee",
            name="Blueprint",
            slug="blueprint",
            group="torque",
            cell_type="agent",
            kind="architect",
            status="running",
        )
        torqly = self.state_mod.AgentCell(
            id="a5a7fc9e",
            name="Torqly",
            slug="torqly",
            group="torque",
            cell_type="agent",
            kind="architect",
            status="running",
        )
        state.agents[blueprint.id] = blueprint
        state.agents[torqly.id] = torqly
        state.groups["torque"].extend([blueprint.id, torqly.id])

        with mock.patch.dict(
            "os.environ",
            {
                "TORQUE_CELL_ID": blueprint.id,
                "TORQUE_ARCHITECT_ID": torqly.id,
            },
            clear=True,
        ):
            architect_id, error = self.mcp_architect_mod.validate_architect_binding(
                state
            )

        self.assertEqual(error, "")
        self.assertEqual(architect_id, blueprint.id)

    def test_validate_architect_binding_falls_back_to_architect_env_when_cell_is_not_architect(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            slug="worker",
            group="torque",
            cell_type="agent",
            kind="worker",
            status="running",
        )
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            slug="architect",
            group="torque",
            cell_type="agent",
            kind="architect",
            status="running",
        )
        state.agents[worker.id] = worker
        state.agents[architect.id] = architect
        state.groups["torque"].extend([worker.id, architect.id])

        with mock.patch.dict(
            "os.environ",
            {"TORQUE_CELL_ID": worker.id, "TORQUE_ARCHITECT_ID": architect.id},
            clear=True,
        ):
            architect_id, error = self.mcp_architect_mod.validate_architect_binding(
                state
            )

        self.assertEqual(error, "")
        self.assertEqual(architect_id, architect.id)
