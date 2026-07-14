"""Dynamic Behavior overlay command handlers and route manifests."""

from __future__ import annotations

from ..behavior_overlay import proposal_summary, version_summary
from ..dispatch_registry import AsyncHandlerRegistry
from ..state import MatrixState


def _behavior_overlay_error(exc: Exception) -> dict:
    return {"type": "error", "message": str(exc)}


def _behavior_overlay_scope_kwargs(data: dict) -> dict:
    return {
        "agent_id": str(data.get("agent_id", "") or ""),
        "scope_kind": str(data.get("scope_kind", "") or ""),
        "scope_group": str(data.get("scope_group", "") or data.get("group", "") or ""),
        "scope_key": str(data.get("scope_key", "") or ""),
        "group": str(data.get("group", "") or ""),
        "role_kind": str(data.get("role_kind", "") or data.get("role", "") or ""),
    }


def _handle_behavior_overlay_read_command(
        data: dict, state: MatrixState) -> dict:
    try:
        scope = state._behavior_scope_from_args(**_behavior_overlay_scope_kwargs(data))
    except Exception as exc:
        return _behavior_overlay_error(exc)
    version = state.db.load_behavior_overlay_active_version(scope) if state.db else None
    active = state.db.load_behavior_overlay_active(scope) if state.db else {}
    if not version and data.get("seed", False):
        version = state.ensure_behavior_overlay_seed(
            agent_id=scope.agent_id,
            scope_kind=scope.scope_kind,
            scope_group=scope.scope_group,
            scope_key=scope.scope_key,
        )
        active = state.db.load_behavior_overlay_active(scope) if state.db else {}
    return {
        "type": "behavior_overlay",
        **scope.as_row_fields(),
        "active": active or {},
        "version": version_summary(version),
        "text": str((version or {}).get("text", "") or ""),
    }


def _handle_behavior_overlay_versions_command(
        data: dict, state: MatrixState) -> dict:
    try:
        scope = state._behavior_scope_from_args(**_behavior_overlay_scope_kwargs(data))
    except Exception as exc:
        return _behavior_overlay_error(exc)
    return {
        "type": "behavior_overlay_versions",
        **scope.as_row_fields(),
        "versions": [
            version_summary(row)
            for row in state.list_behavior_overlay_versions(
                scope_key=scope.scope_key,
                scope_kind=scope.scope_kind,
                scope_group=scope.scope_group,
                limit=int(data.get("limit", 50) or 50),
            )
        ],
    }


def _handle_behavior_overlay_proposals_command(
        data: dict, state: MatrixState) -> dict:
    return {
        "type": "behavior_overlay_proposals",
        "proposals": [
            proposal_summary(row)
            for row in state.list_behavior_overlay_proposals(
                status_filter=str(data.get("status_filter", "") or ""),
                agent_id=str(data.get("agent_id", "") or ""),
                scope_kind=str(data.get("scope_kind", "") or ""),
                scope_group=str(data.get("scope_group", "") or data.get("group", "") or ""),
                scope_key=str(data.get("scope_key", "") or ""),
                group=str(data.get("group", "") or ""),
                role_kind=str(data.get("role_kind", "") or data.get("role", "") or ""),
                next_actor_kind=str(data.get("next_actor_kind", "") or ""),
                proposed_by_agent_id=str(data.get("proposed_by_agent_id", "") or ""),
                limit=int(data.get("limit", 100) or 100),
            )
        ],
    }


def _handle_behavior_overlay_diff_command(
        data: dict, state: MatrixState) -> dict:
    try:
        return state.behavior_overlay_diff_payload(
            proposal_id=str(data.get("proposal_id", "") or ""),
            from_version_id=str(data.get("from_version_id", "") or ""),
            to_version_id=str(data.get("to_version_id", "") or ""),
            agent_id=str(data.get("agent_id", "") or ""),
            scope_kind=str(data.get("scope_kind", "") or ""),
            scope_group=str(data.get("scope_group", "") or data.get("group", "") or ""),
            scope_key=str(data.get("scope_key", "") or ""),
            group=str(data.get("group", "") or ""),
            role_kind=str(data.get("role_kind", "") or data.get("role", "") or ""),
        )
    except Exception as exc:
        return _behavior_overlay_error(exc)


def _behavior_overlay_maybe_create_user_task(
        proposal: dict,
        state: MatrixState) -> dict:
    if (
            proposal
            and proposal.get("status") in {"proposed", "approved"}
            and str(proposal.get("next_actor_kind", "") or "") == "user"
            and not str(proposal.get("user_task_id", "") or "").strip()):
        task_id = state.create_behavior_overlay_user_task(proposal["id"])
        if task_id:
            proposal = state.load_behavior_overlay_proposal(proposal["id"]) or proposal
    return proposal


def _handle_behavior_overlay_propose_command(
        data: dict, state: MatrixState) -> dict:
    try:
        proposal = state.create_behavior_overlay_proposal(
            agent_id=str(data.get("agent_id", "") or ""),
            scope_kind=str(data.get("scope_kind", "") or ""),
            scope_group=str(data.get("scope_group", "") or data.get("group", "") or ""),
            scope_key=str(data.get("scope_key", "") or ""),
            group=str(data.get("group", "") or ""),
            role_kind=str(data.get("role_kind", "") or data.get("role", "") or ""),
            proposed_by_agent_id=str(data.get("proposed_by_agent_id", "") or ""),
            proposed_by_kind=str(data.get("proposed_by_kind", "") or ""),
            text=str(data.get("text", "") or ""),
            rationale=str(data.get("rationale", "") or ""),
            proposal_type=str(data.get("proposal_type", "set_text") or "set_text"),
            target_version_id=str(data.get("target_version_id", "") or ""),
            expected_base_version_id=str(data.get("expected_base_version_id", "") or ""),
            idempotency_key=str(data.get("idempotency_key", "") or ""),
            architect_approver_id=str(data.get("architect_approver_id", "") or ""),
            auto_apply_architect_direct=bool(
                data.get("auto_apply_architect_direct", False)
            ),
        )
        proposal = _behavior_overlay_maybe_create_user_task(proposal, state)
        return {
            "type": "behavior_overlay_proposal",
            "proposal": proposal_summary(proposal),
            "proposal_id": str(proposal.get("id", "") or ""),
            "status": str(proposal.get("status", "") or ""),
            "approval_route": str(proposal.get("approval_route", "") or ""),
            "next_actor_kind": str(proposal.get("next_actor_kind", "") or ""),
            "user_task_id": str(proposal.get("user_task_id", "") or ""),
        }
    except Exception as exc:
        return _behavior_overlay_error(exc)


def _handle_behavior_overlay_architect_approve_command(
        data: dict, state: MatrixState) -> dict:
    try:
        proposal = state.architect_approve_behavior_overlay_proposal(
            str(data.get("proposal_id", "") or data.get("id", "") or ""),
            architect_id=str(data.get("architect_id", "") or ""),
            expected_proposed_text_sha256=str(
                data.get("expected_proposed_text_sha256", "") or ""
            ),
            note=str(data.get("note", "") or ""),
        )
        proposal = _behavior_overlay_maybe_create_user_task(proposal, state)
        return {
            "type": "behavior_overlay_proposal",
            "proposal": proposal_summary(proposal),
            "proposal_id": proposal.get("id", ""),
            "status": proposal.get("status", ""),
            "user_task_id": proposal.get("user_task_id", ""),
        }
    except Exception as exc:
        return _behavior_overlay_error(exc)


def _handle_behavior_overlay_reject_command(
        data: dict, state: MatrixState, *, actor_kind: str) -> dict:
    try:
        proposal = state.reject_behavior_overlay_proposal(
            str(data.get("proposal_id", "") or data.get("id", "") or ""),
            actor_kind=actor_kind,
            actor_id=str(data.get("actor_id", "") or data.get("architect_id", "") or ""),
            note=str(data.get("note", "") or ""),
        )
        return {
            "type": "behavior_overlay_proposal",
            "proposal": proposal_summary(proposal),
            "proposal_id": proposal.get("id", ""),
            "status": proposal.get("status", ""),
        }
    except Exception as exc:
        return _behavior_overlay_error(exc)


def _handle_behavior_overlay_user_approve_command(
        data: dict, state: MatrixState) -> dict:
    try:
        proposal = state.load_behavior_overlay_proposal(
            str(data.get("proposal_id", "") or data.get("id", "") or "")
        )
        if not proposal:
            return {"type": "error", "message": "behavior overlay proposal not found"}
        expected = str(data.get("expected_proposed_text_sha256", "") or "").strip()
        if expected and expected != str(proposal.get("proposed_text_sha256", "") or ""):
            return {"type": "error", "message": "proposed text hash does not match"}
        if str(proposal.get("next_actor_kind", "") or "") != "user":
            return {
                "type": "error",
                "message": "behavior overlay proposal is not awaiting user approval",
            }
        proposal = state.apply_behavior_overlay_proposal(
            proposal["id"],
            actor_kind="user",
            actor_id="user",
            note=str(data.get("note", "") or ""),
        )
        return {
            "type": "behavior_overlay_proposal",
            "proposal": proposal_summary(proposal),
            "proposal_id": proposal.get("id", ""),
            "status": proposal.get("status", ""),
        }
    except Exception as exc:
        return _behavior_overlay_error(exc)


def _handle_behavior_overlay_user_rollback_command(
        data: dict, state: MatrixState) -> dict:
    try:
        proposal = state.create_behavior_overlay_proposal(
            agent_id=str(data.get("agent_id", "") or ""),
            scope_kind=str(data.get("scope_kind", "") or ""),
            scope_group=str(data.get("scope_group", "") or data.get("group", "") or ""),
            scope_key=str(data.get("scope_key", "") or ""),
            group=str(data.get("group", "") or ""),
            role_kind=str(data.get("role_kind", "") or data.get("role", "") or ""),
            proposed_by_agent_id="user",
            proposed_by_kind="user",
            rationale=str(data.get("rationale", "") or "User-requested rollback"),
            proposal_type="rollback",
            target_version_id=str(data.get("version_id", "") or ""),
            expected_base_version_id=str(data.get("expected_base_version_id", "") or ""),
            idempotency_key=str(data.get("idempotency_key", "") or ""),
        )
        proposal = state.apply_behavior_overlay_proposal(
            proposal["id"],
            actor_kind="user",
            actor_id="user",
            note=str(data.get("rationale", "") or "User-requested rollback"),
        )
        return {
            "type": "behavior_overlay_proposal",
            "proposal": proposal_summary(proposal),
            "proposal_id": proposal.get("id", ""),
            "status": proposal.get("status", ""),
        }
    except Exception as exc:
        return _behavior_overlay_error(exc)


BEHAVIOR_OVERLAY_READ_COMMAND_NAMES = frozenset({
    "behavior_overlay_read",
    "behavior_overlay_versions",
    "behavior_overlay_proposals",
    "behavior_overlay_diff",
})
BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES = frozenset({
    "behavior_overlay_propose",
    "behavior_overlay_architect_approve",
    "behavior_overlay_architect_reject",
    "behavior_overlay_user_approve",
    "behavior_overlay_user_reject",
    "behavior_overlay_user_rollback",
})
BEHAVIOR_OVERLAY_COMMAND_NAMES = frozenset().union(
    BEHAVIOR_OVERLAY_READ_COMMAND_NAMES,
    BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES,
)


def _handle_behavior_overlay_architect_reject_command(
        data: dict, state: MatrixState) -> dict:
    return _handle_behavior_overlay_reject_command(
        data,
        state,
        actor_kind="architect",
    )


def _handle_behavior_overlay_user_reject_command(
        data: dict, state: MatrixState) -> dict:
    return _handle_behavior_overlay_reject_command(
        data,
        state,
        actor_kind="user",
    )


_BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY = AsyncHandlerRegistry()
_BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_read"},
    _handle_behavior_overlay_read_command,
)
_BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_versions"},
    _handle_behavior_overlay_versions_command,
)
_BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_proposals"},
    _handle_behavior_overlay_proposals_command,
)
_BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_diff"},
    _handle_behavior_overlay_diff_command,
)

_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY = AsyncHandlerRegistry()
_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_propose"},
    _handle_behavior_overlay_propose_command,
)
_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_architect_approve"},
    _handle_behavior_overlay_architect_approve_command,
)
_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_architect_reject"},
    _handle_behavior_overlay_architect_reject_command,
)
_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_user_approve"},
    _handle_behavior_overlay_user_approve_command,
)
_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_user_reject"},
    _handle_behavior_overlay_user_reject_command,
)
_BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.register_many(
    {"behavior_overlay_user_rollback"},
    _handle_behavior_overlay_user_rollback_command,
)
