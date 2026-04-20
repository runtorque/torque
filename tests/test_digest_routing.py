import asyncio
import importlib
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


class FakeBridge:
    def __init__(self):
        self.sent = []

    async def send_text(self, session_id, text):
        self.sent.append((session_id, text))
        await asyncio.sleep(0)


class DigestRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.routing_mod = importlib.import_module("loom.digest_routing")
        self.routing_mod = importlib.reload(self.routing_mod)
        self.weaver_mod = importlib.import_module("loom.weaver")
        self.weaver_mod = importlib.reload(self.weaver_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        group = "g"
        state.groups[group] = []
        return state, group

    def _add_agent(self, state, *, agent_id, name, group, kind,
                   owner_engineer_id="", hired_by_architect_id="",
                   created_by_weaver_id="", running=True):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=agent_id,
            group=group,
            cell_type="agent",
            kind=kind,
            owner_engineer_id=owner_engineer_id,
            hired_by_architect_id=hired_by_architect_id,
            created_by_weaver_id=created_by_weaver_id,
            session_id=f"session-{agent_id}" if running else "",
            status="running" if running else "stopped",
            persistent=kind in {"engineer", "architect"},
        )
        state.agents[cell.id] = cell
        state.groups.setdefault(group, []).append(cell.id)
        return cell

    def test_resolve_worker_owner_and_hiring_architect(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id, architect.id])

    def test_resolve_worker_without_owner_returns_no_recipients(self):
        state, group = self._make_state()
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_resolve_engineer_with_and_without_architect(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )
        self.assertEqual(recipients, [engineer.id, architect.id])

        engineer.hired_by_architect_id = ""
        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )
        self.assertEqual(recipients, [engineer.id])

    def test_architect_events_do_not_generate_recipients(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": architect.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_paused_recipient_is_filtered_out(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        state.update_agent_digest_settings(engineer.id, paused=True)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_event_not_in_enabled_events_is_filtered_out(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        state.update_agent_digest_settings(
            engineer.id,
            enabled_events=["task_derived"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_dispatched"},
        )

        self.assertEqual(recipients, [])

    def test_architect_coarse_filter_blocks_non_coarse_events(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(
            engineer.id,
            enabled_events=["task_dispatched"],
        )
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["task_dispatched"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_dispatched"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_mandatory_events_bypass_enabled_events_filter(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        state.update_agent_digest_settings(engineer.id, enabled_events=[])

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_blank_cell_task_event_routes_to_assigned_engineer(self):
        state, group = self._make_state()
        legacy = self._add_agent(
            state,
            agent_id="eng-a",
            name="Legacy Engineer",
            group=group,
            kind="engineer",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-b",
            name="Owner Engineer",
            group=group,
            kind="engineer",
        )
        state.group_settings[group] = self.state_mod.GroupSettings(
            weaver_agent_id=legacy.id
        )
        task = state.board_add_task(
            "Verify release",
            group,
            id="task-1",
            assigned_engineer_id=engineer.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(legacy.id)
        state.update_agent_digest_settings(engineer.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": "",
                "group": group,
                "kind": "task_verification_updated",
                "task_id": task.id,
            },
        )

        self.assertEqual(recipients, [engineer.id])

    def test_blank_cell_task_event_routes_via_task_agent_chain(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Owner Engineer",
            group=group,
            kind="engineer",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        task = state.board_add_task(
            "Review implementation",
            group,
            id="task-1",
            agent_id=worker.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(engineer.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": "",
                "group": group,
                "kind": "task_verification_updated",
                "task_id": task.id,
            },
        )

        self.assertEqual(recipients, [engineer.id])

    async def test_per_recipient_buffer_isolation(self):
        state, group = self._make_state()
        engineer_a = self._add_agent(
            state,
            agent_id="eng-a",
            name="Engineer A",
            group=group,
            kind="engineer",
        )
        engineer_b = self._add_agent(
            state,
            agent_id="eng-b",
            name="Engineer B",
            group=group,
            kind="engineer",
        )
        worker_a = self._add_agent(
            state,
            agent_id="worker-a",
            name="Worker A",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_a.id,
        )
        worker_b = self._add_agent(
            state,
            agent_id="worker-b",
            name="Worker B",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_b.id,
        )
        state.update_agent_digest_settings(engineer_a.id)
        state.update_agent_digest_settings(engineer_b.id)

        buffer = self.weaver_mod.WeaverEventBuffer(state, FakeBridge())
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker_a.id,
                "group": group,
                "kind": "task_dispatched",
                "message": "event A",
            }
        )
        buffer.on_panel_event(
            {
                "id": 2,
                "cell_id": worker_b.id,
                "group": group,
                "kind": "task_dispatched",
                "message": "event B",
            }
        )
        await asyncio.sleep(0)

        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer_a.id]],
            ["event A"],
        )
        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer_b.id]],
            ["event B"],
        )

    async def test_pausing_one_recipient_does_not_block_another_buffer(self):
        state, group = self._make_state()
        engineer_a = self._add_agent(
            state,
            agent_id="eng-a",
            name="Engineer A",
            group=group,
            kind="engineer",
        )
        engineer_b = self._add_agent(
            state,
            agent_id="eng-b",
            name="Engineer B",
            group=group,
            kind="engineer",
        )
        worker_a = self._add_agent(
            state,
            agent_id="worker-a",
            name="Worker A",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_a.id,
        )
        worker_b = self._add_agent(
            state,
            agent_id="worker-b",
            name="Worker B",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_b.id,
        )
        state.update_agent_digest_settings(
            engineer_a.id,
            push_interval=0,
            max_interval=0,
        )
        state.update_agent_digest_settings(
            engineer_b.id,
            push_interval=0,
            max_interval=0,
        )

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        state.update_agent_digest_settings(engineer_a.id, paused=True)
        buffer.on_delivery_paused(engineer_a.id)

        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker_a.id,
                "group": group,
                "kind": "task_completed",
                "message": "event A",
            }
        )
        buffer.on_panel_event(
            {
                "id": 2,
                "cell_id": worker_b.id,
                "group": group,
                "kind": "task_completed",
                "message": "event B",
            }
        )
        await asyncio.sleep(0.05)

        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer_a.id]],
            ["event A"],
        )
        self.assertEqual(
            [event["message"] for event in buffer.get_sent_events(engineer_b.id)],
            ["event B"],
        )
        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("event B", bridge.sent[0][1])

    async def test_stopped_recipient_keeps_buffered_events_until_running(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            running=False,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(
            engineer.id,
            push_interval=0,
            max_interval=0,
        )

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker.id,
                "group": group,
                "kind": "task_completed",
                "message": "event A",
            }
        )
        await asyncio.sleep(0)

        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer.id]],
            ["event A"],
        )
        self.assertEqual(bridge.sent, [])

        engineer.session_id = "session-eng-1"
        engineer.status = "running"
        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("event A", bridge.sent[0][1])

    async def test_worker_event_creates_default_owner_digest_settings(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        self.assertNotIn(engineer.id, state.agent_digest_settings)

        buffer = self.weaver_mod.WeaverEventBuffer(state, FakeBridge())
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker.id,
                "group": group,
                "kind": "task_dispatched",
                "message": "event A",
            }
        )
        await asyncio.sleep(0)

        self.assertIn(engineer.id, state.agent_digest_settings)
