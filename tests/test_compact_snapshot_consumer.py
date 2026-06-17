"""WS-level integration test for the compact-snapshot consumer contract.

Walks the full compact-v1 handshake + lazy-load round-trip the way the
standalone UI consumer does it:

  1. Client opts in via query (`?compact=1`) — backend flips the snapshot
     shape to the compact summary.
  2. Initial state is lean: no archived tasks, no decisions, no pending
     hires, and board_tasks entries are card summaries with eager
     board-semantic fields plus a metadata-only message preview.
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
    "created_at",
    "updated_at",
    "scheduled_at",
    "dispatch_state",
    "depends_on",
    "provider",
    "external_id",
    "external_url",
    "board_sync",
    "health_state",
    "health_since",
    "health_details",
    "verification_state",
    "verification_mode",
    "messages",
    "messages_thread_summary",
    "lane_entered_at",
    "worktree_boundary",
    "resume_after_boundary_task_id",
    "deliverable_required",
    "deliverable_type",
    "requires_review",
    "pre_approved_by",
}

HEAVY_TASK_FIELDS = {
    "description",
    "artifacts",
    "attachments",
    "instructions",
    "context",
    "criteria",
    "action_vars",
    "agent_template",
    "messages_thread",
    "verification_notes",
    "verification_summary",
    "completion_evidence",
}


class CompactSnapshotConsumerTests(unittest.TestCase):
    """Exercises the consumer-side contract: handshake → snapshot → lazy-load."""

    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module("torque.state"))
        self.server_mod = importlib.reload(
            importlib.import_module("torque.server"))

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
            assigned_engineer_id="eng-1",
            created_at="2026-04-21T00:00:00+00:00",
            updated_at="2026-04-22T00:00:00+00:00",
            scheduled_at="2026-04-23T00:00:00+00:00",
            dispatch_state="live",
            depends_on=["task-root"],
            provider="github",
            external_id="123",
            external_url="https://example.test/tasks/123",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "sync_state": "idle",
                "last_synced_hash": "heavy-hash",
                "github": {"issue_number": 123, "project_item_id": "heavy"},
            },
            status="on-review",
            health_state="attention",
            health_since="2026-04-22T12:00:00+00:00",
            health_details={"reason": "recent_activity"},
            verification_mode="deploy",
            verification_state="pending",
            verification_notes="needs smoke",
            verification_summary={"tests_run": "targeted"},
            completion_evidence={
                "status": "evidence_attached",
                "sources": ["verification"],
            },
            worktree_boundary={
                "repo_root": "/tmp/repo",
                "branch": "main",
                "status": "open",
                "diff": "heavy",
                "pr": {"url": "https://example.test/pr/1", "number": 1, "body": "heavy"},
            },
            resume_after_boundary_task_id="task-boundary",
            description="full description with lots of detail",
            messages=[{"action": "progress", "message": "progress body"}],
            messages_thread=[{
                "timestamp": 123,
                "sender_agent_id": "eng-1",
                "recipient_agent_id": "agent-1",
                "content": "inline body",
                "reply_required": True,
            }],
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
        self.assertEqual(card["created_at"], "2026-04-21T00:00:00+00:00")
        self.assertEqual(card["updated_at"], "2026-04-22T00:00:00+00:00")
        self.assertEqual(card["scheduled_at"], "2026-04-23T00:00:00+00:00")
        self.assertEqual(card["dispatch_state"], "live")
        self.assertEqual(card["depends_on"], ["task-root"])
        self.assertEqual(card["provider"], "github")
        self.assertEqual(card["external_id"], "123")
        self.assertEqual(
            card["external_url"], "https://example.test/tasks/123")
        self.assertEqual(card["board_sync"], {
            "version": 1,
            "provider": "github",
            "enabled": True,
            "sync_state": "idle",
        })
        self.assertEqual(card["health_since"], "2026-04-22T12:00:00+00:00")
        self.assertEqual(card["health_details"], {"reason": "recent_activity"})
        self.assertEqual(
            card["messages"],
            [{"count": 1, "action": "progress", "message": "progress"}],
        )
        self.assertEqual(card["messages_thread_summary"], {
            "count": 1,
            "recipient_agent_ids": ["agent-1"],
            "sender_agent_ids": ["eng-1"],
            "reply_required": True,
            "last_timestamp": 123.0,
        })
        self.assertEqual(card["worktree_boundary"], {
            "repo_root": "/tmp/repo",
            "branch": "main",
            "status": "open",
            "pr": {"url": "https://example.test/pr/1", "number": 1},
        })
        self.assertEqual(
            card["resume_after_boundary_task_id"], "task-boundary")

        for deferred in ("decisions", "pending_hires",
                         "engineer_journal", "engineer_worklog", "engineer_streams"):
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
        self.assertEqual(
            merged["messages"],
            [{"action": "progress", "message": "progress body"}],
        )
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

    def test_engineer_journal_round_trip_returns_author_payload(self):
        state = self.state_mod.MatrixState()
        state.groups["alpha"] = []
        state.agents["eng-a"] = self.state_mod.AgentCell(
            id="eng-a",
            name="Engineer A",
            group="alpha",
            kind="engineer",
        )
        state.engineer_worklog["alpha"] = [{"id": 1, "entry": "w1"}]
        state.journal_read = lambda group, limit=20, **kw: [
            {
                "id": 1,
                "group": group,
                "entry": f"author={kw.get('author_cell_id')} limit={limit}",
                "author_cell_id": kw.get("author_cell_id"),
            },
        ]

        async def run():
            return await self.server_mod._handle_engineer_journal_snapshot_command(
                {
                    "group": "alpha",
                    "limit": 5,
                    "worklog_limit": 10,
                    "include_streams": False,
                },
                state,
            )

        result = asyncio.run(run())

        self.assertEqual(result["type"], "engineer_journal_snapshot")
        self.assertEqual(result["group"], "alpha")
        self.assertEqual(
            result["engineer_journal"]["eng-a"],
            [{
                "id": 1,
                "group": "alpha",
                "entry": "author=eng-a limit=5",
                "author_cell_id": "eng-a",
            }],
        )
        self.assertEqual(
            result["engineer_worklog"]["alpha"],
            [{"id": 1, "entry": "w1"}],
        )

    # -- Size budget -----------------------------------------------------

    def test_compact_card_preserves_size_reduction_under_v2_contract(self):
        """Lock the phase-2 goal: even with the v2-eager fields added to the
        summary, a production-sized task's compact card must keep at least
        95% of the phase-1 98-99% size reduction (i.e. >= ~94% smaller than
        the legacy full dict)."""
        from dataclasses import asdict
        import json as _json

        long_msg = "x" * 400
        description = ("Background and context for this task. " * 200).strip()
        messages = [
            {
                "timestamp": 1000 + i,
                "action": "progress",
                "agent_name": "worker-" + str(i),
                "message": "step " + str(i) + " " + long_msg,
            }
            for i in range(80)
        ]
        artifacts = [
            {
                "id": "artifact-" + str(i),
                "type": "log",
                "title": "log " + str(i),
                "path": "/tmp/run-" + str(i) + ".log",
                "storage": {"kind": "path", "content": "y" * 2000},
                "summary": ("summary line " * 50).strip(),
                "metadata": {"boundary_recorded_at": "2026-04-22T12:00:00+00:00"},
            }
            for i in range(10)
        ]
        attachments = [
            {
                "id": "att-" + str(i),
                "type": "file_ref",
                "filename": "file-" + str(i) + ".txt",
                "content": "attachment body " * 200,
            }
            for i in range(6)
        ]
        instructions = ("Do the thing. " * 80).strip()
        context_text = ("Relevant context lines " * 80).strip()
        criteria = ("Acceptance criteria bullet " * 50).strip()
        action_vars = {
            "target_module": "torque.state",
            "notes": "some notes " * 100,
            "long": "z" * 2000,
        }

        task = self.state_mod.BoardTask(
            id="TORQUE:200",
            task="perf: finalize compact consumer",
            slug="perf-finalize-compact-consumer",
            group="torque",
            lane="In Progress",
            position=42,
            action_name="feature/implement",
            labels=["performance", "p1"],
            agent_id="agent-uuid-000000000000",
            assigned_engineer_id="eng-uuid-000000000000",
            created_at="2026-04-22T22:00:00+00:00",
            updated_at="2026-04-22T23:30:03.758052+00:00",
            depends_on=["TORQUE:120"],
            provider="github",
            external_id="123",
            external_url="https://github.com/x/y/issues/123",
            health_state="healthy",
            health_since="2026-04-22T23:30:03.758052+00:00",
            health_details={"reasons": ["recent_activity"]},
            verification_state="pending",
            verification_mode="deploy",
            verification_notes="needs smoke on review env",
            verification_summary={"tests_run": "targeted"},
            lane_entered_at="2026-04-22T23:30:03.758052+00:00",
            worktree_boundary={
                "repo_root": "/repo",
                "branch": "feature/x",
                "status": "open",
            },
            description=description,
            messages=messages,
            artifacts=artifacts,
            attachments=attachments,
            instructions=instructions,
            context=context_text,
            criteria=criteria,
            action_vars=action_vars,
            agent_template="engineer-default",
        )

        legacy_bytes = len(
            _json.dumps(asdict(task), default=str).encode("utf-8"))
        compact_bytes = len(
            _json.dumps(
                self.state_mod.board_task_compact(task), default=str
            ).encode("utf-8"))

        reduction = 1.0 - (compact_bytes / legacy_bytes)
        # ≥95% of the phase-1 ~99.09% reduction = 94.14%. Pad slightly to
        # 0.94 so the test stays robust against tiny shape/format changes.
        self.assertGreaterEqual(
            reduction,
            0.94,
            msg=(
                f"compact reduction degraded to {reduction*100:.2f}% "
                f"({compact_bytes} / {legacy_bytes}) — v2 must keep "
                "≥94% reduction vs the legacy dict"
            ),
        )
        # Absolute per-card cap: stay under 3 KB even after adding the 13
        # v2-eager fields. Budget in the frontend render path assumes cards
        # are cheap enough to iterate without pagination overhead.
        self.assertLess(
            compact_bytes,
            3_000,
            msg=f"compact per-card size {compact_bytes} B exceeds 3 KB budget",
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
