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
            self.state.board_tasks.values(), repo_root="/repo", branch="topic", merge_sha="merge")
        self.assertEqual(root.worktree_boundary["code_delta"]["state"], "present")
        self.assertEqual(root.worktree_boundary["merge_commit_sha"], "merge")

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
