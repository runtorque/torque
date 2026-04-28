import importlib
from pathlib import Path
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class FakeActionManager:
    def __init__(self, action=None, transitions=None, *,
                 actions=None, transitions_by_name=None):
        self.action = action or {}
        self.transitions = transitions or []
        self.actions = actions
        self.transitions_by_name = transitions_by_name

    def load_action(self, name, _base_dir=""):
        if self.actions is not None:
            return dict(self.actions.get(name, self.action))
        return dict(self.action)

    def get_transitions(self, name, _base_dir=""):
        if self.transitions_by_name is not None:
            return list(self.transitions_by_name.get(name, []))
        return list(self.transitions)


class FakeWorktreeManager:
    def __init__(self, summary):
        self.summary = summary
        self.non_test_only = None

    async def diff_summary(self, _cell, *, non_test_only=False):
        self.non_test_only = non_test_only
        return dict(self.summary)


class ReviewGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.worktree_mod = importlib.import_module("loom.worktree")
        self.worktree_mod = importlib.reload(self.worktree_mod)

    def _state_cell_task(self, *, action_name="feature/implement"):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            worktree_path="/tmp/wt",
            worktree_base_branch="main",
        )
        state.agents[cell.id] = cell
        task = state.board_add_task(
            "Implement",
            "g",
            lane="In Progress",
            id="task-1",
            action_name=action_name,
            agent_id=cell.id,
        )
        return state, cell, task

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, dispatch_handler, action_mgr):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
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
            "action_mgr": action_mgr,
            "handle_command": dispatch_handler,
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
        })
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
            self.assertTrue(payload.get("create_agent"))
            self.assertNotIn("agent_id", payload)
            task = state.board_tasks[payload["id"]]
            agent_id = f"fresh-reviewer-{len(calls)}"
            agent = self.state_mod.AgentCell(
                id=agent_id,
                name=agent_id,
                slug=agent_id,
                group=task.group,
                cell_type="agent",
                status="idle",
                session_id=f"{agent_id}-session",
                directory="/repo",
            )
            state.agents[agent_id] = agent
            state.groups.setdefault(task.group, []).append(agent_id)
            task.agent_id = agent_id
            task.lane = "In Progress"
            return {
                "type": "ok",
                "task_id": task.id,
                "agent_id": agent_id,
            }

        return calls, dispatch

    async def test_gate_fires_above_threshold_and_auto_derives_review(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 2, "insertions": 80, "deletions": 30},
        )
        calls = []
        checkpoints = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        async def checkpoint_for_gate():
            checkpoints.append("checkpoint")

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
            checkpoint_for_gate=checkpoint_for_gate,
        )

        self.assertEqual(result["type"], "error")
        self.assertIn("auto-derived at task-review", result["message"])
        self.assertIn("diff: 110 LOC", result["message"])
        self.assertIn("threshold: 100", result["message"])
        self.assertEqual(calls[0]["action"], "derive")
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertEqual(calls[0]["task_id"], task.id)
        self.assertEqual(checkpoints, ["checkpoint"])
        self.assertTrue(worktree_mgr.non_test_only)

    async def test_gate_fire_emits_workflow_breach_for_owner_engineer(self):
        state, cell, task = self._state_cell_task()
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        cell.kind = "worker"
        cell.owner_engineer_id = engineer.id
        cell.worktree_branch = "loom/worker-1"
        task.assigned_engineer_id = engineer.id
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 2, "insertions": 80, "deletions": 30},
        )
        panel_events = []

        async def handle_command(_payload):
            return {"type": "ok", "task_id": "task-review"}

        def panel_event(kind, cell_id, agent_name, group, message, task_id=""):
            panel_events.append({
                "kind": kind,
                "cell_id": cell_id,
                "agent_name": agent_name,
                "group": group,
                "message": message,
                "task_id": task_id,
            })

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            panel_event,
            cell=cell,
            task=task,
        )

        self.assertEqual(result["type"], "error")
        self.assertEqual(len(panel_events), 1)
        event = panel_events[0]
        self.assertEqual(event["kind"], "workflow_breach")
        self.assertEqual(event["cell_id"], engineer.id)
        self.assertEqual(event["task_id"], task.id)
        self.assertIn("escape_clause_skip", event["message"])
        self.assertIn("worker=worker-1", event["message"])
        self.assertIn("branch=loom/worker-1", event["message"])

    async def test_gate_skips_below_threshold(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 99, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_gate_inactive_without_action_metadata(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager({}, [{"action": "feature/review"}])
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 500, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_gate_fires_without_declared_review_transition(self):
        state, cell, task = self._state_cell_task(action_name="oneshot/fix")
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 200, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertEqual(result["type"], "error")
        self.assertEqual(calls[0]["action"], "derive")
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertTrue(calls[0]["_review_gate"])

    async def test_auto_gate_no_transition_derives_review_via_ai_report(self):
        state, cell, task = self._state_cell_task(
            action_name="oneshot/no-review-transition",
        )
        cell.slug = cell.id
        cell.status = "running"
        cell.session_id = "worker-session"
        cell.current_task_id = task.id
        cell.worktree_repo_root = "/repo"
        ancestor = self.state_mod.AgentCell(
            id="ancestor-1",
            name="Ancestor",
            slug="ancestor-1",
            group="g",
            cell_type="agent",
            status="idle",
            session_id="ancestor-session",
        )
        state.agents[ancestor.id] = ancestor
        state.groups["g"].insert(0, ancestor.id)
        root = state.board_add_task(
            "Parent task",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/review",
            agent_id=ancestor.id,
        )
        task.parent_task_id = root.id
        task.pipeline_root_id = root.id
        task.pipeline_depth = 1
        action_mgr = FakeActionManager(
            actions={
                task.action_name: {
                    "implementation_depth": True,
                    "review_required_above_loc": 50,
                },
                "feature/review": {},
            },
            transitions_by_name={
                task.action_name: [{"action": "feature/follow-up"}],
            },
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 51, "deletions": 0},
        )
        dispatch_calls, dispatch = self._recording_dispatch(state)
        ai_report = self._extract_handle_command(state, dispatch, action_mgr)
        prior_live = self.server_mod._prior_live_reviewer_agent_for_chain
        self.server_mod._prior_live_reviewer_agent_for_chain = (
            lambda _state, _task: None
        )

        try:
            manual = await ai_report({
                "cmd": "ai_report",
                "cell_id": cell.id,
                "action": "derive",
                "task_id": task.id,
                "action_name": "feature/review",
                "message": "Manual review",
            })
            self.assertEqual(manual["type"], "error")
            self.assertIn("cannot transition", manual["message"])
            self.assertEqual(dispatch_calls, [])

            derive_payloads = []
            derive_results = []

            async def handle_command(payload):
                derive_payloads.append(dict(payload))
                response = await ai_report(payload)
                derive_results.append(response)
                return response

            result = await self.server_mod._maybe_apply_review_required_gate(
                state,
                action_mgr,
                worktree_mgr,
                handle_command,
                lambda *args, **kwargs: None,
                cell=cell,
                task=task,
            )
        finally:
            self.server_mod._prior_live_reviewer_agent_for_chain = prior_live

        self.assertEqual(result["type"], "error")
        self.assertIn("auto-derived at", result["message"])
        self.assertEqual(derive_payloads[0]["cmd"], "ai_report")
        self.assertEqual(derive_payloads[0]["action"], "derive")
        self.assertEqual(derive_payloads[0]["action_name"], "feature/review")
        self.assertTrue(derive_payloads[0]["_review_gate"])

        self.assertEqual(derive_results[0]["type"], "ok")
        review_task = state.board_tasks[derive_results[0]["task_id"]]
        self.assertEqual(review_task.action_name, "feature/review")
        self.assertEqual(task.status, "On Review")
        self.assertEqual(root.status, "On Review")

        dispatch_payload = dispatch_calls[0]
        self.assertTrue(dispatch_payload.get("create_agent"))
        self.assertNotIn("agent_id", dispatch_payload)
        self.assertEqual(dispatch_payload["id"], review_task.id)
        reviewer_id = derive_results[0]["agent_id"]
        self.assertEqual(review_task.agent_id, reviewer_id)
        self.assertNotIn(reviewer_id, {cell.id, ancestor.id})
        self.assertTrue(worktree_mgr.non_test_only)

    async def test_gate_skips_when_implementation_depth_false(self):
        state, cell, task = self._state_cell_task(action_name="feature/research")
        action_mgr = FakeActionManager(
            {
                "implementation_depth": False,
                "review_required_above_loc": 100,
            },
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 200, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_project_implementation_depth_actions_gate_by_default(self):
        action_mgr = importlib.import_module("loom.actions").ActionManager()
        repo_root = str(Path(__file__).resolve().parents[1])

        for action_name in (
            "oneshot/fix",
            "oneshot/improvement",
            "oneshot/feature",
            "feature/implement",
        ):
            with self.subTest(action_name=action_name):
                state, cell, task = self._state_cell_task(
                    action_name=action_name,
                )
                worktree_mgr = FakeWorktreeManager(
                    {"files": 1, "insertions": 251, "deletions": 0},
                )
                calls = []

                async def handle_command(payload):
                    calls.append(payload)
                    return {"type": "ok", "task_id": "task-review"}

                result = await self.server_mod._maybe_apply_review_required_gate(
                    state,
                    action_mgr,
                    worktree_mgr,
                    handle_command,
                    lambda *args, **kwargs: None,
                    cell=cell,
                    task=task,
                    base_dir=repo_root,
                )

                self.assertEqual(result["type"], "error")
                self.assertEqual(calls[0]["action_name"], "feature/review")

    async def test_project_research_action_does_not_gate(self):
        action_mgr = importlib.import_module("loom.actions").ActionManager()
        repo_root = str(Path(__file__).resolve().parents[1])
        state, cell, task = self._state_cell_task(action_name="feature/research")
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 500, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
            base_dir=repo_root,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_gate_skips_after_closed_ship_review_in_chain(self):
        state, cell, task = self._state_cell_task()
        review = state.board_add_task(
            "Review",
            "g",
            lane="Done",
            id="task-review",
            action_name="feature/review",
            parent_task_id=task.id,
            pipeline_root_id=task.id,
            pipeline_depth=1,
        )
        review.messages.append({
            "action": "done",
            "message": "Verdict — Ship",
            "agent_name": "Reviewer",
        })
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 200, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_transition_loc_gate_overrides_action_and_architect_thresholds(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 250,
                "review_default_above": 300,
                "self_review_bypass_allowed": False,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {
                "implementation_depth": True,
                "review_required_above_loc": 200,
                "transitions": [{
                    "action": "feature/review",
                    "loc_gate": {
                        "ship_direct_max": 25,
                        "review_default_above": 75,
                        "self_review_bypass_allowed": False,
                    },
                }],
            },
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 80, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertEqual(result["type"], "error")
        self.assertIn("threshold: 75", result["message"])
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertIn("Transition LOC gate", calls[0]["description"])
        self.assertIn("review_default_above=75", calls[0]["description"])

    async def test_action_threshold_overrides_architect_fallback(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 25,
                "review_default_above": 75,
                "self_review_bypass_allowed": False,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 80, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_action_threshold_preserves_architect_self_review_bypass_policy(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 25,
                "review_default_above": 75,
                "self_review_bypass_allowed": False,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 120, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
            force_skip_review=True,
            skip_reason="self-reviewed locally",
        )

        self.assertEqual(result["type"], "error")
        self.assertIn("threshold: 100", result["message"])
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertIn("Skip request ignored", calls[0]["description"])
        self.assertIn("self-review bypass disabled by architect settings",
                      calls[0]["description"])

    async def test_architect_review_gate_used_when_no_transition_or_action_threshold(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 25,
                "review_default_above": 75,
                "self_review_bypass_allowed": False,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {
                "implementation_depth": True,
                "transitions": [{"action": "feature/review"}],
            },
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 80, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertEqual(result["type"], "error")
        self.assertIn("threshold: 75", result["message"])
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertIn("Architect review policy", calls[0]["description"])
        self.assertIn("review_default_above=75", calls[0]["description"])

    async def test_architect_review_gate_ship_direct_max_is_direct_allowance(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 90,
                "review_default_above": 75,
                "self_review_bypass_allowed": False,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {
                "implementation_depth": True,
                "transitions": [{"action": "feature/review"}],
            },
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 80, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_architect_review_gate_can_disallow_self_review_bypass(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 25,
                "review_default_above": 75,
                "self_review_bypass_allowed": False,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {
                "implementation_depth": True,
                "transitions": [{"action": "feature/review"}],
            },
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 80, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
            force_skip_review=True,
            skip_reason="self-reviewed locally",
        )

        self.assertEqual(result["type"], "error")
        self.assertEqual(calls[0]["action_name"], "feature/review")
        self.assertIn("Skip request ignored", calls[0]["description"])
        self.assertIn("self-review bypass disabled", calls[0]["description"])

    async def test_architect_review_gate_can_allow_self_review_bypass(self):
        state, cell, task = self._state_cell_task()
        architect = self.state_mod.AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[architect.id] = architect
        state.group_settings["g"] = self.state_mod.GroupSettings(
            architect_review_gate_thresholds={
                "ship_direct_max": 25,
                "review_default_above": 75,
                "self_review_bypass_allowed": True,
            },
        )
        task.created_by_architect_id = architect.id
        action_mgr = FakeActionManager(
            {
                "implementation_depth": True,
                "transitions": [{"action": "feature/review"}],
            },
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 1, "insertions": 80, "deletions": 0},
        )
        calls = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            lambda *args, **kwargs: None,
            cell=cell,
            task=task,
            force_skip_review=True,
            skip_reason="self-reviewed locally",
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertEqual(task.messages[-1]["action"], "review_gate_skipped")
        self.assertIn("self-reviewed locally", task.messages[-1]["message"])

    async def test_force_skip_review_bypasses_gate_and_records_audit(self):
        state, cell, task = self._state_cell_task()
        action_mgr = FakeActionManager(
            {"implementation_depth": True, "review_required_above_loc": 100},
            [{"action": "feature/review"}],
        )
        worktree_mgr = FakeWorktreeManager(
            {"files": 2, "insertions": 175, "deletions": 25},
        )
        calls = []
        panel_events = []
        history = []

        async def handle_command(payload):
            calls.append(payload)
            return {"type": "ok", "task_id": "task-review"}

        def panel_event(*args, **kwargs):
            panel_events.append((args, kwargs))

        def append_task_msg(t, act, msg, agent_name):
            t.messages.append({
                "action": act,
                "message": msg,
                "agent_name": agent_name,
            })

        def record_history_msg(c, act, msg, task_override=None):
            history.append((c.id, act, msg, task_override.id))

        result = await self.server_mod._maybe_apply_review_required_gate(
            state,
            action_mgr,
            worktree_mgr,
            handle_command,
            panel_event,
            cell=cell,
            task=task,
            force_skip_review=True,
            skip_reason="mechanical rename",
            append_task_msg=append_task_msg,
            record_history_msg=record_history_msg,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertEqual(task.messages[-1]["action"], "review_gate_skipped")
        audit = task.messages[-1]["message"]
        self.assertIn("worker-1", audit)
        self.assertIn("task-1", audit)
        self.assertIn("diff size 200 LOC", audit)
        self.assertIn("threshold 100", audit)
        self.assertIn("mechanical rename", audit)
        self.assertEqual(panel_events[0][0][0], "review_gate_skipped")
        self.assertIn("diff size 200 LOC", panel_events[0][0][4])
        self.assertEqual(history[0][1], "review_gate_skipped")

    async def test_non_test_filter_excludes_test_files_from_diff_size(self):
        summary, paths = self.worktree_mod._numstat_summary(
            "120\t10\tloom/server.py\n"
            "999\t1\ttests/test_server.py\n"
            "50\t50\tstatic/app.test.js\n"
            "10\t10\tpkg/foo_test.go\n"
            "1\t2\tsrc/main.py\n",
            non_test_only=True,
        )

        self.assertEqual(paths, ["loom/server.py", "src/main.py"])
        self.assertEqual(summary["files"], 2)
        self.assertEqual(summary["insertions"], 121)
        self.assertEqual(summary["deletions"], 12)
