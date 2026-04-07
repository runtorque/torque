from types import SimpleNamespace
import unittest

from loom.worktree_boundaries import (
    boundary_summary,
    branch_boundary_tasks,
    latest_boundary_task,
    mark_branch_boundaries_merged,
    queued_successor_tasks,
    started_successor_tasks,
)


def _task(task_id, lane="Done", *, boundary=None,
          resume_after="", created_at="", updated_at="", labels=None):
    return SimpleNamespace(
        id=task_id,
        task=f"Task {task_id}",
        slug=task_id,
        lane=lane,
        labels=list(labels or []),
        created_at=created_at,
        updated_at=updated_at,
        worktree_boundary=boundary or {},
        resume_after_boundary_task_id=resume_after,
    )


class WorktreeBoundaryTests(unittest.TestCase):
    def test_latest_open_boundary_uses_recorded_at(self):
        older = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        newer = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
            },
        )

        latest = latest_boundary_task(
            [older, newer],
            repo_root="/repo",
            branch="loom/worker",
            statuses={"open"},
        )

        self.assertIs(latest, newer)

    def test_successor_filters_split_queued_and_started(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        queued = _task("task-b", lane="To Do", resume_after="task-a")
        started = _task("task-c", lane="In Progress", resume_after="task-a")

        self.assertEqual(
            [t.id for t in queued_successor_tasks(
                [boundary_task, queued, started], "task-a"
            )],
            ["task-b"],
        )
        self.assertEqual(
            [t.id for t in started_successor_tasks(
                [boundary_task, queued, started], "task-a"
            )],
            ["task-c"],
        )

    def test_boundary_summary_includes_followers(self):
        task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "abcdef0123456789",
            },
        )
        queued = _task("task-b", lane="To Do", resume_after="task-a")

        summary = boundary_summary(task, queued_followers=[queued])

        self.assertEqual(summary["task_id"], "task-a")
        self.assertEqual(summary["boundary"]["commit_sha"], "abcdef0123456789")
        self.assertEqual(summary["queued_followers"][0]["task_id"], "task-b")

    def test_branch_boundary_tasks_filter_status(self):
        open_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        merged_task = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "merged",
                "recorded_at": "2026-04-07T11:00:00+00:00",
            },
        )

        tasks = branch_boundary_tasks(
            [open_task, merged_task],
            repo_root="/repo",
            branch="loom/worker",
            statuses={"open"},
        )

        self.assertEqual([t.id for t in tasks], ["task-a"])

    def test_mark_branch_boundaries_merged_updates_all_branch_boundaries(self):
        open_task = _task(
            "task-a",
            labels=["ready"],
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "reason": "checkpoint_failed",
            },
        )
        superseded_task = _task(
            "task-b",
            labels=["merged", "reviewed"],
            boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "superseded",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "reason": "superseded_by_newer_task",
            },
        )
        queued_followup = _task(
            "task-c",
            lane="To Do",
            labels=["ready"],
            resume_after="task-b",
        )
        other_branch = _task(
            "task-d",
            labels=["ready"],
            boundary={
                "repo_root": "/repo",
                "branch": "loom/other",
                "status": "open",
                "recorded_at": "2026-04-07T12:00:00+00:00",
            },
        )

        updated = mark_branch_boundaries_merged(
            [open_task, superseded_task, queued_followup, other_branch],
            repo_root="/repo",
            branch="loom/worker",
            merge_sha="abc123",
            merged_at="2026-04-07T13:00:00+00:00",
        )

        self.assertEqual([task.id for task in updated], ["task-a", "task-b"])
        self.assertEqual(open_task.worktree_boundary["status"], "merged")
        self.assertEqual(
            open_task.worktree_boundary["merged_at"],
            "2026-04-07T13:00:00+00:00",
        )
        self.assertEqual(open_task.worktree_boundary["merge_commit_sha"], "abc123")
        self.assertNotIn("reason", open_task.worktree_boundary)
        self.assertEqual(open_task.labels, ["ready", "merged"])

        self.assertEqual(superseded_task.worktree_boundary["status"], "merged")
        self.assertEqual(superseded_task.labels, ["merged", "reviewed"])

        self.assertEqual(queued_followup.labels, ["ready"])
        self.assertEqual(other_branch.worktree_boundary["status"], "open")
        self.assertEqual(other_branch.labels, ["ready"])
