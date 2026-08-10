import importlib
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.direct_message_mirrors import NON_USER_ASK_LABEL
from torque.db import TorqueDB


class FakeActionManager:
    def __init__(self, actions=None):
        self.actions = actions or {}

    def load_action(self, name, base_dir=""):
        return dict(self.actions.get(name, {}))

    def get_transitions(self, name, base_dir=""):
        return list(self.actions.get(name, {}).get("transitions", []))

    def get_auto_close_on_done(self, name, base_dir=""):
        return bool(self.actions.get(name, {}).get("auto_close_on_done", False))

    def list_actions(self, base_dir=""):
        return []


class FakeWorktreeManager:
    async def diff_summary(self, _cell, *, non_test_only=False):
        return {"files": 0, "insertions": 0, "deletions": 0}

    async def checkpoint(self, _cell, *, message=""):
        return ""


class ServerAiReportMandatoryReviewGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, *, action_mgr=None,
                                dispatch_command=None, panel_event=None):
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
                return types.SimpleNamespace(
                    current_path="",
                    current_profile="",
                )

            async def update_session(self, *_args, **_kwargs):
                return None

        async def noop_async(*_args, **_kwargs):
            return None

        async def default_dispatch_command(data):
            if data.get("cmd") != "dispatch_task":
                return {"type": "ok"}
            task = state.board_tasks[data["id"]]
            agent_id = data.get("agent_id", "")
            if not agent_id:
                agent_id = f"derived-{task.id}"
                state.agents[agent_id] = self.state_mod.AgentCell(
                    id=agent_id,
                    name="Derived worker",
                    group=task.group,
                    cell_type="agent",
                    kind="worker",
                )
            task.agent_id = agent_id
            state.board_move_task(task.id, "In Progress")
            state.agents[agent_id].current_task_id = task.id
            return {"type": "ok", "agent_id": agent_id}

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_broadcast_toast": noop_async,
            "_checkpoint_message": lambda _cell: "checkpoint",
            "_checkpoint_on_report": noop_async,
            "_cleanup_after_merge": noop_async,
            "_close_agent_session_only": noop_async,
            "_panel_event": panel_event or (lambda *args, **kwargs: None),
            "_record_task_boundary": noop_async,
            "_resolve_base_dir": noop_async,
            "_runtime_payload": lambda: {},
            "_ownership_engineer_id_for_dispatch_source": lambda _cell: "",
            "action_mgr": action_mgr or self._default_action_mgr(),
            "bridge": DummyBridge(),
            "db": None,
            "handle_command": dispatch_command or default_dispatch_command,
            "panel_log": types.SimpleNamespace(
                replace_last=lambda *args, **kwargs: {}
            ),
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
            "worktree_mgr": FakeWorktreeManager(),
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

    def _default_action_mgr(self):
        return FakeActionManager({
            "feature/implement": {
                "implementation_depth": True,
                "review_required_above_loc": 100,
                "transitions": [{
                    "action": "feature/review",
                    "when": "implementation is complete and ready for review",
                    "status": "On Review",
                }],
            },
            "feature/review": {
                "auto_close_on_done": True,
                "transitions": [{
                    "action": "feature/fix-review",
                    "when": "issues were found",
                }],
            },
            "oneshot/fix": {
                "implementation_depth": True,
                "review_required_above_loc": 150,
                "transitions": [{
                    "action": "feature/review",
                    "when": "fix diff exceeded review gate threshold",
                    "status": "On Review",
                }],
            },
            "custom/no-review": {
                "implementation_depth": True,
                "review_required_above_loc": 100,
                "transitions": [],
            },
        })

    def _state_cell_task(self, *, kind="worker", action_name="feature/implement"):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id=f"{kind}-1",
            name=kind.title(),
            group="g",
            cell_type="agent",
            kind=kind,
            directory="/repo",
        )
        state.agents[cell.id] = cell
        task = state.board_add_task(
            "Implement mandatory gate",
            "g",
            lane="In Progress",
            id="task-1",
            action_name=action_name,
            agent_id=cell.id,
        )
        cell.current_task_id = task.id
        return state, cell, task

    def _add_review(self, state, task, *, lane="Done", message="Verdict: Ship", status=""):
        review = state.board_add_task(
            "Review mandatory gate",
            "g",
            lane=lane,
            id="task-review",
            action_name="feature/review",
            parent_task_id=task.id,
            pipeline_root_id=task.pipeline_root_id or task.id,
            pipeline_depth=task.pipeline_depth + 1,
            status=status,
            messages=[{
                "action": "done",
                "message": message,
                "agent_name": "Reviewer",
            }] if message else [],
        )
        return review

    async def _ai_done(self, state, cell, *, action_mgr=None):
        handle_command = self._extract_handle_command(
            state,
            action_mgr=action_mgr,
        )
        return await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "Implementation complete",
            "terminal_declaration": (
                "No further work is needed; I will not derive after this."
            ),
        })

    async def _ai_ask(self, state, cell, question):
        handle_command = self._extract_handle_command(state)
        return await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "ask",
            "message": question,
        })

    async def _ai_report(self, state, cell, action, message):
        handle_command = self._extract_handle_command(state)
        return await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": action,
            "message": message,
        })

    async def _mcp_progress(self, state, cell, message):
        return await self.mcp_mod._dispatch_tool(
            "torque_progress",
            {"message": message},
            cell.id,
            self._extract_handle_command(state),
            state,
        )

    def _unresolved_active_state(self):
        state, cell, root = self._state_cell_task()
        child = state.board_add_task(
            "Implement review follow-up", "g", lane="In Progress",
            id="task-child", action_name="feature/implement",
            agent_id=cell.id, parent_task_id=root.id,
            pipeline_root_id=root.id, pipeline_depth=1,
        )
        state.board_add_task(
            "Review follow-up", "g", lane="In Progress",
            id="task-review", action_name="feature/review",
            agent_id="reviewer-1", parent_task_id=child.id,
            pipeline_root_id=root.id, pipeline_depth=2,
        )
        cell.current_task_id = child.id
        linked = [
            task for task in state.board_tasks.values()
            if task.agent_id == cell.id
            and task.lane not in ("Done", "Backlog", self.state_mod.ARCHIVED_LANE)
        ]
        self.assertEqual(cell.current_task_id, child.id)
        self.assertEqual(root.agent_id, cell.id)
        self.assertEqual(child.agent_id, cell.id)
        self.assertFalse(state.task_occupies_execution_slot(root, agent_id=cell.id))
        self.assertFalse(state.task_occupies_execution_slot(child, agent_id=cell.id))
        self.assertIsNone(state.agent_current_task(cell.id))
        self.assertEqual({task.id for task in linked}, {root.id, child.id})
        self.assertIsNone(self.server_mod._resolve_ai_report_task(state, cell))
        return state, cell, root, child

    async def test_unresolved_done_and_active_ready_are_retryable_without_side_effects(self):
        for action in ("done", "ready"):
            with self.subTest(action=action):
                state, cell, root, child = self._unresolved_active_state()
                history = []
                events = []
                state.history_record_message = lambda *args, **kwargs: history.append(
                    (args, kwargs)
                )
                handle = self._extract_handle_command(
                    state,
                    panel_event=lambda *args, **kwargs: events.append((args, kwargs)),
                )
                args = {"message": "Implementation complete"} if action == "done" else {}

                response = await self.mcp_mod._dispatch_tool(
                    f"torque_{action}", args, cell.id, handle, state,
                )

                self.assertEqual(cell.current_task_id, child.id)
                self.assertEqual(root.lane, "In Progress")
                self.assertEqual(child.lane, "In Progress")
                self.assertEqual(root.messages, [])
                self.assertEqual(child.messages, [])
                self.assertFalse(any(event[0][0] == "task_completed" for event in events))
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0][0][1], action)
                self.assertEqual(history[0][1], {"task_id": "", "mark_progress": False})
                self.assertEqual(len(response), 3)
                text, is_error, cacheable = response
                self.assertTrue(is_error)
                self.assertFalse(cacheable)
                self.assertIn("No unique reportable task", text)
                self.assertIn("have the owner restore one unique reportable task", text)

    async def test_unresolved_blocked_and_error_return_explicit_partial_warning(self):
        for action in ("blocked", "error"):
            with self.subTest(action=action):
                state, cell, root, child = self._unresolved_active_state()
                history = []
                state.history_record_message = lambda *args, **kwargs: history.append(
                    (args, kwargs)
                )
                handle = self._extract_handle_command(state)
                arg_name = "reason" if action == "blocked" else "message"

                text, is_error = await self.mcp_mod._dispatch_tool(
                    f"torque_{action}", {arg_name: "Binding is broken"},
                    cell.id, handle, state,
                )
                payload = self.mcp_mod.json.loads(text)

                self.assertFalse(is_error)
                self.assertEqual(payload["type"], "warning")
                self.assertEqual(payload["status"], "partial")
                self.assertIn("Ownership must repair task attribution", payload["message"])
                self.assertTrue(cell.needs_attention)
                if action == "blocked":
                    self.assertEqual(cell.activity, "waiting")
                else:
                    self.assertEqual(cell.error_message, "Binding is broken")
                self.assertEqual(root.messages, [])
                self.assertEqual(child.messages, [])
                self.assertNotIn(f"torque:{action}", root.labels)
                self.assertNotIn(f"torque:{action}", child.labels)
                self.assertEqual(history[0][0][1:], (action, "Binding is broken"))
                self.assertEqual(history[0][1], {"task_id": ""})

    async def test_genuinely_taskless_ready_releases_without_completion_event(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id="worker-free", name="Free worker", group="g",
            cell_type="agent", kind="worker",
        )
        state.agents[cell.id] = cell
        events = []
        handle = self._extract_handle_command(
            state,
            panel_event=lambda *args, **kwargs: events.append((args, kwargs)),
        )

        text, is_error = await self.mcp_mod._dispatch_tool(
            "torque_ready", {}, cell.id, handle, state,
        )

        self.assertFalse(is_error)
        self.assertEqual(self.mcp_mod.json.loads(text)["type"], "agent_released")
        self.assertFalse(any(event[0][0] == "task_completed" for event in events))

    async def test_mapped_ai_report_missing_result_fails_closed_and_is_not_cacheable(self):
        async def missing_result(_payload):
            return None

        response = await self.mcp_mod._dispatch_tool(
            "torque_done", {"message": "Done"}, "worker-1",
            missing_result, self.state_mod.MatrixState(),
        )

        self.assertEqual(len(response), 3)
        text, is_error, cacheable = response
        self.assertTrue(is_error)
        self.assertFalse(cacheable)
        self.assertIn("returned no result", text)

    async def test_same_idempotency_key_retries_done_after_binding_repair(self):
        state, cell, root, child = self._unresolved_active_state()
        root.action_name = "custom/no-review"
        child.action_name = "custom/no-review"
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = TorqueDB(Path(temp_dir.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)
        state.db = db
        handle = self._extract_handle_command(state)
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "task_complete",
                "arguments": {
                    "message": "Implementation complete",
                    "terminal_declaration": (
                        "No further work is needed; I will not derive after this."
                    ),
                },
            },
        }

        first, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            body, cell_id=cell.id, handle_command=handle, state=state,
            idempotency_header="same-done-key",
        )

        self.assertEqual(status, 200)
        self.assertTrue(first["result"]["isError"])
        self.assertIsNone(db.load_mcp_idempotency("same-done-key"))
        root.agent_id = ""
        state.board_tasks.pop("task-review")
        self.assertTrue(state.task_occupies_execution_slot(child, agent_id=cell.id))
        self.assertIs(self.server_mod._resolve_ai_report_task(state, cell), child)
        body["id"] = 2

        second, status = await self.mcp_mod.dispatch_mcp_rpc_body(
            body, cell_id=cell.id, handle_command=handle, state=state,
            idempotency_header="same-done-key",
        )

        self.assertEqual(status, 200)
        self.assertFalse(
            second["result"]["isError"],
            second["result"]["content"],
        )
        self.assertEqual(child.lane, "Done")
        self.assertEqual(cell.current_task_id, "")
        self.assertIsNotNone(db.load_mcp_idempotency("same-done-key"))

    async def test_progress_fails_closed_when_linked_tasks_are_not_reportable(self):
        state, cell, root = self._state_cell_task()
        child = state.board_add_task(
            "Implement review follow-up",
            "g",
            lane="In Progress",
            id="task-child",
            action_name="feature/implement",
            agent_id=cell.id,
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )
        state.board_add_task(
            "Review follow-up",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            agent_id="reviewer-1",
            parent_task_id=child.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )
        cell.current_task_id = child.id
        history = []
        state.history_record_message = lambda *args, **kwargs: history.append(
            (args, kwargs)
        )

        self.assertFalse(state.task_occupies_execution_slot(root, agent_id=cell.id))
        self.assertFalse(state.task_occupies_execution_slot(child, agent_id=cell.id))
        self.assertIsNone(state.agent_current_task(cell.id))
        self.assertIsNone(self.server_mod._resolve_ai_report_task(state, cell))

        text, is_error = await self._mcp_progress(
            state, cell, "Progress that cannot be attributed"
        )

        self.assertTrue(is_error)
        self.assertIn("No unique reportable task", text)
        self.assertIn("current task task-child does not occupy", text)
        self.assertEqual(root.messages, [])
        self.assertEqual(child.messages, [])
        self.assertEqual(
            history,
            [((cell.id, "progress", "Progress that cannot be attributed"), {
                "task_id": "",
                "mark_progress": False,
            })],
        )
        self.assertEqual(cell.activity_detail, "")
        self.assertEqual(cell.last_progress_at, 0.0)

    async def test_progress_fails_closed_for_one_sided_stale_pointer(self):
        state, cell, stale_task = self._state_cell_task()
        stale_task.agent_id = ""
        history = []
        state.history_record_message = lambda *args, **kwargs: history.append(
            (args, kwargs)
        )

        self.assertFalse(
            state.task_occupies_execution_slot(stale_task, agent_id=cell.id)
        )
        self.assertIsNone(state.agent_current_task(cell.id))
        self.assertIsNone(self.server_mod._resolve_ai_report_task(state, cell))

        text, is_error = await self._mcp_progress(
            state, cell, "Progress from a stale pointer"
        )

        self.assertTrue(is_error)
        self.assertIn("No unique reportable task", text)
        self.assertIn("current task task-1 does not occupy", text)
        self.assertIn("no active task is linked", text)
        self.assertEqual(stale_task.messages, [])
        self.assertEqual(
            history,
            [((cell.id, "progress", "Progress from a stale pointer"), {
                "task_id": "",
                "mark_progress": False,
            })],
        )
        self.assertEqual(cell.activity_detail, "")
        self.assertEqual(cell.last_progress_at, 0.0)

    async def test_progress_on_single_occupying_task_is_unchanged(self):
        state, cell, task = self._state_cell_task()
        saved_task_ids = []
        state._db_save_task = lambda saved: saved_task_ids.append(saved.id)

        text, is_error = await self._mcp_progress(
            state, cell, "Progress on the active task"
        )

        self.assertFalse(is_error)
        self.assertEqual(self.mcp_mod.json.loads(text), {"type": "ok"})
        self.assertEqual(task.messages[-1]["action"], "progress")
        self.assertEqual(task.messages[-1]["message"], "Progress on the active task")
        self.assertEqual(saved_task_ids, [task.id])

    @staticmethod
    def _append_block_reply(task, block, *, delivery_state="delivered"):
        task.messages.append({
            "timestamp": block["timestamp"] + 0.5,
            "action": "block_reply",
            "message": "Use the resolved contract.",
            "worker_id": block["agent_id"],
            "block_id": block["block_id"],
            "reply_message_id": "reply-" + block["block_id"],
            "delivery_state": delivery_state,
            "acknowledged_at": 0.0,
        })

    def _pipeline_action_mgr(self):
        return FakeActionManager({
            "pipeline": {
                "transitions": [{
                    "action": "pipeline",
                    "status": "Follow-up",
                }],
            },
        })

    async def _derive_pipeline_follow_up(self, state, cell, task):
        handle_command = self._extract_handle_command(
            state, action_mgr=self._pipeline_action_mgr())
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "derive",
            "action_name": "pipeline",
            "message": "Continue pipeline",
            "task_id": task.id,
        })
        self.assertEqual(result["type"], "ok")
        return state.board_tasks[result["task_id"]]

    async def _complete_pipeline_task(self, state, cell, task, action,
                                      *, terminal_declaration=""):
        handle_command = self._extract_handle_command(
            state, action_mgr=self._pipeline_action_mgr())
        return await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": action,
            "task_id": task.id,
            "message": "Finished assigned work",
            "terminal_declaration": terminal_declaration,
        })

    async def test_ai_ask_marks_user_attention_for_architect_owned_ask(self):
        state, cell, task = self._state_cell_task(kind="architect")

        result = await self._ai_ask(state, cell, "Should we ship?")

        self.assertEqual(result["type"], "ok")
        ask = state.board_tasks[result["task_id"]]
        self.assertIn("torque:human", ask.labels)
        self.assertNotIn(NON_USER_ASK_LABEL, ask.labels)
        self.assertTrue(cell.needs_attention)
        self.assertEqual(task.status, "Awaiting Input")

    async def test_progress_acknowledges_delivered_reply_and_clears_block_signals(self):
        state, cell, task = self._state_cell_task()

        await self._ai_report(state, cell, "blocked", "Which contract?")
        block = task.messages[-1]
        self._append_block_reply(task, block)

        self.assertIn("torque:blocked", task.labels)
        self.assertEqual(cell.activity, "waiting")
        self.assertEqual(task.health_state, "blocked")

        await self._ai_report(
            state, cell, "progress", "Continuing with the ruling."
        )

        self.assertNotIn("torque:blocked", task.labels)
        self.assertEqual(cell.activity, "")
        self.assertEqual(task.health_state, "healthy")
        self.assertEqual(len(task.messages), 3)
        self.assertEqual(task.messages[0]["action"], "blocked")
        self.assertEqual(task.messages[1]["action"], "block_reply")
        self.assertTrue(task.messages[1]["acknowledged_at"])
        self.assertEqual(
            task.messages[1]["acknowledged_by_action"], "progress"
        )

    async def test_progress_keeps_block_signals_for_newer_unanswered_block(self):
        state, cell, task = self._state_cell_task()
        await self._ai_report(state, cell, "blocked", "First question?")
        first_block = task.messages[-1]
        self._append_block_reply(task, first_block)
        await self._ai_report(state, cell, "blocked", "New question?")

        await self._ai_report(state, cell, "progress", "Partial progress")

        self.assertTrue(task.messages[1]["acknowledged_at"])
        self.assertIn("torque:blocked", task.labels)
        self.assertEqual(cell.activity, "waiting")
        self.assertEqual(task.health_state, "blocked")

    async def test_progress_keeps_block_signals_for_failed_reply(self):
        for delivery_state in ("failed", "unrecoverable"):
            with self.subTest(delivery_state=delivery_state):
                state, cell, task = self._state_cell_task()
                await self._ai_report(
                    state, cell, "blocked", "Which contract?"
                )
                self._append_block_reply(
                    task, task.messages[-1],
                    delivery_state=delivery_state,
                )

                await self._ai_report(
                    state, cell, "progress", "Cannot use the ruling yet."
                )

                self.assertFalse(task.messages[1]["acknowledged_at"])
                self.assertIn("torque:blocked", task.labels)
                self.assertEqual(cell.activity, "waiting")
                self.assertEqual(task.health_state, "blocked")

    async def test_done_health_short_circuit_is_unchanged_for_unanswered_block(self):
        state, cell, task = self._state_cell_task(action_name="custom/no-review")
        await self._ai_report(state, cell, "blocked", "Which contract?")

        await self._ai_report(state, cell, "done", "Finished")

        self.assertEqual(task.lane, "Done")
        self.assertNotIn("torque:blocked", task.labels)
        self.assertEqual(cell.activity, "")
        self.assertEqual(task.health_state, "healthy")

    async def test_ai_ask_suppresses_user_attention_for_engineer_owned_worker(self):
        state, worker, task = self._state_cell_task(kind="worker")
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"].append(engineer.id)
        worker.owner_engineer_id = engineer.id

        result = await self._ai_ask(state, worker, "Can I use the fallback?")

        self.assertEqual(result["type"], "ok")
        ask = state.board_tasks[result["task_id"]]
        self.assertIn("torque:human", ask.labels)
        self.assertIn(NON_USER_ASK_LABEL, ask.labels)
        self.assertEqual(ask.assigned_engineer_id, engineer.id)
        self.assertFalse(worker.needs_attention)
        self.assertEqual(task.status, "Awaiting Input")

    async def test_worker_feature_implement_without_ship_review_is_rejected(self):
        state, cell, task = self._state_cell_task()

        # feature/implement has a derive transition, but its mandatory-review
        # gate is evaluated first.  The terminal-declaration rule therefore
        # does not create a reachable completion path while review is open.
        handle_command = self._extract_handle_command(state)
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "Implementation complete",
        })

        self.assertEqual(result["type"], "error")
        self.assertIn(
            "This is a mandatory-review task (action=feature/implement).",
            result["message"],
        )
        self.assertIn(
            'torque_derive(description="Review Implement mandatory gate", action="feature/review")',
            result["message"],
        )
        self.assertNotIn("terminal_declaration", result["message"])
        self.assertEqual(task.lane, "In Progress")

    async def test_worker_feature_implement_with_ship_review_succeeds(self):
        state, cell, task = self._state_cell_task()
        self._add_review(state, task, message="## Verdict\n**Ship** — ready")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")
        self.assertEqual(cell.current_task_id, "")

    async def test_unknown_structured_review_never_satisfies_ship_gate(self):
        state, cell, task = self._state_cell_task()
        review = self._add_review(
            state,
            task,
            message="Final review verdict: Ship",
        )
        # A recorded unknown must fail closed even if an old fallback could
        # find Ship in the durable message.
        review.completion_evidence = {"review": {"verdict": "unknown"}}

        result = await self._ai_done(state, cell)

        self.assertEqual(result["type"], "error")
        self.assertEqual(task.lane, "In Progress")

    async def test_worker_feature_implement_with_in_progress_review_is_rejected(self):
        state, cell, task = self._state_cell_task()
        self._add_review(state, task, lane="In Progress", message="Verdict: Ship")

        result = await self._ai_done(state, cell)

        self.assertEqual(result["type"], "error")
        self.assertIn(
            'torque_derive(description="Review Implement mandatory gate", action="feature/review")',
            result["message"],
        )
        self.assertEqual(task.lane, "In Progress")

    async def test_worker_oneshot_fix_done_succeeds(self):
        state, cell, task = self._state_cell_task(action_name="oneshot/fix")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_worker_action_without_feature_review_transition_succeeds(self):
        state, cell, task = self._state_cell_task(action_name="custom/no-review")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_engineer_feature_implement_done_succeeds_without_review(self):
        state, cell, task = self._state_cell_task(kind="engineer")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_done_review_with_ship_status_satisfies_gate(self):
        state, cell, task = self._state_cell_task()
        self._add_review(state, task, message="", status="Ship")

        result = await self._ai_done(state, cell)

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_done_after_derive_records_completion_but_waits_for_child(self):
        state, cell, parent = self._state_cell_task(
            action_name="pipeline")
        child = await self._derive_pipeline_follow_up(state, cell, parent)

        result = await self._complete_pipeline_task(
            state, cell, parent, "done",
            terminal_declaration=(
                "No further work is needed; I will not derive after this."
            ),
        )

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(parent.lane, "In Progress")
        self.assertEqual(parent.status, "Worker Complete — Follow-up Open")
        self.assertEqual(parent.agent_id, "")
        self.assertEqual(child.lane, "In Progress")
        self.assertEqual(cell.current_task_id, "")

        state.board_move_task(child.id, "Done")
        state.board_cascade_done(child.id)
        self.assertEqual(parent.lane, "Done")
        self.assertEqual(parent.status, "")
        self.assertIn("Terminal declaration:", parent.messages[-1]["message"])

    async def test_ready_after_derive_matches_done_and_waits_for_child(self):
        state, cell, parent = self._state_cell_task(
            action_name="pipeline")
        child = await self._derive_pipeline_follow_up(state, cell, parent)

        result = await self._complete_pipeline_task(
            state, cell, parent, "ready",
            terminal_declaration=(
                "No further work is needed; I will not derive after this."
            ),
        )

        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(parent.lane, "In Progress")
        self.assertEqual(parent.status, "Worker Complete — Follow-up Open")
        self.assertEqual(parent.agent_id, "")
        self.assertEqual(child.lane, "In Progress")
        self.assertEqual(cell.current_task_id, "")

        state.board_move_task(child.id, "Done")
        state.board_cascade_done(child.id)
        self.assertEqual(parent.lane, "Done")
        self.assertEqual(parent.status, "")

    async def test_done_requires_terminal_declaration_only_when_derive_available(self):
        state, cell, task = self._state_cell_task(action_name="pipeline")

        rejected = await self._complete_pipeline_task(state, cell, task, "done")

        self.assertEqual(rejected["type"], "error")
        self.assertIn("terminal_declaration", rejected["message"])
        self.assertIn("because this task has an available derive transition",
                      rejected["message"])
        self.assertIn("torque_done(message=", rejected["message"])
        self.assertEqual(task.lane, "In Progress")

        ready_rejected = await self._complete_pipeline_task(
            state, cell, task, "ready")
        self.assertEqual(ready_rejected["type"], "error")
        self.assertIn("terminal_declaration", ready_rejected["message"])
        self.assertIn("torque_ready(terminal_declaration=",
                      ready_rejected["message"])
        self.assertEqual(task.lane, "In Progress")

        state, cell, task = self._state_cell_task(
            action_name="custom/no-review")
        handle_command = self._extract_handle_command(state)
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "No follow-up transition exists",
        })
        self.assertFalse(result and result.get("type") == "error")
        self.assertEqual(task.lane, "Done")

    async def test_derive_after_done_is_refused_but_same_agent_can_continue_on_child(self):
        state, cell, parent = self._state_cell_task(
            action_name="pipeline")
        child = await self._derive_pipeline_follow_up(state, cell, parent)

        done = await self._complete_pipeline_task(
            state, cell, parent, "done",
            terminal_declaration=(
                "No further work is needed; I will not derive after this."
            ),
        )
        self.assertFalse(done and done.get("type") == "error")

        handle_command = self._extract_handle_command(
            state, action_mgr=self._pipeline_action_mgr())
        rejected = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "derive",
            "task_id": parent.id,
            "action_name": "pipeline",
            "message": "Too late",
        })
        self.assertEqual(rejected["type"], "error")
        self.assertIn("completed task", rejected["message"])

        child.agent_id = cell.id
        cell.current_task_id = child.id
        continued = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "derive",
            "task_id": child.id,
            "action_name": "pipeline",
            "message": "Continue on the follow-up task",
        })
        self.assertEqual(continued["type"], "ok")
        self.assertNotEqual(continued["task_id"], child.id)
