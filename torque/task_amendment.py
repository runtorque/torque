"""Append-only, attributed amendments to authored board-task descriptions."""

from __future__ import annotations

import base64
import hashlib
import json
import re


TASK_AMENDMENT_MARKER_PREFIX = "<!-- torque-task-amendment:v1:"
_TASK_AMENDMENT_MARKER_RE = re.compile(
    r"<!-- torque-task-amendment:v1:(?P<payload>[A-Za-z0-9_-]+) -->"
)


def task_amendment_text_hash(amendment: str) -> str:
    return hashlib.sha256(str(amendment).encode("utf-8")).hexdigest()


def validate_task_amendment(amendment: str, amendment_id: str) -> str:
    if not str(amendment).strip():
        return "amendment is required"
    if TASK_AMENDMENT_MARKER_PREFIX in str(amendment):
        return "amendment contains a reserved Torque amendment marker"
    amendment_id = str(amendment_id or "")
    if not amendment_id.strip():
        return "amendment_id is required"
    if len(amendment_id) > 200 or any(
            ord(character) < 32 or ord(character) == 127
            for character in amendment_id):
        return (
            "amendment_id must be at most 200 characters and contain no "
            "control characters"
        )
    return ""


def _marker_payload(amendment_id: str, amendment: str) -> str:
    encoded = json.dumps(
        {
            "amendment_id": amendment_id,
            "amendment_sha256": task_amendment_text_hash(amendment),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _code(value: str) -> str:
    return str(value).replace("`", "\\`")


def build_task_amendment_block(
    *,
    amendment: str,
    amendment_id: str,
    actor_id: str,
    prior_task_content_hash: str,
    added_at: str,
) -> str:
    """Return a visibly additive block; callers prepend no mutable content."""
    marker = _marker_payload(amendment_id, amendment)
    return (
        "\n\n---\n"
        f"{TASK_AMENDMENT_MARKER_PREFIX}{marker} -->\n"
        "## Task amendment\n"
        f"- **Amendment ID:** `{_code(amendment_id)}`\n"
        f"- **Added at:** `{_code(added_at)}`\n"
        f"- **Actor:** `{_code(actor_id)}`\n"
        f"- **Prior content hash:** `{_code(prior_task_content_hash)}`\n"
        "\n"
        f"{amendment}"
    )


def find_task_amendment(description: str, amendment_id: str) -> dict | None:
    """Return immutable idempotency metadata embedded in *description*."""
    for match in _TASK_AMENDMENT_MARKER_RE.finditer(str(description or "")):
        encoded = match.group("payload")
        encoded += "=" * (-len(encoded) % 4)
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
                isinstance(payload, dict)
                and payload.get("amendment_id") == amendment_id
        ):
            return payload
    return None


def task_amendment_advisory(task_id: str, task_content_hash: str) -> str:
    """Build the only live-executor advisory shape.

    Deliberately accept no correction/amendment argument so authored text
    cannot accidentally cross into the non-authoritative delivery channel.
    """
    return (
        f"Task {task_id} was amended. New task_content_hash: "
        f"{task_content_hash}. Call torque_context(detail=true)."
    )
