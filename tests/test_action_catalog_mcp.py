"""Regression coverage for the agent-facing effective action catalog."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.actions import ActionManager


class EffectiveActionCatalogTests(unittest.TestCase):
    def test_catalog_merges_scopes_with_dispatch_precedence_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_actions = root / "project" / ".torque" / "actions"
            user_actions = root / "user-actions"
            project_actions.mkdir(parents=True)
            user_actions.mkdir()

            (project_actions / "shared.yaml").write_text(
                """name: shared
description: Project definition wins
agent: project-role
worktree: true
labels:
  - project
transitions: []
"""
            )
            (project_actions / "without-transition.yaml").write_text(
                """name: without-transition
description: Missing transitions is still explicit in the catalog
agent:
  name_prefix: inline-project
labels:
  - project
"""
            )
            (user_actions / "shared.yaml").write_text(
                """name: shared
description: Shadowed user definition
agent: shadowed-user-role
transitions:
  - action: never-used
"""
            )
            (user_actions / "user-only.yaml").write_text(
                """name: user-only
description: User action remains available
agent: user-role
worktree: false
labels:
  - user
transitions:
  - action: without-transition
    when: hand off
"""
            )

            with mock.patch(
                "torque.actions.os.path.expanduser",
                side_effect=lambda value: (
                    str(user_actions)
                    if value == "~/.torque/actions" else value
                ),
            ):
                catalog = ActionManager().list_effective_actions(
                    str(root / "project")
                )

        by_name = {action["name"]: action for action in catalog}
        self.assertEqual(set(by_name), {
            "shared", "without-transition", "user-only",
        })
        self.assertEqual(by_name["shared"]["scope"], "project")
        self.assertEqual(by_name["shared"]["description"], "Project definition wins")
        self.assertEqual(by_name["shared"]["transitions"], [])
        self.assertTrue(by_name["shared"]["worktree"])
        self.assertEqual(by_name["shared"]["labels"], ["project"])
        self.assertEqual(
            by_name["shared"]["agent"],
            {"kind": "role", "name": "project-role"},
        )

        # Missing and explicit-empty YAML declarations both mean no legal
        # derives, so callers never have to infer from an omitted key.
        self.assertEqual(by_name["without-transition"]["transitions"], [])
        self.assertIsNone(by_name["without-transition"]["worktree"])
        self.assertEqual(
            by_name["without-transition"]["agent"],
            {"kind": "inline", "name": "inline-project"},
        )
        self.assertEqual(by_name["user-only"]["scope"], "user")
        self.assertEqual(
            by_name["user-only"]["transitions"],
            [{"action": "without-transition", "when": "hand off"}],
        )
        self.assertFalse(by_name["user-only"]["worktree"])
        self.assertEqual(by_name["user-only"]["labels"], ["user"])
        self.assertEqual(
            by_name["user-only"]["agent"],
            {"kind": "role", "name": "user-role"},
        )


if __name__ == "__main__":
    unittest.main()
