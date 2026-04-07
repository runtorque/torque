import tempfile
from pathlib import Path
import subprocess
import unittest

from loom.worktree import LOOM_EXCLUDE_ENTRIES, WorktreeManager


class WorktreeGitignoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_gitignore_keeps_repo_ignore_minimal_and_uses_git_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            gitignore = repo_root / ".gitignore"
            gitignore.write_text(".loom/worktrees/\n")
            subprocess.run(
                ["git", "-C", str(repo_root), "init"],
                check=True,
                capture_output=True,
                text=True,
            )

            mgr = WorktreeManager()
            await mgr._ensure_gitignore(str(repo_root))

            self.assertEqual(
                gitignore.read_text().splitlines(),
                [
                    ".loom/worktrees/",
                ],
            )
            exclude_lines = [
                line
                for line in (repo_root / ".git" / "info" / "exclude")
                .read_text()
                .splitlines()
                if line and not line.startswith("#")
            ]
            self.assertEqual(
                exclude_lines,
                LOOM_EXCLUDE_ENTRIES,
            )
