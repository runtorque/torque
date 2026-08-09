import asyncio
import importlib
import sys
import subprocess
import tempfile
import types
import unittest
from pathlib import Path


def _aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")
    web.WebSocketResponse = type("WebSocketResponse", (), {})
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


def _boundary(state, *, merged=False, sha="candidate", merge_sha="merge"):
    result = {
        "status": "merged" if merged else "open",
        "commit_sha": sha,
        "code_delta": {"state": state, "commit_sha": sha},
    }
    if merged:
        result["merge_commit_sha"] = merge_sha
    return result


class CodeBoundaryDoneGateTests(unittest.TestCase):
    def setUp(self):
        _aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state = self.state_mod.MatrixState()
        self.state.groups["g"] = []

    def _root(self, *, boundary=None, creator="architect", assignee=""):
        return self.state.board_add_task(
            "Implement", "g", id="root", lane="In Progress",
            action_name="feature/implement", requires_review=True,
            created_by_architect_id=creator, assigned_architect_id=assignee,
            worktree_boundary=boundary or {},
        )

    def _ship(self, root, *, boundary=None):
        return self.state.board_add_task(
            "Review", "g", id="review", lane="Done", action_name="feature/review",
            parent_task_id=root.id, pipeline_root_id=root.id, pipeline_depth=1,
            completion_evidence={"review": {"verdict": "ship", "agent_id": "reviewer"}},
            worktree_boundary=boundary or {},
        )

    @staticmethod
    def _merge_boundary(*, merged=False):
        boundary = _boundary("present", merged=merged, sha="candidate",
                             merge_sha="merge123")
        boundary.update({
            "repo_root": "/repo",
            "branch": "topic",
            "base_branch": "main",
        })
        return boundary

    def _record_merge_evidence(self):
        evidence = importlib.import_module("torque.server_evidence")
        return evidence._record_merge_completion_evidence(
            self.state,
            result={"ok": True, "sha": "merge123", "mode": "direct"},
            task_ids=["root"],
            repo_root="/repo",
            branch="topic",
            base_branch="main",
            origin_verification={"verified": True},
        )

    def test_ship_code_present_unmerged_blocks_fresh_1315_shape(self):
        root = self._root()
        review = self._ship(root, boundary=_boundary("present"))
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "In Progress")

    def test_atomic_policy_done_update_cannot_bypass_open_code_boundary(self):
        root = self._root(boundary=_boundary("present"))
        review = self.state.board_add_task(
            "Audit", "g", id="audit", lane="Done", parent_task_id=root.id,
            pipeline_root_id=root.id, pipeline_depth=1,
            completion_evidence={"finalization_review": {
                "executed": True, "gate_id": "audit", "verdict": "ship",
                "has_blocking_issues": False,
                "required_follow_up_resolved": True, "boundary": "d|v|e",
            }},
        )
        result = self.state.board_update_task(
            root.id, lane="Done", finalization_mode="review_only",
            required_review_gates=[{
                "id": "audit", "role": "audit", "review_task_id": review.id,
            }],
            finalization_boundary={
                "artifact_digest": "d", "artifact_version": "v",
                "source_identity": "e", "immutable": True,
            },
        )
        self.assertFalse(result["eligible"])
        self.assertIn("code_boundary_not_durably_merged", result["missing_gates"])
        self.assertEqual(root.lane, "In Progress")
        self.assertEqual(root.finalization_mode, "legacy")

    def test_two_successive_ship_reviews_cannot_bypass_unmerged_code_gate(self):
        root = self._root()
        first = self._ship(root, boundary=_boundary("present"))
        self.state.board_cascade_done(first.id)
        self.assertEqual(root.lane, "In Progress")
        second = self.state.board_add_task(
            "Second independent review", "g", id="review-2", lane="Done",
            action_name="feature/review", parent_task_id=root.id,
            pipeline_root_id=root.id, pipeline_depth=1,
            completion_evidence={"review": {"verdict": "ship", "agent_id": "reviewer-2"}},
            worktree_boundary=_boundary("present", sha="candidate-2"),
        )
        self.state.board_cascade_done(second.id)
        self.assertEqual(root.lane, "In Progress")

    def test_explicit_absent_boundary_cascades_normally(self):
        root = self._root()
        review = self._ship(root, boundary=_boundary("absent"))
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "Done")

    def test_no_boundary_is_vacuously_no_code(self):
        root = self._root()
        review = self._ship(root)
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "Done")

    def test_missing_ship_still_blocks_1353_cascade(self):
        root = self._root()
        child = self.state.board_add_task(
            "Unreviewed child", "g", id="child", lane="Done",
            parent_task_id=root.id, pipeline_root_id=root.id, pipeline_depth=1,
            worktree_boundary=_boundary("absent"),
        )
        self.state.board_cascade_done(child.id)
        self.assertEqual(root.lane, "In Progress")

    def test_merged_code_boundary_allows_done_after_cleanup_without_live_git(self):
        root = self._root()
        review = self._ship(root, boundary=_boundary("present", merged=True))
        # The guard has no cell/worktree manager and therefore cannot invoke
        # live Git; only the durable boundary evidence is consumed.
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "Done")

    def test_merge_evidence_rechecks_cascade_after_last_descendant_done(self):
        """Evidence arriving after the normal cascade must re-ask its gate."""
        root = self._root(boundary=self._merge_boundary())
        review = self._ship(root)

        # This is the production ordering: review completion occurs first and
        # correctly refuses the still-open code boundary.
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "In Progress")
        self.assertEqual(root.completion_evidence, {})

        root.worktree_boundary.update(self._merge_boundary(merged=True))
        updated = self._record_merge_evidence()

        self.assertEqual(updated, [root.id])
        self.assertTrue(root.completion_evidence["verified"])
        self.assertEqual(root.completion_evidence["merge"]["sha"], "merge123")
        self.assertEqual(root.lane, "Done")

    def test_merge_evidence_expires_engineer_message_before_done_1588_shape(self):
        root = self._root(boundary=self._merge_boundary())
        review = self._ship(root)
        message = self.state.board_add_task(
            "Engineer: Need status", "g", id="engineer-message",
            lane="Backlog", status="Awaiting Reply",
            parent_task_id=root.id, pipeline_root_id=root.id, pipeline_depth=1,
            labels=["torque:derived", "torque:engineer-message"],
        )
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "In Progress")

        root.worktree_boundary.update(self._merge_boundary(merged=True))
        self._record_merge_evidence()

        self.assertEqual(root.lane, "Done")
        self.assertEqual(message.lane, "Done")
        self.assertEqual(message.status, "")

    def test_blocked_code_gate_does_not_expire_engineer_message(self):
        root = self._root(boundary=self._merge_boundary())
        review = self._ship(root)
        message = self.state.board_add_task(
            "Engineer: Need status", "g", id="engineer-message",
            lane="Backlog", status="Awaiting Reply",
            parent_task_id=root.id, pipeline_root_id=root.id, pipeline_depth=1,
            labels=["torque:derived", "torque:engineer-message"],
        )

        self.state.board_cascade_done(review.id)

        self.assertEqual(root.lane, "In Progress")
        self.assertEqual(message.lane, "Backlog")
        self.assertEqual(message.status, "Awaiting Reply")

    def test_merge_evidence_recheck_preserves_human_ask_block(self):
        root = self._root(boundary=self._merge_boundary())
        review = self._ship(root)
        ask = self.state.board_add_task(
            "Human decision", "g", id="human-ask", lane="Backlog",
            status="Awaiting Reply", parent_task_id=root.id,
            pipeline_root_id=root.id, pipeline_depth=1,
            labels=["torque:derived", "torque:human"],
        )
        self.state.board_cascade_done(review.id)

        root.worktree_boundary.update(self._merge_boundary(merged=True))
        self._record_merge_evidence()

        self.assertEqual(ask.lane, "Backlog")
        self.assertEqual(root.lane, "In Progress")

    def test_last_descendant_without_merge_evidence_remains_blocked(self):
        root = self._root(boundary=self._merge_boundary())
        review = self._ship(root)

        self.state.board_cascade_done(review.id)

        # Preserve ba5a8bfa behavior: a Ship alone cannot pass an open code
        # boundary, and no recheck occurs until evidence is durably recorded.
        self.assertEqual(root.lane, "In Progress")
        self.assertEqual(root.completion_evidence, {})

    def test_merge_evidence_recheck_preserves_unresolved_descendant_block(self):
        root = self._root(boundary=self._merge_boundary())
        review = self._ship(root)
        pending = self.state.board_add_task(
            "Still implementing", "g", id="pending", lane="In Progress",
            parent_task_id=root.id, pipeline_root_id=root.id, pipeline_depth=1,
        )
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "In Progress")

        root.worktree_boundary.update(self._merge_boundary(merged=True))
        self._record_merge_evidence()

        self.assertEqual(pending.lane, "In Progress")
        self.assertEqual(root.lane, "In Progress")

    def test_system_created_root_still_blocks_unmerged_code(self):
        root = self._root(creator="", assignee="")
        review = self._ship(root, boundary=_boundary("present"))
        self.state.board_cascade_done(review.id)
        self.assertEqual(root.lane, "In Progress")

    def test_unknown_and_legacy_boundary_block_open_but_allow_durable_merge(self):
        for state in ("unknown", None):
            with self.subTest(state=state):
                self.state = self.state_mod.MatrixState()
                self.state.groups["g"] = []
                root = self._root()
                boundary = _boundary("present")
                if state is None:
                    boundary.pop("code_delta")
                else:
                    boundary["code_delta"]["state"] = state
                review = self._ship(root, boundary=boundary)
                self.state.board_cascade_done(review.id)
                self.assertEqual(root.lane, "In Progress")
                boundary["status"] = "merged"
                boundary["merge_commit_sha"] = "merged-sha"
                self.state.board_move_task(root.id, "Done")
                self.assertEqual(root.lane, "Done")

    def test_merge_projection_carries_classifier_to_root(self):
        root = self._root()
        child = self.state.board_add_task(
            "Implement child", "g", id="child", lane="In Progress",
            parent_task_id=root.id, pipeline_root_id=root.id, pipeline_depth=1,
            worktree_boundary={
                "repo_root": "/repo", "branch": "topic", "status": "open",
                "commit_sha": "candidate", "code_delta": {"state": "present"},
            },
        )
        boundaries = importlib.import_module("torque.worktree_boundaries")
        boundaries.mark_branch_boundaries_merged(
            self.state.board_tasks.values(), repo_root="/repo", branch="topic",
            merge_sha="merge", task_ids=[child.id])
        self.assertEqual(root.worktree_boundary["code_delta"]["state"], "present")
        self.assertEqual(root.worktree_boundary["merge_commit_sha"], "merge")

    def test_merge_evidence_does_not_contaminate_paused_shared_branch_task(self):
        """Merge evidence follows the selected task, never a shared branch."""
        merged_task = self.state.board_add_task(
            "Merged task", "g", id="TORQUE:1290", lane="In Progress",
            worktree_boundary={
                "repo_root": "/repo", "branch": "topic", "status": "open",
                "commit_sha": "candidate-1290",
            },
        )
        paused_task = self.state.board_add_task(
            "Paused discriminator", "g", id="TORQUE:1298", lane="In Progress",
            worktree_boundary={
                "repo_root": "/repo", "branch": "topic", "status": "open",
                "commit_sha": "candidate-1298",
            },
        )
        boundaries = importlib.import_module("torque.worktree_boundaries")
        boundaries.mark_branch_boundaries_merged(
            self.state.board_tasks.values(), repo_root="/repo", branch="topic",
            merge_sha="merge123", task_ids=[merged_task.id],
        )
        evidence = importlib.import_module("torque.server_evidence")
        evidence._record_merge_completion_evidence(
            self.state,
            result={"ok": True, "sha": "merge123", "mode": "direct"},
            task_ids=[merged_task.id],
            repo_root="/repo", branch="topic", base_branch="main",
            origin_verification={"verified": True},
        )

        self.assertEqual(merged_task.worktree_boundary["status"], "merged")
        self.assertIn("merge", merged_task.completion_evidence)
        self.assertEqual(paused_task.worktree_boundary["status"], "open")
        self.assertEqual(paused_task.completion_evidence, {})

    def test_classifier_records_present_and_absent_from_real_checkpointed_git(self):
        boundaries = importlib.import_module("torque.worktree_boundaries")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Torque Test")
            (repo / "base.txt").write_text("base\n")
            git("add", "base.txt")
            git("commit", "-m", "base")
            base_sha = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            absent = asyncio.run(boundaries.classify_boundary_code_delta(
                worktree_path=str(repo), base_branch="main", commit_sha=base_sha))
            self.assertEqual(absent["state"], "absent")
            git("checkout", "-b", "topic")
            (repo / "code.py").write_text("VALUE = 1\n")
            git("add", "code.py")
            git("commit", "-m", "code")
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            present = asyncio.run(boundaries.classify_boundary_code_delta(
                worktree_path=str(repo), base_branch="main", commit_sha=head))
            self.assertEqual(present["state"], "present")
            self.assertEqual(present["commit_sha"], head)
            self.assertEqual(present["path_count"], 1)

    def test_classifier_failure_is_persistable_unknown(self):
        boundaries = importlib.import_module("torque.worktree_boundaries")
        fact = asyncio.run(boundaries.classify_boundary_code_delta(
            worktree_path="", base_branch="main", commit_sha="candidate"))
        self.assertEqual(fact["state"], "unknown")
        self.assertEqual(fact["reason"], "missing_boundary_inputs")


if __name__ == "__main__":
    unittest.main()
