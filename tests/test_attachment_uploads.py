import asyncio
import tempfile
import unittest
from pathlib import Path

from torque.attachment_uploads import (
    AttachmentUploadError,
    save_message_attachment_bytes,
)


class MessageAttachmentUploadTests(unittest.TestCase):
    def test_saves_image_under_attachments_dir_with_sanitized_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = asyncio.run(save_message_attachment_bytes(
                agent_id="agent/one",
                filename="../../Screen Shot 1.png",
                mime_type="image/png",
                data=b"png-data",
                attachments_dir=Path(tmpdir),
            ))

            saved = Path(entry["path"])
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_bytes(), b"png-data")
            self.assertEqual(saved.parent, Path(tmpdir))
            self.assertTrue(saved.name.startswith("agent-one-"))
            self.assertTrue(saved.name.endswith("-Screen_Shot_1.png"))
            self.assertEqual(entry["mime_type"], "image/png")
            self.assertEqual(entry["size_bytes"], len(b"png-data"))

    def test_rejects_non_image_mime_without_writing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(AttachmentUploadError) as ctx:
                asyncio.run(save_message_attachment_bytes(
                    agent_id="agent-1",
                    filename="notes.txt",
                    mime_type="text/plain",
                    data=b"hello",
                    attachments_dir=Path(tmpdir),
                ))

            self.assertIn("Only PNG", str(ctx.exception))
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

    def test_oversized_file_is_rejected_without_partial_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(AttachmentUploadError) as ctx:
                asyncio.run(save_message_attachment_bytes(
                    agent_id="agent-1",
                    filename="huge.png",
                    mime_type="image/png",
                    data=b"abcdef",
                    attachments_dir=Path(tmpdir),
                    max_bytes=5,
                ))

            self.assertEqual(ctx.exception.status, 413)
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

    def test_filename_extension_is_forced_to_match_image_mime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = asyncio.run(save_message_attachment_bytes(
                agent_id="agent-1",
                filename="surprise.php",
                mime_type="image/jpeg; charset=binary",
                data=b"jpg",
                attachments_dir=Path(tmpdir),
            ))

            self.assertTrue(Path(entry["path"]).name.endswith("-surprise.jpg"))
            self.assertEqual(entry["mime_type"], "image/jpeg")


if __name__ == "__main__":
    unittest.main()
