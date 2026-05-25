import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.worktree import WorktreeManager


def install_iterm2_stub():
    iterm2 = types.ModuleType("iterm2")

    class Connection:
        pass

    class Modifier(Enum):
        COMMAND = "command"
        OPTION = "option"
        SHIFT = "shift"
        CONTROL = "control"
        FUNCTION = "function"

    class Keycode(Enum):
        UP_ARROW = "UP_ARROW"
        DOWN_ARROW = "DOWN_ARROW"
        LEFT_ARROW = "LEFT_ARROW"
        RIGHT_ARROW = "RIGHT_ARROW"
        HOME = "HOME"
        END = "END"
        PAGE_UP = "PAGE_UP"
        PAGE_DOWN = "PAGE_DOWN"
        FORWARD_DELETE = "FORWARD_DELETE"
        ANSI_A = "ANSI_A"
        ANSI_B = "ANSI_B"
        ANSI_C = "ANSI_C"
        ANSI_T = "ANSI_T"

    tool = types.SimpleNamespace(async_register_web_view_tool=None)
    binding = types.ModuleType("iterm2.binding")
    keyboard = types.ModuleType("iterm2.keyboard")
    keyboard.Modifier = Modifier
    keyboard.Keycode = Keycode
    iterm2.Connection = Connection
    iterm2.tool = tool
    iterm2.binding = binding
    iterm2.keyboard = keyboard
    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard
    return iterm2


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

    async def _module_core_worktree(self) -> str:
        return await self._git_out(
            "config",
            "--file",
            str(self._module_dir() / "config"),
            "--get",
            "core.worktree",
            cwd=self.root,
        )

    def _resolve_module_core_worktree(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._module_dir() / path
        return path.resolve()

    async def _assert_module_core_worktree_pinned(self):
        value = await self._module_core_worktree()
        self.assertEqual(
            self._resolve_module_core_worktree(value),
            (self.repo_root / self.sub_path).resolve(),
            value,
        )
        status_code, _status_out, status_err = await self._git(
            "status",
            "--short",
            cwd=self.repo_root / self.sub_path,
            check=False,
        )
        self.assertEqual(0, status_code, status_err)
        list_code, _list_out, list_err = await self._git(
            "worktree",
            "list",
            "--porcelain",
            cwd=self._module_dir(),
            check=False,
        )
        self.assertEqual(0, list_code, list_err)

    async def _hijack_module_core_worktree(self, target: Path):
        await self._git(
            "config",
            "--file",
            str(self._module_dir() / "config"),
            "core.worktree",
            str(Path(target).resolve()),
            cwd=self.root,
        )

    async def _gitlink_sha(self, repo: Path, ref: str = "HEAD") -> str:
        line = await self._git_out("ls-tree", ref, self.sub_path, cwd=repo)
        parts = line.split()
        self.assertGreaterEqual(len(parts), 3, line)
        return parts[2]

    async def _sub_branch(self, sub_wt: Path) -> str:
        return await self._git_out("branch", "--show-current", cwd=sub_wt)

    async def _push_sub_branch(self, sub_wt: Path) -> str:
        branch = await self._sub_branch(sub_wt)
        await self._git("push", "-u", "origin", branch, cwd=sub_wt)
        return branch

    async def _create_nested(self, agent_id="agent-123", name="Worker"):
        cell = self._make_cell(agent_id=agent_id, name=name)
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
        await self._assert_module_core_worktree_pinned()

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

    async def test_refresh_state_counts_submodule_ahead_dimension(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nsub ahead line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Nested ahead checkpoint",
                worktree_submodules=[self.sub_path],
            )
        )

        self.assertTrue(
            await self.mgr.refresh_state(
                cell,
                worktree_submodules=[self.sub_path],
            )
        )

        # One superproject gitlink commit plus one nested submodule commit.
        self.assertEqual(cell.worktree_ahead, 2)
        self.assertEqual(cell.worktree_checkpoints, 2)

    async def test_stale_base_info_detects_submodule_base_advance(self):
        cell, _wt = await self._create_nested()
        base_sub = self.repo_root / self.sub_path
        (base_sub / "base.txt").write_text("base submodule line\n")
        await self._git("add", "base.txt", cwd=base_sub)
        await self._git("commit", "-m", "Advance submodule base", cwd=base_sub)

        stale = await self.mgr.stale_base_info(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(stale["stale"], stale)
        self.assertEqual(stale["submodule"], self.sub_path)

    async def test_is_merged_requires_submodule_branch_dimension(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nunmerged sub line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Super gitlink only merge",
                worktree_submodules=[self.sub_path],
            )
        )

        merged = await self.mgr.server_merge(cell, "Merge super gitlink only")

        self.assertTrue(merged["ok"], merged.get("error"))
        self.assertTrue(await self.mgr.is_merged(cell))
        self.assertFalse(
            await self.mgr.is_merged(
                cell,
                worktree_submodules=[self.sub_path],
            )
        )

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

    async def test_remove_repairs_hijacked_submodule_module_core_worktree(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        await self._hijack_module_core_worktree(sub_wt)
        self.assertEqual(
            self._resolve_module_core_worktree(await self._module_core_worktree()),
            sub_wt.resolve(),
        )

        result = await self.mgr.remove_result(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(result["worktree_removed"], result)
        self.assertFalse(sub_wt.exists())
        await self._assert_module_core_worktree_pinned()

    async def test_remove_skips_empty_uninitialized_submodule_dir(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            str(self.repo_root),
            base_branch="main",
            worktree_submodules=[],
        )
        self.assertIsNotNone(wt_path)
        wt = Path(wt_path)
        sub_wt = wt / self.sub_path
        sub_wt.mkdir(parents=True, exist_ok=True)
        self.assertFalse((sub_wt / ".git").exists())
        self.assertEqual([], list(sub_wt.iterdir()))
        await self._hijack_module_core_worktree(wt / "not-a-submodule-worktree")
        core_worktree_before = await self._module_core_worktree()

        result = await self.mgr.remove_result(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["worktree_removed"], result)
        self.assertEqual([], result["nested_submodules"])
        self.assertFalse(wt.exists())
        self.assertEqual(core_worktree_before, await self._module_core_worktree())

    async def test_multi_worker_remove_keeps_submodule_module_core_worktree_pinned(self):
        await self._assert_module_core_worktree_pinned()
        cell_a, wt_a = await self._create_nested(
            agent_id="agent-a",
            name="Worker A",
        )
        sub_a = wt_a / self.sub_path
        await self._assert_module_core_worktree_pinned()

        cell_b, wt_b = await self._create_nested(
            agent_id="agent-b",
            name="Worker B",
        )
        sub_b = wt_b / self.sub_path
        await self._assert_module_core_worktree_pinned()

        result = await self.mgr.remove_result(
            cell_a,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(result["worktree_removed"], result)
        self.assertFalse(sub_a.exists())
        self.assertTrue(sub_b.exists())
        await self._assert_module_core_worktree_pinned()
        module_worktrees = await self._git_out(
            "worktree",
            "list",
            "--porcelain",
            cwd=self._module_dir(),
        )
        self.assertNotIn(str(sub_a), module_worktrees)
        self.assertIn(str(sub_b), module_worktrees)

    async def test_multi_worker_remove_recovers_after_submodule_update_hijack(self):
        cell_a, wt_a = await self._create_nested(
            agent_id="agent-a",
            name="Worker A",
        )
        sub_a = wt_a / self.sub_path
        cell_b, wt_b = await self._create_nested(
            agent_id="agent-b",
            name="Worker B",
        )
        sub_b = wt_b / self.sub_path
        code, _out, err = await self._git(
            "submodule",
            "update",
            "--init",
            self.sub_path,
            cwd=wt_a,
            check=False,
        )
        self.assertEqual(0, code, err)
        self.assertNotEqual(
            self._resolve_module_core_worktree(await self._module_core_worktree()),
            (self.repo_root / self.sub_path).resolve(),
        )

        result = await self.mgr.remove_result(
            cell_a,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(result["worktree_removed"], result)
        self.assertFalse(sub_a.exists())
        self.assertTrue(sub_b.exists())
        await self._assert_module_core_worktree_pinned()

    async def test_checkpoint_resets_clean_submodule_head_drift_to_base_gitlink(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        base_gitlink = await self._gitlink_sha(wt, "main")
        (self.repo_root / self.sub_path / "base.txt").write_text("base-only\n")
        await self._git("add", "base.txt", cwd=self.repo_root / self.sub_path)
        await self._git(
            "commit",
            "-m",
            "Advance main submodule outside worker",
            cwd=self.repo_root / self.sub_path,
        )
        drift_sha = await self._git_out(
            "rev-parse",
            "HEAD",
            cwd=self.repo_root / self.sub_path,
        )
        await self._git(
            "fetch",
            str(self.repo_root / self.sub_path),
            "main",
            cwd=sub_wt,
        )
        await self._git("reset", "--hard", drift_sha, cwd=sub_wt)
        self.assertEqual(
            drift_sha,
            await self._git_out("rev-parse", "HEAD", cwd=sub_wt),
        )
        (wt / "README.md").write_text("super line one\nworker-only\n")

        sha = await self.mgr.checkpoint(
            cell,
            message="Super-only checkpoint with clean submodule drift",
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(sha)
        self.assertEqual(base_gitlink, await self._gitlink_sha(wt, "HEAD"))
        self.assertEqual(
            base_gitlink,
            await self._git_out("rev-parse", "HEAD", cwd=sub_wt),
        )
        preserved = await self._git_out(
            "for-each-ref",
            "--format=%(objectname) %(refname:short)",
            "refs/heads/torque/preserved/deps-sub",
            cwd=self._module_dir(),
        )
        self.assertIn(drift_sha, preserved)
        self.assertEqual(
            ["README.md"],
            (
                await self._git_out(
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                    cwd=wt,
                )
            ).splitlines(),
        )

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
            "nested_submodule_merge_preflight",
            side_effect=AssertionError("nested merge preflight should be dormant"),
        ):
            check = await self.mgr.check_merge_conflicts(
                cell,
                worktree_submodules=[],
            )
        self.assertTrue(check["clean"], check)

        with mock.patch.object(
            self.mgr,
            "_rebase_nested_submodules",
            side_effect=AssertionError("nested rebase should be dormant"),
        ):
            self.assertTrue(
                await self.mgr.rebase_onto_base(
                    cell,
                    worktree_submodules=[],
                )
            )

        with mock.patch.object(
            self.mgr,
            "_remove_nested_submodule_worktrees",
            side_effect=AssertionError("nested remove should be dormant"),
        ):
            self.assertTrue(await self.mgr.remove(cell, worktree_submodules=[]))

    async def test_merge_both_merges_submodule_base_bumps_gitlink_and_super(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nmerged line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Nested merge work",
                worktree_submodules=[self.sub_path],
            )
        )
        worker_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)
        await self._push_sub_branch(sub_wt)

        merged = await self.mgr.server_merge(
            cell,
            "Merge nested submodule work",
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(merged["ok"], merged.get("error"))
        origin_main = await self._git_out(
            "--git-dir",
            str(self.sub_origin),
            "rev-parse",
            "main",
            cwd=self.root,
        )
        final_gitlink = await self._gitlink_sha(self.repo_root, "main")
        self.assertEqual(final_gitlink, origin_main)
        self.assertEqual(final_gitlink, worker_sub_sha)
        self.assertIn(final_gitlink, await self._git_out("ls-tree", "main", self.sub_path))

    async def test_preflight_rejects_super_conflict_before_nested_publish(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        state_mod = importlib.import_module("torque.state")
        state_mod = importlib.reload(state_mod)
        server_mod = importlib.import_module("torque.server")
        server_mod = importlib.reload(server_mod)

        cell = state_mod.AgentCell(
            id="agent-conflict",
            name="Conflict Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
        )
        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            worktree_submodules=[self.sub_path],
        )
        self.assertIsNotNone(wt_path)
        wt = Path(wt_path)

        (wt / "README.md").write_text("worker branch line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Worker README change",
                worktree_submodules=[self.sub_path],
            )
        )

        (self.repo_root / "README.md").write_text("base branch line\n")
        await self._git("add", "README.md", cwd=self.repo_root)
        await self._git(
            "commit",
            "--no-verify",
            "-m",
            "Conflicting base README change",
            cwd=self.repo_root,
        )

        state = state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", worktree_submodules=[self.sub_path])
        state.agents[cell.id] = cell
        state.groups["g"].append(cell.id)

        class RecordingWorktreeManager(WorktreeManager):
            def __init__(self):
                super().__init__()
                self.publish_called = False

            async def publish_nested_submodule_branches_for_merge(
                self,
                *_args,
                **_kwargs,
            ):
                self.publish_called = True
                return {"ok": True, "submodules": []}

        fake_mgr = RecordingWorktreeManager()

        async def latest_boundary_state(_cell):
            return {"latest": None, "clean": True, "reason": ""}

        gates = await server_mod._preflight_worktree_merge_gates(
            state=state,
            cell=cell,
            worktree_mgr=fake_mgr,
            aid=cell.id,
            data={"force_stale_base": True},
            latest_boundary_state_for_cell=latest_boundary_state,
            boundary_reason_message=lambda reason, latest: reason,
            panel_event=None,
            publish_nested_submodule_branches=True,
        )

        self.assertFalse(gates["ok"], gates)
        self.assertFalse(fake_mgr.publish_called)
        conflict_paths = {
            item.get("path")
            for item in gates["precheck"].get("conflicts", [])
        }
        self.assertIn("README.md", conflict_paths)

    async def test_pr_merge_path_reconciles_concurrent_submodule_base_advance(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        state_mod = importlib.import_module("torque.state")
        state_mod = importlib.reload(state_mod)
        server_mod = importlib.import_module("torque.server")
        server_mod = importlib.reload(server_mod)

        super_origin = self.root / "super-origin.git"
        await self._git("init", "--bare", str(super_origin), cwd=self.root)
        await self._git(
            "remote",
            "add",
            "origin",
            str(super_origin),
            cwd=self.repo_root,
        )
        await self._git("push", "-u", "origin", "main", cwd=self.repo_root)

        cell = state_mod.AgentCell(
            id="agent-pr",
            name="PR Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
        )
        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            worktree_submodules=[self.sub_path],
        )
        self.assertIsNotNone(wt_path)
        wt = Path(wt_path)
        sub_wt = wt / self.sub_path

        (sub_wt / "lib.txt").write_text("sub line one\npr merged line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Nested PR merge work",
                worktree_submodules=[self.sub_path],
            )
        )
        worker_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)
        sub_branch = await self._sub_branch(sub_wt)
        code, _out, _err = await self._git(
            "--git-dir",
            str(self.sub_origin),
            "rev-parse",
            "--verify",
            f"refs/heads/{sub_branch}",
            cwd=self.root,
            check=False,
        )
        self.assertNotEqual(
            0,
            code,
            "setup should reproduce the PR path's formerly-unpushed sub branch",
        )

        base_sub = self.repo_root / self.sub_path
        (base_sub / "base.txt").write_text("concurrent base line\n")
        await self._git("add", "base.txt", cwd=base_sub)
        await self._git(
            "commit",
            "-m",
            "Concurrent submodule base advance",
            cwd=base_sub,
        )
        await self._git("push", "origin", "main", cwd=base_sub)
        base_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=base_sub)
        await self._git("add", self.sub_path, cwd=self.repo_root)
        await self._git(
            "commit",
            "--no-verify",
            "-m",
            "Concurrent super gitlink advance",
            cwd=self.repo_root,
        )
        await self._git("push", "origin", "main", cwd=self.repo_root)

        pre_reconcile_check = await self.mgr.check_merge_conflicts(cell)
        self.assertFalse(pre_reconcile_check["clean"], pre_reconcile_check)
        self.assertTrue(
            server_mod._is_reconcilable_nested_gitlink_conflict(
                pre_reconcile_check,
                [self.sub_path],
            ),
            pre_reconcile_check,
        )

        class FakePrWorktreeManager(WorktreeManager):
            def __init__(self, case):
                super().__init__()
                self.case = case
                self.pushed_super_gitlinks = []
                self.branch = ""
                self.base_branch = ""

            async def github_preflight(self, worktree_path):
                return {
                    "ok": True,
                    "phase": "github_preflight",
                    "name_with_owner": "acme/super",
                }

            async def github_select_remote(self, worktree_path):
                return {
                    "ok": True,
                    "phase": "github_remote",
                    "remote": "origin",
                }

            async def github_push_branch(self, worktree_path, remote, branch):
                self.pushed_super_gitlinks.append(
                    await self.case._gitlink_sha(Path(worktree_path), "HEAD")
                )
                return await super().github_push_branch(
                    worktree_path,
                    remote,
                    branch,
                )

            async def github_create_or_reuse_pr(
                self,
                worktree_path,
                branch,
                base_branch,
                title="",
                body="",
            ):
                self.branch = branch
                self.base_branch = base_branch
                return {
                    "ok": True,
                    "phase": "pr_create",
                    "url": "https://github.com/acme/super/pull/1",
                    "number": 1,
                    "body": body,
                    "head_sha": await self.rev_parse(worktree_path, branch),
                    "state": "OPEN",
                    "merge_state": "CLEAN",
                    "existing": False,
                }

            async def github_request_squash_merge(
                self,
                worktree_path,
                pr_number,
                head_sha,
                subject="",
                body="",
                auto=False,
                url="",
            ):
                branch_sha = await self.rev_parse(worktree_path, self.branch)
                if head_sha != branch_sha:
                    return {
                        "ok": False,
                        "phase": "pr_merge",
                        "error": "head SHA did not match pushed branch",
                        "pending": False,
                    }
                base_sha = await self.rev_parse(worktree_path, self.base_branch)
                code, tree_out, tree_err = await self._git_run(
                    worktree_path,
                    "merge-tree",
                    "--write-tree",
                    self.base_branch,
                    self.branch,
                )
                if code != 0:
                    return {
                        "ok": False,
                        "phase": "pr_merge",
                        "error": tree_err or tree_out,
                        "pending": False,
                    }
                code, commit_out, commit_err = await self._git_run(
                    worktree_path,
                    "commit-tree",
                    tree_out.splitlines()[0].strip(),
                    "-p",
                    base_sha,
                    "-m",
                    subject or "Squash merge PR",
                    "-m",
                    body or "",
                )
                if code != 0:
                    return {
                        "ok": False,
                        "phase": "pr_merge",
                        "error": commit_err,
                        "pending": False,
                    }
                merge_sha = commit_out.splitlines()[0].strip()
                push = await self._git_run(
                    worktree_path,
                    "push",
                    "origin",
                    f"{merge_sha}:refs/heads/{self.base_branch}",
                )
                if push[0] != 0:
                    return {
                        "ok": False,
                        "phase": "pr_merge",
                        "error": push[2],
                        "pending": False,
                    }
                return {
                    "ok": True,
                    "phase": "pr_merge",
                    "url": "https://github.com/acme/super/pull/1",
                    "number": pr_number,
                    "head_sha": head_sha,
                    "merge_commit_sha": merge_sha,
                    "merge_state": "CLEAN",
                    "pending": False,
                    "pr_status": {"ok": True, "state": "MERGED"},
                }

        state = state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", worktree_submodules=[self.sub_path])
        state.agents[cell.id] = cell
        state.groups["g"].append(cell.id)
        fake_mgr = FakePrWorktreeManager(self)

        async def latest_boundary_state(_cell):
            return {"latest": None, "clean": True, "reason": ""}

        async def cleanup_after_merge(
            _cell,
            *,
            close_agent=False,
            remove_worktree=False,
        ):
            return {
                "close_agent": close_agent,
                "remove_worktree": remove_worktree,
                "agent_closed": close_agent,
                "worktree_removed": remove_worktree,
                "errors": [],
            }

        async def broadcast_toast(*_args, **_kwargs):
            return None

        class DummyBridge:
            async def send_text(self, *_args, **_kwargs):
                return None

        result = await server_mod._run_pr_worktree_merge(
            state=state,
            cell=cell,
            aid=cell.id,
            data={
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
                "force_stale_base": True,
            },
            worktree_mgr=fake_mgr,
            latest_boundary_state_for_cell=latest_boundary_state,
            boundary_reason_message=lambda reason, latest: reason,
            mark_branch_boundaries_merged=lambda *_args, **_kwargs: None,
            cleanup_after_merge=cleanup_after_merge,
            broadcast_toast=broadcast_toast,
            bridge=DummyBridge(),
            handle_command=None,
            panel_event=None,
        )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["mode"], "pull_request")
        self.assertTrue(result["nested_submodules"]["ok"])

        remote_sub_main = await self._git_out(
            "--git-dir",
            str(self.sub_origin),
            "rev-parse",
            "main",
            cwd=self.root,
        )
        self.assertEqual(fake_mgr.pushed_super_gitlinks, [remote_sub_main])
        self.assertNotEqual(remote_sub_main, worker_sub_sha)
        self.assertEqual(
            0,
            (
                await self._git(
                    "--git-dir",
                    str(self.sub_origin),
                    "merge-base",
                    "--is-ancestor",
                    worker_sub_sha,
                    remote_sub_main,
                    cwd=self.root,
                    check=False,
                )
            )[0],
        )
        self.assertEqual(
            0,
            (
                await self._git(
                    "--git-dir",
                    str(self.sub_origin),
                    "merge-base",
                    "--is-ancestor",
                    base_sub_sha,
                    remote_sub_main,
                    cwd=self.root,
                    check=False,
                )
            )[0],
        )
        remote_sub_branch = await self._git_out(
            "--git-dir",
            str(self.sub_origin),
            "rev-parse",
            sub_branch,
            cwd=self.root,
        )
        self.assertEqual(remote_sub_branch, remote_sub_main)

        remote_super_gitlink_line = await self._git_out(
            "--git-dir",
            str(super_origin),
            "ls-tree",
            "main",
            self.sub_path,
            cwd=self.root,
        )
        self.assertIn(remote_sub_main, remote_super_gitlink_line)
        self.assertEqual(
            await self._gitlink_sha(self.repo_root, "main"),
            remote_sub_main,
        )

    async def test_nested_publish_skips_detached_no_gitlink_change(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        await self._git("checkout", "--detach", "HEAD", cwd=sub_wt)

        result = await self.mgr.publish_nested_submodule_branches_for_merge(
            cell,
            [self.sub_path],
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["submodules"][0]["skip_reason"],
            "no_gitlink_change_detached_head",
        )

    async def test_nested_base_sync_preserves_dirty_checked_out_branch(self):
        base_sub = self.repo_root / self.sub_path
        original_main = await self._git_out("rev-parse", "main", cwd=base_sub)

        (self.sub_seed / "lib.txt").write_text("remote branch line\n")
        await self._git("add", "lib.txt", cwd=self.sub_seed)
        await self._git("commit", "-m", "Remote submodule main", cwd=self.sub_seed)
        await self._git("push", "origin", "main", cwd=self.sub_seed)
        await self._git("fetch", "origin", "main", cwd=base_sub)
        remote_main = await self._git_out("rev-parse", "origin/main", cwd=base_sub)
        self.assertNotEqual(original_main, remote_main)

        (base_sub / "lib.txt").write_text("local dirty line\n")

        result = await self.mgr._sync_branch_to_remote(
            str(self._module_dir()),
            remote="origin",
            branch="main",
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual((base_sub / "lib.txt").read_text(), "local dirty line\n")
        self.assertEqual(
            await self._git_out("rev-parse", "main", cwd=base_sub),
            original_main,
        )

        forced = await self.mgr._sync_branch_to_remote(
            str(self._module_dir()),
            remote="origin",
            branch="main",
            force=True,
        )
        self.assertFalse(forced["ok"], forced)
        self.assertIn("uncommitted changes", forced.get("error", ""))
        self.assertEqual((base_sub / "lib.txt").read_text(), "local dirty line\n")
        self.assertEqual(
            await self._git_out("rev-parse", "main", cwd=base_sub),
            original_main,
        )

    async def test_boundary_allows_pure_gitlink_reconciliation_commit(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nreviewed worker line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Reviewed nested work",
                worktree_submodules=[self.sub_path],
            )
        )
        reviewed_super_sha = await self._git_out("rev-parse", "HEAD", cwd=wt)
        recorded_submodules = await self.mgr.nested_submodule_head_states(
            cell,
            [self.sub_path],
        )
        await self._push_sub_branch(sub_wt)

        base_sub = self.repo_root / self.sub_path
        (base_sub / "base.txt").write_text("landed base line\n")
        await self._git("add", "base.txt", cwd=base_sub)
        await self._git("commit", "-m", "Landed submodule base", cwd=base_sub)
        await self._git("push", "origin", "main", cwd=base_sub)
        await self._git("add", self.sub_path, cwd=self.repo_root)
        await self._git(
            "commit",
            "--no-verify",
            "-m",
            "Landed super gitlink",
            cwd=self.repo_root,
        )

        merged = await self.mgr._merge_nested_submodules_for_merge(
            cell,
            [self.sub_path],
            message="Merge reviewed nested work",
        )
        self.assertTrue(merged["ok"], merged)
        reconciled_super_sha = await self._git_out("rev-parse", "HEAD", cwd=wt)
        current_submodules = await self.mgr.nested_submodule_head_states(
            cell,
            [self.sub_path],
        )

        boundary = await self.mgr.gitlink_reconciliation_boundary_state(
            cell,
            boundary_commit_sha=reviewed_super_sha,
            head_sha=reconciled_super_sha,
            recorded_submodules=recorded_submodules,
            current_submodules=current_submodules,
            worktree_submodules=[self.sub_path],
        )

        self.assertTrue(boundary["ok"], boundary)
        self.assertEqual(boundary["reason"], "gitlink_reconciliation")
        self.assertEqual(boundary["paths"], [self.sub_path])

    async def test_merge_preflight_blocks_dirty_submodule(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\ndirty line\n")

        check = await self.mgr.check_merge_conflicts(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertFalse(check["clean"])
        self.assertIn("DIRTY", check["error"])
        self.assertIn(self.sub_path, check["error"])
        self.assertIn("old_gitlink=", check["error"])
        self.assertIn("new_gitlink=", check["error"])

    async def test_merge_preflight_blocks_head_gitlink_mismatch(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nhead only line\n")
        await self._git("add", "lib.txt", cwd=sub_wt)
        await self._git("commit", "-m", "Submodule head only", cwd=sub_wt)

        check = await self.mgr.check_merge_conflicts(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertFalse(check["clean"])
        self.assertIn("HEAD_MISMATCH", check["error"])
        self.assertIn(self.sub_path, check["error"])

    async def test_merge_preflight_blocks_unpushed_submodule_branch(self):
        cell, wt = await self._create_nested()

        check = await self.mgr.check_merge_conflicts(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertFalse(check["clean"])
        self.assertIn("UNPUSHED", check["error"])
        self.assertIn(self.sub_path, check["error"])

    async def test_merge_preflight_blocks_gitlink_missing_from_remote(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nlocal only line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Local-only submodule gitlink",
                worktree_submodules=[self.sub_path],
            )
        )

        check = await self.mgr.check_merge_conflicts(
            cell,
            worktree_submodules=[self.sub_path],
        )

        self.assertFalse(check["clean"])
        self.assertIn("MISSING_FROM_REMOTE", check["error"])
        self.assertIn(self.sub_path, check["error"])

    async def test_rebase_both_rebases_submodule_and_rebumps_gitlink(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nworker line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Worker submodule work",
                worktree_submodules=[self.sub_path],
            )
        )

        base_sub = self.repo_root / self.sub_path
        (base_sub / "base.txt").write_text("base submodule line\n")
        await self._git("add", "base.txt", cwd=base_sub)
        await self._git("commit", "-m", "Base submodule advance", cwd=base_sub)
        await self._git("push", "origin", "main", cwd=base_sub)

        (self.repo_root / "README.md").write_text(
            "super line one\nbase super line\n"
        )
        await self._git("add", "README.md", cwd=self.repo_root)
        await self._git(
            "commit",
            "--no-verify",
            "-m",
            "Base super advance",
            cwd=self.repo_root,
        )

        self.assertTrue(
            await self.mgr.rebase_onto_base(
                cell,
                worktree_submodules=[self.sub_path],
            )
        )

        self.assertEqual(
            0,
            (
                await self._git(
                    "merge-base",
                    "--is-ancestor",
                    "main",
                    "HEAD",
                    cwd=sub_wt,
                    check=False,
                )
            )[0],
        )
        self.assertEqual(
            0,
            (
                await self._git(
                    "merge-base",
                    "--is-ancestor",
                    "main",
                    "HEAD",
                    cwd=wt,
                    check=False,
                )
            )[0],
        )
        self.assertEqual(
            await self._git_out("rev-parse", "HEAD", cwd=sub_wt),
            await self._gitlink_sha(wt, "HEAD"),
        )

    async def test_rebase_both_handles_base_superproject_gitlink_advance(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nworker line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Worker submodule work",
                worktree_submodules=[self.sub_path],
            )
        )
        old_worker_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)

        base_sub = self.repo_root / self.sub_path
        (base_sub / "base.txt").write_text("base submodule line\n")
        await self._git("add", "base.txt", cwd=base_sub)
        await self._git("commit", "-m", "Base submodule advance", cwd=base_sub)
        await self._git("push", "origin", "main", cwd=base_sub)
        base_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=base_sub)

        await self._git("add", self.sub_path, cwd=self.repo_root)
        await self._git(
            "commit",
            "--no-verify",
            "-m",
            "Base super gitlink advance",
            cwd=self.repo_root,
        )

        self.assertTrue(
            await self.mgr.rebase_onto_base(
                cell,
                worktree_submodules=[self.sub_path],
            )
        )

        rebased_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)
        self.assertNotEqual(rebased_sub_sha, old_worker_sub_sha)
        self.assertEqual(rebased_sub_sha, await self._gitlink_sha(wt, "HEAD"))
        self.assertEqual("", await self._git_out("status", "--porcelain", cwd=wt))
        self.assertEqual(
            "",
            await self._git_out("status", "--porcelain", cwd=sub_wt),
        )
        self.assertEqual(
            0,
            (
                await self._git(
                    "merge-base",
                    "--is-ancestor",
                    "main",
                    "HEAD",
                    cwd=wt,
                    check=False,
                )
            )[0],
        )
        self.assertEqual(
            0,
            (
                await self._git(
                    "merge-base",
                    "--is-ancestor",
                    base_sub_sha,
                    "HEAD",
                    cwd=sub_wt,
                    check=False,
                )
            )[0],
        )

    async def test_rebase_both_rolls_back_nested_branch_on_super_conflict(self):
        cell, wt = await self._create_nested()
        sub_wt = wt / self.sub_path
        (sub_wt / "lib.txt").write_text("sub line one\nworker line\n")
        (wt / "README.md").write_text("worker super line\n")
        self.assertTrue(
            await self.mgr.checkpoint(
                cell,
                message="Worker mixed work",
                worktree_submodules=[self.sub_path],
            )
        )
        old_worker_sub_sha = await self._git_out("rev-parse", "HEAD", cwd=sub_wt)

        base_sub = self.repo_root / self.sub_path
        (base_sub / "base.txt").write_text("base submodule line\n")
        await self._git("add", "base.txt", cwd=base_sub)
        await self._git("commit", "-m", "Base submodule advance", cwd=base_sub)
        await self._git("push", "origin", "main", cwd=base_sub)
        await self._git("add", self.sub_path, cwd=self.repo_root)

        (self.repo_root / "README.md").write_text("base super line\n")
        await self._git("add", "README.md", cwd=self.repo_root)
        await self._git(
            "commit",
            "--no-verify",
            "-m",
            "Base conflicting super advance",
            cwd=self.repo_root,
        )

        self.assertFalse(
            await self.mgr.rebase_onto_base(
                cell,
                worktree_submodules=[self.sub_path],
            )
        )

        self.assertEqual(old_worker_sub_sha, await self._gitlink_sha(wt, "HEAD"))
        self.assertEqual(
            old_worker_sub_sha,
            await self._git_out("rev-parse", "HEAD", cwd=sub_wt),
        )
        self.assertEqual("", await self._git_out("status", "--porcelain", cwd=wt))
        self.assertEqual(
            "",
            await self._git_out("status", "--porcelain", cwd=sub_wt),
        )
