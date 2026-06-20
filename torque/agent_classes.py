"""Agent Class registry, validation, and launch preview helpers.

Agent Classes are user-facing structured templates over Torque's existing
runtime agent kinds. They are intentionally narrow: an Agent Class only names a
base runtime kind and a referenced Agent Profile. Runtime capability projection
continues to come from the frozen effective Agent Profile snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
import time
from typing import Any

import yaml

from .agent_profiles import (
    BASE_KINDS,
    AgentProfileDefinition,
    ValidationIssue,
    enriched_profile_preview,
    load_agent_profiles,
    profile_definition_by_id,
)

BUILTIN_CLASS_DIR = Path(__file__).resolve().parent / "builtin_agent_classes"
PROJECT_CLASS_LEAF = "agent_classes"

CLASS_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CLASS_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_LIFECYCLES = {"stable", "draft"}
SAFE_UI_METADATA_KEYS = {"label", "icon", "badge", "color"}
AUTHORING_DISPLAY_ALIASES = {"title", "display_title"}
AUTHORING_PROMPT_ALIASES = {"instructions", "class_instructions", "class_prompt"}
CUSTOM_CLASS_ARCHIVED_KEY = "archived"
MAX_DISPLAY_NAME_LEN = 120
MAX_DESCRIPTION_LEN = 2000
MAX_PROMPT_LEN = 30000
MAX_METADATA_JSON_BYTES = 65536

DEFAULT_CLASS_BY_KIND = {
    "architect": "default-architect",
    "engineer": "default-engineer",
    "worker": "default-worker",
}

BUILTIN_CLASS_BASE_KIND = {
    "default-architect": "architect",
    "default-engineer": "engineer",
    "default-worker": "worker",
    "product-manager": "architect",
}

BUILTIN_CLASS_PROFILE_REF = {
    "default-architect": ("full-architect", "1"),
    "default-engineer": ("full-engineer", "1"),
    "default-worker": ("full-worker", "1"),
    "product-manager": ("product-manager-draft", "2"),
}

KNOWN_CLASS_KEYS = {
    "id",
    "version",
    "base_kind",
    "display_name",
    "description",
    "lifecycle",
    "agent_profile_ref",
    "prompt",
    "metadata",
    "draft",
}

# These names either collide with AgentCell.profile terminology or would create
# class-local raw capability/tool semantics. Wave 6B deliberately forbids them.
AMBIGUOUS_CLASS_PROFILE_KEYS = {
    "profile",
    "profile_id",
    "agent_profile",
    "agent_profile_id",
    "agent_profile_version",
    "runtime_profile",
    "agent_cell_profile",
}

RAW_TOOL_OR_CAPABILITY_FIELDS = {
    "tools",
    "tool",
    "tool_categories",
    "allowed_tools",
    "denied_tools",
    "mcp",
    "mcp_tools",
    "mcp_tool_picker",
    "tool_picker",
    "capabilities",
    "capability",
    "capability_deltas",
    "capability_grants",
    "capability_denies",
    "grants",
    "denies",
    "generators",
    "generator",
}

EXTERNAL_CONNECTOR_CAVEAT = (
    "External connector exposure is not governed or enforced by Agent Classes "
    "in Wave 6B; manage connector access separately."
)
EXTERNAL_CONNECTOR_DRAFT_WARNING = (
    "External connector exposure is not enforced by Agent Classes in Wave 6B; "
    "do not treat draft/restricted classes as live-safe for external connectors."
)
PM_DRAFT_WARNING = (
    "Product Manager is draft/scratch-only in Wave 6B; do not use it for live "
    "PM dogfood, Blueprint replacement, or production product authority without "
    "explicit future approval."
)


@dataclass(frozen=True)
class AgentClassProfileRef:
    id: str
    version: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version}


@dataclass
class AgentClassDefinition:
    id: str
    version: str
    base_kind: str
    agent_profile_ref: AgentClassProfileRef
    display_name: str = ""
    description: str = ""
    lifecycle: str = "stable"
    prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    draft: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    builtin: bool = False

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        source: str = "",
        builtin: bool = False,
    ) -> "AgentClassDefinition":
        ref_data = data.get("agent_profile_ref")
        if isinstance(ref_data, dict):
            ref = AgentClassProfileRef(
                id=str(ref_data.get("id", "") or "").strip(),
                version=str(ref_data.get("version", "") or "").strip(),
            )
        else:
            ref = AgentClassProfileRef(id="", version="")
        return cls(
            id=str(data.get("id", "") or "").strip(),
            version=str(data.get("version", "") or "").strip(),
            base_kind=str(data.get("base_kind", "") or "").strip(),
            display_name=str(data.get("display_name", "") or "").strip(),
            description=str(data.get("description", "") or "").strip(),
            lifecycle=str(data.get("lifecycle", "stable") or "stable").strip(),
            agent_profile_ref=ref,
            prompt=str(data.get("prompt", "") or "").strip(),
            metadata=(dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else {}),
            draft=(dict(data.get("draft") or {}) if isinstance(data.get("draft"), dict) else {}),
            source=source,
            builtin=builtin,
        )

    def as_preview_dict(self) -> dict[str, Any]:
        source_kind = "builtin" if self.builtin else "project"
        archived = agent_class_is_archived(self)
        return {
            "id": self.id,
            "version": self.version,
            "base_kind": self.base_kind,
            "display_name": self.display_name,
            "description": self.description,
            "lifecycle": self.lifecycle,
            "agent_profile_ref": self.agent_profile_ref.as_dict(),
            "builtin": self.builtin,
            "custom": not self.builtin,
            "source": source_kind,
            "source_path": self.source,
            "archived": archived,
            "disabled": archived,
            "scratch_only": bool((self.draft or {}).get("scratch_only") is True),
        }


def agent_class_is_archived(definition_or_preview: AgentClassDefinition | dict[str, Any]) -> bool:
    """Return whether a class has been disabled/archived in project metadata."""

    if isinstance(definition_or_preview, AgentClassDefinition):
        metadata = definition_or_preview.metadata
    else:
        metadata = definition_or_preview.get("metadata", {})
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get(CUSTOM_CLASS_ARCHIVED_KEY)
        or metadata.get("disabled")
        or metadata.get("archived_at")
    )


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    path: str = "",
    profile_id: str = "",
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        path=path,
        profile_id=profile_id,
    )


def _find_project_dir(base_dir: str = "") -> Path | None:
    d = Path(os.path.expanduser(base_dir) if base_dir else os.getcwd())
    if not d.is_dir():
        d = Path(os.getcwd())
    for _ in range(20):
        candidate = d / ".torque" / PROJECT_CLASS_LEAF
        if candidate.is_dir():
            return candidate
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


def _find_project_root_for_authoring(base_dir: str = "") -> Path:
    """Resolve the project root used for trusted YAML authoring.

    Readers only discover an existing ``.torque/agent_classes`` directory so
    they do not create files as a side effect.  Authoring is an explicit trusted
    operator action, so it may create the project config directory.  Prefer the
    nearest existing class dir, then the nearest repo/project marker, and finally
    the supplied directory itself.
    """

    existing = _find_project_dir(base_dir)
    if existing:
        return existing.parent.parent
    d = Path(os.path.expanduser(base_dir) if base_dir else os.getcwd())
    if not d.is_dir():
        d = d.parent if d.parent != d else Path(os.getcwd())
    d = d.resolve()
    fallback = d
    for _ in range(20):
        if (d / ".git").exists() or (d / ".torque").exists():
            return d
        parent = d.parent
        if parent == d:
            break
        d = parent
    return fallback


def project_agent_class_dir(base_dir: str = "", *, create: bool = False) -> Path:
    root = _find_project_root_for_authoring(base_dir)
    path = root / ".torque" / PROJECT_CLASS_LEAF
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def find_agent_class_dirs(base_dir: str = "", *, include_builtin: bool = True) -> list[tuple[Path, bool]]:
    dirs: list[tuple[Path, bool]] = []
    project_dir = _find_project_dir(base_dir)
    if project_dir:
        dirs.append((project_dir, False))
    if include_builtin and BUILTIN_CLASS_DIR.is_dir():
        dirs.append((BUILTIN_CLASS_DIR, True))
    return dirs


def _iter_yaml_paths(root_dir: Path) -> list[Path]:
    if not root_dir.is_dir():
        return []
    return sorted(
        path for path in root_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )


def load_class_yaml(path: Path) -> tuple[dict[str, Any] | None, ValidationIssue | None]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, ValidationIssue(
            "error",
            "malformed_yaml",
            f"class YAML is malformed: {exc}",
            path=str(path),
        )
    if not isinstance(data, dict):
        return None, ValidationIssue(
            "error",
            "class_not_mapping",
            "Agent Class YAML must be a mapping",
            path=str(path),
        )
    return data, None


def _nested_forbidden_key_paths(raw: Any, forbidden: set[str], *,
                                prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_text = str(key)
            child = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in forbidden:
                paths.append(child)
            paths.extend(_nested_forbidden_key_paths(value, forbidden, prefix=child))
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            child = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_nested_forbidden_key_paths(value, forbidden, prefix=child))
    return paths


def _metadata_json_size(metadata: dict[str, Any]) -> int:
    try:
        return len(json.dumps(metadata, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return MAX_METADATA_JSON_BYTES + 1


def _profile_by_id_for_validation(base_dir: str = "") -> dict[str, AgentProfileDefinition]:
    profiles, issues = load_agent_profiles(base_dir=base_dir)
    if any(issue.severity == "error" for issue in issues):
        # Missing/invalid profiles are reported as class reference failures below
        # instead of hiding class YAML validation entirely.
        return {profile.id: profile for profile in profiles}
    return {profile.id: profile for profile in profiles}


def validate_class_data(
    data: dict[str, Any],
    *,
    source: str = "",
    builtin: bool = False,
    base_dir: str = "",
    profiles_by_id: dict[str, AgentProfileDefinition] | None = None,
) -> tuple[AgentClassDefinition | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    class_id = str(data.get("id", "") or "").strip()

    unknown_keys = sorted(set(data) - KNOWN_CLASS_KEYS)
    ambiguous = sorted(set(data) & AMBIGUOUS_CLASS_PROFILE_KEYS)
    nested_ambiguous = sorted(set(_nested_forbidden_key_paths(data, AMBIGUOUS_CLASS_PROFILE_KEYS)))
    nested_raw_tool_fields = sorted(set(_nested_forbidden_key_paths(data, RAW_TOOL_OR_CAPABILITY_FIELDS)))
    raw_tool_fields = sorted(set(data) & RAW_TOOL_OR_CAPABILITY_FIELDS)
    if ambiguous:
        issues.append(ValidationIssue(
            "error",
            "agent_cell_profile_confusion",
            "Agent Class definitions must use agent_profile_ref, not legacy AgentCell.profile/runtime profile fields: "
            + ", ".join(ambiguous),
            path=source,
            profile_id=class_id,
        ))
    extra_ambiguous = [path for path in nested_ambiguous if path not in ambiguous]
    if extra_ambiguous:
        issues.append(ValidationIssue(
            "error",
            "agent_cell_profile_confusion",
            "Agent Class definitions must not contain AgentCell/profile-like fields: "
            + ", ".join(extra_ambiguous),
            path=source,
            profile_id=class_id,
        ))
    if raw_tool_fields:
        issues.append(ValidationIssue(
            "error",
            "raw_tool_fields_forbidden",
            "Agent Class definitions must not contain raw MCP/tool/capability fields: "
            + ", ".join(raw_tool_fields),
            path=source,
            profile_id=class_id,
        ))
    extra_raw_tool_fields = [path for path in nested_raw_tool_fields if path not in raw_tool_fields]
    if extra_raw_tool_fields:
        issues.append(ValidationIssue(
            "error",
            "raw_tool_fields_forbidden",
            "Agent Class definitions must not contain nested raw MCP/tool/capability fields: "
            + ", ".join(extra_raw_tool_fields),
            path=source,
            profile_id=class_id,
        ))
    non_confusing_unknown = [
        key for key in unknown_keys
        if key not in AMBIGUOUS_CLASS_PROFILE_KEYS and key not in RAW_TOOL_OR_CAPABILITY_FIELDS
    ]
    if non_confusing_unknown:
        issues.append(ValidationIssue(
            "error",
            "unknown_class_fields",
            "unknown Agent Class fields: " + ", ".join(non_confusing_unknown),
            path=source,
            profile_id=class_id,
        ))

    if not class_id:
        issues.append(ValidationIssue("error", "missing_class_id", "Agent Class id is required", path=source))
    elif not CLASS_ID_RE.match(class_id):
        issues.append(ValidationIssue(
            "error",
            "invalid_class_id",
            "Agent Class id must be lowercase kebab-case alphanumerics",
            path=source,
            profile_id=class_id,
        ))
    version = str(data.get("version", "") or "").strip()
    if not version:
        issues.append(ValidationIssue(
            "error", "missing_class_version", "Agent Class version is required", path=source, profile_id=class_id
        ))
    elif not CLASS_VERSION_RE.match(version):
        issues.append(ValidationIssue(
            "error",
            "invalid_class_version",
            "Agent Class version must be a safe non-empty token",
            path=source,
            profile_id=class_id,
        ))
    display_name = data.get("display_name", "")
    if not isinstance(display_name, str):
        issues.append(ValidationIssue(
            "error",
            "display_name_not_string",
            "display_name must be a string",
            path=source,
            profile_id=class_id,
        ))
    else:
        display_name_text = display_name.strip()
        if not display_name_text:
            issues.append(ValidationIssue(
                "error",
                "missing_display_name",
                "Agent Class display_name is required",
                path=source,
                profile_id=class_id,
            ))
        elif len(display_name_text) > MAX_DISPLAY_NAME_LEN or "\n" in display_name_text or "\r" in display_name_text:
            issues.append(ValidationIssue(
                "error",
                "invalid_display_name",
                f"display_name must be one line and at most {MAX_DISPLAY_NAME_LEN} characters",
                path=source,
                profile_id=class_id,
            ))
    if "description" in data:
        description = data.get("description", "")
        if not isinstance(description, str):
            issues.append(ValidationIssue(
                "error", "description_not_string", "description must be a string", path=source, profile_id=class_id
            ))
        elif len(description) > MAX_DESCRIPTION_LEN:
            issues.append(ValidationIssue(
                "error",
                "description_too_long",
                f"description must be at most {MAX_DESCRIPTION_LEN} characters",
                path=source,
                profile_id=class_id,
            ))
    base_kind = str(data.get("base_kind", "") or "").strip()
    if base_kind not in BASE_KINDS:
        issues.append(ValidationIssue(
            "error",
            "invalid_base_kind",
            f"base_kind must be one of {', '.join(sorted(BASE_KINDS))}",
            path=source,
            profile_id=class_id,
        ))
    lifecycle = str(data.get("lifecycle", "stable") or "stable").strip()
    if lifecycle not in ALLOWED_LIFECYCLES:
        issues.append(ValidationIssue(
            "error",
            "invalid_lifecycle",
            "Agent Class lifecycle must be stable or draft",
            path=source,
            profile_id=class_id,
        ))
    if "metadata" in data and not isinstance(data.get("metadata"), dict):
        issues.append(ValidationIssue(
            "error", "metadata_not_mapping", "metadata must be a mapping", path=source, profile_id=class_id
        ))
    elif isinstance(data.get("metadata"), dict):
        metadata = data.get("metadata") or {}
        if _metadata_json_size(metadata) > MAX_METADATA_JSON_BYTES:
            issues.append(ValidationIssue(
                "error",
                "metadata_too_large",
                f"metadata must serialize to at most {MAX_METADATA_JSON_BYTES} bytes",
                path=source,
                profile_id=class_id,
            ))
        for bool_key in (CUSTOM_CLASS_ARCHIVED_KEY, "disabled"):
            if bool_key in metadata and not isinstance(metadata.get(bool_key), bool):
                issues.append(ValidationIssue(
                    "error",
                    "metadata_lifecycle_flag_not_bool",
                    f"metadata.{bool_key} must be a boolean when present",
                    path=source,
                    profile_id=class_id,
                ))
    if "draft" in data and not isinstance(data.get("draft"), dict):
        issues.append(ValidationIssue(
            "error", "draft_not_mapping", "draft must be a mapping", path=source, profile_id=class_id
        ))
    if "prompt" in data and not isinstance(data.get("prompt"), str):
        issues.append(ValidationIssue(
            "error", "prompt_not_string", "prompt must be a string", path=source, profile_id=class_id
        ))
    elif isinstance(data.get("prompt"), str) and len(data.get("prompt", "")) > MAX_PROMPT_LEN:
        issues.append(ValidationIssue(
            "error",
            "prompt_too_long",
            f"prompt must be at most {MAX_PROMPT_LEN} characters",
            path=source,
            profile_id=class_id,
        ))

    ref_data = data.get("agent_profile_ref")
    ref_id = ""
    ref_version = ""
    if not isinstance(ref_data, dict):
        issues.append(ValidationIssue(
            "error",
            "missing_agent_profile_ref",
            "Agent Class must reference exactly one Agent Profile via agent_profile_ref.id/version",
            path=source,
            profile_id=class_id,
        ))
    else:
        ref_id = str(ref_data.get("id", "") or "").strip()
        ref_version = str(ref_data.get("version", "") or "").strip()
        allowed_ref_keys = {"id", "version"}
        extra_ref_keys = sorted(set(ref_data) - allowed_ref_keys)
        if extra_ref_keys:
            issues.append(ValidationIssue(
                "error",
                "unknown_agent_profile_ref_fields",
                "agent_profile_ref supports only id/version, got: " + ", ".join(extra_ref_keys),
                path=source,
                profile_id=class_id,
            ))
        if not ref_id:
            issues.append(ValidationIssue(
                "error", "missing_agent_profile_ref", "agent_profile_ref.id is required", path=source, profile_id=class_id
            ))
        if not ref_version:
            issues.append(ValidationIssue(
                "error", "missing_agent_profile_ref_version", "agent_profile_ref.version is required", path=source, profile_id=class_id
            ))

    expected_kind = BUILTIN_CLASS_BASE_KIND.get(class_id)
    if expected_kind and base_kind and base_kind != expected_kind:
        issues.append(ValidationIssue(
            "error",
            "class_base_kind_mismatch",
            f"Agent Class {class_id} must use base_kind={expected_kind}, got {base_kind}",
            path=source,
            profile_id=class_id,
        ))
    expected_ref = BUILTIN_CLASS_PROFILE_REF.get(class_id)
    if expected_ref and ref_id and (ref_id, ref_version) != expected_ref:
        issues.append(ValidationIssue(
            "error",
            "class_profile_ref_mismatch",
            f"Agent Class {class_id} must reference {expected_ref[0]}@{expected_ref[1]}",
            path=source,
            profile_id=class_id,
        ))

    draft_data = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    if lifecycle == "draft":
        if draft_data.get("scratch_only") is not True:
            issues.append(ValidationIssue(
                "error",
                "invalid_draft_metadata",
                "draft Agent Classes must set draft.scratch_only: true",
                path=source,
                profile_id=class_id,
            ))
        if draft_data.get("approved_for_live_dogfood", False) is not False:
            issues.append(ValidationIssue(
                "error",
                "invalid_draft_metadata",
                "draft Agent Classes must not claim live dogfood approval in Wave 6B",
                path=source,
                profile_id=class_id,
            ))
    elif draft_data:
        issues.append(ValidationIssue(
            "error",
            "invalid_draft_metadata",
            "stable Agent Classes must not carry draft metadata",
            path=source,
            profile_id=class_id,
        ))

    profiles_lookup = profiles_by_id if profiles_by_id is not None else _profile_by_id_for_validation(base_dir or os.getcwd())
    if ref_id:
        profile = profiles_lookup.get(ref_id)
        if not profile:
            issues.append(ValidationIssue(
                "error",
                "missing_agent_profile_ref",
                f"Agent Class references unknown or invalid Agent Profile: {ref_id}",
                path=source,
                profile_id=class_id,
            ))
        else:
            if ref_version and profile.version != ref_version:
                issues.append(ValidationIssue(
                    "error",
                    "agent_profile_ref_version_mismatch",
                    f"Agent Class references {ref_id}@{ref_version}, but registry has version {profile.version}",
                    path=source,
                    profile_id=class_id,
                ))
            if base_kind and profile.base_kind != base_kind:
                issues.append(ValidationIssue(
                    "error",
                    "agent_profile_base_kind_mismatch",
                    f"Agent Class base_kind={base_kind} cannot reference Agent Profile {ref_id} base_kind={profile.base_kind}",
                    path=source,
                    profile_id=class_id,
                ))

    definition = AgentClassDefinition.from_dict(data, source=source, builtin=builtin)
    if any(issue.severity == "error" for issue in issues):
        return None, issues
    return definition, issues


def load_agent_classes(base_dir: str = "") -> tuple[list[AgentClassDefinition], list[ValidationIssue]]:
    classes: list[AgentClassDefinition] = []
    issues: list[ValidationIssue] = []
    seen: dict[str, AgentClassDefinition] = {}
    profiles_by_id = _profile_by_id_for_validation(base_dir or os.getcwd())
    for root, builtin in find_agent_class_dirs(base_dir=base_dir):
        for path in _iter_yaml_paths(root):
            data, load_issue = load_class_yaml(path)
            if load_issue:
                issues.append(load_issue)
                continue
            assert data is not None
            definition, validation_issues = validate_class_data(
                data,
                source=str(path),
                builtin=builtin,
                base_dir=base_dir,
                profiles_by_id=profiles_by_id,
            )
            issues.extend(validation_issues)
            if not definition:
                continue
            if definition.id in seen:
                issues.append(ValidationIssue(
                    "error",
                    "duplicate_class_id",
                    f"duplicate Agent Class id {definition.id}; first defined at {seen[definition.id].source}",
                    path=str(path),
                    profile_id=definition.id,
                ))
                continue
            classes.append(definition)
            seen[definition.id] = definition
    return sorted(classes, key=lambda item: (item.builtin, item.id)), issues


@lru_cache(maxsize=32)
def _valid_class_lookup(base_dir: str = "") -> tuple[dict[str, AgentClassDefinition], tuple[ValidationIssue, ...]]:
    classes, issues = load_agent_classes(base_dir=base_dir)
    return {definition.id: definition for definition in classes}, tuple(issues)


def agent_class_definition_by_id(class_id: str, *, base_dir: str = "",
                                 include_archived: bool = False) -> AgentClassDefinition | None:
    class_id = str(class_id or "").strip()
    if not class_id:
        return None
    classes_by_id, issues = _valid_class_lookup(base_dir or "")
    if any(issue.severity == "error" for issue in issues):
        return None
    definition = classes_by_id.get(class_id)
    if definition and agent_class_is_archived(definition) and not include_archived:
        return None
    return definition


def default_agent_class_id_for_kind(kind: str) -> str:
    return DEFAULT_CLASS_BY_KIND.get(str(kind or "").strip(), "")


def _class_status_from_previews(class_preview: dict[str, Any], profile_preview: dict[str, Any]) -> str:
    if agent_class_is_archived(class_preview):
        return "archived"
    lifecycle = str(class_preview.get("lifecycle", "") or "").strip().lower()
    profile_status = str(profile_preview.get("status", "") or "").strip().lower()
    if lifecycle and lifecycle != "stable":
        return lifecycle
    if profile_status and profile_status != "full":
        return profile_status
    return "full"


def _profile_ref_text(definition_or_preview: AgentClassDefinition | dict[str, Any]) -> str:
    ref = definition_or_preview.agent_profile_ref if isinstance(definition_or_preview, AgentClassDefinition) else definition_or_preview.get("agent_profile_ref", {})
    if isinstance(ref, AgentClassProfileRef):
        ref_id = ref.id
        ref_version = ref.version
    elif isinstance(ref, dict):
        ref_id = str(ref.get("id", "") or "").strip()
        ref_version = str(ref.get("version", "") or "").strip()
    else:
        ref_id = ""
        ref_version = ""
    return f"{ref_id}@{ref_version}" if ref_id and ref_version else ref_id


def class_warnings_for_preview(class_preview: dict[str, Any], profile_preview: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    class_id = str(class_preview.get("id", "") or "").strip()
    lifecycle = str(class_preview.get("lifecycle", "") or "").strip().lower()
    status = _class_status_from_previews(class_preview, profile_preview)
    if agent_class_is_archived(class_preview):
        warnings.append(
            f"{class_id or 'Agent Class'} is archived/disabled and cannot be assigned or launched until re-enabled."
        )
    if lifecycle and lifecycle != "stable":
        warnings.append(
            f"{class_id or 'Agent Class'} is lifecycle={lifecycle}; use only for scratch/preview unless explicitly approved."
        )
    if class_id == "product-manager" or str((class_preview.get("metadata") or {}).get("archetype", "") if isinstance(class_preview.get("metadata"), dict) else "") == "product_manager":
        warnings.append(PM_DRAFT_WARNING)
    if status in {"draft", "restricted"} or lifecycle == "draft":
        warnings.append(EXTERNAL_CONNECTOR_DRAFT_WARNING)
    # Preserve profile warnings (PM wrapper restrictions, narrowed MCP surface).
    for warning in list(profile_preview.get("warnings", []) or []):
        text = str(warning or "").strip()
        if text and text not in warnings:
            warnings.append(text)
    return warnings


def compact_agent_profile_preview(profile_preview: dict[str, Any]) -> dict[str, Any]:
    denied = list(profile_preview.get("denied_high_risk_capabilities", []) or [])
    return {
        "id": str(profile_preview.get("id", "") or ""),
        "version": str(profile_preview.get("version", "") or ""),
        "base_kind": str(profile_preview.get("base_kind", "") or ""),
        "display_name": str(profile_preview.get("display_name", "") or ""),
        "lifecycle": str(profile_preview.get("lifecycle", "") or ""),
        "status": str(profile_preview.get("status", "") or ""),
        "capability_count": int(profile_preview.get("capability_count", 0) or 0),
        "denied_high_risk_count": len(denied),
        "denied_high_risk_capabilities": denied[:24],
        "runtime_enforcement": str(profile_preview.get("runtime_enforcement", "") or ""),
    }


def enriched_agent_class_preview(definition: AgentClassDefinition | dict[str, Any], *, base_dir: str = "") -> dict[str, Any]:
    if isinstance(definition, dict):
        definition = AgentClassDefinition.from_dict(definition)
    profile = profile_definition_by_id(definition.agent_profile_ref.id, base_dir=base_dir)
    profile_preview = enriched_profile_preview(profile) if profile else {}
    preview = definition.as_preview_dict()
    preview["metadata"] = dict(definition.metadata or {})
    preview["draft"] = dict(definition.draft or {})
    preview["prompt"] = definition.prompt
    preview["agent_profile"] = profile_preview
    preview["status"] = _class_status_from_previews(preview, profile_preview)
    preview["warnings"] = class_warnings_for_preview(preview, profile_preview)
    preview["external_connector_caveat"] = EXTERNAL_CONNECTOR_CAVEAT
    preview["runtime_enforcement"] = "launch_frozen_agent_class_profile_pairing"
    prompt = str(definition.prompt or "")
    preview["prompt_summary"] = {
        "has_prompt": bool(prompt.strip()),
        "char_count": len(prompt),
        "preview": prompt.strip()[:240],
    }
    preview["restrictions"] = [
        "Agent Class can reference exactly one Agent Profile.",
        "Agent Profile remains the MCP/capability enforcement layer.",
        "Agent Class definitions do not mutate running sessions; changes apply only at launch/relaunch boundaries.",
        "Raw MCP tools, capability grants/denies, connector governance, and arbitrary runtime kinds are not part of Agent Class YAML/API.",
    ]
    preview["launchable"] = not agent_class_is_archived(preview)
    return preview


def _json_for_hash(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def snapshot_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(_json_for_hash(data).encode("utf-8")).hexdigest()


def freeze_agent_class_snapshot(
    definition: AgentClassDefinition,
    profile_preview: dict[str, Any],
    *,
    assignment_source: str,
    frozen_at: float,
    base_dir: str = "",
) -> dict[str, Any]:
    full_preview = enriched_agent_class_preview(definition, base_dir=base_dir)
    warnings = list(full_preview.get("warnings", []) or [])
    snapshot = {
        "id": definition.id,
        "version": definition.version,
        "base_kind": definition.base_kind,
        "display_name": definition.display_name,
        "description": definition.description,
        "lifecycle": definition.lifecycle,
        "builtin": bool(definition.builtin),
        "status": str(full_preview.get("status", "") or "full"),
        "agent_profile_ref": definition.agent_profile_ref.as_dict(),
        "agent_profile": compact_agent_profile_preview(profile_preview),
        "prompt": definition.prompt,
        "metadata": dict(definition.metadata or {}),
        "draft": dict(definition.draft or {}),
        "warnings": warnings[:12],
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
        "runtime_enforcement": "launch_frozen_agent_class_profile_pairing",
        "assignment_source": assignment_source,
        "frozen_at": float(frozen_at),
    }
    snapshot["snapshot_hash"] = snapshot_hash({k: v for k, v in snapshot.items() if k != "snapshot_hash"})
    return snapshot


def compact_agent_class_audit_preview(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    return {
        "id": str(snapshot.get("id", "") or ""),
        "version": str(snapshot.get("version", "") or ""),
        "base_kind": str(snapshot.get("base_kind", "") or ""),
        "status": str(snapshot.get("status", "") or ""),
        "lifecycle": str(snapshot.get("lifecycle", "") or ""),
        "agent_profile_ref": dict(snapshot.get("agent_profile_ref") or {}),
        "agent_profile": dict(snapshot.get("agent_profile") or {}),
        "snapshot_hash": str(snapshot.get("snapshot_hash", "") or ""),
        "warnings": list(snapshot.get("warnings", []) or [])[:6],
    }


def agent_class_context_for_cell(cell: Any) -> dict[str, Any]:
    snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return {}
    profile = snapshot.get("agent_profile") if isinstance(snapshot.get("agent_profile"), dict) else {}
    return {
        "id": str(snapshot.get("id", "") or ""),
        "version": str(snapshot.get("version", "") or ""),
        "base_kind": str(snapshot.get("base_kind", "") or ""),
        "display_name": str(snapshot.get("display_name", "") or ""),
        "lifecycle": str(snapshot.get("lifecycle", "") or ""),
        "status": str(snapshot.get("status", "") or ""),
        "agent_profile_id": str(profile.get("id", "") or ""),
        "agent_profile_version": str(profile.get("version", "") or ""),
        "warnings": list(snapshot.get("warnings", []) or [])[:6],
        "external_connector_caveat": str(snapshot.get("external_connector_caveat", "") or ""),
    }


def agent_class_prompt_block_for_cell(cell: Any) -> str:
    snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return ""
    prompt = str(snapshot.get("prompt", "") or "").strip()
    class_id = str(snapshot.get("id", "") or "").strip()
    lifecycle = str(snapshot.get("lifecycle", "") or "").strip()
    status = str(snapshot.get("status", "") or "").strip()
    # Default/full classes intentionally add no prompt text so unassigned base
    # kinds preserve existing behavior by construction.
    if not prompt and class_id.startswith("default-") and status == "full" and lifecycle == "stable":
        return ""
    profile = snapshot.get("agent_profile") if isinstance(snapshot.get("agent_profile"), dict) else {}
    ref = snapshot.get("agent_profile_ref") if isinstance(snapshot.get("agent_profile_ref"), dict) else {}
    lines = [
        "## Agent Class",
        f"Class: {class_id}@{snapshot.get('version', '')} ({snapshot.get('display_name', '') or class_id})",
        f"Lifecycle/status: {lifecycle or '-'} / {status or '-'}",
        f"Referenced Agent Profile: {ref.get('id', profile.get('id', ''))}@{ref.get('version', profile.get('version', ''))}",
        "Agent Class is additive identity/context only; Agent Profile remains the enforcement layer.",
    ]
    if prompt:
        lines.extend(["", prompt])
    warnings = [str(item or "").strip() for item in list(snapshot.get("warnings", []) or []) if str(item or "").strip()]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings[:6])
    return "\n".join(lines).strip() + "\n"


def append_agent_class_prompt_block(base_prompt: str, cell: Any) -> str:
    """Append the frozen Agent Class prompt block to a base prompt.

    Default/full classes intentionally produce no block, preserving existing
    base-kind prompt behavior.  Callers should invoke this only after
    ``apply_effective_agent_class_for_launch`` has frozen the launch snapshot.
    """

    class_block = agent_class_prompt_block_for_cell(cell)
    if not class_block:
        return base_prompt
    base_text = str(base_prompt or "").rstrip()
    if not base_text:
        return class_block
    if class_block.strip() in base_text:
        return base_text + ("\n" if not base_text.endswith("\n") else "")
    return base_text + "\n\n" + class_block


def agent_class_cell_status(cell: Any, *, base_dir: str = "") -> dict[str, Any]:
    kind = str(getattr(cell, "kind", "") or "").strip()
    assigned_id = str(getattr(cell, "agent_class_id", "") or "").strip()
    direct_profile_id = str(getattr(cell, "agent_profile_id", "") or "").strip()
    direct_profile_without_class = bool(direct_profile_id and not assigned_id)
    effective_id = str(getattr(cell, "effective_agent_class_id", "") or "").strip()
    snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
    if not isinstance(snapshot, dict):
        snapshot = {}

    effective_preview = dict(snapshot) if snapshot.get("id") else {}
    if not effective_preview and not direct_profile_without_class:
        default_id = default_agent_class_id_for_kind(kind)
        default_class = agent_class_definition_by_id(default_id, base_dir=base_dir) if default_id else None
        if default_class:
            profile = profile_definition_by_id(default_class.agent_profile_ref.id, base_dir=base_dir)
            profile_preview = enriched_profile_preview(profile) if profile else {}
            effective_preview = freeze_agent_class_snapshot(
                default_class,
                profile_preview,
                assignment_source="implicit_default_class",
                frozen_at=0,
                base_dir=base_dir,
            )
            effective_id = default_class.id
    assigned_preview = {}
    if assigned_id:
        assigned_class = agent_class_definition_by_id(
            assigned_id,
            base_dir=base_dir,
            include_archived=True,
        )
        if assigned_class:
            assigned_preview = enriched_agent_class_preview(assigned_class, base_dir=base_dir)

    next_launch_class_id = "" if direct_profile_without_class else assigned_id or default_agent_class_id_for_kind(kind)
    next_launch_class_version = str(getattr(cell, "agent_class_version", "") or "") if assigned_id else ""
    next_launch_profile_id = ""
    next_launch_profile_version = ""
    if direct_profile_without_class:
        next_launch_profile_id = direct_profile_id
        next_launch_profile_version = str(getattr(cell, "agent_profile_version", "") or "")
    elif next_launch_class_id:
        next_class = agent_class_definition_by_id(
            next_launch_class_id,
            base_dir=base_dir,
            include_archived=True,
        )
        if next_class:
            if not next_launch_class_version:
                next_launch_class_version = next_class.version
            next_launch_profile_id = next_class.agent_profile_ref.id
            next_launch_profile_version = next_class.agent_profile_ref.version

    effective_version = str(
        getattr(cell, "effective_agent_class_version", "")
        or effective_preview.get("version", "")
        or ""
    )
    effective_profile = effective_preview.get("agent_profile") if isinstance(effective_preview.get("agent_profile"), dict) else {}
    effective_profile_id = str(effective_profile.get("id", "") or "")
    effective_profile_version = str(effective_profile.get("version", "") or "")
    if not effective_profile_id:
        effective_profile_id = str(getattr(cell, "effective_agent_profile_id", "") or "")
    if not effective_profile_version:
        effective_profile_version = str(getattr(cell, "effective_agent_profile_version", "") or "")
    if direct_profile_without_class:
        pending_next_launch = bool(
            effective_id
            or (next_launch_profile_id and next_launch_profile_id != effective_profile_id)
            or (next_launch_profile_version and next_launch_profile_version != effective_profile_version)
        )
    else:
        pending_next_launch = bool(
            next_launch_class_id
            and (
            next_launch_class_id != effective_id
            or (next_launch_class_version and next_launch_class_version != effective_version)
            or (next_launch_profile_id and next_launch_profile_id != effective_profile_id)
            or (next_launch_profile_version and next_launch_profile_version != effective_profile_version)
            )
        )
    return {
        "agent_id": str(getattr(cell, "id", "") or ""),
        "agent_name": str(getattr(cell, "name", "") or ""),
        "base_kind": kind,
        "assigned_class_id": assigned_id,
        "assigned_class_version": str(getattr(cell, "agent_class_version", "") or ""),
        "assigned_at": float(getattr(cell, "agent_class_assigned_at", 0) or 0),
        "assigned_by": str(getattr(cell, "agent_class_assigned_by", "") or ""),
        "effective_class_id": effective_id,
        "effective_class_version": effective_version,
        "effective_applied_at": float(getattr(cell, "effective_agent_class_applied_at", 0) or 0),
        "effective_class": effective_preview,
        "assigned_class": assigned_preview,
        "next_launch_class_id": next_launch_class_id,
        "next_launch_class_version": next_launch_class_version,
        "next_launch_profile_id": next_launch_profile_id,
        "next_launch_profile_version": next_launch_profile_version,
        "pending_next_launch": pending_next_launch,
        "status": str(effective_preview.get("status", "") or "full"),
        "direct_agent_profile_assignment": direct_profile_without_class,
        "next_launch_class_disabled": bool(agent_class_is_archived(assigned_preview)) if assigned_preview else False,
        "warnings": list(effective_preview.get("warnings", []) or []),
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }


def built_in_agent_class_ids() -> list[str]:
    return ["default-architect", "default-engineer", "default-worker", "product-manager"]


def _extract_project_class_paths_by_id(base_dir: str = "") -> dict[str, Path]:
    out: dict[str, Path] = {}
    project_dir = project_agent_class_dir(base_dir, create=False)
    for path in _iter_yaml_paths(project_dir):
        data, _load_issue = load_class_yaml(path)
        if not isinstance(data, dict):
            continue
        class_id = str(data.get("id", "") or "").strip()
        if class_id and class_id not in out:
            out[class_id] = path
    return out


def project_agent_class_path_for_id(class_id: str, *, base_dir: str = "") -> Path | None:
    class_id = str(class_id or "").strip()
    if not class_id:
        return None
    existing = _extract_project_class_paths_by_id(base_dir)
    if class_id in existing:
        return existing[class_id]
    if not CLASS_ID_RE.match(class_id):
        return None
    return project_agent_class_dir(base_dir, create=False) / f"{class_id}.yaml"


def _class_authoring_storage(base_dir: str = "", *, path: Path | None = None) -> dict[str, Any]:
    directory = project_agent_class_dir(base_dir, create=False)
    return {
        "kind": "project_yaml",
        "directory": str(directory),
        "path": str(path or ""),
        "config_glob": ".torque/agent_classes/*.yaml",
        "atomic_writes": True,
        "mutates_running_sessions": False,
    }


def normalize_agent_class_authoring_data(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize trusted UI/API authoring aliases into the YAML contract.

    The persisted model intentionally stays narrow.  This function accepts a few
    user-facing aliases (``title`` and safe UI metadata fields) and stores them
    under existing fields so raw capability/tool data is still rejected by the
    validator instead of being silently dropped.
    """

    data = dict(raw or {})
    for alias in AUTHORING_DISPLAY_ALIASES:
        if alias in data and not str(data.get("display_name", "") or "").strip():
            data["display_name"] = data.get(alias)
        data.pop(alias, None)
    for alias in AUTHORING_PROMPT_ALIASES:
        if alias in data and not str(data.get("prompt", "") or "").strip():
            data["prompt"] = data.get(alias)
        data.pop(alias, None)

    ui_metadata: dict[str, str] = {}
    for key in sorted(SAFE_UI_METADATA_KEYS):
        if key not in data:
            continue
        value = data.pop(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            ui_metadata[key] = text[:200]
    if ui_metadata:
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            existing_ui = metadata.get("ui", {})
            if not isinstance(existing_ui, dict):
                existing_ui = {}
            metadata["ui"] = {**existing_ui, **ui_metadata}
            data["metadata"] = metadata
    return data


def _canonical_agent_class_data(data: dict[str, Any]) -> dict[str, Any]:
    ref = data.get("agent_profile_ref") if isinstance(data.get("agent_profile_ref"), dict) else {}
    out: dict[str, Any] = {
        "id": str(data.get("id", "") or "").strip(),
        "version": str(data.get("version", "") or "").strip(),
        "base_kind": str(data.get("base_kind", "") or "").strip(),
        "display_name": str(data.get("display_name", "") or "").strip(),
    }
    description = str(data.get("description", "") or "").strip()
    if description:
        out["description"] = description
    out["lifecycle"] = str(data.get("lifecycle", "stable") or "stable").strip()
    out["agent_profile_ref"] = {
        "id": str(ref.get("id", "") or "").strip(),
        "version": str(ref.get("version", "") or "").strip(),
    }
    prompt = str(data.get("prompt", "") or "").strip()
    if prompt:
        out["prompt"] = prompt
    draft = data.get("draft")
    if isinstance(draft, dict) and draft:
        out["draft"] = dict(draft)
    metadata = data.get("metadata")
    if isinstance(metadata, dict) and metadata:
        out["metadata"] = dict(metadata)
    return out


def validate_agent_class_draft(
    raw_data: dict[str, Any],
    *,
    base_dir: str = "",
    source: str = "agent_class_draft",
) -> dict[str, Any]:
    data = normalize_agent_class_authoring_data(raw_data)
    definition, issues = validate_class_data(
        data,
        source=source,
        builtin=False,
        base_dir=base_dir,
    )
    preview = enriched_agent_class_preview(definition, base_dir=base_dir) if definition else None
    return {
        "ok": not any(issue.severity == "error" for issue in issues),
        "valid": not any(issue.severity == "error" for issue in issues),
        "agent_class": preview,
        "normalized": _canonical_agent_class_data(data) if definition else data,
        "issues": [issue.as_dict() for issue in issues],
        "errors": [issue.as_dict() for issue in issues if issue.severity == "error"],
        "warnings": (
            list(preview.get("warnings", []) or []) if isinstance(preview, dict) else []
        ) + [
            issue.as_dict() for issue in issues if issue.severity == "warn"
        ],
        "storage": _class_authoring_storage(base_dir),
    }


def _write_agent_class_yaml_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                data,
                fh,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def save_custom_agent_class(
    raw_data: dict[str, Any],
    *,
    base_dir: str = "",
    mode: str = "save",
) -> dict[str, Any]:
    """Validate and atomically persist a custom project Agent Class YAML file."""

    normalized = normalize_agent_class_authoring_data(raw_data)
    class_id = str(normalized.get("id", "") or "").strip()
    validation = validate_agent_class_draft(
        normalized,
        base_dir=base_dir,
        source="agent_class_save",
    )
    if not validation["valid"]:
        validation["type"] = "agent_class_save"
        validation["operation"] = mode
        return validation
    if class_id in built_in_agent_class_ids():
        issue = _issue(
            "error",
            "builtin_class_id_reserved",
            f"{class_id} is a built-in Agent Class id and cannot be overwritten by custom YAML",
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_save",
            "ok": False,
            "valid": False,
            "operation": mode,
            "agent_class": None,
            "normalized": normalized,
            "issues": [issue],
            "errors": [issue],
            "warnings": [],
            "storage": _class_authoring_storage(base_dir),
        }

    project_dir = project_agent_class_dir(base_dir, create=True)
    existing_by_id = _extract_project_class_paths_by_id(base_dir)
    existing_path = existing_by_id.get(class_id)
    canonical_path = project_dir / f"{class_id}.yaml"
    path = existing_path or canonical_path
    mode = str(mode or "save").strip().lower()
    if mode == "create" and (existing_path or canonical_path.exists()):
        issue = _issue(
            "error",
            "custom_class_already_exists",
            f"Custom Agent Class already exists: {class_id}",
            path=str(existing_path or canonical_path),
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_save",
            "ok": False,
            "valid": False,
            "operation": "create",
            "agent_class": None,
            "normalized": normalized,
            "issues": [issue],
            "errors": [issue],
            "warnings": [],
            "storage": _class_authoring_storage(base_dir, path=path),
        }
    if mode == "update" and not existing_path:
        issue = _issue(
            "error",
            "custom_class_not_found",
            f"Custom Agent Class not found for update: {class_id}",
            path=str(canonical_path),
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_save",
            "ok": False,
            "valid": False,
            "operation": "update",
            "agent_class": None,
            "normalized": normalized,
            "issues": [issue],
            "errors": [issue],
            "warnings": [],
            "storage": _class_authoring_storage(base_dir, path=path),
        }

    canonical = _canonical_agent_class_data(normalized)
    _write_agent_class_yaml_atomic(path, canonical)
    _valid_class_lookup.cache_clear()
    definition = agent_class_definition_by_id(
        class_id,
        base_dir=base_dir,
        include_archived=True,
    )
    preview = enriched_agent_class_preview(definition, base_dir=base_dir) if definition else None
    operation = "updated" if existing_path else "created"
    return {
        "type": "agent_class_save",
        "ok": True,
        "valid": True,
        "operation": operation,
        "agent_class": preview,
        "normalized": canonical,
        "issues": [],
        "errors": [],
        "warnings": list(preview.get("warnings", []) or []) if isinstance(preview, dict) else [],
        "storage": _class_authoring_storage(base_dir, path=path),
        "audit": {
            "event": f"custom_class_{operation}",
            "mutates_running_sessions": False,
        },
    }


def archive_custom_agent_class(class_id: str, *, base_dir: str = "") -> dict[str, Any]:
    class_id = str(class_id or "").strip()
    path = _extract_project_class_paths_by_id(base_dir).get(class_id)
    if class_id in built_in_agent_class_ids():
        issue = _issue(
            "error",
            "builtin_class_read_only",
            f"{class_id} is built-in and cannot be archived from project config",
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_archive",
            "ok": False,
            "valid": False,
            "issues": [issue],
            "errors": [issue],
            "storage": _class_authoring_storage(base_dir),
        }
    if not path or not path.exists():
        issue = _issue(
            "error",
            "custom_class_not_found",
            f"Custom Agent Class not found: {class_id}",
            path=str(path or ""),
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_archive",
            "ok": False,
            "valid": False,
            "issues": [issue],
            "errors": [issue],
            "storage": _class_authoring_storage(base_dir, path=path),
        }
    data, load_issue = load_class_yaml(path)
    if load_issue or not isinstance(data, dict):
        issue = (load_issue.as_dict() if load_issue else _issue(
            "error",
            "class_not_mapping",
            "Agent Class YAML must be a mapping",
            path=str(path),
            profile_id=class_id,
        ).as_dict())
        return {
            "type": "agent_class_archive",
            "ok": False,
            "valid": False,
            "issues": [issue],
            "errors": [issue],
            "storage": _class_authoring_storage(base_dir, path=path),
        }
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata[CUSTOM_CLASS_ARCHIVED_KEY] = True
    metadata["archived_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["metadata"] = metadata
    save_result = save_custom_agent_class(data, base_dir=base_dir, mode="update")
    save_result["type"] = "agent_class_archive"
    save_result["operation"] = "archived"
    if save_result.get("audit"):
        save_result["audit"]["event"] = "custom_class_archived"
    return save_result


def delete_custom_agent_class(class_id: str, *, base_dir: str = "") -> dict[str, Any]:
    class_id = str(class_id or "").strip()
    path = _extract_project_class_paths_by_id(base_dir).get(class_id)
    if class_id in built_in_agent_class_ids():
        issue = _issue(
            "error",
            "builtin_class_read_only",
            f"{class_id} is built-in and cannot be deleted from project config",
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_delete",
            "ok": False,
            "issues": [issue],
            "errors": [issue],
            "storage": _class_authoring_storage(base_dir),
        }
    if not path or not path.exists():
        issue = _issue(
            "error",
            "custom_class_not_found",
            f"Custom Agent Class not found: {class_id}",
            path=str(path or ""),
            profile_id=class_id,
        ).as_dict()
        return {
            "type": "agent_class_delete",
            "ok": False,
            "issues": [issue],
            "errors": [issue],
            "storage": _class_authoring_storage(base_dir, path=path),
        }
    path.unlink()
    _valid_class_lookup.cache_clear()
    return {
        "type": "agent_class_delete",
        "ok": True,
        "operation": "deleted",
        "class_id": class_id,
        "issues": [],
        "errors": [],
        "storage": _class_authoring_storage(base_dir, path=path),
        "audit": {
            "event": "custom_class_deleted",
            "mutates_running_sessions": False,
        },
    }


def validate_all_agent_classes(base_dir: str = "") -> dict[str, Any]:
    classes, issues = load_agent_classes(base_dir=base_dir)
    errors = [issue for issue in issues if issue.severity == "error"]
    return {
        "classes": classes,
        "issues": issues,
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len([issue for issue in issues if issue.severity == "warn"]),
        "class_count": len(classes),
    }
