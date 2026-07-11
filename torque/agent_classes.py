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
    HIGH_RISK_CAPABILITIES,
    AgentProfileDefinition,
    ValidationIssue,
    enriched_profile_preview,
    load_agent_profiles,
    profile_definition_by_id,
    validate_profile_data,
)
from .capability_catalog import (
    CAPABILITY_CATALOG,
    capability_catalog_for_base_kind,
    legacy_atoms_for_canonical_capabilities,
)
from .mcp_authority import (
    AuthorityValidationError,
    compile_agent_class_acl,
    registry_hash,
)

BUILTIN_CLASS_DIR = Path(__file__).resolve().parent / "builtin_agent_classes"
PROJECT_CLASS_LEAF = "agent_classes"

CLASS_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CLASS_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_LIFECYCLES = {"stable", "draft"}
AGENT_CLASS_SCHEMA_VERSION = 5
DEFAULT_AGENT_CLASS_SCHEMA_VERSION = 5
POLICY_SCHEMA_VERSION = 1
POLICY_COMPILER_VERSION = "agent_class_policy_compiler_v1"
ALLOWED_POLICY_MODES = {"wrap_profile", "compile"}
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
BUILTIN_CLASS_PROFILE_REF: dict[str, tuple[str, str]] = {}
BUILTIN_CLASS_POLICY_MODE: dict[str, str] = {}

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


@dataclass(frozen=True)
class AgentClassCapabilityBucket:
    """Operator-facing class capability bucket.

    Buckets are deliberately coarser than Agent Profile capability atoms.  They
    are the normal Agent Class authoring contract; the generated/internal Agent
    Profile policy is derived from these buckets and exposed only as diagnostics.
    """

    id: str
    label: str
    summary: str
    grants: frozenset[str] = frozenset()
    denies: frozenset[str] = frozenset()
    base_kinds: frozenset[str] = frozenset(BASE_KINDS)
    category: str = "general"
    risk: str = "normal"

    def as_catalog_dict(self, *, base_kind: str = "", restriction: bool = False) -> dict[str, Any]:
        base_kind = str(base_kind or "").strip()
        available = not base_kind or base_kind in self.base_kinds
        data = {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "description": self.summary,
            "category": self.category,
            "risk": self.risk,
            "base_kinds": sorted(self.base_kinds),
            "available": available,
            "unavailable_reason": "" if available else f"Only available to {', '.join(sorted(self.base_kinds))} base classes.",
            "grant_count": len(self.grants),
            "deny_count": len(self.denies),
            "kind": "restriction" if restriction else "capability",
        }
        if self.grants:
            data["advanced_internal_grants"] = sorted(self.grants)
        if self.denies:
            data["advanced_internal_denies"] = sorted(self.denies)
        return data


def _bucket(
    bucket_id: str,
    label: str,
    summary: str,
    grants: list[str] | tuple[str, ...] | set[str] | frozenset[str] = (),
    *,
    denies: list[str] | tuple[str, ...] | set[str] | frozenset[str] = (),
    base_kinds: list[str] | tuple[str, ...] | set[str] | frozenset[str] = BASE_KINDS,
    category: str = "general",
    risk: str = "normal",
) -> AgentClassCapabilityBucket:
    return AgentClassCapabilityBucket(
        id=bucket_id,
        label=label,
        summary=summary,
        grants=frozenset(grants),
        denies=frozenset(denies),
        base_kinds=frozenset(base_kinds),
        category=category,
        risk=risk,
    )


# Operator-language buckets.  IDs are stable API/YAML tokens; labels/summaries
# are what Panelsmith should show.  The internal grants are diagnostics only.
CAPABILITY_BUCKETS: dict[str, AgentClassCapabilityBucket] = {
    bucket.id: bucket for bucket in [
        _bucket(
            "self_context",
            "Self and assigned task context",
            "Read own agent/session context and visible assigned task details.",
            {"observe.self_context", "observe.task_detail"},
            category="read",
        ),
        _bucket(
            "task_reporting",
            "Task reporting and verification",
            "Report progress/completion, record verification, and attach task artifacts.",
            {"task.complete", "task.verify", "task.upload_artifact"},
            base_kinds={"worker", "engineer", "architect"},
            category="task",
        ),
        _bucket(
            "planning_area_reads",
            "Area reads",
            "Read visible Areas and area context.",
            {"planning.area_read"},
            category="planning",
        ),
        _bucket(
            "planning_reads",
            "Planning reads",
            "Read same-group board/task planning summaries, Areas, Initiatives, and Decisions.",
            {"observe.board_summary", "observe.task_detail", "planning.area_read", "planning.initiative_read", "decision.list"},
            base_kinds={"engineer", "architect"},
            category="planning",
        ),
        _bucket(
            "recent_context_reads",
            "Recent context reads",
            "Read recent same-group activity summaries/events where the base kind already permits them.",
            {"observe.events"},
            base_kinds={"engineer", "architect"},
            category="read",
        ),
        _bucket(
            "thinking_workspace",
            "Thinking workspace",
            "Read same-group Scratchpad notes and Mind Maps; create/update only caller-owned Thinking artifacts.",
            {"thinking.read", "thinking.write_own"},
            base_kinds={"architect"},
            category="thinking",
        ),
        _bucket(
            "idea_briefs",
            "Idea Brief proposals",
            "Draft, refine, park, archive, and explicitly propose caller-owned Idea Brief artifacts for product-safe review.",
            {"idea_brief.read", "idea_brief.write_own", "idea_brief.propose"},
            base_kinds={"architect"},
            category="thinking",
        ),
        _bucket(
            "planning_writes",
            "Planning writes",
            "Create/update/archive Areas and Initiatives and link visible decisions where supported.",
            {"planning.area_write", "planning.initiative_write", "decision.link"},
            base_kinds={"architect"},
            category="planning",
            risk="high",
        ),
        _bucket(
            "board_task_reads",
            "Board/task reads",
            "Read board/task detail, events, MCP call telemetry, and board-sync status.",
            {"observe.board_summary", "observe.task_detail", "observe.events", "observe.mcp_calls", "task.board_sync_read"},
            base_kinds={"engineer", "architect"},
            category="task",
        ),
        _bucket(
            "board_task_proposals",
            "Board/task proposals",
            "Create and maintain queued, planning-safe product task proposals without dispatch authority.",
            {"task.create_queued", "task.update_planning_fields", "task.move_planning_safe", "task.board_sync_read"},
            base_kinds={"architect"},
            category="task",
        ),
        _bucket(
            "execution_task_control",
            "Execution task control",
            "Create, update, reassign, move, and dispatch executable Board tasks.",
            {"task.create", "task.update", "task.reassign", "task.move", "task.dispatch", "task.mark_covered"},
            base_kinds={"engineer", "architect"},
            category="task",
            risk="critical",
        ),
        _bucket(
            "proposed_decisions",
            "Proposed decisions",
            "Create/update/link proposed product decisions; does not accept or supersede decisions.",
            {"decision.list", "decision.create_proposed", "decision.update_proposed", "decision.link"},
            base_kinds={"architect"},
            category="decision",
        ),
        _bucket(
            "behavior_overlay_self",
            "Own behavior overlay proposals",
            "Read visible Dynamic Behavior overlay state and propose user-approved changes to this agent's own overlay; does not grant role, Engineer, profile, or class administration.",
            {"behavior_overlay.read", "behavior_overlay.propose_self"},
            base_kinds={"architect"},
            category="behavior_overlay",
        ),
        _bucket(
            "decision_authority",
            "Accepted decision authority",
            "Create, update, and accept product/architecture decisions.",
            {"decision.list", "decision.create", "decision.update", "decision.accept", "decision.link"},
            base_kinds={"architect"},
            category="decision",
            risk="high",
        ),
        _bucket(
            "user_messages",
            "User messages",
            "Ask the user for blocking decisions and send non-blocking user-facing messages.",
            {"comm.user_ask", "comm.user_message"},
            category="communication",
        ),
        _bucket(
            "product_peer_messages",
            "Product peer Architect messages",
            "Coordinate with same-group Architect/product peers through product-scoped peer wrappers.",
            {"comm.peer_architect_list", "comm.peer_architect_message", "comm.product_ack_request"},
            base_kinds={"architect"},
            category="communication",
        ),
        _bucket(
            "peer_architect_messages",
            "Peer Architect messages",
            "List and message same-group Architect peers for coordination and handoff nudges.",
            {"comm.peer_architect_list", "comm.peer_architect_message"},
            base_kinds={"architect"},
            category="communication",
        ),
        _bucket(
            "engineer_worker_messages",
            "Engineer/Worker messages",
            "Message Engineers and Workers through the base-kind communication surface.",
            {"comm.engineer_message", "comm.worker_message"},
            base_kinds={"engineer", "architect"},
            category="communication",
            risk="high",
        ),
        _bucket(
            "private_journal",
            "Private journal",
            "Use the private recovery journal for the running agent.",
            {"journal.private"},
            category="journal",
        ),
        _bucket(
            "scoped_journals",
            "Scoped journals",
            "Read/write scoped Engineer or Architect journals where the base kind already permits it.",
            {"journal.read", "journal.write"},
            base_kinds={"engineer", "architect"},
            category="journal",
        ),
        _bucket(
            "shared_memory",
            "Shared memory",
            "Read and publish shared memory entries.",
            {"memory.read", "memory.publish"},
            category="memory",
        ),
        _bucket(
            "shared_memory_admin",
            "Shared memory admin",
            "Pin, unpin, and link shared memory entries.",
            {"memory.admin"},
            base_kinds={"engineer", "architect"},
            category="memory",
            risk="high",
        ),
        _bucket(
            "engineer_management",
            "Engineer roster and hiring",
            "Read/manage the Engineer roster and request/create Engineer hires.",
            {"agent.engineer_roster_read", "agent.hire_engineer", "agent.manage_engineer_roster"},
            base_kinds={"architect"},
            category="agent_management",
            risk="critical",
        ),
        _bucket(
            "worker_dispatch",
            "Worker dispatch",
            "Launch or route work to Workers and dispatch Board tasks.",
            {"agent.dispatch_worker", "task.dispatch"},
            base_kinds={"engineer", "architect"},
            category="agent_management",
            risk="critical",
        ),
        _bucket(
            "worktree_merge",
            "Worktree and merge",
            "Read worktree metadata and merge/apply/checkpoint worktree changes.",
            {"worktree.read", "worktree.merge"},
            base_kinds={"engineer", "architect"},
            category="worktree",
            risk="critical",
        ),
        _bucket(
            "deploy_admin",
            "Deploy and admin",
            "Read deploy state and deploy/restart/change live runtime settings.",
            {"observe.deploy_state", "deploy.apply", "admin.settings"},
            base_kinds={"architect"},
            category="admin",
            risk="critical",
        ),
        _bucket(
            "class_profile_admin",
            "Class/Profile admin",
            "Assign or edit Agent Profile-compatible policy definitions.",
            {"profile.assign", "profile.edit"},
            base_kinds={"architect"},
            category="admin",
            risk="critical",
        ),
    ]
}

ACL_ACTION_CAPABILITIES: dict[str, frozenset[str]] = {
    "self.read": frozenset({"observe.self_context", "observe.task_detail"}),
    "help.read": frozenset({"observe.self_context"}),
    "task.read": frozenset({"observe.board_summary", "observe.task_detail"}),
    "task.report": frozenset({"task.complete", "task.upload_artifact", "task.verify"}),
    "task.create": frozenset({"task.create"}),
    "task.propose": frozenset({"task.create_queued", "task.update_planning_fields", "task.move_planning_safe", "task.board_sync_read"}),
    "task.update": frozenset({"task.update", "task.reassign", "task.mark_covered"}),
    "task.move": frozenset({"task.move"}),
    "task.dispatch": frozenset({"task.dispatch", "agent.dispatch_worker"}),
    "planning.area.read": frozenset({"planning.area_read"}),
    "planning.area.write": frozenset({"planning.area_write"}),
    "planning.initiative.read": frozenset({"planning.initiative_read"}),
    "planning.initiative.write": frozenset({"planning.initiative_write"}),
    "decision.read": frozenset({"decision.list"}),
    "decision.propose": frozenset({"decision.list", "decision.create_proposed", "decision.update_proposed", "decision.link"}),
    "decision.write": frozenset({"decision.list", "decision.create", "decision.update", "decision.link"}),
    "decision.accept": frozenset({"decision.accept"}),
    "engineer.roster.read": frozenset({"agent.engineer_roster_read"}),
    "engineer.manage": frozenset({"agent.engineer_roster_read", "agent.hire_engineer", "agent.manage_engineer_roster"}),
    "worker.dispatch": frozenset({"agent.dispatch_worker", "task.dispatch"}),
    "message.user": frozenset({"comm.user_ask", "comm.user_message"}),
    "message.engineer": frozenset({"comm.engineer_message"}),
    "message.worker": frozenset({"comm.worker_message"}),
    "message.architect_peer": frozenset({"comm.peer_architect_list", "comm.peer_architect_message"}),
    "journal.private": frozenset({"journal.private"}),
    "journal.scoped": frozenset({"journal.read", "journal.write"}),
    "memory.read": frozenset({"memory.read"}),
    "memory.write": frozenset({"memory.publish"}),
    "memory.admin": frozenset({"memory.admin"}),
    "worktree.read": frozenset({"worktree.read"}),
    "worktree.merge": frozenset({"worktree.merge"}),
    "deploy.read": frozenset({"observe.deploy_state"}),
    "deploy.apply": frozenset({"deploy.apply", "admin.settings"}),
    "settings.admin": frozenset({"admin.settings"}),
    "class_profile.admin": frozenset({"profile.assign", "profile.edit"}),
    "behavior_overlay.read": frozenset({"behavior_overlay.read"}),
    "behavior_overlay.propose": frozenset({"behavior_overlay.read", "behavior_overlay.propose_self"}),
    "behavior_overlay.admin": frozenset({"profile.assign", "profile.edit"}),
    "thinking.read": frozenset({"thinking.read"}),
    "thinking.write": frozenset({"thinking.read", "thinking.write_own"}),
    "idea_brief.read": frozenset({"idea_brief.read"}),
    "idea_brief.write": frozenset({"idea_brief.read", "idea_brief.write_own", "idea_brief.propose"}),
    "telemetry.read": frozenset({"observe.events", "observe.mcp_calls"}),
}


def _acl_action_capabilities(action: str) -> frozenset[str]:
    return ACL_ACTION_CAPABILITIES.get(str(action or "").strip(), frozenset())


RESTRICTION_BUCKETS: dict[str, AgentClassCapabilityBucket] = {
    bucket.id: bucket for bucket in [
        _bucket(
            "deny_execution_task_control",
            "Deny execution task control",
            "Explicitly deny executable task create/update/reassign/move/dispatch authority.",
            denies={"task.create", "task.update", "task.reassign", "task.move", "task.dispatch", "task.mark_covered"},
            category="task",
            risk="high",
        ),
        _bucket(
            "deny_worker_dispatch",
            "Deny Worker dispatch",
            "Explicitly deny Worker launch/routing and task dispatch authority.",
            denies={"agent.dispatch_worker", "task.dispatch"},
            category="agent_management",
            risk="critical",
        ),
        _bucket(
            "deny_engineer_management",
            "Deny Engineer management",
            "Explicitly deny Engineer roster management and hire authority.",
            denies={"agent.engineer_roster_read", "agent.hire_engineer", "agent.manage_engineer_roster"},
            category="agent_management",
            risk="critical",
        ),
        _bucket(
            "deny_engineer_worker_messages",
            "Deny Engineer/Worker messages",
            "Explicitly deny direct Engineer/Worker messaging.",
            denies={"comm.engineer_message", "comm.worker_message"},
            category="communication",
            risk="high",
        ),
        _bucket(
            "deny_worktree_merge",
            "Deny worktree/merge",
            "Explicitly deny merge/apply/checkpoint worktree authority.",
            denies={"worktree.merge"},
            category="worktree",
            risk="critical",
        ),
        _bucket(
            "deny_deploy_admin",
            "Deny deploy/admin",
            "Explicitly deny deploy/restart/live-settings authority.",
            denies={"observe.deploy_state", "deploy.apply", "admin.settings"},
            category="admin",
            risk="critical",
        ),
        _bucket(
            "deny_class_profile_admin",
            "Deny Class/Profile admin",
            "Explicitly deny Agent Profile assignment/edit authority.",
            denies={"profile.assign", "profile.edit"},
            category="admin",
            risk="critical",
        ),
        _bucket(
            "deny_decision_acceptance",
            "Deny accepted-decision authority",
            "Explicitly deny accepting decisions or creating accepted decisions.",
            denies={"decision.accept", "decision.create", "decision.update"},
            category="decision",
            risk="high",
        ),
        _bucket(
            "deny_raw_tool_picker",
            "Deny raw tool picker",
            "Explicitly records that arbitrary MCP/raw tool picker authority is outside the class contract.",
            denies=set(),
            category="admin",
            risk="critical",
        ),
        _bucket(
            "deny_high_risk_operations",
            "Deny remaining high-risk operations",
            "Explicitly deny high-risk/critical operations not selected by capability buckets.",
            denies=HIGH_RISK_CAPABILITIES,
            category="safety",
            risk="critical",
        ),
    ]
}


def agent_class_capability_bucket_catalog(*, base_kind: str = "") -> list[dict[str, Any]]:
    """Return operator-facing capability bucket choices for UI authoring."""

    return [
        bucket.as_catalog_dict(base_kind=base_kind, restriction=False)
        for bucket in sorted(CAPABILITY_BUCKETS.values(), key=lambda item: (item.category, item.label, item.id))
    ]


def agent_class_restriction_bucket_catalog(*, base_kind: str = "") -> list[dict[str, Any]]:
    """Return operator-facing explicit restriction choices for UI authoring."""

    return [
        bucket.as_catalog_dict(base_kind=base_kind, restriction=True)
        for bucket in sorted(RESTRICTION_BUCKETS.values(), key=lambda item: (item.category, item.label, item.id))
    ]


def agent_class_authoring_contract(*, base_kind: str = "") -> dict[str, Any]:
    return {
        "schema_version": AGENT_CLASS_SCHEMA_VERSION,
        "normal_authoring_mode": "capability_acl",
        "acl_modes": ["allow", "deny"],
        "acl_shape": "acl.mode + acl.rules",
        "acl_rule_keys": ["capability", "scope"],
        "scope_vocabulary": ["self", "children", "group", "global"],
        "capability_catalog": capability_catalog_for_base_kind(base_kind),
        "apply_model": "Agent Class saves/assignments do not mutate running sessions; ACL changes apply at next launch/relaunch.",
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
    }


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
    prompt: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    acl: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    communication: dict[str, Any] = field(default_factory=dict)
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
            prompt=_normalized_prompt_mapping(data.get("prompt", {})),
            identity=(dict(data.get("identity") or {}) if isinstance(data.get("identity"), dict) else {}),
            runtime=(dict(data.get("runtime") or {}) if isinstance(data.get("runtime"), dict) else {}),
            policy=(dict(data.get("policy") or {}) if isinstance(data.get("policy"), dict) else {}),
            acl=(dict(data.get("acl") or {}) if isinstance(data.get("acl"), dict) else {}),
            capabilities=(dict(data.get("capabilities") or {}) if isinstance(data.get("capabilities"), dict) else {}),
            communication=(dict(data.get("communication") or {}) if isinstance(data.get("communication"), dict) else {}),
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
            "agent_profile_ref": self.agent_profile_ref.as_dict(),
            "acl": compact_agent_class_acl_preview(self),
            "authority_summary": agent_class_authority_summary(self),
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

def _dedupe_string_list(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if values is None:
        return out
    raw_values = values if isinstance(values, list) else [values]
    for item in raw_values:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _capabilities_mapping(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("capabilities")
    return dict(value or {}) if isinstance(value, dict) else {}


def _policy_mapping(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("policy")
    return dict(value or {}) if isinstance(value, dict) else {}


def _bucket_selection_from_data(data: dict[str, Any]) -> list[str]:
    capabilities = _capabilities_mapping(data)
    policy = _policy_mapping(data)
    for value in (
        data.get("capability_buckets"),
        capabilities.get("buckets"),
        capabilities.get("capability_buckets"),
        policy.get("capability_buckets"),
        policy.get("buckets"),
    ):
        selected = _dedupe_string_list(value)
        if selected:
            return selected
    return []


def _restriction_selection_from_data(data: dict[str, Any]) -> list[str]:
    capabilities = _capabilities_mapping(data)
    policy = _policy_mapping(data)
    for value in (
        data.get("restriction_buckets"),
        capabilities.get("restrictions"),
        capabilities.get("restriction_buckets"),
        capabilities.get("denied_buckets"),
        policy.get("restriction_buckets"),
        policy.get("restrictions"),
    ):
        selected = _dedupe_string_list(value)
        if selected:
            return selected
    return []


def _has_operator_bucket_selection(data: dict[str, Any]) -> bool:
    return bool(_bucket_selection_from_data(data) or _restriction_selection_from_data(data))


def _normalize_bucket_fields(out: dict[str, Any]) -> dict[str, Any]:
    """Fold authoring aliases into capabilities.buckets/restrictions."""

    capabilities = _capabilities_mapping(out)
    selected = _bucket_selection_from_data(out)
    restrictions = _restriction_selection_from_data(out)
    if selected:
        capabilities["buckets"] = selected
    if restrictions:
        capabilities["restrictions"] = restrictions
    out.pop("capability_buckets", None)
    out.pop("restriction_buckets", None)
    if capabilities:
        out["capabilities"] = capabilities
    return out


def _acl_mapping(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("acl")
    return dict(value or {}) if isinstance(value, dict) else {}


def _acl_entry_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _entry_text(entry: Any, *keys: str) -> str:
    if isinstance(entry, dict):
        for key in keys:
            text = str(entry.get(key, "") or "").strip()
            if text:
                return text
        return ""
    return str(entry or "").strip()


def _expand_acl_capability_token(token: str, *, restriction: bool = False) -> tuple[set[str], set[str], str]:
    """Expand an ACL capability token into grant/deny atoms.

    The ACL authoring surface accepts legacy bucket ids during migration.  A
    token can be a capability atom, a capability bucket id, or a restriction
    bucket id.  Restriction bucket ids always deny their atoms.
    """

    token = str(token or "").strip()
    grants: set[str] = set()
    denies: set[str] = set()
    if not token:
        return grants, denies, ""
    if token in CAPABILITIES:
        (denies if restriction else grants).add(token)
        return grants, denies, "atom"
    bucket = CAPABILITY_BUCKETS.get(token)
    if bucket:
        (denies if restriction else grants).update(bucket.grants)
        denies.update(bucket.denies)
        return grants, denies, "capability_bucket"
    bucket = RESTRICTION_BUCKETS.get(token)
    if bucket:
        denies.update(bucket.denies)
        return grants, denies, "restriction_bucket"
    return grants, denies, ""


def _legacy_acl_from_bucket_fields(raw: dict[str, Any]) -> dict[str, Any]:
    selected = _bucket_selection_from_data(raw)
    restrictions = _restriction_selection_from_data(raw)
    if not selected and not restrictions:
        return {}
    if restrictions and not selected:
        return {
            "mode": "deny",
            "deny": [{"capability": item} for item in restrictions],
            "source": "legacy_capability_buckets",
        }
    return {
        "mode": "allow",
        "allow": [{"capability": item} for item in selected],
        "source": "legacy_capability_buckets",
    }


def _normalized_acl_for_data(data: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    if isinstance(data, AgentClassDefinition):
        if data.agent_class_schema_version >= AGENT_CLASS_SCHEMA_VERSION:
            return dict(data.acl or {})
        raw = {
            "acl": dict(data.acl or {}),
            "capabilities": dict(data.capabilities or {}),
            "policy": dict(data.policy or {}),
            "base_kind": data.base_kind,
        }
    else:
        raw = _normalize_bucket_fields(_normalized_class_data(dict(data or {})))
        if _agent_class_schema_version(raw) >= AGENT_CLASS_SCHEMA_VERSION:
            return _acl_mapping(raw)
    acl = _acl_mapping(raw)
    if not acl:
        acl = _legacy_acl_from_bucket_fields(raw)
    mode = str(acl.get("mode", "") or "").strip().lower()
    if not mode:
        mode = "allow" if acl else ""
    if mode not in {"allow", "deny", ""}:
        mode = "allow"
    if not acl:
        return {}
    out = dict(acl)
    out["mode"] = mode
    out["allow"] = _acl_entry_list(out.get("allow"))
    out["deny"] = _acl_entry_list(out.get("deny"))
    return out


def _compiled_bucket_policy_for_data(data: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    if isinstance(data, AgentClassDefinition):
        base_kind = data.base_kind
        schema_version = data.agent_class_schema_version
        raw = {
            "acl": dict(data.acl or {}),
            "capabilities": dict(data.capabilities or {}),
            "policy": dict(data.policy or {}),
        }
    else:
        normalized = _normalize_bucket_fields(_normalized_class_data(dict(data or {})))
        base_kind = str(normalized.get("base_kind", "") or "").strip()
        schema_version = _agent_class_schema_version(normalized)
        raw = normalized
    if schema_version >= AGENT_CLASS_SCHEMA_VERSION:
        acl = _acl_mapping(raw)
        try:
            authority = compile_agent_class_acl(
                base_kind=base_kind,
                acl=acl,
                capabilities=CAPABILITY_CATALOG,
            )
        except AuthorityValidationError:
            return {
                "mode": str(acl.get("mode", "") or ""),
                "grants": [],
                "denies": [],
                "allowed_tools": [],
                "denied_tools": [],
                "allowed_families": [],
                "denied_families": [],
                "allowed_actions": [],
                "denied_actions": [],
                "bucket_ids": [],
                "restriction_ids": [],
                "bucket_previews": [],
                "restriction_previews": [],
                "canonical_capabilities": {},
                "effective_authority": {},
            }
        effective_snapshot = authority.as_snapshot()
        canonical_ids = set(authority.capabilities)
        ceiling = set(BASE_KIND_CEILINGS.get(base_kind, frozenset()))
        grants = set(legacy_atoms_for_canonical_capabilities(canonical_ids))
        grants &= ceiling
        denies = ceiling - grants
        action_items = [
            {
                "capability": capability,
                **({"scope": scope} if scope is not None else {}),
            }
            for capability, scope in sorted(authority.capabilities.items())
        ]
        return {
            "mode": authority.mode,
            "bucket_ids": [],
            "restriction_ids": [],
            "grants": sorted(grants),
            "denies": sorted(denies),
            "allowed_tools": [],
            "denied_tools": [],
            "allowed_families": [],
            "denied_families": [],
            "allowed_actions": action_items if authority.mode == "allow" else [],
            "denied_actions": action_items if authority.mode == "deny" else [],
            "bucket_previews": [],
            "restriction_previews": [],
            "canonical_capabilities": dict(authority.capabilities),
            "effective_authority": effective_snapshot,
        }
    acl = _normalized_acl_for_data(raw)
    selected = _bucket_selection_from_data(raw)
    restrictions = _restriction_selection_from_data(raw)
    mode = str(acl.get("mode", "") or "").strip().lower()
    ceiling = set(BASE_KIND_CEILINGS.get(base_kind, frozenset()))
    grants: set[str] = set(ceiling) if mode == "deny" and ceiling else set()
    denies: set[str] = set()
    allowed_tools: set[str] = set()
    denied_tools: set[str] = set()
    allowed_families: set[str] = set()
    denied_families: set[str] = set()
    allowed_actions: list[dict[str, Any]] = []
    denied_actions: list[dict[str, Any]] = []
    bucket_previews: list[dict[str, Any]] = []
    restriction_previews: list[dict[str, Any]] = []

    def apply_entry(entry: Any, *, deny: bool = False) -> None:
        nonlocal grants, denies
        token = _entry_text(entry, "capability", "bucket", "atom")
        if token:
            entry_grants, entry_denies, kind = _expand_acl_capability_token(token, restriction=deny)
            grants.update(entry_grants)
            denies.update(entry_denies)
            if kind == "capability_bucket" and token in CAPABILITY_BUCKETS:
                bucket_previews.append(CAPABILITY_BUCKETS[token].as_catalog_dict(base_kind=base_kind, restriction=False))
            elif kind == "restriction_bucket" and token in RESTRICTION_BUCKETS:
                restriction_previews.append(RESTRICTION_BUCKETS[token].as_catalog_dict(base_kind=base_kind, restriction=True))
        tool = _entry_text(entry, "tool")
        if tool:
            (denied_tools if deny else allowed_tools).add(tool)
        family = _entry_text(entry, "family", "tool_family")
        if family:
            (denied_families if deny else allowed_families).add(family)
        action = _entry_text(entry, "action")
        if action:
            action_caps = _acl_action_capabilities(action)
            if deny:
                denies.update(action_caps)
            else:
                grants.update(action_caps)
            item = {"action": action}
            if isinstance(entry, dict) and str(entry.get("scope", "") or "").strip():
                item["scope"] = str(entry.get("scope", "") or "").strip()
            if action_caps:
                item["capabilities"] = sorted(action_caps)
            (denied_actions if deny else allowed_actions).append(item)

    if acl:
        if mode == "deny":
            for entry in acl.get("deny", []) or []:
                apply_entry(entry, deny=True)
        else:
            for entry in acl.get("allow", []) or []:
                apply_entry(entry, deny=False)
    else:
        for bucket_id in selected:
            bucket = CAPABILITY_BUCKETS.get(bucket_id)
            if not bucket:
                continue
            grants.update(bucket.grants)
            denies.update(bucket.denies)
            bucket_previews.append(bucket.as_catalog_dict(base_kind=base_kind, restriction=False))
        for restriction_id in restrictions:
            bucket = RESTRICTION_BUCKETS.get(restriction_id)
            if not bucket:
                continue
            denies.update(bucket.denies)
            restriction_previews.append(bucket.as_catalog_dict(base_kind=base_kind, restriction=True))

    if ceiling:
        grants &= ceiling
        denies &= ceiling
    grants -= denies
    # De-dupe previews while preserving first occurrence.
    def dedupe_previews(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            item_id = str(item.get("id", "") or "")
            if item_id and item_id not in seen:
                out.append(item)
                seen.add(item_id)
        return out

    return {
        "mode": mode or ("allow" if selected or restrictions else ""),
        "bucket_ids": (selected if mode != "deny" else []) or ([str(_entry_text(item, "capability", "bucket")) for item in acl.get("allow", []) if str(_entry_text(item, "capability", "bucket")) in CAPABILITY_BUCKETS] if mode != "deny" else []),
        "restriction_ids": (restrictions if mode == "deny" else []) or ([str(_entry_text(item, "capability", "bucket")) for item in acl.get("deny", []) if str(_entry_text(item, "capability", "bucket")) in RESTRICTION_BUCKETS] if mode == "deny" else []),
        "grants": sorted(grants),
        "denies": sorted(denies),
        "allowed_tools": sorted(allowed_tools),
        "denied_tools": sorted(denied_tools),
        "allowed_families": sorted(allowed_families),
        "denied_families": sorted(denied_families),
        "allowed_actions": allowed_actions,
        "denied_actions": denied_actions,
        "bucket_previews": dedupe_previews(bucket_previews),
        "restriction_previews": dedupe_previews(restriction_previews),
    }

def _compiled_bucket_policy_for_definition(definition: "AgentClassDefinition") -> dict[str, Any]:
    return _compiled_bucket_policy_for_data(definition)


def _operator_access_summary(compiled_policy: dict[str, Any]) -> dict[str, Any]:
    buckets = list(compiled_policy.get("bucket_previews", []) or [])
    restrictions = list(compiled_policy.get("restriction_previews", []) or [])
    allowed_labels = [str(item.get("label", item.get("id", "")) or "") for item in buckets]
    denied_labels = [str(item.get("label", item.get("id", "")) or "") for item in restrictions]
    return {
        "allowed": allowed_labels,
        "denied": denied_labels,
        "allowed_summary": "; ".join(label for label in allowed_labels if label),
        "denied_summary": "; ".join(label for label in denied_labels if label),
        "bucket_count": len(allowed_labels),
        "restriction_count": len(denied_labels),
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


def _policy_mode_from_data(data: dict[str, Any]) -> str:
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    mode = str(policy.get("mode", "") or "").strip()
    if mode:
        return mode
    if _acl_mapping(data) or _has_operator_bucket_selection(data):
        return "compile"
    if isinstance(policy, dict) and policy.get("generated_profile_id") is not None:
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
    out = _normalize_bucket_fields(out)
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
    out["prompt"] = _normalized_prompt_mapping(out.get("prompt", {}))
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


def internal_policy_display_name_for_class(definition: "AgentClassDefinition") -> str:
    label = primary_identity_label_for_class(definition)
    return f"{label} internal policy"


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
    compiled = _compiled_bucket_policy_for_data(definition_or_data)
    mode = str(compiled.get("mode", "") or "").strip() or "wrap_profile"
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
    return {
        "mode": mode,
        "allowed_capabilities": list(compiled.get("grants", []) or []),
        "denied_capabilities": list(compiled.get("denies", []) or []),
        "allowed_tools": list(compiled.get("allowed_tools", []) or []),
        "denied_tools": list(compiled.get("denied_tools", []) or []),
        "allowed_families": list(compiled.get("allowed_families", []) or []),
        "denied_families": list(compiled.get("denied_families", []) or []),
        "allowed_actions": list(compiled.get("allowed_actions", []) or []),
        "denied_actions": list(compiled.get("denied_actions", []) or []),
        "capability_buckets": list(compiled.get("bucket_ids", []) or []),
        "restriction_buckets": list(compiled.get("restriction_ids", []) or []),
    }


def agent_class_authority_summary(definition_or_data: "AgentClassDefinition | dict[str, Any]") -> dict[str, Any]:
    if isinstance(definition_or_data, AgentClassDefinition):
        base_kind = definition_or_data.base_kind
        lifecycle = definition_or_data.lifecycle
    else:
        normalized = _normalized_class_data(definition_or_data)
        base_kind = str(normalized.get("base_kind", "") or "")
        lifecycle = str(normalized.get("lifecycle", "") or "")
    compiled = _compiled_bucket_policy_for_data(definition_or_data)
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
    grants = set(compiled.get("grants", []) or [])
    denies = set(compiled.get("denies", []) or [])
    ceiling = set(BASE_KIND_CEILINGS.get(base_kind, frozenset()))
    unavailable_high_risk = sorted((ceiling & HIGH_RISK_CAPABILITIES) - grants)
    high_risk_grants = sorted(grants & HIGH_RISK_CAPABILITIES)
    return {
        "mode": str(compiled.get("mode", "") or "wrap_profile"),
        "base_kind": base_kind,
        "lifecycle": lifecycle,
        "granted_capability_count": len(grants),
        "denied_capability_count": len(denies),
        "allowed_tool_count": len(compiled.get("allowed_tools", []) or []),
        "denied_tool_count": len(compiled.get("denied_tools", []) or []),
        "allowed_family_count": len(compiled.get("allowed_families", []) or []),
        "denied_family_count": len(compiled.get("denied_families", []) or []),
        "high_risk_grants": high_risk_grants,
        "unavailable_high_risk_capabilities": unavailable_high_risk,
    }


def effective_authority_snapshot_for_class(
    definition_or_data: "AgentClassDefinition | dict[str, Any]",
) -> dict[str, Any]:
    """Return the canonical authority snapshot compiled from class ACL data."""

    compiled = _compiled_bucket_policy_for_data(definition_or_data)
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
        compiled = _compiled_bucket_policy_for_data(definition_or_data)
        summary["policy_compiler_version"] = POLICY_COMPILER_VERSION
        summary["grant_count"] = len(compiled.get("grants", []) or [])
        summary["deny_count"] = len(compiled.get("denies", []) or [])
        summary["capability_buckets"] = list(compiled.get("bucket_ids", []) or [])
        summary["restriction_buckets"] = list(compiled.get("restriction_ids", []) or [])
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


def _profile_by_id_for_validation(base_dir: str = "") -> dict[str, AgentProfileDefinition]:
    profiles, issues = load_agent_profiles(base_dir=base_dir)
    if any(issue.severity == "error" for issue in issues):
        # Missing/invalid profiles are reported as class reference failures below
        # instead of hiding class YAML validation entirely.
        return {profile.id: profile for profile in profiles}
    return {profile.id: profile for profile in profiles}


def _allowed_raw_field_paths_for_schema(schema_version: int) -> set[str]:
    if schema_version >= AGENT_CLASS_SCHEMA_VERSION:
        # The top-level ``capabilities`` section is operator-facing metadata plus
        # bucket selection.  Raw Atom grants/denies remain forbidden for Agent
        # Classes; generated Agent Profile-compatible atoms are diagnostics.
        return {"capabilities"}
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

def _bucket_field_entries(raw_data: dict[str, Any], *, restriction: bool = False) -> list[tuple[str, Any]]:
    capabilities = raw_data.get("capabilities") if isinstance(raw_data.get("capabilities"), dict) else {}
    policy = raw_data.get("policy") if isinstance(raw_data.get("policy"), dict) else {}
    if restriction:
        candidates = [
            ("restriction_buckets", raw_data.get("restriction_buckets")),
            ("capabilities.restrictions", capabilities.get("restrictions") if isinstance(capabilities, dict) else None),
            ("capabilities.restriction_buckets", capabilities.get("restriction_buckets") if isinstance(capabilities, dict) else None),
            ("capabilities.denied_buckets", capabilities.get("denied_buckets") if isinstance(capabilities, dict) else None),
            ("policy.restriction_buckets", policy.get("restriction_buckets") if isinstance(policy, dict) else None),
            ("policy.restrictions", policy.get("restrictions") if isinstance(policy, dict) else None),
        ]
    else:
        candidates = [
            ("capability_buckets", raw_data.get("capability_buckets")),
            ("capabilities.buckets", capabilities.get("buckets") if isinstance(capabilities, dict) else None),
            ("capabilities.capability_buckets", capabilities.get("capability_buckets") if isinstance(capabilities, dict) else None),
            ("policy.capability_buckets", policy.get("capability_buckets") if isinstance(policy, dict) else None),
            ("policy.buckets", policy.get("buckets") if isinstance(policy, dict) else None),
        ]
    return [(path, value) for path, value in candidates if value is not None]


def _validate_bucket_alias_fields(raw_data: dict[str, Any], issues: list[ValidationIssue], *,
                                  source: str, class_id: str, restriction: bool = False) -> list[str]:
    registry = RESTRICTION_BUCKETS if restriction else CAPABILITY_BUCKETS
    entries = _bucket_field_entries(raw_data, restriction=restriction)
    selected: list[str] = []
    seen: set[str] = set()
    for path, value in entries:
        values = _validate_string_list_field(value, path, issues, source=source, class_id=class_id)
        for item in values:
            if item not in seen:
                selected.append(item)
                seen.add(item)
    unknown = sorted(set(selected) - set(registry))
    if unknown:
        issues.append(ValidationIssue(
            "error",
            "unknown_restriction_buckets" if restriction else "unknown_capability_buckets",
            ("unknown restriction buckets: " if restriction else "unknown capability buckets: ") + ", ".join(unknown),
            path=source,
            profile_id=class_id,
        ))
    if len(entries) > 1:
        # Multiple aliases are accepted after de-duping, but warn because the UI
        # should use the canonical capabilities.* field to avoid drift.
        issues.append(ValidationIssue(
            "warn",
            "duplicate_restriction_bucket_fields" if restriction else "duplicate_capability_bucket_fields",
            ("multiple restriction bucket fields supplied; canonical field is capabilities.restrictions" if restriction
             else "multiple capability bucket fields supplied; canonical field is capabilities.buckets"),
            path=source,
            profile_id=class_id,
        ))
    return selected


def _validate_bucket_policy_semantics(raw_data: dict[str, Any], normalized: dict[str, Any],
                                      issues: list[ValidationIssue], *,
                                      source: str, class_id: str, base_kind: str,
                                      policy_mode: str) -> None:
    selected = _validate_bucket_alias_fields(raw_data, issues, source=source, class_id=class_id, restriction=False)
    restrictions = _validate_bucket_alias_fields(raw_data, issues, source=source, class_id=class_id, restriction=True)
    if policy_mode != "compile":
        return
    acl = _normalized_acl_for_data(normalized)
    acl_allow_entries = _acl_entry_list(acl.get("allow"))
    acl_deny_entries = _acl_entry_list(acl.get("deny"))
    has_acl_entries = bool(acl_allow_entries or acl_deny_entries)
    if not selected and not has_acl_entries:
        issues.append(ValidationIssue(
            "error",
            "missing_acl_entries",
            "Agent Class compile authority must select acl.allow entries in allow mode, acl.deny entries in deny mode, or legacy capabilities.buckets",
            path=source,
            profile_id=class_id,
        ))
        return
    acl_mode = str(acl.get("mode", "") or "").strip().lower()
    if acl_mode == "allow" and acl_deny_entries:
        issues.append(ValidationIssue(
            "error",
            "acl_deny_not_allowed_in_allow_mode",
            "acl.mode=allow must use acl.allow only; deny entries are only valid when acl.mode=deny",
            path=source,
            profile_id=class_id,
        ))
    if acl_mode == "deny" and acl_allow_entries:
        issues.append(ValidationIssue(
            "error",
            "acl_allow_not_allowed_in_deny_mode",
            "acl.mode=deny must use acl.deny only; allow entries are only valid when acl.mode=allow",
            path=source,
            profile_id=class_id,
        ))
    for bucket_id in selected:
        bucket = CAPABILITY_BUCKETS.get(bucket_id)
        if not bucket:
            continue
        if base_kind and base_kind not in bucket.base_kinds:
            issues.append(ValidationIssue(
                "error",
                "capability_bucket_unavailable_for_base_kind",
                f"capability bucket {bucket_id} is not available for base_kind={base_kind}",
                path=source,
                profile_id=class_id,
            ))
        if base_kind in BASE_KIND_CEILINGS:
            outside = sorted(set(bucket.grants) - BASE_KIND_CEILINGS[base_kind])
            if outside:
                issues.append(ValidationIssue(
                    "error",
                    "capability_bucket_outside_base_kind_ceiling",
                    f"capability bucket {bucket_id} grants atoms outside {base_kind} ceiling: " + ", ".join(outside),
                    path=source,
                    profile_id=class_id,
                ))
    for restriction_id in restrictions:
        bucket = RESTRICTION_BUCKETS.get(restriction_id)
        if not bucket:
            continue
        if base_kind and base_kind not in bucket.base_kinds:
            issues.append(ValidationIssue(
                "error",
                "restriction_bucket_unavailable_for_base_kind",
                f"restriction bucket {restriction_id} is not available for base_kind={base_kind}",
                path=source,
                profile_id=class_id,
            ))

    def validate_acl_capability_entry(entry: Any, *, deny: bool) -> None:
        token = _entry_text(entry, "capability", "bucket", "atom")
        if not token:
            return
        capability_bucket = CAPABILITY_BUCKETS.get(token)
        restriction_bucket = RESTRICTION_BUCKETS.get(token)
        if capability_bucket:
            if base_kind and base_kind not in capability_bucket.base_kinds:
                issues.append(ValidationIssue(
                    "error",
                    "capability_bucket_unavailable_for_base_kind",
                    f"capability bucket {token} is not available for base_kind={base_kind}",
                    path=source,
                    profile_id=class_id,
                ))
            if base_kind in BASE_KIND_CEILINGS:
                outside = sorted(set(capability_bucket.grants) - BASE_KIND_CEILINGS[base_kind])
                if outside:
                    issues.append(ValidationIssue(
                        "error",
                        "capability_bucket_outside_base_kind_ceiling",
                        f"capability bucket {token} grants atoms outside {base_kind} ceiling: " + ", ".join(outside),
                        path=source,
                        profile_id=class_id,
                    ))
            return
        if restriction_bucket:
            if base_kind and base_kind not in restriction_bucket.base_kinds:
                issues.append(ValidationIssue(
                    "error",
                    "restriction_bucket_unavailable_for_base_kind",
                    f"restriction bucket {token} is not available for base_kind={base_kind}",
                    path=source,
                    profile_id=class_id,
                ))
            return
        if token in CAPABILITIES and base_kind in BASE_KIND_CEILINGS and token not in BASE_KIND_CEILINGS[base_kind]:
            issues.append(ValidationIssue(
                "error",
                "acl_capability_outside_base_kind_ceiling",
                f"ACL capability {token} is outside {base_kind} ceiling",
                path=source,
                profile_id=class_id,
            ))

    for entry in acl.get("allow", []) or []:
        validate_acl_capability_entry(entry, deny=False)
    for entry in acl.get("deny", []) or []:
        validate_acl_capability_entry(entry, deny=True)
    compiled = _compiled_bucket_policy_for_data(normalized)
    grants = set(compiled.get("grants", []) or [])
    if base_kind in BASE_KIND_CEILINGS:
        outside = sorted(grants - BASE_KIND_CEILINGS[base_kind])
        if outside:
            issues.append(ValidationIssue(
                "error",
                "compiled_policy_outside_base_kind_ceiling",
                f"compiled bucket policy outside {base_kind} ceiling: " + ", ".join(outside),
                path=source,
                profile_id=class_id,
            ))


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

    if schema_version != AGENT_CLASS_SCHEMA_VERSION:
        issues.append(ValidationIssue(
            "error",
            "invalid_agent_class_schema_version",
            f"agent_class_schema_version must be {AGENT_CLASS_SCHEMA_VERSION}",
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
            "Agent Class definitions must not contain raw MCP/tool fields or raw capability atom grants/denies; use capabilities.buckets/restrictions instead: "
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

    for mapping_key in ("identity", "runtime", "metadata", "draft", "policy", "acl", "capabilities", "communication"):
        if mapping_key in raw_data and not isinstance(raw_data.get(mapping_key), dict):
            issues.append(ValidationIssue(
                "error",
                f"{mapping_key}_not_mapping",
                f"{mapping_key} must be a mapping",
                path=source,
                profile_id=class_id,
            ))

    legacy_authority_fields = sorted(
        key for key in (
            "policy",
            "capabilities",
            "capability_buckets",
            "restriction_buckets",
            "agent_profile_ref",
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

    prompt_value = raw_data.get("prompt", {})
    if "prompt" in raw_data and not isinstance(prompt_value, dict):
        issues.append(ValidationIssue(
            "error", "prompt_not_mapping", "prompt must be a mapping with identity, job, boot_checklist, operating_guidelines, and/or tool_guidance", path=source, profile_id=class_id
        ))
    if isinstance(prompt_value, dict):
        unknown_prompt_keys = sorted(set(prompt_value) - PROMPT_ALLOWED_KEYS)
        if unknown_prompt_keys:
            issues.append(ValidationIssue(
                "error",
                "unknown_prompt_fields",
                "unknown prompt fields: " + ", ".join(unknown_prompt_keys),
                path=source,
                profile_id=class_id,
            ))
        for key in PROMPT_TEXT_KEYS:
            if key in prompt_value and not isinstance(prompt_value.get(key), str):
                issues.append(ValidationIssue(
                    "error",
                    "prompt_text_field_not_string",
                    f"prompt.{key} must be a string",
                    path=source,
                    profile_id=class_id,
                ))
        for key in PROMPT_LIST_KEYS:
            if key in prompt_value and not isinstance(prompt_value.get(key), list):
                issues.append(ValidationIssue(
                    "error",
                    "prompt_list_field_not_list",
                    f"prompt.{key} must be a list of strings",
                    path=source,
                    profile_id=class_id,
                ))
            _validate_string_list_field(prompt_value.get(key), f"prompt.{key}", issues, source=source, class_id=class_id)
        if PROMPT_TOOL_GUIDANCE_KEY in prompt_value and not isinstance(prompt_value.get(PROMPT_TOOL_GUIDANCE_KEY), list):
            issues.append(ValidationIssue(
                "error",
                "prompt_tool_guidance_not_list",
                "prompt.tool_guidance must be a list of mappings",
                path=source,
                profile_id=class_id,
            ))
        elif isinstance(prompt_value.get(PROMPT_TOOL_GUIDANCE_KEY), list):
            for idx, item in enumerate(prompt_value.get(PROMPT_TOOL_GUIDANCE_KEY) or []):
                if not isinstance(item, dict):
                    issues.append(ValidationIssue(
                        "error",
                        "prompt_tool_guidance_item_not_mapping",
                        f"prompt.tool_guidance[{idx}] must be a mapping",
                        path=source,
                        profile_id=class_id,
                    ))
                    continue
                unknown = sorted(set(item) - {"when_capability", "text"})
                if unknown:
                    issues.append(ValidationIssue(
                        "error",
                        "unknown_prompt_tool_guidance_fields",
                        f"unknown prompt.tool_guidance[{idx}] fields: " + ", ".join(unknown),
                        path=source,
                        profile_id=class_id,
                    ))
                if not isinstance(item.get("text", ""), str) or not str(item.get("text", "") or "").strip():
                    issues.append(ValidationIssue(
                        "error",
                        "prompt_tool_guidance_missing_text",
                        f"prompt.tool_guidance[{idx}].text must be a non-empty string",
                        path=source,
                        profile_id=class_id,
                    ))
                selector = item.get("when_capability")
                if not isinstance(selector, str) or not str(selector or "").strip():
                    issues.append(ValidationIssue(
                        "error",
                        "prompt_tool_guidance_capability_required",
                        f"prompt.tool_guidance[{idx}].when_capability must be a non-empty string",
                        path=source,
                        profile_id=class_id,
                    ))
                elif str(selector).strip() not in CAPABILITY_CATALOG:
                    issues.append(ValidationIssue(
                        "error",
                        "unknown_prompt_tool_guidance_capability",
                        f"prompt.tool_guidance[{idx}] references unknown capability {str(selector).strip()}",
                        path=source,
                        profile_id=class_id,
                    ))
    prompt_text = _prompt_plain_text(normalized.get("prompt", {}))
    if len(prompt_text) > MAX_PROMPT_LEN:
        issues.append(ValidationIssue(
            "error",
            "prompt_too_long",
            f"prompt text must be at most {MAX_PROMPT_LEN} characters",
            path=source,
            profile_id=class_id,
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
        compiled_bucket_policy = _compiled_bucket_policy_for_data(normalized)
        unknown_atoms = sorted(
            (set(compiled_bucket_policy.get("grants", []) or [])
             | set(compiled_bucket_policy.get("denies", []) or []))
            - set(CAPABILITIES)
        )
        if unknown_atoms:
            issues.append(ValidationIssue(
                "error",
                "unknown_compiled_policy_capability_atoms",
                "compiled bucket policy emitted unknown capability atoms: " + ", ".join(unknown_atoms),
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
    generated_ref = definition.agent_profile_ref
    compiled_bucket_policy = _compiled_bucket_policy_for_definition(definition)
    grants = list(compiled_bucket_policy.get("grants", []) or [])
    denies = list(compiled_bucket_policy.get("denies", []) or [])
    acl_preview = compact_agent_class_acl_preview(definition)
    profile_policy = {
        "base_kind": definition.base_kind,
        "acl": acl_preview,
    }
    metadata = dict(definition.metadata or {})
    metadata["generated_by_agent_class"] = {
        "id": definition.id,
        "version": definition.version,
        "display_name": definition.display_name,
        "schema_version": definition.agent_class_schema_version,
        "compiler_version": POLICY_COMPILER_VERSION,
        "acl_mode": str(compiled_bucket_policy.get("mode", "") or ""),
        "capability_buckets": list(compiled_bucket_policy.get("bucket_ids", []) or []),
        "restriction_buckets": list(compiled_bucket_policy.get("restriction_ids", []) or []),
    }
    metadata["agent_class_acl"] = acl_preview
    metadata["internal_policy_source"] = "compiled_from_agent_class_acl"
    metadata["capability_bucket_count"] = len(compiled_bucket_policy.get("bucket_ids", []) or [])
    metadata["restriction_bucket_count"] = len(compiled_bucket_policy.get("restriction_ids", []) or [])
    metadata["generated_profile"] = True
    return {
        "id": generated_ref.id,
        "version": generated_ref.version,
        "base_kind": definition.base_kind,
        "display_name": internal_policy_display_name_for_class(definition),
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
    compiled_bucket_policy = _compiled_bucket_policy_for_definition(definition) if policy_summary.get("mode") == "compile" else {}
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
        out["capability_bucket_selection"] = list(compiled_bucket_policy.get("bucket_ids", []) or [])
        out["restriction_bucket_selection"] = list(compiled_bucket_policy.get("restriction_ids", []) or [])
        out["operator_access_summary"] = _operator_access_summary(compiled_bucket_policy)
        out["advanced_internal_grants"] = list(compiled_bucket_policy.get("grants", []) or [])
        out["advanced_internal_denies"] = list(compiled_bucket_policy.get("denies", []) or [])
    return out


def _class_status_from_previews(class_preview: dict[str, Any], profile_preview: dict[str, Any]) -> str:
    if agent_class_is_archived(class_preview):
        return "archived"
    lifecycle = str(class_preview.get("lifecycle", "") or "").strip().lower()
    if lifecycle and lifecycle != "stable":
        return lifecycle
    # Schema-v5 classes are governed by their canonical effective authority.
    # The generated Agent Profile is only a temporary compatibility artifact;
    # using its lossy legacy-atom status here can incorrectly label a full ACL
    # as restricted (and make default classes inject a prompt block).
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
    profile_status = str(profile_preview.get("status", "") or "").strip().lower()
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
    if status in {"draft", "restricted"} or lifecycle == "draft":
        warnings.append(EXTERNAL_CONNECTOR_DRAFT_WARNING)
    # Preserve generated profile warnings (narrowed MCP surface, migration caveats).
    for warning in list(profile_preview.get("warnings", []) or []):
        text = str(warning or "").strip()
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
        "capability_bucket_selection": list(generated_by.get("capability_buckets", []) or []) if isinstance(generated_by, dict) else [],
        "restriction_bucket_selection": list(generated_by.get("restriction_buckets", []) or []) if isinstance(generated_by, dict) else [],
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
    preview["prompt"] = _normalized_prompt_mapping(definition.prompt)
    preview["capabilities"] = dict(definition.capabilities or {})
    preview["acl"] = compact_agent_class_acl_preview(definition)
    preview["authority_summary"] = agent_class_authority_summary(definition)
    preview["effective_authority"] = effective_authority_snapshot_for_class(
        definition
    )
    preview["communication"] = dict(definition.communication or {})
    preview["class_warnings"] = list(definition.warnings or [])
    compiled_bucket_policy = _compiled_bucket_policy_for_definition(definition) if agent_class_policy_mode(definition) == "compile" else {}
    preview["purpose"] = definition.description
    preview["capability_bucket_selection"] = list(compiled_bucket_policy.get("bucket_ids", []) or [])
    preview["restriction_bucket_selection"] = list(compiled_bucket_policy.get("restriction_ids", []) or [])
    preview["capability_bucket_summary"] = _operator_access_summary(compiled_bucket_policy) if compiled_bucket_policy else {
        "allowed": [],
        "denied": [],
        "allowed_summary": "Wrapped internal Agent Profile policy",
        "denied_summary": "",
        "bucket_count": 0,
        "restriction_count": 0,
    }
    preview["capability_buckets"] = list(compiled_bucket_policy.get("bucket_previews", []) or [])
    preview["restriction_buckets"] = list(compiled_bucket_policy.get("restriction_previews", []) or [])
    preview["operator_access_summary"] = preview["capability_bucket_summary"]
    preview["agent_profile"] = profile_preview
    preview["compiled_profile"] = profile_preview if agent_class_policy_mode(definition) == "compile" else {}
    preview["internal_profile"] = profile_preview
    preview["internal_policy"] = internal_policy_preview_for_class(definition, profile_preview)
    preview["primary_display_name"] = primary_identity_label_for_class(definition)
    preview["primary_identity_label"] = primary_identity_label_for_class(definition)
    preview["secondary_base_kind_label"] = secondary_base_kind_label_for_class(definition)
    preview["secondary_base_kind_metadata"] = agent_class_runtime_preview(definition)
    preview["status"] = _class_status_from_previews(preview, profile_preview)
    warnings = class_warnings_for_preview(preview, profile_preview)
    for warning in definition.warnings or []:
        text = str(warning or "").strip()
        if text and text not in warnings:
            warnings.append(text)
    preview["warnings"] = warnings
    preview["external_connector_caveat"] = EXTERNAL_CONNECTOR_CAVEAT
    preview["runtime_enforcement"] = "launch_frozen_agent_class_profile_pairing"
    prompt = _normalized_prompt_mapping(definition.prompt)
    prompt_text = _prompt_plain_text(prompt)
    preview["prompt_summary"] = {
        "has_prompt": bool(prompt_text.strip()),
        "char_count": len(prompt_text),
        "preview": prompt_text.strip()[:240],
    }
    preview["restrictions"] = [
        "Agent Class is the operator-facing identity and policy intent.",
        "Agent Profile-compatible internal policy remains the MCP/capability enforcement layer.",
        "Agent Class definitions do not mutate running sessions; changes apply only at launch/relaunch boundaries.",
        "Raw MCP tools, connector governance, generated profile IDs, and arbitrary runtime kinds are not part of normal Agent Class authoring.",
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
    profile_preview: dict[str, Any],
    *,
    assignment_source: str,
    frozen_at: float,
    base_dir: str = "",
) -> dict[str, Any]:
    full_preview = enriched_agent_class_preview(definition, base_dir=base_dir)
    warnings = list(full_preview.get("warnings", []) or [])
    compiled_bucket_policy = _compiled_bucket_policy_for_definition(definition) if agent_class_policy_mode(definition) == "compile" else {}
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
        "agent_profile_ref": definition.agent_profile_ref.as_dict(),
        "agent_profile": compact_agent_profile_preview(profile_preview),
        "internal_policy": internal_policy_preview_for_class(definition, profile_preview),
        "compiled_profile": compact_agent_profile_preview(profile_preview) if agent_class_policy_mode(definition) == "compile" else {},
        "identity": dict(definition.identity or {}),
        "runtime": agent_class_runtime_preview(definition),
        "capabilities": dict(definition.capabilities or {}),
        "acl": compact_agent_class_acl_preview(definition),
        "authority_summary": agent_class_authority_summary(definition),
        "effective_authority": effective_authority_snapshot_for_class(
            definition
        ),
        "capability_bucket_selection": list(compiled_bucket_policy.get("bucket_ids", []) or []),
        "restriction_bucket_selection": list(compiled_bucket_policy.get("restriction_ids", []) or []),
        "capability_bucket_summary": _operator_access_summary(compiled_bucket_policy) if compiled_bucket_policy else {
            "allowed": [],
            "denied": [],
            "allowed_summary": "Wrapped internal Agent Profile policy",
            "denied_summary": "",
            "bucket_count": 0,
            "restriction_count": 0,
        },
        "communication": dict(definition.communication or {}),
        "prompt": _normalized_prompt_mapping(definition.prompt),
        "metadata": dict(definition.metadata or {}),
        "draft": dict(definition.draft or {}),
        "warnings": warnings[:12],
        "external_connector_caveat": EXTERNAL_CONNECTOR_CAVEAT,
        "runtime_enforcement": "launch_frozen_agent_class_profile_pairing",
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
        "acl": dict(snapshot.get("acl") or {}),
        "authority_summary": dict(snapshot.get("authority_summary") or {}),
        "capability_bucket_selection": list(snapshot.get("capability_bucket_selection", []) or []),
        "restriction_bucket_selection": list(snapshot.get("restriction_bucket_selection", []) or []),
        "capability_bucket_summary": dict(snapshot.get("capability_bucket_summary") or {}),
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
    assigned_version = str(getattr(cell, "agent_class_version", "") or "")
    next_launch_class_version = assigned_version if assigned_id else ""
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
            # ``apply_effective_agent_class_for_launch`` resolves the current
            # class definition by id and freezes that latest definition.  Do
            # the same for status: a built-in class can gain a new version
            # while existing agents still store the older assignment version,
            # and operators need to see that the next launch will refresh the
            # frozen class/profile snapshot.
            next_launch_class_version = next_class.version or next_launch_class_version
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
    if direct_profile_without_class:
        legacy_direct_profile_warning = (
            "Legacy direct Agent Profile assignment is active; set a desired Agent Class "
            "for class-first next relaunch. No silent migration is performed."
        )
        warnings.append(legacy_direct_profile_warning)
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
        "next_launch_profile_id": next_launch_profile_id,
        "next_launch_profile_version": next_launch_profile_version,
        "pending_next_launch": pending_next_launch,
        "status": str(effective_preview.get("status", "") or "full"),
        "direct_agent_profile_assignment": direct_profile_without_class,
        "legacy_direct_profile": bool(legacy_direct_profile_warning),
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
        "apply_state": {
            "pending_next_launch": pending_next_launch,
            "relaunch_required": pending_next_launch,
            "mutates_running_sessions": False,
            "applies_at": "next_launch_or_relaunch",
            "effective_class_id": effective_id,
            "next_launch_class_id": next_launch_class_id,
            "direct_agent_profile_assignment": direct_profile_without_class,
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
    data = _normalize_bucket_fields(data)

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
    raw_had_policy = isinstance(data.get("policy"), dict) and bool(data.get("policy"))
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
    prompt = _normalized_prompt_mapping(data.get("prompt", {}))
    if prompt:
        out["prompt"] = prompt
    for key in ("identity", "acl", "capabilities", "communication"):
        value = data.get(key)
        if key == "capabilities" and not value:
            continue
        if isinstance(value, dict) and value:
            out[key] = dict(value)
    if raw_had_policy:
        value = data.get("policy")
        if isinstance(value, dict) and value:
            out["policy"] = dict(value)
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
