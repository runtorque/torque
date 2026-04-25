import asyncio
import importlib
import sqlite3
import sys
import time
import types
import unittest
from unittest import mock


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
        self.sent.append(text)
        await asyncio.sleep(0)


class FakeDigestDB:
    def __init__(self, *, queued=None, sent=None, fail_queue_locks=0, fail_complete_locks=0):
        self.fail_queue_locks = int(fail_queue_locks or 0)
        self.fail_complete_locks = int(fail_complete_locks or 0)
        self.health = []
        self.queued = {
            recipient_id: [dict(evt) for evt in events]
            for recipient_id, events in (queued or {}).items()
        }
        self.sent = {
            recipient_id: [dict(evt) for evt in events]
            for recipient_id, events in (sent or {}).items()
        }
        self.next_queue_id = 1
        for events in self.queued.values():
            for evt in events:
                self.next_queue_id = max(
                    self.next_queue_id,
                    int(evt.get("_digest_queue_id") or 0) + 1,
                )
        self.completed = []
        self.saved_settings = []

    def save_agent_digest_settings(self, agent_id, settings):
        self.saved_settings.append((agent_id, dict(settings)))

    def record_mcp_health_event_safe(self, **kwargs):
        self.health.append(dict(kwargs))

    def save_digest_queued_event(self, recipient_id, event, enqueued_at):
        if self.fail_queue_locks > 0:
            self.fail_queue_locks -= 1
            raise sqlite3.OperationalError("database is locked")
        row_id = self.next_queue_id
        self.next_queue_id += 1
        stored = dict(event)
        stored["_digest_queue_id"] = row_id
        stored["_digest_enqueued_at"] = enqueued_at
        self.queued.setdefault(recipient_id, []).append(stored)
        return row_id

    def load_digest_queued_events(self):
        return {
            recipient_id: [dict(evt) for evt in events]
            for recipient_id, events in self.queued.items()
        }

    def load_digest_sent_events(self, *, limit_per_recipient=200):
        result = {}
        for recipient_id, events in self.sent.items():
            result[recipient_id] = [dict(evt) for evt in events[-limit_per_recipient:]]
        return result

    def complete_digest_delivery(
        self,
        recipient_id,
        sent_events,
        queue_ids,
        *,
        sent_cap=200,
    ):
        if self.fail_complete_locks > 0:
            self.fail_complete_locks -= 1
            raise sqlite3.OperationalError("database is locked")
        self.completed.append((recipient_id, list(queue_ids)))
        remove_ids = {int(row_id) for row_id in queue_ids}
        self.queued[recipient_id] = [
            evt
            for evt in self.queued.get(recipient_id, [])
            if int(evt.get("_digest_queue_id") or 0) not in remove_ids
        ]
        for event, _enqueued_at, delivered_at in sent_events:
            stored = dict(event)
            stored["delivered_at"] = delivered_at
            self.sent.setdefault(recipient_id, []).append(stored)
        if len(self.sent.get(recipient_id, [])) > sent_cap:
            self.sent[recipient_id] = self.sent[recipient_id][-sent_cap:]


class EngineerEventBufferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.events_mod = importlib.import_module("loom.events")
        self.events_mod = importlib.reload(self.events_mod)
        self.routing_mod = importlib.import_module("loom.digest_routing")
        self.routing_mod = importlib.reload(self.routing_mod)
        self.engineer_mod = importlib.import_module("loom.engineer")
        self.engineer_mod = importlib.reload(self.engineer_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        group = "g"
        state.groups[group] = []
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            slug="engineer",
            group=group,
            cell_type="agent",
            session_id="session-1",
            status="running",
            activity="",
            kind="engineer",
            persistent=True,
        )
        state.agents[engineer.id] = engineer
        state.group_settings[group] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.engineer_settings[group] = self.state_mod.EngineerSettings(group=group)
        return state, group, engineer

    def _add_architect_digest_team(self, state, group, *,
                                   worker_owner_id="eng-assigned"):
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            slug="planner",
            group=group,
            cell_type="agent",
            kind="architect",
            persistent=True,
        )
        assigned = self.state_mod.AgentCell(
            id="eng-assigned",
            name="Assigned Engineer",
            slug="assigned-engineer",
            group=group,
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            persistent=True,
        )
        other = self.state_mod.AgentCell(
            id="eng-other",
            name="Other Engineer",
            slug="other-engineer",
            group=group,
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            persistent=True,
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker Bee",
            slug="worker-bee",
            group=group,
            cell_type="agent",
            kind="worker",
            owner_engineer_id=worker_owner_id,
        )
        for cell in (architect, assigned, other, worker):
            state.agents[cell.id] = cell
            state.groups[group].append(cell.id)
        return architect, assigned, other, worker

    def _attach_panel_log(self, state):
        panel_log = self.events_mod.PanelEventLog(max_size=20)
        state.panel_log = panel_log
        return panel_log

    async def test_persisted_digest_state_reloads_on_buffer_construction(self):
        state, _, engineer = self._make_state()
        state.db = FakeDigestDB(
            queued={
                engineer.id: [
                    {
                        "kind": "task_completed",
                        "message": "survived restart",
                        "_digest_queue_id": 7,
                        "_digest_enqueued_at": 50.0,
                    }
                ],
            },
            sent={
                engineer.id: [
                    {
                        "kind": "task_completed",
                        "message": f"sent {i}",
                        "delivered_at": 1000.0 + i,
                    }
                    for i in range(205)
                ],
            },
        )

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        stats = buffer.get_buffer_stats(engineer.id)
        self.assertEqual(stats["buffered_events"], 1)
        self.assertEqual(stats["queued_events"][0]["message"], "survived restart")
        self.assertEqual(buffer._buffer_started[engineer.id], 50.0)
        sent = buffer.get_sent_events(engineer.id)
        self.assertEqual(len(sent), 200)
        self.assertEqual(sent[0]["message"], "sent 5")
        self.assertEqual(sent[-1]["message"], "sent 204")

    async def test_manual_flush_moves_persisted_queue_rows_to_sent_history(self):
        state, group, engineer = self._make_state()
        db = FakeDigestDB()
        state.db = db
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "persist then deliver",
            })

        self.assertEqual(len(db.queued[engineer.id]), 1)
        queue_id = db.queued[engineer.id][0]["_digest_queue_id"]

        with mock.patch.object(self.engineer_mod.time, "time", return_value=120.0):
            ok, message = buffer.request_manual_flush(engineer.id)
            self.assertTrue(ok)
            self.assertEqual(message, "")
            await asyncio.sleep(0.05)

        self.assertEqual(bridge.sent and bridge.sent[0].count("persist then deliver"), 1)
        self.assertEqual(db.queued[engineer.id], [])
        self.assertEqual(db.completed, [(engineer.id, [queue_id])])
        self.assertEqual(db.sent[engineer.id][0]["message"], "persist then deliver")
        self.assertEqual(db.sent[engineer.id][0]["delivered_at"], 120.0)
        buffer.stop()


    async def test_digest_queue_and_delivery_retry_sqlite_locks(self):
        state, group, engineer = self._make_state()
        db = FakeDigestDB(fail_queue_locks=1, fail_complete_locks=1)
        state.db = db
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "lock retry digest",
            })

        self.assertEqual(len(db.queued[engineer.id]), 1)
        queue_id = db.queued[engineer.id][0]["_digest_queue_id"]

        with mock.patch.object(self.engineer_mod.time, "time", return_value=120.0):
            ok, message = buffer.request_manual_flush(engineer.id)
            self.assertTrue(ok)
            self.assertEqual(message, "")
            await asyncio.sleep(0.05)

        self.assertEqual(db.queued[engineer.id], [])
        self.assertEqual(db.completed, [(engineer.id, [queue_id])])
        self.assertEqual([evt["event"] for evt in db.health], ["retry", "retry"])
        self.assertEqual({evt["surface"] for evt in db.health}, {"digest"})
        buffer.stop()

    async def test_persisted_paused_queue_flushes_after_resume(self):
        state, _, engineer = self._make_state()
        state.agent_digest_settings[engineer.id] = self.state_mod.AgentDigestSettings(
            agent_id=engineer.id,
            paused=True,
        )
        db = FakeDigestDB(
            queued={
                engineer.id: [
                    {
                        "kind": "task_completed",
                        "message": "queued while paused",
                        "_digest_queue_id": 42,
                        "_digest_enqueued_at": 75.0,
                    }
                ],
            },
        )
        state.db = db
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])
        self.assertEqual(buffer.get_buffer_stats(engineer.id)["buffered_events"], 1)

        state.agent_digest_settings[engineer.id].paused = False
        with mock.patch.object(self.engineer_mod.time, "time", return_value=180.0):
            buffer.on_delivery_resumed(engineer.id)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("queued while paused", bridge.sent[0])
        self.assertEqual(db.queued[engineer.id], [])
        self.assertEqual(db.sent[engineer.id][0]["message"], "queued while paused")
        buffer.stop()

    async def test_simultaneous_flush_triggers_only_one_digest(self):
        state, group, engineer = self._make_state()
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 61

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._check_engineer_flush(engineer)

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])
        self.assertTrue(bridge.sent[0].strip().endswith("---"))
        self.assertNotIn("Heartbeat", bridge.sent[0])

    async def test_idle_buffered_events_wait_for_push_interval_before_flushing(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "batched",
            })

        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.engineer_mod.time, "time", return_value=159.0):
            buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.engineer_mod.time, "time", return_value=160.0):
            buffer._check_engineer_flush(engineer)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])
        buffer.stop()

    async def test_max_interval_caps_buffered_digest_delay(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            push_interval=120,
            max_interval=30,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "respect max interval",
            })

        with mock.patch.object(self.engineer_mod.time, "time", return_value=129.0):
            buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.engineer_mod.time, "time", return_value=130.0):
            buffer._check_engineer_flush(engineer)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("respect max interval", bridge.sent[0])
        buffer.stop()

    async def test_buffer_stats_count_down_while_idle_events_wait_to_flush(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "count me down",
            })

        with mock.patch.object(self.engineer_mod.time, "time", return_value=115.0):
            stats = buffer.get_buffer_stats(group)

        self.assertEqual(stats["buffered_events"], 1)
        self.assertEqual(stats["next_push_in"], 45)
        self.assertEqual(stats["queued_events"][0]["message"], "count me down")
        buffer.stop()

    async def test_manual_flush_sends_queued_events_before_push_interval(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "send me now",
            })

        ok, message = buffer.request_manual_flush(group)
        self.assertTrue(ok)
        self.assertEqual(message, "")

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("send me now", bridge.sent[0])
        self.assertEqual(buffer.get_buffer_stats(group)["buffered_events"], 0)
        sent = buffer.get_sent_events(group)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["message"], "send me now")
        self.assertGreater(sent[0]["delivered_at"], 0)
        buffer.stop()

    async def test_manual_flush_rejects_paused_delivery_without_dropping_queue(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            paused=True,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "stay queued",
        })

        ok, message = buffer.request_manual_flush(group)

        self.assertFalse(ok)
        self.assertIn("paused", message.lower())
        self.assertEqual(buffer.get_buffer_stats(group)["buffered_events"], 1)
        self.assertEqual(buffer.get_sent_events(group), [])
        self.assertEqual(bridge.sent, [])
        buffer.stop()

    async def test_overdue_idle_push_uses_digest_format(self):
        state, group, engineer = self._make_state()
        task = state.board_add_task(
            "Investigate blocked review",
            group,
            lane="In Progress",
            id="task-1",
        )
        self.assertIsNotNone(task)
        task.health_state = "blocked"
        active = self.state_mod.AgentCell(
            id="agent-2",
            name="Worker",
            slug="worker",
            group=group,
            cell_type="agent",
            activity="thinking",
        )
        state.agents[active.id] = active

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 301

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (0 events)", bridge.sent[0])
        self.assertIn("No new events since last digest.", bridge.sent[0])
        self.assertIn("Active: worker (thinking)", bridge.sent[0])
        self.assertIn("Attention: blocked: Investigate blocked review", bridge.sent[0])
        self.assertNotIn("Heartbeat", bridge.sent[0])

    async def test_idle_heartbeat_can_be_disabled(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            heartbeat_interval=0,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 600

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(bridge.sent, [])

    async def test_idle_heartbeat_does_not_duplicate_regular_event_pushes(self):
        state, group, engineer = self._make_state()
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 600

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])

    async def test_idle_heartbeat_does_not_fire_while_engineer_is_active(self):
        state, group, engineer = self._make_state()
        engineer.activity = "thinking"
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 600

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(bridge.sent, [])

    async def test_compact_digest_verbosity_truncates_event_list(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            digest_verbosity="compact",
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 61

        for idx in range(7):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": f"done {idx}",
            })

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (7 events)", bridge.sent[0])
        self.assertIn("… 2 more events", bridge.sent[0])

    async def test_detailed_digest_verbosity_includes_attention_even_with_events(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            digest_verbosity="detailed",
        )
        task = state.board_add_task(
            "Investigate blocked release",
            group,
            lane="In Progress",
            id="task-1",
        )
        self.assertIsNotNone(task)
        task.health_state = "blocked"

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 61
        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn(
            "Attention: blocked: Investigate blocked release",
            bridge.sent[0],
        )

    def test_ask_created_digest_includes_recommended_action_summary(self):
        state, group, engineer = self._make_state()
        ask = state.board_add_task(
            "Need approval to merge release branch",
            group,
            id="ask-1",
            description=(
                "Context: Smoke tests already passed.\n"
                "Recommended action: Approve merge to main after docs review.\n"
                "Background: Support already has the rollback note."
            ),
        )
        self.assertIsNotNone(ask)

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        digest = buffer._format_digest(
            group,
            [{
                "group": group,
                "kind": "ask_created",
                "message": ask.task,
                "task_id": ask.id,
            }],
            buffer._board_summary(group),
        )

        self.assertIn(
            "ask_created: Need approval to merge release branch — "
            "Approve merge to main after docs review.",
            digest,
        )
        self.assertNotIn("Smoke tests already passed", digest)

    def test_compact_ask_created_digest_clips_long_context(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            digest_verbosity="compact",
        )
        ask = state.board_add_task(
            "Need go-live approval",
            group,
            id="ask-1",
            description=(
                "Context: Approve the production rollout window after smoke "
                "tests finish in staging and support signs off on the "
                "incident playbook updates for the migration."
            ),
        )
        self.assertIsNotNone(ask)

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        digest = buffer._format_digest(
            group,
            [{
                "group": group,
                "kind": "ask_created",
                "message": ask.task,
                "task_id": ask.id,
            }],
            buffer._board_summary(group),
        )

        self.assertIn(
            "ask_created: Need go-live approval — Approve the production rollout",
            digest,
        )
        self.assertIn("…", digest)
        self.assertNotIn("support signs off", digest)

    async def test_architect_digest_uses_inject_and_rolls_up_worker_events(self):
        state, group, _engineer = self._make_state()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            slug="planner",
            group=group,
            cell_type="agent",
            session_id="session-arch",
            status="running",
            kind="architect",
            persistent=True,
        )
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer One",
            slug="engineer-one",
            group=group,
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            persistent=True,
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker Bee",
            slug="worker-bee",
            group=group,
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups[group].extend([architect.id, engineer.id, worker.id])
        task = state.board_add_task(
            "Ship architect digests",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=engineer.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(
            architect.id,
            push_interval=0,
            max_interval=0,
        )

        injected = []

        async def inject_message(target, text, **kwargs):
            injected.append((target.id, text, kwargs))

        buffer = self.engineer_mod.EngineerEventBuffer(
            state,
            FakeBridge(),
            inject_message=inject_message,
        )
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event({
            "id": 10,
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "task_completed",
            "message": "Ready (task completed)",
            "task_id": task.id,
        })

        await asyncio.sleep(0.05)

        self.assertEqual(len(injected), 1)
        self.assertEqual(injected[0][0], architect.id)
        digest = injected[0][1]
        self.assertIn("## Architect digest", digest)
        self.assertIn("### Engineer One", digest)
        self.assertIn("1 worker event: done ×1", digest)
        self.assertIn("Worker Bee: done task-1", digest)
        self.assertIn("### Pipeline activity", digest)
        self.assertIn("task-1 — Ship architect digests", digest)
        self.assertEqual(injected[0][2]["action"], "digest")

    def test_architect_digest_task_done_uses_assigned_engineer_without_owner(self):
        state, group, _engineer = self._make_state()
        _architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="",
        )
        task = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(task)
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        engineer = buffer._architect_digest_engineer({
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "task_done",
            "message": "Done",
            "task_id": task.id,
        })

        self.assertEqual(getattr(engineer, "id", ""), assigned.id)

    def test_architect_digest_task_done_prefers_assignment_over_worker_owner(self):
        state, group, _engineer = self._make_state()
        _architect, assigned, other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="eng-other",
        )
        task = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(task)
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        engineer = buffer._architect_digest_engineer({
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "task_done",
            "message": "Done",
            "task_id": task.id,
        })

        self.assertEqual(getattr(engineer, "id", ""), assigned.id)
        self.assertNotEqual(getattr(engineer, "id", ""), other.id)

    def test_architect_digest_task_scoped_events_prefer_assignment(self):
        state, group, _engineer = self._make_state()
        _architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="eng-other",
        )
        task = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(task)
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        for kind in (
            "task_blocked",
            "agent_blocked",
            "agent_error",
            "ask_created",
            "pipeline_complete",
        ):
            with self.subTest(kind=kind):
                engineer = buffer._architect_digest_engineer({
                    "group": group,
                    "cell_id": worker.id,
                    "agent_name": worker.name,
                    "kind": kind,
                    "message": kind,
                    "task_id": task.id,
                })

                self.assertEqual(getattr(engineer, "id", ""), assigned.id)

    def test_architect_digest_task_done_without_assignment_or_owner_is_unassigned(self):
        state, group, _engineer = self._make_state()
        architect, _assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="",
        )
        task = state.board_add_task(
            "Unowned task",
            group,
            id="task-1",
            agent_id=worker.id,
        )
        self.assertIsNotNone(task)
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        digest = buffer._format_digest(
            group,
            [{
                "group": group,
                "cell_id": worker.id,
                "agent_name": worker.name,
                "kind": "task_done",
                "message": "Done",
                "task_id": task.id,
            }],
            "1 task active",
            engineer=architect,
        )

        self.assertIn("### Unassigned engineer", digest)
        self.assertIn("Worker Bee: done task-1", digest)

    def test_architect_digest_task_done_and_derive_resolve_same_assignment(self):
        state, group, _engineer = self._make_state()
        _architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="eng-other",
        )
        task = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(task)
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        done_engineer = buffer._architect_digest_engineer({
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "task_done",
            "message": "Done",
            "task_id": task.id,
        })
        derive_engineer = buffer._architect_digest_engineer({
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "task_derived",
            "message": "Follow-up",
            "task_id": task.id,
        })

        self.assertEqual(getattr(done_engineer, "id", ""), assigned.id)
        self.assertEqual(getattr(derive_engineer, "id", ""), assigned.id)

    def test_architect_digest_ask_created_uses_parent_task_assignment(self):
        state, group, _engineer = self._make_state()
        _architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="",
        )
        parent = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(parent)
        ask_task = state.board_add_task(
            "Need approval",
            group,
            id="task-1.1",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        self.assertIsNotNone(ask_task)
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        engineer = buffer._architect_digest_engineer({
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "ask_created",
            "message": "Need approval",
            "task_id": ask_task.id,
        })

        self.assertEqual(getattr(engineer, "id", ""), assigned.id)

    async def test_architect_digest_fanout_task_completed_uses_task_assignment(self):
        state, group, _engineer = self._make_state()
        architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
            worker_owner_id="",
        )
        architect.session_id = "session-arch"
        architect.status = "running"
        task = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(
            architect.id,
            push_interval=0,
            max_interval=0,
        )
        injected = []

        async def inject_message(target, text, **kwargs):
            injected.append((target.id, text, kwargs))

        buffer = self.engineer_mod.EngineerEventBuffer(
            state,
            FakeBridge(),
            inject_message=inject_message,
        )
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event({
            "group": group,
            "cell_id": worker.id,
            "agent_name": worker.name,
            "kind": "task_completed",
            "message": "Done",
            "task_id": task.id,
        })
        await asyncio.sleep(0.05)

        self.assertEqual(len(injected), 1)
        digest = injected[0][1]
        self.assertIn("### Assigned Engineer", digest)
        self.assertNotIn("### Unassigned engineer", digest)
        self.assertIn("Worker Bee: done task-1", digest)

    def test_architect_digest_groups_workflow_breaches_by_subkind(self):
        state, group, _engineer = self._make_state()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            slug="planner",
            group=group,
            cell_type="agent",
            session_id="session-arch",
            status="running",
            kind="architect",
            persistent=True,
        )
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer One",
            slug="engineer-one",
            group=group,
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            persistent=True,
        )
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.groups[group].extend([architect.id, engineer.id])
        state.update_agent_digest_settings(
            architect.id,
            architect_digest=True,
        )
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        digest = buffer._format_digest(
            group,
            [
                {
                    "id": 1,
                    "group": group,
                    "cell_id": engineer.id,
                    "agent_name": engineer.name,
                    "kind": "workflow_breach",
                    "message": (
                        "escape_clause_skip: Review gate caught direct done"
                    ),
                    "task_id": "task-1",
                },
                {
                    "id": 2,
                    "group": group,
                    "cell_id": engineer.id,
                    "agent_name": engineer.name,
                    "kind": "workflow_breach",
                    "subkind": "stale_base_catch",
                    "message": "operator rebased after stale-base warning",
                    "task_id": "task-2",
                },
            ],
            "1 task active",
            engineer=architect,
        )

        self.assertIn("workflow breach/escape clause skip ×1", digest)
        self.assertIn("workflow breach/stale base catch ×1", digest)
        self.assertIn("Engineer One: workflow breach/escape clause skip", digest)
        self.assertIn("operator rebased after stale-base warning", digest)

    def test_architect_digest_terse_keeps_counts_and_pipeline_without_hints(self):
        state, group, _engineer = self._make_state()
        architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
        )
        state.group_settings[group].architect_digest_verbosity = "terse"
        task = state.board_add_task(
            "Terse digest task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        digest = buffer._format_digest(
            group,
            [{
                "id": 1,
                "group": group,
                "cell_id": worker.id,
                "agent_name": worker.name,
                "kind": "task_completed",
                "message": "A detailed completion note that should not appear",
                "task_id": task.id,
            }],
            "1 task active",
            engineer=architect,
            hints=[{"message": "Dispatch a follow-up"}],
        )

        self.assertIn("1 worker event: done ×1", digest)
        self.assertIn("### Pipeline activity", digest)
        self.assertIn("task-1 — Terse digest task", digest)
        self.assertNotIn("Worker Bee: done task-1", digest)
        self.assertNotIn("detailed completion note", digest)
        self.assertNotIn("Hints:", digest)

    def test_architect_digest_verbose_expands_worker_details(self):
        state, group, _engineer = self._make_state()
        architect, assigned, _other, worker = self._add_architect_digest_team(
            state,
            group,
        )
        state.group_settings[group].architect_digest_verbosity = "verbose"
        task = state.board_add_task(
            "Verbose digest task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        long_message = (
            "Completed implementation with a deliberately long explanation "
            "that should survive the architect verbose digest expansion and "
            "include extra diagnostic details for the architect recipient."
        )
        events = [
            {
                "id": idx,
                "group": group,
                "cell_id": worker.id,
                "agent_name": worker.name,
                "kind": "task_completed",
                "message": f"{long_message} event {idx}",
                "task_id": task.id,
            }
            for idx in range(4)
        ]
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        digest = buffer._format_digest(
            group,
            events,
            "1 task active",
            engineer=architect,
        )

        self.assertIn("4 worker events: done ×4", digest)
        self.assertIn("event 0", digest)
        self.assertIn("event 3", digest)
        self.assertNotIn("worker detail", digest)
        self.assertIn("extra diagnostic details", digest)

    def test_architect_digest_reminds_after_checkpoint_action_threshold(self):
        state, group, _engineer = self._make_state()
        architect, _assigned, _other, _worker = self._add_architect_digest_team(
            state,
            group,
        )
        state.group_settings[group].architect_journal_checkpoint_frequency = (
            "every_10_actions"
        )
        entries = [
            {
                "id": f"entry-{idx}",
                "architect_id": architect.id,
                "timestamp": 1000.0 + idx,
                "type": "observation",
                "entry": f"Observation {idx}",
            }
            for idx in range(11)
        ]
        state.architect_journal_read = (
            lambda architect_id, **_kwargs: entries
            if architect_id == architect.id else []
        )
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        digest = buffer._format_digest(
            group,
            [],
            "1 task active",
            engineer=architect,
        )

        self.assertIn(
            "Checkpoint reminder: 11 journal entries since your last checkpoint",
            digest,
        )
        self.assertIn("target every_10_actions", digest)
        self.assertIn("active engineers, open scope, pending hires", digest)

    def test_architect_digest_omits_checkpoint_reminder_below_threshold_or_manual(self):
        state, group, _engineer = self._make_state()
        architect, _assigned, _other, _worker = self._add_architect_digest_team(
            state,
            group,
        )
        entries = [
            {
                "id": f"entry-{idx}",
                "architect_id": architect.id,
                "timestamp": 1000.0 + idx,
                "type": "observation",
                "entry": f"Observation {idx}",
            }
            for idx in range(9)
        ]
        state.architect_journal_read = lambda _architect_id, **_kwargs: entries
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        state.group_settings[group].architect_journal_checkpoint_frequency = (
            "every_10_actions"
        )
        below_threshold = buffer._format_digest(
            group,
            [],
            "1 task active",
            engineer=architect,
        )
        self.assertNotIn("Checkpoint reminder:", below_threshold)

        state.group_settings[group].architect_journal_checkpoint_frequency = (
            "manual_only"
        )
        manual_only = buffer._format_digest(
            group,
            [],
            "1 task active",
            engineer=architect,
        )
        self.assertNotIn("Checkpoint reminder:", manual_only)

    def test_architect_digest_reminds_after_checkpoint_minute_threshold(self):
        state, group, _engineer = self._make_state()
        architect, _assigned, _other, _worker = self._add_architect_digest_team(
            state,
            group,
        )
        state.group_settings[group].architect_journal_checkpoint_frequency = (
            "every_20_minutes"
        )
        entries = [
            {
                "id": "recent-observation",
                "architect_id": architect.id,
                "timestamp": 2100.0,
                "type": "observation",
                "entry": "Recent activity",
            },
            {
                "id": "checkpoint",
                "architect_id": architect.id,
                "timestamp": 1000.0,
                "type": "checkpoint",
                "entry": "Last synthesis",
            },
        ]
        state.architect_journal_read = lambda _architect_id, **_kwargs: entries
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        with mock.patch.object(self.engineer_mod.time, "time",
                               return_value=1000.0 + 21 * 60):
            digest = buffer._format_digest(
                group,
                [],
                "1 task active",
                engineer=architect,
            )

        self.assertIn(
            "Checkpoint reminder: no checkpoint for 21 minutes",
            digest,
        )
        self.assertIn("target every_20_minutes", digest)
        self.assertIn("open decisions, and next moves", digest)

        no_checkpoint_entries = [
            {
                "id": "recent-observation",
                "architect_id": architect.id,
                "timestamp": 2100.0,
                "type": "observation",
                "entry": "Recent activity",
            },
            {
                "id": "oldest-observation",
                "architect_id": architect.id,
                "timestamp": 1000.0,
                "type": "observation",
                "entry": "First activity",
            },
        ]
        state.architect_journal_read = (
            lambda _architect_id, **_kwargs: no_checkpoint_entries
        )
        with mock.patch.object(self.engineer_mod.time, "time",
                               return_value=1000.0 + 22 * 60):
            digest = buffer._format_digest(
                group,
                [],
                "1 task active",
                engineer=architect,
            )
        self.assertIn(
            "Checkpoint reminder: no checkpoint for 22 minutes",
            digest,
        )

    def test_architect_digest_formats_engineer_queue_empty_compactly(self):
        state, group, _engineer = self._make_state()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            slug="planner",
            group=group,
            cell_type="agent",
            kind="architect",
            persistent=True,
        )
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Courier",
            slug="courier",
            group=group,
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            persistent=True,
        )
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.groups[group].extend([architect.id, engineer.id])
        state.update_agent_digest_settings(
            architect.id,
            architect_digest=True,
        )
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        digest = buffer._format_digest(
            group,
            [{
                "id": 1,
                "group": group,
                "cell_id": engineer.id,
                "agent_name": engineer.name,
                "kind": "engineer_queue_empty",
                "message": "",
            }],
            "0 active tasks",
            engineer=architect,
        )

        self.assertIn("### Courier", digest)
        self.assertIn("Courier: queue empty", digest)
        self.assertNotIn("queue empty — queue empty", digest)

    def test_architect_digest_caps_lines_with_elision_footer(self):
        state, group, _engineer = self._make_state()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            slug="planner",
            group=group,
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        events = []
        for idx in range(30):
            engineer = self.state_mod.AgentCell(
                id=f"eng-{idx}",
                name=f"Engineer {idx}",
                slug=f"engineer-{idx}",
                group=group,
                cell_type="agent",
                kind="engineer",
                hired_by_architect_id=architect.id,
            )
            state.agents[engineer.id] = engineer
            events.append({
                "id": idx,
                "group": group,
                "cell_id": engineer.id,
                "agent_name": engineer.name,
                "kind": "task_derived",
                "message": f"Review slice {idx}",
            })

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        digest = buffer._format_digest(
            group,
            events,
            buffer._board_summary(group),
            architect,
        )

        lines = digest.splitlines()
        self.assertLessEqual(len(lines), 40)
        self.assertRegex(digest, r"… \d+ events? elided")
        self.assertTrue(digest.endswith("---"))

    async def test_task_artifact_uploaded_digest_includes_ref_and_preview(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            enabled_events=["task_artifact_uploaded"],
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 61

        buffer.on_panel_event({
            "group": group,
            "kind": "task_artifact_uploaded",
            "agent_name": "Worker",
            "message": (
                "[test report] pytest.log — /attachments/task-1/pytest.log — "
                "15 B | uploaded report — E assert 1 == 2"
            ),
            "task_id": "task-1",
        })

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("/attachments/task-1/pytest.log", bridge.sent[0])
        self.assertIn("E assert 1 == 2", bridge.sent[0])

    async def test_board_summary_in_digest_mentions_task_health(self):
        state, group, engineer = self._make_state()
        task = state.board_add_task(
            "Investigate stalled dispatch",
            group,
            lane="In Progress",
            id="task-1",
        )
        self.assertIsNotNone(task)
        task.health_state = "stalled"

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        summary = buffer._board_summary(group)

        self.assertIn("1 In Progress", summary)
        self.assertIn("health 1 stalled", summary)
        self.assertIn("Investigate stalled dispatch (stalled)", summary)

    def test_build_engineer_system_prompt_contains_first_session_guidance(self):
        text = self.engineer_mod.build_engineer_system_prompt("g")

        self.assertIn("You are the designated engineer", text)
        self.assertIn("First session", text)
        self.assertIn("do a short reconnaissance pass before dispatching", text)
        self.assertIn("inspect the action catalog", text)
        self.assertIn("call `engineer_ask`", text)
        self.assertNotIn("Don't start dispatching tasks without human guidance.", text)

    async def test_idle_heartbeat_surfaces_stale_in_progress_attention(self):
        state, group, engineer = self._make_state()
        task = state.board_add_task(
            "Close the loop on merge",
            group,
            lane="In Progress",
            id="task-stale",
        )
        self.assertIsNotNone(task)
        task.health_state = "stale-in-progress"

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[engineer.id] = time.time() - 301

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn(
            "Attention: stale-in-progress: Close the loop on merge",
            bridge.sent[0],
        )

    async def test_idle_hint_digest_sends_without_events_and_respects_cooldown(self):
        state, group, engineer = self._make_state()
        for worker_id in ("worker-a", "worker-b"):
            worker = self.state_mod.AgentCell(
                id=worker_id,
                name=worker_id.title(),
                slug=worker_id,
                group=group,
                cell_type="agent",
                kind="worker",
                owner_engineer_id=engineer.id,
                created_by_engineer_id=engineer.id,
                status="idle",
                worktree_path=f"/repo/.loom/worktrees/{worker_id}",
                worktree_repo_root="/repo",
                worktree_branch=f"loom/{worker_id}",
                worktree_merged=True,
            )
            state.agents[worker.id] = worker
            state.groups[group].append(worker.id)

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (0 events)", bridge.sent[0])
        self.assertIn("Hints:", bridge.sent[0])
        self.assertIn("merged branches ready for cleanup", bridge.sent[0])

        buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)
        self.assertEqual(len(bridge.sent), 1)

        worker_c = self.state_mod.AgentCell(
            id="worker-c",
            name="Worker C",
            slug="worker-c",
            group=group,
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
            created_by_engineer_id=engineer.id,
            status="idle",
            worktree_path="/repo/.loom/worktrees/worker-c",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker-c",
            worktree_merged=True,
        )
        state.agents[worker_c.id] = worker_c
        state.groups[group].append(worker_c.id)

        buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 2)
        self.assertIn("3 idle agents have merged branches", bridge.sent[1])
        buffer.stop()

    async def test_due_hints_piggyback_on_buffered_event_digest(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        for worker_id in ("worker-a", "worker-b"):
            worker = self.state_mod.AgentCell(
                id=worker_id,
                name=worker_id.title(),
                slug=worker_id,
                group=group,
                cell_type="agent",
                kind="worker",
                owner_engineer_id=engineer.id,
                created_by_engineer_id=engineer.id,
                status="idle",
                worktree_path=f"/repo/.loom/worktrees/{worker_id}",
                worktree_repo_root="/repo",
                worktree_branch=f"loom/{worker_id}",
                worktree_merged=True,
            )
            state.agents[worker.id] = worker
            state.groups[group].append(worker.id)

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.engineer_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "batched with hint",
            })

        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.engineer_mod.time, "time", return_value=159.0):
            buffer._check_engineer_flush(engineer)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.engineer_mod.time, "time", return_value=160.0):
            buffer._check_engineer_flush(engineer)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])
        self.assertIn("batched with hint", bridge.sent[0])
        self.assertIn("Hints:", bridge.sent[0])
        self.assertIn("merged branches ready for cleanup", bridge.sent[0])
        buffer.stop()

    async def test_paused_engineer_buffers_events_without_flushing(self):
        state, group, engineer = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            paused=True,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._check_engineer_flush(engineer)

        await asyncio.sleep(0.05)

        self.assertEqual(buffer.get_buffer_stats(group)["buffered_events"], 1)
        self.assertEqual(bridge.sent, [])

    async def test_resume_flushes_events_buffered_while_paused_in_order(self):
        state, group, _ = self._make_state()
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            paused=True,
        )
        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "first while paused",
        })
        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "second while paused",
        })

        state.update_engineer_settings(group, paused=False)
        buffer.on_delivery_resumed(group)

        await asyncio.sleep(0.05)

        self.assertEqual(buffer.get_buffer_stats(group)["buffered_events"], 0)
        self.assertEqual(len(bridge.sent), 1)
        digest = bridge.sent[0]
        self.assertIn("## Loom Digest (2 events)", digest)
        self.assertLess(
            digest.index("first while paused"),
            digest.index("second while paused"),
        )

    async def test_engineer_ask_emits_architect_scoped_human_input_event(self):
        state, group, engineer = self._make_state()
        panel_log = self._attach_panel_log(state)
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            slug="planner",
            group=group,
            cell_type="agent",
            kind="architect",
            persistent=True,
        )
        state.agents[architect.id] = architect
        engineer.hired_by_architect_id = architect.id
        question = "Need a rollout decision? " + ("extra context " * 30)

        state.update_engineer_settings(
            group,
            pending_question=question,
            paused=True,
            _pending_question_actor_id=engineer.id,
        )

        self.assertEqual(
            state.get_engineer_settings(group).pending_question_actor_id,
            engineer.id,
        )
        events = panel_log.get_recent(10)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "engineer_awaiting_human_input")
        self.assertEqual(event["cell_id"], engineer.id)
        self.assertEqual(event["agent_name"], engineer.name)
        self.assertIn("Need a rollout decision?", event["message"])
        self.assertLessEqual(len(event["message"]), 225)
        self.assertEqual(
            self.routing_mod.resolve_digest_recipients(state, event),
            [architect.id],
        )

    async def test_engineer_ask_without_hiring_architect_has_no_digest_recipient(self):
        state, group, engineer = self._make_state()
        panel_log = self._attach_panel_log(state)

        state.update_engineer_settings(
            group,
            pending_question="Need input from the human.",
            paused=True,
            _pending_question_actor_id=engineer.id,
        )

        event = panel_log.get_recent(10)[0]
        self.assertEqual(event["kind"], "engineer_awaiting_human_input")
        self.assertEqual(
            self.routing_mod.resolve_digest_recipients(state, event),
            [],
        )

    async def test_same_question_text_with_new_actor_emits_new_engineer_ask(self):
        state, group, engineer = self._make_state()
        panel_log = self._attach_panel_log(state)
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            group=group,
            cell_type="agent",
            kind="architect",
            persistent=True,
        )
        other_engineer = self.state_mod.AgentCell(
            id="eng-2",
            name="Other Engineer",
            group=group,
            cell_type="agent",
            kind="engineer",
            hired_by_architect_id=architect.id,
            persistent=True,
        )
        state.agents[architect.id] = architect
        state.agents[other_engineer.id] = other_engineer
        engineer.hired_by_architect_id = architect.id
        question = "Same blocker text"

        with mock.patch("time.time", side_effect=[100.0, 101.0, 200.0, 201.0]):
            state.update_engineer_settings(
                group,
                pending_question=question,
                paused=True,
                _pending_question_actor_id=engineer.id,
            )
            state.update_engineer_settings(
                group,
                pending_question=question,
                paused=True,
                _pending_question_actor_id=other_engineer.id,
            )

        ws = state.get_engineer_settings(group)
        self.assertEqual(ws.pending_question_actor_id, other_engineer.id)
        self.assertEqual(ws.pending_question_set_at, 200.0)
        events = panel_log.get_recent(10)
        self.assertEqual(
            [event["cell_id"] for event in events],
            [engineer.id, other_engineer.id],
        )

    async def test_engineer_resume_emits_ask_resolved_event(self):
        state, group, engineer = self._make_state()
        panel_log = self._attach_panel_log(state)
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            group=group,
            cell_type="agent",
            kind="architect",
            persistent=True,
        )
        state.agents[architect.id] = architect
        engineer.hired_by_architect_id = architect.id
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            pending_question="Need approval",
            paused=True,
        )

        state.update_engineer_settings(
            group,
            pending_question="",
            paused=False,
            _pending_question_actor_id=engineer.id,
        )

        ws = state.get_engineer_settings(group)
        self.assertEqual(ws.pending_question, "")
        self.assertEqual(ws.pending_question_actor_id, "")
        events = panel_log.get_recent(10)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "engineer_ask_resolved")
        self.assertEqual(event["cell_id"], engineer.id)
        self.assertIn("Need approval", event["message"])
        self.assertEqual(
            self.routing_mod.resolve_digest_recipients(state, event),
            [architect.id],
        )

    async def test_group_engineer_activity_does_not_clear_actor_owned_pending_question(self):
        state, group, engineer = self._make_state()
        panel_log = self._attach_panel_log(state)
        owner = self.state_mod.AgentCell(
            id="eng-owner",
            name="Owner Engineer",
            group=group,
            cell_type="agent",
            kind="engineer",
            persistent=True,
        )
        state.agents[owner.id] = owner
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            pending_question="Owner needs input",
            pending_question_actor_id=owner.id,
            paused=True,
        )
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        buffer.on_agent_activity_change(engineer)
        engineer.activity = "thinking"
        buffer.on_agent_activity_change(engineer)

        ws = state.get_engineer_settings(group)
        self.assertEqual(ws.pending_question, "Owner needs input")
        self.assertEqual(ws.pending_question_actor_id, owner.id)
        self.assertTrue(ws.paused)
        self.assertEqual(panel_log.get_recent(10), [])

    async def test_pending_question_clears_only_after_engineer_becomes_active(self):
        state, group, engineer = self._make_state()
        panel_log = self._attach_panel_log(state)
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Planner",
            group=group,
            cell_type="agent",
            kind="architect",
            persistent=True,
        )
        state.agents[architect.id] = architect
        engineer.hired_by_architect_id = architect.id
        state.engineer_settings[group] = self.state_mod.EngineerSettings(
            group=group,
            pending_question="Need approval",
            pending_question_actor_id=engineer.id,
            paused=True,
        )
        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())

        buffer.on_agent_activity_change(engineer)
        self.assertEqual(
            state.get_engineer_settings(group).pending_question,
            "Need approval",
        )

        engineer.activity = "thinking"
        buffer.on_agent_activity_change(engineer)

        ws = state.get_engineer_settings(group)
        self.assertEqual(ws.pending_question, "")
        self.assertEqual(ws.pending_question_actor_id, "")
        self.assertFalse(ws.paused)
        events = panel_log.get_recent(10)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "engineer_ask_resolved")
        self.assertEqual(event["cell_id"], engineer.id)
        self.assertEqual(
            self.routing_mod.resolve_digest_recipients(state, event),
            [architect.id],
        )
