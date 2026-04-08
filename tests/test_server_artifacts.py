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


if __name__ == "__main__":
    unittest.main()
