"""Helpers for structured task artifacts and prompt rendering."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

ARTIFACT_IMAGE = "image"
ARTIFACT_FILE_REF = "file_ref"
ARTIFACT_SNIPPET = "snippet"
ARTIFACT_LOG = "log"
ARTIFACT_DIFF = "diff"
ARTIFACT_TEST_REPORT = "test_report"
ARTIFACT_GENERATED_DOC = "generated_doc"

KNOWN_ARTIFACT_TYPES = {
    ARTIFACT_IMAGE,
    ARTIFACT_FILE_REF,
    ARTIFACT_SNIPPET,
    ARTIFACT_LOG,
    ARTIFACT_DIFF,
    ARTIFACT_TEST_REPORT,
    ARTIFACT_GENERATED_DOC,
}

PROMPT_MODE_AUTO = "auto"
PROMPT_MODE_NONE = "none"
PROMPT_MODE_PATH = "path"
PROMPT_MODE_SUMMARY = "summary"
PROMPT_MODE_INLINE = "inline"

KNOWN_PROMPT_MODES = {
    PROMPT_MODE_AUTO,
    PROMPT_MODE_NONE,
    PROMPT_MODE_PATH,
    PROMPT_MODE_SUMMARY,
    PROMPT_MODE_INLINE,
}

STORAGE_PATH = "path"
STORAGE_INLINE = "inline"
STORAGE_FILE_REF = "file_ref"

_INLINE_LIMITS = {
    ARTIFACT_SNIPPET: 1200,
    ARTIFACT_GENERATED_DOC: 1600,
    ARTIFACT_LOG: 1000,
    ARTIFACT_DIFF: 1200,
    ARTIFACT_TEST_REPORT: 1200,
}

_TYPE_LABELS = {
    ARTIFACT_IMAGE: "image",
    ARTIFACT_FILE_REF: "file reference",
    ARTIFACT_SNIPPET: "snippet",
    ARTIFACT_LOG: "log",
    ARTIFACT_DIFF: "diff",
    ARTIFACT_TEST_REPORT: "test report",
    ARTIFACT_GENERATED_DOC: "generated doc",
}

_DEFAULT_PROMPT_MODES = {
    ARTIFACT_IMAGE: PROMPT_MODE_PATH,
    ARTIFACT_FILE_REF: PROMPT_MODE_PATH,
    ARTIFACT_SNIPPET: PROMPT_MODE_INLINE,
    ARTIFACT_LOG: PROMPT_MODE_SUMMARY,
    ARTIFACT_DIFF: PROMPT_MODE_SUMMARY,
    ARTIFACT_TEST_REPORT: PROMPT_MODE_SUMMARY,
    ARTIFACT_GENERATED_DOC: PROMPT_MODE_SUMMARY,
}


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_dict(value) -> dict:
    return deepcopy(value) if isinstance(value, dict) else {}


def _safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _artifact_type(raw: dict) -> str:
    atype = _safe_text(raw.get("type")).lower()
    return atype or ARTIFACT_FILE_REF


def _prompt_mode_for_type(atype: str, requested: str) -> str:
    req = _safe_text(requested).lower()
    if req in KNOWN_PROMPT_MODES:
        return req
    return _DEFAULT_PROMPT_MODES.get(atype, PROMPT_MODE_SUMMARY)


def _guess_storage_kind(atype: str, raw: dict) -> str:
    storage = raw.get("storage")
    if isinstance(storage, dict):
        kind = _safe_text(storage.get("kind")).lower()
        if kind in {STORAGE_PATH, STORAGE_INLINE, STORAGE_FILE_REF}:
            return kind
    if atype == ARTIFACT_FILE_REF:
        return STORAGE_FILE_REF
    if _safe_text(raw.get("content")) and not _safe_text(raw.get("path")):
        return STORAGE_INLINE
    return STORAGE_PATH if _safe_text(raw.get("path")) else STORAGE_INLINE


def _title_for_artifact(atype: str, path: str, raw: dict) -> str:
    title = _safe_text(raw.get("title"))
    if title:
        return title
    if path:
        return Path(path).name
    return _TYPE_LABELS.get(atype, atype.replace("_", " "))


def _filename_for(path: str, raw: dict) -> str:
    filename = _safe_text(raw.get("filename"))
    if filename:
        return filename
    return Path(path).name if path else ""


def normalize_attachment(attachment: dict) -> dict:
    """Normalize a legacy image attachment record."""
    if not isinstance(attachment, dict):
        return {}
    path = _safe_text(attachment.get("path"))
    filename = _filename_for(path, attachment)
    mime_type = _safe_text(attachment.get("mime_type")) or "image/png"
    return {
        "path": path,
        "filename": filename,
        "mime_type": mime_type,
    }


def attachment_to_artifact(attachment: dict, index: int = 0) -> dict:
    """Project a legacy image attachment into the artifact model."""
    att = normalize_attachment(attachment)
    if not att:
        return {}
    title = _title_for_artifact(ARTIFACT_IMAGE, att["path"], att)
    return {
        "id": _safe_text(att.get("id")) or f"attachment-image-{index + 1}",
        "type": ARTIFACT_IMAGE,
        "title": title,
        "filename": att["filename"],
        "path": att["path"],
        "mime_type": att["mime_type"],
        "summary": "",
        "content": "",
        "line_start": None,
        "line_end": None,
        "metadata": {"legacy_attachment": True},
        "prompt": {"mode": PROMPT_MODE_PATH},
        "provenance": {
            "source": "user",
            "created_at": "",
            "agent_id": "",
            "agent_name": "",
            "task_id": "",
        },
        "storage": {
            "kind": STORAGE_PATH,
            "path": att["path"],
            "content": "",
            "line_start": None,
            "line_end": None,
        },
        "lifecycle": {
            "owner": "task",
            "cleanup": "delete_with_task",
        },
    }


def normalize_artifact(artifact: dict, index: int = 0) -> dict:
    """Normalize a structured artifact record for task persistence."""
    if not isinstance(artifact, dict):
        return {}
    atype = _artifact_type(artifact)
    path = _safe_text(artifact.get("path"))
    content = artifact.get("content")
    if content is None and isinstance(artifact.get("storage"), dict):
        content = artifact["storage"].get("content")
    content = "" if content is None else str(content)
    line_start = _safe_int(artifact.get("line_start"))
    line_end = _safe_int(artifact.get("line_end"))
    storage_kind = _guess_storage_kind(atype, artifact)
    title = _title_for_artifact(atype, path, artifact)
    filename = _filename_for(path, artifact)
    metadata = _safe_dict(artifact.get("metadata"))
    prompt = _safe_dict(artifact.get("prompt"))
    provenance = _safe_dict(artifact.get("provenance"))
    lifecycle = _safe_dict(artifact.get("lifecycle"))
    prompt_mode = _prompt_mode_for_type(atype, prompt.get("mode", ""))
    if storage_kind == STORAGE_FILE_REF and path:
        metadata.setdefault("reference", "file")
    if line_start is not None:
        metadata.setdefault("line_start", line_start)
    if line_end is not None:
        metadata.setdefault("line_end", line_end)
    owner = _safe_text(lifecycle.get("owner"))
    cleanup = _safe_text(lifecycle.get("cleanup"))
    if not owner:
        owner = "external" if storage_kind == STORAGE_FILE_REF else "task"
    if not cleanup:
        cleanup = "keep" if owner == "external" else "delete_with_task"
    return {
        "id": _safe_text(artifact.get("id")) or f"artifact-{index + 1}",
        "type": atype,
        "title": title,
        "filename": filename,
        "path": path,
        "mime_type": _safe_text(artifact.get("mime_type")),
        "summary": _safe_text(artifact.get("summary")),
        "content": content,
        "line_start": line_start,
        "line_end": line_end,
        "metadata": metadata,
        "prompt": {"mode": prompt_mode},
        "provenance": {
            "source": _safe_text(provenance.get("source")) or "user",
            "created_at": _safe_text(provenance.get("created_at")),
            "agent_id": _safe_text(provenance.get("agent_id")),
            "agent_name": _safe_text(provenance.get("agent_name")),
            "task_id": _safe_text(provenance.get("task_id")),
        },
        "storage": {
            "kind": storage_kind,
            "path": path,
            "content": content,
            "line_start": line_start,
            "line_end": line_end,
        },
        "lifecycle": {
            "owner": owner,
            "cleanup": cleanup,
        },
    }


def normalize_attachments(attachments) -> list[dict]:
    if not isinstance(attachments, list):
        return []
    out = []
    for attachment in attachments:
        normalized = normalize_attachment(attachment)
        if normalized:
            out.append(normalized)
    return out


def normalize_artifacts(artifacts) -> list[dict]:
    if not isinstance(artifacts, list):
        return []
    out = []
    for index, artifact in enumerate(artifacts):
        normalized = normalize_artifact(artifact, index=index)
        if normalized:
            out.append(normalized)
    return out


def task_artifacts(attachments, artifacts) -> list[dict]:
    """Return the combined artifact view for prompts and torque context."""
    combined = []
    for index, attachment in enumerate(normalize_attachments(attachments)):
        synthetic = attachment_to_artifact(attachment, index=index)
        if synthetic:
            combined.append(synthetic)
    for index, artifact in enumerate(normalize_artifacts(artifacts)):
        combined.append(artifact)
    return combined


def render_line_reference(path: str, line_start=None, line_end=None) -> str:
    if not path:
        return ""
    if line_start is None:
        return path
    if line_end is not None and line_end != line_start:
        return f"{path}:{line_start}-{line_end}"
    return f"{path}:{line_start}"


def _clip_text(text: str, limit: int) -> str:
    body = str(text or "").strip()
    if not body:
        return ""
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 3)].rstrip() + "..."


def _markdown_fenced_block(text: str, language: str = "text") -> str:
    body = str(text or "")
    longest = 0
    current = 0
    for ch in body:
        if ch == "`":
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{body}\n{fence}"


def _artifact_reference(artifact: dict) -> str:
    storage = artifact.get("storage") or {}
    path = storage.get("path") or artifact.get("path") or ""
    return render_line_reference(path,
                                 storage.get("line_start")
                                 or artifact.get("line_start"),
                                 storage.get("line_end")
                                 or artifact.get("line_end"))


def _auto_mode_as_effective(artifact: dict) -> str:
    mode = ((artifact.get("prompt") or {}).get("mode")
            or _DEFAULT_PROMPT_MODES.get(artifact.get("type"), PROMPT_MODE_SUMMARY))
    if mode != PROMPT_MODE_AUTO:
        return mode
    atype = artifact.get("type")
    content = artifact.get("content") or ""
    if atype in {ARTIFACT_SNIPPET, ARTIFACT_GENERATED_DOC}:
        return PROMPT_MODE_INLINE if content and len(content) <= _INLINE_LIMITS.get(
            atype, 1200) else PROMPT_MODE_SUMMARY
    return _DEFAULT_PROMPT_MODES.get(atype, PROMPT_MODE_SUMMARY)


def artifact_prompt_blocks(artifacts) -> list[str]:
    """Render explicit, type-aware prompt references for non-image artifacts."""
    blocks = []
    for artifact in normalize_artifacts(artifacts):
        effective_mode = _auto_mode_as_effective(artifact)
        if effective_mode == PROMPT_MODE_NONE:
            continue
        title = artifact.get("title") or _TYPE_LABELS.get(
            artifact.get("type"), artifact.get("type", "artifact"))
        atype = artifact.get("type") or "artifact"
        ref = _artifact_reference(artifact)
        summary = artifact.get("summary") or ""
        metadata = artifact.get("metadata") or {}
        content = artifact.get("content") or ""
        line = f"- [{atype}] {title}"
        extras = []
        if ref:
            extras.append(f"ref: `{ref}`")
        if atype == ARTIFACT_TEST_REPORT:
            total = metadata.get("total")
            passed = metadata.get("passed")
            failed = metadata.get("failed")
            if total not in (None, ""):
                extras.append(f"results: {passed or 0} passed, {failed or 0} failed, {total} total")
        elif atype == ARTIFACT_DIFF:
            files = metadata.get("files")
            ins = metadata.get("insertions")
            dels = metadata.get("deletions")
            if files not in (None, ""):
                extras.append(f"stats: {files} files, +{ins or 0}/-{dels or 0}")
        if effective_mode == PROMPT_MODE_PATH:
            if extras:
                line += " (" + "; ".join(extras) + ")"
            blocks.append(line)
            continue
        if summary:
            extras.append(f"summary: {_clip_text(summary, 240)}")
        if effective_mode == PROMPT_MODE_INLINE and content:
            limit = _INLINE_LIMITS.get(atype, 1200)
            extras.append("inline excerpt follows")
        if extras:
            line += " (" + "; ".join(extras) + ")"
        if effective_mode == PROMPT_MODE_INLINE and content:
            line += "\n" + _markdown_fenced_block(
                _clip_text(content, _INLINE_LIMITS.get(atype, 1200))
            )
        blocks.append(line)
    return blocks


def upstream_artifact_prompt_block(artifacts) -> str:
    """Render a bounded handoff section for upstream task artifacts."""
    if not isinstance(artifacts, list):
        return ""

    blocks = []
    for raw in artifacts:
        if not isinstance(raw, dict):
            continue
        artifact = normalize_artifact(raw)
        if not artifact:
            continue

        source_task_label = _safe_text(raw.get("source_task_label"))
        source_task_id = _safe_text(raw.get("source_task_id"))
        source_label = source_task_label or source_task_id or "upstream task"
        source_suffix = f" ({source_task_id})" if source_task_id else ""
        title = artifact.get("title") or _TYPE_LABELS.get(
            artifact.get("type"), artifact.get("type", "artifact")
        )
        atype = artifact.get("type") or "artifact"
        ref = _artifact_reference(artifact)
        summary = artifact.get("summary") or ""
        metadata = artifact.get("metadata") or {}
        content = artifact.get("content") or ""
        effective_mode = _auto_mode_as_effective(artifact)
        if effective_mode == PROMPT_MODE_NONE:
            continue

        line = (
            f"- From `{source_label}`{source_suffix}: "
            f"[{atype}] {title}"
        )
        extras = []
        if ref:
            extras.append(f"ref: `{ref}`")
        if atype == ARTIFACT_TEST_REPORT:
            total = metadata.get("total")
            passed = metadata.get("passed")
            failed = metadata.get("failed")
            if total not in (None, ""):
                extras.append(
                    f"results: {passed or 0} passed, {failed or 0} failed, {total} total"
                )
        elif atype == ARTIFACT_DIFF:
            files = metadata.get("files")
            ins = metadata.get("insertions")
            dels = metadata.get("deletions")
            if files not in (None, ""):
                extras.append(f"stats: {files} files, +{ins or 0}/-{dels or 0}")
        if effective_mode == PROMPT_MODE_PATH:
            if extras:
                line += " (" + "; ".join(extras) + ")"
            blocks.append(line)
            continue
        if summary:
            extras.append(f"summary: {_clip_text(summary, 240)}")
        if effective_mode == PROMPT_MODE_INLINE and content:
            extras.append("inline excerpt follows")
        if extras:
            line += " (" + "; ".join(extras) + ")"
        if effective_mode == PROMPT_MODE_INLINE and content:
            line += "\n" + _markdown_fenced_block(
                _clip_text(content, _INLINE_LIMITS.get(atype, 1200))
            )
        blocks.append(line)

    if not blocks:
        return ""
    return "\n\n## Upstream handoff artifacts\n" + "\n".join(blocks)


def legacy_image_prompt_block(attachments, artifacts) -> str:
    """Preserve the existing image attachment prompt section."""
    lines = []
    for attachment in normalize_attachments(attachments):
        if attachment.get("path"):
            lines.append("- `" + attachment["path"] + "`")
    for artifact in normalize_artifacts(artifacts):
        if artifact.get("type") == ARTIFACT_IMAGE and artifact.get("path"):
            lines.append("- `" + artifact["path"] + "`")
    if not lines:
        return ""
    return "\n\n## Attached images\n" + "\n".join(lines)


def artifact_prompt_block(artifacts) -> str:
    blocks = artifact_prompt_blocks(artifacts)
    if not blocks:
        return ""
    return "\n\n## Task artifacts\n" + "\n".join(blocks)
