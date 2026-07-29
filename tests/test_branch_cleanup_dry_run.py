"""Tests for the intentionally non-mutating branch cleanup enumerator."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
            CREATE TABLE board_tasks (id TEXT, lane TEXT, worktree_boundary TEXT, agent_id TEXT);
            """
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?, 0)",
            ("live1234", "running", "torque/engineer/agent-branch"),
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?, 0)",
            ("idle5678", "idle", "torque/engineer/idle-assigned-agent"),
        )
        conn.execute(
            "INSERT INTO board_tasks VALUES (?, ?, ?, ?)",
            ("TASK:1", "In Progress", json.dumps({"branch": "torque/engineer/task-boundary"}), ""),
        )
        conn.execute(
            "INSERT INTO board_tasks VALUES (?, ?, ?, ?)",
            ("TASK:2", "Done", json.dumps({"branch": "torque/engineer/terminal-boundary"}), ""),
        )
        conn.execute(
            "INSERT INTO board_tasks VALUES (?, ?, ?, ?)",
            ("TASK:3", "In Progress", "{}", "idle5678"),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def branch_at_main(self, branch: str) -> None:
        git(self.repo, "branch", branch, self.main)

    @staticmethod
    def eligible(branch: str, location: str) -> dict:
        return {
            "ref": f"refs/{'heads' if location == 'local' else 'remotes/origin'}/{branch}",
            "branch": branch,
            "location": location,
            "gate_evidence": {"not_live_excluded": True, "valid_agent_namespace": True},
            "tree_evidence": {"identical": True, "merge_tree": "tree", "main_tree": "tree"},
        }

    @classmethod
    def report(cls, eligible: list[dict]) -> dict:
        return {
            "eligible": eligible,
            "excluded_for_live_work": [],
            "outside_namespace": [],
            "protected_status": [],
            "ineligible": [],
        }

    def test_normalize_ref_handles_local_and_origin_names(self):
        self.assertEqual(cleanup.normalize_ref("refs/heads/topic"), "topic")
        self.assertEqual(cleanup.normalize_ref("refs/remotes/origin/topic"), "topic")
        self.assertEqual(cleanup.normalize_ref("origin/topic"), "topic")

    def test_enumerator_excludes_live_gates_before_tree_identity(self):
        for branch in (
            "torque/engineer/eligible",
            "torque/engineer/task-boundary",
            "torque/engineer/terminal-boundary",
            "torque/engineer/agent-branch",
            "torque/engineer/idle-assigned-agent",
            "torque/engineer/worker-live1234",
            "torque/submodules/lib/topic",
            cleanup.ATTESTED_PROTECTED[0],
            "torque/engineer/content",
            "feature/outside",
        ):
            self.branch_at_main(branch)
        # Give content a tree that differs from main, so it proves the
        # tree-identity half still rejects actual added content.
        git(self.repo, "checkout", "torque/engineer/content")
        (self.repo / "content").write_text("new\n")
        git(self.repo, "add", "content")
        git(self.repo, "commit", "-m", "content")
        git(self.repo, "checkout", "main")
        git(self.repo, "update-ref", "refs/remotes/origin/torque/engineer/eligible", self.main)

        live_path = Path(self.tmp.name) / "live-worktree"
        git(self.repo, "worktree", "add", str(live_path), "torque/engineer/task-boundary")

        report = cleanup.enumerate_cleanup(self.repo, self.db)
        excluded = {item["branch"]: set(item["reasons"]) for item in report["excluded_for_live_work"]}
        eligible = {(item["location"], item["branch"]) for item in report["eligible"]}

        self.assertIn("live_worktree", excluded["torque/engineer/task-boundary"])
        self.assertIn("non_terminal_task_boundary:TASK:1", excluded["torque/engineer/task-boundary"])
        self.assertIn("non_idle_agent_branch:live1234", excluded["torque/engineer/agent-branch"])
        self.assertIn("non_idle_agent_id:live1234", excluded["torque/engineer/worker-live1234"])
        self.assertIn("non_terminal_task_agent:TASK:3", excluded["torque/engineer/idle-assigned-agent"])
        self.assertNotIn("torque/engineer/terminal-boundary", excluded)
        protected = {item["branch"]: item for item in report["protected_status"]}
        self.assertTrue(protected[cleanup.ATTESTED_PROTECTED[0]]["present"])
        self.assertEqual(
            eligible,
            {
                ("local", "torque/engineer/eligible"),
                ("origin", "torque/engineer/eligible"),
                ("local", "torque/engineer/terminal-boundary"),
            },
        )
        self.assertEqual(
            {(item["location"], item["branch"]) for item in report["outside_namespace"]},
            {
                ("local", "feature/outside"),
                ("local", "torque/submodules/lib/topic"),
            },
        )
        self.assertTrue(report["refs_unchanged"])
        self.assertEqual(report["counts"]["eligible_local_refs"], 2)
        self.assertEqual(report["counts"]["eligible_origin_refs"], 1)
        self.assertTrue(any(item["branch"] == "torque/engineer/content" for item in report["ineligible"]))

    def test_apply_refuses_drift_before_any_mutation_in_either_direction(self):
        for actual in (148, 204):
            with self.subTest(actual=actual):
                report = self.report([self.eligible(f"torque/e/w{i}", "local") for i in range(actual)])
                calls = []
                with mock.patch.object(cleanup, "_apply_command", side_effect=calls.append):
                    result = cleanup.apply_cleanup(
                        self.repo, self.db, expected_eligible_count=176, max_count_drift=27,
                        measure=lambda *_: report,
                    )
                self.assertEqual(result["refusal"], "eligible_count_drift_exceeds_threshold")
                self.assertFalse(result["mutation_started"])
                self.assertEqual(calls, [])
                self.assertEqual(result["drift"]["difference"], 28)

    def test_apply_does_not_accept_an_eligible_artifact_argument(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cleanup.main([
                "--output-dir", str(Path(self.tmp.name) / "out"),
                "--eligible-artifact", "untrusted.json",
            ])
        self.assertEqual(raised.exception.code, 2)

    def test_apply_runs_gated_force_local_before_one_at_a_time_remote_and_remeasures(self):
        candidates = [
            self.eligible("torque/e/local", "local"),
            self.eligible("torque/e/remote", "origin"),
        ]
        reports = [self.report(candidates), self.report([])]
        measured = []
        commands = []

        def measure(*_):
            measured.append(True)
            return reports[len(measured) - 1]

        def command(_repo, *args):
            commands.append(args)
            return {"command": list(args), "returncode": 0, "stdout": "", "stderr": ""}

        with mock.patch.object(cleanup, "_apply_command", side_effect=command):
            result = cleanup.apply_cleanup(
                self.repo, self.db, expected_eligible_count=2, max_count_drift=0, measure=measure,
            )
        self.assertEqual(commands, [("branch", "-D", "torque/e/local"), ("push", "origin", "--delete", "torque/e/remote")])
        self.assertEqual(len(measured), 2)
        local = result["local"]["succeeded"][0]
        self.assertTrue(local["gate_evidence"]["not_live_excluded"])
        self.assertTrue(local["tree_evidence"]["identical"])
        self.assertEqual(result["post_measurement"], reports[1])

    def test_remote_failure_stops_and_partitions_remaining_refs(self):
        candidates = [
            self.eligible("torque/e/local", "local"),
            self.eligible("torque/e/remote-one", "origin"),
            self.eligible("torque/e/remote-two", "origin"),
            self.eligible("torque/e/remote-three", "origin"),
        ]
        outcomes = iter((0, 0, 1))

        def command(_repo, *args):
            return {"command": list(args), "returncode": next(outcomes), "stdout": "", "stderr": "denied"}

        with mock.patch.object(cleanup, "_apply_command", side_effect=command):
            result = cleanup.apply_cleanup(
                self.repo, self.db, expected_eligible_count=4, max_count_drift=0,
                measure=lambda *_: self.report(candidates),
            )
        self.assertEqual(result["stop_reason"], "remote_delete_failed")
        self.assertEqual([x["branch"] for x in result["remote"]["succeeded"]], ["torque/e/remote-one"])
        self.assertEqual([x["branch"] for x in result["remote"]["failed"]], ["torque/e/remote-two"])
        self.assertEqual([x["branch"] for x in result["remote"]["never_attempted"]], ["torque/e/remote-three"])
