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
    BASE_KIND_CEILINGS,
    CAPABILITIES,
    AgentProfileDefinition,
    PM_DANGEROUS_CAPABILITIES,
    ValidationIssue,
    enriched_profile_preview,
    load_agent_profiles,
    profile_definition_by_id,
    validate_profile_data,
)

BUILTIN_CLASS_DIR = Path(__file__).resolve().parent / "builtin_agent_classes"
PROJECT_CLASS_LEAF = "agent_classes"

CLASS_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CLASS_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_LIFECYCLES = {"stable", "draft"}
AGENT_CLASS_SCHEMA_VERSION = 3
DEFAULT_AGENT_CLASS_SCHEMA_VERSION = 2
POLICY_SCHEMA_VERSION = 1
POLICY_COMPILER_VERSION = "agent_class_policy_compiler_v1"
ALLOWED_POLICY_MODES = {"wrap_profile", "compile"}
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
}

BUILTIN_CLASS_POLICY_MODE = {
    "default-architect": "wrap_profile",
    "default-engineer": "wrap_profile",
    "default-worker": "wrap_profile",
    "product-manager": "compile",
}

KNOWN_CLASS_KEYS = {
    "agent_class_schema_version",
    "id",
    "version",
    "base_kind",
    "display_name",
    "description",
    "lifecycle",
    "identity",
    "runtime",
    "agent_profile_ref",
    "prompt",
    "policy",
    "capabilities",
    "communication",
    "delegation",
    "warnings",
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
    "or Agent Profiles in Wave 7; manage connector access separately."
)
EXTERNAL_CONNECTOR_DRAFT_WARNING = (
    "External connector exposure is not enforced by Agent Classes or Agent Profiles in Wave 7; "
    "do not treat draft/restricted classes as live-safe for external connectors."
)
PM_DRAFT_WARNING = (
    "Product Manager is draft/restricted until explicit live-dogfood approval; "
    "do not use it for Blueprint replacement or production product authority without "
    "explicit future approval."
)
PM_DOGFOOD_AUTHORITY_CAVEAT = (
    "Product Manager is approved for live dogfood as a permanent Agent Class, "
    "but remains PM-safe and authority-bounded: no hire, dispatch, merge, deploy, "
    "admin, raw tool picker, accepted-decision authority, connector governance, "
    "or direct Engineer/Worker messaging."
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
    agent_class_schema_version: int = DEFAULT_AGENT_CLASS_SCHEMA_VERSION
    display_name: str = ""
    description: str = ""
    lifecycle: str = "stable"
    prompt: str = ""
    identity: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    communication: dict[str, Any] = field(default_factory=dict)
    delegation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
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
        data = _normalized_class_data(data)
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
            agent_class_schema_version=_agent_class_schema_version(data),
            display_name=str(data.get("display_name", "") or "").strip(),
            description=str(data.get("description", "") or "").strip(),
            lifecycle=str(data.get("lifecycle", "stable") or "stable").strip(),
            agent_profile_ref=ref,
            prompt=str(data.get("prompt", "") or "").strip(),
            identity=(dict(data.get("identity") or {}) if isinstance(data.get("identity"), dict) else {}),
            runtime=(dict(data.get("runtime") or {}) if isinstance(data.get("runtime"), dict) else {}),
            policy=(dict(data.get("policy") or {}) if isinstance(data.get("policy"), dict) else {}),
            capabilities=(dict(data.get("capabilities") or {}) if isinstance(data.get("capabilities"), dict) else {}),
            communication=(dict(data.get("communication") or {}) if isinstance(data.get("communication"), dict) else {}),
            delegation=(dict(data.get("delegation") or {}) if isinstance(data.get("delegation"), dict) else {}),
            warnings=_string_list(data.get("warnings")),
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
            "agent_class_schema_version": self.agent_class_schema_version,
            "base_kind": self.base_kind,
            "display_name": self.display_name,
            "primary_display_name": primary_identity_label_for_class(self),
            "primary_identity_label": primary_identity_label_for_class(self),
            "secondary_base_kind_label": secondary_base_kind_label_for_class(self),
            "description": self.description,
            "lifecycle": self.lifecycle,
            "agent_profile_ref": self.agent_profile_ref.as_dict(),
            "policy": compact_agent_class_policy_preview(self),
            "identity": dict(self.identity or {}),
            "runtime": agent_class_runtime_preview(self),
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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _agent_class_schema_version(data: dict[str, Any]) -> int:
    raw = data.get("agent_class_schema_version", "")
    if raw not in ("", None):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return -1
        return value
    if any(key in data for key in ("identity", "runtime", "policy", "capabilities", "communication", "delegation", "warnings")):
        return AGENT_CLASS_SCHEMA_VERSION
    return DEFAULT_AGENT_CLASS_SCHEMA_VERSION


def _prompt_addendum_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("addendum", "text", "instructions"):
            text = str(value.get(key, "") or "").strip()
            if text:
                return text
    return ""


def _policy_mode_from_data(data: dict[str, Any]) -> str:
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    mode = str(policy.get("mode", "") or "").strip()
    if mode:
        return mode
    if isinstance(policy, dict) and (
            policy.get("grants") is not None
            or policy.get("denies") is not None
            or policy.get("generated_profile_id") is not None):
        return "compile"
    return "wrap_profile"


def _generated_profile_ref_for_data(data: dict[str, Any]) -> AgentClassProfileRef:
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    class_id = str(data.get("id", "") or "").strip()
    class_version = str(data.get("version", "") or "").strip()
    profile_id = str(policy.get("generated_profile_id", "") or "").strip()
    if not profile_id and class_id:
        profile_id = f"class-policy-{class_id}"
    profile_version = str(policy.get("generated_profile_version", "") or "").strip() or class_version
    return AgentClassProfileRef(profile_id, profile_version)


def _normalized_class_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize v2/v3 class shapes into a common in-memory contract."""

    out = dict(data or {})
    schema_version = _agent_class_schema_version(out)
    out["agent_class_schema_version"] = schema_version
    runtime = out.get("runtime") if isinstance(out.get("runtime"), dict) else {}
    runtime_base_kind = str(runtime.get("base_kind", "") or "").strip()
    if runtime_base_kind and not str(out.get("base_kind", "") or "").strip():
        out["base_kind"] = runtime_base_kind
    if runtime_base_kind:
        runtime = dict(runtime)
        runtime["base_kind"] = runtime_base_kind
        out["runtime"] = runtime
    prompt_text = _prompt_addendum_from_value(out.get("prompt", ""))
    out["prompt"] = prompt_text
    mode = _policy_mode_from_data(out)
    policy = out.get("policy") if isinstance(out.get("policy"), dict) else {}
    if mode == "compile":
        policy = dict(policy)
        policy.setdefault("mode", "compile")
        policy.setdefault("policy_schema_version", POLICY_SCHEMA_VERSION)
        out["policy"] = policy
        ref = _generated_profile_ref_for_data(out)
        out["agent_profile_ref"] = ref.as_dict()
    elif isinstance(policy, dict) and policy:
        policy = dict(policy)
        policy.setdefault("mode", "wrap_profile")
        out["policy"] = policy
    if "runtime" not in out or not isinstance(out.get("runtime"), dict):
        out["runtime"] = {
            "base_kind": str(out.get("base_kind", "") or "").strip(),
        }
    else:
        runtime = dict(out.get("runtime") or {})
        runtime.setdefault("base_kind", str(out.get("base_kind", "") or "").strip())
        out["runtime"] = runtime
    return out


def agent_class_policy_mode(definition_or_data: "AgentClassDefinition | dict[str, Any]") -> str:
    if isinstance(definition_or_data, AgentClassDefinition):
        policy = definition_or_data.policy
        if isinstance(policy, dict):
            mode = str(policy.get("mode", "") or "").strip()
            if mode:
                return mode
        return "wrap_profile"
    return _policy_mode_from_data(_normalized_class_data(definition_or_data))


def primary_identity_label_for_class(definition_or_preview: "AgentClassDefinition | dict[str, Any]") -> str:
    if isinstance(definition_or_preview, AgentClassDefinition):
        identity = definition_or_preview.identity or {}
        display_name = definition_or_preview.display_name
        class_id = definition_or_preview.id
    else:
        identity = definition_or_preview.get("identity", {}) if isinstance(definition_or_preview.get("identity"), dict) else {}
        display_name = str(definition_or_preview.get("display_name", "") or "")
        class_id = str(definition_or_preview.get("id", "") or "")
    for key in ("primary_ui_label", "label", "name"):
        text = str(identity.get(key, "") or "").strip()
        if text:
            return text
    return str(display_name or class_id).strip()


def secondary_base_kind_label_for_class(definition_or_preview: "AgentClassDefinition | dict[str, Any]") -> str:
    if isinstance(definition_or_preview, AgentClassDefinition):
        runtime = definition_or_preview.runtime or {}
        base_kind = definition_or_preview.base_kind
        class_id = definition_or_preview.id
    else:
        runtime = definition_or_preview.get("runtime", {}) if isinstance(definition_or_preview.get("runtime"), dict) else {}
        base_kind = str(definition_or_preview.get("base_kind", "") or "")
        class_id = str(definition_or_preview.get("id", "") or "")
    explicit = str(runtime.get("base_kind_label", "") or "").strip()
    if explicit:
        return explicit
    if class_id.startswith("default-"):
        return base_kind.title() if base_kind else ""
    return f"{base_kind.title()}-derived" if base_kind else ""


def agent_class_runtime_preview(definition_or_preview: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    if isinstance(definition_or_preview, AgentClassDefinition):
        runtime = dict(definition_or_preview.runtime or {})
        base_kind = definition_or_preview.base_kind
    else:
        runtime = dict(definition_or_preview.get("runtime") or {}) if isinstance(definition_or_preview.get("runtime"), dict) else {}
        base_kind = str(definition_or_preview.get("base_kind", "") or "")
    runtime["base_kind"] = base_kind
    runtime.setdefault("base_kind_label", secondary_base_kind_label_for_class(definition_or_preview))
    runtime.setdefault("arbitrary_runtime_kind", False)
    return runtime


def compact_agent_class_policy_preview(definition_or_data: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    if isinstance(definition_or_data, AgentClassDefinition):
        policy = dict(definition_or_data.policy or {})
        ref = definition_or_data.agent_profile_ref
        schema_version = definition_or_data.agent_class_schema_version
    else:
        normalized = _normalized_class_data(definition_or_data)
        policy = dict(normalized.get("policy") or {}) if isinstance(normalized.get("policy"), dict) else {}
        ref_data = normalized.get("agent_profile_ref") if isinstance(normalized.get("agent_profile_ref"), dict) else {}
        ref = AgentClassProfileRef(
            str(ref_data.get("id", "") or "").strip(),
            str(ref_data.get("version", "") or "").strip(),
        )
        schema_version = _agent_class_schema_version(normalized)
    mode = str(policy.get("mode", "") or "").strip() or "wrap_profile"
    summary = {
        "mode": mode,
        "agent_class_schema_version": schema_version,
        "policy_schema_version": int(policy.get("policy_schema_version", POLICY_SCHEMA_VERSION) or POLICY_SCHEMA_VERSION)
        if str(policy.get("policy_schema_version", POLICY_SCHEMA_VERSION) or "").isdigit()
        else policy.get("policy_schema_version", POLICY_SCHEMA_VERSION),
        "internal_profile_id": ref.id,
        "internal_profile_version": ref.version,
        "profile_source": "compiled_from_agent_class" if mode == "compile" else "wrapped_agent_profile",
    }
    if mode == "compile":
        summary["policy_compiler_version"] = POLICY_COMPILER_VERSION
        summary["grant_count"] = len(_string_list(policy.get("grants")))
        summary["deny_count"] = len(_string_list(policy.get("denies")))
    return summary


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
                                prefix: str = "",
                                allowed_paths: set[str] | frozenset[str] | None = None) -> list[str]:
    allowed_paths = allowed_paths or set()
    paths: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_text = str(key)
            child = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in forbidden and child not in allowed_paths:
                paths.append(child)
            paths.extend(_nested_forbidden_key_paths(
                value,
                forbidden,
                prefix=child,
                allowed_paths=allowed_paths,
            ))
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            child = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_nested_forbidden_key_paths(
                value,
                forbidden,
                prefix=child,
                allowed_paths=allowed_paths,
            ))
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


def _allowed_raw_field_paths_for_schema(schema_version: int) -> set[str]:
    if schema_version >= AGENT_CLASS_SCHEMA_VERSION:
        return {"capabilities", "policy.grants", "policy.denies"}
    return set()


def _validate_mapping_field(data: dict[str, Any], key: str, issues: list[ValidationIssue], *,
                            source: str, class_id: str, required: bool = False) -> dict[str, Any]:
    if key not in data:
        if required:
            issues.append(ValidationIssue(
                "error",
                f"missing_{key}",
                f"{key} is required",
                path=source,
                profile_id=class_id,
            ))
        return {}
    value = data.get(key)
    if not isinstance(value, dict):
        issues.append(ValidationIssue(
            "error",
            f"{key}_not_mapping",
            f"{key} must be a mapping",
            path=source,
            profile_id=class_id,
        ))
        return {}
    return dict(value or {})


def _validate_string_list_field(value: Any, field_path: str, issues: list[ValidationIssue], *,
                                source: str, class_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        issues.append(ValidationIssue(
            "error",
            "string_list_not_list",
            f"{field_path} must be a list of strings",
            path=source,
            profile_id=class_id,
        ))
        return []
    out: list[str] = []
    for index, item in enumerate(value):
        text = str(item or "").strip()
        if not text:
            issues.append(ValidationIssue(
                "error",
                "string_list_item_empty",
                f"{field_path}[{index}] must be a non-empty string",
                path=source,
                profile_id=class_id,
            ))
            continue
        out.append(text)
    return out


def _validate_compiled_agent_class_profile(definition: "AgentClassDefinition", *,
                                           source: str, base_dir: str = "") -> list[ValidationIssue]:
    del base_dir
    profile_data = _compiled_profile_data_for_class(definition)
    _profile, profile_issues = validate_profile_data(
        profile_data,
        source=f"{source or definition.source or definition.id}#compiled_profile",
        builtin=False,
    )
    out: list[ValidationIssue] = []
    for issue in profile_issues:
        if issue.severity == "error":
            out.append(ValidationIssue(
                issue.severity,
                f"compiled_profile_{issue.code}",
                issue.message,
                path=issue.path or source,
                profile_id=definition.id,
            ))
    return out


def validate_class_data(
    data: dict[str, Any],
    *,
    source: str = "",
    builtin: bool = False,
    base_dir: str = "",
    profiles_by_id: dict[str, AgentProfileDefinition] | None = None,
) -> tuple[AgentClassDefinition | None, list[ValidationIssue]]:
    raw_data = dict(data or {})
    normalized = _normalized_class_data(raw_data)
    issues: list[ValidationIssue] = []
    class_id = str(normalized.get("id", "") or "").strip()
    schema_version = _agent_class_schema_version(raw_data)
    policy_mode = _policy_mode_from_data(normalized)
    allowed_raw_paths = _allowed_raw_field_paths_for_schema(schema_version)

    if schema_version not in {DEFAULT_AGENT_CLASS_SCHEMA_VERSION, AGENT_CLASS_SCHEMA_VERSION}:
        issues.append(ValidationIssue(
            "error",
            "invalid_agent_class_schema_version",
            f"agent_class_schema_version must be {DEFAULT_AGENT_CLASS_SCHEMA_VERSION} or {AGENT_CLASS_SCHEMA_VERSION}",
            path=source,
            profile_id=class_id,
        ))

    unknown_keys = sorted(set(raw_data) - KNOWN_CLASS_KEYS)
    ambiguous = sorted(set(raw_data) & AMBIGUOUS_CLASS_PROFILE_KEYS)
    nested_ambiguous = sorted(set(_nested_forbidden_key_paths(raw_data, AMBIGUOUS_CLASS_PROFILE_KEYS)))
    nested_raw_tool_fields = sorted(set(_nested_forbidden_key_paths(
        raw_data,
        RAW_TOOL_OR_CAPABILITY_FIELDS,
        allowed_paths=allowed_raw_paths,
    )))
    raw_tool_fields = sorted(
        path for path in nested_raw_tool_fields
        if "." not in path and "[" not in path
    )
    if ambiguous:
        issues.append(ValidationIssue(
            "error",
            "agent_cell_profile_confusion",
            "Agent Class definitions must use agent_profile_ref or policy.mode, not legacy AgentCell.profile/runtime profile fields: "
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
            "Agent Class definitions must not contain raw MCP/tool fields or top-level grants/denies: "
            + ", ".join(raw_tool_fields),
            path=source,
            profile_id=class_id,
        ))
    extra_raw_tool_fields = [path for path in nested_raw_tool_fields if path not in raw_tool_fields]
    if extra_raw_tool_fields:
        issues.append(ValidationIssue(
            "error",
            "raw_tool_fields_forbidden",
            "Agent Class definitions must not contain nested raw MCP/tool fields outside policy.grants/policy.denies: "
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
    version = str(normalized.get("version", "") or "").strip()
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
    display_name = normalized.get("display_name", "")
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
    if "description" in normalized:
        description = normalized.get("description", "")
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
    base_kind = str(normalized.get("base_kind", "") or "").strip()
    runtime_data = raw_data.get("runtime") if isinstance(raw_data.get("runtime"), dict) else {}
    runtime_base_kind = str(runtime_data.get("base_kind", "") or "").strip()
    if runtime_base_kind and str(raw_data.get("base_kind", "") or "").strip() and runtime_base_kind != str(raw_data.get("base_kind", "") or "").strip():
        issues.append(ValidationIssue(
            "error",
            "runtime_base_kind_mismatch",
            f"runtime.base_kind={runtime_base_kind} does not match base_kind={raw_data.get('base_kind')}",
            path=source,
            profile_id=class_id,
        ))
    if base_kind not in BASE_KINDS:
        issues.append(ValidationIssue(
            "error",
            "invalid_base_kind",
            f"base_kind/runtime.base_kind must be one of {', '.join(sorted(BASE_KINDS))}",
            path=source,
            profile_id=class_id,
        ))
    lifecycle = str(normalized.get("lifecycle", "stable") or "stable").strip()
    if lifecycle not in ALLOWED_LIFECYCLES:
        issues.append(ValidationIssue(
            "error",
            "invalid_lifecycle",
            "Agent Class lifecycle must be stable or draft",
            path=source,
            profile_id=class_id,
        ))

    for mapping_key in ("identity", "runtime", "metadata", "draft", "policy", "capabilities", "communication", "delegation"):
        if mapping_key in raw_data and not isinstance(raw_data.get(mapping_key), dict):
            issues.append(ValidationIssue(
                "error",
                f"{mapping_key}_not_mapping",
                f"{mapping_key} must be a mapping",
                path=source,
                profile_id=class_id,
            ))
    if "warnings" in raw_data and not isinstance(raw_data.get("warnings"), list):
        issues.append(ValidationIssue(
            "error",
            "warnings_not_list",
            "warnings must be a list of strings",
            path=source,
            profile_id=class_id,
        ))
    _validate_string_list_field(raw_data.get("warnings"), "warnings", issues, source=source, class_id=class_id)

    metadata = raw_data.get("metadata") if isinstance(raw_data.get("metadata"), dict) else {}
    if metadata:
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

    prompt_value = raw_data.get("prompt", "")
    if "prompt" in raw_data and not isinstance(prompt_value, (str, dict)):
        issues.append(ValidationIssue(
            "error", "prompt_not_string_or_mapping", "prompt must be a string or mapping with addendum", path=source, profile_id=class_id
        ))
    prompt_text = str(normalized.get("prompt", "") or "")
    if len(prompt_text) > MAX_PROMPT_LEN:
        issues.append(ValidationIssue(
            "error",
            "prompt_too_long",
            f"prompt addendum must be at most {MAX_PROMPT_LEN} characters",
            path=source,
            profile_id=class_id,
        ))

    ref_data = normalized.get("agent_profile_ref")
    ref_id = ""
    ref_version = ""
    if policy_mode not in ALLOWED_POLICY_MODES:
        issues.append(ValidationIssue(
            "error",
            "invalid_policy_mode",
            "policy.mode must be compile or wrap_profile",
            path=source,
            profile_id=class_id,
        ))
    if not isinstance(ref_data, dict):
        if policy_mode == "wrap_profile":
            issues.append(ValidationIssue(
                "error",
                "missing_agent_profile_ref",
                "Agent Class wrap_profile policy must reference exactly one Agent Profile via agent_profile_ref.id/version",
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
    expected_mode = BUILTIN_CLASS_POLICY_MODE.get(class_id)
    if expected_mode and policy_mode and policy_mode != expected_mode:
        issues.append(ValidationIssue(
            "error",
            "class_policy_mode_mismatch",
            f"Agent Class {class_id} must use policy.mode={expected_mode}",
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

    draft_data = normalized.get("draft") if isinstance(normalized.get("draft"), dict) else {}
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
                "draft Agent Classes must not claim live dogfood approval in Wave 7",
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

    policy_data = normalized.get("policy") if isinstance(normalized.get("policy"), dict) else {}
    if policy_mode == "compile":
        grants = _validate_string_list_field(policy_data.get("grants"), "policy.grants", issues, source=source, class_id=class_id)
        denies = _validate_string_list_field(policy_data.get("denies"), "policy.denies", issues, source=source, class_id=class_id)
        unknown_atoms = sorted((set(grants) | set(denies)) - set(CAPABILITIES))
        if unknown_atoms:
            issues.append(ValidationIssue(
                "error",
                "unknown_policy_capability_atoms",
                "unknown policy capability atoms: " + ", ".join(unknown_atoms),
                path=source,
                profile_id=class_id,
            ))
        if base_kind in BASE_KIND_CEILINGS:
            outside = sorted(set(grants) - BASE_KIND_CEILINGS[base_kind])
            if outside:
                issues.append(ValidationIssue(
                    "error",
                    "policy_grants_outside_base_kind_ceiling",
                    f"policy.grants outside {base_kind} ceiling: " + ", ".join(outside),
                    path=source,
                    profile_id=class_id,
                ))
        archetype = str((metadata or {}).get("archetype", "") or "").strip()
        is_pm_class = class_id == "product-manager" or archetype == "product_manager" or "product-manager" in class_id
        dangerous = sorted(set(grants) & PM_DANGEROUS_CAPABILITIES)
        if is_pm_class and dangerous:
            issues.append(ValidationIssue(
                "error",
                "dangerous_product_manager_policy_grants",
                "Product Manager Agent Classes must not grant dangerous execution/admin capabilities: "
                + ", ".join(dangerous),
                path=source,
                profile_id=class_id,
            ))
        generated_ref = _generated_profile_ref_for_data(normalized)
        if not generated_ref.id or not CLASS_ID_RE.match(generated_ref.id):
            issues.append(ValidationIssue(
                "error",
                "invalid_generated_profile_id",
                "policy.generated_profile_id must be lowercase kebab-case alphanumerics",
                path=source,
                profile_id=class_id,
            ))
        if not generated_ref.version or not CLASS_VERSION_RE.match(generated_ref.version):
            issues.append(ValidationIssue(
                "error",
                "invalid_generated_profile_version",
                "policy.generated_profile_version must be a safe non-empty token",
                path=source,
                profile_id=class_id,
            ))
        for map_key in ("scope", "communication", "spawn", "audit"):
            if map_key in policy_data and not isinstance(policy_data.get(map_key), dict):
                issues.append(ValidationIssue(
                    "error",
                    f"policy_{map_key}_not_mapping",
                    f"policy.{map_key} must be a mapping",
                    path=source,
                    profile_id=class_id,
                ))
    elif policy_mode == "wrap_profile":
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

    definition = AgentClassDefinition.from_dict(normalized, source=source, builtin=builtin)
    if policy_mode == "compile" and not any(issue.severity == "error" for issue in issues):
        issues.extend(_validate_compiled_agent_class_profile(definition, source=source, base_dir=base_dir))
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


def _compiled_profile_data_for_class(definition: AgentClassDefinition) -> dict[str, Any]:
    policy = dict(definition.policy or {})
    generated_ref = definition.agent_profile_ref
    grants = _string_list(policy.get("grants"))
    denies = _string_list(policy.get("denies"))
    profile_policy = {
        "base_kind": definition.base_kind,
        "scope": dict(policy.get("scope") or {}) if isinstance(policy.get("scope"), dict) else {},
        "communication": dict(policy.get("communication") or {}) if isinstance(policy.get("communication"), dict) else {},
        "spawn": dict(policy.get("spawn") or {}) if isinstance(policy.get("spawn"), dict) else {},
        "audit": dict(policy.get("audit") or {}) if isinstance(policy.get("audit"), dict) else {},
    }
    if definition.communication:
        profile_policy.setdefault("communication", {}).update(dict(definition.communication or {}))
    metadata = dict(definition.metadata or {})
    metadata.setdefault("archetype", metadata.get("archetype", ""))
    metadata["generated_by_agent_class"] = {
        "id": definition.id,
        "version": definition.version,
        "display_name": definition.display_name,
        "schema_version": definition.agent_class_schema_version,
        "policy_schema_version": policy.get("policy_schema_version", POLICY_SCHEMA_VERSION),
        "compiler_version": POLICY_COMPILER_VERSION,
    }
    metadata["internal_policy_source"] = "compiled_from_agent_class"
    metadata["generated_profile"] = True
    return {
        "id": generated_ref.id,
        "version": generated_ref.version,
        "base_kind": definition.base_kind,
        "display_name": f"{primary_identity_label_for_class(definition)} internal policy",
        "description": (
            "Generated internal Agent Profile-compatible policy compiled from "
            f"Agent Class {definition.id}@{definition.version}. It is not written as project YAML."
        ),
        "lifecycle": definition.lifecycle,
        "grants": grants,
        "denies": denies,
        "policy": profile_policy,
        "metadata": metadata,
        "tool_categories": {},
    }


def compile_agent_class_profile(definition: AgentClassDefinition | dict[str, Any]) -> AgentProfileDefinition:
    """Compile a class-owned policy into an internal Agent Profile definition."""

    if isinstance(definition, dict):
        definition = AgentClassDefinition.from_dict(definition)
    data = _compiled_profile_data_for_class(definition)
    profile, issues = validate_profile_data(
        data,
        source=f"{definition.source or definition.id}#compiled_profile",
        builtin=False,
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise ValueError(
            "compiled Agent Class policy is invalid: "
            + "; ".join(issue.message for issue in errors[:3])
        )
    assert profile is not None
    profile.source = "generated_internal_agent_class_policy"
    profile.builtin = bool(definition.builtin)
    return profile


def resolve_agent_class_profile_definition(
    definition: AgentClassDefinition | dict[str, Any],
    *,
    base_dir: str = "",
) -> AgentProfileDefinition | None:
    """Return the Agent Profile-compatible policy for a class.

    ``wrap_profile`` classes return a registry profile. ``compile`` classes
    return an in-memory/generated definition that is frozen into SQLite
    snapshots but never written as project YAML in Wave 7B.
    """

    if isinstance(definition, dict):
        definition = AgentClassDefinition.from_dict(definition)
    if agent_class_policy_mode(definition) == "compile":
        return compile_agent_class_profile(definition)
    return profile_definition_by_id(definition.agent_profile_ref.id, base_dir=base_dir)


def internal_policy_preview_for_class(
    definition: AgentClassDefinition,
    profile_preview: dict[str, Any],
) -> dict[str, Any]:
    policy_summary = compact_agent_class_policy_preview(definition)
    denied = list(profile_preview.get("denied_high_risk_capabilities", []) or [])
    out = {
        **policy_summary,
        "display_name": str(profile_preview.get("display_name", "") or ""),
        "base_kind": definition.base_kind,
        "lifecycle": str(profile_preview.get("lifecycle", "") or definition.lifecycle),
        "status": str(profile_preview.get("status", "") or ""),
        "capability_count": int(profile_preview.get("capability_count", 0) or 0),
        "denied_high_risk_count": len(denied),
        "denied_high_risk_capabilities": denied[:24],
        "projected_tool_categories": list(profile_preview.get("projected_tool_categories", []) or []),
        "runtime_enforcement": str(profile_preview.get("runtime_enforcement", "") or ""),
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }
    if policy_summary.get("mode") == "compile":
        out["snapshot_source"] = "sqlite_effective_snapshot_only"
        out["generated_profile_written_to_project_yaml"] = False
    return out


def _product_manager_status_contract(class_preview: dict[str, Any],
                                     profile_preview: dict[str, Any]) -> dict[str, Any]:
    if not _class_is_product_manager_preview(class_preview):
        return {}
    approved = _class_preview_approved_for_live_dogfood(class_preview)
    metadata = class_preview.get("metadata") if isinstance(class_preview.get("metadata"), dict) else {}
    return {
        "approved_for_live_dogfood": approved,
        "permanence_state": str((metadata or {}).get("permanence_state", "") or (
            "dogfood_permanent" if approved else "draft"
        )),
        "authority_model": "pm_safe_restricted",
        "status": _class_status_from_previews(class_preview, profile_preview),
        "raw_architect_authority": False,
        "direct_engineer_worker_messaging": False,
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }


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


def _class_is_product_manager_preview(class_preview: dict[str, Any]) -> bool:
    class_id = str(class_preview.get("id", "") or "").strip()
    metadata = class_preview.get("metadata") if isinstance(class_preview.get("metadata"), dict) else {}
    return (
        class_id == "product-manager"
        or str((metadata or {}).get("archetype", "") or "").strip() == "product_manager"
    )


def _class_preview_approved_for_live_dogfood(class_preview: dict[str, Any]) -> bool:
    metadata = class_preview.get("metadata") if isinstance(class_preview.get("metadata"), dict) else {}
    return bool((metadata or {}).get("approved_for_live_dogfood") is True)


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
    is_product_manager = _class_is_product_manager_preview(class_preview)
    pm_approved = is_product_manager and _class_preview_approved_for_live_dogfood(class_preview)
    if agent_class_is_archived(class_preview):
        warnings.append(
            f"{class_id or 'Agent Class'} is archived/disabled and cannot be assigned or launched until re-enabled."
        )
    if lifecycle and lifecycle != "stable":
        warnings.append(
            f"{class_id or 'Agent Class'} is lifecycle={lifecycle}; use only for scratch/preview unless explicitly approved."
        )
    if is_product_manager:
        warnings.append(PM_DOGFOOD_AUTHORITY_CAVEAT if pm_approved else PM_DRAFT_WARNING)
    if (status in {"draft", "restricted"} or lifecycle == "draft") and not pm_approved:
        warnings.append(EXTERNAL_CONNECTOR_DRAFT_WARNING)
    # Preserve profile warnings (PM wrapper restrictions, narrowed MCP surface).
    for warning in list(profile_preview.get("warnings", []) or []):
        text = str(warning or "").strip()
        if pm_approved and (
                text == PM_DRAFT_WARNING
                or "draft/restricted until explicit live-dogfood approval" in text
                or "approved for bounded live dogfood through the Agent Class" in text
                or text == EXTERNAL_CONNECTOR_DRAFT_WARNING):
            continue
        if text and text not in warnings:
            warnings.append(text)
    return warnings


def compact_agent_profile_preview(profile_preview: dict[str, Any]) -> dict[str, Any]:
    denied = list(profile_preview.get("denied_high_risk_capabilities", []) or [])
    metadata = profile_preview.get("metadata", {}) if isinstance(profile_preview.get("metadata"), dict) else {}
    generated_by = metadata.get("generated_by_agent_class", {}) if isinstance(metadata, dict) else {}
    return {
        "id": str(profile_preview.get("id", "") or ""),
        "version": str(profile_preview.get("version", "") or ""),
        "base_kind": str(profile_preview.get("base_kind", "") or ""),
        "display_name": str(profile_preview.get("display_name", "") or ""),
        "lifecycle": str(profile_preview.get("lifecycle", "") or ""),
        "status": str(profile_preview.get("status", "") or ""),
        "generated": bool(metadata.get("generated_profile")) if isinstance(metadata, dict) else False,
        "source_class_id": str(generated_by.get("id", "") or "") if isinstance(generated_by, dict) else "",
        "source_class_version": str(generated_by.get("version", "") or "") if isinstance(generated_by, dict) else "",
        "policy_compiler_version": str(generated_by.get("compiler_version", "") or "") if isinstance(generated_by, dict) else "",
        "capability_count": int(profile_preview.get("capability_count", 0) or 0),
        "denied_high_risk_count": len(denied),
        "denied_high_risk_capabilities": denied[:24],
        "runtime_enforcement": str(profile_preview.get("runtime_enforcement", "") or ""),
    }


def enriched_agent_class_preview(definition: AgentClassDefinition | dict[str, Any], *, base_dir: str = "") -> dict[str, Any]:
    if isinstance(definition, dict):
        definition = AgentClassDefinition.from_dict(definition)
    profile = resolve_agent_class_profile_definition(definition, base_dir=base_dir)
    profile_preview = enriched_profile_preview(profile) if profile else {}
    preview = definition.as_preview_dict()
    preview["metadata"] = dict(definition.metadata or {})
    preview["draft"] = dict(definition.draft or {})
    preview["prompt"] = definition.prompt
    preview["capabilities"] = dict(definition.capabilities or {})
    preview["communication"] = dict(definition.communication or {})
    preview["delegation"] = dict(definition.delegation or {})
    preview["class_warnings"] = list(definition.warnings or [])
    preview["agent_profile"] = profile_preview
    preview["compiled_profile"] = profile_preview if agent_class_policy_mode(definition) == "compile" else {}
    preview["internal_profile"] = profile_preview
    preview["internal_policy"] = internal_policy_preview_for_class(definition, profile_preview)
    preview["primary_display_name"] = primary_identity_label_for_class(definition)
    preview["primary_identity_label"] = primary_identity_label_for_class(definition)
    preview["secondary_base_kind_label"] = secondary_base_kind_label_for_class(definition)
    preview["secondary_base_kind_metadata"] = agent_class_runtime_preview(definition)
    preview["status"] = _class_status_from_previews(preview, profile_preview)
    pm_status_contract = _product_manager_status_contract(preview, profile_preview)
    if pm_status_contract:
        preview["product_manager_status"] = pm_status_contract
    warnings = class_warnings_for_preview(preview, profile_preview)
    for warning in definition.warnings or []:
        text = str(warning or "").strip()
        if text and text not in warnings:
            warnings.append(text)
    preview["warnings"] = warnings
    preview["external_connector_caveat"] = EXTERNAL_CONNECTOR_CAVEAT
    preview["runtime_enforcement"] = "launch_frozen_agent_class_profile_pairing"
    prompt = str(definition.prompt or "")
    preview["prompt_summary"] = {
        "has_prompt": bool(prompt.strip()),
        "char_count": len(prompt),
        "preview": prompt.strip()[:240],
    }
    preview["restrictions"] = [
        "Agent Class is the operator-facing identity and policy intent.",
        "Agent Profile-compatible internal policy remains the MCP/capability enforcement layer.",
        "Agent Class definitions do not mutate running sessions; changes apply only at launch/relaunch boundaries.",
        "Raw MCP tools, connector governance, and arbitrary runtime kinds are not part of Agent Class YAML/API.",
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
        "agent_class_schema_version": definition.agent_class_schema_version,
        "base_kind": definition.base_kind,
        "display_name": definition.display_name,
        "primary_display_name": primary_identity_label_for_class(definition),
        "primary_identity_label": primary_identity_label_for_class(definition),
        "secondary_base_kind_label": secondary_base_kind_label_for_class(definition),
        "secondary_base_kind_metadata": agent_class_runtime_preview(definition),
        "description": definition.description,
        "lifecycle": definition.lifecycle,
        "builtin": bool(definition.builtin),
        "status": str(full_preview.get("status", "") or "full"),
        "agent_profile_ref": definition.agent_profile_ref.as_dict(),
        "agent_profile": compact_agent_profile_preview(profile_preview),
        "internal_policy": internal_policy_preview_for_class(definition, profile_preview),
        "compiled_profile": compact_agent_profile_preview(profile_preview) if agent_class_policy_mode(definition) == "compile" else {},
        "identity": dict(definition.identity or {}),
        "runtime": agent_class_runtime_preview(definition),
        "capabilities": dict(definition.capabilities or {}),
        "communication": dict(definition.communication or {}),
        "delegation": dict(definition.delegation or {}),
        "prompt": definition.prompt,
        "metadata": dict(definition.metadata or {}),
        "draft": dict(definition.draft or {}),
        "warnings": warnings[:12],
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
        "runtime_enforcement": "launch_frozen_agent_class_profile_pairing",
        "assignment_source": assignment_source,
        "frozen_at": float(frozen_at),
    }
    pm_status_contract = _product_manager_status_contract(snapshot, profile_preview)
    if pm_status_contract:
        snapshot["product_manager_status"] = pm_status_contract
    snapshot["snapshot_hash"] = snapshot_hash({k: v for k, v in snapshot.items() if k != "snapshot_hash"})
    return snapshot


def compact_agent_class_audit_preview(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    return {
        "id": str(snapshot.get("id", "") or ""),
        "version": str(snapshot.get("version", "") or ""),
        "base_kind": str(snapshot.get("base_kind", "") or ""),
        "primary_identity_label": str(snapshot.get("primary_identity_label", snapshot.get("display_name", "")) or ""),
        "secondary_base_kind_label": str(snapshot.get("secondary_base_kind_label", "") or ""),
        "status": str(snapshot.get("status", "") or ""),
        "lifecycle": str(snapshot.get("lifecycle", "") or ""),
        "agent_profile_ref": dict(snapshot.get("agent_profile_ref") or {}),
        "agent_profile": dict(snapshot.get("agent_profile") or {}),
        "internal_policy": dict(snapshot.get("internal_policy") or {}),
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
        "primary_identity_label": str(snapshot.get("primary_identity_label", snapshot.get("display_name", "")) or ""),
        "secondary_base_kind_label": str(snapshot.get("secondary_base_kind_label", "") or ""),
        "lifecycle": str(snapshot.get("lifecycle", "") or ""),
        "status": str(snapshot.get("status", "") or ""),
        "agent_profile_id": str(profile.get("id", "") or ""),
        "agent_profile_version": str(profile.get("version", "") or ""),
        "internal_policy": dict(snapshot.get("internal_policy") or {}),
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
        f"Class: {class_id}@{snapshot.get('version', '')} ({snapshot.get('primary_identity_label', snapshot.get('display_name', '')) or class_id})",
        f"Lifecycle/status: {lifecycle or '-'} / {status or '-'}",
        f"Internal Agent Profile policy: {ref.get('id', profile.get('id', ''))}@{ref.get('version', profile.get('version', ''))}",
        f"Internal base kind: {snapshot.get('secondary_base_kind_label', snapshot.get('base_kind', '')) or '-'}",
        "Agent Class is the primary operator-facing identity; Agent Profile-compatible policy remains the enforcement layer.",
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
    warnings = list(effective_preview.get("warnings", []) or [])
    legacy_direct_profile_warning = ""
    if direct_profile_without_class and next_launch_profile_id == "product-manager-draft":
        legacy_direct_profile_warning = (
            "Legacy direct Product Manager Agent Profile assignment is pending; "
            "set desired Agent Class to product-manager for class-first next relaunch. "
            "No silent migration is performed."
        )
        warnings.append(legacy_direct_profile_warning)
    next_launch_pm_approved = False
    if assigned_preview and assigned_preview.get("id") == next_launch_class_id:
        next_launch_pm_approved = (
            _class_is_product_manager_preview(assigned_preview)
            and _class_preview_approved_for_live_dogfood(assigned_preview)
        )
    elif effective_preview and effective_preview.get("id") == next_launch_class_id:
        next_launch_pm_approved = (
            _class_is_product_manager_preview(effective_preview)
            and _class_preview_approved_for_live_dogfood(effective_preview)
        )
    if not direct_profile_without_class and next_launch_class_id == "product-manager" and not next_launch_pm_approved:
        if EXTERNAL_CONNECTOR_DRAFT_WARNING not in warnings:
            warnings.append(EXTERNAL_CONNECTOR_DRAFT_WARNING)
    deduped_warnings: list[str] = []
    seen_warnings: set[str] = set()
    for warning in warnings:
        text = str(warning or "").strip()
        if text and text not in seen_warnings:
            deduped_warnings.append(text)
            seen_warnings.add(text)
    next_launch_label = ""
    if direct_profile_without_class:
        next_launch_label = "Internal policy: " + next_launch_profile_id if next_launch_profile_id else ""
    elif next_launch_class_id:
        if assigned_preview and assigned_preview.get("id") == next_launch_class_id:
            next_launch_label = str(assigned_preview.get("primary_identity_label", "") or "")
        elif effective_preview and effective_preview.get("id") == next_launch_class_id:
            next_launch_label = str(effective_preview.get("primary_identity_label", "") or "")
        else:
            next_class = agent_class_definition_by_id(
                next_launch_class_id,
                base_dir=base_dir,
                include_archived=True,
            )
            next_launch_label = primary_identity_label_for_class(next_class) if next_class else next_launch_class_id
    effective_label = str(effective_preview.get("primary_identity_label", effective_preview.get("display_name", "")) or "")
    assigned_label = str(assigned_preview.get("primary_identity_label", assigned_preview.get("display_name", "")) or "")
    return {
        "agent_id": str(getattr(cell, "id", "") or ""),
        "agent_name": str(getattr(cell, "name", "") or ""),
        "base_kind": kind,
        "primary_class_display_label": effective_label,
        "primary_identity_label": effective_label,
        "effective_primary_identity_label": effective_label,
        "assigned_primary_identity_label": assigned_label,
        "next_launch_primary_identity_label": next_launch_label,
        "secondary_base_kind_label": str(effective_preview.get("secondary_base_kind_label", "") or ""),
        "secondary_base_kind_metadata": dict(effective_preview.get("secondary_base_kind_metadata") or {}),
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
        "legacy_direct_product_manager_profile": bool(legacy_direct_profile_warning),
        "legacy_direct_profile_warning": legacy_direct_profile_warning,
        "internal_policy": dict(effective_preview.get("internal_policy") or {}),
        "next_launch_internal_policy": (
            dict(assigned_preview.get("internal_policy") or {})
            if assigned_preview and assigned_preview.get("id") == next_launch_class_id
            else dict(effective_preview.get("internal_policy") or {})
            if effective_preview and effective_preview.get("id") == next_launch_class_id
            else {}
        ),
        "next_launch_class_disabled": bool(agent_class_is_archived(assigned_preview)) if assigned_preview else False,
        "warnings": deduped_warnings,
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
    data = _normalized_class_data(data)
    ref = data.get("agent_profile_ref") if isinstance(data.get("agent_profile_ref"), dict) else {}
    schema_version = _agent_class_schema_version(data)
    out: dict[str, Any] = {
        "id": str(data.get("id", "") or "").strip(),
        "version": str(data.get("version", "") or "").strip(),
        "display_name": str(data.get("display_name", "") or "").strip(),
    }
    if schema_version >= AGENT_CLASS_SCHEMA_VERSION:
        out["agent_class_schema_version"] = schema_version
        runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
        if runtime:
            out["runtime"] = dict(runtime)
        else:
            out["base_kind"] = str(data.get("base_kind", "") or "").strip()
    else:
        out["base_kind"] = str(data.get("base_kind", "") or "").strip()
    description = str(data.get("description", "") or "").strip()
    if description:
        out["description"] = description
    out["lifecycle"] = str(data.get("lifecycle", "stable") or "stable").strip()
    policy_mode = _policy_mode_from_data(data)
    if policy_mode == "wrap_profile":
        out["agent_profile_ref"] = {
            "id": str(ref.get("id", "") or "").strip(),
            "version": str(ref.get("version", "") or "").strip(),
        }
    prompt = str(data.get("prompt", "") or "").strip()
    if prompt:
        out["prompt"] = {"addendum": prompt} if schema_version >= AGENT_CLASS_SCHEMA_VERSION else prompt
    for key in ("identity", "policy", "capabilities", "communication", "delegation"):
        value = data.get(key)
        if isinstance(value, dict) and value:
            out[key] = dict(value)
    warnings = _string_list(data.get("warnings"))
    if warnings:
        out["warnings"] = warnings
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
