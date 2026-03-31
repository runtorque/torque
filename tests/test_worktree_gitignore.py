import tempfile
from pathlib import Path
import unittest

from loom.worktree import WorktreeManager


class WorktreeGitignoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_gitignore_adds_all_managed_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            gitignore = repo_root / ".gitignore"
            gitignore.write_text(".loom/worktrees/\n")

            mgr = WorktreeManager()
            await mgr._ensure_gitignore(str(repo_root))

            self.assertEqual(
                gitignore.read_text().splitlines(),
                [
                    ".loom/worktrees/",
                    ".claude/settings.local.json",
                    ".mcp.json",
                    ".claude/skills/loom-*/",
                    ".codex/config.toml",
                    ".codex/hooks.json",
                ],
            )
