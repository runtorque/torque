import unittest
from pathlib import Path

from torque.actions import ActionManager


REPO_ROOT = Path(__file__).resolve().parents[1]


class OneshotActionPromptTests(unittest.TestCase):
    def test_sample_action_prompts_lock_risk_based_verification_language(self):
        fix_prompt = (REPO_ROOT / "actions" / "oneshot" / "fix.yaml").read_text()
        feature_prompt = (REPO_ROOT / "actions" / "oneshot" / "feature.yaml").read_text()

        for prompt in (fix_prompt, feature_prompt):
            self.assertIn("Run the narrowest relevant tests or checks first", prompt)
            self.assertIn("manual smoke check for user-visible changes when practical", prompt)
            self.assertIn("live verification could not be performed", prompt)

        self.assertNotIn("Run the full test suite", fix_prompt)
        self.assertNotIn("Run the test suite before finishing", feature_prompt)

    def test_rendered_project_oneshot_prompts_keep_verification_guidance(self):
        mgr = ActionManager()
        clean_context = {
            "context": {"is_clean": True},
            "task": {
                "title": "Investigate login failure",
                "description": "Only affects the browser flow.",
            },
        }
        follow_up_context = {
            "context": {"is_clean": False},
            "task": {
                "title": "Polish the settings panel",
                "description": "Keep the diff focused.",
            },
        }

        rendered_prompts = [
            mgr.render_prompt("oneshot/fix", {}, base_dir=str(REPO_ROOT), torque_context=clean_context),
            mgr.render_prompt("oneshot/fix", {}, base_dir=str(REPO_ROOT), torque_context=follow_up_context),
            mgr.render_prompt("oneshot/feature", {}, base_dir=str(REPO_ROOT), torque_context=clean_context),
            mgr.render_prompt("oneshot/feature", {}, base_dir=str(REPO_ROOT), torque_context=follow_up_context),
        ]

        for prompt in rendered_prompts:
            self.assertIsNotNone(prompt)
            self.assertIn("narrowest relevant tests or checks", prompt)
            self.assertIn("user-visible changes", prompt)
            self.assertIn("what you did not verify", prompt)
            self.assertIn("live verification could not be performed", prompt)
