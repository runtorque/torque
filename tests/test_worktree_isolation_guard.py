"""Regression coverage for the worktree-isolation guard (TORQUE:580).

These tests pin down the fail-closed protections that stop a worker's git
changes from contaminating the shared main checkout:

  * the managed ``pre-commit`` hook that refuses a commit into the main
    checkout from inside a Torque worker session (``TORQUE_CELL_ID`` set),
    while leaving human commits and isolated-worktree commits alone; and
  * the daemon-side directory assertion that refuses to run a mutating git
    op when a cell's ``worktree_path`` has collapsed onto the repo root.
"""

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from torque.worktree import (
    WorktreeManager,
    ensure_worktree_isolation_guard,
    worktree_dir_is_shared_checkout,
    worktree_isolation_guard_installed,
    _ISOLATION_GUARD_HOOK_MARKER,
)


class WorktreeIsolationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.mgr = WorktreeManager()

        await self._git("init", "-b", "main")
        await self._git("config", "user.name", "Torque Test")
        await self._git("config", "user.email", "torque@example.com")
        (self.repo_root / "README.md").write_text("line one\n")
        await self._git("add", "README.md")
        await self._git("commit", "-m", "Initial commit")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _git(self, *args, cwd=None):
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(cwd or self.repo_root), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    def _commit(self, cwd, message, *, worker: bool):
        """Run ``git commit`` in *cwd*, optionally as a worker session.

        Returns ``(returncode, combined_output)``.
        """
        env = dict(os.environ)
        env.pop("TORQUE_CELL_ID", None)
        if worker:
            env["TORQUE_CELL_ID"] = "test-cell"
        proc = subprocess.run(
            ["git", "-C", str(cwd), "commit", "-m", message],
            capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def _hook_path(self) -> Path:
        return self.repo_root / ".git" / "hooks" / "pre-commit"

    def _make_cell(self, **overrides):
        cell = SimpleNamespace(
            id="agent-123",
            name="Worker",
            slug="",
            kind="worker",
            owner_engineer_id="",
            worktree_path="",
            worktree_branch="",
            worktree_repo_root="",
            worktree_base_branch="",
            worktree_dirty=False,
            worktree_diff={},
            worktree_checkpoints=0,
        )
        for key, value in overrides.items():
            setattr(cell, key, value)
        return cell

    # ---- hook installation -------------------------------------------------

    def test_install_is_idempotent_and_marks_the_hook(self):
        self.assertFalse(worktree_isolation_guard_installed(str(self.repo_root)))

        self.assertEqual(
            ensure_worktree_isolation_guard(str(self.repo_root)), "installed"
        )
        hook = self._hook_path()
        self.assertTrue(hook.exists())
        self.assertIn(_ISOLATION_GUARD_HOOK_MARKER, hook.read_text())
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertTrue(worktree_isolation_guard_installed(str(self.repo_root)))

        # Second call is a no-op.
        self.assertEqual(
            ensure_worktree_isolation_guard(str(self.repo_root)), "present"
        )

    def test_stale_managed_hook_is_refreshed(self):
        hook = self._hook_path()
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            f"#!/bin/sh\n# {_ISOLATION_GUARD_HOOK_MARKER} v0 (old)\nexit 0\n"
        )
        self.assertEqual(
            ensure_worktree_isolation_guard(str(self.repo_root)), "refreshed"
        )
        self.assertIn(
            f"{_ISOLATION_GUARD_HOOK_MARKER} v", hook.read_text()
        )
        self.assertIn("BLOCKED commit", hook.read_text())

    def test_foreign_hook_is_not_clobbered(self):
        hook = self._hook_path()
        hook.parent.mkdir(parents=True, exist_ok=True)
        foreign = "#!/bin/sh\n# my own hook\necho hi\nexit 0\n"
        hook.write_text(foreign)

        self.assertEqual(
            ensure_worktree_isolation_guard(str(self.repo_root)), "foreign"
        )
        self.assertEqual(hook.read_text(), foreign)
        self.assertFalse(worktree_isolation_guard_installed(str(self.repo_root)))

    # ---- hook behaviour (end to end) ---------------------------------------

    async def test_hook_blocks_worker_commit_into_main_checkout(self):
        ensure_worktree_isolation_guard(str(self.repo_root))

        # A worker editing files in the shared main checkout and committing
        # there is exactly the TORQUE:580 breach — it must be refused.
        (self.repo_root / "README.md").write_text("worker contamination\n")
        await self._git("add", "README.md")
        rc, out = self._commit(self.repo_root, "worker commit on main", worker=True)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("shared main checkout", out)
        # Nothing committed: HEAD subject is still the initial commit.
        head_subject = await self._git("show", "-s", "--format=%s", "HEAD")
        self.assertEqual(head_subject, "Initial commit")

    async def test_hook_allows_human_commit_into_main_checkout(self):
        ensure_worktree_isolation_guard(str(self.repo_root))

        (self.repo_root / "README.md").write_text("human edit\n")
        await self._git("add", "README.md")
        rc, out = self._commit(self.repo_root, "human commit on main", worker=False)
        self.assertEqual(rc, 0, out)
        head_subject = await self._git("show", "-s", "--format=%s", "HEAD")
        self.assertEqual(head_subject, "human commit on main")

    async def test_hook_allows_worker_commit_inside_isolated_worktree(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(
            cell, str(self.repo_root), base_branch="main"
        )
        self.assertTrue(wt_path)
        # create() installs the guard for the repo.
        self.assertTrue(worktree_isolation_guard_installed(str(self.repo_root)))

        (Path(wt_path) / "feature.txt").write_text("isolated work\n")
        await self._git("add", "feature.txt", cwd=wt_path)
        rc, out = self._commit(wt_path, "worker commit in worktree", worker=True)
        self.assertEqual(rc, 0, out)
        subject = await self._git(
            "show", "-s", "--format=%s", "HEAD", cwd=wt_path
        )
        self.assertEqual(subject, "worker commit in worktree")
        # The main checkout HEAD is untouched by the worktree commit.
        main_subject = await self._git("show", "-s", "--format=%s", "HEAD")
        self.assertEqual(main_subject, "Initial commit")

    # ---- daemon-side directory assertion -----------------------------------

    def test_worktree_dir_is_shared_checkout_detection(self):
        same = self._make_cell(
            worktree_path=str(self.repo_root),
            worktree_repo_root=str(self.repo_root),
        )
        self.assertTrue(worktree_dir_is_shared_checkout(same))

        isolated = self._make_cell(
            worktree_path=str(self.repo_root / ".torque" / "worktrees" / "x"),
            worktree_repo_root=str(self.repo_root),
        )
        self.assertFalse(worktree_dir_is_shared_checkout(isolated))

    async def test_checkpoint_refuses_when_worktree_path_is_repo_root(self):
        # Simulate a corrupted cell whose worktree_path collapsed onto the
        # shared repo root. The daemon-side guard must refuse rather than
        # commit the dirty main checkout.
        (self.repo_root / "README.md").write_text("would-be contamination\n")
        cell = self._make_cell(
            worktree_path=str(self.repo_root),
            worktree_repo_root=str(self.repo_root),
            worktree_branch="main",
            worktree_base_branch="main",
        )
        sha = await self.mgr.checkpoint(cell, message="should be refused")
        self.assertIsNone(sha)
        # No checkpoint commit landed on the shared checkout.
        head_subject = await self._git("show", "-s", "--format=%s", "HEAD")
        self.assertEqual(head_subject, "Initial commit")


if __name__ == "__main__":
    unittest.main()
