"""Regression tests for explicit action binding at the dispatch boundary."""

import asyncio
import importlib
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


if __name__ == "__main__":
    unittest.main()
