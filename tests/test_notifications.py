import asyncio
import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class NotificationManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.base_mod = importlib.import_module("torque.adapters.base")
        self.base_mod = importlib.reload(self.base_mod)
        self.notifications_mod = importlib.import_module("torque.notifications")
        self.notifications_mod = importlib.reload(self.notifications_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.groups["other"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            notifications=True,
            notify_on_finish=True,
            notify_on_error=True,
            notify_on_attention=True,
        )
        state.group_settings["other"] = self.state_mod.GroupSettings(
            notifications=True,
            notify_on_finish=True,
        )
        return state

    async def test_build_body_handles_single_and_grouped_items(self):
        self.assertEqual(
            self.notifications_mod._build_body(
                [{"group": "g", "cell_name": "Worker", "kind": "finished"}]
            ),
            "Worker finished",
        )
        self.assertEqual(
            self.notifications_mod._build_body(
                [
                    {"group": "g", "cell_name": "Alpha", "kind": "finished"},
                    {"group": "g", "cell_name": "Beta", "kind": "finished"},
                    {"group": "g", "cell_name": "Gamma", "kind": "error"},
                ]
            ),
            "2 agents finished, Gamma error",
        )

    async def test_manager_batches_notifications_by_group(self):
        state = self._make_state()
        first = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
        )
        second = self.state_mod.AgentCell(
            id="agent-2",
            name="Beta",
            group="g",
            cell_type="agent",
        )
        other = self.state_mod.AgentCell(
            id="agent-3",
            name="Gamma",
            group="other",
            cell_type="agent",
        )
        state.agents = {first.id: first, second.id: second, other.id: other}

        manager = self.notifications_mod.NotificationManager(state)
        sent = []
        orig_send = self.notifications_mod._send_notification

        async def fake_send(title, body):
            sent.append((title, body))

        self.notifications_mod._send_notification = fake_send
        try:
            manager.on_event(
                self.base_mod.AgentEvent(
                    cell_id=first.id,
                    timestamp=1.0,
                    event_type="session_end",
                )
            )
            manager.on_event(
                self.base_mod.AgentEvent(
                    cell_id=second.id,
                    timestamp=2.0,
                    event_type="error",
                    data={"error": "boom"},
                )
            )
            manager.on_event(
                self.base_mod.AgentEvent(
                    cell_id=other.id,
                    timestamp=3.0,
                    event_type="session_end",
                )
            )

            await manager._flush(manager._pending[:])
        finally:
            self.notifications_mod._send_notification = orig_send

        self.assertEqual(
            sent,
            [
                ("Torque — g", "Alpha finished, Beta error"),
                ("Torque — other", "Gamma finished"),
            ],
        )

    async def test_manager_ignores_events_when_group_notifications_disabled(self):
        state = self._make_state()
        state.group_settings["g"].notifications = False
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
        )
        state.agents[cell.id] = cell

        manager = self.notifications_mod.NotificationManager(state)
        manager.on_event(
            self.base_mod.AgentEvent(
                cell_id=cell.id,
                timestamp=1.0,
                event_type="session_end",
            )
        )
        manager.on_health_alert(cell.id, "stuck")

        self.assertEqual(manager._pending, [])

    async def test_direct_user_message_sends_attention_notification(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        state.agents[cell.id] = cell

        manager = self.notifications_mod.NotificationManager(state)
        manager.start()
        sent = []
        orig_send = self.notifications_mod._send_notification

        async def fake_send(title, body):
            sent.append((title, body))

        self.notifications_mod._send_notification = fake_send
        try:
            queued = manager.on_direct_user_message({
                "id": "msg-1",
                "group_name": "g",
                "sender_id": cell.id,
                "sender_kind": "worker",
                "sender_name": cell.name,
                "recipient_id": "user",
                "recipient_kind": "user",
                "message": "First line\nSecond line",
                "message_type": "message",
            })
            await asyncio.sleep(0)
        finally:
            self.notifications_mod._send_notification = orig_send

        self.assertTrue(queued)
        self.assertEqual(
            sent,
            [("Torque message from Alpha", "Alpha: First line")],
        )

    async def test_direct_user_message_respects_attention_notification_settings(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        state.agents[cell.id] = cell
        manager = self.notifications_mod.NotificationManager(state)
        manager.start()
        row = {
            "id": "msg-1",
            "group_name": "g",
            "sender_id": cell.id,
            "sender_kind": "worker",
            "sender_name": cell.name,
            "recipient_id": "user",
            "recipient_kind": "user",
            "message": "Update",
            "message_type": "message",
        }

        state.group_settings["g"].notifications = False
        self.assertFalse(manager.on_direct_user_message(row))

        state.group_settings["g"].notifications = True
        state.group_settings["g"].notify_on_attention = False
        self.assertFalse(manager.on_direct_user_message(row))

    async def test_direct_user_message_ignores_user_to_agent_rows(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        state.agents[cell.id] = cell
        manager = self.notifications_mod.NotificationManager(state)
        manager.start()

        self.assertFalse(manager.on_direct_user_message({
            "id": "msg-1",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": cell.id,
            "recipient_kind": "worker",
            "message": "Please check this",
            "message_type": "message",
        }))

    async def test_direct_user_message_notification_failure_is_best_effort(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        state.agents[cell.id] = cell
        manager = self.notifications_mod.NotificationManager(state)
        manager.start()
        orig_send = self.notifications_mod._send_notification

        async def failing_send(_title, _body):
            raise RuntimeError("notification unavailable")

        self.notifications_mod._send_notification = failing_send
        try:
            with self.assertLogs("torque", level="ERROR") as logs:
                queued = manager.on_direct_user_message({
                    "id": "msg-1",
                    "group_name": "g",
                    "sender_id": cell.id,
                    "sender_kind": "worker",
                    "sender_name": cell.name,
                    "recipient_id": "user",
                    "recipient_kind": "user",
                    "message": "Update",
                    "message_type": "message",
                })
                await asyncio.sleep(0)
        finally:
            self.notifications_mod._send_notification = orig_send

        self.assertTrue(queued)
        self.assertTrue(any(
            "Failed to send direct user message notification" in message
            for message in logs.output
        ))

    async def test_task_health_alerts_do_not_publish_or_batch_operator_notifications(self):
        state = self._make_state()
        state.board_add_task(
            "Investigate silent worker",
            "g",
            lane="In Progress",
            id="task-1",
        )
        state.board_add_task(
            "Stabilize retry loop",
            "g",
            lane="In Progress",
            id="task-2",
        )

        manager = self.notifications_mod.NotificationManager(state)
        notices = []
        state.publish_operator_notice_best_effort = lambda **kwargs: notices.append(kwargs)

        manager.on_task_health_alert("task-1", "stalled")
        manager.on_task_health_alert("task-2", "thrashing")

        self.assertEqual(notices, [])
        self.assertEqual(manager._pending, [])

    async def test_task_health_suppression_leaves_other_operator_alerts_unchanged(self):
        state = self._make_state()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Alpha",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        state.agents[cell.id] = cell
        notices = []
        state.publish_operator_notice_best_effort = lambda **kwargs: notices.append(kwargs)
        # Operator notices are durable even when desktop fanout is disabled;
        # disable the latter to keep this unit test local.
        state.group_settings["g"].notifications = False
        manager = self.notifications_mod.NotificationManager(state)

        manager.on_event(self.base_mod.AgentEvent(
            cell_id=cell.id,
            timestamp=1.0,
            event_type="waiting",
        ))
        manager.on_direct_user_message({
            "id": "message-1",
            "group_name": "g",
            "sender_id": cell.id,
            "sender_kind": "worker",
            "sender_name": cell.name,
            "recipient_id": "user",
            "recipient_kind": "user",
            "message": "Need an operator decision.",
            "message_type": "message",
        })

        self.assertEqual(
            [(notice["category"], notice["dedupe_key"]) for notice in notices],
            [
                ("agent", "agent:agent-1:1.0:waiting"),
                ("direct_message", "direct-message:message-1"),
            ],
        )
