import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.db import TorqueDB
from torque.server_evidence import (
    _MERGE_DAEMON_CODE_REVISION_UNAVAILABLE,
    _MERGE_DAEMON_CODE_REVISION_UNVERSIONED,
    _merge_daemon_code_revision,
    _record_merge_completion_evidence,
)
from torque.state import BoardTask, MatrixState


class MergeDaemonRevisionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)

    def _state_with_merged_task(
            self, task_id: str, *, completion_evidence=None) -> tuple:
        task = BoardTask(
            id=task_id,
            task="Synthetic merged task",
            group="g",
            lane="In Progress",
            completion_evidence=completion_evidence or {},
            worktree_boundary={
                "status": "merged",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "merge_commit_sha": "merge-sha",
            },
        )
        self.db.save_board_task(task)
        state = MatrixState(self.db)
        state.load()
        return state, state.board_tasks[task_id]

    def _record(self, state, task_id: str) -> None:
        updated = _record_merge_completion_evidence(
            state,
            result={"ok": True, "sha": "merge-sha", "mode": "direct"},
            task_ids=[task_id],
            repo_root="/repo",
            branch="torque/worker",
        )
        self.assertEqual(updated, [task_id])

    def test_known_boot_revision_survives_state_reload_from_database(self):
        state, task = self._state_with_merged_task("task-known")
        state.boot_head_commit = "  daemon-code-sha  "

        self._record(state, task.id)
        self.assertEqual(
            task.completion_evidence["merge"]["daemon_code_revision"],
            "daemon-code-sha",
        )

        # Discard the writer's in-memory state and simulate a daemon restart.
        del task
        del state
        reloaded = MatrixState(self.db)
        reloaded.load()

        persisted_merge = reloaded.board_tasks[
            "task-known"
        ].completion_evidence["merge"]
        self.assertEqual(
            persisted_merge["daemon_code_revision"],
            "daemon-code-sha",
        )
        self.assertEqual(
            _merge_daemon_code_revision(persisted_merge),
            "daemon-code-sha",
        )

    def test_legacy_merge_without_revision_reads_as_unversioned(self):
        legacy = {
            "version": 1,
            "sources": ["merge"],
            "merge": {"sha": "merge-sha", "mode": "direct"},
        }
        _state, task = self._state_with_merged_task(
            "task-legacy",
            completion_evidence=legacy,
        )

        persisted_merge = self.db.load_all()["board_tasks"][
            task.id
        ]["completion_evidence"]["merge"]
        self.assertNotIn("daemon_code_revision", persisted_merge)
        self.assertEqual(
            _merge_daemon_code_revision(persisted_merge),
            _MERGE_DAEMON_CODE_REVISION_UNVERSIONED,
        )

    def test_empty_boot_revision_records_unavailable_with_bounded_reason(self):
        state, task = self._state_with_merged_task("task-unavailable")
        state.boot_head_commit = "  "
        state.boot_head_error = "capture failed: " + ("x" * 1000)

        self._record(state, task.id)

        merge = task.completion_evidence["merge"]
        self.assertEqual(
            merge["daemon_code_revision"],
            _MERGE_DAEMON_CODE_REVISION_UNAVAILABLE,
        )
        self.assertTrue(merge["daemon_code_revision_reason"].startswith(
            "capture failed:"
        ))
        self.assertLessEqual(len(merge["daemon_code_revision_reason"]), 500)

    def test_detached_head_records_revision_and_bounded_caveat(self):
        state, task = self._state_with_merged_task("task-detached")
        state.boot_head_commit = "detached-head-sha"
        state.boot_head_error = (
            "repository is in detached HEAD state: " + ("x" * 1000)
        )

        self._record(state, task.id)

        merge = task.completion_evidence["merge"]
        self.assertEqual(
            merge["daemon_code_revision"],
            "detached-head-sha",
        )
        self.assertTrue(merge["daemon_code_revision_caveat"].startswith(
            "repository is in detached HEAD state:"
        ))
        self.assertLessEqual(
            len(merge["daemon_code_revision_caveat"]),
            500,
        )
        self.assertNotIn("daemon_code_revision_reason", merge)


if __name__ == "__main__":
    unittest.main()
