import asyncio
from types import SimpleNamespace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from torque.worktree_boundaries import (
    attach_pr_metadata_to_latest_open_boundary,
    boundary_code_delta_state,
    boundary_pr_metadata,
    boundary_submodule_branches,
    boundary_summary,
    branch_boundary_tasks,
    classify_boundary_code_delta,
    clear_stale_successor_references,
    code_boundary_done_status,
    latest_boundary_base_branch,
    latest_boundary_task,
    mark_branch_boundaries_merged,
    queued_successor_tasks,
    refresh_latest_boundary_after_rebase,
    retarget_queued_successor_tasks,
    started_successor_tasks,
    task_branch_keys,
    advance_latest_boundary_after_mechanical_commit,
)


def _task(task_id, lane="Done", *, boundary=None,
          resume_after="", created_at="", updated_at="", labels=None,
          agent_id="agent-1", parent_task_id="", pipeline_root_id=""):
    return SimpleNamespace(
        id=task_id,
        task=f"Task {task_id}",
        slug=task_id,
        lane=lane,
        agent_id=agent_id,
        labels=list(labels or []),
        created_at=created_at,
        updated_at=updated_at,
        worktree_boundary=boundary or {},
        resume_after_boundary_task_id=resume_after,
        parent_task_id=parent_task_id,
        pipeline_root_id=pipeline_root_id,
    )


class WorktreeBoundaryTests(unittest.TestCase):
    def test_latest_open_boundary_uses_recorded_at(self):
        older = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        newer = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
            },
        )

        latest = latest_boundary_task(
            [older, newer],
            repo_root="/repo",
            branch="torque/worker",
            statuses={"open"},
        )

        self.assertIs(latest, newer)

    def test_successor_filters_split_queued_and_started(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
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
                "branch": "torque/worker",
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
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        merged_task = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "merged",
                "recorded_at": "2026-04-07T11:00:00+00:00",
            },
        )

        tasks = branch_boundary_tasks(
            [open_task, merged_task],
            repo_root="/repo",
            branch="torque/worker",
            statuses={"open"},
        )

        self.assertEqual([t.id for t in tasks], ["task-a"])

    def test_branch_boundary_tasks_can_match_submodule_branch_pair(self):
        task = _task(
            "task-a",
            boundary={
                "repo_root": "/super",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "submodules": [
                    {
                        "path": "deps/sub",
                        "repo_root": "/sub",
                        "branch": "torque/submodules/deps-sub/torque/worker",
                        "commit_sha": "sub-head",
                    }
                ],
            },
        )

        self.assertEqual(
            boundary_submodule_branches(task.worktree_boundary)[0]["path"],
            "deps/sub",
        )
        self.assertIn(
            "/sub::torque/submodules/deps-sub/torque/worker",
            task_branch_keys(task, include_submodules=True),
        )
        self.assertEqual(
            branch_boundary_tasks(
                [task],
                repo_root="/sub",
                branch="torque/submodules/deps-sub/torque/worker",
                statuses={"open"},
                include_submodules=True,
            ),
            [task],
        )

    def test_attach_pr_metadata_to_latest_open_boundary_preserves_json_shape(self):
        older = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        latest = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
                "commit_sha": "reviewed-head",
            },
        )

        updated = attach_pr_metadata_to_latest_open_boundary(
            [older, latest],
            repo_root="/repo",
            branch="torque/worker",
            pr_metadata={
                "provider": "github",
                "remote": "origin",
                "base_branch": "main",
                "head_branch": "torque/worker",
                "head_sha": "reviewed-head",
                "url": "https://github.com/acme/repo/pull/123",
                "number": "123",
                "status": "pending",
                "merge_state": "BLOCKED",
            },
            requested_cleanup={
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": False,
                "auto_move_to_done": True,
                "preserve_merge_diff": False,
            },
            now="2026-04-07T12:00:00+00:00",
        )

        self.assertIs(updated, latest)
        self.assertNotIn("pr", older.worktree_boundary)
        pr = latest.worktree_boundary["pr"]
        self.assertEqual(pr["provider"], "github")
        self.assertEqual(pr["remote"], "origin")
        self.assertEqual(pr["base_branch"], "main")
        self.assertEqual(pr["head_branch"], "torque/worker")
        self.assertEqual(pr["head_sha"], "reviewed-head")
        self.assertEqual(pr["number"], 123)
        self.assertEqual(pr["state"], "auto_merge_enabled")
        self.assertEqual(pr["merge_state"], "BLOCKED")
        self.assertEqual(pr["created_at"], "2026-04-07T12:00:00+00:00")
        self.assertEqual(pr["updated_at"], "2026-04-07T12:00:00+00:00")
        self.assertEqual(
            pr["requested_cleanup"],
            {
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": False,
                "auto_move_to_done": True,
                "preserve_merge_diff": False,
            },
        )

        round_tripped = json.loads(json.dumps(latest.worktree_boundary))
        self.assertEqual(boundary_pr_metadata(round_tripped), pr)

    def test_latest_boundary_base_branch_uses_latest_matching_boundary(self):
        older = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "develop",
                "status": "merged",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        newer = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "status": "open",
                "recorded_at": "2026-04-07T11:00:00+00:00",
            },
        )

        base_branch = latest_boundary_base_branch(
            [older, newer],
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(base_branch, "main")

    def test_latest_boundary_base_branch_returns_blank_without_match(self):
        base_branch = latest_boundary_base_branch(
            [],
            repo_root="/repo",
            branch="torque/worker",
        )

        self.assertEqual(base_branch, "")

    def test_retarget_queued_successors_tracks_latest_completed_task(self):
        first = _task("task-a")
        second = _task("task-b", lane="To Do", resume_after="")
        third = _task("task-c", lane="Backlog", resume_after="")

        updated = retarget_queued_successor_tasks(
            [first, second, third],
            agent_id="agent-1",
            boundary_task_id="task-a",
            exclude_task_id="task-a",
        )

        self.assertEqual([task.id for task in updated], ["task-b", "task-c"])
        self.assertEqual(second.resume_after_boundary_task_id, "task-a")
        self.assertEqual(third.resume_after_boundary_task_id, "task-a")

        second.lane = "Done"
        updated = retarget_queued_successor_tasks(
            [first, second, third],
            agent_id="agent-1",
            boundary_task_id="task-b",
            exclude_task_id="task-b",
        )

        self.assertEqual([task.id for task in updated], ["task-c"])
        self.assertEqual(second.resume_after_boundary_task_id, "task-a")
        self.assertEqual(third.resume_after_boundary_task_id, "task-b")

    def test_clear_stale_successor_references_drops_missing_or_closed_links(self):
        open_boundary = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        merged_boundary = _task(
            "task-b",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "merged",
                "recorded_at": "2026-04-07T11:00:00+00:00",
            },
        )
        valid = _task("task-c", lane="To Do", resume_after="task-a")
        stale = _task("task-d", lane="To Do", resume_after="task-b")
        missing = _task("task-e", lane="To Do", resume_after="task-missing")

        updated = clear_stale_successor_references(
            [open_boundary, merged_boundary, valid, stale, missing]
        )

        self.assertEqual([task.id for task in updated], ["task-d", "task-e"])
        self.assertEqual(valid.resume_after_boundary_task_id, "task-a")
        self.assertEqual(stale.resume_after_boundary_task_id, "")
        self.assertEqual(missing.resume_after_boundary_task_id, "")

    def test_refresh_latest_boundary_after_rebase_updates_tip_for_clean_history(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "old-head",
                "reason": "branch_tip_moved",
                "code_delta": {
                    "state": "present",
                    "base_sha": "old-base",
                    "commit_sha": "old-head",
                    "comparison": "fork_point..boundary",
                    "path_count": 1,
                    "classified_at": "2026-04-07T10:00:00+00:00",
                },
            },
        )
        queued = _task("task-b", lane="To Do", resume_after="task-a")

        refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
            [boundary_task, queued],
            repo_root="/repo",
            branch="torque/worker",
            worktree_path="",
            previous_head_sha="old-head",
            rebased_head_sha="new-head",
        ))

        self.assertIs(refreshed, boundary_task)
        self.assertEqual(
            boundary_task.worktree_boundary["commit_sha"],
            "new-head",
        )
        self.assertEqual(
            boundary_task.worktree_boundary["code_delta"]["commit_sha"],
            "new-head",
        )
        self.assertEqual(
            boundary_task.worktree_boundary["code_delta"]["state"],
            "unknown",
        )
        self.assertNotEqual(
            boundary_task.worktree_boundary["code_delta"]["classified_at"],
            "2026-04-07T10:00:00+00:00",
        )
        self.assertEqual(boundary_task.worktree_boundary["status"], "open")
        self.assertNotIn("reason", boundary_task.worktree_boundary)

    def test_refresh_latest_boundary_after_rebase_reclassifies_code_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Torque Test")
            (repo / "base.txt").write_text("base\n")
            git("add", "base.txt")
            git("commit", "-m", "base")
            old_base = git("rev-parse", "HEAD")
            git("checkout", "-b", "topic")
            (repo / "code.py").write_text("VALUE = 1\n")
            git("add", "code.py")
            git("commit", "-m", "topic")
            old_head = git("rev-parse", "HEAD")
            old_fact = asyncio.run(classify_boundary_code_delta(
                worktree_path=str(repo),
                base_branch="main",
                commit_sha=old_head,
            ))
            self.assertEqual(old_fact["state"], "present")
            self.assertEqual(old_fact["base_sha"], old_base)
            self.assertEqual(old_fact["commit_sha"], old_head)

            boundary_task = _task(
                "task-a",
                boundary={
                    "repo_root": str(repo),
                    "branch": "topic",
                    "base_branch": "main",
                    "status": "open",
                    "recorded_at": "2026-04-07T10:00:00+00:00",
                    "commit_sha": old_head,
                    "code_delta": old_fact,
                },
            )
            self.assertEqual(
                boundary_code_delta_state(boundary_task.worktree_boundary),
                "present",
            )

            git("checkout", "main")
            (repo / "base-advanced.txt").write_text("advanced\n")
            git("add", "base-advanced.txt")
            git("commit", "-m", "advance base")
            new_base = git("rev-parse", "HEAD")
            git("checkout", "topic")
            git("rebase", "main")
            rebased_head = git("rev-parse", "HEAD")
            self.assertNotEqual(rebased_head, old_head)

            refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
                [boundary_task],
                repo_root=str(repo),
                branch="topic",
                worktree_path=str(repo),
                previous_head_sha=old_head,
                rebased_head_sha=rebased_head,
            ))

            self.assertIs(refreshed, boundary_task)
            boundary = boundary_task.worktree_boundary
            self.assertEqual(boundary["commit_sha"], rebased_head)
            self.assertEqual(boundary["code_delta"]["commit_sha"], rebased_head)
            self.assertEqual(boundary["code_delta"]["base_sha"], new_base)
            self.assertEqual(
                boundary["code_delta"]["comparison"],
                "fork_point..boundary",
            )
            self.assertEqual(boundary["code_delta"]["state"], "present")
            self.assertNotEqual(
                boundary["code_delta"]["classified_at"],
                old_fact["classified_at"],
            )
            self.assertEqual(
                boundary_code_delta_state(boundary),
                "present",
            )
            gate = code_boundary_done_status([boundary_task])
            self.assertEqual(
                [item["task_id"] for item in gate["present"]],
                ["task-a"],
            )
            self.assertFalse(gate["eligible"])

    def test_refresh_latest_boundary_after_rebase_allows_absorbed_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Torque Test")
            (repo / "base.txt").write_text("base\n")
            git("add", "base.txt")
            git("commit", "-m", "base")
            git("checkout", "-b", "topic")
            (repo / "absorbed.txt").write_text("same patch\n")
            git("add", "absorbed.txt")
            git("commit", "-m", "topic patch")
            old_head = git("rev-parse", "HEAD")
            boundary_task = _task(
                "task-a",
                boundary={
                    "repo_root": str(repo),
                    "branch": "topic",
                    "base_branch": "main",
                    "status": "open",
                    "recorded_at": "2026-04-07T10:00:00+00:00",
                    "commit_sha": old_head,
                    "code_delta": {
                        "state": "present",
                        "base_sha": git("rev-parse", "main"),
                        "commit_sha": old_head,
                        "comparison": "fork_point..boundary",
                        "path_count": 1,
                        "classified_at": "2026-04-07T10:00:00+00:00",
                    },
                },
            )

            git("checkout", "main")
            (repo / "absorbed.txt").write_text("same patch\n")
            git("add", "absorbed.txt")
            git("commit", "-m", "absorb topic patch")
            new_base = git("rev-parse", "HEAD")
            git("checkout", "topic")
            git("rebase", "main")
            rebased_head = git("rev-parse", "HEAD")
            self.assertEqual(rebased_head, new_base)

            refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
                [boundary_task],
                repo_root=str(repo),
                branch="topic",
                worktree_path=str(repo),
                previous_head_sha=old_head,
                rebased_head_sha=rebased_head,
            ))

            self.assertIs(refreshed, boundary_task)
            boundary = boundary_task.worktree_boundary
            self.assertEqual(boundary["commit_sha"], rebased_head)
            self.assertEqual(boundary["code_delta"]["commit_sha"], rebased_head)
            self.assertEqual(boundary["code_delta"]["base_sha"], new_base)
            self.assertEqual(boundary["code_delta"]["state"], "absent")
            self.assertEqual(boundary["code_delta"]["path_count"], 0)
            gate = code_boundary_done_status([boundary_task])
            self.assertTrue(gate["eligible"])
            self.assertEqual(gate["present"], [])
            self.assertEqual(gate["unknown"], [])
            self.assertEqual(gate["blocking"], [])

    def test_refresh_latest_boundary_after_rebase_failure_stays_blocking(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "topic",
                "base_branch": "main",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "old-head",
                "code_delta": {
                    "state": "present",
                    "base_sha": "old-base",
                    "commit_sha": "old-head",
                    "comparison": "fork_point..boundary",
                    "path_count": 1,
                    "classified_at": "2026-04-07T10:00:00+00:00",
                },
            },
        )

        refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
            [boundary_task],
            repo_root="/repo",
            branch="topic",
            worktree_path="/path/that/does/not/exist",
            previous_head_sha="old-head",
            rebased_head_sha="new-head",
        ))

        self.assertIs(refreshed, boundary_task)
        boundary = boundary_task.worktree_boundary
        self.assertEqual(boundary["commit_sha"], "new-head")
        self.assertEqual(boundary["code_delta"]["commit_sha"], "new-head")
        self.assertEqual(boundary["code_delta"]["state"], "unknown")
        self.assertEqual(
            boundary["code_delta"]["reason"],
            "merge_base_failed",
        )
        self.assertNotIn("base_sha", boundary["code_delta"])
        self.assertNotIn("comparison", boundary["code_delta"])
        self.assertNotIn("path_count", boundary["code_delta"])
        self.assertNotEqual(
            boundary["code_delta"]["classified_at"],
            "2026-04-07T10:00:00+00:00",
        )
        gate = code_boundary_done_status([boundary_task])
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["present"], [])
        self.assertEqual(
            [item["task_id"] for item in gate["unknown"]],
            ["task-a"],
        )
        self.assertEqual(
            [item["task_id"] for item in gate["blocking"]],
            ["task-a"],
        )

    def test_refresh_latest_boundary_after_rebase_updates_submodule_pair_tip(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "old-head",
                "submodules": [
                    {
                        "path": "deps/sub",
                        "repo_root": "/sub",
                        "branch": "torque/submodules/deps-sub/torque/worker",
                        "commit_sha": "old-sub-head",
                    }
                ],
            },
        )

        refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
            [boundary_task],
            repo_root="/repo",
            branch="torque/worker",
            worktree_path="",
            previous_head_sha="old-head",
            rebased_head_sha="new-head",
            previous_submodules=[
                {"path": "deps/sub", "commit_sha": "old-sub-head"}
            ],
            rebased_submodules=[
                {
                    "path": "deps/sub",
                    "repo_root": "/sub",
                    "branch": "torque/submodules/deps-sub/torque/worker",
                    "commit_sha": "new-sub-head",
                }
            ],
        ))

        self.assertIs(refreshed, boundary_task)
        self.assertEqual(
            boundary_task.worktree_boundary["commit_sha"],
            "new-head",
        )
        self.assertEqual(
            boundary_task.worktree_boundary["code_delta"]["commit_sha"],
            "new-head",
        )
        self.assertEqual(
            boundary_task.worktree_boundary["submodules"][0]["commit_sha"],
            "new-sub-head",
        )

    def test_refresh_latest_boundary_after_rebase_refuses_started_successor(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "old-head",
            },
        )
        started = _task("task-b", lane="In Progress", resume_after="task-a")

        refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
            [boundary_task, started],
            repo_root="/repo",
            branch="torque/worker",
            worktree_path="",
            previous_head_sha="old-head",
            rebased_head_sha="new-head",
        ))

        self.assertIsNone(refreshed)
        self.assertEqual(
            boundary_task.worktree_boundary["commit_sha"],
            "old-head",
        )

    def test_refresh_latest_boundary_after_rebase_refuses_unexpected_old_tip(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "recorded-tip",
            },
        )

        original_boundary = json.loads(json.dumps(boundary_task.worktree_boundary))
        refreshed = asyncio.run(refresh_latest_boundary_after_rebase(
            [boundary_task],
            repo_root="/repo",
            branch="torque/worker",
            worktree_path="",
            previous_head_sha="different-old-head",
            rebased_head_sha="new-head",
        ))

        self.assertIsNone(refreshed)
        self.assertEqual(boundary_task.worktree_boundary, original_boundary)

    def test_mark_branch_boundaries_merged_updates_all_branch_boundaries(self):
        open_task = _task(
            "task-a",
            labels=["ready"],
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
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
                "branch": "torque/worker",
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
                "branch": "torque/other",
                "status": "open",
                "recorded_at": "2026-04-07T12:00:00+00:00",
            },
        )

        updated = mark_branch_boundaries_merged(
            [open_task, superseded_task, queued_followup, other_branch],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="abc123",
            task_ids=["task-a", "task-b"],
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

    def test_mark_branch_boundaries_merged_does_not_contaminate_paused_task(self):
        """A shared worker branch is not evidence that both tasks shipped."""
        merged_task = _task(
            "TORQUE:1290",
            labels=["ready"],
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-07-28T10:00:00+00:00",
            },
        )
        paused_task = _task(
            "TORQUE:1298",
            lane="In Progress",
            labels=["paused"],
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-07-28T11:00:00+00:00",
            },
        )

        updated = mark_branch_boundaries_merged(
            [merged_task, paused_task],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="landed1290",
            task_ids=[merged_task.id],
            merged_at="2026-07-28T12:00:00+00:00",
        )

        self.assertEqual([task.id for task in updated], [merged_task.id])
        self.assertEqual(merged_task.worktree_boundary["status"], "merged")
        self.assertEqual(paused_task.worktree_boundary["status"], "open")
        self.assertNotIn("merge_commit_sha", paused_task.worktree_boundary)
        self.assertEqual(paused_task.labels, ["paused"])

    def test_mark_branch_boundaries_merged_marks_pipeline_root_without_boundary(self):
        root = _task("task-root", lane="In Progress", labels=["ready"])
        review = _task(
            "task-review",
            labels=["reviewed"],
            parent_task_id="task-root",
            pipeline_root_id="task-root",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "commit_sha": "abc123",
                "kind": "checkpoint",
                "status": "open",
                "recorded_at": " 2026-04-07T10:00:00+00:00 ",
                "recorded_by_agent_id": "agent-1",
                "message": "Review complete",
                "superseded_by_task_id": "",
            },
        )
        unrelated = _task("task-other", lane="Done", labels=["ready"])

        updated = mark_branch_boundaries_merged(
            [root, review, unrelated],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="deadbeef",
            task_ids=[review.id],
            merged_at="2026-04-07T11:00:00+00:00",
        )

        self.assertEqual(
            [task.id for task in updated],
            ["task-review", "task-root"],
        )
        self.assertEqual(root.labels, ["ready", "merged"])
        self.assertEqual(root.worktree_boundary["status"], "merged")
        self.assertEqual(root.worktree_boundary["repo_root"], "/repo")
        self.assertEqual(root.worktree_boundary["branch"], "torque/worker")
        self.assertEqual(root.worktree_boundary["merge_commit_sha"], "deadbeef")
        self.assertEqual(
            root.worktree_boundary["merged_at"],
            "2026-04-07T11:00:00+00:00",
        )
        self.assertEqual(root.worktree_boundary["commit_sha"], "abc123")
        self.assertEqual(
            root.worktree_boundary["recorded_at"],
            "2026-04-07T10:00:00+00:00",
        )
        self.assertEqual(
            root.worktree_boundary["recorded_by_agent_id"],
            "agent-1",
        )
        self.assertEqual(root.worktree_boundary["message"], "")

        self.assertEqual(review.labels, ["reviewed", "merged"])
        self.assertEqual(review.worktree_boundary["status"], "merged")
        self.assertEqual(unrelated.labels, ["ready"])
        self.assertEqual(unrelated.worktree_boundary, {})

    def test_mark_branch_boundaries_merged_preserves_existing_root_boundary(self):
        root = _task(
            "task-root",
            lane="In Progress",
            labels=["ready"],
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "commit_sha": "root-head",
                "kind": "checkpoint",
                "status": "open",
                "recorded_at": "2026-04-07T09:00:00+00:00",
                "recorded_by_agent_id": "root-agent",
                "message": "Root checkpoint",
                "superseded_by_task_id": "",
            },
        )
        review = _task(
            "task-review",
            labels=["reviewed"],
            parent_task_id="task-root",
            pipeline_root_id="task-root",
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "commit_sha": "reviewed-head",
                "kind": "review",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "recorded_by_agent_id": "review-agent",
                "message": "Review complete",
                "superseded_by_task_id": "",
            },
        )

        updated = mark_branch_boundaries_merged(
            [root, review],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="deadbeef",
            task_ids=[review.id],
            merged_at="2026-04-07T11:00:00+00:00",
        )

        self.assertEqual(
            [task.id for task in updated],
            ["task-review", "task-root"],
        )
        boundary = root.worktree_boundary
        self.assertEqual(boundary["recorded_at"], "2026-04-07T09:00:00+00:00")
        self.assertEqual(boundary["recorded_by_agent_id"], "root-agent")
        self.assertEqual(boundary["message"], "Root checkpoint")
        self.assertEqual(boundary["commit_sha"], "root-head")
        self.assertEqual(boundary["status"], "merged")
        self.assertEqual(boundary["merged_at"], "2026-04-07T11:00:00+00:00")
        self.assertEqual(boundary["merge_commit_sha"], "deadbeef")

    def test_mark_branch_boundaries_merged_updates_existing_pr_metadata(self):
        review = _task(
            "task-review",
            labels=["reviewed"],
            boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "commit_sha": "reviewed-head",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
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
                    "created_at": "2026-04-07T10:30:00+00:00",
                    "updated_at": "2026-04-07T10:30:00+00:00",
                    "requested_cleanup": {
                        "close_agent_on_merge": True,
                        "remove_worktree_on_merge": False,
                        "auto_move_to_done": True,
                        "preserve_merge_diff": False,
                    },
                },
            },
        )

        updated = mark_branch_boundaries_merged(
            [review],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="squash-sha",
            task_ids=[review.id],
            merged_at="2026-04-07T11:00:00+00:00",
            pr_metadata={"merge_state": "CLEAN"},
        )

        self.assertEqual([task.id for task in updated], [review.id])
        boundary = review.worktree_boundary
        self.assertEqual(boundary["status"], "merged")
        self.assertEqual(boundary["commit_sha"], "reviewed-head")
        self.assertEqual(boundary["merge_commit_sha"], "squash-sha")
        pr = boundary["pr"]
        self.assertEqual(pr["state"], "merged")
        self.assertEqual(pr["merge_state"], "CLEAN")
        self.assertEqual(pr["merge_commit_sha"], "squash-sha")
        self.assertEqual(pr["merged_at"], "2026-04-07T11:00:00+00:00")
        self.assertEqual(pr["updated_at"], "2026-04-07T11:00:00+00:00")
        self.assertEqual(pr["head_sha"], "reviewed-head")
        self.assertEqual(pr["requested_cleanup"]["close_agent_on_merge"], True)

    def test_advance_boundary_requires_machine_verified_gitlink(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "old",
            },
        )

        updated, result = advance_latest_boundary_after_mechanical_commit(
            [boundary_task],
            repo_root="/repo",
            branch="torque/worker",
            expected_previous_head="old",
            new_head="new",
            machine_verification={"ok": False, "reason": "non_gitlink_diff"},
            verification_note="human says this was mechanical",
        )

        self.assertIsNone(updated)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "machine_verification_failed")
        self.assertEqual(boundary_task.worktree_boundary["commit_sha"], "old")

    def test_advance_boundary_records_audit_after_machine_verified_gitlink(self):
        boundary_task = _task(
            "task-a",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
                "commit_sha": "old",
                "submodules": [{"path": "ee", "commit_sha": "old-ee"}],
            },
        )

        updated, result = advance_latest_boundary_after_mechanical_commit(
            [boundary_task],
            repo_root="/repo",
            branch="torque/worker",
            expected_previous_head="old",
            new_head="new",
            machine_verification={
                "ok": True,
                "mechanical_commit": "new",
                "paths": ["ee"],
                "submodules": [{"path": "ee", "commit_sha": "new-ee"}],
            },
            actor_agent_id="eng-1",
            verification_note="verified gitlink-only ee bump",
            now="2026-04-07T12:00:00+00:00",
        )

        self.assertIs(updated, boundary_task)
        self.assertTrue(result["ok"])
        boundary = boundary_task.worktree_boundary
        self.assertEqual(boundary["commit_sha"], "new")
        self.assertEqual(boundary["submodules"][0]["commit_sha"], "new-ee")
        audit = boundary["mechanical_advances"][0]
        self.assertEqual(audit["previous_head"], "old")
        self.assertEqual(audit["new_head"], "new")
        self.assertEqual(audit["paths"], ["ee"])
