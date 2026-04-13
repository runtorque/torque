"""Helpers for task-linked artifact uploads and MCP serialization."""

from __future__ import annotations

import base64
import binascii
import mimetypes
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
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


def _filename_token(value: str, *, default: str = "artifact") -> str:
    raw = str(value or "").strip()
    token = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
        for ch in raw
    ).strip("._")
    return token or default


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


def _merge_diff_stats(stats: dict | None, files) -> dict:
    source = dict(stats or {})
    normalized = {
        "files": 0,
        "insertions": 0,
        "deletions": 0,
    }
    for key in normalized:
        value = source.get(key)
        try:
            if value not in (None, ""):
                normalized[key] = int(value)
        except (TypeError, ValueError):
            pass
    if not normalized["files"] and isinstance(files, list):
        normalized["files"] = len(files)
    if not normalized["insertions"] and isinstance(files, list):
        normalized["insertions"] = sum(
            int(file_info.get("insertions", 0) or 0)
            for file_info in files
            if isinstance(file_info, dict)
        )
    if not normalized["deletions"] and isinstance(files, list):
        normalized["deletions"] = sum(
            int(file_info.get("deletions", 0) or 0)
            for file_info in files
            if isinstance(file_info, dict)
        )
    return normalized


def _merge_diff_summary(stats: dict) -> str:
    files = int(stats.get("files", 0) or 0)
    insertions = int(stats.get("insertions", 0) or 0)
    deletions = int(stats.get("deletions", 0) or 0)
    parts = [
        f"{files} file" + ("" if files == 1 else "s"),
        f"+{insertions}",
        f"-{deletions}",
        "pre-merge patch",
    ]
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


def _clip_display_text(text: str, *, limit: int) -> str:
    body = " ".join(str(text or "").split())
    if not body or limit <= 0:
        return ""
    if len(body) <= limit:
        return body
    return body[: max(limit - 1, 0)].rstrip() + "…"


def _contains_ci(haystack: str, needle: str) -> bool:
    return bool(haystack and needle and needle.casefold() in haystack.casefold())


def _artifact_diff_preview(metadata: dict) -> str:
    files = metadata.get("files")
    if not isinstance(files, list):
        return ""

    visible = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = _clip_display_text(entry.get("path", ""), limit=72)
        if not path:
            continue
        parts = []
        status = str(entry.get("status", "") or "").strip()
        if status and status != "modified":
            parts.append(status)
        if entry.get("binary"):
            parts.append("binary")
        else:
            insertions = int(entry.get("insertions", 0) or 0)
            deletions = int(entry.get("deletions", 0) or 0)
            if insertions or deletions:
                parts.append(f"+{insertions}/-{deletions}")
        visible.append(path + (f" ({', '.join(parts)})" if parts else ""))
        if len(visible) >= 1:
            break

    if not visible:
        return ""
    remaining = sum(
        1 for entry in files
        if isinstance(entry, dict)
        and str(entry.get("path", "") or "").strip()
    ) - len(visible)
    preview = visible[0]
    if remaining > 0:
        preview += f" +{remaining} more"
    return preview


def _artifact_inline_preview(payload: dict) -> str:
    content = str(
        payload.get("content")
        or ((payload.get("storage") or {}).get("content"))
        or ""
    )
    if not content.strip():
        return ""
    for raw_line in content.splitlines():
        line = " ".join(raw_line.split())
        if not line or line.startswith("<?xml"):
            continue
        return _clip_display_text(line, limit=96)
    return ""


def describe_task_artifact_for_digest(
    artifact: dict,
    *,
    task_id: str = "",
    task_label: str = "",
) -> str:
    payload = serialize_task_artifact(
        artifact,
        task_id=task_id,
        task_label=task_label,
    )
    if not payload:
        return ""

    atype = str(payload.get("type", "") or "artifact").replace("_", " ")
    title = _clip_display_text(
        payload.get("title")
        or payload.get("filename")
        or payload.get("path")
        or atype,
        limit=56,
    )
    ref = _clip_display_text(
        payload.get("url") or payload.get("path") or payload.get("filename"),
        limit=120,
    )
    summary = _clip_display_text(payload.get("summary", ""), limit=88)
    preview = ""
    if payload.get("type") == "diff":
        preview = _artifact_diff_preview(payload.get("metadata") or {})
    if not preview:
        preview = _artifact_inline_preview(payload)

    parts = [f"[{atype}] {title}" if title else f"[{atype}]"]
    if ref and not _contains_ci(title, ref):
        parts.append(ref)
    if summary and not _contains_ci(parts[-1], summary) and not _contains_ci(
        title, summary
    ):
        parts.append(summary)
    if preview and not _contains_ci(summary, preview) and not _contains_ci(
        title, preview
    ):
        parts.append(preview)
    return _clip_display_text(" — ".join(parts), limit=280)


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


def _task_value(task, field: str, default=""):
    if isinstance(task, dict):
        return task.get(field, default)
    return getattr(task, field, default)


def serialize_upstream_task_artifacts(task, tasks_by_id=None) -> list[dict]:
    """Return direct-parent artifacts with explicit handoff metadata."""
    if not tasks_by_id:
        return []
    parent_task_id = str(_task_value(task, "parent_task_id", "") or "").strip()
    if not parent_task_id:
        return []
    parent = tasks_by_id.get(parent_task_id)
    if not parent:
        return []

    parent_id = str(_task_value(parent, "id", "") or "").strip()
    parent_label = str(_task_value(parent, "task", "") or "").strip()
    parent_depth = int(_task_value(parent, "pipeline_depth", 0) or 0)
    payloads = serialize_task_artifacts(
        _task_value(parent, "attachments", []) or [],
        _task_value(parent, "artifacts", []) or [],
        task_id=parent_id,
        task_label=parent_label,
    )
    enriched = []
    for payload in payloads:
        item = dict(payload)
        item["source_task_id"] = parent_id
        item["source_task_label"] = parent_label
        item["source_task_depth"] = parent_depth
        item["source_relation"] = "direct_parent"
        enriched.append(item)
    return enriched


def serialize_task_for_mcp(task, *, tasks_by_id=None) -> dict:
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
    data["upstream_artifacts"] = serialize_upstream_task_artifacts(
        data,
        tasks_by_id=tasks_by_id,
    )
    return data


def store_preserved_merge_diff(
    *,
    task_id: str,
    patch_text: str,
    worktree_branch: str = "",
    base_branch: str = "",
    merge_commit_sha: str = "",
    boundary_task_id: str = "",
    boundary_recorded_at: str = "",
    boundary_task_title: str = "",
    diff_stats: dict | None = None,
    diff_files=None,
    agent_id: str = "",
    agent_name: str = "",
) -> dict:
    patch = "" if patch_text is None else str(patch_text)
    if not patch:
        raise ValueError("patch_text is required")

    stats = _merge_diff_stats(diff_stats, diff_files)
    branch_token = _filename_token(
        worktree_branch or boundary_task_id or "merge-diff",
        default="merge-diff",
    )
    title_subject = (
        f"{worktree_branch} → {base_branch}"
        if worktree_branch and base_branch
        else (boundary_task_title or worktree_branch or "merge")
    )
    artifact = store_task_upload(
        task_id=task_id,
        filename=f"{branch_token}-pre-merge.patch",
        content_text=patch,
        artifact_type="diff",
        title=f"Pre-merge diff — {title_subject}",
        mime_type="text/x-diff",
        summary=_merge_diff_summary(stats),
        prompt_mode="summary",
        provenance={
            "source": "loom",
            "agent_id": agent_id,
            "agent_name": agent_name,
        },
    )

    metadata = dict(artifact.get("metadata") or {})
    metadata["preserved_on_merge"] = True
    metadata["base_branch"] = str(base_branch or "").strip()
    metadata["worktree_branch"] = str(worktree_branch or "").strip()
    metadata["merge_commit_sha"] = str(merge_commit_sha or "").strip()
    metadata["boundary_task_id"] = str(boundary_task_id or "").strip()
    metadata["boundary_recorded_at"] = str(boundary_recorded_at or "").strip()
    metadata["stats"] = stats
    compact_files = []
    for file_info in diff_files or []:
        if not isinstance(file_info, dict):
            continue
        path = str(file_info.get("path", "") or "").strip()
        if not path:
            continue
        compact_files.append({
            "path": path,
            "status": str(file_info.get("status", "modified") or "modified"),
            "insertions": int(file_info.get("insertions", 0) or 0),
            "deletions": int(file_info.get("deletions", 0) or 0),
            "binary": bool(file_info.get("binary")),
        })
    if compact_files:
        metadata["files"] = compact_files
    artifact["metadata"] = metadata
    artifact["summary"] = _merge_diff_summary(stats)
    artifact["prompt"] = {"mode": "summary"}
    return normalize_artifact(artifact)


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


def finalize_task_attachments(attachments, artifacts, *,
                              draft_task_id: str = "",
                              task_id: str = "") -> tuple[list[dict], list[dict]]:
    """Move uploaded draft files under the final task directory and rewrite refs."""
    normalized_attachments = normalize_attachments(attachments)
    normalized_artifacts = normalize_artifacts(artifacts)
    old_id = str(draft_task_id or "").strip()
    new_id = str(task_id or "").strip()
    if not old_id or not new_id or old_id == new_id:
        return normalized_attachments, normalized_artifacts

    old_dir = ATTACHMENTS_DIR / old_id
    new_dir = ATTACHMENTS_DIR / new_id
    if old_dir.exists():
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            for child in old_dir.iterdir():
                target = new_dir / child.name
                if target.exists():
                    target.unlink()
                shutil.move(str(child), str(target))
            shutil.rmtree(old_dir, ignore_errors=True)
        else:
            shutil.move(str(old_dir), str(new_dir))

    def _rewrite_path(path_value: str) -> str:
        path = str(path_value or "").strip()
        if not path:
            return path
        try:
            return str(new_dir / Path(path).name)
        except Exception:
            return path.replace(str(old_dir), str(new_dir))

    attachments_out = []
    for attachment in normalized_attachments:
        item = dict(attachment)
        if item.get("path"):
            item["path"] = _rewrite_path(item.get("path", ""))
        attachments_out.append(item)

    artifacts_out = []
    for artifact in normalized_artifacts:
        item = dict(artifact)
        if item.get("path"):
            item["path"] = _rewrite_path(item.get("path", ""))
        storage = item.get("storage")
        if isinstance(storage, dict) and storage.get("path"):
            storage = dict(storage)
            storage["path"] = _rewrite_path(storage.get("path", ""))
            item["storage"] = storage
        provenance = item.get("provenance")
        if isinstance(provenance, dict):
            provenance = dict(provenance)
            provenance["task_id"] = new_id
            item["provenance"] = provenance
        artifacts_out.append(item)

    return attachments_out, artifacts_out


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
