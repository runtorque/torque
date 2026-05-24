import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from torque.worktree import WorktreeManager


class NestedWorktreeSubmoduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sub_origin = self.root / "sub-origin.git"
        self.sub_seed = self.root / "sub-seed"
        self.repo_root = self.root / "super"
        self.sub_path = "deps/sub"
        self.mgr = WorktreeManager()

        await self._git("init", "--bare", str(self.sub_origin), cwd=self.root)

        self.sub_seed.mkdir()
        await self._git("init", "-b", "main", cwd=self.sub_seed)
        await self._configure_user(self.sub_seed)
        (self.sub_seed / "lib.txt").write_text("sub line one\n")
        await self._git("add", "lib.txt", cwd=self.sub_seed)
        await self._git("commit", "-m", "Initial submodule", cwd=self.sub_seed)
        await self._git("remote", "add", "origin", str(self.sub_origin),
                        cwd=self.sub_seed)
        await self._git("push", "-u", "origin", "main", cwd=self.sub_seed)

        self.repo_root.mkdir()
        await self._git("init", "-b", "main", cwd=self.repo_root)
        await self._configure_user(self.repo_root)
        (self.repo_root / "README.md").write_text("super line one\n")
        await self._git("add", "README.md", cwd=self.repo_root)
        await self._git("commit", "-m", "Initial super", cwd=self.repo_root)
        await self._git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(self.sub_origin),
            self.sub_path,
            cwd=self.repo_root,
        )
        await self._configure_user(self.repo_root / self.sub_path)
        await self._git("commit", "-m", "Add submodule", cwd=self.repo_root)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _configure_user(self, cwd: Path):
        await self._git("config", "user.name", "Torque Test", cwd=cwd)
        await self._git("config", "user.email", "torque@example.com", cwd=cwd)

    async def _git(self, *args, cwd=None, check=True):
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd or self.repo_root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode().strip()
        err = stderr.decode().strip()
        if check and proc.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {err}")
        return proc.returncode, out, err

    async def _git_out(self, *args, cwd=None):
        _code, out, _err = await self._git(*args, cwd=cwd)
        return out

    def _make_cell(self, agent_id="agent-123", name="Worker"):
        return SimpleNamespace(
            id=agent_id,
            name=name,
            slug="",
            kind="worker",
            owner_engineer_id="",
            worktree_path="",
            worktree_branch="",
            worktree_repo_root="",
            worktree_base_branch="",
            worktree_dirty=False,
            worktree_diff={},
            worktree_changed_files=[],
            worktree_checkpoints=0,
            worktree_ahead=0,
            worktree_behind=0,
            worktree_merged=False,
        )

    def _module_dir(self) -> Path:
        return self.repo_root / ".git" / "modules" / "deps" / "sub"

    async def _create_nested(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            worktree_submodules=[self.sub_path],
        )
        self.assertIsNotNone(wt_path)
        return cell, Path(wt_path)

    async def _show_ref_exists(self, repo: Path, branch: str) -> bool:
        code, _out, _err = await self._git(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            cwd=repo,
            check=False,
        )
        return code == 0

    async def test_create_adds_nested_linked_submodule_worktree(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path

        self.assertTrue(wt.is_dir())
        self.assertTrue(sub_wt.is_dir())
        self.assertTrue((sub_wt / ".git").is_file())

        super_branch = await self._git_out("branch", "--show-current", cwd=wt)
        sub_branch = await self._git_out("branch", "--show-current", cwd=sub_wt)
        self.assertEqual(super_branch, cell.worktree_branch)
        self.assertTrue(
            sub_branch.startswith(
                "torque/submodules/deps-sub/" + cell.worktree_branch
            ),
            sub_branch,
        )

        common_dir = Path(
            await self._git_out(
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                cwd=sub_wt,
            )
        )
        self.assertEqual(common_dir.resolve(), self._module_dir().resolve())

        module_worktrees = await self._git_out(
            "worktree",
            "list",
            "--porcelain",
            cwd=self._module_dir(),
        )
        self.assertIn(str(sub_wt), module_worktrees)

    async def test_checkpoint_commits_submodule_first_and_bumps_gitlink(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path

        (sub_wt / "lib.txt").write_text("sub line one\nsub line two\n")

        sha = await self.mgr.checkpoint(
            cell,
            message="Nested checkpoint",
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(sha)
        sub_head = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)
        gitlink_line = await self._git_out(
            "ls-tree",
            "HEAD",
            self.sub_path,
            cwd=wt,
        )
        self.assertIn(sub_head, gitlink_line)
        self.assertEqual(
            await self._git_out("show", "-s", "--format=%s", "HEAD", cwd=sub_wt),
            "Nested checkpoint",
        )
        self.assertEqual(
            await self._git_out("show", "-s", "--format=%s", "HEAD", cwd=wt),
            "Nested checkpoint",
        )

    async def test_refresh_diff_counts_submodule_file_level_numstat(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nsub line two\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Nested checkpoint",
                worktree_submodules=[self.sub_path],
            )
        )

        changed = await self.mgr.refresh_state(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(changed)
        self.assertEqual(
            cell.worktree_diff,
            {"files": 1, "insertions": 1, "deletions": 0},
        )
        self.assertEqual(cell.worktree_changed_files, ["deps/sub/lib.txt"])

    async def test_remove_preserves_unpushed_submodule_commit_branch_ref(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nunpushed line\n")
        await self._git("add", "lib.txt", cwd=sub_wt)
        await self._git("commit", "-m", "Unpushed submodule work", cwd=sub_wt)
        unpushed_sha = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)
        sub_branch = await self._git_out("branch", "--show-current", cwd=sub_wt)

        result = await self.mgr.remove_result(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(result["worktree_removed"], result)
        self.assertFalse(sub_wt.exists())
        self.assertTrue(await self._show_ref_exists(self._module_dir(), sub_branch))
        self.assertEqual(
            0,
            (
                await self._git(
                    "cat-file",
                    "-e",
                    f"{unpushed_sha}^{{commit}}",
                    cwd=self._module_dir(),
                    check=False,
                )
            )[0],
        )
        nested = result.get("nested_submodules", [])
        self.assertEqual(len(nested), 1)
        self.assertTrue(nested[0]["worktree_removed"], nested)
        self.assertTrue(nested[0]["branch_preserved"], nested)
        module_worktrees = await self._git_out(
            "worktree",
            "list",
            "--porcelain",
            cwd=self._module_dir(),
        )
        self.assertNotIn(str(sub_wt), module_worktrees)

    async def test_empty_worktree_submodules_keeps_nested_machinery_dormant(self):
        cell = self._make_cell()
        with mock.patch.object(
            self.mgr,
            "_create_nested_submodule_worktrees",
            side_effect=AssertionError("nested create should be dormant"),
        ):
            wt_path = await self.mgr.create(
                cell,
                str(self.repo_root),
                base_branch="main",
                worktree_submodules=[],
            )
        self.assertIsNotNone(wt_path)
        wt = Path(wt_path)
        self.assertFalse((wt / self.sub_path / ".git").exists())

        (wt / "README.md").write_text("super line one\nsuper line two\n")
        with mock.patch.object(
            self.mgr,
            "_checkpoint_nested_submodules",
            side_effect=AssertionError("nested checkpoint should be dormant"),
        ):
            self.assertTrue(await self.mgr.checkpoint(cell, message="Super only"))

        self.mgr.forget_refresh_state(cell.id)
        with mock.patch.object(
            self.mgr,
            "_nested_submodule_numstat",
            side_effect=AssertionError("nested diff should be dormant"),
        ):
            self.assertTrue(await self.mgr.refresh_state(cell))

        with mock.patch.object(
            self.mgr,
            "_remove_nested_submodule_worktrees",
            side_effect=AssertionError("nested remove should be dormant"),
        ):
            self.assertTrue(await self.mgr.remove(cell, worktree_submodules=[]))
