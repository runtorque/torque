"""Helpers for direct-to-agent image attachment uploads."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ATTACHMENTS_DIR

ALLOWED_MESSAGE_ATTACHMENT_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})
MESSAGE_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_ALLOWED_SUFFIXES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
    "image/gif": {".gif"},
}


class AttachmentUploadError(ValueError):
    """Validation error raised while storing a message attachment."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_attachment_mime_type(mime_type: str | None) -> str:
    """Return a lower-case MIME type without parameters."""

    return str(mime_type or "").split(";", 1)[0].strip().lower()


def _safe_token(value: str | None, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    token = token.strip(".-_")
    if not token:
        token = fallback
    return token[:80]


def sanitize_attachment_filename(filename: str | None, mime_type: str) -> str:
    """Return a safe basename with an image extension matching ``mime_type``."""

    raw = str(filename or "screenshot")
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
    raw = re.sub(r"[\x00-\x1f\x7f]+", "", raw).strip()
    raw = raw.replace(" ", "_")
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    if not raw:
        raw = "screenshot"

    expected_ext = _MIME_EXTENSIONS.get(mime_type, ".img")
    suffix = Path(raw).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES.get(mime_type, set()):
        stem = Path(raw).stem if suffix else raw
        stem = stem.strip("._-") or "screenshot"
        raw = stem + expected_ext

    if len(raw) > 120:
        suffix = Path(raw).suffix
        stem = Path(raw).stem[: max(1, 120 - len(suffix))]
        raw = stem.rstrip("._-") + suffix
    return raw


def build_attachment_destination(
    *,
    agent_id: str,
    filename: str,
    mime_type: str,
    attachments_dir: Path | None = None,
) -> Path:
    """Build a unique destination path for a direct message attachment."""

    base_dir = Path(attachments_dir or ATTACHMENTS_DIR).expanduser()
    safe_agent = _safe_token(agent_id, "agent")
    safe_name = sanitize_attachment_filename(filename, mime_type)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    dest = base_dir / f"{safe_agent}-{timestamp}-{uuid.uuid4().hex[:8]}-{safe_name}"
    return dest


def _ensure_child_path(base_dir: Path, dest: Path) -> None:
    base_resolved = base_dir.resolve()
    dest_parent = dest.parent.resolve()
    if dest_parent != base_resolved:
        raise AttachmentUploadError("invalid attachment path", status=400)


async def save_message_attachment_stream(
    *,
    agent_id: str,
    filename: str,
    mime_type: str,
    stream,
    attachments_dir: Path | None = None,
    max_bytes: int = MESSAGE_ATTACHMENT_MAX_BYTES,
) -> dict:
    """Validate and store one direct-to-agent image attachment.

    ``stream`` must provide aiohttp's ``read_chunk()`` coroutine.  The file is
    written through a temporary path and atomically renamed, so over-limit or
    invalid uploads never leave a partial destination file behind.
    """

    clean_mime = normalize_attachment_mime_type(mime_type)
    if clean_mime not in ALLOWED_MESSAGE_ATTACHMENT_MIME_TYPES:
        raise AttachmentUploadError(
            "Only PNG, JPEG, WebP, or GIF image files can be attached.",
            status=400,
        )

    if not str(agent_id or "").strip():
        raise AttachmentUploadError("missing agent_id", status=400)

    base_dir = Path(attachments_dir or ATTACHMENTS_DIR).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    dest = build_attachment_destination(
        agent_id=agent_id,
        filename=filename,
        mime_type=clean_mime,
        attachments_dir=base_dir,
    )
    _ensure_child_path(base_dir, dest)
    tmp = base_dir / f".{dest.name}.{uuid.uuid4().hex}.tmp"

    size = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                chunk = await stream.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise AttachmentUploadError(
                        f"Image attachments must be {max_bytes // (1024 * 1024)} MB or smaller.",
                        status=413,
                    )
                fh.write(chunk)
        if size <= 0:
            raise AttachmentUploadError("attachment file is empty", status=400)
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise

    return {
        "path": str(dest),
        "filename": dest.name,
        "mime_type": clean_mime,
        "size_bytes": size,
    }


class _BytesUploadStream:
    def __init__(self, data: bytes, chunk_size: int = 64 * 1024):
        self._data = data
        self._chunk_size = chunk_size
        self._offset = 0

    async def read_chunk(self):
        if self._offset >= len(self._data):
            return b""
        end = min(len(self._data), self._offset + self._chunk_size)
        chunk = self._data[self._offset:end]
        self._offset = end
        return chunk


async def save_message_attachment_bytes(
    *,
    agent_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
    attachments_dir: Path | None = None,
    max_bytes: int = MESSAGE_ATTACHMENT_MAX_BYTES,
) -> dict:
    """Test-friendly wrapper around ``save_message_attachment_stream``."""

    return await save_message_attachment_stream(
        agent_id=agent_id,
        filename=filename,
        mime_type=mime_type,
        stream=_BytesUploadStream(bytes(data or b"")),
        attachments_dir=attachments_dir,
        max_bytes=max_bytes,
    )
