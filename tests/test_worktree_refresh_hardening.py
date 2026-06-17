import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from torque.worktree import WorktreeManager
from torque.metrics import MetricsCollector


def _cell(cell_id: str, path: str):
    return SimpleNamespace(
        id=cell_id,
        name=f"Cell {cell_id}",
        worktree_path=path,
        worktree_base_branch="main",
        worktree_repo_root=path,
        worktree_branch=f"branch-{cell_id}",
        worktree_merged=False,
        worktree_diff={"files": 9, "insertions": 8, "deletions": 7},
        worktree_changed_files=["old.txt"],
        worktree_dirty=True,
        worktree_checkpoints=7,
        worktree_ahead=7,
        worktree_behind=2,
    )


class _HangingProc:
    def __init__(self):
        self.returncode = None
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(60)
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class WorktreeRefreshHardeningTests(IsolatedAsyncioTestCase):
    async def test_refresh_timeout_preserves_prior_state_and_records_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorktreeManager(refresh_git_timeout_seconds=0.01)
            cell = _cell("a", tmp)
            proc = _HangingProc()

            with mock.patch.object(
                mgr,
                "_refresh_fingerprint",
                return_value=(123.0, 456.0),
            ), mock.patch(
                "torque.worktree.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(return_value=proc),
            ):
                changed = await mgr.refresh_state(cell)

            self.assertFalse(changed)
            self.assertTrue(proc.killed)
            self.assertEqual(cell.worktree_diff["files"], 9)
            self.assertEqual(cell.worktree_changed_files, ["old.txt"])
            self.assertTrue(cell.worktree_dirty)
            self.assertEqual(cell.worktree_ahead, 7)
            self.assertEqual(cell.worktree_behind, 2)
            self.assertFalse(cell.worktree_merged)
            metrics = mgr.refresh_metrics_snapshot()
            self.assertEqual(metrics["attempts"], 1)
            self.assertEqual(metrics["successes"], 0)
            self.assertEqual(metrics["failures"], 1)
            self.assertEqual(metrics["timeouts"], 1)
            self.assertEqual(metrics["last_error_kind"], "timeout")
            self.assertIn("rev-list", metrics["last_error_command"])

    async def test_missing_worktree_preserves_state_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "gone")
            mgr = WorktreeManager(refresh_git_timeout_seconds=0.01)
            cell = _cell("missing", missing)

            changed = await mgr.refresh_state(cell)

            self.assertFalse(changed)
            self.assertEqual(cell.worktree_diff["files"], 9)
            self.assertEqual(cell.worktree_ahead, 7)
            metrics = mgr.refresh_metrics_snapshot()
            self.assertEqual(metrics["failures"], 1)
            self.assertEqual(metrics["missing_worktrees"], 1)
            self.assertEqual(metrics["last_error_kind"], "missing_worktree")

    async def test_concurrent_refreshes_for_same_cell_are_coalesced(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorktreeManager(refresh_git_timeout_seconds=0.01)
            cell = _cell("same", tmp)
            calls = 0

            async def slow_inner(_cell, _fingerprint, worktree_submodules=None):
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.03)
                return True

            with mock.patch.object(
                mgr,
                "_refresh_fingerprint",
                return_value=(1.0, 2.0),
            ), mock.patch.object(mgr, "_refresh_state_inner", side_effect=slow_inner):
                first, second = await asyncio.gather(
                    mgr.refresh_state(cell),
                    mgr.refresh_state(cell),
                )

            self.assertTrue(first)
            self.assertTrue(second)
            self.assertEqual(calls, 1)
            self.assertEqual(mgr.refresh_metrics_snapshot()["coalesced"], 1)

    async def test_refresh_concurrency_limit_bounds_parallel_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorktreeManager(
                refresh_git_timeout_seconds=0.01,
                refresh_max_concurrent=1,
            )
            cells = [_cell("one", tmp), _cell("two", tmp)]
            active = 0
            peak = 0
            calls = 0

            async def slow_inner(_cell, _fingerprint, worktree_submodules=None):
                nonlocal active, peak, calls
                calls += 1
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1
                return False

            def fingerprint(cell, _submodules=None):
                return (1.0, 1.0 if cell.id == "one" else 2.0)

            with mock.patch.object(
                mgr,
                "_refresh_fingerprint",
                side_effect=fingerprint,
            ), mock.patch.object(mgr, "_refresh_state_inner", side_effect=slow_inner):
                await asyncio.gather(*(mgr.refresh_state(cell) for cell in cells))

            self.assertEqual(calls, 2)
            self.assertEqual(peak, 1)
            self.assertEqual(mgr.refresh_metrics_snapshot()["max_concurrent"], 1)

    async def test_nested_submodule_drift_probe_timeout_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            mgr = WorktreeManager(refresh_git_timeout_seconds=0.01)
            cell = _cell("nested-drift", tmp)

            async def refresh_git(_directory, *args, **_kwargs):
                if args[:3] == ("rev-list", "--left-right", "--count"):
                    return 0, "0 1", ""
                if args[:2] == ("status", "--porcelain=v2"):
                    return 0, "1 .M N... 160000 160000 160000 abc abc sub", ""
                return 0, "", ""

            async def hanging_git_run(*_args, **_kwargs):
                await asyncio.sleep(60)
                return 0, "", ""

            with mock.patch.object(
                mgr,
                "_refresh_fingerprint",
                return_value=(123.0, 456.0),
            ), mock.patch.object(
                mgr,
                "_refresh_git",
                side_effect=refresh_git,
            ), mock.patch.object(mgr, "_git_run", side_effect=hanging_git_run):
                changed = await asyncio.wait_for(
                    mgr.refresh_state(cell, worktree_submodules=["sub"]),
                    timeout=0.5,
                )

            self.assertFalse(changed)
            self.assertEqual(cell.worktree_diff["files"], 9)
            self.assertEqual(cell.worktree_changed_files, ["old.txt"])
            self.assertEqual(cell.worktree_ahead, 7)
            self.assertEqual(cell.worktree_behind, 2)
            metrics = mgr.refresh_metrics_snapshot()
            self.assertEqual(metrics["failures"], 1)
            self.assertEqual(metrics["timeouts"], 1)
            self.assertEqual(metrics["last_error_kind"], "timeout")
            self.assertEqual(
                metrics["last_error_command"],
                "nested_submodule_gitlink_drift",
            )

    async def test_nested_submodule_discovery_timeout_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorktreeManager(refresh_git_timeout_seconds=0.01)
            cell = _cell("nested-discovery", tmp)

            async def refresh_git(_directory, *args, **_kwargs):
                if args[:3] == ("rev-list", "--left-right", "--count"):
                    return 0, "0 1", ""
                return 0, "", ""

            async def hanging_infos(*_args, **_kwargs):
                await asyncio.sleep(60)
                return []

            with mock.patch.object(
                mgr,
                "_refresh_fingerprint",
                return_value=(123.0, 456.0),
            ), mock.patch.object(
                mgr,
                "_refresh_git",
                side_effect=refresh_git,
            ), mock.patch.object(
                mgr,
                "_nested_submodule_infos",
                side_effect=hanging_infos,
            ):
                changed = await asyncio.wait_for(
                    mgr.refresh_state(cell, worktree_submodules=["sub"]),
                    timeout=0.5,
                )

            self.assertFalse(changed)
            self.assertEqual(cell.worktree_ahead, 7)
            self.assertEqual(cell.worktree_behind, 2)
            metrics = mgr.refresh_metrics_snapshot()
            self.assertEqual(metrics["failures"], 1)
            self.assertEqual(metrics["timeouts"], 1)
            self.assertEqual(
                metrics["last_error_command"],
                "nested_submodule_infos:ahead_behind",
            )


    def test_metrics_tick_surfaces_worktree_refresh_snapshot(self):
        collector = MetricsCollector(enabled=True)
        tick = collector.aggregate_tick(
            live={
                "agents": 1,
                "ptys": 0,
                "prompt_queue_depth": 0,
                "worktree_refresh": {"timeouts": 2, "failures": 3},
            },
            now=1_000.0,
            interval_seconds=1.0,
        )

        self.assertEqual(
            tick["perf"]["live"]["worktree_refresh"],
            {"timeouts": 2, "failures": 3},
        )
