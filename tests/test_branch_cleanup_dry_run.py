"""Tests for the intentionally non-mutating branch cleanup enumerator."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "branch_cleanup_dry_run.py"
SPEC = importlib.util.spec_from_file_location("branch_cleanup_dry_run", SCRIPT)
cleanup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


class BranchCleanupDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "tracked").write_text("base\n")
        git(self.repo, "add", "tracked")
        git(self.repo, "commit", "-m", "base")
        self.main = git(self.repo, "rev-parse", "main")
        self.db = Path(self.tmp.name) / "torque.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE agents (id TEXT, status TEXT, worktree_branch TEXT, deleted_at REAL);
            CREATE TABLE board_tasks (id TEXT, lane TEXT, worktree_boundary TEXT);
            """
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?, 0)",
            ("live1234", "running", "agent-branch"),
        )
        conn.execute(
            "INSERT INTO board_tasks VALUES (?, ?, ?)",
            ("TASK:1", "In Progress", json.dumps({"branch": "task-boundary"})),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def branch_at_main(self, branch: str) -> None:
        git(self.repo, "branch", branch, self.main)

    def test_normalize_ref_handles_local_and_origin_names(self):
        self.assertEqual(cleanup.normalize_ref("refs/heads/topic"), "topic")
        self.assertEqual(cleanup.normalize_ref("refs/remotes/origin/topic"), "topic")
        self.assertEqual(cleanup.normalize_ref("origin/topic"), "topic")

    def test_enumerator_excludes_live_gates_before_tree_identity(self):
        for branch in (
            "eligible",
            "task-boundary",
            "agent-branch",
            "torque/worker-live1234",
            "torque/submodules/lib/topic",
            cleanup.ATTESTED_PROTECTED[0],
            "content",
        ):
            self.branch_at_main(branch)
        # Give content a tree that differs from main, so it proves the
        # tree-identity half still rejects actual added content.
        git(self.repo, "checkout", "content")
        (self.repo / "content").write_text("new\n")
        git(self.repo, "add", "content")
        git(self.repo, "commit", "-m", "content")
        git(self.repo, "checkout", "main")
        git(self.repo, "update-ref", "refs/remotes/origin/eligible", self.main)

        live_path = Path(self.tmp.name) / "live-worktree"
        git(self.repo, "worktree", "add", str(live_path), "task-boundary")

        report = cleanup.enumerate_cleanup(self.repo, self.db)
        excluded = {item["branch"]: set(item["reasons"]) for item in report["excluded_for_live_work"]}
        eligible = {(item["location"], item["branch"]) for item in report["eligible"]}

        self.assertIn("live_worktree", excluded["task-boundary"])
        self.assertIn("non_terminal_task:TASK:1", excluded["task-boundary"])
        self.assertIn("task_boundary:TASK:1", excluded["task-boundary"])
        self.assertIn("non_idle_agent_branch:live1234", excluded["agent-branch"])
        self.assertIn("non_idle_agent_id:live1234", excluded["torque/worker-live1234"])
        self.assertIn("explicit_protected", excluded[cleanup.ATTESTED_PROTECTED[0]])
        self.assertIn("submodule_branch_not_in_scope", excluded["torque/submodules/lib/topic"])
        self.assertEqual(eligible, {("local", "eligible"), ("origin", "eligible")})
        self.assertTrue(report["refs_unchanged"])
        self.assertEqual(report["counts"]["eligible_local_refs"], 1)
        self.assertEqual(report["counts"]["eligible_origin_refs"], 1)
        self.assertTrue(any(item["branch"] == "content" for item in report["ineligible"]))
