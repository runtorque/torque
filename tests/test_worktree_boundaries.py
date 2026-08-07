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
    review_cycle_containment_candidates,
    refresh_latest_boundary_after_rebase,
    retarget_queued_successor_tasks,
    started_successor_tasks,
    task_branch_keys,
    advance_latest_boundary_after_mechanical_commit,
    verified_review_cycle_containment_task_ids,
)


def _task(task_id, lane="Done", *, boundary=None,
          resume_after="", created_at="", updated_at="", labels=None,
          agent_id="agent-1", parent_task_id="", pipeline_root_id="",
          action_name="", completion_evidence=None):
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
        action_name=action_name,
        completion_evidence=completion_evidence or {},
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

    def test_mark_merge_consumes_only_explicit_review_cycle_predecessor(self):
        root = _task("TORQUE:1", lane="In Progress")
        predecessor = _task(
            "TORQUE:1:1",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            completion_evidence={"review_cycle_continuations": [{
                "original_review_task_id": "TORQUE:1:1",
                "continuation_task_id": "TORQUE:1:2",
                "pipeline_root_id": root.id,
                "repo_root": "/repo",
                "branch": "torque/worker",
            }]},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
                "superseded_by_task_id": "TORQUE:1:2",
                "recorded_at": "2026-08-07T10:00:00+00:00",
                "code_delta": {"state": "present"},
            },
        )
        continuation = _task(
            "TORQUE:1:2",
            action_name="feature/implement",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            completion_evidence={"review_cycle_continue": {
                "original_review_task_id": predecessor.id,
                "continuation_task_id": "TORQUE:1:2",
            }},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
                "recorded_at": "2026-08-07T11:00:00+00:00",
            },
        )
        fresh_review = _task(
            "TORQUE:1:3",
            action_name="feature/review",
            parent_task_id=continuation.id,
            pipeline_root_id=root.id,
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
                "recorded_at": "2026-08-07T12:00:00+00:00",
            },
        )

        linked = review_cycle_containment_candidates(
            [root, predecessor, continuation, fresh_review],
            repo_root="/repo",
            branch="torque/worker",
            task_ids=[continuation.id, fresh_review.id],
        )
        self.assertEqual([task.id for task in linked], [predecessor.id])

        updated = mark_branch_boundaries_merged(
            [root, predecessor, continuation, fresh_review],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="landed",
            task_ids=[continuation.id, fresh_review.id, predecessor.id],
        )

        self.assertEqual(
            {task.id for task in updated},
            {root.id, predecessor.id, continuation.id, fresh_review.id},
        )
        self.assertEqual(
            predecessor.worktree_boundary["merge_commit_sha"], "landed"
        )
        self.assertEqual(root.worktree_boundary["merge_commit_sha"], "landed")
        self.assertTrue(code_boundary_done_status(
            [root, predecessor, continuation, fresh_review]
        )["eligible"])

    def test_mark_merge_does_not_stamp_linked_predecessor_without_fresh_review(self):
        predecessor = _task(
            "TORQUE:1:1",
            action_name="feature/review",
            pipeline_root_id="TORQUE:1",
            completion_evidence={"review_cycle_continuations": [{
                "original_review_task_id": "TORQUE:1:1",
                "continuation_task_id": "TORQUE:1:2",
                "pipeline_root_id": "TORQUE:1",
                "repo_root": "/repo",
                "branch": "torque/worker",
            }]},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
                "superseded_by_task_id": "TORQUE:1:2",
            },
        )
        continuation = _task(
            "TORQUE:1:2",
            action_name="feature/implement",
            pipeline_root_id="TORQUE:1",
            completion_evidence={"review_cycle_continue": {
                "original_review_task_id": predecessor.id,
                "continuation_task_id": "TORQUE:1:2",
            }},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
            },
        )

        mark_branch_boundaries_merged(
            [predecessor, continuation],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="landed",
            task_ids=[continuation.id],
        )

        self.assertEqual(
            predecessor.worktree_boundary["status"], "superseded"
        )
        self.assertNotIn(
            "merge_commit_sha", predecessor.worktree_boundary
        )

    def test_mark_merge_does_not_stamp_sibling_or_reroute_boundary(self):
        predecessor = _task(
            "TORQUE:1:1",
            action_name="feature/review",
            pipeline_root_id="TORQUE:1",
            completion_evidence={"review_cycle_continuations": [{
                "original_review_task_id": "TORQUE:1:1",
                "continuation_task_id": "TORQUE:1:2",
                "pipeline_root_id": "TORQUE:1",
                "repo_root": "/repo",
                "branch": "torque/worker",
            }]},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
                "superseded_by_task_id": "TORQUE:1:2",
            },
        )
        continuation = _task(
            "TORQUE:1:2",
            action_name="feature/implement",
            pipeline_root_id="TORQUE:1",
            completion_evidence={"review_cycle_continue": {
                "original_review_task_id": predecessor.id,
                "continuation_task_id": "TORQUE:1:2",
            }},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
            },
        )
        fresh_review = _task(
            "TORQUE:1:3",
            action_name="feature/review",
            parent_task_id=continuation.id,
            pipeline_root_id="TORQUE:1",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "open",
            },
        )
        sibling = _task(
            "TORQUE:1:4",
            action_name="feature/review",
            pipeline_root_id="TORQUE:1",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/sibling",
                "status": "superseded",
            },
        )
        reroute = _task(
            "TORQUE:2",
            action_name="feature/review",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
            },
        )

        mark_branch_boundaries_merged(
            [predecessor, continuation, fresh_review, sibling, reroute],
            repo_root="/repo",
            branch="torque/worker",
            merge_sha="landed",
            task_ids=[continuation.id, fresh_review.id],
        )

        self.assertEqual(sibling.worktree_boundary["status"], "superseded")
        self.assertEqual(reroute.worktree_boundary["status"], "superseded")
        self.assertNotIn("merge_commit_sha", sibling.worktree_boundary)
        self.assertNotIn("merge_commit_sha", reroute.worktree_boundary)

    def test_review_cycle_containment_candidates_exclude_sibling_and_reroute(self):
        root = _task("TORQUE:1", lane="In Progress")
        predecessor = _task(
            "TORQUE:1:1",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            completion_evidence={"review_cycle_continuations": [{
                "original_review_task_id": "TORQUE:1:1",
                "continuation_task_id": "TORQUE:1:2",
                "pipeline_root_id": root.id,
                "repo_root": "/repo",
                "branch": "torque/worker",
            }]},
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
                "superseded_by_task_id": "TORQUE:1:2",
            },
        )
        continuation = _task(
            "TORQUE:1:2",
            action_name="feature/implement",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            completion_evidence={"review_cycle_continue": {
                "original_review_task_id": predecessor.id,
                "continuation_task_id": "TORQUE:1:2",
            }},
        )
        fresh_review = _task(
            "TORQUE:1:3",
            action_name="feature/review",
            parent_task_id=continuation.id,
            pipeline_root_id=root.id,
        )
        sibling = _task(
            "TORQUE:1:4",
            action_name="feature/review",
            pipeline_root_id=root.id,
            boundary={
                "repo_root": "/repo",
                "branch": "torque/sibling",
                "status": "superseded",
            },
        )
        reroute = _task(
            "TORQUE:2",
            action_name="feature/review",
            boundary={
                "repo_root": "/repo",
                "branch": "torque/worker",
                "status": "superseded",
            },
        )

        candidates = review_cycle_containment_candidates(
            [root, predecessor, continuation, fresh_review, sibling, reroute],
            repo_root="/repo",
            branch="torque/worker",
            task_ids=[continuation.id, fresh_review.id],
        )

        self.assertEqual([task.id for task in candidates], [predecessor.id])

    def test_verified_review_cycle_containment_uses_base_aware_noop_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args, input_text=None):
                result = subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    input=input_text,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return result.stdout.strip()

            def commit(message):
                git("add", "-A")
                git("commit", "-m", message)
                return git("rev-parse", "HEAD")

            def linked_tasks(prefix, base_sha, boundary_sha):
                root = _task(
                    f"{prefix}:root",
                    lane="In Progress",
                    action_name="feature/implement",
                )
                predecessor = _task(
                    f"{prefix}:review-1",
                    action_name="feature/review",
                    parent_task_id=root.id,
                    pipeline_root_id=root.id,
                    completion_evidence={"review_cycle_continuations": [{
                        "original_review_task_id": f"{prefix}:review-1",
                        "continuation_task_id": f"{prefix}:fix",
                        "pipeline_root_id": root.id,
                        "repo_root": str(repo),
                        "branch": "topic",
                    }]},
                    boundary={
                        "repo_root": str(repo),
                        "branch": "topic",
                        "commit_sha": boundary_sha,
                        "status": "superseded",
                        "superseded_by_task_id": f"{prefix}:fix",
                        "code_delta": {
                            "state": "present",
                            "base_sha": base_sha,
                            "commit_sha": boundary_sha,
                        },
                    },
                )
                continuation = _task(
                    f"{prefix}:fix",
                    action_name="feature/implement",
                    parent_task_id=root.id,
                    pipeline_root_id=root.id,
                    completion_evidence={"review_cycle_continue": {
                        "original_review_task_id": predecessor.id,
                        "continuation_task_id": f"{prefix}:fix",
                    }},
                )
                review = _task(
                    f"{prefix}:review-2",
                    action_name="feature/review",
                    parent_task_id=continuation.id,
                    pipeline_root_id=root.id,
                )
                return [root, predecessor, continuation, review]

            def verify(tasks, merge_sha):
                return asyncio.run(
                    verified_review_cycle_containment_task_ids(
                        tasks,
                        repo_root=str(repo),
                        branch="topic",
                        merge_sha=merge_sha,
                        task_ids=[tasks[-2].id, tasks[-1].id],
                    )
                )

            git("init", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Torque Test")
            (repo / "base.txt").write_text("base\n")
            base_0 = commit("base")

            # Rebased boundary: its stored base is the advanced base, and the
            # squash landing exactly contains that rebased commit.
            git("checkout", "-b", "rebased")
            (repo / "feature.txt").write_text("candidate\n")
            commit("candidate before rebase")
            git("checkout", "main")
            (repo / "base.txt").write_text("advanced\n")
            rebased_base = commit("advance base")
            git("checkout", "rebased")
            git("rebase", "main")
            rebased_boundary = git("rev-parse", "HEAD")
            git("checkout", "main")
            git("merge", "--squash", "rebased")
            rebased_merge = commit("squash rebased candidate")
            rebased_tasks = linked_tasks(
                "rebased", rebased_base, rebased_boundary
            )
            self.assertEqual(
                verify(rebased_tasks, rebased_merge),
                (rebased_tasks[1].id,),
            )

            # An additive fix after the earlier boundary changes the final
            # tree, but merging the earlier candidate into it remains a no-op.
            additive_base = rebased_merge
            git("checkout", "-b", "additive")
            (repo / "second.txt").write_text("boundary\n")
            additive_boundary = commit("earlier boundary")
            (repo / "fix.txt").write_text("later additive fix\n")
            commit("additive fix")
            git("checkout", "main")
            git("merge", "--squash", "additive")
            additive_merge = commit("squash with additive fix")
            additive_tasks = linked_tasks(
                "additive", additive_base, additive_boundary
            )
            self.assertEqual(
                verify(additive_tasks, additive_merge),
                (additive_tasks[1].id,),
            )

            # Reapplying the candidate after the base changed the same path is
            # rejected even though merge-tree alone would report a no-op.
            overlap_base = additive_merge
            git("checkout", "-b", "overlap-candidate")
            (repo / "shared.txt").write_text("candidate\n")
            overlap_boundary = commit("overlap candidate")
            git("checkout", "main")
            (repo / "shared.txt").write_text("base movement\n")
            commit("overlapping base movement")
            git("checkout", "-b", "overlap-replay")
            (repo / "shared.txt").write_text("candidate\n")
            commit("reapply after overlap")
            git("checkout", "main")
            git("merge", "--squash", "overlap-replay")
            overlap_merge = commit("squash overlap replay")
            overlap_tasks = linked_tasks(
                "overlap", overlap_base, overlap_boundary
            )
            self.assertEqual(verify(overlap_tasks, overlap_merge), ())
            mark_branch_boundaries_merged(
                overlap_tasks,
                repo_root=str(repo),
                branch="topic",
                merge_sha=overlap_merge,
                task_ids=[overlap_tasks[-2].id, overlap_tasks[-1].id],
            )
            self.assertEqual(
                overlap_tasks[1].worktree_boundary["status"], "superseded"
            )
            self.assertNotIn(
                "merge_commit_sha", overlap_tasks[1].worktree_boundary
            )
            self.assertFalse(
                code_boundary_done_status(overlap_tasks)["eligible"]
            )

            # The same replay is safe when base movement is path-disjoint.
            disjoint_base = overlap_merge
            git("checkout", "-b", "disjoint-candidate")
            (repo / "delta.txt").write_text("candidate\n")
            disjoint_boundary = commit("disjoint candidate")
            git("checkout", "main")
            (repo / "unrelated.txt").write_text("base movement\n")
            commit("disjoint base movement")
            git("checkout", "-b", "disjoint-replay")
            (repo / "delta.txt").write_text("candidate\n")
            commit("reapply after disjoint movement")
            git("checkout", "main")
            git("merge", "--squash", "disjoint-replay")
            disjoint_merge = commit("squash disjoint replay")
            disjoint_tasks = linked_tasks(
                "disjoint", disjoint_base, disjoint_boundary
            )
            self.assertEqual(
                verify(disjoint_tasks, disjoint_merge),
                (disjoint_tasks[1].id,),
            )

            single_root = _task(
                "single:root",
                lane="In Progress",
                action_name="feature/implement",
                boundary={
                    "repo_root": str(repo),
                    "branch": "topic",
                    "commit_sha": disjoint_merge,
                    "status": "open",
                    "code_delta": {
                        "state": "present",
                        "base_sha": disjoint_base,
                        "commit_sha": disjoint_merge,
                    },
                },
            )
            single_review = _task(
                "single:review",
                action_name="feature/review",
                parent_task_id=single_root.id,
                pipeline_root_id=single_root.id,
                boundary={
                    "repo_root": str(repo),
                    "branch": "topic",
                    "commit_sha": disjoint_merge,
                    "status": "open",
                    "code_delta": {
                        "state": "present",
                        "base_sha": disjoint_base,
                        "commit_sha": disjoint_merge,
                    },
                },
            )
            single_candidates = review_cycle_containment_candidates(
                [single_root, single_review],
                repo_root=str(repo),
                branch="topic",
                task_ids=[single_root.id, single_review.id],
            )
            self.assertEqual(single_candidates, [])
            single_verified = asyncio.run(
                verified_review_cycle_containment_task_ids(
                    [single_root, single_review],
                    repo_root=str(repo),
                    branch="topic",
                    merge_sha=disjoint_merge,
                    task_ids=[single_root.id, single_review.id],
                )
            )
            self.assertEqual(single_verified, ())
            single_updated = mark_branch_boundaries_merged(
                [single_root, single_review],
                repo_root=str(repo),
                branch="topic",
                merge_sha=disjoint_merge,
                task_ids=[single_root.id, single_review.id],
            )
            self.assertEqual(
                {task.id for task in single_updated},
                {single_root.id, single_review.id},
            )

            self.assertTrue(base_0)

    def test_plain_review_containment_requires_exact_structure_per_hop(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args):
                result = subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return result.stdout.strip()

            def commit(message):
                git("add", "-A")
                git("commit", "-m", message)
                return git("rev-parse", "HEAD")

            git("init", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Torque Test")
            (repo / "base.txt").write_text("base\n")
            base_sha = commit("base")
            git("checkout", "-b", "topic")
            (repo / "feature.txt").write_text("contained\n")
            contained_sha = commit("contained review boundary")
            git("checkout", "main")
            git("merge", "--squash", "topic")
            merge_sha = commit("squash contained boundary")
            git("checkout", "-b", "uncontained", base_sha)
            (repo / "uncontained.txt").write_text("not landed\n")
            uncontained_sha = commit("uncontained boundary")
            git("checkout", "main")

            root = _task(
                "root",
                lane="In Progress",
                action_name="feature/implement",
            )
            implementation = _task(
                "root:implementation",
                action_name="feature/implement",
                parent_task_id=root.id,
                pipeline_root_id=root.id,
            )

            def boundary(commit_sha, *, branch="topic", superseded_by,
                         recorded_at):
                return {
                    "repo_root": str(repo),
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "status": "superseded",
                    "superseded_by_task_id": superseded_by,
                    "recorded_at": recorded_at,
                    "code_delta": {
                        "state": "present",
                        "base_sha": base_sha,
                        "commit_sha": commit_sha,
                    },
                }

            selected_review = _task(
                "root:review-final",
                action_name="feature/review",
                parent_task_id=implementation.id,
                pipeline_root_id=root.id,
                boundary={
                    "repo_root": str(repo),
                    "branch": "topic",
                    "commit_sha": contained_sha,
                    "status": "open",
                    "recorded_at": "2026-08-07T15:00:00+00:00",
                    "code_delta": {
                        "state": "present",
                        "base_sha": base_sha,
                        "commit_sha": contained_sha,
                    },
                },
            )
            valid = _task(
                "root:review-valid",
                action_name="feature/review",
                parent_task_id=implementation.id,
                pipeline_root_id=root.id,
                completion_evidence={
                    # Verdict is deliberately irrelevant to admission.
                    "review": {"verdict": "block"},
                },
                boundary=boundary(
                    contained_sha,
                    superseded_by=selected_review.id,
                    recorded_at="2026-08-07T14:55:00+00:00",
                ),
            )
            other_root_exact_edge = _task(
                "other-root:review",
                action_name="feature/review",
                pipeline_root_id="other-root",
                boundary=boundary(
                    contained_sha,
                    superseded_by=selected_review.id,
                    recorded_at="2026-08-07T14:54:00+00:00",
                ),
            )
            wrong_edge = _task(
                "root:review-wrong-edge",
                action_name="feature/review",
                pipeline_root_id=root.id,
                boundary=boundary(
                    contained_sha,
                    superseded_by="root:review-different",
                    recorded_at="2026-08-07T14:53:00+00:00",
                ),
            )
            sibling = _task(
                "root:review-sibling",
                action_name="feature/review",
                pipeline_root_id=root.id,
                boundary=boundary(
                    contained_sha,
                    branch="topic-sibling",
                    superseded_by=selected_review.id,
                    recorded_at="2026-08-07T14:52:00+00:00",
                ),
            )
            reroute = _task(
                "reroute-root:review-victim",
                action_name="feature/review",
                pipeline_root_id="reroute-root",
                boundary=boundary(
                    contained_sha,
                    superseded_by=selected_review.id,
                    recorded_at="2026-08-07T14:51:00+00:00",
                ),
            )
            uncontained_near = _task(
                "root:review-uncontained",
                action_name="feature/review",
                pipeline_root_id=root.id,
                boundary=boundary(
                    uncontained_sha,
                    superseded_by=selected_review.id,
                    recorded_at="2026-08-07T14:50:00+00:00",
                ),
            )
            contained_far = _task(
                "root:review-contained-far",
                action_name="feature/review",
                pipeline_root_id=root.id,
                boundary=boundary(
                    contained_sha,
                    superseded_by=uncontained_near.id,
                    recorded_at="2026-08-07T14:49:00+00:00",
                ),
            )
            tasks = [
                root, implementation, selected_review, valid,
                other_root_exact_edge, wrong_edge, sibling, reroute,
                uncontained_near, contained_far,
            ]
            target_ids = [implementation.id, selected_review.id]

            candidates = review_cycle_containment_candidates(
                tasks,
                repo_root=str(repo),
                branch="topic",
                task_ids=target_ids,
            )
            candidate_ids = {task.id for task in candidates}
            self.assertEqual(
                candidate_ids,
                {valid.id, uncontained_near.id, contained_far.id},
            )
            self.assertNotIn(other_root_exact_edge.id, candidate_ids)
            self.assertNotIn(wrong_edge.id, candidate_ids)
            self.assertNotIn(sibling.id, candidate_ids)
            self.assertNotIn(reroute.id, candidate_ids)

            verified = asyncio.run(
                verified_review_cycle_containment_task_ids(
                    tasks,
                    repo_root=str(repo),
                    branch="topic",
                    merge_sha=merge_sha,
                    task_ids=target_ids,
                )
            )
            self.assertEqual(verified, (valid.id,))
            self.assertNotIn(uncontained_near.id, verified)
            # This commit is contained, but its immediate successor failed
            # containment.  No transitive trust may skip that failed hop.
            self.assertNotIn(contained_far.id, verified)

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
