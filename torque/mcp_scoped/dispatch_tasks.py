"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

import copy
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

from torque.commands.task_dispatch import ACTION_BINDING_REQUIRED_FOR_DISPATCH
from torque.agent_classes import (
    has_frozen_platform_group_board_authority,
    has_frozen_platform_task_authority_mode,
)
from torque.config import log
from torque.finalization import normalize_required_review_gates
from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403
from torque.task_dispatch_gate import active_dispatch_edit_error, task_has_active_dispatch
from torque.server_board_sync import (
    task_description_sync_constraint,
    task_description_sync_limit_status,
    task_description_sync_validation_error,
    task_labels_sync_validation_error,
    task_title_sync_validation_error,
)
from torque.task_amendment import (
    build_task_amendment_block,
    find_task_amendment,
    task_amendment_advisory,
    validate_task_amendment,
)
from torque.task_content import compute_task_content_hash
from torque.worktree_boundaries import task_boundary


def _live_workers_linked_to_task(state, task) -> list:
    """Return active worker cells directly linked to *task*.

    ``BoardTask.agent_id`` is the durable task-to-worker relation.  Do not
    infer ownership from lanes, health, or branch names: those are unrelated
    state transitions and must never transfer a worker.
    """
    agent_id = str(getattr(task, "agent_id", "") or "").strip()
    if not agent_id:
        return []
    cell = state.agents.get(agent_id)
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return []
    if str(getattr(cell, "kind", "") or "").strip() != "worker":
        return []
    if state.agent_is_tombstoned(cell):
        return []
    return [cell]


def _orphaned_task_worker_count(state) -> int:
    """Count reportable task/worker ownership mismatches without changing them."""
    count = 0
    for task in state.board_tasks.values():
        assigned_engineer_id = _effective_assigned_engineer_id(task)
        for worker in _live_workers_linked_to_task(state, task):
            owner_engineer_id = str(
                getattr(worker, "owner_engineer_id", "") or ""
            ).strip()
            if owner_engineer_id != assigned_engineer_id:
                count += 1
    return count


def _review_cycle_continue_verdict(review_task) -> tuple[dict, str]:
    evidence = getattr(review_task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}, ""
    review = evidence.get("review", {}) or {}
    if not isinstance(review, dict):
        return {}, ""
    verdict = str(review.get("verdict", "") or "").strip().lower()
    return copy.deepcopy(review), verdict


async def _review_cycle_continue(ctx: ScopedDispatchContext):
    """Create one audited continuation without changing the prior verdict."""
    args = ctx.args
    state = ctx.real_state
    caller_id = str(ctx.caller_id or "").strip()
    task_id = state.resolve_board_task_id(str(args.get("task", "") or ""))
    review_task = state.board_tasks.get(task_id) if task_id else None
    if not review_task:
        return "Task not found", True
    if str(getattr(review_task, "group", "") or "") != ctx.caller_group:
        return "Task not found", True
    if str(getattr(review_task, "action_name", "") or "").strip().lower() \
            != "feature/review":
        return "review_cycle_continue requires a feature/review task", True
    if not board_task_is_closed(review_task):
        return "review_cycle_continue requires a completed feature/review", True
    if _effective_assigned_engineer_id(review_task) != caller_id:
        return (
            "Authorization denied: the review stream is not owned by this "
            "Engineer.",
            True,
        )
    reason = str(args.get("reason", "") or "").strip()
    if not reason:
        return "reason is required", True

    predecessor_boundary = copy.deepcopy(task_boundary(review_task))
    if str(predecessor_boundary.get("status", "") or "").lower() != "open":
        return "The completed review does not have an open boundary", True
    reviewed_sha = str(predecessor_boundary.get("commit_sha", "") or "").strip()
    repo_root = str(predecessor_boundary.get("repo_root", "") or "").strip()
    branch = str(predecessor_boundary.get("branch", "") or "").strip()
    if not reviewed_sha or not repo_root or not branch:
        return "The completed review boundary is incomplete", True

    parent_id = str(getattr(review_task, "parent_task_id", "") or "").strip()
    implementation = state.board_tasks.get(parent_id) if parent_id else None
    if not implementation or str(
            getattr(implementation, "action_name", "") or ""
    ).strip().lower() != "feature/implement":
        return (
            "The completed review has no direct implementation parent",
            True,
        )
    root_id = str(
        getattr(review_task, "pipeline_root_id", "")
        or getattr(implementation, "pipeline_root_id", "")
        or implementation.id
    ).strip()
    implementation_root = str(
        getattr(implementation, "pipeline_root_id", "") or implementation.id
    ).strip()
    if not root_id or root_id != implementation_root:
        return "The review and implementation do not share one root", True

    implementer_id = str(getattr(implementation, "agent_id", "") or "").strip()
    implementer = state.agents.get(implementer_id) if implementer_id else None
    if not implementer:
        return "The original implementer is unavailable", True
    if str(getattr(implementer, "owner_engineer_id", "") or "").strip() \
            != caller_id:
        return (
            "Authorization denied: the original implementer is not owned by "
            "this Engineer.",
            True,
        )
    implementer_repo = str(
        getattr(implementer, "worktree_repo_root", "")
        or getattr(implementer, "git_root", "")
        or ""
    ).strip()
    if (
            implementer_repo != repo_root
            or str(getattr(implementer, "worktree_branch", "") or "").strip()
            != branch
    ):
        return (
            "The original implementer no longer owns the reviewed branch",
            True,
        )

    review_record, original_verdict = _review_cycle_continue_verdict(review_task)
    if not review_record or not original_verdict:
        return "The completed review has no structured verdict evidence", True
    prior_continuations = (
        getattr(review_task, "completion_evidence", {}) or {}
    ).get("review_cycle_continuations", [])
    if prior_continuations:
        return "This review already has an audited continuation", True

    # Read-only preflight against the real branch. The merge check supplies
    # the classified boundary/head mismatch; the summary supplies staleness.
    merge_check = await ctx.handle_command({
        "cmd": "worktree_check_merge",
        "id": implementer_id,
        "merge_task_id": implementation.id,
        "allow_stale_base": True,
    })
    boundary_summary = (
        merge_check.get("boundary", {})
        if isinstance(merge_check, dict) else {}
    )
    mismatch = (
        boundary_summary.get("boundary_tip_mismatch", {})
        if isinstance(boundary_summary, dict) else {}
    )
    observed_head = str(
        boundary_summary.get("head_sha", "")
        if isinstance(boundary_summary, dict) else ""
    ).strip()
    if (
            str(boundary_summary.get("task_id", "") or "").strip()
            != review_task.id
            or str(mismatch.get("classification", "") or "").lower() != "ahead"
            or not observed_head
            or observed_head == reviewed_sha
    ):
        return (
            "review_cycle_continue requires the branch tip to be verified "
            "ahead of this completed review boundary",
            True,
        )
    diff_summary = await ctx.handle_command({
        "cmd": "worktree_diff",
        "id": implementer_id,
        "summary_only": True,
    })
    stale_base = (
        diff_summary.get("stale_base", {})
        if isinstance(diff_summary, dict) else {}
    )
    if not stale_base.get("stale"):
        return (
            "review_cycle_continue is reserved for the composed stale-base "
            "review-cycle state; derive a fresh review directly instead",
            True,
        )

    now = datetime.now(timezone.utc).isoformat()
    try:
        continuation_id = state._allocate_derived_task_id(
            review_task.group, root_id
        )
    except ValueError:
        return "Failed to allocate the review-cycle continuation", True
    audit = {
        "operation": "review_cycle_continue",
        "actor_id": caller_id,
        "actor_name": str(
            getattr(ctx.caller_cell, "name", "") or "Engineer"
        ).strip(),
        "continued_at": now,
        "reason": reason,
        "original_review_task_id": review_task.id,
        "original_verdict": original_verdict,
        "reviewed_sha": reviewed_sha,
        "observed_branch_head": observed_head,
        "continuation_task_id": continuation_id,
        "pipeline_root_id": root_id,
        "repo_root": repo_root,
        "branch": branch,
    }
    continuation = state.board_add_task(
        task=f"Continue review cycle for {implementation.task}",
        group=review_task.group,
        lane="Backlog",
        id=continuation_id,
        action_name="feature/implement",
        parent_task_id=implementation.id,
        pipeline_root_id=root_id,
        pipeline_depth=max(
            int(getattr(review_task, "pipeline_depth", 0) or 0) + 1,
            int(getattr(implementation, "pipeline_depth", 0) or 0) + 1,
        ),
        agent_id=implementer_id,
        assigned_engineer_id=caller_id,
        dispatch_state="queued",
        labels=["torque:derived", "torque:review-cycle-continuation"],
        completion_evidence={
            "review_cycle_continue": copy.deepcopy(audit),
        },
        description=(
            "Audited continuation of completed review "
            f"{review_task.id}. Non-force rebase, rerun evidence, then derive "
            "a fresh feature/review."
        ),
    )
    if not continuation:
        return "Failed to create the review-cycle continuation", True

    predecessor_boundary["status"] = "superseded"
    predecessor_boundary["superseded_by_task_id"] = continuation.id
    predecessor_boundary["superseded_at"] = now
    predecessor_boundary["superseded_by_operation"] = "review_cycle_continue"
    predecessor_boundary["review_cycle_continuation_task_id"] = continuation.id
    predecessor_boundary.pop("reason", None)
    review_evidence = copy.deepcopy(
        getattr(review_task, "completion_evidence", {}) or {}
    )
    review_evidence["review_cycle_continuations"] = [copy.deepcopy(audit)]
    review_messages = list(getattr(review_task, "messages", []) or [])
    review_messages.append({
        "timestamp": time.time(),
        "action": "review_cycle_continue",
        "message": (
            f"Review cycle continued as {continuation.id}; original verdict "
            f"{original_verdict} at {reviewed_sha[:12]} remains immutable."
        ),
        "agent_name": audit["actor_name"],
    })
    state.board_update_task(
        review_task.id,
        completion_evidence=review_evidence,
        worktree_boundary=predecessor_boundary,
        messages=review_messages,
    )
    return json.dumps({
        "type": "review_cycle_continued",
        "review_task_id": review_task.id,
        "continuation_task_id": continuation.id,
        "implementer_id": implementer_id,
        "audit": audit,
        "original_review": review_record,
        "boundary": predecessor_boundary,
        "next_steps": [
            f"worktree_rebase id={implementer_id}",
            "rerun the relevant evidence",
            "derive a fresh feature/review",
        ],
    }), False


def _required_review_gates_arg(args: dict) -> tuple[list | None, str]:
    """Validate the durable cardinality declaration exposed by task tools."""
    if "required_review_gates" not in args:
        return None, ""
    value = args.get("required_review_gates")
    if not isinstance(value, list):
        return None, "required_review_gates must be a list"
    normalized = normalize_required_review_gates(value)
    if len(normalized) != len(value):
        return None, (
            "required_review_gates entries require unique non-empty id values"
        )
    return normalized, ""

async def dispatch_tasks(ctx: ScopedDispatchContext):
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
    is_creator_proposal_mode = (
        caller_kind == "architect"
        and has_frozen_platform_task_authority_mode(
            _engineer_cell, "creator-proposal-only")
    )
    is_group_board_authority_mode = (
        caller_kind == "architect"
        and has_frozen_platform_group_board_authority(_engineer_cell)
    )

    def group_task_allowed(task) -> bool:
        return bool(
            task
            and str(getattr(task, "group", "") or "").strip()
            == _engineer_group
        )

    def creator_task_allowed(task) -> bool:
        return (
            not is_creator_proposal_mode
            or str(getattr(task, "created_by_architect_id", "") or "").strip()
            == str(caller_id or "").strip()
        )

    if tool_name == "review_cycle_continue":
        if caller_kind != "engineer":
            return "review_cycle_continue is available only to Engineers", True
        return await _review_cycle_continue(ctx)

    if tool_name == "task_create":
        required_review_gates, gates_error = _required_review_gates_arg(args)
        if gates_error:
            return gates_error, True
        if caller_kind == "architect":
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            requested_group = str(args.get("group", "") or "").strip()
            if not requested_group:
                return "group is required", True
            if requested_group != _engineer_group:
                return "group must match the architect's group", True
            assigned_engineer_ident = str(
                args.get("assigned_engineer_id", "") or ""
            ).strip()
            if not assigned_engineer_ident:
                return "assigned_engineer_id is required", True
            assigned_engineer_id, engineer_error = _resolve_architect_hired_engineer(
                real_state, caller_id, assigned_engineer_ident
            )
            if not assigned_engineer_id:
                return engineer_error, True
            assigned_engineer = real_state.agents.get(assigned_engineer_id)
            if _agent_dismissed_at(assigned_engineer):
                return _engineer_dismissed_error(assigned_engineer_id), True
            action_vars = args.get("action_vars", {})
            if action_vars is None:
                action_vars = {}
            if not isinstance(action_vars, dict):
                return "action_vars must be an object", True
            suggested_action = str(args.get("suggested_action", "") or "").strip()
            suggested_action_error = await _validate_suggested_action(
                suggested_action,
                _engineer_group,
                handle_command,
            )
            if suggested_action_error:
                return suggested_action_error, True

            dispatch_requested, dispatch_error = _optional_bool_arg(
                args, "dispatch", False
            )
            if dispatch_error:
                return dispatch_error, True
            dispatch_message = str(args.get("dispatch_message", "") or "").strip()
            dispatch_requested = dispatch_requested or bool(dispatch_message)

            # Architect task creation intentionally accepts only a
            # non-binding ``suggested_action``. Do not infer that hint as
            # the Engineer's eventual selection; an atomic dispatch is only
            # valid when the group's pre-existing default has already bound
            # the created task.
            default_action = ""
            if dispatch_requested:
                group_settings = real_state.get_group_settings(_engineer_group)
                default_action = str(
                    getattr(group_settings, "board_default_action", "") or ""
                ).strip()
                if not default_action:
                    return ACTION_BINDING_REQUIRED_FOR_DISPATCH, True

            suggested_specialization = str(
                args.get("suggested_specialization", "") or ""
            ).strip()
            requested_lane = str(args.get("lane", "") or "").strip()
            if dispatch_requested and not requested_lane:
                live_lane_for_create = getattr(
                    real_state,
                    "_board_live_transition_lane",
                    None,
                )
                if callable(live_lane_for_create):
                    requested_lane = live_lane_for_create(_engineer_group)

            architect_create_payload = {
                "cmd": "board_add_task",
                "task": title,
                "description": args.get("description", ""),
                "group": _engineer_group,
                "lane": requested_lane,
                "labels": args.get("labels", []),
                "assigned_engineer_id": assigned_engineer_id,
            }
            if required_review_gates is not None:
                architect_create_payload["required_review_gates"] = required_review_gates
            architect_deliverable = args.get("deliverable")
            if isinstance(architect_deliverable, dict):
                architect_create_payload["deliverable"] = architect_deliverable
            create_result = await handle_command(architect_create_payload)
            if create_result and create_result.get("type") == "error":
                return create_result.get("message", "Unknown error"), True

            task_id = str((create_result or {}).get("task_id", "") or "").strip()
            if task_id:
                update_result = await handle_command({
                    "cmd": "board_update_task",
                    "id": task_id,
                    "assigned_engineer_id": assigned_engineer_id,
                    "created_by_architect_id": str(caller_id or "").strip(),
                    "suggested_action": suggested_action,
                    "suggested_specialization": suggested_specialization,
                    "action_name": default_action,
                    "action_vars": action_vars,
                })
                if update_result and update_result.get("type") == "error":
                    return update_result.get("message", "Unknown error"), True

            response = dict(create_result) if create_result else {"type": "ok"}
            if suggested_specialization:
                engineer_specs = set(
                    getattr(assigned_engineer, "engineer_specializations", [])
                    or []
                )
                # Empty means a routable generalist, not a missing binding.
                if engineer_specs and suggested_specialization not in engineer_specs:
                    response["suggested_specialization_warning"] = (
                        f"assigned engineer does not carry specialization "
                        f"'{suggested_specialization}'"
                    )
            if task_id:
                created_task = real_state.board_tasks.get(task_id)
                awareness_block = build_engineer_deliverable_awareness(
                    created_task
                )
                if awareness_block:
                    response["deliverable_awareness"] = awareness_block
                if dispatch_requested:
                    dispatch_response, dispatch_send_error = (
                        await _send_architect_engineer_message(
                            real_state,
                            handle_command,
                            caller_id,
                            {
                                "engineer_id": assigned_engineer_id,
                                "message": dispatch_message,
                            },
                            dispatch_task_id=task_id,
                        )
                    )
                    if dispatch_send_error:
                        return dispatch_send_error, True
                    response["dispatch_state"] = "live"
                    response["dispatch"] = dispatch_response
                else:
                    response["dispatch_state"] = str(
                        getattr(created_task, "dispatch_state", "queued")
                        or "queued"
                    )
            return json.dumps(response), False

        payload = {
            "cmd": "board_add_task",
            "task": args.get("title", ""),
            "description": args.get("description", ""),
            "group": _engineer_group,
            "lane": args.get("lane", ""),
            "action_name": args.get("action", ""),
            "action_vars": args.get("action_vars", {}),
            "labels": args.get("labels", []),
            "verification_mode": args.get("verification_mode", ""),
            "verification_state": args.get("verification_state", ""),
            "verification_notes": args.get("verification_notes", ""),
            "verification_summary": args.get("verification_summary", {}),
            "assigned_engineer_id": str(caller_id or "").strip(),
            "created_by_engineer_id": str(caller_id or "").strip(),
        }
        if required_review_gates is not None:
            payload["required_review_gates"] = required_review_gates
        engineer_deliverable = args.get("deliverable")
        if isinstance(engineer_deliverable, dict):
            payload["deliverable"] = engineer_deliverable
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_pickup" and caller_kind == "architect":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = real_state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        caller_id_str = str(caller_id or "").strip()
        authorization, auth_error = _architect_task_pickup_authorization(
            real_state,
            caller_id_str,
            task,
        )
        if auth_error:
            return auth_error, True
        actor_name = (
            str(getattr(_engineer_cell, "slug", "") or "").strip()
            or str(getattr(_engineer_cell, "name", "") or "").strip()
            or "architect"
        )
        reason = str(args.get("reason", "") or "").strip()
        source = str(args.get("source", "") or "").strip()
        if not source:
            if str(authorization.get("scope", "") or "").strip() == (
                    "engineer_created_task_handoff"
            ):
                source = "hired-engineer provenance"
            else:
                route_message_id = str(
                    authorization.get("route_message_id", "") or ""
                ).strip()
                source = (
                    f"product-peer route {route_message_id}"
                    if route_message_id else
                    "product-peer route"
                )
        result = await handle_command({
            "cmd": "board_pickup_architect_task",
            "id": tid,
            "architect_id": caller_id_str,
            "actor_name": actor_name,
            "actor_kind": "architect",
            "reason": reason,
            "source": source,
            "authorization": authorization,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_update" and caller_kind == "architect":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task or not group_task_allowed(task):
            return "Task not found", True
        caller_id_str = str(caller_id or "").strip()
        creator_class = _task_created_by_classifier(task)
        if is_group_board_authority_mode:
            pass
        elif is_creator_proposal_mode:
            if not creator_task_allowed(task):
                return "Authorization denied: task is outside Product Manager creator/self scope", True
        elif creator_class != "user" and not _architect_task_owned_by_caller(
                task,
                caller_id_str,
        ):
            handoff_refusal = _engineer_created_task_handoff_refusal(
                task, caller_id_str,
            )
            return handoff_refusal or "Task was not created by this architect", True

        # ``dispatch_state`` preserves the handoff history after a worker
        # exits. Protect amendments only while that execution stream is
        # actually running; otherwise a stopped-worker repair is safe.
        if task_has_active_dispatch(real_state, task):
            return json.dumps(active_dispatch_edit_error(task)), True

        patch = {}
        updated_fields = []
        if "title" in args:
            title = str(args.get("title", "") or "").strip()
            if not title:
                return "title is required", True
            title_error = task_title_sync_validation_error(real_state, task, title)
            if title_error:
                return title_error, True
            patch["task"] = title
            updated_fields.append("title")
        if "description" in args:
            description = str(args.get("description", "") or "")
            if not description.strip():
                return "description is required", True
            description_error = task_description_sync_validation_error(
                real_state, task, description,
            )
            if description_error:
                return description_error, True
            patch["description"] = description
            updated_fields.append("description")
        if "labels" in args:
            labels = args.get("labels")
            if not isinstance(labels, list):
                return "labels must be a list", True
            normalized_labels = []
            seen_labels = set()
            for item in labels:
                if not isinstance(item, str):
                    return "labels entries must be strings", True
                label = item.strip()
                if not label or label in seen_labels:
                    continue
                normalized_labels.append(label)
                seen_labels.add(label)
            labels_error = task_labels_sync_validation_error(
                real_state, task, normalized_labels,
            )
            if labels_error:
                return labels_error, True
            if (
                    (is_creator_proposal_mode or is_group_board_authority_mode)
                    and {"product-proposal", "proposal-only"}.issubset(
                        set(getattr(task, "labels", []) or [])
                    )
                    and not {"product-proposal", "proposal-only"}.issubset(
                        set(normalized_labels)
                    )):
                return (
                    "Product Manager updates must retain product-proposal and "
                    "proposal-only labels"
                ), True
            patch["labels"] = normalized_labels
            updated_fields.append("labels")
        if "action_name" in args:
            action_name = str(args.get("action_name", "") or "").strip()
            action_error = await _validate_task_update_action_name(
                action_name,
                _engineer_group,
                handle_command,
            )
            if action_error:
                return action_error, True
            patch["action_name"] = action_name
            updated_fields.append("action_name")
        if "action_vars" in args:
            action_vars = args.get("action_vars")
            if action_vars is None:
                action_vars = {}
            if not isinstance(action_vars, dict):
                return "action_vars must be an object", True
            patch["action_vars"] = copy.deepcopy(action_vars)
            updated_fields.append("action_vars")
        required_review_gates, gates_error = _required_review_gates_arg(args)
        if gates_error:
            return gates_error, True
        if required_review_gates is not None:
            patch["required_review_gates"] = required_review_gates
            updated_fields.append("required_review_gates")
        if "suggested_action" in args:
            suggested_action = str(args.get("suggested_action", "") or "").strip()
            suggested_action_error = await _validate_suggested_action(
                suggested_action,
                _engineer_group,
                handle_command,
            )
            if suggested_action_error:
                return suggested_action_error, True
            patch["suggested_action"] = suggested_action
            updated_fields.append("suggested_action")
        if not updated_fields:
            return "At least one editable field is required", True

        update_result = await handle_command({
            "cmd": "board_update_task",
            "id": tid,
            **patch,
        })
        if update_result and update_result.get("type") == "error":
            return update_result.get("message", "Unknown error"), True
        return json.dumps({
            "type": "ok",
            "task_id": tid,
            "updated_fields": updated_fields,
        }), False

    if tool_name == "task_amend" and caller_kind == "architect":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task or not group_task_allowed(task):
            return "Task not found", True
        caller_id_str = str(caller_id or "").strip()
        creator_class = _task_created_by_classifier(task)
        if is_group_board_authority_mode:
            pass
        elif is_creator_proposal_mode:
            if not creator_task_allowed(task):
                return (
                    "Authorization denied: task is outside Product Manager "
                    "creator/self scope"
                ), True
        elif creator_class != "user" and not _architect_task_owned_by_caller(
                task, caller_id_str):
            handoff_refusal = _engineer_created_task_handoff_refusal(
                task, caller_id_str,
            )
            return (
                handoff_refusal or "Task was not created by this architect"
            ), True

        amendment = str(args.get("amendment", "") or "")
        amendment_id = str(
            args.get("amendment_id", "")
            or idempotency_key
            or ""
        )
        amendment_error = validate_task_amendment(amendment, amendment_id)
        if amendment_error:
            return amendment_error, True
        expected_hash = str(
            args.get("expected_task_content_hash", "") or ""
        ).strip()
        if not expected_hash:
            return "expected_task_content_hash is required", True

        # Use one timestamp and one pure formatter for both the provider-size
        # projection and the atomic state append.
        added_at = datetime.now(timezone.utc).isoformat()
        current_hash = compute_task_content_hash(task)
        existing_amendment = find_task_amendment(
            task.description, amendment_id
        )
        provider_limit_status = task_description_sync_limit_status(
            real_state, task,
        )
        if not existing_amendment:
            projected_description = (
                task.description
                + build_task_amendment_block(
                    amendment=amendment,
                    amendment_id=amendment_id,
                    actor_id=caller_id_str,
                    prior_task_content_hash=current_hash,
                    added_at=added_at,
                )
            )
            description_error = task_description_sync_validation_error(
                real_state, task, projected_description,
            )
            if description_error:
                constraint = task_description_sync_constraint(
                    real_state, task,
                )
                if not constraint:
                    return (
                        "Unable to determine the provider's task-description "
                        "limit locally; task was not amended."
                    ), True
                return json.dumps({
                    "type": "error",
                    "reason": "provider_description_limit",
                    "task_id": tid,
                    "provider": constraint["provider"],
                    "current_size": len(task.description),
                    "projected_size": len(projected_description),
                    "limit": constraint["limit"],
                    "message": description_error,
                }), True

        amend_result = await handle_command({
            "cmd": "board_amend_task",
            "id": tid,
            "amendment": amendment,
            "amendment_id": amendment_id,
            "actor_id": caller_id_str,
            "expected_task_content_hash": expected_hash,
            "added_at": added_at,
        })
        if amend_result.get("type") == "error":
            return json.dumps(amend_result), True
        if (
                provider_limit_status["active"]
                and not provider_limit_status["verifiable"]):
            amend_result["provider_body_limit"] = {
                "provider": provider_limit_status["provider"],
                "verifiable": False,
                "limit": None,
                "message": (
                    "No provider body limit was verifiable; Torque did not "
                    "invent a limit or probe the provider."
                ),
            }
        if not amend_result.get("deduped"):
            executor = None
            executor_id = str(
                getattr(task, "agent_id", "") or ""
            ).strip()
            candidate = next(
                iter(_live_workers_linked_to_task(real_state, task)),
                None,
            )
            if (
                    candidate
                    and candidate.id == executor_id
                    and str(
                        getattr(candidate, "status", "") or ""
                    ).strip().lower() == "running"
                    and real_state.task_occupies_execution_slot(
                        task, agent_id=executor_id)):
                executor = candidate
            if executor:
                # This system advisory is intentionally not a dispatch prompt
                # rebuild and its constructor cannot accept authored text.
                await handle_command({
                    "cmd": "inject_mcp_message",
                    "agent_id": executor.id,
                    "message": task_amendment_advisory(
                        tid, amend_result["task_content_hash"]
                    ),
                    "sender_name": "Torque",
                    "sender_kind": "system",
                })
        return json.dumps(amend_result), False

    if tool_name == "task_block_reply" and caller_kind == "architect":
        tid = _resolve_task(state, args.get("task", ""))
        task = state.board_tasks.get(tid) if tid else None
        if not task or not group_task_allowed(task):
            return "Task not found", True
        caller_id_str = str(caller_id or "").strip()
        creator_class = _task_created_by_classifier(task)
        if is_group_board_authority_mode:
            pass
        elif is_creator_proposal_mode:
            if not creator_task_allowed(task):
                return "Authorization denied: task is outside Product Manager creator/self scope", True
        elif creator_class != "user" and not _architect_task_owned_by_caller(task, caller_id_str):
            return "Task was not created by this architect", True
        answer = str(args.get("answer", "") or "").strip()
        if not answer:
            return "answer is required", True
        # Stable across transport retries: duplicate invokes return the first
        # persisted delivery result and never send a second provider turn.
        material = "|".join((caller_id_str, tid, answer, str(idempotency_key or "")))
        reply_id = "msg-block-" + hashlib.sha256(material.encode()).hexdigest()[:12]
        result = await handle_command({
            "cmd": "blocked_task_reply", "task_id": tid,
            "actor_id": caller_id_str, "answer": answer, "reply_id": reply_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result or {"type": "error", "message": "Reply failed"}), False

    if tool_name == "task_reassign":
        task_state = state
        if caller_kind == "engineer":
            task_state = real_state
        tid = _resolve_task(task_state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = task_state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        if board_task_is_closed(task):
            return "Task is already closed", True
        engineer_ident = str(args.get("new_engineer_id", "") or "").strip()
        if not engineer_ident:
            return "new_engineer_id is required", True
        caller_id_str = str(caller_id or "").strip()
        old_engineer_id = _effective_assigned_engineer_id(task)

        if caller_kind == "architect":
            if is_group_board_authority_mode:
                if not group_task_allowed(task):
                    return "Authorization denied: task is outside Product Manager group scope", True
                engineer_id, engineer_error = _resolve_group_engineer(
                    real_state, caller_id, engineer_ident
                )
            else:
                if is_creator_proposal_mode and not creator_task_allowed(task):
                    return "Authorization denied: task is outside Product Manager creator/self scope", True
                if (
                        not is_creator_proposal_mode
                        and not _architect_task_owned_by_caller(task, caller_id_str)
                ):
                    return "Task was not created by this architect", True
                if is_creator_proposal_mode:
                    engineer_id, engineer_error = _resolve_group_engineer(
                        real_state, caller_id, engineer_ident
                    )
                else:
                    engineer_id, engineer_error = _resolve_architect_hired_engineer(
                        real_state, caller_id, engineer_ident
                    )
        else:
            caller_group = _caller_group(real_state, caller_id_str)
            if str(getattr(task, "group", "") or "").strip() != caller_group:
                return "Task not found", True
            created_by_engineer_id = str(
                getattr(task, "created_by_engineer_id", "") or ""
            ).strip()
            if (
                old_engineer_id != caller_id_str
                and created_by_engineer_id != caller_id_str
            ):
                return (
                    "Task can only be reassigned by the assigned engineer "
                    "or creator"
                ), True
            engineer_id, engineer_error = _resolve_group_engineer(
                real_state, caller_id, engineer_ident
            )
        if not engineer_id:
            return engineer_error, True
        engineer = real_state.agents.get(engineer_id)
        if _agent_dismissed_at(engineer):
            return _engineer_dismissed_error(engineer_id), True
        workers = _live_workers_linked_to_task(real_state, task)
        prospective_task = copy.deepcopy(task)
        prospective_task.assigned_engineer_id = engineer_id
        prospective_task.updated_at = datetime.now(timezone.utc).isoformat()
        prospective_workers = []
        for worker in workers:
            prospective_worker = copy.deepcopy(worker)
            prospective_worker.owner_engineer_id = engineer_id
            prospective_workers.append(prospective_worker)

        # Commit both sides before making the new projection visible.  This
        # keeps SQLite readers from ever observing the task reassigned without
        # the linked live worker(s) following it.  No worker is inferred from
        # lanes, health, or branch names; only this explicit reassignment can
        # trigger the transfer.
        if real_state.db:
            try:
                await real_state.flush_db_writes()
                await real_state.db.save_task_and_agents_async(
                    prospective_task,
                    prospective_workers,
                )
            except Exception:
                log.exception(
                    "Failed to persist task/worker ownership transfer for %s",
                    tid,
                )
                return "Failed to persist task and worker ownership transfer", True

        for worker in workers:
            worker.owner_engineer_id = engineer_id
            real_state._emit_agent(worker)
        task.assigned_engineer_id = engineer_id
        task.updated_at = prospective_task.updated_at
        real_state.emit_task_upsert(task)
        real_state.recompute_task_health()
        worker_transfer = {
            "status": "transferred" if workers else "not_applicable",
            "transferred_count": len(workers),
            "worker_ids": [worker.id for worker in workers],
        }
        if not workers:
            worker_transfer["warning"] = (
                "No live workers are linked to this task; no worker "
                "ownership changed."
            )
        remaining_orphans = _orphaned_task_worker_count(real_state)
        if caller_kind == "architect":
            return json.dumps({
                "type": "ok",
                "task_id": tid,
                "assigned_engineer_id": engineer_id,
                "worker_transfer": worker_transfer,
                "orphaned_task_worker_count": remaining_orphans,
            }), False
        return json.dumps({
            "type": "ok",
            "task_id": tid,
            "from": old_engineer_id,
            "to": engineer_id,
            "worker_transfer": worker_transfer,
            "orphaned_task_worker_count": remaining_orphans,
        }), False

    if tool_name == "task_coverage_reconcile":
        if caller_kind not in {"architect", "engineer"}:
            return (
                "Authorization denied: task_coverage_reconcile is not "
                "available to this caller.",
                True,
            )
        # This intentionally precedes all task resolution and command/state
        # work. TORQUE:1228 replaces it with the semantic implementation.
        return json.dumps({
            "type": "tool_unavailable",
            "tool": "task_coverage_reconcile",
            "status": "recognized_but_not_yet_available",
            "activation_task": "TORQUE:1228",
            "activation_conditions": [
                "TORQUE:1228 is merged",
                "the caller session is relaunched",
            ],
            "message": (
                "This tool exists but is NOT YET AVAILABLE. It activates only "
                "after TORQUE:1228 is merged and this caller session is "
                "relaunched; tool projection and authority are frozen for the "
                "session."
            ),
        }, separators=(",", ":")), True

    if tool_name == "proposal_root_backlog_hygiene" and caller_kind == "architect":
        apply, apply_error = _optional_bool_arg(args, "apply", False)
        if apply_error:
            return apply_error, True
        raw_task_ids = args.get("task_ids", []) or []
        if isinstance(raw_task_ids, str):
            raw_task_ids = [
                part.strip()
                for part in raw_task_ids.split(",")
                if part.strip()
            ]
        if not isinstance(raw_task_ids, list):
            return "task_ids must be a list or comma-separated string", True
        task_ids = []
        for raw_task_id in raw_task_ids:
            task_id = _resolve_task(state, str(raw_task_id or "").strip())
            if not task_id:
                return f"Task not found: {raw_task_id}", True
            task_ids.append(task_id)
        try:
            limit = int(args.get("limit", 0) or 0)
        except (TypeError, ValueError):
            return "limit must be an integer", True
        if limit < 0:
            return "limit must be non-negative", True
        result = await handle_command({
            "cmd": "architect_proposal_root_backlog_hygiene",
            "architect_id": str(caller_id or "").strip(),
            "apply": apply,
            "task_ids": task_ids,
            "limit": limit,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name in {"task_edit", "task_update"}:
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        required_review_gates, gates_error = _required_review_gates_arg(args)
        if gates_error:
            return gates_error, True
        if required_review_gates is not None:
            task = real_state.board_tasks.get(tid)
            if task_has_active_dispatch(real_state, task):
                return json.dumps(active_dispatch_edit_error(task)), True
        payload = {"cmd": "board_update_task", "id": tid}
        if "title" in args:
            title = str(args["title"] or "").strip()
            if not title:
                return "title is required", True
            task = real_state.board_tasks.get(tid)
            title_error = task_title_sync_validation_error(real_state, task, title)
            if title_error:
                return title_error, True
            payload["task"] = title
        if "description" in args:
            description = str(args["description"] or "")
            task = real_state.board_tasks.get(tid)
            description_error = task_description_sync_validation_error(
                real_state, task, description,
            )
            if description_error:
                return description_error, True
            payload["description"] = args["description"]
        if "labels" in args:
            labels = args["labels"]
            if isinstance(labels, list):
                task = real_state.board_tasks.get(tid)
                labels_error = task_labels_sync_validation_error(
                    real_state, task, labels,
                )
                if labels_error:
                    return labels_error, True
            payload["labels"] = args["labels"]
        if "action" in args:
            payload["action_name"] = args["action"]
        if "action_vars" in args:
            payload["action_vars"] = args["action_vars"]
        if required_review_gates is not None:
            payload["required_review_gates"] = required_review_gates
        for key in (
            "verification_mode",
            "verification_state",
            "verification_notes",
            "verification_summary",
        ):
            if key in args:
                payload[key] = args[key]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_upload_artifact":
        task_ident = args.get("task", "") or args.get("task_id", "")
        tid = _resolve_task(real_state, task_ident)
        if not tid:
            return "Task not found", True
        task = real_state.board_tasks.get(tid)
        if is_creator_proposal_mode and not creator_task_allowed(task):
            return "Authorization denied: task is outside Product Manager creator/self scope", True
        if (
                caller_kind == "engineer"
                and not real_state.engineer_can_access_task(
                    caller_id,
                    task,
                    allow_created=True,
                    allow_unassigned=False,
                )):
            return "task not found in scope", True
        payload = {"cmd": "task_upload_artifact", "task_id": tid}
        if caller_id:
            payload["cell_id"] = caller_id
        for key in (
            "local_path",
            "filename",
            "content_base64",
            "content_text",
            "artifact_type",
            "title",
            "mime_type",
            "summary",
            "prompt_mode",
        ):
            if key in args:
                payload[key] = args[key]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_mark_covered":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = real_state.board_tasks.get(tid)
        if not task:
            return "Task not found", True

        caller_id_str = str(caller_id or "").strip()
        if is_group_board_authority_mode and not group_task_allowed(task):
            return "Authorization denied: task is outside Product Manager group scope", True
        if (
                is_creator_proposal_mode
                and not is_group_board_authority_mode
                and not creator_task_allowed(task)
        ):
            return "Authorization denied: task is outside Product Manager creator/self scope", True
        if caller_kind == "engineer":
            if not real_state.engineer_can_access_task(
                caller_id_str,
                task,
                allow_created=True,
                allow_unassigned=False,
            ):
                return "task not found in scope", True
        else:
            creator_class = _task_created_by_classifier(task)
            coverage_authorization = {}
            if (
                    not is_group_board_authority_mode
                    and creator_class != "user"
                    and not _architect_task_owned_by_caller(
                        task,
                        caller_id_str,
                    )
            ):
                coverage_authorization, auth_error = (
                    _routed_product_root_coverage_authorization(
                        real_state,
                        caller_id_str,
                        task,
                        _resolve_task(
                            real_state,
                            args.get("covering_task", "")
                            or args.get("covering_task_id", ""),
                        ) or "",
                    )
                )
                if auth_error:
                    return auth_error, True
        if caller_kind == "engineer":
            coverage_authorization = {}

        covering_ident = (
            args.get("covering_task", "")
            or args.get("covering_task_id", "")
        )
        covering_task_id = ""
        if str(covering_ident or "").strip():
            covering_task_id = _resolve_task(state, covering_ident)
            if not covering_task_id:
                return "covering_task not found in scope", True
        if (
                caller_kind == "architect"
                and coverage_authorization
                and not any(
                    str(args.get(key, "") or "").strip()
                    for key in ("pr_url", "sha", "tests_run", "evidence", "notes")
                )):
            return (
                "Routed product proposal root coverage requires PR/SHA/tests "
                "or notes evidence"
            ), True

        move_to_done, move_error = _optional_bool_arg(
            args,
            "move_to_done",
            False,
        )
        if move_error:
            return move_error, True

        actor_name = (
            str(getattr(_engineer_cell, "slug", "") or "").strip()
            or str(getattr(_engineer_cell, "name", "") or "").strip()
            or caller_kind
        )
        payload = {
            "cmd": "board_mark_task_covered",
            "id": tid,
            "covering_task_id": covering_task_id,
            "actor_name": actor_name,
            "actor_id": caller_id_str,
            "actor_kind": caller_kind,
            "move_to_done": move_to_done,
        }
        if coverage_authorization:
            payload["authorization"] = coverage_authorization
        for key in ("pr_url", "sha", "tests_run", "evidence", "notes"):
            if key in args:
                payload[key] = args[key]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_verify":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = real_state.board_tasks.get(tid)
        if is_group_board_authority_mode and not group_task_allowed(task):
            return "Authorization denied: task is outside Product Manager group scope", True
        if (
                is_creator_proposal_mode
                and not is_group_board_authority_mode
                and not creator_task_allowed(task)
        ):
            return "Authorization denied: task is outside Product Manager creator/self scope", True
        actor_name = getattr(_engineer_cell, "name", "") or caller_kind
        payload = {
            "cmd": "board_verify_task",
            "id": tid,
            "actor_name": actor_name,
        }
        if "mode" in args:
            payload["verification_mode"] = args["mode"]
        if "state" in args:
            payload["verification_state"] = args["state"]
        if "notes" in args:
            payload["verification_notes"] = args["notes"]
        if "tests_run" in args:
            payload["tests_run"] = args["tests_run"]
        if "tested_sha" in args:
            payload["tested_sha"] = args["tested_sha"]
        if "human_validation_pending" in args:
            payload["human_validation_pending"] = args["human_validation_pending"]
        for key in (
            "test_outcome",
            "full_suite_attempted",
            "unrelated_flake_accepted",
            "isolated_rerun_evidence",
            "reviewer_acceptance",
            "live_smoke_pending",
        ):
            if key in args:
                payload[key] = args[key]
        if "deploy_needed" in args:
            payload["deploy_needed"] = args["deploy_needed"]
        if "attempted" in args:
            payload["deploy_attempted"] = args["attempted"]
        if "smoke" in args:
            if args["smoke"] == "clear":
                payload["manual_smoke_done"] = False
            else:
                payload["smoke_status"] = args["smoke"]
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "task_move":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = state.board_tasks.get(tid)
        if not task:
            return "Task not found", True
        if is_group_board_authority_mode and not group_task_allowed(task):
            return "Authorization denied: task is outside Product Manager group scope", True
        if (
                is_creator_proposal_mode
                and not is_group_board_authority_mode
                and not creator_task_allowed(task)
        ):
            return "Authorization denied: task is outside Product Manager creator/self scope", True
        requested_new_lane = str(args.get("new_lane", "") or "").strip()
        requested_lane = str(args.get("lane", "") or "").strip()
        if (
            requested_new_lane
            and requested_lane
            and requested_new_lane != requested_lane
        ):
            return "lane and new_lane must match when both are provided", True
        target_lane = requested_new_lane or requested_lane
        if not target_lane:
            required_arg = "new_lane" if tool_prefix == "architect_" else "lane"
            return f"{required_arg} is required", True
        if target_lane not in real_state.board_lanes:
            return f"Unknown lane: {target_lane}", True
        clear_status, clear_status_error = _optional_bool_arg(
            args,
            "clear_status",
            False,
        )
        if clear_status_error:
            return clear_status_error, True
        acknowledge_unmerged, acknowledge_error = _optional_bool_arg(
            args,
            "acknowledge_unmerged",
            False,
        )
        if acknowledge_error:
            return acknowledge_error, True
        previous_lane = str(getattr(task, "lane", "") or "")
        result = await handle_command({
            "cmd": "board_move_task",
            "id": tid,
            "lane": target_lane,
            "clear_status": clear_status,
            "acknowledge_unmerged": acknowledge_unmerged,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if result and result.get("type") == "task_move_acknowledgement_required":
            return json.dumps(result), False
        moved = real_state.board_tasks.get(tid)
        if not moved:
            return "Task not found", True
        response = {
            "type": "task_moved",
            "task_id": tid,
            "previous_lane": previous_lane,
            "new_lane": str(getattr(moved, "lane", "") or ""),
            "status": str(getattr(moved, "status", "") or ""),
        }
        if isinstance(result, dict) and isinstance(result.get("advisory"), dict):
            response["advisory"] = result["advisory"]
        return json.dumps(response), False

    if tool_name == "task_dispatch":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        task = real_state.board_tasks.get(tid)
        if caller_kind == "architect" and (
                not task
                or (
                    is_creator_proposal_mode
                    and not creator_task_allowed(task)
                )
                or (
                    not is_creator_proposal_mode
                    and not _architect_task_owned_by_caller(
                        task, str(caller_id or "").strip()
                    )
                )):
            return "Authorization denied: task is outside creator/self scope", True
        payload = {
            "cmd": "dispatch_task",
            "id": tid,
            "_engineer_dispatch_group": _engineer_group,
            "_engineer_dispatch_id": _engineer_cell.id,
        }
        agent_ident = args.get("agent", "")
        if agent_ident:
            agent_id, agent_error = _resolve_visible_agent(
                real_state, caller_kind, caller_id, agent_ident
            )
            if not agent_id:
                return agent_error, True
            target_cell = real_state.agents.get(agent_id)
            if (
                    caller_kind == "engineer"
                    and is_architect_execution_target(target_cell)
            ):
                message = engineer_architect_task_routing_denied_message(
                    target_cell
                )
                log.warning(
                    "Denied engineer task dispatch to architect target: "
                    "engineer=%s task=%s target=%s",
                    caller_id,
                    tid,
                    agent_id,
                )
                return message, True
            payload["agent_id"] = agent_id
        else:
            payload["create_agent"] = True
            if caller_kind == "engineer":
                payload["_created_by_engineer_id"] = _engineer_cell.id
                payload["owner_engineer_id"] = str(caller_id or "").strip()
            if _engineer_cell:
                if _engineer_cell.session_id:
                    payload["target_session_id"] = _engineer_cell.session_id
                if _engineer_cell.window_id:
                    payload["target_window_id"] = _engineer_cell.window_id
            requested_provider, provider_error = (
                _mcp_worker_provider_override_arg(args)
            )
            if provider_error:
                return provider_error, True
            agent_type = _sanitize_mcp_worker_provider_override(
                state,
                _engineer_group,
                _engineer_cell.id,
                requested_provider,
            )
            if agent_type:
                payload["agent_type"] = agent_type
            command = args.get("command", "")
            if command:
                payload["command"] = command
            model = args.get("model", "")
            if model:
                payload["model"] = model
            reasoning_effort = args.get("reasoning_effort", "")
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            adopt_path = str(args.get("adopt_worktree_path", "") or "").strip()
            if adopt_path:
                payload["adopt_worktree_path"] = adopt_path
                for source_key, payload_key in (
                    ("adopt_branch", "adopt_branch"),
                    ("adopt_base_branch", "adopt_base_branch"),
                    ("adopt_repo_root", "adopt_repo_root"),
                ):
                    value = str(args.get(source_key, "") or "").strip()
                    if value:
                        payload[payload_key] = value
        agent_name = args.get("name", "")
        if agent_name:
            payload["name"] = agent_name
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        result_type = str((result or {}).get("type", "") or "ok")
        if (
                caller_kind == "engineer"
                and result_type != "dispatch_action_missing"):
            has_launch_overrides = _has_task_dispatch_launch_overrides(args)
            existing_agent = bool(agent_ident)
            shape = "warm_cluster" if existing_agent else "serial"
            task_ids = [
                str((result or {}).get("task_id", "") or tid).strip()
            ]
            _record_engineer_dispatch_shape(
                real_state,
                engineer_id=_engineer_cell.id,
                group=_engineer_group,
                source_tool="engineer_task_dispatch",
                shape=shape,
                task_ids=task_ids,
                task_count=1,
                outcome=result_type,
                hintable=(
                    shape == "serial"
                    and not existing_agent
                    and not has_launch_overrides
                ),
                metadata={
                    "existing_agent": existing_agent,
                    "has_launch_overrides": has_launch_overrides,
                    "target_agent_id": str(payload.get("agent_id", "") or ""),
                },
            )
        return json.dumps(result) if result else '{"type":"ok"}', False

    if tool_name == "batch_dispatch":
        raw_tasks = args.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return "entries must be a non-empty array", True
        raw_max_concurrent = args.get("max_concurrent", None)
        if raw_max_concurrent is None:
            max_concurrent = normalize_default_worker_concurrency(
                state.get_engineer_settings(
                    _engineer_group).default_worker_concurrency
            )
        elif not isinstance(raw_max_concurrent, int) \
                or raw_max_concurrent < 1:
            return (
                "max_concurrent must be an integer >= 1; it is the "
                "engineer-group active worker cap for this batch, not an "
                "agent_group affinity cap."
            ), True
        else:
            max_concurrent = raw_max_concurrent

        provider = _sanitize_mcp_worker_provider_override(
            state,
            _engineer_group,
            _engineer_cell.id,
            args.get("provider", ""),
        )
        dispatch_lane = (
            state.get_group_settings(_engineer_group).dispatch_lane
            or "In Progress"
        )
        active_agents = _active_worker_ids(state, _engineer_group)
        active_before = len(active_agents)
        group_agents = {}
        seen_task_ids = set()
        results = []

        def _fail(entry_idx: int, task_ident: str, reason: str,
                  message: str, task_id: str = "") -> None:
            item = {
                "index": entry_idx,
                "task": task_ident,
                "status": "failed",
                "reason": reason,
                "message": message,
            }
            if task_id:
                item["task_id"] = task_id
            results.append(item)

        for idx, raw_entry in enumerate(raw_tasks):
            if not isinstance(raw_entry, dict):
                _fail(idx, "", "invalid_entry",
                      "Each tasks entry must be an object.")
                continue

            task_ident = raw_entry.get("task", "")
            if not isinstance(task_ident, str) or not task_ident.strip():
                _fail(idx, "", "invalid_entry",
                      "Each tasks entry must include a task string.")
                continue
            agent_group = raw_entry.get("agent_group", "")
            if agent_group and not isinstance(agent_group, str):
                _fail(idx, task_ident, "invalid_entry",
                      "agent_group must be a string when provided.")
                continue

            tid = _resolve_task(state, task_ident)
            if not tid:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if tid in seen_task_ids:
                _fail(idx, task_ident, "duplicate_task",
                      "Task appears more than once in this batch.", tid)
                continue
            seen_task_ids.add(tid)
            queued_group, queued_idx, queued_entry = \
                state.auto_dispatch_queue_find(tid)
            if queued_entry:
                if queued_group != _engineer_group:
                    _fail(
                        idx,
                        task_ident,
                        "already_queued",
                        (
                            "Task is already queued for auto-dispatch in "
                            f"engineer group '{queued_group}'."
                        ),
                        tid,
                    )
                    continue
                prior_cap = int(queued_entry.max_concurrent or 1)
                refreshed_entry, cap_raised = (
                    state.auto_dispatch_queue_raise_max_concurrent(
                        _engineer_group, tid, max_concurrent
                    )
                )
                queued_agent_group = str(agent_group or "").strip()
                target_for_group = (
                    group_agents.get(queued_agent_group, "")
                    if queued_agent_group else ""
                )
                updated_entry, intent_changed = (
                    state.auto_dispatch_queue_update_dispatch_intent(
                        _engineer_group,
                        tid,
                        agent_group=(
                            queued_agent_group if queued_agent_group
                            else None
                        ),
                        target_agent_id=(
                            target_for_group if target_for_group else None
                        ),
                        engineer_owner_id=(
                            _engineer_cell.id if queued_agent_group else None
                        ),
                        provider=(
                            provider if provider and queued_agent_group
                            else None
                        ),
                    )
                )
                refreshed_entry = updated_entry or refreshed_entry or queued_entry
                effective_agent_group = (
                    str(getattr(refreshed_entry, "agent_group", "") or "")
                    .strip()
                )
                effective_target_agent_id = (
                    str(
                        getattr(refreshed_entry, "target_agent_id", "") or ""
                    ).strip()
                )
                if effective_agent_group and effective_target_agent_id:
                    group_agents.setdefault(
                        effective_agent_group,
                        effective_target_agent_id,
                    )
                queue = state.auto_dispatch_queues.get(_engineer_group, [])
                queue_position = queued_idx + 1 if queued_idx >= 0 else len(queue)
                if (cap_raised or intent_changed) and refreshed_entry:
                    status = "cap_raised" if cap_raised else "deferred"
                    reason = (
                        "already_queued_updated"
                        if cap_raised and intent_changed
                        else "already_queued_cap_raised"
                        if cap_raised
                        else "already_queued_group_updated"
                    )
                    message = (
                        "Task was already deferred for engineer group "
                        f"'{_engineer_group}'; "
                    )
                    if cap_raised:
                        message += (
                            "raised stored max_concurrent "
                            "(engineer-group active worker cap) from "
                            f"{prior_cap} to {max_concurrent}"
                        )
                    if intent_changed:
                        if cap_raised:
                            message += " and "
                        message += (
                            "refreshed stored batch dispatch intent so "
                            "auto-dispatch preserves agent_group "
                            "same-agent affinity"
                        )
                    message += "."
                    item = {
                        "index": idx,
                        "task": task_ident,
                        "task_id": tid,
                        "status": status,
                        "reason": reason,
                        "message": message,
                        "queue_position": queue_position,
                        "max_concurrent": max_concurrent,
                        "queued_at": refreshed_entry.enqueued_at,
                    }
                    if cap_raised:
                        item["previous_max_concurrent"] = prior_cap
                    else:
                        item["current_max_concurrent"] = prior_cap
                        item["requested_max_concurrent"] = max_concurrent
                    if refreshed_entry.agent_group:
                        item["agent_group"] = refreshed_entry.agent_group
                    if refreshed_entry.target_agent_id:
                        item["agent_id"] = refreshed_entry.target_agent_id
                    results.append(item)
                    continue
                item = {
                    "index": idx,
                    "task": task_ident,
                    "task_id": tid,
                    "status": "failed",
                    "reason": "already_queued",
                    "message": (
                        "Task is already queued for auto-dispatch in "
                        f"engineer group '{_engineer_group}' with "
                        f"max_concurrent={prior_cap}; requested "
                        f"max_concurrent={max_concurrent} does not raise "
                        "the engineer-group active worker cap, so the "
                        "queued entry was left unchanged."
                    ),
                    "queue_position": queue_position,
                    "current_max_concurrent": prior_cap,
                    "requested_max_concurrent": max_concurrent,
                }
                if queued_entry.agent_group:
                    item["agent_group"] = queued_entry.agent_group
                results.append(item)
                continue

            task = state.board_tasks.get(tid)
            if not task:
                _fail(idx, task_ident, "task_not_found", "Task not found.")
                continue
            if task.group != _engineer_group:
                _fail(idx, task_ident, "wrong_group",
                      "Task is outside the engineer's group.", tid)
                continue
            if task.agent_id:
                _fail(idx, task_ident, "already_assigned",
                      "Task is already linked to an agent.", tid)
                continue
            if task.lane == ARCHIVED_LANE:
                _fail(idx, task_ident, "already_archived",
                      "Task is archived.", tid)
                continue
            if task.lane == "Done":
                _fail(idx, task_ident, "already_done",
                      "Task is already Done.", tid)
                continue
            if task.lane in ("In Progress", dispatch_lane):
                _fail(idx, task_ident, "already_in_progress",
                      "Task is already in the dispatch lane.", tid)
                continue
            if not state.board_deps_met(task):
                unmet = _blocked_dependency_titles(state, task)
                _fail(
                    idx,
                    task_ident,
                    "blocked_by_dependencies",
                    "Blocked by dependencies: " + ", ".join(unmet),
                    tid,
                )
                continue

            target_agent_id = ""
            mode = "new_agent"
            if agent_group:
                mode = "agent_group"
                target_agent_id = group_agents.get(agent_group, "")

            needs_capacity = not target_agent_id
            if target_agent_id:
                needs_capacity = not _is_busy_agent(state, target_agent_id)
            if needs_capacity and len(active_agents) >= max_concurrent:
                queue_entry = state.auto_dispatch_queue_add(
                    _engineer_group,
                    tid,
                    agent_group=agent_group,
                    max_concurrent=max_concurrent,
                    target_agent_id=target_agent_id,
                    engineer_owner_id=_engineer_cell.id,
                    provider=provider,
                )
                queue = state.auto_dispatch_queues.get(_engineer_group, [])
                item = {
                    "index": idx,
                    "task": task_ident,
                    "task_id": tid,
                    "status": "deferred",
                    "reason": "max_concurrent_reached",
                    "message": (
                        "Dispatch would exceed max_concurrent for engineer "
                        f"group '{_engineer_group}' "
                        f"({len(active_agents)}/{max_concurrent} active "
                        "worker slots in use). max_concurrent is the "
                        "engineer-group active worker cap for this batch; "
                        "agent_group only controls same-agent affinity."
                    ),
                    "engineer_group": _engineer_group,
                    "active_count": len(active_agents),
                    "cap": max_concurrent,
                    "queue_position": len(queue),
                    "queued_at": (
                        queue_entry.enqueued_at if queue_entry else ""
                    ),
                }
                if agent_group:
                    item["agent_group"] = agent_group
                results.append(item)
                continue

            payload = {
                "cmd": "dispatch_task",
                "id": tid,
                "_engineer_dispatch_group": _engineer_group,
                "_engineer_dispatch_id": _engineer_cell.id,
            }
            if target_agent_id:
                payload["agent_id"] = target_agent_id
            else:
                payload["create_agent"] = True
                payload["_created_by_engineer_id"] = _engineer_cell.id
                payload["owner_engineer_id"] = str(caller_id or "").strip()
                if provider:
                    payload["agent_type"] = provider

            result = await handle_command(payload)
            task_after = state.board_tasks.get(tid)
            resolved_agent_id = task_after.agent_id if task_after else ""
            if resolved_agent_id and real_state.agents.get(resolved_agent_id):
                state.agents[resolved_agent_id] = real_state.agents[
                    resolved_agent_id
                ]
            if resolved_agent_id and (
                _is_busy_agent(state, resolved_agent_id)
                or resolved_agent_id in active_agents
            ):
                active_agents.add(resolved_agent_id)

            if result and result.get("type") == "error":
                _fail(
                    idx,
                    task_ident,
                    "dispatch_error",
                    result.get("message", "Unknown error"),
                    tid,
                )
                continue
            if result and result.get("type") == "dispatch_action_missing":
                _fail(
                    idx,
                    task_ident,
                    "dispatch_action_missing",
                    f"Action not found: {result.get('action_name', '')}",
                    tid,
                )
                continue

            if agent_group and not target_agent_id and resolved_agent_id:
                group_agents[agent_group] = resolved_agent_id

            item = {
                "index": idx,
                "task": task_ident,
                "task_id": tid,
                "status": "queued"
                if result and result.get("type") == "queued"
                else "dispatched",
                "mode": mode,
            }
            if agent_group:
                item["agent_group"] = agent_group
            if resolved_agent_id:
                item["agent_id"] = resolved_agent_id
                agent = real_state.agents.get(resolved_agent_id) or state.agents.get(
                    resolved_agent_id
                )
                if agent:
                    item["agent_name"] = agent.slug or agent.name
            results.append(item)

        payload = {
            "type": "ok",
            "max_concurrent": max_concurrent,
            "active_before": active_before,
            "active_after": len(active_agents),
            "results": results,
        }
        valid_entries = [
            item for item in results
            if str(item.get("status", "") or "")
            in _DISPATCH_SHAPE_VALID_BATCH_STATUSES
        ]
        if caller_kind == "engineer" and valid_entries:
            shape, shape_data = _batch_dispatch_shape(valid_entries)
            metadata = dict(shape_data.get("metadata", {}))
            metadata.update({
                "raw_entry_count": len(raw_tasks),
                "result_count": len(results),
                "max_concurrent": max_concurrent,
                "active_before": active_before,
                "active_after": len(active_agents),
                "provider": provider,
            })
            _record_engineer_dispatch_shape(
                real_state,
                engineer_id=_engineer_cell.id,
                group=_engineer_group,
                source_tool="engineer_batch_dispatch",
                shape=shape,
                task_ids=shape_data.get("task_ids", []),
                task_count=len(valid_entries),
                outcome="ok",
                hintable=False,
                metadata=metadata,
            )
        return json.dumps(payload), False

    if tool_name == "task_resolve":
        tid = _resolve_task(state, args.get("task", ""))
        if not tid:
            return "Task not found", True
        result = await handle_command({
            "cmd": "resolve_ask",
            "id": tid,
            "answer": args.get("answer", ""),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result) if result else '{"type":"ok"}', False

    # -- Event tools --------------------------------------------------------

    if tool_name == "events":
        # Pull events from panel_log with optional filters
        since_id = args.get("since_id", 0)
        limit = args.get("limit", 50)
        type_filter = set(args.get("types", []))

        if state.panel_log:
            events = state.panel_log.get_page(limit, since_id)
        else:
            events = []

        # Scope to engineer's group
        events = [e for e in events
                  if e.get("group", "") == _engineer_group]
        if type_filter:
            events = [e for e in events if e.get("kind") in type_filter]

        cursor = events[-1]["id"] if events else since_id
        return json.dumps({"events": events, "cursor": cursor},
                          ), False

    if tool_name == "launch_settings":
        fields = {}
        mapping = {
            "provider": "engineer_provider",
            "command": "engineer_boot_command",
            "model": "engineer_model",
            "reasoning_effort": "engineer_reasoning_effort",
        }
        for src, dest in mapping.items():
            if src in args:
                fields[dest] = str(args.get(src, "") or "").strip()
        result = await handle_command({
            "cmd": "engineer_update_settings",
            "group": _engineer_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_engineer_settings(_engineer_group))}), False

    if tool_name == "notifications":
        ws = state.get_engineer_settings(_engineer_group)
        fields = {}
        preset_name = str(args.get("preset", "") or "").strip().lower()
        if preset_name:
            try:
                fields.update(get_engineer_notification_preset(preset_name))
            except ValueError:
                return (
                    "Unknown notification preset. Use quiet, normal, or noisy.",
                    True,
                )
        if "digest_verbosity" in args:
            fields["digest_verbosity"] = normalize_engineer_digest_verbosity(
                args["digest_verbosity"]
            )
        if "push_interval" in args:
            fields["push_interval"] = max(10, args["push_interval"])
        if "max_interval" in args:
            fields["max_interval"] = max(30, args["max_interval"])
        if "heartbeat_interval" in args:
            heartbeat_interval = args["heartbeat_interval"]
            fields["heartbeat_interval"] = 0 if heartbeat_interval <= 0 \
                else max(30, heartbeat_interval)
        if preset_name or "enable" in args or "disable" in args:
            current = set(fields.get("enabled_events", ws.enabled_events))
            for e in args.get("enable", []):
                current.add(e)
            for e in args.get("disable", []):
                current.discard(e)
            fields["enabled_events"] = sorted(current)

        result = await handle_command({
            "cmd": "engineer_update_settings",
            "group": _engineer_group,
            **fields,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok", "settings": asdict(
            state.get_engineer_settings(_engineer_group))}), False

    if tool_name == "resume":
        result = await handle_command({
            "cmd": "engineer_resume",
            "group": _engineer_group,
            "engineer_id": str(caller_id or "").strip(),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return "Event delivery resumed.", False

    # -- Context tools ------------------------------------------------------

    return UNHANDLED
