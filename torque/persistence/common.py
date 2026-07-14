"""Shared helpers for domain persistence modules."""

from __future__ import annotations

import copy
import json
import re
import sys


# GroupSettings fields that store structured values as JSON text.
GROUP_SETTINGS_JSON_FIELDS = {
    "env_vars", "agent_env_vars", "terminal_env_vars",
    "board_default_labels", "worktree_symlinks", "worktree_submodules",
    "board_sync_github",
    "architect_review_gate_thresholds",
    "architect_enabled_events",
    "default_engineer_specializations",
    "engineer_hint_snoozes",
}

# GroupSettings fields represented as SQLite INTEGER 0/1 values.
GROUP_SETTINGS_BOOL_FIELDS = {
    "collapsed_default", "filter_by_window", "git_worktree",
    "worktree_auto_checkpoint", "checkpoint_on_progress",
    "worktree_merge_squash", "worktree_merge_preserve_diff",
    "worktree_symlink_gitignored_paths",
    "agent_session_resume",
    "board_sync_enabled",
    "notifications", "notify_on_finish", "notify_on_error",
    "notify_on_attention", "terminal_always_custom_dialog",
    "terminal_close_on_disconnect", "architect_suppress_empty_digests",
    "engineer_behavior_requires_user_approval",
}


def group_settings_field_names() -> set[str] | None:
    """Return the current GroupSettings dataclass field names when available."""
    state_module = sys.modules.get("torque.state")
    group_settings_class = getattr(state_module, "GroupSettings", None)
    if group_settings_class is None:
        try:
            from torque.state import GroupSettings as group_settings_class
        except Exception:
            return None
    return set(
        getattr(group_settings_class, "__dataclass_fields__", {}) or {}
    )


def slugify(value: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "unnamed"


def unique_value(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def normalize_actor_kind(value: str, *, default: str = "user") -> str:
    kind = str(value or "").strip().lower() or default
    if kind not in {"user", "architect", "engineer", "system"}:
        raise ValueError(
            "actor kind must be one of: user, architect, engineer, system"
        )
    return kind


def snapshot_db_payload(value):
    """Detach a payload before it enters an asynchronous write queue."""
    if hasattr(value, "__dataclass_fields__"):
        return copy.deepcopy(value)
    if isinstance(value, (dict, list, tuple, set)):
        return copy.deepcopy(value)
    return value


def json_payload(value, default):
    """Normalize a value to a detached JSON-compatible container."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return copy.deepcopy(default)
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return copy.deepcopy(default)
    if isinstance(value, type(default)):
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            decoded = json.loads(encoded)
            if isinstance(decoded, type(default)):
                return decoded
        except (TypeError, ValueError):
            return copy.deepcopy(default)
    return copy.deepcopy(default)


def json_payload_text(value, default) -> str:
    """Serialize :func:`json_payload` with a stable compact representation."""
    return json.dumps(
        json_payload(value, default),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def json_loads_default(value, default):
    """Decode a JSON container, returning a detached default on mismatch."""
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    try:
        parsed = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError):
        return copy.deepcopy(default)
    if isinstance(default, list) and not isinstance(parsed, list):
        return copy.deepcopy(default)
    if isinstance(default, dict) and not isinstance(parsed, dict):
        return copy.deepcopy(default)
    return parsed
