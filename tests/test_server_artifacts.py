import importlib
import tempfile
import unittest
from pathlib import Path


class ServerArtifactHelperTests(unittest.TestCase):
    def setUp(self):
        self.helper = importlib.import_module("loom.server_artifacts")
        self.helper = importlib.reload(self.helper)

    def test_store_task_upload_from_inline_text_persists_file_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = self.helper.ATTACHMENTS_DIR
            self.helper.ATTACHMENTS_DIR = Path(tmpdir)
            try:
                artifact = self.helper.store_task_upload(
                    task_id="task-1",
                    filename="pytest.log",
                    content_text="E assert 1 == 2\n",
                    provenance={
                        "source": "agent",
                        "agent_id": "agent-1",
                        "agent_name": "worker",
                    },
                )
                serialized = self.helper.serialize_task_artifact(
                    artifact,
                    task_id="task-1",
                    task_label="Investigate flaky run",
                )
                self.assertTrue(Path(artifact["path"]).is_file())
            finally:
                self.helper.ATTACHMENTS_DIR = original_dir

        self.assertEqual(artifact["type"], "test_report")
        self.assertEqual(artifact["provenance"]["agent_id"], "agent-1")
        self.assertEqual(artifact["provenance"]["task_id"], "task-1")
        self.assertEqual(artifact["lifecycle"]["owner"], "task")
        self.assertEqual(artifact["metadata"]["size_bytes"], len(b"E assert 1 == 2\n"))
        self.assertEqual(serialized["url"], "/attachments/task-1/pytest.log")
        self.assertEqual(serialized["task_label"], "Investigate flaky run")

        digest_text = self.helper.describe_task_artifact_for_digest(
            artifact,
            task_id="task-1",
            task_label="Investigate flaky run",
        )
        self.assertIn("[test report] pytest.log", digest_text)
        self.assertIn("/attachments/task-1/pytest.log", digest_text)
        self.assertIn("uploaded report", digest_text)
        self.assertIn("E assert 1 == 2", digest_text)

    def test_store_task_upload_dedupes_existing_filenames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = self.helper.ATTACHMENTS_DIR
            self.helper.ATTACHMENTS_DIR = Path(tmpdir)
            try:
                first = self.helper.store_task_upload(
                    task_id="task-1",
                    filename="report.txt",
                    content_text="first",
                )
                second = self.helper.store_task_upload(
                    task_id="task-1",
                    filename="report.txt",
                    content_text="second",
                )
            finally:
                self.helper.ATTACHMENTS_DIR = original_dir

        self.assertEqual(first["filename"], "report.txt")
        self.assertEqual(second["filename"], "report_1.txt")

    def test_remove_task_owned_artifacts_by_filename_keeps_external_refs(self):
        artifacts = [
            {
                "type": "image",
                "filename": "screenshot.png",
                "path": "/tmp/task-1/screenshot.png",
                "lifecycle": {"owner": "task", "cleanup": "delete_with_task"},
            },
            {
                "type": "file_ref",
                "filename": "screenshot.png",
                "path": "/repo/docs/screenshot.png",
                "lifecycle": {"owner": "external", "cleanup": "keep"},
            },
        ]

        kept = self.helper.remove_task_owned_artifacts_by_filename(
            artifacts,
            "screenshot.png",
            task_id="task-1",
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["lifecycle"]["owner"], "external")

    def test_serialize_task_for_mcp_adds_combined_task_artifacts(self):
        task = {
            "id": "task-1",
            "task": "Review auth patch",
            "attachments": [
                {"path": "/tmp/task-1/image.png", "filename": "image.png"}
            ],
            "artifacts": [
                {"type": "log", "title": "pytest", "path": "/tmp/task-1/pytest.log"}
            ],
        }

        serialized = self.helper.serialize_task_for_mcp(task)

        self.assertEqual(len(serialized["task_artifacts"]), 2)
        self.assertEqual(serialized["task_artifacts"][0]["type"], "image")
        self.assertEqual(
            serialized["task_artifacts"][0]["url"],
            "/attachments/task-1/image.png",
        )
        self.assertEqual(serialized["task_artifacts"][1]["task_label"], "Review auth patch")

    def test_store_task_upload_rejects_remote_urls(self):
        with self.assertRaises(ValueError):
            self.helper.store_task_upload(
                task_id="task-1",
                local_path="https://example.com/report.txt",
            )

    def test_store_preserved_merge_diff_writes_diff_artifact_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = self.helper.ATTACHMENTS_DIR
            self.helper.ATTACHMENTS_DIR = Path(tmpdir)
            try:
                artifact = self.helper.store_preserved_merge_diff(
                    task_id="task-1",
                    patch_text="diff --git a/README.md b/README.md\n+hello\n",
                    worktree_branch="loom/worker",
                    base_branch="main",
                    merge_commit_sha="abc123",
                    boundary_task_id="task-1",
                    boundary_recorded_at="2026-04-10T00:00:00+00:00",
                    boundary_task_title="Review worker patch",
                    diff_stats={"files": 1, "insertions": 1, "deletions": 0},
                    diff_files=[{
                        "path": "README.md",
                        "status": "modified",
                        "insertions": 1,
                        "deletions": 0,
                        "binary": False,
                    }],
                    agent_id="agent-1",
                    agent_name="worker",
                )
                self.assertTrue(Path(artifact["path"]).is_file())
            finally:
                self.helper.ATTACHMENTS_DIR = original_dir

        self.assertEqual(artifact["type"], "diff")
        self.assertEqual(artifact["prompt"]["mode"], "summary")
        self.assertEqual(artifact["provenance"]["source"], "loom")
        self.assertTrue(artifact["metadata"]["preserved_on_merge"])
        self.assertEqual(artifact["metadata"]["merge_commit_sha"], "abc123")
        self.assertEqual(artifact["metadata"]["stats"]["files"], 1)
        self.assertEqual(artifact["metadata"]["files"][0]["path"], "README.md")
        self.assertIn("pre-merge patch", artifact["summary"])
        digest_text = self.helper.describe_task_artifact_for_digest(
            artifact,
            task_id="task-1",
            task_label="Review worker patch",
        )
        self.assertIn("/attachments/task-1/loom_worker-pre-merge.patch", digest_text)
        self.assertIn("README.md (+1/-0)", digest_text)

    def test_finalize_task_attachments_moves_draft_uploads_to_canonical_task_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = self.helper.ATTACHMENTS_DIR
            self.helper.ATTACHMENTS_DIR = Path(tmpdir)
            try:
                draft_artifact = self.helper.store_task_upload(
                    task_id="draft-abcd1234",
                    filename="pytest.log",
                    content_text="ok\n",
                )
                attachments = [{
                    "path": str(self.helper.ATTACHMENTS_DIR / "draft-abcd1234" / "image.png"),
                    "filename": "image.png",
                }]
                (self.helper.ATTACHMENTS_DIR / "draft-abcd1234" / "image.png").write_text("img")

                new_attachments, new_artifacts = self.helper.finalize_task_attachments(
                    attachments,
                    [draft_artifact],
                    draft_task_id="draft-abcd1234",
                    task_id="LOOM:7",
                )
            finally:
                self.helper.ATTACHMENTS_DIR = original_dir

        self.assertTrue(Path(new_attachments[0]["path"]).name == "image.png")
        self.assertIn("/LOOM:7/", new_attachments[0]["path"])
        self.assertIn("/LOOM:7/", new_artifacts[0]["path"])
        self.assertEqual(new_artifacts[0]["provenance"]["task_id"], "LOOM:7")


if __name__ == "__main__":
    unittest.main()
