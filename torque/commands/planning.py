"""Planning and thinking command handlers.

This module owns the user-operated command surface for Areas, Initiatives,
Scratchpad notes, Mind Maps, and Idea Briefs.  Keeping the registry beside the
handlers makes route ownership explicit while :mod:`torque.server` remains a
compatibility facade and application composition root.
"""

from __future__ import annotations

from ..dispatch_registry import AsyncHandlerRegistry
from ..idea_briefs import (
    IDEA_BRIEF_PROPOSAL_SCOPE,
    IDEA_BRIEF_TEXT_FIELDS,
    idea_brief_contract_metadata,
    idea_brief_is_archived,
)
from ..state import MatrixState


def _initiative_error(message: str) -> dict:
    return {"type": "error", "message": str(message or "")}


def _initiative_actor_from_data(data: dict, *, default_kind: str = "user") -> dict:
    kind = str(data.get("actor_kind", "") or default_kind).strip().lower() or default_kind
    actor_id = str(data.get("actor_id", "") or "").strip()
    return {"kind": kind, "id": actor_id}


def _decision_belongs_to_group(state: MatrixState, decision_id: str,
                               group: str) -> bool:
    decision = state.load_decision(decision_id)
    if not decision:
        return False
    architect_id = str(decision.get("architect_id", "") or "").strip()
    architect = state.agents.get(architect_id)
    return bool(
        architect
        and str(getattr(architect, "group", "") or "").strip()
        == str(group or "").strip()
    )


async def _handle_initiative_command(data: dict, state: MatrixState) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "initiative_list":
        group = str(data.get("group", "") or "").strip()
        include_archived = bool(data.get("include_archived", False))
        initiatives = [
            state.initiative_payload(item["id"], include_links=True) or item
            for item in state.list_initiatives(
                group=group,
                include_archived=include_archived,
            )
        ]
        return {"type": "initiative_list", "group": group, "initiatives": initiatives}

    ident = str(
        data.get("initiative", "") or data.get("initiative_id", "")
        or data.get("id", "") or ""
    ).strip()
    group_hint = str(data.get("group", "") or "").strip()
    initiative_id = state.resolve_initiative_id(ident, group=group_hint)

    if cmd == "initiative_show":
        if not initiative_id:
            return _initiative_error("Initiative not found")
        payload = state.initiative_payload(initiative_id)
        if not payload:
            return _initiative_error("Initiative not found")
        payload["type"] = "initiative"
        return payload

    if cmd == "initiative_create":
        actor = _initiative_actor_from_data(data)
        owner_kind = str(data.get("owner_kind", "") or actor["kind"]).strip()
        owner_id = str(data.get("owner_id", "") or actor["id"]).strip()
        try:
            created = await state.create_initiative_async({
                "group": data.get("group", ""),
                "title": data.get("title", data.get("task", "")),
                "summary": data.get("summary", ""),
                "why": data.get("why", ""),
                "in_scope": data.get("in_scope", ""),
                "out_of_scope": data.get("out_of_scope", ""),
                "done_definition": data.get("done_definition", ""),
                "planning_status": data.get("planning_status", "triage"),
                "priority": data.get("priority", ""),
                "owner_kind": owner_kind,
                "owner_id": owner_id,
                "created_by_kind": actor["kind"],
                "created_by_id": actor["id"],
                "updated_by_kind": actor["kind"],
                "updated_by_id": actor["id"],
            })
        except ValueError as exc:
            return _initiative_error(str(exc))
        return {"type": "initiative_created", "initiative": created}

    if not initiative_id:
        return _initiative_error("Initiative not found")
    initiative = state.load_initiative(initiative_id)
    if not initiative:
        return _initiative_error("Initiative not found")

    if cmd == "initiative_update":
        actor = _initiative_actor_from_data(data)
        allowed = {
            "title", "summary", "why", "in_scope", "out_of_scope",
            "done_definition", "planning_status", "priority", "owner_kind",
            "owner_id", "slug",
        }
        patch = {key: data[key] for key in allowed if key in data}
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_initiative_async(initiative_id, patch)
        except ValueError as exc:
            return _initiative_error(str(exc))
        return {"type": "initiative_updated", "initiative": updated}

    if cmd == "initiative_archive":
        actor = _initiative_actor_from_data(data)
        archived = await state.archive_initiative_async(
            initiative_id,
            archived_by_kind=actor["kind"],
            archived_by_id=actor["id"],
        )
        return {"type": "initiative_archived", "initiative": archived}

    task_ref = str(data.get("task", "") or data.get("task_id", "") or "").strip()
    decision_ref = str(
        data.get("decision", "") or data.get("decision_id", "") or ""
    ).strip()
    actor = _initiative_actor_from_data(data)

    if cmd in {"initiative_link_task", "initiative_unlink_task"}:
        task_id = state.resolve_board_task_id(task_ref)
        task = state.board_tasks.get(task_id)
        if not task:
            return _initiative_error("Task not found")
        if str(getattr(task, "group", "") or "").strip() != str(
                initiative.get("group_name", "") or "").strip():
            return _initiative_error("Task is outside initiative group")
        if cmd == "initiative_link_task":
            link = await state.save_initiative_link_async(
                initiative_id,
                "task",
                task_id,
                created_by_kind=actor["kind"],
                created_by_id=actor["id"],
            )
            return {"type": "initiative_task_linked", "link": link}
        removed = await state.delete_initiative_link_async(
            initiative_id, "task", task_id,
        )
        return {"type": "initiative_task_unlinked", "removed": removed}

    if cmd in {"initiative_link_decision", "initiative_unlink_decision"}:
        decision_id = decision_ref
        if not decision_id or not _decision_belongs_to_group(
                state, decision_id, initiative.get("group_name", "")):
            return _initiative_error("Decision not found")
        if cmd == "initiative_link_decision":
            link = await state.save_initiative_link_async(
                initiative_id,
                "decision",
                decision_id,
                created_by_kind=actor["kind"],
                created_by_id=actor["id"],
            )
            return {"type": "initiative_decision_linked", "link": link}
        removed = await state.delete_initiative_link_async(
            initiative_id, "decision", decision_id,
        )
        return {"type": "initiative_decision_unlinked", "removed": removed}

    return _initiative_error(f"Unknown initiative command: {cmd}")


def _area_error(message: str) -> dict:
    return {"type": "error", "message": str(message or "")}


def _area_actor_from_data(data: dict, *, default_kind: str = "user") -> dict:
    kind = str(data.get("actor_kind", "") or default_kind).strip().lower() or default_kind
    actor_id = str(data.get("actor_id", "") or "").strip()
    return {"kind": kind, "id": actor_id}


def _area_note_target_fields(data: dict) -> dict:
    target_type = str(data.get("target_type", "") or "").strip().lower()
    target_id = str(data.get("target_id", "") or data.get("target", "") or "").strip()
    return {"target_type": target_type, "target_id": target_id}


def _validate_area_note_target(state: MatrixState, area_group: str,
                               target_type: str, target_id: str) -> str:
    if not target_type and not target_id:
        return ""
    if target_type not in {"task", "decision", "initiative", "area"}:
        return "note target_type must be one of: task, decision, initiative, area"
    if not target_id:
        return "note target_id is required when target_type is set"
    if target_type == "task":
        task_id = state.resolve_board_task_id(target_id)
        task = state.board_tasks.get(task_id)
        if not task or str(getattr(task, "group", "") or "").strip() != area_group:
            return "Task not found"
        return ""
    if target_type == "decision":
        if not _decision_belongs_to_group(state, target_id, area_group):
            return "Decision not found"
        return ""
    if target_type == "initiative":
        initiative_id = state.resolve_initiative_id(target_id, group=area_group)
        initiative = state.load_initiative(initiative_id)
        if not initiative or str(initiative.get("group_name", "") or "").strip() != area_group:
            return "Initiative not found"
        return ""
    target_area_id = state.resolve_area_id(target_id, group=area_group)
    target_area = state.load_area(target_area_id)
    if not target_area or str(target_area.get("group_name", "") or "").strip() != area_group:
        return "Area not found"
    return ""


async def _handle_area_command(data: dict, state: MatrixState) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "area_list":
        group = str(data.get("group", "") or "").strip()
        include_archived = bool(data.get("include_archived", False))
        try:
            limit = min(max(int(data.get("limit", 100)), 1), 500)
        except (TypeError, ValueError):
            limit = 100
        areas = [
            state.area_payload(
                item["id"],
                include_links=bool(data.get("include_links", False)),
                include_notes=bool(data.get("include_notes", False)),
                decision_details=True,
            ) or item
            for item in state.list_areas(
                group=group,
                include_archived=include_archived,
                limit=limit,
            )
        ]
        return {"type": "area_list", "group": group, "areas": areas}

    ident = str(
        data.get("area", "") or data.get("area_id", "")
        or data.get("id", "") or ""
    ).strip()
    group_hint = str(data.get("group", "") or "").strip()
    area_id = state.resolve_area_id(ident, group=group_hint)

    if cmd == "area_show":
        if not area_id:
            return _area_error("Area not found")
        payload = state.area_payload(area_id, decision_details=True)
        if not payload:
            return _area_error("Area not found")
        payload["type"] = "area"
        return payload

    if cmd == "area_create":
        actor = _area_actor_from_data(data)
        owner_kind = str(data.get("owner_kind", "") or actor["kind"]).strip()
        owner_id = str(data.get("owner_id", "") or actor["id"]).strip()
        try:
            created = await state.create_area_async({
                "group": data.get("group", ""),
                "title": data.get("title", ""),
                "area_type": data.get("area_type", ""),
                "lifecycle": data.get("lifecycle", "planned"),
                "summary": data.get("summary", ""),
                "user_purpose": data.get("user_purpose", ""),
                "system_purpose": data.get("system_purpose", ""),
                "in_scope": data.get("in_scope", ""),
                "out_of_scope": data.get("out_of_scope", ""),
                "owner_kind": owner_kind,
                "owner_id": owner_id,
                "created_by_kind": actor["kind"],
                "created_by_id": actor["id"],
                "updated_by_kind": actor["kind"],
                "updated_by_id": actor["id"],
            })
        except ValueError as exc:
            return _area_error(str(exc))
        return {"type": "area_created", "area": created}

    if not area_id:
        return _area_error("Area not found")
    area = state.load_area(area_id)
    if not area:
        return _area_error("Area not found")
    area_group = str(area.get("group_name", "") or "").strip()

    if cmd == "area_update":
        actor = _area_actor_from_data(data)
        allowed = {
            "title", "area_type", "lifecycle", "summary", "user_purpose",
            "system_purpose", "in_scope", "out_of_scope", "owner_kind",
            "owner_id", "slug",
        }
        patch = {key: data[key] for key in allowed if key in data}
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_area_async(area_id, patch)
        except ValueError as exc:
            return _area_error(str(exc))
        return {"type": "area_updated", "area": updated}

    if cmd == "area_archive":
        actor = _area_actor_from_data(data)
        archived = await state.archive_area_async(
            area_id,
            archived_by_kind=actor["kind"],
            archived_by_id=actor["id"],
        )
        return {"type": "area_archived", "area": archived}

    actor = _area_actor_from_data(data)
    link_type = str(data.get("link_type", "") or "").strip().lower()
    if cmd.startswith("area_link_") or cmd.startswith("area_unlink_"):
        suffix = cmd.split("_", 2)[2]
        link_type = link_type or suffix
        if link_type == "task":
            target_ref = str(data.get("task", "") or data.get("task_id", "") or data.get("target_id", "") or "").strip()
            target_id = state.resolve_board_task_id(target_ref)
            task = state.board_tasks.get(target_id)
            if not task:
                return _area_error("Task not found")
            if str(getattr(task, "group", "") or "").strip() != area_group:
                return _area_error("Task is outside area group")
        elif link_type == "decision":
            target_id = str(data.get("decision", "") or data.get("decision_id", "") or data.get("target_id", "") or "").strip()
            if not target_id or not _decision_belongs_to_group(state, target_id, area_group):
                return _area_error("Decision not found")
        elif link_type == "initiative":
            target_ref = str(data.get("initiative", "") or data.get("initiative_id", "") or data.get("target_id", "") or "").strip()
            target_id = state.resolve_initiative_id(target_ref, group=area_group)
            initiative = state.load_initiative(target_id)
            if not initiative or str(initiative.get("group_name", "") or "").strip() != area_group:
                return _area_error("Initiative not found")
        elif link_type == "area":
            target_ref = str(data.get("target_area", "") or data.get("target_area_id", "") or data.get("target_id", "") or "").strip()
            target_id = state.resolve_area_id(target_ref, group=area_group)
            target_area = state.load_area(target_id)
            if not target_area or str(target_area.get("group_name", "") or "").strip() != area_group:
                return _area_error("Area not found")
            if target_id == area_id:
                return _area_error("Area cannot link to itself")
        else:
            return _area_error("link_type must be one of: task, decision, initiative, area")
        relation = str(data.get("relation", "") or "").strip().lower()
        if cmd.startswith("area_link_"):
            try:
                link = await state.save_area_link_async(
                    area_id,
                    link_type,
                    target_id,
                    relation=relation,
                    created_by_kind=actor["kind"],
                    created_by_id=actor["id"],
                )
            except ValueError as exc:
                return _area_error(str(exc))
            return {"type": "area_linked", "link": link}
        try:
            removed = await state.delete_area_link_async(
                area_id,
                link_type,
                target_id,
                relation,
            )
        except ValueError as exc:
            return _area_error(str(exc))
        return {"type": "area_unlinked", "removed": removed}

    if cmd == "area_note_create":
        actor = _area_actor_from_data(data)
        target = _area_note_target_fields(data)
        target_error = _validate_area_note_target(
            state, area_group, target["target_type"], target["target_id"]
        )
        if target_error:
            return _area_error(target_error)
        try:
            note = await state.create_area_note_async(area_id, {
                "note_type": data.get("note_type", data.get("type", "")),
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                **target,
                "created_by_kind": actor["kind"],
                "created_by_id": actor["id"],
                "updated_by_kind": actor["kind"],
                "updated_by_id": actor["id"],
            })
        except ValueError as exc:
            return _area_error(str(exc))
        return {"type": "area_note_created", "note": note}

    if cmd in {"area_note_update", "area_note_archive"}:
        note_id = data.get("note_id", data.get("note", ""))
        note = state.load_area_note(note_id)
        if not note or str(note.get("area_id", "") or "") != area_id:
            return _area_error("Area note not found")
        actor = _area_actor_from_data(data)
        if cmd == "area_note_archive":
            archived = await state.archive_area_note_async(
                note_id,
                archived_by_kind=actor["kind"],
                archived_by_id=actor["id"],
            )
            return {"type": "area_note_archived", "note": archived}
        allowed = {"note_type", "title", "body", "target_type", "target_id"}
        patch = {key: data[key] for key in allowed if key in data}
        target_type = str(patch.get("target_type", note.get("target_type", "")) or "").strip().lower()
        target_id = str(patch.get("target_id", note.get("target_id", "")) or "").strip()
        target_error = _validate_area_note_target(state, area_group, target_type, target_id)
        if target_error:
            return _area_error(target_error)
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_area_note_async(note_id, patch)
        except ValueError as exc:
            return _area_error(str(exc))
        return {"type": "area_note_updated", "note": updated}

    return _area_error(f"Unknown area command: {cmd}")


def _thinking_error(message: str, code: str = "thinking_error") -> dict:
    return {
        "type": "error",
        "code": str(code or "thinking_error"),
        "message": str(message or ""),
    }


def _thinking_actor_from_data(data: dict, *, default_kind: str = "user") -> dict:
    kind = str(data.get("actor_kind", "") or default_kind).strip().lower() or default_kind
    actor_id = str(data.get("actor_id", "") or "").strip()
    return {"kind": kind, "id": actor_id}


def _thinking_group_from_data(data: dict, *, required: bool = False) -> tuple[str, dict | None]:
    group = str(data.get("group", "") or "").strip()
    if required and not group:
        return "", _thinking_error("group is required", "group_required")
    return group, None


def _thinking_item_scope_error(item: dict | None, group: str, label: str,
                               *, include_archived: bool = False,
                               include_deleted: bool = False) -> dict | None:
    if not item:
        return _thinking_error(f"{label} not found", "not_found")
    item_group = str(item.get("group_name", item.get("group", "")) or "").strip()
    if group and item_group and item_group != group:
        return _thinking_error(f"{label} is outside group", "out_of_scope")
    if item.get("deleted") and not include_deleted:
        return _thinking_error(f"{label} is deleted", "deleted")
    if item.get("archived") and not include_archived:
        return _thinking_error(f"{label} is archived", "archived")
    return None


def _thinking_bool(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


async def _handle_scratchpad_command(data: dict, state: MatrixState) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    include_archived = _thinking_bool(data, "include_archived", False)
    include_deleted = _thinking_bool(data, "include_deleted", False)
    if cmd == "scratchpad_note_list":
        group, error = _thinking_group_from_data(data, required=True)
        if error:
            return error
        try:
            limit = min(max(int(data.get("limit", 200)), 1), 1000)
        except (TypeError, ValueError):
            limit = 200
        notes = state.list_scratchpad_notes(
            group=group,
            include_archived=include_archived,
            include_deleted=include_deleted,
            limit=limit,
        )
        return {
            "type": "scratchpad_note_list",
            "group": group,
            "notes": notes,
        }

    if cmd == "scratchpad_note_create":
        group, error = _thinking_group_from_data(data, required=True)
        if error:
            return error
        actor = _thinking_actor_from_data(data)
        try:
            note = await state.create_scratchpad_note_async({
                "group": group,
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "context": data.get("context", data.get("context_json", {})),
                "links": data.get("links", data.get("links_json", [])),
                "created_by_kind": actor["kind"],
                "created_by_id": actor["id"],
                "updated_by_kind": actor["kind"],
                "updated_by_id": actor["id"],
            })
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "scratchpad_note_created", "note": note}

    ident = str(
        data.get("note", "") or data.get("note_id", "") or data.get("id", "")
        or ""
    ).strip()
    group_hint, _ = _thinking_group_from_data(data, required=False)
    note_id = state.resolve_scratchpad_note_id(ident, group=group_hint)
    note = state.load_scratchpad_note(note_id)
    error = _thinking_item_scope_error(
        note,
        group_hint,
        "Scratchpad note",
        include_archived=include_archived or cmd in {"scratchpad_note_archive", "scratchpad_note_delete"},
        include_deleted=include_deleted,
    )
    if error:
        return error

    if cmd == "scratchpad_note_show":
        payload = dict(note)
        payload["type"] = "scratchpad_note"
        return payload

    if note.get("deleted"):
        return _thinking_error("Scratchpad note is deleted", "deleted")
    if note.get("archived") and cmd not in {"scratchpad_note_delete"}:
        return _thinking_error("Scratchpad note is archived", "archived")

    actor = _thinking_actor_from_data(data)
    if cmd == "scratchpad_note_update":
        allowed = {"title", "body", "context", "context_json", "links", "links_json", "slug"}
        patch = {key: data[key] for key in allowed if key in data}
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_scratchpad_note_async(note_id, patch)
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "scratchpad_note_updated", "note": updated}

    if cmd == "scratchpad_note_archive":
        archived = await state.archive_scratchpad_note_async(
            note_id,
            archived_by_kind=actor["kind"],
            archived_by_id=actor["id"],
        )
        return {"type": "scratchpad_note_archived", "note": archived}

    if cmd == "scratchpad_note_delete":
        deleted = await state.delete_scratchpad_note_async(
            note_id,
            deleted_by_kind=actor["kind"],
            deleted_by_id=actor["id"],
        )
        return {"type": "scratchpad_note_deleted", "note": deleted}

    return _thinking_error(f"Unknown scratchpad command: {cmd}", "unknown_command")


def _mind_map_ref_from_data(data: dict) -> str:
    return str(
        data.get("mind_map", "") or data.get("map", "")
        or data.get("map_id", "") or data.get("mind_map_id", "")
        or data.get("id", "") or ""
    ).strip()


def _mind_map_node_ref_from_data(data: dict) -> str:
    explicit = str(data.get("node", "") or data.get("node_id", "") or "").strip()
    if explicit:
        return explicit
    return str(data.get("id", "") or "").strip()


def _mind_map_link_ref_from_data(data: dict) -> str:
    explicit = str(data.get("link", "") or data.get("link_id", "") or "").strip()
    if explicit:
        return explicit
    return str(data.get("id", "") or "").strip()


def _mind_map_order_from_data(data: dict, primary: str) -> list:
    value = data.get(primary)
    if value is None:
        value = data.get("order")
    return value if isinstance(value, list) else []


async def _resolve_mind_map_for_command(data: dict, state: MatrixState, *,
                                        include_archived: bool = False,
                                        include_deleted: bool = False,
                                        label: str = "Mind Map"
                                        ) -> tuple[str, dict | None, dict | None]:
    group_hint, _ = _thinking_group_from_data(data, required=False)
    map_ref = _mind_map_ref_from_data(data)
    map_id = state.resolve_mind_map_id(map_ref, group=group_hint)
    mind_map = state.load_mind_map(map_id, include_counts=True)
    error = _thinking_item_scope_error(
        mind_map,
        group_hint,
        label,
        include_archived=include_archived,
        include_deleted=include_deleted,
    )
    if error:
        return "", None, error
    return map_id, mind_map, None


async def _handle_mind_map_command(data: dict, state: MatrixState) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    include_archived = _thinking_bool(data, "include_archived", False)
    include_deleted = _thinking_bool(data, "include_deleted", False)

    if cmd == "mind_map_list":
        group, error = _thinking_group_from_data(data, required=True)
        if error:
            return error
        try:
            limit = min(max(int(data.get("limit", 200)), 1), 1000)
        except (TypeError, ValueError):
            limit = 200
        maps = state.list_mind_maps(
            group=group,
            include_archived=include_archived,
            include_deleted=include_deleted,
            include_counts=True,
            limit=limit,
        )
        return {"type": "mind_map_list", "group": group, "mind_maps": maps}

    if cmd == "mind_map_create":
        group, error = _thinking_group_from_data(data, required=True)
        if error:
            return error
        actor = _thinking_actor_from_data(data)
        try:
            mind_map = await state.create_mind_map_async({
                "group": group,
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "metadata": data.get("metadata", data.get("metadata_json", {})),
                "created_by_kind": actor["kind"],
                "created_by_id": actor["id"],
                "updated_by_kind": actor["kind"],
                "updated_by_id": actor["id"],
            })
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_created", "mind_map": mind_map}

    if not any(data.get(key) for key in ("mind_map", "map", "map_id", "mind_map_id")):
        if cmd in {"mind_map_node_update", "mind_map_node_position", "mind_map_node_delete"}:
            node = state.load_mind_map_node(_mind_map_node_ref_from_data(data))
            if node:
                data = dict(data)
                data["map_id"] = node.get("map_id", "")
        elif cmd in {"mind_map_link_update", "mind_map_link_delete"}:
            link = state.load_mind_map_link(_mind_map_link_ref_from_data(data))
            if link:
                data = dict(data)
                data["map_id"] = link.get("map_id", "")

    map_include_archived = include_archived or cmd in {
        "mind_map_archive", "mind_map_delete",
    }
    map_id, mind_map, error = await _resolve_mind_map_for_command(
        data,
        state,
        include_archived=map_include_archived,
        include_deleted=include_deleted,
    )
    if error:
        return error

    if cmd == "mind_map_show":
        payload = state.mind_map_payload(
            map_id,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )
        if not payload:
            return _thinking_error("Mind Map not found", "not_found")
        payload["type"] = "mind_map"
        return payload

    if mind_map.get("deleted"):
        return _thinking_error("Mind Map is deleted", "deleted")
    if mind_map.get("archived") and cmd not in {"mind_map_delete"}:
        return _thinking_error("Mind Map is archived", "archived")

    actor = _thinking_actor_from_data(data)
    if cmd == "mind_map_update":
        allowed = {"title", "description", "metadata", "metadata_json", "slug"}
        patch = {key: data[key] for key in allowed if key in data}
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_mind_map_async(map_id, patch)
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_updated", "mind_map": updated}

    if cmd == "mind_map_archive":
        archived = await state.archive_mind_map_async(
            map_id,
            archived_by_kind=actor["kind"],
            archived_by_id=actor["id"],
        )
        return {"type": "mind_map_archived", "mind_map": archived}

    if cmd == "mind_map_delete":
        deleted = await state.delete_mind_map_async(
            map_id,
            deleted_by_kind=actor["kind"],
            deleted_by_id=actor["id"],
        )
        return {"type": "mind_map_deleted", "mind_map": deleted}

    if cmd == "mind_map_node_create":
        node_payload = {
            "label": data.get("label", data.get("title", "")),
            "title": data.get("title", ""),
            "notes": data.get("notes", ""),
            "node_type": data.get("node_type", data.get("type", "")),
            "tags": data.get("tags", data.get("tags_json", [])),
            "color": data.get("color", ""),
            "position": data.get("position", data.get("position_json", {})),
            "sort_order": data.get("sort_order", None),
            "created_by_kind": actor["kind"],
            "created_by_id": actor["id"],
            "updated_by_kind": actor["kind"],
            "updated_by_id": actor["id"],
        }
        if "x" in data:
            node_payload["x"] = data.get("x")
        if "y" in data:
            node_payload["y"] = data.get("y")
        try:
            node = await state.create_mind_map_node_async(map_id, node_payload)
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_node_created", "node": node}

    if cmd in {"mind_map_node_update", "mind_map_node_position", "mind_map_node_delete"}:
        node_id = _mind_map_node_ref_from_data(data)
        node = state.load_mind_map_node(node_id)
        if (
                not node or node.get("deleted")
                or str(node.get("map_id", "") or "") != map_id):
            return _thinking_error("Mind Map node not found", "not_found")
        if cmd == "mind_map_node_delete":
            deleted = await state.delete_mind_map_node_async(
                node_id,
                deleted_by_kind=actor["kind"],
                deleted_by_id=actor["id"],
            )
            return {"type": "mind_map_node_deleted", "node": deleted}
        allowed = {
            "label", "title", "notes", "node_type", "type", "tags",
            "tags_json", "color", "x", "y", "position", "position_json",
            "sort_order",
        }
        patch = {key: data[key] for key in allowed if key in data}
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_mind_map_node_async(node_id, patch)
        except (TypeError, ValueError) as exc:
            return _thinking_error(str(exc), "validation_error")
        return {
            "type": (
                "mind_map_node_positioned"
                if cmd == "mind_map_node_position"
                else "mind_map_node_updated"
            ),
            "node": updated,
        }

    if cmd == "mind_map_node_reorder":
        order = _mind_map_order_from_data(data, "nodes") or _mind_map_order_from_data(data, "node_order")
        try:
            nodes = await state.reorder_mind_map_nodes_async(
                map_id,
                order,
                updated_by_kind=actor["kind"],
                updated_by_id=actor["id"],
            )
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_node_reordered", "nodes": nodes, "map_id": map_id}

    if cmd == "mind_map_link_create":
        try:
            link = await state.create_mind_map_link_async(map_id, {
                "source_node_id": data.get("source_node_id", data.get("source", "")),
                "target_node_id": data.get("target_node_id", data.get("target", "")),
                "label": data.get("label", ""),
                "link_type": data.get("link_type", data.get("type", "")),
                "sort_order": data.get("sort_order", None),
                "created_by_kind": actor["kind"],
                "created_by_id": actor["id"],
                "updated_by_kind": actor["kind"],
                "updated_by_id": actor["id"],
            })
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_link_created", "link": link}

    if cmd in {"mind_map_link_update", "mind_map_link_delete"}:
        link_id = _mind_map_link_ref_from_data(data)
        link = state.load_mind_map_link(link_id)
        if (
                not link or link.get("deleted")
                or str(link.get("map_id", "") or "") != map_id):
            return _thinking_error("Mind Map link not found", "not_found")
        if cmd == "mind_map_link_delete":
            deleted = await state.delete_mind_map_link_async(
                link_id,
                deleted_by_kind=actor["kind"],
                deleted_by_id=actor["id"],
            )
            return {"type": "mind_map_link_deleted", "link": deleted}
        allowed = {
            "label", "link_type", "type", "source_node_id", "source",
            "target_node_id", "target", "sort_order",
        }
        patch = {key: data[key] for key in allowed if key in data}
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_mind_map_link_async(link_id, patch)
        except (TypeError, ValueError) as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_link_updated", "link": updated}

    if cmd == "mind_map_link_reorder":
        order = _mind_map_order_from_data(data, "links") or _mind_map_order_from_data(data, "link_order")
        try:
            links = await state.reorder_mind_map_links_async(
                map_id,
                order,
                updated_by_kind=actor["kind"],
                updated_by_id=actor["id"],
            )
        except ValueError as exc:
            return _thinking_error(str(exc), "validation_error")
        return {"type": "mind_map_link_reordered", "links": links, "map_id": map_id}

    return _thinking_error(f"Unknown mind map command: {cmd}", "unknown_command")


def _idea_brief_error(message: str, code: str = "idea_brief_error") -> dict:
    return {
        "type": "error",
        "code": str(code or "idea_brief_error"),
        "message": str(message or ""),
        **idea_brief_contract_metadata(),
    }


def _idea_brief_ref_from_data(data: dict) -> str:
    return str(
        data.get("idea_brief", "")
        or data.get("brief", "")
        or data.get("brief_id", "")
        or data.get("idea_brief_id", "")
        or data.get("id", "")
        or ""
    ).strip()


def _idea_brief_scope_error(brief: dict | None, group: str, *,
                            include_archived: bool = False) -> dict | None:
    if not brief:
        return _idea_brief_error("Idea Brief not found", "not_found")
    item_group = str(brief.get("group_name", brief.get("group", "")) or "").strip()
    if group and item_group and item_group != group:
        return _idea_brief_error("Idea Brief is outside group", "out_of_scope")
    if idea_brief_is_archived(brief) and not include_archived:
        return _idea_brief_error("Idea Brief is archived", "archived")
    return None


def _idea_brief_patch_from_data(data: dict) -> dict:
    allowed = set(IDEA_BRIEF_TEXT_FIELDS) | {
        "status",
        "slug",
        "thinking_links",
        "thinking_links_json",
        "source_context",
        "source_context_json",
        "proposal",
        "proposal_json",
        "refinement_log",
        "refinement_log_json",
    }
    return {key: data[key] for key in allowed if key in data}


async def _handle_idea_brief_command(data: dict, state: MatrixState) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    include_archived = _thinking_bool(data, "include_archived", False)
    group, group_error = _thinking_group_from_data(data, required=cmd in {
        "idea_brief_list",
        "idea_brief_create",
    })
    if group_error:
        return _idea_brief_error(group_error.get("message", "group is required"),
                                 group_error.get("code", "group_required"))

    if cmd == "idea_brief_list":
        try:
            limit = min(max(int(data.get("limit", 200)), 1), 1000)
        except (TypeError, ValueError):
            limit = 200
        try:
            briefs = state.list_idea_briefs(
                group=group,
                status=str(data.get("status", "") or ""),
                include_archived=include_archived,
                created_by_id=str(data.get("created_by_id", "") or ""),
                limit=limit,
            )
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_list",
            "group": group,
            "idea_briefs": briefs,
            **idea_brief_contract_metadata(),
        }

    actor = _thinking_actor_from_data(data)
    if cmd == "idea_brief_create":
        payload = _idea_brief_patch_from_data(data)
        payload.update({
            "group": group,
            "created_by_kind": actor["kind"],
            "created_by_id": actor["id"],
            "updated_by_kind": actor["kind"],
            "updated_by_id": actor["id"],
        })
        try:
            brief = await state.create_idea_brief_async(payload)
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_created",
            "idea_brief": brief,
            **idea_brief_contract_metadata(),
        }

    ident = _idea_brief_ref_from_data(data)
    group_hint = group
    brief_id = state.resolve_idea_brief_id(ident, group=group_hint)
    brief = state.load_idea_brief(brief_id)
    error = _idea_brief_scope_error(
        brief,
        group_hint,
        include_archived=include_archived or cmd == "idea_brief_archive",
    )
    if error:
        return error

    if cmd == "idea_brief_show":
        payload = dict(brief)
        payload["type"] = "idea_brief"
        payload.update(idea_brief_contract_metadata())
        return payload

    if idea_brief_is_archived(brief):
        return _idea_brief_error("Idea Brief is archived", "archived")

    if cmd == "idea_brief_update":
        patch = _idea_brief_patch_from_data(data)
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            updated = await state.update_idea_brief_async(brief_id, patch)
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_updated",
            "idea_brief": updated,
            **idea_brief_contract_metadata(),
        }

    if cmd == "idea_brief_refine":
        patch = _idea_brief_patch_from_data(data)
        if "refinement_note" in data:
            patch["refinement_note"] = data.get("refinement_note", "")
        patch["updated_by_kind"] = actor["kind"]
        patch["updated_by_id"] = actor["id"]
        try:
            refined = await state.refine_idea_brief_async(brief_id, patch)
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_refined",
            "idea_brief": refined,
            **idea_brief_contract_metadata(),
        }

    if cmd == "idea_brief_park":
        try:
            parked = await state.park_idea_brief_async(
                brief_id,
                parked_by_kind=actor["kind"],
                parked_by_id=actor["id"],
                reason=str(data.get("reason", "") or ""),
            )
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_parked",
            "idea_brief": parked,
            **idea_brief_contract_metadata(),
        }

    if cmd == "idea_brief_archive":
        try:
            archived = await state.archive_idea_brief_async(
                brief_id,
                archived_by_kind=actor["kind"],
                archived_by_id=actor["id"],
                reason=str(data.get("reason", "") or ""),
            )
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_archived",
            "idea_brief": archived,
            **idea_brief_contract_metadata(),
        }

    if cmd in {"idea_brief_propose", "idea_brief_promote"}:
        try:
            proposed = await state.propose_idea_brief_async(
                brief_id,
                proposed_by_kind=actor["kind"],
                proposed_by_id=actor["id"],
                note=str(data.get("note", data.get("proposal_note", "")) or ""),
                review_target=str(data.get("review_target", "") or ""),
            )
        except ValueError as exc:
            return _idea_brief_error(str(exc), "validation_error")
        return {
            "type": "idea_brief_proposed",
            "idea_brief": proposed,
            "review_scope": IDEA_BRIEF_PROPOSAL_SCOPE,
            "proposal": proposed.get("proposal", {}) if proposed else {},
            "caveat": (
                "Idea Brief was marked proposed for product-safe review only; "
                "no task, assignment, dispatch, decision acceptance, merge, or "
                "deploy action was created."
            ),
            **idea_brief_contract_metadata(),
        }

    return _idea_brief_error(f"Unknown Idea Brief command: {cmd}", "unknown_command")


INITIATIVE_COMMAND_NAMES = frozenset({
    "initiative_list",
    "initiative_show",
    "initiative_create",
    "initiative_update",
    "initiative_archive",
    "initiative_link_task",
    "initiative_unlink_task",
    "initiative_link_decision",
    "initiative_unlink_decision",
})
AREA_COMMAND_NAMES = frozenset({
    "area_list",
    "area_show",
    "area_create",
    "area_update",
    "area_archive",
    "area_link_task",
    "area_unlink_task",
    "area_link_decision",
    "area_unlink_decision",
    "area_link_initiative",
    "area_unlink_initiative",
    "area_link_area",
    "area_unlink_area",
    "area_note_create",
    "area_note_update",
    "area_note_archive",
})
SCRATCHPAD_COMMAND_NAMES = frozenset({
    "scratchpad_note_list",
    "scratchpad_note_show",
    "scratchpad_note_create",
    "scratchpad_note_update",
    "scratchpad_note_archive",
    "scratchpad_note_delete",
})
MIND_MAP_COMMAND_NAMES = frozenset({
    "mind_map_list",
    "mind_map_show",
    "mind_map_create",
    "mind_map_update",
    "mind_map_archive",
    "mind_map_delete",
    "mind_map_node_create",
    "mind_map_node_update",
    "mind_map_node_position",
    "mind_map_node_reorder",
    "mind_map_node_delete",
    "mind_map_link_create",
    "mind_map_link_update",
    "mind_map_link_reorder",
    "mind_map_link_delete",
})
IDEA_BRIEF_COMMAND_NAMES = frozenset({
    "idea_brief_list",
    "idea_brief_show",
    "idea_brief_create",
    "idea_brief_update",
    "idea_brief_refine",
    "idea_brief_park",
    "idea_brief_archive",
    "idea_brief_propose",
    "idea_brief_promote",
})
PLANNING_COMMAND_NAMES = frozenset().union(
    INITIATIVE_COMMAND_NAMES,
    AREA_COMMAND_NAMES,
    SCRATCHPAD_COMMAND_NAMES,
    MIND_MAP_COMMAND_NAMES,
    IDEA_BRIEF_COMMAND_NAMES,
)

_PLANNING_COMMAND_REGISTRY = AsyncHandlerRegistry()
_PLANNING_COMMAND_REGISTRY.register_many(
    INITIATIVE_COMMAND_NAMES,
    _handle_initiative_command,
    label="initiatives",
)
_PLANNING_COMMAND_REGISTRY.register_many(
    AREA_COMMAND_NAMES,
    _handle_area_command,
    label="areas",
)
_PLANNING_COMMAND_REGISTRY.register_many(
    SCRATCHPAD_COMMAND_NAMES,
    _handle_scratchpad_command,
    label="scratchpad",
)
_PLANNING_COMMAND_REGISTRY.register_many(
    MIND_MAP_COMMAND_NAMES,
    _handle_mind_map_command,
    label="mind_maps",
)
_PLANNING_COMMAND_REGISTRY.register_many(
    IDEA_BRIEF_COMMAND_NAMES,
    _handle_idea_brief_command,
    label="idea_briefs",
)
