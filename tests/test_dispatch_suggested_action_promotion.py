"""Regression tests for explicit action binding at the dispatch boundary."""

import asyncio
import importlib
import types
import unittest
from dataclasses import fields

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class DispatchActionBindingTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(importlib.import_module("torque.state"))
        self.dispatch_mod = importlib.reload(
            importlib.import_module("torque.commands.task_dispatch")
        )
        self.state = self.state_mod.MatrixState()
        self.state.add_group("g")

    def _runtime(self):
        values = {
            field.name: (lambda *_args, **_kwargs: None)
            for field in fields(self.dispatch_mod.TaskDispatchRuntime)
        }

        async def resolve_base_dir(_group):
            return ""

        values.update({
            "state": self.state,
            "resolve_task_id": lambda _state, task_id: task_id,
            "resolve_base_dir": resolve_base_dir,
        })
        return self.dispatch_mod.TaskDispatchRuntime(**values)

    def test_suggested_action_is_not_promoted_and_actionless_dispatch_refuses(self):
        task = self.state.board_add_task(
            "Engineer must choose final action",
            "g",
            id="TASK:1",
            suggested_action="feature/implement",
        )
        self.assertIsNotNone(task)

        result = asyncio.run(
            self.dispatch_mod.handle_dispatch_task_command(
                {"id": task.id}, self._runtime()
            )
        )

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["reason"], "action_binding_required")
        self.assertEqual(result["task_id"], task.id)
        self.assertEqual(result["dispatch_state"], "queued")
        self.assertIn("requires an action binding before dispatch", result["message"])
        self.assertEqual(task.action_name, "")
        self.assertEqual(task.suggested_action, "feature/implement")
        self.assertEqual(task.dispatch_state, "queued")

    def test_explicit_engineer_action_is_recognized_as_a_binding(self):
        task = self.state.board_add_task(
            "Engineer selected action",
            "g",
            id="TASK:2",
            action_name="feature/implement",
            suggested_action="oneshot/fix",
        )
        self.assertIsNotNone(task)

        self.assertTrue(self.dispatch_mod.task_has_action_binding(task))
        self.assertEqual(task.action_name, "feature/implement")
        self.assertEqual(task.suggested_action, "oneshot/fix")

    def test_invalid_provider_slug_model_prevents_all_worker_provisioning(self):
        """Resolver failure precedes cell, session, worktree, and prompt work."""
        server_agent = importlib.reload(
            importlib.import_module("torque.server_agent")
        )
        self.state.update_group_settings("g", worker_provider="codex")
        task = self.state.board_add_task(
            "Reject invalid model before provisioning",
            "g",
            id="TASK:3",
            action_name="feature/implement",
            requires_review=True,
        )
        calls = {
            "cell": 0,
            "session": 0,
            "worktree": 0,
            "prompt": 0,
        }

        class Bridge:
            capabilities = types.SimpleNamespace(
                supports_embedded_terminal=False
            )

            async def create_session(self, *_args, **_kwargs):
                calls["session"] += 1

        class Worktrees:
            async def get_repo_root(self, _directory):
                return "/repo"

            async def create(self, *_args, **_kwargs):
                calls["worktree"] += 1
                return "/repo/worktree"

        class Templates:
            def resolve_agent_config(
                    self, _template, gs, overrides, **_kwargs):
                resolved = {
                    "provider": gs.agent_provider,
                    "command": gs.agent_boot_command,
                    "model": gs.agent_model,
                    "reasoning_effort": gs.agent_reasoning_effort,
                }
                resolved.update(overrides)
                return resolved

        bridge = Bridge()
        worktrees = Worktrees()
        service = server_agent.AgentLaunchService(
            state=self.state,
            connection=None,
            bridge=bridge,
            worktree_mgr=worktrees,
            template_mgr=Templates(),
        )
        runtime = self._runtime()
        runtime.resolve_worker_launch_config = (
            service.resolve_worker_launch_config
        )
        runtime.worktree_mgr = worktrees
        runtime.action_mgr = types.SimpleNamespace(
            load_action=lambda *_args, **_kwargs: None,
        )

        async def create_agent(*_args, **_kwargs):
            calls["cell"] += 1
            return None

        async def send_prompt(*_args, **_kwargs):
            calls["prompt"] += 1

        def build_persistent_prompt(*_args, **_kwargs):
            calls["prompt"] += 1
            return "prompt"

        runtime.create_agent_with_config = create_agent
        runtime.send_agent_prompt = send_prompt
        runtime.build_dispatch_persistent_prompt = build_persistent_prompt

        result = asyncio.run(self.dispatch_mod.handle_dispatch_task_command(
            {
                "id": task.id,
                "create_agent": True,
                "model": "codex",
            },
            runtime,
        ))

        self.assertEqual(result["type"], "error")
        self.assertIn(
            "Invalid model override `codex`: that is a provider name",
            result["message"],
        )
        self.assertEqual(calls, {
            "cell": 0,
            "session": 0,
            "worktree": 0,
            "prompt": 0,
        })
        self.assertEqual(self.state.agents, {})


if __name__ == "__main__":
    unittest.main()
