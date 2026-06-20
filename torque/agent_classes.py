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
ALLOWED_LIFECYCLES = {"stable", "draft"}

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
    "agent_profile",
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
        return {
            "id": self.id,
            "version": self.version,
            "base_kind": self.base_kind,
            "display_name": self.display_name,
            "description": self.description,
            "lifecycle": self.lifecycle,
            "agent_profile_ref": self.agent_profile_ref.as_dict(),
            "builtin": self.builtin,
        }


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
    if raw_tool_fields:
        issues.append(ValidationIssue(
            "error",
            "raw_tool_fields_forbidden",
            "Agent Class definitions must not contain raw MCP/tool/capability fields: "
            + ", ".join(raw_tool_fields),
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
    if "draft" in data and not isinstance(data.get("draft"), dict):
        issues.append(ValidationIssue(
            "error", "draft_not_mapping", "draft must be a mapping", path=source, profile_id=class_id
        ))
    if "prompt" in data and not isinstance(data.get("prompt"), str):
        issues.append(ValidationIssue(
            "error", "prompt_not_string", "prompt must be a string", path=source, profile_id=class_id
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


def agent_class_definition_by_id(class_id: str, *, base_dir: str = "") -> AgentClassDefinition | None:
    class_id = str(class_id or "").strip()
    if not class_id:
        return None
    classes_by_id, issues = _valid_class_lookup(base_dir or "")
    if any(issue.severity == "error" for issue in issues):
        return None
    return classes_by_id.get(class_id)


def default_agent_class_id_for_kind(kind: str) -> str:
    return DEFAULT_CLASS_BY_KIND.get(str(kind or "").strip(), "")


def _class_status_from_previews(class_preview: dict[str, Any], profile_preview: dict[str, Any]) -> str:
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
        assigned_class = agent_class_definition_by_id(assigned_id, base_dir=base_dir)
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
        next_class = agent_class_definition_by_id(next_launch_class_id, base_dir=base_dir)
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
        "warnings": list(effective_preview.get("warnings", []) or []),
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }


def built_in_agent_class_ids() -> list[str]:
    return ["default-architect", "default-engineer", "default-worker", "product-manager"]


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
