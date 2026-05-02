"""Catalog-level tests for the deliverable contract rollout (TORQUE:245).

Confirms that:
- ``oneshot/investigate`` and ``oneshot/audit`` ship a deliverable contract.
- ``feature/research`` declares a plan deliverable while preserving its
  existing prompt text and transitions (including the user-approval ask).
- Each action's dispatch postscript injects the Deliverable contract block
  end-to-end (loader -> ``render_action`` -> ``build_dispatch_postscript``).
"""

import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.actions import ActionManager
from torque.server_prompts import build_dispatch_postscript


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / ".torque" / "actions"


class CatalogDeliverableContractTests(unittest.TestCase):
    """The catalog files load with a parsed deliverable contract."""

    def setUp(self):
        self.mgr = ActionManager()

    def _render(self, name: str) -> dict:
        rendered = self.mgr.render_action(
            (CATALOG_DIR / f"{name}.yaml").read_text(),
            {"TASK": "Look into X"},
        )
        self.assertIsNotNone(rendered)
        return rendered

    def test_oneshot_investigate_declares_report_deliverable(self):
        rendered = self._render("oneshot/investigate")
        self.assertEqual(
            rendered["deliverable"],
            {
                "required": True,
                "type": "report",
                "format": "markdown",
                "artifact_title": "Investigation report",
            },
        )

    def test_oneshot_audit_declares_report_deliverable(self):
        rendered = self._render("oneshot/audit")
        self.assertEqual(
            rendered["deliverable"],
            {
                "required": True,
                "type": "report",
                "format": "markdown",
                "artifact_title": "Audit report",
            },
        )

    def test_feature_research_declares_plan_deliverable(self):
        rendered = self._render("feature/research")
        self.assertEqual(
            rendered["deliverable"],
            {
                "required": True,
                "type": "plan",
                "format": "markdown",
                "artifact_title": "Implementation plan",
            },
        )

    def test_feature_research_preserves_existing_transitions(self):
        # The deliverable rollout MUST NOT clobber the existing pipeline
        # transitions: the engineer-approved feature/implement self-transition
        # and the torque_ask user-approval path.
        rendered = self._render("feature/research")
        transitions = rendered["transitions"]
        actions = [
            tr.get("action") for tr in transitions if tr.get("action")
        ]
        self.assertIn("feature/implement", actions)
        self.assertTrue(
            any(tr.get("ask") for tr in transitions),
            "feature/research must keep its torque_ask transition for "
            "user-approval flows",
        )

    def test_feature_research_prompt_unchanged_on_core_phrases(self):
        rendered = self._render("feature/research")
        prompt = rendered["prompt"]
        # Smoke-check the existing voice/structure is intact.
        for needle in (
            "Phase 1: Research",
            "Phase 2: Plan",
            "Engineer approval is enough",
            "Human approval is required",
            "torque_ask",
        ):
            self.assertIn(needle, prompt)


class CatalogDispatchPostscriptTests(unittest.TestCase):
    """End-to-end: rendering -> postscript injects the Deliverable block."""

    def setUp(self):
        self.mgr = ActionManager()

    def _postscript_for(self, name: str, **postscript_kwargs) -> str:
        rendered = self.mgr.render_action(
            (CATALOG_DIR / f"{name}.yaml").read_text(),
            {"TASK": "Look into X"},
        )
        deliverable = rendered["deliverable"]
        return build_dispatch_postscript(
            transitions=rendered["transitions"],
            is_clean=True,
            deliverable_required=deliverable["required"],
            deliverable_type=deliverable["type"],
            deliverable_format=deliverable["format"],
            deliverable_artifact_title=deliverable["artifact_title"],
            task_title="Look into X",
            **postscript_kwargs,
        )

    def test_oneshot_investigate_postscript_contains_contract(self):
        ps = self._postscript_for("oneshot/investigate")
        self.assertIn("Deliverable contract", ps)
        self.assertIn("torque_task_upload_artifact", ps)
        self.assertIn("Investigation report", ps)
        self.assertIn("report", ps)
        self.assertIn("markdown", ps)

    def test_oneshot_audit_postscript_contains_contract(self):
        ps = self._postscript_for("oneshot/audit")
        self.assertIn("Deliverable contract", ps)
        self.assertIn("Audit report", ps)
        self.assertIn("torque_task_upload_artifact", ps)

    def test_feature_research_postscript_contains_contract_and_transitions(self):
        ps = self._postscript_for("feature/research")
        # Deliverable contract is present...
        self.assertIn("Deliverable contract", ps)
        self.assertIn("Implementation plan", ps)
        self.assertIn("plan", ps)
        # ...AND the user-approval ask path is still wired through.
        self.assertIn("torque_ask", ps)
        # The block precedes the completion-paths section so the worker
        # reads the contract before deciding which path to take.
        self.assertLess(
            ps.index("Deliverable contract"),
            ps.index("Available completion paths"),
        )


if __name__ == "__main__":
    unittest.main()
