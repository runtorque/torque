"""Generic MCP authority registry primitives.

This module is intentionally independent from Agent Classes and Agent
Profiles.  It provides the platform vocabulary used to describe capabilities,
scopes, and MCP tool requirements without knowing any named class.

The first migration slice uses :func:`audit_tool_authority_coverage` against
the existing tool requirement registry.  Later slices will move each
requirement beside its ``ToolDefinition`` and use the same primitives for ACL
evaluation and resource authorization.
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

    ``target_argument`` is empty for projection-only/unscoped requirements.
    Scoped handlers set it (or use a handler-owned resolver in a later
    migration slice) so call-time authorization can compare a concrete target
    with the caller's effective scope.
    """

    capability: str
    target_argument: str = ""
    conditional: bool = False


@dataclass(frozen=True)
class ToolAuthorityDefinition:
    """Internal authority metadata retained beside a public MCP tool spec."""

    name: str
    base_kinds: frozenset[str]
    requirements: tuple[CapabilityRequirement, ...]


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


def merge_tool_authority_requirements(
    *registries: Mapping[str, Iterable[str]],
) -> dict[str, frozenset[str]]:
    """Merge surface-owned tool registries without silent shadowing."""

    merged: dict[str, frozenset[str]] = {}
    duplicates: set[str] = set()
    for registry in registries:
        for raw_name, raw_requirements in registry.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            if name in merged:
                duplicates.add(name)
                continue
            merged[name] = frozenset(
                str(capability or "").strip()
                for capability in (raw_requirements or ())
                if str(capability or "").strip()
            )
    if duplicates:
        raise RuntimeError(
            "MCP tools have authority metadata in multiple surfaces: "
            + ", ".join(sorted(duplicates))
        )
    return merged


def registry_hash(payload: Any) -> str:
    """Return a deterministic versioned SHA-256 registry hash."""

    encoded = json.dumps(
        {"authority_schema_version": AUTHORITY_SCHEMA_VERSION, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
