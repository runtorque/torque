"""Helpers for task-linked artifact uploads and MCP serialization."""

from __future__ import annotations

import base64
import binascii
import mimetypes
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .artifacts import (
    attachment_to_artifact,
    normalize_artifact,
    normalize_artifacts,
    normalize_attachments,
)
from .config import ATTACHMENTS_DIR

INLINE_PREVIEW_LIMIT = 256 * 1024


def _attachment_url(task_id: str, filename: str) -> str:
    if not task_id or not filename:
        return ""
    return "/attachments/" + quote(task_id, safe="") + "/" + quote(
        filename, safe=""
    )


def sanitize_uploaded_filename(filename: str, *, default: str = "artifact.bin") -> str:
    name = str(filename or "").strip()
    if not name:
        name = default
    name = Path(name).name
    name = name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return name or default


def dedupe_uploaded_filename(directory: Path, filename: str) -> str:
    safe = sanitize_uploaded_filename(filename)
    dest = directory / safe
    if not dest.exists():
        return safe
    stem = dest.stem
    suffix = dest.suffix
    index = 1
    while True:
        candidate = f"{stem}_{index}{suffix}"
        if not (directory / candidate).exists():
            return candidate
        index += 1


def infer_artifact_type(filename: str, mime_type: str = "") -> str:
    lower_name = str(filename or "").lower()
    lower_mime = str(mime_type or "").lower()
    if lower_mime.startswith("image/"):
        return "image"
    if lower_name.endswith((".diff", ".patch")):
        return "diff"
    if any(token in lower_name for token in (
        "pytest", "junit", "tap", "coverage", "report", "results"
    )):
        return "test_report"
    if lower_name.endswith((".log", ".out", ".err", ".trace", ".txt")):
        return "log"
    if lower_name.endswith(
        (".md", ".markdown", ".html", ".htm", ".json", ".yaml", ".yml",
         ".xml", ".csv")
    ):
        return "generated_doc"
    if lower_mime.startswith("text/") or "json" in lower_mime or "xml" in lower_mime:
        return "generated_doc"
    return "file_ref"


def _default_prompt_mode(artifact_type: str) -> str:
    if artifact_type in {"image", "file_ref"}:
        return "path"
    if artifact_type == "snippet":
        return "inline"
    return "summary"


def _is_text_like(artifact_type: str, mime_type: str = "") -> bool:
    if artifact_type in {
        "snippet", "log", "diff", "test_report", "generated_doc"
    }:
        return True
    lower = str(mime_type or "").lower()
    return (
        lower.startswith("text/")
        or "json" in lower
        or "xml" in lower
        or "javascript" in lower
    )


def _inline_preview_text(data: bytes, artifact_type: str, mime_type: str = "") -> str:
    if not data or len(data) > INLINE_PREVIEW_LIMIT:
        return ""
    if not _is_text_like(artifact_type, mime_type):
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _summary_for_upload(size_bytes: int, artifact_type: str) -> str:
    parts = []
    if size_bytes >= 1024 * 1024:
        parts.append(f"{(size_bytes / (1024 * 1024)):.1f} MB")
    elif size_bytes >= 1024:
        parts.append(f"{round(size_bytes / 1024)} KB")
    else:
        parts.append(f"{size_bytes} B")
    if artifact_type == "diff":
        parts.append("uploaded patch")
    elif artifact_type == "log":
        parts.append("uploaded log")
    elif artifact_type == "test_report":
        parts.append("uploaded report")
    return " | ".join(parts)


def serialize_task_artifact(
    artifact: dict,
    *,
    task_id: str = "",
    task_label: str = "",
) -> dict:
    normalized = normalize_artifact(artifact)
    if not normalized:
        return {}
    payload = dict(normalized)
    payload["task_id"] = task_id
    payload["task_label"] = task_label
    payload["url"] = _attachment_url(task_id, payload.get("filename", ""))
    payload["created_at"] = (
        ((payload.get("provenance") or {}).get("created_at")) or ""
    )
    size_bytes = ((payload.get("metadata") or {}).get("size_bytes"))
    if size_bytes not in (None, ""):
        payload["size_bytes"] = size_bytes
    return payload


def serialize_task_artifacts(attachments, artifacts, *, task_id: str = "",
                             task_label: str = "") -> list[dict]:
    combined = []
    for index, attachment in enumerate(normalize_attachments(attachments)):
        synthetic = attachment_to_artifact(attachment, index=index)
        if synthetic:
            combined.append(
                serialize_task_artifact(
                    synthetic,
                    task_id=task_id,
                    task_label=task_label,
                )
            )
    for artifact in normalize_artifacts(artifacts):
        serialized = serialize_task_artifact(
            artifact,
            task_id=task_id,
            task_label=task_label,
        )
        if serialized:
            combined.append(serialized)
    return combined


def serialize_task_for_mcp(task) -> dict:
    data = asdict(task) if is_dataclass(task) else dict(task or {})
    attachments = normalize_attachments(data.get("attachments", []))
    raw_artifacts = normalize_artifacts(data.get("artifacts", []))
    data["attachments"] = attachments
    data["artifacts"] = raw_artifacts
    data["task_artifacts"] = serialize_task_artifacts(
        attachments,
        raw_artifacts,
        task_id=str(data.get("id", "") or ""),
        task_label=str(data.get("task", "") or ""),
    )
    return data


def task_owned_artifact_filenames(artifacts, *, task_id: str = "") -> set[str]:
    names: set[str] = set()
    task_dir = ATTACHMENTS_DIR / str(task_id or "")
    for artifact in normalize_artifacts(artifacts):
        lifecycle = artifact.get("lifecycle") or {}
        if lifecycle.get("owner") != "task":
            continue
        filename = str(artifact.get("filename", "") or "").strip()
        path = str(artifact.get("path", "") or "").strip()
        if filename:
            names.add(filename)
            continue
        if path:
            try:
                path_obj = Path(path)
                if task_id and path_obj.parent == task_dir:
                    names.add(path_obj.name)
            except OSError:
                continue
    return names


def remove_task_owned_artifacts_by_filename(artifacts, filename: str, *,
                                            task_id: str = "") -> list[dict]:
    keep = []
    filename = str(filename or "").strip()
    if not filename:
        return normalize_artifacts(artifacts)
    task_dir = ATTACHMENTS_DIR / str(task_id or "")
    for artifact in normalize_artifacts(artifacts):
        lifecycle = artifact.get("lifecycle") or {}
        artifact_filename = str(artifact.get("filename", "") or "").strip()
        artifact_path = str(artifact.get("path", "") or "").strip()
        owns_file = lifecycle.get("owner") == "task"
        if task_id and artifact_path:
            try:
                owns_file = owns_file or Path(artifact_path).parent == task_dir
            except OSError:
                pass
        if owns_file and artifact_filename == filename:
            continue
        keep.append(artifact)
    return keep


def store_task_upload(
    *,
    task_id: str,
    local_path: str = "",
    filename: str = "",
    content_base64: str = "",
    content_text: str = "",
    artifact_type: str = "",
    title: str = "",
    mime_type: str = "",
    summary: str = "",
    prompt_mode: str = "",
    provenance: dict | None = None,
) -> dict:
    source_path = str(local_path or "").strip()
    b64 = str(content_base64 or "").strip()
    text = content_text
    if source_path:
        if "://" in source_path:
            raise ValueError("remote URLs are not supported; use a local path")
        src = Path(source_path).expanduser()
        if not src.is_file():
            raise FileNotFoundError(f"local_path not found: {source_path}")
        raw_bytes = src.read_bytes()
        input_filename = filename or src.name
        resolved_mime = mime_type or mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    elif b64:
        try:
            raw_bytes = base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content_base64 must be valid base64") from exc
        input_filename = filename
        resolved_mime = mime_type or mimetypes.guess_type(input_filename or "")[0] or "application/octet-stream"
    elif text not in (None, ""):
        raw_bytes = str(text).encode("utf-8")
        input_filename = filename
        resolved_mime = mime_type or "text/plain"
    else:
        raise ValueError(
            "provide one of local_path, content_base64, or content_text"
        )

    safe_name = sanitize_uploaded_filename(
        input_filename,
        default="artifact.txt" if text not in (None, "") else "artifact.bin",
    )
    task_dir = ATTACHMENTS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    safe_name = dedupe_uploaded_filename(task_dir, safe_name)
    dest = task_dir / safe_name
    dest.write_bytes(raw_bytes)

    resolved_type = str(artifact_type or "").strip() or infer_artifact_type(
        safe_name, resolved_mime
    )
    created_at = datetime.now(timezone.utc).isoformat()
    preview_text = _inline_preview_text(raw_bytes, resolved_type, resolved_mime)

    artifact = normalize_artifact({
        "type": resolved_type,
        "title": title or safe_name,
        "filename": safe_name,
        "path": str(dest),
        "mime_type": resolved_mime,
        "summary": summary or _summary_for_upload(len(raw_bytes), resolved_type),
        "content": preview_text,
        "prompt": {"mode": prompt_mode or _default_prompt_mode(resolved_type)},
        "metadata": {"size_bytes": len(raw_bytes)},
        "storage": {
            "kind": "file_ref" if resolved_type == "file_ref" else "path",
            "path": str(dest),
            "content": preview_text,
            "line_start": None,
            "line_end": None,
        },
        "provenance": {
            "source": str((provenance or {}).get("source", "") or "agent"),
            "created_at": created_at,
            "agent_id": str((provenance or {}).get("agent_id", "") or ""),
            "agent_name": str((provenance or {}).get("agent_name", "") or ""),
            "task_id": task_id,
        },
        "lifecycle": {"owner": "task", "cleanup": "delete_with_task"},
    })
    return artifact
