import unittest
from types import SimpleNamespace

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
        state.board_add_task(
            "Boundary",
            "g",
            id="task-boundary",
            lane="Done",
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
            data={},
            latest_boundary_state_for_cell=latest_boundary,
            boundary_reason_message=lambda reason, boundary=None: reason,
        )

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
