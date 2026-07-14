"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403

async def dispatch_planning(ctx: ScopedDispatchContext):
    name = ctx.name
    args = ctx.args
    handle_command = ctx.handle_command
    state = ctx.state
    real_state = ctx.real_state
    tool_prefix = ctx.tool_prefix
    caller_kind = ctx.caller_kind
    caller_id = ctx.caller_id
    idempotency_key = ctx.idempotency_key
    _engineer_cell = ctx.caller_cell
    _engineer_group = ctx.caller_group
    tool_name = normalize_tool_name(name, tool_prefix)

    if tool_name == "initiative_create" and caller_kind == "architect":
        title = str(args.get("title", "") or "").strip()
        if not title:
            return "title is required", True
        status = str(args.get("planning_status", "triage") or "triage").strip()
        if status not in {"triage", "now", "next", "later", "parked", "shipped"}:
            return "planning_status must be one of: triage, now, next, later, parked, shipped", True
        try:
            created = await real_state.create_initiative_async({
                "group": _engineer_group,
                "title": title,
                "summary": args.get("summary", ""),
                "why": args.get("why", ""),
                "in_scope": args.get("in_scope", ""),
                "out_of_scope": args.get("out_of_scope", ""),
                "done_definition": args.get("done_definition", ""),
                "planning_status": status,
                "priority": args.get("priority", ""),
                "owner_kind": "architect",
                "owner_id": caller_id,
                "created_by_kind": "architect",
                "created_by_id": caller_id,
                "updated_by_kind": "architect",
                "updated_by_id": caller_id,
            })
        except ValueError as exc:
            return str(exc), True
        return _compact_json({"type": "initiative_created", "initiative": created}), False

    if tool_name in {
        "initiative_update", "initiative_archive", "initiative_link_task",
        "initiative_unlink_task", "initiative_link_decision",
        "initiative_unlink_decision",
    } and caller_kind == "architect":
        initiative, error = _initiative_from_args(real_state, caller_id, args)
        if not initiative:
            return error, True
        if not _architect_can_write_initiative(initiative, caller_id):
            return "Initiative not found", True

        if tool_name == "initiative_update":
            allowed = {
                "title", "summary", "why", "in_scope", "out_of_scope",
                "done_definition", "planning_status", "priority", "owner_kind",
                "owner_id", "slug",
            }
            patch = {key: args[key] for key in allowed if key in args}
            if "owner_kind" in patch or "owner_id" in patch:
                owner_kind = str(patch.get("owner_kind", initiative.get("owner_kind", "")) or "").strip()
                owner_id = str(patch.get("owner_id", initiative.get("owner_id", "")) or "").strip()
                if owner_kind != "architect" or owner_id != str(caller_id or "").strip():
                    return "Architect MCP can only keep initiatives owned by the caller architect", True
            patch["updated_by_kind"] = "architect"
            patch["updated_by_id"] = caller_id
            try:
                updated = await real_state.update_initiative_async(initiative["id"], patch)
            except ValueError as exc:
                return str(exc), True
            return _compact_json({"type": "initiative_updated", "initiative": updated}), False

        if tool_name == "initiative_archive":
            archived = await real_state.archive_initiative_async(
                initiative["id"], archived_by_kind="architect", archived_by_id=caller_id,
            )
            return _compact_json({"type": "initiative_archived", "initiative": archived}), False

        if tool_name in {"initiative_link_task", "initiative_unlink_task"}:
            task_id, task_error = _initiative_task_link_target(
                real_state, caller_kind, caller_id,
                args.get("task", args.get("task_id", "")),
            )
            if not task_id:
                return task_error, True
            task = real_state.board_tasks.get(task_id)
            if str(getattr(task, "group", "") or "").strip() != str(initiative.get("group_name", "") or "").strip():
                return "Task is outside initiative group", True
            if tool_name == "initiative_link_task":
                link = await real_state.save_initiative_link_async(
                    initiative["id"], "task", task_id,
                    created_by_kind="architect", created_by_id=caller_id,
                )
                return _compact_json({"type": "initiative_task_linked", "link": link}), False
            removed = await real_state.delete_initiative_link_async(
                initiative["id"], "task", task_id,
            )
            return _compact_json({"type": "initiative_task_unlinked", "removed": removed}), False

        decision_id, decision_error = _initiative_decision_link_target(
            real_state,
            caller_id,
            args.get("decision", args.get("decision_id", "")),
        )
        if not decision_id:
            return decision_error, True
        if tool_name == "initiative_link_decision":
            link = await real_state.save_initiative_link_async(
                initiative["id"], "decision", decision_id,
                created_by_kind="architect", created_by_id=caller_id,
            )
            return _compact_json({"type": "initiative_decision_linked", "link": link}), False
        removed = await real_state.delete_initiative_link_async(
            initiative["id"], "decision", decision_id,
        )
        return _compact_json({"type": "initiative_decision_unlinked", "removed": removed}), False


    if tool_name == "area_create" and caller_kind == "architect":
        title = str(args.get("title", "") or "").strip()
        if not title:
            return "title is required", True
        lifecycle = str(args.get("lifecycle", "planned") or "planned").strip()
        if lifecycle not in {"planned", "experimental", "active_investment", "stable", "maintenance", "deprecated", "retired"}:
            return "lifecycle must be one of: planned, experimental, active_investment, stable, maintenance, deprecated, retired", True
        try:
            created = await real_state.create_area_async({
                "group": _engineer_group,
                "title": title,
                "area_type": args.get("area_type", ""),
                "lifecycle": lifecycle,
                "summary": args.get("summary", ""),
                "user_purpose": args.get("user_purpose", ""),
                "system_purpose": args.get("system_purpose", ""),
                "in_scope": args.get("in_scope", ""),
                "out_of_scope": args.get("out_of_scope", ""),
                "owner_kind": "architect",
                "owner_id": caller_id,
                "created_by_kind": "architect",
                "created_by_id": caller_id,
                "updated_by_kind": "architect",
                "updated_by_id": caller_id,
            })
        except ValueError as exc:
            return str(exc), True
        return _compact_json({"type": "area_created", "area": created}), False

    if tool_name in {
        "area_update", "area_archive", "area_link_task", "area_unlink_task",
        "area_link_decision", "area_unlink_decision", "area_link_initiative",
        "area_unlink_initiative", "area_link_area", "area_unlink_area",
        "area_note_create", "area_note_update", "area_note_archive",
    } and caller_kind == "architect":
        area, error = _area_from_args(real_state, caller_id, args)
        if not area:
            return error, True
        if not _architect_can_write_area(area, caller_id):
            return "Area not found", True
        area_group = str(area.get("group_name", "") or "").strip()

        if tool_name == "area_update":
            allowed = {
                "title", "area_type", "lifecycle", "summary", "user_purpose",
                "system_purpose", "in_scope", "out_of_scope", "owner_kind",
                "owner_id", "slug",
            }
            patch = {key: args[key] for key in allowed if key in args}
            if "owner_kind" in patch or "owner_id" in patch:
                owner_kind = str(patch.get("owner_kind", area.get("owner_kind", "")) or "").strip()
                owner_id = str(patch.get("owner_id", area.get("owner_id", "")) or "").strip()
                if owner_kind != "architect" or owner_id != str(caller_id or "").strip():
                    return "Architect MCP can only keep areas owned by the caller architect", True
            patch["updated_by_kind"] = "architect"
            patch["updated_by_id"] = caller_id
            try:
                updated = await real_state.update_area_async(area["id"], patch)
            except ValueError as exc:
                return str(exc), True
            return _compact_json({"type": "area_updated", "area": updated}), False

        if tool_name == "area_archive":
            archived = await real_state.archive_area_async(
                area["id"], archived_by_kind="architect", archived_by_id=caller_id,
            )
            return _compact_json({"type": "area_archived", "area": archived}), False

        if tool_name in {"area_link_task", "area_unlink_task"}:
            target_id, target_error = _area_task_link_target(
                real_state, caller_kind, caller_id,
                args.get("task", args.get("task_id", args.get("target_id", ""))),
            )
            if not target_id:
                return target_error, True
            task = real_state.board_tasks.get(target_id)
            if str(getattr(task, "group", "") or "").strip() != area_group:
                return "Task is outside area group", True
            if tool_name == "area_link_task":
                link = await real_state.save_area_link_async(
                    area["id"], "task", target_id,
                    created_by_kind="architect", created_by_id=caller_id,
                )
                return _compact_json({"type": "area_linked", "link": link}), False
            removed = await real_state.delete_area_link_async(area["id"], "task", target_id)
            return _compact_json({"type": "area_unlinked", "removed": removed}), False

        if tool_name in {"area_link_decision", "area_unlink_decision"}:
            target_id, target_error = _area_decision_link_target(
                real_state, caller_id,
                args.get("decision", args.get("decision_id", args.get("target_id", ""))),
            )
            if not target_id:
                return target_error, True
            if tool_name == "area_link_decision":
                link = await real_state.save_area_link_async(
                    area["id"], "decision", target_id,
                    created_by_kind="architect", created_by_id=caller_id,
                )
                return _compact_json({"type": "area_linked", "link": link}), False
            removed = await real_state.delete_area_link_async(area["id"], "decision", target_id)
            return _compact_json({"type": "area_unlinked", "removed": removed}), False

        if tool_name in {"area_link_initiative", "area_unlink_initiative"}:
            target_id, target_error = _area_initiative_link_target(
                real_state, caller_id,
                args.get("initiative", args.get("initiative_id", args.get("target_id", ""))),
            )
            if not target_id:
                return target_error, True
            if tool_name == "area_link_initiative":
                link = await real_state.save_area_link_async(
                    area["id"], "initiative", target_id,
                    created_by_kind="architect", created_by_id=caller_id,
                )
                return _compact_json({"type": "area_linked", "link": link}), False
            removed = await real_state.delete_area_link_async(area["id"], "initiative", target_id)
            return _compact_json({"type": "area_unlinked", "removed": removed}), False

        if tool_name in {"area_link_area", "area_unlink_area"}:
            target_id, target_error = _area_area_link_target(
                real_state, caller_id, area["id"],
                args.get("target_area", args.get("target_area_id", args.get("target_id", ""))),
            )
            if not target_id:
                return target_error, True
            relation = str(args.get("relation", "") or "").strip().lower()
            if tool_name == "area_link_area":
                try:
                    link = await real_state.save_area_link_async(
                        area["id"], "area", target_id, relation=relation,
                        created_by_kind="architect", created_by_id=caller_id,
                    )
                except ValueError as exc:
                    return str(exc), True
                return _compact_json({"type": "area_linked", "link": link}), False
            try:
                removed = await real_state.delete_area_link_async(
                    area["id"], "area", target_id, relation,
                )
            except ValueError as exc:
                return str(exc), True
            return _compact_json({"type": "area_unlinked", "removed": removed}), False

        if tool_name == "area_note_create":
            target_type = str(args.get("target_type", "") or "").strip().lower()
            target_id = str(args.get("target_id", "") or "").strip()
            target_error = _area_note_target_error(
                real_state, caller_kind, caller_id, area_group, target_type, target_id,
            )
            if target_error:
                return target_error, True
            try:
                note = await real_state.create_area_note_async(area["id"], {
                    "note_type": args.get("note_type", args.get("type", "")),
                    "title": args.get("title", ""),
                    "body": args.get("body", ""),
                    "target_type": target_type,
                    "target_id": target_id,
                    "created_by_kind": "architect",
                    "created_by_id": caller_id,
                    "updated_by_kind": "architect",
                    "updated_by_id": caller_id,
                })
            except ValueError as exc:
                return str(exc), True
            return _compact_json({"type": "area_note_created", "note": note}), False

        note_id = args.get("note", args.get("note_id", ""))
        note = real_state.load_area_note(note_id)
        if not note or str(note.get("area_id", "") or "") != str(area["id"]):
            return "Area note not found", True
        if tool_name == "area_note_archive":
            archived = await real_state.archive_area_note_async(
                note_id, archived_by_kind="architect", archived_by_id=caller_id,
            )
            return _compact_json({"type": "area_note_archived", "note": archived}), False
        allowed = {"note_type", "title", "body", "target_type", "target_id"}
        patch = {key: args[key] for key in allowed if key in args}
        target_type = str(patch.get("target_type", note.get("target_type", "")) or "").strip().lower()
        target_id = str(patch.get("target_id", note.get("target_id", "")) or "").strip()
        target_error = _area_note_target_error(
            real_state, caller_kind, caller_id, area_group, target_type, target_id,
        )
        if target_error:
            return target_error, True
        patch["updated_by_kind"] = "architect"
        patch["updated_by_id"] = caller_id
        try:
            updated = await real_state.update_area_note_async(note_id, patch)
        except ValueError as exc:
            return str(exc), True
        return _compact_json({"type": "area_note_updated", "note": updated}), False

    if tool_name == "decision_create" and caller_kind == "architect":
        title = str(args.get("title", "") or "").strip()
        rationale = str(args.get("rationale", "") or "").strip()
        if not title:
            return "title is required", True
        if not rationale:
            return "rationale is required", True
        status = str(args.get("status", "") or "proposed").strip() or "proposed"
        if status not in _DECISION_STATUSES:
            return (
                "status must be one of: proposed, accepted, revised, rejected",
                True,
            )
        supersedes = str(args.get("supersedes", "") or "").strip() or None
        if supersedes:
            prior_decision, decision_error = _load_architect_decision(
                real_state, caller_id, supersedes
            )
            if not prior_decision:
                return decision_error, True
        linked_task_ids, linked_engineer_ids, link_error = _normalize_decision_links(
            real_state,
            caller_id,
            task_ids=args.get("linked_task_ids", []),
            engineer_ids=args.get("linked_engineer_ids", []),
        )
        if link_error:
            return link_error, True
        decision = await real_state.save_decision_async({
            "id": "decision-" + uuid.uuid4().hex[:12],
            "architect_id": str(caller_id or "").strip(),
            "title": title,
            "rationale": rationale,
            "status": status,
            "supersedes": supersedes,
            "linked_task_ids": linked_task_ids,
            "linked_engineer_ids": linked_engineer_ids,
            "archived": False,
        })
        if not decision:
            return "Failed to save decision", True
        return json.dumps({
            "id": decision["id"],
            "created_at": decision["created_at"],
        }), False

    if tool_name == "decision_update" and caller_kind == "architect":
        decision, decision_error = _load_architect_decision(
            real_state, caller_id, args.get("id", "")
        )
        if not decision:
            return decision_error, True
        patch = {"id": decision["id"]}
        if "title" in args:
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            patch["title"] = title
        if "rationale" in args:
            rationale = str(args.get("rationale", "") or "").strip()
            if not rationale:
                return "rationale is required", True
            patch["rationale"] = rationale
        if "status" in args:
            status = str(args.get("status", "") or "").strip()
            if status not in _DECISION_STATUSES:
                return (
                    "status must be one of: proposed, accepted, revised, rejected",
                    True,
                )
            patch["status"] = status
        if "supersedes" in args:
            supersedes = str(args.get("supersedes", "") or "").strip() or None
            if supersedes:
                prior_decision, supersedes_error = _load_architect_decision(
                    real_state, caller_id, supersedes
                )
                if not prior_decision:
                    return supersedes_error, True
            patch["supersedes"] = supersedes
        if "linked_task_ids" in args or "linked_engineer_ids" in args:
            linked_task_ids, linked_engineer_ids, link_error = _normalize_decision_links(
                real_state,
                caller_id,
                task_ids=args.get(
                    "linked_task_ids",
                    decision.get("linked_task_ids", []),
                ),
                engineer_ids=args.get(
                    "linked_engineer_ids",
                    decision.get("linked_engineer_ids", []),
                ),
            )
            if link_error:
                return link_error, True
            if "linked_task_ids" in args:
                patch["linked_task_ids"] = linked_task_ids
            if "linked_engineer_ids" in args:
                patch["linked_engineer_ids"] = linked_engineer_ids
        if "archived" in args:
            patch["archived"] = bool(args.get("archived"))

        updated = await real_state.save_decision_async(patch)
        if not updated:
            return "Failed to save decision", True
        return json.dumps(updated), False

    if tool_name == "decision_link" and caller_kind == "architect":
        decision, decision_error = _load_architect_decision(
            real_state, caller_id, args.get("id", "")
        )
        if not decision:
            return decision_error, True
        task_id = str(args.get("task_id", "") or "").strip()
        engineer_id = str(args.get("engineer_id", "") or "").strip()
        if bool(task_id) == bool(engineer_id):
            return "Provide exactly one of task_id or engineer_id", True

        patch = {"id": decision["id"]}
        if task_id:
            linked_task_ids, _linked_engineer_ids, link_error = _normalize_decision_links(
                real_state,
                caller_id,
                task_ids=list(decision.get("linked_task_ids", [])) + [task_id],
                engineer_ids=[],
            )
            if link_error:
                return link_error, True
            patch["linked_task_ids"] = linked_task_ids
        else:
            _linked_task_ids, linked_engineer_ids, link_error = _normalize_decision_links(
                real_state,
                caller_id,
                task_ids=[],
                engineer_ids=list(decision.get("linked_engineer_ids", [])) + [engineer_id],
            )
            if link_error:
                return link_error, True
            patch["linked_engineer_ids"] = linked_engineer_ids

        updated = await real_state.save_decision_async(patch)
        if not updated:
            return "Failed to save decision", True
        return json.dumps(updated), False

    if tool_name == "decision_list" and caller_kind == "architect":
        status_filter = str(args.get("status_filter", "") or "").strip()
        if status_filter and status_filter not in _DECISION_STATUSES:
            return (
                "status_filter must be one of: proposed, accepted, revised, rejected",
                True,
            )
        decisions = real_state.load_decisions_for_architect(
            caller_id,
            include_archived=bool(args.get("include_archived", False)),
        )
        if status_filter:
            decisions = [
                decision for decision in decisions
                if str(decision.get("status", "") or "").strip() == status_filter
            ]
        return json.dumps({"decisions": decisions}), False

    if tool_name == "journal":
        if caller_kind == "architect":
            entry_type = str(args.get("type", "") or "").strip()
            if entry_type not in _ARCHITECT_AUTHORED_JOURNAL_ENTRY_TYPES:
                return (
                    "type must be one of: decision, observation, "
                    "checkpoint, plan",
                    True,
                )
            entry = str(args.get("entry", "") or "")
            if not entry:
                return "entry is required", True
            if not idempotency_key:
                return json.dumps(real_state.architect_journal_append(
                    caller_id,
                    entry_type,
                    entry,
                )), False
            result = await handle_command({
                "cmd": "architect_journal_append",
                "architect_id": caller_id,
                "entry_type": entry_type,
                "entry": entry,
            })
            if result and result.get("type") == "error":
                return result.get("message", "Unknown error"), True
            return json.dumps(result), False
        result = await handle_command({
            "cmd": "engineer_journal_append",
            "group": _engineer_group,
            "entry_type": args.get("type", "observation"),
            "entry": args.get("entry", ""),
            "author_cell_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result), False

    if tool_name == "journal_read":
        if caller_kind == "architect":
            return json.dumps({
                "type": "journal",
                "entries": real_state.architect_journal_read(
                    caller_id,
                    since=args.get("since", 0),
                    limit=args.get("limit", 20),
                ),
            }), False
        scope = str(args.get("scope", "") or "").strip().lower()
        include_cross_author = bool(args.get("include_cross_author", False))
        if scope and scope not in {"self", "group"}:
            return "scope must be one of: self, group", True
        author_cell_id = ""
        if caller_kind == "engineer" and scope != "group" \
                and not include_cross_author:
            author_cell_id = str(caller_id or "").strip()
        entries = real_state.journal_read(
            _engineer_group,
            args.get("tail", 20),
            args.get("type", ""),
            author_cell_id=author_cell_id,
        )
        return json.dumps({"type": "journal", "entries": entries}), False

    if tool_name == "engineer_journal_read" and caller_kind == "architect":
        engineer_ident = str(args.get("engineer_id", "") or "").strip()
        if not engineer_ident:
            return "engineer_id is required", True
        engineer_id, engineer_error = _resolve_architect_hired_engineer(
            real_state, caller_id, engineer_ident
        )
        if not engineer_id:
            return engineer_error, True
        type_filter = str(args.get("type_filter", "") or "").strip()
        if type_filter and type_filter not in _JOURNAL_ENTRY_TYPES:
            return (
                "type_filter must be one of: decision, observation, "
                "checkpoint, plan, note_dismissed, qa",
                True,
            )
        try:
            since_value = float(args.get("since", 0) or 0)
        except (TypeError, ValueError):
            since_value = 0.0
        try:
            limit_value = int(args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit_value = 20
        if limit_value <= 0:
            return json.dumps({"type": "journal", "entries": []}), False
        limit_value = min(limit_value, 100)
        engineer = real_state.agents.get(engineer_id)
        engineer_group = str(getattr(engineer, "group", "") or "").strip()
        if not engineer_group:
            return json.dumps({"type": "journal", "entries": []}), False
        entries = real_state.journal_read(
            engineer_group,
            limit_value,
            type_filter,
            author_cell_id=engineer_id,
        )
        if since_value:
            filtered = []
            for entry in entries:
                try:
                    timestamp = float((entry or {}).get("timestamp", 0) or 0)
                except (TypeError, ValueError):
                    timestamp = 0.0
                if timestamp > since_value:
                    filtered.append(entry)
            entries = filtered
        return json.dumps({"type": "journal", "entries": entries}), False

    # -- Interaction tools --------------------------------------------------

    return UNHANDLED
