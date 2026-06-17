import importlib
import sys
import types
import unittest
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

    async def test_feature_review_derive_reuses_live_prior_reviewer_in_chain(self):
        state = self._make_state()
        implementer, reviewer, _root, _fix = (
            self._add_second_review_cycle_chain(state)
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
        self.assertEqual(result["agent_id"], reviewer.id)
        self.assertEqual(calls[0]["agent_id"], reviewer.id)
        self.assertNotIn("create_agent", calls[0])

    async def test_feature_review_derive_refuses_stale_base_before_dispatch(self):
        state = self._make_state()
        implementer, _reviewer, _root, fix = (
            self._add_second_review_cycle_chain(state)
        )
        implementer.worktree_path = "/repo/.torque/worktrees/impl"
        implementer.worktree_branch = "torque/impl"
        implementer.worktree_base_branch = "main"
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
        summary = state.engineer_dispatch_shape_summary(engineer.id)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["derives_by_shape"]["serial"], 1)
        event = state.engineer_dispatch_shape_events(engineer.id)[0]
        self.assertEqual(event["source_tool"], "torque_derive")
        self.assertEqual(event["shape"], "serial")
        self.assertFalse(event["hintable"])
        self.assertEqual(event["metadata"]["parent_task_id"], "task-root")

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
