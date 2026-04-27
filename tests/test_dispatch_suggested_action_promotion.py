"""Regression tests for LOOM:262 — dispatch must auto-promote
``suggested_action`` to ``action_name`` so the action's deliverable
contract / template / transitions bind without manual ``engineer_task_edit``
intervention.

Covers:
- ``_promote_suggested_action`` helper unit behaviour (promote / no-op /
  override).
- End-to-end binding: after promotion runs, ``action_mgr.get_deliverable``
  + the dispatch_task late-bind block stamps ``deliverable_required`` and
  the typed-contract fields onto the task. Mirrors the slice of
  ``cmd=dispatch_task`` between the suggested_action promotion and the
  prompt-rendering pipeline (which is exercised by the existing catalog
  gate test).
"""

import importlib
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


REPO_ROOT = Path(__file__).resolve().parents[1]


class PromoteSuggestedActionUnitTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module("loom.state")
        )
        self.server_mod = importlib.reload(
            importlib.import_module("loom.server")
        )
        self.state = self.state_mod.MatrixState()
        self.state.add_group("g")

    def _make_task(self, *, action_name="", suggested_action=""):
        task = self.state.board_add_task(
            "Investigate dispatch flow",
            "g",
            lane="Backlog",
            id="task-1",
            action_name=action_name,
            suggested_action=suggested_action,
        )
        self.assertIsNotNone(task)
        return task

    def test_empty_action_name_promotes_suggested_action(self):
        task = self._make_task(suggested_action="oneshot/investigate")
        promoted = self.server_mod._promote_suggested_action(self.state, task)

        self.assertEqual(promoted.action_name, "oneshot/investigate")
        # Suggested action stays — promotion records the binding without
        # discarding the architect's hint.
        self.assertEqual(promoted.suggested_action, "oneshot/investigate")
        # State row was updated, not just the local object.
        self.assertEqual(
            self.state.board_tasks["task-1"].action_name,
            "oneshot/investigate",
        )

    def test_explicit_action_name_overrides_suggestion(self):
        task = self._make_task(
            action_name="oneshot/fix",
            suggested_action="oneshot/investigate",
        )
        promoted = self.server_mod._promote_suggested_action(self.state, task)

        # action_name was already authored explicitly (e.g. via
        # engineer_task_edit) — promotion must not clobber it.
        self.assertEqual(promoted.action_name, "oneshot/fix")
        self.assertEqual(
            self.state.board_tasks["task-1"].action_name,
            "oneshot/fix",
        )

    def test_neither_field_set_is_a_noop(self):
        task = self._make_task()
        promoted = self.server_mod._promote_suggested_action(self.state, task)

        self.assertEqual(promoted.action_name, "")
        self.assertEqual(promoted.suggested_action, "")
        self.assertEqual(self.state.board_tasks["task-1"].action_name, "")

    def test_whitespace_only_action_name_is_treated_as_empty(self):
        task = self._make_task(
            action_name="   ",
            suggested_action="oneshot/investigate",
        )
        promoted = self.server_mod._promote_suggested_action(self.state, task)

        self.assertEqual(promoted.action_name, "oneshot/investigate")

    def test_none_task_is_a_safe_noop(self):
        # Defensive — dispatch_task fetches by id and may pass through
        # before the not-found early return on rare timing windows.
        self.assertIsNone(
            self.server_mod._promote_suggested_action(self.state, None)
        )


class PromoteAndContractBindIntegrationTests(unittest.TestCase):
    """Verify the late-bind contract path in dispatch_task picks up the
    promoted action_name and stamps the deliverable contract on the task.
    Mirrors loom/server.py::dispatch_task lines around the
    ``_promote_suggested_action`` -> ``get_deliverable`` -> ``board_update_task``
    sequence so the regression is caught even if the call ordering inside
    dispatch_task is later refactored.
    """

    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module("loom.state")
        )
        self.actions_mod = importlib.reload(
            importlib.import_module("loom.actions")
        )
        self.server_mod = importlib.reload(
            importlib.import_module("loom.server")
        )

    def _build_state_and_task(self, *, action_name="", suggested_action=""):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        task = state.board_add_task(
            "Investigate dispatch regression",
            "g",
            lane="Backlog",
            id="task-deliv",
            action_name=action_name,
            suggested_action=suggested_action,
        )
        self.assertIsNotNone(task)
        return state, task

    def _bind_contract_like_dispatch_task(self, state, task, action_mgr,
                                          base_dir):
        """Replay the late-bind block from dispatch_task verbatim (server.py
        ~lines 9056-9073). Keeps the test focused on the promotion + bind
        path without spinning up the full prompt pipeline."""
        if (task.action_name and not task.deliverable_required
                and not task.deliverable_type):
            try:
                deliv = action_mgr.get_deliverable(
                    task.action_name, base_dir)
            except Exception:
                deliv = None
            if deliv and deliv.get("required"):
                state.board_update_task(
                    task.id,
                    deliverable_required=bool(deliv["required"]),
                    deliverable_type=deliv["type"],
                    deliverable_format=deliv["format"],
                    deliverable_artifact_title=deliv["artifact_title"],
                )
                task = state.board_tasks.get(task.id) or task
        return task

    def test_suggested_only_dispatch_binds_deliverable_contract(self):
        action_mgr = self.actions_mod.ActionManager()
        state, task = self._build_state_and_task(
            suggested_action="oneshot/investigate"
        )

        task = self.server_mod._promote_suggested_action(state, task)
        self.assertEqual(task.action_name, "oneshot/investigate")

        task = self._bind_contract_like_dispatch_task(
            state, task, action_mgr, base_dir=str(REPO_ROOT)
        )
        refreshed = state.board_tasks["task-deliv"]
        self.assertTrue(
            refreshed.deliverable_required,
            "Promoted action should bring its deliverable contract",
        )
        self.assertEqual(refreshed.deliverable_type, "report")
        self.assertEqual(refreshed.deliverable_format, "markdown")
        self.assertEqual(
            refreshed.deliverable_artifact_title,
            "Investigation report",
        )

    def test_explicit_action_name_blocks_promotion_and_uses_its_contract(self):
        # Engineer override path: action_name authored ahead of dispatch.
        # The action's own contract should bind; suggestion ignored.
        action_mgr = self.actions_mod.ActionManager()
        state, task = self._build_state_and_task(
            action_name="oneshot/audit",
            suggested_action="oneshot/investigate",
        )

        task = self.server_mod._promote_suggested_action(state, task)
        self.assertEqual(task.action_name, "oneshot/audit")

        task = self._bind_contract_like_dispatch_task(
            state, task, action_mgr, base_dir=str(REPO_ROOT)
        )
        refreshed = state.board_tasks["task-deliv"]
        self.assertTrue(refreshed.deliverable_required)
        # Both oneshot/audit and oneshot/investigate use type=report —
        # disambiguate via the artifact_title to confirm the bind
        # followed action_name (audit), not suggested_action (investigate).
        self.assertEqual(
            refreshed.deliverable_artifact_title, "Audit report"
        )

    def test_neither_field_set_leaves_task_uncontracted(self):
        action_mgr = self.actions_mod.ActionManager()
        state, task = self._build_state_and_task()

        task = self.server_mod._promote_suggested_action(state, task)
        task = self._bind_contract_like_dispatch_task(
            state, task, action_mgr, base_dir=str(REPO_ROOT)
        )
        refreshed = state.board_tasks["task-deliv"]
        self.assertEqual(refreshed.action_name, "")
        self.assertFalse(refreshed.deliverable_required)
        self.assertEqual(refreshed.deliverable_type, "")


class DispatchTaskInvokesPromotionTests(unittest.TestCase):
    """Lightweight white-box check that ``cmd=dispatch_task`` calls
    ``_promote_suggested_action`` before resolving the action template.

    The dispatch_task body is too large to drive end-to-end here without
    full bridge / worktree / prompt-pipeline plumbing (the existing
    catalog gate test exercises the late-bind block downstream from
    promotion). This guard keeps the call site from drifting away from
    the public helper.
    """

    def setUp(self):
        install_aiohttp_stub()
        self.server_mod = importlib.reload(
            importlib.import_module("loom.server")
        )

    def test_dispatch_task_handler_references_promote_helper(self):
        source = Path(self.server_mod.__file__).read_text()
        # Anchor to the dispatch_task handler so we don't accidentally
        # match an unrelated future caller of the helper.
        marker = 'elif cmd == "dispatch_task":'
        idx = source.find(marker)
        self.assertGreaterEqual(
            idx, 0, "dispatch_task command branch missing")
        # Look only inside the handler region (next ~6000 chars covers
        # the whole branch comfortably without spilling into the next
        # elif cmd == ... block in any realistic refactor).
        region = source[idx:idx + 6000]
        self.assertIn("_promote_suggested_action(state, task)", region)


if __name__ == "__main__":
    unittest.main()
