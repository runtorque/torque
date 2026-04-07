import unittest
from pathlib import Path

from loom.actions import ActionManager


REPO_ROOT = Path(__file__).resolve().parents[1]


class FeaturePipelinePromptTests(unittest.TestCase):
    def test_feature_research_prompt_locks_approval_paths_and_progress_signal(self):
        prompt = (REPO_ROOT / ".loom" / "actions" / "feature" / "research.yaml").read_text()

        self.assertIn("Weaver approval is enough", prompt)
        self.assertIn("Human approval is required", prompt)
        self.assertIn("Do NOT derive to implementation before the appropriate approval", prompt)
        self.assertIn("send an immediate `loom_progress(message=\"...\")` update", prompt)

    def test_feature_review_prompts_lock_reporting_sections(self):
        sample_prompt = (REPO_ROOT / "actions" / "feature" / "review.yaml").read_text()
        project_prompt = (REPO_ROOT / ".loom" / "actions" / "feature" / "review.yaml").read_text()
        rendered_prompt = ActionManager().render_prompt(
            "feature/review",
            {},
            base_dir=str(REPO_ROOT),
            loom_context={
                "task": {
                    "title": "Review the auth refactor",
                    "description": "Check the API and UI paths.",
                    "parent_agent_slug": "impl-auth-refactor",
                }
            },
        )

        self.assertIn("Verification summary", sample_prompt)
        self.assertIn("Merge-risk summary", sample_prompt)
        self.assertIn("Blocking issues", sample_prompt)
        self.assertIn("Follow-up suggestions", sample_prompt)
        self.assertIn("loom_ask", sample_prompt)
        self.assertIn("loom_derive", project_prompt)
        self.assertIn("loom_done", project_prompt)

        self.assertIsNotNone(rendered_prompt)
        self.assertIn("Verification summary", rendered_prompt)
        self.assertIn("Merge-risk summary", rendered_prompt)
        self.assertIn("Blocking issues", rendered_prompt)
        self.assertIn("Follow-up suggestions", rendered_prompt)
        self.assertIn("loom_derive", rendered_prompt)
        self.assertIn("loom_done", rendered_prompt)
        self.assertIn("deploy or live verification that still needs to happen", rendered_prompt)
