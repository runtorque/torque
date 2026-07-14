"""Initiative, area, and decision argument/visibility helpers."""

from torque.mcp_engineer_tools.shared import resolve_task as _resolve_task
from torque.mcp_scoped.architect_reports import _compact_json
from torque.mcp_scoped.common import (
    _caller_group,
    _dedupe_strings,
    _filter_tasks_for_caller,
    _load_architect_decision,
    _resolve_architect_engineer,
)

def _normalize_decision_links(state, caller_id: str, *,
                              task_ids=None,
                              engineer_ids=None) -> tuple[list[str], list[str], str]:
    visible_tasks = _filter_tasks_for_caller(state, "architect", caller_id)
    normalized_task_ids = []
    for task_ident in _dedupe_strings(task_ids):
        task_id = _resolve_task(state, task_ident)
        if not task_id or task_id not in visible_tasks:
            return [], [], f"Task not found: {task_ident}"
        normalized_task_ids.append(task_id)

    normalized_engineer_ids = []
    for engineer_ident in _dedupe_strings(engineer_ids):
        engineer_id, error_text = _resolve_architect_engineer(
            state, caller_id, engineer_ident
        )
        if not engineer_id:
            return [], [], error_text
        normalized_engineer_ids.append(engineer_id)

    return normalized_task_ids, normalized_engineer_ids, ""


def _initiative_visible_task_ids_for_caller(state, caller_kind: str,
                                            caller_id: str) -> set[str]:
    return set(_filter_tasks_for_caller(state, caller_kind, caller_id))


def _initiative_visible_decision_ids_for_caller(state, caller_kind: str,
                                                caller_id: str) -> set[str]:
    if caller_kind != "architect":
        return set()
    return {
        str(decision.get("id", "") or "")
        for decision in state.load_decisions_for_architect(
            caller_id,
            include_archived=False,
        )
        if str(decision.get("id", "") or "")
    }


def _initiative_scope_group(state, caller_id: str) -> str:
    return _caller_group(state, caller_id)


def _initiative_from_args(state, caller_id: str, args: dict) -> tuple[dict | None, str]:
    ident = str(
        args.get("initiative", "") or args.get("initiative_id", "")
        or args.get("id", "") or ""
    ).strip()
    if not ident:
        return None, "initiative is required"
    group = _initiative_scope_group(state, caller_id)
    initiative_id = state.resolve_initiative_id(ident, group=group)
    if not initiative_id:
        return None, "Initiative not found"
    initiative = state.load_initiative(initiative_id)
    if not initiative or str(initiative.get("group_name", "") or "").strip() != group:
        return None, "Initiative not found"
    return initiative, ""


def _architect_can_write_initiative(initiative: dict, architect_id: str) -> bool:
    architect_id = str(architect_id or "").strip()
    return bool(
        (str(initiative.get("owner_kind", "") or "") == "architect"
         and str(initiative.get("owner_id", "") or "").strip() == architect_id)
        or (str(initiative.get("created_by_kind", "") or "") == "architect"
            and str(initiative.get("created_by_id", "") or "").strip() == architect_id)
    )


def _initiative_read_json(state, caller_kind: str, caller_id: str,
                          args: dict, *, show: bool) -> tuple[str, bool]:
    group = _initiative_scope_group(state, caller_id)
    visible_task_ids = _initiative_visible_task_ids_for_caller(
        state, caller_kind, caller_id,
    )
    visible_decision_ids = _initiative_visible_decision_ids_for_caller(
        state, caller_kind, caller_id,
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


def _initiative_task_link_target(state, caller_kind: str, caller_id: str,
                                 task_ref: str) -> tuple[str, str]:
    task_id = _resolve_task(state, task_ref)
    if not task_id:
        return "", "Task not found"
    visible = _filter_tasks_for_caller(state, caller_kind, caller_id)
    if task_id not in visible:
        return "", "Task not found"
    return task_id, ""


def _initiative_decision_link_target(state, architect_id: str,
                                     decision_ref: str) -> tuple[str, str]:
    decision_id = str(decision_ref or "").strip()
    if not decision_id:
        return "", "decision_id is required"
    decision, error = _load_architect_decision(state, architect_id, decision_id)
    if not decision:
        return "", error
    return decision_id, ""


def _area_scope_group(state, caller_id: str) -> str:
    return _caller_group(state, caller_id)


def _area_from_args(state, caller_id: str, args: dict) -> tuple[dict | None, str]:
    ident = str(
        args.get("area", "") or args.get("area_id", "")
        or args.get("id", "") or ""
    ).strip()
    if not ident:
        return None, "area is required"
    group = _area_scope_group(state, caller_id)
    area_id = state.resolve_area_id(ident, group=group)
    if not area_id:
        return None, "Area not found"
    area = state.load_area(area_id)
    if not area or str(area.get("group_name", "") or "").strip() != group:
        return None, "Area not found"
    return area, ""


def _architect_can_write_area(area: dict, architect_id: str) -> bool:
    architect_id = str(architect_id or "").strip()
    return bool(
        (str(area.get("owner_kind", "") or "") == "architect"
         and str(area.get("owner_id", "") or "").strip() == architect_id)
        or (str(area.get("created_by_kind", "") or "") == "architect"
            and str(area.get("created_by_id", "") or "").strip() == architect_id)
    )


def _area_read_json(state, caller_kind: str, caller_id: str,
                    args: dict, *, show: bool) -> tuple[str, bool]:
    group = _area_scope_group(state, caller_id)
    visible_task_ids = _initiative_visible_task_ids_for_caller(
        state, caller_kind, caller_id,
    )
    visible_decision_ids = _initiative_visible_decision_ids_for_caller(
        state, caller_kind, caller_id,
    )
    decision_details = caller_kind == "architect"
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


def _area_task_link_target(state, caller_kind: str, caller_id: str,
                           task_ref: str) -> tuple[str, str]:
    return _initiative_task_link_target(state, caller_kind, caller_id, task_ref)


def _area_decision_link_target(state, architect_id: str,
                               decision_ref: str) -> tuple[str, str]:
    return _initiative_decision_link_target(state, architect_id, decision_ref)


def _area_initiative_link_target(state, caller_id: str,
                                 initiative_ref: str) -> tuple[str, str]:
    group = _area_scope_group(state, caller_id)
    initiative_id = state.resolve_initiative_id(initiative_ref, group=group)
    if not initiative_id:
        return "", "Initiative not found"
    initiative = state.load_initiative(initiative_id)
    if not initiative or str(initiative.get("group_name", "") or "").strip() != group:
        return "", "Initiative not found"
    return initiative_id, ""


def _area_area_link_target(state, caller_id: str, current_area_id: str,
                           area_ref: str) -> tuple[str, str]:
    group = _area_scope_group(state, caller_id)
    area_id = state.resolve_area_id(area_ref, group=group)
    if not area_id:
        return "", "Area not found"
    area = state.load_area(area_id)
    if not area or str(area.get("group_name", "") or "").strip() != group:
        return "", "Area not found"
    if area_id == str(current_area_id or ""):
        return "", "Area cannot link to itself"
    return area_id, ""


def _area_note_target_error(state, caller_kind: str, caller_id: str,
                            area_group: str, target_type: str,
                            target_id: str) -> str:
    target_type = str(target_type or "").strip().lower()
    target_id = str(target_id or "").strip()
    if not target_type and not target_id:
        return ""
    if target_type not in {"task", "decision", "initiative", "area"}:
        return "note target_type must be one of: task, decision, initiative, area"
    if not target_id:
        return "note target_id is required when target_type is set"
    if target_type == "task":
        task_id, error = _area_task_link_target(state, caller_kind, caller_id, target_id)
        if error:
            return error
        task = state.board_tasks.get(task_id)
        if str(getattr(task, "group", "") or "").strip() != area_group:
            return "Task is outside area group"
        return ""
    if target_type == "decision":
        decision_id, error = _area_decision_link_target(state, caller_id, target_id)
        return error if not decision_id else ""
    if target_type == "initiative":
        initiative_id, error = _area_initiative_link_target(state, caller_id, target_id)
        return error if not initiative_id else ""
    area_id, error = _area_area_link_target(state, caller_id, "", target_id)
    return error if not area_id else ""
