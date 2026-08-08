import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from enum import Enum


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


def _install_iterm2_stub():
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

    binding = types.ModuleType("iterm2.binding")
    keyboard = types.ModuleType("iterm2.keyboard")
    tool = types.SimpleNamespace(async_register_web_view_tool=None)
    keyboard.Modifier = Modifier
    keyboard.Keycode = Keycode
    iterm2.Connection = Connection
    iterm2.tool = tool
    iterm2.binding = binding
    iterm2.keyboard = keyboard
    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard


class ServerDeriveLifecycleTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        _install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress"
        )
        return state

    def test_review_to_fix_flow_frees_parent_slot_and_promotes_started_fix(self):
        state = self._make_state()
        implementer = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-root",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            current_task_id="task-review",
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )

        self.assertFalse(state.task_occupies_execution_slot(root, agent_id=implementer.id))
        self.assertEqual(state.agent_current_task(implementer.id).id, fix.id)

        self.server_mod._promote_task_for_active_report(
            state, implementer, fix
        )

        self.assertEqual(state.board_tasks[fix.id].lane, "In Progress")
        self.assertEqual(implementer.current_task_id, fix.id)
        self.assertTrue(state.agent_is_busy(implementer.id))

    def test_fix_to_rereview_reuses_prior_reviewer_stage(self):
        state = self._make_state()
        implementer = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            session_id="impl-session",
            status="running",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            status="idle",
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
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
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )

        reused = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, fix, "feature/review"
        )
        fresh = self.server_mod._nearest_ancestor_agent_for_action_stage(
            state, root, "feature/review"
        )

        self.assertIs(reused, reviewer)
        self.assertIsNone(fresh)

    def test_open_descendants_block_manual_completion(self):
        state = self._make_state()
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            parent_task_id=review.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )

        rejected = self.server_mod._reject_completion_with_open_descendants(
            state, root, "done"
        )
        self.assertEqual(rejected["type"], "error")

        state.board_move_task(fix.id, "Done")
        allowed = self.server_mod._reject_completion_with_open_descendants(
            state, root, "done"
        )

        self.assertIsNone(allowed)


class ServerReviewAgentReuseDeriveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        _install_iterm2_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    class _ActionManager:
        def get_transitions(self, action_name, _base_dir):
            if action_name == "feature/implement":
                return [{"action": "feature/review"}]
            if action_name == "feature/review":
                return [{"action": "feature/fix-review"}]
            return []

        def load_action(self, _action_name, _base_dir):
            return {}

        def list_actions(self, _base_dir):
            return []

        def get_auto_close_on_done(self, _action_name, *, base_dir=""):
            return False

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress"
        )
        return state

    def _make_agent(self, agent_id, *, status="idle", session_id=None,
                    current_task_id=""):
        return self.state_mod.AgentCell(
            id=agent_id,
            name=agent_id,
            slug=agent_id,
            group="g",
            cell_type="agent",
            status=status,
            session_id=(
                session_id if session_id is not None
                else f"{agent_id}-session"
            ),
            current_task_id=current_task_id,
            directory="/repo",
        )

    def _extract_handle_command(self, state, dispatch_handler,
                                worktree_mgr=None, action_mgr=None,
                                closure_overrides=None):
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

        async def resolve_base_dir(_group=""):
            return "/repo"

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_ownership_engineer_id_for_dispatch_source":
                lambda _cell: "",
            "_panel_event": lambda *args, **kwargs: None,
            "_resolve_base_dir": resolve_base_dir,
            "_runtime_payload": lambda: {},
            "action_mgr": action_mgr or self._ActionManager(),
            "bridge": DummyBridge(),
            "handle_command": dispatch_handler,
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
            "worktree_mgr": worktree_mgr,
        })
        if closure_overrides:
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

    def _dispatch_linker(self, state):
        main_code = self.server_mod.main.__code__
        linker_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "_record_task_dispatch"
        )
        closure = tuple(
            self._make_cell(state)
            for _name in linker_code.co_freevars
        )
        return types.FunctionType(
            linker_code,
            self.server_mod.__dict__,
            "_record_task_dispatch",
            None,
            closure,
        )

    def _latest_boundary_state(self, state, worktree_mgr):
        main_code = self.server_mod.main.__code__
        boundary_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "_latest_boundary_state_for_cell"
        )
        closure_values = {
            "_worktree_submodules_for_cell": lambda _cell: [],
            "state": state,
            "worktree_mgr": worktree_mgr,
        }
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in boundary_code.co_freevars
        )
        return types.FunctionType(
            boundary_code,
            self.server_mod.__dict__,
            "_latest_boundary_state_for_cell",
            None,
            closure,
        )

    def _boundary_reason_message(self):
        main_code = self.server_mod.main.__code__
        message_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "_boundary_reason_message"
        )
        return types.FunctionType(
            message_code,
            self.server_mod.__dict__,
            "_boundary_reason_message",
        )

    def test_dispatch_linker_keeps_lane_assignment_state_and_backlink_together(self):
        """The one-step re-dispatch contract must remain a single code path."""
        state = self._make_state()
        worker = self._make_agent("worker-1", status="running")
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        task = state.board_add_task(
            "Synthetic redispatch",
            "g",
            lane="Done",
            id="TASK-REDISPATCH",
            action_name="feature/implement",
        )
        self.assertIsNotNone(task)
        self.assertEqual(task.dispatch_state, "queued")

        self._dispatch_linker(state)(worker, task, "In Progress")

        self.assertEqual(task.lane, "In Progress")
        self.assertEqual(task.agent_id, worker.id)
        self.assertEqual(task.dispatch_state, "live")
        self.assertEqual(worker.current_task_id, task.id)

    async def test_dispatching_reviewed_pipeline_root_does_not_poison_merge_boundary(self):
        state = self._make_state()
        worker = self._make_agent("worker-1", status="idle")
        worker.worktree_path = "/repo/.torque/worktrees/worker-1"
        worker.worktree_repo_root = "/repo"
        worker.worktree_branch = "torque/worker-1"
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="TASK-ROOT",
            action_name="feature/implement",
        )
        superseded_review = state.board_add_task(
            "Earlier review round",
            "g",
            lane="Done",
            id="TASK-REVIEW-1",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            action_name="feature/review",
        )
        superseded_review.worktree_boundary = {
            "repo_root": "/repo",
            "branch": worker.worktree_branch,
            "status": "superseded",
            "recorded_at": "2026-08-08T09:00:00+00:00",
            "commit_sha": "earlier-head",
        }
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="TASK-REVIEW",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            action_name="feature/review",
        )
        review.worktree_boundary = {
            "repo_root": "/repo",
            "branch": worker.worktree_branch,
            "status": "open",
            "recorded_at": "2026-08-08T10:00:00+00:00",
            "commit_sha": "reviewed-head",
        }

        self._dispatch_linker(state)(worker, root, "In Progress")

        class WorktreeManager:
            async def current_head(self, _cell):
                return "reviewed-head"

        boundary_state = await self._latest_boundary_state(
            state,
            WorktreeManager(),
        )(worker)
        self.assertEqual(root.resume_after_boundary_task_id, "")
        self.assertEqual(boundary_state["reason"], "")
        self.assertEqual(boundary_state["clean"]["task_id"], review.id)

    def test_dispatching_genuine_started_followup_still_blocks_with_existing_message(self):
        state = self._make_state()
        worker = self._make_agent("worker-1", status="idle")
        worker.worktree_repo_root = "/repo"
        worker.worktree_branch = "torque/worker-1"
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="Done",
            id="TASK-ROOT",
            action_name="feature/implement",
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="TASK-REVIEW",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            action_name="feature/review",
        )
        review.worktree_boundary = {
            "repo_root": "/repo",
            "branch": worker.worktree_branch,
            "status": "open",
            "recorded_at": "2026-08-08T10:00:00+00:00",
            "commit_sha": "reviewed-head",
        }
        followup = state.board_add_task(
            "Implement genuine follow-up",
            "g",
            lane="To Do",
            id="TASK-FOLLOWUP",
            action_name="feature/implement",
        )

        self._dispatch_linker(state)(worker, followup, "In Progress")

        started = self.server_mod.started_successor_tasks(
            state.board_tasks.values(),
            review.id,
        )
        self.assertEqual([task.id for task in started], [followup.id])
        main_code = self.server_mod.main.__code__
        message_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "_boundary_reason_message"
        )
        reason_message = types.FunctionType(
            message_code,
            self.server_mod.__dict__,
            "_boundary_reason_message",
        )
        boundary = self.server_mod.boundary_summary(
            review,
            started_followers=started,
        )
        self.assertEqual(
            reason_message("started_successor", boundary),
            "Latest task boundary is no longer cleanly mergeable because "
            'follow-up task "Implement genuine follow-up" has already started.',
        )

    async def test_block_fix_cycle_can_continue_then_rebase_without_weakening_successor_gate(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            slug="engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
        )
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="TASK:1:3",
        )
        implementer.kind = "worker"
        implementer.owner_engineer_id = engineer.id
        implementer.created_by_engineer_id = engineer.id
        implementer.worktree_path = "/repo/.torque/worktrees/impl-1"
        implementer.worktree_repo_root = "/repo"
        implementer.git_root = "/repo"
        implementer.worktree_branch = "torque/engineer/impl-1"
        implementer.worktree_base_branch = "main"
        first_reviewer = self._make_agent("review-1")
        first_reviewer.kind = "worker"
        first_reviewer.owner_engineer_id = engineer.id
        second_reviewer = self._make_agent("review-2")
        second_reviewer.kind = "worker"
        second_reviewer.owner_engineer_id = engineer.id
        state.agents = {
            cell.id: cell
            for cell in (
                engineer, implementer, first_reviewer, second_reviewer
            )
        }
        state.groups["g"] = list(state.agents)

        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="TASK:1",
            action_name="feature/implement",
            agent_id=implementer.id,
            assigned_engineer_id=engineer.id,
        )
        shipped_review = state.board_add_task(
            "First review ships",
            "g",
            lane="Done",
            id="TASK:1:1",
            action_name="feature/review",
            agent_id=first_reviewer.id,
            assigned_engineer_id=engineer.id,
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            completion_evidence={
                "review": {
                    "verdict": "ship",
                    "agent_id": first_reviewer.id,
                    "recorded_at": "2026-08-08T10:00:00+00:00",
                },
            },
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": implementer.worktree_branch,
                "base_branch": "main",
                "commit_sha": "a" * 40,
                "status": "open",
                "recorded_at": "2026-08-08T10:00:00+00:00",
                "code_delta": {"state": "present"},
            },
        )
        blocking_review = state.board_add_task(
            "Independent review blocks",
            "g",
            lane="In Progress",
            id="TASK:1:2",
            action_name="feature/review",
            agent_id=second_reviewer.id,
            assigned_engineer_id=engineer.id,
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            resume_after_boundary_task_id=shipped_review.id,
            status="Fixing blockers",
            completion_evidence={
                "review": {
                    "verdict": "block",
                    "agent_id": second_reviewer.id,
                },
            },
        )
        fix = state.board_add_task(
            "Fix blockers",
            "g",
            lane="In Progress",
            id="TASK:1:3",
            action_name="feature/implement",
            agent_id=implementer.id,
            assigned_engineer_id=engineer.id,
            parent_task_id=blocking_review.id,
            pipeline_root_id=root.id,
            pipeline_depth=3,
        )
        state.pipeline_task_counters[root.id] = 4

        class WorktreeManager:
            def __init__(self):
                self.rebased = False
                self.stale = True
                self.classification = "ahead"

            async def current_head(self, _cell):
                return ("c" if self.rebased else "b") * 40

            async def boundary_tip_mismatch_info(
                    self, _cell, boundary_sha, tip_sha):
                return {
                    "classification": self.classification,
                    "commit_count": 1,
                    "boundary_sha": boundary_sha,
                    "tip_sha": tip_sha,
                }

            async def has_uncommitted_changes(self, _cell, **_kwargs):
                return False

            async def stale_base_info(self, _cell, **_kwargs):
                return {
                    "stale": self.stale and not self.rebased,
                    "branch_head": ("c" if self.rebased else "b") * 40,
                    "base_head": "d" * 40,
                }

            async def check_merge_conflicts(self, _cell, **_kwargs):
                return {"clean": True, "tree_sha": "", "conflicts": []}

        worktree_mgr = WorktreeManager()
        latest_boundary_state = self._latest_boundary_state(
            state, worktree_mgr
        )
        dispatch_calls, dispatch = self._recording_dispatch(state)

        async def record_task_boundary(task, cell, message=""):
            for older in self.server_mod.branch_boundary_tasks(
                state.board_tasks.values(),
                repo_root=implementer.worktree_repo_root,
                branch=implementer.worktree_branch,
                statuses={"open"},
            ):
                if older.id == task.id:
                    continue
                older.worktree_boundary = {
                    **older.worktree_boundary,
                    "status": "superseded",
                    "superseded_by_task_id": task.id,
                }
            task.worktree_boundary = {
                "version": "1",
                "repo_root": implementer.worktree_repo_root,
                "branch": implementer.worktree_branch,
                "base_branch": implementer.worktree_base_branch,
                "commit_sha": await worktree_mgr.current_head(cell),
                "status": "open",
                "recorded_at": "2026-08-08T12:00:00+00:00",
                "recorded_by_agent_id": cell.id,
                "message": message,
                "code_delta": {"state": "present"},
            }
            return dict(task.worktree_boundary)

        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=worktree_mgr,
            closure_overrides={
                "_boundary_reason_message": self._boundary_reason_message(),
                "_latest_boundary_state_for_cell": latest_boundary_state,
                "_record_task_boundary": record_task_boundary,
                "_worktree_submodules_for_cell": lambda _cell: [],
                "_broadcast_toast": lambda *_args, **_kwargs: None,
            },
        )
        calls = []

        async def tracing_handle(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_diff":
                return {"type": "ok", "summary": {},
                        "stale_base": {"stale": True}}
            if payload["cmd"] == "worktree_rebase":
                worktree_mgr.rebased = True
                return {"type": "worktree_rebase", "ok": True}
            return await handle_command(payload)

        mcp_engineer = importlib.reload(
            importlib.import_module("torque.mcp_engineer")
        )
        rebase_text, rebase_error = await (
            mcp_engineer._dispatch_engineer_tool(
                "engineer_rebase",
                {"agent": implementer.id},
                tracing_handle,
                state,
                caller_id=engineer.id,
            )
        )

        self.assertTrue(rebase_error)
        self.assertIn("review_cycle_continue", rebase_text)
        self.assertNotIn(
            "worktree_rebase", [call["cmd"] for call in calls]
        )
        first_check = calls[0]
        self.assertEqual(first_check["cmd"], "worktree_check_merge")
        boundary_state = await latest_boundary_state(implementer)
        self.assertEqual(boundary_state["reason"], "started_successor")
        boundary = boundary_state["latest"]
        self.assertEqual(boundary["task_id"], shipped_review.id)
        self.assertEqual(boundary["head_sha"], "b" * 40)
        self.assertEqual(
            boundary["boundary_tip_mismatch"]["classification"], "ahead"
        )
        self.assertEqual(
            [item["task_id"] for item in boundary["started_followers"]],
            [blocking_review.id],
        )

        worktree_mgr.stale = False
        generic_check = await tracing_handle({
            "cmd": "worktree_check_merge",
            "id": implementer.id,
            "allow_stale_base": True,
        })
        self.assertEqual(
            generic_check["error"],
            "Latest task boundary is no longer cleanly mergeable because "
            'follow-up task "Independent review blocks" has already started.',
        )
        worktree_mgr.stale = True

        worktree_mgr.classification = "unknown"
        refused_text, refused_error = await (
            mcp_engineer._dispatch_engineer_tool(
                "engineer_review_cycle_continue",
                {
                    "task": shipped_review.id,
                    "reason": "Classification must fail closed.",
                },
                tracing_handle,
                state,
                caller_id=engineer.id,
            )
        )
        self.assertTrue(refused_error)
        self.assertIn("branch tip to be verified ahead", refused_text)
        self.assertEqual(shipped_review.worktree_boundary["status"], "open")
        self.assertNotIn(
            "review_cycle_continuations",
            shipped_review.completion_evidence,
        )
        worktree_mgr.classification = "ahead"

        continued_text, continued_error = await (
            mcp_engineer._dispatch_engineer_tool(
                "engineer_review_cycle_continue",
                {
                    "task": shipped_review.id,
                    "reason": "Continue the verified Block-fix cycle.",
                },
                tracing_handle,
                state,
                caller_id=engineer.id,
            )
        )
        self.assertFalse(continued_error, continued_text)
        continued = json.loads(continued_text)
        self.assertEqual(continued["type"], "review_cycle_continued")
        self.assertEqual(
            shipped_review.worktree_boundary["status"], "superseded"
        )
        self.assertEqual(
            shipped_review.worktree_boundary["commit_sha"], "a" * 40
        )
        continuation = state.board_tasks[
            continued["continuation_task_id"]
        ]
        predecessor_link = shipped_review.completion_evidence[
            "review_cycle_continuations"
        ][0]
        continuation_link = continuation.completion_evidence[
            "review_cycle_continue"
        ]
        for key in (
                "original_review_task_id", "continuation_task_id",
                "pipeline_root_id", "repo_root", "branch"):
            self.assertEqual(predecessor_link[key], continuation_link[key])
        self.assertEqual(
            shipped_review.worktree_boundary["superseded_by_task_id"],
            continuation.id,
        )

        calls.clear()
        post_text, post_error = await mcp_engineer._dispatch_engineer_tool(
            "engineer_rebase",
            {"agent": implementer.id},
            tracing_handle,
            state,
            caller_id=engineer.id,
        )
        self.assertFalse(post_error, post_text)
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_rebase",
             "worktree_check_merge"],
        )

        # Model the ordinary queue handoff after the already-started Block/fix
        # stage finishes: the audited continuation becomes the implementer's
        # active task, then derives the required fresh review.
        state.board_move_task(blocking_review.id, "Done")
        state.board_move_task(fix.id, "Done")
        fix.agent_id = ""
        implementer.current_task_id = ""
        await dispatch({
            "cmd": "dispatch_task",
            "id": continuation.id,
            "agent_id": implementer.id,
        })
        implementer.current_task_id = continuation.id
        derive_result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Fresh review after audited continuation and rebase",
        })
        self.assertEqual(derive_result["type"], "ok", derive_result)
        fresh_review = state.board_tasks[derive_result["task_id"]]
        self.assertEqual(fresh_review.parent_task_id, continuation.id)
        self.assertEqual(fresh_review.pipeline_root_id, root.id)
        self.assertEqual(fresh_review.action_name, "feature/review")
        self.assertTrue(dispatch_calls)
        fresh_reviewer = state.agents[fresh_review.agent_id]
        fresh_reviewer.worktree_path = implementer.worktree_path
        fresh_reviewer.worktree_repo_root = implementer.worktree_repo_root
        fresh_reviewer.git_root = implementer.git_root
        fresh_reviewer.worktree_branch = implementer.worktree_branch
        fresh_reviewer.worktree_base_branch = implementer.worktree_base_branch
        fresh_reviewer.current_task_id = fresh_review.id
        shipped = await handle_command({
            "cmd": "ai_report",
            "cell_id": fresh_reviewer.id,
            "action": "done",
            "message": (
                "Blocking issues: None\n"
                "Follow-up classification: none\n"
                "Final review verdict: Ship"
            ),
            "terminal_declaration": (
                "No further work is needed; I will not derive after this."
            ),
        })
        self.assertTrue(shipped is None or shipped.get("type") == "ok", shipped)
        self.assertEqual(fresh_review.lane, "Done")
        merged = self.server_mod.mark_branch_boundaries_merged(
            state.board_tasks.values(),
            repo_root=implementer.worktree_repo_root,
            branch=implementer.worktree_branch,
            merge_sha="e" * 40,
            task_ids=(
                continuation.id,
                fresh_review.id,
                shipped_review.id,
            ),
        )
        self.assertIn(shipped_review.id, {task.id for task in merged})
        state.board_cascade_done(fresh_review.id)
        self.assertEqual(
            root.lane,
            "Done",
            [
                (task.id, task.lane, task.action_name,
                 task.worktree_boundary,
                 task.completion_evidence)
                for task in state.board_get_chain(root.id)
            ],
        )

    def _recording_dispatch(self, state):
        calls = []

        async def dispatch(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "dispatch_task")
            task = state.board_tasks[payload["id"]]
            if payload.get("agent_id"):
                task.agent_id = payload["agent_id"]
                task.lane = "In Progress"
                return {
                    "type": "ok",
                    "task_id": task.id,
                    "agent_id": payload["agent_id"],
                }
            self.assertTrue(payload.get("create_agent"))
            agent_id = f"created-{len(calls)}"
            agent = self._make_agent(agent_id)
            state.agents[agent_id] = agent
            state.groups["g"].append(agent_id)
            task.agent_id = agent_id
            task.lane = "In Progress"
            return {
                "type": "ok",
                "task_id": task.id,
                "agent_id": agent_id,
            }

        return calls, dispatch

    class _AutoCloseActionManager:
        def get_transitions(self, action_name, _base_dir):
            if action_name == "feature/implement":
                return [{"action": "feature/review"}]
            return []

        def load_action(self, _action_name, _base_dir):
            return {}

        def get_deliverable(self, _action_name, _base_dir):
            return None

        def has_required_transition(self, _action_name, _base_dir):
            return False

        def get_auto_close_on_done(self, action_name, *, base_dir=""):
            return action_name == "feature/review"

        def render_action(self, action_name, _vars, *, base_dir="",
                          torque_context=None):
            if action_name == "feature/review":
                return {"prompt": "Review the implementation."}
            return {"prompt": "Proceed."}

        def is_implementation_depth(self, _action_name, _base_dir):
            return False

    class _StaleWorktreeManager:
        async def stale_base_info(self, _cell):
            return {
                "stale": True,
                "branch": "torque/impl",
                "base_branch": "main",
                "fork_point": "1111111111111111111111111111111111111111",
                "base_head": "2222222222222222222222222222222222222222",
                "branch_head": "3333333333333333333333333333333333333333",
                "fork_point_subject": "old base",
                "base_head_subject": "new base",
                "commits_on_base": 2,
                "files_changed_on_base": 3,
                "warning": "⚠ STALE BASE: torque/impl forked behind main",
            }

        async def boundary_tip_mismatch_info(
                self, _cell, boundary_sha, tip_sha):
            return {
                "classification": "ahead",
                "commit_count": 1,
                "boundary_sha": boundary_sha,
                "tip_sha": tip_sha,
            }

    class _ReviewBackstopWorktreeManager:
        def __init__(self, *, ahead=1, head="reviewed-head"):
            self.ahead = ahead
            self.head = head

        async def _ahead_behind(self, _cell, worktree_submodules=None):
            return self.ahead, 0

        async def stale_base_info(self, _cell):
            return {"stale": False}

        async def has_uncommitted_changes(self, _cell):
            return False

        async def current_head(self, _cell):
            return self.head

    def _add_second_review_cycle_chain(self, state, *,
                                       reviewer_status="idle",
                                       reviewer_session="review-1-session"):
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="task-fix",
        )
        reviewer = self._make_agent(
            "review-1",
            status=reviewer_status,
            session_id=reviewer_session,
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        state.groups["g"].extend([implementer.id, reviewer.id])
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
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
        )
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )
        return implementer, reviewer, root, fix

    def _make_stream_backstop_implementer(self, state, *,
                                          current_task_id=""):
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id=current_task_id,
        )
        implementer.worktree_path = "/repo/.torque/worktrees/impl-1"
        implementer.worktree_repo_root = "/repo"
        implementer.git_root = "/repo"
        implementer.worktree_branch = "torque/impl-1"
        implementer.worktree_base_branch = "main"
        state.agents[implementer.id] = implementer
        state.groups["g"].append(implementer.id)
        return implementer

    def _add_stream_backstop_parent_boundary(self, state, implementer, *,
                                             task_id="task-root",
                                             agent_id="",
                                             lane="In Progress"):
        task = state.board_add_task(
            "Implement feature",
            "g",
            lane=lane,
            id=task_id,
            action_name="feature/implement",
            agent_id=agent_id,
        )
        task.worktree_boundary = {
            "version": "1",
            "repo_root": implementer.worktree_repo_root,
            "branch": implementer.worktree_branch,
            "base_branch": implementer.worktree_base_branch,
            "status": "open",
            "recorded_at": "2026-04-07T10:00:00+00:00",
            "commit_sha": "impl-head",
            "recorded_by_agent_id": implementer.id,
        }
        return task

    async def test_cold_loaded_backlink_allows_feature_review_derive(self):
        """A durable assignment must restore the derive parent after restart."""
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        persisted = self.state_mod.MatrixState(db=db)
        persisted.groups["g"] = []
        persisted.group_settings["g"] = self.state_mod.GroupSettings(
            dispatch_lane="In Progress",
        )
        implementer = self._make_agent("impl-1", status="running")
        persisted.agents[implementer.id] = implementer
        persisted.groups["g"].append(implementer.id)
        persisted._db_save_groups()
        persisted._db_save_group_settings("g")
        persisted._db_save_agent(implementer)
        parent = persisted.board_add_task(
            "Synthetic implementation",
            "g",
            lane="In Progress",
            id="TASK-IMPLEMENT",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        self.assertIsNotNone(parent)

        state = self.state_mod.MatrixState(db=db)
        state.load()
        self.assertEqual(state.agents[implementer.id].current_task_id, parent.id)

        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review synthetic implementation",
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["cmd"], "dispatch_task")
        self.assertEqual(calls[0]["id"], result["task_id"])

    async def test_feature_review_derive_stream_backstop_restores_shared_branch_review(self):
        state = self._make_state()
        implementer = self._make_stream_backstop_implementer(state)
        parent = self._add_stream_backstop_parent_boundary(
            state,
            implementer,
        )
        worktree_mgr = self._ReviewBackstopWorktreeManager(
            ahead=1,
            head="reviewed-head",
        )
        calls = []

        async def dispatch(payload):
            calls.append(dict(payload))
            self.assertEqual(payload["cmd"], "dispatch_task")
            self.assertTrue(payload.get("create_agent"))
            self.assertEqual(payload.get("inherit_worktree_from"),
                             implementer.id)
            self.assertEqual(payload.get("handoff_worktree_from"),
                             implementer.id)
            task = state.board_tasks[payload["id"]]
            reviewer = self._make_agent(
                "created-reviewer",
                status="running",
                current_task_id="",
            )
            reviewer.worktree_path = implementer.worktree_path
            reviewer.worktree_repo_root = implementer.worktree_repo_root
            reviewer.git_root = implementer.git_root
            reviewer.worktree_branch = implementer.worktree_branch
            reviewer.worktree_base_branch = implementer.worktree_base_branch
            state.agents[reviewer.id] = reviewer
            state.groups["g"].append(reviewer.id)
            task.agent_id = reviewer.id
            task.lane = "In Progress"
            return {
                "type": "ok",
                "task_id": task.id,
                "agent_id": reviewer.id,
            }

        async def record_task_boundary(task, cell, message=""):
            for older in self.server_mod.branch_boundary_tasks(
                state.board_tasks.values(),
                repo_root=cell.worktree_repo_root,
                branch=cell.worktree_branch,
                statuses={"open"},
            ):
                if older.id == task.id:
                    continue
                boundary = dict(older.worktree_boundary or {})
                boundary["status"] = "superseded"
                boundary["superseded_by_task_id"] = task.id
                older.worktree_boundary = boundary
            task.worktree_boundary = {
                "version": "1",
                "repo_root": cell.worktree_repo_root,
                "branch": cell.worktree_branch,
                "base_branch": cell.worktree_base_branch,
                "status": "open",
                "recorded_at": "2026-04-07T12:00:00+00:00",
                "commit_sha": await worktree_mgr.current_head(cell),
                "recorded_by_agent_id": cell.id,
                "message": message,
            }
            return dict(task.worktree_boundary)

        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=worktree_mgr,
            closure_overrides={
                "_record_task_boundary": record_task_boundary,
            },
        )

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review implementation",
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(result["agent_id"], "created-reviewer")
        self.assertEqual(implementer.current_task_id, "")
        self.assertEqual(parent.agent_id, "")
        reviews = [
            task for task in state.board_tasks.values()
            if task.action_name == "feature/review"
        ]
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review.parent_task_id, parent.id)
        self.assertNotEqual(review.agent_id, implementer.id)

        done = await handle_command({
            "cmd": "ai_report",
            "cell_id": review.agent_id,
            "action": "done",
            "message": "Ship",
            "terminal_declaration": (
                "No further work is needed; I will not derive after this."
            ),
        })

        self.assertTrue(done is None or done.get("type") == "ok", done)
        self.assertEqual(review.worktree_boundary["repo_root"],
                         implementer.worktree_repo_root)
        self.assertEqual(review.worktree_boundary["branch"],
                         implementer.worktree_branch)
        self.assertEqual(review.worktree_boundary["commit_sha"],
                         "reviewed-head")
        self.assertEqual(parent.worktree_boundary["status"], "superseded")
        latest = self.server_mod.latest_boundary_task(
            state.board_tasks.values(),
            repo_root=implementer.worktree_repo_root,
            branch=implementer.worktree_branch,
            statuses={"open"},
        )
        self.assertEqual(latest.id, review.id)

    async def test_feature_review_derive_stream_backstop_does_not_fire_without_ahead_commits(self):
        state = self._make_state()
        implementer = self._make_stream_backstop_implementer(state)
        self._add_stream_backstop_parent_boundary(state, implementer)
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=self._ReviewBackstopWorktreeManager(ahead=0),
        )

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review implementation",
        })

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["message"], "No linked task to derive from")
        self.assertEqual(calls, [])
        self.assertEqual([
            task.id for task in state.board_tasks.values()
            if task.action_name == "feature/review"
        ], [])

    async def test_feature_review_derive_stream_backstop_does_not_fire_with_open_review_boundary(self):
        state = self._make_state()
        implementer = self._make_stream_backstop_implementer(state)
        parent = self._add_stream_backstop_parent_boundary(state, implementer)
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        review.worktree_boundary = {
            "version": "1",
            "repo_root": implementer.worktree_repo_root,
            "branch": implementer.worktree_branch,
            "base_branch": implementer.worktree_base_branch,
            "status": "open",
            "recorded_at": "2026-04-07T11:00:00+00:00",
            "commit_sha": "reviewed-head",
            "recorded_by_agent_id": "reviewer-1",
        }
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=self._ReviewBackstopWorktreeManager(ahead=1),
        )

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review implementation again",
        })

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["message"], "No linked task to derive from")
        self.assertEqual(calls, [])
        self.assertEqual([
            task.id for task in state.board_tasks.values()
            if task.action_name == "feature/review"
        ], [review.id])

    async def test_feature_review_derive_stream_backstop_does_not_fire_when_tracked_task_exists(self):
        state = self._make_state()
        implementer = self._make_stream_backstop_implementer(
            state,
            current_task_id="task-live",
        )
        live = state.board_add_task(
            "Implement tracked feature",
            "g",
            lane="In Progress",
            id="task-live",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        boundary = self._add_stream_backstop_parent_boundary(
            state,
            implementer,
            task_id="task-boundary",
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=self._ReviewBackstopWorktreeManager(ahead=0),
        )

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review tracked implementation",
        })

        self.assertEqual(result["type"], "ok")
        reviews = [
            task for task in state.board_tasks.values()
            if task.action_name == "feature/review"
        ]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].parent_task_id, live.id)
        self.assertNotEqual(reviews[0].parent_task_id, boundary.id)
        self.assertEqual(calls[0]["inherit_worktree_from"], implementer.id)

    async def test_feature_review_derive_surfaces_live_prior_reviewer_reuse(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(state)
        )
        implementer.worktree_path = "/repo/fix-worktree"
        implementer.worktree_branch = "torque/implementer/fix"
        reviewer.worktree_path = "/repo/predecessor-worktree"
        reviewer.worktree_branch = "torque/reviewer/predecessor"
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
            "description": (
                "Hard requirement: use a reviewer distinct from review-1."
            ),
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(result["agent_id"], reviewer.id)
        self.assertEqual(calls[0]["agent_id"], reviewer.id)
        self.assertEqual(calls[0]["inherit_worktree_from"], implementer.id)
        self.assertNotIn("create_agent", calls[0])
        reuse = result["reviewer_reuse"]
        self.assertEqual(reuse["kind"], "prior_reviewer_reuse")
        self.assertEqual(reuse["reviewer_id"], reviewer.id)
        self.assertEqual(reuse["prior_review_task_ids"], ["task-review"])
        self.assertEqual(reuse["selection_source"], "automatic_chain_reuse")

        review_task = state.board_tasks[result["task_id"]]
        self.assertEqual(
            review_task.completion_evidence["reviewer_assignment"],
            reuse,
        )
        self.assertIn("torque:reviewer-reused", review_task.labels)
        reuse_messages = [
            entry for entry in review_task.messages
            if entry.get("action") == "reviewer_reused"
        ]
        self.assertEqual(len(reuse_messages), 1)
        self.assertIn(reviewer.id, reuse_messages[0]["message"])
        self.assertIn("task-review", reuse_messages[0]["message"])

    async def test_reused_reviewer_records_boundary_at_fix_worktree_head(self):
        """A re-review boundary must name the SHA the reviewer was handed."""
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(state)
        )
        implementer.worktree_path = "/repo/fix-worktree"
        implementer.worktree_branch = "torque/implementer/fix"
        implementer.worktree_repo_root = "/repo"
        implementer.worktree_base_branch = "main"
        reviewer.worktree_path = "/repo/predecessor-worktree"
        reviewer.worktree_branch = "torque/reviewer/predecessor"
        reviewer.worktree_repo_root = "/repo"
        reviewer.worktree_base_branch = "main"
        heads = {
            implementer.worktree_path: "reviewed-fix-sha",
            reviewer.worktree_path: "predecessor-sha",
        }

        async def dispatch(payload):
            task = state.board_tasks[payload["id"]]
            target = state.agents[payload["agent_id"]]
            source = state.agents[payload["inherit_worktree_from"]]
            self.server_mod._copy_worktree_context(target, source)
            task.agent_id = target.id
            task.lane = "In Progress"
            target.current_task_id = task.id
            return {"type": "ok", "task_id": task.id, "agent_id": target.id}

        async def record_task_boundary(task, cell, message=""):
            task.worktree_boundary = {
                "repo_root": cell.worktree_repo_root,
                "branch": cell.worktree_branch,
                "commit_sha": heads[cell.worktree_path],
                "message": message,
                "status": "open",
            }
            return dict(task.worktree_boundary)

        handle_command = self._extract_handle_command(
            state,
            dispatch,
            closure_overrides={"_record_task_boundary": record_task_boundary},
        )
        derived = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes at reviewed-fix-sha",
        })
        self.assertEqual(derived["type"], "ok")

        review = state.board_tasks[derived["task_id"]]
        completed = await handle_command({
            "cmd": "ai_report",
            "cell_id": reviewer.id,
            "action": "done",
            "message": "Ship; reviewed reviewed-fix-sha",
            "terminal_declaration": (
                "No further work is needed; I will not derive after this."
            ),
        })

        self.assertTrue(completed is None or completed.get("type") == "ok")
        self.assertEqual(review.worktree_boundary["branch"],
                         implementer.worktree_branch)
        self.assertEqual(review.worktree_boundary["commit_sha"],
                         "reviewed-fix-sha")
        assignment = review.completion_evidence["reviewer_assignment"]
        self.assertEqual(assignment["reviewer_id"], reviewer.id)
        self.assertEqual(
            assignment["prior_review_task_ids"],
            ["task-review"],
        )

    async def test_dispatch_reused_reviewer_adopts_explicit_fix_worktree(self):
        """Existing-agent dispatch applies the review handoff before prompting."""
        state = self._make_state()
        implementer, reviewer, _root, fix = (
            self._add_second_review_cycle_chain(state)
        )
        implementer.worktree_path = "/repo/fix-worktree"
        implementer.worktree_branch = "torque/implementer/fix"
        implementer.worktree_repo_root = "/repo"
        implementer.worktree_base_branch = "main"
        reviewer.worktree_path = "/repo/predecessor-worktree"
        reviewer.worktree_branch = "torque/reviewer/predecessor"
        reviewer.worktree_repo_root = "/repo"
        reviewer.worktree_base_branch = "main"
        review = state.board_add_task(
            "Review fixed implementation",
            "g",
            lane="Backlog",
            id="task-rereview",
            action_name="feature/review",
            parent_task_id=fix.id,
            pipeline_root_id="task-root",
        )
        review.completion_evidence = {
            "reviewer_assignment": {
                "kind": "prior_reviewer_reuse",
                "reviewer_id": reviewer.id,
                "prior_review_task_ids": ["task-review"],
            },
        }
        sent_prompts = []

        def record_task_dispatch(cell, task, lane):
            state.board_update_task(task.id, agent_id=cell.id, lane=lane)
            cell.current_task_id = task.id

        async def send_agent_prompt(cell, prompt, **_kwargs):
            sent_prompts.append((cell.id, prompt))

        holder = {}

        async def recursive_dispatch(payload):
            return await holder["handle_command"](payload)

        handle_command = self._extract_handle_command(
            state,
            recursive_dispatch,
            action_mgr=self._AutoCloseActionManager(),
            closure_overrides={
                "_build_postscript": lambda *args, **kwargs: "",
                "_record_task_dispatch": record_task_dispatch,
                "_send_agent_prompt": send_agent_prompt,
            },
        )
        holder["handle_command"] = handle_command

        result = await handle_command({
            "cmd": "dispatch_task",
            "id": review.id,
            "agent_id": reviewer.id,
            "inherit_worktree_from": implementer.id,
            "handoff_worktree_from": implementer.id,
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(reviewer.worktree_path, implementer.worktree_path)
        self.assertEqual(reviewer.worktree_branch, implementer.worktree_branch)
        self.assertEqual(sent_prompts[0][0], reviewer.id)
        self.assertIn("Reviewer assignment disclosure", sent_prompts[0][1])
        self.assertIn(reviewer.id, sent_prompts[0][1])
        self.assertIn("task-review", sent_prompts[0][1])
        self.assertIn("not a fresh independent reviewer", sent_prompts[0][1])

    async def test_feature_review_derive_refuses_stale_base_before_dispatch(self):
        state = self._make_state()
        implementer, _reviewer, _root, fix = (
            self._add_second_review_cycle_chain(state)
        )
        implementer.worktree_path = "/repo/.torque/worktrees/impl"
        implementer.worktree_repo_root = "/repo"
        implementer.git_root = "/repo"
        implementer.worktree_branch = "torque/impl"
        implementer.worktree_base_branch = "main"
        review = state.board_tasks["task-review"]
        review.lane = "Done"
        review.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/impl",
            "base_branch": "main",
            "commit_sha": "3333333333333333333333333333333333333333",
            "status": "open",
            "recorded_at": "2026-08-07T00:00:00+00:00",
        }
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=self._StaleWorktreeManager(),
        )

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
        })

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["code"], "stale_base")
        self.assertIn("STALE BASE", result["message"])
        self.assertIn("worktree_rebase", result["message"])
        self.assertEqual(result["suggested_command"],
                         f"worktree_rebase id={implementer.id}")
        self.assertTrue(result["stale_base"]["stale"])
        self.assertEqual(
            result["stale_base"]["branch_head"],
            "3333333333333333333333333333333333333333",
        )
        evidence = result["post_rebase_evidence_required"]
        self.assertEqual(
            evidence["base_head_sha"],
            "2222222222222222222222222222222222222222",
        )
        self.assertEqual(evidence["base_branch"], "main")
        self.assertIn("rerun_tests", evidence)
        self.assertEqual(calls, [])
        self.assertEqual(fix.status, "")

    async def test_feature_review_derive_composed_state_names_safe_continuation(self):
        state = self._make_state()
        implementer, _reviewer, _root, fix = (
            self._add_second_review_cycle_chain(state)
        )
        implementer.worktree_path = "/repo/.torque/worktrees/impl"
        implementer.worktree_repo_root = "/repo"
        implementer.git_root = "/repo"
        implementer.worktree_branch = "torque/impl"
        implementer.worktree_base_branch = "main"
        review = state.board_tasks["task-review"]
        review.lane = "Done"
        review.completion_evidence = {
            "review": {
                "verdict": "ship",
                "agent_id": "review-1",
                "recorded_at": "2026-08-07T00:00:00+00:00",
            },
        }
        review.worktree_boundary = {
            "version": "1",
            "repo_root": "/repo",
            "branch": "torque/impl",
            "base_branch": "main",
            "commit_sha": "2222222222222222222222222222222222222222",
            "status": "open",
            "recorded_at": "2026-08-07T00:00:00+00:00",
        }
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(
            state,
            dispatch,
            worktree_mgr=self._StaleWorktreeManager(),
        )

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the unreviewed commit",
        })

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["code"], "review_cycle_deadlock")
        expected_guidance = (
            "This branch is stale and has unreviewed commits past a completed "
            "open feature/review boundary. Do not record a reviewed boundary "
            "at the unreviewed tip. Use `review_cycle_continue` on the "
            "completed review, then non-force rebase, rerun the relevant "
            "evidence, and obtain a fresh feature/review."
        )
        self.assertEqual(
            result["message"],
            "Cannot derive feature/review from this composed review-cycle "
            f"state.\n\n{expected_guidance}",
        )
        self.assertIn("review_cycle_continue", result["message"])
        self.assertIn("non-force rebase", result["message"])
        self.assertIn("rerun the relevant evidence", result["message"])
        self.assertIn("fresh feature/review", result["message"])
        self.assertNotIn("worktree_rebase", result["message"])
        self.assertNotIn("re-review the new commits", result["message"])
        self.assertNotIn(
            "record a reviewed boundary at the tip", result["message"]
        )
        self.assertEqual(calls, [])
        self.assertEqual(fix.status, "")

    async def test_feature_review_derive_creates_agent_when_prior_reviewer_closed(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(
                state,
                reviewer_status="stopped",
                reviewer_session="",
            )
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
        })

        self.assertEqual(result["type"], "ok")
        self.assertNotEqual(result["agent_id"], reviewer.id)
        self.assertTrue(calls[0]["create_agent"])
        self.assertNotIn("agent_id", calls[0])

    async def test_explicit_target_agent_overrides_prior_reviewer_reuse(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(state)
        )
        explicit = self._make_agent("review-2")
        state.agents[explicit.id] = explicit
        state.groups["g"].append(explicit.id)
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review the fixes",
            "target_agent": explicit.id,
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(result["agent_id"], explicit.id)
        self.assertEqual(calls[0]["agent_id"], explicit.id)
        self.assertNotEqual(calls[0]["agent_id"], reviewer.id)
        self.assertNotIn("reviewer_reuse", result)

    async def test_first_feature_review_derive_creates_new_agent(self):
        state = self._make_state()
        engineer = self._make_agent("engineer-1")
        engineer.kind = "engineer"
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="task-root",
        )
        state.agents[engineer.id] = engineer
        state.agents[implementer.id] = implementer
        state.groups["g"].extend([engineer.id, implementer.id])
        state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
            assigned_engineer_id=engineer.id,
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review implementation",
        })

        self.assertEqual(result["type"], "ok")
        self.assertNotEqual(result["agent_id"], implementer.id)
        self.assertTrue(calls[0]["create_agent"])
        self.assertNotIn("agent_id", calls[0])
        self.assertNotIn("reviewer_reuse", result)
        summary = state.engineer_dispatch_shape_summary(engineer.id)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["derives_by_shape"]["serial"], 1)
        event = state.engineer_dispatch_shape_events(engineer.id)[0]
        self.assertEqual(event["source_tool"], "torque_derive")
        self.assertEqual(event["shape"], "serial")
        self.assertFalse(event["hintable"])
        self.assertEqual(event["metadata"]["parent_task_id"], "task-root")

    async def test_engineer_origin_dispatch_command_rejects_architect_target(self):
        state = self._make_state()
        engineer = self._make_agent("engineer-1")
        engineer.kind = "engineer"
        architect = self._make_agent("arch-catalyst")
        architect.kind = "architect"
        architect.name = "Catalyst"
        architect.slug = "catalyst"
        architect.effective_agent_class_snapshot = {
            "id": "creative-architect",
            "base_kind": "architect",
        }
        state.agents[engineer.id] = engineer
        state.agents[architect.id] = architect
        state.groups["g"].extend([engineer.id, architect.id])
        state.board_add_task(
            "Invalid architect-routed review",
            "g",
            lane="Backlog",
            id="task-review",
            assigned_engineer_id=engineer.id,
        )
        panel_events = []

        async def recursive_dispatch(_payload):
            self.fail("denied dispatch should not recurse")

        handle_command = self._extract_handle_command(
            state,
            recursive_dispatch,
            closure_overrides={
                "_panel_event": lambda *args, **kwargs: panel_events.append(
                    (args, kwargs)
                ),
            },
        )

        result = await handle_command({
            "cmd": "dispatch_task",
            "id": "task-review",
            "agent_id": architect.id,
            "_engineer_dispatch_id": engineer.id,
            "_engineer_dispatch_group": "g",
        })

        self.assertEqual(result["type"], "error")
        self.assertIn(
            "Engineer-originated executable task routing to Architect targets/classes is denied",
            result["message"],
        )
        self.assertEqual(result["agent_id"], architect.id)
        self.assertEqual(state.board_tasks["task-review"].agent_id, "")
        self.assertEqual(state.board_tasks["task-review"].lane, "Backlog")
        self.assertEqual(panel_events[0][0][0], "task_dispatch_denied")
        self.assertEqual(panel_events[0][1]["task_id"], "task-review")

    async def test_derive_auto_dispatch_persists_auto_close_spawn_label(self):
        state = self._make_state()
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="task-root",
        )
        state.agents[implementer.id] = implementer
        state.groups["g"].append(implementer.id)
        state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )

        persisted_labels = {}

        def record_saved_task(task):
            persisted_labels[task.id] = list(task.labels or [])

        state._db_save_task = record_saved_task

        async def create_agent(group, name, launch_cfg, **_kwargs):
            agent = self._make_agent(
                "created-reviewer",
                status="idle",
                session_id="created-reviewer-session",
            )
            agent.name = name
            state.agents[agent.id] = agent
            state.groups[group].append(agent.id)
            return agent

        sent_prompts = []

        async def send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        def record_task_dispatch(cell, task, lane):
            state.board_update_task(task.id, agent_id=cell.id, lane=lane)
            cell.current_task_id = task.id
            state.mark_agent_progress(cell, emit=False)

        holder = {}

        async def recursive_dispatch(payload):
            return await holder["handle_command"](payload)

        handle_command = self._extract_handle_command(
            state,
            recursive_dispatch,
            action_mgr=self._AutoCloseActionManager(),
            closure_overrides={
                "_build_postscript": lambda *args, **kwargs: "",
                "_create_agent_with_config": create_agent,
                "_record_task_dispatch": record_task_dispatch,
                "_resolve_worker_launch_config": (
                    lambda *args, **kwargs: {"worktree": False}
                ),
                "_send_agent_prompt": send_agent_prompt,
            },
        )
        holder["handle_command"] = handle_command

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "action_name": "feature/review",
            "message": "Review implementation",
        })

        self.assertEqual(result["type"], "ok")
        derived = [
            task for task in state.board_tasks.values()
            if task.action_name == "feature/review"
        ]
        self.assertEqual(len(derived), 1)
        derived_task = derived[0]
        self.assertIn(
            self.server_mod.AUTO_CLOSE_SPAWNED_LABEL,
            derived_task.labels,
        )
        self.assertIn(
            self.server_mod.AUTO_CLOSE_SPAWNED_LABEL,
            persisted_labels.get(derived_task.id, []),
        )
        self.assertEqual(derived_task.agent_id, "created-reviewer")
        self.assertEqual(result["agent_id"], "created-reviewer")
        self.assertEqual([call[0] for call in sent_prompts],
                         ["created-reviewer"])

    async def test_reuse_self_derive_records_warm_cluster_shape(self):
        state = self._make_state()
        engineer = self._make_agent("engineer-1")
        engineer.kind = "engineer"
        implementer = self._make_agent(
            "impl-1",
            status="running",
            current_task_id="task-root",
        )
        state.agents[engineer.id] = engineer
        state.agents[implementer.id] = implementer
        state.groups["g"].extend([engineer.id, implementer.id])
        state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/adhoc",
            agent_id=implementer.id,
            assigned_engineer_id=engineer.id,
        )
        calls, dispatch = self._recording_dispatch(state)
        handle_command = self._extract_handle_command(state, dispatch)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": implementer.id,
            "action": "derive",
            "message": "Self-review this small change",
            "reuse_self": True,
        })

        self.assertEqual(result["type"], "ok")
        self.assertEqual(result["agent_id"], implementer.id)
        self.assertEqual(calls[0]["agent_id"], implementer.id)
        event = state.engineer_dispatch_shape_events(engineer.id)[0]
        self.assertEqual(event["source_tool"], "torque_derive")
        self.assertEqual(event["shape"], "warm_cluster")
        self.assertEqual(event["metadata"]["target_agent_id"], implementer.id)
        self.assertTrue(event["metadata"]["reuse_self"])
