import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.db import TorqueDB
from torque.server_evidence import (
    _apply_verification_report,
    _record_merge_completion_evidence,
    _record_task_completion_evidence_snapshot,
    _task_verification_evidence,
)
from torque.server_artifacts import serialize_task_for_mcp
from torque.state import BoardTask, MatrixState


class VerificationRunHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(self.db)

    def _task(self, task_id="task-1"):
        task = BoardTask(
            id=task_id,
            task="Preserve verification evidence",
            group="Torque",
            lane="Done",
            worktree_boundary={
                "status": "open",
                "repo_root": "/repo",
                "branch": "torque/test",
                "commit_sha": "a" * 40,
            },
        )
        self.state.board_tasks[task.id] = task
        self.db.save_board_task(task)
        return task

    def _verify(self, task, sha, tests_run, actor, timestamp):
        _apply_verification_report(
            task,
            {
                "verification_state": "passed",
                "tests_run": tests_run,
                "test_outcome": "full_suite_passed",
                "full_suite_attempted": True,
                "tested_sha": sha,
            },
            actor,
            self.db.save_board_task,
            timestamp=timestamp,
        )

    def test_rebase_fresh_verification_and_merge_preserve_both_runs(self):
        task = self._task()
        sha_a = "a" * 40
        sha_b = "b" * 40
        merge_sha = "c" * 40

        self._verify(
            task,
            sha_a,
            "python -m unittest (100 tests)",
            "worker-a",
            "2026-08-07T10:00:00+00:00",
        )
        stored_run_a = deepcopy(task.verification_summary["runs"][0])
        self.assertEqual(
            _task_verification_evidence(task)["summary"]["currentness"],
            "current",
        )

        task.worktree_boundary["commit_sha"] = sha_b
        self.db.save_board_task(task)
        moved = _task_verification_evidence(task)
        self.assertEqual(moved["summary"]["currentness"], "superseded")
        self.assertEqual(task.verification_summary["runs"][0], stored_run_a)

        self._verify(
            task,
            sha_b,
            "python -m unittest (108 tests)",
            "worker-b",
            "2026-08-07T11:00:00+00:00",
        )
        self.assertTrue(_record_task_completion_evidence_snapshot(
            self.state,
            task,
            action="verify",
            actor_name="worker-b",
            timestamp="2026-08-07T11:00:00+00:00",
        ))
        task.worktree_boundary.update({
            "status": "merged",
            "merge_commit_sha": merge_sha,
        })
        self.db.save_board_task(task)
        updated = _record_merge_completion_evidence(
            self.state,
            result={"ok": True, "sha": merge_sha, "mode": "direct"},
            task_ids=[task.id],
            repo_root="/repo",
            branch="torque/test",
            origin_verification={"verified": True},
        )
        self.assertEqual(updated, [task.id])

        persisted = self.db.load_all()["board_tasks"][task.id]
        runs = persisted["verification_summary"]["runs"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0], stored_run_a)
        self.assertEqual(runs[0]["recorded_by"], "worker-a")
        self.assertEqual(runs[1]["recorded_by"], "worker-b")
        self.assertEqual(
            runs[0]["report"]["summary"]["tested_sha"],
            sha_a,
        )
        self.assertEqual(
            runs[1]["report"]["summary"]["tested_sha"],
            sha_b,
        )
        self.assertEqual(
            persisted["verification_summary"]["tested_sha"],
            sha_b,
        )
        durable_verification = persisted["completion_evidence"]["verification"]
        self.assertEqual(
            durable_verification["summary"]["tested_sha"],
            sha_b,
        )
        self.assertEqual(len(durable_verification["runs"]), 2)
        self.assertNotIn("currentness", str(durable_verification))

        durable_state = MatrixState(self.db)
        durable_state.load()
        durable_task = durable_state.board_tasks[task.id]
        evidence = _task_verification_evidence(durable_task)
        self.assertEqual(evidence["summary"]["tested_sha"], sha_b)
        self.assertEqual(evidence["summary"]["currentness"], "current")
        self.assertEqual(
            [run["currentness"] for run in evidence["runs"]],
            ["superseded", "current"],
        )
        self.assertNotIn("currentness", persisted["verification_summary"])
        self.assertNotIn("currentness", str(runs))
        reader_record = serialize_task_for_mcp(durable_task)
        self.assertEqual(
            reader_record["verification_evidence"]["summary"]["currentness"],
            "current",
        )
        self.assertEqual(
            [
                run["currentness"]
                for run in reader_record["verification_evidence"]["runs"]
            ],
            ["superseded", "current"],
        )
        self.assertNotIn(
            "currentness",
            reader_record["verification_summary"],
        )

    def test_no_rebase_keeps_one_current_run_without_duplication(self):
        task = self._task("task-control")
        sha_a = "a" * 40

        self._verify(
            task,
            sha_a,
            "python -m unittest (100 tests)",
            "worker-a",
            "2026-08-07T10:00:00+00:00",
        )

        persisted = self.db.load_all()["board_tasks"][task.id]
        self.assertEqual(len(persisted["verification_summary"]["runs"]), 1)
        durable_state = MatrixState(self.db)
        durable_state.load()
        evidence = _task_verification_evidence(
            durable_state.board_tasks[task.id]
        )
        self.assertEqual(evidence["summary"]["currentness"], "current")
        self.assertEqual(len(evidence["runs"]), 1)

    def test_missing_tested_sha_has_unknown_currentness(self):
        task = self._task("task-legacy")

        _apply_verification_report(
            task,
            {
                "verification_state": "passed",
                "tests_run": "python -m unittest",
            },
            "legacy-worker",
            self.db.save_board_task,
            timestamp="2026-08-07T10:00:00+00:00",
        )

        evidence = _task_verification_evidence(task)
        self.assertEqual(evidence["summary"]["currentness"], "unknown")
        self.assertEqual(evidence["runs"][0]["currentness"], "unknown")

    def test_merge_only_nonmatch_is_unknown_but_exact_merge_match_is_current(self):
        task = self._task("task-merge-only")
        task.worktree_boundary = {
            "status": "merged",
            "repo_root": "/repo",
            "branch": "torque/test",
            "merge_commit_sha": "c" * 40,
        }
        self._verify(
            task,
            "b" * 40,
            "python -m unittest",
            "worker-a",
            "2026-08-07T10:00:00+00:00",
        )
        self.assertEqual(
            _task_verification_evidence(task)["summary"]["currentness"],
            "unknown",
        )

        self._verify(
            task,
            "c" * 40,
            "python -m unittest",
            "worker-b",
            "2026-08-07T11:00:00+00:00",
        )
        self.assertEqual(
            _task_verification_evidence(task)["summary"]["currentness"],
            "current",
        )


if __name__ == "__main__":
    unittest.main()
