"""Canonical Agent Class capability catalog.

Capability ids are the only public ACL actions in schema v5.  The catalog is
platform-owned and class-agnostic: built-in and custom classes select from the
same definitions, and no entry refers to a named Agent Class.

Scope ceilings are deliberately conservative.  They describe authority that
the current runtime can enforce, not possible future behavior.  A later
handler migration may widen a ceiling only together with resource-level tests.
"""

from __future__ import annotations

import re
from typing import Iterable

from .mcp_authority import (
    CapabilityDefinition,
    RISK_LEVELS,
    SCOPE_ORDER,
    SCOPE_RANK,
)


BASE_KINDS = frozenset({"worker", "engineer", "architect"})
CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def _unscoped(
    capability_id: str,
    label: str,
    description: str,
    *,
    base_kinds: Iterable[str],
    risk: str = "normal",
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        label=label,
        description=description,
        risk=risk,
        base_kinds=frozenset(base_kinds),
    )


def _scoped(
    capability_id: str,
    label: str,
    description: str,
    *,
    ceilings: dict[str, str],
    scopes: Iterable[str] = SCOPE_ORDER,
    risk: str = "normal",
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        label=label,
        description=description,
        risk=risk,
        scopes=tuple(scopes),
        ceilings=dict(ceilings),
    )


_ALL = BASE_KINDS
_ARCHITECT = frozenset({"architect"})
_ENGINEER_ARCHITECT = frozenset({"engineer", "architect"})


CAPABILITY_CATALOG: dict[str, CapabilityDefinition] = {
    definition.id: definition
    for definition in (
        _scoped(
            "self.read",
            "Read own context",
            "Read caller identity, session, assignment, and own context.",
            scopes=("self",),
            ceilings={kind: "self" for kind in _ALL},
        ),
        _unscoped(
            "help.read",
            "Read help",
            "List, search, and read maintained Torque help documentation.",
            base_kinds=_ALL,
        ),
        _unscoped(
            "tool.search",
            "Search visible tools",
            "Search the already-projected MCP tool surface.",
            base_kinds=_ENGINEER_ARCHITECT,
        ),
        _scoped(
            "board.read",
            "Read board summaries",
            "Read board-level summaries and orchestration rollups.",
            scopes=("group",),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "event.read",
            "Read recent events",
            "Read recent activity, recovery summaries, and event streams.",
            scopes=("group",),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "telemetry.read",
            "Read MCP telemetry",
            "Read MCP calls and related operational telemetry.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "children", "architect": "group"},
        ),
        _scoped(
            "semantic_recall.read",
            "Use semantic recall",
            "Read semantically recalled context within existing visibility rules.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _unscoped(
            "deploy.read",
            "Read deploy state",
            "Read sensitive process-wide deployment state.",
            base_kinds=_ARCHITECT,
            risk="high",
        ),
        _scoped(
            "task.read",
            "Read tasks",
            "Read task lists, task detail, chains, and task-linked context.",
            scopes=("self", "children", "group"),
            ceilings={
                "worker": "self",
                "engineer": "group",
                "architect": "group",
            },
        ),
        _scoped(
            "task.report",
            "Report task status",
            "Report progress, blockers, errors, readiness, and completion.",
            scopes=("self",),
            ceilings={kind: "self" for kind in _ALL},
        ),
        _scoped(
            "task.create",
            "Create executable tasks",
            "Create executable Board tasks inside the caller's routing ceiling.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "children", "architect": "children"},
            risk="high",
        ),
        _scoped(
            "task.propose",
            "Propose queued tasks",
            "Create queued, unassigned, non-dispatched task proposals.",
            scopes=("self",),
            ceilings={"architect": "self"},
        ),
        _scoped(
            "task.update",
            "Update tasks",
            "Update task fields within the caller's task ownership ceiling.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "children", "architect": "self"},
            risk="high",
        ),
        _scoped(
            "task.reassign",
            "Reassign tasks",
            "Change task ownership within the caller's routing ceiling.",
            scopes=("children", "group"),
            ceilings={"engineer": "children", "architect": "children"},
            risk="high",
        ),
        _scoped(
            "task.move",
            "Move tasks",
            "Move tasks across Board lanes within the caller's task ceiling.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "children", "architect": "self"},
            risk="high",
        ),
        _scoped(
            "task.mark_covered",
            "Mark task coverage",
            "Record review or root-task coverage without dispatching work.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "group", "architect": "self"},
        ),
        _scoped(
            "task.dispatch",
            "Dispatch tasks",
            "Dispatch executable tasks to eligible Workers.",
            scopes=("children",),
            ceilings={"engineer": "children"},
            risk="critical",
        ),
        _scoped(
            "task.verify",
            "Verify tasks",
            "Record verification evidence for visible tasks.",
            scopes=("self", "children", "group"),
            ceilings={
                "worker": "self",
                "engineer": "group",
                "architect": "self",
            },
        ),
        _scoped(
            "task.artifact.write",
            "Attach task artifacts",
            "Upload or attach artifacts to a visible task.",
            scopes=("self", "children", "group"),
            ceilings={
                "worker": "self",
                "engineer": "group",
                "architect": "self",
            },
        ),
        _scoped(
            "task.board_sync.read",
            "Read task board sync",
            "Read external-board synchronization state for visible tasks.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "planning.area.read",
            "Read Areas",
            "Read Planning Areas and filtered Area-linked context.",
            scopes=("group",),
            ceilings={kind: "group" for kind in _ALL},
        ),
        _scoped(
            "planning.area.write",
            "Write Areas",
            "Create, update, archive, link, and annotate writable Areas.",
            scopes=("self", "group"),
            ceilings={"architect": "self"},
            risk="high",
        ),
        _scoped(
            "planning.initiative.read",
            "Read Initiatives",
            "Read same-group product Initiatives.",
            scopes=("group",),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "planning.initiative.write",
            "Write Initiatives",
            "Create, update, archive, and link Initiatives.",
            scopes=("group",),
            ceilings={"architect": "group"},
            risk="high",
        ),
        _scoped(
            "decision.read",
            "Read decisions",
            "Read decisions visible to the caller.",
            scopes=("self", "group"),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "decision.propose",
            "Propose decisions",
            "Create and update caller-owned proposed decisions.",
            scopes=("self",),
            ceilings={"architect": "self"},
        ),
        _scoped(
            "decision.create",
            "Create decisions",
            "Create ordinary durable decisions within the caller's ownership scope.",
            scopes=("self",),
            ceilings={"architect": "self"},
            risk="high",
        ),
        _scoped(
            "decision.update",
            "Update decisions",
            "Edit or supersede caller-owned durable decisions.",
            scopes=("self",),
            ceilings={"architect": "self"},
            risk="high",
        ),
        _scoped(
            "decision.accept",
            "Accept decisions",
            "Accept, approve, reject, or revise decisions as durable authority.",
            scopes=("self", "group"),
            ceilings={"architect": "self"},
            risk="high",
        ),
        _scoped(
            "decision.link",
            "Link decisions",
            "Link visible decisions to tasks, Areas, and Initiatives.",
            scopes=("self", "group"),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "engineer.roster.read",
            "Read Engineer roster",
            "Read Engineers visible in the caller's group.",
            scopes=("children", "group"),
            ceilings={"architect": "group"},
        ),
        _scoped(
            "engineer.hire",
            "Hire Engineers",
            "Request or create direct-child Engineer hires.",
            scopes=("children",),
            ceilings={"architect": "children"},
            risk="critical",
        ),
        _scoped(
            "engineer.manage",
            "Manage Engineers",
            "Dismiss, rehire, restore, or update direct-child Engineers.",
            scopes=("children",),
            ceilings={"architect": "children"},
            risk="critical",
        ),
        _scoped(
            "worker.manage",
            "Manage Workers",
            "Launch, close, relaunch, or configure direct-child Workers.",
            scopes=("children",),
            ceilings={"engineer": "children"},
            risk="critical",
        ),
        _scoped(
            "message.user",
            "Message the user",
            "Ask, message, reply to, or close a loop with the owning user.",
            scopes=("self",),
            ceilings={kind: "self" for kind in _ALL},
        ),
        _scoped(
            "message.engineer",
            "Message Engineers",
            "List, inspect, message, and reply to eligible Engineers.",
            scopes=("children", "group", "global"),
            ceilings={"engineer": "group", "architect": "children"},
            risk="high",
        ),
        _scoped(
            "message.worker",
            "Message Workers",
            "Message, inspect questions from, or answer eligible Workers.",
            scopes=("children", "group"),
            ceilings={"engineer": "children", "architect": "children"},
            risk="high",
        ),
        _scoped(
            "message.architect_peer",
            "Message peer Architects",
            "List, inspect, message, and reply to peer Architects.",
            scopes=("group", "global"),
            ceilings={"architect": "group"},
        ),
        _scoped(
            "message.ack_required",
            "Require acknowledgement",
            "Mark eligible peer communication as requiring acknowledgement.",
            scopes=("group",),
            ceilings={"architect": "group"},
        ),
        _scoped(
            "journal.private",
            "Use private journal",
            "Read and write the caller's private recovery journal.",
            scopes=("self",),
            ceilings={kind: "self" for kind in _ALL},
        ),
        _scoped(
            "journal.read",
            "Read scoped journals",
            "Read journals visible through the caller's orchestration scope.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "group", "architect": "children"},
        ),
        _scoped(
            "journal.write",
            "Write scoped journals",
            "Write notes or journal entries within the caller's scope.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "self", "architect": "self"},
        ),
        _scoped(
            "memory.read",
            "Read shared memory",
            "List and read shared-memory entries visible to the caller.",
            scopes=("group",),
            ceilings={kind: "group" for kind in _ALL},
        ),
        _scoped(
            "memory.write",
            "Publish shared memory",
            "Publish shared-memory entries to the caller's group.",
            scopes=("group",),
            ceilings={kind: "group" for kind in _ALL},
        ),
        _scoped(
            "memory.admin",
            "Administer shared memory",
            "Pin, link, and unpin shared-memory entries.",
            scopes=("group",),
            ceilings={kind: "group" for kind in _ALL},
            risk="high",
        ),
        _scoped(
            "worktree.read",
            "Read worktrees",
            "Read worktree status and diffs for controlled work.",
            scopes=("self", "children"),
            ceilings={"engineer": "children", "architect": "children"},
        ),
        _scoped(
            "worktree.merge",
            "Merge and manage worktrees",
            "Merge, rebase, publish, checkpoint, adopt, or remove worktrees.",
            scopes=("self", "children"),
            ceilings={"engineer": "children", "architect": "children"},
            risk="critical",
        ),
        _unscoped(
            "deploy.apply",
            "Apply deployments",
            "Deploy, restart, or otherwise mutate live runtime state.",
            base_kinds=_ARCHITECT,
            risk="critical",
        ),
        _unscoped(
            "settings.admin",
            "Administer settings",
            "Change process-wide, group, or runtime settings.",
            base_kinds=_ARCHITECT,
            risk="critical",
        ),
        _unscoped(
            "class_profile.admin",
            "Administer classes and profiles",
            "Assign or edit Agent Class/Profile authority definitions.",
            base_kinds=_ARCHITECT,
            risk="critical",
        ),
        _scoped(
            "behavior_overlay.read",
            "Read behavior overlays",
            "Read visible Dynamic Behavior overlay state and versions.",
            scopes=("self", "children", "group"),
            ceilings={"engineer": "self", "architect": "group"},
        ),
        _scoped(
            "behavior_overlay.propose",
            "Propose behavior overlays",
            "Propose or request changes to the caller's own behavior overlay.",
            scopes=("self",),
            ceilings={"engineer": "self", "architect": "self"},
        ),
        _scoped(
            "behavior_overlay.admin",
            "Administer behavior overlays",
            "Propose for others, approve, reject, or roll back overlays.",
            scopes=("children", "group", "global"),
            ceilings={"architect": "group"},
            risk="critical",
        ),
        _scoped(
            "thinking.read",
            "Read Thinking artifacts",
            "Read Scratchpad notes and Mind Maps visible to the caller.",
            scopes=("self", "group"),
            ceilings={"architect": "group"},
        ),
        _scoped(
            "thinking.write",
            "Write Thinking artifacts",
            "Create and update caller-owned Scratchpad notes and Mind Maps.",
            scopes=("self",),
            ceilings={"architect": "self"},
        ),
        _scoped(
            "idea_brief.read",
            "Read Idea Briefs",
            "Read Idea Brief proposal artifacts visible to the caller.",
            scopes=("self", "group"),
            ceilings={"architect": "group"},
        ),
        _scoped(
            "idea_brief.write",
            "Write Idea Briefs",
            "Create, update, refine, park, and archive caller-owned Idea Briefs.",
            scopes=("self",),
            ceilings={"architect": "self"},
        ),
        _scoped(
            "idea_brief.propose",
            "Propose Idea Briefs",
            "Mark caller-owned Idea Briefs proposed for review.",
            scopes=("self",),
            ceilings={"architect": "self"},
        ),
        _unscoped(
            "specialization.read",
            "Read specializations",
            "List and inspect the project specialization catalog.",
            base_kinds=frozenset({"engineer"}),
        ),
        _unscoped(
            "specialization.write",
            "Write specializations",
            "Create, update, or delete project specialization definitions.",
            base_kinds=frozenset({"engineer"}),
            risk="critical",
        ),
    )
}


# Temporary compatibility bridge from direct legacy Agent Profile atom grants
# to canonical schema-v5 capabilities. MCP tool registration no longer uses
# this vocabulary; it remains only while direct Agent Profile assignment is a
# supported migration surface.
LEGACY_ATOM_TO_CAPABILITY: dict[str, str] = {
    "observe.self_context": "self.read",
    "observe.board_summary": "board.read",
    "observe.task_detail": "task.read",
    "observe.events": "event.read",
    "observe.mcp_calls": "telemetry.read",
    "observe.deploy_state": "deploy.read",
    "observe.semantic_recall": "semantic_recall.read",
    "planning.area_read": "planning.area.read",
    "planning.area_write": "planning.area.write",
    "planning.initiative_read": "planning.initiative.read",
    "planning.initiative_write": "planning.initiative.write",
    "task.create": "task.create",
    "task.create_queued": "task.propose",
    "task.update": "task.update",
    "task.update_planning_fields": "task.propose",
    "task.reassign": "task.reassign",
    "task.move": "task.move",
    "task.move_planning_safe": "task.propose",
    "task.mark_covered": "task.mark_covered",
    "task.dispatch": "task.dispatch",
    "task.verify": "task.verify",
    "task.complete": "task.report",
    "task.upload_artifact": "task.artifact.write",
    "task.board_sync_read": "task.board_sync.read",
    "decision.list": "decision.read",
    "decision.create": "decision.create",
    "decision.create_proposed": "decision.propose",
    "decision.update_proposed": "decision.propose",
    "decision.accept": "decision.accept",
    "decision.update": "decision.update",
    "decision.link": "decision.link",
    "agent.engineer_roster_read": "engineer.roster.read",
    "agent.hire_engineer": "engineer.hire",
    "agent.manage_engineer_roster": "engineer.manage",
    "agent.dispatch_worker": "worker.manage",
    "comm.user_ask": "message.user",
    "comm.user_message": "message.user",
    "comm.engineer_message": "message.engineer",
    "comm.worker_message": "message.worker",
    "comm.peer_architect_list": "message.architect_peer",
    "comm.peer_architect_message": "message.architect_peer",
    "comm.product_ack_request": "message.ack_required",
    "journal.private": "journal.private",
    "journal.read": "journal.read",
    "journal.write": "journal.write",
    "memory.read": "memory.read",
    "memory.publish": "memory.write",
    "memory.admin": "memory.admin",
    "worktree.read": "worktree.read",
    "worktree.merge": "worktree.merge",
    "deploy.apply": "deploy.apply",
    "admin.settings": "settings.admin",
    "profile.assign": "class_profile.admin",
    "profile.edit": "class_profile.admin",
    "behavior_overlay.read": "behavior_overlay.read",
    "behavior_overlay.propose_self": "behavior_overlay.propose",
    "thinking.read": "thinking.read",
    "thinking.write_own": "thinking.write",
    "idea_brief.read": "idea_brief.read",
    "idea_brief.write_own": "idea_brief.write",
    "idea_brief.propose": "idea_brief.propose",
}

# Old atoms that implicitly covered several distinct MCP operations. This is
# used only while legacy Agent Profile snapshots still feed projection.
LEGACY_ATOM_GRANT_EXPANSIONS: dict[str, frozenset[str]] = {
    "observe.self_context": frozenset({"self.read", "help.read", "tool.search"}),
    "profile.edit": frozenset({
        "class_profile.admin",
        "behavior_overlay.propose",
        "behavior_overlay.admin",
    }),
    "agent.manage_engineer_roster": frozenset({
        "engineer.manage",
        "specialization.read",
        "specialization.write",
    }),
}


def canonical_capabilities_from_legacy_atoms(
    legacy_atoms: Iterable[str],
) -> frozenset[str]:
    """Expand a legacy profile grant/deny set into canonical capabilities."""

    canonical: set[str] = set()
    for raw_atom in legacy_atoms or ():
        atom = str(raw_atom or "").strip()
        mapped = LEGACY_ATOM_TO_CAPABILITY.get(atom)
        if mapped:
            canonical.add(mapped)
        canonical.update(LEGACY_ATOM_GRANT_EXPANSIONS.get(atom, ()))
    return frozenset(canonical)


def legacy_atoms_for_canonical_capabilities(
    canonical_capabilities: Iterable[str],
) -> frozenset[str]:
    """Approximate old profile atoms for transitional frozen diagnostics."""

    selected = {
        str(capability or "").strip()
        for capability in (canonical_capabilities or ())
        if str(capability or "").strip()
    }
    legacy: set[str] = set()
    for atom, primary in LEGACY_ATOM_TO_CAPABILITY.items():
        represented = {primary}
        represented.update(LEGACY_ATOM_GRANT_EXPANSIONS.get(atom, ()))
        if represented & selected:
            legacy.add(atom)
    return frozenset(legacy)


def validate_capability_catalog(
    catalog: dict[str, CapabilityDefinition] = CAPABILITY_CATALOG,
) -> list[str]:
    """Return deterministic catalog validation errors."""

    errors: list[str] = []
    for capability_id, definition in sorted(catalog.items()):
        if capability_id != definition.id:
            errors.append(
                f"catalog key {capability_id} does not match id {definition.id}"
            )
        if not CAPABILITY_ID_RE.match(capability_id):
            errors.append(f"invalid capability id: {capability_id}")
        if definition.risk not in RISK_LEVELS:
            errors.append(
                f"invalid risk {definition.risk} for {capability_id}"
            )
        if not definition.label.strip() or not definition.description.strip():
            errors.append(f"missing label/description for {capability_id}")
        if len(set(definition.scopes)) != len(definition.scopes):
            errors.append(f"duplicate scopes for {capability_id}")
        unknown_scopes = sorted(set(definition.scopes) - set(SCOPE_ORDER))
        if unknown_scopes:
            errors.append(
                f"unknown scopes for {capability_id}: {', '.join(unknown_scopes)}"
            )
        if tuple(sorted(definition.scopes, key=SCOPE_RANK.get)) != definition.scopes:
            errors.append(f"scopes are not ordered for {capability_id}")
        unknown_kinds = sorted(
            (set(definition.base_kinds) | set(definition.ceilings)) - BASE_KINDS
        )
        if unknown_kinds:
            errors.append(
                f"unknown base kinds for {capability_id}: {', '.join(unknown_kinds)}"
            )
        if definition.scoped:
            if definition.base_kinds:
                errors.append(
                    f"scoped capability {capability_id} must use ceilings only"
                )
            if not definition.ceilings:
                errors.append(f"scoped capability {capability_id} has no ceilings")
            for kind, ceiling in definition.ceilings.items():
                if ceiling not in definition.scopes:
                    errors.append(
                        f"ceiling {ceiling} for {capability_id}/{kind} "
                        "is not a supported scope"
                    )
        else:
            if definition.ceilings:
                errors.append(
                    f"unscoped capability {capability_id} must not use ceilings"
                )
            if not definition.base_kinds:
                errors.append(
                    f"unscoped capability {capability_id} has no base kinds"
                )
    return errors


def capability_catalog_for_base_kind(base_kind: str) -> list[dict]:
    """Return the public class-authoring catalog for one base kind."""

    base_kind = str(base_kind or "").strip()
    result = []
    for definition in sorted(CAPABILITY_CATALOG.values(), key=lambda item: item.id):
        available_kinds = sorted(
            kind for kind in BASE_KINDS if definition.available_to(kind)
        )
        if base_kind and base_kind not in available_kinds:
            continue
        result.append({
            "id": definition.id,
            "label": definition.label,
            "description": definition.description,
            "risk": definition.risk,
            "scoped": definition.scoped,
            "scopes": list(definition.scopes),
            "base_kinds": available_kinds,
            "maximum_scope": (
                definition.maximum_scope_for(base_kind) if base_kind else ""
            ),
            "maximum_scopes": {
                kind: definition.maximum_scope_for(kind)
                for kind in available_kinds
                if definition.scoped
            },
        })
    return result


_CATALOG_ERRORS = validate_capability_catalog()
if _CATALOG_ERRORS:
    raise RuntimeError("; ".join(_CATALOG_ERRORS))
