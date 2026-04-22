import asyncio
import json
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
        for service in ("service-a", "service-b"):
            path = self.repo_root / "etl" / service / "node_modules"
            path.mkdir(parents=True)
            (path / ".keep").write_text("\n")
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
            slug="",
            kind="",
            owner_engineer_id="",
            worktree_path="",
            worktree_branch="",
            worktree_repo_root="",
            worktree_base_branch="",
            worktree_dirty=False,
            worktree_diff={},
            worktree_checkpoints=0,
        )

    def _make_state(self, *cells):
        return SimpleNamespace(
            agents={
                cell.id: cell for cell in cells
            }
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

    async def test_create_worktree_expands_glob_symlink_patterns(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            symlinks=["etl/**/node_modules"],
        )

        self.assertIsNotNone(wt_path)

        for service in ("service-a", "service-b"):
            link = Path(wt_path) / "etl" / service / "node_modules"
            target = self.repo_root / "etl" / service / "node_modules"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), target.resolve())

        exclude = self._worktree_gitdir(wt_path) / "info" / "exclude"
        lines = exclude.read_text().splitlines()
        self.assertEqual(
            [line for line in lines if line.endswith("/node_modules")],
            [
                "etl/service-a/node_modules",
                "etl/service-b/node_modules",
            ],
        )

    async def test_create_worktree_rejects_escape_glob_patterns(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            symlinks=["../outside", "etl/**/../node_modules"],
        )

        self.assertIsNotNone(wt_path)
        self.assertFalse((Path(wt_path) / "outside").exists())
        self.assertFalse((Path(wt_path) / "etl" / "service-a" / "node_modules").exists())
        self.assertFalse((Path(wt_path) / "etl" / "service-b" / "node_modules").exists())

    async def test_create_worktree_dedupes_overlapping_exact_and_glob_symlinks(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            symlinks=[
                "etl/**/node_modules",
                "etl/service-a/node_modules",
                "etl/**/missing_modules",
            ],
        )

        self.assertIsNotNone(wt_path)

        exclude = self._worktree_gitdir(wt_path) / "info" / "exclude"
        lines = exclude.read_text().splitlines()
        self.assertEqual(lines.count("etl/service-a/node_modules"), 1)
        self.assertEqual(lines.count("etl/service-b/node_modules"), 1)

    async def test_get_repo_root_returns_common_root_for_linked_worktree(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
        )

        self.assertIsNotNone(wt_path)
        self.assertEqual(
            await self.mgr.get_repo_root(wt_path),
            str(self.repo_root.resolve()),
        )

    async def test_stale_base_info_detects_base_advance_and_rebase_clears(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
        )
        self.assertIsNotNone(wt_path)

        (Path(wt_path) / "worker.txt").write_text("worker change\n")
        checkpoint = await self.mgr.checkpoint(cell, "Worker change")
        self.assertTrue(checkpoint)

        (self.repo_root / "base.txt").write_text("base change\n")
        await self._git("add", "base.txt")
        await self._git("commit", "-m", "Base branch advanced")

        stale = await self.mgr.stale_base_info(cell)

        self.assertTrue(stale["stale"])
        self.assertEqual(stale["branch"], cell.worktree_branch)
        self.assertEqual(stale["base_branch"], "main")
        self.assertEqual(stale["commits_on_base"], 1)
        self.assertEqual(stale["files_changed_on_base"], 1)
        self.assertIn("⚠ STALE BASE", stale["warning"])
        self.assertIn("Base branch advanced", stale["base_head_subject"])

        self.assertTrue(await self.mgr.rebase_onto_base(cell))

        refreshed = await self.mgr.stale_base_info(cell)
        self.assertFalse(refreshed["stale"])
        self.assertEqual(refreshed["fork_point"], refreshed["base_head"])

    async def test_stale_base_info_current_base_branch_is_not_stale(self):
        (self.repo_root / "base.txt").write_text("base change\n")
        await self._git("add", "base.txt")
        await self._git("commit", "-m", "Base branch already current")

        cell = self._make_cell()
        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
        )
        self.assertIsNotNone(wt_path)
        (Path(wt_path) / "worker.txt").write_text("worker change\n")
        self.assertTrue(await self.mgr.checkpoint(cell, "Worker change"))

        info = await self.mgr.stale_base_info(cell)

        self.assertFalse(info["stale"])
        self.assertEqual(info["fork_point"], info["base_head"])

    async def test_blank_custom_worktree_name_preserves_default_naming(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
            worktree_name="",
        )

        self.assertIsNotNone(wt_path)
        self.assertEqual(
            Path(wt_path).resolve(),
            (self.repo_root / ".loom" / "worktrees" / cell.id).resolve(),
        )
        self.assertTrue(cell.worktree_branch.startswith("loom/worker-"))

    async def test_worker_branch_is_namespaced_under_owner_engineer_slug(self):
        engineer = self._make_cell(agent_id="eng12345", name="Alice")
        engineer.kind = "engineer"
        engineer.slug = "alice"
        worker = self._make_cell(agent_id="face1234", name="Feature Worker")
        worker.kind = "worker"
        worker.slug = "feature-worker"
        worker.owner_engineer_id = engineer.id

        wt_path = await self.mgr.create(
            worker,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(engineer, worker),
        )

        self.assertIsNotNone(wt_path)
        self.assertRegex(
            worker.worktree_branch,
            r"^loom/alice/feature-worker-[a-f0-9]+$",
        )

    async def test_worker_branch_uses_user_namespace_without_owner(self):
        worker = self._make_cell(agent_id="cafe1234", name="Feature Worker")
        worker.kind = "worker"
        worker.slug = "feature-worker"

        wt_path = await self.mgr.create(
            worker,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(worker),
        )

        self.assertIsNotNone(wt_path)
        self.assertRegex(
            worker.worktree_branch,
            r"^loom/user/feature-worker-[a-f0-9]+$",
        )

    async def test_engineer_branch_keeps_flat_naming(self):
        engineer = self._make_cell(agent_id="beef1234", name="Alice")
        engineer.kind = "engineer"
        engineer.slug = "alice"

        wt_path = await self.mgr.create(
            engineer,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(engineer),
        )

        self.assertIsNotNone(wt_path)
        self.assertRegex(engineer.worktree_branch, r"^loom/alice-[a-f0-9]+$")

    async def test_engineer_worktree_disables_claude_auto_memory(self):
        engineer = self._make_cell(agent_id="beef1234", name="Alice")
        engineer.kind = "engineer"
        engineer.slug = "alice"

        wt_path = await self.mgr.create(
            engineer,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(engineer),
        )

        self.assertIsNotNone(wt_path)
        settings_path = Path(wt_path) / ".claude" / "settings.local.json"
        self.assertTrue(settings_path.exists())
        settings = json.loads(settings_path.read_text())
        self.assertIs(settings.get("autoMemoryEnabled"), False)
        self.assertEqual(await self._git("status", "--porcelain", cwd=wt_path), "")

    async def test_worker_worktree_disables_claude_auto_memory(self):
        worker = self._make_cell(agent_id="cafe1234", name="Feature Worker")
        worker.kind = "worker"
        worker.slug = "feature-worker"

        wt_path = await self.mgr.create(
            worker,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(worker),
        )

        self.assertIsNotNone(wt_path)
        settings_path = Path(wt_path) / ".claude" / "settings.local.json"
        self.assertTrue(settings_path.exists())
        settings = json.loads(settings_path.read_text())
        self.assertIs(settings.get("autoMemoryEnabled"), False)
        self.assertEqual(await self._git("status", "--porcelain", cwd=wt_path), "")

    async def test_claude_settings_merge_preserves_existing_keys(self):
        settings_path = self.repo_root / ".claude" / "settings.local.json"
        settings_path.parent.mkdir()
        settings_path.write_text(
            json.dumps({
                "permissions": {"allow": ["Bash(git status)"]},
                "autoMemoryEnabled": True,
            }) + "\n"
        )
        await self._git("add", "-f", ".claude/settings.local.json")
        await self._git("commit", "-m", "Add template Claude settings")

        worker = self._make_cell(agent_id="cafe1234", name="Feature Worker")
        worker.kind = "worker"
        worker.slug = "feature-worker"

        wt_path = await self.mgr.create(
            worker,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(worker),
        )

        self.assertIsNotNone(wt_path)
        settings = json.loads(
            (Path(wt_path) / ".claude" / "settings.local.json").read_text()
        )
        self.assertIs(settings.get("autoMemoryEnabled"), False)
        self.assertEqual(
            settings.get("permissions"),
            {"allow": ["Bash(git status)"]},
        )

    async def test_existing_claude_dir_is_preserved_when_writing_settings(self):
        claude_dir = self.repo_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "template-note.md").write_text("keep me\n")
        await self._git("add", ".claude/template-note.md")
        await self._git("commit", "-m", "Add Claude template dir")

        worker = self._make_cell(agent_id="cafe1234", name="Feature Worker")
        worker.kind = "worker"
        worker.slug = "feature-worker"

        wt_path = await self.mgr.create(
            worker,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(worker),
        )

        self.assertIsNotNone(wt_path)
        self.assertEqual(
            (Path(wt_path) / ".claude" / "template-note.md").read_text(),
            "keep me\n",
        )
        settings_path = Path(wt_path) / ".claude" / "settings.local.json"
        self.assertTrue(settings_path.exists())
        settings = json.loads(settings_path.read_text())
        self.assertIs(settings.get("autoMemoryEnabled"), False)

    async def test_architect_branch_keeps_flat_naming(self):
        architect = self._make_cell(agent_id="fade1234", name="Productmind")
        architect.kind = "architect"
        architect.slug = "productmind"

        wt_path = await self.mgr.create(
            architect,
            str(self.repo_root),
            base_branch="main",
            state=self._make_state(architect),
        )

        self.assertIsNotNone(wt_path)
        self.assertRegex(
            architect.worktree_branch,
            r"^loom/productmind-[a-f0-9]+$",
        )

    async def test_custom_worktree_name_is_sanitized_for_branch_and_path(self):
        cell = self._make_cell()

        wt_path = await self.mgr.create(
            cell,
            str(self.repo_root),
            base_dir=".loom/custom-worktrees",
            base_branch="main",
            worktree_name="Feature API / v2!!!",
        )

        self.assertIsNotNone(wt_path)
        self.assertEqual(
            Path(wt_path).resolve(),
            (self.repo_root / ".loom" / "custom-worktrees" / "feature-api-v2").resolve(),
        )
        self.assertEqual(cell.worktree_branch, "loom/feature-api-v2")

    async def test_worker_custom_worktree_name_keeps_namespaced_shortid_branch(self):
        engineer = self._make_cell(agent_id="eng12345", name="Alice")
        engineer.kind = "engineer"
        engineer.slug = "alice"
        worker = self._make_cell(agent_id="face1234", name="Feature Worker")
        worker.kind = "worker"
        worker.slug = "feature-worker"
        worker.owner_engineer_id = engineer.id

        wt_path = await self.mgr.create(
            worker,
            str(self.repo_root),
            base_dir=".loom/custom-worktrees",
            base_branch="main",
            worktree_name="Feature API / v2!!!",
            state=self._make_state(engineer, worker),
        )

        self.assertIsNotNone(wt_path)
        self.assertEqual(
            Path(wt_path).resolve(),
            (self.repo_root / ".loom" / "custom-worktrees" / "feature-api-v2").resolve(),
        )
        self.assertEqual(
            worker.worktree_branch,
            "loom/alice/feature-api-v2-face123",
        )

    async def test_custom_worktree_name_collision_adds_numeric_suffix(self):
        first = self._make_cell(agent_id="agent-1", name="Worker One")
        second = self._make_cell(agent_id="agent-2", name="Worker Two")

        first_path = await self.mgr.create(
            first,
            str(self.repo_root),
            base_branch="main",
            worktree_name="feature-api-v2",
        )
        second_path = await self.mgr.create(
            second,
            str(self.repo_root),
            base_branch="main",
            worktree_name="feature-api-v2",
        )

        self.assertIsNotNone(first_path)
        self.assertIsNotNone(second_path)
        self.assertEqual(first.worktree_branch, "loom/feature-api-v2")
        self.assertEqual(second.worktree_branch, "loom/feature-api-v2-2")
        self.assertEqual(
            Path(second_path).resolve(),
            (self.repo_root / ".loom" / "worktrees" / "feature-api-v2-2").resolve(),
        )

    async def test_legacy_flat_branch_still_diff_merges_and_removes(self):
        cell = self._make_cell(agent_id="123abcd9", name="Legacy Worker")
        cell.kind = "worker"
        cell.slug = "legacy-worker"
        cell.worktree_path = str(
            self.repo_root / ".loom" / "worktrees" / "legacy-worker"
        )
        cell.worktree_branch = "loom/legacy-worker-123abcd"
        cell.worktree_repo_root = str(self.repo_root)
        cell.worktree_base_branch = "main"

        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.repo_root),
            "worktree",
            "add",
            "-b",
            cell.worktree_branch,
            cell.worktree_path,
            "main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            self.fail(f"git worktree add failed: {stderr.decode().strip()}")

        readme = Path(cell.worktree_path) / "README.md"
        readme.write_text("line one\nlegacy branch change\n")
        await self._git("add", "README.md", cwd=cell.worktree_path)
        await self._git("commit", "-m", "Legacy branch update", cwd=cell.worktree_path)

        summary = await self.mgr.diff_files_summary(cell)
        self.assertEqual(summary["stats"]["files"], 1)
        self.assertEqual(summary["files"][0]["path"], "README.md")

        merged = await self.mgr.server_merge(cell, "Merge legacy branch")
        self.assertTrue(merged["ok"], merged.get("error"))
        self.assertEqual(
            (self.repo_root / "README.md").read_text(),
            "line one\nlegacy branch change\n",
        )

        old_path = Path(cell.worktree_path)
        removed = await self.mgr.remove(cell)
        self.assertTrue(removed)
        self.assertFalse(old_path.exists())
        self.assertEqual(cell.worktree_branch, "")

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

    async def test_diff_files_summary_reports_structured_signals(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        auth_file = Path(wt_path) / "auth" / "login.py"
        auth_file.parent.mkdir(parents=True)
        auth_file.write_text("def login():\n    return True\n")

        config_file = Path(wt_path) / "config" / "settings.yml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("feature: enabled\n")

        migration_file = Path(wt_path) / "db" / "migrations" / "001_add_users.sql"
        migration_file.parent.mkdir(parents=True)
        migration_file.write_text("create table users(id int);\n")

        binary_file = Path(wt_path) / "assets" / "logo.bin"
        binary_file.parent.mkdir(parents=True)
        binary_file.write_bytes(b"\x00\x01\x02")

        (Path(wt_path) / "shared" / "config.txt").unlink()

        await self._git("add", "-A", cwd=wt_path)
        await self._git("commit", "-m", "Add review signals", cwd=wt_path)

        summary = await self.mgr.diff_files_summary(cell)

        self.assertEqual(summary["stats"]["files"], 5)
        files = {item["path"]: item for item in summary["files"]}
        self.assertIn("auth/login.py", files)
        self.assertIn("config/settings.yml", files)
        self.assertIn("db/migrations/001_add_users.sql", files)
        self.assertIn("assets/logo.bin", files)
        self.assertIn("shared/config.txt", files)
        self.assertIn("auth", files["auth/login.py"]["signals"])
        self.assertIn("config", files["config/settings.yml"]["signals"])
        self.assertIn("migration", files["db/migrations/001_add_users.sql"]["signals"])
        self.assertIn("binary", files["assets/logo.bin"]["signals"])
        self.assertIn("destructive", files["shared/config.txt"]["signals"])
        self.assertEqual(summary["signal_counts"]["auth"], 1)
        self.assertEqual(summary["signal_counts"]["config"], 1)
        self.assertEqual(summary["signal_counts"]["migration"], 1)
        interesting_paths = {
            item["path"] for item in summary["interesting_files"]
        }
        self.assertIn("assets/logo.bin", interesting_paths)
        self.assertIn("shared/config.txt", interesting_paths)

    async def test_diff_files_summary_respects_path_filters(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        config_file = Path(wt_path) / "config" / "settings.yml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("feature: enabled\n")

        auth_file = Path(wt_path) / "auth" / "login.py"
        auth_file.parent.mkdir(parents=True)
        auth_file.write_text("def login():\n    return True\n")

        await self._git("add", "-A", cwd=wt_path)
        await self._git("commit", "-m", "Add filtered paths", cwd=wt_path)

        summary = await self.mgr.diff_files_summary(
            cell,
            paths=["config/settings.yml"],
        )

        self.assertEqual(summary["stats"]["files"], 1)
        self.assertEqual(
            [item["path"] for item in summary["files"]],
            ["config/settings.yml"],
        )
        self.assertEqual(summary["files"][0]["signals"], ["config"])

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

    async def test_is_branch_merged_treats_reset_to_base_branch_as_prunable(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        readme = Path(wt_path) / "README.md"
        readme.write_text("line one\nmerged from worktree\n")
        commit_sha = await self.mgr.checkpoint(cell, message="Finish worker change")
        self.assertTrue(commit_sha)

        merge_result = await self.mgr.server_merge(cell, "Merge worker change")

        self.assertTrue(merge_result["ok"], merge_result.get("error"))
        self.assertTrue(await self.mgr.reset_to_base(cell))
        self.assertFalse(await self.mgr.is_merged(cell))
        self.assertTrue(
            await self.mgr.is_branch_merged(
                str(self.repo_root),
                branch=cell.worktree_branch,
                base_branch=cell.worktree_base_branch,
            )
        )

    async def test_check_merge_conflicts_reports_binary_add_add_details(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        worker_binary = Path(wt_path) / "assets" / "logo.bin"
        worker_binary.parent.mkdir(parents=True)
        worker_binary.write_bytes(b"\x00\x01\x02")
        await self._git("add", "assets/logo.bin", cwd=wt_path)
        await self._git("commit", "-m", "Worker adds binary", cwd=wt_path)

        main_binary = self.repo_root / "assets" / "logo.bin"
        main_binary.parent.mkdir(parents=True)
        main_binary.write_bytes(b"\x03\x04\x05\x06")
        await self._git("add", "assets/logo.bin")
        await self._git("commit", "-m", "Main adds binary")

        conflict_info = await self.mgr.check_merge_conflicts(cell)

        self.assertFalse(conflict_info["clean"])
        self.assertEqual(len(conflict_info["conflicts"]), 1)
        conflict = conflict_info["conflicts"][0]
        self.assertEqual(conflict["path"], "assets/logo.bin")
        self.assertTrue(conflict["binary"])
        self.assertTrue(conflict["ours_blob_sha"])
        self.assertTrue(conflict["theirs_blob_sha"])
        self.assertEqual(conflict["ours_size"], 4)
        self.assertEqual(conflict["theirs_size"], 3)
        self.assertIn("binary differs", conflict["detail"])
        self.assertIn("main", conflict["detail"])
        self.assertIn(cell.worktree_branch, conflict["detail"])

    async def test_merge_untracked_overwrite_paths_detect_checked_out_base_collisions(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        tracked_file = Path(wt_path) / "app" / "config.yml"
        tracked_file.parent.mkdir(parents=True)
        tracked_file.write_text("tracked from worktree\n")
        await self._git("add", "app/config.yml", cwd=wt_path)
        await self._git("commit", "-m", "Add config from worktree", cwd=wt_path)

        local_untracked = self.repo_root / "app" / "config.yml"
        local_untracked.parent.mkdir(parents=True)
        local_untracked.write_text("repo local only\n")

        check = await self.mgr.check_merge_conflicts(cell)
        overwrite_paths = await self.mgr.merge_untracked_overwrite_paths(
            str(self.repo_root),
            "main",
            check["tree_sha"],
        )

        self.assertEqual(overwrite_paths, ["app/config.yml"])

    async def test_rebase_untracked_overwrite_paths_detect_worktree_collisions(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        local_untracked = Path(wt_path) / "app" / "config.yml"
        local_untracked.parent.mkdir(parents=True)
        local_untracked.write_text("worktree local only\n")

        tracked_file = self.repo_root / "app" / "config.yml"
        tracked_file.parent.mkdir(parents=True)
        tracked_file.write_text("tracked on main\n")
        await self._git("add", "app/config.yml")
        await self._git("commit", "-m", "Main adds config")

        overwrite_paths = await self.mgr.rebase_untracked_overwrite_paths(cell)

        self.assertEqual(overwrite_paths, ["app/config.yml"])

    async def test_list_worktrees_and_prune_admin_clears_missing_paths(self):
        cell = self._make_cell()
        wt_path = await self.mgr.create(cell, str(self.repo_root), base_branch="main")

        self.assertIsNotNone(wt_path)

        self.assertTrue(
            any(
                item["path"] == wt_path
                for item in await self.mgr.list_worktrees(str(self.repo_root))
            )
        )

        Path(wt_path).rename(Path(wt_path + "-moved"))
        listed = await self.mgr.list_worktrees(str(self.repo_root))
        stale_entry = next(item for item in listed if item["path"] == wt_path)
        self.assertTrue(stale_entry["prunable"])

        self.assertTrue(await self.mgr.prune_admin(str(self.repo_root)))
        listed_after = await self.mgr.list_worktrees(str(self.repo_root))
        self.assertFalse(any(item["path"] == wt_path for item in listed_after))

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
