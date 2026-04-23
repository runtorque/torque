"""WS-level integration test for the compact-snapshot consumer contract.

Walks the full compact-v1 handshake + lazy-load round-trip the way the
standalone UI consumer does it:

  1. Client opts in via query (`?compact=1`) — backend flips the snapshot
     shape to the compact summary.
  2. Initial state is lean: no archived tasks, no decisions, no pending
     hires, and board_tasks entries are card summaries with no heavy fields.
  3. Client issues each lazy-load command — the server returns the full
     detail and the local task entry can be merged back to the legacy shape.

This is the contract from docs/compact-snapshot-v1.md.
"""

import asyncio
import importlib
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


COMPACT_CARD_FIELDS = {
    "id",
    "task",
    "slug",
    "group",
    "lane",
    "position",
    "action_name",
    "labels",
    "agent_id",
    "assigned_engineer_id",
    "parent_task_id",
    "pipeline_depth",
    "status",
    "health_state",
    "verification_state",
    "verification_mode",
    "lane_entered_at",
}

HEAVY_TASK_FIELDS = {
    "description",
    "messages",
    "artifacts",
    "attachments",
    "instructions",
    "context",
    "criteria",
    "action_vars",
    "health_details",
    "verification_summary",
    "verification_notes",
    "worktree_boundary",
}


class CompactSnapshotConsumerTests(unittest.TestCase):
    """Exercises the consumer-side contract: handshake → snapshot → lazy-load."""

    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module("loom.state"))
        self.server_mod = importlib.reload(
            importlib.import_module("loom.server"))

    def _populated_state(self):
        state = self.state_mod.MatrixState()
        state.groups["alpha"] = []
        state.board_tasks["task-live"] = self.state_mod.BoardTask(
            id="task-live",
            task="Ship the thing",
            group="alpha",
            lane="In Progress",
            position=3,
            action_name="feature/implement",
            labels=["performance"],
            agent_id="agent-1",
            status="on-review",
            description="full description with lots of detail",
            messages=[{"message": "progress"}],
            artifacts=[{"kind": "log", "url": "http://x"}],
            attachments=[{"kind": "pr", "url": "http://pr"}],
        )
        state.board_tasks["task-archived"] = self.state_mod.BoardTask(
            id="task-archived",
            task="Old win",
            group="alpha",
            lane=self.state_mod.ARCHIVED_LANE,
            archived_from_lane="Done",
            archived_at="2026-04-20T00:00:00+00:00",
            description="archived body",
            messages=[{"message": "closed"}],
        )
        return state

    # -- Handshake --------------------------------------------------------

    def test_handshake_query_param_opts_in(self):
        req = types.SimpleNamespace(query={"compact": "1"})
        self.assertTrue(
            self.server_mod._request_wants_compact_snapshot(req))

    def test_handshake_protocol_version_query_opts_in(self):
        req = types.SimpleNamespace(query={"protocol_version": "compact-v1"})
        self.assertTrue(
            self.server_mod._request_wants_compact_snapshot(req))

    def test_handshake_connect_resync_payload_opts_in(self):
        for data in (
            {"type": "connect", "protocol_version": "compact-v1"},
            {"cmd": "resync", "protocol_version": "compact-v1"},
            {"cmd": "resync", "compact": True},
        ):
            self.assertTrue(
                self.server_mod._payload_wants_compact_snapshot(data),
                f"expected opt-in for {data}",
            )

    def test_legacy_client_with_no_flag_stays_on_full_snapshot(self):
        req = types.SimpleNamespace(query={})
        self.assertFalse(
            self.server_mod._request_wants_compact_snapshot(req))
        self.assertFalse(
            self.server_mod._payload_wants_compact_snapshot({}))

    # -- Compact snapshot shape ------------------------------------------

    def test_compact_snapshot_omits_heavy_fields_and_archived_tasks(self):
        state = self._populated_state()
        compact = state.to_dict_compact()

        self.assertEqual(compact["snapshot_protocol"], "compact-v1")
        self.assertIn("task-live", compact["board_tasks"])
        self.assertNotIn(
            "task-archived",
            compact["board_tasks"],
            msg="archived tasks must be lazy-loaded, not in the initial frame",
        )

        card = compact["board_tasks"]["task-live"]
        self.assertEqual(set(card.keys()), COMPACT_CARD_FIELDS)
        self.assertTrue(
            HEAVY_TASK_FIELDS.isdisjoint(card.keys()),
            msg=f"card leaked heavy fields: {set(card.keys()) & HEAVY_TASK_FIELDS}",
        )

        for deferred in ("decisions", "pending_hires",
                         "weaver_journal", "weaver_worklog", "weaver_streams"):
            self.assertNotIn(
                deferred,
                compact,
                msg=f"{deferred} must be lazy-loaded, not in the initial frame",
            )

    # -- Lazy-load round-trip --------------------------------------------

    def test_task_detail_round_trip_restores_legacy_shape(self):
        state = self._populated_state()
        compact = state.to_dict_compact()
        card = compact["board_tasks"]["task-live"]

        response = self.server_mod._handle_task_detail_command(
            {"id": "task-live"},
            state,
        )

        self.assertEqual(response["type"], "task_detail")
        self.assertEqual(response["id"], "task-live")
        full = response["task"]
        # Merge the compact card into the full detail the way the consumer
        # does: full takes precedence, but local card fields survive.
        merged = {**card, **full}
        self.assertEqual(merged["description"], "full description with lots of detail")
        self.assertEqual(merged["messages"], [{"message": "progress"}])
        self.assertEqual(merged["status"], "on-review")
        self.assertEqual(merged["action_name"], "feature/implement")

    def test_archived_tasks_round_trip_is_scoped_by_group(self):
        state = self._populated_state()
        state.groups["beta"] = []
        state.board_tasks["task-beta"] = self.state_mod.BoardTask(
            id="task-beta",
            task="Beta archive",
            group="beta",
            lane=self.state_mod.ARCHIVED_LANE,
            archived_at="2026-04-21T00:00:00+00:00",
        )

        alpha = self.server_mod._handle_archived_tasks_command(
            {"group": "alpha"}, state)
        self.assertEqual(alpha["type"], "archived_tasks")
        self.assertEqual(alpha["group"], "alpha")
        self.assertEqual(set(alpha["board_tasks"]), {"task-archived"})

        beta = self.server_mod._handle_archived_tasks_command(
            {"group": "beta"}, state)
        self.assertEqual(set(beta["board_tasks"]), {"task-beta"})

    def test_decisions_and_pending_hires_round_trip(self):
        state = self.state_mod.MatrixState()

        class FakeDB:
            def load_all_decisions(self, *, include_archived=False):
                return [{"id": "d-1", "architect_id": "arch-1"}]

            def load_pending_hires(self, *, status_filter="", architect_id=""):
                self.status_filter = status_filter
                return [{"id": "h-1", "status": "pending"}]

        state.db = FakeDB()

        decisions = self.server_mod._handle_decisions_snapshot_command({}, state)
        hires = self.server_mod._handle_pending_hires_snapshot_command({}, state)

        self.assertEqual(decisions["type"], "decisions_snapshot")
        self.assertEqual(hires["type"], "pending_hires_snapshot")
        self.assertEqual(set(decisions["decisions"]), {"d-1"})
        self.assertEqual(set(hires["pending_hires"]), {"h-1"})
        # Default filter must be 'pending' per the contract.
        self.assertEqual(state.db.status_filter, "pending")

    def test_weaver_journal_round_trip_returns_group_payload(self):
        state = self.state_mod.MatrixState()
        state.groups["alpha"] = []
        state.weaver_worklog["alpha"] = [{"id": 1, "entry": "w1"}]
        state.journal_read = lambda group, limit=20, **_kw: [
            {"id": 1, "group": group, "entry": f"limit={limit}"},
        ]

        async def run():
            return await self.server_mod._handle_weaver_journal_snapshot_command(
                {
                    "group": "alpha",
                    "limit": 5,
                    "worklog_limit": 10,
                    "include_streams": False,
                },
                state,
            )

        result = asyncio.run(run())

        self.assertEqual(result["type"], "weaver_journal_snapshot")
        self.assertEqual(result["group"], "alpha")
        self.assertEqual(
            result["weaver_journal"]["alpha"],
            [{"id": 1, "group": "alpha", "entry": "limit=5"}],
        )
        self.assertEqual(
            result["weaver_worklog"]["alpha"],
            [{"id": 1, "entry": "w1"}],
        )

    # -- Delta compatibility ---------------------------------------------

    def test_delta_task_upsert_carries_full_task_for_compact_clients(self):
        """Per docs: delta task_upsert continues to carry the full BoardTask
        dict even for compact clients, so a compact consumer can merge a
        delta-only change without re-fetching task_detail."""
        from dataclasses import asdict

        state = self._populated_state()
        state.board_tasks["task-live"].status = "shipped"
        state._delta_ops = []
        state._emit("task_upsert", **asdict(state.board_tasks["task-live"]))

        upserts = [op for op in state._delta_ops
                   if op.get("op") == "task_upsert"
                   and op.get("id") == "task-live"]
        self.assertTrue(upserts, "expected a task_upsert delta op")
        upsert = upserts[0]
        # The heavy fields must ride along in the delta so compact clients
        # don't need an immediate task_detail round-trip.
        self.assertIn("description", upsert)
        self.assertIn("messages", upsert)
        self.assertEqual(upsert["status"], "shipped")


if __name__ == "__main__":
    unittest.main()
