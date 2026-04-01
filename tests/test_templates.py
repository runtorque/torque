import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from loom.templates import TemplateManager


class TemplateManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo" / "subdir"
        self.project.mkdir(parents=True)
        self.project_templates = self.root / "repo" / ".loom" / "agents"
        self.project_templates.mkdir(parents=True)
        self.user_home = self.root / "home"
        self.user_templates = self.user_home / ".loom" / "agents"
        self.user_templates.mkdir(parents=True)
        self.prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.user_home)
        self.addCleanup(self._restore_home)
        self.mgr = TemplateManager()

    def _restore_home(self):
        if self.prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.prev_home

    def test_list_templates_marks_shadowed_user_templates(self):
        (self.project_templates / "researcher.yaml").write_text(
            "name: researcher\ndescription: Project\n")
        (self.user_templates / "researcher.yaml").write_text(
            "name: researcher\ndescription: User\n")
        (self.user_templates / "implementer.yaml").write_text(
            "name: implementer\ndescription: User\n")

        listed = self.mgr.list_templates(str(self.project))
        by_name = {(item["name"], item["global"]): item for item in listed}

        self.assertFalse(by_name[("researcher", False)]["shadowed"])
        self.assertTrue(by_name[("researcher", True)]["shadowed"])
        self.assertFalse(by_name[("implementer", True)]["shadowed"])

    def test_save_template_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            self.mgr.save_template(
                "bad",
                {"name": "bad", "provider": "codex", "unknown": "x"},
                scope="project",
                base_dir=str(self.project),
            )

    def test_resolve_agent_config_merges_in_expected_order(self):
        self.mgr.save_template(
            "base/default",
            {
                "name": "base/default",
                "provider": "claude-code",
                "tab_color": "#111111",
                "model": "opus",
                "worktree": True,
                "worktree_base_branch": "main",
                "env_vars": {"BASE": "1", "SHARED": "base"},
            },
            scope="project",
            base_dir=str(self.project),
        )
        self.mgr.save_template(
            "researcher",
            {
                "name": "researcher",
                "provider": "codex",
                "model": "gpt-5",
                "tab_color": "#222222",
                "session_resume": False,
                "env_vars": {"TPL": "1", "SHARED": "template"},
            },
            scope="project",
            base_dir=str(self.project),
        )

        gs = SimpleNamespace(
            default_agent_template="base/default",
            agent_provider="claude-code",
            agent_directory="/group/repo",
            agent_profile="Dev",
            agent_shell="zsh",
            agent_tab_color="#abcdef",
            agent_env_vars={"GROUP": "1", "SHARED": "group"},
            git_worktree=False,
            worktree_base_dir=".loom/custom",
            worktree_base_branch="develop",
            worktree_auto_checkpoint=True,
            worktree_merge_squash=False,
            agent_session_resume=True,
        )

        resolved = self.mgr.resolve_agent_config(
            "researcher",
            gs,
            {
                "directory": "/override/repo",
                "shell": "bash",
                "tab_color": "#333333",
                "env_vars": {"OVERRIDE": "1", "SHARED": "override"},
            },
            base_dir=str(self.project),
        )

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["model"], "gpt-5")
        self.assertEqual(resolved["directory"], "/override/repo")
        self.assertEqual(resolved["profile"], "Dev")
        self.assertEqual(resolved["shell"], "bash")
        self.assertEqual(resolved["tab_color"], "#333333")
        self.assertFalse(resolved["worktree"])
        self.assertEqual(resolved["worktree_base_dir"], ".loom/custom")
        self.assertEqual(resolved["worktree_base_branch"], "develop")
        self.assertTrue(resolved["worktree_auto_checkpoint"])
        self.assertFalse(resolved["worktree_merge_squash"])
        self.assertFalse(resolved["session_resume"])
        self.assertEqual(
            resolved["env_vars"],
            {
                "BASE": "1",
                "GROUP": "1",
                "TPL": "1",
                "OVERRIDE": "1",
                "SHARED": "override",
            },
        )
