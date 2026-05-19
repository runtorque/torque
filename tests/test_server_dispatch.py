import importlib
import types
import unittest
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ServerDispatchObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.dispatch_mod = importlib.import_module("torque.server_dispatch")
        self.dispatch_mod = importlib.reload(self.dispatch_mod)

    def _state(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        return state

    def _task(self, task_id="probe", *, group="g", lane="To Do", **kwargs):
        return self.state_mod.BoardTask(
            id=task_id,
            task=f"Task {task_id}",
            group=group,
            lane=lane,
            **kwargs,
        )

    def test_stream_resume_allowed_logs_autonomy_gates(self):
        current = {
            "queue_items": [{
                "task_id": "task-1",
                "queue_state": "ready_to_resume",
                "deps_met": True,
            }],
        }

        with self.assertLogs("torque", level="INFO") as suggest_logs:
            allowed = self.dispatch_mod._stream_resume_allowed(
                types.SimpleNamespace(autonomy_mode="suggest_only"),
                previous_stream=None,
                current_stream=current,
            )
        self.assertFalse(allowed)
        suggest_text = "\n".join(suggest_logs.output)
        self.assertIn("reason=autonomy_suggest_only", suggest_text)
        self.assertIn("autonomy_mode=suggest_only", suggest_text)

        previous = {
            "queue_items": [{
                "task_id": "task-1",
                "queue_state": "ready_to_resume",
                "deps_met": True,
            }],
        }
        with self.assertLogs("torque", level="INFO") as transition_logs:
            allowed = self.dispatch_mod._stream_resume_allowed(
                types.SimpleNamespace(autonomy_mode="dispatch_when_clear"),
                previous_stream=previous,
                current_stream=current,
            )
        self.assertFalse(allowed)
        self.assertIn(
            "reason=no_ready_transition",
            "\n".join(transition_logs.output),
        )

    async def test_maybe_auto_resume_stream_logs_early_return_reasons(self):
        scenarios = []

        state = self._state()
        scenarios.append((state, None, "no_current_stream", "g"))

        state = self._state()
        scenarios.append((
            state,
            {"stream_id": "stream:g", "group": "g", "queue_items": []},
            "no_ready_task",
            "g",
        ))

        state = self._state()
        state.board_tasks["ready"] = self._task("ready", lane="Done")
        scenarios.append((state, self._ready_stream(), "ready_task_closed", "g"))

        state = self._state()
        state.board_tasks["ready"] = self._task("ready", lane="In Progress")
        scenarios.append((
            state,
            self._ready_stream(),
            "ready_task_not_actionable",
            "g",
        ))

        state = self._state()
        state.board_tasks["dep"] = self._task("dep", lane="In Progress")
        state.board_tasks["ready"] = self._task(
            "ready",
            lane="To Do",
            depends_on=["dep"],
        )
        scenarios.append((state, self._ready_stream(), "deps_not_met", "g"))

        state = self._state()
        state.board_tasks["ready"] = self._task("ready", group="", lane="To Do")
        scenarios.append((
            state,
            {"stream_id": "stream:empty", "queue_items": [{
                "task_id": "ready",
                "queue_state": "ready_to_resume",
                "deps_met": True,
            }]},
            "missing_stream_group",
            "",
        ))

        async def handle_command(payload):
            raise AssertionError(f"dispatch should not fire: {payload}")

        for state, stream, expected_reason, group in scenarios:
            probe = self._task("probe", group=group or "g")
            with mock.patch.object(
                self.dispatch_mod,
                "compute_worktree_stream_for_task",
                return_value=stream,
            ):
                with self.assertLogs("torque", level="INFO") as logs:
                    result = await self.dispatch_mod._maybe_auto_resume_stream(
                        state,
                        handle_command,
                        lambda *args, **kwargs: None,
                        task=probe,
                        group=group,
                    )
            self.assertIsNone(result)
            self.assertIn(
                f"reason={expected_reason}",
                "\n".join(logs.output),
            )

    def _ready_stream(self):
        return {
            "stream_id": "stream:g",
            "group": "g",
            "queue_items": [{
                "task_id": "ready",
                "queue_state": "ready_to_resume",
                "deps_met": True,
            }],
        }

    def test_stream_resume_target_logs_no_eligible_agent(self):
        state = self._state()
        state.agents["wrong-group"] = self.state_mod.AgentCell(
            id="wrong-group",
            name="Wrong Group",
            group="other",
            cell_type="agent",
        )
        task = self._task("ready", agent_id="wrong-group")
        stream = {"agent_id": "missing-agent"}

        with self.assertLogs("torque", level="INFO") as logs:
            target = self.dispatch_mod._stream_resume_target_agent_id(
                state,
                stream,
                task,
            )

        self.assertEqual(target, "")
        text = "\n".join(logs.output)
        self.assertIn("no eligible target", text)
        self.assertIn("missing-agent", text)
        self.assertIn("missing_or_non_agent", text)
        self.assertIn("group_mismatch", text)

    async def test_auto_dispatch_queue_logs_capacity_deferral(self):
        state = self._state()
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            current_task_id="active",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        state.board_add_task(
            "Active",
            "g",
            lane="In Progress",
            id="active",
            agent_id=worker.id,
        )
        state.board_add_task("Queued", "g", lane="To Do", id="queued")
        state.auto_dispatch_queue_add("g", "queued", max_concurrent=1)

        async def handle_command(payload):
            raise AssertionError(f"dispatch should defer: {payload}")

        with self.assertLogs("torque", level="INFO") as logs:
            dispatched = await self.dispatch_mod._pump_auto_dispatch_queue(
                state,
                handle_command,
                lambda *args, **kwargs: None,
                group="g",
            )

        self.assertEqual(dispatched, [])
        text = "\n".join(logs.output)
        self.assertIn("capacity reached for group=g", text)
        self.assertIn("1/1", text)
        self.assertIn("deferring task=queued", text)

    async def test_auto_dispatch_queue_keeps_entry_and_emits_failure_on_dispatch_error(self):
        state = self._state()
        state.board_add_task("Queued", "g", lane="To Do", id="queued")
        state.auto_dispatch_queue_add("g", "queued", max_concurrent=1)
        panel_events = []

        async def handle_command(payload):
            return {"type": "error", "message": "transient dispatch failure"}

        with self.assertLogs("torque", level="WARNING") as logs:
            dispatched = await self.dispatch_mod._pump_auto_dispatch_queue(
                state,
                handle_command,
                lambda *args, **kwargs: panel_events.append((args, kwargs)),
                group="g",
            )

        self.assertEqual(dispatched, [])
        self.assertEqual(state.auto_dispatch_queues["g"][0].task_id, "queued")
        text = "\n".join(logs.output)
        self.assertIn("auto-dispatch failed for group=g task=queued", text)
        self.assertIn("keeping queued entry for retry", text)
        self.assertEqual(panel_events[0][0][0], "task_auto_dispatch_failed")
        self.assertEqual(panel_events[0][1]["task_id"], "queued")
        self.assertIn("keeping it queued", panel_events[0][0][4])

    async def test_auto_dispatch_queue_logs_empty_queue_with_bound_followups_once(self):
        state = self._state()
        state.board_add_task(
            "Bound followup",
            "g",
            lane="To Do",
            id="queued",
            agent_id="worker-1",
        )

        with self.assertLogs("torque", level="INFO") as logs:
            dispatched = await self.dispatch_mod._pump_auto_dispatch_queue(
                state,
                lambda *_args, **_kwargs: None,
                lambda *args, **kwargs: None,
                group="g",
            )

        self.assertEqual(dispatched, [])
        text = "\n".join(logs.output)
        self.assertIn("auto-dispatch queue empty for group=g", text)
        self.assertIn("queued", text)


if __name__ == "__main__":
    unittest.main()
