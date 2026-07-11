"""Agent profile capability registry, validation, and policy evaluation.

Wave 1 loaded and validated profile YAML as a dry-run registry. Wave 2 keeps
the same registry shape and adds reusable MCP capability evaluation for
synthetic/effective profile contexts. Production callers without an explicit
effective profile assignment still default to the existing full base-kind
behavior; profile policy is only applied when a caller has an effective profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import os
from typing import Any

import yaml

BASE_KINDS = {"architect", "engineer", "worker"}
BUILTIN_PROFILE_DIR = Path(__file__).resolve().parent / "builtin_agent_profiles"
PROJECT_PROFILE_LEAF = "agent_profiles"


@dataclass(frozen=True)
class Capability:
    atom: str
    category: str
    description: str
    risk: str = "normal"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    path: str = ""
    profile_id: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            data["path"] = self.path
        if self.profile_id:
            data["profile_id"] = self.profile_id
        return data


@dataclass
class AgentProfileDefinition:
    id: str
    version: str
    base_kind: str
    display_name: str = ""
    description: str = ""
    lifecycle: str = "stable"
    grants: list[str] = field(default_factory=list)
    denies: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    acl: dict[str, Any] = field(default_factory=dict)
    tool_categories: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    builtin: bool = False

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        source: str = "",
        builtin: bool = False,
    ) -> "AgentProfileDefinition":
        grants = _string_list(data.get("grants"))
        denies = _string_list(data.get("denies"))
        return cls(
            id=str(data.get("id", "") or "").strip(),
            version=str(data.get("version", "") or "").strip(),
            base_kind=str(data.get("base_kind", "") or "").strip(),
            display_name=str(data.get("display_name", "") or "").strip(),
            description=str(data.get("description", "") or "").strip(),
            lifecycle=str(data.get("lifecycle", "stable") or "stable").strip(),
            grants=grants,
            denies=denies,
            policy=dict(data.get("policy") or {}) if isinstance(data.get("policy"), dict) else {},
            acl=dict(data.get("acl") or {}) if isinstance(data.get("acl"), dict) else {},
            tool_categories=(
                dict(data.get("tool_categories") or {})
                if isinstance(data.get("tool_categories"), dict)
                else {}
            ),
            metadata=(
                dict(data.get("metadata") or {})
                if isinstance(data.get("metadata"), dict)
                else {}
            ),
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
            "builtin": self.builtin,
            "metadata": dict(self.metadata or {}),
            "acl": dict(self.acl or {}),
        }


@dataclass(frozen=True)
class AgentProfilePolicy:
    """Effective monotonic capability policy for one caller profile."""

    profile_id: str
    base_kind: str
    grants: frozenset[str]
    denies: frozenset[str] = frozenset()
    acl_mode: str = ""
    allowed_tools: frozenset[str] = frozenset()
    denied_tools: frozenset[str] = frozenset()
    allowed_families: frozenset[str] = frozenset()
    denied_families: frozenset[str] = frozenset()
    profile: AgentProfileDefinition | None = None

    @property
    def is_full_base_kind_profile(self) -> bool:
        ceiling = BASE_KIND_CEILINGS.get(self.base_kind, frozenset())
        return (
            bool(ceiling)
            and self.grants == ceiling
            and not self.denies
            and not self.acl_mode
            and not self.allowed_tools
            and not self.denied_tools
            and not self.allowed_families
            and not self.denied_families
        )

    def allows_all(self, requirements: frozenset[str]) -> bool:
        required = set(requirements)
        if required & set(self.denies):
            return False
        return required.issubset(self.grants)


# Small Wave-1 taxonomy. Atoms are intentionally product-level capabilities, not
# raw MCP tool names; future waves can map them onto exact tool specs for server
# projection/enforcement without changing profile shape.
CAPABILITIES: dict[str, Capability] = {
    "observe.self_context": Capability("observe.self_context", "observe", "Read own context and assignment."),
    "observe.board_summary": Capability("observe.board_summary", "observe", "Read same-group board summaries."),
    "observe.task_detail": Capability("observe.task_detail", "observe", "Read visible task details."),
    "observe.events": Capability("observe.events", "observe", "Read recent events/digests."),
    "observe.mcp_calls": Capability("observe.mcp_calls", "observe", "Read MCP call telemetry."),
    "observe.deploy_state": Capability("observe.deploy_state", "observe", "Read deploy state.", "high"),
    "observe.semantic_recall": Capability("observe.semantic_recall", "observe", "Use semantic recall."),
    "planning.area_read": Capability("planning.area_read", "planning", "Read Areas."),
    "planning.area_write": Capability("planning.area_write", "planning", "Create/update/archive Areas."),
    "planning.initiative_read": Capability("planning.initiative_read", "planning", "Read Initiatives."),
    "planning.initiative_write": Capability("planning.initiative_write", "planning", "Create/update/archive Initiatives."),
    "task.create": Capability("task.create", "task", "Create executable Board tasks.", "high"),
    "task.create_queued": Capability("task.create_queued", "task", "Create queued/non-dispatched planning tasks."),
    "task.update": Capability("task.update", "task", "Update task fields broadly.", "high"),
    "task.update_planning_fields": Capability("task.update_planning_fields", "task", "Update planning-safe task fields."),
    "task.reassign": Capability("task.reassign", "task", "Reassign tasks between engineers.", "high"),
    "task.move": Capability("task.move", "task", "Move tasks across execution lanes.", "high"),
    "task.move_planning_safe": Capability("task.move_planning_safe", "task", "Move own queued tasks among planning-safe lanes."),
    "task.mark_covered": Capability("task.mark_covered", "task", "Mark review coverage."),
    "task.dispatch": Capability("task.dispatch", "task", "Dispatch tasks to agents.", "critical"),
    "task.verify": Capability("task.verify", "task", "Record verification state."),
    "task.complete": Capability("task.complete", "task", "Complete/report own task state."),
    "task.upload_artifact": Capability("task.upload_artifact", "task", "Upload task artifacts."),
    "task.board_sync_read": Capability("task.board_sync_read", "task", "Read visible task board-sync state."),
    "decision.list": Capability("decision.list", "decision", "List visible decisions."),
    "decision.create": Capability("decision.create", "decision", "Create decisions, including accepted where scoped.", "high"),
    "decision.create_proposed": Capability("decision.create_proposed", "decision", "Create proposed PM-owned decisions."),
    "decision.update_proposed": Capability("decision.update_proposed", "decision", "Update PM-owned proposed decisions."),
    "decision.accept": Capability("decision.accept", "decision", "Accept/product-owner approve decisions.", "high"),
    "decision.update": Capability("decision.update", "decision", "Edit/supersede decisions.", "high"),
    "decision.link": Capability("decision.link", "decision", "Link visible decisions to tasks/areas/initiatives."),
    "agent.engineer_roster_read": Capability("agent.engineer_roster_read", "agent", "Read scoped Engineer roster."),
    "agent.hire_engineer": Capability("agent.hire_engineer", "agent", "Request or create Engineer hires.", "critical"),
    "agent.manage_engineer_roster": Capability("agent.manage_engineer_roster", "agent", "Dismiss/rehire/restore/update Engineers.", "critical"),
    "agent.dispatch_worker": Capability("agent.dispatch_worker", "agent", "Launch or route work to Workers.", "critical"),
    "comm.user_ask": Capability("comm.user_ask", "communication", "Ask the user for blocking decisions."),
    "comm.user_message": Capability("comm.user_message", "communication", "Send user-facing messages."),
    "comm.engineer_message": Capability("comm.engineer_message", "communication", "Message Engineers.", "high"),
    "comm.worker_message": Capability("comm.worker_message", "communication", "Message or answer Workers.", "high"),
    "comm.peer_architect_list": Capability("comm.peer_architect_list", "communication", "List peer Architects."),
    "comm.peer_architect_message": Capability("comm.peer_architect_message", "communication", "Message selected peer Architects/product profiles."),
    "comm.product_ack_request": Capability("comm.product_ack_request", "communication", "Require acknowledgement for product-scope decisions."),
    "journal.private": Capability("journal.private", "journal", "Private recovery journal."),
    "journal.read": Capability("journal.read", "journal", "Read scoped journals."),
    "journal.write": Capability("journal.write", "journal", "Write scoped journals."),
    "thinking.read": Capability("thinking.read", "thinking", "Read same-group Scratchpad notes and Mind Maps."),
    "thinking.write_own": Capability("thinking.write_own", "thinking", "Create/update caller-owned Scratchpad notes and Mind Maps."),
    "idea_brief.read": Capability("idea_brief.read", "idea_brief", "Read same-group Idea Brief proposal artifacts."),
    "idea_brief.write_own": Capability("idea_brief.write_own", "idea_brief", "Create/update caller-owned Idea Brief proposal artifacts."),
    "idea_brief.propose": Capability("idea_brief.propose", "idea_brief", "Mark caller-owned Idea Briefs proposed for product-safe review."),
    "memory.read": Capability("memory.read", "memory", "Read shared memory."),
    "memory.publish": Capability("memory.publish", "memory", "Publish shared memory."),
    "memory.admin": Capability("memory.admin", "memory", "Pin/link/unpin shared memory.", "high"),
    "worktree.read": Capability("worktree.read", "worktree", "Read worktree metadata/diff."),
    "worktree.merge": Capability("worktree.merge", "worktree", "Merge or apply worktree changes.", "critical"),
    "deploy.apply": Capability("deploy.apply", "deploy", "Deploy, restart, or mutate live services.", "critical"),
    "admin.settings": Capability("admin.settings", "admin", "Change global/team/runtime settings.", "critical"),
    "profile.assign": Capability("profile.assign", "profile_admin", "Assign agent profiles.", "critical"),
    "profile.edit": Capability("profile.edit", "profile_admin", "Create/edit trusted profile definitions.", "critical"),
    "behavior_overlay.read": Capability(
        "behavior_overlay.read",
        "behavior_overlay",
        "Read visible Dynamic Behavior overlay state.",
    ),
    "behavior_overlay.propose_self": Capability(
        "behavior_overlay.propose_self",
        "behavior_overlay",
        "Propose user-approved changes to this agent's own Dynamic Behavior overlay.",
    ),
}

WORKER_CEILING = frozenset({
    "observe.self_context",
    "observe.task_detail",
    "planning.area_read",
    "task.complete",
    "task.verify",
    "task.upload_artifact",
    "comm.user_ask",
    "comm.user_message",
    "journal.private",
    "memory.read",
    "memory.publish",
})

ENGINEER_CEILING = frozenset(WORKER_CEILING | {
    "observe.board_summary",
    "observe.events",
    "observe.mcp_calls",
    "observe.semantic_recall",
    "planning.initiative_read",
    "task.create",
    "task.update",
    "task.reassign",
    "task.move",
    "task.mark_covered",
    "task.dispatch",
    "task.board_sync_read",
    "decision.list",
    "decision.create",
    "decision.update",
    "decision.link",
    "agent.dispatch_worker",
    "comm.worker_message",
    "comm.engineer_message",
    "journal.read",
    "journal.write",
    "memory.admin",
    "worktree.read",
    "worktree.merge",
})

ARCHITECT_CEILING = frozenset(ENGINEER_CEILING | {
    "observe.deploy_state",
    "planning.area_write",
    "planning.initiative_write",
    "decision.accept",
    "agent.engineer_roster_read",
    "agent.hire_engineer",
    "agent.manage_engineer_roster",
    "comm.peer_architect_list",
    "comm.peer_architect_message",
    "comm.product_ack_request",
    "deploy.apply",
    "admin.settings",
    "profile.assign",
    "profile.edit",
    "task.create_queued",
    "task.update_planning_fields",
    "task.move_planning_safe",
    "decision.create_proposed",
    "decision.update_proposed",
    "thinking.read",
    "thinking.write_own",
    "idea_brief.read",
    "idea_brief.write_own",
    "idea_brief.propose",
    "behavior_overlay.read",
    "behavior_overlay.propose_self",
})

BASE_KIND_CEILINGS = {
    "worker": WORKER_CEILING,
    "engineer": ENGINEER_CEILING,
    "architect": ARCHITECT_CEILING,
}

BUILTIN_PROFILE_BASE_KIND = {
    "full-architect": "architect",
    "full-engineer": "engineer",
    "full-worker": "worker",
}

HIGH_RISK_CAPABILITIES = frozenset(
    atom for atom, cap in CAPABILITIES.items() if cap.risk in {"high", "critical"}
)

KNOWN_PROFILE_KEYS = {
    "id",
    "version",
    "base_kind",
    "display_name",
    "description",
    "lifecycle",
    "grants",
    "denies",
    "policy",
    "acl",
    "tool_categories",
    "metadata",
}

CONFUSING_PROFILE_KEYS = {
    "profile",
    "agent_profile",
    "agent_cell_profile",
    "runtime_profile",
}

TOOL_CATEGORY_REQUIREMENTS: dict[str, frozenset[str]] = {
    "context_read": frozenset({"observe.self_context"}),
    "planning_reads": frozenset({
        "observe.board_summary",
        "observe.task_detail",
        "planning.area_read",
        "planning.initiative_read",
        "decision.list",
    }),
    "planning_writes": frozenset({"planning.area_write", "planning.initiative_write"}),
    "pm_decisions": frozenset({"decision.create_proposed", "decision.update_proposed", "decision.link", "decision.list"}),
    "pm_queued_tasks": frozenset({
        "task.create_queued",
        "task.update_planning_fields",
        "task.move_planning_safe",
    }),
    "execution_task_control": frozenset({"task.create", "task.update", "task.move", "task.dispatch"}),
    "engineer_roster": frozenset({"agent.engineer_roster_read", "agent.hire_engineer", "agent.manage_engineer_roster"}),
    "worker_dispatch": frozenset({"agent.dispatch_worker", "task.dispatch"}),
    "peer_architect_comm": frozenset({"comm.peer_architect_list", "comm.peer_architect_message"}),
    "engineer_worker_comm": frozenset({"comm.engineer_message", "comm.worker_message"}),
    "worktree_merge": frozenset({"worktree.merge"}),
    "deploy_admin": frozenset({"deploy.apply", "admin.settings"}),
    "profile_admin": frozenset({"profile.assign", "profile.edit"}),
    "behavior_overlay_self": frozenset({"behavior_overlay.read", "behavior_overlay.propose_self"}),
    "thinking_reads": frozenset({"thinking.read"}),
    "thinking_writes": frozenset({"thinking.read", "thinking.write_own"}),
    "idea_briefs": frozenset({"idea_brief.read", "idea_brief.write_own", "idea_brief.propose"}),
}




def _string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _find_project_dir(base_dir: str = "") -> Path | None:
    d = Path(os.path.expanduser(base_dir) if base_dir else os.getcwd())
    if not d.is_dir():
        d = Path(os.getcwd())
    for _ in range(20):
        candidate = d / ".torque" / PROJECT_PROFILE_LEAF
        if candidate.is_dir():
            return candidate
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


def find_agent_profile_dirs(base_dir: str = "", *, include_builtin: bool = True) -> list[tuple[Path, bool]]:
    dirs: list[tuple[Path, bool]] = []
    project_dir = _find_project_dir(base_dir)
    if project_dir:
        dirs.append((project_dir, False))
    if include_builtin and BUILTIN_PROFILE_DIR.is_dir():
        dirs.append((BUILTIN_PROFILE_DIR, True))
    return dirs


def _iter_yaml_paths(root_dir: Path) -> list[Path]:
    if not root_dir.is_dir():
        return []
    return sorted(
        path for path in root_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )


def load_profile_yaml(path: Path) -> tuple[dict[str, Any] | None, ValidationIssue | None]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, ValidationIssue(
            "error",
            "malformed_yaml",
            f"profile YAML is malformed: {exc}",
            path=str(path),
        )
    if not isinstance(data, dict):
        return None, ValidationIssue(
            "error",
            "profile_not_mapping",
            "profile YAML must be a mapping",
            path=str(path),
        )
    return data, None


def validate_profile_data(
    data: dict[str, Any],
    *,
    source: str = "",
    builtin: bool = False,
) -> tuple[AgentProfileDefinition | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    profile_id = str(data.get("id", "") or "").strip()

    unknown_keys = sorted(set(data) - KNOWN_PROFILE_KEYS)
    confusing = sorted(set(data) & CONFUSING_PROFILE_KEYS)
    if confusing:
        issues.append(ValidationIssue(
            "error",
            "agent_cell_profile_confusion",
            "agent profile definitions must not use AgentCell.profile/runtime profile fields: "
            + ", ".join(confusing),
            path=source,
            profile_id=profile_id,
        ))
    non_confusing_unknown = [key for key in unknown_keys if key not in CONFUSING_PROFILE_KEYS]
    if non_confusing_unknown:
        issues.append(ValidationIssue(
            "error",
            "unknown_profile_fields",
            "unknown profile fields: " + ", ".join(non_confusing_unknown),
            path=source,
            profile_id=profile_id,
        ))

    if not profile_id:
        issues.append(ValidationIssue("error", "missing_profile_id", "profile id is required", path=source))
    version = str(data.get("version", "") or "").strip()
    if not version:
        issues.append(ValidationIssue(
            "error", "missing_profile_version", "profile version is required", path=source, profile_id=profile_id
        ))
    base_kind = str(data.get("base_kind", "") or "").strip()
    if base_kind not in BASE_KINDS:
        issues.append(ValidationIssue(
            "error",
            "invalid_base_kind",
            f"base_kind must be one of {', '.join(sorted(BASE_KINDS))}",
            path=source,
            profile_id=profile_id,
        ))

    if not isinstance(data.get("grants", []), list):
        issues.append(ValidationIssue(
            "error", "grants_not_list", "grants must be a list of capability atoms", path=source, profile_id=profile_id
        ))
    if not isinstance(data.get("denies", []), list):
        issues.append(ValidationIssue(
            "error", "denies_not_list", "denies must be a list of capability atoms", path=source, profile_id=profile_id
        ))
    if "policy" in data and not isinstance(data.get("policy"), dict):
        issues.append(ValidationIssue(
            "error", "policy_not_mapping", "policy must be a mapping", path=source, profile_id=profile_id
        ))
    if "acl" in data and not isinstance(data.get("acl"), dict):
        issues.append(ValidationIssue(
            "error", "acl_not_mapping", "acl must be a mapping", path=source, profile_id=profile_id
        ))
    if "metadata" in data and not isinstance(data.get("metadata"), dict):
        issues.append(ValidationIssue(
            "error", "metadata_not_mapping", "metadata must be a mapping", path=source, profile_id=profile_id
        ))

    profile = AgentProfileDefinition.from_dict(data, source=source, builtin=builtin)
    grants = set(profile.grants)
    denies = set(profile.denies)
    known_atoms = set(CAPABILITIES)
    unknown_atoms = sorted((grants | denies) - known_atoms)
    if unknown_atoms:
        issues.append(ValidationIssue(
            "error",
            "unknown_capability_atoms",
            "unknown capability atoms: " + ", ".join(unknown_atoms),
            path=source,
            profile_id=profile.id,
        ))

    if base_kind in BASE_KIND_CEILINGS:
        outside = sorted(grants - BASE_KIND_CEILINGS[base_kind])
        if outside:
            issues.append(ValidationIssue(
                "error",
                "grants_outside_base_kind_ceiling",
                f"grants outside {base_kind} ceiling: " + ", ".join(outside),
                path=source,
                profile_id=profile.id,
            ))

    expected_kind = BUILTIN_PROFILE_BASE_KIND.get(profile.id)
    if expected_kind and base_kind and base_kind != expected_kind:
        issues.append(ValidationIssue(
            "error",
            "profile_base_kind_mismatch",
            f"profile {profile.id} must use base_kind={expected_kind}, got {base_kind}",
            path=source,
            profile_id=profile.id,
        ))
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    policy_base_kind = str((policy or {}).get("base_kind", "") or "").strip()
    if policy_base_kind and base_kind and policy_base_kind != base_kind:
        issues.append(ValidationIssue(
            "error",
            "profile_base_kind_mismatch",
            f"policy.base_kind={policy_base_kind} does not match base_kind={base_kind}",
            path=source,
            profile_id=profile.id,
        ))

    if profile.id.startswith("full-") and base_kind in BASE_KIND_CEILINGS:
        missing = sorted(BASE_KIND_CEILINGS[base_kind] - grants)
        extra = sorted(grants - BASE_KIND_CEILINGS[base_kind])
        if missing or extra:
            issues.append(ValidationIssue(
                "error",
                "full_profile_not_equal_base_ceiling",
                "full profiles must equal their base-kind ceiling; "
                f"missing={', '.join(missing) or '-'} extra={', '.join(extra) or '-'}",
                path=source,
                profile_id=profile.id,
            ))

    if any(issue.severity == "error" for issue in issues):
        return None, issues
    return profile, issues


def load_agent_profiles(base_dir: str = "") -> tuple[list[AgentProfileDefinition], list[ValidationIssue]]:
    profiles: list[AgentProfileDefinition] = []
    issues: list[ValidationIssue] = []
    seen: dict[str, AgentProfileDefinition] = {}
    for root, builtin in find_agent_profile_dirs(base_dir=base_dir):
        for path in _iter_yaml_paths(root):
            data, load_issue = load_profile_yaml(path)
            if load_issue:
                issues.append(load_issue)
                continue
            assert data is not None
            profile, validation_issues = validate_profile_data(
                data,
                source=str(path),
                builtin=builtin,
            )
            issues.extend(validation_issues)
            if not profile:
                continue
            if profile.id in seen:
                issues.append(ValidationIssue(
                    "error",
                    "duplicate_profile_id",
                    f"duplicate profile id {profile.id}; first defined at {seen[profile.id].source}",
                    path=str(path),
                    profile_id=profile.id,
                ))
                continue
            profiles.append(profile)
            seen[profile.id] = profile
    return sorted(profiles, key=lambda item: (item.builtin, item.id)), issues


@lru_cache(maxsize=32)
def _valid_profile_lookup(base_dir: str = "") -> tuple[dict[str, AgentProfileDefinition], tuple[ValidationIssue, ...]]:
    profiles, issues = load_agent_profiles(base_dir=base_dir)
    return {profile.id: profile for profile in profiles}, tuple(issues)


def _profile_dict_with_preview_grants(data: dict[str, Any]) -> dict[str, Any]:
    """Return profile-like data with grants reconstructed from preview snapshots.

    Frozen effective launch snapshots intentionally store preview/audit data so a
    running session is not re-bound to a changed on-disk profile definition.
    Those previews expose granted atoms as ``capabilities[].atom`` rather than a
    raw ``grants`` list; reconstruct grants from that frozen list for MCP policy
    projection without doing a live profile lookup.
    """

    if data.get("grants"):
        return data
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list):
        return data
    grants: list[str] = []
    seen: set[str] = set()
    for item in capabilities:
        atom = ""
        if isinstance(item, dict):
            atom = str(item.get("atom", "") or "").strip()
        else:
            atom = str(item or "").strip()
        if atom and atom not in seen:
            grants.append(atom)
            seen.add(atom)
    if not grants:
        return data
    enriched = dict(data)
    enriched["grants"] = grants
    return enriched


def profile_policy_from_definition(profile: AgentProfileDefinition | dict[str, Any]) -> AgentProfilePolicy:
    if isinstance(profile, dict):
        data = _profile_dict_with_preview_grants(profile)
        if isinstance(data.get("acl"), dict):
            data = dict(data)
            policy_data = dict(data.get("policy") or {}) if isinstance(data.get("policy"), dict) else {}
            policy_data.setdefault("acl", dict(data.get("acl") or {}))
            data["policy"] = policy_data
        profile = AgentProfileDefinition.from_dict(data)
    ceiling = BASE_KIND_CEILINGS.get(profile.base_kind, frozenset())
    grants = frozenset(set(profile.grants) & set(ceiling))
    denies = frozenset(set(profile.denies) & set(ceiling))
    policy_data = profile.policy if isinstance(profile.policy, dict) else {}
    acl = profile.acl if isinstance(getattr(profile, "acl", {}), dict) and profile.acl else {}
    if not acl:
        acl = policy_data.get("acl") if isinstance(policy_data.get("acl"), dict) else {}
    if not acl and isinstance(profile.metadata, dict):
        acl = profile.metadata.get("agent_class_acl") if isinstance(profile.metadata.get("agent_class_acl"), dict) else {}
    acl_mode = str((acl or {}).get("mode", "") or "").strip().lower()
    allowed_tools = frozenset(str(item or "").strip() for item in (acl or {}).get("allowed_tools", []) or [] if str(item or "").strip())
    denied_tools = frozenset(str(item or "").strip() for item in (acl or {}).get("denied_tools", []) or [] if str(item or "").strip())
    allowed_families = frozenset(str(item or "").strip() for item in (acl or {}).get("allowed_families", []) or [] if str(item or "").strip())
    denied_families = frozenset(str(item or "").strip() for item in (acl or {}).get("denied_families", []) or [] if str(item or "").strip())
    return AgentProfilePolicy(
        profile_id=profile.id,
        base_kind=profile.base_kind,
        grants=frozenset(set(grants) - set(denies)),
        denies=denies,
        acl_mode=acl_mode,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        allowed_families=allowed_families,
        denied_families=denied_families,
        profile=profile,
    )


def profile_policy_by_id(profile_id: str, *, base_dir: str = "") -> AgentProfilePolicy | None:
    profile_id = str(profile_id or "").strip()
    if not profile_id:
        return None
    profiles_by_id, issues = _valid_profile_lookup(base_dir or "")
    if any(issue.severity == "error" for issue in issues):
        return None
    profile = profiles_by_id.get(profile_id)
    if not profile:
        return None
    return profile_policy_from_definition(profile)




def dry_run_profile_preview(profile: AgentProfileDefinition | dict[str, Any]) -> dict[str, Any]:
    if isinstance(profile, dict):
        profile = AgentProfileDefinition.from_dict(profile)
    grants = set(profile.grants)
    ceiling = BASE_KIND_CEILINGS.get(profile.base_kind, frozenset())
    denied_high_risk = sorted((ceiling & HIGH_RISK_CAPABILITIES) - grants)
    capabilities = [
        {
            "atom": atom,
            "category": CAPABILITIES[atom].category,
            "risk": CAPABILITIES[atom].risk,
        }
        for atom in sorted(grants)
        if atom in CAPABILITIES
    ]
    category_preview = []
    for category, required in sorted(TOOL_CATEGORY_REQUIREMENTS.items()):
        missing = sorted(required - grants)
        category_preview.append({
            "category": category,
            "status": "allowed" if not missing else "denied",
            "missing_capabilities": missing,
        })

    policy = dict(profile.policy or {})
    acl_policy = dict(profile.acl or {}) if isinstance(getattr(profile, "acl", {}), dict) and profile.acl else {}
    if not acl_policy:
        acl_policy = dict(policy.get("acl") or {}) if isinstance(policy.get("acl"), dict) else {}

    return {
        **profile.as_preview_dict(),
        "capability_count": len(capabilities),
        "grants": sorted(atom for atom in grants if atom in CAPABILITIES),
        "denies": sorted(atom for atom in set(profile.denies) if atom in CAPABILITIES),
        "capabilities": capabilities,
        "denied_high_risk_capabilities": denied_high_risk,
        "acl": acl_policy,
        "projected_tool_categories": category_preview,
        "runtime_enforcement": "mcp_projection_when_effective_profile_is_set",
    }



def profile_definition_by_id(profile_id: str, *, base_dir: str = "") -> AgentProfileDefinition | None:
    """Return a validated profile definition by id without creating a policy."""

    profile_id = str(profile_id or "").strip()
    if not profile_id:
        return None
    profiles_by_id, issues = _valid_profile_lookup(base_dir or "")
    if any(issue.severity == "error" for issue in issues):
        return None
    return profiles_by_id.get(profile_id)


def default_full_profile_id_for_kind(kind: str) -> str:
    kind = str(kind or "").strip()
    if kind in BASE_KINDS:
        return f"full-{kind}"
    return ""


def _profile_status_from_preview(preview: dict[str, Any]) -> str:
    lifecycle = str(preview.get("lifecycle", "") or "").strip().lower()
    if lifecycle and lifecycle != "stable":
        return lifecycle
    denied = list(preview.get("denied_high_risk_capabilities", []) or [])
    ceiling = BASE_KIND_CEILINGS.get(str(preview.get("base_kind", "") or ""), frozenset())
    if denied or int(preview.get("capability_count", 0) or 0) < len(ceiling):
        return "restricted"
    return "full"


def preview_warnings_for_profile_preview(preview: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    profile_id = str(preview.get("id", "") or "").strip()
    lifecycle = str(preview.get("lifecycle", "") or "").strip().lower()
    status = _profile_status_from_preview(preview)
    if lifecycle and lifecycle != "stable":
        warnings.append(
            f"{profile_id or 'profile'} is lifecycle={lifecycle}; use only for preview/testing unless explicitly approved."
        )
    if status in {"draft", "restricted"} or preview.get("denied_high_risk_capabilities"):
        warnings.append(
            "This profile narrows the base kind and hides/denies MCP tools before side effects."
        )
    return warnings


def enriched_profile_preview(profile: AgentProfileDefinition | dict[str, Any]) -> dict[str, Any]:
    preview = dry_run_profile_preview(profile)
    preview["status"] = _profile_status_from_preview(preview)
    preview["warnings"] = preview_warnings_for_profile_preview(preview)
    return preview


def agent_profile_cell_status(cell: Any, *, base_dir: str = "") -> dict[str, Any]:
    """Return display/audit status for one AgentCell without changing policy."""

    kind = str(getattr(cell, "kind", "") or "").strip()
    assigned_id = str(getattr(cell, "agent_profile_id", "") or "").strip()
    effective_id = str(getattr(cell, "effective_agent_profile_id", "") or "").strip()
    snapshot = getattr(cell, "effective_agent_profile_snapshot", {}) or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    effective_preview = dict(snapshot) if snapshot.get("id") else {}
    if not effective_preview:
        default_id = default_full_profile_id_for_kind(kind)
        default_profile = profile_definition_by_id(default_id, base_dir=base_dir) if default_id else None
        if default_profile:
            effective_preview = enriched_profile_preview(default_profile)
            effective_preview["assignment_source"] = "implicit_default_full_base_kind"
            effective_id = default_profile.id
    else:
        effective_preview.setdefault("status", _profile_status_from_preview(effective_preview))
        effective_preview.setdefault("warnings", preview_warnings_for_profile_preview(effective_preview))
    assigned_preview = {}
    if assigned_id:
        assigned_profile = profile_definition_by_id(assigned_id, base_dir=base_dir)
        if assigned_profile:
            assigned_preview = enriched_profile_preview(assigned_profile)

    class_assigned_id = str(getattr(cell, "agent_class_id", "") or "").strip()
    effective_class_id = str(getattr(cell, "effective_agent_class_id", "") or "").strip()
    class_next_profile_id = ""
    class_next_profile_version = ""
    if class_assigned_id:
        try:
            from .agent_classes import agent_class_definition_by_id
            definition = agent_class_definition_by_id(class_assigned_id, base_dir=base_dir)
            if definition:
                class_next_profile_id = definition.agent_profile_ref.id
                class_next_profile_version = definition.agent_profile_ref.version
        except Exception:
            class_next_profile_id = ""
            class_next_profile_version = ""
    next_launch_profile_id = (
        class_next_profile_id
        or assigned_id
        or default_full_profile_id_for_kind(kind)
    )
    next_launch_profile_version = (
        class_next_profile_version
        or (str(getattr(cell, "agent_profile_version", "") or "") if assigned_id else "")
    )
    if next_launch_profile_id and not next_launch_profile_version:
        next_profile = profile_definition_by_id(next_launch_profile_id, base_dir=base_dir)
        if next_profile:
            next_launch_profile_version = next_profile.version
    effective_version = str(
        getattr(cell, "effective_agent_profile_version", "")
        or effective_preview.get("version", "")
        or ""
    )
    pending_next_launch = bool(
        next_launch_profile_id
        and (next_launch_profile_id != effective_id
             or (next_launch_profile_version
                 and next_launch_profile_version != effective_version))
    )
    warnings = list(effective_preview.get("warnings", []) or [])
    if assigned_id and not class_assigned_id:
        warnings.append(
            "Legacy direct Agent Profile assignment is active; set a desired Agent Class when using the class-first flow."
        )
    # Deduplicate while preserving order.
    deduped_warnings: list[str] = []
    seen_warnings: set[str] = set()
    for warning in warnings:
        text = str(warning or "").strip()
        if text and text not in seen_warnings:
            deduped_warnings.append(text)
            seen_warnings.add(text)
    return {
        "agent_id": str(getattr(cell, "id", "") or ""),
        "agent_name": str(getattr(cell, "name", "") or ""),
        "base_kind": kind,
        "assigned_profile_id": assigned_id,
        "assigned_profile_version": str(getattr(cell, "agent_profile_version", "") or ""),
        "assigned_at": float(getattr(cell, "agent_profile_assigned_at", 0) or 0),
        "assigned_by": str(getattr(cell, "agent_profile_assigned_by", "") or ""),
        "effective_profile_id": effective_id,
        "effective_profile_version": effective_version,
        "effective_applied_at": float(getattr(cell, "effective_agent_profile_applied_at", 0) or 0),
        "effective_profile": effective_preview,
        "assigned_profile": assigned_preview,
        "next_launch_profile_id": next_launch_profile_id,
        "next_launch_profile_version": next_launch_profile_version,
        "pending_next_launch": pending_next_launch,
        "status": str(effective_preview.get("status", "") or "full"),
        "warnings": deduped_warnings,
        "legacy_direct_profile": bool(assigned_id and not class_assigned_id),
        "denied_high_risk_capabilities": list(effective_preview.get("denied_high_risk_capabilities", []) or []),
    }

def built_in_full_profile_ids() -> list[str]:
    return ["full-architect", "full-engineer", "full-worker"]


def validate_all_agent_profiles(base_dir: str = "") -> dict[str, Any]:
    profiles, issues = load_agent_profiles(base_dir=base_dir)
    errors = [issue for issue in issues if issue.severity == "error"]
    return {
        "profiles": profiles,
        "issues": issues,
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len([issue for issue in issues if issue.severity == "warn"]),
        "profile_count": len(profiles),
    }
