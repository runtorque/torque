"""Explicit attribution for released tasks with an owned open boundary."""
import unittest
from types import SimpleNamespace

from torque.services.worktrees.preflight import (
    _merge_attribution_task_id,
    _merge_task_attribution_error,
)
from torque.state import _compact_worktree_boundary_summary


class ReleasedTaskMergeAttributionTests(unittest.TestCase):
    def setUp(self):
        self.worker = SimpleNamespace(
            id="worker", group="qa", worktree_repo_root="/repo",
            git_root="/repo", worktree_branch="torque/worker",
            worktree_base_branch="main", driverless=False,
        )
        self.boundary = {
            "status": "open", "recorded_by_agent_id": "worker",
            "repo_root": "/repo", "branch": "torque/worker",
            "base_branch": "main", "commit_sha": "reviewed-head",
        }
        self.task = SimpleNamespace(
            id="QA:1", group="qa", agent_id="", lane="In Progress",
            worktree_boundary=dict(self.boundary),
        )
        self.state = SimpleNamespace(
            board_tasks={self.task.id: self.task},
            agent_active_tasks=lambda _id: [],
        )
        self.data = {"merge_task_id": self.task.id}

    def test_explicit_released_boundary_retains_implementation_attribution(self):
        self.assertEqual(
            _merge_attribution_task_id(self.state, self.worker, self.data),
            self.task.id,
        )
        self.assertIsNone(_merge_task_attribution_error(
            state=self.state, cell=self.worker, aid=self.worker.id,
            data=self.data,
        ))

    def test_released_boundary_never_becomes_automatic_attribution(self):
        self.assertEqual(_merge_attribution_task_id(
            self.state, self.worker, {}), "")
        result = _merge_task_attribution_error(
            state=self.state, cell=self.worker, aid=self.worker.id, data={},
        )
        self.assertEqual(result["code"], "merge_task_attribution_missing")

    def test_released_boundary_requires_complete_matching_provenance(self):
        for key, value in [
            ("status", "merged"), ("status", "superseded"),
            ("recorded_by_agent_id", "peer"), ("repo_root", "/other"),
            ("branch", "torque/peer"), ("base_branch", "other"),
            ("commit_sha", ""),
            *[(key, "") for key in self.boundary if key != "commit_sha"],
        ]:
            with self.subTest(key=key, value=value):
                self.task.worktree_boundary = {**self.boundary, key: value}
                self.assertEqual(_merge_attribution_task_id(
                    self.state, self.worker, self.data), "")

    def test_reassigned_and_cross_group_tasks_stay_blocked(self):
        self.task.agent_id = "peer"
        self.assertEqual(_merge_attribution_task_id(
            self.state, self.worker, self.data), "")
        self.task.agent_id = ""
        self.task.group = "other"
        self.assertEqual(_merge_attribution_task_id(
            self.state, self.worker, self.data), "")

    def test_compact_boundary_keeps_explicit_merge_selector_provenance(self):
        compact = _compact_worktree_boundary_summary(self.boundary)
        self.assertEqual(compact["recorded_by_agent_id"], "worker")
        self.assertEqual(compact["commit_sha"], "reviewed-head")
