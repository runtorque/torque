"""Tests for the dispatch-postscript commit-hint helper.

The hint is the source of the implicit commit-discipline instruction
appended to every implementation worker's dispatch prompt. Two paths
must be covered:

* **Isolated worktree** (``feature/implement``, ``feature/fix-review``):
  worker has its own branch + working dir; a plain commit instruction
  is enough.
* **Shared working tree** (``oneshot/feature``, ``oneshot/fix``): no
  isolated worktree, so the hint must additionally warn against
  ``git add -A`` to prevent sweeping concurrent workers' WIP.

Regression target: prior to this helper, the commit hint was gated on
``cell.worktree_branch`` alone, so ``oneshot/*`` workers never received
it. The first ``oneshot/feature`` dispatched in this codebase
(TORQUE:356) called ``torque_done`` without committing — engineer had
to commit on its behalf. This test pins the corrected behavior.
"""

import unittest
from pathlib import Path

from torque.actions import ActionManager
from torque.server_prompts import build_dispatch_postscript, compute_commit_hint


REPO_ROOT = Path(__file__).resolve().parents[1]


class ComputeCommitHintTests(unittest.TestCase):
    def test_worktree_implementation_emits_plain_hint(self):
        hint = compute_commit_hint(
            has_worktree_branch=True,
            is_implementation=True,
            auto_checkpoint=False,
            checkpoint_on_progress=False,
        )
        self.assertIn("commit all your changes", hint)
        self.assertNotIn("git add -A", hint)
        self.assertNotIn("scope `git add`", hint)

    def test_oneshot_implementation_emits_scoped_hint(self):
        hint = compute_commit_hint(
            has_worktree_branch=False,
            is_implementation=True,
            auto_checkpoint=False,
            checkpoint_on_progress=False,
        )
        self.assertIn("commit all your changes", hint)
        self.assertIn("scope `git add`", hint)
        self.assertIn("`git add -A`", hint)
        self.assertIn("Other workers may have uncommitted", hint)

    def test_review_without_worktree_returns_empty(self):
        hint = compute_commit_hint(
            has_worktree_branch=False,
            is_implementation=False,
            auto_checkpoint=False,
            checkpoint_on_progress=False,
        )
        self.assertEqual(hint, "")

    def test_auto_checkpoint_suppresses_hint(self):
        hint = compute_commit_hint(
            has_worktree_branch=True,
            is_implementation=True,
            auto_checkpoint=True,
            checkpoint_on_progress=False,
        )
        self.assertEqual(hint, "")

    def test_checkpoint_on_progress_suppresses_hint(self):
        hint = compute_commit_hint(
            has_worktree_branch=False,
            is_implementation=True,
            auto_checkpoint=False,
            checkpoint_on_progress=True,
        )
        self.assertEqual(hint, "")

    def test_worktree_review_emits_plain_hint(self):
        hint = compute_commit_hint(
            has_worktree_branch=True,
            is_implementation=False,
            auto_checkpoint=False,
            checkpoint_on_progress=False,
        )
        self.assertIn("commit all your changes", hint)
        self.assertNotIn("scope `git add`", hint)


class DispatchPostscriptCommitHintIntegrationTests(unittest.TestCase):
    def test_postscript_appends_commit_hint_when_provided(self):
        ps = build_dispatch_postscript(
            transitions=[],
            commit_hint="commit your changes scoped",
        )
        self.assertIn("commit your changes scoped", ps)

    def test_postscript_omits_commit_hint_when_empty(self):
        ps = build_dispatch_postscript(
            transitions=[],
            commit_hint="",
        )
        self.assertNotIn("commit all your changes", ps)


class OneshotActionsAreImplementationDepthTests(unittest.TestCase):
    """Pin the action-manifest contract that drives commit-hint emission.

    If any of these flip to ``False`` the commit-hint logic in
    ``_build_postscript`` will silently stop firing for that action.
    """

    def setUp(self):
        self.amgr = ActionManager()

    def test_oneshot_feature_is_implementation_depth(self):
        self.assertTrue(
            self.amgr.is_implementation_depth(
                "oneshot/feature", str(REPO_ROOT)),
        )

    def test_oneshot_fix_is_implementation_depth(self):
        self.assertTrue(
            self.amgr.is_implementation_depth(
                "oneshot/fix", str(REPO_ROOT)),
        )

    def test_feature_implement_is_implementation_depth(self):
        self.assertTrue(
            self.amgr.is_implementation_depth(
                "feature/implement", str(REPO_ROOT)),
        )

    def test_feature_review_is_not_implementation_depth(self):
        self.assertFalse(
            self.amgr.is_implementation_depth(
                "feature/review", str(REPO_ROOT)),
        )


if __name__ == "__main__":
    unittest.main()
