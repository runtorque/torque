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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_prompts_mod = importlib.import_module("torque.server_prompts")
        self.server_prompts_mod = importlib.reload(self.server_prompts_mod)
        self.server_agent_mod = importlib.import_module("torque.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_agent(self, agent_id, *, current_task_id="",
                    worktree_path="/repo/.torque/worktrees/shared",
                    worktree_branch="torque/shared",
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

    def test_engineer_worker_provider_override_sanitizer_falls_back_when_disabled(self):
        state = self.state_mod.MatrixState()
        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=False,
        )

        with self.assertLogs("torque", level="WARNING") as logs:
            provider = self.server_mod._sanitize_engineer_worker_provider_override(
                state,
                "g",
                {"_engineer_dispatch_id": "engineer-1"},
                "claude-code",
            )

        self.assertEqual(provider, "")
        self.assertIn("falling back to group default", "\n".join(logs.output))

    def test_engineer_worker_provider_override_sanitizer_preserves_default_behavior(self):
        state = self.state_mod.MatrixState()

        self.assertEqual(
            self.server_mod._sanitize_engineer_worker_provider_override(
                state,
                "g",
                {"_engineer_dispatch_id": "engineer-1"},
                "codex",
            ),
            "codex",
        )
        state.update_engineer_settings(
            "g",
            engineer_can_override_worker_provider=False,
        )
        self.assertEqual(
            self.server_mod._sanitize_engineer_worker_provider_override(
                state,
                "g",
                {},
                "codex",
            ),
            "codex",
        )

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
            id="TORQUE:12",
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
            self.server_mod._resolve_task_id(state, "TORQUE:1"),
            task.id,
        )

    def test_resolve_task_id_prefers_alias_over_archived_literal_collision(self):
        state = self.state_mod.MatrixState()
        archived = self.state_mod.BoardTask(
            id="TORQUE:51",
            task="Archived task",
            group="g",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Live task",
            group="g",
            lane="Backlog",
        )
        state.board_tasks[archived.id] = archived
        state.board_tasks[live.id] = live
        state.task_id_aliases[archived.id] = live.id

        self.assertEqual(
            self.server_mod._resolve_task_id(state, "TORQUE:51"),
            live.id,
        )
        self.assertEqual(
            self.server_mod._resolve_task_id(state, "TORQUE:5"),
            live.id,
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
            "torque-cli",
            lambda current: saved.append(current.id),
            timestamp="2026-04-07T18:00:00+00:00",
        )

        self.assertIsNone(root)
        self.assertEqual(task.verification_mode, "deploy")
        self.assertEqual(task.verification_state, "attempted")
        self.assertTrue(task.verification_summary["deploy_attempted"])
        self.assertEqual(task.verification_notes, "Deployed to staging")
        self.assertEqual(task.verification_updated_by, "torque-cli")
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
            "Engineer",
            lambda current: saved.append(current.id),
            root_task=root_task,
            timestamp="2026-04-07T18:05:00+00:00",
        )

        self.assertIs(root, root_task)
        self.assertEqual(task.verification_mode, "restart")
        self.assertEqual(task.verification_state, "failed")
        self.assertTrue(task.verification_summary["manual_smoke_done"])
        self.assertEqual(task.verification_updated_by, "Engineer")
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

    def test_torque_system_prompt_prefers_mcp_reporting_tools(self):
        prompt = self.server_prompts_mod.build_torque_system_prompt()

        self.assertIn("Use the Torque MCP tools", prompt)
        self.assertIn("torque_done(message=", prompt)
        self.assertIn("torque_verify(state=", prompt)
        self.assertIn("blocking human decision or approval", prompt)
        self.assertIn("For status updates, non-blocking observations, or optional", prompt)
        self.assertIn("torque_context()", prompt)
        self.assertEqual(
            prompt.count("`torque_ask(question=\"question\", description=\"details\")`"),
            1,
        )
        self.assertNotIn("torque_a-", prompt)

    def test_torque_system_prompt_includes_shared_memory_guidance(self):
        prompt = self.server_prompts_mod.build_torque_system_prompt()

        self.assertIn("## Shared memory", prompt)
        self.assertIn("torque_memory_publish", prompt)
        self.assertIn('entry_type="warning"', prompt)
        self.assertIn('entry_type="decision"', prompt)
        self.assertIn('entry_type="handoff"', prompt)
        self.assertIn('entry_type="finding"', prompt)
        self.assertIn('scope_kind="task"', prompt)
        self.assertIn('scope_kind="pipeline"', prompt)
        self.assertIn('scope_kind="group"', prompt)
        self.assertIn('scope_kind="project"', prompt)
        self.assertIn("top 5", prompt)
        self.assertIn("Do not publish routine progress", prompt)
        self.assertIn("MEMORY.md", prompt)

    def test_torque_system_prompt_default_omits_owner_user_message(self):
        # A non-user-owned worker (engineer-owned / architect-hired) must
        # NOT receive the post-bootstrap message-user instruction.
        prompt = self.server_prompts_mod.build_torque_system_prompt()
        self.assertNotIn("## After bootstrap: message the user", prompt)
        self.assertNotIn("owned by the user", prompt)

    def test_torque_system_prompt_owner_user_appends_message_instruction(self):
        prompt = self.server_prompts_mod.build_torque_system_prompt(
            owner_is_user=True)
        self.assertIn("## After bootstrap: message the user", prompt)
        self.assertIn("You are owned by the user", prompt)
        self.assertIn('torque_message_user(message="...")', prompt)
        self.assertIn("rather than only emitting it to the terminal", prompt)

    def test_torque_system_prompt_owner_user_is_pure_append(self):
        # Proves the existing prompt body is byte-unchanged: the
        # user-owned variant only appends the new section to the default.
        default = self.server_prompts_mod.build_torque_system_prompt()
        owned = self.server_prompts_mod.build_torque_system_prompt(
            owner_is_user=True)
        self.assertTrue(owned.startswith(default.rstrip()))
        self.assertEqual(
            self.server_prompts_mod.build_torque_system_prompt(
                owner_is_user=False),
            default,
        )

    def test_owner_is_user_from_ids_detects_user_ownership(self):
        from_ids = self.server_mod._owner_is_user_from_ids
        # No ownership stamps → user-owned.
        self.assertTrue(from_ids())
        self.assertTrue(from_ids(owner_engineer_id="",
                                 created_by_engineer_id="  ",
                                 hired_by_architect_id=None))
        # Any non-user ownership stamp → not user-owned.
        self.assertFalse(from_ids(owner_engineer_id="eng-1"))
        self.assertFalse(from_ids(created_by_engineer_id="eng-2"))
        self.assertFalse(from_ids(hired_by_architect_id="arch-1"))

    def test_agent_owner_is_user_reads_cell_ownership(self):
        is_user = self.server_mod._agent_owner_is_user
        self.assertFalse(is_user(None))
        user_owned = types.SimpleNamespace(
            owner_engineer_id="", created_by_engineer_id="",
            hired_by_architect_id="")
        self.assertTrue(is_user(user_owned))
        engineer_owned = types.SimpleNamespace(
            owner_engineer_id="eng-1", created_by_engineer_id="",
            hired_by_architect_id="")
        self.assertFalse(is_user(engineer_owned))
        architect_hired = types.SimpleNamespace(
            owner_engineer_id="", created_by_engineer_id="",
            hired_by_architect_id="arch-1")
        self.assertFalse(is_user(architect_hired))

    def test_system_prompt_preview_uses_unsaved_engineer_form_values(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_engineer_settings(
            "g",
            custom_instructions="Saved engineer instructions.",
            autonomy_mode="suggest_only",
        )
        state.update_group_settings("g", worktree_merge_cleanup="keep")

        prompt = self.server_mod._build_group_system_prompt_preview(
            state,
            "g",
            "engineer",
            settings_payload={
                "custom_instructions": "Unsaved engineer instructions.",
                "autonomy_mode": "aggressive_auto_continue",
            },
            group_settings_payload={
                "worktree_merge_cleanup": "close_remove",
            },
            action_system_prompt="Template system prompt.",
        )

        self.assertIn("Template system prompt.", prompt)
        self.assertIn("Unsaved engineer instructions.", prompt)
        self.assertNotIn("Saved engineer instructions.", prompt)
        self.assertIn("Autonomy mode: Aggressive auto-continue", prompt)
        self.assertIn(
            "Default post-merge cleanup: Close agent session and remove worktree",
            prompt,
        )
        self.assertEqual(prompt.count("## Shared memory"), 1)

    def test_system_prompt_preview_uses_unsaved_architect_form_values(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_architect_settings(
            "g",
            architect_custom_instructions="Saved architect instructions.",
            architect_autonomy_mode="dispatch_after_confirm",
        )

        prompt = self.server_mod._build_group_system_prompt_preview(
            state,
            "g",
            "architect",
            settings_payload={
                "architect_custom_instructions": "Unsaved architect instructions.",
                "architect_autonomy_mode": "ask_always",
                "architect_journal_checkpoint_frequency": "manual_only",
            },
            action_system_prompt="Architect template system prompt.",
        )

        self.assertIn("# Torque Agent", prompt)
        self.assertIn("Architect template system prompt.", prompt)
        self.assertIn("Unsaved architect instructions.", prompt)
        self.assertNotIn("Saved architect instructions.", prompt)
        self.assertIn("Autonomy mode: Ask always", prompt)
        self.assertIn("Journal checkpoint cadence: manual_only", prompt)
        self.assertEqual(prompt.count("## Shared memory"), 1)

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

        self.assertIn("IMPORTANT: Finish this task by calling a Torque MCP tool below.", prompt)
        self.assertIn("Available completion paths for this task:", prompt)
        self.assertIn("torque_derive(description=", prompt)
        self.assertIn("continues in the same agent", prompt)
        self.assertIn("torque_ask(question=\"title\", description=\"details\")", prompt)
        self.assertIn("torque_done(message=\"brief summary\")", prompt)
        self.assertIn("Other reporting tools when relevant:", prompt)
        self.assertIn("torque_blocked(reason=", prompt)
        self.assertIn("torque_error(message=", prompt)
        self.assertIn("torque_verify(state=", prompt)
        self.assertNotIn("Report your progress with these Torque MCP tools", prompt)
        self.assertNotIn("torque_ready()", prompt)
        self.assertNotIn("torque_progress(message=", prompt)

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
        self.assertIn("torque_done(message=\"brief summary\")", prompt)
        self.assertIn("Other reporting tools when relevant:", prompt)
        self.assertIn("torque_blocked(reason=", prompt)
        self.assertIn("torque_error(message=", prompt)
        self.assertIn("torque_verify(state=", prompt)
        self.assertIn("This task is part of a pipeline", prompt)
        self.assertIn("Before reporting done, commit all your changes.", prompt)
        self.assertNotIn("torque_ask(question=\"title\", description=\"details\")", prompt)
        self.assertNotIn("torque_ready()", prompt)

    def test_dispatch_postscript_without_transitions_stays_compact(self):
        prompt = self.server_prompts_mod.build_dispatch_postscript(
            transitions=[],
            is_clean=True,
        )

        self.assertIn("Available completion paths for this task:", prompt)
        self.assertIn("torque_done(message=\"brief summary\")", prompt)
        self.assertIn("Other reporting tools when relevant:", prompt)
        self.assertIn("torque_blocked(reason=", prompt)
        self.assertIn("torque_error(message=", prompt)
        self.assertIn("torque_verify(state=", prompt)
        self.assertNotIn("IMPORTANT:", prompt)
        self.assertNotIn("torque_derive(description=", prompt)
        self.assertNotIn("torque_ask(question=\"title\", description=\"details\")", prompt)

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

    def test_startup_prompt_for_new_claude_engineer_does_not_duplicate_persistent_prompt(self):
        # Engineers/architects on claude-code receive their persistent prompt
        # via --append-system-prompt-file in the boot command. Sending it
        # again as the first chat turn duplicates the entire system prompt
        # into the conversation transcript.
        self.assertEqual(
            self.server_mod._startup_prompt_for_new_agent(
                agent_type="claude-code",
                persistent_prompt_text="Engineer system prompt",
            ),
            "",
        )

    def test_startup_prompt_for_new_codex_engineer_still_uses_persistent_prompt(self):
        # Codex's file-based path historically required the persistent prompt
        # to be sent as the first chat turn — preserve that behavior.
        self.assertEqual(
            self.server_mod._startup_prompt_for_new_agent(
                agent_type="codex",
                persistent_prompt_text="Engineer system prompt",
            ),
            "Engineer system prompt",
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

    def test_active_review_blocks_parent_checkpoint_writes_only(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent("impl-1", current_task_id="")
        reviewer = self._make_agent("review-1", current_task_id="task-review")
        state.agents = {owner.id: owner, reviewer.id: reviewer}
        parent = self.state_mod.BoardTask(
            id="task-impl",
            task="Implementation",
            group="g",
            lane="In Progress",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = self.state_mod.BoardTask(
            id="task-review",
            task="Review implementation",
            group="g",
            lane="In Progress",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
            action_name="feature/review",
        )
        state.board_tasks = {parent.id: parent, review.id: review}

        block_reason = self.server_mod._shared_review_checkpoint_block_reason(
            state,
            owner,
        )

        self.assertIn("feature/review task task-review", block_reason)
        self.assertIs(
            self.server_mod._active_shared_worktree_review_for_cell(
                state,
                owner,
            ),
            review,
        )
        self.assertEqual(
            self.server_mod._shared_review_checkpoint_block_reason(
                state,
                reviewer,
            ),
            "",
        )

    def test_review_fix_handoff_allows_parent_checkpoint_writes(self):
        state = self.state_mod.MatrixState()
        owner = self._make_agent("impl-1", current_task_id="task-fix")
        reviewer = self._make_agent("review-1", current_task_id="task-review")
        state.agents = {owner.id: owner, reviewer.id: reviewer}
        parent = self.state_mod.BoardTask(
            id="task-impl",
            task="Implementation",
            group="g",
            lane="Done",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = self.state_mod.BoardTask(
            id="task-review",
            task="Review implementation",
            group="g",
            lane="In Progress",
            status="Fixing",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
            action_name="feature/review",
        )
        fix = self.state_mod.BoardTask(
            id="task-fix",
            task="Fix review blockers",
            group="g",
            lane="In Progress",
            parent_task_id=review.id,
            pipeline_root_id=parent.id,
            pipeline_depth=2,
            agent_id=owner.id,
            action_name="feature/implement",
        )
        state.board_tasks = {
            parent.id: parent,
            review.id: review,
            fix.id: fix,
        }

        self.assertTrue(state.agent_is_busy(owner.id))
        self.assertFalse(state.agent_is_busy(reviewer.id))
        self.assertIsNone(
            self.server_mod._active_shared_worktree_review_for_cell(
                state,
                owner,
            )
        )
        self.assertEqual(
            self.server_mod._shared_review_checkpoint_block_reason(
                state,
                owner,
            ),
            "",
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
            worktree_path="/repo/.torque/worktrees/one",
            worktree_branch="torque/shared-branch",
        )
        target = self._make_agent(
            "agent-2",
            worktree_path="/repo/.torque/worktrees/two",
            worktree_branch="torque/shared-branch",
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

    def test_worktree_removal_refuses_attached_agent(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        worker = self.state_mod.AgentCell(
            id="20d95b63",
            name="Active Worker",
            group="g",
            cell_type="agent",
            status="idle",
            session_id="session-1",
            worktree_path="/repo/.torque/worktrees/20d95b63",
            worktree_branch="torque/panelsmith/chat-panel-20d95b6",
            worktree_repo_root="/repo",
            worktree_base_branch="main",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        reason = self.server_mod._worktree_removal_refusal_reason(
            state,
            worker,
            now=1_779_000_000,
        )

        self.assertIn("active/fresh agent", reason)
        self.assertIn("attached session", reason)

    def test_worktree_entry_matches_active_agent_after_tracking_was_cleared(self):
        worker = self.state_mod.AgentCell(
            id="20d95b63",
            name="Active Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="",
            worktree_branch="",
            worktree_repo_root="",
            directory="/repo/.torque/worktrees/20d95b63",
            current_path="/repo/.torque/worktrees/20d95b63/subdir",
            git_root="/repo/.torque/worktrees/20d95b63",
        )

        self.assertTrue(
            self.server_mod._worktree_entry_matches_agent(
                "/repo",
                "/repo/.torque/worktrees/20d95b63",
                worker,
            )
        )

    def test_merge_cleanup_preserves_directory_when_remove_is_skipped(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        worktree_path = "/repo/.torque/worktrees/20d95b63"
        worker = self.state_mod.AgentCell(
            id="20d95b63",
            name="Active Worker",
            group="g",
            cell_type="agent",
            directory=worktree_path,
            worktree_path=worktree_path,
            worktree_branch="torque/panelsmith/chat-panel-20d95b6",
            worktree_repo_root="/repo",
            worktree_base_branch="main",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        main_code = self.server_mod.main.__code__
        cleanup_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "_cleanup_after_merge"
        )

        async def close_agent_session_only(*_args, **_kwargs):
            return []

        async def safe_remove_worktree_result(cell):
            self.assertIs(cell, worker)
            return {
                "ok": False,
                "worktree_removed": False,
                "branch_deleted": False,
                "skipped": True,
                "message": "skipped: worktree belongs to active/fresh agent",
                "mismatches": [],
            }

        closure_values = {
            name: (lambda *args, **kwargs: None)
            for name in cleanup_code.co_freevars
        }
        closure_values.update({
            "_close_agent_session_only": close_agent_session_only,
            "_safe_remove_worktree_result": safe_remove_worktree_result,
            "bridge": types.SimpleNamespace(),
            "state": state,
        })
        closure = tuple(
            (lambda x: lambda: x)(closure_values[name]).__closure__[0]
            for name in cleanup_code.co_freevars
        )
        cleanup_after_merge = types.FunctionType(
            cleanup_code,
            self.server_mod.__dict__,
            "_cleanup_after_merge",
            None,
            closure,
        )

        cleanup = asyncio.run(
            cleanup_after_merge(
                worker,
                close_agent=False,
                remove_worktree=True,
            )
        )

        self.assertFalse(cleanup["worktree_removed"])
        self.assertIn("skipped: worktree belongs", cleanup["errors"][0])
        self.assertEqual(worker.directory, worktree_path)
        self.assertEqual(worker.worktree_path, worktree_path)


class ServerVerifyHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, **closure_overrides):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )

        class DummyBridge:
            async def list_profiles(self):
                return []

            async def get_launch_context(self):
                return types.SimpleNamespace(
                    current_path="",
                    current_profile="",
                )

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_panel_event": lambda *args, **kwargs: None,
            "_resolve_base_dir": lambda group="": "",
            "_runtime_payload": lambda: {},
            "action_mgr": types.SimpleNamespace(
                list_actions=lambda _base_dir: [],
            ),
            "bridge": DummyBridge(),
            "handle_command": None,
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
        })
        closure_values.update(closure_overrides)
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            self.server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    async def test_board_verify_task_handler_accepts_minimal_payload(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.board_add_task("Verify deploy", "g", id="TORQUE:160")
        handle_command = self._extract_handle_command(state)

        result = await handle_command({
            "cmd": "board_verify_task",
            "id": "TORQUE:160",
            "actor_name": "engineer-1",
            "verification_state": "passed",
        })

        task = state.board_tasks["TORQUE:160"]
        self.assertEqual(
            result,
            {
                "type": "verification_updated",
                "task_id": "TORQUE:160",
                "message": "Verification updated: state=passed",
            },
        )
        self.assertEqual(task.verification_state, "passed")
        self.assertEqual(task.verification_updated_by, "engineer-1")
        self.assertTrue(task.verification_updated_at)

    async def test_board_verify_task_handler_accepts_smoke_and_notes_payload(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        root_task = state.board_add_task(
            "Release billing changes",
            "g",
            id="TORQUE:160",
        )
        task = state.board_add_task(
            "Restart billing service",
            "g",
            id="TORQUE:160:1",
            pipeline_root_id="TORQUE:160",
        )
        self.assertIsNotNone(root_task)
        self.assertIsNotNone(task)
        handle_command = self._extract_handle_command(state)

        result = await handle_command({
            "cmd": "board_verify_task",
            "id": "TORQUE:160:1",
            "actor_name": "engineer-1",
            "verification_mode": "restart",
            "verification_notes": "Smoke failed on login redirect",
            "tests_run": (
                "python3 -m unittest "
                "tests.test_server_self_dispatch"
            ),
            "human_validation_pending": (
                "Confirm redirect recovers after restart"
            ),
            "deploy_needed": True,
            "deploy_attempted": True,
            "smoke_status": "failed",
        })

        task = state.board_tasks["TORQUE:160:1"]
        root_task = state.board_tasks["TORQUE:160"]
        self.assertEqual(result["type"], "verification_updated")
        self.assertEqual(result["task_id"], "TORQUE:160:1")
        self.assertIn("state=failed", result["message"])
        self.assertIn("mode=restart", result["message"])
        self.assertIn("manual smoke done", result["message"])
        self.assertEqual(task.verification_mode, "restart")
        self.assertEqual(task.verification_state, "failed")
        self.assertEqual(
            task.verification_notes,
            "Smoke failed on login redirect",
        )
        self.assertEqual(
            task.verification_summary["tests_run"],
            "python3 -m unittest tests.test_server_self_dispatch",
        )
        self.assertTrue(task.verification_summary["manual_smoke_done"])
        self.assertTrue(task.verification_summary["deploy_needed"])
        self.assertTrue(task.verification_summary["deploy_attempted"])
        self.assertEqual(
            task.verification_summary["human_validation_pending"],
            "Confirm redirect recovers after restart",
        )
        self.assertEqual(root_task.verification_mode, "restart")
        self.assertEqual(root_task.verification_state, "failed")
        self.assertEqual(
            root_task.verification_notes,
            "Smoke failed on login redirect",
        )
        self.assertTrue(root_task.verification_summary["manual_smoke_done"])

    async def test_board_move_task_handler_can_clear_status(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        task = state.board_add_task(
            "Manual cleanup",
            "g",
            lane="In Progress",
            id="task-move",
            status="Fixing",
        )
        self.assertIsNotNone(task)
        handle_command = self._extract_handle_command(state)

        result = await handle_command({
            "cmd": "board_move_task",
            "id": "task-move",
            "lane": "Done",
            "clear_status": True,
        })

        self.assertEqual(
            result,
            {
                "type": "task_moved",
                "task_id": "task-move",
                "previous_lane": "In Progress",
                "new_lane": "Done",
                "status": "",
            },
        )
        self.assertEqual(state.board_tasks["task-move"].lane, "Done")
        self.assertEqual(state.board_tasks["task-move"].status, "")

    async def test_worktree_merge_auto_moves_sole_linked_task_to_done(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", engineer_merge_mode="engineer-choice")
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/tmp/worker",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
            worktree_repo_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        task = state.board_add_task(
            "Ship merged change",
            "g",
            lane="In Progress",
            id="TORQUE:168",
            agent_id=worker.id,
        )
        self.assertIsNotNone(task)
        task.status = "On Review"

        class FakeWorktreeManager:
            async def has_uncommitted_changes(self, _cell):
                return False

            async def stale_base_info(self, _cell):
                return {"stale": False}

            async def check_merge_conflicts(self, _cell):
                return {"clean": True, "tree_sha": "tree-sha"}

            async def merge_untracked_overwrite_paths(
                self, _repo_root, _base_branch, _tree_sha
            ):
                return []

            async def server_merge(self, _cell, _msg, squash=True):
                return {"ok": True, "sha": "abc123"}

        async def fake_broadcast_toast(*_args, **_kwargs):
            return None

        async def fake_cleanup_after_merge(
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

        async def fake_latest_boundary_state(_cell):
            return {"latest": None, "clean": True, "reason": ""}

        async def fake_reviewer_cleanup(*_args, **_kwargs):
            return {"agents": [], "agent_closed": 0, "worktree_removed": 0, "errors": []}

        async def fake_sibling_gate(*_args, **_kwargs):
            return None

        old_reviewer_cleanup = (
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell
        )
        old_sibling_gate = (
            self.server_mod._sibling_branch_divergence_gate_for_merge
        )
        self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
            fake_reviewer_cleanup
        )
        self.server_mod._sibling_branch_divergence_gate_for_merge = (
            fake_sibling_gate
        )
        try:
            handle_command = self._extract_handle_command(
                state,
                _broadcast_toast=fake_broadcast_toast,
                _cleanup_after_merge=fake_cleanup_after_merge,
                _latest_boundary_state_for_cell=fake_latest_boundary_state,
                _mark_branch_boundaries_merged=lambda *_args, **_kwargs: None,
                worktree_mgr=FakeWorktreeManager(),
            )

            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "force_direct": True,
                "message": "Merge worker branch",
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            })
        finally:
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
                old_reviewer_cleanup
            )
            self.server_mod._sibling_branch_divergence_gate_for_merge = (
                old_sibling_gate
            )

        task = state.board_tasks["TORQUE:168"]
        self.assertEqual(result["type"], "worktree_merge")
        self.assertTrue(result["ok"])
        self.assertEqual(result["sha"], "abc123")
        self.assertTrue(result["force_direct"])
        self.assertIn("Direct local worktree merge", result["warning"])
        self.assertEqual(
            result["workflow_breach"]["subkind"],
            "force_direct_merge",
        )
        self.assertEqual(task.lane, "Done")
        self.assertEqual(task.status, "")
        self.assertEqual(task.agent_id, "")

    async def test_worktree_merge_drains_bound_followup_queue_immediately(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", engineer_merge_mode="engineer-choice")
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/tmp/worker",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
            worktree_repo_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        shipped = state.board_add_task(
            "Shipped first task",
            "g",
            lane="Done",
            id="TORQUE:392",
            agent_id=worker.id,
        )
        queued = state.board_add_task(
            "Queued followup",
            "g",
            lane="To Do",
            id="TORQUE:393",
            agent_id=worker.id,
        )
        self.assertIsNotNone(shipped)
        self.assertIsNotNone(queued)
        state.auto_dispatch_queue_add(
            "g",
            queued.id,
            target_agent_id=worker.id,
            max_concurrent=1,
        )
        calls = []

        class FakeWorktreeManager:
            async def has_uncommitted_changes(self, _cell):
                return False

            async def stale_base_info(self, _cell):
                return {"stale": False}

            async def check_merge_conflicts(self, _cell):
                return {"clean": True, "tree_sha": "tree-sha"}

            async def merge_untracked_overwrite_paths(
                self, _repo_root, _base_branch, _tree_sha
            ):
                return []

            async def server_merge(self, _cell, _msg, squash=True):
                return {"ok": True, "sha": "abc123"}

            async def validate(self, _cell):
                return True

            async def reset_to_base(self, _cell):
                return True

            async def count_commits(self, _cell):
                return 0

        async def fake_broadcast_toast(*_args, **_kwargs):
            return None

        async def fake_latest_boundary_state(_cell):
            return {"latest": None, "clean": True, "reason": ""}

        async def fake_reviewer_cleanup(*_args, **_kwargs):
            return {"agents": [], "agent_closed": 0, "worktree_removed": 0, "errors": []}

        async def fake_sibling_gate(*_args, **_kwargs):
            return None

        async def nested_dispatch(payload):
            calls.append(dict(payload))
            if payload["cmd"] != "dispatch_task":
                self.fail(f"Unexpected nested command: {payload}")
            task = state.board_tasks[payload["id"]]
            task.agent_id = payload.get("agent_id", "")
            task.lane = "In Progress"
            worker.current_task_id = task.id
            return {
                "type": "ok",
                "task_id": task.id,
                "agent_id": task.agent_id,
            }

        old_reviewer_cleanup = (
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell
        )
        old_sibling_gate = (
            self.server_mod._sibling_branch_divergence_gate_for_merge
        )
        self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
            fake_reviewer_cleanup
        )
        self.server_mod._sibling_branch_divergence_gate_for_merge = (
            fake_sibling_gate
        )
        try:
            handle_command = self._extract_handle_command(
                state,
                _broadcast_toast=fake_broadcast_toast,
                _latest_boundary_state_for_cell=fake_latest_boundary_state,
                _mark_branch_boundaries_merged=lambda *_args, **_kwargs: None,
                handle_command=nested_dispatch,
                worktree_mgr=FakeWorktreeManager(),
            )

            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "force_direct": True,
                "message": "Merge worker branch",
            })
        finally:
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
                old_reviewer_cleanup
            )
            self.server_mod._sibling_branch_divergence_gate_for_merge = (
                old_sibling_gate
            )

        self.assertEqual(result["type"], "worktree_merge")
        self.assertTrue(result["ok"])
        self.assertEqual([call["cmd"] for call in calls], ["dispatch_task"])
        self.assertEqual(calls[0]["id"], queued.id)
        self.assertEqual(calls[0]["agent_id"], worker.id)
        self.assertEqual(state.board_tasks[queued.id].lane, "In Progress")
        self.assertNotIn("g", state.auto_dispatch_queues)

    def _make_followup_override_state(self):
        """State with a worker carrying one queued follow-up task."""
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", engineer_merge_mode="engineer-choice")
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/tmp/worker",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
            worktree_repo_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        state.board_add_task(
            "Shipped first task",
            "g",
            lane="Done",
            id="TORQUE:392",
            agent_id=worker.id,
        )
        queued = state.board_add_task(
            "Queued followup",
            "g",
            lane="To Do",
            id="TORQUE:393",
            agent_id=worker.id,
        )
        self.assertIsNotNone(queued)
        return state, worker, queued

    async def _run_followup_override_merge(
        self, state, worker, *, command_extra, reset_ok, nested_dispatch
    ):
        """Drive a direct worktree_merge for the override/guard regressions."""

        class FakeWorktreeManager:
            async def has_uncommitted_changes(self, _cell):
                return False

            async def stale_base_info(self, _cell):
                return {"stale": False}

            async def check_merge_conflicts(self, _cell):
                return {"clean": True, "tree_sha": "tree-sha"}

            async def merge_untracked_overwrite_paths(
                self, _repo_root, _base_branch, _tree_sha
            ):
                return []

            async def server_merge(self, _cell, _msg, squash=True):
                return {"ok": True, "sha": "abc123"}

            async def validate(self, _cell):
                return True

            async def reset_to_base(self, _cell):
                return reset_ok

            async def count_commits(self, _cell):
                return 0

        async def fake_broadcast_toast(*_args, **_kwargs):
            return None

        async def fake_latest_boundary_state(_cell):
            return {"latest": None, "clean": True, "reason": ""}

        async def fake_reviewer_cleanup(*_args, **_kwargs):
            return {
                "agents": [],
                "agent_closed": 0,
                "worktree_removed": 0,
                "errors": [],
            }

        async def fake_sibling_gate(*_args, **_kwargs):
            return None

        cleanup_calls = []

        async def fake_cleanup_after_merge(cell, *, close_agent, remove_worktree):
            cleanup_calls.append((cell.id, close_agent, remove_worktree))
            return {
                "close_agent": close_agent,
                "remove_worktree": remove_worktree,
                "agent_closed": close_agent,
                "worktree_removed": remove_worktree,
                "errors": [],
            }

        old_reviewer_cleanup = (
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell
        )
        old_sibling_gate = (
            self.server_mod._sibling_branch_divergence_gate_for_merge
        )
        self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
            fake_reviewer_cleanup
        )
        self.server_mod._sibling_branch_divergence_gate_for_merge = (
            fake_sibling_gate
        )
        try:
            handle_command = self._extract_handle_command(
                state,
                _broadcast_toast=fake_broadcast_toast,
                _cleanup_after_merge=fake_cleanup_after_merge,
                _latest_boundary_state_for_cell=fake_latest_boundary_state,
                _mark_branch_boundaries_merged=lambda *_a, **_k: None,
                handle_command=nested_dispatch,
                worktree_mgr=FakeWorktreeManager(),
            )
            command = {
                "cmd": "worktree_merge",
                "id": worker.id,
                "force_direct": True,
                "message": "Merge worker branch",
            }
            command.update(command_extra)
            result = await handle_command(command)
        finally:
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
                old_reviewer_cleanup
            )
            self.server_mod._sibling_branch_divergence_gate_for_merge = (
                old_sibling_gate
            )
        return result, cleanup_calls

    async def test_worktree_merge_surfaces_silent_cleanup_override(self):
        # Part A (TORQUE:381 / :393): a caller passing
        # remove_worktree_on_merge=true must learn the flag was silently
        # overridden to preserve the agent for queued follow-ups.
        state, worker, _queued = self._make_followup_override_state()

        async def nested_dispatch(payload):
            if payload["cmd"] == "dispatch_task":
                task = state.board_tasks[payload["id"]]
                task.agent_id = payload.get("agent_id", "")
                task.lane = "In Progress"
                return {"type": "ok", "task_id": task.id}
            return {"type": "ok"}

        with self.assertLogs("torque", level="WARNING") as logs:
            result, cleanup_calls = await self._run_followup_override_merge(
                state,
                worker,
                command_extra={
                    "close_agent_on_merge": True,
                    "remove_worktree_on_merge": True,
                },
                reset_ok=True,
                nested_dispatch=nested_dispatch,
            )

        self.assertEqual(result["type"], "worktree_merge")
        self.assertTrue(result["ok"])
        cleanup = result["cleanup"]
        # The override decision itself is unchanged: agent + worktree preserved.
        self.assertFalse(cleanup["close_agent"])
        self.assertFalse(cleanup["remove_worktree"])
        self.assertEqual(cleanup_calls, [])  # cleanup_after_merge never ran
        # ...but now the override is surfaced in the struct.
        self.assertTrue(cleanup["cleanup_overridden"])
        self.assertEqual(cleanup["override_reason"], "queued_followups")
        self.assertEqual(cleanup["queued_followup_count"], 1)
        self.assertTrue(
            any(
                "cleanup flags overridden due to queued follow-ups" in msg
                and "agent=worker" in msg
                for msg in logs.output
            ),
            logs.output,
        )

    async def test_worktree_merge_no_override_field_without_cleanup_flags(self):
        # When no cleanup was requested there is nothing to override, so the
        # surfacing fields must stay absent (no false alarms).
        state, worker, _queued = self._make_followup_override_state()

        async def nested_dispatch(payload):
            if payload["cmd"] == "dispatch_task":
                task = state.board_tasks[payload["id"]]
                task.lane = "In Progress"
                return {"type": "ok", "task_id": task.id}
            return {"type": "ok"}

        result, _calls = await self._run_followup_override_merge(
            state,
            worker,
            command_extra={},
            reset_ok=True,
            nested_dispatch=nested_dispatch,
        )

        cleanup = result["cleanup"]
        self.assertNotIn("cleanup_overridden", cleanup)
        self.assertNotIn("override_reason", cleanup)
        self.assertNotIn("queued_followup_count", cleanup)

    async def test_worktree_merge_skips_followup_dispatch_when_reset_fails(self):
        # Part B (TORQUE:381 / :423): if the post-merge reset_to_base fails
        # while queued follow-ups exist, the auto-resume + pump-drain must be
        # skipped this cycle so the next task does not land on a dirty worktree.
        state, worker, queued = self._make_followup_override_state()
        state.auto_dispatch_queue_add(
            "g",
            queued.id,
            target_agent_id=worker.id,
            max_concurrent=1,
        )

        calls = []

        async def nested_dispatch(payload):
            calls.append(dict(payload))
            return {"type": "ok"}

        with self.assertLogs("torque", level="WARNING") as logs:
            result, _cleanup_calls = await self._run_followup_override_merge(
                state,
                worker,
                command_extra={},
                reset_ok=False,
                nested_dispatch=nested_dispatch,
            )

        self.assertEqual(result["type"], "worktree_merge")
        self.assertTrue(result["ok"])
        # No follow-up dispatched onto the dirty worktree.
        self.assertEqual(calls, [])
        # Queue is preserved for the next pump cycle, not drained.
        self.assertIn("g", state.auto_dispatch_queues)
        self.assertEqual(state.board_tasks[queued.id].lane, "To Do")
        self.assertTrue(
            any(
                "Skipping post-merge follow-up dispatch" in msg
                for msg in logs.output
            ),
            logs.output,
        )

    def _make_pr_merge_state(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            status="running",
            worktree_path="/tmp/worker",
            worktree_branch="torque/worker",
            worktree_base_branch="main",
            worktree_repo_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        task = state.board_add_task(
            "Ship PR merged change",
            "g",
            lane="In Progress",
            id="TORQUE:490",
            agent_id=worker.id,
        )
        task.worktree_boundary = {
            "version": "1",
            "branch": worker.worktree_branch,
            "repo_root": worker.worktree_repo_root,
            "base_branch": worker.worktree_base_branch,
            "commit_sha": "head123",
            "kind": "marker",
            "status": "open",
            "recorded_at": "2026-05-19T18:00:00+00:00",
            "recorded_by_agent_id": worker.id,
            "message": "",
            "superseded_by_task_id": "",
            "merged_at": "",
            "merge_commit_sha": "",
            "reason": "",
        }
        return state, worker, task

    def _mark_boundaries_for_state(self, state):
        from torque.worktree_boundaries import mark_branch_boundaries_merged

        def _mark(cell, merge_sha):
            for branch_task in mark_branch_boundaries_merged(
                state.board_tasks.values(),
                repo_root=cell.worktree_repo_root or cell.git_root or "",
                branch=cell.worktree_branch or "",
                merge_sha=merge_sha,
            ):
                state._emit(
                    "task_upsert",
                    **self.server_mod.asdict(branch_task),
                )
                state._db_save_task(branch_task)

        return _mark

    def _pr_handle_command(self, state, worker, worktree_mgr,
                           cleanup_after_merge, *, nested_dispatch=None):
        async def fake_broadcast_toast(*_args, **_kwargs):
            return None

        async def fake_latest_boundary_state(_cell):
            task = state.board_tasks["TORQUE:490"]
            summary = {
                "task_id": task.id,
                "task_title": task.task,
                "boundary": dict(task.worktree_boundary),
                "clean_mergeable": True,
            }
            return {"latest": summary, "clean": summary, "reason": ""}

        async def fake_reviewer_cleanup(*_args, **_kwargs):
            return {
                "agents": [],
                "agent_closed": 0,
                "worktree_removed": 0,
                "errors": [],
            }

        async def fake_sibling_gate(*_args, **_kwargs):
            return None

        old_reviewer_cleanup = (
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell
        )
        old_sibling_gate = (
            self.server_mod._sibling_branch_divergence_gate_for_merge
        )
        self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
            fake_reviewer_cleanup
        )
        self.server_mod._sibling_branch_divergence_gate_for_merge = (
            fake_sibling_gate
        )

        def restore():
            self.server_mod._cleanup_shipped_reviewers_for_merged_cell = (
                old_reviewer_cleanup
            )
            self.server_mod._sibling_branch_divergence_gate_for_merge = (
                old_sibling_gate
            )

        return (
            self._extract_handle_command(
                state,
                _broadcast_toast=fake_broadcast_toast,
                _cleanup_after_merge=cleanup_after_merge,
                _latest_boundary_state_for_cell=fake_latest_boundary_state,
                _mark_branch_boundaries_merged=(
                    self._mark_boundaries_for_state(state)
                ),
                handle_command=nested_dispatch,
                worktree_mgr=worktree_mgr,
            ),
            restore,
        )

    class _FakePrWorktreeManager:
        def __init__(self, merge_result, direct_result=None,
                     push_result=None, force_retry_result=None):
            self.merge_result = merge_result
            self.direct_result = direct_result or {"ok": True, "sha": "direct123"}
            self.push_result = push_result or {
                "ok": True,
                "phase": "push_branch",
            }
            self.force_retry_result = force_retry_result
            self.calls = []
            self.sync_calls = 0

        async def github_preflight(self, worktree_path):
            self.calls.append(("preflight", worktree_path))
            return {"ok": True, "phase": "github_preflight"}

        async def github_select_remote(self, worktree_path):
            self.calls.append(("select_remote", worktree_path))
            return {
                "ok": True,
                "phase": "github_remote",
                "remote": "origin",
            }

        async def github_sync_remote_base(
            self,
            worktree_path,
            repo_root,
            remote,
            base_branch,
        ):
            self.sync_calls += 1
            self.calls.append(
                ("sync_base", self.sync_calls, worktree_path, repo_root,
                 remote, base_branch)
            )
            return {
                "ok": True,
                "phase": "remote_base_sync",
                "synced": self.sync_calls > 1,
            }

        async def has_uncommitted_changes(self, _cell):
            self.calls.append(("dirty",))
            return False

        async def stale_base_info(self, _cell):
            self.calls.append(("stale_base",))
            return {"stale": False}

        async def check_merge_conflicts(self, _cell):
            self.calls.append(("check_merge",))
            return {"clean": True, "tree_sha": "tree-sha"}

        async def merge_untracked_overwrite_paths(
            self,
            _repo_root,
            _base_branch,
            _tree_sha,
        ):
            self.calls.append(("untracked_overwrite",))
            return []

        async def server_merge(self, cell, message, squash=True):
            self.calls.append(("server_merge", cell.id, message, squash))
            if callable(self.direct_result):
                return self.direct_result(squash)
            return dict(self.direct_result)

        async def validate(self, _cell):
            self.calls.append(("validate",))
            return False

        async def reset_to_base(self, _cell):
            self.calls.append(("reset_to_base",))
            return True

        async def count_commits(self, _cell):
            self.calls.append(("count_commits",))
            return 0

        async def list_checkpoints(self, _cell):
            self.calls.append(("list_checkpoints",))
            return [{"message": "Implement worker change", "body": ""}]

        async def github_push_branch(self, worktree_path, remote, branch):
            self.calls.append(("push", worktree_path, remote, branch))
            return dict(self.push_result)

        async def github_force_push_branch_with_lease_if_safe(
            self,
            worktree_path,
            remote,
            branch,
            *,
            base_branch,
            push_error=None,
        ):
            self.calls.append(
                (
                    "force_push_retry",
                    worktree_path,
                    remote,
                    branch,
                    base_branch,
                    push_error,
                )
            )
            if self.force_retry_result is None:
                return {
                    "ok": False,
                    "phase": "push_branch",
                    "error": "retry not configured",
                    "non_fast_forward": False,
                    "auto_force_push": False,
                    "safety_gate_passed": False,
                }
            return dict(self.force_retry_result)

        async def github_create_or_reuse_pr(
            self,
            worktree_path,
            branch,
            base_branch,
            title="",
            body="",
        ):
            self.calls.append(
                ("create_pr", worktree_path, branch, base_branch, title, body)
            )
            return {
                "ok": True,
                "phase": "pr_create",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "head_sha": "head123",
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
            self.calls.append(
                (
                    "merge_pr",
                    worktree_path,
                    pr_number,
                    head_sha,
                    auto,
                    subject,
                    body,
                    url,
                )
            )
            if callable(self.merge_result):
                return self.merge_result(auto)
            return dict(self.merge_result)

    async def test_worktree_merge_mode_pr_rejects_force_direct(self):
        state, worker, _task = self._make_pr_merge_state()
        state.update_group_settings("g", engineer_merge_mode="pr")
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": True,
            "phase": "pr_merge",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_commit_sha": "squash789",
            "merge_state": "CLEAN",
            "pending": False,
            "pr_status": {"ok": True, "state": "MERGED"},
        })
        panel_events = []

        handle_command = self._extract_handle_command(
            state,
            _panel_event=lambda *args, **kwargs: panel_events.append(
                (args, kwargs)
            ),
            worktree_mgr=worktree_mgr,
        )

        result = await handle_command({
            "cmd": "worktree_merge",
            "id": worker.id,
            "force_direct": True,
        })

        self.assertEqual(result["type"], "worktree_merge")
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "merge_mode_locked")
        self.assertEqual(result["code"], "force_direct_disallowed")
        self.assertIn("engineer_merge_mode='pr'", result["message"])
        self.assertEqual(
            result["workflow_breach"]["subkind"],
            "merge_mode_locked",
        )
        self.assertEqual(panel_events[0][0][0], "workflow_breach")
        self.assertEqual(worktree_mgr.calls, [])

    async def test_worktree_merge_mode_direct_without_force_uses_direct_path(self):
        state, worker, _task = self._make_pr_merge_state()
        state.update_group_settings("g", engineer_merge_mode="direct")
        worktree_mgr = self._FakePrWorktreeManager(
            {
                "ok": True,
                "phase": "pr_merge",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "head_sha": "head123",
                "merge_commit_sha": "squash789",
                "merge_state": "CLEAN",
                "pending": False,
                "pr_status": {"ok": True, "state": "MERGED"},
            },
            direct_result={"ok": True, "sha": "direct456"},
        )

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        self.assertEqual(result["sha"], "direct456")
        self.assertTrue(result["force_direct"])
        self.assertEqual(result["engineer_merge_mode"], "direct")
        self.assertIn("engineer_merge_mode='direct'", result["warning"])
        self.assertEqual(
            result["workflow_breach"]["subkind"],
            "merge_mode_locked",
        )
        self.assertIn("server_merge", [call[0] for call in worktree_mgr.calls])
        self.assertNotIn("create_pr", [call[0] for call in worktree_mgr.calls])

    async def test_worktree_merge_mode_direct_with_force_uses_direct_path(self):
        state, worker, _task = self._make_pr_merge_state()
        state.update_group_settings("g", engineer_merge_mode="direct")
        worktree_mgr = self._FakePrWorktreeManager(
            {"ok": True, "phase": "pr_merge"},
            direct_result={"ok": True, "sha": "direct789"},
        )

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "force_direct": True,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        self.assertEqual(result["sha"], "direct789")
        self.assertTrue(result["force_direct"])
        self.assertEqual(result["engineer_merge_mode"], "direct")
        self.assertNotIn("workflow_breach", result)
        self.assertIn("server_merge", [call[0] for call in worktree_mgr.calls])
        self.assertNotIn("create_pr", [call[0] for call in worktree_mgr.calls])

    async def test_worktree_merge_mode_engineer_choice_preserves_pr_default(self):
        state, worker, _task = self._make_pr_merge_state()
        state.update_group_settings("g", engineer_merge_mode="engineer-choice")
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": True,
            "phase": "pr_merge",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_commit_sha": "squash789",
            "merge_state": "CLEAN",
            "pending": False,
            "pr_status": {"ok": True, "state": "MERGED"},
        })

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "pull_request")
        self.assertIn("create_pr", [call[0] for call in worktree_mgr.calls])
        self.assertNotIn("server_merge", [call[0] for call in worktree_mgr.calls])

    async def test_worktree_merge_mode_engineer_choice_force_direct_stays_direct(self):
        state, worker, _task = self._make_pr_merge_state()
        state.update_group_settings("g", engineer_merge_mode="engineer-choice")
        worktree_mgr = self._FakePrWorktreeManager(
            {"ok": True, "phase": "pr_merge"},
            direct_result={"ok": True, "sha": "direct-choice"},
        )

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "force_direct": True,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        self.assertEqual(result["sha"], "direct-choice")
        self.assertTrue(result["force_direct"])
        self.assertEqual(
            result["workflow_breach"]["subkind"],
            "force_direct_merge",
        )
        self.assertIn("server_merge", [call[0] for call in worktree_mgr.calls])
        self.assertNotIn("create_pr", [call[0] for call in worktree_mgr.calls])

    async def test_worktree_merge_pr_uses_engineer_title_body_for_pr_and_squash(self):
        state, worker, _task = self._make_pr_merge_state()
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": True,
            "phase": "pr_merge",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_commit_sha": "squash789",
            "merge_state": "CLEAN",
            "pending": False,
            "pr_status": {"ok": True, "state": "MERGED"},
        })

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "pr_title": "Ship engineer-authored PR metadata",
                "pr_body": (
                    "Implements TORQUE:497 by passing authored PR metadata "
                    "through merge."
                ),
                "close_agent_on_merge": True,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        create_calls = [call for call in worktree_mgr.calls
                        if call[0] == "create_pr"]
        self.assertEqual(create_calls[-1][4],
                         "Ship engineer-authored PR metadata")
        self.assertEqual(
            create_calls[-1][5],
            "Implements TORQUE:497 by passing authored PR metadata through merge.",
        )
        merge_calls = [call for call in worktree_mgr.calls
                       if call[0] == "merge_pr"]
        self.assertEqual(merge_calls[-1][5],
                         "Ship engineer-authored PR metadata")
        self.assertEqual(
            merge_calls[-1][6],
            (
                "Implements TORQUE:497 by passing authored PR metadata "
                "through merge.\n\n"
                "PR: https://github.com/acme/repo/pull/7"
            ),
        )

    async def test_worktree_merge_pr_falls_back_to_generated_title_body(self):
        state, worker, _task = self._make_pr_merge_state()
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": True,
            "phase": "pr_merge",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_commit_sha": "squash789",
            "merge_state": "CLEAN",
            "pending": False,
            "pr_status": {"ok": True, "state": "MERGED"},
        })

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "close_agent_on_merge": True,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        create_calls = [call for call in worktree_mgr.calls
                        if call[0] == "create_pr"]
        self.assertEqual(create_calls[-1][4], "Implement worker change")
        self.assertNotEqual(create_calls[-1][4], "Squash merge: torque/worker")
        self.assertEqual(create_calls[-1][5], "- Implement worker change")
        merge_calls = [call for call in worktree_mgr.calls
                       if call[0] == "merge_pr"]
        self.assertEqual(merge_calls[-1][5], "Implement worker change")
        self.assertNotEqual(merge_calls[-1][5], "Squash merge: torque/worker")
        self.assertEqual(
            merge_calls[-1][6],
            "- Implement worker change\n\n"
            "PR: https://github.com/acme/repo/pull/7",
        )

    async def test_worktree_merge_pr_fallback_title_uses_done_task_title(self):
        state, worker, task = self._make_pr_merge_state()
        task.lane = "Done"
        task.messages.append({
            "action": "done",
            "message": "Implemented the worker change.",
        })
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": True,
            "phase": "pr_merge",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_commit_sha": "squash789",
            "merge_state": "CLEAN",
            "pending": False,
            "pr_status": {"ok": True, "state": "MERGED"},
        })

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "close_agent_on_merge": True,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        create_call = [call for call in worktree_mgr.calls
                       if call[0] == "create_pr"][-1]
        merge_call = [call for call in worktree_mgr.calls
                      if call[0] == "merge_pr"][-1]
        self.assertEqual(create_call[4], "Ship PR merged change")
        self.assertNotEqual(create_call[4], "Squash merge: torque/worker")
        self.assertEqual(
            create_call[5],
            "- Ship PR merged change\n  Implemented the worker change.",
        )
        self.assertEqual(merge_call[5], "Ship PR merged change")
        self.assertNotEqual(merge_call[5], "Squash merge: torque/worker")

    async def test_worktree_merge_pr_mixes_provided_and_generated_fields(self):
        async def run_merge(payload):
            state, worker, _task = self._make_pr_merge_state()
            worktree_mgr = self._FakePrWorktreeManager({
                "ok": True,
                "phase": "pr_merge",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "head_sha": "head123",
                "merge_commit_sha": "squash789",
                "merge_state": "CLEAN",
                "pending": False,
                "pr_status": {"ok": True, "state": "MERGED"},
            })

            async def fake_cleanup_after_merge(*_args, **_kwargs):
                return {"errors": []}

            handle_command, restore = self._pr_handle_command(
                state,
                worker,
                worktree_mgr,
                fake_cleanup_after_merge,
            )
            try:
                result = await handle_command({
                    "cmd": "worktree_merge",
                    "id": worker.id,
                    "close_agent_on_merge": True,
                    **payload,
                })
            finally:
                restore()
            self.assertTrue(result["ok"])
            create_call = [call for call in worktree_mgr.calls
                           if call[0] == "create_pr"][-1]
            merge_call = [call for call in worktree_mgr.calls
                          if call[0] == "merge_pr"][-1]
            return create_call, merge_call

        create_call, merge_call = await run_merge({
            "pr_title": "Ship authored PR title",
        })
        self.assertEqual(create_call[4], "Ship authored PR title")
        self.assertEqual(create_call[5], "- Implement worker change")
        self.assertEqual(merge_call[5], "Ship authored PR title")
        self.assertEqual(
            merge_call[6],
            "- Implement worker change\n\n"
            "PR: https://github.com/acme/repo/pull/7",
        )

        create_call, merge_call = await run_merge({
            "pr_body": "Covers TORQUE:497 and regression tests.",
        })
        self.assertEqual(create_call[4], "Implement worker change")
        self.assertNotEqual(create_call[4], "Squash merge: torque/worker")
        self.assertEqual(
            create_call[5],
            "Covers TORQUE:497 and regression tests.",
        )
        self.assertEqual(merge_call[5], "Implement worker change")
        self.assertNotEqual(merge_call[5], "Squash merge: torque/worker")
        self.assertEqual(
            merge_call[6],
            "Covers TORQUE:497 and regression tests.\n\n"
            "PR: https://github.com/acme/repo/pull/7",
        )

    async def test_worktree_merge_pr_success_finalizes_with_github_squash_sha(self):
        state, worker, task = self._make_pr_merge_state()
        cleanup_calls = []
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": True,
            "phase": "pr_merge",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_commit_sha": "squash789",
            "merge_state": "CLEAN",
            "pending": False,
            "pr_status": {"ok": True, "state": "MERGED"},
        })

        async def fake_cleanup_after_merge(
            _cell,
            *,
            close_agent=False,
            remove_worktree=False,
        ):
            cleanup_calls.append((close_agent, remove_worktree))
            return {
                "close_agent": close_agent,
                "remove_worktree": remove_worktree,
                "agent_closed": close_agent,
                "worktree_removed": remove_worktree,
                "errors": [],
            }

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            })
        finally:
            restore()

        self.assertEqual(result["type"], "worktree_merge")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "pull_request")
        self.assertEqual(result["sha"], "squash789")
        self.assertEqual(result["pr_url"], "https://github.com/acme/repo/pull/7")
        self.assertEqual(cleanup_calls, [(True, True)])
        boundary = task.worktree_boundary
        self.assertEqual(boundary["status"], "merged")
        self.assertEqual(boundary["merge_commit_sha"], "squash789")
        self.assertNotIn("pull_request", boundary)
        self.assertEqual(boundary["pr"]["state"], "merged")
        self.assertEqual(boundary["pr"]["merge_commit_sha"], "squash789")
        self.assertEqual(boundary["pr"]["requested_cleanup"], {
            "close_agent_on_merge": True,
            "remove_worktree_on_merge": True,
            "auto_move_to_done": True,
            "preserve_merge_diff": False,
        })
        self.assertEqual(task.lane, "Done")
        self.assertEqual(task.agent_id, "")
        self.assertEqual(worktree_mgr.sync_calls, 2)

    async def test_worktree_merge_pr_reports_auto_force_push_retry(self):
        state, worker, _task = self._make_pr_merge_state()
        worktree_mgr = self._FakePrWorktreeManager(
            {
                "ok": True,
                "phase": "pr_merge",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "head_sha": "head123",
                "merge_commit_sha": "squash789",
                "merge_state": "CLEAN",
                "pending": False,
                "pr_status": {"ok": True, "state": "MERGED"},
            },
            push_result={
                "ok": False,
                "phase": "push_branch",
                "error": "rejected (non-fast-forward)",
            },
            force_retry_result={
                "ok": True,
                "phase": "push_branch",
                "remote": "origin",
                "branch": worker.worktree_branch,
                "base_branch": "main",
                "auto_force_push": True,
                "force_with_lease": True,
                "auto_force_reason": "remote_merged_to_base",
                "remote_sha": "oldremote123",
                "local_sha": "local456",
                "base_sha": "main789",
                "force_lease_ref": "refs/heads/torque/worker",
                "force_lease_sha": "oldremote123",
            },
        )

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
            })
        finally:
            restore()

        self.assertTrue(result["ok"])
        self.assertTrue(result["auto_force_push"])
        self.assertEqual(result["push"]["reason"], "remote_merged_to_base")
        self.assertTrue(result["push"]["force_with_lease"])
        self.assertEqual(result["push"]["force_lease_sha"], "oldremote123")
        call_names = [call[0] for call in worktree_mgr.calls]
        self.assertIn("push", call_names)
        self.assertIn("force_push_retry", call_names)
        retry_call = [
            call for call in worktree_mgr.calls
            if call[0] == "force_push_retry"
        ][0]
        self.assertEqual(retry_call[4], "main")
        self.assertIn("non-fast-forward", retry_call[5]["error"])

    async def test_worktree_merge_pr_surfaces_auto_force_safety_refusal(self):
        state, worker, _task = self._make_pr_merge_state()
        refusal = (
            "Local branch does not include the current remote base tip; "
            "refusing auto force-push."
        )
        worktree_mgr = self._FakePrWorktreeManager(
            {"ok": True, "phase": "pr_merge"},
            push_result={
                "ok": False,
                "phase": "push_branch",
                "error": "rejected (non-fast-forward)",
            },
            force_retry_result={
                "ok": False,
                "phase": "push_branch",
                "error": refusal,
                "non_fast_forward": True,
                "auto_force_push": False,
                "safety_gate_passed": False,
                "auto_force_safety": {"ok": False, "error": refusal},
            },
        )

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            return {"errors": []}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
            })
        finally:
            restore()

        self.assertFalse(result["ok"])
        # Operator sees both the original push rejection AND why the safe
        # force-with-lease retry was declined.
        self.assertIn("non-fast-forward", result["error"])
        self.assertIn("Auto force-with-lease refused", result["error"])
        self.assertIn(refusal, result["error"])
        self.assertEqual(result["auto_force_refusal"], refusal)
        call_names = [call[0] for call in worktree_mgr.calls]
        self.assertIn("force_push_retry", call_names)

    async def test_worktree_merge_pr_pending_stores_metadata_without_cleanup(self):
        state, worker, task = self._make_pr_merge_state()
        queued = state.board_add_task(
            "Queued followup",
            "g",
            lane="To Do",
            id="TORQUE:491",
            agent_id=worker.id,
        )
        state.auto_dispatch_queue_add(
            "g",
            queued.id,
            target_agent_id=worker.id,
            max_concurrent=1,
        )
        cleanup_calls = []
        dispatch_calls = []

        def merge_result(auto):
            if not auto:
                return {
                    "ok": False,
                    "phase": "pr_merge",
                    "error": "merge blocked by required checks",
                    "url": "https://github.com/acme/repo/pull/7",
                    "number": 7,
                    "head_sha": "head123",
                    "merge_state": "BLOCKED",
                    "pending": False,
                    "pr_status": {
                        "ok": True,
                        "state": "OPEN",
                        "merge_state": "BLOCKED",
                    },
                }
            return {
                "ok": True,
                "phase": "pr_merge",
                "url": "https://github.com/acme/repo/pull/7",
                "number": 7,
                "head_sha": "head123",
                "merge_state": "BLOCKED",
                "pending": True,
                "pr_status": {
                    "ok": True,
                    "state": "OPEN",
                    "merge_state": "BLOCKED",
                },
            }

        worktree_mgr = self._FakePrWorktreeManager(merge_result)

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            cleanup_calls.append(dict(_kwargs))
            return {}

        async def nested_dispatch(payload):
            dispatch_calls.append(dict(payload))
            return {"type": "ok"}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
            nested_dispatch=nested_dispatch,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            })
        finally:
            restore()

        self.assertEqual(result["type"], "worktree_merge")
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertFalse(result["merged"])
        self.assertEqual(cleanup_calls, [])
        self.assertEqual(dispatch_calls, [])
        self.assertEqual(task.lane, "In Progress")
        self.assertEqual(task.agent_id, worker.id)
        self.assertEqual(queued.lane, "To Do")
        boundary = task.worktree_boundary
        self.assertEqual(boundary["status"], "open")
        self.assertNotIn("pull_request", boundary)
        self.assertNotIn("pr_url", boundary)
        self.assertEqual(
            boundary["pr"]["url"],
            "https://github.com/acme/repo/pull/7",
        )
        self.assertEqual(boundary["pr"]["state"], "auto_merge_enabled")
        self.assertEqual(boundary["pr"]["merge_state"], "BLOCKED")
        self.assertEqual(boundary["pr"]["requested_cleanup"], {
            "close_agent_on_merge": True,
            "remove_worktree_on_merge": True,
            "auto_move_to_done": True,
            "preserve_merge_diff": False,
        })
        self.assertEqual(worktree_mgr.sync_calls, 1)
        merge_calls = [call for call in worktree_mgr.calls
                       if call[0] == "merge_pr"]
        self.assertEqual([call[4] for call in merge_calls], [False, True])

    async def test_worktree_merge_pr_failure_keeps_boundary_and_returns_url(self):
        state, worker, task = self._make_pr_merge_state()
        cleanup_calls = []
        worktree_mgr = self._FakePrWorktreeManager({
            "ok": False,
            "phase": "pr_merge",
            "error": "merge conflict on GitHub",
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "head_sha": "head123",
            "merge_state": "DIRTY",
            "pending": False,
            "pr_status": {
                "ok": True,
                "state": "OPEN",
                "merge_state": "DIRTY",
            },
        })

        async def fake_cleanup_after_merge(*_args, **_kwargs):
            cleanup_calls.append(dict(_kwargs))
            return {}

        handle_command, restore = self._pr_handle_command(
            state,
            worker,
            worktree_mgr,
            fake_cleanup_after_merge,
        )
        try:
            result = await handle_command({
                "cmd": "worktree_merge",
                "id": worker.id,
                "close_agent_on_merge": True,
                "remove_worktree_on_merge": True,
            })
        finally:
            restore()

        self.assertEqual(result["type"], "worktree_merge")
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "pull_request")
        self.assertEqual(result["url"], "https://github.com/acme/repo/pull/7")
        self.assertIn("merge conflict", result["error"])
        self.assertEqual(cleanup_calls, [])
        self.assertEqual(worker.worktree_path, "/tmp/worker")
        boundary = task.worktree_boundary
        self.assertEqual(boundary["status"], "open")
        self.assertEqual(boundary["merge_commit_sha"], "")
        self.assertNotIn("pull_request", boundary)
        self.assertEqual(boundary["pr"]["state"], "blocked")
        self.assertEqual(boundary["pr"]["merge_state"], "DIRTY")
        merge_calls = [call for call in worktree_mgr.calls
                       if call[0] == "merge_pr"]
        self.assertEqual([call[4] for call in merge_calls], [False])


class ServerAutoCloseOnDoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def _write_action(self, base_dir, name, *,
                      auto_close_on_done=False) -> None:
        import os

        action_path = os.path.join(base_dir, ".torque", "actions",
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

    async def test_auto_close_on_done_skips_same_agent_queue_and_engineer_followups(self):
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
            "Engineer: need reply",
            "g",
            lane="Backlog",
            id="task-reply",
            reply_agent_id=reviewer.id,
            labels=["torque:engineer-message"],
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self, *, verdict_message):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        impl = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            worktree_path="/repo/.torque/worktrees/impl",
            worktree_branch="torque/impl",
            worktree_repo_root="/repo",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            worktree_path="/repo/.torque/worktrees/review",
            worktree_branch="torque/review",
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

    def test_review_verdict_parser_pins_drift_cases(self):
        cases = [
            ("Ship", "ship"),
            ("ship", "ship"),
            ("SHIP", "ship"),
            ("ship with fixes", "ship_with_fixes"),
            ("SHIP WITH FIXES", "ship_with_fixes"),
            ("- Ship", "ship"),
            ("## Verdict: Ship", "ship"),
            ("## Ship verdict: Ship", "ship"),
            (
                "Review notes\n\nVerdict: Needs changes",
                "needs_rework",
            ),
            (
                "Review notes\n\nAfter review I believe this should Ship",
                "",
            ),
            (
                "Approved, looks good to me",
                "",
            ),
            (
                "LGTM",
                "",
            ),
        ]

        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    self.server_mod._review_verdict_from_message(message),
                    expected,
                )

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
            "Approved, looks good to me",
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_agent_mod = importlib.import_module("torque.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            cell_type="agent",
        )
        state.agents[engineer.id] = engineer
        state.groups["g"] = [engineer.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
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

    async def test_pump_auto_dispatch_queue_forwards_engineer_owner_on_new_agents(self):
        state = self._make_state()
        task = state.board_add_task("Queued task", "g", id="task-1")
        self.assertIsNotNone(task)
        state.auto_dispatch_queue_add(
            "g",
            "task-1",
            max_concurrent=1,
            engineer_owner_id="engineer-1",
        )

        async def handle_command(payload):
            self.assertEqual(payload["cmd"], "dispatch_task")
            self.assertTrue(payload["create_agent"])
            self.assertEqual(payload["_created_by_engineer_id"], "engineer-1")
            queued_task = state.board_tasks[payload["id"]]
            agent = self.state_mod.AgentCell(
                id="agent-1",
                name="worker",
                group="g",
                cell_type="agent",
                current_task_id=queued_task.id,
                created_by_engineer_id=payload["_created_by_engineer_id"],
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
            state.agents["agent-1"].created_by_engineer_id,
            "engineer-1",
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
            agent = state.agents[payload["agent_id"]]
            active = state.agent_current_task(agent.id)
            if active and active.id != task.id:
                task.agent_id = agent.id
                task.lane = "To Do"
                return {
                    "type": "queued",
                    "task_id": task.id,
                    "agent_id": agent.id,
                }
            task.agent_id = agent.id
            task.lane = "In Progress"
            agent.current_task_id = task.id
            return None

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(
            [item["task_id"] for item in dispatched],
            ["task-1"],
        )
        self.assertEqual(state.board_tasks["task-1"].agent_id, "agent-1")
        self.assertEqual(state.board_tasks["task-2"].agent_id, "")
        self.assertEqual(state.board_tasks["task-2"].lane, "Backlog")
        self.assertEqual(
            [entry.task_id for entry in state.auto_dispatch_queues["g"]],
            ["task-2"],
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].target_agent_id,
            "agent-1",
        )

        state.agents["agent-1"].current_task_id = ""
        state.board_tasks["task-1"].lane = "Done"

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(
            [item["task_id"] for item in dispatched],
            ["task-2"],
        )
        self.assertEqual(state.board_tasks["task-2"].agent_id, "agent-1")
        self.assertEqual(state.board_tasks["task-2"].lane, "In Progress")
        self.assertNotIn("g", state.auto_dispatch_queues)

    async def test_pump_promotes_same_agent_queue_one_task_at_a_time(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            session_id="sess-1",
            current_task_id="task-a",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        active = state.board_add_task(
            "Active",
            "g",
            lane="In Progress",
            id="task-a",
            agent_id=worker.id,
        )
        first = state.board_add_task(
            "First queued followup",
            "g",
            lane="To Do",
            id="task-b",
            agent_id=worker.id,
        )
        second = state.board_add_task(
            "Second queued followup",
            "g",
            lane="To Do",
            id="task-c",
            agent_id=worker.id,
        )
        self.assertIsNotNone(active)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        state.auto_dispatch_queue_add(
            "g", first.id, target_agent_id=worker.id, max_concurrent=1,
        )
        state.auto_dispatch_queue_add(
            "g", second.id, target_agent_id=worker.id, max_concurrent=1,
        )
        calls = []

        async def handle_command(payload):
            calls.append(dict(payload))
            task = state.board_tasks[payload["id"]]
            agent = state.agents[payload["agent_id"]]
            active_task = state.agent_current_task(agent.id)
            if active_task and active_task.id != task.id:
                task.agent_id = agent.id
                task.lane = "To Do"
                state.auto_dispatch_queue_add(
                    task.group,
                    task.id,
                    target_agent_id=agent.id,
                    max_concurrent=1,
                )
                return {
                    "type": "queued",
                    "task_id": task.id,
                    "agent_id": agent.id,
                }
            task.agent_id = agent.id
            task.lane = "In Progress"
            agent.current_task_id = task.id
            return {"type": "ok", "task_id": task.id, "agent_id": agent.id}

        worker.current_task_id = ""
        active.lane = "Done"

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual([call["id"] for call in calls], ["task-b"])
        self.assertEqual(
            [item["task_id"] for item in dispatched],
            ["task-b"],
        )
        self.assertEqual(worker.current_task_id, "task-b")
        self.assertEqual(first.lane, "In Progress")
        self.assertEqual(second.lane, "To Do")
        self.assertEqual(
            [entry.task_id for entry in state.auto_dispatch_queues["g"]],
            ["task-c"],
        )

        calls.clear()
        worker.current_task_id = ""
        first.lane = "Done"

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual([call["id"] for call in calls], ["task-c"])
        self.assertEqual(
            [item["task_id"] for item in dispatched],
            ["task-c"],
        )
        self.assertEqual(worker.current_task_id, "task-c")
        self.assertEqual(second.lane, "In Progress")
        self.assertNotIn("g", state.auto_dispatch_queues)

    async def test_pump_targeted_idle_agent_still_respects_other_active_cap(self):
        state = self._make_state()
        target = self.state_mod.AgentCell(
            id="agent-idle",
            name="Idle target",
            group="g",
            cell_type="agent",
            session_id="sess-idle",
        )
        busy = self.state_mod.AgentCell(
            id="agent-busy",
            name="Busy worker",
            group="g",
            cell_type="agent",
            session_id="sess-busy",
            current_task_id="task-active",
        )
        state.agents[target.id] = target
        state.agents[busy.id] = busy
        state.groups["g"].extend([target.id, busy.id])
        active = state.board_add_task(
            "Other active task",
            "g",
            lane="In Progress",
            id="task-active",
            agent_id=busy.id,
        )
        queued = state.board_add_task(
            "Queued target task",
            "g",
            lane="To Do",
            id="task-queued",
            agent_id=target.id,
        )
        self.assertIsNotNone(active)
        self.assertIsNotNone(queued)
        state.auto_dispatch_queue_add(
            "g",
            queued.id,
            target_agent_id=target.id,
            max_concurrent=1,
        )

        async def handle_command(payload):
            raise AssertionError(f"dispatch should defer: {payload}")

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(
            [entry.task_id for entry in state.auto_dispatch_queues["g"]],
            [queued.id],
        )
        self.assertEqual(queued.lane, "To Do")
        self.assertEqual(target.current_task_id, "")

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

        # The queued follow-up is pre-bound to its target, but the target is
        # still running another task. The pump must leave the entry queued
        # until the target clears its current task.
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "queued", "task_id": payload["id"],
                    "agent_id": payload.get("agent_id", "")}

        dispatched = await self.server_mod._pump_auto_dispatch_queue(
            state,
            handle_command,
            lambda *args, **kwargs: None,
            group="g",
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(calls, [])
        self.assertIn("g", state.auto_dispatch_queues)
        self.assertEqual(
            [entry.task_id for entry in state.auto_dispatch_queues["g"]],
            ["task-queued"],
        )

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

        server_dispatch = importlib.import_module("torque.server_dispatch")
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

    async def test_new_agent_prompt_sequence_anchors_first_prompt_when_cell_known(self):
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Panelsmith",
            group="g",
            cell_type="agent",
            kind="worker",
        )

        prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": "Template intro"},
            startup_prompt="Persistent worker prompt",
            final_prompt="Dispatch task body",
            cell=cell,
        )

        self.assertEqual(
            prompts[0],
            (
                "You are Panelsmith (worker, id=worker-1).\n\n"
                "Persistent worker prompt",
                {},
            ),
        )
        self.assertEqual(
            prompts[1][0],
            "You are Panelsmith (worker, id=worker-1).\n\nTemplate intro",
        )
        self.assertEqual(
            prompts[2][0],
            "You are Panelsmith (worker, id=worker-1).\n\nDispatch task body",
        )

    async def test_identity_anchor_cadence_launch_dispatch_split_and_session_reset(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            guidance_hint_cadence=4,
        )
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Panelsmith",
            group="g",
            cell_type="agent",
            kind="worker",
            session_id="session-1",
        )

        launch_due = state.should_show_guidance_hint(
            self.server_mod.GUIDANCE_HINT_IDENTITY_LAUNCH,
            cell,
        )
        launch_prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": "Template intro"},
            startup_prompt="Persistent worker prompt",
            cell=cell,
            include_identity_anchor=launch_due,
        )
        self.assertTrue(launch_due)
        self.assertIn("You are Panelsmith", launch_prompts[0][0])
        self.assertIn("You are Panelsmith", launch_prompts[1][0])

        launch_due = state.should_show_guidance_hint(
            self.server_mod.GUIDANCE_HINT_IDENTITY_LAUNCH,
            cell,
        )
        launch_prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": "Template intro"},
            startup_prompt="Persistent worker prompt",
            cell=cell,
            include_identity_anchor=launch_due,
        )
        self.assertFalse(launch_due)
        self.assertNotIn("You are Panelsmith", launch_prompts[0][0])
        self.assertNotIn("You are Panelsmith", launch_prompts[1][0])

        dispatch_due = state.should_show_guidance_hint(
            self.server_mod.GUIDANCE_HINT_IDENTITY_DISPATCH,
            cell,
        )
        dispatch_prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=None,
            cell=cell,
            prompt_body="Implement feature",
            postscript="TORQUE POSTSCRIPT",
            include_identity_anchor=dispatch_due,
        )
        self.assertTrue(dispatch_due)
        self.assertTrue(dispatch_prompt.startswith("You are Panelsmith"))

        dispatch_due = state.should_show_guidance_hint(
            self.server_mod.GUIDANCE_HINT_IDENTITY_DISPATCH,
            cell,
        )
        dispatch_prompt = self.server_mod._assemble_worker_prompt(
            role_mgr=None,
            cell=cell,
            prompt_body="Implement feature",
            postscript="TORQUE POSTSCRIPT",
            include_identity_anchor=dispatch_due,
        )
        self.assertFalse(dispatch_due)
        self.assertEqual(dispatch_prompt, "Implement feature\n\nTORQUE POSTSCRIPT\n")

        cell.session_id = "session-2"
        self.assertTrue(
            state.should_show_guidance_hint(
                self.server_mod.GUIDANCE_HINT_IDENTITY_LAUNCH,
                cell,
            )
        )
        self.assertTrue(
            state.should_show_guidance_hint(
                self.server_mod.GUIDANCE_HINT_IDENTITY_DISPATCH,
                cell,
            )
        )

    async def test_new_agent_prompt_sequence_uses_default_nudge_when_initial_prompt_empty(self):
        """TORQUE:263 — empty initial_prompt + default_boot_nudge synthesizes the
        default kickoff so architect/engineer agents don't sit idle on boot."""
        cell = self.state_mod.AgentCell(
            id="eng-1",
            name="Alice",
            group="g",
            cell_type="agent",
            kind="engineer",
        )

        prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": ""},
            cell=cell,
            default_boot_nudge="Wake. Run engineer wake protocol.",
        )

        self.assertEqual(len(prompts), 1)
        self.assertEqual(
            prompts[0][0],
            "You are Alice (engineer, id=eng-1).\n\n"
            "Wake. Run engineer wake protocol.",
        )

    async def test_new_agent_prompt_sequence_uses_default_when_initial_prompt_whitespace(self):
        """Whitespace-only initial_prompt is treated as empty for the fallback."""
        cell = self.state_mod.AgentCell(
            id="arch-1",
            name="Sage",
            group="g",
            cell_type="agent",
            kind="architect",
        )

        prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": "   \n  "},
            cell=cell,
            default_boot_nudge="Wake. Run boot protocol.",
        )

        self.assertEqual(len(prompts), 1)
        self.assertIn("Wake. Run boot protocol.", prompts[0][0])

    async def test_new_agent_prompt_sequence_initial_prompt_overrides_default(self):
        """Configured initial_prompt must take precedence over default_boot_nudge.

        Regression guard for TORQUE:263 — the default must NOT fire when the role
        config has set a real initial_prompt (TORQUE:259/:261 must keep working).
        """
        cell = self.state_mod.AgentCell(
            id="eng-1",
            name="Alice",
            group="g",
            cell_type="agent",
            kind="engineer",
        )

        prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": "Custom kickoff."},
            cell=cell,
            default_boot_nudge="Wake. Run engineer wake protocol.",
        )

        self.assertEqual(len(prompts), 1)
        self.assertIn("Custom kickoff.", prompts[0][0])
        self.assertNotIn("Wake.", prompts[0][0])

    async def test_new_agent_prompt_sequence_no_default_means_no_fallback(self):
        """Empty initial_prompt + no default_boot_nudge -> no kickoff turn."""
        prompts = self.server_mod._new_agent_prompt_sequence(
            {"initial_prompt": ""},
            startup_prompt="Persistent prompt",
        )

        self.assertEqual(prompts, [("Persistent prompt", {})])

    def test_resolve_default_boot_nudge_for_kinds(self):
        """Architect/engineer return their nudge; workers/terminals do not."""
        state = self.state_mod.MatrixState()

        architect = self.state_mod.AgentCell(
            id="arch-1", name="Sage", group="g",
            cell_type="agent", kind="architect",
        )
        engineer = self.state_mod.AgentCell(
            id="eng-1", name="Alice", group="g",
            cell_type="agent", kind="engineer",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1", name="Panelsmith", group="g",
            cell_type="agent", kind="worker",
        )
        terminal = self.state_mod.AgentCell(
            id="term-1", name="Term", group="g",
            cell_type="terminal", kind="",
        )

        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, architect),
            state.global_settings.architect_default_boot_nudge,
        )
        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, engineer),
            state.global_settings.engineer_default_boot_nudge,
        )
        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, worker),
            "",
        )
        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, terminal),
            "",
        )
        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, None),
            "",
        )
        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(None, engineer),
            "",
        )

    def test_resolve_default_boot_nudge_respects_global_settings_override(self):
        """User overrides in GlobalSettings flow through to resolution."""
        state = self.state_mod.MatrixState()
        state.global_settings.architect_default_boot_nudge = "Custom architect."
        state.global_settings.engineer_default_boot_nudge = ""

        architect = self.state_mod.AgentCell(
            id="arch-1", name="Sage", group="g",
            cell_type="agent", kind="architect",
        )
        engineer = self.state_mod.AgentCell(
            id="eng-1", name="Alice", group="g",
            cell_type="agent", kind="engineer",
        )

        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, architect),
            "Custom architect.",
        )
        self.assertEqual(
            self.server_agent_mod.resolve_default_boot_nudge(state, engineer),
            "",
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
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="torque/worker",
        )
        state.agents[owner.id] = owner
        state.groups["g"].append(owner.id)

        product = state.board_add_task(
            "Add Events tab",
            "g",
            lane="Done",
            id="TORQUE:1",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="In Progress",
            id="TORQUE:1:1",
            parent_task_id="TORQUE:1",
            pipeline_root_id="TORQUE:1",
            pipeline_depth=1,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="TORQUE:2",
            agent_id=owner.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="TORQUE:1",
        )
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/worker",
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
        state.update_engineer_settings("g", autonomy_mode="suggest_only")
        owner = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="torque/worker",
        )
        state.agents[owner.id] = owner
        state.groups["g"].append(owner.id)

        product = state.board_add_task(
            "Add Events tab",
            "g",
            lane="Done",
            id="TORQUE:1",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="In Progress",
            id="TORQUE:1:1",
            parent_task_id="TORQUE:1",
            pipeline_root_id="TORQUE:1",
            pipeline_depth=1,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="TORQUE:2",
            agent_id=owner.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="TORQUE:1",
        )
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/worker",
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
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="torque/worker",
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
            id="TORQUE:1",
            agent_id=owner.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "g",
            lane="Done",
            id="TORQUE:1:1",
            parent_task_id="TORQUE:1",
            pipeline_root_id="TORQUE:1",
            pipeline_depth=1,
            agent_id=owner.id,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="TORQUE:2",
            agent_id=owner.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="TORQUE:1:1",
            depends_on=["EXT:1"],
        )
        self.assertIsNotNone(external)
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/worker",
            "status": "open",
            "recorded_at": "2026-04-07T10:00:00+00:00",
            "commit_sha": "impl123",
            "recorded_by_agent_id": owner.id,
        }
        review.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/worker",
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
        engineer_b = self.state_mod.AgentCell(
            id="engineer-b",
            name="Engineer B",
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
            worktree_path="/repo/.torque/worktrees/agent-b",
            worktree_repo_root="/repo",
            worktree_branch="torque/worker-b",
        )
        state.agents[engineer_b.id] = engineer_b
        state.agents[owner_b.id] = owner_b
        state.groups["B"] = [engineer_b.id, owner_b.id]
        state.group_settings["B"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer_b.id
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
            id="TORQUE:1",
            agent_id=owner_b.id,
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review Events implementation",
            "B",
            lane="Done",
            id="TORQUE:1:1",
            parent_task_id="TORQUE:1",
            pipeline_root_id="TORQUE:1",
            pipeline_depth=1,
            agent_id=owner_b.id,
            action_name="feature/review",
        )
        queued = state.board_add_task(
            "Add Worklog tab",
            "B",
            lane="To Do",
            id="TORQUE:2",
            agent_id=owner_b.id,
            action_name="feature/implement",
            resume_after_boundary_task_id="TORQUE:1:1",
            depends_on=["EXT:1"],
        )
        self.assertIsNotNone(external)
        self.assertIsNotNone(product)
        self.assertIsNotNone(review)
        self.assertIsNotNone(queued)
        product.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/worker-b",
            "status": "open",
            "recorded_at": "2026-04-07T10:00:00+00:00",
            "commit_sha": "impl123",
            "recorded_by_agent_id": owner_b.id,
        }
        review.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/worker-b",
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
            id="TORQUE:1:1",
            action_name="feature/review",
        )
        fix_task = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="TORQUE:1:2",
            parent_task_id="TORQUE:1:1",
            pipeline_root_id="TORQUE:1",
            pipeline_depth=1,
            action_name="feature/fix-review",
            labels=["torque:derived", "review-fix"],
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
            id="TORQUE:1:1",
            action_name="feature/review",
        )
        queued_product = state.board_add_task(
            "Add Worklog tab",
            "g",
            lane="To Do",
            id="TORQUE:2",
            parent_task_id="TORQUE:1:1",
            pipeline_root_id="TORQUE:1",
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
            labels=["torque:derived", "review-fix"],
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
        self.bridge_mod = importlib.import_module("torque.bridge")
        self.bridge_mod = importlib.reload(self.bridge_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_agent_mod = importlib.import_module("torque.server_agent")
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
