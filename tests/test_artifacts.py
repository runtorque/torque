import unittest

from loom.artifacts import (
    artifact_prompt_block,
    legacy_image_prompt_block,
    normalize_artifact,
    task_artifacts,
    upstream_artifact_prompt_block,
)


class TaskArtifactTests(unittest.TestCase):
    def test_normalize_artifact_preserves_metadata_and_lifecycle(self):
        artifact = normalize_artifact({
            "type": "file_ref",
            "title": "auth config",
            "path": "/repo/auth.py",
            "line_start": 12,
            "line_end": 18,
            "prompt": {"mode": "path"},
            "provenance": {"source": "agent", "agent_id": "agent-1"},
        })

        self.assertEqual(artifact["type"], "file_ref")
        self.assertEqual(artifact["storage"]["kind"], "file_ref")
        self.assertEqual(artifact["line_start"], 12)
        self.assertEqual(artifact["line_end"], 18)
        self.assertEqual(artifact["prompt"]["mode"], "path")
        self.assertEqual(artifact["provenance"]["source"], "agent")
        self.assertEqual(artifact["lifecycle"]["owner"], "external")
        self.assertEqual(artifact["lifecycle"]["cleanup"], "keep")

    def test_task_artifacts_combines_legacy_attachments_with_structured_artifacts(self):
        combined = task_artifacts(
            [{"path": "/tmp/screenshot.png", "filename": "screenshot.png"}],
            [{"type": "log", "title": "pytest log", "path": "/tmp/pytest.log"}],
        )

        self.assertEqual([artifact["type"] for artifact in combined],
                         ["image", "log"])
        self.assertTrue(combined[0]["metadata"]["legacy_attachment"])

    def test_prompt_blocks_are_type_aware_and_safe_by_default(self):
        image_block = legacy_image_prompt_block(
            [{"path": "/tmp/screenshot.png", "filename": "screenshot.png"}],
            [],
        )
        artifact_block = artifact_prompt_block([
            {
                "type": "diff",
                "title": "auth patch",
                "path": "/tmp/auth.diff",
                "summary": "Touches login and token parsing",
                "metadata": {"files": 2, "insertions": 18, "deletions": 4},
            },
            {
                "type": "snippet",
                "title": "repro",
                "content": "pytest tests/auth/test_login.py -k invalid_token",
                "prompt": {"mode": "inline"},
            },
        ])

        self.assertIn("## Attached images", image_block)
        self.assertIn("/tmp/screenshot.png", image_block)
        self.assertIn("## Task artifacts", artifact_block)
        self.assertIn("stats: 2 files, +18/-4", artifact_block)
        self.assertIn("inline excerpt follows", artifact_block)
        self.assertIn("pytest tests/auth/test_login.py -k invalid_token",
                      artifact_block)
        self.assertNotIn("/tmp/auth.diff\n```", artifact_block)

    def test_inline_prompt_blocks_use_safe_markdown_fences(self):
        artifact_block = artifact_prompt_block([
            {
                "type": "snippet",
                "title": "prompt-shaped note",
                "content": "before\n```\nafter",
                "prompt": {"mode": "inline"},
            },
        ])

        self.assertIn("inline excerpt follows", artifact_block)
        self.assertIn("````text", artifact_block)
        self.assertIn("\n````", artifact_block)
        self.assertIn("before\n```\nafter", artifact_block)

    def test_upstream_prompt_block_labels_parent_task_and_reuses_safe_shaping(self):
        artifact_block = upstream_artifact_prompt_block([
            {
                "type": "generated_doc",
                "title": "Implementation plan",
                "path": "/tmp/task-parent/plan.md",
                "summary": "Canonical plan for the downstream task",
                "source_task_id": "task-parent",
                "source_task_label": "Research auth patch",
            },
            {
                "type": "snippet",
                "title": "Acceptance criteria",
                "content": "Line 1\n```\nLine 2",
                "prompt": {"mode": "inline"},
                "source_task_id": "task-parent",
                "source_task_label": "Research auth patch",
            },
        ])

        self.assertIn("## Upstream handoff artifacts", artifact_block)
        self.assertIn("From `Research auth patch` (task-parent)", artifact_block)
        self.assertIn("summary: Canonical plan for the downstream task", artifact_block)
        self.assertIn("inline excerpt follows", artifact_block)
        self.assertIn("````text", artifact_block)


if __name__ == "__main__":
    unittest.main()
