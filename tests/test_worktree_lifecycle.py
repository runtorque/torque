import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from loom.worktree import WorktreeManager


class WorktreeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.mgr = WorktreeManager()

        await self._git("init", "-b", "main")
        await self._git("config", "user.name", "Loom Test")
        await self._git("config", "user.email", "loom@example.com")

        (self.repo_root / "README.md").write_text("line one\nline two\n")
        (self.repo_root / "shared").mkdir()
        (self.repo_root / "shared" / "config.txt").write_text("shared\n")
        (self.repo_root / "local-only").mkdir()
        (self.repo_root / "local-only" / "config.json").write_text("{}\n")
        await self._git("add", "README.md", "shared/config.txt")
        await self._git("commit", "-m", "Initial commit")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _git(self, *args, cwd=None):
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd or self.repo_root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed: {stderr.decode().strip()}"
            )
        return stdout.decode().strip()

    def _make_cell(self, agent_id="agent-123", name="Worker"):
        return SimpleNamespace(
            id=agent_id,
            name=name,
            worktree_path="",
            worktree_branch="",
            worktree_repo_root="",
            worktree_base_branch="",
            worktree_dirty=False,
            worktree_diff={},
            worktree_checkpoints=0,
        )

    def _worktree_gitdir(self, wt_path: str) -> Path:
        dot_git = Path(wt_path) / ".git"
        raw = dot_git.read_text().strip().removeprefix("gitdir: ").strip()
        gitdir = Path(raw)
        if not gitdir.is_absolute():
            gitdir = (Path(wt_path) / gitdir).resolve()
        return gitdir

    async def test_create_and_remove_worktree_updates_state_and_files(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            symlinks=["local-only/config.json", "../invalid", "missing.txt"],
        )

        self.assertIsNotNone(wt_path)
        self.assertTrue(Path(wt_path).is_dir())
        self.assertEqual(cell.worktree_path, wt_path)
        self.assertEqual(cell.worktree_repo_root, str(self.repo_root))
        self.assertEqual(cell.worktree_base_branch, "main")
        self.assertTrue(cell.worktree_branch.startswith("loom/worker-"))

        link = Path(wt_path) / "local-only" / "config.json"
        self.assertTrue(link.is_symlink())
        self.assertEqual(
            link.resolve(),
            (self.repo_root / "local-only" / "config.json").resolve(),
        )

        exclude = self._worktree_gitdir(wt_path) / "info" / "exclude"
        self.assertIn("local-only/config.json", exclude.read_text().splitlines())

        cell.worktree_dirty = True
        cell.worktree_diff = {"files": 1}
        cell.worktree_checkpoints = 2

        removed = await self.mgr.remove(cell)

        self.assertTrue(removed)
        self.assertFalse(Path(wt_path).exists())
        self.assertEqual(cell.worktree_path, "")
        self.assertEqual(cell.worktree_branch, "")
        self.assertEqual(cell.worktree_repo_root, "")
        self.assertEqual(cell.worktree_base_branch, "")
        self.assertFalse(cell.worktree_dirty)
        self.assertEqual(cell.worktree_diff, {})
        self.assertEqual(cell.worktree_checkpoints, 0)

    async def test_checkpoint_history_and_rollback_cover_worktree_progress(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        readme = Path(wt_path) / "README.md"
        readme.write_text("line one\ncheckpoint one\n")
        first_sha = await self.mgr.checkpoint(cell, message="Implement step 1")
        self.assertTrue(first_sha)

        readme.write_text("line one\ncheckpoint two\n")
        second_sha = await self.mgr.checkpoint(cell)
        self.assertTrue(second_sha)

        commits = await self.mgr.list_checkpoints(cell)

        self.assertEqual([c["sha"] for c in commits], [second_sha, first_sha])
        self.assertEqual(commits[1]["message"], "Implement step 1")
        self.assertEqual(
            commits[0]["message"],
            f"loom: checkpoint 2 — {cell.name}",
        )

        rolled_back = await self.mgr.rollback(cell, first_sha)

        self.assertTrue(rolled_back)
        self.assertEqual(readme.read_text(), "line one\ncheckpoint one\n")

    async def test_changed_files_includes_dirty_and_untracked_work(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        readme = Path(wt_path) / "README.md"
        readme.write_text("line one\nlocal dirty edit\n")
        new_file = Path(wt_path) / "src" / "dirty_only.py"
        new_file.parent.mkdir()
        new_file.write_text("print('dirty')\n")

        changed = await self.mgr.changed_files(cell)

        self.assertIn("README.md", changed)
        self.assertIn("src/dirty_only.py", changed)

    async def test_server_merge_and_reset_to_base_keep_future_work_clean(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        readme = Path(wt_path) / "README.md"
        readme.write_text("line one\nmerged from worktree\n")
        commit_sha = await self.mgr.checkpoint(cell, message="Finish worker change")
        self.assertTrue(commit_sha)

        merge_result = await self.mgr.server_merge(cell, "Merge worker change")

        self.assertTrue(merge_result["ok"], merge_result.get("error"))
        self.assertEqual(
            (self.repo_root / "README.md").read_text(),
            "line one\nmerged from worktree\n",
        )
        self.assertTrue(await self.mgr.is_merged(cell))

        reset = await self.mgr.reset_to_base(cell)

        self.assertTrue(reset)
        self.assertEqual(await self.mgr.count_commits(cell), 0)
        self.assertEqual(
            await self._git("rev-parse", "HEAD", cwd=wt_path),
            await self._git("rev-parse", "main"),
        )

    async def test_conflict_detection_and_rebase_abort_leave_worktree_clean(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        readme = Path(wt_path) / "README.md"
        readme.write_text("line one\nworker version\n")
        worker_sha = await self.mgr.checkpoint(cell, message="Worker change")
        self.assertTrue(worker_sha)

        (self.repo_root / "README.md").write_text("line one\nmain version\n")
        await self._git("add", "README.md")
        await self._git("commit", "-m", "Mainline change")

        conflict_info = await self.mgr.check_merge_conflicts(cell)

        self.assertFalse(conflict_info["clean"])
        self.assertTrue(conflict_info["conflicts"])

        rebased = await self.mgr.rebase_onto_base(cell)

        self.assertFalse(rebased)
        self.assertFalse(await self.mgr.has_uncommitted_changes(cell))
        self.assertEqual(
            await self._git("status", "--porcelain", cwd=wt_path),
            "",
        )
