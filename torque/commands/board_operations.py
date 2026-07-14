"""Board synchronization, mutation, verification, external-ticket, and artifact commands."""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


BOARD_OPERATION_COMMAND_NAMES = frozenset({
    "board_sync_preflight", "board_sync_list_projects", "board_sync_task",
    "board_sync_group", "board_pull_preview", "board_pull_apply",
    "board_import_preview", "board_pull_import_preview", "board_add_task",
    "board_archive_task", "board_archive_tasks", "board_unarchive_task",
    "board_update_task", "board_mark_task_covered",
    "board_pickup_architect_task",
    "architect_proposal_root_backlog_hygiene", "board_verify_task",
    "workflow_breach", "external_import_task", "external_link_task",
    "external_open_task", "external_push_task_status",
    "external_post_task_comment", "board_remove_task", "remove_attachment",
    "task_upload_artifact", "board_move_task", "board_reorder_task",
})


@dataclass(slots=True)
class BoardOperationRuntime:
    ATTACHMENTS_DIR: Any
    ExternalTicketError: Any
    apply_verification_report: Any
    capture_auto_resume_targets: Any
    emit_task_artifact_uploaded_event: Any
    engineer_tombstoned_error: Any
    finalize_already_covered_proposal_roots: Any
    handle_board_archive_command: Any
    handle_board_archive_tasks_command: Any
    handle_board_unarchive_command: Any
    handle_workflow_breach_command: Any
    maybe_auto_resume_targets: Any
    panel_event: Any
    record_task_completion_evidence_snapshot: Any
    resolve_base_dir: Any
    resolve_deliverable_for_create: Any
    resolve_task_id: Any
    task_upload_actor_source: Any
    task_upload_engineer_scope_error: Any
    board_sync_manager: Any
    build_completion_comment: Any
    finalize_task_attachments: Any
    handle_command: Any
    import_external_ticket: Any
    is_canonical_task_id: Any
    is_draft_task_token: Any
    normalize_artifacts: Any
    normalize_external_link: Any
    open_ticket_url: Any
    post_ticket_comment: Any
    push_ticket_status: Any
    remove_task_owned_artifacts_by_filename: Any
    serialize_task_artifact: Any
    state: Any
    store_task_upload: Any
    task_counts_as_done: Any
    task_is_closed: Any


async def handle_board_operation_command(
    data: dict, runtime: BoardOperationRuntime,
) -> dict | None:
    cmd = str(data.get("cmd", "") or "").strip()
    result = None
    ATTACHMENTS_DIR = runtime.ATTACHMENTS_DIR
    ExternalTicketError = runtime.ExternalTicketError
    _apply_verification_report = runtime.apply_verification_report
    _capture_auto_resume_targets = runtime.capture_auto_resume_targets
    _emit_task_artifact_uploaded_event = runtime.emit_task_artifact_uploaded_event
    _engineer_tombstoned_error = runtime.engineer_tombstoned_error
    _finalize_already_covered_proposal_roots = runtime.finalize_already_covered_proposal_roots
    _handle_board_archive_command = runtime.handle_board_archive_command
    _handle_board_archive_tasks_command = runtime.handle_board_archive_tasks_command
    _handle_board_unarchive_command = runtime.handle_board_unarchive_command
    _handle_workflow_breach_command = runtime.handle_workflow_breach_command
    _maybe_auto_resume_targets = runtime.maybe_auto_resume_targets
    _panel_event = runtime.panel_event
    _record_task_completion_evidence_snapshot = runtime.record_task_completion_evidence_snapshot
    _resolve_base_dir = runtime.resolve_base_dir
    _resolve_deliverable_for_create = runtime.resolve_deliverable_for_create
    _resolve_task_id = runtime.resolve_task_id
    _task_upload_actor_source = runtime.task_upload_actor_source
    _task_upload_engineer_scope_error = runtime.task_upload_engineer_scope_error
    board_sync_manager = runtime.board_sync_manager
    build_completion_comment = runtime.build_completion_comment
    finalize_task_attachments = runtime.finalize_task_attachments
    handle_command = runtime.handle_command
    import_external_ticket = runtime.import_external_ticket
    is_canonical_task_id = runtime.is_canonical_task_id
    is_draft_task_token = runtime.is_draft_task_token
    normalize_artifacts = runtime.normalize_artifacts
    normalize_external_link = runtime.normalize_external_link
    open_ticket_url = runtime.open_ticket_url
    post_ticket_comment = runtime.post_ticket_comment
    push_ticket_status = runtime.push_ticket_status
    remove_task_owned_artifacts_by_filename = runtime.remove_task_owned_artifacts_by_filename
    serialize_task_artifact = runtime.serialize_task_artifact
    state = runtime.state
    store_task_upload = runtime.store_task_upload
    task_counts_as_done = runtime.task_counts_as_done
    task_is_closed = runtime.task_is_closed

    if cmd == "board_sync_preflight":
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            result = await board_sync_manager.preflight(
                data.get("group", ""),
                provider_name=data.get("provider", ""),
                settings_overrides=(
                    data.get("settings")
                    or data.get("group_settings")
                    or {}
                ),
            )

    elif cmd == "board_sync_list_projects":
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            result = await board_sync_manager.list_projects(
                data.get("group", ""),
                owner=data.get("owner", ""),
                provider_name=data.get("provider", ""),
                settings_overrides=(
                    data.get("settings")
                    or data.get("group_settings")
                    or {}
                ),
            )

    elif cmd == "board_sync_task":
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            sync_result = board_sync_manager.enqueue_task(
                data.get("task", data.get("id", "")),
                reason="explicit",
                explicit=True,
                force=True,
            )
            result = {
                "type": "board_sync_task",
                **sync_result,
            }

    elif cmd == "board_sync_group":
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            result = board_sync_manager.enqueue_group(
                data.get("group", ""),
                explicit=True,
                force=bool(data.get("force", False)),
                reason="group_sync",
            )

    elif cmd == "board_pull_preview":
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            result = await board_sync_manager.pull_preview(
                data.get("task", data.get("id", "")))

    elif cmd == "board_pull_apply":
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            result = await board_sync_manager.pull_apply(
                data.get("task", data.get("id", "")),
                data.get("fields", []),
            )

    elif cmd in ("board_import_preview", "board_pull_import_preview"):
        if not board_sync_manager:
            result = {"type": "error", "message": "Board sync manager unavailable"}
        else:
            result = await board_sync_manager.import_preview(
                data.get("group", ""))

    # -- Board commands (Phase 5) --
    elif cmd == "board_add_task":
        # Apply per-group board defaults for fields not
        # explicitly provided by the client
        group = data.get("group", "")
        gs = state.get_group_settings(group)
        lane = data.get("lane", "") or gs.board_default_lane
        action_name = data.get("action_name", "") or \
            gs.board_default_action
        labels = data.get("labels", [])
        if not labels and gs.board_default_labels:
            labels = list(gs.board_default_labels)
        ext_link = normalize_external_link(
            data.get("provider", ""),
            data.get("external_id", ""),
            data.get("external_url", ""),
        )
        add_kwargs = dict(
            task=data.get("task", ""),
            group=group,
            lane=lane,
            description=data.get("description", ""),
            action_name=action_name,
            action_vars=data.get("action_vars", {}),
            agent_template=data.get("agent_template", ""),
            agent_id=data.get("agent_id", ""),
            labels=labels,
            provider=ext_link["provider"],
            external_id=ext_link["external_id"],
            external_url=ext_link["external_url"],
            depends_on=data.get("depends_on", []),
            scheduled_at=data.get("scheduled_at", ""),
            dispatch_state=data.get("dispatch_state", ""),
            assigned_engineer_id=data.get("assigned_engineer_id", ""),
            assigned_architect_id=data.get("assigned_architect_id", ""),
            created_by_engineer_id=data.get("created_by_engineer_id", ""),
            suggested_specialization=data.get(
                "suggested_specialization", ""),
            verification_mode=data.get("verification_mode", ""),
            verification_state=data.get("verification_state", ""),
            verification_notes=data.get("verification_notes", ""),
            verification_updated_at=data.get(
                "verification_updated_at", ""),
            verification_updated_by=data.get(
                "verification_updated_by", ""),
            verification_summary=data.get(
                "verification_summary", {}),
            completion_evidence=data.get(
                "completion_evidence", {}),
        )
        assigned_cell = state.agents.get(
            str(add_kwargs.get("assigned_engineer_id", "") or "").strip()
        )
        if assigned_cell and state.agent_is_tombstoned(assigned_cell):
            result = _engineer_tombstoned_error(assigned_cell.id)
        else:
            # Resolve deliverable contract from action + explicit kwarg
            deliverable_explicit = data.get("deliverable")
            if (action_name or isinstance(deliverable_explicit, dict)
                    and deliverable_explicit):
                deliverable_base_dir = await _resolve_base_dir(group)
                deliverable_contract = _resolve_deliverable_for_create(
                    action_name,
                    deliverable_base_dir,
                    deliverable_explicit
                    if isinstance(deliverable_explicit, dict) else None,
                )
                add_kwargs["deliverable_required"] = bool(
                    deliverable_contract["required"])
                add_kwargs["deliverable_type"] = deliverable_contract["type"]
                add_kwargs["deliverable_format"] = (
                    deliverable_contract["format"])
                add_kwargs["deliverable_artifact_title"] = (
                    deliverable_contract["artifact_title"])
            # Pass client-provided ID (for pre-uploaded attachments)
            draft_upload_id = ""
            incoming_id = str(data.get("id", "") or "").strip()
            if incoming_id:
                if is_draft_task_token(incoming_id) or (
                    not is_canonical_task_id(incoming_id)
                ):
                    draft_upload_id = incoming_id
                else:
                    add_kwargs["id"] = incoming_id
            # Attachments from client (already uploaded to disk)
            if data.get("attachments"):
                add_kwargs["attachments"] = data["attachments"]
            if data.get("artifacts"):
                add_kwargs["artifacts"] = data["artifacts"]
            bt = state.board_add_task(**add_kwargs)
            if not bt:
                result = {"type": "error",
                          "message": "Invalid lane, group, or empty task"}
            else:
                if draft_upload_id:
                    attachments, artifacts = finalize_task_attachments(
                        bt.attachments,
                        bt.artifacts,
                        draft_task_id=draft_upload_id,
                        task_id=bt.id,
                    )
                    state.board_update_task(
                        bt.id,
                        attachments=attachments,
                        artifacts=artifacts,
                    )
                    bt = state.board_tasks.get(bt.id, bt)
                result = {
                    "type": "external_imported" if bt.external_id
                    or bt.external_url else "board_task_added",
                    "task_id": bt.id,
                    "title": bt.task,
                }
                if board_sync_manager:
                    board_sync_manager.enqueue_task(
                        bt.id,
                        reason="task_create",
                    )

    elif cmd == "board_archive_task":
        result = _handle_board_archive_command(state, data)
        if not (isinstance(result, dict)
                and result.get("type") == "error"):
            if board_sync_manager:
                board_sync_manager.enqueue_task(
                    data.get("id", ""),
                    reason="task_archive",
                )

    elif cmd == "board_archive_tasks":
        result = _handle_board_archive_tasks_command(state, data)
        if not (isinstance(result, dict)
                and result.get("type") == "error"):
            for _sync_tid in data.get("ids", data.get("task_ids", [])):
                if board_sync_manager:
                    board_sync_manager.enqueue_task(
                        _sync_tid,
                        reason="task_archive",
                    )

    elif cmd == "board_unarchive_task":
        result = _handle_board_unarchive_command(state, data)
        if not (isinstance(result, dict)
                and result.get("type") == "error"):
            if board_sync_manager:
                board_sync_manager.enqueue_task(
                    data.get("id", ""),
                    reason="task_unarchive",
                )

    elif cmd == "board_update_task":
        tid = _resolve_task_id(state, data.get("id", ""))
        _update_task = state.board_tasks.get(tid)
        _update_resume_targets = _capture_auto_resume_targets(
            state,
            task=_update_task,
            group=_update_task.group if _update_task else "",
        )
        fields = {k: v for k, v in data.items()
                  if k not in ("cmd", "id")}
        if {"provider", "external_id", "external_url"} & set(fields):
            link = normalize_external_link(
                fields.get("provider", ""),
                fields.get("external_id", ""),
                fields.get("external_url", ""),
            )
            fields["provider"] = link["provider"]
            fields["external_id"] = link["external_id"]
            fields["external_url"] = link["external_url"]
        assigned_update = str(
            fields.get("assigned_engineer_id", "") or ""
        ).strip()
        assigned_cell = (
            state.agents.get(assigned_update)
            if "assigned_engineer_id" in fields and assigned_update
            else None
        )
        agent_update = str(fields.get("agent_id", "") or "").strip()
        agent_cell = (
            state.agents.get(agent_update)
            if "agent_id" in fields and agent_update else None
        )
        if assigned_cell and state.agent_is_tombstoned(assigned_cell):
            result = _engineer_tombstoned_error(assigned_cell.id)
        elif agent_cell and state.agent_is_tombstoned(agent_cell):
            result = {
                "type": "error",
                "message": "Agent is tombstoned",
            }
        else:
            state.board_update_task(tid, **fields)
            if board_sync_manager:
                board_sync_manager.enqueue_for_local_change(
                    tid,
                    reason="task_update",
                    fields=fields.keys(),
                )
            # Auto-dispatch if agent_id was set and agent is idle
            _new_aid = fields.get("agent_id", "")
            if _new_aid:
                _tsk = state.board_tasks.get(tid)
                _cell = state.agents.get(_new_aid)
                if (_tsk and _cell
                        and _tsk.lane == "To Do"
                        and not state.agent_is_busy(_new_aid)
                        and _cell.cell_type == "agent"
                        and state.board_deps_met(_tsk)):
                    await handle_command({
                        "cmd": "dispatch_task",
                        "id": tid, "agent_id": _new_aid})
            await _maybe_auto_resume_targets(
                state,
                handle_command,
                _panel_event,
                targets=_update_resume_targets,
                group=_update_task.group if _update_task else "",
            )

    elif cmd == "board_mark_task_covered":
        tid = _resolve_task_id(state, data.get("id", ""))
        _covered_task = state.board_tasks.get(tid)
        if not _covered_task:
            result = {"type": "error", "message": "Task not found"}
        else:
            _covered_resume_targets = _capture_auto_resume_targets(
                state,
                task=_covered_task,
                group=_covered_task.group,
            )
            _covering_task_id = _resolve_task_id(
                state,
                data.get("covering_task_id", "")
                or data.get("covering_task", "")
            )
            try:
                result = state.board_mark_task_covered(
                    tid,
                    covering_task_id=_covering_task_id,
                    pr_url=data.get("pr_url", ""),
                    sha=data.get("sha", ""),
                    tests_run=data.get("tests_run", ""),
                    evidence=data.get("evidence", ""),
                    notes=data.get("notes", ""),
                    actor_name=data.get("actor_name", "") or "Torque",
                    actor_id=data.get("actor_id", ""),
                    actor_kind=data.get("actor_kind", ""),
                    authorization=data.get("authorization", {}),
                    move_to_done=bool(data.get("move_to_done", False)),
                )
            except ValueError as exc:
                result = {"type": "error", "message": str(exc)}
            else:
                if board_sync_manager:
                    board_sync_manager.enqueue_for_local_change(
                        tid,
                        reason="task_covered",
                        fields=(
                            "completion_evidence",
                            "messages",
                            "lane",
                        ) if result.get("moved_to_done") else (
                            "completion_evidence",
                            "messages",
                        ),
                    )
                _panel_event(
                    "task_covered",
                    data.get("actor_id", ""),
                    data.get("actor_name", "") or "Torque",
                    _covered_task.group,
                    result.get("message", ""),
                    task_id=tid,
                )
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=_covered_resume_targets,
                    group=_covered_task.group,
                )

    elif cmd == "board_pickup_architect_task":
        tid = _resolve_task_id(state, data.get("id", ""))
        _pickup_task = state.board_tasks.get(tid)
        if not _pickup_task:
            result = {"type": "error", "message": "Task not found"}
        else:
            _pickup_resume_targets = _capture_auto_resume_targets(
                state,
                task=_pickup_task,
                group=_pickup_task.group,
            )
            try:
                result = state.board_pickup_architect_task(
                    tid,
                    architect_id=data.get("architect_id", ""),
                    actor_name=data.get("actor_name", "") or "Torque",
                    actor_kind=data.get("actor_kind", "") or "architect",
                    reason=data.get("reason", ""),
                    source=data.get("source", ""),
                    authorization=data.get("authorization", {}),
                )
            except ValueError as exc:
                result = {"type": "error", "message": str(exc)}
            else:
                if board_sync_manager:
                    board_sync_manager.enqueue_for_local_change(
                        tid,
                        reason="task_architect_pickup",
                        fields=(
                            "assigned_architect_id",
                            "completion_evidence",
                            "messages",
                        ),
                    )
                _panel_event(
                    "task_architect_pickup",
                    data.get("architect_id", ""),
                    data.get("actor_name", "") or "Torque",
                    _pickup_task.group,
                    "Architect picked up task",
                    task_id=tid,
                )
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=_pickup_resume_targets,
                    group=_pickup_task.group,
                )

    elif cmd == "architect_proposal_root_backlog_hygiene":
        architect_id = str(data.get("architect_id", "") or "").strip()
        architect = state.agents.get(architect_id)
        architect_group = str(
            getattr(architect, "group", "") or ""
        ).strip()
        if (
                not architect
                or str(getattr(architect, "kind", "") or "").strip()
                != "architect"
                or not architect_group):
            result = {
                "type": "error",
                "message": "architect not found",
            }
        elif state.agent_is_tombstoned(architect):
            result = {
                "type": "error",
                "message": "architect is tombstoned",
            }
        else:
            raw_task_ids = data.get("task_ids", []) or []
            if isinstance(raw_task_ids, str):
                raw_task_ids = [
                    part.strip()
                    for part in raw_task_ids.split(",")
                    if part.strip()
                ]
            task_ids = [
                _resolve_task_id(state, task_id)
                for task_id in raw_task_ids
                if str(task_id or "").strip()
            ]
            try:
                limit = int(data.get("limit", 0) or 0)
            except (TypeError, ValueError):
                limit = 0
            result = _finalize_already_covered_proposal_roots(
                state,
                apply=bool(data.get("apply", False)),
                task_ids=task_ids,
                limit=limit,
                board_sync_manager=board_sync_manager,
                architect_id=architect_id,
                group=architect_group,
            )

    elif cmd == "board_verify_task":
        tid = _resolve_task_id(state, data.get("id", ""))
        task = state.board_tasks.get(tid)
        if not task:
            result = {"type": "error", "message": "Task not found"}
        else:
            resume_targets = _capture_auto_resume_targets(
                state,
                task=task,
                group=task.group,
            )
            actor_name = str(
                data.get("actor_name", "") or "torque"
            ).strip()

            def _save_verified_task(current_task):
                current_task.updated_at = datetime.now(
                    timezone.utc
                ).isoformat()
                state._emit("task_upsert", **asdict(current_task))
                state._db_save_task(current_task)

            root_task = None
            root_id = task.pipeline_root_id or ""
            if root_id and root_id != task.id:
                root_task = state.board_tasks.get(root_id)
            payload = {}
            for key in (
                "verification_mode",
                "verification_state",
                "verification_notes",
                "tests_run",
                "manual_smoke_done",
                "deploy_needed",
                "deploy_attempted",
                "human_validation_pending",
                "test_outcome",
                "full_suite_attempted",
                "unrelated_flake_accepted",
                "isolated_rerun_evidence",
                "reviewer_acceptance",
                "live_smoke_pending",
                "smoke_status",
            ):
                if key in data:
                    payload[key] = data[key]
            verify_msg, _updated_root = _apply_verification_report(
                task,
                payload,
                actor_name,
                _save_verified_task,
                root_task=root_task,
            )
            if task_counts_as_done(task):
                _record_task_completion_evidence_snapshot(
                    state,
                    task,
                    action="verify",
                    message=verify_msg,
                    actor_name=actor_name,
                    board_sync_manager=board_sync_manager,
                )
            if (
                    _updated_root
                    and task_counts_as_done(_updated_root)):
                _record_task_completion_evidence_snapshot(
                    state,
                    _updated_root,
                    action="verify",
                    message=verify_msg,
                    actor_name=actor_name,
                    board_sync_manager=board_sync_manager,
                )
            _panel_event(
                "task_verification_updated",
                "",
                actor_name,
                task.group,
                verify_msg,
                task_id=task.id,
            )
            state.recompute_task_health()
            result = {
                "type": "verification_updated",
                "task_id": task.id,
                "message": verify_msg,
            }
            await _maybe_auto_resume_targets(
                state,
                handle_command,
                _panel_event,
                targets=resume_targets,
                group=task.group,
            )

    elif cmd == "workflow_breach":
        result = _handle_workflow_breach_command(
            data,
            state,
            _panel_event,
        )

    elif cmd == "external_import_task":
        group = data.get("group", "")
        lane = data.get("lane", "") or "Backlog"
        labels = data.get("labels", [])
        try:
            # `import_external_ticket` may shell out to the `gh`
            # CLI for GitHub tickets (sync subprocess.run). Offload
            # it to a thread so the event loop keeps serving the UI.
            imported = await asyncio.to_thread(
                import_external_ticket,
                data.get("ref", ""),
                provider=data.get("provider", ""),
                title=data.get("title", ""),
                description=data.get("description", ""),
                external_id=data.get("external_id", ""),
                external_url=data.get("external_url", ""),
            )
            bt = state.board_add_task(
                task=imported.title,
                group=group,
                lane=lane,
                description=imported.description,
                labels=labels,
                provider=imported.provider,
                external_id=imported.external_id,
                external_url=imported.external_url,
            )
            if not bt:
                result = {"type": "error",
                          "message": "Invalid group, lane, or task"}
            else:
                result = {
                    "type": "external_imported",
                    "task_id": bt.id,
                    "title": bt.task,
                    "provider": bt.provider,
                    "external_id": bt.external_id,
                    "external_url": bt.external_url,
                }
                if board_sync_manager:
                    board_sync_manager.enqueue_task(
                        bt.id,
                        reason="external_import",
                    )
        except ExternalTicketError as exc:
            result = {"type": "error", "message": str(exc)}

    elif cmd == "external_link_task":
        tid = _resolve_task_id(state, data.get("id", ""))
        task = state.board_tasks.get(tid)
        if not task:
            result = {"type": "error",
                      "message": "Task not found"}
        else:
            link = normalize_external_link(
                data.get("provider", ""),
                data.get("external_id", ""),
                data.get("external_url", ""),
                ref=data.get("ref", ""),
            )
            update_fields = {
                "provider": link["provider"],
                "external_id": link["external_id"],
                "external_url": link["external_url"],
            }
            board_sync = data.get("board_sync", None)
            if isinstance(board_sync, dict):
                update_fields["board_sync"] = board_sync
            elif (
                    not link["provider"]
                    and not link["external_id"]
                    and not link["external_url"]
            ):
                update_fields["board_sync"] = {
                    "version": 1,
                    "enabled": False,
                }
            state.board_update_task(tid, **update_fields)
            if board_sync_manager:
                board_sync_manager.enqueue_task(
                    tid,
                    reason="external_link",
                )
            result = {
                "type": "external_unlinked"
                if not link["provider"]
                and not link["external_id"]
                and not link["external_url"]
                else "external_linked",
                "task_id": tid,
                **link,
            }

    elif cmd == "external_open_task":
        tid = _resolve_task_id(state, data.get("id", ""))
        task = state.board_tasks.get(tid)
        if not task:
            result = {"type": "error",
                      "message": "Task not found"}
        else:
            try:
                url = open_ticket_url(
                    task.provider,
                    task.external_id,
                    task.external_url,
                )
                result = {
                    "type": "external_open",
                    "task_id": tid,
                    "url": url,
                }
            except ExternalTicketError as exc:
                result = {"type": "error", "message": str(exc)}

    elif cmd == "external_push_task_status":
        tid = _resolve_task_id(state, data.get("id", ""))
        task = state.board_tasks.get(tid)
        if not task:
            result = {"type": "error",
                      "message": "Task not found"}
        else:
            try:
                pushed = push_ticket_status(
                    task,
                    status=data.get("status", "") or task.status
                    or task.lane,
                    note=data.get("note", ""),
                )
                task.messages.append({
                    "timestamp": time.time(),
                    "action": "external_status",
                    "message": pushed,
                    "agent_name": "torque",
                })
                state.board_update_task(tid, messages=task.messages)
                result = {
                    "type": "external_status_pushed",
                    "task_id": tid,
                    "message": pushed,
                }
            except ExternalTicketError as exc:
                result = {"type": "error", "message": str(exc)}

    elif cmd == "external_post_task_comment":
        tid = _resolve_task_id(state, data.get("id", ""))
        task = state.board_tasks.get(tid)
        if not task:
            result = {"type": "error",
                      "message": "Task not found"}
        else:
            try:
                comment = (data.get("comment", "") or "").strip()
                if not comment:
                    comment = build_completion_comment(
                        task.task,
                        data.get("summary", ""),
                    )
                posted = post_ticket_comment(task, comment=comment)
                task.messages.append({
                    "timestamp": time.time(),
                    "action": "external_comment",
                    "message": posted,
                    "agent_name": "torque",
                })
                state.board_update_task(tid, messages=task.messages)
                result = {
                    "type": "external_comment_posted",
                    "task_id": tid,
                    "message": posted,
                }
            except ExternalTicketError as exc:
                result = {"type": "error", "message": str(exc)}

    elif cmd == "board_remove_task":
        tid = _resolve_task_id(state, data.get("id", ""))
        state.board_remove_task(tid)
        # Clean up attachment files
        att_dir = ATTACHMENTS_DIR / tid
        if att_dir.is_dir():
            shutil.rmtree(att_dir, ignore_errors=True)

    elif cmd == "board_archive_task":
        result = _handle_board_archive_command(state, data)
        if not (isinstance(result, dict)
                and result.get("type") == "error"):
            if board_sync_manager:
                board_sync_manager.enqueue_task(
                    data.get("id", ""),
                    reason="task_archive",
                )

    elif cmd == "board_archive_tasks":
        result = _handle_board_archive_tasks_command(state, data)
        if not (isinstance(result, dict)
                and result.get("type") == "error"):
            for _sync_tid in data.get("ids", data.get("task_ids", [])):
                if board_sync_manager:
                    board_sync_manager.enqueue_task(
                        _sync_tid,
                        reason="task_archive",
                    )

    elif cmd == "board_unarchive_task":
        result = _handle_board_unarchive_command(state, data)
        if not (isinstance(result, dict)
                and result.get("type") == "error"):
            if board_sync_manager:
                board_sync_manager.enqueue_task(
                    data.get("id", ""),
                    reason="task_unarchive",
                )

    elif cmd == "remove_attachment":
        tid = _resolve_task_id(state, data.get("task_id", ""))
        fname = data.get("filename", "")
        task = state.board_tasks.get(tid)
        if fname:
            fpath = ATTACHMENTS_DIR / tid / fname
            if fpath.is_file():
                fpath.unlink()
        if task and fname:
            task.attachments = [
                a for a in task.attachments
                if a.get("filename") != fname]
            task.artifacts = remove_task_owned_artifacts_by_filename(
                task.artifacts,
                fname,
                task_id=tid,
            )
            state.board_update_task(
                tid,
                attachments=task.attachments,
                artifacts=task.artifacts,
            )

    elif cmd == "task_upload_artifact":
        tid = _resolve_task_id(state, data.get("task_id", ""))
        cell_id = data.get("cell_id", "")
        if not tid and cell_id:
            current_task = state.agent_current_task(cell_id)
            if current_task:
                tid = current_task.id
        task = state.board_tasks.get(tid)
        if not task:
            result = {
                "type": "error",
                "message": (
                    "Task not found"
                    if data.get("task_id")
                    else "No active task available for this agent"
                ),
            }
        else:
            actor = state.agents.get(cell_id) if cell_id else None
            result = _task_upload_engineer_scope_error(
                state, actor, task)
            provenance = {
                "source": _task_upload_actor_source(state, actor, task),
                "agent_id": actor.id if actor else "",
                "agent_name": (actor.slug or actor.name) if actor else "",
            }
            if not result:
                try:
                    artifact = store_task_upload(
                        task_id=tid,
                        local_path=data.get("local_path", ""),
                        filename=data.get("filename", ""),
                        content_base64=data.get("content_base64", ""),
                        content_text=data.get("content_text", ""),
                        artifact_type=data.get("artifact_type", ""),
                        title=data.get("title", ""),
                        mime_type=data.get("mime_type", ""),
                        summary=data.get("summary", ""),
                        prompt_mode=data.get("prompt_mode", ""),
                        provenance=provenance,
                    )
                except FileNotFoundError as exc:
                    result = {"type": "error", "message": str(exc)}
                except ValueError as exc:
                    result = {"type": "error", "message": str(exc)}
                else:
                    artifacts = normalize_artifacts(task.artifacts or [])
                    artifacts.append(artifact)
                    state.board_update_task(tid, artifacts=artifacts)
                    refreshed = state.board_tasks.get(tid)
                    serialized_artifact = serialize_task_artifact(
                        artifact,
                        task_id=tid,
                        task_label=(
                            refreshed.task if refreshed else task.task
                        ),
                    )
                    _emit_task_artifact_uploaded_event(
                        _panel_event,
                        refreshed or task,
                        actor,
                        serialized_artifact,
                    )
                    result = {
                        "type": "task_artifact_uploaded",
                        "task_id": tid,
                        "artifact": serialized_artifact,
                    }

    elif cmd == "board_move_task":
        _mv_id = _resolve_task_id(state, data.get("id", ""))
        _mv_task = state.board_tasks.get(_mv_id)
        if not _mv_task:
            result = {"type": "error", "message": "Task not found"}
        else:
            _mv_resume_targets = _capture_auto_resume_targets(
                state,
                task=_mv_task,
                group=_mv_task.group if _mv_task else "",
            )
            _mv_done_before = task_counts_as_done(_mv_task)
            _mv_previous_lane = str(getattr(_mv_task, "lane", "") or "")
            _mv_new = data.get("lane", "")
            if not _mv_new:
                result = {"type": "error", "message": "lane is required"}
            elif _mv_new not in state.board_lanes:
                result = {
                    "type": "error",
                    "message": f"Unknown lane: {_mv_new}",
                }
            else:
                _mv_clear_status = data.get("clear_status", False)
                if not isinstance(_mv_clear_status, bool):
                    _mv_clear_status = False
                state.board_move_task(
                    _mv_id,
                    _mv_new,
                    data.get("position"),
                    clear_status=_mv_clear_status,
                )
                if board_sync_manager:
                    board_sync_manager.enqueue_task(
                        _mv_id,
                        reason="task_move",
                    )
                _mv_task_after = state.board_tasks.get(_mv_id)
                result = {
                    "type": "task_moved",
                    "task_id": _mv_id,
                    "previous_lane": _mv_previous_lane,
                    "new_lane": (
                        str(getattr(_mv_task_after, "lane", "") or "")
                        if _mv_task_after else _mv_new
                    ),
                    "status": (
                        str(getattr(_mv_task_after, "status", "") or "")
                        if _mv_task_after else ""
                    ),
                }
                # Moving out of Done may re-block dependents
                if _mv_done_before and not task_counts_as_done(_mv_task_after):
                    for _dt in state.board_get_dependents(_mv_id):
                        if not task_is_closed(_dt):
                            _panel_event(
                                "task_blocked_by_dep", "",
                                "", _dt.group,
                                f"Task '{_dt.task[:60]}' is "
                                "blocked again (dependency "
                                "moved out of Done)",
                                task_id=_dt.id)
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=_mv_resume_targets,
                    group=_mv_task_after.group if _mv_task_after else "",
                )

    elif cmd == "board_reorder_task":
        state.board_reorder_task(
            data.get("id", ""),
            data.get("position", 0))


    return result
