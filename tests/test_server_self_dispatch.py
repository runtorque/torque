import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from enum import Enum
try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


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


class ServerSelfDispatchTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_prompts_mod = importlib.import_module("loom.server_prompts")
        self.server_prompts_mod = importlib.reload(self.server_prompts_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_agent(self, agent_id, *, current_task_id="",
                    worktree_path="/repo/.loom/worktrees/shared",
                    worktree_branch="loom/shared",
                    worktree_repo_root="/repo"):
        return self.state_mod.AgentCell(
            id=agent_id,
            name=agent_id,
            group="g",
            current_task_id=current_task_id,
            worktree_path=worktree_path,
            worktree_branch=worktree_branch,
            worktree_repo_root=worktree_repo_root,
        )

    def test_self_dispatch_prompt_is_minimal_hint(self):
        self.assertEqual(
            self.server_mod._build_self_dispatch_prompt(),
            "Proceed with the derived task you just created.",
        )

    def test_self_dispatch_prompt_can_append_shared_context(self):
        prompt = self.server_mod._build_self_dispatch_prompt(
            "\n\nRelevant shared context:\n- [decision] Keep it focused."
        )

        self.assertIn("Proceed with the derived task you just created.", prompt)
        self.assertIn("Relevant shared context", prompt)

    def test_append_task_artifacts_can_include_upstream_handoff_block(self):
        prompt = self.server_mod._append_task_artifacts(
            "Implement the plan.",
            [],
            [],
            [
                {
                    "type": "generated_doc",
                    "title": "Implementation plan",
                    "path": "/tmp/task-parent/plan.md",
                    "summary": "Canonical downstream handoff",
                    "source_task_id": "task-parent",
                    "source_task_label": "Research auth patch",
                }
            ],
        )

        self.assertIn("Implement the plan.", prompt)
        self.assertIn("## Upstream handoff artifacts", prompt)
        self.assertIn("From `Research auth patch` (task-parent)", prompt)
        self.assertIn("Canonical downstream handoff", prompt)

    def test_resolve_memory_cell_and_task_recovers_task_from_active_agent(self):
        state = self.state_mod.MatrixState()
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Document the context flow",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
        )
        agent = self._make_agent("agent-1", current_task_id="task-1")
        state.agents[agent.id] = agent
        state.board_tasks[task.id] = task

        cell, resolved_task = self.server_mod._resolve_memory_cell_and_task(
            state,
            cell_id="agent-1",
        )

        self.assertIs(cell, agent)
        self.assertIs(resolved_task, task)

    def test_resolve_memory_scope_ref_defaults_from_context(self):
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Document the context flow",
            group="g",
            lane="In Progress",
            pipeline_root_id="root-1",
        )
        agent = self._make_agent("agent-1", current_task_id="task-1")

        self.assertEqual(
            self.server_mod._resolve_memory_scope_ref(
                "task",
                "",
                cell=agent,
                task=task,
            ),
            "task-1",
        )
        self.assertEqual(
            self.server_mod._resolve_memory_scope_ref(
                "pipeline",
                "",
                cell=agent,
                task=task,
            ),
            "root-1",
        )
        self.assertEqual(
            self.server_mod._resolve_memory_scope_ref(
                "group",
                "",
                cell=agent,
                task=task,
            ),
            "g",
        )

    def test_resolve_task_id_accepts_alias_and_prefix(self):
        state = self.state_mod.MatrixState()
        task = self.state_mod.BoardTask(
            id="LOOM:12",
            task="Document the context flow",
            group="g",
            lane="Backlog",
        )
        state.board_tasks[task.id] = task
        state.task_id_aliases["task-1234abcd"] = task.id

        self.assertEqual(
            self.server_mod._resolve_task_id(state, "task-1234abcd"),
            task.id,
        )
        self.assertEqual(
            self.server_mod._resolve_task_id(state, "LOOM:1"),
            task.id,
        )

    def test_resolve_agent_id_accepts_slug_name_and_prefix(self):
        state = self.state_mod.MatrixState()
        agent = self._make_agent("agent-1234abcd")
        agent.name = "Worker One"
        agent.slug = "worker-one"
        state.agents[agent.id] = agent

        self.assertEqual(
            self.server_mod._resolve_agent_id(state, "worker-one"),
            agent.id,
        )
        self.assertEqual(
            self.server_mod._resolve_agent_id(state, "worker one"),
            agent.id,
        )
        self.assertEqual(
            self.server_mod._resolve_agent_id(state, "agent-1234"),
            agent.id,
        )

    def test_resolve_memory_link_ref_defaults_from_context(self):
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Document the context flow",
            group="g",
            lane="In Progress",
            pipeline_root_id="root-1",
        )
        agent = self._make_agent("agent-1", current_task_id="task-1")

        self.assertEqual(
            self.server_mod._resolve_memory_link_ref(
                "task",
                "",
                cell=agent,
                task=task,
            ),
            "task-1",
        )
        self.assertEqual(
            self.server_mod._resolve_memory_link_ref(
                "pipeline",
                "",
                cell=agent,
                task=task,
            ),
            "root-1",
        )
        self.assertEqual(
            self.server_mod._resolve_memory_link_ref(
                "agent",
                "",
                cell=agent,
                task=task,
            ),
            "agent-1",
        )

    def test_apply_verification_report_marks_attempted(self):
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Deploy billing changes",
            group="g",
            lane="In Progress",
        )
        saved = []

        message, root = self.server_mod._apply_verification_report(
            task,
            {
                "verification_mode": "deploy",
                "deploy_attempted": True,
                "verification_notes": "Deployed to staging",
            },
            "loom-cli",
            lambda current: saved.append(current.id),
            timestamp="2026-04-07T18:00:00+00:00",
        )

        self.assertIsNone(root)
        self.assertEqual(task.verification_mode, "deploy")
        self.assertEqual(task.verification_state, "attempted")
        self.assertTrue(task.verification_summary["deploy_attempted"])
        self.assertEqual(task.verification_notes, "Deployed to staging")
        self.assertEqual(task.verification_updated_by, "loom-cli")
        self.assertEqual(task.verification_updated_at, "2026-04-07T18:00:00+00:00")
        self.assertEqual(saved, ["task-1"])
        self.assertIn("state=attempted", message)
        self.assertIn("deploy attempted", message)

    def test_apply_verification_report_marks_smoke_failure_and_updates_root(self):
        root_task = self.state_mod.BoardTask(
            id="root-1",
            task="Release billing changes",
            group="g",
            lane="In Progress",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Restart billing service",
            group="g",
            lane="In Progress",
            pipeline_root_id=root_task.id,
        )
        saved = []

        message, root = self.server_mod._apply_verification_report(
            task,
            {
                "verification_mode": "restart",
                "smoke_status": "failed",
                "verification_notes": "Smoke failed on login redirect",
            },
            "Weaver",
            lambda current: saved.append(current.id),
            root_task=root_task,
            timestamp="2026-04-07T18:05:00+00:00",
        )

        self.assertIs(root, root_task)
        self.assertEqual(task.verification_mode, "restart")
        self.assertEqual(task.verification_state, "failed")
        self.assertTrue(task.verification_summary["manual_smoke_done"])
        self.assertEqual(task.verification_updated_by, "Weaver")
        self.assertEqual(root_task.verification_state, "failed")
        self.assertEqual(root_task.verification_mode, "restart")
        self.assertTrue(root_task.verification_summary["manual_smoke_done"])
        self.assertEqual(root_task.verification_notes, "Smoke failed on login redirect")
        self.assertEqual(saved, ["task-1", "root-1"])
        self.assertIn("state=failed", message)
        self.assertIn("manual smoke done", message)

    def test_apply_verification_report_clears_pending_human_validation_when_passed(self):
        root_task = self.state_mod.BoardTask(
            id="root-1",
            task="Release billing changes",
            group="g",
            lane="Done",
            verification_mode="deploy",
            verification_state="pending",
            verification_summary={
                "deploy_needed": True,
                "human_validation_pending": "Confirm billing dashboard loads",
                "tests_run": "python3 -m unittest",
            },
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Deploy billing changes",
            group="g",
            lane="Done",
            pipeline_root_id=root_task.id,
            verification_mode="deploy",
            verification_state="pending",
            verification_summary={
                "deploy_needed": True,
                "human_validation_pending": "Confirm billing dashboard loads",
                "tests_run": "python3 -m unittest",
            },
        )
        saved = []

        message, root = self.server_mod._apply_verification_report(
            task,
            {
                "verification_state": "passed",
                "manual_smoke_done": True,
            },
            "Operator",
            lambda current: saved.append(current.id),
            root_task=root_task,
            timestamp="2026-04-11T18:10:00+00:00",
        )

        self.assertIs(root, root_task)
        self.assertEqual(task.verification_state, "passed")
        self.assertTrue(task.verification_summary["manual_smoke_done"])
        self.assertNotIn("human_validation_pending", task.verification_summary)
        self.assertNotIn("deploy_needed", task.verification_summary)
        self.assertEqual(root_task.verification_state, "passed")
        self.assertNotIn("human_validation_pending", root_task.verification_summary)
        self.assertNotIn("deploy_needed", root_task.verification_summary)
        self.assertEqual(saved, ["task-1", "root-1"])
        self.assertIn("state=passed", message)
        self.assertIn("manual smoke done", message)

    def test_loom_system_prompt_prefers_mcp_reporting_tools(self):
        prompt = self.server_prompts_mod.build_loom_system_prompt()

        self.assertIn("Use the Loom MCP tools", prompt)
        self.assertIn("loom_done(message=", prompt)
        self.assertIn("loom_verify(state=", prompt)
        self.assertIn("blocking human decision or approval", prompt)
        self.assertIn("For status updates, non-blocking observations, or optional", prompt)
        self.assertIn("loom_context()", prompt)
        self.assertEqual(
            prompt.count("`loom_ask(question=\"question\", description=\"details\")`"),
            1,
        )
        self.assertNotIn("loom_a-", prompt)

    def test_dispatch_postscript_clean_variant_is_compact_and_transition_aware(self):
        prompt = self.server_prompts_mod.build_dispatch_postscript(
            transitions=[
                {
                    "action": "feature/implement",
                    "when": "the user has reviewed and approved the plan",
                    "target": "self",
                },
                {
                    "ask": True,
                    "when": "you need clarification or approval",
                },
            ],
            is_clean=True,
        )

        self.assertIn("IMPORTANT: Finish this task by calling a Loom MCP tool below.", prompt)
        self.assertIn("Available completion paths for this task:", prompt)
        self.assertIn("loom_derive(description=", prompt)
        self.assertIn("continues in the same agent", prompt)
        self.assertIn("loom_ask(question=\"title\", description=\"details\")", prompt)
        self.assertIn("loom_done(message=\"brief summary\")", prompt)
        self.assertIn("Other reporting tools when relevant:", prompt)
        self.assertIn("loom_blocked(reason=", prompt)
        self.assertIn("loom_error(message=", prompt)
        self.assertIn("loom_verify(state=", prompt)
        self.assertNotIn("Report your progress with these Loom MCP tools", prompt)
        self.assertNotIn("loom_ready()", prompt)
        self.assertNotIn("loom_progress(message=", prompt)

    def test_dispatch_postscript_abbreviated_variant_keeps_shared_fallback_tools(self):
        prompt = self.server_prompts_mod.build_dispatch_postscript(
            transitions=[
                {
                    "action": "feature/implement",
                    "when": "issues were found that need to be fixed",
                },
            ],
            is_clean=False,
            pipeline_context=(
                "This task is part of a pipeline (depth 1/∞).\n"
                "Parent task: \"Review the auth refactor\" (In Progress)"
            ),
            commit_hint="Before reporting done, commit all your changes.",
        )

        self.assertTrue(prompt.startswith("\n\n---\n"))
        self.assertIn("Available completion paths for this task:", prompt)
        self.assertIn("issues were found that need to be fixed", prompt)
        self.assertIn("loom_done(message=\"brief summary\")", prompt)
        self.assertIn("Other reporting tools when relevant:", prompt)
        self.assertIn("loom_blocked(reason=", prompt)
        self.assertIn("loom_error(message=", prompt)
        self.assertIn("loom_verify(state=", prompt)
        self.assertIn("This task is part of a pipeline", prompt)
        self.assertIn("Before reporting done, commit all your changes.", prompt)
        self.assertNotIn("loom_ask(question=\"title\", description=\"details\")", prompt)
        self.assertNotIn("loom_ready()", prompt)

    def test_dispatch_postscript_without_transitions_stays_compact(self):
        prompt = self.server_prompts_mod.build_dispatch_postscript(
            transitions=[],
            is_clean=True,
        )

        self.assertIn("Available completion paths for this task:", prompt)
        self.assertIn("loom_done(message=\"brief summary\")", prompt)
        self.assertIn("Other reporting tools when relevant:", prompt)
        self.assertIn("loom_blocked(reason=", prompt)
        self.assertIn("loom_error(message=", prompt)
        self.assertIn("loom_verify(state=", prompt)
        self.assertNotIn("IMPORTANT:", prompt)
        self.assertNotIn("loom_derive(description=", prompt)
        self.assertNotIn("loom_ask(question=\"title\", description=\"details\")", prompt)

    def test_startup_prompt_for_new_codex_worker_uses_persistent_prompt(self):
        self.assertEqual(
            self.server_mod._startup_prompt_for_new_agent(
                agent_type="codex",
                persistent_prompt_text="Persistent worker prompt",
            ),
            "Persistent worker prompt",
        )

    def test_startup_prompt_for_new_claude_worker_does_not_duplicate_persistent_prompt(self):
        self.assertEqual(
            self.server_mod._startup_prompt_for_new_agent(
                agent_type="claude-code",
                persistent_prompt_text="Persistent worker prompt",
            ),
            "",
        )

    def test_self_dispatch_bypasses_busy_agent_queue(self):
        active = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
        )

        self.assertTrue(
            self.server_mod._should_queue_existing_agent_dispatch(
                active,
                target_task_id="task-2",
                self_dispatch=False,
            )
        )
        self.assertFalse(
            self.server_mod._should_queue_existing_agent_dispatch(
                active,
                target_task_id="task-2",
                self_dispatch=True,
            )
        )

    def test_existing_agent_dispatch_detects_active_shared_worktree_owner(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent("agent-1", current_task_id="task-1")
        target = self._make_agent("agent-2")
        state.agents = {owner.id: owner, target.id: target}
        state.board_tasks["task-1"] = self.state_mod.BoardTask(
            id="task-1",
            task="Active task",
            group="g",
            lane="In Progress",
            agent_id=owner.id,
        )

        active_owner = self.server_mod._find_active_worktree_owner(
            state, target
        )

        self.assertIs(active_owner, owner)
        self.assertFalse(
            self.server_mod._should_handoff_shared_worktree(
                active_owner,
                target_agent_id=target.id,
                handoff_from="",
            )
        )

    def test_self_dispatch_keeps_shared_worktree_owner(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent("agent-1", current_task_id="task-1")
        state.agents = {owner.id: owner}

        active_owner = self.server_mod._find_active_worktree_owner(
            state, owner
        )

        self.assertIsNone(active_owner)

    def test_review_handoff_allows_shared_worktree_reuse(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent("impl-1", current_task_id="task-impl")
        reviewer = self._make_agent("review-1")
        state.agents = {owner.id: owner, reviewer.id: reviewer}
        state.board_tasks["task-impl"] = self.state_mod.BoardTask(
            id="task-impl",
            task="Implementation",
            group="g",
            lane="In Progress",
            agent_id=owner.id,
        )

        active_owner = self.server_mod._find_active_worktree_owner(
            state, reviewer
        )

        self.assertIs(active_owner, owner)
        self.assertTrue(
            self.server_mod._should_handoff_shared_worktree(
                active_owner,
                target_agent_id=reviewer.id,
                handoff_from=owner.id,
            )
        )

    def test_suspended_parent_does_not_claim_shared_worktree_ownership(self):
        state = self.state_mod.MatrixState()
        parent = self.state_mod.BoardTask(
            id="task-parent",
            task="Implement feature",
            group="g",
            lane="In Progress",
            agent_id="impl-1",
        )
        review = self.state_mod.BoardTask(
            id="task-review",
            task="Review feature",
            group="g",
            lane="In Progress",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
            agent_id="review-1",
        )
        owner = self._make_agent("impl-1", current_task_id="task-parent")
        target = self._make_agent("impl-2")
        state.agents = {owner.id: owner, target.id: target}
        state.board_tasks = {parent.id: parent, review.id: review}

        active_owner = self.server_mod._find_active_worktree_owner(
            state, target
        )

        self.assertIsNone(active_owner)

    def test_concurrent_dispatch_detects_shared_branch_context(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent(
            "agent-1",
            current_task_id="task-1",
            worktree_path="/repo/.loom/worktrees/one",
            worktree_branch="loom/shared-branch",
        )
        target = self._make_agent(
            "agent-2",
            worktree_path="/repo/.loom/worktrees/two",
            worktree_branch="loom/shared-branch",
        )
        state.agents = {owner.id: owner, target.id: target}
        state.board_tasks["task-1"] = self.state_mod.BoardTask(
            id="task-1",
            task="Active task",
            group="g",
            lane="In Progress",
            agent_id=owner.id,
        )

        active_owner = self.server_mod._find_active_worktree_owner(
            state, target
        )

        self.assertIs(active_owner, owner)

    def test_relaunch_is_blocked_while_other_agent_owns_shared_worktree(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent("agent-1", current_task_id="task-1")
        stopped = self._make_agent("agent-2")
        stopped.status = "stopped"
        state.agents = {owner.id: owner, stopped.id: stopped}
        state.board_tasks["task-1"] = self.state_mod.BoardTask(
            id="task-1",
            task="Active task",
            group="g",
            lane="In Progress",
            agent_id=owner.id,
        )

        active_owner = self.server_mod._find_active_worktree_owner(
            state, stopped
        )

        self.assertIs(active_owner, owner)

    def test_promote_task_for_active_report_moves_task_into_dispatch_lane(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress"
        )
        cell = self._make_agent("agent-1")
        state.agents[cell.id] = cell
        task = state.board_add_task(
            "Queued follow-up",
            "g",
            lane="To Do",
            id="task-1",
            agent_id=cell.id,
        )

        self.server_mod._promote_task_for_active_report(state, cell, task)

        self.assertEqual(state.board_tasks["task-1"].lane, "In Progress")
        self.assertEqual(cell.current_task_id, "task-1")

    def test_nearest_ancestor_agent_for_action_stage_reuses_prior_reviewer(self):
        state = self.state_mod.MatrixState()
        impl = self._make_agent("impl-1")
        reviewer = self._make_agent("review-1")
        impl.session_id = "impl-session"
        impl.status = "running"
        reviewer.session_id = "review-session"
        reviewer.status = "idle"
        state.agents = {impl.id: impl, reviewer.id: reviewer}
        root = self.state_mod.BoardTask(
            id="task-root",
            task="Implement feature",
            group="g",
            lane="In Progress",
            action_name="feature/implement",
            agent_id=impl.id,
        )
        review = self.state_mod.BoardTask(
            id="task-review",
            task="Review feature",
            group="g",
            lane="In Progress",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        fix = self.state_mod.BoardTask(
            id="task-fix",
            task="Fix review issues",
            group="g",
            lane="In Progress",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=impl.id,
        )
        state.board_tasks = {root.id: root, review.id: review, fix.id: fix}

        reused = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, fix, "feature/review"
        )
        first_pass = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, root, "feature/review"
        )

        self.assertIs(reused, reviewer)
        self.assertIsNone(first_pass)

    def test_nearest_ancestor_agent_for_action_stage_skips_dead_reviewer(self):
        state = self.state_mod.MatrixState()
        impl = self._make_agent("impl-1")
        reviewer = self._make_agent("review-1")
        impl.session_id = "impl-session"
        impl.status = "running"
        reviewer.status = "stopped"
        reviewer.session_id = ""
        state.agents = {impl.id: impl, reviewer.id: reviewer}
        root = self.state_mod.BoardTask(
            id="task-root",
            task="Implement feature",
            group="g",
            lane="In Progress",
            action_name="feature/implement",
            agent_id=impl.id,
        )
        review = self.state_mod.BoardTask(
            id="task-review",
            task="Review feature",
            group="g",
            lane="Done",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        fix = self.state_mod.BoardTask(
            id="task-fix",
            task="Fix review issues",
            group="g",
            lane="In Progress",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=impl.id,
        )
        state.board_tasks = {root.id: root, review.id: review, fix.id: fix}

        reused = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, fix, "feature/review"
        )

        self.assertIsNone(reused)

    def test_resolve_ai_report_task_prefers_live_child_over_stale_parent(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            agent_id="agent-1",
        )
        child = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="task-fix",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            agent_id="agent-1",
        )
        cell = self._make_agent("agent-1", current_task_id=parent.id)
        state.agents[cell.id] = cell

        resolved = self.server_mod._resolve_ai_report_task(state, cell)

        self.assertIs(resolved, child)
        self.assertEqual(state.agent_current_task(cell.id).id, child.id)

    def test_derive_handoff_accepted_requires_explicit_success_result(self):
        self.assertFalse(self.server_mod._derive_handoff_accepted(None))
        self.assertFalse(self.server_mod._derive_handoff_accepted({
            "type": "dispatch_action_missing",
        }))
        self.assertTrue(self.server_mod._derive_handoff_accepted({
            "type": "ok",
        }))
        self.assertTrue(self.server_mod._derive_handoff_accepted({
            "type": "queued",
        }))

    def test_reject_completion_with_open_descendants_blocks_manual_done(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        task = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
        )
        child = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="task-fix",
            parent_task_id="task-review",
            pipeline_root_id="task-review",
            pipeline_depth=1,
        )

        rejected = self.server_mod._reject_completion_with_open_descendants(
            state, task, "done"
        )
        state.board_move_task(child.id, "Done")
        allowed = self.server_mod._reject_completion_with_open_descendants(
            state, task, "done"
        )

        self.assertEqual(rejected["type"], "error")
        self.assertIn("still unresolved", rejected["message"])
        self.assertIsNone(allowed)


class ServerAutoCloseOnDoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def _write_action(self, base_dir, name, *,
                      auto_close_on_done=False) -> None:
        import os

        action_path = os.path.join(base_dir, ".loom", "actions",
                                   name + ".yaml")
        os.makedirs(os.path.dirname(action_path), exist_ok=True)
        with open(action_path, "w", encoding="utf-8") as handle:
            handle.write(f"name: {name}\n")
            if auto_close_on_done:
                handle.write("auto_close_on_done: true\n")
            handle.write("prompt: |\n")
            handle.write("  {{ TASK }}\n")

    async def test_auto_close_on_done_closes_opt_in_reviewers_after_root_finishes(self):
        state = self._make_state()
        impl = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
        )
        reviewer_one = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer One",
            group="g",
            cell_type="agent",
            session_id="review-1-session",
            status="idle",
        )
        reviewer_two = self.state_mod.AgentCell(
            id="review-2",
            name="Reviewer Two",
            group="g",
            cell_type="agent",
            session_id="review-2-session",
            status="idle",
        )
        state.agents = {
            impl.id: impl,
            reviewer_one.id: reviewer_one,
            reviewer_two.id: reviewer_two,
        }
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="Done",
            id="task-root",
            action_name="feature/implement",
            agent_id=impl.id,
        )
        review_one = state.board_add_task(
            "Initial review",
            "g",
            lane="Done",
            id="task-review-1",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer_one.id,
        )
        fix = state.board_add_task(
            "Fix issues",
            "g",
            lane="Done",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=review_one.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=impl.id,
        )
        re_review = state.board_add_task(
            "Re-review after fixes",
            "g",
            lane="Done",
            id="task-review-2",
            action_name="feature/review",
            parent_task_id=fix.id,
            pipeline_root_id=root.id,
            pipeline_depth=3,
            agent_id=reviewer_two.id,
        )

        with tempfile.TemporaryDirectory() as tempdir:
            self._write_action(tempdir, "feature/implement")
            self._write_action(
                tempdir,
                "feature/review",
                auto_close_on_done=True,
            )
            closed = []

            async def resolve_base_dir(_group):
                return tempdir

            async def close_agent(cell):
                closed.append(cell.id)

            result = await self.server_mod._maybe_auto_close_root_done_agents(
                state,
                re_review,
                action_mgr=self.server_mod.ActionManager(),
                resolve_base_dir=resolve_base_dir,
                close_agent=close_agent,
            )

        self.assertEqual(result, [reviewer_one.id, reviewer_two.id])
        self.assertEqual(closed, [reviewer_one.id, reviewer_two.id])

    async def test_auto_close_on_done_waits_for_root_resolution(self):
        state = self._make_state()
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            status="idle",
        )
        impl = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
        )
        state.agents = {reviewer.id: reviewer, impl.id: impl}
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=impl.id,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        state.board_add_task(
            "Fix issues",
            "g",
            lane="In Progress",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=impl.id,
        )

        with tempfile.TemporaryDirectory() as tempdir:
            self._write_action(tempdir, "feature/implement")
            self._write_action(
                tempdir,
                "feature/review",
                auto_close_on_done=True,
            )
            closed = []

            async def resolve_base_dir(_group):
                return tempdir

            async def close_agent(cell):
                closed.append(cell.id)

            result = await self.server_mod._maybe_auto_close_root_done_agents(
                state,
                review,
                action_mgr=self.server_mod.ActionManager(),
                resolve_base_dir=resolve_base_dir,
                close_agent=close_agent,
            )

        self.assertEqual(result, [])
        self.assertEqual(closed, [])

    async def test_auto_close_on_done_skips_same_agent_queue_and_weaver_followups(self):
        state = self._make_state()
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            status="idle",
        )
        state.agents = {reviewer.id: reviewer}
        root = state.board_add_task(
            "Review root",
            "g",
            lane="Done",
            id="task-root",
            action_name="feature/review",
            agent_id=reviewer.id,
        )
        queued = state.board_add_task(
            "Queued follow-up",
            "g",
            lane="Backlog",
            id="task-queued",
            action_name="feature/implement",
        )
        state.auto_dispatch_queue_add(
            "g",
            queued.id,
            target_agent_id=reviewer.id,
        )
        state.board_add_task(
            "Weaver: need reply",
            "g",
            lane="Backlog",
            id="task-reply",
            reply_agent_id=reviewer.id,
            labels=["loom:weaver-message"],
            status="Awaiting Reply",
        )

        with tempfile.TemporaryDirectory() as tempdir:
            self._write_action(
                tempdir,
                "feature/review",
                auto_close_on_done=True,
            )
            self._write_action(tempdir, "feature/implement")
            closed = []

            async def resolve_base_dir(_group):
                return tempdir

            async def close_agent(cell):
                closed.append(cell.id)

            result = await self.server_mod._maybe_auto_close_root_done_agents(
                state,
                root,
                action_mgr=self.server_mod.ActionManager(),
                resolve_base_dir=resolve_base_dir,
                close_agent=close_agent,
            )

        self.assertEqual(result, [])
        self.assertEqual(closed, [])

    async def test_auto_close_on_done_skips_agents_with_open_assigned_tasks(self):
        state = self._make_state()
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            status="idle",
        )
        state.agents = {reviewer.id: reviewer}
        root = state.board_add_task(
            "Review root",
            "g",
            lane="Done",
            id="task-root",
            action_name="feature/review",
            agent_id=reviewer.id,
        )
        state.board_add_task(
            "Queued follow-up",
            "g",
            lane="To Do",
            id="task-next",
            action_name="feature/implement",
            agent_id=reviewer.id,
        )

        with tempfile.TemporaryDirectory() as tempdir:
            self._write_action(
                tempdir,
                "feature/review",
                auto_close_on_done=True,
            )
            self._write_action(tempdir, "feature/implement")
            closed = []

            async def resolve_base_dir(_group):
                return tempdir

            async def close_agent(cell):
                closed.append(cell.id)

            result = await self.server_mod._maybe_auto_close_root_done_agents(
                state,
                root,
                action_mgr=self.server_mod.ActionManager(),
                resolve_base_dir=resolve_base_dir,
                close_agent=close_agent,
            )

        self.assertEqual(result, [])
        self.assertEqual(closed, [])


class ServerReviewMergeCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self, *, verdict_message):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        impl = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            worktree_path="/repo/.loom/worktrees/impl",
            worktree_branch="loom/impl",
            worktree_repo_root="/repo",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            worktree_path="/repo/.loom/worktrees/review",
            worktree_branch="loom/review",
            worktree_repo_root="/repo",
        )
        state.agents = {impl.id: impl, reviewer.id: reviewer}
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="Done",
            id="task-root",
            action_name="feature/implement",
            agent_id=impl.id,
        )
        state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
            messages=[
                {
                    "action": "done",
                    "message": verdict_message,
                    "agent_name": reviewer.name,
                }
            ],
        )
        return state, impl, reviewer

    async def test_ship_review_cleanup_fires_after_parent_merge(self):
        state, impl, reviewer = self._make_state(
            verdict_message=(
                "Verification summary: tests passed\n\n"
                "## Verdict\n"
                "**Ship** — no issues, ready to merge"
            )
        )
        calls = []

        async def cleanup(cell, *, close_agent, remove_worktree):
            calls.append((cell.id, close_agent, remove_worktree))
            return {
                "agent_closed": True,
                "worktree_removed": True,
                "errors": [],
            }

        summary = await self.server_mod._cleanup_shipped_reviewers_for_merged_cell(
            state,
            impl,
            cleanup,
        )

        self.assertEqual(calls, [(reviewer.id, True, True)])
        self.assertEqual(summary["agents"], [reviewer.id])
        self.assertEqual(summary["agent_closed"], 1)
        self.assertEqual(summary["worktree_removed"], 1)

    async def test_non_ship_review_cleanup_defers(self):
        for verdict in (
            "## Verdict\n**Ship with fixes** — address minor issues first",
            "Verdict: Needs rework — core behavior is incorrect",
        ):
            with self.subTest(verdict=verdict):
                state, impl, _reviewer = self._make_state(
                    verdict_message=verdict,
                )
                calls = []

                async def cleanup(cell, *, close_agent, remove_worktree):
                    calls.append(cell.id)
                    return {
                        "agent_closed": True,
                        "worktree_removed": True,
                        "errors": [],
                    }

                summary = await self.server_mod._cleanup_shipped_reviewers_for_merged_cell(
                    state,
                    impl,
                    cleanup,
                )

                self.assertEqual(calls, [])
                self.assertEqual(summary["agents"], [])

    async def test_review_cleanup_skips_implementer_agent(self):
        state, impl, _reviewer = self._make_state(
            verdict_message="Verdict: Ship",
        )
        review_task = state.board_tasks["task-review"]
        review_task.agent_id = impl.id
        calls = []

        async def cleanup(cell, *, close_agent, remove_worktree):
            calls.append(cell.id)
            return {
                "agent_closed": True,
                "worktree_removed": True,
                "errors": [],
            }

        summary = await self.server_mod._cleanup_shipped_reviewers_for_merged_cell(
            state,
            impl,
            cleanup,
        )

        self.assertEqual(calls, [])
        self.assertEqual(summary["agents"], [])


class ServerAutoDispatchQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        state.agents[weaver.id] = weaver
        state.groups["g"] = [weaver.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        return state

    async def test_pump_auto_dispatch_queue_dispatches_queued_task(self):
        state = self._make_state()
        task = state.board_add_task("Queued task", "g", id="task-1")
        self.assertIsNotNone(task)
        state.auto_dispatch_queue_add("g", "task-1", max_concurrent=1)

        panel_events = []

        async def handle_command(payload):
            self.assertEqual(payload["cmd"], "dispatch_task")
            queued_task = state.board_tasks[payload["id"]]
            agent = self.state_mod.AgentCell(
                id="agent-1",
                name="worker",
                group="g",
                cell_type="agent",
                current_task_id=queued_task.id,
            )
            state.agents[agent.id] = agent
            state.groups["g"].append(agent.id)
            queued_task.agent_id = agent.id
            queued_task.lane = "In Progress"
            return None

        def panel_event(kind, cell_id, agent_name, group, message, task_id=""):
            panel_events.append((kind, cell_id, agent_name, group, message,
                                 task_id))

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            panel_event,
            group="g",
        )

        self.assertEqual(dispatched, [{
            "group": "g",
            "task_id": "task-1",
            "agent_id": "agent-1",
        }])
        self.assertEqual(state.board_tasks["task-1"].agent_id, "agent-1")
        self.assertEqual(state.board_tasks["task-1"].lane, "In Progress")
        self.assertNotIn("g", state.auto_dispatch_queues)
        self.assertEqual(panel_events[0][0], "task_auto_dispatched")

    async def test_pump_auto_dispatch_queue_forwards_weaver_owner_on_new_agents(self):
        state = self._make_state()
        task = state.board_add_task("Queued task", "g", id="task-1")
        self.assertIsNotNone(task)
        state.auto_dispatch_queue_add(
            "g",
            "task-1",
            max_concurrent=1,
            weaver_owner_id="weaver-1",
        )

        async def handle_command(payload):
            self.assertEqual(payload["cmd"], "dispatch_task")
            self.assertTrue(payload["create_agent"])
            self.assertEqual(payload["_created_by_weaver_id"], "weaver-1")
            queued_task = state.board_tasks[payload["id"]]
            agent = self.state_mod.AgentCell(
                id="agent-1",
                name="worker",
                group="g",
                cell_type="agent",
                current_task_id=queued_task.id,
                created_by_weaver_id=payload["_created_by_weaver_id"],
            )
            state.agents[agent.id] = agent
            state.groups["g"].append(agent.id)
            queued_task.agent_id = agent.id
            queued_task.lane = "In Progress"
            return None

        await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(
            state.agents["agent-1"].created_by_weaver_id,
            "weaver-1",
        )

    async def test_pump_auto_dispatch_queue_binds_agent_group_followups(self):
        state = self._make_state()
        first = state.board_add_task("First", "g", id="task-1")
        second = state.board_add_task("Second", "g", id="task-2")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        state.auto_dispatch_queue_add(
            "g", "task-1", agent_group="followup", max_concurrent=1
        )
        state.auto_dispatch_queue_add(
            "g", "task-2", agent_group="followup", max_concurrent=1
        )

        created = {"done": False}

        async def handle_command(payload):
            task = state.board_tasks[payload["id"]]
            if payload.get("create_agent"):
                created["done"] = True
                agent = self.state_mod.AgentCell(
                    id="agent-1",
                    name="worker",
                    group="g",
                    cell_type="agent",
                    current_task_id=task.id,
                )
                state.agents[agent.id] = agent
                state.groups["g"].append(agent.id)
                task.agent_id = agent.id
                task.lane = "In Progress"
                return None

            self.assertTrue(created["done"])
            self.assertEqual(payload["agent_id"], "agent-1")
            task.agent_id = "agent-1"
            task.lane = "To Do"
            return {
                "type": "queued",
                "task_id": task.id,
                "agent_id": "agent-1",
            }

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(
            [item["task_id"] for item in dispatched],
            ["task-1", "task-2"],
        )
        self.assertEqual(state.board_tasks["task-1"].agent_id, "agent-1")
        self.assertEqual(state.board_tasks["task-2"].agent_id, "agent-1")
        self.assertEqual(state.board_tasks["task-2"].lane, "To Do")
        self.assertNotIn("g", state.auto_dispatch_queues)

    async def test_pump_keeps_entry_when_task_agent_id_matches_target(self):
        """A task pre-bound to its target agent must survive the pump.

        Regression: direct-dispatch to a busy agent sets ``task.agent_id``
        up front and enqueues the follow-up. The pump must not treat the
        pre-bound agent as evidence of stale queue state.
        """
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            session_id="sess-1",
            current_task_id="task-active",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        active = state.board_add_task(
            "Active",
            "g",
            lane="In Progress",
            id="task-active",
            agent_id=worker.id,
        )
        queued = state.board_add_task(
            "Queued for same agent",
            "g",
            lane="To Do",
            id="task-queued",
            agent_id=worker.id,
        )
        self.assertIsNotNone(active)
        self.assertIsNotNone(queued)
        state.auto_dispatch_queue_add(
            "g",
            queued.id,
            target_agent_id=worker.id,
            max_concurrent=2,
        )

        # Pre-existing pump check would remove this entry because
        # task.agent_id is set. Verify the loosened check keeps it when
        # the agent matches the entry's target.
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "queued", "task_id": payload["id"],
                    "agent_id": payload.get("agent_id", "")}

        await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["cmd"], "dispatch_task")
        self.assertEqual(calls[0]["id"], "task-queued")
        self.assertEqual(calls[0]["agent_id"], worker.id)

    async def test_pump_removes_entry_when_task_agent_id_differs_from_target(self):
        """Stale entries (task reassigned to another agent) must be purged."""
        state = self._make_state()
        first = self.state_mod.AgentCell(
            id="agent-1", name="A", group="g", cell_type="agent",
        )
        second = self.state_mod.AgentCell(
            id="agent-2", name="B", group="g", cell_type="agent",
        )
        state.agents[first.id] = first
        state.agents[second.id] = second
        state.groups["g"].extend([first.id, second.id])

        task = state.board_add_task(
            "Reassigned",
            "g",
            lane="To Do",
            id="task-1",
            agent_id=second.id,  # actually assigned to agent-2
        )
        self.assertIsNotNone(task)
        state.auto_dispatch_queue_add(
            "g",
            task.id,
            target_agent_id=first.id,  # queue says target is agent-1
            max_concurrent=1,
        )

        async def handle_command(payload):
            raise AssertionError("handle_command should not be called")

        await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertNotIn("g", state.auto_dispatch_queues)

    def test_cleanup_preserves_entries_matching_target_agent(self):
        """cleanup_stale must keep entries where task.agent_id == target."""
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id="agent-1", name="Worker", group="g", cell_type="agent",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        kept = state.board_add_task(
            "Queued for target",
            "g",
            lane="To Do",
            id="task-kept",
            agent_id=worker.id,
        )
        stale = state.board_add_task(
            "Stale",
            "g",
            lane="To Do",
            id="task-stale",
            agent_id="agent-other",  # assigned elsewhere
        )
        self.assertIsNotNone(kept)
        self.assertIsNotNone(stale)
        state.auto_dispatch_queue_add(
            "g", kept.id, target_agent_id=worker.id, max_concurrent=1,
        )
        state.auto_dispatch_queue_add(
            "g", stale.id, target_agent_id=worker.id, max_concurrent=1,
        )

        removed = state.cleanup_stale_auto_dispatch_queue()

        self.assertEqual(removed, 1)
        remaining = state.auto_dispatch_queues.get("g", [])
        self.assertEqual([entry.task_id for entry in remaining], ["task-kept"])

    async def test_pump_forever_survives_cycle_exceptions(self):
        """The persistent pump must not die on a per-cycle failure."""
        state = self._make_state()
        cycles = []

        async def boom(*args, **kwargs):
            cycles.append("cycle")
            if len(cycles) == 1:
                raise RuntimeError("boom")
            return []

        slept = []

        async def fake_sleep(delay):
            slept.append(delay)
            if len(slept) >= 2:
                raise asyncio.CancelledError()

        server_dispatch = importlib.import_module("loom.server_dispatch")
        orig_pump = server_dispatch._pump_auto_dispatch_queue
        orig_sleep = server_dispatch.asyncio.sleep
        server_dispatch._pump_auto_dispatch_queue = boom
        server_dispatch.asyncio.sleep = fake_sleep
        try:
            with self.assertRaises(asyncio.CancelledError):
                await server_dispatch._pump_auto_dispatch_queue_forever(
                    state, lambda *a, **kw: None, lambda *a, **kw: None,
                    interval=0.01,
                )
        finally:
            server_dispatch._pump_auto_dispatch_queue = orig_pump
            server_dispatch.asyncio.sleep = orig_sleep

        # First cycle raised; second cycle ran cleanly; both slept.
        self.assertEqual(len(cycles), 2)
        self.assertEqual(len(slept), 2)

    async def test_new_agent_prompt_sequence_preserves_old_codex_order(self):
        prompts = self.server_mod._new_agent_prompt_sequence(
            {
                "initial_prompt": "Template intro",
            },
            startup_prompt="Persistent worker prompt",
            final_prompt="Dispatch task body",
        )

        self.assertEqual(
            prompts,
            [
                ("Persistent worker prompt", {}),
                ("Template intro", {}),
                ("Dispatch task body", {"background": True}),
            ],
        )

    async def test_stream_auto_resume_dispatches_when_queue_head_becomes_ready(self):
        state = self._make_state()
        owner = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker",
        )
        state.agents[owner.id] = owner
        state.groups["g"].append(owner.id)

        product = state.board_add_task(
            "Add Events tab",
            "g",
            lane="Done",
            id="LOOM:1",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="In Progress",
            id="LOOM:1:1",
            parent_task_id="LOOM:1",
            pipeline_root_id="LOOM:1",
            pipeline_depth=1,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="LOOM:2",
            agent_id=owner.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="LOOM:1",
        )
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "loom/worker",
            "status": "open",
            "recorded_at": "2026-04-07T11:00:00+00:00",
            "commit_sha": "impl123",
            "recorded_by_agent_id": owner.id,
        }
        targets = self.server_mod._capture_auto_resume_targets(
            state,
            group="g",
            task=review,
        )
        state.board_move_task(review.id, "Done")

        calls = []

        async def handle_command(payload):
            calls.append(payload)
            queued_task = state.board_tasks[payload["id"]]
            queued_task.lane = "In Progress"
            queued_task.agent_id = payload["agent_id"]
            owner.current_task_id = queued_task.id
            return {
                "type": "ok",
                "task_id": queued_task.id,
                "agent_id": payload["agent_id"],
            }

        results = await self.server_mod._maybe_auto_resume_targets(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            targets=targets,
            group="g",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["cmd"], "dispatch_task")
        self.assertEqual(calls[0]["id"], queued.id)
        self.assertEqual(calls[0]["agent_id"], owner.id)
        self.assertEqual(results[0]["type"], "stream_auto_resumed")

    async def test_stream_auto_resume_respects_suggest_only_mode(self):
        state = self._make_state()
        state.update_weaver_settings("g", autonomy_mode="suggest_only")
        owner = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker",
        )
        state.agents[owner.id] = owner
        state.groups["g"].append(owner.id)

        product = state.board_add_task(
            "Add Events tab",
            "g",
            lane="Done",
            id="LOOM:1",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="In Progress",
            id="LOOM:1:1",
            parent_task_id="LOOM:1",
            pipeline_root_id="LOOM:1",
            pipeline_depth=1,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="LOOM:2",
            agent_id=owner.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="LOOM:1",
        )
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "loom/worker",
            "status": "open",
            "recorded_at": "2026-04-07T11:00:00+00:00",
            "commit_sha": "impl123",
            "recorded_by_agent_id": owner.id,
        }
        targets = self.server_mod._capture_auto_resume_targets(
            state,
            group="g",
            task=review,
        )
        state.board_move_task(review.id, "Done")

        async def handle_command(payload):
            self.fail(f"Unexpected auto-resume dispatch: {payload}")

        results = await self.server_mod._maybe_auto_resume_targets(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            targets=targets,
            group="g",
        )

        self.assertEqual(results, [])

    async def test_auto_resume_targets_include_dependent_streams_unblocked_by_external_task(self):
        state = self._make_state()
        owner = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker",
        )
        state.agents[owner.id] = owner
        state.groups["g"].append(owner.id)

        external = state.board_add_task(
            "External prerequisite",
            "g",
            lane="In Progress",
            id="EXT:1",
            action_name="oneshot/fix",
        )
        product = state.board_add_task(
            "Add Events tab",
            "g",
            lane="Done",
            id="LOOM:1",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="Done",
            id="LOOM:1:1",
            parent_task_id="LOOM:1",
            pipeline_root_id="LOOM:1",
            pipeline_depth=1,
            agent_id=owner.id,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="LOOM:2",
            agent_id=owner.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="LOOM:1:1",
            depends_on=["EXT:1"],
        )
        self.assertIsNotNone(external)
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "loom/worker",
            "status": "open",
            "recorded_at": "2026-04-07T10:00:00+00:00",
            "commit_sha": "impl123",
            "recorded_by_agent_id": owner.id,
        }
        review.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "loom/worker",
            "status": "open",
            "recorded_at": "2026-04-07T11:00:00+00:00",
            "commit_sha": "rev456",
            "recorded_by_agent_id": owner.id,
        }

        targets = self.server_mod._capture_auto_resume_targets(
            state,
            task=external,
            group="g",
        )
        self.assertEqual([item["task_id"] for item in targets], [queued.id])

        state.board_move_task(external.id, "Done")

        calls = []

        async def handle_command(payload):
            calls.append(payload)
            queued_task = state.board_tasks[payload["id"]]
            queued_task.lane = "In Progress"
            queued_task.agent_id = payload["agent_id"]
            owner.current_task_id = queued_task.id
            return {
                "type": "ok",
                "task_id": queued_task.id,
                "agent_id": payload["agent_id"],
            }

        results = await self.server_mod._maybe_auto_resume_targets(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            targets=targets,
            group="g",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], queued.id)
        self.assertEqual(calls[0]["agent_id"], owner.id)
        self.assertEqual(results[0]["type"], "stream_auto_resumed")

    async def test_auto_resume_targets_capture_cross_group_dependents(self):
        state = self._make_state()
        weaver_b = self.state_mod.AgentCell(
            id="weaver-b",
            name="Weaver B",
            group="B",
            cell_type="agent",
        )
        owner_b = self.state_mod.AgentCell(
            id="agent-b",
            name="Worker B",
            slug="worker-b",
            group="B",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/agent-b",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker-b",
        )
        state.agents[weaver_b.id] = weaver_b
        state.agents[owner_b.id] = owner_b
        state.groups["B"] = [weaver_b.id, owner_b.id]
        state.group_settings["B"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver_b.id
        )

        external = state.board_add_task(
            "External prerequisite",
            "g",
            lane="In Progress",
            id="EXT:1",
            action_name="oneshot/fix",
        )
        product = state.board_add_task(
            "Add Events tab",
            "B",
            lane="Done",
            id="LOOM:1",
            agent_id=owner_b.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "B",
            lane="Done",
            id="LOOM:1:1",
            parent_task_id="LOOM:1",
            pipeline_root_id="LOOM:1",
            pipeline_depth=1,
            agent_id=owner_b.id,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "B",
            lane="To Do",
            id="LOOM:2",
            agent_id=owner_b.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="LOOM:1:1",
            depends_on=["EXT:1"],
        )
        self.assertIsNotNone(external)
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "loom/worker-b",
            "status": "open",
            "recorded_at": "2026-04-07T10:00:00+00:00",
            "commit_sha": "impl123",
            "recorded_by_agent_id": owner_b.id,
        }
        review.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "loom/worker-b",
            "status": "open",
            "recorded_at": "2026-04-07T11:00:00+00:00",
            "commit_sha": "rev456",
            "recorded_by_agent_id": owner_b.id,
        }

        targets = self.server_mod._capture_auto_resume_targets(
            state,
            task=external,
            group="g",
        )

        self.assertEqual([item["task_id"] for item in targets], [queued.id])
        self.assertEqual([item["group"] for item in targets], ["B"])

        state.board_move_task(external.id, "Done")

        calls = []

        async def handle_command(payload):
            calls.append(payload)
            queued_task = state.board_tasks[payload["id"]]
            queued_task.lane = "In Progress"
            queued_task.agent_id = payload["agent_id"]
            owner_b.current_task_id = queued_task.id
            return {
                "type": "ok",
                "task_id": queued_task.id,
                "agent_id": payload["agent_id"],
            }

        results = await self.server_mod._maybe_auto_resume_targets(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            targets=targets,
            group="g",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], queued.id)
        self.assertEqual(calls[0]["agent_id"], owner_b.id)
        self.assertEqual(results[0]["type"], "stream_auto_resumed")

    def test_find_reusable_review_fix_task_reuses_open_fix_review_shell(self):
        state = self._make_state()
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="In Progress",
            id="LOOM:1:1",
            action_name="feature/review",
        )
        fix_task = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="LOOM:1:2",
            parent_task_id="LOOM:1:1",
            pipeline_root_id="LOOM:1",
            pipeline_depth=1,
            action_name="feature/fix-review",
            labels=["loom:derived", "review-fix"],
        )
        self.assertIsNotNone(review)
        self.assertIsNotNone(fix_task)

        reusable = self.server_mod._find_reusable_review_fix_task(
            state,
            review,
            "feature/fix-review",
        )

        self.assertIsNotNone(reusable)
        self.assertEqual(reusable.id, fix_task.id)

    def test_find_reusable_review_fix_task_does_not_promote_queued_product_task(self):
        state = self._make_state()
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="In Progress",
            id="LOOM:1:1",
            action_name="feature/review",
        )
        queued_product = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="LOOM:2",
            parent_task_id="LOOM:1:1",
            pipeline_root_id="LOOM:1",
            pipeline_depth=1,
            action_name="feature/implement",
        )
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued_product)

        reusable = self.server_mod._find_reusable_review_fix_task(
            state,
            review,
            "feature/fix-review",
        )

        self.assertIsNone(reusable)

    def test_refresh_reused_derived_task_updates_prompt_payload(self):
        task = self.state_mod.BoardTask(
            id="task-fix",
            task="Old fix guidance",
            group="g",
            lane="To Do",
            description="First review issue set",
            action_name="feature/fix-review",
            action_vars={"existing": "keep"},
            labels=["loom:derived", "review-fix"],
        )

        self.server_mod._refresh_reused_derived_task(
            task,
            message="Updated fix guidance with new review issues",
            description="Second review issue set",
            action_vars={"latest": "value"},
        )

        self.assertEqual(task.task, "Updated fix guidance with new review issues")
        self.assertIn("First review issue set", task.description)
        self.assertIn("Second review issue set", task.description)
        self.assertEqual(task.action_vars["existing"], "keep")
        self.assertEqual(task.action_vars["latest"], "value")


class ServerAgentPromptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.bridge_mod = importlib.import_module("loom.bridge")
        self.bridge_mod = importlib.reload(self.bridge_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_agent_mod = importlib.import_module("loom.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)

    async def test_background_prompt_task_is_retained_until_send_completes(self):
        state = self.state_mod.MatrixState()
        bridge_started = asyncio.Event()
        bridge_release = asyncio.Event()

        class FakeBridge:
            def __init__(self):
                self.sent = []

            async def send_text(self, session_id, payload):
                self.sent.append((session_id, payload))
                bridge_started.set()
                await bridge_release.wait()

        class FakeTemplateManager:
            pass

        bridge = FakeBridge()
        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=FakeTemplateManager(),
        )
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="agent",
            group="g",
            cell_type="agent",
            session_id="session-1",
        )

        task = await service.send_agent_prompt(
            cell,
            "Proceed with the derived task you just created.\n\nContext",
            background=True,
        )

        self.assertIn(task, service._background_prompt_tasks)
        await bridge_started.wait()
        self.assertIn(task, service._background_prompt_tasks)
        self.assertEqual(
            bridge.sent,
            [("session-1",
              "Proceed with the derived task you just created.\n\nContext\r")],
        )

        bridge_release.set()
        await task
        self.assertNotIn(task, service._background_prompt_tasks)

    async def test_background_prompt_queue_preserves_per_session_order(self):
        state = self.state_mod.MatrixState()
        first_release = asyncio.Event()
        started = []
        finished = []

        class FakeBridge:
            async def send_text(self, session_id, payload):
                started.append((session_id, payload))
                if payload.startswith("first"):
                    await first_release.wait()
                finished.append((session_id, payload))

        class FakeTemplateManager:
            pass

        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=FakeBridge(),
            worktree_mgr=None,
            template_mgr=FakeTemplateManager(),
        )
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="agent",
            group="g",
            cell_type="agent",
            session_id="session-1",
        )

        first = await service.send_agent_prompt(
            cell,
            "first message",
            background=True,
        )
        second = await service.send_agent_prompt(
            cell,
            "second message",
            background=True,
        )

        await asyncio.sleep(0)
        self.assertEqual(
            started,
            [("session-1", "first message\r")],
        )
        self.assertEqual(finished, [])

        first_release.set()
        await asyncio.gather(first, second)

        self.assertEqual(
            started,
            [
                ("session-1", "first message\r"),
                ("session-1", "second message\r"),
            ],
        )
        self.assertEqual(finished, started)

    async def test_delayed_self_dispatch_primes_ready_before_submit(self):
        class FakeLineInfo:
            overflow = 0
            first_visible_line_number = 0
            mutable_area_height = 24

        class FakeSession:
            def __init__(self, session_id):
                self.session_id = session_id
                self.sent = []
                self.screen_reads = 0

            async def async_set_profile_properties(self, change):
                pass

            async def async_send_text(self, text):
                self.sent.append(text)

            async def async_get_variable(self, name):
                return None

            async def async_get_line_info(self):
                self.screen_reads += 1
                return FakeLineInfo()

            async def async_get_contents(self, first, count):
                return []

        class FakeTab:
            def __init__(self, session):
                self.current_session = session
                self.sessions = [session]

        class FakeWindow:
            def __init__(self, session):
                self.window_id = "window-1"
                self.tabs = [FakeTab(session)]

        class FakeApp:
            def __init__(self, session):
                window = FakeWindow(session)
                self.current_window = window
                self.windows = [window]

        session = FakeSession("session-derive")

        async def fake_get_app(conn):
            return FakeApp(session)

        self.bridge_mod.iterm2.async_get_app = fake_get_app
        state = self.state_mod.MatrixState()
        bridge = self.bridge_mod.ITerm2Adapter(None, state)

        class FakeTemplateManager:
            pass

        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=FakeTemplateManager(),
        )
        cell = self.state_mod.AgentCell(
            id="agent-derive",
            name="agent",
            group="g",
            cell_type="agent",
            session_id="session-derive",
            agent_type="codex",
            command="codex",
        )
        state.agents[cell.id] = cell

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        orig_sleep = self.server_agent_mod.asyncio.sleep
        self.server_agent_mod.asyncio.sleep = fake_sleep
        try:
            task = await service.send_agent_prompt(
                cell,
                "Proceed with the derived task you just created.",
                delay=3,
                background=True,
                prime_input_ready=True,
                settled_submit=True,
            )
            await task
        finally:
            self.server_agent_mod.asyncio.sleep = orig_sleep

        self.assertEqual(delays, [3, 0.3])
        self.assertIn("session-derive", bridge._input_ready_sessions)
        self.assertEqual(
            session.sent,
            [
                "Proceed with the derived task you just created.",
                "\r",
            ],
        )
        self.assertEqual(session.screen_reads, 0)
