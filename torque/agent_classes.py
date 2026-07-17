"""Agent Class registry, validation, and launch preview helpers.

Agent Classes are user-facing structured templates over Torque's existing
runtime agent kinds. A class defines its prompt and a generic capability ACL;
the ACL compiles directly into the frozen effective-authority snapshot used to
project and authorize Torque MCP tools.
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

from .capability_catalog import (
    BASE_KINDS,
    CAPABILITY_CATALOG,
    capability_catalog_for_base_kind,
)
from .mcp_authority import (
    AuthorityValidationError,
    canonical_capability_ids,
    compile_agent_class_acl,
    registry_hash,
)

BUILTIN_CLASS_DIR = Path(__file__).resolve().parent / "builtin_agent_classes"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    path: str = ""
    class_id: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            data["path"] = self.path
        if self.class_id:
            data["class_id"] = self.class_id
        return data
PROJECT_CLASS_LEAF = "agent_classes"

CLASS_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CLASS_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_LIFECYCLES = {"stable", "draft"}
AGENT_CLASS_SCHEMA_VERSION = 5
DEFAULT_AGENT_CLASS_SCHEMA_VERSION = 5
SAFE_UI_METADATA_KEYS = {"label", "icon", "badge", "color"}
AUTHORING_DISPLAY_ALIASES = {"title", "display_title"}
AUTHORING_DESCRIPTION_ALIASES = {"purpose"}
CUSTOM_CLASS_ARCHIVED_KEY = "archived"
MAX_DISPLAY_NAME_LEN = 120
MAX_DESCRIPTION_LEN = 2000
MAX_PROMPT_LEN = 30000
MAX_METADATA_JSON_BYTES = 65536

PROMPT_TEXT_KEYS = ("identity", "job")
PROMPT_LIST_KEYS = ("boot_checklist", "operating_guidelines")
PROMPT_TOOL_GUIDANCE_KEY = "tool_guidance"
PROMPT_ALLOWED_KEYS = set(PROMPT_TEXT_KEYS) | set(PROMPT_LIST_KEYS) | {PROMPT_TOOL_GUIDANCE_KEY}

DEFAULT_CLASS_BY_KIND = {
    "architect": "default-architect",
    "engineer": "default-engineer",
    "worker": "default-worker",
}

BUILTIN_CLASS_BASE_KIND: dict[str, str] = {}

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
    "prompt",
    "policy",
    "acl",
    "operator_summary",
    "capability_buckets",
    "restriction_buckets",
    "capabilities",
    "communication",
    "warnings",
    "metadata",
    "draft",
}

# These names collide with AgentCell terminal-profile terminology.
AMBIGUOUS_CLASS_RUNTIME_PROFILE_KEYS = {
    "profile",
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
    "External connector exposure is not governed or enforced by Agent Class "
    "ACLs; manage connector access separately."
)
EXTERNAL_CONNECTOR_DRAFT_WARNING = (
    "External connector exposure is not enforced by Agent Class ACLs; "
    "do not treat draft/restricted classes as live-safe for external connectors."
)


def agent_class_authoring_contract(*, base_kind: str = "") -> dict[str, Any]:
    return {
        "schema_version": AGENT_CLASS_SCHEMA_VERSION,
        "normal_authoring_mode": "capability_acl",
        "acl_modes": ["allow", "deny"],
        "acl_shape": "acl.mode + acl.rules",
        "acl_rule_keys": ["capability", "capabilities", "scope"],
        "acl_rule_variants": {
            "single": ["capability", "scope"],
            "grouped_by_scope": ["scope", "capabilities"],
        },
        "scope_vocabulary": ["self", "children", "group", "global"],
        "capability_catalog": capability_catalog_for_base_kind(base_kind),
        "apply_model": "Agent Class saves/assignments do not mutate running sessions; ACL changes apply at next launch/relaunch.",
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }


@dataclass
class AgentClassDefinition:
    id: str
    version: str
    base_kind: str
    agent_class_schema_version: int = DEFAULT_AGENT_CLASS_SCHEMA_VERSION
    display_name: str = ""
    description: str = ""
    lifecycle: str = "stable"
    prompt: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    acl: dict[str, Any] = field(default_factory=dict)
    operator_summary: dict[str, Any] = field(default_factory=dict)
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
        return cls(
            id=str(data.get("id", "") or "").strip(),
            version=str(data.get("version", "") or "").strip(),
            base_kind=str(data.get("base_kind", "") or "").strip(),
            agent_class_schema_version=_agent_class_schema_version(data),
            display_name=str(data.get("display_name", "") or "").strip(),
            description=str(data.get("description", "") or "").strip(),
            lifecycle=str(data.get("lifecycle", "stable") or "stable").strip(),
            prompt=_normalized_prompt_mapping(data.get("prompt", {})),
            identity=(dict(data.get("identity") or {}) if isinstance(data.get("identity"), dict) else {}),
            runtime=(dict(data.get("runtime") or {}) if isinstance(data.get("runtime"), dict) else {}),
            acl=(dict(data.get("acl") or {}) if isinstance(data.get("acl"), dict) else {}),
            operator_summary=(dict(data.get("operator_summary") or {}) if isinstance(data.get("operator_summary"), dict) else {}),
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
            "purpose": self.description,
            "lifecycle": self.lifecycle,
            "acl": compact_agent_class_acl_preview(self),
            "authority_summary": agent_class_authority_summary(self),
            "identity": dict(self.identity or {}),
            "runtime": agent_class_runtime_preview(self),
            "operator_summary": dict(self.operator_summary or {}),
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
    class_id: str = "",
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        path=path,
        class_id=class_id,
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

def _acl_mapping(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("acl")
    return dict(value or {}) if isinstance(value, dict) else {}


def _compiled_authority_for_data(
    data: "AgentClassDefinition | dict[str, Any]",
) -> dict[str, Any]:
    if isinstance(data, AgentClassDefinition):
        base_kind = data.base_kind
        acl = dict(data.acl or {})
    else:
        normalized = _normalized_class_data(dict(data or {}))
        base_kind = str(normalized.get("base_kind", "") or "").strip()
        acl = _acl_mapping(normalized)
    try:
        authority = compile_agent_class_acl(
            base_kind=base_kind,
            acl=acl,
            capabilities=CAPABILITY_CATALOG,
        )
    except AuthorityValidationError:
        return {
            "mode": str(acl.get("mode", "") or ""),
            "canonical_capabilities": {},
            "effective_authority": {},
        }
    return {
        "mode": authority.mode,
        "canonical_capabilities": dict(authority.capabilities),
        "effective_authority": authority.as_snapshot(),
    }


def _agent_class_schema_version(data: dict[str, Any]) -> int:
    raw = data.get("agent_class_schema_version", "")
    if raw not in ("", None):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return -1
        return value
    if any(key in data for key in ("identity", "runtime", "prompt", "policy", "acl", "capabilities", "capability_buckets", "restriction_buckets", "communication", "warnings")):
        return AGENT_CLASS_SCHEMA_VERSION
    return DEFAULT_AGENT_CLASS_SCHEMA_VERSION


def _normalized_prompt_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in PROMPT_TEXT_KEYS:
        text = str(value.get(key, "") or "").strip()
        if text:
            out[key] = text
    for key in PROMPT_LIST_KEYS:
        items = _string_list(value.get(key))
        if items:
            out[key] = items
    guidance_items: list[dict[str, str]] = []
    raw_guidance = value.get(PROMPT_TOOL_GUIDANCE_KEY)
    if isinstance(raw_guidance, list):
        for item in raw_guidance:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "") or "").strip()
            if not text:
                continue
            normalized: dict[str, str] = {"text": text}
            selector = str(item.get("when_capability", "") or "").strip()
            if selector:
                normalized["when_capability"] = selector
            guidance_items.append(normalized)
    if guidance_items:
        out[PROMPT_TOOL_GUIDANCE_KEY] = guidance_items
    return out


def _prompt_plain_text(prompt: Any) -> str:
    prompt = _normalized_prompt_mapping(prompt)
    parts: list[str] = []
    for key in PROMPT_TEXT_KEYS:
        if prompt.get(key):
            parts.append(str(prompt[key]))
    for key in PROMPT_LIST_KEYS:
        parts.extend(str(item) for item in prompt.get(key, []) or [])
    for item in prompt.get(PROMPT_TOOL_GUIDANCE_KEY, []) or []:
        if isinstance(item, dict):
            parts.append(str(item.get("text", "") or ""))
    return "\n".join(part.strip() for part in parts if str(part).strip()).strip()


def _snapshot_prompt_capability_tokens(snapshot: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    effective = snapshot.get("effective_authority")
    if isinstance(effective, dict):
        canonical = effective.get("capabilities")
        if isinstance(canonical, dict):
            tokens.update(str(item or "").strip() for item in canonical if str(item or "").strip())
    return tokens


def _prompt_guidance_visible(item: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    capability = str(item.get("when_capability", "") or "").strip()
    if capability and capability not in _snapshot_prompt_capability_tokens(snapshot):
        return False
    return True


def render_agent_class_prompt(prompt: Any, *, snapshot: dict[str, Any] | None = None) -> str:
    prompt = _normalized_prompt_mapping(prompt)
    snapshot = snapshot or {}
    lines: list[str] = []
    identity = str(prompt.get("identity", "") or "").strip()
    if identity:
        lines.extend(["## Class identity", identity])
    job = str(prompt.get("job", "") or "").strip()
    if job:
        if lines:
            lines.append("")
        lines.extend(["## Class job", job])
    boot = [str(item or "").strip() for item in prompt.get("boot_checklist", []) or [] if str(item or "").strip()]
    if boot:
        if lines:
            lines.append("")
        lines.append("## Class boot checklist")
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(boot, start=1))
    guidelines = [str(item or "").strip() for item in prompt.get("operating_guidelines", []) or [] if str(item or "").strip()]
    if guidelines:
        if lines:
            lines.append("")
        lines.append("## Class operating guidelines")
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(guidelines, start=1))
    guidance = [
        item for item in prompt.get(PROMPT_TOOL_GUIDANCE_KEY, []) or []
        if isinstance(item, dict) and _prompt_guidance_visible(item, snapshot)
    ]
    if guidance:
        if lines:
            lines.append("")
        lines.append("## Class tool guidance")
        for item in guidance:
            selector = str(item.get("when_capability") or "").strip()
            text = str(item.get("text", "") or "").strip()
            prefix = f"When `{selector}` is available: " if selector else ""
            lines.append(f"- {prefix}{text}")
    return "\n".join(lines).strip()


def _normalized_class_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize the schema-v5 class shape without inventing authority fields."""

    out = dict(data or {})
    if isinstance(out.get("acl"), dict):
        acl = dict(out.get("acl") or {})
        acl.setdefault("mode", "allow")
        out["acl"] = acl
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
    base_kind = str(out.get("base_kind", "") or runtime_base_kind).strip()
    if isinstance(out.get("acl"), dict):
        acl = dict(out.get("acl") or {})
        migrated_rules = []
        capability_alias_found = False
        for rule in list(acl.get("rules") or []):
            if not isinstance(rule, dict):
                migrated_rules.append(rule)
                continue
            raw_capabilities = (
                list(rule.get("capabilities") or [])
                if "capabilities" in rule
                else [rule.get("capability", "")]
            )
            for raw_capability in raw_capabilities:
                canonical_ids = canonical_capability_ids(
                    raw_capability,
                    base_kind=base_kind,
                )
                capability_alias_found = capability_alias_found or (
                    canonical_ids != (str(raw_capability or "").strip(),)
                )
                for capability_id in canonical_ids:
                    migrated = {"capability": capability_id}
                    if "scope" in rule:
                        migrated["scope"] = (
                            "self"
                            if capability_id == "message.supervisor"
                            else rule.get("scope")
                        )
                    migrated_rules.append(migrated)
        if capability_alias_found:
            acl["rules"] = migrated_rules
        out["acl"] = acl
    out["prompt"] = _normalized_prompt_mapping(out.get("prompt", {}))
    migrated_guidance = []
    for guidance in out["prompt"].get(PROMPT_TOOL_GUIDANCE_KEY, []) or []:
        if not isinstance(guidance, dict):
            migrated_guidance.append(guidance)
            continue
        selector = str(guidance.get("when_capability", "") or "").strip()
        capability_ids = (
            canonical_capability_ids(selector, base_kind=base_kind)
            if selector
            else ("",)
        )
        for capability_id in capability_ids:
            migrated = dict(guidance)
            if capability_id:
                migrated["when_capability"] = capability_id
            migrated_guidance.append(migrated)
    out["prompt"][PROMPT_TOOL_GUIDANCE_KEY] = migrated_guidance
    if "runtime" not in out or not isinstance(out.get("runtime"), dict):
        out["runtime"] = {
            "base_kind": str(out.get("base_kind", "") or "").strip(),
        }
    else:
        runtime = dict(out.get("runtime") or {})
        runtime.setdefault("base_kind", str(out.get("base_kind", "") or "").strip())
        out["runtime"] = runtime
    return out


def primary_identity_label_for_class(definition_or_preview: "AgentClassDefinition | dict[str, Any]") -> str:
    if isinstance(definition_or_preview, AgentClassDefinition):
        identity = definition_or_preview.identity or {}
        display_name = definition_or_preview.display_name
        class_id = definition_or_preview.id
        metadata = definition_or_preview.metadata or {}
    else:
        identity = definition_or_preview.get("identity", {}) if isinstance(definition_or_preview.get("identity"), dict) else {}
        display_name = str(definition_or_preview.get("display_name", "") or "")
        class_id = str(definition_or_preview.get("id", "") or "")
        metadata = definition_or_preview.get("metadata", {}) if isinstance(definition_or_preview.get("metadata"), dict) else {}
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


def compact_agent_class_acl_preview(definition_or_data: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    compiled = _compiled_authority_for_data(definition_or_data)
    mode = str(compiled.get("mode", "") or "").strip()
    canonical = compiled.get("canonical_capabilities")
    if isinstance(canonical, dict):
        return {
            "mode": mode,
            "rules": [
                {
                    "capability": capability,
                    **({"scope": scope} if scope is not None else {}),
                }
                for capability, scope in sorted(canonical.items())
            ],
            "capabilities": dict(sorted(canonical.items())),
        }
    return {"mode": mode, "rules": [], "capabilities": {}}


def agent_class_authority_summary(definition_or_data: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    if isinstance(definition_or_data, AgentClassDefinition):
        base_kind = definition_or_data.base_kind
        lifecycle = definition_or_data.lifecycle
    else:
        normalized = _normalized_class_data(definition_or_data)
        base_kind = str(normalized.get("base_kind", "") or "")
        lifecycle = str(normalized.get("lifecycle", "") or "")
    compiled = _compiled_authority_for_data(definition_or_data)
    canonical = compiled.get("canonical_capabilities")
    if isinstance(canonical, dict):
        high_risk = sorted(
            capability
            for capability in canonical
            if CAPABILITY_CATALOG[capability].risk in {"high", "critical"}
        )
        return {
            "mode": str(compiled.get("mode", "") or ""),
            "base_kind": base_kind,
            "lifecycle": lifecycle,
            "capability_count": len(canonical),
            "capabilities": dict(sorted(canonical.items())),
            "high_risk_capabilities": high_risk,
        }
    return {
        "mode": str(compiled.get("mode", "") or ""),
        "base_kind": base_kind,
        "lifecycle": lifecycle,
        "capability_count": 0,
        "capabilities": {},
        "high_risk_capabilities": [],
    }


def effective_authority_snapshot_for_class(
    definition_or_data: "AgentClassDefinition | dict[str, Any]",
) -> dict[str, Any]:
    """Return the canonical authority snapshot compiled from class ACL data."""

    compiled = _compiled_authority_for_data(definition_or_data)
    snapshot = compiled.get("effective_authority")
    if not isinstance(snapshot, dict) or not snapshot:
        return {}
    out = dict(snapshot)
    catalog_payload = {
        capability_id: {
            "risk": definition.risk,
            "base_kinds": sorted(definition.base_kinds),
            "scopes": list(definition.scopes),
            "ceilings": dict(sorted(definition.ceilings.items())),
        }
        for capability_id, definition in sorted(CAPABILITY_CATALOG.items())
    }
    out["capability_registry_hash"] = registry_hash(catalog_payload)
    return out


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
            if key_text in forbidden and child not in allowed_paths and not child.startswith("acl."):
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
            class_id=class_id,
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
                class_id=class_id,
            ))
            continue
        out.append(text)
    return out


def validate_class_data(
    data: dict[str, Any],
    *,
    source: str = "",
    builtin: bool = False,
    base_dir: str = "",
) -> tuple[AgentClassDefinition | None, list[ValidationIssue]]:
    del base_dir
    raw_data = dict(data or {})
    normalized = _normalized_class_data(raw_data)
    issues: list[ValidationIssue] = []
    class_id = str(normalized.get("id", "") or "").strip()
    schema_version = _agent_class_schema_version(raw_data)
    allowed_raw_paths: set[str] = set()

    if schema_version != AGENT_CLASS_SCHEMA_VERSION:
        issues.append(ValidationIssue(
            "error",
            "invalid_agent_class_schema_version",
            f"agent_class_schema_version must be {AGENT_CLASS_SCHEMA_VERSION}",
            path=source,
            class_id=class_id,
        ))

    unknown_keys = sorted(set(raw_data) - KNOWN_CLASS_KEYS)
    ambiguous = sorted(set(raw_data) & AMBIGUOUS_CLASS_RUNTIME_PROFILE_KEYS)
    nested_ambiguous = sorted(set(_nested_forbidden_key_paths(raw_data, AMBIGUOUS_CLASS_RUNTIME_PROFILE_KEYS)))
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
            "Agent Class definitions must use base_kind plus acl.mode/acl.rules, not AgentCell/profile fields: "
            + ", ".join(ambiguous),
            path=source,
            class_id=class_id,
        ))
    extra_ambiguous = [path for path in nested_ambiguous if path not in ambiguous]
    if extra_ambiguous:
        issues.append(ValidationIssue(
            "error",
            "agent_cell_profile_confusion",
            "Agent Class definitions must not contain AgentCell/profile-like fields: "
            + ", ".join(extra_ambiguous),
            path=source,
            class_id=class_id,
        ))
    if raw_tool_fields:
        issues.append(ValidationIssue(
            "error",
            "raw_tool_fields_forbidden",
            "Agent Class definitions must not contain raw MCP/tool fields or top-level grants/denies: "
            + ", ".join(raw_tool_fields),
            path=source,
            class_id=class_id,
        ))
    extra_raw_tool_fields = [path for path in nested_raw_tool_fields if path not in raw_tool_fields]
    if extra_raw_tool_fields:
        issues.append(ValidationIssue(
            "error",
            "raw_tool_fields_forbidden",
            "Agent Class definitions must not contain raw MCP/tool fields or raw grants/denies; use acl.mode + acl.rules instead: "
            + ", ".join(extra_raw_tool_fields),
            path=source,
            class_id=class_id,
        ))
    non_confusing_unknown = [
        key for key in unknown_keys
        if key not in AMBIGUOUS_CLASS_RUNTIME_PROFILE_KEYS and key not in RAW_TOOL_OR_CAPABILITY_FIELDS
    ]
    if non_confusing_unknown:
        issues.append(ValidationIssue(
            "error",
            "unknown_class_fields",
            "unknown Agent Class fields: " + ", ".join(non_confusing_unknown),
            path=source,
            class_id=class_id,
        ))

    if not class_id:
        issues.append(ValidationIssue("error", "missing_class_id", "Agent Class id is required", path=source))
    elif not CLASS_ID_RE.match(class_id):
        issues.append(ValidationIssue(
            "error",
            "invalid_class_id",
            "Agent Class id must be lowercase kebab-case alphanumerics",
            path=source,
            class_id=class_id,
        ))
    version = str(normalized.get("version", "") or "").strip()
    if not version:
        issues.append(ValidationIssue(
            "error", "missing_class_version", "Agent Class version is required", path=source, class_id=class_id
        ))
    elif not CLASS_VERSION_RE.match(version):
        issues.append(ValidationIssue(
            "error",
            "invalid_class_version",
            "Agent Class version must be a safe non-empty token",
            path=source,
            class_id=class_id,
        ))
    display_name = normalized.get("display_name", "")
    if not isinstance(display_name, str):
        issues.append(ValidationIssue(
            "error",
            "display_name_not_string",
            "display_name must be a string",
            path=source,
            class_id=class_id,
        ))
    else:
        display_name_text = display_name.strip()
        if not display_name_text:
            issues.append(ValidationIssue(
                "error",
                "missing_display_name",
                "Agent Class display_name is required",
                path=source,
                class_id=class_id,
            ))
        elif len(display_name_text) > MAX_DISPLAY_NAME_LEN or "\n" in display_name_text or "\r" in display_name_text:
            issues.append(ValidationIssue(
                "error",
                "invalid_display_name",
                f"display_name must be one line and at most {MAX_DISPLAY_NAME_LEN} characters",
                path=source,
                class_id=class_id,
            ))
    if "description" in normalized:
        description = normalized.get("description", "")
        if not isinstance(description, str):
            issues.append(ValidationIssue(
                "error", "description_not_string", "description must be a string", path=source, class_id=class_id
            ))
        elif len(description) > MAX_DESCRIPTION_LEN:
            issues.append(ValidationIssue(
                "error",
                "description_too_long",
                f"description must be at most {MAX_DESCRIPTION_LEN} characters",
                path=source,
                class_id=class_id,
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
            class_id=class_id,
        ))
    if base_kind not in BASE_KINDS:
        issues.append(ValidationIssue(
            "error",
            "invalid_base_kind",
            f"base_kind/runtime.base_kind must be one of {', '.join(sorted(BASE_KINDS))}",
            path=source,
            class_id=class_id,
        ))
    lifecycle = str(normalized.get("lifecycle", "stable") or "stable").strip()
    if lifecycle not in ALLOWED_LIFECYCLES:
        issues.append(ValidationIssue(
            "error",
            "invalid_lifecycle",
            "Agent Class lifecycle must be stable or draft",
            path=source,
            class_id=class_id,
        ))

    for mapping_key in ("identity", "runtime", "metadata", "draft", "acl", "operator_summary"):
        if mapping_key in raw_data and not isinstance(raw_data.get(mapping_key), dict):
            issues.append(ValidationIssue(
                "error",
                f"{mapping_key}_not_mapping",
                f"{mapping_key} must be a mapping",
                path=source,
                class_id=class_id,
            ))

    legacy_authority_fields = sorted(
        key for key in (
            "policy",
            "capabilities",
            "capability_buckets",
            "restriction_buckets",
        )
        if key in raw_data
    )
    if legacy_authority_fields:
        issues.append(ValidationIssue(
            "error",
            "legacy_agent_class_authority_fields",
            "schema v5 uses only acl.mode + acl.rules for authority; remove: "
            + ", ".join(legacy_authority_fields),
            path=source,
            class_id=class_id,
        ))
    if "warnings" in raw_data and not isinstance(raw_data.get("warnings"), list):
        issues.append(ValidationIssue(
            "error",
            "warnings_not_list",
            "warnings must be a list of strings",
            path=source,
            class_id=class_id,
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
                class_id=class_id,
            ))
        for bool_key in (CUSTOM_CLASS_ARCHIVED_KEY, "disabled"):
            if bool_key in metadata and not isinstance(metadata.get(bool_key), bool):
                issues.append(ValidationIssue(
                    "error",
                    "metadata_lifecycle_flag_not_bool",
                    f"metadata.{bool_key} must be a boolean when present",
                    path=source,
                    class_id=class_id,
                ))

    prompt_value = raw_data.get("prompt", {})
    if "prompt" in raw_data and not isinstance(prompt_value, dict):
        issues.append(ValidationIssue(
            "error", "prompt_not_mapping", "prompt must be a mapping with identity, job, boot_checklist, operating_guidelines, and/or tool_guidance", path=source, class_id=class_id
        ))
    if isinstance(prompt_value, dict):
        unknown_prompt_keys = sorted(set(prompt_value) - PROMPT_ALLOWED_KEYS)
        if unknown_prompt_keys:
            issues.append(ValidationIssue(
                "error",
                "unknown_prompt_fields",
                "unknown prompt fields: " + ", ".join(unknown_prompt_keys),
                path=source,
                class_id=class_id,
            ))
        for key in PROMPT_TEXT_KEYS:
            if key in prompt_value and not isinstance(prompt_value.get(key), str):
                issues.append(ValidationIssue(
                    "error",
                    "prompt_text_field_not_string",
                    f"prompt.{key} must be a string",
                    path=source,
                    class_id=class_id,
                ))
        for key in PROMPT_LIST_KEYS:
            if key in prompt_value and not isinstance(prompt_value.get(key), list):
                issues.append(ValidationIssue(
                    "error",
                    "prompt_list_field_not_list",
                    f"prompt.{key} must be a list of strings",
                    path=source,
                    class_id=class_id,
                ))
            _validate_string_list_field(prompt_value.get(key), f"prompt.{key}", issues, source=source, class_id=class_id)
        if PROMPT_TOOL_GUIDANCE_KEY in prompt_value and not isinstance(prompt_value.get(PROMPT_TOOL_GUIDANCE_KEY), list):
            issues.append(ValidationIssue(
                "error",
                "prompt_tool_guidance_not_list",
                "prompt.tool_guidance must be a list of mappings",
                path=source,
                class_id=class_id,
            ))
        elif isinstance(prompt_value.get(PROMPT_TOOL_GUIDANCE_KEY), list):
            for idx, item in enumerate(prompt_value.get(PROMPT_TOOL_GUIDANCE_KEY) or []):
                if not isinstance(item, dict):
                    issues.append(ValidationIssue(
                        "error",
                        "prompt_tool_guidance_item_not_mapping",
                        f"prompt.tool_guidance[{idx}] must be a mapping",
                        path=source,
                        class_id=class_id,
                    ))
                    continue
                unknown = sorted(set(item) - {"when_capability", "text"})
                if unknown:
                    issues.append(ValidationIssue(
                        "error",
                        "unknown_prompt_tool_guidance_fields",
                        f"unknown prompt.tool_guidance[{idx}] fields: " + ", ".join(unknown),
                        path=source,
                        class_id=class_id,
                    ))
                if not isinstance(item.get("text", ""), str) or not str(item.get("text", "") or "").strip():
                    issues.append(ValidationIssue(
                        "error",
                        "prompt_tool_guidance_missing_text",
                        f"prompt.tool_guidance[{idx}].text must be a non-empty string",
                        path=source,
                        class_id=class_id,
                    ))
                selector = item.get("when_capability")
                if not isinstance(selector, str) or not str(selector or "").strip():
                    issues.append(ValidationIssue(
                        "error",
                        "prompt_tool_guidance_capability_required",
                        f"prompt.tool_guidance[{idx}].when_capability must be a non-empty string",
                        path=source,
                        class_id=class_id,
                    ))
                elif not all(
                    capability_id in CAPABILITY_CATALOG
                    for capability_id in canonical_capability_ids(
                        str(selector).strip(),
                        base_kind=str(
                            normalized.get("base_kind", "") or ""
                        ).strip(),
                    )
                ):
                    issues.append(ValidationIssue(
                        "error",
                        "unknown_prompt_tool_guidance_capability",
                        f"prompt.tool_guidance[{idx}] references unknown capability {str(selector).strip()}",
                        path=source,
                        class_id=class_id,
                    ))
    prompt_text = _prompt_plain_text(normalized.get("prompt", {}))
    if len(prompt_text) > MAX_PROMPT_LEN:
        issues.append(ValidationIssue(
            "error",
            "prompt_too_long",
            f"prompt text must be at most {MAX_PROMPT_LEN} characters",
            path=source,
            class_id=class_id,
        ))

    acl_data = raw_data.get("acl")
    try:
        compile_agent_class_acl(
            base_kind=base_kind,
            acl=acl_data,
            capabilities=CAPABILITY_CATALOG,
        )
    except AuthorityValidationError as exc:
        issues.append(ValidationIssue(
            "error",
            "invalid_capability_acl",
            str(exc),
            path=source,
            class_id=class_id,
        ))

    expected_kind = BUILTIN_CLASS_BASE_KIND.get(class_id)
    if expected_kind and base_kind and base_kind != expected_kind:
        issues.append(ValidationIssue(
            "error",
            "class_base_kind_mismatch",
            f"Agent Class {class_id} must use base_kind={expected_kind}, got {base_kind}",
            path=source,
            class_id=class_id,
        ))
    draft_data = normalized.get("draft") if isinstance(normalized.get("draft"), dict) else {}
    if lifecycle == "draft":
        if draft_data.get("scratch_only") is not True:
            issues.append(ValidationIssue(
                "error",
                "invalid_draft_metadata",
                "draft Agent Classes must set draft.scratch_only: true",
                path=source,
                class_id=class_id,
            ))
        if draft_data.get("approved_for_live_dogfood", False) is not False:
            issues.append(ValidationIssue(
                "error",
                "invalid_draft_metadata",
                "draft Agent Classes must not claim live dogfood approval in Wave 7",
                path=source,
                class_id=class_id,
            ))
    elif draft_data:
        issues.append(ValidationIssue(
            "error",
            "invalid_draft_metadata",
            "stable Agent Classes must not carry draft metadata",
            path=source,
            class_id=class_id,
        ))

    definition = AgentClassDefinition.from_dict(normalized, source=source, builtin=builtin)
    if any(issue.severity == "error" for issue in issues):
        return None, issues
    return definition, issues


def load_agent_classes(base_dir: str = "") -> tuple[list[AgentClassDefinition], list[ValidationIssue]]:
    classes: list[AgentClassDefinition] = []
    issues: list[ValidationIssue] = []
    seen: dict[str, AgentClassDefinition] = {}
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
                    class_id=definition.id,
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


def _class_status_from_preview(class_preview: dict[str, Any]) -> str:
    if agent_class_is_archived(class_preview):
        return "archived"
    lifecycle = str(class_preview.get("lifecycle", "") or "").strip().lower()
    if lifecycle and lifecycle != "stable":
        return lifecycle
    effective = class_preview.get("effective_authority")
    if isinstance(effective, dict) and isinstance(effective.get("capabilities"), dict):
        base_kind = str(
            effective.get("base_kind", class_preview.get("base_kind", "")) or ""
        ).strip()
        expected = {
            capability_id: (
                definition.maximum_scope_for(base_kind)
                if definition.scoped
                else None
            )
            for capability_id, definition in CAPABILITY_CATALOG.items()
            if definition.available_to(base_kind)
        }
        actual = dict(effective.get("capabilities") or {})
        return "full" if actual == expected else "restricted"
    return "full"


def class_warnings_for_preview(class_preview: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    class_id = str(class_preview.get("id", "") or "").strip()
    lifecycle = str(class_preview.get("lifecycle", "") or "").strip().lower()
    status = _class_status_from_preview(class_preview)
    if agent_class_is_archived(class_preview):
        warnings.append(
            f"{class_id or 'Agent Class'} is archived/disabled and cannot be assigned or launched until re-enabled."
        )
    if lifecycle and lifecycle != "stable":
        warnings.append(
            f"{class_id or 'Agent Class'} is lifecycle={lifecycle}; use only for scratch/preview unless explicitly approved."
        )
    if status in {"draft", "restricted"} or lifecycle == "draft":
        warnings.append(EXTERNAL_CONNECTOR_DRAFT_WARNING)
    return warnings


def enriched_agent_class_preview(definition: AgentClassDefinition | dict[str, Any], *, base_dir: str = "") -> dict[str, Any]:
    if isinstance(definition, dict):
        definition = AgentClassDefinition.from_dict(definition)
    preview = definition.as_preview_dict()
    preview["metadata"] = dict(definition.metadata or {})
    preview["draft"] = dict(definition.draft or {})
    preview["prompt"] = _normalized_prompt_mapping(definition.prompt)
    preview["acl"] = compact_agent_class_acl_preview(definition)
    preview["authority_summary"] = agent_class_authority_summary(definition)
    preview["effective_authority"] = effective_authority_snapshot_for_class(
        definition
    )
    preview["class_warnings"] = list(definition.warnings or [])
    preview["purpose"] = definition.description
    preview["primary_display_name"] = primary_identity_label_for_class(definition)
    preview["primary_identity_label"] = primary_identity_label_for_class(definition)
    preview["secondary_base_kind_label"] = secondary_base_kind_label_for_class(definition)
    preview["secondary_base_kind_metadata"] = agent_class_runtime_preview(definition)
    preview["status"] = _class_status_from_preview(preview)
    warnings = class_warnings_for_preview(preview)
    for warning in definition.warnings or []:
        text = str(warning or "").strip()
        if text and text not in warnings:
            warnings.append(text)
    preview["warnings"] = warnings
    preview["external_connector_caveat"] = EXTERNAL_CONNECTOR_CAVEAT
    preview["runtime_enforcement"] = "launch_frozen_effective_authority"
    prompt = _normalized_prompt_mapping(definition.prompt)
    prompt_text = _prompt_plain_text(prompt)
    preview["prompt_summary"] = {
        "has_prompt": bool(prompt_text.strip()),
        "char_count": len(prompt_text),
        "preview": prompt_text.strip()[:240],
    }
    preview["restrictions"] = [
        "The frozen Agent Class effective-authority snapshot is the Torque MCP enforcement source.",
        "Agent Class definitions do not mutate running sessions; changes apply only at launch/relaunch boundaries.",
        "External connector governance and arbitrary runtime kinds are outside Agent Class authority.",
    ]
    preview["apply_state"] = {
        "mutates_running_sessions": False,
        "applies_at": "next_launch_or_relaunch",
        "relaunch_required_after_assignment": True,
    }
    preview["authoring_contract"] = agent_class_authoring_contract(base_kind=definition.base_kind)
    preview["launchable"] = not agent_class_is_archived(preview)
    return preview


def _json_for_hash(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def snapshot_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(_json_for_hash(data).encode("utf-8")).hexdigest()


def freeze_agent_class_snapshot(
    definition: AgentClassDefinition,
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
        "purpose": definition.description,
        "lifecycle": definition.lifecycle,
        "builtin": bool(definition.builtin),
        "status": str(full_preview.get("status", "") or "full"),
        "identity": dict(definition.identity or {}),
        "runtime": agent_class_runtime_preview(definition),
        "operator_summary": dict(definition.operator_summary or {}),
        "acl": compact_agent_class_acl_preview(definition),
        "authority_summary": agent_class_authority_summary(definition),
        "effective_authority": effective_authority_snapshot_for_class(
            definition
        ),
        "prompt": _normalized_prompt_mapping(definition.prompt),
        "metadata": dict(definition.metadata or {}),
        "draft": dict(definition.draft or {}),
        "warnings": warnings[:12],
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
        "runtime_enforcement": "launch_frozen_effective_authority",
        "apply_state": {
            "mutates_running_sessions": False,
            "applies_at": "next_launch_or_relaunch",
        },
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
        "primary_identity_label": str(snapshot.get("primary_identity_label", snapshot.get("display_name", "")) or ""),
        "secondary_base_kind_label": str(snapshot.get("secondary_base_kind_label", "") or ""),
        "status": str(snapshot.get("status", "") or ""),
        "lifecycle": str(snapshot.get("lifecycle", "") or ""),
        "effective_authority": dict(snapshot.get("effective_authority") or {}),
        "snapshot_hash": str(snapshot.get("snapshot_hash", "") or ""),
        "warnings": list(snapshot.get("warnings", []) or [])[:6],
    }


def agent_class_context_for_cell(cell: Any) -> dict[str, Any]:
    snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return {}
    return {
        "id": str(snapshot.get("id", "") or ""),
        "version": str(snapshot.get("version", "") or ""),
        "base_kind": str(snapshot.get("base_kind", "") or ""),
        "display_name": str(snapshot.get("display_name", "") or ""),
        "primary_identity_label": str(snapshot.get("primary_identity_label", snapshot.get("display_name", "")) or ""),
        "secondary_base_kind_label": str(snapshot.get("secondary_base_kind_label", "") or ""),
        "lifecycle": str(snapshot.get("lifecycle", "") or ""),
        "status": str(snapshot.get("status", "") or ""),
        "acl": dict(snapshot.get("acl") or {}),
        "authority_summary": dict(snapshot.get("authority_summary") or {}),
        "effective_authority": dict(snapshot.get("effective_authority") or {}),
        "warnings": list(snapshot.get("warnings", []) or [])[:6],
        "external_connector_caveat": str(snapshot.get("external_connector_caveat", "") or ""),
    }


def agent_class_prompt_block_for_cell(cell: Any) -> str:
    snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return ""
    prompt = _normalized_prompt_mapping(snapshot.get("prompt", {}))
    prompt_text = render_agent_class_prompt(prompt, snapshot=snapshot)
    class_id = str(snapshot.get("id", "") or "").strip()
    lifecycle = str(snapshot.get("lifecycle", "") or "").strip()
    status = str(snapshot.get("status", "") or "").strip()
    # Default/full classes intentionally add no prompt text so unassigned base
    # kinds preserve existing behavior by construction.
    if not prompt_text and class_id.startswith("default-") and status == "full" and lifecycle == "stable":
        return ""
    lines = [
        "## Agent Class",
        f"Class: {class_id}@{snapshot.get('version', '')} ({snapshot.get('primary_identity_label', snapshot.get('display_name', '')) or class_id})",
        f"Lifecycle/status: {lifecycle or '-'} / {status or '-'}",
        f"Base runtime: {snapshot.get('secondary_base_kind_label', snapshot.get('base_kind', '')) or '-'}",
        "The frozen Agent Class ACL controls the Torque MCP tools and resource scopes available in this session.",
    ]
    if prompt_text:
        lines.extend(["", prompt_text])
    effective = snapshot.get("effective_authority")
    capabilities = (
        effective.get("capabilities")
        if isinstance(effective, dict)
        and isinstance(effective.get("capabilities"), dict)
        else {}
    )
    acl = snapshot.get("acl") if isinstance(snapshot.get("acl"), dict) else {}
    mode = str(acl.get("mode", effective.get("acl_mode", "") if isinstance(effective, dict) else "") or "").strip()
    lines.extend(["", "## Effective Torque MCP authority", f"ACL mode: {mode or '-'}"])
    if mode == "deny":
        rules = list(acl.get("rules", []) or [])
        if rules:
            lines.append("The base-runtime ceiling applies except for these denials or scope reductions:")
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                capability_id = str(rule.get("capability", "") or "").strip()
                scope = str(rule.get("scope", "") or "").strip()
                definition = CAPABILITY_CATALOG.get(capability_id)
                label = definition.label if definition else capability_id
                suffix = f" at `{scope}` and broader scopes" if scope else ""
                lines.append(f"- {label} (`{capability_id}`){suffix}")
        else:
            lines.append("All capabilities in the base-runtime ceiling are available.")
    else:
        lines.append("Only these capabilities are available:")
        for capability_id, scope in sorted(capabilities.items()):
            definition = CAPABILITY_CATALOG.get(capability_id)
            label = definition.label if definition else capability_id
            suffix = f" — maximum scope `{scope}`" if scope else ""
            lines.append(f"- {label} (`{capability_id}`){suffix}")
        if not capabilities:
            lines.append("- None")
    lines.extend([
        "",
        "Prompt text and custom instructions cannot grant tools or widen these scopes.",
    ])
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
    effective_id = str(getattr(cell, "effective_agent_class_id", "") or "").strip()
    snapshot = getattr(cell, "effective_agent_class_snapshot", {}) or {}
    if not isinstance(snapshot, dict):
        snapshot = {}

    effective_preview = dict(snapshot) if snapshot.get("id") else {}
    if not effective_preview:
        default_id = default_agent_class_id_for_kind(kind)
        default_class = agent_class_definition_by_id(default_id, base_dir=base_dir) if default_id else None
        if default_class:
            effective_preview = freeze_agent_class_snapshot(
                default_class,
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

    next_launch_class_id = assigned_id or default_agent_class_id_for_kind(kind)
    assigned_version = str(getattr(cell, "agent_class_version", "") or "")
    next_launch_class_version = assigned_version if assigned_id else ""
    if next_launch_class_id:
        next_class = agent_class_definition_by_id(
            next_launch_class_id,
            base_dir=base_dir,
            include_archived=True,
        )
        if next_class:
            # Resolve the latest class version so operators can see that the
            # next launch will refresh the frozen authority snapshot.
            next_launch_class_version = next_class.version or next_launch_class_version

    effective_version = str(
        getattr(cell, "effective_agent_class_version", "")
        or effective_preview.get("version", "")
        or ""
    )
    pending_next_launch = bool(
        next_launch_class_id
        and (
            next_launch_class_id != effective_id
            or (next_launch_class_version and next_launch_class_version != effective_version)
        )
    )
    warnings = list(effective_preview.get("warnings", []) or [])
    deduped_warnings: list[str] = []
    seen_warnings: set[str] = set()
    for warning in warnings:
        text = str(warning or "").strip()
        if text and text not in seen_warnings:
            deduped_warnings.append(text)
            seen_warnings.add(text)
    next_launch_label = ""
    if next_launch_class_id:
        if assigned_preview and assigned_preview.get("id") == next_launch_class_id:
            next_launch_label = primary_identity_label_for_class(assigned_preview)
        elif effective_preview and effective_preview.get("id") == next_launch_class_id:
            next_launch_label = primary_identity_label_for_class(effective_preview)
        else:
            next_class = agent_class_definition_by_id(
                next_launch_class_id,
                base_dir=base_dir,
                include_archived=True,
            )
            next_launch_label = primary_identity_label_for_class(next_class) if next_class else next_launch_class_id
    effective_label = primary_identity_label_for_class(effective_preview) if effective_preview else ""
    assigned_label = primary_identity_label_for_class(assigned_preview) if assigned_preview else ""
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
        "pending_next_launch": pending_next_launch,
        "status": str(effective_preview.get("status", "") or "full"),
        "next_launch_class_disabled": bool(agent_class_is_archived(assigned_preview)) if assigned_preview else False,
        "apply_state": {
            "pending_next_launch": pending_next_launch,
            "relaunch_required": pending_next_launch,
            "mutates_running_sessions": False,
            "applies_at": "next_launch_or_relaunch",
            "effective_class_id": effective_id,
            "next_launch_class_id": next_launch_class_id,
        },
        "warnings": deduped_warnings,
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }


def built_in_agent_class_ids() -> list[str]:
    ids: list[str] = []
    for path in _iter_yaml_paths(BUILTIN_CLASS_DIR):
        data, issue = load_class_yaml(path)
        if issue or not isinstance(data, dict):
            continue
        class_id = str(data.get("id", "") or "").strip()
        if class_id:
            ids.append(class_id)
    return sorted(set(ids))


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
    for alias in AUTHORING_DESCRIPTION_ALIASES:
        if alias in data and not str(data.get("description", "") or "").strip():
            data["description"] = data.get(alias)
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
    prompt = _normalized_prompt_mapping(data.get("prompt", {}))
    if prompt:
        out["prompt"] = prompt
    for key in ("identity", "acl", "operator_summary"):
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
    runtime_for_contract = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    contract_base_kind = str(data.get("base_kind", "") or runtime_for_contract.get("base_kind", "") or "").strip()
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
        "authoring_contract": agent_class_authoring_contract(base_kind=contract_base_kind),
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
            class_id=class_id,
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
            class_id=class_id,
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
            class_id=class_id,
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
            class_id=class_id,
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
            class_id=class_id,
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
            class_id=class_id,
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
            class_id=class_id,
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
            class_id=class_id,
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
