"""Unsaved prompt fields must agree across rendering without mutating state."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from torque.commands.prompt_preview import (
    PromptPreviewRuntime, handle_prompt_preview_command,
)


class PromptPreviewDraftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task = SimpleNamespace(
            id="task-1", task="Saved title", description="Saved description",
            action_name="build", action_vars={"SCOPE": "saved"},
            agent_template="saved-role", agent_id="worker", group="Original",
            attachments=[{"filename": "saved.png", "path": "/saved.png"}],
            artifacts=[], parent_task_id="parent", pipeline_depth=2,
            slug="saved-slug", labels=["saved"], status="queued",
            verification_mode="restart", verification_state="pending",
            verification_notes="Saved gate", verification_summary={},
        )
        self.cell = SimpleNamespace(
            id="worker", role="saved-role", template="saved-role",
            group="Original", worktree_repo_root="", directory="/project",
        )
        self.state = SimpleNamespace(
            board_tasks={"task-1": self.task}, agents={"worker": self.cell}, db=None,
        )
        self.context = Mock(side_effect=lambda state, cell, task: {
            "context": {"is_clean": True},
            "task": {"title": task.task, "description": task.description,
                     "labels": task.labels, "id": task.id},
        })
        self.actions = Mock()
        self.actions.render_action.side_effect = lambda name, values, **kw: {
            "prompt": repr((values, kw["torque_context"]["task"]))}
        self.postscript = Mock(side_effect=lambda task, *args, **kw: " | " + task.description)
        self.assemble = Mock(side_effect=lambda **kw: kw["prompt_body"] + kw["postscript"])
        self.runtime = PromptPreviewRuntime(
            assemble_worker_prompt=self.assemble,
            behavior_overlay_prompt_block_for_cell=Mock(return_value=""),
            build_postscript=self.postscript, build_torque_context=self.context,
            resolve_base_dir=AsyncMock(return_value="/project"),
            action_mgr=self.actions, state=self.state, template_mgr=Mock(),
        )
        self.enterContext(patch("torque.commands.prompt_preview.build_prompt_memory_block", return_value=""))
        self.enterContext(patch("torque.commands.prompt_preview.serialize_upstream_task_artifacts", return_value=[]))

    async def test_unsaved_fields_reach_context_role_and_postscript_without_writing(self):
        result = await handle_prompt_preview_command({
            "id": "task-1", "task": "Draft title", "description": "",
            "group": "Draft group", "agent_template": "draft-role",
            "action_vars": {"SCOPE": "draft"}, "attachments": [],
            "artifacts": [], "labels": [],
        }, self.runtime)
        self.assertEqual(result["task_id"], "task-1")
        values = self.actions.render_action.call_args.args[1]
        self.assertEqual(values, {"TASK": "Draft title", "SCOPE": "draft"})
        context = self.actions.render_action.call_args.kwargs["torque_context"]
        self.assertEqual(context["task"], {"title": "Draft title", "description": "", "labels": [], "id": "task-1"})
        draft = self.postscript.call_args.args[0]
        self.assertEqual(draft.description, "")
        self.assertEqual(draft.parent_task_id, "parent")
        self.assertEqual(draft.verification_notes, "Saved gate")
        self.assertEqual(self.assemble.call_args.kwargs["cell"].role, "draft-role")
        self.assertEqual(self.assemble.call_args.kwargs["cell"].group, "Draft group")
        self.assertEqual(self.task.description, "Saved description")
        self.assertEqual(self.task.attachments[0]["filename"], "saved.png")
        self.assertEqual(self.task.action_vars, {"SCOPE": "saved"})
        self.assertEqual(self.cell.role, "saved-role")
        self.assertEqual(self.cell.group, "Original")

    async def test_id_only_preview_keeps_persisted_defaults(self):
        await handle_prompt_preview_command({"id": "task-1"}, self.runtime)
        self.assertEqual(self.actions.render_action.call_args.args[:2], ("build", {"TASK": "Saved title", "SCOPE": "saved"}))
        self.assertEqual(self.postscript.call_args.args[0].description, "Saved description")
        self.assertEqual(self.assemble.call_args.kwargs["cell"].role, "saved-role")

    async def test_explicit_clears_do_not_fall_back_to_saved_task_or_agent(self):
        result = await handle_prompt_preview_command({
            "id": "task-1", "task": "", "description": "", "action_name": "",
            "agent_id": "", "agent_template": "", "action_vars": {},
            "attachments": [], "artifacts": [],
        }, self.runtime)
        self.actions.render_action.assert_not_called()
        self.assertIsNone(self.assemble.call_args.kwargs["cell"])
        self.assertEqual(result["prompt"], " | ")
        self.assertEqual(self.task.task, "Saved title")

    async def test_unassigned_draft_retains_identity_and_unsaved_context(self):
        await handle_prompt_preview_command({
            "id": "task-1", "agent_id": "", "agent_template": "",
            "task": "Unassigned draft", "description": "", "labels": ["draft"],
        }, self.runtime)
        context = self.actions.render_action.call_args.kwargs["torque_context"]["task"]
        self.assertEqual(context["id"], "task-1")
        self.assertEqual(context["parent_task_id"], "parent")
        self.assertEqual(context["depth"], 2)
        self.assertEqual(context["title"], "Unassigned draft")
        self.assertEqual(context["description"], "")
        self.assertEqual(context["labels"], ["draft"])
