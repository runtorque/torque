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
    agent_class_base_kinds: Iterable[str] | None = None,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        label=label,
        description=description,
        risk=risk,
        scopes=tuple(scopes),
        ceilings=dict(ceilings),
        agent_class_base_kinds=(
            frozenset(agent_class_base_kinds)
            if agent_class_base_kinds is not None else None
        ),
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
            scopes=("self", "children", "group"),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "event.manage",
            "Manage event delivery",
            "Change the caller's digest preferences or resume event delivery.",
            scopes=("self",),
            ceilings={"engineer": "self", "architect": "self"},
            risk="high",
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
            scopes=("self", "children", "group"),
            ceilings={"engineer": "group", "architect": "children"},
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
            scopes=("self", "children"),
            ceilings={"engineer": "children", "architect": "self"},
            risk="critical",
            agent_class_base_kinds={"engineer"},
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
            "task.review.amend",
            "Amend own review verdict",
            "Append an attributable correction to a review verdict the caller recorded.",
            scopes=("self",),
            ceilings={kind: "self" for kind in _ALL},
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
            ceilings={
                "worker": "self",
                "engineer": "group",
                "architect": "group",
            },
        ),
        _scoped(
            "planning.area.read",
            "Read Areas",
            "Read Planning Areas and filtered Area-linked context.",
            scopes=("self", "group"),
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
            scopes=("self", "group"),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "planning.initiative.write",
            "Write Initiatives",
            "Create, update, archive, and link Initiatives.",
            scopes=("self",),
            ceilings={"architect": "self"},
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
            "message.peer",
            "Message peers",
            "List, inspect, message, and reply to eligible same-level peers.",
            scopes=("group",),
            ceilings={"engineer": "group", "architect": "group"},
        ),
        _scoped(
            "message.subordinate",
            "Message subordinate agents",
            "Message, inspect, and answer eligible directly managed agents.",
            scopes=("children",),
            ceilings={"engineer": "children", "architect": "children"},
            risk="high",
        ),
        _scoped(
            "message.supervisor",
            "Message supervisor",
            "Message and reply to the caller's directly supervising agent.",
            scopes=("self",),
            ceilings={"engineer": "self"},
            risk="high",
        ),
        _scoped(
            "message.ack_request",
            "Request acknowledgement",
            "Mark eligible peer communication as requiring acknowledgement.",
            scopes=("group",),
            ceilings={"engineer": "group", "architect": "group"},
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
            "settings.read",
            "Read settings",
            "Read process-wide or role-specific settings visible to the caller.",
            base_kinds=_ARCHITECT,
            risk="high",
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
            (
                set(definition.base_kinds)
                | set(definition.ceilings)
                | set(definition.agent_class_base_kinds or ())
            ) - BASE_KINDS
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
            kind for kind in BASE_KINDS
            if definition.authorable_by_agent_class(kind)
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
