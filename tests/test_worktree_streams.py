import asyncio
import copy
import importlib
import sys
import types
import tempfile
import subprocess
import unittest
from unittest import mock
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.streams_mod = importlib.import_module("torque.worktree_streams")
        self.streams_mod = importlib.reload(self.streams_mod)
        self.readiness_mod = importlib.import_module(
            "torque.worktree_stream_readiness"
        )
        self.readiness_mod = importlib.reload(self.readiness_mod)


    def test_branch_prefill_async_timeout_is_bounded(self):
        class HangingProc:
            returncode = None

            def __init__(self):
                self.killed = False

            async def communicate(self):
                await asyncio.sleep(60)
                return b"", b""

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        proc = HangingProc()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            self.readiness_mod,
            "_BRANCH_EXISTS_GIT_TIMEOUT_SECONDS",
            0.01,
        ), mock.patch(
            "torque.worktree_stream_readiness.asyncio.create_subprocess_exec",
            new=mock.AsyncMock(return_value=proc),
        ):
            branches = asyncio.run(
                self.streams_mod._list_repo_branches_async(tmp)
            )

        self.assertEqual(branches, frozenset())
        self.assertTrue(proc.killed)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def test_exported_prefill_path_handles_empty_state(self):
        class EmptyState:
            agents = {}
            board_tasks = {}

            @staticmethod
            def iter_active_agents():
                return iter(())

        asyncio.run(
            self.streams_mod.prefill_branch_exists_for_state(EmptyState())
        )

    def _add_agent(self, state, *, agent_id="agent-1", branch="torque/worker",
                   current_task_id="", status="running",
                   last_event_at="2026-04-07T12:00:00+00:00"):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status=status,
            worktree_path=f"/repo/.torque/worktrees/{agent_id}",
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
              verification_updated_at="", messages=None,
              completion_evidence=None):
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
            completion_evidence=dict(completion_evidence or {}),
        )

    def test_single_product_task_with_clean_review_is_ready_to_merge(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            agent_id=worker.id,
            updated_at="2026-04-07T10:30:00+00:00",
            verification_state="passed",
            verification_summary={
                "tests_run": "pytest tests/test_worktree_streams.py",
                "test_outcome": "full_suite_passed",
                "deploy_attempted": False,
                "live_smoke_pending": True,
            },
            verification_notes="Worker-context deploy skipped.",
            verification_updated_at="2026-04-07T11:25:00+00:00",
        )
        review = self._task(
            "TORQUE:1:1",
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
                "branch": "torque/worker",
                "base_branch": "main",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
                "stale_base": {
                    "stale": False,
                    "source": "merge_readiness_check",
                    "base_branch": "main",
                    "merge_clean": True,
                },
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "source_action": "done",
                    "recorded_at": "2026-04-07T11:30:00+00:00",
                }
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/worker", "main", {
                "state": "fresh", "stale": False,
                "source": "merge_readiness_check", "merge_clean": True,
                "branch_head": "abc123",
            },
        )

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
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
        packet = stream["merge_readiness"]
        self.assertEqual(packet["state"], "ready_to_merge")
        self.assertEqual(packet["recommended_next_action"], "merge")
        self.assertEqual(packet["recommended_tool"], "worktree_merge")
        self.assertEqual(stream["recommended_tool"], "worktree_merge")
        self.assertEqual(packet["product_task_ids"], [product.id])
        self.assertEqual(packet["workflow_task_ids"], [review.id])
        self.assertEqual(packet["active_workflow_task_ids"], [])
        self.assertEqual(
            packet["latest_reviewed_boundary"]["task_id"],
            review.id,
        )
        self.assertEqual(
            packet["head"]["reviewed_boundary_sha"],
            "abc123",
        )
        self.assertEqual(
            packet["head"]["current_branch_head_sha"],
            "abc123",
        )
        self.assertTrue(
            packet["head"]["current_branch_head_sha_verified"],
        )
        self.assertEqual(
            packet["head"]["current_branch_head_sha_source"],
            "merge_readiness_check",
        )
        self.assertEqual(packet["review_final"]["verdict"], "ship")
        self.assertEqual(packet["verification"]["state"], "validated")
        self.assertEqual(
            packet["verification"]["summary"]["test_outcome"],
            "full_suite_passed",
        )
        self.assertIn("- PR: <pr_url>", packet["merge_report_snippet"])

    def test_rebased_reviewed_stream_uses_verified_head_and_requires_rereview(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")
        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            agent_id=worker.id,
            verification_state="passed",
            completion_evidence={
                "completion": {"message": "Implementation complete."},
            },
        )
        review = self._task(
            "TORQUE:1:1",
            "Review Events implementation",
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
                "branch": "torque/worker",
                "base_branch": "main",
                "status": "open",
                "commit_sha": "reviewed123",
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "source_action": "done",
                }
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        evidence_before = {
            product.id: copy.deepcopy(product.completion_evidence),
            review.id: copy.deepcopy(review.completion_evidence),
        }
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/worker", "main", {
                "state": "fresh",
                "stale": False,
                "source": "merge_readiness_check",
                "merge_clean": True,
                "branch_head": "rebased456",
            },
        )

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        packet = stream["merge_readiness"]
        self.assertEqual(
            packet["head"]["reviewed_boundary_sha"],
            "reviewed123",
        )
        self.assertEqual(
            packet["head"]["current_branch_head_sha"],
            "rebased456",
        )
        self.assertEqual(
            packet["head"]["current_branch_head_sha_source"],
            "merge_readiness_check",
        )
        self.assertTrue(stream["branch_advanced"])
        self.assertFalse(stream["partial_review_safe"])
        self.assertEqual(packet["followups"]["started_count"], 0)
        self.assertNotEqual(stream["state"], "ready_to_merge")
        self.assertEqual(stream["merge_state"], "not_ready")
        self.assertEqual(stream["recommended_next_action"], "re-review")
        self.assertEqual(stream["expected_next_transition"], "re-review")
        self.assertIn("advanced beyond", stream["gate_reason"])
        self.assertEqual(packet["review_final"]["verdict"], "ship")
        self.assertEqual(product.completion_evidence, evidence_before[product.id])
        self.assertEqual(review.completion_evidence, evidence_before[review.id])

    def test_unverified_reviewed_stream_fails_closed_without_assuming_head(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")
        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            agent_id=worker.id,
            verification_state="passed",
            completion_evidence={
                "completion": {"message": "Implementation complete."},
            },
        )
        review = self._task(
            "TORQUE:1:1",
            "Review Events implementation",
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
                "branch": "torque/worker",
                "base_branch": "main",
                "status": "open",
                "commit_sha": "reviewed123",
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "source_action": "done",
                }
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        evidence_before = {
            product.id: copy.deepcopy(product.completion_evidence),
            review.id: copy.deepcopy(review.completion_evidence),
        }
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/worker", "main", {
                "state": "stale",
                "stale": True,
                "source": "merge_readiness_check",
                "merge_clean": True,
            },
        )

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        packet = stream["merge_readiness"]
        self.assertEqual(
            packet["head"]["reviewed_boundary_sha"],
            "reviewed123",
        )
        self.assertEqual(packet["head"]["current_branch_head_sha"], "")
        self.assertEqual(
            packet["head"]["current_branch_head_sha_source"],
            "unknown",
        )
        self.assertFalse(packet["head"]["current_branch_head_sha_verified"])
        self.assertEqual(stream["state"], "merge_readiness_unknown")
        self.assertEqual(stream["merge_state"], "not_ready")
        self.assertEqual(
            stream["recommended_next_action"],
            "check_merge_readiness",
        )
        self.assertEqual(packet["state"], "merge_readiness_unknown")
        self.assertEqual(
            packet["recommended_next_action"],
            "check_merge_readiness",
        )
        self.assertIn("head", stream["gate_reason"].lower())
        self.assertEqual(packet["review_final"]["verdict"], "ship")
        self.assertEqual(product.completion_evidence, evidence_before[product.id])
        self.assertEqual(review.completion_evidence, evidence_before[review.id])

    def test_merge_readiness_exposes_worker_attested_completion_deviation(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")
        product = self._task(
            "TORQUE:2",
            "Implement stream evidence",
            agent_id=worker.id,
            completion_evidence={
                "completion": {
                    "action": "done",
                    "acceptance_deviation": {
                        "statement": "Delivered an API contract only.",
                        "reason": "The UI integration needs product review first.",
                        "agent_id": worker.id,
                        "agent_name": worker.name,
                        "recorded_at": "2026-04-07T10:30:00+00:00",
                    },
                },
            },
        )
        review = self._task(
            "TORQUE:2:1",
            "Review stream evidence",
            lane="Done",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            agent_id=worker.id,
            boundary={
                "version": "1", "repo_root": "/repo",
                "branch": "torque/worker", "base_branch": "main",
                "status": "open", "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123", "recorded_by_agent_id": worker.id,
            },
            completion_evidence={"review": {"verdict": "ship"}},
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        packet = self.streams_mod.compute_worktree_stream(
            state, repo_root="/repo", branch="torque/worker",
        )["merge_readiness"]

        self.assertEqual(packet["completion_deviations"], [{
            "task_id": product.id,
            "task_title": product.task,
            "statement": "Delivered an API contract only.",
            "reason": "The UI integration needs product review first.",
            "agent_id": worker.id,
            "agent_name": worker.name,
            "recorded_at": "2026-04-07T10:30:00+00:00",
        }])

    def test_merge_readiness_exposes_incomplete_deviation_attempt_without_attesting_it(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")
        product = self._task(
            "TORQUE:2a",
            "Implement stream evidence",
            agent_id=worker.id,
            completion_evidence={
                "completion": {
                    "action": "ready",
                    "acceptance_deviation_attempt": {
                        "statement": "Delivered the API only.",
                        "reason": "",
                        "missing_fields": ["reason"],
                        "agent_id": worker.id,
                        "agent_name": worker.name,
                        "recorded_at": "2026-04-07T10:30:00+00:00",
                    },
                },
            },
        )
        state.board_tasks[product.id] = product

        packet = self.streams_mod.compute_worktree_stream(
            state, repo_root="/repo", branch="torque/worker",
        )["merge_readiness"]

        self.assertEqual(packet["completion_deviations"], [])
        self.assertEqual(packet["completion_deviation_disclosure_attempts"], [{
            "task_id": product.id,
            "task_title": product.task,
            "statement": "Delivered the API only.",
            "reason": "",
            "missing_fields": ["reason"],
            "agent_id": worker.id,
            "agent_name": worker.name,
            "recorded_at": "2026-04-07T10:30:00+00:00",
        }])

    def test_pending_pr_stream_exposes_pr_metadata_without_looking_merged(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")

        product = self._task(
            "TORQUE:1",
            "Add PR merge flow",
            agent_id=worker.id,
            updated_at="2026-04-07T10:30:00+00:00",
        )
        review = self._task(
            "TORQUE:1:1",
            "Review PR merge flow",
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
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "reviewed-head",
                "recorded_by_agent_id": worker.id,
                "stale_base": {
                    "stale": False,
                    "source": "merge_readiness_check",
                    "base_branch": "main",
                    "merge_clean": True,
                },
                "pr": {
                    "provider": "github",
                    "remote": "origin",
                    "base_branch": "main",
                    "head_branch": "torque/worker",
                    "head_sha": "reviewed-head",
                    "url": "https://github.com/acme/repo/pull/123",
                    "number": 123,
                    "state": "auto_merge_enabled",
                    "merge_state": "BLOCKED",
                },
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/worker", "main", {
                "state": "fresh", "stale": False,
                "source": "merge_readiness_check", "merge_clean": True,
            },
        )

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "ready_to_merge")
        self.assertEqual(stream["merge_state"], "ready")
        self.assertEqual(stream["latest_boundary_status"], "open")
        self.assertEqual(stream["latest_reviewed_commit_sha"], "reviewed-head")
        self.assertEqual(stream["latest_merged_commit_sha"], "")
        self.assertEqual(
            stream["pr_url"],
            "https://github.com/acme/repo/pull/123",
        )
        self.assertEqual(stream["pr_state"], "auto_merge_enabled")
        self.assertEqual(stream["pr_head_sha"], "reviewed-head")
        self.assertEqual(stream["pr"]["merge_state"], "BLOCKED")

    def test_merge_readiness_packet_recommends_rebase_for_stale_base(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")

        product = self._task(
            "TORQUE:1",
            "Add stale-base guard",
            lane="Done",
            agent_id=worker.id,
        )
        review = self._task(
            "TORQUE:1:1",
            "Review stale-base guard",
            lane="Done",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            agent_id=worker.id,
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "reviewed-head",
                "recorded_by_agent_id": worker.id,
                "stale_base": {
                    "stale": True,
                    "base_branch": "main",
                    "base_head": "new-main",
                    "merge_base": "old-main",
                    "warning": "Base branch advanced; rebase before merge.",
                },
                "pr": {
                    "provider": "github",
                    "remote": "origin",
                    "base_branch": "main",
                    "head_branch": "torque/worker",
                    "head_sha": "reviewed-head",
                    "url": "https://github.com/acme/repo/pull/124",
                    "number": 124,
                    "state": "open",
                    "merge_state": "BEHIND",
                },
            },
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "follow_up_classification": "none",
                    "source_action": "done",
                    "recorded_at": "2026-04-07T11:30:00+00:00",
                }
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "ready_to_merge")
        self.assertEqual(stream["merge_state"], "ready")
        self.assertEqual(stream["recommended_next_action"], "rebase")
        self.assertEqual(stream["recommended_tool"], "worktree_rebase")
        packet = stream["merge_readiness"]
        self.assertEqual(packet["stale_base"]["state"], "stale")
        self.assertTrue(packet["stale_base"]["stale"])
        self.assertEqual(packet["stale_base"]["source"], "boundary")
        self.assertEqual(packet["recommended_next_action"], "rebase")
        self.assertEqual(packet["recommended_tool"], "worktree_rebase")
        self.assertEqual(
            packet["head"]["current_branch_head_sha"],
            "reviewed-head",
        )

    def test_merge_readiness_conflict_is_not_reported_ready(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")
        product = self._task("TORQUE:1", "Add guard", agent_id=worker.id)
        review = self._task(
            "TORQUE:1:1", "Review guard", lane="Done",
            action_name="feature/review", parent_task_id=product.id,
            pipeline_root_id=product.id, pipeline_depth=1, agent_id=worker.id,
            boundary={
                "version": "1", "repo_root": "/repo",
                "branch": "torque/worker", "base_branch": "main",
                "commit_sha": "reviewed-head", "recorded_by_agent_id": worker.id,
            },
            completion_evidence={"review": {"verdict": "ship"}},
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        self.streams_mod._merge_readiness_cache_put(
            "/repo", "torque/worker", "main", {
                "state": "stale", "stale": True,
                "source": "merge_readiness_check", "base_advanced": True,
                "merge_clean": False, "merge_conflict": True,
            },
        )

        stream = self.streams_mod.compute_worktree_stream(
            state, repo_root="/repo", branch="torque/worker",
        )

        self.assertEqual(stream["state"], "fixing_blockers")
        self.assertEqual(stream["merge_state"], "not_ready")
        self.assertEqual(stream["recommended_next_action"], "rebase")
        self.assertEqual(stream["recommended_tool"], "worktree_rebase")
        self.assertTrue(stream["merge_readiness"]["stale_base"]["merge_conflict"])

    def test_unknown_merge_readiness_never_asserts_ready(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")
        product = self._task("TORQUE:1", "Add guard", agent_id=worker.id)
        review = self._task(
            "TORQUE:1:1", "Review guard", lane="Done",
            action_name="feature/review", parent_task_id=product.id,
            pipeline_root_id=product.id, pipeline_depth=1, agent_id=worker.id,
            boundary={
                "version": "1", "repo_root": "/repo",
                "branch": "torque/worker", "base_branch": "main",
                "commit_sha": "reviewed-head", "recorded_by_agent_id": worker.id,
            },
            completion_evidence={"review": {"verdict": "ship"}},
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        stream = self.streams_mod.compute_worktree_stream(
            state, repo_root="/repo", branch="torque/worker",
        )

        self.assertEqual(stream["state"], "merge_readiness_unknown")
        self.assertEqual(stream["merge_state"], "not_ready")
        self.assertEqual(
            stream["recommended_next_action"], "check_merge_readiness",
        )
        self.assertEqual(
            stream["merge_readiness"]["stale_base"]["source"], "not_checked",
        )

    def test_merge_tree_readiness_probe_distinguishes_clean_and_conflict(self):
        def probe(*, conflict: bool):
            with tempfile.TemporaryDirectory() as repo:
                def git(*args):
                    subprocess.run(
                        ["git", "-C", repo, *args], check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )

                git("init", "-q", "-b", "main")
                git("config", "user.email", "test@example.com")
                git("config", "user.name", "Test")
                with open(f"{repo}/shared.txt", "w") as handle:
                    handle.write("base\n")
                git("add", "shared.txt")
                git("commit", "-qm", "initial")
                git("switch", "-qc", "worker")
                target = "shared.txt" if conflict else "worker.txt"
                with open(f"{repo}/{target}", "w") as handle:
                    handle.write("worker\n")
                git("add", target)
                git("commit", "-qm", "worker")
                git("switch", "-q", "main")
                target = "shared.txt" if conflict else "base.txt"
                with open(f"{repo}/{target}", "w") as handle:
                    handle.write("main\n")
                git("add", target)
                git("commit", "-qm", "main")
                return asyncio.run(self.streams_mod._probe_merge_readiness(
                    repo, "worker", "main",
                ))

        clean = probe(conflict=False)
        conflict = probe(conflict=True)
        self.assertTrue(clean["base_advanced"])
        self.assertTrue(clean["merge_clean"])
        self.assertFalse(clean["merge_conflict"])
        self.assertTrue(conflict["base_advanced"])
        self.assertFalse(conflict["merge_clean"])
        self.assertTrue(conflict["merge_conflict"])

    def test_merged_pr_stream_keeps_reviewed_sha_and_surfaces_merge_sha(self):
        state = self._make_state()
        worker = self._add_agent(state, status="idle")

        product = self._task(
            "TORQUE:1",
            "Add PR merge flow",
            lane="Done",
            agent_id=worker.id,
        )
        review = self._task(
            "TORQUE:1:1",
            "Review PR merge flow",
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
                "branch": "torque/worker",
                "status": "merged",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "reviewed-head",
                "merged_at": "2026-04-07T12:00:00+00:00",
                "merge_commit_sha": "squash-merge-sha",
                "recorded_by_agent_id": worker.id,
                "pr": {
                    "provider": "github",
                    "remote": "origin",
                    "base_branch": "main",
                    "head_branch": "torque/worker",
                    "head_sha": "reviewed-head",
                    "url": "https://github.com/acme/repo/pull/123",
                    "number": 123,
                    "state": "merged",
                    "merge_state": "CLEAN",
                    "merged_at": "2026-04-07T12:00:00+00:00",
                    "merge_commit_sha": "squash-merge-sha",
                },
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "merged")
        self.assertEqual(stream["merge_state"], "merged")
        self.assertEqual(stream["latest_boundary_status"], "merged")
        self.assertEqual(stream["latest_reviewed_commit_sha"], "reviewed-head")
        self.assertEqual(stream["latest_merged_commit_sha"], "squash-merge-sha")
        self.assertEqual(
            stream["pr_url"],
            "https://github.com/acme/repo/pull/123",
        )
        self.assertEqual(stream["pr_state"], "merged")
        self.assertEqual(stream["pr_head_sha"], "reviewed-head")
        packet = stream["merge_readiness"]
        self.assertEqual(packet["state"], "merged")
        self.assertEqual(packet["merge_state"], "merged")
        self.assertEqual(packet["recommended_next_action"], "none")
        self.assertEqual(packet["latest_merged_commit_sha"], "squash-merge-sha")
        self.assertEqual(packet["pr"]["state"], "merged")
        self.assertIn("- Merged SHA: <merge_sha>", packet["merge_report_snippet"])

    def test_multiple_product_tasks_on_one_branch_collapse_into_one_stream(self):
        state = self._make_state()

        product_one = self._task("TORQUE:1", "Add Events tab")
        review = self._task(
            "TORQUE:1:1",
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
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "review123",
            },
        )
        product_two = self._task(
            "TORQUE:2",
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
        self.assertEqual(
            streams[0]["queue_items"],
            [
                {
                    "task_id": product_two.id,
                    "task_title": product_two.task,
                    "lane": "To Do",
                    "queue_state": "ready_to_resume",
                    "deps_met": True,
                    "resume_after_boundary_task_id": review.id,
                    "held": False,
                }
            ],
        )
        self.assertEqual(streams[0]["ready_to_resume_task_id"], product_two.id)
        self.assertTrue(streams[0]["can_auto_resume"])

    def test_missing_branch_historical_stream_is_marked_orphaned_and_filtered(self):
        state = self._make_state()

        review = self._task(
            "TORQUE:1",
            "Review completed branch",
            lane="Done",
            action_name="feature/review",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/historical-review",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
            },
        )
        state.board_tasks[review.id] = review

        with mock.patch.object(
            self.streams_mod,
            "_branch_exists_locally",
            return_value=False,
        ):
            streams = self.streams_mod.compute_worktree_streams(state)
            operational = self.streams_mod.compute_worktree_streams(
                state,
                include_orphaned=False,
            )

        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["stream_presence"], "orphaned")
        self.assertFalse(streams[0]["branch_exists_locally"])
        self.assertEqual(operational, [])

    def test_existing_closed_branch_stream_stays_visible_as_dormant(self):
        state = self._make_state()

        review = self._task(
            "TORQUE:1",
            "Review completed branch",
            lane="Done",
            action_name="feature/review",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/dormant-review",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
            },
        )
        state.board_tasks[review.id] = review

        with mock.patch.object(
            self.streams_mod,
            "_branch_exists_locally",
            return_value=True,
        ):
            streams = self.streams_mod.compute_worktree_streams(
                state,
                include_orphaned=False,
            )

        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["stream_presence"], "dormant")
        self.assertTrue(streams[0]["branch_exists_locally"])

    def test_review_blocker_loop_becomes_fixing_blockers(self):
        state = self._make_state()
        worker = self._add_agent(
            state,
            current_task_id="TORQUE:1:2",
            status="running",
            last_event_at="2026-04-07T12:45:00+00:00",
        )

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            lane="Done",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
                "recorded_by_agent_id": worker.id,
            },
        )
        review = self._task(
            "TORQUE:1:1",
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
            "TORQUE:1:2",
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
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "fixing_blockers")
        self.assertEqual(stream["code_state"], "review_blocked")
        self.assertEqual(stream["merge_state"], "not_ready")
        self.assertEqual(stream["active_blocker_task_id"], blocker_fix.id)
        self.assertEqual(stream["active_blocker_task_title"], blocker_fix.task)
        self.assertEqual(stream["active_blocker_health_state"], "healthy")
        self.assertEqual(stream["blocker_parent_review_task_id"], review.id)
        self.assertEqual(
            stream["blocker_parent_review_task_title"],
            review.task,
        )
        self.assertEqual(stream["expected_next_transition"], "re-review")
        self.assertEqual(stream["recommended_next_action"], "address_review_blockers")
        self.assertEqual(stream["foreground_task_id"], blocker_fix.id)
        self.assertIn(blocker_fix.id, stream["workflow_task_ids"])
        packet = stream["merge_readiness"]
        self.assertEqual(packet["state"], "fixing_blockers")
        self.assertEqual(packet["merge_state"], "not_ready")
        self.assertEqual(
            packet["followups"]["active_blocker_fix_task"]["task_id"],
            blocker_fix.id,
        )
        self.assertEqual(
            packet["followups"]["blocker_parent_review_task"]["task_id"],
            review.id,
        )
        self.assertEqual(packet["recommended_next_action"], "address_review_blockers")

    def test_review_in_progress_pauses_future_product_queue(self):
        state = self._make_state()

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            lane="Done",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
            },
        )
        review = self._task(
            "TORQUE:1:1",
            "Review Events implementation",
            lane="In Progress",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            created_at="2026-04-07T11:10:00+00:00",
            updated_at="2026-04-07T11:20:00+00:00",
        )
        queued_next = self._task(
            "TORQUE:2",
            "Add Worklog tab",
            lane="To Do",
            created_at="2026-04-07T11:30:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            resume_after=product.id,
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        state.board_tasks[queued_next.id] = queued_next

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "reviewing")
        self.assertEqual(stream["queue_gate"], {})
        self.assertEqual(stream["ready_to_resume_task_id"], "")
        self.assertEqual(
            [item["queue_state"] for item in stream["queue_items"]],
            ["paused_by_review"],
        )

    def test_blocker_gate_pauses_future_product_queue(self):
        state = self._make_state()
        worker = self._add_agent(
            state,
            current_task_id="TORQUE:1:2",
            status="running",
            last_event_at="2026-04-07T12:45:00+00:00",
        )

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            lane="Done",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
                "recorded_by_agent_id": worker.id,
            },
        )
        review = self._task(
            "TORQUE:1:1",
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
            "TORQUE:1:2",
            "Fix the issues found",
            lane="In Progress",
            action_name="feature/fix-review",
            parent_task_id=review.id,
            pipeline_root_id=product.id,
            pipeline_depth=2,
            agent_id=worker.id,
            created_at="2026-04-07T11:30:00+00:00",
            updated_at="2026-04-07T12:40:00+00:00",
            resume_after=product.id,
            labels=["torque:derived", "review-fix"],
        )
        queued_next = self._task(
            "TORQUE:2",
            "Add Worklog tab",
            lane="To Do",
            created_at="2026-04-07T12:41:00+00:00",
            updated_at="2026-04-07T12:41:00+00:00",
            resume_after=review.id,
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        state.board_tasks[blocker_fix.id] = blocker_fix
        state.board_tasks[queued_next.id] = queued_next

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["queue_gate"]["gate_type"], "review_blocker")
        self.assertEqual(stream["queue_gate"]["blocking_task_id"], blocker_fix.id)
        self.assertEqual(stream["queue_gate"]["source_task_id"], review.id)
        self.assertEqual(
            [item["queue_state"] for item in stream["queue_items"]],
            ["paused_by_blocker"],
        )
        self.assertEqual(stream["ready_to_resume_task_id"], "")
        self.assertFalse(stream["can_auto_resume"])

    def test_validation_pending_branch_is_awaiting_human_validation(self):
        state = self._make_state()

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            verification_state="pending",
            verification_summary={
                "human_validation_pending": "Live/manual Engineer-panel smoke pending",
            },
            verification_updated_at="2026-04-07T11:45:00+00:00",
        )
        review = self._task(
            "TORQUE:1:1",
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
                "branch": "torque/worker",
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
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "awaiting_human_validation")
        self.assertEqual(stream["code_state"], "reviewed_clean")
        self.assertEqual(stream["validation_state"], "pending_human_validation")
        self.assertEqual(
            stream["gate_reason"],
            "Live/manual Engineer-panel smoke pending",
        )
        self.assertEqual(stream["recommended_next_action"], "merge_after_validation")

    def test_validation_gate_pauses_future_product_queue(self):
        state = self._make_state()

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            lane="Done",
            verification_state="pending",
            verification_summary={
                "human_validation_pending": "Live/manual Engineer-panel smoke pending",
            },
            verification_updated_at="2026-04-07T11:45:00+00:00",
        )
        review = self._task(
            "TORQUE:1:1",
            "Review Events implementation",
            lane="Done",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "review123",
            },
        )
        queued_next = self._task(
            "TORQUE:2",
            "Add Worklog tab",
            lane="To Do",
            created_at="2026-04-07T11:35:00+00:00",
            updated_at="2026-04-07T11:35:00+00:00",
            resume_after=review.id,
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        state.board_tasks[queued_next.id] = queued_next

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["queue_gate"]["gate_type"], "human_validation")
        self.assertEqual(
            [item["queue_state"] for item in stream["queue_items"]],
            ["paused_by_validation"],
        )
        self.assertEqual(stream["ready_to_resume_task_id"], "")
        self.assertFalse(stream["can_auto_resume"])

    def test_merge_conflict_stream_marks_branch_advanced_and_not_partial_review_safe(self):
        state = self._make_state()
        worker = self._add_agent(
            state,
            current_task_id="TORQUE:1:2",
            status="running",
            last_event_at="2026-04-07T12:30:00+00:00",
        )

        product = self._task("TORQUE:1", "Add Events tab")
        review = self._task(
            "TORQUE:1:1",
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
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "review123",
                "recorded_by_agent_id": worker.id,
            },
        )
        merge_fix = self._task(
            "TORQUE:1:2",
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
            branch="torque/worker",
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
            "TORQUE:1",
            "Add Events tab",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
            },
        )
        visibility = self._task(
            "TORQUE:9",
            "Engineer: reprioritize blocker fix",
            lane="Done",
            action_name="",
            reply_agent_id=worker.id,
            labels=["torque:engineer-message"],
            created_at="2026-04-07T11:10:00+00:00",
            updated_at="2026-04-07T11:12:00+00:00",
            messages=[
                {
                    "timestamp": _ts("2026-04-07T11:11:00+00:00"),
                    "action": "engineer_message",
                    "message": "Reprioritized blocker fix before queued work",
                    "agent_name": "Engineer",
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
            branch="torque/worker",
        )

        self.assertEqual(stream["product_task_ids"], [product.id])
        self.assertEqual(stream["workflow_task_ids"], [])
        self.assertEqual(stream["queue_items"], [])
        self.assertEqual(stream["queue_counts"], {})
        self.assertEqual(
            [item["kind"] for item in stream["recent_visibility_items"]],
            ["agent_reply", "engineer_message"],
        )
        self.assertEqual(
            stream["recent_visibility_items"][1]["summary"],
            "Reprioritized blocker fix before queued work",
        )

    def test_compute_streams_keeps_branch_membership_local_when_pipeline_root_spans_branches(self):
        state = self._make_state()
        agent_a = self._add_agent(
            state,
            agent_id="agent-a",
            branch="torque/branch-a",
            status="idle",
        )
        agent_b = self._add_agent(
            state,
            agent_id="agent-b",
            branch="torque/branch-b",
            status="idle",
        )

        root = self._task("TORQUE:1", "Shared product root")
        review_a = self._task(
            "TORQUE:1:1",
            "Review branch A",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:05:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/branch-a",
                "status": "open",
                "recorded_at": "2026-04-07T11:05:00+00:00",
                "commit_sha": "aaa111",
                "recorded_by_agent_id": agent_a.id,
            },
        )
        visibility_a = self._task(
            "TORQUE:1:2",
            "Engineer: branch A note",
            lane="Done",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            reply_agent_id=agent_a.id,
            labels=["torque:engineer-message"],
            created_at="2026-04-07T11:06:00+00:00",
            updated_at="2026-04-07T11:06:00+00:00",
            messages=[
                {
                    "timestamp": _ts("2026-04-07T11:06:00+00:00"),
                    "action": "engineer_message",
                    "message": "Branch A note",
                    "agent_name": "Engineer",
                }
            ],
        )
        review_b = self._task(
            "TORQUE:1:3",
            "Review branch B",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            created_at="2026-04-07T12:00:00+00:00",
            updated_at="2026-04-07T12:05:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/branch-b",
                "status": "open",
                "recorded_at": "2026-04-07T12:05:00+00:00",
                "commit_sha": "bbb222",
                "recorded_by_agent_id": agent_b.id,
            },
        )
        visibility_b = self._task(
            "TORQUE:1:4",
            "Engineer: branch B note",
            lane="Done",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            reply_agent_id=agent_b.id,
            labels=["torque:engineer-message"],
            created_at="2026-04-07T12:06:00+00:00",
            updated_at="2026-04-07T12:06:00+00:00",
            messages=[
                {
                    "timestamp": _ts("2026-04-07T12:06:00+00:00"),
                    "action": "engineer_message",
                    "message": "Branch B note",
                    "agent_name": "Engineer",
                }
            ],
        )
        for task in (root, review_a, visibility_a, review_b, visibility_b):
            state.board_tasks[task.id] = task

        streams = {
            stream["branch"]: stream
            for stream in self.streams_mod.compute_worktree_streams(state)
        }

        self.assertEqual(set(streams), {"torque/branch-a", "torque/branch-b"})
        self.assertEqual(streams["torque/branch-a"]["workflow_task_ids"], [review_a.id])
        self.assertEqual(
            [item["summary"] for item in streams["torque/branch-a"]["recent_visibility_items"]],
            ["Branch A note"],
        )
        self.assertEqual(streams["torque/branch-b"]["workflow_task_ids"], [review_b.id])
        self.assertEqual(
            [item["summary"] for item in streams["torque/branch-b"]["recent_visibility_items"]],
            ["Branch B note"],
        )

    def test_owner_agent_prefers_branch_implementation_owner_over_busy_reviewer(self):
        state = self._make_state()
        impl_agent = self._add_agent(
            state,
            agent_id="impl-agent",
            branch="torque/worker",
            status="idle",
            last_event_at="2026-04-07T12:10:00+00:00",
        )
        review_agent = self._add_agent(
            state,
            agent_id="review-agent",
            branch="torque/worker",
            current_task_id="TORQUE:1:1",
            status="running",
            last_event_at="2026-04-07T12:20:00+00:00",
        )

        product = self._task(
            "TORQUE:1",
            "Add Events tab",
            lane="Done",
            agent_id=impl_agent.id,
            updated_at="2026-04-07T11:00:00+00:00",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "impl123",
                "recorded_by_agent_id": impl_agent.id,
            },
        )
        review = self._task(
            "TORQUE:1:1",
            "Review Events implementation",
            lane="In Progress",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            agent_id=review_agent.id,
            created_at="2026-04-07T11:30:00+00:00",
            updated_at="2026-04-07T12:15:00+00:00",
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review

        stream = self.streams_mod.compute_worktree_stream(
            state,
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(stream["state"], "reviewing")
        self.assertEqual(stream["foreground_task_id"], review.id)
        self.assertEqual(stream["agent_id"], impl_agent.id)
        self.assertEqual(stream["agent_name"], impl_agent.name)
