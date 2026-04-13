import asyncio
import importlib
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


class WeaverEventBufferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.weaver_mod = importlib.import_module("loom.weaver")
        self.weaver_mod = importlib.reload(self.weaver_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        group = "g"
        state.groups[group] = []
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            slug="weaver",
            group=group,
            cell_type="agent",
            session_id="session-1",
            status="running",
            activity="",
        )
        state.agents[weaver.id] = weaver
        state.group_settings[group] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.weaver_settings[group] = self.state_mod.WeaverSettings(group=group)
        return state, group, weaver

    async def test_simultaneous_flush_triggers_only_one_digest(self):
        state, group, weaver = self._make_state()
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 61

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._check_weaver_flush(weaver)

        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])
        self.assertTrue(bridge.sent[0].strip().endswith("---"))
        self.assertNotIn("Heartbeat", bridge.sent[0])

    async def test_idle_buffered_events_wait_for_push_interval_before_flushing(self):
        state, group, weaver = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.weaver_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "batched",
            })

        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.weaver_mod.time, "time", return_value=159.0):
            buffer._check_weaver_flush(weaver)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.weaver_mod.time, "time", return_value=160.0):
            buffer._check_weaver_flush(weaver)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])
        buffer.stop()

    async def test_max_interval_caps_buffered_digest_delay(self):
        state, group, weaver = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            push_interval=120,
            max_interval=30,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.weaver_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "respect max interval",
            })

        with mock.patch.object(self.weaver_mod.time, "time", return_value=129.0):
            buffer._check_weaver_flush(weaver)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.weaver_mod.time, "time", return_value=130.0):
            buffer._check_weaver_flush(weaver)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("respect max interval", bridge.sent[0])
        buffer.stop()

    async def test_buffer_stats_count_down_while_idle_events_wait_to_flush(self):
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.weaver_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "count me down",
            })

        with mock.patch.object(self.weaver_mod.time, "time", return_value=115.0):
            stats = buffer.get_buffer_stats(group)

        self.assertEqual(stats["buffered_events"], 1)
        self.assertEqual(stats["next_push_in"], 45)
        self.assertEqual(stats["queued_events"][0]["message"], "count me down")
        buffer.stop()

    async def test_manual_flush_sends_queued_events_before_push_interval(self):
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            push_interval=60,
            max_interval=300,
            heartbeat_interval=300,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.weaver_mod.time, "time", return_value=100.0):
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
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            paused=True,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
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
        state, group, _ = self._make_state()
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
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 301

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (0 events)", bridge.sent[0])
        self.assertIn("No new events since last digest.", bridge.sent[0])
        self.assertIn("Active: worker (thinking)", bridge.sent[0])
        self.assertIn("Attention: blocked: Investigate blocked review", bridge.sent[0])
        self.assertNotIn("Heartbeat", bridge.sent[0])

    async def test_idle_heartbeat_can_be_disabled(self):
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            heartbeat_interval=0,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 600

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(bridge.sent, [])

    async def test_idle_heartbeat_does_not_duplicate_regular_event_pushes(self):
        state, group, _ = self._make_state()
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 600

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])

    async def test_idle_heartbeat_does_not_fire_while_weaver_is_active(self):
        state, group, weaver = self._make_state()
        weaver.activity = "thinking"
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 600

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(bridge.sent, [])

    async def test_compact_digest_verbosity_truncates_event_list(self):
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            digest_verbosity="compact",
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 61

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
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
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
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 61
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
        state, group, _ = self._make_state()
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

        buffer = self.weaver_mod.WeaverEventBuffer(state, FakeBridge())
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
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
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

        buffer = self.weaver_mod.WeaverEventBuffer(state, FakeBridge())
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

    async def test_board_summary_in_digest_mentions_task_health(self):
        state, group, _ = self._make_state()
        task = state.board_add_task(
            "Investigate stalled dispatch",
            group,
            lane="In Progress",
            id="task-1",
        )
        self.assertIsNotNone(task)
        task.health_state = "stalled"

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        summary = buffer._board_summary(group)

        self.assertIn("1 In Progress", summary)
        self.assertIn("health 1 stalled", summary)
        self.assertIn("Investigate stalled dispatch (stalled)", summary)

    def test_build_weaver_system_prompt_contains_first_session_guidance(self):
        text = self.weaver_mod.build_weaver_system_prompt("g")

        self.assertIn("You are the Weaver", text)
        self.assertIn("First session", text)
        self.assertIn("do a short reconnaissance pass before dispatching", text)
        self.assertIn("inspect the action catalog", text)
        self.assertIn("call `weaver_ask`", text)
        self.assertNotIn("Don't start dispatching tasks without human guidance.", text)

    async def test_idle_heartbeat_surfaces_stale_in_progress_attention(self):
        state, group, _ = self._make_state()
        task = state.board_add_task(
            "Close the loop on merge",
            group,
            lane="In Progress",
            id="task-stale",
        )
        self.assertIsNotNone(task)
        task.health_state = "stale-in-progress"

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer._last_push[group] = time.time() - 301

        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn(
            "Attention: stale-in-progress: Close the loop on merge",
            bridge.sent[0],
        )

    async def test_idle_hint_digest_sends_without_events_and_respects_cooldown(self):
        state, group, weaver = self._make_state()
        for worker_id in ("worker-a", "worker-b"):
            worker = self.state_mod.AgentCell(
                id=worker_id,
                name=worker_id.title(),
                slug=worker_id,
                group=group,
                cell_type="agent",
                status="idle",
                worktree_path=f"/repo/.loom/worktrees/{worker_id}",
                worktree_repo_root="/repo",
                worktree_branch=f"loom/{worker_id}",
                worktree_merged=True,
            )
            state.agents[worker.id] = worker
            state.groups[group].append(worker.id)

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer._check_weaver_flush(weaver)
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (0 events)", bridge.sent[0])
        self.assertIn("Hints:", bridge.sent[0])
        self.assertIn("merged branches ready for cleanup", bridge.sent[0])

        buffer._check_weaver_flush(weaver)
        await asyncio.sleep(0.05)
        self.assertEqual(len(bridge.sent), 1)

        worker_c = self.state_mod.AgentCell(
            id="worker-c",
            name="Worker C",
            slug="worker-c",
            group=group,
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/worker-c",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker-c",
            worktree_merged=True,
        )
        state.agents[worker_c.id] = worker_c
        state.groups[group].append(worker_c.id)

        buffer._check_weaver_flush(weaver)
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 2)
        self.assertIn("3 idle agents have merged branches", bridge.sent[1])
        buffer.stop()

    async def test_due_hints_piggyback_on_buffered_event_digest(self):
        state, group, weaver = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
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
                status="idle",
                worktree_path=f"/repo/.loom/worktrees/{worker_id}",
                worktree_repo_root="/repo",
                worktree_branch=f"loom/{worker_id}",
                worktree_merged=True,
            )
            state.agents[worker.id] = worker
            state.groups[group].append(worker.id)

        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        with mock.patch.object(self.weaver_mod.time, "time", return_value=100.0):
            buffer.on_panel_event({
                "group": group,
                "kind": "task_completed",
                "message": "batched with hint",
            })

        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.weaver_mod.time, "time", return_value=159.0):
            buffer._check_weaver_flush(weaver)
        await asyncio.sleep(0.05)
        self.assertEqual(bridge.sent, [])

        with mock.patch.object(self.weaver_mod.time, "time", return_value=160.0):
            buffer._check_weaver_flush(weaver)
            await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("## Loom Digest (1 event)", bridge.sent[0])
        self.assertIn("batched with hint", bridge.sent[0])
        self.assertIn("Hints:", bridge.sent[0])
        self.assertIn("merged branches ready for cleanup", bridge.sent[0])
        buffer.stop()

    async def test_paused_weaver_buffers_events_without_flushing(self):
        state, group, weaver = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            paused=True,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event({
            "group": group,
            "kind": "task_completed",
            "message": "done",
        })
        buffer._check_weaver_flush(weaver)

        await asyncio.sleep(0.05)

        self.assertEqual(buffer.get_buffer_stats(group)["buffered_events"], 1)
        self.assertEqual(bridge.sent, [])

    async def test_resume_flushes_events_buffered_while_paused_in_order(self):
        state, group, _ = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            paused=True,
        )
        bridge = FakeBridge()
        buffer = self.weaver_mod.WeaverEventBuffer(state, bridge)
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

        state.update_weaver_settings(group, paused=False)
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

    async def test_pending_question_clears_only_after_weaver_becomes_active(self):
        state, group, weaver = self._make_state()
        state.weaver_settings[group] = self.state_mod.WeaverSettings(
            group=group,
            pending_question="Need approval",
            paused=True,
        )
        buffer = self.weaver_mod.WeaverEventBuffer(state, FakeBridge())

        buffer.on_agent_activity_change(weaver)
        self.assertEqual(
            state.get_weaver_settings(group).pending_question,
            "Need approval",
        )

        weaver.activity = "thinking"
        buffer.on_agent_activity_change(weaver)

        ws = state.get_weaver_settings(group)
        self.assertEqual(ws.pending_question, "")
        self.assertFalse(ws.paused)
