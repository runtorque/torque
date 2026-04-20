import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class DispatchPreambleTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.actions_mod = importlib.import_module("loom.actions")
        self.actions_mod = importlib.reload(self.actions_mod)
        self.roles_mod = importlib.import_module("loom.roles")
        self.roles_mod = importlib.reload(self.roles_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo" / "subdir"
        self.project.mkdir(parents=True)
        (self.root / "repo" / ".loom" / "roles").mkdir(parents=True)
        self.user_home = self.root / "home"
        (self.user_home / ".loom" / "roles").mkdir(parents=True)
        self.prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.user_home)
        self.addCleanup(self._restore_home)

        self.role_mgr = self.roles_mod.RoleManager()
        self.action_mgr = self.actions_mod.ActionManager()
        self.state = self.state_mod.MatrixState()
        self.state.add_group("g")

        self.engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            slug="engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        self.worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            kind="worker",
            role="reviewer",
            template="reviewer",
            owner_engineer_id="engineer-1",
            created_by_weaver_id="engineer-1",
        )
        self.legacy_worker = self.state_mod.AgentCell(
            id="worker-legacy",
            name="Legacy Worker",
            slug="legacy-worker",
            group="g",
            cell_type="agent",
            kind="",
            role="reviewer",
            template="reviewer",
            owner_engineer_id="engineer-1",
            created_by_weaver_id="engineer-1",
        )
        for cell in (self.engineer, self.worker, self.legacy_worker):
            self.state.agents[cell.id] = cell
            self.state.groups["g"].append(cell.id)

        self.task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
            action_name="review/code",
        )
        self.task.agent_id = self.worker.id

    def _restore_home(self):
        if self.prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.prev_home

    def _render_action(self, raw: str, *, loom_context=None) -> dict:
        return self.action_mgr.render_action(
            raw,
            {"TASK": self.task.task},
            loom_context=loom_context,
        )

    def test_worker_role_preamble_is_prepended_before_action_and_postscript(self):
        self.role_mgr.save_role(
            "reviewer",
            {
                "name": "reviewer",
                "preamble": "Be careful.",
                "priorities": ["a", "b"],
            },
            scope="project",
            base_dir=str(self.project),
        )
        loom_ctx = self.server_mod._build_loom_context(
            self.state, self.worker, self.task
        )
        rendered = self._render_action(
            "name: review\nprompt: |\n  {{ TASK }}\n",
            loom_context=loom_ctx,
        )

        prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.worker,
            base_dir=str(self.project),
            prompt_body=rendered["prompt"],
            postscript="LOOM POSTSCRIPT",
            disable_role_preamble=rendered["disable_role_preamble"],
        )

        self.assertEqual(
            prompt,
            "Be careful.\n\nPriorities:\n- a\n- b\n\n"
            "Implement feature\n\nLOOM POSTSCRIPT\n",
        )

    def test_empty_role_preamble_emits_no_preamble_block(self):
        self.role_mgr.save_role(
            "reviewer",
            {"name": "reviewer"},
            scope="project",
            base_dir=str(self.project),
        )

        prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.worker,
            base_dir=str(self.project),
            prompt_body="Implement feature",
            postscript="LOOM POSTSCRIPT",
        )

        self.assertEqual(prompt, "Implement feature\n\nLOOM POSTSCRIPT\n")

    def test_disable_role_preamble_omits_preamble_even_for_worker(self):
        self.role_mgr.save_role(
            "reviewer",
            {
                "name": "reviewer",
                "preamble": "Be careful.",
                "priorities": ["a", "b"],
            },
            scope="project",
            base_dir=str(self.project),
        )
        rendered = self._render_action(
            "name: diag\ndisable_role_preamble: true\nprompt: |\n  {{ TASK }}\n"
        )

        prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.worker,
            base_dir=str(self.project),
            prompt_body=rendered["prompt"],
            postscript="LOOM POSTSCRIPT",
            disable_role_preamble=rendered["disable_role_preamble"],
        )

        self.assertEqual(prompt, "Implement feature\n\nLOOM POSTSCRIPT\n")

    def test_engineer_and_terminal_agents_do_not_get_role_preamble(self):
        self.role_mgr.save_role(
            "reviewer",
            {"name": "reviewer", "preamble": "Be careful."},
            scope="project",
            base_dir=str(self.project),
        )
        self.engineer.role = "reviewer"
        self.engineer.template = "reviewer"

        prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.engineer,
            base_dir=str(self.project),
            prompt_body="Implement feature",
            postscript="LOOM POSTSCRIPT",
        )

        self.assertEqual(prompt, "Implement feature\n\nLOOM POSTSCRIPT\n")

    def test_legacy_worker_without_kind_still_gets_role_preamble(self):
        self.role_mgr.save_role(
            "reviewer",
            {"name": "reviewer", "preamble": "Be careful."},
            scope="project",
            base_dir=str(self.project),
        )

        prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.legacy_worker,
            base_dir=str(self.project),
            prompt_body="Implement feature",
            postscript="LOOM POSTSCRIPT",
        )

        self.assertEqual(
            prompt,
            "Be careful.\n\nImplement feature\n\nLOOM POSTSCRIPT\n",
        )

    def test_loom_agent_context_and_stub_defaults_render_safely(self):
        raw = (
            "name: inspect\nprompt: |\n"
            "  kind={{ loom.agent.kind }}\n"
            "  role={{ loom.agent.role }}\n"
            "  owner={{ loom.agent.owner_engineer }}\n"
            "  {{ TASK }}\n"
        )

        stub_rendered = self._render_action(raw)
        self.assertEqual(
            stub_rendered["prompt"],
            "kind=\nrole=\nowner=\nImplement feature",
        )

        loom_ctx = self.server_mod._build_loom_context(
            self.state, self.worker, self.task
        )
        self.assertEqual(loom_ctx["agent"]["kind"], "worker")
        self.assertEqual(loom_ctx["agent"]["role"], "reviewer")
        self.assertEqual(loom_ctx["agent"]["owner_engineer"], "Engineer")

        rendered = self._render_action(raw, loom_context=loom_ctx)
        self.assertEqual(
            rendered["prompt"],
            "kind=worker\nrole=reviewer\nowner=Engineer\nImplement feature",
        )

    def test_preview_and_dispatch_use_same_prompt_structure_helper(self):
        self.role_mgr.save_role(
            "reviewer",
            {"name": "reviewer", "preamble": "Be careful."},
            scope="project",
            base_dir=str(self.project),
        )
        loom_ctx = self.server_mod._build_loom_context(
            self.state, self.worker, self.task
        )
        rendered = self._render_action(
            "name: review\nprompt: |\n  {{ TASK }}\n",
            loom_context=loom_ctx,
        )
        prompt_body = rendered["prompt"] + "\n\n## Task artifacts\n- none"
        postscript = "LOOM POSTSCRIPT"

        dispatch_prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.worker,
            base_dir=str(self.project),
            prompt_body=prompt_body,
            postscript=postscript,
            disable_role_preamble=rendered["disable_role_preamble"],
        )
        preview_prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=self.worker,
            base_dir=str(self.project),
            prompt_body=prompt_body,
            postscript=postscript,
            disable_role_preamble=rendered["disable_role_preamble"],
        )

        self.assertEqual(dispatch_prompt, preview_prompt)

    def test_worktree_checkpoint_context_preserves_integer_zero(self):
        self.worker.worktree_checkpoints = 0

        loom_ctx = self.server_mod._build_loom_context(
            self.state, self.worker, self.task
        )
        self.assertIsInstance(loom_ctx["worktree"]["checkpoints"], int)
        self.assertEqual(loom_ctx["worktree"]["checkpoints"], 0)

        rendered = self._render_action(
            "name: preview\nprompt: |\n"
            "  checkpoints={{ loom.worktree.checkpoints }}\n",
            loom_context=loom_ctx,
        )
        self.assertEqual(rendered["prompt"], "checkpoints=0")

    def test_inline_role_preview_worker_without_agent_builds_context_safely(self):
        self.role_mgr.save_role(
            "reviewer",
            {"name": "reviewer", "preamble": "Be careful."},
            scope="project",
            base_dir=str(self.project),
        )
        preview_cell = SimpleNamespace(
            id="",
            name="",
            slug="",
            group="g",
            cell_type="agent",
            agent_type="",
            directory="",
            kind="worker",
            role="reviewer",
            template="reviewer",
            owner_engineer_id="",
            created_by_weaver_id="",
            worktree_repo_root="",
            git_root="",
            worktree_branch="",
            worktree_auto_checkpoint=False,
            checkpoint_on_progress=False,
        )
        preview_task = SimpleNamespace(
            id="",
            task=self.task.task,
            slug="",
            description="",
            pipeline_depth=0,
            parent_task_id="",
            pipeline_root_id="",
            labels=[],
            group="g",
            status="",
            verification_mode="",
            verification_state="",
            verification_notes="",
            verification_updated_at="",
            verification_updated_by="",
            verification_summary={},
            worktree_boundary={},
            resume_after_boundary_task_id="",
            attachments=[],
            artifacts=[],
            action_name="review/code",
            created_at="",
            updated_at="",
            agent_id="",
        )

        loom_ctx = self.server_mod._build_loom_context(
            self.state, preview_cell, preview_task
        )
        self.assertTrue(loom_ctx["context"]["is_clean"])
        self.assertEqual(loom_ctx["context"]["tasks_dispatched"], 0)
        self.assertEqual(loom_ctx["worktree"]["path"], "")
        self.assertIsInstance(loom_ctx["worktree"]["checkpoints"], int)
        self.assertEqual(loom_ctx["worktree"]["checkpoints"], 0)
        self.assertEqual(loom_ctx["agent"]["role"], "reviewer")

        rendered = self._render_action(
            "name: preview\nprompt: |\n"
            "  role={{ loom.agent.role }}\n"
            "  checkpoints={{ loom.worktree.checkpoints }}\n"
            "  {{ TASK }}\n",
            loom_context=loom_ctx,
        )
        prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=self.role_mgr,
            cell=preview_cell,
            base_dir=str(self.project),
            prompt_body=rendered["prompt"],
            postscript="LOOM POSTSCRIPT",
            disable_role_preamble=rendered["disable_role_preamble"],
        )

        self.assertEqual(
            prompt,
            "Be careful.\n\nrole=reviewer\ncheckpoints=0\n"
            "Implement feature\n\nLOOM POSTSCRIPT\n",
        )
