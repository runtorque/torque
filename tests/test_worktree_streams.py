import importlib
import sys
import types
import unittest
from datetime import datetime, timezone


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


class WorktreeStreamTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.streams_mod = importlib.import_module("loom.worktree_streams")
        self.streams_mod = importlib.reload(self.streams_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def _add_agent(self, state, *, agent_id="agent-1", branch="loom/worker",
                   current_task_id="", status="running",
                   last_event_at="2026-04-07T12:00:00+00:00"):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status=status,
            worktree_path=f"/repo/.loom/worktrees/{agent_id}",
            worktree_repo_root="/repo",
            worktree_branch=branch,
            git_root="/repo",
            current_task_id=current_task_id,
            last_event_at=_ts(last_event_at),
        )
        state.agents[cell.id] = cell
        state.groups["g"].append(cell.id)
        return cell

    def _task(self, task_id, title, *, lane="Done", action_name="feature/implement",
              parent_task_id="", pipeline_root_id="", pipeline_depth=0,
              agent_id="", reply_agent_id="", labels=None, status="",
              created_at="2026-04-07T10:00:00+00:00",
              updated_at="2026-04-07T10:00:00+00:00",
              boundary=None, resume_after="", verification_state="",
              verification_summary=None, verification_notes="",
              verification_updated_at="", messages=None):
        return self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group="g",
            lane=lane,
            action_name=action_name,
            parent_task_id=parent_task_id,
            pipeline_root_id=pipeline_root_id,
            pipeline_depth=pipeline_depth,
            agent_id=agent_id,
            reply_agent_id=reply_agent_id,
            labels=list(labels or []),
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            lane_entered_at=created_at,
            worktree_boundary=dict(boundary or {}),
            resume_after_boundary_task_id=resume_after,
            verification_state=verification_state,
            verification_summary=dict(verification_summary or {}),
            verification_notes=verification_notes,
            verification_updated_at=verification_updated_at,
            messages=list(messages or []),
        )

    def test_single_product_task_with_clean_review_is_ready_to_merge(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")

        product = self._task(
            "LOOM:1",
            "Add Events tab",
            agent_id=worker.id,
            updated_at="2026-04-07T10:30:00+00:00",
        )
        review = self._task(
            "LOOM:1:1",
            "Review Events implementation",
            lane="Done",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            agent_id=worker.id,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="loom/worker",
        )

        self.assertEqual(stream["product_task_ids"], [product.id])
        self.assertEqual(stream["workflow_task_ids"], [review.id])
        self.assertEqual(stream["state"], "ready_to_merge")
        self.assertEqual(stream["code_state"], "reviewed_clean")
        self.assertEqual(stream["merge_state"], "ready")
        self.assertEqual(stream["latest_boundary_task_id"], review.id)
        self.assertEqual(stream["latest_reviewed_commit_sha"], "abc123")
        self.assertTrue(stream["partial_review_safe"])
        self.assertFalse(stream["branch_advanced"])

    def test_multiple_product_tasks_on_one_branch_collapse_into_one_stream(self):
        state = self._make_state()

        product_one = self._task("LOOM:1", "Add Events tab")
        review = self._task(
            "LOOM:1:1",
            "Review Events implementation",
            action_name="feature/review",
            parent_task_id=product_one.id,
            pipeline_root_id=product_one.id,
            pipeline_depth=1,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "review123",
            },
        )
        product_two = self._task(
            "LOOM:2",
            "Add Worklog tab",
            lane="To Do",
            created_at="2026-04-07T12:00:00+00:00",
            updated_at="2026-04-07T12:00:00+00:00",
            resume_after=review.id,
        )
        state.board_tasks[product_one.id] = product_one
        state.board_tasks[review.id] = review
        state.board_tasks[product_two.id] = product_two

        streams = self.streams_mod.compute_worktree_streams(state)

        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["product_task_ids"], [product_one.id, product_two.id])
        self.assertEqual(streams[0]["queued_task_ids"], [product_two.id])
        self.assertEqual(streams[0]["state"], "implementing")

    def test_review_blocker_loop_becomes_fixing_blockers(self):
        state = self._make_state()
        worker = self._add_agent(
            state,
            current_task_id="LOOM:1:2",
            status="running",
            last_event_at="2026-04-07T12:45:00+00:00",
        )

        product = self._task(
            "LOOM:1",
            "Add Events tab",
            lane="Done",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
                "recorded_by_agent_id": worker.id,
            },
        )
        review = self._task(
            "LOOM:1:1",
            "Review Events implementation",
            lane="In Progress",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            status="Fixing",
            created_at="2026-04-07T11:10:00+00:00",
            updated_at="2026-04-07T11:20:00+00:00",
        )
        blocker_fix = self._task(
            "LOOM:1:2",
            "Fix the issues found",
            lane="In Progress",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=product.id,
            pipeline_depth=2,
            agent_id=worker.id,
            created_at="2026-04-07T11:30:00+00:00",
            updated_at="2026-04-07T12:40:00+00:00",
            resume_after=product.id,
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        state.board_tasks[blocker_fix.id] = blocker_fix

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="loom/worker",
        )

        self.assertEqual(stream["state"], "fixing_blockers")
        self.assertEqual(stream["code_state"], "review_blocked")
        self.assertEqual(stream["active_blocker_task_id"], blocker_fix.id)
        self.assertEqual(stream["foreground_task_id"], blocker_fix.id)
        self.assertIn(blocker_fix.id, stream["workflow_task_ids"])

    def test_validation_pending_branch_is_awaiting_human_validation(self):
        state = self._make_state()

        product = self._task(
            "LOOM:1",
            "Add Events tab",
            verification_state="pending",
            verification_summary={
                "human_validation_pending": "Live/manual Weaver-panel smoke pending",
            },
            verification_updated_at="2026-04-07T11:45:00+00:00",
        )
        review = self._task(
            "LOOM:1:1",
            "Review Events implementation",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "review123",
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="loom/worker",
        )

        self.assertEqual(stream["state"], "awaiting_human_validation")
        self.assertEqual(stream["code_state"], "reviewed_clean")
        self.assertEqual(stream["validation_state"], "pending_human_validation")
        self.assertEqual(
            stream["gate_reason"],
            "Live/manual Weaver-panel smoke pending",
        )
        self.assertEqual(stream["recommended_next_action"], "merge_after_validation")

    def test_merge_conflict_stream_marks_branch_advanced_and_not_partial_review_safe(self):
        state = self._make_state()
        worker = self._add_agent(
            state,
            current_task_id="LOOM:1:2",
            status="running",
            last_event_at="2026-04-07T12:30:00+00:00",
        )

        product = self._task("LOOM:1", "Add Events tab")
        review = self._task(
            "LOOM:1:1",
            "Review Events implementation",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "review123",
                "recorded_by_agent_id": worker.id,
            },
        )
        merge_fix = self._task(
            "LOOM:1:2",
            "Resolve merge conflict in state.py",
            lane="In Progress",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=product.id,
            pipeline_depth=2,
            agent_id=worker.id,
            created_at="2026-04-07T12:00:00+00:00",
            updated_at="2026-04-07T12:20:00+00:00",
            resume_after=review.id,
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        state.board_tasks[merge_fix.id] = merge_fix

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="loom/worker",
        )

        self.assertEqual(stream["state"], "fixing_blockers")
        self.assertEqual(stream["code_state"], "merge_conflict")
        self.assertEqual(stream["active_blocker_task_id"], merge_fix.id)
        self.assertTrue(stream["branch_advanced"])
        self.assertFalse(stream["partial_review_safe"])
        self.assertEqual(stream["recommended_next_action"], "resolve_merge_conflict")

    def test_visibility_items_surface_without_becoming_product_tasks(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")

        product = self._task(
            "LOOM:1",
            "Add Events tab",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
            },
        )
        visibility = self._task(
            "LOOM:9",
            "Weaver: reprioritize blocker fix",
            lane="Done",
            action_name="",
            reply_agent_id=worker.id,
            labels=["loom:weaver-message"],
            created_at="2026-04-07T11:10:00+00:00",
            updated_at="2026-04-07T11:12:00+00:00",
            messages=[
                {
                    "timestamp": _ts("2026-04-07T11:11:00+00:00"),
                    "action": "weaver_message",
                    "message": "Reprioritized blocker fix before queued work",
                    "agent_name": "Weaver",
                },
                {
                    "timestamp": _ts("2026-04-07T11:12:00+00:00"),
                    "action": "reply",
                    "message": "Will handle blocker first",
                    "agent_name": "Worker",
                },
            ],
        )
        state.board_tasks[product.id] = product
        state.board_tasks[visibility.id] = visibility

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="loom/worker",
        )

        self.assertEqual(stream["product_task_ids"], [product.id])
        self.assertEqual(stream["workflow_task_ids"], [])
        self.assertEqual(
            [item["kind"] for item in stream["recent_visibility_items"]],
            ["agent_reply", "weaver_message"],
        )
        self.assertEqual(
            stream["recent_visibility_items"][1]["summary"],
            "Reprioritized blocker fix before queued work",
        )

