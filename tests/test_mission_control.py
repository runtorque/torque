import importlib
import types
import unittest
from dataclasses import asdict
from datetime import datetime, timezone

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class MissionControlSummaryTests(unittest.TestCase):
    def setUp(self):
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mc = importlib.import_module("torque.mission_control")
        self.mc = importlib.reload(self.mc)
        self.state = self.state_mod.MatrixState()
        self.state.add_group("Torque")
        self.state.add_group("Other")
        self.now = 1_800_000_000.0

    def add_task(self, title, *, group="Torque", lane="In Progress", id=None, **kw):
        task = self.state.board_add_task(
            title,
            group,
            lane=lane,
            id=id or title.lower().replace(" ", "-"),
            **kw,
        )
        self.assertIsNotNone(task)
        return task

    def summary(self, **kw):
        return self.mc.build_mission_control_summary(
            self.state,
            group=kw.pop("group", "Torque"),
            now_ts=kw.pop("now_ts", self.now),
            streams=kw.pop("streams", []),
            deploy_state=kw.pop("deploy_state", None),
            **kw,
        )

    @staticmethod
    def cards(summary, section):
        return summary["sections"][section]["items"]

    def test_summary_contract_fixed_sections_stable_ids_sorting_and_no_state_mutation(self):
        ask = self.add_task("Needs answer", id="ask-1", labels=["torque:human"])
        ask.updated_at = iso(self.now - 10)
        older = self.add_task("Older ask", id="ask-2", labels=["torque:human"])
        older.updated_at = iso(self.now - 5)
        before_tasks = {tid: asdict(task) for tid, task in self.state.board_tasks.items()}
        before_agents = {aid: asdict(agent) for aid, agent in self.state.agents.items()}

        summary = self.summary()

        self.assertEqual(summary["type"], "mission_control_summary")
        self.assertEqual(summary["version"], 1)
        self.assertEqual(
            list(summary["sections"].keys()),
            ["needs_operator_now", "at_risk_watchlist", "in_flight", "recently_completed"],
        )
        cards = self.cards(summary, "needs_operator_now")
        self.assertEqual([card["id"] for card in cards], ["mc:task:ask-2:ask", "mc:task:ask-1:ask"])
        self.assertEqual(cards[0]["recommended_next_action"], "answer_ask")
        self.assertEqual(before_tasks, {tid: asdict(task) for tid, task in self.state.board_tasks.items()})
        self.assertEqual(before_agents, {aid: asdict(agent) for aid, agent in self.state.agents.items()})
        self.assertEqual(summary["source_freshness"]["tasks"]["state"], "ok")

    def test_ready_to_merge_and_deploy_pending_are_operator_now(self):
        stream = {
            "stream_id": "stream:repo:feature",
            "group": "Torque",
            "branch": "feature",
            "state": "ready_to_merge",
            "merge_state": "ready",
            "product_task_ids": ["TORQUE:1"],
            "foreground_task_id": "TORQUE:1",
            "foreground_task_title": "Feature",
            "last_activity_at": iso(self.now - 1),
        }
        deploy = {"pending_deploy": {"count": 2, "torque_task_ids": ["TORQUE:9"]}}

        summary = self.summary(streams=[stream], deploy_state=deploy)

        ids = [card["id"] for card in self.cards(summary, "needs_operator_now")]
        self.assertIn("mc:stream:stream:repo:feature:ready_to_merge", ids)
        self.assertIn("mc:deploy:Torque:pending", ids)
        actions = {card["id"]: card["recommended_next_action"] for card in self.cards(summary, "needs_operator_now")}
        self.assertEqual(actions["mc:stream:stream:repo:feature:ready_to_merge"], "merge_ready_stream")
        self.assertEqual(actions["mc:deploy:Torque:pending"], "record_deploy_or_relaunch")

    def test_verification_failed_and_live_smoke_manual_validation(self):
        failed = self.add_task(
            "Failed verification",
            id="verify-failed",
            verification_state="failed",
            verification_notes="Tests failed",
        )
        smoke = self.add_task(
            "Needs smoke",
            id="smoke",
            verification_state="attempted",
            verification_summary={
                "live_smoke_pending": True,
                "human_validation_pending": "Open the app",
            },
        )

        summary = self.summary()

        cards = {card["id"]: card for card in self.cards(summary, "needs_operator_now")}
        self.assertEqual(
            cards[f"mc:task:{failed.id}:failed_verification"]["recommended_next_action"],
            "investigate_failed_verification",
        )
        self.assertEqual(
            cards[f"mc:task:{smoke.id}:manual_validation"]["recommended_next_action"],
            "perform_live_smoke",
        )
        self.assertIn("operator_validation_required", cards[f"mc:task:{smoke.id}:manual_validation"]["caveat_chips"])

    def test_engineer_pending_question_is_operator_now(self):
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            group="Torque",
            cell_type="agent",
            kind="engineer",
        )
        self.state.agents[engineer.id] = engineer
        self.state.engineer_settings["Torque"] = self.state_mod.EngineerSettings(
            group="Torque",
            pending_question="Approve the merge?",
            pending_question_actor_id=engineer.id,
            pending_question_set_at=self.now - 30,
        )

        summary = self.summary()

        card = next(
            card for card in self.cards(summary, "needs_operator_now")
            if card["id"] == "mc:engineer:eng-1:question"
        )
        self.assertEqual(card["recommended_next_action"], "answer_engineer_question")
        self.assertEqual(card["reason"], "Approve the merge?")

    def test_health_risks_retained_worktree_and_sync_error_watchlist_only_error_state(self):
        blocked = self.add_task("Blocked", id="blocked")
        blocked.health_state = "blocked"
        idle = self.add_task("Idle", id="idle")
        idle.health_state = "idle-risk"
        sync_error = self.add_task(
            "Sync error",
            id="sync-error",
            board_sync={"sync_state": "error", "last_error": "boom"},
        )
        self.add_task(
            "Sync queued",
            id="sync-queued",
            board_sync={"sync_state": "queued", "last_error": "not yet"},
        )
        agent = self.state_mod.AgentCell(
            id="worker-1",
            name="Merged Worker",
            group="Torque",
            cell_type="agent",
            kind="worker",
            worktree_path="/tmp/worktree",
            worktree_branch="feature",
            worktree_merged=True,
        )
        self.state.agents[agent.id] = agent

        summary = self.summary()

        ids = [card["id"] for card in self.cards(summary, "at_risk_watchlist")]
        self.assertIn(f"mc:task:{blocked.id}:health:blocked", ids)
        self.assertIn(f"mc:task:{idle.id}:health:idle-risk", ids)
        self.assertIn(f"mc:task:{sync_error.id}:sync_error", ids)
        self.assertIn("mc:agent:worker-1:retained_merged_worktree", ids)
        self.assertNotIn("mc:task:sync-queued:sync_error", ids)
        cleanup = next(card for card in self.cards(summary, "at_risk_watchlist") if card["id"] == "mc:agent:worker-1:retained_merged_worktree")
        self.assertEqual(cleanup["recommended_next_action"], "clean_retained_merged_worktree")
        self.assertIn("no_cleanup_button", cleanup["caveat_chips"])

    def test_recent_completed_default_window_and_include_flag(self):
        recent = self.add_task("Recent", id="recent", lane="Done")
        recent.lane_entered_at = iso(self.now - 60)
        recent.updated_at = recent.lane_entered_at
        old = self.add_task("Old", id="old", lane="Done")
        old.lane_entered_at = iso(self.now - (8 * 24 * 60 * 60))
        old.updated_at = old.lane_entered_at

        summary = self.summary()
        ids = [card["primary_task_id"] for card in self.cards(summary, "recently_completed")]
        self.assertEqual(ids, [recent.id])

        hidden = self.summary(include_recent_completed=False)
        self.assertEqual(self.cards(hidden, "recently_completed"), [])

    def test_review_needed_only_explicit_review_or_implemented_no_review_boundary(self):
        review = self.add_task("Review it", id="review", action_name="feature/review")
        no_review_boundary = self.add_task("Implemented", id="impl")
        no_review_boundary.health_details = {"reasons": ["implemented_no_review_boundary"]}
        branch_ahead_only = self.add_task("Branch ahead", id="ahead")
        branch_ahead_only.worktree_boundary = {"branch": "feature", "head_sha": "abc"}

        summary = self.summary()

        ids = [card["id"] for card in self.cards(summary, "needs_operator_now")]
        self.assertIn(f"mc:task:{review.id}:review_needed", ids)
        self.assertIn(f"mc:task:{no_review_boundary.id}:review_needed", ids)
        self.assertNotIn(f"mc:task:{branch_ahead_only.id}:review_needed", ids)

    def test_in_flight_healthy_active_work_and_group_scope_no_peer_leakage(self):
        active = self.add_task("Active", id="active")
        self.add_task("Other active", id="other-active", group="Other")
        self.state.peer_ack_threads = {"secret": {"task_ids": ["other-active"]}}

        summary = self.summary()

        ids = [card["primary_task_id"] for card in self.cards(summary, "in_flight")]
        self.assertEqual(ids, [active.id])
        serialized = repr(summary)
        self.assertNotIn("other-active", serialized)
        self.assertNotIn("secret", serialized)
        self.assertEqual(summary["scope"]["peer_ack_details"], "omitted_without_caller_scoped_context")

    def test_source_freshness_error_isolation_when_streams_raise(self):
        original = self.mc.compute_worktree_streams

        def boom(*_args, **_kwargs):
            raise RuntimeError("stream exploded")

        self.mc.compute_worktree_streams = boom
        self.addCleanup(lambda: setattr(self.mc, "compute_worktree_streams", original))

        summary = self.mc.build_mission_control_summary(
            self.state,
            group="Torque",
            now_ts=self.now,
        )

        self.assertEqual(summary["type"], "mission_control_summary")
        self.assertEqual(summary["source_freshness"]["streams"]["state"], "error")
        self.assertIn("stream exploded", summary["source_freshness"]["streams"]["error"])


class MissionControlServerCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )
        closure_values = {name: None for name in handle_code.co_freevars}
        closure_values.update({"handle_command": None, "state": state})
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

    async def test_get_mission_control_command_inputs_limits_and_result_type(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        task = state.board_add_task("Needs answer", "Torque", id="ask-1", labels=["torque:human"])
        self.assertIsNotNone(task)
        seen = {"prefill": [], "streams": [], "deploy": []}

        def fake_deploy(arg_state, group):
            seen["deploy"].append((arg_state, group))
            return {"pending_deploy": {"count": 0, "torque_task_ids": []}}

        async def fake_prefill(arg_state, *, group=""):
            seen["prefill"].append((arg_state, group))

        def fake_streams(arg_state, *, group="", visibility_limit=10, include_orphaned=True):
            seen["streams"].append((arg_state, group, visibility_limit, include_orphaned))
            return []

        original_deploy = self.server_mod.architect_deploy_state_payload
        original_prefill = self.server_mod.prefill_branch_exists_for_state
        original_streams = self.server_mod.compute_worktree_streams
        self.server_mod.architect_deploy_state_payload = fake_deploy
        self.server_mod.prefill_branch_exists_for_state = fake_prefill
        self.server_mod.compute_worktree_streams = fake_streams
        self.addCleanup(lambda: setattr(self.server_mod, "architect_deploy_state_payload", original_deploy))
        self.addCleanup(lambda: setattr(self.server_mod, "prefill_branch_exists_for_state", original_prefill))
        self.addCleanup(lambda: setattr(self.server_mod, "compute_worktree_streams", original_streams))
        handle_command = self._extract_handle_command(state)

        result = await handle_command({
            "cmd": "get_mission_control",
            "group": "Torque",
            "limit_per_section": "2",
            "include_recent_completed": "false",
            "recent_completed_seconds": "60",
        })

        self.assertEqual(result["type"], "mission_control_summary")
        self.assertEqual(result["group"], "Torque")
        self.assertEqual(result["limits"]["limit_per_section"], 2)
        self.assertFalse(result["limits"]["include_recent_completed"])
        self.assertEqual(result["limits"]["recent_completed_seconds"], 60)
        self.assertEqual(seen["deploy"], [(state, "Torque")])
        self.assertEqual(seen["prefill"], [(state, "Torque")])
        self.assertEqual(seen["streams"], [(state, "Torque", 2, False)])

        missing_group = await handle_command({"cmd": "get_mission_control"})
        self.assertEqual(missing_group["type"], "error")
        self.assertIn("group required", missing_group["message"])

    async def test_get_mission_control_isolates_deploy_and_stream_errors(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")

        def bad_deploy(_state, _group):
            raise RuntimeError("deploy bad")

        async def bad_prefill(_state, *, group=""):
            raise RuntimeError("prefill bad")

        def bad_streams(*_args, **_kwargs):
            raise RuntimeError("streams bad")

        original_deploy = self.server_mod.architect_deploy_state_payload
        original_prefill = self.server_mod.prefill_branch_exists_for_state
        original_streams = self.server_mod.compute_worktree_streams
        self.server_mod.architect_deploy_state_payload = bad_deploy
        self.server_mod.prefill_branch_exists_for_state = bad_prefill
        self.server_mod.compute_worktree_streams = bad_streams
        self.addCleanup(lambda: setattr(self.server_mod, "architect_deploy_state_payload", original_deploy))
        self.addCleanup(lambda: setattr(self.server_mod, "prefill_branch_exists_for_state", original_prefill))
        self.addCleanup(lambda: setattr(self.server_mod, "compute_worktree_streams", original_streams))
        handle_command = self._extract_handle_command(state)

        result = await handle_command({"cmd": "get_mission_control", "group": "Torque"})

        self.assertEqual(result["type"], "mission_control_summary")
        self.assertEqual(result["source_freshness"]["deploy_state"]["state"], "error")
        self.assertIn("deploy bad", result["source_freshness"]["deploy_state"]["error"])
        self.assertEqual(result["source_freshness"]["branch_cache"]["state"], "error")
        self.assertEqual(result["source_freshness"]["streams"]["state"], "error")
        self.assertIn("streams bad", result["source_freshness"]["streams"]["error"])


if __name__ == "__main__":
    unittest.main()
