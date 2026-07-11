"""Generic MCP authority registry primitives.

This module is intentionally independent from Agent Classes and Agent
Profiles.  It provides the platform vocabulary used to describe capabilities,
scopes, and MCP tool requirements without knowing any named class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping


AUTHORITY_SCHEMA_VERSION = 1
SCOPE_ORDER = ("self", "children", "group", "global")
SCOPE_RANK = {scope: index for index, scope in enumerate(SCOPE_ORDER)}
RISK_LEVELS = frozenset({"normal", "high", "critical"})


@dataclass(frozen=True)
class CapabilityDefinition:
    """One stable semantic operation selectable by an Agent Class ACL."""

    id: str
    label: str
    description: str
    risk: str = "normal"
    base_kinds: frozenset[str] = frozenset()
    scopes: tuple[str, ...] = ()
    ceilings: Mapping[str, str] = field(default_factory=dict)

    @property
    def scoped(self) -> bool:
        return bool(self.scopes)

    def maximum_scope_for(self, base_kind: str) -> str:
        return str(self.ceilings.get(str(base_kind or "").strip(), "") or "")

    def available_to(self, base_kind: str) -> bool:
        base_kind = str(base_kind or "").strip()
        if self.scoped:
            return bool(self.maximum_scope_for(base_kind))
        return base_kind in self.base_kinds


@dataclass(frozen=True)
class EffectiveAuthority:
    """Frozen capability result of evaluating one Agent Class ACL."""

    base_kind: str
    mode: str
    # ``None`` is an unscoped capability; strings are maximum scopes.
    capabilities: Mapping[str, str | None]

    def has(self, capability: str) -> bool:
        return str(capability or "").strip() in self.capabilities

    def allows(self, capability: str, *, scope: str = "") -> bool:
        capability = str(capability or "").strip()
        if capability not in self.capabilities:
            return False
        maximum = self.capabilities[capability]
        if maximum is None:
            return not str(scope or "").strip()
        return scope_includes(maximum, scope)

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORITY_SCHEMA_VERSION,
            "base_kind": self.base_kind,
            "acl_mode": self.mode,
            "capabilities": {
                capability: scope
                for capability, scope in sorted(self.capabilities.items())
            },
        }


class AuthorityValidationError(ValueError):
    """Raised when an ACL cannot be evaluated safely."""


def effective_authority_from_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    capabilities: Mapping[str, CapabilityDefinition],
) -> EffectiveAuthority | None:
    """Rehydrate a validated frozen effective-authority snapshot."""

    if not isinstance(snapshot, Mapping) or not snapshot:
        return None
    base_kind = str(snapshot.get("base_kind", "") or "").strip()
    mode = str(snapshot.get("acl_mode", "") or "").strip().lower()
    raw_capabilities = snapshot.get("capabilities")
    if mode not in {"allow", "deny"} or not base_kind:
        raise AuthorityValidationError("invalid effective authority header")
    if not isinstance(raw_capabilities, Mapping):
        raise AuthorityValidationError(
            "effective authority capabilities must be a mapping"
        )
    effective: dict[str, str | None] = {}
    for raw_id, raw_scope in raw_capabilities.items():
        capability_id = str(raw_id or "").strip()
        definition = capabilities.get(capability_id)
        if not definition or not definition.available_to(base_kind):
            raise AuthorityValidationError(
                f"invalid effective capability {capability_id} for {base_kind}"
            )
        if definition.scoped:
            scope = normalize_scope(raw_scope)
            ceiling = definition.maximum_scope_for(base_kind)
            if (
                not scope
                or scope not in definition.scopes
                or not scope_includes(ceiling, scope)
            ):
                raise AuthorityValidationError(
                    f"invalid effective scope for {capability_id}"
                )
            effective[capability_id] = scope
        else:
            if raw_scope is not None:
                raise AuthorityValidationError(
                    f"unscoped capability {capability_id} must use null scope"
                )
            effective[capability_id] = None
    return EffectiveAuthority(
        base_kind=base_kind,
        mode=mode,
        capabilities=dict(sorted(effective.items())),
    )


@dataclass(frozen=True)
class CapabilityRequirement:
    """A capability required by one MCP tool.

    ``minimum_scope`` is the narrowest grant required to project the tool.
    ``target_argument`` is empty when the tool has no directly addressable
    target. Scoped handlers set both it and ``target_kind`` so call-time
    authorization can compare a concrete resource with the caller's effective
    scope. A capability may appear more than once when a tool accepts multiple
    independent targets.
    """

    capability: str
    minimum_scope: str = ""
    target_argument: str = ""
    target_kind: str = ""
    result_kind: str = ""
    result_paths: tuple[str, ...] = ()
    conditional: bool = False


@dataclass(frozen=True)
class ToolAuthorityDefinition:
    """Internal authority metadata retained beside a public MCP tool spec."""

    name: str
    base_kinds: frozenset[str]
    requirements: tuple[CapabilityRequirement, ...]


def authority_definition_from_tool_spec(
    tool_spec: Mapping[str, Any],
    *,
    base_kinds: Iterable[str],
    capabilities: Mapping[str, CapabilityDefinition],
) -> ToolAuthorityDefinition:
    """Parse and validate authority metadata colocated with one MCP tool.

    ``authority`` is Torque-internal registration metadata and must be removed
    before a tool schema is sent over MCP. Keeping it on the registered spec
    makes the schema and its authorization contract one reviewable unit.
    """

    name = str(tool_spec.get("name", "") or "").strip()
    if not name:
        raise AuthorityValidationError("MCP tool requires a name")
    raw_authority = tool_spec.get("authority")
    if not isinstance(raw_authority, Mapping):
        raise AuthorityValidationError(
            f"MCP tool {name} requires authority metadata"
        )
    unknown_authority_fields = sorted(
        set(raw_authority) - {"requirements"}
    )
    if unknown_authority_fields:
        raise AuthorityValidationError(
            f"MCP tool {name} has unknown authority fields: "
            + ", ".join(unknown_authority_fields)
        )
    raw_requirements = raw_authority.get("requirements")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise AuthorityValidationError(
            f"MCP tool {name} requires at least one capability"
        )

    properties = tool_spec.get("inputSchema", {}).get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    requirements: list[CapabilityRequirement] = []
    normalized_base_kinds = frozenset(
        str(kind or "").strip()
        for kind in base_kinds
        if str(kind or "").strip()
    )
    if not normalized_base_kinds:
        raise AuthorityValidationError(
            f"MCP tool {name} requires at least one base kind"
        )
    seen: set[tuple[str, str]] = set()
    for index, raw_requirement in enumerate(raw_requirements):
        if not isinstance(raw_requirement, Mapping):
            raise AuthorityValidationError(
                f"MCP tool {name} authority requirement {index} must be a mapping"
            )
        unknown_fields = sorted(
            set(raw_requirement)
            - {
                "capability",
                "minimum_scope",
                "target_argument",
                "target_kind",
                "result_kind",
                "result_paths",
                "conditional",
            }
        )
        if unknown_fields:
            raise AuthorityValidationError(
                f"MCP tool {name} authority requirement {index} has unknown fields: "
                + ", ".join(unknown_fields)
            )
        capability = str(raw_requirement.get("capability", "") or "").strip()
        definition = capabilities.get(capability)
        if not definition:
            raise AuthorityValidationError(
                f"MCP tool {name} references unknown capability {capability}"
            )
        incompatible_kinds = sorted(
            kind
            for kind in normalized_base_kinds
            if not definition.available_to(kind)
        )
        if incompatible_kinds:
            raise AuthorityValidationError(
                f"MCP tool {name} capability {capability} is unavailable to: "
                + ", ".join(incompatible_kinds)
            )
        minimum_scope = normalize_scope(raw_requirement.get("minimum_scope"))
        target_argument = str(
            raw_requirement.get("target_argument", "") or ""
        ).strip()
        target_kind = str(raw_requirement.get("target_kind", "") or "").strip()
        result_kind = str(raw_requirement.get("result_kind", "") or "").strip()
        raw_result_paths = raw_requirement.get("result_paths", [])
        if not isinstance(raw_result_paths, list) or any(
            not str(path or "").strip() for path in raw_result_paths
        ):
            raise AuthorityValidationError(
                f"MCP tool {name} result_paths must be a list of paths"
            )
        result_paths = tuple(
            str(path or "").strip() for path in raw_result_paths
        )
        conditional = bool(raw_requirement.get("conditional", False))
        if definition.scoped:
            if not minimum_scope or minimum_scope not in definition.scopes:
                raise AuthorityValidationError(
                    f"MCP tool {name} requires a valid minimum_scope for {capability}"
                )
            insufficient_kinds = sorted(
                kind
                for kind in normalized_base_kinds
                if not scope_includes(
                    definition.maximum_scope_for(kind),
                    minimum_scope,
                )
            )
            if insufficient_kinds:
                raise AuthorityValidationError(
                    f"MCP tool {name} minimum_scope {minimum_scope} exceeds "
                    f"the {capability} ceiling for: "
                    + ", ".join(insufficient_kinds)
                )
        elif minimum_scope:
            raise AuthorityValidationError(
                f"MCP tool {name} uses minimum_scope for unscoped {capability}"
            )
        if target_argument:
            if target_argument not in properties:
                raise AuthorityValidationError(
                    f"MCP tool {name} target argument {target_argument} is not in its schema"
                )
            if target_kind not in {"agent", "task"}:
                raise AuthorityValidationError(
                    f"MCP tool {name} target {target_argument} requires target_kind"
                )
            if not definition.scoped:
                raise AuthorityValidationError(
                    f"MCP tool {name} cannot target unscoped {capability}"
                )
        elif target_kind:
            raise AuthorityValidationError(
                f"MCP tool {name} target_kind requires target_argument"
            )
        if result_paths and not definition.scoped:
            raise AuthorityValidationError(
                f"MCP tool {name} cannot filter results for unscoped {capability}"
            )
        if result_paths and result_kind not in {"agent", "task"}:
            raise AuthorityValidationError(
                f"MCP tool {name} result_paths require result_kind"
            )
        if result_kind and not result_paths:
            raise AuthorityValidationError(
                f"MCP tool {name} result_kind requires result_paths"
            )
        identity = (capability, target_argument)
        if identity in seen:
            raise AuthorityValidationError(
                f"MCP tool {name} repeats authority requirement {capability}"
            )
        seen.add(identity)
        requirements.append(CapabilityRequirement(
            capability=capability,
            minimum_scope=minimum_scope,
            target_argument=target_argument,
            target_kind=target_kind,
            result_kind=result_kind,
            result_paths=result_paths,
            conditional=conditional,
        ))

    return ToolAuthorityDefinition(
        name=name,
        base_kinds=normalized_base_kinds,
        requirements=tuple(requirements),
    )


def authority_definitions_from_tool_specs(
    tool_specs: Iterable[Mapping[str, Any]],
    *,
    base_kinds: Iterable[str],
    capabilities: Mapping[str, CapabilityDefinition],
) -> tuple[ToolAuthorityDefinition, ...]:
    """Build validated first-class descriptors for a registered surface."""

    return tuple(
        authority_definition_from_tool_spec(
            tool_spec,
            base_kinds=base_kinds,
            capabilities=capabilities,
        )
        for tool_spec in tool_specs
    )


def authority_definition_map(
    definitions: Iterable[ToolAuthorityDefinition],
) -> dict[str, ToolAuthorityDefinition]:
    """Index descriptors without permitting silent duplicate tool names."""

    indexed: dict[str, ToolAuthorityDefinition] = {}
    duplicates: set[str] = set()
    for definition in definitions:
        if definition.name in indexed:
            duplicates.add(definition.name)
        else:
            indexed[definition.name] = definition
    if duplicates:
        raise RuntimeError(
            "duplicate MCP authority definitions: " + ", ".join(sorted(duplicates))
        )
    return indexed


@dataclass(frozen=True)
class AuthorityCoverageReport:
    """Deterministic coverage audit for registered MCP tools."""

    registered_tools: tuple[str, ...]
    authority_tools: tuple[str, ...]
    unmapped_tools: tuple[str, ...] = ()
    stale_authority_tools: tuple[str, ...] = ()
    duplicate_registered_tools: tuple[str, ...] = ()
    empty_requirements: tuple[str, ...] = ()
    unknown_capabilities: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any((
            self.unmapped_tools,
            self.stale_authority_tools,
            self.duplicate_registered_tools,
            self.empty_requirements,
            self.unknown_capabilities,
        ))

    def errors(self) -> list[str]:
        errors: list[str] = []
        if self.unmapped_tools:
            errors.append(
                "registered MCP tools missing authority metadata: "
                + ", ".join(self.unmapped_tools)
            )
        if self.stale_authority_tools:
            errors.append(
                "authority metadata references unregistered MCP tools: "
                + ", ".join(self.stale_authority_tools)
            )
        if self.duplicate_registered_tools:
            errors.append(
                "duplicate registered MCP tool names: "
                + ", ".join(self.duplicate_registered_tools)
            )
        if self.empty_requirements:
            errors.append(
                "MCP tools have empty authority requirements: "
                + ", ".join(self.empty_requirements)
            )
        if self.unknown_capabilities:
            errors.append(
                "MCP authority metadata references unknown capabilities: "
                + ", ".join(self.unknown_capabilities)
            )
        return errors

    def require_valid(self) -> None:
        errors = self.errors()
        if errors:
            raise RuntimeError("; ".join(errors))


def normalize_scope(value: Any) -> str:
    """Return a valid normalized scope or an empty string."""

    scope = str(value or "").strip().lower()
    return scope if scope in SCOPE_RANK else ""


def scope_includes(maximum_scope: str, requested_scope: str) -> bool:
    """Whether ``maximum_scope`` contains ``requested_scope``."""

    maximum = normalize_scope(maximum_scope)
    requested = normalize_scope(requested_scope)
    if not maximum or not requested:
        return False
    return SCOPE_RANK[requested] <= SCOPE_RANK[maximum]


def next_narrower_scope(scope: str, *, supported: Iterable[str] = SCOPE_ORDER) -> str:
    """Return the broadest supported scope strictly narrower than ``scope``."""

    normalized = normalize_scope(scope)
    supported_set = {normalize_scope(item) for item in supported}
    supported_set.discard("")
    if not normalized:
        return ""
    narrower = [
        item for item in SCOPE_ORDER
        if item in supported_set and SCOPE_RANK[item] < SCOPE_RANK[normalized]
    ]
    return narrower[-1] if narrower else ""


def evaluate_capability_acl(
    *,
    base_kind: str,
    mode: str,
    rules: Iterable[Mapping[str, Any]],
    capabilities: Mapping[str, CapabilityDefinition],
) -> EffectiveAuthority:
    """Evaluate canonical ``acl.mode + acl.rules`` capability authority.

    Allow mode starts empty. Deny mode starts from the complete capability
    ceiling for ``base_kind``. A scoped deny removes the named scope and every
    broader scope by reducing the effective maximum to the next narrower
    supported scope.
    """

    base_kind = str(base_kind or "").strip()
    mode = str(mode or "").strip().lower()
    if mode not in {"allow", "deny"}:
        raise AuthorityValidationError("ACL mode must be allow or deny")

    effective: dict[str, str | None] = {}
    if mode == "deny":
        for capability_id, definition in capabilities.items():
            if not definition.available_to(base_kind):
                continue
            effective[capability_id] = (
                definition.maximum_scope_for(base_kind)
                if definition.scoped
                else None
            )

    seen: set[str] = set()
    for index, raw_rule in enumerate(rules or ()):
        if not isinstance(raw_rule, Mapping):
            raise AuthorityValidationError(
                f"ACL rule {index} must be a mapping"
            )
        unknown_fields = sorted(set(raw_rule) - {"capability", "scope"})
        if unknown_fields:
            raise AuthorityValidationError(
                f"ACL rule {index} has unknown fields: "
                + ", ".join(unknown_fields)
            )
        capability_id = str(raw_rule.get("capability", "") or "").strip()
        if not capability_id:
            raise AuthorityValidationError(
                f"ACL rule {index} requires capability"
            )
        if capability_id in seen:
            raise AuthorityValidationError(
                f"duplicate ACL capability rule: {capability_id}"
            )
        seen.add(capability_id)
        definition = capabilities.get(capability_id)
        if not definition:
            raise AuthorityValidationError(
                f"unknown ACL capability: {capability_id}"
            )
        if not definition.available_to(base_kind):
            raise AuthorityValidationError(
                f"capability {capability_id} is outside the {base_kind} ceiling"
            )

        raw_scope_present = "scope" in raw_rule and bool(
            str(raw_rule.get("scope", "") or "").strip()
        )
        scope = normalize_scope(raw_rule.get("scope", ""))
        if definition.scoped:
            if mode == "allow" and not raw_scope_present:
                raise AuthorityValidationError(
                    f"scoped capability {capability_id} requires scope"
                )
            if raw_scope_present and not scope:
                raise AuthorityValidationError(
                    f"invalid scope for {capability_id}"
                )
            if scope and scope not in definition.scopes:
                raise AuthorityValidationError(
                    f"scope {scope} is not supported by {capability_id}"
                )
            ceiling = definition.maximum_scope_for(base_kind)
            if scope and not scope_includes(ceiling, scope):
                raise AuthorityValidationError(
                    f"scope {scope} exceeds the {base_kind} ceiling "
                    f"{ceiling} for {capability_id}"
                )
            if mode == "allow":
                effective[capability_id] = scope
            elif not raw_scope_present:
                effective.pop(capability_id, None)
            else:
                narrower = next_narrower_scope(
                    scope,
                    supported=(
                        supported_scope
                        for supported_scope in definition.scopes
                        if scope_includes(ceiling, supported_scope)
                    ),
                )
                if narrower:
                    effective[capability_id] = narrower
                else:
                    effective.pop(capability_id, None)
        else:
            if raw_scope_present:
                raise AuthorityValidationError(
                    f"unscoped capability {capability_id} does not accept scope"
                )
            if mode == "allow":
                effective[capability_id] = None
            else:
                effective.pop(capability_id, None)

    return EffectiveAuthority(
        base_kind=base_kind,
        mode=mode,
        capabilities=dict(sorted(effective.items())),
    )


def compile_agent_class_acl(
    *,
    base_kind: str,
    acl: Mapping[str, Any],
    capabilities: Mapping[str, CapabilityDefinition],
) -> EffectiveAuthority:
    """Validate and compile the canonical Agent Class ACL mapping."""

    if not isinstance(acl, Mapping):
        raise AuthorityValidationError("acl must be a mapping")
    unknown_fields = sorted(set(acl) - {"mode", "rules"})
    if unknown_fields:
        raise AuthorityValidationError(
            "acl has unknown fields: " + ", ".join(unknown_fields)
        )
    if "mode" not in acl:
        raise AuthorityValidationError("acl.mode is required")
    if "rules" not in acl:
        raise AuthorityValidationError("acl.rules is required")
    rules = acl.get("rules")
    if not isinstance(rules, list):
        raise AuthorityValidationError("acl.rules must be a list")
    return evaluate_capability_acl(
        base_kind=base_kind,
        mode=str(acl.get("mode", "") or ""),
        rules=rules,
        capabilities=capabilities,
    )


def audit_tool_authority_coverage(
    tool_specs: Iterable[Mapping[str, Any]],
    tool_requirements: Mapping[str, Iterable[str]],
    *,
    known_capabilities: Iterable[str] = (),
) -> AuthorityCoverageReport:
    """Audit exact coverage between registered tools and authority metadata.

    During the migration ``tool_requirements`` is the existing centralized
    mapping.  Once metadata is colocated with ``ToolDefinition`` instances,
    callers will pass the derived descriptor mapping instead.  The audit
    contract remains unchanged.
    """

    names: list[str] = []
    for spec in tool_specs:
        name = str(spec.get("name", "") or "").strip()
        if name:
            names.append(name)
    registered = set(names)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    authority_names = {
        str(name or "").strip()
        for name in tool_requirements
        if str(name or "").strip()
    }
    empty_requirements: list[str] = []
    requirement_capabilities: set[str] = set()
    for tool_name, requirements in tool_requirements.items():
        normalized_tool = str(tool_name or "").strip()
        normalized_requirements = {
            str(capability or "").strip()
            for capability in (requirements or ())
            if str(capability or "").strip()
        }
        if normalized_tool and not normalized_requirements:
            empty_requirements.append(normalized_tool)
        requirement_capabilities.update(normalized_requirements)
    known = {
        str(capability or "").strip()
        for capability in known_capabilities
        if str(capability or "").strip()
    }
    unknown = sorted(requirement_capabilities - known) if known else []
    return AuthorityCoverageReport(
        registered_tools=tuple(sorted(registered)),
        authority_tools=tuple(sorted(authority_names)),
        unmapped_tools=tuple(sorted(registered - authority_names)),
        stale_authority_tools=tuple(sorted(authority_names - registered)),
        duplicate_registered_tools=tuple(duplicates),
        empty_requirements=tuple(sorted(empty_requirements)),
        unknown_capabilities=tuple(unknown),
    )


def registry_hash(payload: Any) -> str:
    """Return a deterministic versioned SHA-256 registry hash."""

    encoded = json.dumps(
        {"authority_schema_version": AUTHORITY_SCHEMA_VERSION, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
