import unittest
from pathlib import Path

from torque.actions import ActionManager


REPO_ROOT = Path(__file__).resolve().parents[1]


class FeaturePipelinePromptTests(unittest.TestCase):
    def test_feature_research_prompt_locks_approval_paths_and_progress_signal(self):
        prompt = (REPO_ROOT / ".torque" / "actions" / "feature" / "research.yaml").read_text()

        self.assertIn("Engineer approval is enough", prompt)
        self.assertIn("Human approval is required", prompt)
        self.assertIn("torque_ask(question=\"Clarification needed\"", prompt)
        self.assertIn("Do NOT derive to implementation before the appropriate approval", prompt)
        self.assertIn("send an immediate `torque_progress(message=\"...\")` update", prompt)
        self.assertIn("compact task brief for the implementer", prompt)
        self.assertIn("acceptance criteria", prompt)
        self.assertIn("handoff evidence required", prompt)
        self.assertNotIn("ask the user for clarification before proceeding", prompt)

    def test_feature_review_prompts_lock_reporting_sections(self):
        sample_prompt = (REPO_ROOT / "actions" / "feature" / "review.yaml").read_text()
        project_prompt = (REPO_ROOT / ".torque" / "actions" / "feature" / "review.yaml").read_text()
        rendered_prompt = ActionManager().render_prompt(
            "feature/review",
            {},
            base_dir=str(REPO_ROOT),
            torque_context={
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
        self.assertIn("Follow-up classification", sample_prompt)
        self.assertIn("user_ask", sample_prompt)
        self.assertIn("torque_derive", project_prompt)
        self.assertIn("torque_done", project_prompt)
        self.assertIn("## Closeout (mandatory)", project_prompt)
        self.assertIn("Replying via `engineer_reply`", project_prompt)

        self.assertIsNotNone(rendered_prompt)
        self.assertIn("Verification summary", rendered_prompt)
        self.assertIn("Merge-risk summary", rendered_prompt)
        self.assertIn("Blocking issues", rendered_prompt)
        self.assertIn("Follow-up suggestions", rendered_prompt)
        self.assertIn("Follow-up classification", rendered_prompt)
        self.assertIn("task_derive", rendered_prompt)
        self.assertIn("task_complete", rendered_prompt)
        self.assertIn("deploy or live verification that still needs to happen", rendered_prompt)
        self.assertIn("stated acceptance criteria, non-goals, constraints", rendered_prompt)
        self.assertIn("If you derive a fix, make the handoff self-contained", rendered_prompt)
        self.assertIn("expected fix scope", rendered_prompt)
        self.assertIn("Pick exactly one closeout path", rendered_prompt)
        self.assertIn("No blocking issues: your FINAL action MUST be `torque ai done`", rendered_prompt)
        self.assertIn("Final review verdict: Ship", rendered_prompt)
        self.assertIn("Replying via `agent_reply`", rendered_prompt)
        self.assertTrue(rendered_prompt.rstrip().endswith("silence is read as a stall."))

    def test_feature_review_blocking_closeout_uses_fix_handoff_not_done(self):
        rendered_prompt = ActionManager().render_prompt(
            "feature/review",
            {},
            base_dir=str(REPO_ROOT),
            torque_context={
                "task": {
                    "title": "Review a risky implementation",
                    "description": "Look for blocking issues.",
                    "parent_agent_slug": "impl-risky-change",
                }
            },
        )

        self.assertIsNotNone(rendered_prompt)
        closeout = rendered_prompt.split("## Closeout (mandatory)", 1)[1]
        self.assertIn(
            'Blocking issues found: your FINAL action MUST be `torque ai derive "Fix the issues found" -t feature/implement`',
            closeout,
        )
        self.assertNotIn("-a feature/implement", rendered_prompt)
        self.assertIn("Do NOT call `torque ai done` after deriving the fix task", closeout)
        self.assertIn("Include the original goal, blocking issues, expected fix scope", rendered_prompt)
        self.assertIn("unresolved fix handoff is the valid blocking-review closeout path", rendered_prompt)
        self.assertIn("No blocking issues: your FINAL action MUST be `torque ai done`", closeout)
        self.assertIn("NOT a substitute for either closeout path", closeout)

    def test_feature_implement_prompt_requires_review_derivation_when_requested(self):
        project_prompt = (REPO_ROOT / ".torque" / "actions" / "feature" / "implement.yaml").read_text()
        rendered_prompt = ActionManager().render_prompt(
            "feature/implement",
            {},
            base_dir=str(REPO_ROOT),
            torque_context={
                "task": {
                    "title": "Implement the auth refactor",
                    "description": "Implementation must be derived to feature/review before closeout.",
                }
            },
        )

        reminder = 'MUST call `torque ai derive "<summary>" -t feature/review` before `torque ai done`'
        self.assertIn(reminder, project_prompt)
        self.assertNotIn("-a feature/review", project_prompt)
        self.assertIsNotNone(rendered_prompt)
        self.assertIn(reminder, rendered_prompt)
        self.assertIn("## Handoff evidence", rendered_prompt)
        self.assertIn("Put this evidence in the `feature/review` derive context", rendered_prompt)
        self.assertIn("acceptance criteria satisfied", rendered_prompt)
        self.assertNotIn("-a feature/review", rendered_prompt)
