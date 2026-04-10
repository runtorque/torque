import asyncio
import importlib
import sys
import types
import unittest
from enum import Enum
from pathlib import Path
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

    def test_loom_system_prompt_prefers_mcp_reporting_tools(self):
        prompt_source = (Path(__file__).resolve().parents[1] / "loom" / "server.py").read_text()

        self.assertIn("Use the Loom MCP tools", prompt_source)
        self.assertIn("loom_done(message=", prompt_source)
        self.assertIn("loom_verify(state=", prompt_source)
        self.assertIn("blocking human decision or approval", prompt_source)
        self.assertIn("Do not use it for status updates or", prompt_source)
        self.assertIn("loom_context()", prompt_source)
        self.assertEqual(
            prompt_source.count("`loom_ask(question=\"question\", description=\"details\")`"),
            1,
        )
        self.assertNotIn("loom_a-", prompt_source)

    def test_dispatch_postscript_prefers_mcp_reporting_tools(self):
        prompt_source = (Path(__file__).resolve().parents[1] / "loom" / "server.py").read_text()

        self.assertIn("Report your progress with these Loom MCP tools", prompt_source)
        self.assertIn("loom_done(message=", prompt_source)
        self.assertIn("loom_derive(description=", prompt_source)
        self.assertIn("loom_verify(state=", prompt_source)
        self.assertIn("blocking human decision/approval only", prompt_source)
        self.assertIn("loom_blocked(reason=", prompt_source)
        self.assertEqual(
            prompt_source.count("`loom_ask(question=\\\"title\\\", description=\\\"details\\\")`"),
            1,
        )
        self.assertNotIn("loom_a-", prompt_source)

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


class ServerAgentPromptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        install_iterm2_stub()
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
