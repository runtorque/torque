from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class DriverlessWorktreeGateParityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()

    async def test_driverless_and_live_targets_produce_identical_gate_decisions(self):
        from torque import server
        from torque.state import MatrixState

        state = MatrixState()
        state.groups["g"] = []
        boundary_task = state.board_add_task(
            "Boundary",
            "g",
            id="task-boundary",
            lane="Done",
            agent_id="agent-1",
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "commit_sha": "head",
                "status": "open",
                "recorded_at": "2026-05-26T00:00:00+00:00",
            },
        )
        live = SimpleNamespace(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            worktree_path="/wt",
            worktree_branch="torque/worker",
            worktree_repo_root="/repo",
            git_root="/repo",
            worktree_base_branch="main",
            worktree_merge_squash=True,
            current_task_id="",
        )
        driverless = server.WorktreeCommandTarget(
            id="driverless:torque/worker",
            name="driverless:torque/worker",
            group="g",
            worktree_path="/wt",
            worktree_branch="torque/worker",
            worktree_repo_root="/repo",
            git_root="/repo",
            worktree_base_branch="main",
            driverless=True,
        )

        class FakeWorktreeManager:
            async def has_uncommitted_changes(self, cell, worktree_submodules=None):
                return False

            async def stale_base_info(self, cell, worktree_submodules=None):
                return {"stale": False}

            async def check_merge_conflicts(self, cell, worktree_submodules=None):
                return {"clean": True, "tree_sha": "tree", "conflicts": []}

            async def merge_untracked_overwrite_paths(self, repo_root, base_branch, tree_sha):
                return []

        async def latest_boundary(cell):
            return {
                "latest": {"boundary": {"commit_sha": "head"}},
                "clean": {"boundary": {"commit_sha": "head"}},
                "reason": "",
            }

        kwargs = dict(
            state=state,
            worktree_mgr=FakeWorktreeManager(),
            data={"merge_task_id": boundary_task.id},
            latest_boundary_state_for_cell=latest_boundary,
            boundary_reason_message=lambda reason, boundary=None: reason,
        )

        backend_modularity_result = {
            "ok": True,
            "applicable": False,
            "phase": "backend_modularity",
            "checked_files": [],
            "crossings": [],
        }
        with patch(
            "torque.services.worktrees.preflight.check_backend_modularity_crossings",
            return_value=backend_modularity_result,
        ):
            live_result = await server._preflight_worktree_merge_gates(
                cell=live,
                aid=live.id,
                **kwargs,
            )
            driverless_result = await server._preflight_worktree_merge_gates(
                cell=driverless,
                aid=driverless.id,
                **kwargs,
            )

        for result in (live_result, driverless_result):
            result.pop("boundary_state", None)
        self.assertEqual(live_result, driverless_result)


class BackendModularityMergeGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        marker = self.repo / "tests" / "test_backend_modularity.py"
        marker.parent.mkdir()
        marker.write_text("# marker\n")
        backend = self.repo / "torque" / "sample.py"
        backend.parent.mkdir()
        backend.write_text("x = 1\n" * 2500)
        (self.repo / "torque" / "backend_invariants.py").write_text(
            "DEFAULT_BACKEND_LINE_LIMIT = 2500\n"
            "BACKEND_LINE_LIMITS = {}\n"
        )
        self._git("add", ".")
        self._git("commit", "-qm", "baseline")
        self._git("switch", "-qc", "worker")
        backend.write_text("x = 1\n" * 2501)
        self._git("add", "torque/sample.py")
        self._git("commit", "-qm", "cross limit")
        self._git("switch", "-qc", "safe", "main")
        backend.write_text("x = 1\n" * 2499)
        self._git("add", "torque/sample.py")
        self._git("commit", "-qm", "stay below limit")
        self._git("switch", "-qc", "marker-deleted", "main")
        marker.unlink()
        backend.write_text("x = 1\n" * 2501)
        self._git("add", "-A")
        self._git("commit", "-qm", "delete marker while crossing limit")

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    async def _preflight(self, branch: str, *, repo_root: str | None = None):
        from torque import server
        from torque.state import MatrixState

        state = MatrixState()
        state.groups["g"] = []
        cell = SimpleNamespace(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            worktree_path=str(self.repo),
            worktree_branch=branch,
            worktree_repo_root=(
                str(self.repo) if repo_root is None else repo_root
            ),
            git_root=str(self.repo),
            worktree_base_branch="main",
            worktree_merge_squash=True,
            current_task_id="",
        )
        merge_task = state.board_add_task(
            "Merge backend change",
            "g",
            id="task-merge",
            lane="In Progress",
            agent_id=cell.id,
        )

        class FakeWorktreeManager:
            def __init__(self):
                self.merge_check_called = False

            async def has_uncommitted_changes(self, _cell, worktree_submodules=None):
                return False

            async def stale_base_info(self, _cell, worktree_submodules=None):
                return {"stale": False}

            async def check_merge_conflicts(self, _cell, worktree_submodules=None):
                self.merge_check_called = True
                return {"clean": True, "tree_sha": "tree", "conflicts": []}

            async def merge_untracked_overwrite_paths(self, *_args):
                return []

        mgr = FakeWorktreeManager()

        async def latest_boundary(_cell):
            return {"latest": None, "clean": True, "reason": ""}

        result = await server._preflight_worktree_merge_gates(
            state=state,
            cell=cell,
            worktree_mgr=mgr,
            aid=cell.id,
            data={"merge_task_id": merge_task.id},
            latest_boundary_state_for_cell=latest_boundary,
            boundary_reason_message=lambda reason, boundary=None: reason,
        )
        return result, mgr

    async def test_shared_merge_preflight_blocks_invalid_repo_root_before_merge_check(self):
        invalid_root = str(self.repo / "missing-repository-root")
        result, mgr = await self._preflight("worker", repo_root=invalid_root)

        self.assertFalse(result["ok"])
        self.assertFalse(mgr.merge_check_called)
        self.assertEqual(result["result"]["phase"], "backend_modularity")
        self.assertEqual(result["backend_modularity"]["phase"], "backend_modularity")
        self.assertIn("repo_root", result["backend_modularity"]["error"])

    async def test_shared_merge_preflight_blocks_crossing_without_author_check(self):
        result, mgr = await self._preflight("worker")

        self.assertFalse(result["ok"])
        self.assertFalse(mgr.merge_check_called)
        self.assertEqual(result["result"]["phase"], "backend_modularity")
        self.assertEqual(
            result["backend_modularity"]["crossings"][0]["path"],
            "torque/sample.py",
        )

    async def test_shared_merge_preflight_blocks_marker_deletion_bypass(self):
        result, mgr = await self._preflight("marker-deleted")

        self.assertFalse(result["ok"])
        self.assertFalse(mgr.merge_check_called)
        self.assertEqual(result["result"]["phase"], "backend_modularity")
        self.assertTrue(result["backend_modularity"]["applicable"])
        self.assertEqual(
            result["backend_modularity"]["crossings"][0]["path"],
            "torque/sample.py",
        )

    async def test_shared_merge_preflight_allows_change_without_crossing(self):
        result, mgr = await self._preflight("safe")

        self.assertTrue(result["ok"], result.get("result"))
        self.assertTrue(mgr.merge_check_called)
        self.assertTrue(result["backend_modularity"]["ok"])
        self.assertEqual(result["backend_modularity"]["crossings"], [])


class BoundaryTipGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()

    def _cell(self):
        return SimpleNamespace(
            id="worker-1",
            name="Worker",
            group="g",
            slug="worker",
            worktree_path="/wt",
            worktree_branch="torque/worker",
            worktree_repo_root="/repo",
            git_root="/repo",
            worktree_base_branch="main",
            worktree_merge_squash=True,
            current_task_id="task-1",
            owner_engineer_id="eng-1",
            created_by_engineer_id="eng-1",
            kind="worker",
            cell_type="agent",
        )

    async def _preflight(self, *, boundary_state, data=None, mismatch=None,
                         completed_review=False, stale=False):
        from torque import server
        from torque.state import MatrixState

        state = MatrixState()
        state.groups["g"] = []
        cell = self._cell()
        state.agents[cell.id] = cell
        state.groups["g"].append(cell.id)
        task = state.board_add_task(
            "Implement worker change",
            "g",
            id="task-1",
            lane="In Progress",
            agent_id=cell.id,
            action_name="feature/implement",
        )
        task.assigned_engineer_id = "eng-1"
        if completed_review:
            review = state.board_add_task(
                "Review worker change",
                "g",
                id="review-task",
                lane="Done",
                agent_id="reviewer-1",
                action_name="feature/review",
                parent_task_id=task.id,
                pipeline_root_id=task.id,
            )
            review.lane = "Done"
            review.worktree_boundary = {
                "version": "1",
                "repo_root": "/repo",
                "branch": "torque/worker",
                "base_branch": "main",
                "commit_sha": "abcdef1234567890",
                "status": "open",
                "recorded_at": "2026-08-07T00:00:00+00:00",
            }

        class FakeWorktreeManager:
            def __init__(self):
                self.check_calls = 0

            async def has_uncommitted_changes(self, _cell, worktree_submodules=None):
                return False

            async def stale_base_info(self, _cell, worktree_submodules=None):
                return {"stale": stale}

            async def check_merge_conflicts(self, _cell, worktree_submodules=None):
                self.check_calls += 1
                return {"clean": True, "tree_sha": "tree", "conflicts": []}

            async def merge_untracked_overwrite_paths(self, *_args):
                return []

            async def boundary_tip_mismatch_info(self, _cell, boundary_sha, tip_sha):
                info = dict(mismatch or {})
                info.setdefault("boundary_sha", boundary_sha)
                info.setdefault("tip_sha", tip_sha)
                return info

        mgr = FakeWorktreeManager()

        async def latest_boundary(_cell):
            return boundary_state

        panel_events = []

        def panel_event(*args, **kwargs):
            panel_events.append((args, kwargs))

        def boundary_reason_message(reason, boundary=None):
            if reason == "branch_tip_moved":
                return server._boundary_tip_mismatch_message(boundary)
            return reason or "Latest task boundary is not mergeable."

        backend_modularity_result = {
            "ok": True,
            "applicable": False,
            "phase": "backend_modularity",
            "checked_files": [],
            "crossings": [],
        }
        with patch(
            "torque.services.worktrees.preflight.check_backend_modularity_crossings",
            return_value=backend_modularity_result,
        ):
            result = await server._preflight_worktree_merge_gates(
                state=state,
                cell=cell,
                worktree_mgr=mgr,
                aid=cell.id,
                data=data or {"merge_task_id": task.id},
                latest_boundary_state_for_cell=latest_boundary,
                boundary_reason_message=boundary_reason_message,
                panel_event=panel_event,
            )
        return result, mgr, panel_events

    def _mismatched_boundary_state(self):
        return {
            "latest": {
                "task_id": "review-task",
                "task_title": "Reviewed boundary",
                "boundary": {"commit_sha": "abcdef1234567890"},
                "head_sha": "fedcba9876543210",
                "clean_mergeable": False,
            },
            "clean": None,
            "reason": "branch_tip_moved",
        }

    async def test_boundary_tip_ahead_refuses_with_ancestor_count(self):
        result, mgr, _events = await self._preflight(
            boundary_state=self._mismatched_boundary_state(),
            mismatch={"classification": "ahead", "commit_count": 2},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(mgr.check_calls, 0)
        error = result["result"]["error"]
        self.assertIn("Branch advanced 2 commit(s)", error)
        self.assertIn("last reviewed boundary abcdef123456", error)
        self.assertIn("re-review the new commits", error)
        self.assertNotIn("no file details", error.lower())
        self.assertNotIn("conflict", error.lower())

    async def test_composed_review_cycle_merge_refusal_names_safe_continuation(self):
        boundary_state = self._mismatched_boundary_state()
        result, mgr, _events = await self._preflight(
            boundary_state=boundary_state,
            mismatch={"classification": "ahead", "commit_count": 1},
            completed_review=True,
            stale=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(mgr.check_calls, 0)
        error = result["result"]["error"]
        expected = (
            "This branch is stale and has unreviewed commits past a completed "
            "open feature/review boundary. Do not record a reviewed boundary "
            "at the unreviewed tip. Use `review_cycle_continue` on the "
            "completed review, then non-force rebase, rerun the relevant "
            "evidence, and obtain a fresh feature/review."
        )
        self.assertEqual(error, expected)
        self.assertIn("review_cycle_continue", error)
        self.assertIn("non-force rebase", error)
        self.assertIn("rerun the relevant evidence", error)
        self.assertIn("fresh feature/review", error)
        self.assertIn("Do not record a reviewed boundary", error)
        self.assertNotIn("worktree_rebase", error)
        self.assertNotIn("re-review the new commits", error)
        self.assertNotIn("record a reviewed boundary at the tip", error)

    async def test_boundary_tip_rewrite_refuses_with_diverged_message(self):
        result, mgr, _events = await self._preflight(
            boundary_state=self._mismatched_boundary_state(),
            mismatch={"classification": "diverged"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(mgr.check_calls, 0)
        error = result["result"]["error"]
        self.assertIn("Branch diverged from the last recorded boundary", error)
        self.assertIn("history rewritten", error)
        self.assertIn("re-review required", error)

    async def test_stale_base_force_does_not_bypass_boundary_tip_gate(self):
        result, mgr, _events = await self._preflight(
            boundary_state=self._mismatched_boundary_state(),
            data={"force_stale_base": True},
            mismatch={"classification": "ahead", "commit_count": 3},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(mgr.check_calls, 0)
        self.assertIn("Branch advanced 3 commit(s)", result["result"]["error"])

    async def test_force_boundary_mismatch_allows_and_writes_audit_event(self):
        result, mgr, panel_events = await self._preflight(
            boundary_state=self._mismatched_boundary_state(),
            data={
                "force_boundary_mismatch": True,
                "actor_agent_id": "eng-1",
                "boundary_mismatch_reason": "verified with git log",
            },
            mismatch={"classification": "ahead", "commit_count": 1},
        )

        self.assertTrue(result["ok"], result.get("result"))
        self.assertEqual(mgr.check_calls, 1)
        audit = result.get("workflow_breach")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["subkind"], "boundary_mismatch_override")
        self.assertEqual(audit["actor_agent_id"], "eng-1")
        self.assertEqual(audit["reason"], "verified with git log")
        self.assertEqual(audit["boundary_sha"], "abcdef1234567890")
        self.assertEqual(audit["tip_sha"], "fedcba9876543210")
        self.assertIn("force_boundary_mismatch=true", audit["context"])
        self.assertTrue(panel_events)
        self.assertIn("boundary_mismatch_override", panel_events[0][0][4])

    async def test_matching_boundary_tip_passes_without_override(self):
        state = {
            "latest": {
                "boundary": {"commit_sha": "abcdef1234567890"},
                "head_sha": "abcdef1234567890",
                "clean_mergeable": True,
            },
            "clean": {"boundary": {"commit_sha": "abcdef1234567890"}},
            "reason": "",
        }
        result, mgr, _events = await self._preflight(boundary_state=state)

        self.assertTrue(result["ok"], result.get("result"))
        self.assertEqual(mgr.check_calls, 1)
        self.assertIsNone(result.get("workflow_breach"))
