"""Durable, versioned identity for a board task's authored execution contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


TASK_CONTENT_HASH_DOMAIN = "torque.board-task-content"
TASK_CONTENT_HASH_VERSION = 1
TASK_CONTENT_HASH_PREFIX = "task-content-v1:sha256:"

# This manifest is intentionally data, rather than an informal comment.  A
# pinned content hash does not cover labels because labels are a mixed
# authored/lifecycle channel.  Consequently, execution-critical instructions
# must never live only in a label.
TASK_CONTENT_HASH_MANIFEST_V1 = {
    "domain": TASK_CONTENT_HASH_DOMAIN,
    "version": TASK_CONTENT_HASH_VERSION,
    "included_fields": (
        "task",
        "description",
        "action_name",
        "action_vars",
        "agent_template",
        "suggested_action",
        "required_review_gates",
        "depends_on",
        "deliverable_required",
        "deliverable_type",
        "deliverable_format",
        "deliverable_artifact_title",
        "requires_review",
        "finalization_mode",
        "instructions",
        "context",
        "criteria",
    ),
    "normalization": {
        "task": "encoded as title; exact text preserved",
        "depends_on": "order-insensitive set",
        "other_fields": "exact values; canonical JSON object-key ordering only",
    },
    "excluded_field_policies": {
        "labels": (
            "Mixed authored/lifecycle channel; not covered by a pinned hash. "
            "Nothing execution-critical may live only in a label."
        ),
    },
}

TASK_CONTENT_HASH_INCLUDED_FIELDS = frozenset(
    TASK_CONTENT_HASH_MANIFEST_V1["included_fields"]
)

# Every BoardTask field not in the included set must be named here.  Tests
# compare these sets with the dataclass so a new field cannot silently escape
# a manifest ruling.
TASK_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({
    "id",
    "slug",
    "group",
    "lane",
    "position",
    "agent_id",
    "assigned_engineer_id",
    "assigned_architect_id",
    "created_by_architect_id",
    "created_by_engineer_id",
    "suggested_specialization",
    "reply_agent_id",
    "labels",
    "created_at",
    "updated_at",
    "lane_entered_at",
    "provider",
    "external_id",
    "external_url",
    "board_sync",
    "parent_task_id",
    "pipeline_depth",
    "pipeline_root_id",
    "status",
    "scheduled_at",
    "dispatch_state",
    "messages",
    "messages_thread",
    "attachments",
    "health_state",
    "health_since",
    "health_details",
    "artifacts",
    "verification_mode",
    "verification_state",
    "verification_notes",
    "verification_updated_at",
    "verification_updated_by",
    "verification_summary",
    "completion_evidence",
    "worktree_boundary",
    "resume_after_boundary_task_id",
    "archived_at",
    "archived_from_lane",
    "pre_approved_by",
    "finalization_boundary",
    "finalization_audit",
    "finalization_status",
    "task_content_hash",
})


def _value(task, field, default):
    if isinstance(task, Mapping):
        return task.get(field, default)
    return getattr(task, field, default)


def task_content_payload(task) -> dict:
    """Return the canonical v1 payload without mutating the task."""

    depends_on = _value(task, "depends_on", [])
    if not isinstance(depends_on, (list, tuple, set, frozenset)):
        depends_on = []
    canonical_depends_on = sorted({
        str(item) for item in depends_on
    })
    return {
        "domain": TASK_CONTENT_HASH_DOMAIN,
        "manifest_version": TASK_CONTENT_HASH_VERSION,
        "content": {
            "title": _value(task, "task", ""),
            "description": _value(task, "description", ""),
            "action_name": _value(task, "action_name", ""),
            "action_vars": _value(task, "action_vars", {}),
            "agent_template": _value(task, "agent_template", ""),
            "suggested_action": _value(task, "suggested_action", ""),
            "required_review_gates": _value(
                task, "required_review_gates", []
            ),
            "depends_on": canonical_depends_on,
            "deliverable_required": bool(
                _value(task, "deliverable_required", False)
            ),
            "deliverable_type": _value(task, "deliverable_type", ""),
            "deliverable_format": _value(task, "deliverable_format", ""),
            "deliverable_artifact_title": _value(
                task, "deliverable_artifact_title", ""
            ),
            "requires_review": bool(_value(task, "requires_review", False)),
            "finalization_mode": _value(task, "finalization_mode", "legacy"),
            "instructions": _value(task, "instructions", ""),
            "context": _value(task, "context", ""),
            "criteria": _value(task, "criteria", ""),
        },
    }


def compute_task_content_hash(task) -> str:
    """Compute the synchronous v1 SHA-256 identity for authored task content."""

    encoded = json.dumps(
        task_content_payload(task),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return TASK_CONTENT_HASH_PREFIX + hashlib.sha256(encoded).hexdigest()
