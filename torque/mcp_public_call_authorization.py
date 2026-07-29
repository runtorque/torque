"""Classify public MCP calls against transport authorization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .agent_classes import (
    has_frozen_platform_group_board_authority,
    has_frozen_platform_task_authority_mode,
)
from .mcp_authority import SCOPE_RANK
from .mcp_canonical import (
    canonical_tool_name,
    select_legacy_tool,
    translate_canonical_arguments,
)
from .mcp_engineer_tools.shared import (
    resolve_agent as resolve_mcp_agent,
    resolve_task as resolve_mcp_task,
)
from .state import task_counts_as_done
from .worktree_scope import architect_can_access_user_owned_worker_worktree


@dataclass(frozen=True)
class PublicCallAuthorizationDependencies:
    """Explicit composition dependencies for public-call classification."""

    raw_tools_for_caller: Callable
    effective_class_authority_for_cell: Callable
    visible_tools: Callable
    all_tool_map: Mapping[str, dict]
    canonical_callable_handler_registry: Mapping[str, tuple[str, ...]]
    engineer_architect_chain_tool_names: frozenset[str]
    tool_authority_definitions: Mapping[str, Any]
    tool_allowed_by_authority: Callable


def _resolve_public_tool_call(
    state,
    cell_id: str,
    name: str,
    arguments: dict,
    dependencies: PublicCallAuthorizationDependencies,
) -> tuple[str, dict]:
    """Resolve a canonical public call to an authority-projected operation.

    Historical names are accepted as a hidden migration bridge, but only
    canonical names are advertised.
    """

    requested = str(name or "").strip()
    raw_tools, cell, caller_kind = dependencies.raw_tools_for_caller(state, cell_id)
    raw_names = {
        str(tool.get("name", "") or "").strip()
        for tool in raw_tools
    }
    if requested in raw_names:
        return requested, dict(arguments or {})
    if requested in dependencies.all_tool_map:
        return "", dict(arguments or {})

    authority = dependencies.effective_class_authority_for_cell(cell)
    canonical = canonical_tool_name(requested)
    # Resolve only through the canonical callable registry.  The visible
    # catalog and runtime now share this exact source of truth, which keeps a
    # projected name such as task_claim or task_mark_covered from becoming an
    # advertised-but-unknown operation.
    candidates = [
        handler
        for handler in dependencies.canonical_callable_handler_registry.get(
            canonical, ()
        )
        if handler in raw_names
    ]
    selected = select_legacy_tool(
        canonical,
        candidates,
        arguments,
        caller_kind=caller_kind or "worker",
        authority=authority,
    )
    if not selected:
        return "", dict(arguments or {})
    translated = translate_canonical_arguments(
        canonical,
        selected,
        arguments,
        caller_kind=caller_kind or "worker",
    )
    if (
        canonical == "supervisor_message"
        and not str(translated.get("architect_id", "") or "").strip()
        and cell is not None
    ):
        translated["architect_id"] = str(
            getattr(cell, "hired_by_architect_id", "") or ""
        ).strip()
    return selected, translated


_PUBLIC_TOOL_CALL_UNKNOWN = "unknown"
_PUBLIC_TOOL_CALL_UNAUTHORIZED = "known_but_unauthorized"
_PUBLIC_TOOL_CALL_NOT_PROJECTED = "known_but_not_projected"
_PUBLIC_TOOL_CALL_FROZEN_AUTHORITY_DENIED = (
    "known_but_frozen_authority_denied"
)
_PUBLIC_TOOL_CALL_TARGET_SCOPE_DENIED = "known_but_target_scope_denied"
_PUBLIC_TOOL_CALL_AUTHORIZED = "authorized"


def public_call_refusal_message(classification: str,
                                requested_tool_name: str) -> str:
    """Describe an entitled public-call refusal without exposing a target.

    The caller is entitled to this diagnostic only after the exact canonical
    name has passed the tools/list projection gate.  In particular, the
    target-scope wording deliberately covers both missing and out-of-scope
    targets, preserving the no-target-oracle boundary.
    """
    tool_name = str(requested_tool_name or "").strip()
    if classification == _PUBLIC_TOOL_CALL_NOT_PROJECTED:
        return (
            f"Authorization denied: {tool_name} is not projected for this "
            "caller."
        )
    if classification == _PUBLIC_TOOL_CALL_FROZEN_AUTHORITY_DENIED:
        return (
            f"Authorization denied: {tool_name} is denied by this session's "
            "frozen authority snapshot; relaunch after an Agent Class change "
            "to refresh it."
        )
    if classification == _PUBLIC_TOOL_CALL_TARGET_SCOPE_DENIED:
        return (
            "Authorization denied: target is outside this caller's "
            f"authorized scope for {tool_name}."
        )
    return f"Known tool is not authorized: {tool_name}"


def _classify_public_tool_call(
    state,
    cell_id: str,
    requested_tool_name: str,
    arguments: dict,
    dependencies: PublicCallAuthorizationDependencies,
) -> tuple[str, str, dict, object, str]:
    """Classify a public call without broadening an MCP tool's authority.

    Only an exact canonical name in this caller's ``tools/list`` projection is
    entitled to a truthful authorization refusal.  Every other spelling stays
    non-disclosing: unknown names, legacy handler aliases, and operations not
    projected to this caller all retain the existing ``Unknown tool`` result.
    The classifier deliberately stops at transport authorization; recognized
    provisional routes still dispatch to their own inert handler response.
    """

    requested = str(requested_tool_name or "").strip()
    tool_name, translated_arguments = _resolve_public_tool_call(
        state,
        cell_id,
        requested,
        arguments,
        dependencies,
    )
    caller_cell = (
        state.agents.get(str(cell_id or "").strip()) if cell_id else None
    )
    caller_kind = str(
        getattr(caller_cell, "kind", "") or ""
    ).strip() if caller_cell else ""
    projected_canonical_name = (
        requested == canonical_tool_name(requested)
        and requested in {
            str(tool.get("name", "") or "").strip()
            for tool in dependencies.visible_tools(state, cell_id)
        }
    )

    # An unresolved call or a missing handler is never a public operation.
    if not tool_name or tool_name not in dependencies.all_tool_map:
        return (
            _PUBLIC_TOOL_CALL_UNKNOWN,
            tool_name,
            translated_arguments,
            caller_cell,
            caller_kind,
        )

    if _tool_hidden_for_caller(
            tool_name, caller_kind, caller_cell, dependencies
    ):
        refusal = _PUBLIC_TOOL_CALL_NOT_PROJECTED
    elif _tool_denied_by_effective_authority(
            tool_name, caller_cell, dependencies
    ):
        refusal = _PUBLIC_TOOL_CALL_FROZEN_AUTHORITY_DENIED
    elif _tool_argument_scope_denied(
            state,
            tool_name,
            translated_arguments,
            caller_cell,
            dependencies,
    ):
        refusal = _PUBLIC_TOOL_CALL_TARGET_SCOPE_DENIED
    else:
        refusal = ""
    if refusal:
        return (
            (
                refusal
                if projected_canonical_name
                else _PUBLIC_TOOL_CALL_UNKNOWN
            ),
            tool_name,
            translated_arguments,
            caller_cell,
            caller_kind,
        )

    return (
        _PUBLIC_TOOL_CALL_AUTHORIZED,
        tool_name,
        translated_arguments,
        caller_cell,
        caller_kind,
    )


def _tool_hidden_for_caller(
    tool_name: str,
    caller_kind: str,
    caller_cell,
    dependencies: PublicCallAuthorizationDependencies,
) -> bool:
    """Return True when a known tool is intentionally scoped out."""
    name = str(tool_name or "").strip()
    if (
        caller_kind == "engineer"
        and name in dependencies.engineer_architect_chain_tool_names
    ):
        return not bool(
            str(getattr(caller_cell, "hired_by_architect_id", "") or "").strip()
        )
    return False


def _tool_denied_by_effective_authority(
    tool_name: str,
    caller_cell,
    dependencies: PublicCallAuthorizationDependencies,
) -> bool:
    authority = dependencies.effective_class_authority_for_cell(caller_cell)
    return bool(
        authority is not None
        and not dependencies.tool_allowed_by_authority(tool_name, authority)
    )


def _agent_target_scope(caller_cell, target_cell) -> str:
    """Return the generic caller-to-agent ACL relationship scope."""

    if not caller_cell or not target_cell:
        return "global"
    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    target_id = str(getattr(target_cell, "id", "") or "").strip()
    if caller_id and caller_id == target_id:
        return "self"
    caller_kind = str(getattr(caller_cell, "kind", "") or "").strip()
    if caller_kind == "architect" and str(
        getattr(target_cell, "hired_by_architect_id", "") or ""
    ).strip() == caller_id:
        return "children"
    if caller_kind == "engineer" and str(
        getattr(target_cell, "owner_engineer_id", "") or ""
    ).strip() == caller_id:
        return "children"
    caller_group = str(getattr(caller_cell, "group", "") or "").strip()
    target_group = str(getattr(target_cell, "group", "") or "").strip()
    if caller_group and caller_group == target_group:
        return "group"
    return "global"


def _task_target_scope(state, caller_cell, task) -> str:
    """Return the generic caller-to-task ACL relationship scope."""

    if not caller_cell or not task:
        return "global"
    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    creator_proposal_mode = has_frozen_platform_task_authority_mode(caller_cell, "creator-proposal-only")
    # Creator-proposal authority is narrower than the normal Architect task
    # relationship: assignment is not ownership.  Do not let assignment or a
    # worker/Engineer relationship turn a peer-created task into ``self``.
    if creator_proposal_mode and str(
            getattr(task, "created_by_architect_id", "") or ""
    ).strip() != caller_id:
        caller_group = str(getattr(caller_cell, "group", "") or "").strip()
        task_group = str(getattr(task, "group", "") or "").strip()
        return "group" if caller_group and caller_group == task_group else "global"
    owner_ids = {
        str(getattr(task, field, "") or "").strip()
        for field in (
            "agent_id",
            "assigned_engineer_id",
            "created_by_architect_id",
            "assigned_architect_id",
            "created_by_engineer_id",
            "owner_engineer_id",
        )
    }
    owner_ids.discard("")
    if caller_id in owner_ids:
        return "self"
    for owner_id in owner_ids:
        target_cell = state.agents.get(owner_id)
        if target_cell and _agent_target_scope(caller_cell, target_cell) == "children":
            return "children"
    caller_group = str(getattr(caller_cell, "group", "") or "").strip()
    task_group = str(getattr(task, "group", "") or "").strip()
    if caller_group and caller_group == task_group:
        return "group"
    return "global"


def _legacy_open_worker_ask_scope(state, caller_cell, task) -> str:
    """Return ``self`` for the one legacy Worker-ask resolution route.

    Old Worker asks predate their ``assigned_engineer_id`` ownership stamp.
    They are intentionally discoverable through their reply Worker, whose
    current owner is the only Engineer that may resolve the open ask.  This is
    not a general task relationship: it applies only to the typed Engineer
    ask resolver, an open human ask, and a Worker in the same group.
    """

    if not caller_cell or not task:
        return ""
    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    caller_group = str(getattr(caller_cell, "group", "") or "").strip()
    if (
        not caller_id
        or str(getattr(caller_cell, "kind", "") or "").strip() != "engineer"
        or not caller_group
        or str(getattr(task, "assigned_engineer_id", "") or "").strip()
        or task_counts_as_done(task)
        or "torque:human" not in (getattr(task, "labels", []) or [])
        or str(getattr(task, "group", "") or "").strip() != caller_group
    ):
        return ""
    worker = state.agents.get(
        str(getattr(task, "reply_agent_id", "") or "").strip()
    )
    if (
        not worker
        or str(getattr(worker, "cell_type", "") or "").strip() != "agent"
        or str(getattr(worker, "kind", "") or "").strip() != "worker"
        or str(getattr(worker, "group", "") or "").strip() != caller_group
        or str(getattr(worker, "owner_engineer_id", "") or "").strip()
        != caller_id
    ):
        return ""
    return "self"


def _record_target_scope(state, caller_cell, record: dict | None) -> str:
    """Return scope for a persisted planning/artifact record."""

    if not caller_cell or not isinstance(record, dict):
        return "global"
    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    owner_ids = {
        str(record.get(field, "") or "").strip()
        for field in (
            "architect_id",
            "created_by_architect_id",
            "created_by_id",
            "owner_id",
        )
    }
    owner_ids.discard("")
    if caller_id in owner_ids:
        return "self"
    owner_scopes = []
    for owner_id in owner_ids:
        owner = state.agents.get(owner_id)
        if owner is not None:
            owner_scopes.append(_agent_target_scope(caller_cell, owner))
    if owner_scopes:
        return min(owner_scopes, key=lambda scope: SCOPE_RANK[scope])
    caller_group = str(getattr(caller_cell, "group", "") or "").strip()
    record_group = str(
        record.get("group_name", record.get("group", "")) or ""
    ).strip()
    if caller_group and caller_group == record_group:
        return "group"
    return "global"


def _event_target_scope(state, caller_cell, event: dict | None) -> str:
    """Return the narrowest caller relationship represented by an event."""

    if not caller_cell or not isinstance(event, dict):
        return "global"
    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    if caller_id and caller_id in {
        str(event.get("cell_id", "") or "").strip(),
        str(event.get("created_by_architect_id", "") or "").strip(),
    }:
        return "self"
    scopes = []
    task_id = str(event.get("task_id", "") or "").strip()
    task = state.board_tasks.get(task_id) if task_id else None
    if task is not None:
        scopes.append(_task_target_scope(state, caller_cell, task))
    for field in (
        "cell_id",
        "assigned_engineer_id",
        "owner_engineer_id",
        "peer_architect_id",
    ):
        target_id = str(event.get(field, "") or "").strip()
        target = state.agents.get(target_id) if target_id else None
        if target is not None:
            scopes.append(_agent_target_scope(caller_cell, target))
    if scopes:
        return min(scopes, key=lambda scope: SCOPE_RANK[scope])
    caller_group = str(getattr(caller_cell, "group", "") or "").strip()
    event_group = str(event.get("group", "") or "").strip()
    if caller_group and caller_group == event_group:
        return "group"
    return "global"


def _semantic_recall_target_scope(
    state,
    caller_cell,
    result: dict | None,
) -> str:
    """Return scope for one semantic recall result's internal ACL anchors."""

    if not caller_cell or not isinstance(result, dict):
        return "global"
    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    owner_id = str(result.get("_acl_owner_id", "") or "").strip()
    participant_ids = {
        str(item or "").strip()
        for item in (result.get("_acl_participant_ids", []) or [])
        if str(item or "").strip()
    }
    if caller_id and caller_id in ({owner_id} | participant_ids):
        return "self"
    source_type = str(result.get("source_type", "") or "").strip()
    source_id = str(result.get("source_id", "") or "").strip()
    if source_type == "task" and source_id:
        task = state.board_tasks.get(source_id)
        if task is not None:
            return _task_target_scope(state, caller_cell, task)
    if source_type == "decision" and source_id:
        decision = state.load_decision(source_id)
        if decision is not None:
            return _record_target_scope(state, caller_cell, decision)

    scopes = []
    for agent_id in ({owner_id} | participant_ids) - {""}:
        agent = state.agents.get(agent_id)
        if agent is not None:
            scopes.append(_agent_target_scope(caller_cell, agent))
    if scopes:
        return min(scopes, key=lambda scope: SCOPE_RANK[scope])
    caller_group = str(getattr(caller_cell, "group", "") or "").strip()
    result_group = str(result.get("group", "") or "").strip()
    if caller_group and caller_group == result_group:
        return "group"
    return "global"


def _resolve_scoped_resource(state, caller_cell, resource_kind: str, reference):
    """Resolve one descriptor-declared resource without widening visibility."""

    reference = str(reference or "").strip()
    if not reference:
        return None
    if resource_kind == "task":
        task_id = resolve_mcp_task(state, reference)
        return state.board_tasks.get(task_id) if task_id else None
    if resource_kind == "agent":
        agent_id = resolve_mcp_agent(state, reference)
        return state.agents.get(agent_id) if agent_id else None
    group = str(getattr(caller_cell, "group", "") or "").strip()
    if resource_kind == "scratchpad_note":
        note_id = state.resolve_scratchpad_note_id(reference, group=group)
        return state.load_scratchpad_note(note_id) if note_id else None
    if resource_kind == "mind_map":
        map_id = state.resolve_mind_map_id(reference, group=group)
        return state.load_mind_map(map_id, include_counts=True) if map_id else None
    if resource_kind == "idea_brief":
        brief_id = state.resolve_idea_brief_id(reference, group=group)
        return state.load_idea_brief(brief_id) if brief_id else None
    if resource_kind == "area":
        area_id = state.resolve_area_id(reference, group=group)
        return state.load_area(area_id) if area_id else None
    if resource_kind == "initiative":
        initiative_id = state.resolve_initiative_id(reference, group=group)
        return state.load_initiative(initiative_id) if initiative_id else None
    if resource_kind == "decision":
        return state.load_decision(reference)
    if resource_kind == "message_peer":
        db = getattr(state, "db", None)
        loader = getattr(db, "load_agent_peer_message", None)
        message = loader(reference) if callable(loader) else None
        if not isinstance(message, dict):
            return None
        caller_id = str(getattr(caller_cell, "id", "") or "").strip()
        sender_id = str(message.get("sender_id", "") or "").strip()
        recipient_id = str(message.get("recipient_id", "") or "").strip()
        peer_id = recipient_id if sender_id == caller_id else sender_id
        return state.agents.get(peer_id)
    return None


def _scoped_resource_relationship(state, caller_cell, resource_kind: str, resource):
    if resource_kind == "task":
        return _task_target_scope(state, caller_cell, resource)
    if resource_kind == "event":
        return _event_target_scope(state, caller_cell, resource)
    if resource_kind == "semantic_recall":
        return _semantic_recall_target_scope(state, caller_cell, resource)
    if resource_kind in {"agent", "message_peer"}:
        return _agent_target_scope(caller_cell, resource)
    return _record_target_scope(state, caller_cell, resource)


def _handler_scoped_target_scope(
    state,
    tool_name: str,
    arguments: dict,
    caller_cell,
    requirement,
    target,
) -> str:
    """Return the frozen ACL scope for a declared handler-scoped target.

    A small number of Architect handlers have a deliberately narrow,
    evidence-backed exception to normal task ownership.  Reuse that
    side-effect-free authorization predicate at the transport boundary so a
    valid routed product-proposal call remains authorized, while every other
    nonempty target is denied before its handler can disclose it.
    """

    scope = _scoped_resource_relationship(
        state,
        caller_cell,
        requirement.target_kind,
        target,
    )
    # Legacy Worker asks were created before their assigned-Engineer stamp.
    # The existing task resolver is the sole write route that may derive
    # self-scope from the reply Worker, and only while that exact ask remains
    # open.  Keep every other task tool on normal ownership scope.
    if (
        tool_name == "engineer_task_resolve"
        and requirement.target_kind == "task"
        and _legacy_open_worker_ask_scope(state, caller_cell, target) == "self"
    ):
        return "self"
    if (
        not requirement.handler_scoped
        or str(getattr(caller_cell, "kind", "") or "").strip() != "architect"
    ):
        return scope

    caller_id = str(getattr(caller_cell, "id", "") or "").strip()
    if not caller_id:
        return scope
    creator_proposal_mode = has_frozen_platform_task_authority_mode(caller_cell, "creator-proposal-only")
    if (
            creator_proposal_mode
            and tool_name != "architect_task_pickup"
            and requirement.target_kind == "task"
            and str(getattr(target, "created_by_architect_id", "") or "").strip()
            != caller_id
    ):
        return scope
    # A class may declare the bounded creator-proposal task authority mode.
    # Its reassignment handler independently enforces a same-group Engineer
    # target and caller-owned task; retain that platform check while allowing
    # the agent-target descriptor to represent this intentionally narrow route.
    if (
            tool_name == "architect_task_reassign"
            and requirement.target_kind == "agent"
            and creator_proposal_mode
            and str(getattr(target, "group", "") or "").strip()
            == str(getattr(caller_cell, "group", "") or "").strip()
    ):
        return "self"
    if (
            tool_name in {
                "architect_merge", "architect_rebase", "architect_create_pr",
            }
            and requirement.target_kind == "agent"
            and architect_can_access_user_owned_worker_worktree(
                state, caller_cell, target
            )
    ):
        # A user-owned worker has no Engineer parent.  Its bounded Architect
        # relationship is its same-group task stream, checked by this
        # side-effect-free predicate before the handler is reached.
        return "children"
    # Keep this exception tied to the exact legacy handlers whose documented
    # contract permits a routed product proposal owned by another architect.
    # The predicates inspect persisted route evidence only; they do not
    # mutate state or dispatch a handler.
    if tool_name == "architect_task_pickup":
        from .mcp_scoped.proposals import (
            _routed_product_proposal_root_pickup_authorization,
        )

        authorization, _error = (
            _routed_product_proposal_root_pickup_authorization(
                state,
                caller_id,
                target,
            )
        )
        return "self" if authorization else scope
    if tool_name == "architect_task_mark_covered":
        from .mcp_scoped.proposals import (
            _routed_product_root_coverage_authorization,
        )

        covering_ref = str(
            arguments.get("covering_task", "")
            or arguments.get("covering_task_id", "")
            or ""
        ).strip()
        covering_id = resolve_mcp_task(state, covering_ref) if covering_ref else ""
        authorization, _error = _routed_product_root_coverage_authorization(
            state,
            caller_id,
            target,
            covering_id,
        )
        return "self" if authorization else scope
    return scope


def _tool_argument_scope_denied(
    state,
    tool_name: str,
    arguments: dict,
    caller_cell,
    dependencies: PublicCallAuthorizationDependencies,
) -> bool:
    """Authorize concrete MCP targets against the frozen class ACL.

    Existing handler ownership checks are still applied after this generic
    gate.  The two checks are intersected so the ACL can narrow behavior but
    can never broaden the handler's platform ceiling.
    """

    authority = dependencies.effective_class_authority_for_cell(caller_cell)
    if not isinstance(arguments, dict):
        return False
    tool_authority = dependencies.tool_authority_definitions.get(tool_name)
    if not tool_authority:
        return True
    for requirement in tool_authority.requirements:
        if requirement.scope_argument:
            requested_scope = str(
                arguments.get(requirement.scope_argument, "") or ""
            ).strip().lower()
            if (
                authority is not None
                and requested_scope
                and not authority.allows(
                    requirement.capability,
                    scope=requested_scope,
                )
            ):
                return True
        # ``handler_scoped`` retains the handler's business-rule authority
        # ceiling, but does not waive public transport's target
        # non-disclosure boundary. Every declared, nonempty target must be
        # resolved and checked before a handler can distinguish it.
        if not requirement.target_argument:
            continue
        raw_target = arguments.get(requirement.target_argument, "")
        target_refs = raw_target if isinstance(raw_target, list) else [raw_target]
        for raw_ref in target_refs:
            # Batch dispatch declares ``tasks`` as its target argument, with
            # each target carried by an entry's ``task`` field. Do not treat
            # the entry object itself as a reference.
            if isinstance(raw_ref, dict) and requirement.target_kind == "task":
                raw_ref = raw_ref.get("task", "")
            target_ref = str(raw_ref or "").strip()
            if not target_ref:
                continue
            target = _resolve_scoped_resource(
                state,
                caller_cell,
                requirement.target_kind,
                target_ref,
            )
            target_scope = (
                _handler_scoped_target_scope(
                    state,
                    tool_name,
                    arguments,
                    caller_cell,
                    requirement,
                    target,
                )
                if target is not None
                else ""
            )
            # Exact canonical calls that are actually projected must not leak
            # whether a declared target exists. A nonempty unresolved target
            # therefore receives the same pre-handler refusal as a resolved
            # target outside the frozen authority scope. Empty optional
            # targets remain the handler's concern.
            if target is None or (
                authority is not None
                and not authority.allows(
                    requirement.capability,
                    scope=target_scope,
                )
            ):
                return True
    return False
