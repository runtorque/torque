"""Product proposals, Thinking workspaces, and idea-brief MCP helpers."""

from datetime import datetime

from torque.agent_classes import (
    has_frozen_platform_task_authority_mode,
)
from torque.behavior_overlay import BehaviorOverlayScope
from torque.config import log
from torque.idea_briefs import (
    IDEA_BRIEF_PROPOSAL_SCOPE,
    idea_brief_contract_metadata,
    idea_brief_is_archived,
)
from torque.mcp_engineer_tools.shared import resolve_task as _resolve_task
from torque.mcp_scoped.architect_reports import (
    _architect_board_summary_task_item,
    _compact_json,
    _normalize_architect_task_list_label_filter,
    _normalize_architect_task_list_limit,
)
from torque.mcp_scoped.common import (
    _agent_is_tombstoned,
    _agent_peer_message_row_to_entry,
    _attach_task_board_sync_inline_state,
    _attach_task_review_inline_state,
    _caller_group,
    _dedupe_strings,
    _load_architect_decision,
    _load_same_group_architect_decisions,
    _optional_bool_arg,
    _task_created_by_classifier,
    _thread_requires_architect_reply,
)
from torque.mcp_scoped.health import _task_health_payload_for_response
from torque.mcp_scoped.peer_context import (
    _peer_context_decision_snapshot,
    _peer_context_task_snapshot,
    _resolve_architect_peer,
)
from torque.mcp_scoped.planning import (
    _area_from_args,
    _area_scope_group,
    _initiative_from_args,
    _initiative_scope_group,
)
from torque.server_artifacts import serialize_task_for_mcp
from torque.state import ARCHIVED_LANE, board_task_is_closed

_ARCHITECT_PEER_INBOX_DEFAULT_LIMIT = 20
_ARCHITECT_PEER_INBOX_MAX_LIMIT = 100
_PROPOSAL_PEER_MARKER = "torque.proposal_peer.v1"
_PROPOSAL_DECISION_MARKER = "torque.decision_proposal.v1"
_PROPOSAL_CONTEXT_MARKER = "torque.proposal_context.v1"
_PRODUCT_TASK_LABELS = frozenset({"product-proposal", "proposal-only"})

def _cell_has_proposal_peer_authority(state, cell) -> bool:
    if not cell:
        return False
    cell_id = str(getattr(cell, "id", "") or "").strip()
    if not cell_id:
        return False
    return (
        _caller_authority_allows_capability(state, cell_id, "message.peer")
        and _caller_authority_allows_capability(state, cell_id, "task.propose")
    )


def _proposal_peer_allowlist_contains(state, caller_id: str, peer_id: str) -> bool:
    allowlist = getattr(state, "proposal_peer_allowlist", None)
    if not allowlist:
        return False
    caller_id = str(caller_id or "").strip()
    peer_id = str(peer_id or "").strip()
    caller = getattr(state, "agents", {}).get(caller_id)
    group = str(getattr(caller, "group", "") or "").strip()
    candidates = []
    if isinstance(allowlist, dict):
        for key in (caller_id, group, "*"):
            value = allowlist.get(key)
            if value is not None:
                candidates.extend(value if isinstance(value, (list, tuple, set)) else [value])
    elif isinstance(allowlist, (list, tuple, set)):
        candidates.extend(allowlist)
    return peer_id in {str(item or "").strip() for item in candidates}


def _cell_kind(cell) -> str:
    return str(getattr(cell, "kind", "") or "").strip()


def _cell_id(cell) -> str:
    return str(getattr(cell, "id", "") or "").strip()


def _cell_group(cell) -> str:
    return str(getattr(cell, "group", "") or "").strip()


def _proposal_peer_scope_reason(state, caller_id: str, peer) -> str:
    if not peer:
        return ""
    caller_id = str(caller_id or "").strip()
    peer_id = _cell_id(peer)
    if not caller_id or not peer_id or peer_id == caller_id:
        return ""
    caller = getattr(state, "agents", {}).get(caller_id)
    if not caller:
        return ""
    if (
            getattr(peer, "cell_type", "") != "agent"
            or _cell_kind(peer) != "architect"
            or _agent_is_tombstoned(state, peer)):
        return ""
    if not _cell_group(caller) or _cell_group(peer) != _cell_group(caller):
        return ""
    if _cell_has_proposal_peer_authority(state, peer):
        return "product-peer-authority"
    if _proposal_peer_allowlist_contains(state, caller_id, peer_id):
        return "product-peer-allowlist"
    if _caller_authority_allows_capability(
            state,
            peer_id,
            "message.peer"):
        return "same-group-architect-peer-capable"
    return ""


def _proposal_peer_eligible(state, caller_id: str, peer) -> bool:
    return bool(_proposal_peer_scope_reason(state, caller_id, peer))


def _resolve_product_architect_peer(state, caller_id: str,
                                    architect_ident: str) -> tuple[object | None, str]:
    peer, error = _resolve_architect_peer(state, caller_id, architect_ident)
    if not peer:
        return None, error
    if not _proposal_peer_eligible(state, caller_id, peer):
        return None, "architect not found in product-peer scope"
    return peer, ""


def _resolve_product_architect_peer_filter(
        state,
        caller_id: str,
        architect_ident: str) -> tuple[str, str]:
    ident = str(architect_ident or "").strip()
    if not ident:
        return "", ""
    peer, error = _resolve_product_architect_peer(state, caller_id, ident)
    if not peer:
        return "", error
    return str(getattr(peer, "id", "") or "").strip(), ""


def _decision_has_product_marker(decision: dict | None) -> bool:
    metadata = (decision or {}).get("metadata", {})
    if not isinstance(metadata, dict):
        return False
    product = metadata.get("product_proposal", {})
    return (
        isinstance(product, dict)
        and str(product.get("marker", "") or "").strip() == _PROPOSAL_DECISION_MARKER
    )


def _product_decision_metadata(caller_id: str) -> dict:
    return {
        "product_proposal": {
            "marker": _PROPOSAL_DECISION_MARKER,
            "owner_architect_id": str(caller_id or "").strip(),
            "proposed_only": True,
            "wave": "4B",
        }
    }


def _load_product_decision(state, caller_id: str,
                           decision_id: str) -> tuple[dict | None, str]:
    decision, error = _load_architect_decision(state, caller_id, decision_id)
    if not decision:
        return None, error
    if not _decision_has_product_marker(decision):
        return None, "Decision not found"
    if str(decision.get("status", "") or "").strip() != "proposed":
        return None, "Decision is not a proposed product decision"
    return decision, ""


def _product_decisions_for_architect(state, caller_id: str, *,
                                     include_archived: bool = False) -> list[dict]:
    return [
        decision for decision in state.load_decisions_for_architect(
            caller_id,
            include_archived=include_archived,
        )
        if _decision_has_product_marker(decision)
        and str(decision.get("status", "") or "").strip() == "proposed"
    ]


def _decision_proposal_linked_task_ids(state, caller_id: str) -> set[str]:
    linked: set[str] = set()
    for decision in _product_decisions_for_architect(state, caller_id):
        linked.update(str(item or "").strip() for item in decision.get("linked_task_ids", []) or [])
    return {item for item in linked if item}


def _task_has_product_label(task) -> bool:
    labels = {
        str(label or "").strip()
        for label in (getattr(task, "labels", []) or [])
    }
    return bool(labels & set(_PRODUCT_TASK_LABELS))


def _task_has_covers_label(task, covered_task_id: str) -> bool:
    covered_task_id = str(covered_task_id or "").strip().lower()
    if not task or not covered_task_id:
        return False
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    return f"covers:{covered_task_id}" in labels


def _parse_timestampish(value) -> float:
    if isinstance(value, (int, float)):
        return float(value or 0)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(normalized).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _proposal_peer_route_message_for_task(
        state,
        caller_id: str,
        task,
        *,
        covering_task=None) -> dict:
    """Return durable product-peer evidence routing ``task`` to caller.

    Product/ideation wrappers persist product-peer markers
    plus product-scoped task context in ``agent_peer_messages``.  Treat only
    inbound architect→architect product-peer rows, anchored to the product-proposal
    root, as route/management evidence for cross-architect coverage closure.
    If a caller-created covering task is supplied, prefer rows sent before the
    covering task was created, but do not require timestamp parsing when older
    rows lack normalized timestamps.
    """
    caller_id = str(caller_id or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    creator_id = str(getattr(task, "created_by_architect_id", "") or "").strip()
    if not caller_id or not task_id or not creator_id:
        return {}
    db = getattr(state, "db", None)
    loader = getattr(db, "load_agent_peer_messages_for_agent", None) if db else None
    if not callable(loader):
        return {}

    covering_created_at = 0.0
    if covering_task is not None:
        covering_created_at = _parse_timestampish(
            getattr(covering_task, "created_at", "") or ""
        )

    for row in loader(caller_id, limit=1000):
        if str((row or {}).get("recipient_id", "") or "").strip() != caller_id:
            continue
        if str((row or {}).get("sender_id", "") or "").strip() != creator_id:
            continue
        if {
            str((row or {}).get("sender_kind", "") or "").strip(),
            str((row or {}).get("recipient_kind", "") or "").strip(),
        } != {"architect"}:
            continue
        if not _row_has_proposal_peer_marker(row):
            continue
        context_task_ids = {
            str(item or "").strip()
            for item in ((row or {}).get("context_task_ids", []) or [])
        }
        if task_id not in context_task_ids:
            continue
        if covering_created_at:
            route_created_at = _parse_timestampish(
                (row or {}).get("created_at", 0) or 0
            )
            if route_created_at and route_created_at > covering_created_at:
                continue
        return dict(row or {})
    return {}


def _routed_product_root_coverage_authorization(
        state,
        caller_id: str,
        task,
        covering_task_id: str) -> tuple[dict, str]:
    """Authorize a non-owner architect to close a routed product-proposal root.

    This deliberately grants only the ``task_mark_covered``/Done transition
    path.  Broader task edits, reassignments, deletion, or dispatch remain
    guarded by their existing owner-created checks.
    """
    caller_id = str(caller_id or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    creator_id = str(getattr(task, "created_by_architect_id", "") or "").strip()
    if not caller_id or not task_id:
        return {}, "Task was not created by this architect"
    caller = state.agents.get(caller_id)
    caller_group = str(getattr(caller, "group", "") or "").strip()
    if (
            not _task_has_product_label(task)
            or not creator_id
            or creator_id == caller_id
            or not caller_group
            or str(getattr(task, "group", "") or "").strip() != caller_group):
        return {}, "Task was not created by this architect"
    if not covering_task_id:
        return {}, (
            "Routed product proposal roots require a covering_task created "
            "by this architect"
        )
    covering_task = state.board_tasks.get(str(covering_task_id or "").strip())
    if not covering_task:
        return {}, "covering_task not found in scope"
    if str(getattr(covering_task, "group", "") or "").strip() != caller_group:
        return {}, "covering_task not found in scope"
    if str(getattr(covering_task, "created_by_architect_id", "") or "").strip() != caller_id:
        return {}, (
            "Routed product proposal roots require a covering_task created "
            "by this architect"
        )

    has_cover_label = _task_has_covers_label(covering_task, task_id)
    route_row = _proposal_peer_route_message_for_task(
        state,
        caller_id,
        task,
        covering_task=covering_task,
    )
    if not has_cover_label and not route_row:
        return {}, (
            "Routed product proposal roots require covers:<task> label "
            "or inbound product-peer route evidence"
        )

    labels = [
        str(label or "").strip()
        for label in (getattr(task, "labels", []) or [])
        if str(label or "").strip() in _PRODUCT_TASK_LABELS
    ]
    authorization = {
        "scope": "routed_product_proposal_root",
        "source": (
            "covering_task_label_and_proposal_peer"
            if has_cover_label and route_row else
            "covering_task_label"
            if has_cover_label else
            "proposal_peer_route"
        ),
        "covered_task_id": task_id,
        "root_creator_architect_id": creator_id,
        "covering_task_id": str(covering_task_id or "").strip(),
        "product_labels": labels,
    }
    if route_row:
        authorization["route_message_id"] = str(route_row.get("id", "") or "")
        authorization["route_thread_id"] = str(route_row.get("thread_id", "") or "")
        authorization["route_sender_id"] = str(route_row.get("sender_id", "") or "")
    return authorization, ""


def _routed_product_proposal_root_pickup_authorization(
        state,
        caller_id: str,
        task) -> tuple[dict, str]:
    """Authorize direct Architect pickup of a product proposal root.

    This is intentionally narrower than general task ownership.  A task is
    claimable only when it is a same-group, product-labeled task created by a
    Product-authority Architect, unclaimed (or already claimed by the
    caller), and there is durable inbound product-peer route evidence from the
    proposal creator to the claiming Architect.
    """
    caller_id = str(caller_id or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    creator_id = str(getattr(task, "created_by_architect_id", "") or "").strip()
    if not caller_id or not task_id:
        return {}, "Task not found"
    caller = state.agents.get(caller_id)
    caller_group = str(getattr(caller, "group", "") or "").strip()
    if not caller or str(getattr(caller, "kind", "") or "").strip() != "architect":
        return {}, "Architect pickup is available only to Architects"
    if not caller_group or str(getattr(task, "group", "") or "").strip() != caller_group:
        return {}, "Task not found"
    if board_task_is_closed(task):
        return {}, "Task is already closed"
    if not _task_has_product_label(task):
        return {}, "Task is not a product-proposal product proposal"
    if not creator_id:
        return {}, "Task is not a product-proposal product proposal"
    creator = state.agents.get(creator_id)
    if not creator or str(getattr(creator, "group", "") or "").strip() != caller_group:
        return {}, "Task is not a same-group product-proposal product proposal"
    if not _cell_has_proposal_peer_authority(state, creator):
        return {}, "Task creator does not have product proposal authority"
    if creator_id == caller_id:
        return {}, "Task was created by this architect; pickup is not required"
    assigned_architect_id = str(
        getattr(task, "assigned_architect_id", "") or ""
    ).strip()
    if assigned_architect_id and assigned_architect_id != caller_id:
        return {}, "Task is already assigned to another architect"

    route_row = _proposal_peer_route_message_for_task(
        state,
        caller_id,
        task,
    )
    if not route_row:
        return {}, (
            "product-proposal product task pickup requires inbound product-peer "
            "route evidence from the product proposal creator"
        )

    labels = [
        str(label or "").strip()
        for label in (getattr(task, "labels", []) or [])
        if str(label or "").strip() in _PRODUCT_TASK_LABELS
    ]
    authorization = {
        "scope": "routed_product_proposal_root_pickup",
        "source": "proposal_peer_route",
        "task_id": task_id,
        "root_creator_architect_id": creator_id,
        "claiming_architect_id": caller_id,
        "product_labels": labels,
        "route_message_id": str(route_row.get("id", "") or ""),
        "route_thread_id": str(route_row.get("thread_id", "") or ""),
        "route_sender_id": str(route_row.get("sender_id", "") or ""),
        "route_recipient_id": str(route_row.get("recipient_id", "") or ""),
    }
    return authorization, ""


def _architect_task_owned_by_caller(task, caller_id: str) -> bool:
    caller_id = str(caller_id or "").strip()
    if not task or not caller_id:
        return False
    if str(getattr(task, "created_by_architect_id", "") or "").strip() == caller_id:
        return True
    if str(getattr(task, "assigned_architect_id", "") or "").strip() == caller_id:
        return True
    return False


def _product_task_visible_for_architect(state, caller_id: str, task) -> bool:
    """Return same-group task read visibility for every Architect.

    Product labels are an optional query filter, never a visibility ceiling.
    The product wrapper remains responsible for proposal-only *writes*, but
    must not make same-group coordination depend on manually pasted task text.
    """

    if not task:
        return False
    caller = state.agents.get(str(caller_id or "").strip())
    group = str(getattr(caller, "group", "") or "").strip()
    if not group or str(getattr(task, "group", "") or "").strip() != group:
        return False
    return True


def _product_visible_task_ids_for_architect(state, caller_id: str) -> set[str]:
    return {
        str(getattr(task, "id", "") or "").strip()
        for task in getattr(state, "board_tasks", {}).values()
        if _product_task_visible_for_architect(state, caller_id, task)
    }


def _product_visible_decision_ids_for_architect(state, caller_id: str) -> set[str]:
    """Return all same-group decision IDs for read-only product projections."""
    return {
        str(decision.get("id", "") or "").strip()
        for decision in _load_same_group_architect_decisions(state, caller_id)
        if str(decision.get("id", "") or "").strip()
    }


def _product_task_summary(state, caller_id: str, task) -> dict:
    item = _architect_board_summary_task_item(
        task,
        created_by=_task_created_by_classifier(task),
    )
    linked_decisions = [
        decision.get("id")
        for decision in _load_same_group_architect_decisions(state, caller_id)
        if str(getattr(task, "id", "") or "").strip()
        in {str(tid or "").strip() for tid in decision.get("linked_task_ids", []) or []}
    ]
    if linked_decisions:
        item["linked_product_decision_ids"] = linked_decisions
    return item


def _product_task_items(state, caller_id: str, args: dict) -> tuple[list[dict], str]:
    labels, label_error = _normalize_architect_task_list_label_filter(
        args.get("label_filter")
    )
    if label_error:
        return [], label_error
    lane_filter = str(args.get("lane_filter", "") or "").strip()
    include_archived = bool(args.get("include_archived", False))
    tasks = []
    for task in state.board_tasks.values():
        if not _product_task_visible_for_architect(state, caller_id, task):
            continue
        lane = str(getattr(task, "lane", "") or "").strip()
        if lane == ARCHIVED_LANE and not include_archived:
            continue
        if lane_filter and lane != lane_filter:
            continue
        task_labels = set(getattr(task, "labels", []) or [])
        if labels and not set(labels).issubset(task_labels):
            continue
        tasks.append(task)
    lane_order = {lane: idx for idx, lane in enumerate(getattr(state, "board_lanes", []) or [])}
    tasks.sort(key=lambda task: (
        lane_order.get(str(getattr(task, "lane", "") or ""), len(lane_order)),
        getattr(task, "position", 0),
        str(getattr(task, "task", "") or "").lower(),
        str(getattr(task, "id", "") or ""),
    ))
    return [_product_task_summary(state, caller_id, task) for task in tasks], ""


def _task_proposal_list_json(state, caller_id: str, args: dict) -> tuple[str, bool]:
    limit, limit_error = _normalize_architect_task_list_limit(args.get("limit"))
    if limit_error:
        return limit_error, True
    items, error = _product_task_items(state, caller_id, args)
    if error:
        return error, True
    payload_items = items[:limit] if limit else []
    return _compact_json({
        "type": "task_proposal_list",
        "tasks": payload_items,
        "count": len(items),
        "truncated": bool(limit and len(items) > limit),
    }), False


def _proposal_board_summary_json(state, caller_id: str, args: dict) -> tuple[str, bool]:
    try:
        limit = int(args.get("limit", 20) or 20)
    except (TypeError, ValueError):
        return "limit must be an integer", True
    limit = max(0, min(limit, 100))
    items, error = _product_task_items(state, caller_id, {})
    if error:
        return error, True
    lane_counts: dict[str, int] = {}
    for item in items:
        lane = str(item.get("lane", "") or "")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    return _compact_json({
        "type": "proposal_board_summary",
        "tasks_total": len(items),
        "lanes": lane_counts,
        "tasks": items[:limit],
        "truncated": len(items) > limit,
        "scope": "same-group task summaries; product labels are optional filters",
    }), False


def _task_proposal_show_json(state, caller_id: str, args: dict) -> tuple[str, bool]:
    task_id = _resolve_task(state, args.get("task", ""))
    if not task_id:
        return "Task not found", True
    task = state.board_tasks.get(task_id)
    if not _product_task_visible_for_architect(state, caller_id, task):
        return "Task not found", True
    payload = serialize_task_for_mcp(task, tasks_by_id=state.board_tasks)
    payload.update(_task_health_payload_for_response(state, task))
    payload["type"] = "product_task"
    payload["title"] = task.task
    payload["created_by"] = _task_created_by_classifier(task)
    _attach_task_board_sync_inline_state(payload, task)
    _attach_task_review_inline_state(payload, task)
    return _compact_json(payload), False


def _product_area_ref(state, caller_id: str, area_ref: str) -> tuple[str, str]:
    group = _area_scope_group(state, caller_id)
    area_id = state.resolve_area_id(area_ref, group=group)
    if not area_id:
        return "", "Area not found"
    area = state.load_area(area_id)
    if not area or str(area.get("group_name", "") or "").strip() != group:
        return "", "Area not found"
    return area_id, ""


def _product_initiative_ref(state, caller_id: str,
                            initiative_ref: str) -> tuple[str, str]:
    group = _initiative_scope_group(state, caller_id)
    initiative_id = state.resolve_initiative_id(initiative_ref, group=group)
    if not initiative_id:
        return "", "Initiative not found"
    initiative = state.load_initiative(initiative_id)
    if not initiative or str(initiative.get("group_name", "") or "").strip() != group:
        return "", "Initiative not found"
    return initiative_id, ""


def _idea_brief_ref(state, caller_id: str,
                            brief_ref: str) -> tuple[str, dict | None, str]:
    caller = state.agents.get(str(caller_id or "").strip())
    group = str(getattr(caller, "group", "") or "").strip()
    brief_id = state.resolve_idea_brief_id(brief_ref, group=group)
    if not brief_id:
        return "", None, "Idea Brief not found"
    brief = state.load_idea_brief(brief_id)
    if (
            not brief
            or _idea_brief_item_group(brief) != group
            or idea_brief_is_archived(brief)):
        return "", None, "Idea Brief not found"
    return brief_id, brief, ""


def _product_initiative_read_json(state, caller_id: str,
                                  args: dict, *, show: bool) -> tuple[str, bool]:
    """Read broad same-group initiatives with product-scoped link detail."""

    group = _initiative_scope_group(state, caller_id)
    visible_task_ids = _product_visible_task_ids_for_architect(state, caller_id)
    visible_decision_ids = _product_visible_decision_ids_for_architect(
        state,
        caller_id,
    )
    if show:
        initiative, error = _initiative_from_args(state, caller_id, args)
        if not initiative:
            return error, True
        payload = state.initiative_payload(
            initiative["id"],
            visible_task_ids=visible_task_ids,
            visible_decision_ids=visible_decision_ids,
        )
        if not payload:
            return "Initiative not found", True
        payload["type"] = "initiative"
        return _compact_json(payload), False
    include_archived = bool(args.get("include_archived", False))
    initiatives = []
    for item in state.list_initiatives(group=group, include_archived=include_archived):
        payload = state.initiative_payload(
            item["id"],
            visible_task_ids=visible_task_ids,
            visible_decision_ids=visible_decision_ids,
            include_links=bool(args.get("include_links", False)),
        ) or item
        initiatives.append(payload)
    return _compact_json({
        "type": "initiative_list",
        "group": group,
        "initiatives": initiatives,
    }), False


def _product_area_read_json(state, caller_id: str,
                            args: dict, *, show: bool) -> tuple[str, bool]:
    """Read broad same-group Areas with product-scoped link detail."""

    group = _area_scope_group(state, caller_id)
    visible_task_ids = _product_visible_task_ids_for_architect(state, caller_id)
    visible_decision_ids = _product_visible_decision_ids_for_architect(
        state,
        caller_id,
    )
    decision_details = True
    if show:
        area, error = _area_from_args(state, caller_id, args)
        if not area:
            return error, True
        try:
            note_limit = min(max(int(args.get("note_limit", 50) or 50), 1), 100)
        except (TypeError, ValueError):
            note_limit = 50
        payload = state.area_payload(
            area["id"],
            visible_task_ids=visible_task_ids,
            visible_decision_ids=visible_decision_ids,
            decision_details=decision_details,
            note_limit=note_limit,
        )
        if not payload:
            return "Area not found", True
        payload["type"] = "area"
        return _compact_json(payload), False
    include_archived = bool(args.get("include_archived", False))
    include_links = bool(args.get("include_links", False))
    include_notes = bool(args.get("include_notes", False))
    try:
        limit = min(max(int(args.get("limit", 50) or 50), 1), 100)
    except (TypeError, ValueError):
        limit = 50
    areas = []
    for item in state.list_areas(
            group=group,
            include_archived=include_archived,
            limit=limit):
        payload = state.area_payload(
            item["id"],
            visible_task_ids=visible_task_ids,
            visible_decision_ids=visible_decision_ids,
            include_links=include_links,
            include_notes=include_notes,
            decision_details=decision_details,
            note_limit=10,
        ) or item
        areas.append(payload)
    return _compact_json({
        "type": "area_list",
        "group": group,
        "areas": areas,
    }), False


def _normalize_proposal_context(
        state,
        caller_id: str,
        caller_group: str,
        args: dict) -> tuple[dict, str]:
    if _dedupe_strings(args.get("context_engineer_ids", [])):
        return {}, "context_engineer_ids are not supported for proposal tools"

    task_ids = []
    task_snapshots = []
    for task_ident in _dedupe_strings(args.get("context_task_ids", [])):
        task_id = _resolve_task(state, task_ident)
        task = state.board_tasks.get(task_id or "")
        if not task_id or not _product_task_visible_for_architect(state, caller_id, task):
            return {}, f"Task not found: {task_ident}"
        task_ids.append(task_id)
        task_snapshots.append(_peer_context_task_snapshot(task))

    decision_ids = []
    decision_snapshots = []
    for decision_ident in _dedupe_strings(args.get("context_decision_ids", [])):
        decision_id = str(decision_ident or "").strip()
        decision, decision_error = _load_product_decision(
            state,
            caller_id,
            decision_id,
        )
        if not decision:
            return {}, decision_error if decision_error != "decision id is required" else f"Decision not found: {decision_ident}"
        decision_ids.append(decision_id)
        decision_snapshots.append(_peer_context_decision_snapshot(decision))

    area_ids = []
    area_snapshots = []
    for area_ident in _dedupe_strings(args.get("context_area_ids", [])):
        area_id, area_error = _product_area_ref(state, caller_id, area_ident)
        if not area_id:
            return {}, area_error
        area = state.load_area(area_id)
        area_ids.append(area_id)
        area_snapshots.append({
            "id": area_id,
            "title": str((area or {}).get("title", "") or ""),
            "slug": str((area or {}).get("slug", "") or ""),
        })

    initiative_ids = []
    initiative_snapshots = []
    for initiative_ident in _dedupe_strings(args.get("context_initiative_ids", [])):
        initiative_id, initiative_error = _product_initiative_ref(
            state,
            caller_id,
            initiative_ident,
        )
        if not initiative_id:
            return {}, initiative_error
        initiative = state.load_initiative(initiative_id)
        initiative_ids.append(initiative_id)
        initiative_snapshots.append({
            "id": initiative_id,
            "title": str((initiative or {}).get("title", "") or ""),
            "slug": str((initiative or {}).get("slug", "") or ""),
        })

    idea_brief_ids = []
    idea_brief_snapshots = []
    for brief_ident in _dedupe_strings(args.get("context_idea_brief_ids", [])):
        brief_id, brief, brief_error = _idea_brief_ref(
            state,
            caller_id,
            brief_ident,
        )
        if not brief_id:
            return {}, brief_error
        idea_brief_ids.append(brief_id)
        idea_brief_snapshots.append({
            "id": brief_id,
            "title": str((brief or {}).get("title", "") or ""),
            "slug": str((brief or {}).get("slug", "") or ""),
            "status": str((brief or {}).get("status", "") or ""),
            "caller_owned": _idea_brief_owned_by_caller(brief, caller_id),
        })

    context_summary = str(args.get("context_summary", "") or "").strip()
    return {
        "context_task_ids": task_ids,
        "context_engineer_ids": [],
        "context_decision_ids": decision_ids,
        "context_summary": context_summary,
        "context_snapshot": {
            "proposal_context": {
                "marker": _PROPOSAL_CONTEXT_MARKER,
                "area_ids": area_ids,
                "initiative_ids": initiative_ids,
                "idea_brief_ids": idea_brief_ids,
                "scope": "product_proposal_v1",
            },
            "tasks": task_snapshots,
            "engineers": [],
            "decisions": decision_snapshots,
            "areas": area_snapshots,
            "initiatives": initiative_snapshots,
            "idea_briefs": idea_brief_snapshots,
        },
    }, ""


def _proposal_context_anchor_count(context: dict) -> int:
    snapshot = dict((context or {}).get("context_snapshot", {}) or {})
    proposal = dict(snapshot.get("proposal_context", {}) or {})
    return (
        len(list((context or {}).get("context_task_ids", []) or []))
        + len(list((context or {}).get("context_decision_ids", []) or []))
        + len(list(proposal.get("area_ids", []) or []))
        + len(list(proposal.get("initiative_ids", []) or []))
        + len(list(proposal.get("idea_brief_ids", []) or []))
    )


def _require_proposal_peer_anchor(context: dict, *, tool_name: str) -> str:
    if _proposal_context_anchor_count(context) > 0:
        return ""
    return (
        f"{tool_name} requires at least one product-scope anchor "
        "(Idea Brief, decision, task proposal, Area, or Initiative)"
    )


def _require_product_ack_anchor(ack_required: bool, context: dict) -> str:
    if not ack_required:
        return ""
    if _proposal_context_anchor_count(context) <= 0:
        return (
            "ack_required=true requires message.ack_request plus at least "
            "one product-scope anchor (Idea Brief, decision, task proposal, Area, or Initiative)"
        )
    return ""


def _caller_authority_allows_capability(state, caller_id: str,
                                        capability: str) -> bool:
    """Check wrapper refinements against frozen Agent Class authority."""

    caller = getattr(state, "agents", {}).get(str(caller_id or "").strip())
    if not caller:
        return False
    class_snapshot = getattr(caller, "effective_agent_class_snapshot", {}) or {}
    if not (
        isinstance(class_snapshot, dict)
        and class_snapshot.get("effective_authority")
    ):
        # Callers not launched through the class boundary retain the platform
        # base-kind behavior. Every managed launch freezes a class snapshot.
        return True
    try:
        from torque.capability_catalog import CAPABILITY_CATALOG
        from torque.mcp_authority import effective_authority_from_snapshot

        authority = effective_authority_from_snapshot(
            class_snapshot.get("effective_authority"),
            capabilities=CAPABILITY_CATALOG,
        )
        return bool(authority and authority.has(str(capability or "").strip()))
    except Exception:
        log.exception("Failed to evaluate caller Agent Class authority")
        return False


def _caller_has_behavior_overlay_admin(state, caller_id: str) -> bool:
    """Return whether caller has behavior-overlay administration authority."""

    return _caller_authority_allows_capability(
        state,
        caller_id,
        "behavior_overlay.admin",
    )


def _restricted_behavior_overlay_scope_allowed(
        state,
        caller_id: str,
        scope: BehaviorOverlayScope | None) -> bool:
    """Restrict non-admin Architect-derived overlay access to self + own role.

    Product/ideation classes may inspect their effective overlay and
    propose user-approved changes to their own agent overlay.  They must not
    use the broad Architect overlay tools to reach hired Engineer overlays or
    worker/engineer role overlays, because those scopes are not part of their
    Agent Class authority.
    """

    if not scope:
        return False
    if _caller_has_behavior_overlay_admin(state, caller_id):
        return True
    if not _caller_authority_allows_capability(
            state, caller_id, "behavior_overlay.read"):
        return False
    if scope.scope_kind == "agent":
        return str(scope.scope_key or "").strip() == str(caller_id or "").strip()
    if scope.scope_kind == "role":
        return (
            str(scope.scope_group or "").strip() == _caller_group(state, caller_id)
            and str(scope.scope_key or "").strip() == "architect"
        )
    return False


def _restricted_behavior_overlay_proposal_allowed(
        state,
        caller_id: str,
        proposal: dict | None) -> bool:
    if not proposal:
        return False
    if _caller_has_behavior_overlay_admin(state, caller_id):
        return True
    scope_kind = str(proposal.get("scope_kind", "") or "agent").strip()
    if scope_kind == "role":
        return (
            str(proposal.get("scope_group", "") or "").strip()
            == _caller_group(state, caller_id)
            and str(proposal.get("scope_key", "") or "").strip() == "architect"
        )
    return (
        str(proposal.get("agent_id", "") or proposal.get("scope_key", "") or "").strip()
        == str(caller_id or "").strip()
    )


def _add_proposal_peer_marker(context: dict, *, source: str,
                             caller_id: str) -> dict:
    enriched = dict(context or {})
    snapshot = dict(enriched.get("context_snapshot", {}) or {})
    snapshot["proposal_peer"] = {
        "marker": _PROPOSAL_PEER_MARKER,
        "source": source,
        "caller_id": str(caller_id or "").strip(),
        "scope": "product_proposal_v1",
    }
    enriched["context_snapshot"] = snapshot
    return enriched


def _row_has_proposal_peer_marker(row: dict | None) -> bool:
    snapshot = dict((row or {}).get("context_snapshot", {}) or {})
    proposal_peer = snapshot.get("proposal_peer", {})
    return (
        isinstance(proposal_peer, dict)
        and str(proposal_peer.get("marker", "") or "").strip() == _PROPOSAL_PEER_MARKER
    )


def _row_proposal_context(row: dict | None) -> dict:
    return {
        "context_task_ids": list((row or {}).get("context_task_ids", []) or []),
        "context_engineer_ids": [],
        "context_decision_ids": list((row or {}).get("context_decision_ids", []) or []),
        "context_summary": str((row or {}).get("context_summary", "") or ""),
        "context_snapshot": dict((row or {}).get("context_snapshot", {}) or {}),
    }


def _proposal_peer_row_visible(state, caller_id: str, row: dict,
                              peer_id: str = "") -> bool:
    """Return whether a peer-message row is addressable in product scope.

    Product-peer markers classify rows emitted by the proposal send paths.
    They are retained for product-route evidence, but must not turn an
    otherwise eligible same-group Architect conversation into an invisible,
    unreplyable one: normal Architect peer messages predate the marker and
    share the same participant/eligibility boundary.
    """
    caller_id = str(caller_id or "").strip()
    participants = {
        str((row or {}).get("sender_id", "") or "").strip(),
        str((row or {}).get("recipient_id", "") or "").strip(),
    }
    if caller_id not in participants:
        return False
    if peer_id and peer_id not in participants:
        return False
    if {
        str((row or {}).get("sender_kind", "") or "").strip(),
        str((row or {}).get("recipient_kind", "") or "").strip(),
    } != {"architect"}:
        return False
    peer_architect_id = next((pid for pid in participants if pid != caller_id), "")
    peer = state.agents.get(peer_architect_id)
    if not peer or _agent_is_tombstoned(state, peer):
        return False
    caller = state.agents.get(caller_id)
    if str(getattr(peer, "group", "") or "").strip() != str(
            getattr(caller, "group", "") or "").strip():
        return False
    return _proposal_peer_eligible(state, caller_id, peer)


def _proposal_peer_inbox_json(state, caller_id: str,
                             args: dict) -> tuple[str, bool]:
    try:
        limit = int(args.get("limit", _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
                    or _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT
    limit = max(1, min(limit, _ARCHITECT_PEER_INBOX_MAX_LIMIT))
    try:
        since_value = float(args.get("since", 0) or 0)
    except (TypeError, ValueError):
        return "since must be a number", True
    requires_reply, requires_reply_error = _optional_bool_arg(
        args,
        "requires_reply",
        False,
    )
    if requires_reply_error:
        return requires_reply_error, True
    peer_id, peer_error = _resolve_product_architect_peer_filter(
        state,
        caller_id,
        str(args.get("peer_architect_id", "") or "").strip(),
    )
    if peer_error:
        return peer_error, True
    thread_id = str(args.get("thread_id", "") or "").strip()
    db = getattr(state, "db", None)
    if not db:
        return _compact_json({"type": "proposal_peer_inbox", "threads": []}), False
    row_limit = min(max(limit * 20, limit), 1000)
    rows = db.load_agent_peer_messages_for_agent(
        caller_id,
        limit=row_limit,
        since=since_value,
        peer_id=peer_id,
        thread_id=thread_id,
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if not _proposal_peer_row_visible(state, caller_id, row, peer_id=peer_id):
            continue
        grouped.setdefault(str(row.get("thread_id", "") or ""), []).append(row)
    threads = []
    for group_thread_id, messages in grouped.items():
        messages.sort(
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            )
        )
        requires = _thread_requires_architect_reply(messages, caller_id)
        if requires_reply and not requires:
            continue
        last = messages[-1]
        entry_for_caller = _agent_peer_message_row_to_entry(last, caller_id)
        peer_architect_id = entry_for_caller["peer_id"]
        peer = state.agents.get(peer_architect_id)
        threads.append({
            "thread_id": group_thread_id,
            "peer_architect_id": peer_architect_id,
            "peer_name": str(getattr(peer, "name", "") or "").strip()
            or peer_architect_id,
            "last_message_at": float(last.get("created_at", 0) or 0),
            "requires_reply": requires,
            "messages": [
                _agent_peer_message_row_to_entry(row, caller_id)
                for row in messages
            ],
        })
    threads.sort(
        key=lambda item: (
            float(item.get("last_message_at", 0) or 0),
            str(item.get("thread_id", "") or ""),
        ),
        reverse=True,
    )
    return _compact_json({
        "type": "proposal_peer_inbox",
        "threads": threads[:limit],
    }), False


def _thinking_group_for_caller(caller_group: str, args: dict) -> tuple[str, str]:
    requested = str((args or {}).get("group", "") or "").strip()
    caller_group = str(caller_group or "").strip()
    if requested and requested != caller_group:
        return "", "group must match the architect's group"
    if not caller_group:
        return "", "architect is not assigned to a group"
    return caller_group, ""


def _thinking_bool_arg(args: dict, key: str, default: bool = False) -> bool:
    value = (args or {}).get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _thinking_limit(args: dict, default: int = 100, maximum: int = 500) -> int:
    try:
        limit = int((args or {}).get("limit", default) or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


def _thinking_item_group(item: dict | None) -> str:
    return str((item or {}).get("group_name", (item or {}).get("group", "")) or "").strip()


def _thinking_item_owned_by_caller(item: dict | None, caller_id: str) -> bool:
    return (
        str((item or {}).get("created_by_kind", "") or "").strip() == "architect"
        and str((item or {}).get("created_by_id", "") or "").strip()
        == str(caller_id or "").strip()
    )


def _with_thinking_owner_flag(item: dict | None, caller_id: str) -> dict:
    payload = dict(item or {})
    payload["caller_owned"] = _thinking_item_owned_by_caller(payload, caller_id)
    return payload


def _resolve_thinking_scratchpad_note(state, caller_group: str, args: dict, *,
                                      include_archived: bool = False
                                      ) -> tuple[str, dict | None, str]:
    ident = str(
        (args or {}).get("note", "")
        or (args or {}).get("note_id", "")
        or (args or {}).get("id", "")
        or ""
    ).strip()
    if not ident:
        return "", None, "note is required"
    note_id = state.resolve_scratchpad_note_id(ident, group=caller_group)
    note = state.load_scratchpad_note(note_id)
    if not note or _thinking_item_group(note) != caller_group:
        return "", None, "Scratchpad note not found"
    if note.get("deleted"):
        return "", None, "Scratchpad note not found"
    if note.get("archived") and not include_archived:
        return "", None, "Scratchpad note is archived"
    return str(note.get("id", "") or ""), note, ""


def _mind_map_ref_from_args(args: dict) -> str:
    return str(
        (args or {}).get("mind_map", "")
        or (args or {}).get("map", "")
        or (args or {}).get("map_id", "")
        or (args or {}).get("mind_map_id", "")
        or (args or {}).get("id", "")
        or ""
    ).strip()


def _mind_map_node_ref_from_args(args: dict) -> str:
    return str(
        (args or {}).get("node", "")
        or (args or {}).get("node_id", "")
        or (args or {}).get("id", "")
        or ""
    ).strip()


def _mind_map_link_ref_from_args(args: dict) -> str:
    return str(
        (args or {}).get("link", "")
        or (args or {}).get("link_id", "")
        or (args or {}).get("id", "")
        or ""
    ).strip()


def _resolve_thinking_mind_map(state, caller_group: str, args: dict, *,
                               include_archived: bool = False
                               ) -> tuple[str, dict | None, str]:
    ident = _mind_map_ref_from_args(args)
    if not ident:
        return "", None, "mind_map is required"
    map_id = state.resolve_mind_map_id(ident, group=caller_group)
    mind_map = state.load_mind_map(map_id, include_counts=True)
    if not mind_map or _thinking_item_group(mind_map) != caller_group:
        return "", None, "Mind Map not found"
    if mind_map.get("deleted"):
        return "", None, "Mind Map not found"
    if mind_map.get("archived") and not include_archived:
        return "", None, "Mind Map is archived"
    return str(mind_map.get("id", "") or ""), mind_map, ""


def _resolve_thinking_mind_map_for_node_or_link(state, caller_group: str,
                                                args: dict, *,
                                                item_kind: str
                                                ) -> tuple[str, dict | None, str]:
    if _mind_map_ref_from_args(args):
        return _resolve_thinking_mind_map(state, caller_group, args)
    if item_kind == "node":
        node = state.load_mind_map_node(_mind_map_node_ref_from_args(args))
        map_id = str((node or {}).get("map_id", "") or "").strip()
    else:
        link = state.load_mind_map_link(_mind_map_link_ref_from_args(args))
        map_id = str((link or {}).get("map_id", "") or "").strip()
    if not map_id:
        return "", None, "Mind Map not found"
    return _resolve_thinking_mind_map(
        state,
        caller_group,
        {"map_id": map_id},
    )


def _require_caller_owned_thinking_item(item: dict | None, caller_id: str,
                                        label: str) -> str:
    if _thinking_item_owned_by_caller(item, caller_id):
        return ""
    return f"{label} is not in this architect's caller-owned Thinking workspace"


async def _architect_thinking_tool(
        tool_name: str,
        state,
        caller_id: str,
        caller_group: str,
        args: dict) -> tuple[str, bool]:
    args = dict(args or {})
    group, group_error = _thinking_group_for_caller(caller_group, args)
    if group_error:
        return group_error, True
    include_archived = _thinking_bool_arg(args, "include_archived", False)

    if tool_name == "thinking_scratchpad_list":
        notes = [
            _with_thinking_owner_flag(note, caller_id)
            for note in state.list_scratchpad_notes(
                group=group,
                include_archived=include_archived,
                include_deleted=False,
                limit=_thinking_limit(args),
            )
        ]
        return _compact_json({
            "type": "thinking_scratchpad_list",
            "group": group,
            "notes": notes,
        }), False

    if tool_name == "thinking_scratchpad_show":
        _note_id, note, error = _resolve_thinking_scratchpad_note(
            state,
            group,
            args,
            include_archived=include_archived,
        )
        if error:
            return error, True
        payload = _with_thinking_owner_flag(note, caller_id)
        payload["type"] = "thinking_scratchpad_note"
        return _compact_json(payload), False

    if tool_name == "thinking_scratchpad_create":
        title = str(args.get("title", "") or "").strip()
        if not title:
            return "title is required", True
        try:
            note = await state.create_scratchpad_note_async({
                "group": group,
                "title": title,
                "body": str(args.get("body", "") or ""),
                "context": args.get("context", args.get("context_json", {})),
                "links": args.get("links", args.get("links_json", [])),
                "created_by_kind": "architect",
                "created_by_id": str(caller_id or "").strip(),
                "updated_by_kind": "architect",
                "updated_by_id": str(caller_id or "").strip(),
            })
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "thinking_scratchpad_created",
            "note": _with_thinking_owner_flag(note, caller_id),
        }), False

    if tool_name == "thinking_scratchpad_update":
        note_id, note, error = _resolve_thinking_scratchpad_note(
            state,
            group,
            args,
            include_archived=False,
        )
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            note,
            caller_id,
            "Scratchpad note",
        )
        if owner_error:
            return owner_error, True
        patch = {
            key: args[key]
            for key in ("title", "body", "context", "context_json", "links", "links_json")
            if key in args
        }
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = str(caller_id or "").strip()
        try:
            updated = await state.update_scratchpad_note_async(note_id, patch)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "thinking_scratchpad_updated",
            "note": _with_thinking_owner_flag(updated, caller_id),
        }), False

    if tool_name == "thinking_archive":
        actor = {
            "archived_by_kind": "architect",
            "archived_by_id": str(caller_id or "").strip(),
        }
        if _mind_map_ref_from_args(args):
            map_id, mind_map, error = _resolve_thinking_mind_map(
                state, group, args
            )
            if error:
                return error, True
            owner_error = _require_caller_owned_thinking_item(
                mind_map, caller_id, "Mind Map"
            )
            if owner_error:
                return owner_error, True
            archived = await state.archive_mind_map_async(map_id, **actor)
            return _compact_json({
                "type": "thinking_mind_map_archived",
                "mind_map": _with_thinking_owner_flag(archived, caller_id),
            }), False
        note_id, note, error = _resolve_thinking_scratchpad_note(
            state, group, args
        )
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            note, caller_id, "Scratchpad note"
        )
        if owner_error:
            return owner_error, True
        archived = await state.archive_scratchpad_note_async(note_id, **actor)
        return _compact_json({
            "type": "thinking_scratchpad_archived",
            "note": _with_thinking_owner_flag(archived, caller_id),
        }), False

    if tool_name == "thinking_mind_map_list":
        mind_maps = [
            _with_thinking_owner_flag(mind_map, caller_id)
            for mind_map in state.list_mind_maps(
                group=group,
                include_archived=include_archived,
                include_deleted=False,
                include_counts=True,
                limit=_thinking_limit(args),
            )
        ]
        return _compact_json({
            "type": "thinking_mind_map_list",
            "group": group,
            "mind_maps": mind_maps,
        }), False

    if tool_name == "thinking_mind_map_show":
        map_id, mind_map, error = _resolve_thinking_mind_map(
            state,
            group,
            args,
            include_archived=include_archived,
        )
        if error:
            return error, True
        payload = state.mind_map_payload(
            map_id,
            include_archived=include_archived,
            include_deleted=False,
        )
        if not payload:
            return "Mind Map not found", True
        payload = _with_thinking_owner_flag(payload, caller_id)
        payload["type"] = "thinking_mind_map"
        payload["mind_map"] = _with_thinking_owner_flag(mind_map, caller_id)
        return _compact_json(payload), False

    if tool_name == "thinking_mind_map_create":
        title = str(args.get("title", "") or "").strip()
        if not title:
            return "title is required", True
        try:
            mind_map = await state.create_mind_map_async({
                "group": group,
                "title": title,
                "description": str(args.get("description", "") or ""),
                "metadata": args.get("metadata", args.get("metadata_json", {})),
                "created_by_kind": "architect",
                "created_by_id": str(caller_id or "").strip(),
                "updated_by_kind": "architect",
                "updated_by_id": str(caller_id or "").strip(),
            })
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "thinking_mind_map_created",
            "mind_map": _with_thinking_owner_flag(mind_map, caller_id),
        }), False

    if tool_name == "thinking_mind_map_update":
        map_id, mind_map, error = _resolve_thinking_mind_map(state, group, args)
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map,
            caller_id,
            "Mind Map",
        )
        if owner_error:
            return owner_error, True
        patch = {
            key: args[key]
            for key in ("title", "description", "metadata", "metadata_json")
            if key in args
        }
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = str(caller_id or "").strip()
        try:
            updated = await state.update_mind_map_async(map_id, patch)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "thinking_mind_map_updated",
            "mind_map": _with_thinking_owner_flag(updated, caller_id),
        }), False

    if tool_name == "thinking_mind_map_node_create":
        map_id, mind_map, error = _resolve_thinking_mind_map(state, group, args)
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map,
            caller_id,
            "Mind Map",
        )
        if owner_error:
            return owner_error, True
        node_payload = {
            "label": args.get("label", args.get("title", "")),
            "title": args.get("title", ""),
            "notes": args.get("notes", ""),
            "node_type": args.get("node_type", args.get("type", "")),
            "tags": args.get("tags", args.get("tags_json", [])),
            "color": args.get("color", ""),
            "position": args.get("position", args.get("position_json", {})),
            "sort_order": args.get("sort_order", None),
            "created_by_kind": "architect",
            "created_by_id": str(caller_id or "").strip(),
            "updated_by_kind": "architect",
            "updated_by_id": str(caller_id or "").strip(),
        }
        if "x" in args:
            node_payload["x"] = args.get("x")
        if "y" in args:
            node_payload["y"] = args.get("y")
        try:
            node = await state.create_mind_map_node_async(map_id, node_payload)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({"type": "thinking_mind_map_node_created", "node": node}), False

    if tool_name in {"thinking_mind_map_node_update", "thinking_mind_map_node_position"}:
        map_id, mind_map, error = _resolve_thinking_mind_map_for_node_or_link(
            state,
            group,
            args,
            item_kind="node",
        )
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map,
            caller_id,
            "Mind Map",
        )
        if owner_error:
            return owner_error, True
        node_id = _mind_map_node_ref_from_args(args)
        if not node_id:
            return "node is required", True
        node = state.load_mind_map_node(node_id)
        if (
                not node
                or node.get("deleted")
                or str(node.get("map_id", "") or "").strip() != map_id):
            return "Mind Map node not found", True
        allowed = (
            ("position", "position_json", "x", "y")
            if tool_name == "thinking_mind_map_node_position"
            else (
                "label", "title", "notes", "node_type", "type", "tags",
                "tags_json", "color", "x", "y", "position",
                "position_json", "sort_order",
            )
        )
        patch = {key: args[key] for key in allowed if key in args}
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = str(caller_id or "").strip()
        try:
            updated = await state.update_mind_map_node_async(node_id, patch)
        except (TypeError, ValueError) as exc:
            return str(exc), True
        return _compact_json({
            "type": (
                "thinking_mind_map_node_positioned"
                if tool_name == "thinking_mind_map_node_position"
                else "thinking_mind_map_node_updated"
            ),
            "node": updated,
        }), False

    if tool_name == "thinking_mind_map_node_delete":
        map_id, mind_map, error = _resolve_thinking_mind_map_for_node_or_link(
            state, group, args, item_kind="node"
        )
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map, caller_id, "Mind Map"
        )
        if owner_error:
            return owner_error, True
        node_id = _mind_map_node_ref_from_args(args)
        node = state.load_mind_map_node(node_id)
        if (
            not node
            or node.get("deleted")
            or str(node.get("map_id", "") or "").strip() != map_id
        ):
            return "Mind Map node not found", True
        deleted = await state.delete_mind_map_node_async(
            node_id,
            deleted_by_kind="architect",
            deleted_by_id=str(caller_id or "").strip(),
        )
        return _compact_json({
            "type": "thinking_mind_map_node_deleted",
            "node": deleted,
        }), False

    if tool_name == "thinking_mind_map_link_create":
        map_id, mind_map, error = _resolve_thinking_mind_map(state, group, args)
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map,
            caller_id,
            "Mind Map",
        )
        if owner_error:
            return owner_error, True
        try:
            link = await state.create_mind_map_link_async(map_id, {
                "source_node_id": args.get("source_node_id", args.get("source", "")),
                "target_node_id": args.get("target_node_id", args.get("target", "")),
                "label": args.get("label", ""),
                "link_type": args.get("link_type", args.get("type", "")),
                "sort_order": args.get("sort_order", None),
                "created_by_kind": "architect",
                "created_by_id": str(caller_id or "").strip(),
                "updated_by_kind": "architect",
                "updated_by_id": str(caller_id or "").strip(),
            })
        except ValueError as exc:
            return str(exc), True
        return _compact_json({"type": "thinking_mind_map_link_created", "link": link}), False

    if tool_name == "thinking_mind_map_link_update":
        map_id, mind_map, error = _resolve_thinking_mind_map_for_node_or_link(
            state,
            group,
            args,
            item_kind="link",
        )
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map,
            caller_id,
            "Mind Map",
        )
        if owner_error:
            return owner_error, True
        link_id = _mind_map_link_ref_from_args(args)
        if not link_id:
            return "link is required", True
        link = state.load_mind_map_link(link_id)
        if (
                not link
                or link.get("deleted")
                or str(link.get("map_id", "") or "").strip() != map_id):
            return "Mind Map link not found", True
        allowed = (
            "label", "link_type", "type", "source_node_id", "source",
            "target_node_id", "target", "sort_order",
        )
        patch = {key: args[key] for key in allowed if key in args}
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = str(caller_id or "").strip()
        try:
            updated = await state.update_mind_map_link_async(link_id, patch)
        except (TypeError, ValueError) as exc:
            return str(exc), True
        return _compact_json({"type": "thinking_mind_map_link_updated", "link": updated}), False

    if tool_name == "thinking_mind_map_link_delete":
        map_id, mind_map, error = _resolve_thinking_mind_map_for_node_or_link(
            state, group, args, item_kind="link"
        )
        if error:
            return error, True
        owner_error = _require_caller_owned_thinking_item(
            mind_map, caller_id, "Mind Map"
        )
        if owner_error:
            return owner_error, True
        link_id = _mind_map_link_ref_from_args(args)
        link = state.load_mind_map_link(link_id)
        if (
            not link
            or link.get("deleted")
            or str(link.get("map_id", "") or "").strip() != map_id
        ):
            return "Mind Map link not found", True
        deleted = await state.delete_mind_map_link_async(
            link_id,
            deleted_by_kind="architect",
            deleted_by_id=str(caller_id or "").strip(),
        )
        return _compact_json({
            "type": "thinking_mind_map_link_deleted",
            "link": deleted,
        }), False

    return f"Unknown architect thinking tool: {tool_name}", True


def _idea_brief_item_group(item: dict | None) -> str:
    return str((item or {}).get("group_name", (item or {}).get("group", "")) or "").strip()


def _idea_brief_owned_by_caller(item: dict | None, caller_id: str) -> bool:
    return (
        str((item or {}).get("created_by_kind", "") or "").strip() == "architect"
        and str((item or {}).get("created_by_id", "") or "").strip()
        == str(caller_id or "").strip()
    )


def _with_idea_brief_owner_flag(item: dict | None, caller_id: str) -> dict:
    payload = dict(item or {})
    payload["caller_owned"] = _idea_brief_owned_by_caller(payload, caller_id)
    return payload


def _idea_brief_ref_from_args(args: dict) -> str:
    return str(
        (args or {}).get("idea_brief", "")
        or (args or {}).get("brief", "")
        or (args or {}).get("brief_id", "")
        or (args or {}).get("idea_brief_id", "")
        or (args or {}).get("id", "")
        or ""
    ).strip()


def _resolve_idea_brief_for_caller(state, caller_group: str, args: dict, *,
                                   include_archived: bool = False
                                   ) -> tuple[str, dict | None, str]:
    ident = _idea_brief_ref_from_args(args)
    if not ident:
        return "", None, "idea_brief is required"
    brief_id = state.resolve_idea_brief_id(ident, group=caller_group)
    brief = state.load_idea_brief(brief_id)
    if not brief or _idea_brief_item_group(brief) != caller_group:
        return "", None, "Idea Brief not found"
    if idea_brief_is_archived(brief) and not include_archived:
        return "", None, "Idea Brief is archived"
    return str(brief.get("id", "") or ""), brief, ""


def _require_caller_owned_idea_brief(item: dict | None, caller_id: str) -> str:
    if _idea_brief_owned_by_caller(item, caller_id):
        return ""
    return "Idea Brief is not caller-owned by this architect"


def _idea_brief_patch_from_args(args: dict) -> dict:
    allowed = {
        "title",
        "problem_opportunity",
        "why_it_matters",
        "proposed_shape",
        "smallest_useful_version",
        "risks_tradeoffs",
        "open_questions",
        "status",
        "thinking_links",
        "source_context",
    }
    return {key: args[key] for key in allowed if key in args}


async def _architect_idea_brief_tool(
        tool_name: str,
        state,
        caller_id: str,
        caller_group: str,
        args: dict) -> tuple[str, bool]:
    args = dict(args or {})
    group, group_error = _thinking_group_for_caller(caller_group, args)
    if group_error:
        return group_error, True
    include_archived = _thinking_bool_arg(args, "include_archived", False)

    if tool_name == "idea_brief_list":
        try:
            briefs = [
                _with_idea_brief_owner_flag(brief, caller_id)
                for brief in state.list_idea_briefs(
                    group=group,
                    status=str(args.get("status", "") or ""),
                    include_archived=include_archived,
                    limit=_thinking_limit(args),
                )
            ]
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_list",
            "group": group,
            "idea_briefs": briefs,
            **idea_brief_contract_metadata(),
        }), False

    if tool_name == "idea_brief_show":
        _brief_id, brief, error = _resolve_idea_brief_for_caller(
            state,
            group,
            args,
            include_archived=include_archived,
        )
        if error:
            return error, True
        payload = _with_idea_brief_owner_flag(brief, caller_id)
        payload["type"] = "idea_brief"
        payload.update(idea_brief_contract_metadata())
        return _compact_json(payload), False

    if tool_name == "idea_brief_create":
        problem = str(args.get("problem_opportunity", "") or "").strip()
        if not problem:
            return "problem_opportunity is required", True
        patch = _idea_brief_patch_from_args(args)
        patch.update({
            "group": group,
            "created_by_kind": "architect",
            "created_by_id": str(caller_id or "").strip(),
            "updated_by_kind": "architect",
            "updated_by_id": str(caller_id or "").strip(),
        })
        try:
            brief = await state.create_idea_brief_async(patch)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_created",
            "idea_brief": _with_idea_brief_owner_flag(brief, caller_id),
            **idea_brief_contract_metadata(),
        }), False

    brief_id, brief, error = _resolve_idea_brief_for_caller(
        state,
        group,
        args,
        include_archived=(include_archived or tool_name == "idea_brief_archive"),
    )
    if error:
        return error, True
    owner_error = _require_caller_owned_idea_brief(brief, caller_id)
    if owner_error:
        return owner_error, True

    if tool_name == "idea_brief_update":
        patch = _idea_brief_patch_from_args(args)
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = str(caller_id or "").strip()
        try:
            updated = await state.update_idea_brief_async(brief_id, patch)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_updated",
            "idea_brief": _with_idea_brief_owner_flag(updated, caller_id),
            **idea_brief_contract_metadata(),
        }), False

    if tool_name == "idea_brief_refine":
        patch = _idea_brief_patch_from_args(args)
        if "refinement_note" in args:
            patch["refinement_note"] = args.get("refinement_note", "")
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = str(caller_id or "").strip()
        try:
            refined = await state.refine_idea_brief_async(brief_id, patch)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_refined",
            "idea_brief": _with_idea_brief_owner_flag(refined, caller_id),
            **idea_brief_contract_metadata(),
        }), False

    if tool_name == "idea_brief_park":
        try:
            parked = await state.park_idea_brief_async(
                brief_id,
                parked_by_kind="architect",
                parked_by_id=str(caller_id or "").strip(),
                reason=str(args.get("reason", "") or ""),
            )
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_parked",
            "idea_brief": _with_idea_brief_owner_flag(parked, caller_id),
            **idea_brief_contract_metadata(),
        }), False

    if tool_name == "idea_brief_archive":
        try:
            archived = await state.archive_idea_brief_async(
                brief_id,
                archived_by_kind="architect",
                archived_by_id=str(caller_id or "").strip(),
                reason=str(args.get("reason", "") or ""),
            )
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_archived",
            "idea_brief": _with_idea_brief_owner_flag(archived, caller_id),
            **idea_brief_contract_metadata(),
        }), False

    if tool_name == "idea_brief_propose":
        try:
            proposed = await state.propose_idea_brief_async(
                brief_id,
                proposed_by_kind="architect",
                proposed_by_id=str(caller_id or "").strip(),
                note=str(args.get("note", args.get("proposal_note", "")) or ""),
                review_target=str(args.get("review_target", "") or ""),
            )
        except ValueError as exc:
            return str(exc), True
        return _compact_json({
            "type": "idea_brief_proposed",
            "idea_brief": _with_idea_brief_owner_flag(proposed, caller_id),
            "review_scope": IDEA_BRIEF_PROPOSAL_SCOPE,
            "proposal": proposed.get("proposal", {}) if proposed else {},
            "caveat": (
                "Marked proposed for product-safe review only; no task, "
                "decision acceptance, assignment, dispatch, merge, or deploy "
                "was created."
            ),
            **idea_brief_contract_metadata(),
        }), False

    return f"Unknown architect product Idea Brief tool: {tool_name}", True
